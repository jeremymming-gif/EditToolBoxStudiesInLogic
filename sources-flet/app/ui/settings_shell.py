"""设置外壳：环境检查、诊断包、全局日志、快捷键与设置页的补充卡片。

只做展示与取值。诊断包默认只含版本、平台、状态与错误类别；设置内容、路径与
日志正文不默认导出，附加内容必须本次显式勾选并先看预览（P01 R04/R05）。
"""
from __future__ import annotations

import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import flet as ft

from services import ai_prefs as AI
from services import engine_bridge as EB
from services import winplat
from ui import components as C

LOG_LIMIT = 4000
LOG_TRIM = 2000
FIX_HINTS = {
    "xelatex": "安装 TeX Live 或 MiKTeX，并把 xelatex 加入 PATH",
    "biber": "安装 biber（TeX Live 自带），并确认版本与 biblatex 匹配",
    "latexmk": "安装 latexmk（TeX Live 自带）",
    "fonts": "安装模板所需中文字体（宋体/黑体/等线）",
    "engine": "在「设置 → 路径」里指定引擎目录，或重装发行包",
    "python": "安装 Python 3.11+ 并让引擎随包携带的 Python 可用",
    "pdf": "确认 PDF 工具（pdfinfo/pdftoppm）在 PATH 中",
    "path": "检查路径是否含空格/中文导致工具链找不到文件",
}
SHORTCUTS = [
    {"keys": "Ctrl+,", "action": "settings", "label": "打开设置"},
    {"keys": "F1", "action": "help", "label": "帮助与关于"},
    {"keys": "F5", "action": "refresh", "label": "重新检测引擎"},
    {"keys": "F9", "action": "run", "label": "运行当前步骤"},
    {"keys": "Esc", "action": "stop", "label": "停止运行"},
    {"keys": "Ctrl+Shift+D", "action": "theme", "label": "切换深浅色"},
    {"keys": "Ctrl+1…5", "action": "page", "label": "切换页面（第 1–5 页）"},
]
PAGE_KEYS = ("home", "single", "issue", "bib", "settings", "help")


# ── 全局日志 ─────────────────────────────────────────────────────────────
def classify(line: str) -> str:
    text = str(line or "")
    low = text.lower()
    if "❌" in text or "traceback" in low or "error:" in low:
        return "error"
    if "⚠" in text or "warning" in low or "warn:" in low:
        return "warn"
    if EB.is_summary_line(text):
        return "summary"
    return "info"


class LogBuffer:
    """内存里的全局日志：最近 LOG_LIMIT 行，超出按 LOG_TRIM 成块裁剪。"""

    def __init__(self, limit: int = LOG_LIMIT, trim: int = LOG_TRIM) -> None:
        self.limit = max(1, int(limit))
        self.trim = max(1, int(trim))
        self._rows: list[tuple[str, str]] = []

    def add(self, line: str, level: str = "") -> None:
        self._rows.append((level or classify(line), str(line)))
        if len(self._rows) > self.limit:
            del self._rows[:self.trim]

    def clear(self) -> None:
        self._rows.clear()

    def count(self) -> int:
        return len(self._rows)

    def lines(self, level: str = "all") -> list[str]:
        if level in ("", "all"):
            return [line for _level, line in self._rows]
        return [line for lv, line in self._rows if lv == level]

    def text(self, level: str = "all") -> str:
        return "\n".join(self.lines(level))


def log_buffer(app) -> LogBuffer:
    buffer = getattr(app, "log_buffer", None)
    if not isinstance(buffer, LogBuffer):
        buffer = LogBuffer()
        app.log_buffer = buffer
    return buffer


# ── 快捷键 ───────────────────────────────────────────────────────────────
def hotkey_action(key: str, ctrl: bool = False, shift: bool = False,
                  alt: bool = False) -> str:
    name = str(key or "")
    if ctrl and shift and name.lower() == "d":
        return "theme"
    if ctrl and name == ",":
        return "settings"
    if ctrl and name.isdigit() and 1 <= int(name) <= len(PAGE_KEYS):
        return f"page:{int(name)}"
    if name == "F1":
        return "help"
    if name == "F5":
        return "refresh"
    if name == "F9":
        return "run"
    if name == "Escape":
        return "stop"
    return ""


def _suppress_auto_page_update(app, event, reason: str) -> None:
    """声明本按键事件不需要框架补整页 patch；抑制失败退回旧行为并记 marker。"""
    if C.mark_event_updated(reason):
        return
    try:
        app.mark("hotkey_autopatch_unsuppressed", reason=reason,
                 key=str(getattr(event, "key", "")))
    except Exception:  # noqa: BLE001 marker 通道失败也不得影响按键
        pass


