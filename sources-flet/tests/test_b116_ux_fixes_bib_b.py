"""批 116 FIX-BIB-B：P1 #7/#8/#9/#14 的定向回归（不连引擎、不写库）。"""
from __future__ import annotations

import flet as ft

from ui import bib_dupes as BD
from ui import bib_fieldfix as BF
from ui import bib_import as BI
from ui import bib_normalize as NV
from ui.tokens import Tokens


class _Page:
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

    def set_clipboard(self, text) -> None:
        pass


class _App:
    def __init__(self) -> None:
        self.toasts: list[str] = []
        self.notes_repo = ""
        self.window_w = 1160

    def toast(self, message: str) -> None:
        self.toasts.append(message)

    def copy_text(self, text) -> None:
        pass


class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _Host:
    def __init__(self, db: str = "", *, width: int = 1160, height: int = 780) -> None:
        self.app = _App()
        self.app.window_w = width
        self.store = _Store()
        self.page = _Page(width, height)
        self.t = Tokens()
        self.stats_data = {"db": db, "records": 3883}
        self.events: list = []
        self.tasks: list = []

    def mark(self, event, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)

    def goto(self, view: str) -> None:
        self.events.append(("goto", {"view": view}))

    def _render(self) -> None:
        pass


def _walk(control):
    yield control
    for name in ("controls", "content", "actions"):
        child = getattr(control, name, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(root) -> list[str]:
    out: list[str] = []
    for control in _walk(root):
        for name in ("value", "content", "label", "hint_text", "tooltip"):
            value = getattr(control, name, None)
            if isinstance(value, str):
                out.append(value)
    return out


def _buttons(root, label: str) -> list:
    return [node for node in _walk(root)
            if getattr(node, "content", None) == label
            and node.__class__.__name__.endswith("Button")]


# ── P1-7 入藏：空态主按钮 / 通栏确认条 / 陈旧才重新预览 ────────────────
def test_import_empty_preview_primary_and_full_width_confirm():
    view = BI.BibImportView(_Host())
    tree = view.build()
    texts = _texts(tree)
    assert "尚未预览：选好来源后点「预览（不写库）」" in texts

    bars = [n for n in _walk(tree) if getattr(n, "_bib_confirm_bar", None)]
    assert len(bars) == 1
    bar = bars[0]
    assert bar._bib_confirm_btn.disabled is True
    assert "尚未预览" in (bar._bib_confirm_btn.tooltip or "")
    assert bar._bib_repreview_btn.visible is False

    preview = _buttons(tree, "预览（不写库）")
    assert len(preview) == 1 and isinstance(preview[0], ft.FilledButton)
    assert preview[0]._bib_primary is True

    # 确认条不在右预览栏，而是布局外的通栏 holder（Column 拉伸）。
    assert not any(getattr(n, "_bib_confirm_bar", None)
                   for n in _walk(view._preview_col()))
    holders = [n for n in _walk(tree)
               if isinstance(n, ft.Column) and view._confirm_holder in (n.controls or [])]
    assert holders and holders[0].horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    assert view._confirm_holder.content is bar


def test_import_preview_success_demotes_preview_and_enables_confirm():
    view = BI.BibImportView(_Host())
    view.payload = {"entries": 1, "new": 1, "updated": 0, "conflicts": 0,
                    "skipped": 0, "changes": 1, "db_sha256_before": "a" * 64}
    view.expect_sha = "a" * 64
    view.state = "ready"
    tree = view.build()
    preview = _buttons(tree, "预览（不写库）")
    assert len(preview) == 1 and isinstance(preview[0], ft.OutlinedButton)
    bar = [n for n in _walk(tree) if getattr(n, "_bib_confirm_bar", None)][0]
    assert bar._bib_confirm_btn.disabled is False
    assert bar._bib_repreview_btn.visible is False


def test_import_stale_shows_repreview_only_then():
    view = BI.BibImportView(_Host())
    view.payload = {"entries": 1, "new": 1, "changes": 1,
                    "db_sha256_before": "a" * 64}
    view.expect_sha = "a" * 64
    view.state = "stale"
    view.dirty_reason = "库 sha 已变化"
    tree = view.build()
    bar = [n for n in _walk(tree) if getattr(n, "_bib_confirm_bar", None)][0]
    assert bar._bib_repreview_btn.visible is True
    assert isinstance(bar._bib_repreview_btn, ft.FilledButton)
    assert bar._bib_confirm_btn.disabled is True


# ── P1-8 查重合并：未选对不编数字，编辑器禁用 ──────────────────────────
def test_dupes_no_pair_has_no_fake_impact_and_editors_disabled():
    view = BD.BibDupesView(_Host())
    band = view._plan_band()
    texts = _texts(band)
    assert "影响 — · 先选一对候选" in texts
    assert any(text.startswith("字段取值 —") for text in texts)
    assert not any("影响 2 条" in text for text in texts)
    assert not any("6 字段取值" in text for text in texts)

    key_field = [n for n in _walk(band)
                 if isinstance(n, ft.TextField) and n.hint_text == "合并后 key"][0]
    assert key_field.disabled is True
    selects = [n for n in _walk(band) if n.__class__.__name__ == "Select"]
    assert selects and selects[0].disabled is True


def test_dupes_pair_drives_field_count_and_enables_editors():
    view = BD.BibDupesView(_Host())
    view.pairs = [{"a": "a", "b": "b", "similarity": 0.95, "diff": 2,
                   "title": "T", "year": 2020, "status": "候选"}]
    view.selected = 0
    view.left = {"key": "a", "entry_type": "article",
                 "fields": {"title": "A", "year": "2020"}}
    view.right = {"key": "b", "entry_type": "book",
                  "fields": {"title": "B", "year": "2020"}}
    view.merged_key = "a"
    view.entry_type = "article"

    band = view._plan_band()
    texts = _texts(band)
    assert any("影响 2 条 → 1 条" in text for text in texts)
    assert "2 字段取值 · 旧 key 记别名 · 未写库（dry-run）" in texts
    key_field = [n for n in _walk(band)
                 if isinstance(n, ft.TextField) and n.hint_text == "合并后 key"][0]
    assert key_field.disabled is False
    assert key_field.value == "a"
    selects = [n for n in _walk(band) if n.__class__.__name__ == "Select"]
    assert selects and selects[0].disabled is False

    # 字段数从当前 pair 现算，不是常量。
    view.left["fields"] = {"title": "A"}
    view.right["fields"] = {"title": "B"}
    texts2 = _texts(view._plan_band())
    assert "1 字段取值 · 旧 key 记别名 · 未写库（dry-run）" in texts2


# ── P1-9 字段归位：真实 candidates / 筛选 chip / 批量按钮 ──────────────
REAL_PAYLOAD = {"rows": [
    {"key": "k1", "entry_type": "article", "missing": ["year"],
     "candidates": [{"field": "year", "value": "1921", "evidence": "据题名",
                     "risk": "low", "source": "registry"}]},
    {"key": "k2", "entry_type": "unknown", "missing": [],
     "candidates": [{"value": "article", "evidence": "类型未知",
                     "risk": "high", "source": "entry_type"}]},
    {"key": "Gettier1963", "entry_type": "article", "missing": [],
     "candidates": [], "key_candidate": "gettier_1963",
     "key_reason": "键含大写", "key_risk": "medium"},
]}


def test_fieldfix_parse_real_candidates_and_key_rows():
    rows = BF.parse_suggestions(REAL_PAYLOAD)
    assert [(row["key"], row["field"]) for row in rows] == [
        ("k1", "year"), ("k2", "entry_type"), ("Gettier1963", "key")]
    assert rows[0]["candidate"] == "1921" and rows[0]["reason"] == "据题名"
    assert rows[1]["candidate"] == "article" and rows[1]["reason"] == "类型未知"
    assert rows[2]["candidate"] == "gettier_1963" and rows[2]["reason"] == "键含大写"
    assert BF.field_label(rows[2]["field"]) == "键"


def test_fieldfix_row_shows_candidate_and_reason_and_chips_clickable():
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions(REAL_PAYLOAD)
    view.state = "ready"
    tree = view.build()
    texts = _texts(tree)
    assert "1921" in texts and "据题名" in texts
    assert "gettier_1963" in texts and "键含大写" in texts

    chips = [n for n in _walk(tree) if getattr(n, "_bib_filter_chip", None)]
    assert {chip._bib_filter_chip for chip in chips} == {"field", "confidence"}
    assert all(chip.tooltip for chip in chips)
    before = set(view.fields)
    field_chip = [chip for chip in chips if chip._bib_filter_chip == "field"][0]
    field_chip.on_click(None)
    assert view.fields != before


def test_fieldfix_apply_all_only_when_rows_checked():
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions(REAL_PAYLOAD)
    view.state = "ready"

    empty = view._bottom()
    empty_text = " ".join(_texts(empty))
    assert "全部应用" not in empty_text
    assert "预览所选" in empty_text

    view.checked = {row["id"] for row in view.rows}
    bar = view._bottom()
    assert bar._bib_batch_bar is not None
    button = [n for n in _walk(bar) if getattr(n, "_bib_apply_all_btn", None)][0]
    assert isinstance(button, ft.OutlinedButton)
    assert button.content == "全部应用…（3）"
    assert "预览所选" in _texts(bar)


# ── P1-14 回读校验：None / 重复可空 / 无报告禁用+原因 / 识别解释 ───────
def test_normalize_empty_counts_and_labels_have_no_none_or_duplicate_suffix():
    view = NV.NormalizeView(_Host())
    tree = view.build()
    texts = _texts(tree)
    assert not any("None" in text for text in texts)
    assert "0 错误 · 0 警告 · 0 记录 · 0 键" in texts
    assert "（可空）（可空）" not in " ".join(texts)
    assert any("原件（.bib）" in text for text in texts)
    assert any("正文（.tex）" in text for text in texts)


def test_normalize_no_report_disables_open_copy_with_reason():
    view = NV.NormalizeView(_Host())
    tree = view.build()
    for label in ("打开", "复制"):
        buttons = _buttons(tree, label)
        assert buttons and buttons[0].disabled is True, label
        assert "还没有报告" in (buttons[0].tooltip or ""), label
    header = _buttons(tree, "打开报告")
    assert header and header[0].disabled is True
    assert "还没有报告" in (header[0].tooltip or "")

    identify = [n for n in _walk(tree) if getattr(n, "_bib_identify_btn", None)]
    assert identify and identify[0].tooltip


def test_normalize_unparseable_report_uses_dashes_not_none():
    view = NV.NormalizeView(_Host())
    view.report_text = "not a report"
    view.data = NV.parse_report(view.report_text)
    texts = _texts(view.build())
    assert not any("None" in text for text in texts)
    assert "— 错误 · — 警告 · — 记录 · — 键" in texts


def test_normalize_existing_report_enables_open_and_copy(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# report\n", encoding="utf-8")
    view = NV.NormalizeView(_Host())
    view.report_path = str(report)
    tree = view.build()
    for label in ("打开", "复制"):
        buttons = _buttons(tree, label)
        assert buttons and buttons[0].disabled is False, label


def test_fieldfix_no_candidate_row_still_shows_dash():
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions({"rows": [
        {"key": "k3", "entry_type": "article", "missing": ["doi"],
         "candidates": []},
    ]})
    view.state = "ready"
    assert view.rows[0]["candidate"] == ""
    assert view._value_for(view.rows[0]) == ""
    texts = _texts(view.build())
    assert "—" in texts
