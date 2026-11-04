"""引用库 · 查重合并（写库视图；批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/dupes/spec.md`。只读候选表 → 同屏并排对比
→ 合并方案带（58）→ 确认条（56）：dry-run 预览 → 页内二次确认（影响 + 库 sha）
→ 原子写（`--expect-db-sha256` + `--backup`）→ 本地提交（不 push）→ 可回退。

报告只写临时目录（`--out <mkdtemp>`）：默认 `--out` 会落到笔记仓，禁止。
"""
from __future__ import annotations

import asyncio
import csv
import io
import tempfile
import time
from datetime import datetime
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "dupes"
TITLE = "查重合并"
ROLE = "write"

SORTS = (("similarity", "相似度 高→低"), ("diff", "差异数 少→多"),
         ("year", "年份 新→旧"))
FILTERS = (("diff", "全部差异"), ("high", "仅高相似 ≥0.90"),
           ("same_year", "仅同年"), ("conflict", "有关键字段冲突"))
MERGE_TYPES = ("article", "book", "incollection", "thesis", "misc")
MERGE_FIELD_ORDER = ("title", "author", "editor", "translator", "year", "publisher",
                     "journal", "booktitle", "volume", "number", "pages", "doi", "url",
                     "isbn", "language", "note")


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_duplicates_csv(text: str) -> list[dict]:
    """把 `duplicates.csv` 归一成候选对；列名按几种常见写法兜底。"""
    text = (text or "").strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    pairs: list[dict] = []
    for row in reader:
        low = {(str(k or "")).strip().lower(): (str(v or "")).strip()
               for k, v in row.items()}
        a = (low.get("key_a") or low.get("a") or low.get("key1")
             or low.get("left") or low.get("record_a") or "")
        b = (low.get("key_b") or low.get("b") or low.get("key2")
             or low.get("right") or low.get("record_b") or "")
        if not a or not b:
            continue
        pairs.append({
            "a": a, "b": b,
            "similarity": _num(low.get("similarity") or low.get("score") or low.get("相似度")),
            "title": (low.get("title") or low.get("题名")
                      or low.get("title_a") or ""),
            "year": (low.get("year") or low.get("年份") or ""),
            "diff": _int(low.get("diff") or low.get("differences") or low.get("差异")
                         or low.get("diff_count")),
            "status": (low.get("status") or low.get("状态") or "候选"),
            "code": (low.get("code") or low.get("reason") or "near_duplicates"),
        })
    return pairs


def parse_pairs_json(payload: dict | None) -> list[dict]:
    """兼容引擎直接给 JSON 数组（`pairs` / `duplicates` / `items`）。"""
    payload = payload or {}
    for name in ("pairs", "duplicates", "items"):
        raw = payload.get(name)
        if isinstance(raw, list):
            out = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                a = str(item.get("a") or item.get("key_a") or "")
                b = str(item.get("b") or item.get("key_b") or "")
                if not a or not b:
                    continue
                out.append({"a": a, "b": b,
                            "similarity": _num(item.get("similarity")),
                            "title": str(item.get("title") or ""),
                            "year": str(item.get("year") or ""),
                            "diff": _int(item.get("diff")),
                            "status": str(item.get("status") or "候选"),
                            "code": str(item.get("code") or "near_duplicates")})
            return out
    return []


def record_from_payload(payload: dict | None) -> dict:
    payload = payload or {}
    hits = payload.get("hits")
    if isinstance(hits, list):
        for hit in hits:
            record = (hit or {}).get("record") if isinstance(hit, dict) else None
            if isinstance(record, dict) and record.get("key"):
                return record
    for name in ("record", "item"):
        record = payload.get(name)
        if isinstance(record, dict) and record.get("key"):
            return record
    return {}


def pair_field_names(left: dict, right: dict) -> list[str]:
    names: list[str] = []
    for record in (left, right):
        fields = (record or {}).get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for name in MERGE_FIELD_ORDER:
            if name in fields and name not in names:
                names.append(name)
        for name in sorted(fields):
            if name not in names:
                names.append(name)
    return names


