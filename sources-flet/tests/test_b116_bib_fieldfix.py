"""批 116：字段归位页（写库）定向测试。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import flet as ft
import pytest

import services.engine_bridge as EB
from ui import bib_fieldfix as BF
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

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        pass


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
    return [c.value for c in _walk(root) if isinstance(getattr(c, "value", None), str)]


SUGGEST = {
    "db_sha256": "c" * 64,
    "suggestions": [
        {"key": "li_2021", "title": "庄子注疏", "entry_type": "article",
         "suggestions": [{"field": "year", "candidate": "1921",
                          "reason": "缺年份", "risk": "low"}]},
        {"key": "wang_2020", "title": "逻辑研究", "entry_type": "unknown",
         "anomaly_kind": "unknown_entry_type", "suggested_type": "article",
         "key_reason": "类型未知", "risk": "high"},
        {"key": "zhao_2019", "title": "名理", "entry_type": "article",
         "suggestions": [{"field": "publisher", "candidate": "商务印书馆",
                          "reason": "缺出版社", "risk": "medium"}]},
    ],
}


FULL_FIELDS = {
    "li_2021": {"author": "李", "journal": "古籍", "title": "庄子注疏",
                "year": "1920"},
    "wang_2020": {"author": "王", "journal": "逻辑", "title": "逻辑研究",
                  "year": "2020"},
    "zhao_2019": {"author": "赵", "journal": "名理", "title": "名理",
                  "year": "2019", "publisher": "旧社"},
}


def _install(monkeypatch, *, calls=None, write_fail_at=None, commit=None):
    calls = calls if calls is not None else []
    committed = commit if commit is not None else []
    sha_counter = {"n": 0}

    def fake_read(call, *, notes="", **kwargs):
        argv = args_of(call)
        calls.append(argv)
        if "query" in argv:
            key = argv[argv.index("--key") + 1]
            return {"ok": True, "code": 0, "stderr": "",
                    "json": {"count": 1, "ambiguous": False,
                             "hits": [{"key": key,
                                       "record": {"key": key,
                                                  "fields": dict(FULL_FIELDS[key])}}]}}
        if "suggest-field-fixes" in argv:
            return {"ok": True, "json": SUGGEST, "code": 0, "stderr": ""}
        if "edit" in argv:
            if "--dry-run" in argv:
                sha_counter["n"] += 1
                sha = f"{sha_counter['n']:064x}"[-64:]
                return {"ok": True,
                        "json": {"db_sha256_before": "c" * 64,
                                 "db_sha256_after": sha,
                                 "field_changes": {"year": ["", "1921"]}},
                        "code": 0, "stderr": ""}
            index = len([a for a in calls if "edit" in a and "--dry-run" not in a])
            if write_fail_at is not None and index == write_fail_at:
                return {"ok": False, "json": {"error": "库 sha256 不符"},
                        "code": 1, "stderr": "boom"}
            sha_counter["n"] += 1
            sha = f"{sha_counter['n']:064x}"[-64:]
            return {"ok": True, "json": {"db_sha256_after": sha}, "code": 0,
                    "stderr": ""}
        raise AssertionError(f"unexpected call {argv}")

    monkeypatch.setattr(BF.EB, "read_json_command", fake_read)

    def fake_commit(db, message):
        committed.append((db, message))
        return {"ok": True, "committed": True, "sha": "cafe1234"}

    monkeypatch.setattr(BF.GIT, "commit_db", fake_commit)


def _view(tmp_path, **kwargs):
    host = _Host(tmp_path, **kwargs)
    return BF.BibFieldFixView(host), host


# ── 纯函数 ────────────────────────────────────────────────────────────
def test_parse_suggestions_flat_and_unknown_type():
    rows = BF.parse_suggestions(SUGGEST)
    assert [row["field"] for row in rows] == ["year", "entry_type", "publisher"]
    assert rows[1]["field"] == "entry_type"
    assert rows[1]["candidate"] == "article"
    assert rows[1]["reason"] == "类型未知"


def test_parse_suggestions_missing_list_and_dedupe():
    payload = {"items": [
        {"key": "a", "missing": ["year", "title"], "candidate": "x", "reason": "r"},
        {"key": "a", "missing": ["year"]},
    ]}
    rows = BF.parse_suggestions(payload)
    assert [row["field"] for row in rows] == ["year", "title"]
    assert [row["id"] for row in rows] == ["a|year", "a|title"]


def test_confidence_is_inverse_of_risk():
    assert BF.confidence_of({"risk": "low"}) == "高"
    assert BF.confidence_of({"risk": "medium"}) == "中"
    assert BF.confidence_of({"risk": "high"}) == "低"
    assert BF.confidence_of({}) == "中"


# ── 布局 ──────────────────────────────────────────────────────────────
def test_build_wide_has_eight_columns_one_h1(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    root = view.build()
    assert _texts(root).count("字段归位") == 1
    headers = [c for c in _walk(root) if getattr(c, "_bib_header_row", None)]
    assert headers, "表格必须有表头"
    # 表头 30：由 bib_shell 负责；列数由 8 列文本体现
    assert any("来源" == text for text in _texts(root))
    assert "[26]" not in "".join(_texts(root))


def test_build_narrow_has_six_columns_without_source(tmp_path, monkeypatch):
    view, host = _view(tmp_path, width=1000, height=700)
    _install(monkeypatch)
    asyncio.run(view._scan())
    root = view.build()
    headers, weights, mono, align = view._columns()
    assert len(headers) == 6
    assert "来源" not in headers
    assert "key" not in headers
    assert "└" not in "".join(_texts(root))


def test_no_card_helper_in_model(tmp_path):
    source = Path(BF.__file__).read_text(encoding="utf-8")
    assert "C.card" not in source


# ── 扫描 ──────────────────────────────────────────────────────────────
def test_scan_emits_registry_and_limit(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    scan = [argv for argv in calls if "suggest-field-fixes" in argv][0]
    assert scan[scan.index("--registry") + 1] == "missing_field"
    assert scan[scan.index("--limit") + 1] == "50"
    assert len(view.rows) == 3
    assert view.state == "ready"
    assert view.scan_sha == "c" * 8


def test_registry_switch_rescans_with_unknown_type(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    view._set_registry("unknown_entry_type")
    fn = host.tasks[-1]
    asyncio.run(fn())
    scans = [argv for argv in calls if "suggest-field-fixes" in argv]
    assert scans[-1][scans[-1].index("--registry") + 1] == "unknown_entry_type"
    assert host.store.get("bib_fieldfix_registry") == "unknown_entry_type"


def test_scan_failure_shows_stderr_banner(tmp_path, monkeypatch):
    view, host = _view(tmp_path)

    def fake_read(call, **kwargs):
        return {"ok": False, "json": {}, "code": 1, "stderr": "registry 读取失败"}

    monkeypatch.setattr(BF.EB, "read_json_command", fake_read)
    asyncio.run(view._scan())
    assert view.state == "failure"
    root = view.build()
    assert any("读取失败" in text for text in _texts(root))


# ── 筛选 / 展开 ───────────────────────────────────────────────────────
def test_local_filters_and_sort_do_not_rescan(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    before = len(calls)
    view.query = "名理"
    assert [row["key"] for row in view._filtered()] == ["zhao_2019"]
    view.query = ""
    view.fields = {"publisher"}
    assert [row["field"] for row in view._filtered()] == ["publisher"]
    view.fields = set()
    view._on_sort(3)
    assert len(calls) == before


def test_expanded_row_has_manual_48_and_radio(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    row = view.rows[0]
    view._expand(row["id"])
    root = view.build()
    manuals = [c for c in _walk(root)
               if isinstance(c, ft.TextField) and c.height == 48]
    assert manuals, "展开行必须有 48 高手工覆盖输入"
    assert view.expanded == row["id"]


def test_narrow_expanded_row_exposes_full_record_and_field_values(tmp_path, monkeypatch):
    view, host = _view(tmp_path, width=1000, height=668)
    _install(monkeypatch)
    asyncio.run(view._scan())
    row = view.rows[0]
    row["title"] = "记录题名：" + ("长题名" * 30)
    row["reason"] = "缺失原因：" + ("需要核对" * 30)
    row["candidate"] = "候选值：" + ("完整内容" * 30)

    # Narrow table cells remain single-line; the expanded detail is the non-hover
    # path for reading every full value at a 1000px window.
    row_control = view._row_control(row, view._columns()[2])
    row_texts = [node for node in _walk(row_control) if isinstance(node, ft.Text)]
    assert any(node.value == row["title"] and node.max_lines == 1 for node in row_texts)
    assert any(node.value == row["reason"] and node.max_lines == 1 for node in row_texts)
    assert any(node.value == row["candidate"] and node.max_lines == 1 for node in row_texts)

    detail = view._expanded_row(row)
    full_texts = [node.value for node in _walk(detail) if isinstance(node, ft.Text)]
    assert f"记录：{row['title']}" in full_texts
    assert row["reason"] in full_texts
    candidate_fields = [node for node in _walk(detail)
                        if isinstance(node, ft.TextField)
                        and node.value == row["candidate"]]
    assert len(candidate_fields) == 1
    assert candidate_fields[0].multiline is True
    assert candidate_fields[0].max_lines is None
    assert candidate_fields[0].read_only is True


def test_expanded_row_wraps_long_candidate_and_keeps_actions_reachable(tmp_path, monkeypatch):
    view, host = _view(tmp_path, width=1000, height=668)
    _install(monkeypatch)
    asyncio.run(view._scan())
    row = view.rows[0]
    row["candidate"] = "极长候选值_" + ("x" * 240)
    view._expand(row["id"])
    detail = view._expanded_row(row)
    assert detail.height is None, "展开详情必须随内容增高，不能固定裁切"
    candidate_fields = [c for c in _walk(detail)
                        if isinstance(c, ft.TextField) and c.value == row["candidate"]]
    assert len(candidate_fields) == 1
    candidate_field = candidate_fields[0]
    assert candidate_field.multiline is True
    assert candidate_field.max_lines is None
    assert candidate_field.read_only is True
    assert "跳过" in _texts(detail) or any(
        getattr(c, "content", None) == "跳过" for c in _walk(detail))


def test_expand_and_collapse_keep_the_mounted_table_scroll(tmp_path, monkeypatch):
    view, host = _view(tmp_path, width=1000, height=668)
    _install(monkeypatch)
    asyncio.run(view._scan())
    root = view.build()
    body = root.content

    def table_scroll():
        return next(c for c in _walk(root) if isinstance(c, ft.Column)
                    and isinstance(c.scroll, ft.Scrollbar)
                    and any(getattr(row, "_bib_row", False) for row in c.controls))

    scroll = table_scroll()
    updates = host.page.updates

    def click(index):
        rows = [c for c in scroll.controls if getattr(c, "_bib_row", False)]
        rows[index].on_click(None)
        assert root.content is body
        assert table_scroll() is scroll

    def assert_detail(index):
        detail_indices = [i for i, c in enumerate(scroll.controls)
                          if any(isinstance(node, ft.RadioGroup) for node in _walk(c))]
        if index is None:
            assert detail_indices == []
        else:
            row = [i for i, c in enumerate(scroll.controls)
                   if getattr(c, "_bib_row", False)][index]
            assert detail_indices == [row + 1]

    click(-1)
    assert view.expanded == view._filtered()[-1]["id"]
    assert_detail(-1)
    click(0)
    assert view.expanded == view._filtered()[0]["id"]
    assert_detail(0)
    click(0)
    assert view.expanded is None
    assert_detail(None)
    assert host.page.updates == updates + 3


@pytest.mark.parametrize("width", [1000, 1160])
def test_table_stays_mounted_through_row_actions_and_preview(tmp_path, monkeypatch, width):
    view, host = _view(tmp_path, width=width)
    calls = []
    commits = []
    _install(monkeypatch, calls=calls, commit=commits)
    asyncio.run(view._scan())
    root = view.build()
    shell = root.content
    table = view._table_scroll
    updated_states = []

    def check_mounted():
        assert root.content is shell
        assert view._table_scroll is table and any(n is table for n in _walk(root))
        updated_states.append(view.state)

    monkeypatch.setattr(host.page, "update", check_mounted)

    def checkbox():
        return next(n for n in _walk(table) if isinstance(n, ft.Checkbox))

    def button(label):
        return next(n for n in _walk(root) if getattr(n, "content", None) == label)

    row = view._filtered()[0]
    checkbox().on_change(None)
    assert view.checked == {row["id"]} and checkbox().value
    before_sort = [r["key"] for r in view._filtered()]
    view._on_sort(1)
    rendered = " ".join(_texts(table))
    sorted_keys = sorted(before_sort, reverse=True)
    assert [r["key"] for r in view._filtered()] == sorted_keys
    positions = [rendered.index(key) for key in sorted_keys]
    assert positions == sorted(positions)

    field_chip = next(n for n in _walk(root) if getattr(n, "_bib_filter_chip", None) == "field")
    field_chip.on_click(None)
    assert view.fields and len(view._filtered()) < len(view.rows)
    assert sum(bool(getattr(n, "_bib_row", False)) for n in table.controls) == len(view._filtered())
    view.fields.clear()
    view._apply_search()

    other = next(r for r in view.rows if r["id"] != row["id"])
    view._expand(other["id"])
    button("跳过").on_click(None)
    assert not view._row_by_id(other["id"]) and not view.expanded
    assert other["key"] not in " ".join(_texts(table))
    assert f"{len(view.rows)} 条建议" in shell._bib_meta.value

    button("预览所选").on_click(None)
    asyncio.run(host.tasks.pop()())
    assert view.state == "previewed" and "running" in updated_states
    assert view._can_confirm() and not view._bottom_holder.content._bib_confirm_btn.disabled
    assert all("--dry-run" in argv for argv in calls if "edit" in argv)

    call = view._call

    async def cancel_during_preview(cmd):
        result = await call(cmd)
        if "--dry-run" in cmd.argv:
            button("取消").on_click(None)
            assert view.state == "cancelled" and not view._can_confirm()
        return result

    monkeypatch.setattr(view, "_call", cancel_during_preview)
    asyncio.run(view._preview())
    check_mounted()
    assert view.state == "cancelled" and "已取消预览，未写库" in _texts(root)
    assert not view._can_confirm() and commits == []
    assert all("--dry-run" in argv for argv in calls if "edit" in argv)


def test_manual_override_invalidates_preview(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    view.checked = {view.rows[0]["id"]}
    asyncio.run(view._preview())
    queries = [argv for argv in calls if "query" in argv]
    assert len(queries) == 1, "预览前必须按 key 读取一次完整字段"
    assert queries[0][queries[0].index("--key") + 1] == "li_2021"
    assert view.state == "previewed"
    view._set_candidate(view.rows[0]["id"], "1922")
    assert view.dirty_reason
    assert view._can_confirm() is False


# ── 预览 / 写入 ──────────────────────────────────────────────────────
def test_preview_chains_sha_and_passes_dry_run(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls)
    asyncio.run(view._scan())
    view.checked = {row["id"] for row in view.rows}
    asyncio.run(view._preview())
    queries = [argv for argv in calls if "query" in argv]
    assert [argv[argv.index("--key") + 1] for argv in queries] == [
        "li_2021", "wang_2020", "zhao_2019"]
    edits = [argv for argv in calls if "edit" in argv]
    assert len(edits) == 3
    assert all("--dry-run" in argv for argv in edits)
    assert edits[0][edits[0].index("--key") + 1] == "li_2021"
    assert json.loads(edits[0][edits[0].index("--json-fields") + 1]) == {
        **FULL_FIELDS["li_2021"], "year": "1921"}
    # 未知类型行写 entry_type，字段仍传整份原值
    assert edits[1][edits[1].index("--entry-type") + 1] == "article"
    assert json.loads(edits[1][edits[1].index("--json-fields") + 1]) == \
        FULL_FIELDS["wang_2020"]
    assert view.state == "previewed"
    assert view.expect_sha
    bar = view._bottom()
    assert bar._bib_confirm_btn is not None


def test_write_applies_with_sha_chain_and_commits_once(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    db = _db(tmp_path)
    calls: list[list[str]] = []
    commits: list[tuple] = []
    _install(monkeypatch, calls=calls, commit=commits)
    asyncio.run(view._scan())
    view.checked = {view.rows[0]["id"], view.rows[2]["id"]}
    asyncio.run(view._preview())
    asyncio.run(view._write())
    writes = [argv for argv in calls if "edit" in argv and "--apply" in argv]
    assert len(writes) == 2
    assert writes[0][writes[0].index("--backup") + 1]
    assert writes[0][writes[0].index("--expect-db-sha256") + 1] == \
        f"{2:064x}", "第二条预览后的 sha 应成为首次写入的 CAS 基准"
    assert writes[1][writes[1].index("--expect-db-sha256") + 1] == \
        f"{3:064x}", "首次写入返回的 db_sha256_after 应成为第二次写入的 CAS 基准"
    assert json.loads(writes[0][writes[0].index("--json-fields") + 1]) == {
        **FULL_FIELDS["li_2021"], "year": "1921"}
    assert json.loads(writes[1][writes[1].index("--json-fields") + 1]) == {
        **FULL_FIELDS["zhao_2019"], "publisher": "商务印书馆"}
    assert len(commits) == 1
    assert "字段归位 2 条" == commits[0][1]
    assert view.state == "success"
    assert view._bottom()._bib_result_banner is not None


def test_partial_write_failure_keeps_written_count(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _db(tmp_path)
    commits: list[tuple] = []
    calls: list[list[str]] = []
    _install(monkeypatch, calls=calls, write_fail_at=2, commit=commits)
    asyncio.run(view._scan())
    view.checked = {row["id"] for row in view.rows}
    asyncio.run(view._preview())
    asyncio.run(view._write())
    queries = [argv for argv in calls if "query" in argv]
    assert [argv[argv.index("--key") + 1] for argv in queries] == [
        "li_2021", "wang_2020", "zhao_2019",
        "li_2021", "wang_2020"], "预览各一次；写入按到达顺序读到失败行为止"
    assert view.state == "failure"
    assert view.written == 1
    assert commits == []
    assert "已写 1 条未提交" in view.error


def test_confirm_disabled_without_preview(tmp_path, monkeypatch):
    view, host = _view(tmp_path)
    _install(monkeypatch)
    asyncio.run(view._scan())
    assert view._can_confirm() is False
    view.checked = {view.rows[0]["id"]}
    bar = view._bottom()
    assert bar._bib_batch_bar is not None
    assert getattr(bar, "_bib_confirm_btn", None) is None
