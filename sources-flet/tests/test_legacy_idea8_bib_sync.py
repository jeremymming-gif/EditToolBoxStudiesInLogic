"""legacy-idea-8 引用库版本与同步页：隔离 Git fixture 与内部路由。"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import bib_git as GIT
from ui import bib_host as BH
from ui import bib_ops as BO
from ui import bib_settings as BS
from ui import bib_version_sync as VS
from ui.tokens import Tokens


@pytest.fixture
def git_library(tmp_path, monkeypatch):
    repo = tmp_path / "synthetic-manuscripts"
    repo.mkdir()
    (repo / "cases").mkdir()
    db = repo / "bib_db" / "bibliography.jsonl"
    db.parent.mkdir()
    db.write_text('{"key":"sample","fields":{"title":"First"}}\n', encoding="utf-8")
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(repo))
    monkeypatch.setenv("SLCN_TEST_DATASET_ID", "legacy-idea-8-fixed-fixture")
    monkeypatch.delenv("SLCN_NOTES", raising=False)

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8")
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Fixture Editor")
    git("config", "user.email", "fixture@example.invalid")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: initial bibliography")
    return repo, db, git


@pytest.fixture
def git_library_with_remote(tmp_path, monkeypatch):
    repo = tmp_path / "synthetic-manuscripts"
    repo.mkdir()
    (repo / "cases").mkdir()
    db = repo / "bib_db" / "bibliography.jsonl"
    db.parent.mkdir()
    db.write_text('{"key":"sample","fields":{"title":"First"}}\n', encoding="utf-8")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True,
                   capture_output=True, text=True)
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(repo))
    monkeypatch.setenv("SLCN_TEST_DATASET_ID", "legacy-idea-8-fixed-fixture")
    monkeypatch.delenv("SLCN_NOTES", raising=False)

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8")
        return result.stdout.strip()

    def remote(*args: str) -> str:
        result = subprocess.run(
            ["git", "--git-dir", str(bare), *args], check=True,
            capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Fixture Editor")
    git("config", "user.email", "fixture@example.invalid")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: initial bibliography")
    git("remote", "add", "origin", str(bare))
    git("push", "-q", "-u", "origin", "HEAD")
    base = git("rev-parse", "HEAD")
    return repo, db, bare, git, remote, base


class _FakeApp:
    def __init__(self, notes: Path) -> None:
        self.notes_repo = str(notes)


class _FakePage:
    def __init__(self, repo: Path, db: Path) -> None:
        self.app = _FakeApp(repo)
        self.page = object()
        self.t = Tokens()
        self.stats_data = {"db": str(db), "records": 1, "aliases": {"total": 0},
                           "bytes": db.stat().st_size}
        self.events = []
        self.renders = 0
        self.routes = []

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def _render(self) -> None:
        self.renders += 1

    def navigate_route(self, route: str) -> None:
        self.routes.append(route)


def test_version_page_loads_explicit_synthetic_repo(git_library):
    repo, db, _git = git_library
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)

    asyncio.run(view.ensure_loaded())

    assert view.probe["ok"] is True
    assert view.probe["repo_root"] == str(repo)
    assert view.probe["head"]
    assert view.probe["branch"] in {"main", "master"}
    assert view.probe["upstream"] == ""
    assert view.probe["ahead"] is None and view.probe["behind"] is None
    assert view.probe["commits"][0]["subject"] == "fixture: initial bibliography"
    assert dict(page.events)["bib_version_sync_load"]["db"] == str(db)
    root = view.build()
    assert getattr(root, "_bib_shell", False)
    assert root._bib_title.value == "版本与同步"


def test_commit_and_rollback_only_change_library_file(monkeypatch, git_library):
    repo, db, git = git_library
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())
    old_content = db.read_text(encoding="utf-8")

    monkeypatch.setattr(VS.EB, "read_json_command", lambda *_args, **_kwargs: {
        "ok": True, "json": {"db": str(db), "records": 1, "aliases": {"total": 0},
                              "bytes": db.stat().st_size}})

    db.write_text('{"key":"sample","fields":{"title":"Second"}}\n',
                  encoding="utf-8")
    other = repo / "notes.txt"
    other.write_text("keep dirty\n", encoding="utf-8")
    asyncio.run(view._do_commit())

    assert GIT.status(str(db), notes_repo=str(repo))["dirty_db"] is False
    assert GIT.status(str(db), notes_repo=str(repo))["dirty_other"] == 1
    assert git("show", "--pretty=format:", "--name-only", "HEAD").splitlines() == [
        "bib_db/bibliography.jsonl"]

    asyncio.run(view._do_rollback())

    assert db.read_text(encoding="utf-8") == old_content
    assert other.read_text(encoding="utf-8") == "keep dirty\n"
    assert GIT.status(str(db), notes_repo=str(repo))["dirty_other"] == 1
    assert "回退" in view.notices[-1][1]


def test_all_mutating_actions_require_existing_confirmation(monkeypatch, git_library):
    repo, db, _git = git_library
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())
    confirmations = []

    def confirm(_page, _tokens, **kwargs):
        confirmations.append(kwargs)

    monkeypatch.setattr(VS.C, "confirm", confirm)
    view._run = lambda task: asyncio.run(task)
    view._ask_commit()
    view._ask_rollback()

    assert [item["confirm_label"] for item in confirmations] == ["提交", "回退"]
    assert all(callable(item["on_confirm"]) for item in confirmations)
    assert not view.busy


def test_version_page_key_value_row_avoids_wrap_expand_parent_conflict():
    view = object.__new__(VS.BibVersionSyncView)
    view.page = SimpleNamespace(t=Tokens())

    row = view._kv("库文件路径", "/tmp/synthetic/bib_db/bibliography.jsonl")

    assert row.wrap is False
    assert row.controls[1].expand


@pytest.mark.parametrize("operation", ["commit", "rollback"])
def test_commit_and_rollback_confirmation_bind_selected_library(
        monkeypatch, git_library, operation):
    repo, db, git = git_library
    if operation == "commit":
        db.write_text('{"key":"sample","fields":{"title":"Pending"}}\n',
                      encoding="utf-8")
    else:
        db.write_text('{"key":"sample","fields":{"title":"Second"}}\n',
                      encoding="utf-8")
        git("add", "--", "bib_db/bibliography.jsonl")
        git("commit", "-q", "-m", "fixture: rollback confirmation target")
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())
    confirmations = []
    monkeypatch.setattr(VS.C, "confirm",
                        lambda _page, _tokens, **kwargs: confirmations.append(kwargs))
    view._run = lambda task: asyncio.run(task)

    (view._ask_commit if operation == "commit" else view._ask_rollback)()

    assert len(confirmations) == 1
    message = confirmations[0]["message"]
    assert str(db) in message and str(repo) in message
    assert "SHA256" in message and "HEAD" in message
    head = git("rev-parse", "HEAD")
    contents = db.read_bytes()
    changed_db = str(repo / "other" / "bib_db" / "bibliography.jsonl")
    view.stats = {"db": changed_db}
    page.stats_data = {"path": changed_db}

    confirmations[0]["on_confirm"]()

    assert git("rev-parse", "HEAD") == head
    assert db.read_bytes() == contents
    assert not view.busy


def test_rollback_force_refreshes_stats_from_current_synthetic_library(
        monkeypatch, git_library):
    repo, db, git = git_library
    first = db.read_text(encoding="utf-8")
    second = '{"key":"second","fields":{"title":"Second"}}\n'
    db.write_text(first + second, encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: second bibliography record")
    page = _FakePage(repo, db)
    page.stats_data["records"] = 2
    page.stats_data["bytes"] = db.stat().st_size
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())

    def read_stats(_args, *, notes=""):
        data = db.read_text(encoding="utf-8").splitlines()
        return {"ok": True, "json": {"db": str(db), "records": len(data),
                                       "aliases": {"total": 0}, "bytes": db.stat().st_size}}

    monkeypatch.setattr(VS.EB, "read_json_command", read_stats)
    asyncio.run(view._do_rollback())

    assert db.read_text(encoding="utf-8") == first
    assert view.stats["records"] == 1
    assert view.stats["bytes"] == db.stat().st_size


def test_push_confirmation_binds_exact_tip_upstream_and_runs_once(
        monkeypatch, git_library_with_remote):
    repo, db, _bare, git, remote, _base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Confirmed"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: confirmed bibliography")
    expected_tip = git("rev-parse", "HEAD")
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())
    confirmations = []
    monkeypatch.setattr(VS.C, "confirm",
                        lambda _page, _tokens, **kwargs: confirmations.append(kwargs))
    view._run = lambda task: asyncio.run(task)

    view._ask_push()

    assert len(confirmations) == 1
    message = confirmations[0]["message"]
    assert str(db) in message and str(repo) in message
    assert "origin/" in message and expected_tip[:12] in message
    assert str(_bare) in message
    assert expected_tip[:12] in message.split("待推提交 SHA：", 1)[1]
    assert "1 条 bib-only 提交" in message
    callback = confirmations[0]["on_confirm"]
    callback()
    callback()

    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == expected_tip
    assert not view.busy


@pytest.mark.parametrize("changed_target", ["library", "notes"])
def test_push_confirmation_callback_rejects_stale_host_target(
        monkeypatch, git_library_with_remote, tmp_path, changed_target):
    repo, db, _bare, git, remote, base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Confirmed"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: stale host target confirmation")
    page = _FakePage(repo, db)
    view = VS.BibVersionSyncView(page)
    asyncio.run(view.ensure_loaded())
    confirmations = []
    monkeypatch.setattr(VS.C, "confirm",
                        lambda _page, _tokens, **kwargs: confirmations.append(kwargs))
    view._run = lambda task: asyncio.run(task)
    real_push = GIT.push
    pushes = []

    def count_push(*args, **kwargs):
        pushes.append((args, kwargs))
        return real_push(*args, **kwargs)

    monkeypatch.setattr(GIT, "push", count_push)
    view._ask_push()
    assert len(confirmations) == 1
    callback = confirmations[0]["on_confirm"]

    if changed_target == "library":
        other_db = tmp_path / "other-library.jsonl"
        other_db.write_text("{}\n", encoding="utf-8")
        page.stats_data = {"path": str(other_db)}
    else:
        page.app.notes_repo = str(tmp_path / "other-notes")
    callback()

    assert pushes == []
    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == base
    assert not view.busy
    assert "已变化" in view.notices[-1][1]


def test_push_explicit_refspec_does_not_follow_tags(git_library_with_remote):
    repo, db, bare, git, remote, _base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Tagged tip"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: tagged bibliography")
    tip = git("rev-parse", "HEAD")
    git("tag", "fixture-extra-tag", tip)
    git("config", "push.followTags", "true")

    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))
    result = GIT.push(str(db), notes_repo=str(repo), expected=confirmed)

    assert result["ok"] is True and result["pushed"] is True
    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == tip
    refs = subprocess.run(["git", "--git-dir", str(bare), "show-ref", "--tags"],
                          capture_output=True, text=True)
    assert refs.returncode == 1


def test_push_rejects_new_unseen_commit_after_confirmation(git_library_with_remote):
    repo, db, _bare, git, remote, base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Shown"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: shown bibliography")
    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))
    assert confirmed["kind"] == "pushable" and confirmed["count"] == 1

    db.write_text('{"key":"sample","fields":{"title":"Unseen"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: unseen bibliography")
    result = GIT.push(str(db), notes_repo=str(repo), expected=confirmed)

    assert result["ok"] is False and result["pushed"] is False
    assert "已变化" in result["error"] and "未确认" in result["error"]
    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == base


def test_push_rejects_confirmation_for_different_target_repo(
        tmp_path, monkeypatch, git_library_with_remote):
    repo, db, _bare, git, remote, base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"First target"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: first target")
    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))

    other = tmp_path / "other-manuscripts"
    other.mkdir()
    (other / "bib_db").mkdir()
    other_db = other / "bib_db" / "bibliography.jsonl"
    other_db.write_text('{"key":"other","fields":{"title":"Other"}}\n',
                        encoding="utf-8")
    other_git = lambda *args: subprocess.run(
        ["git", "-C", str(other), *args], check=True, capture_output=True,
        text=True, encoding="utf-8").stdout.strip()
    other_git("init", "-q")
    other_git("config", "user.name", "Fixture Editor")
    other_git("config", "user.email", "fixture@example.invalid")
    other_git("add", "--", "bib_db/bibliography.jsonl")
    other_git("commit", "-q", "-m", "fixture: other base")
    other_git("remote", "add", "origin", str(_bare))
    other_branch = other_git("branch", "--show-current")
    other_base = other_git("rev-parse", "HEAD")
    other_db.write_text('{"key":"other","fields":{"title":"Other local"}}\n',
                        encoding="utf-8")
    other_git("add", "--", "bib_db/bibliography.jsonl")
    other_git("commit", "-q", "-m", "fixture: other local bibliography")
    other_git("update-ref", "refs/remotes/origin/" + other_branch, other_base)
    other_git("branch", "--set-upstream-to=origin/" + other_branch)

    result = GIT.push(str(other_db), expected=confirmed)

    assert result["ok"] is False and result["pushed"] is False
    assert "已变化" in result["error"]
    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == base


def test_push_rejects_remote_destination_changed_after_confirmation(
        tmp_path, git_library_with_remote):
    repo, db, _bare, git, remote, base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Confirmed"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: confirmed destination")
    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))
    alternate = tmp_path / "alternate-origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(alternate)], check=True,
                   capture_output=True, text=True)
    git("remote", "set-url", "--push", "origin", str(alternate))

    result = GIT.push(str(db), notes_repo=str(repo), expected=confirmed)

    assert result["ok"] is False and result["pushed"] is False
    assert "已变化" in result["error"]
    assert remote("rev-parse", "refs/heads/" + git("branch", "--show-current")) == base
    assert subprocess.run(["git", "--git-dir", str(alternate), "show-ref"],
                          capture_output=True, text=True).returncode == 1


def test_push_resolves_and_rechecks_push_instead_of_destination(
        tmp_path, git_library_with_remote):
    repo, db, _bare, git, _remote, _base = git_library_with_remote
    branch = git("branch", "--show-current")
    db.write_text('{"key":"sample","fields":{"title":"Rewrite target"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: rewritten push target")
    first = tmp_path / "first-rewrite.git"
    second = tmp_path / "second-rewrite.git"
    for target in (first, second):
        subprocess.run(["git", "init", "--bare", "-q", str(target)], check=True,
                       capture_output=True, text=True)
    git("remote", "set-url", "origin", "fixture-target:")
    git("config", "url." + str(first) + ".pushInsteadOf", "fixture-target:")
    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))
    actual_upstream_ref = git("rev-parse", "--symbolic-full-name", "@{upstream}")

    assert confirmed["ok"] is True
    assert confirmed["upstream_ref"] == actual_upstream_ref
    assert confirmed["push_urls"] == [str(first)]
    git("config", "--unset-all", "url." + str(first) + ".pushInsteadOf")
    git("config", "url." + str(second) + ".pushInsteadOf", "fixture-target:")
    rejected = GIT.push(str(db), notes_repo=str(repo), expected=confirmed)

    assert rejected["ok"] is False and rejected["pushed"] is False
    assert "已变化" in rejected["error"]
    for target in (first, second):
        refs = subprocess.run(["git", "--git-dir", str(target), "show-ref"],
                              capture_output=True, text=True)
        assert refs.returncode == 1

    confirmed_after_change = GIT.push_snapshot(str(db), notes_repo=str(repo))
    result = GIT.push(str(db), notes_repo=str(repo), expected=confirmed_after_change)
    assert result["ok"] is True and result["pushed"] is True
    assert result["tip"] == confirmed_after_change["tip"]
    assert git("rev-parse", actual_upstream_ref) == confirmed_after_change["tip"]
    assert subprocess.run(
        ["git", "--git-dir", str(second), "rev-parse", "refs/heads/" + branch],
        check=True, capture_output=True, text=True).stdout.strip() == confirmed_after_change["tip"]


def test_push_rejects_destination_changed_by_second_url_rewrite(
        tmp_path, git_library_with_remote):
    repo, db, _bare, git, _remote, _base = git_library_with_remote
    db.write_text('{"key":"sample","fields":{"title":"Rewrite chain"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: rewrite chain bibliography")
    first = tmp_path / "first-rewrite.git"
    second = tmp_path / "second-rewrite.git"
    for target in (first, second):
        subprocess.run(["git", "init", "--bare", "-q", str(target)], check=True,
                       capture_output=True, text=True)
    git("remote", "set-url", "origin", "fixture-target:")
    git("config", "url." + str(first) + ".pushInsteadOf", "fixture-target:")
    git("config", "url." + str(second) + ".insteadOf", str(first))

    resolved = GIT._push_urls(GIT.find_git(), str(repo), "origin")
    preview = GIT.push_snapshot(str(db), notes_repo=str(repo))

    assert resolved == [str(first)]
    assert preview["ok"] is False and preview["soft"] is True
    assert "再次改变" in preview["error"]
    for target in (first, second):
        refs = subprocess.run(["git", "--git-dir", str(target), "show-ref"],
                              capture_output=True, text=True)
        assert refs.returncode == 1


def test_push_uses_upstream_remote_ref_with_custom_fetch_mapping(
        git_library_with_remote):
    repo, db, bare, git, remote, base = git_library_with_remote
    branch_ref = git("symbolic-ref", "--quiet", "HEAD")
    git("config", "--unset-all", "remote.origin.fetch")
    git("config", "--add", "remote.origin.fetch",
        "+refs/heads/main:refs/remotes/origin/mirror-main")
    git("update-ref", "-d", "refs/remotes/origin/main")
    git("update-ref", "refs/remotes/origin/mirror-main", base)
    git("config", "branch." + branch_ref.removeprefix("refs/heads/") + ".remote", "origin")
    git("config", "branch." + branch_ref.removeprefix("refs/heads/") + ".merge",
        "refs/heads/main")
    db.write_text('{"key":"sample","fields":{"title":"Mapped upstream"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: mapped bibliography")

    actual_local_ref = git("rev-parse", "--symbolic-full-name", "@{upstream}")
    confirmed = GIT.push_snapshot(str(db), notes_repo=str(repo))

    assert confirmed["ok"] is True
    assert actual_local_ref == "refs/remotes/origin/mirror-main"
    assert confirmed["upstream_ref"] == actual_local_ref
    assert confirmed["remote"] == "origin"
    assert confirmed["target_branch"] == "main"
    result = GIT.push(str(db), notes_repo=str(repo), expected=confirmed)

    assert result["ok"] is True and result["pushed"] is True
    assert remote("rev-parse", "refs/heads/main") == confirmed["tip"]
    assert git("rev-parse", actual_local_ref) == confirmed["tip"]
    unmapped = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
         "refs/remotes/origin/main"], capture_output=True, text=True)
    assert unmapped.returncode == 1


def test_commit_rejects_changed_confirmed_library_snapshot(git_library):
    repo, db, git = git_library
    db.write_text('{"key":"sample","fields":{"title":"Confirmed"}}\n',
                  encoding="utf-8")
    snapshot = GIT.mutation_snapshot(str(db), notes_repo=str(repo))
    head = git("rev-parse", "HEAD")
    db.write_text('{"key":"sample","fields":{"title":"Unseen"}}\n',
                  encoding="utf-8")

    result = GIT.commit_db(str(db), "fixture: must not commit", expected_snapshot=snapshot)

    assert result["ok"] is False and result["committed"] is False
    assert "已变化" in result["error"]
    assert git("rev-parse", "HEAD") == head
    assert 'Unseen' in db.read_text(encoding="utf-8")


def test_rollback_rejects_changed_confirmed_worktree_snapshot(git_library):
    repo, db, git = git_library
    db.write_text('{"key":"sample","fields":{"title":"Second"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")
    git("commit", "-q", "-m", "fixture: second bibliography")
    snapshot = GIT.mutation_snapshot(str(db), notes_repo=str(repo))
    head = git("rev-parse", "HEAD")
    other = repo / "notes.txt"
    other.write_text("new worktree state\n", encoding="utf-8")

    result = GIT.rollback(str(db), notes_repo=str(repo), expected_snapshot=snapshot)

    assert result["ok"] is False and result["reverted"] is False
    assert "已变化" in result["error"]
    assert git("rev-parse", "HEAD") == head
    assert 'Second' in db.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == "new worktree state\n"


def test_mutation_snapshot_detects_repo_status_metadata_change(git_library):
    repo, db, git = git_library
    other = repo / "notes.txt"
    other.write_text("first dirty content\n", encoding="utf-8")
    confirmed = GIT.mutation_snapshot(str(db), notes_repo=str(repo))
    git("add", "--", "notes.txt")

    current = GIT.mutation_snapshot(str(db), notes_repo=str(repo))

    assert confirmed["ok"] is True and current["ok"] is True
    assert confirmed["dirty_other"] == current["dirty_other"] == 1
    assert confirmed["worktree_sha256"] != current["worktree_sha256"]


def test_mutation_snapshot_binds_database_index_fingerprint(git_library):
    repo, db, git = git_library
    confirmed = GIT.mutation_snapshot(str(db), notes_repo=str(repo))
    db.write_text('{"key":"sample","fields":{"title":"Staged"}}\n',
                  encoding="utf-8")
    git("add", "--", "bib_db/bibliography.jsonl")

    current = GIT.mutation_snapshot(str(db), notes_repo=str(repo))

    assert confirmed["ok"] is True and current["ok"] is True
    assert confirmed["db_index_sha256"] != current["db_index_sha256"]
    assert not GIT._matches_mutation_snapshot(confirmed, current)


def test_host_keeps_version_page_reachable_without_global_ia(monkeypatch):
    host = object.__new__(BH.BibHost)
    marks, renders, loads = [], [], []
    navigated = []
    host._version_sync_active = False
    host.mark = lambda event, **fields: marks.append((event, fields))
    host._sync_header = lambda: None
    host._render = lambda: renders.append(True)
    host._ensure_loaded = lambda view: loads.append(view)
    host.app = type("App", (), {"navigate": lambda self, *args, **kwargs:
                                navigated.append((args, kwargs))})()

    host.navigate_route("bib:version_sync")

    assert host._version_sync_active is True
    assert marks == [("bib_version_sync_view", {"source": "ui"})]
    assert renders and loads == ["version_sync"]
    assert navigated == []
    assert "version_sync" not in BH.BibHost.view_keys()


def test_settings_summary_links_to_version_page(monkeypatch):
    routes = []
    buttons = []
    view = object.__new__(BS.BibSettingsView)
    view.page = SimpleNamespace(t=Tokens(), navigate_route=routes.append)
    view.probe = {"ok": True, "branch": "main", "head": "abc", "upstream": "origin/main",
                  "ahead": 0, "behind": 0, "dirty_db": False, "dirty_other": 0,
                  "commits": [], "warnings": []}
    monkeypatch.setattr(BS.C, "secondary_button",
                        lambda label, callback, *_args, **_kwargs:
                        buttons.append((label, callback)) or object())

    view._version_summary()

    assert buttons[0][0] == "打开版本与同步"
    buttons[0][1](None)
    assert routes == ["bib:version_sync"]


def test_edit_result_links_to_version_page(monkeypatch):
    routes = []
    buttons = []
    page = SimpleNamespace(t=Tokens(), app=SimpleNamespace(toast=lambda *_args: None),
                            navigate_route=routes.append)
    view = BO._OpsView(page)
    view.commit = {"committed": False, "error": "未生成提交"}
    view._toast("编辑已写入；版本层提示：未生成提交")
    monkeypatch.setattr(BO.C, "secondary_button",
                        lambda label, callback, *_args, **_kwargs:
                        buttons.append((label, callback)) or object())

    view._notice_bar()

    assert buttons[0][0] == "查看版本与同步"
    buttons[0][1](None)
    assert routes == ["bib:version_sync"]
