"""批 116 CLIP-FIX-A：全壳遮挡审计的 P1 + P2 定向回归。

P1 库健康 `bib_health.py`：严重度筛选 width=100 时选中值「全部严重度」被省略成
「全部严…」（扣掉下拉箭头与 8/8 内边距后文字只有约 52 px）。
P2 字段归位 `bib_fieldfix.py`：「缺失原因 / 候选值」两列原先都是 0 内边距，省略号
几乎贴住下一列（约 7 px 光学间距）而被连读；滚到底时最后一行又贴着 44/56 常驻条。

这些断言都只看控件几何/文案，不启动 Flet 也不连引擎。
"""

from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from ui import bib_fieldfix as BF
from ui import bib_health as HV
from ui.tokens import Tokens


class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self, width: int = 1160, height: int = 780) -> None:
        self.on_keyboard_event = None
        self.dialogs: list = []
        self.tasks: list = []
        self.width = width
        self.height = height

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)

    def update(self) -> None:
        pass


class _Host:
    """健康页 / 字段归位页共用的宿主桩（只实现两个视图真正用到的方法）。"""

    def __init__(self, width: int = 1160, height: int = 780) -> None:
        self.t = Tokens("light")
        self.page = _FakePage(width, height)
        self.app = SimpleNamespace(store=_Store(), notes_repo="", window_w=width,
                                   copy_text=lambda text: None, toast=lambda msg: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl",
                           "sha256": "9c02" + "b" * 60}
        self.store = self.app.store
        self.events: list = []

    def mark(self, event, **kwargs) -> None:
        self.events.append((event, kwargs))

    def notify(self, message, **kwargs) -> None:
        self.events.append(("notify", message))

    def run_task_ui(self, fn) -> None:
        self.page.tasks.append(fn)

    def goto(self, *args, **kwargs) -> None:
        pass

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _selects(root) -> list:
    """页面里的自绘下拉（`C.select` → `components.Select`）。"""
    return [node for node in _walk(root)
            if hasattr(node, "options") and hasattr(node, "_value_text")]


def _option_texts(select) -> list[str]:
    return [getattr(option, "text", "") for option in select.options]


# ── P1：严重度筛选的选中值必须整字显示 ──────────────────────────────
def test_health_severity_filter_width_holds_full_label():
    view = HV.HealthView(_Host())
    row = view._filter()
    selects = _selects(row)
    severity = [s for s in selects if "全部严重度" in _option_texts(s)]
    assert len(severity) == 1, _option_texts(selects[0]) if selects else "没有下拉"
    severity = severity[0]

    # 100 的旧值：100 - (8+8 内边距 + 14 箭头 + 6 间距) = 64，5 个字 13 px 需 65，
    # 实际还差箭头外框，审计实测只剩约 52 px，所以必须放宽到 ≥ 124。
    assert 5 * 13 + 48 <= HV.SEVERITY_FILTER_W, "5 字标签按 13 px 至少 113，留余量到 124"
    assert 5 * 13 + 48 > 100, "旧宽度 100 容不下 5 字标签——这条断言就是回归判据"
    assert severity.width == HV.SEVERITY_FILTER_W == 132

    # 选中值是整字，没有靠缩短标签/加省略号绕过问题：`.value` 是原始 key（空串
    # = 全部），显示文本必须整 5 字。
    assert severity.value == ""
    assert severity._value_text.value == "全部严重度"
    assert "…" not in severity._value_text.value
    assert "全部严重度" in (severity.tooltip or "按严重度筛选")


def test_health_severity_options_are_full_labels_not_abbreviations():
    view = HV.HealthView(_Host())
    severity = [s for s in _selects(view._filter())
                if "全部严重度" in _option_texts(s)][0]
    assert _option_texts(severity) == ["全部严重度", "警告", "提示"]
    # 不靠 max_lines 截断：文本仍可整行（max_lines=1 是单行布局，不是砍字），
    # 且整字文案在 tooltip 里可读。
    assert severity._value_text.max_lines == 1
    assert severity._value_text.overflow == ft.TextOverflow.ELLIPSIS
    assert severity.tooltip == "按严重度筛选：全部严重度 / 警告 / 提示"


