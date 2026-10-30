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

import theme


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


class DiagnosisCard(ttk.Frame):
    """失败诊断卡（B-285b）：红卡 = 失败；中性卡 = 有结果但不是程序出错。

    三件事都**在本地完成**：打开日志（应用缓存里的文件）、复制诊断包（纯文本）、
    查看文档（契约/使用说明）。卡里只放引擎给的类别标题、人话原因、2–4 步下一步，
    与可折叠的日志尾部证据 —— 判据一条都不在 Tk 里。
    """

    def __init__(self, parent, *, on_open_log, on_copy_pack, on_open_doc):
        super().__init__(parent)
        self._on_open_log = on_open_log
        self._on_copy_pack = on_copy_pack
        self._on_open_doc = on_open_doc
        self._log_path = ""
        self._pack = ""
        self._evidence_shown = False
        self.columnconfigure(0, weight=1)

        self.title_label = ttk.Label(self, text="", font=("TkDefaultFont", 10, "bold"),
                                     wraplength=740, justify="left")
        self.title_label.grid(row=0, column=0, sticky="w")
        self.meta_label = ttk.Label(self, text="", foreground=theme.COLOR["muted"],
                                    wraplength=740, justify="left")
        self.meta_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.reason_label = ttk.Label(self, text="", wraplength=740, justify="left")
        self.reason_label.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.steps_label = ttk.Label(self, text="", wraplength=740, justify="left")
        self.steps_label.grid(row=3, column=0, sticky="w", pady=(4, 0))

        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.log_btn = ttk.Button(btns, text="打开日志", width=10, command=self._open_log)
        self.log_btn.pack(side="left")
        self.copy_btn = ttk.Button(btns, text="复制诊断包", width=12, command=self._copy_pack)
        self.copy_btn.pack(side="left", padx=6)
        self.doc_btn = ttk.Button(btns, text="查看文档", width=10, command=self._open_doc)
        self.doc_btn.pack(side="left")
        self.evidence_btn = ttk.Button(btns, text="显示日志证据", width=12,
                                       command=self.toggle_evidence)
        self.evidence_btn.pack(side="left", padx=(6, 0))
        self.status_label = ttk.Label(btns, text="", foreground=theme.COLOR["ok"])
        self.status_label.pack(side="left", padx=(8, 0))

        self.evidence_label = ttk.Label(self, text="", wraplength=740, justify="left",
                                        font=("TkFixedFont", 9))
        self.evidence_label.grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.evidence_label.grid_remove()

    # ── 显示 ────────────────────────────────────────────────────────────────
    def show(self, *, level: str, title: str, meta: str, reason: str,
             steps, evidence, log_path: str, pack: str) -> None:
        prefix = "❌" if level == "error" else "⚠️"
        self.title_label.config(text=f"{prefix} {title}",
                                foreground=theme.COLOR["error"] if level == "error" else theme.COLOR["warn"])
        self.meta_label.config(text=meta)
        self.reason_label.config(text=reason)
        step_text = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        self.steps_label.config(text=("下一步：\n" + step_text) if step_text else "")
        lines = [str(ln).rstrip() for ln in evidence if str(ln).strip()][-10:]
        self.evidence_label.config(text="\n".join(lines) or "（本次没有捕获到日志行）")
        self._log_path = log_path or ""
        self._pack = pack or ""
        self.log_btn.config(state="normal" if self._log_path else "disabled")
        self.copy_btn.config(state="normal" if self._pack else "disabled")
        self.status_label.config(text="")
        self._evidence_shown = False
        self.evidence_label.grid_remove()
        self.evidence_btn.config(text="显示日志证据")
        self.grid()

    def hide(self) -> None:
        self._log_path = ""
        self._pack = ""
        self.grid_remove()

    def toggle_evidence(self) -> None:
        self._evidence_shown = not self._evidence_shown
        if self._evidence_shown:
            self.evidence_label.grid()
            self.evidence_btn.config(text="隐藏日志证据")
        else:
            self.evidence_label.grid_remove()
            self.evidence_btn.config(text="显示日志证据")

    # ── 三个按钮 ────────────────────────────────────────────────────────────
    def _open_log(self) -> None:
        if self._log_path:
            self._on_open_log(self._log_path)

    def _copy_pack(self) -> None:
        if not self._pack:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._pack)
            if self._on_copy_pack:
                self._on_copy_pack(self._pack)
            self.status_label.config(text="已复制诊断包（本地文本，不会上传）")
        except tk.TclError as e:
            self.status_label.config(text=f"复制失败：{e}")

    def _open_doc(self) -> None:
        if self._on_open_doc:
            self._on_open_doc()


