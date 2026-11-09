"""整期装配：合并/编译/清理临时文件（单入口）/目录/推送的取值与编排。

文档逻辑全在 `Engine/`；本模块只拼 `EngineCall`、编排步骤、显示结果。
清理走 `--mode daily`（自动校验 + 回收，可撤销），撤销走 `--restore`；
本模块不提供三档选择、手动预演闸或永久删除参数。
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
from services import toc_settings as TS
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
    """「仅生成 tex」开关（批 115 / D1-115-29；批 117 改为纯读取）。

    只读页面 store 的 `options.toc_text_only`：缺键按 False（新默认），已有值原样尊重。
    **不再写 store / 不再写迁移标记**——读取开关不该悄悄改用户设置；旧 store 里存下的
    True 也保持原样（批 115 的一次性迁移已把缺键写成 False，不在此重复判断）。
    """
    value = page.app.store.get("options.toc_text_only", False)
    return bool(value)


def toc_column_gate(page) -> tuple[bool, str]:
    """B-375 共享门：所有「生成目录」入口共用的栏目有效性判断。

    返回 `(是否阻断, 原因)`。面板在场就用面板当前值与读取错误；面板还没建
    （例如整期串行、还没打开目录页）则读已保存的 `目录设置.json` 校验，
    read 失败同样阻断——不能因为没打开面板就绕过栏目的四项必填。
    """
    panel = getattr(page, "_toc_panel", None)
    if panel is not None:
        load_error = str(getattr(panel, "_load_error", "") or "")
        if load_error:
            return True, (f"栏目表设置读取失败（{load_error}）："
                          "修好该 JSON 或点「读取栏目表」重新读入后再生成。")
        return panel.column_issue()
    report = page.field("toc_report") or ""
    if not report:
        issue = page.field("issue_dir")
        if issue:
            report = TOC.latest_report(issue)
    explicit = page.field("toc_settings")
    path = explicit or (TS.path_for(os.path.dirname(report)) if report else "")
    if not path or not os.path.isfile(path):
        return False, ""
    data, error = TS.read(path)
    if error:
        return True, (f"栏目表设置读取失败（{error}）："
                      "修好该 JSON 或点「读取栏目表」重新读入后再生成。")
    return TOC.column_issue(data)


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
            C.option_row("只预演（不写入）", "列出将要编译的篇目，不真跑 xelatex", t,
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
            C.option_row("嵌套书签", "合并后保留章节书签层级", t, icon="BOOKMARKS_OUTLINED",
                         checked=bool(options.get("merge_nest", False)),
                         on_change=lambda e: page.app.set_option("merge_nest", e.control.value)),
            C.option_row("加入编辑页", "附加编委页", t, icon="PEOPLE_OUTLINED",
                         checked=bool(options.get("merge_editors", False)),
                         on_change=lambda e: page.app.set_option("merge_editors", e.control.value)),
            C.option_row("加封底", "附加封底页", t, icon="LAST_PAGE_OUTLINED",
                         checked=bool(options.get("merge_back_cover", False)),
                         on_change=lambda e: page.app.set_option("merge_back_cover",
                                                                 e.control.value)),
            ft.Row(spacing=t.sp("1"), controls=[
                C.text_button("先检查", lambda e: run_detect(page), t,
                              icon="SEARCH"),
                C.text("只探测编委页/封底页/扉页，不合并", t, "caption",
                       t.palette.text_faint),
            ]),
        ]
    elif key == "cleantemp":
        rows += page._cleanup.params()
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


def cleantemp_include_tocwork(page) -> bool:
    """B-121 B-357：清理时是否追加 `--mode tocwork`（默认勾选，保持旧设置兼容）。"""
    options = page.app.store.get("options", {}) or {}
    return bool(options.get("cleantemp_tocwork", True))


def cleantemp_all_reports(page) -> bool:
    """旧《信息检查》报告是否连最新一组也清（默认保留，显式勾选才全清）。"""
    options = page.app.store.get("options", {}) or {}
    return bool(options.get("cleantemp_all_reports", False))


def cleantemp_preview_ok(page, mode: str | None = None) -> bool:
    """有效预演 = 同期目录 + 同档位 + rc0（批 115；切档/换目录/清理成功即失效）。"""
    preview = _ct_preview(page)
    if not preview.get("ok"):
        return False
    issue = str(page.field("issue_dir") or "")
    if not issue or preview.get("issue") != issue:
        return False
    return preview.get("mode") == (mode if mode is not None else cleantemp_mode(page))


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
        # 批 121 A6：整期装配与面板共用同一份自动保存后的栏目设置。
        # 面板在场 → 校验 + 自动保存（失败显式中止）；否则按「已保存默认文件 → recent」回退。
        settings = ""
        panel = getattr(page, "_toc_panel", None)
        # B-375：面板/已保存设置的栏目有效性走同一个共享门（与页面按钮一致）。
        blocked, hint = toc_column_gate(page)
        if blocked:
            raise ValueError(hint)
        if panel is not None:
            path, error = panel.sync_settings()
            if error:
                raise ValueError(f"栏目表保存失败：{error}")
            if path and os.path.isfile(path):
                settings = path
        if not settings:
            saved = TS.path_for(os.path.dirname(report))
            settings = TOC.existing_settings_path(saved)
        if not settings:
            # Lead m00984(3)：recent 可能是已删除/搬走的旧路径；引擎对显式不存在的
            # --settings 会直接拒绝（「找不到设置文件」）。与预览同一套解析：只传
            # 真实存在的文件，否则省略 --settings 让引擎用默认，避免生成前才炸。
            settings = TOC.existing_settings_path(_recent(page, "toc_settings"))
        # 批 121 B-355 接口：结果 JSON 落期目录 `.toolbox-tmp/`；建目录失败回退旧路径。
        try:
            result_json = TOC.ensure_result_dir(report)
        except OSError:
            result_json = TOC.legacy_result_json_path(report)
        return EB.toc_call(report=report, template=_recent(page, "toc_template"),
                           settings=settings,
                           text_only=bool(_toc_text_only(page)),
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
        # T3：单一日常入口（INTERFACE-DAILY）；旧 mode/dry_run/tocwork/all_reports
        # 设置不再参与构建。旧三档 --trash 仍由 EB.cleantemp_call 保留给旧调用方。
        return EB.cleantemp_daily_call(folder=issue)
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


def _step_started(page, key: str) -> None:
    """P04 R05-1/R05-3：通知页面某一步真正开始（合并清上一轮结果缓存）。

    B-116/B-117 的页面桩没有这个钩子，用 getattr + try/except 兼容；
    钩子失败只记一行警告，不打断运行。
    """
    hook = getattr(page, "on_step_started", None)
    if not callable(hook):
        return
    try:
        hook(key)
    except Exception as exc:  # noqa: BLE001 —— 页面钩子失败不应打断运行
        _log_line(page, f"步骤启动钩子失败：{type(exc).__name__}: {exc}", "warn")


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
        wall_started = time.time()
        lines: list[str] = []
        page._status[key] = StepStatus("running", started_at=started_at)
        page._refresh_steps()
        _step_started(page, key)
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
            _result_show(page, f"失败 · 无法启动：{exc}", "error")
            results.append(_record(key, status))
            _mark_pending(page, results, keys[position + 1:], "skipped")
            break
        _result_reset(page)
        CK.before_issue_step(page, key)
        page.app.begin_run(call)
        try:
            code = await EB.run_call(
                call, on_line=lambda line: (lines.append(str(line)),
                                            _on_line(page, line)),
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
                page._result.show(f"失败 · 无法启动任务：{exc}", level="error")
                page.app.toast(f"任务启动失败：{exc}")
                page.app.mark("run_start_error", step=key, error=str(exc))
            results.append(_record(key, status))
            _mark_pending(page, results, keys[position + 1:],
                          "cancelled" if cancelled else "skipped")
            break
        page.app.finish_run(call, code)
        followup = getattr(call, "_followup", None)
        if (followup is not None and code == 0
                and not getattr(page.app, "cancel_requested", False)):
            _log_line(page, f"▶ 追加清理：{followup.label or followup.entry}", "dim")
            page.app.begin_run(followup)
            try:
                code = await EB.run_call(
                    followup, on_line=lambda line: (lines.append(str(line)),
                                                   _on_line(page, line)),
                    on_proc=page.app.track_proc, notes=page.app.notes_repo,
                )
            except Exception as exc:  # noqa: BLE001
                code = -1
                _log_line(page, f"追加清理启动失败：{type(exc).__name__}: {exc}", "error")
            page.app.finish_run(followup, code)
        duration = round((time.monotonic() - started) * 1000, 1)
        cancelled = bool(getattr(page.app, "cancel_requested", False))
        if key == "dirpush":
            # BUG-07：rc≠0 / JSON ok=false（含同名覆盖失败）都算失败；产物只认
            # 本次引擎 JSON outputs[] 里真实存在的文件，不再拿日志行末尾猜路径。
            state, artifact = DIRPUSH.absorb(page, lines, code, cancelled)
        else:
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
        try:  # 后处理（信息卡/目录结果）失败不应打断运行状态收尾
            await CK.after_issue_step(page, key, call, code, cancelled, artifact)
            # 「last」= 此后不会再有步骤真正运行：成功/告警且后面还有步骤时不算收尾，
            # 失败/取消会在本步后 break，同样按收尾处理（日志 dock 收回，参数区可见）。
            after_step(page, key, call, code, cancelled, artifact,
                       cancelled or code >= 2 or code < 0
                       or position == len(keys) - 1,
                       lines=lines, started=wall_started)
        except Exception as exc:  # noqa: BLE001
            _log_line(page, f"步骤后处理失败：{type(exc).__name__}: {exc}", "warn")
        if cancelled:
            # P04 R05-2：取消的合并没有「本次产物」，不得把旧/半成品 PDF 作为
            # 本轮产物提供「打开文件」。
            sanitize = getattr(page, "sanitize_cancelled_artifact", None)
            if callable(sanitize):
                try:
                    artifact = sanitize(key, artifact) or ""
                except Exception:  # noqa: BLE001 页面桩/旧测试兼容
                    artifact = ""
            text = ("本次已取消/未完成：没有生成本次合并产物"
                    if key == "merge" else "已取消当前步骤（后续步骤未启动）")
            _result_show(page, text, "warn", artifact)
        else:
            text = EB.result_text(call.entry, code)
            level = EB.result_level(call.entry, code)
            if key == "dirpush":
                level = _STATE_LEVELS[state]
                if state == "error" and code == 0:
                    # 引擎 rc=0 但 JSON ok=false（如覆盖失败）：不得显示「完成」。
                    text = "失败 · 引擎报告本次没有生成（详见日志）"
            _result_show(page, text, level, artifact)
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
    """整期装配一键运行。

    默认只跑 1–4 步（DEFAULT_RUN_ALL_KEYS，不含清理/推送）。调用方（确认框）
    可显式勾选 cleantemp/dirpush：清理此时也走 --mode daily 单入口，
    **一次勾选即视为明确选择**（C14：串行默认不清理，显式选择才 daily）。
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
            page.app.toast("输出根在代码仓内，整期运行已停止；请改输出根或勾选只预演")
            page.mark("validation_error", step="all", reason="repo out root")
            return
    # B-375：含 toc 时在派发前就用共享门拦住无效栏目，避免跑到一半才失败。
    if "toc" in selected:
        blocked, hint = toc_column_gate(page)
        if blocked:
            page.app.toast(hint)
            page.mark("validation_error", step="all", reason=hint)
            return
    # 参数在轮到该步时才构建（infocheck 新产出的报告要让 toc 自动发现）。
    page.mark("run_all_submit", steps=list(selected))
    page._run_results = []
    page.page.run_task(_run_sequence, page, selected, {})


