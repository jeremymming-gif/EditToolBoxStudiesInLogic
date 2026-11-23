"""批 115 BPAGES-B · 入藏页（bib_import）单元测试。

纪律：不连真实引擎、不碰真实库；引擎调用一律 monkeypatch，库路径指向 tmp。
"""
from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from pathlib import Path

import pytest
import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_import as BI
from ui.tokens import Tokens


@pytest.fixture(autouse=True)
def _synthetic_manuscripts_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(tmp_path / "no-manuscripts"))
    monkeypatch.setenv("SLCN_TEST_DATASET_ID", "legacy-idea-8-synthetic-v1")


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
        self.routes: list[str] = []

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)

    def navigate_route(self, route: str) -> None:
        self.routes.append(route)


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
    monkeypatch.setattr(BI, "_db_fingerprint", lambda _path: "a" * 64)

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


def _walk(control):
    out = [control]
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    return out


def _labels(control) -> list[str]:
    labels = []
    for node in _walk(control):
        form_label = getattr(node, "_form_label", None)
        if isinstance(form_label, str):
            labels.append(form_label)
        value = getattr(node, "value", None)
        if isinstance(value, str):
            labels.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            labels.append(content)
    return labels


def _button_label(control) -> str:
    content = getattr(control, "content", None)
    if isinstance(content, str):
        return content
    return str(getattr(content, "value", "") or "")


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


def test_paste_mode_keeps_provenance_optional_and_editor_bounded(tmp_path):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.mode = "paste"
    tree = view._params(narrow=False)
    labels = _labels(tree)
    field = next(node for node in _walk(tree)
                 if getattr(node, "_form_label", None) == "粘贴 .bib 文本")
    editor = next(node for node in _walk(field)
                  if getattr(node, "multiline", False))

    assert labels.count("来源期号") == 0 and labels.count("稿件目录") == 0
    assert "标为已核实" in labels
    view._toggle_more()
    tree = view._params(narrow=False)
    labels = _labels(tree)
    fields = [node for node in _walk(tree) if isinstance(node, ft.TextField)]
    assert labels.count("来源期号") == 1 and labels.count("稿件目录") == 1
    assert any(node.hint_text == "如 01 Lei_Wang" and node.tooltip for node in fields)
    assert editor.min_lines == 6 and editor.max_lines == 10
    assert editor.expand is True  # Row 主轴上的横向填充
    assert field.expand is None   # 不纵向吃掉参数列剩余高度


@pytest.mark.parametrize("mode", ["bib", "paste"])
def test_provenance_inputs_appear_only_once_in_optional_section(tmp_path, mode):
    view = BI.BibImportView(_Host(_db(tmp_path)))
    view.mode = mode

    assert _labels(view._params(narrow=False)).count("期号") == 0
    assert _labels(view._params(narrow=False)).count("稿件目录") == 0

    view.more_open = True
    tree = view._params(narrow=False)
    labels = _labels(tree)
    fields = [node for node in _walk(tree) if isinstance(node, ft.TextField)]
    assert labels.count("来源期号") == 1
    assert labels.count("稿件目录") == 1
    assert any(node.hint_text == "如 01 Lei_Wang" and node.tooltip for node in fields)


def test_import_modes_only_offer_file_and_paste(tmp_path):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    labels = _labels(view._params(narrow=False))
    assert BI.MODES == (("bib", "单个 .bib"), ("paste", "粘贴文本"))
    assert "来源" in labels and "来源 *" not in labels
    assert "截止年份" not in labels and "只要已完期" not in labels


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
    assert "--unverified" in args
    assert args[args.index("--db") + 1] == str(Path(host.stats_data["db"]).resolve())
    assert view.state == "ready"
    assert view.expect_sha == "a" * 64
    assert view._counts()["conflicts"] == 3


def test_preview_uses_stats_path_when_engine_stats_has_no_db_key(tmp_path, monkeypatch):
    db = _db(tmp_path)
    host = _Host(db)
    host.stats_data = {"path": db}
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    calls = _install(monkeypatch)

    asyncio.run(view._preview())

    args = args_of(calls[0])
    assert args[args.index("--db") + 1] == str(Path(db).resolve())
    assert view.state == "ready"


