"""批 109 W1b：写库高级能力（编辑 / 查重合并 / 字段归位 / 库健康 / 批量裁决 dry-run）。

纪律（与 `test_bib_write.py` 同款）：不碰真实库、不碰真实 GUI。命令拼装只看 argv；
视图层用桩 `read_json_command` + 桩 `GIT.commit_db`；真实库的 sha 只读校验放在最后。
"""
from __future__ import annotations

import asyncio

import pytest

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_ops as BO
from ui import bib_ops_health as BOH


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


# ── 命令拼装：预览只 dry-run；真写一律 --apply + sha 闸 ──────────────────
def test_edit_preview_argv(engine):
    import json

    fields = {"title": "新题名", "author": "张", "year": "2020"}
    args = args_of(EB.bibdb_edit_call(key="zhang2020", fields=fields))
    assert args[:2] == ["edit", "--json"]
    assert args[args.index("--key") + 1] == "zhang2020"
    assert json.loads(args[args.index("--json-fields") + 1]) == fields
    assert "--dry-run" in args and "--apply" not in args
    assert "--expect-db-sha256" not in args          # 预览不带 sha 闸


def test_edit_apply_argv(engine):
    import json

    sha = "a" * 64
    args = args_of(EB.bibdb_edit_call(
        key="zhang2020", fields={"title": "新题名"}, dry_run=False,
        expect_db_sha256=sha, new_key="zhang2020b", entry_type="book", note="归位"))
    assert "--apply" in args and "--dry-run" not in args
    assert args[args.index("--expect-db-sha256") + 1] == sha
    assert args[args.index("--new-key") + 1] == "zhang2020b"
    assert args[args.index("--entry-type") + 1] == "book"
    # `--json-fields` 是整份字段字典（引擎整份替换，不是 patch）
    assert json.loads(args[args.index("--json-fields") + 1]) == {"title": "新题名"}


def test_merge_argv(engine):
    preview = args_of(EB.bibdb_merge_call(
        keys=["k1", "k2"], new_key="k1", entry_type="book",
        field_sources={"title": "k2", "year": "k1"}))
    assert preview[:2] == ["merge-records", "--json"]
    assert preview[preview.index("--keys") + 1] == "k1,k2"
    assert preview.count("--field-source") == 2
    assert "--dry-run" in preview and "--apply" not in preview
    write = args_of(EB.bibdb_merge_call(keys=["k1", "k2"], dry_run=False,
                                        expect_db_sha256="b" * 64))
    assert "--apply" in write and "--dry-run" not in write
    assert write[write.index("--expect-db-sha256") + 1] == "b" * 64


def test_suggest_fix_argv(engine):
    args = args_of(EB.bibdb_suggest_fixes_call(registry="missing_field", limit=25))
    assert args == ["suggest-field-fixes", "--json", "--registry", "missing_field",
                    "--limit", "25"]
    assert args_of(EB.bibdb_suggest_fixes_call(limit=0)) == [
        "suggest-field-fixes", "--json", "--registry", "missing_field"]


def test_report_duplicates_argv(engine):
    args = args_of(EB.bibdb_report_duplicates_call(out="D:\\tmp\\b109-dupes"))
    assert args[:2] == ["report-duplicates", "--json"]
    assert args[args.index("--out") + 1] == "D:\\tmp\\b109-dupes"


def test_ack_argv(engine):
    preview = args_of(EB.bibdb_ack_duplicates_call(
        code="near_duplicates", keys=["k1", "k2"], reason="同一本书"))
    assert preview[:2] == ["ack-duplicates", "--json"]
    assert preview[preview.index("--code") + 1] == "near_duplicates"
    assert preview[preview.index("--keys") + 1] == "k1,k2"
    assert preview[preview.index("--reason") + 1] == "同一本书"
    assert "--dry-run" in preview and "--apply" not in preview
    write = args_of(EB.bibdb_ack_duplicates_call(keys=["k1", "k2"], dry_run=False))
    assert "--apply" in write and "--dry-run" not in write


def test_lint_argv(engine):
    assert args_of(EB.bibdb_lint_call()) == ["lint", "--json"]
    assert args_of(EB.bibdb_lint_call(with_records=True)) == [
        "lint", "--json", "--with-records"]


def test_edit_argv_uses_db_not_bib(engine):
    """库路径只能走 `--db`；`--bib` 在 edit 里是「从 .bib 读字段」，与 --json-fields 互斥。"""
    args = args_of(EB.bibdb_edit_call(key="k1", fields={"title": "x"},
                                      db="D:\\tmp\\notes\\bib_db\\bibliography.jsonl"))
    assert "--bib" not in args
    assert args[args.index("--db") + 1] == "D:\\tmp\\notes\\bib_db\\bibliography.jsonl"
    for call in (EB.bibdb_lint_call(db="D:\\db.jsonl"),
                 EB.bibdb_suggest_fixes_call(db="D:\\db.jsonl"),
                 EB.bibdb_report_duplicates_call(out="D:\\o", db="D:\\db.jsonl"),
                 EB.bibdb_ack_duplicates_call(keys=["a", "b"], db="D:\\db.jsonl"),
                 EB.bibdb_merge_call(keys=["a", "b"], db="D:\\db.jsonl")):
        argv = args_of(call)
        assert "--bib" not in argv and "D:\\db.jsonl" in argv


