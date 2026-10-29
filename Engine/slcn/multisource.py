r"""多文件源稿识别：把 `original/` 下的所有文件按**内容**判定角色。

为什么需要
----------
全库实测：**92 篇稿件中 26 篇（28%）** 的 `original/` 里有多于一个 docx。
典型的 02 Lili_Zhao：

    original/
      最新  02示例悖论的"矛盾论"解决方案探析（定稿）.docx   ← 正文
      符号整体替换.docx          ← 符号替换表
      附件一 完整参考文献.docx    ← 参考文献
      附件二 图.pdf              ← 图
      附件三 脚注+英文单位名.docx  ← 脚注
      第1页.PDF … 第10页.PDF      ← 分页 PDF

工具原先只认 `--source` 指定的那一个文件，于是参考文献拿不到、引用全部无法替换
（实测 22 处）。**先前的设计把「多源合并」写成原则，却从没在真实稿件上验证过。**

判定原则
--------
**按内容判定，不按文件名。** 文件名五花八门（「附件一」「符号整体替换」…），
但内容特征稳定：

    正文     → 字符最多，含摘要/关键词/大段连续论述
    参考文献 → 大量「含年份 + 刊名/出版社」的条目行
    脚注     → 含脚注编号或「脚注」字样，条目短且编号连续
    符号表   → 大量「A = B」或两列对照的短行
    图       → PDF/PNG/JPG 等二进制

**只给建议、标注置信度**，最终由用户确认——判错了走 `--bib` / `--metadata` 手动指定。
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

from . import paths as P
# 第 134 条：附件结构校验与 bibnorm 共用「整行注释」口径（同一份实现，避免分叉）
from .bibnorm import strip_bib_comments

# 各角色的文件名线索（**仅作辅助**，主判据是内容）
_NAME_HINT = {
    "refs": r"参考文献|references?|bibliography|文献",
    "footnotes": r"脚注|footnote|注释|注解",
    "symbols": r"符号|symbol|替换",
    "figure": r"图|figure|fig|chart|表",
    "main": r"定稿|修改稿|最新|投稿|manuscript|main|正文|全文",
}

_IMG_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eps", ".svg"}


@dataclass
class SourceFile:
    path: str
    name: str
    role: str = "unknown"  # 'main' | 'refs' | 'footnotes' | 'symbols'
                           # | 'figure' | 'pages' | 'junk'
    reason: str = ""
    score: float = 0.0
    chars: int = 0
    n_refs: int = 0
    fields: dict = field(default_factory=dict)


# 本工具**自己产出**的文件：永远不是源稿，扫描时直接跳过。
#
# 为什么需要这一条（2026-10-13 实测踩到）：用户把 `--source` 指到桌面上的散稿时，
# 扫描是**整个目录递归**的，于是上一次的产物也被当成候选。其中
# `Lei_Li_参考文献联网核实.txt` 的文件名里带「文献」→ 命中 refs 文件名线索
# （见下方「文件名线索」一段）→ 被当成参考文献附件 → 合并时往 reference.bib
# 里塞了一条 `@misc{a1}`，内容是**整份 CrossRef 核对报告**。
# 同一次扫描还把 `Lei_Li/Lei_Li.tex` 列成了「另一个完整稿件版本」。
_OWN_OUTPUT_SUFFIXES = (
    "_参考文献核对.txt",
    "_参考文献联网核实.txt",
    "_原稿.pdf",          # `--source-pdf` 导出的原稿 PDF
    "check单字成行.txt",  # 已移除的「单字成行」功能留下的报告
)


def _is_junk(name: str) -> bool:
    base = os.path.basename(name)
    return (
        base.startswith("._")
        or base == ".DS_Store"
        or "__MACOSX" in name
        or base.startswith("~$")
        or base.endswith(".tmp")
        or base.endswith(_OWN_OUTPUT_SUFFIXES)
    )


def _docx_text(path: str) -> tuple[str, int]:
    """取 docx 的纯文本与段落数。"""
    try:
        from .docxxml import Para, read_blocks, segs_to_text

        blocks = read_blocks(path)
        txt = "\n".join(
            segs_to_text(b.segs) for b in blocks if isinstance(b, Para)
        )
        return txt, len(blocks)
    except Exception:  # noqa: BLE001
        return "", 0


# 一条参考文献的典型特征：含年份 + 刊名/出版社/卷期页之一
_REF_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_REF_MARK = re.compile(r"\[\s*[JMDCNDZSPA]\s*\]")
_REF_PUB = re.compile(r"(出版社|Press|University|Verlag|Publishing|学报|学刊|杂志|\[J\]|\[M\])")


def _ref_line_score(line: str) -> bool:
    t = line.strip()
    if len(t) < 20 or len(t) > 400:
        return False
    if not _REF_YEAR.search(t):
        return False
    return bool(_REF_MARK.search(t) or _REF_PUB.search(t) or re.search(r"\d+\s*\(\s*\d+", t))


def _count_ref_lines(text: str) -> int:
    lines = [l for l in text.split("\n") if l.strip()]
    return sum(1 for l in lines if _ref_line_score(l))


def _looks_like_symbol_table(text: str) -> bool:
    """符号替换表：大量短行，且多为 `A  B` 或 `A = B` 的两列对照。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return False
    short = sum(1 for l in lines if len(l) <= 24)
    pairs = sum(1 for l in lines if _SYMBOL_PAIR.search(l))
    return short / len(lines) > 0.75 and pairs / len(lines) > 0.5


