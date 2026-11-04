"""批 115 设计检修：标题层级（P1-2）/ 整期步骤页主操作（P1-3）/ 断点回归（P1-8）。

不建窗口、不跑引擎：
- P1-2：Single / Issue 是 2 级页面（页面→子页/步骤），页头去 crumb，
  只留唯一 h1 + 说明；BibPage 是 3 级，crumb 必须保留。
- P1-3：总览页页面级主色＝「运行整期流水线」；步骤页页面级主色＝
  「运行这一步（仅本步）」，整期运行降为描边次按钮（D1-115-24 覆盖旧 m03093）。
- P1-8：窗口逻辑宽 1000（k=1.0）高级参数 4→2 列、处理选项 2×2→1 列；
  1240 恢复宽布局；Issue 内容宽 <900 单列。
"""
from __future__ import annotations

import inspect

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import components as C
from ui import pages as P
from ui.tokens import Tokens
from test_b112_post_b import _FakeApp, _walk


class _WinApp(_FakeApp):
    """带窗口逻辑宽的假壳；t.k = zoom（批 115 P0-1 起 DPI 交给 Flutter）。"""

    def __init__(self, width: float = 1160, zoom: float = 1.0) -> None:
        super().__init__()
        self.window_w = width
        self.window_h = 700
        self.t = Tokens("light", zoom, 96)


def _build(cls, width: float = 1160, zoom: float = 1.0):
    app = _WinApp(width, zoom)
    page = cls(app)
    root = page.build()
    return page, root


def _texts(root) -> list[str]:
    return [str(ctl.value) for ctl in _walk(root) if isinstance(ctl, ft.Text)]


def _of(root, kind):
    return [ctl for ctl in _walk(root) if isinstance(ctl, kind)]


def _adv_group_rows(page) -> list[list[int]]:
    """高级参数三组的每行单元格数（用来量 4/3/2 列 → 2 列断点）。"""
    advanced = page._advanced_params()
    groups = [ctl for ctl in advanced.controls if isinstance(ctl, ft.Column)]
    return [[len(row.controls) for row in group.controls if isinstance(row, ft.Row)]
            for group in groups]


def _tile_rows(page) -> list[int]:
    tiles = page._option_tiles({})
    return [len(row.controls) for row in tiles.controls if isinstance(row, ft.Row)]


# ── P1-2：2 级页面去 crumb，h1 唯一 ───────────────────────────────────────
def test_single_header_has_only_h1_and_desc():
    page, root = _build(P.SinglePage)
    assert page._crumb is None, "页头 crumb 只保留属性兼容，不应再创建"
    assert page._title_col.controls == [page._child_title, page._flow_desc]
    # 批 116 NAV-B：h1 固定「单篇稿件」，不再靠父级 crumb 标识页面。
    assert _texts(root).count("单篇稿件") == 1, "页头应固定一个「单篇稿件」h1"
    titles = [ctl for ctl in _walk(root)
              if isinstance(ctl, ft.Text) and ctl.value == page._child_title.value]
    assert len(titles) == 1, "页头只能有一个同名 h1（toolbar 标题已隐藏）"
    assert titles[0] is page._child_title
    # 切子页只换说明与 chip 选中态，h1 不变；crumb 不复活（批 116 NAV-B）。
    page.activate("proofcheck")
    assert page._child_title.value == "单篇稿件"
    assert page._title_col.controls == [page._child_title, page._flow_desc]
    assert [chip.content.value for chip in page._flow_chips] == [
        "初排转换", "一校核查"]
    assert page._flow_chips[1].bgcolor == page.app.t.palette.nav_sel
    assert page._crumb is None


def test_issue_header_has_only_h1_and_desc_on_empty_and_step():
    page, root = _build(P.IssuePage)
    # 批 115（D1-115-26）：2 级工作台 h1 固定「整期装配」；步骤名在详情头 h2，
    # 不再把页头 h1 换成步骤名（否则来回切换时页标题会跳）。
    assert page._crumb is None
    assert page._title_col.controls == [page._child_title, page._flow_desc]
    assert _texts(root).count("整期装配") == 1
    assert page._child_title.value == "整期装配"
    # 批 117：无期目录时详情只是空态；有期目录才构建步骤页。
    assert "还没有选择期目录" in _texts(
        page._content_box)
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    assert page._child_title.value == "整期装配"
    # 批 116 P2-1：页头副标题固定产品行，不再随步骤 desc 漂移。
    assert page._flow_desc.value == "一期稿件，从检查到合刊。"
    assert page._title_col.controls == [page._child_title, page._flow_desc]
    assert page._crumb is None
    labels = _texts(page._content_box)
    assert "生成目录" in labels, "步骤详情头必须显示步骤名"
    assert "流水线总览" not in labels, "步骤子页不应重复父页标题"


