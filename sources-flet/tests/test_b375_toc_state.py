"""批 375：生成目录页面状态（OBS-A 读取草稿保护 / 四项必填 / 模式状态）。

锁两组契约（离线，不建窗口、不跑引擎）：

1. 2026-11-06 用户决定(b)(c)：启用专栏/专辑四项（类型/中文名/英文名/主持人）strip 后
   全部非空才可生成；缺项一次列全；无栏目不受字段阻断。UI 标签不再写「（可空）」。
2. 用户决定(f)：读取栏目表若会覆盖未保存草稿，必须三向确认
   （保存草稿并读取 / 放弃草稿并读取 / 取消）；保存失败保持旧状态且不推进读取；
   读失败保持旧状态、不写部分值；不为显示控件提前创建设置文件。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import toc_settings as TS
from ui import toc as TOC

from test_b121_toc_settings import _args_of, _make_panel, _texts, _write_settings

TARGET = {
    "columnKind": "专栏",
    "columnName": "目标栏目",
    "columnNameEN": "Target Column",
    "columnHost": "主持人乙",
    "columnArticles": ["Jingru_Kou"],
}
OLD = {
    "columnKind": "专栏",
    "columnName": "旧栏目",
    "columnNameEN": "Old Column",
    "columnHost": "主持人甲",
}


def _scene(tmp_path):
    """面板已载入「旧期」设置并改出脏草稿；当前报告指向 tmp_path 下的目标设置。"""
    target_path = tmp_path / TS.FILE_NAME
    _write_settings(target_path, **TARGET)
    old_dir = tmp_path / "旧期"
    old_dir.mkdir()
    old_path = old_dir / TS.FILE_NAME
    _write_settings(old_path, **OLD)

    panel, report, page = _make_panel(tmp_path)
    panel.values = TS.normalize(dict(TARGET))
    panel.selected = list(TARGET["columnArticles"])
    panel._loaded_path = str(old_path)
    panel._loaded_report = str(old_dir / "信息检查旧.txt")
    panel._dirty = True
    panel.values["columnName"] = "草稿栏目"  # 未保存草稿
    return panel, report, page, target_path, old_path


def _capture_dialog(monkeypatch):
    dialogs: list = []
    dismissed: list = []

    def _show_dialog(page, dialog):
        dialogs.append(dialog)

    def _dismiss_top_dialog(page):
        dismissed.append(page)

    monkeypatch.setattr(TOC.C, "show_dialog", _show_dialog)
    monkeypatch.setattr(TOC.C, "dismiss_top_dialog", _dismiss_top_dialog)
    return dialogs, dismissed


def _click(dialog, label: str) -> None:
    for action in dialog.actions:
        if str(getattr(action, "content", "")) == label:
            action.on_click(None)
            return
    raise AssertionError(f"对话框没有按钮 {label}："
                         f"{[getattr(a, 'content', None) for a in dialog.actions]}")


def _marks(page, event: str) -> list[dict]:
    return [fields for name, fields in page.marks if name == event]


# ── 1. 四项必填（module 级 column_issue 是唯一判断源）──────────────────────
def test_column_issue_requires_all_four_column_fields():
    complete = {"columnKind": "专栏", "columnName": "逻辑与认知",
                "columnNameEN": "Logic and Cognition", "columnHost": "编辑部"}
    assert TOC.column_issue(complete) == (False, "")

    for key, label in (("columnName", "栏目名称"), ("columnNameEN", "栏目英文名"),
                       ("columnHost", "主持人")):
        for broken in ({key: ""}, {key: "   \t"}):
            values = dict(complete, **broken)
            blocked, hint = TOC.column_issue(values)
            assert blocked is True, f"{key} 缺失/纯空格必须阻断"
            assert label in hint and "四项都填好" in hint, hint

    # 多项缺失一次列全（不是只报第一个）
    blocked, hint = TOC.column_issue({"columnKind": "专辑"})
    assert blocked is True
    assert "栏目名称" in hint and "栏目英文名" in hint and "主持人" in hint

    # 2026-11-06 用户决定(a)：本期无栏目 → 永不因字段缺失阻断。
    assert TOC.column_issue({}) == (False, "")
    assert TOC.column_issue({"columnKind": "", "columnArticles": ["遗留勾选"]}) == (False, "")
    # 已启用但类型无法识别 → 阻断
    blocked, hint = TOC.column_issue(dict(complete, columnKind="栏目甲"))
    assert blocked is True and "无法识别" in hint


def test_album_gate_needs_four_fields_but_no_checklist_state(tmp_path):
    panel, _report, _page = _make_panel(tmp_path, settings={
        "columnKind": "专辑", "columnName": "逻辑与概率",
        "columnNameEN": "Logic and Probability", "columnHost": "主持人丙"})
    assert panel.column_issue() == (False, "")
    panel.values["columnHost"] = "   "
    blocked, hint = panel.column_issue()
    assert blocked is True and "主持人" in hint  # 专辑与专栏同一套四项必填
    panel.values["columnKind"] = ""  # 无栏目允许生成
    assert panel.column_issue() == (False, "")


# ── 2. 标签去掉「（可空）」（2026-11-06 用户决定(b)(c)：四项语义必填）───────
def test_editor_labels_have_no_optional_suffix(tmp_path):
    panel, _report, _page = _make_panel(tmp_path, settings={
        "columnKind": "专栏", "columnName": "逻辑与认知",
        "columnNameEN": "Logic and Cognition", "columnHost": "编辑部"})
    controls = panel._editor_controls()
    grid = controls[0]
    labels = [getattr(cell.content, "_form_label", None) for cell in grid.controls]
    assert labels == ["栏目类型", "栏目名称（中文）", "栏目英文名", "主持人"]
    texts = _texts(ft.Column(controls=controls))
    assert not any("（可空）" in text for text in texts), texts


def test_editor_renders_visible_missing_field_hint(tmp_path):
    """frozen #1 冒烟：缺项不能只在按钮 tooltip 里，编辑区要有可见提示。"""
    panel, _report, _page = _make_panel(tmp_path, settings={
        "columnKind": "专栏", "columnName": "逻辑与认知",
        "columnNameEN": "", "columnHost": ""})

    texts = _texts(ft.Column(controls=panel._editor_controls()))

    assert any("还缺" in text and "栏目英文名" in text and "主持人" in text
               for text in texts), texts
    assert TOC.column_issue(panel.values)[0] is True

    # 补齐后提示立即消失（输入回调只换 content，不重建输入框）
    panel._set_value("columnNameEN", "Logic and Cognition")
    panel._set_value("columnHost", "编辑部")
    assert panel._gate_hint_control().content is None

    # 切回「本期无栏目」也不再有缺项提示
    panel.values.update({"columnKind": "", "columnNameEN": "", "columnHost": ""})
    panel._render_editor()
    texts = _texts(ft.Column(controls=panel._editor_controls()))
    assert not any("还缺" in text for text in texts), texts


