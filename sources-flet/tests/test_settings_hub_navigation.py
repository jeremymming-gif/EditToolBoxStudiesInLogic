"""Settings hub routing, search destinations, summaries, and dirty state."""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from ui import ia_map as IA
from ui import pages as P
from ui import search_index as SI
from ui import settings_shell as SHELL
from ui import settings_detail as SD
from ui.tokens import Tokens


VIEW_LABELS = {
    "appearance": "外观",
    "engine": "引擎与环境",
    "paths": "路径",
    "diagnostics": "日志与诊断",
    "advanced": "高级与关于",
    "window": "窗口与托盘",
    "language": "语言与地区",
    "ai": "AI 与模型",
    "proxy": "网络代理",
}

EXPECTED_ROUTES = {
    "home", "single:convert", "single:proofcheck",
    "issue:overview", "issue:infocheck", "issue:toc", "issue:order",
    "issue:refresh", "issue:merge", "issue:cleantemp", "issue:dirpush",
    "bib:overview", "bib:query", "bib:import", "bib:normalize", "bib:review",
    "bib:edit", "bib:dupes", "bib:fieldfix", "bib:health", "bib:settings",
    "settings", "settings:appearance", "settings:engine", "settings:paths",
    "settings:diagnostics", "settings:advanced", "settings:window",
    "settings:language", "settings:ai", "settings:proxy", "help",
}


class _Store:
    def __init__(self) -> None:
        self.data = {
            "zoom": 1.1,
            "button_palette": "c",
            "env_check_done": True,
            "tray_enabled": False,
            "engine_override": "/private/engine/path",
            "paths": {
                "engine_override": "/private/engine/path",
                "notes_repo": "/private/notes/path",
            },
            "ai": {
                "enabled": True,
                "key": "SECRET_API_KEY_FOR_TESTS",
                "base": "https://private.example.test/endpoint",
                "model": "safe-test-model",
                "protocol": "chat",
                "thinking": False,
                "article_budget_tokens": 30000,
                "batch_budget_tokens": 1000000,
            },
            "form": {},
        }
        self.path = "/private/settings/settings.json"
        self.dirty = False
        self.unsupported_version = None

    def get(self, key: str, default=None):
        current = self.data
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        parts = str(key).split(".")
        current = self.data
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
        self.dirty = True


def _app():
    app = SimpleNamespace()
    app.store = _Store()
    app.page = SimpleNamespace(
        run_task=lambda *args, **kwargs: None,
        update=lambda: None,
        show_dialog=lambda *args, **kwargs: None,
        pop_dialog=lambda: None,
        set_clipboard=lambda *args, **kwargs: None,
        platform="macos",
    )
    app.t = Tokens("light", 1.1, 96)
    app.engine_status = SimpleNamespace(
        ok=True, version="1.8.0", engine_dir="/private/engine/path", problems=[],
        describe=lambda: "源码引擎 · /private/engine/path", mode="source",
    )
    app.effective_zoom = 1.1
    app.max_zoom = 1.5
    app.button_palette = "c"
    app.tray_enabled = False
    app.tray_active = False
    app.app_version = "test-version"
    app.flet_version = "test-flet"
    app.python_version = "3.14-test"
    app.window_w, app.window_h, app.dpi = 1160, 780, 96
    app.log_buffer = SHELL.LogBuffer()
    app.mark = lambda *args, **kwargs: None
    app.toast = lambda *args, **kwargs: None
    app.copy_text = lambda *args, **kwargs: None
    app.open_path = lambda *args, **kwargs: None
    app.browse_field = lambda *args, **kwargs: None
    app.on_theme_switch = lambda *args, **kwargs: None
    app.set_zoom = lambda value: setattr(app, "effective_zoom", value)
    app.set_button_palette = lambda value: setattr(app, "button_palette", value)
    app.set_check_done = lambda value: app.store.set("env_check_done", value)
    app.set_tray_enabled = lambda value: setattr(app, "tray_enabled", value)
    app.redetect_engine = lambda: None
    app.persist = lambda: None

    def save_settings(*, toast=False):
        app.store.dirty = False
        return True

    app.save_settings = save_settings
    return app


def _walk(control):
    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, (list, tuple)):
            for item in child:
                if isinstance(item, ft.Control):
                    yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(control) -> list[str]:
    return [str(item.value) for item in _walk(control)
            if isinstance(getattr(item, "value", None), str)]