# ── 纯函数：解析与文案 ─────────────────────────────────────────────────
CSV_TEXT = ("\ufeffpair_no,similarity,key_a,key_b,title_a,title_b,author_a,author_b,"
            "year_a,year_b,entry_type_a,entry_type_b,issues_a,issues_b,verified_a,verified_b\n"
            "1,1.0000,k1,k2,题名甲,题名乙,张,张,1971,1971,book,phdthesis,,,\u662f,\u5426\n"
            "2,0.9312,k3,,题名丙,,李,,2001,,book,,,,\u662f,\n")


def test_duplicate_rows_parse_csv():
    rows = BO.duplicate_rows(CSV_TEXT)
    assert len(rows) == 1                       # 缺 key_b 的行被过滤
    row = rows[0]
    assert row["key_a"] == "k1" and row["key_b"] == "k2"
    assert row["similarity"] == "1.0000"
    assert BO.duplicate_pair_title(row) == "k1 ≈ k2（相似度 1.0000）"
    lines = BO.duplicate_pair_lines(row)
    assert lines[0].startswith("A：题名甲 · 张 · 1971 · book · 已核实 是")
    assert lines[1].startswith("B：题名乙 · 张 · 1971 · phdthesis · 已核实 否")
    assert BO.duplicate_rows("") == []


def test_merge_preview_lines():
    payload = {"keys": ["k1", "k2"], "new_key": "k1", "entry_type": "book",
               "field_comparison": {"title": {"chosen": "k2", "value": "题名乙",
                                              "k1": "题名甲", "k2": "题名乙"}}}
    lines = BO.merge_preview_lines(payload)
    assert lines[0] == "合并 k1、k2 → k1（book）"
    assert "题名 ← k2：题名乙" in lines[1]
    assert "k1=题名甲" in lines[1] and "k2=题名乙" in lines[1]


def test_health_counts_from_lint():
    lint = {"counts": {
        "records": 3883, "verified": 3857, "unverified": 26, "errors": 0, "warnings": 76,
        "conflicts": {"records": 135, "total": 157},
        "conflicts_resolved": {"records": 135, "total": 157},
        "near_duplicates": 0, "near_duplicates_acknowledged": 2,
        "needs_inversion": {"author": 18, "editor": 21, "institution": 4}}}
    counts = BO.health_counts(lint, {"totals": {"pending": 0, "resolved": 157, "total": 157}})
    assert counts["records"] == 3883 and counts["pending"] == 0
    assert counts["resolved"] == 157 and counts["conflicts_total"] == 157
    assert counts["invert_author"] == 18 and counts["invert_institution"] == 4
    lines = BO.health_lines(counts)
    assert "记录 3883 条：已核实 3857，未核实 26" in lines[0]
    assert "待裁决 0，已裁决 157" in lines[2]
    assert "近似重复 0 对（已确认非重复 2）" in lines[3]
    # 真实库待裁决为 0：批量按钮必须优雅降级（这里只核对计数口径）
    assert BO.health_counts({})["records"] == 0


def test_batch_preview_lines_zero_and_changed():
    zero = {"changed": False, "records": 0, "conflicts": 0, "adopted": [],
            "totals_before": {"pending": 0}, "totals_after": {"pending": 0},
            "db_sha256_before": "a" * 64, "db_sha256_after": "a" * 64}
    lines = BO.batch_preview_lines(zero)
    assert lines[0] == "待裁决 0 条 → 0 条"
    assert lines[-1] == "本次没有可采用的待裁决冲突（库与预览一致，零写入）。"
    changed = {"changed": True, "records": 3, "conflicts": 4,
               "adopted": [{"key": "k1"}, {"key": "k2"}], "skipped": [{"key": "k3"}],
               "totals_before": {"pending": 4}, "totals_after": {"pending": 2},
               "db_sha256_before": "a" * 64, "db_sha256_after": "b" * 64}
    lines = BO.batch_preview_lines(changed)
    assert lines[0] == "待裁决 4 条 → 2 条"
    assert "受影响记录 3 条、冲突 4 条（采用 2 条）" in lines[1]
    assert any(line.startswith("跳过 1 条") for line in lines)


