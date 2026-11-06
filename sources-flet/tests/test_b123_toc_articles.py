"""批 123（用户 m02698）：生成目录勾选「哪些文章是专栏文章」。

根因：篇目清单只在批 117 起已死的 `_preview_task` 里加载，真实生成路径
（整期装配 → `build_step_call("toc")`）从未填充 `available`，用户无法勾选。
本批改为：报告旁边《信息检查》同名 JSON 同步载入篇目 → 编辑区直接出勾选清单。

锁四组契约（离线，不建窗口、不跑引擎）：

1. `articles_from_report()` 能从报告同 stem 的 `.json` 读篇目（folderName/folder/标题）。
2. `_ensure_articles()` 在真实路径载入 `available`，并把历史「全名」规范化成 folderName。
3. `_article_checklist()` 渲染每篇一个 Checkbox，勾选/取消/全选/清空写进 `selected` 并标脏。
4. 无 JSON 时安静回退（`available` 空、不抛异常），不阻塞生成目录。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import toc_settings as TS
from ui import toc as TOC

from test_b121_toc_settings import _make_panel, _write_settings


def _write_report(tmp_path: Path, articles: list[dict]) -> tuple[Path, Path]:
    report = tmp_path / "信息检查2026_11_05_07_57_55.txt"
    report.write_text("report", encoding="utf-8")
    payload = {"articles": articles}
    js = report.with_suffix(".json")
    js.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return report, js


ARTICLES = [
    {"folderName": "Jingru_Kou", "folder": "01 Jingru_Kou",
     "chineseTitle": "可知性悖论、形式可证与认知实现", "englishTitle": "Knowability"},
    {"folderName": "Yanjun_Li", "folder": "02 Yanjun_Li",
     "chineseTitle": "专栏二篇", "englishTitle": "Column Two"},
]


def test_articles_from_report_reads_sibling_json(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)

    rows = TOC.articles_from_report(str(report))

    assert [row["name"] for row in rows] == ["Jingru_Kou", "Yanjun_Li"]
    assert rows[0]["folder"] == "01 Jingru_Kou"
    assert rows[0]["title"] == "可知性悖论、形式可证与认知实现"


def test_articles_from_report_missing_json_returns_empty(tmp_path):
    report = tmp_path / "信息检查.txt"
    report.write_text("report", encoding="utf-8")

    assert TOC.articles_from_report(str(report)) == []
    assert TOC.articles_from_report("") == []


def test_ensure_articles_fills_available_and_normalizes_selected(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, page = _make_panel(tmp_path, selected=["01 Jingru_Kou"])

    panel._ensure_articles(str(report))

    assert [row["name"] for row in panel.available] == ["Jingru_Kou", "Yanjun_Li"]
    # 历史设置里存的是全名，勾选态必须映射到 folderName。
    assert panel.selected == ["Jingru_Kou"]
    assert any(event == "toc_articles_loaded" for event, _ in page.marks)


def test_ensure_articles_without_json_falls_back_quietly(tmp_path):
    report = tmp_path / "信息检查.txt"
    report.write_text("report", encoding="utf-8")
    panel, _, _ = _make_panel(tmp_path)

    panel._ensure_articles(str(report))  # 测试桩没有 run_task，必须不抛

    assert panel.available == []


def test_column_first_entry_without_draft_does_not_default_select_all(tmp_path):
    """2026-11-06 用户决定(g)：专栏首次进入、没有草稿时不得默认全选。"""
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, page = _make_panel(tmp_path, settings={
        "columnKind": "专栏", "columnName": "逻辑与概率",
        "columnNameEN": "Logic", "columnHost": "张三"})

    panel._ensure_articles(str(report))

    assert panel.available, "篇目应已载入，空选是用户的真实起点"
    assert panel.selected == [], "专栏首进入不得默认勾选任何篇目"
    assert panel._is_article_selected("Jingru_Kou") is False
    assert panel._dirty is False, "只是载入篇目，不得因此标脏/写盘"
    assert not any(event == "toc_settings_saved" for event, _ in page.marks)


def test_article_checklist_renders_checkboxes_with_selected_state(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, selected=["Jingru_Kou"])
    panel._ensure_articles(str(report))

    checklist = panel._article_checklist()

    assert isinstance(checklist, ft.Column)
    header = checklist.controls[0]
    texts = [c.value for c in header.controls if isinstance(c, ft.Text)]
    assert any("勾选 1 / 2 篇" in value for value in texts), texts
    item_rows = [c for c in checklist.controls[1:] if isinstance(c, ft.Row)]
    checks = [c for row in item_rows for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.label for c in checks] == ["Jingru_Kou", "Yanjun_Li"]
    assert [c.value for c in checks] == [True, False]


def test_toggle_select_all_and_clear_update_selected(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, selected=[])
    panel._ensure_articles(str(report))

    panel._toggle_article("Jingru_Kou", True)
    assert panel.selected == ["Jingru_Kou"]
    assert panel._dirty is True

    panel._toggle_article("Jingru_Kou", False)
    assert panel.selected == []

    panel._select_all_articles()
    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]
    assert panel._is_article_selected("Yanjun_Li") is True

    panel._clear_articles()
    assert panel.selected == []


def test_editor_controls_shows_checklist_when_report_loaded(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    # 2026-11-06 用户决定(a)/(b)：勾选清单只在「专栏」显示。
    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专栏"})
    panel._ensure_articles(str(report))

    controls = panel._editor_controls()

    checklist = controls[-1]
    assert isinstance(checklist, ft.Column)
    boxes = [c for row in checklist.controls if isinstance(row, ft.Row)
             for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.label for c in boxes] == ["Jingru_Kou", "Yanjun_Li"]


def test_editor_controls_keeps_legacy_selected_rows_without_report(tmp_path):
    # 2026-11-06 用户决定(b)：历史选中行只在「专栏」模式下显示。
    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专栏"},
                              selected=["01 Jingru_Kou"])
    panel.available = []

    controls = panel._editor_controls()

    article_rows = [row for row in controls
                    if isinstance(row, ft.Row) and len(row.controls) == 4]
    assert article_rows, "没有报告时仍应能显示已选篇目（读旧设置）"
    assert isinstance(article_rows[0].controls[0], ft.Container)


# ── 2026-11-06 用户决定(c)：专辑 = 当前报告全部篇目自动收入 ───────────────
# 旧契约（B-374）「显式空专辑集合保持空、缓存重建仍空」已被用户决定(c) 取代：
# 专辑的有效选择永远是当前报告全体；columnArticles 只保存专栏历史，绝不写专辑全体。
def _album_panel(tmp_path, *, settings=None, selected=None):
    base = {"columnKind": "专辑", "columnName": "逻辑与概率",
            "columnNameEN": "Logic and Probability", "columnHost": "张三"}
    base.update(settings or {})
    return _make_panel(tmp_path, settings=base, selected=selected)


def _flat_controls(control) -> list:
    found = [control]
    for child in getattr(control, "controls", None) or []:
        found.extend(_flat_controls(child))
    return found


def test_album_kind_includes_all_articles_without_writing_selection(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, page = _album_panel(tmp_path)

    panel._ensure_articles(str(report))

    assert panel.selected == [], "专辑全体是有效状态，不写进 columnArticles（保留专栏历史）"
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)
    assert panel._dirty is False, "只是载入不得标脏/改盘"
    assert any(event == "toc_articles_loaded" for event, _ in page.marks)


def test_album_editor_shows_auto_include_count_without_manual_controls(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _album_panel(tmp_path)
    panel._ensure_articles(str(report))

    controls = panel._editor_controls()
    flat = [c for control in controls for c in _flat_controls(control)]

    assert not any(isinstance(c, ft.Checkbox) for c in flat), "专辑不得显示逐篇勾选"
    texts = [c.value for c in flat if isinstance(c, ft.Text)]
    assert any("自动收入" in value and "2" in value for value in texts), texts
    buttons = [c.content for c in flat if isinstance(c, ft.TextButton)]
    assert buttons == [], f"专辑不得有全选/清空/↑↓/删除等手动控件：{buttons}"


def test_album_explicit_empty_saved_settings_do_not_change_effective_all(tmp_path):
    """决定(c)：旧「显式空专辑集合保持空」作废；空列表读到也按当前报告全体。"""
    report, _ = _write_report(tmp_path, ARTICLES)
    _write_settings(tmp_path / TS.FILE_NAME, columnKind="专辑",
                    columnName="逻辑与概率", columnNameEN="Logic", columnHost="张三",
                    columnArticles=[])
    panel, _, _ = _album_panel(tmp_path)
    panel._auto_load_settings(str(report))

    panel._ensure_articles(str(report))

    assert panel.selected == []
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)
    assert panel._dirty is False, "读入旧设置不得静默重写磁盘"


def test_album_cached_rebuild_and_async_arrival_keep_everyone_included(tmp_path, monkeypatch):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _album_panel(tmp_path)
    panel._ensure_articles(str(report))
    panel._ensure_articles(str(report))  # 缓存重建
    assert panel.selected == []
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)

    late, _, _ = _album_panel(tmp_path)
    late.available = []

    async def _fake_load():
        late.available = [dict(row, name=row["folderName"]) for row in ARTICLES]
        late._articles_for = str(report)

    monkeypatch.setattr(late, "_load_articles", _fake_load)
    asyncio.run(late._load_articles_and_render())

    assert late.selected == [], "异步篇目后到不得把专辑全体写进 columnArticles"
    assert all(late._is_article_selected(row["folderName"]) for row in ARTICLES)
    assert late._dirty is False


def test_album_save_keeps_column_history_and_never_writes_album_all(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    target = tmp_path / TS.FILE_NAME
    panel, _, _ = _album_panel(tmp_path, selected=["Jingru_Kou"])  # 专栏历史勾选

    panel._ensure_articles(str(report))
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)

    path, error = panel.sync_settings(force=True)

    assert error == "" and path == str(target)
    saved, _ = TS.read(str(target))
    assert saved["columnArticles"] == ["Jingru_Kou"], "专辑全体不得覆盖 columnArticles"
    assert saved["columnKind"] == "专辑"


def test_switching_album_back_to_column_restores_column_selection(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, page = _make_panel(tmp_path, selected=["Jingru_Kou"])
    panel.values.update({"columnKind": "专栏", "columnName": "逻辑与概率",
                         "columnNameEN": "Logic", "columnHost": "张三"})

    panel._ensure_articles(str(report))
    assert panel._is_article_selected("Yanjun_Li") is False

    panel._on_kind_select("专辑", "专辑")
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)
    assert panel.selected == ["Jingru_Kou"], "切专辑不得改专栏选择"

    panel._on_kind_select("专栏", "专栏")
    assert panel.selected == ["Jingru_Kou"]
    checklist = panel._article_checklist()
    checks = [c for row in checklist.controls[1:] if isinstance(row, ft.Row)
              for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.value for c in checks] == [True, False]
    assert any(event == "toc_editor" for event, _ in page.marks), "切换后要刷新编辑区"


def test_switching_report_does_not_leak_album_selection(tmp_path):
    a_dir = tmp_path / "A期"
    b_dir = tmp_path / "B期"
    a_dir.mkdir()
    b_dir.mkdir()
    report_a, _ = _write_report(a_dir, ARTICLES)
    report_b, _ = _write_report(b_dir, [{"folderName": "Other_Person",
                                         "folder": "01 Other_Person",
                                         "chineseTitle": "另一篇", "englishTitle": ""}])
    panel, _, page = _make_panel(tmp_path, settings={"columnKind": "专辑",
                                                     "columnName": "逻辑与概率",
                                                     "columnNameEN": "Logic",
                                                     "columnHost": "张三"})
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    panel._ensure_articles(str(report_a))
    assert all(panel._is_article_selected(row["folderName"]) for row in ARTICLES)

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))
    panel._ensure_articles(str(report_b))

    assert panel.values["columnKind"] == "", "换到没有设置文件的报告应回到默认栏目类型"
    assert panel.selected == [], "不得把 A 的专辑状态串到 B"
    assert panel._is_article_selected("Other_Person") is False
    assert panel.values["columnName"] == "", "不得把 A 的栏目名串到 B"


def test_no_column_mode_hides_checklist_and_keeps_stale_selection_harmless(tmp_path):
    """决定(a)：本期无栏目可生成；勾选菜单隐藏，旧选择不生效但保留以便切回专栏。"""
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, selected=["Jingru_Kou"])

    panel._ensure_articles(str(report))
    controls = panel._editor_controls()
    flat = [c for control in controls for c in _flat_controls(control)]

    assert not any(isinstance(c, ft.Checkbox) for c in flat), "无栏目不得显示逐篇勾选"
    buttons = [c.content for c in flat if isinstance(c, ft.TextButton)]
    assert not any(label in ("全选", "清空", "↑", "↓", "删除") for label in buttons), buttons
    texts = [c.value for c in flat if isinstance(c, ft.Text)]
    assert any("本期无栏目" in value for value in texts), texts
    assert panel.column_issue() == (False, "")
    assert panel.selected == ["Jingru_Kou"], "隐藏旧选择，但专栏历史不清空"