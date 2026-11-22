"""引用库 · 概览（批 115 夜间重设计 BPAGES-A，VIEW_KEY="overview"）。

一屏回答「库现在什么状态、下一步做什么」：五格摘要带（条目 / 待办冲突 /
体检警告 / 重复候选 / 归位建议）+ 固定顺序的队列表 + 右侧库详情。

口径（不可混用）：
* 待办冲突用 `conflicts.totals.pending`（现在 0），历史累计用
  `conflicts.totals.total`（157）/ `stats.conflicts.records`（135 条记录）；
* 体检警告用 `lint.counts.warnings`（76 是警告**句数**，不是记录数）；
* 重复候选用 `report-duplicates --json` 的 `pairs`（0），已确认数取
  `lint.counts.near_duplicates_acknowledged`（2）；
* 归位建议取 `suggest-field-fixes --registry missing_field --json` 的 `count`。

本页只读：不写库、不 git、不推送。所有引擎调用走 `EB.*`，加载失败只降级
对应格子（显示「—」+「读取失败」），其余格子与队列表照常可读。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "overview"
TITLE = "概览"
ROLE = "readonly"

#: 队列固定顺序：待办（未完成）→ 已清空 → 未运行。
QUEUE = (
    ("conflict", "冲突裁决", "review", "去待处理"),
    ("fieldfix", "字段归位", "fieldfix", "去字段归位"),
    ("lint", "体检警告", "health", "去库健康"),
    ("dupes", "重复候选", "dupes", "去查重合并"),
    ("normalize", "AI 辅助处理 Bib", "normalize", "去 AI 辅助处理 Bib"),
)

_BAND = (
    ("records", "条目"),
    ("pending", "待办冲突"),
    ("warnings", "体检警告"),
    ("pairs", "重复候选"),
    ("suggest", "归位建议"),
)

DETAIL_W = 306


def _num(value) -> str:
    """整数千分位；None/异常值一律给空串，由调用方补「—」。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f"{int(value):,}"


