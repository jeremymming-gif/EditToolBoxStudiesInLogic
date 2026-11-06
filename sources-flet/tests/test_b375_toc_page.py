"""批 375：整期装配「生成目录」整页规则（2026-11-06 用户决定 a-e / Lead m00363）。

锁这些页级契约（离线，不建窗口、不跑引擎）：

1. 共享门：启用专栏/专辑四项必填——header 主按钮、重新生成、仅 TeX、整期串行
   都用同一判断；禁用时**不派发**、不弹「重新生成目录？」、不调用引擎、不写产物。
2. 无栏目：四项字段不阻断生成；旧勾选清单隐藏（旧选择不生效）。
3. OBS-2：toc 步结束（含整期串行、当前看的是别的步骤）后步骤结果区立即刷新：
   成功显示本轮产物/按钮，失败/cancel 只显示本轮结论，旧结果只能标「上次结果（非本轮）」；
   换报告不跨用上一份结果。
4. OBS-UI-2：跑完单步「生成目录」自动收起日志 dock（日志内容保留），
   TOC 参数区控件不被移除；整期串行中间步保持展开。
"""
from __future__ import annotations

import json
from pathlib import Path

import flet as ft
import pytest

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import engine_bridge as EB
from services import toc_settings as TS
from ui import assembly as ASM
from ui import toc as TOC

from test_b112_post_b import _build_issue, _walk
from test_b121_toc_settings import _texts, _write_settings

ISSUE_NAME = "2030年第2期(98) 合成一期"
COLUMN = {
    "columnKind": "专栏",
    "columnName": "逻辑与认知",
    "columnNameEN": "Logic and Cognition",
    "columnHost": "编辑部",
}
COLUMN_FIELDS = (("columnName", "栏目名称"), ("columnNameEN", "栏目英文名"),
                 ("columnHost", "主持人"))


# ── 页面桩：真实 IssuePage + 真实结果 JSON（离线）───────────────────────────
def _issue_dir(tmp_path: Path, name: str = ISSUE_NAME) -> tuple[Path, Path]:
    issue = tmp_path / name
    issue.mkdir()
    report = issue / f"信息检查{name}.txt"
    report.write_text("report", encoding="utf-8")
    # 与报告同 stem 的 JSON：让篇目同步载入，避免测试里起异步任务。
    (report.with_suffix(".json")).write_text(
        json.dumps({"articles": [
            {"folderName": "Jingru_Kou", "folder": "01 Jingru_Kou",
             "chineseTitle": "可知性悖论", "englishTitle": "Knowability"},
            {"folderName": "Yanjun_Li", "folder": "02 Yanjun_Li",
             "chineseTitle": "专栏二篇", "englishTitle": "Column Two"},
        ]}, ensure_ascii=False),
        encoding="utf-8")
    return issue, report


def _build_page(tmp_path: Path, *, settings: dict | None = None):
    issue, report = _issue_dir(tmp_path)
    if settings is not None:
        _write_settings(issue / TS.FILE_NAME, **settings)
    page, _root = _build_issue()
    page.set_field("issue_dir", str(issue))
    # 报告 picker 重建时取 store 的 recent 值（base_page.add_picker）。测试用的
    # _Store.get 只走 data 字典，所以这里直接塞 recent.*，让 activate 后的
    # picker、面板与页级 gate 看到同一份报告。
    page.app.store.data.setdefault("recent", {}).update(
        {"issue_dir": str(issue), "toc_report": str(report)})
    page.activate("toc")
    return page, issue, report


def _result_payload(issue: Path, *, compiled: bool = True, text_only: bool = False,
                    in_toc: int = 3) -> dict:
    tmp = Path(issue) / TOC.TMP_DIR
    tmp.mkdir(exist_ok=True)
    pdf = tmp / "Chinese Content.pdf"
    zh_tex = tmp / "Chinese Content.tex"
    en_tex = tmp / "English Content.tex"
    for path in (pdf, zh_tex, en_tex):
        path.write_bytes(b"x")
    payload = {
        "articles": {"total": 6, "inToc": in_toc},
        "templateSource": "内置", "templateNote": "",
        "tocDir": str(tmp),
        "placed": {"zhTex": str(zh_tex), "zhPdf": str(pdf),
                   "enTex": str(en_tex), "enPdf": ""},
        "outputs": {},
        "compiled": compiled,
        "pages": {"zh": 10, "en": 8},
        "column": {"enabled": 1, "title": "逻辑与认知"},
        "manualEdits": 0,
        "warnings": [],
    }
    if text_only:
        payload.update({"placed": {}, "outputs": {"zhTex": str(zh_tex)},
                        "compiled": False})
    return payload


def _write_result(report: Path, payload: dict) -> Path:
    path = Path(TOC.ensure_result_dir(str(report)))
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _button_labels(control) -> list[str]:
    labels: list[str] = []
    for node in _walk(control):
        if isinstance(node, (ft.TextButton, ft.FilledButton, ft.OutlinedButton)):
            content = getattr(node, "content", None)
            if isinstance(content, str):
                labels.append(content)
    return labels