def on_key(app, event) -> None:
    # 批 114：搜索框聚焦时 ↑↓/Escape 先交给搜索（Enter 走 TextField on_submit）。
    search_hook = getattr(app, "search_key_handle", None)
    if callable(search_hook):
        try:
            if search_hook(event):
                _suppress_auto_page_update(app, event, "search_key")
                return
        except Exception:  # noqa: BLE001 搜索键盘处理失败不能拖垮全局热键
            pass
    action = hotkey_action(getattr(event, "key", ""), bool(getattr(event, "ctrl", False)),
                           bool(getattr(event, "shift", False)),
                           bool(getattr(event, "alt", False)))
    if not action:
        # Q2（FIX-20261112）：未命中热键的按键（绝大多数普通输入）不能留给框架的
        # 自动整页 patch —— page.on_keyboard_event 让每个按键都走这里，而整页 patch
        # 会把正在输入的 TextField 重建掉（首字母后失焦、每键一次全量 patch）。
        # 命中热键的分支行为不变：navigate/set_theme 走 _coalesced_update 各自负责发送。
        _suppress_auto_page_update(app, event, "hotkey_miss")
        return
    app.mark("hotkey", action=action, key=str(getattr(event, "key", "")))
    if action.startswith("page:"):
        index = int(action.split(":", 1)[1]) - 1
        if 0 <= index < len(PAGE_KEYS) and PAGE_KEYS[index] in app.pages:
            app.goto(PAGE_KEYS[index])
    elif action in ("settings", "help"):
        app.goto(action)
    elif action == "refresh":
        app.refresh_engine_status()
        app.rebuild_shell()
        app.toast("引擎状态已刷新")
    elif action == "run":
        _run_current(app)
    elif action == "stop":
        # 对话框打开时 Escape = 取消对话框（比停跑更符合预期，也避免误停）
        if C.dismiss_top_dialog(app.page):
            app.mark("dialog_dismissed", by="Escape")
            return
        app.stop_run()
    elif action == "theme":
        app.set_theme("light" if app.theme == "dark" else "dark")


def _run_current(app) -> None:
    page = app.pages.get(app.current_page_key)
    if page is None:
        return
    for name in ("run_step_demo", "run_current_demo", "run_demo"):
        action = getattr(page, name, None)
        if callable(action):
            try:
                if name == "run_step_demo":
                    action(getattr(page, "selected", ""))
                else:
                    action()
                return
            except Exception:  # noqa: BLE001
                continue
    app.toast("这一页没有可快捷键运行的步骤")


def install_hotkeys(app) -> None:
    try:
        app.page.on_keyboard_event = lambda event: on_key(app, event)
        app.mark("hotkeys_installed", count=len(SHORTCUTS))
    except Exception as exc:  # noqa: BLE001
        app.mark("hotkeys_error", error=f"{type(exc).__name__}: {exc}")


def shortcut_rows(t) -> list:
    return [ft.Row(spacing=t.sp("1"), controls=[
        C.text(item["keys"], t, "small", t.palette.text, weight="semibold"),
        ft.Container(expand=True),
        C.text(item["label"], t, "small", t.palette.text_muted),
    ]) for item in SHORTCUTS]


# ── 环境检查 ─────────────────────────────────────────────────────────────
# P08 I-1：这三种 status 在引擎里表示「未参与结论」，不是成功。
SKIP_STATUSES = frozenset({"info", "skip", "skipped", "n/a", "not_applicable"})


def check_ok(check: dict) -> bool:
    """只有真正的成功才算 ok；跳过/未参与结论的条目不算（P08 I-1）。"""
    return envcheck_verdict(check) == "success"


def envcheck_skipped(check: dict) -> bool:
    """条目是否是「跳过 / 未参与结论」态。

    引擎 `envcheck.py` 的 `_item()` 用 `level`（required/optional/info）表达是否参与
    结论，`status` 只表达结果；`output_writable` 就是 `level="info"` + `status="ok"`
    + detail 含「跳过」——旧实现只看 status，于是把跳过画成绿色 ok（P01-4.1-③）。
    """
    status = str(check.get("status") or "").strip().lower()
    severity = str(check.get("severity") or check.get("level") or "").strip().lower()
    detail = str(check.get("detail") or "")
    return severity == "info" or status in SKIP_STATUSES or "跳过" in detail


def envcheck_verdict(check: dict) -> str:
    """一条检查的结论：success / warn / fail / skipped（skip 不算成功）。"""
    if envcheck_skipped(check):
        return "skipped"
    status = str(check.get("status") or "").strip().lower()
    if status in ("ok", "ok-ish", "pass", "pass-ish", "success"):
        return "success"
    return "warn" if status.startswith("warn") else "fail"


def _fix_hint(check: dict) -> str:
    ident = str(check.get("id") or "").lower()
    for key, hint in FIX_HINTS.items():
        if key in ident:
            return hint
    return "点「运行环境检查」重试；仍失败请看设置页的引擎目录与 PATH"


