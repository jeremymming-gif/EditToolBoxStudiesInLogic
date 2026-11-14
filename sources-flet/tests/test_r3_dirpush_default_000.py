"""R3 缺陷 5（B 方案）：GUI 单期输出根默认跟随当期 000。

覆盖：
  · validate_out_root 放行「当期/000 Table of Contents」，仍拒绝当期本身、
    当期其他子目录、000 的子目录；
  · 默认路径显示随 issue 变化；年度模式仍显示旧静态默认根；
  · DirPushPanel.params() 的「将写入」文案确实是当前 issue 的 000。
"""
from __future__ import annotations

import os

import flet as ft

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import dirpush as DP

from test_b112_post_b import _build_issue, _walk


def _texts(controls) -> list[str]:
    out: list[str] = []
    for control in controls:
        for item in _walk(control):
            if isinstance(item, ft.Text):
                out.append(str(item.value))
    return out


def test_validate_allows_current_issue_000_only(tmp_path):
    issue = tmp_path / "2026年第5期(90)"
    toc = issue / "000 Table of Contents"
    toc.mkdir(parents=True)

    assert DP.validate_out_root("", str(issue))[0] == "ok"
    assert DP.validate_out_root(str(toc), str(issue))[0] == "ok"

    level, msg = DP.validate_out_root(str(issue), str(issue))
    assert level == "error" and "期目录内" in msg

    level, msg = DP.validate_out_root(str(issue / "01 王路"), str(issue))
    assert level == "error" and "期目录内" in msg

    level, msg = DP.validate_out_root(str(toc / "outsub"), str(issue))
    assert level == "error" and "期目录内" in msg

    level, msg = DP.validate_out_root(str(tmp_path / "推送"), str(issue))
    assert level == "ok", msg


def test_default_display_follows_issue_and_annual_keeps_legacy(tmp_path):
    issue_a = tmp_path / "2026年第5期(90)"
    issue_b = tmp_path / "2026年第6期(91)"

    assert EB.dirpush_default_out_root(str(issue_a)) == str(
        issue_a / "000 Table of Contents")
    assert EB.dirpush_default_out_root(str(issue_b)) == str(
        issue_b / "000 Table of Contents")

    legacy = os.path.expanduser("~/Documents/sysu/编辑部/目录摘要推送")
    assert EB.dirpush_legacy_default_out_root() == legacy
    assert DP.default_out_root(str(issue_a), annual=True) == legacy
    assert DP.default_out_root(str(issue_a), annual=False) == str(
        issue_a / "000 Table of Contents")


def test_dirpush_params_shows_selected_issue_000(tmp_path):
    issue_a = tmp_path / "2026年第5期(90)"
    issue_b = tmp_path / "2026年第6期(91)"

    page, _root = _build_issue()
    page.set_field("issue_dir", str(issue_a))
    texts = _texts(ASM.dirpush_panel(page).params())
    assert str(issue_a / "000 Table of Contents") in texts
    assert os.path.expanduser("~/Documents/sysu/编辑部/目录摘要推送") not in texts

    page.set_field("issue_dir", str(issue_b))
    texts = _texts(ASM.dirpush_panel(page).params())
    assert str(issue_b / "000 Table of Contents") in texts
    assert str(issue_a / "000 Table of Contents") not in texts