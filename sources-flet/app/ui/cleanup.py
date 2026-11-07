"""清理临时文件：单一日常入口（INTERFACE-DAILY，T3）。

用户可见只有一个主按钮「清理临时文件」：点击后自动先 dry-run 校验（扫描），
零候选/预检失败不进入真跑、不建批次；有候选才按 --mode daily 真跑。
旧的三档（aux/delivery/workspace）、手动预演闸、tocwork 开关与「清理」危险确认词
都已删除；旧设置只保留只读兼容（assembly.cleantemp_mode 等），不影响本入口。

本模块只消费引擎 JSON（cleantemp.py --mode daily --json 与
cleantemp.py --restore TRASH --json），不重新扫描/解析 TeX，也不从 stderr
人类日志推断数量。结果绑 root + operationId（撤销回传本批次 trashDir），
持久化在 app.store["issue_state"][root]["cleanup"]，刷新/离页/重启后仍能显示
与撤销；晚到的回调只写它自己那一期的记录，不会把旧期结果显示到新一期。
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import components as C

DAILY = "daily"
_DAY_STATUSES = ("success", "empty", "partial", "preflight_failed", "cancelled")
_RESTORE_STATUSES = ("success", "empty", "partial", "preflight_failed", "cancelled")
_STATE_OF = {
    "success": "done",
    "empty": "done",
    "partial": "warn",
    "preflight_failed": "error",
    "cancelled": "cancelled",
    "failed": "error",
}
_STATUS_LABEL = {
    "success": "已完成",
    "empty": "没有可清理的临时文件",
    "partial": "部分失败",
    "preflight_failed": "预检未通过（零写入）",
    "cancelled": "已停止",
    "failed": "失败",
}


def _now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clock(stamp: str) -> str:
    return str(stamp or "")[11:16]


def _norm_entries(value, *, action: str = "move") -> list:
    """entries 兼容 [{"path","action"}] 与旧版字符串数组；只留相对路径。"""
    rows: list = []
    for item in value or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            act = str(item.get("action") or action)
        else:
            path = str(item or "").strip()
            act = action
        if path:
            rows.append({"path": path, "action": act})
    return rows


def _norm_kept(value) -> list:
    rows: list = []
    for item in value or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            rows.append({"path": path, "kind": str(item.get("kind") or ""),
                         "reason": str(item.get("reason") or "")})
        else:
            path = str(item or "").strip()
            if path:
                rows.append({"path": path, "kind": "", "reason": ""})
    return rows


def _norm_failures(value) -> list:
    rows: list = []
    for item in value or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            error = str(item.get("error") or item.get("message") or "").strip()
        else:
            path, error = str(item or "").strip(), ""
        if path or error:
            rows.append({"path": path, "error": error})
    return rows


class CleanupPanel:
    """清理页的取值/执行/结果/撤销。由 IssuePage._cleanup 持有。

    方法：params() 参数区、result() 结果区（自更新 Column）、start() 单入口、
    restore_last() 撤销本批次、details_dialog() 明细、phase_label() 主按钮文案、
    reset() 换期/重建时清运行态（不动已持久化结果）、absorb_result() 供整期串行复用。
    """

    def __init__(self, page) -> None:
        self.page = page
        self._token = 0
        self._phase_token = 0       # 当前 phase 属于哪一次运行（晚到回调不误清新任务）
        self._phase = ""            # scanning / cleaning / restoring
        self._phase_root = ""
        self._phase_started = 0.0
        self._lines: list = []
        self._box = None
        self._box_root = ""

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def root(self) -> str:
        return str(self.page.field("issue_dir") or "")

    @property
    def phase(self) -> str:
        return self._phase

    def _store(self):
        return self.page.app.store

    def _entries(self) -> dict:
        data = self._store().get("issue_state", {}) or {}
        return dict(data) if isinstance(data, dict) else {}

    def record(self, root: str = "") -> dict:
        root = root or self.root
        if not root:
            return {}
        entry = self._entries().get(root) or {}
        rec = entry.get("cleanup") if isinstance(entry, dict) else None
        return dict(rec) if isinstance(rec, dict) else {}

    def _save(self, rec: dict, root: str) -> None:
        if not root:
            return
        data = self._entries()
        entry = dict(data.get(root) or {})
        entry["cleanup"] = rec
        data[root] = entry
        self._store().set("issue_state", data)
        save = getattr(self.page.app, "save_settings", None)
        if callable(save):
            try:
                save(toast=False)
            except TypeError:
                save()

    def _mark(self, event: str, **fields) -> None:
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            mark(event, **fields)

    def _toast(self, message: str) -> None:
        toast = getattr(self.page.app, "toast", None)
        if callable(toast):
            toast(message)

    def _refresh_steps(self) -> None:
        fn = getattr(self.page, "_refresh_steps", None)
        if callable(fn):
            fn()

    def _sync_buttons(self) -> None:
        fn = getattr(self.page, "_sync_detail_status", None)
        if callable(fn):
            fn()

    def reset(self) -> None:
        """换期/重建：让晚到回调失效；不删任何已持久化结果。"""
        self._token += 1
        self._phase = ""
        self._phase_root = ""
        self._lines = []

    # ── 参数区（唯一的范围说明，没有开关）──────────────────────────────
    def params(self) -> list:
        t = self.page.t
        return [
            C.notice_bar(
                "点「清理临时文件」= 自动校验 + 回收：aux / 编译日志 / .toolbox-tmp 中间件 / "
                "时间戳中英 tex / 目录结果 JSON / 旧《信息检查》报告（保留最新一组）。"
                "文件移入 <期目录>/.toolbox-trash/，可撤销、不永久删除；"
                "最终 tex/pdf 与 目录设置.json 不动。", t, "info"),
            C.text("范围固定：旧「三档清理 / 只预演 / 含中间件 / 全部报告」设置不再影响本入口。",
                   t, "caption", t.palette.text_faint),
        ]

    # ── 结果区 ──────────────────────────────────────────────────────────
    def result(self) -> ft.Control:
        if self._box is None or self._box_root != self.root:
            self._box = ft.Column(spacing=self.page.t.sp("1"),
                                  controls=self._result_controls())
            self._box_root = self.root
        return self._box

    def _refresh_result(self) -> None:
        box = self.result()
        box.controls = self._result_controls()
        try:
            box.update()
        except Exception:  # noqa: BLE001 —— 未挂载（构建期/测试桩）
            pass

    def _result_controls(self) -> list:
        t = self.page.t
        root = self.root
        if not root:
            return [C.text("先选择期目录；选好后点「清理临时文件」。", t, "caption",
                           t.palette.text_faint)]
        if self._phase and self._phase_root == root:
            label = {"scanning": "正在扫描临时文件…",
                     "cleaning": "正在清理：文件移入回收区…",
                     "restoring": "正在撤销本次清理…"}.get(self._phase, "正在运行…")
            hint = ("扫描阶段不写入；" if self._phase == "scanning"
                    else "文件会移入回收区，可撤销；")
            return [
                C.section_label("清理临时文件", t, note="运行中"),
                C.hairline(t),
                C.inline_note(label, t, "info"),
                C.text(hint + "点页头「停止」可中断。", t, "caption",
                       t.palette.text_faint),
                C.busy_bar(t, visible=True),
            ]
        rec = self.record(root)
        if not rec or rec.get("root") != root:
            return [self._empty("尚未清理",
                                "点「清理临时文件」自动校验并回收；扫描不动磁盘，空候选不建批次。")]
        rows: list = [
            C.section_label("清理结果", t,
                            note=_clock(rec.get("finishedAt") or rec.get("startedAt"))),
            C.hairline(t),
        ]
        rows += self._status_rows(rec)
        rows += self._kept_rows(rec)
        rows += self._failure_rows(rec)
        rows += self._restore_rows(rec)
        rows += self._action_rows(rec)
        return rows

    def _empty(self, title: str, desc: str) -> ft.Control:
        t = self.page.t
        return ft.Container(
            height=t.px(72), border=ft.Border.all(1, t.palette.border),
            border_radius=t.radius("md"), alignment=ft.Alignment.CENTER,
            content=ft.Column(spacing=t.sp("half"),
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                                  C.text(title, t, "small", t.palette.text_muted),
                                  C.text(desc, t, "caption", t.palette.text_faint,
                                         text_align=ft.TextAlign.CENTER, max_lines=2),
                              ]),
        )

    def _status_rows(self, rec: dict) -> list:
        t = self.page.t
        status = str(rec.get("status") or "failed")
        count = int(rec.get("count") or 0)
        failures = _norm_failures(rec.get("failures"))
        error = str(rec.get("error") or "").strip()
        rows: list = []
        if status == "success":
            rows.append(C.inline_note(f"实际回收 {count} 个文件（移入回收区，可撤销）。",
                                      t, "ok"))
        elif status == "empty":
            rows.append(C.inline_note("没有可清理的临时文件（未创建批次，也没有撤销）。",
                                      t, "info"))
        elif status == "partial":
            rows.append(C.inline_note(
                f"实际回收 {count} 个文件，{len(failures)} 项失败；已回收的条目仍可撤销。",
                t, "warn"))
        elif status == "preflight_failed":
            rows.append(C.inline_note(
                f"预检未通过，未写入任何文件：{error or '见下方明细'}", t, "error"))
        elif status == "cancelled":
            if count > 0:
                rows.append(C.inline_note(
                    f"已停止，部分文件已回收：实际回收 {count} 个文件；可撤销本批次。",
                    t, "warn"))
            elif str(rec.get("trashDir") or ""):
                rows.append(C.inline_note("已停止：本批次没有成功回收的文件。", t, "warn"))
            else:
                rows.append(C.inline_note("已停止：扫描阶段未写入，未回收任何文件。", t, "warn"))
        else:
            rows.append(C.inline_note(f"清理未完成：{error or '见下方明细'}", t, "error"))
        trash = str(rec.get("trashDir") or "")
        if trash:
            rows.append(C.text(f"回收区：{trash}", t, "caption", t.palette.text_faint,
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
        return rows

    def _kept_rows(self, rec: dict) -> list:
        t = self.page.t
        kept = _norm_kept(rec.get("kept"))
        if not kept:
            return []
        rows = [C.text(f"保留 {len(kept)} 项（有意不动）：", t, "caption",
                       t.palette.text_muted)]
        for item in kept[:3]:
            reason = item.get("reason") or "受保护"
            rows.append(C.text(f"· {item['path']} — {reason}", t, "caption",
                               t.palette.text_faint, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS))
        if len(kept) > 3:
            rows.append(C.text(f"…其余 {len(kept) - 3} 项在「查看明细」", t, "caption",
                               t.palette.text_faint))
        return rows

    def _failure_rows(self, rec: dict) -> list:
        t = self.page.t
        failures = _norm_failures(rec.get("failures"))
        if not failures:
            return []
        rows = [C.text(f"失败 {len(failures)} 项：", t, "caption", t.palette.error)]
        for item in failures[:3]:
            text = f"· {item['path']}" + (f" — {item['error']}" if item["error"] else "")
            rows.append(C.text(text, t, "caption", t.palette.error, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS))
        if len(failures) > 3:
            rows.append(C.text(f"…其余 {len(failures) - 3} 项在「查看明细」", t, "caption",
                               t.palette.text_faint))
        return rows

    def _restore_rows(self, rec: dict) -> list:
        t = self.page.t
        restore = rec.get("restore") if isinstance(rec.get("restore"), dict) else {}
        if not restore:
            return []
        status = str(restore.get("status") or "")
        count = int(restore.get("count") or 0)
        failures = _norm_failures(restore.get("failures"))
        if status == "success":
            return [C.inline_note(f"已撤销本次清理：恢复 {count} 个文件。", t, "ok")]
        if status == "empty":
            return [C.text("该批次没有可恢复的条目（可能已撤销过）。", t, "caption",
                           t.palette.text_faint)]
        if status == "partial":
            return [C.inline_note(
                f"已恢复 {count} 项；{len(failures)} 项未恢复（同名冲突不覆盖/缺失，"
                "处理后可重试）。", t, "warn")]
        if status == "preflight_failed":
            return [C.inline_note(
                f"撤销未执行：{restore.get('error') or '批次清单校验失败'}", t, "error")]
        if status == "cancelled":
            return [C.inline_note(f"撤销已停止：本次恢复 {count} 项。", t, "warn")]
        return [C.inline_note(f"撤销未完成：{restore.get('error') or '见日志'}",
                              t, "error")]

    def _undo_enabled(self, rec: dict) -> bool:
        status = str(rec.get("status") or "")
        trash = str(rec.get("trashDir") or "")
        if status not in ("success", "partial", "cancelled") or not trash:
            return False
        restore = rec.get("restore") if isinstance(rec.get("restore"), dict) else {}
        return str(restore.get("status") or "") != "success"

    def _action_rows(self, rec: dict) -> list:
        t = self.page.t
        restore = rec.get("restore") if isinstance(rec.get("restore"), dict) else {}
        restored = str(restore.get("status") or "") == "success"
        undo = C.secondary_button(
            "已撤销本次清理" if restored else "撤销本次清理",
            lambda e: self.restore_last(), t,
            icon="RESTORE", disabled=not self._undo_enabled(rec),
            height=t.px(28))
        return [ft.Row(spacing=t.sp("1"), controls=[
            undo,
            C.text_button("查看明细", lambda e: self.details_dialog(), t),
        ])]

    # ── 主按钮 ──────────────────────────────────────────────────────────
    def phase_label(self) -> str:
        return {"scanning": "正在扫描…",
                "cleaning": "正在清理…",
                "restoring": "正在撤销…"}.get(self._phase, "清理临时文件")

    def start(self) -> None:
        app = self.page.app
        if self._phase:
            self._toast("正在运行，等当前任务结束")
            return
        if getattr(app, "running", False):
            self._toast("运行中，完成后才能开始清理")
            return
        root = self.root
        if not root:
            self._toast("先选择期目录")
            return
        self._token += 1
        token = self._token
        self._phase = "scanning"
        self._phase_root = root
        self._phase_started = time.time()
        self._lines = []
        self._mark_running()
        self._expand_log()
        self._mark("cleanup_start", root=root, token=token)
        runner = getattr(getattr(self.page, "page", None), "run_task", None)
        if callable(runner):
            runner(self._execute, token, root)

    def _expand_log(self) -> None:
        fn = getattr(self.page, "_expand_log", None)
        if callable(fn):
            try:
                fn()
            except Exception:  # noqa: BLE001 —— 测试桩
                pass

    def _mark_running(self) -> None:
        self.page._status["cleantemp"] = ASM.StepStatus(
            "running", started_at=_now_iso())
        self._sync_buttons()
        self._refresh_steps()
        self._refresh_result()

    # ── 执行：自动扫描 →（有候选才）真跑 ───────────────────────────────
    async def _execute(self, token: int, root: str) -> None:
        started = time.time()
        scan = EB.cleantemp_daily_call(folder=root, dry_run=True)
        code, cancelled, payload, lines = await self._invoke(
            scan, token=token, root=root, phase="scanning")
        if self._stale(token, root):
            self._drop_phase(token)
            return
        scan_payload = payload if isinstance(payload, dict) else {}
        scan_status = str(scan_payload.get("status") or "")
        if not cancelled and code == 0 and scan_status != "empty" \
                and scan_payload.get("ok") and scan_status in ("success", "partial"):
            clean = EB.cleantemp_daily_call(folder=root, dry_run=False)
            code, cancelled, payload, lines = await self._invoke(
                clean, token=token, root=root, phase="cleaning")
            if self._stale(token, root):
                # 换期/重建后晚到的真跑结果只落到它自己的期目录，不刷新当前页。
                self._persist_payload(root, payload, code=code, cancelled=cancelled,
                                      started=started, stage="cleaning", lines=lines)
                self._drop_phase(token)
                return
            self._finish(root, payload, code=code, cancelled=cancelled,
                         started=started, stage="cleaning", lines=lines)
            return
        self._finish(root, scan_payload or None, code=code, cancelled=cancelled,
                     started=started, stage="scanning", lines=lines)

    def _stale(self, token: int, root: str) -> bool:
        return token != self._token

    def _drop_phase(self, token: int) -> None:
        """晚到回调收尾：只清自己那轮的运行态，不碰后启动的新任务。"""
        if self._phase_token == token:
            self._phase = ""
            self._phase_root = ""

    async def _invoke(self, call, *, token, root, phase):
        app = self.page.app
        self._phase = phase
        self._phase_token = token
        self._phase_root = root
        self._lines = []
        self._mark_running()
        begin = getattr(app, "begin_run", None)
        finish = getattr(app, "finish_run", None)
        if callable(begin):
            begin(call)
        try:
            code = await EB.run_call(
                call,
                on_line=lambda line: self._on_line(line),
                on_proc=getattr(app, "track_proc", None),
                notes=str(getattr(app, "notes_repo", "") or ""))
        except Exception as exc:  # noqa: BLE001 —— 子进程/取消异常都要落到真实结果态
            self._lines.append(str(exc))
            code = -1
        cancelled = bool(getattr(app, "cancel_requested", False))
        if callable(finish):
            finish(call, code)
        cancelled = cancelled or bool(getattr(app, "cancel_requested", False))
        payload = self._payload()
        return code, cancelled, payload, list(self._lines)

    def _on_line(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        self._lines.append(text)
        fn = getattr(self.page, "_on_line", None)
        if callable(fn):
            try:
                fn(text)
            except Exception:  # noqa: BLE001 —— 日志区未挂载
                pass

    def _payload(self):
        text = "\n".join(self._lines)
        if not text.strip():
            return None
        try:
            payload = EB.parse_json_document(text)
        except Exception:  # noqa: BLE001
            return None
        return payload if isinstance(payload, dict) else None

    def _finish(self, root: str, payload, *, code, cancelled, started, stage,
                lines) -> None:
        rec = self._record_payload(root, payload, code=code, cancelled=cancelled,
                                   started=started, stage=stage, lines=lines)
        self._phase = ""
        self._phase_root = ""
        self._save(rec, root)
        state = _STATE_OF.get(str(rec.get("status")), "error")
        self.page._status["cleantemp"] = ASM.StepStatus(
            state, exit_code=code,
            duration_ms=int((time.time() - started) * 1000),
            started_at=str(rec.get("startedAt") or ""),
            finished_at=str(rec.get("finishedAt") or ""),
            artifact=str(rec.get("trashDir") or ""),
            error=str(rec.get("error") or ""))
        if self.root == root:
            self._sync_buttons()
            self._refresh_steps()
            self._refresh_result()
        self._mark("cleanup_done", root=root, status=rec.get("status"),
                   operationId=rec.get("operationId"), count=rec.get("count"),
                   trashDir=rec.get("trashDir"), cancelled=bool(cancelled))

    def _persist_payload(self, root, payload, *, code, cancelled, started, stage,
                         lines) -> None:
        rec = self._record_payload(root, payload, code=code, cancelled=cancelled,
                                   started=started, stage=stage, lines=lines)
        self._save(rec, root)
        self._mark("cleanup_done", root=root, status=rec.get("status"),
                   operationId=rec.get("operationId"), count=rec.get("count"),
                   trashDir=rec.get("trashDir"), late=True)

    def _record_payload(self, root, payload, *, code, cancelled, started, stage,
                        lines) -> dict:
        payload = payload if isinstance(payload, dict) else {}
        status = str(payload.get("status") or "")
        if status not in _DAY_STATUSES:
            if cancelled:
                status = "cancelled"
            elif payload.get("ok") is True or code == 0:
                status = "success"
            else:
                status = "failed"
        if cancelled:
            # 引擎正常收尾的 cancelled 已带实际数量；进程被强杀时同样按取消处理。
            status = "cancelled"
        rec = {
            "schema": 1,
            "root": root,
            "operationId": str(payload.get("operationId") or ""),
            "status": status,
            "count": int(payload.get("count") or 0),
            "entryCount": int(payload.get("entryCount") or 0),
            "entries": _norm_entries(payload.get("entries")),
            "kept": _norm_kept(payload.get("kept")),
            "failures": _norm_failures(payload.get("failures")),
            "warnings": [str(x) for x in (payload.get("warnings") or [])
                         if str(x).strip()],
            "error": str(payload.get("error") or ""),
            "trashDir": str(payload.get("trashDir") or ""),
            "startedAt": _dt.datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "finishedAt": _now_iso(),
            "stage": stage,
        }
        if status == "cancelled" and not rec["trashDir"]:
            # 没有完整 JSON（如 SIGKILL）：按回收区里本时段新建的批次做真实兜底；
            # 扫描阶段没有目录 → 零写入。绝不把旧批次当本次结果。
            fallback = self._newest_batch(root, started)
            if fallback:
                rec["trashDir"] = str(fallback)
                info = EB.trash_info(str(fallback)) or {}
                if info.get("ok"):
                    rec["count"] = int(info.get("count")
                                       or len(info.get("entries") or []))
                    rec["entries"] = _norm_entries(info.get("entries"))
                rec["error"] = "进程未输出完整结果；已从本批次清单读取实际回收数。"
        if status == "cancelled" and rec["trashDir"] and rec["count"]:
            rec["warnings"] = list(rec["warnings"]) + ["已停止，部分文件已回收"]
        if status in ("failed", "preflight_failed") and not rec["error"] and lines:
            rec["error"] = str(lines[-1])
        if status == "failed" and not rec["trashDir"]:
            rec["count"] = 0
        return rec

    def _newest_batch(self, root: str, started: float):
        try:
            base = Path(root) / ".toolbox-trash"
            if not base.is_dir():
                return None
            best = None
            for item in base.iterdir():
                if not item.is_dir():
                    continue
                try:
                    mtime = item.stat().st_mtime
                except OSError:
                    continue
                if mtime + 2.0 < started:
                    continue
                if best is None or mtime > best[0]:
                    best = (mtime, item)
            return best[1] if best else None
        except OSError:
            return None

    # ── 撤销本批次 ──────────────────────────────────────────────────────
    def restore_last(self) -> None:
        app = self.page.app
        if self._phase:
            self._toast("正在运行，等当前任务结束")
            return
        if getattr(app, "running", False):
            self._toast("运行中，完成后才能撤销")
            return
        root = self.root
        rec = self.record(root)
        if not rec or rec.get("root") != root:
            self._toast("当前期目录没有清理结果")
            return
        trash = str(rec.get("trashDir") or "")
        if not self._undo_enabled(rec):
            if str(rec.get("status") or "") == "empty":
                self._toast("本次没有可清理的临时文件，无需撤销")
            else:
                self._toast("本次没有可撤销的批次")
            return
        self._token += 1
        token = self._token
        self._phase = "restoring"
        self._phase_token = token
        self._phase_root = root
        self._phase_started = time.time()
        self._lines = []
        self._mark_running()
        self._mark("cleanup_restore_start", root=root, trashDir=trash,
                   operationId=rec.get("operationId"))
        runner = getattr(getattr(self.page, "page", None), "run_task", None)
        if callable(runner):
            runner(self._restore_execute, token, root, trash,
                   rec.get("operationId") or "")

    async def _restore_execute(self, token, root, trash, operation_id) -> None:
        started = time.time()
        app = self.page.app
        call = EB.cleantemp_call(folder=root, restore=trash)
        begin = getattr(app, "begin_run", None)
        finish = getattr(app, "finish_run", None)
        if callable(begin):
            begin(call)
        try:
            code = await EB.run_call(
                call, on_line=lambda line: self._on_line(line),
                on_proc=getattr(app, "track_proc", None),
                notes=str(getattr(app, "notes_repo", "") or ""))
        except Exception as exc:  # noqa: BLE001
            self._lines.append(str(exc))
            code = -1
        cancelled = bool(getattr(app, "cancel_requested", False))
        if callable(finish):
            finish(call, code)
        payload = self._payload() or {}
        status = str(payload.get("status") or "")
        if cancelled:
            status = "cancelled"
        if status not in _RESTORE_STATUSES:
            status = "success" if (code == 0 and payload.get("ok")) else "failed"
        rec = self.record(root)
        if not rec or str(rec.get("trashDir") or "") != trash:
            rec = rec or {"schema": 1, "root": root, "operationId": operation_id,
                          "status": "success", "count": 0, "entries": [],
                          "kept": [], "failures": [], "error": "",
                          "trashDir": trash}
        restore = {
            "status": status,
            "count": int(payload.get("count") or 0),
            "failures": _norm_failures(payload.get("failures")),
            "error": str(payload.get("error") or ""),
            "at": _now_iso(),
        }
        rec = dict(rec)
        rec["restore"] = restore
        self._save(rec, root)
        self._drop_phase(token)
        current = token == self._token and self.root == root
        if current:
            state = {"success": "done", "empty": "done", "partial": "warn",
                     "preflight_failed": "error", "cancelled": "cancelled"}.get(
                         status, "error")
            self.page._status["cleantemp"] = ASM.StepStatus(
                state, exit_code=code,
                duration_ms=int((time.time() - started) * 1000),
                started_at=str(rec.get("startedAt") or ""),
                finished_at=_now_iso(),
                artifact=trash, error=restore["error"])
            self._sync_buttons()
            self._refresh_steps()
            self._refresh_result()
            if status == "success":
                self._toast(f"已恢复 {restore['count']} 个文件")
            elif status == "partial":
                self._toast("部分文件未恢复（同名冲突不覆盖）")
            elif status == "preflight_failed":
                self._toast("撤销未执行：批次清单校验失败")
        self._mark("cleanup_restore", root=root, status=status,
                   count=restore["count"], trashDir=trash, operationId=operation_id)

    # ── 明细 ────────────────────────────────────────────────────────────
    def details_dialog(self) -> None:
        t = self.page.t
        root = self.root
        rec = self.record(root)
        if not rec:
            self._toast("还没有清理结果")
            return
        rows: list = []
        rows.append(C.kv_row(t, "状态", _STATUS_LABEL.get(str(rec.get("status")), "—")))
        rows.append(C.kv_row(t, "批次号", str(rec.get("operationId") or "—"), mono=True))
        rows.append(C.kv_row(t, "期目录", root, mono=True))
        trash = str(rec.get("trashDir") or "")
        rows.append(C.kv_row(t, "回收区", trash or "—", mono=True))
        rows.append(C.kv_row(t, "实际回收", f"{int(rec.get('count') or 0)} 个文件"))
        entries = _norm_entries(rec.get("entries"))
        if entries:
            rows.append(C.section_label(f"已回收条目（{len(entries)}）", t))
            for item in entries[:300]:
                rows.append(C.text(f"· {item['path']}", t, "caption",
                                   t.palette.text_muted, max_lines=1,
                                   overflow=ft.TextOverflow.ELLIPSIS))
            if len(entries) > 300:
                rows.append(C.text(f"…其余 {len(entries) - 300} 项未显示", t, "caption",
                                   t.palette.text_faint))
        kept = _norm_kept(rec.get("kept"))
        if kept:
            rows.append(C.section_label(f"保留（{len(kept)}）", t))
            for item in kept[:100]:
                reason = item.get("reason") or item.get("kind") or "受保护"
                rows.append(C.text(f"· {item['path']} — {reason}", t, "caption",
                                   t.palette.text_faint, max_lines=1,
                                   overflow=ft.TextOverflow.ELLIPSIS))
        failures = _norm_failures(rec.get("failures"))
        if failures:
            rows.append(C.section_label(f"失败（{len(failures)}）", t))
            for item in failures[:100]:
                detail = item["path"] + (f" — {item['error']}" if item["error"] else "")
                rows.append(C.text(f"· {detail}", t, "caption", t.palette.error,
                                   max_lines=2, overflow=ft.TextOverflow.ELLIPSIS))
        warnings = [str(x) for x in (rec.get("warnings") or []) if str(x).strip()]
        if warnings:
            rows.append(C.section_label("提示", t))
            rows += [C.text(f"· {item}", t, "caption", t.palette.text_muted,
                            max_lines=2) for item in warnings[:20]]
        error = str(rec.get("error") or "").strip()
        if error:
            rows.insert(0, C.inline_note(error, t, "error"))
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                         controls=[
                             C.text("清理明细", t, "h2"),
                             ft.Container(expand=True),
                             C.icon_button("CLOSE", "关闭",
                                           lambda e: C.dismiss_top_dialog(self.page.page), t),
                         ]),
            content=ft.Container(
                width=t.px(560), height=t.px(380),
                content=ft.Column(rows, spacing=t.sp("1"), scroll=C.scrollbar(t))),
            actions=[C.text_button("关闭",
                                   lambda e: C.dismiss_top_dialog(self.page.page), t)],
        )
        C.show_dialog(self.page.page, dialog)

    # ── 整期串行复用（runall 显式勾选 cleantemp 时）─────────────────────
    def absorb_result(self, lines, *, code: int, cancelled: bool,
                      artifact: str = "", started=None) -> None:
        """整期串行执行完 cleantemp 后，把同一份 JSON 落成正式结果记录。"""
        root = self.root
        if not root:
            return
        text = "\n".join(str(x) for x in (lines or []) if str(x).strip())
        try:
            payload = EB.parse_json_document(text) if text else None
        except Exception:  # noqa: BLE001
            payload = None
        rec = self._record_payload(
            root, payload, code=code, cancelled=cancelled,
            started=started if started is not None else time.time(),
            stage="runall", lines=[str(x) for x in (lines or [])])
        if not rec.get("trashDir") and artifact:
            rec["trashDir"] = str(artifact)
        self._save(rec, root)
        # 整期串行里 cleantemp 没有走 _finish；这里补真实结束态，避免状态条停在 running。
        try:
            self.page._status["cleantemp"] = ASM.StepStatus(
                _STATE_OF.get(str(rec.get("status")), "error"), exit_code=code,
                duration_ms=int((time.time() - (started if started is not None
                                                       else time.time())) * 1000),
                started_at=str(rec.get("startedAt") or ""),
                finished_at=str(rec.get("finishedAt") or ""),
                artifact=str(rec.get("trashDir") or ""),
                error=str(rec.get("error") or ""))
            self._refresh_steps()
        except Exception:  # noqa: BLE001 —— 极简测试桩
            pass
        self._refresh_result()
        self._mark("cleanup_done", root=root, status=rec.get("status"),
                   operationId=rec.get("operationId"), count=rec.get("count"),
                   trashDir=rec.get("trashDir"), runall=True)