def envcheck_rows(payload: dict) -> list[dict]:
    """把 envcheck JSON 的 checks 转成行；detail/severity/skipped 全部保真（P08 I-1）。"""
    rows: list[dict] = []
    for check in (payload or {}).get("checks") or []:
        if not isinstance(check, dict):
            continue
        verdict = envcheck_verdict(check)
        status = str(check.get("status") or "?")
        detail = str(check.get("detail") or "")
        severity = str(check.get("severity") or check.get("level") or "")
        skipped = verdict == "skipped"
        rows.append({
            "ok": verdict == "success",
            "verdict": verdict,
            "skipped": skipped,
            "title": str(check.get("title") or check.get("id") or ""),
            "version": str(check.get("version") or ""),
            "status": status,
            "badge": "跳过" if skipped else (status or "?"),
            "tone": {"success": "success", "warn": "warn",
                     "fail": "error", "skipped": "idle"}[verdict],
            "detail": detail,
            "severity": severity,
            # 跳过的行不给修复建议（未参与结论，不该催用户去修）。
            "fix": "" if verdict in ("success", "skipped")
                   else str(check.get("hint") or _fix_hint(check)),
        })
    return rows


def _envcheck_row_detail(row: dict) -> tuple[str, str]:
    """行下说明的 (文本, 语气)。返回空文本表示不显示。"""
    if row["skipped"]:
        detail = row["detail"] or "本条未参与结论"
        if "未参与" not in detail:
            detail = f"{detail}（未参与结论）"
        return detail, "info"
    if row["verdict"] in ("fail", "warn"):
        parts = []
        if row["detail"]:
            parts.append(f"原因：{row['detail']}")
        if row["fix"]:
            parts.append(f"修复：{row['fix']}")
        return "；".join(parts), "warn" if row["verdict"] == "warn" else "error"
    return "", ""


_BADGE_TONE = {"success": "success", "warn": "warn", "error": "error", "idle": "neutral"}
_DETAIL_COLOR = {"info": "text_muted", "warn": "warn", "error": "error"}


def render_envcheck(page) -> None:
    t = page.t
    slot = page.envcheck_slot
    if getattr(page, "envcheck_error", ""):
        slot.content = ft.Column(spacing=t.sp("1"), controls=[
            C.notice_bar(page.envcheck_error, t, "warn"),
            C.secondary_button("重试", lambda e: page.page.run_task(page._envcheck_task), t,
                               icon="REFRESH"),
        ])
    elif not getattr(page, "envcheck_data", None):
        slot.content = C.text("尚未运行。检查只读展示，不修改系统。", t, "small",
                              t.palette.text_muted)
    else:
        payload = page.envcheck_data
        rows: list = [C.text(f"{payload.get('profile', '')} · {payload.get('platform', '')}",
                             t, "caption", t.palette.text_faint)]
        for row in envcheck_rows(payload):
            rows.append(ft.Row(spacing=t.sp("1"),
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.status_dot(row["tone"], t, 8),
                C.text(row["title"], t, "small", weight="medium"),
                C.text(row["version"], t, "small", t.palette.text_muted),
                ft.Container(expand=True),
                C.badge(row["badge"] or "?", t, _BADGE_TONE.get(row["tone"], "neutral")),
            ]))
            detail, tone = _envcheck_row_detail(row)
            if detail:
                rows.append(C.text(f"  {detail}", t, "caption",
                                   getattr(t.palette, _DETAIL_COLOR[tone])))
        slot.content = ft.Column(spacing=t.sp("1"), controls=rows)
    try:
        slot.update()
    except Exception:  # noqa: BLE001
        pass


def envcheck_card(page) -> ft.Control:
    t = page.t
    first_run = not bool(page.app.store.get("env_check_done", False))
    rows: list = [
        C.section_title("环境检查", t, note="只读检查本机环境（不写文件）"),
        ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.secondary_button("运行环境检查",
                               lambda e: page.page.run_task(page._envcheck_task), t,
                               icon="HEALTH_AND_SAFETY_OUTLINED"),
            C.text("检查 TeX 工具链、引擎、字体与路径；失败不阻塞启动。", t, "caption",
                   t.palette.text_faint),
        ]),
    ]
    if first_run:
        rows.append(C.notice_bar("首次启动建议先跑一次环境检查；失败项会给出修复入口。",
                                 t, "info"))
    rows.append(page.envcheck_slot)
    return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)


