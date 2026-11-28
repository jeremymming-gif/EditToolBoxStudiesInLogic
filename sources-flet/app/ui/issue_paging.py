"""Preview, apply and recover a page refresh for the current issue."""
from __future__ import annotations

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
    order_needs_confirmation: bool
    order_edit_pending: bool


@dataclass(frozen=True)
class AutomaticCompile:
    panel: PagingPanel
    options: RefreshOptions
    only: str

    def call(self, *, plan_id: str = "") -> EB.EngineCall:
        return EB.refreshpages_call(
            issue=self.panel.issue, plan_id=plan_id, on_change=True, only=self.only,
            regenerate_toc=self.options.regenerate_toc,
            order_result=self.options.order_result, order_json=self.options.order_json,
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
        self.auto_enabled = False
        self.auto_run_result = False
        self.pending = False
        self.message = ""
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
        order_edit_pending = bool(getattr(order_panel, "refresh_order_needs_confirmation", False)
                                  and not self.order_result)
        needs_confirmation = bool(
            not self.order_result
            and (order_panel is None
                 or order_edit_pending
                 or not result
                 or result.get("status") not in {"completed", "unchanged"})
        )
        return RefreshOptions(self.regenerate_toc, self.order_result, order_json,
                              needs_confirmation, order_edit_pending)

    def automatic_request(self) -> AutomaticCompile | None:
        options = self.app.store.get("options", {}) or {}
        if not self.auto_enabled or not self._is_current() or options.get("compileall_dry_run"):
            return None
        return AutomaticCompile(self, self.options(),
                                str(self.page.field("compile_only") or "").strip())

    def controls(self) -> ft.Control:
        t = self.t
        self.preview_button = C.primary_button("编译并刷新页码", self._preview, t)
        self.toc_checkbox = ft.Checkbox(
            label="重生成目录", value=self.regenerate_toc, on_change=self._toc_changed,
        )
        self.auto_checkbox = ft.Checkbox(
            label="编译后自动刷新页码", value=self.auto_enabled and self._is_current(),
            tooltip="启用后，编译篇目以引擎预检和已确认的排序为准。",
            on_change=self._auto_changed,
        )
        self.order_input = C.text_field(t, value=self.order_result, expand=True,
                                        on_change=self._order_changed)
        self.order_note = C.text("", t, "caption", t.palette.text_muted)
        self.preview_view = ft.Column(spacing=t.sp("half"))
        self.result_view = ft.Column(spacing=t.sp("half"))
        self.message_view = C.text("", t, "small", t.palette.text_muted)
        self.view = ft.Column(spacing=t.sp("1"), controls=[
            self.auto_checkbox,
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[self.preview_button, self.toc_checkbox]),
            C.form_field(t, "排序结果 JSON 路径（可选）", self.order_input),
            self.order_note,
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
        for control in (self.preview_button, self.toc_checkbox, self.auto_checkbox,
                        self.order_input, *self.action_buttons):
            control.disabled = disabled
            self.page._safe_update(control)

    def _render(self) -> None:
        if self.view is None:
            return
        options = self.options()
        if options.order_json:
            self.order_note.value = "将使用本期已确认的排序和排除清单。"
        elif options.order_needs_confirmation:
            self.order_note.value = "请回到排序步骤确认改名，或填写已确认的排序结果 JSON。"
        else:
            self.order_note.value = "若本期排序时排除了目录，重启后请填写排序结果 JSON，或回到排序步骤重新确认。"
        self.message_view.value = self.message
        self.action_buttons = []
        self.preview_view.controls = self._preview_controls() if self._is_current() else []
        self.result_view.controls = self._result_controls() if self._is_current() else []
        self.refresh_running()
        self.page._safe_update(self.view)

    def _clear_preview(self) -> None:
        self.preview = None
        self.preview_options = None

    def _options_changed(self) -> None:
        self._clear_preview()
        self.message = "刷新选项已变化，请重新预览。"
        self._render()

    def _auto_changed(self, e) -> None:
        requested = bool(e.control.value)
        self.page._ensure_issue_identity()
        if self._editable() and self.issue:
            if not requested:
                self.auto_enabled = False
            elif not self.auto_enabled:
                def confirm() -> None:
                    if self._editable():
                        self.auto_enabled = True
                        self.auto_checkbox.value = True
                        self.page._safe_update(self.auto_checkbox)

                C.confirm(self.page.page, self.t, title="启用本期自动刷新页码？",
                          message=(f"{self.issue}\n正常编译会替换本轮正文 PDF。"
                                   "如果页数变化或不能确认原有页码，会更新整期初排 TeX 的页码字段、"
                                   "正文 PDF 和目录产物。"),
                          confirm_label="启用自动刷新", on_confirm=confirm)
        e.control.value = self.auto_enabled and self._is_current()
        self.page._safe_update(e.control)

    def _toc_changed(self, e) -> None:
        if self._editable():
            self.regenerate_toc = bool(e.control.value)
            self._options_changed()

    def _order_changed(self, e) -> None:
        if self._editable():
            self.order_result = str(e.control.value or "").strip()
            self._options_changed()

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

        C.confirm(self.page.page, self.t, title="确认刷新整期页码",
                  message="将编译稿件并更新页码字段、正文 PDF 与目录产物。",
                  confirm_label="开始编译并刷新", on_confirm=confirm)

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
        if not self._editable() or not self.issue or options != self.options():
            return
        # Preview is also how the user discovers a pending transaction. Let the
        # engine report it even when the editor still needs to confirm order.
        if kind == "apply" and self.preview_options != options:
            self._options_changed()
            return
        try:
            call = EB.refreshpages_call(
                issue=self.issue, plan_id=plan_id, recovery_id=recovery_id,
                regenerate_toc=options.regenerate_toc, order_result=options.order_result,
                order_json=options.order_json,
            )
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
        if self._is_current() and options == self.options():
            self._consume(kind, code, payload or {}, options, cancelled)
            self._render()

    def _track_proc(self, proc) -> None:
        self.app.track_proc(proc)
        if self.app.cancel_requested:
            EB.kill_tree(proc)

    def begin_regular_compile(self) -> None:
        if self.auto_run_result:
            self.auto_run_result = False
            self.message = ""
            self.result = {}
            self._render()

    async def compile_automatically(self, request: AutomaticCompile,
                                    preview_call: EB.EngineCall, on_line) -> tuple[int, str]:
        if not self._is_current():
            return 2, "期目录已变化，请在当前期重新运行编译。"
        self.auto_run_result = True
        self._clear_preview()
        self.result = {}
        self.message = "正在预览自动页码刷新…"
        self._render()
        kind = "preview"
        try:
            if self.app.cancel_requested:
                code, payload = -1, {}
            elif request != self.automatic_request():
                code, payload = 2, {"error": "编译选项已变，请重新运行编译。"}
            else:
                code, payload = await EB.run_json_call(
                    preview_call, on_line=on_line, on_proc=self._track_proc)
                payload = payload or {}
                if code == 0 and not self.app.cancel_requested:
                    problem = self._automatic_preview_problem(request, payload)
                    if problem:
                        code, payload = 2, {"error": problem}
                    else:
                        for warning in payload.get("warnings") or []:
                            on_line(str(warning))
                        apply_call = request.call(plan_id=payload["plan_id"])
                        kind = "apply"
                        self.message = "正在编译并按需刷新页码…"
                        self._render()
                        self.app.mark("refresh_auto_apply", argv=apply_call.argv)
                        code, payload = await EB.run_json_call(
                            apply_call, on_line=on_line, on_proc=self._track_proc)
                        payload = payload or {}
                        if code == 0 and (payload.get("status") not in {"completed", "unchanged"}
                                          or payload.get("scope") not in {"issue", "selected_pdfs"}):
                            code, payload = 2, {"error": "引擎未返回有效的自动刷新结果。"}
        except (OSError, RuntimeError, ValueError) as exc:
            code, payload = -1, {"error": str(exc)}
        if not self._is_current():
            return 2, "期目录已变化，请在当前期重新运行编译。"
        self._consume(kind, code, payload, request.options, self.app.cancel_requested,
                      automatic=True)
        self._render()
        return code, self.message

    def _automatic_preview_problem(self, request: AutomaticCompile, payload: dict) -> str:
        if request != self.automatic_request():
            return "期目录或编译选项已变，请重新运行编译。"
        if not self._can_apply(0, payload):
            return str(payload.get("error") or "引擎未返回有效的自动刷新预览。")
        if not payload.get("articles"):
            return "没有可编译的篇目。"
        return self._order_problem(payload, request.options)

    @staticmethod
    def _can_apply(code: int, payload: dict) -> bool:
        return (code == 0 and payload.get("status") == "preview"
                and bool(payload.get("can_apply") and payload.get("plan_id")))

    @staticmethod
    def _order_problem(payload: dict, options: RefreshOptions) -> str:
        source = payload.get("order_source")
        if source == "implicit_all":
            return "尚无确认的排序交接，将按所有识别到的篇目预演；请先回到排序步骤确认清单后重新预览。"
        if options.order_edit_pending or (options.order_needs_confirmation and source != "saved"):
            return "请先确认本期的排序和排除清单，再重新预览。"
        return ""

    def _consume(self, kind: str, code: int, payload: dict, options: RefreshOptions,
                 cancelled: bool, *, automatic: bool = False) -> None:
        status = payload.get("status")
        self.result = payload
        if self._can_apply(code, payload) and not cancelled:
            if problem := self._order_problem(payload, options):
                self.message = problem
                return
            self.preview = payload
            self.preview_options = options
            self.message = "请核对篇目、当前页码和写入范围。新页码将在编译完成后显示。"
        elif status == "recovery_required":
            self.message = ("刷新中断，需恢复本期文件。" if self._recovery_id()
                            else "本期有待恢复事务，请到对应步骤恢复。")
        elif status == "rolled_back":
            self.message = "已恢复本期文件。" if kind == "recover" else "刷新失败，已回滚。"
        elif status in {"completed", "unchanged"} and code == 0 and not cancelled:
            if status == "unchanged":
                self.message = "刷新核验完成，文件无需更新。"
            elif automatic:
                self.message = ("已刷新整期页码。" if payload.get("scope") == "issue"
                                else "页码无需调整，已只更新本次编译的正文 PDF。")
            else:
                self.message = "刷新完成。"
        elif cancelled:
            self.result = {}
            self.message = ("已请求停止，请重新预览核对本期文件。"
                            if kind in {"apply", "recover"} else "已取消预览。")
        else:
            self.message = str(payload.get("error") or f"刷新未完成（退出码 {code}）。")

    def _action(self, label: str, callback) -> ft.Control:
        button = C.primary_button(label, callback, self.t)
        self.action_buttons.append(button)
        return button

    def _preview_controls(self) -> list[ft.Control]:
        rows = []
        displayed = self.preview or self.result
        for article in displayed.get("articles", []):
            old = article.get("old") or {}
            new = article.get("new") or {}
            current = f"{old['first']}–{old['last']}" if old else "未知"
            span = f"{current} → {new['first']}–{new['last']}" if new else current
            rows.append(C.text(f"{article['folder']}　{span}", self.t, "body"))
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

        def open_result(e) -> None:
            self.page._ensure_issue_identity()
            if self._is_current() and self.result is result:
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