#: 符号表的"两列对照"判据。⚠️ 写成 `\S\s{2,}\S` 而**不是** `\S+\s{2,}\S+`：
#: 后者在每行 200+ 字符的长行上有二次回溯，单次调用实测 **56.4 ms**，前者 0.73 ms（80×）；
#: 35 篇真实稿 / 2892 行的判定**完全一致**（0 篇不同）。逐页体检实测（案例第 107 条）。
_SYMBOL_PAIR = re.compile(r"[=＝]|\S\s{2,}\S")

_ABS = re.compile(r"^\s*(摘\s*要|内容提要|Abstract)\s*[:：]", re.I | re.M)
_KW = re.compile(r"^\s*(关\s*键\s*词|Key\s*words?)\s*[:：]", re.I | re.M)
_LATEX_SYM = re.compile(r"\\(Box|Diamond|forall|exists|alpha|beta|neg|land|lor|to)\b")


def _looks_like_article(text: str) -> bool:
    """判断是不是**完整的稿件**（有摘要/关键词，或正文够长且成段）。"""
    # 纯参考文献文件不能算稿件。实测 01 Weston_Cole 的
    # `bibitem单列….docx` 就是一串 \bibitem，段落够长够多，
    # 会被误判成「另一个完整稿件版本」。
    if text.count("\\bibitem") >= 3:
        return False
    if len(re.findall(r"\\begin\{thebibliography\}", text)):
        return False
    if _ABS.search(text[:3000]) and _KW.search(text[:4000]):
        return True
    paras = [p for p in text.split("\n") if len(p.strip()) > 60]
    return len(paras) >= 20


def _looks_like_footnotes(text: str) -> bool:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return False
    numbered = sum(1 for l in lines if re.match(r"^[\[\(（]?\d{1,3}[\]\)）]?[.、\s]", l))
    return len(lines) >= 3 and numbered / len(lines) > 0.4


# ── 第 134 条（2026-10-18）：参考文献附件的结构校验 ─────────────────────────
# 真实稿 2023(71)04：convert ⑦ 自动选中 `预处理/用于上传text2bib.txt`（text2bib 的
# 中间产物：两条文献挤一行、含 `\href{a}{b}`）；footref 把它拆成 `title = {...}{...}`
# 这种嵌套花括号，biber 直接语法报错、**整份 bib 报废**（19 页 vs 人工 20 页）。
# 所以附件不能「扫到就并」：先按下面这些**保守、可解释**的判据过一遍。
_BIB_ENTRY = re.compile(
    r"(?m)^[ \t]*@([A-Za-z]+)\s*\{\s*([^,\s][^,]*?)\s*,"
)
_TEXT_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_TEXT_TYPE_MARK = re.compile(r"\[\s*[A-Z]{1,3}\s*\]")


