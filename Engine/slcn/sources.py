"""源识别：判断 original/ 目录下每个文件扮演什么角色。

这是整个项目最关键的认知落地。以 03 Liang_Zhang 为例，`original/03雅辛之谜（定稿）/`
里躺着两个 .tex，角色完全不同：

    yalcin puzzle.tex   上一轮按【旧模板 SLCN.sty】排好的完整稿
                        → 题名/作者/单位/邮箱/摘要/关键词/基金/英文摘要 全在这里
    main.tex            作者本轮新提交的【纯正文】，这些元数据一个都没有

所以 LaTeX 通道不是「单文件转换」，而是 **多源合并**：
    元数据来自旧模板稿 + 正文来自新稿 + 骨架来自新版模板。

识别依据是「有没有 SLCN 模板的 \\my* 宏」和「有没有正文」这两件事，
而不是文件名——文件名完全不可靠。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import paths as P
from .latex import parse_macros

TEX_EXT = {".tex"}
DOC_EXT = {".docx", ".doc"}
TEXT_EXT = {".txt"}


@dataclass
class SourceFile:
    path: str
    role: str  # 'metadata' | 'body' | 'combined' | 'reference' | 'unknown'
    reason: str
    score: float = 0.0


@dataclass
class SourceSet:
    metadata: SourceFile | None = None
    body: SourceFile | None = None
    bib: str | None = None
    docx: str | None = None
    candidates: list[SourceFile] = field(default_factory=list)


def _tex_stats(path: str) -> dict:
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    macros = parse_macros(text)
    my_macros = {k: v for k, v in macros.items() if k.startswith("my")}
    has_docclass = "\\documentclass" in text
    has_begin_doc = "\\begin{document}" in text
    # 正文体量：去掉前导区后的字符数
    body_start = text.find("\\begin{document}")
    body = text[body_start:] if body_start >= 0 else text
    # 估算正文段落数（长度超过 100 的段落）
    paras = [p for p in body.split("\n\n") if len(p.strip()) > 100]
    return {
        "text": text,
        "macros": macros,
        "n_my": len(my_macros),
        "my_names": set(my_macros),
        "has_docclass": has_docclass,
        "has_begin_doc": has_begin_doc,
        "body_chars": len(body),
        "n_paras": len(paras),
        "n_cite": len(__import__("re").findall(r"\\[a-zA-Z]*cite", text)),
    }


# macOS 解压 zip 时产生的垃圾，绝不能当成源稿：
#   __MACOSX/ 目录，以及其中的 ._xxx AppleDouble 存根（每个只有 200~300 字节）。
# 实测 03 Da_Fan 的 original/ 里就有 __MACOSX/，识别器一度把
# `._Focused Counterfactuals.tex`（275 字符）当成了正文源，相似度掉到 0.97%。
_JUNK_DIRS = {"__MACOSX", ".Spotlight-V100", ".Trashes", ".fseventsd", ".DocumentRevisions-V100"}


def is_junk(path: str) -> bool:
    """判断是否 macOS 系统垃圾文件/目录。"""
    parts = os.path.normpath(path).split(os.sep)
    if any(p in _JUNK_DIRS for p in parts):
        return True
    name = os.path.basename(path)
    return name.startswith("._") or name == ".DS_Store"


# ── 第 76 条：英文元数据第二源（「英文摘要」docx）────────────────────────────
#
# 用户实测（2026年第5期(90)/10 Bo_Chen）：正文 `陈波 当代逻辑哲学鸟瞰.docx` 里
# **只有中文**，而英文题名/摘要/作者一直就在**同一个 original/ 目录**的
# `当代逻辑哲学鸟瞰英文摘要.docx` 里，`parse_docx()` 也早就读得出来：
#     title_en='A Survey of Contemporary Philosophy of Logic'、author_en='Chen Bo'、
#     abstract_en 1529 字符、keywords_en='contemporary philosophy of logic; …'
# 但 Word 通道从来没把这个第二源接进来，编辑只能在界面里手工粘贴。
#
# 所以这里给这类文件一个**明确角色**，并把「它是不是一张英文元数据表」的判据
# 放在一处，供 `identify()`（角色）与 `convert.py`（第二源发现）**共用同一套判据**。
EN_META_FIELDS = ("title_en", "subtitle_en", "abstract_en",
                  "author_en", "affiliation_en", "keywords_en")

# 「元数据表」= 除题名/作者/摘要/关键词之外**没有叙述正文**。
#   · `当代逻辑哲学鸟瞰英文摘要.docx`：doc.body 为空（整份文件就是一张表）→ 命中；
#   · 同目录的**另一篇英文全文稿**（如 02 Lu_Jiang 的英文稿）：doc.body 上万字符
#     → 不命中。这一条是必需的：否则会把**另一篇文章**的英文题名/摘要塞进本篇。
_EN_SHEET_MAX_BODY = 200


def en_metadata_sheet(path: str) -> dict[str, str]:
    """读一份 docx：若它是「英文元数据表」，返回它自带的英文字段；否则返回 `{}`。

    判据（宁 fail 不误判）：
      ① 解析出 `title_en` / `abstract_en` / `keywords_en` 之一
         （纯中文稿这三个都为空 → 不命中，绝不拿中文摘要冒充英文摘要）；
      ② 它**没有叙述正文**（`doc.body` 少于 200 字符）——排除同目录的另一篇全文稿。

    失败/打不开一律返回 `{}`（宁可补不上、写占位符，也不猜）。
    """
    try:
        from .docxsource import parse_docx

        doc = parse_docx(path)
    except Exception:  # noqa: BLE001
        return {}
    if len((doc.body or "").strip()) >= _EN_SHEET_MAX_BODY:
        return {}
    out: dict[str, str] = {}
    for f in EN_META_FIELDS:
        v = getattr(doc, f, "") or ""
        if v.strip():
            out[f] = v
    if not any(f in out for f in ("title_en", "abstract_en", "keywords_en")):
        return {}
    return out


def _docx_candidate(path: str, reason: str) -> SourceFile:
    """被排除的 Word 候选：其中「英文元数据表」给明确角色 `en_abstract`（第 76 条）。

    为什么要有这个角色：用户实测 `--scan` 把
    `当代逻辑哲学鸟瞰英文摘要.docx` 报成【未识别】「2227 字符，特征不明显」——
    它其实是有下游用途的**可选第二源**，不是没特征的垃圾。

    ⚠️ 这里**不动** `_docx_score()` 的扣分（文件名含「摘要」依旧 -12000），
    所以 `pick_main_docx()` 照旧**不会**把它选成正文档。
    """
    fields = en_metadata_sheet(path)
    if fields:
        return SourceFile(
            path, "en_abstract",
            f"英文元数据表（{len(fields)} 项英文字段，可作英文题名/摘要的第二源）",
        )
    return SourceFile(path, "aux", reason)


def identify(article_dir: str, verbose: bool = False) -> SourceSet:
    """扫描一篇文章目录，识别出各角色的源文件。"""
    ss = SourceSet()
    original = os.path.join(article_dir, "original")
    roots = [original] if os.path.isdir(original) else [article_dir]

    tex_files: list[tuple[str, dict]] = []
    doc_files: list[str] = []
    for root in roots:
        for dp, _, fs in os.walk(root):
            if os.path.basename(dp) in _JUNK_DIRS:
                continue
            for f in fs:
                p = os.path.join(dp, f)
                if is_junk(p):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in TEX_EXT:
                    st = _tex_stats(p)
                    if st:
                        tex_files.append((p, st))
                elif ext in DOC_EXT:
                    doc_files.append(p)
                elif ext == ".bib":
                    if ss.bib is None or "reference" in f.lower():
                        ss.bib = p

    # Word 候选可能有好几份（汉语摘要 / 作者信息 / 缩写对照表 / 审稿回复 / 编辑版…），
    # 必须按**内容**挑出正文那份，不能取第一个——实测取第一个会让相似度掉到 1%。
    if doc_files:
        picked, doc_reason = pick_main_docx(doc_files)
        if picked:
            ss.docx = picked
            ss.candidates.append(SourceFile(picked, "combined", f"Word 文稿（{doc_reason}）"))
            for d in doc_files:
                if d != ss.docx:
                    # 排除的候选里，英文元数据表给 en_abstract 角色（第 76 条）
                    ss.candidates.append(_docx_candidate(d, "辅助文档，已排除"))
        else:
            for d in doc_files:
                ss.candidates.append(_docx_candidate(d, doc_reason))

    # 分类每个 tex
    # 「模板稿」= 含 SLCN 的 \my* 宏；「纯正文稿」= 无模板宏但有实质正文
    for p, st in tex_files:
        if st["n_my"] >= 5:
            ss.candidates.append(
                SourceFile(p, "metadata", f"含 {st['n_my']} 个 SLCN 模板宏，元数据齐全", st["n_my"])
            )
        elif st["n_paras"] >= 1 and not st["has_docclass"]:
            ss.candidates.append(
                SourceFile(p, "body", f"无模板宏，纯正文（{st['n_paras']} 段 / {st['body_chars']} 字符）", st["body_chars"])
            )
        else:
            ss.candidates.append(SourceFile(p, "unknown", "形态不明", 0))

    # 选定元数据源：模板宏最多的那个
    metas = sorted([c for c in ss.candidates if c.role == "metadata"], key=lambda c: -c.score)
    if metas:
        ss.metadata = metas[0]

    # 选定正文源：纯正文稿优先（它是本轮新提交的），否则退回模板稿
    bodies = sorted([c for c in ss.candidates if c.role == "body"], key=lambda c: -c.score)
    if bodies:
        ss.body = bodies[0]
    elif ss.metadata:
        ss.body = SourceFile(ss.metadata.path, "body", "无独立正文稿，正文取自模板稿", 0)

    if verbose:
        print(f"  识别到 {len(ss.candidates)} 个候选：")
        for c in ss.candidates:
            mark = ""
            if ss.metadata and c.path == ss.metadata.path:
                mark += " ←元数据源"
            if ss.body and c.path == ss.body.path:
                mark += " ←正文源"
            print(f"    {P.rel(c.path, article_dir):50s} [{c.role}] {c.reason}{mark}")
    return ss


# 文件名里出现这些词，基本可断定不是正文
_AUX_HINTS = (
    "摘要", "作者信息", "缩写", "审稿", "修改说明", "回复", "意见", "校对",
    "封面", "目录", "版权", "协议", "模板", "图表", "附录",
    "abstract", "response", "reply", "reviewer", "cover", "template",
)


def _docx_score(path: str) -> tuple[float, str]:
    """给一份 docx 打「它是不是正文」的分。按内容判断，文件名只作减分项。"""
    try:
        # 同样用自己的 XML 直读，避免依赖 python-docx（打包时少一个依赖）
        from .docxxml import Para, read_blocks, segs_to_text

        blocks = read_blocks(path)
        texts = [segs_to_text(b.segs) for b in blocks if isinstance(b, Para)]
    except Exception as e:  # noqa: BLE001
        return -1e9, f"无法打开（{type(e).__name__}）"

    paras = [t.strip() for t in texts if t.strip()]
    paras = [p for p in paras if p]
    text = "\n".join(paras)
    if len(text) < 200:
        return -1e9, f"内容过短（{len(text)} 字符），非正文"

    score = float(len(text))
    reasons = [f"{len(text)} 字符"]
    if any(p in ("参考文献", "References", "参考书目") for p in paras):
        score += 8000
        reasons.append("含参考文献")
    if any(re.match(r"^摘\s*要", p) for p in paras):
        score += 4000
        reasons.append("含摘要")
    if any(p.startswith("关键词") for p in paras):
        score += 3000
        reasons.append("含关键词")
    n_head = sum(1 for p in paras if re.match(r"^[一二三四五六七八九十]+、", p))
    if n_head >= 2:
        score += 2000
        reasons.append(f"{n_head} 个章节标题")

    low = os.path.basename(path).lower()
    hit = [h for h in _AUX_HINTS if h.lower() in low]
    if hit:
        score -= 12000
        reasons.append(f"文件名疑似辅助稿「{hit[0]}」")

    return score, "、".join(reasons)


def pick_main_docx(paths: list[str]) -> tuple[str, str]:
    """从多份 docx 里挑出正文档。全部都不像正文时返回 ('', 原因)。

    实测 05 Zhen_Liang 的 original/ 里只有「缩写英文全称表.docx」「缩写汉译表.docx」，
    正文其实是 PDF。这种必须判为无可用源，而不是拿一张缩写表去生成空稿。
    """
    if not paths:
        return "", "没有 docx 候选"
    scored = [(p, *_docx_score(p)) for p in paths]
    scored.sort(key=lambda x: -x[1])
    best = scored[0]
    if best[1] < 0:
        return "", f"无正文档（{best[2]}）"
    if len(scored) > 1:
        others = "；".join(f"{os.path.basename(p)[:18]}→{s:.0f}" for p, s, _ in scored[1:4])
        return best[0], f"{best[2]}（共 {len(scored)} 份候选，已排除 {others}）"
    return best[0], best[2]


def extract_body(path: str) -> str:
    return extract_body_text(open(path, encoding="utf-8", errors="replace").read())


def extract_body_text(text: str) -> str:
    """从整份 `.tex` 文本里取正文（第 120 条起：多文件稿先展开 `\\input` 再走这里）。"""
    i = text.find("\\begin{document}")
    body = text[i + len("\\begin{document}") :] if i >= 0 else text
    # 去掉标题页打印与非原创文章信息
    for marker in ("\\printtitlepage", "\\printtitlepageEN"):
        j = body.find(marker)
        if 0 <= j < 2000:
            nl = body.find("\n", j)
            if nl > 0:
                body = body[nl:]
    # 截到参考文献之前
    for marker in ("\\printbibliography", "\\bibliography{", "\\begin{thebibliography}", "\\end{document}"):
        j = body.find(marker)
        if j > 0:
            body = body[:j]
    # 去掉残留的模板注释块
    lines = [ln for ln in body.split("\n") if not ln.strip().startswith("%---")]
    return "\n".join(lines).strip("\n")


def extract_bibkeys_from_body(body: str) -> set[str]:
    """取正文里引用到的所有 bib key。"""
    import re

    keys: set[str] = set()
    for m in re.finditer(r"\\(?:parencite|textcite|cite|autocite|footcite)\*?(?:\[[^\]]*\])*\{([^{}]*)\}", body):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                keys.add(k.split("=")[-1].strip())
    return keys
