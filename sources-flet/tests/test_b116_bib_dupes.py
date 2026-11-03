"""批 116：查重合并页（写库）定向测试。"""
from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

import services.engine_bridge as EB
from ui import bib_dupes as BD
from ui.tokens import Tokens


# ── 夹具 ──────────────────────────────────────────────────────────────
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
        self.tasks: list = []

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


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
        self.store = _Store()
        self.t = Tokens()
        self.page = _FletPage(width, height)
        self.events: list[tuple] = []
        self.tasks: list = []
        root = Path(tmp_path) if tmp_path else Path("/tmp")
        self.stats_data = {"db": str(root / "bib_db" / "bibliography.jsonl"),
                           "records": 3883}

    def mark(self, event, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)

    def goto(self, view: str) -> None:
        self.events.append(("goto", {"view": view}))


def _db(tmp_path) -> Path:
    path = Path(tmp_path) / "bib_db" / "bibliography.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def args_of(call) -> list[str]:
    return list(call.argv[len(EB.base_cmd(call.entry)):])


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
    out = []
    for control in _walk(root):
        value = getattr(control, "value", None)
        if isinstance(value, str):
            out.append(value)
    return out


def _csv_text() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["key_a", "key_b", "similarity", "title", "year", "diff",
                     "status", "code"])
    writer.writerow(["li_2021_zhuangzi", "li_2021_zhuang_zi", "0.94", "庄子注疏",
                     "2021", "6", "候选", "near_duplicates"])
    writer.writerow(["wang_2020_a", "wang_2020_b", "0.91", "逻辑研究", "2020", "3",
                     "候选", "duplicate_content"])
    return buffer.getvalue()


RECORDS = {
    "li_2021_zhuangzi": {"key": "li_2021_zhuangzi", "entry_type": "article",
                         "fields": {"title": "庄子注疏", "year": "2021",
                                    "journal": "逻辑学研究"}},
    "li_2021_zhuang_zi": {"key": "li_2021_zhuang_zi", "entry_type": "article",
                          "fields": {"title": "庄子注疏", "year": "2021",
                                     "journal": "逻辑学研究集刊"}},
    "wang_2020_a": {"key": "wang_2020_a", "entry_type": "article",
                    "fields": {"title": "逻辑研究", "year": "2020", "pages": "1-10"}},
    "wang_2020_b": {"key": "wang_2020_b", "entry_type": "article",
                    "fields": {"title": "逻辑研究", "year": "2020", "pages": "1–10"}},
}


def _install(monkeypatch, *, calls=None, merge_ok=True, merge_code=0,
             merge_json=None, commit=None, write_ok=True) -> None:
    calls = calls if calls is not None else []

    def fake_read(call, *, notes="", **kwargs):
        argv = args_of(call)
        calls.append(argv)
        if "report-duplicates" in argv:
            out = argv[argv.index("--out") + 1]
            Path(out, "duplicates.csv").write_text(_csv_text(), encoding="utf-8")
            return {"ok": True, "json": {"pairs": 2}, "code": 0, "stderr": ""}
        if "query" in argv:
            key = argv[argv.index("--key") + 1]
            return {"ok": True, "json": {"hits": [{"record": RECORDS[key]}]},
                    "code": 0, "stderr": ""}
        if "merge-records" in argv:
            if "--dry-run" in argv:
                if not merge_ok:
                    return {"ok": False, "json": {"error": "库 sha256 不符"},
                            "code": merge_code, "stderr": "sha256 mismatch"}
                payload = {"db_sha256_before": "a" * 64, "db": "/tmp/x"}
                return {"ok": True, "json": payload, "code": 0, "stderr": ""}
            if not write_ok:
                return {"ok": False, "json": {"error": "库 sha256 不符"},
                        "code": merge_code, "stderr": "sha256 mismatch"}
            payload = merge_json or {"db_sha256_before": "a" * 64,
                                     "db": "/tmp/x"}
            return {"ok": True, "json": payload, "code": 0, "stderr": ""}
        if "ack-duplicates" in argv:
            return {"ok": True, "json": {"acknowledged": True}, "code": 0,
                    "stderr": ""}
        raise AssertionError(f"unexpected call {argv}")

    monkeypatch.setattr(BD.EB, "read_json_command", fake_read)
    committed: list[tuple] = commit if commit is not None else []

    def fake_commit(db, message):
        committed.append((db, message))
        return {"ok": True, "committed": True, "sha": "abc1234def"}

    monkeypatch.setattr(BD.GIT, "commit_db", fake_commit)