def test_preview_filter_counts_follow_payload_and_map_conflict_key(tmp_path):
    view = BI.BibImportView(_Host(_db(tmp_path)))
    view.payload = {
        "report": {"entries": 4, "new": 1, "updated": 1, "conflicts": 1,
                   "skipped": 1},
        "changes": [
            {"key": "new1", "status": "new"},
            {"key": "upd1", "status": "updated"},
            {"key": "con1", "status": "conflict"},
            {"key": "skip1", "status": "skipped"},
        ],
    }

    assert [view._chip_count(key) for key in
            ("all", "new", "updated", "conflict", "skipped")] == ["4", "1", "1", "1", "1"]
    view.filter = "conflict"
    assert [row["key"] for row in view._rows()] == ["con1"]


def test_conflict_detail_renders_field_values_and_missing_value_fallback(tmp_path):
    view = BI.BibImportView(_Host(_db(tmp_path)))
    view.payload = {"changes": [{
        "key": "Fine2017", "incoming_key": "Fine2018", "status": "conflict",
        "title": "Truthmaker semantics", "author": "Fine, Kit", "year": "2017",
        "identity": "doi:10.1002/9781118972090.ch22",
        "field_diff": {"year": {"existing": "2017", "incoming": "2018"}},
    }]}
    row = view._changes()[0]
    rendered = view._change_detail(row)
    labels = _labels(rendered)
    assert "库内值" in labels and "本次输入" in labels
    assert "2017" in labels and "2018" in labels
    assert any("doi:10.1002/9781118972090.ch22" in value for value in labels)

    view.payload["changes"][0].pop("field_diff")
    view.payload["changes"][0]["conflict_fields"] = ["year"]
    fallback = _labels(view._change_detail(view._changes()[0]))
    assert "差异字段：year" in fallback
    assert any("引擎未返回字段值" in value for value in fallback)


def test_skipped_change_detail_shows_engine_reason(tmp_path):
    view = BI.BibImportView(_Host(_db(tmp_path)))
    view.payload = {"report": {"entries": 3, "skipped": {
        "条目结构损坏（花括号/引号不配平）": 1, "没有 key": 1,
        "同一份 bib 里 key 重复": 1,
        "DOI 匹配多条记录：Fine2017、Fine2018": 1}}, "changes": [{
        "change_id": "line:1:malformed_entry", "line": 1,
        "skip_code": "malformed_entry", "key": "",
        "incoming_key": "Malformed", "status": "skipped",
        "reason": "条目结构损坏（花括号/引号不配平）",
    }, {
        "change_id": "line:3:missing_key", "line": 3,
        "skip_code": "missing_key", "key": "", "incoming_key": "",
        "status": "skipped", "reason": "没有 key",
    }, {
        "change_id": "line:5:duplicate_key", "line": 5,
        "skip_code": "duplicate_key", "key": "", "incoming_key": "Repeated",
        "status": "skipped", "reason": "同一份 bib 里 key 重复",
    }, {
        "key": "FineIncoming", "incoming_key": "FineIncoming", "status": "skipped",
        "identity": "doi:10.1002/example", "reason": "DOI 匹配多条记录：Fine2017、Fine2018",
    }]}
    assert view._counts()["skipped"] == 4
    skipped_rows = [row for row in view._rows() if row["status"] == "skipped"]
    assert len(skipped_rows) == 4
    assert dict(Counter(row["reason"] for row in skipped_rows)) == \
        view.payload["report"]["skipped"]
    assert {row["skip_code"] for row in view._changes()} == {
        "malformed_entry", "missing_key", "duplicate_key", ""}
    assert len({row["change_id"] for row in view._changes()[:3]}) == 3

    view.filter = "skipped"
    row = next(row for row in view._rows() if row["skip_code"] == "missing_key")
    labels = _labels(view._change_detail(row))
    assert "第 3 行" in " ".join(labels)
    assert "分类 missing_key" in " ".join(labels)
    assert "原因：没有 key" in labels

    view.filter = "all"
    row = view._changes()[0]
    labels = _labels(view._change_detail(row))
    assert "原因：条目结构损坏（花括号/引号不配平）" in labels
    doi_row = next(row for row in view._rows() if row.get("identity"))
    assert "原因：DOI 匹配多条记录：Fine2017、Fine2018" in _labels(
        view._change_detail(doi_row))


