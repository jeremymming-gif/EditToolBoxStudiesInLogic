"""R1 排序决策稿与 R2 稿件目录改名界面。"""
from __future__ import annotations

from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C


_CANDIDATE_STATES = {
    "missing_field": "缺少题名或作者",
    "missing_tex": "缺少精确同名初排 TeX",
    "unreadable": "无法读取",
    "unsafe_entry": "目录或 TeX 不安全",
}
_RENAME_STATES = {
    "preview": "改名预览已更新",
    "completed": "改名已完成。继续排序前请重新读取候选。",
    "unchanged": "文件夹名称已符合当前顺序",
    "rolled_back": "改名已回滚",
    "recovery_required": "改名需要恢复",
}


class IssueOrderPanel:
    def __init__(self, page) -> None:
        self.page = page
        self.t = page.t
        self.issue = self._current_issue()
        self.binding = page.current_artifact_binding()
        self.candidates: list[dict] = []
        self.excluded: set[str] = set()
        self.result: dict = {}
        self.refresh_order_result: dict | None = None
        self.refresh_order_needs_confirmation = False
        self._hide_renames = False
        self.preview_order: dict | None = None
        self.docx = ""
        self.status = ""
        self.warnings: list[str] = []
        self.pending = False
        self._picker_seq = 0
        self._output = C.text_field(self.t, value=str(Path(self.issue).expanduser().absolute().parent),
                                    expand=True)
        self._output_field = C.form_field(self.t, "排序稿输出文件夹", self._output, expand=True)

    def controls(self) -> ft.Control:
        t = self.t
        self._read_button = C.secondary_button("读取排序候选", self._read, t, icon="REFRESH")
        self._generate_button = C.secondary_button(
            "生成排序决策稿", self._generate, t, icon="DESCRIPTION_OUTLINED")
        self._preview_button = C.secondary_button("预览改名", self._preview_rename, t,
                                                  icon="VISIBILITY")
        self._apply_button = C.primary_button("应用改名", self._confirm_rename, t,
                                              icon="DRIVE_FILE_RENAME_OUTLINED")
        self._recover_button = C.secondary_button("恢复本期改名", self._recover, t, icon="RESTORE")
        self._docx_action = ft.Row()
        self._candidates = ft.Column(spacing=t.sp("1"))
        self._feedback = ft.Column(spacing=t.sp("1"))
        self._root = ft.Column(spacing=t.sp("2"), controls=[
            C.section_label("排序与文件夹名称", t),
            C.text("读取候选后可调整顺序，排除非稿件目录，再预览并确认改名。",
                   t, "caption", t.palette.text_muted),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                self._read_button,
                C.secondary_button("打开期目录", self._open_issue, t, icon="FOLDER_OPEN"),
            ]),
            ft.Row(spacing=t.sp("1"), controls=[
                self._output_field,
                C.secondary_button("浏览…", self._browse_output, t, icon="FOLDER_OPEN"),
            ]),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                self._generate_button, self._docx_action,
            ]),
            self._candidates,
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                self._preview_button, self._apply_button, self._recover_button,
            ]),
            self._feedback,
        ])
        self._render()
        return self._root

    def _candidate_row(self, index: int, row: dict) -> ft.Control:
        folder = row["folder"]
        excluded = folder in self.excluded
        note = _CANDIDATE_STATES.get(row.get("state"))
        metadata = [C.text(folder, self.t, "small")]
        if row.get("title"):
            metadata.append(C.text(row["title"], self.t, "small"))
        if row.get("authors"):
            metadata.append(C.text("、".join(row["authors"]), self.t, "caption"))
        if note:
            metadata.append(C.text(note, self.t, "caption", self.t.palette.warn))
        up = C.text_button("上移", lambda e: self._move(index, -1), self.t, icon="ARROW_UPWARD")
        down = C.text_button("下移", lambda e: self._move(index, 1), self.t, icon="ARROW_DOWNWARD")
        up.disabled = index == 0
        down.disabled = index + 1 == len(self.candidates)
        include = C.text_button("纳入" if excluded else "排除", lambda e: self._exclude(folder),
                                self.t)
        return ft.Row(spacing=self.t.sp("1"), controls=[
            ft.Column(expand=True, spacing=2, controls=metadata),
            ft.Row(spacing=0, controls=[up, down, include]),
        ])

    def _can_apply(self) -> bool:
        return (self.result.get("status") == "preview" and bool(self.result.get("can_apply"))
                and bool(self.result.get("plan_id")) and self.preview_order is not None)

    def _recovery_id(self) -> str:
        if (self.result.get("status") == "recovery_required"
                and self.result.get("recovery_operation", "renamefolders") == "renamefolders"):
            return self.result.get("recovery_id") or ""
        return ""

    def refresh_running(self) -> None:
        if getattr(self, "_root", None) is None:
            return
        disabled = self.pending or bool(self.page.app.running) or not self.page.app.engine_status.ok
        self._read_button.disabled = disabled
        self._generate_button.disabled = disabled
        self._preview_button.disabled = disabled or not self.candidates
        self._apply_button.disabled = disabled or not self._can_apply()
        self._recover_button.disabled = disabled or not self._recovery_id()
        self._recover_button.visible = bool(self._recovery_id())
        self._output.disabled = disabled
        self._candidates.disabled = disabled
        self.page._safe_update(self._read_button, self._generate_button, self._preview_button,
                               self._apply_button, self._recover_button,
                               self._output, self._candidates)

    def _render(self) -> None:
        t = self.t
        self._candidates.controls = [
            self._candidate_row(index, row) for index, row in enumerate(self.candidates)
        ]
        self._docx_action.controls = [
            C.text_button("打开排序决策稿", self._open_docx, t, icon="OPEN_IN_NEW")
        ] if self.docx else []
        details = [self.status, *self.warnings, *(self.result.get("warnings") or []),
                   *(self.result.get("conflicts") or [])]
        recovery_path = self.result.get("recovery_path")
        if recovery_path:
            details.append(recovery_path)
        messages = dict.fromkeys(message for message in details if message)
        self._feedback.controls = [
            *[C.text(str(message), t, "small", t.palette.text_muted) for message in messages],
            *([C.text(f"{item['old']} → {item['new']}", t, "small")
               for item in self.result.get("renames") or []] if not self._hide_renames else []),
        ]
        self.refresh_running()
        self.page._safe_update(self._docx_action, self._feedback)

    def _current_issue(self) -> str:
        return str(self.page.field("issue_dir") or "").strip()

    def _is_current(self) -> bool:
        return (self.binding == self.page.current_artifact_binding()
                and self.issue == self._current_issue())

    def _editable(self) -> bool:
        return self._is_current() and not self.pending and not self.page.app.running

    def _invalidate_preview(self) -> None:
        if self.result.get("status") == "preview":
            self.result = {}
            self.preview_order = None
            self.status = "顺序已变更，请重新预览改名。"

    def _move(self, index: int, delta: int) -> None:
        other = index + delta
        if self._editable() and 0 <= other < len(self.candidates):
            self.candidates[index], self.candidates[other] = self.candidates[other], self.candidates[index]
            self._invalidate_refresh_order()
            self._invalidate_preview()
            self._render()

    def _exclude(self, folder: str) -> None:
        if self._editable():
            self.excluded.symmetric_difference_update({folder})
            self._invalidate_refresh_order()
            self._invalidate_preview()
            self._render()

    def _open_issue(self, e=None) -> None:
        self.page.open_artifact(self.issue, binding=self.binding)

    def _open_docx(self, e=None) -> None:
        if self.docx:
            self.page.open_artifact(self.docx, binding=self.binding)

    def _browse_output(self, e=None) -> None:
        """复用现有目录 picker；取消不改值，换期/迟到来访不写回新期字段。"""
        if not self._is_current():
            return
        self._picker_seq += 1
        seq = self._picker_seq
        before = str(self._output.value or "").strip()
        issue, binding = self.issue, self.binding

        def guard() -> bool:
            return (seq == self._picker_seq and issue == self.issue
                    and binding == self.binding and self._is_current()
                    and before == str(self._output.value or "").strip())

        browse = getattr(self.page.app, "browse_field", None)
        if browse is None:
            return
        browse(ref=[self._output], dialog_title="选择排序稿输出文件夹", kind="dir",
               key="issue_order_out", result_guard=guard, on_result=lambda selected: None)

    def _invalidate_order_panel(self) -> None:
        """成功确认或改名恢复后让期分页重取排序结果（未挂载则进入刷新页重取）。"""
        panel = getattr(self.page, "_issue_paging_panel", None)
        invalidate = getattr(panel, "invalidate_order", None)
        if callable(invalidate):
            invalidate()

    def _toast_rename_applied(self) -> None:
        """仅成功应用改名时发一次「已应用改名」Toast + 日志。"""
        toast = getattr(self.page.app, "toast", None)
        if callable(toast):
            toast("已应用改名")
        log = getattr(self.page, "_on_line", None)
        if callable(log):
            log("已应用改名")

    def _read(self, e=None) -> None:
        # 读候选只做预演；用与决策稿输出字段无关的安全默认路径，绝不写盘。
        out_root = str(Path(self.issue).expanduser().absolute().parent)
        self._queue("draft", EB.orderdraft_call, issue=self.issue, out_root=out_root, dry_run=True)

    def _generate(self, e=None) -> None:
        out_root = str(self._output.value or "").strip()
        if not self._is_current():
            return
        if not out_root:
            self.status = "请先填写排序稿输出文件夹"
            self._render()
            return
        self._queue("docx", EB.orderdraft_call, issue=self.issue, out_root=out_root)

    def _order(self) -> dict:
        names = [row["folder"] for row in self.candidates]
        return {"schema": 1, "ordered": [name for name in names if name not in self.excluded],
                "excluded": [name for name in names if name in self.excluded]}

    def _preview_rename(self, e=None) -> None:
        if not self._editable() or not self.candidates:
            return
        order = self._order()
        if not order["ordered"]:
            self._invalidate_preview()
            self.status = "请选择至少一篇纳入排序的稿件"
            self._render()
            return
        self._queue("preview", EB.renamefolders_call, issue=self.issue, order=order)

    def _confirm_rename(self, e=None) -> None:
        if not self._editable() or not self._can_apply():
            return
        preview = self.result
        order = self.preview_order

        def confirm() -> None:
            if self._is_current() and self.result is preview and self._can_apply():
                self._queue("apply", EB.renamefolders_call, issue=self.issue, order=order,
                            plan_id=preview["plan_id"])

        C.confirm(self.page.page, self.t, title="应用改名？",
                  message="只更改本期各稿件文件夹名称，文件夹内部内容保持原样。",
                  confirm_label="应用改名", on_confirm=confirm)

    def _recover(self, e=None) -> None:
        recovery_id = self._recovery_id()
        if not self._editable() or not recovery_id:
            return

        def confirm() -> None:
            if self._is_current() and recovery_id == self._recovery_id():
                self._queue("recover", EB.renamefolders_call, issue=self.issue, recovery_id=recovery_id)

        C.confirm(self.page.page, self.t, title="恢复本期改名？",
                  message="将按恢复记录检查并恢复本期文件夹名称。",
                  confirm_label="恢复本期改名", on_confirm=confirm)

    def _queue(self, kind: str, build_call, **kwargs) -> None:
        if not self._editable():
            return
        self.page._ensure_issue_identity()
        if not self._is_current():
            return
        if kind in {"draft", "preview", "apply", "recover"}:
            self._invalidate_refresh_order()
        try:
            call = build_call(**kwargs)
        except (ValueError, RuntimeError) as exc:
            self.status = str(exc)
            self._render()
            return
        self.pending = True
        if kind == "docx":
            self.docx = ""
        if kind in {"draft", "preview", "apply"}:
            self._invalidate_preview()
        self.status = f"正在{call.label}…"
        self._render()
        self.page.page.run_task(self._run, call, kind, kwargs.get("order"))

    def _invalidate_refresh_order(self) -> None:
        # 草稿读取/上移/排除等未提交编辑只标记待确认，不清除上一次有效回执（§2 T2↔T1/T3）。
        # 真正清除回执只发生在改名恢复成功时（见 _consume）。
        self.refresh_order_needs_confirmation = True

    async def _run(self, call: EB.EngineCall, kind: str, order: dict | None) -> None:
        app = self.page.app
        if not self._is_current() or not app.begin_run(call):
            self.pending = False
            if self._is_current():
                self.status = "暂未运行，请重试"
                self._render()
            return

        def track(proc) -> None:
            app.track_proc(proc)
            if app.cancel_requested:
                EB.kill_tree(proc)

        code = 2
        try:
            code, payload = await EB.run_json_call(call, on_line=self.page._on_line, on_proc=track)
        except (OSError, RuntimeError, ValueError) as exc:
            payload = {"error": str(exc)}
        finally:
            cancelled = app.cancel_requested
            self.pending = False
            app.finish_run(call, code)
        if self._is_current():
            if cancelled:
                self._invalidate_refresh_order()
            self._consume(kind, code, payload, order, cancelled)
            self._render()

    def _consume(self, kind: str, code: int, payload: dict | None, order: dict | None,
                 cancelled: bool) -> None:
        payload = payload or {}
        state = payload.get("status")
        self.warnings = []
        if cancelled and state not in {"completed", "unchanged", "rolled_back", "recovery_required"}:
            self.status = ("已请求停止。请重新预览，核对本期状态。"
                           if kind in {"apply", "recover"} else "已取消")
            return
        if kind in {"draft", "docx"}:
            self.warnings = list(payload.get("warnings") or [])
            if kind == "draft":
                self.candidates = list(payload.get("rows") or [])
                self.excluded.clear()
            if kind == "docx" and code == 0 and state == "completed":
                self.docx = payload.get("docx") or ""
                self.status = f"排序决策稿已生成：{self.docx}"
                return
            self.status = (payload.get("error") or ("已读取候选" if code == 0
                           else f"引擎未返回排序结果（退出码 {code}）"))
            return
        if kind == "recover" and code == 0 and state in {"completed", "unchanged", "rolled_back"}:
            # 改名恢复成功先清旧回执，避免旧排序结果被分页再次消费。
            self.refresh_order_result = None
            self.refresh_order_needs_confirmation = True
            self._invalidate_order_panel()
        if kind == "preview":
            self._hide_renames = False
        self.result = payload
        self.preview_order = order if kind == "preview" else None
        # 只有「应用改名」的成功确认才产出跨任务回执；读候选/移动/排除/预览均不清除旧回执。
        # _run 调用方已验证 _is_current()；此处再核对期代次与确认时的预览绑定。
        if kind == "apply" and code == 0 and state in {"completed", "unchanged"} \
                and self._is_current() and self.result is payload:
            self.refresh_order_result = dict(payload)  # 引擎原始成功 JSON
            self.refresh_order_needs_confirmation = False
            self._invalidate_order_panel()
            if state == "completed":
                self._toast_rename_applied()
        # 无变更（unchanged）不重复展开同名映射，也不报「已应用改名」成功态。
        self._hide_renames = state == "unchanged"
        self.status = ("顺序已确认，目录无需改名" if state == "unchanged" else
                       payload.get("error") or _RENAME_STATES.get(state)
                       or f"改名计划未能执行（退出码 {code}）")
        if state == "completed":
            self.candidates.clear()
            self.excluded.clear()
