r"""目录交付：**落位 + 编译 + 单页适配 + 字体前置检查**的唯一实现（B-226）。

谁用：

  · `toc.py --place/--compile`（「生成目录」一键最终交付）；
  · `publish.py` 第 ④ 步（「本期出刊」向导的目录 PDF 落位）。

为什么抽到 `slcn/`：这两处从前各自实现「找 000 目录 / 复制最新 tex / xelatex ×2」，
一旦口径分叉就会出现「合并读 A 目录、落位写 B 目录」（案例 98/106 都栽在"两份实现"）。
本模块只做**搬运与编排**，不复制任何判据：页数解析、xelatex 调用都复用既有实现。

交付链（`deliver()`）：

  ① `find_toc_dir` / `ensure_toc_dir`：找 `000*` 目录；**缺了自动创建** `000 Table of Contents`
     （2026-10-25 用户拍板：本期出刊不再依赖该页前置步骤，见 B-227）；
  ② `place_tex`：期目录根下最新的 `Chinese Content_<时间戳>.tex` / `contents_<时间戳>.tex`
     复制为 `000*/Chinese Content.tex` / `contents.tex`（覆盖旧成品，根下的时间戳 tex 留着给下一期继承）；
  ③ `check_windows_fonts`：编译前用最小 ctexart + `fontset=windows` 探针检查 SimSun/SimHei/KaiTi
     可用（**绝不静默换字体**；结果在进程内缓存，一次交付只探一次）；
  ④ `compile_pdfs`：`xelatex ×2`（复用 `compileall.compile_tex_xelatex`，延迟 import）；
  ⑤ `fit_single_page`：页数 > 1 时**一次性**压缩 `\vdistance` 行距（中英同一机制），
     最多再编译 1 轮；仍 > 1 页 → 返回失败原因。

字体检查的探针与「行距压缩」的实测常量见各自函数的文档；`\vdistance` 的改写形式是
`\\[\dimexpr <原值> - <每行压缩量>pt\relax]` —— 用 `\dimexpr` 就不必在 Python 里猜
`ex/mm/pt` 的换算（`ex` 到底多高取决于当前字体，猜错就会压缩不足或压过头）。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import paths as _paths
from . import toctemplate as _tt

#: 目录成品固定名：`(语言键, 文件主干)`。顺序即编译/合并顺序（先中文、后英文）。
TEX_STEMS = (("zh", "Chinese Content"), ("en", "contents"))

#: 日志里 xelatex 的页数行：`Output written on contents.pdf (2 pages).`
_PAGES_RE = re.compile(r"Output written on .*?\((\d+)\s+pages?\)")

#: 模板里行距宏的定义：`\newcommand{\vdistance}{\\[4.8ex]}`（尾部注释不影响匹配）
_VDISTANCE_RE = re.compile(
    r"(\\newcommand\{\\vdistance\}\{\\\\\[)([^\]]*?)(\s*\]\})")

_OVERFULL_RE = re.compile(r"Overfull \\vbox \(([\d.]+)pt too high\)")

# ── 单页适配的实测常量（B-226 P1；证据 /tmp/b226/engine-owner/calib、sweep、deep）──
# 实测（2026-10-25，5 期 × 本期/上期/内置三类模板，逐个搜索「总压缩多少 pt 回到 1 页」）：
#   · 「本期成品」模板（同事已手调过 vdistance）：2 页时所需总压缩量只有 5~40pt
#     （90 本期英文实测 ~17pt），取 **40pt**（≈14mm）即够，且每间距只压 4.4pt，肉眼几乎看不出；
#   · 「上一期/内置」模板：同事上期手调的行距与本期版式不一定匹配——85→86 期实测需 **280pt**
#     （4.8ex 的继承行距 + 旧版头距），所以对**继承来的大行距**按「压到人工量级」估算：
#       (旧 vdistance − 目标值) × 条目间距数 + 版面余量
#     目标值取中英人工成品里常见的量级（中文 ~−3pt；英文 ~−6pt），版面余量 60pt；
#   · 日志里的 `Overfull \vbox (Xpt too high)` 不是可直接用的真实溢出（xelatex 会把页面撑高，
#     实测 130pt 的期只需 30pt），只作为「大溢出」放大项 X/4；
#   · 总压缩上限 **320pt**（≈113mm）：实测最坏验收路径（85→86 缺 000 场景）280pt 可达 1 页，
#     上限再留 ~40pt 余量；超过上限就不压了 —— 宁可按失败返回（rc=2）也不把目录压成重叠。
_BASE_SHRINK_PT = 40.0
_MAX_TOTAL_SHRINK_PT = 320.0
#: 继承模板的 vdistance 超过这个值（pt）才按「压到人工量级」估算；本期成品的小/负行距不触发。
_INHERITED_VDUSTANCE_PT = 6.0
#: 「压到人工量级」的目标值与版面余量（见上）。
_TARGET_VDUSTANCE_PT = {"zh": -3.0, "en": -6.0}
_INHERIT_HEADROOM_PT = 60.0

#: 常见长度单位 → pt（只用于**估算**压缩量：ex 取 4.9pt/个，误差由 60pt 余量吸收）。
_LENGTH_UNIT_PT = {"pt": 1.0, "bp": 1.00375, "mm": 2.845, "cm": 28.45, "in": 72.27,
                   "ex": 4.9, "em": 11.0}
_LENGTH_RE = re.compile(r"^\s*([+-]?[\d.]+)\s*(pt|bp|mm|cm|in|ex|em)\s*$")


class DeliveryError(Exception):
    """交付链的**可操作**失败原因（上游转成 rc=2 + stderr 原文）。"""


@dataclass
class DeliveryResult:
    """一次目录交付的结果（`toc.py` 拿去拼结果 JSON，`publish.py` 只看 error）。"""

    toc_dir: str = ""
    placed: dict[str, str] = field(default_factory=dict)      # 语言键 → 000 里固定名 .tex
    pdfs: dict[str, str] = field(default_factory=dict)        # 语言键 → 000 里 .pdf
    compiled: bool = False
    pages: dict[str, int] = field(default_factory=dict)       # 语言键 → 页数
    warnings: list[str] = field(default_factory=list)
    error: str = ""                                           # 非空 = 失败原因（rc=2）


# ── ① 目录定位 / 自动创建 ────────────────────────────────────────────────

def find_toc_dir(root: str) -> str:
    """找 `000` 开头的目录（`000 Table of Contents` / 旧名 `000 目录` 都认）。

    识别口径与 `slcn/pdfmerge.py` **完全一致**（`numbered_entries` + `startswith("000")`），
    不另写一套判据 —— 否则会出现「合并读 A 目录、落位写 B 目录」。
    """
    from . import pdfmerge as _pm  # noqa: PLC0415  延迟 import：只做检查时不必付 pypdf 的成本

    for name in _pm.numbered_entries(root):
        if name.startswith("000"):
            return os.path.join(root, name)
    return ""


def ensure_toc_dir(root: str) -> str:
    """缺 `000*` 时自动创建 `000 Table of Contents`（B-226 用户拍板）；已有则原样返回。"""
    existing = find_toc_dir(root)
    if existing:
        return existing
    path = os.path.join(root, _tt.TOC_DIR)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise DeliveryError(f"创建目录 `{_tt.TOC_DIR}` 失败：{e}") from e
    return path


# ── ② 落位 ───────────────────────────────────────────────────────────────

def newest_timestamped_tex(root: str, stem: str) -> str:
    """期目录根下最新的 `<stem>_<时间戳>.tex`；没有返回空串。"""
    cands = glob.glob(os.path.join(root, f"{stem}_*.tex"))
    if not cands:
        return ""
    return max(cands, key=lambda p: (os.path.getmtime(p), p))


def place_tex(root: str, toc_dir: str,
              stems: tuple[tuple[str, str], ...] = TEX_STEMS) -> dict[str, str]:
    """把期目录根最新两份目录 tex 落位成 `000*/<固定名>.tex`（覆盖旧成品）。

    **覆盖旧的、根下带时间戳的 .tex 保留**（下一期继承的是 000 里的成品）。
    返回 `{语言键: 落位后的路径}`；缺任何一份源 tex 都抛 `DeliveryError`（不做半套交付）。
    """
    placed: dict[str, str] = {}
    for key, stem in stems:
        src = newest_timestamped_tex(root, stem)
        if not src:
            raise DeliveryError(
                f"期目录根下没有 `{stem}_<时间戳>.tex` —— 请先生成目录（toc.py / 向导第 2 步）")
        dst = os.path.join(toc_dir, f"{stem}.tex")
        try:
            shutil.copyfile(src, dst)
        except OSError as e:
            raise DeliveryError(f"复制目录 tex 到 000 目录失败：{e}") from e
        placed[key] = dst
    return placed


# ── ③ 字体前置检查 ───────────────────────────────────────────────────────

#: ctex 的 `fontset=windows` 需要的字体；探针会把它们各用一遍（缺哪个会当场报错）。
_WINDOWS_FONTS = ("SimSun", "SimHei", "KaiTi", "FangSong")
_FONT_PROBE_TEX = (
    "\\documentclass[b5paper,fontset=windows]{ctexart}\n"
    "\\begin{document}\n"
    "\\songti 宋体 \\heiti 黑体 \\kaishu 楷体 \\fangsong 仿宋\n"
    "\\end{document}\n"
)

#: 进程内缓存：一次交付/一次进程只探一次（探针本身要起一次 xelatex）。
_FONT_CHECK_CACHE: tuple[bool, str] | None = None


def check_windows_fonts() -> tuple[bool, str]:
    """验证 `fontset=windows` 需要的 SimSun/SimHei/KaiTi 可用。返回 `(可用, 失败原因)`。

    做法：临时目录里写一个最小 ctexart（`fontset=windows`）+ xelatex 编译；
    成功即视为字体齐全，失败则从 `.log` 里找缺失字体名，给出**可操作**原因。
    **绝不静默换字体**：缺字体就返回失败，由上游 rc=2 明确报错。
    """
    global _FONT_CHECK_CACHE
    if _FONT_CHECK_CACHE is not None:
        return _FONT_CHECK_CACHE

    if shutil.which("xelatex") is None:
        res = (False, "找不到命令 xelatex，无法检查 Windows 字体（SimSun/SimHei/KaiTi）"
                      "：请确认已安装 TeX Live")
        _FONT_CHECK_CACHE = res
        return res

    work = tempfile.mkdtemp(prefix="slcn_fontcheck_")
    try:
        probe = os.path.join(work, "probe.tex")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(_FONT_PROBE_TEX)
        try:
            proc = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "probe.tex"],
                cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError as e:
            res = (False, f"启动 xelatex 失败：{e}")
            _FONT_CHECK_CACHE = res
            return res
        if proc.returncode == 0:
            res = (True, "")
            _FONT_CHECK_CACHE = res
            return res
        log = ""
        log_path = os.path.join(work, "probe.log")
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                log = fh.read()
        missing = [name for name in _WINDOWS_FONTS if name in log]
        detail = ""
        for line in log.splitlines():
            if line.startswith("!") or "cannot be found" in line or "font-not-found" in line:
                detail = line.strip()
                break
        hint = ("、".join(missing) if missing
                else "SimSun/SimHei/KaiTi/FangSong")
        res = (False,
               f"缺少 Windows 字体（{hint}），ctex 的 fontset=windows 无法编译"
               f" —— 请安装这些字体后再试（引擎不会静默改用其它字体）"
               f"{('：' + detail) if detail else ''}")
        _FONT_CHECK_CACHE = res
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── ④ 编译 ───────────────────────────────────────────────────────────────

def compile_pdfs(toc_dir: str,
                 stems: tuple[tuple[str, str], ...] = TEX_STEMS
                 ) -> dict[str, tuple[bool, str]]:
    """在 `000*` 里把两份目录 tex 分别 `xelatex ×2`。返回 `{语言键: (成功, PDF/错误)}`。

    实现复用 `compileall.compile_tex_xelatex`（与文章编译同一套 xelatex 口径），
    这里延迟 import：只生成 tex 的路径不必付这个成本。
    """
    import compileall as _ca  # noqa: PLC0415

    out: dict[str, tuple[bool, str]] = {}
    for key, stem in stems:
        out[key] = _ca.compile_tex_xelatex(toc_dir, stem, runs=2)
    return out


def read_page_counts(toc_dir: str,
                     stems: tuple[tuple[str, str], ...] = TEX_STEMS) -> dict[str, int | None]:
    """从 `000*/<stem>.log` 的 `Output written on … (N pages)` 解析页数（不引入新依赖）。"""
    counts: dict[str, int | None] = {}
    for key, stem in stems:
        counts[key] = page_count_from_log(toc_dir, stem)
    return counts


def page_count_from_log(toc_dir: str, stem: str) -> int | None:
    """解析一份目录 tex 编译后的页数；log 不存在/异常返回 None。"""
    path = os.path.join(toc_dir, f"{stem}.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            log = fh.read()
    except OSError:
        return None
    hits = _PAGES_RE.findall(log)
    if not hits:
        return None
    return int(hits[-1])


# ── ⑤ 单页适配 ───────────────────────────────────────────────────────────

def count_vdistance_gaps(tex_path: str) -> int:
    """数一份目录 tex 里 `\\vdistance` 的实际使用次数（= 条目间距个数）。"""
    try:
        with open(tex_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")
    except OSError:
        return 0
    return sum(1 for ln in lines if ln.strip() == "\\vdistance")


def length_to_pt(value: str) -> float | None:
    """把 `4.8ex` / `-3.05mm` / `12pt` 这类长度估成 pt（只用于估算压缩量，不是精确换算）。"""
    m = _LENGTH_RE.match(value or "")
    if not m:
        return None
    return float(m.group(1)) * _LENGTH_UNIT_PT[m.group(2)]


def _shrink_pt(toc_dir: str, stem: str, pages: int, lang: str,
               old_value: str = "") -> float:
    """算一份目录的总压缩量（pt）。规则见文件顶部常量注释：

      · 继承模板的大行距（> `_INHERITED_VDUSTANCE_PT`）：按「压到人工量级」估算；
      · 其余：2 页基准 40pt（本期成品模板实测足够）；
      · 日志 Overfull 大时按 X/4 放大；3 页以上每多一页再加一个基准量；封顶 320pt。
    """
    shrink = _BASE_SHRINK_PT
    old_pt = length_to_pt(old_value)
    gaps = max(1, count_vdistance_gaps(os.path.join(toc_dir, f"{stem}.tex")))
    if old_pt is not None and old_pt > _INHERITED_VDUSTANCE_PT:
        target = _TARGET_VDUSTANCE_PT.get(lang, _TARGET_VDUSTANCE_PT["zh"])
        shrink = max(shrink, (old_pt - target) * gaps + _INHERIT_HEADROOM_PT)
    log_path = os.path.join(toc_dir, f"{stem}.log")
    overfull = 0.0
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            log = fh.read()
        overfull = max((float(x) for x in _OVERFULL_RE.findall(log)), default=0.0)
    except OSError:
        pass
    shrink = max(shrink, overfull / 4.0)
    shrink += _BASE_SHRINK_PT * max(0, pages - 2)     # 3 页以上：每多一页再加一个基准量
    return min(shrink, _MAX_TOTAL_SHRINK_PT)


def read_vdistance(tex_path: str) -> str | None:
    """读模板里 `\\vdistance` 的当前值（如 `4.8ex`）；没有这个宏返回 None。"""
    try:
        with open(tex_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _VDISTANCE_RE.search(text)
    return m.group(2).strip() if m else None


def _rewrite_vdistance(tex_path: str, shrink_pt: float) -> tuple[str, str, int] | None:
    """把 `\\newcommand{\\vdistance}{\\[...]}` 改写为 `\\dimexpr 原值 - 每间距量pt`。

    返回 `(旧值, 新值, 间距数)`；模板里没有这个宏则返回 None（不改、由调用方记失败原因）。
    """
    try:
        with open(tex_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _VDISTANCE_RE.search(text)
    if not m:
        return None
    gaps = max(1, count_vdistance_gaps(tex_path))
    per_gap = shrink_pt / gaps
    old = m.group(2).strip()
    new = f"\\dimexpr {old} - {per_gap:.2f}pt\\relax"
    text = text[:m.start(2)] + new + text[m.end(2):]
    try:
        with open(tex_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except OSError:
        return None
    return old, new, gaps


def fit_single_page(toc_dir: str, stems: list[tuple[str, str]] | tuple[tuple[str, str], ...],
                    compile_func, log=lambda _s: None) -> tuple[dict[str, int | None], str]:
    """页数 > 1 时**一次性**压缩行距（`\\vdistance`），最多再编译 1 轮。

    `stems` 里只处理页数 > 1 的那几份（页数=1 的不动 —— 动了会破坏与人工成品的位置对齐）；
    `compile_func(toc_dir, stem) -> (成功, PDF/错误)` 可被测试替换成假实现。
    返回 `(各份最终页数, 失败原因)`：失败原因为空串表示成功。
    """
    counts = read_page_counts(toc_dir, tuple(stems))
    over = [(key, stem) for key, stem in stems if (counts.get(key) or 0) > 1]
    if not over:
        return counts, ""

    log(f"目录页数超过 1 页："
        + "、".join(f"{stem}.pdf {counts.get(key)} 页" for key, stem in over)
        + "；开始一次性压缩行距")
    for key, stem in over:
        pages_before = counts.get(key) or 0
        tex_path = os.path.join(toc_dir, f"{stem}.tex")
        old_value = read_vdistance(tex_path)
        if old_value is None:
            return counts, (f"{stem}.tex 里没有可改写的 \\vdistance 行距宏，"
                            f"无法做单页适配（目录仍 {pages_before} 页）")
        shrink = _shrink_pt(toc_dir, stem, pages_before, key, old_value)
        rewritten = _rewrite_vdistance(tex_path, shrink)
        if rewritten is None:
            return counts, (f"{stem}.tex 里没有可改写的 \\vdistance 行距宏，"
                            f"无法做单页适配（目录仍 {pages_before} 页）")
        _old, new, gaps = rewritten
        log(f"   {stem}.tex：\\vdistance {old_value} → {new}"
            f"（共压缩 {shrink:.1f}pt，{gaps} 处间距）")

    # 只重编译被压缩的那几份；页数=1 的 PDF 保持初次编译的产物（位置不动）
    for key, stem in over:
        ok, msg = compile_func(toc_dir, stem)
        if not ok:
            return counts, f"压缩行距后重新编译 {stem}.tex 失败：{msg}"
    counts_after = read_page_counts(toc_dir, tuple(stems))
    for key, stem in over:
        before = counts.get(key)
        after = counts_after.get(key)
        log(f"   {stem}.pdf：{before} 页 → {after} 页")
        if (after or 0) > 1:
            shrink = _shrink_pt(toc_dir, stem, before or 2, key, old_value)
            return counts_after, (
                f"{stem}.pdf 压缩行距（{shrink:.0f}pt，上限 {_MAX_TOTAL_SHRINK_PT:.0f}pt）后仍是 "
                f"{after} 页 —— 自动单页适配失败，请人工调整该份目录的行距/断行")
    return counts_after, ""


# ── 交付编排（toc.py 与 publish.py 第 ④ 步共用的唯一入口）────────────────

def deliver(root: str, *, do_compile: bool = True,
            compile_func=None, log=lambda _s: None) -> DeliveryResult:
    """从「期目录根下最新的时间戳目录 tex」一路交付到 `000*/` 里的成品 tex+pdf。

    缺 `000*` 会自动创建；`do_compile=False` 时只落位不编译。
    任何一步失败都把可操作原因放进 `DeliveryResult.error`（不抛异常）。
    """
    res = DeliveryResult()
    try:
        toc_dir = find_toc_dir(root)
        if not toc_dir:
            toc_dir = ensure_toc_dir(root)
            log(f"没有 000 开头的目录，已自动创建：{_tt.TOC_DIR}")
        res.toc_dir = toc_dir
        res.placed = place_tex(root, toc_dir)
        for key, stem in TEX_STEMS:
            log(f"   {os.path.basename(res.placed[key])} → "
                f"{_paths.rel(res.placed[key], root)}")
    except DeliveryError as e:
        res.error = str(e)
        return res

    if not do_compile:
        return res

    ok, reason = check_windows_fonts()
    if not ok:
        res.error = reason
        return res

    # `compile_func` 的签名与 compile_pdfs 一致：(toc_dir, stems) → {语言键: (成功, PDF/错误)}；
    # 测试注入假实现时也不必真跑 TeX。
    compile_all = compile_func or compile_pdfs
    results = compile_all(toc_dir, TEX_STEMS)
    for key, stem in TEX_STEMS:
        good, msg = results[key]
        if not good:
            res.error = (f"目录 PDF 编译失败（{stem}.tex）：{msg}；未生成新的目录 PDF，"
                         f"不会拿旧 PDF 继续合并")
            return res
    res.compiled = True
    res.pdfs = {key: results[key][1] for key, _stem in TEX_STEMS}

    def compile_one(folder: str, stem: str) -> tuple[bool, str]:
        key = next(k for k, s in TEX_STEMS if s == stem)
        return compile_all(folder, ((key, stem),))[key]

    counts, err = fit_single_page(toc_dir, TEX_STEMS, compile_one, log=log)
    res.pages = {key: (counts.get(key) or 0) for key, _stem in TEX_STEMS}
    if err:
        res.error = err
        return res
    return res
