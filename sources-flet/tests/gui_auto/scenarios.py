"""GUI 自动走查场景（S1–S10）与控件树断言（A 类）。

每个 ``sN_*`` 函数都是一个独立场景：自己启动真实进程、自己断言、自己关闭。
pytest 入口在 test_gui_auto.py；本模块也可被单独 import 做手工调试。
"""
from __future__ import annotations

import time
from pathlib import Path

import flet as ft

from ui import components as C
from ui import tokens as T

from . import harness as H

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLOW_LABELS = ["初排转换", "一校核查"]  # 单篇页两 flow（checkbib 已迁「引用库 → 规范化」）
BIB_VIEWS = ["overview", "query", "normalize", "import", "review",
             "edit", "dupes", "fieldfix", "health", "settings"]
BIB_LABELS = ["概况", "查库", "规范化", "入藏", "待处理",
              "编辑", "查重合并", "字段归位", "库健康", "设置"]
BIB_GROUPS = ["read", "intake", "treat", "maint"]
BIB_GROUP_LABELS = ["查阅", "采录", "治理", "维护"]
BIB_GROUP_VIEWS = {
    "read": ("overview", "query"),
    "intake": ("normalize", "import"),
    "treat": ("review", "edit", "dupes", "fieldfix"),
    "maint": ("health", "settings"),
}
BIB_VIEW_GROUP = {view: group for group, views in BIB_GROUP_VIEWS.items() for view in views}
NAV_SEQUENCE = ["home", "single", "issue", "bib", "settings", "help"]


# ── 公共小工具 ─────────────────────────────────────────────────────────────
def ensure_fixtures() -> dict[str, str]:
    """在 gui_auto/fixtures 下生成确定性的演示文件（不触碰任何真实数据）。"""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    src = FIXTURES / "demo_single.tex"
    if not src.exists():
        src.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "GUI 自动走查夹具（不含真实稿件内容）。\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
    out = FIXTURES / "out"
    out.mkdir(exist_ok=True)
    return {"source": str(src), "out": str(out)}


def _click_until_event(session: H.AppSession, dip: tuple[float, float], event: str,
                       pred=None, *, timeout: float = 3.0,
                       offsets=((0, 0), (0, 6), (0, -6), (6, 0), (-6, 0))) -> dict:
    base_after = sum(1 for e in session.events() if e.get("event") == event)
    last_error: Exception | None = None
    for dx, dy in offsets:
        session.click_dip(dip[0] + dx, dip[1] + dy)
        try:
            return session.wait_event(event, pred, timeout=timeout, after=base_after)
        except AssertionError as exc:  # noqa: PERF203
            last_error = exc
    raise AssertionError(f"点击 {dip} 未能触发事件 {event}: {last_error}")


def _chip_region(session: H.AppSession, img) -> tuple[int, int, int, int]:
    """二级胶囊行：bib 页在组条下一行（y≈143.5），single 页仍是一行（y≈96.7）。"""
    bx, by = session.border
    width, _height = img.size
    left = int(round(bx + 236 * session.scale))
    top_dip, bottom_dip = (118, 176) if session.page == "bib" else (70, 130)
    top = int(round(by + top_dip * session.scale))
    bottom = int(round(by + bottom_dip * session.scale))
    return (left, top, width, bottom)


def _group_region(session: H.AppSession, img) -> tuple[int, int, int, int]:
    """一级组条行（只在 bib 页）：y≈103.5，取 accent 下划线/文字色做选中判定。"""
    bx, by = session.border
    width, _height = img.size
    left = int(round(bx + 236 * session.scale))
    top = int(round(by + 70 * session.scale))
    bottom = int(round(by + 126 * session.scale))
    return (left, top, width, bottom)


def _active_chip_center(session: H.AppSession, img):
    return H.active_row_center(img, H.ACCENT_SOFT, _chip_region(session, img), min_count=20)


def _active_group_center(session: H.AppSession, img):
    # 组条选中态是 accent 文字 + 2px 下划线（宽约 26DIP），min_count 取小值。
    return H.active_row_center(img, H.ACCENT, _group_region(session, img), min_count=10)


