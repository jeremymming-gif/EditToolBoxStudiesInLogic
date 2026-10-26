"""「检查tex信息」页（`InfoCheckTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

from tkinter import ttk

import theme
from .base import TabPage

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