def after_step(page, key: str, call: EB.EngineCall, code: int, cancelled: bool,
               artifact: str, last: bool = True, lines: list | None = None,
               started: float | None = None) -> None:
    """每一步结束后补记结构化结果（显示与状态收尾由 _run_sequence 负责）。

    last 表示这是本次运行的最后一步：toc 结束（成功/失败/取消）时页面据此
    决定是否收起已自动展开的日志 dock（整期串行中间步保持展开）。
    """
    if key == "cleantemp":
        # T3：整期串行里显式勾选清理时，同一份 JSON 也落成正式结果（绑 root+
        # operationId），cancelled/partial 同样刷新真实结果态，不能只看 rc0。
        panel = getattr(page, "_cleanup", None)
        absorb = getattr(panel, "absorb_result", None)
        if callable(absorb) and not getattr(panel, "phase", ""):
            try:
                absorb(lines or [], code=code, cancelled=cancelled,
                       artifact=artifact, started=started)
            except Exception:  # noqa: BLE001 页面桩/旧测试兼容
                pass
        page.mark("cleantemp_done", code=code, artifact=artifact)
        return
    if cancelled:
        # OBS-2/D（B-375）/ P04 R05-2：任何步骤被停止都刷新自己的结果区，显式标
        # 「本次已停止/取消」，不能停留在上一轮结果上让用户误以为本轮成功。
        cancel_hook = getattr(page, "on_step_cancelled", None)
        if callable(cancel_hook):
            try:
                cancel_hook(key)
            except Exception:  # noqa: BLE001 页面桩/旧测试兼容
                pass
        return
    # B-375：整期串行时 toc 可能不是当前视图，由页面决定是否已收尾到可操作状态。
    if key == "toc":
        try:
            page._toc_run_last = bool(last)
        except Exception:  # noqa: BLE001 页面桩/旧测试兼容
            pass
    # 批 116：03 编译成功后由页面决定是否把详情滚到 04（不自动运行 04）。
    hook = getattr(page, "on_step_finished", None)
    if callable(hook):
        try:
            hook(key, code)
        except Exception:  # noqa: BLE001 页面桩/旧测试兼容
            pass
    if key == "toc" and code == 0:
        report = page.field("toc_report")
        payload = TOC.read_result(report) if report else {}
        page.mark("toc_result", ok=bool(payload), pages=(payload.get("pages") or {}),
                  warnings=len(payload.get("warnings") or []))
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