class BibDupesView:
    """查重合并：候选表 / 并排对比（84+360+360+88）/ 方案带 58 / 确认条 56。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.pairs: list[dict] = []
        self.selected: int | None = None
        self.left: dict = {}
        self.right: dict = {}
        self.choices: dict[str, str] = {}
        self.merged_key = ""
        self.entry_type = "article"
        self.ignore_reason = ""
        self.query = ""
        self.sort_key = str(self._store_get("bib_dupes_sort", "similarity") or "similarity")
        self.filter_key = "diff"
        self.only_diff = True
        self.expanded = False
        self.payload: dict | None = None
        self.expect_sha = ""
        self.state = "empty"       # empty/loading/ready/running/failure/stale/success
        self.selection_state = ""  # loading/failure 用于并排区
        self.error = ""
        self.stderr = ""
        self.busy = False
        self.dirty_reason = ""
        self.confirm_layer = False
        self.report_at = ""
        self.report_dir = ""
        self.commit: dict | None = None
        self.result: dict | None = None
        self._root: ft.Container | None = None
        self._bottom_holder: ft.Container | None = None

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

    def _total_records(self) -> int:
        stats = getattr(self.page, "stats_data", None)
        if isinstance(stats, dict):
            return _int(stats.get("records") or stats.get("total"))
        return 0

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
        if not self.pairs and not self.busy:
            self._task(self._scan)

    def refresh_running(self) -> None:
        return

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    # ── 报告 / 候选 ─────────────────────────────────────────────────
    async def _scan(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.state = "loading"
        self.error = ""
        self._render()
        self.report_dir = tempfile.mkdtemp(prefix="bib-dupes-report-")
        res = await self._call(EB.bibdb_report_duplicates_call(out=self.report_dir))
        pairs: list[dict] = []
        if res.get("ok"):
            pairs = parse_pairs_json(res.get("json") or {})
            if not pairs:
                csv_path = Path(self.report_dir) / "duplicates.csv"
                if csv_path.exists():
                    try:
                        pairs = parse_duplicates_csv(csv_path.read_text(encoding="utf-8"))
                    except OSError:
                        pairs = []
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_dupes_scan", ok=False, code=res.get("code"))
            self._render()
            return
        self.pairs = pairs
        self.report_at = datetime.now().strftime("%H:%M")
        self.state = "ready" if pairs else "empty"
        self.selected = 0 if pairs else None
        self.payload = None
        self.expect_sha = ""
        self._mark("bib_dupes_scan", ok=True, pairs=len(pairs))
        self._render()
        if pairs:
            await self._select(0)

    def _filtered(self) -> list[dict]:
        rows = list(self.pairs)
        query = self.query.strip().lower()
        if query:
            rows = [row for row in rows
                    if query in row["a"].lower() or query in row["b"].lower()
                    or query in (row.get("title") or "").lower()]
        if self.filter_key == "high":
            rows = [row for row in rows if row["similarity"] >= 0.90]
        elif self.filter_key == "same_year":
            rows = [row for row in rows if str(row.get("year") or "")]
        elif self.filter_key == "conflict":
            rows = [row for row in rows if row["diff"] >= 2]
        if self.only_diff:
            rows = [row for row in rows if row["diff"] > 0]
        if self.sort_key == "similarity":
            rows.sort(key=lambda row: row["similarity"], reverse=True)
        elif self.sort_key == "diff":
            rows.sort(key=lambda row: row["diff"])
        else:
            rows.sort(key=lambda row: str(row.get("year") or ""), reverse=True)
        return rows

    def _current(self) -> dict:
        rows = self._filtered()
        if not rows or self.selected is None:
            return {}
        index = max(0, min(self.selected, len(rows) - 1))
        return rows[index]

    async def _select(self, index: int) -> None:
        rows = self._filtered()
        if not rows:
            return
        index = max(0, min(index, len(rows) - 1))
        self.selected = index
        pair = rows[index]
        self.selection_state = "loading"
        self.payload = None
        self.expect_sha = ""
        self.dirty_reason = ""
        self.confirm_layer = False
        self._store_set("bib_dupes_sel", f"{pair['a']}|{pair['b']}")
        self._render()
        left_res, right_res = await asyncio.gather(
            self._call(EB.bibdb_query_call(key=pair["a"], limit=10)),
            self._call(EB.bibdb_query_call(key=pair["b"], limit=10)))
        left = record_from_payload(left_res.get("json") or {})
        right = record_from_payload(right_res.get("json") or {})
        if not left_res.get("ok") or not right_res.get("ok") or not left or not right:
            self.selection_state = "failure"
            self.error = "读不到候选记录（可能已合并或改名）"
            self.left, self.right = {}, {}
            self._render()
            return
        self.left, self.right = left, right
        self.selection_state = "ready"
        self.merged_key = str(left.get("key") or pair["a"])
        self.entry_type = str(left.get("entry_type") or "article")
        self.choices = {}
        left_fields = left.get("fields") or {}
        right_fields = right.get("fields") or {}
        for name in pair_field_names(left, right):
            lv = str(left_fields.get(name) or "")
            rv = str(right_fields.get(name) or "")
            self.choices[name] = "A" if not rv or lv == rv else "A"
        self._mark("bib_dupes_select", a=pair["a"], b=pair["b"])
        self._render()

    def _select_index(self, index: int) -> None:
        self._task(lambda: self._select(index))

    def _set_sort(self, key: str) -> None:
        self.sort_key = key
        self._store_set("bib_dupes_sort", key)
        self._render()

    def _set_filter(self, key: str) -> None:
        self.filter_key = key
        self._render()

    def _toggle_only_diff(self, e=None) -> None:
        self.only_diff = not self.only_diff
        self._render()

    def _on_search(self, e) -> None:
        self.query = e.control.value or ""

    def _on_choice(self, name: str, side: str) -> None:
        if self.choices.get(name) == side:
            return
        self.choices[name] = side
        self._invalidate("采用值已改，需重新预览")
        self._touch_bottom()

    def _on_merged_key(self, e) -> None:
        self.merged_key = (e.control.value or "").strip()
        self._invalidate("合并 key 已改，需重新预览")

    def _on_type(self, e) -> None:
        self.entry_type = str(getattr(e, "data", None) or self.entry_type)
        self._invalidate("条目类型已改，需重新预览")

    # ── 预览 / 写入 ─────────────────────────────────────────────────
    def _is_stale(self) -> bool:
        return bool(self.dirty_reason) and self.payload is not None

    def _can_confirm(self) -> bool:
        return bool(self.payload) and bool(self.expect_sha) and not self._is_stale() \
            and not self.busy and self.state not in ("failure", "stale", "success")

    def _invalidate(self, reason: str) -> None:
        if self.payload is not None and not self.dirty_reason:
            self.dirty_reason = reason
        self.confirm_layer = False

    def _field_sources(self) -> dict:
        return {name: "A" if side != "B" else "B" for name, side in self.choices.items()}

    def _build_call(self, *, dry_run: bool, expect_sha: str = "") -> EB.EngineCall:
        pair = self._current()
        return EB.bibdb_merge_call(
            keys=[pair["a"], pair["b"]], new_key=self.merged_key,
            entry_type=self.entry_type, field_sources=self._field_sources(),
            dry_run=dry_run, expect_db_sha256=expect_sha,
            backup="" if dry_run else self._backup_path(), db=self._db())

    def _backup_path(self) -> str:
        db = self._db()
        folder = Path(db).parent / ".backup" if db else Path(tempfile.mkdtemp())
        return str(folder / f"merge-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")

    async def _preview(self) -> None:
        pair = self._current()
        if self.busy or not pair or not self.merged_key:
            if not self.merged_key:
                self._notify("先填合并后的 key")
            return
        if not self._valid_key(self.merged_key):
            self._notify("合并 key 只能用小写字母 / 数字 / _ : -，3–64 位")
            return
        self.busy = True
        self.state = "loading"
        self.error = ""
        self.confirm_layer = False
        self._render()
        res = await self._call(self._build_call(dry_run=True))
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_dupes_preview", ok=False, code=res.get("code"))
            self._render()
            return
        payload = res.get("json") or {}
        self.payload = payload
        self.expect_sha = str(payload.get("db_sha256_before") or "")
        self.dirty_reason = ""
        self.state = "ready"
        self._mark("bib_dupes_preview", ok=True, code=res.get("code"),
                   sha=_sha8(self.expect_sha))
        self._render()

    @staticmethod
    def _valid_key(key: str) -> bool:
        import re
        return bool(re.fullmatch(r"[a-z0-9_:-]{3,64}", key or ""))

    def _open_confirm(self, e=None) -> None:
        if not self._can_confirm():
            return
        self.confirm_layer = True
        self._render()

    def _cancel_confirm(self, e=None) -> None:
        self.confirm_layer = False
        self._notify("已取消，未写库")
        self._render()

    async def _write(self) -> None:
        if self.busy or not self._can_confirm():
            return
        pair = self._current()
        self.busy = True
        self.state = "running"
        self.confirm_layer = False
        self._render()
        res = await self._call(self._build_call(dry_run=False,
                                                expect_sha=self.expect_sha))
        if not res.get("ok"):
            if self._sha_mismatch(res):
                self.state = "stale"
                self.dirty_reason = "库 sha256 已变化，预览不再可信"
            else:
                self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self.busy = False
            self._mark("bib_dupes_write", ok=False, code=res.get("code"))
            self._render()
            return
        payload = res.get("json") or {}
        db = str(payload.get("db") or self._db())
        commit: dict = {}
        if db:
            try:
                commit = await asyncio.to_thread(
                    GIT.commit_db, db,
                    f"引用库：合并 {pair['a']} + {pair['b']} → {self.merged_key}")
            except Exception as exc:  # noqa: BLE001
                commit = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        self.state = "success"
        self.result = payload
        self.commit = commit if isinstance(commit, dict) else {}
        self._mark("bib_dupes_write", ok=True,
                   commit=_sha8(str((self.commit or {}).get("sha") or "")))
        self._notify("已合并 2 条 → 1 条（已提交，未推送）")
        await self._after_write_refresh()

    async def _after_write_refresh(self) -> None:
        self.pairs = [p for p in self.pairs
                      if not (self._current() and p["a"] == self._current()["a"]
                              and p["b"] == self._current()["b"])]
        self.selected = 0 if self.pairs else None
        self._render()
        if self.pairs:
            await self._select(0)

    def _sha_mismatch(self, res: dict) -> bool:
        text = f"{res.get('error') or ''} {res.get('stderr') or ''}"
        return res.get("code") == 2 or "sha256" in text

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return str(value).strip().splitlines()[0][:300]
        return "引擎没有返回原因。"

    def _mark_pair(self, e=None) -> None:
        pair = self._current()
        if not pair:
            return
        t = self.t
        page = self._flet()
        if page is None or not hasattr(page, "show_dialog"):
            return
        field = C.text_field(t, hint_text="至少 4 个字，写清为什么不是重复",
                             on_change=lambda e: setattr(self, "ignore_reason",
                                                         e.control.value or ""))

        def _close(e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:  # noqa: BLE001
                pass

        def _ok(e=None) -> None:
            _close()
            self._task(self._ignore)

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("标记这对不是重复？", t, role="dialog-title"),
            content=ft.Container(width=t.px(380), content=ft.Column(spacing=t.sp("1"),
                                                                   controls=[
                C.text("只写旁车记录，不改库内条目。", t,
                       "small", t.palette.text_muted),
                field])),
            actions=[C.text_button("取消", _close, t),
                     C.secondary_button("标记非重复", _ok, t)])
        page.show_dialog(dialog)

    async def _ignore(self) -> None:
        pair = self._current()
        reason = self.ignore_reason.strip()
        if not pair:
            return
        if len(reason) < 4:
            self._notify("忽略原因至少 4 个字")
            return
        res = await self._call(EB.bibdb_ack_duplicates_call(
            code=pair.get("code") or "near_duplicates",
            keys=(pair["a"], pair["b"]), reason=reason, dry_run=False))
        if res.get("ok"):
            self._notify("已标记非重复（库未改动）")
            self.pairs = [p for p in self.pairs if p is not pair]
            self.selected = 0 if self.pairs else None
            self._render()
        else:
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self.state = "failure"
            self._render()

    def _revert_confirm(self, e=None) -> None:
        if not (self.commit or {}).get("ok"):
            self._notify("没有可回退的本次提交")
            return
        dialog = S.danger_confirm(
            self._flet(), self.t, title="回退这次合并？",
            message=("恢复到本次合并前的版本，再生成一条回退提交；只动 bibliography.jsonl，不推送。"),
            impact="1 个文件 · 1 条回退提交",
            confirm_label="回退这次提交", on_confirm=lambda e: self._task(self._revert))
        if dialog is not None:
            self._render()

    async def _revert(self) -> None:
        db = self._db()
        try:
            result = await asyncio.to_thread(GIT.rollback, db) if db else {"ok": False}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("ok"):
            self.state = "ready"
            self.payload = None
            self.commit = None
            self._notify("已回退本次合并（生成了一条新提交，未推送）")
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
        body = ft.Column(spacing=0, expand=True, controls=[
            self._filter_row(),
            self._table(),
            ft.Container(height=t.px(8)),
            self._diff_section(),
            self._plan_band(),
            self._bottom_holder,
        ])
        return S.page_shell(
            t, title=TITLE,
            meta="选对候选 → 并排对比 → 预览写入",
            role_chip=S.role_chip(t, "write"),
            action=C.secondary_button("重新生成报告", lambda e: self._task(self._scan), t),
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

    def _filter_row(self) -> ft.Control:
        t = self.t
        sort_label = dict(SORTS).get(self.sort_key, SORTS[0][1])
        filter_label = dict(FILTERS).get(self.filter_key, FILTERS[0][1])
        search = C.search_field(t, value=self.query, hint_text="搜索 key / 题名",
                                on_change=self._on_search, width=t.px(300))
        count = (f"{len(self._filtered())} 对 · 报告 {self.report_at}"
                 if self.report_at else "尚未生成报告")
        return S.filter_row(
            t, search=search, on_search=self._on_search, count=count,
            controls=[
                C.chip(sort_label, t, active=False,
                       on_click=lambda e: self._cycle_sort()),
                C.chip(filter_label, t, active=self.filter_key != "diff",
                       on_click=lambda e: self._cycle_filter()),
                ft.Row(spacing=t.sp("half"), controls=[
                    ft.Checkbox(value=self.only_diff,
                                on_change=self._toggle_only_diff),
                    C.text("只看有差异", t, "caption", t.palette.text_muted),
                ]),
            ], page=self._flet())

    def _cycle_sort(self) -> None:
        keys = [key for key, _label in SORTS]
        index = (keys.index(self.sort_key) + 1) % len(keys) if self.sort_key in keys else 0
        self._set_sort(keys[index])

    def _cycle_filter(self) -> None:
        keys = [key for key, _label in FILTERS]
        index = (keys.index(self.filter_key) + 1) % len(keys) \
            if self.filter_key in keys else 0
        self._set_filter(keys[index])

    def _table(self) -> ft.Control:
        t = self.t
        rows = self._filtered()
        controls: list[ft.Control] = [S.table_header_row(
            t, ["相似度", "记录 A", "记录 B", "题名", "年", "差异", "状态"],
            weights=[0.8, 2.0, 2.0, 3.4, 0.5, 0.7, 1.0],
            mono=(1, 2), align=["right", "left", "left", "left", "right", "right", "left"])]
        if self.state == "loading":
            controls.append(S.empty_row(t, "运行中"))
        elif not rows:
            controls.append(S.empty_row(
                t, "没有匹配的候选对" if self.pairs else "没有待处理的候选对"))
        for index, pair in enumerate(rows[:20]):
            controls.append(S.table_row(
                t, [f"{pair['similarity']:.2f}", pair["a"], pair["b"],
                    pair.get("title") or "—", str(pair.get("year") or "—"),
                    str(pair["diff"]), pair.get("status") or "候选"],
                weights=[0.8, 2.0, 2.0, 3.4, 0.5, 0.7, 1.0],
                mono=(1, 2), align=["right", "left", "left", "left", "right",
                                    "right", "left"],
                selected=index == self.selected,
                on_click=lambda e, i=index: self._select_index(i)))
        # ≤20 行候选：不设固定高、不内滚，滚轮交给页体。
        box = ft.Container(content=ft.Column(spacing=0, controls=controls))
        box._bib_shrink = True
        return box

    def _diff_section(self) -> ft.Control:
        t = self.t
        pair = self._current()
        if not pair:
            return ft.Container(height=t.px(40), alignment=ft.Alignment.CENTER_LEFT,
                                content=C.text("选一对候选后，这里并排显示两条记录。", t,
                                               "caption", t.palette.text_faint))
        if self.selection_state == "loading":
            return S.state_banner(t, "loading", message="运行中")
        if self.selection_state == "failure":
            return S.state_banner(t, "failure", message=self.error or "读不到候选记录")
        names = pair_field_names(self.left, self.right)
        diff_count = self._diff_names()
        title_row = ft.Row(spacing=t.sp("1"), controls=[
            C.text(f"并排对比 · 第 {self.selected + 1 if self.selected is not None else 1} 对",
                   t, role="body-strong"),
            ft.Container(expand=True),
            C.text(f"相似度 {pair['similarity']:.2f} · 差异 {diff_count} / {len(names)} 字段",
                   t, "caption", t.palette.accent),
        ])
        if self.narrow:
            return ft.Column(spacing=t.sp("half"), controls=[
                title_row,
                ft.Container(height=t.px(40), alignment=ft.Alignment.CENTER_LEFT,
                             content=ft.Row(spacing=t.sp("1"), controls=[
                                 C.text(f"{diff_count} 个字段有差异 · 展开对比", t, "small",
                                        t.palette.text_muted),
                                 ft.Container(expand=True),
                                 C.text_button("展开", lambda e: self._open_sheet(), t)]))
            ])
        return ft.Column(spacing=0, controls=[
            title_row,
            ft.Container(height=t.px(6)),
            self._side_by_side(names, narrow=False),
            self._collapse_row(names, diff_count),
        ])

    def _diff_names(self) -> int:
        left_fields = (self.left or {}).get("fields") or {}
        right_fields = (self.right or {}).get("fields") or {}
        names = set(left_fields) | set(right_fields)
        return sum(1 for name in names
                   if str(left_fields.get(name) or "") != str(right_fields.get(name) or ""))

    def _side_by_side(self, names: list[str], *, narrow: bool) -> ft.Control:
        t = self.t
        p = t.palette
        left_fields = (self.left or {}).get("fields") or {}
        right_fields = (self.right or {}).get("fields") or {}
        visible = names if self.expanded else self._different_first(names)
        header = S.table_header_row(
            t, ["字段", f"A：{self.left.get('key') or '—'}",
                f"B：{self.right.get('key') or '—'}", "采用"],
            weights=[0.24, 1.0, 1.0, 0.24], mono=(1, 2))
        rows: list[ft.Control] = [header]
        for name in visible:
            lv = str(left_fields.get(name) or "—")
            rv = str(right_fields.get(name) or "—")
            side = self.choices.get(name, "A")
            rows.append(ft.Container(
                height=t.px(32),
                content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                    ft.Container(width=t.px(84), content=C.text(
                        name, t, role="mono", color=p.text_muted, max_lines=1)),
                    self._value_cell(lv, chosen=side == "A"),
                    self._value_cell(rv, chosen=side == "B"),
                    ft.Container(width=t.px(88), content=ft.Row(
                        spacing=t.sp("half"), controls=[
                            self._choice_chip("A", name, side == "A"),
                            self._choice_chip("B", name, side == "B"),
                        ])),
                ])))
        box = ft.Container(content=ft.Column(spacing=0, controls=rows))
        box._bib_shrink = True
        return box

    def _value_cell(self, value: str, *, chosen: bool) -> ft.Control:
        t = self.t
        p = t.palette
        return ft.Container(
            expand=True, height=t.px(32),
            bgcolor=p.accent_soft if chosen else None,
            padding=ft.Padding(left=t.px(6), top=0, right=t.px(6), bottom=0),
            alignment=ft.Alignment.CENTER_LEFT,
            content=C.text(value, t, "small",
                           p.on_accent_soft if chosen else p.text_faint,
                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))

    def _choice_chip(self, label: str, name: str, active: bool) -> ft.Control:
        t = self.t
        p = t.palette
        return ft.Container(
            width=t.px(24), height=t.px(20), alignment=ft.Alignment.CENTER,
            border=C.box(t, p.accent if active else p.border),
            border_radius=t.radius("mark"),
            bgcolor=p.accent_soft if active else None,
            content=C.text(label, t, "caption",
                           p.on_accent_soft if active else p.text_faint),
            on_click=lambda e: self._on_choice(name, label))

    def _different_first(self, names: list[str]) -> list[str]:
        return names

    def _collapse_row(self, names: list[str], diff_count: int) -> ft.Control:
        t = self.t
        same = max(0, len(names) - diff_count)
        return ft.Container(
            height=t.px(30), content=ft.Row(spacing=t.sp("1"), controls=[
                C.text(f"相同字段 {same} 个已折叠", t, "caption", t.palette.text_faint),
                C.text_button("全部展开" if not self.expanded else "只看差异",
                              lambda e: self._toggle_expand(), t),
                ft.Container(expand=True),
                C.text(f"共 {len(names)} 字段 · 差异 {diff_count}", t, "caption",
                       t.palette.text_faint),
            ]))

    def _toggle_expand(self) -> None:
        self.expanded = not self.expanded
        self._render()

    def _open_sheet(self) -> None:
        t = self.t
        page = self._flet()
        if page is None or not hasattr(page, "show_dialog"):
            self.expanded = True
            self._render()
            return
        try:
            sheet = ft.BottomSheet(
                content=ft.Container(
                    height=t.px(440),
                    padding=ft.Padding(left=t.px(16), top=t.px(12), right=t.px(16),
                                       bottom=t.px(12)),
                    content=ft.Column(spacing=t.sp("1"), controls=[
                        C.text("并排对比", t, role="body-strong"),
                        self._side_by_side(pair_field_names(self.left, self.right),
                                           narrow=True),
                    ])))
            page.show_dialog(sheet)
        except Exception:  # noqa: BLE001 - 平台不支持 BottomSheet 时退回行内展开
            self.expanded = True
            self._render()

    def _plan_band(self) -> ft.Control:
        t = self.t
        p = t.palette
        pair = self._current()
        total = self._total_records()
        names = pair_field_names(self.left, self.right) if pair else []
        # P1-8：未选对不编造「2 条→1 条 / 6 字段」；所有数字都由当前 pair 现算。
        if pair:
            impact = (f"影响 2 条 → 1 条 · 库 {total:,} → {total - 1:,}" if total
                      else "影响 2 条 → 1 条 · 预览（不写库）")
            field_note = f"{len(names)} 字段取值"
        else:
            impact = "影响 — · 先选一对候选"
            field_note = "字段取值 —"
        key_field = C.text_field(
            t, value=self.merged_key, hint_text="合并后 key", width=t.px(196),
            on_change=self._on_merged_key, disabled=not pair)
        type_select = C.select(t, value=self.entry_type,
                               options=[(name, name) for name in MERGE_TYPES],
                               on_select=self._on_type, width=t.px(100),
                               disabled=not pair)
        mark_btn = C.text_button("标记非重复…", self._mark_pair, t)
        mark_btn.disabled = not pair
        return ft.Container(
            height=t.px(58), bgcolor=p.surface_2,
            border=ft.Border(top=ft.BorderSide(0.6, p.border),
                             bottom=ft.BorderSide(0.6, p.border)),
            padding=ft.Padding(left=t.px(12), top=t.px(4), right=t.px(12), bottom=t.px(4)),
            content=ft.Column(spacing=0, controls=[
                ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           C.text("合并后", t, "caption", p.text_faint),
                           key_field, type_select,
                           ft.Container(expand=True),
                           C.text(impact, t, "small", p.text_muted),
                           mark_btn,
                       ]),
                C.text(f"{field_note} · 旧 key 记别名 · 预览（不写库）", t, "caption",
                       p.text_faint, max_lines=1),
            ]))

    def _bottom(self) -> ft.Control:
        t = self.t
        if self.state == "success":
            commit = self.commit or {}
            sha = _sha8(str(commit.get("sha") or ""))
            pair = self._current()
            label = self.merged_key or (pair.get("a") if pair else "")
            return S.result_banner(
                t, title=f"已合并 2 条 → 1 条（{label}）",
                detail=f"已提交 {sha} · 未推送" if sha else "已合并；未生成提交",
                on_review=lambda e: self._goto("health"),
                on_revert=self._revert_confirm if commit.get("ok") else None)
        stale = self._is_stale() or self.state == "stale"
        pair = self._current()
        consequence = (f"将合并 {pair['a']} + {pair['b']} → {self.merged_key}"
                       if pair else "先选一对候选")
        detail = "只写 bibliography.jsonl · 不推送"
        if stale:
            detail = f"预览已过期：{self.dirty_reason}，先重新预览"
        bar = S.confirm_bar(
            t, consequence=consequence, detail=detail, sha=_sha8(self.expect_sha),
            on_confirm=self._open_confirm, confirm_disabled=not self._can_confirm(),
            confirm_label="运行中" if self.state == "running" else "确认写入",
            on_repreview=lambda e: self._task(self._preview),
            stale=stale and self.payload is not None)
        # 重新预览只在已有陈旧预览时出现。
        if not (stale and self.payload is not None):
            bar._bib_repreview_btn.visible = False
        return bar

    def _goto(self, view: str) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav(view)
            return
        self._store_set("bib_view", view)
        self._notify("暂不可跳转")
