"""B-246c P6 写库单元测试：命令拼装 / JSON 解析 / 冲突动作 / git 状态解析 / sha 闸。

纪律：不依赖真实 GUI、真实库；全部在 tmp_path 里造数据（真实库零写入）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os

import pytest

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_write as BW


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式（与 test_engine_bridge 同款）。"""
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


# ── 命令拼装（写库一律 --json；预览只 dry-run）─────────────────────────
def test_import_preview_argv(engine):
    args = args_of(EB.bibdb_import_call(bib="a.bib"))
    assert args[:2] == ["import-bib", "--json"]
    assert args[args.index("--bib") + 1] == "a.bib"
    assert "--dry-run" in args
    assert "--expect-db-sha256" not in args          # 预览不带 sha 闸
    assert "--force" not in args and "--verified" not in args


def test_import_write_argv(engine):
    sha = "a" * 64
    args = args_of(EB.bibdb_import_call(bib="a.bib", dry_run=False, force=True,
                                        verified=True, expect_db_sha256=sha))
    assert "--dry-run" not in args
    assert args[args.index("--expect-db-sha256") + 1] == sha
    assert "--force" in args and "--verified" in args


def test_import_dir_argv(engine):
    preview = args_of(EB.bibdb_import_dir_call(root="D:\\issue", through_year=2026,
                                               finished_only=True))
    assert preview[:2] == ["import-dir", "--json"]
    assert preview[preview.index("--root") + 1] == "D:\\issue"
    assert preview[preview.index("--through-year") + 1] == "2026"
    assert "--finished-only" in preview and "--dry-run" in preview
    write = args_of(EB.bibdb_import_dir_call(root="D:\\issue", dry_run=False,
                                             expect_db_sha256="b" * 64))
    assert "--dry-run" not in write
    assert write[write.index("--expect-db-sha256") + 1] == "b" * 64


def test_conflicts_argv(engine):
    assert args_of(EB.bibdb_conflicts_call()) == ["conflicts", "--json", "--pending-only"]
    assert args_of(EB.bibdb_conflicts_call(pending_only=False)) == ["conflicts", "--json"]


def test_resolve_argv(engine):
    args = args_of(EB.bibdb_resolve_call(
        key="zhang2020", action="adopt", fields="title,year", incoming_sha256="c" * 64,
        expect_db_sha256="d" * 64))
    assert args[:2] == ["resolve", "--json"]
    assert args[args.index("--key") + 1] == "zhang2020"
    assert args[args.index("--action") + 1] == "adopt"
    assert args[args.index("--fields") + 1] == "title,year"
    assert args[args.index("--incoming-sha256") + 1] == "c" * 64
    assert args[args.index("--expect-db-sha256") + 1] == "d" * 64
    assert "--all-fields" not in args and "--dry-run" not in args


def test_resolve_adopt_all_argv(engine):
    args = args_of(EB.bibdb_resolve_call(adopt_all_pending=True, expect_db_sha256="e" * 64))
    assert "--adopt-all-pending" in args
    for flag in ("--key", "--action", "--incoming-sha256", "--fields"):
        assert flag not in args
    assert args[args.index("--expect-db-sha256") + 1] == "e" * 64


# ── 入藏 payload 解析（两种 payload 同口径）───────────────────────────
def test_import_counts_bib_payload():
    payload = {"report": {"entries": 12, "new": 3, "updated": 1, "merged": 2,
                          "conflicts": 4, "skipped": {"duplicate": 5}},
               "changes": []}
    assert BW.import_counts(payload) == {"entries": 12, "new": 3, "updated": 1,
                                         "merged": 2, "conflicts": 4, "skipped": 5}
    assert BW.import_counts({})["entries"] == 0
    assert BW.import_counts(None)["new"] == 0


def test_import_counts_dir_payload():
    payload = {"result": {"total": {"entries": 30, "new": 9, "updated": 0,
                                    "merged": 1, "conflicts": 2},
                          "warnings": ["w-dir"]},
               "stats": {"records": 100}}
    counts = BW.import_counts(payload)
    assert counts["new"] == 9 and counts["entries"] == 30 and counts["conflicts"] == 2


