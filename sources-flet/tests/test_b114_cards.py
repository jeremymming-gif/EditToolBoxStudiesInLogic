"""批 114 收口（用户 m02855）：输入区白卡化 + 顶栏搜索白卡胶囊。

批 115（D1-115-12）改为「一张纸」白面板后，锁的仍是同一件事——字段不得直接躺在
纸灰页面（`palette.bg`）上；但页面级白卡不再存在，白底由 `C.page_body(panel=True)`
的圆角面板提供。保留四组契约（全部离线，不建窗口、不跑引擎、不联网）：

1. 单篇「输入」区（源稿件 / 输出根目录）与高级参数都落在同一张圆角白面板上，
   不再各自套一层白底描边 C.card。
2. 一校核查（proofcheck）输入区不再套卡：section_label + 发丝线 + 帮助 inline_note，
   初排 tex / 语种 / 顺手修选项直接落在白面板上。
3. 单篇页面所有 TextField 都有白面板祖先（`palette.surface` + 顶部圆角），
   页面级输入不再有 C.card/Border 白卡夹层（元数据/AI 面板内部卡不属本包）。
4. 顶栏搜索区是白色圆角胶囊：搜索框 + 外观图标，描边只在胶囊上，输入框本身无框
   （换肤/缩放时胶囊属性同步更新，不换控件）。
"""
from __future__ import annotations

import flet as ft

import app.main as M
from app.ui import checks as CK
from app.ui.tokens import Tokens
from test_b114_polish import _build_single, _walk


def _containers(control) -> list[ft.Container]:
    return [ctl for ctl in _walk(control) if isinstance(ctl, ft.Container)]


def _contains(control, target) -> bool:
    return any(ctl is target for ctl in _walk(control))


def _card_around(root, target, t: Tokens) -> ft.Container | None:
    """包含 target 的最内层「白卡」（surface 底 + Border 描边的 Container）。"""
    hits = [ctl for ctl in _containers(root)
            if ctl.bgcolor == t.palette.surface
            and isinstance(ctl.border, ft.Border)
            and _contains(ctl, target)]
    return hits[-1] if hits else None


def _panel_around(root, target, t: Tokens) -> ft.Container | None:
    """包含 target 的最内层「白面板」（surface 底 + BorderRadius 圆角的 Container）。"""
    hits = [ctl for ctl in _containers(root)
            if ctl.bgcolor == t.palette.surface
            and isinstance(ctl.border_radius, ft.BorderRadius)
            and _contains(ctl, target)]
    return hits[-1] if hits else None


def _padding_tuple(container: ft.Container) -> tuple[float, float, float, float]:
    pad = container.padding
    return (round(pad.left, 2), round(pad.top, 2),
            round(pad.right, 2), round(pad.bottom, 2))


# ── 1. 单篇「输入」区落在白面板上，不再套白卡 ─────────────────────────────
def test_single_input_area_sits_on_white_panel_without_nested_card():
    page, root = _build_single()
    t = page.t
    source = page.fields["single_source"][0]
    output = page.fields["single_out"][0]
    panel = _panel_around(root, source, t)
    assert panel is not None, "源稿件字段应在白面板上"
    assert _panel_around(root, output, t) is panel, "两行应共用同一张页面白面板"
    assert _panel_around(root, page.fields["convert_sty"][0], t) is panel, \
        "高级参数也直接落在同一张白面板上"
    # 面板 = 「一张纸」：surface 底 + 顶部 10 圆角（底部贴齐，不圆）。
    assert panel.bgcolor == t.palette.surface
    assert isinstance(panel.border_radius, ft.BorderRadius)
    assert panel.border_radius.top_left == t.radius("panel")
    assert panel.border_radius.top_right == t.radius("panel")
    assert panel.border_radius.bottom_left == 0
    assert panel.border_radius.bottom_right == 0
    # page_body 原内边距不变（24/12/24/18）。
    assert _padding_tuple(panel) == (t.px(24), t.px(12), t.px(24), t.px(18))
    # 输入区 / 高级参数都不再套白底描边卡（回归 m02855 的中间态）。
    assert _card_around(root, source, t) is None
    assert _card_around(root, output, t) is None
    assert _card_around(root, page.fields["convert_sty"][0], t) is None

    panel_texts = [str(ctl.value) for ctl in _walk(panel)
                   if isinstance(ctl, ft.Text)]
    assert any("可直接粘贴路径" in value for value in panel_texts)
    browse = [ctl for ctl in _walk(panel)
              if isinstance(ctl, ft.OutlinedButton)
              and "浏览…" == str(getattr(ctl, "content", ""))]
    assert len(browse) >= 2, "输入区两行各有一个「浏览…」按钮"
    # 组标题留在面板上、不再是卡片标题。
    assert _find_text(panel, "输入") is not None
    assert _find_text(panel, "处理选项") is not None