def tray_card(page) -> ft.Control:
    """窗口与托盘：托盘开关只在 Windows 可用（默认关）。"""
    t = page.t
    app = page.app

    def _status_text() -> str:
        if not winplat.IS_WIN:
            return "托盘是 Windows 功能；macOS 上关闭窗口即退出。"
        if getattr(app, "tray_active", False):
            return "托盘已启动：关闭窗口后从通知区菜单「显示主窗口」恢复，选「退出」才结束。"
        if bool(getattr(app, "tray_enabled", False)):
            return "已启用但托盘尚未启动（启动失败会自动回退）；关掉再打开可重试。"
        return "未启用：关闭窗口即退出。"

    status = C.text(_status_text(), t, "caption", t.palette.text_faint)

    def _toggle(e) -> None:
        app.set_tray_enabled(bool(e.control.value))
        switch.value = bool(getattr(app, "tray_enabled", False))
        status.value = _status_text()
        try:
            switch.update()
            status.update()
        except Exception:  # noqa: BLE001
            pass

    switch = C.switch(t, value=bool(getattr(app, "tray_enabled", False)),
                      disabled=not winplat.IS_WIN, on_change=_toggle)
    return C.card(ft.Column(spacing=t.sp("2"), controls=[
        C.section_title("窗口与托盘", t, note="仅 Windows"),
        ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.text("关闭窗口时最小化到托盘", t, "body"),
            ft.Container(expand=True),
            switch,
        ]),
        status,
        C.text("启用后立即尝试启动托盘图标；启动失败会回退为关闭窗口即退出，"
               "不会让窗口关不掉。设置写入本机设置文件，下次启动沿用。",
               t, "caption", t.palette.text_muted),
    ]), t)


def first_run_needed(store) -> bool:
    return not bool(store.get("env_check_done", False))


# ── 诊断包 ───────────────────────────────────────────────────────────────
# P01 R04/R05：默认包只输出「非内容」信息——版本、平台、状态与错误类别；设置
# 值、路径、最近记录与日志正文一律不默认导出。附加内容（设置白名单摘要 / 日志
# 尾部）必须本次显式勾选，导出前先看预览；写入文件的就是预览里的那段文本。
LOG_TAIL_LINES = 200
_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_BEARER_LIKE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd)"
    r"(\s*[=:]\s*)(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;\"']{4,})")


def _store_of(app):
    return getattr(app, "store", None)


def _log_counts(logs: str) -> dict:
    counts = {"error": 0, "warn": 0, "summary": 0, "all": 0}
    for line in str(logs or "").splitlines():
        if not line.strip():
            continue
        counts["all"] += 1
        level = classify(line)
        if level in counts:
            counts[level] += 1
    return counts


def _settings_state_lines(app) -> list[str]:
    """设置状态（存在/版本/未保存），不输出任何设置值。"""
    store = _store_of(app)
    lines = ["设置内容: （已隐藏）默认诊断不导出设置值、路径与日志正文"]
    if store is None:
        return lines + ["设置文件: （不可用）"]
    path = Path(str(getattr(store, "path", "")))
    lines.append("设置文件: " + ("存在" if path.exists() else "尚未创建"))
    status = str(getattr(store, "version_status", "") or "")
    version = getattr(store, "file_version", None)
    lines.append(f"设置版本: {status or '未知'}" + (f"（文件 v{version}）" if version else ""))
    lines.append("未保存改动: " + ("有" if getattr(store, "dirty", False) else "无"))
    if getattr(store, "unsupported_version", None):
        lines.append(f"警告: 设置文件版本 {store.unsupported_version} 高于本版支持，已拒绝改写")
    return lines


