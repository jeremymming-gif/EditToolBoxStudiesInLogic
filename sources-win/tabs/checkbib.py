"""「文献回读校验」页（`CheckBibTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

import bibdb_ui as B
import engine as E
import theme
from .base import APP_TITLE, TabPage, _check

class CheckBibTab(TabPage):
    # 批62（入口收敛 B）：侧栏短名保持不动（长串在 185px 侧栏会截断；与 mac 同做法：
    # 长定位标题放正文顶部 `page_title`）。能力一个未删：离线体检/并回/入藏引用库。
    name = "文献回读校验"
    page_title = "回读/比对报告查看器（高级：手动并回，离线）"
    pick = "file"
    instruction = (
        "用途：对一份外部改过 / 交回的 .bib 做离线体检——语法、编辑部规范、引用键闭合，"
        "以及与基准 reference.bib 的逐字段对比（改 key 会让正文引用悬空，是最危险的一类）。"
        "需要时可把通过核对的条目手动并回产物（高级操作：并回前自动备份 .ai前）。\n"
        "本页不生成《文献核实任务书》：需要任务书请走「初排转换 → AI 协同初排（两段式）」"
        "的第一段产物，或命令行 --verify-refs（高级）。AI 交回的修订须人工核对；"
        "本页不联网、不调任何模型，也不能判断文献真伪。\n"
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
