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
  ② `place_tex`：取期目录 `.toolbox-tmp/` 下最新的 `Chinese Content_<时间戳>.tex` /
     `contents_<时间戳>.tex`（没有时回退期目录根的旧散落文件）复制为
     `000*/Chinese Content.tex` / `contents.tex`（覆盖旧成品；中间 tex 留在隐藏目录）；
  ③ `check_windows_fonts`：编译前用最小 ctexart + `fontset=windows` 探针检查 SimSun/SimHei/KaiTi
     可用（**绝不静默换字体**；结果在进程内缓存，一次交付只探一次）；
  ④ `compile_pdfs`：`xelatex ×2`（复用 `compileall.compile_tex_xelatex`，延迟 import）；
  ⑤ fit_single_page：页数 > 1 的目录自动单页适配（中英同一机制）—— 逐份解析当前
     \vdistance 行距，从原始 tex 文本重写、有界搜索「回到 1 页所需的最小压缩量」
     （中文每处下限 -3pt、英文 -3.6mm（约 -10.24pt）；最多 6 次二分、总编译 ≤ 8）；压到下限仍多页 →
     返回可操作失败原因（rc=2，000 按快照回滚）。页数已是 1 的份不重编、不改宏。

字体检查的探针见 check_windows_fonts 的文档。行距改写形式是
\\[\dimexpr <原值> - <每处压缩量>pt\relax]（每次尝试都从原始文本重写，重复运行不累计）；
用 \dimexpr 就不必在 Python 里猜 ex/mm/pt 的换算（ex 到底多高取决于当前字体，
猜错就会压缩不足或压过头），压缩量由「编译一次、读页数」实测决定。
"""

from __future__ import annotations

import glob
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import paths as _paths
from . import platform as _plat
from . import toctemplate as _tt

#: 目录成品固定名：`(语言键, 文件主干)`。顺序即编译/合并顺序（先中文、后英文）。
TEX_STEMS = (("zh", "Chinese Content"), ("en", "contents"))

#: 生成目录的隐藏工作目录（B-121）：中间 tex 先落这里，期目录根不再散落。
#: 与 `slcn.toc.TOC_WORK_DIR` 同值（不 import 该模块以免拉入 infocheck/docx 重依赖）；
#: 双源一致性由 `tests/test_b121_toc_workdir.py` 守卫。
_TOC_WORK_DIR = ".toolbox-tmp"

#: 日志里 xelatex 的页数行：`Output written on contents.pdf (2 pages).`
_PAGES_RE = re.compile(r"Output written on .*?\((\d+)\s+pages?\)")

#: 模板里行距宏的定义：`\newcommand{\vdistance}{\\[4.8ex]}`（尾部注释不影响匹配）
_VDISTANCE_RE = re.compile(
    r"(\\newcommand\{\\vdistance\}\{\\\\\[)([^\]]*?)(\s*\]\})")

# ── 单页适配常量（B-375 round2 重做；用户拍板见 docs/RULES.md 十二）────────
# 2026-11-06 用户在 9 篇双行长英题合成夹具上实测（closeout fixtures）：
#   · 英文目录 base 4mm=11.36pt、8 处间距：总压缩 125pt 仍 2 页，130pt 才 1 页
#     （每处约 15.8–16.3pt）；每处下限 -3.6mm≈-10.24pt 时总上限 172.8pt，仍可容纳；
#   · 中文目录 base 4.8ex≈23.52pt、8 处间距：80pt 总压缩已回 1 页，下限 -3pt 时上限 212.2pt；
#   · 20 篇双行长英题夹具在旧下限下总上限 330pt 时，320pt 仍 2 页；新下限只扩大搜索
#     边界，仍必须走「先试上限仍多页 ⇒ 明确失败」路径，绝不无声交付 2 页。
# 因此不再用「一次性估一个压缩量 + 320pt 封顶」的老办法，而是逐份有界搜索：
#   总压缩上限 = (当前行距 pt − 每处下限) × 间距数；先试上限判可行性，可行则二分
#   最小通过值（收敛 3pt、≤6 次二分、总编译 ≤8）；每次尝试从原始 tex 文本重写，
#   最终只写「最小通过值」的每处压缩量，重复运行不累计。
# 下限只是允许搜索的边界，不是最终行距；最终仍由二分取最小必要压缩量。
# 英文 -3.6mm 按 TeX 1mm=2.845pt 换算为 -10.242pt，取 -10.24pt（单点定义，避免单位漂移）。
_EN_SINGLE_PAGE_FLOOR_PT = -10.24
_SINGLE_PAGE_FLOOR_PT = {"zh": -3.0, "en": _EN_SINGLE_PAGE_FLOOR_PT}
_FIT_MAX_BISECT = 6
_FIT_TOLERANCE_PT = 3.0
_FIT_MAX_ATTEMPTS = 8

#: 常见长度单位 → pt（估算行距用；ex 取 4.9pt/个，与历史口径一致）。
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
    #: B-375 round2：单页适配明细（仅 compiled 时由 deliver 填入；见 fit_single_page）
    single_page_fit: dict = field(default_factory=dict)
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
    """期目录里最新的 `<stem>_<时间戳>.tex`；没有返回空串。

    B-121 起中间 tex 落在 `<期目录>/.toolbox-tmp/`，**优先**取那里的最新一份；
    为兼容旧版本散落在期目录根的 `<stem>_<时间戳>.tex`，隐藏目录为空时回退根目录。
    两处都有时取隐藏目录（新链路）——不会被遗留旧文件“抢”回落位源。
    """
    for folder in (os.path.join(root, _TOC_WORK_DIR), root):
        cands = glob.glob(os.path.join(folder, f"{stem}_*.tex"))
        if cands:
            return max(cands, key=lambda p: (os.path.getmtime(p), p))
    return ""


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
                f"期目录下没有 `{stem}_<时间戳>.tex`（`.toolbox-tmp/` 与期目录根都没有）"
                f" —— 请先生成目录（toc.py / 向导第 2 步）")
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
                cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                **_plat.no_window_kwargs())
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


def read_vdistance(tex_path: str) -> str | None:
    """读模板里 \vdistance 的当前值（如 4.8ex）；没有这个宏返回 None。"""
    try:
        with open(tex_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _VDISTANCE_RE.search(text)
    return m.group(2).strip() if m else None


def _read_text(tex_path: str) -> str:
    """读 tex 原文（保持原始换行，不做 newline 转换）。"""
    with open(tex_path, encoding="utf-8") as fh:
        return fh.read()


def _write_text(tex_path: str, text: str) -> None:
    """写回 tex；newline="" 不做换行转换，保证与模板逐字节可比。"""
    with open(tex_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _restore_text(tex_path: str, text: str) -> None:
    """把 tex 恢复成尝试前的原文（失败路径不留半截压缩）。"""
    try:
        _write_text(tex_path, text)
    except OSError:
        pass


def _parse_vdistance_base(value: str) -> tuple[str, float] | None:
    r"""解析 \vdistance 当前值，返回 (基准长度文本, 基准 pt)。

    · 纯长度（4.8ex / -3.05mm / 12pt）→ 原文本 + pt 估算；
    · 旧版改写结果 \dimexpr <基准> - <n>pt\relax → 只取 <基准>（绝不把已有压缩量再累计，
      否则重复运行会把行距越压越小）；
    · 其它（未知单位 / 解包超过 5 层 / 没有减法形式）→ None（调用方明确失败）。
    """
    inner = (value or "").strip()
    if not inner:
        return None
    for _ in range(5):
        if not inner.startswith("\\dimexpr"):
            break
        inner = inner[len("\\dimexpr"):].strip()
        if inner.endswith("\\relax"):
            inner = inner[: -len("\\relax")].strip()
        m = re.match(r"^(?P<base>.*?)\s*-\s*[+-]?[\d.]+\s*pt\s*$", inner, re.S)
        if not m:
            return None
        inner = m.group("base").strip()
        if not inner:
            return None
    base_pt = length_to_pt(inner)
    if base_pt is None:
        return None
    return inner, base_pt


def _rewrite_vdistance_text(text: str, base: str, per_gap_pt: float) -> str | None:
    r"""把文本里的 \vdistance 改写成 \dimexpr <base> - <每处压缩量>pt\relax（不写盘）。"""
    m = _VDISTANCE_RE.search(text)
    if not m:
        return None
    new = f"\\dimexpr {base} - {per_gap_pt:.2f}pt\\relax"
    return text[: m.start(2)] + new + text[m.end(2):]


def fit_single_page(toc_dir: str, stems: list[tuple[str, str]] | tuple[tuple[str, str], ...],
                    compile_func, log=lambda _s: None
                    ) -> tuple[dict[str, int | None], str, dict[str, dict]]:
    """把页数 > 1 的目录自动压到 1 页（B-375 round2：逐份有界搜索最小压缩量）。

    返回 (各份最终页数, 失败原因, 单页适配明细)；失败原因为空串表示成功。
    compile_func(toc_dir, stem) -> (成功, PDF/错误) 可被测试替换成假实现。
    页数已是 1 的份不重编、不改宏；页数 > 1 的份先试「压到每处下限」判可行性，
    可行后用二分法找最小通过量（≤ _FIT_MAX_BISECT 次二分、总编译 ≤ _FIT_MAX_ATTEMPTS）。
    """
    counts = read_page_counts(toc_dir, tuple(stems))
    over = [(key, stem) for key, stem in stems if (counts.get(key) or 0) > 1]
    if not over:
        log("目录均为 1 页，无需单页适配（不重编、不改行距）")
        return counts, "", {}

    log("目录页数超过 1 页："
        + "、".join(f"{stem}.pdf {counts.get(key)} 页" for key, stem in over)
        + "；开始自动单页适配（逐份搜索最小压缩量）")
    fit: dict[str, dict] = {}
    for key, stem in over:
        pages_before = counts.get(key) or 0
        tex_path = os.path.join(toc_dir, f"{stem}.tex")
        try:
            original_text = _read_text(tex_path)
        except OSError as e:
            return counts, f"读取 {stem}.tex 失败：{e}", fit
        m = _VDISTANCE_RE.search(original_text)
        if not m:
            return counts, (f"{stem}.tex 里没有可改写的 \\vdistance 行距宏，"
                            f"无法做单页适配（目录仍 {pages_before} 页）"), fit
        old_value = m.group(2).strip()
        parsed = _parse_vdistance_base(old_value)
        if parsed is None:
            return counts, (
                f"{stem}.tex 的 \\vdistance 当前值「{old_value}」无法解析"
                f"（只支持纯长度 mm/cm/pt/in/em/ex 或 \\dimexpr <长度> - <n>pt\\relax），"
                f"无法做单页适配"), fit
        base, base_pt = parsed
        gaps = max(1, count_vdistance_gaps(tex_path))
        floor = _SINGLE_PAGE_FLOOR_PT.get(key, _SINGLE_PAGE_FLOOR_PT["zh"])
        max_total = (base_pt - floor) * gaps
        if max_total <= 0:
            return counts, (
                f"{stem}.tex 当前行距 {old_value}（每处约 {base_pt:.2f}pt）"
                f"已越过自动适配下限（每处 {floor:.1f}pt，当前更紧），"
                f"按当前下限无法继续压缩 —— "
                f"请人工调整该份目录的行距/断行"), fit

        attempts = 0

        def attempt(total_pt: float) -> tuple[str, int | None]:
            """写一次压缩量并重编译；返回 (错误原因, 页数)，错误原因为空串表示成功。"""
            nonlocal attempts
            per_gap = math.ceil(total_pt / gaps * 100) / 100
            new_text = _rewrite_vdistance_text(original_text, base, per_gap)
            if new_text is None:  # 调用前已确认宏存在，理论上不可达
                return "改写 \\vdistance 失败", None
            try:
                _write_text(tex_path, new_text)
            except OSError as e:
                return f"写 {stem}.tex 失败：{e}", None
            attempts += 1
            ok, msg = compile_func(toc_dir, stem)
            if not ok:
                _restore_text(tex_path, original_text)
                return f"压缩行距后重新编译 {stem}.tex 失败：{msg}", None
            page = page_count_from_log(toc_dir, stem)
            log(f"   尝试压缩 {per_gap * gaps:.2f}pt（每处 {per_gap:.2f}pt）"
                f"→ {page if page is not None else '?'} 页")
            if page is None:
                _restore_text(tex_path, original_text)
                return (f"压缩后读不到 {stem}.tex 的编译页数"
                        f"（{stem}.log 里没有 Output written on …）"), None
            return "", page

        err, page = attempt(max_total)
        if err:
            return counts, err, fit
        if page is not None and page > 1:
            _restore_text(tex_path, original_text)
            return counts, (
                f"{stem}.pdf 压缩到自动适配下限（每处 {floor:.1f}pt、共约 {max_total:.0f}pt）"
                f"后仍是 {page} 页 —— 自动单页适配失败，请人工调整该份目录的行距/断行"), fit

        lo, hi = 0.0, max_total
        bisects = 0
        while (hi - lo > _FIT_TOLERANCE_PT and bisects < _FIT_MAX_BISECT
               and attempts < _FIT_MAX_ATTEMPTS):
            mid = (lo + hi) / 2.0
            err, page = attempt(mid)
            if err:
                return counts, err, fit
            if page is not None and page <= 1:
                hi = mid
            else:
                lo = mid
            bisects += 1

        applied_per_gap = math.ceil(hi / gaps * 100) / 100
        # 四舍五入到 0.01pt 后不得超过每处下限；超过时钳到下限（差 <0.01pt/处，不影响单页
        # 判定：hi 是实测通过值、搜索容差 3pt）。
        if applied_per_gap > base_pt - floor:
            applied_per_gap = round(base_pt - floor, 2)
        final_text = _rewrite_vdistance_text(original_text, base, applied_per_gap)
        if final_text is None:
            return counts, f"改写 {stem}.tex 的 \\vdistance 失败", fit
        try:
            _write_text(tex_path, final_text)
        except OSError as e:
            _restore_text(tex_path, original_text)
            return counts, f"写 {stem}.tex 失败：{e}", fit
        # Lead review（2026-11-07）：二分最后一次 attempt 可能是「仍 2 页」的失败尝试，
        # 000 里的 PDF 未必对应最终写下的最小压缩量；因此最终写盘后必须再编译一次并
        # 确认确实是 1 页，否则不能报成功。预算：上限试探 1 + 二分 ≤6 + 最终复核 1 = 8。
        attempts += 1
        ok, msg = compile_func(toc_dir, stem)
        if not ok:
            _restore_text(tex_path, original_text)
            return counts, f"压缩行距后重新编译 {stem}.tex 失败：{msg}", fit
        page = page_count_from_log(toc_dir, stem)
        log(f"   最终复核：压缩 {applied_per_gap * gaps:.2f}pt（每处 {applied_per_gap:.2f}pt）"
            f"→ {page if page is not None else '?'} 页")
        if page is None:
            _restore_text(tex_path, original_text)
            return counts, (f"最终复核读不到 {stem}.tex 的编译页数"
                            f"（{stem}.log 里没有 Output written on …）"), fit
        if page > 1:
            _restore_text(tex_path, original_text)
            return counts, (
                f"{stem}.pdf 按最小压缩量（每处 {applied_per_gap:.2f}pt、"
                f"共 {applied_per_gap * gaps:.2f}pt）写回后复核仍是 {page} 页 —— "
                f"自动单页适配失败，请人工调整该份目录的行距/断行"), fit
        new_macro = f"\\dimexpr {base} - {applied_per_gap:.2f}pt\\relax"
        total_shrink = round(applied_per_gap * gaps, 2)
        counts[key] = 1
        log(f"   {stem}.tex：\\vdistance {old_value} → {new_macro}")
        log(f"   共压缩 {total_shrink:.2f}pt（{gaps} 处间距，每处 {applied_per_gap:.2f}pt，"
            f"下限 {floor:.1f}pt/处），尝试 {attempts} 次")
        log(f"   {stem}.pdf：{pages_before} 页 → 1 页")
        fit[stem] = {
            "pagesBefore": pages_before,
            "pagesAfter": 1,
            "gaps": gaps,
            "oldMacro": old_value,
            "newMacro": new_macro,
            "applied": True,
            "attempts": attempts,
            "totalShrinkPt": total_shrink,
            "perGapShrinkPt": applied_per_gap,
        }
    return counts, "", fit


# ── 失败回滚（B-375 freeze-3 修复）────────────────────────────────────────
#
# 独立验收复现：--place 直接覆盖 000 里既有的两份 tex；若随后编译/EN 阶段失败
# （如 000 目录只读时 xelatex 写不了 .aux/.log），rc2 但 000 里已是「新 tex + 旧 pdf」
# 的混合半成品。修复：落位前把整个 000 快照到 <期目录>/.toolbox-tmp/toc-backup-*，
# 任何失败（含未预期异常）都按快照恢复文件集合与内容；成功/失败都清理备份。

#: 000 快照备份目录名前缀（放在期目录 .toolbox-tmp/ 下，不进入 000）
_BACKUP_PREFIX = "toc-backup-"


@dataclass
class _TocDirBackup:
    """落位前对 000 目录做的一次快照（失败时原样恢复）。"""

    path: str = ""            # 备份根目录；空串 = 无需备份（000 原本不存在）
    existed: bool = False     # 本轮开始前 000 是否存在
    mode: int = 0             # 000 顶层目录权限（恢复时还原）
    parent: str = ""          # 备份所在父目录（.toolbox-tmp）
    parent_created: bool = False  # 父目录是否本轮新建（清理时顺手删掉空目录）


def _backup_toc_dir(root: str, toc_dir: str) -> _TocDirBackup:
    """把 000 目录整棵快照到 <期目录>/.toolbox-tmp/toc-backup-*。

    为什么必须在落位前做：place_tex 会覆盖 000 里既有的两份 tex；之后任何
    失败都必须让 000 回到本轮开始前的状态，否则 PDF/结果 JSON 旧、tex 新。
    """
    info = _TocDirBackup()
    if not toc_dir or not os.path.isdir(toc_dir):
        return info
    try:
        info.mode = stat.S_IMODE(os.stat(toc_dir).st_mode)
    except OSError as e:
        raise DeliveryError(f"读 000 目录权限失败：{e}") from e
    parent = os.path.join(root, _TOC_WORK_DIR)
    try:
        if not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
            info.parent_created = True
        info.path = tempfile.mkdtemp(prefix=_BACKUP_PREFIX, dir=parent)
    except OSError as e:
        raise DeliveryError(f"创建 000 备份目录失败：{e}") from e
    try:
        shutil.copytree(toc_dir, os.path.join(info.path, "snapshot"), symlinks=True)
    except OSError as e:
        try:
            _force_rmtree(info.path)
        except OSError:
            pass
        raise DeliveryError(f"备份 000 目录失败：{e}") from e
    info.existed = True
    info.parent = parent if not info.parent_created else ""
    return info


def _force_rmtree(path: str) -> None:
    """删除目录树；先给目录补写权限（000 可能是只读的，直接 rmtree 会失败）。"""
    if not os.path.isdir(path) or os.path.islink(path):
        return
    for dirpath, _dirnames, filenames in os.walk(path):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                os.chmod(full, 0o600)
            except OSError:
                pass
    shutil.rmtree(path)


def _restore_toc_dir(toc_dir: str, info: _TocDirBackup) -> str:
    """把 000 恢复成快照状态；返回空串表示成功，否则返回失败原因。"""
    try:
        if toc_dir and os.path.isdir(toc_dir):
            _force_rmtree(toc_dir)
        if info.existed:
            shutil.copytree(os.path.join(info.path, "snapshot"), toc_dir, symlinks=True)
            try:
                os.chmod(toc_dir, info.mode)
            except OSError:
                pass
        return ""
    except OSError as e:
        return str(e)


def _discard_backup(info: _TocDirBackup) -> None:
    """删除 000 快照（成功路径也调用：不得残留备份）。

    快照里 000 的目录权限是原样复制的（可能是只读 0555），所以不能直接
    rmtree（删不动子项会静默失败）；用 _force_rmtree 先补写权限。
    """
    if info.path:
        try:
            _force_rmtree(info.path)
        except OSError:
            pass
    if info.parent_created and info.parent:
        try:
            os.rmdir(info.parent)
        except OSError:
            pass


# ── 交付编排（toc.py 与 publish.py 第 ④ 步共用的唯一入口）────────────────

def deliver(root: str, *, do_compile: bool = True,
            compile_func=None, log=lambda _s: None) -> DeliveryResult:
    """从「期目录根下最新的时间戳目录 tex」一路交付到 000*/ 里的成品 tex+pdf。

    缺 000* 会自动创建；do_compile=False 时只落位不编译。
    任何一步失败都把可操作原因放进 DeliveryResult.error（不抛异常），并且把 000
    目录恢复成本轮开始前的文件集合与内容（B-375 freeze-3：落位会覆盖既有 tex，
    不能留下「新 tex + 旧 pdf」的混合半成品）。
    """
    res = DeliveryResult()
    backup = _TocDirBackup()
    toc_dir = ""
    try:
        toc_dir = find_toc_dir(root)
        backup = _backup_toc_dir(root, toc_dir)
    except DeliveryError as e:
        res.error = str(e)
        return res

    try:
        if not toc_dir:
            toc_dir = ensure_toc_dir(root)
            log(f"没有 000 开头的目录，已自动创建：{_tt.TOC_DIR}")
        res.toc_dir = toc_dir
        res.placed = place_tex(root, toc_dir)
        for key, stem in TEX_STEMS:
            log(f"   {os.path.basename(res.placed[key])} → "
                f"{_paths.rel(res.placed[key], root)}")
        if do_compile:
            ok, reason = check_windows_fonts()
            if not ok:
                raise DeliveryError(reason)
            # compile_func 的签名与 compile_pdfs 一致：(toc_dir, stems) → {语言键: (成功, PDF/错误)}；
            # 测试注入假实现时也不必真跑 TeX。
            compile_all = compile_func or compile_pdfs
            results = compile_all(toc_dir, TEX_STEMS)
            for key, stem in TEX_STEMS:
                good, msg = results[key]
                if not good:
                    raise DeliveryError(
                        f"目录 PDF 编译失败（{stem}.tex）：{msg}；未生成新的目录 PDF，"
                        f"不会拿旧 PDF 继续合并")
            res.compiled = True
            res.pdfs = {key: results[key][1] for key, _stem in TEX_STEMS}

            def compile_one(folder: str, stem: str) -> tuple[bool, str]:
                key = next(k for k, s in TEX_STEMS if s == stem)
                return compile_all(folder, ((key, stem),))[key]

            counts, err, single_page_fit = fit_single_page(
                toc_dir, TEX_STEMS, compile_one, log=log)
            res.pages = {key: (counts.get(key) or 0) for key, _stem in TEX_STEMS}
            res.single_page_fit = single_page_fit
            if err:
                raise DeliveryError(err)
    except DeliveryError as e:
        restore_error = _restore_toc_dir(toc_dir, backup)
        res.error = str(e) + (f"（另：恢复 000 目录失败：{restore_error}）"
                              if restore_error else "")
        _discard_backup(backup)
        return res
    except Exception as e:  # noqa: BLE001 - 交付链任何未预期异常也要先恢复 000
        restore_error = _restore_toc_dir(toc_dir, backup)
        detail = str(e) + (f"（另：恢复 000 目录失败：{restore_error}）"
                           if restore_error else "")
        res.error = f"目录交付过程中出错：{detail}"
        _discard_backup(backup)
        return res
    _discard_backup(backup)
    return res
