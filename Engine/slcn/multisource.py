r"""多文件源稿识别：把 `original/` 下的所有文件按**内容**判定角色。

为什么需要
----------
全库实测：**92 篇稿件中 26 篇（28%）** 的 `original/` 里有多于一个 docx。
典型的 02 Zixin_Luo：

    original/
      最新  02可知性悖论的"真矛盾论"解决方案探析（定稿）.docx   ← 正文
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
# `Bo_Chen_参考文献联网核实.txt` 的文件名里带「文献」→ 命中 refs 文件名线索
# （见下方「文件名线索」一段）→ 被当成参考文献附件 → 合并时往 reference.bib
# 里塞了一条 `@misc{a1}`，内容是**整份 CrossRef 核对报告**。
# 同一次扫描还把 `Bo_Chen/Bo_Chen.tex` 列成了「另一个完整稿件版本」。
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
    pairs = sum(1 for l in lines if re.search(r"[=＝]|\S+\s{2,}\S+", l))
    return short / len(lines) > 0.75 and pairs / len(lines) > 0.5


_ABS = re.compile(r"^\s*(摘\s*要|内容提要|Abstract)\s*[:：]", re.I | re.M)
_KW = re.compile(r"^\s*(关\s*键\s*词|Key\s*words?)\s*[:：]", re.I | re.M)
_LATEX_SYM = re.compile(r"\\(Box|Diamond|forall|exists|alpha|beta|neg|land|lor|to)\b")


def _looks_like_article(text: str) -> bool:
    """判断是不是**完整的稿件**（有摘要/关键词，或正文够长且成段）。"""
    # 纯参考文献文件不能算稿件。实测 01 Ulrich_Kohlenbach 的
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
            rel = os.path.relpath(full, directory)
            if _is_junk(rel):
                continue
            ext = os.path.splitext(f)[1].lower()
            sf = SourceFile(path=full, name=rel)
            if ext in _IMG_EXT:
                # `第3页.PDF` 这类是**分页稿**（作者按页交稿或扫描），不是插图。
                # 实测 02 Zixin_Luo 有 9 个这样的文件，全判成图会淹没真正的 `附件二 图.pdf`。
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
                candidates.append(sf)
            elif ext in (".tex", ".txt", ".md", ".rtf"):
                try:
                    txt = open(full, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                sf.chars = len(txt)
                sf.n_refs = _count_ref_lines(txt)
                sf.fields["_text"] = txt
                candidates.append(sf)

    if not candidates:
        return out

    # ── 判定 ──────────────────────────────────────────────────────────
    max_chars = max(c.chars for c in candidates)
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
        if _looks_like_symbol_table(txt):
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
            # 实测 Bo_Chen 的 `当代逻辑哲学鸟瞰英文摘要.docx` 只有 2227 字符
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

    # 若一个 main 都没有，把最长的那个顶上
    if not any(s.role == "main" for s in out):
        longest = max(candidates, key=lambda c: c.chars)
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
