r"""引用库「默认回填」（B-217 ⑧，用户 2026-10-24 拍板：默认回填）。

只做一件事：把 `bibdb.Store.match_reference()` 判成 **HIT_SAFE** 的条目，按库内
已核实记录**只补缺失字段**，并产出 before/after 核对清单。纪律（宁缺毋滥）：

  · 只碰 HIT_SAFE；候选（HIT_CANDIDATE）/未命中一律不动（绝不臆造，仍走原流程）；
  · 只补 `FILL_FIELDS` 这 8 类字段，且必须在该条目**类型**的有效字段内
    （`_FIELD_BY_TYPE`；类型不在表里 → 整条跳过）；
  · **已有非空字段不覆盖**；字段存在但值为空 → 原位替换（不产生重复字段）；
  · 条目在 .bib 里定位不到 / 结构校验失败 / 任何意外 → 该条跳过（fail-soft，
    绝不写半条）；返回的 new_text 与旧文本在**未命中条目上逐字节相同**。

调用方：`convert.py::bibdb_readonly_scan()`（默认开，`--no-bibdb-apply` 关闭时
本模块完全不被调用 → 产物/日志回到旧的只读比对口径）。
"""

from __future__ import annotations

import os
import re

#: 允许回填的字段（spec §8.4 的 8 类；url/doi 等不在其列——那是别的卡的范围）
FILL_FIELDS: tuple[str, ...] = (
    "journal", "volume", "number", "pages", "booktitle", "editor",
    "publisher", "address",
)

#: 条目类型 → 该类型下**有效且有印刷意义**的可回填字段（保守表：不认识的类型不动）
_FIELD_BY_TYPE: dict[str, tuple[str, ...]] = {
    "article": ("journal", "volume", "number", "pages"),
    "book": ("publisher", "address", "editor", "volume", "pages", "number"),
    "booklet": ("publisher", "address", "editor"),
    "collection": ("editor", "publisher", "address", "volume", "number"),
    "incollection": ("booktitle", "editor", "publisher", "address", "pages", "volume"),
    "inbook": ("booktitle", "editor", "publisher", "address", "pages", "volume"),
    "inproceedings": ("booktitle", "editor", "publisher", "address", "pages", "volume"),
    "conference": ("booktitle", "editor", "publisher", "address", "pages", "volume"),
    "proceedings": ("editor", "publisher", "address", "volume", "number"),
    "phdthesis": ("address",),
    "mastersthesis": ("address",),
    "thesis": ("address",),
}

_ENT_HEAD_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,")
_FIELD_HEAD_RE = re.compile(r"([A-Za-z][\w:\-]*)\s*=\s*")


def _clean_value(value) -> str:
    """库字段值 → 可写入 bib 的单行值；花括号不配平/含换行 → 空（放弃该字段）。"""
    v = " ".join(str(value if value is not None else "").split())
    if not v or "{" in v or "}" in v:
        return ""
    return v


