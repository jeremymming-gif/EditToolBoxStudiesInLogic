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

import json
from pathlib import Path

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import toc as TOC

from test_b121_toc_settings import _make_panel


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
    panel, _, _ = _make_panel(tmp_path)
    panel._ensure_articles(str(report))

    controls = panel._editor_controls()

    checklist = controls[-1]
    assert isinstance(checklist, ft.Column)
    boxes = [c for row in checklist.controls if isinstance(row, ft.Row)
             for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.label for c in boxes] == ["Jingru_Kou", "Yanjun_Li"]


def test_editor_controls_keeps_legacy_selected_rows_without_report(tmp_path):
    panel, _, _ = _make_panel(tmp_path, selected=["01 Jingru_Kou"])
    panel.available = []

    controls = panel._editor_controls()

    article_rows = [row for row in controls
                    if isinstance(row, ft.Row) and len(row.controls) == 4]
    assert article_rows, "没有报告时仍应能显示已选篇目（读旧设置）"
    assert isinstance(article_rows[0].controls[0], ft.Container)


# ── 用户 m02929：勾选「专辑」默认整期都是专辑稿件 ─────────────────────────
def test_album_kind_defaults_all_articles_selected(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专辑",
                                                  "columnName": "逻辑与概率"})

    panel._ensure_articles(str(report))

    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]
    assert panel._dirty is True
    checklist = panel._article_checklist()
    header = checklist.controls[0]
    texts = [c.value for c in header.controls if isinstance(c, ft.Text)]
    assert any("勾选 2 / 2 篇" in value for value in texts), texts
    checks = [c for row in checklist.controls[1:] if isinstance(row, ft.Row)
              for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.value for c in checks] == [True, True]


def test_album_default_keeps_explicit_subset(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专辑"},
                              selected=["Jingru_Kou"])

    panel._ensure_articles(str(report))

    assert panel.selected == ["Jingru_Kou"], "用户显式取消的勾选不能被默认值覆盖"


def test_column_kind_does_not_default_select(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专栏"})

    panel._ensure_articles(str(report))

    assert panel.selected == []


def test_switching_kind_to_album_selects_all(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _ = _make_panel(tmp_path)
    panel._ensure_articles(str(report))

    panel._on_kind_select("专辑", "专辑")

    assert panel.values["columnKind"] == "专辑"
    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]
    assert panel._dirty is True
