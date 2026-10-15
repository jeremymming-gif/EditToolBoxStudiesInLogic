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

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E  # noqa: E402
import settings as S  # noqa: E402

APP_TITLE = "编辑部工具箱（Windows）"


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

        self.entry = tk.Entry(self, exportselection=False)
        self.entry.grid(row=row, column=0, padx=10, sticky="ew"); row += 1

        btns = ttk.Frame(self)
        btns.grid(row=row, column=0, padx=10, pady=6, sticky="w"); row += 1
        tk.Button(btns, text="浏览", width=10, command=self.browse).pack(side="left")
        self.run_button = tk.Button(btns, text="运行", width=10,
                                    command=lambda: self.on_run(self))
        self.run_button.pack(side="left", padx=6)
        self.stop_button = tk.Button(btns, text="停止", width=10, state="disabled",
                                     command=self.on_stop)
        self.stop_button.pack(side="left")

        self.options = ttk.Frame(self)
        self.options.grid(row=row, column=0, padx=10, sticky="w"); row += 1
        self.build_options(self.options)

        if self.instruction:
            ttk.Label(self, text=self.instruction, wraplength=760,
                      justify="left").grid(row=row, column=0, padx=10, pady=(2, 10), sticky="w")

    # 子类可覆盖
    def build_options(self, parent: ttk.Frame) -> None:
        pass

    def extra_args(self) -> dict:
        return {}

    def browse(self) -> None:
        path = fd.askdirectory() if self.pick == "dir" else fd.askopenfilename()
        if path:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, path)

    def path(self) -> str:
        return self.entry.get().strip().strip('"')


def _check(parent: tk.Widget, text: str, var: tk.BooleanVar) -> tk.Checkbutton:
    return tk.Checkbutton(parent, text=text, variable=var, anchor="w")