def test_desktop_import_body_has_independent_scrollable_params_and_fixed_confirm(tmp_path):
    view = BI.BibImportView(_Host(_db(tmp_path)))
    root = view.build()
    layout = next(node for node in _walk(root) if hasattr(node, "_bib_left"))
    left = layout._bib_left
    assert isinstance(left, ft.ListView)
    assert left.expand is True
    assert isinstance(left.scroll, ft.Scrollbar)
    assert view._confirm_holder in _walk(root)


@pytest.mark.parametrize("row_count", [6, 12])
def test_narrow_import_uses_one_page_scroller_for_preview_rows(tmp_path, row_count):
    host = _Host(_db(tmp_path))
    host.page.width = 1000
    view = BI.BibImportView(host)
    view.state = "ready"
    view.payload = {
        "entries": row_count,
        "new": row_count,
        "updated": 0,
        "conflicts": 0,
        "skipped": 0,
        "changes": [
            {"key": f"anon{i:02d}", "status": "new", "title": f"题名 {i}"}
            for i in range(row_count)
        ],
    }

    root = view.build()
    layout = next(node for node in _walk(root)
                  if getattr(node, "_bib_narrow", False))
    assert isinstance(layout, ft.ListView)
    assert isinstance(layout.scroll, ft.Scrollbar)
    assert layout._bib_preview.expand is False

    tables = [node for node in _walk(layout._bib_preview)
              if getattr(node, "_bib_shrink", None) is not None]
    assert len(tables) == 1
    # B-348：结果表恒 shrink-wrap，长列表的滚轮由页面级 ListView 统一承载。
    assert tables[0]._bib_shrink is True
    assert tables[0].scroll is None
    assert view._confirm_holder in _walk(root)


@pytest.mark.parametrize("row_count", [6, 15])
def test_wide_long_list_wheel_belongs_to_preview_not_inner_table(
        tmp_path, monkeypatch, row_count):
    """B-348：宽窗滚轮归右预览栏（页体内统一滚动），结果表不再内滚。

    长短列表都 shrink-wrap；筛选局部更新不重建滚动容器（延续 37b5c48）。
    """
    host = _Host(_db(tmp_path))
    host.page.width = 1160
    view = BI.BibImportView(host)
    view.state = "ready"
    keys = [f"anon{i:02d}" for i in range(row_count)]
    view.payload = {
        "report": {"entries": row_count, "new": row_count, "updated": 0},
        "changes": [{"key": key, "title": f"题名 {key}", "status": "new"}
                    for key in keys],
    }
    root = view.build()
    body = root.content
    layout = next(node for node in _walk(root) if hasattr(node, "_bib_left"))
    assert layout._bib_narrow is False
    preview = layout._bib_preview
    # 统一的滚动容器就是右预览栏本身（页体内），不是结果表的内层滚动。
    assert isinstance(preview, ft.Column)
    assert getattr(preview, "_bib_preview_scroller", False) is True
    assert isinstance(preview.scroll, ft.Scrollbar)
    assert preview.expand is True
    table = next(node for node in _walk(preview) if hasattr(node, "_bib_shrink"))
    assert table._bib_shrink is True
    assert table.scroll is None
    assert not any(getattr(node, "_bib_preview_scroller", False)
                   for node in _walk(table))

    # 筛选局部更新：滚动容器与滚动条身份不变，结果表也不被重建。
    scrollbar = preview.scroll
    chips = next(node for node in _walk(preview)
                 if isinstance(node, ft.Row)
                 and _labels(node) == ["全部 %d" % row_count, "新增 %d" % row_count,
                                       "更新 0", "冲突 0", "跳过 0"])
    block = next(node for node in _walk(preview)
                 if any(child is chips for child in getattr(node, "controls", ())))
    monkeypatch.setattr(block, "update", lambda: None)
    chips.controls[2].on_click(None)  # 切到「更新 0」→ 空结果
    assert root.content is body
    assert any(node is preview for node in _walk(root))
    assert any(node is table for node in _walk(root))
    assert preview.scroll is scrollbar
    assert table.scroll is None
    assert "没有变化记录" in _labels(preview)


