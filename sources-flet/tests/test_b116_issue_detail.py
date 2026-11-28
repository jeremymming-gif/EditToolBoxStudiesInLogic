"""批 115 六步详情运行期契约（D1-115-26 / D1-115-29）。

覆盖：infocheck 五列表格/筛选、D3 下线编译预扫描后 refresh 页签的
PagingPanel 契约、cleantemp 默认只预演与预览失效、merge 覆盖确认（两出口）、
dirpush 输入校验、toc 仅生成 tex 默认 false；不启动真 GUI。
"""
from __future__ import annotations

import inspect
import os
import time
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import checks as CK
from ui import dirpush as DP
from ui import pages as P
from ui import toc as TOC

from test_b112_post_b import _FakeApp, _build_issue, _walk


def _labels(dialog) -> list[str]:
    out = []
    for ctl in list(dialog.actions or []) + [dialog.content]:
        for c in _walk(ctl):
            if isinstance(c, ft.Text):
                out.append(str(c.value))
            elif isinstance(c, (ft.Button, ft.FilledButton, ft.OutlinedButton,
                                ft.TextButton)):
                out.append(str(getattr(c, "content", "")))
    return out


def _article(**kw):
    data = {
        "folderName": "01 王路", "volume": "5", "issue": "2026", "year": "2026",
        "startPage": "19", "endPage": "40", "pages": "18",
        "actualPDFPages": 20, "fontSize": "10.5", "lineSpacing": "1.35",
        "firstAuthorChinese": "王路", "chineseTitle": "意义理论导论", "hasPDF": True,
    }
    data.update(kw)
    return CK.normalize_article(data)


# ── infocheck：5 列 + 筛选 + 行内展开 ────────────────────────────────
def test_info_table5_five_columns_and_expand_row():
    t = _FakeApp().t
    arts = [_article(),
            _article(folderName="02 张三", firstAuthorChinese="张三",
                     chineseTitle="指称与模态", hasPDF=False, actualPDFPages=None)]
    table = CK.build_info_table5(t, arts, active_filter="all",
                                 expanded="01 王路", on_toggle=lambda k: None,
                                 on_copy=lambda a: None)
    texts = [str(c.value) for c in _walk(table) if isinstance(c, ft.Text)]
    for head in CK.INFO5_COLUMNS:
        assert head in texts, head
    assert any("18 / 20" in x for x in texts)
    assert any("18 / —" in x for x in texts)
    # 展开行只放 3 个字段 + 复制该行
    assert any("第一作者" in x and "王路" in x for x in texts)
    assert any("中文标题" in x for x in texts)
    assert any("文件夹" in x for x in texts)
    buttons = [str(getattr(c, "content", "")) for c in _walk(table)
               if isinstance(c, (ft.Button, ft.FilledButton, ft.OutlinedButton,
                                 ft.TextButton))]
    assert any("复制该行" in x for x in buttons), buttons


def test_info_pages_cell_and_filters():
    text, tone = CK.info_pages_cell(_article())
    assert text == "18 / 20" and tone == "error"
    text, tone = CK.info_pages_cell(_article(actualPDFPages=None, hasPDF=False))
    assert tone == "warn" and "—" in text
    arts = [_article(), _article(folderName="02 张三", actualPDFPages=None,
                                 hasPDF=False)]
    assert len(CK.info_filter(arts, "all")) == 2
    assert len(CK.info_filter(arts, "issues")) == 1
    assert len(CK.info_filter(arts, "missing")) == 1
    counts = CK.info_counts(arts)
    assert "2" in counts


def test_infocheck_default_filter_is_all():
    page, _root = _build_issue()
    page.activate("infocheck", source="test")
    assert page._info_filter == "all"


# ── D3/D7：编译预扫描与事件解析整体下线，刷新页码交给 PagingPanel ─────
def _issue_tree(tmp_path: Path) -> Path:
    root = tmp_path / "2026年第5期(90)"
    (root / "000 目录").mkdir(parents=True)
    (root / "000 目录" / "目录.tex").write_text("x", encoding="utf-8")
    for name, tex in (("01 王路", "王路.tex"), ("02 张三", "张三.tex"),
                      ("03 李四", "李四.tex")):
        folder = root / name
        folder.mkdir()
        (folder / tex).write_text("x", encoding="utf-8")
    (root / "04 赵六").mkdir()
    (root / "04 赵六" / "说明.txt").write_text("x", encoding="utf-8")
    return root


