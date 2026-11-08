"""P07 引用库资产包 · R02/R03/R04/R05 验收测试（纯本地，不联网）。

覆盖（每条都先在 $EVID/team/gui/ 的 repro_*.py 里复现过）：

* R04 commit_db：只 stage ``bib_db/bibliography.jsonl``；库没变不空提交；
  身份缺失 = 已写库、未生成提交；merge 进行中拒绝；其它 dirty 文件不动。
* R05 rollback：目标是「上一次触及库文件的提交」（HEAD~1 不是库提交时也对）；
  库 dirty 时拒绝且零写；连续回退不假成功、不来回切换；提交失败不留半写。
* R05 push：只推连续 bib-only 前缀；merge 提交不算 bib-only；blocked 整次拒绝；
  绝不 ``--force``、不自动 pull。
* R04 status()：严格只读（不改 .git/index），字段与真实状态一致。
* R05 GUI：回退只有 ``ok and committed`` 才显示「生成新提交」。
* R02：编辑/入藏预览只针对明确选定记录；引擎拒绝/取消 → 零写、零提交。
* R03：批量动作逐条对账（成功/失败计数、部分写入保留、提示诚实）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import bib_git as GIT
from ui import bib_edit as BE
from ui import bib_dupes as BD
from ui import bib_write as BW
from ui import bib_ops as BO
from ui import bib_ops_health as BOH

DB_REL = "bib_db/bibliography.jsonl"
BIB1 = '{"key":"v1","title":"第一卷"}\n'
BIB2 = '{"key":"v2","title":"第二卷"}\n'


# ── 本地 git 夹具（不联网） ─────────────────────────────────────────────

def _env(home: Path | None = None) -> dict:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull})
    if home is not None:
        env["HOME"] = str(home)
        env.pop("XDG_CONFIG_HOME", None)
    return env


def git(repo: Path, *args: str, env: dict | None = None, check: bool = True):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=env or _env())
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{proc.stderr or proc.stdout}")
    return proc


def make_repo(tmp_path: Path, db_text: str = BIB1) -> Path:
    repo = tmp_path / "notes"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "P07 Fixture")
    git(repo, "config", "user.email", "p07@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "cases").mkdir()
    (repo / "cases" / "base.md").write_text("base\n", encoding="utf-8")
    (repo / DB_REL).parent.mkdir(parents=True)
    (repo / DB_REL).write_text(db_text, encoding="utf-8")
    git(repo, "add", "--", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def commit_file(repo: Path, rel: str, text: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", rel)
    git(repo, "commit", "-q", "-m", message, "--", rel)
    return head(repo)


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def commit_count(repo: Path) -> int:
    return int(git(repo, "rev-list", "--count", "HEAD").stdout.strip())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def changed_paths(repo: Path, rev: str = "HEAD") -> list[str]:
    out = git(repo, "show", "--name-only", "--format=", rev).stdout
    return [line for line in out.splitlines() if line.strip()]


def make_remote(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)],
                   capture_output=True, text=True, check=True, env=_env())
    git(repo, "remote", "add", "origin", f"file://{bare}")
    git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def remote_head(bare: Path) -> str:
    return git(bare, "rev-parse", "refs/heads/main").stdout.strip()


# ── R04 commit_db ───────────────────────────────────────────────────────

def test_commit_db_stages_only_bib_file_and_keeps_other_dirty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "cases" / "x.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "--", "cases/x.md")
    git(repo, "commit", "-q", "-m", "other base", "--", "cases/x.md")
    (repo / "cases" / "x.md").write_text("dirty\n", encoding="utf-8")
    (repo / DB_REL).write_text(BIB2, encoding="utf-8")

    before = commit_count(repo)
    res = GIT.commit_db(str(repo / DB_REL), "引用库：测试提交")

    assert res["ok"] is True and res["committed"] is True
    assert commit_count(repo) == before + 1
    assert changed_paths(repo) == [DB_REL], "提交只允许包含库文件"
    status = git(repo, "status", "--porcelain").stdout
    assert " M cases/x.md" in status or "M cases/x.md" in status
    # 别人的 dirty 没有被 stage
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_commit_db_no_change_makes_no_empty_commit(tmp_path):
    repo = make_repo(tmp_path)
    before = head(repo)
    res = GIT.commit_db(str(repo / DB_REL), "引用库：没变")
    assert res["ok"] is True and res["committed"] is False
    assert res["sha"] == before
    assert "没有改动" in res["reason"]
    assert head(repo) == before


def test_commit_db_identity_missing_is_explicit(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    git(repo, "config", "--unset-all", "user.name")
    git(repo, "config", "--unset-all", "user.email")
    git(repo, "config", "user.useConfigOnly", "true")
    (repo / DB_REL).write_text(BIB2, encoding="utf-8")

    before = commit_count(repo)
    res = GIT.commit_db(str(repo / DB_REL), "引用库：身份缺失")

    assert res["ok"] is False and res["committed"] is False
    assert res["soft"] is True
    assert "已写库，未生成提交" in res["error"]
    assert commit_count(repo) == before
    # 数据已经写进工作区，只是没提交（不能假装库没写）
    assert (repo / DB_REL).read_text(encoding="utf-8") == BIB2


def test_commit_db_refuses_during_merge_conflict(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    git(repo, "checkout", "-q", "-b", "side")
    (repo / DB_REL).write_text(BIB2, encoding="utf-8")
    git(repo, "commit", "-q", "-am", "side db")
    git(repo, "checkout", "-q", "main")
    (repo / DB_REL).write_text('{"key":"v3"}\n', encoding="utf-8")
    git(repo, "commit", "-q", "-am", "main db")
    merged = subprocess.run(["git", "-C", str(repo), "merge", "side"],
                            capture_output=True, text=True, env=_env())
    assert merged.returncode != 0, "夹具必须制造出未解决的合并冲突"
    before = head(repo)

    res = GIT.commit_db(str(repo / DB_REL), "引用库：冲突中提交")

    assert res["ok"] is False and res["committed"] is False
    assert res["soft"] is True
    assert "合并" in res["error"] or "merge" in res["error"].lower()
    assert head(repo) == before


# ── R05 rollback ────────────────────────────────────────────────────────

def test_rollback_uses_last_db_commit_not_head_parent(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    commit_file(repo, DB_REL, BIB2, "bib v2")
    commit_file(repo, "cases/a.md", "notes\n", "notes only")
    before = commit_count(repo)

    res = GIT.rollback(str(repo / DB_REL))

    assert res["ok"] is True and res["reverted"] is True and res["committed"] is True
    assert (repo / DB_REL).read_text(encoding="utf-8") == BIB1
    assert commit_count(repo) == before + 1
    assert changed_paths(repo) == [DB_REL], "回退提交也只允许动库文件"
    assert "回退" in git(repo, "log", "-1", "--format=%s").stdout


def test_rollback_refuses_dirty_db_and_writes_nothing(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    commit_file(repo, DB_REL, BIB2, "bib v2")
    dirty = '{"key":"v2","dirty":true}\n'
    (repo / DB_REL).write_text(dirty, encoding="utf-8")
    before_head, before_count = head(repo), commit_count(repo)

    res = GIT.rollback(str(repo / DB_REL))

    assert res["ok"] is False and res["reverted"] is False and res["soft"] is True
    assert "未提交改动" in res["error"]
    assert (repo / DB_REL).read_text(encoding="utf-8") == dirty
    assert head(repo) == before_head and commit_count(repo) == before_count
    assert "M bib_db/bibliography.jsonl" in git(repo, "status", "--porcelain").stdout


def test_second_rollback_is_not_fake_success(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    commit_file(repo, DB_REL, BIB2, "bib v2")
    first = GIT.rollback(str(repo / DB_REL))
    assert first["ok"] is True and first["committed"] is True
    db_after_first = (repo / DB_REL).read_text(encoding="utf-8")
    before_head, before_count = head(repo), commit_count(repo)

    second = GIT.rollback(str(repo / DB_REL))

    assert second["ok"] is False
    assert second["reverted"] is False
    assert "最早可回退" in second["error"] or "没有变化" in second["error"]
    assert (repo / DB_REL).read_text(encoding="utf-8") == db_after_first
    assert head(repo) == before_head and commit_count(repo) == before_count


def test_rollback_commit_failure_leaves_no_half_write(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, BIB1)
    commit_file(repo, DB_REL, BIB2, "bib v2")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    git(repo, "config", "--unset-all", "user.name")
    git(repo, "config", "--unset-all", "user.email")
    git(repo, "config", "user.useConfigOnly", "true")
    before_head, before_count = head(repo), commit_count(repo)

    res = GIT.rollback(str(repo / DB_REL))

    assert res["ok"] is False and res["reverted"] is False
    assert (repo / DB_REL).read_text(encoding="utf-8") == BIB2
    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    assert head(repo) == before_head and commit_count(repo) == before_count


def test_rollback_rejects_history_with_one_db_commit(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    commit_file(repo, "cases/a.md", "notes\n", "notes only")
    res = GIT.rollback(str(repo / DB_REL))
    assert res["ok"] is False and res["reverted"] is False
    assert "没有可回退" in res["error"]


# ── R05 push ────────────────────────────────────────────────────────────

def test_push_only_continuous_bib_prefix_and_never_force(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, BIB1)
    bare = make_remote(tmp_path, repo)
    upstream = head(repo)
    c1 = commit_file(repo, DB_REL, BIB2, "bib one")
    c2 = commit_file(repo, DB_REL, '{"key":"v3"}\n', "bib two")
    c3 = commit_file(repo, "cases/a.md", "notes\n", "notes only")
    c4 = commit_file(repo, DB_REL, '{"key":"v4"}\n', "bib after notes")
    calls: list[list[str]] = []
    original = GIT._run

    def spy(git_bin, args, **kwargs):
        calls.append(list(args))
        return original(git_bin, args, **kwargs)

    monkeypatch.setattr(GIT, "_run", spy)
    res = GIT.push(str(repo / DB_REL))

    assert res["ok"] is True and res["pushed"] is True
    assert res["count"] == 2 and res["tip"] == c2
    assert len(res["remainder"]) == 2
    assert remote_head(bare) == c2
    assert head(repo) == c4, "push 不改本地 HEAD"
    pushes = [args for args in calls if args[:1] == ["push"]]
    assert pushes, "必须真的调用 push"
    for args in pushes:
        assert not any(str(item).startswith("+") for item in args), "绝不 force"
        assert "--force" not in args
    flat = [item for args in calls for item in args]
    for forbidden in ("pull", "fetch", "reset", "merge"):
        assert forbidden not in flat, f"push 不得自动 {forbidden}"
    assert "origin" in flat

    # 第二次：最旧未推送是 notes 提交 → 整次拒绝，remote 不动
    before_remote = remote_head(bare)
    calls.clear()
    blocked = GIT.push(str(repo / DB_REL))
    assert blocked["ok"] is False and blocked["pushed"] is False
    assert "非 bib_db/" in blocked["error"] or "整次拒绝" in blocked["error"]
    assert remote_head(bare) == before_remote
    assert [args for args in calls if args[:1] == ["push"]] == []


def test_push_blocks_merge_commit(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    bare = make_remote(tmp_path, repo)
    c1 = head(repo)
    c2 = commit_file(repo, DB_REL, BIB2, "bib v2")
    git(repo, "push", "-q", "origin", "main")
    # 合成 merge 提交：两个父（c1、c2）都已在 upstream 里，只有 merge 本身未推送，
    # 且 git log 对 merge 默认不列文件（paths 为空）——仍必须按 merge 拒推。
    tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    merge = git(repo, "commit-tree", tree, "-p", c1, "-p", c2,
                "-m", "merge synthetic").stdout.strip()
    git(repo, "update-ref", "refs/heads/main", merge, c2)
    res = GIT.push(str(repo / DB_REL))
    assert res["ok"] is False and res["pushed"] is False
    assert "merge" in res["error"].lower() or "合并" in res["error"]
    assert remote_head(bare) == c2, "blocked 时 remote 不动"


def test_push_reports_nothing_when_up_to_date(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    make_remote(tmp_path, repo)
    res = GIT.push(str(repo / DB_REL))
    assert res["ok"] is True and res["pushed"] is False
    assert "没有待推送提交" in res["reason"]


def test_push_without_upstream_is_soft_error(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    res = GIT.push(str(repo / DB_REL))
    assert res["ok"] is False and res["pushed"] is False and res["soft"] is True
    assert "没有 upstream" in res["error"]


# ── R04 status 只读 ─────────────────────────────────────────────────────

def test_status_is_readonly_and_reports_real_state(tmp_path):
    repo = make_repo(tmp_path, BIB1)
    bare = make_remote(tmp_path, repo)
    commit_file(repo, DB_REL, BIB2, "bib v2")
    (repo / "cases" / "a.md").write_text("changed\n", encoding="utf-8")

    GIT.status(str(repo / DB_REL))  # 先让 git 把 stat 缓存写下来
    index = repo / ".git" / "index"
    index_before = sha256_file(index)
    head_before = head(repo)
    db_before = sha256_file(repo / DB_REL)

    # 内容不变、只让 mtime 变旧：git status 的 stat 缓存会失效
    old = time.time() - 30
    os.utime(repo / DB_REL, (old, old))
    res = GIT.status(str(repo / DB_REL))

    assert res["ok"] is True
    assert res["branch"] == "main"
    assert res["ahead"] == 1 and res["behind"] == 0
    assert res["dirty_db"] is False
    assert res["dirty_other"] == 1
    assert res["dirty_paths"] == ["cases/a.md"]
    assert res["conflict"] is False and res["merge"] is False and res["rebase"] is False
    assert res["identity_ok"] is True
    assert [item["sha"] for item in res["commits"]]
    # 严格只读：index / HEAD / 库文件字节都不许变
    assert sha256_file(index) == index_before, "status() 不得刷新并改写 .git/index"
    assert head(repo) == head_before
    assert sha256_file(repo / DB_REL) == db_before
    assert remote_head(bare) == git(repo, "rev-parse", "origin/main").stdout.strip()


# ── R05 GUI：回退/写库提示诚实 ──────────────────────────────────────────

class _FakeApp:
    def __init__(self) -> None:
        self.notes_repo = ""
        self.toasts: list[tuple[str, object]] = []

    def toast(self, message: str, tone=None) -> None:
        self.toasts.append((str(message), tone))


class _FakePage:
    def __init__(self, db: Path) -> None:
        self.app = _FakeApp()
        self.stats_data = {"db": str(db)}
        self.t = None
        self.events: list[tuple[str, dict]] = []
        self.renders = 0

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def _render(self) -> None:
        self.renders += 1


def _review_view(tmp_path: Path, monkeypatch, rollback_result: dict):
    repo = make_repo(tmp_path)
    page = _FakePage(repo / DB_REL)
    monkeypatch.setattr(BW.GIT, "rollback", lambda db="", **kw: dict(rollback_result))
    monkeypatch.setattr(BW.GIT, "status", lambda db="", **kw: {
        "ok": True, "branch": "main", "head": head(repo), "commits": [], "warnings": []})
    monkeypatch.setattr(BW.EB, "read_json_command", lambda call, notes="": {
        "ok": True, "json": {"items": [], "totals": {}, "db_sha256": ""}, "code": 0})
    return BW.BibReviewView(page), page


def test_gui_rollback_without_new_commit_is_not_success(tmp_path, monkeypatch):
    view, page = _review_view(tmp_path, monkeypatch, {
        "ok": True, "reverted": True, "committed": False, "sha": "a" * 40,
        "message": "引用库：回退"})
    asyncio.run(view._rollback_async())
    toast = page.app.toasts[-1][0]
    assert "没有生成新提交" in toast
    assert "已回退到上一个提交并生成新提交" not in toast


def test_gui_rollback_committed_reports_success(tmp_path, monkeypatch):
    view, page = _review_view(tmp_path, monkeypatch, {
        "ok": True, "reverted": True, "committed": True, "sha": "c" * 40,
        "message": "引用库：回退"})
    asyncio.run(view._rollback_async())
    assert "已回退到上一个提交并生成新提交" in page.app.toasts[-1][0]


def test_gui_rollback_refused_dirty_is_failure(tmp_path, monkeypatch):
    view, page = _review_view(tmp_path, monkeypatch, {
        "ok": False, "reverted": False, "soft": True,
        "error": "库文件有未提交改动：回退会覆盖这些改动，请先提交或丢弃后再回退。"})
    asyncio.run(view._rollback_async())
    toast = page.app.toasts[-1][0]
    assert toast.startswith("回退未完成：")
    assert "已回退" not in toast


@pytest.mark.parametrize("module,notice_name", [
    (BE, "_commit_notice"), (BD, "_commit_notice")])
def test_commit_notice_never_claims_commit_without_commit(module, notice_name):
    notice = getattr(module, notice_name)
    no_commit = notice("已写入 1 个字段", {
        "ok": True, "committed": False, "sha": "a" * 40,
        "reason": "库文件没有改动，未生成空提交。"})
    assert "已提交" not in no_commit
    assert "版本层提示" in no_commit
    committed = notice("已写入 1 个字段", {"ok": True, "committed": True, "sha": "c" * 40})
    assert "已提交 cccccccc · 未推送" in committed


def test_revert_guard_requires_committed(tmp_path, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(BE.GIT, "rollback",
                        lambda db="", **kw: called.append(db) or {"ok": True})
    view = SimpleNamespace(commit={"ok": True, "committed": False, "sha": "a" * 40},
                           notices=[], _notify=lambda message: view.notices.append(message))
    BE.BibEditView._revert_confirm(view)
    assert view.notices == ["没有可回退的已提交写入"]
    assert called == []


# ── R02：只写明确选定的记录/字段；拒绝 = 零写 ──────────────────────────

def test_edit_call_targets_single_selected_key_and_sha(tmp_path):
    view = SimpleNamespace(loaded_key="k1", new_key="", entry_type="book",
                           values={"title": "A"}, _db=lambda: str(tmp_path / DB_REL),
                           _backup_dir=lambda: str(tmp_path / "backup"),
                           _fields_payload=lambda: {"title": "A"})
    call = BE.BibEditView._build_call(view, dry_run=False, expect_sha="ab" * 32)
    argv = call.argv
    assert "edit" in argv and "--json" in argv
    assert argv[argv.index("--key") + 1] == "k1"
    assert argv[argv.index("--db") + 1] == str(tmp_path / DB_REL)
    assert argv[argv.index("--expect-db-sha256") + 1] == "ab" * 32
    assert "--dry-run" not in argv
    fields = json.loads(argv[argv.index("--json-fields") + 1])
    assert set(fields) == {"title"}


def test_edit_rejected_write_keeps_library_bytes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    db = repo / DB_REL
    sha_before = sha256_file(db)
    commits: list[str] = []
    monkeypatch.setattr(BE.GIT, "commit_db", lambda *a, **k: commits.append("x"))

    async def fake_call(call):
        return {"ok": False, "code": 2, "json": {}, "error": "库指纹已变化"}

    view = SimpleNamespace(busy=False, state="ready", error="", stderr="",
                           confirm_layer=False, payload={}, expect_sha="x",
                           _can_confirm=lambda: True,
                           _build_call=lambda **kw: None, _call=fake_call,
                           _sha_mismatch=lambda res: True,
                           _error_text=lambda res: "库指纹已变化",
                           _render=lambda: None, _mark=lambda *a, **k: None,
                           _notify=lambda message: None)
    asyncio.run(BE.BibEditView._write(view))

    assert commits == [], "引擎拒绝时不得生成版本提交"
    assert sha256_file(db) == sha_before, "拒绝必须零写"
    assert view.state == "stale"


def test_import_rejected_write_keeps_library_bytes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    db = repo / DB_REL
    sha_before = sha256_file(db)
    commits: list[str] = []
    monkeypatch.setattr(BW.GIT, "commit_db", lambda *a, **k: commits.append("x"))
    page = _FakePage(db)

    async def fake_call(call):
        return {"ok": False, "code": 2, "json": {}, "error": "库指纹已变化"}

    view = SimpleNamespace(preview={"db": str(db)}, expect_sha="x", stale=False,
                           done=None, commit=None, error="", mode="single",
                           issue="", slug="", verified=True,
                           preview_signature=None, t=None,
                           _preview_stale=lambda: False, _call=fake_call,
                           _build_call=lambda **kw: None, page=page)
    asyncio.run(BW.BibImportView._write(view))

    assert commits == []
    assert sha256_file(db) == sha_before
    marks = [event for event, _fields in page.events]
    assert "bib_import_write" in marks
    write_mark = dict(page.events[marks.index("bib_import_write")][1])
    assert write_mark["ok"] is False and write_mark["written"] is False


def test_dupes_merge_without_explicit_confirmation_does_nothing(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(BD.EB, "bibdb_merge_call",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    view = SimpleNamespace(pending={}, busy=False, error="",
                           _can_confirm=lambda: False)
    asyncio.run(BD.BibDupesView._write(view))
    assert calls == [], "没有明确选定并确认的一对候选时不得自动合并"


def test_edit_cancel_keeps_library_bytes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    db = repo / DB_REL
    sha_before = sha256_file(db)
    commits: list[str] = []
    monkeypatch.setattr(BE.GIT, "commit_db", lambda *a, **k: commits.append("x"))
    view = SimpleNamespace(confirm_layer=True, notices=[],
                           _notify=lambda message: view.notices.append(message),
                           _render=lambda: None)
    BE.BibEditView._cancel_confirm(view)
    assert commits == []
    assert sha256_file(db) == sha_before
    assert view.notices == ["已取消，未写库（草稿保留）"]


# ── R03：批量动作逐条对账，不默改语义 ───────────────────────────────────

def test_ops_notice_is_honest_about_write_and_commit():
    assert "没有改动" in BO.ops_notice("合并已写入", {"written": False}, {})
    partial = BO.ops_notice("采用所选 2 条", {"written": True},
                            {"ok": True, "committed": False,
                             "reason": "库文件没有改动，未生成空提交。"})
    assert "已生成版本提交" not in partial
    assert "版本层提示" in partial
    done = BO.ops_notice("采用所选 2 条", {"written": True},
                         {"ok": True, "committed": True, "sha": "d" * 40})
    assert "已生成版本提交 dddddddd" in done


def test_ops_commit_write_does_not_roll_back_business_write(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    db = repo / DB_REL
    sha_before = sha256_file(db)
    marks: list[tuple[str, dict]] = []
    monkeypatch.setattr(BO.GIT, "commit_db", lambda *a, **k: {
        "ok": False, "committed": False, "soft": True,
        "error": "git 未配置 user.name/email：已写库，未生成提交；配置好后再写库即可补上。"})
    view = SimpleNamespace(commit=None, page=SimpleNamespace(mark=lambda e, **f: marks.append((e, f))),
                           _db=lambda: str(db))
    commit = asyncio.run(BO._OpsView._commit_write(
        view, {"written": True, "db": str(db), "db_sha256_before": "a", "db_sha256_after": "b"},
        label="编辑 k1", count=1))

    assert commit["committed"] is False
    assert view.commit["ok"] is False
    assert marks and marks[-1][0] == "bib_git_commit"
    assert sha256_file(db) == sha_before, "提交失败不得回滚已完成的业务写入"

    view2 = SimpleNamespace(commit="old", page=SimpleNamespace(mark=lambda e, **f: None),
                            _db=lambda: str(db))
    out = asyncio.run(BO._OpsView._commit_write(view2, {"written": False}, label="x", count=0))
    assert out == {} and view2.commit is None


def test_ops_batch_partial_write_counts_match_ledger(tmp_path):
    repo = make_repo(tmp_path)
    db = repo / DB_REL
    plan = [{"key": f"k{i}", "incoming_sha256": "a" * 64, "expect": "e" * 64}
            for i in range(3)]
    responses = [
        {"ok": True, "json": {"written": True, "db": str(db)}},
        {"ok": True, "json": {"written": True, "db": str(db)}},
        {"ok": False, "json": {}, "error": "engine boom"},
    ]
    records = {"commits": [], "toasts": [], "loaded": 0}

    async def fake_read(call):
        return responses.pop(0)

    async def fake_commit(payload, *, label, count):
        records["commits"].append({"payload": dict(payload), "label": label, "count": count})
        view.commit = {"ok": True, "committed": False,
                       "reason": "库文件没有改动，未生成空提交。"}
        return view.commit

    async def fake_load():
        records["loaded"] += 1

    def fake_toast(message):
        records["toasts"].append(str(message))

    view = SimpleNamespace(
        pending={"plan": plan}, busy=False, error="", preview={}, batch_label="采用所选",
        commit=None, selected=set(),
        page=SimpleNamespace(stats_data={"db": str(db)}),
        _db=lambda: str(db),
        _read=fake_read, _commit_write=fake_commit,
        _toast=fake_toast, _load=fake_load, _refresh=lambda: None)

    asyncio.run(BOH.BibHealthView._write_batch(view))

    assert view.error == "" or "第 3 条" in view.error
    assert len(records["commits"]) == 1
    commit = records["commits"][0]
    assert commit["count"] == 2, "成功 2 条、失败 1 条：总账必须逐条一致"
    assert commit["label"] == "采用所选 2 条"
    assert records["toasts"] == ["采用所选 2 条；版本层提示：库文件没有改动，未生成空提交。"]
    assert records["loaded"] == 1
    assert view.pending is None and view.preview is None


# ── 第 2 轮返工 G-1..G-6（2.7-V 对 10f0d4c 的取证） ────────────────────
# 复现脚本：$EVID/team/gui/repro_p07_round2.py；
# 日志：logs/repro_p07_round2_before.log / logs/repro_p07_round2_after.log。

def _engine_cli(entry: str, *args: str):
    engine = Path(__file__).resolve().parents[2] / "Engine" / "bibdb.py"
    if not engine.exists():
        pytest.skip("本地 Engine 不存在，跳过引擎层断言")
    proc = subprocess.run([sys.executable, str(engine), entry, *args],
                          capture_output=True, text=True, env=_env())
    return engine, proc


def _review_page(db: Path) -> _FakePage:
    page = _FakePage(db)
    page.page = SimpleNamespace(run_task=lambda fn: None)
    return page


def _resolve_payload(argv: list[str], sha: str) -> dict:
    dry = "--dry-run" in argv
    return {"ok": True, "json": {"written": not dry, "db_sha256_after": sha}, "code": 0}


def test_g1_review_adopt_sends_incoming_sha_only(tmp_path, monkeypatch):
    """G-1：ReviewView 不再同时传 --fields 与 --incoming-sha256。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    seen: list[list[str]] = []

    def fake_read(call, **kw):
        seen.append(list(call.argv))
        if "resolve" in call.argv:
            return _resolve_payload(call.argv, BIB1 and "a" * 64)
        return {"ok": True, "json": {}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BR.GIT, "commit_db", lambda db, message: {
        "ok": True, "committed": False, "reason": "fixture：没有改动"})
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    row = {"no": "C001", "key": "li2024analysis",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"},
                             "year": {"old": "2020", "new": "2021"}}},
           "severity": "高", "field": "pages", "source": "b.bib"}
    view.all_rows = [row]
    view.staged = {"kind": "detail", "no": row["no"], "row": row, "note": ""}
    asyncio.run(view._write())

    resolves = [argv for argv in seen if "resolve" in argv]
    assert resolves, "应当调用 resolve"
    assert all("--fields" not in argv for argv in resolves)
    assert all("--incoming-sha256" in argv for argv in resolves)
    assert "库已写入" in view.success or "已完成" in view.success