# ── P2：字段归位「缺失原因 / 候选值」列宽与列间距 ──────────────────
def test_fieldfix_reason_and_candidate_columns_have_gap_and_width():
    view = BF.BibFieldFixView(_Host())
    headers, wide_weights, _, _ = view._columns()
    reason = headers.index("缺失原因")
    candidate = headers.index("候选值")
    assert wide_weights[candidate] > wide_weights[reason]
    assert wide_weights[reason] >= 1.4      # 原 1.32：给足最小宽
    assert wide_weights[candidate] >= 1.48  # 原 1.32：候选值最宽

    view.narrow = True
    n_headers, narrow_weights, _, _ = view._columns()
    n_reason, n_candidate = n_headers.index("缺失原因"), n_headers.index("候选值")
    assert narrow_weights[n_candidate] > narrow_weights[n_reason]
    assert narrow_weights[n_candidate] >= 1.5
    assert BF.CELL_PAD_R >= 12


def test_fieldfix_row_cells_reserve_right_padding_for_gap():
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions({"rows": [
        {"key": "k1", "entry_type": "article", "missing": ["year"],
         "candidates": [{"field": "year", "value": "1921", "evidence": "据题名",
                         "risk": "low", "source": "registry"}]},
    ]})
    view.state = "ready"
    t = view.t
    control = view._row_control(view.rows[0], [1, 2])
    row = control.content
    cells = [cell for cell in row.controls if isinstance(cell, ft.Container)]
    # 第一格是固定宽 34 的勾选框，其余是数据列。
    data_cells = cells[1:]
    assert data_cells, row.controls
    for cell in data_cells[:-1]:
        padding = cell.padding
        assert padding is not None and padding.right >= t.px(BF.CELL_PAD_R) >= 12
    # 末列不补内边距，右边缘与表头（统一右 8）对齐。
    assert data_cells[-1].padding is None or not data_cells[-1].padding.right


# ── P2：滚动表尾部留白（底部 44/56 常驻条不切末行） ─────────────────
def test_fieldfix_scroll_table_ends_with_bottom_safety_pad():
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions({"rows": [
        {"key": f"k{i}", "entry_type": "article", "missing": ["year"],
         "candidates": [{"field": "year", "value": "1921", "evidence": "据题名",
                         "risk": "low", "source": "registry"}]}
        for i in range(3)
    ]})
    view.state = "ready"
    table = view._table()
    assert isinstance(table, ft.Container) and table.expand is True
    column = table.content
    # 批 118 D2：字段归位主表改常显条（6px 可拖），仍保留尾部安全留白。
    assert isinstance(column, ft.Column)
    assert isinstance(column.scroll, ft.Scrollbar)
    assert column.scroll.thumb_visibility is True
    last = column.controls[-1]
    assert isinstance(last, ft.Container) and last.content is None
    assert last.height == view.t.px(BF.TABLE_BOTTOM_PAD) >= 12


# ── P2：省略号不是砍字，全文仍可读（无 max_lines 丢内容） ────────────
def test_fieldfix_ellipsized_cells_keep_full_text_in_tooltip():
    long_reason = "publisher 字段里混进了出版地（南京：凤凰出版社），需要拆分"
    long_value = "南京：凤凰出版社（南京大学出版社重印，2011 年第 2 版）"
    view = BF.BibFieldFixView(_Host())
    view.rows = BF.parse_suggestions({"rows": [
        {"key": "k9", "entry_type": "article", "missing": ["publisher"],
         "candidates": [{"field": "publisher", "value": long_value,
                         "evidence": long_reason, "risk": "low",
                         "source": "registry"}]},
    ]})
    view.state = "ready"
    texts = [node for node in _walk(view._table()) if isinstance(node, ft.Text)]
    by_value = {node.value: node for node in texts}
    assert long_reason in by_value and long_value in by_value
    for value in (long_reason, long_value):
        node = by_value[value]
        assert node.max_lines == 1          # 单行布局
        assert "…" not in node.value        # 内容没被写死截断
        assert node.tooltip == value        # hover 能读到全文