def _all_texts(control) -> list[str]:
    """递归收集文本（含按钮/标签的字符串 content，_texts 不覆盖的那种）。"""
    out: list[str] = []
    stack = [control]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if isinstance(node, str):
            out.append(node)
            continue
        for attr in ("text", "value"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
        content = getattr(node, "content", None)
        if content is not None:
            stack.append(content)
        stack.extend(getattr(node, "controls", None) or [])
    return out


def _labels(control) -> set[str]:
    """控件树里的表单标签（form_field / select_box 的 _form_label 或 label）。"""
    out: set[str] = set()
    for node in _walk(control):
        label = getattr(node, "_form_label", None) or getattr(node, "label", None)
        if isinstance(label, str) and label:
            out.add(label)
    return out


def _marked(app, event: str) -> list[dict]:
    return [fields for name, fields in app.marked if name == event]


def _toc_primary(page):
    button = page._primary_btns.get("toc")
    return button if button is not None else page._detail_primary


# ── 1. 共享门：四项必填、纯空格无效、补齐后恢复 ────────────────────────────
def test_shared_gate_blocks_each_missing_column_field_then_recovers(tmp_path):
    page, _issue, _report = _build_page(tmp_path, settings=COLUMN)
    panel = ASM.toc_panel(page)
    assert page._primary_disabled("toc") is False, "四项齐全应可生成"
    assert ASM.toc_column_gate(page) == (False, "")

    for field, label in COLUMN_FIELDS:
        panel.values[field] = "   "  # 纯空格按空处理（用户决定 b/c）
        page.on_toc_state_changed()
        assert page._primary_disabled("toc") is True
        assert label in page._primary_reason("toc")
        assert _toc_primary(page).disabled is True
        blocked, hint = ASM.toc_column_gate(page)
        assert blocked is True and hint == panel.column_issue()[1], "单一判断源"
        panel.values[field] = COLUMN[field]
        page.on_toc_state_changed()
        assert page._primary_disabled("toc") is False

    panel.values["columnKind"] = "栏目甲"  # 非法类型也阻断
    assert page._primary_disabled("toc") is True
    assert "无法识别" in page._primary_reason("toc")
    panel.values["columnKind"] = ""  # 无栏目：字段不阻断（用户决定 a）
    assert page._primary_disabled("toc") is False


def test_shared_gate_blocks_settings_read_error(tmp_path):
    issue, report = _issue_dir(tmp_path)
    (issue / TS.FILE_NAME).write_text("{ not json", encoding="utf-8")
    page, _root = _build_issue()
    page.set_field("issue_dir", str(issue))
    page.set_field("toc_report", str(report))
    page.activate("toc")

    assert page._primary_disabled("toc") is True
    assert "读取失败" in page._primary_reason("toc")
    with pytest.raises(ValueError, match="读取失败"):
        ASM.build_step_call(page, "toc")


# ── 2. 禁用必须真正阻止派发（不是视觉）────────────────────────────────────
def test_blocked_primary_action_does_not_dispatch(tmp_path, monkeypatch):
    page, _issue, _report = _build_page(tmp_path, settings={**COLUMN, "columnHost": " "})
    from ui import pages as P
    dispatched: list = []
    confirms: list = []
    monkeypatch.setattr(ASM, "run_step", lambda *a, **k: dispatched.append(a))
    monkeypatch.setattr(P.C, "confirm", lambda *a, **k: confirms.append(a))

    page._primary_action("toc")

    assert dispatched == [], "禁用时不得派发运行"
    assert confirms == [], "禁用时不得弹「重新生成目录？」确认框"
    assert page.app.toasts and "主持人" in page.app.toasts[-1]
    assert _marked(page.app, "toc_column_invalid"), "要留明确标记"


def test_run_step_and_run_all_block_without_engine_call(tmp_path, monkeypatch):
    page, issue, _report = _build_page(tmp_path, settings={**COLUMN, "columnNameEN": "  "})
    engine_calls: list = []
    tasks: list = []
    monkeypatch.setattr(EB, "toc_call", lambda **kwargs: engine_calls.append(kwargs))
    monkeypatch.setattr(page.page, "run_task", lambda *a, **k: tasks.append(a))

    ASM.run_step(page, "toc")
    assert engine_calls == [] and tasks == [], "单步入口必须被共享门挡住"
    assert any("栏目英文名" in message for message in page.app.toasts)
    assert _marked(page.app, "validation_error")

    ASM.run_all(page, ["infocheck", "toc"])
    assert tasks == [], "整期串行含 toc 且栏目无效时派发前就阻断"
    assert not (issue / TOC.TMP_DIR).exists(), "阻断时不得创建结果目录/写产物"


def test_text_only_path_uses_same_shared_gate(tmp_path, monkeypatch):
    """决定(e)：仅 TeX 入口与主按钮/重新生成共用同一判断，禁用时同样不派发。"""
    page, _issue, _report = _build_page(tmp_path, settings={**COLUMN, "columnName": " "})
    page.app.store.data["options"] = {"toc_text_only": True}
    engine_calls: list = []
    tasks: list = []
    monkeypatch.setattr(EB, "toc_call", lambda **kwargs: engine_calls.append(kwargs))
    monkeypatch.setattr(page.page, "run_task", lambda *a, **k: tasks.append(a))

    ASM.run_step(page, "toc")

    assert tasks == [] and engine_calls == [], "仅 TeX 也不得绕过共享门"
    assert any("栏目名称" in message for message in page.app.toasts)


def test_no_column_generation_allowed_and_serial_run_dispatches(tmp_path, monkeypatch):
    page, _issue, report = _build_page(tmp_path)  # 默认设置 = 无栏目
    assert page._primary_disabled("toc") is False
    call = ASM.build_step_call(page, "toc")
    assert call is not None, "无栏目时允许构建生成调用"
    assert not (Path(report).parent / TOC.TMP_DIR / TOC.RESULT_JSON_NAME).exists(), \
        "构建调用不写产物"

    tasks: list = []
    monkeypatch.setattr(page.page, "run_task", lambda *a, **k: tasks.append(a))
    ASM.run_all(page, ["toc"])
    assert len(tasks) == 1, "无栏目串行应正常派发"


# ── 3. OBS-2：结果区在 toc 步结束后立即刷新（含 selected != toc）──────────
def test_toc_success_refreshes_result_zone_from_other_step(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue, in_toc=3))
    page.selected = "compileall"  # 模拟整期串行：当前看的是别的步骤

    page.on_step_finished("toc", 0)

    box = page._toc_box
    assert box is not None
    texts = _texts(box)
    assert any("输出：" in text for text in texts), texts
    assert any("篇目：3 篇" in text for text in texts), texts
    assert any("已编译 / 已写入" in text for text in texts), texts
    labels = _button_labels(box)
    assert "打开 PDF" in labels and "打开结果 JSON" in labels, labels
    assert page._toc_payload_report == str(report)
    assert page._toc_run_failed is False and page._toc_run_code == 0
    # 同一容器原地重建（OBS-2 根因：页面构建期的控件从不更新）
    assert page._toc_result_box() is box


