"""MC-01（2026-11-13）：整期目录身份必须在编辑结束时按**完整路径**提交。

集中验收的反例（`MAC-CENTRAL-REVIEW-20261113/scripts/issue_identity_probe.py`）：
逐字输入 `/tmp/synthetic-issue-B` 时，旧实现把首字符 `/` 当成整期身份，
`_last_issue` 停在 `/`，B 的 `done`/产物没有恢复，还生成以 `/` 为键的状态记录。

本文件锁住的不变量（纯壳层，不建窗口、不跑真引擎）：
1. 逐字输入全程不提交身份、不落任何前缀键，也不重建正文；
2. blur 一次提交完整路径：旧期写回、新期状态/产物恢复、日志标题完整；
3. blur 提交不重建区块头/运行按钮对象（紧接着的点击不会被换掉的控件吞掉）；
4. 键入后不失焦、直接运行/切页：结果归属完整路径；
5. A→B→A 往返：每期状态/产物各自隔离，键集只有完整路径。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import assembly as ASM
from ui import pages as P
from test_b112_post_b import _FakeApp, _build_issue

ISSUE_A = "/tmp/mc01-issue-A"
ISSUE_B = "/tmp/mc01-issue-B"


def _entry(artifact: str, *, state: str = "done") -> dict:
    return {"schema": 1, "last_step": "infocheck",
            "statuses": {"infocheck": {"state": state, "artifact": artifact}}}


def _states(page) -> dict:
    return page.app.store.get("issue_state", {}) or {}


def _page_with_issue(issue: str, seeded: dict | None = None):
    page, _root = _build_issue()
    if seeded:
        page.app.store.set("issue_state", seeded)
    page.set_field("issue_dir", issue)
    page._on_issue_changed(None)
    return page


def _type(page, value: str):
    """逐字喂 on_change（与集中验收探针同一手法，不冒充原生键盘）。"""
    field = page.fields["issue_dir"][0]
    for i in range(1, len(value) + 1):
        field.value = value[:i]
        field.on_change(SimpleNamespace(control=field))
    return field


def _blur(page) -> None:
    field = page.fields["issue_dir"][0]
    field.on_blur(SimpleNamespace(control=field))


def _settle(page, issue: str = ISSUE_B) -> None:
    """跑掉失焦后延后的正文刷新（生产里由 page.run_task 调度）。"""
    asyncio.run(page._refresh_after_commit_later(issue, 0))


def test_typing_keeps_old_identity_and_never_writes_prefix_keys():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B-only.pdf")})
    box, body = page._step_box, page._section_bodies["infocheck"]
    status = page._detail_statuses["infocheck"]
    _type(page, ISSUE_B)
    assert page._last_issue == ISSUE_A
    assert page._state_of("infocheck") == "idle"      # A 还没运行过
    assert set(_states(page)) == {ISSUE_A, ISSUE_B}   # 没有任何前缀键
    assert page._step_box is box                      # 输入期不重建正文
    assert page._section_bodies["infocheck"] is body
    assert page._detail_statuses["infocheck"] is status


def test_blur_commits_full_path_and_restores_state_artifact():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B-only.pdf")})
    _type(page, ISSUE_B)
    _blur(page)
    assert page._last_issue == ISSUE_B
    assert page._state_of("infocheck") == "done"
    assert page._status["infocheck"].artifact == "B-only.pdf"
    assert set(_states(page)) == {ISSUE_A, ISSUE_B}
    assert page.app.store.recent["issue_dir"] == ISSUE_B
    assert any(e == "issue_changed" for e, _ in page.app.marked)
    _settle(page)
    assert page._detail_statuses["infocheck"].value == "成功"


def test_blur_commit_defers_body_refresh_to_keep_the_following_tap():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B-only.pdf")})
    box, btn = page._step_box, page._primary_btns["infocheck"]
    head = page._section_heads["infocheck"]
    calls: list = []
    page.app.page.run_task = lambda *args, **kwargs: calls.append(args)
    _type(page, ISSUE_B)
    _blur(page)
    # N6：blur 与点击是同一次按压。提交后必须先把正文刷新挂起，
    # 否则按钮在抬起前被更新/替换，Flutter 会取消这次 tap。
    assert calls and calls[0][0] == page._refresh_after_commit_later
    assert calls[0][1] == ISSUE_B
    assert page._step_box is box
    assert page._primary_btns["infocheck"] is btn
    assert page._section_heads["infocheck"] is head
    _settle(page)
    # 延后刷新仍保留同一个主按钮对象（原地刷新，不整块重建）。
    assert page._primary_btns["infocheck"] is btn
    assert page._section_heads["infocheck"] is head


def test_deferred_commit_refresh_yields_to_a_started_run():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B.pdf")})
    _type(page, ISSUE_B)
    _blur(page)
    page.app.running = True
    calls: list = []
    page._refresh_after_issue_commit = lambda: calls.append("refresh")
    asyncio.run(page._refresh_after_commit_later(ISSUE_B, 0))
    assert calls == []


def test_deferred_commit_refresh_is_dropped_after_another_commit():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B.pdf")})
    _type(page, ISSUE_B)
    _blur(page)
    page._last_issue = ISSUE_A
    calls: list = []
    page._refresh_after_issue_commit = lambda: calls.append("refresh")
    asyncio.run(page._refresh_after_commit_later(ISSUE_B, 0))
    assert calls == []


def test_run_without_blur_commits_identity_before_dispatch(monkeypatch):
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B-only.pdf")})
    _type(page, ISSUE_B)
    seen: dict = {}

    def fake_run(target, key):
        seen["issue"] = target.field("issue_dir")
        seen["last"] = target._last_issue
        seen["key"] = key

    monkeypatch.setattr(ASM, "run_step", fake_run)
    page._run_step("infocheck")
    assert seen == {"issue": ISSUE_B, "last": ISSUE_B, "key": "infocheck"}
    assert page._last_issue == ISSUE_B
    assert set(_states(page)) == {ISSUE_A, ISSUE_B}


def test_activate_without_blur_commits_typed_identity(monkeypatch):
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B-only.pdf")})
    _type(page, ISSUE_B)
    page.activate("toc")
    assert page._last_issue == ISSUE_B
    assert page._state_of("infocheck") == "done"


def test_round_trip_isolates_each_issue_state_and_artifact():
    page, _root = _build_issue()
    page.app.store.set("issue_state", {ISSUE_A: _entry("A.pdf"),
                                       ISSUE_B: _entry("B.pdf")})
    page.set_field("issue_dir", ISSUE_A)
    page._on_issue_changed(None)
    assert page._status["infocheck"].artifact == "A.pdf"

    _type(page, ISSUE_B)
    _blur(page)
    assert page._status["infocheck"].artifact == "B.pdf"

    _type(page, ISSUE_A)
    _blur(page)
    assert page._status["infocheck"].artifact == "A.pdf"
    assert page._state_of("infocheck") == "done"
    assert set(_states(page)) == {ISSUE_A, ISSUE_B}
    assert _states(page)[ISSUE_A]["statuses"]["infocheck"]["artifact"] == "A.pdf"
    assert _states(page)[ISSUE_B]["statuses"]["infocheck"]["artifact"] == "B.pdf"


def test_first_issue_from_empty_state_builds_sections_on_blur():
    page, _root = _build_issue()
    page.app.store.set("issue_state", {ISSUE_B: _entry("B-only.pdf")})
    _type(page, ISSUE_B)
    _blur(page)
    assert page._last_issue == ISSUE_B
    assert page._state_of("infocheck") == "done"
    # 空态 → 有期：正文整块建区（此时没有需要保留的运行按钮）。
    assert page._step_box.controls[0] is page._result.root
    assert "infocheck" in page._section_bodies
    assert set(_states(page)) == {ISSUE_B}


def test_blur_without_change_is_idempotent():
    page = _page_with_issue(ISSUE_A)
    btn = page._primary_btns["infocheck"]
    page.app.marked.clear()
    _blur(page)  # 值没变
    assert page._last_issue == ISSUE_A
    assert page._primary_btns["infocheck"] is btn
    assert "issue_changed" not in [e for e, _ in page.app.marked]


# ── N5 补充：结果条属于每一期，换期不得沿用上一期的产物入口 ────────────
def _run_like(page, text: str, level: str, artifact: str) -> None:
    """走与 assembly 运行收尾同一条路径：显示结果条 + 记快照 + 落库。"""
    ASM._result_show(page, text, level, artifact)
    page._refresh_steps()


def test_switch_to_unrun_issue_hides_previous_result_banner():
    page = _page_with_issue(ISSUE_A, {ISSUE_B: _entry("B.pdf")})
    _run_like(page, "完成", "ok", "/tmp/A-report.txt")
    assert page._result.root.visible
    assert page._result.open_btn.visible

    _type(page, ISSUE_B)
    _blur(page)
    # B 从未运行过：不得继续显示 A 的结果条，更不得留着 A 的「打开产物」。
    assert page._result.root.visible is False
    assert page._result.title.value == "尚未运行"
    assert page._result.detail.value == ""
    assert "result" not in _states(page)[ISSUE_B]
    # A 的结果条快照留在自己的记录里（切回来要能恢复）。
    assert _states(page)[ISSUE_A]["result"]["artifact"] == "/tmp/A-report.txt"


def test_round_trip_restores_each_issue_result_banner():
    page, _root = _build_issue()
    page.app.store.set("issue_state", {ISSUE_A: _entry("A.pdf"),
                                       ISSUE_B: _entry("B.pdf")})
    page.set_field("issue_dir", ISSUE_A)
    page._on_issue_changed(None)
    _run_like(page, "完成", "ok", "/tmp/a-report.txt")

    _type(page, ISSUE_B)
    _blur(page)
    assert page._result.root.visible is False
    _run_like(page, "完成，但有需处理的结果（退出码 1）", "warn",
              "/tmp/b-report.txt")

    _type(page, ISSUE_A)
    _blur(page)
    assert page._result.root.visible
    assert page._result.title.value == "完成"
    assert page._result.detail.value == "/tmp/a-report.txt"
    assert page._result.open_btn.visible

    _type(page, ISSUE_B)
    _blur(page)
    assert page._result.title.value == "完成，但有需处理的结果（退出码 1）"
    assert page._result.detail.value == "/tmp/b-report.txt"
    assert page._result.open_btn.visible


def test_build_restores_persisted_result_banner_for_recent_issue():
    app = _FakeApp()
    app.store.set("recent.issue_dir", ISSUE_B)
    entry = dict(_entry("B.pdf"),
                 result={"text": "完成", "level": "ok",
                         "artifact": "/tmp/b-report.txt"})
    app.store.set("issue_state", {ISSUE_B: entry})
    page = P.IssuePage(app)
    page.build()
    assert page._result.root.visible
    assert page._result.title.value == "完成"
    assert page._result.detail.value == "/tmp/b-report.txt"
    assert page._result.open_btn.visible


def test_empty_issue_then_new_issue_rebuilds_body_not_stale_sections():
    """F1：先清空再输入新期，正文必须重建，不能复用已脱离页面树的旧区块。"""
    page = _page_with_issue(ISSUE_A)
    old_head = page._section_heads["infocheck"]
    old_body = page._section_bodies["infocheck"]

    # 先把字段清空并失焦：这是与 ✕ 并行的真实路径；若 ✕ 的清除
    # 先被 blur 抢先提交，_on_issue_changed 会因值相同而幂等早退。
    page.fields["issue_dir"][0].value = ""
    _blur(page)
    page._on_issue_changed(None)
    assert page._last_issue == ""

    _type(page, ISSUE_B)
    _blur(page)
    _settle(page, ISSUE_B)

    # 正文回到有期结构，且不再引用空态前的旧区块。
    assert page._step_box.controls[0] is page._result.root
    assert page._section_heads["infocheck"] is not old_head
    assert page._section_bodies["infocheck"] is not old_body
    assert "infocheck" in page._primary_btns
