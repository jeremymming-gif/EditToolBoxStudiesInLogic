"""P3 页面：工作台 / 单篇稿件（2 步）/ 整期装配（4 功能页）/ 引用库（10 视图）/ 设置 / 帮助与关于。

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
from ui import bib_host as BH
from ui import bib_ops as BO
from ui import bib_ops_dupes as BOD
from ui import bib_ops_health as BOH
from ui import bib_settings as BS
from ui import bib_write as BW
from ui import checks as CK
from ui import components as C
from ui import dirpush as DIRPUSH
from ui import metadata_form as MF
from ui import normalize as NR
from ui import settings_shell as SHELL
from ui.base_page import BasePage
from ui.tokens import BREAK, SIDEBAR_W, ZOOM_MIN
from ui.help_page import HelpPage
from ui.ia_map import (ISSUE_VIEWS, issue_view_of, issue_view_steps,
                       stats as ia_stats)

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



_SECTION_RE = _re.compile(r"▶\s*\d{2}/06\s+[^·]+·\s*([a-z]+)\s*·")


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
        # 批 115 P1-2（设计评审；D1-115-24）：Single 只有 2 级（页面→子页），
        # 按「breadcrumb 只在层级 >2 出现」去 crumb；h1 是唯一锚点。
        # `_crumb` 仅保留属性给旧测试/外部调用，不进入布局、不显示。
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
        # 批 116 SINGLE-FN：结果卡下方的产物动作行（有产物才显示；见 _sync_artifact_actions）。
        self._artifact_row = self._build_artifact_actions()
        self._run_btn = C.primary_button("运行", self._on_run, t, icon="PLAY_ARROW",
                                         disabled=not self.app.engine_status.ok)
        self._stop_btn = C.secondary_button("停止", self._on_stop, t, icon="STOP", disabled=True)
        self._sync_header()
        # 批 116 NAV-B：侧栏不再展开单篇子项，convert/proofcheck 收到页内 chip；
        # 放在页头下、内容面板顶部（28 高，选中用 palette sel_tint，不反白）。
        self._chip_row = self._build_flow_chips()
        # 批 115 P1-2：页头只有 h1 + 说明（crumb 已去）；列存起来供测试核对。
        self._title_col = ft.Column(spacing=t.sp("xs"), expand=True,
                                    controls=[self._child_title, self._flow_desc])
        header = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            self._title_col,
                            ft.Row(spacing=t.sp("1"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                   controls=[self._stop_btn, self._run_btn]),
                        ])
        self._flow_box.controls = [self._flow_builder(self.flow_key)()]
        self._proof.root.visible = False
        self._norm_jump_btn = C.secondary_button(
            "去引用库 → 回读校验", self._goto_normalize, t, icon="FACT_CHECK")
        # 批 115：入口搬出页头胶囊行，落到输入区末尾（结果区之前），功能不变。
        norm_row = ft.Row(spacing=t.sp("2"), alignment=ft.MainAxisAlignment.END,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[self._norm_jump_btn])
        status = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            C.busy_bar(t, visible=False),
                            ft.Container(expand=True),
                        ])
        self._busy = status.controls[0]
        # 批 115 gap1：日志浮层不再用 page_body 的静态 overlay_reserve，改由页面
        # 自己的底部 spacer 按 LogConsole 实际高度（收起/展开/拖拽）动态让位。
        self._log_spacer = ft.Container(height=t.px(52))
        self._log.on_layout_change = self._sync_log_reserve
        body = C.page_body(header, self._chip_row, status, self._flow_box, norm_row,
                           self._result.root, self._artifact_row, self._proof.root,
                           self._log_spacer, t=t, gap=10,
                           overlay=self._log.dock_root, overlay_reserve=0,
                           panel=True)
        self.refresh_running()
        return body

    def _sync_log_reserve(self) -> None:
        """日志浮层收展/拖拽后同步底部占位（审计 gap1：展开不得遮住最后一行）。

        两档均按 1160×780 截图实测（evidence/b115-p3/impl-single/reserve-measure.txt）：
        收起 dock 26.0 逻辑 px ≈ 设计 39（6+26+6 + 1px 顶边框），取 40 留 1px 余量；
        展开 46 + `_log_height`（6+26+8+6 + 日志框，拖拽时跟随，实测 164.5 vs 164.0）。
        """
        spacer = getattr(self, "_log_spacer", None)
        log = getattr(self, "_log", None)
        if spacer is None or log is None:
            return
        if log.is_expanded:
            height = self.t.px(54) + log._log_height
        else:
            height = self.t.px(52)
        if spacer.height == height:
            return
        spacer.height = height
        try:
            spacer.update()
        except Exception:  # noqa: BLE001 未挂载（构建期/证据脚本）
            pass

    def _flow_builder(self, key: str):
        """按 flow_key 选构建器（重建 shell 后恢复上次步骤也走这里）。"""
        return {
            "convert": self._build_convert,
            "proofcheck": self._build_proofcheck,
        }.get(key, self._build_convert)

    def _sync_header(self) -> None:
        """页头 h1 固定「单篇稿件」；子步骤只换说明（批 116 NAV-B）。"""
        t = self.t
        flow = self.FLOWS[self.flow_key]
        if self._child_title is None:
            self._child_title = C.text("单篇稿件", t, role="page-title")
        if self._flow_desc is None:
            self._flow_desc = C.text(flow["desc"], t, "small", color=t.palette.text_muted)
        self._child_title.value = "单篇稿件"
        self._flow_desc.value = flow["desc"]

    def _build_flow_chips(self) -> ft.Row:
        """页内步骤 chip（初排转换/一校核查）；选中用 sel_tint，不反白不高亮主色。"""
        t = self.t
        chips: list[ft.Control] = []
        for key, flow in self.FLOWS.items():
            chip = C.chip(
                flow["label"], t, active=key == self.flow_key,
                on_click=lambda e, v=key: self._switch_flow(v))
            chip.height = t.px(28)
            chip.alignment = ft.Alignment.CENTER
            chips.append(chip)
        return ft.Row(spacing=t.sp("half"), controls=chips,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _sync_chips(self) -> None:
        """activate 后原地换 chip 的选中态（不重建整行，保持位置）。"""
        row = getattr(self, "_chip_row", None)
        if row is None:
            return
        row.controls = self._build_flow_chips().controls

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
            self._sync_artifact_actions("")
            CK.on_flow_switch(self)
        self._sync_header()
        self._sync_chips()
        self._safe_update(self._flow_box, self._child_title, self._flow_desc,
                          getattr(self, "_chip_row", None))
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
        # 批 115 D1-115-12：页面=一张纸白面板，分区用 section_label + 发丝线，
        # 不再用页面级 C.card/C.section_title（用户 m02855 的「字段躺在纸灰底上」）。
        return ft.Column(spacing=t.sp("2"), controls=[
            C.section_label("输入", t),
            C.hairline(t),
            # 必填语义已进每个输入框的 label（「源稿件（必填）」），不再另起一行提示。
            ft.Column(spacing=t.sp("2"), controls=[
                self.add_picker("源稿件", "single_source",
                                "作者来稿 .tex / .docx", kind="file"),
                self.add_picker("输出根目录", "single_out",
                                "产物写到该目录下的 <作者>/ 里", kind="dir"),
            ]),
            C.section_label("处理选项", t, note="默认勾选常用项"),
            C.hairline(t),
            rows,
            self._advanced_title(),
            C.hairline(t),
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
        return C.section_label(
            "高级参数", t,
            trailing=C.secondary_button("清空高级参数", self._clear_advanced_params, t,
                                        icon="CLEAR_ALL"),
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
        # 批 115：高级参数不再套页面级白卡，直接落在白面板上（分组标题保留）。
        return ft.Column(spacing=t.px(16), controls=blocks)

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
        """设计栅格宽（设计 px）：窗口逻辑宽 / k（k=zoom，DPI 已交给 Flutter）。

        批 115 P1-8：这里的 /k 是栅格换算（zoom 后能放多少设计 px），不是旧 DPI
        放大；k=1.0 时就是窗口逻辑宽。构建期 page.width 可能还是 800，故优先 window_w。
        """
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
        """处理选项 2 列卡片网格（内容宽 <900 才降单列）；替代原先一列四行的紧凑列表。"""
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
            # 批 116 SINGLE-FN（F1 P0-1）：引擎 --dry-run 在生成前返回，
            # 只算「将要做什么」；默认关，勾选后仍走同一套 argv。
            ("只预演（不落盘）", "列出将生成什么，不建产物、不写任何文件",
             "PREVIEW", "convert_dry_run", False, None),
        )
        tiles = [
            C.option_tile(label, desc, t, icon=icon, badge_label=badge_label,
                          checked=bool(options.get(key, default)),
                          on_change=lambda e, k=key: self.app.set_option(k, e.control.value))
            for label, desc, icon, key, default, badge_label in specs
        ]
        if self._design_w() >= 900:
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
                # 批 116 SINGLE-FN（F1 P0-1）：只预演不落盘；默认 False。
                dry_run=bool(options.get("convert_dry_run", False)),
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
        # 批 116 SINGLE-FN：预演标记取自已拼好的 argv（tile → _build_call → --dry-run）。
        dry_run = self.flow_key == "convert" and "--dry-run" in call.argv
        self._sync_artifact_actions("")
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
        elif dry_run and code == 0 and not self.app.cancel_requested:
            # 批 116 SINGLE-FN（F1 P0-1）：预演只读。引擎会打印「将创建…」的路径，
            # 但那些路径尚未落盘；显式清空 artifact，也不写最近记录。
            text, level = "预演完成（只读：未建产物目录、未写产物）", "ok"
            artifact = ""
        self._result.show(text, level=level, artifact=artifact)
        self._sync_artifact_actions(artifact)
        if artifact:
            self.app.store.set_recent(self._artifact_recent_key(), artifact)
        self._cleanup_meta_temp()
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

    def _build_artifact_actions(self) -> ft.Row:
        """结果卡下方的产物动作行：打开产物目录（与卡内「打开产物」并存）。

        批 116 SINGLE-FN：没有 artifact 时整行隐藏（visible=False，不占布局）；
        初排成功后才由 _sync_artifact_actions 显示。
        """
        t = self.t
        self._open_dir_btn = C.secondary_button(
            "打开产物目录", self._on_open_artifact_dir, t, icon="FOLDER_OPEN")
        return ft.Row(spacing=t.sp("2"), alignment=ft.MainAxisAlignment.END,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      visible=False, controls=[self._open_dir_btn])

    def _sync_artifact_actions(self, artifact: str) -> None:
        """有产物才显示目录动作行；同时记住当前 artifact 供按钮使用。"""
        self._artifact = artifact or ""
        row = getattr(self, "_artifact_row", None)
        if row is None:
            return
        row.visible = bool(self._artifact) and self.flow_key == "convert"
        self._safe_update(row)

    def _on_open_artifact_dir(self, e=None) -> None:
        """在文件管理器里打开产物所在目录（只读动作，不改任何文件）。"""
        artifact = getattr(self, "_artifact", "")
        if not artifact:
            return
        ok, error = EB.open_path(os.path.dirname(artifact))
        if not ok:
            self.app.toast(error)

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
    """整期装配：四个页内功能子页（生成目录 / 编译并合并 / 清理临时文件 / 生成推送文案）。

    批 117（用户拍板）：与「单篇稿件」「引用库」同构——页头 + 36 高页内 chip 导航
    + 功能页内容。期目录输入内嵌在每个功能页顶部（四页共用同一值），不再有页面级
    上下文条、左侧导航栏或窄窗横条；`issue:<step>` 6 条深链仍落到所属
    功能页并展开对应区块，22 条路由不变。
    """

    STEPS = [
        {"key": "infocheck", "title": "检查 tex 信息", "icon": "FACT_CHECK_OUTLINED",
         "desc": "扫描整期 tex，核对卷期年 / 页码 / 字号行距；只读，不改任何稿件。"},
        {"key": "toc", "title": "生成目录", "icon": "LIST_ALT_OUTLINED",
         "desc": "取最新信息检查报告，生成中英文目录 tex；可落位并编译。"},
        {"key": "compileall", "title": "编译全部", "icon": "TERMINAL",
         "desc": "逐篇 xelatex ×3（含 biber）；跳过 000；同名 PDF 覆盖。"},
        {"key": "merge", "title": "合并 PDF", "icon": "PICTURE_AS_PDF",
         "desc": "按篇序合并整期 PDF；可插编委页 / 封底 / 书签。"},
        {"key": "cleantemp", "title": "删除临时文件", "icon": "DELETE_SWEEP_OUTLINED",
         "desc": "把中间文件移入期目录/.toolbox-trash；可撤销，不永久删除。"},
        {"key": "dirpush", "title": "推送目录摘要", "icon": "IOS_SHARE",
         "desc": "生成《目录摘要》docx 并写入推送根；只预演时不动文件。"},
    ]
    ACTIONS = {
        "infocheck": ("运行检查", "重新检查"),
        "toc": ("生成目录", "重新生成"),
        "compileall": ("开始编译", "重新编译"),
        "merge": ("合并 PDF", "重新合并"),
        "cleantemp": ("预览清理", "开始清理…"),
        "dirpush": ("生成摘要", "重新推送"),
    }
    #: 批 116：4 个页内视图（顺序即栏内顺序；`steps` 顺序即详情区块顺序）。
    VIEWS = ISSUE_VIEWS
    key = "issue"
    title = "整期装配"
    LOG_LIMIT = 2000

    def __init__(self, app) -> None:
        super().__init__(app)
        self.selected = ""
        self._status: dict = {step["key"]: "idle" for step in self.STEPS}
        self._run_results: list = []
        self._content_box = ft.Column(spacing=10)
        self._params_box = ft.Column(spacing=10)
        self._step_pages: list = []
        self._step_run_btns: dict = {}
        self._log = None
        self._result = None
        self._info = None
        self._log_spacer = None
        self._log_expanded = False
        self._log_filter = "step"
        self._log_step_key = ""
        self._log_buffer: list[str] = []
        self._log_lines = 0
        self._info_payload = None
        self._info_articles: list = []
        self._info_notes: list = []
        self._info_report = ""
        self._info_filter = "all"
        self._info_expanded = ""
        self._compile_rows: dict = {}
        self._compile_order: list = []
        self._compile_current = ""
        self._compile_summary = ""
        self._compile_plan: list = []
        self._merge_info: dict = {}
        self._toc_payload: dict = {}
        self._ct_preview: dict = {}
        self._detail_status = None
        self._detail_primary = None
        self._shell = None
        self._rail_box = None
        self._detail_box = None
        self._keyboard_installed = False
        self._run_backup: list = []
        self._crumb = None            # D1-115-26：2 级页面不再有 crumb，仅保留属性兼容
        self._step_run_primary = None  # 详情头唯一主按钮（旧测试/外部读取用）
        # 批 116：多区块视图（一个视图最多 2 个区块，各自独立主按钮/状态）
        self._detail_statuses: dict = {}
        self._primary_btns: dict = {}
        self._section_refs: dict = {}
        # 批 116 ISSUE4-C：视图内手风琴（同一时刻只展开一个区块）。
        # 值 "" = 该视图全部收起；键缺失 = 默认展开首个区块（01 / 03 / 05 / 06）。
        # flet 1.0.5 桌面 `scroll_to`（key/offset/page）实测静默无位移，
        # 深链「首屏见目标区块」改用展开状态表达，不再保留滚动调度死码。
        self._expanded: dict = {}
        self._section_heads: dict = {}
        self._section_bodies: dict = {}
        self._chevrons: dict = {}
        self._prep_pages = None         # mergepdf --detect 的只读前置页结果
        self._prep_box = None
        # 批 117：页内 4 功能子页导航 / 换期重置 / 失败日志摘要。
        self._nav_box = None
        self._nav_chips: dict = {}
        self._last_issue = ""
        self._step_tail: dict = {}

    # ── 页面骨架 ───────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="整期日志", dock=True,
                                 toast=self.app.toast, mark=self.mark)
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._info = CK.issue_card(self)
        self._child_title = C.text("整期装配", t, role="page-title")
        self._flow_desc = C.text("一期稿件，从检查到合刊。", t, "small",
                                 color=t.palette.text_muted)
        # 批 117：期目录输入内嵌进每个功能页（见 `_build_step_page`），
        # 页面不再有期目录条、左侧导航栏或窄窗横条。
        #
        # 页面级唯一主操作：运行整期…（运行中 → 停止；失败后 → 从失败处继续…）
        self._run_all_btn = C.primary_button(
            "运行整期…", lambda e: self._open_runall(), t, icon="PLAY_ARROW",
            disabled=not self.app.engine_status.ok)
        self._continue_btn = C.secondary_button(
            "从失败处继续…", lambda e: self._continue_from_failure(), t, icon="REPLAY")
        self._stop_btn = C.danger_button("停止", lambda e: self._confirm_stop(), t,
                                         icon="STOP")
        self._run_all_secondary = self._continue_btn
        self._step_run_btns = {}
        self._header_actions = ft.Row(spacing=t.sp("1"),
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                      controls=[])
        self._title_col = ft.Column(spacing=t.sp("xs"), expand=True,
                                    controls=[self._child_title, self._flow_desc])
        header = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[self._title_col, self._header_actions])
        landing = self._default_view()
        self.selected = landing
        self._set_expanded(landing)
        self._nav_box = ft.Container(content=self._nav_bar())
        self._content_box = ft.Column(
            spacing=0, expand=True, controls=[self._build_step_page(landing)])
        self._last_issue = self.field("issue_dir")
        self._shell = ft.Column(
            spacing=t.sp("2"), expand=True,
            controls=[self._nav_box, self._content_box])
        # 旧属性名保留给外部/测试读取：内容区仍是 `_content_box`，导航即 `_nav_box`。
        self._detail_box = self._content_box
        self._rail_box = self._nav_box
        self._log.set_expanded(False)
        self._log_spacer = ft.Container(height=t.px(52))
        self._log.on_layout_change = self._sync_log_reserve
        self._sync_header()
        self._sync_run_actions()
        self._install_keyboard()
        body = C.page_body(header, self._shell, self._log_spacer, t=t, gap=10,
                           overlay=self._log.dock_root, overlay_reserve=0, panel=True)
        self.refresh_running()
        self._restore_issue_state()
        return body

    def _build_issue_row(self) -> ft.Control:
        """功能页顶部的期目录行（48 浮标字段 + 112×48「更换…」+ 尾部 ✕）。

        四个功能页共用同一字段与同一取值：每页渲染的是同一条 `issue_dir`，
        改一页即四页同步（切页时重建，值取 store 的 recent.issue_dir）。
        """
        t = self.t
        # 值优先取当前字段（程序化 set_field / 切页重建都要保留），回退 store。
        prior = ""
        if self.fields.get("issue_dir"):
            prior = str(self.fields["issue_dir"][0].value or "").strip()
        value = prior or (self.app.store.get("recent.issue_dir", "") or "")
        ref: list = []
        self.fields["issue_dir"] = ref

        def _changed(e) -> None:
            value = (e.control.value or "").strip()
            self.app.store.set_recent("issue_dir", value)
            self.mark("path_change", field="issue_dir", value=e.control.value)
            self._on_issue_changed(e)

        field = C.floating_field(
            t, "期目录", value=value,
            hint="一期稿件的根目录，含 000 / 01 作者…",
            on_change=_changed, expand=True, clearable=False)
        ref.append(field)

        def _browse(e) -> None:
            self.app.browse_field(ref, "选择期目录", "dir", "issue_dir")
            # 异步轮询必须显式调度：直接调用只会得到未 await 的协程（换期不重置 P0）。
            self.page.run_task(self._watch_issue_pick, ref, (ref[0].value or "").strip())

        browse_btn = C.secondary_button("更换…", _browse, t, icon="FOLDER_OPEN")
        browse_btn.width = t.px(112)
        clear_btn = C.text_button("✕", lambda e: self._clear_issue(), t)
        return ft.Container(
            key="issue-dir-row", padding=C._pad(t, 0, 0, 0, 2),
            content=C.field_row(field, [browse_btn, clear_btn], t, height=t.px(48)))

    async def _watch_issue_pick(self, ref: list, before: str) -> None:
        """文件选择器回填不会触发 on_change（main.browse_field 直接写 value），
        这里等值变化后补一次 `_on_issue_changed`；取消选择则静默结束。"""
        for _ in range(240):
            await asyncio.sleep(0.25)
            try:
                value = (ref[0].value or "").strip()
            except Exception:  # noqa: BLE001
                return
            if value != before:
                self.app.store.set_recent("issue_dir", value)
                self._on_issue_changed(None)
                return

    def _browse_issue(self, e=None) -> None:
        """无期目录空态的 CTA：直接唤起现有目录选择器。"""
        ref = self.fields.get("issue_dir") if getattr(self, "fields", None) else None
        if ref is None:
            ref = []
            self.fields["issue_dir"] = ref
        self.app.browse_field(ref, "选择期目录", "dir", "issue_dir")
        before = (ref[0].value or "").strip() if ref else ""
        self.page.run_task(self._watch_issue_pick, ref, before)

    def _clear_issue(self) -> None:
        self.set_field("issue_dir", "")
        self.app.store.set_recent("issue_dir", "")
        self._on_issue_changed(None)

    # ── 页内 4 功能子页导航（批 117，视觉对齐「引用库」页内 chip）─────────
    def _nav_narrow(self) -> bool:
        """窗口宽 <1100 → 两行；1160 单行。窗口变化只重建导航，不重建正文。"""
        width = self._logical_width()
        if width <= 0:
            try:
                width = float(self.app._design_width())
            except (AttributeError, TypeError, ValueError):
                return False
        return 0 < width < 1100

    def _nav_chip(self, view: dict, active: bool) -> ft.Container:
        t = self.t
        p = t.palette
        key = str(view["key"])
        state = self._view_state(key)
        chip = ft.Container(
            key=f"issue-nav-{key}",
            height=t.px(28), border_radius=t.radius("control"),
            padding=C._pad(t, 10, 0, 10, 0),
            alignment=ft.Alignment.CENTER,
            bgcolor=p.nav_sel if active else None,
            tooltip=f"{view['title']}：{self._state_word(state)}",
            on_click=lambda e, k=key: self._select_view(k),
            content=ft.Row(
                spacing=t.px(6),
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    C.text(str(view["title"]), t, "small",
                           p.text if active else p.text_muted,
                           weight="semibold" if active else "medium"),
                    C.status_dot(self._view_tone(state), t),
                ]),
        )
        chip.on_hover = lambda e, box=chip, sel=active: self._nav_hover(e, box, sel)
        self._nav_chips[key] = chip
        return chip

    def _nav_hover(self, e, chip: ft.Container, active: bool) -> None:
        p = self.t.palette
        hovered = str(getattr(e, "data", "")).lower() in ("true", "1", "on")
        chip.bgcolor = p.nav_sel if active else (p.surface_2 if hovered else None)
        self._safe_update(chip)

    def _nav_row(self, views: list) -> ft.Container:
        t = self.t
        active = self._view_of_step(self.selected)
        return ft.Container(
            height=t.px(36), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(
                spacing=t.px(4),
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self._nav_chip(view, str(view["key"]) == active)
                          for view in views]))

    def _nav_bar(self) -> ft.Control:
        """36 高导航条：1160 一行 4 chip；内容宽 <1100 折两行（高度 72）。"""
        t = self.t
        if self._nav_narrow():
            bar = ft.Column(spacing=0, controls=[
                self._nav_row(self.VIEWS[:2]),
                self._nav_row(self.VIEWS[2:]),
            ])
            height = 36 * 2
        else:
            bar = self._nav_row(self.VIEWS)
            height = 36
        return ft.Container(
            key="issue-nav", height=t.px(height),
            border=ft.Border(bottom=ft.BorderSide(width=t.px(1),
                                                  color=t.palette.border)),
            content=bar)

    def _sync_nav(self) -> None:
        """只换导航条本身（换期/状态变化/宽度变化），正文控件保留。"""
        box = getattr(self, "_nav_box", None)
        if box is None:
            return
        self._nav_chips = {}
        box.content = self._nav_bar()
        self._safe_update(box)

    def _refresh_layout(self) -> None:
        """宽窄变化只影响页内导航行数（1160 单行 / 1000 两行）。"""
        self._sync_nav()

    def _sync_log_reserve(self) -> None:
        spacer = getattr(self, "_log_spacer", None)
        log = getattr(self, "_log", None)
        if spacer is None or log is None:
            return
        height = (self.t.px(54) + log._log_height
                  if log.is_expanded else self.t.px(52))
        if spacer.height == height:
            return
        spacer.height = height
        try:
            spacer.update()
        except Exception:  # noqa: BLE001
            pass

    def _sync_log_dock(self) -> None:
        log = getattr(self, "_log", None)
        if log is not None and hasattr(log, "set_expanded"):
            log.set_expanded(bool(self._log_expanded))

    def _sync_header(self) -> None:
        # P2-1：产品行固定，不再随步骤 desc 漂移；步骤说明留在详情页。
        child, desc = "整期装配", "一期稿件，从检查到合刊。"
        if self._child_title is not None:
            self._child_title.value = child
        if self._flow_desc is not None:
            self._flow_desc.value = desc
        self._sync_detail_status()

    def _sync_run_actions(self) -> None:
        t = self.t
        actions = getattr(self, "_header_actions", None)
        if actions is None:
            return
        if self.app.running:
            controls = [self._stop_btn]
        elif self._has_failed():
            controls = [self._continue_btn, self._run_all_btn]
        else:
            controls = [self._run_all_btn]
        if getattr(self, "_run_all_secondary", None) is not None:
            self._run_all_secondary.disabled = bool(self.app.running)
        if not self.field("issue_dir"):
            self._run_all_btn.disabled = True
            self._run_all_btn.tooltip = "先选择期目录"
        else:
            self._run_all_btn.disabled = not self.app.engine_status.ok
            self._run_all_btn.tooltip = (None if self.app.engine_status.ok
                                         else "引擎未就绪")
        actions.controls = controls
        self._safe_update(actions)

    def _has_failed(self) -> bool:
        return any(self._state_of(key) == "error" for key in self._status)

    # ── 路由 / 子页 ────────────────────────────────────────────────────
    def _default_view(self) -> str:
        """默认落点：始终是某个步骤（旧总览已删除，不再返回 overview）。

        无期目录时落首步，由详情区显示选择期目录的空态；有期目录时优先上次
        步骤，否则首个未完成步，最后兜底首步。
        """
        steps = [step["key"] for step in self.STEPS]
        if not getattr(self, "fields", None):
            return steps[0]
        issue = self.field("issue_dir")
        if not issue:
            return steps[0]
        state = self._issue_state(issue)
        last = str(state.get("last_step") or "")
        if last in steps:
            return last
        for step in self.STEPS:
            if self._state_of(step["key"]) in ("idle", "cancelled"):
                return step["key"]
        return steps[0]

    def activate(self, view: str, *, source: str = "nav") -> None:
        if view in ("", "overview"):
            # `issue` / `issue:overview` 别名只保留深链兼容，语义上不再有总览屏：
            # 一律落到上次步骤或默认首视图（无期目录时该视图显示空态）。
            view = self._default_view()
        step = self._step_by_key(view)
        if step is None:
            return
        if self.app.running and view != getattr(self, "selected", ""):
            # 批 116：同一视图内切区块不打断长任务 UI；跨视图切换仍禁止。
            if self._view_of_step(str(step["key"])) != self._view_of_step(
                    str(getattr(self, "selected", ""))):
                self.app.toast("运行中不能切换步骤，先停止")
                return
        self.selected = str(step["key"])
        if getattr(self, "_content_box", None) is not None:
            # 批 116 ISSUE4-C：深链/⌘N 进入时展开目标区块并收起同视图另一区块；
            # 视图的展开状态随 `_expanded` 保留（切走再回来仍是最近展开的那个）。
            self._set_expanded(self.selected)
            self._step_run_btns = {}
            self._primary_btns = {}
            self._detail_statuses = {}
            self._section_refs = {}
            self._section_heads = {}
            self._section_bodies = {}
            self._chevrons = {}
            self._detail_status = None
            self._detail_primary = None
            self._sync_header()
            self._sync_run_actions()
            self._content_box.controls = [self._build_step_page(self.selected)]
            self.refresh_running()
            self._sync_nav()
            self._safe_update(self._child_title, self._flow_desc,
                              self._nav_box, self._content_box)
            self._persist_issue_state()
        self.mark("issue_activate", step=self.selected, source=source)

    def _build_empty_detail(self) -> ft.Control:
        """无期目录空态：四个功能页共用；上方由调用方渲染同一行期目录输入。"""
        t = self.t
        return ft.Container(
            padding=C._pad(t, 16, 22, 16, 22),
            bgcolor=t.palette.surface, border=C.box(t), border_radius=t.radius("md"),
            content=ft.Column(
                spacing=t.sp("2"), horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    C.text("还没有选择期目录（一期稿件的根目录，含 000/01 作者… 等）",
                           t, "h2", text_align=ft.TextAlign.CENTER),
                    C.text("选择后四个功能页共用这一期；换期会各自恢复上次结果。",
                           t, "caption", t.palette.text_faint),
                    C.primary_button("选择期目录", self._browse_issue, t,
                                     icon="FOLDER_OPEN"),
                ]))

    def _build_step_page(self, key: str) -> ft.Control:
        """功能页：最上方是内嵌的期目录行，其下是该功能自己的 1–2 个区块。

        批 117：期目录输入从页面级 44 高横条挪进四个功能页（四页共用同一值）；
        无期目录时仍显示输入行 + 统一空态，不再回落到旧总览树。
        """
        t = self.t
        controls: list = [self._build_issue_row()]
        if not self.field("issue_dir"):
            controls.append(self._build_empty_detail())
            return ft.Column(spacing=t.sp("2"), controls=controls)
        if self._result is not None:
            controls.append(self._result.root)
        for step_key in self._view_steps(self._view_of_step(key)):
            controls.append(self._build_section(step_key))
        return ft.Column(spacing=0, controls=controls)

    def _build_section(self, key: str) -> ft.Control:
        """功能区块：区块头常显（标题 + 状态 + 本功能主按钮），内容手风琴展开。

        同页两区块只展开一个（`expanded_step`），默认展开第一个；区块间用 1px
        hairline 分隔，最后一个区块不再画底边。
        """
        t = self.t
        steps = self._view_steps(self._view_of_step(key))
        expanded = self.expanded_step(self._view_of_step(key)) == key
        head = self._detail_header(key)
        body: list = [
            C.text(self._step_desc(key), t, "small", t.palette.text_muted),
            C.hairline(t),
            C.section_label("参数", t,
                            note=(None if key == "infocheck" else "改动即时保存")),
        ]
        if key == "toc" and not self._toc_report_ready():
            body.append(self._toc_report_cta())
        body.append(self._build_params(key))
        if key == "dirpush":
            reason = DIRPUSH.validation_error(self)
            if reason and reason != "先选择期目录":
                body.append(C.inline_note(reason, t, "warn"))
        if key == "merge":
            body.append(self._prep_card())
        body.append(self._result_zone(key, banner=False))
        body_col = ft.Column(spacing=t.sp("2"), controls=body, visible=expanded)
        self._section_bodies[key] = body_col
        last = bool(steps) and steps[-1] == key
        wrapper = ft.Container(
            key=f"issue-section-{key}",
            border=None if last else ft.Border(
                bottom=ft.BorderSide(width=t.px(1), color=t.palette.border)),
            padding=C._pad(t, 0, 6, 0, 6),
            content=ft.Column(spacing=t.sp("2"), controls=[head, body_col]))
        self._section_refs[key] = wrapper
        return wrapper

    def _detail_header(self, key: str) -> ft.Control:
        """区块头（40 高）：15px 标题 + 状态点/状态词 + 右侧 Filled 主按钮 + chevron。

        点击区只包住左侧头部与 chevron；主按钮在点击区之外——点主按钮只运行
        该功能，不触发展开/收起。批 117 去掉了 01–06 序号列（不再按编号呈现）。
        """
        t = self.t
        expanded = self.expanded_step(self._view_of_step(key)) == key
        state = self._state_of(key)
        status = C.text(self._status_text(key), t, "caption", self._state_color(state))
        self._detail_statuses[key] = status
        btn = self._primary_button(key)
        chevron = C.text("▾" if expanded else "▸", t, "caption", t.palette.text_faint)
        self._chevrons[key] = chevron
        head = ft.Container(
            key=f"issue-head-{key}", expand=True, height=t.px(40),
            alignment=ft.Alignment.CENTER_LEFT,
            on_click=lambda e, k=key: self._toggle_section(k),
            content=ft.Row(spacing=t.sp("1"),
                           vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text(self._step_title(key), t, "section"),
                C.status_dot(self._tone(state), t),
                status,
                ft.Container(expand=True),
                chevron,
            ]))
        self._section_heads[key] = head
        if key == self.selected:
            # 旧契约（`_detail_status`/`_step_run_primary`）指向当前选中区块。
            self._detail_status = status
            self._detail_primary = btn
            self._step_run_primary = btn
        return ft.Row(spacing=t.sp("1"),
                      vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[head, btn])

    # ── 批 117：目录缺报告时的页内补救（不强制顺序、不走整期）──────────
    def _toc_report_ready(self) -> bool:
        report = self.field("toc_report")
        if not report:
            issue = self._issue_dir()
            if issue:
                import ui.toc as TOC
                report = TOC.latest_report(issue)
        return bool(report) and os.path.isfile(os.path.expanduser(report))

    def _toc_report_cta(self) -> ft.Control:
        t = self.t
        return C.inline_note(
            "还没有信息检查报告：先生成报告，目录才有卷期/栏目依据。", t, "warn",
            action=C.secondary_button("先生成检查报告",
                                      lambda e: self._run_infocheck_only(), t,
                                      icon="FACT_CHECK_OUTLINED", height=t.px(28)))

    def _run_infocheck_only(self) -> None:
        """页内 CTA：直接跑「检查 tex 信息」单步；成功后回填 latest_report 并启用。"""
        if self._state_of("infocheck") == "running":
            self.app.toast("检查正在进行")
            return
        self._run_step("infocheck")

    # ── 批 116：4 视图（呈现层，不动路由/步骤）─────────────────────────
    def _view_by_key(self, view_key: str) -> dict | None:
        for view in self.VIEWS:
            if view["key"] == view_key:
                return view
        return None

    def _view_steps(self, view_key: str) -> list[str]:
        return issue_view_steps(view_key)

    def _view_of_step(self, step_key: str) -> str:
        return issue_view_of(str(step_key or ""))

    def _view_state(self, view_key: str) -> str:
        """视图聚合状态：任一步 error→红 / 有警告→琥珀 / running→动 / 全完成→绿 / 否则灰。"""
        states = [self._state_of(key) for key in self._view_steps(view_key)
                  if self._step_by_key(key)]
        if "error" in states:
            return "error"
        if "warn" in states:
            return "warn"
        if "running" in states:
            return "running"
        if states and all(state == "done" for state in states):
            return "done"
        return "idle"

    @staticmethod
    def _view_tone(state: str) -> str:
        return state if state in ("error", "warn", "running", "done") else "idle"

    def _select_view(self, view_key: str) -> None:
        """点栏内视图/横条：切到该视图**最近展开**的区块（没有则首个）。

        已在该视图时不重建（展开/收起只由区块头点击决定），避免把用户
        刚切好的手风琴状态冲掉。flet 1.0.5 的 scroll_to 静默无效，故这里
        不再有「滚到当前区块」的分支。
        """
        steps = self._view_steps(view_key)
        if not steps:
            return
        if self._view_of_step(self.selected) == view_key:
            return
        self._select(self.expanded_step(view_key) or steps[0])

    # ── 批 116 ISSUE4-C：视图内手风琴（替代无效的 scroll_to）────────────
    def expanded_step(self, view_key: str) -> str:
        """该视图当前展开的区块 key；"" = 全部收起。

        未设置过 → 默认展开首个区块（catalog=01 / produce=03 / 05 / 06）。
        同一视图只存一个值，天然保证「同一时刻只展开一个区块」。
        """
        steps = self._view_steps(view_key)
        if not steps:
            return ""
        expanded = getattr(self, "_expanded", None) or {}
        if view_key in expanded:
            step = str(expanded.get(view_key) or "")
            return step if step in steps else ""
        return steps[0]

    def _set_expanded(self, step_key: str) -> None:
        view_key = self._view_of_step(step_key)
        if not view_key:
            return
        # 兼容 `object.__new__(IssuePage)` 的旧测试桩（不跑 __init__）。
        if getattr(self, "_expanded", None) is None:
            self._expanded = {}
        self._expanded[view_key] = str(step_key)

    def _toggle_section(self, key: str) -> None:
        """点区块头：展开该区块（并收起同视图另一块）或收起它。"""
        view_key = self._view_of_step(key)
        if not view_key:
            return
        will_expand = self.expanded_step(view_key) != key
        if getattr(self, "_expanded", None) is None:
            self._expanded = {}
        self._expanded[view_key] = key if will_expand else ""
        self.mark("issue_accordion", step=key, expanded=will_expand)
        if self._view_of_step(self.selected) == view_key:
            self._rebuild_detail()

    def _rebuild_detail(self) -> None:
        """按当前 `_expanded` 重建视图详情（不动路由/选中步）。"""
        if (not self.selected
                or getattr(self, "_content_box", None) is None):
            return
        self._step_run_btns = {}
        self._primary_btns = {}
        self._detail_statuses = {}
        self._section_refs = {}
        self._section_heads = {}
        self._section_bodies = {}
        self._chevrons = {}
        self._detail_status = None
        self._detail_primary = None
        self._step_run_primary = None
        self._content_box.controls = [self._build_step_page(self.selected)]
        self.refresh_running()
        self._safe_update(self._content_box)

    def on_step_finished(self, key: str, code: int = 0) -> None:
        """整期步骤完成钩子（assembly.after_step 调用）。

        批 116 ISSUE4-C：03 编译成功 → 切到 04 合并区块展开、收起 03，
        但**不自动运行 04**（04 仍有独立确认点：覆盖检查 / 前置页确认）。
        其他区块完成不自动切换（01→02 由用户点）；不在「编译并合并」视图时
        只记住 produce 的展开状态，下次进该视图直接看到 04。
        flet 1.0.5 桌面 scroll_to（key/offset/page）实测静默无效，
        「首屏见 04」改由展开状态表达（截图为证，见批 116 issue4b 证据目录）。
        """
        if key == "infocheck" and code == 0:
            # 批 117：页内「先生成检查报告」成功后，最新报告回填目录参数，
            # 目录主按钮随之启用（不强制用户先跑整期 01）。
            self._latest_report()
            self._rebuild_detail()
            self._sync_nav()
            return
        if key != "compileall" or code != 0:
            return
        self._set_expanded("merge")
        if self._view_of_step(self.selected) == "produce":
            self.selected = "merge"
            self._rebuild_detail()

    def _primary_button(self, key: str) -> ft.Control:
        t = self.t
        label = self._primary_label(key)
        btn = C.primary_button(label, lambda e, k=key: self._primary_action(k), t,
                               height=t.px(28), disabled=self._primary_disabled(key))
        reason = self._primary_reason(key)
        if reason:
            btn.tooltip = reason
        self._step_run_btns[key] = btn
        self._primary_btns[key] = btn
        return btn

    def _primary_label(self, key: str) -> str:
        state = self._state_of(key)
        running = self.app.running and self._state_of(key) == "running"
        if key == "cleantemp":
            if running:
                return "正在移入…" if self._preview_valid() else "取消预演"
            if ASM.cleantemp_dry_run(self):
                return "预览清理"
            return "开始清理…" if self._preview_valid() else "开始清理…"
        if key == "compileall" and running:
            return "停止"
        if key == "infocheck" and running:
            return "取消"
        first, again = self.ACTIONS.get(key, ("运行", "重新运行"))
        if state in ("done", "warn", "error", "cancelled"):
            return again
        return first

    def _primary_disabled(self, key: str) -> bool:
        if not self.app.engine_status.ok:
            return True
        if not self.field("issue_dir"):
            return True
        if key == "toc" and not self._toc_report_ready():
            return True
        if self.app.running:
            return not (key == self.selected and key in ("compileall", "infocheck"))
        if key == "dirpush" and DIRPUSH.validation_error(self):
            return True
        return False

    def _primary_reason(self, key: str) -> str:
        if not self.field("issue_dir"):
            return "先在上方选择期目录"
        if not self.app.engine_status.ok:
            return "引擎未就绪：先到「设置 → 引擎」检查"
        if self.app.running:
            return "运行中：等当前步骤结束"
        if key == "toc" and not self._toc_report_ready():
            return ("还缺信息检查报告：点区块内「先生成检查报告」，"
                    "或在参数里手选一份报告")
        if key == "dirpush":
            err = DIRPUSH.validation_error(self)
            if err:
                return err
        return ""

    def _primary_action(self, key: str) -> None:
        if self.app.running:
            if key == "compileall":
                self._confirm_stop("停止编译？已完成篇目的 PDF 会保留；当前篇可能留下半成品，下次编译会覆盖。")
            elif key == "infocheck":
                self._confirm_stop("停止检查？已扫描的部分不会写入报告。")
            else:
                self.app.toast("运行中，完成后才能切换")
            return
        if not self.field("issue_dir"):
            self.app.toast("先选择期目录")
            return
        if key == "dirpush":
            err = DIRPUSH.validation_error(self)
            if err:
                self.app.toast(err)
                return
        if key == "cleantemp":
            self._cleantemp_primary()
            return
        if (key == "toc" and not ASM._toc_text_only(self)
                and not getattr(self.app, "auto_confirm", False)):
            def _go() -> None:
                self._run_step("toc")
            C.confirm(self.page, self.t, title="重新生成目录？",
                      message="会覆盖 000 目录中同名 tex/PDF 并重新编译（约 1–3 分钟）；稿件不受影响。",
                      confirm_label="重新生成", on_confirm=_go)
            return
        if key == "merge":
            out = self.field("merge_out")
            if (out and os.path.isfile(os.path.expanduser(out))
                and not getattr(self.app, "auto_confirm", False)):
                self._confirm_overwrite(out)
                return
        self._run_step(key)

    def _confirm_overwrite(self, path: str) -> None:
        t = self.t
        try:
            size = f"{os.path.getsize(os.path.expanduser(path)) / (1024 * 1024):.1f} MB"
            mtime = _dt.datetime.fromtimestamp(
                os.path.getmtime(os.path.expanduser(path))).strftime("%m-%d %H:%M")
        except OSError:
            size, mtime = "?", "?"
        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("覆盖已有文件？", t, "h2"),
            content=ft.Container(width=t.px(480), content=ft.Column([
                C.text(f"「{os.path.basename(path)}」已存在：{size}，{mtime} 修改。",
                       t, "small"),
                C.text("覆盖会原地写入，不能撤销，也不会进废纸篓。", t, "caption",
                       t.palette.text_muted),
                C.text(os.path.abspath(os.path.expanduser(path)), t, "mono",
                       t.palette.text_muted, max_lines=2),
            ], spacing=t.sp("1"), tight=True)),
            actions=[
                C.text_button("取消", lambda e: C.dismiss_top_dialog(self.page), t),
                C.danger_button("覆盖并合并", lambda e: self._merge_confirmed(), t),
            ],
        )
        self.page.show_dialog(dialog)

    def _merge_confirmed(self) -> None:
        C.dismiss_top_dialog(self.page)
        self._run_step("merge")

    def _confirm_stop(self, message: str = "已完成步骤保留，当前步标记已取消。") -> None:
        def _go() -> None:
            self.app.stop_run()
        C.confirm(self.page, self.t, title="停止当前步骤？", message=message,
                  confirm_label="停止", on_confirm=_go)

    # ── 运行整期对话框 ────────────────────────────────────────────────
    def _open_runall(self) -> None:
        t = self.t
        if not self.field("issue_dir"):
            self.app.toast("先选择期目录")
            return
        if self.app.running:
            self.app.toast("正在运行，先停止")
            return
        chosen = {step["key"]: i < 4 for i, step in enumerate(self.STEPS)}
        skip = {"done": True}
        rows: list = []
        danger = C.notice_bar(
            "05 会真实删除临时文件（移入系统回收站，可恢复）；本次未勾选「只预演」时不再二次确认。",
            t, "error")
        danger.visible = bool(chosen.get("cleantemp"))
        self._runall_danger = danger
        for index, step in enumerate(self.STEPS):
            if index == 4:
                rows.append(ft.Container(height=t.px(8)))
            if index == 4:
                rows.append(danger)
            rows.append(C.option_row(
                f"{index + 1:02d} {step['title']}",
                "默认不跑 · 危险" if index >= 4 else "整期装配",
                t, checked=chosen[step["key"]],
                on_change=lambda e, k=step["key"]: (
                    chosen.__setitem__(k, bool(e.control.value)),
                    setattr(danger, "visible", bool(chosen.get("cleantemp"))),
                    self._safe_update(danger))))
        rows.append(C.option_row(
            "跳过已完成的步骤",
            f"已完成 {sum(1 for s in self.STEPS if self._state_of(s['key']) == 'done')} 步",
            t, checked=True,
            on_change=lambda e: skip.__setitem__("done", bool(e.control.value))))
        def _start(e) -> None:
            C.dismiss_top_dialog(self.page)
            keys = [s["key"] for s in self.STEPS if chosen[s["key"]]]
            if skip["done"]:
                done = {k for k in keys if self._state_of(k) == "done"}
                keys = [k for k in keys if k not in done]
            if not keys:
                self.app.toast("没有要运行的步骤")
                return
            self._expand_log()
            self.page.run_task(ASM.run_all, self, keys)

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("运行整期", t, "h2"),
            content=ft.Container(width=t.px(600), height=t.px(470),
                                 content=ft.Column(rows + [
                                     ft.Container(height=t.px(4)),
                                     C.text("某步失败即停（rc≥2），已完成步骤保留；05/06 默认不跑。",
                                            t, "caption", t.palette.text_faint),
                                 ], spacing=t.sp("1"), scroll=ft.ScrollMode.AUTO)),
            actions=[C.text_button("取消", lambda e: C.dismiss_top_dialog(self.page), t),
                     C.primary_button("开始运行", _start, t)],
        )
        self.page.show_dialog(dialog)

    def _continue_from_failure(self) -> None:
        failed = [s["key"] for s in self.STEPS if self._state_of(s["key"]) == "error"]
        if not failed:
            self.app.toast("没有失败步骤")
            return
        first = failed[0]
        start = [s["key"] for s in self.STEPS].index(first)
        keys = [s["key"] for s in self.STEPS[start:] if self._state_of(s["key"]) != "done"]
        self._expand_log()
        self.page.run_task(ASM.run_all, self, keys)

    def _expand_log(self) -> None:
        self._log_expanded = True
        if self._log is not None:
            self._log.set_expanded(True)
        self._sync_log_reserve()

    # ── 参数 / 结果 ────────────────────────────────────────────────────
    def _build_params(self, key: str) -> ft.Control:
        self.fields = {k: v for k, v in self.fields.items()
                       if k in ("issue_dir", "toc_report", "compile_only", "merge_out",
                                "dirpush_out")}
        params = ASM.build_params(self, key)
        if isinstance(params, ft.Column) and params.controls:
            params.controls = params.controls[1:]
            self._drop_legacy_step_run(params)
        return params

    @staticmethod
    def _drop_legacy_step_run(params: ft.Column) -> None:
        if not params.controls:
            return
        last = params.controls[-1]
        if not isinstance(last, ft.Row):
            return
        kept = [c for c in last.controls
                if not (isinstance(c, ft.OutlinedButton)
                        and str(getattr(c, "content", "")) == "运行这一步")]
        if len(kept) != len(last.controls):
            if kept:
                last.controls = kept
            else:
                params.controls.pop()

    def _result_zone(self, key: str, *, banner: bool = True) -> ft.Control:
        t = self.t
        parts: list = []
        if banner and self._result is not None:
            parts.append(self._result.root)
        if key == "infocheck":
            parts.append(self._info_result())
        elif key == "toc":
            parts.append(self._toc_result())
        elif key == "compileall":
            parts.append(self._compile_result())
        elif key == "merge":
            parts.append(self._merge_result())
        elif key == "cleantemp":
            parts.append(self._cleantemp_result())
        elif key == "dirpush":
            parts.append(self._dirpush_result())
        return ft.Column(spacing=t.sp("2"), controls=parts)

    def _empty_result(self, title: str, desc: str, height: int = 72,
                      action: ft.Control | None = None) -> ft.Control:
        t = self.t
        inner = [C.text(title, t, "small", t.palette.text_muted),
                 C.text(desc, t, "caption", t.palette.text_faint,
                        text_align=ft.TextAlign.CENTER)]
        if action is not None:
            inner.append(action)
            height = max(height, 120)
        return ft.Container(
            height=t.px(height), border=ft.Border.all(1, t.palette.border),
            border_radius=t.radius("md"), alignment=ft.Alignment.CENTER,
            content=ft.Column(spacing=t.sp("half"), horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=inner),
        )

    def _info_result(self) -> ft.Control:
        t = self.t
        payload = self._info_payload
        if not payload:
            if not self.field("issue_dir"):
                return self._empty_result(
                    "还没有选择期目录",
                    "先选一期稿件的根目录，再运行检查；切到别的步再回来，结果还在。",
                    action=C.primary_button("选择期目录", self._browse_issue, t,
                                            height=t.px(30), icon="FOLDER_OPEN"))
            return self._empty_result(
                "还没有检查结果",
                "点右上「运行检查」，或按 Enter；切到别的步再回来，结果还在。")
        articles = self._info_articles
        verdict = CK.info_summary(payload, articles=articles, notes=self._info_notes)
        tone = "warn" if self._info_notes else "info"
        head = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
            C.text(verdict, t, "small", t.palette.warn if self._info_notes else t.palette.text,
                   max_lines=2, expand=True),
            CK.build_info_filter_chips(t, self._info_filter, self._set_info_filter),
        ])
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("检查结果", t, trailing=C.badge("只读", t, "success")),
            C.hairline(t),
            head,
            C.text(CK.info_counts(articles, self._info_notes), t, "caption",
                   t.palette.text_muted),
            CK.build_info_table5(t, articles, active_filter=self._info_filter,
                                 expanded=self._info_expanded,
                                 on_toggle=self._toggle_info_row,
                                 on_copy=self._copy_info_row),
            C.text(f"报告：{self._info_report or '—'}", t, "caption",
                   t.palette.text_faint, max_lines=2),
        ])

    def _toc_result(self) -> ft.Control:
        t = self.t
        if not self._toc_payload:
            return self._empty_result("还没有生成目录",
                                      "点右上「生成目录」；默认取最新信息检查报告。")
        payload = self._toc_payload
        placed = payload.get("placed") or {}
        column = payload.get("column") or {}
        lines = [
            f"输出：{placed.get('zhPdf') or placed.get('zhTex') or '—'}",
            f"篇目：{(payload.get('articles') or {}).get('inToc', '—')} 篇",
            f"栏目：{column.get('enabled', '—')} 个（{column.get('title', '—')}）",
            "已编译 / 已落位" if payload.get("compiled") else "仅 tex · 未落位、未编译",
        ]
        actions = []
        zh_pdf = str(placed.get("zhPdf") or "")
        zh_tex = str(placed.get("zhTex") or "")
        if zh_pdf:
            actions.append(C.text_button("打开 PDF", lambda e: self.app.open_path(zh_pdf), t))
        elif zh_tex:
            actions.append(C.text_button("打开 tex", lambda e: self.app.open_path(zh_tex), t))
        if report := self._latest_report():
            result_json = os.path.join(os.path.dirname(report), "目录结果.json")
            if os.path.exists(result_json):
                actions.append(C.text_button("打开结果 JSON",
                                             lambda e: self.app.open_path(result_json), t))
            actions.append(C.text_button("复制结果路径",
                                         lambda e: self.app.copy_text(str(placed.get("zhPdf") or placed.get("zhTex") or "")),
                                         t))
        if payload.get("textOnly"):
            lines.append("仅 tex · 未落位、未编译 · 步骤 4 合并需要 000 目录 PDF")
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("生成结果", t), C.hairline(t),
            *[C.text(line, t, "caption", t.palette.text_muted) for line in lines],
            ft.Row(spacing=t.sp("1"), wrap=True, controls=actions) if actions else ft.Container(),
        ])

    def _dirpush_result(self) -> ft.Control:
        t = self.t
        status = self._status.get("dirpush")
        artifact = str(getattr(status, "artifact", "") or "")
        state = self._state_of("dirpush")
        if not artifact and state != "error":
            return self._empty_result("还没有推送记录",
                                      "在上方选好输出根与范围，点右上「生成摘要」。")
        rows = [C.section_label("推送结果", t), C.hairline(t)]
        if artifact:
            rows.append(C.text(os.path.basename(artifact), t, "small", t.palette.text))
        if state == "error":
            # 失败通常没有产物；先渲染原因，不能因为 artifact 为空退回空态（B P2-1）。
            tail = self._tail_lines("dirpush", 3)
            rows.append(C.inline_note("推送失败：原因在下面末 3 行（日志可展开看全文）。",
                                      t, "error"))
            rows += [C.text(line, t, "mono", t.palette.error, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS) for line in tail]
        if artifact:
            rows.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                C.text_button("打开 docx", lambda e: self.app.open_path(artifact), t),
                C.text_button("打开所在目录",
                              lambda e: self.app.open_path(os.path.dirname(artifact)), t),
                C.text_button("复制路径", lambda e: self.app.copy_text(artifact), t),
            ]))
        return ft.Column(spacing=t.sp("1"), controls=rows)

    def _compile_result(self) -> ft.Control:
        t = self.t
        if not self._compile_rows:
            return self._empty_result("还没有编译记录",
                                      "点右上「开始编译」；可先只预演。")
        rows = [ft.Container(
            height=t.px(24), bgcolor=t.palette.surface_2,
            padding=C._pad(t, 8, 0, 8, 0),
            content=ft.Row(controls=[C.text("篇目", t, "caption", t.palette.text_muted, expand=5),
                                     C.text("状态", t, "caption", t.palette.text_muted, expand=2),
                                     C.text("耗时", t, "caption", t.palette.text_muted, expand=2),
                                     C.text("PDF", t, "caption", t.palette.text_muted, expand=5)]))]
        order = self._compile_order or list(self._compile_rows)
        for stem in order:
            row = self._compile_rows.get(stem) or {}
            status = row.get("status", "pending")
            word = {"pending": "等待", "compiling": "编译中", "ok": "成功",
                    "fail": "失败", "skip": "跳过"}.get(status, status)
            color = {"ok": t.palette.success, "fail": t.palette.error,
                     "compiling": t.palette.accent}.get(status, t.palette.text_muted)
            rows.append(ft.Container(
                height=t.px(32), padding=C._pad(t, 8, 0, 8, 0),
                content=ft.Row(controls=[
                    C.text(stem, t, "small", expand=5, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                    C.text(word, t, "caption", color, expand=2),
                    C.text(f"{row.get('seconds', '—')}s", t, "caption", expand=2),
                    C.text(os.path.basename(row.get("pdf", "") or "") or "—", t, "mono",
                           t.palette.text_muted, expand=5, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                ])))
        summary = self._compile_summary or f"计划 {len(order)} 篇"
        failed = [stem for stem in order
                  if (self._compile_rows.get(stem) or {}).get("status") == "fail"]
        actions: list = []
        if failed:
            actions.append(C.secondary_button("重试失败项", lambda e: self._retry_failed_compile(),
                                              t, height=28))
            actions.append(C.text_button("重新编译全部", lambda e: self._recompile_all(), t))
        if self._state_of("compileall") == "cancelled":
            prefixes = self._done_prefixes()
            remaining = [p for p in self._plan_prefixes() if p not in prefixes]
            actions.append(C.secondary_button(
                f"继续编译剩余 {len(remaining)} 篇",
                lambda e: self._continue_compile(remaining), t, height=28))
        report = os.path.join(self.field("issue_dir") or "", "编译报告.md")
        if os.path.exists(report):
            actions.append(C.text_button("打开编译报告.md",
                                         lambda e: self.app.open_path(report), t))
        return ft.Column(spacing=0, controls=[
            C.section_label("编译结果", t), C.hairline(t),
            C.text(summary, t, "caption", t.palette.text_muted),
            ft.Column(rows, spacing=0),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=actions) if actions else ft.Container(),
        ])

    def _plan_prefixes(self) -> list[str]:
        plan = self._compile_plan
        if not plan:
            plan = ASM.compile_plan(self.field("issue_dir") or "",
                                    self.field("compile_only") or "")
        return [target.prefix for target in plan]

    def _done_prefixes(self) -> list[str]:
        plan = {target.stem: target.prefix for target in self._compile_plan}
        return [plan[s] for s, row in self._compile_rows.items()
                if (row or {}).get("status") == "ok" and s in plan]

    def _set_compile_only(self, prefixes: list[str]) -> None:
        self.set_field("compile_only", ",".join(prefixes))

    def _retry_failed_compile(self) -> None:
        plan = {target.stem: target.prefix for target in self._compile_plan}
        failed = [plan[s] for s, row in self._compile_rows.items()
                  if (row or {}).get("status") == "fail" and s in plan]
        if not failed:
            self.app.toast("没有失败项")
            return
        self._set_compile_only(sorted(dict.fromkeys(failed)))
        self._run_step("compileall")

    def _recompile_all(self) -> None:
        self.set_field("compile_only", "")
        self._run_step("compileall")

    def _continue_compile(self, prefixes: list[str]) -> None:
        self._set_compile_only(prefixes)
        self._run_step("compileall")

    def _merge_result(self) -> ft.Control:
        t = self.t
        if not self._merge_info:
            return self._empty_result("还没有运行记录。",
                                      "第 3 步还没跑 —— 先编译，合并才不会缺篇。")
        info = self._merge_info
        try:
            pages = int(info.get("pages") or 0)
        except (TypeError, ValueError):
            pages = 0
        if pages <= 0:
            path = str(info.get("path") or "")
            return ft.Column(spacing=t.sp("1"), controls=[
                C.section_label("合并结果", t), C.hairline(t),
                C.inline_note("没有可合并的 PDF：先完成编译，或用「先检查」"
                              "确认期目录里的 PDF 是否可读。", t, "warn"),
                C.text(path, t, "mono", t.palette.text_faint, max_lines=1,
                       overflow=ft.TextOverflow.ELLIPSIS) if path else ft.Container(),
            ])
        lines = [f"✅ 合并完成 · {pages} 页"]
        if info.get("missing"):
            lines.append(f"⚠ 合并完成，缺 {info.get('missing')} 篇：{info.get('names', '')}")
        path = str(info.get("path") or "")
        actions: list = []
        if path:
            actions = [
                C.text_button("打开文件", lambda e: self.app.open_path(path), t),
                C.text_button("打开所在目录",
                              lambda e: self.app.open_path(os.path.dirname(path)), t),
                C.text_button("复制路径", lambda e: self.app.copy_text(path), t),
            ]
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("合并结果", t), C.hairline(t),
            *[C.text(line, t, "caption", t.palette.warn if "⚠" in line
                     else t.palette.text_muted) for line in lines],
            C.text(path, t, "mono", t.palette.text_faint, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS) if path else ft.Container(),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=actions) if actions else ft.Container(),
        ])

    # ── 批 116：04 合并区块的只读「前置页」卡 ────────────────────────────
    def _prep_card(self) -> ft.Control:
        """只读前置页卡：mergepdf --detect 的扉页/编委页/封底结果。

        批 116 范围（用户拍板 I-2）：**只显示不编辑**；编辑前置页是新引擎子系统
        （数据/模板/编译预览），已入想法池。空态说明「点检测」；有数据态显示
        文件名/页数/缺失，并提供「打开」。
        """
        t = self.t
        self._prep_box = ft.Container(
            content=ft.Column(spacing=t.sp("1"), controls=self._prep_rows()))
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("前置页", t, note="只读 · 不提供编辑"),
            self._prep_box,
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       C.secondary_button("检测前置页", lambda e: ASM.run_detect(self), t,
                                          icon="SEARCH", height=t.px(28)),
                       C.text("只探测扉页 / 编委页 / 封底，不合并；缺失的页合并时跳过。",
                              t, "caption", t.palette.text_faint, expand=True),
                   ]),
        ])

    def _prep_rows(self) -> list:
        """前置页卡内容（空态 / 有数据态），供 `_prep_card` 与检测后刷新共用。"""
        t = self.t
        payload = self._prep_pages
        if not payload:
            return [
                C.text("还没检测前置页", t, "small", t.palette.text_muted),
                C.text("点「检测前置页」只读列出扉页 / 编委页 / 封底；"
                       "编辑功能在后续批次提供。", t, "caption", t.palette.text_faint),
            ]
        return [
            self._prep_row("扉页", payload.get("coverPages"), count=True),
            self._prep_row("编委页", payload.get("editorsPage")),
            self._prep_row("封底", payload.get("backCover")),
        ]

    def _prep_row(self, label: str, value, *, count: bool = False) -> ft.Row:
        t = self.t
        path = ""
        if count:
            pages = int(value or 0)
            found = pages > 0
            detail = f"扉页.pdf · {pages} 页" if found else "未检测到扉页.pdf"
            if found:
                path = os.path.join(self._issue_dir(), "扉页.pdf")
        else:
            info = value if isinstance(value, dict) else {}
            name = str(info.get("fileName") or "")
            pages = int(info.get("pageCount") or 0)
            found = bool(name)
            detail = f"{name} · {pages} 页" if found else "未检测到"
            if found:
                path = os.path.join(self._issue_dir(), name)
        controls: list = [
            ft.Container(width=t.px(48),
                         content=C.text(label, t, "small", t.palette.text_muted)),
            C.text(detail, t, "small",
                   t.palette.text if found else t.palette.text_faint,
                   expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            C.badge("已找到" if found else "缺失", t,
                    "success" if found else "warn"),
        ]
        if path:
            controls.append(C.text_button(
                "打开", lambda e, p=path: self.app.open_path(p), t))
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=controls)

    def set_prep_pages(self, payload) -> None:
        """detect 结果落盘（`assembly._detect_task` 调用）：只读，不触发合并。"""
        self._prep_pages = dict(payload) if isinstance(payload, dict) else None
        box = getattr(self, "_prep_box", None)
        if box is None:
            return
        box.content = ft.Column(spacing=self.t.sp("1"), controls=self._prep_rows())
        self._safe_update(box)

    def _tail_lines(self, key: str, n: int = 3) -> list[str]:
        """该功能最近 n 行输出（失败时内联给读者，不必展开日志）。"""
        lines = [line for line in (self._step_tail.get(key) or []) if line]
        if not lines:
            lines = [line for line in self._log_buffer[-n:] if str(line).strip()]
        return [str(line) for line in lines[-n:]]

    def _cleantemp_result(self) -> ft.Control:
        t = self.t
        preview = self._ct_preview or {}
        if not preview:
            return self._empty_result("尚未预演", "先点「预览清理」看数量；预演不动磁盘。")
        if preview.get("ok"):
            rows = [C.section_label("清理结果", t), C.hairline(t),
                    C.text(f"预演：{preview.get('count', 0)} 项待清理 · "
                           f"{str(preview.get('ts', ''))[11:16]}", t, "caption",
                           t.palette.text_muted)]
            items = [str(x) for x in (preview.get("items") or []) if str(x).strip()]
            if not items:
                lines = [str(x) for x in (preview.get("lines") or []) if str(x).strip()]
                items = [line for line in lines
                         if "Will move" in line or "将移入" in line][:8]
            for item in items[:8]:
                rows.append(C.text(f"· {os.path.basename(item) or item}", t, "caption",
                                   t.palette.text_faint, max_lines=1,
                                   overflow=ft.TextOverflow.ELLIPSIS))
            if items:
                rows.append(C.text(f"清单 {len(items)} 项（完整清单在日志）", t, "caption",
                                   t.palette.text_faint))
            if preview.get("trash"):
                rows.append(C.text(f"目标：{preview.get('trash')}", t, "caption",
                                   t.palette.text_faint))
            return ft.Column(spacing=t.sp("1"), controls=rows)
        tail = self._tail_lines("cleantemp", 3)
        rows = [C.section_label("清理结果", t), C.hairline(t),
                C.inline_note(f"上次预演失败（rc={preview.get('code')}）："
                              f"{preview.get('error') or '看日志'}", t, "error")]
        if tail:
            rows.append(C.text("末 3 行：", t, "caption", t.palette.text_faint))
            rows += [C.text(line, t, "mono", t.palette.error, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS) for line in tail]
        rows.append(C.text_button("展开日志看全文", lambda e: self._expand_log(), t))
        return ft.Column(spacing=t.sp("1"), controls=rows)

    # ── infocheck 交互 ─────────────────────────────────────────────────
    def _set_log_filter(self, key: str) -> None:
        """切「本步 / 整期」；本次运行内记住用户选择。"""
        if key not in ("step", "all"):
            return
        self._log_filter = key
        self._log_user_filter = True
        self.page.update()

    def _set_info_filter(self, key: str) -> None:
        self._info_filter = key
        self._info_expanded = ""
        self._refresh_info_box()

    def _toggle_info_row(self, key: str) -> None:
        self._info_expanded = "" if self._info_expanded == key else key
        self._refresh_info_box()

    def _copy_info_row(self, article: dict) -> None:
        cells = [str(article.get("title") or ""),
                 CK.info_issue_year(article),
                 str(article.get("span") or ""),
                 CK.info_pages_cell(article)[0],
                 str(article.get("font_line") or "")]
        self.app.copy_text("\t".join(cells))

    def _refresh_info_box(self) -> None:
        box = getattr(self, "_info_box", None)
        if box is not None:
            box.controls = [self._info_result()]
            self._safe_update(box)

    # ── 状态 / 持久化 ──────────────────────────────────────────────────
    def _state_of(self, key: str) -> str:
        status = self._status.get(key, "idle")
        if hasattr(status, "state"):
            return str(status.state)
        return str(status or "idle")

    @staticmethod
    def _state_word(state: str) -> str:
        return {"idle": "未运行", "running": "运行中", "done": "成功",
                "warn": "有警告", "error": "失败", "cancelled": "已取消",
                "skipped": "已跳过"}.get(state, "未运行")

    def _tone(self, state: str) -> str:
        return {"done": "ok", "running": "run", "warn": "warn",
                "error": "error", "cancelled": "idle"}.get(state, "idle")

    def _status_text(self, key: str) -> str:
        state = self._state_of(key)
        status = self._status.get(key)
        word = self._state_word(state)
        duration = getattr(status, "duration_ms", None)
        if state in ("done", "warn") and duration:
            return f"{word} · {max(1, round(duration / 1000))} 秒"
        return word

    def _issue_state(self, issue: str) -> dict:
        data = self.app.store.get("issue_state", {}) or {}
        entry = data.get(issue) if isinstance(data, dict) else None
        return entry if isinstance(entry, dict) else {}

    def _persist_issue_state(self, issue: str = "") -> None:
        if not getattr(self, "fields", None):
            return
        issue = issue or self.field("issue_dir")
        # 换期时由 `_on_issue_changed` 显式传旧路径；空值不落库。
        if not issue:
            return
        data = dict(self.app.store.get("issue_state", {}) or {})
        data[issue] = {
            "schema": 1,
            "last_step": self.selected,
            "statuses": {
                key: (value.as_dict() if hasattr(value, "as_dict")
                      else {"state": str(value)})
                for key, value in self._status.items()
            },
        }
        self.app.store.set("issue_state", data)
        save = getattr(self.app, "save_settings", None)
        if callable(save):
            try:
                save(toast=False)
            except TypeError:
                save()

    def _restore_issue_state(self, *, keep_view: bool = False) -> None:
        issue = self.field("issue_dir")
        if not issue:
            return
        entry = self._issue_state(issue)
        if not entry:
            return
        statuses = entry.get("statuses") or {}
        for key, value in statuses.items():
            if key not in self._status or not isinstance(value, dict):
                continue
            state = str(value.get("state") or "idle")
            if state == "running":
                state = "cancelled"
            self._status[key] = ASM.StepStatus(
                state,
                exit_code=value.get("exit_code"),
                duration_ms=value.get("duration_ms"),
                started_at=value.get("started_at", ""),
                finished_at=value.get("finished_at", ""),
                artifact=value.get("artifact", ""),
                error=value.get("error", ""),
            )
        last = str(entry.get("last_step") or "")
        if not keep_view and last in [step["key"] for step in self.STEPS] \
                and not self.app.running:
            self.selected = last
        # 手风琴：恢复/当前步骤即该视图展开的区块（同视图另一块随之收起）。
        self._set_expanded(self.selected)
        if self._content_box is not None:
            self._content_box.controls = [self._build_step_page(self.selected)]

    def _reset_issue_runtime(self) -> None:
        """换期清空本页运行态与结果缓存（P0）。"""
        self._run_results = []
        self._info_payload = None
        self._info_articles = []
        self._info_notes = []
        self._info_report = ""
        self._info_filter = "all"
        self._info_expanded = ""
        self._compile_rows = {}
        self._compile_order = []
        self._compile_current = ""
        self._compile_summary = ""
        self._compile_plan = []
        self._merge_info = {}
        self._toc_payload = {}
        self._ct_preview = {}
        self._prep_pages = None
        self._prep_box = None
        self._step_tail = {}
        self._status = {step["key"]: "idle" for step in self.STEPS}
        # 目录面板缓存里有上一期的栏目表/预览；重建，避免带进新一期。
        self._toc_panel = None
        self._info = CK.issue_card(self)

    def _update_log_title(self, issue: str = "") -> None:
        """日志 dock 标题带上当前期名；换期后不再显示旧路径（B 包 P2-4）。"""
        log = getattr(self, "_log", None)
        if log is None:
            return
        issue = issue or self.field("issue_dir")
        title = "整期日志"
        if issue:
            title = f"整期日志 · {os.path.basename(os.path.normpath(issue))}"
        try:
            toolbar = log.root.content.controls[0]
            for control in toolbar.controls:
                if isinstance(control, ft.Text) \
                        and str(control.value).startswith("整期日志"):
                    control.value = title
                    control.update()
                    break
        except Exception:  # noqa: BLE001  未挂载/结构变化
            pass

    def _on_issue_changed(self, event=None) -> None:
        """换期（P0）：旧目录状态写回 issue_state，新目录清空运行态/结果/日志。

        此前只清 4 个字段，`_status/_merge_info/_compile_rows` 等仍是上一期的
        结果（B 包实测：C 目录显示 B 的「编译成功 · 21 秒」与 47 页合并卡）。
        现在换期整页重置，但**保持当前功能页**；空目录显示统一空态。
        """
        issue = self.field("issue_dir")
        previous = getattr(self, "_last_issue", "")
        if previous and previous != issue:
            self._persist_issue_state(previous)
        self._last_issue = issue
        self._reset_issue_runtime()
        if issue:
            # 新目录有上次记录就恢复状态，但不改变当前功能页（换期不跳走）。
            self._restore_issue_state(keep_view=True)
        self._set_expanded(self.selected)
        if getattr(self, "_content_box", None) is not None:
            self._step_run_btns = {}
            self._primary_btns = {}
            self._detail_statuses = {}
            self._section_refs = {}
            self._section_heads = {}
            self._section_bodies = {}
            self._chevrons = {}
            self._detail_status = None
            self._detail_primary = None
            self._step_run_primary = None
            self._content_box.controls = [self._build_step_page(self.selected)]
            self.refresh_running()
            self._sync_nav()
            self._sync_header()
            self._sync_run_actions()
            self._safe_update(self._nav_box, self._content_box)
            self._persist_issue_state()
        if self._log is not None:
            self._log.clear()
        self._log_step_key = ""
        self._log_buffer = []
        self._log_lines = 0
        self._update_log_title(issue)
        self.mark("issue_changed", issue=issue)

    # ── 运行入口 ───────────────────────────────────────────────────────
    def _run_step(self, key: str) -> None:
        # 统一入口：唯一主按钮与旧测试都走 ASM.run_step(self, key)；
        # 这里只负责日志展开与守卫，参数/确认逻辑见 _primary_action。
        if not self._issue_dir():
            self.app.toast("先选择期目录")
            return
        self._expand_log()
        ASM.run_step(self, key)

    def _run_all(self) -> None:
        self._open_runall()

    def _on_stop(self) -> None:
        self._confirm_stop()

    def _step_run_state(self) -> tuple[bool, str | None]:
        if self.app.running:
            return True, "运行中：等当前任务结束，或先点右上角「停止」"
        if not self.app.engine_status.ok:
            return True, "引擎未就绪：先到「设置 → 引擎」检查"
        return False, None

    def _select(self, key: str) -> None:
        if key not in [step["key"] for step in self.STEPS]:
            return
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"issue:{key}", source="page")
            return
        self.activate(key, source="page")

    def _apply_status(self, key: str, code: int, cancelled: bool,
                      duration: float = 0.0, artifact: str = "") -> None:
        if cancelled:
            state = "cancelled"
        elif code == 0:
            state = "done"
        elif code == 1:
            state = "warn"
        else:
            state = "error"
        if key == "merge" and state == "done" and self._merge_info:
            try:
                if int(self._merge_info.get("pages") or 0) <= 0:
                    state = "warn"
            except (TypeError, ValueError):
                pass
        self._status[key] = ASM.StepStatus(
            state, exit_code=code, duration_ms=duration,
            started_at=ASM._now(), finished_at=ASM._now(), artifact=artifact or "")
        refresh = getattr(self, "_refresh_steps", None)
        if callable(refresh):
            refresh()

    async def _run_calls(self, calls: list, sequential: bool = True) -> None:
        """逐条执行（测试/旧路径）；与 ASM._run_sequence 同样写状态、不清日志。"""
        for key, call in calls:
            started = time.monotonic()
            self._status[key] = ASM.StepStatus("running", started_at=ASM._now())
            self._refresh_steps()
            CK.before_issue_step(self, key)
            self.app.begin_run(call)
            try:
                code = await EB.run_call(call, on_line=self._on_line,
                                         on_proc=getattr(self.app, "track_proc", None),
                                         notes=self.app.notes_repo)
            except Exception as exc:  # noqa: BLE001
                code = -1
                self._result.show(f"❌ 任务启动失败：{exc}", level="error")
            self.app.finish_run(call, code)
            cancelled = bool(getattr(self.app, "cancel_requested", False))
            duration = (time.monotonic() - started) * 1000
            artifact = EB.artifact_path(str(getattr(self.app, "last_line", "") or ""))
            self._apply_status(key, code, cancelled, duration, artifact=artifact)
            try:
                await CK.after_issue_step(self, key, call, code, cancelled, artifact)
            except Exception:  # noqa: BLE001
                pass
            ASM.after_step(self, key, call, code, cancelled, artifact)
            self._refresh_steps()
            if cancelled or code >= 2:
                break

    LOG_LIMIT = 2000

    def _on_line(self, line: str) -> None:
        text = str(line)
        try:
            self.app.record_line(text)
        except Exception:  # noqa: BLE001
            pass
        stamped = _SECTION_RE.match(text)
        if stamped:
            self._log_step_key = stamped.group(1)
        step_key = self._log_step_key or self.selected
        tail = self._step_tail.setdefault(step_key, [])
        if text.strip():
            tail.append(text.strip())
            del tail[:-4]
        # 批 115 / D1-115-26：段头切分日志；「本步」过滤只显示当前步段，
        # 「整期」全显示。用户手动切换后本次运行不再自动跟随。
        show = True
        if self._log_filter == "step" and self._log_step_key:
            show = self._log_step_key == self.selected
        if show:
            self._log.add_line(text, _level_of_line(text), EB.is_summary_line(text))
        self._log_buffer.append(text)
        if len(self._log_buffer) > self.LOG_LIMIT:
            del self._log_buffer[:len(self._log_buffer) - self.LOG_LIMIT]
        self._log_lines += 1
        if self.selected == "compileall":
            self._feed_compile(text)
        if self.selected == "merge":
            self._feed_merge(text)
        if self.selected == "infocheck" and "正在扫描" in text:
            self._detail_status.value = text.strip()[:40]

    def _feed_compile(self, text: str) -> None:
        event = ASM.compile_event(text)
        if not event:
            return
        kind, data = event
        if kind == "start":
            self._compile_current = data["stem"]
            row = self._compile_rows.setdefault(data["stem"], {})
            row.update({"status": "compiling"})
            if data["stem"] not in self._compile_order:
                self._compile_order.append(data["stem"])
        elif kind == "ok":
            row = self._compile_rows.setdefault(data["stem"], {})
            row.update({"status": "ok", "seconds": data.get("seconds")})
            plan = {target.stem: target for target in self._compile_plan}
            if data["stem"] in plan:
                row["pdf"] = plan[data["stem"]].pdf
        elif kind == "fail":
            self._compile_rows.setdefault(data["stem"], {}).update(
                {"status": "fail", "seconds": data.get("seconds"),
                 "message": data.get("message", "")})
        elif kind == "summary":
            self._compile_summary = (f"{'✅' if not data['fail'] else '⚠'} "
                                     f"{data['ok']} 成功 / {data['fail']} 失败"
                                     f" · 共 {data['targets']} 篇")
        if self._detail_primary is not None and self.selected == "compileall":
            self._detail_status.value = f"运行中 · {len(self._compile_rows)} 篇已开始"

    def _feed_merge(self, text: str) -> None:
        match = _re.search(r"完成。合并文件：(.+?)（共 (\d+) 页）", text)
        if match:
            self._merge_info = {"path": match.group(1), "pages": int(match.group(2))}
        match = _re.search(r"⚠ 有 (\d+) 个预期文件没找到：(.+)", text)
        if match:
            info = dict(self._merge_info or {})
            info.update({"missing": int(match.group(1)), "names": match.group(2)})
            self._merge_info = info

    def _refresh_steps(self) -> None:
        if self._log is not None:
            self._sync_log_dock()
        self._sync_header()
        self._sync_run_actions()
        self._sync_info_from_card()
        if self.selected == "toc":
            self._sync_toc_payload()
        self._sync_nav()
        if not self.app.running:
            self._persist_issue_state()
        self._safe_update(self._nav_box, self._header_actions)

    def _sync_info_from_card(self) -> None:
        info = self._info
        if info is None:
            return
        articles = getattr(info, "articles", None)
        if articles is None:
            return
        self._info_articles = list(articles or [])
        self._info_payload = getattr(info, "payload", None) or (
            {"articles": articles} if articles else None)
        self._info_notes = list(getattr(info, "notes", None) or [])
        self._info_report = str(getattr(info, "report", "") or "")
        box = getattr(self, "_info_box", None)
        if box is not None:
            box.controls = [self._info_result()]

    def _sync_toc_payload(self) -> None:
        report = self.field("toc_report")
        if not report:
            return
        path = os.path.join(os.path.dirname(report), "目录结果.json")
        payload = EB.toc_result(path)
        if payload:
            self._toc_payload = payload

    def refresh_running(self) -> None:
        self._sync_run_actions()
        disabled, reason = self._step_run_state()
        for btn in self._step_run_btns.values():
            try:
                btn.disabled = disabled
                btn.tooltip = reason
                btn.update()
            except Exception:  # noqa: BLE001
                pass
        self._sync_detail_status()

    def _sync_detail_status(self) -> None:
        # 批 116：一个视图最多 2 个区块，状态文本与主按钮都按区块各自刷新。
        for key, status in getattr(self, "_detail_statuses", {}).items():
            if self._step_by_key(key) is None:
                continue
            status.value = self._status_text(key)
            status.color = self._state_color(self._state_of(key))
            self._safe_update(status)
        for key, btn in getattr(self, "_primary_btns", {}).items():
            if self._step_by_key(key) is None:
                continue
            btn.content = self._primary_label(key)
            btn.disabled = self._primary_disabled(key)
            reason = self._primary_reason(key)
            btn.tooltip = reason or None
            self._safe_update(btn)
        if self._detail_status is not None and self.selected:
            self._detail_status.value = self._status_text(self.selected)
            self._detail_status.color = self._state_color(self._state_of(self.selected))
        if self._detail_primary is not None and self.selected:
            self._detail_primary.content = self._primary_label(self.selected)
            self._detail_primary.disabled = self._primary_disabled(self.selected)
            reason = self._primary_reason(self.selected)
            self._detail_primary.tooltip = reason or None
        self._safe_update(self._detail_status, self._detail_primary)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            if control is None:
                continue
            try:
                control.update()
            except Exception:  # noqa: BLE001 未挂载（构建期/测试桩）
                pass

    def _state_color(self, state: str):
        t = self.t
        return {"done": t.palette.success, "running": t.palette.accent,
                "warn": t.palette.warn, "error": t.palette.error,
                "cancelled": t.palette.text_muted}.get(state, t.palette.text_faint)

    def _preview_valid(self) -> bool:
        return ASM.cleantemp_preview_ok(self)

    def _cleantemp_primary(self) -> None:
        if ASM.cleantemp_dry_run(self):
            self._expand_log()
            self.page.run_task(ASM._preview_task, self)
            return
        if not self._preview_valid():
            # P0（B 包）：预演失败时「开始清理」曾被永久拦死。预演只是数量提示，
            # 真正的安全闸是下面的危险确认框（档位 + 目标 + 输入「清理」）。
            self._open_clean_confirm()
            return
        self._open_clean_confirm()

    def _open_clean_confirm(self) -> None:
        t = self.t
        preview = self._ct_preview or {}
        issue = self.field("issue_dir")
        mode = ASM.cleantemp_mode(self)
        label = ASM.CLEANTEMP_MODES.get(mode, {}).get("title", mode)
        confirm_word = {"input": ""}
        can_go = {"ok": mode != "workspace"}
        def _on_confirm_word(e) -> None:
            value = e.control.value or ""
            confirm_word["input"] = value
            can_go["ok"] = value.strip() == "清理"
            btn.disabled = not can_go["ok"]
            try:
                btn.update()
            except Exception:  # noqa: BLE001 —— 构建期/未挂载
                pass

        field = C.text_field(t, hint_text="输入「清理」以继续", width=t.px(200),
                             on_change=_on_confirm_word)
        btn = C.danger_button("移入回收目录", lambda e: self._clean_now(), t,
                              disabled=not can_go["ok"])
        target = f"{issue}/.toolbox-trash/（新建目录）"
        target_box = ft.SelectionArea(content=ft.Container(
            padding=C._pad(t, 8, 6, 8, 6), bgcolor=t.palette.surface_2,
            border_radius=t.radius("sm"),
            content=C.text(target, t, "mono", t.palette.text, max_lines=None)))
        rows = [
            C.kv_row(t, "档位", f"{label} · {preview.get('count', 0)} 项"),
            ft.Column(spacing=t.px(2), controls=[
                C.text("目标", t, "caption", t.palette.text_faint),
                target_box,
            ]),
            C.text("数量来自刚才的预演，实际以运行时文件系统为准。", t, "caption",
                   t.palette.text_muted),
        ]
        if mode == "workspace":
            rows.append(field)
        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("开始清理？", t, "h2"),
            content=ft.Container(width=t.px(520),
                                 content=ft.Column(rows, spacing=t.sp("1"),
                                                   scroll=ft.ScrollMode.AUTO)),
            actions=[C.text_button("取消", lambda e: C.dismiss_top_dialog(self.page), t),
                     btn],
        )
        self.page.show_dialog(dialog)

    def _clean_now(self) -> None:
        C.dismiss_top_dialog(self.page)
        self._ct_preview = {}
        ASM.run_cleantemp_confirmed(self)

    # ── 兼容旧测试/旧入口 ──────────────────────────────────────────────
    def prefill(self, issue_dir: str = "", toc_report: str = "") -> None:
        if issue_dir:
            self.set_field("issue_dir", issue_dir)
        if toc_report:
            self.set_field("toc_report", toc_report)
        self._restore_issue_state()
        landing = self._default_view()
        if landing != self.selected:
            self.activate(landing, source="prefill")

    def select_step(self, key: str) -> None:
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"issue:{key}", source="page")
        else:
            self.activate(key, source="page")

    def run_step_demo(self, key: str) -> None:
        self._run_step(key)

    def run_all_demo(self) -> None:
        self._open_runall()

    # ── 键盘 ───────────────────────────────────────────────────────────
    def _install_keyboard(self) -> None:
        page = getattr(self.app, "page", None)
        if page is None or self._keyboard_installed:
            return
        if getattr(page, "on_keyboard_event", None) is None:
            page.on_keyboard_event = self._on_key
        self._keyboard_installed = True

    def _on_key(self, event) -> None:
        key = str(getattr(event, "key", "") or "")
        meta = bool(getattr(event, "meta", False) or getattr(event, "ctrl", False))
        if meta and key in ("1", "2", "3", "4"):
            view = self.VIEWS[int(key) - 1]
            self._set_expanded(self.expanded_step(str(view["key"]))
                               or self._view_steps(str(view["key"]))[0])
            self._select_view(str(view["key"]))
            return
        if meta and key in ("5", "6"):
            # ⌘5/⌘6：进清理/推送功能页并展开该页唯一区块。
            step = "cleantemp" if key == "5" else "dirpush"
            self._set_expanded(step)
            if self._view_of_step(self.selected) == self._view_of_step(step):
                self._rebuild_detail()
            else:
                self._select(step)
            return
        if key == "ArrowDown":
            self._move_selection(1)
            return
        if key == "ArrowUp":
            self._move_selection(-1)
            return
        if key == "Escape":
            if self._log_expanded and self._log is not None:
                self._log_expanded = False
                self._log.set_expanded(False)
                self._sync_log_reserve()
                return
            if self.app.running:
                self._confirm_stop()

    def _move_selection(self, delta: int) -> None:
        keys = [step["key"] for step in self.STEPS]
        current = self.selected if self.selected in keys else keys[0]
        index = max(0, min(len(keys) - 1, keys.index(current) + delta))
        self._select(keys[index])

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _issue_dir(self) -> str:
        return self.field("issue_dir")

    def _build_step_call(self, key: str):
        if not self._issue_dir():
            raise ValueError("请先选择期目录")
        return ASM.build_step_call(self, key)

    def _latest_report(self) -> str:
        import ui.toc as TOC

        report = TOC.latest_report(self._issue_dir())
        if report:
            self.set_field("toc_report", report)
        return report

    def _step_title(self, key: str) -> str:
        step = self._step_by_key(key)
        return str(step["title"]) if step else key

    def _step_desc(self, key: str) -> str:
        step = self._step_by_key(key)
        return str(step["desc"]) if step else ""

    def _step_by_key(self, key: str) -> dict | None:
        for step in self.STEPS:
            if step["key"] == key:
                return step
        return None

    def _logical_width(self) -> float:
        page = getattr(self.app, "page", None)
        try:
            width = float(getattr(page, "width", 0) or 0)
            height = float(getattr(page, "height", 0) or 0)
        except (TypeError, ValueError):
            width = height = 0.0
        if width == 800 and height == 600:
            width = 0.0
        if width <= 0:
            try:
                width = float(getattr(self.app, "window_w", 0) or 0)
            except (TypeError, ValueError):
                width = 0.0
        return width

    def _narrow(self) -> bool:
        width = self._logical_width()
        if width <= 0:
            try:
                width = float(self.app._design_width())
            except (AttributeError, TypeError, ValueError):
                return False
        try:
            rail = width < BREAK["rail"]
            sidebar = self.app.t.rail_width / max(self.app.t.k, 1e-6) if rail else SIDEBAR_W
        except (AttributeError, TypeError, ValueError):
            sidebar = SIDEBAR_W
        return width - sidebar < 880

    def on_resize(self) -> None:
        if getattr(self, "_shell", None) is None:
            return
        # 批 117：总览树已删除；宽窄切换只换外壳（rail ↔ 横条），详情内容保留，
        # 避免运行中的输入框被重置。
        self._refresh_layout()


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


class BibPage(BH.BibHost):
    """引用库薄宿主（批 115 BHOST）：10 个页面模块的路由 / 懒加载 / 宿主 API。

    页面自绘 `bib_shell.page_shell`（唯一 h1、权限 chip、最多 1 个动作 + 齿轮），
    宿主只切内容、转发生命周期并维护旧 BibPage 的兼容成员；实现见 `app/ui/bib_host.py`。
    """
    # 兼容旧模块级常量：测试与说明文案仍从 pages 导入。
    WRITE_NOTICE = BIB_WRITE_NOTICE
    WRITE_VIEWS = BIB_WRITE_VIEWS


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
        # 批 115 P3-C2：12 张 C.card 长滚 → 4 组发丝分区（引擎与环境 / 外观 /
        # 窗口与托盘 / 路径与关于）。组 = section_label + 1px 线，组内不再套卡片；
        # settings_shell / ai_panel 的卡片用 _flat 去壳（那两个文件不在写域）。
        engine_line = C.inline_note(
            f"引擎{'就绪' if engine.ok else '未就绪'} · {engine.version or '版本未知'}",
            t, "ok" if engine.ok else "warn")
        engine_rows = C.block(
            *[C.kv_row(t, label, value, mono=label in ("引擎目录", "设置文件"))
              for label, value in status_rows],
            ft.Row(spacing=t.sp("1"), controls=[
                C.secondary_button("重新检测", lambda e: self._rescan(), t, icon="REFRESH"),
                C.secondary_button("引擎目录…", lambda e: self._browse_engine(), t, icon="FOLDER_OPEN_OUTLINED"),
            ]),
            t=t, gap="1",
        )

        options = self.app.store.get("options", {}) or {}
        appearance = C.block(
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
            t=t, gap="2",
        )

        tray = SHELL.tray_card(self)

        paths = C.block(
            C.section_label("路径", t, note="引擎目录留空 = 自动定位"),
            C.hairline(t),
            self._path_field("engine_override", "引擎目录（可空）", kind="dir"),
            self._path_field("notes_repo", "私有笔记仓（可空）", kind="dir"),
            t=t, gap="1",
        )

        about_rows = [
            ("应用版本", self.app.app_version),
            ("Flet / Python", f"{self.app.flet_version} · {self.app.python_version}"),
            ("窗口 / DPI", f"{self.app.window_w}×{self.app.window_h} @ {self.app.dpi} ({self.app.dpi / 96:.0%})"),
            ("IA", f"{ia_stats()['legacy_pages']} 个旧页 → {ia_stats()['new_pages']} 页 + 子流程"),
            ("许可", "专有软件 · © 2026 Jeremymming（第三方许可见 THIRD_PARTY.md）"),
        ]
        about = C.block(
            C.section_label("关于", t),
            C.hairline(t),
            *[C.kv_row(t, label, value, mono=label == "应用版本")
              for label, value in about_rows],
            t=t, gap="1",
        )

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
        # 批 112 A-06 的「环境检查首屏可见」保留：并入「引擎与环境」组首（inline_note
        # 给出引擎状态/版本，下面就是「运行环境检查」与上次结果槽）。
        page = C.page_body(
            header,
            self._group("引擎与环境", t,
                        engine_line,
                        self._flat(envcheck, "环境检查"),
                        engine_rows,
                        self._flat(AIP.AiSettings(self).build(), "AI"),
                        self._flat(proxy, "网络代理"),
                        self._flat(logs, "日志"),
                        self._flat(diagnostics, "诊断包")),
            self._group("外观", t, appearance),
            self._group("窗口与托盘", t, self._flat(tray, "窗口与托盘")),
            self._group("路径与关于", t, paths,
                        self._flat(advanced, "高级"), about),
            t=t, gap=12, panel=True,
        )
        return page

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _group(self, label: str, t, *controls, note: str | None = None) -> ft.Column:
        """设置页分区：11.5 分区小标题 + 1px 发丝线 + 组内内容（无卡片底）。

        组标题与组内去壳小标题同名时（如「窗口与托盘」只有一张卡）合并成一行，
        note 上提到组头，避免同一分区出现两层同名标题。
        """
        body: list = []
        for control in controls:
            column, head = self._titled_column(control)
            if column is not None and head is not None and str(head.controls[0].value) == label:
                note = note or self._row_note(head, label)
                column.controls = column.controls[1:]
                control = column
            body.append(control)
        head = ft.Column(spacing=t.sp("half"), controls=[
            C.section_label(label, t, note=note),
            C.hairline(t),
        ])
        return ft.Column(spacing=t.sp("1"), controls=[head, *body])

    @staticmethod
    def _titled_column(control: ft.Control):
        """返回 (Column, 首行 Row)；只认「首行第一个控件是 Text」的标题行。"""
        if isinstance(control, ft.Container) and isinstance(control.content, ft.Column):
            column = control.content
        elif isinstance(control, ft.Column):
            column = control
        else:
            return None, None
        if column.controls:
            head = column.controls[0]
            if (isinstance(head, ft.Row) and head.controls
                    and isinstance(head.controls[0], ft.Text)):
                return column, head
        return None, None

    @staticmethod
    def _row_note(head: ft.Row, title: str) -> str | None:
        for item in head.controls[1:]:
            if isinstance(item, ft.Text) and item.value and str(item.value) != title:
                return str(item.value)
        return None

    def _flat(self, control: ft.Control, title: str) -> ft.Control:
        """给 settings_shell / ai_panel 的卡片去壳（定义不在本包写域，勿改那两处）。

        取 Container.content 的 Column，把首行该卡的 `section_title` 换成同文案的
        `section_label`（保留 note）；其余控件/回调原样返回。
        """
        t = self.t
        column, head = self._titled_column(control)
        if column is None:
            return control
        if head is None or str(head.controls[0].value) != title:
            return column
        note = self._row_note(head, title)
        column.controls = [C.section_label(title, t, note=note), *column.controls[1:]]
        return column

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