def test_bib_page_keeps_its_crumb():
    # BibPage 是 3 级（页面 > 组 > 视图），按评审必须保留 crumb；此处只做源码守卫。
    assert "_crumb" in inspect.getsource(P.BibPage.build)


# ── P1-3：页面级主操作随子页切换 ──────────────────────────────────────────
def test_step_page_primary_is_run_this_step_only():
    page, root = _build(P.IssuePage)
    # 批 120 B-354：页头「运行整期…」「从失败处继续…」两按钮删除。
    assert not hasattr(page, ("_run_" + "all_btn"))
    assert not hasattr(page, ("_header_" + "actions"))
    assert not hasattr(page, ("_run_" + "all_secondary"))
    # 批 117：无期目录时详情有选目录 CTA；页头已无实心按钮。
    filled = _of(root, ft.FilledButton)
    assert filled and [str(ctl.content) for ctl in filled] == ["选择期目录"]
    assert page._stop_btn.visible is False

    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    detail_filled = [ctl for ctl in _of(page._content_box, ft.FilledButton)]
    # 批 116 决策（I-2 E′）：toc 与 infocheck 同属「生成目录」视图，两个区块
    # 各有一个主按钮；选中步（02）的主按钮仍是 _step_run_primary。
    contents = [str(ctl.content) for ctl in detail_filled]
    assert "生成目录" in contents and "运行检查" in contents
    assert "运行整期…" not in contents
    toc_btn = next(ctl for ctl in detail_filled if str(ctl.content) == "生成目录")
    assert toc_btn is page._step_run_primary
    assert not hasattr(page, ("_continue_" + "from_failure"))
    # 旧参数区描边「运行这一步」已摘掉，避免两个入口。
    legacy = [ctl for ctl in _of(page._build_step_page("toc"), ft.OutlinedButton)
              if str(ctl.content) == "运行这一步"]
    assert not legacy

    # `issue:overview` 别名不再有总览屏，落到上次步骤（刚才的 toc）。
    page.activate("overview")
    assert page.selected == "toc"
    assert page._stop_btn.visible is False


def test_step_primary_disabled_reason_when_engine_not_ready_and_running():
    page, _root = _build(P.IssuePage)
    page.set_field("issue_dir", "/tmp/期目录")  # 有期目录后才是引擎/运行态原因
    page.activate("toc")
    page.app.engine_status.ok = False
    page.refresh_running()
    assert page._step_run_primary.disabled is True
    assert "引擎未就绪" in (page._step_run_primary.tooltip or "")
    assert not hasattr(page, ("_run_" + "all_btn"))

    page.app.engine_status.ok = True
    page.app.running = True
    page.refresh_running()
    assert page._step_run_primary.disabled is True
    assert "运行中" in (page._step_run_primary.tooltip or "")
    assert page._stop_btn.visible is True

    page.app.running = False
    page.refresh_running()
    assert page._stop_btn.visible is False
    # 批 117：缺信息检查报告时主按钮禁用并给出补救 CTA；补报告后恢复。
    assert page._step_run_primary.disabled is True
    assert "信息检查报告" in (page._step_run_primary.tooltip or "")
    page.set_field("toc_report", __file__)
    page.refresh_running()
    assert page._step_run_primary.disabled is False
    assert page._step_run_primary.tooltip is None


def test_step_primary_visible_at_1000_width():
    page, _root = _build(P.IssuePage, width=1000)
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    assert str(page._step_run_primary.content) == "生成目录"
    assert page._step_run_primary in _of(page._content_box, ft.FilledButton)


# ── P1-8：k=1.0 时按窗口逻辑宽判断点 ─────────────────────────────────────
def test_single_breakpoint_at_1000_and_1240():
    narrow, _ = _build(P.SinglePage, width=1000)
    assert narrow._design_w() == 1000
    assert _adv_group_rows(narrow)[0] == [2, 2], "高级参数首组应 4→2 列"
    # 批 116 P1-10：1000 内容宽仍 ≥900，处理选项保持 2 列（5 张卡 → 2+2+占位）。
    assert _tile_rows(narrow) == [2, 2, 2], "1000 宽处理选项应保持 2 列"

    wide, _ = _build(P.SinglePage, width=1240)
    assert wide._design_w() == 1240
    assert _adv_group_rows(wide)[0] == [4], "1240 宽应恢复 4 列"
    # 5 张卡两两成行，末行 = 1 张卡 + 占位 Container（仍算 2 个控件）。
    assert _tile_rows(wide) == [2, 2, 2], "1240 宽应恢复 2 列网格"


def test_issue_narrow_breakpoint_uses_logical_width():
    narrow, _ = _build(P.IssuePage, width=1000)
    assert narrow._narrow() is True
    wide, _ = _build(P.IssuePage, width=1240)
    assert wide._narrow() is False
