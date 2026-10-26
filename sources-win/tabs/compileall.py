"""「编译全部」页（`CompileAllTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from .base import TabPage, _check

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
