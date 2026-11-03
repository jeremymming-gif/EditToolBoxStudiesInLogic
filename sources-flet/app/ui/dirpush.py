"""目录摘要推送（当期 / 年度）：只拼参数、读引擎 JSON、做代码仓内确认。

生成逻辑全在 `Engine/dirpush.py`；本模块不解析目录、不复制推送排版。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C

MODE_ISSUE = "issue"
MODE_ANNUAL = "annual"


def build_call(*, issue: str, out_root: str = "", annual: bool = False,
               year: str = "", include_issues: str = "",
               with_en_toc: bool = False, dry_run: bool = False) -> EB.EngineCall:
    return EB.dirpush_call(
        issue=issue, out_root=out_root, with_en_toc=with_en_toc, annual=annual,
        year=year, include_issues=include_issues, dry_run=dry_run,
    )


def repo_warning(out_root: str, repo=None) -> str:
    """输出根落在代码仓内时返回中文警告；空输出根 / 仓外返回空串。"""
    text = str(out_root or "").strip()
    if not text:
        return ""
    try:
        root = Path(repo).resolve() if repo else EB.repo_root().resolve()
        target = Path(text).expanduser().resolve()
    except (OSError, RuntimeError):
        return ""
    try:
        if target == root or root in target.parents:
            return (f"输出根在代码仓内：{target}。生成会在仓库里留下未跟踪的 docx 文件，"
                    "污染工作树。")
    except OSError:
        return ""
    return ""


def output_files(payload: dict) -> list[dict]:
    files: list[dict] = []
    for raw in (payload or {}).get("outputs") or []:
        path = str(raw or "")
        if not path:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        files.append({"path": path, "name": os.path.basename(path), "size": size})
    return files


def size_text(size: int) -> str:
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def summary_lines(payload: dict) -> list[str]:
    """把引擎 JSON 转成给人看的中文摘要（不含任何排版判断）。"""
    payload = payload or {}
    lines: list[str] = []
    issue = payload.get("issue") or {}
    if issue:
        head = (f"{issue.get('year')} 年第 {issue.get('number')} 期"
                f"（第 {issue.get('vol')} 卷，总第 {issue.get('total')} 期）")
        lines.append(head)
    annual = payload.get("annual") or {}
    if annual:
        included = annual.get("included") or []
        lines.append(f"年度：{annual.get('year')} 年，纳入 {len(included)} 期："
                     + "、".join(str(n) for n in included))
        if annual.get("missing_issues"):
            lines.append("缺期：" + "、".join(str(n) for n in annual.get("missing_issues") or []))
    counts = payload.get("counts") or {}
    if counts:
        lines.append(f"目录条目：中文 {counts.get('zh_entries', 0)} / "
                     f"英文 {counts.get('en_entries', 0)}；"
                     f"文章 {counts.get('articles', 0)} 篇")
    for item in output_files(payload):
        lines.append(f"{'计划输出' if payload.get('dry_run') else '输出'}："
                     f"{item['path']}（{size_text(item['size'])}）")
    if payload.get("dry_run"):
        lines.append("dry-run：未写任何文件。")
    if not payload.get("ok", True):
        lines.append("引擎报告：本次没有生成（详见日志与下面的提示）。")
    for text in payload.get("warnings") or []:
        lines.append(f"⚠️ {text}")
    return lines


class DirPushPanel:
    """当期 / 年度推送参数与结果摘要；输出根记忆在 recent.dirpush_out。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = page.app
        self.t = page.t
        self.feedback = ft.Container()
        self.mode = C.select(
            self.t, value=self.app.store.get("form.dirpush_mode", MODE_ISSUE) or MODE_ISSUE,
            width=self.t.px(180), label="推送范围",
            options=[(MODE_ISSUE, "当期目录摘要"), (MODE_ANNUAL, "年度总目录")],
            on_select=self._mode_changed,
        )
        self.year = C.text_field(
            self.t, value=self.app.store.get("form.dirpush_year", "") or "",
            width=self.t.px(150),
            on_change=lambda e: self._store_value("form.dirpush_year", e.control.value),
        )
        self.issues = C.text_field(
            self.t, value=self.app.store.get("form.dirpush_issues", "") or "",
            expand=True,
            on_change=lambda e: self._store_value("form.dirpush_issues", e.control.value),
        )
        self.annual_rows = ft.Column(spacing=self.t.sp("1"), visible=False)
        self.annual_rows.controls = [ft.Row(spacing=self.t.sp("1"), controls=[
            C.form_field(self.t, "年度（yyyy，可空）", self.year),
            C.form_field(self.t, "只含这些期号（如 1,2,3；年度可空）", self.issues, expand=True),
        ])]

    def _store_value(self, key: str, value) -> None:
        self.app.store.set(key, str(value or "").strip())

    def _mode_changed(self, e=None) -> None:
        value = (self.mode.value or MODE_ISSUE)
        self._store_value("form.dirpush_mode", value)
        self.annual_rows.visible = value == MODE_ANNUAL
        try:
            self.annual_rows.update()
        except Exception:  # noqa: BLE001
            pass

    def params(self) -> list:
        options = self.app.store.get("options", {}) or {}
        self.annual_rows.visible = (self.mode.value or MODE_ISSUE) == MODE_ANNUAL
        return [
            C.notice_bar("先预览（--dry-run）不写文件；输出根落在代码仓内会二次确认。",
                         self.t, "info"),
            self.page.add_picker("输出根", "dirpush_out", "目录摘要 docx 输出根（可空 = 引擎默认）",
                                 kind="dir", required=False),
            ft.Row(spacing=self.t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[C.form_field(self.t, "推送范围", self.mode)]),
            self.annual_rows,
            C.option_row("含英文目录", "当期模式加 --with-en-toc", self.t, icon="TRANSLATE",
                         checked=bool(options.get("dirpush_with_en_toc", False)),
                         on_change=lambda e: self.app.set_option("dirpush_with_en_toc",
                                                                 e.control.value)),
            C.option_row("只预演（--dry-run）", "采集并校验，不写文件", self.t,
                         icon="SCIENCE_OUTLINED",
                         checked=bool(options.get("dirpush_dry_run", False)),
                         on_change=lambda e: self.app.set_option("dirpush_dry_run",
                                                                 e.control.value)),
            ft.Row(spacing=self.t.sp("1"), controls=[
                C.primary_button("预览/生成", lambda e: self.start(False), self.t,
                                 icon="PLAY_ARROW"),
                C.secondary_button("只看预览（dry-run）", lambda e: self.start(True), self.t,
                                   icon="SCIENCE_OUTLINED"),
            ]),
            self.feedback,
        ]

    def values(self) -> dict:
        return {
            "out_root": self.page.field("dirpush_out"),
            "annual": (self.mode.value or MODE_ISSUE) == MODE_ANNUAL,
            "year": (self.year.value or "").strip(),
            "include_issues": (self.issues.value or "").strip(),
            "with_en_toc": bool((self.app.store.get("options", {}) or {})
                                .get("dirpush_with_en_toc", False)),
            "dry_run": bool((self.app.store.get("options", {}) or {})
                            .get("dirpush_dry_run", False)),
        }

    def start(self, dry_run: bool = False) -> None:
        if self.app.running:
            self.app.toast("上一任务仍在运行，先停止或等它结束")
            self.page.mark("validation_error", step="dirpush", reason="busy")
            return
        issue = self.page.field("issue_dir")
        if not issue:
            self.app.toast("请先选择期目录")
            self.page.mark("validation_error", step="dirpush", reason="missing issue dir")
            return
        values = self.values()
        call = build_call(issue=issue, out_root=values["out_root"],
                          annual=values["annual"], year=values["year"],
                          include_issues=values["include_issues"],
                          with_en_toc=values["with_en_toc"],
                          dry_run=bool(dry_run or values["dry_run"]))
        warn = repo_warning(values["out_root"])
        if warn:
            self.feedback.content = C.notice_bar(warn, self.t, "warn")
            try:
                self.feedback.update()
            except Exception:  # noqa: BLE001
                pass

        def _go() -> None:
            self.page.mark("dirpush_submit",
                           mode=MODE_ANNUAL if values["annual"] else MODE_ISSUE,
                           dry_run=call.argv.count("--dry-run") > 0)
            self.page._status["dirpush"] = "running"
            self.page._refresh_steps()
            self.page.page.run_task(self.page._run_calls, [("dirpush", call)])

        if warn and not (dry_run or values["dry_run"]) and not getattr(self.app, "auto_confirm", False):
            def _confirmed() -> None:
                self.page.mark("confirm_repo_out", path=values["out_root"])
                _go()

            C.confirm(self.page.page, self.t, title="输出根在代码仓内",
                      message=warn + " 仍要生成吗？", confirm_label="继续生成",
                      on_confirm=_confirmed)
            return
        _go()