def _short_sha(sha: str) -> str:
    sha = str(sha or "").strip()
    return f"{sha[:8]}…" if len(sha) > 8 else sha


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _basename(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        return "（未读取）"
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path


def _engine_reason(result) -> tuple[str, str]:
    """引擎失败原文 →（一行摘要，完整原文）。

    F1（P5）：此前只写「X 读取失败」，引擎真正说的那句（例如
    「SLCN_NOTES 指向 …，但那里没有 `cases/` 目录」）被整段吞掉，用户无从判断
    是库坏了还是根本没找到笔记仓。``read_json_command`` 里 stderr 才是引擎原文
    （error 只在 payload 解析不出且有非零退出码时才填），所以优先读 stderr。
    """
    if not isinstance(result, dict):
        return "", ""
    full = str(result.get("stderr") or result.get("error") or "").strip()
    if not full:
        return "", ""
    lines = [raw.strip().lstrip("✗⚠️ ").strip() for raw in full.splitlines()]
    lines = [text for text in lines if text]
    if not lines:
        return "", full
    # 首行是「找不到私有笔记仓」这类总述、中间是通用设置指引，真正点出毛病的是
    # 「指向 X，但那里没有 cases/ 目录」那句。按可操作性排优先级，横幅一行才装
    # 得下有用信息。
    line = next((text for text in lines if "但那里没有" in text), "")
    if not line:
        line = next((text for text in lines if "cases/" in text), "")
    if not line:
        line = next((text for text in lines if "SLCN_NOTES" in text), lines[0])
    if len(line) > 120:
        line = f"{line[:117]}…"
    return line, full


class OverviewView:
    """概览视图，页内刷新时保留正文滚动容器。"""

    def __init__(self, page) -> None:
        self.page = page
        self.loaded = False
        self.loading = False
        self.error = ""
        self.error_detail = ""
        self.stale = False
        self.cancelled = False
        self.success = ""
        self.phase = ""
        self.snapshot = ""
        self.db_sha = ""
        self.cells: dict[str, dict] = {}
        self.queue: dict[str, dict] = {}
        self.detail: dict = {}
        self._task_running = False
        self._build_width = 0.0
        self._panel: ft.Container | None = None

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self):
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _db(self) -> str:
        """引用库物理路径：stats_data["db"] → stats_data["path"] → GIT.default_db。

        R2-1（DEF-01）：真实引擎 ``cmd_stats`` 输出的是 ``store.stats()``（只有
        ``path``/``bytes``/``sha256``/``records`` 等，没有 ``db`` 键），
        ``bib_host._load_stats`` 又原样存进 ``stats_data``，所以只读 ``["db"]``
        在真实装配里恒为空 —— 查重报告与归位建议会因此退回引擎默认库。第二级
        读 ``path``；第三级回退到用户设置里的默认库，与 bib_query / bib_edit /
        bib_dupes / bib_fieldfix / bib_import 兄弟页同款。
        """
        stats = getattr(self.page, "stats_data", None) or {}
        path = ""
        if isinstance(stats, dict):
            path = str(stats.get("db") or stats.get("path") or "")
        if path:
            return path
        try:
            return str(GIT.default_db(self._notes()) or "")
        except Exception:  # noqa: BLE001 —— 设置读不到时由调用方按「未配置路径」提示
            return ""

    def _store(self):
        return getattr(getattr(self.page, "app", None), "store", None)

    def _set_store(self, key: str, value) -> None:
        store = self._store()
        if store is None:
            return
        try:
            store.set(key, value)
        except Exception:  # noqa: BLE001 —— 存储失败不影响只读页面
            pass

    def _mark(self, event: str, **kwargs) -> None:
        marker = getattr(self.page, "mark", None)
        if callable(marker):
            try:
                marker(event, **kwargs)
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        panel = self._panel
        slot = getattr(self.page, "view_slot", None)
        if panel is None or (slot is not None and slot.content is not panel):
            return
        self._build_width = self._design_width()
        panel._bib_body.content.controls = self._body_controls(self._build_width < 1100)
        panel._bib_meta.value = self._meta()
        panel._bib_dock.content = self._notice_dock()
        panel._bib_dock.height = self.t.px(S.DOCK_H_EXPANDED if self.error else S.DOCK_H)
        try:
            panel.update()
        except RuntimeError:  # 控件尚未挂载
            pass

    def _schedule(self) -> None:
        """统一走 Page.run_task(coroutine function)（flet 1.0.5 只收函数）。"""
        if self._task_running:
            return
        self._task_running = True
        runner = getattr(self.page.page, "run_task", None)
        if callable(runner):
            runner(self.load)
        else:  # 测试替身没有 run_task 时退回同步 asyncio（不会在真实 Flet 发生）
            asyncio.run(self.load())

    # ── 生命周期 ────────────────────────────────────────────────────────
    def activate(self) -> None:
        if not self.loaded and not self.loading:
            self._schedule()

    def refresh_running(self) -> None:
        """概览没有可取消的长任务；保留契约方法。"""
        return None

    def header_actions(self) -> list:
        return [self._primary()]

    def primary_action(self):
        return self._primary()

    # ── 引擎读取 ────────────────────────────────────────────────────────
    async def _read(self, call) -> dict:
        try:
            return await asyncio.to_thread(
                EB.read_json_command, call, timeout=10.0, notes=self._notes())
        except Exception as exc:  # noqa: BLE001 —— 单格失败不能拖垮整页
            return {"ok": False, "json": {}, "code": -1, "lines": [],
                    "error": f"{type(exc).__name__}: {exc}"}

    async def load(self, *, manual: bool = False) -> None:
        """Phase A 并行 stats/conflicts/lint；Phase B 并行查重报告/归位建议。"""
        if self.loading:
            return
        self.loading = True
        self.error = ""
        self.error_detail = ""
        self.cancelled = False
        self.success = ""
        self.phase = "运行中 0/5"
        self._refresh()
        tmp = ""
        try:
            self.phase = "运行中 1/5"
            stats_r, conflicts_r, lint_r = await asyncio.gather(
                self._read(EB.bibdb_stats_call()),
                self._read(EB.bibdb_conflicts_call(pending_only=True)),
                self._read(EB.bibdb_lint_call()),
            )
            self.phase = "运行中 4/5"
            tmp = tempfile.mkdtemp(prefix="bib-overview-")
            dup_r, sug_r = await asyncio.gather(
                self._read(EB.bibdb_report_duplicates_call(out=tmp, db=self._db())),
                self._read(EB.bibdb_suggest_fixes_call(
                    registry="missing_field", db=self._db())),
            )
            self.phase = "运行中 5/5"
            self._apply(stats_r, conflicts_r, lint_r, dup_r, sug_r)
            self.loaded = True
            self.snapshot = _clock()
            self.success = f"已刷新 · {self.snapshot}"
            self._mark("bib_overview_loaded", records=self.cells.get("records", {}).get("value"),
                       pending=self.cells.get("pending", {}).get("value"),
                       warnings=self.cells.get("warnings", {}).get("value"))
        finally:
            self.loading = False
            self._task_running = False
            self.phase = ""
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            self._refresh()

    def _apply(self, stats_r, conflicts_r, lint_r, dup_r, sug_r) -> None:
        stats = (stats_r or {}).get("json") or {}
        conflicts = (conflicts_r or {}).get("json") or {}
        lint = (lint_r or {}).get("json") or {}
        dup = (dup_r or {}).get("json") or {}
        sug = (sug_r or {}).get("json") or {}
        counts = lint.get("counts") or {}
        totals = conflicts.get("totals") or {}

        def ok(result) -> bool:
            return bool((result or {}).get("ok"))

        def cell(key: str, value, *, hint: str = "", failure: bool = False) -> dict:
            if failure or value is None:
                return {"value": "—", "hint": hint or "读取失败", "failed": True}
            return {"value": value, "hint": hint, "failed": False}

        records = stats.get("records")
        sources = stats.get("sources")
        verified, unverified = stats.get("verified"), stats.get("unverified")
        pending = totals.get("pending")
        total = totals.get("total")
        conflict_records = (stats.get("conflicts") or {}).get("records")
        warnings = counts.get("warnings")
        errors = counts.get("errors")
        acked = counts.get("near_duplicates_acknowledged")
        inv = counts.get("needs_inversion") or {}
        inverted = None
        if isinstance(inv, dict):
            inverted = sum(v for v in inv.values() if isinstance(v, (int, float)))
        pairs = dup.get("pairs")
        pairs_pending = None
        if isinstance(pairs, (int, float)):
            acknowledged = acked if isinstance(acked, (int, float)) else 0
            pairs_pending = max(0, int(pairs) - int(acknowledged))
        suggest = sug.get("count")

        self.cells = {
            "records": cell("records", _num(records) if ok(stats_r) else None,
                            hint=f"源 {_num(sources) or '—'}"),
            "pending": cell("pending", _num(pending) if ok(conflicts_r) else None,
                            hint=(f"累计 {_num(total) or '—'} 条已裁决"
                                  if total is not None else "读取失败")),
            "warnings": cell("warnings", _num(warnings) if ok(lint_r) else None,
                             hint=f"{_num(errors) if errors is not None else '—'} 错误"),
            "pairs": cell("pairs", _num(pairs_pending) if ok(dup_r) else None,
                          hint=(f"{_num(pairs) or 0} 对已确认非重复"
                                if acked is not None else "查重报告")),
            "suggest": cell("suggest", _num(suggest) if ok(sug_r) else None,
                            hint="缺字段 · 只读"),
        }
        self.queue = {
            "conflict": {
                "value": f"{_num(pending) or '—'} 条待办",
                "impact": (f"累计 {_num(total) or '—'} · {_num(conflict_records) or '—'} 条记录"
                           if total is not None else "读取失败"),
            },
            "fieldfix": {
                "value": f"{_num(suggest) or '—'} 条",
                "impact": "缺题名 / 年份的候选",
            },
            "lint": {
                "value": f"{_num(warnings) or '—'} 条",
                "impact": (f"{_num(errors) if errors is not None else '—'} 错误"
                           f" · 倒置名 {_num(inverted) if inverted is not None else '—'}"),
            },
            "dupes": {
                "value": f"{_num(pairs_pending) or '—'} 对待办",
                "impact": (f"{_num(acked) or 0} 对已确认"
                           if acked is not None else "查重报告"),
            },
            "normalize": {
                "value": "未运行",
                "impact": "只出报告，不改库",
            },
        }
        self.detail = {
            "path": stats.get("path") or "",
            "sha": stats.get("sha256") or "",
            "records": records,
            "sources": sources,
            "verified": verified,
            "unverified": unverified,
            "aliases": (stats.get("aliases") or {}).get("total"),
            "conflicts": total,
            "pending": pending,
            "warnings": warnings,
            "updated": stats.get("updated_newest") or "",
            "size": stats.get("bytes") or 0,
            "dirty": bool(stats.get("dirty")),
            "raw_lines": stats.get("raw_lines"),
        }
        self.db_sha = str(stats.get("sha256") or "")
        failures = [name for name, c in self.cells.items() if c.get("failed")]
        if failures:
            first = failures[0]
            labels = {"records": "库概况", "pending": "冲突", "warnings": "体检",
                      "pairs": "查重报告", "suggest": "归位建议"}
            results = {"records": stats_r, "pending": conflicts_r, "warnings": lint_r,
                       "pairs": dup_r, "suggest": sug_r}
            reason, detail = _engine_reason(results.get(first))
            self.error = f"{labels.get(first, first)}读取失败"
            if reason:
                self.error = f"{self.error} · {reason}"
            self.error_detail = detail
            self._mark("bib_overview_partial_failure", cells=failures, reason=reason)

    # ── 控件 ────────────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _primary(self):
        return C.primary_button("入藏新条目", lambda e: self._goto("import"), self.t,
                                icon="LIBRARY_ADD", height=28)

    def _goto(self, view: str) -> None:
        self._set_store("bib_nav_origin", "overview")
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)
        self._mark("bib_overview_goto", view=view)

    def build(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        narrow = self._build_width < 1100
        self._panel = S.page_shell(
            t, title=TITLE,
            meta=self._meta(),
            role_chip="readonly",
            action=self._primary(),
            on_gear=lambda e: self._goto("settings"),
            body=ft.Column(spacing=t.sp("1"), scroll=C.scrollbar(t), expand=True,
                           controls=self._body_controls(narrow)),
            dock=self._notice_dock(),
            dock_height=S.DOCK_H_EXPANDED if self.error else S.DOCK_H,
        )
        return self._panel

    def _meta(self) -> str:
        if not self.loaded:
            return "运行中" if self.loading else "只读 · 概览与下一步"
        updated = str(self.detail.get("updated") or "")[:16].replace("T", " ") or "—"
        return f"更新 {updated} · 快照 {self.snapshot or '—'}"

    def _band(self) -> ft.Control:
        t = self.t
        items = []
        for key, label in _BAND:
            cell = self.cells.get(key) or {"value": "—", "hint": "尚未读取", "failed": False}
            unit = ""
            if key == "pairs":
                unit = " 对"
            items.append({
                "label": label,
                "value": f"{cell.get('value') or '—'}{unit}",
                "hint": cell.get("hint") or "",
                "tone": "error" if cell.get("failed") else
                        ("warn" if key in ("pending", "warnings") and
                         any(ch.isdigit() for ch in str(cell.get("value") or "")) and
                         str(cell.get("value")) not in ("0", "—") else ""),
            })
        return S.summary_band(t, items)

    def _queue_table(self) -> ft.Control:
        t = self.t
        header = S.table_header_row(
            t, ["队列", "数量", "影响 · 范围", "下一步"],
            weights=[17, 7, 22, 11], sortable=[False] * 4)
        rows: list = []
        if self.loaded and (self.detail.get("records") or 0) == 0:
            rows.append(S.empty_row(
                t, "库里还没有条目；导入 .bib 后列出待办。",
                action=C.text_button("入藏新条目", lambda e: self._goto("import"), t,
                                     height=28)))
        else:
            for key, label, target, next_label in QUEUE:
                data = self.queue.get(key) or {"value": "—", "impact": "尚未读取"}
                rows.append(S.table_row(
                    t, [label, data.get("value") or "—", data.get("impact") or "—",
                        f"{next_label} →"],
                    weights=[17, 7, 22, 11], mono=[],
                    on_click=lambda e, target=target: self._goto(target)))
        return ft.Column(spacing=0, controls=[header, *rows])

    def _detail_panel(self) -> ft.Control:
        t = self.t
        d = self.detail
        lines = [
            ("库文件", _basename(d.get("path") or "")),
            ("库指纹", _short_sha(d.get("sha") or "") or "—"),
            ("记录", _num(d.get("records")) or "—"),
            ("来源", _num(d.get("sources")) or "—"),
            ("已核实 / 未核实", f"{_num(d.get('verified')) or '—'} / {_num(d.get('unverified')) or '—'}"),
            ("别名", _num(d.get("aliases")) or "—"),
            ("冲突（累计）", _num(d.get("conflicts")) or "—"),
            ("待办冲突", _num(d.get("pending")) or "—"),
            ("体检警告", _num(d.get("warnings")) or "—"),
            ("快照", self.snapshot or "—"),
        ]
        body = [C.text("库详情", t, "caption", t.palette.text_faint, weight="semibold"),
                ft.Container(height=t.sp("half"))]
        for label, value in lines:
            body.append(ft.Row(
                spacing=t.sp("1"), controls=[
                    C.text(label, t, "caption", t.palette.text_muted, max_lines=1),
                    ft.Container(expand=True),
                    C.text(str(value), t, "caption", t.palette.text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(value)),
                ]))
        body.append(ft.Container(height=t.sp("1")))
        body.append(C.text("只读 · 不写库", t, "caption",
                           t.palette.text_faint, max_lines=2))
        return ft.Container(
            width=None if self._build_width < 1100 else t.px(DETAIL_W),
            expand=self._build_width < 1100,
            padding=ft.Padding(left=t.px(16), top=t.px(12), right=0, bottom=0),
            content=ft.Column(spacing=t.sp("half"), controls=body))

    def _body_controls(self, narrow: bool) -> list[ft.Control]:
        t = self.t
        parts: list = []
        if self.error and self.loaded:
            parts.append(self._error_bar())
        if self.cancelled:
            parts.append(S.state_banner(
                t, "cancelled", message="已取消刷新 · 显示上次快照",
                detail=self.snapshot or "尚未读取", next_label="重新统计",
                on_next=lambda e: self._schedule()))
        if self.loading and not self.loaded:
            parts.append(S.state_banner(t, "loading", message="运行中"))
        if self.stale:
            parts.append(S.stale_notice(
                t, expected_sha=self.db_sha, actual_sha="",
                title="库已更新 · 当前是上次快照结果"))
        if self.success and not self.loading:
            parts.append(S.state_banner(t, "success", message=self.success))
        parts.append(self._band())
        table = self._queue_table()
        detail = self._detail_panel()
        if narrow:
            info = self._detail_grid()
            parts.extend([table, ft.Container(height=t.sp("1")), info])
        else:
            parts.append(ft.Row(
                spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[ft.Container(expand=True, content=table), detail]))
        parts.append(self._refresh_row())
        return parts

    def _detail_grid(self) -> ft.Control:
        """1000 宽：详情折成 3 行 × 2 列，不横向滚动。"""
        t = self.t
        d = self.detail
        pairs = [
            ("库文件", _basename(d.get("path") or "")),
            ("库指纹", _short_sha(d.get("sha") or "") or "—"),
            ("记录 / 来源", f"{_num(d.get('records')) or '—'} / {_num(d.get('sources')) or '—'}"),
            ("已核实 / 未核实", f"{_num(d.get('verified')) or '—'} / {_num(d.get('unverified')) or '—'}"),
            ("累计冲突 / 待办", f"{_num(d.get('conflicts')) or '—'} / {_num(d.get('pending')) or '—'}"),
            ("体检警告 / 快照", f"{_num(d.get('warnings')) or '—'} / {self.snapshot or '—'}"),
        ]
        rows = []
        for index in range(0, len(pairs), 2):
            cells = []
            for label, value in pairs[index:index + 2]:
                cells.append(ft.Container(expand=1, content=ft.Column(spacing=0, controls=[
                    C.text(label, t, "caption", t.palette.text_faint),
                    C.text(str(value), t, "caption", t.palette.text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                ])))
            rows.append(ft.Row(spacing=t.sp("2"), controls=cells))
        return ft.Column(spacing=t.sp("half"), controls=[
            C.text("库详情", t, "caption", t.palette.text_faint, weight="semibold"), *rows,
            C.text("只读 · 不写库", t, "caption", t.palette.text_faint),
        ])

    def _refresh_row(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(f"快照 {self.snapshot or '—'}", t, "caption", t.palette.text_faint),
            ft.Container(expand=True),
            C.text_button("重新统计", lambda e: self._schedule(), t, icon="REFRESH",
                          height=28),
        ])

    def _error_bar(self) -> ft.Control:
        t = self.t
        copy = None
        if self.error:
            copy = lambda e: self._copy(self.error)
        # F1（P5）：把引擎原文（含「没有 cases/ 目录」这类关键行）放进 stderr 区，
        # 用户能选中/复制；message 仍是一句话摘要，detail 保留降级说明。
        return S.state_banner(t, "failure", message=self.error,
                              detail="其余格子仍显示上次成功读取的值。",
                              stderr=self.error_detail or None,
                              on_next=lambda e: self._copy(self.error),
                              next_label="复制摘要", page=self.page.page,
                              on_copy=copy)

    def _notice_dock(self) -> ft.Control:
        t = self.t
        if self.error:
            return C.text(self.error, t, "caption", t.palette.error, max_lines=1,
                          overflow=ft.TextOverflow.ELLIPSIS,
                          tooltip=self.error)
        hint = "只读 · 本页不写库" if not self.loading else self.phase
        return C.text(hint, t, "caption", t.palette.text_faint, max_lines=1)

    def _copy(self, text: str) -> None:
        for name in ("copy_text",):
            fn = getattr(getattr(self.page, "app", None), name, None)
            if callable(fn):
                try:
                    fn(text)
                    return
                except Exception:  # noqa: BLE001
                    pass
        page = getattr(self.page, "page", None)
        setter = getattr(page, "set_clipboard", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:  # noqa: BLE001
                pass
