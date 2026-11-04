"""批 117 P2：整期装配改成页内 4 功能子页 + P0/P1 实测缺陷回归。

锁住的不变量（纯壳层，不建窗口、不跑真引擎）：
1. 216 左栏 / 54 横条 / 「正文流水线」全部删除，换成 4 个页内 28 高 chip；
2. 每个功能页顶部都内嵌同一个「期目录」字段（48 浮动字段 + 112「更换…」+ ✕）；
3. 无期目录时四页共用同一空态与「选择期目录」CTA；
4. ⌘1–4 切功能页，⌘5/6 进清理/推送页并展开唯一区块；
5. 目录页缺信息检查报告：主按钮禁用 + 页内「先生成检查报告」CTA（单步直跑）；
6. 换期目录：旧期状态写回 issue_state，新期清空运行态/结果/日志并留在当前页；
7. 合并 0 页不再显示成功（warn + 可执行提示）；
8. 清理预演失败不再拦死「开始清理」，失败原因内联末 3 行；
9. 仅生成 tex：toc 生成任务按 assembly 开关透传 text_only，结果区标注未落位；
10. 推送前置预检：000 期头卷/期仍是「?」时禁用主按钮并说明；
11. 22 路由与 issue:<step> 6 条 alias 保持不变。
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import assembly as ASM
from ui import dirpush as D
from ui import ia_map as IA
from ui import pages as P
from ui import toc as TOC
from test_b112_post_b import _build_issue, _walk


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _walk(control) if isinstance(ctl, ft.Text)]


def _issue_page(issue: str = "/tmp/期目录"):
    page, _root = _build_issue()
    page.set_field("issue_dir", issue)
    return page


# ── 1. 旧 rail / 横条 / 流水线文案彻底删除 ─────────────────────────────
def test_no_rail_progress_band_or_pipeline_copy_left():
    src = inspect.getsource(P.IssuePage)
    for banned in ("正文流水线", "流水线栏", "流水线", "_progress_band",
                   "_rail_row", "_rail_group", "_rail(", "216"):
        assert banned not in src, f"整期页仍残留旧布局标记：{banned}"
    assert not hasattr(P.IssuePage, "_build_overview")
    assert "GROUPS" not in src, "01–04/05–06 分组常量已删除"
    # 日志 dock 的 54 是展开避让高度（保留），与旧 54 横条无关。
    assert "_sync_log_reserve" in src


# ── 2. 四个 chip：存在 / 28 高 / 点击切换功能页 ────────────────────────
def test_four_nav_chips_exist_and_switch_views():
    page = _issue_page()
    assert set(page._nav_chips) == {"catalog", "produce", "clean", "push"}
    assert {chip.height for chip in page._nav_chips.values()} == {page.t.px(28)}
    labels = [chip.content.controls[0].value for chip in page._nav_chips.values()]
    assert labels == ["生成目录", "编译并合并", "清理临时文件", "生成推送文案"]
    page._nav_chips["produce"].on_click(SimpleNamespace())
    assert page._view_of_step(page.selected) == "produce"
    assert page.expanded_step("produce") in ("compileall", "merge")


# ── 3. 四页顶部都有内嵌「期目录」行（同一值）──────────────────────────
def test_every_function_page_has_embedded_issue_row():
    page = _issue_page("/tmp/期目录-A")
    for view in page.VIEWS:
        detail = page._build_step_page(view["steps"][0])
        first = detail.controls[0]
        assert isinstance(first, ft.Container) and first.key == "issue-dir-row"
        fields = [ctl for ctl in _walk(first) if isinstance(ctl, ft.TextField)]
        assert fields and fields[0].value == "/tmp/期目录-A", \
            f"{view['key']} 页未显示同一条期目录"
        browse = [ctl for ctl in _walk(first)
                  if isinstance(ctl, ft.OutlinedButton)
                  and str(ctl.content) == "更换…"]
        assert browse and browse[0].width == page.t.px(112), "更换按钮 112 宽"
        assert any(str(ctl.content) == "✕" for ctl in _walk(first)
                   if isinstance(ctl, ft.TextButton))


# ── 4. 无期目录：四页同一空态 + 选目录 CTA ─────────────────────────────
def test_empty_state_is_shared_across_four_pages():
    page, _root = _build_issue()
    assert page.field("issue_dir") == ""
    sentence = "还没有选择期目录"
    for view in page.VIEWS:
        detail = page._build_step_page(view["steps"][0])
        labels = _texts(detail)
        assert labels.count(sentence) == 1, f"{view['key']} 空态文案不一致"
        cta = [ctl for ctl in _walk(detail) if isinstance(ctl, ft.FilledButton)
               and str(ctl.content) == "选择期目录"]
        assert len(cta) == 1, "每页空态都要有同一个 CTA"


# ── 5. ⌘1–4 功能页；⌘5/6 清理/推送页并展开该区块 ─────────────────────
def test_keyboard_maps_four_views_and_five_six():
    page = _issue_page()
    for index, view in enumerate(page.VIEWS, start=1):
        page._on_key(SimpleNamespace(key=str(index), ctrl=True, meta=False))
        assert page._view_of_step(page.selected) == view["key"]
    page._on_key(SimpleNamespace(key="5", ctrl=True, meta=False))
    assert page.selected == "cleantemp" and page.expanded_step("clean") == "cleantemp"
    page._on_key(SimpleNamespace(key="6", ctrl=True, meta=False))
    assert page.selected == "dirpush" and page.expanded_step("push") == "dirpush"


# ── 6. 目录页缺报告：主按钮禁用 + 页内 CTA 单步直跑 infocheck ─────────
def test_toc_primary_needs_report_and_cta_runs_infocheck():
    page = _issue_page()
    page.activate("toc")
    assert page._step_run_primary.disabled is True
    assert "信息检查报告" in (page._step_run_primary.tooltip or "")
    cta = [ctl for ctl in _walk(page._content_box) if isinstance(ctl, ft.OutlinedButton)
           and str(ctl.content) == "先生成检查报告"]
    assert len(cta) == 1, "目录区块内要有「先生成检查报告」CTA"
    assert page._toc_report_cta() is not None
    seen: list[str] = []
    page._run_step = lambda key: seen.append(key)
    page._run_infocheck_only()
    assert seen == ["infocheck"], "CTA 不经整期，直接跑检查 tex 信息单步"


# ── 7. 换期：旧期落库、新期全清、停留当前页、日志清空换标题 ──────────
def test_issue_change_resets_state_and_keeps_current_page(tmp_path):
    old = tmp_path / "2026年第5期(90)-A"
    new = tmp_path / "2026年第6期(91)-B"
    old.mkdir()
    new.mkdir()
    page, _root = _build_issue()
    page.set_field("issue_dir", str(old))
    page._on_issue_changed(None)
    page.activate("toc")
    page._status["compileall"] = ASM.StepStatus("done", exit_code=0,
                                                duration_ms=21000)
    page._merge_info = {"pages": 47, "path": "/tmp/old.pdf"}
    page._compile_rows = {"x": "ok"}
    page._toc_payload = {"ok": True}
    page._ct_preview = {"ok": True, "count": 3}
    page._log.add_line("上一期的日志")

    page.set_field("issue_dir", str(new))
    page._on_issue_changed(None)

    saved = page.app.store.get("issue_state", {}).get(str(old), {})
    assert saved.get("schema") == 1
    assert saved.get("statuses", {}).get("compileall", {}).get("state") == "done"
    assert page._state_of("compileall") == "idle"
    assert page._merge_info == {} and page._compile_rows == {}
    assert page._toc_payload == {} and page._ct_preview == {}
    assert page.selected == "toc", "换期必须停留在当前功能页"
    assert page._view_of_step(page.selected) == "catalog"
    assert page._log._lines == [], "换期清日志内容"
    assert "整期日志 · 2026年第6期(91)-B" in _texts(page._log.root), \
        "日志标题要跟到新期路径"


# ── 8. 清空期目录：留在当前页并显示统一空态 ────────────────────────────
def test_clear_issue_shows_empty_state_without_leaving_page():
    page = _issue_page()
    page.activate("compileall")
    page._clear_issue()
    assert page.selected == "compileall", "清空目录不跳走"
    assert page._view_of_step(page.selected) == "produce"
    assert "还没有选择期目录" in \
        _texts(page._content_box)


# ── 9. 合并 0 页：warn + 可执行提示，绝不显示成功 ─────────────────────
def test_merge_zero_pages_is_warning_not_success():
    page = _issue_page()
    page.activate("merge")
    page._merge_info = {"pages": 0, "path": "/tmp/out.pdf"}
    labels = _texts(page._merge_result())
    assert not any("✅" in value for value in labels)
    assert any("没有可合并的 PDF" in value for value in labels)
    page._apply_status("merge", 0, False, 1.2, "/tmp/out.pdf")
    assert page._state_of("merge") == "warn"
    # 正常篇数仍显示成功（防过度收紧）。
    page._merge_info = {"pages": 47, "path": "/tmp/out.pdf"}
    assert any("合并完成 · 47 页" in value for value in _texts(page._merge_result()))


# ── 10. 清理预演失败：不再拦死清理；失败原因内联末 3 行 ────────────────
def test_cleantemp_failure_is_inline_and_does_not_block_clean():
    page = _issue_page()
    page._ct_preview = {"ok": False, "code": 2, "error": "引擎没有返回 JSON",
                        "lines": ["x"]}
    page._status["cleantemp"] = ASM.StepStatus("error", exit_code=2)
    page._step_tail["cleantemp"] = ["第一行", "第二行", "末行原因"]
    labels = _texts(page._cleantemp_result())
    assert any("引擎没有返回 JSON" in value for value in labels)
    assert "末 3 行：" in labels and "末行原因" in labels
    assert any(isinstance(ctl, ft.TextButton)
               and str(ctl.content) == "展开日志看全文"
               for ctl in _walk(page._cleantemp_result()))

    page.app.store.set("options.cleantemp_dry_run", False)
    opened: list[bool] = []
    page._open_clean_confirm = lambda: opened.append(True)
    page._cleantemp_primary()
    assert opened == [True], "预演失败/未预演也能打开危险确认框开始清理"


# ── 11. 仅生成 tex：生成任务透传 assembly 开关，并提示未落位 ───────────
def test_toc_text_only_is_passed_and_annotated(tmp_path, monkeypatch):
    report = tmp_path / "信息检查.txt"
    report.write_text("report", encoding="utf-8")
    page = _issue_page(str(tmp_path))
    page.set_field("toc_report", str(report))
    page.app.notes_repo = None  # 假壳：_generate_task 会把它传给 read_json_command
    page.app.store.set("options.toc_text_only", True)
    import ui.toc as TOCMOD
    captured: dict = {}
    real_call = TOCMOD.EB.toc_call

    def fake_call(**kwargs):
        captured.update(kwargs)
        return real_call(**kwargs)

    monkeypatch.setattr(TOCMOD.EB, "toc_call", fake_call)
    payload = {"ok": True, "placed": {}, "outputs": {}, "warnings": []}
    monkeypatch.setattr(
        TOCMOD.EB, "read_json_command",
        lambda call, **kwargs: {"ok": True, "code": 0, "json": payload,
                                "stderr": "", "lines": [], "error": ""})
    panel = TOC.TocPanel(page)
    asyncio.run(panel._generate_task())
    assert captured.get("text_only") is True, "toc.py 生成任务必须透传 text_only"
    assert any("仅生成 tex，未写入 000" in value
               for value in _texts(panel.feedback))


# ── 12. 目录 tex 产物：P1 JSON outputs 优先，glob 兜底要标「推断」 ─────
def test_toc_outputs_prefer_json_and_mark_glob_inferred(tmp_path):
    tex = tmp_path / "zh.tex"
    tex.write_text("x", encoding="utf-8")
    payload = {"outputs": {"zhTex": str(tex), "enTex": ""}}
    rows = TOC.result_outputs(payload, str(tmp_path))
    assert rows == [("中文目录 tex", str(tex), False)], "JSON outputs 不应标推断"

    issue = tmp_path / "期"
    (issue / "000").mkdir(parents=True)
    (issue / "000" / "Chinese Content_01.tex").write_text("x", encoding="utf-8")
    rows = TOC.result_outputs({}, str(issue))
    assert rows and rows[0][0] == "中文目录 tex" and rows[0][2] is True, \
        "旧结果无 outputs 时允许 glob，但必须标「推断」"


# ── 13. 推送前置预检：000 期头卷/期可解析性 ──────────────────────────
def test_dirpush_precheck_blocks_unparseable_issue_header(tmp_path):
    issue = tmp_path / "2026年第5期(90)"
    (issue / "000").mkdir(parents=True)
    (issue / "000" / "目录.tex").write_text("第 ? 卷，第 ? 期，总第 90 期",
                                            encoding="utf-8")
    assert "期头" in D.header_parse_error(str(issue))
    page = _issue_page(str(issue))
    page.activate("dirpush")
    assert page._step_run_primary.disabled is True
    assert "期头" in (page._step_run_primary.tooltip or "")

    (issue / "000" / "目录.tex").write_text("第 5 卷，第 3 期，总第 90 期",
                                            encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""


# ── 14. 推送失败即使没有产物也要显示原因（B P2-1 埋没在日志里）────────
def test_dirpush_failure_without_artifact_shows_reason():
    page = _issue_page()
    page._status["dirpush"] = ASM.StepStatus("error", exit_code=2)
    page._step_tail["dirpush"] = ["第一行", "第二行",
                                  "期头解析失败，需要 卷/期/总期/年月日"]
    labels = _texts(page._dirpush_result())
    assert any("推送失败" in value for value in labels)
    assert any("期头解析失败" in value for value in labels)
    assert "还没有推送记录" not in labels


# ── 15. 22 路由 + 6 条 alias：落到对应功能页并展开该区块 ─────────────
def test_routes_and_issue_aliases_stay_intact():
    assert len(IA.all_routes()) == 22
    assert IA.issue_step_keys() == ["infocheck", "toc", "compileall",
                                    "merge", "cleantemp", "dirpush"]
    page = _issue_page()
    for step in IA.issue_step_keys():
        page.activate(step)
        assert page.selected == step
        view = IA.issue_view_of(step)
        assert page.expanded_step(view) == step, \
            f"issue:{step} 要落到「{view}」并展开对应区块"


# ── 15. 选择目录后必须真的调度换期监视器（未 await 的协程 = P0 不触发）──
def test_browse_schedules_issue_watch_coroutine():
    page = _issue_page()
    calls: list = []
    page.page.run_task = lambda fn, *args, **kwargs: calls.append((fn, args))
    page._browse_issue()
    assert calls and calls[0][0] == page._watch_issue_pick, \
        "「更换…」/空态 CTA 之后必须调度 _watch_issue_pick，否则换期不重置"


# ── 16. 每页默认展开第一个区块、手风琴保持单展开 ─────────────────────
def test_each_function_page_expands_first_section_only():
    page = _issue_page()
    for view in page.VIEWS:
        page.activate(view["steps"][0])
        assert page.expanded_step(view["key"]) in view["steps"]
        visible = [key for key, body in page._section_bodies.items() if body.visible]
        assert len(visible) == 1, f"{view['key']} 同时展开了多个区块"
