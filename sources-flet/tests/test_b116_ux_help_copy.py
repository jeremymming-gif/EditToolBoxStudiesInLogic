"""批 116 · UX 修复 FIX-HELP：帮助页 IA 口径（P1-13）+ 目录最近跳转高亮（P2-12）。

锁：
1. 入口计数由 `ia_map` 现算，不再是「六个入口」：5 个一级页（引用库在侧栏无父行）
   + 引用库 3 组 9 叶 + 页头齿轮进设置；文案同时给出 `bib:settings` 可深链。
2. 旧 IA 名一律不得回流：四组 / 10 视图 / 规范化 / 六个入口 / 「维护」组。
3. 视图名统一「回读校验」；9 叶名称与 `ia_map` 逐一对得上（组名 查阅 / 采录 / 治理）。
4. 快捷键段仍与外壳同源（`settings_shell.shortcut_rows`），不另抄一份。
5. 目录点击后只有该行选中（accent_soft），`_active_toc` 记最近跳转；滚动联动高亮
   不做比例猜测（flet 1.0.5 无分区几何），见证据 notes 的 backlog。
6. 钉点文案不丢：`Jeremymming` / `MIT` / `作者来稿只读` / `绝不自动 push`
   （与 `test_b109_w45.test_help_page_states_proprietary_license_and_boundaries` 同一组）。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from ui import help_content as HC
from ui import help_page as HP
from ui import ia_map as IA
from ui import settings_shell as SHELL
from ui.tokens import Tokens

#: 旧 IA 口径（本轮统一清除）
STALE = ("四组", "10 视图", "10 个视图", "规范化", "六个入口", "维护四组")


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _app_stub(ai_enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        t=_t(),
        page=SimpleNamespace(run_task=lambda *a, **k: None),
        store=SimpleNamespace(get=lambda key, default=None: ai_enabled),
        app_version="1.6.0",
        flet_version="1.0.5",
        python_version="3.14.8",
        engine_status=SimpleNamespace(version="1.5.0", mode="auto"),
    )


def _walk(control, out: list) -> None:
    out.append(control)
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for item in value:
                _walk(item, out)
        elif isinstance(value, ft.Control):
            _walk(value, out)


def _texts(control) -> list[str]:
    nodes: list = []
    _walk(control, nodes)
    return [str(n.value) for n in nodes
            if isinstance(n, ft.Text) and n.value is not None]


def _joined() -> str:
    return "\n".join(_texts(HP.HelpPage(_app_stub()).build()))


def _facts() -> dict:
    """帮助页文案必须对齐的导航事实（直接读 ia_map，不抄常量）。"""
    top = [entry for entry in IA.nav_parent_entries() if entry["key"] != "bib"]
    groups = IA.group_views("bib")
    leaves = [view for view in IA.nav_views("bib") if not view.get("route_only")]
    return {"top": len(top), "groups": groups, "leaves": leaves}


# ── 1. 入口计数按实际导航现算 ─────────────────────────────────────────────
def test_entry_count_follows_navigation_tree():
    facts = _facts()
    assert facts["top"] == 5                      # 工作台/单篇/整期/设置/帮助
    assert [group["label"] for group in facts["groups"]] == ["查阅", "采录", "治理"]
    assert len(facts["leaves"]) == 9
    assert len({view["key"] for view in facts["leaves"]}) == 9

    joined = _joined()
    assert f"侧栏 {facts['top']} 个一级页" in joined
    assert f"引用库 {len(facts['groups'])} 组 {len(facts['leaves'])} 叶" in joined
    assert "页头齿轮进设置" in joined
    assert "单篇页里的 AI 面板" in joined


def test_entries_entry_documents_three_groups_nine_leaves_and_gear():
    joined = _joined()
    entry = next(detail for name, _where, detail in HC.ENTRIES if name == "引用库")
    assert entry.startswith("3 组 9 叶：")
    assert "bib:settings" in entry and "深链" in entry
    for group in _facts()["groups"]:
        assert group["label"] in entry, group["label"]
        for view in group["views"]:
            assert view["label"] in entry, view["label"]
    # 渲染出来的入口说明与常量同一份
    assert entry in joined


# ── 2. 旧 IA 名不得回流（渲染文本 + 纯数据文案都查）────────────────────────
def test_stale_ia_wording_is_gone_from_copy_and_render():
    rendered = _joined()
    data = "\n".join(
        [HC.TITLE, HC.SUBTITLE, HC.LICENSE, HC.THIRD_PARTY, *HC.TRAY_STEPS,
         *HC.TRAY_LIMITS, *HC.BOUNDARY,
         *(" ".join(str(item) for item in row)
           for rows in (HC.ENTRIES, HC.PLATFORM, HC.DETAILS, HC.DOCS)
           for row in rows)]
    )
    for stale in STALE:
        assert stale not in rendered, stale
        assert stale not in data, stale
    assert "回读校验" in rendered
    assert "回读校验" in next(d for _n, _w, d in HC.ENTRIES if "单篇" in _n)


def test_single_page_points_to_intake_group_readback_check():
    entry = next(detail for name, _where, detail in HC.ENTRIES if name == "单篇稿件")
    assert "「引用库 → 采录 → 回读校验」" in entry


# ── 3. 快捷键与外壳同源（不另抄一份）──────────────────────────────────────
def test_shortcut_section_stays_sourced_from_shell_rows():
    texts = _texts(HP.HelpPage(_app_stub()).build())
    for item in SHELL.SHORTCUTS:
        assert item["keys"] in texts, item["keys"]
        assert item["label"] in texts, item["label"]
    assert "与外壳实际绑定同源" in "\n".join(texts)


# ── 4. 目录：最近跳转高亮（不猜滚动位置）─────────────────────────────────
def test_toc_highlights_only_the_last_jump():
    app = _app_stub()
    page = HP.HelpPage(app)
    root = page.build()
    accent = page.t.palette.accent_soft
    assert page._active_toc == ""
    assert {row.bgcolor for row in page._toc_rows.values()} == {None}

    page._jump("docs")
    assert page._active_toc == "docs"
    assert page._toc_rows["docs"].bgcolor == accent
    assert [key for key, row in page._toc_rows.items() if row.bgcolor == accent] == ["docs"]

    page._jump("entries")
    assert page._active_toc == "entries"
    assert [key for key, row in page._toc_rows.items() if row.bgcolor == accent] == ["entries"]
    assert len(page._toc_rows) == len(HP.TOC) == 7
    assert isinstance(root, ft.Container)


def test_toc_note_states_highlight_means_last_jump():
    joined = _joined()
    assert "高亮为最近跳转的段" in joined
    # 不宣称按滚动位置高亮（当前实现做不到，见 notes backlog）
    assert "滚动高亮" not in joined


# ── 5. 钉点文案（与 b109_w45 同一组）─────────────────────────────────────
def test_pinned_copy_is_preserved():
    joined = _joined()
    assert "Jeremymming" in joined and "MIT" in joined
    assert "作者来稿只读" in joined and "绝不自动 push" in joined
