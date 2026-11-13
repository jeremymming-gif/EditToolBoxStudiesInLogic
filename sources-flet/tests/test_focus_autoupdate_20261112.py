"""APP-FOCUS-20261112 · Q1/Q2/Q3 契约测试（不启窗口、不碰真库、不发网络）。

背景：flet 1.0.5 每个事件处理完，若本事件没调用过 `update()`，框架会从事发控件
上溯到第一个 isolated 控件（产品里是 Page）补一次**整页 patch**；整页 patch 里含正在
聚焦的 TextField 时客户端重建该框 → 输入首字母后失焦（本卡根因）。

本文件锁住修复后的四条契约：
- Q1 键入路径只定向更新 ✕ 按钮，聚焦中的 TextField 一律不进 patch；tooltip 延后到
  失焦 flush（R01）；程序化回填（update=True）行为不变。
- Q2 `settings_shell.on_key` 未命中热键时抑制本事件自动 patch；命中热键仍照旧分发。
- Q3 `_coalesced_update` 合并发送（多次调用只发一次）并抑制本事件自动 patch；
  换肤 rebuild 路径暂缓 `rebuild_shell()` 的内联发送、统一走合并发送，保证「换肤 = 1 次 patch」。
- 抑制通道失败时退回旧行为：只计数、不抛异常，界面照旧可用。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui import components as C
from ui import settings_shell as S
from ui.pages import SinglePage
from ui.tokens import Tokens


# ── 测试替身 ────────────────────────────────────────────────────────────────
class _FakeControl:
    """只记录 update() 次数的控件替身（不碰 flet 会话）。"""

    def __init__(self, **kwargs) -> None:
        self.update_calls = 0
        for key, value in kwargs.items():
            setattr(self, key, value)

    def update(self) -> None:
        self.update_calls += 1


class _FakeField(_FakeControl):
    def __init__(self, value: str = "", **kwargs) -> None:
        super().__init__(value=value, **kwargs)
        self.tooltip = None
        self.on_change = None
        self.on_blur = None
        self.read_only = False
        self.password = False
        self.can_reveal_password = False
        self.suffix_icon = None


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self) -> None:
        self.update_calls = 0
        self.tasks = []
        self.controls = []
        self.theme_mode = None
        self.bgcolor = None

    def update(self, *controls) -> None:
        self.update_calls += 1

    def run_task(self, fn, *args) -> None:
        self.tasks.append(fn if not args else (lambda: fn(*args)))


class _FakeStatusBar:
    def set_meta(self, meta) -> None:
        pass


class _FakeApp:
    """App 替身：够 Q2/Q3/set_theme 的真身方法用。"""

    def __init__(self) -> None:
        self.page = _FakePage()
        self.store = _FakeStore()
        self.marks = []
        self.theme = "light"
        self.t = Tokens("light")
        self.zoom = 1.0
        self.dpi = 96
        self.button_palette = "c"
        self.running = False
        self._update_scheduled = False
        self._update_after = None
        self.status_bar = _FakeStatusBar()
        self.rebuild_calls = 0
        self.coalesced_calls = 0
        self.retint_calls = 0

    def mark(self, event, **fields) -> None:
        self.marks.append((event, fields))

    def _status_meta(self):
        return {}

    def _apply_page_theme(self) -> None:
        pass

    def apply_titlebar(self) -> None:
        pass

    def persist(self) -> bool:
        return False

    def toast(self, message: str) -> None:
        pass

    def rebuild_shell(self) -> None:  # 真身会整页发送一次（被 set_theme 暂缓时除外）
        self.rebuild_calls += 1
        if not getattr(self, "_shell_update_deferred", False):
            self.page.update()

    def _rebuild_shell_deferred(self) -> None:
        self._shell_update_deferred = True
        try:
            self.rebuild_shell()
        finally:
            self._shell_update_deferred = False

    def _retint_shell(self, old) -> int:
        self.retint_calls += 1
        return 0

    def _coalesced_update(self, *, after=None) -> None:  # 计数替身
        self.coalesced_calls += 1
        self.page.update()


@pytest.fixture(autouse=True)
def _isolate_update_state():
    """每个用例前后都清掉 flet 的「本事件已更新」标志与计数。"""
    ft.context.reset_update_called()
    snapshot = dict(C.AUTO_UPDATE_MARKS)
    C.AUTO_UPDATE_MARKS.update({"count": 0, "errors": 0, "last_error": ""})
    yield
    ft.context.reset_update_called()
    C.AUTO_UPDATE_MARKS.update(snapshot)


def _typed_field(value: str = "", *, value_tooltip: bool = False) -> _FakeField:
    field = _FakeField(value=value)
    field._value_tooltip = value_tooltip
    C.make_clearable(field, Tokens("light"))
    # make_clearable 挂的是真 ft.IconButton；换成计数替身，测试只关心「发了哪些 patch」。
    field._clear_button = _FakeControl(visible=bool(value.strip()))
    return field


def _type(field: _FakeField, value: str) -> None:
    """模拟一次键入：客户端值变化 → make_clearable 的 _wrapped 回调。"""
    field.value = value
    field.on_change(SimpleNamespace(control=field))


# ── Q1：键入路径只动 ✕，聚焦框不进 patch ──────────────────────────────────
def test_typing_patches_only_clear_button():
    field = _typed_field("")
    button = field._clear_button
    _type(field, "a")

    assert field.update_calls == 0, "聚焦中的 TextField 不许进 patch"
    assert button.update_calls == 1, "✕ 首字符后必须立刻上屏（定向 patch）"
    assert field._clearable_dirty is True
    assert ft.context.was_update_called() is True, "必须抑制框架补的整页 patch"


def test_typing_without_visible_change_still_suppresses_autopatch():
    field = _typed_field("a")
    button = field._clear_button
    _type(field, "ab")

    assert field.update_calls == 0
    assert button.update_calls == 0, "✕ 没变就不发按钮 patch（不增加噪声）"
    assert ft.context.was_update_called() is True


def test_backspace_to_empty_hides_clear_button():
    field = _typed_field("ab")
    button = field._clear_button
    _type(field, "")

    assert button.visible is False
    assert button.update_calls == 1
    assert field.update_calls == 0


def test_blur_flushes_field_and_value_tooltip_once():
    field = _typed_field("", value_tooltip=True)
    _type(field, "notes/a.tex")
    assert field.update_calls == 0 and field.tooltip == "notes/a.tex"  # 键入期间不发

    field.on_blur(SimpleNamespace(control=field))
    assert field.update_calls == 1, "失焦时一次 flush（R01 全文 tooltip）"
    assert field.tooltip == "notes/a.tex"
    assert field._clearable_dirty is False


def test_programmatic_sync_keeps_old_behavior():
    field = _typed_field("")
    field.value = "/tmp/out.pdf"
    C.sync_clearable(field)

    assert field.update_calls == 1
    assert field._clear_button.update_calls == 1
    assert field._clear_button.visible is True
    assert field._clearable_dirty is False


# ── FIX2R2-2 追加：失焦无变化不补整页 patch / 提交 handler 自己刷新 ──────────
def test_blur_without_changes_marks_instead_of_patching():
    field = _typed_field("abc")
    ft.context.reset_update_called()

    field.on_blur(SimpleNamespace(control=field))      # 聚焦→失焦，期间没改任何东西

    assert field.update_calls == 0 and field._clear_button.update_calls == 0
    assert ft.context.was_update_called() is True, "无变化失焦也必须抑制框架补的整页 patch"


def test_blur_with_changed_value_still_flushes_and_marks():
    field = _typed_field("", value_tooltip=True)
    _type(field, "a")
    button_calls = field._clear_button.update_calls    # 首字符那一次已发过
    ft.context.reset_update_called()

    field.on_blur(SimpleNamespace(control=field))      # 有变化：照旧一次 flush（字段 + ✕）

    assert field.update_calls == 1
    assert field._clear_button.update_calls == button_calls + 1
    assert ft.context.was_update_called() is True


def test_submit_handler_marks_event_after_callback():
    seen = []
    field = C.text_field(Tokens("light"), value="",
                         on_submit=lambda e: seen.append(e.control.value))
    field.value = "logicality"
    ft.context.reset_update_called()

    assert field.on_submit is not None, "有 on_submit 时必须包一层"
    field.on_submit(SimpleNamespace(control=field))

    assert seen == ["logicality"]
    assert ft.context.was_update_called() is True, "提交 handler 自己刷新，框架不再补整页 patch"


def test_text_field_without_submit_keeps_none_handler():
    field = C.text_field(Tokens("light"), value="")
    assert field.on_submit is None


# ── FIX2R2-3 K7：导航补丁前给落点字段打 autofocus ───────────────────────────
class _FocusControl(_FakeControl):
    def __init__(self) -> None:
        super().__init__(autofocus=False)

    def focus(self) -> None:
        pass


def test_prepare_nav_focus_marks_target_autofocus():
    target = _FocusControl()
    page = SimpleNamespace(key="single", focus_after_nav=lambda: target, fields={})
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.mark = lambda event, **fields: None

    assert M.App._prepare_nav_focus(app, page) is target
    assert target.autofocus is True, "落点字段必须在整页 patch 里带上 autofocus"


def test_prepare_nav_focus_cold_page_falls_back_to_first_field():
    first, second = _FocusControl(), _FocusControl()
    page = SimpleNamespace(key="single", focus_after_nav=lambda: None,
                           fields={"源稿件": [first], "输出根目录": [second]})
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.mark = lambda event, **fields: None

    assert M.App._prepare_nav_focus(app, page) is first
    assert first.autofocus is True
    assert second.autofocus is False, "只给第一个落点打标记"


def test_prepare_nav_focus_prefers_page_nav_target():
    preferred, fallback = _FocusControl(), _FocusControl()
    page = SimpleNamespace(
        key="single",
        nav_focus_target=lambda: preferred,
        focus_after_nav=lambda: fallback,
        fields={},
    )
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.mark = lambda event, **fields: None

    assert M.App._prepare_nav_focus(app, page) is preferred
    assert preferred.autofocus is True
    assert fallback.autofocus is False


def test_single_page_focus_prefers_source_path_over_advanced_volume():
    """K7：`fields` 按构建序登记，卷在源稿件之前；落点必须选源稿件。"""
    page = SinglePage.__new__(SinglePage)
    source, out, volume = _FocusControl(), _FocusControl(), _FocusControl()
    page.flow_key = "convert"
    page.fields = {
        "convert_vol": [volume],
        "single_source": [source],
        "single_out": [out],
    }

    assert page.nav_focus_target() is source
    assert source is not volume


def test_single_page_focus_falls_back_to_output_when_source_missing():
    page = SinglePage.__new__(SinglePage)
    out, volume = _FocusControl(), _FocusControl()
    page.flow_key = "convert"
    page.fields = {"convert_vol": [volume], "single_out": [out]}

    assert page.nav_focus_target() is out


def test_single_page_focus_uses_proof_tex_on_proofcheck():
    """F2-12：切到一校核查后，落点必须是本步骤的 proof_tex，不能复用旧源稿件。"""
    page = SinglePage.__new__(SinglePage)
    stale_source, proof_tex = _FocusControl(), _FocusControl()
    page.flow_key = "proofcheck"
    page.fields = {
        "single_source": [stale_source],
        "single_out": [stale_source],
        "proof_tex": [proof_tex],
    }

    assert page.nav_focus_target() is proof_tex
    assert page.nav_focus_target() is not stale_source


def test_single_page_focus_does_not_reuse_source_without_flow_target():
    """flow_key 未知时宁可不预聚焦，也不把上一个子步骤的旧控件当落点。"""
    page = SinglePage.__new__(SinglePage)
    stale_source = _FocusControl()
    page.flow_key = "unknown"
    page.fields = {"single_source": [stale_source]}

    assert page.nav_focus_target() is None


def test_focus_after_nav_schedules_focus_and_autofocus_release():
    target = _FocusControl()
    page = SimpleNamespace(key="single", focus_after_nav=lambda: target)
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))

    M.App._focus_after_nav(app, page, target=target)

    assert len(app.page.tasks) == 2, "focus + 释放 autofocus 各一个任务"
    assert app.marks and app.marks[0][0] == "nav_focus"
def test_focus_after_nav_schedules_swallowing_focus_for_stale_control():
    """F2-12 端到端：focus 目标已离树时，调度任务本身不抛，只落 marker。"""
    async def _fail():
        raise RuntimeError("Control must be added to the page first")

    target = SimpleNamespace(focus=_fail)
    page = SimpleNamespace(key="single", focus_after_nav=lambda: target)
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))

    M.App._focus_after_nav(app, page)
    assert len(app.page.tasks) == 2, "focus + 释放 autofocus"
    asyncio.run(app.page.tasks[0]())

    events = [event for event, _ in app.marks]
    assert events[0] == "nav_focus"
    assert events[-1] == "nav_focus_error"


def test_focus_target_safely_swallows_detached_control_error():
    """F2-12：控件已离树时 focus 抛错只落 marker，不再冒泡成未处理异常。"""
    async def _fail():
        raise RuntimeError("Control must be added to the page first")

    app = M.App.__new__(M.App)
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))

    asyncio.run(M.App._focus_target_safely(
        app, SimpleNamespace(focus=_fail), "single"))

    assert app.marks
    assert app.marks[-1][0] == "nav_focus_error"
    assert "Control must be added" in app.marks[-1][1]["error"]


# ── Q2：普通按键抑制自动 patch，热键照旧分发 ───────────────────────────────
def test_hotkey_miss_suppresses_autopatch():
    app = SimpleNamespace(search_key_handle=None, marks=[], mark=lambda e, **k: None)
    S.on_key(app, SimpleNamespace(key="a", ctrl=False, shift=False, alt=False))

    assert ft.context.was_update_called() is True


def test_search_key_consumed_also_suppresses_autopatch():
    consumed = []
    app = SimpleNamespace(search_key_handle=lambda event: consumed.append(event) or True,
                          mark=lambda e, **k: None)
    S.on_key(app, SimpleNamespace(key="Down", ctrl=False, shift=False, alt=False))

    assert len(consumed) == 1
    assert ft.context.was_update_called() is True


def test_hotkey_hit_still_dispatches():
    calls = []
    app = SimpleNamespace(theme="light", set_theme=lambda theme: calls.append(theme),
                          mark=lambda e, **k: None)
    S.on_key(app, SimpleNamespace(key="d", ctrl=True, shift=True, alt=False))

    assert calls == ["dark"], "ctrl+shift+D 语义不变"


# ── Q3：合并发送 + 抑制自动 patch ──────────────────────────────────────────
def _real_app() -> M.App:
    """真 App 实例（跳过 __init__）：让 _coalesced_update 用真身方法跑合并逻辑。"""
    app = M.App.__new__(M.App)
    app.page = _FakePage()
    app.store = _FakeStore()
    app._marker_path = None
    app.dpi_aware = False
    app._update_scheduled = False
    app._update_after = None
    return app


def test_coalesced_update_merges_and_suppresses():
    app = _real_app()
    for _ in range(3):
        app._coalesced_update()

    assert app.page.update_calls == 0, "窗口内不立即发送"
    assert len(app.page.tasks) == 1, "三次调用只排一次发送"
    assert ft.context.was_update_called() is True

    asyncio.run(app.page.tasks[0]())
    assert app.page.update_calls == 1, "合并窗口结束只发一次整页 patch"


def test_coalesced_update_keeps_last_after_hook():
    app = _real_app()
    done = []
    app._coalesced_update(after=lambda: done.append("first"))
    app._coalesced_update(after=lambda: done.append("last"))
    app._flush_page_update()

    assert done == ["last"], "多次排队以最后一次 after 为准"
    assert app.page.update_calls == 1


def test_set_theme_rebuild_defers_to_single_coalesced_send():
    app = _FakeApp()
    M.App.set_theme(app, "dark")

    assert app.rebuild_calls == 1
    assert app.coalesced_calls == 1, "重建路径也归合并发送，一次换肤只发一次"
    assert app.page.update_calls == 1, "rebuild_shell 的内联 patch 必须被暂缓"
    assert app.page.theme_mode == ft.ThemeMode.DARK


def test_set_theme_retint_sends_once():
    app = _FakeApp()
    app.page.controls = [object()]  # built=True → retint 路径
    M.App.set_theme(app, "dark")

    assert app.retint_calls == 1
    assert app.rebuild_calls == 0
    assert app.coalesced_calls == 1
    assert app.page.update_calls == 1


# ── 反弱化：抑制通道失败必须退回旧行为且不抛 ──────────────────────────────
def test_suppression_failure_falls_back(monkeypatch):
    def _boom() -> None:
        raise RuntimeError("no session")

    monkeypatch.setattr(ft.context, "mark_update_called", _boom)
    field = _typed_field("")
    _type(field, "a")

    assert C.AUTO_UPDATE_MARKS["errors"] == 1
    assert "no session" in C.AUTO_UPDATE_MARKS["last_error"]
    assert field._clear_button.update_calls == 1, "抑制失败仍要保证 ✕ 上屏"
    assert field._clearable_dirty is True

# ── FIX2R2-3：待处理 / bib 编辑的定向刷新契约 ─────────────────────────────
def test_review_typing_refreshes_only_results_not_filter() -> None:
    """键入只重建结果区：不触发宿主整页 _render（否则含输入框的过滤器被重建 → 丢焦点）。"""
    from ui import bib_review as RV

    class _Host:
        def __init__(self) -> None:
            self.t = Tokens("light")
            self.page = SimpleNamespace(width=1160, run_task=lambda fn: None,
                                        update=lambda *a: None)
            self.app = SimpleNamespace(notes_repo="", window_w=1160,
                                       copy_text=lambda text: None)
            self.stats_data = {"db": "/tmp/lib/bibliography.jsonl",
                               "conflicts": {"records": 0, "total": 0}}
            self.render_calls = 0

        def mark(self, event, **fields) -> None:
            pass

        def _render(self) -> None:
            self.render_calls += 1

        def set_view(self, view) -> None:
            pass

    host = _Host()
    view = RV.ReviewView(host)
    body_holder = _FakeControl(content=None)
    count_text = _FakeControl(value="")
    dock_text = _FakeControl(value="")
    view._body_holder = body_holder
    view._count_text = count_text
    view._dock_text = dock_text

    view._on_text(SimpleNamespace(control=SimpleNamespace(value="逻辑")))

    assert view.text == "逻辑"
    assert body_holder.update_calls == 1 and count_text.update_calls == 1
    assert dock_text.update_calls == 1
    assert host.render_calls == 0            # 不重建含输入框的过滤器 → 不丢焦点


def test_edit_touch_updates_holders_without_page_patch() -> None:
    """bib 编辑键入只定向刷新差异栏/确认条，不再发整页 patch（10 键 9 次的根因）。"""
    from ui import bib_edit as BE

    class _Page:
        def __init__(self) -> None:
            self.update_calls = 0

        def update(self) -> None:
            self.update_calls += 1

        def mark(self, *args, **kwargs) -> None:
            pass

    view = object.__new__(BE.BibEditView)
    view.page = _Page()
    view._diff_holder = _FakeControl(content=None)
    view._bottom_holder = _FakeControl(content=None)
    view._diff_col_body = lambda: ft.Text("diff")
    view._bottom = lambda: ft.Text("bottom")

    BE.BibEditView._touch(view)

    assert view._diff_holder.update_calls == 1
    assert view._bottom_holder.update_calls == 1
    assert view.page.update_calls == 0        # 整页 patch = 0
