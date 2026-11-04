"""整期装配：合并/编译/三档清理与撤销/目录/推送的取值与编排。

文档逻辑全在 `Engine/`；本模块只拼 `EngineCall`、确认危险操作、显示结果。
清理只暴露 `--trash`（可恢复）与 `--restore`（撤销），不提供永久删除参数。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import checks as CK
from ui import components as C
from ui import dirpush as DIRPUSH
from ui import toc as TOC

MODE_LABELS = {
    "aux": "辅助文件（默认，可撤销）",
    "delivery": "交付收尾（引用闭包外移入回收站）",
    "workspace": "工作区旧件（危险，仍可撤销）",
}
MODE_KEYS = ("aux", "delivery", "workspace")


def toc_panel(page) -> TOC.TocPanel:
    panel = getattr(page, "_toc_panel", None)
    if panel is None:
        panel = TOC.TocPanel(page)
        page._toc_panel = panel
    return panel


def dirpush_panel(page) -> DIRPUSH.DirPushPanel:
    panel = getattr(page, "_dirpush_panel", None)
    if panel is None:
        panel = DIRPUSH.DirPushPanel(page)
        page._dirpush_panel = panel
    return panel


def cleantemp_mode(page) -> str:
    mode = str(page.app.store.get("form.cleantemp_mode", "aux") or "aux")
    return mode if mode in MODE_KEYS else "aux"


def cleantemp_dry_run(page) -> bool:
    """默认 True（批 115 / D1-115-29）：清理是危险动作，先预演。

    旧设置里没有这个键 → 一次性迁移成 True 并记 `cleantemp_dry_run_migrated`；
    用户显式关掉（false）就尊重，不再回弹。
    """
    options = page.app.store.get("options", {}) or {}
    if "cleantemp_dry_run" in options:
        return bool(options.get("cleantemp_dry_run"))
    page.app.store.set("options.cleantemp_dry_run", True)
    page.app.store.set("options.cleantemp_dry_run_migrated", True)
    return True


def _toc_text_only(page) -> bool:
    """「仅生成 tex」开关（批 115 / D1-115-29）：新默认 False。

    旧 store 缺键（旧默认 true）→ 一次性写 False 并记 `toc_text_only_migrated`，
    详情页读到迁移标记会显示一句说明；用户显式改过就按用户的来。
    """
    options = page.app.store.get("options", {}) or {}
    if "toc_text_only" in options:
        return bool(options.get("toc_text_only"))
    page.app.store.set("options.toc_text_only", False)
    page.app.store.set("options.toc_text_only_migrated", True)
    return False


CLEANTEMP_MODES = {
    "aux": {
        "title": "辅助文件", "tone": "success", "badge": "推荐",
        "desc": "*.aux · *.bbl · *.bcf · *.blg · *.out · *.synctex.gz · .DS_Store",
        "keep": "保留 .tex .pdf .sty .bib .log",
    },
    "delivery": {
        "title": "交付收尾", "tone": "accent", "badge": "交付",
        "desc": "tex 引用闭包之外的文件 + *_待核/ 目录树",
        "keep": "引用缺失 1 项即整体不执行（零写入）",
    },
    "workspace": {
        "title": "工作区", "tone": "error", "badge": "危险",
        "desc": "信息检查* · *.bak · *.coop-bak-* · *_待核 · 旧 合并*.pdf",
        "keep": "保留每组最新 合并*.pdf；跳过 000/contents/扉页/编委/封底",
    },
}


def _recent(page, key: str) -> str:
    return str(page.app.store.get(f"recent.{key}", "") or "").strip()


def _form(page, key: str) -> str:
    return str(page.app.store.get(f"form.{key}", "") or "").strip()


# ── 参数区 ───────────────────────────────────────────────────────────────
def build_params(page, key: str) -> ft.Control:
    t = page.t
    step = next(s for s in page.STEPS if s["key"] == key)
    rows: list = [C.text(step["desc"], t, "small", t.palette.text_muted)]
    if key == "infocheck":
        rows += CK.info_params(page)
    elif key == "toc":
        rows += toc_panel(page).params()
    elif key == "compileall":
        options = page.app.store.get("options", {}) or {}
        plan = compile_plan(page.field("issue_dir"), page.field("compile_only"))
        all_plan = compile_plan(page.field("issue_dir"), "")
        only = page.field("compile_only")
        rows += [
            page._meta_field("compile_only", "只编这几篇", 260),
            ft.Row(spacing=t.sp("1"), controls=[
                C.text(f"共 {len(all_plan)} 篇 · 将编译 {len(plan)} 篇", t, "caption",
                       t.palette.text_muted),
                ft.Container(expand=True),
                C.text("留空 = 全部；逗号分隔，如 01,03", t, "caption",
                       t.palette.text_faint),
            ]),
            C.option_row("只预演（--dry-run）", "列出将要编译的篇目，不真跑 xelatex", t,
                         icon="SCIENCE_OUTLINED",
                         checked=bool(options.get("compileall_dry_run", False)),
                         on_change=lambda e: page.app.set_option("compileall_dry_run",
                                                                 e.control.value)),
        ]
    elif key == "merge":
        options = page.app.store.get("options", {}) or {}
        rows += [
            # 用户 m03037：右侧参数卡内宽约 398（1000 宽窗口）→ 固定 420 会横向溢出，
            # 收到 300 后 1240/1000 两档都不越界。
            page._meta_field("merge_out", "输出文件（可空）", 260),
            C.option_row("嵌套书签", "--nest-outlines", t, icon="BOOKMARKS_OUTLINED",
                         checked=bool(options.get("merge_nest", False)),
                         on_change=lambda e: page.app.set_option("merge_nest", e.control.value)),
            C.option_row("加入编辑页", "--editors-page", t, icon="PEOPLE_OUTLINED",
                         checked=bool(options.get("merge_editors", False)),
                         on_change=lambda e: page.app.set_option("merge_editors", e.control.value)),
            C.option_row("加封底", "--back-cover", t, icon="LAST_PAGE_OUTLINED",
                         checked=bool(options.get("merge_back_cover", False)),
                         on_change=lambda e: page.app.set_option("merge_back_cover",
                                                                 e.control.value)),
            ft.Row(spacing=t.sp("1"), controls=[
                C.text_button("先检查（--detect）", lambda e: run_detect(page), t,
                              icon="SEARCH"),
                C.text("只探测编委页/封底页/扉页，不合并", t, "caption",
                       t.palette.text_faint),
            ]),
        ]
    elif key == "cleantemp":
        rows += _cleantemp_params(page)
    elif key == "dirpush":
        rows += dirpush_panel(page).params()
    engine_note = C.text(f"引擎：{page.app.engine_status.describe()}", t, "caption",
                         t.palette.text_faint)
    engine_note.max_lines = 2
    engine_note.overflow = ft.TextOverflow.ELLIPSIS
    # 批 115 / D1-115-29：唯一主按钮在详情头右上（pages.py），参数区不再出第二个运行入口。
    rows.append(engine_note)
    return ft.Column(spacing=t.sp("1"), controls=rows)


def _ct_preview(page) -> dict:
    return getattr(page, "_ct_preview", None) or {}


def cleantemp_preview_ok(page, mode: str | None = None) -> bool:
    """有效预演 = 同期目录 + 同档位 + rc0（批 115；切档/换目录/清理成功即失效）。"""
    preview = _ct_preview(page)
    if not preview.get("ok"):
        return False
    issue = str(page.field("issue_dir") or "")
    if not issue or preview.get("issue") != issue:
        return False
    return preview.get("mode") == (mode if mode is not None else cleantemp_mode(page))


def _ct_choice_row(page, key: str) -> ft.Control:
    t = page.t
    info = CLEANTEMP_MODES[key]
    selected = cleantemp_mode(page) == key
    danger = key == "workspace"
    accent = t.palette.danger if danger else t.palette.accent
    border = (ft.Border.all(1.5, accent) if selected
              else ft.Border.all(1, t.palette.border))
    dot = ft.Container(
        width=t.px(16), height=t.px(16), border_radius=t.px(8),
        border=ft.Border.all(1.5, accent if selected else t.palette.border_strong),
        alignment=ft.Alignment.CENTER,
        content=(ft.Container(width=t.px(8), height=t.px(8), border_radius=t.px(4),
                              bgcolor=accent) if selected else None),
    )
    preview = _ct_preview(page)
    count = None
    if preview.get("ok") and preview.get("mode") == key:
        count = C.text(f"{preview.get('count', 0)} 项", t, "caption", t.palette.text_muted)
    head_controls = [C.text(info["title"], t, "body", weight="medium"),
                     C.badge(info["badge"], t, info["tone"]),
                     ft.Container(expand=True)]
    if count is not None:      # None 不能进 Row.controls：flet 1.0.5 会整块渲染成灰块
        head_controls.append(count)
    head = ft.Row(spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=head_controls)
    # 用「Column 竖排」结构（同 C.option_tile 的已验证布局）；Row 直接套在定高
    # Container 里在 flet 1.0.5 会触发 Flex 约束错误（截图呈灰块），见 notes 遗留。
    return ft.Container(
        height=t.px(72), border=border, border_radius=t.radius("md"),
        bgcolor=t.palette.surface_2 if selected else t.palette.surface,
        padding=C._pad(t, 12, 6, 12, 6),
        on_click=lambda e, k=key: _set_mode(page, k),
        content=ft.Column(spacing=t.px(2),
                          controls=[
            head,
            C.text(info["desc"], t, "caption", t.palette.text_muted, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            C.text(info["keep"], t, "caption", t.palette.text_faint, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
        ]),
    )


def _cleantemp_params(page) -> list:
    t = page.t
    mode = cleantemp_mode(page)
    rows: list = [
        C.notice_bar("三档都移入 `<期目录>/.toolbox-trash/<目录名>-<时间戳>/`；可恢复，不永久删除。",
                     t, "info"),
        ft.Column(spacing=t.sp("1"), horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                  controls=[_ct_choice_row(page, key) for key in MODE_KEYS]),
        _ct_preview_note(page),
        C.option_row("只预演（--dry-run）", "默认开；列出将清理的文件与数量，不动磁盘", t,
                     icon="SCIENCE_OUTLINED", checked=cleantemp_dry_run(page),
                     on_change=lambda e: _set_dry(page, e.control.value)),
        _restore_row(page),
    ]
    return rows


def _set_dry(page, value) -> None:
    page.app.store.set("options.cleantemp_dry_run", bool(value))
    save = getattr(page.app, "save_settings", None)
    if callable(save):
        try:
            save(toast=False)
        except TypeError:
            save()
    page.mark("cleantemp_dry_run", value=bool(value))
    page._refresh_steps()


def _ct_preview_note(page) -> ft.Control:
    t = page.t
    mode = cleantemp_mode(page)
    preview = _ct_preview(page)
    if cleantemp_preview_ok(page):
        when = str(preview.get("ts") or "")[11:16]
        return C.text(f"预演：{preview.get('count', 0)} 项待清理 · {when} · "
                      f"移入 .toolbox-trash/…", t, "caption", t.palette.text_muted)
    if preview and preview.get("mode") == mode and preview.get("issue") == page.field("issue_dir"):
        return C.text(f"上次预演失败（rc={preview.get('code')}）：{preview.get('error') or '看日志'}",
                      t, "caption", t.palette.error, max_lines=2)
    return C.text("尚未预演：先点右上「预览清理」看数量；预演不动磁盘。", t, "caption",
                  t.palette.text_faint)


def _set_mode(page, value) -> None:
    mode = str(value or "aux")
    if mode not in MODE_KEYS:
        mode = "aux"
    page.app.store.set("form.cleantemp_mode", mode)
    page.app.store.set("form.cleantemp_restore", "")
    page.mark("cleantemp_mode", mode=mode)
    page._refresh_steps()


def _restore_row(page) -> ft.Control:
    t = page.t
    issue = page.field("issue_dir")
    choices = trash_dirs(issue) if issue else []
    if not choices:
        return C.text("恢复清理：暂无回收目录（清理后出现在 .toolbox-trash/）", t, "caption",
                      t.palette.text_faint)
    return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[
        C.text_button(f"恢复清理…（{len(choices)} 个回收目录）",
                      lambda e: restore_dialog(page), t, icon="RESTORE"),
    ])


def restore_dialog(page) -> None:
    """恢复入口（只读列出最近 6 个回收目录；真正恢复走 cleantemp --restore）。"""
    t = page.t
    issue = page.field("issue_dir")
    choices = trash_dirs(issue)[:6]
    if not choices:
        page.app.toast("暂无回收目录")
        return
    rows: list = []
    selected = {"path": str(choices[0])}
    for path in choices:
        info = EB.trash_info(str(path))
        caption = (f"{info.get('count', 0)} 项 · {info.get('createdAt', '')[:16]}"
                   if info.get("ok") else "manifest 读不回（仍可整目录搬回）")
        mode = info.get("mode") or ""
        label = ft.Row(spacing=t.sp("1"), controls=[
            C.text(path.name, t, "mono", t.palette.text, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            C.badge(mode or "未知档", t, "accent"),
            C.text(caption, t, "caption", t.palette.text_muted),
        ])
        box = ft.Container(
            height=t.px(44), border_radius=t.radius("sm"),
            bgcolor=(t.palette.accent_soft if str(path) == selected["path"]
                     else t.palette.surface_2),
            content=ft.Row(controls=[ft.Container(width=t.px(16), height=t.px(16),
                                                 border_radius=t.radius("pill"),
                                                 border=ft.Border.all(1.5, t.palette.accent)),
                                     ft.Column([label], expand=True)]),
        )
        box.on_click = (lambda e, p=str(path), target=box: (
            selected.update(path=p),
            [item.__setattr__("bgcolor", t.palette.surface_2) for item in rows],
            target.__setattr__("bgcolor", t.palette.accent_soft)))
        rows.append(box)
    # 单选：点行即选中（默认第一行）；列表信息已足以确认。
    def _go(e) -> None:
        C.dismiss_top_dialog(page.page)
        page.page.run_task(_restore_task, page, issue, selected["path"])

    dialog = ft.AlertDialog(
        modal=True,
        title=C.text("恢复清理", t, "dialog-title"),
        content=ft.Container(width=t.px(560), content=ft.Column([
            C.text("按 manifest 搬回原路径；同名文件/目录跳过不覆盖；回收目录本身保留。",
                   t, "caption", t.palette.text_muted),
            ft.Column(rows, spacing=t.sp("1")),
        ], spacing=t.sp("1"), tight=True)),
        actions=[C.text_button("取消", lambda e: C.dismiss_top_dialog(page.page), t),
                 C.primary_button("恢复", _go, t)],
    )
    page.page.show_dialog(dialog)


# ── 调用构建 ─────────────────────────────────────────────────────────────
def build_step_call(page, key: str) -> EB.EngineCall:
    issue = page.field("issue_dir")
    if not issue:
        raise ValueError("请先选择期目录")
    options = page.app.store.get("options", {}) or {}
    if key == "infocheck":
        return EB.infocheck_call(folder=issue)
    if key == "toc":
        report = page.field("toc_report")
        if not report:
            latest = TOC.latest_report(issue)
            if latest:
                report = latest
                page.set_field("toc_report", latest)
        if not report:
            raise ValueError("请先选择信息检查报告（可先运行「检查 tex 信息」）")
        result_json = os.path.join(os.path.dirname(report), "目录结果.json")
        return EB.toc_call(report=report, template=_recent(page, "toc_template"),
                           settings=_recent(page, "toc_settings"),
                           text_only=_toc_text_only(page),
                           result_json=result_json)
    if key == "compileall":
        return EB.compileall_call(folder=issue, only=page.field("compile_only"),
                                  dry_run=bool(options.get("compileall_dry_run", False)))
    if key == "merge":
        return EB.merge_call(
            folder=issue, out=page.field("merge_out"),
            nest_outlines=bool(options.get("merge_nest", False)),
            editors_page=bool(options.get("merge_editors", False)),
            back_cover=bool(options.get("merge_back_cover", False)),
        )
    if key == "cleantemp":
        return EB.cleantemp_call(folder=issue, mode=cleantemp_mode(page),
                                 dry_run=cleantemp_dry_run(page))
    if key == "dirpush":
        return DIRPUSH.build_call(
            issue=issue, out_root=page.field("dirpush_out"),
            annual=_form(page, "dirpush_mode") == DIRPUSH.MODE_ANNUAL,
            year=_form(page, "dirpush_year"),
            include_issues=_form(page, "dirpush_issues"),
            with_en_toc=bool(options.get("dirpush_with_en_toc", False)),
            dry_run=bool(options.get("dirpush_dry_run", False)),
        )
    raise ValueError(f"未知步骤：{key}")


# ── 运行 ─────────────────────────────────────────────────────────────────
# 批 115（D1-115-26）：整期默认只跑 1–4 步；清理/推送默认不跑，由确认框显式勾选。
DEFAULT_RUN_ALL_KEYS = ("infocheck", "toc", "compileall", "merge")
_STATE_LABELS = {
    "done": "完成",
    "warn": "警告",
    "error": "失败",
    "cancelled": "已取消",
    "skipped": "跳过",
}
_STATE_LEVELS = {
    "done": "ok",
    "warn": "warn",
    "error": "error",
    "cancelled": "dim",
    "skipped": "dim",
}


class StepStatus(str):
    """步骤状态字符串 + 运行元数据（批 115 工作台用）。

    值仍是 ``idle/running/done/warn/error/cancelled/skipped`` 之一，所以 pages.py
    既有的 ``_status[key] == "done"``、``{...}.get(state)`` 等按字符串的用法原样兼容；
    新增属性承载以后按 issue 路径持久化的 state/exit_code/时长/产物。
    """

    def __new__(cls, state, *, exit_code=None, duration_ms=None, started_at="",
                finished_at="", artifact="", error=""):
        obj = super().__new__(cls, str(state))
        obj.state = str(state)
        obj.exit_code = exit_code
        obj.duration_ms = duration_ms
        obj.started_at = started_at
        obj.finished_at = finished_at
        obj.artifact = artifact
        obj.error = error
        return obj

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifact": self.artifact,
            "error": self.error,
        }


def _steps(page) -> list[dict]:
    return list(getattr(page, "STEPS", []) or [])


def _step(page, key: str) -> dict:
    for step in _steps(page):
        if step.get("key") == key:
            return step
    return {"key": key, "title": key}


def _step_index(page, key: str) -> int:
    for index, step in enumerate(_steps(page), 1):
        if step.get("key") == key:
            return index
    return 0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _log_line(page, message: str, level: str = "info") -> None:
    """写日志行；**不再 clear**（清空只由「清空」按钮触发，见 DECISIONS D1-115-26）。"""
    add = getattr(getattr(page, "_log", None), "add_line", None)
    if add is None:
        return
    try:
        add(message, level, True)
    except TypeError:  # 旧/精简 LogConsole 只接受 (value)
        add(message)


def _section_start(page, key: str, started_at: str) -> str:
    index = _step_index(page, key)
    total = len(_steps(page)) or index
    title = str(_step(page, key).get("title") or key)
    return f"▶ {index:02d}/{total:02d} {title} · {key} · {started_at[11:19]}"


def _section_end(page, key: str, status: StepStatus) -> str:
    index = _step_index(page, key)
    total = len(_steps(page)) or index
    title = str(_step(page, key).get("title") or key)
    rc = "rc=—" if status.exit_code is None else f"rc={status.exit_code}"
    seconds = "—" if status.duration_ms is None else f"{status.duration_ms / 1000:.1f}s"
    label = _STATE_LABELS.get(status.state, status.state)
    return f"■ {index:02d}/{total:02d} {title} · {key} · {rc} · {seconds} · {label}"


def _record(key: str, status: StepStatus) -> dict:
    """results 条目：`{key, rc, duration_ms, error?}` + 工作台要用的元数据。"""
    item = {
        "key": key,
        "state": status.state,
        "rc": status.exit_code,
        "duration_ms": status.duration_ms,
        "started_at": status.started_at,
        "finished_at": status.finished_at,
        "artifact": status.artifact,
    }
    if status.error:
        item["error"] = status.error
    return item


def _result_reset(page) -> None:
    result = getattr(page, "_result", None)
    if result is not None:
        result.reset()


def _result_show(page, text: str, level: str = "info", artifact: str = "") -> None:
    result = getattr(page, "_result", None)
    if result is not None:
        result.show(text, level=level, artifact=artifact)


def _page_update(page) -> None:
    try:
        page.page.update()
    except Exception:  # noqa: BLE001 — 页面可能尚未挂载（测试/构建期）
        pass


def _on_line(page, line: str) -> None:
    handler = getattr(page, "_on_line", None)
    if handler is not None:
        handler(line)


def _mark_pending(page, results: list[dict], keys: list[str], state: str) -> None:
    """把没跑的步骤标成 skipped / cancelled（不启动子进程，不改结果条）。"""
    for key in keys:
        status = StepStatus(state, exit_code=None, duration_ms=None,
                            started_at="", finished_at=_now())
        page._status[key] = status
        results.append(_record(key, status))
        _log_line(page, _section_end(page, key, status), "dim")
    page._refresh_steps()


async def _run_sequence(page, keys: list[str], calls: dict | None = None) -> list[dict]:
    """统一执行器：单步与整期共用（pages._run_calls 会 per-step 清日志，不能再用）。

    - 默认**不清空日志**，每步前后各写一条段头/段尾（序号 + key + 时间）；
    - 每步把状态与元数据写回 ``page._status[key]``，并保留 ``page._run_results``；
    - rc >= 2（或启动异常）→ 立即停止，后续标 ``skipped``；rc == 1 → ``warn`` 继续；
    - 取消只停当前子进程（kill 由 app.stop_run 负责），后续标 ``cancelled``。
    """
    calls = dict(calls or {})
    results: list[dict] = []
    page._run_results = results
    for position, key in enumerate(keys):
        if getattr(page.app, "cancel_requested", False):
            _mark_pending(page, results, keys[position:], "cancelled")
            break
        started_at = _now()
        page._status[key] = StepStatus("running", started_at=started_at)
        page._refresh_steps()
        _log_line(page, _section_start(page, key, started_at), "dim")
        started = time.monotonic()
        try:
            call = calls.get(key)
            if call is None:
                # 懒构建：前一步（infocheck）刚产出的报告要让 toc 在这里自动发现。
                call = build_step_call(page, key)
        except (ValueError, RuntimeError) as exc:
            duration = round((time.monotonic() - started) * 1000, 1)
            status = StepStatus("error", exit_code=None, duration_ms=duration,
                                started_at=started_at, finished_at=_now(),
                                error=str(exc))
            page._status[key] = status
            page._refresh_steps()
            _log_line(page, _section_end(page, key, status), "error")
            page.app.toast(str(exc))
            page.mark("run_start_error", step=key, error=str(exc))
            _result_show(page, f"❌ 无法启动：{exc}", "error")
            results.append(_record(key, status))
            _mark_pending(page, results, keys[position + 1:], "skipped")
            break
        _result_reset(page)
        CK.before_issue_step(page, key)
        page.app.begin_run(call)
        try:
            code = await EB.run_call(
                call, on_line=lambda line: _on_line(page, line),
                on_proc=page.app.track_proc, notes=page.app.notes_repo,
            )
        except Exception as exc:  # noqa: BLE001 — 引擎启动失败也要落状态
            duration = round((time.monotonic() - started) * 1000, 1)
            page.app.finish_run(call, -1)
            cancelled = bool(getattr(page.app, "cancel_requested", False))
            state = "cancelled" if cancelled else "error"
            status = StepStatus(state, exit_code=-1, duration_ms=duration,
                                started_at=started_at, finished_at=_now(),
                                error=f"{type(exc).__name__}: {exc}")
            page._status[key] = status
            page._refresh_steps()
            _log_line(page, _section_end(page, key, status), _STATE_LEVELS[state])
            if not cancelled:
                page._result.show(f"❌ 任务启动失败：{exc}", level="error")
                page.app.toast(f"任务启动失败：{exc}")
                page.app.mark("run_start_error", step=key, error=str(exc))
            results.append(_record(key, status))
            _mark_pending(page, results, keys[position + 1:],
                          "cancelled" if cancelled else "skipped")
            break
        duration = round((time.monotonic() - started) * 1000, 1)
        page.app.finish_run(call, code)
        cancelled = bool(getattr(page.app, "cancel_requested", False))
        if cancelled:
            state = "cancelled"
        elif code == 0:
            state = "done"
        elif code == 1:
            state = "warn"
        else:
            state = "error"
        # app.last_line 由 app 的 on_line 回调更新（见 main.begin_run/_on_line）。
        artifact = EB.artifact_path(getattr(page.app, "last_line", "") or "")
        status = StepStatus(state, exit_code=code, duration_ms=duration,
                            started_at=started_at, finished_at=_now(),
                            artifact=artifact)
        page._status[key] = status
        page._refresh_steps()
        results.append(_record(key, status))
        try:  # 后处理（信息卡/目录结果）失败不应打断流水线状态收尾
            await CK.after_issue_step(page, key, call, code, cancelled, artifact)
            after_step(page, key, call, code, cancelled, artifact)
        except Exception as exc:  # noqa: BLE001
            _log_line(page, f"⚠ 步骤后处理失败：{type(exc).__name__}: {exc}", "warn")
        if cancelled:
            _result_show(page, "⏹ 已取消当前步骤（后续步骤未启动）", "warn", artifact)
        else:
            _result_show(page, EB.result_text(call.entry, code),
                         EB.result_level(call.entry, code), artifact)
            if artifact:
                page.app.store.set_recent("issue_dir", page.field("issue_dir"))
        _log_line(page, _section_end(page, key, status), _STATE_LEVELS[state])
        _page_update(page)
        if cancelled:
            _mark_pending(page, results, keys[position + 1:], "cancelled")
            break
        if code >= 2 or code < 0:
            _mark_pending(page, results, keys[position + 1:], "skipped")
            break
    page._refresh_steps()
    return results


def _run_all_keys(page, keys) -> tuple[list[str], list[str]]:
    known = [str(step.get("key")) for step in _steps(page)]
    if keys is None:
        return [key for key in DEFAULT_RUN_ALL_KEYS if key in known], []
    raw = [keys] if isinstance(keys, str) else [str(key) for key in keys]
    selected: list[str] = []
    unknown: list[str] = []
    for key in raw:
        if key not in known:
            unknown.append(key)
        elif key not in selected:
            selected.append(key)
    return selected, unknown


def run_step(page, key: str) -> None:
    if page.app.running:
        return
    try:
        call = build_step_call(page, key)
    except (ValueError, RuntimeError) as exc:
        page.app.toast(str(exc))
        page.mark("validation_error", step=key, reason=str(exc))
        return
    if key == "cleantemp" and not cleantemp_dry_run(page) and not page.app.auto_confirm:
        def _confirmed() -> None:
            page.mark("confirm_clean", step=key)
            _start(page, key, call)

        C.confirm(page.page, page.t, title="确认清理临时文件？",
                  message="临时文件将移入本期回收目录（.toolbox-trash）（期目录内 .toolbox-trash），"
                          "可从回收站或该目录恢复；本页不提供永久删除。",
                  confirm_label="开始清理", on_confirm=_confirmed)
        return
    if key == "dirpush":
        warning = DIRPUSH.repo_warning(page.field("dirpush_out"))
        if warning and not _dirpush_dry(page) and not page.app.auto_confirm:
            def _confirmed() -> None:
                page.mark("confirm_repo_out", path=page.field("dirpush_out"))
                _start(page, key, call)

            C.confirm(page.page, page.t, title="输出根在代码仓内", message=warning,
                      confirm_label="继续生成", on_confirm=_confirmed)
            return
        if warning and _dirpush_dry(page):
            page.page.run_task(_show_repo_note, page, warning)
    _start(page, key, call)


def _dirpush_dry(page) -> bool:
    return bool((page.app.store.get("options", {}) or {}).get("dirpush_dry_run", False))


async def _show_repo_note(page, warning: str) -> None:
    page._result.show(warning, level="warn")
    try:
        page.page.update()
    except Exception:  # noqa: BLE001
        pass


def _start(page, key: str, call: EB.EngineCall) -> None:
    page._status[key] = "running"
    page._refresh_steps()
    # 单步也走统一执行器：日志不再被 pages._run_calls 的 log.clear() 抹掉。
    page.page.run_task(_run_sequence, page, [key], {key: call})


def run_all(page, keys: list[str] | None = None) -> None:
    """整期流水线。

    默认只跑 1–4 步（``DEFAULT_RUN_ALL_KEYS``）；调用方（确认框）可显式传入
    含 ``cleantemp``/``dirpush`` 的 keys，此时按页面当前 mode/dry_run 正常构建、
    不再强制清理只预演——**确认由 pages.py 在调用前负责**，本函数不重复弹窗。
    """
    if page.app.running:
        return
    selected, unknown = _run_all_keys(page, keys)
    if unknown:
        message = f"未知步骤：{unknown[0]}"
        page.app.toast(message)
        page.mark("validation_error", step="all", reason=message)
        return
    if not selected:
        page.app.toast("请至少选择一个步骤")
        page.mark("validation_error", step="all", reason="no step selected")
        return
    if not page.field("issue_dir"):
        page.app.toast("请先选择期目录")
        page.mark("validation_error", step="all", reason="missing issue dir")
        return
    if "dirpush" in selected and not _dirpush_dry(page):
        warning = DIRPUSH.repo_warning(page.field("dirpush_out"))
        if warning:
            page.app.toast("输出根在代码仓内，整期流水线已停止；请改输出根或勾选只预演")
            page.mark("validation_error", step="all", reason="repo out root")
            return
    # 参数在轮到该步时才构建（infocheck 新产出的报告要让 toc 自动发现）。
    page.mark("run_all_submit", steps=list(selected))
    page._run_results = []
    page.page.run_task(_run_sequence, page, selected, {})


def after_step(page, key: str, call: EB.EngineCall, code: int, cancelled: bool,
               artifact: str) -> None:
    """流水线每步结束后补记结构化结果（显示与状态收尾由 _run_sequence 负责）。"""
    if cancelled:
        return
    # 批 116：03 编译成功后由页面决定是否把详情滚到 04（不自动运行 04）。
    hook = getattr(page, "on_step_finished", None)
    if callable(hook):
        try:
            hook(key, code)
        except Exception:  # noqa: BLE001 页面桩/旧测试兼容
            pass
    if key == "toc" and code == 0:
        report = page.field("toc_report")
        path = os.path.join(os.path.dirname(report), "目录结果.json") if report else ""
        payload = EB.toc_result(path)
        page.mark("toc_result", ok=bool(payload), pages=(payload.get("pages") or {}),
                  warnings=len(payload.get("warnings") or []))
    elif key == "cleantemp":
        page.mark("cleantemp_done", code=code, artifact=artifact)
    elif key == "dirpush":
        page.mark("dirpush_done", code=code, artifact=artifact)


# ── 清理预览 / 撤销 ──────────────────────────────────────────────────────
def trash_dirs(issue: str) -> list[Path]:
    if not issue:
        return []
    root = Path(issue) / ".toolbox-trash"
    try:
        dirs = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return []
    try:
        return sorted(dirs, key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return dirs


def parse_count(lines: list[str]) -> int:
    for line in reversed(lines or []):
        # 批 115 / D1-115-29：aux 档契约用「个」，delivery/workspace 用「项」。
        match = re.search(r"(\d+)\s*(?:个|项)", str(line))
        if match:
            return int(match.group(1))
    return 0


def preview_summary(lines: list[str], mode: str) -> str:
    count = parse_count(lines)
    last = ""
    for line in reversed(lines or []):
        text = str(line).strip()
        if text:
            last = text
            break
    head = f"预演（{MODE_LABELS.get(mode, mode)}）：{count} 个文件待清理"
    return f"{head}\n{last}" if last else head


# ── 编译预扫描 / 事件解析（批 115）─────────────────────────────────────
_NUMBERED = re.compile(r"^\d{2,3}\s")


@dataclass
class CompileTarget:
    prefix: str
    dir_name: str
    stem: str
    tex: str
    pdf: str


def _tex_key(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum()).lower()


def _find_tex(folder: str, art: str) -> str:
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return ""
    for name in files:
        if name.lower().endswith(".tex") and os.path.splitext(name)[0] == art:
            return os.path.join(folder, name)
    target = _tex_key(art)
    for name in files:
        if (name.lower().endswith(".tex")
                and _tex_key(os.path.splitext(name)[0]) == target):
            return os.path.join(folder, name)
    return ""


def compile_plan(issue: str, only: str = "") -> list[CompileTarget]:
    """复刻 `Engine/compileall.py` 的扫描规则（numbered_entries → 跳 000 → 前缀过滤
    → article_name → find_tex 精确/宽松），只用于运行前显示「计划编译 m 篇」。"""
    root = os.path.abspath(os.path.expanduser(str(issue or "")))
    if not root or not os.path.isdir(root):
        return []
    wanted = {x.strip() for x in str(only or "").split(",") if x.strip()}
    plans: list[CompileTarget] = []
    try:
        names = sorted(name for name in os.listdir(root)
                       if _NUMBERED.match(name)
                       and os.path.isdir(os.path.join(root, name)))
    except OSError:
        return []
    for name in names:
        if name.startswith("000"):
            continue
        if wanted and not any(name.startswith(prefix) for prefix in wanted):
            continue
        parts = name.split()
        art = parts[-1] if parts else name
        sub = os.path.join(root, name)
        tex = os.path.join(sub, f"{art}.tex")
        if not os.path.exists(tex):
            tex = _find_tex(sub, art)
        if not tex or not os.path.exists(tex):
            continue
        stem = os.path.splitext(os.path.basename(tex))[0]
        plans.append(CompileTarget(
            prefix=parts[0] if parts else name,
            dir_name=name, stem=stem, tex=tex,
            pdf=os.path.join(sub, f"{stem}.pdf"),
        ))
    return plans


def compile_event(line: str) -> tuple[str, dict] | None:
    """编译日志 → 事件（详情页拿真实篇数/当前篇，不造百分比）。"""
    text = str(line or "")
    match = re.search(r'第一步：Xelatex "(.+?)\.tex"', text)
    if match:
        return "start", {"stem": match.group(1)}
    match = re.search(r"✅\s+(.+?)（(\d+)\s*秒）", text)
    if match:
        return "ok", {"stem": match.group(1), "seconds": int(match.group(2))}
    match = re.search(r"❌\s+(.+?)（(\d+)\s*秒）(.*)", text)
    if match:
        return "fail", {"stem": match.group(1), "seconds": int(match.group(2)),
                        "message": (match.group(3) or "").strip()}
    match = re.search(r"打开路径：(.+)", text)
    if match:
        return "discover", {"path": match.group(1).strip()}
    match = re.search(r"没有找到(.+?)文件！", text)
    if match:
        return "skip", {"name": match.group(1).strip()}
    match = re.search(r"完成。编译 (\d+) 篇（成功 (\d+) / 失败 (\d+)）", text)
    if match:
        return "summary", {"targets": int(match.group(1)),
                           "ok": int(match.group(2)), "fail": int(match.group(3))}
    return None


async def _detect_task(page, call: EB.EngineCall) -> None:
    try:
        result = await asyncio.to_thread(EB.read_json_command, call, timeout=180,
                                         notes=page.app.notes_repo)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload = result.get("json") or {}
    if result.get("ok"):
        # 批 116 I-2：detect 结果落到 04 区块的只读「前置页」卡（不提供编辑）。
        setter = getattr(page, "set_prep_pages", None)
        if callable(setter):
            try:
                setter(payload)
            except Exception:  # noqa: BLE001 页面桩/旧测试兼容
                pass
        editors = payload.get("editorsPage") or {}
        back = payload.get("backCover") or {}
        lines = [
            f"编委页：{editors.get('fileName') or '未找到'}"
            + (f"（{editors.get('pageCount')} 页）" if editors else ""),
            f"封底：{back.get('fileName') or '未找到'}"
            + (f"（{back.get('pageCount')} 页）" if back else ""),
            f"扉页：{payload.get('coverPages') or 0} 页",
        ]
        page._result.show("检测完成", detail="\n".join(lines), level="info")
    else:
        page._result.show(f"检测失败：{result.get('error') or '看日志'}", level="error")
    try:
        page.page.update()
    except Exception:  # noqa: BLE001
        pass
    page.mark("merge_detect", ok=bool(result.get("ok")))


def run_detect(page) -> None:
    issue = page.field("issue_dir")
    if not issue:
        page.app.toast("请先选择期目录")
        return
    page.page.run_task(_detect_task, page, EB.merge_call(folder=issue, detect=True))


async def _preview_task(page) -> None:
    issue = page.field("issue_dir")
    if not issue:
        page.app.toast("请先选择期目录")
        page.mark("validation_error", step="cleantemp", reason="missing issue dir")
        return
    call = EB.cleantemp_call(folder=issue, mode=cleantemp_mode(page), dry_run=True)
    try:
        result = await asyncio.to_thread(EB.read_json_command, call, timeout=300,
                                         notes=page.app.notes_repo)
        lines = result.get("lines") or []
        ok = bool(result.get("ok"))
        error = str(result.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        lines, ok, error = [], False, f"{type(exc).__name__}: {exc}"
    if ok:
        text = preview_summary(lines, cleantemp_mode(page))
        level = "info"
    else:
        text, level = f"预览失败：{error or '看日志'}（检查引擎与期目录）", "error"
    trash = Path(issue) / ".toolbox-trash"
    mode = cleantemp_mode(page)
    count = parse_count(lines)
    page._ct_preview = {
        "ok": ok, "issue": issue, "mode": mode, "count": count, "code": 0,
        "lines": list(lines), "error": error,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    page._result.show(text, level=level, artifact=str(trash) if trash.is_dir() else "")
    try:
        page.page.update()
    except Exception:  # noqa: BLE001
        pass
    page.mark("cleantemp_preview", ok=ok, files=count, mode=mode, error=error)
    refresh = getattr(page, "_refresh_steps", None)
    if callable(refresh):
        refresh()


def cleantemp_preview_lines(page) -> list[str]:
    """「查看清单」对话框用：预演记录里的 Will move 行（≤200）。"""
    lines = [str(x) for x in (_ct_preview(page).get("lines") or [])]
    return [x for x in lines if x][:200]


def run_cleantemp_confirmed(page) -> None:
    """确认框之后真正开始清理（跳过 run_step 内部的第二张确认框）。"""
    if page.app.running:
        return
    try:
        call = build_step_call(page, "cleantemp")
    except (ValueError, RuntimeError) as exc:
        page.app.toast(str(exc))
        return
    page._ct_preview = {}
    page.mark("confirm_clean", step="cleantemp")
    _start(page, "cleantemp", call)


def restore(page, path: str = "") -> None:
    issue = page.field("issue_dir")
    target = str(path or page.app.store.get("form.cleantemp_restore", "") or "")
    if not issue:
        page.app.toast("请先选择期目录")
        return
    if not target:
        page.app.toast("请选择要恢复的回收目录")
        page.mark("validation_error", step="cleantemp", reason="missing trash dir")
        return
    page.page.run_task(_restore_task, page, issue, target)


async def _restore_task(page, issue: str, target: str) -> None:
    call = EB.cleantemp_call(folder=issue, restore=target)
    try:
        result = await asyncio.to_thread(EB.read_json_command, call, timeout=300,
                                         notes=page.app.notes_repo)
        lines = result.get("lines") or []
        ok = bool(result.get("ok"))
        error = str(result.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        lines, ok, error = [], False, f"{type(exc).__name__}: {exc}"
    count = parse_count(lines)
    if ok:
        page.app.toast(f"已恢复 {count} 个文件" if count else "已恢复所选目录")
        page._result.show(f"撤销清理完成：搬回 {count or '若干'} 个文件", level="success")
        page.app.store.set("form.cleantemp_restore", "")
    else:
        reason = error or next((str(x) for x in reversed(lines) if str(x).strip()), "看日志")
        page.app.toast(f"撤销失败：{reason}")
        page._result.show(f"撤销清理失败：{reason}（同名文件不会被覆盖）", level="error")
    page.mark("cleantemp_restore", ok=ok, files=count, path=target, error=error)
    try:
        page.page.update()
    except Exception:  # noqa: BLE001
        pass
    if ok:
        page._params_box.controls = [build_params(page, "cleantemp")]
        page._safe_update(page._params_box)