def _settings_summary(store) -> str:
    """附加诊断里的设置白名单摘要：不含 Key、路径、最近记录、表单与问题单状态。"""
    data = getattr(store, "data", {}) if store is not None else {}
    if not isinstance(data, dict):
        data = {}
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    options = data.get("options") if isinstance(data.get("options"), dict) else {}
    bibdb = data.get("bibdb") if isinstance(data.get("bibdb"), dict) else {}
    summary = {
        "theme": data.get("theme"),
        "zoom": data.get("zoom"),
        "last_page": data.get("last_page"),
        "last_route": data.get("last_route"),
        "button_palette": data.get("button_palette"),
        "tray_enabled": bool(data.get("tray_enabled")),
        "log_show_raw": bool(data.get("log_show_raw")),
        "env_check_done": bool(data.get("env_check_done")),
        "bib_view": data.get("bib_view"),
        "options": {key: bool(value) for key, value in options.items()
                    if isinstance(key, str) and isinstance(value, bool)},
        "bibdb": {key: bibdb[key] for key in
                  ("verify_max", "verify_plan", "verify_all_refs", "lookup_enabled")
                  if key in bibdb},
        "ai": {
            "enabled": bool(ai.get("enabled")),
            "key_set": bool(str(ai.get("key") or "").strip()),
            "model": str(ai.get("model") or ""),
            "protocol": str(ai.get("protocol") or ""),
            "thinking": bool(ai.get("thinking")),
            "article_budget_tokens": ai.get("article_budget_tokens"),
            "batch_budget_tokens": ai.get("batch_budget_tokens"),
            "verify_max": ai.get("verify_max"),
        },
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _scrub_secrets(text: str, store) -> str:
    """日志尾部可能含用户贴入的 Key：输出前替换本机 Key 与常见 Key 形状。"""
    out = str(text or "")
    data = getattr(store, "data", {}) if store is not None else {}
    ai = data.get("ai") if isinstance(data, dict) and isinstance(data.get("ai"), dict) else {}
    secret = str(ai.get("key") or "").strip()
    if secret:
        out = out.replace(secret, "（已隐藏）")
    out = _KEY_LIKE.sub("（已隐藏）", out)
    out = _BEARER_LIKE.sub("Bearer （已隐藏）", out)
    out = _ASSIGNED_SECRET.sub(r"\1\2（已隐藏）", out)
    return out


def build_diagnosis(app, envcheck: dict | None = None, logs: str = "",
                    *, settings_summary: bool = False, log_tail: bool = False) -> str:
    """诊断文本：默认最小集；`settings_summary` / `log_tail` 只能由导出对话框显式勾选。"""
    engine = getattr(app, "engine_status", None)
    envcheck = envcheck or {}
    page = getattr(app, "page", None)
    store = _store_of(app)
    problems = getattr(engine, "problems", None) or []
    engine_line = (f"引擎: {'就绪' if bool(getattr(engine, 'ok', False)) else '未就绪'}"
                   f" · {getattr(engine, 'version', '') or '版本未知'}"
                   f" · {getattr(engine, 'mode', '') or '未知模式'}")
    if problems:
        engine_line += f" · 问题 {len(problems)} 项"
    lines = [
        "=== 编辑部工具箱诊断 ===",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"应用: {getattr(app, 'app_version', '?')} · Flet {getattr(app, 'flet_version', '?')}"
        f" · Python {getattr(app, 'python_version', '?')}",
        f"系统: {platform.platform()} · {platform.machine()}",
        f"平台: {getattr(page, 'platform', '?')} · {getattr(app, 'dpi', '?')} dpi"
        f" · 窗口 {getattr(app, 'window_w', '?')}×{getattr(app, 'window_h', '?')}",
        engine_line,
        *_settings_state_lines(app),
        "环境检查:",
    ]
    rows = envcheck_rows(envcheck)
    if rows:
        for row in rows:
            if row["skipped"]:
                # P01-4.1-③：跳过项在诊断文本里也不能写成 ok。
                lines.append(f"  {row['title']}: 跳过（{row['detail'] or '未参与结论'}）")
            else:
                lines.append(f"  {row['title']}: {row['status']}")
    else:
        lines.append("  （未运行）")
    counts = _log_counts(logs)
    lines.append(f"日志: 共 {counts['all']} 行（错误 {counts['error']}"
                 f" · 警告 {counts['warn']} · 摘要 {counts['summary']}）")
    if settings_summary:
        lines.append("")
        lines.append("设置摘要（本机白名单；不含 Key、路径与最近记录）:")
        lines.append(_settings_summary(store))
    if log_tail:
        lines.append("")
        lines.append(f"最近日志（本次显式勾选，最多 {LOG_TAIL_LINES} 行）:")
        tail = "\n".join(str(logs or "").splitlines()[-LOG_TAIL_LINES:])
        lines.append(_scrub_secrets(tail, store) or "  （暂无日志）")
    lines.append("=== 结束（默认不含设置内容、路径与日志正文；附加内容需本次显式勾选）===")
    return "\n".join(lines)


def diagnosis_dir(app) -> Path:
    return Path(app.store.path).parent / "diagnostics"


def write_diagnosis(app, envcheck: dict | None = None, logs: str = "",
                    *, text: str | None = None) -> str:
    """写诊断文件。`text` 给定时逐字节写它（导出 = 预览）；失败抛 `OSError` 且不留文件。"""
    body = text if isinstance(text, str) else build_diagnosis(app, envcheck, logs)
    folder = diagnosis_dir(app)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"诊断-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    app.mark("diagnosis_exported", path=str(path), bytes=path.stat().st_size)
    return str(path)


def copy_diagnosis(page) -> None:
    """复制默认诊断文本（不含设置内容、路径与日志正文）。"""
    page.app.copy_text(build_diagnosis(page.app, getattr(page, "envcheck_data", None),
                                       log_buffer(page.app).text()))
    page.mark("diagnosis_copied")


def _export(page) -> None:
    """导出对话框：默认最小集；附加内容勾选后即时刷新预览，导出写预览原文。"""
    app = page.app
    t = page.t
    envcheck = getattr(page, "envcheck_data", None)
    logs = log_buffer(app).text()

    def _preview_text() -> str:
        return build_diagnosis(app, envcheck, logs,
                               settings_summary=bool(settings_box.value),
                               log_tail=bool(logs_box.value))

    preview = C.text_field(
        t, value=build_diagnosis(app, envcheck, logs), multiline=True,
        min_lines=10, max_lines=14, read_only=True, mono=True, clearable=False,
        expand=True)

    def _refresh(e=None) -> None:
        try:
            preview.value = _preview_text()
            preview.update()
        except Exception:  # noqa: BLE001 预览刷新失败不拦导出
            pass

    settings_box = ft.Checkbox(
        label="包含设置白名单摘要（不含 Key、路径、最近记录）",
        value=False, on_change=_refresh)
    logs_box = ft.Checkbox(
        label=f"包含最近日志（最多 {LOG_TAIL_LINES} 行；可能含文件路径，密钥形状先打码）",
        value=False, on_change=_refresh)

    def _close() -> None:
        try:
            page.page.pop_dialog()
        except Exception:  # noqa: BLE001
            pass

    def _cancel(e=None) -> None:
        _close()

    def _confirmed(e=None) -> None:
        text = preview.value or ""
        _close()
        try:
            path = write_diagnosis(app, envcheck, logs, text=text)
        except OSError as exc:
            page.app.toast(f"诊断包写入失败：{exc}")
            page.mark("diagnosis_error", error=str(exc))
            return
        page.app.toast(f"诊断包已写入：{path}")
        page.app.open_path(str(Path(path).parent))

    dialog = ft.AlertDialog(
        modal=True,
        title=C.text("导出诊断包", t, role="dialog-title"),
        content=ft.Container(
            width=t.px(620),
            content=ft.Column(spacing=t.sp("1"), tight=True, controls=[
                C.text("默认只含版本、平台、状态与错误类别；附加内容请先勾选并核对下方预览，"
                       "写入文件的内容与预览逐字相同。", t, "small", t.palette.text_muted),
                settings_box,
                logs_box,
                preview,
            ])),
        actions=[
            C.text_button("取消", _cancel, t),
            C.primary_button("导出", _confirmed, t),
        ],
    )
    page.page.show_dialog(dialog)


def diagnostics_card(page) -> ft.Control:
    t = page.t
    rows: list = [
        C.section_title("诊断包", t, note="默认只含版本与状态；附加内容需勾选并预览"),
        _kv("设置文件", str(page.app.store.path), t),
        _kv("诊断目录", str(diagnosis_dir(page.app)), t),
        _kv("日志", f"内存最近 {log_buffer(page.app).count()} 行（上限 {LOG_LIMIT}）", t),
        ft.Row(spacing=t.sp("1"), controls=[
            C.primary_button("导出诊断包", lambda e: _export(page), t, icon="BUG_REPORT_OUTLINED"),
            C.text_button("复制诊断信息", lambda e: copy_diagnosis(page), t),
            C.text_button("打开设置目录", lambda e: page.app.open_path(
                str(Path(page.app.store.path).parent)), t),
        ]),
    ]
    return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)


