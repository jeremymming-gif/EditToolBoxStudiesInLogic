"""编辑部工具箱 · Windows 外壳（Tkinter）。

**这是个「壳」**：所有稿件处理逻辑都在 `Engine/`（Python 引擎，唯一一份）。
本文件只做三件事 —— 取值、拼参数（`engine.py`）、把引擎的实时日志显示出来。

界面骨架取自 chenphilo 的 `Editorialbox`（MIT，`Copyright (c) 2024 chenphilo`）：
标签页 + 路径输入框 + 浏览/运行按钮 + 底部日志框那套交互原样保留，
**里面的功能实现全部换成调用我们的引擎 CLI**（见 `README.md` 的「改了什么」）。

跑起来（开发机）：
    cd sources-win && python main.py
打包：见 `README.md`（PyInstaller；引擎另冻成 `SLCNEngine.exe`）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bibdb_ui as B  # noqa: E402  第 132 条：引用库 5 页（拆文件，见 bibdb_ui.py 抬头）
import coop as C  # noqa: E402  第 55/56 批：两段式 AI 协同初排的纯逻辑（无 tkinter）
import engine as E  # noqa: E402
import settings as S  # noqa: E402
import theme  # noqa: E402

def plain(text: str) -> str:
    """把界面文案里的 markdown 记号去掉（Tk 不解析 markdown，会**原样显示**）。

    ⚠️ 2026-10-17 **截了窗口截图自查**才发现：说明文字里全是 `**` 与反引号，
    编辑看到的是「选**一期的目录**」「`` `信息检查<时间戳>.txt` ``」这种带记号的原样文本。
    读代码看不出来（字符串就是那样的），只有真看一眼界面才知道。

    只清理**界面文案**；下方运行日志保持引擎原文（那是证据，mac 侧同样原样显示）。
    """
    return text.replace("**", "").replace("`", "")


APP_TITLE = "编辑部工具箱（Windows）"


def open_path(path: str) -> None:
    """在访达 / 资源管理器里打开一个路径（文件则定位到它所在的目录）。

    完成横幅的「打开产物」、协同面板的「打开任务书/暂存目录/交付目录…」共用这一处，
    免得两个地方对「文件还是目录」的判法慢慢分叉。
    """
    if not path:
        return
    try:
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if os.name == "nt":
            os.startfile(target)                      # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open", target])
    except Exception as e:                            # noqa: BLE001
        messagebox.showerror(APP_TITLE, f"打不开：\n{path}\n\n{e}")

#: 内存里保留的日志行上限（整期编译一次能吐几千行；超了从最旧的丢起，
#: 与 mac 侧 `LogStore.limit`（40 万字符）同一个精神 —— 宁可丢最旧，也别无限涨）。
LOG_LINE_LIMIT = 4000
#: 截断时一次多丢一些：否则越到后面每来一行都要整屏重画一遍。
LOG_TRIM_CHUNK = 500
#: 空状态（观察③）：日志区还没有任何一行时显示的那句灰字。
LOG_PLACEHOLDER = "运行任务后，日志会显示在这里…"


class TabPage(ttk.Frame):
    """一页 = 一个路径输入 + 浏览 + 若干选项 + 运行。子类只填 `instruction` 与选项。"""

    instruction = ""
    pick = "dir"          # 'dir' | 'file'

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent)
        self.name = name
        self.on_run = on_run
        self.on_stop = on_stop
        self.columnconfigure(0, weight=1)
        row = 0

        ttk.Label(self, text="输入路径，或点「浏览」选择：").grid(
            row=row, column=0, padx=10, pady=(10, 2), sticky="w"); row += 1

        self.entry = ttk.Entry(self, exportselection=False)
        self.entry.grid(row=row, column=0, padx=10, sticky="ew"); row += 1

        btns = ttk.Frame(self)
        btns.grid(row=row, column=0, padx=10, pady=6, sticky="w"); row += 1
        ttk.Button(btns, text="浏览", width=10, command=self.browse).pack(side="left")
        # 「运行」是本页的主动作：accent 样式对应 mac 的 `.borderedProminent`
        self.run_button = ttk.Button(btns, text="运行", width=10, style="Accent.TButton",
                                     command=lambda: self.on_run(self))
        self.run_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(btns, text="停止", width=10, state="disabled",
                                      command=self.on_stop)
        self.stop_button.pack(side="left")

        # ── 完成契约横幅（2026-10-17 UI 方案第 1 件）──
        # 为什么要有：编辑最常问的两句是「跑完了吗」「产物在哪」。引擎的契约行里本来就有
        # 产物路径，界面把它做成**常驻 + 可点开 + 可复制**，比让人翻日志强得多。
        self.result_bar = ttk.Frame(self)
        self.result_bar.grid(row=row, column=0, padx=10, pady=(0, 6), sticky="w"); row += 1
        # 不确定进度条（不知道总量就不编百分比，只表示"在动"）
        self.progress = ttk.Progressbar(self.result_bar, mode="indeterminate", length=110)
        self.result_label = ttk.Label(self.result_bar, text="", wraplength=460, justify="left")
        self.result_label.pack(side="left")
        self.open_btn = ttk.Button(self.result_bar, text="打开产物", width=10,
                                   command=self._open_result, state="disabled")
        self.open_btn.pack(side="left", padx=(10, 4))
        self.copy_btn = ttk.Button(self.result_bar, text="复制路径", width=10,
                                   command=self._copy_result, state="disabled")
        self.copy_btn.pack(side="left")
        # 「撤销」按需出现（删除临时文件 → 移入回收目录后可一键还原；UI 方案第 3 件）
        self.undo_btn = ttk.Button(self.result_bar, text="", width=12, command=self._undo_result)
        self._undo_cmd = None
        self.result_bar.grid_remove()          # 跑完/运行中才出现
        self._result_path = ""

        self.options = ttk.Frame(self)
        self.options.grid(row=row, column=0, padx=10, sticky="w"); row += 1
        self.build_options(self.options)

        if self.instruction:
            ttk.Label(self, text=plain(self.instruction), wraplength=760,
                      justify="left").grid(row=row, column=0, padx=10, pady=(2, 10), sticky="w")

    # 子类可覆盖
    def build_options(self, parent: ttk.Frame) -> None:
        pass

    def extra_args(self) -> dict:
        return {}

    # ── 完成横幅：运行中显示秒表，跑完显示状态 + 产物路径 ───────────────────
    def show_running(self, seconds: float, phase: str = "") -> None:
        """运行中：常驻一条"正在运行 + 已用秒表 + 当前阶段"＋不确定进度条。

        UI 方案第 2 件：>10 秒的动作必须有"在动"的证据。引擎的阶段行（①②…）本来就在
        日志里流式输出，这里把**最新一行**当阶段显示 —— 不需要知道总量，也**不编百分比**。
        """
        self._result_path = ""
        text = f"⏳ 正在运行…（已用 {seconds:.1f} 秒）"
        if phase:
            text += f"｜{phase[:70]}"
        self.result_label.config(text=text)
        if not self.progress.winfo_ismapped():
            self.progress.pack(side="left", padx=(8, 0))
            self.progress.start(18)
        self.open_btn.config(state="disabled")
        self.copy_btn.config(state="disabled")
        self.result_bar.grid()

    def show_result(self, text: str, path: str, seconds: float,
                    undo: tuple[str, object] | None = None) -> None:
        """跑完：状态 + （≥1 秒才显示）用时 + 产物路径，路径可点开/可复制。

        `undo=(按钮文字, 回调)` 时多出一个可撤销按钮（目前只有"移入回收目录"用）。
        """
        self._result_path = path or ""
        shown = text + (f"｜用时 {seconds:.1f} 秒" if seconds >= 1 else "")
        if path:
            shown += f"｜产物：{path}"
        self.progress.stop()
        self.progress.pack_forget()
        self.result_label.config(text=shown)
        self.open_btn.config(state="normal" if path else "disabled")
        self.copy_btn.config(state="normal" if path else "disabled")
        if undo:
            label, cb = undo
            self._undo_cmd = cb
            self.undo_btn.config(text=label)
            self.undo_btn.pack(side="left", padx=(4, 0))
        else:
            self._undo_cmd = None
            self.undo_btn.pack_forget()
        self.result_bar.grid()

    def _undo_result(self) -> None:
        cb = self._undo_cmd
        if cb is not None:
            cb()

    def _open_result(self) -> None:
        """在访达 / 资源管理器里打开产物（文件就定位到它所在的目录）。"""
        open_path(self._result_path)

    def _copy_result(self) -> None:
        if not self._result_path:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._result_path)
            self.log(f"（已复制产物路径：{self._result_path}）")
        except tk.TclError:
            pass

    def browse(self) -> None:
        path = fd.askdirectory() if self.pick == "dir" else fd.askopenfilename()
        if path:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, path)

    def path(self) -> str:
        return self.entry.get().strip().strip('"')


def _check(parent: tk.Widget, text: str, var: tk.BooleanVar,
           command=None) -> ttk.Checkbutton:
    """勾选框统一走 ttk（clam 才管得到它的底色/字体；经典控件的锚点是 ttk 不支持的）。"""
    return ttk.Checkbutton(parent, text=text, variable=var, command=command)


class ConvertTab(TabPage):
    name = "初排转换"
    pick = "file"

    def __init__(self, parent, name, on_run, on_stop, cfg=None, ai_knobs_sink=None):
        # cfg：AI 的两个旋钮（思考模式/最长输出）现在放在**这一页**（UI 方案 §4：
        # 任务相关选项就地改，别让人去设置页找上下文）。值仍以 cfg 为准（运行时就地读）。
        self.cfg = cfg or {}
        self.ai_knobs_sink = ai_knobs_sink or (lambda *_a: None)
        # ── 第五十五批：两段式 AI 协同初排的界面状态 ──
        # `_coop_out` = 第一段选定的输出根目录（第二段**复用**，不再弹目录选择）；
        # `_coop_stage` = 从暂存目录发现的状态（第二段/各「打开…」按钮都靠它）。
        self._coop_out = ""
        self._coop_stage: C.CoopStage | None = None
        # 批56：已选回填清单（文件/目录/剪贴板临时文件可混合、累加；纯逻辑在 coop.py）
        self._coop_sel = C.ReplySelection()
        self._coop_clip_dir = ""          # 剪贴板回填临时目录（首次粘贴时创建）
        super().__init__(parent, name, on_run, on_stop)
    instruction = ("使用说明：选作者的稿件（.docx / .doc / .tex），再选一个输出根目录，"
                   "产物会写到「输出目录/<作者名>/」。\n"
                   "勾了「出文献核实任务书」会同时生成《…_文献核实.md》；"
                   "联网核实需要先在「设置」里填好 API 配置。\n"
                   "⚠️ 正文里读不出第一作者姓名时（部分 LaTeX 投稿就是这样），**必须**填"
                   "「输出名」或「作者」，否则引擎会拒绝转换并提示要这两个参数。\n"
                   "第二行是编辑侧出版信息（卷/期/年/页码…）：不填也能转，但 PDF 页眉会是"
                   "「xxxx 年第 x 期，1–10」占位，得手工改 .tex。\n"
                   "核实成本闸门（上限 / 先算账 / 全量 / 先查库）在「引用库 → 引用库设置」页；"
                   "默认值 = 一个参数都不加（与接入引用库之前逐字节相同）。\n"
                   "勾「AI 协同初排（两段式）」= 第一段只把初排产物 + 《文献核实任务书》"
                   "写进暂存目录就暂停（软件不调用任何 API、不上传任何内容）；"
                   "编辑拿任务书去外部 chat 核实后，把回复导入（可多文件/目录/剪贴板）"
                   "再运行第二段才交付。回填含 BibTeX 与附一 JSON 两部分，别只交一部分。")

    def build_options(self, parent: ttk.Frame) -> None:
        row1 = ttk.Frame(parent)
        row1.pack(anchor="w")
        row2 = ttk.Frame(parent)
        row2.pack(anchor="w", pady=(4, 0))

        self.v_compile = tk.BooleanVar(value=True)
        self.v_clean = tk.BooleanVar(value=False)
        self.v_refs = tk.BooleanVar(value=True)
        self.v_srcpdf = tk.BooleanVar(value=False)
        _check(row1, "编译 PDF", self.v_compile).pack(side="left")
        _check(row1, "清理临时文件", self.v_clean).pack(side="left")
        _check(row1, "出文献核实任务书", self.v_refs).pack(side="left")
        _check(row1, "导出原稿 PDF", self.v_srcpdf).pack(side="left")

        # 「输出名 / 作者」决定产物目录名；正文读不出姓名时它们是**唯一活路**（H1）
        self.v_name = tk.StringVar(value="")
        self.v_author = tk.StringVar(value="")
        ttk.Label(row1, text="输出名").pack(side="left", padx=(14, 2))
        ttk.Entry(row1, textvariable=self.v_name, width=14).pack(side="left")
        ttk.Label(row1, text="作者").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.v_author, width=10).pack(side="left")

        # 编辑侧出版信息（与 mac 同页同一组；缺了只是页眉占位，不报错）
        self.v_vol = tk.StringVar(value="")
        self.v_issue = tk.StringVar(value="")
        self.v_year = tk.StringVar(value="")
        self.v_pages = tk.StringVar(value="")
        self.v_received = tk.StringVar(value="")
        self.v_editor = tk.StringVar(value="")
        self.v_grants = tk.StringVar(value="")
        for label, var, width in (("卷", self.v_vol, 5), ("期", self.v_issue, 5),
                                  ("年", self.v_year, 6), ("页码", self.v_pages, 10),
                                  ("收稿日期", self.v_received, 11),
                                  ("责任编辑", self.v_editor, 8), ("基金", self.v_grants, 20)):
            ttk.Label(row2, text=label).pack(side="left", padx=(8, 2))
            ttk.Entry(row2, textvariable=var, width=width).pack(side="left")

        # AI 的两个旋钮（2026-10-17 从「设置」页挪到这里 —— 方案 §4：任务选项就地改，
        # 别让人去设置页找上下文）。留空 = 不发送该字段（引擎默认行为）。
        ai_cfg = self.cfg.get("api") or {}
        row3 = ttk.Frame(parent)
        row3.pack(anchor="w", pady=(4, 0))
        ttk.Label(row3, text="AI 核实用：思考模式").pack(side="left")
        self.v_thinking = tk.StringVar(value=str(ai_cfg.get("thinking", "")))
        ttk.Entry(row3, textvariable=self.v_thinking, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(row3, text="最长输出").pack(side="left")
        self.v_maxtok = tk.StringVar(value=str(ai_cfg.get("maxTokens", "")))
        ttk.Entry(row3, textvariable=self.v_maxtok, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(row3, text="留空 = 用端点默认值", style="Muted.TLabel").pack(side="left")
        for _v in (self.v_thinking, self.v_maxtok):
            _v.trace_add("write", lambda *_: self.ai_knobs_sink(
                self.v_thinking.get().strip(), self.v_maxtok.get().strip()))

        # 第 132 条：把「当前核实范围」就地显示（方案 §4：任务相关选项就地改，别去设置页找）；
        # 值仍以「引用库设置」页为准，这里只是**每进本页刷新一次**的只读回显。
        row4 = ttk.Frame(parent)
        row4.pack(anchor="w", pady=(4, 0))
        self.bibdb_hint = ttk.Label(row4, text="", style="Muted.TLabel",
                                    wraplength=780, justify="left")
        self.bibdb_hint.pack(anchor="w")
        self.refresh_bibdb_hint()

        # ── 第五十五批：两段式 AI 协同初排（默认关；勾上才展开操作区）──────────
        # 用户口径（2026-10-19）：第一段只写暂存目录 + 任务书并暂停；编辑拿任务书去
        # 外部 chat（豆包/千问/DeepSeek…）核实 —— **软件不上传任何内容、零费用**；
        # 回填后导入回复跑第二段，第二段成功才交付。
        row5 = ttk.Frame(parent)
        row5.pack(anchor="w", pady=(8, 0))
        self.v_coop = tk.BooleanVar(value=False)
        _check(row5, "AI 协同初排（两段式）", self.v_coop,
               command=self._toggle_coop_panel).pack(side="left")
        ttk.Label(row5, text="（软件不调用 API、不上传任何内容；外部 chat 核实后导入回复再跑第二段）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        self.coop_panel = ttk.Frame(parent)
        self.v_coop_status = tk.StringVar(value="状态：未运行")
        ttk.Label(self.coop_panel, textvariable=self.v_coop_status).pack(anchor="w")
        btns = ttk.Frame(self.coop_panel)
        btns.pack(anchor="w", pady=(2, 0))
        self.coop_taskbook_btn = ttk.Button(btns, text="打开任务书", width=12,
                                            state="disabled", command=self._open_coop_taskbook)
        self.coop_stage_btn = ttk.Button(btns, text="打开暂存目录", width=12,
                                         state="disabled", command=self._open_coop_stage_dir)
        self.coop_deliver_btn = ttk.Button(btns, text="打开交付目录", width=12,
                                           state="disabled", command=self._open_coop_delivery)
        self.coop_record_btn = ttk.Button(btns, text="打开协同记录", width=12,
                                          state="disabled", command=self._open_coop_record)
        self.coop_suggest_btn = ttk.Button(btns, text="打开建议清单", width=12,
                                           state="disabled", command=self._open_coop_suggestions)
        for _btn in (self.coop_taskbook_btn, self.coop_stage_btn, self.coop_deliver_btn,
                     self.coop_record_btn, self.coop_suggest_btn):
            _btn.pack(side="left", padx=(0, 6))
        # ── 批59：全文 AI 核查（任务书附正文；默认关，软件零上传/零费用）─────────
        # 用户 2026-10-20 授权全文外发（可选即可），但**默认不勾**；勾了才在 ph1 的
        # argv 追加 `--coop-fulltext context|full`（引擎把正文/引用上下文附进任务书，
        # 之后由编辑自己复制给外部 chat）。范围默认「引用上下文」（外发得更少）。
        full_row = ttk.Frame(self.coop_panel)
        full_row.pack(anchor="w", pady=(4, 0))
        self.v_coop_fulltext = tk.BooleanVar(value=False)
        _check(full_row, "全文 AI 核查（任务书附正文）", self.v_coop_fulltext).pack(side="left")
        self.v_coop_fulltext_scope = tk.StringVar(value=C.FULLTEXT_CONTEXT)
        for _text, _value in (("引用上下文（默认，约 4 万字上限）", C.FULLTEXT_CONTEXT),
                              ("全文（约 8 万字上限）", C.FULLTEXT_FULL)):
            ttk.Radiobutton(full_row, text=_text, value=_value,
                            variable=self.v_coop_fulltext_scope).pack(
                                side="left", padx=(10, 0))
        self.coop_fulltext_hint = ttk.Label(
            self.coop_panel,
            # 静态提示与 mac `ConvertTab.swift` 的全文核查说明**逐字一致**（批59；两壳同口径，
            # 改文案必须两边同时改，否则「两个平台算出来的结果一致」就破了）。
            text="默认不勾：任务书不含正文，与从前一致（软件零上传、零费用）。\n"
                 "勾上后引擎会把正文/引用上下文写进本机《文献核实任务书》（范围见上）："
                 "文件只留在本机，由你自己复制给外部 chat 做全文核查——软件仍不上传、不调用 API。"
                 "已获授权，不再弹二次确认。\n"
                 "⚠ 只对「开始第一段（出任务书）」生效；第二段续跑与交付不受影响。",
            style="Muted.TLabel", wraplength=820, justify="left")
        self.coop_fulltext_hint.pack(anchor="w")
        # ── 批56：回填导入（多文件/目录/剪贴板 → 已选清单；可移除/清空）──────────
        # 用户实测：mac 上多选两个文件被折叠成父目录、只导入了 json 丢了 bibtex。
        # 这里把「已选清单」做成可见、可改的：每个路径按顺序作为一个 `--coop-replies`
        # 交给引擎（批56 可重复），不再复制到临时目录、不再折叠父目录。
        sel_title = ttk.Frame(self.coop_panel)
        sel_title.pack(anchor="w", pady=(6, 0))
        self.v_coop_sel = tk.StringVar(value="已选 0 项")
        ttk.Label(sel_title, textvariable=self.v_coop_sel).pack(side="left")
        ttk.Label(sel_title, text="（回填 = BibTeX 代码块 + 附一 JSON 两部分；可多文件/目录/剪贴板一起导入）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        addrow = ttk.Frame(self.coop_panel)
        addrow.pack(anchor="w", pady=(2, 0))
        ttk.Button(addrow, text="添加回填文件…", width=14,
                   command=self._add_coop_files).pack(side="left")
        ttk.Button(addrow, text="添加回填目录…", width=14,
                   command=self._add_coop_dir).pack(side="left", padx=(6, 0))
        ttk.Button(addrow, text="从剪贴板导入", width=14,
                   command=self._import_clipboard_reply).pack(side="left", padx=(6, 0))
        self.coop_remove_btn = ttk.Button(addrow, text="移除选中", width=10,
                                          state="disabled",
                                          command=self._remove_selected_coop_replies)
        self.coop_remove_btn.pack(side="left", padx=(10, 0))
        self.coop_clear_btn = ttk.Button(addrow, text="全部清除", width=10,
                                         state="disabled",
                                         command=self._clear_coop_replies)
        self.coop_clear_btn.pack(side="left", padx=(6, 0))
        self.coop_list = tk.Listbox(self.coop_panel, selectmode=tk.EXTENDED, height=4,
                                    exportselection=False)
        theme.style_listbox(self.coop_list)   # 颜色只从 theme 来（主文件不写死颜色）
        self.coop_list.pack(anchor="w", fill="x", pady=(2, 0))
        self.coop_reply_msg = ttk.Label(self.coop_panel, text="",
                                        foreground=theme.COLOR["error"],
                                        wraplength=820, justify="left")
        self.coop_reply_msg.pack(anchor="w")
        run2 = ttk.Frame(self.coop_panel)
        run2.pack(anchor="w", pady=(4, 0))
        self.coop_import_btn = ttk.Button(run2, text="运行第二段…",
                                          state="disabled", command=self._run_coop_ph2)
        self.coop_import_btn.pack(side="left")
        self.v_coop_apply = tk.BooleanVar(value=False)
        _check(run2, "一键修改产物（--coop-apply）", self.v_coop_apply).pack(
            side="left", padx=(10, 0))
        ttk.Label(run2, text="应用前先备份 reference.bib.ai前 / <名>.tex.ai前（可恢复）；"
                             "文献字段→bib、元数据→.tex 的 \\my*、citation_unmatched 脚注→.tex 的 "
                             "\\parencite；须在运行前勾选，交付后重跑是幂等早退",
                  style="Muted.TLabel").pack(side="left", padx=(6, 0))
        self.v_coop_summary = tk.StringVar(value="")
        ttk.Label(self.coop_panel, textvariable=self.v_coop_summary, style="Muted.TLabel",
                  wraplength=820, justify="left").pack(anchor="w", pady=(3, 0))
        # 0 文献回填橙色警告（批56）：只在「文献回填 0/N」时出现（默认收起）
        self.v_coop_warn = tk.StringVar(value="")
        self.coop_warn_label = ttk.Label(self.coop_panel, textvariable=self.v_coop_warn,
                                         foreground=theme.COLOR["warn"],
                                         wraplength=820, justify="left")
        self.coop_panel.pack_forget()              # 默认隐藏，勾上「两段式」才出现

    def refresh_bibdb_hint(self) -> None:
        """回显「引用库 → 引用库设置」里的成本闸门/查库开关（只读，不写配置）。"""
        opts = self.cfg.get("bibdb") or {}
        text = "核实范围：" + S.verify_max_summary(self.cfg)
        if opts.get("verifyAllRefs"):
            text += "；已允许全量核实"
        if opts.get("verifyPlan"):
            text += "；先算账（本次只打印计划，不真正核实）"
        if opts.get("lookupEnabled") is False:
            text += "；本次不查引用库（--no-bibdb）"
        text += "（在「引用库 → 引用库设置」改；默认 = 一个参数都不加）"
        self.bibdb_hint.config(text=text)

    # ── 第五十五批：两段式 AI 协同初排（判据/发现/摘要都在 coop.py；这里只取值/显示）──
    def coop_enabled(self) -> bool:
        return bool(self.v_coop.get())

    def coop_out_root(self) -> str:
        """第一段选定的输出根目录（第二段复用，不再弹目录选择）。"""
        return self._coop_out

    def set_coop_out(self, out: str) -> None:
        self._coop_out = out or ""

    def _toggle_coop_panel(self) -> None:
        if self.coop_enabled():
            self.coop_panel.pack(anchor="w", pady=(2, 0))
        else:
            self.coop_panel.pack_forget()

    def _ensure_coop_stage(self) -> C.CoopStage | None:
        """当前暂存目录：按输出根 + 源稿**重新发现**（源稿/输出目录改过就不认旧的）。"""
        if self._coop_out:
            found = C.discover_stage(self._coop_out, source=self.path())
            if found is not None:
                self._coop_stage = found
        return self._coop_stage

    def _refresh_coop_buttons(self, stage: C.CoopStage | None = None) -> None:
        """按暂存目录当前状态启用/禁用各按钮（找不到的路径一律禁用，不给假按钮）。"""
        stage = stage if stage is not None else self._coop_stage

        def _on(path: str) -> str:
            return "normal" if path and os.path.exists(path) else "disabled"

        self.coop_taskbook_btn.config(state=_on(stage.taskbook if stage else ""))
        self.coop_stage_btn.config(state="normal" if stage else "disabled")
        delivered = bool(stage and stage.delivery_dir)
        self.coop_deliver_btn.config(
            state=_on(stage.delivery_dir if delivered else ""))
        self.coop_record_btn.config(state=_on(stage.record_path if delivered else ""))
        self.coop_suggest_btn.config(state=_on(stage.suggestions_md if stage else ""))
        # 批56：第二段要「已选回填清单」非空才可运行（无回填时明确禁用，不让人白点）
        self.coop_import_btn.config(
            state="normal" if (stage and not delivered and self._coop_sel.count > 0)
            else "disabled")

    def apply_coop_result(self, payload: dict, code: int) -> None:
        """工作线程跑完后的协同结果 → 界面状态（只能主线程调；`_worker` 经队列送来）。"""
        phase = str(payload.get("phase") or "")
        stage = payload.get("stage")
        if not isinstance(stage, C.CoopStage):
            stage = None
        if stage is not None:
            self._coop_stage = stage
        cand = ("（发现 %d 个匹配的暂存目录，已选最近修改的一个）" % stage.found
                if stage is not None and stage.found > 1 else "")
        if code == 0 and phase == "ph1" and stage is not None:
            self._set_coop_warning("")
            self.v_coop_status.set(f"状态：{stage.status_text}")
            self.v_coop_summary.set(
                f"暂存目录：{stage.stage_dir}｜任务书："
                f"{os.path.basename(stage.taskbook) or '（未找到）'}{cand}"
                "｜下一步：把任务书交给外部 chat 核实；回填（BibTeX + 附一 JSON）后"
                "用上面的「添加回填文件…/目录…/从剪贴板导入」加入清单，再点「运行第二段…」"
                "（第二段成功才算交付；最终交付目录本次未写）")
        elif code == 0 and phase == "ph2" and stage is not None:
            self.v_coop_status.set(f"状态：{C.STATUS_DELIVERED}")
            summary = payload.get("summary") or {}
            text = str(summary.get("text") or "")
            if not text:
                # 极老的状态文件（连 keys/record 都没有）→ 退回建议清单口径，仍不瞎编
                text = C.suggestions_text(payload.get("suggestions") or {})
            if summary.get("legacy") and text:
                text += "（旧状态回退统计，部分为估算）"
            self.v_coop_summary.set(
                f"交付目录：{stage.delivery_dir}｜{text}｜协同记录：{C.RECORD_NAME}{cand}")
            self._set_coop_warning(str(summary.get("warning") or ""))
        else:
            self._set_coop_warning("")
            why = payload.get("error") or f"退出码 {code}"
            self.v_coop_status.set(f"状态：{C.STATUS_FAILED}（{why}）")
            if stage is not None:
                self.v_coop_summary.set(
                    f"暂存目录仍在：{stage.stage_dir}（**未交付**，原因见运行日志）")
            else:
                self.v_coop_summary.set(
                    "详见运行日志；没有发现可续跑的暂存目录（第一段可能没跑成）")
        self._refresh_coop_buttons(stage)
        self._refresh_coop_replies()

    # 「打开…」按钮：路径一律来自状态文件（引擎写、界面只读），不存在就明说，不猜。
    def _coop_open(self, path: str) -> None:
        if not path:
            messagebox.showinfo(APP_TITLE, "还没有可打开的文件：请先运行第一段（或第二段）。")
            return
        if not os.path.exists(path):
            messagebox.showwarning(APP_TITLE, f"文件/目录不存在（可能已被移动）：\n{path}")
            return
        open_path(path)

    def _open_coop_taskbook(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.taskbook if stage else "")

    def _open_coop_stage_dir(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.stage_dir if stage else "")

    def _open_coop_delivery(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.delivery_dir if stage else "")

    def _open_coop_record(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.record_path if stage else "")

    def _open_coop_suggestions(self) -> None:
        stage = self._ensure_coop_stage()
        if not stage:
            self._coop_open("")
            return
        # 清单有 .md + .jsonl 两份：优先给人看的 .md（jsonl 是机器可读的逐条记录）
        self._coop_open(stage.suggestions_md if os.path.exists(stage.suggestions_md)
                        else stage.suggestions_jsonl)

    # ── 批56：回填导入（已选清单 / 多选文件 / 目录 / 剪贴板）────────────────
    def _coop_error(self, text: str) -> None:
        """回填导入区的红字提示（为空 = 清掉）。"""
        self.coop_reply_msg.config(text=text or "")

    def _refresh_coop_replies(self) -> None:
        """把 `ReplySelection` 同步到界面：清单、计数、移除/清空/运行按钮状态。"""
        paths = self._coop_sel.paths
        self.coop_list.delete(0, tk.END)
        for path in paths:
            self.coop_list.insert(tk.END, path)
        self.v_coop_sel.set(self._coop_sel.label())
        has_items = bool(paths)
        self.coop_remove_btn.config(state="normal" if has_items else "disabled")
        self.coop_clear_btn.config(state="normal" if has_items else "disabled")
        stage = self._coop_stage
        delivered = bool(stage and stage.delivery_dir)
        self.coop_import_btn.config(
            state="normal" if (stage and not delivered and has_items) else "disabled")

    def add_coop_replies(self, paths) -> tuple[int, int]:
        """把路径加入已选清单，返回 `(新增, 跳过)`（跳过 = 空串/重复）。"""
        added, skipped = self._coop_sel.add(paths)
        self._refresh_coop_replies()
        if added:
            self._coop_error("")
        return added, skipped

    def _add_coop_files(self) -> None:
        """选文件（可多选）：每个文件按顺序作为一个独立的回填路径（不折叠父目录）。"""
        files = fd.askopenfilenames(
            title="选择 AI 回填文件（可多选：BibTeX/JSON/文本）",
            filetypes=[("回填文本", ("*.md", "*.txt", "*.bib", "*.json", "*.jsonl")),
                       ("全部文件", "*.*")])
        paths = [path for path in (files or ()) if path]
        if paths:
            _added, skipped = self.add_coop_replies(paths)
            if skipped:
                self._coop_error(f"已跳过 {skipped} 项（与已选重复或为空）")

    def _add_coop_dir(self) -> None:
        """选目录：目录本身作为一个回填路径（引擎读入其中全部可读文本）。"""
        directory = fd.askdirectory(title="选择 AI 回填目录（读入其中全部 .md/.txt/.bib/.json）")
        if directory:
            self.add_coop_replies([directory])

    def _import_clipboard_reply(self) -> None:
        """「从剪贴板导入」：文本 → 临时 .md（命名确定、带序号）→ 加入已选清单。"""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._coop_error("剪贴板里没有可导入的文本（或不是文本内容）")
            return
        if not str(text or "").strip():
            self._coop_error("剪贴板里没有可导入的文本")
            return
        try:
            if not self._coop_clip_dir:
                self._coop_clip_dir = tempfile.mkdtemp(prefix="slcn-coop-clip-")
            path = C.write_clipboard_reply(text, self._coop_clip_dir)
        except (OSError, ValueError) as e:  # noqa: BLE001
            self._coop_error(f"剪贴板导入失败：{e}")
            return
        self.add_coop_replies([path])
        self._coop_error("")

    def _remove_selected_coop_replies(self) -> None:
        """移除清单里选中的项（多选：从后往前删，避免下标错位）。"""
        indexes = sorted(self.coop_list.curselection(), reverse=True)
        for index in indexes:
            self._coop_sel.remove_index(index)
        self._refresh_coop_replies()

    def _clear_coop_replies(self) -> None:
        self._coop_sel.clear()
        self._refresh_coop_replies()

    def coop_ph1_call(self, out: str) -> E.EngineCall:
        """第一段 argv：页面现有参数 + `--verify-refs --no-ai-merge --coop-taskbook-only`。

        批59：勾了「全文 AI 核查」再追加 `--coop-fulltext context|full`；不勾时值是空串，
        argv 与批58 逐字节相同（默认零变化）。范围/勾选的映射在 `coop.coop_fulltext_mode`。
        """
        args = self.extra_args()
        args.pop("verify_plan", None)      # 第一段绝不传 --verify-plan（引擎会提前 return 0）
        args["coop_taskbook_only"] = True
        args["coop_fulltext"] = C.coop_fulltext_mode(self.v_coop_fulltext.get(),
                                                     self.v_coop_fulltext_scope.get())
        return E.convert_call(source=self.path(), out=out, **args)

    def coop_ph2_call(self, replies) -> E.EngineCall:
        """第二段 argv（最小集）：**重复** `--coop-replies`（按已选顺序）+ 其余由状态继承。

        批56 起允许把多个文件/目录一起交给引擎（用户实测里 json + bibtex 是两个文件，
        批55 只传一个 → 文献回填 0/9）。`--clean` 跟随页面「清理临时文件」开关。
        """
        stage = self._coop_stage
        if stage is None or not self._coop_out:
            raise ValueError("还没有第一段生成的暂存目录：请先勾选「AI 协同初排（两段式）」"
                             "并运行第一段")
        paths = [str(path) for path in (replies or []) if str(path or "").strip()]
        if not paths:
            raise ValueError("还没有加入任何回填：请先「添加回填文件…/添加回填目录…/"
                             "从剪贴板导入」（回填 = BibTeX + 附一 JSON 两部分）")
        # 源稿改过就**不能**拿旧暂存续跑（引擎会因指纹不符拒绝，界面先讲清楚）。
        state_source = str((stage.state.get("source") or {}).get("path") or "")
        current = os.path.abspath(self.path()) if self.path() else ""
        if (state_source and current
                and os.path.normcase(os.path.abspath(state_source)) != os.path.normcase(current)):
            raise ValueError("源稿与第一段时不一致（暂存目录属于另一份稿）："
                             "请重新运行第一段")
        return E.convert_call(source=self.path(), out=self._coop_out,
                              coop_replies=paths, coop_stage=stage.stage_dir,
                              coop_apply=bool(self.v_coop_apply.get()),
                              compile_pdf=bool(self.v_compile.get()),
                              clean=bool(self.v_clean.get()))

    def _run_coop_ph2(self) -> None:
        """「运行第二段…」：用**已选清单**（不再弹文件选择框），先定 --coop-apply 再跑。"""
        stage = self._ensure_coop_stage()
        if stage is None or not self._coop_out:
            messagebox.showwarning(
                APP_TITLE,
                "还没有可续跑的暂存目录：请先勾选「AI 协同初排（两段式）」并点「运行」"
                "跑第一段（任务书/暂存目录出来后这个按钮才能用）。")
            return
        if stage.delivery_dir:
            messagebox.showinfo(APP_TITLE, "这篇已经交付过了。要重新处理请重新运行第一段"
                                           "（第一段会覆盖暂存目录）。")
            return
        if self._coop_sel.count == 0:
            messagebox.showwarning(
                APP_TITLE,
                "还没有加入任何回填：请先用「添加回填文件…/添加回填目录…/从剪贴板导入」"
                "把外部 chat 的回复加进来。\n\n回填 = ①第三节完整 BibTeX 代码块 "
                "②附一 JSON；两部分都要交（可放同一文件，也可多个文件一起导入）。")
            return
        try:
            call = self.coop_ph2_call(self._coop_sel.paths)
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        self.on_run(self, call)          # MainWindow.start(tab, call)：显式传入第二段调用

    def _set_coop_warning(self, text: str) -> None:
        """0 文献回填的橙色警示（空串 = 收起）。"""
        self.v_coop_warn.set(text or "")
        if text:
            self.coop_warn_label.pack(anchor="w", pady=(2, 0))
        else:
            self.coop_warn_label.pack_forget()

    def on_activate(self) -> None:
        self.refresh_bibdb_hint()
        self._ensure_coop_stage()          # 切回本页时重新扫一次暂存目录（源稿可能改过）
        self._refresh_coop_buttons()
        self._refresh_coop_replies()

    def extra_args(self) -> dict:
        args = {"compile_pdf": self.v_compile.get(), "clean": self.v_clean.get(),
                "verify_refs": self.v_refs.get(), "source_pdf": self.v_srcpdf.get(),
                "name": self.v_name.get().strip(), "author": self.v_author.get().strip(),
                "vol": self.v_vol.get().strip(), "issue": self.v_issue.get().strip(),
                "year": self.v_year.get().strip(), "pages": self.v_pages.get().strip(),
                "received": self.v_received.get().strip(),
                "editor": self.v_editor.get().strip(), "grants": self.v_grants.get().strip()}
        # 第 132 条：引用库设置 → convert 参数。默认返回空 dict（一个参数都不加）。
        args.update(S.bibdb_convert_args(self.cfg))
        # 第五十五批：直连 API 的 token 预算。两段式 = 外部 chat（零费用）→ 一个都不传；
        # 非 coop 时也只在「配置了 API 且值 != 引擎默认」才给键（默认 argv 逐字节不变）。
        args.update(S.ai_budget_convert_args(self.cfg, coop=self.coop_enabled()))
        return args


class CheckBibTab(TabPage):
    name = "文献回读校验"
    pick = "file"
    instruction = ("使用说明：选编辑改完交回的 .bib（或产物目录里的 reference.bib）。\n"
                   "勾了「并回产物」会把交回的条目并进产物的 reference.bib —— "
                   "并入前引擎会自动留一份 `reference.bib.ai前` 备份。\n"
                   "第 132 条：「入藏到引用库」把**产物** reference.bib 按往期成品 bib 入库"
                   "（verified=true）——编辑交回的那份是待核对稿，不要入。")

    def __init__(self, parent, name, on_run, on_stop, *, log=None, post=None, cfg=None):
        # 「入藏到引用库」要后台跑引擎再回主线程更新界面：`post` 是主窗口的队列入口，
        # `log` 是运行日志。Tkinter 不是线程安全的（见 main.MainWindow._worker 的注释）。
        self._log = log or (lambda _text: None)
        self._post = post or (lambda _item: None)
        self.cfg = cfg or {}
        self._importing = False
        self._import_where = ""
        super().__init__(parent, name, on_run, on_stop)

    def build_options(self, parent: ttk.Frame) -> None:
        row1 = ttk.Frame(parent)
        row1.pack(anchor="w")
        self.v_merge = tk.BooleanVar(value=False)
        _check(row1, "并回产物 reference.bib（合并前自动备份 .ai前）", self.v_merge).pack(side="left")
        # 与 mac 对等的三个可选输入（U7）：引擎早支持，界面从前没传 —— 报告里一直写着
        # 「原件：未提供（不做字段级 diff）」「正文：未提供（不做引用键闭合核对）」。
        row2 = ttk.Frame(parent)
        row2.pack(anchor="w", pady=(4, 0))
        self.v_original = tk.StringVar(value="")
        self.v_tex = tk.StringVar(value="")
        self.v_out = tk.StringVar(value="")
        for label, var, width in (("原件 .bib", self.v_original, 26),
                                  ("正文 .tex", self.v_tex, 20),
                                  ("产物目录", self.v_out, 18)):
            ttk.Label(row2, text=label).pack(side="left", padx=(8, 2))
            ttk.Entry(row2, textvariable=var, width=width).pack(side="left")

        # 第 132 条：把产物 bib 入藏到引用库（复用「引用库 → 入藏」的引擎调用，
        # 这里只做「取路径 → 推断期/篇 → 后台跑 → 显示结果」，判据一行都不在这儿）。
        row3 = ttk.Frame(parent)
        row3.pack(anchor="w", pady=(4, 0))
        self.import_btn = ttk.Button(row3, text="入藏到引用库", width=14,
                                     command=self.import_to_library)
        self.import_btn.pack(side="left")
        self.import_status = ttk.Label(row3, text="", style="Muted.TLabel",
                                       wraplength=620, justify="left")
        self.import_status.pack(side="left", padx=(8, 0))

    # ── 入藏到引用库（第 132 条）──
    def import_to_library(self) -> None:
        """把**产物** reference.bib 按「往期成品 bib」入藏（`import-bib --verified`）。

        为什么只认「原件 .bib」：编辑交回的 .bib 是待核对的稿子，不能当成品入
        `verified=true`（那会把没核对完的内容当成已核实事实）；产物的
        `reference.bib` 才是准备随本期出版的那份 —— 与 mac `CheckBibView` 同一口径。
        """
        if self._importing:
            return
        bib = self.v_original.get().strip().strip('"')
        if not bib or not os.path.isfile(bib):
            messagebox.showwarning(APP_TITLE, "先在「原件 .bib」填产物目录里的 reference.bib"
                                              " —— 要入藏的是它，不是编辑交回的那份。")
            return
        notes = E.effective_notes_repo(self.cfg.get("notesRepoPath", ""))
        if not notes:
            messagebox.showwarning(APP_TITLE, "找不到私有笔记仓，无法确定引用库位置。\n"
                                              "请到「引用库 → 引用库设置」页指定笔记仓路径。")
            return
        issue, slug = E.infer_issue_and_slug(bib)
        self._import_where = " / ".join(part for part in (issue, slug) if part) \
            or "（未从路径推断出期/篇）"
        try:
            call = E.bibdb_import_bib_call(
                bib=bib, issue=issue, slug=slug, dry_run=False,
                db=E.bibdb_db_path(notes) or "", verified=True)
        except RuntimeError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._importing = True
        self.import_btn.config(state="disabled")
        self.import_status.config(text="⏳ 正在入藏到引用库…",
                                  foreground=theme.COLOR["muted"])
        self._log(f"引用库入藏（产物 bib → 已核实）：{bib}")
        B.run_engine_async(post=self._post, call=call, on_result=self._on_import_result,
                           log=self._log, api=self.cfg.get("api"),
                           notes=self.cfg.get("notesRepoPath", ""))

    def _on_import_result(self, code: int, lines: list) -> None:
        """入藏结果回主线程（`B.run_engine_async` 的约定：这里已经在 UI 线程）。"""
        self._importing = False
        self.import_btn.config(state="normal")
        payload = E.parse_json_lines(lines)
        if code != 0 or not isinstance(payload, dict):
            text = E.output_tail(lines, count=2)
            self.import_status.config(text=f"❌ 入藏失败：{text}",
                                      foreground=theme.COLOR["error"])
            self._log(f"❌ 入藏到引用库失败：{text}")
            return
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        line = B.summary_line(report)
        self.import_status.config(text="✅ 已入藏：" + line,
                                  foreground=theme.COLOR["ok"])
        self._log(f"  来源：{self._import_where}")
        self._log("  " + line)
        if not payload.get("db_existed"):
            self._log("  （本次新建了引用库文件）")
        self._log("  （库变了：建议到「引用库 → 待裁决」页重新生成清单。）")

    def extra_args(self) -> dict:
        return {"merge_into_same_dir": self.v_merge.get(),
                "original": self.v_original.get().strip(),
                "tex": self.v_tex.get().strip(),
                "out": self.v_out.get().strip()}


class MergeTab(TabPage):
    name = "合并文件"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**（里面有 扉页.pdf、000 目录/、01 作者/ …）。\n"
                   "例如 E:\\…\\2026年第5期(90)——不要在末尾多写字，直接选目录即可。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_editors = tk.BooleanVar(value=True)
        self.v_back = tk.BooleanVar(value=False)
        self.v_nest = tk.BooleanVar(value=True)
        _check(parent, "插入编委页", self.v_editors).pack(side="left")
        _check(parent, "加封底页", self.v_back).pack(side="left")
        _check(parent, "小节书签挂在文章下", self.v_nest).pack(side="left")
        # 预览清单（与 mac 的 `--detect` 对等）：先看会合并哪些文件，不写任何产物
        ttk.Button(parent, text="预览清单", width=10, command=self.preview).pack(
            side="left", padx=(12, 0))

    def extra_args(self) -> dict:
        return {"editors_page": self.v_editors.get(), "back_cover": self.v_back.get(),
                "nest_outlines": self.v_nest.get()}

    def preview(self) -> None:
        """先看会合并哪些文件（`mergepdf.py --detect`）—— 不写任何产物。"""
        folder = self.path()
        if not folder:
            messagebox.showwarning(APP_TITLE, "请先选目录。")
            return
        try:
            call = E.merge_call(folder=folder, detect=True)
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        lines: list[str] = []
        if E.run_call(call, on_line=lines.append) != 0:
            messagebox.showerror(APP_TITLE, "预览失败（详见日志）。")
            return
        tail = "\n".join(lines[-18:])
        messagebox.showinfo(APP_TITLE, "会合并这些（--detect，没写任何文件）：\n\n" + tail[-1500:])


class InfoCheckTab(TabPage):
    name = "检查tex信息"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**，把各篇文章 tex 里已填好的信息抄出来核对：\n"
                   "卷号 / 期号 / 年份 / 起止页码 / 页数 / 字号 / 中英文标题 / 各作者 / 全文行距，\n"
                   "并与该篇 PDF 的**实际页数**对比。没有任何问答 —— 信息本来就在 tex 里。\n"
                   "产物：`信息检查<时间戳>.txt`（与原版一致）与 `.json`（结构化，供「生成目录」用）。")

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent, name, on_run, on_stop)
        # 结果表格：Tkinter 的 Treeview（只读展示）
        self.summary = ttk.Label(self, text="（还没检查：选一期的目录，点「运行」）",
                                 wraplength=760, justify="left")
        self.summary.grid(row=90, column=0, padx=10, pady=(4, 0), sticky="w")
        cols = ("folder", "vol", "issue", "year", "pages", "declared", "actual", "authors", "title")
        heads = ("文件夹", "卷", "期", "年份", "起–止", "声明页数", "实际页数", "第一作者", "中文标题")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=9)
        for c, h in zip(cols, heads):
            self.table.heading(c, text=h)
            # 宽度：第一列放「01 A_Author」，太窄会被截成 A_Autho|（2026-10-17 截图自查）
            self.table.column(c, width=130 if c == "folder"
                              else 118 if c in ("authors", "title") else 62, anchor="w")
        self.table.grid(row=91, column=0, padx=10, pady=(2, 10), sticky="nsew")
        self.rowconfigure(91, weight=1)

    def show_report(self, payload: dict) -> None:
        """填表格与顶部提示（`payload` = 引擎 `--preview` 的 JSON）。"""
        for item in self.table.get_children():
            self.table.delete(item)
        arts = payload.get("articles", [])
        for a in arts:
            def nz(v, dash="—"):
                return dash if not v else str(v)
            span = f"{nz(a.get('startPage'), '?')}–{nz(a.get('endPage'), '?')}"
            declared = nz(a.get("pages"))
            actual = nz(a.get("actualPDFPages"))
            mismatch = (a.get("pages") or "").strip().isdigit() and a.get("actualPDFPages") \
                and int(a["pages"]) != a["actualPDFPages"]
            self.table.insert("", "end", values=(
                a.get("folder", ""), nz(a.get("volume")), nz(a.get("issue")), nz(a.get("year")),
                span, declared, actual,
                (a.get("firstAuthorChinese") or a.get("firstAuthorEnglish") or "—"),
                (a.get("chineseTitle") or "—")[:40]))
            if mismatch:
                self.table.item(self.table.get_children()[-1], tags=("bad",))
        self.table.tag_configure("bad", foreground=theme.COLOR["error"])
        notes = payload.get("inconsistencies") or []
        if not arts:
            self.summary.config(text="（还没检查）")
        elif notes:
            self.summary.config(text=f"共 {len(arts)} 篇；发现 {len(notes)} 处需要核对：" + "；".join(notes[:3]))
        else:
            # ⚠️ 不许"假绿"：有的篇没有可比对的 PDF 页数（PDF 打不开 / 没 PDF）时，
            #    说「页数与 PDF 实际页数相符」是**撒谎**（实测：坏 PDF 被跳过后这里照样写"相符"）。
            n_nopdf = sum(1 for a in arts if not a.get("actualPDFPages"))
            if n_nopdf:
                self.summary.config(
                    text=f"共 {len(arts)} 篇，卷号/期号/年份一致；但 {n_nopdf} 篇**没有可比对的**"
                         " PDF 页数（PDF 缺失或打不开，见日志）——页数一致性未验证。".replace("**", ""))
            else:
                self.summary.config(text=f"共 {len(arts)} 篇，卷号/期号/年份一致，页数与 PDF 实际页数相符。")


class TocTab(TabPage):
    name = "生成目录"
    pick = "file"
    instruction = ("使用说明：选「检查tex信息」生成的**报告 .txt**，生成中英文两份目录 .tex（**先英文后中文**）。\n"
                   "版式**继承上一期成品**（本期 000 Table of Contents 里已有的优先），只替换本期的卷期信息，\n"
                   "所以同事手工调过的版式会自动延续。下方可填本期栏目（专栏/专辑），留空表示本期无栏目。\n"
                   "点「读取篇目」会列出报告里的文章，可勾选哪些属于栏目 —— 设置会写进报告同目录的 `目录设置.json`。")

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent, name, on_run, on_stop)
        self.rows: list[tuple[str, str]] = []      # [(文章名, 中文标题)]
        self.note = ttk.Label(self, text="（还没读取篇目：选报告后点「读取篇目」）",
                              wraplength=760, justify="left", style="Muted.TLabel")
        self.note.grid(row=90, column=0, padx=10, pady=(4, 0), sticky="w")
        # ⚠️ `tk.Listbox` 保留：Tk 没有 ttk 对等物。颜色由 `theme.COLOR` 喂进去
        #    （`theme.style_listbox`）—— 主文件里不写死颜色。
        self.listbox = tk.Listbox(self, selectmode=tk.EXTENDED, height=7, exportselection=False)
        theme.style_listbox(self.listbox)
        self.listbox.grid(row=91, column=0, padx=10, pady=(2, 10), sticky="nsew")
        self.rowconfigure(91, weight=1)

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_kind = tk.StringVar(value="")
        self.v_cname = tk.StringVar(value="")
        self.v_cname_en = tk.StringVar(value="")
        self.v_host = tk.StringVar(value="")
        self.v_template = tk.StringVar(value="")
        ttk.Button(parent, text="读取篇目", width=10, command=self.load_preview).pack(side="left")
        ttk.Label(parent, text="栏目类型").pack(side="left", padx=(10, 2))
        ttk.Combobox(parent, textvariable=self.v_kind, width=6, state="readonly",
                     values=("", "专栏", "专辑")).pack(side="left")
        ttk.Label(parent, text="栏目名").pack(side="left", padx=(8, 2))
        ttk.Entry(parent, textvariable=self.v_cname, width=16).pack(side="left")
        ttk.Label(parent, text="英文名").pack(side="left", padx=(8, 2))
        ttk.Entry(parent, textvariable=self.v_cname_en, width=18).pack(side="left")
        ttk.Label(parent, text="主持人").pack(side="left", padx=(8, 2))
        ttk.Entry(parent, textvariable=self.v_host, width=10).pack(side="left")
        ttk.Label(parent, text="模板来源期目录（留空=自动找上一期）").pack(side="left", padx=(8, 2))
        ttk.Entry(parent, textvariable=self.v_template, width=22).pack(side="left")

    def extra_args(self) -> dict:
        return {"template": self.v_template.get()}

    def load_preview(self) -> None:
        """问引擎要篇目清单（`toc.py --preview`），不要自己解析报告 —— 口径必须与生成时一致。"""
        report = self.path()
        if not report:
            messagebox.showwarning(APP_TITLE, "请先选择「检查tex信息」生成的报告 .txt。")
            return
        try:
            call = E.toc_preview_call(report=report)
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        proc = subprocess.run(call.argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=E.env_for_run(None), timeout=120,
                              # Windows：与 engine.run_call 同一口径，别弹黑框
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = ""
        for candidate in (proc.stdout or "").splitlines():
            if candidate.strip().startswith("{"):
                line = candidate
        if not line:
            messagebox.showerror(APP_TITLE, "读不到篇目预览，请看「设置」里的引擎路径是否正确。")
            return
        try:
            payload = json.loads(line)
        except ValueError:
            messagebox.showerror(APP_TITLE, "篇目预览不是合法 JSON（引擎版本可能过旧）。")
            return
        self.rows = [(a.get("name", ""), a.get("title", "")) for a in payload.get("articles", [])]
        self.listbox.delete(0, tk.END)
        saved = self._load_settings(report)
        for name, title in self.rows:
            self.listbox.insert(tk.END, f"{name}　{title[:34]}")
            if name in saved.get("columnArticles", []):
                self.listbox.selection_set(tk.END)
        self.note.config(text=plain(str(payload.get("templateNote", ""))))
        # ⚠️ **逐字段**回填：从前这一整块被 `if saved.get("columnKind"):` 罩着 ——
        #    编辑只填了「模板来源期目录」、没填栏目类型时，读篇目**不回填**任何字段，
        #    紧接着 `save_settings()` 又无条件写回界面上的空值 → 他填的模板期被**静默抹掉**
        #    （2026-10-17 审计实测：文件里 templateFolder=第4期(89)、columnKind 为空
        #    → 读篇目后输入框空 → 运行后文件里变 ""）。现在按 key 存在与否逐个回填。
        for _key, _var in (("columnKind", self.v_kind), ("columnName", self.v_cname),
                           ("columnNameEN", self.v_cname_en), ("columnHost", self.v_host),
                           ("templateFolder", self.v_template)):
            if isinstance(saved.get(_key), str):
                _var.set(saved[_key])

    @staticmethod
    def _settings_path(report: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(report)), "目录设置.json")

    def _load_settings(self, report: str) -> dict:
        try:
            with open(self._settings_path(report), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self, report: str) -> None:
        """把界面上的本期设置写进引擎要读的那个文件（只有一个真相来源）。"""
        path = self._settings_path(report)
        data = self._load_settings(report)
        data.update({
            "columnKind": self.v_kind.get(),
            "columnName": self.v_cname.get(),
            "columnNameEN": self.v_cname_en.get(),
            "columnHost": self.v_host.get(),
            "columnArticles": [self.rows[i][0] for i in self.listbox.curselection()]
            if self.rows else data.get("columnArticles", []),
            "templateFolder": self.v_template.get(),
        })
        data.setdefault("titleWrapLimit", 20)
        # 英文断行默认 30（2026-10-17 第十四批按期 90 成品校准，原 35 见案例第 110 条）
        data.setdefault("titleWrapLimitEN", 30)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


class CleanTab(TabPage):
    name = "删除临时文件"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**（或任意目录），会递归删掉 LaTeX 编译中间文件：\n"
                   ".aux .bbl .bcf .blg .out .run.xml .synctex.gz（以及 macOS 的 .DS_Store）。\n"
                   "**不会删** .tex / .pdf / .sty / .bib / .log —— .log 是排错用的，故意留着。\n"
                   "换了设备改过 .tex 之后，建议先清一遍再编译，避免残留中间文件影响参考文献序号。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_dry = tk.BooleanVar(value=True)
        _check(parent, "先预演（只列出会删哪些，不真删）", self.v_dry).pack(side="left")

    def extra_args(self) -> dict:
        return {}

    def confirm_trash(self) -> None:
        """点「运行」时：先预演一遍，把**具体清单与大小**摆给编辑看，确认后才移入回收目录。

        依据（`UIUX-PROPOSAL.md` 第 4 节）：确认框只用于后果严重的动作，且必须**具体**
        （删什么/多大/不碰什么），按钮文案是动词；能撤销就别用红色 destructive。
        用户取消 → 抛 ValueError，走现有的"已取消"提示，**一个文件都不动**。
        """
        folder = self.path()
        if not folder:
            raise ValueError("请先选目录")
        call = E.cleantemp_call(folder=folder, dry_run=True, trash=True)
        lines: list[str] = []
        if E.run_call(call, on_line=lines.append) != 0:
            raise ValueError("预演失败，详见下面日志（什么都没动）")
        items = [l[len("Will move: "):] for l in lines if l.startswith("Will move: ")]
        if not items:
            raise ValueError("没有需要清理的临时文件（什么都没动）")
        size = next((l.strip() for l in lines if "共 " in l and "（预演）" in l), "")
        head = "\n".join("  " + x for x in items[:12])
        if len(items) > 12:
            head += f"\n  … 还有 {len(items) - 12} 个"
        ok = messagebox.askyesno(
            "删除临时文件（可撤销）",
            plain(f"将把 {len(items)} 个编译中间文件移入回收目录。\n{size}\n\n{head}\n\n"
            "只处理 .aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/.DS_Store；\n"
            "**不会动** .tex/.pdf/.sty/.bib/.log，也**不进** original/（作者来稿）。\n\n"
            "移入后可以点结果行里的「撤销清理」还原。要继续吗？"))
        if not ok:
            raise ValueError("已取消（一个文件都没动）")


class CompileAllTab(TabPage):
    name = "编译全部"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**，对每个 `01 作者/` 子目录编译 `<作者>.tex`：\n"
                   "xelatex → biber -l=zh_pinyin → xelatex → xelatex。\n"
                   "`000 目录`（中英文目录）不参与编译；某一篇失败只停那一篇，并回显 .log 里的错误摘要。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_only = tk.StringVar(value="")
        self.v_dry = tk.BooleanVar(value=False)
        ttk.Label(parent, text="只编这几篇（序号，如 01,03；留空=全部）").pack(side="left")
        ttk.Entry(parent, textvariable=self.v_only, width=12).pack(side="left", padx=(4, 12))
        _check(parent, "先预演（只列出会编哪些）", self.v_dry).pack(side="left")

    def extra_args(self) -> dict:
        return {"only": self.v_only.get(), "dry_run": self.v_dry.get()}


class PublishTab(TabPage):
    name = "本期出刊"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**，点「运行」按顺序跑完四步：\n"
                   "① 检查tex信息 → ② 生成目录 → ③ 合并整期 PDF → ④ 编译全部。\n"
                   "**失败即中止**（前 N−1 步的产物保留，日志末行会写明卡在第几步）；\n"
                   "退出码 1（有结果/需人工核对，例如有文章编译失败）只算**橙色警告**并继续。\n"
                   "要单步重跑某一环节，仍可用左边的单个页面（编排与判据在引擎 `publish.py`，只有一份）。")

    def build_options(self, parent: ttk.Frame) -> None:
        pass


class ProofCheckTab(TabPage):
    name = "一校核查"
    pick = "file"
    instruction = ("使用说明：选**初排稿的 .tex**（一般在一校之后跑）。按《排校手册》与\n"
                   "《流程化经验书》的规范逐条核查：脚注/引文位置（逗号前、句号后）、半角标点混入中文、\n"
                   "顿号后紧跟引号、集合竖线、图表引用间隔……每条都注明依据与行号。\n"
                   "**默认只报不改**（报告写到同目录的 `<名字>_一校核查.md`）；\n"
                   "勾了「顺手把无歧义的改掉」会另写一个 `<名字>_一校核查后.tex`，**输入文件不动**。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_fix = tk.BooleanVar(value=False)
        self.v_lang = tk.StringVar(value="自动")
        _check(parent, "顺手把无歧义的改掉（另存新文件）", self.v_fix).pack(side="left")
        ttk.Label(parent, text="语种").pack(side="left", padx=(12, 2))
        ttk.Combobox(parent, textvariable=self.v_lang, width=6, state="readonly",
                     values=("自动", "zh", "en")).pack(side="left")

    def extra_args(self) -> dict:
        lang = "" if self.v_lang.get() == "自动" else self.v_lang.get()
        return {"fix": self.v_fix.get(), "lang": lang}


class SettingsTab(ttk.Frame):
    """设置：引擎状态 + AI 配置（密钥只落本地，显示打码）。"""

    name = "设置"

    def __init__(self, parent, cfg: dict, on_save, post=None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_save = on_save
        # 子线程只往队列里放结果（Tk 控件一律在主线程改 —— 项目纪律）
        self.post = post or (lambda _msg: None)
        self.columnconfigure(1, weight=1)
        row = 0

        self.status_label = ttk.Label(self, text="", wraplength=760, justify="left")
        self.status_label.grid(row=row, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        row += 1

        self.vars: dict[str, tk.StringVar] = {}
        for label, key, hint in (
            ("引擎目录（Engine/）", "engineDir", "留空 = 自动找；也可用环境变量 SLCN_ENGINE"),
            ("API 地址", "base", "例如 https://api.deepseek.com"),
            ("API Key", "key", "只存在本机，显示时打码；绝不出现在命令行里"),
            ("模型名", "model", "例如 deepseek-chat"),
            ("检索工具名/路径", "searchTool",
             "留空 = 用引擎默认；内置检索由端点/模型决定（DeepSeek 需 deepseek-v4-pro）"),
        ):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=3, sticky="e")
            var = tk.StringVar(value=str(cfg.get("api", {}).get(key, "") or cfg.get(key, "")))
            self.vars[key] = var
            show = "•" if key == "key" else ""
            entry = ttk.Entry(self, textvariable=var, width=52, show=show)
            entry.grid(row=row, column=1, padx=10, pady=3, sticky="ew")
            if key == "key":
                self.key_entry = entry                      # 显示/隐藏要切它的 show 属性
                self.v_show_key = tk.BooleanVar(value=False)
                tools = ttk.Frame(self)
                tools.grid(row=row, column=1, padx=10, sticky="e")
                ttk.Checkbutton(tools, text="显示密钥", variable=self.v_show_key,
                                command=self._toggle_key_visible).pack(side="right")
                ttk.Button(tools, text="清除密钥", width=10, command=self._clear_key).pack(
                    side="right", padx=6)
            ttk.Label(self, text=hint, style="Muted.TLabel").grid(
                row=row + 1, column=1, padx=10, sticky="w")
            row += 2

        # 方案 §4「设置页不吓人」：只留**必需**项。思考模式/最长输出是任务相关选项，
        # 已挪到「初排转换」页的 AI 区域（那里才知道上下文）。
        self.v_web = tk.BooleanVar(value=bool(cfg.get("api", {}).get("webSearch", False)))
        # P2（第 72 条起）：检索证据开关（默认关）——与「允许联网检索」是**两件事**：
        # 后者是模型内置检索工具；前者是工具自己用 DeepSeek 独立 Search API 检索证据，
        # 把证据注入提示词并在合并前校验 `% 来源：` URL（按次计费 ¥5/1000 次）。
        self.v_searchrefs = tk.BooleanVar(value=bool(cfg.get("api", {}).get("searchRefs", False)))
        opts = ttk.Frame(self)
        opts.grid(row=row, column=1, padx=10, pady=6, sticky="w"); row += 1
        _check(opts, "允许联网检索", self.v_web).pack(side="left")
        _check(opts, "检索参考文献证据（DeepSeek 独立 Search API，按次计费；默认关）",
               self.v_searchrefs).pack(side="left", padx=(16, 0))

        # ── 第五十五批：AI 直连 API 的 token 预算闸（默认 30000 / 1000000；0 = 不限）──
        # 只约束「直连 API」通道；两段式外部 chat 由编辑自己操作、软件零上传零费用。
        api_cfg = cfg.get("api") or {}
        self.v_budget = tk.StringVar(
            value=str(api_cfg.get("budgetTokens", S.AI_BUDGET_DEFAULT)))
        self.v_batch_budget = tk.StringVar(
            value=str(api_cfg.get("batchBudgetTokens", S.AI_BATCH_BUDGET_DEFAULT)))
        budget_row = ttk.Frame(self)
        budget_row.grid(row=row, column=1, padx=10, pady=(6, 0), sticky="w"); row += 1
        ttk.Label(budget_row, text="单篇 token 预算").pack(side="left")
        ttk.Entry(budget_row, textvariable=self.v_budget, width=10).pack(
            side="left", padx=(4, 14))
        ttk.Label(budget_row, text="单批 token 预算").pack(side="left")
        ttk.Entry(budget_row, textvariable=self.v_batch_budget, width=12).pack(
            side="left", padx=(4, 10))
        ttk.Label(budget_row, text="0 = 不限", style="Muted.TLabel").pack(side="left")
        ttk.Label(
            self,
            text=(f"默认 {S.AI_BUDGET_DEFAULT} / {S.AI_BATCH_BUDGET_DEFAULT}；"
                  "只约束「直连 API」通道（超预计用量先打印账、不发起请求），"
                  "直连也只发条目/片段、不发正文全文。两段式外部 chat 流程由编辑自己操作，"
                  "软件不上传任何内容、零费用，预算不参与。"),
            style="Muted.TLabel", wraplength=760, justify="left").grid(
            row=row, column=1, padx=10, sticky="w"); row += 1
        # 非法值红字提示（不落盘）：颜色取 theme，不写死色号（(f) 组有守卫）
        self.budget_error = ttk.Label(self, text="", foreground=theme.COLOR["error"],
                                      wraplength=760, justify="left")
        self.budget_error.grid(row=row, column=1, padx=10, pady=(0, 4), sticky="w"); row += 1

        # 密钥常驻状态行：说清「已存/末四位/只在本机」—— 隐藏字段光靠打码，编辑不知道配没配对
        self.key_status = ttk.Label(self, text="", wraplength=760, justify="left",
                                    style="Muted.TLabel")
        self.key_status.grid(row=row, column=1, padx=10, pady=(0, 4), sticky="w"); row += 1
        btns = ttk.Frame(self)
        btns.grid(row=row, column=1, padx=10, pady=10, sticky="w")
        ttk.Button(btns, text="保存设置", width=14, style="Accent.TButton",
                   command=self.save).pack(side="left")
        # 测试连接（mac 侧早就有这个按钮）：把"配对了没有"从玄学变事实
        ttk.Button(btns, text="测试连接", width=12,
                   command=self.test_connection).pack(side="left", padx=8)
        self.refresh_status()
        self.refresh_key_status()

    def test_connection(self) -> None:
        """打一次最小请求，把「连不连得上 / 密钥对不对」变成一条实测结论。

        在**工作线程**里发请求（不冻界面），结果经主窗口队列回传（子线程不碰 Tk 控件）。
        密钥只进 Authorization 头，不进日志、不进命令行。
        """
        base = self.vars["base"].get().strip().rstrip("/")
        key = self.vars["key"].get().strip()
        model = self.vars["model"].get().strip()
        if not base:
            messagebox.showwarning(APP_TITLE, "请先填 API 地址（如 https://api.deepseek.com）")
            return
        self.key_status.config(text="⏳ 测试连接中…（最多等 10 秒）")
        threading.Thread(target=self._probe, args=(base, key, model), daemon=True).start()

    def _probe(self, base: str, key: str, model: str) -> None:
        import time as _t
        import urllib.request
        def report(text: str) -> None:
            self.post(("__KEYSTATUS__", text))
        t0 = _t.monotonic()
        # ① 先试 GET /models（尽力而为：自建端点没有这个路由不算失败）
        req = urllib.request.Request(base + "/models", headers={"User-Agent": "SLCN-toolbox"})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                report(f"✅ 连接正常（{_t.monotonic() - t0:.1f} 秒，HTTP {resp.status}）· 密钥只在本机使用")
                return
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 401:
                report("⛔ 401 密钥无效（地址对、密钥不对）")
                return
            if code == 403:
                report("⛔ 403 无权限（密钥或 IP 受限）")
                return
            # ② 退回打一次最小 chat 请求（有的端点不提供 /models）
        body = json.dumps({"model": model or "deepseek-chat", "max_tokens": 1,
                           "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
        req2 = urllib.request.Request(base + "/chat/completions", data=body,
                                      headers={"Content-Type": "application/json",
                                               "User-Agent": "SLCN-toolbox"})
        if key:
            req2.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req2, timeout=10) as resp:
                report(f"✅ 连接正常（{_t.monotonic() - t0:.1f} 秒，HTTP {resp.status}）· 密钥只在本机使用")
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code in (401, 403):
                report(f"⛔ {code} 密钥无效或无权限")
            else:
                report(f"⛔ 连不上：{e}")

    def _toggle_key_visible(self) -> None:
        """眼睛开关：只切输入框的显示方式，**不落盘**。"""
        self.key_entry.config(show="" if self.v_show_key.get() else "•")

    def _clear_key(self) -> None:
        """清空密钥（只清界面输入框；点「保存」才真正覆盖磁盘 —— 与 mac 同一口径）。"""
        self.vars["key"].set("")
        self.refresh_key_status()

    def refresh_key_status(self) -> None:
        key = self.vars["key"].get().strip()
        if not key:
            self.key_status.config(text="密钥：未配置（本机/自建端点通常不需要）")
            return
        tail = key[-4:] if len(key) >= 8 else "…"
        self.key_status.config(
            text=f"密钥：已保存 · 末四位 {tail} · 只存在本机设置文件里，不会上传、不进命令行")

    def refresh_status(self) -> None:
        st = E.status()
        text = st.describe()
        if st.problems:
            text += "\n" + "\n".join("· " + p for p in st.problems)
        text += "\n（引擎目录改完点「保存」即生效；这一行显示的是当前实际解析到的路径）"
        self.status_label.config(text=text)

    def save(self) -> None:
        # 第五十五批：预算输入先校验 —— 非法值**不落盘**（红字提示，旧值保留）；
        # 其他设置照常保存，不让一个填错的预算把整页保存卡死。
        budget_article, err_article = S.parse_budget_tokens(
            self.v_budget.get(), S.AI_BUDGET_DEFAULT)
        budget_batch, err_batch = S.parse_budget_tokens(
            self.v_batch_budget.get(), S.AI_BATCH_BUDGET_DEFAULT)
        errors = [e for e in (err_article, err_batch) if e]
        self.budget_error.config(
            text=("；".join(errors) + "（本次未保存预算，其他设置照常保存）") if errors else "")
        for key, var in self.vars.items():
            if key == "engineDir":
                self.cfg["engineDir"] = var.get().strip()
                # 立刻生效（不用重开）：engine.py 读的就是这个环境变量
                if self.cfg["engineDir"]:
                    os.environ["SLCN_ENGINE"] = self.cfg["engineDir"]
                else:
                    os.environ.pop("SLCN_ENGINE", None)
            else:
                self.cfg.setdefault("api", {})[key] = var.get().strip()
        # thinking/maxTokens 不再由本页负责（input 在「初排转换」页，改即写进 cfg）
        self.cfg["api"]["webSearch"] = bool(self.v_web.get())
        self.cfg["api"]["searchRefs"] = bool(self.v_searchrefs.get())
        if not errors:
            self.cfg["api"]["budgetTokens"] = budget_article
            self.cfg["api"]["batchBudgetTokens"] = budget_batch
        try:
            path = S.save(self.cfg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"保存失败：{e}")
            return
        # 保存回执：**写进窗口**而不是弹窗（静默成功/静默失败在用户眼里一样 —— 方案 §4）
        self.refresh_key_status()
        self.log(f"✅ 已保存设置（{time.strftime('%H:%M:%S')}，仅存在本机：{path}）") if hasattr(self, "log") else None
        self.key_status.config(
            text=f"✅ 已保存 · {time.strftime('%H:%M:%S')}（仅存在本机：{path}）")


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        # 主题必须**先**套：clam + 色板 + 字号（见 theme.py 抬头；用户 2026-10-17 拍板
        # vista → clam —— vista 的控件由系统绘制，改不动颜色，做不出与 mac 同一套观感）。
        theme.apply(self)
        self.geometry("1000x700+340+100")   # 侧栏吃掉 ~184px，最小宽度跟着提（UI 方案候选 B）
        self.title(APP_TITLE)
        self.cfg = S.load()
        # U4 日志偏好（默认摘要 = 原始层折叠）：`settings.DEFAULTS` 里已有这个键，
        # 读回后写进 cfg —— 否则设置页点一次「保存」会把整个 cfg 写回、抹掉这份偏好。
        self.cfg["logShowRaw"] = bool(self.cfg.get("logShowRaw", False))
        # ⚠️ 设置页的「引擎目录」必须**真的生效**：`engine.py` 只认环境变量 `SLCN_ENGINE`，
        #    从前这里写进 cfg 却全仓无人读 → 自动定位失败时，双击 exe 的编辑在 GUI 里
        #    **没有任何自救入口**（他们不会去设环境变量）。2026-10-17 审计实测（M5）。
        #    用 `setdefault`：显式设过环境变量的人仍然优先，不被界面覆盖。
        _saved_engine = self.cfg.get("engineDir", "").strip()
        if _saved_engine:
            os.environ.setdefault("SLCN_ENGINE", _saved_engine)
        self._proc: subprocess.Popen | None = None
        self._running_tab = None
        self._t0 = 0.0
        self._phase = ""
        self._q: queue.Queue[str] = queue.Queue()
        self._busy = False
        self._current = 0
        #: 完整日志行 `(显示文本, 级别 tag, 是否摘要行)` —— 视图只画其中一部分，
        #: 但复制/保存/切换模式都要用**全量**（U4：摘要只是默认视图，不是数据）。
        self._log_lines: list[tuple[str, str, bool]] = []
        self._log_folded = 0                    # 摘要模式下折叠了几行（工具条显示）
        self._log_placeholder_active = False    # 日志框里现在是不是那行灰占位

        # ── 分组侧边栏（UI 方案候选 B；2026-10-17 用户批准）──
        # 为什么不再用 9 个平级标签：WinUI 说「左侧导航适合 5–10 个**同等重要**的分类」，
        # HIG 说侧栏「层级不超过两层」—— 我们的 9 个其实是三层（单篇生产 / 整期装配 /
        # 维护设置），却摆成 9 个平级项。分组后一层是组、二层是页，正好两级。
        # 选 Treeview 而不是 Notebook：Notebook 的标签**不能分组**（只能用顺序暗示）。
        self.body = ttk.Frame(self)
        self.body.pack(expand=1, fill="both")
        self.sidebar = ttk.Frame(self.body, width=184)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)          # 固定宽度：别被长标签名撑开
        self.nav = ttk.Treeview(self.sidebar, show="tree", selectmode="browse")
        self.nav.pack(fill="both", expand=True, padx=(8, 4), pady=8)
        self.content = ttk.Frame(self.body)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.convert_tab = ConvertTab(self.content, "初排转换", self.start, self.stop,
                                      cfg=self.cfg, ai_knobs_sink=self._set_ai_knobs)
        # CheckBibTab 多两样：「入藏到引用库」按钮要写运行日志 + 后台线程回传（第 132 条）。
        self.checkbib_tab = CheckBibTab(self.content, "文献回读校验", self.start, self.stop,
                                        log=self.log, post=self.post, cfg=self.cfg)
        self.merge_tab = MergeTab(self.content, "合并文件", self.start, self.stop)
        self.proof_tab = ProofCheckTab(self.content, "一校核查", self.start, self.stop)
        self.info_tab = InfoCheckTab(self.content, "检查tex信息", self.start, self.stop)
        self.toc_tab = TocTab(self.content, "生成目录", self.start, self.stop)
        self.compile_tab = CompileAllTab(self.content, "编译全部", self.start, self.stop)
        self.clean_tab = CleanTab(self.content, "删除临时文件", self.start, self.stop)
        self.publish_tab = PublishTab(self.content, "本期出刊", self.start, self.stop)
        # ── 第 132 条：「引用库」5 页（与 mac 同口径；数据源是 Engine/bibdb.py）──
        # 页面在 bibdb_ui.py（main.py 已经 1300+ 行，别继续膨胀）；这里只建对象、排进侧栏。
        self.bibdb_stats_tab = B.BibDbStatsPage(self.content, app=self)
        self.bibdb_query_tab = B.BibDbQueryPage(self.content, app=self)
        self.bibdb_import_tab = B.BibDbImportPage(self.content, app=self)
        self.bibdb_review_tab = B.BibDbReviewPage(self.content, app=self)
        self.bibdb_settings_tab = B.BibDbSettingsPage(self.content, app=self)
        self.settings_tab = SettingsTab(self.content, self.cfg, lambda: None,
                                        post=self._q.put)
        self.tabs = [self.convert_tab, self.checkbib_tab, self.merge_tab, self.proof_tab,
                     self.info_tab, self.toc_tab, self.compile_tab, self.clean_tab,
                     self.publish_tab,
                     # 第 132 条：引用库分组（9..13）；「设置」仍是组外底部（14）
                     self.bibdb_stats_tab, self.bibdb_query_tab, self.bibdb_import_tab,
                     self.bibdb_review_tab, self.bibdb_settings_tab,
                     self.settings_tab]
        for tab in self.tabs:                     # 全部叠在同一格里，切页 = tkraise
            tab.grid(row=0, column=0, sticky="nsew")

        #: 分组 → 页（顺序就是侧栏里的顺序；`设置` 单独放在最下面，照 WinUI 的惯例）
        #: 第 132 条起多一个与另两组**平行**的顶级分组「引用库」（按使用顺序排：
        #: 看概况 → 查库 → 入藏 → 待裁决 → 设置）。设置仍是组外的配置入口。
        self.nav_groups = [("单篇稿件", (0, 1, 2, 3)),
                           ("整期装配", (4, 5, 6, 7, 8)),
                           ("引用库", (9, 10, 11, 12, 13))]
        for title, idxs in self.nav_groups:
            group = self.nav.insert("", "end", text=title, open=True)
            for i in idxs:
                self.nav.insert(group, "end", text=self.tabs[i].name, iid=f"tab:{i}")
        self.nav.insert("", "end", text="设置", iid=f"tab:{len(self.tabs) - 1}")
        self.nav.bind("<<TreeviewSelect>>", self._on_nav)
        self.nav.selection_set("tab:0")
        self.show_tab(0)

        # 键盘快捷键（UI 方案 §5）：Ctrl+R 运行当前页 / Esc 停止 / Ctrl+, 打开设置。
        # 回车不绑（侧栏/按钮各有默认行为，绑了反而与输入框冲突）。
        self.bind_all("<Control-r>", lambda _e: self._hotkey_run())
        self.bind_all("<Control-R>", lambda _e: self._hotkey_run())
        self.bind_all("<Escape>", lambda _e: self._hotkey_stop())
        self.bind_all("<Control-comma>", lambda _e: self._hotkey_settings())

        # `tk.Text` 保留：Tk 没有 ttk 对等物。颜色全部从 `theme.COLOR` 取（不写死）。
        self.output = tk.Text(self, height=18, wrap="none", font=theme.mono_font(),
                              background=theme.COLOR["text_bg"],
                              foreground=theme.COLOR["fg"],
                              insertbackground=theme.COLOR["fg"],
                              selectbackground=theme.COLOR["accent"],
                              selectforeground=theme.COLOR["on_accent"])
        self.output.pack(side="bottom", fill="both", expand=False)
        self.output.configure(state="disabled")
        # 级别色（UI 方案 §2-8：普通/琥珀/红三档；颜色**不是唯一**区分手段 ——
        # 行首本来就有 ✅/⚠/❌ 符号，色弱用户也能读）
        self.output.tag_configure("err", foreground=theme.COLOR["error"])
        self.output.tag_configure("warn", foreground=theme.COLOR["warn"])
        self.output.tag_configure("ok", foreground=theme.COLOR["ok"])
        # 灰字：空状态占位 / 辅助说明（不是"日志内容"，复制与保存都不含它）
        self.output.tag_configure("muted", foreground=theme.COLOR["muted"])
        # 空状态（观察③）：窗口刚起来、日志还空着时先给一句灰字，第一条日志到达即让位
        self._insert_placeholder()

        # 日志工具条：自动滚动（上翻看旧日志时别被强拉到底）、摘要/原始、跳到最新、
        # 复制全部、保存、清空、当前阶段
        self.log_tools = ttk.Frame(self)
        self.log_tools.pack(side="bottom", fill="x", padx=10, pady=(2, 0))
        self.v_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.log_tools, text="自动滚动", variable=self.v_autoscroll).pack(side="left")
        # U4：摘要 / 原始二选一（默认摘要）。取值必须写 **0/1**：BooleanVar 的 Tcl 值是
        # "0"/"1"，value 写成 False/True 初始态一个都选不中（2026-10-17 实测）。
        self.v_log_raw = tk.BooleanVar(value=bool(self.cfg.get("logShowRaw", False)))
        for _text, _value in (("摘要", 0), ("原始", 1)):
            ttk.Radiobutton(self.log_tools, text=_text, value=_value,
                            variable=self.v_log_raw, style="Toolbutton",
                            command=self._on_log_mode).pack(side="left", padx=(8, 0))
        self.folded_label = ttk.Label(self.log_tools, text="", style="Muted.TLabel")
        self.folded_label.pack(side="left", padx=(8, 0))
        ttk.Button(self.log_tools, text="跳到最新",
                   command=lambda: self.output.see("end")).pack(side="left", padx=6)
        ttk.Button(self.log_tools, text="复制全部", command=self._copy_log).pack(side="left")
        ttk.Button(self.log_tools, text="保存日志…", command=self._save_log).pack(
            side="left", padx=6)
        # 「清空」只清日志（内存与视图一起清，见 clear_log）—— 与 mac 的 LogPane 同一个按钮
        ttk.Button(self.log_tools, text="清空", command=self.clear_log).pack(side="left")
        self.phase_label = ttk.Label(self.log_tools, text="", style="Muted.TLabel")
        self.phase_label.pack(side="left", padx=12)

        self.log(f"{APP_TITLE}")
        self.log(E.status().describe())
        self.log("运行日志：\n")
        self.after(80, self._drain)

    # ── 侧边栏导航（UI 方案候选 B）──
    def tab_texts(self) -> list[str]:
        """全部页的名字（按侧栏顺序）—— 测试与自检用（Notebook 换成 Treeview 后的稳定接口）。"""
        return [t.name for t in self.tabs]

    def _set_ai_knobs(self, thinking: str, maxtok: str) -> None:
        """初排转换页的两个 AI 旋钮 → cfg["api"]（运行时就地读，不用先保存）。"""
        api = self.cfg.setdefault("api", {})
        api["thinking"] = thinking
        api["maxTokens"] = maxtok

    def current_tab(self):
        """当前显示的页（快捷键要操作它）。"""
        return self.tabs[self._current]

    def _hotkey_run(self) -> str:
        tab = self.current_tab()
        if isinstance(tab, TabPage) and not self._busy:
            self.start(tab)
        return "break"

    def _hotkey_stop(self) -> str:
        if self._busy:
            self.stop()
        return "break"

    def _hotkey_settings(self) -> str:
        self.show_tab(len(self.tabs) - 1)
        return "break"

    def show_tab(self, index: int) -> None:
        """切到第 `index` 页（侧栏选中态与内容一起变，并触发该页的 `on_activate`）。"""
        self._show(index)
        want = f"tab:{index}"
        if self.nav.selection() != (want,):          # 已经有就别再 set（见 _on_nav 的注释）
            self.nav.selection_set(want)

    def _show(self, index: int) -> None:
        """切页的唯一实现：更新 `_current`、tkraise、调页面钩子。

        `_current` 必须在这里同步：从前只有 `show_tab()` 写它，鼠标点侧栏走
        `_on_nav` 时 `_current` 停在旧页 —— 于是 Ctrl+R 跑的是**上一页**（第 132 条
        一并修掉）；「库概况/待裁决」也靠 `on_activate` 在切到本页时读库/刷新清单。
        """
        self._current = index
        tab = self.tabs[index]
        tab.tkraise()
        hook = getattr(tab, "on_activate", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:  # noqa: BLE001  钩子出错不许把切页弄崩
                self.log(f"❌ 切到「{tab.name}」时出错：{exc}")

    def _on_nav(self, _event=None) -> None:
        """侧栏选中 → 切内容。⚠️ **这里绝不回写** `selection_set`。

        坑：`selection_set()` 自己也会触发 `<<TreeviewSelect>>` —— 在事件处理器里回写
        就等于自我触发，Tk 的事件循环原地打转、建窗阶段直接卡死（2026-10-17 实测，
        桥接测试超时 180 s 才暴露）。所以：事件里只 `tkraise`，选中态由用户点击 /
        `show_tab()` 负责。
        """
        sel = self.nav.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("tab:"):
            return
        index = int(iid.split(":", 1)[1])
        # show_tab() 会先写 _current 再 set 选中态，事件回来时 index == _current
        # → 只 tkraise，不重复触发 on_activate（避免「库概况」一次切页读两遍库）。
        if index == self._current:
            self.tabs[index].tkraise()
        else:
            self._show(index)

    # ── 后台线程 → 主线程的唯一通道（第 132 条）──
    def post(self, item) -> None:
        """把一条队列消息交给 `_drain`（工作线程只许调它，绝不许直接碰控件）。

        `bibdb_ui.run_async` 用 `("__CALL__", callback, args)` 把「后台跑完引擎后的
        收尾」送回主线程执行 —— Tkinter 不是线程安全的（见 `_worker` 的注释）。
        """
        self._q.put(item)

    # ── 日志 ──
    @staticmethod
    def _level_tag(text: str) -> str:
        """按内容判级别（与 mac 侧同一套词表：前缀符号 + 关键中文）。
        颜色只是辅助 —— 行首符号本来就在，色弱可读。"""
        if any(k in text for k in ("❌", "程序错误", "未找到", "启动失败")):
            return "err"
        if any(k in text for k in ("⚠", "警告")):
            return "warn"
        if any(k in text for k in ("✅", "完成。")):
            return "ok"
        return ""

    def log(self, text: str) -> None:
        """追加一行日志：**永远进内存**（`_log_lines`），显示与否看当前是摘要还是原始。

        摘要判据在 `engine.is_summary_line`（与 mac 侧同一份词表 + 同一份夹具）。
        ⚠️ 判定要用**引擎原文**：下面盖相对时间戳（`+0:03.2s  `）之后，
        「`▶` 开头 / `[3/25]` 开头」这两类前缀判据就对不上了。
        """
        is_summary = E.is_summary_line(text)
        # 相对时间戳（UI 方案 §2-8）：运行中每行前面加 `+0:03.2`，方便回看"哪一步慢"；
        # 未运行时（启动信息等）不加，保持干净。绝对时间对排错没用，相对时间才有。
        if self._busy and self._t0:
            text = f"+{time.monotonic() - self._t0:5.1f}s  {text}"
        tag = self._level_tag(text)
        self._log_lines.append((text, tag, is_summary))
        if not is_summary and text.strip():
            self._log_folded += 1
        if self._trim_log():             # 超上限截断过 → 整屏重画（含这行），别让视图与内存对不上
            self._render_log()
            return
        if self._log_placeholder_active:     # 空状态让位：占位只代表「还没有任何一行」
            self._hide_placeholder()
        if self.v_log_raw.get() or is_summary:
            self.output.configure(state="normal")
            self.output.insert("end", text + "\n", tag)
            if self.v_autoscroll.get():          # 上翻看旧日志时别把人强拉到底
                self.output.see("end")
            self.output.configure(state="disabled")
        self._refresh_folded_label()

    # ── 日志：摘要 / 原始两层（U4）──
    def _render_log(self) -> None:
        """按当前模式把内存里的**完整**日志重画进日志框：摘要模式只画摘要行。

        切换模式、截断、清空都走这里 —— 除了 `log()` 的实时追加，日志框没有别的数据来源。
        """
        show_raw = bool(self.v_log_raw.get())
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        folded = 0
        for text, tag, is_summary in self._log_lines:
            if show_raw or is_summary:
                self.output.insert("end", text + "\n", tag)
            elif text.strip():
                folded += 1
        self._log_folded = folded
        self._log_placeholder_active = False
        if not self._log_lines:                    # 空状态（观察③）
            self.output.insert("end", LOG_PLACEHOLDER + "\n", "muted")
            self._log_placeholder_active = True
        self.output.configure(state="disabled")
        self._refresh_folded_label()
        if self.v_autoscroll.get():
            self.output.see("end")

    def _on_log_mode(self) -> None:
        """摘要 / 原始切换：立刻重画，并把偏好写回配置（下次启动沿用，AUDIT U4）。"""
        self.cfg["logShowRaw"] = bool(self.v_log_raw.get())
        try:
            S.save(self.cfg)
        except OSError:
            pass            # 配置目录只读时别弹窗打断看日志：本次切换照样生效
        self._render_log()

    def _refresh_folded_label(self) -> None:
        """工具条上「已折叠 N 行原始日志」：只有摘要模式且真的折叠了才写。"""
        if self.v_log_raw.get() or self._log_folded <= 0:
            self.folded_label.config(text="")
        else:
            self.folded_label.config(text=f"已折叠 {self._log_folded} 行原始日志")

    def _trim_log(self) -> bool:
        """超过上限时丢掉最旧的行（一次丢一批，避免每来一行就整屏重画）。返回是否丢了。"""
        over = len(self._log_lines) - LOG_LINE_LIMIT
        if over < 0:
            return False
        del self._log_lines[:over + LOG_TRIM_CHUNK]
        return True

    def _full_log_text(self) -> str:
        """**完整日志**文本（含摘要模式下折叠的原始行）—— 复制/保存一律用它。

        与视图模式无关：摘要只是默认视图（见 U4），留证据/发给人看时不能漏行。
        """
        return "\n".join(text for text, _tag, _is_summary in self._log_lines)

    def _insert_placeholder(self) -> None:
        """空状态（观察③）：日志区还没有任何一行时，给一句灰字，别留一片空白。"""
        self.output.configure(state="normal")
        self.output.insert("end", LOG_PLACEHOLDER + "\n", "muted")
        self.output.configure(state="disabled")
        self._log_placeholder_active = True

    def _hide_placeholder(self) -> None:
        """第一条真实日志到达时把占位删掉 —— 占位**从不**进复制/保存的内容。"""
        if not self._log_placeholder_active:
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self._log_placeholder_active = False

    def clear_log(self) -> None:
        """清空日志：**内存与视图一起清**（否则「复制全部」还会把清掉的行拿出来）。"""
        self._log_lines.clear()
        self._log_folded = 0
        self._render_log()                  # 空 → 自动恢复那行灰占位

    def _save_log(self) -> None:
        """把**整份**运行日志存成 .txt 并打开它（「打开日志文件」的可行形态：
        平时不落盘，需要留证据/发给人看时再存）。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = fd.asksaveasfilename(
            title="保存运行日志", defaultextension=".txt",
            initialfile=f"运行日志-{stamp}.txt",
            filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                # 完整日志（含摘要模式下折叠的原始行），与视图模式无关；占位不在内
                fh.write(self._full_log_text())
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"保存日志失败：{e}")
            return
        self.log(f"（日志已保存：{path}）")
        try:
            if os.name == "nt":
                os.startfile(path)                    # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", path])
        except Exception:  # noqa: BLE001  打不开也不致命（路径已经写进日志）
            pass

    def _copy_log(self) -> None:
        """复制**完整日志**（含摘要模式下折叠的原始行）—— 与当前视图模式无关。"""
        try:
            self.clipboard_clear()
            self.clipboard_append(self._full_log_text())
            self.log("（已复制全部运行日志到剪贴板）")
        except tk.TclError:
            pass

    def _drain(self) -> None:
        """把工作线程放进队列的日志搬到界面上（Tkinter 不是线程安全的）。"""
        try:
            while True:
                line = self._q.get_nowait()
                if line == "__DONE__":          # 内部信号，不是日志
                    self._proc = None
                    self._running_tab = None
                    self._set_busy(False)
                    continue
                if isinstance(line, tuple) and line[0] == "__REPORT__":
                    _tag, tab, payload = line
                    if isinstance(tab, InfoCheckTab):
                        tab.show_report(payload)
                    continue
                if isinstance(line, tuple) and line[0] == "__KEYSTATUS__":
                    self.settings_tab.key_status.config(text=str(line[1]))
                    continue
                if isinstance(line, tuple) and line[0] == "__CALL__":
                    # 第 132 条：引用库页面在后台线程跑完引擎后，把「结果回填」送回主线程。
                    # 回调里只碰控件；它自己抛的异常在这里兜住，别让 Tk 的事件循环断掉。
                    _tag, callback, args = line
                    try:
                        callback(*args)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"❌ 界面回调出错：{exc}")
                    continue
                if isinstance(line, tuple) and line[0] == "__RESULT__":
                    _tag, tab, text, path, seconds, undo = line
                    if isinstance(undo, tuple) and len(undo) == 2:
                        _label, trash_dir = undo
                        tab.show_result(text, path, seconds,
                                        undo=(_label, lambda d=trash_dir: self.start(
                                            tab, E.cleantemp_call(folder=tab.path(),
                                                                  restore_trash=d))))
                    else:
                        tab.show_result(text, path, seconds)
                    continue
                if isinstance(line, tuple) and line[0] == "__COOP__":
                    # 第五十五批：两段式协同跑完后，由工作线程把「发现到的暂存目录/
                    # 建议摘要」送回主线程再更新界面（Tk 控件只在主线程碰）。
                    _tag, tab, payload, code = line
                    try:
                        tab.apply_coop_result(payload, code)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"❌ 协同状态界面更新失败：{exc}")
                    continue
                self.log(line)
                if self._busy:
                    self._phase = line.strip()          # 当前阶段 = 最新一行（引擎的阶段行）
        except queue.Empty:
            pass
        # 秒表 + 当前阶段：运行中每 80ms 刷一次（进度"在动"要看得见 —— UI 方案第 2 件）
        if self._busy and self._running_tab is not None:
            self._running_tab.show_running(time.monotonic() - self._t0, self._phase)
        self.after(80, self._drain)

    # ── 运行 ──
    @staticmethod
    def _info_report_payload(folder: str):
        """跑完「检查tex信息」后，把最新的那份 `信息检查*.json` 读回来喂表格。

        为什么读文件而不是把 `--preview` 的 stdout 接回来：这一趟**必须**不带
        `--preview`（要落报告给「生成目录」用），所以结构化结果就在它写的 json 里。
        """
        try:
            files = [f for f in os.listdir(folder)
                     if f.startswith("信息检查") and f.endswith(".json")]
        except OSError:
            return None
        if not files:
            return None
        newest = max((os.path.join(folder, f) for f in files), key=lambda q: os.path.getmtime(q))
        try:
            with open(newest, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _call_for(self, tab) -> E.EngineCall:
        path = tab.path()
        if not path:
            raise ValueError("请先选路径")
        opts = tab.extra_args()
        if isinstance(tab, ConvertTab):
            out = fd.askdirectory(title="选择输出根目录（产物写到 <输出目录>/<作者名>/）")
            if not out:
                raise ValueError("没有选输出目录")
            if tab.coop_enabled():
                # 第五十五批：两段式第一段。**记住输出根** —— 第二段复用，不再弹目录选择。
                tab.set_coop_out(out)
                return tab.coop_ph1_call(out)
            return E.convert_call(source=path, out=out, **opts)
        if isinstance(tab, InfoCheckTab):
            # ⚠️ 不带 `--preview`：这一次跑还要把《信息检查…txt/.json》落到目录里
            #    （「生成目录」页要读它）。表格由跑完之后的 `__REPORT__` 那条路填
            #    —— 从前这里 0 处调用 `show_report()`，于是表格永远「（还没检查）」，
            #    而引擎那一轮其实跑得好好的（2026-10-17 审计实测，H2）。
            return E.infocheck_call(folder=path)
        if isinstance(tab, TocTab):
            tab.save_settings(path)          # 先把本期设置写进引擎要读的 json
            return E.toc_call(report=path, **opts)
        if isinstance(tab, ProofCheckTab):
            return E.proofcheck_call(tex=path, **opts)
        if isinstance(tab, CompileAllTab):
            return E.compileall_call(folder=path, **opts)
        if isinstance(tab, PublishTab):
            return E.publish_call(folder=path)
        if isinstance(tab, CleanTab):
            # UI 方案第 3 件：**先预览再动手**，且动手的方式是"移到回收目录"（可撤销）。
            # 老行为（直接删）仍保留在 CLI 默认值里，mac 外壳不受影响。
            if not tab.v_dry.get():
                tab.confirm_trash()          # 取消 → ValueError（什么都没动）
            return E.cleantemp_call(folder=path, trash=True)
        if isinstance(tab, CheckBibTab):
            merge_into = ""
            if opts.get("merge_into_same_dir"):
                target = os.path.join(os.path.dirname(path), "reference.bib")
                if not os.path.exists(target):
                    raise ValueError(f"同目录下没有 reference.bib：{target}")
                merge_into = target
            return E.checkbib_call(returned=path, merge_into=merge_into,
                                   original=opts.get("original", ""),
                                   tex=opts.get("tex", ""), out=opts.get("out", ""))
        return E.merge_call(folder=path, **opts)

    def start(self, tab, call: E.EngineCall | None = None) -> None:
        if self._busy:
            messagebox.showinfo(APP_TITLE, "上一个任务还在跑，请等它结束或点「停止」。")
            return
        try:
            call = call or self._call_for(tab)
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        except Exception as e:  # noqa: BLE001
            # ⚠️ 从前只捕 ValueError/RuntimeError：报告目录只读/被占用时（Windows 上
            #    PermissionError 高发）异常从 `_call_for`（`save_settings` 写 json）直接冒出，
            #    **点「运行」毫无反应**；发布形态还是 `--noconsole`，连 traceback 都看不见
            #    （2026-10-17 审计实测，M4）。
            messagebox.showerror(APP_TITLE, f"准备运行时就出错了：\n{e}")
            self.log(f"❌ 准备运行失败：{e}")
            return
        self.log(f"\n▶ {call.label}：{call.display}")
        self._running_tab = tab
        self._t0 = time.monotonic()
        tab.show_running(0.0)
        self._set_busy(True)
        # ⚠️ 路径必须在**主线程**里取好再交给工作线程：Tk 控件不能跨线程碰 ——
        #    在工作线程里调 `tab.path()`（读 Entry）会 `RuntimeError: main thread is not
        #    in main loop`，worker 当场死掉 ⇒ 界面永远卡在「运行中」。
        #    这是我自己第一版 H2 修法踩的坑，靠**真驱动一次界面**才抓出来（2026-10-17）。
        path = tab.path()
        threading.Thread(target=self._worker, args=(call, tab, path), daemon=True).start()

    @staticmethod
    def _argv_value(argv: list[str], flag: str) -> str:
        """取命令行里 `flag` 后面的值（没有就返回空串；不猜、不抛）。"""
        try:
            index = argv.index(flag)
        except ValueError:
            return ""
        return argv[index + 1] if index + 1 < len(argv) else ""

    @staticmethod
    def _coop_payload(call: E.EngineCall, phase: str, code: int) -> dict:
        """跑完两段式的一步后，把界面要的状态从**引擎写的状态文件**里读出来。

        发现规则与引擎同一口径（`coop.py: discover_stage`：source + out_root 都匹配）。
        第一段失败但暂存目录已写（如编译失败退出 2）时仍返回 stage —— 界面会显示
        「失败」但允许打开任务书/暂存目录，**不把暂存目录当交付目录**。
        """
        out = MainWindow._argv_value(call.argv, "--out")
        source = MainWindow._argv_value(call.argv, "--source")
        stage_dir = MainWindow._argv_value(call.argv, "--coop-stage")
        stage = C.load_stage(stage_dir) if stage_dir else None
        if stage is None:
            stage = C.discover_stage(out, source=source)
        payload: dict = {"phase": phase, "stage": stage}
        if stage is not None and phase == "ph2" and code == 0:
            # 批56：成功摘要以引擎写进状态文件的 stats 为准（旧状态回退 record/keys）；
            # 0 文献回填的橙色警告也在这里算（只对 done 的暂存目录有意义）。
            payload["summary"] = C.summarize_stage(stage)
            payload["suggestions"] = C.suggestions_summary(stage.stage_dir, stage.stem)
        if stage is None:
            payload["error"] = ("没有发现暂存目录（第一段可能没跑成，见上面的日志）"
                                if phase == "ph1"
                                else "暂存目录不见了（可能已被移动/删除）")
        return payload

    def _worker(self, call: E.EngineCall, tab=None, path: str = "") -> None:
        last = {"line": ""}
        tail: list[str] = []                 # 失败时给界面看「日志尾部」（只留最后几行）

        def _on_line(line: str) -> None:
            self._q.put(line)
            if line.strip():
                last["line"] = line          # 契约行 = stdout 最后一行（ENGINE_API 五）
                tail.append(line)
                if len(tail) > 8:
                    del tail[0]

        # 第五十五批：两段式协同**绝不**把 API 配置传给子进程（外部 chat 通道零上传/
        # 零费用；与 mac `CoopFeature.prepare` 强制 useAI=false 同一口径）。
        coop_phase = ""
        if isinstance(tab, ConvertTab):
            if "--coop-taskbook-only" in call.argv:
                coop_phase = "ph1"
            elif "--coop-replies" in call.argv:
                coop_phase = "ph2"
        try:
            code = E.run_call(call, on_line=_on_line,
                              api=None if coop_phase else self.cfg.get("api"),
                              on_proc=lambda p: setattr(self, "_proc", p),
                              notes=self.cfg.get("notesRepoPath", ""))
        except Exception as e:  # noqa: BLE001
            self._q.put(f"❌ 启动失败：{e}")
            code = -1
        if code == 0 and isinstance(tab, InfoCheckTab) and path:
            payload = self._info_report_payload(path)
            if payload:
                self._q.put(("__REPORT__", tab, payload))
        coop_payload: dict | None = None
        result = E.result_text(call.entry, code)
        if coop_phase:
            try:
                coop_payload = self._coop_payload(call, coop_phase, code)
            except Exception as e:  # noqa: BLE001  读状态失败不许把 worker 弄死
                coop_payload = {"phase": coop_phase, "stage": None,
                                "error": f"读取协同状态失败：{e}"}
            if code != 0:
                detail = E.output_tail(tail, count=2)
                coop_payload["error"] = (f"退出码 {code}｜{detail}" if detail
                                         else f"退出码 {code}")
            elif coop_payload.get("stage") is not None:
                result = ("✅ 初排完成 · 待人工核实（未交付）" if coop_phase == "ph1"
                          else "✅ 已交付")
        # ⚠️ 退出码 1 在引擎里常常是「有结果」而不是失败（INVARIANTS 三.2）：
        #    checkbib 并回 / proofcheck 有命中 / compileall 有失败 / toc 只出一份。
        #    从前一律写「❌ 失败（退出码 1）」，与上一行引擎自己打的「完成。…」自相矛盾
        #    （2026-10-17 审计实测，H3）。文案与 mac 各页逐字对齐，且在 bridge 里有守卫。
        self._q.put(f"{result}（退出码 {code}）")
        if tab is not None:
            path = E.artifact_path(last["line"]) if code == 0 else ""
            undo = None
            # 清理跑完 → 给一个「撤销清理」按钮：把回收目录里的东西搬回去
            if isinstance(tab, CleanTab) and code == 0 and path and "--trash" in call.argv:
                undo = ("撤销清理", path)
            self._q.put(("__RESULT__", tab, result,
                         path, time.monotonic() - self._t0, undo))
            if coop_payload is not None:
                self._q.put(("__COOP__", tab, coop_payload, code))
        self._q.put("__DONE__")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for tab in (self.convert_tab, self.checkbib_tab, self.merge_tab,
                    self.proof_tab, self.info_tab, self.toc_tab,
                    self.compile_tab, self.clean_tab):
            tab.run_button.config(state="disabled" if busy else "normal")
            tab.stop_button.config(state="normal" if busy else "disabled")

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            # ⚠️ 只 `terminate()` 直接子进程是不够的：发布形态里那只是 PyInstaller 的
            #    bootloader，引擎与它起的 xelatex 都是孙进程 —— 实测按下停止后 xelatex
            #    又跑了 ~12 秒并写出了 PDF（2026-10-17 审计，M3/W1）。
            #    忙状态也**不在这里解除**：等 worker 读到管道结束（`__DONE__`）再解除，
            #    否则会出现「界面已不忙、任务还在写产物目录」，编辑再点一次就有两组 TeX 同写。
            E.kill_tree(proc)
            self.log("⏹ 已请求停止（连同它起的 TeX 等子进程一起停；等日志收尾后按钮会恢复）")
        else:
            self._set_busy(False)


def _enable_dpi_awareness() -> None:
    """Windows 高分屏：声明 **System DPI Aware**。

    不做的话系统把整个窗口当位图拉伸 → 高分屏上**发虚**（Microsoft 文档原话：
    "Windows will stretch the application bitmap… appearing blurry"）。
    为什么只做 System 级、不做 Per-Monitor V2：PMv2 要求应用自己响应 DPI 变化，
    Tkinter 不做（UIUX-PROPOSAL §6 的取舍：办公室一台机器足够）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 1 = System DPI Aware
    except Exception:  # noqa: BLE001  老系统没有 shcore，退回旧 API
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001  失败也不致命（只是可能发虚）
            pass


def main() -> int:
    _enable_dpi_awareness()
    app = MainWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
