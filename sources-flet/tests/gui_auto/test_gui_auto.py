"""GUI 自动走查 pytest 入口（默认 skip；``--run-gui`` 或 ``SLCN_GUI_AUTO=1`` 才跑）。

运行示例（仓库根）：:

    py -3.14 -m pytest sources-flet/tests/gui_auto -v --run-gui

首次生成基线加 ``--gui-update-baseline``；第二次运行即做视觉 diff 回归。
"""
from __future__ import annotations

import pytest

from . import scenarios as S


def test_gui_a_component_weights(gui_ctx):
    """A 类：分区标题 / 选项名 / 侧栏选中 / 运行日志标题的字重与字号。"""
    rows = S.assert_structural()
    gui_ctx.record("A_component_weights", checks=rows)
    assert all(row["ok"] for row in rows), [row for row in rows if not row["ok"]]


def test_gui_s1_launch_smoke(gui_ctx):
    """S1：真实进程启动、窗口标题/尺寸/DPI、引擎就绪、无异常日志。"""
    info = S.s1_launch(gui_ctx)
    assert info["engine_ok"], info
    assert info["window_title"] == "编辑部工具箱", info


def test_gui_s2_six_page_navigation(gui_ctx):
    """S2：侧栏六页逐页点击 + 选中高亮 + 截图差异。"""
    result = S.s2_navigation(gui_ctx)
    assert [row["page"] for row in result["pages"]] == S.NAV_SEQUENCE


def test_gui_s3_dark_mode_switch(gui_ctx):
    """S3：深色切换前后截图、背景/侧栏取色、切回浅色一致。"""
    info = S.s3_theme(gui_ctx)
    assert info["dark_bg"] != info["light_bg"], info
    assert info["back_mean"] <= 0.2, info


def test_gui_s4_single_page_flows(gui_ctx):
    """S4：单篇稿件三步骤 chip 可见/可切换，内容确实不同。"""
    info = S.s4_single_flows(gui_ctx)
    assert [row["flow"] for row in info["flows"]] == S.FLOW_LABELS


def test_gui_s5_bib_five_views(gui_ctx):
    """S5：引用库五视图（概况/查库/入藏/待处理/设置）。"""
    info = S.s5_bib_views(gui_ctx)
    assert [row["view"] for row in info["views"]] == S.BIB_VIEWS


def test_gui_s6_run_stop_buttons(gui_ctx):
    """S6：空表单运行→校验提示；停止按钮未运行时不产生取消事件。"""
    info = S.s6_buttons(gui_ctx)
    assert "源稿件" in (info["validation_reason"] or ""), info


def test_gui_s7_native_close(gui_ctx):
    """S7：点原生标题栏 X，进程 exit 0、无残留进程。"""
    info = S.s7_close(gui_ctx)
    assert info["rc"] == 0 and info["native_close_ok"], info
