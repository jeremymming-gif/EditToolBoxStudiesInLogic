"""批 117 A：删除旧「流水线总览」，无期目录时四视图只显示选目录空态。

契约（源码静态 + 假壳运行期，不建窗口、不跑引擎）：
1. `_build_overview`/`_overview_step_row`/`_step_run_button` 与所有
   `selected ==/!= "overview"` 死分支全部删除；
2. build 初始内容不再挂总览，无期目录时落首视图 + 空态（说明 + 选目录按钮）；
3. 四视图在空期目录下都渲染同一份空态，窄窗仍带 54 高横条；
4. 点 ✕ 清空后停留当前视图，只换详情区，不跳页、不抛异常；
5. 有期目录时四视图照常构建各自 1–2 个区块；
6. `issue:overview` 别名仅落到上次步骤/默认首视图，不再有总览屏。
"""
from __future__ import annotations

import inspect

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import pages as P
from test_b112_post_b import _build_issue, _walk

EMPTY = "还没有选择期目录（一期稿件的根目录，含 000/01 作者… 等）"


def _texts(root) -> list[str]:
    return [str(ctl.value) for ctl in _walk(root) if isinstance(ctl, ft.Text)]


def test_source_has_no_legacy_overview_page():
    src = inspect.getsource(P.IssuePage)
    assert "_build_overview" not in src
    assert "_overview_step_row" not in src
    assert "_overview_stacked" not in src
    assert "_step_run_button" not in src
    assert 'selected == "overview"' not in src
    assert 'selected != "overview"' not in src
    # 别名兼容只剩 activate 的入参归一化。
    assert 'if view in ("", "overview")' in src


def test_build_without_issue_dir_shows_empty_state_not_overview():
    page, root = _build_issue()
    assert page.selected == page.STEPS[0]["key"]
    assert page._default_view() == page.STEPS[0]["key"]
    labels = _texts(root)
    assert EMPTY in labels
    assert "流水线总览" not in labels
    assert not hasattr(page, "_overview_stacked")
    assert page._step_run_btns == {}
    cta = [ctl for ctl in _walk(root) if isinstance(ctl, ft.FilledButton)
           and str(ctl.content) == "选择期目录"]
    assert len(cta) == 1


def test_all_four_views_render_same_empty_state():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1000  # 窄窗：导航折两行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(72)
    for view in page.VIEWS:
        detail = page._build_step_page(view["steps"][0])
        labels = _texts(detail)
        assert labels.count(EMPTY) == 1, f"{view['key']} 空态文案应一致"
        assert "流水线总览" not in labels
        # 批 117：每页顶部都有内嵌「期目录」行；空态不再叠 54 高横条。
        assert detail.controls[0].key == "issue-dir-row"
        assert not any(isinstance(ctl, ft.Container) and ctl.height == page.t.px(54)
                       for ctl in detail.controls)
    page.app._design_width = lambda: 1240  # 宽窗：导航单行，空态一致
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(36)
    for view in page.VIEWS:
        detail = page._build_step_page(view["steps"][0])
        assert EMPTY in _texts(detail)
        assert detail.controls[0].key == "issue-dir-row"
        assert not any(isinstance(ctl, ft.Container) and ctl.height == page.t.px(54)
                       for ctl in detail.controls)


def test_clear_issue_stays_on_current_view_with_empty_state():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    assert page.selected == "toc"
    page._clear_issue()
    assert page.selected == "toc", "点 ✕ 后必须停留当前视图"
    assert page.field("issue_dir") == ""
    labels = _texts(page._content_box)
    assert EMPTY in labels
    assert "流水线总览" not in labels
    assert page._step_run_btns == {}
    assert page._detail_primary is None


def test_after_clear_switching_views_keeps_empty_state():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("compileall")
    page._clear_issue()
    for view in page.VIEWS:
        page.activate(view["steps"][0])
        assert page.selected == view["steps"][0]
        assert EMPTY in _texts(page._content_box), f"{view['key']} 空态丢失"
        assert page._step_run_btns == {}


def test_all_four_views_build_normally_with_issue_dir():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    for view in page.VIEWS:
        page.activate(view["steps"][0])
        labels = _texts(page._content_box)
        assert EMPTY not in labels, f"{view['key']} 有期目录时不应再显示空态"
        assert "流水线总览" not in labels
        assert page._step_run_btns, f"{view['key']} 应构建区块主按钮"
        assert page._step_run_primary is not None


def test_issue_overview_alias_lands_on_last_or_default_step():
    page, _root = _build_issue()
    page.activate("overview")
    assert page.selected == page.STEPS[0]["key"], "无期目录时别名落首视图"
    assert EMPTY in _texts(page._content_box)

    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("merge")
    assert page.selected == "merge"
    page.activate("overview")
    assert page.selected == "merge", "有记忆时别名落上次步骤，不再有总览屏"
    assert page._view_of_step(page.selected) == "produce"