def count_bibtex_entries(text: str) -> int:
    """文本里「key 非空的 `@entry`」条数（BibTeX 附件的最硬特征；整行注释不算）。"""
    return len(_BIB_ENTRY.findall(strip_bib_comments(text or "")))


def looks_like_bibtex(text: str) -> bool:
    """文本是不是 BibTeX（至少 1 条 key 非空的 `@entry`）。"""
    return count_bibtex_entries(text) > 0


def attachment_kind(path: str, text: str) -> str:
    """附件类型：`"bib"`（真 BibTeX：扩展名 .bib 或内容有 @entry）或 `"text"`（纯文献清单）。"""
    if os.path.splitext(path)[1].lower() == ".bib":
        return "bib"
    return "bib" if looks_like_bibtex(text) else "text"


def text_attachment_issue(text: str) -> str:
    """纯文本参考文献附件的结构校验；返回拒绝原因（空串 = 通过）。

    判据（保守、可解释；只按**整行**判，不做行内截断）：

      · 含 `\\href{...}`：脚注/编号清单解析器会把 `\\href{a}{b}` 拆进字段 →
        产物出现 `title = {...}{...}` 这种嵌套花括号 → biber 语法报错、整份 bib 报废
        （71/04 实测）；
      · 含相邻的 `}{`：同上，非法 BibTeX 结构；
      · 疑似「一行两条文献」：同一行 ≥2 个 `[J]/[M]/[A]…` 类型标记且 ≥2 个年份，
        或一行 ≥2 个年份且长度 ≥500 字符——text2bib 中间产物常见的两条挤一行，
        解析器会把两条揉成一条。

    边界：正常的中文/英文文献清单（`[N] 作者, 年, 题名, 刊名, 卷(期): 页` 或
    「作者，年：《题名》，刊名，年期。」）逐行只有 1 条，不命中。
    """
    t = text or ""
    if re.search(r"\\href\s*\{", t):
        return "含 \\href{...}（会被拆成嵌套花括号，biber 会报废）"
    if re.search(r"\}\s*\{", t):
        return "含相邻花括号 }{（非法 BibTeX 嵌套结构）"
    for line in t.splitlines():
        if not line.strip() or line.lstrip().startswith("%"):
            continue
        years = len(_TEXT_YEAR.findall(line))
        marks = len(_TEXT_TYPE_MARK.findall(line))
        if (marks >= 2 and years >= 2) or (years >= 2 and len(line) >= 500):
            return f"疑似一行两条文献（类型标记 {marks} 个 / 年份 {years} 个）"
    return ""


def bib_attachment_issue(text: str) -> str:
    """BibTeX 附件的结构校验；返回拒绝原因（空串 = 通过）。

    只做最低限度检查（不重写解析器）：至少 1 条 key 非空的 `@entry`、花括号配平、
    字段区不出现相邻花括号 `}{`（那会让 biber 认为值后面还有东西）。
    """
    t = strip_bib_comments(text or "")  # 注释掉的旧条目不算（第 133 条同一口径）
    if count_bibtex_entries(t) < 1:
        return "没有可解析出非空 key 的 BibTeX 条目"
    depth = 0
    for ch in t:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return "花括号不配平（多出右花括号）"
    if depth != 0:
        return "花括号不配平（缺右花括号）"
    if re.search(r"\}\s*\{", t):
        return "含相邻花括号 }{（非法 BibTeX 嵌套结构）"
    return ""


