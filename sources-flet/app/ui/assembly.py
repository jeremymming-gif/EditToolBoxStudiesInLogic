"""整期装配：合并/编译/三档清理与撤销/目录/推送的取值与编排。

文档逻辑全在 `Engine/`；本模块只拼 `EngineCall`、确认危险操作、显示结果。
清理只暴露 `--trash`（可恢复）与 `--restore`（撤销），不提供永久删除参数。
"""
from __future__ import annotations

import asyncio
import os
import re
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
    return bool((page.app.store.get("options", {}) or {}).get("cleantemp_dry_run", False))


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
        rows += [
            page._meta_field("compile_only", "只编这几篇（如 01,03；可空）", 260),
            C.option_row("只预演（--dry-run）", "列出将要编译的篇目，不真跑 xelatex", t,
                         icon="SCIENCE_OUTLINED",
                         checked=bool(options.get("compileall_dry_run", False)),
                         on_change=lambda e: page.app.set_option("compileall_dry_run",
                                                                 e.control.value)),
        ]
    elif key == "merge":
        options = page.app.store.get("options", {}) or {}
        rows += [
            page._meta_field("merge_out", "输出文件（可空）", 420),
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
        ]
    elif key == "cleantemp":
        rows += _cleantemp_params(page)
    elif key == "dirpush":
        rows += dirpush_panel(page).params()
    engine_note = C.text(f"引擎：{page.app.engine_status.describe()}", t, "caption",
                         t.palette.text_faint)
    engine_note.max_lines = 2
    engine_note.overflow = ft.TextOverflow.ELLIPSIS
    rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
        C.primary_button("运行这一步", lambda e, k=key: run_step(page, k), t, icon="PLAY_ARROW",
                         disabled=not page.app.engine_status.ok),
        ft.Container(expand=True, content=engine_note),
    ]))
    return ft.Column(spacing=t.sp("1"), controls=rows)


def _cleantemp_params(page) -> list:
    t = page.t
    mode = cleantemp_mode(page)
    mode_box = ft.Dropdown(
        label="清理档位", value=mode, width=t.px(300), dense=True,
        text_size=t.font("body"),
        options=[ft.DropdownOption(key=key, text=MODE_LABELS[key]) for key in MODE_KEYS],
        on_select=lambda e: _set_mode(page, e.control.value),
    )
    rows: list = [
        C.notice_bar("清理一律移入 `<期目录>/.toolbox-trash/`，可撤销；本页不提供永久删除。",
                     t, "info"),
        mode_box,
        C.option_row("只预演（--dry-run）", "列出将清理的文件与数量，不真删", t,
                     icon="SCIENCE_OUTLINED", checked=cleantemp_dry_run(page),
                     on_change=lambda e: page.app.set_option("cleantemp_dry_run",
                                                             e.control.value)),
        ft.Row(spacing=t.sp("1"), controls=[
            C.primary_button("预览清理", lambda e: page.page.run_task(_preview_task, page), t,
                             icon="PREVIEW"),
            C.secondary_button("开始清理", lambda e: run_step(page, "cleantemp"), t,
                               icon="CLEANING_SERVICES"),
        ]),
        _restore_row(page),
    ]
    return rows


def _set_mode(page, value) -> None:
    mode = str(value or "aux")
    if mode not in MODE_KEYS:
        mode = "aux"
    page.app.store.set("form.cleantemp_mode", mode)
    page.app.persist()
    page.mark("cleantemp_mode", mode=mode)


def _restore_row(page) -> ft.Control:
    t = page.t
    issue = page.field("issue_dir")
    choices = trash_dirs(issue) if issue else []
    if not choices:
        return C.text("撤销清理：暂无回收目录（清理后出现在 .toolbox-trash/）", t, "caption",
                      t.palette.text_faint)
    selected = str(page.app.store.get("form.cleantemp_restore", "") or "")
    names = [str(path) for path in choices]
    if selected not in names:
        selected = names[0]
    dropdown = ft.Dropdown(
        label="回收目录", value=selected, width=t.px(320), dense=True,
        text_size=t.font("body"),
        options=[ft.DropdownOption(key=str(path), text=path.name) for path in choices],
        on_select=lambda e: page.app.store.set("form.cleantemp_restore",
                                               str(e.control.value or "")),
    )
    return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[
        C.text("撤销清理", t, "body"),
        dropdown,
        C.secondary_button("恢复所选目录", lambda e: restore(page, dropdown.value or ""), t,
                           icon="RESTORE"),
    ])


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
                           text_only=bool(options.get("toc_text_only", True)),
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
    page.page.run_task(page._run_calls, [(key, call)])


def run_all(page) -> None:
    if page.app.running:
        return
    if not page.field("issue_dir"):
        page.app.toast("请先选择期目录")
        page.mark("validation_error", step="all", reason="missing issue dir")
        return
    warning = DIRPUSH.repo_warning(page.field("dirpush_out"))
    if warning and not _dirpush_dry(page):
        page.app.toast("输出根在代码仓内，整期流水线已停止；请改输出根或勾选只预演")
        page.mark("validation_error", step="all", reason="repo out root")
        return
    try:
        calls = []
        for step in page.STEPS:
            key = step["key"]
            if key == "cleantemp":
                calls.append((key, EB.cleantemp_call(folder=page.field("issue_dir"),
                                                     mode=cleantemp_mode(page), dry_run=True)))
                continue
            calls.append((key, build_step_call(page, key)))
    except (ValueError, RuntimeError) as exc:
        page.app.toast(str(exc))
        page.mark("validation_error", step="all", reason=str(exc))
        return
    page.mark("run_all_submit", steps=[key for key, _ in calls])
    page.page.run_task(page._run_calls, calls, True)


def after_step(page, key: str, call: EB.EngineCall, code: int, cancelled: bool,
               artifact: str) -> None:
    """流水线每步结束后补记结构化结果（真正的显示仍由 RunCalls 负责）。"""
    if cancelled:
        return
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
        match = re.search(r"(\d+)\s*个", str(line))
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
    page._result.show(text, level=level, artifact=str(trash) if trash.is_dir() else "")
    try:
        page.page.update()
    except Exception:  # noqa: BLE001
        pass
    page.mark("cleantemp_preview", ok=ok, files=parse_count(lines),
              mode=cleantemp_mode(page), error=error)


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
