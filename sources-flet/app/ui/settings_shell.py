"""设置外壳：环境检查、诊断包、全局日志、快捷键与设置页的补充卡片。

只做展示与取值；密钥、运行日志等敏感内容不进诊断包（设置段统一脱敏）。
"""
from __future__ import annotations

import json
import os
import platform
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
SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "auth")


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


def on_key(app, event) -> None:
    action = hotkey_action(getattr(event, "key", ""), bool(getattr(event, "ctrl", False)),
                           bool(getattr(event, "shift", False)),
                           bool(getattr(event, "alt", False)))
    if not action:
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
def check_ok(check: dict) -> bool:
    return (str(check.get("status") or "")).lower() in ("ok", "ok-ish", "pass", "pass-ish",
                                                        "success")


def _fix_hint(check: dict) -> str:
    ident = str(check.get("id") or "").lower()
    for key, hint in FIX_HINTS.items():
        if key in ident:
            return hint
    return "点「运行环境检查」重试；仍失败请看设置页的引擎目录与 PATH"


def envcheck_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for check in (payload or {}).get("checks") or []:
        if not isinstance(check, dict):
            continue
        ok = check_ok(check)
        status = str(check.get("status") or "?")
        rows.append({
            "ok": ok,
            "title": str(check.get("title") or check.get("id") or ""),
            "version": str(check.get("version") or ""),
            "status": status,
            "tone": "success" if ok else ("warn" if status.lower().startswith("warn") else "error"),
            "fix": "" if ok else _fix_hint(check),
        })
    return rows


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
            color = {"success": t.palette.success, "warn": t.palette.warn,
                     "error": t.palette.error}[row["tone"]]
            rows.append(ft.Row(spacing=t.sp("1"),
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Container(width=t.px(8), height=t.px(8), border_radius=99, bgcolor=color),
                C.text(row["title"], t, "small", weight="medium"),
                C.text(row["version"], t, "small", t.palette.text_muted),
                ft.Container(expand=True),
                C.badge(row["status"] or "?", t, row["tone"]),
            ]))
            if row["fix"]:
                rows.append(C.text(f"  修复：{row['fix']}", t, "caption", t.palette.text_faint))
        slot.content = ft.Column(spacing=t.sp("1"), controls=rows)
    try:
        slot.update()
    except Exception:  # noqa: BLE001
        pass


def envcheck_card(page) -> ft.Control:
    t = page.t
    first_run = not bool(page.app.store.get("env_check_done", False))
    rows: list = [
        C.section_title("环境检查", t, note="调 Engine/envcheck.py --json（只读）"),
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

    switch = ft.Switch(value=bool(getattr(app, "tray_enabled", False)),
                       active_color=t.palette.accent, disabled=not winplat.IS_WIN,
                       on_change=_toggle)
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
def redact(value, depth: int = 0):
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if any(hint in str(key).lower() for hint in SECRET_HINTS):
                out[key] = "（已隐藏）"
            else:
                out[key] = redact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value]
    return value


def build_diagnosis(app, envcheck: dict | None = None, logs: str = "") -> str:
    engine = getattr(app, "engine_status", None)
    envcheck = envcheck or {}
    page = getattr(app, "page", None)
    lines = [
        "=== 编辑部工具箱诊断 ===",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"应用版本: {getattr(app, 'app_version', '?')} · Flet {getattr(app, 'flet_version', '?')}"
        f" · Python {getattr(app, 'python_version', '?')}",
        f"系统: {platform.platform()} · {platform.machine()}",
        f"平台/DPI/窗口: {getattr(page, 'platform', '?')} · {getattr(app, 'dpi', '?')} dpi"
        f" · {getattr(app, 'window_w', '?')}x{getattr(app, 'window_h', '?')}",
        f"引擎: {getattr(engine, 'describe', lambda: '?')()} · 版本 {getattr(engine, 'version', '?')}"
        f" · 目录 {getattr(engine, 'engine_dir', '?')}",
        f"设置文件: {getattr(getattr(app, 'store', None), 'path', '?')}",
        "设置（脱敏）:",
        json.dumps(redact(getattr(getattr(app, "store", None), "data", {})),
                   ensure_ascii=False, indent=2)[:6000],
        "环境检查:",
    ]
    rows = envcheck_rows(envcheck)
    if rows:
        lines.append(f"  {envcheck.get('profile', '')} · {envcheck.get('platform', '')}")
        for row in rows:
            lines.append(f"  [{row['status']}] {row['title']} {row['version']}".rstrip())
    else:
        lines.append("  （未运行）")
    lines.append("最近日志（内存，最多 4000 行）:") 
    tail = "\n".join((logs or "").splitlines()[-400:])
    lines.append(tail or "  （暂无日志）")
    lines.append("=== 结束（本文件不含 API Key / Token）===")
    return "\n".join(lines)


