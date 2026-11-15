"""FR-20261115：换期整块重建后，flet 的 keyed 补丁把新状态词标成 frozen。

flet 1.0.5 对列表里「显式 key 相同、对象不同」的一对按 frozen 语义比较
（`controls/object_patch.py::_effective_frozen`），随后
`_dataclass_added(..., frozen=True)` 把新子树全部 `_frozen = True`。
IssuePage 换期重建 `issue-section-*` / `issue-head-*` 若沿用固定 key，点运行时
`_sync_detail_status` 写状态词即抛 `RuntimeError("Frozen controls cannot be updated.")`。

这里不建窗口，直接用 flet 的 `ObjectPatch.from_diff` 复现真实补丁路径，
锁住「换期重建后状态词仍可更新」。
"""
from __future__ import annotations

import flet as ft
from flet.controls.object_patch import ObjectPatch

from ui import pages as P  # noqa: F401  （保证 app/ 在 sys.path）
from test_b112_post_b import _build_issue

ISSUE_A = "/tmp/fr20261115-issue-A"
ISSUE_B = "/tmp/fr20261115-issue-B"


def _switch(page, issue: str) -> None:
    page.set_field("issue_dir", issue)
    page._on_issue_changed(None)


def _diff(old: list, new: list):
    """复现 `_step_box.controls` 重建时 session 发出的 keyed 补丁。"""
    return ObjectPatch.from_diff(
        old, new, control_cls=ft.Control, parent=None, path=[], frozen=False)


def test_issue_switch_rebuild_does_not_freeze_status_text():
    page, _root = _build_issue()
    _switch(page, ISSUE_A)
    old = list(page._step_box.controls)
    old_head = page._section_heads["infocheck"]
    old_ref = page._section_refs["infocheck"]

    _switch(page, ISSUE_B)
    new = list(page._step_box.controls)
    head = page._section_heads["infocheck"]
    ref = page._section_refs["infocheck"]

    # 每次重建都要换新代次 key：同 key 不同对象正是 flet 冻结语义的触发条件。
    assert ref is not old_ref and ref.key != old_ref.key
    assert head is not old_head and head.key != old_head.key

    _diff(old, new)
    status = page._detail_statuses["infocheck"]
    assert not hasattr(status, "_frozen")
    status.value = page._status_text("infocheck")  # 旧实现此处抛 Frozen
    assert status.value == "未运行"


def test_two_way_issue_switch_keeps_status_updatable():
    page, _root = _build_issue()
    _switch(page, ISSUE_A)
    for issue in (ISSUE_B, ISSUE_A):
        old = list(page._step_box.controls)
        _switch(page, issue)
        _diff(old, list(page._step_box.controls))

    # 运行前的状态刷新（_run_step → _refresh_steps → _sync_header）应正常。
    page._sync_detail_status()
    assert page._detail_statuses["infocheck"].value == "未运行"
    assert page._detail_statuses["toc"].value == "未运行"
