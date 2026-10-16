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

import json
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
                   "联网核实需要先在「设置」里填好 API 配置。\n"
                   "⚠️ 正文里读不出第一作者姓名时（部分 LaTeX 投稿就是这样），**必须**填"
                   "「输出名」或「作者」，否则引擎会拒绝转换并提示要这两个参数。\n"
                   "第二行是编辑侧出版信息（卷/期/年/页码…）：不填也能转，但 PDF 页眉会是"
                   "「xxxx 年第 x 期，1–10」占位，得手工改 .tex。")

    def build_options(self, parent: ttk.Frame) -> None:
        row1 = ttk.Frame(parent)
        row1.pack(anchor="w")
        row2 = ttk.Frame(parent)
        row2.pack(anchor="w", pady=(4, 0))

        self.v_compile = tk.BooleanVar(value=True)
        self.v_clean = tk.BooleanVar(value=False)
        self.v_refs = tk.BooleanVar(value=True)
        self.v_srcpdf = tk.BooleanVar(value=False)
        _check(row1, "编译 PDF", self.v_compile).pack(side="left")
        _check(row1, "清理临时文件", self.v_clean).pack(side="left")
        _check(row1, "出文献核实任务书", self.v_refs).pack(side="left")
        _check(row1, "导出原稿 PDF", self.v_srcpdf).pack(side="left")

        # 「输出名 / 作者」决定产物目录名；正文读不出姓名时它们是**唯一活路**（H1）
        self.v_name = tk.StringVar(value="")
        self.v_author = tk.StringVar(value="")
        ttk.Label(row1, text="输出名").pack(side="left", padx=(14, 2))
        tk.Entry(row1, textvariable=self.v_name, width=14).pack(side="left")
        ttk.Label(row1, text="作者").pack(side="left", padx=(8, 2))
        tk.Entry(row1, textvariable=self.v_author, width=10).pack(side="left")

        # 编辑侧出版信息（与 mac 同页同一组；缺了只是页眉占位，不报错）
        self.v_vol = tk.StringVar(value="")
        self.v_issue = tk.StringVar(value="")
        self.v_year = tk.StringVar(value="")
        self.v_pages = tk.StringVar(value="")
        self.v_received = tk.StringVar(value="")
        self.v_editor = tk.StringVar(value="")
        self.v_grants = tk.StringVar(value="")
        for label, var, width in (("卷", self.v_vol, 5), ("期", self.v_issue, 5),
                                  ("年", self.v_year, 6), ("页码", self.v_pages, 10),
                                  ("收稿日期", self.v_received, 11),
                                  ("责任编辑", self.v_editor, 8), ("基金", self.v_grants, 20)):
            ttk.Label(row2, text=label).pack(side="left", padx=(8, 2))
            tk.Entry(row2, textvariable=var, width=width).pack(side="left")

    def extra_args(self) -> dict:
        return {"compile_pdf": self.v_compile.get(), "clean": self.v_clean.get(),
                "verify_refs": self.v_refs.get(), "source_pdf": self.v_srcpdf.get(),
                "name": self.v_name.get().strip(), "author": self.v_author.get().strip(),
                "vol": self.v_vol.get().strip(), "issue": self.v_issue.get().strip(),
                "year": self.v_year.get().strip(), "pages": self.v_pages.get().strip(),
                "received": self.v_received.get().strip(),
                "editor": self.v_editor.get().strip(), "grants": self.v_grants.get().strip()}


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
        self.summary = ttk.Label(self, text="（还没检查）", wraplength=760, justify="left")
        self.summary.grid(row=90, column=0, padx=10, pady=(4, 0), sticky="w")
        cols = ("folder", "vol", "issue", "year", "pages", "declared", "actual", "authors", "title")
        heads = ("文件夹", "卷", "期", "年份", "起–止", "声明页数", "实际页数", "第一作者", "中文标题")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=9)
        for c, h in zip(cols, heads):
            self.table.heading(c, text=h)
            self.table.column(c, width=118 if c in ("authors", "title") else 62, anchor="w")
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
        self.table.tag_configure("bad", foreground="#b00")
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
                         " PDF 页数（PDF 缺失或打不开，见日志）——页数一致性未验证。")
            else:
                self.summary.config(text=f"共 {len(arts)} 篇，卷号/期号/年份一致，页数与 PDF 实际页数相符。")