def test_import_warnings_dedupe_and_limit():
    payload = {"report": {"warnings": ["w1", "w1", "w2"]},
               "result": {"warnings": ["w2", "w3", "w4"]}}
    assert BW.import_warnings(payload) == ["w1", "w2", "w3", "w4"]
    assert BW.import_warnings(payload, limit=2) == ["w1", "w2"]
    assert BW.import_warnings({}) == []


def test_change_rows_and_describe_change():
    payload = {"changes": [
        {"key": "a2020", "status": "new", "title": "题名", "author": "张三", "year": "2020"},
        {"key": "b2021", "status": "merged"},
        "坏行",
    ]}
    rows = BW.change_rows(payload)
    assert len(rows) == 2
    assert BW.describe_change(rows[0]) == "[新增] 题名（张三 2020） · a2020"
    assert BW.describe_change(rows[1]) == "[并来源] b2021 · b2021"
    assert BW.describe_change({"status": "conflict", "title": "T"}) == "[冲突] T"
    assert BW.change_rows({}) == []


# ── 冲突条目：动作映射 / 字段差异展示 ────────────────────────────────
def test_action_label_mapping():
    assert set(BW.ACTION_LABEL) == {"keep", "defer", "adopt", "replace"}
    assert BW.ACTION_LABEL["keep"] == "保留旧值"
    assert BW.ACTION_LABEL["adopt"] == "采用新稿值"


def test_conflict_helpers():
    item = {"stable_no": 7, "record_key": "zhang2020", "title": "",
            "entry_type": "article", "issue": "2026年第1期", "slug": "01 A",
            "incoming_sha256": "f" * 64,
            "diff": {"title": {"old": "旧题", "new": "新题"},
                     "pages": {"old": "1-2", "remove": True},
                     "doi": {"old": "", "new": "10.1000/x"}}}
    assert BW.conflict_where(item) == "2026年第1期/01 A"
    assert BW.conflict_title(item) == "zhang2020"
    assert BW.conflict_fields(item) == ["title", "pages", "doi"]
    lines = BW.conflict_diff_lines(item, limit=2)
    assert lines[0] == "title：旧题 → 新题"
    assert lines[1] == "pages：1-2 → （新稿无此字段）"
    assert lines[-1] == "… 另有 1 个字段差异"
    assert BW.conflict_diff_lines({}) == []


def test_explain_result_prefers_json_error():
    assert BW.explain_result({"json": {"error": "库 sha256 不符"}, "code": 2}) == \
        "库 sha256 不符（退出码 2）"
    assert BW.explain_result({"json": None, "lines": ["x", "✗ 需要完整 sha"], "code": 2}) == \
        "需要完整 sha（退出码 2）"
    assert BW.explain_result({"json": None, "lines": [], "error": "启动失败"}) == "启动失败"


def test_sha_short_and_git_summary_lines():
    assert BW.sha_short("abcdef0123456789", 8) == "abcdef01…"
    assert BW.sha_short("") == "（空）"
    lines = BW.git_summary_lines({"ok": True, "branch": "main", "upstream": "origin/main",
                                  "ahead": 2, "behind": 0, "dirty_db": True,
                                  "dirty_other": 1, "identity": "A <a@b>",
                                  "commits": [{"sha": "a" * 40, "subject": "引用库：入藏"}]})
    assert any("分支 main" in line for line in lines)
    assert any("未推送 2" in line for line in lines)
    assert any("有未提交改动" in line for line in lines)
    assert BW.git_summary_lines({"ok": False, "error": "找不到 git"}) == ["找不到 git"]


# ── git 版本层解析（纯函数）───────────────────────────────────────────
def _rec(sha: str, parents: str, subject: str, paths: list[str]) -> str:
    return ("\x1e" + sha + "\x1f" + parents + "\x1f" + subject + "\n"
            + "".join(path + "\n" for path in paths))


def test_commit_and_revert_message():
    assert GIT.commit_message("入藏", 5, "a" * 40, "b" * 40) == \
        "引用库：入藏（记录 5 条；sha aaaaaaaaaaaa→bbbbbbbbbbbb）"
    assert GIT.commit_message("", 0, "", "") == "引用库：写库（记录 0 条；sha →）"
    assert GIT.commit_message("入藏", 2, "a" * 40, "b" * 40, note="note").endswith("\n备注：note")
    assert GIT.revert_message("a" * 40, "") == "引用库：回退到上一个提交（sha aaaaaaaaaaaa→）"