def entry_spans(bib: str) -> list[tuple[str, str, int, int, int]]:
    """定位 .bib 里每条顶层条目的 `(etype, key, @起点, 字段体起点, 右花括号位置)`。

    只认**行首**条目（整行 `%` 注释掉的旧条目不动——与 `bibcheck.split_entries()`
    剥注释同一口径）；花括号配平感知。
    """
    out: list[tuple[str, str, int, int, int]] = []
    for m in _ENT_HEAD_RE.finditer(bib or ""):
        line_start = bib.rfind("\n", 0, m.start()) + 1
        if bib[line_start:m.start()].strip():
            continue                      # 行内/注释行内的同形文本：不是顶层条目
        i = m.end()
        depth = 1
        j = i
        while j < len(bib) and depth > 0:
            ch = bib[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            j += 1
        if depth != 0:
            continue
        out.append((m.group(1).strip().lower(), m.group(2).strip(),
                    m.start(), i, j - 1))
    return out


def _parse_fields(body: str) -> list[tuple[str, int, int, str]]:
    """解析字段体 → `[(字段名, 值起点, 值终点, 值原文)]`（位置相对 body，含花括号）。

    值起点/终点覆盖**带花括号**的整个值（`{...}` / `"..."`）；认不出的片段跳到下个
    逗号，不猜。字段名小写（`issue` 不归一——调用方只比对 `FILL_FIELDS`）。
    """
    fields: list[tuple[str, int, int, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        m = _FIELD_HEAD_RE.match(body, i)
        if not m:
            j = body.find(",", i)
            i = n if j < 0 else j + 1
            continue
        name = m.group(1).lower()
        k = m.end()
        if k < n and body[k] == "{":
            depth = 0
            j = k
            while j < n:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                break
            fields.append((name, k, j + 1, body[k:j + 1]))
            i = j + 1
        elif k < n and body[k] in "\"'":
            q = body[k]
            j = body.find(q, k + 1)
            if j < 0:
                break
            fields.append((name, k, j + 1, body[k:j + 1]))
            i = j + 1
        else:
            fields.append((name, k, k, ""))
            i = k
    return fields


def plan_fills(store, entries, safe: dict) -> list[dict]:
    """HIT_SAFE 命中 → 逐条计算「缺哪些字段、库值是什么」（不改文本）。"""
    from . import bibdb

    plans: list[dict] = []
    for etype, key, body in entries or []:
        match = safe.get(key) if isinstance(safe, dict) else None
        if not match or match.get("status") != bibdb.HIT_SAFE:
            continue
        allowed = _FIELD_BY_TYPE.get(str(etype or "").strip().lower())
        if not allowed:
            continue                      # 类型不在保守表 → 不动（不猜字段有效性）
        rec_fields = bibdb.canonical_fields((match.get("record") or {}).get("fields") or {})
        existing = {n for n, _s, _e, _v in _parse_fields(body)}
        fills: dict[str, str] = {}
        for name in allowed:
            if name not in FILL_FIELDS or name in existing:
                continue
            value = _clean_value(rec_fields.get(name))
            if value:
                fills[name] = value
        if fills:
            plans.append({"etype": etype, "key": key, "fills": fills})
    return plans


def _render_entry(body: str, fills: dict[str, str]) -> str | None:
    """字段体 + 要补的字段 → 新字段体；结构不安全 → None（调用方跳过该条）。"""
    fields = _parse_fields(body)
    by_name = {n: (s, e) for n, s, e, _v in fields}
    edits: list[tuple[int, int, str]] = []   # (起点, 删除长度, 新文本)，逆序应用
    new_fields: dict[str, str] = {}
    for name, value in fills.items():
        if name in by_name:
            s, e = by_name[name]
            raw = body[s:e]
            if raw not in ("{}", '""'):
                continue                  # 已有非空值（防御：plan 已过滤）→ 不覆盖
            edits.append((s, e - s, "{" + value + "}"))
        else:
            new_fields[name] = value
    if new_fields:
        m_tail = re.search(r"\s*$", body)
        tail = body[m_tail.start():]
        trim_ends = m_tail.start()
        if body[:trim_ends].strip() and not body[:trim_ends].rstrip().endswith(","):
            edits.append((trim_ends, 0, ","))   # 补最后一个字段后的逗号
        indent = "  "
        for m in re.finditer(r"(?m)^([ \t]*)[A-Za-z][\w:\-]*\s*=", body):
            indent = m.group(1) or indent
            break
        insertion = ("" if "\n" in tail else "\n") + "".join(
            f"{indent}{name} = {{{value}}},\n" for name, value in new_fields.items())
        edits.append((len(body), 0, insertion))
    new_body = body
    for pos, cut, text in sorted(edits, key=lambda x: -x[0]):
        new_body = new_body[:pos] + text + new_body[pos + cut:]
    depth = 0
    for ch in new_body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return None
    if depth != 0 or "}{" in new_body:
        return None
    have = {n for n, _s, _e, _v in _parse_fields(new_body)}
    if any(name not in have for name in fills):
        return None
    return new_body


def apply_text(bib: str, plans: list[dict]) -> tuple[str, list[dict]]:
    """按计划回填 → `(新文本, 实际回填清单)`；未命中条目逐字节不变。"""
    spans = {key: (etype, start, body_start, close)
             for etype, key, start, body_start, close in entry_spans(bib)}
    edits: list[tuple[int, int, str]] = []
    applied: list[dict] = []
    for plan in plans or []:
        key = plan.get("key") or ""
        span = spans.get(key)
        if not span:
            continue
        etype, _start, body_start, close = span
        if etype and plan.get("etype") and etype != str(plan["etype"]).lower():
            continue
        body = bib[body_start:close]
        new_body = _render_entry(body, plan.get("fills") or {})
        if new_body is None or new_body == body:
            continue
        edits.append((body_start, close - body_start, new_body))
        applied.append({"key": key, "etype": etype,
                        "filled": dict(plan.get("fills") or {})})
    new_bib = bib
    for pos, cut, text in sorted(edits, key=lambda x: -x[0]):
        new_bib = new_bib[:pos] + text + new_bib[pos + cut:]
    return new_bib, applied


def format_checklist(applied: list[dict], stem: str = "") -> str:
    """before/after 核对清单（沿用「参考文献核对.txt」的分节体例）。"""
    n_items = sum(len(a.get("filled") or {}) for a in applied or [])
    lines = [f"  ── 引用库回填（{len(applied or [])} 条 / {n_items} 项）──",
             "  命中已核实条目后**只补缺失字段**；已有非空字段不覆盖，候选/未命中一律不动。",
             "  上文「按条目列出」若把这些字段列为必补，以本节为准。"]
    for a in applied or []:
        lines.append(f"  [{a.get('etype') or '?'}] {a.get('key')}")
        for name, value in (a.get("filled") or {}).items():
            lines.append(f"      {name}：（缺失）→ {value}")
    return "\n".join(lines)


def write_checklist(out_dir: str, stem: str, applied: list[dict],
                    log=None) -> str:
    """把核对清单追加进 `<stem>_参考文献核对.txt`（没有就按同体例新建）；返回路径。"""
    from .fsutil import atomic_write

    name = f"{stem}_参考文献核对.txt" if stem else "参考文献核对.txt"
    path = os.path.join(out_dir, name)
    section = format_checklist(applied, stem)
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            old = f.read()
        new = old.rstrip("\n") + "\n\n" + section
    else:
        new = f"══ {stem} 参考文献核对 ══\n\n" + section
    atomic_write(path, new + "\n", newline="\n", log=log)
    return path