@pytest.fixture
def settings_page(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-user:proxy-password@proxy.example.test:8080")
    monkeypatch.setattr(SHELL, "log_buffer", lambda app: app.log_buffer)
    app = _app()
    app.log_buffer.add("LOG_BODY_SECRET_FOR_TESTS", "info")
    page = P.SettingsPage(app)
    root = page.build()
    return app, page, root


def test_settings_routes_groups_labels_and_exact_route_set(settings_page):
    _app_instance, _page, _root = settings_page
    assert set(IA.all_routes()) == EXPECTED_ROUTES
    assert len(IA.all_routes()) == 32
    assert IA.normalize_route("settings") == ("settings", "")

    for key, label in VIEW_LABELS.items():
        route = f"settings:{key}"
        assert IA.normalize_route(route) == ("settings", key)
        labels = IA.route_labels(route)
        assert labels["view_label"] == label
        assert labels["group_label"] == ("AI" if key in {"ai", "proxy"} else "通用")

    assert IA.normalize_route("settings:not-a-view") == ("settings", "")


def test_hub_rows_navigate_to_nine_details_and_existing_bib_preferences(settings_page):
    app, page, _root = settings_page
    page.activate("")
    hub = page._content_slot.content
    labels = _texts(hub)
    assert all(label in labels for label in ("通用", "AI", "引用库"))
    assert all(label in labels for label in (*VIEW_LABELS.values(), "引用库偏好"))

    buttons = [item for item in _walk(hub)
               if isinstance(item, ft.TextButton) and item.on_click is not None]
    assert len(buttons) == 10
    routes = []
    app.navigate = lambda route, *, source="nav": routes.append((route, source))
    for button in buttons:
        button.on_click(None)
    assert routes == [
        *((f"settings:{key}", "settings") for key in VIEW_LABELS),
        ("bib:settings", "settings"),
    ]


def test_all_nine_views_activate_and_invalid_view_returns_to_hub(settings_page):
    _app_instance, page, _root = settings_page
    assert set(SD._BUILDERS) == set(VIEW_LABELS)

    for key, label in VIEW_LABELS.items():
        page.activate(key)
        assert page._active_view == key
        assert page._status_slot.visible is (key == "engine")
        assert label in _texts(page._content_slot.content)

    page.activate("not-a-view")
    assert page._active_view == ""
    assert "引用库偏好" in _texts(page._content_slot.content)


def test_search_index_directly_targets_each_settings_route(settings_page):
    _app_instance, _page, _root = settings_page
    entries = SI.nav_entries(IA.NAV_TREE)
    by_key = {entry.key: entry for entry in entries if entry.kind == "page"}

    for key, label in VIEW_LABELS.items():
        entry = by_key[f"settings:{key}"]
        assert entry.action == f"goto:settings:{key}"
        assert entry.label == f"设置 · {label}"


def test_hub_summaries_are_live_and_do_not_expose_private_values(settings_page):
    _app_instance, page, _root = settings_page
    page.activate("")
    hub = page._content_slot.content
    summary = "\n".join(
        [*_texts(hub), *(str(getattr(item, "tooltip", "") or "") for item in _walk(hub))]
    )

    assert "已配置 2 项" in summary
    assert "已开启 · safe-test-model" in summary
    assert "内存日志 1 行" in summary
    assert "已配置代理" in summary
    for private_value in (
        "SECRET_API_KEY_FOR_TESTS", "proxy-password", "LOG_BODY_SECRET_FOR_TESTS",
        "/private/engine/path", "/private/notes/path", "/private/settings/settings.json",
    ):
        assert private_value not in summary


def test_dirty_status_and_save_action_stay_available_on_hub_and_details(settings_page):
    app, page, root = settings_page

    def save_actions():
        return [control for control in _walk(root)
                if isinstance(control, ft.FilledButton)
                and control.content == "立即保存"]

    page.activate("")
    assert "所有设置已保存" in _texts(page._dirty_slot)
    assert len(save_actions()) == 1

    app.store.dirty = True
    page._refresh_dirty_status()
    assert "有未保存改动" in _texts(page._dirty_slot)
    page.activate("appearance")
    assert "有未保存改动" in _texts(page._dirty_slot)
    assert len(save_actions()) == 1

    page._save_settings()
    assert app.store.dirty is False
    assert "所有设置已保存" in _texts(page._dirty_slot)
