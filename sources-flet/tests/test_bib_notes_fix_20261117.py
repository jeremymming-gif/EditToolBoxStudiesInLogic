"""F1 修复回归：bib 读取链（notes 透传 / 引擎原文上屏 / 引用库路径三级回退）。

三条各自对应一处真实装配缺陷，均由 GUI 实测 marker 复现：

- P2：``bib_host._load_stats`` 调 ``read_json_command`` 不带 notes，打包态下
  ``main.py`` 启动又不写 ``SLCN_NOTES``，引擎找不到笔记仓 → ``bibdb stats``
  rc=2 → 工具栏恒「运行中」，marker 里 ``bib_stats{ok:false,code:2}``。
- P5：引擎的 stderr 原文（「…但那里没有 ``cases/`` 目录」）被整段吞掉，失败
  横幅只剩「库概况读取失败」，用户无从判断是库坏了还是没找到笔记仓。
- P7：``_db()`` 只读 ``stats_data["db"]``，而真实 ``cmd_stats`` 输出的是
  ``store.stats()``（只有 ``path``），于是查重/归位建议在真实装配里拿不到库。

全部用替身，绝不启动窗口、绝不触发真实子进程。
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import flet as ft
import pytest

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_host as BH
from ui import bib_overview as OV
from ui.tokens import Tokens

#: 引擎真实 stderr 形状（bibdb_cli.cmd_stats 打 ``✗ {exc}``，paths.describe_notes
#: 补第二行；三行合成一段就是 GUI 里被吞掉的那段原文）。
ENGINE_STDERR = (
    "✗ 找不到私有笔记仓，无法确定引用库位置（默认放在 EditToolBox-notes/bib_db/）。\n"
    "  请设环境变量 SLCN_NOTES=<笔记仓路径>（该目录下要有 cases/ 子目录），"
    "或用 --db <库文件路径> 显式指定。\n"
    "  ⚠️ SLCN_NOTES 指向 /tmp/no-such-notes，但那里没有 `cases/` 目录。"
)


# ── 替身 ────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

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
            nxt = current.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        current[parts[-1]] = value


class FakePage:
    width = 1160

    def __init__(self) -> None:
        self.tasks: list = []
        self.updates = 0

    def run_task(self, task, *args, **kwargs) -> None:
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)

    def update(self) -> None:
        self.updates += 1


class FakeApp:
    def __init__(self, data: dict | None = None, *, notes_repo: str = "") -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.page = FakePage()
        self.notes_repo = notes_repo
        self.marks: list[tuple[str, dict]] = []
        self.badges: list[int] = []
        self.toolbar = None

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def toast(self, message: str) -> None:
        pass

    def navigate(self, route: str, *, source: str = "nav") -> None:
        pass

    def set_bib_badge(self, count) -> None:
        self.badges.append(int(count))

    def _sync_toolbar(self) -> None:
        pass


class FakeView:
    """宿主懒加载的最小替身（本文件只测 _load_stats，不关心页面构建）。"""

    def __init__(self, page) -> None:
        self.page = page

    def build(self):
        return ft.Container(content=ft.Text("leaf"), expand=True)

    def activate(self) -> None:
        pass

    def refresh_running(self) -> None:
        pass


class _OverviewHost:
    """概览页宿主替身（照 test_b116_bib_overview._Host，notes_repo 可参数化）。"""

    def __init__(self, *, notes_repo: str = "", stats_data: dict | None = None) -> None:
        self.t = Tokens("light")
        self.page = FakePage()
        self.app = SimpleNamespace(store=FakeStore(), notes_repo=notes_repo,
                                   window_w=1160, copy_text=lambda text: None)
        self.stats_data = stats_data if stats_data is not None else {
            "path": "/tmp/lib/bibliography.jsonl", "sha256": "8f21c0de1234abcd"}
        self.renders = 0
        self.marks: list[tuple[str, dict]] = []
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def _render(self) -> None:
        self.renders += 1

    def set_view(self, view) -> None:
        self.view = view


def _host(monkeypatch, *, notes_repo: str = ""):
    monkeypatch.setattr(BH, "load_view_class", lambda module, cls: FakeView)
    app = FakeApp({"bib_view": "overview"}, notes_repo=notes_repo)
    page = BH.BibHost(app)
    page._sync_toolbar_meta = lambda: None
    return page, app


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out: list[str] = []
    seen: set[int] = set()
    for node in _walk(control):
        if id(node) in seen:
            continue
        seen.add(id(node))
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        label = getattr(node, "content", None)
        if isinstance(label, str):
            out.append(label)
    return out


def _stats_payload() -> dict:
    return {"ok": True, "json": {"records": 3883, "aliases": {"total": 12},
                                 "conflicts": {"totals": {"pending": 5}}}}


def _lint_payload() -> dict:
    return {"ok": True, "json": {"counts": {"warnings": 76}}}


# ── P2：notes 透传 ───────────────────────────────────────────────────────
def test_load_stats_passes_notes_to_engine(monkeypatch) -> None:
    """notes_repo 非空时必须把 notes 传给 read_json_command（P2 主因）。"""
    page, app = _host(monkeypatch, notes_repo="/tmp/notes-repo")
    seen: list[object] = []

    def fake_read(call, *, timeout=120.0, notes="", env_extra=None):
        seen.append(notes)
        return _lint_payload() if "lint" in list(getattr(call, "argv", []) or []) \
            else _stats_payload()

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    asyncio.run(page._load_stats())

    assert seen, "引擎一次都没被调用"
    assert set(seen) == {"/tmp/notes-repo"}
    # 顺带确认没把统计链改坏：工具栏与徽标照旧。
    assert page.toolbar_meta() == "3,883 条 · 76 条警告"
    assert app.badges == [5]


def test_load_stats_omits_notes_when_unset(monkeypatch) -> None:
    """notes_repo 为空时不传 notes 关键字（兼容既有单参数替身，语义等价）。"""
    page, _app = _host(monkeypatch, notes_repo="")
    calls: list = []

    def fake_read(call):  # 单参数签名：多传一个关键字就会 TypeError
        calls.append(call)
        return _lint_payload() if "lint" in list(getattr(call, "argv", []) or []) \
            else _stats_payload()

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    asyncio.run(page._load_stats())

    assert len(calls) == 2
    assert page.stats_data["records"] == 3883


def test_notes_reads_notes_repo_from_app(monkeypatch) -> None:
    page, _app = _host(monkeypatch, notes_repo="/tmp/x")
    assert page._notes() == "/tmp/x"


# ── P5：引擎原文上屏 ─────────────────────────────────────────────────────
def _patch_overview(monkeypatch, *, fail: str = "stats", stderr: str = ENGINE_STDERR):
    names = {"stats": "stats", "conflicts": "conflicts", "lint": "lint",
             "dup": "dup", "sug": "sug"}
    calls = {key: SimpleNamespace(name=name) for key, name in names.items()}
    monkeypatch.setattr(EB, "bibdb_stats_call", lambda: calls["stats"])
    monkeypatch.setattr(EB, "bibdb_conflicts_call", lambda **kw: calls["conflicts"])
    monkeypatch.setattr(EB, "bibdb_lint_call", lambda **kw: calls["lint"])
    monkeypatch.setattr(EB, "bibdb_report_duplicates_call", lambda **kw: calls["dup"])
    monkeypatch.setattr(EB, "bibdb_suggest_fixes_call", lambda **kw: calls["sug"])
    payloads = {
        "stats": {"ok": True, "json": {"path": "/tmp/lib/bibliography.jsonl",
                                       "records": 3883, "sha256": "8f21c0de"},
                  "code": 0},
        "conflicts": {"ok": True, "json": {"totals": {"total": 0, "pending": 0}},
                      "code": 0},
        "lint": {"ok": True, "json": {"counts": {"warnings": 0}}, "code": 0},
        "dup": {"ok": True, "json": {"pairs": 0}, "code": 0},
        "sug": {"ok": True, "json": {"count": 0}, "code": 0},
    }
    if fail in names:
        payloads[fail] = {"ok": False, "json": {}, "code": 2, "lines": [],
                          "stderr": stderr, "error": stderr}

    def fake_read(call, *, timeout=120, notes="", env_extra=None):
        name = getattr(call, "name", "")
        return dict(payloads[name])

    monkeypatch.setattr(EB, "read_json_command", fake_read)


def _load_overview(host) -> None:
    asyncio.run(host.view_obj.load())


def test_overview_error_message_carries_engine_reason(monkeypatch) -> None:
    """失败摘要必须带引擎那句关键原因，而不是只有「库概况读取失败」。"""
    _patch_overview(monkeypatch, fail="stats")
    host = _OverviewHost()
    host.view_obj = OV.OverviewView(host)
    _load_overview(host)

    error = host.view_obj.error
    assert error.startswith("库概况读取失败")
    # 挑的是诊断句（「指向 X，但那里没有 cases/ 目录」），不是首行总述或通用指引。
    assert "但那里没有 `cases/` 目录" in error
    assert "✗ " not in error                      # 前缀已剥掉，读起来是句子
    assert host.view_obj.error_detail == ENGINE_STDERR


def test_overview_error_bar_renders_engine_stderr(monkeypatch) -> None:
    """失败横幅里要能看见完整引擎原文（可选中/可复制），含 cases/ 那句。"""
    _patch_overview(monkeypatch, fail="stats")
    host = _OverviewHost()
    host.view_obj = OV.OverviewView(host)
    _load_overview(host)

    tree = host.view_obj.build()
    texts = _texts(tree)
    joined = "\n".join(texts)
    assert "库概况读取失败" in joined
    assert "但那里没有 `cases/` 目录" in joined
    # stderr 区是独立控件，页面上可选中复制。
    assert any(getattr(node, "_bib_stderr", None) is not None for node in _walk(tree))


def test_overview_no_error_detail_when_no_engine_text(monkeypatch) -> None:
    """引擎没给原文时退回旧文案，且不硬塞空 stderr 区。"""
    _patch_overview(monkeypatch, fail="stats", stderr="")
    host = _OverviewHost()
    host.view_obj = OV.OverviewView(host)
    _load_overview(host)

    assert host.view_obj.error == "库概况读取失败"
    assert host.view_obj.error_detail == ""
    tree = host.view_obj.build()
    assert not any(getattr(node, "_bib_stderr", None) is not None for node in _walk(tree))


def test_host_marker_carries_engine_error(monkeypatch) -> None:
    """宿主 stats_error 不再是无人生成的死字段：失败原文进 bib_stats 标记。"""
    page, app = _host(monkeypatch, notes_repo="/tmp/notes-repo")

    def fake_read(call, **kwargs):
        if "lint" in list(getattr(call, "argv", []) or []):
            return {"ok": False, "json": {}, "code": 2, "error": "读取失败"}
        return {"ok": False, "json": {}, "code": 2, "stderr": ENGINE_STDERR}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    asyncio.run(page._load_stats())

    stats_marks = [fields for event, fields in app.marks if event == "bib_stats"]
    assert stats_marks and stats_marks[0]["ok"] is False
    assert "cases/" in str(stats_marks[0].get("error") or "")


# ── P7：引用库路径三级回退 ────────────────────────────────────────────────
def test_db_prefers_db_key(monkeypatch) -> None:
    host = _OverviewHost(stats_data={"db": "/tmp/x/bibliography.jsonl"})
    host.view_obj = OV.OverviewView(host)
    assert host.view_obj._db() == "/tmp/x/bibliography.jsonl"


def test_db_falls_back_to_path_key(monkeypatch) -> None:
    """真实引擎只给 path，没有 db 键（P7 的原始缺陷）。"""
    host = _OverviewHost(stats_data={"path": "/tmp/lib/bibliography.jsonl"})
    host.view_obj = OV.OverviewView(host)
    assert host.view_obj._db() == "/tmp/lib/bibliography.jsonl"


def test_db_falls_back_to_default_db(monkeypatch) -> None:
    host = _OverviewHost(stats_data={})
    host.view_obj = OV.OverviewView(host)
    seen: list[str] = []

    def fake_default(notes_repo: str = "") -> str:
        seen.append(notes_repo)
        return "/tmp/notes-repo/bib_db/bibliography.jsonl"

    monkeypatch.setattr(GIT, "default_db", fake_default)
    assert host.view_obj._db() == "/tmp/notes-repo/bib_db/bibliography.jsonl"
    assert seen == [""]


def test_db_returns_empty_when_nothing_configured(monkeypatch) -> None:
    host = _OverviewHost(stats_data={})
    host.view_obj = OV.OverviewView(host)
    monkeypatch.setattr(GIT, "default_db", lambda notes_repo="": "")
    assert host.view_obj._db() == ""
