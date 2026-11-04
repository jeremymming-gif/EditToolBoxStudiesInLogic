"""引用库 · 字段归位（写库视图；批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/fieldfix/spec.md`。引擎只给建议、人来裁决：
筛选行 44 → 主表（表头 30 / 行 32）→ 单行展开（176）→ 底部 44/56 双态条。
预览逐条 dry-run（sha 链式传递）→ 确认条（影响 + 库 sha）→ 逐条原子写
（`--apply` + `--expect-db-sha256` + `--backup`）→ 1 次本地提交，不 push。
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "fieldfix"
TITLE = "字段归位"
ROLE = "write"

FIX_LIMIT = 50
REGISTRIES = (("missing_field", "缺字段"), ("unknown_entry_type", "未知类型"))
TEXT_FIELDS = ("title", "author", "editor", "translator", "year", "publisher",
               "journal", "booktitle", "volume", "number", "pages", "doi", "url",
               "isbn", "language", "note", "address", "edition", "series", "school",
               "institution")
FIELD_LABELS = {"title": "题名", "author": "作者", "editor": "编者", "translator": "译者",
                "key": "键",
                "year": "年", "publisher": "出版社", "journal": "期刊",
                "booktitle": "书名", "volume": "卷", "number": "期", "pages": "页码",
                "doi": "DOI", "url": "URL", "isbn": "ISBN", "language": "语言",
                "note": "附注", "entry_type": "条目类型", "address": "地址",
                "edition": "版次", "series": "丛书", "school": "学校",
                "institution": "机构"}
CONFIDENCE = {"low": "高", "medium": "中", "high": "低"}
CONFIDENCE_LEVELS = ("高", "中", "低")
RISK = {"高": "low", "中": "medium", "低": "high"}
# 数据列右内边距：缺失原因列以「…」收尾，原先 0 内边距时与「候选值」列只剩约
# 7 px 光学间距，两列被读成一句（批 116 CLIP-A P2）。12 px 是可读的最小列间距，
# 同时给候选值列加宽（见 `_columns`），长出版社名不再一进来就省略。
CELL_PAD_R = 12
# 滚动表尾部留白：底部 44/56 常驻条紧贴表视口，滚到底时最后一行必须能完整
# 露出且不贴条（批 116 CLIP-A P2：末行曾被切掉下半行）。
TABLE_BOTTOM_PAD = 12


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field or "", field or "字段")


def confidence_of(row: dict) -> str:
    risk = _text(row.get("risk")).lower()
    if risk in CONFIDENCE:
        return CONFIDENCE[risk]
    value = _text(row.get("confidence")).lower()
    if value in CONFIDENCE:
        return CONFIDENCE[value]
    return "中"


def parse_suggestions(payload: dict | None) -> list[dict]:
    """把 `suggest-field-fixes --json` 归一成逐字段建议行。

    引擎真实形状（ENGINE_API）：每行 `{key, entry_type, missing[], candidates[]}`；
    `missing` 只是字段名，候选值/证据在 `candidates[{field,value,evidence,risk}]`。
    旧夹具/旧版还有嵌套 `suggestions[]`、`suggested_type`，一并兼容。
    """
    payload = payload or {}
    raw: list = []
    for name in ("suggestions", "items", "rows", "records", "candidates"):
        value = payload.get(name)
        if isinstance(value, list):
            raw = value
            break
    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("key") or item.get("record_key"))
        if not key:
            continue
        entry_type = _text(item.get("entry_type") or item.get("type"))
        anomaly = _text(item.get("anomaly_kind") or item.get("kind"))
        missing = item.get("missing_fields") or item.get("fields") or item.get("missing")
        suggestions = item.get("suggestions")

        # 引擎 candidates：按 field 建索引；没有 field 的是 entry_type 建议。
        cand_by_field: dict[str, tuple] = {}
        cand_type: tuple | None = None
        candidates = item.get("candidates")
        if isinstance(candidates, list):
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                field = _text(cand.get("field") or cand.get("name"))
                value = _text(cand.get("candidate") or cand.get("value")
                              or cand.get("guess") or cand.get("suggested_type"))
                reason = _text(cand.get("evidence") or cand.get("reason")
                               or cand.get("missing_reason"))
                risk = _text(cand.get("risk") or item.get("risk"))
                source = _text(cand.get("source"))
                if field:
                    cand_by_field.setdefault(
                        field, (value, reason, risk, source))
                elif value and cand_type is None:
                    cand_type = (value, reason, risk, source)

        pairs: list[tuple[str, str, str, str, str]] = []

        def _add(field: str, candidate: str, reason: str, risk: str,
                 source: str = "") -> None:
            if not field:
                return
            if any(existing[0] == field for existing in pairs):
                return
            pairs.append((field, candidate, reason, risk, source))

        if isinstance(suggestions, list):
            for suggestion in suggestions:
                if not isinstance(suggestion, dict):
                    continue
                field = _text(suggestion.get("field") or suggestion.get("name"))
                _add(field,
                     _text(suggestion.get("candidate") or suggestion.get("value")
                           or suggestion.get("guess")),
                     _text(suggestion.get("reason") or suggestion.get("missing_reason")
                           or suggestion.get("evidence")),
                     _text(suggestion.get("risk") or item.get("risk")),
                     _text(suggestion.get("source")))
        elif isinstance(missing, list):
            for field in missing:
                if isinstance(field, dict):
                    name = _text(field.get("field") or field.get("name"))
                    cands = cand_by_field.get(name, ("", "", "", ""))
                    _add(name,
                         _text(field.get("candidate") or field.get("value") or cands[0]),
                         _text(field.get("reason") or cands[1]),
                         _text(field.get("risk") or cands[2] or item.get("risk")),
                         _text(field.get("source") or cands[3]))
                else:
                    name = _text(field)
                    cands = cand_by_field.get(name, ("", "", "", ""))
                    reason = _text(cands[1] or item.get("reason"))
                    _add(name, _text(item.get("candidate") or cands[0]),
                         reason, _text(cands[2] or item.get("risk")), cands[3])
        else:
            field = _text(item.get("field"))
            if field:
                _add(field, _text(item.get("candidate")),
                     _text(item.get("missing_reason") or item.get("reason")),
                     _text(item.get("risk")), _text(item.get("source")))

        # candidates 里有、missing 没列出的字段（或 entry_type 建议）补进来。
        for field, (value, reason, risk, source) in cand_by_field.items():
            _add(field, value, reason, risk, source)
        if cand_type is not None:
            _add("entry_type", cand_type[0], cand_type[1],
                 cand_type[2], cand_type[3])

        if not pairs:
            # 未知类型行：可能是 entry_type 建议，也可能只有 key 改名建议。
            candidate = _text(item.get("candidate") or item.get("suggested_type"))
            if candidate:
                _add("entry_type", candidate,
                     _text(item.get("reason") or item.get("key_reason") or anomaly),
                     _text(item.get("risk")), _text(item.get("source")))
            elif _text(item.get("key_candidate")):
                _add("key", _text(item.get("key_candidate")),
                     _text(item.get("key_reason") or anomaly),
                     _text(item.get("key_risk") or item.get("risk")), "key")

        for field, candidate, reason, risk, source in pairs:
            rows.append({
                "id": f"{key}|{field}",
                "key": key,
                "title": _text(item.get("title")),
                "field": field,
                "candidate": candidate,
                "reason": reason,
                "risk": (risk or "medium").lower(),
                "source": source or ("registry" if anomaly else "missing_field"),
                "entry_type": entry_type,
                "anomaly_kind": anomaly,
                "key_candidate": _text(item.get("key_candidate")),
                "key_reason": _text(item.get("key_reason")),
            })
    # 去重（同 key 同 field 只留第一条）
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        unique.append(row)
    return unique


class BibFieldFixView:
    """字段归位：建议表 / 单行展开 / 底部 44↔56。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.registry = str(self._store_get("bib_fieldfix_registry", "missing_field")
                            or "missing_field")
        self.rows: list[dict] = []
        self.query = str(self._store_get("bib_fieldfix_q", "") or "")
        self.checked: set[str] = set()
        self.expanded: str | None = None
        self.fields: set[str] = set()
        self.confidences: set[str] = set()
        self.sort_key = "key"
        self.sort_desc = False
        self.manual: dict[str, str] = {}
        self.previewed: list[dict] = []
        self.payloads: dict[str, dict] = {}
        self.expect_sha = ""
        self.scanned_at = ""
        self.scan_sha = ""
        self.state = "empty"
        self.error = ""
        self.stderr = ""
        self.busy = False
        self.stage = ""
        self.progress = (0, 0)
        self.written = 0
        self.failed_at = 0
        self.commit: dict | None = None
        self.dirty_reason = ""
        self._root: ft.Container | None = None
        self._bottom_holder: ft.Container | None = None
        self.narrow = False

    # ── 宿主小工具 ───────────────────────────────────────────────────
    def _flet(self):
        return getattr(self.page, "page", None)

    def _store(self):
        store = getattr(self.page, "store", None)
        if store is None and self.app is not None:
            store = getattr(self.app, "store", None)
        return store

    def _store_get(self, key, default=None):
        store = self._store()
        if store is None or not hasattr(store, "get"):
            return default
        try:
            return store.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _store_set(self, key, value) -> None:
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

    def _notes(self) -> str:
        return str(getattr(self.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = getattr(self.page, "stats_data", None)
        db = stats.get("db") if isinstance(stats, dict) else ""
        if not db:
            try:
                db = GIT.default_db(self._notes())
            except Exception:  # noqa: BLE001
                db = ""
        return str(db or "")

    def _task(self, fn) -> None:
        runner = getattr(self.page, "run_task_ui", None)
        if callable(runner):
            runner(fn)
            return
        page = self._flet()
        if page is not None and hasattr(page, "run_task"):
            page.run_task(fn)
            return
        asyncio.ensure_future(fn())

    def _notify(self, message: str) -> None:
        notify = getattr(self.page, "notify", None)
        if callable(notify):
            notify(message)
            return
        toast = getattr(self.app, "toast", None)
        if callable(toast):
            toast(message)

    def _mark(self, event: str, **fields) -> None:
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            try:
                mark(event, **fields)
            except Exception:  # noqa: BLE001
                pass

    def _render(self) -> None:
        if self._root is not None:
            self._root.content = self._body()
            replant = getattr(self.page, "_replant_nav", None)
            if callable(replant):
                replant()
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001
                pass

    def _touch_bottom(self) -> None:
        if self._bottom_holder is not None:
            self._bottom_holder.content = self._bottom()
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001
                pass

    async def _call(self, call: EB.EngineCall) -> dict:
        try:
            return await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:
            return {"ok": False, "json": {}, "error": str(exc), "code": None}

    # ── 宿主接口 ─────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        self._root = ft.Container(content=self._body(), expand=True)
        return self._root

    def activate(self) -> None:
        focus = self._store_get("bib_focus_key")
        if focus and not self.rows and not self.busy:
            self._store_set("bib_focus_key", "")
            self.query = str(focus)
        if not self.rows and not self.busy:
            self._task(self._scan)

    def refresh_running(self) -> None:
        return

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    # ── 扫描 ─────────────────────────────────────────────────────────
    async def _scan(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.state = "loading"
        self.error = ""
        self.stage = "正在读候选…"
        self._render()
        res = await self._call(EB.bibdb_suggest_fixes_call(
            registry=self.registry, limit=FIX_LIMIT, db=self._db()))
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_fieldfix_scan", ok=False, code=res.get("code"))
            self._render()
            return
        payload = res.get("json") or {}
        self.rows = parse_suggestions(payload)
        self.checked = set()
        self.previewed = []
        self.payloads = {}
        self.expect_sha = ""
        self.manual = {}
        self.expanded = None
        self.scanned_at = datetime.now().strftime("%H:%M")
        self.scan_sha = _sha8(_text(payload.get("db_sha256")
                                    or payload.get("db_sha256_before")))
        self.state = "ready" if self.rows else "empty"
        self._mark("bib_fieldfix_scan", ok=True, registry=self.registry,
                   rows=len(self.rows))
        self._render()

    def _set_registry(self, registry: str) -> None:
        if registry == self.registry:
            return
        self.registry = registry
        self._store_set("bib_fieldfix_registry", registry)
        self.rows = []
        self.state = "loading"
        self._render()
        self._task(self._scan)

    # ── 筛选 / 排序 ──────────────────────────────────────────────────
    def _on_search(self, e) -> None:
        self.query = (e.control.value or "").strip()
        self._store_set("bib_fieldfix_q", self.query)

    def _apply_search(self, e=None) -> None:
        self._render()

    def _filtered(self) -> list[dict]:
        rows = list(self.rows)
        query = self.query.strip().lower()
        if len(query) >= 2:
            rows = [row for row in rows
                    if query in row["key"].lower() or query in row["title"].lower()
                    or query in row["field"].lower()]
        if self.fields:
            rows = [row for row in rows if row["field"] in self.fields]
        if self.confidences:
            rows = [row for row in rows if confidence_of(row) in self.confidences]
        reverse = self.sort_desc
        if self.sort_key == "key":
            rows.sort(key=lambda row: (row["key"], row["field"]), reverse=reverse)
        elif self.sort_key == "field":
            rows.sort(key=lambda row: (row["field"], row["key"]), reverse=reverse)
        elif self.sort_key == "confidence":
            order = {"高": 3, "中": 2, "低": 1}
            rows.sort(key=lambda row: order.get(confidence_of(row), 0), reverse=reverse)
        else:
            rows.sort(key=lambda row: (row["source"], row["key"]), reverse=reverse)
        return rows

    def _on_sort(self, index: int) -> None:
        mapping = {1: "key", 3: "field", 6: "confidence", 7: "source"}
        key = mapping.get(index, "key")
        if key == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = key
            self.sort_desc = False
        self._render()

    def _toggle_field(self, field: str) -> None:
        if field in self.fields:
            self.fields.discard(field)
        else:
            self.fields.add(field)
        self._invalidate("字段筛选已改")
        self._render()

    def _toggle_confidence(self, level: str) -> None:
        if level in self.confidences:
            self.confidences.discard(level)
        else:
            self.confidences.add(level)
        self._invalidate("置信度筛选已改")
        self._render()

    # ── 选择 / 展开 ──────────────────────────────────────────────────
    def _row_by_id(self, row_id: str) -> dict:
        for row in self.rows:
            if row["id"] == row_id:
                return row
        return {}

    def _toggle_check(self, row_id: str) -> None:
        if row_id in self.checked:
            self.checked.discard(row_id)
        else:
            self.checked.add(row_id)
        self._invalidate("选择已改")
        self._render()

    def _toggle_all_visible(self, e=None) -> None:
        visible = [row["id"] for row in self._filtered()]
        if all(row_id in self.checked for row_id in visible) and visible:
            for row_id in visible:
                self.checked.discard(row_id)
        else:
            self.checked.update(visible)
        self._invalidate("选择已改")
        self._render()

    def _clear_selection(self, e=None) -> None:
        self.checked.clear()
        self._invalidate("选择已清空")
        self._render()

    def _expand(self, row_id: str) -> None:
        self.expanded = None if self.expanded == row_id else row_id
        self._render()

    def _set_candidate(self, row_id: str, value: str) -> None:
        self.manual[row_id] = value
        self._invalidate("候选值已改，需重新预览")
        self._touch_bottom()

    # ── 预览 / 写入 ──────────────────────────────────────────────────
    def _selected_rows(self) -> list[dict]:
        return [row for row in self.rows if row["id"] in self.checked]

    def _value_for(self, row: dict) -> str:
        manual = self.manual.get(row["id"])
        return (manual if manual is not None else row.get("candidate") or "").strip()

    def _can_confirm(self) -> bool:
        return bool(self.previewed) and bool(self.expect_sha) and not self.busy \
            and not self.dirty_reason and self.state == "previewed"

    def _invalidate(self, reason: str) -> None:
        if self.previewed and not self.dirty_reason:
            self.dirty_reason = reason
            self.state = "ready"
            self._notify("预览已作废，请重新预览")

    def _build_preview_call(self, row: dict, expect_sha: str) -> EB.EngineCall:
        value = self._value_for(row)
        if row["field"] == "key":
            return EB.bibdb_edit_call(
                key=row["key"], new_key=value,
                entry_type=row.get("entry_type", ""),
                dry_run=True, expect_db_sha256=expect_sha, db=self._db())
        fields = {row["field"]: value} if row["field"] != "entry_type" else None
        entry_type = value if row["field"] == "entry_type" else row.get("entry_type", "")
        return EB.bibdb_edit_call(
            key=row["key"], fields=fields, entry_type=entry_type,
            dry_run=True, expect_db_sha256=expect_sha, db=self._db())

    async def _preview(self) -> None:
        if self.busy:
            return
        rows = self._selected_rows()
        if not rows:
            self._notify("先勾选要归位的字段")
            return
        self.busy = True
        self.state = "running"
        self.stage = "逐条 dry-run 0/%d" % len(rows)
        self.error = ""
        self.dirty_reason = ""
        self.previewed = []
        self.payloads = {}
        self._render()
        sha = self.expect_sha
        try:
            for index, row in enumerate(rows, start=1):
                if not self.busy:  # 取消
                    break
                self.stage = f"逐条 dry-run {index}/{len(rows)}"
                self.progress = (index, len(rows))
                self._render()
                res = await self._call(self._build_preview_call(row, sha))
                if not res.get("ok"):
                    self.state = "failure"
                    self.error = f"第 {index} 条 dry-run 失败：{self._error_text(res)}"
                    self.stderr = str(res.get("stderr") or "")
                    self.busy = False
                    self._mark("bib_fieldfix_preview", ok=False, index=index,
                               code=res.get("code"))
                    self._render()
                    return
                payload = res.get("json") or {}
                self.payloads[row["id"]] = payload
                self.previewed.append(row)
                sha = _text(payload.get("db_sha256_after")) or sha
            if not self.busy:
                self._render()
                return
            self.expect_sha = sha
            self.state = "previewed"
            self.stage = ""
            self._mark("bib_fieldfix_preview", ok=True, rows=len(self.previewed),
                       sha=_sha8(sha))
        finally:
            self.busy = False
        self._render()

    def _cancel(self, e=None) -> None:
        if not self.busy:
            return
        self.busy = False
        self.state = "cancelled"
        self.stage = "已取消预览，未写库"
        self._notify("已取消预览，未写库")
        self._render()

    def _preview_all(self, e=None) -> None:
        visible = [row["id"] for row in self._filtered()]
        if not visible:
            self._notify("这个口径下没有可归位的字段")
            return
        self.checked.update(visible)
        self._task(self._preview)

    def _ask_apply_all(self, e=None) -> None:
        rows = self._filtered()
        if not rows:
            self._notify("没有可应用的记录")
            return
        keys = len({row["key"] for row in rows})
        fields = len({row["field"] for row in rows})
        impact = (f"{len(rows)} 条建议 / {keys} 条记录 / {fields} 个字段 / "
                  f"预计 {len(rows)} 次 edit / 库 sha {_sha8(self.scan_sha) or '—'} / "
                  f"可 git 回退")
        dialog = S.danger_confirm(
            self._flet(), self.t, title="全部应用（先预览）",
            message="这只是预览：逐条 dry-run 后回到确认条，真正写入仍要在确认条点「确认写入」。",
            impact=impact, confirm_label="全部预览", on_confirm=self._preview_all)
        if dialog is not None:
            self._render()

    async def _write(self) -> None:
        if self.busy or not self._can_confirm():
            return
        rows = list(self.previewed)
        self.busy = True
        self.state = "running"
        self.stage = "写入中 0/%d" % len(rows)
        self.written = 0
        self.failed_at = 0
        self._render()
        sha = self.expect_sha
        db = self._db()
        backup = str(Path(tempfile.mkdtemp(prefix="bib-fieldfix-backup-")))
        for index, row in enumerate(rows, start=1):
            value = self._value_for(row)
            if row["field"] == "key":
                call = EB.bibdb_edit_call(
                    key=row["key"], new_key=value,
                    entry_type=row.get("entry_type", ""),
                    dry_run=False, expect_db_sha256=sha, backup=backup, db=db)
            else:
                fields = ({row["field"]: value} if row["field"] != "entry_type"
                          else None)
                entry_type = (value if row["field"] == "entry_type"
                              else row.get("entry_type", ""))
                call = EB.bibdb_edit_call(
                    key=row["key"], fields=fields, entry_type=entry_type,
                    dry_run=False, expect_db_sha256=sha, backup=backup, db=db)
            self.stage = f"写入中 {index}/{len(rows)}"
            self._render()
            res = await self._call(call)
            if not res.get("ok"):
                self.busy = False
                self.state = "stale" if self._sha_mismatch(res) else "failure"
                self.failed_at = index
                self.error = (f"第 {index} 条失败 · 已写 {self.written} 条未提交"
                              f"：{self._error_text(res)}")
                self.stderr = str(res.get("stderr") or "")
                self._mark("bib_fieldfix_write", ok=False, index=index,
                           written=self.written)
                self._render()
                return
            payload = res.get("json") or {}
            sha = _text(payload.get("db_sha256_after")) or sha
            self.expect_sha = sha
            self.written += 1
        commit: dict = {}
        if db:
            try:
                commit = await asyncio.to_thread(
                    GIT.commit_db, db, f"字段归位 {self.written} 条")
            except Exception as exc:  # noqa: BLE001
                commit = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        self.state = "success"
        self.commit = commit if isinstance(commit, dict) else {}
        self._mark("bib_fieldfix_write", ok=True, rows=self.written,
                   commit=_sha8(_text((self.commit or {}).get("sha"))))
        self._notify(f"已归位 {self.written} 条（1 次提交，未推送）")
        self._render()

    def _sha_mismatch(self, res: dict) -> bool:
        text = f"{res.get('error') or ''} {res.get('stderr') or ''}"
        return res.get("code") == 2 or "sha256" in text

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return _text(value).strip().splitlines()[0][:300]
        return f"引擎退出码 {res.get('code')}"

    def _revert(self, e=None) -> None:
        if not (self.commit or {}).get("ok"):
            self._notify("没有可回退的本次提交")
            return
        dialog = S.danger_confirm(
            self._flet(), self.t, title="回退这次字段归位？",
            message=("把 bibliography.jsonl 恢复到本次写入前的版本，再生成一条新提交；"
                     "其它文件不动，也不推送。"),
            impact="影响 1 个文件（bibliography.jsonl）· 生成 1 条回退提交 · 不推送",
            confirm_label="回退这次提交", on_confirm=lambda e: self._task(self._rollback))
        if dialog is not None:
            self._render()

    async def _rollback(self) -> None:
        db = self._db()
        try:
            result = await asyncio.to_thread(GIT.rollback, db) if db else {"ok": False}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("ok"):
            self.state = "ready"
            self.commit = None
            self.previewed = []
            self.payloads = {}
            self._notify("已回退本次归位（生成新提交，未推送）")
            self._task(self._scan)
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else ""
            self._notify(f"回退失败：{error or '未知错误'}")
        self._render()

    # ── 渲染 ─────────────────────────────────────────────────────────
    def _body(self) -> ft.Control:
        t = self.t
        width = int(getattr(self._flet(), "width", 1160) or 1160)
        self.narrow = width <= S.NARROW_W
        self._bottom_holder = ft.Container(content=self._bottom())
        meta = (f"{len(self.rows)} 条建议 · {len({r['field'] for r in self.rows})} 个字段 · "
                f"registry={self.registry} · 扫描 {self.scanned_at or '—'} · "
                f"库 sha {self.scan_sha or '—'}")
        body = ft.Column(spacing=0, expand=True, controls=[
            self._hint_line(),
            self._filter_row(),
            self._table(),
            self._bottom_holder,
        ])
        return S.page_shell(
            t, title=TITLE, meta=meta, role_chip=S.role_chip(t, "write"),
            action=C.secondary_button("重新扫描", lambda e: self._task(self._scan), t),
            body=body, dock=self._dock())

    def _dock(self) -> ft.Control:
        t = self.t
        if not self.stderr and not self.error:
            return ft.Container(height=S.DOCK_H)
        return ft.Container(
            height=S.DOCK_H, bgcolor=t.palette.log_bg,
            padding=ft.Padding(left=t.px(12), top=t.px(6), right=t.px(12), bottom=t.px(6)),
            content=C.text((self.stderr or self.error).splitlines()[0][:400], t,
                           role="log", color=t.palette.log_text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))

    def _hint_line(self) -> ft.Control:
        t = self.t
        if self.state == "failure" and self.error:
            return S.state_banner(t, "failure", message=self.error,
                                  stderr=self.stderr or None, page=self._flet())
        if self.state == "cancelled":
            return S.state_banner(t, "cancelled", message="已取消预览，未写库")
        if self.state == "loading":
            return S.state_banner(t, "loading", message=self.stage or "正在读候选…")
        return ft.Container(
            height=t.px(28), content=C.text(
                "预览 → 确认（含库 sha）→ 逐条原子写 · 只提交 bibliography.jsonl，"
                "1 次提交，不推送", t, "caption", t.palette.text_faint, max_lines=1))

    def _filter_row(self) -> ft.Control:
        t = self.t
        seg = ft.Row(spacing=0, controls=[
            self._scope_chip(key, label, 92) for key, label in REGISTRIES])
        field_chip = C.chip(
            "字段：" + ("全部" if not self.fields else f"{len(self.fields)} 项") + " ▾",
            t, active=bool(self.fields), on_click=lambda e: self._cycle_fields())
        field_chip.tooltip = "点按切换缺失字段筛选"
        field_chip._bib_filter_chip = "field"
        conf_chip = C.chip(
            "置信度：" + ("全部" if not self.confidences
                          else "/".join(sorted(self.confidences,
                                               key=CONFIDENCE_LEVELS.index))) + " ▾",
            t, active=bool(self.confidences),
            on_click=lambda e: self._cycle_confidences())
        conf_chip.tooltip = "点按切换置信度筛选"
        conf_chip._bib_filter_chip = "confidence"
        search = C.search_field(t, value=self.query, hint_text="搜索 key / 题名 / 字段",
                                on_change=self._on_search,
                                on_submit=self._apply_search, width=t.px(236))
        return S.filter_row(
            t, search=search, on_search=self._apply_search, count="上限 50 条",
            controls=[ft.Container(width=t.px(184), content=seg),
                      field_chip, conf_chip], page=self._flet())

    def _scope_chip(self, key: str, label: str, width: int) -> ft.Control:
        t = self.t
        p = t.palette
        active = self.registry == key
        return ft.Container(
            width=t.px(width), height=t.px(28), alignment=ft.Alignment.CENTER,
            bgcolor=p.accent_soft if active else p.surface,
            border=C.box(t, p.accent if active else p.border),
            border_radius=t.radius("control"),
            content=C.text(label, t, "caption",
                           p.on_accent_soft if active else p.text_muted),
            on_click=lambda e: self._set_registry(key))

    def _cycle_fields(self) -> None:
        present = [field for field in TEXT_FIELDS
                   if any(row["field"] == field for row in self.rows)]
        if not self.fields:
            if present:
                self.fields.add(present[0])
        else:
            index = 0
            for position, field in enumerate(present):
                if field in self.fields:
                    index = position + 1
                    break
            self.fields = {present[index]} if index < len(present) else set()
        self._render()

    def _cycle_confidences(self) -> None:
        present = [level for level in CONFIDENCE_LEVELS
                   if any(confidence_of(row) == level for row in self.rows)]
        if not self.confidences:
            if present:
                self.confidences.add(present[0])
        else:
            index = 0
            for position, level in enumerate(present):
                if level in self.confidences:
                    index = position + 1
                    break
            self.confidences = {present[index]} if index < len(present) else set()
        self._render()

    def _columns(self) -> tuple[list[str], list[float], list, list[str]]:
        if self.narrow:
            return (["☐", "记录", "字段", "缺失原因", "候选值", "置信度"],
                    [0.34, 2.46, 0.84, 1.44, 1.5, 0.64], [1, 2, 3, 4, 5],
                    ["left", "left", "left", "left", "left", "right"])
        return (["☐", "记录", "key", "字段", "缺失原因", "候选值", "置信度", "来源"],
                [0.34, 2.5, 1.36, 0.84, 1.4, 1.48, 0.58, 0.56],
                [2, 3, 5, 6, 7],
                ["left", "left", "left", "left", "left", "left", "right", "left"])

    def _table(self) -> ft.Control:
        t = self.t
        headers, weights, mono, align = self._columns()
        sort_index = {"key": 1, "field": 3, "confidence": 6, "source": 7}.get(
            self.sort_key, 1)
        rows = self._filtered()
        controls: list[ft.Control] = [S.table_header_row(
            t, headers, weights=weights, mono=tuple(mono), align=align,
            sortable=list(range(len(headers))), sort_index=sort_index,
            sort_desc=self.sort_desc, on_sort=self._on_sort)]
        if self.state == "loading":
            controls.append(S.empty_row(t, "正在读候选…"))
        elif not rows:
            controls.append(S.empty_row(
                t, "这个口径下没有要补的字段" if not self.rows else "没有匹配的建议"))
        for row in rows:
            controls.append(self._row_control(row, mono))
            if self.expanded == row["id"]:
                controls.append(self._expanded_row(row))
        # 尾部安全留白：滚到底时最后一行与底部常驻条之间留 12 px，不再贴边。
        controls.append(ft.Container(height=t.px(TABLE_BOTTOM_PAD)))
        return ft.Container(expand=True, content=ft.Column(
            spacing=0, scroll=ft.ScrollMode.AUTO, controls=controls))

    def _row_control(self, row: dict, mono) -> ft.Control:
        t = self.t
        checked = row["id"] in self.checked
        cells: list[ft.Control] = [
            ft.Container(width=t.px(34), alignment=ft.Alignment.CENTER_LEFT,
                         content=ft.Checkbox(
                             value=checked,
                             on_change=lambda e, rid=row["id"]: self._toggle_check(rid))),
        ]
        label = row["title"] or "—"
        if self.narrow:
            cells.append(ft.Container(expand=True, content=ft.Column(
                spacing=0, controls=[C.text(label, t, "small", max_lines=1),
                                     C.text(f"{row['key']} · {row['field']}", t,
                                            "caption", t.palette.text_faint,
                                            max_lines=1)])))
            values = [field_label(row["field"]), row["reason"] or "—",
                      self._value_for(row) or "—", confidence_of(row)]
        else:
            values = [label, row["key"], field_label(row["field"]), row["reason"] or "—",
                      self._value_for(row) or "—", confidence_of(row),
                      row["source"] or "—"]
        last = len(values) - 1
        for index, value in enumerate(values):
            cells.append(ft.Container(
                expand=True, alignment=ft.Alignment.CENTER_LEFT,
                # 末列不补内边距，右边缘才能和表头（表头统一右 8）对齐。
                padding=ft.Padding(left=0, top=0, bottom=0,
                                   right=t.px(CELL_PAD_R) if index < last else 0),
                content=C.text(str(value), t,
                               "mono" if (index + 1) in (1, 2) else "small",
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                               # 省略号不是砍字：全文挂 tooltip，hover 可读。
                               tooltip=(None if value in ("", "—") else str(value)))))
        control = ft.Container(
            height=t.px(32),
            bgcolor=t.palette.accent_soft if self.expanded == row["id"] else None,
            border=ft.Border(bottom=ft.BorderSide(
                0.6, t.palette.border)),
            padding=ft.Padding(left=t.px(8), top=0, right=t.px(8), bottom=0),
            on_click=lambda e, rid=row["id"]: self._expand(rid),
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=cells))
        control._bib_row = True
        return control

    def _expanded_row(self, row: dict) -> ft.Control:
        t = self.t
        p = t.palette
        evidence = [C.text(f"{row['key']} · {field_label(row['field'])}", t,
                           role="body-strong"),
                    C.text(row["reason"] or "引擎没有给出缺失原因", t, "small",
                           p.text_muted),
                    C.text(f"来源 {row['source'] or '—'} · 置信度 {confidence_of(row)}"
                           f"（引擎 risk={row['risk']}）", t, "caption", p.text_faint)]
        candidate = row.get("candidate") or ""
        radios = ft.RadioGroup(
            value=self.manual.get(row["id"], candidate),
            on_change=lambda e, rid=row["id"]: self._set_candidate(rid, e.control.value),
            content=ft.Column(spacing=0, controls=[
                ft.Radio(value=candidate or "—", label=f"候选值：{candidate or '（无）'}"),
            ]))
        manual = C.floating_field(
            t, f"手工覆盖 {field_label(row['field'])}", width=t.px(250),
            value=self.manual.get(row["id"], ""),
            hint="留空 = 用候选值" if candidate else "手工填写",
            on_change=lambda e, rid=row["id"]: self._set_candidate(rid, e.control.value))
        actions = ft.Row(spacing=t.sp("1"), controls=[
            C.secondary_button("应用此条", lambda e, rid=row["id"]: self._apply_single(rid),
                               t),
            C.text_button("跳过", lambda e, rid=row["id"]: self._skip(rid), t),
        ])
        return ft.Container(
            bgcolor=p.surface_2, height=t.px(176),
            border=ft.Border(left=ft.BorderSide(2, p.accent),
                             bottom=ft.BorderSide(0.6, p.border)),
            padding=ft.Padding(left=t.px(16), top=t.px(8), right=t.px(12), bottom=t.px(8)),
            content=ft.Column(spacing=t.sp("half"), controls=[
                ft.Row(spacing=t.sp("1"), controls=[
                    ft.Column(spacing=0, expand=True, controls=evidence),
                    radios, manual]),
                actions,
            ]))

    def _apply_single(self, row_id: str) -> None:
        self.checked = {row_id}
        self._task(self._preview)

    def _skip(self, row_id: str) -> None:
        self.checked.discard(row_id)
        self.rows = [row for row in self.rows if row["id"] != row_id]
        self.expanded = None
        self._invalidate("已跳过一条")
        self._render()

    def _bottom(self) -> ft.Control:
        t = self.t
        if self.state == "success":
            commit = self.commit or {}
            sha = _sha8(_text(commit.get("sha")))
            fields = len({row["field"] for row in self.previewed}) or self.written
            return S.result_banner(
                t, title=f"已归位 {self.written} 条 · {fields} 个字段",
                detail=f"提交 {sha or '未生成'} · 1 条提交 · 未 push",
                on_review=lambda e: self._goto("review"),
                on_revert=self._revert if commit.get("ok") else None)
        if self.state == "failure" and self.written:
            return S.stale_notice(
                t, title="部分写入，未提交",
                detail=f"{self.error} · 已写 {self.written} 条可用「回退已写」撤销",
                on_repreview=lambda e: self._task(self._preview))
        if self.state == "previewed" and self.previewed:
            stale = bool(self.dirty_reason)
            consequence = (f"将写入 {len(self.previewed)} 条记录 · "
                           f"{len({row['field'] for row in self.previewed})} 个字段")
            detail = "只提交 bibliography.jsonl（1 次提交）· 不推送 · 可 git 回退"
            if stale:
                detail = f"预览已过期：{self.dirty_reason}，先重新预览"
            return S.confirm_bar(
                t, consequence=consequence, detail=detail, sha=_sha8(self.expect_sha),
                on_confirm=lambda e: self._task(self._write)
                if self._can_confirm() else None,
                confirm_disabled=not self._can_confirm(),
                confirm_label="确认写入",
                on_repreview=lambda e: self._task(self._preview), stale=stale)
        if self.state == "running":
            return S.state_banner(
                t, "running", stage=self.stage or "逐条 dry-run", page=self._flet(),
                on_cancel=self._cancel)
        count = len(self.checked)
        actions = []
        if count:
            # P1-9：选中行后常驻次按钮；未选中整条批量栏不出现。
            apply_all = C.secondary_button(
                f"全部应用…（{len(self._filtered())}）", self._ask_apply_all, t)
            apply_all._bib_apply_all_btn = True
            actions.append(apply_all)
        actions.append(C.primary_button(
            "预览所选", lambda e: self._task(self._preview), t,
            disabled=not count or self.busy))
        bar = S.batch_bar(t, count=count, actions=actions,
                          on_clear=self._clear_selection)
        if bar is not None:
            return bar
        return ft.Container(
            height=t.px(44), bgcolor=t.palette.surface_2,
            border=ft.Border(top=ft.BorderSide(0.6, t.palette.border)),
            padding=ft.Padding(left=t.px(12), top=0, right=t.px(12), bottom=0),
            alignment=ft.Alignment.CENTER_LEFT,
            content=C.text("勾选要归位的字段后点「预览所选」；展开单条可手工覆盖。", t,
                           "caption", t.palette.text_faint))

    def _goto(self, view: str) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav(view)
            return
        self._store_set("bib_view", view)
        self._notify("已记录跳转目标；宿主接线后即可切换")
