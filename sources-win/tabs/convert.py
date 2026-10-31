"""「初排转换」页（`ConvertTab`）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import os
import tempfile
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk

import coop as C
import engine as E
import settings as S
import theme
from .base import APP_TITLE, TabPage, _check, open_path

class ConvertTab(TabPage):
    name = "初排转换"
    pick = "file"

    def __init__(self, parent, name, on_run, on_stop, cfg=None, ai_knobs_sink=None):
        # cfg：AI 的两个旋钮（思考模式/最长输出）现在放在**这一页**（UI 方案 §4：
        # 任务相关选项就地改，别让人去设置页找上下文）。值仍以 cfg 为准（运行时就地读）。
        self.cfg = cfg or {}
        self.ai_knobs_sink = ai_knobs_sink or (lambda *_a: None)
        # ── 第五十五批：两段式 AI 协同初排的界面状态 ──
        # `_coop_out` = 第一段选定的输出根目录（第二段**复用**，不再弹目录选择）；
        # `_coop_stage` = 从暂存目录发现的状态（第二段/各「打开…」按钮都靠它）。
        self._coop_out = ""
        self._coop_stage: C.CoopStage | None = None
        # 批56：已选回填清单（文件/目录/剪贴板临时文件可混合、累加；纯逻辑在 coop.py）
        self._coop_sel = C.ReplySelection()
        self._coop_clip_dir = ""          # 剪贴板回填临时目录（首次粘贴时创建）
        super().__init__(parent, name, on_run, on_stop)
    instruction = ("使用说明：选作者的稿件（.docx / .doc / .tex），再选一个输出根目录，"
                   "产物会写到「输出目录/<作者名>/」。\n"
                   "批62 入口收敛：普通转换**不再**默认生成《文献核实任务书》、也不调用 AI；"
                   "需要核实就勾下面「用 AI 处理参考文献（直连 API）」（产生费用）"
                   "或「AI 协同初排（两段式）」（外部 chat，零费用）；"
                   "CLI 高级用法仍可用 `--verify-refs`。\n"
                   "⚠️ 正文里读不出第一作者姓名时（部分 LaTeX 投稿就是这样），**必须**填"
                   "「输出名」或「作者」，否则引擎会拒绝转换并提示要这两个参数。\n"
                   "第二行是编辑侧出版信息（卷/期/年/页码…）：不填也能转，但 PDF 页眉会是"
                   "「xxxx 年第 x 期，1–10」占位，得手工改 .tex。\n"
                   "核实成本闸门（上限 / 先算账 / 全量 / 先查库）在「引用库 → 引用库设置」页；"
                   "默认值 = 一个参数都不加（与接入引用库之前逐字节相同）。\n"
                   "勾「AI 协同初排（两段式）」= 第一段只把初排产物 + 《文献核实任务书》"
                   "写进暂存目录就暂停（软件不调用任何 API、不上传任何内容）；"
                   "编辑拿任务书去外部 chat 核实后，把回复导入（可多文件/目录/剪贴板）"
                   "再运行第二段才交付。回填含 BibTeX 与附一 JSON 两部分，别只交一部分。")

    def build_options(self, parent: ttk.Frame) -> None:
        row1 = ttk.Frame(parent)
        row1.pack(anchor="w")
        row2 = ttk.Frame(parent)
        row2.pack(anchor="w", pady=(4, 0))

        self.v_compile = tk.BooleanVar(value=True)
        self.v_clean = tk.BooleanVar(value=False)
        # 批62（入口收敛 B）：独立「出文献核实任务书」勾选**不再暴露**。字段保留兼容旧设置/
        # 旧调用（界面默认 false）；任务书改由「用 AI 处理参考文献」或两段式第一段产出。
        self.v_refs = tk.BooleanVar(value=False)
        self.v_srcpdf = tk.BooleanVar(value=False)
        _check(row1, "编译 PDF", self.v_compile).pack(side="left")
        _check(row1, "清理临时文件", self.v_clean).pack(side="left")
        _check(row1, "导出原稿 PDF", self.v_srcpdf).pack(side="left")

        # 「输出名 / 作者」决定产物目录名；正文读不出姓名时它们是**唯一活路**（H1）
        self.v_name = tk.StringVar(value="")
        self.v_author = tk.StringVar(value="")
        ttk.Label(row1, text="输出名").pack(side="left", padx=(14, 2))
        ttk.Entry(row1, textvariable=self.v_name, width=14).pack(side="left")
        ttk.Label(row1, text="作者").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.v_author, width=10).pack(side="left")

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
            ttk.Entry(row2, textvariable=var, width=width).pack(side="left")

        # AI 的两个旋钮（2026-10-17 从「设置」页挪到这里 —— 方案 §4：任务选项就地改，
        # 别让人去设置页找上下文）。留空 = 不发送该字段（引擎默认行为）。
        ai_cfg = self.cfg.get("api") or {}
        row3 = ttk.Frame(parent)
        row3.pack(anchor="w", pady=(4, 0))
        ttk.Label(row3, text="AI 核实用：思考模式").pack(side="left")
        self.v_thinking = tk.StringVar(value=str(ai_cfg.get("thinking", "")))
        ttk.Entry(row3, textvariable=self.v_thinking, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(row3, text="最长输出").pack(side="left")
        self.v_maxtok = tk.StringVar(value=str(ai_cfg.get("maxTokens", "")))
        ttk.Entry(row3, textvariable=self.v_maxtok, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(row3, text="留空 = 用端点默认值", style="Muted.TLabel").pack(side="left")
        for _v in (self.v_thinking, self.v_maxtok):
            _v.trace_add("write", lambda *_: self.ai_knobs_sink(
                self.v_thinking.get().strip(), self.v_maxtok.get().strip()))

        # ── 批62：入口收敛 B —— 直连 AI 从「配了端点就隐式跑」改为**显式主动勾选** ──
        # 勾上 = 本次走 ⑪（内部自动带 `--verify-refs`：出任务书 + 按同一份规范直连核实/并入）；
        # 不勾 = 普通初排：不联网、不出《文献核实任务书》（批62 的行为变化，文档会写清）。
        # label 与 mac 逐字一致；会话态、默认不勾（涉及费用与未刊稿外发，每次运行都由编辑主动勾）。
        ai_row = ttk.Frame(parent)
        ai_row.pack(anchor="w", pady=(4, 0))
        self.v_ai = tk.BooleanVar(value=False)
        _check(ai_row, "用 AI 处理参考文献（直连 API，会产生费用）", self.v_ai,
               command=self._toggle_ai_options).pack(side="left")
        ttk.Label(ai_row, text="（需先在「设置」里配置端点；勾上才联网）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        # 动态提示（与 mac 一样按「配置没配置端点」给不同颜色的话，不抄判据只读配置）。
        self.ai_hint = ttk.Label(parent, text="", wraplength=820, justify="left",
                                 foreground=theme.COLOR["ok"])

        # ── 批60：全文 AI 核查（直连 API，会产生费用；默认不勾、**会话态**）─────────
        # 用户 2026-10-20 授权全文外发；Lead 拍板「每次运行主动同意」——**不写 settings.json**
        # （与 mac `Options.llmFulltext`、批59 coopFulltext 同一模式：默认每次都不勾最安全）。
        # 勾了才在 ⑪（本次真的走直连 AI：非两段式 + `--verify-refs` 且不是 `--verify-plan`）
        # 追加 `--llm-fulltext`；report-only：只生成《…_AI全文复核.md》，不改正文/reference.bib。
        # 批62：整块**只在「用 AI 处理参考文献」勾着时展开**（与 mac 的嵌套结构一致）——
        # 未勾 AI 时既看不到、也一个参数都不传（`llm_fulltext` 进了 extra_args 也不会生效）。
        self.llm_fulltext_box = ttk.Frame(parent)
        row_ft = ttk.Frame(self.llm_fulltext_box)
        row_ft.pack(anchor="w", pady=(4, 0))
        self.v_llm_fulltext = tk.BooleanVar(value=False)
        _check(row_ft, "全文 AI 核查（直连 API，会产生费用）",
               self.v_llm_fulltext).pack(side="left")
        self.llm_fulltext_hint = ttk.Label(
            self.llm_fulltext_box,
            # 逐字对齐 mac `ConvertTab.swift`（批60）的三行说明：两壳同口径，改文案必须两边
            # 同时改；这里只讲「会发生什么/不勾会怎样/前提是什么」，判据不抄进外壳。
            text="⚠ 勾上后会产生费用：引擎把稿件正文按片（每片 ≤3 万字）直接发送到"
                 "「设置」里配置的 AI 端点，每片一次请求；不勾（默认）时正文一个字节都不外发。\n"
                 "范围：只生成核查报告《…_AI全文复核.md》，不改正文、不改 reference.bib"
                 "（report-only）；两段式协同模式下本轮不生效（那条流程正文只进本机任务书）。\n"
                 "⚠ 前提：需先在「设置」页配置端点；未配置时引擎直接退出 2（不会发出任何请求）。"
                 "另请确认预算够——「设置」页的「单篇预算」（默认 30000）可能不够全文分片，"
                 "请按日志提示调大（0 = 不限）。",
            style="Muted.TLabel", wraplength=820, justify="left")
        self.llm_fulltext_hint.pack(anchor="w")
        self._toggle_ai_options()          # 默认不勾 → 提示/全文核查区全部收起

        # 第 132 条：把「当前核实范围」就地显示（方案 §4：任务相关选项就地改，别去设置页找）；
        # 值仍以「引用库设置」页为准，这里只是**每进本页刷新一次**的只读回显。
        row4 = ttk.Frame(parent)
        row4.pack(anchor="w", pady=(4, 0))
        self.bibdb_hint = ttk.Label(row4, text="", style="Muted.TLabel",
                                    wraplength=780, justify="left")
        self.bibdb_hint.pack(anchor="w")
        self.refresh_bibdb_hint()

        # ── 第五十五批：两段式 AI 协同初排（默认关；勾上才展开操作区）──────────
        # 用户口径（2026-10-19）：第一段只写暂存目录 + 任务书并暂停；编辑拿任务书去
        # 外部 chat（豆包/千问/DeepSeek…）核实 —— **软件不上传任何内容、零费用**；
        # 回填后导入回复跑第二段，第二段成功才交付。
        row5 = ttk.Frame(parent)
        row5.pack(anchor="w", pady=(8, 0))
        self.v_coop = tk.BooleanVar(value=False)
        _check(row5, "AI 协同初排（两段式）", self.v_coop,
               command=self._toggle_coop_panel).pack(side="left")
        ttk.Label(row5, text="（软件不调用 API、不上传任何内容；外部 chat 核实后导入回复再跑第二段）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        self.coop_panel = ttk.Frame(parent)
        # 批62（入口收敛 B）：任务书入口收敛到两段式/直连 AI/CLI，独立勾选已隐藏。
        # 文案与 mac 逐字一致（「=」半角、`--verify-refs` 无反引号）。
        self.coop_guide = ttk.Label(
            self.coop_panel,
            text="文献核实任务书 = 两段式第一段产物；普通转换如需任务书请用两段式或 "
                 "CLI --verify-refs（高级）",
            style="Muted.TLabel", wraplength=820, justify="left")
        self.coop_guide.pack(anchor="w")
        self.v_coop_status = tk.StringVar(value="状态：未运行")
        ttk.Label(self.coop_panel, textvariable=self.v_coop_status).pack(anchor="w")
        btns = ttk.Frame(self.coop_panel)
        btns.pack(anchor="w", pady=(2, 0))
        self.coop_taskbook_btn = ttk.Button(btns, text="打开任务书", width=12,
                                            state="disabled", command=self._open_coop_taskbook)
        self.coop_stage_btn = ttk.Button(btns, text="打开暂存目录", width=12,
                                         state="disabled", command=self._open_coop_stage_dir)
        self.coop_deliver_btn = ttk.Button(btns, text="打开交付目录", width=12,
                                           state="disabled", command=self._open_coop_delivery)
        self.coop_record_btn = ttk.Button(btns, text="打开协同记录", width=12,
                                          state="disabled", command=self._open_coop_record)
        self.coop_suggest_btn = ttk.Button(btns, text="打开建议清单", width=12,
                                           state="disabled", command=self._open_coop_suggestions)
        for _btn in (self.coop_taskbook_btn, self.coop_stage_btn, self.coop_deliver_btn,
                     self.coop_record_btn, self.coop_suggest_btn):
            _btn.pack(side="left", padx=(0, 6))
        # ── B-288b：TASK-ID 展示（引擎写进 state；旧状态没有就不显示）──────────
        self.coop_task_id_label = ttk.Label(self.coop_panel, text="",
                                            style="Muted.TLabel",
                                            wraplength=820, justify="left")
        self.coop_task_id_label.pack(anchor="w")
        # ── B-006：分片列表/路径（第一段写入 state 后才有内容）──────────────
        self.v_coop_slices = tk.StringVar(value="")
        ttk.Label(self.coop_panel, textvariable=self.v_coop_slices,
                  style="Muted.TLabel", wraplength=820,
                  justify="left").pack(anchor="w")
        # ── B-288b：分卷入口（`--coop-split`；三个固定按钮，缺卷时禁用）────
        vol_row = ttk.Frame(self.coop_panel)
        vol_row.pack(anchor="w", pady=(2, 0))
        ttk.Label(vol_row, text="分卷：", style="Muted.TLabel").pack(side="left")
        self.coop_volume_btns: list[ttk.Button] = []
        for _label in ("A", "B", "C"):
            _b = ttk.Button(vol_row, text="打开卷 " + _label, width=10,
                            state="disabled")
            _b.pack(side="left", padx=(0, 6))
            self.coop_volume_btns.append(_b)
        # ── 批59：全文 AI 核查（任务书附正文；默认关，软件零上传/零费用）─────────
        # 用户 2026-10-20 授权全文外发（可选即可），但**默认不勾**；勾了才在 ph1 的
        # argv 追加 `--coop-fulltext context|full`（引擎把正文/引用上下文附进任务书，
        # 之后由编辑自己复制给外部 chat）。范围默认「引用上下文」（外发得更少）。
        full_row = ttk.Frame(self.coop_panel)
        full_row.pack(anchor="w", pady=(4, 0))
        self.v_coop_fulltext = tk.BooleanVar(value=False)
        _check(full_row, "全文 AI 核查（任务书附正文）", self.v_coop_fulltext).pack(side="left")
        self.v_coop_fulltext_scope = tk.StringVar(value=C.FULLTEXT_CONTEXT)
        for _text, _value in (("引用上下文（默认，约 4 万字上限）", C.FULLTEXT_CONTEXT),
                              ("全文（约 8 万字上限）", C.FULLTEXT_FULL)):
            ttk.Radiobutton(full_row, text=_text, value=_value,
                            variable=self.v_coop_fulltext_scope).pack(
                                side="left", padx=(10, 0))
        self.coop_fulltext_hint = ttk.Label(
            self.coop_panel,
            # 静态提示与 mac `ConvertTab.swift` 的全文核查说明**逐字一致**（批59；两壳同口径，
            # 改文案必须两边同时改，否则「两个平台算出来的结果一致」就破了）。
            text="默认不勾：任务书不含正文，与从前一致（软件零上传、零费用）。\n"
                 "勾上后引擎会把正文/引用上下文写进本机《文献核实任务书》（范围见上）："
                 "文件只留在本机，由你自己复制给外部 chat 做全文核查——软件仍不上传、不调用 API。"
                 "已获授权，不再弹二次确认。\n"
                 "⚠ 只对「开始第一段（出任务书）」生效；第二段续跑与交付不受影响。",
            style="Muted.TLabel", wraplength=820, justify="left")
        self.coop_fulltext_hint.pack(anchor="w")
        # ── B-006：任务书自动分片（默认关）──────────────────────────────────
        # 不勾 → `coop_ph1_call` 一个参数都不加（argv 与从前逐字节相同）。
        # 勾了才在 ph1 argv 末尾追加 `--coop-slice 40`（固定 40 条/片，落 30–50 区间）。
        slice_row = ttk.Frame(self.coop_panel)
        slice_row.pack(anchor="w", pady=(4, 0))
        self.v_coop_slice = tk.BooleanVar(value=False)
        _check(slice_row, "按片拆分任务书（每片 40 条，带片号 / TASK-ID）",
               self.v_coop_slice).pack(side="left")
        ttk.Label(slice_row, text="（分片后请逐片回传；不勾 = 与从前一样只有一份任务书）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        # ── 批56：回填导入（多文件/目录/剪贴板 → 已选清单；可移除/清空）──────────
        # 用户实测：mac 上多选两个文件被折叠成父目录、只导入了 json 丢了 bibtex。
        # 这里把「已选清单」做成可见、可改的：每个路径按顺序作为一个 `--coop-replies`
        # 交给引擎（批56 可重复），不再复制到临时目录、不再折叠父目录。
        sel_title = ttk.Frame(self.coop_panel)
        sel_title.pack(anchor="w", pady=(6, 0))
        self.v_coop_sel = tk.StringVar(value="已选 0 项")
        ttk.Label(sel_title, textvariable=self.v_coop_sel).pack(side="left")
        ttk.Label(sel_title, text="（回填 = BibTeX 代码块 + 附一 JSON 两部分；可多文件/目录/剪贴板一起导入）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        addrow = ttk.Frame(self.coop_panel)
        addrow.pack(anchor="w", pady=(2, 0))
        ttk.Button(addrow, text="添加回填文件…", width=14,
                   command=self._add_coop_files).pack(side="left")
        ttk.Button(addrow, text="添加回填目录…", width=14,
                   command=self._add_coop_dir).pack(side="left", padx=(6, 0))
        ttk.Button(addrow, text="从剪贴板导入", width=14,
                   command=self._import_clipboard_reply).pack(side="left", padx=(6, 0))
        self.coop_remove_btn = ttk.Button(addrow, text="移除选中", width=10,
                                          state="disabled",
                                          command=self._remove_selected_coop_replies)
        self.coop_remove_btn.pack(side="left", padx=(10, 0))
        self.coop_clear_btn = ttk.Button(addrow, text="全部清除", width=10,
                                         state="disabled",
                                         command=self._clear_coop_replies)
        self.coop_clear_btn.pack(side="left", padx=(6, 0))
        self.coop_list = tk.Listbox(self.coop_panel, selectmode=tk.EXTENDED, height=4,
                                    exportselection=False)
        theme.style_listbox(self.coop_list)   # 颜色只从 theme 来（主文件不写死颜色）
        self.coop_list.pack(anchor="w", fill="x", pady=(2, 0))
        self.coop_reply_msg = ttk.Label(self.coop_panel, text="",
                                        foreground=theme.COLOR["error"],
                                        wraplength=820, justify="left")
        self.coop_reply_msg.pack(anchor="w")
        run2 = ttk.Frame(self.coop_panel)
        run2.pack(anchor="w", pady=(4, 0))
        self.coop_import_btn = ttk.Button(run2, text="运行第二段…",
                                          state="disabled", command=self._run_coop_ph2)
        self.coop_import_btn.pack(side="left")
        self.v_coop_apply = tk.BooleanVar(value=False)
        _check(run2, "一键修改产物（--coop-apply）", self.v_coop_apply).pack(
            side="left", padx=(10, 0))
        ttk.Label(run2, text="应用前先备份 reference.bib.ai前 / <名>.tex.ai前（可恢复）；"
                             "文献字段→bib、元数据→.tex 的 \\my*、citation_unmatched 脚注→.tex 的 "
                             "\\parencite；须在运行前勾选，交付后重跑是幂等早退",
                  style="Muted.TLabel").pack(side="left", padx=(6, 0))
        # ── B-014 U6-3：三档状态（结果区第一行；文案固定 ✅完成/⚠需核对 N 处/⛔失败）──
        self.v_coop_tier = tk.StringVar(value="")
        self.coop_tier_label = ttk.Label(self.coop_panel, textvariable=self.v_coop_tier)
        self.coop_tier_label.pack(anchor="w", pady=(3, 0))
        self.v_coop_summary = tk.StringVar(value="")
        ttk.Label(self.coop_panel, textvariable=self.v_coop_summary, style="Muted.TLabel",
                  wraplength=820, justify="left").pack(anchor="w", pady=(3, 0))
        # 0 文献回填橙色警告（批56）：只在「文献回填 0/N」时出现（默认收起）
        self.v_coop_warn = tk.StringVar(value="")
        self.coop_warn_label = ttk.Label(self.coop_panel, textvariable=self.v_coop_warn,
                                         foreground=theme.COLOR["warn"],
                                         wraplength=820, justify="left")
        self.coop_panel.pack_forget()              # 默认隐藏，勾上「两段式」才出现

    def refresh_bibdb_hint(self) -> None:
        """回显「引用库 → 引用库设置」里的成本闸门/查库开关（只读，不写配置）。"""
        opts = self.cfg.get("bibdb") or {}
        text = "核实范围：" + S.verify_max_summary(self.cfg)
        if opts.get("verifyAllRefs"):
            text += "；已允许全量核实"
        if opts.get("verifyPlan"):
            text += "；先算账（本次只打印计划，不真正核实）"
        if opts.get("lookupEnabled") is False:
            text += "；本次不查引用库（--no-bibdb）"
        text += "（在「引用库 → 引用库设置」改；默认 = 一个参数都不加）"
        self.bibdb_hint.config(text=text)

    # ── 第五十五批：两段式 AI 协同初排（判据/发现/摘要都在 coop.py；这里只取值/显示）──
    def coop_enabled(self) -> bool:
        return bool(self.v_coop.get())

    def coop_out_root(self) -> str:
        """第一段选定的输出根目录（第二段复用，不再弹目录选择）。"""
        return self._coop_out

    def set_coop_out(self, out: str) -> None:
        self._coop_out = out or ""

    def _toggle_coop_panel(self) -> None:
        if self.coop_enabled():
            self.coop_panel.pack(anchor="w", pady=(2, 0))
        else:
            self.coop_panel.pack_forget()
        self._refresh_ai_hint()            # 批62：协同模式下直连 AI 不生效，提示要跟着变

    # ── 批62：入口收敛 B —— 直连 AI 显式勾选（说明与全文核查区按需展开）──────────
    def _toggle_ai_options(self) -> None:
        """勾「用 AI 处理参考文献」才展开直连说明与「全文 AI 核查」区（默认全部收起）。"""
        on = bool(self.v_ai.get())
        self._refresh_ai_hint()
        if on:
            self.ai_hint.pack(anchor="w")
            self.llm_fulltext_box.pack(anchor="w")
        else:
            self.ai_hint.pack_forget()
            self.llm_fulltext_box.pack_forget()

    def _refresh_ai_hint(self) -> None:
        """批62：AI 勾选下的动态提示（配置了端点=会直连核实；没配=只出任务书，不调模型）。"""
        if self.v_ai.get() and getattr(self, "v_coop", None) is not None \
                and self.v_coop.get():
            self.ai_hint.config(
                text="⚠ 已勾「AI 协同初排（两段式）」：本次直连 API 不生效"
                     "（两段式全程不调用 API，外部 chat 由编辑自己操作）；"
                     "「用 AI 处理参考文献」的勾选不影响两段的产物。",
                foreground=theme.COLOR["warn"])
            return
        api = self.cfg.get("api") or {}
        if S.ai_configured(self.cfg):
            base = str(api.get("base", "") or "").strip()
            model = str(api.get("model", "") or "").strip()
            key = str(api.get("key", "") or "").strip()
            self.ai_hint.config(
                text=("✔ 已配置 " + (model or "（未填模型名）") + " @ " + base
                      + (f"（密钥 {S.mask_key(key)}）" if key else "")
                      + " —— 转换时内部加 --verify-refs：在本机直连这个端点，按《文献核实任务书》"
                      "同一份规范生成修订，并把通过安全校验的条目并入交付的 reference.bib"
                      "（合并前先备份 reference.bib.ai前；逐条记录写进《…_文献核实.md》）。"
                      "会产生 API 费用；并完之后都必须人工过一遍。"),
                foreground=theme.COLOR["ok"])
        else:
            self.ai_hint.config(
                text="⚠ 端点还没配置：本次只会生成《文献核实任务书》，不会调用任何模型、"
                     "reference.bib 一个字节不改。请到「设置」页填写 API 地址与模型名"
                     "（「测试连接」可自检），配好后再回来运行。",
                foreground=theme.COLOR["warn"])

    def _ensure_coop_stage(self) -> C.CoopStage | None:
        """当前暂存目录：按输出根 + 源稿**重新发现**（源稿/输出目录改过就不认旧的）。"""
        if self._coop_out:
            found = C.discover_stage(self._coop_out, source=self.path())
            if found is not None:
                self._coop_stage = found
        return self._coop_stage

    def _refresh_coop_buttons(self, stage: C.CoopStage | None = None) -> None:
        """按暂存目录当前状态启用/禁用各按钮（找不到的路径一律禁用，不给假按钮）。"""
        stage = stage if stage is not None else self._coop_stage

        def _on(path: str) -> str:
            return "normal" if path and os.path.exists(path) else "disabled"

        self.coop_taskbook_btn.config(state=_on(stage.taskbook if stage else ""))
        self.coop_stage_btn.config(state="normal" if stage else "disabled")
        delivered = bool(stage and stage.delivery_dir)
        self.coop_deliver_btn.config(
            state=_on(stage.delivery_dir if delivered else ""))
        self.coop_record_btn.config(state=_on(stage.record_path if delivered else ""))
        self.coop_suggest_btn.config(state=_on(stage.suggestions_md if stage else ""))
        task_id = stage.taskbook_task_id if stage else ""
        self.coop_task_id_label.config(
            text=(f"任务书 TASK-ID：{task_id}（分片/分卷时按片回传；片头 TASK-ID 必须一致）"
                  if task_id else ""))
        self._refresh_coop_slices(stage)
        volumes = stage.taskbook_volumes if stage else []
        for btn, path in zip(self.coop_volume_btns, (volumes + ["", "", ""])[:3]):
            btn.config(state=_on(path), command=lambda p=path: self._coop_open(p))
        # 批56：第二段要「已选回填清单」非空才可运行（无回填时明确禁用，不让人白点）
        self.coop_import_btn.config(
            state="normal" if (stage and not delivered and self._coop_sel.count > 0)
            else "disabled")

    def _refresh_coop_slices(self, stage: C.CoopStage | None) -> None:
        """把 `taskbook_slices` 显示成「分片 k/n：路径」（没有分片时清空）。"""
        slices = stage.taskbook_slices if stage else []
        if not slices:
            self.v_coop_slices.set("")
            return
        parts = []
        for item in slices:
            try:
                index, total = int(item.get("index") or 0), int(item.get("total") or 0)
            except (TypeError, ValueError):
                index, total = 0, 0
            path = str(item.get("path") or "")
            parts.append(f"分片 {index}/{total}：{path}")
        self.v_coop_slices.set("｜".join(parts) +
                               "（回填时请逐片导入；缺片只会提示，不会改动产物）")

    def _set_coop_tier(self, tier: str, count: int = 0) -> None:
        """三档状态文字 + 颜色（文字/颜色双通道，不靠颜色单一区分）。"""
        if tier == "failed":
            self.v_coop_tier.set("⛔ 失败")
            self.coop_tier_label.config(foreground=theme.COLOR["error"])
        elif tier == "review":
            self.v_coop_tier.set(f"⚠ 需核对 {count} 处" if count else "⚠ 需核对")
            self.coop_tier_label.config(foreground=theme.COLOR["warn"])
        else:
            self.v_coop_tier.set("✅ 完成")
            self.coop_tier_label.config(foreground=theme.COLOR["ok"])

    def apply_coop_result(self, payload: dict, code: int) -> None:
        """工作线程跑完后的协同结果 → 界面状态（只能主线程调；`_worker` 经队列送来）。"""
        phase = str(payload.get("phase") or "")
        stage = payload.get("stage")
        if not isinstance(stage, C.CoopStage):
            stage = None
        if stage is not None:
            self._coop_stage = stage
        cand = ("（发现 %d 个匹配的暂存目录，已选最近修改的一个）" % stage.found
                if stage is not None and stage.found > 1 else "")
        if code == 0 and phase == "ph1" and stage is not None:
            self._set_coop_warning("")
            self._set_coop_tier("review")      # 第一段只出任务书：待人工核实
            self.v_coop_status.set(f"状态：{stage.status_text}")
            self.v_coop_summary.set(
                f"暂存目录：{stage.stage_dir}｜任务书："
                f"{os.path.basename(stage.taskbook) or '（未找到）'}{cand}"
                "｜下一步：把任务书交给外部 chat 核实；回填（BibTeX + 附一 JSON）后"
                "用上面的「添加回填文件…/目录…/从剪贴板导入」加入清单，再点「运行第二段…」"
                "（第二段成功才算交付；最终交付目录本次未写）")
        elif code == 0 and phase == "ph2" and stage is not None:
            self.v_coop_status.set(f"状态：{C.STATUS_DELIVERED}")
            summary = payload.get("summary") or {}
            text = str(summary.get("text") or "")
            if not text:
                # 极老的状态文件（连 keys/record 都没有）→ 退回建议清单口径，仍不瞎编
                text = C.suggestions_text(payload.get("suggestions") or {})
            if summary.get("legacy") and text:
                text += "（旧状态回退统计，部分为估算）"
            self.v_coop_summary.set(
                f"交付目录：{stage.delivery_dir}｜{text}｜协同记录：{C.RECORD_NAME}{cand}")
            self._set_coop_warning(str(summary.get("warning") or ""))
            self._set_coop_tier(*C.status_tier(
                summary.get("stats") or {}, legacy=bool(summary.get("legacy")),
                failed=False))
        else:
            self._set_coop_warning("")
            self._set_coop_tier("failed")
            why = payload.get("error") or f"退出码 {code}"
            self.v_coop_status.set(f"状态：{C.STATUS_FAILED}（{why}）")
            if stage is not None:
                self.v_coop_summary.set(
                    f"暂存目录仍在：{stage.stage_dir}（**未交付**，原因见运行日志）")
            else:
                self.v_coop_summary.set(
                    "详见运行日志；没有发现可续跑的暂存目录（第一段可能没跑成）")
        self._refresh_coop_buttons(stage)
        self._refresh_coop_replies()

    # 「打开…」按钮：路径一律来自状态文件（引擎写、界面只读），不存在就明说，不猜。
    def _coop_open(self, path: str) -> None:
        if not path:
            messagebox.showinfo(APP_TITLE, "还没有可打开的文件：请先运行第一段（或第二段）。")
            return
        if not os.path.exists(path):
            messagebox.showwarning(APP_TITLE, f"文件/目录不存在（可能已被移动）：\n{path}")
            return
        open_path(path)

    def _open_coop_taskbook(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.taskbook if stage else "")

    def _open_coop_stage_dir(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.stage_dir if stage else "")

    def _open_coop_delivery(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.delivery_dir if stage else "")

    def _open_coop_record(self) -> None:
        stage = self._ensure_coop_stage()
        self._coop_open(stage.record_path if stage else "")

    def _open_coop_suggestions(self) -> None:
        stage = self._ensure_coop_stage()
        if not stage:
            self._coop_open("")
            return
        # 清单有 .md + .jsonl 两份：优先给人看的 .md（jsonl 是机器可读的逐条记录）
        self._coop_open(stage.suggestions_md if os.path.exists(stage.suggestions_md)
                        else stage.suggestions_jsonl)

    # ── 批56：回填导入（已选清单 / 多选文件 / 目录 / 剪贴板）────────────────
    def _coop_error(self, text: str) -> None:
        """回填导入区的红字提示（为空 = 清掉）。"""
        self.coop_reply_msg.config(text=text or "")

    def _refresh_coop_replies(self) -> None:
        """把 `ReplySelection` 同步到界面：清单、计数、移除/清空/运行按钮状态。"""
        paths = self._coop_sel.paths
        self.coop_list.delete(0, tk.END)
        for path in paths:
            self.coop_list.insert(tk.END, C.reply_prefix(path) + path)
        self.v_coop_sel.set(self._coop_sel.label())
        has_items = bool(paths)
        self.coop_remove_btn.config(state="normal" if has_items else "disabled")
        self.coop_clear_btn.config(state="normal" if has_items else "disabled")
        stage = self._coop_stage
        delivered = bool(stage and stage.delivery_dir)
        self.coop_import_btn.config(
            state="normal" if (stage and not delivered and has_items) else "disabled")

    def add_coop_replies(self, paths) -> tuple[int, int]:
        """把路径加入已选清单，返回 `(新增, 跳过)`（跳过 = 空串/重复）。"""
        added, skipped = self._coop_sel.add(paths)
        self._refresh_coop_replies()
        if added:
            self._coop_error("")
        return added, skipped

    def _add_coop_files(self) -> None:
        """选文件（可多选）：每个文件按顺序作为一个独立的回填路径（不折叠父目录）。"""
        files = fd.askopenfilenames(
            title="选择 AI 回填文件（可多选：BibTeX/JSON/文本）",
            filetypes=[("回填文本", ("*.md", "*.txt", "*.bib", "*.json", "*.jsonl")),
                       ("全部文件", "*.*")])
        paths = [path for path in (files or ()) if path]
        if paths:
            _added, skipped = self.add_coop_replies(paths)
            if skipped:
                self._coop_error(f"已跳过 {skipped} 项（与已选重复或为空）")

    def _add_coop_dir(self) -> None:
        """选目录：目录本身作为一个回填路径（引擎读入其中全部可读文本）。"""
        directory = fd.askdirectory(title="选择 AI 回填目录（读入其中全部 .md/.txt/.bib/.json）")
        if directory:
            self.add_coop_replies([directory])

    def _import_clipboard_reply(self) -> None:
        """「从剪贴板导入」：文本 → 临时 .md（命名确定、带序号）→ 加入已选清单。"""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._coop_error("剪贴板里没有可导入的文本（或不是文本内容）")
            return
        if not str(text or "").strip():
            self._coop_error("剪贴板里没有可导入的文本")
            return
        try:
            if not self._coop_clip_dir:
                self._coop_clip_dir = tempfile.mkdtemp(prefix="slcn-coop-clip-")
            path = C.write_clipboard_reply(text, self._coop_clip_dir)
        except (OSError, ValueError) as e:  # noqa: BLE001
            self._coop_error(f"剪贴板导入失败：{e}")
            return
        self.add_coop_replies([path])
        self._coop_error("")

    def _remove_selected_coop_replies(self) -> None:
        """移除清单里选中的项（多选：从后往前删，避免下标错位）。"""
        indexes = sorted(self.coop_list.curselection(), reverse=True)
        for index in indexes:
            self._coop_sel.remove_index(index)
        self._refresh_coop_replies()

    def _clear_coop_replies(self) -> None:
        self._coop_sel.clear()
        self._refresh_coop_replies()

    def coop_ph1_call(self, out: str) -> E.EngineCall:
        """第一段 argv：页面现有参数 + `--verify-refs --no-ai-merge --coop-taskbook-only`。

        批59：勾了「全文 AI 核查」再追加 `--coop-fulltext context|full`；不勾时值是空串，
        argv 与批58 逐字节相同（默认零变化）。范围/勾选的映射在 `coop.coop_fulltext_mode`。
        """
        args = self.extra_args()
        args.pop("verify_plan", None)      # 第一段绝不传 --verify-plan（引擎会提前 return 0）
        args["coop_taskbook_only"] = True
        args["coop_fulltext"] = C.coop_fulltext_mode(self.v_coop_fulltext.get(),
                                                     self.v_coop_fulltext_scope.get())
        call = E.convert_call(source=self.path(), out=out, **args)
        # B-006：分片开关**默认关** → 一个参数都不加（argv 与从前逐字节相同）。
        # 勾了才追加 `--coop-slice 40`；追加在末尾（argparse 与位置无关）。
        slice_value = C.slice_mode(self.v_coop_slice.get())
        if slice_value:
            call.argv += ["--coop-slice", slice_value]
        return call

    def coop_ph2_call(self, replies) -> E.EngineCall:
        """第二段 argv（最小集）：**重复** `--coop-replies`（按已选顺序）+ 其余由状态继承。

        批56 起允许把多个文件/目录一起交给引擎（用户实测里 json + bibtex 是两个文件，
        批55 只传一个 → 文献回填 0/9）。`--clean` 跟随页面「清理临时文件」开关。
        """
        stage = self._coop_stage
        if stage is None or not self._coop_out:
            raise ValueError("还没有第一段生成的暂存目录：请先勾选「AI 协同初排（两段式）」"
                             "并运行第一段")
        paths = [str(path) for path in (replies or []) if str(path or "").strip()]
        if not paths:
            raise ValueError("还没有加入任何回填：请先「添加回填文件…/添加回填目录…/"
                             "从剪贴板导入」（回填 = BibTeX + 附一 JSON 两部分）")
        # 源稿改过就**不能**拿旧暂存续跑（引擎会因指纹不符拒绝，界面先讲清楚）。
        state_source = str((stage.state.get("source") or {}).get("path") or "")
        current = os.path.abspath(self.path()) if self.path() else ""
        if (state_source and current
                and os.path.normcase(os.path.abspath(state_source)) != os.path.normcase(current)):
            raise ValueError("源稿与第一段时不一致（暂存目录属于另一份稿）："
                             "请重新运行第一段")
        return E.convert_call(source=self.path(), out=self._coop_out,
                              coop_replies=paths, coop_stage=stage.stage_dir,
                              coop_apply=bool(self.v_coop_apply.get()),
                              compile_pdf=bool(self.v_compile.get()),
                              clean=bool(self.v_clean.get()))

    def _run_coop_ph2(self) -> None:
        """「运行第二段…」：用**已选清单**（不再弹文件选择框），先定 --coop-apply 再跑。"""
        stage = self._ensure_coop_stage()
        if stage is None or not self._coop_out:
            messagebox.showwarning(
                APP_TITLE,
                "还没有可续跑的暂存目录：请先勾选「AI 协同初排（两段式）」并点「运行」"
                "跑第一段（任务书/暂存目录出来后这个按钮才能用）。")
            return
        if stage.delivery_dir:
            messagebox.showinfo(APP_TITLE, "这篇已经交付过了。要重新处理请重新运行第一段"
                                           "（第一段会覆盖暂存目录）。")
            return
        if self._coop_sel.count == 0:
            messagebox.showwarning(
                APP_TITLE,
                "还没有加入任何回填：请先用「添加回填文件…/添加回填目录…/从剪贴板导入」"
                "把外部 chat 的回复加进来。\n\n回填 = ①第三节完整 BibTeX 代码块 "
                "②附一 JSON；两部分都要交（可放同一文件，也可多个文件一起导入）。")
            return
        try:
            call = self.coop_ph2_call(self._coop_sel.paths)
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        self.on_run(self, call)          # MainWindow.start(tab, call)：显式传入第二段调用

    def _set_coop_warning(self, text: str) -> None:
        """0 文献回填的橙色警示（空串 = 收起）。"""
        self.v_coop_warn.set(text or "")
        if text:
            self.coop_warn_label.pack(anchor="w", pady=(2, 0))
        else:
            self.coop_warn_label.pack_forget()

    def on_activate(self) -> None:
        self.refresh_bibdb_hint()
        self._refresh_ai_hint()            # 批62：设置页可能刚改过端点，提示要刷新
        self._ensure_coop_stage()          # 切回本页时重新扫一次暂存目录（源稿可能改过）
        self._refresh_coop_buttons()
        self._refresh_coop_replies()

    def extra_args(self) -> dict:
        args = {"compile_pdf": self.v_compile.get(), "clean": self.v_clean.get(),
                "verify_refs": self.v_refs.get(), "source_pdf": self.v_srcpdf.get(),
                "name": self.v_name.get().strip(), "author": self.v_author.get().strip(),
                "vol": self.v_vol.get().strip(), "issue": self.v_issue.get().strip(),
                "year": self.v_year.get().strip(), "pages": self.v_pages.get().strip(),
                "received": self.v_received.get().strip(),
                "editor": self.v_editor.get().strip(), "grants": self.v_grants.get().strip()}
        # 第 132 条：引用库设置 → convert 参数。默认返回空 dict（一个参数都不加）。
        args.update(S.bibdb_convert_args(self.cfg))
        # 第五十五批：直连 API 的 token 预算。两段式 = 外部 chat（零费用）→ 一个都不传；
        # 非 coop 时也只在「配置了 API 且值 != 引擎默认」才给键（默认 argv 逐字节不变）。
        args.update(S.ai_budget_convert_args(self.cfg, coop=self.coop_enabled()))
        # 批60：全文 AI 核查（直连 API）—— **会话态**（每次新开窗口都默认不勾，不落盘）。
        # 只把「用户勾没勾」交给 `convert_call`；「是否真的进 argv」（仅 ⑪ 直连路径）在
        # engine.py 的拼装点一处收口，coop/普通转换天然不受影响。
        args["llm_fulltext"] = bool(self.v_llm_fulltext.get())
        # 批62：初排页不再暴露独立任务书勾选；⑪ 直连 AI 入口 = 「用 AI 处理参考文献」。
        # `verify_refs` 仍原样传（字段保留兼容，界面恒 false）；`use_ai` 让拼装点内部
        # 等价成 --verify-refs（普通转换两者皆 false → argv 与入口收敛前「不勾」时逐字节相同）。
        args["use_ai"] = bool(self.v_ai.get())
        return args
