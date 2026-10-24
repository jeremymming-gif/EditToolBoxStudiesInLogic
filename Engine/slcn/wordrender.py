"""渲染辅助（B-207 从 docxsource 纯搬运）：标题分类、字符图、三线表/图片、题注提升、render_para。"""

from __future__ import annotations

import re

from .wordcore import (_has_cjk, escape_latex)
from .wordcitebody import (link_footnote_bare_citations)
from .wordmeta import (_HEAD_EN1, _HEAD_EN2, _HEAD_EN2_HALF, _HEAD_EN3)


#: 无样式英文小标题里允许的小写词（Title Case 判断用）
_TITLE_SMALL_WORDS = {
    "a", "an", "the", "and", "or", "nor", "but", "for", "of", "on", "in", "to",
    "at", "by", "with", "from", "as", "into", "about", "over", "under", "after",
    "before", "between", "through", "during", "without", "within", "against",
    "upon", "via", "per", "its",
}


def _looks_plain_en_title(t: str) -> bool:
    """无编号、无样式的一段英文短句，像不像章节标题（Title Case、无句末标点）。

    第 121 条（07 Hu_Liu 真稿）：访谈稿的 9 个小标题在 docx 里是**无样式独立短段**
    （`On Logic Pluralism`…），旧判据只认编号标题 → 产物一个 `\\section` 都没有。
    这里只做「像标题」的文本判据；是否真是标题由调用方结合上下文（前后段落）决定。
    """
    if not t or len(t) > 80 or _has_cjk(t):
        return False
    if re.search(r"[.?!;:。；：\u2019\u2018]", t):
        return False
    words = t.split()
    if not (2 <= len(words) <= 10):
        return False
    if not words[0][:1].isupper():
        return False
    for w in words:
        core = re.sub(r"[^A-Za-z'\-]", "", w)
        if not core:
            return False
        if core[0].isupper() or core.lower() in _TITLE_SMALL_WORDS:
            continue
        return False
    return True


#: 无样式英文小标题里允许的小写词（Title Case 判断用）
def classify_en_heading(t: str) -> tuple[int, str]:
    """判断一段英文文本是不是章节标题，返回 (层级, 标题)。层级 0 表示不是标题。

    保守起见，编号列表项不算标题：要求够短、不以句末标点结尾、且句中不含句点。
    """
    m = _HEAD_EN2_HALF.match(t)
    if m and len(t) < 130 and "." not in m.group("t"):
        return 2, m.group("t").strip()
    m = _HEAD_EN2.match(t)
    if m and len(t) < 130 and "。" not in t:
        return 2, m.group("t").strip()
    m = _HEAD_EN3.match(t)
    if m and len(t) < 130 and "." not in m.group("t"):
        return 2, m.group("t").strip()
    m = _HEAD_EN1.match(t)
    if m:
        rest = m.group("t")
        if (len(t) < 130 and "." not in rest
                and not rest.rstrip().endswith((".", "。", ";", "；", ":", "：", ","))
                and rest[:1].isupper()):
            return 1, rest.strip()
    return 0, t




def find_char_diagrams(paras, min_run: int = 8, max_len: int = 12) -> list[tuple[int, int]]:
    """找出「用一堆单字符段落拼出的图」。

    返回 [(起始下标, 连续段数), ...]。判据：连续 min_run 段以上，每段可见长度
    都不超过 max_len 且以单个字符为主。
    """
    runs: list[tuple[int, int]] = []
    i, n = 0, len(paras)
    while i < n:
        # 含内嵌图片的段落绝不能算进「字符拼图」——
        # 实测 01 Yanjin_Chen 的段落 77（3 张图、无文字）被相邻的单字符段
        # 吞掉，导致那一组图完全没有输出。
        if any(sg.kind == "image" for sg in paras[i].segs):
            i += 1
            continue
        t = paras[i].plain.strip()
        if t and len(t) <= max_len:
            j = i
            tiny = 0
            while j < n:
                if any(sg.kind == "image" for sg in paras[j].segs):
                    break  # 图片段切开连续区间
                tj = paras[j].plain.strip()
                if not tj:
                    j += 1
                    continue
                if len(tj) > max_len:
                    break
                if len(tj) <= 3:
                    tiny += 1
                j += 1
            if j - i >= min_run and tiny >= (j - i) * 0.7:
                runs.append((i, j - i))
                i = j
                continue
        i += 1
    return runs