# ── 4. frozen #2 返工：可见「预览模板/篇目」入口（预览不锁死）─────────────
def _params_panel(tmp_path, *, tasks: list | None = None, settings: dict | None = None):
    panel, report, page = _make_panel(tmp_path, settings=settings or {
        "columnKind": "专栏", "columnName": "逻辑与认知",
        "columnNameEN": "", "columnHost": ""})
    page.add_picker = lambda *a, **k: ft.Container()
    page.page = SimpleNamespace(
        run_task=lambda fn, *a, **k: tasks.append(fn) if tasks is not None else None)
    panel._articles_for = str(report)
    panel.available = [{"name": "Jingru_Kou", "title": "可知性悖论"}]
    return panel, report, page


def _button_rows(controls) -> list:
    kinds = (ft.FilledButton, ft.OutlinedButton, ft.TextButton)
    return [row for row in controls
            if isinstance(row, ft.Row) and row.controls
            and all(isinstance(button, kinds) for button in row.controls)]


def test_params_exposes_visible_preview_button_with_missing_fields(tmp_path):
    tasks: list = []
    panel, _report, _page = _params_panel(tmp_path, tasks=tasks)

    controls = panel.params()

    rows = _button_rows(controls)
    labels = [button.content for row in rows for button in row.controls]
    assert "预览模板/篇目" in labels, labels
    four = next(row for row in rows if len(row.controls) == 4)
    preview = next(row for row in rows if any(
        getattr(button, "content", "") == "预览模板/篇目" for button in row.controls))
    assert len(preview.controls) == 1, "预览入口单独一行，不改 pinned 的 4 按钮行"
    four_at = next(i for i, control in enumerate(controls) if control is four)
    preview_at = next(i for i, control in enumerate(controls) if control is preview)
    assert preview_at > four_at, "预览入口在 4 个描边按钮之后"
    # 四项缺项：编辑区有缺项提示，但预览入口仍在（辅助预览不锁死，Lead m00363 A）。
    texts = _texts(ft.Column(controls=controls))
    assert any("还缺" in text for text in texts), texts
    assert panel.column_issue()[0] is True


