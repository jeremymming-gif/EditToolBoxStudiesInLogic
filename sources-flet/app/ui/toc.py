"""生成目录：预览模板/篇目 → 编辑栏目表（目录设置.json）→ 落位编译并读结果 JSON。

排版与落位逻辑全在 `Engine/toc.py`；本模块只做取值、拼参数、显示。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from services import toc_settings as TS
from ui import components as C

PREVIEW_HINT = "预览只读：显示模板来源与篇目清单，不写任何文件。"
TEXT_ONLY_HINT = "「仅生成 tex」不落位不编译；要 PDF 请点「生成并编译」。"


def latest_report(issue: str) -> str:
    try:
        reports = sorted(Path(issue).glob("信息检查*.txt"), key=lambda p: p.stat().st_mtime)
        return str(reports[-1]) if reports else ""
    except OSError:
        return ""


def article_rows(payload: dict) -> list[dict]:
    """从 infocheck --preview 的 JSON 取篇目（文件夹名 + 标题）。"""
    rows: list[dict] = []
    for raw in (payload or {}).get("articles") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("folderName") or raw.get("folder") or "").strip()
        title = str(raw.get("chineseTitle") or raw.get("englishTitle") or "").strip()
        title = " ".join(title.split())
        if name:
            rows.append({"name": name, "title": title})
    return rows


def list_label(row: dict, limit: int = 34) -> str:
    title = row.get("title") or ""
    short = title[:limit] + ("…" if len(title) > limit else "")
    return f"{row.get('name', '')} {short}".strip()


def preview_lines(payload: dict) -> list[str]:
    payload = payload or {}
    lines = [f"模板来源：{payload.get('templateSource') or '（未知）'}"]
    note = str(payload.get("templateNote") or "").strip()
    if note:
        lines.append(note)
    articles = payload.get("columnArticles") or []
    lines.append(f"栏目：{'已启用' if payload.get('columnEnabled') else '本期无栏目'}"
                 f" · 已选 {len(articles)} 篇")
    return lines


def result_paths(payload: dict) -> list[tuple[str, str]]:
    placed = (payload or {}).get("placed") or {}
    pairs = [
        ("中文目录 tex", placed.get("zhTex", "")),
        ("中文目录 PDF", placed.get("zhPdf", "")),
        ("英文目录 tex", placed.get("enTex", "")),
        ("英文目录 PDF", placed.get("enPdf", "")),
    ]
    return [(label, str(path)) for label, path in pairs if path]


def result_lines(payload: dict) -> list[str]:
    payload = payload or {}
    lines: list[str] = []
    pages = payload.get("pages") or {}
    if pages:
        lines.append(f"页数：中文 {pages.get('zh', 0)} 页 / 英文 {pages.get('en', 0)} 页")
    column = payload.get("column") or {}
    if column.get("enabled"):
        lines.append(f"本期栏目：{column.get('title', '')}")
    if payload.get("manualEdits"):
        lines.append(f"人工改动 {len(payload.get('manualEdits') or [])} 处（见结果 JSON）")
    else:
        lines.append("人工改动：无")
    for warning in payload.get("warnings") or []:
        lines.append(f"⚠️ {warning}")
    return lines


def first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


class TocPanel:
    """目录步骤的预览/栏目表/生成 UI；状态保存在实例上，切步骤不丢。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = page.app
        self.t = page.t
        self.payload: dict = {}
        self.values: dict = TS.defaults()
        self.selected: list[str] = []
        self.available: list[dict] = []
        self.info = ft.Container()
        self.feedback = ft.Container()
        self.editor_box = ft.Column(spacing=self.t.sp("1"))
        self.kind = ft.Dropdown(
            label="栏目类型", width=self.t.px(160), dense=True,
            text_size=self.t.font("body"),
            options=[ft.DropdownOption(key=key, text=TS.KIND_LABELS[key])
                     for key in TS.COLUMN_KINDS],
            on_select=lambda e: self._set_value("columnKind", self.kind.value),
        )
        self.add_box = ft.Dropdown(label="可加入的篇目", width=self.t.px(300), dense=True,
                                   text_size=self.t.font("body"), options=[])

    # ── 参数区 ─────────────────────────────────────────────────────────
    def params(self) -> list:
        options = self.app.store.get("options", {}) or {}
        self.editor_box.controls = self._editor_controls()
        return [
            C.notice_bar(PREVIEW_HINT, self.t, "info"),
            self.page.add_picker("信息检查报告", "toc_report", "期目录下的 信息检查*.txt",
                                 kind="file"),
            self.page.add_picker("目录模板（可空）", "toc_template",
                                 "指定模板期目录；空 = 本期成品 → 上一期 → 内置",
                                 kind="dir", required=False),
            self.page.add_picker("目录设置 JSON（可空）", "toc_settings",
                                 "默认读写报告同目录的 目录设置.json", kind="file",
                                 required=False),
            C.option_row("仅生成 tex（不落位、不编译）", TEXT_ONLY_HINT, self.t,
                         icon="DESCRIPTION_OUTLINED",
                         checked=bool(options.get("toc_text_only", True)),
                         on_change=lambda e: self.app.set_option("toc_text_only",
                                                                 e.control.value)),
            ft.Row(spacing=self.t.sp("1"), controls=[
                C.primary_button("预览", lambda e: self.page.page.run_task(self._preview_task),
                                 self.t, icon="PREVIEW"),
                C.secondary_button("读取栏目表", lambda e: self._load_settings(), self.t,
                                   icon="UPLOAD_FILE"),
                C.secondary_button("保存栏目表", lambda e: self._save_settings(), self.t,
                                   icon="SAVE_OUTLINED"),
                C.primary_button("生成并编译",
                                 lambda e: self.page.page.run_task(self._generate_task),
                                 self.t, icon="PLAY_ARROW"),
            ]),
            self.info,
            self.editor_box,
            self.feedback,
        ]

    def _set_value(self, key: str, value) -> None:
        self.values[key] = str(value or "").strip()

    def _report(self) -> str:
        report = self.page.field("toc_report")
        if not report:
            report = latest_report(self.page.field("issue_dir"))
            if report:
                self.page.set_field("toc_report", report)
        return report

    def _settings_path(self) -> str:
        explicit = self.page.field("toc_settings")
        if explicit:
            return explicit
        report = self._report()
        return TS.path_for(os.path.dirname(report)) if report else ""

    # ── 栏目表 ─────────────────────────────────────────────────────────
    def _load_settings(self) -> None:
        path = self._settings_path()
        if not path:
            self._notice("请先选信息检查报告或目录设置文件", "warn")
            return
        data, error = TS.read(path)
        if error:
            self._notice(error, "error")
            self.page.mark("toc_settings_error", path=path, error=error)
            return
        self.values = data
        self.selected = list(data.get("columnArticles") or [])
        self.editor_box.controls = self._editor_controls()
        self._notice(f"已读入：{path}", "info")
        self.page.mark("toc_settings_loaded", path=path, articles=len(self.selected))
        self._safe_update(self.editor_box)

    def _save_settings(self) -> None:
        path = self._settings_path()
        data = dict(self.values)
        data["columnArticles"] = list(self.selected)
        ok, message = TS.save(path, data)
        if not ok:
            self._notice(message, "error")
            self.page.mark("toc_settings_error", path=path, error=message)
            return
        self._notice(f"已保存：{message}（原文件已备份）", "success")
        self.page.mark("toc_settings_saved", path=message, articles=len(self.selected),
                       kind=data.get("columnKind", ""))

    def _editor_controls(self) -> list:
        t = self.t
        self.kind.value = str(self.values.get("columnKind") or "")
        if self.kind.value not in TS.COLUMN_KINDS:
            self.kind.options = [ft.DropdownOption(key=key, text=TS.KIND_LABELS[key])
                                 for key in TS.COLUMN_KINDS]
            self.kind.options.append(ft.DropdownOption(key=self.kind.value, text=self.kind.value))
        else:
            self.kind.options = [ft.DropdownOption(key=key, text=TS.KIND_LABELS[key])
                                 for key in TS.COLUMN_KINDS]
        name = self._field("columnName", "栏目名称（中文）")
        name_en = self._field("columnNameEN", "栏目英文名（可空）")
        host = self._field("columnHost", "主持人（可空）", width=200)
        rows: list = [
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[self.kind, name, name_en, host]),
        ]
        if not self.selected:
            rows.append(C.text("栏目篇目：尚未选择。预览后从下拉里加入，或读入已有栏目表。",
                               t, "caption", t.palette.text_faint))
        for index, article in enumerate(self.selected):
            rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                C.text(article, t, "small", weight="medium"),
                ft.Container(expand=True),
                C.text_button("↑", lambda e, i=index: self._move(i, -1), t),
                C.text_button("↓", lambda e, i=index: self._move(i, 1), t),
                C.text_button("删除", lambda e, i=index: self._remove(i), t),
            ]))
        remaining = [row["name"] for row in self.available if row["name"] not in self.selected]
        self.add_box.options = [ft.DropdownOption(key=name, text=name) for name in remaining]
        self.add_box.value = remaining[0] if remaining else ""
        if remaining:
            rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[self.add_box,
                                         C.secondary_button("加入栏目",
                                                            lambda e: self._add(), t)]))
        else:
            rows.append(C.text("没有可加入的篇目：先点「预览」取篇目清单。", t, "caption",
                               t.palette.text_faint))
        return rows

    def _field(self, key: str, label: str, width: float = 220) -> ft.TextField:
        return ft.TextField(
            label=label, value=str(self.values.get(key) or ""), dense=True,
            text_size=self.t.font("body"), width=self.t.px(width),
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                    side=ft.BorderSide(1, self.t.palette.border)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                    side=ft.BorderSide(2, self.t.palette.accent)),
            },
            content_padding=C._pad(self.t, 10, 8, 10, 8),
            on_change=lambda e, k=key: self._set_value(k, e.control.value),
        )

    def _move(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= target < len(self.selected):
            self.selected[index], self.selected[target] = self.selected[target], self.selected[index]
            self._render_editor()

    def _remove(self, index: int) -> None:
        if 0 <= index < len(self.selected):
            self.selected.pop(index)
            self._render_editor()

    def _add(self) -> None:
        name = self.add_box.value or ""
        if name and name not in self.selected:
            self.selected.append(name)
            self._render_editor()

    def _render_editor(self) -> None:
        self.editor_box.controls = self._editor_controls()
        self._safe_update(self.editor_box)
        self.page.mark("toc_editor", articles=len(self.selected))

    # ── 预览 / 生成 ────────────────────────────────────────────────────
    async def _preview_task(self) -> None:
        report = self._report()
        if not report:
            self._notice("请先选信息检查报告（可先运行「检查 tex 信息」）", "error")
            self.page.mark("validation_error", step="toc", reason="missing report")
            return
        self.info.content = C.text("预览中…", self.t, "small", self.t.palette.text_muted)
        call = EB.toc_call(report=report, template=self.page.field("toc_template"),
                           settings=self.page.field("toc_settings"), preview=True)
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, timeout=180,
                                             notes=self.app.notes_repo)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "json": {}}
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self._notice(f"预览失败：{first_line(result.get('error')) or '引擎没有返回 JSON'}",
                         "error")
            self.page.mark("toc_preview", ok=False, error=str(result.get("error") or ""))
            return
        self.payload = payload
        self.values["columnArticles"] = list(self.payload.get("columnArticles") or [])
        if not self.selected:
            self.selected = list(self.values["columnArticles"])
        if not self.available:
            await self._load_articles()
        self.info.content = C.card(ft.Column(spacing=self.t.sp("half"), controls=[
            C.text(line, self.t, "small") for line in preview_lines(payload)
        ]), self.t)
        self._render_editor()
        self._safe_update(self.info)
        self.page.mark("toc_preview", ok=True,
                       source=payload.get("templateSource"),
                       column=bool(payload.get("columnEnabled")),
                       articles=len(self.available))

    async def _load_articles(self) -> None:
        issue = self.page.field("issue_dir")
        if not issue:
            return
        try:
            result = await asyncio.to_thread(
                EB.read_json_command, EB.infocheck_call(folder=issue, preview=True), timeout=180)
        except Exception:  # noqa: BLE001
            return
        self.available = article_rows(result.get("json") or {})
        if not self.available:
            self._notice("篇目清单为空：先运行「检查 tex 信息」生成报告", "warn")

    async def _generate_task(self) -> None:
        report = self._report()
        if not report:
            self._notice("请先选信息检查报告（可先运行「检查 tex 信息」）", "error")
            self.page.mark("validation_error", step="toc", reason="missing report")
            return
        result_path = os.path.join(os.path.dirname(report), "目录结果.json")
        call = EB.toc_call(report=report, template=self.page.field("toc_template"),
                           settings=self.page.field("toc_settings"), text_only=False,
                           result_json=result_path)
        self.feedback.content = C.text("生成并编译中…（首次编译约 1–3 分钟）", self.t, "small",
                                       self.t.palette.text_muted)
        self._safe_update(self.feedback)
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, timeout=900,
                                             notes=self.app.notes_repo)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "json": {}}
        payload = result.get("json") or EB.toc_result(result_path)
        ok = bool(result.get("ok")) and bool(payload)
        rows: list = [C.text(line, self.t, "small") for line in result_lines(payload)]
        warnings = [line for line in result_lines(payload) if line.startswith("⚠️")]
        if not ok:
            rows.append(C.notice_bar(f"生成失败：{first_line(result.get('error')) or '没有结果 JSON'}",
                                     self.t, "error"))
        for label, path in result_paths(payload):
            rows.append(ft.Row(spacing=self.t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                C.text(label, self.t, "small", self.t.palette.text_muted),
                C.text(path, self.t, "caption", self.t.palette.text_faint),
                ft.Container(expand=True),
                C.text_button("打开", lambda e, p=path: self.app.open_path(p), self.t),
            ]))
        rows.append(ft.Row(spacing=self.t.sp("2"), controls=[
            C.text("结果 JSON", self.t, "small", self.t.palette.text_muted),
            ft.Container(expand=True),
            C.text_button("打开", lambda e: self.app.open_path(result_path), self.t),
        ]))
        self.feedback.content = C.card(ft.Column(spacing=self.t.sp("1"), controls=rows), self.t)
        self._safe_update(self.feedback)
        if ok:
            self.app.store.set_recent("toc_report", report)
        self.page.mark("toc_generated", ok=ok, result=result_path,
                       warnings=len(warnings),
                       zh_pdf=bool((payload.get("placed") or {}).get("zhPdf")),
                       error=str(result.get("error") or ""))

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _notice(self, message: str, tone: str = "info") -> None:
        self.feedback.content = C.notice_bar(message, self.t, tone)
        self._safe_update(self.feedback)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass
