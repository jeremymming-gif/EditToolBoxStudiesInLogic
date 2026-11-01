"""P3 页面：工作台 / 单篇稿件（3 步）/ 整期装配（6 步）/ 引用库（只读）/ 设置 / 帮助与关于。

页面只做三件事：取值（输入控件）→ 拼参数（services.engine_bridge）→ 显示日志（LogConsole）。
不复制任何引擎判定逻辑；长任务走 asyncio 子进程，不阻塞 UI 线程。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from services import settings_store as SS
from ui import assembly as ASM
from ui import ai_panel as AIP
from ui import bib_advanced as BA
from ui import bib_ops as BO
from ui import bib_ops_dupes as BOD
from ui import bib_ops_health as BOH
from ui import bib_settings as BS
from ui import bib_write as BW
from ui import checks as CK
from ui import components as C
from ui import metadata_form as MF
from ui import normalize as NR
from ui import settings_shell as SHELL
from ui.base_page import BasePage
from ui.help_page import HelpPage
from ui.ia_map import stats as ia_stats

STATUS_TEXT = {"idle": "待运行", "running": "运行中", "done": "已完成", "warn": "有提示", "error": "失败"}


def _level_of_line(text: str) -> str:
    line = text or ""
    if "❌" in line or "错误" in line or "Traceback" in line:
        return "error"
    if "⚠" in line or "警告" in line or "❗" in line:
        return "warn"
    if "✅" in line or "完成。" in line:
        return "ok"
    if EB.is_summary_line(line):
        return "info"
    return "dim"


def _basename(path: str) -> str:
    try:
        return Path(path).name if path else ""
    except (OSError, ValueError):
        return path


# ── 工作台 ─────────────────────────────────────────────────────────────────
class HomePage(BasePage):
    key = "home"

    def build(self) -> ft.Control:
        t = self.t
        engine = self.app.engine_status
        if engine.ok:
            engine_note = (
                f"{engine.describe()} · 版本 {engine.version}"
            )
            engine_chip = C.status_chip("引擎就绪", t, "done")
        else:
            engine_note = "未找到引擎：" + "；".join(engine.problems or ["路径未配置"])
            engine_chip = C.status_chip("引擎缺失", t, "error")

        def entry_card(icon: str, title: str, desc: str, target: str) -> ft.Container:
            return ft.Container(
                expand=True,
                bgcolor=t.palette.surface,
                border=C.box(t),
                border_radius=t.radius("md"),
                padding=C._pad(t, 16, 14, 16, 14),
                on_click=lambda e: self.app.goto(target),
                content=ft.Row(spacing=t.sp("2"), controls=[
                    ft.Container(
                        width=t.px(40), height=t.px(40), border_radius=t.radius("md"),
                        bgcolor=t.palette.accent_soft, alignment=ft.Alignment.CENTER,
                        content=ft.Icon(C.ic(icon, "CIRCLE"), size=t.px(20), color=t.palette.accent),
                    ),
                    ft.Column(spacing=t.sp("half"), expand=True, controls=[
                        C.text(title, t, "h3", weight="semibold"),
                        C.text(desc, t, "small", t.palette.text_muted),
                    ]),
                    ft.Icon(C.ic("CHEVRON_RIGHT", "NAVIGATE_NEXT"), size=t.px(18),
                            color=t.palette.text_faint),
                ]),
            )

        quick = ft.Row(spacing=t.sp("2"), controls=[
            entry_card("DESCRIPTION_OUTLINED", "单篇稿件", "初排转换 / 文献回读校验 / 一校核查", "single"),
            entry_card("VIEW_AGENDA_OUTLINED", "整期装配", "检查 → 目录 → 编译 → 合刊 → 清理 → 推送", "issue"),
            entry_card("LIBRARY_BOOKS_OUTLINED", "引用库", "查库、入藏与待裁决（写库需二次确认）", "bib"),
        ])

        recent = self.app.store.get("recent", {}) or {}
        recent_rows = []
        for key, label in (("single_source", "最近单篇源稿"), ("issue_dir", "最近期目录")):
            value = recent.get(key) or ""
            recent_rows.append(ft.Row(spacing=t.sp("1"), controls=[
                C.text(label, t, "small", t.palette.text_muted),
                ft.Container(expand=True),
                C.text(_basename(value) or "（空）", t, "small", t.palette.text),
            ]))

        body = C.page_body(
            C.notice_bar("已接真引擎：单篇 3 步、整期 6 步、引用库（查库/入藏/待裁决/版本层）。", t, "info"),
            ft.Row(spacing=t.sp("2"), controls=[
                C.card(ft.Column(spacing=t.sp("1"), controls=[
                    ft.Row(spacing=t.sp("2"), controls=[
                        C.text("引擎状态", t, "h3", weight="semibold"),
                        ft.Container(expand=True),
                        engine_chip,
                    ]),
                    C.text(engine_note, t, "small", t.palette.text_muted),
                    C.text(f"应用版本 {self.app.app_version} · Flet {self.app.flet_version}", t,
                           "caption", t.palette.text_faint),
                ]), t, expand=True),
                C.card(ft.Column(spacing=t.sp("1"), controls=[
                    C.text("快捷操作", t, "h3", weight="semibold"),
                    ft.Row(spacing=t.sp("1"), controls=[
                        C.secondary_button("打开设置", lambda e: self.app.goto("settings"), t,
                                           icon="SETTINGS_OUTLINED"),
                        C.secondary_button("环境检查", lambda e: self.app.goto("settings"), t,
                                           icon="CHECK_CIRCLE_OUTLINE"),
                    ]),
                    C.text("环境检查见「设置 → 环境检查」；这里先用引擎版本做探活。", t,
                           "caption", t.palette.text_faint),
                ]), t, expand=True),
            ]),
            C.section_title("两条生产线", t, note="单篇逐篇处理；整期按 6 步流水线"),
            quick,
            C.section_title("继续上次", t, note="最近使用的路径都落在设置文件里"),
            C.card(ft.Column(spacing=t.sp("2"), controls=recent_rows), t),
            C.text("数据只写本地设置文件；不联网、不上传、不动稿件仓。", t, "caption",
                   t.palette.text_faint),
            t=t, gap=14,
        )
        return body


# ── 单篇稿件 ───────────────────────────────────────────────────────────────
class SinglePage(BasePage):
    key = "single"

    FLOWS = {
        "convert": {
            "label": "初排转换",
            "icon": "DESCRIPTION_OUTLINED",
            "desc": "作者来稿（LaTeX / Word）→ 符合本刊规范的可编译初排 tex（可顺带编译 PDF、清理临时文件）",
        },
        "checkbib": {
            "label": "参考文献规范化",
            "icon": "CHECK_CIRCLE_OUTLINE",
            "desc": "交回的 .bib 离线体检（语法/规范/引用键闭合/逐字段对比），只出《回读校验报告》",
        },
        "proofcheck": {
            "label": "一校核查",
            "icon": "SEARCH",
            "desc": "对初排后的 tex 做一校核查，输出核查报告（--fix 另写修正稿）",
        },
    }

    def __init__(self, app) -> None:
        super().__init__(app)
        self.flow_key = "convert"
        self._flow_box = ft.Column(spacing=12)
        self._log = None
        self._result = None
        self._run_btn = None
        self._stop_btn = None
        self._chips_row = None
        self._ai = None
        self._norm = None
        self._meta_panel = None
        self._meta_temp = ""

    # ── 构建 ───────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="运行日志")
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._proof = CK.single_card(self)
        self._run_btn = C.primary_button("运行", self._on_run, t, icon="PLAY_ARROW",
                                         disabled=not self.app.engine_status.ok)
        self._stop_btn = C.secondary_button("停止", self._on_stop, t, icon="STOP", disabled=True)
        self._chips_row = ft.Row(spacing=t.sp("1"), controls=[])
        self._flow_desc = C.text("", t, role="small", color=t.palette.text_muted)
        self._render_chips()
        header = C.page_header(
            "单篇稿件", "一次只做一件事：选文件 → 勾选项 → 运行；日志与产物都在这一页。", t,
            actions=[self._stop_btn, self._run_btn],
        )
        self._flow_box.controls = [self._build_convert()]
        self._proof.root.visible = False
        status = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            self._chips_row,
            ft.Container(expand=True),
            C.busy_bar(t, visible=False),
        ])
        self._busy = status.controls[2]
        flow_bar = ft.Column(spacing=t.sp("half"), controls=[status, self._flow_desc])
        body = C.page_body(header, flow_bar, self._flow_box, self._result.root,
                           self._proof.root, self._log.root, t=t, gap=10)
        self.refresh_running()
        return body

    def _render_chips(self) -> None:
        t = self.t
        self._chips_row.controls = [
            C.chip(self.FLOWS[key]["label"], t, active=(key == self.flow_key),
                   on_click=lambda e, k=key: self._switch_flow(k))
            for key in self.FLOWS
        ]
        self._flow_desc.value = self.FLOWS[self.flow_key]["desc"]

    def _switch_flow(self, key: str) -> None:
        if self.app.running:
            self.app.toast("运行中不能切换步骤，先停止")
            return
        self.flow_key = key
        self._ai = None
        self._render_chips()
        builder = {
            "convert": self._build_convert,
            "checkbib": self._build_checkbib,
            "proofcheck": self._build_proofcheck,
        }[key]
        self._flow_box.controls = [builder()]
        self._result.reset()
        CK.on_flow_switch(self)
        self._safe_update(self._chips_row, self._flow_desc, self._flow_box)

    # ── 三个步骤的输入卡 ────────────────────────────────────────────────
    def _build_convert(self) -> ft.Control:
        t, p = self.t, self.t.palette
        options = self.app.store.get("options", {}) or {}
        rows = [
            C.option_row("编译 PDF", "调用 xelatex 生成正文 PDF（长任务，日志实时滚动）", t,
                         icon="PICTURE_AS_PDF_OUTLINED", checked=bool(options.get("convert_compile", True)),
                         on_change=lambda e: self.app.set_option("convert_compile", e.control.value),
                         badge_label="主要"),
            C.option_row("清理临时文件", "删除 .aux / .log / .out 等中间文件", t,
                         icon="CLEANING_SERVICES", checked=bool(options.get("convert_clean", False)),
                         on_change=lambda e: self.app.set_option("convert_clean", e.control.value)),
            C.option_row("导出原稿 PDF", "保留原始稿件并另存一份 PDF 备查", t,
                         icon="PICTURE_AS_PDF_OUTLINED", checked=bool(options.get("convert_source_pdf", False)),
                         on_change=lambda e: self.app.set_option("convert_source_pdf", e.control.value)),
            C.option_row("强制覆盖（--force）", "产物已存在时仍重跑并覆盖（谨慎）", t,
                         icon="WARNING_AMBER_ROUNDED", checked=bool(options.get("convert_force", False)),
                         on_change=lambda e: self.app.set_option("convert_force", e.control.value)),
        ]
        advanced = ft.ExpansionTile(
            title=C.text("高级参数（卷 / 期 / 年 / 页码 / 收稿）", t, "small", p.text_muted),
            subtitle=C.text("缺省不填；填了才传给引擎", t, "caption", p.text_faint),
            controls=[ft.Column(spacing=t.sp("1"), controls=[
                ft.Row(spacing=t.sp("1"), controls=[
                    self._meta_field("convert_vol", "卷", 90),
                    self._meta_field("convert_issue", "期", 90),
                    self._meta_field("convert_year", "年份", 110),
                    self._meta_field("convert_pages", "页码", 110),
                    self._meta_field("convert_received", "收稿日期", 150),
                ]),
                ft.Row(spacing=t.sp("1"), controls=[
                    self._meta_field("convert_author", "作者", 200),
                    self._meta_field("convert_name", "篇名", 320),
                    self._meta_field("convert_bib", "参考文献 .bib", 320),
                ]),
                ft.Row(spacing=t.sp("1"), controls=[
                    self._meta_field("convert_lang", "语种（zh / en，可空）", 240),
                    self._meta_field("convert_editor", "责编", 160),
                    self._meta_field("convert_grants", "基金", 300),
                ]),
                ft.Row(spacing=t.sp("1"), controls=[
                    self._meta_field("convert_refs", "附加参考文献（--refs，可空）", 320),
                    self._meta_field("convert_sty", "样式文件（--sty，可空）", 300),
                    self._meta_field("convert_skeleton", "模板骨架（--skeleton，可空）", 300),
                ]),
            ])],
            expanded=False,
        )
        return ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("输入", t, note="源稿件必填 · 输出根目录必填"),
            ft.Row(spacing=t.sp("1"), controls=[
                ft.Container(expand=True, content=self.add_picker("源稿件", "single_source",
                                                                  "作者来稿 .tex / .docx", kind="file")),
            ]),
            self.add_picker("输出根目录", "single_out", "产物写到该目录下的 <作者>/ 里", kind="dir"),
            C.section_title("处理选项", t, note="默认勾选常用项"),
            C.card(ft.Column(spacing=0, controls=self._interleave(rows)), t),
            C.section_title("元数据（人工填写，可选）", t,
                            note="表单 → --metadata-json；「预览元数据」= 同一 argv + --metadata-preview-out"),
            self._build_metadata_section(),
            advanced,
            C.text(SS.bibdb_convert_hint(self.app.store.data), t, "caption", p.text_faint),
            self._ai_section(),
        ])

    def _ai_section(self) -> ft.Control:
        self._ai = AIP.AiPanel(self)
        return self._ai.build()

    def _build_checkbib(self) -> ft.Control:
        # 批 109 W2a：整页交给 ui/normalize.py（改名「参考文献规范化」+ 粘贴模式 +
        # 报告默认路径 + 识别；Wave 0 / S1：**没有任何并回入口**）。
        self._norm = NR.NormalizePanel(self)
        return self._norm.build()

    def _build_metadata_section(self) -> ft.Control:
        t = self.t
        self._meta_panel = MF.MetadataPanel(self)
        return ft.Column(spacing=t.sp("1"), controls=[
            self._meta_panel.build(),
            ft.Row(spacing=t.sp("1"), controls=[
                C.secondary_button("预览元数据", self._on_metadata_preview, t, icon="PREVIEW"),
            ]),
        ])

    def _build_proofcheck(self) -> ft.Control:
        return CK.proofcheck_section(self)

    @staticmethod
    def _interleave(rows: list) -> list:
        controls: list = []
        for index, row in enumerate(rows):
            if index:
                controls.append(ft.Divider(height=1))
            controls.append(row)
        return controls

    # ── 运行 ───────────────────────────────────────────────────────────
    def _on_run(self, e=None) -> None:
        if self.app.running:
            return
        try:
            call = self._build_call()
        except ValueError as exc:
            self.app.toast(str(exc))
            self.mark("validation_error", reason=str(exc))
            return
        except RuntimeError as exc:
            self.app.toast(str(exc))
            return
        self.app.mark("form_submit", flow=self.flow_key, call=call.to_dict())
        # 批 109 W2a 返工：只有「参考文献规范化」的粘贴模式才读 stdin；切流程后残留的
        # _norm 不许把粘贴正文漏进 convert/proofcheck（它们不读 stdin）。
        stdin_text = None
        if self.flow_key == "checkbib" and self._norm is not None:
            stdin_text = self._norm.stdin_text()
        self.page.run_task(self._run, call, stdin_text)

    def _on_metadata_preview(self, e=None) -> None:
        """CV-002/003：跑与「开始转换」同一 argv，只多 --metadata-preview-out（只读）。"""
        if self.app.running:
            return
        preview_path = os.path.join(tempfile.gettempdir(),
                                    f"slcn_metadata_preview_{os.getpid()}.json")
        try:
            call = self._build_call(preview_path=preview_path)
        except ValueError as exc:
            self.app.toast(str(exc))
            return
        self.app.mark("metadata_preview", call=call.to_dict())
        self.page.run_task(self._run, call, None, preview_path)

    def _on_paste_preview(self) -> None:
        """NR-003/015：--list-keys + --returned-stdin，只打印「N 条：key…」，永不写文件。"""
        text = self._norm.pasted_text() if self._norm is not None else ""
        self.page.run_task(self._run_paste_preview, NR.preview_call(), text)

    async def _run_paste_preview(self, call: EB.EngineCall, text: str) -> None:
        lines: list[str] = []
        code = await EB.run_call(call, on_line=lines.append, stdin_text=text)
        if self._norm is not None:
            self._norm.set_preview_line(NR.preview_text(code, lines, text))

    def _on_stop(self, e=None) -> None:
        self.app.stop_run()

    def _build_call(self, preview_path: str = "") -> EB.EngineCall:
        if self.flow_key == "convert":
            source = self.field("single_source")
            out = self.field("single_out")
            if not source:
                raise ValueError("请先选择源稿件")
            if not out:
                raise ValueError("请先选择输出根目录")
            options = self.app.store.get("options", {}) or {}
            kwargs = dict(
                source=source, out=out,
                compile_pdf=bool(options.get("convert_compile", True)),
                clean=bool(options.get("convert_clean", False)),
                source_pdf=bool(options.get("convert_source_pdf", False)),
                vol=self.field("convert_vol"), issue=self.field("convert_issue"),
                year=self.field("convert_year"), pages=self.field("convert_pages"),
                received=self.field("convert_received"), author=self.field("convert_author"),
                name=self.field("convert_name"), bib=self.field("convert_bib"),
                lang=self.field("convert_lang"), editor=self.field("convert_editor"),
                grants=self.field("convert_grants"), refs=self.field("convert_refs"),
                sty=self.field("convert_sty"), skeleton=self.field("convert_skeleton"),
                force=bool(options.get("convert_force", False)),
                metadata_preview_out=preview_path,
            )
            if self._meta_panel is not None:
                # 人工元数据：写临时 JSON（运行结束删除），argv 里是 --metadata-json。
                self._cleanup_meta_temp()
                self._meta_temp, _text = self._meta_panel.payload_for_run()
                kwargs["metadata_json"] = self._meta_temp
            if self._ai is not None:
                # 批 109 W1b（R6）：引用库设置只在核实链路（AI 面板 → verify_refs）
                # 进 argv；普通初排没有核实入口，绝不注入 --verify-plan（否则会变成只算账）。
                bibdb = SS.bibdb_convert_args(self.app.store.data)
                return self._ai.build_call(**kwargs, **bibdb)
            return EB.convert_call(**kwargs)
        if self.flow_key == "checkbib":
            # 批 109 Wave 0 / S1：只做离线体检/报告；正文 tex 仅作 --tex 只读输入，
            # 不再传写入型 --merge-into（原实现会把正文 .tex 覆盖写回）。
            if self._norm is None:
                raise ValueError("参考文献规范化页还没准备好，请重开该步骤")
            return self._norm.build_call()
        tex = self.field("proof_tex")
        if not tex:
            raise ValueError("请先选择初排 tex")
        options = self.app.store.get("options", {}) or {}
        return EB.proofcheck_call(
            tex=tex, lang=self.field("proof_lang"),
            fix=bool(options.get("proofcheck_fix", False)),
        )

    async def _run(self, call: EB.EngineCall, stdin_text: str | None = None,
                   preview_path: str = "") -> None:
        page = self.page
        self.app.begin_run(call)
        self._result.reset()
        self._log.clear()
        CK.before_run(self)
        CK.on_proof_log(self)
        panel = self._ai if self.flow_key == "convert" else None
        if panel is not None:
            panel.reset_run()
        try:
            code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                     api=panel.api_env() if panel is not None else None,
                                     notes=self.app.notes_repo, stdin_text=stdin_text)
        except Exception as exc:  # noqa: BLE001 - 引擎缺失/启动失败一律给中文提示
            self.app.finish_run(call, -1)
            text = f"❌ 任务启动失败：{exc}"
            self._result.show(text, level="error")
            self.app.toast(f"任务启动失败：{exc}")
            self.mark("run_start_error", error=f"{type(exc).__name__}: {exc}")
            return
        self.app.finish_run(call, code)
        level = "warn" if self.app.cancel_requested else EB.result_level(call.entry, code)
        text = "⏹ 已取消" if self.app.cancel_requested else EB.result_text(call.entry, code)
        if panel is not None:
            notice = panel.budget_notice()
            if notice:
                text, level = notice, "error"
            panel.after_run(call, code)
        artifact = EB.artifact_path(self.app.last_line)
        text, level = CK.after_proof_run(self, call, code, artifact, text, level)
        if preview_path:
            self._show_metadata_preview(preview_path)
            if not self.app.cancel_requested:
                text, level = "元数据预览完成（只读：未建产物目录、未写产物）", "info"
                artifact = ""
        if self.flow_key == "checkbib" and self._norm is not None:
            self._echo_checkbib_report(code)  # NR-010：报告正文进日志（不改 Engine）
            self._norm.refresh_after_run()
        self._result.show(text, level=level, artifact=artifact)
        if artifact:
            self.app.store.set_recent(self._artifact_recent_key(), artifact)
        self._cleanup_meta_temp()
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

    def _echo_checkbib_report(self, code: int) -> None:
        """NR-010：引擎带 --out 时只回一行结论，这里把报告正文逐行贴进运行日志。"""
        if self.app.cancel_requested or code not in (0, 1):
            return
        report = getattr(self._norm, "last_report", "") if self._norm is not None else ""
        if not report:
            self._on_line("（未生成报告：没有可回显的报告路径，只有上面的结论行）")
            return
        try:
            with open(report, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            self._on_line(f"（报告读取失败：{report}；只有上面的结论行，可稍后手动打开）")
            return
        self._on_line(f"== 文本报告：{os.path.basename(report)} ==")
        for line in lines:
            self._on_line(line)

    def _show_metadata_preview(self, path: str) -> None:
        payload = None
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = None
        MF.remove_temp(path)
        if self._meta_panel is not None:
            self._meta_panel.show_preview(payload)

    def _cleanup_meta_temp(self) -> None:
        if self._meta_temp:
            MF.remove_temp(self._meta_temp)
            self._meta_temp = ""

    def _artifact_recent_key(self) -> str:
        return {
            "convert": "single_out",
            "checkbib": "bib_out",
            "proofcheck": "proof_tex",
        }[self.flow_key]

    def _on_line(self, line: str) -> None:
        self.app.record_line(line)
        summary = EB.is_summary_line(line)
        self._log.add_line(line, _level_of_line(line), summary)
        if self._ai is not None and self.flow_key == "convert":
            self._ai.on_line(line)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    # ── 运行态刷新（由 App 调） ─────────────────────────────────────────
    def refresh_running(self) -> None:
        running = self.app.running
        if self._run_btn is not None:
            self._run_btn.disabled = running or not self.app.engine_status.ok
        if self._stop_btn is not None:
            self._stop_btn.disabled = not running
        if getattr(self, "_busy", None) is not None:
            self._busy.visible = running
        if self._ai is not None:
            self._ai.refresh_running()
        self._safe_update(*(c for c in (self._run_btn, self._stop_btn, getattr(self, "_busy", None)) if c is not None))

    # 证据钩子 ─────────────────────────────────────────────────────────
    def prefill(self, *, flow: str = "convert", source: str = "", out: str = "", bib: str = "",
                proof_tex: str = "") -> None:
        if flow in self.FLOWS:
            self.flow_key = flow
        self.set_field("single_source", source)
        if out:
            self.set_field("single_out", out)
        if bib:
            self.set_field("bib_returned", bib)
        if proof_tex:
            self.set_field("proof_tex", proof_tex)
        self.mark("demo_prefill", flow=self.flow_key, source=source, out=out, bib=bib)

    def run_demo(self) -> None:
        self._on_run()


# ── 整期装配 ───────────────────────────────────────────────────────────────
class IssuePage(BasePage):
    key = "issue"

    STEPS = [
        {"key": "infocheck", "title": "检查tex信息", "icon": "FACT_CHECK_OUTLINED",
         "desc": "扫描整期 tex，输出 10 列清单（篇目/页码/字号行距…）"},
        {"key": "toc", "title": "生成目录", "icon": "TOC", "desc": "用信息检查报告生成目录 tex（可落位编译）"},
        {"key": "compileall", "title": "编译全部", "icon": "PICTURE_AS_PDF_OUTLINED",
         "desc": "逐篇 xelatex→biber→xelatex×2；期目录形如 20xx年第N期(…)，同名 PDF 直接覆盖（跳过 000 目录）"},
        {"key": "merge", "title": "合并文件", "icon": "MERGE", "desc": "把各篇 PDF 合为一期合刊"},
        {"key": "cleantemp", "title": "删除临时文件", "icon": "CLEANING_SERVICES",
         "desc": "三档清理：辅助文件 / 交付收尾 / 工作区（危险）"},
        {"key": "dirpush", "title": "目录摘要推送", "icon": "PUSH_PIN_OUTLINED",
         "desc": "按刊期生成当期目录摘要 docx（可只预演）"},
    ]

    def __init__(self, app) -> None:
        super().__init__(app)
        self.selected = "infocheck"
        self._steps_box = ft.Column(spacing=6)
        self._params_box = ft.Column(spacing=10)
        self._log = None
        self._result = None
        self._info = None
        self._run_btn = None
        self._stop_btn = None
        self._run_all_btn = None
        self._status: dict[str, str] = {step["key"]: "idle" for step in self.STEPS}

    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="整期日志")
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._info = CK.issue_card(self)
        disabled = not self.app.engine_status.ok
        self._run_all_btn = C.primary_button("运行整期流水线", lambda e: self._run_all(),
                                            t, icon="PLAY_ARROW", disabled=disabled)
        self._stop_btn = C.secondary_button("取消", self._on_stop, t, icon="STOP", disabled=True)
        header = C.page_header(
            "整期装配", "一期稿件从检查到合刊是一条固定流水线：一屏看得到进度、当前步骤和日志。", t,
            actions=[self._run_all_btn, self._stop_btn],
        )
        self._steps_box.controls = [self._step_row(i, step) for i, step in enumerate(self.STEPS)]
        self._params_box.controls = [self._build_params(self.selected)]
        self._issue_picker = self.add_picker("期目录", "issue_dir", "整期根目录（扉页/000/01 作者…）", kind="dir")
        layout = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Column(expand=5, spacing=t.sp("2"), controls=[
                C.card(ft.Column(spacing=t.sp("2"), controls=[
                    C.section_title("期目录", t, note="必填 · 一次运行会按顺序执行 6 步"),
                    self._issue_picker,
                ]), t),
                C.card(ft.Column(spacing=8, controls=[
                    C.section_title("整期流水线", t, note="6 个步骤 · 按顺序执行"),
                    self._steps_box,
                ]), t),
            ]),
            ft.Column(expand=6, spacing=t.sp("2"), controls=[
                C.card(ft.Column(spacing=t.sp("2"), controls=[
                    C.section_title("当前步骤参数", t, note="点左侧步骤切换"),
                    self._params_box,
                ]), t),
                self._result.root,
                self._info.root,
                self._log.root,
            ]),
        ])
        body = C.page_body(header, layout, t=t, gap=10)
        self.refresh_running()
        return body

    # ── 步骤列表 ───────────────────────────────────────────────────────
    def _step_row(self, index: int, step: dict) -> ft.Container:
        t = self.t
        selected = step["key"] == self.selected
        state = self._status.get(step["key"], "idle")
        color = {"done": t.palette.success, "running": t.palette.accent,
                 "warn": t.palette.warn, "error": t.palette.error}.get(state, t.palette.text_faint)
        return ft.Container(
            bgcolor=t.palette.accent_soft if selected else None,
            border=C.box(t, t.palette.accent_soft if selected else t.palette.border),
            border_radius=t.radius("md"),
            padding=C._pad(t, 12, 10, 12, 10),
            on_click=lambda e, k=step["key"]: self._select(k),
            content=ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Container(
                    width=t.px(26), height=t.px(26), border_radius=t.radius("pill"),
                    bgcolor=t.palette.surface if not selected else t.palette.surface_2,
                    alignment=ft.Alignment.CENTER,
                    content=C.text(str(index + 1), t, "small", t.palette.text_muted, "semibold"),
                ),
                ft.Icon(C.ic(step["icon"], "CIRCLE"), size=t.px(18), color=color),
                ft.Column(spacing=0, expand=True, controls=[
                    C.text(step["title"], t, "body", weight="semibold"),
                    C.text(step["desc"], t, "caption", t.palette.text_faint),
                ]),
                C.status_chip(STATUS_TEXT.get(state, "待运行"), t, state),
                C.text_button("运行", lambda e, k=step["key"]: self._run_step(k), t),
            ]),
        )

    def _select(self, key: str) -> None:
        if self.app.running:
            self.app.toast("运行中不能切换步骤，先取消")
            return
        self.selected = key
        self._steps_box.controls = [self._step_row(i, step) for i, step in enumerate(self.STEPS)]
        self._params_box.controls = [self._build_params(key)]
        self._safe_update(self._steps_box, self._params_box)

    # ── 各步骤参数 ─────────────────────────────────────────────────────
    def _build_params(self, key: str) -> ft.Control:
        self.fields = {k: v for k, v in self.fields.items() if k in ("issue_dir", "toc_report")}
        return ASM.build_params(self, key)

    # ── 构建调用 ───────────────────────────────────────────────────────
    def _issue_dir(self) -> str:
        return self.field("issue_dir")

    def _build_step_call(self, key: str) -> EB.EngineCall:
        return ASM.build_step_call(self, key)

    @staticmethod
    def _latest_report(issue: str) -> str:
        return ASM.TOC.latest_report(issue)

    # ── 运行 ───────────────────────────────────────────────────────────
    def _run_step(self, key: str) -> None:
        ASM.run_step(self, key)

    def _run_all(self) -> None:
        ASM.run_all(self)

    def _on_stop(self, e=None) -> None:
        self.app.stop_run()

    async def _run_calls(self, calls: list, sequential: bool = False) -> None:
        last_code = 0
        for key, call in calls:
            if self.app.cancel_requested:
                break
            self._status[key] = "running"
            self._refresh_steps()
            self.app.begin_run(call)
            self._log.clear()
            self._result.reset()
            CK.before_issue_step(self, key)
            try:
                code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                         notes=self.app.notes_repo)
            except Exception as exc:  # noqa: BLE001 - 引擎缺失/启动失败一律给中文提示
                self.app.finish_run(call, -1)
                self._status[key] = "error"
                self._refresh_steps()
                self._result.show(f"❌ 任务启动失败：{exc}", level="error")
                self.app.toast(f"任务启动失败：{exc}")
                self.mark("run_start_error", step=key, error=f"{type(exc).__name__}: {exc}")
                return
            self.app.finish_run(call, code)
            cancelled = self.app.cancel_requested
            self._status[key] = "error" if (code != 0 and not cancelled) else ("warn" if code == 1 else "done")
            if cancelled:
                self._status[key] = "idle"
            self._refresh_steps()
            artifact = EB.artifact_path(self.app.last_line)
            await CK.after_issue_step(self, key, call, code, cancelled, artifact)
            ASM.after_step(self, key, call, code, cancelled, artifact)
            level = "warn" if cancelled else EB.result_level(call.entry, code)
            text = "⏹ 已取消" if cancelled else EB.result_text(call.entry, code)
            self._result.show(text, level=level, artifact=artifact)
            if artifact:
                self.app.store.set_recent("issue_dir", str(Path(self._issue_dir())))
            last_code = code
            try:
                self.page.update()
            except Exception:  # noqa: BLE001
                pass
            if cancelled:
                break
        self._refresh_steps()

    def _on_line(self, line: str) -> None:
        self.app.record_line(line)
        self._log.add_line(line, _level_of_line(line), EB.is_summary_line(line))

    def _refresh_steps(self) -> None:
        self._steps_box.controls = [self._step_row(i, step) for i, step in enumerate(self.STEPS)]
        self._safe_update(self._steps_box)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    def refresh_running(self) -> None:
        running = self.app.running
        disabled = not self.app.engine_status.ok
        if self._run_all_btn is not None:
            self._run_all_btn.disabled = running or disabled
        if self._stop_btn is not None:
            self._stop_btn.disabled = not running
        self._safe_update(*(c for c in (self._run_all_btn, self._stop_btn) if c is not None))

    # 证据钩子 ─────────────────────────────────────────────────────────
    def prefill(self, issue_dir: str = "", toc_report: str = "") -> None:
        if issue_dir:
            self.set_field("issue_dir", issue_dir)
            self.app.store.set_recent("issue_dir", issue_dir)
        if toc_report:
            self.set_field("toc_report", toc_report)
        self.mark("demo_prefill", issue_dir=issue_dir, toc_report=toc_report)

    def select_step(self, key: str) -> None:
        self.selected = key
        self._steps_box.controls = [self._step_row(i, step) for i, step in enumerate(self.STEPS)]
        self._params_box.controls = [self._build_params(key)]
        self._safe_update(self._steps_box, self._params_box)
        self.mark("step_selected", step=key)

    def run_step_demo(self, key: str) -> None:
        self._run_step(key)

    def run_all_demo(self) -> None:
        self._run_all()


# ── 引用库（P3 实装，P2 保留入口）────────────────────────────────────────
class BibPage(BasePage):
    """引用库（P6）：概况/查库只读；入藏与待裁决是真写（二次确认 + sha 闸）。

    写库一律先干跑预览、再带 `--expect-db-sha256` 真写，写完自动本地提交
    （只提交 bib_db/bibliography.jsonl）；推送/回退要二次确认（B-190 契约）。
    """

    key = "bib"
    VIEWS = [("overview", "概况"), ("query", "查库"), ("import", "入藏"),
             ("review", "待处理"), ("settings", "设置"),
             ("edit", "编辑"), ("dupes", "查重合并"), ("fieldfix", "字段归位"),
             ("health", "库健康")]

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = "overview"
        self.stats_data: dict | None = None
        self.stats_error = ""
        self.query_field: ft.TextField | None = None  # P2_BIB_QUERY_UI 自检钩子用
        self.view_slot: ft.Container | None = None
        self.query_view = BA.BibQueryView(self)
        self.settings_view = BS.BibSettingsView(self)
        self.import_view = BW.BibImportView(self)
        self.review_view = BW.BibReviewView(self)
        self.editor_view = BO.BibEditorView(self)
        self.dupes_view = BOD.BibDuplicatesView(self)
        self.fieldfix_view = BOH.BibFieldFixView(self)
        self.health_view = BOH.BibHealthView(self)

    def build(self) -> ft.Control:
        t = self.t
        self.view_slot = ft.Container(expand=True)
        header = C.page_header("引用库", "查库与统计只读；入藏 / 待裁决是写库操作（预览后二次确认）。", t,
                               actions=[
                                   C.secondary_button("刷新统计", lambda e: self.page.run_task(self._load_stats), t,
                                                      icon="REFRESH"),
                               ])
        chips = ft.Row(spacing=t.sp("1"), wrap=True,
                       controls=[C.chip(label, t, active=(key == self.view),
                                        on_click=lambda e, k=key: self._switch(k))
                                 for key, label in self.VIEWS])
        notice = C.notice_bar("写库：编辑 / 查重合并 / 字段归位 / 批量裁决都要先预览、再二次确认，"
                              "写前校验库 sha256（不符即拒绝、零写入）；写成功后自动本地提交"
                              "（每次写库 1 条提交，批量操作也只 1 条；只提交 bib_db/bibliography.jsonl、"
                              "不推送）。库健康视图只读；「确认非重复」只写 sidecar、不改库。", t, "info")
        body = C.page_body(header, chips, notice, self.view_slot, t=t, gap=12)
        self._render()
        return body

    # ── 视图切换 / 数据加载 ─────────────────────────────────────────────
    def _switch(self, view: str) -> None:
        self.view = view
        self.mark("bib_view", view=view)
        self._render()
        if view == "overview" and self.stats_data is None:
            self.page.run_task(self._load_stats)
        elif view == "review":
            self.review_view.ensure_loaded()
        elif view == "settings":
            self.page.run_task(self.settings_view.ensure_loaded)
        elif view == "dupes":
            self.page.run_task(self.dupes_view.ensure_loaded)
        elif view == "fieldfix":
            self.page.run_task(self.fieldfix_view.ensure_loaded)
        elif view == "health":
            self.page.run_task(self.health_view.ensure_loaded)

    async def _load_stats(self) -> None:
        try:
            call = EB.bibdb_stats_call()
            result = await asyncio.to_thread(EB.read_json_command, call)
        except RuntimeError as exc:
            self.stats_error = str(exc)
            result = {"ok": False, "error": str(exc), "json": {}}
        self.stats_data = result.get("json") or None
        self.stats_error = "" if result.get("ok") else str(result.get("error") or "读取失败")
        stats = self.stats_data or {}
        aliases = stats.get("aliases") or {}
        conflicts = stats.get("conflicts") or {}
        self.mark("bib_stats", ok=bool(result.get("ok")), code=result.get("code"),
                  records=aliases.get("records"), aliases_total=aliases.get("total"),
                  conflicts=conflicts.get("records"), bytes=stats.get("bytes"))
        self._render()

    async def _run_query(self) -> None:
        """兼容 `P2_BIB_QUERY_UI` 自检钩子：把旧入口接到新的查库视图（基本检索 / 题名）。"""
        value = (self.query_field.value or "").strip() if self.query_field else ""
        if value:
            self.query_view.basic_text = value
        await self.query_view._run_basic()

    # ── 渲染 ───────────────────────────────────────────────────────────
    def _render(self) -> None:
        if self.view_slot is None:
            return
        builders = {
            "overview": self._overview_view,
            "query": self.query_view.build,
            "import": self.import_view.build,
            "review": self.review_view.build,
            "settings": self.settings_view.build,
            "edit": self.editor_view.build,
            "dupes": self.dupes_view.build,
            "fieldfix": self.fieldfix_view.build,
            "health": self.health_view.build,
        }
        builder = builders.get(self.view, self._overview_view)
        try:
            content = builder()
        except Exception as exc:  # noqa: BLE001
            import traceback

            frames = traceback.extract_tb(exc.__traceback__)
            self.mark("bib_render_error", error=f"{type(exc).__name__}: {exc}",
                      where=[f"{f.name}:{f.lineno}" for f in frames][-4:])
            return
        self.view_slot.content = content
        try:
            # 构建期（未挂载）update 会抛 RuntimeError；忽略，等页面显示后自然生效。
            self.view_slot.update()
        except Exception:  # noqa: BLE001
            pass

    def _overview_view(self) -> ft.Control:
        t = self.t
        stats = self.stats_data or {}
        aliases = stats.get("aliases") or {}
        conflicts = stats.get("conflicts") or {}
        entry_types = stats.get("entry_types") or {}
        size_mb = (stats.get("bytes") or 0) / 1024 / 1024
        cards = ft.Row(spacing=t.sp("2"), controls=[
            C.stat_card("条目总数", str(aliases.get("records") or "—"),
                        f"别名合计 {aliases.get('total') or '—'} 条 · 只读", t),
            C.stat_card("冲突 / 待裁决", str(conflicts.get("records") or "0"),
                        f"冲突合计 {conflicts.get('total') or 0} 条", t),
            C.stat_card("库文件", f"{size_mb:.1f} MB" if stats.get("bytes") else "—",
                        "本地 jsonl · 不联网", t),
        ])
        type_line = "、".join(f"{k} {v}" for k, v in list(entry_types.items())[:6]) or "—"
        info = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("库信息", t, note="只读"),
            self._kv("库文件", str(stats.get("db") or "（未知）")),
            self._kv("条目类型", type_line),
            self._kv("创建时间范围", " → ".join(stats.get("created_range") or []) or "—"),
            self._kv("工作区脏标记", "dirty" if stats.get("dirty") else "clean"),
            self._kv("重复内容", str(stats.get("duplicate_content") or 0)),
            self._kv("读取状态", self.stats_error or ("已读取" if self.stats_data else "未读取")),
        ]), t)
        return ft.Column(spacing=t.sp("2"), controls=[cards, info])

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text),
        ])

    def on_show(self) -> None:
        if self.view == "overview" and self.stats_data is None:
            self.page.run_task(self._load_stats)
        elif self.view == "review":
            self.review_view.ensure_loaded()
        elif self.view == "settings":
            self.page.run_task(self.settings_view.ensure_loaded)
        elif self.view == "dupes":
            self.page.run_task(self.dupes_view.ensure_loaded)
        elif self.view == "fieldfix":
            self.page.run_task(self.fieldfix_view.ensure_loaded)
        elif self.view == "health":
            self.page.run_task(self.health_view.ensure_loaded)

    def refresh_running(self) -> None:
        pass



# ── 设置 ───────────────────────────────────────────────────────────────────
class SettingsPage(BasePage):
    key = "settings"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.envcheck_data: dict | None = None
        self.envcheck_error = ""
        self.envcheck_slot = ft.Container()
        self.connection_slot = ft.Container()

    def build(self) -> ft.Control:
        t = self.t
        engine = self.app.engine_status
        status_rows = [
            ("运行方式", engine.describe()),
            ("引擎目录", engine.engine_dir or "（未找到）"),
            ("引擎版本", engine.version),
            ("问题", "；".join(engine.problems) if engine.problems else "无"),
            ("设置文件", str(self.app.store.path)),
        ]
        engine_card = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("引擎", t, note="探测只读；诊断包与环境检查见下方"),
            *[self._kv(label, value) for label, value in status_rows],
            ft.Row(spacing=t.sp("1"), controls=[
                C.secondary_button("重新检测", lambda e: self._rescan(), t, icon="REFRESH"),
                C.secondary_button("引擎目录…", lambda e: self._browse_engine(), t, icon="FOLDER_OPEN_OUTLINED"),
            ]),
        ]), t)

        options = self.app.store.get("options", {}) or {}
        appearance = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("外观", t, note="深浅色与缩放都会落盘，下次启动恢复"),
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("深色模式", t, "body"),
                ft.Container(expand=True),
                ft.Switch(value=self.app.t.is_dark, active_color=t.palette.accent,
                          on_change=self.app.on_theme_switch),
            ]),
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("界面缩放", t, "body"),
                ft.Slider(min=0.8, max=2.0, divisions=12, value=float(self.app.store.get("zoom", 1.0)),
                          active_color=t.palette.accent, expand=True,
                          on_change=self._zoom_changed),
                C.text(f"{self.app.effective_zoom:.2f}×（上限 {self.app.max_zoom:.2f}×）", t,
                       "small", t.palette.text_muted, "medium"),
            ]),
            C.text(f"屏幕工作区钳制：本屏最大 {self.app.max_zoom:.2f}×；更大缩放会被限制，窗口不会越界。", t,
                   "caption", t.palette.text_faint),
        ]), t)

        tray = SHELL.tray_card(self)

        paths = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("路径", t, note="引擎目录留空 = 自动定位"),
            self._path_field("engine_override", "引擎目录（可空）", kind="dir"),
            self._path_field("notes_repo", "私有笔记仓（可空）", kind="dir"),
        ]), t)

        about = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("关于", t),
            self._kv("应用版本", self.app.app_version),
            self._kv("Flet / Python", f"{self.app.flet_version} · {self.app.python_version}"),
            self._kv("窗口 / DPI", f"{self.app.window_w}×{self.app.window_h} @ {self.app.dpi} ({self.app.dpi / 96:.0%})"),
            self._kv("IA", f"{ia_stats()['legacy_pages']} 个旧页 → {ia_stats()['new_pages']} 页 + 子流程"),
            self._kv("许可", "专有软件 · © 2026 Jeremymming（第三方许可见 THIRD_PARTY.md）"),
        ]), t)

        envcheck = SHELL.envcheck_card(self)
        proxy = SHELL.proxy_card(self)
        logs = SHELL.logs_card(self)
        advanced = SHELL.advanced_card(self)
        diagnostics = SHELL.diagnostics_card(self)

        header = C.page_header(
            "设置", "外观、引擎、路径、环境检查与诊断；所有设置持久化，不依赖系统注册表。", t,
            actions=[C.primary_button("立即保存", lambda e: self.app.save_settings(toast=True), t,
                                      icon="SAVE_OUTLINED")],
        )
        page = C.page_body(header, engine_card, appearance, tray, paths, AIP.AiSettings(self).build(),
                           envcheck, proxy, logs, advanced, about, diagnostics, t=t, gap=12)
        return page

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text),
        ])

    def _path_field(self, key: str, label: str, *, kind: str = "dir") -> ft.Container:
        ref: list = []
        value = self.app.store.get(f"paths.{key}", "") or self.app.store.get(key, "") or ""
        if key == "engine_override":
            value = self.app.store.get("engine_override", "") or ""

        def _browse(e) -> None:
            self.app.browse_field(ref, f"选择{label}", kind, key, store_prefix="paths")

        control = C.path_picker(label, value, "", self.t, required=False,
                                on_browse=_browse, show_note=False, field_ref=ref)
        self.fields[key] = ref
        return control

    def _zoom_changed(self, e) -> None:
        value = round(float(e.control.value), 2)
        if abs(value - self.app.store.get("zoom", 1.0)) > 1e-6:
            self.page.run_task(self._apply_zoom, value)

    async def _apply_zoom(self, value: float) -> None:
        self.app.set_zoom(value)

    def _rescan(self) -> None:
        self.app.refresh_engine_status()
        self.app.rebuild_shell()
        self.app.toast("引擎状态已刷新")

    def _browse_engine(self) -> None:
        ref = self.fields.get("engine_override")
        self.app.browse_field(ref, "选择引擎目录", "dir", "engine_override", store_prefix="paths")

    # ── 环境检查（只读，调 Engine/envcheck.py --json）───────────────────
    async def _envcheck_task(self) -> None:
        t = self.t
        try:
            self.envcheck_slot.content = C.text("检查中…（TeX / 引擎 / 字体，约 3–10 秒）", t, "small",
                                                t.palette.text_muted)
            self.envcheck_slot.update()
        except Exception:  # noqa: BLE001
            pass
        try:
            call = EB.envcheck_call()
            result = await asyncio.to_thread(EB.read_json_command, call)
        except RuntimeError as exc:
            result = {"ok": False, "error": str(exc), "json": {}}
        payload = result.get("json") or {}
        self.envcheck_data = payload if payload else None
        self.envcheck_error = "" if result.get("ok") else str(result.get("error") or "环境检查失败")
        checks = payload.get("checks") or []
        ok_count = sum(1 for c in checks if self._check_ok(c))
        failed = [c for c in checks if not self._check_ok(c)]
        self.app.mark("envcheck", ok=bool(result.get("ok")), code=result.get("code"),
                      checks=len(checks), ok_count=ok_count,
                      failed=[str(c.get("id")) for c in failed][:20],
                      profile=payload.get("profile"), platform=payload.get("platform"))
        if result.get("ok"):
            self.app.set_check_done(True)
        self._render_envcheck()

    @staticmethod
    def _check_ok(check: dict) -> bool:
        return SHELL.check_ok(check)

    def _render_envcheck(self) -> None:
        SHELL.render_envcheck(self)

    def _copy_diagnosis(self) -> None:
        SHELL.copy_diagnosis(self)

    def refresh_running(self) -> None:
        pass


PAGE_CLASSES = [HomePage, SinglePage, IssuePage, BibPage, SettingsPage, HelpPage]
