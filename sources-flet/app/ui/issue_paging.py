"""Preview, apply and recover a page refresh for the current issue."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C


@dataclass(frozen=True)
class RefreshOptions:
    regenerate_toc: bool
    order_result: str
    order_json: str
    only: str = ""

    def call(self, panel, *, plan_id: str = "") -> EB.EngineCall:
        return EB.refreshpages_call(
            issue=panel.issue, plan_id=plan_id,
            regenerate_toc=self.regenerate_toc, order_result=self.order_result,
            order_json=self.order_json, on_change=bool(self.only), only=self.only,
        )


class PagingPanel:
    def __init__(self, page) -> None:
        self.page = page
        self.app = page.app
        self.t = page.t
        self.issue = self._current_issue()
        self.binding = page.current_artifact_binding()
        self.preview: dict | None = None
        self.preview_options: RefreshOptions | None = None
        self.result: dict = {}
        self.regenerate_toc = False
        self.order_result = ""
        self.only = ""
        self.pending = False
        self.message = ""
        self.selection: dict | None = None
        self.selection_message = ""
        self.selection_generation = -1
        self.selection_seq = 0
        self.selection_in_flight = 0
        self.summary_text = ""
        self.view = None
        self.action_buttons = []

    def _current_issue(self) -> str:
        return str(self.page.field("issue_dir") or "").strip()

    def _is_current(self) -> bool:
        return (self.issue == self._current_issue()
                and self.binding == self.page.current_artifact_binding())

    def _editable(self) -> bool:
        return (self._is_current() and not self.pending and not self.app.running
                and self.app.engine_status.ok)

    def options(self) -> RefreshOptions:
        order_panel = getattr(self.page, "_issue_order_panel", None)
        result = getattr(order_panel, "refresh_order_result", None)
        order_json = (json.dumps(result, ensure_ascii=False, sort_keys=True)
                      if result and not self.order_result else "")
        return RefreshOptions(self.regenerate_toc, self.order_result, order_json, self.only)

    def controls(self) -> ft.Control:
        t = self.t
        self.preview_button = C.primary_button("编译并刷新页码", self._preview, t)
        self.toc_checkbox = ft.Checkbox(
            label="重生成目录", value=self.regenerate_toc, on_change=self._toc_changed,
        )
        self.order_input = C.text_field(t, value=self.order_result, expand=True,
                                        on_change=self._order_changed)
        self.only_input = C.text_field(t, hint_text="如 01,03", expand=True,
                                       value=self.only, on_change=self._only_changed)
        self.order_note = C.text("", t, "caption", t.palette.text_muted)
        self.selection_view = C.text("", t, "small", t.palette.text_muted)
        self.preview_view = ft.Column(spacing=t.sp("half"))
        self.result_view = ft.Column(spacing=t.sp("half"))
        self.message_view = C.text("", t, "small", t.palette.text_muted)
        self.view = ft.Column(spacing=t.sp("1"), controls=[
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[self.preview_button, self.toc_checkbox]),
            C.form_field(t, "排序结果 JSON 路径（可选）", self.order_input),
            C.form_field(t, "只编这几篇", self.only_input),
            self.order_note,
            self.selection_view,
            self.message_view,
            self.preview_view,
            self.result_view,
        ])
        self._render()
        return self.view

    def refresh_running(self) -> None:
        if self.view is None:
            return
        disabled = not self._editable()
        for control in (self.preview_button, self.toc_checkbox, self.order_input,
                        self.only_input, *self.action_buttons):
            control.disabled = disabled
        self.toc_checkbox.disabled = disabled or bool(self.only.strip())
        self.only_input.disabled = disabled or self.regenerate_toc
        self.page._safe_update(self.view)

    def _render(self) -> None:
        if self.view is None:
            return
        options = self.options()
        if options.order_json:
            self.order_note.value = "将使用本期已确认的排序和排除清单。"
        elif self.order_result:
            self.order_note.value = "将读取填写的排序结果 JSON；若已过期请清空后重新预览。"
        else:
            self.order_note.value = "若本期排序时排除了目录，重启后请填写排序结果 JSON，或回到排序步骤重新确认。"
        self.selection_view.value = self.summary_text
        self.message_view.value = self.message
        self.action_buttons = []
        current = self._is_current()
        self.preview_view.controls = self._preview_controls() if current else []
        self.result_view.controls = self._result_controls() if current else []
        self.refresh_running()
        self.page._safe_update(self.view)

    def _clear_preview(self) -> None:
        self.preview = None
        self.preview_options = None

    def _options_changed(self) -> None:
        self._clear_preview()
        self.message = "刷新选项已变化，请重新预览。"
        self.summary_text = ""
        self.selection = None
        # 重试/修正后清除红字（错误与警告），但保留恢复入口信息。
        if self.result and (self.result.get("warnings") or self.result.get("error")):
            self.result = {**self.result, "warnings": [], "error": ""}
        self._render()

    def _toc_changed(self, e) -> None:
        if self._editable():
            self.regenerate_toc = bool(e.control.value)
            self._options_changed()

    def _order_changed(self, e) -> None:
        if self._editable():
            self.order_result = str(e.control.value or "").strip()
            self._options_changed()

    def _only_changed(self, e) -> None:
        if self._editable():
            self.only = str(e.control.value or "").strip()
            self._options_changed()

    def refresh_selection(self) -> None:
        """后台读取当前期编号目录摘要；同一在途请求幂等，不占前台运行槽。"""
        if not self._is_current() or not self.issue:
            return
        if self.selection_in_flight:
            return
        seq = self.selection_seq + 1
        self.selection_seq = seq
        generation = self.page.current_artifact_binding()
        try:
            # 摘要顺序：显式 JSON 路径 → 本期仍绑定的改名回执 → 引擎 saved/implicit。
            # 未应用的排序编辑不吊销上一次成功回执（由 order panel 侧维护）。
            call = EB.refreshpages_call(
                issue=self.issue, selection_summary=True,
                order_result=self.order_result,
                order_json=(self.options().order_json if not self.order_result else ""),
            )
        except (ValueError, RuntimeError) as exc:
            self.selection_message = str(exc)
            self.summary_text = ""
            self.selection = None
            self._render()
            return
        self.selection_in_flight = seq
        self.summary_text = "正在读取编号目录"
        self.selection = None
        self.selection_message = ""
        self._render()

        async def run() -> None:
            code, payload = -1, {}
            try:
                code, payload = await EB.run_json_call(call, on_line=None, on_proc=None)
            except (OSError, RuntimeError, ValueError) as exc:
                payload = {"error": str(exc)}
            finally:
                self.selection_in_flight = 0
            if (self.selection_seq != seq or self.page.current_artifact_binding() != generation
                    or self.issue != self._current_issue()):
                return
            payload = payload or {}
            if code == 0 and payload.get("status") == "selection_summary":
                self.selection = payload
                self.selection_message = ""
                numbered = int(payload.get("numbered_count") or 0)
                excluded = int(payload.get("excluded_numbered_count") or 0)
                articles = numbered - excluded
                if not numbered:
                    self.summary_text = "未找到可刷新的编号目录，请先到排序步骤确认篇目。"
                else:
                    self.summary_text = f"本期按当前 {articles} 个编号目录刷新"
                    if excluded:
                        self.summary_text += f"，已排除 {excluded} 个编号目录"
                if payload.get("order_source") == "saved" and excluded:
                    self.summary_text += "。已确认的排除清单生效；若目录变化请重新核对排序"
                verify = str(payload.get("error") or "")
                if "已确认的编号目录发生变化" in verify or "重新预览整期刷新并核准篇目" in verify:
                    self.selection_message = verify + "；请重新核对排序与排除清单"
            else:
                self.selection = None
                self.summary_text = ""
                self.selection_message = str(payload.get("error") or f"读取编号目录失败（退出码 {code}）。")
            self._render()

        self.page.page.run_task(run)

    def invalidate_order(self) -> None:
        """排序/改名成功后调用：取消旧预览与链接，重取摘要。"""
        self.selection_in_flight = 0
        self.selection = None
        self.selection_message = ""
        self._clear_preview()
        self.result = {}
        self.message = ""
        self.summary_text = ""
        self.selection_message = ""
        if self.view is not None:
            self._render()
            self.refresh_selection()

    def _preview(self, e=None) -> None:
        self._schedule("preview", self.options())

    def _ask_apply(self, e=None) -> None:
        preview = self.preview
        options = self.preview_options
        if not self._editable() or preview is None or options is None or not preview.get("plan_id"):
            return
        if options != self.options():
            self._options_changed()
            return

        def confirm() -> None:
            if self.preview is preview and options == self.options():
                self._schedule("apply", options, plan_id=preview["plan_id"])

        if options.only:
            message = "先编选定篇目，页码有变化或旧页码无法核实时会刷新整期及目录。"
        else:
            message = "将编译稿件并更新页码字段、正文 PDF 与目录产物。"
        C.confirm(self.page.page, self.t, title="确认刷新整期页码",
                  message=message, confirm_label="开始编译并刷新", on_confirm=confirm)

    def _recovery_id(self) -> str:
        if (self.result.get("status") == "recovery_required"
                and self.result.get("pending_operation") == "refreshpages"):
            return str(self.result.get("recovery_id") or "")
        return ""

    def _ask_recover(self, e=None) -> None:
        result, recovery_id = self.result, self._recovery_id()
        if not self._editable() or not recovery_id:
            return

        def confirm() -> None:
            if self.result is result:
                self._schedule("recover", self.options(), recovery_id=recovery_id)

        C.confirm(self.page.page, self.t, title="恢复上一轮页码刷新",
                  message="将根据恢复记录核对并还原本轮修改。",
                  confirm_label="开始恢复", on_confirm=confirm)

    def _schedule(self, kind: str, options: RefreshOptions, *,
                  plan_id: str = "", recovery_id: str = "") -> None:
        self.page._ensure_issue_identity()
        if not self._editable() or not self.issue:
            return
        if kind == "preview" and options != self.options():
            return
        if kind == "apply" and (self.preview_options != options
                                or self.preview is None
                                or self.preview.get("plan_id") != plan_id):
            self._options_changed()
            return
        try:
            call = (EB.refreshpages_call(issue=self.issue, recovery_id=recovery_id)
                    if kind == "recover" else options.call(self, plan_id=plan_id))
        except (ValueError, RuntimeError) as exc:
            self.message = str(exc)
            self._render()
            return
        self.pending = True
        self._clear_preview()
        self.result = {}
        self.message = f"正在{call.label}…"
        self._render()
        self.page.page.run_task(self._run, call, kind, options)

    async def _run(self, call: EB.EngineCall, kind: str, options: RefreshOptions) -> None:
        generation = self.page.current_artifact_binding()
        if (not self._is_current() or options != self.options()
                or self.app.begin_run(call) is False):
            self.pending = False
            if self._is_current():
                self.message = "未开始刷新，请重新预览。"
                self._render()
            return

        code = -1
        try:
            code, payload = await EB.run_json_call(
                call, on_line=self.page._on_line, on_proc=self._track_proc)
        except (OSError, RuntimeError, ValueError) as exc:
            payload = {"error": str(exc)}
        finally:
            cancelled = self.app.cancel_requested
            self.pending = False
            self.app.finish_run(call, code)
        if (self.page.current_artifact_binding() == generation and self._is_current()
                and options == self.options()):
            self._consume(kind, code, payload or {}, options, cancelled)
            self._render()

    def _track_proc(self, proc) -> None:
        self.app.track_proc(proc)
        if self.app.cancel_requested:
            EB.kill_tree(proc)

    @staticmethod
    def _can_apply(code: int, payload: dict) -> bool:
        return (code == 0 and payload.get("status") == "preview"
                and bool(payload.get("can_apply") and payload.get("plan_id")))

    def _consume(self, kind: str, code: int, payload: dict, options: RefreshOptions,
                 cancelled: bool) -> None:
        status = payload.get("status")
        self.result = payload
        if self._can_apply(code, payload) and not cancelled:
            self.preview = payload
            self.preview_options = options
            articles = payload.get("articles") or []
            self.summary_text = f"本期按当前 {len(articles)} 个编号目录刷新"
            self.selection = None
            self.selection_message = ""
            self.message = "请核对篇目、当前页码和写入范围。新页码将在编译完成后显示。"
        elif status == "recovery_required":
            self.message = ("刷新中断，需恢复本期文件。" if self._recovery_id()
                            else "本期有待恢复事务，请到对应步骤恢复。")
        elif status == "rolled_back":
            self.message = "已恢复本期文件。" if kind == "recover" else "刷新失败，已回滚。"
        elif status in {"completed", "unchanged"} and code == 0 and not cancelled:
            self.message = ("刷新核验完成，文件无需更新。" if status == "unchanged"
                            else "刷新完成。")
        elif cancelled:
            self.result = {}
            self.message = ("已请求停止，请重新预览核对本期文件。"
                            if kind in {"apply", "recover"} else "已取消预览。")
        elif status == "recovery_required":
            self.message = ("刷新中断，需恢复本期文件。" if self._recovery_id()
                            else "本期有待恢复事务，请到对应步骤恢复。")
        else:
            self.message = str(payload.get("error") or f"刷新未完成（退出码 {code}）。")

    def _action(self, label: str, callback) -> ft.Control:
        button = C.primary_button(label, callback, self.t)
        self.action_buttons.append(button)
        return button

    def _preview_controls(self) -> list[ft.Control]:
        rows = []
        displayed = self.preview or self.result
        if self.selection_message:
            rows.append(C.text(self.selection_message, self.t, "caption"))
        for index, article in enumerate(displayed.get("articles", []), 1):
            old = article.get("old") or {}
            new = article.get("new") or {}
            current = f"{old['first']}–{old['last']}" if old else "未知"
            span = f"{current} → {new['first']}–{new['last']}" if new else current
            rows.append(C.text(f"{index:02d} {article['folder']}　{span}", self.t, "body"))
        if self.preview is None:
            return rows
        strategy = {"updated": "更新已有目录", "regenerated": "重新生成目录",
                    "generated": "生成目录"}.get(self.preview.get("toc_strategy"))
        if strategy:
            rows.append(C.text(strategy, self.t, "small"))
        rows.extend(C.text(str(path), self.t, "mono") for path in self.preview.get("writes", []))
        rows.append(self._action("应用刷新计划", self._ask_apply))
        return rows

    def _open_button(self, label: str, path: Path) -> ft.Control:
        result = self.result
        generation = self.page.current_artifact_binding()

        def open_result(e) -> None:
            self.page._ensure_issue_identity()
            if (self._is_current() and self.result is result
                    and self.page.current_artifact_binding() == generation):
                self.page.open_artifact(str(path), binding=self.binding)
            else:
                self.app.toast("期目录或刷新结果已变化，请使用当前产物入口。")

        return C.text_button(label, open_result, self.t)

    def _result_controls(self) -> list[ft.Control]:
        result = self.result
        rows = []
        diagnostic = result.get("diagnostic_dir") or result.get("diagnostics")
        if diagnostic:
            rows.append(self._open_button("打开诊断目录", Path(diagnostic)))
        for warning in dict.fromkeys(result.get("warnings") or []):
            if warning != self.message:
                rows.append(C.text(str(warning), self.t, "caption"))
        if result.get("recovery_path"):
            rows.append(C.text(str(result["recovery_path"]), self.t, "mono"))
        if self._recovery_id():
            rows.append(self._action("恢复上一轮", self._ask_recover))
        if result.get("status") == "completed":
            writes = set(result.get("writes") or [])
            outputs = [(f"打开 {article['folder']} 正文 PDF", article["pdf"])
                       for article in result.get("articles", [])
                       if article.get("pdf") in writes]
            outputs += [(f"打开 {Path(path).name}", path) for path in result.get("toc_outputs", [])
                        if path in writes]
            issue = Path((result.get("issue") or {}).get("path") or self.issue)
            rows.extend(self._open_button(label, issue / relative) for label, relative in outputs)
        return rows
