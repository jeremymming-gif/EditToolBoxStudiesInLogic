r"""引用环境（block quotation）的生成。

人工惯例（实测 07 Jiayi_He，37/37 个块完全一致）
------------------------------------------------------
    \begin{quotation}
      引文内容……\hfill（\parencite{KEY}，第 N 页）
    \end{quotation}

要点：
  · 环境用 `quotation`（该文 37 处全是 quotation，`quote` 0 处）
  · 引文与引用之间用 **`\hfill` 把引用推到右侧**
  · 引用写成 `（\parencite{KEY}，第 N 页）`——`\parencite` **不带 `[]` 页码定位**，
    页码放在后面的中文里
  · 页码范围写作 `第 471\cndash472 页`

触发条件
--------
用户指定：**引用内容超过三行** → 放进引用环境。

行数换算：SLCN-MIX 正文 10.5pt、B5 版心宽约 130mm，每汉字约 3.7mm
         → 约 35 字/行 → 三行 ≈ 105 字。

⚠️ 实测偏差：参考样例里 37 个块有 14 个短于 105 字（最短 45 字），
   说明人工并不严格按三行切。因此阈值做成可调参数，默认按用户给的三行规则，
   并把「接近阈值」的块一并列出供人工复核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 三行 ≈ 105 字（见上文换算）
DEFAULT_THRESHOLD = 105

# 引号：中文引号为主，兼容直引号
_QUOTE_RE = re.compile(r"[“]([^”]{20,})[”]")

# 缺少文献或页码时的标注（可见 + 可 grep）
NEED_REF = r"\textbf{【需补充参考文献】}"
NEED_PAGE = r"\textbf{【需补充页码】}"
NEED_BOTH = r"\textbf{【需补充参考文献与页码】}"


@dataclass
class QuoteBlock:
    """一个待处理的引文块。"""

    content: str  # 引号内的文字（LaTeX 已渲染）
    visible_len: int  # 可见字数
    key: str = ""  # bib 键
    page: str = ""  # 页码（已转成 LaTeX，如 `147` 或 `471\cndash472`）
    used: bool = False  # 是否已提升为引用环境


def visible_len(latex: str) -> int:
    """估算可见字数：去掉 LaTeX 命令、标记与空白。"""
    t = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?(\{[^{}]*\})?", "", latex)
    t = re.sub(r"[{}$\\~]", "", t)
    return len(re.sub(r"\s+", "", t))


def page_to_tex(page: str) -> str:
    """`147` → `147`；`471-472` / `471--472` → `471\\cndash472`。"""
    p = page.strip()
    m = re.fullmatch(r"(\d+)\s*[-–—]+\s*(\d+)", p)
    if m:
        return f"{m.group(1)}\\cndash {m.group(2)}"
    return p


def build_quotation(
    content: str,
    key: str = "",
    page: str = "",
    *,
    indent: str = "  ",
) -> tuple[str, str]:
    """生成一个引用环境。

    返回 (LaTeX 文本, 提示信息)。缺文献/页码时会加可见标注。
    """
    note = ""
    if key and page:
        cite = f"\\hfill（\\parencite{{{key}}}，第 {page_to_tex(page)} 页）"
    elif key and not page:
        cite = f"\\hfill（\\parencite{{{key}}}，{NEED_PAGE}）"
        note = "有文献但缺页码"
    elif page and not key:
        cite = f"\\hfill（{NEED_REF}，第 {page_to_tex(page)} 页）"
        note = "有页码但缺文献"
    else:
        cite = f"\\hfill（{NEED_BOTH}）"
        note = "文献与页码都缺"

    body = content.strip()
    return (
        "\\begin{quotation}\n"
        f"{indent}{body}{cite}\n"
        "\\end{quotation}",
        note,
    )


def _sentence_ends(text: str) -> int:
    """数引文里的句末标点。用来看它是不是「完整的陈述」而非短语引用。"""
    return len(re.findall(r"[。！？]", text))


def find_quotes(latex: str) -> list[QuoteBlock]:
    """找出一段已渲染 LaTeX 里的引文块（按可见字数降序）。"""
    out = []
    for m in _QUOTE_RE.finditer(latex):
        c = m.group(1)
        out.append(QuoteBlock(content=c, visible_len=visible_len(c)))
    out.sort(key=lambda q: -q.visible_len)
    return out


def hoist_quotes(
    latex: str,
    keymap: dict[int, str],
    fn_pages: dict[int, str],
    footnote_ids: list[int],
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[str, list[str], list[QuoteBlock]]:
    """把超过阈值的引文提升为引用环境。

    keymap      脚注 id → bib 键
    fn_pages    脚注 id → 页码
    footnote_ids 本段挂着的脚注（按出现顺序）

    返回 (新 LaTeX, 提示列表, 所有识别到的引文块)。
    """
    notes: list[str] = []
    blocks = find_quotes(latex)
    if not blocks:
        return latex, notes, []

    # 本段的第一个可用脚注就是这条引文的出处（实测原稿是「引文段 + 紧跟一条 pure_ref 脚注」）
    key = ""
    page = ""
    for fid in footnote_ids:
        if fid in keymap:
            key = keymap[fid]
            page = fn_pages.get(fid, "")
            break

    out = latex
    for b in blocks:
        # 提升的两条判据（满足其一即可）：
        #   ① 够长（默认 105 字，约三行）
        #   ② **含 ≥2 个句末标点** —— 这是「完整陈述」的特征。
        #
        # 为什么要加 ②：实测 Lei_Li 有一段 92 字的引文，人工把它放进了 quotation
        # 环境（`\hfill（\parencite{Hjortland2017}，第632页）`），但 92 < 105 被漏掉。
        # 它含 3 个「。」——是完整的多句陈述；而没被提升的都是
        # 「科学家实际在做什么」这类**不含句号的短语引用**。
        # 只看长度会把这二者混为一谈。
        if b.visible_len < threshold and _sentence_ends(b.content) < 2:
            continue
        b.key, b.page, b.used = key, page, True
        quoted, note = build_quotation(b.content, key, page)
        if note:
            notes.append(f"{note}（引文 {b.visible_len} 字）")
        # 只替换引号部分，保留引号之外的上下文
        out = out.replace(f"“{b.content}”", quoted, 1)
        key = page = ""  # 一条脚注只供一条引文

    return out, notes, blocks
