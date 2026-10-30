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
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bibdb_ui as B  # noqa: E402  第 132 条：引用库 5 页（拆文件，见 bibdb_ui.py 抬头）
import clean_logic as _clean  # noqa: E402  B-281：清理三模式的纯逻辑（无 tkinter）
import coop as C  # noqa: E402  第 55/56 批：两段式 AI 协同初排的纯逻辑（无 tkinter）
import engine as E  # noqa: E402
import settings as S  # noqa: E402
import theme  # noqa: E402

# ── B-230 拆分：13 个 tab 页搬进 `tabs/`（纯搬运，行为零变化）──────────────
# 实例化仍在 MainWindow 里，**属性名不变**（`self.info_tab` 等被 test_bridge 与
# `window.tab_texts()` 依赖）。`tabs/__init__.py` 负责把 sources-win 放上 sys.path。
from tabs import (checkbib as _cb, clean as _cl, compileall as _ca, convert as _ct,
                  infocheck as _ic, merge as _mg, proofcheck as _pc,
                  settings_tab as _st, toc as _tc)
from tabs.base import APP_TITLE, LOG_LINE_LIMIT, LOG_PLACEHOLDER, LOG_TRIM_CHUNK, TabPage


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        # 主题必须**先**套：clam + 色板 + 字号（见 theme.py 抬头；用户 2026-10-17 拍板
        # vista → clam —— vista 的控件由系统绘制，改不动颜色，做不出与 mac 同一套观感）。
        theme.apply(self)
        self.geometry("1000x700+340+100")   # 侧栏吃掉 ~184px，最小宽度跟着提（UI 方案候选 B）
        self.title(APP_TITLE)
        self.cfg = S.load()
        # U4 日志偏好（默认摘要 = 原始层折叠）：`settings.DEFAULTS` 里已有这个键，
        # 读回后写进 cfg —— 否则设置页点一次「保存」会把整个 cfg 写回、抹掉这份偏好。
        self.cfg["logShowRaw"] = bool(self.cfg.get("logShowRaw", False))
        # ⚠️ 设置页的「引擎目录」必须**真的生效**：`engine.py` 只认环境变量 `SLCN_ENGINE`，
        #    从前这里写进 cfg 却全仓无人读 → 自动定位失败时，双击 exe 的编辑在 GUI 里
        #    **没有任何自救入口**（他们不会去设环境变量）。2026-10-17 审计实测（M5）。
        #    用 `setdefault`：显式设过环境变量的人仍然优先，不被界面覆盖。
        _saved_engine = self.cfg.get("engineDir", "").strip()
        if _saved_engine:
            os.environ.setdefault("SLCN_ENGINE", _saved_engine)
        self._proc: subprocess.Popen | None = None
        self._running_tab = None
        self._t0 = 0.0
        self._phase = ""
        self._q: queue.Queue[str] = queue.Queue()
        self._busy = False
        self._current = 0
        #: 完整日志行 `(显示文本, 级别 tag, 是否摘要行)` —— 视图只画其中一部分，
        #: 但复制/保存/切换模式都要用**全量**（U4：摘要只是默认视图，不是数据）。
        self._log_lines: list[tuple[str, str, bool]] = []
        self._log_folded = 0                    # 摘要模式下折叠了几行（工具条显示）
        self._log_placeholder_active = False    # 日志框里现在是不是那行灰占位

        # ── 分组侧边栏（UI 方案候选 B；2026-10-17 用户批准）──
        # 为什么不再用 9 个平级标签：WinUI 说「左侧导航适合 5–10 个**同等重要**的分类」，
        # HIG 说侧栏「层级不超过两层」—— 我们的 9 个其实是三层（单篇生产 / 整期装配 /
        # 维护设置），却摆成 9 个平级项。分组后一层是组、二层是页，正好两级。
        # 选 Treeview 而不是 Notebook：Notebook 的标签**不能分组**（只能用顺序暗示）。
        self.body = ttk.Frame(self)
        self.body.pack(expand=1, fill="both")
        self.sidebar = ttk.Frame(self.body, width=184)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)          # 固定宽度：别被长标签名撑开
        # B-274（用户 m02462）：侧栏底部常驻「版本 x.y.z」，**只看版本号**。
        # 先 pack 底部条再 pack 导航：Tk 按 pack 顺序分配空间，这样 label 不会被 nav 挤掉。
        self.version_label = ttk.Label(self.sidebar, text="版本 …", style="Muted.TLabel",
                                       anchor="w")
        self.version_label.pack(side="bottom", fill="x", padx=12, pady=(4, 8))
        self.nav = ttk.Treeview(self.sidebar, show="tree", selectmode="browse")
        self.nav.pack(fill="both", expand=True, padx=(8, 4), pady=8)
        self.content = ttk.Frame(self.body)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.convert_tab = _ct.ConvertTab(self.content, "初排转换", self.start, self.stop,
                                      cfg=self.cfg, ai_knobs_sink=self._set_ai_knobs)
        # _cb.CheckBibTab 多两样：「入藏到引用库」按钮要写运行日志 + 后台线程回传（第 132 条）。
        self.checkbib_tab = _cb.CheckBibTab(self.content, "文献回读校验", self.start, self.stop,
                                        log=self.log, post=self.post, cfg=self.cfg)
        self.merge_tab = _mg.MergeTab(self.content, "合并文件", self.start, self.stop)
        self.proof_tab = _pc.ProofCheckTab(self.content, "一校核查", self.start, self.stop)
        self.info_tab = _ic.InfoCheckTab(self.content, "检查tex信息", self.start, self.stop)
        self.toc_tab = _tc.TocTab(self.content, "生成目录", self.start, self.stop)
        self.compile_tab = _ca.CompileAllTab(self.content, "编译全部", self.start, self.stop)
        self.clean_tab = _cl.CleanTab(self.content, "删除临时文件", self.start, self.stop)
        # ── 第 132 条：「引用库」5 页（与 mac 同口径；数据源是 Engine/bibdb.py）──
        # 页面在 bibdb_ui.py（main.py 已经 1300+ 行，别继续膨胀）；这里只建对象、排进侧栏。
        self.bibdb_stats_tab = B.BibDbStatsPage(self.content, app=self)
        self.bibdb_query_tab = B.BibDbQueryPage(self.content, app=self)
        self.bibdb_import_tab = B.BibDbImportPage(self.content, app=self)
        self.bibdb_review_tab = B.BibDbReviewPage(self.content, app=self)
        self.bibdb_settings_tab = B.BibDbSettingsPage(self.content, app=self)
        self.settings_tab = _st.SettingsTab(self.content, self.cfg, lambda: None,
                                        post=self._q.put)
        self.tabs = [self.convert_tab, self.checkbib_tab, self.merge_tab, self.proof_tab,
                     self.info_tab, self.toc_tab, self.compile_tab, self.clean_tab,
                     # 第 132 条：引用库分组（8..12）；「设置」仍是组外底部（13）
                     self.bibdb_stats_tab, self.bibdb_query_tab, self.bibdb_import_tab,
                     self.bibdb_review_tab, self.bibdb_settings_tab,
                     self.settings_tab]
        for tab in self.tabs:                     # 全部叠在同一格里，切页 = tkraise
            tab.grid(row=0, column=0, sticky="nsew")

        #: 分组 → 页（顺序就是侧栏里的顺序；`设置` 单独放在最下面，照 WinUI 的惯例）
        #: 第 132 条起多一个与另两组**平行**的顶级分组「引用库」（按使用顺序排：
        #: 看概况 → 查库 → 入藏 → 待裁决 → 设置）。设置仍是组外的配置入口。
        self.nav_groups = [("单篇稿件", (0, 1, 3)),
                           ("整期装配", (4, 5, 6, 7, 2)),
                           ("引用库", (8, 9, 10, 11, 12))]
        for title, idxs in self.nav_groups:
            group = self.nav.insert("", "end", text=title, open=True)
            for i in idxs:
                self.nav.insert(group, "end", text=self.tabs[i].name, iid=f"tab:{i}")
        self.nav.insert("", "end", text="设置", iid=f"tab:{len(self.tabs) - 1}")
        self.nav.bind("<<TreeviewSelect>>", self._on_nav)
        self.nav.selection_set("tab:0")
        self.show_tab(0)

        # 键盘快捷键（UI 方案 §5）：Ctrl+R 运行当前页 / Esc 停止 / Ctrl+, 打开设置。
        # 回车不绑（侧栏/按钮各有默认行为，绑了反而与输入框冲突）。
        self.bind_all("<Control-r>", lambda _e: self._hotkey_run())
        self.bind_all("<Control-R>", lambda _e: self._hotkey_run())
        self.bind_all("<Escape>", lambda _e: self._hotkey_stop())
        self.bind_all("<Control-comma>", lambda _e: self._hotkey_settings())

        # `tk.Text` 保留：Tk 没有 ttk 对等物。颜色全部从 `theme.COLOR` 取（不写死）。
        self.output = tk.Text(self, height=18, wrap="none", font=theme.mono_font(),
                              background=theme.COLOR["text_bg"],
                              foreground=theme.COLOR["fg"],
                              insertbackground=theme.COLOR["fg"],
                              selectbackground=theme.COLOR["accent"],
                              selectforeground=theme.COLOR["on_accent"])
        self.output.pack(side="bottom", fill="both", expand=False)
        self.output.configure(state="disabled")
        # 级别色（UI 方案 §2-8：普通/琥珀/红三档；颜色**不是唯一**区分手段 ——
        # 行首本来就有 ✅/⚠/❌ 符号，色弱用户也能读）
        self.output.tag_configure("err", foreground=theme.COLOR["error"])
        self.output.tag_configure("warn", foreground=theme.COLOR["warn"])
        self.output.tag_configure("ok", foreground=theme.COLOR["ok"])
        # 灰字：空状态占位 / 辅助说明（不是"日志内容"，复制与保存都不含它）
        self.output.tag_configure("muted", foreground=theme.COLOR["muted"])
        # 空状态（观察③）：窗口刚起来、日志还空着时先给一句灰字，第一条日志到达即让位
        self._insert_placeholder()

        # 日志工具条：自动滚动（上翻看旧日志时别被强拉到底）、摘要/原始、跳到最新、
        # 复制全部、保存、清空、当前阶段
        self.log_tools = ttk.Frame(self)
        self.log_tools.pack(side="bottom", fill="x", padx=10, pady=(2, 0))
        self.v_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.log_tools, text="自动滚动", variable=self.v_autoscroll).pack(side="left")
        # U4：摘要 / 原始二选一（默认摘要）。取值必须写 **0/1**：BooleanVar 的 Tcl 值是
        # "0"/"1"，value 写成 False/True 初始态一个都选不中（2026-10-17 实测）。
        self.v_log_raw = tk.BooleanVar(value=bool(self.cfg.get("logShowRaw", False)))
        for _text, _value in (("摘要", 0), ("原始", 1)):
            ttk.Radiobutton(self.log_tools, text=_text, value=_value,
                            variable=self.v_log_raw, style="Toolbutton",
                            command=self._on_log_mode).pack(side="left", padx=(8, 0))
        self.folded_label = ttk.Label(self.log_tools, text="", style="Muted.TLabel")
        self.folded_label.pack(side="left", padx=(8, 0))
        ttk.Button(self.log_tools, text="跳到最新",
                   command=lambda: self.output.see("end")).pack(side="left", padx=6)
        ttk.Button(self.log_tools, text="复制全部", command=self._copy_log).pack(side="left")
        ttk.Button(self.log_tools, text="保存日志…", command=self._save_log).pack(
            side="left", padx=6)
        # 「清空」只清日志（内存与视图一起清，见 clear_log）—— 与 mac 的 LogPane 同一个按钮
        ttk.Button(self.log_tools, text="清空", command=self.clear_log).pack(side="left")
        self.phase_label = ttk.Label(self.log_tools, text="", style="Muted.TLabel")
        self.phase_label.pack(side="left", padx=12)

        self.log(f"{APP_TITLE}")
        self.log(E.status().describe())
        self.log("运行日志：\n")
        # B-274：版本号后台算（冻结形态要起一次 `SLCNEngine.exe --version`，别卡启动），
        # 算完经 `post(("__VERSION__", …))` 回主线程贴到侧栏底部。
        threading.Thread(target=self._load_version_label, daemon=True).start()
        self.after(80, self._drain)

    # ── 侧边栏导航（UI 方案候选 B）──
    def tab_texts(self) -> list[str]:
        """全部页的名字（按侧栏顺序）—— 测试与自检用（Notebook 换成 Treeview 后的稳定接口）。"""
        return [t.name for t in self.tabs]

    def _set_ai_knobs(self, thinking: str, maxtok: str) -> None:
        """初排转换页的两个 AI 旋钮 → cfg["api"]（运行时就地读，不用先保存）。"""
        api = self.cfg.setdefault("api", {})
        api["thinking"] = thinking
        api["maxTokens"] = maxtok

    def current_tab(self):
        """当前显示的页（快捷键要操作它）。"""
        return self.tabs[self._current]

    def _hotkey_run(self) -> str:
        tab = self.current_tab()
        if isinstance(tab, TabPage) and not self._busy:
            self.start(tab)
        return "break"

    def _hotkey_stop(self) -> str:
        if self._busy:
            self.stop()
        return "break"

    def _hotkey_settings(self) -> str:
        self.show_tab(len(self.tabs) - 1)
        return "break"

    def show_tab(self, index: int) -> None:
        """切到第 `index` 页（侧栏选中态与内容一起变，并触发该页的 `on_activate`）。"""
        self._show(index)
        want = f"tab:{index}"
        if self.nav.selection() != (want,):          # 已经有就别再 set（见 _on_nav 的注释）
            self.nav.selection_set(want)

    def _show(self, index: int) -> None:
        """切页的唯一实现：更新 `_current`、tkraise、调页面钩子。

        `_current` 必须在这里同步：从前只有 `show_tab()` 写它，鼠标点侧栏走
        `_on_nav` 时 `_current` 停在旧页 —— 于是 Ctrl+R 跑的是**上一页**（第 132 条
        一并修掉）；「库概况/待裁决」也靠 `on_activate` 在切到本页时读库/刷新清单。
        """
        self._current = index
        tab = self.tabs[index]
        tab.tkraise()
        hook = getattr(tab, "on_activate", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:  # noqa: BLE001  钩子出错不许把切页弄崩
                self.log(f"❌ 切到「{tab.name}」时出错：{exc}")

    def _on_nav(self, _event=None) -> None:
        """侧栏选中 → 切内容。⚠️ **这里绝不回写** `selection_set`。

        坑：`selection_set()` 自己也会触发 `<<TreeviewSelect>>` —— 在事件处理器里回写
        就等于自我触发，Tk 的事件循环原地打转、建窗阶段直接卡死（2026-10-17 实测，
        桥接测试超时 180 s 才暴露）。所以：事件里只 `tkraise`，选中态由用户点击 /
        `show_tab()` 负责。
        """
        sel = self.nav.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("tab:"):
            return
        index = int(iid.split(":", 1)[1])
        # show_tab() 会先写 _current 再 set 选中态，事件回来时 index == _current
        # → 只 tkraise，不重复触发 on_activate（避免「库概况」一次切页读两遍库）。
        if index == self._current:
            self.tabs[index].tkraise()
        else:
            self._show(index)

    # ── 后台线程 → 主线程的唯一通道（第 132 条）──
    def post(self, item) -> None:
        """把一条队列消息交给 `_drain`（工作线程只许调它，绝不许直接碰控件）。

        `bibdb_ui.run_async` 用 `("__CALL__", callback, args)` 把「后台跑完引擎后的
        收尾」送回主线程执行 —— Tkinter 不是线程安全的（见 `_worker` 的注释）。
        """
        self._q.put(item)

    def _load_version_label(self) -> None:
        """后台取引擎版本号并回主线程贴到侧栏底部（B-274）。

        取不到时 `E.engine_version()` 已兜底返回「未知」；这里再不碰控件，
        只经队列把 `("__VERSION__", "版本 x.y.z")` 交给 `_drain`。
        """
        try:
            text = f"版本 {E.engine_version()}"
        except Exception:  # noqa: BLE001  版本号取不到不该让外壳起不来
            text = "版本 未知"
        self.post(("__VERSION__", text))

    # ── 日志 ──
    @staticmethod
    def _level_tag(text: str) -> str:
        """按内容判级别（与 mac 侧同一套词表：前缀符号 + 关键中文）。
        颜色只是辅助 —— 行首符号本来就在，色弱可读。"""
        if any(k in text for k in ("❌", "程序错误", "未找到", "启动失败")):
            return "err"
        if any(k in text for k in ("⚠", "警告")):
            return "warn"
        if any(k in text for k in ("✅", "完成。")):
            return "ok"
        return ""

    def log(self, text: str) -> None:
        """追加一行日志：**永远进内存**（`_log_lines`），显示与否看当前是摘要还是原始。

        摘要判据在 `engine.is_summary_line`（与 mac 侧同一份词表 + 同一份夹具）。
        ⚠️ 判定要用**引擎原文**：下面盖相对时间戳（`+0:03.2s  `）之后，
        「`▶` 开头 / `[3/25]` 开头」这两类前缀判据就对不上了。
        """
        is_summary = E.is_summary_line(text)
        # 相对时间戳（UI 方案 §2-8）：运行中每行前面加 `+0:03.2`，方便回看"哪一步慢"；
        # 未运行时（启动信息等）不加，保持干净。绝对时间对排错没用，相对时间才有。
        if self._busy and self._t0:
            text = f"+{time.monotonic() - self._t0:5.1f}s  {text}"
        tag = self._level_tag(text)
        self._log_lines.append((text, tag, is_summary))
        if not is_summary and text.strip():
            self._log_folded += 1
        if self._trim_log():             # 超上限截断过 → 整屏重画（含这行），别让视图与内存对不上
            self._render_log()
            return
        if self._log_placeholder_active:     # 空状态让位：占位只代表「还没有任何一行」
            self._hide_placeholder()
        if self.v_log_raw.get() or is_summary:
            self.output.configure(state="normal")
            self.output.insert("end", text + "\n", tag)
            if self.v_autoscroll.get():          # 上翻看旧日志时别把人强拉到底
                self.output.see("end")
            self.output.configure(state="disabled")
        self._refresh_folded_label()

    # ── 日志：摘要 / 原始两层（U4）──
    def _render_log(self) -> None:
        """按当前模式把内存里的**完整**日志重画进日志框：摘要模式只画摘要行。

        切换模式、截断、清空都走这里 —— 除了 `log()` 的实时追加，日志框没有别的数据来源。
        """
        show_raw = bool(self.v_log_raw.get())
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        folded = 0
        for text, tag, is_summary in self._log_lines:
            if show_raw or is_summary:
                self.output.insert("end", text + "\n", tag)
            elif text.strip():
                folded += 1
        self._log_folded = folded
        self._log_placeholder_active = False
        if not self._log_lines:                    # 空状态（观察③）
            self.output.insert("end", LOG_PLACEHOLDER + "\n", "muted")
            self._log_placeholder_active = True
        self.output.configure(state="disabled")
        self._refresh_folded_label()
        if self.v_autoscroll.get():
            self.output.see("end")

    def _on_log_mode(self) -> None:
        """摘要 / 原始切换：立刻重画，并把偏好写回配置（下次启动沿用，AUDIT U4）。"""
        self.cfg["logShowRaw"] = bool(self.v_log_raw.get())
        try:
            S.save(self.cfg)
        except OSError:
            pass            # 配置目录只读时别弹窗打断看日志：本次切换照样生效
        self._render_log()

    def _refresh_folded_label(self) -> None:
        """工具条上「已折叠 N 行原始日志」：只有摘要模式且真的折叠了才写。"""
        if self.v_log_raw.get() or self._log_folded <= 0:
            self.folded_label.config(text="")
        else:
            self.folded_label.config(text=f"已折叠 {self._log_folded} 行原始日志")

    def _trim_log(self) -> bool:
        """超过上限时丢掉最旧的行（一次丢一批，避免每来一行就整屏重画）。返回是否丢了。"""
        over = len(self._log_lines) - LOG_LINE_LIMIT
        if over < 0:
            return False
        del self._log_lines[:over + LOG_TRIM_CHUNK]
        return True

    def _full_log_text(self) -> str:
        """**完整日志**文本（含摘要模式下折叠的原始行）—— 复制/保存一律用它。

        与视图模式无关：摘要只是默认视图（见 U4），留证据/发给人看时不能漏行。
        """
        return "\n".join(text for text, _tag, _is_summary in self._log_lines)

    def _insert_placeholder(self) -> None:
        """空状态（观察③）：日志区还没有任何一行时，给一句灰字，别留一片空白。"""
        self.output.configure(state="normal")
        self.output.insert("end", LOG_PLACEHOLDER + "\n", "muted")
        self.output.configure(state="disabled")
        self._log_placeholder_active = True

    def _hide_placeholder(self) -> None:
        """第一条真实日志到达时把占位删掉 —— 占位**从不**进复制/保存的内容。"""
        if not self._log_placeholder_active:
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self._log_placeholder_active = False

    def clear_log(self) -> None:
        """清空日志：**内存与视图一起清**（否则「复制全部」还会把清掉的行拿出来）。"""
        self._log_lines.clear()
        self._log_folded = 0
        self._render_log()                  # 空 → 自动恢复那行灰占位

    def _save_log(self) -> None:
        """把**整份**运行日志存成 .txt 并打开它（「打开日志文件」的可行形态：
        平时不落盘，需要留证据/发给人看时再存）。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = fd.asksaveasfilename(
            title="保存运行日志", defaultextension=".txt",
            initialfile=f"运行日志-{stamp}.txt",
            filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                # 完整日志（含摘要模式下折叠的原始行），与视图模式无关；占位不在内
                fh.write(self._full_log_text())
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"保存日志失败：{e}")
            return
        self.log(f"（日志已保存：{path}）")
        try:
            if os.name == "nt":
                os.startfile(path)                    # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", path])
        except Exception:  # noqa: BLE001  打不开也不致命（路径已经写进日志）
            pass

    def _copy_log(self) -> None:
        """复制**完整日志**（含摘要模式下折叠的原始行）—— 与当前视图模式无关。"""
        try:
            self.clipboard_clear()
            self.clipboard_append(self._full_log_text())
            self.log("（已复制全部运行日志到剪贴板）")
        except tk.TclError:
            pass

    def _drain(self) -> None:
        """把工作线程放进队列的日志搬到界面上（Tkinter 不是线程安全的）。"""
        try:
            while True:
                line = self._q.get_nowait()
                if line == "__DONE__":          # 内部信号，不是日志
                    self._proc = None
                    self._running_tab = None
                    self._set_busy(False)
                    continue
                if isinstance(line, tuple) and line[0] == "__VERSION__":
                    # B-274：版本号后台算完（失败时是「版本 未知」）→ 贴到侧栏底部
                    self.version_label.config(text=str(line[1]))
                    continue
                if isinstance(line, tuple) and line[0] == "__REPORT__":
                    _tag, tab, payload = line
                    if isinstance(tab, _ic.InfoCheckTab):
                        tab.show_report(payload)
                    continue
                if isinstance(line, tuple) and line[0] == "__KEYSTATUS__":
                    self.settings_tab.key_status.config(text=str(line[1]))
                    continue
                if isinstance(line, tuple) and line[0] == "__CALL__":
                    # 第 132 条：引用库页面在后台线程跑完引擎后，把「结果回填」送回主线程。
                    # 回调里只碰控件；它自己抛的异常在这里兜住，别让 Tk 的事件循环断掉。
                    _tag, callback, args = line
                    try:
                        callback(*args)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"❌ 界面回调出错：{exc}")
                    continue
                if isinstance(line, tuple) and line[0] == "__DIAG__":
                    # B-285b：诊断卡（红/中性）或「本次不出卡」→ 隐藏旧卡。
                    _tag, tab, level, report, log_path, pack, tail = line
                    if tab is not None:
                        try:
                            if level == "hide":
                                tab.hide_diagnosis()
                            else:
                                tab.show_diagnosis(level, report, log_path, pack, tail)
                        except Exception as exc:  # noqa: BLE001
                            self.log(f"❌ 诊断卡显示失败：{exc}")
                    continue
                if isinstance(line, tuple) and line[0] == "__RESULT__":
                    _tag, tab, text, path, seconds, undo = line
                    if isinstance(undo, tuple) and len(undo) == 2:
                        _label, trash_dir = undo
                        tab.show_result(text, path, seconds,
                                        undo=(_label, lambda d=trash_dir: self.start(
                                            tab, E.cleantemp_call(folder=tab.path(),
                                                                  restore_trash=d))))
                    else:
                        tab.show_result(text, path, seconds)
                    continue
                if isinstance(line, tuple) and line[0] == "__TOC_RESULT__":
                    _tag, tab, payload, code, tail = line
                    try:
                        tab.show_delivery(payload, code, tail)
                    except Exception as exc:  # noqa: BLE001  结果区出错不许弄断事件循环
                        self.log(f"❌ 生成目录结果区显示失败：{exc}")
                    continue
                if isinstance(line, tuple) and line[0] == "__COOP__":
                    # 第五十五批：两段式协同跑完后，由工作线程把「发现到的暂存目录/
                    # 建议摘要」送回主线程再更新界面（Tk 控件只在主线程碰）。
                    _tag, tab, payload, code = line
                    try:
                        tab.apply_coop_result(payload, code)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"❌ 协同状态界面更新失败：{exc}")
                    continue
                self.log(line)
                if self._busy:
                    self._phase = line.strip()          # 当前阶段 = 最新一行（引擎的阶段行）
        except queue.Empty:
            pass
        # 秒表 + 当前阶段：运行中每 80ms 刷一次（进度"在动"要看得见 —— UI 方案第 2 件）
        if self._busy and self._running_tab is not None:
            self._running_tab.show_running(time.monotonic() - self._t0, self._phase)
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
        if isinstance(tab, _ct.ConvertTab):
            out = fd.askdirectory(title="选择输出根目录（产物写到 <输出目录>/<作者名>/）")
            if not out:
                raise ValueError("没有选输出目录")
            if tab.coop_enabled():
                # 第五十五批：两段式第一段。**记住输出根** —— 第二段复用，不再弹目录选择。
                tab.set_coop_out(out)
                return tab.coop_ph1_call(out)
            return E.convert_call(source=path, out=out, **opts)
        if isinstance(tab, _ic.InfoCheckTab):
            # ⚠️ 不带 `--preview`：这一次跑还要把《信息检查…txt/.json》落到目录里
            #    （「生成目录」页要读它）。表格由跑完之后的 `__REPORT__` 那条路填
            #    —— 从前这里 0 处调用 `show_report()`，于是表格永远「（还没检查）」，
            #    而引擎那一轮其实跑得好好的（2026-10-17 审计实测，H2）。
            return E.infocheck_call(folder=path)
        if isinstance(tab, _tc.TocTab):
            tab.save_settings(path)          # 先把本期设置写进引擎要读的 json
            text_only = bool(opts.get("text_only"))
            tab.text_only_run = text_only
            result_json = "" if text_only else tab.prepare_result_json()
            return E.toc_call(report=path, result_json=result_json, **opts)
        if isinstance(tab, _pc.ProofCheckTab):
            return E.proofcheck_call(tex=path, **opts)
        if isinstance(tab, _ca.CompileAllTab):
            return E.compileall_call(folder=path, **opts)
        if isinstance(tab, _cl.CleanTab):
            # UI 方案第 3 件：**先预览再动手**，且动手的方式是"移到回收目录"（可撤销）。
            # B-281：三种清理模式与 mac `DeleteTempFeature.CleanMode` 对齐（aux/delivery/
            # workspace）；勾「先预演」= 只预演不写（`--dry-run`），勾掉才真动手。
            mode = tab.mode()
            dry = bool(tab.v_dry.get())
            if not dry:
                tab.confirm_trash(mode)      # 取消 → ValueError（什么都没动）
            return E.cleantemp_call(folder=path, mode=mode, dry_run=dry,
                                    trash=(mode == _clean.DEFAULT_MODE))
        if isinstance(tab, _cb.CheckBibTab):
            merge_into = ""
            if opts.get("merge_into_same_dir"):
                target = os.path.join(os.path.dirname(path), "reference.bib")
                if not os.path.exists(target):
                    raise ValueError(f"同目录下没有 reference.bib：{target}")
                merge_into = target
            return E.checkbib_call(returned=path, merge_into=merge_into,
                                   original=opts.get("original", ""),
                                   tex=opts.get("tex", ""), out=opts.get("out", ""))
        return E.merge_call(folder=path, **opts)

    def start(self, tab, call: E.EngineCall | None = None) -> None:
        if self._busy:
            messagebox.showinfo(APP_TITLE, "上一个任务还在跑，请等它结束或点「停止」。")
            return
        try:
            call = call or self._call_for(tab)
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
        # 批60：全文 AI 核查（直连 API）真的进了 argv（= 本次跑 ⑪ 直连通道）时，
        # 日志先讲清「会花钱 / report-only」；端点没配就更要提前说（引擎会 exit 2、零请求）。
        if "--llm-fulltext" in call.argv:
            if S.ai_configured(self.cfg):
                self.log("  全文核查 **直连 API**（--llm-fulltext）：正文按片发送到上面这个端点，"
                         "**会产生费用**；只生成《…_AI全文复核.md》核查报告，**不改产物**"
                         "（不改正文、不写 reference.bib）。")
            else:
                self.log("⚠ 已勾「全文 AI 核查（直连 API）」但端点未配置：引擎会**直接退出 2**"
                         "（不会发出任何请求）；请先到「设置」页填地址与模型名。")
        self._running_tab = tab
        self._t0 = time.monotonic()
        if isinstance(tab, _tc.TocTab):
            tab.clear_delivery()
        tab.hide_diagnosis()
        tab.show_running(0.0)
        self._set_busy(True)
        # ⚠️ 路径必须在**主线程**里取好再交给工作线程：Tk 控件不能跨线程碰 ——
        #    在工作线程里调 `tab.path()`（读 Entry）会 `RuntimeError: main thread is not
        #    in main loop`，worker 当场死掉 ⇒ 界面永远卡在「运行中」。
        #    这是我自己第一版 H2 修法踩的坑，靠**真驱动一次界面**才抓出来（2026-10-17）。
        path = tab.path()
        threading.Thread(target=self._worker, args=(call, tab, path), daemon=True).start()

    @staticmethod
    def _argv_value(argv: list[str], flag: str) -> str:
        """取命令行里 `flag` 后面的值（没有就返回空串；不猜、不抛）。"""
        try:
            index = argv.index(flag)
        except ValueError:
            return ""
        return argv[index + 1] if index + 1 < len(argv) else ""

    @staticmethod
    def _coop_payload(call: E.EngineCall, phase: str, code: int) -> dict:
        """跑完两段式的一步后，把界面要的状态从**引擎写的状态文件**里读出来。

        发现规则与引擎同一口径（`coop.py: discover_stage`：source + out_root 都匹配）。
        第一段失败但暂存目录已写（如编译失败退出 2）时仍返回 stage —— 界面会显示
        「失败」但允许打开任务书/暂存目录，**不把暂存目录当交付目录**。
        """
        out = MainWindow._argv_value(call.argv, "--out")
        source = MainWindow._argv_value(call.argv, "--source")
        stage_dir = MainWindow._argv_value(call.argv, "--coop-stage")
        stage = C.load_stage(stage_dir) if stage_dir else None
        if stage is None:
            stage = C.discover_stage(out, source=source)
        payload: dict = {"phase": phase, "stage": stage}
        if stage is not None and phase == "ph2" and code == 0:
            # 批56：成功摘要以引擎写进状态文件的 stats 为准（旧状态回退 record/keys）；
            # 0 文献回填的橙色警告也在这里算（只对 done 的暂存目录有意义）。
            payload["summary"] = C.summarize_stage(stage)
            payload["suggestions"] = C.suggestions_summary(stage.stage_dir, stage.stem)
        if stage is None:
            payload["error"] = ("没有发现暂存目录（第一段可能没跑成，见上面的日志）"
                                if phase == "ph1"
                                else "暂存目录不见了（可能已被移动/删除）")
        return payload

    def _worker(self, call: E.EngineCall, tab=None, path: str = "") -> None:
        last = {"line": ""}
        tail: list[str] = []                 # 失败时给界面看「日志尾部」（只留最后几行）
        run_lines: list[str] = []            # B-285b：诊断落盘/复制包用的整段输出

        def _on_line(line: str) -> None:
            self._q.put(line)
            if line.strip():
                last["line"] = line          # 契约行 = stdout 最后一行（ENGINE_API 五）
                tail.append(line)
                if len(tail) > 8:
                    del tail[0]
            run_lines.append(line)
            if len(run_lines) > 4000:
                del run_lines[:500]

        # 第五十五批：两段式协同**绝不**把 API 配置传给子进程（外部 chat 通道零上传/
        # 零费用；与 mac `CoopFeature.prepare` 强制 useAI=false 同一口径）。
        coop_phase = ""
        if isinstance(tab, _ct.ConvertTab):
            if "--coop-taskbook-only" in call.argv:
                coop_phase = "ph1"
            elif "--coop-replies" in call.argv:
                coop_phase = "ph2"
        try:
            code = E.run_call(call, on_line=_on_line,
                              api=None if coop_phase else self.cfg.get("api"),
                              on_proc=lambda p: setattr(self, "_proc", p),
                              notes=self.cfg.get("notesRepoPath", ""))
        except Exception as e:  # noqa: BLE001
            self._q.put(f"❌ 启动失败：{e}")
            code = -1
        if code == 0 and isinstance(tab, _ic.InfoCheckTab) and path:
            payload = self._info_report_payload(path)
            if payload:
                self._q.put(("__REPORT__", tab, payload))
        # 生成目录：把引擎写的结果 JSON 读回来（4 路径/模板来源/页数），读完即删
        toc_payload: dict = {}
        if isinstance(tab, _tc.TocTab) and tab.result_json_path:
            toc_payload = E.read_toc_result(tab.result_json_path)
            try:
                os.remove(tab.result_json_path)
            except OSError:
                pass
            tab.result_json_path = ""
        coop_payload: dict | None = None
        result = E.result_text(call.entry, code)
        if isinstance(tab, _tc.TocTab) and code == 0:
            result = ("✅ 生成并交付完成" if not tab.text_only_run
                      else "✅ 已生成 tex（仅生成，未落位/未编译）")
        if coop_phase:
            try:
                coop_payload = self._coop_payload(call, coop_phase, code)
            except Exception as e:  # noqa: BLE001  读状态失败不许把 worker 弄死
                coop_payload = {"phase": coop_phase, "stage": None,
                                "error": f"读取协同状态失败：{e}"}
            if code != 0:
                detail = E.output_tail(tail, count=2)
                coop_payload["error"] = (f"退出码 {code}｜{detail}" if detail
                                         else f"退出码 {code}")
            elif coop_payload.get("stage") is not None:
                result = ("✅ 初排完成 · 待人工核实（未交付）" if coop_phase == "ph1"
                          else "✅ 已交付")
        # ⚠️ 退出码 1 在引擎里常常是「有结果」而不是失败（INVARIANTS 三.2）：
        #    checkbib 并回 / proofcheck 有命中 / compileall 有失败 / toc 只出一份。
        #    从前一律写「❌ 失败（退出码 1）」，与上一行引擎自己打的「完成。…」自相矛盾
        #    （2026-10-17 审计实测，H3）。文案与 mac 各页逐字对齐，且在 bridge 里有守卫。
        self._q.put(f"{result}（退出码 {code}）")
        if tab is not None:
            self._post_diagnosis(tab, call.entry, code, run_lines)
            path = E.artifact_path(last["line"]) if code == 0 else ""
            if isinstance(tab, _tc.TocTab):
                toc_dir = str(toc_payload.get("tocDir") or "").strip()
                if toc_dir:
                    path = toc_dir
            undo = None
            # 清理跑完 → 给一个「撤销清理」按钮：把回收目录里的东西搬回去。
            # B-281：判定交给 `clean_logic.undo_available`（三种模式 + dry-run/永久删/
            # 清空回收站都不给撤销），不再只认 aux 的 `--trash`。
            if (isinstance(tab, _cl.CleanTab) and code == 0 and path
                    and _clean.undo_available(call.argv)):
                undo = ("撤销清理", path)
            self._q.put(("__RESULT__", tab, result,
                         path, time.monotonic() - self._t0, undo))
            if isinstance(tab, _tc.TocTab):
                self._q.put(("__TOC_RESULT__", tab, toc_payload, code,
                             E.output_tail(tail, count=2) if code not in (0, 1) else ""))
            if coop_payload is not None:
                self._q.put(("__COOP__", tab, coop_payload, code))
        self._q.put("__DONE__")

    def _post_diagnosis(self, tab, entry: str, code: int, lines: list[str]) -> None:
        """B-285b：按退出码决定出不出卡；出卡就落日志 → 调 diagnose.py → 拼本地诊断包。

        ⚠️ 全程在 worker 线程：只碰纯函数与 ``self._q``，不碰任何 Tk 控件。
        旧引擎（没有 diagnose.py）或诊断本身失败 → ``report=None``，仍出卡（回退口径），
        绝不阻断运行结果的主流程。
        """
        level = E.diagnosis_level(entry, code)
        if not level:
            self._q.put(("__DIAG__", tab, "hide", None, "", "", []))
            return
        log_path = ""
        try:
            log_path = E.save_diagnosis_log(entry, lines)
        except Exception as exc:  # noqa: BLE001  写缓存失败也不阻断
            self._q.put(f"⚠️ 诊断日志保存失败（不影响结果）：{exc}")
        report = None
        if log_path:
            try:
                dcall = E.diagnose_call(entry, code, log_path)
                out: list[str] = []
                dcode = E.run_call(dcall, on_line=out.append)
                if dcode == 0:
                    report = E.parse_diagnosis("\n".join(out))
            except Exception:  # noqa: BLE001  旧引擎/诊断崩溃 → 回退
                report = None
        try:
            pack = E.diagnosis_pack(
                entry=entry, exit_code=code, level=level, report=report,
                log_tail=[str(x) for x in lines][-10:],
                engine_version=E.engine_version(),
            )
        except Exception as exc:  # noqa: BLE001
            pack = ""
            self._q.put(f"⚠️ 诊断包生成失败（不影响结果）：{exc}")
        self._q.put(("__DIAG__", tab, level, report, log_path, pack,
                     [str(x) for x in lines][-10:]))

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


def _enable_dpi_awareness() -> None:
    """Windows 高分屏：声明 **System DPI Aware**。

    不做的话系统把整个窗口当位图拉伸 → 高分屏上**发虚**（Microsoft 文档原话：
    "Windows will stretch the application bitmap… appearing blurry"）。
    为什么只做 System 级、不做 Per-Monitor V2：PMv2 要求应用自己响应 DPI 变化，
    Tkinter 不做（UIUX-PROPOSAL §6 的取舍：办公室一台机器足够）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 1 = System DPI Aware
    except Exception:  # noqa: BLE001  老系统没有 shcore，退回旧 API
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001  失败也不致命（只是可能发虚）
            pass


def main() -> int:
    _enable_dpi_awareness()
    app = MainWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
