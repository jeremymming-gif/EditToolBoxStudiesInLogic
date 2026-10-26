"""「合并文件」页（`MergeTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import engine as E
from .base import APP_TITLE, TabPage, _check

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