def _click_chip(session: H.AppSession, dip: tuple[float, float],
                expected_x_dip: float | None = None) -> object:
    """点击 chip 并等画面稳定；用 accent_soft 高亮中心校验是否真的切过去。"""
    last = None
    for dx, dy in ((0, 0), (0, 6), (0, -6), (6, 0), (-6, 0)):
        session.click_dip(dip[0] + dx, dip[1] + dy)
        img = session.wait_stable(timeout=2.5)
        center = _active_chip_center(session, img)
        if center is None:
            continue
        last = img
        if expected_x_dip is None:
            return img
        actual_dip = (center[0] - session.border[0]) / session.scale
        if abs(actual_dip - expected_x_dip) <= 12:
            return img
    if last is not None:
        return last
    raise AssertionError(f"点击 chip {dip} 后未看到 accent_soft 高亮")


def nav_click(session: H.AppSession, key: str) -> dict:
    event = _click_until_event(
        session, (H.NAV_X, H.NAV_Y[key]), "nav_change",
        lambda e: e.get("page") == key, timeout=3.0,
    )
    session.settle(0.5)
    return event


def _sample(session: H.AppSession, img, dip: tuple[float, float]):
    bx, by = session.border
    x = int(round(bx + dip[0] * session.scale))
    y = int(round(by + dip[1] * session.scale))
    return img.load()[x, y]


# ── A 类：控件树 / 字重断言（不需要窗口）──────────────────────────────────
def structural_checks() -> list[dict]:
    t = T.Tokens(theme="light", zoom=1.0, dpi=H.system_dpi())
    rows: list[dict] = []

    def add(name: str, actual, expected, note: str = "") -> None:
        rows.append({
            "check": name,
            "actual": str(actual),
            "expected": str(expected),
            "ok": actual == expected,
            "note": note,
        })

    section = C.section_title("输入", t)
    add("分区标题「输入」字重", section.controls[0].weight, ft.FontWeight.W_600)
    add("分区标题「输入」字号", section.controls[0].size, t.font("h3"))

    option = C.option_row("编译 PDF", "说明", t, icon="PICTURE_AS_PDF",
                          checked=True, badge_label="主要")
    label = option.controls[1].controls[0].controls[0]
    # 批 112 S1：产品侧 item-title=W600（option_row 标题走 role="item-title"，h3/semibold），
    # 旧断言 W500 是历史遗留；Windows 复核时以本断言为准。
    add("选项名「编译 PDF」字重", label.weight, ft.FontWeight.W_600)
    add("选项名「编译 PDF」字号", label.size, t.font("h3"))

    active = C.nav_item("HOME", "工作台", t, active=True, on_click=lambda e: None)
    inactive = C.nav_item("HOME", "工作台", t, active=False, on_click=lambda e: None)
    add("侧栏选中项字重", active.content.controls[1].weight, ft.FontWeight.W_500,
        "源码有意的选中加粗（Medium vs Regular），不是缺陷")
    add("侧栏未选中项字重", inactive.content.controls[1].weight, ft.FontWeight.W_400)

    console = C.LogConsole(t, height=150, title="运行日志")
    toolbar = console.root.content.controls[0]
    add("「运行日志」标题字重", toolbar.controls[1].weight, ft.FontWeight.W_600,
        "与分区标题同字重，排版一致")
    add("「运行日志」标题字号", toolbar.controls[1].size, t.font("h3"))
    return rows


def assert_structural() -> list[dict]:
    rows = structural_checks()
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise AssertionError(f"A 类字重/字号断言失败: {bad}")
    return rows