def test_field_fix_fields_keeps_full_dict():
    row = {"key": "k1", "entry_type": "book", "fields": {"title": "题名", "year": "1971"},
           "missing": ["publisher"], "candidates": [
               {"field": "publisher", "value": "Springer", "source": "同类条目", "risk": "低"}]}
    assert BO.field_fix_missing(row) == ["publisher"]
    assert BO.field_fix_candidates(row, "publisher")[0]["value"] == "Springer"
    # 提交给 edit 的字段必须是「原字段 + 选中候选」的完整字典（引擎整份替换）
    fields = BO.field_fix_fields(row, {"publisher": "Springer", "address": "  "})
    assert fields == {"title": "题名", "year": "1971", "publisher": "Springer"}
    assert BO.field_fix_line(row, {"publisher": "Springer"}) == \
        "k1（book）：出版社＝Springer"


def test_edit_validation_and_diff():
    assert "缺条目 key" in BO.edit_validation_error("", {"title": "x"})
    assert "所有字段都空着" in BO.edit_validation_error("k1", {})
    assert BO.edit_validation_error("k1", {"title": "x"}) == ""
    lines = BO.edit_diff_lines({"title": {"old": "旧题", "new": "新题"},
                                "year": {"old": None, "new": "1971"}})
    assert lines[0] == "题名：旧题 → 新题"
    assert lines[1] == "年份：（空） → 1971"


def test_ops_notice_paths():
    assert BO.ops_notice("编辑已写入", {"written": False}, {}) == \
        "编辑已写入（库文件没有改动，未生成版本提交）"
    text = BO.ops_notice("编辑已写入", {"written": True},
                         {"committed": True, "sha": "abcdef0123456789"})
    assert text == "编辑已写入；已生成版本提交 abcdef01"
    text = BO.ops_notice("编辑已写入", {"written": True}, {"committed": False, "error": "没有 git"})
    assert text == "编辑已写入；版本层提示：没有 git"


# ── 视图：预览不发写、写成功只提交 1 条、失败不提交 ─────────────────────
class _FakeApp:
    def __init__(self) -> None:
        self.notes_repo = ""
        self.toasts: list[str] = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _FakePage:
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


def _db_path(tmp_path) -> str:
    db = tmp_path / "bib_db" / "bibliography.jsonl"
    db.parent.mkdir()
    db.write_text("", encoding="utf-8")
    return str(db)