def _name_bib_hint(name: str) -> int:
    """文件名偏好：真 bib/参考文献附件优先（0 = 最优先）。"""
    base = os.path.basename(name).lower()
    if os.path.splitext(base)[1] == ".bib":
        return 0
    if "bib" in base or "reference" in base or "参考文献" in base:
        return 0
    return 1


def select_refs_attachment(
    items: list[SourceFile], *, allow_bibtex: bool = True
) -> tuple[SourceFile | None, list[str], list[str]]:
    """从扫描清单里挑参考文献附件（第 134 条；保守、可解释）。

    返回 `(采用的文件 | None, 采用说明, 跳过说明)`（说明里带文件名与原因，供 convert 打印）。

    优先级：
      1. 真 BibTeX（`.bib` 扩展名 / 内容有非空 key 的 `@entry`）—— 按文件名线索排序；
      2. 纯文本文献清单 —— 文件名带 bib/reference/参考文献 的优先，其次按扫描置信度/长度；
    两者都要过对应的结构校验；不过的进「跳过说明」，继续看下一个候选。
    `allow_bibtex=False`（Word 通道的旧行为）时**不把 BibTeX 文件当附件**，只看文本清单。
    """
    cands = [s for s in items if s.role == "refs"]
    if not cands:
        return None, [], []
    adopted: list[str] = []
    skipped: list[str] = []
    bib_cands: list[SourceFile] = []
    text_cands: list[SourceFile] = []
    for s in cands:
        kind = s.fields.get("refs_kind") or attachment_kind(s.path, s.fields.get("_text", ""))
        if kind == "bib":
            if allow_bibtex:
                bib_cands.append(s)
            else:
                # Word 通道保留旧行为：BibTeX 文件从前**不**被当附件（那时 .bib 甚至不在
                # 扫描范围内、BibTeX 文本也多半判「未识别」），不能因为本批识别能力变强
                # 就突然把它塞进 Word 产物。
                skipped.append(f"{s.name}（Word 通道不把 BibTeX 文件当参考文献附件）")
        else:
            text_cands.append(s)
    bib_cands.sort(key=lambda s: (_name_bib_hint(s.name), -s.score, -s.chars))
    text_cands.sort(key=lambda s: (_name_bib_hint(s.name), -s.score, -s.chars))

    # 先把**全部**候选校验一遍（坏候选的跳过原因要一个不漏地写进日志），再择优：
    # 真 BibTeX > 文本清单；同类按文件名线索/扫描置信度/长度。
    ok_bib: list[SourceFile] = []
    for s in bib_cands:
        issue = bib_attachment_issue(s.fields.get("_text", ""))
        if issue:
            skipped.append(f"{s.name}（{issue}）")
        else:
            ok_bib.append(s)
    ok_text: list[SourceFile] = []
    for s in text_cands:
        issue = text_attachment_issue(s.fields.get("_text", ""))
        if issue:
            skipped.append(f"{s.name}（{issue}）")
        else:
            ok_text.append(s)
    if ok_bib:
        chosen = ok_bib[0]
        adopted.append(
            f"{chosen.name}（BibTeX 附件："
            f"{count_bibtex_entries(chosen.fields.get('_text', ''))} 条）"
        )
        return chosen, adopted, skipped
    if ok_text:
        chosen = ok_text[0]
        adopted.append(f"{chosen.name}（文本清单：{chosen.reason or '按内容识别'}）")
        return chosen, adopted, skipped
    return None, adopted, skipped