# ── S1 启动冒烟（B + C + E）──────────────────────────────────────────────
def s1_launch(ctx: H.RunContext) -> dict:
    with ctx.session("s1", page="home") as session:
        img = session.screenshot("s1_launch_home")
        events = session.event_names()
        must = ["process_start", "shell_built", "visible", "resize", "engine_status"]
        missing = [name for name in must if name not in events]
        H.expect(not missing, f"S1 缺少启动标记: {missing}；events={events}")
        H.expect(session.window is not None and H.APP_TITLE in session.window.title,
                 f"S1 窗口标题不应为 {getattr(session.window, 'title', None)!r}")
        H.expect(session.engine_ok(), f"S1 引擎状态不是就绪: {session.last_event('engine_status')}")
        H.expect(1080 <= img.size[0] <= 4000 and 700 <= img.size[1] <= 2600,
                 f"S1 窗口截图尺寸异常: {img.size}")
        expected_scale = session.dpi / 96.0
        H.expect(abs(session.scale - expected_scale) < 0.35,
                 f"S1 client/page 比例 {session.scale:.3f} 与 DPI {session.dpi} 不符")
        # 无错误日志、无平台错误标记（E）
        H.expect("Traceback (most recent call last)" not in session.stdout_text(),
                 "S1 stdout 出现 Traceback")
        fatal = [e for e in session.events() if e.get("event") in H.FATAL_EVENTS]
        H.expect(not fatal, f"S1 出现错误标记: {fatal[-2:]}")
        state = session.last_event("state") or {}
        info = {
            "window_title": session.window.title,
            "hwnd": session.window.hwnd,
            "frame": session.frame,
            "client": session.client_size,
            "page_size_dip": session.page_size,
            "scale": round(session.scale, 4),
            "dpi": session.dpi,
            "engine_ok": session.engine_ok(),
            "engine": (session.last_event("engine_status") or {}).get("version"),
            "flet": state.get("flet"),
            "python": state.get("python"),
            "screenshot": img.size,
            "screenshot_name": f"s1_launch_home__win{img.size[0]}x{img.size[1]}"
                               f"_client{session.client_size[0]}x{session.client_size[1]}_dpi{session.dpi}.png",
            "events": events,
        }
        ctx.record("S1_launch", **info)
        return info


# ── S2 六页导航（B + C + D）──────────────────────────────────────────────
def s2_navigation(ctx: H.RunContext) -> dict:
    results = []
    with ctx.session("s2", page="home") as session:
        for key in NAV_SEQUENCE:
            if key != "home":
                nav_click(session, key)
            img = session.screenshot(f"s2_page_{key}")
            width, height = img.size
            sidebar = H.regions_for(session, width, height)["sidebar"]
            center = H.active_row_center(img, H.ACCENT_SOFT, sidebar)
            H.expect(center is not None, f"S2 页面 {key} 侧栏没有选中高亮")
            actual_dip = (center[1] - session.border[1]) / session.scale
            H.expect(abs(actual_dip - H.NAV_Y[key]) <= 10,
                     f"S2 页面 {key} 选中项 y={actual_dip:.1f}DIP 与期望 "
                     f"{H.NAV_Y[key]} 偏差过大")
            if key == "bib":
                stats = session.last_event("bib_stats")
                H.expect(stats is not None, "S2 引用库页未拿到 bib_stats 标记")
                ctx.record("S2_bib_stats", **{k: stats.get(k) for k in
                                              ("ok", "records", "aliases_total",
                                               "conflicts", "bytes")})
            results.append({"page": key, "active_y_dip": round(actual_dip, 1)})
        ctx.record("S2_navigation", pages=results,
                   nav_events=[e for e in session.events() if e.get("event") == "nav_change"])
        return {"pages": results}


