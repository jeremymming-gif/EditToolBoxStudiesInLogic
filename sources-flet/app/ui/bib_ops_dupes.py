"""引用库 · 查重合并视图（批 109 W1b R2；从 bib_ops 拆出的 submodule）。

候选对来自 `report-duplicates --json --out <临时目录>`：默认输出目录是库目录 =
笔记仓，所以本模块只写 `tempfile.mkdtemp()` 出来的临时目录，读完 `duplicates.csv`
立即删掉。合并写库走 `merge-records`：先 dry-run 预览，再二次确认 + sha 闸真写，
写成功只提交 `bib_db/bibliography.jsonl` 一条（不推送）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import components as C
from ui.bib_ops import (DUPE_SHOW, _OpsView, _text, duplicate_pair_lines,
                        duplicate_pair_title, duplicate_rows, field_label,
                        merge_preview_lines, ordered_fields, record_from_payload)
from ui.bib_write import explain_result


# ── R2 查重合并 ─────────────────────────────────────────────────────────
class BibDuplicatesView(_OpsView):
    """查重合并：报告候选对 → 对比两条 / 选字段来源 → 预览 → 合并 + 1 条提交。"""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.rows: list[dict] = []
        self.report: dict = {}
        self.active_pair = ""
        self.pair: dict = {}
        self.left: dict = {}
        self.right: dict = {}
        self.field_choice: dict[str, str] = {}
        self.new_key = ""
        self.entry_type = ""
        self.preview: dict | None = None
        self.pending: dict | None = None

    async def ensure_loaded(self, force: bool = False) -> None:
        if self.loading or (self.loaded and not force):
            return
        self.loading = True
        try:
            await self._load()
        finally:
            self.loading = False
            self.loaded = True
        self._refresh()

    async def _load(self) -> None:
        self.busy, self.error = True, ""
        self._refresh()
        tmp = tempfile.mkdtemp(prefix="b109-dupes-")
        try:
            result = await self._read(EB.bibdb_report_duplicates_call(out=tmp))
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.rows = []
                self.error = explain_result(result)
                return
            text, path = "", ""
            for item in payload.get("reports") or []:
                candidate = _text((item or {}).get("path"))
                if candidate.lower().endswith(".csv"):
                    path = candidate
                    break
            path = path or os.path.join(tmp, "duplicates.csv")
            try:
                text = Path(path).read_text(encoding="utf-8-sig")
            except OSError as exc:
                self.error = f"读不到查重 CSV：{exc}"
            self.rows = duplicate_rows(text, limit=200)
            self.report = payload
            self.page.mark("bib_dupes_report", pairs=len(self.rows))
        finally:
            self.busy = False
            shutil.rmtree(tmp, ignore_errors=True)

    def _ask_open_pair(self, row: dict) -> None:
        self._run(self._open_pair(row))

    async def _open_pair(self, row: dict) -> None:
        self.busy, self.error = True, ""
        self.active_pair = _text(row.get("pair_no"))
        self._refresh()
        records: list[dict] = []
        for key in (row.get("key_a"), row.get("key_b")):
            result = await self._read(
                EB.bibdb_query_basic_call("key", _text(key), bibtex=False))
            records.append(record_from_payload(result.get("json") or {}))
        self.busy = False
        if not all(records):
            self.error = "读不到候选记录（可能已被合并或改名）：刷新候选重试。"
            self.active_pair, self.pair = "", {}
            self._refresh()
            return
        self.left, self.right = records[0], records[1]
        self.pair = dict(row)
        self.field_choice = {}
        self.new_key = _text(row.get("key_a"))
        self.entry_type = (_text(row.get("entry_type_a")) or _text(row.get("entry_type_b")))
        self.preview, self.pending = None, None
        self._refresh()

    def _left_key(self) -> str:
        return _text(self.left.get("key"))

    def _right_key(self) -> str:
        return _text(self.right.get("key"))

    def _field_source(self, field: str) -> str:
        side = self.field_choice.get(field) or "left"
        return self._right_key() if side == "right" else self._left_key()

    def _field_sources(self) -> dict[str, str]:
        names = {name for name, _ in ordered_fields(self.left)}
        names |= {name for name, _ in ordered_fields(self.right)}
        return {name: self._field_source(name) for name in sorted(names)}

    def _ask_preview_merge(self) -> None:
        self._run(self._preview_merge())

    async def _preview_merge(self) -> None:
        keys = [self._left_key(), self._right_key()]
        if not all(keys):
            self.error = "先展开一对候选记录。"
            self._refresh()
            return
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_merge_call(
            keys=keys, new_key=str(self.new_key or "").strip(),
            entry_type=str(self.entry_type or "").strip(),
            field_sources=self._field_sources(), dry_run=True))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.preview, self.pending = None, None
            self.error = explain_result(result)
            self._refresh()
            return
        self.preview = payload
        self.pending = {"keys": keys, "new_key": str(self.new_key or "").strip(),
                        "entry_type": str(self.entry_type or "").strip(),
                        "field_sources": self._field_sources(), "dry_run": False,
                        "expect_db_sha256": str(payload.get("db_sha256_before") or "")}
        self.page.mark("bib_merge_preview", keys=keys, dry_run=True)
        self._refresh()

    def _ask_write_merge(self) -> None:
        if not self.pending:
            return
        self._confirm(
            title="合并这两条记录？",
            message=("确认后真写：两条记录合并成一条，旧 key 记进别名，写后只提交 "
                     "bib_db/bibliography.jsonl 一条（不推送）。\n"
                     "回退方式：到「设置」页用「回退到上一个提交…」（只提示，不自动回退）。"),
            confirm_label="确认合并",
            on_confirm=lambda e: self._run(self._write_merge()))

    async def _write_merge(self) -> None:
        pending = dict(self.pending or {})
        if not pending:
            return
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_merge_call(**pending))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.error = explain_result(result)
            self._refresh()
            return
        self.preview, self.pending = None, None
        label = "查重合并 " + (_text(payload.get("new_key")) or "")
        await self._commit_write(payload, label=label.strip(), count=1)
        self.page.stats_data = None
        self._toast(ops_notice("合并已写入", payload, self.commit))
        self.active_pair, self.pair, self.left, self.right = "", {}, {}, {}
        await self._load()

    def _ask_ack(self, row: dict) -> None:
        self._confirm(
            title="标记「不是重复」？",
            message=("只会写库目录下的 sidecar health-ignore.json（让 lint 不再报这对），"
                     "库文件一个字节都不动，也不会自动提交。\n"
                     "之后想恢复报出，需要手动删该 sidecar 条目。"),
            confirm_label="确认标记",
            on_confirm=lambda e: self._run(self._ack(row)))

    async def _ack(self, row: dict) -> None:
        keys = [_text(row.get("key_a")), _text(row.get("key_b"))]
        code = "near_duplicates" if _text(row.get("similarity")) else "duplicate_content"
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_ack_duplicates_call(
            code=code, keys=keys, reason="Flet 查重页人工确认不是重复", dry_run=False))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.error = explain_result(result)
            self._refresh()
            return
        self.page.mark("bib_dupes_ack", keys=keys, added=bool(payload.get("added")))
        self._toast("已记入 health-ignore.json" if payload.get("written")
                    else "这条已在 health-ignore.json 里（库文件没有改动）", "success")
        await self._load()

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.t
        if not self.loaded and not self.loading:
            self._run(self.ensure_loaded())
        controls: list = [self._report_card()]
        for row in self.rows[:DUPE_SHOW]:
            controls.append(self._pair_card(row))
        if self.pair:
            controls.append(self._merge_card())
        if self.preview:
            controls.append(self._merge_preview_card())
        bar = self._error_bar()
        if bar is not None:
            controls.append(bar)
        bar = self._notice_bar()
        if bar is not None:
            controls.append(bar)
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _report_card(self) -> ft.Control:
        t = self.t
        pairs = len(self.rows)
        note = (f"候选 {pairs} 对" if self.loaded else "正在读取候选…")
        controls: list = [C.section_title(
            "查重合并", t, note="report-duplicates --json --out <临时目录>（不写笔记仓）"),
            C.text("同作品不同 key 的候选对（题名/字段相似度，引擎现算）。报告只写临时目录，"
                   "读完即删；合并写库后只提交库文件一条。", t, "small", t.palette.text_muted),
            ft.Row(spacing=t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                       C.secondary_button("重新扫描", lambda e: self._run(self.ensure_loaded(True)),
                                          t, icon="REFRESH", disabled=self.busy),
                       C.text(note, t, "small", t.palette.text_faint),
                       C.text("处理中…" if self.busy else "", t, "small", t.palette.text_faint),
                   ])]
        if self.loaded and not pairs:
            controls.append(C.text("没有待处理的候选对。", t, "small", t.palette.text_muted))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _pair_card(self, row: dict) -> ft.Control:
        t = self.t
        active = _text(row.get("pair_no")) == self.active_pair
        controls: list = [C.text(duplicate_pair_title(row), t,
                                  role="item-selected" if active else "body",
                                  color=t.palette.text)]
        controls += [C.text(line, t, "small", t.palette.text_muted)
                     for line in duplicate_pair_lines(row)]
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("展开对比 / 合并…", lambda e, r=row: self._ask_open_pair(r), t,
                               disabled=self.busy),
            C.secondary_button("标记不是重复…", lambda e, r=row: self._ask_ack(r), t,
                               disabled=self.busy),
        ]))
        card = C.card(ft.Column(spacing=t.sp("half"), controls=controls), t)
        return card

    def _merge_card(self) -> ft.Control:
        t = self.t
        left_key, right_key = self._left_key(), self._right_key()
        names: list[str] = []
        for record in (self.left, self.right):
            for name, _ in ordered_fields(record):
                if name not in names:
                    names.append(name)
        rows = []
        for name in names:
            left_value = _text((self.left.get("fields") or {}).get(name))
            right_value = _text((self.right.get("fields") or {}).get(name))
            choice = ft.Dropdown(
                value=self.field_choice.get(name, "left"), dense=True, width=t.px(260),
                text_size=t.font("body"), options=[
                    ft.DropdownOption(key="left", text=f"{left_key}：{left_value or '（空）'}"),
                    ft.DropdownOption(key="right", text=f"{right_key}：{right_value or '（空）'}"),
                ],
                on_change=lambda e, n=name: self.field_choice.__setitem__(
                    n, e.control.value or "left"))
            rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[C.text(field_label(name), t, "small",
                                                t.palette.text_muted), choice]))
        top = ft.Row(spacing=t.sp("1"), wrap=True,
                     vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.TextField(label="合并后 key", value=self.new_key, dense=True, width=t.px(200),
                         text_size=t.font("body"), border_color=t.palette.border,
                         focused_border_color=t.palette.accent,
                         on_change=lambda e: setattr(self, "new_key", e.control.value or "")),
            ft.TextField(label="条目类型", value=self.entry_type, dense=True, width=t.px(150),
                         text_size=t.font("body"), border_color=t.palette.border,
                         focused_border_color=t.palette.accent,
                         on_change=lambda e: setattr(self, "entry_type", e.control.value or "")),
            C.primary_button("预览合并（dry-run）", lambda e: self._ask_preview_merge(), t,
                             icon="VISIBILITY", disabled=self.busy),
        ])
        return C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title(f"对比 · {duplicate_pair_title(self.pair)}", t,
                            note="逐字段选保留哪一条的值（默认左）"),
            top,
            *(rows[:40]),
        ]), t)

    def _merge_preview_card(self) -> ft.Control:
        t = self.t
        payload = self.preview or {}
        controls: list = [C.section_title("合并预览（未写库）", t,
                                          note="库 sha256 " + GIT.short_sha(
                                              _text(payload.get("db_sha256_before")), 8))]
        controls += [C.text(line, t, "small", t.palette.text)
                     for line in merge_preview_lines(payload)]
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.primary_button("确认合并并提交", lambda e: self._ask_write_merge(), t,
                             icon="SAVE", disabled=self.busy),
            C.secondary_button("放弃预览", lambda e: self._clear_preview(), t),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _clear_preview(self) -> None:
        self.preview, self.pending = None, None
        self._refresh()