def test_preview_button_schedules_preview_task_and_marks(tmp_path):
    tasks: list = []
    panel, _report, page = _params_panel(tmp_path, tasks=tasks)
    controls = panel.params()
    button = next(button for row in _button_rows(controls) for button in row.controls
                  if getattr(button, "content", "") == "预览模板/篇目")

    button.on_click(None)

    assert tasks == [panel._preview_task], "点击应把既有 _preview_task 交给 run_task"
    assert any(event == "toc_preview_requested" for event, _ in page.marks)


# ── 3. OBS-A：读取栏目表不静默覆盖 dirty 草稿 ─────────────────────────────
def test_load_settings_dialog_cancel_keeps_draft(tmp_path, monkeypatch):
    panel, _report, page, target_path, old_path = _scene(tmp_path)
    dialogs, dismissed = _capture_dialog(monkeypatch)

    panel._load_settings()

    assert len(dialogs) == 1
    dialog = dialogs[0]
    texts = _texts(dialog.title) + _texts(dialog.content)
    assert any("会覆盖草稿" in text for text in texts), texts
    labels = [str(getattr(action, "content", "")) for action in dialog.actions]
    assert labels == ["取消", "放弃草稿并读取", "保存草稿并读取"], labels

    _click(dialog, "取消")
    assert dismissed, "取消要关闭对话框"
    assert panel.values["columnName"] == "草稿栏目" and panel._dirty is True
    assert panel._loaded_path == str(old_path)
    assert not _marks(page, "toc_settings_loaded")


def test_load_settings_save_then_load_writes_draft_and_reads_target(tmp_path, monkeypatch):
    panel, _report, page, target_path, old_path = _scene(tmp_path)
    dialogs, _dismissed = _capture_dialog(monkeypatch)
    panel._load_settings()

    _click(dialogs[0], "保存草稿并读取")

    saved_old, error = TS.read(str(old_path))
    assert error == ""
    assert saved_old["columnName"] == "草稿栏目", "草稿要写回它所属的旧设置文件"
    target_data, _ = TS.read(str(target_path))
    assert target_data["columnName"] == "目标栏目"
    assert panel.values["columnName"] == "目标栏目", "保存后继续读取目标"
    assert panel._loaded_path == str(target_path)
    assert panel._dirty is False
    assert _marks(page, "toc_settings_loaded")


def test_load_settings_save_failure_keeps_draft_and_does_not_advance(tmp_path, monkeypatch):
    panel, _report, page, target_path, old_path = _scene(tmp_path)
    dialogs, _dismissed = _capture_dialog(monkeypatch)
    monkeypatch.setattr(panel, "_write_settings",
                        lambda path, force=False: (str(old_path), "磁盘已满"))
    panel._load_settings()

    _click(dialogs[0], "保存草稿并读取")

    assert panel.values["columnName"] == "草稿栏目", "保存失败不得丢草稿"
    assert panel._dirty is True
    assert panel._loaded_path == str(old_path), "保存失败不得推进读取路径"
    assert any("草稿保存失败" in text for text in _texts(panel.feedback))
    assert _marks(page, "toc_settings_error")
    target_data, _ = TS.read(str(target_path))
    assert target_data["columnName"] == "目标栏目", "目标文件不得被草稿覆盖"
    assert not _marks(page, "toc_settings_loaded")


def test_load_settings_discard_reads_target_and_leaves_draft_unsaved(tmp_path, monkeypatch):
    panel, _report, page, target_path, old_path = _scene(tmp_path)
    dialogs, _dismissed = _capture_dialog(monkeypatch)
    panel._load_settings()

    _click(dialogs[0], "放弃草稿并读取")

    assert panel.values["columnName"] == "目标栏目"
    assert panel._loaded_path == str(target_path)
    assert panel._dirty is False
    old_data, _ = TS.read(str(old_path))
    assert old_data["columnName"] == "旧栏目", "放弃草稿不得写盘"
    assert _marks(page, "toc_settings_loaded")


