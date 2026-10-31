"""B-312 修法 A：脚注提升通道补容器字段（默认 C 类并集口径内）。

独立 helper 模块的背景（B-313A）：`slcn/wordbody.py` 受 (h3) 行数上限 1500 约束且基线
恰好 1500 行，修法 A 的装配层补字段逻辑迁到本模块，wordbody 只保留最小调用点。

根因（B-312 / 90-07 实测）：脚注提升路径只走 `footnotes.parse_entry`，不做容器提取；
中文编著章节条目（`作者：《题名》，编者（主编）：《书名》卷次，出版信息`）只能靠
parse 之后的 `bibnorm.repair_word_refs` ② 从编号原文重解析——既丢 `editor` 字段，又把
`bibnorm._parse_cn_numbered_line` ③ 写进 note 的「编者（主编）」再拼一次，得到
`洪谦（主编）（主编）` 这样的重复 note。

修法（门控在 wordbody.parse_docx 的 `if c_class_union:` 内，`--no-c-class-union` 时
整块不执行 → 逐字节不变）：

* 编号条目：装配阶段直接从**编号原文**归位碎片字段、补容器字段，使 repair ② 的
  fragment 判据不再命中 → 保留补全后的字段；repair ④ 仍按既有判据把空题名脚注碎片
  并回来（键与正文引用不变，bib 条数不变）。
* 脚注提升条目：同作品已在书目里时**不物化**（留给 repair ④ 归并，避免重复条目）；
  独有作品才把行内证据提升为字段（纯脚注体例也能出容器字段）。

只补缺 / 只归位、不猜；证据全部来自该条目自己的行内原文；解析单点仍在
`bibnorm._parse_cn_numbered_line` 与 `wordrefs.apply_numbered_extras`。
"""

from __future__ import annotations

import re

#: `_parse_cn_numbered_line` ③ 写进 note 的「编者（主编）」形态（B-312 A2）。
_CN_EDITOR_NOTE_RE = re.compile(r"^(.{1,20})（主编）$")


def _cn_container_parse(raw: str) -> dict | None:
    """中文编著章节行内证据 → 字段；note 的「编者（主编）」归位 editor（B-312 A2）。"""
    from .bibnorm import _parse_cn_numbered_line

    parsed = _parse_cn_numbered_line(raw or "")
    if not parsed or parsed.get("_type") != "incollection" or not parsed.get("booktitle"):
        return None
    m = _CN_EDITOR_NOTE_RE.match(str(parsed.get("note") or ""))
    if m:
        editor = m.group(1).strip().removesuffix("（主编）").strip()
        if editor:
            parsed["editor"] = editor
        parsed["note"] = ""
    return parsed


def _fill_container_fields(entry: dict, parsed: dict) -> None:
    """author/title/year 归位；容器字段与 note 只补缺（不猜）。"""
    entry["_type"] = parsed.get("_type") or entry.get("_type") or "incollection"
    for field in ("author", "title", "year", "booktitle", "editor", "volume",
                  "number", "pages", "publisher", "address", "edition", "note"):
        value = str(parsed.get(field) or "").strip()
        keep = field in ("author", "title", "year") or not str(entry.get(field) or "").strip()
        if value and keep:
            entry[field] = value


def _needs_container_fix(entry: dict) -> bool:
    """只有解析碎片（标题带「》」/作者带「《」「：」）或空标题才补。"""
    title = str(entry.get("title") or "")
    author = str(entry.get("author") or "")
    return not title or "》" in title or "《" in author or "：" in author


def _entry_work_identity(entry: dict) -> tuple[str, str]:
    """条目「作者+题名」归一（只用于同作品判断，不改字段）。"""
    def clean(text: object, limit: int) -> str:
        text = re.sub(r"[（(][^）)]*[）)]", "", str(text or "")).lower()
        return re.sub(r"[^\w\u4e00-\u9fa5]+", "", text)[:limit]

    return clean(entry.get("author"), 40), clean(entry.get("title"), 60)


def apply_footnote_container_fields(doc) -> int:
    """给装配出的 bib 条目补容器字段；返回改动条数（只用于 warning 日志）。"""
    from .wordbody import _NUMBERED_PREFIX_RE
    from .wordrefs import apply_numbered_extras

    entries = getattr(doc, "bib_entries", None) or []
    raw_by_key = getattr(doc, "ref_raw_by_key", {}) or {}
    numbered_keys = {str(k) for k in (getattr(doc, "ref_key_by_num", {}) or {}).values()}
    numbered: list[tuple[dict, str]] = []
    promoted: list[tuple[dict, str]] = []
    for key, ent, raw in entries:
        text = str(raw_by_key.get(key) or raw or "").strip() if isinstance(ent, dict) else ""
        if not text:
            continue
        if str(key) in numbered_keys or _NUMBERED_PREFIX_RE.match(text):
            numbered.append((ent, text))
        else:
            promoted.append((ent, text))
    fixed = 0
    for ent, text in numbered:  # ① 编号条目：按编号原文归位 → repair ② 不再重解析
        parsed = _cn_container_parse(text)
        if parsed is not None and _needs_container_fix(ent):
            _fill_container_fields(ent, parsed)
            fixed += 1
    known = {_entry_work_identity(e) for _k, e, _r in entries
             if isinstance(e, dict) and str(e.get("title") or "").strip()}
    known.discard(("", ""))
    for ent, text in promoted:  # ② 同作品已在书目 → 留给 repair ④；只提升独有作品
        try:  # 脚注提升条目：英文 in-container / URL 字段只补缺
            apply_numbered_extras(ent, text)
        except Exception:  # noqa: BLE001 —— 补字段失败不影响出稿
            pass
        parsed = _cn_container_parse(text)
        if parsed is None or not _needs_container_fix(ent):
            continue
        ident = _entry_work_identity(parsed)
        if ident in known:
            continue
        had_title = bool(str(ent.get("title") or "").strip())
        _fill_container_fields(ent, parsed)
        if not had_title:  # 纯原文碎片：字段已归位，不重复保留 note
            ent.pop("note", None)
        known.add(ident)
        fixed += 1
    return fixed