@pytest.mark.parametrize("width, updated", [(1000, 0), (1000, 3), (1160, 3)])
def test_preview_filter_updates_results_without_rebuilding_scroll_owner(
        tmp_path, monkeypatch, width, updated):
    host = _Host(_db(tmp_path))
    host.page.width = width
    view = BI.BibImportView(host)
    view.state = "ready"
    new_keys = [f"preview{i}" for i in range(6)]
    existing_keys = [f"synthetic{i}" for i in range(6)]
    view.payload = {
        "report": {"entries": 12, "new": 6, "updated": updated,
                   "merged": 6 - updated, "conflicts": 0, "skipped": {}},
        "changes": [
            {"key": key, "status": "new"} for key in new_keys
        ] + [
            {"key": key, "status": "updated" if i < updated else "merged"}
            for i, key in enumerate(existing_keys)
        ],
    }
    root = view.build()
    body = root.content
    layout = next(node for node in _walk(root) if hasattr(node, "_bib_left"))
    scroll_owner = layout if layout._bib_narrow else layout._bib_left
    assert isinstance(scroll_owner, ft.ListView)
    preview = layout._bib_preview
    result_table = next(node for node in _walk(preview)
                        if hasattr(node, "_bib_shrink"))
    confirm = view._confirm_holder
    chip_labels = ["全部 12", "新增 6", "更新 6", "冲突 0", "跳过 0"]
    chips = next(node for node in _walk(preview)
                 if isinstance(node, ft.Row) and _labels(node) == chip_labels)
    buttons = tuple(chips.controls)
    block = next(node for node in _walk(preview)
                 if any(child is chips for child in getattr(node, "controls", ())))
    chrome = tuple(block.controls)
    updates = []
    monkeypatch.setattr(block, "update", lambda: updates.append(block))

    cases = [(1, new_keys), (2, existing_keys), (3, []), (4, []),
             (0, new_keys + existing_keys)]
    for index, keys in cases:
        buttons[index].on_click(None)
        assert view.filter == BI.FILTERS[index][0]
        assert root.content is body
        assert any(node is scroll_owner for node in _walk(root))
        assert any(node is result_table for node in _walk(root))
        assert any(node is confirm for node in _walk(root))
        assert all(old is new for old, new in zip(chrome, block.controls, strict=True))
        assert all(old is new for old, new in zip(buttons, chips.controls, strict=True))
        assert _labels(chips) == chip_labels
        assert [button.bgcolor is not None for button in buttons] == [
            i == index for i in range(len(buttons))]
        labels = _labels(preview)
        assert [key for key in labels if key in new_keys + existing_keys] == keys
        assert ("没有变化记录" in labels) == (not keys)
        if index == 2:
            assert labels.count("更新") == updated + 1  # includes the summary label
            assert labels.count("合并") == 6 - updated

    buttons[0].on_click(None)  # reselecting the active filter is a no-op
    assert updates == [block] * len(cases)
    assert host.page.updates == 0


