"""引用库 · 库健康（批 115 夜间重设计 BPAGES-A，VIEW_KEY="health"）。

只读体检页：``lint --json --with-records`` 的 warning_items 一行 = 一个规则类。
两个数字绝不混：``counts.warnings`` 是**警告句数**（76），``warning_items`` 是
**规则类数**（12）；冲突类的记录数用 ``pending_records``（135），提示里另给
``pending_total``（157 处差异）。

确认「不是重复」只写 ``<库目录>/health-ignore.json`` sidecar，不改库本体、
不提交；失败/过期时禁用所有写入。
"""

from __future__ import annotations

import asyncio

import flet as ft

from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C
from ui.tokens import Tokens

VIEW_KEY = "health"
TITLE = "库健康"
ROLE = "readonly"

ACK_CODES = ("near_duplicates", "duplicate_content")
RULE_LABELS = {
    "conflict_pending": "冲突待裁决",
    "near_duplicates": "近似重复",
    "duplicate_content": "内容重复",
    "missing_title": "缺题名",
    "missing_author": "缺作者",
    "missing_year": "缺年份",
    "bad_doi": "DOI 格式",
    "unknown_entry_type": "未知条目类型",
    "alias_duplicate": "别名重复",
    "alias_self": "别名指向自身",
    "alias_ambiguous": "别名歧义",
    "alias_conflict": "别名冲突",
    "unverified": "未核实",
    "needs_inversion": "姓名未倒装",
    "health_ignore": "忽略清单损坏",
}
WARN_CODES = {"conflict_pending", "near_duplicates", "duplicate_content",
              "missing_title", "missing_author", "missing_year", "bad_doi",
              "unknown_entry_type", "alias_duplicate", "alias_self",
              "alias_ambiguous", "alias_conflict", "health_ignore"}
PAGE_SIZE = 9
# 严重度筛选固定宽：5 字「全部严重度」在 13 px 正文下约 65 px，加左右内边距 8/8、
# 箭头 14 与行间距 6，width=100 时可用文字宽只剩约 52 px → 省略成「全部严…」
# （批 116 CLIP-A P1）。124 是刚好放下 5 字的临界，132 再留 8 px 余量；
# 1160 与 1000 两种窗口宽度都实测整字显示（evidence/b116-impl/clip-fix-a）。
SEVERITY_FILTER_W = 132


def rule_label(code: str) -> str:
    return RULE_LABELS.get(str(code or ""), str(code or "（未知规则）"))


def severity_of(code: str) -> str:
    if code == "needs_inversion" or code == "unverified":
        return "提示"
    return "警告"


def count_text(item: dict) -> str:
    code = str(item.get("code") or "")
    if code == "conflict_pending":
        return f"{int(item.get('pending_records') or 0)} 条"
    if code == "near_duplicates":
        return f"{int(item.get('count') or 0)} 对"
    if code == "duplicate_content":
        return f"{int(item.get('count') or 0)} 组"
    return f"{int(item.get('count') or 0)} 条"


def count_hint(item: dict) -> str:
    code = str(item.get("code") or "")
    if code == "conflict_pending":
        return f"{int(item.get('pending_total') or 0)} 处差异；去待处理"
    if code == "near_duplicates":
        return "相似；可标为不重复"
    if code == "duplicate_content":
        return "内容相同；可整组确认"
    return str(item.get("action") or "")


def sample_text(item: dict) -> str:
    sample = item.get("keys_sample") or []
    records = item.get("records") or []
    values = sample or records[:3]
    return "、".join(str(value) for value in values[:3]) or "—"


def record_keys(record: str) -> list[str]:
    raw = str(record or "")
    for sep in ("≈", " ≈ ", "|", ","):
        if sep in raw:
            return [part.strip() for part in raw.split(sep) if part.strip()]
    return [raw.strip()] if raw.strip() else []