class TabPage(ttk.Frame):
    """一页 = 一个路径输入 + 浏览 + 若干选项 + 运行。子类只填 `instruction` 与选项。"""

    instruction = ""
    pick = "dir"          # 'dir' | 'file'
    #: 页内长标题（可选）。批62：侧栏保持短名（185px 宽带不动长串），
    #: 「参考文献规范化」这类页的定位长标题在**正文顶部**显示 —— 与 mac 同做法。
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

        # ── 失败诊断卡（B-285b）──
        # 红卡：转换 exit 2 / 信息检查 exit 2 / 全编译 exit 1 / 启动失败 <0；
        # 中性卡：一校核查、文献回读 exit 1；exit 0 不出卡。判据在 engine.diagnosis_level。
        self.diagnosis_card = DiagnosisCard(
            self,
            on_open_log=self._open_diagnosis_log,
            on_copy_pack=self._log_diagnosis_copied,
            on_open_doc=self._open_diagnosis_doc,
        )
        self.diagnosis_card.grid(row=row, column=0, padx=10, pady=(0, 6), sticky="ew"); row += 1
        self.diagnosis_card.grid_remove()
        self._diagnosis_log_path = ""

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

    # ── 失败诊断卡 ─────────────────────────────────────────────────────────
    def show_diagnosis(self, level: str, report: dict | None, log_path: str,
                       pack: str, log_tail=()) -> None:
        """把引擎的 diagnose.v1（或回退信息）画成卡；`report=None` = 旧引擎回退。"""
        report = report or {}
        evidence = ((report.get("evidence") or {}).get("log_tail")
                    if isinstance(report.get("evidence"), dict) else None) or list(log_tail)
        title = report.get("title") or (
            "本次运行失败" if level == "error" else "本次运行有需要处理的结果")
        category = report.get("category") or "old-engine"
        exit_code = report.get("exit_code", "?")
        meta = f"退出码 {exit_code}｜类别 {category}"
        if report.get("confidence") is not None:
            meta += f"｜置信度 {report.get('confidence')}"
        reason = report.get("reason") or (
            "诊断器不可用（旧版引擎没有 diagnose.py，或诊断本身失败）："
            "先按退出码与下面的日志尾部排查；升级后会有完整诊断。")
        steps = report.get("next_steps") or [
            "看「日志证据」里最后几行的报错，按提示修正输入或环境后重跑。",
            "仍失败就点「复制诊断包」，把这份本地文本发给维护者。",
            "需要完整契约时点「查看文档」看 ENGINE_API.md §十一。",
        ]
        steps = [str(s) for s in steps][:4]
        self._diagnosis_log_path = log_path or ""
        self.diagnosis_card.show(level=level, title=title, meta=meta, reason=reason,
                                 steps=steps, evidence=evidence,
                                 log_path=self._diagnosis_log_path, pack=pack)
        self.diagnosis_card.grid()

    def hide_diagnosis(self) -> None:
        self._diagnosis_log_path = ""
        self.diagnosis_card.hide()

    def _log_diagnosis_copied(self, _pack: str) -> None:
        # 有的页面把 log 方法挂在窗口上；没有也绝不能因一行提示崩掉。
        log = getattr(self, "log", None)
        if callable(log):
            log("（已复制诊断包：本地文本，未上传任何地方）")

    def _open_diagnosis_log(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)                    # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", path])
        except Exception as e:                        # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打不开诊断日志：\n{path}\n\n{e}")

    def _open_diagnosis_doc(self) -> None:
        try:
            import engine as _E
            doc = _E.diagnosis_doc_path(_E.slcn_engine())
        except Exception:                             # noqa: BLE001
            doc = None
        if not doc:
            messagebox.showinfo(APP_TITLE, "没找到文档。\n可在仓库根看：使用说明.md、Engine/docs/ENGINE_API.md。")
            return
        try:
            if os.name == "nt":
                os.startfile(doc)                     # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", doc])
        except Exception as e:                        # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打不开文档：\n{doc}\n\n{e}")

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