def _kv(label: str, value: str, t) -> ft.Row:
    return ft.Row(spacing=t.sp("1"), controls=[
        C.text(label, t, "small", t.palette.text_muted),
        ft.Container(expand=True),
        C.text(str(value), t, "small", t.palette.text),
    ])


# ── 日志卡 ───────────────────────────────────────────────────────────────
LEVELS = [("all", "全部"), ("error", "错误"), ("warn", "警告"), ("summary", "摘要")]


def logs_card(page) -> ft.Control:
    t = page.t
    buffer = log_buffer(page.app)
    current = str(page.app.store.get("form.log_filter", "all") or "all")
    if current not in dict(LEVELS):
        current = "all"
    page.log_filter = C.select(
        t, value=current, width=t.px(130), label="级别", options=LEVELS,
        on_select=lambda e: _set_filter(page, e.control.value),
    )
    page.log_follow = C.switch(
        t, value=bool(page.app.store.get("form.log_follow", True)),
        on_change=lambda e: page.app.store.set("form.log_follow", bool(e.control.value)),
    )
    page.log_meta = C.text("", t, "caption", t.palette.text_faint)
    page.log_list = ft.ListView(expand=True, spacing=t.px(1), auto_scroll=True,
                                height=t.px(180), padding=0)
    page.log_slot = ft.Container(content=page.log_list)
    controls = [
        C.section_title("日志", t, note=f"内存最近 {LOG_LIMIT} 行；导出诊断包可留档"),
        # 单行工具条：标签内联，下拉与按钮同为 control_h 高（用户 m02816：不许高矮不齐）。
        ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.text("级别", t, "caption", t.palette.text_muted),
            page.log_filter,
            C.text("自动跟随", t, "caption", t.palette.text_muted),
            page.log_follow,
            ft.Container(expand=True),
            C.text_button("复制全部", lambda e: page.app.copy_text(
                buffer.text(page.log_filter.value or "all")), t, height=t.control_h()),
            C.text_button("跳到最新", lambda e: _jump(page), t, height=t.control_h()),
            C.text_button("清空", lambda e: _clear_logs(page), t, height=t.control_h()),
            # 用户 m02990：生成诊断包是明确动作 → 描边按钮（复制/跳转/清空仍为文字工具）。
            C.secondary_button("导出诊断包", lambda e: _export(page), t, icon="ARCHIVE",
                               height=t.control_h()),
        ]),
        page.log_meta,
        page.log_slot,
    ]
    render_logs(page)
    return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)


