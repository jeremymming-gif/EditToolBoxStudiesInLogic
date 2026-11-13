"""R7 回归（C-1 / C-2）：换期后旧正文 Open 与跨期报告/设置继承。

两条独立反例（对应 dsh ISSUES C-1/C-2、workbuddy ACCEPT §2/§3）：

C-1：提交 B 后的 350ms 延迟刷新窗口里，正文里仍挂着的旧期控件
（打开中文 tex / 打开结果 JSON / 打开目录设置）会无提示打开 A 的产物。
修复口径：正文 Open 回调带构建时的「期代次」；换期提交后旧代次一律拒绝，
新代次仍可正常打开当前期（含缺失文件诚实透传，不回退祖先）。

C-2：A→B→A 后 `toc_report`/`toc_settings` 仍是 B 的，面板重建后加载 B 的报告，
「重新生成」的 argv 与产物落点都在 B。修复口径：换期提交时丢掉不属于新期的
报告/设置引用（当期显式选择、包括显式外部报告，仍原样保留）。

另含正向守卫：当前期正确 Open、B 已有结果仍打开 B、显式外部报告在同一期内可用、
旧 issue_state schema 不被扩写。

R7 收尾 a/c（同一批）：
a. 运行中期身份一致性：键入/失焦/选择器/清空/深链恢复都必须把字段与持久
   recent 锁在本轮身份，逐键不刷屏；运行结束立即恢复可编辑与正常提交。
c. 同机制旧期正文 Open 一次补齐：信息检查 / 编译报告 / 合并 / 推送 / 前置页
   的旧卡片在 350ms 刷新窗口内一律拒绝；当前期重建后仍正常打开。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import toc_settings as TS
from ui import assembly as ASM
from ui import toc as TOC

from test_b112_post_b import _build_issue, _walk


# ── 夹具 ───────────────────────────────────────────────────────────────
def _issue(tmp_path: Path, name: str) -> tuple[Path, Path]:
    issue = tmp_path / name
    issue.mkdir()
    report = issue / f"信息检查{name}.txt"
    report.write_text("report", encoding="utf-8")
    report.with_suffix(".json").write_text(
        json.dumps({"articles": []}), encoding="utf-8")
    return issue, report


def _payload(issue: Path, *, in_toc: int = 1) -> dict:
    tmp = issue / TOC.TMP_DIR
    tmp.mkdir(exist_ok=True)
    zh_tex = tmp / "Chinese Content.tex"
    zh_tex.write_text("x", encoding="utf-8")
    pdf = tmp / "Chinese Content.pdf"
    pdf.write_bytes(b"x")
    return {
        "articles": {"inToc": in_toc},
        "placed": {"zhTex": str(zh_tex), "zhPdf": str(pdf)},
        "outputs": {}, "compiled": True,
        "pages": {"zh": 1, "en": 1},
        "column": {"enabled": False, "title": ""},
        "manualEdits": 0, "warnings": [],
    }


def _write_result(report: Path, payload: dict) -> Path:
    path = Path(TOC.ensure_result_dir(str(report)))
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _page_at(issue: Path, report: Path) -> object:
    page, _root = _build_issue()
    page.set_field("issue_dir", str(issue))
    page._on_issue_changed(None)
    page.activate("toc")
    page.set_field("toc_report", str(report))
    page.on_toc_report_changed(str(report))
    return page


def _switch(page, issue: Path) -> None:
    """真实提交路径：完整值 + blur（不经逐键 on_change）。"""
    field = page.fields["issue_dir"][0]
    field.value = str(issue)
    field.on_blur(SimpleNamespace(control=field))


def _settle(page, issue: Path) -> None:
    asyncio.run(page._refresh_after_commit_later(str(issue), 0))


def _open_buttons(control) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for node in _walk(control):
        if isinstance(node, (ft.TextButton, ft.OutlinedButton, ft.FilledButton)):
            label = node.content if isinstance(node.content, str) else ""
            if str(label).startswith("打开"):
                out.append((str(label), node.on_click))
    return out


def _toc_argv(call) -> dict:
    argv = list(call.argv)
    options: dict = {}
    for name in ("--report", "--settings", "--result-json"):
        if name in argv:
            options[name] = argv[argv.index(name) + 1]
    return options


def _states(page) -> dict:
    return page.app.store.get("issue_state", {}) or {}


# ── C-1：换期提交后旧正文 Open 一律不打开上一期 ─────────────────────────
def test_c1_stale_body_open_is_refused_between_commit_and_refresh(tmp_path):
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, _report_b = _issue(tmp_path, "issue-B")
    _write_result(report_a, _payload(issue_a))
    page = _page_at(issue_a, report_a)
    page.on_step_finished("toc", 0)

    # 旧代的全部正文 Open：tex / PDF / 结果 JSON / 目录设置。
    old_buttons = _open_buttons(page._section_bodies["toc"])
    labels = {label for label, _cb in old_buttons}
    assert {"打开中文 tex", "打开结果 JSON", "打开目录设置"} <= labels, labels

    opened: list[str] = []
    page.app.open_path = lambda value: opened.append(str(value))

    _switch(page, issue_b)          # 已提交 B，正文尚未刷新（350ms 窗口内）
    assert page._last_issue == str(issue_b)
    for _label, callback in old_buttons:
        callback(None)

    assert opened == [], f"换期后旧正文控件不得打开上一期产物：{opened}"
    assert any(event == "artifact_open_stale" for event, _ in page.app.marked)
    assert page.app.toasts, "拒绝必须可见（toast），不能静默"


def test_c1_fresh_body_after_commit_still_opens_current_issue(tmp_path):
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, report_b = _issue(tmp_path, "issue-B")
    page = _page_at(issue_a, report_a)
    _switch(page, issue_b)
    _settle(page, issue_b)

    page.set_field("toc_report", str(report_b))
    page.on_toc_report_changed(str(report_b))
    _write_result(report_b, _payload(issue_b, in_toc=2))
    page.on_step_finished("toc", 0)

    opened: list[str] = []
    page.app.open_path = lambda value: opened.append(str(value))
    buttons = _open_buttons(page._section_bodies["toc"])
    labels = {label for label, _cb in buttons}
    assert "打开中文 tex" in labels, labels
    assert "打开结果 JSON" in labels, labels
    for _label, callback in buttons:
        callback(None)

    assert opened, "当前代的正文 Open 必须仍可用"
    assert all(str(issue_b) in path for path in opened), opened


def test_c1_stale_open_never_falls_back_to_ancestor(tmp_path):
    """缺失文件仍按原路径诚实透传：不因守卫改写路径，也不回退祖先目录。"""
    issue_a, report_a = _issue(tmp_path, "issue-A")
    page = _page_at(issue_a, report_a)
    payload = _payload(issue_a)
    zh_tex = Path(payload["placed"]["zhTex"])
    zh_tex.unlink()  # 产物被移走/删除
    _write_result(report_a, payload)
    page.on_step_finished("toc", 0)

    opened: list[str] = []
    page.app.open_path = lambda value: opened.append(str(value))
    for label, callback in _open_buttons(page._section_bodies["toc"]):
        if label == "打开中文 tex":
            callback(None)
            break
    assert opened == [str(zh_tex)], "必须原样透传缺失路径，不回退到祖先目录"


def test_c1_fast_round_trip_does_not_resurrect_obsolete_controls(tmp_path):
    """A→B→A 快速往返：最终路径又回到 A，但旧代控件不得「复活」。"""
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, _report_b = _issue(tmp_path, "issue-B")
    _write_result(report_a, _payload(issue_a))
    page = _page_at(issue_a, report_a)
    page.on_step_finished("toc", 0)
    stale_buttons = _open_buttons(page._section_bodies["toc"])

    _switch(page, issue_b)
    _switch(page, issue_a)          # 同一个 A 路径，但已是新代
    assert page._last_issue == str(issue_a)

    # 过期刷新回调乱序：B 的代次不得覆盖 A 的正文。
    asyncio.run(page._refresh_after_commit_later(str(issue_b), 0))
    asyncio.run(page._refresh_after_commit_later(str(issue_a), 0))

    opened: list[str] = []
    page.app.open_path = lambda value: opened.append(str(value))
    for _label, callback in stale_buttons:
        callback(None)
    assert opened == [], f"旧代控件不得因最终期名相同而复活：{opened}"

    fresh = _open_buttons(page._section_bodies["toc"])
    assert fresh, "刷新后应重建当前期正文 Open"
    for _label, callback in fresh:
        callback(None)
    assert opened and all(str(issue_a) in path for path in opened), opened


# ── C-2：A→B→A 不沿用上一期的报告/设置/结果键 ────────────────────────────
def test_c2_round_trip_drops_previous_report_settings_and_result_key(tmp_path):
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, report_b = _issue(tmp_path, "issue-B")
    settings_a = issue_a / TS.FILE_NAME
    settings_b = issue_b / TS.FILE_NAME
    settings_a.write_text(json.dumps({"columnKind": ""}), encoding="utf-8")
    settings_b.write_text(json.dumps({"columnKind": ""}), encoding="utf-8")

    page = _page_at(issue_a, report_a)
    _switch(page, issue_b)
    _settle(page, issue_b)
    # B 本会话跑过信息检查：报告字段回填 B 的报告；用户也显式选了 B 的设置。
    page.set_field("toc_report", str(report_b))
    page.on_toc_report_changed(str(report_b))
    page.set_field("toc_settings", str(settings_b))
    page.app.store.set_recent("toc_settings", str(settings_b))

    _switch(page, issue_a)
    _settle(page, issue_a)

    # 1) 报告字段不得再指向 B（空 = 需要重选/自动取 A 最新，但绝不继承 B）。
    field_report = page.field("toc_report")
    assert not field_report or str(issue_b) not in field_report, field_report
    assert str(issue_b) not in str(page.app.store.get("recent.toc_report", "") or "")

    # 2) 面板重建后加载的是 A 的报告（或空态），不是 B。
    panel = page._toc_panel
    assert panel is not None
    assert str(issue_b) not in panel._report()
    assert str(issue_b) not in page._toc_report_path()

    # 3) 引擎 argv：报告/结果键都归属当前期。
    call = ASM.build_step_call(page, "toc")
    options = _toc_argv(call)
    assert str(issue_b) not in options["--report"], options
    assert str(issue_b) not in options["--result-json"], options
    assert options["--report"].startswith(str(issue_a)), options

    # 4) 用户在 A 页改栏目表后自动保存：只写 A 的设置，绝不动 B 的设置。
    before_b = settings_b.read_text(encoding="utf-8")
    panel._set_value("columnName", "A 期栏目")
    ASM.build_step_call(page, "toc")
    assert settings_b.read_text(encoding="utf-8") == before_b, "不得跨期改写 B 的设置"
    assert "A 期栏目" in settings_a.read_text(encoding="utf-8"), "应写回当前期设置"


def test_c2_issue_without_report_requires_reselection(tmp_path):
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_empty = tmp_path / "issue-empty"
    issue_empty.mkdir()

    page = _page_at(issue_a, report_a)
    _switch(page, issue_empty)
    _settle(page, issue_empty)

    assert page.field("toc_report") == ""
    assert page._toc_report_path() == ""
    try:
        ASM.build_step_call(page, "toc")
    except ValueError as exc:
        assert "信息检查报告" in str(exc)
    else:  # pragma: no cover - 明确失败信息
        raise AssertionError("空期不得用上一期报告派发目录生成")


def test_c2_explicit_external_report_is_still_allowed_within_issue(tmp_path):
    """现行契约：当期显式选择（即使跨目录）仍可用；只有换期继承被丢弃。"""
    issue_a, report_a = _issue(tmp_path, "issue-A")
    external = tmp_path / "shared"
    external.mkdir()
    external_report = external / "外部报告.txt"
    external_report.write_text("report", encoding="utf-8")
    page = _page_at(issue_a, report_a)

    page.set_field("toc_report", str(external_report))
    page.on_toc_report_changed(str(external_report))
    call = ASM.build_step_call(page, "toc")
    options = _toc_argv(call)
    assert options["--report"] == str(external_report), options
    assert options["--result-json"] == str(
        external / TOC.TMP_DIR / TOC.RESULT_JSON_NAME), options

    # 同值 blur（身份未变）不得清掉用户显式选择。
    _switch(page, issue_a)
    assert page.field("toc_report") == str(external_report)


def test_c2_commit_does_not_extend_old_issue_state_schema(tmp_path):
    """不靠新增 schema 字段实现修复：旧记录键集合保持不变。"""
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, _report_b = _issue(tmp_path, "issue-B")
    page, _root = _build_issue()
    page.app.store.set("issue_state", {
        str(issue_a): {"schema": 1, "last_step": "infocheck", "statuses": {}}})
    page.set_field("issue_dir", str(issue_a))
    page._on_issue_changed(None)
    page.activate("toc")
    page.set_field("toc_report", str(report_a))
    page.on_toc_report_changed(str(report_a))

    _switch(page, issue_b)
    assert set(_states(page)[str(issue_a)]) == {"schema", "last_step", "statuses"}
    assert set(_states(page)[str(issue_b)]) == {"schema", "last_step", "statuses"}


# ── R7-a：运行中期身份一致性（键入/选择器/清空/恢复）────────────────────
def _latched_page(issue: Path):
    page, _root = _build_issue()
    page.set_field("issue_dir", str(issue))
    page._on_issue_changed(None)
    return page


def _begin_run(page) -> None:
    page.app.marked.clear()
    page.app.toasts.clear()
    page.app.running = True
    page.refresh_running()


def _end_run(page) -> None:
    page.app.running = False
    page.refresh_running()


def _log_title(page) -> str:
    toolbar = page._log.root.content.controls[0]
    for control in toolbar.controls:
        if isinstance(control, ft.Text) and str(control.value).startswith("整期日志"):
            return str(control.value)
    return ""


def _type_into(page, value: str):
    field = page.fields["issue_dir"][0]
    for i in range(1, len(value) + 1):
        field.value = value[:i]
        field.on_change(SimpleNamespace(control=field))
    return field


def test_a_running_typing_keeps_field_recent_title_and_identity_on_the_issue(tmp_path):
    issue_a = tmp_path / "issue-A"
    issue_a.mkdir()
    issue_b = tmp_path / "issue-B"
    issue_b.mkdir()
    page = _latched_page(issue_a)
    _begin_run(page)

    field = _type_into(page, str(issue_b))
    field.on_blur(SimpleNamespace(control=field))

    # 运行中：输入框、持久 recent、日志标题、身份、结果归属必须同一期。
    assert field.value == str(issue_a), "运行中键入不得把字段留在另一个期"
    assert page.app.store.recent["issue_dir"] == str(issue_a)
    assert page._last_issue == str(issue_a)
    assert _log_title(page).endswith(issue_a.name)
    assert not [e for e, _ in page.app.marked if e == "issue_changed"]
    assert len(page.app.toasts) == 1, "逐键拒绝只提示一次"
    # 输入框与更换/清空入口都锁住（可见拒绝，不靠日志标签掩盖）。
    assert field.read_only is True
    assert page._issue_browse_btn.disabled is True
    assert page._issue_clear_btn.disabled is True


def test_a_running_picker_and_clear_are_rejected_without_moving_recent(tmp_path, monkeypatch):
    issue_a = tmp_path / "issue-A"
    issue_a.mkdir()
    issue_b = tmp_path / "issue-B"
    issue_b.mkdir()
    page = _latched_page(issue_a)
    _begin_run(page)
    field = page.fields["issue_dir"][0]

    async def _no_sleep(_seconds):
        return None

    # 选择器：main.browse_field 直接写 value，_watch_issue_pick 补提交。
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    field.value = str(issue_b)
    asyncio.run(page._watch_issue_pick(page.fields["issue_dir"], str(issue_a)))
    assert field.value == str(issue_a)
    assert page.app.store.recent["issue_dir"] == str(issue_a)
    assert _log_title(page).endswith(issue_a.name)

    # ✕ 清空：运行中同样保留本轮身份，不得把标题/日志改成未归属态。
    page._clear_issue()
    assert field.value == str(issue_a)
    assert page.app.store.recent["issue_dir"] == str(issue_a)
    assert page._last_issue == str(issue_a)
    assert _log_title(page).endswith(issue_a.name)
    assert len(page.app.toasts) == 1, "选择器与清空共用一次运行期提示"


def test_a_run_end_restores_editing_and_normal_commit(tmp_path):
    issue_a = tmp_path / "issue-A"
    issue_a.mkdir()
    issue_b = tmp_path / "issue-B"
    issue_b.mkdir()
    page = _latched_page(issue_a)
    _begin_run(page)
    _type_into(page, str(issue_b))
    _end_run(page)

    field = page.fields["issue_dir"][0]
    assert field.read_only is False
    assert page._issue_browse_btn.disabled is False
    assert page._issue_clear_btn.disabled is False

    # 结束后恢复正常编辑：完整输入仍可提交（不保留拒绝状态）。
    _type_into(page, str(issue_b))
    assert page.app.store.recent["issue_dir"] == str(issue_b)
    field.on_blur(SimpleNamespace(control=field))
    assert page._last_issue == str(issue_b)
    assert _log_title(page).endswith(issue_b.name)
    _settle(page, issue_b)


def test_a_prefill_deep_link_does_not_move_issue_while_running(tmp_path):
    issue_a = tmp_path / "issue-A"
    issue_a.mkdir()
    issue_b = tmp_path / "issue-B"
    issue_b.mkdir()
    page = _latched_page(issue_a)
    _begin_run(page)

    page.prefill(issue_dir=str(issue_b))

    assert page.field("issue_dir") == str(issue_a)
    assert page.app.store.recent["issue_dir"] == str(issue_a)
    assert page._last_issue == str(issue_a)


# ── R7-c：同机制旧期正文 Open（编译报告 / 合并 / 推送 / 前置页）──────────
def _dirpush_card(page, issue: Path):
    artifact = issue / "push" / "推送摘要.docx"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"docx")
    page._status["dirpush"] = ASM.StepStatus("done", artifact=str(artifact))
    page._dirpush_payload = {"ok": True}
    return page._dirpush_result(), [str(artifact), str(artifact.parent)]


def _compile_card(page, issue: Path):
    report = issue / "编译报告.md"
    report.write_text("report", encoding="utf-8")
    page._compile_rows = {"01a": {"status": "done", "pdf": ""}}
    page._compile_order = ["01a"]
    return page._compile_result(), [str(report)]


def _merge_card(page, issue: Path):
    product = issue / "合并.pdf"
    product.write_bytes(b"%PDF-1.4\n")
    page._merge_info = {"pages": 1, "path": str(product), "warnings": [],
                        "missing": 0, "names": ""}
    page._merge_run = {"state": "done", "code": 0, "artifact": str(product)}
    return page._merge_result(), [str(product), str(product.parent)]


def _prep_card(page, issue: Path):
    cover = issue / "扉页.pdf"
    cover.write_bytes(b"%PDF-1.4\n")
    page._prep_pages = {"coverPages": 1}
    return page._prep_card(), [str(cover)]


def _info_card(page, issue: Path):
    report = issue / "信息检查.txt"
    report.write_text("report", encoding="utf-8")
    page._info.show({"articles": []}, report=str(report))
    return page._info.root, [str(report)]


C_CARDS = [_dirpush_card, _compile_card, _merge_card, _prep_card, _info_card]


def _c_page_at(issue: Path, report: Path):
    """在 build 之前装好 open 记录器：信息检查卡在构建时绑定 on_open。"""
    from test_b112_post_b import _FakeApp
    from ui import pages as P

    opened: list[str] = []
    app = _FakeApp()
    app.open_path = lambda value: opened.append(str(value))
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", str(issue))
    page._on_issue_changed(None)
    page.activate("toc")
    page.set_field("toc_report", str(report))
    page.on_toc_report_changed(str(report))
    return page, opened


@pytest.mark.parametrize("builder", C_CARDS, ids=[b.__name__ for b in C_CARDS])
def test_c_old_issue_body_open_is_refused_and_current_issue_still_opens(tmp_path, builder):
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, _report_b = _issue(tmp_path, "issue-B")
    page, opened = _c_page_at(issue_a, report_a)

    old_card, _old_paths = builder(page, issue_a)
    old_buttons = _open_buttons(old_card)
    assert old_buttons, f"{builder.__name__} 应渲染可打开的入口"

    _switch(page, issue_b)          # 已提交 B，正文尚未刷新（350ms 窗口内）
    for _label, callback in old_buttons:
        callback(None)
    assert opened == [], f"换期后旧正文控件不得打开上一期产物：{opened}"
    assert any(event == "artifact_open_stale" for event, _ in page.app.marked)

    opened.clear()
    fresh_card, fresh_paths = builder(page, issue_b)
    for _label, callback in _open_buttons(fresh_card):
        callback(None)
    assert opened == fresh_paths, (opened, fresh_paths)


def test_c_result_banner_cannot_open_previous_artifact_after_switch(tmp_path):
    """结果条不靠代次守卫：提交时同步 reset 清空 detail，旧按钮只能透传空路径。"""
    issue_a, report_a = _issue(tmp_path, "issue-A")
    issue_b, _report_b = _issue(tmp_path, "issue-B")
    page = _page_at(issue_a, report_a)
    ASM._result_show(page, "完成", "ok", str(report_a))
    old_open = page._result.open_btn.on_click

    _switch(page, issue_b)

    assert page._result.root.visible is False
    assert page._result.detail.value == ""
    opened: list[str] = []
    page.app.open_path = lambda value: opened.append(str(value))
    old_open(None)
    assert str(report_a) not in opened, "旧结果条按钮不得再带出上一期产物"