def test_toc_failure_does_not_show_old_artifact_as_this_run(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue))
    page.on_step_finished("toc", 0)  # 先有一份旧成功结果
    page.on_step_finished("toc", 2)  # 本轮失败

    box = page._toc_box
    texts = _texts(box)
    assert any("本次生成未成功（退出码 2）" in text for text in texts), texts
    assert any("上次结果（非本轮" in text for text in texts), texts
    assert _button_labels(box) == [], "失败时不得把旧产物当本轮结果提供打开按钮"
    assert page._toc_run_failed is True and page._toc_run_code == 2

    # 下一轮成功后失败标记消失，显示本轮新结果
    _write_result(report, _result_payload(issue, in_toc=5))
    page.on_step_finished("toc", 0)
    texts = _texts(page._toc_box)
    assert not any("本次生成未成功" in text for text in texts), texts
    assert any("篇目：5 篇" in text for text in texts), texts
    assert "打开 PDF" in _button_labels(page._toc_box)


def test_toc_cancel_marks_result_zone_and_keeps_params(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue))
    page.on_step_finished("toc", 0)
    page.on_step_cancelled("toc")

    box = page._toc_box
    texts = _texts(box)
    assert any("本次已停止，未产生新结果" in text for text in texts), texts
    assert _button_labels(box) == [], "取消不得把旧产物当本轮结果"
    assert page._toc_run_cancelled is True and page._toc_run_failed is False


def test_report_change_clears_stale_result_and_run_flags(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue))
    page.on_step_finished("toc", 2)
    assert page._toc_run_failed is True

    other_issue = tmp_path / "另一期"
    other_issue.mkdir()
    other_report = other_issue / "信息检查另一期.txt"
    other_report.write_text("report", encoding="utf-8")
    page.set_field("toc_report", str(other_report))
    page._sync_toc_payload()

    texts = _texts(page._toc_box)
    assert any("还没有生成目录" in text for text in texts), texts
    assert not any("上次结果" in text for text in texts), "换报告不跨用上一份结果"
    assert page._toc_payload == {}
    assert page._toc_run_failed is False and page._toc_run_cancelled is False
    assert page._toc_run_code is None


def test_text_only_result_never_offers_old_pdf(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue, compiled=False, text_only=True))

    page.on_step_finished("toc", 0)

    texts = _texts(page._toc_box)
    labels = _button_labels(page._toc_box)
    assert any("仅 tex · 未写入、未编译" in text for text in texts), texts
    assert "打开中文 tex" in labels
    assert "打开 PDF" not in labels, "仅 tex 不得把旧 PDF 当本轮产物"
    assert "打开英文 PDF" not in labels, "仅 tex 也不得提供英文 PDF 入口"
    assert page.app.store.get("options.toc_text_only", False) is False


def test_toc_result_offers_open_english_pdf_when_present(tmp_path):
    """独立验收 task-6(2)：enPdf 存在时结果区提供「打开英文 PDF」；没有就不显示。

    仅 tex/失败/取消状态不会走到成功分支（placed 为空或前面提前 return），
    故英文 PDF 按钮只会对成功的本轮结果出现。
    """
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    payload = _result_payload(issue)  # 先建 .toolbox-tmp/，再放英文 PDF
    en_pdf = Path(issue) / TOC.TMP_DIR / "English Content.pdf"
    en_pdf.write_bytes(b"x")
    payload["placed"]["enPdf"] = str(en_pdf)
    _write_result(report, payload)

    page.on_step_finished("toc", 0)

    labels = _button_labels(page._toc_box)
    assert "打开 PDF" in labels, labels
    assert "打开英文 PDF" in labels, labels

    # 没有 enPdf 时（旧结果/只落了中文）：不显示英文 PDF 入口。
    payload_without_en = _result_payload(issue)
    _write_result(report, payload_without_en)
    page.on_step_finished("toc", 0)
    labels = _button_labels(page._toc_box)
    assert "打开 PDF" in labels, labels
    assert "打开英文 PDF" not in labels, labels


