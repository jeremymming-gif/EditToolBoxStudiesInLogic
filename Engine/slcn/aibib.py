#!/usr/bin/env python3
r"""把 AI 返回的 BibTeX 条目**并入** `reference.bib`（用户 2026-10-15 的决定）。

背景与决策
----------
本项目的旧口径是「**AI 结果永不进产物**」（README 第 41 条）：工具只生成
《文献核实任务书》，AI 的建议附在任务书里，由编辑自己判断后再手工改 bib。
2026-10-15 用户决定改掉这条：**AI 返回的条目要直接并进 `reference.bib`**，
这样 `--compile` 出来的 PDF 就已经反映了核实结果，编辑不必再手工搬运一遍。
代价是「机器直接改学术事实」，所以本模块的全部篇幅都花在**保障**上。

四条硬保障（违反任何一条 → **跳过该条、保留原字节、记下中文原因**）
------------------------------------------------------------------
  ① **key 必须与请求的 key 逐字符相同**：正文里的 `\parencite{key}` 靠它对应，
     AI 把 key 改名（哪怕只是多了个 a）会让全文引用悬空——**永不接受**；
  ② **回复里必须恰好一条可解析条目**（`slcn.bibparse.parse_one`）：回了一整份
     bib、或回了个半截条目，一律跳过（分不清哪条是哪条，也说明这条回复不可信）；
  ③ **绝不新增、绝不删除条目**：条目数与**键的集合（含顺序）**必须与原件完全一致
     —— `merge()` 最后拿解析结果**再核对一遍**（`verify_key_sets`），
     不一致就整份放弃、退回原件（宁可什么都不并，也不改动文件结构）；
  ④ **AI 没返回的条目逐字节不动**：替换是**按偏移就地拼接**的，只动被返回的那一条
     （连它上方的 `% ⚠ 待核实` / `% 类型已改` 注释一起替换，那两行按任务书第三节
     就是该条的一部分）。

另外一条**不是跳过理由**的：缺必备字段（`slcn.bibcheck.check()` 口径）**照样并入**，
但记成 warning，写进任务书的「合并记录」与回读校验报告里，由编辑补。

`%` 注释要**归到条目上方**（2026-10-15 用户实测报的 bug）
--------------------------------------------------------
任务书第三节第 6/7/8 条要求把说明写成 `% ⚠ 待核实：<字段>` / `% 类型已改：…` 注释。
实测（`2026年第5期(90)/01 Jingru_Kou` 的 `Dang2022`）AI 把那一行写在了**围栏外面、
条目后面**；旧实现只取围栏内的那一条条目，说明当场丢掉——合并后的条目没有「待核实」
注释，回读校验就把 4 个缺失的必填字段报成**问题**（有注释时只报**提示**），
编辑于是以为工具反复出问题。所以 `_extract()` 现在会把**紧邻条目**的整行注释
（围栏内、围栏外、条目上方、条目下方都算）收齐、去重，统一放到条目**上方**。
边界：只认「空行 / 代码围栏 / `%` 行」隔开的紧邻注释，遇到散文就停——任务书正文里
那些 `%` 例子不会被顺手吞掉；注释文本一字不改。

字段顺序按任务书第三节重排（同一轮修）
--------------------------------------
合并进来的条目以前保持 AI 给的字段顺序，回读校验会报「字段顺序与任务书第三节的固定
顺序不一致」。现在 `reorder_entry_fields()` 把字段按
`author → title → journal/booktitle/editor → publisher → address → edition → volume
→ number → pages → year → userf`（**唯一一份**定义在 `slcn/bibreport.py`）重排，
未知/额外字段留在末尾；搬的是**原文切片**，字段值逐字节不动；改动记进字段级 diff。

作者字段：并入前一律跑一遍 `bibnorm.normalize_author_field()`
------------------------------------------------------------
「没有任何 AI 会可靠地把英文作者倒装成 `Last, F.`」正是这条通道存在的理由
（第 63 条：真实成品 20/20 倒装，AI 会照抄源稿的「名 姓」）。所以 `author` / `editor`
的值在写入前**由引擎**规范化一遍（幂等：已倒装的不会被改动），改动记进字段级 diff。
中/日文姓名与判不准的英文名原样保留（判据与规范化器同源）。

`% 来源` 注释：搬进 bib 了，「合并记录」里也必须逐条记下（第 79 条，缺陷 A）
------------------------------------------------------------------------
`% 来源：<URL 或说明>` 是任务书第三节第 11 条要求的**审计线索**——它回答的是
「这批条目到底是联网查过的，还是模型凭记忆写的」。合并时它本来就被 `_entry_block()`
收进条目、写进了 `reference.bib`，但记录只登记 `待核实` / `类型已改`，于是真实稿
`Bo_Chen` 里新增的 25 行 `% 来源` 在「六、合并记录」中 **0 次提及**：产物里唯一的
联网凭据，在留档里看不见（用户 2026-10-15 实测，第 79 条）。
现在 `来源` 进 `_RECORDED_COMMENT_KEYS`，与既有注释**同一种形态**逐条记：
`- 说明：条目上方保留的 AI 注释：\`% 来源：…\``。
⚠️ 它**故意不进** `_ANNOTATION_KEYS`（那是**替换边界**表，不是记账表）——
理由写在 `_ANNOTATION_KEYS` 旁边：进去会把文件头注释一起吞掉。

「待核实说查不到、字段却被填上」要出警告（第 79 条，缺陷 B）
----------------------------------------------------------
本次合并为某条**新增/修改**了字段 X，而同一条上方的 `% ⚠ 待核实` 注释**点名**了 X
（真实案例 `ChenBo2022`：注释写「volume…暂缺」，字段却新增了 `volume = {15}`）——
编辑从产物里看不出这个自相矛盾（回读校验只把缺字段降档，不会指出这件事）。
`merge()` 现在把 `pending_review_contradictions()` 的结果放进 `MergeRecord.warnings`，
**只报不改**：字段、注释、四条硬保障、`n_merged` / `n_skipped` 口径一个字都不动。

两条通道共用本模块（不许有第二份合并实现）
------------------------------------------
  · **自动通道**（`convert.py` ⑪，界面 = 勾「用 AI 处理参考文献」）：编辑的 AI 就在本机
    直连，回复逐条收进 `replies`，`apply_ai_merge()` 调 `merge()` 落盘；
  · **手动通道**（`checkbib.py --merge-into`，界面 = 「文献回读校验」页的「并回」按钮）：
    编辑把 `.bib` 拿给自己的联网 AI 改完交回**一整份**，`merge_returned_bib()` 先把它
    拆成同一个 `replies` 形状，再走同一个 `merge()`——四条硬保障、作者倒装、字段重排、
    回滚副本一字不差地共用。两条通道若各写一份，「自动挡下了改名、手动却放过去」这种
    分叉迟早会出现，而编辑无从知道该信哪一份。

本模块是**纯离线**的：只 import 标准库 + `slcn.bibcheck` / `slcn.bibnorm` /
`slcn.bibparse`。它不调用任何模型——调用模型的活是 `convert.py` ⑪ 的。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field

from . import bibcheck, bibnorm
from .bibparse import (
    _FULL_COMMENT,
    _clip,
    Entry,
    parse_entries,
    parse_one,
    top_field_extents,
    top_field_spans,
)
# 任务书第三节的**固定字段顺序**只有一份实现（在报告模块里，回读校验也用它），
# 这里引用而不是另抄一份——两处一旦分叉就会出现「合并按 A 排、校验按 B 判」的死循环。
from .bibreport import SPEC_FIELD_ORDER as CANONICAL_FIELD_ORDER

# 条目上方这几类注释按任务书第三节就是**该条的一部分**，替换时要连它们一起换
# （否则重跑会叠出第二份——任务书里明写了这两种写法）。
#
# ⚠️ 这张表只管**替换边界**（进表 → 替换起点从「紧贴条目」上移到 `Entry.block_start`，
#    允许注释与条目之间夹空行），**不是**「合并记录里要记哪些注释」的表——
#    记账用下面那张 `_RECORDED_COMMENT_KEYS`。两张表分开是**故意的**（第 79 条）：
#    `_absorb_start()` 走这张表时用的是 `Entry.block_start`（bibparse 的
#    `_comment_block_start()`），它会**跨过注释之间的空行**一路往上走，而合并后的
#    **第一条**上面正好隔着空行压着文件头那两行注释。若把「每条都写」的 `% 来源`
#    并进这张表，第二次把同一份 bib 再并一遍时替换起点就被推到文件头（实测：
#    `reference.bib` 再并一遍后开头的 `% ── 由文末参考文献区生成 ──` 被切坏、
#    只剩下一个 `% ` 残片，结果也不再逐字节幂等）——而「文件头的注释留在原地」是
#    本模块对编辑的承诺（见 `_absorb_start()` 的第二种情形）。
_ANNOTATION_KEYS = ("待核实", "类型已改")

# 「合并记录」里要**逐行说明**的注释种类 = 上面那张替换表 + `来源`。
# `来源` 只记账、不动替换边界，这样也够安全：在文件里它本来就紧贴条目上方，
# `_absorb_start()` 的「紧贴的 `%` 行」分支已经会连它一起替换，重跑不会叠出第二份
# （`reference.bib` 实测：拿同一批 replies 再并一遍 → 逐字节相同）。
_RECORDED_COMMENT_KEYS = _ANNOTATION_KEYS + ("来源",)

# `author` / `editor` 这两类值由引擎负责倒装（见模块头）。
ROLE_FIELDS = ("author", "editor")

_SPACE = re.compile(r"\s+")
# 整行注释（行首可缩进）与代码围栏行——「紧邻条目的注释」只认这三种行（第三种是空行）。
_COMMENT_LINE = re.compile(r"^[ \t]*%")
_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})")


def _norm_ws(s: str) -> str:
    """只比「字段值有没有实质变化」用的归一化（空白折叠）。"""
    return _SPACE.sub(" ", str(s)).strip()


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class FieldDiff:
    """一条条目的**字段级**变化（相对原件）。"""

    added: list[str] = field(default_factory=list)      # 新增的字段名
    removed: list[str] = field(default_factory=list)    # 去掉/清空的字段名
    changed: list[str] = field(default_factory=list)    # `字段: 旧 → 新`
    etype_from: str = ""
    etype_to: str = ""
    # 字段顺序被按任务书第三节重排过时的**新顺序**（字段值没变也算一次改动，
    # 因为 PDF 里字段的排列确实不同了；为空 = 没重排）。
    reordered: list[str] = field(default_factory=list)
    # 本次合并**动过值**的字段名（= `added` + `changed` 的名字，顺序稳定）。
    # 给 `pending_review_contradictions()` 与「`% ⚠ 待核实` 点名」比对用：从
    # `changed` 那些 `字段: 旧 → 新` 字符串里回切字段名太脆，这里一次算好。
    # `removed` **不算**「动过」——注释说「查不到」而字段被删掉，不构成
    # 「注释说查不到、字段却填上了」这种矛盾；`reordered` 同理（值没变）。
    touched: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.reordered) and \
            self.etype_from.lower() == self.etype_to.lower()

    def summary(self) -> str:
        """一行中文摘要（写进任务书的「合并记录」）。"""
        bits: list[str] = []
        if self.etype_from and self.etype_to and \
                self.etype_from.lower() != self.etype_to.lower():
            bits.append(f"类型 @{self.etype_from} → @{self.etype_to}")
        if self.changed:
            bits.append("改 " + "；".join(f"`{c}`" for c in self.changed))
        if self.added:
            bits.append("新增 " + "、".join(f"`{a}`" for a in self.added))
        if self.removed:
            bits.append("去掉 " + "、".join(f"`{r}`" for r in self.removed))
        if self.reordered:
            bits.append("字段顺序按第三节重排为 " + " → ".join(f"`{n}`" for n in self.reordered))
        return "；".join(bits)


@dataclass
class MergeRecord:
    """一个键的合并结果（`status` = `merged` / `skipped`）。"""

    key: str
    status: str
    reason: str
    etype: str = ""
    diff: FieldDiff | None = None
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def merged(self) -> bool:
        return self.status == "merged"


@dataclass
class MergeResult:
    """合并结果：新 bib 文本 + 逐键记录。"""

    text: str
    records: list[MergeRecord]
    original: str = ""

    @property
    def n_merged(self) -> int:
        return sum(1 for r in self.records if r.merged)

    @property
    def n_skipped(self) -> int:
        return sum(1 for r in self.records if not r.merged)

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def by_key(self, key: str) -> MergeRecord | None:
        for r in self.records:
            if r.key == key:
                return r
        return None


# ── ① 从一条 AI 回复里取出那一条 bibtex 条目 ──────────────────────────────


def _fenced_blocks(text: str) -> list[tuple[str, str, int, int]]:
    """取出 markdown 里的 ``` 围栏块，返回 `[(info 串, 块内容, 围栏起点, 围栏终点)]`。

    自己扫行而不写一条大正则：围栏里的内容可能含 ``` 之外的任何东西（包括 `}`、
    空行、甚至反引号少于 3 个的行），正则很容易在这里多吞或少吞。
    多带回两个**偏移**是为了 GAP 1：AI 常把 `% ⚠ 待核实` 那行写在**围栏外面**
    （条目后面或前面），只有知道围栏边界才能把紧邻它的注释也收进来。
    """
    out: list[tuple[str, str, int, int]] = []
    lines = text.splitlines(keepends=True)
    offs: list[int] = []
    p = 0
    for ln in lines:
        offs.append(p)
        p += len(ln)
    i, n = 0, len(lines)
    while i < n:
        m = re.match(r"^\s*(`{3,}|~{3,})\s*([^\s`~]*)", lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(1), m.group(2)
        open_start = offs[i]
        body_start = offs[i] + len(lines[i])
        j = i + 1
        while j < n and not lines[j].strip().startswith(fence[0] * 3):
            j += 1
        close_end = (offs[j] + len(lines[j].rstrip("\n"))) if j < n else p
        body_end = offs[j] if j < n else p
        out.append((info, text[body_start:body_end], open_start, close_end))
        i = j + 1  # 跳过收尾围栏
    return out


def _comment_lines(text: str) -> list[str]:
    """取 `text` 里的整行注释（原样，含行首缩进；空行与别的行丢掉）。"""
    return [ln.rstrip() for ln in text.splitlines() if _COMMENT_LINE.match(ln)]


def _dedupe_lines(lines: list[str]) -> list[str]:
    """去重（按 `strip()` 后的文本比；保留第一次出现的原样书写）。"""
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        k = ln.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(ln)
    return out


def _comments_after(text: str, pos: int) -> list[str]:
    """`pos` **之后紧邻**的整行注释（允许中间夹空行与代码围栏行）。

    只认「空行 / 围栏行 / `%` 行」三种；一遇到别的（散文、markdown 标题、下一条条目）
    就停下。这正是「注释紧邻这条条目」的判据，也是**不吞掉任务书正文里那些 `%` 例子**
    的保证——它们总与条目隔着散文。
    """
    out: list[str] = []
    for ln in text[pos:].splitlines():
        s = ln.strip()
        if not s or _FENCE_LINE.match(ln):
            continue
        if _COMMENT_LINE.match(ln):
            out.append(ln.rstrip())
            continue
        break
    return out


def _comments_before(text: str, pos: int) -> list[str]:
    """`pos` **之前紧邻**的整行注释（判据同 `_comments_after`，返回原顺序）。"""
    out: list[str] = []
    for ln in reversed(text[:pos].splitlines()):
        s = ln.strip()
        if not s or _FENCE_LINE.match(ln):
            continue
        if _COMMENT_LINE.match(ln):
            out.append(ln.rstrip())
            continue
        break
    return list(reversed(out))


def _candidate_bib(reply: str) -> str:
    """从 AI 回复里挑出「最可能是 bibtex 的那段」。

    优先带 `bibtex` / `bib` / `biblatex` 语言标记的围栏块（任务书第三节要求的就是
    这个写法）；没有语言标记就看全部围栏块；连围栏都没有就把整段回复拿去解析
    （有的模型不写围栏）。

    ⚠️ 只选**一个**围栏块时，把围栏**外面**紧邻它的整行注释也拼进来（前后各一次），
    这样「AI 把 `% ⚠ 待核实` 写在围栏外」这种真实写法不会丢说明；多个块拼在一起时
    不做这件事（无法判断注释属于哪一块，宁可不要也不能张冠李戴）。
    """
    blocks = _fenced_blocks(reply)
    if blocks:
        bib = [b for b in blocks if b[0].lower().startswith(("bibtex", "bib", "biblatex"))]
        chosen = bib or list(blocks)
        if len(chosen) == 1:
            _info, body, open_start, close_end = chosen[0]
            parts: list[str] = []
            lead = _comments_before(reply, open_start)
            trail = _comments_after(reply, close_end)
            if lead:
                parts.append("\n".join(lead) + "\n")
            parts.append(body)
            if trail:
                if not body.endswith("\n"):
                    parts.append("\n")
                parts.append("\n".join(trail))
            return "".join(parts)
        return "\n\n".join(b[1] for b in chosen)
    return reply


def _entry_block(candidate: str) -> tuple[str, Entry | None, int, str]:
    """从候选文本里切出**恰好一条**条目（含其上方紧邻的注释）。

    返回 `(条目文本, 条目, 条目体内字段体在条目文本里的偏移, 中文原因)`。

    GAP 1（2026-10-15 用户实测）：条目**下方**紧邻的 `% ⚠ 待核实：…` 也要收，
    并统一挪到条目**上方**——回读校验只认「条目上方的注释」（`bibparse._comments_above`），
    留在下面等于没写。上下的同名注释按 `strip()` 去重；注释文本一字不改。
    """
    e, reason = parse_one(candidate)
    if e is None:
        return "", None, 0, reason
    start = _absorb_start(candidate, e)
    head = _comment_lines(candidate[start : e.start])
    tail = _comments_after(candidate, e.end + 1)
    entry = candidate[e.start : e.end + 1]
    comments = _dedupe_lines(head + tail)
    if comments:
        prefix = "\n".join(comments) + "\n"
        # 字段体在**返回文本**里的偏移：注释前缀长度 + 字段体在条目内的偏移。
        return prefix + entry, e, len(prefix) + (e.body_start - e.start), ""
    return entry, e, e.body_start - e.start, ""


def _absorb_start(text: str, e: Entry) -> int:
    """替换区的起点：**连条目上方那几行注释一起换**。

    两种情形分开处理（都为了「重跑一次不会叠出两份注释」）：

      · 上方注释里有 `待核实` / `类型已改`（任务书第三节第 5/8 条要求写在条目上方）
        → 从 `Entry.block_start`（允许中间夹空行，与 `_comments_above()` 同一判据）
        开始换——那几行按规范就是这条的一部分；
      · 否则只吃**紧贴着的**那几行 `%`（中间有空行就断开）→ 文件级的头注释
        （如 `% ── 由脚注 / 文末编号清单中的文献条目生成 ──`）留在原地，
        不会被一条 AI 回复顺手抹掉。
    """
    if any(k in c for c in e.comments for k in _ANNOTATION_KEYS):
        return e.block_start
    start = e.start
    for ln in reversed(text[: e.start].splitlines(keepends=True)):
        if _FULL_COMMENT.match(ln):
            start -= len(ln)
            continue
        break
    return start


def _extract(key: str, reply: str) -> tuple[str, Entry | None, int, str]:
    """三段安全校验都在这里（回复非空 → 恰好一条可解析条目 → key 逐字符相同）。

    第三个返回值是**字段体在返回文本里的偏移**（`_entry_block` 算的）——
    `normalize_role_fields()` 用它把 `author` / `editor` 的值就地换掉，
    **不能**再用「条目在 candidate 里的起点」（加了注释前缀之后那个偏移不再成立）。
    """
    if not (reply or "").strip():
        return "", None, 0, "AI 这条没有返回内容"
    text, e, body_off, reason = _entry_block(_candidate_bib(reply))
    if e is None:
        return "", None, 0, f"回复里没有可用条目：{reason}"
    if e.key != key:
        return "", None, 0, (
            f"AI 返回的键是 `{e.key}`，与请求的 `{key}` 不一致——"
            "改 key 会让正文 `\\parencite` 全部悬空，工具**永不接受**改名（任务书第三节第 1 条）"
        )
    return text, e, body_off, ""


def extract_reply_entry(key: str, reply: str) -> tuple[str | None, str]:
    """取 `reply` 里那一条条目（含注释）。返回 `(条目文本或 None, 中文原因)`。

    安全校验（任一不满足都算失败）：
      · 回复非空；
      · 剔除围栏后**恰好一条**可解析条目（`bibparse.parse_one`：结构完好、有 key）；
      · 条目的 key 与请求的 `key` **逐字符相同**。
    """
    text, e, _start, reason = _extract(key, reply)
    return (text if e is not None else None), reason


def parse_ai_reply(key: str, reply: str) -> str | None:
    """从一条 AI 回复里取出 bibtex 条目（含其上方注释）。取不出返回 None。

    这是给「只想看一眼 AI 回了什么」的调用方的**只读**入口；
    `merge()` 内部走 `_extract()`，因为记录里要写**中文原因**。
    """
    return extract_reply_entry(key, reply)[0]


# ── ② 作者 / 编者倒装：就地改值，别的一个字节都不碰 ────────────────────────


def normalize_role_fields(entry_text: str, e: Entry, body_off: int) -> tuple[str, list[str]]:
    """把这条条目的 `author` / `editor` 值倒装（幂等），返回 (新文本, 中文说明)。

    `entry_text` = `_entry_block()` 的返回值（可能带条目上方的注释行）；
    `body_off` = **字段体在 `entry_text` 里的偏移**（`_entry_block()` 直接给出，
    不能用 `e.body_start - e.start`——那等于假设条目从 `entry_text[0]` 开始，
    而 GAP 1 之后注释前缀会被搬到条目上方）。

    ⚠️ 这里**只改字段值本身**（花括号/引号内部那一段），条目里别的一切——类型、key、
    字段顺序、缩进、注释、`@string` 宏——一律逐字节保留。做法就是拿
    `bibparse.top_field_spans()` 给的值区间，从后往前拼接；**不做「重新序列化」**，
    那会把 AI 的排版和说明注释一起改写掉（字段顺序的重排是下一道工序，单独记账）。
    """
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    for name, s, en in top_field_spans(e.body):
        if name not in ROLE_FIELDS:
            continue
        vs, ve = body_off + s, body_off + en
        # ⚠️ `e.body` 是**整行注释被抹成空格**的副本（长度与换行不变），所以偏移对
        #    `entry_text` 照样成立；但要比对/替换的值必须取自 `entry_text`
        #    （否则注释内容会被当成空白读，替换也会把注释抹掉）。
        old = entry_text[vs:ve]
        new = bibnorm.normalize_author_field(old)
        if new != old:
            edits.append((vs, ve, new))
            notes.append(
                f"`{name}` 已按编辑部硬规则倒装：`{_clip(old, 40)}` → `{_clip(new, 40)}`"
                "（`bibnorm.normalize_author_field`，幂等）"
            )
    if not edits:
        return entry_text, notes
    out = entry_text
    for vs, ve, new in sorted(edits, reverse=True):
        out = out[:vs] + new + out[ve:]
    return out, notes


def reorder_entry_fields(entry_text: str, e: Entry) -> tuple[str, str, list[str]]:
    """按任务书第三节的**固定顺序**重排这条条目的字段。

    返回 `(新文本, 中文说明, 重排后的字段名顺序)`；没重排时后两项为空。

    为什么不能省掉这一步：合并进来的条目保持 AI 给的顺序，回读校验（`bibreport`）
    会报「字段顺序与任务书第三节的固定顺序不一致」——AI 每条回复的顺序都不一样，
    编辑要一条条手工挪。顺序是**排版规范**，交给工具做。

    未知/额外字段（`doi` 之类不在固定表里的）留在**末尾**，它们之间的相对顺序不变。
    搬的是**原文切片**（`bibparse.top_field_extents()` 给的 `name = {value}` 整段），
    所以字段值、内部空格、花括号/引号写法逐字节不动，改的只有先后与行首缩进；
    条目上方的 `%` 注释原样留在条目上方。
    """
    extents = top_field_extents(e.body)
    if len(extents) < 2:
        return entry_text, "", []
    body_off = e.body_start - e.start
    entry = entry_text[e.start : e.end + 1]
    items: list[tuple[str, str, int]] = []
    for idx, (name, fs, fe) in enumerate(extents):
        raw = entry[body_off + fs : body_off + fe].strip()
        if raw:
            items.append((name, raw, idx))
    if len(items) < 2:
        return entry_text, "", []
    rank = {n: i for i, n in enumerate(CANONICAL_FIELD_ORDER)}
    # 稳定排序：同组（rank 相同不可能，因为表里名字互不相同）与未知字段都保持原相对顺序。
    ordered = sorted(items, key=lambda t: (rank.get(t[0], len(CANONICAL_FIELD_ORDER)), t[2]))
    old_names = [t[0] for t in items]
    new_names = [t[0] for t in ordered]
    if old_names == new_names:
        return entry_text, "", []
    body = ",\n".join("  " + raw for _n, raw, _i in ordered)
    rebuilt = f"{entry_text[: e.start]}@{e.etype}{{{e.key},\n{body}\n}}{entry_text[e.end + 1:]}"
    note = ("字段顺序已按任务书第三节的固定顺序重排："
            + "、".join(f"`{n}`" for n in old_names) + " → "
            + "、".join(f"`{n}`" for n in new_names))
    return rebuilt, note, new_names


# ── ③ 字段级 diff + 缺字段 warning ────────────────────────────────────────


def diff_fields(old: Entry, new: Entry) -> FieldDiff:
    """`old` → `new` 的字段级 diff（新增 / 去掉 / 改动 / 类型变化 / 顺序重排）。

    `touched`（新增 + 改值的字段名）在这里就地攒，不去 `changed` 的
    `字段: 旧 → 新` 字符串里回切——字段名可能含 `-`、值里可能含 `:`，
    回切迟早会切错（第 79 条：这张表要喂给「待核实点名」的比对）。
    """
    d = FieldDiff(etype_from=old.etype, etype_to=new.etype)
    for name in new.fields:
        if name not in old.fields and new.fields[name].strip():
            d.added.append(name)
            d.touched.append(name)
    for name in old.fields:
        if name not in new.fields or (old.fields[name].strip() and not new.fields[name].strip()):
            d.removed.append(name)
    for name in old.fields:
        if name in new.fields and old.fields[name].strip() and new.fields[name].strip():
            if _norm_ws(old.fields[name]) != _norm_ws(new.fields[name]):
                d.changed.append(f"{name}: {_clip(old.fields[name], 50)} → {_clip(new.fields[name], 50)}")
                d.touched.append(name)
    return d


def missing_field_warnings(e: Entry) -> list[str]:
    """缺必备字段 → warning（**不是**跳过理由：照样并入，但要让编辑看见）。

    判据完全复用 `bibcheck.check()`（即 `REQUIRED` 表 + `TYPE_ALIASES`），不另写一套。
    """
    if e.broken:
        return []
    one = f"@{e.etype}{{{e.key},{e.body}}}"
    out: list[str] = []
    for iss in bibcheck.check(one).issues:
        if iss.severity == "must" and iss.kind in ("missing", "empty"):
            what = "缺" if iss.kind == "missing" else "空值"
            out.append(f"仍{what}必填字段 `{iss.detail}`（已按用户决定并入，请人工补）")
    return out


# `% ⚠ 待核实：<字段>（原因）` 里「点名区」的切法（第 79 条，缺陷 B）。
# 点名区 = `待核实` 之后、**第一个**分隔符之前的那一小段；分隔符是 `（` / `(`
# 与全/半角冒号。原因写在括号里，括号里出现英文词（如 `Oxford University Press`）
# 一律不算点名——这才是「窄」的意义。
_PENDING_HEAD = re.compile(r"待核实\s*[:：]?\s*([^（(：:\n]*)")
# 点名区里能当 bibtex 字段名的 token（字段名在 `bibparse` 里一律小写化）。
_PENDING_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def pending_review_fields(comments: list[str]) -> list[str]:
    """取出「`% ⚠ 待核实` 注释点名了哪些字段」（去重、保持出现顺序、一律小写）。

    「点名」口径与 `slcn/bibreport.py` 的 `pointed_fields()`（第 77 条引入，报告侧也在
    认同一批注释）**同源**——两边都是「`待核实` 之后、**第一个** `（`/`(` 之前的那一小段
    才算点名区；括号里的解释文字不算」（原因里会出现 `Oxford University Press`、
    `URL` 这类普通词，扫全句会凭空点出一个根本没提的字段）。四处**明确差异**（有意）：

      · 字段名表：`pointed_fields()` 只认 `bibcheck.REQUIRED` 的 must/nice 字段名；
        这里不设白名单——凡是点名区里的 ASCII 词都算候选，最后由调用方
        `pending_review_contradictions()` 与**这条条目真实拥有的字段名**取交集。
        于是 `% ⚠ 待核实：userf（…）` 这类「不在必备字段表里、但确实是本条字段」的点名
        照样会触发警告（`userf` 不在 `bibcheck.REQUIRED` 里）。
      · 顺序：这里返回**出现顺序**（谁先被点名谁在前），`pointed_fields()` 返回排序结果。
      · 点名区边界：这里**还**把全/半角冒号当分隔符（`待核实：a：b` 只认 `a`），
        `pointed_fields()` 只认括号。
      · token 形态：这里认 `[A-Za-z][A-Za-z0-9_-]*`（`_`/`-` 算词内），
        `pointed_fields()` 用 `(?<![A-Za-z0-9])名字(?![A-Za-z0-9])`。

    共同的取舍：**点名区之外的一律不认**（括号里的不认、扫不出来的不认）——不会凭空
    点出一行注释里根本没提的字段（漏报可以，误报不行；误报会让编辑把一个查得着的字段
    当成矛盾去核）。只认含「待核实」的整行注释；`类型已改` / `来源` 不参与（它们不表示
    「查不到」）。
    """
    out: list[str] = []
    for c in comments or ():
        if "待核实" not in c:
            continue
        m = _PENDING_HEAD.search(c)
        if not m:
            continue
        for tok in _PENDING_FIELD.findall(m.group(1)):
            tok = tok.lower()          # 字段名在 bibparse 里一律小写化（与报告侧 IGNORECASE 对齐）
            if tok not in out:
                out.append(tok)
    return out


def pending_review_contradictions(merged: Entry, diff: FieldDiff) -> list[str]:
    """「本次改了 X，而同一条上方的 `% ⚠ 待核实` 说 X 查不到」→ 逐字段一条警告。

    **只报不改**：不动字段、不动注释、不影响四条硬保障与 `n_merged` / `n_skipped`。

    判据（第 79 条，缺陷 B）：
      · `diff.touched`（本次**新增 / 改值**的字段；去掉与单纯重排不算——见 `FieldDiff`）
        里逐个字段名，去比对 `merged.comments` 里「待核实」点名的字段；
      · 只看**合并结果这条自己**的注释（`merged.comments`）——警告的措辞是「同一条
        **上方**的待核实注释说它查不到」，而注释只可能来自 AI 这条回复：原件上方的注释
        在替换时已被 AI 文本整体换掉（`_absorb_start()`）。所以「原件有注释、AI 没重复
        这行注释、却把字段填上了」这种情形**不出**这条警告（合并后的产物里并没有一行
        说它查不到的注释），这是**故意**的口径，不是漏报。
    """
    named = pending_review_fields(merged.comments)
    if not named:
        return []
    return [
        f"AI 本次改动了 `{name}`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。"
        for name in diff.touched if name in named
    ]


# ── ④ 合并 ────────────────────────────────────────────────────────────────


def verify_key_sets(original_bib: str, merged_bib: str) -> str:
    """核对「条目数与键的集合/顺序」是否逐项一致。一致返回空串，否则给中文原因。

    这是**最后一道闸**（第 ③ 条保障）：替换逻辑是纯偏移拼接，正常情况下不可能新增
    或删除条目；万一被将来的改动破坏，这里会让整份合并作废、退回原件——
    「少一条文献」比「少一处修正」严重得多。
    """
    a, _ = parse_entries(original_bib)
    b, _ = parse_entries(merged_bib)
    ka, kb = [e.key for e in a], [e.key for e in b]
    if len(a) != len(b) or ka != kb:
        return (f"合并后的条目/键与原件不一致（原件 {len(a)} 条 → 合并后 {len(b)} 条）——"
                "整份合并已作废、reference.bib 保持原样（第 ③ 条保障）")
    return ""


def merge(original_bib: str, replies: dict[str, str]) -> MergeResult:
    """把 `replies`（请求的 key → AI 回复原文）并进 `original_bib`。

    返回 `MergeResult`：`text` = 新的 bib 文本，`records` = 逐键记录
    （`merged` / `skipped` + 中文原因 + 字段级 diff）。
    **所有未返回的条目逐字节不动**；任何一条违反硬保障都只是「跳过那一条」。
    """
    entries, _findings = parse_entries(original_bib)
    n_by_key: dict[str, int] = {}
    for e in entries:
        n_by_key[e.key] = n_by_key.get(e.key, 0) + 1

    records: dict[str, MergeRecord] = {}
    edits: list[tuple[int, int, str]] = []

    for e in entries:
        key = e.key
        if key in records:            # 重复键：第一次已经记过原因，不重复刷屏
            continue
        if key not in replies:
            records[key] = MergeRecord(key, "skipped", "AI 未返回这一条（保持原样，逐字节未动）")
            continue
        if n_by_key.get(key, 0) > 1:
            records[key] = MergeRecord(
                key, "skipped",
                f"原件里这个键出现了 {n_by_key[key]} 次（BibTeX 只会认其中一条）——"
                "为免把同一条写进两个位置，整条跳过",
            )
            continue

        text, new_entry, body_off, reason = _extract(key, replies[key])
        if new_entry is None:
            records[key] = MergeRecord(key, "skipped", reason)
            continue
        if not new_entry.fields and e.fields:
            records[key] = MergeRecord(
                key, "skipped",
                "AI 返回的条目一个字段都没有，会把整条清空（原件有 "
                f"{len(e.fields)} 个字段）——已跳过",
            )
            continue

        # ⚠️ 两个偏移**不是一回事**，别混：
        #    · `body_off` 是 `_extract()` 在**AI 那段回复里**算的（字段体在 `text`
        #      里的偏移），只用于 `normalize_role_fields()` 定位 author / editor 值；
        #    · 拼回文件用的偏移必须在 **original_bib** 上重新算一遍。
        #    第一版把回复里的偏移直接用去拼接，结果把整份 bib 切坏了——
        #    `verify_key_sets()` 当场拦下（20 条 → 1 条）、原文件一个字节没动。
        abs_start = _absorb_start(original_bib, e)
        old_block = original_bib[abs_start : e.end + 1]
        new_text, norm_notes = normalize_role_fields(text, new_entry, body_off)
        after, why2 = parse_one(new_text)
        if after is None:
            records[key] = MergeRecord(key, "skipped", f"倒装作者后条目不再可解析：{why2}（已跳过）")
            continue
        # GAP 3：字段顺序按任务书第三节的固定顺序重排（未知字段留末尾）。
        new_text2, order_note, new_order = reorder_entry_fields(new_text, after)
        after2, why3 = parse_one(new_text2)
        if after2 is None:
            records[key] = MergeRecord(key, "skipped", f"按固定顺序重排字段后条目不再可解析：{why3}（已跳过）")
            continue
        new_text, after = new_text2, after2
        notes: list[str] = list(norm_notes)
        if order_note:
            notes.append(order_note)
        for c in after.comments:
            # ⚠️ 记账用 `_RECORDED_COMMENT_KEYS`（含 `来源`），**不是**替换边界表
            #    `_ANNOTATION_KEYS`——两张表为什么分开，见 `_ANNOTATION_KEYS` 旁的注释。
            if any(k in c for k in _RECORDED_COMMENT_KEYS):
                notes.append(f"条目上方保留的 AI 注释：`{_clip(c, 70)}`")
        diff = diff_fields(e, after)
        diff.reordered = new_order
        if diff.empty and old_block != new_text:
            # 字段值一字未变，但**文本**变了（排版 / 注释位置）：
            # 「字段与原件逐字段一致」这句话要说得准确，别让编辑以为文件没动。
            notes.append("本条的**文本**已按 AI 回复重写（字段值逐字段一致，只有排版/注释位置不同）")
        records[key] = MergeRecord(
            key, "merged",
            "已并入（通过全部安全校验）",
            etype=after.etype,
            diff=diff,
            notes=notes,
            # 矛盾警告排在缺字段警告**前面**：它说的是「这条自己前后打架」，比
            # 「还缺字段」更需要编辑先看见（第 79 条，缺陷 B）。
            warnings=pending_review_contradictions(after, diff) + missing_field_warnings(after),
        )
        edits.append((abs_start, e.end + 1, new_text))

    # AI 返回了原件里没有的键：工具**不新增条目**，只记一笔让人看见
    for key in replies:
        if key not in records:
            records[key] = MergeRecord(
                key, "skipped",
                "AI 返回的键在原件里不存在——工具**不新增条目**（第 ③ 条保障），已忽略",
            )

    out = original_bib
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]

    bad = verify_key_sets(original_bib, out)
    if bad:
        return MergeResult(
            text=original_bib,
            original=original_bib,
            records=[MergeRecord(r.key, "skipped", bad) for r in records.values()],
        )
    order = [e.key for e in entries] + [k for k in replies if k not in n_by_key]
    return MergeResult(
        text=out,
        original=original_bib,
        records=[records[k] for k in order if k in records],
    )


# ── ⑤ 任务书的「六、合并记录」 ─────────────────────────────────────────────


def render_merge_section(result: MergeResult, rollback_name: str = "reference.bib.ai前",
                         *, producer: str = "`convert.py`", provenance: str = "") -> str:
    """渲染任务书的 `## 六、合并记录（本次已并入 reference.bib）` 一节。

    为什么要有这一节：AI 的结果这次真的进产物了，编辑**必须**能一眼看出
    「哪几条被并进去了、改了什么、哪几条被工具挡下来了、为什么」——
    否则「工具自己改了 bib」就成了一件无法追责的事。

    `producer` 是**写入者**的名字（自动通道 = `convert.py`，手动并回 =
    `checkbib.py --merge-into`）：记录必须说清是谁改的文件，两种通道不能长得一样。

    `provenance`（默认空串）＝**本次核实通道**的一行说明（协议 + 是否联网检索 + 模型名，
    由 `convert.py` 拼好）。为什么写进「合并记录」而不只是打在控制台：控制台会随窗口关掉，
    而合并记录是**留档**的——编辑事后翻这份 markdown 时要能回答「这些条目是联网查过的，
    还是模型凭记忆写的」。空串时这一行**不出现**（旧调用逐字节不变）。

    第 79 条之后，每条并入的条目还会带上（都走既有的 `notes` / `warnings` 两个通道，
    渲染格式一字未改）：
      · `% 来源` 注释的逐条说明（缺陷 A：没这一行，「联网查过」与「凭记忆写的」在留档里
        长得一模一样）；
      · 「本次改了 X，而该条自己的 `% ⚠ 待核实` 说 X 查不到」的⚠️矛盾警告（缺陷 B，只报不改）。
    """
    L: list[str] = [
        ("## 六、合并记录（本次已并入 reference.bib）" if result.n_merged
         else "## 六、合并记录（本次未并入 reference.bib）"),
        "",
    ]
    L += [
        f"> 本节由 {producer} 在**并入之后**写入：逐键说明**并入了什么**、"
        "**跳过了什么、为什么**。四条硬保障（改 key / 回复不是恰好一条 / 增删条目 / "
        "改动未返回的条目）一律**跳过该条并保留原字节**；作者 / 编者字段在写入前"
        "由引擎按编辑部硬规则倒装（`slcn/bibnorm.py`，幂等）。",
        ">",
        "> ⚠️ **这是机器按 AI 回复直接改出来的 bib**：校对仍要人工过一遍。"
        f"回滚副本 `{rollback_name}` 是本次合并**之前**的那份。",
        "",
        f"- 本次：**并入 {result.n_merged} 条** / 跳过 {result.n_skipped} 条"
        f"（逐条见下；共 {len(result.records)} 个键）",
    ]
    if result.n_merged:
        L.append(f"- 回滚副本：`{rollback_name}`（下一次运行会覆盖它）")
    else:
        L.append("- 本次**没有任何条目并入**：`reference.bib` 与合并前**逐字节相同**（原因见下）")
    if provenance:
        # 通道说明放在「本次：…」之后、逐条记录之前：编辑先看到「这批是怎么核实的」，
        # 再往下才是逐条细节。用加粗点名，免得被当成一句普通附注略过。
        L.append(f"- **{provenance}**")
    L.append("")

    merged = [r for r in result.records if r.merged]
    skipped = [r for r in result.records if not r.merged]

    L.append(f"### 已并入（{len(merged)} 条）")
    L.append("")
    if not merged:
        L.append("（无）")
        L.append("")
    for r in merged:
        head = f"- `{r.key}`"
        if r.diff is not None and not r.diff.empty:
            head += f"：{r.diff.summary()}"
        else:
            head += "：字段与原件逐字段一致（AI 只是把这一条重写了一遍）"
        L.append(head)
        for n in r.notes:
            L.append(f"  - 说明：{n}")
        for w in r.warnings:
            L.append(f"  - ⚠️ {w}")
    if merged:
        L.append("")

    L.append(f"### 已跳过（{len(skipped)} 条）")
    L.append("")
    if not skipped:
        L.append("（无）")
        L.append("")
    for r in skipped:
        L.append(f"- `{r.key}`：{r.reason}")
    L.append("")
    return "\n".join(L)


# ── ⑥ 手动「并回」通道与自动通道**共用**的零件 ────────────────────────────
#
# 2026-10-15 加「文献回读校验」页的「并回」按钮（phase 2b）：编辑把 `.bib` 交给
# **自己的联网 AI** 改完交回一整份，工具把它并回产物的 `reference.bib`。
# 合并判据**必须**与自动通道同一份，所以下面这些零件都放在这里，`convert.py` 与
# `checkbib.py` 各取所需；谁都不许自己 copy+write、自己拼 replies。

#: 回滚副本后缀：`reference.bib.ai前` = 本次合并**之前**的那一份（重跑会覆盖）。
ROLLBACK_SUFFIX = ".ai前"

#: 转换时生成的《文献核实任务书》后缀（`convert.py` ⑪ 的命名：`<稿件名>_文献核实.md`）。
TASKBOOK_SUFFIX = "_文献核实.md"

#: 同级没有任务书时，合并记录落在 `<目标 bib 的 stem>_合并记录.md`。
RECORD_SUFFIX = "_合并记录.md"


def replies_from_bib(text: str) -> dict[str, str]:
    """把编辑交回的**整份** `.bib` 拆成 `merge()` 要的 `replies`（键 → 该条原文）。

    自动通道的 `replies` 是「请求的 key → AI 那一条聊天回复」；手动通道拿到的
    是一整份 bib，所以这里做同一种形状的转换：逐条取**原文切片**（含该条上方的
    `% ⚠ 待核实` 注释，判据与 `merge()` 替换时用的 `_absorb_start()` 同源），
    键就用条目自己的 key。于是 `merge()` 里的四条硬保障一字不差地复用：
    改名的键会落进「原件里不存在 → 不新增条目」那一类（**永不接受**），
    原件里没被返回的条目照旧逐字节不动。

    重复键：第一次出现的那条为准（`merge()` 本来就会对重复键的那一条报「整条跳过」）。
    """
    entries, _findings = parse_entries(text)
    out: dict[str, str] = {}
    for e in entries:
        if not e.key or e.key in out:
            continue
        start = _absorb_start(text, e)
        out[e.key] = text[start : e.end + 1]
    return out


def merge_returned_bib(original_bib: str, returned_bib: str) -> MergeResult:
    """手动通道的入口：把「编辑交回的整份 bib」并进原件。

    一句话封装 `replies_from_bib()` + `merge()`，让 `checkbib.py --merge-into`
    与 `convert.py` 的自动通道在**同一个函数**上汇合（没有第二份合并实现）。
    """
    return merge(original_bib, replies_from_bib(returned_bib))


def apply_merge_to_file(bib_path: str, result: MergeResult) -> str:
    """把合并结果落盘（**先备份、后覆盖**），返回回滚副本路径。

    自动通道（`convert.py: apply_ai_merge()`）与手动通道（`checkbib.py --merge-into`）
    共用这一处：`shutil.copy2` 先写 `<bib>.ai前`（已存在就覆盖——它代表「本次之前」，
    不是历史存档），再用合并文本覆盖目标。

    调用方**必须先确认 `result.n_merged > 0`**：0 条并入时一个文件都不许写
    （bib 没变却留下副本/记录，会让「什么都没发生」长得像发生过）。
    """
    rollback = bib_path + ROLLBACK_SUFFIX
    shutil.copy2(bib_path, rollback)
    with open(bib_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.text)
    return rollback


def merge_record_path(bib_path: str) -> tuple[str, str]:
    """决定「合并记录」写到哪儿，返回 `(路径, 中文说明)`。

    为什么不是简单的「同级 `<stem>_文献核实.md`」：任务书是按**稿件**命名的
    （`convert.py` ⑪ 写的是 `<稿件名>_文献核实.md`，如 `Jingru_Kou_文献核实.md`），
    而这里手上的路径是 `…/Jingru_Kou/reference.bib`，它的 stem 是 `reference`——
    `reference_文献核实.md` 基本不存在。所以顺序是：

      ① 同级 `<bib stem>_文献核实.md`（万一真叫这个名）；
      ② 同级**唯一**一份 `*_文献核实.md`（就是该篇的任务书）；
      ③ 都没有 / 有多份分不清 → 新建 `<bib stem>_合并记录.md`（与目标同级）。

    多份任务书时**故意不猜**：把合并记录塞进别人那篇的任务书里，比单独建一份更糟。
    """
    d = os.path.dirname(os.path.abspath(bib_path))
    stem = os.path.splitext(os.path.basename(bib_path))[0]
    same = os.path.join(d, stem + TASKBOOK_SUFFIX)
    if os.path.isfile(same):
        return same, f"已追加到同级任务书《{os.path.basename(same)}》"
    try:
        sibs = sorted(n for n in os.listdir(d) if n.endswith(TASKBOOK_SUFFIX))
    except OSError:
        sibs = []
    if len(sibs) == 1:
        return os.path.join(d, sibs[0]), f"已追加到同级任务书《{sibs[0]}》"
    return os.path.join(d, stem + RECORD_SUFFIX), (
        f"已新建《{stem}{RECORD_SUFFIX}》"
        + ("（同级有多份任务书，分不清属于哪一篇，故单独新建）" if len(sibs) > 1 else "（同级没有任务书）")
    )


def write_merge_record(bib_path: str, result: MergeResult, *, rollback_name: str = "",
                       producer: str = "`checkbib.py --merge-into`") -> tuple[str, str]:
    """写「合并记录」（内容 = `render_merge_section()`，与自动通道一字不差）。

    落点由 `merge_record_path()` 决定；文件不存在时补一行标题（新建的
    `_合并记录.md` 需要它，任务书里那节本来就有自己的标题层级）。
    **已存在就追加**（同一篇手动并回两次时两份记录都在，别覆盖掉上一次）。
    返回 `(路径, 中文说明)`。
    """
    path, note = merge_record_path(bib_path)
    if not rollback_name:
        rollback_name = os.path.basename(bib_path) + ROLLBACK_SUFFIX
    section = render_merge_section(result, rollback_name, producer=producer)
    existed = os.path.isfile(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if not existed:
            f.write(f"# 合并记录（{os.path.basename(bib_path)}）\n\n")
        f.write(section + "\n")
    return path, note