def _group_items(items: list[dict]) -> list[dict]:
    """Merge per-detail warning items into one row per rule code.

    The engine emits one warning_item per detail (e.g. every ambiguous alias
    pair), while the page contract is one row per rule class.  Counts of
    conflicts/duplicates are maxima (the same records are reported in each
    detail), other rule counts add up; records are concatenated and deduped.
    """
    grouped: dict[str, dict] = {}
    for raw in items:
        code = str(raw.get("code") or "")
        group = grouped.get(code)
        if group is None:
            group = dict(raw)
            group["count"] = 0
            group["records"] = []
            group["_records_seen"] = set()
            grouped[code] = group
        count = raw.get("count")
        if isinstance(count, (int, float)):
            group["count"] += int(count)
        for key in ("pending_records", "pending_total", "resolved_records", "resolved_total"):
            value = raw.get(key)
            if isinstance(value, (int, float)):
                group[key] = max(int(group.get(key) or 0), int(value))
        for record in raw.get("records") or []:
            if record in group["_records_seen"]:
                continue
            group["_records_seen"].add(record)
            if len(group["records"]) < 100:
                group["records"].append(record)
    for group in grouped.values():
        group.pop("_records_seen", None)
        if code_is_pairwise(str(group.get("code") or "")):
            group["count"] = max(1, int(group.get("count") or 0) // 1)
    return list(grouped.values())


def code_is_pairwise(code: str) -> bool:
    return code in ("near_duplicates", "duplicate_content", "conflict_pending")


class HealthView:
    def __init__(self, page) -> None:
        self.page = page
        self.payload: dict = {}
        self.items: list[dict] = []
        self.rows: list[dict] = []
        self.counts: dict = {}
        self.db_sha = ""
        self.loaded = False
        self.loading = False
        self.running = False
        self.error = ""
        self.success = ""
        self.cancelled = False
        self.stale = False
        self.text = ""
        self.rule_filter = ""
        self.severity_filter = ""
        self.only_ackable = False
        self.expanded: set[str] = set()
        self.selected: set[str] = set()
        self.staged = False
        self.limit = PAGE_SIZE
        self.last_check = ""
        self.report_open = ""
        self.report_time = ""
        self._task_running = False
        self._build_width = 0.0

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self) -> Tokens:
        return self.page.t

    def _db(self) -> str:
        return str((getattr(self.page, "stats_data", None) or {}).get("db") or "")

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _refresh(self) -> None:
        render = getattr(self.page, "_render", None)
        if callable(render):
            render()

    def _mark(self, event: str, **kwargs) -> None:
        marker = getattr(self.page, "mark", None)
        if callable(marker):
            try:
                marker(event, **kwargs)
            except Exception:  # noqa: BLE001
                pass

    def _schedule(self, runner) -> None:
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        if callable(run):
            run(runner)
        else:
            asyncio.run(runner())

    def activate(self) -> None:
        if not self.loaded and not self.loading:
            self._schedule(self._load)

    def refresh_running(self) -> None:
        return None

    def primary_action(self):
        return self._check_button()

    # ── 数据 ────────────────────────────────────────────────────────────
    async def _load(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.error = ""
        self._refresh()
        try:
            result = await asyncio.to_thread(
                EB.read_json_command,
                EB.bibdb_lint_call(with_records=True, db=self._db()),
                timeout=60.0, notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.loading = False
        self._task_running = False
        # lint 有警告时退出码可能为 0/1；只要 JSON 完整就照常展示
        data = result.get("json") or {}
        if not data:
            self.error = f"体检失败：{_first_line(result.get('error'))}"
            self._refresh()
            return
        self.payload = data
        self.counts = data.get("counts") or {}
        self.items = _group_items(data.get("warning_items") or [])
        self.db_sha = str((getattr(self.page, "stats_data", None) or {}).get("sha256") or "")
        self.loaded = True
        self.last_check = _now()
        self._apply()
        self._mark("bib_health_load", warnings=self.counts.get("warnings"),
                   classes=len(self.items))
        self._refresh()

    def check(self) -> None:
        self._schedule(self._load)

    def _apply(self) -> None:
        rows = []
        for item in self.items:
            code = str(item.get("code") or "")
            if self.rule_filter and code != self.rule_filter:
                continue
            if self.severity_filter and severity_of(code) != self.severity_filter:
                continue
            if self.only_ackable and code not in ACK_CODES:
                continue
            if self.text:
                hay = " ".join([code, rule_label(code), sample_text(item),
                                str(item.get("detail") or "")]).lower()
                if self.text.lower() not in hay:
                    continue
            rows.append(item)
        rows.sort(key=lambda item: (0 if severity_of(str(item.get("code"))) == "警告" else 1,
                                    -int(item.get("count") or 0)))
        self.rows = rows[: self.limit]

    def _on_text(self, e) -> None:
        self.text = str(getattr(e.control, "value", "") or "")
        self._apply()
        self._refresh()

    def _on_rule(self, e) -> None:
        self.rule_filter = str(getattr(e, "data", "") or "")
        self.rule_filter = "" if self.rule_filter in ("", "全部") else self.rule_filter
        self._apply()
        self._refresh()

    def _on_severity(self, e) -> None:
        self.severity_filter = str(getattr(e, "data", "") or "")
        self.severity_filter = "" if self.severity_filter in ("", "全部") else self.severity_filter
        self._apply()
        self._refresh()

    def _on_only_ackable(self, e) -> None:
        self.only_ackable = bool(getattr(e.control, "value", False))
        self._apply()
        self._refresh()

    def load_more(self) -> None:
        self.limit += PAGE_SIZE
        self._apply()
        self._refresh()

    # ── 展开/选择 ───────────────────────────────────────────────────────
    def _toggle_expand(self, code: str) -> None:
        if code in self.expanded:
            self.expanded.discard(code)
        else:
            self.expanded.add(code)
        self._refresh()

    def _toggle_row(self, code: str, value: bool) -> None:
        if value:
            self.selected.add(code)
        else:
            self.selected.discard(code)
        self.staged = False
        self._refresh()

    def _toggle_all(self, e) -> None:
        value = bool(getattr(e.control, "value", False))
        self.selected = {str(item.get("code")) for item in self.rows
                         if item.get("code") in ACK_CODES} if value else set()
        self.staged = False
        self._refresh()

    def _clear(self) -> None:
        self.selected.clear()
        self.staged = False
        self._refresh()

    # ── 确认非重复 ──────────────────────────────────────────────────────
    def _ack_pairs(self) -> list[tuple[str, list[str]]]:
        pairs = []
        for code in sorted(self.selected):
            item = next((it for it in self.items if it.get("code") == code), None)
            if item is None:
                continue
            for record in item.get("records") or []:
                keys = record_keys(record)
                if len(keys) >= 2:
                    pairs.append((code, keys))
        return pairs

    def stage_ack(self) -> None:
        if not self.selected:
            return
        self.staged = True
        self._refresh()

    def unstage(self) -> None:
        self.staged = False
        self._refresh()

    def confirm_ack(self) -> None:
        if not self.staged:
            return
        self._schedule(self._apply_ack)

    async def _apply_ack(self) -> None:
        pairs = self._ack_pairs()
        if not pairs:
            self.error = "没有可确认的重复对；先展开核对 key。"
            self.staged = False
            self._refresh()
            return
        self.running = True
        self.error = ""
        self.success = ""
        self._refresh()
        added = 0
        try:
            for code, keys in pairs:
                dry = await asyncio.to_thread(
                    EB.read_json_command,
                    EB.bibdb_ack_duplicates_call(code=code, keys=keys,
                                                 reason="库健康页人工确认",
                                                 dry_run=True, db=self._db()),
                    timeout=30.0, notes=self._notes())
                if not dry.get("ok"):
                    self.error = f"确认失败：{_first_line(dry.get('error'))}"
                    break
                result = await asyncio.to_thread(
                    EB.read_json_command,
                    EB.bibdb_ack_duplicates_call(code=code, keys=keys,
                                                 reason="库健康页人工确认",
                                                 dry_run=False, db=self._db()),
                    timeout=30.0, notes=self._notes())
                if not result.get("ok"):
                    self.error = f"确认失败：{_first_line(result.get('error'))}"
                    break
                payload = result.get("json") or {}
                added += int(payload.get("added") or len(keys) - 1)
        except Exception as exc:  # noqa: BLE001
            self.error = f"确认失败：{_first_line(str(exc))}"
        self.running = False
        self._task_running = False
        self.staged = False
        if not self.error:
            self.success = f"已标记 {added} 项 · 清单 {len(pairs)}→{len(pairs) + added} · 库未改"
            self.selected.clear()
            self._mark("bib_health_ack", added=added)
            self._schedule(self._load)
        self._refresh()

    # ── 报告 ────────────────────────────────────────────────────────────
    def make_report(self) -> None:
        self._schedule(self._report)

    async def _report(self) -> None:
        self.running = True
        self.error = ""
        self._refresh()
        out = self._db()
        try:
            result = await asyncio.to_thread(
                EB.read_json_command, EB.bibdb_report_call(db=self._db(), out=out),
                timeout=60.0, notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
        self.running = False
        self._task_running = False
        if not result.get("ok"):
            self.error = f"生成报告失败：{_first_line(result.get('error'))}"
        else:
            self.report_time = _now()
            self.report_open = str(out)
            self.success = f"报告已生成 · {self.report_time}"
        self._refresh()

    def _open_report(self) -> None:
        opener = getattr(EB, "open_path", None)
        if callable(opener) and self.report_open:
            ok, message = opener(self.report_open)
            if not ok:
                self.error = f"打开报告失败：{message}"
                self._refresh()

    # ── 控件 ────────────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _check_button(self):
        btn = C.primary_button("重新体检" if self.loaded else "体检", lambda e: self.check(),
                               self.t, height=28)
        btn.disabled = self.running or self.staged
        btn._bib_primary_recheck = True
        return btn

    def _band(self) -> ft.Control:
        t = self.t
        counts = self.counts
        classes = len(self.items)
        errors = int(counts.get("errors") or 0)
        items = [
            {"label": "警告项", "value": _num(counts.get("warnings")),
             "hint": "警告句数（非记录数）", "tone": "warn"},
            {"label": "错误", "value": _num(errors),
             "hint": "有错先修，再看下表" if errors else "无错误",
             "tone": "error" if errors else ""},
            {"label": "规则类", "value": _num(classes),
             "hint": "规则类数", "tone": ""},
            {"label": "已确认", "value": _num(counts.get("near_duplicates_acknowledged")),
             "hint": "仅近似重复", "tone": ""},
        ]
        return S.summary_band(t, items)

    def _filter(self) -> ft.Control:
        t = self.t
        field = C.text_field(t, value=self.text, hint_text="规则名或示例 key",
                             on_change=self._on_text, width=260)
        field._bib_search_field = True
        rules = [("", "全部规则")] + [(item.get("code"), rule_label(str(item.get("code"))))
                                      for item in self.items]
        rule_select = C.select(t, value=self.rule_filter, options=rules,
                               on_select=self._on_rule, width=110)
        severity = C.select(t, value=self.severity_filter,
                            options=[("", "全部严重度"), ("警告", "警告"), ("提示", "提示")],
                            on_select=self._on_severity, width=SEVERITY_FILTER_W)
        # 菜单项是整字的；选中值在窄窗被挤时 tooltip 兜底（宽度已按整字留足）。
        severity.tooltip = "按严重度筛选：全部严重度 / 警告 / 提示"
        ackable = ft.Container(
            height=t.px(24), padding=ft.Padding(left=t.px(4), right=t.px(8), top=0, bottom=0),
            content=ft.Row(spacing=t.sp("half"), controls=[
                ft.Checkbox(value=self.only_ackable, on_change=self._on_only_ackable,
                            visual_density=ft.VisualDensity.COMPACT, splash_radius=0),
                C.text("只看可标记项", t, "caption", t.palette.text_muted),
            ]))
        return S.filter_row(
            t, search=field, on_search=self._on_text,
            controls=[rule_select, severity, ackable],
            count=self._check_button(), page=self.page.page, search_width=260)

    def _table(self) -> ft.Control:
        t = self.t
        narrow = self._build_width < 1100
        columns = ["", "", "规则", "严重度", "记录数", "说明"]
        weights = [34, 20, 132, 64, 64, 414] if narrow else [34, 20, 150, 76, 78, 300, 234]
        if not narrow:
            columns = ["", "", "规则", "严重度", "记录数", "示例记录", "说明"]
        head_cells = [ft.Container(width=t.px(34), content=ft.Checkbox(
            on_change=self._toggle_all, visual_density=ft.VisualDensity.COMPACT,
            splash_radius=0)), "", *columns[2:]]
        # 用统一表头：首两列是固定宽，其余按权重
        header = ft.Container(
            height=t.px(30), padding=ft.Padding(left=t.px(8), right=t.px(8), top=0, bottom=0),
            border=ft.Border(top=ft.BorderSide(0.6, t.palette.border),
                             bottom=ft.BorderSide(0.6, t.palette.border)),
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Container(width=t.px(34), content=ft.Checkbox(
                                   on_change=self._toggle_all,
                                   visual_density=ft.VisualDensity.COMPACT, splash_radius=0)),
                               ft.Container(width=t.px(20)),
                               *[ft.Container(
                                   expand=w, padding=ft.Padding(left=0, right=t.px(8),
                                                                top=0, bottom=0),
                                   content=C.text(label, t, "caption", t.palette.text_faint,
                                                  weight="semibold", max_lines=1))
                                 for w, label in zip(weights[2:], columns[2:])]]))
        rows: list = []
        for item in self.rows:
            code = str(item.get("code") or "")
            ackable = code in ACK_CODES
            expanded = code in self.expanded
            cells = [
                ft.Container(width=t.px(34), content=(
                    ft.Checkbox(value=code in self.selected,
                                on_change=lambda e, c=code: self._toggle_row(
                                    c, bool(e.control.value)),
                                visual_density=ft.VisualDensity.COMPACT, splash_radius=0)
                    if ackable else ft.Container())),
                ft.Container(width=t.px(20), alignment=ft.Alignment.CENTER,
                             content=ft.Icon(C.ic("KEYBOARD_ARROW_DOWN" if expanded
                                                  else "KEYBOARD_ARROW_RIGHT"),
                                             size=t.px(14), color=t.palette.text_faint),
                             on_click=lambda e, c=code: self._toggle_expand(c)),
                C.text(rule_label(code), t, "section-label", t.palette.text, max_lines=1),
                C.badge(severity_of(code), t, "warn" if severity_of(code) == "警告"
                        else "neutral"),
                C.text(count_text(item), t, "caption", t.palette.text_muted),
            ]
            if not narrow:
                cells.append(C.text(sample_text(item), t, "caption", t.palette.text_muted,
                                    font_family=t.mono_family, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS))
            cells.append(C.text(count_hint(item), t, "caption", t.palette.text_faint,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
            row_controls = [
                ft.Container(width=t.px(34), content=cells[0].content),
                ft.Container(width=t.px(20), content=cells[1].content),
                *[ft.Container(expand=w, content=cell, padding=ft.Padding(
                    left=0, right=t.px(8), top=0, bottom=0))
                  for w, cell in zip(weights[2:], cells[2:])],
            ]
            rows.append(ft.Container(
                height=t.px(32), padding=ft.Padding(left=t.px(8), right=t.px(8), top=0, bottom=0),
                bgcolor=t.palette.accent_soft if code in self.selected else None,
                border=ft.Border(bottom=ft.BorderSide(0.6, t.palette.border)),
                on_click=lambda e, c=code: self._toggle_expand(c),
                content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=row_controls)))
            if expanded:
                rows.append(self._expanded_row(item, narrow))
        if not rows:
            rows.append(S.empty_row(t, "没有待处理警告", height=32))
        return ft.Column(spacing=0, controls=[header, *rows])

    def _expanded_row(self, item: dict, narrow: bool) -> ft.Control:
        t = self.t
        records = list(item.get("records") or [])
        lines = []
        for record in records[:4]:
            lines.append(ft.Row(spacing=t.sp("1"), controls=[
                C.text(str(record), t, "caption", t.palette.text_muted,
                       font_family=t.mono_family, expand=True, max_lines=1,
                       overflow=ft.TextOverflow.ELLIPSIS),
                C.text_button("复制", lambda e, r=record: self._copy(str(r)), t, height=22),
                C.text_button("去编辑", lambda e, r=record: self._edit(record_keys(str(r))),
                              t, height=22),
            ]))
        if not lines:
            lines.append(C.text("没有记录样本。", t,
                                "caption", t.palette.text_faint))
        top = f"共 {int(item.get('count') or len(records))} 项 · 最多展示 4 条"
        box = ft.Container(
            # 最多 5 行样例，shrink-wrap：页体是唯一滚动者，滚轮不被这层吞掉。
            padding=ft.Padding(left=t.px(62), right=t.px(8),
                               top=t.px(6), bottom=t.px(6)),
            bgcolor=t.palette.surface_2,
            content=ft.Column(spacing=0, controls=[
                ft.Row(controls=[
                    C.text(top, t, "caption", t.palette.text_faint),
                    ft.Container(expand=True),
                    C.text_button("查看全部", lambda e: self._copy("\n".join(map(str, records))),
                                  t, height=22),
                ]),
                *lines,
            ]))
        box._bib_shrink = True
        return box

    def _batch(self) -> ft.Control | None:
        t = self.t
        if not self.selected:
            return None
        return S.batch_bar(
            t, count=len(self.selected),
            actions=[C.text_button("确认标记", lambda e: self.stage_ack(), t, height=28)],
            on_clear=lambda e: self._clear())

    def _confirm_bar(self) -> ft.Control | None:
        if not self.staged:
            return None
        t = self.t
        pairs = self._ack_pairs()
        before = int(self.counts.get("near_duplicates_acknowledged") or 0)
        return S.confirm_bar(
            t, consequence=f"影响 {len(pairs)} 项 · 清单 {before}→{before + len(pairs)}",
            detail="只写忽略清单，不改库内容。",
            sha=(self.db_sha[:8] + "…") if self.db_sha else None,
            on_confirm=lambda e: self.confirm_ack(),
            confirm_label="确认标记",
            on_repreview=lambda e: self.unstage(), repreview_label="取消")

    def _footer(self) -> ft.Control:
        t = self.t
        total = len(self._filtered_items())
        shown = len(self.rows)
        controls = [C.text(f"显示 {shown} / {total} 类", t, "caption", t.palette.text_faint)]
        if shown < total:
            controls.append(C.text_button(f"载入更多（{total - shown}）",
                                          lambda e: self.load_more(), t, height=28))
        controls.append(ft.Container(expand=True))
        if self.report_time:
            controls.append(C.text_button(f"上次报告 · {self.report_time}",
                                          lambda e: self._open_report(), t, height=28))
        return ft.Row(spacing=t.sp("1"), controls=controls)

    def _filtered_items(self) -> list[dict]:
        rows = []
        for item in self.items:
            code = str(item.get("code") or "")
            if self.rule_filter and code != self.rule_filter:
                continue
            if self.severity_filter and severity_of(code) != self.severity_filter:
                continue
            if self.only_ackable and code not in ACK_CODES:
                continue
            if self.text:
                hay = " ".join([code, rule_label(code), sample_text(item),
                                str(item.get("detail") or "")]).lower()
                if self.text.lower() not in hay:
                    continue
            rows.append(item)
        return rows

    def _body(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        parts: list = []
        if self.error:
            parts.append(S.state_banner(
                t, "failure", message=self.error,
                next_label="复制错误", on_next=lambda e: self._copy(self.error),
                page=self.page.page))
        if self.success:
            parts.append(S.state_banner(t, "success", message=self.success))
        if self.cancelled:
            parts.append(S.state_banner(t, "cancelled",
                                        message=f"已取消，显示 {self.last_check} 的结果。"))
        if self.running and not self.loading:
            parts.append(S.state_banner(t, "running", message="运行中",
                                        on_cancel=lambda e: self.cancel(),
                                        page=self.page.page))
        if self.loading and not self.loaded:
            parts.append(S.state_banner(t, "loading", message="运行中"))
        if self.loaded and not self.items and not self.error:
            parts.append(S.state_banner(
                t, "empty", message=f"没有待处理警告 · 上次体检 {self.last_check}",
                next_label="重新体检", on_next=lambda e: self.check()))
        elif self.loaded:
            parts.append(self._band())
        parts.append(self._table())
        parts.append(self._footer())
        batch = self._batch()
        if batch is not None:
            parts.append(batch)
        confirm = self._confirm_bar()
        if confirm is not None:
            parts.append(confirm)
        return ft.Column(spacing=t.sp("1"), scroll=S.scrollbar(), expand=True,
                         controls=parts)

    def cancel(self) -> None:
        self.cancelled = True
        self.running = False
        self._refresh()

    def _edit(self, keys: list[str]) -> None:
        if not keys:
            return
        store = getattr(getattr(self.page, "app", None), "store", None)
        if store is not None:
            try:
                store.set("bib_focus_key", keys[0])
            except Exception:  # noqa: BLE001
                pass
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter("edit")

    def _copy(self, text: str) -> None:
        fn = getattr(getattr(self.page, "app", None), "copy_text", None)
        if callable(fn):
            try:
                fn(text)
            except Exception:  # noqa: BLE001
                pass

    def _dock(self) -> ft.Control:
        t = self.t
        if self.running:
            return C.text("运行中", t, "caption", t.palette.text_muted)
        return C.text(f"上次体检 {self.last_check or '—'} · 只读",
                      t, "caption", t.palette.text_faint)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        return S.page_shell(
            self.t, title=TITLE,
            meta=(f"{_num(self.counts.get('warnings'))} 条警告 · "
                  f"{len(self.items)} 类 · {_num(self.counts.get('errors'))} 错误"
                  if self.loaded else "运行中"),
            role_chip="readonly",
            action=C.secondary_button("生成报告", lambda e: self.make_report(), self.t,
                                      height=28),
            on_gear=lambda e: self._goto("settings"),
            body=ft.Column(spacing=0, expand=True,
                           controls=[self._filter(), self._body()]),
            dock=self._dock(), dock_height=S.DOCK_H)

    def _goto(self, view: str) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)


def _num(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "—"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M")


def _first_line(text) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return "引擎没有返回说明"
