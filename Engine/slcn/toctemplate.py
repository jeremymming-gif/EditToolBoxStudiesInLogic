r"""目录模板：**以上一期的成品 .tex 为模板**（第 94 条，从 mac 的 `TocTemplate.swift` 搬进引擎）。

为什么是「继承上一期」而不是内置写死：原版把头部（卷期号、日期、版式参数）写死在代码里，
值停在 2022 年；而同事每期都是在**上一期成品**的基础上手工调整版式。
所以最省事的做法是继承上一期的头部与尾部，只替换本期的卷期信息与中间条目 ——
这样版式调整会自动延续，不用每期重调。

三块结构（`split` 切出来）：

    head        —— 文件开头到 tabularx 的**列定义行**为止（含）
    columnBlock —— 列定义行与 `\end{tabularx}` 之间的栏目行（专栏/专辑/主持人/间距）
    tail        —— 从 `\end{tabularx}` 到文件末尾

来源优先级（由 `slcn/toc.py` 决定）：本期 `000 Table of Contents` 里已有的成品 → 上一期 → 内置模板
（内置模板由 `tools/gen_content_templates.py` 从 Swift 源生成，见 `slcn/content_templates.py`）。
"""

from __future__ import annotations

import os
import re

from . import content_templates as _tpl

BEGIN_TABULARX = "\\begin{tabularx}"
END_TABULARX = "\\end{tabularx}"

#: `000 Table of Contents` 子目录名（与原版/mac 一致）
TOC_DIR = "000 Table of Contents"

_PREV_ISSUE_RE = re.compile(r"^(20\d\d)年第(\d+)期")
_TOTAL_ISSUE_RE = re.compile(r"[（(](\d+)[）)]")
#: 形如 `\\[3.2 ex]` / `\\[4mm]` 的间距行（注意是**两个**反斜杠 + 方括号）
_SPACING_RE = re.compile(r"^\s*\\\\\[\s*[\d.]+\s*(ex|mm|pt)\s*\]")
def is_comment_line(line: str) -> bool:
    """这一行是否被注释掉（行首 `%`；前导空白不算）。

    ⚠️ 必须只认行首：`\begin{tabularx}{...}%调整版式` 这种**行尾**注释是正常内容。
    """
    return line.lstrip().startswith("%")


def previous_issue_folder(issue_folder: str) -> str | None:
    """由期目录名推上一期目录，例如 `2026年第5期(90)` → `2026年第4期(89)`。

    跨年时退回上一年第 6 期（本刊是双月刊，每年 6 期）。
    """
    folder = os.path.normpath(issue_folder)
    parent = os.path.dirname(folder)
    name = os.path.basename(folder)

    m = _PREV_ISSUE_RE.match(name)
    if not m:
        return None
    year, number = int(m.group(1)), int(m.group(2))
    target_year, target_number = year, number - 1
    if target_number < 1:
        target_year, target_number = year - 1, 6

    prefix = f"{target_year}年第{target_number}期"
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return None
    match = next((e for e in entries if e.startswith(prefix)), None)
    return os.path.join(parent, match) if match else None


def total_issue_number(issue_folder: str) -> str | None:
    """从期目录名里取「总第 N 期」的 N，例如 `2026年第5期(90)` → `90`。"""
    name = os.path.basename(os.path.normpath(issue_folder))
    m = _TOTAL_ISSUE_RE.search(name)
    if not m:
        return None
    return m.group(1) or None