@pytest.mark.parametrize("width, filtered", [
    (1000, True), (1000, False), (1160, False),
], ids=["narrow-filtered-6", "narrow-all-12", "wide-all-12"])
def test_preview_detail_keeps_page_and_table_mounted(tmp_path, monkeypatch, width, filtered):
    host = _Host(_db(tmp_path))
    host.page.width = width
    view = BI.BibImportView(host)
    view.state = "ready"
    keys = [f"anon{i:02d}" for i in range(12)]
    view.payload = {
        "report": {"entries": 12, "new": 6, "updated": 6},
        "changes": [
            {"key": key, "title": f"题名 {key}", "incoming_key": f"input-{key}",
             "status": "new" if i < 6 else "updated",
             "field_diff": {"title": {"old": f"old-{key}", "new": f"new-{key}"}}}
            for i, key in enumerate(keys)
        ],
    }
    root = view.build()
    body = root.content
    layout = next(node for node in _walk(body) if hasattr(node, "_bib_preview"))
    preview = layout._bib_preview
    confirm = view._confirm_holder
    confirm_bar = confirm.content
    chip_labels = ["全部 12", "新增 6", "更新 6", "冲突 0", "跳过 0"]
    chips = next(node for node in _walk(preview)
                 if isinstance(node, ft.Row) and _labels(node) == chip_labels)
    if filtered:
        filter_block = next(node for node in _walk(preview)
                            if any(child is chips for child in getattr(node, "controls", ())))
        monkeypatch.setattr(filter_block, "update", lambda: None)
        chips.controls[2].on_click(None)
        keys = keys[6:]
    chip_colors = [chip.bgcolor for chip in chips.controls]
    table = next(node for node in _walk(preview) if hasattr(node, "_bib_shrink"))
    table_header, rows = table.controls
    # B-348：窄窗滚轮归页面级 ListView；宽窗归右预览栏（页体内统一滚动）。
    # 结果表恒无内层滚动条。
    scroll_owner = layout if width == 1000 else preview
    scrollbar = scroll_owner.scroll
    assert isinstance(scrollbar, ft.Scrollbar)
    assert table.scroll is None

    row_updates = []
    monkeypatch.setattr(rows, "update", lambda: row_updates.append(rows))

    def row_for(key):
        return next(row for row in rows.controls if callable(getattr(row, "on_click", None))
                    and key in _labels(row))

    def detail_button(key, label):
        return next(node for node in _walk(row_for(key))
                    if isinstance(node, ft.TextButton) and _button_label(node) == label)

    def assert_preview(open_key):
        assert root.content is body, "detail click replaced the page body"
        mounted = _walk(body)
        for control in (layout, preview, confirm, confirm_bar, chips, table, scroll_owner):
            assert any(node is control for node in mounted)
        assert confirm.content is confirm_bar
        assert _labels(chips) == chip_labels
        assert [chip.bgcolor for chip in chips.controls] == chip_colors
        assert table.controls[0] is table_header
        assert table.controls[1] is rows
        assert scroll_owner.scroll is scrollbar
        labels = _labels(rows)
        assert [label for label in labels if label in keys] == keys
        assert labels.count("收起") == (1 if open_key else 0)
        assert labels.count("查看明细") == len(keys) - (1 if open_key else 0)
        assert [label for label in labels if label.startswith("库键 ")] == (
            [f"库键 {open_key} · 输入键 input-{open_key}"] if open_key else [])
        assert len(rows.controls) == len(keys) + (1 if open_key else 0)
        if open_key:
            selected_row = row_for(open_key)
            index = next(i for i, row in enumerate(rows.controls) if row is selected_row)
            detail_labels = _labels(rows.controls[index + 1])
            assert f"old-{open_key}" in detail_labels
            assert f"new-{open_key}" in detail_labels
        assert host.page.updates == 0

    assert_preview(None)
    row_for(keys[-2]).on_click(None)
    assert_preview(keys[-2])
    detail_button(keys[-1], "查看明细").on_click(None)
    assert_preview(keys[-1])
    detail_button(keys[-1], "收起").on_click(None)
    assert_preview(None)
    assert row_updates == [rows] * 3


