"""批 112 S2：换肤不重建（GUI-03）契约测试。

四类断言：
1. `palette_remap` 完备/对称（浅深两向，含 #FFFFFF 一词多义的 bg/fg 分工）；
2. `_retint` 递归假控件树（bgcolor / Text.color / Border 四边 / Divider / 嵌套），未知色与 None 不动；
3. `set_theme` 热路径走 retint、**不调 rebuild_shell**；异常/空壳才退回重建并记 fallback；
4. `scripts/bench_theme.py` 的阈值纯函数（p95 / summarize）。

不启动 Flet 窗口（只用 `App.__new__` + 假 page/store），和 gui_auto 的交互桌面复核互补。
"""
from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui.tokens import DARK, LIGHT, Palette, Tokens, palette_remap

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bench_theme as B  # noqa: E402


def _light_values() -> set[str]:
    return {getattr(LIGHT, field.name) for field in fields(Palette)}


def _dark_values() -> set[str]:
    return {getattr(DARK, field.name) for field in fields(Palette)}


# ── 1. 映射完备性与对称性 ─────────────────────────────────────────────────
def test_palette_remap_complete_and_symmetric():
    bg = palette_remap("dark")
    fg = palette_remap("dark", role="fg")
    back = palette_remap("light")
    assert set(bg) == _light_values() == set(fg)  # 浅色板每个色值都能查到
    assert set(back) == _dark_values()            # 深→浅同样完备

    # 反向（深→浅）色值唯一，必须逐字段精确。
    light_counts = {
        value: sum(1 for field in fields(Palette) if getattr(LIGHT, field.name) == value)
        for value in _light_values()
    }
    dark_counts = {
        value: sum(1 for field in fields(Palette) if getattr(DARK, field.name) == value)
        for value in _dark_values()
    }
    for field in fields(Palette):
        light_value = getattr(LIGHT, field.name)
        dark_value = getattr(DARK, field.name)
        if light_counts[light_value] == 1 and dark_counts[dark_value] == 1:
            assert back[dark_value] == light_value
            assert bg[light_value] == dark_value and fg[light_value] == dark_value

    # 一词多义：#FFFFFF 背景角色 → surface，前景角色 → on_accent。
    assert bg["#FFFFFF"] == DARK.surface
    assert fg["#FFFFFF"] == DARK.on_accent
    assert bg[LIGHT.text_faint] == DARK.text_faint == DARK.log_muted
    with pytest.raises(ValueError):
        palette_remap("sepia")
    with pytest.raises(ValueError):
        palette_remap("dark", role="whatever")


# ── 2. _retint 递归假树 ───────────────────────────────────────────────────
def _sample_tree() -> ft.Container:
    border = ft.Border(top=ft.BorderSide(1, LIGHT.border), right=ft.BorderSide(1, LIGHT.border),
                       bottom=ft.BorderSide(1, LIGHT.border), left=ft.BorderSide(1, LIGHT.border))
    return ft.Container(
        bgcolor=LIGHT.surface,
        border=border,
        content=ft.Column(controls=[
            ft.Text("正文", color=LIGHT.text),
            ft.Divider(color=LIGHT.border),
            ft.Container(bgcolor=LIGHT.accent_soft,
                         content=ft.Text("胶囊", color=LIGHT.on_accent_soft)),
            ft.Text("未知色", color="#123456"),
            ft.Text("无颜色", color=None),
            ft.Text("白字", color="#FFFFFF"),  # 前景角色：白 → on_accent
        ]),
    )


def test_retint_walks_nested_tree_without_unknown_colors():
    app = M.App.__new__(M.App)
    tree = _sample_tree()
    changed = app._retint(tree, palette_remap("dark"), palette_remap("dark", role="fg"))
    assert changed > 0
    assert tree.bgcolor == DARK.surface
    text, divider, badge, unknown, colorless, white = tree.content.controls
    assert text.color == DARK.text
    assert divider.color == DARK.border
    assert badge.bgcolor == DARK.accent_soft
    assert badge.content.color == DARK.on_accent_soft
    assert unknown.color == "#123456"        # 未知色不动
    assert colorless.color is None           # None 不动
    assert white.color == DARK.on_accent     # fg 角色优先 on_accent
    assert [getattr(tree.border, name).color for name in ("top", "right", "bottom", "left")] == \
        [DARK.border] * 4


def test_retint_log_area_is_recognised_by_semantics():
    app = M.App.__new__(M.App)
    log_area = ft.Container(bgcolor=LIGHT.log_bg, content=ft.ListView(controls=[]))
    other = ft.Container(bgcolor=LIGHT.log_bg, content=ft.Text("不是日志"))
    tree = ft.Column(controls=[log_area, other])
    found = app._retint_log_areas(tree, LIGHT.log_bg)
    assert found == [log_area]


