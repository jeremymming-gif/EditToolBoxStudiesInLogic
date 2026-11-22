"""legacy-idea-8: settings categories and ignored legacy Bib preferences."""
from __future__ import annotations

import json
from types import SimpleNamespace

import flet as ft
import pytest

from ui import bib_prefs as BP
from ui import bib_review as BR
from ui.ai_panel import AiSettings
from ui.pages import SettingsPage
from ui import settings_shell as SHELL
from ui.tokens import Tokens


LEGACY_VALUES = {
    "bibdb.verify_max": 37,
    "bibdb.verify_plan": True,
    "bibdb.verify_all_refs": True,
}


@pytest.fixture(autouse=True)
def _synthetic_manuscripts_root(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-manuscripts"
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(root))
    monkeypatch.setenv("SLCN_TEST_DATASET_ID", "legacy-idea-8-synthetic-v1")
    return root


class _Store:
    def __init__(self):
        self.data = {**LEGACY_VALUES, "bibdb.lookup_enabled": True}
        self.reads: list[str] = []
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        self.reads.append(key)
        return self.data.get(key, default)

    def set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value


class _App:
    notes_repo = ""
    app_version = "test"

    def __init__(self, store):
        self.store = store
        self.toasts: list[str] = []
        self.routes: list[tuple[str, str]] = []

    def toast(self, message):
        self.toasts.append(message)

    def navigate(self, route, *, source="nav"):
        self.routes.append((route, source))


class _Host:
    def __init__(self, tmp_path):
        self.store = _Store()
        self.app = _App(self.store)
        self.t = Tokens()
        self.stats_data = {"db": str(tmp_path / "synthetic-bib" / "bibliography.jsonl")}
        self.events: list[tuple[str, dict]] = []
        self.routes: list[str] = []

    def mark(self, event, **fields):
        self.events.append((event, fields))

    def notify(self, message):
        self.app.toast(message)

    def navigate_route(self, route):
        self.routes.append(route)


