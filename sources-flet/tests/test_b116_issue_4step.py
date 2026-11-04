"""批 116 ISSUE4：整期装配 E′ 四步工作台（用户拍板 I-2）。

锁住的不变量（壳层，不建窗口、不跑引擎）：
1. rail 4 视图（宽窗 216 栏 / 内容 <880 时 54 高横条），标签、caption、选中态；
2. 视图状态聚合：任一步 error→红 / 有警告→琥珀 / running→动 / 全完成→绿 / 否则灰；
3. 6 条 `issue:<step>` 深链 alias 全保留、22 路由不变；深链落到所属视图的两个区块；
4. 01/02、03/04 同屏但**各自有运行入口**（不合并引擎调用、不合并确认）；
5. 04 只读「前置页」卡：空态说明 + detect 结果（文件名/页数/缺失），不提供编辑；
6. 03 编译成功 → 滚到 04 区块，但**不自动运行 04**（04 保留独立确认点）；
7. 页头「运行整期…」默认 1–4、日志 dock、⌘1–6 行为不变。
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import flet as ft

from ui import assembly as ASM
from ui import help_content as HC
from ui import ia_map as IA
from ui import pages as P
from test_b112_post_b import _build_issue, _walk


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _walk(control) if isinstance(ctl, ft.Text)]


def _issue_page(issue: str = "/tmp/期目录"):
    page, _root = _build_issue()
    page.set_field("issue_dir", issue)
    return page


# ── 1. 4 个视图：标签 / caption / 步骤归属 ────────────────────────────
def test_issue_views_are_four_with_labels_and_captions():
    views = IA.issue_views()
    assert [view["key"] for view in views] == ["catalog", "produce", "clean", "push"]
    assert [view["title"] for view in views] == [
        "生成目录", "编译并合并", "清理临时文件", "生成推送文案"]
    assert [view["caption"] for view in views] == [
        "检查 tex 信息 · 生成目录",
        "编译全部 · 合并 PDF",
        "删除临时文件（移入回收站）",
        "目录摘要推送（生成 docx）",
    ]
    # 批 116 ISSUE4-D：rail 不再画序号，`range` 字段已删；caption 仍不复述标题。
    for view in views:
        caption = str(view["caption"])
        assert not caption.startswith(("01", "02", "03", "04", "05", "06")), view["key"]
        assert caption != str(view["title"]), view["key"]
        assert "range" not in view, f"{view['key']} 不再携带序号数据"
        assert view.get("short"), "窄窗 54 横条需要短标题"
    steps = [key for view in views for key in IA.issue_view_steps(view["key"])]
    assert steps == IA.issue_step_keys(), "4 视图按序覆盖 6 步：不重不漏"
    assert P.IssuePage.VIEWS is IA.ISSUE_VIEWS
    assert P.IssuePage.STEPS[0]["key"] == "infocheck"  # 步骤表本身未动


def test_issue_routes_still_22_and_six_deep_links():
    routes = IA.all_routes()
    assert len(routes) == 22, "视图只影响呈现，不新增/删除路由"
    assert len(IA.ISSUE_ROUTE_STEPS) == 6
    for step in IA.issue_step_keys():
        assert f"issue:{step}" in routes, f"深链 alias 必须保留：issue:{step}"
        assert IA.normalize_route(f"issue:{step}") == ("issue", step)
    assert "issue:overview" in routes, "旧 landing 别名仍可持久化/深链"


# ── 2. rail：4 行 + 分组标题；窄窗 54 横条 4 段 ────────────────────────
def test_nav_chips_draw_four_views_without_group_headers():
    # 批 117：216 rail 与「正文流水线/收尾（可选）」分组标题整体删除，
    # 换成与「引用库」同款的页内 4 chip。
    page, _root = _build_issue()
    texts = _texts(page._nav_bar())
    assert "正文流水线" not in texts and "收尾（可选）" not in texts
    for view in page.VIEWS:
        assert str(view["title"]) in texts
        assert str(view["caption"]) not in texts, "chip 只有标题，不再放 caption"
    assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in texts), \
        "导航不再显示 01–02/03–04/05/06"
    chips = [ctl for ctl in _walk(page._nav_bar())
             if isinstance(ctl, ft.Container) and ctl.on_click is not None]
    assert len(chips) == 4, "导航只画 4 个可点功能页（不是 6 个 step）"


def test_narrow_nav_wraps_two_rows_and_click_switches_view():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.app._design_width = lambda: 1000  # <1100 → 两行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(72)
    rows = page._nav_box.content.content.controls
    assert len(rows) == 2 and all(isinstance(row, ft.Container) for row in rows)
    chips = page._nav_chips
    assert set(chips) == {"catalog", "produce", "clean", "push"}
    # 点第 2 个 chip → 进入「编译并合并」的展开区块（默认 03）。
    chips["produce"].on_click(SimpleNamespace())
    assert page.selected == "compileall"


def test_rail_active_view_follows_selected_step():
    page = _issue_page()
    page.activate("toc")            # 02 属于「生成目录」
    assert page._view_of_step(page.selected) == "catalog"
    page.activate("merge")          # 04 属于「编译并合并」
    assert page._view_of_step(page.selected) == "produce"
    page.activate("dirpush")
    assert page._view_of_step(page.selected) == "push"


# ── 3. 视图状态聚合 ───────────────────────────────────────────────────
def test_view_state_aggregation_precedence():
    page, _root = _build_issue()
    assert page._view_state("catalog") == "idle"

    page._status["infocheck"] = ASM.StepStatus("done")
    page._status["toc"] = ASM.StepStatus("done")
    assert page._view_state("catalog") == "done"
    assert page._view_tone(page._view_state("catalog")) == "done"

    page._status["toc"] = ASM.StepStatus("warn", exit_code=1)
    assert page._view_state("catalog") == "warn"

    page._status["toc"] = ASM.StepStatus("running")
    assert page._view_state("catalog") == "running"
    assert page._view_tone(page._view_state("catalog")) == "running"

    page._status["infocheck"] = ASM.StepStatus("error", exit_code=2)
    assert page._view_state("catalog") == "error", "error 优先于 warn/running"
    assert page._view_tone(page._view_state("catalog")) == "error"
    assert page._view_tone("cancelled") == "idle", "取消/跳过按灰显示"


# ── 4. 6 条深链分别落到所属视图的两个区块 ─────────────────────────────
def test_six_deep_links_mount_the_owning_view_sections():
    expected = {
        "infocheck": ("catalog", ["infocheck", "toc"]),
        "toc": ("catalog", ["infocheck", "toc"]),
        "compileall": ("produce", ["compileall", "merge"]),
        "merge": ("produce", ["compileall", "merge"]),
        "cleantemp": ("clean", ["cleantemp"]),
        "dirpush": ("push", ["dirpush"]),
    }
    page = _issue_page()
    for step, (view_key, sections) in expected.items():
        page.activate(step)
        assert page.selected == step
        assert page._view_of_step(step) == view_key
        assert list(page._section_refs) == sections, f"{step} 应在 {view_key} 视图内"
        assert page.expanded_step(view_key) == step, "深链进入后展开目标区块"
        assert page._content_box.controls, "详情区必须已重建"


def test_deep_link_expands_target_and_collapses_sibling():
    """4 条视图内深链：目标区块展开、同视图另一块收起（替代 scroll_to）。"""
    page = _issue_page()
    cases = {"infocheck": ("catalog", "toc"), "toc": ("catalog", "infocheck"),
             "compileall": ("produce", "merge"), "merge": ("produce", "compileall")}
    for step, (view_key, sibling) in cases.items():
        page.activate(step)
        assert page.expanded_step(view_key) == step
        assert page._section_bodies[sibling].visible is False, "收起区块内容隐藏"
        assert page._section_bodies[step].visible is True
        assert page._chevrons[step].value == "▾"
        assert page._chevrons[sibling].value == "▸"


def test_compile_done_expands_merge_but_never_runs_it():
    page = _issue_page()
    page.activate("compileall")
    ran: list = []
    page._run_step = lambda key: ran.append(key)  # on_step_finished 不许触发运行
    page.on_step_finished("compileall", 0)
    assert page.expanded_step("produce") == "merge", "03 成功 → 04 展开"
    assert page._expanded["produce"] == "merge"
    assert page._section_bodies["merge"].visible is True
    assert page._section_bodies["compileall"].visible is False
    assert page.selected == "merge", "切到 04，首屏即见（不依赖滚动）"
    assert ran == [], "04 保留独立确认点：不自动运行"
    assert page._state_of("merge") == "idle"

    page._expanded["produce"] = "compileall"
    page.on_step_finished("compileall", 2)  # 失败不切
    page.on_step_finished("toc", 0)         # 别的步骤不自动切（01→02 由用户点）
    assert page._expanded["produce"] == "compileall"


# ── 5. 01/02/03/04/05/06 各有运行入口，且落在所属视图 ──────────────────
def test_every_step_has_its_own_run_entry_in_its_view():
    page = _issue_page()
    for step in IA.issue_step_keys():
        page.activate(step)
        view_key = page._view_of_step(step)
        assert sorted(page._primary_btns) == sorted(page._view_steps(view_key))
        btn = page._primary_btns[step]
        assert isinstance(btn, ft.FilledButton)
        assert str(btn.content) == page._primary_label(step)
        assert btn is page._step_run_primary, "选中区块的主按钮是旧契约的 _step_run_primary"
        # 同一视图内另一个区块的主按钮仍在，且不是同一个控件。
        others = [k for k in page._view_steps(view_key) if k != step]
        for other in others:
            assert page._primary_btns[other] is not btn


def test_view_switch_keeps_current_step_when_already_inside():
    page = _issue_page()
    page.activate("toc")
    page._select_view("catalog")   # 已在该视图 → 不动，保留用户展开状态
    assert page.selected == "toc"
    page._select_view("produce")   # 换视图 → 落到首区块 03（默认展开）
    assert page.selected == "compileall"
    assert page.expanded_step("produce") == "compileall"
    page._select_view("clean")
    assert page.selected == "cleantemp"


def test_view_switch_remembers_last_expanded_section():
    page = _issue_page()
    page.activate("merge")          # produce 最近展开 04
    page._select_view("catalog")    # 切走
    page._select_view("produce")    # 回来 → 仍展开 04，不是首块 03
    assert page.selected == "merge"
    assert page.expanded_step("produce") == "merge"
    assert page._section_bodies["compileall"].visible is False


# ── 6. 04 只读「前置页」卡 ────────────────────────────────────────────
def test_prep_card_empty_state_is_readonly():
    page = _issue_page()
    page.activate("merge")
    card = page._prep_card()
    labels = _texts(card)
    assert "前置页" in labels
    assert "只读 · 不提供编辑" in labels
    assert "还没检测前置页" in labels
    assert not [ctl for ctl in _walk(card) if isinstance(ctl, ft.TextField)], \
        "前置页卡是只读的，不放编辑表单"
    buttons = [str(getattr(ctl, "content", "")) for ctl in _walk(card)]
    assert any("检测前置页" in label for label in buttons)
    assert all("编辑" not in label for label in buttons)


def test_prep_card_renders_detect_payload_and_missing_rows():
    page = _issue_page()
    page.activate("merge")
    card = page._prep_card()
    page.set_prep_pages({
        "editorsPage": {"fileName": "编委页.pdf", "pageCount": 2},
        "backCover": None,
        "coverPages": 1,
    })
    labels = _texts(card)
    assert "扉页.pdf · 1 页" in labels, "coverPages 是页数整数"
    assert "编委页.pdf · 2 页" in labels
    assert "未检测到" in labels, "缺失的前置页要显式写出来"
    assert "已找到" in labels and "缺失" in labels
    assert not [ctl for ctl in _walk(card) if isinstance(ctl, ft.TextField)]


def test_prep_card_absent_from_compile_section_but_present_in_merge():
    page = _issue_page()
    page.activate("compileall")
    assert "前置页" not in _texts(page._section_refs["compileall"])
    assert page._section_bodies["merge"].visible is False, "收起区块隐藏（前置页卡不可见）"
    page.activate("merge")
    assert "前置页" in _texts(page._section_refs["merge"])
    assert "前置页" not in _texts(page._section_refs["compileall"])


def test_detect_task_feeds_prep_card(monkeypatch):
    page = _issue_page()
    page.activate("merge")
    page.app.notes_repo = None  # 假壳没有笔记仓；_detect_task 会传给引擎桥
    payload = {"editorsPage": {"fileName": "编委页.pdf", "pageCount": 3},
               "backCover": {"fileName": "封底.pdf", "pageCount": 1},
               "coverPages": 2}

    async def _fake(*args, **kwargs):
        return {"ok": True, "json": payload}

    monkeypatch.setattr(ASM.asyncio, "to_thread", _fake)
    ASM.asyncio.run(ASM._detect_task(page, object()))
    assert page._prep_pages == payload
    labels = _texts(page._prep_box)
    assert "封底.pdf · 1 页" in labels


# ── 7. 页头 / 运行整期 / 日志 dock / 键盘不变 ──────────────────────────
def test_run_all_dialog_still_defaults_to_steps_one_to_four():
    page = _issue_page()
    shown: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page._open_runall()
    assert len(shown) == 1
    boxes = [ctl for ctl in _walk(shown[0].content) if isinstance(ctl, ft.Checkbox)]
    assert len(boxes) == 7, "01–06 + 跳过已完成（本次未改任何步骤）"
    assert [box.value for box in boxes[:4]] == [True] * 4
    assert boxes[4].value is False and boxes[5].value is False
    assert page._runall_danger.visible is False
    boxes[4].value = True
    boxes[4].on_change(SimpleNamespace(control=boxes[4]))
    assert page._runall_danger.visible is True


def test_header_run_all_log_dock_and_keyboard_unchanged():
    page = _issue_page()
    assert str(page._run_all_btn.content) == "运行整期…"
    assert page._log.dock is True and page.LOG_LIMIT == 2000
    page.activate("infocheck")
    # 批 117：⌘1–4 切四个功能页（不再对应 6 个 step），⌘5/6 仍落到清理/推送页。
    for index, view in enumerate(page.VIEWS, start=1):
        page._on_key(SimpleNamespace(key=str(index), ctrl=True, meta=False))
        assert page._view_of_step(page.selected) == view["key"], \
            f"⌘{index} 应进「{view['title']}」"
        assert page.expanded_step(view["key"]) in view["steps"]
    page._on_key(SimpleNamespace(key="5", ctrl=True, meta=False))
    assert page.selected == "cleantemp"
    page._on_key(SimpleNamespace(key="6", ctrl=True, meta=False))
    assert page.selected == "dirpush"
    assert page.expanded_step("push") == "dirpush"


def test_help_text_describes_four_views_not_six_steps():
    entry = next(item for item in HC.ENTRIES if item[0] == "整期装配")
    text = entry[2]
    assert "六步按序跑" not in text
    for title in ("生成目录", "编译并合并", "清理临时文件", "生成推送文案"):
        assert title in text
