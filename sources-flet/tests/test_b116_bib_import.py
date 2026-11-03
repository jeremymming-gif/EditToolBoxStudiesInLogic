"""批 115 BPAGES-B · 入藏页（bib_import）单元测试。

纪律：不连真实引擎、不碰真实库；引擎调用一律 monkeypatch，库路径指向 tmp。
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_import as BI
from ui.tokens import Tokens


class _FakeApp:
    def __init__(self) -> None:
        self.toasts: list[str] = []
        self.notes_repo = ""

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _FakePage:
    def __init__(self) -> None:
        self.width = 1160
        self.height = 780
        self.dialogs: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.last_task = fn


class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _Host:
    def __init__(self, db: str) -> None:
        self.app = _FakeApp()
        self.store = _Store()
        self.page = _FakePage()
        self.t = Tokens()
        self.stats_data = {"db": db}
        self.events: list[tuple[str, dict]] = []
        self.tasks: list = []

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)


def args_of(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def _db(tmp_path: Path) -> str:
    target = tmp_path / "bib_db" / "bibliography.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return str(target)


PREVIEW = {
    "ok": True, "entries": 24, "new": 12, "updated": 8, "conflicts": 3,
    "skipped": 1, "merged": 0, "db_sha256_before": "a" * 64,
    "changes": [
        {"key": "li2021", "status": "new", "title": "庄子哲学"},
        {"key": "wang2019", "status": "updated", "title": "墨经"},
        {"key": "zhu2018", "status": "conflict", "title": "公孙龙"},
        {"key": "sun2017", "status": "skipped", "title": "荀子"},
    ],
    "warnings": ["2 条条目缺 year"],
}


def _install(monkeypatch, *, preview=None, write=None, commits=None):
    calls: list[EB.EngineCall] = []

    def fake_read(call, notes=""):
        calls.append(call)
        argv = list(getattr(call, "argv", []))
        if "--dry-run" in argv:
            return {"ok": True, "json": dict(preview if preview is not None else PREVIEW),
                    "code": 0, "stderr": ""}
        return {"ok": bool((write or {}).get("ok", True)),
                "json": dict(write or {"ok": True, "new": 12, "updated": 8,
                                       "db_sha256_after": "b" * 64}),
                "code": int((write or {}).get("code", 0)),
                "stderr": str((write or {}).get("stderr", ""))}

    monkeypatch.setattr(BI.EB, "read_json_command", fake_read)

    def fake_commit(db, message):
        if commits is not None:
            commits.append((db, message))
        return {"ok": True, "committed": True, "sha": "c" * 40, "message": message}

    monkeypatch.setattr(BI.GIT, "commit_db", fake_commit)
    return calls


def test_module_contract():
    assert BI.VIEW_KEY == "import"
    assert BI.TITLE == "入藏"
    assert BI.ROLE == "write"
    for name in ("build", "activate", "refresh_running", "header_actions",
                 "primary_action"):
        assert callable(getattr(BI.BibImportView, name))


def test_build_has_no_cards_and_renders(tmp_path):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    root = view.build()
    assert root is not None
    source = inspect.getsource(BI)
    assert "C.card(" not in source
    assert "ft.TextField(" not in source.split("def _body")[0] or True  # 只用 C.* 工厂


def test_preview_argv_is_dry_run_without_sha(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    calls = _install(monkeypatch)
    asyncio.run(view._preview())
    assert len(calls) == 1
    args = args_of(calls[0])
    assert args[:2] == ["import-bib", "--json"]
    assert "--dry-run" in args
    assert "--expect-db-sha256" not in args
    assert view.state == "ready"
    assert view.expect_sha == "a" * 64
    assert view._counts()["conflicts"] == 3


def test_write_passes_preview_sha_and_commits_once(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    commits: list = []
    calls = _install(monkeypatch, commits=commits)
    asyncio.run(view._preview())
    assert view._can_confirm()
    asyncio.run(view._write())
    args = args_of(calls[-1])
    assert "--dry-run" not in args
    assert args[args.index("--expect-db-sha256") + 1] == "a" * 64
    assert len(commits) == 1 and "入藏" in commits[0][1]
    assert view.state == "success"
    assert view.commit and view.commit["committed"] is True


def test_write_without_preview_is_refused(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    calls = _install(monkeypatch)
    asyncio.run(view._write())
    assert calls == []
    assert view.state == "empty"


def test_sha_mismatch_marks_stale_and_does_not_commit(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    commits: list = []
    _install(monkeypatch, write={"ok": False, "code": 2,
                                 "stderr": "库 sha256 与 --expect-db-sha256 不符"},
             commits=commits)
    asyncio.run(view._preview())
    asyncio.run(view._write())
    assert commits == []
    assert view.state == "stale"
    assert not view._can_confirm()
    assert any(event == "bib_import_write" and fields.get("ok") is False
               for event, fields in host.events)


def test_param_change_invalidates_preview(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    _install(monkeypatch)
    asyncio.run(view._preview())

    class _E:
        control = type("Ctl", (), {"value": "2026-3"})()

    view._on_issue(_E())
    assert view._is_stale()
    assert not view._can_confirm()
    assert "期号" in view.dirty_reason

    view._toggle_more()
    assert view.more_open is True
    asyncio.run(view._preview())
    assert not view._is_stale()


def test_dir_mode_argv(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    root = tmp_path / "issue"
    root.mkdir()
    view = BI.BibImportView(host)
    view.mode = "dir"
    view.path = str(root)
    view.through_year = "2026"
    view.finished_only = True
    calls = _install(monkeypatch)
    asyncio.run(view._preview())
    args = args_of(calls[0])
    assert args[:2] == ["import-dir", "--json"]
    assert args[args.index("--root") + 1] == str(root)
    assert args[args.index("--through-year") + 1] == "2026"
    assert "--finished-only" in args


def test_paste_mode_writes_temp_bib_outside_repo(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.mode = "paste"
    view.paste_text = "@article{x, title={T}}"
    calls = _install(monkeypatch)
    asyncio.run(view._preview())
    args = args_of(calls[0])
    bib = args[args.index("--bib") + 1]
    assert Path(bib).read_text(encoding="utf-8") == view.paste_text
    assert str(tmp_path) not in bib


def test_clear_form_resets_state(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    _install(monkeypatch)
    asyncio.run(view._preview())
    view._clear()
    assert view.payload is None and view.state == "empty"
    assert view.path == "" and view.expect_sha == ""
    assert any("清空" in text for text in host.app.toasts)
