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
from ui import cleanup as CLEANUP
from ui import components as C
from ui import dirpush as DIRPUSH
from ui import metadata_form as MF
from ui import normalize as NR
from ui import settings_shell as SHELL
from ui import task_feedback as TF
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

# P04 R04：合并契约行与告警行（引擎 mergepdf.py 的输出格式，见 CONTRACT.md §1）。
# 这些是合并专属标记，用来把「运行中切到别的步骤」时的 merge 结果仍归到合并页。
_MERGE_DONE_RE = _re.compile(r"完成。合并文件：(.+?)（共\s*(\d+)\s*页）")
_MERGE_MISSING_RE = _re.compile(r"⚠\s*有\s*(\d+)\s*个预期文件没找到：(.+)")
_MERGE_WARN_HEAD_RE = _re.compile(r"⚠\s*注意事项\s*[：:]?\s*$")
_MERGE_WARN_ITEM_RE = _re.compile(r"^\s*[·•]\s*(.+)$")
_MERGE_FAIL_RE = _re.compile(r"❌\s*合并失败\s*[：:]\s*(.+)")
_MERGE_ANY_WARN_RE = _re.compile(r"^\s*⚠\s*(.+)$")
# 只收「合并专属」标记；「⚠ 注意事项」编译步骤也会打，不能作为归属依据。
_MERGE_MARKER_RE = _re.compile(
    r"完成。合并文件：|⚠\s*有\s*\d+\s*个预期文件没找到：|❌\s*合并失败")


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


