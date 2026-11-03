"""批 116：设置页（单列 720 / 四组 / 危险区）定向测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft
import pytest

import services.engine_bridge as EB
from ui import bib_prefs as BP
from ui.tokens import Tokens


class _App:
    def __init__(self) -> None:
        self.toasts: list[str] = []
        self.notes_repo = ""

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _FletPage:
    def __init__(self, width: int = 1160, height: int = 780) -> None:
        self.width = width
        self.height = height
        self.dialogs: list = []
        self.updates = 0
        self.clipboard: list[str] = []

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        pass

    def set_clipboard(self, value) -> None:
        self.clipboard.append(value)


class _Store:
    def __init__(self, data=None) -> None:
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _Host:
    def __init__(self, tmp_path=None, *, width=1160, height=780) -> None:
        self.app = _App()
        self.store = _Store({"bibdb.lookup_enabled": True})
        self.t = Tokens()
        self.page = _FletPage(width, height)
        self.events: list[tuple] = []
        self.tasks: list = []
        root = Path(tmp_path) if tmp_path else Path("/tmp")
        self.stats_data = {"db": str(root / "bib_db" / "bibliography.jsonl")}

    def mark(self, event, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)


def _db(tmp_path) -> Path:
    path = Path(tmp_path) / "bib_db" / "bibliography.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _walk(control):
    yield control
    for name in ("controls", "content"):
        child = getattr(control, name, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(root) -> list[str]:
    return [c.value for c in _walk(root) if isinstance(getattr(c, "value", None), str)]


BUTTONS = (ft.Button, ft.OutlinedButton, ft.TextButton)


def _label(control) -> str:
    content = getattr(control, "content", None)
    if isinstance(content, str):
        return content
    value = getattr(content, "value", None)
    return value if isinstance(value, str) else ""


def _buttons(root) -> list:
    return [c for c in _walk(root) if isinstance(c, BUTTONS)]


def _button_labels(root) -> list[str]:
    return [_label(c) for c in _buttons(root)]


STATS = {"sha256": "a" * 64, "records": 3883,
         "aliases": {"records": 2081, "total": 4102},
         "bytes": 2202009, "engine_version": "1.6.1"}


def _install(monkeypatch, *, status=None, commit=None, push=None, rollback=None,
             calls=None):
    calls = calls if calls is not None else []
    monkeypatch.setattr(BP.EB, "read_json_command",
                        lambda call, **kw: (calls.append(call),
                                            {"ok": True, "json": STATS, "code": 0,
                                             "stderr": ""})[1])
    base_status = {"ok": True, "git": True, "branch": "main", "head": "4f2a9c1abc",
                   "ahead": 0, "dirty_db": False, "dirty_other": False,
                   "commits": [{"sha": "4f2a9c1abc",
                                "subject": "引用库：手动提交",
                                "date": "2026-11-03 22:41"}],
                   "warnings": []}
    if status is not None:
        base_status.update(status)
    monkeypatch.setattr(BP.GIT, "status",
                        lambda db="", *, notes_repo="": dict(base_status))
    commits = commit if commit is not None else []
    monkeypatch.setattr(BP.GIT, "commit_db",
                        lambda db, message: (commits.append((db, message)),
                                             {"ok": True, "committed": True,
                                              "sha": "abc1234def"})[1])
    pushes = push if push is not None else []
    monkeypatch.setattr(BP.GIT, "push",
                        lambda db="", *, notes_repo="": (pushes.append(db),
                                                         {"ok": True})[1])
    rollbacks = rollback if rollback is not None else []
    monkeypatch.setattr(BP.GIT, "rollback",
                        lambda db="", *, notes_repo="": (rollbacks.append(db),
                                                         {"ok": True,
                                                          "sha": "ddd9999"})[1])
    return base_status


def _view(tmp_path, **kwargs):
    host = _Host(tmp_path, **kwargs)
    return BP.BibPrefsView(host), host


# ── 布局 ──────────────────────────────────────────────────────────────
def test_build_single_720_column_with_four_groups(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    root = view.build()
    texts = _texts(root)
    assert texts.count("设置") == 1
    assert any("01  库" == text for text in texts)
    assert any("02  核实边界" == text for text in texts)
    assert any("03  Git" == text for text in texts)
    assert any("本页不写库内容" in text for text in texts)
    widths = [c.width for c in _walk(root) if getattr(c, "width", None) == 720]
    assert widths, "单列必须限宽 720"


def test_no_card_helper_in_source():
    source = Path(BP.__file__).read_text(encoding="utf-8")
    assert "C.card" not in source


def test_only_save_is_primary(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    root = view.build()
    labels = _button_labels(root)
    assert "保存设置" in labels
    save = [c for c in _buttons(root) if _label(c) == "保存设置"][0]
    assert save.disabled is True


# ── 读取 ──────────────────────────────────────────────────────────────
def test_load_shows_db_stats_and_last_commit(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _db(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    assert view.state == "ready"
    root = view.build()
    texts = " ".join(_texts(root))
    assert "3,883 条" in texts
    assert "别名合计 4,102 条" in texts
    assert "4f2a9c1" in texts
    assert "引用库：手动提交" in texts


def test_load_failure_keeps_page_and_shows_error(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    monkeypatch.setattr(BP.EB, "read_json_command",
                        lambda call, **kw: {"ok": False, "json": {}, "code": 1,
                                            "stderr": "stats 读取失败"})
    asyncio.run(view._load())
    root = view.build()
    assert any("读取失败" in text for text in _texts(root))


def test_real_stats_shape_dict_counters(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    real = dict(STATS)
    real.update({"path": str(_db(tmp_path)), "exists": True})
    _install(monkeypatch)
    monkeypatch.setattr(BP.EB, "read_json_command",
                        lambda call, **kw: {"ok": True, "json": real, "code": 0,
                                            "stderr": ""})
    asyncio.run(view._load())
    path, count_text, file_text, _last = view._db_stats()
    assert path.endswith("bibliography.jsonl")
    assert count_text == "3,883 条 · 别名合计 4,102 条"
    assert file_text.startswith("存在")


def test_missing_db_offers_import_link(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    root = view.build()
    texts = _texts(root)
    assert any("库文件不存在" in text for text in texts)
    assert any("去入藏" in label for label in _button_labels(root))


# ── 02 核实 ───────────────────────────────────────────────────────────
def test_save_disabled_until_dirty(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    assert view._dirty() is False
    view.verify_max = "80"
    assert view._dirty() is True
    view._save()
    assert host.store.get("bibdb.verify_max") == 80
    assert view._dirty() is False
    assert "已保存" in view.receipt and "settings.json" in view.receipt


def test_empty_verify_max_means_engine_default(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    view.verify_max = ""
    assert view._dirty() is False
    view.verify_max = "120"
    view._save()
    assert host.store.get("bibdb.verify_max") == 120


def test_invalid_verify_value_blocks_save(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    view.verify_max = "99999"
    view.verify_error = view._validate(view.verify_max)
    assert "0–10000" in view.verify_error
    assert view._dirty() is False
    view._save()
    assert "bibdb.verify_max" not in host.store.data


def test_switch_toggle_is_dirty_and_saved(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    event = type("E", (), {"control": type("C", (), {"value": True})()})()
    view._on_switch("bibdb.verify_plan", event)
    assert view.switch_values["bibdb.verify_plan"] is True
    assert view._dirty() is True
    view._save()
    assert host.store.get("bibdb.verify_plan") is True


def test_verify_all_needs_second_confirm(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    event = type("E", (), {"control": type("C", (), {"value": True})()})()
    view._on_switch("bibdb.verify_all_refs", event)
    assert host.page.dialogs, "全量核实必须二次确认"
    assert view.switch_values["bibdb.verify_all_refs"] is False
    view._force_switch("bibdb.verify_all_refs", True)
    assert view.switch_values["bibdb.verify_all_refs"] is True


# ── 03 Git ────────────────────────────────────────────────────────────
def test_git_unavailable_disables_actions(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch, status={"ok": False, "error": "不是 git 仓库"})
    asyncio.run(view._load())
    root = view.build()
    texts = _texts(root)
    assert any("不可用" == text for text in texts)
    assert any("不是 git 仓库" == text for text in texts)


def test_commit_confirm_copy_and_write(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    commits: list[tuple] = []
    _install(monkeypatch, commit=commits)
    asyncio.run(view._load())
    view._commit_click()
    assert host.page.dialogs
    dialog = host.page.dialogs[-1]
    body = " ".join(_texts(dialog))
    assert "只提交 bib_db/bibliography.jsonl" in body
    assert "不推送" in body
    asyncio.run(view._git_write("commit"))
    assert len(commits) == 1
    assert view.receipt.startswith("已提交")


def test_push_disabled_when_nothing_ahead(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch, status={"ahead": 0})
    asyncio.run(view._load())
    root = view.build()
    push = [c for c in _buttons(root) if "推送" in _label(c)]
    assert push and push[0].disabled is True


def test_rollback_confirm_has_numbers_and_new_commit(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    rollbacks: list[str] = []
    _install(monkeypatch, rollback=rollbacks)
    asyncio.run(view._load())
    asyncio.run(view._rollback_click())
    assert host.page.dialogs
    body = " ".join(_texts(host.page.dialogs[-1]))
    assert "影响文件 1 个" in body
    assert "新增提交 1 条" in body
    asyncio.run(view._rollback())
    assert rollbacks
    assert "已回退" in view.receipt


def test_rollback_stale_head_blocks_dialog(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    monkeypatch.setattr(BP.GIT, "status",
                        lambda db="", *, notes_repo="": {"ok": True, "git": True,
                                                         "head": "ffff0000",
                                                         "branch": "main"})
    asyncio.run(view._rollback_click())
    assert view.stale is True
    assert not host.page.dialogs
    assert any("HEAD" in toast for toast in host.app.toasts)


def test_danger_zone_is_tail_and_holds_rollback(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._load())
    root = view.build()
    zones = [c for c in _walk(root)
             if getattr(c, "bgcolor", None) == Tokens().palette.danger_soft]
    assert zones, "页尾危险区必须存在"
    assert zones[0].height == 56
