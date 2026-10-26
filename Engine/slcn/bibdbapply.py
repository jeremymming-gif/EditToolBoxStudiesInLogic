r"""引用库「默认回填」（B-217 ⑧ → **B-135 核心**，用户 2026-10-26 拍板）。

只做一件事：把 `bibdb.Store.match_reference()` 判成 **HIT_SAFE** 的条目，按库内
已核实记录**以库值覆盖产物现值 / 补齐缺失字段**，并产出 before/after 核对清单。
纪律（宁缺毋滥）：

  · 只碰 HIT_SAFE；候选（HIT_CANDIDATE）/未命中一律不动（绝不臆造，仍走原流程）；
  · 只碰 `FILL_FIELDS` 这 8 类字段，且必须在该条目**类型**的有效字段内
    （`_FIELD_BY_TYPE`；类型不在表里 → 整条跳过）；
  · **库有非空值 → 覆盖产物现值**（现值 == 库值时不动、不重写）；库缺/空 →
    **保留产物原值**（绝不抹除、不写空值）；字段存在但值为空 → 原位替换；
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


def _field_value_text(raw: str) -> str:
    """`.bib` 字段值原文（`{...}` / `"..."`）→ 比较用文本（去外层括号与首尾空白）。"""
    v = (raw or "").strip()
    if len(v) >= 2 and v[0] == "{" and v[-1] == "}":
        v = v[1:-1]
    elif len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        v = v[1:-1]
    return v.strip()


def plan_fills(store, entries, safe: dict) -> list[dict]:
    """HIT_SAFE 命中 → 逐条计算「要补的缺失字段 / 要覆盖的旧值字段」（不改文本）。

    B-135 核心（本批最小语义）：
      · 库值非空且 .bib **缺失/空值** → ``fills``（原位补空值 / 末尾插新字段）；
      · .bib **现值非空且 ≠ 库值** → ``replaces``（整值覆盖；同值不重写）；
      · 其余纪律不变：只碰 HIT_SAFE、只碰 `_FIELD_BY_TYPE` 保守表内类型、只碰
        `FILL_FIELDS` 8 类；候选/未命中/类型不在表 → 整条不动（不猜）。
    """
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
        fields = _parse_fields(body)
        existing = {n: (s, e, v) for n, s, e, v in fields}
        fills: dict[str, str] = {}
        replaces: dict[str, str] = {}
        for name in allowed:
            if name not in FILL_FIELDS:
                continue
            value = _clean_value(rec_fields.get(name))
            if not value:
                continue                  # 库缺/空 → 保留产物原值
            if name not in existing:
                fills[name] = value
                continue
            current = _field_value_text(body[existing[name][0]:existing[name][1]])
            if not current:
                fills[name] = value       # .bib 空值 → 原位补
            elif current != value:
                replaces[name] = value    # 现值非空且不同 → 整值覆盖
            # 现值 == 库值 → 不动（不重写）
        if fills or replaces:
            plans.append({"etype": etype, "key": key,
                          "fills": fills, "replaces": replaces})
    return plans


def _render_entry(body: str, fills: dict[str, str],
                  replaces: dict[str, str] | None = None) -> str | None:
    """字段体 + 要补/要覆盖的字段 → 新字段体；结构不安全 → None（调用方跳过该条）。

    ``fills``：缺失字段插到末尾 / 空值字段原位替换；``replaces``：已有非空值整段
    （``{...}`` / ``"..."``）换成 ``{库值}``。任何花括号不配平 / 结构异常 → None。
    """
    replaces = replaces or {}
    fields = _parse_fields(body)
    by_name = {n: (s, e) for n, s, e, _v in fields}
    edits: list[tuple[int, int, str]] = []   # (起点, 删除长度, 新文本)，逆序应用
    new_fields: dict[str, str] = {}
    for name, value in replaces.items():
        if name not in by_name:
            continue                      # 防御：plan 只覆盖 .bib 已有字段
        s, e = by_name[name]
        edits.append((s, e - s, "{" + value + "}"))
    for name, value in fills.items():
        if name in replaces:
            continue
        if name in by_name:
            s, e = by_name[name]
            raw = body[s:e]
            if raw not in ("{}", '""'):
                continue                  # 已有非空值（防御：plan 已过滤）→ 不补
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
    if any(name not in have for name in list(fills) + list(replaces)):
        return None
    return new_body


def apply_text(bib: str, plans: list[dict]) -> tuple[str, list[dict]]:
    """按计划回填/覆盖 → `(新文本, 实际应用清单)`；未命中条目逐字节不变。

    applied 每项：``{"key","etype","filled":{全部应用字段: 新值},"replaced":{被覆盖
    字段: 旧值}}``；``filled`` 是**全部应用项**口径（bibpipeline 的「N 项」用它）。
    """
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
        fills = dict(plan.get("fills") or {})
        replaces = dict(plan.get("replaces") or {})
        if not fills and not replaces:
            continue
        body = bib[body_start:close]
        old_values = {n: _field_value_text(v)
                      for n, _s, _e, v in _parse_fields(body) if n in replaces}
        new_body = _render_entry(body, fills, replaces)
        if new_body is None or new_body == body:
            continue
        edits.append((body_start, close - body_start, new_body))
        applied.append({"key": key, "etype": etype,
                        "filled": {**fills, **replaces},
                        "replaced": {n: old_values.get(n, "") for n in replaces}})
    new_bib = bib
    for pos, cut, text in sorted(edits, key=lambda x: -x[0]):
        new_bib = new_bib[:pos] + text + new_bib[pos + cut:]
    return new_bib, applied


def format_checklist(applied: list[dict], stem: str = "") -> str:
    """before/after 核对清单（沿用「参考文献核对.txt」分节体例；B-135 核心口径）。"""
    n_items = sum(len(a.get("filled") or {}) for a in applied or [])
    lines = [f"  ── 引用库回填（{len(applied or [])} 条 / {n_items} 项）──",
             "  命中已核实条目后：库内非空值**覆盖产物现值**或**补齐缺失字段**（8 类）；"
             "库缺字段保留产物原值；候选/未命中一律不动。",
             "  上文「按条目列出」若把这些字段列为必补，以本节为准。"]
    for a in applied or []:
        lines.append(f"  [{a.get('etype') or '?'}] {a.get('key')}")
        replaced = a.get("replaced") or {}
        for name, value in (a.get("filled") or {}).items():
            if name in replaced:
                lines.append(f"      {name}：（旧值 {replaced[name]}）→ {value}")
            else:
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

def _unique_normalized_title(store, title: str) -> bool:
    """库内「归一化题名完全相同」的记录是否**恰好 1 条**（短引题名唯一性判据）。"""
    try:
        from . import bibdb

        wanted = bibdb.normalize_title(title)
        if not wanted:
            return False
        records = getattr(store, "_records", None) or []
        hits = sum(
            1 for rec in records
            if bibdb.normalize_title((rec.get("fields") or {}).get("title") or "") == wanted)
        return hits == 1
    except Exception:  # noqa: BLE001
        return False


def resolve_short_citation(store, author: str = "", title: str = "",
                           year: str = "") -> dict | None:
    """B-220 N5：纯引用脚注（只有作者+题名、缺完整条目）→ 引用库安全命中 → bib 条目。

    只在 `Store.match_reference()` 判 **HIT_SAFE**（库内已核实）且题名相似度 ≥0.90
    时返回；候选/未命中 → ``None``（不编造，仍走原路径：脚注保留 + 人工核对 warning）。
    返回 ``{"key", "entry", "raw", "record_key", "title_similarity"}``；``entry`` 是
    可直接交给 `footnotes.entry_to_bib()` 的字段 dict（含 ``_type``）。

    调用方：`wordbody.parse_docx()`（仅当 convert 已解析出引用库且默认回填开着时）。
    """
    if store is None:
        return None
    from . import bibdb

    title = str(title or "").strip().rstrip(" ,，、;；:：")
    author = str(author or "").strip().rstrip(" ,，、;；:：")
    if not title:
        return None
    fields = {"title": title}
    if author:
        fields["author"] = author
    if year:
        fields["year"] = str(year)
    try:
        match = store.match_reference(fields)
    except Exception:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
        return None
    if not isinstance(match, dict) or match.get("status") != bibdb.HIT_SAFE:
        # 作者一侧对不上（源稿短引把作者姓拼错，如 `Gary Waston`）时，允许退一步做
        # **只按题名**的第二次匹配，但必须同时满足：HIT_SAFE + 题名相似度 ≥0.98 +
        # 库内**归一化题名完全相同的记录恰有 1 条**（唯一，不会张冠李戴）。
        if not author:
            return None
        try:
            match = store.match_reference({"title": title})
        except Exception:  # noqa: BLE001
            return None
        if (not isinstance(match, dict) or match.get("status") != bibdb.HIT_SAFE
                or not _unique_normalized_title(store, title)):
            return None
        try:
            if float(match.get("title_similarity") or 0.0) < 0.98:
                return None
        except (TypeError, ValueError):
            return None
    rec = match.get("record") or {}
    rec_key = str(match.get("record_key") or rec.get("key") or "").strip()
    if not rec_key:
        return None
    sim = match.get("title_similarity")
    try:
        if sim is not None and float(sim) < 0.90:
            return None
    except (TypeError, ValueError):
        return None
    out_fields = dict(bibdb.canonical_fields(rec.get("fields") or {}))
    out_fields["_type"] = str(rec.get("entry_type") or "").strip() or "misc"
    return {
        "key": rec_key,
        "entry": out_fields,
        "raw": f"{author} {title}".strip(),
        "record_key": rec_key,
        "title_similarity": sim,
    }

def attach_short_citation(doc, fn, warn, store) -> bool:
    """B-220 N6 接线：把「纯引用脚注 → 库 HIT_SAFE 安全命中」落到 doc/fn。

    命中 → 该脚注改为 ``pure_citation_matched``（渲染成 `\parencite`）、库记录进
    `doc.bib_entries`/`ref_raw_by_key`，并写一条可见 warning；不命中 → **一个字段都
    不改**、返回 False（调用方维持旧路径：脚注保留 + 人工核对）。
    """
    info = resolve_short_citation(
        store,
        author=str(getattr(warn, "author", "") or ""),
        title=str(getattr(warn, "title", "") or ""),
        year=str(getattr(warn, "year", "") or ""))
    if not info:
        return False
    key = info["key"]
    fn.kind = "pure_citation_matched"
    fn.key = key
    fn.entry = info["entry"]
    if not any(k == key for k, _e, _r in getattr(doc, "bib_entries", [])):
        doc.bib_entries.append((key, info["entry"], info["raw"]))
    doc.ref_raw_by_key[key] = info["raw"]
    doc.warnings.append(
        "短引经引用库安全命中（库内已核实，未编造）："
        f"「{str(getattr(warn, 'text', ''))[:40]}」→ {key}")
    return True
