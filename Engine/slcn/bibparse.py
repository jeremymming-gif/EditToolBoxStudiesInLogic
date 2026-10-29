r"""健壮的 .bib 解析器（**唯一一份**）：切条目、取顶层字段、报语法问题。

为什么单独一个模块（2026-10-15）
--------------------------------
`checkbib.py` 的解析器**不能**用 `slcn/bibcheck.py: split_entries()` / `fields_of()`：
那两个正则扫全文，对**坏文件**会静默误解析（值里的 `x = y` 被当成字段、坏条目把后面的
条目一起吞掉、假 key）——回读校验的全部价值就在「坏文件也要如实报出来」。

而 2026-10-15 起，**同一条解析结果有两个消费者**：

  · `checkbib.py`（编辑交回的 bib 的离线体检，`slcn/bibreport.py` 出报告）；
  · `slcn/aibib.py`（把 AI 返回的条目并回 `reference.bib` 前的安全校验：必须恰好
    一条、key 必须相同、花括号必须配平）——**并错一条就是改坏学术事实**。

第二份解析器（哪怕抄一遍）迟早会与第一份分叉，于是把它抽成这份**单一实现**：
`checkbib.py` 从前的 `parse_entries()` 从 2026-10-15 起就是本模块的函数（行为逐字节未变，
它的 86 项断言就是这次搬迁的保护网）。

硬性约束（与 `checkbib.py` 同一条纪律）
--------------------------------------
  · **只 import 标准库**：不联网、不调模型（本模块连 `slcn.bibcheck` 都不需要）；
  · **任何输入都返回结果，不抛异常**：坏文件也要出报告，不能让工具自己崩掉；
  · 注释只解释「为什么」，判据的文字与从前一字不差（报告里的发现文案由调用方拼）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVEL_PROBLEM = "problem"
LEVEL_INFO = "info"

_ENTRY_HEAD = re.compile(r"@\s*([A-Za-z][\w:+-]*)\s*([{(])")
# BibTeX 里本来就不带 key/逗号的结构性块（JabRef 会写 `@Comment{jabref-meta: …}`）：
# 不是文献条目，跳过不报，只汇总一句
NON_ENTRY_TYPES = ("comment", "preamble", "string")
_FULL_COMMENT = re.compile(r"^[ \t]*%")
_FIELD_HEAD = re.compile(r"[ \t\r\n]*([A-Za-z][\w:+-]*)[ \t\r\n]*=[ \t\r\n]*")


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class Finding:
    """一条发现。key 为空串表示「整份文件」层面的问题。"""

    key: str
    section: str
    level: str
    detail: str


@dataclass
class Entry:
    """一条条目：类型、键、字段体、字段（顶层解析）与上方注释。

    `start` / `end` / `body_start` / `block_start` 是**绝对偏移**（2026-10-15 加，给
    `slcn/aibib.py` 就地替换用）：`text[start:end+1]` 恰好是 `@类型{key, …}`，
    `text[body_start:end]` 是字段体，`text[block_start:start]` 是紧邻其上的整行注释
    （可能有空行，与 `_comments_above()` 同一判据）。
    偏移是在**抹过整行注释的副本**上算的，而那份副本长度与换行都和原文逐字符对齐
    （见 `_blank_comment_lines`），所以对原文照样成立。
    """

    etype: str
    key: str
    body: str
    line: int
    comments: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    broken: bool = False
    start: int = 0
    end: int = -1
    body_start: int = 0
    block_start: int = 0


def _clip(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[:n] + "…"


# ── 解析（总函数：任何输入都返回结果，不抛异常）────────────────────────────


def _blank_comment_lines(bib: str) -> str:
    """把**整行注释**（行首 `%`）就地抹成空格：长度与换行都不变，行号仍对得上。"""
    out = []
    for line in bib.splitlines(keepends=True):
        out.append(re.sub(r"[^\n]", " ", line) if _FULL_COMMENT.match(line) else line)
    # splitlines 会丢掉末尾无换行的空串，这里不需要补——长度校验：
    return "".join(out)


def _strip_inline_comments(bib: str) -> str:
    r"""把**行内 `%` 注释**（未转义的 `%` 到行尾）就地抹成空格：长度/换行不变。

    为什么需要（B-264）：真实稿件的 .bib 常把 `%` 当**行内批注**用，例如
    `@book{a19, %20,22,23`、`year = {2003},%1958`、`author = {…},%Seale, C.`。
    这些 `%` 之后是作者自己的备注，却**不在花括号字段值里面**；从前只抹整行注释，
    行内备注就被当成「字段体里有无法解析的片段」→ `broken=True`（实测私域 8 条）。

    判据（与 `parse_entries()` 的花括号/引号层同口径）：
      · **行首** `%`（整行注释）由 `_blank_comment_lines()` 先抹掉，这里不重复处理；
      · 只在**条目结构层**（花括号深度 ≤ 1、且不在顶层 `field = "…"` 引号值里）把
        `%` 到行尾视为注释——字段值 `{…}`/`"…"` 里的 `%` 是**数据**，一个字符都不动；
      · `\%` 是转义（按前导反斜杠个数的奇偶判断），不是注释。
    """
    out: list[str] = []
    depth = 0                      # 花括号深度（整份文件累计：条目跨行，不能逐行重置）
    quote = False                  # 顶层 `field = "…"`（深度 1 的引号值；花括号里的 `"` 是字符）
    for line in bib.splitlines(keepends=True):
        chars: list[str] = []
        i, n = 0, len(line)
        erased = False
        while i < n:
            ch = line[i]
            if erased:                       # 已在行内注释里：抹成空格（保留换行）
                chars.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            if quote:
                if ch == "\\" and i + 1 < n:
                    chars.append(line[i:i + 2])
                    i += 2
                    continue
                if ch == '"':
                    quote = False
                chars.append(ch)
                i += 1
                continue
            if ch == "\\" and i + 1 < n:     # `\%` 等转义：原样保留，不算注释
                chars.append(line[i:i + 2])
                i += 2
                continue
            if ch == "%":
                if depth <= 1:               # 结构层的 `%` → 注释，抹到行尾
                    erased = True
                    chars.append(" ")
                    i += 1
                    continue
                chars.append(ch)             # 字段值花括号里的 `%`：数据
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            elif ch == '"' and depth == 1:
                quote = True
            chars.append(ch)
            i += 1
        out.append("".join(chars))
    return "".join(out)


def _comments_above(bib: str, pos: int) -> list[str]:
    """取某条条目上方紧邻的注释行（允许中间夹空行）。"""
    out: list[str] = []
    for ln in reversed(bib[:pos].splitlines()):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("%"):
            out.append(s)
            continue
        break
    return list(reversed(out))


def _comment_block_start(bib: str, pos: int) -> int:
    """`pos` 上方那一串整行注释**最上面一行的行首偏移**（没有注释就返回 `pos`）。

    与 `_comments_above()` 同一套判据（只认整行 `%` 开头的行，允许中间夹空行），
    只是这里要的是**位置**：`slcn/aibib.py` 替换一条条目时，连它上方的
    `% ⚠ 待核实：…` / `% 类型已改：…` 一起替换（那两行按任务书第三节就是这条
    条目的一部分），因此必须知道它们从哪里开始。

    ⚠️ **起点不能吃掉注释块之前的那个空行**：`aibib.merge()` 会把 `[起点, 条目末]`
    整段换成 AI 的文本，若起点落在空行上，这一段的换行数就会随重跑而变
    （2026-10-15 实测：第一遍替换保留了分隔空行、第二遍把它吃掉 → 不幂等）。
    所以「注释之间的空行」跳过不扣，「注释块之前的空行」一律不进来。
    """
    start = pos
    seen_comment = False
    for ln in reversed(bib[:pos].splitlines(keepends=True)):
        s = ln.strip()
        if not s:
            if seen_comment:
                continue          # 注释之间的空行：它在替换区内部，位置由注释行决定
            break                 # 注释块之前的空行：不算这条的一部分
        if s.startswith("%"):
            seen_comment = True
            start -= len(ln)
            continue
        break
    return start


def _next_entry_start(text: str, pos: int) -> int:
    """找 pos 之后**独占行首**的下一个条目起点（坏条目脱困用）。找不到返回 -1。"""
    for m in _ENTRY_HEAD.finditer(text, pos):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start : m.start()].strip():
            continue
        if _in_line_comment(text, m.start()):
            continue
        return m.start()
    return -1


def _in_line_comment(text: str, pos: int) -> bool:
    r"""pos 是否落在同一行的**未转义** `%` 之后（行内注释里的 `@` 不算条目）。

    B-264：`\%` 是转义（按前导反斜杠个数奇偶判断），不算注释起点；否则
    `title = {50\% off}` 这类行会把后面合法的 `@` 误判进注释。
    """
    start = text.rfind("\n", 0, pos) + 1
    line = text[start:pos]
    k = line.find("%")
    while k >= 0:
        bs = 0
        q = k - 1
        while q >= 0 and line[q] == "\\":
            bs += 1
            q -= 1
        if bs % 2 == 0:
            return True
        k = line.find("%", k + 1)
    return False


def _scan_top_fields(
    body: str,
) -> tuple[list[tuple[str, str]], list[str], list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    r"""在**顶层**（不在字段值里面）解析 `name = {value}`。

    返回 `([(名, 值)], [结构问题], [(小写名, 值起点, 值终点)], [(小写名, 字段起点, 字段终点)])`：

      · 第三项是**值在 `body` 里的绝对偏移**（不含定界符），`slcn/aibib.py` 用它就地把
        `author` / `editor` 的值替换成倒装式；
      · 第四项是**整个字段**（字段名首字符 → 值定界符结束）的偏移，`slcn/aibib.py` 用它
        按任务书第三节的固定顺序**重排**字段（搬的是原文切片，所以字段值一个字节都不动）。

    为什么不用 `bibcheck.fields_of`：它的正则扫全文，值里的 `x = y`（如 `title = {… = …}`）
    会被当成一个字段，做 diff 时会冒出根本不存在的「字段变化」。这里只在顶层取值。
    值层面的**清洗规则**仍然全部调 `bibnorm._clean_field_value`，不另写一套。
    """
    pairs: list[tuple[str, str]] = []
    problems: list[str] = []
    spans: list[tuple[str, int, int]] = []
    extents: list[tuple[str, int, int]] = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c in " \t\r\n,":
            i += 1
            continue
        m = _FIELD_HEAD.match(body, i)
        if not m:
            j = body.find(",", i)
            frag = body[i : j if j >= 0 else min(n, i + 30)].strip()
            # 真实语料里见过 `1 = {https://doi.org/…}` 这种把 url 误替换成数字的写法
            bad = re.match(r"([^\s=]{1,20})\s*=", body[i:])
            if bad and not bad.group(1)[:1].isalpha():
                problems.append(
                    f"字段名不合法：`{_clip(frag, 24)}`——BibTeX 字段名必须以字母开头"
                    "（常见于把 `url` 之类误替换成了数字）"
                )
            else:
                problems.append(f"字段体里有无法解析的片段：`{_clip(frag, 30)}`")
            i = j + 1 if j >= 0 else n
            continue
        name = m.group(1)
        fs = i                      # 字段名首字符（整个字段的起点，见 extents）
        j = m.end()
        if j >= n:
            problems.append(f"字段 `{name}` 后面没有值")
            break
        if body[j] == "{":
            k, depth = j, 0
            while k < n:
                if body[k] == "{":
                    depth += 1
                elif body[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if k >= n:
                problems.append(f"字段 `{name}` 的花括号没有闭合")
                pairs.append((name.lower(), body[j + 1 :]))
                spans.append((name.lower(), j + 1, n))
                extents.append((name.lower(), fs, n))
                break
            value, end = body[j + 1 : k], k + 1
            spans.append((name.lower(), j + 1, k))
            extents.append((name.lower(), fs, k + 1))
        elif body[j] == '"':
            k = j + 1
            while k < n and body[k] != '"':
                k += 2 if body[k] == "\\" else 1
            if k >= n:
                problems.append(f"字段 `{name}` 的引号没有闭合")
                pairs.append((name.lower(), body[j + 1 :]))
                spans.append((name.lower(), j + 1, n))
                extents.append((name.lower(), fs, n))
                break
            value, end = body[j + 1 : k], k + 1
            spans.append((name.lower(), j + 1, k))
            extents.append((name.lower(), fs, k + 1))
        else:  # 裸值（数字 / 宏），BibTeX 允许
            k = j
            while k < n and body[k] not in ",\n":
                k += 1
            value, end = body[j:k].strip(), k
            spans.append((name.lower(), j, k))
            extents.append((name.lower(), fs, k))
        pairs.append((name.lower(), value))
        k2 = end
        while k2 < n and body[k2] in " \t\r\n":
            k2 += 1
        if k2 < n and body[k2] != ",":
            problems.append(
                f"字段 `{name}` 后面缺少逗号（后面紧跟 `{_clip(body[k2 : k2 + 16], 16)}`）"
            )
            i = k2
            continue
        i = k2 + 1 if k2 < n else n
    return pairs, problems, spans, extents


def _top_fields(body: str) -> tuple[list[tuple[str, str]], list[str]]:
    """`_scan_top_fields()` 的两元组版本（从 `checkbib.py` 搬来的原签名，行为未变）。"""
    pairs, problems, _spans, _extents = _scan_top_fields(body)
    return pairs, problems


def top_field_spans(body: str) -> list[tuple[str, int, int]]:
    """顶层字段的 `(小写字段名, 值起点, 值终点)`（偏移相对 `body`）。

    ⚠️ 起点/终点是**值本身**的边界：`name = {…}` 与 `name = "…"` 给的是花括号/引号
    **内部**的范围（所以就地替换不会动到定界符）；裸值给的是整段。
    """
    return _scan_top_fields(body)[2]


def top_field_extents(body: str) -> list[tuple[str, int, int]]:
    """顶层字段的 `(小写字段名, 字段起点, 字段终点)`（偏移相对 `body`）。

    起点 = 字段名的第一个字符，终点 = 值定界符之后——即 `body[起点:终点]` 恰好是
    `name = {value}` 这一段（不含分隔逗号）。`slcn/aibib.py` 靠它**重排字段顺序**：
    搬的是这段原文切片，所以字段值、内部空格、花括号/引号写法都逐字节保留。
    """
    return _scan_top_fields(body)[3]


def parse_entries(bib: str) -> tuple[list[Entry], list[Finding]]:
    """把 bib 拆成 Entry 列表 + 语法层面的发现。**任何输入都不抛异常。**"""
    entries: list[Entry] = []
    findings: list[Finding] = []
    skipped_blocks: list[tuple[str, int]] = []
    clean = _strip_inline_comments(_blank_comment_lines(bib))
    n = len(clean)
    i = 0
    while i < n:
        p = clean.find("@", i)
        if p < 0:
            break
        if _in_line_comment(bib, p):
            i = p + 1
            continue
        line = clean.count("\n", 0, p) + 1
        m = _ENTRY_HEAD.match(clean, p)
        if not m:
            frag = _clip(clean[p : p + 40].replace("\n", " "), 40)
            findings.append(
                Finding(
                    "",
                    "syntax",
                    LEVEL_PROBLEM,
                    f"第 {line} 行有一个 `@`，后面不是合法条目头（应为 `@类型{{key,`）：`{frag}`",
                )
            )
            i = p + 1
            continue

        etype, opener = m.group(1), m.group(2)
        open_pos = m.end() - 1
        close = "}" if opener == "{" else ")"
        if opener == "(":
            findings.append(
                Finding(
                    "",
                    "syntax",
                    LEVEL_PROBLEM,
                    f"第 {line} 行 `@{etype}(…)` 用圆括号界定：现有解析器（`bibcheck.split_entries`）"
                    "只认花括号，这一条会逃过所有核对，请改成 `@类型{key, …}`",
                )
            )

        depth, quote, j = 1, False, open_pos + 1
        first_comma, end = -1, -1
        while j < n:
            c = clean[j]
            if quote:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    quote = False
            elif depth == 1 and c == '"':
                # 只有顶层 `field = "…"` 的引号才是定界符；
                # 花括号值里的 `"`（如 `{F}itch's "A Definition of Value"`、`{\"u}`）
                # 是普通字符——早先不分层地跟踪引号，会把整个文件吞掉一条。
                quote = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            elif c == close and depth == 1:
                end = j
                break
            elif c == "," and depth == 1 and first_comma < 0:
                first_comma = j
            j += 1

        broken = False
        # ── 结构性块（@Comment/@string/@preamble）不是文献条目：跳过、只汇总 ──
        if etype.lower() in NON_ENTRY_TYPES and end >= 0:
            skipped_blocks.append((etype, line))
            i = end + 1
            continue
        if end < 0:
            broken = True
            nxt = _next_entry_start(clean, open_pos + 1)
            tail = (
                f"；已跳到第 {clean.count(chr(10), 0, nxt) + 1} 行的下一条继续核对"
                if nxt > 0
                else "（这条之后没有别的条目了）"
            )
            findings.append(
                Finding(
                    "",
                    "syntax",
                    LEVEL_PROBLEM,
                    f"第 {line} 行 `@{etype}` 起的花括号不配平：没有闭合的 `{close}`，"
                    f"BibTeX 会把后面的条目一起吞掉{tail}",
                )
            )
            end = nxt - 1 if nxt > 0 else n
        if first_comma < 0:
            broken = True
            bad_key = _clip(clean[open_pos + 1 : min(end, open_pos + 40)].strip(), 30)
            findings.append(
                Finding(
                    bad_key,
                    "syntax",
                    LEVEL_PROBLEM,
                    f"第 {line} 行 `@{etype}` 的 key 后面没有逗号，字段无法解析：`@{etype}{{{bad_key}…`",
                )
            )
            key = bad_key
            body = ""
        else:
            key = clean[open_pos + 1 : first_comma].strip()
            body = clean[first_comma + 1 : end]
            if not key:
                broken = True
                findings.append(
                    Finding(
                        "",
                        "syntax",
                        LEVEL_PROBLEM,
                        f"第 {line} 行 `@{etype}` 没有 key（`@{etype}{{,`）",
                    )
                )

        pairs, problems = _top_fields(body)
        for pr in problems:
            findings.append(Finding(key, "syntax", LEVEL_PROBLEM, f"第 {line} 行：{pr}"))
            broken = True

        fields: dict[str, str] = {}
        dupes: list[str] = []
        for fname, fval in pairs:
            if fname in fields:
                if fname not in dupes:
                    dupes.append(fname)
                continue
            fields[fname] = fval
        if not body.strip() and not broken:
            findings.append(
                Finding(key, "syntax", LEVEL_INFO, f"第 {line} 行 `{key}` 没有任何字段")
            )

        entries.append(
            Entry(
                etype=etype,
                key=key,
                body=body,
                line=line,
                comments=_comments_above(bib, p),
                fields=fields,
                broken=broken,
                start=p,
                end=end,
                body_start=(first_comma + 1 if first_comma >= 0 else p),
                block_start=_comment_block_start(bib, p),
            )
        )
        if dupes:
            findings.append(
                Finding(
                    key,
                    "syntax",
                    LEVEL_INFO,
                    f"字段重复出现：{'、'.join(f'`{d}`' for d in dupes)}（只取第一个值）",
                )
            )
        i = end + 1 if end > p else p + 1

    # 重复的引用键
    seen: dict[str, list[int]] = {}
    for e in entries:
        seen.setdefault(e.key, []).append(e.line)
    for k, lines in seen.items():
        if k and len(lines) > 1:
            findings.append(
                Finding(
                    k,
                    "syntax",
                    LEVEL_PROBLEM,
                    f"引用键重复 {len(lines)} 次（第 {'、'.join(str(x) for x in lines)} 行）："
                    "BibTeX 只会认其中一条，正文引用指向哪条不确定",
                )
            )

    if skipped_blocks:
        kinds = "、".join(sorted({f"@{t}" for t, _l in skipped_blocks}))
        findings.append(
            Finding(
                "",
                "syntax",
                LEVEL_INFO,
                f"文件里有 {len(skipped_blocks)} 个非文献块（{kinds}，如 JabRef 的 "
                "`@Comment{jabref-meta: …}`）：按结构跳过，不参与字段与引用核对",
            )
        )

    if not entries:
        if not bib.strip():
            findings.append(Finding("", "syntax", LEVEL_PROBLEM, "退回的文件是空的（0 字节 / 只有空白）"))
        else:
            findings.append(
                Finding(
                    "",
                    "syntax",
                    LEVEL_PROBLEM,
                    "整个文件里没有解析到任何条目（应该形如 `@article{key,` … `}`）",
                )
            )
    return entries, findings


# ── 单条解析：给「把 AI 返回的条目并回 bib」做安全校验（2026-10-15）──────────
#
# 为什么单开一个入口、而不让调用方自己数 `parse_entries()` 的长度：
# 合并的判据是「**恰好一条**、结构完好、key 对得上」，三条缺一不可，而调用方
# （`slcn/aibib.py`）需要的是**中文原因**——那份原因要写进任务书的「合并记录」
# 给人看。把判据与文案放在解析器旁边，就不会出现「两个调用方各判一次、判得不一样」。


def parse_one(text: str) -> tuple[Entry | None, str]:
    """解析**恰好一条**结构完好的条目。失败返回 `(None, 中文原因)`，绝不抛异常。

    判据（任何一条不满足都算失败）：
      ① 恰好解析出 1 条（0 条 = 没回条目；≥2 条 = 回了一整份 bib，分不清哪条是哪条）；
      ② 结构完好（`Entry.broken` 为假：花括号/引号配平、字段体可解析、有 key）；
      ③ 没有任何 problem 级语法发现（孤立的 `@`、重复 key 之类）。
    """
    entries, findings = parse_entries(text)
    if not entries:
        return None, "没有解析到 BibTeX 条目（应该形如 `@article{key, …}`）"
    if len(entries) > 1:
        keys = "、".join(f"`{e.key or '（无键）'}`" for e in entries[:5])
        more = f" 等 {len(entries)} 条" if len(entries) > 5 else ""
        return None, f"解析出 {len(entries)} 条条目（要求恰好 1 条）：{keys}{more}"
    e = entries[0]
    if e.broken:
        return None, "条目结构损坏（花括号/引号不配平，或字段体无法解析）"
    if not e.key:
        return None, "条目没有 key（`@类型{,`）"
    probs = [f for f in findings if f.level == LEVEL_PROBLEM]
    if probs:
        return None, f"条目文本还有 {len(probs)} 处语法问题（例：{probs[0].detail}）"
    return e, ""