# 矢量形状占位。页码在转换阶段由 slcn/office.py 定位后回填。
SHAPE_PLACEHOLDER = (
    "\n% {marker}\n"
    "% 【需人工截图】原稿此处有 {n} 个矢量图形（Word 绘图工具所画，无内嵌图片可提取）。\n"
    "% 打开原稿 PDF 找到对应位置，截图或重画后替换本段。\n"
    "\\begin{{center}}\n"
    "  \\fbox{{\\parbox{{0.8\\textwidth}}{{\\centering "
    "\\textbf{{【需人工截图：{n} 个矢量图形{page_hint}】}}}}}}\n"
    "\\end{{center}}\n"
)


def table_env(tbl, idx: int, img_names: dict[str, str] | None = None,
              caption: str | None = None) -> str:
    """把 Word 表格转成 LaTeX table + tabular。

    格式对齐人工成品惯例（见 Haiting_Huang / Yingying_Tang）：
        \\begin{table}[htbp]
          \\centering
          \\begin{tabular}{|c|c|c|}
          \\hline
          ... \\\\
          \\hline
          \\end{tabular}
          \\caption{...}
          \\label{tab:...}
        \\end{table}

    ``caption``（B-217③）：Word 题注段落解析出的说明文字（已转义）；``None`` 时
    与从前逐字节相同（占位「【需补充表题】」）。
    """
    ncol = max(tbl.ncols, 1)
    # 单元格文字长（>18 字）的列改用 p{} 定宽，否则 \\hline 之间会被撑爆版心
    long_col = [
        any(
            len(r[i].text) > 18
            or any(sg.kind == "image" for sg in r[i].segs)   # 含图列用 p{} 定宽
            for r in tbl.rows if i < len(r)
        )
        for i in range(ncol)
    ]
    any_long = any(long_col)
    # 第四十六批②续：含图片的表格必须能**跨页**（图片纵向排列后单行会超过一页，
    # 普通 tabular 不能跨页 → 图被裁掉、页数不增；86-01 实测 17 张图全挤在 p8 被切）。
    # 人工成品正是跨页表格（表头每页重复）。模板已加载 longtable（SLCN-MIX/SLEN）。
    has_img = any(
        any(sg.kind == "image" and (img_names or {}).get(sg.rid) for sg in c.segs)
        for r in tbl.rows for c in r
    )

    if any_long:
        w = round(0.92 / ncol, 3)
        _cols = ["p{" + str(w) + "\\textwidth}" for _ in range(ncol)]
    else:
        _cols = ["c" for _ in range(ncol)]
    # B-219①：三线表列格式**不带竖线**；网格表仍带（默认路径逐字节不变）。
    # 含图表走 longtable 分支（spec：该分支不改），因此三线表判据在这里排除它。
    _three = bool(getattr(tbl, "three_line", False)) and not has_img
    colspec = "".join(_cols) if _three else "|" + "|".join(_cols) + "|"

    def _row_tex(r, bars: bool = True) -> str:
        cells = []
        used = 0
        for c in r:
            txt = render_cell(c, img_names, soft_break=_three)
            if c.colspan > 1:
                cells.append(f"\\multicolumn{{{c.colspan}}}"
                             f"{{{'|c|' if bars else 'c'}}}{{{txt}}}")
                used += c.colspan
            else:
                cells.append(txt)
                used += 1
        while used < ncol:  # 补齐缺格，否则表格会错位
            cells.append("")
            used += 1
        return "  " + " & ".join(cells) + " \\\\"

    if _three:
        # B-219① 三线表 → booktabs（判据见 docxxml.Table.three_line；含图表仍走
        # longtable 分支，见下）。`\usepackage{booktabs}` 由 build.build_docx 按
        # 产物是否含 `\toprule` 注入，模板 .sty/.tex 一个字都不改。
        lines = ["\n\\begin{table}[htbp]", "  \\centering",
                 f"  \\begin{{tabular}}{{{colspec}}}", "  \\toprule"]
        if tbl.rows:
            lines.append(_row_tex(tbl.rows[0], bars=False))
            lines.append("  \\midrule")
            for r in tbl.rows[1:]:
                lines.append(_row_tex(r, bars=False))
        lines.append("  \\bottomrule")
        lines.append("  \\end{tabular}")
        lines.append(f"  \\caption{{{caption}}}" if caption is not None
                     else "  \\caption{【需补充表题】}")
        lines.append(f"  \\label{{tab:{idx}}}")
        lines.append("\\end{table}\n")
        return "\n".join(lines)

    if has_img:
        head = _row_tex(tbl.rows[0]) if tbl.rows else ""
        _cap = f"\\caption{{{caption}}}" if caption is not None \
            else "\\caption{【需补充表题】}"
        lines = ["\n\\begin{longtable}{" + colspec + "}",
                 f"  {_cap}\\label{{tab:{idx}}}\\\\",
                 "  \\hline", head, "  \\hline", "  \\endfirsthead",
                 "  \\hline", head, "  \\hline", "  \\endhead"]
        for r in tbl.rows[1:]:
            lines.append(_row_tex(r))
            lines.append("  \\hline")
        lines.append("\\end{longtable}\n")
        return "\n".join(lines)

    lines = ["\n\\begin{table}[htbp]", "  \\centering"]
    lines.append(f"  \\begin{{tabular}}{{{colspec}}}")
    lines.append("  \\hline")
    for r in tbl.rows:
        lines.append(_row_tex(r))
        lines.append("  \\hline")
    lines.append("  \\end{tabular}")
    lines.append(f"  \\caption{{{caption}}}" if caption is not None
                 else "  \\caption{【需补充表题】}")
    lines.append(f"  \\label{{tab:{idx}}}")
    lines.append("\\end{table}\n")
    return "\n".join(lines)