def _walk(control):
    yield control
    for name in ("controls", "content"):
        child = getattr(control, name, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(root):
    return [control.value for control in _walk(root)
            if isinstance(getattr(control, "value", None), str)]


def _button_label(control):
    content = getattr(control, "content", None)
    return content if isinstance(content, str) else getattr(content, "value", None)


def test_legacy_bib_preferences_are_not_read_or_rewritten(tmp_path):
    host = _Host(tmp_path)
    view = BP.BibPrefsView(host)

    view._load_form()
    assert host.store.reads == ["bibdb.lookup_enabled"]
    assert view.lookup_enabled is True

    view.lookup_enabled = False
    view._save()

    assert host.store.writes == [("bibdb.lookup_enabled", False)]
    assert host.store.data == {**LEGACY_VALUES, "bibdb.lookup_enabled": False}


def test_bib_preferences_view_keeps_lookup_and_hides_legacy_controls(tmp_path):
    host = _Host(tmp_path)
    view = BP.BibPrefsView(host)
    view._load_form()

    texts = _texts(view._group_db())
    assert "引用库设置" in texts
    assert "先查引用库" in texts
    assert "核实上限" not in texts
    assert "先算账（不花钱）" not in texts
    assert "允许全量核实" not in texts


def test_bib_preferences_version_link_navigates_to_sync_page(tmp_path):
    host = _Host(tmp_path)
    view = BP.BibPrefsView(host)
    link = view._version_link()

    assert _button_label(link) == "打开版本与同步"
    link.on_click(None)
    assert host.routes == ["bib:version_sync"]


def test_ai_settings_does_not_render_legacy_verify_max_control(tmp_path):
    host = _Host(tmp_path)

    texts = _texts(AiSettings(host).build())

    assert not any("verify_max" in text or "条数上限" in text for text in texts)
    assert "单篇预算 tokens" in texts
    assert "批量预算 tokens" in texts


def test_settings_shell_uses_three_category_names():
    assert SHELL.SETTINGS_CATEGORIES == ("通用", "AI", "引用库")
    assert SHELL.settings_category_note("通用", "日志") == "通用设置 · 日志"
    assert SHELL.settings_category_note("AI", "网络代理") == "AI设置 · 网络代理"
    assert SHELL.settings_category_note("引用库", "偏好") == "引用库设置 · 偏好"
    with pytest.raises(ValueError):
        SHELL.settings_category_note("其它", "偏好")


class _SettingsStore:
    path = "/tmp/legacy-idea-8/settings.json"
    dirty = False
    unsupported_version = None

    def __init__(self):
        self.data = {"options": {}, "ai": {}}

    def get(self, key, default=None):
        return self.data.get(key, default)


class _SettingsApp:
    def __init__(self):
        self.t = Tokens()
        self.store = _SettingsStore()
        self.page = SimpleNamespace()
        self.engine_status = SimpleNamespace(
            ok=True, version="test", engine_dir="/tmp/engine", problems=[],
            describe=lambda: "测试引擎")
        self.effective_zoom = 1.0
        self.max_zoom = 1.5
        self.flet_version = "test"
        self.python_version = "test"
        self.window_w, self.window_h, self.dpi = 1200, 800, 96
        self.app_version = "test"
        self.routes: list[tuple[str, str]] = []

    def navigate(self, route, *, source="nav"):
        self.routes.append((route, source))

    def on_theme_switch(self, _event):
        pass


def test_settings_page_renders_three_groups_and_links_to_bib_preferences(
        tmp_path, monkeypatch):
    app = _SettingsApp()
    app.store.path = str(tmp_path / "settings.json")
    page = SettingsPage(app)
    page._status_rows = lambda: []
    monkeypatch.setattr(SHELL, "envcheck_card", lambda _page: ft.Text("环境检查内容"))
    monkeypatch.setattr(SHELL, "button_palette_row", lambda _page: ft.Text("按钮配色"))
    monkeypatch.setattr(SHELL, "tray_card", lambda _page: ft.Text("窗口与托盘内容"))
    monkeypatch.setattr(SHELL, "proxy_card", lambda _page: ft.Text("网络代理内容"))
    monkeypatch.setattr(SHELL, "logs_card", lambda _page: ft.Text("日志内容"))
    monkeypatch.setattr(SHELL, "advanced_card", lambda _page: ft.Text("高级内容"))
    monkeypatch.setattr(SHELL, "diagnostics_card", lambda _page: ft.Text("诊断包内容"))

    root = page.build()
    texts = _texts(root)
    assert all(label in texts for label in ("通用", "AI", "引用库"))
    assert all(label in texts for label in ("AI 功能总开关", "环境检查内容",
                                             "引用库路径、默认查库行为与库状态在引用库设置中维护。"))
    links = [control for control in _walk(root)
             if isinstance(control, (ft.Button, ft.OutlinedButton, ft.TextButton))
             and _button_label(control) == "打开引用库设置"]
    assert len(links) == 1
    links[0].on_click(None)
    assert app.routes == [("bib:settings", "settings")]


def test_review_success_banner_links_to_version_sync(monkeypatch, tmp_path):
    host = _Host(tmp_path)
    view = BR.ReviewView(host)
    view.success = "已完成 · 已提交 · 未推送"
    view.loaded = True
    monkeypatch.setattr(BR.ReviewView, "_design_width", lambda _self: 1200)
    monkeypatch.setattr(BR.ReviewView, "_queue", lambda _self: ft.Text("queue"))
    monkeypatch.setattr(BR.ReviewView, "_detail", lambda _self: ft.Text("detail"))
    monkeypatch.setattr(BR.ReviewView, "_batch", lambda _self: None)
    monkeypatch.setattr(BR.ReviewView, "_confirm_bar", lambda _self: None)

    root = view._body()
    buttons = [control for control in _walk(root)
               if isinstance(control, (ft.Button, ft.OutlinedButton, ft.TextButton))
               and _button_label(control) == "查看版本与同步"]
    assert len(buttons) == 1
    buttons[0].on_click(None)
    assert host.routes == ["bib:version_sync"]


def test_diagnostic_summary_omits_legacy_bib_preferences():
    store = SimpleNamespace(data={
        "bibdb": {"verify_max": 37, "verify_plan": True,
                  "verify_all_refs": True, "lookup_enabled": False},
        "ai": {"verify_max": 37, "article_budget_tokens": 50000,
               "batch_budget_tokens": 100000},
    })

    summary = json.loads(SHELL._settings_summary(store))

    assert summary["bibdb"] == {"lookup_enabled": False}
    assert not ({"verify_max", "verify_plan", "verify_all_refs"}
                & summary["bibdb"].keys())
    assert "verify_max" not in summary["ai"]
    assert summary["ai"]["article_budget_tokens"] == 50000
    assert summary["ai"]["batch_budget_tokens"] == 100000