def scan(directory: str, verbose: bool = False) -> list[SourceFile]:
    """扫描目录，按内容判定每个文件的角色。"""
    out: list[SourceFile] = []
    if not os.path.isdir(directory):
        return out

    candidates: list[SourceFile] = []
    for dp, dns, fs in os.walk(directory):
        dns[:] = [d for d in dns if d != "__MACOSX"]
        for f in sorted(fs):
            full = os.path.join(dp, f)
            rel = P.rel(full, directory)
            if _is_junk(rel):
                continue
            ext = os.path.splitext(f)[1].lower()
            sf = SourceFile(path=full, name=rel)
            if ext in _IMG_EXT:
                # `第3页.PDF` 这类是**分页稿**（作者按页交稿或扫描），不是插图。
                # 实测 02 Lili_Zhao 有 9 个这样的文件，全判成图会淹没真正的 `附件二 图.pdf`。
                if re.match(r"^第\s*\d+\s*页", os.path.basename(rel)) or re.match(
                    r"^(p|page)\s*\d+$", os.path.splitext(os.path.basename(rel))[0], re.I
                ):
                    sf.role = "pages"
                    sf.reason = "按页拆分的稿件（非插图）"
                else:
                    sf.role = "figure"
                    sf.reason = f"{ext} 二进制文件"
                out.append(sf)
                continue
            if ext in (".docx", ".doc"):
                txt, npar = _docx_text(full)
                sf.chars = len(txt)
                sf.fields["paras"] = npar
                sf.n_refs = _count_ref_lines(txt)
                sf.fields["_text"] = txt
                sf.fields["refs_kind"] = "text"
                candidates.append(sf)
            elif ext in (".tex", ".txt", ".md", ".rtf", ".bib"):
                try:
                    txt = open(full, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                sf.chars = len(txt)
                sf.n_refs = _count_ref_lines(txt)
                sf.fields["_text"] = txt
                # 第 134 条：真 BibTeX（.bib 或内容有非空 key 的 @entry）单列一类，
                # 附件选择时优先于纯文本清单；Word 通道仍按旧口径不消费它。
                sf.fields["refs_kind"] = (
                    "bib" if (ext == ".bib" or looks_like_bibtex(txt)) else "text"
                )
                candidates.append(sf)

    if not candidates:
        return out

    # ── 判定 ──────────────────────────────────────────────────────────
    # 第 134 条：真 BibTeX 附件（.bib / @entry 文本）不参与「最长文本=正文」的基准——
    # 一份很大的 .bib 不该把真正文的判据顶歪。
    _main_pool = [c for c in candidates if c.fields.get("refs_kind") != "bib"]
    max_chars = max((c.chars for c in (_main_pool or candidates)), default=0)
    for c in candidates:
        txt = c.fields.get("_text", "")
        lines = max(1, len([l for l in txt.split("\n") if l.strip()]))
        hint = next(
            (r for r, pat in _NAME_HINT.items() if re.search(pat, c.name, re.I)), ""
        )
        is_main = c.chars >= max_chars * 0.6 and c.chars > 3000

        # ⚠️ 顺序要紧：**先排除表格类**，再判正文。
        # 实测 `符号整体替换.docx` 有 13173 字符，比真正文（11528）还长——
        # 若先判「字符最多」，它会被误当成正文。
        # 第 134 条：真 BibTeX 附件先于一切文本判据（它的 `field = value` 行也可能
        # 命中「两列对照」之类的启发式；实测 77/04 的 `48312-271746-BIB.txt` 就被旧
        # 判据判成「未识别」而让 text2bib 中间产物捡了便宜）。
        if c.fields.get("refs_kind") == "bib":
            n_bib = count_bibtex_entries(txt)
            c.role, c.score = "refs", 0.95
            c.reason = f"BibTeX 附件：{n_bib} 条可解析条目（非空 key）"
        elif _looks_like_symbol_table(txt):
            c.role, c.score = "symbols", 0.8
            c.reason = "大量短行两列对照，像符号替换表"
        elif c.n_refs >= 5 and c.n_refs / lines > 0.35:
            c.role, c.score = "refs", 0.9
            c.reason = f"{c.n_refs} 行符合文献特征（占 {c.n_refs/lines*100:.0f}%）"
        elif _looks_like_footnotes(txt) and not is_main:
            c.role, c.score = "footnotes", 0.6
            c.reason = "条目短且编号连续，像脚注表"
        elif _looks_like_article(txt) and (
            # 非最长的文件要当「另一个完整稿件版本」，**必须够长**。
            # 实测 Lei_Li 的 `当代逻辑论题鸟瞰英文摘要.docx` 只有 2227 字符
            # （正文 22826），却因为含 Abstract/Keywords 命中了判据，
            # 被误报成「第二个完整稿件版本」。
            is_main or c.chars >= max(6000, max_chars * 0.3)
        ):
            c.role, c.score = "main", 1.0 if is_main else 0.7
            note = "字符最多，含摘要/关键词" if is_main else "也是完整稿件（含摘要/关键词）"
            if _LATEX_SYM.search(txt):
                note += "；含 \\Box、\\Diamond 等 LaTeX 符号（可能是符号已替换的版本）"
            c.reason = note
        elif c.n_refs >= 2:
            c.role, c.score = "refs", 0.5
            c.reason = f"含 {c.n_refs} 行文献特征"
        else:
            c.role, c.score = "unknown", 0.2
            c.reason = f"{c.chars} 字符，特征不明显"

        # 文件名线索只用来微调置信度，不改判定
        if hint and hint == c.role:
            c.score = min(1.0, c.score + 0.1)
            c.reason += f"；文件名含「{hint}」线索"
        elif hint and hint != c.role and c.role == "unknown":
            c.role, c.score = hint, 0.4
            c.reason += f"；按文件名线索判为 {hint}"

    out.extend(candidates)

    # 若一个 main 都没有，把最长的那个顶上（**纯 BibTeX 附件不当正文**：它不可能是一篇稿子）
    if not any(s.role == "main" for s in out):
        pool = [c for c in candidates if c.fields.get("refs_kind") != "bib"]
        if pool:
            longest = max(pool, key=lambda c: c.chars)
            longest.role, longest.score = "main", 0.5
            longest.reason += "；未识别出正文，按最长文本顶替"

    # 多个完整稿件版本 → 必须让用户选，工具不替编辑做这个判断
    mains = [c for c in candidates if c.role == "main"]
    conflict = len(mains) > 1
    if conflict:
        for c in mains:
            c.fields["conflict"] = True
            c.reason += "｜⚠ 与本篇其他文件同为完整稿件"

    out.sort(key=lambda s: ({"main": 0, "refs": 1, "footnotes": 2, "symbols": 3,
                             "figure": 4, "pages": 5, "unknown": 6,
                             "junk": 9}[s.role], -s.chars))
    return out


ROLE_CN = {
    "main": "正文",
    "refs": "参考文献",
    "footnotes": "脚注",
    "symbols": "符号替换表",
    "figure": "图",
    "pages": "分页稿",
    # 英文元数据表（第 76 条）：Word 通道的第二源，只在正文 docx 缺英文字段时才被消费。
    # 角色由 convert.py 的 mark_en_abstract_roles() 补判（本模块只负责显示名）。
    "en_abstract": "英文元数据",
    "unknown": "未识别",
    "junk": "忽略",
}


def format_inventory(items: list[SourceFile], title: str = "源稿清单") -> str:
    L = [f"══ {title} ══"]
    mains = [s for s in items if s.role == "main"]
    if len(mains) > 1:
        L.append(f"  ⚠ 发现 {len(mains)} 个「完整稿件」版本，**请用 --source 指定要用哪一个**：")
        for s in mains:
            L.append(f"      · {s.name}（{s.chars} 字符）")
        L.append("")
    for s in items:
        if s.role == "junk":
            continue
        role = ROLE_CN.get(s.role, s.role)
        extra = f"{s.chars} 字符" if s.chars else ""
        if s.role == "refs" and s.n_refs:
            extra = f"约 {s.n_refs} 条文献"
        L.append(f"  [{role:6s}] {s.name}")
        L.append(f"           {s.reason}{'｜' + extra if extra else ''}")
    return "\n".join(L)


def pick(items: list[SourceFile], role: str) -> SourceFile | None:
    """取某角色里置信度最高的一个。"""
    cand = [s for s in items if s.role == role]
    return max(cand, key=lambda s: s.score) if cand else None