# ── S3 深色模式（D + C）──────────────────────────────────────────────────
def s3_theme(ctx: H.RunContext) -> dict:
    with ctx.session("s3", page="home") as session:
        light = session.screenshot("s3_theme_light")
        light_bg = _sample(session, light, (700, 760))
        light_sidebar = _sample(session, light, (100, 760))
        H.expect(light_bg == H.LIGHT_BG or all(abs(a - b) <= 3 for a, b in zip(light_bg, H.LIGHT_BG)),
                 f"S3 浅色内容背景色异常: {light_bg}")
        session.click_dip(*H.SWITCH)
        dark_ev = session.wait_event("theme_changed", lambda e: e.get("theme") == "dark", timeout=10)
        session.wait_event("titlebar", lambda e: e.get("dark") is True, timeout=10)
        session.settle(2.8)  # toast 2.2s 后才消失
        dark = session.screenshot("s3_theme_dark")
        dark_bg = _sample(session, dark, (700, 760))
        dark_sidebar = _sample(session, dark, (100, 760))
        H.expect(all(abs(a - b) <= 4 for a, b in zip(dark_bg, H.DARK_BG)),
                 f"S3 深色内容背景色异常: {dark_bg}")
        H.expect(all(abs(a - b) <= 4 for a, b in zip(dark_sidebar, H.DARK_SIDEBAR)),
                 f"S3 深色侧栏背景色异常: {dark_sidebar}")
        H.expect(dark_bg != light_bg and dark_sidebar != light_sidebar,
                 "S3 切换后像素颜色没有变化")
        session.click_dip(*H.SWITCH)
        session.wait_event("theme_changed", lambda e: e.get("theme") == "light", timeout=10)
        session.settle(2.8)
        back = session.screenshot("s3_theme_back_light")
        again = H.diff_stats(light, back)
        H.expect(again["mean"] <= 0.2 and again["pct"] <= 0.05,
                 f"S3 切回浅色后与初始浅色不一致: {again}")
        info = {
            "light_bg": light_bg, "light_sidebar": light_sidebar,
            "dark_bg": dark_bg, "dark_sidebar": dark_sidebar,
            "theme_event": dark_ev.get("theme"),
            "back_mean": again["mean"], "back_pct": again["pct"],
        }
        ctx.record("S3_theme", **info)
        return info


# ── S4 单篇页两步骤 + 跳转规范化（A + C + D）────────────────────────────
def s4_single_flows(ctx: H.RunContext) -> dict:
    fixtures = ensure_fixtures()
    env = {
        "P2_DEMO_FLOW": "convert",
        "P2_DEMO_SINGLE_SOURCE": fixtures["source"],
        "P2_DEMO_SINGLE_OUT": fixtures["out"],
    }
    with ctx.session("s4", page="single", extra_env=env) as session:
        session.settle(0.8)
        names = ["convert", "proofcheck"]
        rows: list[dict] = []
        imgs: dict[str, object] = {}
        img = session.screenshot("s4_flow_convert")
        center = _active_chip_center(session, img)
        H.expect(center is not None, "S4 初排转换 chip 未高亮")
        imgs["convert"] = img
        rows.append({"flow": FLOW_LABELS[0], "center_x_dip": round(
            (center[0] - session.border[0]) / session.scale, 1)})
        for index in (1,):
            label = FLOW_LABELS[index]
            img = _click_chip(session, H.SINGLE_CHIPS[index], H.SINGLE_CHIPS[index][0])
            center = _active_chip_center(session, img)
            H.expect(center is not None, f"S4 chip「{label}」未高亮")
            actual_x = (center[0] - session.border[0]) / session.scale
            H.expect(abs(actual_x - H.SINGLE_CHIPS[index][0]) <= 12,
                     f"S4 chip「{label}」中心 x={actual_x:.1f}DIP 与期望 "
                     f"{H.SINGLE_CHIPS[index][0]} 偏差过大")
            img = session.screenshot(f"s4_flow_{names[index]}")
            imgs[names[index]] = img
            rows.append({"flow": label, "center_x_dip": round(actual_x, 1)})
        H.expect(len(rows) == len(FLOW_LABELS) == 2,
                 f"S4 单篇页应只剩 2 个步骤胶囊，实际 {len(rows)}")
        # 两个步骤的内容区必须真的不同（高亮 chip 只占一小块）
        width, height = img.size
        content = H.regions_for(session, width, height)["content"]
        left, top, right, bottom = content
        stats = H.diff_stats(imgs["convert"].crop((left, top, right, bottom)),
                             imgs["proofcheck"].crop((left, top, right, bottom)))
        H.expect(stats["mean"] > 0.3,
                 f"S4 切换 convert→proofcheck 后内容区几乎没变化: {stats}")
        # 跳转按钮：单篇页保留入口，点击后落到「引用库 → 规范化」
        jump = _click_until_event(
            session, H.JUMP_BTN, "bib_normalize_jump", timeout=4.0,
            offsets=((0, 0), (0, 6), (0, -6), (-8, 0), (8, 0),
                     (-24, 0), (24, 0), (-45, 0), (45, 0)))
        view_ev = session.wait_event(
            "bib_view",
            lambda e: e.get("view") == "normalize" and e.get("source") == "single",
            timeout=8.0)
        session.settle(0.8)
        session.screenshot("s4_jump_normalize")
        render_err = session.last_event("bib_render_error")
        H.expect(render_err is None, f"S4 跳转规范化后渲染报错: {render_err}")
        info = {"flows": rows, "jump": jump, "bib_view": view_ev}
        ctx.record("S4_single_flows", **info)
        return info


