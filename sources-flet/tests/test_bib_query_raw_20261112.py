"""批 121 P1-2：查库详情全字段 + 「原始记录」对话框契约测试（不启窗口、不碰真库）。

覆盖 EXECUTION.md v1.2 §5 P1-1/P1-2/P1-3：
- 详情逐行列出 record.fields 全部键（含未知键），空值 ——，值逐字符可选中；
- 来源逐条 / 别名全列 / 冲突计数与去处；未选中「未选择记录」；
- 「原始记录」物理行号、总行数、记录 SHA256、pretty JSON 逐字符、复制两键入参；
- 四种失败路径中文提示不崩溃；Esc/关闭各关一次；
- 只读硬契约（零写、库副本 sha256+mtime 不变）+ 性能预算。
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from services import bib_git as GIT
from ui import bib_query as QV
from ui import components as C
from ui.tokens import Tokens

BUTTON_TYPES = (ft.TextButton, ft.OutlinedButton, ft.FilledButton)


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = 1160

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _Host:
    def __init__(self, db_path: str = "") -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.copied: list[str] = []
        self.opened: list[str] = []
        self.events: list[tuple] = []
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=1160,
                                   copy_text=self.copied.append,
                                   open_path=self.opened.append)
        self.stats_data = {"db": str(db_path), "records": 0, "sha256": ""}
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        self.events.append((event, dict(kwargs)))

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


@pytest.fixture(autouse=True)
def _clean_dialog_stack():
    C._open_dialogs.clear()
    yield
    C._open_dialogs.clear()


def _walk(control) -> list:
    out = [control]
    for attr in ("title", "content"):
        child = getattr(control, attr, None)
        if child is not None and not isinstance(child, str):
            out.extend(_walk(child))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out: list[str] = []
    for node in _walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
    return out


def _button(control, label: str):
    for node in _walk(control):
        if isinstance(node, BUTTON_TYPES) and label in _texts(node):
            return node
    return None


def _instant(view: QV.QueryView) -> None:
    """把「后台任务」在本测试线程内同步跑完（真实壳走 page.run_task）。"""
    view._schedule_async = lambda fn, *args: asyncio.run(fn(*args))


def _rec(key: str, fields: dict, *, sources=None, aliases=None, conflicts=None,
         entry_type: str = "article", verified: bool = True) -> dict:
    record = {"key": key, "entry_type": entry_type, "verified": verified,
              "fields": dict(fields)}
    if sources is not None:
        record["sources"] = sources
    if aliases is not None:
        record["aliases"] = aliases
    if conflicts is not None:
        record["conflicts"] = conflicts
    return record


def _hit(record: dict) -> dict:
    fields = record.get("fields") or {}
    hit = {"key": record.get("key"), "score": 1.0,
           "verified": bool(record.get("verified")), "record": record}
    if fields.get("title"):
        hit["title"] = fields["title"]
    if fields.get("author"):
        hit["author"] = fields["author"]
    if fields.get("year"):
        hit["year"] = fields["year"]
    return hit


def _select(hit: dict) -> tuple[_Host, QV.QueryView]:
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    view.hits = [hit]
    view.loaded = True
    view.selected_key = str(hit.get("key") or "")
    return host, view


def _select_db(hit: dict, db_path: Path) -> tuple[_Host, QV.QueryView]:
    host, view = _select(hit)
    host.stats_data = {"db": str(db_path), "records": 0, "sha256": ""}
    _instant(view)
    return host, view


# ── 详情（P1-1）─────────────────────────────────────────────────────────
FIELD_HEADINGS = ("字段", "来源", "别名", "冲突")


def _detail_sections(view: QV.QueryView) -> dict[str, list]:
    body = view._detail_body()
    sections: dict[str, list] = {name: [] for name in FIELD_HEADINGS}
    current = ""
    for control in getattr(body, "controls", None) or ():
        value = getattr(control, "value", None)
        if isinstance(value, str) and value.endswith("）") and "（" in value:
            head = value.split("（")[0]
            if head in FIELD_HEADINGS:
                current = head
                continue
        if current:
            sections[current].append(control)
    return sections


def _row_pair(control) -> tuple[str, str]:
    texts = [node.value for node in getattr(control, "controls", None) or ()
             if isinstance(node, ft.Text)]
    assert len(texts) == 2, texts
    return texts[0], texts[1]


def test_detail_lists_all_fields_with_chinese_notes_and_empty_marker():
    record = _rec("Fine1975", {
        "title": "Some Logic", "pages": "35-48", "publisher": "",
        "note": None, "volume": 12, "year": "1975",
        "custom_field": {"a": 1},
    })
    _, view = _select(_hit(record))
    sections = _detail_sections(view)
    pairs = [_row_pair(control) for control in sections["字段"]]
    labels = [label for label, _ in pairs]
    assert "字段（7）" in _texts(view._detail_body())
    assert labels == ["title（题名）", "pages（页码）", "publisher（出版社）",
                      "note（附注）", "volume（卷）", "year（年份）",
                      "custom_field"]
    values = dict(pairs)
    assert values["title（题名）"] == "Some Logic"
    assert values["pages（页码）"] == "35-48"
    assert values["publisher（出版社）"] == QV.FIELD_EMPTY      # 空字符串
    assert values["note（附注）"] == QV.FIELD_EMPTY             # None
    assert values["volume（卷）"] == "12"
    assert values["custom_field"] == '{"a": 1}'                 # 未知字段原样
    # 值控件可选中、视觉 2 行、tooltip 全文（与值逐字符相同）。
    for control in sections["字段"]:
        label, value = _row_pair(control)
        value_text = [node for node in control.controls if isinstance(node, ft.Text)][1]
        assert value_text.selectable is True
        assert value_text.max_lines == 2
        assert value_text.tooltip == value


def test_detail_sources_aliases_conflicts_and_zero_states():
    record = _rec("k1", {"title": "t"}, sources=[
        {"issue": "2024年第1期", "slug": "fine", "file": "a.md", "kind": "article"},
        {"issue": "2025年第2期", "slug": "cohen", "file": "b.md", "kind": "review"},
        {"issue": ""},
    ], aliases=["LI2024", "李2024"], conflicts=[{"field": "year"}, {"field": "pages"}])
    _, view = _select(_hit(record))
    sections = _detail_sections(view)
    source_texts = [text for control in sections["来源"]
                    for text in _texts(control)]
    assert source_texts[0] == "1. 2024年第1期 · fine · a.md · article"
    assert source_texts[1] == "2. 2025年第2期 · cohen · b.md · review"
    assert source_texts[2] == "3. — · — · — · —"
    assert "来源（3）" in _texts(view._detail_body())
    assert _texts(sections["别名"][0]) == ["LI2024、李2024"]
    assert _texts(sections["冲突"][0]) == ["2 条 · 去「审核」视图逐字段核对"]
    assert "别 名" not in " ".join(_texts(view._detail_body()))

    empty = _rec("k2", {"title": "t"})
    _, view2 = _select(_hit(empty))
    body_texts = _texts(view2._detail_body())
    assert "来源（0）" in body_texts and QV.NO_SOURCES in body_texts
    assert "别名（0）" in body_texts and "冲突（0）" in body_texts
    assert body_texts.count(QV.NO_VALUE) >= 2


def test_detail_without_selection_shows_placeholder_and_no_raw_button():
    host, view = _select(_hit(_rec("k", {"title": "t"})))
    view.selected_key = None
    texts = _texts(view.build())
    assert "未选择记录" in texts
    assert QV.RAW_BUTTON not in texts
    assert view._detail_actions() == []


def test_detail_long_record_scrolls_without_horizontal_rows():
    fields = {f"f{i:02d}": f"value {i}" for i in range(20)}
    _, view = _select(_hit(_rec("big", fields)))
    body = view._detail_body()
    assert isinstance(body, ft.Column)
    assert body.scroll is not None                 # 360 宽 20 字段：纵向滚动，不压字段
    assert not any(isinstance(node, ft.Row) and getattr(node, "scroll", None)
                   for node in _walk(body))


# ── 原始记录（P1-2）─────────────────────────────────────────────────────
def _write_lines(path: Path, lines: list[bytes]) -> None:
    path.write_bytes(b"\n".join(lines) + b"\n")


def _make_main_db(path: Path) -> tuple[bytes, dict]:
    """200 行库：第 159 行是目标记录（多来源/别名/冲突/12KB 正文），坏行与空行在前。"""
    target = _rec("li2024analysis", {
        "title": "分析哲学", "author": "李", "year": "2024",
        "abstract": "x" * 12000,
    }, sources=[
        {"issue": "2024年第1期", "slug": "li", "file": "li.md", "kind": "article"},
        {"issue": "2024年第2期", "slug": "li2", "file": "li2.md", "kind": "article"},
    ], aliases=["LI2024"], conflicts=[{"field": "year"}])
    lines: list[bytes] = []
    for index in range(200):
        if index == 2:
            lines.append(b'{"key": "broken", "fields": {')
        elif index == 3:
            lines.append(b'\xff\xfe not json at all')
        elif index == 4:
            lines.append(b"")
        elif index == 158:
            lines.append(json.dumps(target, ensure_ascii=False).encode("utf-8"))
        else:
            lines.append(json.dumps(_rec(f"fill{index:04d}",
                                         {"title": f"占位 {index}"}),
                                    ensure_ascii=False).encode("utf-8"))
    _write_lines(path, lines)
    return lines[158], target


def _oracle(raw: bytes) -> tuple[str, str]:
    pretty = json.dumps(json.loads(raw.decode("utf-8")), ensure_ascii=False, indent=2)
    return pretty, hashlib.sha256(pretty.encode("utf-8")).hexdigest()


def test_raw_record_button_opens_dialog_with_line_total_pretty_and_sha(tmp_path):
    db = tmp_path / "bibliography.jsonl"
    raw, target = _make_main_db(db)
    host, view = _select_db(_hit(target), db)
    tree = view.build()
    assert QV.RAW_BUTTON in _texts(tree)
    actions = view._detail_actions()
    assert len(actions) == 4
    _button(tree, QV.RAW_BUTTON).on_click(None)   # 走真实按钮回调

    assert len(host.page.dialogs) == 1
    dialog = host.page.dialogs[0]
    texts = _texts(dialog)
    assert "原始记录 · li2024analysis" in texts
    assert "行号 159 / 200 · 记录 SHA256" in texts
    pretty, sha = _oracle(raw)
    assert len(pretty) > 12000 and len(pretty) < 80000   # 12KB 记录不截断
    assert sha in texts
    field = [node for node in _walk(dialog) if isinstance(node, ft.TextField)][0]
    assert field.value == pretty                    # 正文逐字符
    assert field.read_only is True and field.multiline is True
    assert len(field.value) < 80000
    assert any(event[0] == "bib_raw_record_opened" and event[1]["line"] == 159
               for event in host.events)


def test_raw_record_copy_json_and_raw_line_and_close(tmp_path):
    db = tmp_path / "bibliography.jsonl"
    raw, target = _make_main_db(db)
    host, view = _select_db(_hit(target), db)
    pretty, _sha = _oracle(raw)
    before = (hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns)

    view._raw_record_open()
    dialog = host.page.dialogs[-1]
    _button(dialog, "复制 JSON").on_click(None)
    assert host.copied[-1] == pretty
    _button(dialog, "复制原文行").on_click(None)
    assert host.copied[-1] == raw.decode("utf-8")     # sed -n 159p 的原行，无改写
    _button(dialog, "关闭").on_click(None)
    assert host.page.dialogs == [] and C._open_dialogs == []

    # Esc 与「关闭」同一入口：各关一次不报错。
    view._raw_record_open()
    assert C.dismiss_top_dialog(host.page) is True
    assert host.page.dialogs == [] and C._open_dialogs == []
    after = (hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns)
    assert before == after                            # 库副本 sha256 + mtime 不变


def test_raw_record_skips_bad_lines_and_handles_crlf(tmp_path):
    db = tmp_path / "bibliography.jsonl"
    good = json.dumps(_rec("wanted", {"title": "t"}), ensure_ascii=False)
    db.write_bytes(b'\xff\xfe bad line\r\n' + b"not json\r\n" + good.encode("utf-8") + b"\r\n")
    host, view = _select_db(_hit(_rec("wanted", {"title": "t"})), db)
    view._raw_record_open()
    texts = _texts(host.page.dialogs[0])
    assert "行号 3 / 3 · 记录 SHA256" in texts
    field = [node for node in _walk(host.page.dialogs[0])
             if isinstance(node, ft.TextField)][0]
    assert "\r" not in field.value
    _button(host.page.dialogs[0], "复制原文行").on_click(None)
    assert host.copied[-1] == good                    # \r\n 已按行切掉，不带 CR


def test_raw_record_failure_paths_are_chinese_and_closeable(tmp_path, monkeypatch):
    db = tmp_path / "bibliography.jsonl"
    _make_main_db(db)

    # 1) key 不在库里
    host, view = _select_db(_hit(_rec("ghost", {"title": "t"})), db)
    view._raw_record_open()
    texts = _texts(host.page.dialogs[0])
    assert QV.RAW_NOT_FOUND in texts and QV.RAW_NEXT_HINT in texts
    _button(host.page.dialogs[0], "打开库文件").on_click(None)
    assert host.opened == [str(db)]
    _button(host.page.dialogs[0], "关闭").on_click(None)
    assert host.page.dialogs == []

    # 2) record 为空 → 无原始记录（不读库）
    host2 = _Host()
    view2 = QV.QueryView(host2)
    view2.hits = [{"key": "ghostkey", "record": {}}]
    view2.selected_key = "ghostkey"
    view2._raw_record_open()
    assert QV.RAW_NO_RECORD in _texts(host2.page.dialogs[0])
    assert host2.events[-1][0] == "bib_raw_record_failed"

    # 3) 未配置引用库路径（R2-1 三级回退 stats db / stats path / 用户默认库全空）
    monkeypatch.setattr(GIT, "default_db", lambda notes="": "")
    host3, view3 = _select_db(_hit(_rec("k", {"title": "t"})), Path(""))
    host3.stats_data["db"] = ""
    view3._raw_record_open()
    assert QV.RAW_NO_DB in _texts(host3.page.dialogs[0])

    # 4) 读失败（库路径是目录）→ 中文类型 + 下一步，不崩溃
    host4, view4 = _select_db(_hit(_rec("k", {"title": "t"})), tmp_path)
    view4._raw_record_open()
    texts4 = _texts(host4.page.dialogs[0])
    assert any(text.startswith(("读取失败：IsADirectoryError；", "读取失败：PermissionError；"))
               for text in texts4)
    assert "库文件可能被占用或已损坏" in " ".join(texts4)
    _button(host4.page.dialogs[0], "关闭").on_click(None)
    assert host4.page.dialogs == []


def test_db_fallback_prefers_configured_paths_then_user_default(monkeypatch):
    """R2-1（DEF-01）：stats["db"] → stats["path"] → GIT.default_db，全空才空串。"""
    host = _Host()
    view = QV.QueryView(host)
    host.stats_data = {"db": "/tmp/explicit.jsonl"}
    assert view._db() == "/tmp/explicit.jsonl"
    # 真实引擎 stats 只有 path（bibdb_cli.cmd_stats → store.stats()，无 db 键）
    host.stats_data = {"path": "/tmp/engine_stats.jsonl", "records": 1}
    assert view._db() == "/tmp/engine_stats.jsonl"
    # 第三级：用户设置里的默认库（兄弟页同款回退）
    host.stats_data = {}
    monkeypatch.setattr(GIT, "default_db", lambda notes="": "/tmp/user_default.jsonl")
    assert view._db() == "/tmp/user_default.jsonl"
    monkeypatch.setattr(GIT, "default_db", lambda notes="": "")
    assert view._db() == ""


def test_open_log_uses_resolved_db_path_not_only_stats_db(monkeypatch):
    """R2-1：``_open_log`` 与「原始记录」共用 ``_db()``，不再静默失效。"""
    host = _Host()
    view = QV.QueryView(host)
    host.stats_data = {"path": "/tmp/from_stats/path/bibliography.jsonl"}
    view._open_log("k")
    assert host.opened == ["/tmp/from_stats/path/bibliography.jsonl"]
    host.opened.clear()
    host.stats_data = {}
    monkeypatch.setattr(GIT, "default_db",
                        lambda notes="": "/tmp/fallback/bibliography.jsonl")
    view._open_log("k")
    assert host.opened == ["/tmp/fallback/bibliography.jsonl"]


def test_raw_record_zero_write_guard(monkeypatch, tmp_path):
    db = tmp_path / "bibliography.jsonl"
    raw, target = _make_main_db(db)
    copy = tmp_path / "copy.jsonl"
    copy.write_bytes(db.read_bytes())
    before = (hashlib.sha256(copy.read_bytes()).hexdigest(), os.stat(copy).st_mtime_ns)

    real_open = builtins.open
    modes: list[str] = []

    def guarded(file, mode="r", *args, **kwargs):
        modes.append(str(mode))
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"只读契约被破坏：open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    result = QV._locate_record(str(copy), "li2024analysis")
    monkeypatch.setattr(builtins, "open", real_open)

    assert result["status"] == "ok" and result["line"] == 159
    assert modes and all(mode == "r" for mode in modes)
    after = (hashlib.sha256(copy.read_bytes()).hexdigest(), os.stat(copy).st_mtime_ns)
    assert before == after


def test_raw_record_performance_budget(tmp_path):
    db = tmp_path / "big.jsonl"
    lines = [json.dumps(_rec(f"k{i:05d}", {"title": f"t{i}"}),
                        ensure_ascii=False).encode("utf-8") for i in range(3874)]
    target = _rec("k03869", {"title": "target", "pages": "1-2"})
    lines.insert(3869, json.dumps(target, ensure_ascii=False).encode("utf-8"))
    _write_lines(db, lines)
    assert len(lines) == 3875

    started = time.perf_counter()
    result = QV._locate_record(str(db), "k03869")
    locate_seconds = time.perf_counter() - started
    started = time.perf_counter()
    pretty = QV._pretty_record_text(result["raw"])
    pretty_seconds = time.perf_counter() - started
    print(f"\n[perf] locate 3875 行 = {locate_seconds:.4f} s; "
          f"单条 pretty = {pretty_seconds:.6f} s")
    assert result["status"] == "ok" and result["line"] == 3870 and result["total"] == 3875
    assert QV._record_sha256(pretty) == result["sha256"]
    assert locate_seconds < 0.5
    assert pretty_seconds < 0.05


def test_raw_helpers_are_pure_readonly_and_field_text_roundtrip():
    assert QV._field_label("pages") == "pages（页码）"
    assert QV._field_label("mystery") == "mystery"
    assert QV._field_text(None) == QV.FIELD_EMPTY
    assert QV._field_text("   ") == QV.FIELD_EMPTY
    assert QV._field_text(0) == "0"
    assert QV._field_text(True) == "true"
    assert QV._source_items({}) == [] and QV._alias_items({}) == []
    raw = json.dumps(_rec("k", {"title": "t"}), ensure_ascii=False)
    assert QV._pretty_record_text(raw) == json.dumps(json.loads(raw),
                                                     ensure_ascii=False, indent=2)