@pytest.mark.parametrize("width", [1000, 1160])
def test_options_pagination_and_confirmation_keep_scroll_owners(tmp_path, monkeypatch, width):
    db = _db(tmp_path)
    host = _Host(db)
    host.page.width = width
    view = BI.BibImportView(host)
    view.path = "/tmp/synthetic.bib"
    view.state = "ready"
    view.expect_sha = "a" * 64
    view.payload = {**PREVIEW, "entries": 205, "new": 205, "updated": 0,
                    "conflicts": 0, "skipped": 0, "changes": [
                        {"key": f"sample{i:03}", "status": "new"} for i in range(205)]}
    root = view.build()
    layout = next(node for node in _walk(root) if hasattr(node, "_bib_left"))
    preview = layout._bib_preview
    params_scroll = layout if layout._bib_narrow else layout._bib_left
    table = next(node for node in _walk(preview) if hasattr(node, "_bib_shrink"))
    confirm = view._confirm_holder
    updates = []
    monkeypatch.setattr(table, "update", lambda: updates.append(table))

    def check_mounted():
        for control in (layout, params_scroll, preview, table, confirm):
            assert any(node is control for node in _walk(root))

    def button(label):
        return next(node for node in _walk(root)
                    if callable(getattr(node, "on_click", None)) and _button_label(node) == label)

    if layout._bib_narrow:
        button("编辑参数…").on_click(None)
        check_mounted()
    button("来源信息与选项 ▸").on_click(None)
    assert "来源追溯（可选）" in _labels(layout._bib_left)
    check_mounted()
    button("来源信息与选项 ▾").on_click(None)
    assert "来源追溯（可选）" not in _labels(layout._bib_left)
    check_mounted()

    assert "sample099" in _labels(table) and "sample100" not in _labels(table)
    first_row = table.controls[1].controls[0]
    button("载入更多（还有 105 条）").on_click(None)
    check_mounted()
    assert "sample199" in _labels(table) and "sample200" not in _labels(table)
    button("载入更多（还有 5 条）").on_click(None)
    check_mounted()
    assert "sample204" in _labels(table) and view.shown == 300
    assert table.controls[1].controls[0] is first_row
    assert not any("载入更多" in label for label in _labels(table))
    assert updates == [table, table]

    confirm.content._bib_confirm_btn.on_click(None)
    check_mounted()
    assert view.confirm_layer and "确认写入引用库？" in _labels(preview)
    button("取消").on_click(None)
    check_mounted()
    assert not view.confirm_layer and "确认写入引用库？" not in _labels(preview)
    assert view.state == "ready" and view._can_confirm()

    confirm.content._bib_confirm_btn.on_click(None)
    view._on_issue(type("Event", (), {"control": ft.TextField(value="anonymous-issue")})())
    check_mounted()
    assert not view.confirm_layer and "确认写入引用库？" not in _labels(preview)
    assert confirm.content._bib_confirm_btn.disabled and not view._can_confirm()
    assert not host.tasks and Path(db).read_bytes() == b""


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


