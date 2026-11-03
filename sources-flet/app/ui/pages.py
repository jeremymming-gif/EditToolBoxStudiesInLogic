"""P3 页面：工作台 / 单篇稿件（2 步）/ 整期装配（6 步）/ 引用库（10 视图）/ 设置 / 帮助与关于。

页面只做三件事：取值（输入控件）→ 拼参数（services.engine_bridge）→ 显示日志（LogConsole）。
不复制任何引擎判定逻辑；长任务走 asyncio 子进程，不阻塞 UI 线程。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re as _re
import tempfile
import time
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from services import settings_store as SS
from services import startup_log as SLOG
from ui import assembly as ASM
from ui import ai_panel as AIP
from ui import bib_advanced as BA
from ui import bib_ops as BO
from ui import bib_ops_dupes as BOD
from ui import bib_ops_health as BOH
from ui import bib_settings as BS
from ui import bib_write as BW
from ui import checks as CK
from ui import components as C
from ui import metadata_form as MF
from ui import normalize as NR
from ui import settings_shell as SHELL
from ui.base_page import BasePage
from ui.tokens import ZOOM_MIN
from ui.help_page import HelpPage
from ui.ia_map import stats as ia_stats

STATUS_TEXT = {"idle": "待运行", "running": "运行中", "done": "已完成", "warn": "有提示", "error": "失败"}

# ── 高级参数输入约束（用户 m02483：按格式限制输入并即时提示）──────────────────
_DIGITS_FILTER = ft.InputFilter(allow=True, regex_string=r"[0-9]")
_PAGES_FILTER = ft.InputFilter(allow=True, regex_string=r"[0-9,，\- –—]")
_DATE_FILTER = ft.InputFilter(allow=True, regex_string=r"[0-9\-]")
_YEAR_MIN, _YEAR_MAX = 1900, 2100
# 批 114 用户 m03074：高级参数 14 个可清空键（12 个文本 + 2 个下拉）。
_ADVANCED_TEXT_KEYS = (
    "convert_vol", "convert_issue", "convert_year", "convert_pages",
    "convert_author", "convert_name", "convert_received", "convert_grants",
    "convert_bib", "convert_refs", "convert_sty", "convert_skeleton",
)
_ADVANCED_SELECT_KEYS = ("convert_lang", "convert_editor")



def _valid_digits(value: str) -> str | None:
    """卷/期：只允许纯数字。"""
    if not value or value.isdigit():
        return None
    return "只填数字"


def _valid_year(value: str) -> str | None:
    if not value:
        return None
    if not _re.fullmatch(r"\d{4}", value):
        return "年份写 4 位数字，如 2025"
    return None if _YEAR_MIN <= int(value) <= _YEAR_MAX else f"年份应在 {_YEAR_MIN}–{_YEAR_MAX}"


def _valid_pages(value: str) -> str | None:
    if not value:
        return None
    ok = _re.fullmatch(r"\d+(\s*[-–—]\s*\d+)?(\s*[,，]\s*\d+(\s*[-–—]\s*\d+)?)*", value)
    return None if ok else "页码写法如 137-152 或 12,15-18"


def _valid_date(value: str) -> str | None:
    if not value:
        return None
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "日期写法如 2025-09-19"
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return "日期不存在"
    return None



def _level_of_line(text: str) -> str:
    line = text or ""
    if "❌" in line or "错误" in line or "Traceback" in line:
        return "error"
    if "⚠" in line or "警告" in line or "❗" in line:
        return "warn"
    if "✅" in line or "完成。" in line:
        return "ok"
    if EB.is_summary_line(line):
        return "info"
    return "dim"


def _basename(path: str) -> str:
    try:
        return Path(path).name if path else ""
    except (OSError, ValueError):
        return path


# ── 工作台 ─────────────────────────────────────────────────────────────────
_ACTIVITY_LABELS = {
    "process_start": "启动",
    "shell_built": "外壳就绪",
    "visible": "窗口显示",
    "engine_status": "引擎探活",
    "page_ready": "页面就绪",
    "nav_change": "切换页面",
    "theme_changed": "切换主题",
    "settings_saved": "设置已保存",
    "first_run_detected": "首次启动",
    "envcheck": "环境检查",
    "close_requested": "关闭窗口",
    "version_fallback": "版本回退",
}


def _ago(seconds: float) -> str:
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


class HomePage(BasePage):
    key = "home"
    TOOLBAR_TITLE = "工作台"

    def toolbar_meta(self) -> str:
        return "引擎就绪 · 本地只读" if self.app.engine_status.ok else "引擎缺失 · 只读"

    def toolbar_action(self):
        return ("新建", "ADD", lambda e: self.app.goto("single"))

    # ── 取数：只用真实值（不写死 6 / 3 / 2 / 135）──────────────────────
    def _bib_value(self) -> str:
        stats = getattr(self.app.pages.get("bib"), "stats_data", None) or {}
        records = stats.get("records")
        return f"{int(records):,}" if isinstance(records, (int, float)) else "—"

    def _activity(self, limit: int = 8) -> list[tuple[str, str]]:
        """最近活动来自本机启动日志（JSONL，只有生命周期事件，无稿件内容）。"""
        path = SLOG.log_path()
        if path is None or not Path(path).exists():
            return []
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()[-80:]
        except OSError:
            return []
        rows: list[tuple[str, str]] = []
        now = time.time()
        for raw in reversed(lines):
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            label = _ACTIVITY_LABELS.get(str(item.get("event") or ""))
            if not label:
                continue
            ts = item.get("ts")
            when = _ago(max(0.0, now - ts)) if isinstance(ts, (int, float)) else "—"
            rows.append((label, when))
            if len(rows) >= limit:
                break
        return rows

    # ── 四区块 ─────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        panel = C.panel(
            self._band(),
            self._section("继续上次", trailing=self._trailing("全部记录", "single")),
            self._continue_rows(),
            self._section("工作入口", note="单篇 → 整期 → 引用库"),
            self._entries(),
            self._section("最近活动", trailing=self._activity_link()),
            self._activity_rows(),
            t=t, pad=(24, 12, 24, 10),
        )
        self._kick_bib_stats()
        return panel

    def _kick_bib_stats(self) -> None:
        """首页数据带要真实引用库条数：首次构建时后台取 bib 统计，取回后就地刷新。

        引用库页是统计的唯一加载方（stats + lint）；首页只借用其结果，避免重复读盘，
        也避免「引用库 —」在启动后长期占位。
        """
        bib = self.app.pages.get("bib")
        if bib is None or bib.stats_data is not None:
            return

        async def _load() -> None:
            try:
                await bib._load_stats()
            except Exception:  # noqa: BLE001 取数失败不阻塞首页
                return
            self._refresh_band()

        self.page.run_task(_load)

    def _refresh_band(self) -> None:
        if getattr(self.app, "current_page_key", "") != self.key:
            return
        try:
            self.root = self.build()
            self.app.content.content = self.root
            self.app.page.update()
        except Exception as exc:  # noqa: BLE001
            self.app.mark("home_band_refresh_error", error=f"{type(exc).__name__}: {exc}")

    def _section(self, title: str, *, trailing=None, note: str | None = None) -> ft.Container:
        t = self.t
        return ft.Container(
            padding=C._pad(t, 0, 18, 0, 8),
            content=C.section_label(title, t, trailing=trailing, note=note),
        )

    def _trailing(self, label: str, page_key: str) -> ft.TextButton:
        return C.text_button(label, lambda e, k=page_key: self.app.goto(k), self.t)

    def _band(self) -> ft.Container:
        """58 高状态带：真实运行时事实，无底色、无卡片、数字不用彩色。"""
        t = self.t
        engine_ok = bool(self.app.engine_status.ok)
        values = (
            ("引擎", "就绪" if engine_ok else "缺失"),
            ("版本", str(self.app.app_version)),
            ("页面", str(len(self.app.pages))),
            ("引用库", self._bib_value()),
        )
        cells: list = []
        for index, (label, value) in enumerate(values):
            if index:
                cells.append(ft.Container(width=t.px(1), height=t.px(28),
                                          bgcolor=t.palette.border))
            cells.append(ft.Container(
                padding=C._pad(t, 14, 0, 14, 0),
                content=ft.Column(spacing=0, alignment=ft.MainAxisAlignment.CENTER,
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                  controls=[
                                      C.text(value, t, role="stat"),
                                      C.text(label, t, "caption", color=t.palette.text_faint),
                                  ]),
            ))
        right = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           C.status_dot("ok" if engine_ok else "error", t),
                           C.text("本地只读 · 数据不外传", t, "caption", t.palette.text_muted),
                           C.text_button("打开设置", lambda e: self.app.goto("settings"), t),
                       ])
        return ft.Container(
            height=t.px(58), border=ft.Border(bottom=C._side(t)),
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[*cells, ft.Container(expand=True), right]),
        )

    def _continue_rows(self) -> ft.Column:
        t = self.t
        recent = self.app.store.get("recent", {}) or {}
        specs = (
            ("单篇", "single_source", "single", "初排转换 / 一校核查"),
            ("整期", "issue_dir", "issue", "目录 / 合刊 / 清理"),
            ("引用库", "bib_out", "bib", "查库 / 入藏 / 规范化"),
        )
        rows: list = []
        for tag, key, target, meta in specs:
            value = str(recent.get(key) or "")
            if not value:
                continue
            rows.append(self._recent_row(tag, _basename(value), meta, target))
        if not rows:
            rows.append(ft.Container(
                padding=C._pad(t, 0, 6, 0, 6),
                content=C.empty_line(
                    "还没有最近记录 — 先打开单篇稿件", t,
                    action=C.text_button("打开单篇", lambda e: self.app.goto("single"), t)),
            ))
        return ft.Column(spacing=0, controls=rows)

    def _recent_row(self, tag: str, title: str, meta: str, target: str) -> ft.Container:
        t = self.t

        def _go(e) -> None:
            if target == "bib":
                self.app.open_bib_view("query", source="home")
            else:
                self.app.goto(target)

        return ft.Container(
            height=t.px(32), on_click=_go, border=ft.Border(bottom=C._side(t)),
            content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Container(width=t.px(40), content=C.text(
                                   tag, t, "caption", color=t.palette.text_faint)),
                               C.text(title, t, "body", expand=True, max_lines=1,
                                      overflow=ft.TextOverflow.ELLIPSIS),
                               C.text(meta, t, "caption", color=t.palette.text_faint),
                               ft.Icon(C.ic("CHEVRON_RIGHT", "NAVIGATE_NEXT"),
                                       size=t.px(16), color=t.palette.text_faint),
                           ]),
        )

    def _entries(self) -> ft.Row:
        """3 列 / 2 列 / 1 列断点，竖发丝线分隔，无卡片底。"""
        t = self.t
        items = (
            ("DESCRIPTION_OUTLINED", "单篇稿件", "初排转换 · 一校核查", "single"),
            ("VIEW_AGENDA_OUTLINED", "整期装配", "目录 · 合刊 · 清理", "issue"),
            ("LIBRARY_BOOKS_OUTLINED", "引用库", "查库 · 入藏 · 规范化", "bib"),
        )
        controls: list = []
        for index, (icon, title, desc, key) in enumerate(items):
            if index:
                controls.append(ft.Container(width=t.px(1), height=t.px(36),
                                             bgcolor=t.palette.border))
            controls.append(ft.Container(
                expand=True, height=t.px(52),
                on_click=lambda e, k=key: (self.app.open_bib_view("query", source="home")
                                           if k == "bib" else self.app.goto(k)),
                padding=C._pad(t, 12, 0, 12, 0),
                content=ft.Row(spacing=t.sp("1"),
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                                   ft.Icon(C.ic(icon, "CIRCLE"), size=t.px(16),
                                           color=t.palette.text_muted),
                                   ft.Column(spacing=0, controls=[
                                       C.text(title, t, role="item-title"),
                                       C.text(desc, t, "caption", color=t.palette.text_faint),
                                   ]),
                                   ft.Container(expand=True),
                                   ft.Icon(C.ic("CHEVRON_RIGHT", "NAVIGATE_NEXT"),
                                           size=t.px(16), color=t.palette.text_faint),
                               ]),
            ))
        return ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=controls)

    def _activity_link(self):
        path = SLOG.log_path()
        if path is None or not Path(path).exists():
            return None
        return C.text_button("打开日志", lambda e: self.app.open_path(str(path)), self.t)

    def _activity_rows(self) -> ft.Column:
        t = self.t
        rows = self._activity()
        if not rows:
            return ft.Column(spacing=0, controls=[ft.Container(
                padding=C._pad(t, 0, 4, 0, 4),
                content=C.empty_line(
                    "运行一次任务后在这里回看", t,
                    action=C.text_button("打开引用库",
                                         lambda e: self.app.goto("bib"), t)),
            )])
        controls = []
        for label, when in rows:
            controls.append(ft.Container(
                height=t.px(30), border=ft.Border(bottom=C._side(t)),
                content=ft.Row(spacing=t.sp("1"),
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                                   C.text(label, t, "body", expand=True),
                                   C.text(when, t, "caption", color=t.palette.text_faint),
                               ]),
            ))
        return ft.Column(spacing=0, controls=controls)


# ── 单篇稿件 ───────────────────────────────────────────────────────────────
class SinglePage(BasePage):
    key = "single"

    FLOWS = {
        "convert": {
            "label": "初排转换",
            "icon": "DESCRIPTION_OUTLINED",
            "desc": "作者来稿（LaTeX / Word）→ 符合本刊规范的可编译初排 tex（可顺带编译 PDF、清理临时文件）",
        },
        "proofcheck": {
            "label": "一校核查",
            "icon": "SEARCH",
            "desc": "对初排后的 tex 做一校核查，输出核查报告（--fix 另写修正稿）",
        },
    }

    def __init__(self, app) -> None:
        super().__init__(app)
        saved_flow = app.store.get("single_flow", "convert")
        self.flow_key = saved_flow if saved_flow in self.FLOWS else "convert"
        self._flow_box = ft.Column(spacing=12)
        self._log = None
        self._result = None
        self._run_btn = None
        self._stop_btn = None
        self._ai = None
        # 批 114（用户 m03256）：二级视图进导航 → 页头=面包屑 + 子项标题，不再有 chip 行。
        self._crumb = None
        self._child_title = None
        self._flow_desc = None
        self._norm_hinted = False
        self._meta_panel = None
        self._meta_temp = ""

    # ── 构建 ───────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=200, title="运行日志", dock=True,
                                toast=self.app.toast, mark=self.mark)
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._proof = CK.single_card(self)
        self._run_btn = C.primary_button("运行", self._on_run, t, icon="PLAY_ARROW",
                                         disabled=not self.app.engine_status.ok)
        self._stop_btn = C.secondary_button("停止", self._on_stop, t, icon="STOP", disabled=True)
        self._sync_header()
        header = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Column(spacing=t.sp("xs"), expand=True,
                                      controls=[self._crumb, self._child_title, self._flow_desc]),
                            ft.Row(spacing=t.sp("1"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                   controls=[self._stop_btn, self._run_btn]),
                        ])
        self._flow_box.controls = [self._flow_builder(self.flow_key)()]
        self._proof.root.visible = False
        self._norm_jump_btn = C.secondary_button(
            "去引用库 → 规范化", self._goto_normalize, t, icon="FACT_CHECK")
        status = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            C.busy_bar(t, visible=False),
                            ft.Container(expand=True),
                            self._norm_jump_btn,
                        ])
        self._busy = status.controls[0]
        body = C.page_body(header, status, self._flow_box, self._result.root,
                           self._proof.root, t=t, gap=10,
                           overlay=self._log.dock_root, overlay_reserve=64)
        self.refresh_running()
        return body

    def _flow_builder(self, key: str):
        """按 flow_key 选构建器（重建 shell 后恢复上次步骤也走这里）。"""
        return {
            "convert": self._build_convert,
            "proofcheck": self._build_proofcheck,
        }.get(key, self._build_convert)

    def _sync_header(self) -> None:
        """页头=面包屑「单篇稿件 / 子项」+ 22/600 子项标题（批 114 去 chip 行）。"""
        t = self.t
        flow = self.FLOWS[self.flow_key]
        if self._crumb is None:
            self._crumb = C.text("单篇稿件", t, "caption", t.palette.text_muted)
        if self._child_title is None:
            self._child_title = C.text(flow["label"], t, role="page-title")
        if self._flow_desc is None:
            self._flow_desc = C.text(flow["desc"], t, "small", color=t.palette.text_muted)
        self._child_title.value = flow["label"]
        self._flow_desc.value = flow["desc"]

    def activate(self, view: str, *, source: str = "nav") -> None:
        """导航切到 convert/proofcheck（本页唯一二级视图入口）。"""
        if view not in self.FLOWS:
            return
        if self.app.running and view != getattr(self, "flow_key", ""):
            self.app.toast("运行中不能切换步骤，先停止")
            return
        self.flow_key = view
        self.app.store.set("single_flow", view)
        self._ai = None
        if getattr(self, "_flow_box", None) is not None:
            self._flow_box.controls = [self._flow_builder(view)()]
            if self._result is not None:
                self._result.reset()
            CK.on_flow_switch(self)
        self._sync_header()
        self._safe_update(self._flow_box, self._crumb, self._child_title, self._flow_desc)
        self.mark("single_flow", flow=view, source=source)

    def _switch_flow(self, key: str) -> None:
        """兼容旧调用（GUI 钩子/老测试）：与导航共用一条路由。"""
        if key not in self.FLOWS:
            return
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"single:{key}", source="page")
            return
        self.activate(key, source="page")

    # ── 三个步骤的输入卡 ────────────────────────────────────────────────
    def _build_convert(self) -> ft.Control:
        t, p = self.t, self.t.palette
        options = self.app.store.get("options", {}) or {}
        rows = self._option_tiles(options)
        advanced = self._advanced_params()
        # 元数据表单：默认展开 + 扁平箭头（用户 m02696：不要灰底展开按钮）。
        metadata = C.CollapsibleSection(
            t, title="元数据（可选）", content=self._build_metadata_section(),
            expanded=True,
        )
        self._meta_section = metadata
        # 用户 m02855：输入区不再让字段直接躺在纸灰页面上 → 与「高级参数」同材质的白卡片。
        input_card = C.card(ft.Column(spacing=t.sp("2"), controls=[
            # 必填语义已进每个输入框的 label（「源稿件（必填）」），不再另起一行提示。
            self.add_picker("源稿件", "single_source",
                            "作者来稿 .tex / .docx", kind="file"),
            self.add_picker("输出根目录", "single_out",
                            "产物写到该目录下的 <作者>/ 里", kind="dir"),
        ]), t, pad=14)
        return ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("输入", t),
            input_card,
            C.section_title("处理选项", t, note="默认勾选常用项"),
            rows,
            self._advanced_title(),
            advanced,
            metadata.root,
            C.text(SS.bibdb_convert_hint(self.app.store.data), t, "caption", p.text_faint),
            self._ai_section(),
        ])

    def _ai_section(self) -> ft.Control:
        self._ai = AIP.AiPanel(self)
        return self._ai.build()

    def _build_metadata_section(self) -> ft.Control:
        t = self.t
        self._meta_panel = MF.MetadataPanel(self)
        return ft.Column(spacing=t.sp("1"), controls=[
            self._meta_panel.build(),
            ft.Row(spacing=t.sp("1"), controls=[
                # 用户 m02990：预览是明确动作 → 描边按钮（仅「运行」用主色实心）。
                C.secondary_button("预览元数据", self._on_metadata_preview, t, icon="PREVIEW"),
            ]),
        ])

    def _build_proofcheck(self) -> ft.Control:
        return CK.proofcheck_section(self)

    def _adv_input(self, key: str, label: str, hint: str, *, input_filter=None,
                   validator=None) -> ft.Control:
        """高级参数单行输入：描边浮动标签 + 输入过滤 + 即时格式校验（m02483）。

        批 114 用户 m03131：清空 ✕ 已下沉到 `floating_field` 默认能力（有值才显示，
        点击清空并触发下面的 on_change）；页面不再手工挂 suffix，避免出现两个 ✕。
        """
        def _changed(e) -> None:
            value = (e.control.value or "").strip()
            self.app.store.set(f"form.{key}", value)
            if validator is None:
                return
            error = validator(value)
            if e.control.error != error:
                e.control.error = error
                try:
                    e.control.update()
                except Exception:  # noqa: BLE001 构建期控件可能未挂载
                    pass

        control = C.floating_field(
            self.t, label, value=self.app.store.get(f"form.{key}", "") or "",
            hint=hint, on_change=_changed, input_filter=input_filter,
            on_clear=lambda k=key: self._mark_adv_cleared(k),
        )
        self.fields[key] = [control]
        return control

    # ── 高级参数：单项清除 / 一键清空（用户 m03074；m03131 下沉到 helper）──
    def _mark_adv_cleared(self, key: str) -> None:
        self.app.mark("advanced_field_cleared", field=key)

    def _reset_adv_value(self, key: str) -> None:
        """把单个文本键清空（控件值/error/store 同步），不单独打 mark。"""
        ref = self.fields.get(key)
        if not ref:
            return
        control = ref[0]
        control.value = ""
        control.error = None
        self.app.store.set(f"form.{key}", "")
        C.sync_clearable(control)
        try:
            control.update()
        except Exception:  # noqa: BLE001
            pass

    def _clear_adv_field(self, key: str) -> None:
        self._reset_adv_value(key)
        self.app.mark("advanced_field_cleared", field=key)

    def _advanced_title(self) -> ft.Control:
        t = self.t
        return ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                C.text("高级参数", t, "section"),
                ft.Container(expand=True),
                C.secondary_button("清空高级参数", self._clear_advanced_params, t,
                                   icon="CLEAR_ALL"),
            ],
        )

    def _clear_advanced_params(self, e=None) -> None:
        for key in _ADVANCED_TEXT_KEYS:
            self._reset_adv_value(key)
        self._reset_lang_value()
        self._reset_editor_value()
        self.app.mark("advanced_params_cleared",
                      count=len(_ADVANCED_TEXT_KEYS) + len(_ADVANCED_SELECT_KEYS))
        self.app.toast("已清空高级参数（处理选项与元数据未动）")

    def _adv_path_input(self, key: str, label: str, hint: str, *, kind: str) -> ft.Control:
        """引擎附加文件：路径输入 + 「浏览…」（bib/refs/sty 选文件、skeleton 选目录）。

        复用 path_picker 的版式（描边浮动标签 + 同行等高的描边按钮）；标签带「（可空）」，
        输入方式统一为「可粘贴路径（⌘V）或点『浏览…』」——flet 1.0.5 没有系统级文件
        拖放 API，所以不实现拖拽（见证据 impl-notes.md）。
        """
        t = self.t

        def _changed(e) -> None:
            value = (e.control.value or "").strip()
            self.app.store.set(f"form.{key}", value)

        def _browse(e) -> None:
            self.app.browse_field(self.fields[key], f"选择{label}", kind, key)

        field = C.floating_field(
            t, f"{label}（可空）",
            value=self.app.store.get(f"form.{key}", "") or "",
            hint=hint or "可直接粘贴路径（⌘V），或点「浏览…」选择",
            on_change=_changed, expand=True,
            on_clear=lambda k=key: self._mark_adv_cleared(k),
        )
        self.fields[key] = [field]
        browse = C.secondary_button("浏览…", _browse, t, icon="FOLDER_OPEN")
        # 浮动标签输入框 48 → 浏览按钮跟随 48（用户 m02816：不能高矮不齐）。
        return ft.Column(spacing=t.sp("half"), controls=[
            C.field_row(field, browse, t),
        ])

    def _advanced_params(self) -> ft.Control:
        """高级参数：分组表单（描边浮动标签 + 输入约束 + 可自定义下拉，用户 m02483）。"""
        t = self.t
        cells_issue = [
            ft.Container(expand=True, content=self._adv_input(
                "convert_vol", "卷", "如 21", input_filter=_DIGITS_FILTER, validator=_valid_digits)),
            ft.Container(expand=True, content=self._adv_input(
                "convert_issue", "期", "如 3", input_filter=_DIGITS_FILTER, validator=_valid_digits)),
            ft.Container(expand=True, content=self._adv_input(
                "convert_year", "年份", "如 2025", input_filter=_DIGITS_FILTER, validator=_valid_year)),
            ft.Container(expand=True, content=self._adv_input(
                "convert_pages", "页码", "如 137-152", input_filter=_PAGES_FILTER, validator=_valid_pages)),
        ]
        cells_info = [
            ft.Container(expand=True, content=self._adv_input("convert_author", "作者", "如 周礼全")),
            ft.Container(expand=True, content=self._adv_input("convert_name", "篇名", "稿件标题")),
            ft.Container(expand=True, content=self._adv_input(
                "convert_received", "收稿日期", "如 2025-09-19",
                input_filter=_DATE_FILTER, validator=_valid_date)),
            self._lang_select(),
            self._editor_select(),
            ft.Container(expand=True, content=self._adv_input("convert_grants", "基金", "如 20BZX001")),
        ]
        cells_paths = [
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_bib", "参考文献 .bib", "如 refs.bib", kind="file")),
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_refs", "附加参考文献 --refs", "如 extra.bib", kind="file")),
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_sty", "样式文件 --sty", "如 SLCN-MIX.sty", kind="file")),
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_skeleton", "模板骨架 --skeleton", "如 templates/slcn", kind="dir")),
        ]
        wide = self._design_w() >= 1120
        blocks: list[ft.Control] = []
        for title, cells, cols in (
            ("刊期与页码", cells_issue, 4),
            ("稿件信息", cells_info, 3),
            ("引擎附加文件（均可空 · 可直接粘贴路径或点「浏览…」）", cells_paths, 2),
        ):
            use = cols if wide else min(cols, 2)
            rows: list[ft.Control] = []
            for i in range(0, len(cells), use):
                row_cells = list(cells[i:i + use])
                while len(row_cells) < use:
                    row_cells.append(ft.Container(expand=True))
                rows.append(ft.Row(spacing=t.px(12), controls=row_cells))
            blocks.append(ft.Column(spacing=t.px(10), controls=[
                C.text(title, t, "caption", t.palette.text_muted), *rows,
            ]))
        return C.card(ft.Column(spacing=t.px(16), controls=blocks), t, pad=14)

    # ── 语种 / 责编下拉（用户 m02483；m03131 reset 下沉到 C.select_box）────
    def _lang_select(self) -> ft.Control:
        t = self.t
        current = (self.app.store.get("form.convert_lang", "") or "").strip()
        labels = {"": "自动判断", "zh": "中文（SLCN-MIX.sty）", "en": "英文（SLEN.sty）"}
        box = C.select_box(
            t, "语种", value_text=labels.get(current, "自动判断"),
            options=[("", "自动判断"), ("zh", "中文（SLCN-MIX.sty）"), ("en", "英文（SLEN.sty）")],
            on_select=self._on_lang_select, expand=True,
            reset=True, reset_text="自动判断", on_reset=self._reset_lang,
        )
        self._lang_box = box
        return box

    def _on_lang_select(self, key: str, label: str) -> None:
        self.app.store.set("form.convert_lang", key)
        self.app.mark("lang_select", value=key)

    def _reset_lang_value(self) -> None:
        self.app.store.set("form.convert_lang", "")
        box = getattr(self, "_lang_box", None)
        if box is not None and getattr(box, "_value_text", None) is not None:
            box._value_text.value = "自动判断"
            sync = getattr(box, "_sync_reset", None)
            if sync is not None:
                sync()
            try:
                box._value_text.update()
            except Exception:  # noqa: BLE001
                pass

    def _reset_lang(self, e=None) -> None:
        self.app.store.set("form.convert_lang", "")
        self.app.mark("advanced_field_reset", field="convert_lang")

    def _editor_options(self) -> list[str]:
        raw = self.app.store.get("form.editor_options", [])
        if isinstance(raw, list):
            return [str(name) for name in raw if str(name).strip()]
        return []

    def _editor_select(self) -> ft.Control:
        t = self.t
        current = (self.app.store.get("form.convert_editor", "") or "").strip()
        options = [("", "未指定")]
        options += [(name, name) for name in self._editor_options()]
        options += [("__custom__", "自定义…"), ("__manage__", "编辑名单…")]
        box = C.select_box(
            t, "责编", value_text=current or "未指定", options=options,
            on_select=self._on_editor_select, expand=True,
            reset=True, reset_text="未指定", on_reset=self._reset_editor,
        )
        self._editor_box = box
        return box

    def _reset_editor_value(self) -> None:
        self.app.store.set("form.convert_editor", "")
        self._set_editor_box_text("未指定")

    def _reset_editor(self, e=None) -> None:
        self.app.store.set("form.convert_editor", "")
        self.app.mark("advanced_field_reset", field="convert_editor")

    def _on_editor_select(self, key: str, label: str):
        if key == "__custom__":
            self._open_editor_add_dialog()
            return False       # 显示文字保持原值，由对话框确认后再写入
        if key == "__manage__":
            self._open_editor_manage_dialog()
            return False
        self.app.store.set("form.convert_editor", key)
        self.app.mark("editor_select", value=key)

    def _set_editor_box_text(self, label: str) -> None:
        box = getattr(self, "_editor_box", None)
        if box is not None and getattr(box, "_value_text", None) is not None:
            box._value_text.value = label
            sync = getattr(box, "_sync_reset", None)
            if sync is not None:
                sync()
            try:
                box._value_text.update()
            except Exception:  # noqa: BLE001
                pass

    def _dialog_title(self, title: str) -> ft.Text:
        return C.text(title, self.t, role="item-title")

    def _open_editor_add_dialog(self) -> None:
        t = self.t
        page = self.app.page
        name = C.floating_field(t, "新责编姓名", hint="如 执子")

        def _close() -> None:
            page.pop_dialog()

        def _add(e) -> None:
            value = (name.value or "").strip()
            if not value:
                name.error = "请输入责编姓名"
                try:
                    name.update()
                except Exception:  # noqa: BLE001
                    pass
                return
            options = self._editor_options()
            if value not in options:
                options.append(value)
                self.app.store.set("form.editor_options", options)
            self.app.store.set("form.convert_editor", value)
            self.app.mark("editor_added", value=value)
            self._set_editor_box_text(value)
            _close()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=self._dialog_title("添加责编"),
            content=ft.Container(width=t.px(320), content=ft.Column(spacing=t.px(8), controls=[name])),
            actions=[
                C.text_button("取消", lambda e: _close(), t),
                C.primary_button("添加", _add, t),
            ],
        ))

    def _open_editor_manage_dialog(self) -> None:
        t = self.t
        page = self.app.page
        area = C.text_field(t, value="\n".join(self._editor_options()), multiline=True,
                            min_lines=6, max_lines=10, hint_text="每行一个责编姓名")
        area.height = None

        def _close() -> None:
            page.pop_dialog()

        def _save(e) -> None:
            names = [line.strip() for line in (area.value or "").splitlines() if line.strip()]
            self.app.store.set("form.editor_options", names)
            self.app.mark("editor_options_saved", count=len(names))
            _close()
            self.app.rebuild_shell()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=self._dialog_title("编辑责编名单"),
            content=ft.Container(width=t.px(380), content=ft.Column(spacing=t.px(8), controls=[
                C.text("每行一个姓名；末轮采集为可选辅助", t, "small", t.palette.text_muted),
                area,
            ])),
            actions=[
                C.text_button("取消", lambda e: _close(), t),
                C.primary_button("保存", _save, t),
            ],
        ))

    def _design_w(self) -> float:
        """布局断点用「设计宽」：优先已设窗口宽（window_w/k），构建期 page.width 可能还是 800。"""
        try:
            width = float(getattr(self.app, "window_w", 0) or 0)
        except (TypeError, ValueError):
            width = 0.0
        k = max(float(getattr(self.t, "k", 1.0) or 1.0), 1e-6)
        if width > 0:
            return width / k
        try:
            return float(self.app._design_width())
        except (AttributeError, TypeError, ValueError):
            return 1240.0  # 测试 FakeApp：按 1240 基准宽处理

    def _option_tiles(self, options: dict) -> ft.Control:
        """处理选项 2×2 卡片网格（窄宽降为单列）；替代原先一列四行的紧凑列表。"""
        t = self.t
        specs = (
            ("编译 PDF", "调用 xelatex 生成正文 PDF（长任务，日志实时滚动）",
             "PICTURE_AS_PDF_OUTLINED", "convert_compile", True, "主要"),
            ("清理临时文件", "删除 .aux / .log / .out 等中间文件",
             "CLEANING_SERVICES", "convert_clean", False, None),
            ("导出原稿 PDF", "保留原始稿件并另存一份 PDF 备查",
             "PICTURE_AS_PDF_OUTLINED", "convert_source_pdf", False, None),
            ("强制覆盖（--force）", "产物已存在时仍重跑并覆盖（谨慎）",
             "WARNING_AMBER_ROUNDED", "convert_force", False, None),
        )
        tiles = [
            C.option_tile(label, desc, t, icon=icon, badge_label=badge_label,
                          checked=bool(options.get(key, default)),
                          on_change=lambda e, k=key: self.app.set_option(k, e.control.value))
            for label, desc, icon, key, default, badge_label in specs
        ]
        if self._design_w() >= 1120:
            rows = []
            for index in range(0, len(tiles), 2):
                pair = tiles[index:index + 2]
                if len(pair) == 1:
                    pair = [*pair, ft.Container(expand=True)]
                rows.append(ft.Row(spacing=t.px(12), controls=pair))
            return ft.Column(spacing=t.px(12), controls=rows)
        return ft.Column(spacing=t.px(8),
                         controls=[ft.Row(controls=[tile]) for tile in tiles])

    # ── 运行 ───────────────────────────────────────────────────────────
    def _on_run(self, e=None) -> None:
        if self.app.running:
            return
        try:
            call = self._build_call()
        except ValueError as exc:
            self.app.toast(str(exc))
            self.mark("validation_error", reason=str(exc))
            return
        except RuntimeError as exc:
            self.app.toast(str(exc))
            return
        self.app.mark("form_submit", flow=self.flow_key, call=call.to_dict())
        self.page.run_task(self._run, call)

    def _goto_normalize(self, e=None) -> None:
        """跳「引用库 → 规范化」（唯一跨页入口在 App.open_bib_view，source=single 供冒烟断言）。"""
        self.app.open_bib_view("normalize", source="single")
        if not self._norm_hinted:
            self._norm_hinted = True
            self.app.toast("已切到「引用库 → 规范化」")

    def _on_metadata_preview(self, e=None) -> None:
        """CV-002/003：跑与「开始转换」同一 argv，只多 --metadata-preview-out（只读）。"""
        if self.app.running:
            return
        preview_path = os.path.join(tempfile.gettempdir(),
                                    f"slcn_metadata_preview_{os.getpid()}.json")
        try:
            call = self._build_call(preview_path=preview_path)
        except ValueError as exc:
            self.app.toast(str(exc))
            return
        self.app.mark("metadata_preview", call=call.to_dict())
        self.page.run_task(self._run, call, None, preview_path)

    def _on_stop(self, e=None) -> None:
        self.app.stop_run()

    def _build_call(self, preview_path: str = "") -> EB.EngineCall:
        if self.flow_key == "convert":
            source = self.field("single_source")
            out = self.field("single_out")
            if not source:
                raise ValueError("请先选择源稿件")
            if not out:
                raise ValueError("请先选择输出根目录")
            options = self.app.store.get("options", {}) or {}
            kwargs = dict(
                source=source, out=out,
                compile_pdf=bool(options.get("convert_compile", True)),
                clean=bool(options.get("convert_clean", False)),
                source_pdf=bool(options.get("convert_source_pdf", False)),
                vol=self.field("convert_vol"), issue=self.field("convert_issue"),
                year=self.field("convert_year"), pages=self.field("convert_pages"),
                received=self.field("convert_received"), author=self.field("convert_author"),
                name=self.field("convert_name"), bib=self.field("convert_bib"),
                lang=self.field("convert_lang"), editor=self.field("convert_editor"),
                grants=self.field("convert_grants"), refs=self.field("convert_refs"),
                sty=self.field("convert_sty"), skeleton=self.field("convert_skeleton"),
                force=bool(options.get("convert_force", False)),
                metadata_preview_out=preview_path,
            )
            if self._meta_panel is not None:
                # 人工元数据：写临时 JSON（运行结束删除），argv 里是 --metadata-json。
                self._cleanup_meta_temp()
                self._meta_temp, _text = self._meta_panel.payload_for_run()
                kwargs["metadata_json"] = self._meta_temp
            if self._ai is not None:
                # 批 109 W1b（R6）：引用库设置只在核实链路（AI 面板 → verify_refs）
                # 进 argv；普通初排没有核实入口，绝不注入 --verify-plan（否则会变成只算账）。
                bibdb = SS.bibdb_convert_args(self.app.store.data)
                return self._ai.build_call(**kwargs, **bibdb)
            return EB.convert_call(**kwargs)
        tex = self.field("proof_tex")
        if not tex:
            raise ValueError("请先选择初排 tex")
        options = self.app.store.get("options", {}) or {}
        return EB.proofcheck_call(
            tex=tex, lang=self.field("proof_lang"),
            fix=bool(options.get("proofcheck_fix", False)),
        )

    async def _run(self, call: EB.EngineCall, stdin_text: str | None = None,
                   preview_path: str = "") -> None:
        page = self.page
        self.app.begin_run(call)
        self._result.reset()
        self._log.clear()
        self._log.expand()      # 运行即展开底部日志浮层（VSCode 输出面板式）
        CK.before_run(self)
        CK.on_proof_log(self)
        panel = self._ai if self.flow_key == "convert" else None
        if panel is not None:
            panel.reset_run()
        try:
            code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                     api=panel.api_env() if panel is not None else None,
                                     notes=self.app.notes_repo, stdin_text=stdin_text)
        except Exception as exc:  # noqa: BLE001 - 引擎缺失/启动失败一律给中文提示
            self.app.finish_run(call, -1)
            text = f"❌ 任务启动失败：{exc}"
            self._result.show(text, level="error")
            self.app.toast(f"任务启动失败：{exc}")
            self.mark("run_start_error", error=f"{type(exc).__name__}: {exc}")
            return
        self.app.finish_run(call, code)
        level = "warn" if self.app.cancel_requested else EB.result_level(call.entry, code)
        text = "⏹ 已取消" if self.app.cancel_requested else EB.result_text(call.entry, code)
        if panel is not None:
            notice = panel.budget_notice()
            if notice:
                text, level = notice, "error"
            panel.after_run(call, code)
        artifact = EB.artifact_path(self.app.last_line)
        text, level = CK.after_proof_run(self, call, code, artifact, text, level)
        if preview_path:
            self._show_metadata_preview(preview_path)
            if not self.app.cancel_requested:
                text, level = "元数据预览完成（只读：未建产物目录、未写产物）", "info"
                artifact = ""
        self._result.show(text, level=level, artifact=artifact)
        if artifact:
            self.app.store.set_recent(self._artifact_recent_key(), artifact)
        self._cleanup_meta_temp()
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

    def _show_metadata_preview(self, path: str) -> None:
        payload = None
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = None
        MF.remove_temp(path)
        if self._meta_panel is not None:
            self._meta_panel.show_preview(payload)

    def _cleanup_meta_temp(self) -> None:
        if self._meta_temp:
            MF.remove_temp(self._meta_temp)
            self._meta_temp = ""

    def _artifact_recent_key(self) -> str:
        return {
            "convert": "single_out",
            "proofcheck": "proof_tex",
        }[self.flow_key]

    def _on_line(self, line: str) -> None:
        self.app.record_line(line)
        summary = EB.is_summary_line(line)
        self._log.add_line(line, _level_of_line(line), summary)
        if self._ai is not None and self.flow_key == "convert":
            self._ai.on_line(line)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    # ── 运行态刷新（由 App 调） ─────────────────────────────────────────
    def refresh_running(self) -> None:
        running = self.app.running
        if self._run_btn is not None:
            self._run_btn.disabled = running or not self.app.engine_status.ok
        if self._stop_btn is not None:
            self._stop_btn.disabled = not running
        if getattr(self, "_busy", None) is not None:
            self._busy.visible = running
        if self._ai is not None:
            self._ai.refresh_running()
        self._safe_update(*(c for c in (self._run_btn, self._stop_btn, getattr(self, "_busy", None)) if c is not None))

    # 证据钩子 ─────────────────────────────────────────────────────────
    def prefill(self, *, flow: str = "convert", source: str = "", out: str = "", bib: str = "",
                proof_tex: str = "") -> None:
        if flow in self.FLOWS:
            self.flow_key = flow
        self.set_field("single_source", source)
        if out:
            self.set_field("single_out", out)
        if bib:
            self.set_field("bib_returned", bib)
        if proof_tex:
            self.set_field("proof_tex", proof_tex)
        self.mark("demo_prefill", flow=self.flow_key, source=source, out=out, bib=bib)

    def run_demo(self) -> None:
        self._on_run()


# ── 整期装配 ───────────────────────────────────────────────────────────────
class IssuePage(BasePage):
    key = "issue"

    STEPS = [
        {"key": "infocheck", "title": "检查tex信息", "icon": "FACT_CHECK_OUTLINED",
         "desc": "扫描整期 tex，输出 10 列清单（篇目/页码/字号行距…）"},
        {"key": "toc", "title": "生成目录", "icon": "TOC", "desc": "用信息检查报告生成目录 tex（可落位编译）"},
        {"key": "compileall", "title": "编译全部", "icon": "PICTURE_AS_PDF_OUTLINED",
         "desc": "逐篇 xelatex→biber→xelatex×2；期目录形如 20xx年第N期(…)，同名 PDF 直接覆盖（跳过 000 目录）"},
        {"key": "merge", "title": "合并文件", "icon": "MERGE", "desc": "把各篇 PDF 合为一期合刊"},
        {"key": "cleantemp", "title": "删除临时文件", "icon": "CLEANING_SERVICES",
         "desc": "三档清理：辅助文件 / 交付收尾 / 工作区（危险）"},
        {"key": "dirpush", "title": "目录摘要推送", "icon": "PUSH_PIN_OUTLINED",
         "desc": "按刊期生成当期目录摘要 docx（可只预演）"},
    ]

    def __init__(self, app) -> None:
        super().__init__(app)
        # 批 114（用户 m03256）：父页 = 流水线总览，每步 = 独立子页；"overview" 表示父页。
        self.selected = "overview"
        self._content_box = ft.Column(spacing=10)
        self._params_box = ft.Column(spacing=10)
        self._log = None
        self._result = None
        self._info = None
        self._run_btn = None
        self._stop_btn = None
        self._run_all_btn = None
        self._step_run_btns: dict[str, ft.Control] = {}
        self._status: dict[str, str] = {step["key"]: "idle" for step in self.STEPS}
        self._crumb = None
        self._child_title = None
        self._flow_desc = None

    def _sync_header(self) -> None:
        t = self.t
        step = self._step_by_key(self.selected)
        if step is None:
            page_label, child = "整期装配", "流水线总览"
            desc = "一期稿件从检查到合刊是一条固定流水线：一屏看得到进度、当前步骤和日志。"
        else:
            page_label, child, desc = "整期装配", str(step["title"]), str(step["desc"])
        if self._crumb is None:
            self._crumb = C.text("整期装配", t, "caption", t.palette.text_muted)
        if self._child_title is None:
            self._child_title = C.text(child, t, role="page-title")
        if self._flow_desc is None:
            self._flow_desc = C.text(desc, t, "small", color=t.palette.text_muted)
        self._crumb.value = page_label
        self._child_title.value = child
        self._flow_desc.value = desc

    def _step_by_key(self, key: str) -> dict | None:
        for step in self.STEPS:
            if step["key"] == key:
                return step
        return None

    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="整期日志",
                                toast=self.app.toast, mark=self.mark)
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._info = CK.issue_card(self)
        disabled = not self.app.engine_status.ok
        self._run_all_btn = C.primary_button("运行整期流水线", lambda e: self._run_all(),
                                            t, icon="PLAY_ARROW", disabled=disabled)
        self._stop_btn = C.secondary_button("取消", self._on_stop, t, icon="STOP", disabled=True)
        self.selected = "overview"
        self._sync_header()
        header = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Column(spacing=t.sp("xs"), expand=True,
                                      controls=[self._crumb, self._child_title, self._flow_desc]),
                            ft.Row(spacing=t.sp("1"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                   controls=[self._stop_btn, self._run_all_btn]),
                        ])
        self._issue_picker = self.add_picker("期目录", "issue_dir", "整期根目录（扉页/000/01 作者…）", kind="dir")
        self._step_run_btns = {}
        self._content_box.controls = [self._build_overview()]
        body = C.page_body(header, self._content_box, t=t, gap=10)
        self.refresh_running()
        return body

    # ── 父页：流水线总览 ───────────────────────────────────────────────
    def _build_overview(self) -> ft.Control:
        t = self.t
        rows = [self._overview_step_row(i, step) for i, step in enumerate(self.STEPS)]
        left = ft.Column(expand=5, spacing=t.sp("2"), controls=[
            C.card(ft.Column(spacing=t.sp("2"), controls=[
                C.section_title("期目录", t, note="一次运行会按顺序执行 6 步"),
                self._issue_picker,
            ]), t),
            C.card(ft.Column(spacing=8, controls=[
                C.section_title("流水线总览", t, note="点任一步进入该步参数"),
                *rows,
            ]), t),
        ])
        right = ft.Column(expand=6, spacing=t.sp("2"), controls=[
            self._result.root, self._info.root, self._log.root,
        ])
        return ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                      controls=[left, right])

    def _overview_step_row(self, index: int, step: dict) -> ft.Container:
        t = self.t
        key = step["key"]
        state = self._status.get(key, "idle")
        color = {"done": t.palette.success, "running": t.palette.accent,
                 "warn": t.palette.warn, "error": t.palette.error}.get(state, t.palette.text_faint)
        return ft.Container(
            border=C.box(t), border_radius=t.radius("md"),
            padding=C._pad(t, 10, 8, 10, 8),
            on_click=lambda e, k=key: self._select(k),
            content=ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                ft.Container(width=t.px(24), height=t.px(24), border_radius=t.radius("pill"),
                             bgcolor=t.palette.surface_2, alignment=ft.Alignment.CENTER,
                             content=C.text(str(index + 1), t, "small",
                                            t.palette.text_muted, "semibold")),
                ft.Icon(C.ic(step["icon"], "CIRCLE"), size=t.px(16), color=color),
                ft.Column(spacing=0, expand=True, controls=[
                    C.text(str(step["title"]), t, role="body", max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                    C.text(str(step["desc"]), t, "caption", t.palette.text_faint,
                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ]),
                C.status_chip(STATUS_TEXT.get(state, "待运行"), t, state),
                self._step_run_button(step),
            ]),
        )

    # ── 子页：单步（进度带 + 参数 + 运行本步 + 日志）────────────────────
    def _build_step_page(self, key: str) -> ft.Control:
        t = self.t
        step = self._step_by_key(key) or self.STEPS[0]
        self._params_box.controls = [self._build_params(key)]
        self._step_run_btns = {}
        run_btn = self._step_run_button(step)
        back = C.text_button("← 流水线总览", lambda e: self.app.navigate(
            "issue:overview", source="page"), t)
        params = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title(str(step["title"]), t, note=str(step["desc"])),
            self._params_box,
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[ft.Container(expand=True), back, run_btn]),
        ]), t)
        return ft.Column(spacing=t.sp("2"), controls=[
            self._progress_band(),
            params,
            self._result.root,
            self._log.root,
            self._info.root,
        ])

    def _progress_band(self) -> ft.Control:
        """页顶常驻 54 高 6 段进度带：点段可直接切步骤（用户 m03256 规格②）。"""
        t = self.t
        segs: list = []
        for i, step in enumerate(self.STEPS):
            state = self._status.get(step["key"], "idle")
            active = step["key"] == self.selected
            tone_color = {"done": t.palette.success, "running": t.palette.accent,
                          "warn": t.palette.warn, "error": t.palette.error}.get(
                              state, t.palette.text_faint)
            segs.append(ft.Container(
                expand=True, bgcolor=t.palette.accent_soft if active else None,
                border_radius=t.radius("sm"), padding=C._pad(t, 8, 4, 8, 4),
                on_click=lambda e, k=step["key"]: self._select(k),
                content=ft.Column(spacing=0, alignment=ft.MainAxisAlignment.CENTER,
                                  controls=[
                    ft.Row(spacing=t.sp("half"), controls=[
                        C.status_dot("running" if state == "running" else
                                     ("error" if state == "error" else
                                      ("ok" if state == "done" else "warn")), t),
                        C.text(f"{i + 1}. {step['title']}", t, "small",
                               t.palette.text if active else t.palette.text_muted,
                               weight="medium" if active else "regular", max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS),
                    ]),
                    C.status_chip(STATUS_TEXT.get(state, "待运行"), t, state),
                ]),
            ))
        return ft.Container(
            height=t.px(54), padding=C._pad(t, 8, 6, 8, 6),
            bgcolor=t.palette.surface, border=C.box(t),
            border_radius=t.radius("md"),
            content=ft.Row(spacing=t.sp("1"), controls=segs),
        )

    def activate(self, view: str, *, source: str = "nav") -> None:
        """导航激活：overview=父页总览，6 个步骤 key=子页。"""
        step = self._step_by_key(view) if view not in ("", "overview") else None
        if view not in ("", "overview") and step is None:
            return
        if self.app.running and view != getattr(self, "selected", ""):
            self.app.toast("运行中不能切换步骤，先取消")
            return
        self.selected = "overview" if step is None else str(step["key"])
        if getattr(self, "_content_box", None) is not None:
            self._step_run_btns = {}
            self._sync_header()
            self._content_box.controls = ([self._build_overview()] if step is None
                                          else [self._build_step_page(self.selected)])
            self.refresh_running()
            self._safe_update(self._crumb, self._child_title, self._flow_desc,
                              self._content_box)
        self.mark("issue_activate", step=self.selected, source=source)

    # ── 步骤按钮 / 旧入口兼容 ─────────────────────────────────────────
    def _step_run_state(self) -> tuple[bool, str | None]:
        """步骤「运行」的 disabled 态与原因（用户 m02990：不可用时必须说明原因）。"""
        if not self.app.engine_status.ok:
            return True, "引擎未就绪：先到「设置 → 引擎」检查后再运行"
        if self.app.running:
            return True, "运行中：等当前任务结束，或先点右上角「取消」"
        return False, None

    def _step_run_button(self, step: dict) -> ft.OutlinedButton:
        """步骤「运行」= 明确的描边按钮（全屏主色只留给「运行整期流水线」）。"""
        t = self.t
        disabled, reason = self._step_run_state()
        btn = C.secondary_button("运行", lambda e, k=step["key"]: self._run_step(k), t,
                                 icon="PLAY_ARROW", disabled=disabled,
                                 height=t.control_h())
        if reason:
            btn.tooltip = reason
        self._step_run_btns[step["key"]] = btn
        return btn

    def _select(self, key: str) -> None:
        """旧入口（行点击/测试）：统一走路由，保证侧栏高亮与页头同步。"""
        if key not in [step["key"] for step in self.STEPS] and key != "overview":
            return
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"issue:{key}", source="page")
            return
        self.activate(key, source="page")

    # ── 各步骤参数 ─────────────────────────────────────────────────────
    def _build_params(self, key: str) -> ft.Control:
        self.fields = {k: v for k, v in self.fields.items() if k in ("issue_dir", "toc_report")}
        return ASM.build_params(self, key)

    # ── 构建调用 ───────────────────────────────────────────────────────
    def _issue_dir(self) -> str:
        return self.field("issue_dir")

    def _build_step_call(self, key: str) -> EB.EngineCall:
        return ASM.build_step_call(self, key)

    @staticmethod
    def _latest_report(issue: str) -> str:
        return ASM.TOC.latest_report(issue)

    # ── 运行 ───────────────────────────────────────────────────────────
    def _run_step(self, key: str) -> None:
        ASM.run_step(self, key)

    def _run_all(self) -> None:
        ASM.run_all(self)

    def _on_stop(self, e=None) -> None:
        self.app.stop_run()

    async def _run_calls(self, calls: list, sequential: bool = False) -> None:
        last_code = 0
        for key, call in calls:
            if self.app.cancel_requested:
                break
            self._status[key] = "running"
            self._refresh_steps()
            self.app.begin_run(call)
            self._log.clear()
            self._result.reset()
            CK.before_issue_step(self, key)
            try:
                code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                         notes=self.app.notes_repo)
            except Exception as exc:  # noqa: BLE001 - 引擎缺失/启动失败一律给中文提示
                self.app.finish_run(call, -1)
                self._status[key] = "error"
                self._refresh_steps()
                self._result.show(f"❌ 任务启动失败：{exc}", level="error")
                self.app.toast(f"任务启动失败：{exc}")
                self.mark("run_start_error", step=key, error=f"{type(exc).__name__}: {exc}")
                return
            self.app.finish_run(call, code)
            cancelled = self.app.cancel_requested
            self._status[key] = "error" if (code != 0 and not cancelled) else ("warn" if code == 1 else "done")
            if cancelled:
                self._status[key] = "idle"
            self._refresh_steps()
            artifact = EB.artifact_path(self.app.last_line)
            await CK.after_issue_step(self, key, call, code, cancelled, artifact)
            ASM.after_step(self, key, call, code, cancelled, artifact)
            level = "warn" if cancelled else EB.result_level(call.entry, code)
            text = "⏹ 已取消" if cancelled else EB.result_text(call.entry, code)
            self._result.show(text, level=level, artifact=artifact)
            if artifact:
                self.app.store.set_recent("issue_dir", str(Path(self._issue_dir())))
            last_code = code
            try:
                self.page.update()
            except Exception:  # noqa: BLE001
                pass
            if cancelled:
                break
        self._refresh_steps()

    def _on_line(self, line: str) -> None:
        self.app.record_line(line)
        self._log.add_line(line, _level_of_line(line), EB.is_summary_line(line))

    def _refresh_steps(self) -> None:
        """状态变化后重绘当前视图（总览或单步子页），并同步所有「运行」按钮状态。"""
        if getattr(self, "_content_box", None) is None:
            return
        self._step_run_btns = {}
        if self.selected == "overview":
            self._content_box.controls = [self._build_overview()]
        else:
            self._content_box.controls = [self._build_step_page(self.selected)]
        self.refresh_running()
        self._safe_update(self._content_box)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    def refresh_running(self) -> None:
        running = self.app.running
        disabled = not self.app.engine_status.ok
        if self._run_all_btn is not None:
            self._run_all_btn.disabled = running or disabled
        if self._stop_btn is not None:
            self._stop_btn.disabled = not running
        # 步骤卡的「运行」也跟随运行/引擎状态禁用，并在 tooltip 里说明原因。
        step_disabled, step_reason = self._step_run_state()
        for btn in self._step_run_btns.values():
            btn.disabled = step_disabled
            btn.tooltip = step_reason
        self._safe_update(*(c for c in (self._run_all_btn, self._stop_btn)
                            if c is not None),
                          *self._step_run_btns.values())

    # 证据钩子 ─────────────────────────────────────────────────────────
    def prefill(self, issue_dir: str = "", toc_report: str = "") -> None:
        if issue_dir:
            self.set_field("issue_dir", issue_dir)
            self.app.store.set_recent("issue_dir", issue_dir)
        if toc_report:
            self.set_field("toc_report", toc_report)
        self.mark("demo_prefill", issue_dir=issue_dir, toc_report=toc_report)

    def select_step(self, key: str) -> None:
        """兼容旧入口（P2_ISSUE_STEP / GUI 钩子）：统一走路由。"""
        if key not in [step["key"] for step in self.STEPS]:
            return
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"issue:{key}", source="hook")
        else:
            self.activate(key, source="hook")
        self.mark("step_selected", step=key)

    def run_step_demo(self, key: str) -> None:
        self._run_step(key)

    def run_all_demo(self) -> None:
        self._run_all()


# ── 引用库（P3 实装，P2 保留入口）────────────────────────────────────────
#: 写库视图共用的一行后果提示（W1/W2 压到 1 行；全文进 notice 的 info tooltip）。
BIB_WRITE_NOTICE = "预览 → 二次确认 → 原子写；只提交 bibliography.jsonl，不推送。"
BIB_WRITE_VIEWS = ("import", "review", "edit", "dupes", "fieldfix", "health")
#: notice 的 info tooltip 全文（T9 的 help_content 再收录同一份口径，别改这里再抄一遍）。
BIB_WRITE_RULES = (
    "W1 写库四步：干跑预览 → 二次确认 → 校验 bibliography.jsonl 的 sha256"
    "（不符即拒绝、零写入）→ 原子替换。\n"
    "W2 只本地：写成功后自动本地提交 1 条（批量也只 1 条），"
    "只提交 bib_db/bibliography.jsonl；不 git push、不同步远端。\n"
    "W3 只读不写：概况 / 查库 / 规范化不写库；规范化只产出报告文件"
    "（库健康与字段归位是写库视图，批量裁决同样走 W1 四步）。"
)


class BibPage(BasePage):
    """引用库（P6）：10 视图 / 4 组两级导航；概况/查库/规范化只读，6 个写入视图写库。

    写入面（入藏 / 待处理 / 编辑 / 查重合并 / 字段归位 / 库健康）一律先干跑预览、
    再带 `--expect-db-sha256` 真写，写完自动本地提交（只提交 bib_db/bibliography.jsonl）；
    推送/回退要二次确认（B-190 契约）。
    """

    key = "bib"
    #: 10 视图（IA 序：查阅 → 采录 → 治理 → 维护）。key 沿用旧名，只新增 `normalize`。
    VIEWS = [("overview", "概况"), ("query", "查库"), ("normalize", "规范化"),
             ("import", "入藏"), ("review", "待处理"), ("edit", "编辑"),
             ("dupes", "查重合并"), ("fieldfix", "字段归位"),
             ("health", "库健康"), ("settings", "设置")]
    #: 4 组两级导航：组 key → (组标签, 组内视图 key；顺序即侧栏子项顺序)。
    GROUPS = {
        "read": ("查阅", ("overview", "query")),
        "intake": ("采录", ("normalize", "import")),
        "treat": ("治理", ("review", "edit", "dupes", "fieldfix")),
        "maint": ("维护", ("health", "settings")),
    }
    GROUP_ORDER = ("read", "intake", "treat", "maint")
    #: 页头权限芯片：只读视图不出 notice，靠这一枚自解释。
    VIEW_ACCESS = {"overview": "只读", "query": "只读", "normalize": "只读",
                   "import": "写库", "review": "写库", "edit": "写库",
                   "dupes": "写库", "fieldfix": "写库", "health": "写库",
                   "settings": "配置"}
    #: 视图级 notice：{view: (tone, 一行文案)}；缺键 = 该视图不出 notice。
    VIEW_NOTICES = {key: ("info", BIB_WRITE_NOTICE) for key in BIB_WRITE_VIEWS}
    VIEW_LABELS = {key: label for key, label in VIEWS}

    def __init__(self, app) -> None:
        super().__init__(app)
        # 批 112 族 B：视图状态落 store，主题/缩放重建 shell 后不回到 overview。
        self.view = SS.pick(app.store, "bib_view", self.view_keys(), "overview")
        self.group = self.group_of(self.view)
        # 批 114（用户 m03256）：两行 tab（组条 + 胶囊）删除 → 页头=面包屑+子项标题。
        self._crumb: ft.Row | None = None
        self._child_title: ft.Text | None = None
        self._notice_slot: ft.Container | None = None
        self._status_slot: ft.Row | None = None
        self.stats_data: dict | None = None
        self.lint_counts: dict | None = None  # lint counts（真实警告数；与 stats 并行取）
        self.stats_error = ""
        self.query_field: ft.TextField | None = None  # P2_BIB_QUERY_UI 自检钩子用
        self.view_slot: ft.Container | None = None
        self.query_view = BA.BibQueryView(self)
        self.settings_view = BS.BibSettingsView(self)
        self.import_view = BW.BibImportView(self)
        self.review_view = BW.BibReviewView(self)
        self.editor_view = BO.BibEditorView(self)
        self.dupes_view = BOD.BibDuplicatesView(self)
        self.fieldfix_view = BOH.BibFieldFixView(self)
        self.health_view = BOH.BibHealthView(self)
        # 批 112 S3a2：规范化面板（uid 单例，日志/报告跨重绘保留）。
        self.normalize_view = NR.NormalizeView(self)

    @classmethod
    def view_keys(cls) -> set[str]:
        return {key for key, _ in cls.VIEWS}

    @classmethod
    def group_of(cls, view: str) -> str:
        """视图 → 所属组 key（未知视图按第一组处理，与 set_view 的回落一致）。"""
        for group in cls.GROUP_ORDER:
            if view in cls.GROUPS[group][1]:
                return group
        return cls.GROUP_ORDER[0]

    TOOLBAR_TITLE = "引用库"

    def toolbar_meta(self) -> str:
        """页头计数：条数 = bib_stats.records，警告 = lint.counts.warnings（真实值）。

        旧实现误用 aliases.records（有别名记录数）当条数、conflicts.records 当警告数，
        会显示成 2,081 条 / 135 条警告；真实值是 3,883 条 / 76 条警告（lint）。
        未加载时显示「统计读取中…」；lint 未回前只显示条数。
        """
        stats = self.stats_data or {}
        records = stats.get("records")
        if not isinstance(records, (int, float)):
            return "统计读取中…"
        text = f"{int(records):,} 条"
        warnings = (self.lint_counts or {}).get("warnings")
        if isinstance(warnings, (int, float)) and warnings:
            text += f" · {int(warnings):,} 条警告"
        return text

    def toolbar_action(self):
        return ("采集", "ADD", lambda e: self.set_view("import", source="toolbar"))

    def _sync_toolbar_meta(self) -> None:
        syncer = getattr(self.app, "_sync_toolbar", None)
        if callable(syncer) and getattr(self.app, "toolbar", None) is not None:
            syncer()

    def build(self) -> ft.Control:
        t = self.t
        self.view_slot = ft.Container(expand=True)
        self._status_slot = ft.Row(spacing=t.sp("1"), controls=[])
        self._notice_slot = ft.Container(visible=False)
        self._sync_header()
        head = ft.Column(spacing=t.sp("xs"), controls=[
            self._crumb,
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[self._child_title, ft.Container(expand=True),
                             self._status_slot]),
        ])
        body = C.panel(head, self._notice_slot, self.view_slot, t=t, pad=(24, 10, 24, 12))
        self._render()
        return body

    def _sync_header(self) -> None:
        """页头=面包屑「引用库 / 组」+ 22/600 视图标题（批 114 删两行 tab）。"""
        t = self.t
        self.group = self.group_of(self.view)
        crumb = f"引用库 / {self.GROUPS[self.group][0]}"
        if self._crumb is None:
            self._crumb = ft.Row(spacing=t.sp("half"), controls=[
                C.text(crumb, t, "caption", t.palette.text_muted)])
            self._child_title = C.text(self.VIEW_LABELS[self.view], t, role="page-title")
        else:
            self._crumb.controls[0].value = crumb
            self._child_title.value = self.VIEW_LABELS[self.view]
        if self._notice_slot is not None:
            self._notice_slot.content = self._notice_control()
            self._notice_slot.visible = self._notice_slot.content is not None
        if self._status_slot is not None:
            self._status_slot.controls = self._status_chips()
        self._safe_update(*[c for c in (self._crumb, self._child_title,
                                        self._notice_slot, self._status_slot)
                            if c is not None])
        self._sync_toolbar_meta()

    def _render_nav(self) -> None:
        """兼容旧名：实际只刷新页头（面包屑/标题/notice/状态），导航已在侧栏。"""
        self._sync_header()

    def _notice_control(self) -> ft.Control | None:
        """写库视图的一行后果提示（tooltip 里是全文）；只读视图不出。"""
        spec = self.VIEW_NOTICES.get(self.view)
        if not spec:
            return None
        tone, message = spec
        bar = C.notice_bar(message, self.t, tone)
        try:
            bar.tooltip = ft.Tooltip(message=BIB_WRITE_RULES, wait_duration=0.4)
        except Exception:  # noqa: BLE001  Flet 版本差异：没有 Tooltip 就退化成一行
            pass
        return bar

    def _status_chips(self) -> list:
        """页头右侧状态：权限一句话 + （有则）未提交；不用胶囊。"""
        t = self.t
        access = self.VIEW_ACCESS.get(self.view, "只读")
        tone = "warn" if access == "写库" else "ok"
        chips = [ft.Row(spacing=t.sp("half"), controls=[
            C.status_dot(tone, t),
            C.text(access, t, "caption", color=t.palette.text_muted),
        ])]
        if (self.stats_data or {}).get("dirty"):
            chips.append(ft.Row(spacing=t.sp("half"), controls=[
                C.status_dot("warn", t),
                C.text("未提交", t, "caption", color=t.palette.text_muted),
            ]))
        return chips

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    # ── 视图切换 / 数据加载 ─────────────────────────────────────────────
    def activate(self, view: str, *, source: str = "nav") -> None:
        """导航激活视图：白名单 → store（view + 组记忆）→ mark → 重绘 → 按需加载。

        非法值回落 overview 并 mark `bib_view_invalid`（不静默忽略，便于 P2 断言）。
        """
        view = str(view or "")
        if view not in self.view_keys():
            self.mark("bib_view_invalid", view=view, source=source)
            view = "overview"
        previous = self.view
        self.view = view
        if previous == "normalize" and view != "normalize":
            self.normalize_view.on_leave()
        self.group = self.group_of(view)
        store = self.app.store
        store.set("bib_view", view)
        memory = store.get("bib_group_memory", {}) or {}
        memory = dict(memory) if isinstance(memory, dict) else {}
        memory[self.group] = view
        store.set("bib_group_memory", memory)
        self.mark("bib_view", view=view, source=source, group=self.group)
        self._sync_header()
        self._render()
        self._ensure_loaded(view)

    def set_view(self, view: str, *, source: str = "legacy") -> None:
        """旧入口（工具栏/子视图/GUI 钩子）：有路由时统一 navigate，无路由则本页激活。"""
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"bib:{view}", source=source)
            return
        self.activate(view, source=source)

    def set_group(self, group: str) -> None:
        """点组（旧入口）：进该组上次视图；导航版本由侧栏组行承担。"""
        if group not in self.GROUPS:
            return
        memory = self.app.store.get("bib_group_memory", {}) or {}
        target = memory.get(group) if isinstance(memory, dict) else None
        if target not in self.GROUPS[group][1]:
            target = self.GROUPS[group][1][0]
        self.set_view(target, source="group")

    def _switch(self, view: str, *, source: str = "ui") -> None:
        """兼容别名：main.py 的 P2_BIB_VIEW / P2_BIB_QUERY_UI 仍走这个入口。"""
        self.set_view(view, source=source)

    def _ensure_loaded(self, view: str) -> None:
        """按视图惰性加载（幂等；协程统一走 page.run_task）。"""
        # 页头/工具栏的计数来自 bib_stats：任何视图首开都补一次统计（只读）。
        if self.stats_data is None:
            self.page.run_task(self._load_stats)
        if view == "review":
            self.review_view.ensure_loaded()
        elif view == "settings":
            self.page.run_task(self.settings_view.ensure_loaded)
        elif view == "dupes":
            self.page.run_task(self.dupes_view.ensure_loaded)
        elif view == "fieldfix":
            self.page.run_task(self.fieldfix_view.ensure_loaded)
        elif view == "health":
            self.page.run_task(self.health_view.ensure_loaded)
        elif view == "normalize":
            self.normalize_view.ensure_loaded()

    async def _load_stats(self) -> None:
        """并行读取库概况（stats）与体检计数（lint）：页头条数/警告都用真实值。

        lint 退出码 1 = 有警告（仍带完整 JSON），所以只要 JSON 在就取 counts。
        """

        async def _read(call) -> dict:
            try:
                return await asyncio.to_thread(EB.read_json_command, call)
            except RuntimeError as exc:  # 引擎缺失时不阻塞页面
                return {"ok": False, "error": str(exc), "json": {}}

        result, lint_result = await asyncio.gather(
            _read(EB.bibdb_stats_call()), _read(EB.bibdb_lint_call()))
        self.stats_data = result.get("json") or None
        lint_json = lint_result.get("json") or {}
        self.lint_counts = (lint_json.get("counts") or None) if lint_json else None
        self.stats_error = "" if result.get("ok") else str(result.get("error") or "读取失败")
        stats = self.stats_data or {}
        aliases = stats.get("aliases") or {}
        conflicts = stats.get("conflicts") or {}
        counts = self.lint_counts or {}
        self.mark("bib_stats", ok=bool(result.get("ok")), code=result.get("code"),
                  records=stats.get("records"), warnings=counts.get("warnings"),
                  aliases_total=aliases.get("total"), conflicts=conflicts.get("records"),
                  bytes=stats.get("bytes"))
        self._render()
        self._sync_toolbar_meta()
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        """_sync_toolbar_meta 只换了 toolbar.content；这里补一次控件更新。

        verify4 发现：overview/health/settings 冷启动时页头持久停在「统计读取中…」，
        根因是只替换 content 未推送到客户端（_render 只更新 view_slot）。
        """
        toolbar = getattr(self.app, "toolbar", None)
        if toolbar is None:
            return
        try:
            toolbar.update()
        except Exception:  # noqa: BLE001 构建期未挂载时忽略，挂载后会自然生效
            pass

    async def _run_query(self) -> None:
        """兼容 `P2_BIB_QUERY_UI` 自检钩子：把旧入口接到新的查库视图（基本检索 / 题名）。"""
        value = (self.query_field.value or "").strip() if self.query_field else ""
        if value:
            self.query_view.basic_text = value
        await self.query_view._run_basic()

    # ── 渲染 ───────────────────────────────────────────────────────────
    def _render(self) -> None:
        if self.view_slot is None:
            return
        builders = {
            "overview": self._overview_view,
            "query": self.query_view.build,
            "normalize": self.normalize_view.ensure_built,
            "import": self.import_view.build,
            "review": self.review_view.build,
            "settings": self.settings_view.build,
            "edit": self.editor_view.build,
            "dupes": self.dupes_view.build,
            "fieldfix": self.fieldfix_view.build,
            "health": self.health_view.build,
        }
        builder = builders.get(self.view, self._overview_view)
        try:
            content = builder()
        except Exception as exc:  # noqa: BLE001
            import traceback

            frames = traceback.extract_tb(exc.__traceback__)
            self.mark("bib_render_error", error=f"{type(exc).__name__}: {exc}",
                      where=[f"{f.name}:{f.lineno}" for f in frames][-4:])
            return
        self.view_slot.content = content
        try:
            # 构建期（未挂载）update 会抛 RuntimeError；忽略，等页面显示后自然生效。
            self.view_slot.update()
        except Exception:  # noqa: BLE001
            pass

    def _overview_view(self) -> ft.Control:
        """概览：三张统计卡 → 「下一步」动作 → 折叠的库详情（动作在前、事实在后）。"""
        t = self.t
        stats = self.stats_data or {}
        aliases = stats.get("aliases") or {}
        conflicts = stats.get("conflicts") or {}
        loaded = self.stats_data is not None
        records = stats.get("records")
        conflict_n = conflicts.get("records") or 0
        conflict_total = conflicts.get("total") or 0
        size_mb = (stats.get("bytes") or 0) / 1024 / 1024
        reading = "读取失败" if self.stats_error else "正在读取…"
        controls: list = []
        if self.stats_error:
            controls.append(ft.Row(spacing=t.sp("1"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Container(expand=True, content=C.notice_bar(self.stats_error, t, "error")),
                C.secondary_button("重试", lambda e: self.page.run_task(self._load_stats), t,
                                   icon="REFRESH"),
            ]))
        controls.append(ft.Row(spacing=t.sp("2"), controls=[
            C.stat_card("条目总数", "—" if not loaded else str(records or 0),
                        reading if not loaded else f"别名合计 {aliases.get('total') or 0} 条 · 只读", t),
            C.stat_card("冲突 · 涉及记录", "—" if not loaded else str(conflict_n),
                        reading if not loaded else (f"共 {conflict_total} 条冲突 · 去「待处理」裁决" if conflict_n else "当前无冲突"), t),
            C.stat_card("库文件", f"{size_mb:.1f} MB" if stats.get("bytes") else "—",
                        "bibliography.jsonl", t),
        ]))
        if loaded and not records:
            controls.append(C.card(C.empty_state(
                "INBOX", "库里还没有条目", "入藏：选择 .bib → 预览 → 确认", t), t))
        else:
            controls.append(self._next_card(conflict_n, loaded=loaded))
        controls.append(self._detail_card(stats))
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _next_card(self, conflicts: int, *, loaded: bool = True) -> ft.Control:
        """「下一步」：只列当下能做的事；计数为 0 的冲突项降级成一行静态说明。"""
        t = self.t
        rows: list = [C.action_row("FACT_CHECK", "去体检交回的 .bib", "只出报告，不改库", t,
                                   on_click=lambda e: self.set_view("normalize"))]
        if conflicts:
            rows.append(C.action_row("GAVEL", f"处理 {conflicts} 条冲突", "逐条裁决", t,
                                     on_click=lambda e: self.set_view("review")))
        elif loaded:
            rows.append(C.text("无待裁决冲突", t, "caption", t.palette.text_faint))
        rows.append(C.action_row("LIBRARY_ADD", "入藏新条目", "选择文件并运行", t,
                                 on_click=lambda e: self.set_view("import")))
        return C.card(ft.Column(spacing=t.sp("half"),
                                controls=[C.section_title("下一步", t), *rows]), t)

    def _detail_card(self, stats: dict) -> ft.Control:
        """库详情：静态事实折进 ExpansionTile；工作区脏标记已上浮到页头芯片。"""
        t = self.t
        entry_types = stats.get("entry_types") or {}
        type_line = "、".join(f"{k} {v}" for k, v in list(entry_types.items())[:6]) or "—"
        tile = ft.ExpansionTile(
            title=C.text("库详情", t, "small", t.palette.text_muted),
            controls=[ft.Column(spacing=t.sp("half"), controls=[
                self._kv("库文件", str(stats.get("db") or "（未知）")),
                self._kv("条目类型", type_line),
                self._kv("创建时间范围", " → ".join(stats.get("created_range") or []) or "—"),
                self._kv("重复内容", str(stats.get("duplicate_content") or 0)),
                self._kv("读取状态", self.stats_error or ("已读取" if self.stats_data else "未读取")),
            ])],
            expanded=False,
        )
        return C.card(tile, t, pad=12)

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            ft.Container(expand=True, alignment=ft.Alignment.CENTER_RIGHT, content=C.text(
                str(value), t, "small", t.palette.text, max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(value))),
        ])

    def on_show(self) -> None:
        # 族 B：重建 shell 后新实例已从 store 恢复 view；这里校正高亮 + 补加载（幂等）。
        self._render_nav()
        self._ensure_loaded(self.view)

    def on_resize(self) -> None:
        if self.view == "query":
            self.query_view.on_resize()

    def refresh_running(self) -> None:
        # 规范化视图自带运行/停止按钮与日志；只有构建过才需要同步（幂等）。
        self.normalize_view.refresh_running()


def group_of(view: str) -> str:
    """模块级别名：测试与 S3a2 可直接 `pages.group_of(view)`，页面内走 `BibPage.group_of`。"""
    return BibPage.group_of(view)



# ── 设置 ───────────────────────────────────────────────────────────────────
class SettingsPage(BasePage):
    key = "settings"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.envcheck_data: dict | None = None
        self.envcheck_error = ""
        self.envcheck_slot = ft.Container()
        self.connection_slot = ft.Container()

    def build(self) -> ft.Control:
        t = self.t
        engine = self.app.engine_status
        status_rows = [
            ("运行方式", engine.describe()),
            ("引擎目录", engine.engine_dir or "（未找到）"),
            ("引擎版本", engine.version),
            ("问题", "；".join(engine.problems) if engine.problems else "无"),
            ("设置文件", str(self.app.store.path)),
        ]
        engine_card = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("引擎", t, note="探测只读；诊断包与环境检查见下方"),
            *[self._kv(label, value) for label, value in status_rows],
            ft.Row(spacing=t.sp("1"), controls=[
                C.secondary_button("重新检测", lambda e: self._rescan(), t, icon="REFRESH"),
                C.secondary_button("引擎目录…", lambda e: self._browse_engine(), t, icon="FOLDER_OPEN_OUTLINED"),
            ]),
        ]), t)

        options = self.app.store.get("options", {}) or {}
        appearance = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("外观", t, note="深浅色与缩放都会落盘，下次启动恢复"),
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("深色模式", t, "body"),
                ft.Container(expand=True),
                C.switch(t, value=self.app.t.is_dark,
                         on_change=self.app.on_theme_switch),
            ]),
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("界面缩放", t, "body"),
                # 批 112 A-05：滑块量程与屏幕钳制后的真实上限一致，不再先给 2.0× 再强钳。
                ft.Slider(**self._zoom_slider_spec(t), active_color=t.palette.accent, expand=True,
                          on_change=self._zoom_changed),
                C.text(f"{self.app.effective_zoom:.2f}×（上限 {self.app.max_zoom:.2f}×）", t,
                       "small", t.palette.text_muted, "medium"),
            ]),
            SHELL.button_palette_row(self),
            C.text(f"屏幕工作区钳制：本屏最大 {self.app.max_zoom:.2f}×；更大缩放会被限制，窗口不会越界。", t,
                   "caption", t.palette.text_faint),
        ]), t)

        tray = SHELL.tray_card(self)

        paths = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("路径", t, note="引擎目录留空 = 自动定位"),
            self._path_field("engine_override", "引擎目录（可空）", kind="dir"),
            self._path_field("notes_repo", "私有笔记仓（可空）", kind="dir"),
        ]), t)

        about = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("关于", t),
            self._kv("应用版本", self.app.app_version),
            self._kv("Flet / Python", f"{self.app.flet_version} · {self.app.python_version}"),
            self._kv("窗口 / DPI", f"{self.app.window_w}×{self.app.window_h} @ {self.app.dpi} ({self.app.dpi / 96:.0%})"),
            self._kv("IA", f"{ia_stats()['legacy_pages']} 个旧页 → {ia_stats()['new_pages']} 页 + 子流程"),
            self._kv("许可", "专有软件 · © 2026 Jeremymming（第三方许可见 THIRD_PARTY.md）"),
        ]), t)

        envcheck = SHELL.envcheck_card(self)
        proxy = SHELL.proxy_card(self)
        logs = SHELL.logs_card(self)
        advanced = SHELL.advanced_card(self)
        diagnostics = SHELL.diagnostics_card(self)

        header = C.page_header(
            "设置", "外观、引擎、路径、环境检查与诊断；所有设置持久化，不依赖系统注册表。", t,
            actions=[C.primary_button("立即保存", lambda e: self.app.save_settings(toast=True), t,
                                      icon="SAVE_OUTLINED")],
        )
        # 批 112 A-06：环境检查上提到引擎卡之后，首屏可见（原先在折叠线以下第 7 张卡）。
        page = C.page_body(header, engine_card, envcheck, appearance, tray, paths,
                           AIP.AiSettings(self).build(), proxy, logs, advanced, about, diagnostics,
                           t=t, gap=12)
        return page

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            ft.Container(expand=True, alignment=ft.Alignment.CENTER_RIGHT, content=C.text(
                str(value), t, "small", t.palette.text, max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(value))),
        ])

    def _path_field(self, key: str, label: str, *, kind: str = "dir") -> ft.Container:
        ref: list = []
        value = self.app.store.get(f"paths.{key}", "") or self.app.store.get(key, "") or ""
        if key == "engine_override":
            value = self.app.store.get("engine_override", "") or ""

        def _browse(e) -> None:
            self.app.browse_field(ref, f"选择{label}", kind, key, store_prefix="paths")

        control = C.path_picker(label, value, "", self.t, required=False,
                                on_browse=_browse, show_note=False, field_ref=ref)
        self.fields[key] = ref
        return control

    def _zoom_slider_spec(self, t) -> dict:
        hi = max(1.2, round(float(self.app.max_zoom), 2))
        lo = min(float(ZOOM_MIN), hi - 0.2)
        return {
            "min": lo,
            "max": hi,
            "divisions": max(1, int(round((hi - lo) / 0.1))),
            "value": min(max(float(self.app.store.get("zoom", 1.0)), lo), hi),
        }

    def _zoom_changed(self, e) -> None:
        value = round(float(e.control.value), 2)
        if abs(value - self.app.store.get("zoom", 1.0)) > 1e-6:
            self.page.run_task(self._apply_zoom, value)

    async def _apply_zoom(self, value: float) -> None:
        self.app.set_zoom(value)

    def _rescan(self) -> None:
        self.app.refresh_engine_status()
        self.app.rebuild_shell()
        self.app.toast("引擎状态已刷新")

    def _browse_engine(self) -> None:
        ref = self.fields.get("engine_override")
        self.app.browse_field(ref, "选择引擎目录", "dir", "engine_override", store_prefix="paths")

    # ── 环境检查（只读，调 Engine/envcheck.py --json）───────────────────
    async def _envcheck_task(self) -> None:
        t = self.t
        try:
            self.envcheck_slot.content = C.text("检查中…（TeX / 引擎 / 字体，约 3–10 秒）", t, "small",
                                                t.palette.text_muted)
            self.envcheck_slot.update()
        except Exception:  # noqa: BLE001
            pass
        try:
            call = EB.envcheck_call()
            result = await asyncio.to_thread(EB.read_json_command, call)
        except RuntimeError as exc:
            result = {"ok": False, "error": str(exc), "json": {}}
        payload = result.get("json") or {}
        self.envcheck_data = payload if payload else None
        self.envcheck_error = "" if result.get("ok") else str(result.get("error") or "环境检查失败")
        checks = payload.get("checks") or []
        ok_count = sum(1 for c in checks if self._check_ok(c))
        failed = [c for c in checks if not self._check_ok(c)]
        self.app.mark("envcheck", ok=bool(result.get("ok")), code=result.get("code"),
                      checks=len(checks), ok_count=ok_count,
                      failed=[str(c.get("id")) for c in failed][:20],
                      profile=payload.get("profile"), platform=payload.get("platform"))
        if result.get("ok"):
            self.app.set_check_done(True)
        self._render_envcheck()

    @staticmethod
    def _check_ok(check: dict) -> bool:
        return SHELL.check_ok(check)

    def _render_envcheck(self) -> None:
        SHELL.render_envcheck(self)

    def _copy_diagnosis(self) -> None:
        SHELL.copy_diagnosis(self)

    def refresh_running(self) -> None:
        pass


PAGE_CLASSES = [HomePage, SinglePage, IssuePage, BibPage, SettingsPage, HelpPage]