def _view(tmp_path, **kwargs):
    host = _Host(tmp_path, **kwargs)
    view = BD.BibDupesView(host)
    return view, host


# ── 纯函数 ────────────────────────────────────────────────────────────
def test_parse_duplicates_csv_tolerant_headers():
    rows = BD.parse_duplicates_csv(_csv_text())
    assert len(rows) == 2
    assert rows[0]["a"] == "li_2021_zhuangzi"
    assert rows[0]["b"] == "li_2021_zhuang_zi"
    assert rows[0]["similarity"] == pytest.approx(0.94)
    assert rows[0]["diff"] == 6
    assert rows[0]["code"] == "near_duplicates"


def test_parse_duplicates_csv_accepts_short_headers():
    rows = BD.parse_duplicates_csv("a,b,score,differences\nx1,y1,0.8,2\n")
    assert rows == [{"a": "x1", "b": "y1", "similarity": 0.8, "title": "",
                     "year": "", "diff": 2, "status": "候选",
                     "code": "near_duplicates"}]


def test_parse_duplicates_csv_skips_incomplete_rows():
    assert BD.parse_duplicates_csv("key_a,key_b\nonly, \n") == []
    assert BD.parse_duplicates_csv("") == []


# ── 布局 ──────────────────────────────────────────────────────────────
def test_build_wide_has_one_h1_and_confirm_bar(tmp_path):
    view, host = _view(tmp_path)
    root = view.build()
    assert view._root is root
    assert _texts(root).count("查重合并") == 1
    bars = [c for c in _walk(root) if getattr(c, "_bib_confirm_bar", None)]
    assert bars, "确认条必须常驻（56）"
    assert bars[0]._bib_confirm_btn is not None


def test_build_narrow_collapses_diff_and_keeps_bar(tmp_path, monkeypatch):
    view, host = _view(tmp_path, width=1000, height=700)
    _install(monkeypatch)
    asyncio.run(view._scan())
    root = view.build()
    assert view.narrow is True
    texts = _texts(root)
    assert any("展开对比" in text for text in texts) or any(
        text == "展开" for text in texts)
    assert [c for c in _walk(root) if getattr(c, "_bib_confirm_bar", None)]


