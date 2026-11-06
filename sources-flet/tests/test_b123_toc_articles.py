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


# ── B-374（A-02）：专辑默认全选只补「未初始化」，不得覆盖显式空集/子集 ─────
def test_album_explicit_empty_survives_cache_and_rebuild(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, page = _make_panel(tmp_path, settings={"columnKind": "专辑"})
    panel._auto_load_settings(str(report))
    panel._ensure_articles(str(report))
    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"], "首次选专辑仍应默认全选"

    panel._clear_articles()  # 用户显式清空
    assert panel.selected == []

    panel._ensure_articles(str(report))  # 切页重建：缓存命中后不得再默认全选
    assert panel.selected == [], "显式空集在重建后必须保持"

    checklist = panel._article_checklist()
    checks = [c for row in checklist.controls[1:] if isinstance(row, ft.Row)
              for c in row.controls if isinstance(c, ft.Checkbox)]
    assert [c.value for c in checks] == [False, False]
    header = checklist.controls[0]
    texts = [c.value for c in header.controls if isinstance(c, ft.Text)]
    assert any("勾选 0 / 2 篇" in value for value in texts), texts


def test_saved_empty_album_selection_reloads_as_explicit_empty(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    target = tmp_path / TS.FILE_NAME
    _write_settings(target, columnKind="专辑", columnName="逻辑与概率", columnArticles=[])

    panel, _, _ = _make_panel(tmp_path, settings={"columnKind": "专辑"})
    panel._auto_load_settings(str(report))
    assert panel.selected == []
    panel._ensure_articles(str(report))

    assert panel.selected == [], "文件里已保存的空列表是明确空集，重载后不得默认全选"
    assert panel._dirty is False, "只是读入不得标脏"


def test_album_explicit_empty_survives_manual_save_and_reload(tmp_path):
    report, _ = _write_report(tmp_path, ARTICLES)
    target = tmp_path / TS.FILE_NAME
    panel, _, _page = _make_panel(tmp_path, settings={"columnKind": "专辑",
                                                      "columnName": "逻辑与概率"})
    panel._auto_load_settings(str(report))
    panel._ensure_articles(str(report))
    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]
    panel._clear_articles()
    path, error = panel.sync_settings(force=True)
    assert error == "" and path == str(target)
    saved, _ = TS.read(str(target))
    assert saved["columnArticles"] == [], "显式清空要原样保存成空列表"

    # 新面板（等价于重启界面后重新读入）也不得默认全选。
    fresh, _, _ = _make_panel(tmp_path, settings={"columnKind": "专辑"})
    fresh._auto_load_settings(str(report))
    fresh._ensure_articles(str(report))
    assert fresh.selected == []
    assert [c.value for row in fresh._article_checklist().controls[1:]
            if isinstance(row, ft.Row) for c in row.controls
            if isinstance(c, ft.Checkbox)] == [False, False]


def test_async_articles_later_do_not_override_explicit_choice(tmp_path, monkeypatch):
    report, _ = _write_report(tmp_path, ARTICLES)
    panel, _, _page = _make_panel(tmp_path, settings={"columnKind": "专辑"})
    panel.available = []
    panel._clear_articles()  # 篇目还没到，用户先明确清空

    async def _fake_load():
        panel.available = [dict(row, name=row["folderName"]) for row in ARTICLES]
        panel._articles_for = str(report)

    monkeypatch.setattr(panel, "_load_articles", _fake_load)
    asyncio.run(panel._load_articles_and_render())

    assert panel.selected == [], "异步篇目后到不得把显式空集重置成全选"
    checks = [c for row in panel._article_checklist().controls[1:]
              if isinstance(row, ft.Row) for c in row.controls
              if isinstance(c, ft.Checkbox)]
    assert [c.value for c in checks] == [False, False]


def test_switching_report_does_not_leak_album_selection(tmp_path):
    a_dir = tmp_path / "A期"
    b_dir = tmp_path / "B期"
    a_dir.mkdir()
    b_dir.mkdir()
    report_a, _ = _write_report(a_dir, ARTICLES)
    report_b, _ = _write_report(b_dir, [{"folderName": "Other_Person",
                                         "folder": "01 Other_Person",
                                         "chineseTitle": "另一篇", "englishTitle": ""}])
    panel, _, page = _make_panel(tmp_path, settings={"columnKind": "专辑"})
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    panel._ensure_articles(str(report_a))
    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))
    panel._ensure_articles(str(report_b))

    assert panel.values["columnKind"] == "", "换到没有设置文件的报告应回到默认栏目类型"
    assert panel.selected == [], "不得把 A 的专辑勾选串到 B（也不得默认勾 B 全部）"


# ── 返工 R1（Codex 独立验收）：既有无栏目空集 ≠ 专辑里的明确清空 ────────────
def test_saved_no_column_empty_settings_still_defaults_on_first_album_switch(tmp_path):
    """既有设置文件是「本期无栏目 + 空篇目」时，首次切到专辑仍应默认全选。"""
    report, _ = _write_report(tmp_path, ARTICLES)
    _write_settings(tmp_path / TS.FILE_NAME, columnKind="", columnName="",
                    columnArticles=[])
    panel, _, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report))
    panel._auto_load_settings(str(report))
    panel._ensure_articles(str(report))
    assert panel.selected == [], "无栏目设置里的空列表不是勾选结果"

    panel._on_kind_select("专辑", "专辑")

    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"], (
        "既有无栏目空集不是『专辑里的明确清空』，首次选专辑仍要默认全选")
    assert panel._dirty is True

    # 之后在专辑里明确清空 → 缓存重建与保存重载都必须保持空集（B-374 原契约）。
    panel._clear_articles()
    panel._ensure_articles(str(report))
    assert panel.selected == []
    path, error = panel.sync_settings(force=True)
    assert error == "" and path == str(tmp_path / TS.FILE_NAME)
    fresh, _, _ = _make_panel(tmp_path)
    fresh._auto_load_settings(str(report))
    fresh._ensure_articles(str(report))
    assert fresh.selected == [], "已保存的专辑空集重载后不得恢复全选"


def test_saved_column_empty_settings_still_defaults_on_album_switch(tmp_path):
    """已保存「专栏 + 空篇目」同样不是专辑空集：切到专辑仍要默认全选。"""
    report, _ = _write_report(tmp_path, ARTICLES)
    _write_settings(tmp_path / TS.FILE_NAME, columnKind="专栏",
                    columnName="逻辑与概率", columnArticles=[])
    panel, _, _ = _make_panel(tmp_path)
    panel._auto_load_settings(str(report))
    panel._ensure_articles(str(report))

    panel._on_kind_select("专辑", "专辑")

    assert panel.selected == ["Jingru_Kou", "Yanjun_Li"]
