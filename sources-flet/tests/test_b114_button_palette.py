"""批 114 F3：C/D 按钮配色可点击（验收 P1-1，不启动 GUI）。

锁三件事：
1. settings.json 存 `c` → 主按钮读 `t.buttons` 的 C 填充色 #3D5A80；
2. 切 `d` → 主按钮/令牌立刻变 #1C1C1E，且落盘（store.set + save）；
3. 设置页「外观」卡里有 C/D 两个 radio，改选会走同一 set+save 路径。

命令层不建窗口、不跑引擎；设置文件用 tmp_path。
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import flet as ft

import main as M
from services.settings_store import SettingsStore, normalize_button_palette
from ui import components as C
from ui import pages as P
from ui import settings_shell as SHELL
from ui.tokens import Tokens


def _app(tmp_path):
    """最小 App 壳：只装 set_button_palette 依赖的字段/方法。"""
    app = M.App.__new__(M.App)
    app.store = SettingsStore(tmp_path / "settings.json")
    app.store.load()
    app.button_palette = normalize_button_palette(app.store.get("button_palette"))
    app.theme, app.zoom, app.dpi = "light", 1.0, 96
    app.t = Tokens("light", 1.0, 96, app.button_palette)
    app.page = SimpleNamespace(controls=[], update=lambda: None)
    app.persist = app.store.save
    app.status_bar = SimpleNamespace(set_meta=lambda meta: None)
    app.mark = lambda *args, **kwargs: None
    app.toast = lambda message: None
    return app


def _bg(button: ft.FilledButton) -> str:
    return button.style.bgcolor[ft.ControlState.DEFAULT]


def _walk(control):
    yield control
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ft.Control):
                    yield from _walk(item)
        elif isinstance(value, ft.Control):
            yield from _walk(value)


def test_store_c_makes_main_button_use_slate_palette(tmp_path):
    app = _app(tmp_path)
    app.store.set("button_palette", "c")
    app.store.save()
    # 与 App.__init__ 相同的读回路径：坏值/缺键都归一到 c。
    app.button_palette = normalize_button_palette(app.store.get("button_palette"))
    app.t = Tokens("light", 1.0, 96, app.button_palette)

    assert app.button_palette == "c"
    assert app.t.buttons.fill == "#3D5A80"
    assert _bg(C.primary_button("运行", None, app.t)) == "#3D5A80"
    assert _bg(C.primary_button("运行", None, Tokens("light", 1.0, 96, "d"))) == "#1C1C1E"


def test_switch_to_d_recolors_button_and_persists(tmp_path):
    app = _app(tmp_path)
    assert app.button_palette == "c"
    app.set_button_palette("d")

    assert app.button_palette == "d"
    assert app.t.buttons.fill == "#1C1C1E"
    assert app.t.buttons.hover == "#3A3A3C"
    assert _bg(C.primary_button("运行", None, app.t)) == "#1C1C1E"

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["flet"]["button_palette"] == "d"
    # 非法值仍回落 C，不会把设置写坏。
    assert normalize_button_palette("D") == "d"
    assert normalize_button_palette("x") == "c"


def test_appearance_card_has_c_d_radios_and_uses_set_plus_save(tmp_path):
    app = _app(tmp_path)
    page = SimpleNamespace(app=app, t=app.t)
    row = SHELL.button_palette_row(page)

    group = next(ctl for ctl in _walk(row) if isinstance(ctl, ft.RadioGroup))
    radios = [ctl for ctl in group.content.controls if isinstance(ctl, ft.Radio)]
    assert [(radio.value, radio.label) for radio in radios] == [
        ("c", "C · 石板蓝"), ("d", "D · 石墨")]
    assert group.value == "c"

    group.on_change(SimpleNamespace(control=SimpleNamespace(value="d")))
    assert app.button_palette == "d"
    assert app.t.buttons.fill == "#1C1C1E"
    saved = json.loads((app.store.path).read_text(encoding="utf-8"))
    assert saved["flet"]["button_palette"] == "d"


def test_button_palette_row_lives_in_appearance_card_not_advanced():
    build_src = inspect.getsource(P.SettingsPage.build)
    assert "SHELL.button_palette_row(self)" in build_src
    # 外观组内、且在外观组结束（tray）之前。
    # 批 115 P3-C2：设置页改 S1m-native，外观从 `C.card` 换成发丝线分组
    # `C.block`（settings 页不再有卡片底），分组变量名不变。
    start = build_src.index("appearance = C.block(")
    tray = build_src.index("tray = SHELL.tray_card(self)")
    assert start < build_src.index("SHELL.button_palette_row(self)") < tray
    assert "button_palette" not in inspect.getsource(SHELL.advanced_card)