def _reject_when_running(page, entry: str, action: str = "开始新的运行") -> bool:
    """运行中拒绝新的运行入口：可见 toast + 遥测，不排队、不并发（P08 I-5 / R02）。

    静默 `return` 会让用户以为点击没生效（或者以为已经在跑），所以统一走这里。
    返回 True 表示已经拒绝，调用方应直接返回。
    """
    app = page.app
    if not getattr(app, "running", False):
        return False
    try:
        app.toast(f"运行中不能{action}：等当前任务结束，或先点「停止」")
    except Exception:  # noqa: BLE001 页面桩/证据脚本
        pass
    try:
        app.mark("run_rejected", entry=entry, reason="running")
    except Exception:  # noqa: BLE001
        pass
    return True


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
        panel = C.page_body(
            self._band(),
            self._section("继续上次", trailing=self._trailing("全部记录", "single")),
            self._continue_rows(),
            self._section("工作入口", note="单篇 → 整期 → 引用库"),
            self._entries(),
            self._section("最近活动", trailing=self._activity_link()),
            self._activity_rows(),
            t=t, pad=(24, 12, 24, 10), panel=True,
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
            ("引用库", "bib_out", "bib", "查库 · 入藏 · 回读校验"),
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
            ("LIBRARY_BOOKS_OUTLINED", "引用库", "查库 · 入藏 · 回读校验", "bib"),
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
            "desc": "LaTeX / Word → 初排 tex",
        },
        "proofcheck": {
            "label": "一校核查",
            "icon": "SEARCH",
            "desc": "初排 tex 一校核查，输出报告。",
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
        # 批 119：convert/proofcheck 的页内子导航与整期/引用库共用 C.page_nav；
        # 放在页头下、内容面板顶部（28 高 chip，选中 nav_sel）。
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
                           t=t, gap=10,
                           overlay=self._log.dock_root, overlay_reserve=0,
                           panel=True, tail=self._log_spacer)
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

    def _build_flow_chips(self) -> ft.Container:
        """页内步骤导航条（批 119：与整期/引用库共用 C.page_nav 唯一实现）。"""
        t = self.t
        groups = [[{"key": key, "label": flow["label"]}
                   for key, flow in self.FLOWS.items()]]
        nav = C.page_nav(
            t, groups=groups, active=self.flow_key,
            on_click=self._switch_flow,
            # 页体已有 24 内边距；条内不再叠加，chip 左缘与页头对齐。
            bar_pad=(0, 0, 0, 0),
            palette_getter=lambda: self.t.palette)
        self._flow_chips = list(nav._page_nav_chips)
        return nav

    def _sync_chips(self) -> None:
        """activate 后原地换导航 chip 的选中态（条壳保留，不重建整页）。"""
        bar = getattr(self, "_chip_row", None)
        if bar is None:
            return
        fresh = self._build_flow_chips()
        bar.content = fresh.content
        self._safe_update(bar)

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

    def nav_focus_target(self):
        """导航落点：只取当前子步骤自己的输入框。

        FIX2R2-3 K7：`fields` 按构建序登记，`_advanced_params()` 先于输入卡执行，
        基类取第一个字段会落到只收数字的「卷」——切页后立即键入字母会被输入过滤
        静默丢弃。这里按用户真正要填的输入顺序选：源稿件 → 输出根目录，其余交给
        基类（已挂载的第一个字段）。

        FIX2R2-4 F2-12：`fields` 是跨子步骤累积的；切到「一校核查」后旧「源稿件」
        仍留在字典里，若继续把它当落点，会在该框已从控件树移除后发 focus 往返，
        客户端收到失效控件的方法调用并断开 session（表现为整页重建循环）。因此
        落点必须按 `flow_key` 限定在本步骤自己的字段里。
        """
        keys = {
            "convert": ("single_source", "single_out"),
            "proofcheck": ("proof_tex",),
        }.get(getattr(self, "flow_key", ""), ())
        for key in keys:
            ref = self.fields.get(key) or []
            control = ref[0] if ref else None
            if control is not None and callable(getattr(control, "focus", None)):
                return control
        return None

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
                                "产物写到该目录下的 <作者>/ 里", kind="dir", show_note=False),
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
                "convert_refs", "附加参考文献", "如 extra.bib", kind="file")),
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_sty", "样式文件", "如 SLCN-MIX.sty", kind="file")),
            ft.Container(expand=True, content=self._adv_path_input(
                "convert_skeleton", "模板骨架", "如 templates/slcn", kind="dir")),
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
            ("编译 PDF", "生成正文 PDF；日志实时滚动",
             "PICTURE_AS_PDF_OUTLINED", "convert_compile", True, "主要"),
            ("清理临时文件", "删除 .aux / .log 等中间文件",
             "CLEANING_SERVICES", "convert_clean", False, None),
            ("导出原稿 PDF", "保留原始稿件并另存一份 PDF 备查",
             "PICTURE_AS_PDF_OUTLINED", "convert_source_pdf", False, None),
            ("强制覆盖", "产物已存在时仍重跑并覆盖（谨慎）",
             "WARNING_AMBER_ROUNDED", "convert_force", False, None),
            # 批 116 SINGLE-FN（F1 P0-1）：引擎 --dry-run 在生成前返回，
            # 只算「将要做什么」；默认关，勾选后仍走同一套 argv。
            ("只预演（不写入）", "只列出将生成的文件，不写任何文件",
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
        if _reject_when_running(self, "single_run"):
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
            self.app.toast("已切到「引用库 → 回读校验」")

    def _on_metadata_preview(self, e=None) -> None:
        """CV-002/003：跑与「开始转换」同一 argv，只多 --metadata-preview-out（只读）。"""
        if _reject_when_running(self, "metadata_preview", "预览元数据"):
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
        if self.app.begin_run(call) is False:
            self.mark("run_rejected", entry="single_run", reason="running")
            return
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
            text = f"失败 · 无法启动任务：{exc}"
            self._result.show(text, level="error")
            self.app.toast(f"任务启动失败：{exc}")
            self.mark("run_start_error", error=f"{type(exc).__name__}: {exc}")
            return
        self.app.finish_run(call, code)
        level = "warn" if self.app.cancel_requested else EB.result_level(call.entry, code)
        text = "已取消" if self.app.cancel_requested else EB.result_text(call.entry, code)
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
         "desc": "读取最新信息检查报告，生成中英文目录 tex；可写入并编译。"},
        {"key": "compileall", "title": "编译全部", "icon": "TERMINAL",
         "desc": "逐篇 xelatex ×3（含 biber）；跳过 000；同名 PDF 覆盖。"},
        {"key": "merge", "title": "合并 PDF", "icon": "PICTURE_AS_PDF",
         "desc": "按篇序合并整期 PDF；可插编委页 / 封底 / 书签。"},
        {"key": "cleantemp", "title": "清理临时文件", "icon": "DELETE_SWEEP_OUTLINED",
         "desc": "一次点击自动校验并回收 aux / 日志 / 中间件 / 时间戳 tex / 目录结果 / 旧信息检查；移入回收区，可撤销。"},
        {"key": "dirpush", "title": "推送目录摘要", "icon": "IOS_SHARE",
         "desc": "生成《目录摘要》docx 并写入推送根；只预演时不动文件。"},
    ]
    ACTIONS = {
        "infocheck": ("运行检查", "重新检查"),
        "toc": ("生成目录", "重新生成"),
        "compileall": ("开始编译", "重新编译"),
        "merge": ("合并 PDF", "重新合并"),
        "cleantemp": ("清理临时文件", "重新清理"),
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
        self._dirpush_payload = None
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
        # P04 R05：本轮合并的运行归属（state/code/artifact）。_merge_info 只放本次
        # 契约行解析出的产物信息；_merge_run 记录本次结果归属，取消/失败时绝不拿
        # 上一轮成功卡冒充本轮。空 dict = 没有本次运行（旧数据/换期后），走兼容渲染。
        self._merge_run: dict = {}
        self._merge_warn_capture = False
        self._merge_box = None
        # P04 R05-3：当前真正在跑的步骤（装配层在启动/结束时维护）。引擎输出行没有
        # 段头时用它归属日志，避免「运行中切到别的步骤」把 merge 结果串到别处。
        self._active_step = ""
        self._toc_payload: dict = {}
        # B-375 OBS-2：结果所属报告、本轮失败态、结果区容器（构建期缓存、步骤结束后重建）。
        self._toc_payload_report = ""
        # Lead round3 task-19：结束态（成功/失败/取消）的独立归属。结果 JSON 的归属
        # _toc_payload_report 只在读到 payload 后才设置，没有旧结果 JSON 时恒为空，
        # 不能拿它判断「是否换报告」——否则普通刷新会清掉本轮 failed/cancelled/code。
        self._toc_state_report = ""
        self._toc_run_failed = False
        self._toc_run_cancelled = False
        self._toc_run_code = None
        # Lead round2 C1（结果归属）：本会话内最近一次 toc 运行属于哪份报告；只有
        # _toc_run_report == 当前报告且 rc 0/1 真跑完时才能说「本次」。
        self._toc_run_report = ""
        self._toc_payload_this_run = False
        self._toc_box = None
        self._ct_preview: dict = {}
        # T3：清理页单一入口（扫描→清理→结果/撤销/明细）；旧 _ct_preview 仅兼容。
        self._cleanup = CLEANUP.CleanupPanel(self)
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
        self._step_box = None
        self._nav_chips: dict = {}
        self._last_issue = ""
        self._step_tail: dict = {}
        # 正文产物 Open 的归属代次：整期身份每变一次 +1（C-1）。
        self._issue_generation = 0
        # R7-a：运行中期身份锁定（字段/更换/清空回滚到本轮身份，只提示一次）。
        self._issue_field = None
        self._issue_browse_btn = None
        self._issue_clear_btn = None
        self._issue_lock_warned = False

    # ── 页面骨架 ───────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="整期日志", dock=True,
                                 toast=self.app.toast, mark=self.mark)
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        # 结果条快照：随 issue_state 一期一条落库，换期/重启时按原样恢复。
        self._banner = None
        self._info = self._new_info_card()
        self._child_title = C.text("整期装配", t, role="page-title")
        self._flow_desc = C.text("一期稿件，从检查到合刊。", t, "small",
                                 color=t.palette.text_muted)
        # FIX2R2-3 K7：期目录行仍在功能页顶部，但键入/换期只重建其下的
        # `_step_box`（兄弟节点），聚焦中的期目录输入框不进 patch。
        #
        # 批 120 B-354：页头两个整期入口按钮已删除（入口只剩键盘与空态 CTA）；
        # 运行整期入口仍保留键盘与空态 CTA，页头只在运行中显示「停止」。
        self._stop_btn = C.danger_button("停止", lambda e: self._confirm_stop(), t,
                                         icon="STOP")
        self._stop_btn.visible = False
        self._step_run_btns = {}
        self._title_col = ft.Column(spacing=t.sp("xs"), expand=True,
                                    controls=[self._child_title, self._flow_desc])
        header = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[self._title_col, self._stop_btn])
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
        self._sync_stop_action()
        self._install_keyboard()
        body = C.page_body(header, self._shell, t=t, gap=10,
                           overlay=self._log.dock_root, overlay_reserve=0, panel=True,
                           tail=self._log_spacer)
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

        # MC-01：一段编辑只在**失焦**时提交整期身份。逐字输入只记值；
        # 此前首字符就调 `_on_issue_changed`，`_last_issue` 停在 `/`，
        # 状态/产物按错误键落库。
        def _changed(e) -> None:
            value = (e.control.value or "").strip()
            # R7-a：运行中身份不能变，输入框/持久 recent 也不能先变成另一期。
            if self._issue_change_locked():
                self._reject_issue_change(e.control)
                return
            self.app.store.set_recent("issue_dir", value)
            self.mark("path_change", field="issue_dir", value=e.control.value)
            # FIX2R2-3 K7：只重建期目录行下面的 `_step_box`（兄弟节点），
            # 聚焦中的输入框不在 patch 范围内。
            C.mark_event_updated("issue_path_typing")

        def _commit(e=None) -> None:
            # MC-01：失焦是整期身份的提交点，一次完成
            # 「旧期写回 → 新期恢复 → 正文原地刷新」；值没变则什么都不做。
            # blur 自身不发送 update，显式标记避免框架 after_event 补整页 patch。
            value = getattr(getattr(e, "control", None), "value", None)
            if value is None:
                value = ref[0].value if ref else ""
            if self._issue_change_locked() and str(value or "").strip() != getattr(
                    self, "_last_issue", ""):
                self._reject_issue_change(ref[0] if ref else None)
                C.mark_event_updated("issue_path_blur")
                return
            if self._commit_issue_identity(str(value or "")):
                # MC-01：失焦提交后正文刷新要延后一拍。blur 与紧随的点击是
                # 同一次按压：此刻立刻替换区块头/正文，抬起时的 tap 落到已
                # 卸载的控件上就被取消（N6：点运行没有任何 click 事件）。
                # 日志/标题可立即换；正文等点击窗口过去再刷新。
                self._clear_issue_log()
                self._update_log_title()
                self.mark("issue_changed", issue=getattr(self, "_last_issue", ""))
                self._defer_after_commit_refresh(getattr(self, "_last_issue", ""))
            C.mark_event_updated("issue_path_blur")

        field = C.floating_field(
            t, "期目录", value=value,
            hint="一期稿件的根目录，含 000 / 01 作者…",
            on_change=_changed, on_blur=_commit, expand=True, clearable=False)
        ref.append(field)
        self._issue_field = field

        def _browse(e) -> None:
            if self._reject_issue_change_when_running():
                return
            self.app.browse_field(ref, "选择期目录", "dir", "issue_dir")
            # 异步轮询必须显式调度：直接调用只会得到未 await 的协程（换期不重置 P0）。
            self.page.run_task(self._watch_issue_pick, ref, (ref[0].value or "").strip())

        browse_btn = C.secondary_button("更换…", _browse, t, icon="FOLDER_OPEN")
        browse_btn.width = t.px(112)
        clear_btn = C.text_button("✕", lambda e: self._clear_issue(), t)
        # R03：纯符号按钮没有可读名称，悬停/屏幕阅读器都无从理解（GB 审计唯一缺口）。
        clear_btn.tooltip = "清空期目录"
        self._issue_browse_btn = browse_btn
        self._issue_clear_btn = clear_btn
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
                # R7-a：选择器可能在开跑后才回填，运行中一律拒绝。
                if self._issue_change_locked():
                    self._reject_issue_change(ref[0] if ref else None)
                    return
                self.app.store.set_recent("issue_dir", value)
                self._on_issue_changed(None)
                return

    def _browse_issue(self, e=None) -> None:
        """无期目录空态的 CTA：直接唤起现有目录选择器。"""
        if self._reject_issue_change_when_running():
            return
        ref = self.fields.get("issue_dir") if getattr(self, "fields", None) else None
        if ref is None:
            ref = []
            self.fields["issue_dir"] = ref
        self.app.browse_field(ref, "选择期目录", "dir", "issue_dir")
        before = (ref[0].value or "").strip() if ref else ""
        self.page.run_task(self._watch_issue_pick, ref, before)

    def _clear_issue(self) -> None:
        if self._reject_issue_change_when_running():
            return
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
        """薄包装：单 chip 构建走 C.nav_chip（批 119 三页唯一实现）。"""
        t = self.t
        key = str(view["key"])
        state = self._view_state(key)
        chip = C.nav_chip(
            str(view["title"]), t, active=active,
            on_click=lambda e, k=key: self._select_view(k),
            tone=self._view_tone(state),
            key=f"issue-nav-{key}",
            tooltip=f"{view['title']}：{self._state_word(state)}",
            palette_getter=lambda: self.t.palette)
        self._nav_chips[key] = chip
        return chip

    def _nav_hover(self, e, chip: ft.Container, active: bool) -> None:
        C.nav_hover(self.t, e, chip, active,
                    palette_getter=lambda: self.t.palette)

    def _nav_bar(self) -> ft.Control:
        """36 高导航条（窄窗 72）：批 119 共用 C.page_nav；整期窄窗 2+2 折行。"""
        t = self.t
        items = []
        for view in self.VIEWS:
            key = str(view["key"])
            state = self._view_state(key)
            items.append({
                "key": key,
                "label": str(view["title"]),
                "tone": self._view_tone(state),
                "tooltip": f"{view['title']}：{self._state_word(state)}",
            })
        return C.page_nav(
            t, groups=[items], active=self._view_of_step(self.selected),
            on_click=self._select_view, narrow=self._nav_narrow(),
            narrow_after=str(self.VIEWS[1]["key"]),
            key_prefix="issue-nav-",
            chip_attr=lambda chip, item, is_active: self._nav_chips.__setitem__(
                str(item["key"]), chip),
            bar_key="issue-nav",
            # 页体已有 24 内边距；条内不再叠加，chip 左缘与页头对齐。
            bar_pad=(0, 0, 0, 0),
            palette_getter=lambda: self.t.palette)

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

    def _sync_stop_action(self) -> None:
        """批 120 B-354：页头只保留运行中的「停止」，其余状态无页头按钮。"""
        stop = getattr(self, "_stop_btn", None)
        if stop is None:
            return
        stop.visible = bool(self.app.running)
        self._safe_update(stop)

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
        # MC-01：键入后直接切功能页（没有失焦事件）时补提交整期身份。
        self._ensure_issue_identity()
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
            self._sync_stop_action()
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
                    C.text("还没有选择期目录",
                           t, "h2", text_align=ft.TextAlign.CENTER),
                    C.text("选整期根目录，四个功能页共用。",
                           t, "caption", t.palette.text_faint),
                    C.primary_button("选择期目录", self._browse_issue, t,
                                     icon="FOLDER_OPEN"),
                ]))

    def _step_body(self, key: str) -> list:
        """功能页正文（不含期目录行）：空态卡，或结果条 + 1–2 个区块。"""
        if not self.field("issue_dir"):
            return [self._build_empty_detail()]
        controls: list = []
        if self._result is not None:
            controls.append(self._result.root)
        for step_key in self._view_steps(self._view_of_step(key)):
            controls.append(self._build_section(step_key))
        return controls

    def _build_step_page(self, key: str) -> ft.Control:
        """功能页：最上方是内嵌的期目录行，其下是该功能自己的 1–2 个区块。

        期目录行与正文分成两个兄弟节点：键入/换期只重建 `_step_box`，
        聚焦中的期目录输入框不进任何 patch。
        """
        t = self.t
        self._step_box = ft.Column(spacing=0, controls=self._step_body(key))
        return ft.Column(spacing=t.sp("2"),
                         controls=[self._build_issue_row(), self._step_box])

    def _section_body_controls(self, key: str) -> list:
        """区块正文列的内容（说明 + 参数/结果区）：整页构建与换期原地刷新共用。"""
        t = self.t
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
        return body

    def _build_section(self, key: str) -> ft.Control:
        """功能区块：区块头常显（标题 + 状态 + 本功能主按钮），内容手风琴展开。

        同页两区块只展开一个（`expanded_step`），默认展开第一个；区块间用 1px
        hairline 分隔，最后一个区块不再画底边。
        """
        t = self.t
        steps = self._view_steps(self._view_of_step(key))
        expanded = self.expanded_step(self._view_of_step(key)) == key
        head = self._detail_header(key)
        body_col = ft.Column(spacing=t.sp("2"),
                             controls=self._section_body_controls(key),
                             visible=expanded)
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

    def _detail_head_content(self, key: str, *, expanded: bool,
                             state: str) -> ft.Row:
        """区块头左半（标题 + 状态点/状态词 + chevron），不含右侧主按钮。

        MC-01：换期原地刷新只替换这段内容；`head` 容器与兄弟节点主按钮对象
        都保留，失焦后紧接着的点击不会落在被换掉的控件上。
        """
        t = self.t
        status = C.text(self._status_text(key), t, "caption", self._state_color(state))
        self._detail_statuses[key] = status
        chevron = C.text("▾" if expanded else "▸", t, "caption", t.palette.text_faint)
        self._chevrons[key] = chevron
        if key == self.selected:
            # 旧契约：`_detail_status` 指向当前选中区块的状态词。
            self._detail_status = status
        return ft.Row(spacing=t.sp("1"),
                      vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.text(self._step_title(key), t, "section"),
            C.status_dot(self._tone(state), t),
            status,
            ft.Container(expand=True),
            chevron,
        ])

    def _detail_header(self, key: str) -> ft.Control:
        """区块头（40 高）：15px 标题 + 状态点/状态词 + 右侧 Filled 主按钮 + chevron。

        点击区只包住左侧头部与 chevron；主按钮在点击区之外——点主按钮只运行
        该功能，不触发展开/收起。批 117 去掉了 01–06 序号列（不再按编号呈现）。
        """
        t = self.t
        expanded = self.expanded_step(self._view_of_step(key)) == key
        state = self._state_of(key)
        head = ft.Container(
            key=f"issue-head-{key}", expand=True, height=t.px(40),
            alignment=ft.Alignment.CENTER_LEFT,
            on_click=lambda e, k=key: self._toggle_section(k),
            content=self._detail_head_content(key, expanded=expanded, state=state))
        self._section_heads[key] = head
        btn = self._primary_button(key)
        if key == self.selected:
            # 旧契约（`_detail_status`/`_step_run_primary`）指向当前选中区块。
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
            "缺信息检查报告，先生成报告。", t, "warn",
            action=C.secondary_button("先生成检查报告",
                                      lambda e: self._run_infocheck_only(), t,
                                      icon="FACT_CHECK_OUTLINED", height=t.px(28)))

    def _run_infocheck_only(self) -> None:
        """页内 CTA：直接跑「检查 tex 信息」单步；成功后回填 latest_report 并启用。"""
        if self._state_of("infocheck") == "running":
            self.app.toast("检查运行中")
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
        if key == self._active_step:
            self._active_step = ""
        if key == "toc":
            # OBS-2（B-375）：无论当前看的是哪一步（含整期串行），toc 结束都立即刷新
            # 步骤结果区；rc 0/1 算本轮成功（1 仅表示需人工核对），>=2/<0 才算失败。
            failed = code not in (0, 1)
            # Lead round3 task-19：结束态归属单独记（与结果 JSON 归属分离）；
            # 记到与结果区同一口径解析的当前报告。
            self._toc_state_report = self._toc_report_path()
            # Lead round2 C1：记下这次运行属于哪份报告；只有它才配称「本次」。
            self._toc_run_report = self._toc_report_path()
            self._sync_toc_payload(code=code, failed=failed)
            # OBS-UI-2：单步「生成目录」跑完后收起日志 dock，让参数区回到视野；
            # 整期串行里 toc 不是最后一步时保持日志展开（后续步骤还要看进度）。
            if bool(getattr(self, "_toc_run_last", True)):
                self._collapse_log()
            return
        if key == "infocheck" and code == 0:
            # 批 117：页内「先生成检查报告」成功后，最新报告回填目录参数，
            # 目录主按钮随之启用（不强制用户先跑整期 01）。
            # Lead task-17(a)：回填是显式动作（apply=True）；随后按新报告重算结果区
            # 归属，避免把上一份报告的「本次生成完成」带进这份报告。
            self._latest_report(apply=True)
            self._sync_toc_payload()
            self._rebuild_detail()
            self._sync_nav()
            return
        if key == "merge":
            # P04 R05：合并结束按本次 rc 归属结果卡（0=完成，1=需人工核对，
            # 其它=失败）；结果卡刷新失败不影响步骤状态。
            self._merge_finish(int(code))
            return
        if key != "compileall" or code != 0:
            return
        self._set_expanded("merge")
        if self._view_of_step(self.selected) == "produce":
            self.selected = "merge"
            self._rebuild_detail()

    def on_step_cancelled(self, key: str) -> None:
        """B-375 Lead 返工 D：toc 被用户停止时结果区显式标「已停止，未生成结果摘要」。

        Lead task-22：取消不回滚，000 目录可能已部分更新（原生观察），文案不写
        「未产生新结果」。
        P04 R05-2：合并被停止时结果卡改为「本次已取消/未完成」，不复用上一轮成功。
        """
        if key == self._active_step:
            self._active_step = ""
        if key == "merge":
            self._merge_finish(-1, cancelled=True)
            return
        if key != "toc":
            return
        # Lead round3 task-19：取消同样把结束态归属记到当时解析出的当前报告，
        # 否则没有结果 JSON 时普通刷新会把「已停止」清掉。
        self._toc_state_report = self._toc_report_path()
        self._sync_toc_payload(cancelled=True)
        # 运行已结束，收起日志 dock 让参数区可用（日志仍可再展开查看）。
        self._collapse_log()

    # ── P04 R04/R05：合并运行归属与结果卡刷新 ──────────────────────────
    def on_step_started(self, key: str) -> None:
        """P04 R05-1：合并一开始就清掉上一轮结果缓存。

        装配层（assembly._run_sequence / pages._run_calls）在每步启动时调用。
        只对合并生效；清空后结果卡显示「本次合并正在运行…」，避免上一轮成功卡
        （旧路径 + 旧「打开文件」按钮）冒充本轮。
        """
        self._active_step = key
        if key != "merge":
            return
        self._merge_info = {}
        self._merge_warn_capture = False
        self._merge_run = {"state": "running", "code": None, "artifact": ""}
        self._refresh_merge_result()

    def _refresh_merge_result(self) -> None:
        """原地刷新合并结果卡（不在合并页时安全跳过；下次进页面会重建）。"""
        box = getattr(self, "_merge_box", None)
        if box is None:
            return
        try:
            box.content = self._merge_result()
        except Exception:  # noqa: BLE001 —— 结果卡刷新失败不拖垮正在跑的步骤
            return
        self._safe_update(box)

    def _merge_finish(self, code: int, cancelled: bool = False) -> None:
        """P04 R05：把合并结束归属到本次运行并刷新结果卡。"""
        if cancelled:
            state = "cancelled"
        elif code == 0:
            state = "done"
        elif code == 1:
            state = "warn"
        else:
            state = "error"
        status = self._status.get("merge")
        artifact = str(getattr(status, "artifact", "") or "")
        if not artifact:
            artifact = str((self._merge_info or {}).get("path") or "")
        if state == "done":
            try:
                if int((self._merge_info or {}).get("pages") or 0) <= 0:
                    state = "warn"
            except (TypeError, ValueError):
                state = "warn"
        self._merge_run = {"state": state,
                           "code": None if cancelled else int(code),
                           "artifact": artifact}
        self._refresh_merge_result()

    def sanitize_cancelled_artifact(self, key: str, artifact: str) -> str:
        """P04 R05-2：取消的合并不把旧/半成品 PDF 当作本次产物提供打开。"""
        if key == "merge":
            return ""
        return artifact or ""

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
            # T3 单入口：文案只反映真实阶段（扫描/清理/撤销），没有三档/预演态。
            return self._cleanup.phase_label()
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
        if key == "toc":
            if not self._toc_report_ready():
                return True
            # B-375 共享门：启用栏目四项必填 + 设置读取失败都要真正禁用（不只是视觉）。
            blocked, _hint = ASM.toc_column_gate(self)
            if blocked:
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
        if key == "toc":
            if not self._toc_report_ready():
                return ("还缺信息检查报告：点区块内「先生成检查报告」，"
                        "或在参数里手选一份报告")
            blocked, hint = ASM.toc_column_gate(self)
            if blocked:
                return hint
        if key == "dirpush":
            err = DIRPUSH.validation_error(self)
            if err:
                return err
        return ""

    def _primary_action(self, key: str) -> None:
        # MC-01：键入后没失焦就直接点运行（或按 Enter）时，先把完整路径提交为
        # 整期身份，运行结果才不会记到上一期。
        self._ensure_issue_identity()
        if self.app.running:
            if key == "compileall":
                self._confirm_stop("停止编译？已完成篇目的 PDF 会保留；当前篇可能留下半成品，下次编译会覆盖。")
            elif key == "infocheck":
                self._confirm_stop("停止检查？已扫描的部分不会写入报告。")
            else:
                self.app.toast("正在运行；点页头「停止」可中断" if key == "cleantemp"
                               else "运行中，完成后才能切换")
            return
        if not self.field("issue_dir"):
            self.app.toast("先选择期目录")
            return
        if key == "toc":
            # B-375：栏目无效/设置读错时绝不弹「重新生成目录？」确认框，也不派发。
            blocked, hint = ASM.toc_column_gate(self)
            if blocked:
                self.app.toast(hint)
                self.mark("toc_column_invalid", reason=hint, blocked=True)
                return
        if key == "dirpush":
            err = DIRPUSH.validation_error(self)
            if err:
                self.app.toast(err)
                return
        if key == "cleantemp":
            self._cleanup.start()
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
        C.show_dialog(self.page, dialog)

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
            self.app.toast("运行中，先停止")
            return
        chosen = {step["key"]: i < 4 for i, step in enumerate(self.STEPS)}
        skip = {"done": True}
        rows: list = []
        danger = C.notice_bar(
            "勾选「清理临时文件」= 自动校验并回收（移入回收区，可撤销）；"
            "默认不勾选，整期串行不会清理。", t, "warn")
        danger.visible = bool(chosen.get("cleantemp"))
        self._runall_danger = danger
        for index, step in enumerate(self.STEPS):
            if index == 4:
                rows.append(ft.Container(height=t.px(8)))
            if index == 4:
                rows.append(danger)
            rows.append(C.option_row(
                step["title"],
                "默认不跑 · 显式勾选才执行" if index >= 4 else "整期装配",
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
            self._clear_stale_cancel()
            self._expand_log()
            # R7-a：运行派发与当前提交一致（键入未失焦也先提交整期身份）。
            self._ensure_issue_identity()
            # C14：run_all 是同步入口（内部再 run_task 调度异步序列），
            # 不能交给 Flet 的 run_task（要求 coroutine function）。
            ASM.run_all(self, keys)

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("运行整期", t, "h2"),
            content=ft.Container(width=t.px(600), height=t.px(470),
                                 content=ft.Column(rows + [
                                     ft.Container(height=t.px(4)),
                                     C.text("某步失败即停，已完成步骤保留；清理与推送默认不跑。",
                                            t, "caption", t.palette.text_faint),
                                 ], spacing=t.sp("1"), scroll=C.scrollbar(t))),
            actions=[C.text_button("取消", lambda e: C.dismiss_top_dialog(self.page), t),
                     C.primary_button("开始运行", _start, t)],
        )
        C.show_dialog(self.page, dialog)

    def _expand_log(self) -> None:
        self._log_expanded = True
        if self._log is not None:
            self._log.set_expanded(True)
        self._sync_log_reserve()

    def _collapse_log(self) -> None:
        """OBS-UI-2（B-375）：收起日志 dock（不删除日志内容，仍可再展开）。

        真实缺陷不是参数控件丢了，而是运行开始时展开的日志浮层盖住页面下部：
        目录参数区最长，跑完停在滚动顶端就看只剩前几个控件。步骤结束收起浮层，
        结果区和参数区立刻回到视野；整期日志保留，点「整期日志」仍可展开看本轮输出。
        """
        if not getattr(self, "_log_expanded", False):
            return
        self._log_expanded = False
        log = getattr(self, "_log", None)
        if log is not None and hasattr(log, "set_expanded"):
            try:
                log.set_expanded(False)
            except Exception:  # noqa: BLE001 - 测试桩/未挂载
                pass
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
            parts.append(self._toc_result_box())
        elif key == "compileall":
            parts.append(self._compile_result())
        elif key == "merge":
            # P04 R05：结果卡原地刷新（on_step_started/_feed_merge/_merge_finish
            # 都通过 _merge_box 更新），不必等整页重建。
            self._merge_box = ft.Container(content=self._merge_result())
            parts.append(self._merge_box)
        elif key == "cleantemp":
            parts.append(self._cleanup.result())
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

    def _toc_effective_payload(self) -> dict:
        """只认当前报告名下的结果：换报告后不沿用上一份的目录结果（OBS-2 / Lead C）。"""
        payload = self._toc_payload
        # Lead task-17(b)：与 _sync_toc_payload 同一口径解析（字段空时回退 latest 展示）。
        report = self._toc_report_path()
        owner = str(getattr(self, "_toc_payload_report", "") or "")
        if payload and report and owner and owner != report:
            self._toc_payload = {}
            self._toc_payload_report = ""
            self._toc_state_report = ""
            self._toc_run_failed = False
            self._toc_run_cancelled = False
            self._toc_run_code = None
            self._toc_run_report = ""
            self._toc_payload_this_run = False
            return {}
        return payload

    def _toc_result_box(self) -> ft.Control:
        """结果区容器：_sync_toc_payload 原地重建它（与 _info_box 同思路）。"""
        box = getattr(self, "_toc_box", None)
        if box is None:
            box = ft.Column(spacing=self.t.sp("2"), controls=[])
            self._toc_box = box
        box.controls = [self._toc_result()]
        return box

    def _toc_failure_note(self) -> ft.Control:
        t = self.t
        code = getattr(self, "_toc_run_code", None)
        return C.notice_bar(
            f"本次生成未成功（退出码 {code}）：原因见整期日志；修好后点「重新生成目录」。",
            t, "error")

    def _toc_cancel_note(self) -> ft.Control:
        # Lead task-22（原生取消观察）：取消不回滚，000 目录可能已有本轮部分产物；
        # 结果区与参数区反馈统一改为「未生成结果摘要」的准确表述。
        return C.notice_bar(
            "本次已停止，未生成结果摘要；000 目录可能已部分更新，请重新生成后再使用。"
            "参数区保持可用，可重新运行。",
            self.t, "warn")

    def _toc_summary_lines(self, payload: dict) -> list[str]:
        placed = payload.get("placed") or {}
        outputs = payload.get("outputs") or {}
        column = payload.get("column") or {}
        shown = (placed.get("zhPdf") or placed.get("zhTex")
                 or outputs.get("zhTex") or outputs.get("enTex") or "—")
        # frozen #1 冒烟：无栏目时旧文案会印成「栏目：False（）」，改为读得懂的两态；
        # 启用栏目仍显示引擎给出的栏目名（不回落中文）。
        if column.get("enabled"):
            column_line = f"栏目：{column.get('title') or '—'}"
        else:
            column_line = "栏目：无"
        lines = [
            f"输出：{shown}",
            f"篇目：{(payload.get('articles') or {}).get('inToc', '—')} 篇",
            column_line,
            # frozen #1 冒烟：仅 tex 结论只出一行（旧版重复印了两次）。
            ("已编译 / 已写入" if payload.get("compiled")
             else "仅 tex · 未写入、未编译；合并需要 000 目录 PDF"),
        ]
        return lines

    def _toc_result(self) -> ft.Control:
        """生成结果区（OBS-2）：只认当前报告 + 本轮的退出状态。

        本轮失败/取消时不渲染旧产物的打开按钮，旧结果只作「上次结果（非本轮）」
        文字参考（Lead m00363 B/D）；成功时只提供本次结果 JSON 里记录的产物。
        """
        t = self.t
        failed = bool(getattr(self, "_toc_run_failed", False))
        cancelled = bool(getattr(self, "_toc_run_cancelled", False))
        payload = self._toc_effective_payload()
        if not payload and not failed and not cancelled:
            return self._empty_result("还没有生成目录",
                                      "点右上「生成目录」；默认取最新信息检查报告。")
        head: list = [C.section_label("生成结果", t), C.hairline(t)]
        if not failed and not cancelled:
            # Lead round2 C1：只有本会话内、当前报告、rc 0/1 真跑完的那次才叫「本次」；
            # 重启/初次加载/换报告后读到的本期最近结果只能标「上次」（产物/按钮保留）。
            if bool(getattr(self, "_toc_payload_this_run", False)):
                head.append(C.notice_bar("本次生成完成。", t, "success"))
            else:
                head.append(C.text(
                    "上次生成结果（非本轮，可能由其他检查报告触发），仅作参考。",
                    t, "caption", t.palette.text_muted))
        if failed or cancelled:
            head.append(self._toc_cancel_note() if cancelled else self._toc_failure_note())
            if not payload:
                return ft.Column(spacing=t.sp("1"), controls=head)
            head.append(C.text("上次结果（非本轮，仅供参考）：", t, "caption",
                               t.palette.text_muted))
            return ft.Column(spacing=t.sp("1"), controls=[
                *head,
                *[C.text(line, t, "caption", t.palette.text_faint)
                  for line in self._toc_summary_lines(payload)],
            ])
        placed = payload.get("placed") or {}
        outputs = payload.get("outputs") or {}
        lines = self._toc_summary_lines(payload)
        actions: list = []
        copy_path = ""
        zh_pdf = str(placed.get("zhPdf") or "")
        if zh_pdf:
            actions.append(C.text_button("打开 PDF", self._artifact_open_action(zh_pdf), t))
            copy_path = zh_pdf
        en_pdf = str(placed.get("enPdf") or "")
        if en_pdf:
            # 独立验收 task-6(2)：英文 PDF 与中文 PDF 同规格提供打开入口；仅 tex/
            # 失败/取消状态不会走到这里（placed 为空或前面已提前 return）。
            actions.append(C.text_button("打开英文 PDF",
                                         self._artifact_open_action(en_pdf), t))
            copy_path = copy_path or en_pdf
        shown: set[str] = set()
        for key, label in (("zhTex", "打开中文 tex"), ("enTex", "打开英文 tex")):
            path = str(placed.get(key) or outputs.get(key) or "")
            if not path or path in shown:
                continue
            shown.add(path)
            copy_path = copy_path or path
            actions.append(C.text_button(label, self._artifact_open_action(path), t))
        # Lead task-17(b)：渲染路径用非副作用解析（字段空时 latest 仅用于展示/打开），
        # 绝不能在一次渲染里把 toc_report 字段改回最新报告。
        if report := self._toc_report_path():
            from ui import toc as TOC  # 延后导入，避免 ui 模块循环依赖
            result_json = TOC.existing_result_json_path(report)
            if os.path.exists(result_json):
                actions.append(C.text_button("打开结果 JSON",
                                             self._artifact_open_action(result_json), t))
            else:  # B-121 B-357：可能已被「清理临时文件」移入回收目录
                lines.append("结果 JSON 不在期目录（可能随临时文件清入回收站）；需要时重新生成。")
            if copy_path:
                actions.append(C.text_button("复制结果路径",
                                             lambda e: self.app.copy_text(copy_path), t))
        return ft.Column(spacing=t.sp("1"), controls=[
            *head,
            *[C.text(line, t, "caption", t.palette.text_muted) for line in lines],
            ft.Row(spacing=t.sp("1"), wrap=True, controls=actions) if actions else ft.Container(),
        ])

    def _dirpush_result(self) -> ft.Control:
        t = self.t
        status = self._status.get("dirpush")
        artifact = str(getattr(status, "artifact", "") or "")
        state = self._state_of("dirpush")
        payload = getattr(self, "_dirpush_payload", None)
        if not artifact and state != "error" and not payload:
            return self._empty_result("还没有推送记录",
                                      "在上方选好输出根与范围，点右上「生成摘要」。")
        rows = [C.section_label("推送结果", t), C.hairline(t)]
        if artifact:
            rows.append(C.text(os.path.basename(artifact), t, "small", t.palette.text))
        if payload:
            # BUG-07：结果卡只渲染本次引擎 JSON；ok=false 明确说没生成，绝不显示成功卡。
            if not payload.get("ok", False):
                rows.append(C.inline_note("推送失败：引擎报告本次没有生成。", t, "error"))
            rows += [C.text(line, t, "caption", t.palette.text_muted)
                     for line in DIRPUSH.summary_lines(payload)]
        if state == "cancelled":
            rows.append(C.inline_note("已取消当前步骤：本次没有生成推送产物。", t, "warn"))
        if state == "error":
            # 失败通常没有产物；先渲染原因，不能因为 artifact 为空退回空态（B P2-1）。
            tail = self._tail_lines("dirpush", 3)
            rows.append(C.inline_note("推送失败：原因在下面末 3 行（日志可展开看全文）。",
                                      t, "error"))
            rows += [C.text(line, t, "mono", t.palette.error, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS) for line in tail]
        if artifact:
            rows.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                C.text_button("打开 docx", self._artifact_open_action(artifact), t),
                C.text_button("打开所在目录",
                              self._artifact_open_action(os.path.dirname(artifact)), t),
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
                                         self._artifact_open_action(report), t))
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

    # ── P04：合并结果卡（R04 完整反馈 / R05 取消与重跑 / R06 旧产物） ──
    def _merge_shell(self, rows: list, *, path: str = "",
                     actions: list | None = None) -> ft.Control:
        """合并结果卡骨架：标题 + 分隔线 + 文案行 + 产物路径 + 打开动作。"""
        t = self.t
        controls: list = [C.section_label("合并结果", t), C.hairline(t)]
        controls.extend(rows)
        if path:
            controls.append(C.text(path, t, "mono", t.palette.text_faint,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
        if actions:
            controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=actions))
        return ft.Column(spacing=t.sp("1"), controls=controls)

    def _merge_line(self, text: str, tone: str = "") -> ft.Control:
        t = self.t
        color = {"warn": t.palette.warn, "error": t.palette.error}.get(
            tone, t.palette.text_muted)
        return C.text(text, t, "caption", color)

    def _merge_rows(self, rows: list) -> list:
        return [self._merge_line(text, tone) for text, tone in rows]

    @staticmethod
    def _merge_product_ok(path: str) -> bool:
        """R06：产物是否仍在原位且可识别（存在 + 可读 + PDF 头）。

        只做读取判断，不移动/改名/扫描历史合并文件；读不了就当「无法识别」。
        """
        if not path:
            return False
        try:
            target = Path(path)
            if not target.is_file():
                return False
            with open(target, "rb") as fh:
                return fh.read(5) == b"%PDF-"
        except OSError:
            return False

    def _merge_actions(self, path: str) -> list:
        return [
            C.text_button("打开文件", self._artifact_open_action(path), self.t),
            C.text_button("打开所在目录",
                          self._artifact_open_action(os.path.dirname(path)),
                          self.t),
            C.text_button("复制路径", lambda e: self.app.copy_text(path), self.t),
        ]

    def _merge_result(self) -> ft.Control:
        t = self.t
        info = dict(self._merge_info or {})
        run = dict(getattr(self, "_merge_run", None) or {})
        state = str(run.get("state") or "")
        # R05-1/R05-2：本次运行进行中/被取消/失败时，结果卡只描述本次，绝不回落到
        # 上一轮成功结果（旧 _merge_info 已在 on_step_started 清空）。
        if state == "running":
            return self._merge_shell(self._merge_rows([
                ("本次合并正在运行…", ""),
                ("跑完前不显示上一轮结果；要中止点右上「停止」。", ""),
            ]))
        if state == "cancelled":
            return self._merge_cancelled_card(info)
        if state == "error":
            return self._merge_failed_card(info)
        if not info:
            return self._merge_previous_card(run)
        try:
            pages = int(info.get("pages") or 0)
        except (TypeError, ValueError):
            pages = 0
        path = str(info.get("path") or "")
        if pages <= 0:
            return self._merge_shell([
                C.inline_note("没有可合并的 PDF：先完成编译，或用「先检查」"
                              "确认期目录里的 PDF 是否可读。", t, "warn"),
            ], path=path)
        # R04：只有 rc=0 才能说「合并完成」；rc=1 显示「需人工核对」。
        rows: list = []
        if run.get("code") == 1:
            rows.append((f"需人工核对 · {pages} 页", "warn"))
        else:
            rows.append((f"合并完成 · {pages} 页", ""))
        try:
            missing = int(info.get("missing") or 0)
        except (TypeError, ValueError):
            missing = 0
        if missing:
            rows.append((f"缺 {missing} 篇：{info.get('names', '')}", "warn"))
        # P04-O2：引擎的 ⚠️ 会在文本开头留下 U+FE0F，渲染前统一清掉。
        warnings = TF.clean_warnings(info.get("warnings") or [])
        if warnings:
            rows.append((f"warning {len(warnings)} 条", "warn"))
            rows.extend((f"· {w}", "warn") for w in warnings[:5])
            if len(warnings) > 5:
                rows.append((f"……另有 {len(warnings) - 5} 条，见日志", "warn"))
        # R06：只有本次运行（有归属）才校验产物；旧格式（无 run）保持原样兼容。
        ok = self._merge_product_ok(path) if state else bool(path)
        if state and path and not ok:
            rows.append((f"产物不在原位/无法识别：{path}", "warn"))
        actions = self._merge_actions(path) if (path and ok) else []
        return self._merge_shell(self._merge_rows(rows), path=path, actions=actions)

    def _merge_cancelled_card(self, info: dict) -> ft.Control:
        t = self.t
        rows: list = [("本次已取消/未完成：没有生成本次合并产物。", "warn")]
        target = str(self.field("merge_out") or "")
        # R05-2：原目标（上一次的产物）仍在时，只能明确标注「原有目标未改动」，
        # 不把它当成本次产物、不自动打开。
        if target and self._merge_product_ok(target):
            rows.append((f"原有目标未改动（上次结果在 {target}）", ""))
        elif target:
            rows.append((f"输出路径未改变：{target}（本次没有写出文件）", ""))
        else:
            rows.append(("输出路径未改变（本次没有写出文件）。", ""))
        return self._merge_shell(self._merge_rows(rows))

    def _merge_failed_card(self, info: dict) -> ft.Control:
        rows: list = [("本次合并失败：没有生成本次产物。", "error")]
        reason = str(info.get("error") or "").strip()
        if reason:
            rows.append((reason, "error"))
        tail = [ln for ln in (self._step_tail.get("merge") or [])
                if ln and not ln.startswith(("▶", "■"))]
        for line in tail[-2:]:
            rows.append((line, ""))
        return self._merge_shell(self._merge_rows(rows))

    def _merge_previous_card(self, run: dict) -> ft.Control:
        """没有本次运行记录时的结果卡（R06：上次产物需明确标注，不可静默冒充）。"""
        status = self._status.get("merge")
        artifact = str(getattr(status, "artifact", "") or "")
        state = str(getattr(status, "state", "") or "")
        if artifact and state in ("done", "warn"):
            if self._merge_product_ok(artifact):
                return self._merge_shell(self._merge_rows([
                    ("上次合并结果（本次没有重新合并）", ""),
                    ("上次产物仍在原位；重新合并后以本次结果为准。", ""),
                ]), path=artifact, actions=self._merge_actions(artifact))
            return self._merge_shell(self._merge_rows([
                ("上次产物不在原位/无法识别：", "warn"),
                (artifact, "warn"),
                ("本次没有重新合并；旧路径没有改写。", ""),
            ]))
        return self._empty_result("还没有运行记录。",
                                  "第 3 步还没跑 —— 先编译，合并才不会缺篇。")

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
                C.text("点「检测前置页」列出扉页、编委页与封底；暂不支持编辑。", t, "caption", t.palette.text_faint),
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
                "打开", self._artifact_open_action(path), t))
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
        """兼容旧调用：结果区已由 CleanupPanel 自更新（绑 root+operationId）。"""
        return self._cleanup.result()

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
        issue = issue or getattr(self, "_last_issue", "") or self.field("issue_dir")
        # 换期时由 `_on_issue_changed` 显式传旧路径；空值不落库。
        if not issue:
            return
        data = dict(self.app.store.get("issue_state", {}) or {})
        # T3：cleanup（结果/撤销）由 CleanupPanel 独立维护，这里只更新状态字段，
        # 不能整条覆盖，否则刷新/重启后丢掉本项目的结果与可撤销批次。
        entry = dict(data.get(issue) or {})
        entry.update({
            "schema": 1,
            "last_step": self.selected,
            "statuses": {
                key: (value.as_dict() if hasattr(value, "as_dict")
                      else {"state": str(value)})
                for key, value in self._status.items()
            },
        })
        banner = getattr(self, "_banner", None)
        if isinstance(banner, dict) and banner.get("text"):
            entry["result"] = {"text": banner["text"], "level": banner["level"],
                               "artifact": banner["artifact"]}
        else:
            entry.pop("result", None)
        data[issue] = entry
        self.app.store.set("issue_state", data)
        save = getattr(self.app, "save_settings", None)
        if callable(save):
            try:
                save(toast=False)
            except TypeError:
                save()

    def _remember_result(self, text: str = "", level: str = "info",
                         artifact: str = "") -> None:
        """记下本页结果条快照，随 issue_state 一期一条落库（空 text 表示清空）。

        结果条属于某一期：换期时若不清空，页面会继续挂着上一期的产物入口，
        点开就是别的期的报告（N5 产物隔离）。
        """
        if not text:
            self._banner = None
            return
        self._banner = {"text": str(text), "level": str(level or "info"),
                        "artifact": str(artifact or "")}

    def _restore_result_banner(self, entry: dict) -> None:
        """按该期落库的结果条快照恢复；没有快照就保持清空。"""
        result = getattr(self, "_result", None)
        snapshot = (entry or {}).get("result") if isinstance(entry, dict) else None
        if result is None or not isinstance(snapshot, dict):
            return
        text = str(snapshot.get("text") or "")
        if not text:
            return
        level = str(snapshot.get("level") or "info")
        artifact = str(snapshot.get("artifact") or "")
        result.show(text, level=level, artifact=artifact)
        self._banner = {"text": text, "level": level, "artifact": artifact}

    def _restore_issue_state(self, *, keep_view: bool = False,
                             rebuild: bool = True) -> None:
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
        self._restore_result_banner(entry)
        if not rebuild:
            # MC-01：换期提交时由调用方做「原地刷新」，保留运行按钮对象。
            return
        if getattr(self, "_step_box", None) is not None:
            self._step_box.controls = self._step_body(self.selected)
        elif self._content_box is not None:
            self._content_box.controls = [self._build_step_page(self.selected)]

    def _clear_section_refs(self) -> None:
        """丢掉当前正文的区块/按钮引用；整块重建或进入空态前必须调用。"""
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

    def _reset_issue_runtime(self, *, clear_sections: bool = False) -> None:
        """换期清空本页运行态与结果缓存（P0）。

        clear_sections 只用于「提交为空期」：正文马上换成空态卡，旧区块引用
        会脱离页面树；若不清掉，下一次从空期建区时 _refresh_issue_detail_in_place
        会误判旧引用仍可原地刷新，正文停在空态（F1）。
        """
        # 结果条也是本页运行态：换期先清空，新期有快照再由 _restore_issue_state 恢复。
        self._banner = None
        result = getattr(self, "_result", None)
        if result is not None:
            result.reset()
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
        # P04 R05-3：换期/换报告清掉本次合并归属，避免上一期的结果卡串到新一期。
        self._merge_run = {}
        self._merge_warn_capture = False
        self._merge_box = None
        self._active_step = ""
        self._toc_payload = {}
        self._toc_payload_report = ""
        self._toc_state_report = ""
        self._toc_run_failed = False
        self._toc_run_cancelled = False
        self._toc_run_code = None
        self._toc_run_report = ""
        self._toc_payload_this_run = False
        self._toc_box = None
        self._ct_preview = {}
        panel = getattr(self, "_cleanup", None)
        if panel is not None:
            panel.reset()
        self._prep_pages = None
        self._prep_box = None
        self._step_tail = {}
        self._status = {step["key"]: "idle" for step in self.STEPS}
        # BUG-07：换期清掉上一期的推送结果，避免旧 JSON 串到新一期。
        self._dirpush_payload = None
        # 目录面板缓存里有上一期的栏目表/预览；重建，避免带进新一期。
        self._toc_panel = None
        if clear_sections:
            self._clear_section_refs()
        self._info = self._new_info_card()

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

    # ── MC-01：整期身份提交（逐字输入不提交，失焦/运行/切页时提交一次）──
    def _commit_issue_identity(self, issue: str) -> bool:
        """把输入框最终值提交为整期身份：旧期写回 → 重置 → 新期恢复 → 新期落库。

        返回 True 表示身份确实变了。运行中不改归属（否则长任务的完成态会记到
        另一个期上）；值没变时保持幂等。
        """
        issue = (issue or "").strip()
        previous = getattr(self, "_last_issue", "")
        if issue == previous or getattr(self.app, "running", False):
            return False
        if previous:
            self._persist_issue_state(previous)
        self._last_issue = issue
        # C-1：旧正文控件还在 350ms 刷新窗口里挂着，先让它们手里的代次失效。
        self._issue_generation = self.current_artifact_binding() + 1
        # C-2：报告/设置引用属于上一期时立即丢掉，绝不带进新一期。
        self._reset_issue_bound_fields(issue)
        # 进入空期时正文会换成空态卡；旧区块引用必须同时丢掉，否则从空期
        # 再建区时原地刷新会更新脱离页面树的控件（F1）。
        self._reset_issue_runtime(clear_sections=not issue)
        if issue:
            # 新期有上次记录就恢复状态，但不改变当前功能页（换期不跳走）；
            # 正文由调用方原地刷新，避免换掉刚要点的运行按钮。
            self._restore_issue_state(keep_view=True, rebuild=False)
        self._set_expanded(self.selected)
        self._persist_issue_state()
        return True

    def current_artifact_binding(self) -> int:
        """正文产物 Open 的归属代次：整期身份每提交一次就 +1。"""
        return int(getattr(self, "_issue_generation", 0))

    def open_artifact(self, path: str, *, binding: int | None = None) -> None:
        """正文产物入口的统一打开点：代次过期的控件一律拒绝并可见提示。

        MC-01 的失焦提交与正文刷新之间有 COMMIT_REFRESH_DELAY 的窗口，旧正文
        仍挂在页面上；它的 Open 闭包指向上一期产物，不能悄悄打开（C-1）。
        路径本身原样透传，缺失文件仍按原路径诚实交给系统打开。
        """
        if binding is not None and binding != self.current_artifact_binding():
            self.mark("artifact_open_stale", path=str(path or ""),
                      binding=binding, current=self.current_artifact_binding())
            self.app.toast("期目录已切换，正文正在刷新：请确认当前期的产物再打开")
            return
        self.app.open_path(path)

    def _artifact_open_action(self, path: str):
        """构建正文 Open 回调：把构建时的期代次带进点击，换期后即失效。"""
        binding = self.current_artifact_binding()
        return lambda e: self.open_artifact(path, binding=binding)

    def _new_info_card(self):
        """整期信息检查卡：打开报告的路径在点击时取，但代次取构建时（C-1 同机制）。"""
        card = CK.issue_card(self)
        binding = self.current_artifact_binding()
        card.on_open = lambda path: self.open_artifact(path, binding=binding)
        return card

    @staticmethod
    def _path_within(path: str, root: str) -> bool:
        """路径是否落在 root 之下（normpath 与 realpath 双判定，兼容 macOS /tmp）。"""
        if not path or not root:
            return False
        for convert in (os.path.normpath, os.path.realpath):
            try:
                base = convert(os.path.expanduser(str(root)))
                target = convert(os.path.expanduser(str(path)))
                if os.path.commonpath([target, base]) == base:
                    return True
            except ValueError:  # 相对/绝对混用或不同盘符
                continue
        return False

    def _reset_issue_bound_fields(self, issue: str) -> None:
        """换期丢掉不属于新期的报告/设置引用（C-2），绝不沿用上一期。

        只清「不在新期目录下」的值：同一期内用户显式选的（含跨目录外部报告）
        保持不动；提交为空期则一律清空。设置键同时清 recent，避免面板重建后
        从 recent 兜底又读回上一期。
        """
        if not getattr(self, "fields", None):
            return
        for key in ("toc_report", "toc_settings"):
            current = self.field(key)
            stored = str(self.app.store.get(f"recent.{key}", "") or "").strip()
            value = current or stored
            if not value or (issue and self._path_within(value, issue)):
                continue
            if key in self.fields:
                self.set_field(key, "")
            self.app.store.set_recent(key, "")
            self.mark("issue_field_dropped", field=key, value=value, issue=issue)

    def _issue_change_locked(self) -> bool:
        """运行中身份不可变：字段/recent/日志/结果归属都锁在本轮身份。"""
        return bool(getattr(self.app, "running", False))

    def _reject_issue_change_when_running(self) -> bool:
        """运行中拒绝换期入口；返回 True 表示调用方应停止后续动作。"""
        if not self._issue_change_locked():
            return False
        self._reject_issue_change(getattr(self, "_issue_field", None))
        return True

    def _reject_issue_change(self, field=None) -> None:
        """拒绝运行中的身份变更：把字段与持久 recent 拉回本轮身份。

        R7-a：只拒绝提交而放任输入框/recent 停在新值，会让界面、持久化、
        日志标题与结果归属互相矛盾。每次运行只在第一次拒绝时提示，逐字
        输入不会刷屏。
        """
        issue = str(getattr(self, "_last_issue", "") or "")
        field = field if field is not None else getattr(self, "_issue_field", None)
        if field is not None and str(getattr(field, "value", "") or "").strip() != issue:
            field.value = issue
            self._safe_update(field)
        if str(self.app.store.get("recent.issue_dir", "") or "").strip() != issue:
            self.app.store.set_recent("issue_dir", issue)
        self.mark("issue_change_rejected", issue=issue, reason="running")
        if not getattr(self, "_issue_lock_warned", False):
            self._issue_lock_warned = True
            self.app.toast("运行中不能更换期目录：先停止当前任务，结束后再切换")

    def _ensure_issue_identity(self) -> None:
        """运行/切页前补提交：键入后直接按 Enter 或点运行，结果仍记到完整路径。"""
        if getattr(self.app, "running", False):
            return
        if not getattr(self, "fields", None):
            # `object.__new__(IssuePage)` 的旧测试桩（不跑 __init__/build）。
            return
        issue = (self.field("issue_dir") or "").strip()
        if issue == getattr(self, "_last_issue", ""):
            return
        if not self._commit_issue_identity(issue):
            return
        self._refresh_after_issue_commit()
        self._clear_issue_log()
        self._update_log_title()
        self.mark("issue_changed", issue=issue)

    def _clear_issue_log(self) -> None:
        """换期清空整期日志 dock 与分段缓冲（原 `_on_issue_changed` 的语义）。"""
        log = getattr(self, "_log", None)
        if log is not None:
            log.clear()
        self._log_step_key = ""
        self._log_buffer = []
        self._log_lines = 0

    def _refresh_after_issue_commit(self) -> None:
        """换期后的正文刷新：优先原地换「区块头内容 + 正文列」，保留运行按钮。

        blur 提交紧接着可能就是「运行」点击；整块重建会把按钮换成新控件，
        抬起时的点击落到已卸载的控件上就丢了（K7 的点击时序）。只有空态/
        首次从空目录建区（结构里没有需要保留的按钮）才退回整块重建。
        """
        box = getattr(self, "_step_box", None)
        if box is None:
            return
        if self._refresh_issue_detail_in_place():
            return
        box.controls = self._step_body(self.selected)
        self.refresh_running()
        self._sync_header()
        self._sync_stop_action()
        self._sync_nav()
        self._safe_update(self._nav_box, box)

    COMMIT_REFRESH_DELAY = 0.35

    def _defer_after_commit_refresh(self, issue: str) -> None:
        """MC-01 N6：失焦提交后的正文刷新延到点击按压结束之后。

        blur 与点「运行」属于同一次按压；同步刷新会把按钮替换掉，抬起时
        的 tap 被 Flutter 取消，运行永远不派发。这里只延后正文刷新，地址
        身份与日志标题已经同步提交。
        """
        if not issue or not getattr(self, "_section_heads", None):
            # 空态 → 有期：还没有可点的运行按钮，直接建区。
            self._refresh_after_issue_commit()
            return
        try:
            self.page.run_task(self._refresh_after_commit_later, issue,
                               self.COMMIT_REFRESH_DELAY)
        except Exception:  # noqa: BLE001 - 页面未挂载或测试桩
            self._refresh_after_issue_commit()

    async def _refresh_after_commit_later(self, issue: str, delay: float) -> None:
        import asyncio
        await asyncio.sleep(delay)
        if getattr(self.app, "running", False):
            # 点击已派发运行；完成态会自己刷新，别在跑的时候换正文。
            return
        if getattr(self, "_last_issue", "") != issue:
            # 期间又换了期/清空了，交给新的那次提交。
            return
        self._refresh_after_issue_commit()

    def _refresh_issue_detail_in_place(self) -> bool:
        """原地刷新换期后的区块头/正文，保留 `_section_heads` 与主按钮对象。

        返回 False 表示结构不完整（空态、首次建区、测试桩），调用方整块重建。
        """
        box = getattr(self, "_step_box", None)
        if box is None or not self.field("issue_dir"):
            return False
        view = self._view_of_step(self.selected)
        steps = self._view_steps(view)
        if not steps or any(key not in self._section_heads
                            or key not in self._section_bodies for key in steps):
            return False
        for key in steps:
            expanded = self.expanded_step(view) == key
            head = self._section_heads[key]
            body = self._section_bodies[key]
            head.content = self._detail_head_content(
                key, expanded=expanded, state=self._state_of(key))
            body.controls = self._section_body_controls(key)
            body.visible = expanded
            self._safe_update(head, body)
        self.refresh_running()
        self._sync_header()
        self._sync_stop_action()
        self._sync_nav()
        self._safe_update(box)
        return True

    def _on_issue_changed(self, event=None) -> None:
        """换期（P0/MC-01）：旧目录状态写回 issue_state，新目录清空运行态/结果/日志。

        此前只清 4 个字段，`_status/_merge_info/_compile_rows` 等仍是上一期的
        结果（B 包实测：C 目录显示 B 的「编译成功 · 21 秒」与 47 页合并卡）。
        现在换期整页重置，但**保持当前功能页**；空目录显示统一空态。

        MC-01：本入口只服务显式换期（选择器回填 / ✕ 清空 / 测试注入），
        手动键入的提交点在失焦（`_commit`），两者共用 `_commit_issue_identity`。
        """
        issue = self.field("issue_dir")
        if not self._commit_issue_identity(issue):
            # 值没变保持幂等；运行中被拒绝时回滚字段/recent，
            # 日志标题与结果归属仍留在本轮身份（R7-a，不靠改标签掩盖）。
            if issue != getattr(self, "_last_issue", ""):
                self._reject_issue_change(getattr(self, "_issue_field", None))
            return
        self._set_expanded(self.selected)
        if getattr(self, "_step_box", None) is not None:
            self._clear_section_refs()
            self._step_box.controls = self._step_body(self.selected)
            self.refresh_running()
            self._sync_header()
            self._sync_stop_action()
            self._sync_nav()
            self._safe_update(self._nav_box, self._step_box)
            self._persist_issue_state()
        self._clear_issue_log()
        self._update_log_title(issue)
        self.mark("issue_changed", issue=issue)

    # ── 运行入口 ───────────────────────────────────────────────────────
    def _clear_stale_cancel(self) -> None:
        """P04 R05-4：用户重新点运行 = 新的一次运行，清掉上一次取消留下的陈旧标记。

        只清「当前没有运行任务」时的标记；运行中的真停止请求由 app.stop_run 打在
        running=True 期间，不会被这里清掉。装配层仍保留「启动前
        cancel_requested 已置位 = 本次中止」的语义（见
        test_b116_issue_run::test_cancel_before_start_launches_nothing）。
        """
        if getattr(self.app, "running", False):
            return
        try:
            self.app.cancel_requested = False
        except AttributeError:  # 页面桩没有该属性时忽略
            pass

    def _run_step(self, key: str) -> None:
        # 统一入口：唯一主按钮与旧测试都走 ASM.run_step(self, key)；
        # 这里只负责日志展开与守卫，参数/确认逻辑见 _primary_action。
        # R02：运行中再点步骤（含 F9 热键）必须可见拒绝，ASM.run_step 的静默 return 不能当门。
        if _reject_when_running(self, f"issue_step:{key}", "运行步骤"):
            return
        # MC-01：运行归属完整路径（逐字输入期间身份尚未提交）。
        self._ensure_issue_identity()
        if not self._issue_dir():
            self.app.toast("先选择期目录")
            return
        self._clear_stale_cancel()
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
                      duration: float = 0.0, artifact: str = "",
                      payload: dict | None = None) -> None:
        if key == "dirpush":
            # BUG-07：rc≠0 或 JSON ok=false（含同名覆盖失败）都算失败；
            # 产物只认本次引擎 JSON 里真实存在的 outputs[]，不靠日志行猜路径。
            state = DIRPUSH.run_state(code, cancelled, payload)
            artifact = DIRPUSH.result_artifact(payload, cancelled=cancelled)
            self._dirpush_payload = payload
        elif cancelled:
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
        if key == "merge":
            # P04 R05：直接调用本方法（测试/旧路径）时也把结束态记到合并归属。
            self._merge_run = {"state": state,
                               "code": None if cancelled else int(code),
                               "artifact": str(artifact or "")}
            self._refresh_merge_result()
        refresh = getattr(self, "_refresh_steps", None)
        if callable(refresh):
            refresh()

    async def _run_calls(self, calls: list, sequential: bool = True) -> None:
        """逐条执行（测试/旧路径）；与 ASM._run_sequence 同样写状态、不清日志。"""
        if _reject_when_running(self, "issue_run_calls", "运行步骤"):
            return
        for key, call in calls:
            started = time.monotonic()
            self._status[key] = ASM.StepStatus("running", started_at=ASM._now())
            self._refresh_steps()
            self.on_step_started(key)
            CK.before_issue_step(self, key)
            # P08 I-5：begin_run 返回 False = 已有任务在跑，绝不并发（也不写状态）。
            if self.app.begin_run(call) is False:
                self._status[key] = ASM.StepStatus("cancelled", started_at=started)
                self._refresh_steps()
                break
            lines: list[str] = []

            def _on_line(line: str) -> None:
                lines.append(str(line))
                self._on_line(line)

            try:
                code = await EB.run_call(call, on_line=_on_line,
                                         on_proc=getattr(self.app, "track_proc", None),
                                         notes=self.app.notes_repo)
            except Exception as exc:  # noqa: BLE001
                code = -1
                text = f"失败 · 无法启动任务：{exc}"
                self._result.show(text, level="error")
                self._remember_result(text, "error")
            self.app.finish_run(call, code)
            cancelled = bool(getattr(self.app, "cancel_requested", False))
            duration = (time.monotonic() - started) * 1000
            artifact = EB.artifact_path(str(getattr(self.app, "last_line", "") or ""))
            payload = DIRPUSH.result_payload(lines) if key == "dirpush" else None
            self._apply_status(key, code, cancelled, duration, artifact=artifact,
                               payload=payload)
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
        step_key = self._log_step_key or self._active_step or self.selected
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
        # P04 R05-3：merge 结果按「这条日志属于哪一步」归属，而不是按用户此刻看的
        # 页面。运行中切到别的步骤时契约行/missing/warning 仍进本次合并结果；
        # 合并专属标记兜底（step_key 覆盖不到时也不会漏掉契约行）。
        if step_key == "merge" or _MERGE_MARKER_RE.search(text):
            self._feed_merge(text)
        if self.selected == "infocheck" and "正在扫描" in text:
            self._detail_status.value = "运行中"

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
            self._compile_summary = (f"{data['ok']} 成功 / {data['fail']} 失败"
                                     f" · 共 {data['targets']} 篇")
        if self._detail_primary is not None and self.selected == "compileall":
            self._detail_status.value = f"运行中 · {len(self._compile_rows)} 篇已开始"

    def _feed_merge(self, text: str) -> None:
        """P04 R04/R05：解析 mergepdf 契约行/缺件/warning/失败（与所选步骤解耦）。"""
        stripped = (text or "").strip()
        fail = _MERGE_FAIL_RE.search(text)
        if fail:
            info = dict(self._merge_info or {})
            info.pop("path", None)
            info.pop("pages", None)
            info["error"] = fail.group(1).strip()
            self._merge_info = info
            self._merge_warn_capture = False
            self._refresh_merge_result()
            return
        done = _MERGE_DONE_RE.search(text)
        if done:
            info = dict(self._merge_info or {})
            info.update({"path": done.group(1).strip(),
                         "pages": int(done.group(2))})
            self._merge_info = info
            self._merge_warn_capture = False
            self._refresh_merge_result()
            return
        missing = _MERGE_MISSING_RE.search(text)
        if missing:
            info = dict(self._merge_info or {})
            info.update({"missing": int(missing.group(1)),
                         "names": missing.group(2).strip()})
            self._merge_info = info
            self._merge_warn_capture = False
            self._refresh_merge_result()
            return
        if _MERGE_WARN_HEAD_RE.search(stripped):
            # 引擎先打「⚠ 注意事项：」，随后每条 `· 明细`；下一条明细才入账。
            self._merge_warn_capture = True
            return
        item = _MERGE_WARN_ITEM_RE.match(stripped)
        if item and self._merge_warn_capture:
            self._append_merge_warning(item.group(1))
            return
        generic = _MERGE_ANY_WARN_RE.match(stripped)
        if generic:
            self._append_merge_warning(generic.group(1))
            return
        self._merge_warn_capture = False

    def _append_merge_warning(self, text: str) -> None:
        # P04-O2：`⚠️` 的变体选择符会跟着捕获组进 warning，先清掉再入账。
        note = TF.strip_format_chars(text)
        if not note:
            return
        info = dict(self._merge_info or {})
        warnings = list(info.get("warnings") or [])
        if note not in warnings:
            warnings.append(note)
        info["warnings"] = warnings[-20:]
        self._merge_info = info
        self._refresh_merge_result()

    def _refresh_steps(self) -> None:
        if self._log is not None:
            self._sync_log_dock()
        self._sync_header()
        self._sync_stop_action()
        self._sync_info_from_card()
        if self.selected == "toc":
            self._sync_toc_payload()
        self._sync_nav()
        if not self.app.running:
            self._persist_issue_state()
        self._safe_update(self._nav_box, self._stop_btn)

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

    def _sync_toc_payload(self, *, code=None, failed: bool = False,
                          cancelled: bool = False) -> None:
        """OBS-2：重建目录结果区；toc 步结束（含整期串行）时带上本轮退出码。

        - 换报告/清空报告 → 清旧结果与本轮 run 标记，绝不跨报告显示上一份的产物/结论；
        - 读到 payload → 记下它属于哪个报告；
        - code 给出时记录本轮成功/失败；cancelled 记录「已停止」；运行中清掉旧标记；
        - Lead round3 task-19：结束态（code/failed/cancelled）单独归属到
          `_toc_state_report`。`_toc_payload_report` 只在读到结果 JSON 后才设置，
          报告有效但没有旧结果 JSON 时恒为空——不能拿它判断「是否换报告」，否则
          同一报告的普通刷新会把「本次已停止/本次生成未成功」清掉。
        """
        from ui import toc as TOC  # 延后导入，避免 ui 模块循环依赖
        # Lead task-17：与渲染同一口径（字段优先；字段空时 latest 仅供展示/打开）。
        report = self._toc_report_path()
        if not report:
            self._toc_payload = {}
            self._toc_payload_report = ""
        else:
            owner = str(getattr(self, "_toc_payload_report", "") or "")
            if report != owner:
                self._toc_payload = {}
            payload = TOC.read_result(report)
            if payload:
                self._toc_payload = payload
                self._toc_payload_report = report
        # Lead round3 task-19：普通刷新（无 code/cancel 事件）只按「结束态归属」
        # 清理本节结束态；同一报告——尤其报告有效但该期没有旧结果 JSON 时——必须
        # 保留 cancelled/failed/code（Codex 终审两例）。换报告/报告为空/新一轮运行
        # 开始才失效（换期末页实例重建由 _reset_issue_runtime 负责）。
        running = self._state_of("toc") == "running"
        if code is None and not cancelled:
            state_report = str(getattr(self, "_toc_state_report", "") or "")
            stale_state = (not report) or (state_report != report)
            if stale_state or running:
                # Lead C：换报告后不能带着上一轮的失败/完成标记渲染新报告的结果区。
                self._toc_run_failed = False
                self._toc_run_cancelled = False
                self._toc_run_code = None
                if stale_state:
                    self._toc_state_report = ""
                # Lead round2 C1：换报告/新一轮运行后连「本轮属于谁」也清掉——
                # A→B→A 时 A 的旧运行不能重新冒充「本次」。
                self._toc_run_report = ""
        if cancelled:
            self._toc_run_cancelled = True
            self._toc_run_failed = False
            self._toc_run_code = None
        elif code is not None:
            self._toc_run_code = code
            self._toc_run_failed = bool(failed)
            self._toc_run_cancelled = False
        if cancelled or code is not None:
            # 结束态归属到当前报告。调用方（on_step_finished/on_step_cancelled）
            # 已先记一次；这里兜底直接 _sync_toc_payload(code=…) 的调用路径。
            self._toc_state_report = report
        # Lead round2 C1（结果归属）：磁盘结果 JSON 无报告身份、生成是本期级的，
        # 「本次」必须同时满足：本会话内为当前报告真跑过、rc 0/1、非失败/取消。
        # 重启（_toc_run_report 为空）/初次加载/换报告后读到本期最近结果都只算上次。
        self._toc_payload_this_run = bool(
            self._toc_payload
            and str(getattr(self, "_toc_run_report", "") or "") == str(report or "")
            and self._toc_run_code in (0, 1)
            and not self._toc_run_failed
            and not self._toc_run_cancelled)
        box = getattr(self, "_toc_box", None)
        if box is not None:
            box.controls = [self._toc_result()]
            self._safe_update(box)
        # 参数区反馈卡同样会写「本次生成完成」，必须跟着归属一起刷新（换报告/
        # 重启/初次加载时变成「上次结果」）。
        self._sync_toc_panel_result()

    def _sync_toc_panel_result(self) -> None:
        """若目录参数面板已建，刷新它的反馈区（串行时可能不在 DOM，面板内安全更新）。"""
        panel = getattr(self, "_toc_panel", None)
        refresh = getattr(panel, "refresh_result", None)
        if not callable(refresh):
            return
        try:
            refresh(self.field("toc_report"),
                    getattr(self, "_toc_run_code", None),
                    bool(getattr(self, "_toc_run_failed", False)),
                    bool(getattr(self, "_toc_run_cancelled", False)),
                    this_run=bool(getattr(self, "_toc_payload_this_run", False)))
        except Exception:  # noqa: BLE001 - 页面桩/未挂载
            pass

    def on_toc_state_changed(self) -> None:
        """B-375 共享门：栏目表/勾选/设置状态变化 → 立即刷新「生成目录」入口可用性。

        面板每次改值都会回调（TocPanel._notify_page）；未构建/未挂载时静默跳过。
        """
        try:
            self._sync_detail_status()
        except Exception:  # noqa: BLE001 - 页面构建期
            pass

    def on_toc_report_changed(self, report: str | None = None) -> None:
        """Lead task-17(c)：用户改「信息检查报告」后立即重算结果区归属。

        旧版只在 toc 运行完成/取消时重算；手动切换报告（键入/粘贴/清空/浏览回填）
        不触发重算，B 跑完后再切回 A 仍显示 B 的「本次生成完成」+B 产物。
        这里让每一轮运行归属失效，再按新报告重读磁盘结果——它只能算「上次」。
        """
        current = self.field("toc_report") if report is None else str(report or "")
        current = str(current or "").strip()
        self._toc_payload = {}
        self._toc_payload_report = ""
        self._toc_state_report = ""
        self._toc_run_report = ""
        self._toc_run_code = None
        self._toc_run_failed = False
        self._toc_run_cancelled = False
        self._toc_payload_this_run = False
        self._sync_toc_payload()
        try:
            self.refresh_running()
        except Exception:  # noqa: BLE001 - 页面构建期/未挂载
            pass
        self.mark("toc_report_changed", report=current)

    def _sync_issue_lock(self) -> None:
        """运行中锁住期目录字段与更换/清空入口，结束立即恢复可编辑（R7-a）。"""
        running = self._issue_change_locked()
        field = getattr(self, "_issue_field", None)
        if field is not None:
            field.read_only = running
            self._safe_update(field)
        for name in ("_issue_browse_btn", "_issue_clear_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.disabled = running
                self._safe_update(button)
        if not running:
            self._issue_lock_warned = False

    def refresh_running(self) -> None:
        self._sync_issue_lock()
        self._sync_stop_action()
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
        """T3 单入口：点击即自动扫描校验 + 清理（不再有预演闸/三档/确认词）。"""
        self._cleanup.start()

    # ── 兼容旧测试/旧入口 ──────────────────────────────────────────────
    def prefill(self, issue_dir: str = "", toc_report: str = "") -> None:
        # R7-a：深链/外部入口也要遵守运行中身份不变。
        if issue_dir and self._issue_change_locked() \
                and issue_dir.strip() != getattr(self, "_last_issue", ""):
            self._reject_issue_change(getattr(self, "_issue_field", None))
            issue_dir = ""
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

    def _toc_report_path(self) -> str:
        """非副作用解析「当前报告」：字段优先，空才回退磁盘最新报告。

        Lead task-17：渲染结果区/打开结果 JSON 不能顺手写字段——旧 _latest_report
        每次渲染都可能把 toc_report 改回最新报告，导致 B 的「本次」结果串到 A。
        回退值只用于展示/打开结果 JSON，绝不 set_field。
        """
        report = self.field("toc_report")
        if report:
            return report
        import ui.toc as TOC

        return TOC.latest_report(self._issue_dir())

    def _latest_report(self, *, apply: bool = False) -> str:
        """最新信息检查报告；默认不产生副作用（Lead task-17(a)）。

        默认：字段有值就用字段，空才回退磁盘最新报告，且不写字段（渲染路径专用）。
        apply=True：只给「刚跑完检查、明确回填最新报告」的动作用——重新取磁盘最新
        报告并写回字段；没有最新报告时退回正常的非副作用解析。
        """
        if apply:
            import ui.toc as TOC

            report = TOC.latest_report(self._issue_dir())
            if report:
                self.set_field("toc_report", report)
                return report
        return self._toc_report_path()

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
    "W1 写库四步：预览（不写库）→ 二次确认 → 校验 bibliography.jsonl 的库指纹"
    "（不符即拒绝、零写入）→ 原子替换。\n"
    "W2 只本地：写成功后自动本地提交 1 条（批量也只 1 条），"
    "只提交 bib_db/bibliography.jsonl；不 git push、不同步远端。\n"
    "只读视图：概况、查库、回读校验；回读校验只产出报告文件。"
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
        # P08 I-2：状态行 value 控件的引用（label → Text），供 refresh_status_rows 原地刷新。
        self._status_value_ctls: dict[str, ft.Text] = {}

    def _status_rows(self) -> list[tuple[str, str, bool]]:
        """设置页顶部状态行（P08 I-2）：每次调用都从 store/engine 现取，不做构建期快照。

        构建期快照会让「未保存改动」「设置文件」停在旧值：环境检查/保存后
        `store.dirty` 已经变了，界面却还写着「有」（P01-4.1-④）。
        """
        engine = self.app.engine_status
        store = self.app.store
        rows = [
            ("运行方式", engine.describe(), False),
            ("引擎目录", engine.engine_dir or "（未找到）", True),
            ("引擎版本", engine.version, False),
            ("问题", "；".join(engine.problems) if engine.problems else "无", False),
            ("设置文件", str(store.path), True),
            # P01 R01：有未写盘的改动时如实显示，不让内存状态冒充已持久化。
            ("未保存改动", "有（保存后写入设置文件）"
             if getattr(store, "dirty", False) else "无", False),
        ]
        # P01 R03/R07：高版本设置文件只读不改写，必须在页面上说清，不静默降级。
        unsupported = getattr(store, "unsupported_version", None)
        if unsupported:
            rows.append(
                ("设置版本", f"v{unsupported} 高于本版支持：本版不改写该文件，"
                             "请升级程序，或改用旧版副本回退", False))
        return rows

    def refresh_status_rows(self) -> None:
        """P08 I-2：按 store 现值原地刷新状态行（环境检查/保存后），不重建整页。

        未挂载（证据脚本 / 页面尚未构建）时静默跳过：`update()` 失败不影响业务。
        """
        ctls = getattr(self, "_status_value_ctls", None) or {}
        if not ctls:
            return
        for label, value, _mono in self._status_rows():
            ctl = ctls.get(label)
            if ctl is None:
                continue
            text = "" if value is None else str(value)
            if str(ctl.value or "") == text:
                continue
            ctl.value = text
            try:
                ctl.tooltip = text or None
            except Exception:  # noqa: BLE001
                pass
            try:
                ctl.update()
            except Exception:  # noqa: BLE001 未挂载/证据脚本：吞掉
                pass

    def on_show(self) -> None:
        """每次导航回设置页都重取状态（I-2）。"""
        self.refresh_status_rows()

    def _save_settings(self) -> None:
        """「立即保存」：保存成功才刷新状态行（I-2）。"""
        if self.app.save_settings(toast=True):
            self.refresh_status_rows()

    def build(self) -> ft.Control:
        t = self.t
        engine = self.app.engine_status
        self._status_value_ctls = {}
        engine_row_controls: list = []
        for label, value, mono in self._status_rows():
            row = C.kv_row(t, label, value, mono=mono)
            ctl = getattr(row, "value_ctl", None)
            if ctl is not None:
                self._status_value_ctls[label] = ctl
            engine_row_controls.append(row)
        # 批 115 P3-C2：12 张 C.card 长滚 → 4 组发丝分区（引擎与环境 / 外观 /
        # 窗口与托盘 / 路径与关于）。组 = section_label + 1px 线，组内不再套卡片；
        # settings_shell / ai_panel 的卡片用 _flat 去壳（那两个文件不在写域）。
        engine_line = C.inline_note(
            f"引擎{'就绪' if engine.ok else '未就绪'} · {engine.version or '版本未知'}",
            t, "ok" if engine.ok else "warn")
        engine_rows = C.block(
            *engine_row_controls,
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
            "设置", "外观、引擎、环境检查与诊断。", t,
            actions=[C.primary_button("立即保存", lambda e: self._save_settings(), t,
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

        def _typed(e) -> None:
            """手输/粘贴/✕ 清空也写回 store（P01 R02：路径值必须参与保存）。

            不逐键写盘：存内存并标 dirty，等「立即保存」或关窗时统一落盘。
            """
            text = str(getattr(e.control, "value", "") or "").strip()
            self.app.store.set(f"paths.{key}", text)
            if key == "engine_override":
                # 旧键同步（启动读取 engine_override；浏览分支同样双写）。
                self.app.store.set("engine_override", text)
            self.app.mark("path_change", key=key, typed=True)

        control = C.path_picker(label, value, "", self.t, required=False, on_change=_typed,
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
        # P08 I-1：跳过态（level=info / status=skip / detail 含「跳过」）不算成功，
        # 也不该混进 failed 清单——它压根没参与结论。
        rows = SHELL.envcheck_rows(payload)
        ok_count = sum(1 for row in rows if row["ok"])
        skipped = [row["title"] for row in rows if row["skipped"]]
        self.app.mark("envcheck", ok=bool(result.get("ok")), code=result.get("code"),
                      checks=len(checks), ok_count=ok_count,
                      skipped=skipped[:20],
                      failed=[str(c.get("id")) for c in checks
                              if SHELL.envcheck_verdict(c) == "fail"][:20],
                      profile=payload.get("profile"), platform=payload.get("platform"))
        if result.get("ok"):
            self.app.set_check_done(True)
        # P08 I-2 / P01-4.1-④：env_check_done 已写盘（dirty=False），状态行必须跟着变。
        self.refresh_status_rows()
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
