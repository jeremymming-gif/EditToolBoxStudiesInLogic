"""GUI 自动走查场景（S1–S7）与控件树断言（A 类）。

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
FLOW_LABELS = ["初排转换", "文献回读校验", "一校核查"]
BIB_VIEWS = ["overview", "query", "import", "review", "settings"]
BIB_LABELS = ["概况", "查库", "入藏", "待处理", "设置"]
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
    bx, by = session.border
    width, _height = img.size
    left = int(round(bx + 236 * session.scale))
    top = int(round(by + 70 * session.scale))
    bottom = int(round(by + 130 * session.scale))
    return (left, top, width, bottom)


def _active_chip_center(session: H.AppSession, img):
    return H.active_row_center(img, H.ACCENT_SOFT, _chip_region(session, img), min_count=20)


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
    add("选项名「编译 PDF」字重", label.weight, ft.FontWeight.W_500)
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


# ── S4 单篇页三步骤（A + C + D）─────────────────────────────────────────
def s4_single_flows(ctx: H.RunContext) -> dict:
    fixtures = ensure_fixtures()
    env = {
        "P2_DEMO_FLOW": "convert",
        "P2_DEMO_SINGLE_SOURCE": fixtures["source"],
        "P2_DEMO_SINGLE_OUT": fixtures["out"],
    }
    with ctx.session("s4", page="single", extra_env=env) as session:
        session.settle(0.8)
        names = ["convert", "checkbib", "proofcheck"]
        rows: list[dict] = []
        imgs: dict[str, object] = {}
        img = session.screenshot("s4_flow_convert")
        center = _active_chip_center(session, img)
        H.expect(center is not None, "S4 初排转换 chip 未高亮")
        imgs["convert"] = img
        rows.append({"flow": FLOW_LABELS[0], "center_x_dip": round(
            (center[0] - session.border[0]) / session.scale, 1)})
        for index in (1, 2):
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
        # 三个 flow 的内容区必须真的不同（高亮 chip 只占一小块）
        width, height = img.size
        content = H.regions_for(session, width, height)["content"]
        for a, b in (("convert", "checkbib"), ("checkbib", "proofcheck"), ("convert", "proofcheck")):
            left, top, right, bottom = content
            stats = H.diff_stats(imgs[a].crop((left, top, right, bottom)),
                                 imgs[b].crop((left, top, right, bottom)))
            H.expect(stats["mean"] > 0.3,
                     f"S4 切换 {a}→{b} 后内容区几乎没变化: {stats}")
        ctx.record("S4_single_flows", flows=rows)
        return {"flows": rows}


# ── S5 引用库五视图（B + C + D）─────────────────────────────────────────
def s5_bib_views(ctx: H.RunContext) -> dict:
    rows = []
    with ctx.session("s5", page="bib") as session:
        session.wait_event("bib_stats", timeout=30)
        session.settle(0.6)
        for index, (view, label) in enumerate(zip(BIB_VIEWS, BIB_LABELS)):
            if index == 0:
                img = session.screenshot("s5_view_overview")
            else:
                base_after = sum(1 for e in session.events() if e.get("event") == "bib_view")
                err_after = sum(1 for e in session.events() if e.get("event") == "bib_render_error")
                _click_until_event(
                    session, H.BIB_CHIPS[index], "bib_view",
                    lambda e, wanted=view: e.get("view") == wanted,
                    timeout=4.0, offsets=((0, 0), (0, 5), (0, -5), (-6, 0), (6, 0)),
                )
                H.expect(not session.has_event("bib_render_error", after=err_after),
                         f"S5 视图「{label}」渲染报错: {session.last_event('bib_render_error')}")
                session.settle(0.6)
                img = session.screenshot(f"s5_view_{view}")
            rows.append({"view": view, "label": label,
                         "bib_view_event": session.last_event("bib_view")})
        ctx.record("S5_bib_views", views=rows)
        return {"views": rows}


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


SCENARIOS = {
    "s1": s1_launch,
    "s2": s2_navigation,
    "s3": s3_theme,
    "s4": s4_single_flows,
    "s5": s5_bib_views,
    "s6": s6_buttons,
    "s7": s7_close,
}
