"""FIX-20261112 Q11（粘贴按钮）：组件级契约 + 10 个接入点。

真实 GUI 不可用（pytest 无窗口），这里用假 page / FakeControl 级断言：
* `_FakePage.run_task` 只收协程函数，测试里 `asyncio.run` 执行；
* `flet.Clipboard` 在测试内换成 `_FakeClipboard`（components 的 `ft` 是同一模块对象）；
* 每个接入点只构建「那一行/那一栏」控件树，点按钮后断言 value + 原 on_change 的落地状态。
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import flet as ft
import pytest

from ui import components as C
from ui import bib_query as QV
from ui import bib_advanced as AV
from ui import bib_edit as EV
from ui import bib_dupes as DV
from ui import bib_review as RV
from ui import bib_fieldfix as FV
from ui import bib_import as IV
from ui import bib_write as WV
from ui.tokens import Tokens


# ── 假剪贴板 / 假 page ─────────────────────────────────────────────────
_CLIP: dict = {"text": "", "error": None}


class _FakeClipboard:
    """替身 ft.Clipboard：get() 是协程，内容由 _CLIP 控制。"""

    def __init__(self) -> None:
        self.calls = 0

    async def get(self) -> str:
        self.calls += 1
        if _CLIP["error"] is not None:
            raise _CLIP["error"]
        return _CLIP["text"]


class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs: list = []
        self.tasks: list = []
        self.services: list | None = []
        self.width = 1160
        self.height = 820
        self.updates = 0

    def run_task(self, fn) -> None:
        assert inspect.iscoroutinefunction(fn), "run_task 必须收协程函数"
        self.tasks.append(fn)

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def update(self) -> None:
        self.updates += 1


class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _Host:
    """最小 BibPage 宿主：视图只用到 t / page(flet) / app(store,toast) / stats。"""

    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.toasts: list[str] = []
        self.app = SimpleNamespace(
            store=_Store(), notes_repo="", window_w=1160,
            toast=lambda message: self.toasts.append(str(message)),
        )
        self.stats_data = {"records": 2, "db": "/tmp/lib/bibliography.jsonl"}

    def mark(self, event, **kwargs) -> None:
        pass

    def _render(self) -> None:
        pass


class _NoGuiPage:
    """没有 run_task / services 的假 page（模拟无 GUI 外壳）。"""


@pytest.fixture(autouse=True)
def _fake_clipboard(monkeypatch):
    _CLIP["text"] = ""
    _CLIP["error"] = None
    monkeypatch.setattr(ft, "Clipboard", _FakeClipboard)


def _run_tasks(page: _FakePage) -> None:
    tasks, page.tasks = page.tasks, []
    for fn in tasks:
        asyncio.run(fn())


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None and isinstance(content, ft.Control):
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    return out


def _paste_buttons(tree) -> list:
    return [node for node in _walk(tree) if hasattr(node, "_paste_field")]


def _click(button) -> None:
    assert callable(button.on_click)
    button.on_click(SimpleNamespace(control=button))


# ── 组件级 ──────────────────────────────────────────────────────────────
def test_paste_writes_value_and_fires_original_on_change() -> None:
    t = Tokens("light")
    host = _Host()
    changed: list = []

    def on_change(event) -> None:
        changed.append((event.control, event.control.value))

    field = C.text_field(t, value="", on_change=on_change)
    wrapper = field.on_change
    suffix = field.suffix
    updates: list = []
    field.update = lambda: updates.append(1)

    button = C.paste_button(t, host, field)
    assert button._paste_field is field
    assert button.visible is True
    assert suffix is field.suffix and suffix not in _walk(button)

    _CLIP["text"] = "  Fine1975\n"
    _click(button)
    assert len(host.page.tasks) == 1
    _run_tasks(host.page)

    assert field.value == "  Fine1975\n"
    assert field is button._paste_field                     # 同一控件，未重建
    assert field.suffix is suffix                           # ✕ 仍是原对象
    assert field.on_change is wrapper                       # 包装器未被换掉
    assert changed == [(field, "  Fine1975\n")]
    assert updates == [1]
    assert host.toasts == []


def test_paste_empty_clipboard_toasts_and_keeps_field() -> None:
    t = Tokens("light")
    host = _Host()
    calls: list = []
    field = C.text_field(t, value="旧值", on_change=lambda e: calls.append(e.control.value))
    button = C.paste_button(t, host, field)

    _CLIP["text"] = "   \n\t "
    _click(button)
    _run_tasks(host.page)

    assert field.value == "旧值"
    assert calls == []
    assert host.toasts == [C.PASTE_EMPTY_HINT]
    assert C.PASTE_EMPTY_HINT == "剪贴板里没有可粘贴的文本"


def test_paste_non_text_clipboard_toasts_empty(monkeypatch) -> None:
    t = Tokens("light")
    host = _Host()
    field = C.text_field(t, value="")
    button = C.paste_button(t, host, field)
    _CLIP["text"] = None  # type: ignore[assignment]

    _click(button)
    _run_tasks(host.page)
    assert field.value == ""
    assert host.toasts == [C.PASTE_EMPTY_HINT]


def test_paste_without_clipboard_service_degrades() -> None:
    t = Tokens("light")
    host = _Host()
    host.page.services = None  # 外壳没挂 Clipboard service
    field = C.text_field(t, value="")
    button = C.paste_button(t, host, field)

    _click(button)      # 不允许抛异常
    assert host.page.tasks == []
    assert field.value == ""
    assert host.toasts == [C.PASTE_UNAVAILABLE_HINT]
    assert "手动粘贴" in C.PASTE_UNAVAILABLE_HINT


def test_paste_without_gui_page_degrades() -> None:
    t = Tokens("light")
    host = _Host()
    host.page = _NoGuiPage()
    field = C.text_field(t, value="")
    button = C.paste_button(t, host, field)

    _click(button)      # 假 page 无 run_task：只提示、不抛
    assert field.value == ""
    assert host.toasts == [C.PASTE_UNAVAILABLE_HINT]


def test_paste_clipboard_read_error_degrades() -> None:
    t = Tokens("light")
    host = _Host()
    field = C.text_field(t, value="")
    button = C.paste_button(t, host, field)
    _CLIP["error"] = RuntimeError("no page session")

    _click(button)
    _run_tasks(host.page)
    assert field.value == ""
    assert host.toasts == [C.PASTE_UNAVAILABLE_HINT]


def test_paste_nonstandard_handler_falls_back_to_on_pasted() -> None:
    t = Tokens("light")
    host = _Host()
    pasted: list = []

    def bad_handler(event) -> None:      # 非标准：不读 value，直接抛
        raise TypeError("bad handler")

    field = ft.TextField(value="", on_change=bad_handler)
    button = C.paste_button(t, host, field, on_pasted=lambda item: pasted.append(item))
    _CLIP["text"] = "Key2024"

    _click(button)
    _run_tasks(host.page)

    assert field.value == "Key2024"
    assert pasted == [field]             # 非标准 handler 不吞入口径：交给 on_pasted
    assert host.toasts == []


def test_paste_does_not_touch_suffix_or_visibility_and_keeps_one_button() -> None:
    t = Tokens("light")
    host = _Host()
    field = C.text_field(t, value="")
    button = C.paste_button(t, host, field)
    assert button.content == "粘贴"
    assert button.icon is not None
    assert "剪贴板" in (button.tooltip or "")
    assert button.height == t.control_h()
    # 按钮固定存在，不靠 visible 翻转；也不进 TextField.suffix。
    assert button.visible is True
    assert field.suffix is field._clear_button
    assert button not in _walk(field.suffix)


# ── 10 个接入点：点按钮 → 原 on_change 生效 ─────────────────────────────
def _dup_pair() -> dict:
    return {"a": "Old2020", "b": "New2020", "title": "Some Title",
            "similarity": 0.95, "diff": 2, "year": "2020"}


def _cases():
    """(名字, 构建函数, 期望值函数)。构建函数返回 (view, tree)。"""

    def query(host):
        view = QV.QueryView(host)
        return view, view._filter()

    def advanced_basic(host):
        view = AV.BibQueryView(host)
        return view, view._basic_panel()

    def advanced_row(host):
        view = AV.BibQueryView(host)
        return view, view._adv_row(0, view.rows[0])

    def edit_key(host):
        view = EV.BibEditView(host)
        return view, view._key_row()

    def edit_props(host):
        view = EV.BibEditView(host)
        return view, view._props_row()

    def dupes_band(host):
        view = DV.BibDupesView(host)
        view.pairs = [_dup_pair()]
        view.selected = 0
        return view, view._plan_band()

    def review_filter(host):
        view = RV.ReviewView(host)
        return view, view._filter()

    def fieldfix_filter(host):
        view = FV.BibFieldFixView(host)
        return view, view._filter_row()

    def import_paste(host):
        view = IV.BibImportView(host)
        view.mode = "paste"
        view.params_open = True
        return view, view._params(narrow=False)

    def write_source(host):
        view = WV.BibImportView(host)
        view.mode = "text"
        return view, view._source_control()

    return [
        pytest.param("query", query, lambda v, text: v.text == text, id="query"),
        pytest.param("advanced-basic", advanced_basic,
                     lambda v, text: v.basic_text == text, id="advanced-basic"),
        pytest.param("advanced-row", advanced_row,
                     lambda v, text: v.rows[0]["value"] == text, id="advanced-row"),
        pytest.param("edit-key", edit_key, lambda v, text: v.key_input == text, id="edit-key"),
        pytest.param("edit-props", edit_props,
                     lambda v, text: v.new_key == text.strip(), id="edit-props"),
        pytest.param("dupes-plan", dupes_band,
                     lambda v, text: v.merged_key == text.strip(), id="dupes-plan"),
        pytest.param("review-filter", review_filter,
                     lambda v, text: v.text == text, id="review-filter"),
        pytest.param("fieldfix-filter", fieldfix_filter,
                     lambda v, text: v.query == text.strip(), id="fieldfix-filter"),
        pytest.param("import-paste", import_paste,
                     lambda v, text: v.paste_text == text, id="import-paste"),
        pytest.param("write-source", write_source,
                     lambda v, text: v.text_value == text, id="write-source"),
    ]


@pytest.mark.parametrize("name, build, expect", _cases())
def test_each_entry_point_pastes_and_triggers_original_change(name, build, expect) -> None:
    host = _Host()
    view, tree = build(host)
    buttons = _paste_buttons(tree)
    assert len(buttons) == 1, f"{name}: 粘贴按钮应固定存在且唯一"
    button = buttons[0]
    field = button._paste_field
    assert isinstance(field, ft.TextField)
    assert button.visible is True
    assert field not in _walk(button)

    text = f"  {name}-Key2024\n"
    _CLIP["text"] = text
    _click(button)
    _run_tasks(host.page)

    assert field.value == text, f"{name}: 字段值必须写入"
    assert expect(view, text), f"{name}: 原 on_change 必须生效"
    assert host.toasts == [], f"{name}: 成功路径不应 toast"


@pytest.mark.parametrize("name, build, expect", _cases())
def test_each_entry_point_empty_clipboard_toasts(name, build, expect) -> None:
    host = _Host()
    view, tree = build(host)
    button = _paste_buttons(tree)[0]
    field = button._paste_field
    before = field.value

    _CLIP["text"] = "  "
    _click(button)
    _run_tasks(host.page)

    assert field.value == before
    assert host.toasts == [C.PASTE_EMPTY_HINT]