def test_compile_scan_and_event_parsing_are_gone_after_d3():
    """D3：GUI 不再预扫描 tex、不再解析编译事件，改由引擎自报。"""
    assert not hasattr(ASM, "compile_plan")
    assert not hasattr(ASM, "compile_event")
    assert not hasattr(ASM, "CompileTarget")
    assert not hasattr(ASM, "_find_tex")
    assert not hasattr(ASM, "_tex_key")
    assert not hasattr(P.IssuePage, "_compile_result")
    assert not hasattr(P.IssuePage, "_compile_plan")
    assert not hasattr(P.IssuePage, "_compile_rows")


def test_parse_count_accepts_ge_and_xiang():
    assert ASM.parse_count(["Will move: 18 个文件"]) == 18
    assert ASM.parse_count(["预演：26 项"]) == 26
    assert ASM.parse_count(["没有数字"]) == 0


def test_refresh_view_mounts_paging_panel_instead_of_compile_only(tmp_path):
    """D3/D7：refresh 页签只有一个主动作「编译并刷新页码」，没有 --only 输入。"""
    page, _root = _build_issue()
    page.set_field("issue_dir", str(_issue_tree(tmp_path)))
    page.activate("refresh", source="test")
    section = page._section_refs["refresh"]
    texts = [str(c.value) for c in _walk(section) if isinstance(c, ft.Text)]
    buttons = [str(getattr(c, "content", "")) for c in _walk(section)
               if isinstance(c, (ft.Button, ft.FilledButton, ft.OutlinedButton,
                                 ft.TextButton))]
    assert "编译并刷新页码" in buttons
    assert "刷新页码" in buttons
    labels = [str(c.label) for c in _walk(section) if isinstance(c, ft.Checkbox)]
    # D3 下线了「自动勾选」：candidate 的 PagingPanel 只保留「重生成目录」。
    # 这里只锁仍存在的目录勾选项，不再锁已下线的「编译后自动刷新页码」文案。
    assert "重生成目录" in labels
    assert "compile_only" not in page.fields
    assert page.field("compile_only") == ""
    assert page._primary_btns["refresh"].disabled is True
    assert "先预览再确认" in str(page._primary_btns["refresh"].tooltip)
    assert any("排序结果" in value for value in texts)


# ── cleantemp：默认只预演 / 预览有效性 / 确认框 ─────────────────────
def test_cleantemp_default_dry_run_true_and_migration():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    assert ASM.cleantemp_dry_run(page) is True
    assert app.store.get("options.cleantemp_dry_run_migrated") is True
    app.store.set("options.cleantemp_dry_run", False)
    assert ASM.cleantemp_dry_run(page) is False


def test_cleantemp_preview_validity_by_issue_and_mode():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期")
    page._ct_preview = {"ok": True, "issue": "/tmp/期", "mode": "aux", "count": 18}
    assert ASM.cleantemp_preview_ok(page, "aux") is True
    assert ASM.cleantemp_preview_ok(page, "delivery") is False
    assert ASM.cleantemp_preview_ok(page) is True
    page._ct_preview["issue"] = "/tmp/别的期"
    assert ASM.cleantemp_preview_ok(page) is False


def test_cleantemp_single_entry_has_no_confirm_dialog():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", "/tmp/期")
    app.store.set("options.cleantemp_dry_run", False)
    page._ct_preview = {"ok": True, "issue": "/tmp/期", "mode": "aux",
                        "count": 18, "ts": "2026-11-04 11:04:00"}
    shown: list = []
    app.page.show_dialog = lambda d: shown.append(d)
    assert page._primary_label("cleantemp") == "扫描本期"
    page._primary_action("cleantemp")
    assert shown == [], "T3 单入口点击即自动校验+清理，不再弹危险确认框"
    assert page._cleanup.phase == "scanning"


def test_merge_overwrite_dialog_two_exits(tmp_path):
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    existing = tmp_path / "合并_终稿.pdf"
    existing.write_bytes(b"%PDF-1.4")
    page.set_field("issue_dir", str(tmp_path))
    page.activate("merge", source="test")
    page.set_field("merge_out", str(existing))
    shown: list = []
    app.page.show_dialog = lambda d: shown.append(d)
    page._run_step = lambda key: (_ for _ in ()).throw(AssertionError("未确认就跑"))
    page._primary_action("merge")
    assert shown, "存在同名文件时应弹覆盖确认"
    labels = _labels(shown[0])
    assert any("取消" == x for x in labels), labels
    assert any("覆盖并合并" in x for x in labels), labels
    assert not any("另存为" in x for x in labels), "D1-115-29：本轮不提供另存为出口"