def test_load_settings_read_failure_keeps_old_state(tmp_path, monkeypatch):
    panel, _report, page, target_path, old_path = _scene(tmp_path)
    target_path.write_text("{ 不是合法 JSON", encoding="utf-8")
    dialogs, _dismissed = _capture_dialog(monkeypatch)
    panel._load_settings()

    _click(dialogs[0], "放弃草稿并读取")

    assert panel.values["columnName"] == "草稿栏目", "读失败不得写入部分值"
    assert panel._loaded_path == str(old_path), "读失败不得推进 loaded path"
    assert panel._dirty is True
    assert panel._load_error
    assert any("不是合法 JSON" in text for text in _texts(panel.feedback))
    assert _marks(page, "toc_settings_error")


def test_load_settings_clean_applies_without_dialog(tmp_path, monkeypatch):
    panel, _report, _page, target_path, _old_path = _scene(tmp_path)
    panel._dirty = False
    dialogs, dismissed = _capture_dialog(monkeypatch)

    panel._load_settings()

    assert dialogs == [] and dismissed == []
    assert panel.values["columnName"] == "目标栏目"
    assert panel._loaded_path == str(target_path)
    assert panel._dirty is False


def test_auto_load_missing_settings_does_not_create_file(tmp_path):
    panel, report, _page = _make_panel(tmp_path)
    panel.values["columnKind"] = "专栏"  # 用户正在填，但还没有设置文件

    panel._auto_load_settings(str(report))

    assert not (tmp_path / TS.FILE_NAME).exists(), "不为显示控件提前创建设置文件"
    assert panel.values["columnKind"] == "专栏", "同报告文件缺失时保留面板编辑"


# ── 5. frozen #2 原生返工（Lead m00904）：预览不得传不存在的设置文件 ─────
def _preview_calls(monkeypatch, payload: dict | None = None) -> list:
    """截获预览调用的 argv；返回 (calls, marks) 里只关心 call 列表。"""
    calls: list = []

    def _fake(call, **kwargs):
        calls.append(call)
        return {"ok": True, "code": 0, "json": payload or {
            "templateSource": "内置模板",
            "columnEnabled": False,
            "columnArticles": [],
            "columnValidationError": "",
        }, "lines": [], "error": ""}

    monkeypatch.setattr(TOC.EB, "read_json_command", _fake)
    return calls


def test_preview_without_settings_file_omits_settings_and_writes_nothing(tmp_path, monkeypatch):
    """原生 smoke frozen-2：干净目录首次预览失败（--settings 指向不存在的文件）。

    2026-11-06 Lead m00904：预览与生成共用「只传已存在的设置文件」解析——没有
    目录设置.json 时省略 --settings 让引擎用内置默认；预览零写入、不建文件。
    """
    panel, _report, page = _make_panel(tmp_path)
    # 引擎 preview JSON 的真实形状：只有 templateNote，没有 templateSource（正式结果才有）。
    calls = _preview_calls(monkeypatch, {
        "templateNote": "找不到可继承的成品，将使用内置模板（卷期号等需手工核对）",
        "columnEnabled": False,
        "columnArticles": [],
        "columnValidationError": "",
        "articles": [],
    })

    asyncio.run(panel._preview_task())

    assert len(calls) == 1, "有报告时预览必须调用引擎"
    args = _args_of(calls[0])
    assert "--preview" in args
    assert "--settings" not in args, f"不得把不存在的设置路径传给引擎：{args}"
    assert not any(TS.FILE_NAME in arg for arg in args), args
    assert not (tmp_path / TS.FILE_NAME).exists(), "预览不得提前创建 目录设置.json"
    assert any(event == "toc_preview" and fields.get("ok") is True
               for event, fields in page.marks), page.marks
    texts = _texts(panel.info)
    assert any("内置模板" in text for text in texts), texts
    assert any("已选 0 篇" in text for text in texts), texts
    assert not any("（未知）" in text for text in texts), texts