def _set_filter(page, value) -> None:
    page.app.store.set("form.log_filter", str(value or "all"))
    render_logs(page)


def _jump(page) -> None:
    try:
        page.log_list.auto_scroll = True
        page.log_list.update()
    except Exception:  # noqa: BLE001
        pass


def _clear_logs(page) -> None:
    def _confirmed() -> None:
        log_buffer(page.app).clear()
        page.app.mark("log_cleared")
        render_logs(page)

    C.confirm(page.page, page.t, title="清空日志？", message="只清空内存里的日志，不影响设置与文件。",
              confirm_label="清空", on_confirm=_confirmed)


def render_logs(page) -> None:
    t = page.t
    buffer = log_buffer(page.app)
    level = (getattr(page, "log_filter", None).value if getattr(page, "log_filter", None) else "all")
    lines = buffer.lines(level or "all")
    page.log_list.controls = [C.text(line, t, "caption",
                                     t.palette.text_muted if level in ("", "all") else
                                     t.palette.text) for line in lines[-500:]]
    page.log_meta.value = f"显示 {len(lines)} 行 · 共 {buffer.count()} 行"
    for control in (page.log_list, page.log_meta):
        try:
            control.update()
        except Exception:  # noqa: BLE001
            pass


def render_logs_throttled(page, *, now: float | None = None,
                          min_interval_s: float = 0.15) -> bool:
    """合并窗口内的日志渲染请求；窗口外立即渲染。返回本次是否真的渲染。"""
    now = time.monotonic() if now is None else float(now)
    last = getattr(page, "_logs_render_last_mono", None)
    if last is not None and (now - last) < min_interval_s:
        page._logs_render_pending = True
        return False
    page._logs_render_last_mono = now
    page._logs_render_pending = False
    render_logs(page)
    return True


def flush_logs(page) -> bool:
    """运行结束时补一次渲染，保证尾部日志可见；无待渲染则不动。"""
    if not getattr(page, "_logs_render_pending", False):
        return False
    page._logs_render_last_mono = time.monotonic()
    page._logs_render_pending = False
    render_logs(page)
    return True


# ── 网络 / 代理 / 测试连接 ───────────────────────────────────────────────
def mask_proxy(url: str) -> str:
    text = str(url or "")
    if "@" in text:
        head, tail = text.split("@", 1)
        if "//" in head:
            scheme, _rest = head.split("//", 1)
            return f"{scheme}//***@{tail}"
        return f"***@{tail}"
    return text


def proxy_rows() -> list[dict]:
    rows = []
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        value = os.environ.get(name) or os.environ.get(name.lower()) or ""
        rows.append({"name": name, "value": mask_proxy(value) or "未设置"})
    return rows


def connection_probe(ai: dict) -> dict:
    data = AI.normalize(ai or {})
    items = [
        {"ok": bool(data["key"]), "label": "API Key",
         "detail": "已设置" if data["key"] else "未设置：在下面 AI 段填 Key"},
        {"ok": str(data["base"]).startswith(("http://", "https://")), "label": "接口地址",
         "detail": str(data["base"])},
        {"ok": bool(str(data["model"]).strip()), "label": "模型",
         "detail": str(data["model"])},
    ]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    items.append({"ok": True, "label": "网络代理", "detail": mask_proxy(proxy) or "未设置（直连）"})
    return {
        "ok": all(item["ok"] for item in items),
        "items": items,
        "cost_notice": "真实请求会计费：一次测试约 ≤ ¥0.5；默认不点不花。",
        "confirm": "将发送一次真实 API 请求（测试连接），预计花费 ≤ ¥0.5。继续？",
    }


def probe_ai_api(base: str, key: str, model: str, timeout: float = 15.0) -> tuple[bool, str]:
    """真实测试连接（仅在用户确认后调用）；不打印、不记录 Key。"""
    url = str(base).rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"已连通（HTTP {response.status}）"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"已连通但 Key 无效（HTTP {exc.code}）"
        return False, f"接口返回 HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"连不上：{exc}"