def test_toc_result_lines_fix_frozen_smoke_cosmetics(tmp_path):
    """frozen #1 冒烟：(a) 仅 tex 结论只印一行；(b) 无栏目读作「栏目：无」。"""
    page, _issue, _report = _build_page(tmp_path, settings=COLUMN)
    no_column = {"articles": {"inToc": 3}, "column": {"enabled": False, "title": ""},
                 "placed": {}, "outputs": {"zhTex": "/tmp/x.tex"}, "compiled": False}

    lines = page._toc_summary_lines(no_column)

    assert lines.count("栏目：无") == 1, lines
    assert sum("仅 tex · 未写入、未编译" in line for line in lines) == 1, lines
    assert not any("False" in line or "（）" in line for line in lines), lines

    enabled = {"articles": {"inToc": 3},
               "column": {"enabled": True, "title": "逻辑与认知"},
               "placed": {"zhPdf": "/tmp/x.pdf", "zhTex": "/tmp/x.tex"},
               "outputs": {}, "compiled": True}
    lines = page._toc_summary_lines(enabled)
    assert "栏目：逻辑与认知" in lines, lines
    assert "已编译 / 已写入" in lines, lines


# ── 4. OBS-UI-2：跑完收起日志 dock，参数区保留 ─────────────────────────────
def test_toc_finish_collapses_log_dock_except_intermediate_serial_step(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue))

    page._expand_log()
    assert page._log_expanded is True
    page._toc_run_last = False  # 整期串行中间步：后面还有步骤要看日志
    page.on_step_finished("toc", 0)
    assert page._log_expanded is True, "串行中间步不得收起日志"

    page._toc_run_last = True
    page._expand_log()
    page.on_step_finished("toc", 0)
    assert page._log_expanded is False, "单步跑完收起日志，参数区回到视野"

    page._expand_log()
    page.on_step_finished("toc", 2)
    assert page._log_expanded is False
    page._expand_log()
    page.on_step_cancelled("toc")
    assert page._log_expanded is False


def test_toc_params_stay_complete_after_run(tmp_path):
    page, issue, report = _build_page(tmp_path, settings=COLUMN)
    _write_result(report, _result_payload(issue))

    def snapshot():
        return (set(_all_texts(page._content_box)),
                set(_button_labels(page._content_box)) | _labels(page._content_box))

    texts_before, labels_before = snapshot()
    page.on_step_finished("toc", 2)
    texts_after, labels_after = snapshot()

    expected_texts = {"仅生成 tex（不写入、不编译）"}
    expected_labels = {"读取栏目表", "保存栏目表", "打开目录设置", "打开结果 JSON",
                       "栏目类型", "栏目名称（中文）", "栏目英文名", "主持人"}
    assert expected_texts <= texts_before and expected_texts <= texts_after, \
        f"运行后参数控件丢失：{expected_texts - texts_after}"
    assert expected_labels <= labels_before and expected_labels <= labels_after, \
        f"运行后参数控件丢失：{expected_labels - labels_after}"


# ── 5. after_step 接线：串行最后一步收起日志、取消走专用钩子 ────────────────
def test_after_step_passes_last_flag_and_cancel_hook():
    class _StepPage:
        def __init__(self) -> None:
            self.events: list = []
            self._toc_run_last = None

        def field(self, _key):
            return ""

        def on_step_finished(self, key, code=0):
            self.events.append(("finished", key, code))

        def on_step_cancelled(self, key):
            self.events.append(("cancelled", key))

        def mark(self, *args, **kwargs):
            pass

    page = _StepPage()
    ASM.after_step(page, "toc", object(), 0, False, "", False)
    assert ("finished", "toc", 0) in page.events
    assert page._toc_run_last is False, "中间步 last 标记要传给页面"

    ASM.after_step(page, "toc", object(), 0, False, "", True)
    assert page._toc_run_last is True

    page.events.clear()
    ASM.after_step(page, "toc", object(), -1, True, "")
    assert ("cancelled", "toc") in page.events
    assert not any(event[0] == "finished" for event in page.events), "取消不得走完成钩子"