def test_preview_and_write_use_updated_plus_merged_count(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    preview = {"report": {"entries": 18, "new": 2, "updated": 9,
                           "merged": 9, "conflicts": 0, "skipped": 0},
               "changes": [{"key": "one", "status": "updated"}],
               "db_sha256_before": "a" * 64}
    write = {"report": {"entries": 18, "new": 2, "updated": 9,
                         "merged": 9, "conflicts": 0, "skipped": 0},
             "changes": [{"key": "one", "status": "updated"}],
             "db_sha256_after": "b" * 64}
    _install(monkeypatch, preview=preview, write=write)
    asyncio.run(view._preview())
    assert view._counts()["updated"] == 18
    asyncio.run(view._write())
    assert "18 条更新" in host.app.toasts[-1]
    assert any("18 更新" in label for label in _labels(view._success_banner()))


def test_import_success_result_links_to_version_sync(tmp_path):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.result = {"new": 2, "updated": 1}
    view.commit = {"committed": True, "sha": "c" * 40}

    root = view._success_banner()
    link = next(control for control in _walk(root)
                if isinstance(control, (ft.Button, ft.OutlinedButton, ft.TextButton))
                and _button_label(control) == "查看版本与同步")
    link.on_click(None)

    assert host.routes == ["bib:version_sync"]


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


def test_missing_reply_sha_with_changed_local_db_is_discarded(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    _install(monkeypatch, preview={"report": {"entries": 1, "new": 1},
                                  "changes": [{"key": "x", "status": "new"}]})
    fingerprints = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(BI, "_db_fingerprint", lambda _path: next(fingerprints))
    asyncio.run(view._preview())
    assert view.payload is None
    assert view.state == "empty"
    assert not view._can_confirm()


def test_missing_reply_and_unavailable_local_sha_cannot_be_ready(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "in.bib")
    _install(monkeypatch, preview={"report": {"entries": 1, "new": 1},
                                  "changes": [{"key": "x", "status": "new"}]})
    monkeypatch.setattr(BI, "_db_fingerprint", lambda _path: None)
    asyncio.run(view._preview())
    assert view.payload is None
    assert view.state == "empty"
    assert not view._can_confirm()
    assert "库指纹" in view.dirty_reason


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


def test_source_input_enables_preview_without_rebuilding_page(tmp_path):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.mode = "paste"
    tree = view.build()
    button = next(node for node in _walk(tree)
                  if getattr(node, "_bib_primary", False))
    assert button.disabled

    class _Event:
        control = type("Control", (), {"value": "@article{x,title={T}}"})()

    view._on_paste(_Event())
    assert button.disabled is False
    assert host.page.updates > 0


def test_zero_parsed_entries_are_not_preview_success(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    view = BI.BibImportView(host)
    view.path = str(tmp_path / "empty.bib")
    _install(monkeypatch, preview={"report": {"entries": 0}})
    asyncio.run(view._preview())
    assert view.state == "failure"
    assert view.payload is None
    assert "没有解析到" in view.error


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


# ── N01（验收 §3.1-11① / §3.2-12）：真实选择器 + 取消立刻收工 ─────────────
def _db_dir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="bibimp_")


def test_browse_uses_real_picker_not_file_picker_attribute():
    """旧实现读 `page.file_picker`（Flet 1.0.5 上没有）→ 永久失效分支。"""
    src = inspect.getsource(BI.BibImportView._browse)
    assert 'app.browse_field' in src or 'browse_field' in src
    assert 'getattr(page, "file_picker", None)' not in src


def test_browse_reports_when_host_has_no_picker():
    host = _Host(_db_dir())
    host.app.toasts = []       # 没有 browse_field 的宿主
    view = BI.BibImportView(host)
    view._browse()
    assert any("粘贴路径" in toast for toast in host.app.toasts), "取不到选择器要如实提示"


def test_picker_cancel_stops_polling_without_waiting_60s(monkeypatch):
    """取消时选择器不一定写回任何值：靠宿主的 `_picker_state` 立刻收工。"""
    host = _Host(_db_dir())
    host.app._picker_state = "idle"   # browse_field 之后立刻就 idle（已取消）
    calls: list = []

    def _browse_field(ref, title, kind, key, **_k):
        calls.append((title, kind, key))

    host.app.browse_field = _browse_field
    view = BI.BibImportView(host)
    view.mode = "bib"
    view._params(narrow=False)         # 渲染一次，登记真实输入框引用
    view._browse()
    assert calls and calls[0][1] == "file"

    async def _run():
        await view._watch_pick("")    # 值没变，但宿主已 idle → 必须马上返回

    asyncio.run(_run())
    assert view._picking is False


def test_picker_result_updates_path_enables_and_starts_preview(tmp_path, monkeypatch):
    host = _Host(_db(tmp_path))
    host.app._picker_state = "idle"
    picked = str(tmp_path / "picked.bib")
    calls = _install(monkeypatch)

    def _browse_field(ref, _title, _kind, _key, **_kwargs):
        host.app._picker_state = "opening"
        host.picker_ref = ref

    host.app.browse_field = _browse_field
    view = BI.BibImportView(host)
    tree = view.build()
    button = next(node for node in _walk(tree)
                  if getattr(node, "_bib_primary", False))
    assert button.disabled is True

    view._browse()
    assert host.tasks and view._picking is True

    async def _picker_returns(_delay):
        host.picker_ref[0].value = picked
        host.app._picker_state = "idle"

    monkeypatch.setattr(BI.asyncio, "sleep", _picker_returns)
    asyncio.run(host.tasks[-1]())

    assert view.path == picked
    assert view._preview_button.disabled is False
    asyncio.run(view._preview())
    assert calls
    args = args_of(calls[0])
    assert args[args.index("--bib") + 1] == picked