# ── S5 引用库 10 视图 / 4 组（B + C + D）────────────────────────────────
def s5_bib_views(ctx: H.RunContext) -> dict:
    """10 视图全覆盖 + 两级导航高亮。

    坐标只校验 2 点（组条 1 点 + 胶囊 1 点，`±12 DIP`）；其余视图一律用
    ``P2_BIB_VIEW`` 启动（不点坐标），避免窗口缩放/主题差异把坐标走查拖红。
    """
    rows: list[dict] = []
    with ctx.session("s5", page="bib") as session:
        session.wait_event("bib_stats", timeout=30)
        session.settle(0.6)
        # ① 组条：点「采录」，应进组内首项「规范化」
        group = "intake"
        group_ev = _click_until_event(
            session, H.bib_group_pos(group), "bib_view",
            lambda e: e.get("group") == group, timeout=4.0,
            offsets=((0, 0), (0, 5), (0, -5), (5, 0), (-5, 0)))
        session.settle(0.6)
        img = session.screenshot("s5_group_intake")
        center = _active_group_center(session, img)
        H.expect(center is not None, "S5 组条「采录」未出现选中态（accent 下划线）")
        actual_x = (center[0] - session.border[0]) / session.scale
        H.expect(abs(actual_x - H.bib_group_pos(group)[0]) <= 12,
                 f"S5 组条「采录」高亮中心 x={actual_x:.1f}DIP 与点击坐标 "
                 f"{H.bib_group_pos(group)[0]} 偏差 >12DIP")
        rows.append({"kind": "group", "group": group, "view": group_ev.get("view"),
                     "source": "click", "center_x_dip": round(actual_x, 1)})
        # ② 胶囊：点「入藏」，应切到 import（与组默认视图不同，能区分点错行）
        view = "import"
        view_ev = _click_until_event(
            session, H.bib_chip_pos(view), "bib_view",
            lambda e: e.get("view") == view, timeout=4.0,
            offsets=((0, 0), (0, 5), (0, -5), (5, 0), (-5, 0)))
        session.settle(0.6)
        img = session.screenshot("s5_view_import_click")
        center = _active_chip_center(session, img)
        H.expect(center is not None, "S5 胶囊「入藏」未出现选中态")
        actual_x = (center[0] - session.border[0]) / session.scale
        H.expect(abs(actual_x - H.bib_chip_pos(view)[0]) <= 12,
                 f"S5 胶囊「入藏」高亮中心 x={actual_x:.1f}DIP 与点击坐标 "
                 f"{H.bib_chip_pos(view)[0]} 偏差 >12DIP")
        H.expect(not session.has_event("bib_render_error"),
                 f"S5 视图「入藏」渲染报错: {session.last_event('bib_render_error')}")
        rows.append({"kind": "chip", "group": BIB_VIEW_GROUP[view], "view": view,
                     "source": "click", "center_x_dip": round(actual_x, 1)})
    # ③ 其余 9 个视图（含规范化）：P2_BIB_VIEW 启动，零坐标
    for view, label in zip(BIB_VIEWS, BIB_LABELS):
        with ctx.session(f"s5_{view}", page="bib",
                         extra_env={"P2_BIB_VIEW": view}) as session:
            ev = session.wait_event(
                "bib_view",
                lambda e, wanted=view: e.get("view") == wanted, timeout=30.0)
            H.expect(ev.get("source") == "env",
                     f"S5「{label}」不是 P2_BIB_VIEW 钩子进入: {ev}")
            H.expect(ev.get("group") == BIB_VIEW_GROUP[view],
                     f"S5「{label}」组别错误: {ev} 期望 {BIB_VIEW_GROUP[view]}")
            session.settle(0.8)
            img = session.screenshot(f"s5_view_{view}")
            # 10/10 高亮：启动即该视图，用色块几何中心校验（免点击、免坐标误差）
            center = _active_chip_center(session, img)
            H.expect(center is not None, f"S5 视图「{label}」胶囊未高亮")
            actual_x = (center[0] - session.border[0]) / session.scale
            H.expect(abs(actual_x - H.bib_chip_pos(view)[0]) <= 12,
                     f"S5 视图「{label}」胶囊高亮 x={actual_x:.1f}DIP 与 IA 序 "
                     f"{H.bib_chip_pos(view)[0]} 偏差 >12DIP")
            group = BIB_VIEW_GROUP[view]
            gcenter = _active_group_center(session, img)
            H.expect(gcenter is not None, f"S5 视图「{label}」所属组条未高亮")
            gactual = (gcenter[0] - session.border[0]) / session.scale
            H.expect(abs(gactual - H.bib_group_pos(group)[0]) <= 12,
                     f"S5 视图「{label}」组条高亮 x={gactual:.1f}DIP 与组序 "
                     f"{H.bib_group_pos(group)[0]} 偏差 >12DIP")
            err = session.last_event("bib_render_error")
            H.expect(err is None, f"S5 视图「{label}」渲染报错: {err}")
            rows.append({"kind": "view", "group": group, "view": view,
                         "label": label, "source": "env",
                         "center_x_dip": round(actual_x, 1),
                         "group_x_dip": round(gactual, 1),
                         "bib_view_event": ev})
    ctx.record("S5_bib_views", views=rows)
    return {"views": rows}


