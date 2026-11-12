"""FIX-20261112 Q10（AFF-LABEL-01）：作者单位区按钮/计数必须带语言。

三态（0/1/3 行）× 两语言（中文/英文单位）下，两个「添加单位」与两个「n/3」
必须一眼可辨；上限仍禁用、limit hint 仍显示、点击仍 +1、行高/卡片宽度不变。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import flet as ft
import pytest

from ui import components as C
from ui import metadata_form as MF
from ui.tokens import Tokens


class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _Root:
    def __init__(self) -> None:
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


class _Page:
    def __init__(self) -> None:
        self.t = Tokens("light")
        self.app = SimpleNamespace(store=_Store(), toast=lambda message: None)
        self.root = _Root()


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None and isinstance(content, ft.Control):
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    return out


def _panel() -> tuple[MF.MetadataPanel, MF.Author, _Page]:
    page = _Page()
    panel = MF.MetadataPanel(page)
    panel.build()                      # 建立 _body/_summary，供 _after_change 重渲染
    author = MF.Author(name_cn="张三", name_en="Zhang San")
    panel.form.authors = [author]
    return panel, author, page


def _seed(author: MF.Author, lines: int) -> None:
    author.affiliations_cn = [f"单位{i + 1}" for i in range(lines)]
    author.affiliations_en = [f"Affiliation {i + 1}" for i in range(lines)]


def _labels(tree) -> list[str]:
    return [str(getattr(node, "content", "")) for node in _walk(tree)
            if isinstance(node, ft.OutlinedButton)
            and str(getattr(node, "content", "")).startswith(("添加", "加"))]


def _counts(tree) -> list[str]:
    return [node.value for node in _walk(tree)
            if isinstance(node, ft.Text) and isinstance(node.value, str)
            and "/3" in node.value]


def _hints(tree) -> list[str]:
    return [node.value for node in _walk(tree)
            if isinstance(node, ft.Text) and isinstance(node.value, str)
            and "上限" in node.value]


@pytest.mark.parametrize("lines", [0, 1, 3])
@pytest.mark.parametrize("lang", ["cn", "en"])
def test_button_and_count_carry_language_in_every_state(lines: int, lang: str) -> None:
    panel, author, _ = _panel()
    _seed(author, lines)
    tree = panel._author_card(0, author)

    labels = _labels(tree)
    assert labels == ["添加中文单位", "添加英文单位"], f"{lines} 行：按钮文案必须带语言且唯一"
    assert len(set(labels)) == 2

    counts = _counts(tree)
    short = "中文" if lang == "cn" else "英文"
    expected = f"{lines}/{MF.MAX_AFFILIATIONS} {short}单位"
    assert expected in counts
    assert counts == [f"{lines}/{MF.MAX_AFFILIATIONS} 中文单位",
                      f"{lines}/{MF.MAX_AFFILIATIONS} 英文单位"]
    assert len(set(counts)) == 2, "计数也必须两语言可辨（0 行态同样可辨）"


def test_limit_disables_both_buttons_and_keeps_bilingual_hint() -> None:
    panel, author, _ = _panel()
    _seed(author, MF.MAX_AFFILIATIONS)
    tree = panel._author_card(0, author)

    buttons = [node for node in _walk(tree) if isinstance(node, ft.OutlinedButton)
               and str(getattr(node, "content", "")).startswith("添加")]
    assert len(buttons) == 2
    assert [button.disabled for button in buttons] == [True, True]

    hints = _hints(tree)
    assert MF.AFFILIATION_LIMIT_HINT.format(n=1, lang="中文") in hints
    assert MF.AFFILIATION_LIMIT_HINT.format(n=1, lang="英文") in hints


@pytest.mark.parametrize("lines", [0, 1])
def test_below_limit_buttons_enabled_without_hint(lines: int) -> None:
    panel, author, _ = _panel()
    _seed(author, lines)
    tree = panel._author_card(0, author)
    buttons = [node for node in _walk(tree) if isinstance(node, ft.OutlinedButton)
               and str(getattr(node, "content", "")).startswith("添加")]
    assert [button.disabled for button in buttons] == [False, False]
    assert _hints(tree) == []


@pytest.mark.parametrize("lang,attr", [("cn", "affiliations_cn"), ("en", "affiliations_en")])
def test_click_adds_one_line_and_persists_draft(lang: str, attr: str) -> None:
    panel, author, page = _panel()
    _seed(author, 0)

    panel._on_add_affiliation(0, lang)

    assert len(getattr(author, attr)) == 1
    assert getattr(author, attr) == [""]
    draft = page.app.store.data.get(MF.MetadataPanel.DRAFT_KEY)
    assert isinstance(draft, str) and json.loads(draft)
    # 重渲染后计数同步到 1 行（点击路径 = _on_add_affiliation → _after_change）。
    counts = _counts(panel._body)
    short = "中文" if lang == "cn" else "英文"
    assert f"1/{MF.MAX_AFFILIATIONS} {short}单位" in counts


def test_click_at_limit_is_noop() -> None:
    panel, author, _ = _panel()
    _seed(author, MF.MAX_AFFILIATIONS)
    panel._on_add_affiliation(0, "cn")
    assert len(author.affiliations_cn) == MF.MAX_AFFILIATIONS


def test_card_width_and_row_height_unchanged() -> None:
    panel, author, _ = _panel()
    _seed(author, 0)
    tree = panel._author_card(0, author)

    assert isinstance(tree, ft.Container)          # C.card 仍是 Container，无固定 width
    assert tree.width is None
    buttons = [node for node in _walk(tree) if isinstance(node, ft.OutlinedButton)
               and str(getattr(node, "content", "")).startswith("添加")]
    assert all(button.height == panel.page.t.control_h() for button in buttons)  # 28 不变
    # 每个语言区块的「计数 + 按钮」仍在同一行（原结构）。
    add_rows = [node for node in _walk(tree) if isinstance(node, ft.Row)
                and len(node.controls) == 2
                and isinstance(node.controls[0], ft.Text)
                and "/3" in str(node.controls[0].value)
                and isinstance(node.controls[1], ft.OutlinedButton)]
    assert len(add_rows) == 2
    assert all(row.controls[1].content.startswith("添加") for row in add_rows)