class TocTab(TabPage):
    name = "生成目录"
    pick = "file"
    instruction = ("使用说明：选「检查tex信息」生成的**报告 .txt**，生成中英文两份目录 .tex（**先英文后中文**）。\n"
                   "版式**继承上一期成品**（本期 000 Table of Contents 里已有的优先），只替换本期的卷期信息，\n"
                   "所以同事手工调过的版式会自动延续。下方可填本期栏目（专栏/专辑），留空表示本期无栏目。\n"
                   "点「读取篇目」会列出报告里的文章，可勾选哪些属于栏目 —— 设置会写进报告同目录的 `目录设置.json`。")

    def __init__(self, parent, name, on_run, on_stop):
        super().__init__(parent, name, on_run, on_stop)
        self.rows: list[tuple[str, str]] = []      # [(文章名, 中文标题)]
        self.note = ttk.Label(self, text="", wraplength=760, justify="left", foreground="#555")
        self.note.grid(row=90, column=0, padx=10, pady=(4, 0), sticky="w")
        self.listbox = tk.Listbox(self, selectmode=tk.EXTENDED, height=7, exportselection=False)
        self.listbox.grid(row=91, column=0, padx=10, pady=(2, 10), sticky="nsew")
        self.rowconfigure(91, weight=1)

    def build_options(self, parent: ttk.Frame) -> None:
        self.v_kind = tk.StringVar(value="")
        self.v_cname = tk.StringVar(value="")
        self.v_cname_en = tk.StringVar(value="")
        self.v_host = tk.StringVar(value="")
        self.v_template = tk.StringVar(value="")
        tk.Button(parent, text="读取篇目", width=10, command=self.load_preview).pack(side="left")
        ttk.Label(parent, text="栏目类型").pack(side="left", padx=(10, 2))
        ttk.Combobox(parent, textvariable=self.v_kind, width=6, state="readonly",
                     values=("", "专栏", "专辑")).pack(side="left")
        ttk.Label(parent, text="栏目名").pack(side="left", padx=(8, 2))
        tk.Entry(parent, textvariable=self.v_cname, width=16).pack(side="left")
        ttk.Label(parent, text="英文名").pack(side="left", padx=(8, 2))
        tk.Entry(parent, textvariable=self.v_cname_en, width=18).pack(side="left")
        ttk.Label(parent, text="主持人").pack(side="left", padx=(8, 2))
        tk.Entry(parent, textvariable=self.v_host, width=10).pack(side="left")
        ttk.Label(parent, text="模板来源期目录（留空=自动找上一期）").pack(side="left", padx=(8, 2))
        tk.Entry(parent, textvariable=self.v_template, width=22).pack(side="left")

    def extra_args(self) -> dict:
        return {"template": self.v_template.get()}

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
        self.note.config(text=payload.get("templateNote", ""))
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
            "columnArticles": [self.rows[i][0] for i in self.listbox.curselection()]
            if self.rows else data.get("columnArticles", []),
            "templateFolder": self.v_template.get(),
        })
        data.setdefault("titleWrapLimit", 20)
        data.setdefault("titleWrapLimitEN", 35)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


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
        return {"dry_run": self.v_dry.get()}


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
        tk.Entry(parent, textvariable=self.v_only, width=12).pack(side="left", padx=(4, 12))
        _check(parent, "先预演（只列出会编哪些）", self.v_dry).pack(side="left")

    def extra_args(self) -> dict:
        return {"only": self.v_only.get(), "dry_run": self.v_dry.get()}


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
        text += "\n（引擎目录改完点「保存」即生效；这一行显示的是当前实际解析到的路径）"
        self.status_label.config(text=text)

    def save(self) -> None:
        for key, var in self.vars.items():
            if key == "engineDir":
                self.cfg["engineDir"] = var.get().strip()
                # 立刻生效（不用重开）：engine.py 读的就是这个环境变量
                if self.cfg["engineDir"]:
                    os.environ["SLCN_ENGINE"] = self.cfg["engineDir"]
                else:
                    os.environ.pop("SLCN_ENGINE", None)
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
        # ⚠️ 设置页的「引擎目录」必须**真的生效**：`engine.py` 只认环境变量 `SLCN_ENGINE`，
        #    从前这里写进 cfg 却全仓无人读 → 自动定位失败时，双击 exe 的编辑在 GUI 里
        #    **没有任何自救入口**（他们不会去设环境变量）。2026-10-17 审计实测（M5）。
        #    用 `setdefault`：显式设过环境变量的人仍然优先，不被界面覆盖。
        _saved_engine = self.cfg.get("engineDir", "").strip()
        if _saved_engine:
            os.environ.setdefault("SLCN_ENGINE", _saved_engine)
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[str] = queue.Queue()
        self._busy = False

        self.tab_control = nb = ttk.Notebook(self)
        self.convert_tab = ConvertTab(nb, "初排转换", self.start, self.stop)
        self.checkbib_tab = CheckBibTab(nb, "文献回读校验", self.start, self.stop)
        self.merge_tab = MergeTab(nb, "合并文件", self.start, self.stop)
        self.proof_tab = ProofCheckTab(nb, "一校核查", self.start, self.stop)
        self.info_tab = InfoCheckTab(nb, "检查tex信息", self.start, self.stop)
        self.toc_tab = TocTab(nb, "生成目录", self.start, self.stop)
        self.compile_tab = CompileAllTab(nb, "编译全部", self.start, self.stop)
        self.clean_tab = CleanTab(nb, "删除临时文件", self.start, self.stop)
        self.settings_tab = SettingsTab(nb, self.cfg, lambda: None)
        for tab in (self.convert_tab, self.checkbib_tab, self.merge_tab, self.proof_tab,
                    self.info_tab, self.toc_tab, self.compile_tab, self.clean_tab,
                    self.settings_tab):
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
                if isinstance(line, tuple) and line[0] == "__REPORT__":
                    _tag, tab, payload = line
                    if isinstance(tab, InfoCheckTab):
                        tab.show_report(payload)
                    continue
                self.log(line)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    # ── 运行 ──
    @staticmethod
    def _info_report_payload(folder: str):
        """跑完「检查tex信息」后，把最新的那份 `信息检查*.json` 读回来喂表格。

        为什么读文件而不是把 `--preview` 的 stdout 接回来：这一趟**必须**不带
        `--preview`（要落报告给「生成目录」用），所以结构化结果就在它写的 json 里。
        """
        try:
            files = [f for f in os.listdir(folder)
                     if f.startswith("信息检查") and f.endswith(".json")]
        except OSError:
            return None
        if not files:
            return None
        newest = max((os.path.join(folder, f) for f in files), key=lambda q: os.path.getmtime(q))
        try:
            with open(newest, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

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
        if isinstance(tab, InfoCheckTab):
            # ⚠️ 不带 `--preview`：这一次跑还要把《信息检查…txt/.json》落到目录里
            #    （「生成目录」页要读它）。表格由跑完之后的 `__REPORT__` 那条路填
            #    —— 从前这里 0 处调用 `show_report()`，于是表格永远「（还没检查）」，
            #    而引擎那一轮其实跑得好好的（2026-10-17 审计实测，H2）。
            return E.infocheck_call(folder=path)
        if isinstance(tab, TocTab):
            tab.save_settings(path)          # 先把本期设置写进引擎要读的 json
            return E.toc_call(report=path, **opts)
        if isinstance(tab, ProofCheckTab):
            return E.proofcheck_call(tex=path, **opts)
        if isinstance(tab, CompileAllTab):
            return E.compileall_call(folder=path, **opts)
        if isinstance(tab, CleanTab):
            return E.cleantemp_call(folder=path, **opts)
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
        except Exception as e:  # noqa: BLE001
            # ⚠️ 从前只捕 ValueError/RuntimeError：报告目录只读/被占用时（Windows 上
            #    PermissionError 高发）异常从 `_call_for`（`save_settings` 写 json）直接冒出，
            #    **点「运行」毫无反应**；发布形态还是 `--noconsole`，连 traceback 都看不见
            #    （2026-10-17 审计实测，M4）。
            messagebox.showerror(APP_TITLE, f"准备运行时就出错了：\n{e}")
            self.log(f"❌ 准备运行失败：{e}")
            return
        self.log(f"\n▶ {call.label}：{call.display}")
        self._set_busy(True)
        # ⚠️ 路径必须在**主线程**里取好再交给工作线程：Tk 控件不能跨线程碰 ——
        #    在工作线程里调 `tab.path()`（读 Entry）会 `RuntimeError: main thread is not
        #    in main loop`，worker 当场死掉 ⇒ 界面永远卡在「运行中」。
        #    这是我自己第一版 H2 修法踩的坑，靠**真驱动一次界面**才抓出来（2026-10-17）。
        path = tab.path()
        threading.Thread(target=self._worker, args=(call, tab, path), daemon=True).start()

    def _worker(self, call: E.EngineCall, tab=None, path: str = "") -> None:
        try:
            code = E.run_call(call, on_line=self._q.put, api=self.cfg.get("api"),
                              on_proc=lambda p: setattr(self, "_proc", p))
        except Exception as e:  # noqa: BLE001
            self._q.put(f"❌ 启动失败：{e}")
            code = -1
        if code == 0 and isinstance(tab, InfoCheckTab) and path:
            payload = self._info_report_payload(path)
            if payload:
                self._q.put(("__REPORT__", tab, payload))
        # ⚠️ 退出码 1 在引擎里常常是「有结果」而不是失败（INVARIANTS 三.2）：
        #    checkbib 并回 / proofcheck 有命中 / compileall 有失败 / toc 只出一份。
        #    从前一律写「❌ 失败（退出码 1）」，与上一行引擎自己打的「完成。…」自相矛盾
        #    （2026-10-17 审计实测，H3）。文案与 mac 各页逐字对齐，且在 bridge 里有守卫。
        self._q.put(f"{E.result_text(call.entry, code)}（退出码 {code}）")
        self._q.put("__DONE__")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for tab in (self.convert_tab, self.checkbib_tab, self.merge_tab,
                    self.proof_tab, self.info_tab, self.toc_tab,
                    self.compile_tab, self.clean_tab):
            tab.run_button.config(state="disabled" if busy else "normal")
            tab.stop_button.config(state="normal" if busy else "disabled")

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            # ⚠️ 只 `terminate()` 直接子进程是不够的：发布形态里那只是 PyInstaller 的
            #    bootloader，引擎与它起的 xelatex 都是孙进程 —— 实测按下停止后 xelatex
            #    又跑了 ~12 秒并写出了 PDF（2026-10-17 审计，M3/W1）。
            #    忙状态也**不在这里解除**：等 worker 读到管道结束（`__DONE__`）再解除，
            #    否则会出现「界面已不忙、任务还在写产物目录」，编辑再点一次就有两组 TeX 同写。
            E.kill_tree(proc)
            self.log("⏹ 已请求停止（连同它起的 TeX 等子进程一起停；等日志收尾后按钮会恢复）")
        else:
            self._set_busy(False)


def main() -> int:
    app = MainWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