# ── 报告 / 候选 ──────────────────────────────────────────────────────
def test_scan_uses_temp_out_and_loads_pairs(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    assert view.state == "ready"
    assert len(view.pairs) == 2
    report = [argv for argv in calls if "report-duplicates" in argv][0]
    out = report[report.index("--out") + 1]
    assert "bib_db" not in out, "报告不能写进笔记仓的库目录"
    assert "--json" in report
    assert view.report_at


def test_scan_empty_report_keeps_header(tmp_path, monkeypatch):
    view, host = _view(tmp_path)

    def fake_read(call, *, notes="", **kwargs):
        out = args_of(call)[args_of(call).index("--out") + 1]
        Path(out, "duplicates.csv").write_text("key_a,key_b\n", encoding="utf-8")
        return {"ok": True, "json": {}, "code": 0, "stderr": ""}

    monkeypatch.setattr(BD.EB, "read_json_command", fake_read)
    asyncio.run(view._scan())
    assert view.state == "empty"
    assert view.pairs == []
    root = view.build()
    assert any("没有待处理的候选对" in text for text in _texts(root))


def test_select_loads_both_records_and_defaults_to_a(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    assert view.left["key"] == "li_2021_zhuangzi"
    assert view.right["key"] == "li_2021_zhuang_zi"
    assert view.choices["journal"] == "A"
    assert view.merged_key == "li_2021_zhuangzi"
    assert host.store.get("bib_dupes_sel") == ("li_2021_zhuangzi|"
                                               "li_2021_zhuang_zi")


def test_select_failure_shows_message(tmp_path, monkeypatch):
    view, host = _view(tmp_path)

    def fake_read(call, *, notes="", **kwargs):
        argv = args_of(call)
        if "report-duplicates" in argv:
            out = argv[argv.index("--out") + 1]
            Path(out, "duplicates.csv").write_text(_csv_text(), encoding="utf-8")
            return {"ok": True, "json": {}, "code": 0, "stderr": ""}
        return {"ok": False, "json": {}, "code": 1, "stderr": "gone"}

    monkeypatch.setattr(BD.EB, "read_json_command", fake_read)
    asyncio.run(view._scan())
    assert view.selection_state == "failure"
    root = view.build()
    assert any("读不到候选记录" in text for text in _texts(root))


# ── 预览 / 写入 ──────────────────────────────────────────────────────
def test_preview_sets_sha_and_enables_confirm(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    asyncio.run(view._preview())
    merge = [argv for argv in calls if "merge-records" in argv][-1]
    assert "--dry-run" in merge
    assert "--expect-db-sha256" not in merge
    assert "--field-source" in merge
    assert merge[merge.index("--keys") + 1] == "li_2021_zhuangzi,li_2021_zhuang_zi"
    assert view.expect_sha == "a" * 64
    assert view._can_confirm() is True


def test_changing_choice_invalidates_preview(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    asyncio.run(view._preview())
    view._on_choice("journal", "B")
    assert view.choices["journal"] == "B"
    assert view.dirty_reason
    assert view._can_confirm() is False
    bar = view._bottom()
    assert bar._bib_stale is True


def test_invalid_merged_key_blocks_preview(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    view.merged_key = "Bad Key!"
    asyncio.run(view._preview())
    assert not [argv for argv in calls if "merge-records" in argv]
    assert any("key" in toast for toast in host.app.toasts)


def test_write_passes_expect_sha_and_backup_then_commits(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    db = _db(tmp_path)
    calls: list[list[str]] = []
    commits: list[tuple] = []
    _install(monkeypatch, calls=calls, commit=commits,
             merge_json={"db_sha256_before": "a" * 64, "db_sha256_after": "b" * 64,
                         "db": str(db)})
    asyncio.run(view._scan())
    asyncio.run(view._preview())
    asyncio.run(view._write())
    merge = [argv for argv in calls if "merge-records" in argv][-1]
    assert "--dry-run" not in merge
    assert merge[merge.index("--expect-db-sha256") + 1] == "a" * 64
    backup = merge[merge.index("--backup") + 1]
    assert backup.endswith(".jsonl") and ".backup" in backup
    assert len(commits) == 1
    assert "合并" in commits[0][1]
    assert "push" not in commits[0][1]
    assert view.state == "success"
    assert view._bottom()._bib_result_banner is not None


def test_write_sha_mismatch_marks_stale_and_skips_commit(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    db = _db(tmp_path)
    commits: list[tuple] = []
    _install(monkeypatch, commit=commits, write_ok=False, merge_code=2)
    asyncio.run(view._scan())
    asyncio.run(view._preview())
    view.expect_sha = "a" * 64
    asyncio.run(view._write())
    assert view.state == "stale"
    assert commits == []
    assert any("sha256" in (view.error or "") for _ in [0])
    bar = view._bottom()
    assert bar._bib_stale is True


def test_confirm_layer_then_cancel_keeps_draft(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    asyncio.run(view._preview())
    view._open_confirm()
    assert view.confirm_layer is True
    view._cancel_confirm()
    assert view.confirm_layer is False
    assert view.payload is not None
    assert any("未写库" in toast for toast in host.app.toasts)


def test_ignore_pair_requires_reason_and_writes_sidecar(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    view.ignore_reason = "短"
    asyncio.run(view._ignore())
    assert not [argv for argv in calls if "ack-duplicates" in argv]
    view.ignore_reason = "题名与作者完全不同"
    asyncio.run(view._ignore())
    ack = [argv for argv in calls if "ack-duplicates" in argv][-1]
    assert ack[ack.index("--code") + 1] == "near_duplicates"
    assert "--apply" in ack
    assert len(view.pairs) == 1
