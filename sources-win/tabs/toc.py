"""「生成目录」页（`TocTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

import engine as E
import theme
from .base import APP_TITLE, TabPage, _check, plain

class TocTab(TabPage):
    name = "生成目录"
    pick = "file"
    instruction = ("使用说明：选「检查tex信息」生成的**报告 .txt**，点「运行」一次完成**最终交付**：\n"
                   "  缺少 000 Table of Contents 时自动创建 → 生成中英文目录 → 落位成固定文件名\n"
                   "  （Chinese Content.tex/.pdf、contents.tex/.pdf）→ 用 XeLaTeX 编译成 PDF。\n"
                   "结果区列出 4 个成品路径、模板来源与页数，可直接打开目录。\n"
                   "模板来源由引擎判断并显示（本期成品 → 上一期 → 内置模板），界面不另猜。\n"
                   "高级开关「仅生成 tex（不落位）」打开时只写带时间戳的 .tex（旧行为），不落位、不编译。\n"
                   "下方可填本期栏目（专栏/专辑），留空表示本期无栏目；"
                   "点「读取篇目」会列出报告里的文章，可勾选哪些属于栏目。")

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent, name, on_run, on_stop)
        self.rows: list[tuple[str, str]] = []      # [(文章名, 中文标题)]
        self.result_json_path = ""
        self.text_only_run = False
        self.note = ttk.Label(self, text="（还没读取篇目：选报告后点「读取篇目」）",
                              wraplength=760, justify="left", style="Muted.TLabel")
        self.note.grid(row=90, column=0, padx=10, pady=(4, 0), sticky="w")
        # 结果区（引擎结果 JSON：4 路径 / 模板来源 / 页数 / 注意事项）
        self.delivery = ttk.Label(self, text="", wraplength=820, justify="left")
        self.delivery.grid(row=92, column=0, padx=10, pady=(0, 6), sticky="w")
        self.delivery.grid_remove()
        # ⚠️ `tk.Listbox` 保留：Tk 没有 ttk 对等物。颜色由 `theme.COLOR` 喂进去
        #    （`theme.style_listbox`）—— 主文件里不写死颜色。
        self.listbox = tk.Listbox(self, selectmode=tk.EXTENDED, height=7, exportselection=False)
        theme.style_listbox(self.listbox)
        self.listbox.grid(row=91, column=0, padx=10, pady=(2, 10), sticky="nsew")
        self.rowconfigure(91, weight=1)

    def build_options(self, parent: ttk.Frame) -> None:
        # 两行放：第一行是运行模式与本期栏目；第二行是「显式指定模板」（高级兜底）。
        row1 = ttk.Frame(parent)
        row1.pack(side="top", fill="x")
        row2 = ttk.Frame(parent)
        row2.pack(side="top", fill="x", pady=(4, 0))
        self.v_textonly = tk.BooleanVar(value=False)
        self.v_kind = tk.StringVar(value="")
        self.v_cname = tk.StringVar(value="")
        self.v_cname_en = tk.StringVar(value="")
        self.v_host = tk.StringVar(value="")
        self.v_template = tk.StringVar(value="")
        _check(row1, "仅生成 tex（不落位、不编译）", self.v_textonly).pack(side="left", padx=(0, 10))
        ttk.Button(row1, text="读取篇目", width=10, command=self.load_preview).pack(side="left")
        ttk.Label(row1, text="栏目类型").pack(side="left", padx=(10, 2))
        ttk.Combobox(row1, textvariable=self.v_kind, width=6, state="readonly",
                     values=("", "专栏", "专辑")).pack(side="left")
        ttk.Label(row1, text="栏目名").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.v_cname, width=16).pack(side="left")
        ttk.Label(row1, text="英文名").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.v_cname_en, width=18).pack(side="left")
        ttk.Label(row1, text="主持人").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.v_host, width=10).pack(side="left")
        ttk.Label(row2, text="模板来源期目录（留空=自动找上一期；一般不用填）").pack(side="left")
        ttk.Entry(row2, textvariable=self.v_template, width=32).pack(side="left", padx=(4, 0))

    def extra_args(self) -> dict:
        return {"template": self.v_template.get(),
                "text_only": bool(self.v_textonly.get())}

    def prepare_result_json(self) -> str:
        """每次运行一个新的结果 JSON 临时路径（读回即删；不落稿件目录）。"""
        import tempfile
        handle, path = tempfile.mkstemp(prefix="slcn-toc-", suffix=".json")
        os.close(handle)
        self.result_json_path = path
        return path

    def clear_delivery(self) -> None:
        self.delivery.config(text="")
        self.delivery.grid_remove()

    def show_delivery(self, payload: dict, code: int, tail: str = "") -> None:
        """把引擎结果 JSON 显示到结果区：4 路径 + 模板来源 + 页数 + 注意事项（只显示）。"""
        if not payload:
            if code != 0 and not self.text_only_run:
                text = "❌ 引擎没有写出结果 JSON（已中止）。"
                if tail:
                    text += "\n" + tail
                self.delivery.config(text=plain(text), foreground=theme.COLOR["error"])
                self.delivery.grid()
            return
        note = str(payload.get("templateNote") or "")
        if note:
            self.note.config(text=plain(note))
        lines = E.toc_result_lines(payload)
        if self.text_only_run:
            lines = ["（仅生成 tex：未落位、未编译）"] + lines
        if code == 1:
            lines.insert(0, "⚠️ 已交付，但有需要人工核对的地方（见运行日志）")
        if not lines:
            lines = ["（引擎没有返回可显示的详情；产物位置见运行日志）"]
        self.delivery.config(text=plain("\n".join(lines)),
                             foreground=theme.COLOR["warn"] if code == 1 else theme.COLOR["fg"])
        self.delivery.grid()

    def load_preview(self) -> None:
        """问引擎要篇目清单（`toc.py --preview`），不要自己解析报告 —— 口径必须与生成时一致。"""
        report = self.path()
        if not report:
            messagebox.showwarning(APP_TITLE, "请先选择「检查tex信息」生成的报告 .txt。")
            return
        try:
            call = E.toc_preview_call(report=report)
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        proc = subprocess.run(call.argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=E.env_for_run(None), timeout=120,
                              # Windows：与 engine.run_call 同一口径，别弹黑框
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = ""
        for candidate in (proc.stdout or "").splitlines():
            if candidate.strip().startswith("{"):
                line = candidate
        if not line:
            messagebox.showerror(APP_TITLE, "读不到篇目预览，请看「设置」里的引擎路径是否正确。")
            return
        try:
            payload = json.loads(line)
        except ValueError:
            messagebox.showerror(APP_TITLE, "篇目预览不是合法 JSON（引擎版本可能过旧）。")
            return
        self.rows = [(a.get("name", ""), a.get("title", "")) for a in payload.get("articles", [])]
        self.listbox.delete(0, tk.END)
        saved = self._load_settings(report)
        for name, title in self.rows:
            self.listbox.insert(tk.END, f"{name}　{title[:34]}")
            if name in saved.get("columnArticles", []):
                self.listbox.selection_set(tk.END)
        self.note.config(text=plain(str(payload.get("templateNote", ""))))
        # ⚠️ **逐字段**回填：从前这一整块被 `if saved.get("columnKind"):` 罩着 ——
        #    编辑只填了「模板来源期目录」、没填栏目类型时，读篇目**不回填**任何字段，
        #    紧接着 `save_settings()` 又无条件写回界面上的空值 → 他填的模板期被**静默抹掉**
        #    （2026-10-17 审计实测：文件里 templateFolder=第4期(89)、columnKind 为空
        #    → 读篇目后输入框空 → 运行后文件里变 ""）。现在按 key 存在与否逐个回填。
        for _key, _var in (("columnKind", self.v_kind), ("columnName", self.v_cname),
                           ("columnNameEN", self.v_cname_en), ("columnHost", self.v_host),
                           ("templateFolder", self.v_template)):
            if isinstance(saved.get(_key), str):
                _var.set(saved[_key])

    @staticmethod
    def _settings_path(report: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(report)), "目录设置.json")

    def _load_settings(self, report: str) -> dict:
        try:
            with open(self._settings_path(report), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self, report: str) -> None:
        """把界面上的本期设置写进引擎要读的那个文件（只有一个真相来源）。"""
        path = self._settings_path(report)
        data = self._load_settings(report)
        data.update({
            "columnKind": self.v_kind.get(),
            "columnName": self.v_cname.get(),
            "columnNameEN": self.v_cname_en.get(),
            "columnHost": self.v_host.get(),
            "columnArticles": ([self.rows[i][0] for i in self.listbox.curselection()]
                               if self.rows else data.get("columnArticles", [])),
            "templateFolder": self.v_template.get(),
        })
        data.setdefault("titleWrapLimit", 20)
        # 英文断行默认 30（2026-10-17 第十四批按期 90 成品校准，原 35 见案例第 110 条）
        data.setdefault("titleWrapLimitEN", 30)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