def test_g1_stage_detail_takes_all_diff_fields_not_checkboxes(tmp_path, monkeypatch):
    """G-1：界面不再假装逐字段勾选——staged 永远是整条冲突的 diff 字段。"""
    from ui import bib_review as BR

    view = BR.ReviewView(_review_page(tmp_path / DB_REL))
    row = {"no": "C001", "key": "k",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"},
                             "year": {"old": "2020", "new": "2021"}}}}
    view.all_rows = [row]
    view.selected_key = row["no"]
    view.adopt = {"pages": True, "year": False}
    view._stage_detail(False)
    assert view.staged["fields"] == ["pages", "year"], "必须整条冲突全部字段"


def test_g1_engine_resolve_adopt_rejects_fields_accepts_incoming_sha(tmp_path):
    """G-1：引擎层反证——旧 argv rc=2，新 argv rc=0 且 adopted_field_names。"""
    db = tmp_path / "x" / "db" / "bibliography.jsonl"
    db.parent.mkdir(parents=True)
    engine, proc = _engine_cli("init", "--db", str(db))
    assert proc.returncode == 0, proc.stderr
    a = tmp_path / "a.bib"
    b = tmp_path / "b.bib"
    a.write_text("@article{li2024analysis,\n  author = {Li, Ming},\n"
                 "  title = {Old Title},\n  journal = {J},\n  year = {2020}\n}\n",
                 encoding="utf-8")
    b.write_text("@article{li2024analysis,\n  author = {Li, Ming},\n"
                 "  title = {Old Title},\n  journal = {J},\n  year = {2020},\n"
                 "  pages = {1--10}\n}\n", encoding="utf-8")
    _engine_cli("import-bib", "--bib", str(a), "--db", str(db))
    _engine_cli("import-bib", "--bib", str(b), "--db", str(db))
    _, conflicts = _engine_cli("conflicts", "--db", str(db), "--pending-only", "--json")
    item = json.loads(conflicts.stdout)["items"][0]
    incoming = item["incoming_sha256"]
    _, old = _engine_cli("resolve", "--json", "--key", "li2024analysis", "--action",
                         "adopt", "--fields", "pages", "--incoming-sha256", incoming,
                         "--dry-run", "--db", str(db))
    assert old.returncode == 2, old.stderr
    assert ("mutually exclusive" in old.stderr
            or "not allowed with argument" in old.stderr), old.stderr
    _, new = _engine_cli("resolve", "--json", "--key", "li2024analysis", "--action",
                         "adopt", "--incoming-sha256", incoming, "--dry-run",
                         "--db", str(db))
    assert new.returncode == 0, new.stderr
    assert json.loads(new.stdout)["adopted_field_names"] == ["pages"]