def test_merge_call_detect_flag():
    call = EB.merge_call(folder="/tmp/期", detect=True)
    assert "--detect" in call.argv and "--folder" in call.argv
    call = EB.merge_call(folder="/tmp/期")
    assert "--detect" not in call.argv


# ── dirpush：输入校验 ─────────────────────────────────────────────
def test_dirpush_out_root_validation(tmp_path):
    issue = tmp_path / "2026年第5期(90)"
    issue.mkdir()
    toc = issue / "000 Table of Contents"
    toc.mkdir()
    assert DP.validate_out_root("", str(issue))[0] == "ok"
    level, msg = DP.validate_out_root("相对/路径", str(issue))
    assert level == "error" and "绝对路径" in msg
    level, msg = DP.validate_out_root(str(issue / "输出"), str(issue))
    assert level == "error" and "期目录内" in msg
    # B 方案：当期 000 是唯一允许的期内输出根；其子目录仍拒绝。
    assert DP.validate_out_root(str(toc), str(issue))[0] == "ok"
    level, msg = DP.validate_out_root(str(toc / "输出"), str(issue))
    assert level == "error" and "期目录内" in msg
    level, msg = DP.validate_out_root(str(tmp_path / "推送"), str(issue))
    assert level == "ok", msg


def test_dirpush_normalize_issues_and_year():
    assert DP.normalize_issues("1, 2，3、4 5")[0] == [1, 2, 3, 4, 5]
    numbers, bad = DP.normalize_issues("1,x,0")
    assert numbers == [1] and bad == ["x", "0"]
    assert DP.year_error("2026") == ""
    assert "4 位" in DP.year_error("26")


def test_dirpush_annual_disables_english_switch():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", "/tmp/期")
    app.store.set("form.dirpush_mode", DP.MODE_ANNUAL)
    page.activate("dirpush", source="test")
    params = ASM.build_params(page, "dirpush")
    texts = [str(c.value) for c in _walk(params) if isinstance(c, ft.Text)]
    assert any("只作用于当期" in x for x in texts)


# ── toc：仅生成 tex 默认 false + 收起的旧双主按钮 ─────────────────
def test_toc_text_only_default_false_without_store_write():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    assert ASM._toc_text_only(page) is False
    # 批 117：读取开关不再写 store / 迁移标记（不许静默改用户值）。
    assert app.store.get("options.toc_text_only") is None
    assert app.store.get("options.toc_text_only_migrated") is None
    app.store.set("options.toc_text_only", True)
    assert ASM._toc_text_only(page) is True
    assert app.store.get("options.toc_text_only") is True


def test_toc_params_no_primary_button_and_text_actions():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", "/tmp/期")
    params = ASM.build_params(page, "toc")
    labels = [str(getattr(c, "content", "")) + str(getattr(c, "text", ""))
              for c in _walk(params) if isinstance(c, (ft.OutlinedButton, ft.TextButton))]
    assert any("读取栏目表" in x for x in labels), labels
    assert any("保存栏目表" in x for x in labels), labels
    assert not [c for c in _walk(params) if isinstance(c, ft.FilledButton)]
    source = inspect.getsource(TOC.TocPanel.params)
    assert "primary_button(" not in source
    assert "生成并编译" not in source


def test_toc_result_has_article_counts():
    page, _root = _build_issue()
    page.activate("toc", source="test")
    page._toc_payload = {"articles": {"total": 26, "inToc": 24},
                         "column": {"enabled": 4, "title": 3},
                         "placed": {"zhPdf": "/tmp/000/目录.pdf"}, "compiled": True}
    texts = [str(c.value) for c in _walk(page._toc_result()) if isinstance(c, ft.Text)]
    assert any("24" in x for x in texts)
    assert any("4" in x for x in texts)


def test_toc_call_always_passes_result_json():
    call = EB.toc_call(report="/tmp/期/信息检查.txt", text_only=True,
                       result_json="/tmp/期/目录结果.json")
    assert "--result-json" in call.argv
    call = EB.toc_call(report="/tmp/期/信息检查.txt", text_only=False,
                       result_json="/tmp/期/目录结果.json")
    assert call.argv.count("--result-json") == 1