def test_parse_log_oneline_limit():
    rows = GIT.parse_log_oneline("abc1234 引用库：入藏\n\ndeadbee 引用库：裁决\n", limit=1)
    assert rows == [{"sha": "abc1234", "subject": "引用库：入藏"}]
    assert GIT.parse_log_oneline("") == []


def test_parse_push_log_and_plan_push():
    first = _rec("a" * 40, "p" * 40, "引用库：入藏", ["bib_db/bibliography.jsonl"])
    second = _rec("b" * 40, "a" * 40, "随手记", ["cases/62+.md"])
    commits = GIT.parse_push_log(first + second)
    assert [row["sha"] for row in commits] == ["a" * 40, "b" * 40]
    assert commits[0]["paths"] == ["bib_db/bibliography.jsonl"]
    assert GIT.first_non_bib_path(commits[1]) == "cases/62+.md"

    plan = GIT.plan_push(commits)
    assert plan["kind"] == "pushable" and plan["count"] == 1
    assert plan["tip"]["sha"] == "a" * 40
    assert [row["sha"] for row in plan["remainder"]] == ["b" * 40]

    blocked = GIT.plan_push([commits[1]])
    assert blocked["kind"] == "blocked" and blocked["path"] == "cases/62+.md"
    assert GIT.plan_push([]) == {"kind": "nothing"}


def test_is_merge_bib_only():
    merge = {"sha": "c" * 40, "parents": ["x" * 40, "y" * 40], "subject": "m",
             "paths": ["bib_db/bibliography.jsonl"]}
    assert GIT.is_merge(merge) and not GIT.is_bib_only(merge)
    assert GIT.is_bib_only({"paths": ["bib_db/bibliography.jsonl"]})
    assert GIT.is_bib_only({"paths": []})
    assert not GIT.is_bib_only({"paths": ["bib_db/x", "cases/y"]})
    assert GIT.is_bib_path("bib_db/bibliography.jsonl")
    assert not GIT.is_bib_path("cases/62+.md")


def test_proxy_failure_and_upstream():
    assert GIT.looks_like_proxy_failure("fatal: Could not resolve proxy: 127.0.0.1")
    assert GIT.looks_like_proxy_failure("Failed to connect to 127.0.0.1 port 7890")
    assert not GIT.looks_like_proxy_failure("fatal: repository not found")
    assert GIT.parse_upstream("origin/main\n") == ("origin", "main")
    assert GIT.parse_upstream("origin/feature/x") == ("origin", "feature/x")
    assert GIT.parse_upstream("fatal: no upstream configured for branch 'main'") is None
    assert GIT.parse_upstream("") is None


def test_notes_root_and_default_db(tmp_path, monkeypatch):
    notes = tmp_path / "notes"
    (notes / "cases").mkdir(parents=True)
    monkeypatch.delenv("SLCN_NOTES", raising=False)
    assert GIT.notes_root(str(notes)) == os.path.realpath(str(notes))
    assert GIT.default_db(str(notes)).endswith(os.path.join("bib_db", "bibliography.jsonl"))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert GIT.notes_root(str(plain)) != os.path.realpath(str(plain))


def test_file_sha256_and_commit_guard(tmp_path):
    db = tmp_path / "bibliography.jsonl"
    db.write_bytes(b"abc")
    assert GIT.file_sha256(str(db)) == hashlib.sha256(b"abc").hexdigest()
    assert GIT.file_sha256(str(tmp_path / "missing")) == ""

    result = GIT.commit_db(str(db), "")
    assert result["ok"] is False and result["committed"] is False and result["soft"] is True
    assert "message 为空" in result["error"]


def test_status_and_push_degrade_without_repo(tmp_path):
    missing = str(tmp_path / "nope" / "bibliography.jsonl")
    probe = GIT.status(missing)
    assert probe["ok"] is False and probe["error"]
    assert GIT.push(missing)["ok"] is False
    assert GIT.rollback(missing)["ok"] is False
    assert GIT.commit_db(missing, "x")["ok"] is False