# ── S10 「引用库 → 规范化」P2_BIB_VIEW 启动（B + D）─────────────────────
def s10_bib_normalize_env(ctx: H.RunContext) -> dict:
    """P2_BIB_VIEW=normalize 启动断言：视图/组别正确、无渲染报错、面板标题就位。"""
    from ui import normalize as N
    from ui import pages as P

    H.expect(dict(P.BibPage.VIEWS).get("normalize") == "规范化",
             "S10 IA 里 normalize 的标签不是「规范化」")
    H.expect(P.BibPage.group_of("normalize") == "intake",
             "S10 normalize 不在采录组")
    H.expect(N.PAGE_TITLE == "参考文献规范化", "S10 规范化面板标题变了")
    with ctx.session("s10", page="bib",
                     extra_env={"P2_BIB_VIEW": "normalize"}) as session:
        ev = session.wait_event(
            "bib_view", lambda e: e.get("view") == "normalize", timeout=30.0)
        H.expect(ev.get("source") == "env", f"S10 不是 P2_BIB_VIEW 进入: {ev}")
        H.expect(ev.get("group") == "intake", f"S10 组别错误: {ev}")
        session.settle(0.8)
        session.screenshot("s10_bib_normalize")
        err = session.last_event("bib_render_error")
        H.expect(err is None, f"S10 规范化视图渲染报错: {err}")
        info = {"bib_view": ev, "panel_title": N.PAGE_TITLE,
                "views_label": dict(P.BibPage.VIEWS)["normalize"],
                "group": P.BibPage.group_of("normalize")}
        ctx.record("S10_bib_normalize", **info)
        return info


# ── S6 运行/停止按钮状态（B + D + E）────────────────────────────────────
def s6_buttons(ctx: H.RunContext) -> dict:
    with ctx.session("s6", page="single") as session:
        session.screenshot("s6_before")
        before = len(session.events())
        run_after = sum(1 for e in session.events() if e.get("event") == "validation_error")
        stop_after = sum(1 for e in session.events() if e.get("event") in
                         ("cancel_requested", "cancel_no_proc"))
        session.click_dip(*H.RUN_BTN)
        err = session.wait_event("validation_error", timeout=8, after=run_after)
        H.expect("源稿件" in (err.get("reason") or ""),
                 f"S6 空表单应提示先选源稿件，实际: {err}")
        H.expect(not session.has_event("run_start", after=before), "S6 空表单不应启动任务")
        session.click_dip(*H.STOP_BTN)
        session.settle(0.8)
        H.expect(not session.has_event("cancel_requested", after=stop_after) and
                 not session.has_event("cancel_no_proc", after=stop_after),
                 "S6 未运行时点停止不应产生取消事件")
        session.screenshot("s6_after")
        info = {"validation_reason": err.get("reason"),
                "run_start_events": len([e for e in session.events() if e.get("event") == "run_start"])}
        ctx.record("S6_buttons", **info)
        return info


