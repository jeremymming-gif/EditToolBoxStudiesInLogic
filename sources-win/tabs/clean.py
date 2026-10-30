"""「删除临时文件」页（`CleanTab`）。B-230 从 `main.py` 搬出；B-281 与 mac 三种清理模式对齐。

与 mac `DeleteTempFeature` 同口径（模式、argv、确认文案、撤销条件都在 `clean_logic.py`，
本文件只负责「取值 → 调引擎 → 显示」）：
  · 三种模式下拉：编译临时文件（默认）/ 交付收尾（白名单）/ 工作区旧件；
  · 点「运行」：未勾「先预演」时先跑一遍**预演**，把具体清单摆出来再确认（可撤销）；
    勾了「先预演」= **只预演不写**（不会移走任何文件）；
  · 移入回收站成功后，横幅上出现「撤销清理」（从契约行「回收目录：…」取路径）；
  · 「清空回收站…」= 永久删除回收站（确认「不可撤销」；成功后**不给**撤销按钮）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import clean_logic as CL
import engine as E
from .base import TabPage, _check, plain


class CleanTab(TabPage):
    name = "删除临时文件"
    pick = "dir"
    instruction = ("使用说明：选**一期的目录**（或任意目录），再选清理模式：\n"
                   "**编译临时文件**（默认）：递归清 .aux/.bbl/.bcf/.blg/.out/.run.xml/"
                   ".synctex.gz/.DS_Store；\n"
                   "**交付收尾（白名单）**：清交付残留，保留白名单与 tex 引用闭包；\n"
                   "**工作区旧件**：每期只留最新一份「合并*.pdf」，固定交付物不动。\n"
                   "**不会动** .tex / .pdf / .sty / .bib / .log。清理结果先移入 .toolbox-trash/"
                   "（可撤销）；\n"
                   "「清空回收站…」是永久删除（会再确认一次，不可撤销）。")

    def build_options(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(side="top", anchor="w")
        ttk.Label(row, text="清理模式：").pack(side="left")
        self.v_mode = tk.StringVar(value=CL.mode_title(CL.DEFAULT_MODE))
        self.mode_box = ttk.Combobox(row, textvariable=self.v_mode, state="readonly",
                                     values=CL.mode_labels(), width=34)
        self.mode_box.pack(side="left")
        self.mode_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_mode())
        self.v_dry = tk.BooleanVar(value=True)
        _check(row, "先预演（只列出会删哪些，不真删）", self.v_dry).pack(side="left", padx=(12, 0))

        self.mode_detail = ttk.Label(parent, text="", wraplength=760, justify="left")
        self.mode_detail.pack(side="top", anchor="w", pady=(4, 0))

        self.empty_btn = ttk.Button(parent, text="清空回收站…", width=14,
                                    command=self.confirm_empty_trash)
        self.empty_btn.pack(side="top", anchor="w", pady=(6, 0))
        self._refresh_mode()

    def extra_args(self) -> dict:
        return {}

    def mode(self) -> str:
        """当前下拉框选中的模式（raw value：aux / delivery / workspace）。"""
        return CL.mode_key(self.v_mode.get())

    def _refresh_mode(self) -> None:
        self.mode_detail.config(text=plain(CL.mode_detail(self.mode())))

    def confirm_trash(self, mode: str | None = None) -> None:
        """点「运行」时（未勾「先预演」）：先预演一遍，把**具体清单与大小**摆给编辑看，
        确认后才移入回收目录。

        依据（`UIUX-PROPOSAL.md` 第 4 节）：确认框只用于后果严重的动作，且必须**具体**
        （删什么/多大/不碰什么），按钮文案是动词；能撤销就别用红色 destructive。
        三种模式的标题/正文与 mac `DeleteTempFeature.confirm(folder:mode:)` 同义
        （在 `clean_logic.confirm_text`，两侧由 (k) 组守着）。
        用户取消 → 抛 ValueError，走现有的"已取消"提示，**一个文件都不动**。
        """
        folder = self.path()
        if not folder:
            raise ValueError("请先选目录")
        mode = CL.normalize_mode(mode or self.mode())
        if mode == CL.DEFAULT_MODE:
            call = E.cleantemp_call(folder=folder, dry_run=True, trash=True)
        else:
            call = E.cleantemp_call(folder=folder, mode=mode, dry_run=True)
        lines: list[str] = []
        if E.run_call(call, on_line=lines.append) != 0:
            raise ValueError("预演失败，详见下面日志（什么都没动）")
        items = [l[len("Will move: "):] for l in lines if l.startswith("Will move: ")]
        items += [l[len("Will delete: "):] for l in lines if l.startswith("Will delete: ")]
        if not items:
            raise ValueError("没有需要清理的文件（什么都没动）")
        size = next((l.strip() for l in lines if "共 " in l and "（预演）" in l), "")
        head = "\n".join("  " + x for x in items[:12])
        if len(items) > 12:
            head += f"\n  … 还有 {len(items) - 12} 个"
        title, body = CL.confirm_text(mode)
        message = plain(f"{body.replace('{folder}', folder)}\n\n"
                        f"本次将处理 {len(items)} 项" + (f"（{size}）" if size else "")
                        + f"：\n{head}")
        if not messagebox.askyesno(title, message):
            raise ValueError("已取消（一个文件都没动）")

    def confirm_empty_trash(self) -> None:
        """「清空回收站…」：永久删除，必须先确认「不可撤销」；成功后**不给**撤销按钮。

        为什么成功横幅没有「撤销清理」：清空回收站本身就是不可撤销的永久删除
        （与 mac `emptyTrash()` 的 `allowUndo: false` 同口径）。
        """
        folder = self.path()
        if not folder:
            messagebox.showwarning("清空回收站", "请先选目录")
            return
        if not self.run_button.instate(["!disabled"]):
            messagebox.showinfo("清空回收站", "上一个任务还在跑，请等它结束或点「停止」。")
            return
        title, body = CL.empty_trash_text()
        if not messagebox.askyesno(
                title, plain(body.replace("{folder}", folder) + "\n\n（回收站：.toolbox-trash/）")):
            return
        # 直接带着这次调用交给主窗口：不走「按当前选项重新拼」那条路
        # （清空回收站与模式/预演勾选无关，也不该被它们改写）。
        self.on_run(self, E.cleantemp_call(folder=folder, empty_trash=True))
