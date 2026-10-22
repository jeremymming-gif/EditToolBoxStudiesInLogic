#!/usr/bin/env python3
"""rename_plan.py —— CJK 译名 bib key 改名的「只读计划器 + 新产物副本应用」（B-144 / 维护模式）

一句话：读一张**映射表**（CSV），只读扫描目标产物目录（`.tex` + `reference.bib`），输出
「旧键 → 新键 → 命中位置/次数」+ case 级 diff 预览 + 未命中/冲突/悬空清单；**dry-run 不写
任何文件**。`--apply --out <dir>` 才写，且只写显式输出目录里的**新产物副本**（不就地改、
不动历史产物、不写 `bib_db`、不 commit）。

为什么要有它
------------
CJK 译名条目的 bib 键由 `Engine/slcn/docxsource.py::make_key()` 生成（中文姓拼音 → `Ai1979`
这类键），而人工成品用的是规范英文键（`Einstein1979`）。B-144 的已批口径：**A 档 9 组先行、
只对新生成产物生效、不重建 27 期历史、不写 `bib_db`、未命中项零变化**。改名属于「引用语义
变更」，直接改引擎会让全部产物一起动、无法逐篇审；本工具把改名限制在「产物边界」上：先
dry-run 出逐处影响面给人审，再对显式指定的产物副本应用，并逐字节验证「只动了键字符串」。

映射表 schema（CSV，表头**必须**是这 12 列，顺序不限；多列忽略）
--------------------------------------------------------------
`old_key,new_key,work_sha,case,issue,evidence_type,evidence_ref,tier,status,product_cite_occ,
product_bib_count,db_collision`

- `old_key` / `new_key`：旧键（软件生成）/ 新键（人工成品规范键），必须不同、非空。
- `work_sha`：作品身份锚 = 人工成品（证据）条目的 `content_sha256`（与 notes
  `bib_db/bibliography.jsonl` 的 `content_sha256` 同一算法/同一口径），64 位小写十六进制。
  用途：防止映射表被人把「旧键/新键」对错行（错配）；`--db` 时可与库记录核对。
- `case` / `issue`：证据来源案例（如 `90-07`）/ 期号（`90`），只作出处标注，不作适用域。
- `evidence_type`：`source_latin` / `human_product` / `source_spelling` / `manual_confirmed`。
- `evidence_ref`：证据位置（相对稿件仓路径 + `#人工键`）；只作审计线索，工具不读取。
- `tier`：`A` / `B` / `C`（B-144 首批只批 A 档；`--tier A` 可显式收窄）。
- `status`：`approved` 才生效；`proposed` / `rejected` 跳过并在报告里计数。
- `product_cite_occ` / `product_bib_count`：建立映射表时在 41 篇 mid 快照上的实测命中
  （引用次数 / bib 条目数），只作基线数字，工具不信任、不用于判据。
- `db_collision`：建立映射表时的只读库碰撞结论（`db_same_key_same_work` /
  `db_alias_content_differs` / `db_key_case_variant_same_work` / `db_same_key_content_differs`
  / `none`），只作审计标注；R4 红线：**本卡不写 `bib_db`**。

用法
----
  # 1) 只读 dry-run：扫描目标产物目录，输出影响面（不动文件）
  python3 tools/rename_plan.py --rename-plan map.csv --dry-run --target /tmp/products

  #    机读（JSON 到 stdout；确定性：同一状态两次运行逐字节相同）
  python3 tools/rename_plan.py --rename-plan map.csv --dry-run --target DIR --json

  #    只收窄 A 档；无映射表（或只有表头）时零变化
  python3 tools/rename_plan.py --rename-plan map.csv --dry-run --target DIR --tier A

  # 2) 对新产物副本应用（唯一写出口；拒绝就地改 / 拒绝 --out 落在 target 内）
  python3 tools/rename_plan.py --rename-plan map.csv --apply --target DIR --out /tmp/renamed

  # 3) 回滚映射（new→old），配合 --apply 可把副本改回原样
  python3 tools/rename_plan.py --rename-plan map.csv --emit-reverse --out-map rev.csv

  # 4) hermetic 自检（tempfile 假产物 + 假库；不读真实产物/库、不联网、不启子进程）
  python3 tools/rename_plan.py --self-test

判据（写死）
------------
dry-run 的「命中」= 映射行旧键在目标里出现（`.tex` 引用参数，或 `reference.bib` 条目键）。
冲突（阻断）= 映射结构问题（重复旧键/重复新键/链式映射/坏 sha/坏 tier/坏 status）+ case 级问题
（新键已存在于 bib、旧键被引用但 bib 无条目、旧键出现在引用参数/条目键之外、bib 条目键重复）。
悬空 = `.tex` 里被引用但 bib 无对应条目的键（改名前后集合必须一致，扫不到 `*`）。
`--apply` 逐文件断言：命令数 / 键出现数 / bib 条目数不变；编辑只落在键 token 上；把编辑
逆向恢复后与原文**逐字节相同**（这就是「变化仅为 old↔new 键字符串」的可执行判据）。

退出码（写死）
--------------
- `0` dry-run 分析完成且无阻断冲突；`--apply` 成功；`--emit-reverse` 成功；`--self-test` 全绿。
- `1` 有阻断冲突（dry-run 检出 / `--apply` 拒绝写入）；映射表结构问题；自检失败。
- `2` 用法/IO/解析错误（读不到目标/映射表/输出路径非法）。

只读 / 离线 / 确定性 / 纯 stdlib / 单文件（本仓工具 DoD）
--------------------------------------------------------
- dry-run 零写入（自检里用目录 sha 快照自证）；`--apply` 只写 `--out`；`--emit-reverse` 只写
  指定文件。不联网、不启子进程、不 import 引擎、不写 `bib_db`、不 commit。
- 报告不含时间戳/耗时/绝对路径（target 只取 basename），同一输入两次运行逐字节相同。
- 自检 hermetic：`tempfile.TemporaryDirectory()` 里造假 `.tex` / `reference.bib` / 映射表 /
  `bibliography.jsonl`，不读真实数据。
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from dataclasses import dataclass, field

VERSION = "1.0.0"

#: `.tex` 里认作「引用命令」的命令名（单数 + 复数 + 常见变体；与 b144-impact §2 口径一致并
#: 补上复数形态，避免 `\cites{a,b}` 这类漏计）。
CITE_COMMANDS = frozenset({
    "parencite", "cite", "citet", "citep", "citeauthor", "citeyearpar", "textcite", "nocite",
    "parencites", "cites", "citets", "citeps", "textcites", "nocites",
    "autocite", "footcite", "smartcite",
})
#: `\cmd*?[opt][opt]{keys}`：opt 最多两个（biblatex 的 prenote/postnote）。
CITE_RE = re.compile(r"\\([A-Za-z]+)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}")
#: 映射表必含列（顺序不限）。
MAP_FIELDS = (
    "old_key", "new_key", "work_sha", "case", "issue", "evidence_type", "evidence_ref",
    "tier", "status", "product_cite_occ", "product_bib_count", "db_collision",
)
EVIDENCE_TYPES = frozenset({"source_latin", "human_product", "source_spelling", "manual_confirmed"})
TIERS = frozenset({"A", "B", "C"})
STATUSES = frozenset({"proposed", "approved", "rejected"})
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
#: BibTeX 键：非空白、不含 `{}`/`,`/`#`/`%`/`~`（与 biblatex 实践一致的保守集）。
KEY_RE = re.compile(r"^[^\s{}\\,#%~]+$")
#: 键身份的「独立出现」边界字符（用于抓引用参数/条目键之外的旧键残留）。
KEY_BOUNDARY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:.-")
#: 扫描时跳过的目录名（源稿原样保留区、垃圾缓存、版本库元数据）。
SKIP_DIRS = frozenset({"original", ".git", "__pycache__", ".svn", ".hg"})
#: 不参与改名的文件（备份/临时件）。
SKIP_FILE_SUFFIXES = (".bak", ".orig", ".rej", ".tmp")
#: `.bib` 文件名：产物约定 `reference.bib`；其它 `*.bib` 也扫（只读）。
BIB_NAME_RE = re.compile(r"(?:^reference\.bib$|\.bib$)", re.I)


class ToolError(Exception):
    """用法/输入错误（退出码 2）。"""


class ConflictError(ToolError):
    """有阻断冲突、拒绝写入（退出码 1）。"""


# ── 小工具 ──────────────────────────────────────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _read_text(path: str) -> str:
    """UTF-8 读文本；坏字节替换（只读扫描，不改写）。"""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    return max(1, bisect.bisect_right(starts, offset))


def _standalone_positions(text: str, key: str) -> list[int]:
    """键在文本中「独立出现」的偏移（前后都不是键字符；用于抓键之外的残留）。"""
    out = []
    start = 0
    klen = len(key)
    while True:
        i = text.find(key, start)
        if i < 0:
            break
        start = i + 1
        before = text[i - 1] if i > 0 else ""
        after = text[i + klen] if i + klen < len(text) else ""
        if before and before in KEY_BOUNDARY_CHARS:
            continue
        if after and after in KEY_BOUNDARY_CHARS:
            continue
        out.append(i)
    return out


def _excerpt(line: str, offset_in_line: int, token_len: int, window: int = 48) -> str:
    """以命中 token 为中心截一段人类可读的上下文（含 token 本身）。"""
    s = max(0, offset_in_line - window)
    e = min(len(line), offset_in_line + token_len + window)
    body = line[s:e]
    lead = "…" if s > 0 else ""
    tail = "…" if e < len(line) else ""
    return f"{lead}{body}{tail}"


def _norm_rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


# ── 映射表 ──────────────────────────────────────────────────────────────────


@dataclass
class MapRow:
    old_key: str
    new_key: str
    work_sha: str
    case: str
    issue: str
    evidence_type: str
    evidence_ref: str
    tier: str
    status: str
    product_cite_occ: int
    product_bib_count: int
    db_collision: str
    line: int = 0
    selected: bool = False
    db_check: dict = field(default_factory=dict)


@dataclass
class MapData:
    path: str
    rows: list[MapRow] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    header_problems: list[dict] = field(default_factory=list)

    def selected(self) -> list[MapRow]:
        return [r for r in self.rows if r.selected]

    @property
    def approved(self) -> list[MapRow]:
        return [r for r in self.rows if r.status == "approved"]


def _problem(code: str, detail: str, line: int = 0) -> dict:
    return {"code": code, "detail": detail, "line": line}


def _parse_int(value: str, name: str, line: int, problems: list[dict]) -> int:
    value = (value or "").strip()
    if value == "":
        return 0
    try:
        n = int(value)
    except ValueError:
        problems.append(_problem("bad_product_count", f"{name} 不是整数：{value!r}", line))
        return 0
    if n < 0:
        problems.append(_problem("bad_product_count", f"{name} 为负：{n}", line))
        return 0
    return n


def load_map(path: str, tiers: list[str] | None = None) -> MapData:
    """读映射表并做结构校验（不抛结构错误：写进 problems，由调用方决定退出码）。"""
    if not os.path.isfile(path):
        raise ToolError(f"映射表不存在：{path}")
    md = MapData(path=os.path.basename(path))
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            missing = [c for c in MAP_FIELDS if c not in header]
            if missing:
                md.header_problems.append(_problem(
                    "missing_columns", f"映射表缺列：{','.join(missing)}", 1))
                return md
            seen_old: dict[str, int] = {}
            seen_new: dict[str, int] = {}
            for lineno, raw in enumerate(reader, start=2):
                if raw is None or not any((raw.get(c) or "").strip() for c in header):
                    continue
                old = (raw.get("old_key") or "").strip()
                new = (raw.get("new_key") or "").strip()
                sha = (raw.get("work_sha") or "").strip()
                tier = (raw.get("tier") or "").strip()
                status = (raw.get("status") or "").strip()
                ev = (raw.get("evidence_type") or "").strip()
                if not old or not new:
                    md.problems.append(_problem("empty_key", "old_key/new_key 有空值", lineno))
                    continue
                if old == new:
                    md.problems.append(_problem("self_mapping", f"{old} 映射到自身", lineno))
                if not KEY_RE.match(old) or not KEY_RE.match(new):
                    md.problems.append(_problem("bad_key", f"键含非法字符：{old} → {new}", lineno))
                if not HEX64_RE.match(sha):
                    md.problems.append(_problem(
                        "bad_work_sha", f"{old} 的 work_sha 不是 64 位小写十六进制", lineno))
                if tier not in TIERS:
                    md.problems.append(_problem("bad_tier", f"{old} 的 tier 非法：{tier!r}", lineno))
                if status not in STATUSES:
                    md.problems.append(_problem("bad_status", f"{old} 的 status 非法：{status!r}", lineno))
                if ev not in EVIDENCE_TYPES:
                    md.problems.append(_problem(
                        "bad_evidence_type", f"{old} 的 evidence_type 非法：{ev!r}", lineno))
                if old in seen_old:
                    md.problems.append(_problem(
                        "duplicate_old_key", f"{old} 与第 {seen_old[old]} 行重复", lineno))
                if new in seen_new:
                    md.problems.append(_problem(
                        "duplicate_new_key", f"{new} 与第 {seen_new[new]} 行重复（一 new 多 old）", lineno))
                seen_old.setdefault(old, lineno)
                seen_new.setdefault(new, lineno)
                row = MapRow(
                    old_key=old, new_key=new, work_sha=sha,
                    case=(raw.get("case") or "").strip(),
                    issue=(raw.get("issue") or "").strip(),
                    evidence_type=ev,
                    evidence_ref=(raw.get("evidence_ref") or "").strip(),
                    tier=tier, status=status,
                    product_cite_occ=_parse_int(raw.get("product_cite_occ"), "product_cite_occ",
                                                lineno, md.problems),
                    product_bib_count=_parse_int(raw.get("product_bib_count"), "product_bib_count",
                                                  lineno, md.problems),
                    db_collision=(raw.get("db_collision") or "").strip(),
                    line=lineno,
                )
                md.rows.append(row)
    except OSError as exc:
        raise ToolError(f"读映射表失败：{path}（{exc}）") from exc
    # 链式映射（A→B 且 B→C）：同一键空间里写成一条即可，链式无法一次判定，判冲突。
    approved_old = {r.old_key for r in md.rows if r.status == "approved"}
    for r in md.rows:
        if r.status == "approved" and r.new_key in approved_old:
            md.problems.append(_problem(
                "chain_mapping", f"{r.old_key}→{r.new_key}，但 {r.new_key} 本身又是某行旧键", r.line))
    tier_filter = {t.strip().upper() for t in (tiers or []) if t.strip()}
    for r in md.rows:
        r.selected = (r.status == "approved") and (not tier_filter or r.tier in tier_filter)
    return md


# ── bib 解析（条目键位置；不依赖引擎） ─────────────────────────────────────


@dataclass
class BibEntry:
    etype: str
    key: str
    key_start: int
    key_end: int
    line: int


def parse_bib_entries(text: str) -> list[BibEntry]:
    """尽可能只读地取出 `@type{key, …}` 的键与绝对偏移；任何输入都不抛异常。

    只按大括号/圆括号配平找条目边界；`@string`/`@comment`/`@preamble` 跳过。
    """
    entries: list[BibEntry] = []
    n = len(text)
    i = 0
    starts = None
    while i < n:
        at = text.find("@", i)
        if at < 0:
            break
        i = at + 1
        # 跳过行注释里的伪条目（`% @book{...}`）
        line_start = text.rfind("\n", 0, at) + 1
        head = text[line_start:at]
        if re.search(r"(?<!\\)%", head):
            continue
        m = re.match(r"@\s*([A-Za-z][A-Za-z0-9_-]*)\s*([{(])", text[at:])
        if not m:
            continue
        etype = m.group(1).lower()
        open_ch = m.group(2)
        open_pos = at + m.end() - 1
        close_ch = "}" if open_ch == "{" else ")"
        depth = 0
        j = open_pos
        end = -1
        while j < n:
            ch = text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end < 0:
            break
        i = end + 1
        body_start = open_pos + 1
        body = text[body_start:end]
        if etype in ("string", "comment", "preamble"):
            continue
        # 键 = 开头到第一个顶层逗号（跳过空白）；没有逗号则整段当键的脏条目忽略。
        k = body_start
        while k < end and text[k] in " \t\r\n":
            k += 1
        key_start = k
        while k < end and text[k] not in ",}\r\n":
            k += 1
        key = text[key_start:k].strip()
        if not key or "," not in text[key_start:end]:
            continue
        key_end = key_start + len(text[key_start:k].rstrip()) - (len(key) - len(key.lstrip()))
        # 校正：key_start 指向 strip 后首字符
        key_end = key_start + len(key)
        if starts is None:
            starts = _line_starts(text)
        entries.append(BibEntry(etype=etype, key=key, key_start=key_start, key_end=key_end,
                                line=_line_of(starts, key_start)))
    return entries


# ── 扫描结果 ────────────────────────────────────────────────────────────────


@dataclass
class Edit:
    old: str
    new: str
    start: int
    end: int
    line: int


@dataclass
class FilePlan:
    relpath: str
    abspath: str
    kind: str                      # "tex" | "bib"
    text_before: str
    text_after: str = ""
    edits: list[Edit] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    stats_before: dict = field(default_factory=dict)
    stats_after: dict = field(default_factory=dict)
    roundtrip_ok: bool = False
    preview: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.edits)


@dataclass
class CasePlan:
    relpath: str
    abspath: str
    files: list[FilePlan] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    hits: list[dict] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    dangling_before: list[str] = field(default_factory=list)
    dangling_after: list[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def changed(self) -> bool:
        return any(f.changed for f in self.files) and not self.blocked


@dataclass
class PlanOpts:
    preview_lines: int = 8
    max_line_cols: int = 120


# ── 文件发现 ────────────────────────────────────────────────────────────────


def discover_cases(target: str) -> list[tuple[str, str]]:
    """返回 [(case_rel, abs_dir)]，确定性排序。

    - target 自身直接含 `.tex`/`reference.bib` → 单 case；
    - 否则递归找「直接含产物文件」的目录（跳过 `SKIP_DIRS`），一个目录 = 一个 case。
    """
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise ToolError(f"目标不是目录：{target}")

    def has_products(d: str) -> bool:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p) or name.endswith(SKIP_FILE_SUFFIXES):
                continue
            if name.endswith(".tex") or BIB_NAME_RE.match(name):
                return True
        return False

    if has_products(target):
        return [(os.path.basename(target), target)]
    found: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if root == target:
            continue
        if has_products(root):
            found.append((_norm_rel(target, root), root))
    if not found:
        raise ToolError(f"目标目录下找不到 `.tex` / `.bib` 产物：{target}")
    found.sort(key=lambda x: x[0])
    return found


def case_files(case_dir: str) -> tuple[list[str], list[str]]:
    tex, bib = [], []
    for name in sorted(os.listdir(case_dir)):
        p = os.path.join(case_dir, name)
        if not os.path.isfile(p) or name.startswith(".") or name.endswith(SKIP_FILE_SUFFIXES):
            continue
        if name.endswith(".tex"):
            tex.append(p)
        elif BIB_NAME_RE.match(name):
            bib.append(p)
    return tex, bib


# ── 单文件扫描 ──────────────────────────────────────────────────────────────


def scan_tex(text: str) -> tuple[list[dict], int, int]:
    """返回 (全部引用键 token, 引用命令数, 键出现数)。token: {key,start,end,line}。"""
    starts = _line_starts(text)
    tokens: list[dict] = []
    cmds = 0
    for m in CITE_RE.finditer(text):
        if m.group(1) not in CITE_COMMANDS:
            continue
        cmds += 1
        args = m.group(2)
        base = m.start(2)
        pos = 0
        for part in args.split(","):
            token = part.strip()
            if token and token != "*":
                off = part.find(token)
                s = base + pos + off
                tokens.append({"key": token, "start": s, "end": s + len(token),
                               "line": _line_of(starts, s)})
            pos += len(part) + 1
    return tokens, cmds, len(tokens)


def _apply_edits(text: str, edits: list[Edit]) -> str:
    """按偏移应用替换（编辑必须已排序、互不重叠）。"""
    out = []
    cur = 0
    for e in edits:
        if e.start < cur:
            raise ToolError(f"内部错误：编辑重叠（{e.old} @ {e.start}）")
        out.append(text[cur:e.start])
        out.append(e.new)
        cur = e.end
    out.append(text[cur:])
    return "".join(out)


def _reverse_edits(edits: list[Edit]) -> list[Edit]:
    """把「old→new」编辑换成「new→old」（用于逐字节回滚自证）。"""
    out: list[Edit] = []
    delta = 0
    for e in edits:
        s = e.start + delta
        out.append(Edit(old=e.new, new=e.old, start=s, end=s + len(e.new), line=e.line))
        delta += len(e.new) - len(e.old)
    return out


def _build_preview(file: FilePlan, limit: int) -> list[dict]:
    """行级 diff 预览：只列有编辑的行（每行取第一处编辑做上下文摘录）。"""
    if not file.edits:
        return []
    before_lines = file.text_before.split("\n")
    after_lines = file.text_after.split("\n")
    if len(before_lines) != len(after_lines):
        raise ToolError(f"内部错误：改名改变了行数（{file.relpath}）")
    starts = _line_starts(file.text_before)
    by_line: dict[int, Edit] = {}
    for e in file.edits:
        by_line.setdefault(e.line, e)
    out = []
    for line_no in sorted(by_line):
        e = by_line[line_no]
        line_before = before_lines[line_no - 1]
        line_after = after_lines[line_no - 1]
        col_before = e.start - starts[line_no - 1]
        col_after = col_before  # 同一行内前面若有其它编辑，偏移会变；用新行重新定位
        idx = line_after.find(e.new)
        col_after = idx if idx >= 0 else col_before
        out.append({
            "file": file.relpath,
            "line": line_no,
            "old": e.old,
            "new": e.new,
            "before": _excerpt(line_before, col_before, len(e.old)),
            "after": _excerpt(line_after, col_after, len(e.new)),
        })
        if len(out) >= limit:
            break
    return out


# ── 单 case 分析 ────────────────────────────────────────────────────────────


def analyze_case(case_rel: str, case_dir: str, rows: list[MapRow], opts: PlanOpts) -> CasePlan:
    cp = CasePlan(relpath=case_rel, abspath=case_dir)
    tex_paths, bib_paths = case_files(case_dir)
    by_old = {r.old_key: r for r in rows}
    all_cite_keys: set[str] = set()
    bib_keys: set[str] = set()
    bib_entry_count = 0

    # 先只读解析所有文件
    texts: dict[str, str] = {}
    for p in tex_paths + bib_paths:
        rel = _norm_rel(case_dir, p)
        try:
            texts[rel] = _read_text(p)
        except OSError as exc:
            raise ToolError(f"读文件失败：{p}（{exc}）") from exc
    selected_old = {r.old_key for r in rows}
    selected_new = {r.new_key for r in rows}
    bib_entries: dict[str, list[BibEntry]] = {}
    for p in bib_paths:
        rel = _norm_rel(case_dir, p)
        entries = parse_bib_entries(texts[rel])
        bib_entries[rel] = entries
        bib_entry_count += len(entries)
        counts: dict[str, int] = {}
        for e in entries:
            bib_keys.add(e.key)
            counts[e.key] = counts.get(e.key, 0) + 1
        for key, n in counts.items():
            if n > 1 and (key in selected_old or key in selected_new):
                cp.conflicts.append(_problem(
                    "duplicate_bib_entry", f"{rel} 条目键重复：{key}（{n} 条）"))
    for p in tex_paths:
        rel = _norm_rel(case_dir, p)
        tokens, cmds, occ = scan_tex(texts[rel])
        all_cite_keys.update(t["key"] for t in tokens)
    # 悬空（改名前后集合必须一致）
    dangling = sorted(k for k in all_cite_keys if k not in bib_keys)
    cp.dangling_before = dangling
    renamed = {k: by_old[k].new_key for k in dangling if k in by_old}
    cp.dangling_after = sorted(renamed.get(k, k) for k in dangling)

    # 逐文件构造编辑
    file_plans: dict[str, FilePlan] = {}
    for p in bib_paths:
        rel = _norm_rel(case_dir, p)
        fp = FilePlan(relpath=rel, abspath=p, kind="bib", text_before=texts[rel])
        entries = bib_entries[rel]
        for e in entries:
            row = by_old.get(e.key)
            if not row or not row.selected:
                continue
            fp.edits.append(Edit(old=e.key, new=row.new_key,
                                 start=e.key_start, end=e.key_end, line=e.line))
        file_plans[rel] = fp
    for p in tex_paths:
        rel = _norm_rel(case_dir, p)
        fp = FilePlan(relpath=rel, abspath=p, kind="tex", text_before=texts[rel])
        tokens, _, _ = scan_tex(texts[rel])
        for t in tokens:
            row = by_old.get(t["key"])
            if not row or not row.selected:
                continue
            fp.edits.append(Edit(old=t["key"], new=row.new_key,
                                 start=t["start"], end=t["end"], line=t["line"]))
        file_plans[rel] = fp

    # 每个文件：校验编辑、构造 after、统计不变量、抓「键之外」残留
    for rel, fp in file_plans.items():
        fp.edits.sort(key=lambda e: (e.start, e.end))
        # 同一 token 不应被判两次
        for a, b in zip(fp.edits, fp.edits[1:]):
            if a.end > b.start:
                raise ToolError(f"内部错误：编辑重叠（{rel}）")
        # 旧键在「引用参数/条目键」之外的独立出现 → 阻断（改名会动到键之外的字节）
        covered = [(e.start, e.end) for e in fp.edits]
        starts_fp = _line_starts(fp.text_before)
        for row in rows:
            for pos in _standalone_positions(fp.text_before, row.old_key):
                if not any(s <= pos < t for s, t in covered):
                    fp.conflicts.append(_problem(
                        "old_key_outside_context",
                        f"{rel} 的旧键 {row.old_key} 出现在引用参数/条目键之外（L"
                        f"{_line_of(starts_fp, pos)}）"))
        fp.text_after = _apply_edits(fp.text_before, fp.edits)
        # 机器判据 1：把编辑逆向恢复后必须与原文逐字节相同
        try:
            restored = _apply_edits(fp.text_after, _reverse_edits(fp.edits))
        except ToolError as exc:
            fp.conflicts.append(_problem("edit_internal", str(exc)))
            restored = None
        fp.roundtrip_ok = restored == fp.text_before
        if fp.edits and not fp.roundtrip_ok:
            fp.conflicts.append(_problem("roundtrip_failed", f"{rel} 逆向恢复后与原文不同"))
        # 机器判据 2：命令数 / 键出现数 / bib 条目数不变
        if fp.kind == "tex":
            tokens_b, cmds_b, occ_b = scan_tex(fp.text_before)
            tokens_a, cmds_a, occ_a = scan_tex(fp.text_after)
            fp.stats_before = {"cite_commands": cmds_b, "key_occurrences": occ_b}
            fp.stats_after = {"cite_commands": cmds_a, "key_occurrences": occ_a}
            if cmds_b != cmds_a or occ_b != occ_a:
                fp.conflicts.append(_problem(
                    "count_changed", f"{rel} 引用命令/键出现数变化：{cmds_b}→{cmds_a}, {occ_b}→{occ_a}"))
            # 新键不应与「未被本次替换掉的」已有键冲突
            for e in fp.edits:
                if e.new in bib_keys and e.new not in {x.old for x in fp.edits}:
                    cp.conflicts.append(_problem(
                        "new_key_exists_in_bib", f"{rel} 的新键 {e.new} 已在 bib 中存在（L{e.line}）"))
            for e in fp.edits:
                if e.old not in bib_keys:
                    cp.conflicts.append(_problem(
                        "old_key_cited_without_bib_entry",
                        f"{rel} 的旧键 {e.old} 被引用但 bib 无条目（L{e.line}）"))
        else:
            n_before = len(parse_bib_entries(fp.text_before))
            n_after = len(parse_bib_entries(fp.text_after))
            fp.stats_before = {"bib_entries": n_before}
            fp.stats_after = {"bib_entries": n_after}
            if n_before != n_after:
                fp.conflicts.append(_problem(
                    "count_changed", f"{rel} bib 条目数变化：{n_before}→{n_after}"))
        # 新键在本 case 内其它 bib 里已存在（且不是本行替换掉的键）→ 阻断
        for e in fp.edits:
            if e.new in bib_keys and e.new not in {r.old_key for r in rows}:
                cp.conflicts.append(_problem(
                    "new_key_exists_in_bib", f"{rel} 的新键 {e.new} 已在 bib 中存在（L{e.line}）"))
        if fp.edits:
            fp.preview = _build_preview(fp, opts.preview_lines)
        cp.files.append(fp)

    # 命中聚合（仅 selected 行）
    for row in rows:
        if not row.selected:
            continue
        locations = []
        for fp in cp.files:
            for e in fp.edits:
                if e.old == row.old_key:
                    locations.append({"file": fp.relpath, "kind": fp.kind,
                                      "line": e.line, "count": 1})
        # 同文件同键合并计数
        merged: dict[tuple[str, str], dict] = {}
        for loc in locations:
            k = (loc["file"], loc["kind"])
            if k in merged:
                merged[k]["count"] += 1
                merged[k]["lines"].append(loc["line"])
            else:
                merged[k] = {"file": loc["file"], "kind": loc["kind"], "count": 1,
                             "lines": [loc["line"]]}
        if merged:
            for item in sorted(merged.values(), key=lambda x: (x["file"], x["kind"])):
                item["lines"] = sorted(set(item["lines"]))
            cp.hits.append({"old": row.old_key, "new": row.new_key, "case": cp.relpath,
                            "tier": row.tier, "locations": sorted(
                                merged.values(), key=lambda x: (x["file"], x["kind"]))})
        else:
            cp.misses.append(row.old_key)

    cp.conflicts.extend(c for fp in cp.files for c in fp.conflicts)
    uniq, seen = [], set()
    for c in cp.conflicts:
        k = (c["code"], c["detail"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    cp.conflicts = uniq
    # 悬空集合必须前后一致（改名不能制造/消灭悬空）
    if cp.dangling_before != cp.dangling_after:
        cp.conflicts.append(_problem(
            "dangling_changed",
            f"悬空集合变化：{cp.dangling_before} → {cp.dangling_after}"))
    cp.blocked = bool(cp.conflicts)
    return cp


# ── 全目标分析 ──────────────────────────────────────────────────────────────


def analyze(target: str, md: MapData, opts: PlanOpts) -> dict:
    rows = md.selected()
    cases = discover_cases(target)
    plans = [analyze_case(rel, d, rows, opts) for rel, d in cases]
    hits = [h for cp in plans for h in cp.hits]
    hit_olds = {h["old"] for h in hits}
    # 报告级「未命中」= 映射行在整个目标里零命中（case 级零命中是正常的，不并入这里）
    misses = sorted(r.old_key for r in rows if r.selected and r.old_key not in hit_olds)
    conflicts = [dict(c, case=cp.relpath) for cp in plans for c in cp.conflicts]
    dangling = [{"case": cp.relpath, "keys": cp.dangling_before}
                for cp in plans if cp.dangling_before]
    changed_cases = [cp.relpath for cp in plans if cp.changed]
    unchanged_cases = [cp.relpath for cp in plans if not cp.changed and not cp.blocked]
    blocked_cases = [cp.relpath for cp in plans if cp.blocked]
    files_changed = sum(1 for cp in plans for f in cp.files if f.changed and not cp.blocked)
    report = {
        "tool": "rename_plan.py",
        "version": VERSION,
        "mode": "dry-run",
        "map": {
            "path": md.path,
            "rows_total": len(md.rows),
            "rows_approved": len(md.approved),
            "rows_selected": len(rows),
            "problems": sorted(md.problems, key=lambda p: (p.get("line", 0), p["code"])),
            "header_problems": md.header_problems,
        },
        "target": {"path": os.path.basename(os.path.abspath(target)),
                   "cases_total": len(cases),
                   "files_total": sum(len(cp.files) for cp in plans)},
        "cases": [_case_to_json(cp) for cp in plans],
        "hits_total": len(hits),
        "hits": hits,
        "misses": misses,
        "conflicts": conflicts,
        "dangling": dangling,
        "summary": {
            "cases_total": len(cases),
            "cases_changed": len(changed_cases),
            "cases_unchanged": len(unchanged_cases),
            "cases_blocked": len(blocked_cases),
            "files_changed": files_changed,
            "changed_cases": changed_cases,
            "blocked_cases": blocked_cases,
        },
        "db_check": [],
        "exit_code": 0,
    }
    if md.header_problems or md.problems or conflicts:
        report["exit_code"] = 1
    return report


def _case_to_json(cp: CasePlan) -> dict:
    return {
        "case": cp.relpath,
        "changed": cp.changed,
        "blocked": cp.blocked,
        "hits": cp.hits,
        "misses": cp.misses,
        "conflicts": cp.conflicts,
        "dangling_before": cp.dangling_before,
        "dangling_after": cp.dangling_after,
        "files": [{
            "file": fp.relpath,
            "kind": fp.kind,
            "changed": fp.changed,
            "edits": len(fp.edits),
            "sha256_before": sha256_text(fp.text_before),
            "sha256_after": sha256_text(fp.text_after),
            "stats_before": fp.stats_before,
            "stats_after": fp.stats_after,
            "roundtrip_ok": fp.roundtrip_ok,
            "conflicts": fp.conflicts,
        } for fp in cp.files],
        "preview": [p for fp in cp.files for p in fp.preview],
    }


# ── 库碰撞核对（只读；R4：只报告，不写库） ─────────────────────────────────


def _load_db_index(path: str) -> dict[str, list[dict]]:
    if not os.path.isfile(path):
        raise ToolError(f"bib_db 不存在：{path}")
    idx: dict[str, list[dict]] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                for k in [rec.get("key")] + list(rec.get("aliases") or []):
                    if k:
                        idx.setdefault(k, []).append(rec)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"读 bib_db 失败：{path}（{exc}）") from exc
    return idx


def check_db(rows: list[MapRow], db_path: str) -> list[dict]:
    idx = _load_db_index(db_path)
    out = []
    for row in rows:
        cands = idx.get(row.new_key)
        if not cands:
            cands = [r for k, rs in idx.items() if k.lower() == row.new_key.lower() for r in rs]
        exact = [r for r in cands if r.get("key") == row.new_key]
        sha_match = [r for r in cands if r.get("content_sha256") == row.work_sha]
        if not cands:
            status = "absent"
        elif exact and sha_match:
            status = "verified"
        elif exact:
            status = "content_differs"
        elif any(r.get("key", "").lower() == row.new_key.lower() for r in cands) and sha_match:
            status = "case_variant_verified"
        elif sha_match:
            status = "alias_verified"
        else:
            status = "alias_content_differs"
        entry = {"old_key": row.old_key, "new_key": row.new_key,
                 "work_sha": row.work_sha, "status": status,
                 "db_keys": sorted({str(r.get("key")) for r in cands})}
        row.db_check = entry
        out.append(entry)
    return out


# ── 应用（唯一写出口：显式 --out 的新产物副本） ─────────────────────────────


def write_apply(report: dict, plans: list[CasePlan], target: str, out_dir: str) -> dict:
    target = os.path.abspath(target)
    out_abs = os.path.abspath(out_dir)
    real_t, real_o = os.path.realpath(target), os.path.realpath(out_abs)
    if real_o == real_t or real_o.startswith(real_t + os.sep):
        raise ToolError("--out 不能等于或位于 --target 之内（拒绝就地/覆盖写入）")
    if os.path.exists(out_abs) and not os.path.isdir(out_abs):
        raise ToolError(f"--out 已存在且不是目录：{out_abs}")
    os.makedirs(out_abs, exist_ok=True)
    written, copied = 0, 0
    for cp in plans:
        if cp.blocked:
            continue
        for fp in cp.files:
            rel = _norm_rel(target, fp.abspath)
            dst = os.path.join(out_abs, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = fp.text_after if fp.changed else fp.text_before
            with open(dst, "w", encoding="utf-8", newline="") as fh:
                fh.write(data)
            if fp.changed:
                written += 1
            else:
                copied += 1
    report = dict(report)
    report["mode"] = "apply"
    report["out"] = os.path.basename(out_abs)
    report["written_files"] = written
    report["copied_files"] = copied
    report_path = os.path.join(out_abs, "rename-plan-apply.json")
    payload = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    with open(report_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(payload)
    report["report_path"] = os.path.basename(report_path)
    return report


# ── 反向映射 ────────────────────────────────────────────────────────────────


def emit_reverse(md: MapData, out_path: str) -> int:
    rows = []
    for r in md.rows:
        rows.append({
            "old_key": r.new_key, "new_key": r.old_key, "work_sha": r.work_sha,
            "case": r.case, "issue": r.issue, "evidence_type": r.evidence_type,
            "evidence_ref": r.evidence_ref, "tier": r.tier, "status": r.status,
            "product_cite_occ": r.product_cite_occ,
            "product_bib_count": r.product_bib_count, "db_collision": r.db_collision,
        })
    rows.sort(key=lambda x: (x["tier"], x["old_key"], x["new_key"]))
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(MAP_FIELDS))
        w.writeheader()
        w.writerows(rows)
    print(f"◆ 反向映射已写出：{out_path}（{len(rows)} 行，new→old；回滚用）")
    return 0


# ── 渲染 ────────────────────────────────────────────────────────────────────


def render_console(report: dict) -> str:
    lines = []
    mode = report["mode"]
    tag = "dry-run（只读，未写任何文件）" if mode == "dry-run" else "apply（已写 --out 副本）"
    lines.append(f"◆ B-144 rename-plan v{report['version']} · {tag}")
    mp = report["map"]
    lines.append(f"映射表：{mp['path']}（行 {mp['rows_total']} / approved {mp['rows_approved']}"
                 f" / 选用 {mp['rows_selected']}）")
    tt = report["target"]
    lines.append(f"目标：{tt['path']}（case {tt['cases_total']} / 文件 {tt['files_total']}）")
    if mp["header_problems"]:
        lines.append("")
        lines.append("== 映射表结构问题（阻断）==")
        for p in mp["header_problems"] + mp["problems"]:
            lines.append(f"  [{p['code']}] L{p.get('line', 0)} {p['detail']}")
        return "\n".join(lines) + "\n"
    if mp["problems"]:
        lines.append("")
        lines.append("== 映射表结构问题（阻断）==")
        for p in mp["problems"]:
            lines.append(f"  [{p['code']}] L{p.get('line', 0)} {p['detail']}")
    lines.append("")
    lines.append("== 命中（旧键 → 新键 → 位置/次数）==")
    if not report["hits"]:
        lines.append("  （无）")
    for h in report["hits"]:
        parts = []
        for loc in h["locations"]:
            ls = ",".join(f"L{n}" for n in loc["lines"])
            parts.append(f"{loc['file']} {ls}×{loc['count']}")
        lines.append(f"  {h['case']}  {h['old']} → {h['new']}  " + "；".join(parts))
    lines.append("")
    lines.append("== 未命中（映射行在目标零命中）==")
    lines.append("  " + ("、".join(report["misses"]) if report["misses"] else "（无）"))
    lines.append("")
    lines.append("== 冲突（阻断）==")
    if not report["conflicts"]:
        lines.append("  （无）")
    for c in report["conflicts"]:
        lines.append(f"  [{c['code']}] {c.get('case', '')} {c['detail']}")
    lines.append("")
    lines.append("== 悬空引用（.tex 有 / bib 无；改名前后同集合）==")
    if not report["dangling"]:
        lines.append("  （无）")
    for d in report["dangling"]:
        lines.append(f"  {d['case']}：{', '.join(d['keys'])}")
    lines.append("")
    if report.get("db_check"):
        lines.append("== 库核对（只读；R4：只报告不写库）==")
        for d in report["db_check"]:
            lines.append(f"  {d['old_key']} → {d['new_key']}：{d['status']}"
                         f"（库键 {','.join(d['db_keys']) or '—'}）")
        lines.append("")
    changed_preview = [p for fp in report["cases"] for p in fp["preview"]]
    if changed_preview:
        lines.append("== case 级 diff 预览（仅命中行；截断展示）==")
        for p in changed_preview:
            lines.append(f"  {p['file']} L{p['line']}  - {p['before']}")
            lines.append(f"  {' ' * len(p['file'])}       + {p['after']}")
        lines.append("")
    sm = report["summary"]
    lines.append("== 汇总 ==")
    lines.append(f"  命中行 {report['hits_total']}；变化 case {sm['cases_changed']} / "
                 f"{sm['cases_total']}（文件 {sm['files_changed']}）；"
                 f"未变化 case {sm['cases_unchanged']}；阻断 case {sm['cases_blocked']}")
    if mode == "apply":
        lines.append(f"  已写：改名文件 {report.get('written_files', 0)} / "
                     f"原样复制 {report.get('copied_files', 0)}"
                     f"（报告 {report.get('report_path', '')}）")
    lines.append(f"  退出码：{report['exit_code']}"
                 + ("（有阻断冲突，--apply 会拒绝写入）" if report["exit_code"] else ""))
    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


# ── 自检（hermetic） ────────────────────────────────────────────────────────


def _write(path: str, text: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _dir_snapshot(root: str) -> dict[str, str]:
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            with open(p, "rb") as fh:
                snap[_norm_rel(root, p)] = sha256_bytes(fh.read())
    return snap


def _self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory(prefix="rename-plan-selftest-") as tmp:
        # ---- 夹具：两个 case 的产物 ----
        root = os.path.join(tmp, "products")
        c1 = os.path.join(root, "Liangying_Zhao")
        c2 = os.path.join(root, "Beibei_Gao")
        tex1 = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "正文 A \\parencite[445]{Reichenbach1949} 与 \\parencite{A1}。\n"
            "多键 \\cites{A1, X1}；可选参数 \\citep[see][p.3]{A3}。\n"
            "\\end{document}\n"
        )
        bib1 = (
            "@book{A1,\n  author = {Old, A.},\n  title = {T1},\n  year = {1979}\n}\n"
            "@book{A3,\n  author = {Old, C.},\n  title = {T3},\n  year = {1962}\n}\n"
            "@book{Reichenbach1949,\n  author = {Reichenbach, H.},\n  title = {R}\n}\n"
            "@book{X1,\n  author = {X, X.},\n  title = {X1},\n  year = {2000}\n}\n"
        )
        tex2 = "\\documentclass{article}\n正文 \\parencite{X1}。\n"
        bib2 = "@book{X1,\n  author = {X, X.},\n  title = {X1},\n  year = {2000}\n}\n"
        _write(os.path.join(c1, "Liangying_Zhao.tex"), tex1)
        _write(os.path.join(c1, "reference.bib"), bib1)
        _write(os.path.join(c2, "Beibei_Gao.tex"), tex2)
        _write(os.path.join(c2, "reference.bib"), bib2)

        sha = lambda k: "a" * 64  # noqa: E731 — 夹具固定 sha
        map_path = os.path.join(tmp, "map.csv")
        _write(map_path, ",".join(MAP_FIELDS) + "\n" + "\n".join([
            f"A1,B1,{sha('_')},90-07,90,human_product,bib#B1,A,approved,1,1,db_same_key_same_work",
            f"A3,B3,{sha('_')},90-07,90,human_product,bib#B3,A,approved,0,1,db_same_key_same_work",
        ]) + "\n")

        md = load_map(map_path)
        check("map: 2 行读取、无结构问题", len(md.rows) == 2 and not md.problems,
              f"rows={len(md.rows)} problems={md.problems}")
        check("map: approved 行被选用", len(md.selected()) == 2)
        bad_sha_map = _write(os.path.join(tmp, "bad.csv"), "\n".join([
            ",".join(MAP_FIELDS),
            "A1,B1,not-a-sha,90-07,90,human_product,x,A,approved,0,0,none",
        ]) + "\n")
        md_bad = load_map(bad_sha_map)
        check("map: 坏 work_sha 被检出", any(p["code"] == "bad_work_sha" for p in md_bad.problems))
        below = "\n".join([",".join(MAP_FIELDS),
                           f"A1,B1,{sha('_')},90-07,90,human_product,x,A,proposed,0,0,none",
                           f"A2,B2,{sha('_')},90-07,90,human_product,x,A,approved,0,0,none",
                           f"A2,B3,{sha('_')},90-07,90,human_product,x,A,approved,0,0,none"])
        md_dup = load_map(_write(os.path.join(tmp, "dup.csv"), below + "\n"))
        check("map: 重复旧键被检出", any(p["code"] == "duplicate_old_key" for p in md_dup.problems))
        check("map: proposed 不选用",
              len(md_dup.selected()) == 2 and all(r.old_key != "A1" for r in md_dup.selected()))

        # ---- dry-run ----
        opts = PlanOpts(preview_lines=8)
        snap_before = _dir_snapshot(root)
        report = analyze(root, md, opts)
        snap_after = _dir_snapshot(root)
        check("dry-run: 零写入（目录 sha 快照前后一致）", snap_before == snap_after)
        check("dry-run: 2 个 case", report["target"]["cases_total"] == 2)
        a1_hits = [h for h in report["hits"] if h["old"] == "A1"]
        a1_total = sum(loc["count"] for h in a1_hits for loc in h["locations"])
        check("dry-run: A1 命中 3 次（tex 2 + bib 1）", a1_total == 3,
              json.dumps(report["hits"], ensure_ascii=False)[:300])
        check("dry-run: A3 命中 1 次（可选参数 cite）",
              any(h["old"] == "A3" and h["locations"][0]["count"] == 1 for h in report["hits"]))
        check("dry-run: case2 无命中（未变化）",
              report["summary"]["cases_changed"] == 1 and report["summary"]["cases_unchanged"] == 1)
        check("dry-run: 悬空为空", not report["dangling"])
        check("dry-run: 无冲突", not report["conflicts"])
        check("dry-run: 文件 stats 命令数不变",
              all(f["stats_before"].get("cite_commands", 0) == f["stats_after"].get("cite_commands", 0)
                  for cp in report["cases"] for f in cp["files"]))
        r1 = render_json(analyze(root, load_map(map_path), opts))
        r2 = render_json(analyze(root, load_map(map_path), opts))
        check("dry-run: 两次运行 JSON 逐字节相同", r1 == r2)

        # ---- 空映射表 / 无映射表：零变化 ----
        empty_map = _write(os.path.join(tmp, "empty.csv"), ",".join(MAP_FIELDS) + "\n")
        rep_empty = analyze(root, load_map(empty_map), opts)
        check("空映射表: 零命中零变化", rep_empty["hits_total"] == 0
              and rep_empty["summary"]["cases_changed"] == 0
              and rep_empty["summary"]["cases_unchanged"] == 2)
        check("空映射表: 退出码 0", rep_empty["exit_code"] == 0)

        # ---- apply ----
        out_dir = os.path.join(tmp, "out")
        plans = [analyze_case(r, d, md.selected(), opts) for r, d in discover_cases(root)]
        rep_apply = write_apply(report, plans, root, out_dir)
        check("apply: 写 2 个改名文件、复制 2 个原样文件",
              rep_apply["written_files"] == 2 and rep_apply["copied_files"] == 2,
              f"written={rep_apply['written_files']} copied={rep_apply['copied_files']}")
        out_c1 = _read_text(os.path.join(out_dir, "Liangying_Zhao", "Liangying_Zhao.tex"))
        out_b1 = _read_text(os.path.join(out_dir, "Liangying_Zhao", "reference.bib"))
        check("apply: .tex 只改引用参数中的键",
              "\\parencite{B1}" in out_c1 and "\\cites{B1, X1}" in out_c1
              and "\\citep[see][p.3]{B3}" in out_c1)
        check("apply: bib 只改条目键",
              "@book{B1,\n" in out_b1 and "@book{B3,\n" in out_b1
              and "author = {Old, A.}" in out_b1)
        check("apply: 未命中 case 逐字节同（复制）",
              _read_text(os.path.join(out_dir, "Beibei_Gao", "Beibei_Gao.tex")) == tex2
              and _read_text(os.path.join(out_dir, "Beibei_Gao", "reference.bib")) == bib2)
        check("apply: 报告落在 --out", os.path.isfile(
            os.path.join(out_dir, "rename-plan-apply.json")))
        neg = False
        try:
            write_apply(report, plans, root, os.path.join(root, "inside"))
        except ToolError:
            neg = True
        check("apply: 拒绝 --out 落在 target 内", neg)

        # ---- 逆向编辑：改回后逐字节同 ----
        for fp in plans[0].files:
            if fp.changed:
                back = _apply_edits(fp.text_after, _reverse_edits(fp.edits))
                check(f"apply: {fp.relpath} 逆向恢复逐字节相同", back == fp.text_before)

        # ---- 回滚映射 + 反向应用 ----
        rev_path = os.path.join(tmp, "rev.csv")
        emit_reverse(load_map(map_path), rev_path)
        rev_md = load_map(rev_path)
        check("reverse: 行列对调且 approved", {r.old_key for r in rev_md.rows} == {"B1", "B3"}
              and all(r.status == "approved" for r in rev_md.rows))
        plans_rev = [analyze_case(r, d, rev_md.selected(), opts) for r, d in discover_cases(out_dir)]
        out_back = os.path.join(tmp, "back")
        write_apply(report, plans_rev, out_dir, out_back)
        ok = True
        for dirpath, dirs, files in os.walk(root):
            if dirpath == root:
                continue
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                if _read_text(os.path.join(out_back, rel)) != _read_text(os.path.join(root, rel)):
                    ok = False
        check("reverse: 回滚后与原始产物逐字节相同", ok)

        # ---- 冲突用例 ----
        c3 = os.path.join(root, "Conflict_Case")
        _write(os.path.join(c3, "Conflict_Case.tex"), "正文 \\parencite{A1}。\n")
        _write(os.path.join(c3, "reference.bib"),
               bib1.replace("@book{A1,", "@book{B1,") + "\n% A1 残留\n")
        rep_bad = analyze(root, md, opts)
        codes = {c["code"] for c in rep_bad["conflicts"]}
        check("冲突: 旧键被引用但 bib 无条目", "old_key_cited_without_bib_entry" in codes, str(codes))
        check("冲突: 旧键出现在条目键之外", "old_key_outside_context" in codes, str(codes))
        check("冲突: 阻断时退出码 1", rep_bad["exit_code"] == 1)
        check("冲突: blocked case 被标记、退出码 1",
              rep_bad["summary"]["cases_blocked"] >= 1 and rep_bad["exit_code"] == 1)

        # ---- new_key 已存在的冲突 ----
        root2 = os.path.join(tmp, "products2")
        c4 = os.path.join(root2, "Case4")
        _write(os.path.join(c4, "Case4.tex"), "正文 \\parencite{A1}。\n")
        _write(os.path.join(c4, "reference.bib"),
               "@book{A1,\n author = {Old, A.}\n}\n@book{B1,\n author = {Other, B.}\n}\n")
        rep4 = analyze(root2, md, opts)
        check("冲突: 新键已在 bib 中存在",
              any(c["code"] == "new_key_exists_in_bib" for c in rep4["conflicts"]),
              str({c["code"] for c in rep4["conflicts"]}))

        # ---- 库核对（只读） ----
        db_path = os.path.join(tmp, "bibliography.jsonl")
        recs = [
            {"key": "B1", "aliases": [], "content_sha256": "a" * 64, "fields": {}},
            {"key": "FuLeiGeZhu2006", "aliases": ["B3"], "content_sha256": "a" * 64,
             "fields": {}},
            {"key": "C9", "aliases": [], "content_sha256": "b" * 64, "fields": {}},
        ]
        _write(db_path, "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
        checks_db = check_db(load_map(map_path).selected(), db_path)
        statuses = {c["new_key"]: c["status"] for c in checks_db}
        check("db: 精确键 + sha 相同 → verified", statuses.get("B1") == "verified", str(statuses))
        check("db: alias + sha 相同 → alias_verified", statuses.get("B3") == "alias_verified",
              str(statuses))
        # absent
        md_absent = load_map(_write(os.path.join(tmp, "absent.csv"),
                                    ",".join(MAP_FIELDS) + "\n"
                                    + f"A1,C9,{'c' * 64},90-07,90,human_product,x,A,approved,0,0,none\n"))
        statuses2 = {c["new_key"]: c["status"] for c in check_db(md_absent.selected(), db_path)}
        check("db: 库中键在但 sha 不同 → content_differs", statuses2.get("C9") == "content_differs",
              str(statuses2))

        # ---- 解析器边界 ----
        entries = parse_bib_entries("@book{K1,\n title = {a {nested} brace}\n}\n@comment{x}\n"
                                    "@string{s = {v}}\n@book{K2,\n title = {b}\n}\n")
        check("bib: 嵌套花括号/comment/string 正确跳过",
              [e.key for e in entries] == ["K1", "K2"], str([e.key for e in entries]))
        tokens, cmds, occ = scan_tex("\\parencite{A}\\cite[p.1]{B,C}\\nocite{*}"
                                     "\\unknown{D}\\cites{E}")
        check("tex: 多键/可选参数/白名单/复数命令", occ == 4 and cmds == 4,
              f"occ={occ} cmds={cmds} tokens={[t['key'] for t in tokens]}")
        check("tex: nocite{*} 不计键", all(t["key"] != "*" for t in tokens))

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    for name, ok, detail in checks:
        if not ok:
            print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""))
    print(f"SELF-TEST {passed}/{total} PASS")
    return 0 if passed == total else 1


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rename_plan.py",
        description="B-144 CJK 译名 bib key 改名：只读 dry-run 计划器 + 新产物副本应用")
    ap.add_argument("--rename-plan", metavar="MAP.csv",
                    help="映射表（CSV）；省略 = 空映射 = 零变化")
    ap.add_argument("--dry-run", action="store_true", help="只读扫描（唯一安全模式；不写文件）")
    ap.add_argument("--apply", action="store_true", help="把改名结果写到 --out 的新产物副本")
    ap.add_argument("--target", metavar="DIR", help="目标产物目录或含多个 case 的根目录")
    ap.add_argument("--out", metavar="DIR", help="--apply 的唯一写出口（不得位于 target 内）")
    ap.add_argument("--emit-reverse", action="store_true",
                    help="写出反向映射（new→old，回滚用；需 --out-map）")
    ap.add_argument("--out-map", metavar="REV.csv", help="--emit-reverse 的输出路径")
    ap.add_argument("--db", metavar="bibliography.jsonl",
                    help="可选：只读核对 work_sha 与库记录（R4：只报告，不写库）")
    ap.add_argument("--tier", action="append", default=None, metavar="A|B|C",
                    help="只选用指定 tier 的 approved 行（可重复；默认全部）")
    ap.add_argument("--preview-lines", type=int, default=8, metavar="N",
                    help="每个 case 的 diff 预览行数上限（默认 8）")
    ap.add_argument("--json", action="store_true", help="JSON 输出到 stdout（确定性）")
    ap.add_argument("--self-test", action="store_true", help="hermetic 自检（不读真实数据）")
    return ap


def _main(args: argparse.Namespace) -> int:
    if args.emit_reverse:
        if not args.out_map:
            raise ToolError("--emit-reverse 需要 --out-map <REV.csv>")
        return emit_reverse(_load_map_for(args), args.out_map)
    if not args.target:
        raise ToolError("缺少 --target（dry-run/apply 都需要目标产物目录）")
    if args.rename_plan:
        md = _load_map_for(args)
    else:
        md = MapData(path="（无映射表）")          # 默认关闭：无映射 = 零变化
    if md.header_problems:
        raise ToolError("映射表表头不合法：" + md.header_problems[0]["detail"])
    opts = PlanOpts(preview_lines=max(0, args.preview_lines))
    report = analyze(args.target, md, opts)
    if args.db:
        report["db_check"] = check_db(md.selected(), args.db)
    if args.apply:
        if not args.out:
            raise ToolError("--apply 需要 --out（唯一写出口）")
        plans = [analyze_case(rel, d, md.selected(), opts)
                 for rel, d in discover_cases(args.target)]
        if any(p.blocked for p in plans):
            # 冲突时先输出分析、再拒绝写入（不静默部分应用）
            _emit(report, args.json)
            raise ConflictError("有阻断冲突的 case，拒绝写入（先处理冲突再 --apply）")
        report = write_apply(report, plans, args.target, args.out)
        _emit(report, args.json)
        return 0
    _emit(report, args.json)
    return 1 if report["exit_code"] != 0 else 0


def _emit(report: dict, as_json: bool) -> None:
    sys.stdout.write(render_json(report) if as_json else render_console(report))


def _load_map_for(args: argparse.Namespace) -> MapData:
    if not args.rename_plan:
        raise ToolError("需要 --rename-plan <map.csv>")
    return load_map(args.rename_plan, tiers=args.tier or [])


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    modes = [bool(args.dry_run), bool(args.apply), bool(args.emit_reverse)]
    if sum(modes) == 0:
        ap.print_usage(sys.stderr)
        print("✗ rename_plan：必须指定 --dry-run / --apply / --emit-reverse / --self-test 之一",
              file=sys.stderr)
        return 2
    if sum(modes) > 1:
        print("✗ rename_plan：--dry-run / --apply / --emit-reverse 只能选一个", file=sys.stderr)
        return 2
    try:
        return _main(args)
    except ToolError as exc:
        print(f"✗ rename_plan：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — 内部错误也要带栈暴露，不静默
        traceback.print_exc()
        print(f"✗ rename_plan：内部错误 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
