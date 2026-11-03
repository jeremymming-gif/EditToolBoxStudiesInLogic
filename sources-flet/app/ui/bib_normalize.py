"""引用库 · 回读校验（批 115 夜间重设计 BPAGES-A，VIEW_KEY="normalize"）。

只读：跑 ``Engine/checkbib.py`` 体检并读取它写出的 Markdown 报告；不写库、
不 git、不做 ``--merge-into``。引擎没有 ``--json``，所以这里解析报告里的
「结论 / 条目数 / 附录键数」，降级时只保留结论 + 打开报告。

状态：empty / loading / running / success / failure / cancelled / stale 七态。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C
from ui.tokens import Tokens

VIEW_KEY = "normalize"
TITLE = "回读校验"
ROLE = "readonly"

REPORT_SUFFIX = "_回读校验报告.md"
CONCLUDE = re.compile(r"结论：\s*(\d+)\s*处问题\s*/\s*(\d+)\s*处提示")
ENTRIES = re.compile(r"解析出\s*(\d+)\s*条条目")
APPENDIX_ROW = re.compile(r"^\|\s*`[^`]+`\s*\|\s*@")
BULLET = re.compile(r"^-\s+(.+)$")


def default_report_path(returned: str) -> str:
    """与 checkbib 的默认报告名同源：交回稿同目录 + <stem>_回读校验报告.md。"""
    raw = str(returned or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    return str(path.with_name(path.stem + REPORT_SUFFIX))


def parse_report(text: str) -> dict:
    """解析 checkbib Markdown；只取计数与错误优先的前几条，不复制正文。"""
    data = {"errors": None, "warnings": None, "records": None, "keys": None,
            "rows": [], "ok": None, "conclusion": "", "has_table": False}
    lines = str(text or "").splitlines()
    in_appendix = False
    for line in lines:
        match = CONCLUDE.search(line)
        if match:
            data["errors"] = int(match.group(1))
            data["warnings"] = int(match.group(2))
            data["conclusion"] = line.strip("* ").strip()
            data["ok"] = data["errors"] == 0
        match = ENTRIES.search(line)
        if match:
            data["records"] = int(match.group(1))
        if line.startswith("## 附录"):
            in_appendix = True
        if in_appendix and APPENDIX_ROW.match(line):
            data["keys"] = (data["keys"] or 0) + 1
        if line.startswith("**问题（") or line.startswith("**提示（"):
            continue
        bullet = BULLET.match(line)
        if bullet and "`" in bullet.group(1):
            text = bullet.group(1)
            if "解析出" in text or text.startswith(("退回文件：", "原件：", "正文：")):
                continue  # 这三条是报告抬头，不是问题行
            kind = "error" if "问题" in data.get("_section", "") else "warn"
            data["rows"].append({"kind": kind, "text": text})
        if line.startswith("## "):
            data["_section"] = line[3:].strip()
    rows = data.pop("_section", None)
    if data["keys"] is None and data["records"] is not None:
        data["keys"] = data["records"]
    # 错误优先；同一级保持报告顺序
    data["rows"].sort(key=lambda row: 0 if row["kind"] == "error" else 1)
    return data


def _stamp(path: str) -> str:
    try:
        return datetime.fromtimestamp(Path(path).expanduser().stat().st_mtime).strftime("%H:%M")
    except (OSError, ValueError):
        return ""


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return "引擎没有返回说明"


class NormalizeView:
    def __init__(self, page) -> None:
        self.page = page
        self.returned = ""
        self.original = ""
        self.tex = ""
        self.compare = False
        self.close_keys = False
        self.compare_touched = False
        self.keys_touched = False
        self.report_path = ""
        self.report_text = ""
        self.data = parse_report("")
        self.loaded = False
        self.loading = False
        self.running = False
        self.cancel_requested = False
        self.error = ""
        self.cancelled = False
        self.success = ""
        self.input_error = ""
        self.elapsed = 0.0
        self.identified = ""
        self._proc = None
        self._task_running = False
        self._build_width = 0.0

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self) -> Tokens:
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _refresh(self) -> None:
        render = getattr(self.page, "_render", None)
        if callable(render):
            render()

    def _mark(self, event: str, **kwargs) -> None:
        marker = getattr(self.page, "mark", None)
        if callable(marker):
            try:
                marker(event, **kwargs)
            except Exception:  # noqa: BLE001
                pass

    def _schedule(self, runner) -> None:
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        if callable(run):
            run(runner)
        else:
            asyncio.run(runner())

    # ── 生命周期 ────────────────────────────────────────────────────────
    def activate(self) -> None:
        if not self.loaded and not self.loading:
            self._schedule(self._load_last)

    def refresh_running(self) -> None:
        return None

    def header_actions(self) -> list:
        return [self._open_report_button()]

    def primary_action(self):
        return self._generate_button()

    # ── 表单 ────────────────────────────────────────────────────────────
    def _on_path(self, field: str):
        def _handler(e) -> None:
            value = str(getattr(e.control, "value", "") or "").strip()
            setattr(self, field, value)
            if field == "returned":
                self.report_path = default_report_path(value)
            if field == "original" and value and not self.compare_touched:
                self.compare = True
            if field == "tex" and value and not self.keys_touched:
                self.close_keys = True
            self.input_error = ""
            self._refresh()
        return _handler

    def _browse(self, field: str):
        def _handler(e=None) -> None:
            dialog = getattr(self.page.page, "show_dialog", None)
            picker = getattr(getattr(self.page, "app", None), "pick_file", None)
            if callable(picker):
                try:
                    picked = picker()
                    if picked:
                        setattr(self, field, str(picked))
                        self._refresh()
                    return
                except Exception:  # noqa: BLE001
                    pass
            if callable(dialog):
                self.input_error = "请直接粘贴 .bib 路径；系统文件选择器不可用。"
                self._refresh()
        return _handler

    def _on_compare(self, e) -> None:
        self.compare = bool(getattr(e.control, "value", False))
        self.compare_touched = True
        self.input_error = ""
        self._refresh()

    def _on_keys(self, e) -> None:
        self.close_keys = bool(getattr(e.control, "value", False))
        self.keys_touched = True
        self.input_error = ""
        self._refresh()

    def _validate(self) -> str:
        if not self.returned.strip():
            return "先选交回的 .bib 文件。"
        if self.compare and not self.original.strip():
            return "勾了「字段对比」就必须填原件。"
        if self.close_keys and not self.tex.strip():
            return "勾了「键闭合」就必须填正文 .tex。"
        return ""

    def generate(self) -> None:
        error = self._validate()
        if error:
            self.input_error = error
            self.error = ""
            self._refresh()
            return
        self.input_error = ""
        self.error = ""
        self.cancelled = False
        self.report_path = self.report_path or default_report_path(self.returned)
        self._schedule(self._run_check)

    def identify(self) -> None:
        if not self.returned.strip():
            self.input_error = "先选交回的 .bib 文件。"
            self._refresh()
            return
        self._schedule(self._run_identify)

    def cancel(self) -> None:
        if not self.running:
            return
        self.cancel_requested = True
        proc = self._proc
        if proc is not None:
            try:
                EB.kill_tree(proc)
            except Exception:  # noqa: BLE001
                pass
        self._refresh()

    # ── 读取上次报告 ────────────────────────────────────────────────────
    async def _load_last(self) -> None:
        self.loading = True
        self._refresh()
        try:
            path = self.report_path
            if path and Path(path).exists():
                self.report_text = await asyncio.to_thread(
                    Path(path).read_text, encoding="utf-8", errors="replace")
                self.data = parse_report(self.report_text)
                self.loaded = True
            else:
                self.loaded = True
        except Exception as exc:  # noqa: BLE001
            self.error = f"读取上次报告失败：{_first_line(str(exc))}"
        finally:
            self.loading = False
            self._task_running = False
            self._refresh()

    # ── 引擎执行（checkbib 无 --json，走流式 run_call）──────────────────
    async def _run_check(self) -> None:
        if self.running:
            return
        self.running = True
        self.cancel_requested = False
        self.success = ""
        self.error = ""
        self._proc = None
        started = time.monotonic()
        self._refresh()
        out = self.report_path or default_report_path(self.returned)
        call = EB.checkbib_call(
            returned=self.returned,
            original=self.original if self.compare else "",
            tex=self.tex if self.close_keys else "",
            out=out)
        code = -1
        lines: list[str] = []
        try:
            code = await EB.run_call(
                call, on_line=lambda line: lines.append(str(line)),
                on_proc=lambda proc: setattr(self, "_proc", proc),
                notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            self.error = f"生成失败：{_first_line(str(exc))}"
        finally:
            self.elapsed = time.monotonic() - started
            self.running = False
            self._task_running = False
            self._proc = None
        if self.cancel_requested:
            self.cancelled = True
            self._refresh()
            return
        if self.error:
            self._refresh()
            return
        if code not in (0, 1):
            self.error = (f"生成失败：{_first_line(' '.join(lines)) }"
                          if lines else "生成失败：引擎退出码 %s" % code)
            self._refresh()
            return
        try:
            self.report_text = await asyncio.to_thread(
                Path(out).read_text, encoding="utf-8", errors="replace")
        except OSError as exc:
            self.error = f"生成失败：报告没有写出来（{_first_line(str(exc))}）"
            self._refresh()
            return
        self.data = parse_report(self.report_text)
        self.loaded = True
        counts = self.data
        if counts.get("errors") is None:
            self.success = "体检完成，但报告格式无法解析；请打开报告查看。"
        elif counts["errors"]:
            self.success = (f"体检未通过：{counts['errors']} 错误、"
                            f"{counts['warnings']} 警告；旧报告已更新。")
        else:
            self.success = f"体检通过：0 错误、{counts['warnings']} 警告。"
        self._mark("bib_normalize_run", returned=self.returned,
                   errors=counts.get("errors"), warnings=counts.get("warnings"))
        self._refresh()

    async def _run_identify(self) -> None:
        if self.running:
            return
        self.running = True
        self.error = ""
        lines: list[str] = []
        self._refresh()
        call = EB.checkbib_call(returned=self.returned, list_keys=True)
        try:
            code = await EB.run_call(call, on_line=lambda line: lines.append(str(line)),
                                     notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            code = -1
            lines = [str(exc)]
        self.running = False
        self._task_running = False
        if code == 0 and lines:
            text = " ".join(lines)
            match = re.search(r"(\d+)\s*条", text)
            self.identified = f"已识别 {match.group(1) if match else '?'} 键"
        else:
            self.error = f"识别失败：{_first_line(' '.join(lines))}"
        self._refresh()

    # ── 报告打开/复制 ───────────────────────────────────────────────────
    def _open_report(self) -> None:
        if not self._report_ready():
            self.error = ("还没有报告：先生成报告" if not self.report_path
                          else "报告文件不存在：重新生成或更改输出路径")
            self._refresh()
            return
        path = self.report_path
        opener = getattr(EB, "open_path", None)
        result = None
        if callable(opener):
            try:
                result = opener(path)
            except Exception as exc:  # noqa: BLE001
                result = (False, str(exc))
        if isinstance(result, tuple) and not result[0]:
            self.error = f"打开失败：{result[1]}"
            self._refresh()

    def _report_ready(self) -> bool:
        return bool(self.report_path) and Path(self.report_path).exists()

    def _open_report_button(self):
        btn = C.secondary_button("打开报告", lambda e: self._open_report(), self.t, height=28)
        btn.disabled = not self._report_ready()
        if btn.disabled:
            btn.tooltip = ("还没有报告：先生成报告" if not self.report_path
                           else "报告文件不存在：重新生成或更改输出路径")
        return btn

    def _copy(self, text: str) -> None:
        fn = getattr(getattr(self.page, "app", None), "copy_text", None)
        if callable(fn):
            try:
                fn(text)
                return
            except Exception:  # noqa: BLE001
                pass
        setter = getattr(getattr(self.page.page, "set_clipboard", None), "__call__", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:  # noqa: BLE001
                pass

    # ── 控件 ────────────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _generate_button(self):
        btn = C.primary_button("生成报告", lambda e: self.generate(), self.t, height=28)
        btn.disabled = self.running
        btn._bib_primary_generate = True
        return btn

    def _checkbox(self, label: str, value: bool, on_change) -> ft.Control:
        t = self.t
        return ft.Container(
            height=t.px(28), padding=ft.Padding(left=t.px(6), right=t.px(6), top=0, bottom=0),
            content=ft.Row(spacing=t.sp("half"), controls=[
                ft.Checkbox(value=value, on_change=on_change, visual_density=ft.VisualDensity.COMPACT,
                            splash_radius=0),
                C.text(label, t, "caption", t.palette.text),
            ]))

    def _paths(self) -> ft.Control:
        t = self.t
        return ft.Column(spacing=t.sp("1"), controls=[
            C.path_picker("交回稿（.bib）", self.returned, "作者交回的参考文献文件",
                          t, on_change=self._on_path("returned"),
                          on_browse=self._browse("returned"), required=True, show_note=False),
            C.path_picker("原件（.bib）", self.original, "编辑部手上的原 .bib",
                          t, on_change=self._on_path("original"),
                          on_browse=self._browse("original"), required=False, show_note=False),
            C.path_picker("正文（.tex）", self.tex, "用于键闭合核对",
                          t, on_change=self._on_path("tex"),
                          on_browse=self._browse("tex"), required=False, show_note=False),
        ])

    def _range_row(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
                          self._checkbox("字段对比", self.compare, self._on_compare),
                          self._checkbox("键闭合", self.close_keys, self._on_keys),
                          ft.Container(expand=True),
                          C.text("语言：中文（固定）", t, "caption", t.palette.text_muted),
                          self._identify_button(),
                          self._generate_button(),
                      ])

    def _identify_button(self) -> ft.Control:
        # P1-14：说明「识别」只读列键，避免被误认成生成报告。
        btn = C.text_button("识别", lambda e: self.identify(), self.t, height=28)
        btn.tooltip = "只读：用引擎列出交回稿里的键，用于核对，不写库"
        btn._bib_identify_btn = True
        return btn

    def _conclusion(self) -> ft.Control:
        t = self.t
        d = self.data
        if d.get("errors") is None:
            label = "报告格式无法解析" if self.report_text else "还没有报告"
            tone = t.palette.text_muted
            # P1-14：没有数据时不能把 None 直接拼进结论；无报告记 0，坏报告记 —。
            blank = "—" if self.report_text else "0"
            counts = f"{blank} 错误 · {blank} 警告 · {blank} 记录 · {blank} 键"
        elif d["errors"]:
            label = "体检未通过"
            tone = t.palette.error
            counts = (f"{d.get('errors', '—')} 错误 · {d.get('warnings', '—')} 警告 · "
                      f"{d.get('records', '—')} 记录 · {d.get('keys', '—')} 键")
        else:
            label = "体检通过"
            tone = t.palette.success
            counts = (f"{d.get('errors', '—')} 错误 · {d.get('warnings', '—')} 警告 · "
                      f"{d.get('records', '—')} 记录 · {d.get('keys', '—')} 键")
        return ft.Container(
            height=t.px(44), padding=ft.Padding(left=t.px(9), right=t.px(9), top=0, bottom=0),
            border=ft.Border(bottom=ft.BorderSide(0.6, t.palette.border)),
            content=ft.Row(spacing=t.sp("1"), controls=[
                C.status_dot("error" if d.get("errors") else
                             ("ok" if d.get("errors") == 0 else "idle"), t),
                C.text(label, t, "body", tone, weight="semibold"),
                C.text(counts, t, "caption", t.palette.text_muted),
                ft.Container(expand=True),
                C.text(self._elapsed_text(), t, "caption", t.palette.text_faint),
            ]))

    def _elapsed_text(self) -> str:
        return f"{self.elapsed:.1f} s" if self.elapsed else ""

    def _rows(self) -> ft.Control:
        t = self.t
        header = S.table_header_row(
            t, ["级别", "问题"], weights=[6, 38], sortable=[False, False])
        rows = []
        for item in (self.data.get("rows") or [])[:4]:
            rows.append(S.table_row(
                t, ["错误" if item["kind"] == "error" else "警告", item["text"]],
                weights=[6, 38], height=32))
        if not rows:
            rows.append(S.empty_row(
                t, "还没有报告。先选交回的 .bib，再生成报告。",
                action=C.text_button("生成报告", lambda e: self.generate(), t, height=28)))
        remaining = max(0, len(self.data.get("rows") or []) - 4)
        footer = []
        if remaining:
            footer.append(C.text(f"还有 {remaining} 条 · 完整内容见报告", t, "caption",
                                t.palette.text_faint))
        ready = self._report_ready()
        open_btn = C.text_button("打开", lambda e: self._open_report(), t, height=28)
        open_btn.disabled = not ready
        if not ready:
            open_btn.tooltip = "还没有报告：先生成报告"
        copy_btn = C.text_button("复制", lambda e: self._copy(self.report_path), t, height=28)
        copy_btn.disabled = not ready
        copy_btn._bib_copy_report = True
        if not ready:
            copy_btn.tooltip = "还没有报告：先生成报告"
        path_bar = ft.Row(spacing=t.sp("1"), controls=[
            C.text(self.report_path or "报告路径：生成后自动写入交回稿同目录", t, "caption",
                   t.palette.text_faint, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                   tooltip=self.report_path or None),
            ft.Container(expand=True),
            open_btn,
            copy_btn,
            C.text_button("更改…", lambda e: self._change_report(), t, height=28),
        ])
        return ft.Column(spacing=0, controls=[header, *rows, *footer, path_bar])

    def _change_report(self) -> None:
        t = self.t
        field = C.text_field(t, value=self.report_path, hint_text="报告 .md 路径",
                             width=420)
        def _save(e=None) -> None:
            self.report_path = str(field.value or "").strip()
            try:
                self.page.page.pop_dialog()
            except Exception:  # noqa: BLE001
                pass
            self._refresh()
        dialog = ft.AlertDialog(
            modal=True, title=C.text("报告写到哪", t, "h2"),
            content=ft.Container(width=t.px(440), content=field),
            actions=[C.text_button("取消", lambda e: self.page.page.pop_dialog(), t, height=28),
                     C.primary_button("保存路径", _save, t, height=28)])
        try:
            self.page.page.show_dialog(dialog)
        except Exception:  # noqa: BLE001
            self.input_error = "无法打开报告路径对话框。"

    def _stale_notice(self) -> ft.Control | None:
        if not self.report_path or not self.returned:
            return None
        report_time = _stamp(self.report_path)
        returned_time = _stamp(self.returned)
        if not report_time or not returned_time:
            return None
        try:
            stale = (Path(self.returned).stat().st_mtime
                     > Path(self.report_path).stat().st_mtime + 0.5)
        except OSError:
            return None
        if not stale:
            return None
        t = self.t
        return C.notice_bar(f"报告已过期：交回稿 {returned_time} 改过，报告 {report_time}",
                            t, "warning")

    def _banners(self) -> list:
        t = self.t
        parts: list = []
        if self.input_error:
            parts.append(S.state_banner(t, "failure", message=self.input_error,
                                        detail="修正后重试；不会启动引擎。"))
        elif self.error:
            parts.append(S.state_banner(
                t, "failure", message=self.error,
                detail="旧报告未更新。" if self.loaded else "没有写入任何文件。",
                next_label="复制错误", on_next=lambda e: self._copy(self.error),
                page=self.page.page))
        if self.cancelled:
            parts.append(S.state_banner(t, "cancelled",
                                        message="已取消，报告未写入；半份文件按过期处理。",
                                        next_label="重新生成",
                                        on_next=lambda e: self.generate()))
        if self.success and not self.running:
            passed = self.data.get("errors") == 0
            parts.append(S.state_banner(
                t, "success", message=self.success,
                detail=("报告在交回稿同目录；去入藏只会预填交回稿路径。" if passed else None),
                next_label="去入藏" if passed else "打开报告",
                on_next=(lambda e: self._goto("import")) if passed else
                        (lambda e: self._open_report())))
        stale = self._stale_notice()
        if stale is not None:
            parts.append(stale)
        return parts

    def _body(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        parts = [self._paths(), self._range_row(), *self._banners()]
        if self.loading:
            parts.append(S.state_banner(t, "loading", message="正在读取上次报告…"))
        if self.running:
            parts.append(S.state_banner(
                t, "running", message="正在体检：解析交回稿 → 字段对比 → 写报告",
                stage=self.elapsed_text(), on_cancel=lambda e: self.cancel(),
                page=self.page.page))
        if self.identified:
            parts.append(C.inline_note(self.identified, t) if hasattr(C, "inline_note")
                         else C.text(self.identified, t, "caption", t.palette.text_muted))
        parts.append(self._conclusion())
        parts.append(self._rows())
        return ft.Column(spacing=t.sp("1"), scroll=ft.ScrollMode.AUTO, expand=True,
                         controls=parts)

    def _dock(self) -> ft.Control:
        t = self.t
        if self.running:
            return ft.Row(spacing=t.sp("1"), controls=[
                C.text("正在生成报告…", t, "caption", t.palette.text_muted),
                ft.Container(expand=True),
                C.text_button("取消", lambda e: self.cancel(), t, height=28),
            ])
        return C.text("只读 · 只写报告文件，不改库", t, "caption", t.palette.text_faint)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        return S.page_shell(
            self.t, title=TITLE,
            meta="只读 · checkbib 体检，只写报告",
            role_chip="readonly", action=self._open_report_button(),
            on_gear=lambda e: self._goto("settings"),
            body=self._body(), dock=self._dock(), dock_height=S.DOCK_H)

    def _goto(self, view: str) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)