def load(folder: str, file_name: str):
    """从某期目录的 `000 Table of Contents/<file_name>` 读模板，返回 Parts 或 None。"""
    path = os.path.join(folder, TOC_DIR, file_name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    return split(text, source=os.path.basename(os.path.normpath(folder)))


def builtin(file_name: str):
    """找不到可继承的成品时的兜底：程序内置的原始模板。"""
    is_chinese = file_name.startswith("Chinese Content")
    head_raw = _tpl.chContentHead if is_chinese else _tpl.enContentHead
    tail_raw = _tpl.chContentEnd if is_chinese else _tpl.enContentEnd
    tail = tail_raw[1:] if tail_raw.startswith("\n") else tail_raw

    parts = split(head_raw + "\n\n%中间主体\n\n" + tail, source="内置模板")
    parts.tail = tail
    return parts


class Parts:
    """模板的三块 + 来源说明。字段名对应 Swift 的 `TocTemplateParts`。"""

    __slots__ = ("head", "columnBlock", "tail", "source")

    def __init__(self, head: str, columnBlock: list[str], tail: str, source: str):
        self.head = head
        self.columnBlock = columnBlock
        self.tail = tail
        self.source = source

    def __repr__(self) -> str:  # 便于测试里看
        return (f"Parts(source={self.source!r}, head={len(self.head)} 字符, "
                f"columnBlock={len(self.columnBlock)} 行, tail={len(self.tail)} 字符)")


def split(text: str, source: str) -> Parts:
    """把一份 .tex 切成 head / 栏目块 / tail。

    列定义行有两种写法（历史文件里两种都有）：
      · 英文模板写在同一行：`\\begin{tabularx}{\\textwidth}{l@{\\hspace{1em}}r}`
      · 中文模板写在下一行，且**常见一行被注释掉的旧列定义要先跳过**
    判据用 `}{` 出现的次数：`\\begin{tabularx}{\\textwidth}` 只有 1 处，
    `\\begin{tabularx}{\\textwidth}{l@...}` 有 2 处。
    """
    lines = text.split("\n")

    # ⚠️ 只认**没被注释掉**的 `\begin/\end{tabularx}`：89 期人工 contents.tex 的
    #    第 20 行留着一行注释掉的旧列定义 `%\begin{tabularx}{...}`，从前会被当成真模板起点，
    #    于是「本期/上一期模板」把注释版列定义搬进新 tex → 英文目录 xelatex 直接失败（B-226 E4）。
    begin_idx = next((i for i, ln in enumerate(lines)
                      if BEGIN_TABULARX in ln and not is_comment_line(ln)), None)
    if begin_idx is None:
        return Parts(head=text, columnBlock=[], tail="", source=source)

    spec_idx = begin_idx
    brace_joints = lines[begin_idx].count("}{")
    if brace_joints < 2:
        i = begin_idx + 1
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("%"):
                i += 1
                continue
            spec_idx = i
            break

    end_idx = next((i for i, ln in enumerate(lines)
                    if END_TABULARX in ln and not is_comment_line(ln)), None)
    if end_idx is None:
        return Parts(head="\n".join(lines[:spec_idx + 1]), columnBlock=[], tail="", source=source)

    column_block: list[str] = []
    spacing_line: str | None = None
    for line in lines[spec_idx + 1:end_idx]:
        if is_comment_line(line):
            continue          # 注释行不是本期版式的一部分（87 的注释栏目行不复活，S5）
        if "专栏" in line or "专辑" in line or "Column:" in line:
            column_block.append(line)
        elif "主持人" in line:
            column_block.append(line)
        elif spacing_line is None and "vdistance" not in line and _SPACING_RE.match(line):
            spacing_line = line
    if column_block and spacing_line is not None:
        column_block.append(spacing_line)

    return Parts(head="\n".join(lines[:spec_idx + 1]),
                 columnBlock=column_block,
                 tail="\n".join(lines[end_idx:]),
                 source=source)


def patch_header(head: str, *, volume: str, issue: str, year: str,
                 total_issue: str, publish_month: str, is_chinese: bool) -> str:
    """把头部里的卷期号与出版日期换成本期的值。

    匹配的是**形状**而不是具体数字，所以上一期填的是什么都能替换。
    """
    if is_chinese:
        # 第\ 19 卷，第\ 5 期，总第\ 90 期  —— 反斜杠后面那个空格是模板的写法，要保留
        head = re.sub(r"第\\\s*\d+\s*卷，第\\\s*\d+\s*期，总第\\\s*\d+\s*期",
                      lambda _m: f"第\\ {volume} 卷，第\\ {issue} 期，总第\\ {total_issue} 期",
                      head)
        # 2026 年第\ 5 期，10月\ 18 日出版
        head = re.sub(r"\d+\s*年第\\\s*\d+\s*期，\d+\s*月\\\s*\d+\s*日出版",
                      lambda _m: f"{year} 年第\\ {issue} 期，{publish_month}月\\ 18 日出版",
                      head)
    else:
        month_name = english_month_name(int(publish_month) if publish_month.isdigit() else 0)
        head = re.sub(r"Volume\s+\d+,\s*Number\s+\d+,\s*[A-Za-z]+\s+\d{4}",
                      lambda _m: f"Volume {volume}, Number {issue}, {month_name} {year}",
                      head)
    return head


def ensure_windows_fontset(head: str) -> str:
    r"""保证 `\documentclass[...]{ctexart}` 里带 `fontset=windows`（与期刊正文同一套字体）。

    已经指定了 fontset 的模板原样保留。
    """
    m = re.search(r"\\documentclass\[[^\]]*\]\{ctexart\}", head)
    if not m:
        return head
    line = m.group(0)
    if "fontset=" in line:
        return head
    new_line = line.replace("]{ctexart}", ",fontset=windows]{ctexart}")
    return head[:m.start()] + new_line + head[m.end():]


def english_month_name(month: int) -> str:
    names = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    return names[month] if 1 <= month <= 12 else ""


# ── 列格式规范化（B-355：中文目录横向几何）──────────────────────────────
#
# 背景（证据：`evidence/b120-toc-analysis/report.md` §3–§5）：继承模板的列定义是逐期手调的
# 负胶（90/89：`{l@{\hspace{-7.5em}}r@{\hspace{-4.5em}}r}`；88：`-0.3em/-4.2em`）。
# 普通期没有 `~~~~ ` 前缀抵消负胶，作者列被左拉 7.5em 压进标题、页码列被左拉 4.5em 压进作者；
# 且 `l` 列自然宽合计（≈209pt）远小于版心 369.9pt，tabularx 没有 X 列时内容 glue 落在右侧
# → 右侧空白约 48mm。B-355 起在生成前统一改写为「X 弹性标题列 + 正胶列距」，
# 只对历史 l/r/r（中 3 列）与 l/r（英 2 列）模板安全；列数不符 / 4 列 / 已含自定义 X 列
# 一律不猜，抛 `TemplateLayoutError` 由调用方告警并回退内置模板（spec D2）。

class TemplateLayoutError(ValueError):
    r"""继承模板的 tabularx 列定义无法安全规范化（B-355 D2）。

    调用方（`toc.generate_tex`）捕获后 log 告警并回退内置模板；不在此处静默吞掉，
    否则「无法识别」会悄悄生成一份更差的版式。
    """


#: 普通期中文目录列格式：标题弹性列 + 作者右对齐 + 页码右对齐（正胶 2em / 1.5em）。
#: 与 `evidence/b120-toc-analysis/fix-sample.diff` 里已实测 1 页、0 重叠的写法一致。
CANONICAL_ZH = ("{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}r"
                "@{\\hspace{1.5em}}r@{}}")

#: 栏目期中文目录列格式（B-355 D3）：作者列改成固定宽 `p{0.26\textwidth}` 并允许格内换行。
#: 依据 `column-test/author-width/README.md`：89 期「刘奋荣，雷欧·范德托 / 文」这类长作者串
#: 用自然宽 r 列会把标题列挤到 8–10 字/行；0.26\textwidth（约 96pt）实测 1 页 0 警告、
#: 标题约 14 字/行、作者格内自动换行。
CANONICAL_ZH_COLUMN = ("{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}"
                       ">{\\raggedleft\\arraybackslash}p{0.26\\textwidth}"
                       "@{\\hspace{1.5em}}r@{}}")

#: 英文目录列格式：标题弹性列 + 页码右对齐（作者与标题同一格；正胶 2em）。
CANONICAL_EN = "{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}r@{}}"


def canonical_column_spec(file_name: str, *, is_column: bool = False) -> str:
    """按语言/是否栏目期返回规范列格式（`toc.generate_tex` 与测试共用同一口径）。"""
    is_chinese = file_name.startswith("Chinese Content")
    if is_chinese:
        return CANONICAL_ZH_COLUMN if is_column else CANONICAL_ZH
    return CANONICAL_EN


def _skip_group(text: str, start: int) -> int:
    """返回从 `text[start] == '{'` 开始的花括号组的结束下标（闭括号之后）。"""
    if start >= len(text) or text[start] != "{":
        raise TemplateLayoutError("列定义不是 {...} 形式")
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise TemplateLayoutError("列定义花括号不配对")


def count_columns(spec: str) -> int:
    r"""数列定义里的列数（只认 tabularx 常用列写法，用于 D2 的「列数不符」判据）。

    支持：单字母列 `X l c r`、定宽/偏窄列 `p{...} m{...} b{...}`、间距 `@{...}`、
    前缀声明 `>{...}`。遇到不认识的说明符抛 `TemplateLayoutError`（不猜）。
    """
    s = spec.strip()
    if len(s) < 2 or not (s.startswith("{") and s.endswith("}")):
        raise TemplateLayoutError("列定义不是 {...} 形式")
    body = s[1:-1]
    count = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "@":
            i += 1
            if i >= len(body) or body[i] != "{":
                raise TemplateLayoutError("列定义里的 @ 后不是 {...}")
            i = _skip_group(body, i)
            continue
        if ch == ">":
            i += 1
            if i >= len(body) or body[i] != "{":
                raise TemplateLayoutError("列定义里的 > 后不是 {...}")
            i = _skip_group(body, i)
            continue
        if ch in "pmb" and i + 1 < len(body) and body[i + 1] == "{":
            i = _skip_group(body, i + 1)
            count += 1
            continue
        if ch in "Xlcr":
            count += 1
            i += 1
            continue
        raise TemplateLayoutError(f"未知的列说明符 {ch!r}")
    return count


def _looks_like_commented_begin(line: str) -> bool:
    """被注释掉的旧 `\\begin{tabularx}...` 行（89 期英文模板在生效行上一行留过一条）。"""
    s = line.lstrip()
    return s.startswith("%") and BEGIN_TABULARX in s


def _looks_like_commented_spec(line: str) -> bool:
    """被注释掉的旧列定义行（如 89 期 `%{l@{\\hspace{-12.7em}}r@{\\hspace{0.45em}}r}`）。"""
    s = line.lstrip()
    if not s.startswith("%"):
        return False
    body = s[1:].strip()
    return body.startswith("{") and "@{" in body


def _check_spec_recognizable(spec: str, target: str, expected: int) -> None:
    """D2 识别判据：已是规范形 → 放行；已含自定义 X 列 → 不猜；列数不符 → 不猜。"""
    if spec.strip() == target:
        return
    if "X" in spec:
        raise TemplateLayoutError(
            "列定义已含自定义 X 列（非历史 l/r 模板），无法安全规范化")
    count = count_columns(spec)
    if count != expected:
        raise TemplateLayoutError(
            f"列数 {count} 与预期 {expected} 不符（可能是 4 列/自定义模板）")


def canonicalize_head(head: str, file_name: str, *, is_column: bool = False) -> str:
    r"""把继承 head 里的 tabularx 列定义改写成规范形（B-355 D2/D3）。

    两种历史写法都处理（与 `split` 的判据一致）：
      · 列定义与 `\begin{tabularx}` 同行（英文模板常见）→ 替换该行最后的 `{...}`；
      · 列定义在下一行（中文模板常见）→ 替换第一行非注释的 `{...}` 行。
    同时删掉列定义前后被注释掉的旧列定义行（英文 89 期 `%\begin{tabularx}...`、
    中文 89 期 `%{l@{\hspace{-12.7em}}...}`）——不把历史手调版式复活。

    幂等：已经等于规范形的模板原样返回（下一期继承本期落位件时不会反复改写，也不会
    因为「已含 X」被误判成无法识别）。

    无法识别时抛 `TemplateLayoutError`，由调用方回退内置模板。
    """
    is_chinese = file_name.startswith("Chinese Content")
    target = canonical_column_spec(file_name, is_column=is_column)
    expected = 3 if is_chinese else 2
    lines = head.split("\n")
    begin_idx = next((i for i, ln in enumerate(lines)
                      if BEGIN_TABULARX in ln and not is_comment_line(ln)), None)
    if begin_idx is None:
        raise TemplateLayoutError("找不到未注释的 \\begin{tabularx}")

    # ---- 写法一：列定义与 \begin{tabularx} 同一行 ----
    if lines[begin_idx].count("}{") >= 2:
        line = lines[begin_idx]
        start = line.rfind("}{") + 1
        end = _skip_group(line, start)
        _check_spec_recognizable(line[start:end], target, expected)
        if line[start:end].strip() != target:
            lines[begin_idx] = line[:start] + target + line[end:]
        # 删掉紧邻的旧注释 \begin 行（只删注释行，未注释的上方内容原样保留）
        while begin_idx > 0 and _looks_like_commented_begin(lines[begin_idx - 1]):
            del lines[begin_idx - 1]
            begin_idx -= 1
        return "\n".join(lines)

    # ---- 写法二：列定义在下一行（中间可能夹着注释掉的旧列定义）----
    spec_idx: int | None = None
    i = begin_idx + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("%"):
            i += 1
            continue
        spec_idx = i
        break
    if spec_idx is None:
        raise TemplateLayoutError("\\begin{tabularx} 之后找不到列定义行")

    raw = lines[spec_idx]
    lead = raw[:len(raw) - len(raw.lstrip())]
    body = raw.lstrip()
    if not body.startswith("{"):
        raise TemplateLayoutError("列定义行不是 {...} 形式")
    end = _skip_group(body, 0)
    spec = body[:end]
    _check_spec_recognizable(spec, target, expected)
    trailing = body[end:]
    comment = trailing[trailing.index("%"):] if "%" in trailing else ""
    new_line = lead + target + (" " + comment if comment else "")

    # 删掉 begin 与列定义行之间的旧注释列定义（保留其他注释/空行）
    kept: list[str] = []
    removed = 0
    for j in range(begin_idx + 1, spec_idx):
        if _looks_like_commented_spec(lines[j]):
            removed += 1
            continue
        kept.append(lines[j])
    if removed:
        lines[begin_idx + 1:spec_idx] = kept
        spec_idx -= removed
    lines[spec_idx] = new_line
    return "\n".join(lines)


def make_column_block(settings, shape: list[str], is_chinese: bool) -> list[str]:
    r"""生成本期栏目块：中文沿用上一期形态（含间距/主持人），只替换名称与主持人。

    英文表头**数据驱动**（B-226 P1 补充，用户 2026-10-25 复核口径）：
      · 填了 `columnNameEN` ⇒ 生成 `Column: <英文名>`（间距沿用模板里的 `\\[..]`，没有则 3mm）；
      · `columnNameEN` 为空 ⇒ **不生成英文表头行**（不拿中文名回退），
        条目仍按 `columnArticles` 带 `~~~~` 前缀。
    为什么不做逐期特例：人工 89 期英文写的是一行 `Special Issue: Preference Logic`（不是 `Column:`），
    这是同事的手工改法，进 P2「允许清单」逐条确认，不在代码里复刻（架构护栏 2：「禁止按期硬编码」）。
    """
    if not settings.is_column_enabled:
        return []

    if not is_chinese:
        name_en = (settings.columnNameEN or "").strip()
        if not name_en:
            return []
        spacing = next((ln for ln in shape if _SPACING_RE.match(ln)), None) if shape else None
        return ["\\large  \\bf{Column: " + name_en + "} ", spacing or "\\\\[3mm]"]

    # 中文：沿用上一期的形态（含间距），只替换名称与主持人
    title = settings.chinese_column_title
    if shape:
        out: list[str] = []
        for line in shape:
            new_line = line
            if "主持人" in new_line:
                new_line = re.sub(r"主持人[：:][^}]*", f"主持人：{settings.columnHost}", new_line)
                # 旧模板的 `&&` 把主持人放进页码列；新 canonical 三列表里主持人属于作者列（第 2 列），
                # 否则页码列被撑到 84pt、X 列只剩 151pt（B-355 实测）。
                new_line = re.sub(r"^(\s*)&&(\s*)\\textit", r"\1&\2\\textit", new_line)
            elif not _SPACING_RE.match(new_line):
                new_line = re.sub(r"(专栏|专辑)[：:][^}]*", title, new_line)
            out.append(new_line)
        return out

    # 上一期没有栏目块时的兜底形态（中文）
    return [
        "{\\large \\bf{" + title + "}}",
        "&\\textit{\\large 主持人：" + settings.columnHost + "}",
        "\\\\[3.2 ex]",
    ]