# ── S7 关闭退出（B + E）─────────────────────────────────────────────────
def s7_close(ctx: H.RunContext) -> dict:
    session = ctx.session("s7", page="home")
    session.start()
    try:
        session.screenshot("s7_before_close", compare=False)
        rc = session.close(native=True)
        events = session.event_names()
        H.expect(session.native_close_ok, "S7 点原生标题栏 X 未在 8s 内退出")
        H.expect(rc == 0, f"S7 退出码应为 0，实际 {rc}")
        for name in ("close_requested", "settings_saved"):
            H.expect(name in events, f"S7 缺少关闭标记 {name}；events={events[-12:]}")
        leftovers = session.leftover_pids()
        H.expect(not leftovers, f"S7 关闭后仍有残留进程: {leftovers}")
        info = {"rc": rc, "native_close_ok": session.native_close_ok,
                "leftovers": leftovers,
                "close_events": [e for e in events if e in
                                 ("close_requested", "geometry_saved", "settings_saved")]}
        ctx.record("S7_close", **info)
        return info
    finally:
        session._shutdown(force=True)


# ── S8 关闭到托盘（A + B + E）──────────────────────────────────────────────
def s8_tray_close(ctx: H.RunContext) -> dict:
    """启用托盘后：原生关闭 → 窗口隐藏、进程不退出 → 托盘显示 → 托盘退出。"""
    session = ctx.session(
        "s8", page="home",
        extra_env={"P3_TRAY_TEST": "wait:15,show,wait:8,quit"},
        settings={"flet": {"tray_enabled": True}},
    )
    session.start()
    try:
        started = session.wait_event("tray_started", lambda e: e.get("started") is True, timeout=20)
        hwnd = session.window.hwnd
        H.expect(bool(H.user32.IsWindowVisible(hwnd)), "S8 启动后窗口应可见")
        session.screenshot("s8_before_close", compare=False)

        H.user32.PostMessageW(hwnd, H.WM_CLOSE, 0, 0)
        close_ev = session.wait_event("close_to_tray", timeout=12)
        hide_ev = session.wait_event("tray_hide", timeout=12)
        H.expect(session.proc.poll() is None, "S8 关闭到托盘后进程不应退出")
        hidden = not bool(H.user32.IsWindowVisible(hwnd))
        H.expect(hidden, f"S8 窗口应隐藏；close={close_ev} hide={hide_ev}")

        show_ev = session.wait_event("tray_show", timeout=25)
        deadline = time.monotonic() + 10
        restored = False
        while time.monotonic() < deadline:
            if H.user32.IsWindowVisible(hwnd):
                restored = True
                break
            time.sleep(0.2)
        H.expect(restored, f"S8 托盘「显示主窗口」后窗口应重新可见；show={show_ev}")
        session.settle(0.8)
        session.screenshot("s8_restored", compare=False)

        known = H.tree_pids(session.proc.pid)
        quit_ev = session.wait_event("tray_quit", timeout=20)
        rc = session.proc.wait(20)
        session._rc = int(rc)
        leftovers = session.leftover_pids()
        H.expect(rc == 0, f"S8 托盘退出码应为 0，实际 {rc}")
        H.expect(not leftovers, f"S8 托盘退出后仍有残留进程: {leftovers}")
        session._shutdown(force=False, known=known)
        info = {
            "rc": rc,
            "tray_started": started,
            "hidden": hidden,
            "restored": restored,
            "close_events": [e.get("event") for e in (close_ev, hide_ev, show_ev, quit_ev)],
            "leftovers": leftovers,
        }
        ctx.record("S8_tray_close", **info)
        return info
    finally:
        session._shutdown(force=True)


SCENARIOS = {
    "s1": s1_launch,
    "s2": s2_navigation,
    "s3": s3_theme,
    "s4": s4_single_flows,
    "s5": s5_bib_views,
    "s6": s6_buttons,
    "s7": s7_close,
    "s8": s8_tray_close,
    "s10": s10_bib_normalize_env,
}