def test_g2_engine_edit_json_fields_replaces_whole_dict(tmp_path):
    """G-2：引擎 --json-fields 是整份替换——旧写法丢字段，合并后整份传才安全。"""
    db = tmp_path / "g2" / "db" / "bibliography.jsonl"
    db.parent.mkdir(parents=True)
    _engine_cli("init", "--db", str(db))
    bib = tmp_path / "g2" / "a.bib"
    bib.write_text("@article{li2024analysis,\n  author = {Li, Ming and Wang, Fang},\n"
                   "  title = {Old Title},\n  journal = {Journal of Tests},\n"
                   "  year = {2020},\n  doi = {10.1000/x1}\n}\n", encoding="utf-8")
    _engine_cli("import-bib", "--bib", str(bib), "--db", str(db))
    _, q = _engine_cli("query", "--key", "li2024analysis", "--json", "--db", str(db))
    before = json.loads(q.stdout)["hits"][0]["record"]["fields"]
    assert {"author", "doi", "journal", "title", "year"} <= set(before)

    _, partial = _engine_cli("edit", "--key", "li2024analysis", "--json-fields",
                             json.dumps({"pages": "1--10"}), "--apply",
                             "--db", str(db), "--json")
    assert partial.returncode == 0, partial.stderr
    _, q2 = _engine_cli("query", "--key", "li2024analysis", "--json", "--db", str(db))
    after_partial = json.loads(q2.stdout)["hits"][0]["record"]["fields"]
    assert after_partial == {"pages": "1--10"}, "旧写法确实丢字段（复现用）"

    db.unlink()
    _engine_cli("init", "--db", str(db))
    _engine_cli("import-bib", "--bib", str(bib), "--db", str(db))
    merged = dict(before)
    merged["pages"] = "1--10"
    _, full = _engine_cli("edit", "--key", "li2024analysis", "--json-fields",
                          json.dumps(merged, ensure_ascii=False), "--apply",
                          "--db", str(db), "--json")
    assert full.returncode == 0, full.stderr
    _, q3 = _engine_cli("query", "--key", "li2024analysis", "--json", "--db", str(db))
    after_full = json.loads(q3.stdout)["hits"][0]["record"]["fields"]
    assert after_full == merged, "整份合并字段后，其它字段必须原样保留"