def test_preview_with_existing_settings_file_still_passes_it(tmp_path, monkeypatch):
    """已有 目录设置.json 时预览仍传该路径，且不改写文件（无改动不重写/不备份）。"""
    settings_path = tmp_path / TS.FILE_NAME
    _write_settings(settings_path)
    before = settings_path.read_text(encoding="utf-8")
    panel, _report, _page = _make_panel(tmp_path)
    calls = _preview_calls(monkeypatch)

    asyncio.run(panel._preview_task())

    assert len(calls) == 1
    args = _args_of(calls[0])
    assert "--settings" in args, args
    assert args[args.index("--settings") + 1] == str(settings_path)
    assert settings_path.read_text(encoding="utf-8") == before, "预览不得改写设置文件"
    assert not list(tmp_path.glob(TS.FILE_NAME + ".bak-*")), "预览不得产生备份文件"


def test_preview_failure_never_leaves_loading_card(tmp_path, monkeypatch):
    """Lead m00984(2)：预览失败/异常后信息卡换成失败说明，不能一直显示「预览中…」。"""
    def _engine_error(*_args, **_kwargs):
        return {"ok": False, "code": 2, "json": {}, "lines": [],
                "error": "❌ 找不到设置文件：/tmp/x/目录设置.json"}

    def _raise(*_args, **_kwargs):
        raise RuntimeError("引擎超时")

    for fake in (_engine_error, _raise):
        panel, _report, page = _make_panel(tmp_path)
        monkeypatch.setattr(TOC.EB, "read_json_command", fake)
        asyncio.run(panel._preview_task())

        info_texts = _texts(panel.info)
        assert not any("预览中" in text for text in info_texts), info_texts
        assert any("预览失败" in text for text in info_texts), info_texts
        assert any(event == "toc_preview" and fields.get("ok") is False
                   for event, fields in page.marks), page.marks


def test_preview_card_prefers_engine_template_source(tmp_path, monkeypatch):
    """Lead m00984(1)：引擎 preview 补 additive templateSource 后，卡片优先显示它。"""
    panel, _report, _page = _make_panel(tmp_path)
    _preview_calls(monkeypatch, {
        "templateSource": "上一期",
        "templateNote": "继承了上一期（第 97 期）的模板",
        "columnEnabled": False,
        "columnArticles": [],
        "columnValidationError": "",
    })

    asyncio.run(panel._preview_task())

    texts = _texts(panel.info)
    assert any(text == "模板来源：上一期" for text in texts), texts


# ── 6. 独立验收 task-6(1)：专辑预览卡显示本期全部篇目自动收入 ─────────────
def test_preview_album_shows_all_articles_auto_included(tmp_path, monkeypatch):
    """2026-11-06 用户决定(c) + 独立验收 task-6(1)：专辑的有效集合 = 当前报告全部篇目。

    设置文件里的 columnArticles 通常是空/旧子集，预览卡不能显示「已选 0 篇」；
    应显示「本期全部 N 篇自动收入（无需勾选）」，且不得把全体写回 columnArticles。
    """
    panel, _report, page = _make_panel(tmp_path, settings={
        "columnKind": "专辑",
        "columnName": "逻辑与认知",
        "columnNameEN": "Logic and Cognition",
        "columnHost": "编辑部",
    })
    articles = [{"name": f"作者{index}", "title": f"第 {index} 篇"} for index in range(1, 5)]
    calls = _preview_calls(monkeypatch, {
        "templateSource": "内置模板",
        "columnEnabled": True,
        "columnArticles": [],
        "columnValidationError": "",
        "articles": articles,
    })

    asyncio.run(panel._preview_task())

    texts = _texts(panel.info)
    assert any("本期全部 4 篇自动收入" in text for text in texts), texts
    assert not any("已选 0 篇" in text for text in texts), texts
    # 只用于显示：专辑全体不写回 columnArticles（专栏历史不被覆盖）。
    assert panel.values["columnArticles"] == [], panel.values
    assert panel.selected == [], panel.selected
    args = _args_of(calls[0])
    assert "--preview" in args
    assert "--settings" not in args, f"干净目录不得传不存在的设置文件：{args}"
    assert not (tmp_path / TS.FILE_NAME).exists(), "预览不得提前创建 目录设置.json"
    assert any(event == "toc_preview" and fields.get("ok") is True
               for event, fields in page.marks), page.marks
