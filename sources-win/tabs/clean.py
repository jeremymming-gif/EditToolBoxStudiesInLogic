"""「删除临时文件」页（`CleanTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import engine as E
from .base import TabPage, _check, plain

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