# ── 3. set_theme 热路径 ───────────────────────────────────────────────────
def _app_for_theme() -> SimpleNamespace:
    app = M.App.__new__(M.App)
    app.running = False
    app.theme = "light"
    app.zoom = 1.0
    app.dpi = 96
    app.t = Tokens("light", 1.0, 96)
    app.engine_status = SimpleNamespace(ok=True)
    app.dpi_aware = False
    app._marker_path = None
    app.client_hwnd = 0
    app.titlebar_info = {}
    app.log_result = SimpleNamespace(t=app.t)  # 持 Tokens 快照的 helper
    app.page = SimpleNamespace(theme_mode=None, bgcolor=None, controls=[],
                               update=lambda: None)
    app.sidebar = ft.Container(bgcolor=LIGHT.sidebar,
                               border=ft.Border(right=ft.BorderSide(1, LIGHT.border)))
    app.theme_switch = ft.Switch(value=False, active_color=LIGHT.accent)
    app.status_bar = SimpleNamespace(root=ft.Container(bgcolor=LIGHT.surface), metas=[],
                                     set_meta=lambda meta: app.status_bar.metas.append(meta))
    app.toast_ctl = SimpleNamespace(root=ft.Container(bgcolor=LIGHT.accent_soft))
    app.pages = {"home": SimpleNamespace(root=ft.Text("x"), _log=app.log_result)}
    app.store = SimpleNamespace(set=lambda key, value: None)
    app.page.controls = [app.sidebar, app.theme_switch, app.status_bar.root]
    app.apply_titlebar = lambda: None
    app.persist = lambda: None
    app.toast = lambda message: None
    return app


def _marks(capsys) -> list[dict]:
    return [json.loads(line.split("P2MARK ", 1)[1])
            for line in capsys.readouterr().out.splitlines() if "P2MARK " in line]


def test_set_theme_retints_hot_path_without_rebuild(monkeypatch, capsys):
    app = _app_for_theme()
    rebuilds: list[int] = []
    app.rebuild_shell = lambda: rebuilds.append(1)

    app.set_theme("dark")

    assert rebuilds == []                       # 热路径绝不重建外壳
    assert app.t.palette is DARK
    assert app.page.theme_mode == ft.ThemeMode.DARK
    assert app.page.bgcolor == DARK.bg
    assert app.sidebar.bgcolor == DARK.sidebar
    assert app.theme_switch.value is True
    meta = app.status_bar.metas[-1]
    # S1m：状态栏只留一句人话，不再写 DPI / Flet / 缩放。
    assert "dpi" not in meta and "Flet" not in meta and "×" not in meta
    assert app.log_result.t is app.t            # Tokens 快照已刷新
    marks = {row["event"]: row for row in _marks(capsys)}
    assert marks["theme_begin"]["mode"] == "retint"
    assert marks["theme_changed"]["theme"] == "dark"
    assert marks["theme_changed"]["mode"] == "retint"
    assert isinstance(marks["theme_changed"]["duration_ms"], (int, float))
    assert marks["theme_changed"]["retinted"] > 0
    assert "theme_retint_fallback" not in marks


def test_set_theme_falls_back_to_rebuild_on_retint_error(monkeypatch, capsys):
    app = _app_for_theme()
    rebuilds: list[int] = []
    app.rebuild_shell = lambda: rebuilds.append(1)

    def boom(self, old):
        raise RuntimeError("fake retint failure")

    monkeypatch.setattr(M.App, "_retint_shell", boom)
    app.set_theme("dark")

    assert rebuilds == [1]
    marks = {row["event"]: row for row in _marks(capsys)}
    assert "theme_retint_fallback" in marks
    assert marks["theme_changed"]["mode"] == "rebuild"


def test_set_theme_without_shell_rebuilds(capsys):
    app = _app_for_theme()
    app.page.controls = []
    rebuilds: list[int] = []
    app.rebuild_shell = lambda: rebuilds.append(1)
    app.set_theme("dark")
    assert rebuilds == [1]
    marks = {row["event"]: row for row in _marks(capsys)}
    assert marks["theme_changed"]["mode"] == "rebuild"
    assert "theme_retint_fallback" not in marks


# ── 4. bench 阈值纯函数 ───────────────────────────────────────────────────
def test_bench_p95_and_summarize_thresholds():
    assert B.p95([]) == 0.0
    assert B.p95([7.0]) == 7.0
    assert B.p95(range(1, 21)) == 19  # 最近秩：ceil(0.95*20)-1 = 18

    good = B.summarize([9.0, 10.0, 11.0, 12.0], shell_built=1, fallbacks=0)
    assert good["ok"] and good["median_ms"] == 10.5 and good["checks"]["shell_built_once"]

    slow = B.summarize([41.0, 50.0], shell_built=1, fallbacks=0)
    assert not slow["ok"] and not slow["checks"]["median"]
    tail = B.summarize([10.0] * 18 + [90.0, 91.0], shell_built=1, fallbacks=0)
    assert not tail["ok"] and not tail["checks"]["p95"]
    regressed = B.summarize([10.0, 10.0], shell_built=2, fallbacks=1)
    assert not regressed["ok"]
    assert not regressed["checks"]["shell_built_once"]
    assert not regressed["checks"]["no_fallback"]
