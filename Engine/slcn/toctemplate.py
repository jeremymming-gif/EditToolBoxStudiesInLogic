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
            elif not _SPACING_RE.match(new_line):
                new_line = re.sub(r"(专栏|专辑)[：:][^}]*", title, new_line)
            out.append(new_line)
        return out

    # 上一期没有栏目块时的兜底形态（中文）
    return [
        "{\\large \\bf{" + title + "}}",
        "&&\\textit{\\large 主持人：" + settings.columnHost + "}",
        "\\\\[3.2 ex]",
    ]