# ── 待裁决写库后的自动提交（P6 返工）───────────────────────────────────
class _FakeApp:
    def __init__(self) -> None:
        self.notes_repo = ""
        self.toasts: list[str] = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _FakePage:
    """只实现 BibReviewView 用到的成员；不碰 flet、不碰真实库。"""

    def __init__(self, db: str = "") -> None:
        self.app = _FakeApp()
        self.stats_data = {"db": db} if db else None
        self.events: list[tuple[str, dict]] = []
        self.renders = 0

    @property
    def t(self):
        return None

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def _render(self) -> None:
        self.renders += 1


def _review_page(tmp_path) -> tuple[BW.BibReviewView, _FakePage]:
    db = tmp_path / "bib_db" / "bibliography.jsonl"
    db.parent.mkdir()
    db.write_text("", encoding="utf-8")
    page = _FakePage(str(db))
    return BW.BibReviewView(page), page


def _install_stubs(monkeypatch, *, resolve_payload: dict, commits: list) -> None:
    resolve_result = {"ok": True, "json": resolve_payload, "code": 0}
    list_result = {"ok": True, "json": {"items": [], "totals": {}, "db_sha256": ""}, "code": 0}

    def fake_read(call, notes=""):
        return resolve_result if "resolve" in list(getattr(call, "argv", [])) else list_result

    def fake_commit(db: str, message: str) -> dict:
        commits.append((db, message))
        return {"ok": True, "committed": True, "sha": "c" * 40, "message": message}

    monkeypatch.setattr(BW.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BW.GIT, "commit_db", fake_commit)
    monkeypatch.setattr(BW.GIT, "status", lambda *a, **k: {"ok": True})


def test_resolve_success_commits_once(tmp_path, monkeypatch):
    view, page = _review_page(tmp_path)
    db = page.stats_data["db"]
    commits: list = []
    _install_stubs(monkeypatch, commits=commits, resolve_payload={
        "ok": True, "db": db, "written": True, "count": 1,
        "db_sha256_before": "a" * 64, "db_sha256_after": "b" * 64,
    })
    asyncio.run(view._resolve(key="k1", action="adopt", incoming_sha256="f" * 64))
    assert len(commits) == 1
    assert commits[0][0] == db
    assert "裁决 采用新稿值" in commits[0][1]
    assert "记录 1 条" in commits[0][1]
    assert view.commit and view.commit["committed"] is True
    assert any(event == "bib_git_commit" for event, _ in page.events)
    assert any("已生成版本提交" in text for text in page.app.toasts)


def test_adopt_all_merges_into_one_commit(tmp_path, monkeypatch):
    view, page = _review_page(tmp_path)
    db = page.stats_data["db"]
    commits: list = []
    _install_stubs(monkeypatch, commits=commits, resolve_payload={
        "ok": True, "db": db, "written": True, "records": 4,
        "adopted": [{"key": f"k{index}"} for index in range(4)],
        "db_sha256_before": "a" * 64, "db_sha256_after": "b" * 64,
    })
    asyncio.run(view._resolve(adopt_all_pending=True))
    assert len(commits) == 1
    assert "一键采用 4 条" in commits[0][1]
    assert "记录 4 条" in commits[0][1]


def test_resolve_rejected_sha_does_not_commit(tmp_path, monkeypatch):
    view, page = _review_page(tmp_path)
    commits: list = []
    resolve_result = {"ok": False, "code": 2,
                      "json": {"ok": False, "error": "库 sha256 与预览时不一致"}}
    list_result = {"ok": True, "json": {"items": [], "totals": {}, "db_sha256": ""}, "code": 0}

    def fake_read(call, notes=""):
        return resolve_result if "resolve" in list(getattr(call, "argv", [])) else list_result

    monkeypatch.setattr(BW.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BW.GIT, "commit_db", lambda db, message: commits.append((db, message)))
    asyncio.run(view._resolve(key="k1", action="adopt"))
    assert commits == []
    assert any(event == "bib_resolve" and fields.get("ok") is False
               for event, fields in page.events)
    assert view.commit is None
