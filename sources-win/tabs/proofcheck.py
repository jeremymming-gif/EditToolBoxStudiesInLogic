"""「一校核查」页（`ProofCheckTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from .base import TabPage, _check

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
