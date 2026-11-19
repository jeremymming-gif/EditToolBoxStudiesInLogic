"""引用库 · AI 辅助处理 Bib（VIEW_KEY="normalize"）。

生产页面嵌入共享 AI 工作区：选择目标 Bib → 获取结果 → 审阅 → 并入目标。
当前阶段动作固定在单一滚动内容之外；写入和撤销均由引擎执行，不改中央库。

旧 checkbib 报告解析及只读检查方法保留供兼容入口调用，不再渲染旧表单。
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
from ui import ia_map as IA
from ui.tokens import Tokens

VIEW_KEY = "normalize"
TITLE = "AI 辅助处理 Bib"
ROLE = "readonly"

REPORT_SUFFIX = "_回读校验报告.md"
CONCLUDE = re.compile(r"结论：\s*(\d+)\s*处问题\s*/\s*(\d+)\s*处提示")
ENTRIES = re.compile(r"解析出\s*(\d+)\s*条条目")
APPENDIX_ROW = re.compile(r"^\|\s*`[^`]+`\s*\|\s*@")
BULLET = re.compile(r"^-\s+(.+)$")

#: 引擎报告的**抬头三行**（`bibreport.render()` 原文）。用真实前缀匹配，
#: 不要只写半截 `原件：`——引擎写的是 `原件（--original）：`，半截前缀匹配不上，
#: 抬头就被当成「提示」塞进表里（DESIGN §11.2 A 的「路径噪声」）。
#: 旧写法（`原件：` / `正文：`）一并保留：手工改过的报告与旧测试夹具里还有它。
HEADER_PREFIXES = (
    "退回文件：",
    "原件（--original）：", "原件：",
    "正文（--tex）：", "正文：",
    "说明：", "diff 静音：", "diff 匹配：",
)


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
    # 引擎报告 = `report_header` 的「摘要 / 最需处理 / 全部明细」+ 正文。
    # 抬头那一段**把同样的发现又列了一遍**（还夹着三条路径），若照单全收，
    # 界面上每条问题都会出现两次、路径还会冒充「提示」。所以只从正文标题
    # （第一个 `# ` 行）开始收行——夹具与手写报告都以标题开头，行为不变。
    started = False
    in_problem = False
    for line in lines:
        if not started:
            if line.startswith("# "):
                started = True
            else:
                continue
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
        # 真实报告的分节里，`## 二、编辑部规范` 之后才是 `**问题（N）**` / `**提示（N）**`；
        # 分节标题本身不含「问题」二字，只看标题会把**问题**全判成「提示」（计数对、标色错）。
        if line.startswith("**问题（"):
            in_problem = True
            continue
        if line.startswith("**提示（"):
            in_problem = False
            continue
        bullet = BULLET.match(line)
        if bullet and "`" in bullet.group(1):
            text = bullet.group(1)
            if "解析出" in text or text.startswith(HEADER_PREFIXES):
                continue  # 抬头行不是问题行
            kind = ("error" if (in_problem or "问题" in data.get("_section", ""))
                    else "warn")
            data["rows"].append({"kind": kind, "text": text})
        if line.startswith("## "):
            data["_section"] = line[3:].strip()
            in_problem = False  # 新分节：`**问题（` / `**提示（` 会重新标记
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
        # N02/N04：路径输入框引用（真实选择器回填用）与「正文粘贴」的独立控件。
        self._refs: dict[str, list] = {"returned": [], "original": [], "tex": []}
        self._picking = False
        # DESIGN §4（权威裁定）：本页**就是**「AI 辅助处理 Bib」，BibAiAssistView
        # 直接成为页面内容，不再是旧报告下面默认收起的附属区。下面的 _ai_* 字段
        # 只保留为兼容（旧调用/状态读取），不再驱动任何折叠 UI；视图本体由
        # _ensure_ai_view() 一次性构造，构造失败如实报错，绝不吞异常假装成功。
        self._ai_open = False
        self._ai_view = None
        self._ai_box: ft.Container | None = None
        self._ai_role_chip = "write"  # 写语义；构造视图后与 BAA.ROLE_CHIP 对齐

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

    def mark(self, event: str, **kwargs) -> None:
        """兼容面：对外可调用的埋点入口（委托 ``_mark``，宿主可缺省）。"""
        self._mark(event, **kwargs)

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
        activate_ai = getattr(self._ai_view, "activate", None)
        if callable(activate_ai):
            activate_ai()
        if not self.loaded and not self.loading:
            self._schedule(self._load_last)

    def refresh_running(self) -> None:
        return None

    def _ensure_ai_view(self):
        """设计 §4：一次性构造「AI 辅助处理 Bib」视图，构造失败如实报错。

        宿主 ``self.page`` 是工具箱宿主 BibHost（t/app/page/mark/_render/
        set_view/notify/run_task_ui），是 BibAiAssistView 期望的宿主，**不是**
        裸 Flet Page，不要改成 self.page.page。字段 ``purpose="library"`` 表示
        这是引用库 Bib 入口，不是初排稿。
        """
        if self._ai_view is None:
            # 局部导入：与下方旧兼容方法里的 import 同名别名，保持单一入口。
            from ui import bib_ai_assist as BAA  # noqa: PLC0415
            self._ai_view = BAA.BibAiAssistView(self.page, purpose="library")
            # 写语义页头 chip：与 BAA 的 ROLE_CHIP 对齐，不依赖别人的字面量。
            self._ai_role_chip = BAA.ROLE_CHIP
        return self._ai_view

    def _render(self) -> None:
        """BibHost / 本页状态变化后的幂等重绘入口。

        真实宿主 BibHost._render() 调的是本类 build()，不会调这个名字；但
        ``bib_host._NormalizeCompat`` 等兼容面仍会按需调用它。这里做到：
        已构造过页面壳就只换 body（不重建整壳、不重复造滚动），没构造过就
        通过宿主 _refresh() 让 BibHost 走标准 build() 路径。
        """
        shell = getattr(self, "_shell", None)
        if shell is not None:
            try:
                body_box = getattr(shell, "_bib_body", None)
                if body_box is not None:
                    body_box.content = self._body()
                dock_box = getattr(shell, "_bib_dock", None)
                if dock_box is not None:
                    dock_box.content = self._dock()
            finally:
                self._flet_update()
            return
        self._refresh()

    def _flet_update(self) -> None:
        updater = getattr(getattr(self.page, "page", None), "update", None)
        if callable(updater):
            try:
                updater()
            except Exception:  # noqa: BLE001 - 构建期尚未挂载
                pass

    def header_actions(self) -> list:
        # 设计 §4：报告工具条整个移除，页头不再挂「打开报告」。
        return []

    def primary_action(self):
        # 设计 §4/§6：唯一主按钮跟随当前阶段，由内嵌 BibAiAssistView 提供，
        # 本兼容外壳不再返回「生成报告」。
        return None

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
        """N02：走真实的文件选择服务；选完回读并触发与手工输入相同的处理。

        旧实现取 `app.pick_file`——App 上根本没有这个方法，于是永远落到
        「文件选择器不可用」；文案还只在宿主恰好有 `show_dialog` 时才出现，
        等于两个分支都是死的。`browse_field` 只写 `ref[0].value`、不触发
        `on_change`，所以这里自己轮询回读。
        """
        def _handler(e=None) -> None:
            if self._picking:
                return
            app = getattr(self.page, "app", None)
            browse = getattr(app, "browse_field", None)
            ref = self._refs.get(field)
            if not callable(browse) or not ref:
                self.input_error = "文件选择器不可用；请粘贴路径。"
                self._refresh()
                return
            before = (ref[0].value or "").strip() if ref else ""
            titles = {"returned": "选择交回的 .bib", "original": "选择原件 .bib",
                      "tex": "选择正文 .tex"}
            try:
                browse(ref, titles.get(field, "选择文件"), "file",
                       f"bib_normalize_{field}")
            except Exception as exc:  # noqa: BLE001 - 选择器实现因平台而异
                self.input_error = f"文件选择器不可用（{type(exc).__name__}）；请粘贴路径。"
                self._refresh()
                return
            self._picking = True
            self._schedule(lambda: self._watch_pick(field, ref, before))
        return _handler

    async def _watch_pick(self, field: str, ref: list, before: str) -> None:
        """等真实选择结果写回输入框；取消时保持原值、不报错。"""
        try:
            for _ in range(240):
                await asyncio.sleep(0.25)
                after = (ref[0].value or "").strip() if ref else ""
                if after == before:
                    continue
                if after:
                    setattr(self, field, after)
                    if field == "returned":
                        self.report_path = default_report_path(after)
                    if field == "original" and not self.compare_touched:
                        self.compare = True
                    if field == "tex" and not self.keys_touched:
                        self.close_keys = True
                    self.input_error = ""
                    self._mark("bib_normalize_browse", field=field, picked=True)
                    self._refresh()
                else:
                    self._mark("bib_normalize_browse", field=field, picked=False)
                return
        finally:
            self._picking = False

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
                          if lines else "生成失败。")
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
                            f"{counts['warnings']} 警告。")
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
                          on_browse=self._browse("returned"), required=True, show_note=False,
                          field_ref=self._refs["returned"]),
            C.path_picker("原件（.bib）", self.original, "编辑部手上的原 .bib",
                          t, on_change=self._on_path("original"),
                          on_browse=self._browse("original"), required=False, show_note=False,
                          field_ref=self._refs["original"]),
            C.path_picker("正文（.tex）", self.tex, "用于键闭合核对",
                          t, on_change=self._on_path("tex"),
                          on_browse=self._browse("tex"), required=False, show_note=False,
                          field_ref=self._refs["tex"]),
        ])

    def _ai_section(self) -> ft.Control:
        """AI 处理 Bib 的折叠入口（C，DESIGN §3.2 / §4）。

        为什么放在这里而不是新增一个引用库视图：`BibPage.VIEWS` / 导航 chip 数 /
        命令数被 `test_b116_bib_host`、`test_b116_bib_inpage_nav`、`test_b114_polish`
        多处钉死；把它做成「AI 辅助处理 Bib」页里的折叠区，既不新增路由，也不改这些计数。
        控件约束（同页测试）：本区不得新增 `ft.FilledButton`、不得引入 `ft.Card`、
        不得再出现一次页头大字标题——所以入口是扁平的 `TextButton`，
        且内部视图**首次展开才创建**（`body_only()` 不带页头）。
        """
        t = self.t
        label = "收起 AI 处理 Bib" if self._ai_open else "AI 处理 Bib（可选）"
        toggle = C.text_button(label, self._toggle_ai, t, height=28)
        toggle.tooltip = ("按类别执行 AI、按类别采纳；写目标 Bib 由引擎完成，"
                          "先备份可撤销，不改中央库")
        toggle._bib_ai_toggle = True
        head = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                toggle,
                # 文案里**不出现页面标题**：本页测试钉死 `texts.count("AI 辅助处理 Bib") == 1`。
                C.text("与上面的体检互不影响：这里只调引擎、只写你选定的目标 Bib。",
                       t, "caption", t.palette.text_faint, expand=True,
                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ])
        content = None
        if self._ai_open and self._ai_view is not None:
            try:
                content = self._ai_view.body_only()
            except Exception as exc:  # noqa: BLE001 - 内嵌区坏了不能连带坏本页
                # 视图构造成功、渲染失败也要如实说：否则页面会静默少一整块。
                self.input_error = (f"AI 处理 Bib 绘制失败（{type(exc).__name__}: {exc}）"
                                    "；上面的 AI 辅助处理 Bib 仍可用。")
                content = C.inline_note(self.input_error, t, "error")
        self._ai_box = ft.Container(content=content, visible=self._ai_open,
                                    padding=ft.Padding(left=0, top=0, right=0, bottom=0))
        return ft.Column(spacing=t.sp("1"), controls=[head, self._ai_box])

    def _toggle_ai(self, e=None) -> None:
        """展开/收起 AI 区；首次展开才导入并实例化视图（失败如实报错）。"""
        if self._ai_open:
            self._ai_open = False
            self._mark("bib_ai_toggle", open=False)
            self._refresh()
            return
        if self._ai_view is None:
            try:
                from ui import bib_ai_assist as BAA  # 局部导入：收起时不拉进来
                # 本页是「引用库 Bib」入口（库内条目核实/规范化），不是初排稿：
                # 必须显式传 purpose，否则视图默认按 initial 显示「初排稿 .tex」，
                # 会让人以为这条链路在改初排源稿（验收 §3.1-11③）。
                self._ai_view = BAA.BibAiAssistView(self.page, purpose="library")
            except Exception as exc:  # noqa: BLE001 - 视图坏了不能连带坏本页
                self.input_error = (f"AI 处理 Bib 打不开（{type(exc).__name__}: {exc}）"
                                    "；上面的 AI 辅助处理 Bib 仍可用。")
                self._refresh()
                return
        self._ai_open = True
        self._mark("bib_ai_toggle", open=True)
        self._refresh()

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
        btn.tooltip = "只读：列出交回稿的键，不写库"
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
                t, "还没有报告；先选 .bib 再生成。",
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
                                        message="已取消，报告未写入。",
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
        """共享视图负责单一滚动及固定动作，本层只提供有界页面空间。

        目标 Bib 与可选关联正文由 purpose=library 的共享工作区管理。
        不叠加旧回读表单，也不再用外层滚动把主动作一起滚走。
        """
        self._build_width = self._design_width()
        view = self._ensure_ai_view()
        return view.body_only(compact=True, pinned_actions=True)

    def _dock(self) -> ft.Control | None:
        # 当前动作已固定在工作区底部；页头保留写入边界，不再重复一条空底栏。
        return None

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        self._ensure_ai_view()
        self._shell = S.page_shell(
            self.t, title=IA.view_label("bib", "normalize") or "AI 辅助处理 Bib",
            meta="仅更新选定 Bib，不改中央库",
            role_chip=self._ai_role_chip, action=None,
            on_gear=lambda e: self._goto("settings"),
            body=self._body(), dock=self._dock(), dock_height=S.DOCK_H)
        return self._shell

    def _goto(self, view: str) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)
