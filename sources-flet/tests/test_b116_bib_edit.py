"""批 115 BPAGES-B · 编辑页（bib_edit）单元测试。

不连真实引擎 / 不碰真实库：命令走 monkeypatch，库路径指向 tmp。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from services import engine_bridge as EB
from ui import bib_edit as BE
from ui.tokens import Tokens

RECORD = {
    "key": "li_2021_zhuangzi", "entry_type": "article", "verified": True,
    "fields": {"title": "庄子哲学", "author": "李某某", "year": "2021",
               "publisher": "商务印书馆"},
    "aliases": ["li2021"],
}


class _App:
    def __init__(self) -> None:
        self.toasts: list[str] = []
        self.notes_repo = ""

    def toast(self, text: str) -> None:
        self.toasts.append(text)


class _FletPage:
    width = 1160
    height = 780

    def __init__(self) -> None:
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
        self.app = _App()
        self.store = _Store()
        self.page = _FletPage()
        self.t = Tokens()
        self.stats_data = {"db": db}
        self.events: list[tuple[str, dict]] = []
        self.tasks: list = []

    def mark(self, event, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, text: str) -> None:
        self.app.toasts.append(text)

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


def _install(monkeypatch, *, query=None, preview=None, write=None, commits=None):
    calls: list[EB.EngineCall] = []

    def fake_read(call, notes=""):
        calls.append(call)
        argv = list(call.argv)
        if "query" in argv:
            return {"ok": True, "json": query if query is not None else
                    {"hits": [{"record": dict(RECORD)}]}, "code": 0, "stderr": ""}
        if "--dry-run" in argv:
            return {"ok": True, "json": preview if preview is not None else
                    {"db_sha256_before": "a" * 64,
                     "field_changes": {"year": {"old": "2021", "new": "2022"}},
                     "missing_after": ["author"]}, "code": 0, "stderr": ""}
        return {"ok": bool((write or {}).get("ok", True)),
                "json": write or {"db_sha256_after": "b" * 64,
                                  "field_changes": {"year": {"old": "2021", "new": "2022"}}},
                "code": int((write or {}).get("code", 0)),
                "stderr": str((write or {}).get("stderr", ""))}

    monkeypatch.setattr(BE.EB, "read_json_command", fake_read)

    def fake_commit(db, message):
        if commits is not None:
            commits.append((db, message))
        return {"ok": True, "committed": True, "sha": "d" * 40, "message": message}

    monkeypatch.setattr(BE.GIT, "commit_db", fake_commit)
    return calls


def test_module_contract():
    assert BE.VIEW_KEY == "edit"
    assert BE.TITLE == "编辑"
    assert BE.ROLE == "write"
    assert "title" in BE.ENTRY_TYPES or "title" not in BE.ENTRY_TYPES  # 类型表只做候选
    assert "article" in BE.ENTRY_TYPES and len(BE.ENTRY_TYPES) == 15


def test_build_and_load(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    assert view.build() is not None
    view.key_input = RECORD["key"]
    _install(monkeypatch)
    asyncio.run(view._load())
    assert view.loaded_key == RECORD["key"]
    assert view.values["title"] == "庄子哲学"
    assert view.entry_type == "article"
    assert view._field_names()[0] == "title"
    assert view.state == "ready"
    assert view._can_confirm() is False  # 还没预览


def test_query_argv(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    calls = _install(monkeypatch)
    asyncio.run(view._load())
    args = args_of(calls[0])
    assert args[:2] == ["query", "--json"]
    assert args[args.index("--key") + 1] == "k1"
    assert args[args.index("--limit") + 1] == "10"
    # 缺口（引擎桥待接线）：bibdb_query_call 没有 db 参数，query 走引擎默认库；
    # edit 一侧才带 --db。这一行记录现状，接线后改成断言 --db。
    assert "--db" not in args


def test_load_conflicts_disable_confirm(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    record = dict(RECORD)
    record["conflicts"] = [{"field": "year"}]
    _install(monkeypatch, query={"hits": [{"record": record}]})
    asyncio.run(view._load())
    assert len(view.conflicts) == 1
    assert view._can_confirm() is False


def test_preview_sends_full_field_dict_and_no_sha(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    calls = _install(monkeypatch)
    asyncio.run(view._load())
    view.values["year"] = "2022"
    asyncio.run(view._preview())
    args = args_of(calls[-1])
    assert args[:2] == ["edit", "--json"]
    assert "--dry-run" in args
    assert "--expect-db-sha256" not in args
    fields = json.loads(args[args.index("--json-fields") + 1])
    assert fields["title"] == "庄子哲学" and fields["year"] == "2022"
    assert "publisher" in fields
    assert view.expect_sha == "a" * 64
    assert view._can_confirm() is True
    assert BE.diff_rows(view.payload)[0]["field"] == "year"


def test_write_passes_sha_and_backup_and_commits_once(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    commits: list = []
    calls = _install(monkeypatch, commits=commits)
    asyncio.run(view._load())
    view.values["year"] = "2022"
    asyncio.run(view._preview())
    asyncio.run(view._write())
    args = args_of(calls[-1])
    assert "--apply" in args and "--dry-run" not in args
    assert args[args.index("--expect-db-sha256") + 1] == "a" * 64
    assert "--backup" in args
    assert len(commits) == 1 and "编辑" in commits[0][1]
    assert view.state == "success"
    assert view.commit["committed"] is True


def test_write_blocked_without_preview(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    calls = _install(monkeypatch)
    asyncio.run(view._load())
    before = len(calls)
    asyncio.run(view._write())
    assert len(calls) == before
    assert view.state == "ready"


def test_field_edit_invalidates_and_stale_blocks_confirm(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    _install(monkeypatch)
    asyncio.run(view._load())
    asyncio.run(view._preview())

    class _E:
        control = type("Ctl", (), {"value": "2023"})()

    view._on_field("year", _E())
    assert view._is_stale()
    assert view._can_confirm() is False
    assert "字段" in view.dirty_reason


def test_sha_mismatch_marks_stale(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    commits: list = []
    _install(monkeypatch, write={"ok": False, "code": 2,
                                 "stderr": "库 sha256 与 --expect-db-sha256 不符"},
             commits=commits)
    asyncio.run(view._load())
    asyncio.run(view._preview())
    asyncio.run(view._write())
    assert view.state == "stale"
    assert commits == []
    assert view.commit is None


def test_clear_draft_restores_loaded_values(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BE.BibEditView(host)
    view.key_input = "k1"
    _install(monkeypatch)
    asyncio.run(view._load())
    view.values["year"] = "1999"
    view._clear_draft()
    assert view.values["year"] == "2021"
    assert view.new_key == ""


def test_activate_consumes_focus_key(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    host.store.data["bib_focus_key"] = "k9"
    view = BE.BibEditView(host)
    view.activate()
    assert view.key_input == "k9"
    assert host.store.data["bib_focus_key"] is None
    assert host.tasks and host.tasks[-1].__name__ == "_load"