def render_cell(cell, img_names: dict[str, str] | None = None,
                soft_break: bool = False) -> str:
    """渲染一个单元格（文字 / 公式 / **图片**）。

    第四十六批②续：表格单元格内嵌图片（86-01 实测 18 张）旧实现直接丢弃；这里按
    `img_names`（rId → figN.ext）输出 `\\includegraphics`。**无图单元格**的输出与
    从前逐字节相同（只有 text/math 分支）。

    `soft_break`（B-219①，默认 False=旧行为）：三线表的 p{} 定宽列里，单元格内换行
    渲染成 `\\newline `（spec §2①「单元格内换行」）；网格表仍用空格连接。
    """
    parts = []
    for sg in cell.segs:
        if sg.kind == "text":
            parts.append(escape_latex(sg.text).replace(
                "\n", "\\newline " if soft_break else " ").strip())
        elif sg.kind == "math":
            parts.append(f"${sg.text}$")
        elif sg.kind == "image":
            _name = (img_names or {}).get(sg.rid)
            if _name:
                _h = getattr(sg, "h_pt", 0.0)
                if 0 < _h <= 60:
                    # 第四十七批：表内 VML 公式图 → 行内原尺寸 + 缺文件占位
                    parts.append(
                        "\\IfFileExists{" + _name + "}{\\includegraphics[height="
                        + f"{_h:.2f}" + "pt]{" + _name + "}}"
                        "{\\fbox{\\scriptsize 公式图缺失}}")
                else:
                    # 每张图独占一行（`\par`）：原稿图在单元格里是**纵向排列**的
                    # （86-01 实测人工 PDF 把 17 张图分摊在 3 页；inline 塞进一段会全挤在
                    #  一页、页面高度不增）。`width=\linewidth` 按图像列宽排。
                    parts.append(
                        "\\IfFileExists{" + _name + "}{\\includegraphics"
                        "[width=0.8\\linewidth]{" + _name + "}}"
                        "{\\fbox{\\scriptsize 公式图缺失}}\\par")
    return " ".join(p for p in parts if p).strip()


