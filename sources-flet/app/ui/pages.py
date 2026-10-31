"""P2 页面：工作台 / 单篇稿件（3 步）/ 整期装配（6 步）/ 引用库（P3 实装）/ 设置。

页面只做三件事：取值（输入控件）→ 拼参数（services.engine_bridge）→ 显示日志（LogConsole）。
不复制任何引擎判定逻辑；长任务走 asyncio 子进程，不阻塞 UI 线程。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C
from ui.ia_map import NAV_GROUPS, stats as ia_stats

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


class BasePage:
    key = "base"

    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.fields: dict[str, list] = {}
        self.root = None
        self._run_stats: dict = {}

    # 子类实现 ─────────────────────────────────────────────────────────
    def build(self):
        raise NotImplementedError

    # 公共小工具 ───────────────────────────────────────────────────────
    @property
    def t(self):
        return self.app.t

    def add_picker(self, label: str, recent_key: str, hint: str, *, kind: str = "file",
                   required: bool = True, show_note: bool = True) -> ft.Container:
        ref: list = []
        value = self.app.store.get(f"recent.{recent_key}", "") or ""

        def _browse(e) -> None:
            self.app.browse_field(ref, f"选择{label}", kind, recent_key)

        def _changed(e) -> None:
            self.app.store.set_recent(recent_key, e.control.value.strip())
            self.mark("path_change", field=recent_key, value=e.control.value)

        control = C.path_picker(
            label, value, hint, self.t,
            on_change=_changed, on_browse=_browse, required=required,
            show_note=show_note, field_ref=ref,
        )
        self.fields[recent_key] = ref
        return control

    # ── 表单小工具 ─────────────────────────────────────────────────────
    def _meta_field(self, key: str, label: str, width: float) -> ft.TextField:
        def _changed(e) -> None:
            self.app.store.set(f"form.{key}", e.control.value.strip())

        control = ft.TextField(
            label=label, value=self.app.store.get(f"form.{key}", "") or "",
            dense=True, text_size=self.t.font("small"),
            border_color=self.t.palette.border, focused_border_color=self.t.palette.accent,
            width=self.t.px(width), on_change=_changed,
            content_padding=C._pad(self.t, 10, 8, 10, 8),
        )
        self.fields[key] = [control]
        return control

    def field(self, recent_key: str) -> str:
        ref = self.fields.get(recent_key)
        if not ref:
            return ""
        return (ref[0].value or "").strip()

    def set_field(self, recent_key: str, value: str) -> None:
        ref = self.fields.get(recent_key)
        if ref:
            ref[0].value = value
            try:
                ref[0].update()
            except Exception:  # noqa: BLE001
                pass

    def mark(self, event: str, **fields) -> None:
        self.app.mark(event, page=self.key, **fields)

    def refresh_running(self) -> None:
        pass

    def on_show(self) -> None:
        pass


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
            entry_card("LIBRARY_BOOKS_OUTLINED", "引用库", "P3 实装（本期保留入口）", "bib"),
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
            C.notice_bar("P2：单篇 3 步与整期 6 步已接真引擎；引用库与打包 CI 归 P3/P4。", t, "info"),
            ft.Row(spacing=t.sp("2"), controls=[
                C.card(ft.Column(spacing=t.sp("1"), controls=[
                    ft.Row(spacing=t.sp("2"), controls=[
                        C.text("引擎状态", t, "h3", weight="semibold"),
                        ft.Container(expand=True),
                        engine_chip,
                    ]),
                    C.text(engine_note, t, "small", t.palette.text_muted),
                    C.text(f"项目版本 {self.app.app_version} · Flet {self.app.flet_version}", t,
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
                    C.text("环境检查/诊断包在 P3 落地；现在先用引擎版本做探活。", t,
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
            "label": "文献回读校验",
            "icon": "CHECK_CIRCLE_OUTLINE",
            "desc": "交回的稿件里的引用与 .bib 条目逐条比对，可把结论并回正文",
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

    # ── 构建 ───────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="运行日志")
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
        self._run_btn = C.primary_button("运行", self._on_run, t, icon="PLAY_ARROW",
                                         disabled=not self.app.engine_status.ok)
        self._stop_btn = C.secondary_button("停止", self._on_stop, t, icon="STOP", disabled=True)
        self._chips_row = ft.Row(spacing=t.sp("1"), controls=[])
        self._render_chips()
        header = C.page_header(
            "单篇稿件", "一次只做一件事：选文件 → 勾选项 → 运行；日志与产物都在这一页。", t,
            actions=[self._stop_btn, self._run_btn],
        )
        self._flow_box.controls = [self._build_convert()]
        status = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            self._chips_row,
            ft.Container(expand=True),
            C.busy_bar(t, visible=False),
        ])
        self._busy = status.controls[2]
        body = C.page_body(header, status, self._flow_box, self._result.root, self._log.root,
                           t=t, gap=10)
        self.refresh_running()
        return body

    def _render_chips(self) -> None:
        t = self.t
        self._chips_row.controls = [
            C.chip(self.FLOWS[key]["label"], t, active=(key == self.flow_key),
                   on_click=lambda e, k=key: self._switch_flow(k))
            for key in self.FLOWS
        ]

    def _switch_flow(self, key: str) -> None:
        if self.app.running:
            self.app.toast("运行中不能切换步骤，先停止")
            return
        self.flow_key = key
        self._render_chips()
        builder = {
            "convert": self._build_convert,
            "checkbib": self._build_checkbib,
            "proofcheck": self._build_proofcheck,
        }[key]
        self._flow_box.controls = [builder()]
        self._result.reset()
        self._safe_update(self._chips_row, self._flow_box)

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
            advanced,
        ])

    def _build_checkbib(self) -> ft.Control:
        t = self.t
        options = self.app.store.get("options", {}) or {}
        return ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("输入", t, note="只填交回稿件即可；其余可选"),
            self.add_picker("交回稿件", "bib_returned", "作者交回 .docx / .tex", kind="file"),
            self.add_picker("原始稿件", "bib_original", "用于对照（可选）", kind="file", required=False),
            self.add_picker("正文 tex", "bib_tex", "可选：把结论写回正文时用", kind="file", required=False),
            self.add_picker("报告输出目录", "bib_out", "报告默认写在该目录（可选）", kind="dir",
                            required=False),
            C.option_row("把结论并回正文", "传入 --merge-into（须选正文 tex 与报告输出）", t,
                         icon="MERGE", checked=bool(options.get("checkbib_merge", False)),
                         on_change=lambda e: self.app.set_option("checkbib_merge", e.control.value)),
        ])

    def _build_proofcheck(self) -> ft.Control:
        t = self.t
        options = self.app.store.get("options", {}) or {}
        return ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("输入", t, note="初排后的 tex"),
            self.add_picker("初排 tex", "proof_tex", "初排产物 .tex", kind="file"),
            ft.Row(spacing=t.sp("2"), controls=[
                self._meta_field("proof_lang", "语种（zh / en，可空）", 240),
            ]),
            C.option_row("同时写修正稿", "传 --fix：另写 <stem>_一校核查后.tex", t,
                         icon="AUTO_FIX_HIGH", checked=bool(options.get("proofcheck_fix", False)),
                         on_change=lambda e: self.app.set_option("proofcheck_fix", e.control.value)),
        ])

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
        self.page.run_task(self._run, call)

    def _on_stop(self, e=None) -> None:
        self.app.stop_run()

    def _build_call(self) -> EB.EngineCall:
        if self.flow_key == "convert":
            source = self.field("single_source")
            out = self.field("single_out")
            if not source:
                raise ValueError("请先选择源稿件")
            if not out:
                raise ValueError("请先选择输出根目录")
            options = self.app.store.get("options", {}) or {}
            return EB.convert_call(
                source=source, out=out,
                compile_pdf=bool(options.get("convert_compile", True)),
                clean=bool(options.get("convert_clean", False)),
                source_pdf=bool(options.get("convert_source_pdf", False)),
                vol=self.field("convert_vol"), issue=self.field("convert_issue"),
                year=self.field("convert_year"), pages=self.field("convert_pages"),
                received=self.field("convert_received"), author=self.field("convert_author"),
                name=self.field("convert_name"), bib=self.field("convert_bib"),
            )
        if self.flow_key == "checkbib":
            returned = self.field("bib_returned")
            if not returned:
                raise ValueError("请先选择交回稿件")
            options = self.app.store.get("options", {}) or {}
            merge = bool(options.get("checkbib_merge", False))
            tex = self.field("bib_tex")
            out = self.field("bib_out")
            if merge and not tex:
                raise ValueError("并回正文需要选正文 tex")
            return EB.checkbib_call(
                returned=returned, original=self.field("bib_original"), tex=tex,
                merge_into=tex if merge and out else (tex if merge else ""),
                out=out,
            )
        tex = self.field("proof_tex")
        if not tex:
            raise ValueError("请先选择初排 tex")
        options = self.app.store.get("options", {}) or {}
        return EB.proofcheck_call(
            tex=tex, lang=self.field("proof_lang"),
            fix=bool(options.get("proofcheck_fix", False)),
        )

    async def _run(self, call: EB.EngineCall) -> None:
        page = self.page
        self.app.begin_run(call)
        self._result.reset()
        self._log.clear()
        code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                 notes=self.app.notes_repo)
        self.app.finish_run(call, code)
        level = "warn" if self.app.cancel_requested else EB.result_level(call.entry, code)
        text = "⏹ 已取消" if self.app.cancel_requested else EB.result_text(call.entry, code)
        artifact = EB.artifact_path(self.app.last_line)
        self._result.show(text, level=level, artifact=artifact)
        if artifact:
            self.app.store.set_recent(self._artifact_recent_key(), artifact)
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

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
        {"key": "infocheck", "title": "检查 tex 信息", "icon": "FACT_CHECK_OUTLINED",
         "desc": "扫描整期 tex，输出 9 列清单（篇目/作者/页码…）"},
        {"key": "toc", "title": "生成目录", "icon": "TOC", "desc": "用信息检查报告生成目录 tex（可落位编译）"},
        {"key": "compileall", "title": "编译全部", "icon": "PICTURE_AS_PDF_OUTLINED",
         "desc": "逐篇 xelatex→biber→xelatex×2（跳过 000 目录）"},
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
        self._run_btn = None
        self._stop_btn = None
        self._run_all_btn = None
        self._status: dict[str, str] = {step["key"]: "idle" for step in self.STEPS}

    def build(self) -> ft.Control:
        t = self.t
        self._log = C.LogConsole(t, height=150, title="整期日志")
        self._result = C.ResultBanner(t, on_open=self.app.open_path,
                                      on_copy=lambda path: self.app.copy_text(path))
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
        t = self.t
        self.fields = {k: v for k, v in self.fields.items() if k in ("issue_dir", "toc_report")}
        step = next(s for s in self.STEPS if s["key"] == key)
        title = C.text(step["title"], t, "h3", weight="semibold")
        rows: list = [C.text(step["desc"], t, "small", t.palette.text_muted)]
        if key == "infocheck":
            rows += [
                C.text("读取上方「期目录」，扫描后在本目录写 信息检查<时间戳>.txt / .json。", t,
                       "caption", t.palette.text_faint),
            ]
        elif key == "toc":
            options = self.app.store.get("options", {}) or {}
            rows += [
                self.add_picker("信息检查报告", "toc_report", "期目录下的 信息检查*.txt", kind="file"),
                C.option_row("仅生成 tex（不落位、不编译）", "只出目录 tex，交给下一阶段人工检查", t,
                             icon="DESCRIPTION_OUTLINED", checked=bool(options.get("toc_text_only", True)),
                             on_change=lambda e: self.app.set_option("toc_text_only", e.control.value)),
            ]
        elif key == "compileall":
            options = self.app.store.get("options", {}) or {}
            rows += [
                self._meta_field("compile_only", "只编这几篇（如 01,03；可空）", 260),
                C.option_row("只预演（--dry-run）", "列出将要编译的篇目，不真跑 xelatex", t,
                             icon="SCIENCE_OUTLINED", checked=bool(options.get("compileall_dry_run", False)),
                             on_change=lambda e: self.app.set_option("compileall_dry_run", e.control.value)),
            ]
        elif key == "merge":
            options = self.app.store.get("options", {}) or {}
            rows += [
                self._meta_field("merge_out", "输出文件（可空）", 420),
                C.option_row("嵌套书签", " --nest-outlines", t, icon="BOOKMARKS_OUTLINED",
                             checked=bool(options.get("merge_nest", False)),
                             on_change=lambda e: self.app.set_option("merge_nest", e.control.value)),
                C.option_row("加入编辑页", "--editors-page", t, icon="PEOPLE_OUTLINED",
                             checked=bool(options.get("merge_editors", False)),
                             on_change=lambda e: self.app.set_option("merge_editors", e.control.value)),
                C.option_row("加封底", "--back-cover", t, icon="LAST_PAGE_OUTLINED",
                             checked=bool(options.get("merge_back_cover", False)),
                             on_change=lambda e: self.app.set_option("merge_back_cover", e.control.value)),
            ]
        elif key == "cleantemp":
            options = self.app.store.get("options", {}) or {}
            rows += [
                C.notice_bar("危险动作：默认移入 .toolbox-trash；勾选「直接删除」会在确认后真删。", t, "warn"),
                C.option_row("移入回收站（--trash）", "默认安全模式", t, icon="RESTORE_FROM_TRASH_OUTLINED",
                             checked=bool(options.get("cleantemp_trash", True)),
                             on_change=lambda e: self.app.set_option("cleantemp_trash", e.control.value)),
                C.option_row("只预演（--dry-run）", "列出将删除的文件，不真删", t,
                             icon="SCIENCE_OUTLINED", checked=bool(options.get("cleantemp_dry_run", False)),
                             on_change=lambda e: self.app.set_option("cleantemp_dry_run", e.control.value)),
            ]
        elif key == "dirpush":
            options = self.app.store.get("options", {}) or {}
            rows += [
                self.add_picker("输出根", "dirpush_out", "目录摘要 docx 输出根（可空）", kind="dir",
                                required=False),
                C.option_row("含英文目录", "--with-en-toc", t, icon="TRANSLATE",
                             checked=bool(options.get("dirpush_with_en_toc", False)),
                             on_change=lambda e: self.app.set_option("dirpush_with_en_toc", e.control.value)),
                C.option_row("只预演（--dry-run）", "打印计划，不写文件", t, icon="SCIENCE_OUTLINED",
                             checked=bool(options.get("dirpush_dry_run", False)),
                             on_change=lambda e: self.app.set_option("dirpush_dry_run", e.control.value)),
            ]
        engine_note = C.text(f"引擎：{self.app.engine_status.describe()}", t, "caption",
                             t.palette.text_faint)
        engine_note.max_lines = 2
        engine_note.overflow = ft.TextOverflow.ELLIPSIS
        rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
            C.primary_button("运行这一步", lambda e, k=key: self._run_step(k), t, icon="PLAY_ARROW",
                             disabled=not self.app.engine_status.ok),
            ft.Container(expand=True, content=engine_note),
        ]))
        return ft.Column(spacing=t.sp("1"), controls=rows)

    # ── 构建调用 ───────────────────────────────────────────────────────
    def _issue_dir(self) -> str:
        return self.field("issue_dir")

    def _build_step_call(self, key: str) -> EB.EngineCall:
        issue = self._issue_dir()
        if not issue:
            raise ValueError("请先选择期目录")
        options = self.app.store.get("options", {}) or {}
        if key == "infocheck":
            return EB.infocheck_call(folder=issue)
        if key == "toc":
            report = self.field("toc_report")
            if not report:
                latest = self._latest_report(issue)
                if latest:
                    report = latest
                    self.set_field("toc_report", latest)
            if not report:
                raise ValueError("请先选择信息检查报告（可先运行「检查 tex 信息」）")
            return EB.toc_call(report=report, text_only=bool(options.get("toc_text_only", True)))
        if key == "compileall":
            return EB.compileall_call(folder=issue, only=self.field("compile_only"),
                                      dry_run=bool(options.get("compileall_dry_run", False)))
        if key == "merge":
            return EB.merge_call(
                folder=issue, out=self.field("merge_out"),
                nest_outlines=bool(options.get("merge_nest", False)),
                editors_page=bool(options.get("merge_editors", False)),
                back_cover=bool(options.get("merge_back_cover", False)),
            )
        if key == "cleantemp":
            dry_run = bool(options.get("cleantemp_dry_run", False))
            trash = bool(options.get("cleantemp_trash", True))
            return EB.cleantemp_call(folder=issue, dry_run=dry_run, trash=trash)
        if key == "dirpush":
            return EB.dirpush_call(
                issue=issue, out_root=self.field("dirpush_out"),
                with_en_toc=bool(options.get("dirpush_with_en_toc", False)),
                dry_run=bool(options.get("dirpush_dry_run", False)),
            )
        raise ValueError(f"未知步骤：{key}")

    @staticmethod
    def _latest_report(issue: str) -> str:
        try:
            reports = sorted(Path(issue).glob("信息检查*.txt"), key=lambda p: p.stat().st_mtime)
            return str(reports[-1]) if reports else ""
        except OSError:
            return ""

    # ── 运行 ───────────────────────────────────────────────────────────
    def _run_step(self, key: str) -> None:
        if self.app.running:
            return
        try:
            call = self._build_step_call(key)
        except (ValueError, RuntimeError) as exc:
            self.app.toast(str(exc))
            self.mark("validation_error", step=key, reason=str(exc))
            return
        if (key == "cleantemp"
                and not (self.app.store.get("options", {}) or {}).get("cleantemp_dry_run", False)
                and not getattr(self.app, "auto_confirm", False)):
            def _confirmed() -> None:
                self._status[key] = "running"
                self._refresh_steps()
                self.mark("confirm_clean", step=key)
                self.page.run_task(self._run_calls, [(key, call)])

            C.confirm(self.page, self.t, title="确认清理临时文件？",
                      message="默认移入 .toolbox-trash（可恢复）；若勾选过「直接删除 / 恢复」，请确认这是你要的结果。",
                      confirm_label="开始清理", on_confirm=_confirmed)
            return
        self._status[key] = "running"
        self._refresh_steps()
        self.page.run_task(self._run_calls, [(key, call)])

    def _run_all(self) -> None:
        if self.app.running:
            return
        if not self._issue_dir():
            self.app.toast("请先选择期目录")
            self.mark("validation_error", step="all", reason="missing issue dir")
            return
        try:
            calls = []
            for step in self.STEPS:
                key = step["key"]
                if key == "cleantemp":
                    # 全流程里的清理默认预演，避免一键误删
                    calls.append((key, EB.cleantemp_call(folder=self._issue_dir(), dry_run=True,
                                                         trash=True)))
                    continue
                calls.append((key, self._build_step_call(key)))
        except (ValueError, RuntimeError) as exc:
            self.app.toast(str(exc))
            self.mark("validation_error", step="all", reason=str(exc))
            return
        self.mark("run_all_submit", steps=[k for k, _ in calls])
        self.page.run_task(self._run_calls, calls, True)

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
            code = await EB.run_call(call, on_line=self._on_line, on_proc=self.app.track_proc,
                                     notes=self.app.notes_repo)
            self.app.finish_run(call, code)
            cancelled = self.app.cancel_requested
            self._status[key] = "error" if (code != 0 and not cancelled) else ("warn" if code == 1 else "done")
            if cancelled:
                self._status[key] = "idle"
            self._refresh_steps()
            artifact = EB.artifact_path(self.app.last_line)
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
    key = "bib"

    def build(self) -> ft.Control:
        t = self.t
        header = C.page_header("引用库", "查库、入藏、待裁决都将在 P3 接入引擎（bibdb.py）；本页先保留 IA 入口。", t)
        body = C.page_body(
            header,
            ft.Row(spacing=t.sp("2"), controls=[
                C.stat_card("条目总数", "—", "等待 P3 读取 .bib", t),
                C.stat_card("待裁决", "0", "队列为空", t),
                C.stat_card("最近入藏", "—", "还没有操作记录", t),
            ]),
            C.card(C.empty_state("LIBRARY_BOOKS_OUTLINED", "引用库在 P3 实装",
                                 "本骨架只验证 IA 与组件；引擎入口 bibdb.py 已就绪，P3 再接。", t,
                                 height=260), t),
            t=t, gap=12,
        )
        return body


# ── 设置 ───────────────────────────────────────────────────────────────────
class SettingsPage(BasePage):
    key = "settings"

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
            C.section_title("引擎", t, note="P2 只做探活；诊断包/环境检查归 P3"),
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

        paths = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("路径", t, note="引擎目录留空 = 自动定位"),
            self._path_field("engine_override", "引擎目录（可空）", kind="dir"),
            self._path_field("notes_repo", "私有笔记仓（可空）", kind="dir"),
        ]), t)

        logs = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("日志", t, note="默认只看摘要，原始行可展开"),
            C.option_row("只看摘要", "隐藏非摘要行（引擎 SUMMARY_MARKERS 判据）", t,
                         icon="FILTER_ALT_OUTLINED",
                         checked=bool(options.get("log_summary_only", True)),
                         on_change=lambda e: self.app.set_option("log_summary_only", e.control.value)),
            C.option_row("首次环境检查已完成", "兼容 Tk 版 envCheckDone", t, icon="CHECK_CIRCLE_OUTLINE",
                         checked=bool(self.app.store.get("env_check_done", False)),
                         on_change=lambda e: self.app.set_check_done(e.control.value)),
        ]), t)

        about = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("关于", t),
            self._kv("应用版本", self.app.app_version),
            self._kv("Flet / Python", f"{self.app.flet_version} · {self.app.python_version}"),
            self._kv("窗口 / DPI", f"{self.app.window_w}×{self.app.window_h} @ {self.app.dpi} ({self.app.dpi / 96:.0%})"),
            self._kv("IA", f"{ia_stats()['legacy_pages']} 个旧页 → {ia_stats()['new_pages']} 页 + 子流程"),
        ]), t)

        header = C.page_header(
            "设置", "外观、引擎、路径与日志；所有设置持久化，不依赖系统注册表。", t,
            actions=[C.primary_button("立即保存", lambda e: self.app.save_settings(toast=True), t,
                                      icon="SAVE_OUTLINED")],
        )
        page = C.page_body(header, engine_card, appearance, paths, logs, about, t=t, gap=12)
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

    def refresh_running(self) -> None:
        pass


PAGE_CLASSES = [HomePage, SinglePage, IssuePage, BibPage, SettingsPage]