def test_g2_fieldfix_preview_sends_full_fields(tmp_path, monkeypatch):
    """G-2：字段归位页预览时先读记录完整 fields，再合并目标字段整份传出。"""
    from ui import bib_fieldfix as BFF

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    record_fields = {"author": "Li, Ming", "title": "Old Title",
                     "journal": "J", "year": "2020"}
    seen: list[list[str]] = []

    def fake_read(call, **kw):
        seen.append(list(call.argv))
        if "query" in call.argv:
            return {"ok": True, "json": {"hits": [{
                "key": "li2024analysis",
                "record": {"key": "li2024analysis", "fields": dict(record_fields)}}]},
                "code": 0}
        return {"ok": True, "json": {"db_sha256_after": "a" * 64}, "code": 0}

    monkeypatch.setattr(BFF.EB, "read_json_command", fake_read)
    view = BFF.BibFieldFixView(_review_page(db))
    view.rows = [{"id": "r1", "key": "li2024analysis", "field": "pages",
                  "candidate": "1--10", "entry_type": "article"}]
    view.checked = {"r1"}
    view.expect_sha = "a" * 64
    asyncio.run(view._preview())
    edits = [argv for argv in seen if "edit" in argv]
    assert edits, "应当调用 edit 预览"
    fields = json.loads(edits[0][edits[0].index("--json-fields") + 1])
    assert fields["pages"] == "1--10"
    for key, value in record_fields.items():
        assert fields[key] == value, f"字段 {key} 不得被整份替换删掉"