def _find_text(control, value: str) -> ft.Text | None:
    for ctl in _walk(control):
        if isinstance(ctl, ft.Text) and str(ctl.value) == value:
            return ctl
    return None


# ── 2. 一校核查输入区去卡：section_label + 发丝线 ─────────────────────────
def test_proofcheck_input_section_uses_section_label_and_hairline():
    page, _ = _build_single()
    t = page.t
    section = CK.proofcheck_section(page)
    assert isinstance(section, ft.Column)
    assert _find_text(section.controls[0], "输入") is not None, "分区小标题仍在"
    hairline = section.controls[1]
    assert isinstance(hairline, ft.Container)
    assert hairline.bgcolor == t.palette.border
    assert str(hairline.data).endswith("border")
    input_area = section.controls[2]
    field = page.fields["proof_tex"][0]
    assert _contains(input_area, field), "初排 tex 路径输入应在白面板上"
    assert not _contains(section.controls[0], field)
    # 语种下拉 + 「顺手改」选项在同一输入区。
    assert _find_text(input_area, "语种") is not None
    assert _find_text(input_area, "顺手把无歧义的改掉（另存新文件）") is not None
    assert any("浏览…" == str(getattr(ctl, "content", ""))
               for ctl in _walk(input_area))
    # 旧的白卡（surface + Border + 14 内边距）已不存在；下拉控件自身描边底不算卡。
    assert [ctl for ctl in _containers(section)
            if ctl.bgcolor == t.palette.surface
            and isinstance(ctl.border, ft.Border)
            and _padding_tuple(ctl) == (t.px(14),) * 4] == []
    assert _card_around(section, field, t) is None
    # 帮助 4 行改为 inline_note（文案不变）。
    notes = section.controls[3:]
    assert len(notes) == 4
    note_texts = [str(ctl.value) for note in notes for ctl in _walk(note)
                  if isinstance(ctl, ft.Text)]
    assert any("--fix" in value for value in _texts(section)) or any(
        "一校核查后.tex" in value for value in note_texts)
    assert any("不覆盖稿件" in value for value in note_texts)


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _walk(control) if isinstance(ctl, ft.Text)]


# ── 3. 单篇页面：输入框不再直接躺在纸灰页面上 ─────────────────────────
def test_no_single_page_input_lies_directly_on_page_background():
    page, root = _build_single()
    t = page.t
    fields = [ctl for ctl in _walk(root) if isinstance(ctl, ft.TextField)]
    assert len(fields) >= 6, "单篇页应有源稿/输出/高级参数/元数据等输入框"
    for field in fields:
        label = getattr(field, "_form_label", getattr(field, "label", "?"))
        panel = _panel_around(root, field, t)
        assert panel is not None, f"输入框 {label} 不在白面板上"
        assert panel.bgcolor == t.palette.surface
        assert isinstance(panel.border_radius, ft.BorderRadius), \
            f"输入框 {label} 的白面板应是圆角「一张纸」"
    # 页面级输入（非元数据/AI 面板内部）不得再夹一层白底描边卡。
    for key in ("single_source", "single_out", "convert_vol", "convert_sty"):
        assert _card_around(root, page.fields[key][0], t) is None, f"{key} 仍套着白卡"


# ── 4. 顶栏搜索白卡胶囊 ──────────────────────────────────────────────
def _app(t: Tokens):
    app = M.App.__new__(M.App)
    app.t = t
    app._search_query = ""
    app._toolbar_row = None
    app.current_page_key = "home"
    app.goto = lambda *a, **k: None
    app.toggle_sidebar = lambda *a, **k: None
    app._toolbar_spec = lambda: ("文稿", "", None)
    return app


def test_toolbar_search_is_white_pill_capsule_without_inner_border():
    t = Tokens("light", 1.0, 96)
    app = _app(t)
    row = app._toolbar_content()
    capsule = app._toolbar_search_card
    search = app._toolbar_search
    assert row.controls[-2] is capsule, "工具栏行内应放胶囊（而不是裸搜索框）"
    assert capsule.bgcolor == t.palette.surface
    assert isinstance(capsule.border, ft.Border)
    assert capsule.border.top.color == t.palette.border
    assert capsule.border_radius == t.radius("pill")
    assert capsule.height == t.px(32)
    assert capsule.content.controls == [search, app._toolbar_tune]
    assert search.border is None, "输入框本身无框，描边由白卡胶囊提供"
    assert search.bgcolor == t.palette.surface
    assert search.height == t.control_h()
    # 换肤+缩放只改属性不换控件。
    t2 = Tokens("dark", 1.25, 96)
    app.t = t2
    app._toolbar_content()
    assert app._toolbar_search is search and app._toolbar_search_card is capsule
    assert capsule.bgcolor == t2.palette.surface
    assert capsule.border.top.color == t2.palette.border
    assert capsule.height == t2.px(32)
    assert capsule.border_radius == t2.radius("pill")
    assert search.height == t2.control_h()