def diagnosis_dir(app) -> Path:
    return Path(app.store.path).parent / "diagnostics"


def write_diagnosis(app, envcheck: dict | None = None, logs: str = "") -> str:
    folder = diagnosis_dir(app)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"诊断-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    path.write_text(build_diagnosis(app, envcheck, logs), encoding="utf-8")
    app.mark("diagnosis_exported", path=str(path), bytes=path.stat().st_size)
    return str(path)


def copy_diagnosis(page) -> None:
    page.app.copy_text(build_diagnosis(page.app, getattr(page, "envcheck_data", None),
                                       log_buffer(page.app).text()))
    page.mark("diagnosis_copied")


def _export(page) -> None:
    def _confirmed() -> None:
        try:
            path = write_diagnosis(page.app, getattr(page, "envcheck_data", None),
                                   log_buffer(page.app).text())
        except OSError as exc:
            page.app.toast(f"诊断包写入失败：{exc}")
            page.mark("diagnosis_error", error=str(exc))
            return
        page.app.toast(f"诊断包已写入：{path}")
        page.app.open_path(str(Path(path).parent))

    C.confirm(page.page, page.t, title="导出诊断包？",
              message="写入设置目录；包含版本、路径、设置（已脱敏）、环境检查与最近日志，"
                      "不含 API Key。",
              confirm_label="导出", on_confirm=_confirmed)


def diagnostics_card(page) -> ft.Control:
    t = page.t
    rows: list = [
        C.section_title("诊断包", t, note="二次确认后写文件并打开目录"),
        _kv("设置文件", str(page.app.store.path), t),
        _kv("诊断目录", str(diagnosis_dir(page.app)), t),
        _kv("日志", f"内存最近 {log_buffer(page.app).count()} 行（上限 {LOG_LIMIT} 行）", t),
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
    page.log_filter = ft.Dropdown(
        label="级别", value=current, width=t.px(130), dense=True,
        text_size=t.font("body"),
        options=[ft.DropdownOption(key=key, text=text) for key, text in LEVELS],
        on_select=lambda e: _set_filter(page, e.control.value),
    )
    page.log_follow = ft.Switch(
        value=bool(page.app.store.get("form.log_follow", True)),
        active_color=t.palette.accent,
        on_change=lambda e: page.app.store.set("form.log_follow", bool(e.control.value)),
    )
    page.log_meta = C.text("", t, "caption", t.palette.text_faint)
    page.log_list = ft.ListView(expand=True, spacing=t.px(1), auto_scroll=True,
                                height=t.px(180), padding=0)
    page.log_slot = ft.Container(content=page.log_list)
    controls = [
        C.section_title("日志", t, note=f"内存最近 {LOG_LIMIT} 行；导出诊断包可留档"),
        ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            page.log_filter,
            C.text("自动跟随", t, "caption", t.palette.text_muted),
            page.log_follow,
            ft.Container(expand=True),
            C.text_button("复制全部", lambda e: page.app.copy_text(
                buffer.text(page.log_filter.value or "all")), t),
            C.text_button("跳到最新", lambda e: _jump(page), t),
            C.text_button("清空", lambda e: _clear_logs(page), t),
            C.text_button("导出诊断包", lambda e: _export(page), t),
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


# ── 高级 ─────────────────────────────────────────────────────────────────
def advanced_card(page) -> ft.Control:
    t = page.t
    rows: list = [
        C.section_title("高级", t),
        C.option_row("首次环境检查已完成", "兼容 Tk 版 envCheckDone；失败不阻塞启动", t,
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
        page.app.store.data = page.app.store.defaults_copy()
        page.app.save_settings(toast=False)
        page.app.mark("settings_reset")
        page.app.toast("已恢复默认设置")
        page.app.rebuild_shell()

    C.confirm(page.page, page.t, title="恢复默认设置？",
              message="会重置外观、路径、选项与 AI 设置（API Key 也会清空），再写入设置文件。",
              confirm_label="恢复默认", on_confirm=_confirmed)