def test_g3_review_write_commits_db_and_only_db_file(tmp_path, monkeypatch):
    """G-3：待处理页写库成功后必须生成一条只含库文件的新提交。"""
    from ui import bib_review as BR

    repo = make_repo(tmp_path)
    db = repo / DB_REL
    state = {"sha": sha256_file(db)}

    def fake_read(call, **kw):
        argv = call.argv
        if "resolve" in argv and "--dry-run" not in argv:
            db.write_text(db.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            state["sha"] = sha256_file(db)
        if "resolve" in argv:
            return {"ok": True,
                    "json": {"written": "--dry-run" not in argv,
                             "db_sha256_after": state["sha"]}, "code": 0}
        return {"ok": True, "json": {}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    view = BR.ReviewView(_review_page(db))
    view.db_sha = state["sha"]
    row = {"no": "C001", "key": "li2024analysis",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"}}}}
    view.all_rows = [row]
    view.staged = {"kind": "detail", "no": row["no"], "row": row, "note": ""}
    before = commit_count(repo)
    asyncio.run(view._write())
    assert commit_count(repo) == before + 1, "库已写就必须生成版本提交"
    assert changed_paths(repo) == [DB_REL]
    assert "已提交" in view.success and "未推送" in view.success


def test_g3_review_write_without_commit_is_explicit(tmp_path, monkeypatch):
    """G-3：身份缺失等导致未提交时，页面必须明说「已写库但未生成提交」。"""
    from ui import bib_review as BR

    repo = make_repo(tmp_path)
    db = repo / DB_REL

    def fake_read(call, **kw):
        if "resolve" in call.argv:
            return {"ok": True, "json": {"written": "--dry-run" not in call.argv,
                                         "db_sha256_after": "c" * 64}, "code": 0}
        return {"ok": True, "json": {}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BR.GIT, "commit_db", lambda db, message: {
        "ok": False, "committed": False, "soft": True, "error": GIT.IDENTITY_ERROR})
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    row = {"no": "C001", "key": "li2024analysis",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"}}}}
    view.all_rows = [row]
    view.staged = {"kind": "detail", "no": row["no"], "row": row, "note": ""}
    asyncio.run(view._write())
    assert "库已写入" in view.success
    assert "没有生成版本提交" in view.success
    assert "已写库，未生成提交" in view.success
    assert commit_count(repo) == 1


def test_g4_review_batch_updates_expect_sha_and_ledger(tmp_path, monkeypatch):
    """G-4：批量写每条成功后更新 CAS；部分成功时报总账与原样原因。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    expects: list[str] = []
    counter = {"n": 0}
    state = {"sha": "a" * 64}

    def fake_read(call, **kw):
        argv = call.argv
        if "resolve" in argv:
            counter["n"] += 1
            expects.append(argv[argv.index("--expect-db-sha256") + 1])
            if counter["n"] == 3:
                return {"ok": False, "error": "模拟 CAS 失败：库指纹已变化", "code": 2}
            state["sha"] = ("b" if counter["n"] == 2 else state["sha"]) + "0" * 0
        return {"ok": True, "json": {"written": True,
                                     "db_sha256_after": state["sha"]}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    rows = []
    for i in range(1, 4):
        rows.append({"no": f"C{i:03d}", "key": f"k{i}",
                     "item": {"incoming_sha256": f"{i:02d}" * 32,
                              "diff": {"pages": {"old": "", "new": f"1--{i}0"}}}})
    view.all_rows = rows
    view.staged = {"kind": "batch", "rows": rows, "note": ""}
    asyncio.run(view._write())
    assert view.error.startswith("已写入 1 条（")
    assert "第 2 条预览失败：模拟 CAS 失败：库指纹已变化" in view.error
    assert view.success == "", "部分成功不得显示全成功"
    assert len(set(expects)) > 1, "写成功后必须用新的 db_sha256_after 更新 CAS"


def test_r2b_already_resolved_is_not_success_or_commit(tmp_path, monkeypatch):
    """R2b-1：此前已裁决（already=True, written=False）不得报「库已写入」或提交。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    commits: list[str] = []
    monkeypatch.setattr(BR.EB, "read_json_command", lambda call, **kw: {
        "ok": True,
        "json": {"written": False, "already": True,
                 "db_sha256_after": "a" * 64},
        "code": 0})
    monkeypatch.setattr(BR.GIT, "commit_db",
                        lambda db, message: commits.append(message) or {
                            "ok": True, "committed": True, "sha": "c" * 40})
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    row = {"no": "C001", "key": "li2024analysis",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"}}}}
    view.all_rows = [row]
    view.staged = {"kind": "detail", "no": row["no"], "row": row, "note": ""}
    asyncio.run(view._write())

    assert "没有需要写入的内容" in view.success
    assert "此前已裁决过" in view.success
    assert "库已写入" not in view.success
    assert view.error == ""
    assert commits == [], "无实际写入时不得调用 commit_db"
    assert not (tmp_path / "notes" / ".git").exists(), "无写入场景不应走到真实提交"
    events = [fields for name, fields in view.page.events
              if name == "bib_review_write"]
    assert events and events[-1].get("committed") is False


def test_r2b_batch_partial_failure_commits_written_part(tmp_path, monkeypatch):
    """R2b-2：批量第 2 行失败时，第 1 行已写部分仍提交，文案两段式。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    commits: list[str] = []
    counter = {"n": 0}

    def fake_read(call, **kw):
        counter["n"] += 1
        if counter["n"] == 3:
            return {"ok": False, "error": "模拟 CAS 失败：库指纹已变化", "code": 2}
        if counter["n"] == 2:
            return {"ok": True,
                    "json": {"written": True, "db_sha256_after": "b" * 64},
                    "code": 0}
        return {"ok": True, "json": {"written": False}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BR.GIT, "commit_db",
                        lambda db, message: commits.append(message) or {
                            "ok": True, "committed": True, "sha": "c" * 40})
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    rows = [{"no": "C001", "key": "k1",
             "item": {"incoming_sha256": "b" * 64,
                      "diff": {"pages": {"old": "", "new": "1--10"}}}},
            {"no": "C002", "key": "k2",
             "item": {"incoming_sha256": "b" * 64,
                      "diff": {"pages": {"old": "", "new": "2--20"}}}}]
    view.all_rows = rows
    view.staged = {"kind": "batch", "rows": rows, "note": ""}
    asyncio.run(view._write())

    assert view.success == "", "部分成功不得显示全成功"
    assert len(commits) == 1, "已写部分必须尝试生成提交"
    assert view.commit and view.commit.get("committed") is True
    assert view.error.startswith("已写入 1 条（已提交 cccccccc）")
    assert "第 2 条预览失败：模拟 CAS 失败：库指纹已变化" in view.error
    assert view.db_sha == "b" * 64, "已写入后必须更新页面 CAS 基准"


def test_g5_import_counts_read_nested_report():
    """G-5：入藏计数必须按 report（import-bib）口径取，不再读顶层。"""
    from ui import bib_import as BI

    page = _FakePage(Path(""))
    page.stats_data = {"db": ""}
    view = BI.BibImportView(page)
    view.payload = {"report": {"entries": 3, "new": 2, "updated": 1,
                               "merged": 0, "conflicts": 1,
                               "skipped": {"duplicate": 2}},
                    "written": True}
    counts = view._counts()
    assert counts["new"] == 2 and counts["updated"] == 1
    assert counts["conflicts"] == 1 and counts["skipped"] == 2


def test_g6_import_written_false_is_not_success(tmp_path, monkeypatch):
    """G-6：来源已入藏/内容相同（written=false）不得进 success、不得提交。"""
    from ui import bib_import as BI

    payload = {"written": False, "report": {"entries": 1, "new": 0, "updated": 0,
                                            "merged": 0, "conflicts": 0,
                                            "skipped": {"duplicate": 1}}}
    commits: list[str] = []

    def fake_read(call, **kw):
        return {"ok": True, "json": payload, "code": 0}

    monkeypatch.setattr(BI.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BI.GIT, "commit_db",
                        lambda db, message: commits.append(message) or {
                            "ok": True, "committed": True, "sha": "c" * 40})
    page = _FakePage(Path(""))
    page.stats_data = {"db": ""}
    view = BI.BibImportView(page)
    view.mode = "bib"
    view.path = "fixture.bib"
    view.payload = payload
    view.expect_sha = "e" * 64
    view.state = "ready"
    asyncio.run(view._write())
    assert view.state == "nothing", "无实际写入不得进 success 样式"
    assert commits == [], "无实际写入不得生成提交"
    assert "没有需要写入的内容" in view.no_write_note
    assert "没有需要写入的内容" in page.app.toasts[-1][0]
    assert view._can_confirm() is False, "nothing 状态下不得再弹确认条"

def test_p07v13_review_failure_keeps_engine_error_from_json(tmp_path, monkeypatch):
    """P07V-13：--json 失败时原因在 json.error，必须原样上报而不是「没有返回说明」。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    cas_msg = "库 sha256 与 --expect-db-sha256 不符（本次未写库）"
    commits: list[str] = []
    counter = {"n": 0}

    def fake_read(call, **kw):
        counter["n"] += 1
        if counter["n"] == 4:  # 第 2 条真写失败
            return {"ok": False, "code": 2,
                    "json": {"error": cas_msg},
                    "stderr": "✗ " + cas_msg, "code": 2}
        if counter["n"] == 2:  # 第 1 条真写成功
            return {"ok": True,
                    "json": {"written": True, "db_sha256_after": "b" * 64},
                    "code": 0}
        return {"ok": True, "json": {"written": False}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BR.GIT, "commit_db",
                        lambda db, message: commits.append(message) or {
                            "ok": True, "committed": True, "sha": "c" * 40})
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    rows = [{"no": "C001", "key": "k1",
             "item": {"incoming_sha256": "b" * 64,
                      "diff": {"pages": {"old": "", "new": "1--10"}}}},
            {"no": "C002", "key": "k2",
             "item": {"incoming_sha256": "b" * 64,
                      "diff": {"pages": {"old": "", "new": "2--20"}}}}]
    view.all_rows = rows
    view.staged = {"kind": "batch", "rows": rows, "note": ""}
    asyncio.run(view._write())

    assert "第 2 条写入失败" in view.error
    assert cas_msg in view.error
    assert "引擎没有返回说明" not in view.error
    # 第 1 条已写部分的提交行为不变
    assert len(commits) == 1
    assert view.commit and view.commit.get("committed") is True
    assert view.error.startswith("已写入 1 条（已提交 cccccccc）")


def test_p07v13_review_failure_falls_back_to_stderr(tmp_path, monkeypatch):
    """P07V-13：payload 为 None 且无 result.error 时回退 stderr，而不是空说明。"""
    from ui import bib_review as BR

    db = tmp_path / "notes" / DB_REL
    db.parent.mkdir(parents=True)
    db.write_text(BIB1, encoding="utf-8")
    counter = {"n": 0}

    def fake_read(call, **kw):
        counter["n"] += 1
        if counter["n"] == 2:
            return {"ok": False, "code": 3, "json": None,
                    "stderr": "✗ 磁盘已满\n其余输出"}
        return {"ok": True, "json": {"written": False}, "code": 0}

    monkeypatch.setattr(BR.EB, "read_json_command", fake_read)
    view = BR.ReviewView(_review_page(db))
    view.db_sha = "a" * 64
    row = {"no": "C001", "key": "k1",
           "item": {"incoming_sha256": "b" * 64,
                    "diff": {"pages": {"old": "", "new": "1--10"}}}}
    view.all_rows = [row]
    view.staged = {"kind": "detail", "no": row["no"], "row": row, "note": ""}
    asyncio.run(view._write())

    assert "第 1 条写入失败" in view.error
    assert "磁盘已满" in view.error
    assert "退出码 3" not in view.error
    assert "引擎没有返回说明" not in view.error
