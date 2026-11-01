"""批 112 S3a2：规范化面板迁入「引用库」后的宿主契约回归。

覆盖 impl-plan §2.7 的 7–10、12：SinglePage 删净 checkbib 面、open_bib_view 顺序、
NormalizeView 宿主转发 / 剪贴板服务、T4 两处 bug（`page.root.update`、剪贴板读取）、
P2 钩子源码契约。全部不建窗口、不跑引擎。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

import main as M  # noqa: E402
from services import engine_bridge as EB  # noqa: E402
from ui import normalize as NR  # noqa: E402
from ui import pages as P  # noqa: E402

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")


# ── 假壳：只实现被测宿主面，不建控件、不碰事件循环 ───────────────────────
class _FakeStore:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.recent = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def set_recent(self, key, path):
        self.recent[key] = path


class _FakeApp:
    def __init__(self, values=None):
        self.store = _FakeStore(values)
        self.running = False
        self.toasts = []
        self.marked = []

    def toast(self, message):
        self.toasts.append(message)

    def mark(self, event, **fields):
        self.marked.append((event, fields))

    def open_path(self, value):
        pass

    def copy_text(self, value):
        pass

    def stop_run(self):
        self.running = False


class _FakeFletPage:
    def __init__(self):
        self.tasks = []
        self.services = []
        self.updated = 0

    def update(self):
        self.updated += 1

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))


class _FakeHost:
    """BibPage 替身。"""

    def __init__(self, values=None):
        self.t = None
        self.app = _FakeApp(values)
        self.page = _FakeFletPage()
        self.fields = {"bib_returned": ""}
        self.root = None
        self.marked = []
        self.refreshed = 0

    def field(self, key):
        return self.fields.get(key, "")

    def set_field(self, key, value):
        self.fields[key] = value

    def add_picker(self, *args, **kwargs):
        pass

    def mark(self, event, **fields):
        self.marked.append((event, fields))

    def refresh(self):
        self.refreshed += 1

    def toast(self, message):
        self.app.toast(message)


def _view(values=None):
    host = _FakeHost(values)
    return NR.NormalizeView(host), host


# ── 8. open_bib_view：先 goto 再 set_view，同页也必切 ─────────────────────
def test_open_bib_view_goes_to_bib_then_sets_view():
    calls = []

    class _Bib:
        def set_view(self, view, *, source):
            calls.append(("set_view", view, source))

    class _App:
        pages = {"bib": _Bib()}

        def goto(self, key):
            calls.append(("goto", key))

    M.App.open_bib_view(_App(), "normalize", source="single")
    assert calls == [("goto", "bib"), ("set_view", "normalize", "single")]


def test_open_bib_view_keeps_working_when_goto_early_returns():
    calls = []

    class _Bib:
        def set_view(self, view, *, source):
            calls.append(("set_view", view, source))

    class _App:
        pages = {"bib": _Bib()}

        def goto(self, key):
            calls.append(("goto", key))  # 已在 bib：真实 goto 会 return，不影响下面

    M.App.open_bib_view(_App(), "normalize", source="single")
    assert ("set_view", "normalize", "single") in calls


def test_open_bib_view_without_bib_page_is_noop():
    calls = []

    class _App:
        pages = {}

        def goto(self, key):
            calls.append(key)

    M.App.open_bib_view(_App(), "normalize", source="single")
    assert calls == []


# ── 9. NormalizeView 宿主契约 ─────────────────────────────────────────────
def test_normalize_view_forwards_host_contract():
    view, host = _view()
    assert view.t is host.t
    assert view.app is host.app
    assert view.page is host.page
    assert view.fields is host.fields  # 与 BibPage 共享同一 dict（字段不搬家）
    view.set_field("bib_original", "a.bib")
    assert host.fields["bib_original"] == "a.bib"
    view.toast("提示")
    assert host.app.toasts == ["提示"]
    view.mark("probe", ok=True)
    assert host.marked[-1] == ("probe", {"ok": True})


def test_normalize_view_mounts_flet_clipboard_service_once():
    view, host = _view()
    view._mount_clipboard()
    assert isinstance(view._clipboard, ft.Clipboard)
    assert view._clipboard in host.page.services
    view._mount_clipboard()
    assert host.page.services.count(view._clipboard) == 1  # 幂等，不重复挂服务


def test_normalize_view_clipboard_reads_via_service():
    class _Service:
        async def get(self):
            return "粘贴正文"

    view, host = _view()
    host.page.run_task = lambda handler, *args: asyncio.run(handler(*args))
    view._clipboard = _Service()
    seen = []
    view.clipboard(seen.append)
    assert seen == ["粘贴正文"]


def test_normalize_view_clipboard_missing_returns_empty():
    view, _ = _view()
    seen = []
    view.clipboard(seen.append)
    assert seen == [""]  # 无 GUI/不支持时回空串，面板给中文提示


def test_normalize_view_refresh_uses_view_root_not_root_update():
    view, host = _view()

    class _Root:
        updated = 0

        def update(self):
            _Root.updated += 1

    root = _Root()
    view._root = root
    view.refresh()  # 宿主 BibPage 没有 root.update 这条旧路径
    assert _Root.updated == 1


# ── T4 两处 bug：剪贴板读取不再假定同步字符串 ─────────────────────────────
class _PanelPage:
    """NormalizePanel 的宿主替身（真 NormalizeView 太重，这里只测面板回调）。"""

    def __init__(self, text="缺失"):
        self.app = _FakeApp()
        self.refreshed = 0
        if text != "缺失":
            self.clipboard = lambda on_text: on_text(text)

    def toast(self, message):
        self.app.toasts.append(message)

    def refresh(self):
        self.refreshed += 1


def _panel(text="缺失"):
    page = _PanelPage(text)
    panel = NR.NormalizePanel(page)
    panel._render_returned = lambda: None  # 控件未构建：只测数据流
    panel._refresh_mode_ui = lambda: None
    return panel, page


def test_panel_paste_clipboard_missing_shows_manual_hint():
    panel, page = _panel()
    panel._on_paste_clipboard()
    assert any("手动粘贴" in message for message in page.app.toasts)


def test_panel_paste_clipboard_empty_text_keeps_previous_value():
    panel, page = _panel("")
    page.app.store.set(NR.NormalizePanel.PASTE_KEY, "旧正文")
    panel._on_paste_clipboard()
    assert page.app.store.get(NR.NormalizePanel.PASTE_KEY) == "旧正文"
    assert any("没有可导入的文本" in message for message in page.app.toasts)


def test_panel_paste_clipboard_fills_paste_box_and_toasts():
    panel, page = _panel("  @article{a}  ")
    panel._on_paste_clipboard()
    assert page.app.store.get(NR.NormalizePanel.PASTE_KEY) == "@article{a}"
    assert panel._preview_line == f"{len('@article{a}')} 字符"
    assert any("已粘贴剪贴板文本" in message for message in page.app.toasts)


# ── 12. _artifact_recent_key / SinglePage 残留面 ──────────────────────────
def test_artifact_recent_key_has_no_checkbib():
    page = object.__new__(P.SinglePage)
    for flow in ("convert", "proofcheck"):
        page.flow_key = flow
        assert page._artifact_recent_key() in ("single_out", "proof_tex")
    assert set(P.SinglePage.FLOWS) == {"convert", "proofcheck"}
    assert P.SinglePage.FLOWS["proofcheck"]["label"] == "一校核查"
    assert not hasattr(P.SinglePage, "_build_checkbib")
    assert not hasattr(P.SinglePage, "_echo_checkbib_report")
    # 验收硬指标：规范化迁走后 pages.py 不再出现 checkbib 字样。
    assert "checkbib" not in open(P.__file__, encoding="utf-8").read()


# ── 10. P2 钩子源码契约（真窗口冒烟在 evidence/smoke 里跑） ───────────────
def test_p2_hooks_are_wired_in_source():
    source = open(os.path.join(APP_DIR, "main.py"), encoding="utf-8").read()
    assert "P2_BIB_VIEW(view) / P2_SINGLE_FLOW(convert|proofcheck) / P2_AUTORUN_LEGACY=1" in source
    assert "def open_bib_view(self, view: str, *, source: str" in source
    assert "bib_page.set_view(view, source=source)" in source
    assert 'self.mark("bib_view_hook", view=bib_view, source="env")' in source
    assert 'source="env_fallback"' in source  # 非法视图回落概况并留证据
    assert 'self.mark("single_flow_hook", flow=single_flow, source="env")' in source
    assert 'legacy_checkbib = _env_str("P2_AUTORUN_LEGACY")' in source
    assert "await self._autorun_normalize()" in source
    assert "normalize_redirect" in source  # 无输入时不再空等 _wait_run_finished


def test_run_call_accepts_stdin_text():
    import inspect
    params = inspect.signature(EB.run_call).parameters
    assert "stdin_text" in params and params["stdin_text"].default is None
