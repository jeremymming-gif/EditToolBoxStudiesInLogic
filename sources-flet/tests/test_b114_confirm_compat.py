"""BUG-3-1 回归：确认对话框回调兼容零参与事件参两种写法。"""
from app.ui.components import _invoke_confirm


def test_confirm_callback_zero_arg():
    calls = []
    _invoke_confirm(lambda: calls.append("zero"))
    assert calls == ["zero"]


def test_confirm_callback_event_arg():
    calls = []
    _invoke_confirm(lambda e: calls.append(e))
    assert calls == [None]


def test_confirm_callback_bound_method_and_partial():
    class _Sink:
        def __init__(self):
            self.hits = 0

        def _on_ok(self):
            self.hits += 1

    sink = _Sink()
    _invoke_confirm(sink._on_ok)
    assert sink.hits == 1


def test_escape_closes_dialog_before_stop(monkeypatch):
    """BUG-4-1：对话框打开时 Escape 只关对话框，不进 app.stop_run。"""
    from ui import settings_shell as SHELL
    from ui import components as C
    from types import SimpleNamespace

    calls = {"pop": 0, "stop": 0, "mark": []}

    class _Page:
        def pop_dialog(self):
            calls["pop"] += 1

    class _App:
        page = _Page()

        def stop_run(self):
            calls["stop"] += 1

        def mark(self, event, **kwargs):
            calls["mark"].append((event, kwargs))

    C._open_dialogs.append(object())  # 模拟一个打开中的确认框
    try:
        SHELL.on_key(_App(), SimpleNamespace(key="Escape", ctrl=False, shift=False, alt=False))
        assert calls["pop"] == 1 and calls["stop"] == 0
        assert any(ev == "dialog_dismissed" for ev, _ in calls["mark"])
    finally:
        C._open_dialogs.clear()

    # 无对话框时 Escape 仍走 stop_run
    SHELL.on_key(_App(), SimpleNamespace(key="Escape", ctrl=False, shift=False, alt=False))
    assert calls["stop"] == 1