def figure_env(names: list[str], idx: int, caption: str | None = None) -> str:
    """生成 figure 环境。人工惯例见 07 Liangying_Zhao。

    同一段落里连续的图片**合成一个 figure**——实测 01 Yanjin_Chen 的 docx 里有
    5 张分开的图，人工是把它们合成一张 `p1.png` 放进一个 figure 的。

    ``caption``（B-217③）：Word 题注段落解析出的说明文字（已转义）；``None`` 时
    与从前逐字节相同（占位「【需补充图题】」）。
    """
    if not names:
        return ""
    lines = ["\n\\begin{figure}[htbp]", "\\centering"]
    if len(names) == 1:
        lines.append(f"\\includegraphics[width=0.85\\textwidth]{{{names[0]}}}")
    else:
        # 多张：两两一行，宽度按张数分摊
        w = 0.9 / min(len(names), 2)
        for i in range(0, len(names), 2):
            pair = names[i : i + 2]
            lines.append("".join(
                f"\\includegraphics[width={w:.2f}\\textwidth]{{{n}}}" for n in pair
            ))
    lines.append(f"\\caption{{{caption}}}" if caption is not None
                 else "\\caption{【需补充图题】}")
    lines.append(f"\\label{{fig:{idx}}}")
    lines.append("\\end{figure}\n")
    return "\n".join(lines)


def hoist_quote_fallback(latex: str, quoted: str) -> tuple[str, bool]:
    """引文被行内标记（斜体 / 公式 / 脚注）切开时的退化替换：只替换第一处长引文。

    `render_para` 正常路径是精确替换 `“<content>”`；内容被 `\textit{}` 等标记切开时
    精确串对不上，才走这里。

    ⚠️ 第一处匹配里若含 `\\footnote`，说明**脚注标记插在引文中间**（Word 稿很常见：
       脚注挂在引文第 20 个字后面）。这时整段替换会把脚注正文一起吃掉，而正则实际
       匹配到的是脚注正文里的一对**短引号** → 引文被重复一遍、多出一个 `}`、
       整篇编译失败（`! Too many }'s`）。实测期 87 子案例 3（04 Fayang_Ye，案例 141）。
       此时**放弃提升**（保持原文内联）——人工成品也是这么处理的。

    返回 (新文本, 是否替换)。替换必须用字符串切片而不是 `re.sub` 的替换串：
    `quoted` 里含 `\\hfill`，而 `\\h` 在 re 替换串里是非法转义（实测 2 篇抛
    bad escape \\h）。
    """
    m = re.search(r"“[^”]{20,}”", latex)
    if not m:
        return latex, False
    if "\\footnote" in m.group(0):
        return latex, False
    return latex[: m.start()] + quoted + latex[m.end():], True


