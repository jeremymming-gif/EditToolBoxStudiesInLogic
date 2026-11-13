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
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import flet as ft

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
