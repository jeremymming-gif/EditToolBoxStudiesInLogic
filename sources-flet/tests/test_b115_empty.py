"""批 115 设计检修 P1-4 / P1-9：InfoCheck 空态一行化、AI 区块高度服从内容。

锁：
1. InfoCheckCard 空态不是卡片：root 是 Column，`__init__` 不调 C.card /
   C.empty_state，只有一行 inline_note；「还没检查」只出现一次；无报告时
   「打开报告」不挂进控件树（不是禁用按钮）。
2. 有结果时摘要 / 表格 / 打开报告仍可用，open_btn 挂进头部行。
3. AI 关闭：返回一行 inline_note（Row），不是卡片，也没有任何勾选项。
4. AI 开启：空 `_status_ctl` / `_summary_ctl` 不可见，`_feedback` 整段收起；
   有状态才占位；协同/直连/暂存目录/回填/费用警告/打开设置都保留。
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import flet as ft

from services import ai_prefs as AI
from services import settings_store as SS
from ui import ai_panel as AIP
from ui import checks as CK
from ui.tokens import Tokens
from test_b114_polish import _AiFakePage, _button_contents, _texts, _walk


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _calls(func) -> set[str]:
    """函数体里出现的 `<attr>(...)` 调用属性名集合（注释/字符串不参与）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


def _article(**over):
    raw = {
        "folder": "01 张三", "folderName": "01 张三", "volume": "42", "issue": "3",
        "year": "2026", "startPage": "1", "endPage": "12", "pages": "12",
        "fontSize": "10.5", "lineSpacing": "1.5",
        "firstAuthorChinese": "张三", "chineseTitle": "论 一个 标题\n第二行",
        "actualPDFPages": 13, "hasPDF": True,
    }
    raw.update(over)
    return raw


def _payload():
    return {"articles": [
        _article(),
        _article(folder="02 李四", folderName="02 李四", startPage="13", endPage="20",
                 pages="8", actualPDFPages=8, firstAuthorChinese="",
                 firstAuthorEnglish="Li Si", chineseTitle="", englishTitle="On Logic"),
    ], "inconsistencies": ["01 张三：声明页数 12，实际 13 页"]}


def _store(tmp_path, monkeypatch, *, enabled: bool) -> SS.SettingsStore:
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "b115-empty-settings.json"))
    store = SS.SettingsStore()
    store.load()
    if enabled:
        AI.save(store, {"enabled": True})
    return store


# ── P1-4：InfoCheck 空态 ────────────────────────────────────────────────
def test_info_check_empty_state_is_one_borderless_line():
    card = CK.InfoCheckCard(_t())
    assert isinstance(card.root, ft.Column)
    assert not isinstance(card.root, ft.Container)
    calls = _calls(CK.InfoCheckCard.__init__)
    assert "card" not in calls, "页面主面板已是白面板，信息检查空态不许再套 C.card"
    assert "empty_state" not in calls, "空态不许再用 120 高的插画块"
    assert "inline_note" in calls and "section_label" in calls

    # 同义句只出现一次：chip 的「还没检查」；摘要此时不渲染。
    texts = _texts(card.root)
    assert sum("还没检查" in value for value in texts) == 1
    assert "还没检查" in "".join(texts)
    assert card.summary.visible is False
    assert card.empty.visible is True
    assert isinstance(card.empty, ft.Row) and card.empty.height is None

    # 无报告 → 不挂「打开报告」；不是禁用按钮占位。
    assert card.open_btn not in list(_walk(card.root))
    assert "打开报告" not in _button_contents(card.root)


def test_info_check_result_state_keeps_summary_table_and_open():
    called: list[str] = []
    card = CK.InfoCheckCard(_t(), on_open=called.append)
    card.show(_payload(), report="C:/report.txt")

    assert card.chip.content.value == "需要核对"
    assert card.summary.visible is True
    assert card.summary.value.startswith("共 2 篇；发现 1 处需要核对：")
    assert card.empty.visible is False
    assert card.table_box.visible is True and len(card.table_box.controls) == 1
    # 有报告才把「打开报告」挂进头部行，并且可点。
    assert card.open_btn in card._actions.controls
    assert card.open_btn.disabled is False
    card._open()
    assert called == ["C:/report.txt"]


def test_info_check_unreadable_and_failed_do_not_duplicate_empty_line():
    card = CK.InfoCheckCard(_t())
    card.show_unreadable("C:/report.txt")
    assert card.chip.content.value == "读不回"
    assert card.warning.visible is True and card.warning.value == CK.UNREADABLE_INFO
    # 警告行已经解释表格为什么空，通用空态不再叠一遍。
    assert card.empty.visible is False
    # 报告确实存在 → 仍能给「打开报告」。
    assert card.open_btn in card._actions.controls

    card.show_failed(2)
    assert card.chip.content.value == "检查失败"
    assert card.summary.visible is True and card.empty.visible is False
    assert card.open_btn not in card._actions.controls


# ── P1-9：AI 区块 ──────────────────────────────────────────────────────
def test_ai_panel_master_off_is_single_inline_note(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, enabled=False)
    view = AIP.AiPanel(_AiFakePage(store)).build()

    assert isinstance(view, ft.Row), "总开关关着时只留一行说明，不再套 C.card"
    assert not isinstance(view, ft.Container)
    text = " ".join(_texts(view))
    assert "AI 助手" in text and "总开关关着" in text
    assert not [ctl for ctl in _walk(view) if isinstance(ctl, ft.Checkbox)]


def test_ai_panel_master_on_empty_feedback_takes_no_space(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, enabled=True)
    panel = AIP.AiPanel(_AiFakePage(store))
    view = panel.build()

    # 卡片高度由内容决定：没有固定 height / 纵向 expand。
    assert isinstance(view, ft.Container)
    assert view.height is None
    assert view.expand is False
    # 空状态/摘要：两个 Text 都不可见，外层整段也收起（空 Text 带
    # max_lines 在 flet 1.0.5 会按最大行数占高，这是 P1-9 的根因）。
    assert panel._feedback is not None
    assert panel._feedback.visible is False
    assert panel._status_ctl.visible is False
    assert panel._summary_ctl.visible is False

    # 功能控件一个不少：两个勾选、暂存目录输入框、费用警告、打开设置。
    boxes = [ctl for ctl in _walk(view) if isinstance(ctl, ft.Checkbox)]
    assert len(boxes) >= 2
    assert panel._stage_field is not None
    assert "打开 AI 设置" in _button_contents(view)
    assert "直连核实会产生 API 费用" in " ".join(_texts(view))


def test_ai_panel_feedback_appears_only_with_content(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, enabled=True)
    panel = AIP.AiPanel(_AiFakePage(store))
    panel.build()

    panel._set_status("第一段：任务书已生成")
    assert panel._status_ctl.visible is True and panel._feedback.visible is True

    panel._set_status("")
    assert panel._status_ctl.visible is False and panel._feedback.visible is False

    panel._set_summary("AI 核实完成：核实报告已写进产物目录。", "ok")
    assert panel._summary_ctl.visible is True and panel._feedback.visible is True

    panel._set_summary("")
    assert panel._summary_ctl.visible is False and panel._feedback.visible is False