def render_para(
    para, fn_objs: dict, bib_sink: list, keymap: dict,
    fn_pages: dict | None = None, quote_threshold: int = 105,
    img_names: dict | None = None,
    fig_counter: list[int] | None = None,
    c_class_union: bool = False,
    num2key: dict[int, str] | None = None,
    fig_caption: str | None = None,
    eq_counter: list[int] | None = None,
    caption_only: bool = False,
) -> tuple[str, list[str]]:
    """把一段 XML 片段渲染成 LaTeX。

    - 普通文字 → 转义；带斜体 → \\textit{}
    - OMML 公式 → $...$
    - 脚注 → 按分类分别处理（见 slcn/footnotes.py）
    - 超过三行的引文 → 提升为 quotation 环境（见 slcn/quotations.py）

    fig_counter  **文档级图序号计数器**（P2-0）：`parse_docx()` 在整篇范围内持有同一个
                 `[0]` 并传进来，图 `\\label{fig:N}` 的 N 才是文档内唯一的（旧实现把
                 `fig_seq` 放在本函数内、每个段落从 1 重来 → 多段有图时 label 全叫
                 `fig:1`，实测 corpus 12 篇有图 label 的产物里 6 篇重复，
                 `2025_04_YingJin_Xu` 6 个全是 fig:1）。默认 None = 保留段落内计数
                 （只给不传计数器的旧调用方/单测用，行为与从前逐字节相同）。
    num2key      **B-130（批73）**：编号体例稿的 `{编号 → bib 键}` 可靠映射。非空时把
                 **脚注文本里的裸 `[N]`**（`参见[12]。`）保守换成 `\\parencite{键}`
                 （判据见 `link_footnote_bare_citations`；越界/无引导语证据 → 原样 +
                 warning）。默认 None = 不做，与从前逐字节相同（正文裸编号不在本卡）。
    fig_caption  **B-217③**：本段图对象对应的 Word 题注说明文字（已转义，调用方
                 只在「题注与图片唯一相邻」时传）；本段第一个 figure 用它当
                 `\\caption`，其余 figure/无题注保持占位。默认 None = 逐字节同从前。
    eq_counter   **B-219②**：文档级独立公式计数器 `[0]`（parse_docx 只有在正文
                 「式（N）/公式（N）」引用能与独立公式数对齐时才传同一个 `[0]`）。
                 传了就按文档顺序把 `m:oMathPara` 渲染成
                 `\\begin{equation}…\\label{eq:N}\\end{equation}`；默认 None =
                 仍是 `\\[…\\]`，与从前逐字节相同。
    caption_only **B-219④(a)**：本段是「图片对象 + 同段内联题注」——题注文字只进
                 `\\caption`，不再重复渲染进正文。默认 False = 旧行为。

    返回 (LaTeX, 提示列表)。
    """
    from .quotations import build_quotation, find_quotes

    fn_pages = fn_pages or {}
    img_names = img_names or {}
    notes: list[str] = []

    # 先判断本段是否有「超过三行的引文」。若有，它挂着的第一个参考文献脚注
    # 会被这条引文消费掉（人工惯例：引文 + \hfill（\parencite{}，第 N 页）），
    # 不能再作为普通脚注输出一遍。
    plain = "".join(sg.text for sg in para.segs if sg.kind == "text")
    # 提升判据（满足其一即可）：① 够长；② 含 ≥2 个句末标点（完整陈述）。
    # ⚠️ 这里先前只判长度，于是 Bo_Chen 那段 92 字、含 3 个「。」的引文
    #    （人工放进了 quotation 环境）被漏掉了。
    #   注：slcn/quotations.py 里的 hoist_quotes() 也有一套判据，但**全项目无人调用**，
    #   是死代码；真正生效的是下面这一行。两处判据要一起改，别只改一处。
    from .quotations import _sentence_ends as _se

    long_quotes = [
        q for q in find_quotes(plain)
        if q.visible_len >= quote_threshold or _se(q.content) >= 2
    ]
    hoist = long_quotes[0] if long_quotes else None
    consumed_fid = None
    if hoist is not None:
        for fid in para.footnote_ids:
            if fid in keymap:
                consumed_fid = fid
                break

    out: list[str] = []
    fig_seq = 0

    def _fn_note(text: str) -> str:
        """B-130：脚注文本里的裸编号引用 → `\\parencite`（仅编号体例稿；否则原样）。

        `num2key` 为空（人年制/无可靠编号映射的稿子）= 默认零变化：一个字都不动。
        """
        if not num2key or not text:
            return text
        new, _n, _iss = link_footnote_bare_citations(text, num2key)
        if _iss:
            notes.extend(_iss)
        return new

    def flush_figs(buf: list[str]) -> None:
        nonlocal fig_seq
        if buf:
            # P2-0：优先用**文档级**计数器（parse_docx 传同一个 [0]）→ 图 label 全局唯一；
            # 没传（旧调用方/单测）才退回段落内计数，行为与从前一致。
            if fig_counter is not None:
                fig_counter[0] += 1
                seq = fig_counter[0]
            else:
                fig_seq += 1
                seq = fig_seq
            cap, fig_cap_left[0] = fig_cap_left[0], None
            out.append(figure_env(list(buf), seq, caption=cap))
            buf.clear()

    fig_cap_left = [fig_caption]   # B-217③：题注只并入本段第一个 figure
    fig_buf: list[str] = []
    shape_buf: list[int] = []
    for seg in para.segs:
        if seg.kind == "text":
            if caption_only:
                continue          # B-219④(a)：同段内联题注只进 \caption，不重复进正文
            t = escape_latex(seg.text)
            if not t.strip():
                out.append(t)
                continue
            if seg.italic:
                # 只把有内容的斜体片段包起来，避免产生空的 \textit{}
                lead = t[: len(t) - len(t.lstrip())]
                trail = t[len(t.rstrip()) :]
                out.append(f"{lead}\\textit{{{t.strip()}}}{trail}")
            else:
                out.append(t)
        elif seg.kind == "math":
            # 第四十四批①：行间公式（m:oMathPara）→ `\[...\]`，行内仍 `$...$`
            if getattr(seg, "display", False):
                if eq_counter is not None:
                    # B-219②（拍板 A）：编号只在「正文引用可与独立公式顺序对齐」
                    # 时由 parse_docx 传计数器启用；编号 N = 文档内第 N 个独立公式。
                    eq_counter[0] += 1
                    out.append(
                        f"\\begin{{equation}}\n{seg.text}\n"
                        f"\\label{{eq:{eq_counter[0]}}}\n\\end{{equation}}")
                else:
                    out.append(f"\\[{seg.text}\\]")
            else:
                out.append(f"${seg.text}$")
        elif seg.kind == "shape":
            shape_buf.append(1)
        elif seg.kind == "image":
            name = img_names.get(seg.rid, "")
            if not name:
                continue
            _h = getattr(seg, "h_pt", 0.0)
            if 0 < _h <= 60:
                # 第四十七批：VML 公式图（MathType 预览图）→ **行内**按原尺寸渲染。
                # `\IfFileExists`：soffice 缺失/转换失败时显示「公式图缺失」占位，
                # 不阻断出稿（外部可选依赖，见 docxxml._soffice_bin）。
                out.append(
                    "\\IfFileExists{" + name + "}{\\raisebox{-0.22\\height}"
                    "{\\includegraphics[height=" + f"{_h:.2f}" + "pt]{" + name + "}}}"
                    "{\\fbox{\\scriptsize 公式图缺失}}"
                )
            else:
                fig_buf.append(name)
        elif seg.kind == "footnote":
            flush_figs(fig_buf)  # 图片与脚注之间断开
            if seg.fid == consumed_fid:
                continue  # 已被引文消费
            fn = fn_objs.get(seg.fid)
            if fn is None:
                continue
            if fn.kind in ("pure_ref", "pure_citation", "pure_citation_matched"):
                # 第四十九批（C 类新口径，**开关默认 OFF**）：ON 时纯引用脚注 →
                # 正文 \parencite，脚注删除；页码进可选参数 `[p.13]`（biblatex
                # 样式自己产生括号）。OFF 时保持旧行为（中文括号包裹）逐字节不变。
                key = keymap.get(fn.fid, "")
                loc = f"[{fn.locator}]" if fn.locator else ""
                if c_class_union:
                    out.append(f"\\parencite{loc}{{{key}}}")
                else:
                    out.append(f"（\\parencite{loc}{{{key}}}）")
            elif fn.kind in ("mixed_split_entry", "mixed_split_cite"):
                # B-199：真 mixed 拆分：说明文字保留 + 原位注入 `\parencite`。
                key = keymap.get(fn.fid, "")
                loc = f"[{fn.locator}]" if fn.locator else ""
                before = getattr(fn, "note_before_latex", "") or ""
                after = getattr(fn, "note_after_latex", "") or ""
                out.append(f"\\footnote{{{before}\\parencite{loc}{{{key}}}{after}}}")
            elif fn.kind in ("citation_unmatched", "mixed_manual"):
                # C 类新口径：未匹配短引用/混合型 → 保留脚注原文（不编造条目不丢内容）
                body_note = _fn_note(
                    (getattr(fn, "note_latex", "")
                     or escape_latex(fn.note or getattr(fn, "ref_raw", "")
                                     or fn.text)))
                out.append(f"\\footnote{{{body_note}}}")
            elif fn.kind in ("mixed", "mixed_citation"):
                if c_class_union:
                    # ON：混合型（说明文字+引用）→ 保留脚注，说明文字原样
                    #（引用已由 link_citations 替换），build 汇总「请人工核对」warning。
                    body_note = _fn_note(
                        (getattr(fn, "note_latex", "")
                         or escape_latex(fn.note or fn.text)))
                    out.append(f"\\footnote{{{body_note}}}")
                else:
                    # OFF：旧行为（逐字节不变）
                    key = keymap.get(fn.fid, "")
                    loc = f"[{fn.locator}]" if fn.locator else ""
                    note = escape_latex(fn.note)
                    out.append(f"\\footnote{{cf. \\parencite{loc}{{{key}}}. {note} }}")
            else:
                # 纯注释 / 古典文献：原样保留（英文注释已预先转义+替换引用）
                body_note = _fn_note(
                    getattr(fn, "note_latex", "") or escape_latex(fn.text))
                out.append(f"\\footnote{{{body_note}}}")

    flush_figs(fig_buf)
    if shape_buf:
        notes.append(f"矢量形状 {len(shape_buf)} 个（需截图）")
        out.append(SHAPE_PLACEHOLDER.format(n=len(shape_buf), marker="需人工截图", page_hint=""))
    latex = "".join(out)

    # 把超长引文提升为引用环境
    if hoist is not None:
        key = keymap.get(consumed_fid, "") if consumed_fid else ""
        page = fn_pages.get(consumed_fid, "") if consumed_fid else ""
        quoted, note = build_quotation(hoist.content, key, page)
        if note:
            notes.append(f"{note}（引文 {hoist.visible_len} 字）")
        target = f"“{hoist.content}”"
        if target in latex:
            latex = latex.replace(target, quoted, 1)
            if not key:
                # B-220 N7：引文出处已由正文引用替换解析出来，只是落在关闭引号**之后**
                # （`“……。”（\parencite{K}，第 N 页）`）。旧实现两处都写：环境里留
                # 「【需补充参考文献与页码】」占位、真引用挂在 \end{quotation} 之后
                # （实测 90-05 7 处；人工惯例是把引用放进环境 `\hfill（…）`）。
                # 只在这个**紧随其后**的引用完全可解析时合并；否则保持原样。
                _pos = latex.find(quoted)
                if _pos >= 0:
                    _tail = latex[_pos + len(quoted):]
                    # 源稿常见 `……。”。（[5]，第40页）`：关闭引号后先有句号再是括号引用。
                    _lead = re.match(r"[。.．]?\s*", _tail)
                    _tail = _tail[_lead.end():]
                    _m = re.match(
                        r"（\\parencite\{([^}]+)\}(?:，第\s*([^）]+?)\s*页)?）", _tail)
                    _key2, _page2 = "", ""
                    if _m:
                        _key2, _page2 = _m.group(1), _m.group(2) or ""
                        _end = _m.end()
                    else:
                        # 编号清单体例：渲染期 trailing 引用还是纯文本 `（[18]，第258页）`，
                        # 由 build 的 ②.5 事后替换；这里用同一份编号映射就地折算。
                        _m2 = re.match(
                            r"（\[(\d+)\](?:[，,]\s*第\s*([^）]+?)\s*页)?）", _tail)
                        if _m2 and num2key:
                            _key2 = num2key.get(int(_m2.group(1)), "")
                            _page2 = _m2.group(2) or ""
                            _end = _m2.end()
                        else:
                            _key2 = ""
                    if _key2:
                        _quoted2, _note2 = build_quotation(
                            hoist.content, _key2, _page2)
                        latex = latex[:_pos] + _quoted2 + _tail[_end:]
                        if _note2:
                            notes.append(f"{_note2}（引文 {hoist.visible_len} 字）")
        else:
            # 引文被斜体/公式/脚注标记切开时，退化匹配（见 hoist_quote_fallback 注释）
            latex, _hoisted = hoist_quote_fallback(latex, quoted)
            if not _hoisted:
                notes.append(
                    "引文内含脚注标记，未提升为 quotation 环境（保持原文内联，避免脚注被拆）"
                )
        if consumed_fid:
            keymap.pop(consumed_fid, None)  # 已产出，避免 bib 重复

    return latex, notes


