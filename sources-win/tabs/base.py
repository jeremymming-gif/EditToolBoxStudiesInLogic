"""Windows 壳的公共底座（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。

`TabPage` 基类 + 路径/文案小工具 + 日志上限常量 + 勾选框工厂。
`tabs/__init__.py` 负责把 `sources-win/` 放上 `sys.path`（幂等），
保证单独 `import tabs.convert` 时也能找到 `engine.py`/`settings.py` 等兄弟模块。
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk


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
    #: 页内长标题（可选）。批62：侧栏保持短名（185px 宽带不动长串），
    #: 「文献回读校验」这类页的定位长标题在**正文顶部**显示 —— 与 mac 同做法。
    page_title = ""

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent)
        self.name = name
        self.on_run = on_run
        self.on_stop = on_stop
        self.columnconfigure(0, weight=1)
        row = 0

        if self.page_title:
            ttk.Label(self, text=self.page_title, style="Heading.TLabel").grid(
                row=row, column=0, padx=10, pady=(10, 2), sticky="w"); row += 1

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