def test_editor_preview_then_write_commits_once(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    page = _FakePage(db)
    view = BO.BibEditorView(page)
    calls: list[list[str]] = []
    commits: list[tuple[str, str]] = []

    def fake_read(call, notes=""):
        argv = list(getattr(call, "argv", []))
        calls.append(argv)
        if "edit" in argv:
            if "--dry-run" in argv:
                return {"ok": True, "code": 0, "json": {
                    "command": "edit", "dry_run": True, "written": False, "key": "k1",
                    "db": db, "db_sha256_before": "a" * 64, "db_sha256_after": "c" * 64,
                    "field_changes": {"title": {"old": "旧", "new": "新"}}}}
            assert "--apply" in argv                      # 真写必须 --apply（不是省掉 --dry-run）
            return {"ok": True, "code": 0, "json": {
                "command": "edit", "dry_run": False, "written": True, "key": "k1",
                "new_key": "k1", "db": db,
                "db_sha256_before": "a" * 64, "db_sha256_after": "b" * 64}}
        return {"ok": True, "code": 0, "json": {"hits": [{"record": {
            "key": "k1", "entry_type": "book", "fields": {"title": "旧", "year": "1971"}}}]}}

    monkeypatch.setattr(BO.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BO.GIT, "commit_db",
                        lambda path, message: (commits.append((path, message)) or
                                               {"ok": True, "committed": True, "sha": "d" * 40}))

    view.key_text = "k1"
    asyncio.run(view._load())
    assert view.record.get("key") == "k1" and view.entry_type == "book"
    view.field_values["title"] = "新"
    asyncio.run(view._preview())
    assert view.pending and view.pending["expect_db_sha256"] == "a" * 64
    assert commits == []                                   # 预览绝不写、不提交

    asyncio.run(view._write())
    assert len(commits) == 1 and commits[0][0] == db
    assert "编辑 k1" in commits[0][1]
    edit_writes = [argv for argv in calls if "edit" in argv and "--apply" in argv]
    assert edit_writes and "a" * 64 in edit_writes[0]
    assert view.commit and view.commit["committed"] is True
    assert any(event == "bib_git_commit" for event, _ in page.events)


def test_editor_apply_failure_does_not_commit(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    page = _FakePage(db)
    view = BO.BibEditorView(page)
    view.pending = {"key": "k1", "fields": {"title": "新"}, "dry_run": False,
                    "expect_db_sha256": "a" * 64}
    commits: list = []

    monkeypatch.setattr(BO.EB, "read_json_command", lambda call, notes="": {
        "ok": False, "code": 2,
        "json": {"error": "库 sha256 与 --expect-db-sha256 不符（本次未写库）"}})
    monkeypatch.setattr(BO.GIT, "commit_db",
                        lambda path, message: commits.append((path, message)))
    asyncio.run(view._write())
    assert commits == []
    assert "sha256 不符" in view.error
    assert view.commit is None


def _resolve_payload(db: str, *, written: bool, changed: bool) -> dict:
    return {"command": "resolve", "db": db, "written": written, "changed": changed,
            "records": 2 if changed else 0, "conflicts": 2 if changed else 0,
            "adopted": [{"key": "k1"}, {"key": "k2"}] if changed else [],
            "dry_run": not written,
            "db_sha256_before": "a" * 64,
            "db_sha256_after": ("b" * 64 if changed else "a" * 64),
            "totals_before": {"pending": 2}, "totals_after": {"pending": 0}}


def test_health_batch_preview_never_writes(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    page = _FakePage(db)
    view = BOH.BibHealthView(page)
    commits: list = []
    monkeypatch.setattr(BOH.EB, "read_json_command",
                        lambda call, notes="": {"ok": True, "code": 0,
                                                "json": _resolve_payload(db, written=False,
                                                                         changed=True)})
    monkeypatch.setattr(BOH.GIT, "commit_db",
                        lambda path, message: commits.append((path, message)))
    asyncio.run(view._preview_all())
    assert view.preview and view.preview["changed"] is True
    assert view.pending and view.pending["adopt_all_pending"] is True
    assert view.pending["expect_db_sha256"] == "a" * 64
    assert commits == []                                  # dry-run 预览：零写入、零提交


def test_health_batch_write_one_commit(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    page = _FakePage(db)
    view = BOH.BibHealthView(page)
    commits: list = []
    lint = {"counts": {"records": 10, "verified": 10, "unverified": 0, "errors": 0,
                       "warnings": 0, "conflicts": {"records": 2, "total": 2},
                       "conflicts_resolved": {"records": 0, "total": 0},
                       "near_duplicates": 0, "near_duplicates_acknowledged": 0,
                       "needs_inversion": {}}}
    conflicts = {"items": [{"key": "k1", "incoming_sha256": "f" * 64},
                           {"key": "k2", "incoming_sha256": "e" * 64}],
                 "totals": {"pending": 2, "resolved": 0, "total": 2},
                 "db_sha256": "a" * 64}

    def fake_read(call, notes=""):
        argv = list(getattr(call, "argv", []))
        if "resolve" in argv:
            return {"ok": True, "code": 0,
                    "json": _resolve_payload(db, written="--dry-run" not in argv,
                                             changed=True)}
        if "lint" in argv:
            return {"ok": True, "code": 0, "json": lint}
        return {"ok": True, "code": 0, "json": conflicts}

    monkeypatch.setattr(BOH.EB, "read_json_command", fake_read)
    monkeypatch.setattr(BOH.GIT, "status", lambda *a, **k: {"ok": True, "commits": []})
    monkeypatch.setattr(BOH.GIT, "commit_db",
                        lambda path, message: (commits.append((path, message)) or
                                               {"ok": True, "committed": True, "sha": "d" * 40}))

    asyncio.run(view._load())
    assert view.counts["pending"] == 2 and view.counts["records"] == 10
    asyncio.run(view._preview_all())
    assert commits == []
    asyncio.run(view._write_batch())
    assert len(commits) == 1 and "一键采用 2 条" in commits[0][1]
    assert view.commit and view.commit["committed"] is True


def test_health_view_degrades_when_no_pending():
    counts = BO.health_counts({"counts": {"records": 3883, "conflicts_pending": {}}},
                              {"totals": {"pending": 0, "resolved": 157, "total": 157}})
    assert counts["pending"] == 0
    assert "待裁决 0，已裁决 157" in BO.health_lines(counts)[2]


def test_real_db_untouched_after_ops_tests():
    """真实库只读：2026-11-08 BIB-MIGRATION 后锚点（3,875 条／3,664,354 B；2026-11-10 更新）；若本机有引用库，sha256 必须一致（否则报警）。"""
    import os
    from pathlib import Path

    notes = os.environ.get("SLCN_NOTES") or str(
        Path(__file__).resolve().parents[3] / "EditToolBox-notes")
    db = Path(notes) / "bib_db" / "bibliography.jsonl"
    if not db.exists():
        pytest.skip("本机没有引用库（CI / 独立检出）：真实库零写入由 stub 测试保证")
    # 依据：2026-11-10 实测真库 sha（3,875 条／3,664,354 B；notes bb1a041 BIB-MIGRATION-20261108 后）
    assert GIT.file_sha256(str(db)) == \
        "bae9b714f0268d3f979d1842ba0f4c72d1a1e9dc8d150e14e5821cda755b49ce"
