"""批 114 用户 m03131：全壳输入控件统一「清空」能力契约。

锁八组契约（全部离线，不建窗口、不跑引擎、不联网）：

1. `text_field` / `floating_field` / `form_field` 默认挂框内 ✕，空值隐藏、
   有值显示，且不改变输入框盒型高度。
2. 点击 ✕：值清空、error 清空、`on_clear` 被调用，并合成原 `on_change` 事件
   （store / 校验 / 过滤链路照常运行）。
3. `sync_clearable` 让程序化回填（浏览、set_field、恢复草稿）后 ✕ 状态正确。
4. 多行可编辑 TextField 同样挂 ✕（suffix 在多行上的可用性由真实 GUI 自检复核）。
5. 只读字段、`password + can_reveal_password` 字段不挂 ✕（suffix 位被 reveal 占用）。
6. `select_box(reset=True)`：默认值隐藏 ↻、非默认值显示、点击回默认并回调。
7. 搜索框 / 路径字段走同一套 helper（✕ 在框内，浏览按钮仍在框外）。
8. 静态防回退：页面禁止手工挂 suffix，禁止绕开 helper 裸建可编辑 TextField。
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import flet as ft

from ui import components as C
from ui import pages as P
from ui.tokens import Tokens

from test_b114_polish import _FakeApp, _build_single, _walk


APP_UI = pathlib.Path(__file__).resolve().parents[1] / "app" / "ui"


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


# ── 1. helper 默认挂 ✕，空值隐藏、不改变盒型 ────────────────────────────
def test_helpers_attach_clear_suffix_and_keep_box_height():
    t = _t()
    single = C.text_field(t, value="abc")
    assert isinstance(single.suffix, ft.IconButton)
    assert single.suffix.visible is True
    assert single.height == t.control_h()

    empty = C.text_field(t)
    assert empty.suffix.visible is False

    floating = C.floating_field(t, "作者", value="周礼全")
    assert isinstance(floating.suffix, ft.IconButton)
    assert floating.suffix.visible is True
    assert floating.height == t.px(48)

    form = C.form_field(t, "篇名", C.text_field(t, value="先先秦逻辑研究"))
    assert isinstance(form.suffix, ft.IconButton)
    assert form.suffix.visible is True


# ── 2. 点击 ✕ 清值 + 清 error + 触发 on_clear / 原 on_change ─────────────
def test_clear_click_clears_value_error_and_fires_callbacks():
    t = _t()
    calls: list[str] = []
    changes: list[object] = []

    def on_clear():
        calls.append("clear")

    def on_change(e):
        changes.append(e.control.value)

    field = C.text_field(t, value="137-152", on_change=on_change, on_clear=on_clear)
    field.error = "格式不对"
    field.suffix.on_click(None)

    assert field.value == ""
    assert field.error is None
    assert field.suffix.visible is False
    assert calls == ["clear"]
    assert changes == [""]        # 合成事件走原 on_change，store 链路不丢


# ── 3. 程序化回填后同步 ✕ 状态 ──────────────────────────────────────────
def test_sync_clearable_after_programmatic_fill():
    t = _t()
    field = C.text_field(t)
    assert field.suffix.visible is False
    field.value = "/Users/me/paper.tex"
    C.sync_clearable(field)
    assert field.suffix.visible is True
    field.value = ""
    C.sync_clearable(field)
    assert field.suffix.visible is False


# ── 4. 多行可编辑输入同样挂 ✕ ───────────────────────────────────────────
def test_multiline_editable_field_gets_clear_suffix():
    t = _t()
    field = C.text_field(t, multiline=True, min_lines=3, max_lines=5)
    assert isinstance(field.suffix, ft.IconButton)
    assert field.suffix.visible is False
    field.value = "两段中文摘要"
    C.sync_clearable(field)
    assert field.suffix.visible is True
    field.suffix.on_click(None)
    assert field.value == ""


# ── 5. 只读字段 / password+reveal 字段不挂 ✕ ────────────────────────────
def test_readonly_and_reveal_password_fields_have_no_clear():
    t = _t()
    readonly = C.text_field(t, value="只读内容", read_only=True)
    assert getattr(readonly, "suffix", None) is None
    assert getattr(readonly, "_clearable", None) is None

    secret = C.text_field(t, value="sk-xxx", password=True, can_reveal_password=True)
    assert getattr(secret, "suffix", None) is None
    assert getattr(secret, "_clearable", None) is None


# ── 6. select_box(reset=True)：↻ 默认隐藏、非默认显示、点击回默认 ───────
def test_select_box_reset_icon_returns_to_default():
    t = _t()
    resets: list[str] = []
    box = C.select_box(
        t, "语种", value_text="自动判断",
        options=[("", "自动判断"), ("zh", "中文")],
        reset=True, reset_text="自动判断",
        on_reset=lambda: resets.append("reset"),
    )
    reset = box._reset_button
    assert isinstance(reset, ft.IconButton)
    assert reset.icon == C.ic("RESTART_ALT")
    assert reset.visible is False
    assert box.content.height == t.px(48)   # ↻ 在框内，盒型仍是 48

    box._value_text.value = "中文"
    box._sync_reset()
    assert reset.visible is True
    reset.on_click(None)
    assert box._value_text.value == "自动判断"
    assert reset.visible is False
    assert resets == ["reset"]


def test_select_box_custom_callback_can_keep_own_display():
    t = _t()
    seen: list[tuple[str, str]] = []

    def on_select(key, label):
        seen.append((key, label))
        return False        # 调用方自行维护显示（如「自定义…」弹窗）

    box = C.select_box(t, "责编", value_text="张三",
                       options=[("zhang", "张三"), ("__custom__", "自定义…")],
                       on_select=on_select)
    box._choose("__custom__", "自定义…")
    assert seen == [("__custom__", "自定义…")]
    assert box._value_text.value == "张三"      # 未强行改成「自定义…」


# ── 7. 搜索框 / 路径字段走同一套 helper ─────────────────────────────────
def test_search_field_and_path_picker_use_clearable_helper():
    t = _t()
    search = C.search_field(t, value="对比")
    assert isinstance(search.suffix, ft.IconButton)
    assert search.suffix.visible is True
    search.suffix.on_click(None)
    assert search.value == ""

    page, _root = _build_single()
    picker = C.path_picker("输出根目录", "/tmp/out", "选择目录", page.t)
    field = picker.controls[0].controls[0]
    browse = picker.controls[0].controls[1]
    assert isinstance(field.suffix, ft.IconButton)
    assert field.suffix.visible is True
    assert isinstance(browse, ft.OutlinedButton)   # 浏览按钮在框外，不受 ✕ 影响


# ── 8. 全页：高级参数手工 suffix 已删除、多行摘要挂 ✕ ──────────────────
def test_single_page_advanced_fields_use_helper_clear_and_multiline_summary():
    page, root = _build_single()
    for key in P._ADVANCED_TEXT_KEYS:
        field = page.fields[key][0]
        assert isinstance(field.suffix, ft.IconButton)
        assert not hasattr(page, "_attach_adv_clear")     # 去重：不再手工挂
    multiline = [ctl for ctl in _walk(root)
                 if isinstance(ctl, ft.TextField) and ctl.multiline]
    assert multiline, "单篇页应有多行可编辑输入（摘要）"
    assert all(isinstance(ctl.suffix, ft.IconButton) for ctl in multiline
               if not ctl.read_only)


# ── 9. 静态防回退：页面禁止手工 suffix / 绕开 helper 裸建输入框 ─────────
def test_static_contracts_for_clearable_helper():
    components_src = (APP_UI / "components.py").read_text(encoding="utf-8")
    pages_src = (APP_UI / "pages.py").read_text(encoding="utf-8")
    main_src = (APP_UI.parent / "main.py").read_text(encoding="utf-8")

    # helper 内统一实现；三个入口都调用
    assert "def make_clearable(" in components_src
    for fn in ("def text_field(", "def floating_field(", "def form_field("):
        chunk = components_src.split(fn, 1)[1]
        body = chunk.split("\ndef ", 1)[0]
        assert "make_clearable(" in body, f"{fn} 未走统一清空 helper"

    # 页面不再手工挂 suffix / 手工 ↻
    assert "_attach_adv_clear" not in pages_src
    assert "control.suffix = button" not in pages_src
    assert "_adv_reset_button" not in pages_src
    # 搜索框 / 浏览回填 / set_field 都同步 ✕
    assert "C.sync_clearable(search)" in main_src
    assert "C.sync_clearable(ref[0])" in main_src

    # 除 components 外禁止裸建可编辑 TextField（只读/密码例外见审计表）
    for path in APP_UI.glob("*.py"):
        if path.name == "components.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        raw = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and getattr(node.func.value, "id", "") == "ft"
               and node.func.attr in ("TextField", "Dropdown")]
        assert not raw, f"{path.name} 出现绕过 helper 的表单控件"
