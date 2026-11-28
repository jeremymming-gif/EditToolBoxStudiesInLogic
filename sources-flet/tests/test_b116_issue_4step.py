"""批 118 D7：整期装配页内 6 页签工作台（承接批 116 E′ 的四区块设计）。

锁住的不变量（壳层，不建窗口、不跑引擎）：
1. rail 6 视图（宽窗 216 栏 / 内容 <880 时 54 高横条），标签、caption、选中态；
2. 视图状态聚合：任一步 error→红 / 有警告→琥珀 / running→动 / 全完成→绿 / 否则灰；
3. 7 条 `issue:<step>` 深链 alias 全保留；设置新增九路由后共 32 条；深链落到所属视图的区块；
4. 同一视图内的区块同屏但**各自有运行入口**（不合并引擎调用、不合并确认）；
5. 合并页（05）只读「前置页」卡：空态说明 + detect 结果（文件名/页数/缺失），不提供编辑；
6. D3 起没有「编译成功自动展开/自动运行」：任一步成功后都不切视图、不跑下一步；
7. 没有「运行整期…」入口（run_all 已下线）；日志 dock 与 ⌘/Ctrl+1–6 切六页签不变。
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


# ── 1. 6 个视图：标签 / caption / 步骤归属 ────────────────────────────
def test_issue_views_are_six_with_labels_and_captions():
    views = IA.issue_views()
    assert [view["key"] for view in views] == [
        "catalog", "order", "refresh", "merge", "clean", "push"]
    assert [view["title"] for view in views] == [
        "生成目录", "排序与改名", "刷新页码", "合并",
        "清理临时文件", "生成推送文案"]
    assert [view["caption"] for view in views] == [
        "检查 tex 信息 · 生成目录",
        "读取候选 · 预览改名",
        "预览后确认刷新",
        "合并 PDF",
        "删除临时文件（移入回收站）",
        "生成目录摘要 docx",
    ]
    # 批 116 ISSUE4-D：rail 不再画序号，`range` 字段已删；caption 仍不复述标题。
    for view in views:
        caption = str(view["caption"])
        assert not caption.startswith(("01", "02", "03", "04", "05", "06")), view["key"]
        assert caption != str(view["title"]), view["key"]
        assert "range" not in view, f"{view['key']} 不再携带序号数据"
        assert view.get("short"), "窄窗 54 横条需要短标题"
    steps = [key for view in views for key in IA.issue_view_steps(view["key"])]
    assert steps == IA.issue_step_keys(), "6 视图按序覆盖 7 步：不重不漏"
    assert P.IssuePage.VIEWS is IA.ISSUE_VIEWS
    assert P.IssuePage.STEPS[0]["key"] == "infocheck"  # 步骤表本身未动


def test_issue_routes_still_32_and_seven_deep_links():
    routes = IA.all_routes()
    assert len(routes) == 32, "仅设置增加九个详情路由，其余深链保持不变"
    assert len(IA.ISSUE_ROUTE_STEPS) == 7
    for step in IA.issue_step_keys():
        assert f"issue:{step}" in routes, f"深链 alias 必须保留：issue:{step}"
        assert IA.normalize_route(f"issue:{step}") == ("issue", step)
    assert "issue:overview" in routes, "旧 landing 别名仍可持久化/深链"


# ── 2. 页内 chips：6 个功能页、无分组标题 ─────────────────────────────
def test_nav_chips_draw_six_views_without_group_headers():
    # 批 117 起用与「引用库」同款的页内 chip；D7 扩到 6 个功能页。
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
    assert len(chips) == 6, "导航只画 6 个可点功能页（不是 7 个 step）"


def test_narrow_nav_wraps_two_rows_and_click_switches_view():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.app._design_width = lambda: 1000  # <1100 → 两行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(72)
    rows = page._nav_box.content.content.controls
    assert len(rows) == 2 and all(isinstance(row, ft.Container) for row in rows)
    chips = page._nav_chips
    assert set(chips) == {"catalog", "order", "refresh", "merge", "clean", "push"}
    # 点「刷新页码」chip → 进入该功能页（单区块视图，默认展开自身）。
    chips["refresh"].on_click(SimpleNamespace())
    assert page.selected == "refresh"


def test_rail_active_view_follows_selected_step():
    page = _issue_page()
    page.activate("toc")            # 02 属于「生成目录」
    assert page._view_of_step(page.selected) == "catalog"
    page.activate("merge")          # 05 属于「合并」
    assert page._view_of_step(page.selected) == "merge"
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


# ── 4. 7 条深链分别落到所属视图的区块 ─────────────────────────────────
def test_seven_deep_links_mount_the_owning_view_sections():
    expected = {
        "infocheck": ("catalog", ["infocheck", "toc"]),
        "toc": ("catalog", ["infocheck", "toc"]),
        "order": ("order", ["order"]),
        "refresh": ("refresh", ["refresh"]),
        "merge": ("merge", ["merge"]),
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
    """多区块视图内的深链：目标区块展开、同视图另一块收起（替代 scroll_to）。"""
    page = _issue_page()
    cases = {"infocheck": ("catalog", "toc"), "toc": ("catalog", "infocheck")}
    for step, (view_key, sibling) in cases.items():
        page.activate(step)
        assert page.expanded_step(view_key) == step
        assert page._section_bodies[sibling].visible is False, "收起区块内容隐藏"
        assert page._section_bodies[step].visible is True
        assert page._chevrons[step].value == "▾"
        assert page._chevrons[sibling].value == "▸"


def test_step_finish_never_auto_switches_or_runs():
    """D3：刷新页码不再「成功后自动展开合并」——页签各管一个动作，不自动跳。"""
    page = _issue_page()
    page.activate("refresh")
    ran: list = []
    page._run_step = lambda key: ran.append(key)  # on_step_finished 不许触发运行
    page.on_step_finished("refresh", 0)
    assert page.expanded_step("refresh") == "refresh"
    assert page.selected == "refresh", "成功后停在原页签"
    assert ran == [], "任何页签都不自动运行下一步"
    assert page._state_of("merge") == "idle"

    page.on_step_finished("refresh", 2)  # 失败同样不切
    page.on_step_finished("merge", 0)    # 别的步骤也不自动切
    assert page.expanded_step("refresh") == "refresh"
    assert page.selected == "refresh"


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
    page._select_view("refresh")   # 换视图 → 落到该视图默认区块
    assert page.selected == "refresh"
    assert page.expanded_step("refresh") == "refresh"
    page._select_view("clean")
    assert page.selected == "cleantemp"


def test_view_switch_remembers_last_expanded_section():
    page = _issue_page()
    page.activate("toc")            # catalog 最近展开 02
    page._select_view("merge")      # 切走
    page._select_view("catalog")    # 回来 → 仍展开 02，不是首块 01
    assert page.selected == "toc"
    assert page.expanded_step("catalog") == "toc"
    assert page._section_bodies["infocheck"].visible is False


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


def test_prep_card_absent_from_refresh_section_but_present_in_merge():
    page = _issue_page()
    page.activate("refresh")
    assert "前置页" not in _texts(page._section_refs["refresh"])
    page.activate("merge")
    assert "前置页" in _texts(page._section_refs["merge"])
    assert "前置页" in _texts(page._section_refs["merge"]), "前置页卡只在合并页出现"


def test_detect_entry_restored_and_prep_card_stays_readonly():
    """D3 未下线检测前置页；入口在合并页的前置页卡内，只读不编辑。

    D3 的删除清单只有 run_all / 自动编译 / demo / 三个只预演开关，
    「检测前置页」是前置页卡的探测动作，必须留在合并页；
    其结果只落只读卡（`set_prep_pages`），不触发合并、不提供编辑。
    """
    page = _issue_page()
    page.activate("merge")
    assert hasattr(ASM, "_detect_task"), "检测任务必须存在（D3 不含此项）"
    assert hasattr(ASM, "run_detect"), "检测入口必须存在（D3 不含此项）"
    page.set_prep_pages({"editorsPage": {"fileName": "编委页.pdf", "pageCount": 3},
                         "backCover": {"fileName": "封底.pdf", "pageCount": 1},
                         "coverPages": 2})
    labels = _texts(page._prep_box)
    assert "封底.pdf · 1 页" in labels

    # 合并页有且只有一个检测入口，且整张卡只读（无输入控件）。
    section = page._section_refs["merge"]
    detect_buttons = [
        ctl for ctl in _walk(section)
        if isinstance(ctl, ft.OutlinedButton)
        and "检测前置页" in str(getattr(ctl, "content", "") or getattr(ctl, "label", ""))
    ]
    assert len(detect_buttons) == 1, f"合并页应只有一个检测入口：{detect_buttons}"
    # 只读性只针对「前置页」卡本身（合并页参数区仍有输出路径等输入框）。
    card = page._prep_card()
    assert not [ctl for ctl in _walk(card) if isinstance(ctl, ft.TextField)], \
        "前置页卡不放编辑表单"


# ── 7. 页头 / 日志 dock / 键盘不变；D3 无整期串跑入口 ──────────────────
def test_run_all_dialog_is_gone_after_d3():
    page = _issue_page()
    assert not hasattr(page, "_open_runall"), "D3：界面不再有「运行整期」对话框"
    assert not hasattr(page, "_run_all")
    assert not hasattr(P.IssuePage, "_run_all")


def test_header_run_all_log_dock_and_keyboard_unchanged():
    page = _issue_page()
    # 批 120 B-354：页头两按钮删除；键盘与日志 dock 入口不变。
    assert not hasattr(page, ("_run_" + "all_btn"))
    assert not hasattr(page, ("_header_" + "actions"))
    assert page._log.dock is True and page.LOG_LIMIT == 2000
    page.activate("infocheck")
    # D7：⌘1–6 切六个功能页（不再对应 7 个 step）。
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


def test_help_text_entry_matches_six_tabs():
    """帮助页「整期装配」条目必须与 D7 的 6 页签一致（写域内，锁新文案）。

    D7 落地后页内是 6 个页签，「刷新页码」是唯一的正文编译动作
    （生成目录页取消「仅生成 tex」时会另编译目录 PDF）；帮助文案
    不能再写旧口径「4 个功能页 … 编译并合并」。
    """
    entry = next(item for item in HC.ENTRIES if item[0] == "整期装配")
    text = entry[2]
    assert "六个页签" in text, text
    assert "刷新页码" in text, text
    assert "4 个功能页" not in text, text
    assert "六步按序跑" not in text


# ── 8. C14：单步执行器仍是 async（run_task 只接受 coroutine function）───────
def test_step_sequence_entry_is_async():
    """真机 C14：`page.run_task(ASM.run_all, ...)` 对同步函数抛 TypeError。

    D3 删掉整期入口后，页面只剩 `_run_step` → `_run_calls` → `ASM._run_sequence`
    这条链；这里锁住链条末端仍是 coroutine function。
    """
    import inspect

    assert inspect.iscoroutinefunction(ASM._run_sequence)
    assert not hasattr(ASM, "run_all"), "D3：整期串跑入口已下线"