def proxy_card(page) -> ft.Control:
    t = page.t
    rows: list = [
        C.section_title("网络代理", t, note="跟随系统环境变量；改后重启外壳"),
        *[_kv(row["name"], row["value"], t) for row in proxy_rows()],
        ft.Row(spacing=t.sp("1"), controls=[
            C.secondary_button("测试 AI 连接", lambda e: test_connection(page), t,
                               icon="NETWORK_CHECK"),
            C.text("先做离线检查；真实请求需二次确认并计费。", t, "caption",
                   t.palette.text_faint),
        ]),
        page.connection_slot,
    ]
    return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)


def test_connection(page) -> None:
    ai = AI.from_store(page.app.store)
    probe = connection_probe(ai)
    rows: list = [C.text(f"{item['label']}：{item['detail']}", page.t, "small",
                         page.t.palette.text if item["ok"] else page.t.palette.warn)
                  for item in probe["items"]]
    rows.append(C.text(probe["cost_notice"], page.t, "caption", page.t.palette.text_faint))
    if probe["ok"]:
        def _confirmed() -> None:
            page.mark("connection_confirm", base=ai["base"], model=ai["model"])
            page.page.run_task(_probe_task, page, ai)

        rows.append(C.primary_button("发送真实测试请求", lambda e: C.confirm(
            page.page, page.t, title="测试连接", message=probe["confirm"],
            confirm_label="发送请求", on_confirm=_confirmed), page.t, icon="SEND"))
    else:
        rows.append(C.notice_bar("离线检查未通过：补齐上面的项后再试（未发送任何请求）。",
                                 page.t, "warn"))
    page.connection_slot.content = C.card(ft.Column(spacing=page.t.sp("1"), controls=rows),
                                          page.t)
    try:
        page.connection_slot.update()
    except Exception:  # noqa: BLE001
        pass
    page.mark("connection_probe", ok=probe["ok"])


async def _probe_task(page, ai: dict) -> None:
    import asyncio

    ok, detail = await asyncio.to_thread(probe_ai_api, ai.get("base", ""), ai.get("key", ""),
                                         ai.get("model", ""))
    page.connection_slot.content = C.notice_bar(
        ("连接成功：" if ok else "连接失败：") + detail, page.t, "success" if ok else "error")
    try:
        page.connection_slot.update()
    except Exception:  # noqa: BLE001
        pass
    page.mark("connection_result", ok=ok, detail=detail)


# ── 外观：按钮配色 C/D ───────────────────────────────────────────────────
def button_palette_row(page) -> ft.Control:
    """「外观 → 按钮配色」：C 石板蓝 / D 石墨 radio；切换即时生效、落 settings.json。"""
    t = page.t
    current = getattr(page.app, "button_palette", "c")

    def _changed(e) -> None:
        page.app.set_button_palette(str(e.control.value or "c"))

    row = ft.RadioGroup(
        value=current, on_change=_changed,
        content=ft.Row(spacing=t.sp("2"), controls=[
            ft.Radio(value="c", label="C · 石板蓝"),
            ft.Radio(value="d", label="D · 石墨"),
        ]),
    )
    page.button_palette_group = row
    return ft.Row(spacing=t.sp("1"), controls=[
        C.text("按钮配色", t, "body"), row, ft.Container(expand=True),
        C.text("C 标准 / D 去蓝", t, "caption", t.palette.text_faint)])


# ── 高级 ─────────────────────────────────────────────────────────────────
def advanced_card(page) -> ft.Control:
    t = page.t
    rows: list = [
        C.section_title("高级", t),
        C.option_row("首次环境检查已完成", "兼容旧版；失败不阻塞启动", t,
                     icon="CHECK_CIRCLE_OUTLINE",
                     checked=bool(page.app.store.get("env_check_done", False)),
                     on_change=lambda e: page.app.set_check_done(e.control.value)),
        ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("恢复默认设置", lambda e: _reset(page), t, icon="RESTART_ALT"),
            C.text_button("打开设置文件", lambda e: page.app.open_path(
                str(page.app.store.path)), t),
            C.text_button("打开设置目录", lambda e: page.app.open_path(
                str(Path(page.app.store.path).parent)), t),
        ]),
        C.text("快捷键见「帮助与关于」；日志只在内存里保留最近 4000 行。", t, "caption",
               t.palette.text_faint),
    ]
    return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)


def _reset(page) -> None:
    def _confirmed() -> None:
        store = page.app.store
        store.data = store.defaults_copy()
        store.mark_dirty()
        page.app.mark("settings_reset")
        saved = bool(page.app.save_settings(toast=False))
        if saved:
            page.app.toast("已恢复默认设置")
        else:
            page.app.toast("默认设置已恢复（仅内存），写入磁盘失败，请检查磁盘空间后再保存")
        page.app.rebuild_shell()

    C.confirm(page.page, page.t, title="恢复默认设置？",
              message="会重置外观、路径、选项与 AI 设置，API Key 也会清空。",
              confirm_label="恢复默认", on_confirm=_confirmed)