class ConvertTab(TabPage):
    name = "初排转换"
    pick = "file"
    instruction = ("使用说明：选作者的稿件（.docx / .doc / .tex），再选一个输出根目录，"
                   "产物会写到「输出目录/<作者名>/」。\n"
                   "勾了「出文献核实任务书」会同时生成《…_文献核实.md》；"
                   "联网核实需要先在「设置」里填好 API 配置。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_compile = tk.BooleanVar(value=True)
        self.v_clean = tk.BooleanVar(value=False)
        self.v_refs = tk.BooleanVar(value=True)
        self.v_srcpdf = tk.BooleanVar(value=False)
        _check(parent, "编译 PDF", self.v_compile).pack(side="left")
        _check(parent, "清理临时文件", self.v_clean).pack(side="left")
        _check(parent, "出文献核实任务书", self.v_refs).pack(side="left")
        _check(parent, "导出原稿 PDF", self.v_srcpdf).pack(side="left")

    def extra_args(self) -> dict:
        return {"compile_pdf": self.v_compile.get(), "clean": self.v_clean.get(),
                "verify_refs": self.v_refs.get(), "source_pdf": self.v_srcpdf.get()}


class CheckBibTab(TabPage):
    name = "文献回读校验"
    pick = "file"
    instruction = ("使用说明：选编辑改完交回的 .bib（或产物目录里的 reference.bib）。\n"
                   "勾了「并回产物」会把交回的条目并进产物的 reference.bib —— "
                   "并入前引擎会自动留一份 `reference.bib.ai前` 备份。")

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_merge = tk.BooleanVar(value=False)
        _check(parent, "并回产物 reference.bib（合并前自动备份 .ai前）", self.v_merge).pack(side="left")

    def extra_args(self) -> dict:
        return {"merge_into_same_dir": self.v_merge.get()}


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

    def extra_args(self) -> dict:
        return {"editors_page": self.v_editors.get(), "back_cover": self.v_back.get(),
                "nest_outlines": self.v_nest.get()}


class SettingsTab(ttk.Frame):
    """设置：引擎状态 + AI 配置（密钥只落本地，显示打码）。"""

    name = "设置"

    def __init__(self, parent, cfg: dict, on_save):
        super().__init__(parent)
        self.cfg = cfg
        self.on_save = on_save
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
            ("检索工具名/路径", "searchTool", "留空 = 用引擎默认；本刊目前不依赖联网检索"),
        ):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=3, sticky="e")
            var = tk.StringVar(value=str(cfg.get("api", {}).get(key, "") or cfg.get(key, "")))
            self.vars[key] = var
            show = "•" if key == "key" else ""
            tk.Entry(self, textvariable=var, width=52, show=show).grid(
                row=row, column=1, padx=10, pady=3, sticky="ew")
            ttk.Label(self, text=hint, foreground="#666").grid(
                row=row + 1, column=1, padx=10, sticky="w")
            row += 2

        self.v_thinking = tk.StringVar(value=str(cfg.get("api", {}).get("thinking", "")))
        self.v_maxtok = tk.StringVar(value=str(cfg.get("api", {}).get("maxTokens", "")))
        self.v_web = tk.BooleanVar(value=bool(cfg.get("api", {}).get("webSearch", False)))
        opts = ttk.Frame(self)
        opts.grid(row=row, column=1, padx=10, pady=6, sticky="w"); row += 1
        ttk.Label(opts, text="思考模式").pack(side="left")
        tk.Entry(opts, textvariable=self.v_thinking, width=18).pack(side="left", padx=(4, 12))
        ttk.Label(opts, text="最长输出").pack(side="left")
        tk.Entry(opts, textvariable=self.v_maxtok, width=10).pack(side="left", padx=(4, 12))
        _check(opts, "允许联网检索", self.v_web).pack(side="left")

        tk.Button(self, text="保存设置", width=14, command=self.save).grid(
            row=row, column=1, padx=10, pady=10, sticky="w")
        self.refresh_status()

    def refresh_status(self) -> None:
        st = E.status()
        text = st.describe()
        if st.problems:
            text += "\n" + "\n".join("· " + p for p in st.problems)
        text += "\n（这一页改了引擎目录后请重开本程序）"
        self.status_label.config(text=text)

    def save(self) -> None:
        for key, var in self.vars.items():
            if key == "engineDir":
                self.cfg["engineDir"] = var.get().strip()
            else:
                self.cfg.setdefault("api", {})[key] = var.get().strip()
        self.cfg.setdefault("api", {})["thinking"] = self.v_thinking.get().strip()
        self.cfg.setdefault("api", {})["maxTokens"] = self.v_maxtok.get().strip()
        self.cfg["api"]["webSearch"] = bool(self.v_web.get())
        try:
            path = S.save(self.cfg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"保存失败：{e}")
            return
        messagebox.showinfo(APP_TITLE, f"已保存到：\n{path}")


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.geometry("820x640+400+120")
        self.title(APP_TITLE)
        self.cfg = S.load()
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[str] = queue.Queue()
        self._busy = False

        self.tab_control = nb = ttk.Notebook(self)
        self.convert_tab = ConvertTab(nb, "初排转换", self.start, self.stop)
        self.checkbib_tab = CheckBibTab(nb, "文献回读校验", self.start, self.stop)
        self.merge_tab = MergeTab(nb, "合并文件", self.start, self.stop)
        self.settings_tab = SettingsTab(nb, self.cfg, lambda: None)
        for tab in (self.convert_tab, self.checkbib_tab, self.merge_tab, self.settings_tab):
            nb.add(tab, text=tab.name)
        nb.pack(expand=1, fill="both")

        self.output = tk.Text(self, height=18, wrap="none")
        self.output.pack(side="bottom", fill="both", expand=False)
        self.output.configure(state="disabled")

        self.log(f"{APP_TITLE}")
        self.log(E.status().describe())
        self.log("运行日志：\n")
        self.after(80, self._drain)

    # ── 日志 ──
    def log(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _drain(self) -> None:
        """把工作线程放进队列的日志搬到界面上（Tkinter 不是线程安全的）。"""
        try:
            while True:
                line = self._q.get_nowait()
                if line == "__DONE__":          # 内部信号，不是日志
                    self._proc = None
                    self._set_busy(False)
                    continue
                self.log(line)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    # ── 运行 ──
    def _call_for(self, tab) -> E.EngineCall:
        path = tab.path()
        if not path:
            raise ValueError("请先选路径")
        opts = tab.extra_args()
        if isinstance(tab, ConvertTab):
            out = fd.askdirectory(title="选择输出根目录（产物写到 <输出目录>/<作者名>/）")
            if not out:
                raise ValueError("没有选输出目录")
            return E.convert_call(source=path, out=out, **opts)
        if isinstance(tab, CheckBibTab):
            merge_into = ""
            if opts.get("merge_into_same_dir"):
                target = os.path.join(os.path.dirname(path), "reference.bib")
                if not os.path.exists(target):
                    raise ValueError(f"同目录下没有 reference.bib：{target}")
                merge_into = target
            return E.checkbib_call(returned=path, merge_into=merge_into)
        return E.merge_call(folder=path, **opts)

    def start(self, tab) -> None:
        if self._busy:
            messagebox.showinfo(APP_TITLE, "上一个任务还在跑，请等它结束或点「停止」。")
            return
        try:
            call = self._call_for(tab)
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.log(f"\n▶ {call.label}：{call.display}")
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(call,), daemon=True).start()

    def _worker(self, call: E.EngineCall) -> None:
        try:
            code = E.run_call(call, on_line=self._q.put, api=self.cfg.get("api"),
                              on_proc=lambda p: setattr(self, "_proc", p))
        except Exception as e:  # noqa: BLE001
            self._q.put(f"❌ 启动失败：{e}")
            code = -1
        self._q.put(f"{'✅ 完成' if code == 0 else '❌ 失败'}（退出码 {code}）")
        self._q.put("__DONE__")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for tab in (self.convert_tab, self.checkbib_tab, self.merge_tab):
            tab.run_button.config(state="disabled" if busy else "normal")
            tab.stop_button.config(state="normal" if busy else "disabled")

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            self.log("⏹ 已请求停止（引擎会在当前步骤结束后退出）")
        self._set_busy(False)


def main() -> int:
    app = MainWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
