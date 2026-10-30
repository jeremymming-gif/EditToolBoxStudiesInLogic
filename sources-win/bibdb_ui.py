"""「引用库」5 页的 Tkinter/ttk 界面（第 132 条 Windows 版）。

**这一层只做三件事**：取值、调 `engine.py` 拼好的 CLI、把结果画出来。
引用库的**全部判据**（什么算已核实、冲突怎么留档、题名相似度多少算命中、哪份 bib
算成品期……）都在引擎 `Engine/slcn/bibdb.py`，命令行薄壳是 `Engine/bibdb.py`。
与 mac 侧 `sources-mac/Features/BibDbFeature.swift` + `Views/BibDb*View.swift` 的分工
完全相同 —— 一旦在这里「顺手」判一次，GUI 与核实链路就会各有一套口径（本项目最忌讳
的两份真相）。

页面（侧边栏顶级分组「引用库」，位于「整期装配」之后、「设置」之前）：
    库概况 / 查库 / 入藏 / 待裁决 / 引用库设置

线程纪律（与 main.py 的 `_worker`/`_drain` 同一套）
------------------------------------------------
本文件里所有会起子进程的动作都在**后台线程**跑（`run_engine_async` / `run_async`），
结果经 `MainWindow.post` 的队列回主线程再碰控件。Tkinter 不是线程安全的：
在工作线程里改控件会 `RuntimeError: main thread is not in main loop`（main.py 的注释
里记着这个坑），而**在 UI 线程同步等子进程**又会让窗口假死（案例 126 的同型问题）。

为什么单独一个文件
------------------
`main.py` 已经 1300+ 行；这 5 页是「第 132 条」的一整块新界面，拆出来既避免主文件继续
膨胀，也让「界面只取值/显示」这条边界一眼看得见。入口与打包不受影响：`main.py` 仍只
import 本文件、建 5 个页对象（PyInstaller 看到的是同一张 import 图）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox as mb
from tkinter import ttk

import bibdb_git as G
import engine as E
import settings as S
import theme


# ── 小工具（纯界面层；判据一律在引擎）────────────────────────────────────

def plain(text: str) -> str:
    """去掉界面文案里的 markdown 记号（Tk 会**原样显示** `**` 与反引号）。

    与 `main.plain` 同义但**不 import main**：main → bibdb_ui 是单向依赖，
    反向 import 会循环。这几行重复换来的是「这个文件能独立测试导入」。
    """
    return text.replace("**", "").replace("`", "")


def fmt_size(num_bytes: int) -> str:
    """字节数 → 人看的大小（B/KB/MB）。清单文件通常几 KB，库文件几 MB。"""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def fmt_time(timestamp: float) -> str:
    """文件修改时间 → `YYYY-MM-DD HH:mm`（0 = 没有这个文件，显示「—」）。"""
    if not timestamp:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except (OSError, ValueError, OverflowError):
        return "—"


def fmt_seconds(seconds: float) -> str:
    """用时（<0.05 秒说「很快」，免得显示 0.0 s 像没跑）。"""
    return "很快" if (seconds or 0) < 0.05 else f"{seconds:.1f} s"


def summary_line(summary: dict | None) -> str:
    """一次入藏/预览的计数摘要（与 CLI 的 `_report_line` 同一信息量）。

    `summary` 是引擎 `ImportReport.summary()` 的 JSON：entries/new/merged/…
    这里的措辞可以不同，**数字不许自己算** —— 那些数字引擎已经算好了。
    """
    summary = summary or {}
    try:
        skipped = summary.get("skipped") or {}
        skipped_total = sum(int(v) for v in skipped.values()) if isinstance(skipped, dict) else 0
    except (TypeError, ValueError):
        skipped_total = 0
    text = (f"条目 {summary.get('entries', 0)}（新增 {summary.get('new', 0)} / "
            f"合并或已存在 {summary.get('merged', 0)} / 更新 {summary.get('updated', 0)} / "
            f"冲突 {summary.get('conflicts', 0)} / 跳过 {skipped_total}）")
    if int(summary.get("disambiguated") or 0) > 0:
        text += f"，key 撞车加后缀 {summary['disambiguated']}"
    return text


def skipped_line(summary: dict | None) -> str:
    """跳过原因（`{"reason": count}` → 「原因×N、…」）。"""
    skipped = (summary or {}).get("skipped") or {}
    if not isinstance(skipped, dict) or not skipped:
        return ""
    return "、".join(f"{reason}×{count}" for reason, count in skipped.items())


def open_in_explorer(path: str, *, select: bool = False) -> tuple[bool, str]:
    """在资源管理器 / 访达里打开（`select=True` 时定位到文件）。返回 (成功, 日志话术)。

    失败**不静默**：把一句可读的话交回给运行日志（与 mac `bibDbOpenInFinder` 同口径）。
    """
    if not path:
        return False, "（没有可打开的路径）"
    if not os.path.exists(path):
        return False, f"（找不到：{path}）"
    try:
        if os.name == "nt":
            if select and os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(path)                       # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if select and os.path.isfile(path)
                             else ["open", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) if select else path])
        return True, f"已打开：{path}"
    except OSError as exc:
        return False, f"打不开 {path}：{exc}"


def _status_kind_color(kind: str) -> str:
    return {"ok": theme.COLOR["ok"], "warn": theme.COLOR["warn"],
            "error": theme.COLOR["error"]}.get(kind, theme.COLOR["muted"])


def collect_call(call: E.EngineCall, *, api: dict | None = None,
                 notes: str = "") -> tuple[int, list[str]]:
    """跑一次引擎，收齐输出行（**同步；只许在后台线程调**）。"""
    lines: list[str] = []
    try:
        code = E.run_call(call, on_line=lines.append, api=api, notes=notes)
    except Exception as exc:  # noqa: BLE001  启动失败也要变成一行可读的话
        lines.append(f"❌ 启动失败：{exc}")
        code = -1
    return code, lines


def run_async(post, work, on_result) -> None:
    """后台跑 `work()`，把结果经 `post(("__CALL__", on_result, (payload,)))` 回主线程。

    `work` 里可以跑一次或多次引擎调用（库概况就是 stats + lint 两次）；
    它**绝不能碰 Tk 控件**（只返回数据）。
    """
    def _worker() -> None:
        try:
            payload = work()
        except Exception as exc:  # noqa: BLE001  任何异常都变成界面上一句可读的话
            payload = {"error": f"❌ 准备失败：{exc}"}
        post(("__CALL__", on_result, (payload,)))
    threading.Thread(target=_worker, daemon=True).start()


def run_engine_async(*, post, call: E.EngineCall, on_result, log=None,
                     api: dict | None = None, notes: str = "") -> None:
    """后台跑**一次**引擎调用；`on_result(code, lines)` 在主线程被调。"""
    if log:
        log(f"▶ {call.label}：{call.display}")
    run_async(post, lambda: collect_call(call, api=api, notes=notes), on_result)


# ── 公共控件 ────────────────────────────────────────────────────────────

class VScrollFrame(ttk.Frame):
    """竖向滚动容器：5 页里「设置」最长，固定窗口高度下必须能滚，否则底部按钮看不见。

    为什么用 Canvas 套 Frame：ttk 没有可滚动的 Frame（这是 Tk 的标准做法）。
    滚轮处理刻意**只绑在本页的子孙控件上**，不用 `bind_all` —— 后者会把日志框、
    侧栏的滚轮一起抢掉。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=theme.COLOR["bg"], highlightthickness=0,
                                borderwidth=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._window, width=e.width))

    def bind_wheel(self) -> None:
        """把滚轮绑到已建好的子孙控件上（建完页面之后调一次）。"""
        def _on_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 else 1
            self.canvas.yview_scroll(step * 3, "units")
            return "break"

        def _on_button(event):
            self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
            return "break"

        def walk(widget) -> None:
            for child in widget.winfo_children():
                try:
                    child.bind("<MouseWheel>", _on_wheel, add="+")
                    child.bind("<Button-4>", _on_button, add="+")
                    child.bind("<Button-5>", _on_button, add="+")
                except tk.TclError:      # pragma: no cover  个别控件不接受绑定
                    pass
                walk(child)

        walk(self)


class InfoRows(ttk.Frame):
    """「标签：值」多行（库概况/设置页大量重复的排版；值可换行、可复制）。"""

    def __init__(self, parent, label_width: int = 12):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self._row = 0
        self._label_width = label_width

    def add(self, label: str) -> tk.StringVar:
        ttk.Label(self, text=label, style="Muted.TLabel",
                  width=self._label_width, anchor="e").grid(
            row=self._row, column=0, sticky="e", padx=(0, 8), pady=1)
        var = tk.StringVar(value="—")
        ttk.Label(self, textvariable=var, wraplength=760, justify="left").grid(
            row=self._row, column=1, sticky="w", pady=1)
        self._row += 1
        return var


class StatusLine(ttk.Label):
    """状态条：✅ / ⚠ / ❌ / 灰字（颜色取 `theme.COLOR`，符号本身也表达语义）。"""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("wraplength", 780)
        kwargs.setdefault("justify", "left")
        super().__init__(parent, text="", **kwargs)

    def show(self, kind: str, text: str) -> None:
        self.configure(text=plain(text), foreground=_status_kind_color(kind))

    def clear(self) -> None:
        self.configure(text="")


def _tree(parent, columns, heads, widths, *, height: int = 8):
    """带竖向滚动条的只读表格（Tk 的 Treeview；色板由 theme 统一管）。"""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True, pady=(2, 4))
    tree = ttk.Treeview(frame, columns=columns, show="headings",
                        height=height, selectmode="browse")
    for column, head, width in zip(columns, heads, widths):
        tree.heading(column, text=head)
        tree.column(column, width=width, anchor="w")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return tree


def _readonly_text(parent, *, height: int = 8) -> tk.Text:
    """只读多行文本（BibTeX / 清单预览）：Tk 没有 ttk 对等物，颜色全部取自 theme。"""
    widget = tk.Text(parent, height=height, wrap="none", font=theme.mono_font(),
                     background=theme.COLOR["text_bg"], foreground=theme.COLOR["fg"],
                     insertbackground=theme.COLOR["fg"],
                     selectbackground=theme.COLOR["accent"],
                     selectforeground=theme.COLOR["on_accent"],
                     borderwidth=1, relief="solid", highlightthickness=0,
                     state="disabled")
    return widget


def set_readonly_text(widget: tk.Text, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    if content:
        widget.insert("1.0", content)
    widget.configure(state="disabled")


# ── 页面基类 ────────────────────────────────────────────────────────────

class BibDbPage(ttk.Frame):
    """5 页的公共骨架：标题 + 说明 + 可滚动内容区（子类只填 `build`）。"""

    page_title = ""
    page_subtitle = ""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app                     # MainWindow：只用 log/post/cfg 三个口子
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        outer = ttk.Frame(self)
        outer.grid(row=0, column=0, sticky="nsew", padx=16, pady=(10, 6))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        ttk.Label(outer, text=plain(self.page_title), style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(outer, text=plain(self.page_subtitle), style="Muted.TLabel",
                  wraplength=860, justify="left").grid(row=1, column=0, sticky="w", pady=(2, 8))
        self.scroll = VScrollFrame(outer)
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.body = ttk.Frame(self.scroll.inner)
        self.body.pack(fill="both", expand=True, pady=(2, 14))
        self.build(self.body)
        self.scroll.bind_wheel()

    # 子类实现
    def build(self, parent: ttk.Frame) -> None:      # pragma: no cover - 基类占位
        pass

    def on_activate(self) -> None:
        """切到本页时被 MainWindow 调（读库/刷新清单；子类覆盖）。"""

    # 便捷属性
    @property
    def log(self):
        return self.app.log

    def post(self, item) -> None:
        self.app.post(item)

    def notes_root(self) -> str | None:
        return E.effective_notes_repo(self.app.cfg.get("notesRepoPath", ""))

    def db_path(self) -> str | None:
        return E.bibdb_db_path(self.notes_root())

    def _open_path(self, path: str, *, select: bool = False) -> None:
        ok, line = open_in_explorer(path, select=select)
        self.log(line)


# ── ① 库概况 ────────────────────────────────────────────────────────────

class BibDbStatsPage(BibDbPage):
    name = "库概况"        # 侧栏里显示的名字（组 → 页的第二层）
    """库概况：`stats --json` + `lint --json`，**只读**（不写库、不改清单）。"""

    page_title = "引用库 · 库概况"
    page_subtitle = """编辑部引用库（bib_db）把已核实的参考文献沉淀成可查、可审计的库：
以后核实文献先查库，命中已核实条目的就不再调检索 API / AI（省钱 + 体例统一）。
verified 只认「往期已出版期的成品 bib」或人工核实，AI 结果不会直接入库。
这一页只读：不写库、不改清单。"""

    def build(self, parent: ttk.Frame) -> None:
        self._generation = 0
        self._loading = False
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 6))
        self.reload_btn = ttk.Button(bar, text="刷新", command=self.reload)
        self.reload_btn.pack(side="left")
        self.open_folder_btn = ttk.Button(bar, text="打开库文件夹", command=self._open_folder,
                                          state="disabled")
        self.open_folder_btn.pack(side="left", padx=6)
        self.open_file_btn = ttk.Button(bar, text="打开库文件", command=self._open_file,
                                        state="disabled")
        self.open_file_btn.pack(side="left")
        self.busy = ttk.Label(bar, text="", style="Muted.TLabel")
        self.busy.pack(side="left", padx=10)

        self.status = StatusLine(parent)
        self.status.pack(fill="x", pady=(0, 4))

        self.rows = InfoRows(parent)
        self.rows.pack(fill="x")
        self.v_file = self.rows.add("库文件")
        self.v_records = self.rows.add("条目")
        self.v_issues = self.rows.add("期数")
        self.v_updated = self.rows.add("最近更新")
        self.v_conflicts = self.rows.add("冲突")
        self.v_range = self.rows.add("期范围")
        self.v_size = self.rows.add("库大小")
        self.v_notes = self.rows.add("笔记仓")
        self.v_tool = self.rows.add("引擎脚本")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)
        self.v_lint = self.rows.add("lint 结果")
        self.v_near = self.rows.add("同作品多记录")
        self.lint_errors = ttk.Label(parent, text="", wraplength=780, justify="left",
                                     foreground=theme.COLOR["error"])
        self.lint_errors.pack(fill="x", pady=(2, 0))
        self.lint_warnings = ttk.Label(parent, text="", wraplength=780, justify="left",
                                       foreground=theme.COLOR["warn"])
        self.lint_warnings.pack(fill="x", pady=(2, 0))
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)
        self.footer = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=780,
                                justify="left")
        self.footer.pack(fill="x")
        ttk.Label(parent, text="读库用时与库路径只作核对用；要生成/更新待裁决清单请到"
                               "「引用库 → 待裁决」页。",
                  style="Muted.TLabel", wraplength=780, justify="left").pack(
            fill="x", pady=(4, 0))

    def on_activate(self) -> None:
        self.reload()

    # ── 动作 ──
    def reload(self) -> None:
        if self._loading:
            return
        self._generation += 1
        generation = self._generation
        self._loading = True
        self.reload_btn.config(state="disabled")
        self.busy.config(text="正在读取库概况（stats + lint）…")
        self.status.clear()

        notes = self.notes_root()
        db = E.bibdb_db_path(notes) if notes else None
        try:
            stats_call = E.bibdb_stats_call(db=db or "")
            lint_call = E.bibdb_lint_call(db=db or "")
        except RuntimeError as exc:
            self._loading = False
            self.reload_btn.config(state="normal")
            self.busy.config(text="")
            self.status.show("error", str(exc))
            return
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            payload: dict = {"notes": notes, "db_path": db or "", "manual": manual}
            payload["db_exists"] = bool(db) and os.path.isfile(db)
            if not db:
                payload["error"] = ("找不到私有笔记仓（引用库在 <笔记仓>/bib_db/bibliography.jsonl）。"
                                    "请到「引用库 → 引用库设置」页手动指定笔记仓路径，"
                                    "或确认笔记仓与工具仓并排。")
                payload["seconds"] = time.monotonic() - started
                return payload
            code, lines = collect_call(stats_call, api=api, notes=manual)
            payload.update(stats_code=code, stats_lines=lines,
                           stats=E.parse_json_lines(lines))
            if payload["stats"] is None:
                payload["error"] = (
                    f"读库概况失败：{E.output_tail(lines, count=2)}" if payload["db_exists"]
                    else f"引用库文件还不存在：{db}\n到「入藏」页导入一份 .bib 或一期目录，"
                         "确认导入时会自动建库。")
            elif payload["db_exists"]:
                lint_code, lint_lines = collect_call(lint_call, api=api, notes=manual)
                payload.update(lint_code=lint_code, lint_lines=lint_lines,
                               lint=E.parse_json_lines(lint_lines))
            payload["seconds"] = time.monotonic() - started
            return payload

        run_async(self.post, work, lambda payload: self._on_loaded(payload, generation))

    def _on_loaded(self, payload: dict, generation: int) -> None:
        if generation != self._generation:      # 连续点「刷新」：只认最后一次
            return
        self._loading = False
        self.reload_btn.config(state="normal")
        self.busy.config(text="")
        notes = payload.get("notes")
        db = payload.get("db_path") or ""
        stats = payload.get("stats")
        lint = payload.get("lint")
        self.v_notes.set(notes or "（未找到私有笔记仓）")
        self.v_file.set(db or "（找不到笔记仓，无法定位库文件）")
        self.open_folder_btn.config(
            state="normal" if notes else "disabled")
        self.open_file_btn.config(state="normal" if payload.get("db_exists") else "disabled")
        self._db_file = db if payload.get("db_exists") else ""

        if payload.get("error"):
            kind = "warn" if stats is None and not payload.get("db_exists") else "error"
            self.status.show(kind, str(payload["error"]))
        elif stats is not None:
            self.status.show("ok", "库可读。")

        if stats:
            self._fill_stats(stats)
        # 引擎脚本位置与库能不能读无关：找不到时也要显示（打包漏了 bibdb.py 的表现）
        tool = E.bibdb_tool()
        self.v_tool.set(tool or "未找到引用库脚本 Engine/bibdb.py（打包时漏了 bibdb.py？）")

        if lint:
            self._fill_lint(lint)
        elif stats is not None:
            self.v_lint.set("没读到 lint 结果（库文件存在但 lint 失败，见上方错误）")
            self.v_near.set("—")
        elif payload.get("db_exists"):
            self.v_lint.set("—")
            self.v_near.set("—")
        else:
            self.v_lint.set("（库文件不存在，未跑 lint）")
            self.v_near.set("—")
        self.footer.configure(
            text=f"读库用时 {fmt_seconds(float(payload.get('seconds') or 0))}。")

    def _fill_stats(self, stats: dict) -> None:
        def num(key: str) -> int:
            try:
                return int(stats.get(key) or 0)
            except (TypeError, ValueError):
                return 0
        notes_path = str(stats.get("path") or "")
        self.v_file.set(notes_path or self.v_file.get())
        self.v_records.set(f"{num('records')} 条（已核实 {num('verified')} / "
                           f"未核实 {num('unverified')}）")
        self.v_issues.set(f"{num('issues')} 期（{num('files')} 份成品 bib，"
                          f"{num('sources')} 条来源记录）")
        self.v_updated.set(str(stats.get("updated_newest") or "—"))
        conflicts = stats.get("conflicts") or {}
        try:
            conflict_records = int(conflicts.get("records") or 0)
            conflict_total = int(conflicts.get("total") or 0)
        except (TypeError, ValueError):
            conflict_records = conflict_total = 0
        self.v_conflicts.set(f"{conflict_records} 条记录 / {conflict_total} 处字段差异"
                             + ("（无待裁决项）" if conflict_total == 0
                                else "（到「引用库 → 待裁决」页看清单）"))
        issue_list = stats.get("issue_list") or []
        if issue_list:
            first, last = str(issue_list[0]), str(issue_list[-1])
            self.v_range.set(first if first == last else f"{first} … {last}")
        else:
            self.v_range.set("—")
        sha = str(stats.get("sha256") or "")
        try:
            size = int(stats.get("bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        self.v_size.set(fmt_size(size) + (f"；sha256 {sha[:12]}…" if sha else ""))

    def _fill_lint(self, lint: dict) -> None:
        counts = lint.get("counts") or {}
        try:
            error_count = int(counts.get("errors") or 0)
            warning_count = int(counts.get("warnings") or 0)
            near = int(counts.get("near_duplicates") or 0)
        except (TypeError, ValueError):
            error_count = warning_count = near = 0
        ok = lint.get("ok") is not False
        if not counts:
            error_count = len(lint.get("errors") or [])
            warning_count = len(lint.get("warnings") or [])
        self.v_lint.set(f"error {error_count} 项 / warning {warning_count} 项"
                        + ("（库可用）" if ok else "——库有 error，先修"))
        self.v_near.set(f"{near} 对（经典文献跨期重复引用是常态；仅供参考，不自动合并）")
        errors = [str(item) for item in (lint.get("errors") or [])]
        if errors:
            shown = "\n".join("✗ " + item for item in errors[:6])
            if len(errors) > 6:
                shown += f"\n……另有 {len(errors) - 6} 条 error，跑 bibdb.py lint 看全部。"
            self.lint_errors.configure(text=plain(shown))
        else:
            self.lint_errors.configure(text="")
        warnings = [str(item) for item in (lint.get("warnings") or [])]
        shown_warn = "\n".join("! " + item for item in warnings[:4])
        if len(warnings) > 4:
            shown_warn += f"\n……另有 {len(warnings) - 4} 条 warning（不影响使用）。"
        self.lint_warnings.configure(text=plain(shown_warn))

    def _open_folder(self) -> None:
        directory = E.notes_dir(self.notes_root())
        if directory:
            self._open_path(directory)

    def _open_file(self) -> None:
        path = getattr(self, "_db_file", "")
        if path:
            self._open_path(path, select=True)


# ── ② 查库 ──────────────────────────────────────────────────────────────

#: 查库方式（界面文字 → 引擎参数；键必须与 engine.QUERY_FLAGS 一致）
QUERY_MODES = (("key", "key"), ("DOI", "doi"), ("题名", "title"))
QUERY_HINTS = {
    "key": "如 Fine1975（也认 aliases）",
    "doi": "如 10.1007/s10992-020-09558-5（大小写/前缀不敏感）",
    "title": "题名模糊匹配（返回相似度与置信度）",
}

#: 高级检索（B-261）：字段 / 匹配方式 / 排序（界面文字 → 引擎参数，键与
#: `bibdb.py query-advanced` 的 `--字段` / `--mode 字段=模式` / `--sort` 一致）。
#: 字段**不含 key**（key 是基本检索的精确入口）；年份只收年份表达式。
ADVANCED_FIELDS = (("作者", "author"), ("编者", "editor"), ("题名", "title"),
                   ("书名", "booktitle"), ("期刊", "journal"), ("出版社", "publisher"),
                   ("年份", "year"), ("DOI", "doi"), ("别名", "alias"), ("任意", "any"))
ADVANCED_MODES = (("包含", "contains"), ("前缀", "prefix"), ("精确", "exact"), ("模糊", "fuzzy"))
ADVANCED_SORTS = (("相关度", "relevance"), ("年份", "year"), ("作者", "author"))
#: 引擎对这几个字段**不收** fuzzy（见 b259 spec §2），界面里也不提供该档。
ADVANCED_NO_FUZZY = ("year", "doi", "alias")
ADVANCED_HINT = ("多行之间是 AND（都要满足）；年份行可填 2003 / 1990-2000 / >=2010；"
                 "「模糊」只对文本字段有效。")
_ADV_FIELD_KEY = {label: key for label, key in ADVANCED_FIELDS}
_ADV_FIELD_LABEL = {key: label for label, key in ADVANCED_FIELDS}
_ADV_MODE_KEY = {label: key for label, key in ADVANCED_MODES}
_ADV_MODE_LABEL = {key: label for label, key in ADVANCED_MODES}
_ADV_SORT_KEY = {label: key for label, key in ADVANCED_SORTS}


class BibDbQueryPage(BibDbPage):
    name = "查库"        # 侧栏里显示的名字（组 → 页的第二层）
    """查库：按 key / DOI / 题名查引用库，选中一条复制可直接粘贴的 BibTeX（**只读**）。"""

    page_title = "引用库 · 查库"
    page_subtitle = """按 key（也认别名）、DOI 或题名（模糊匹配）查引用库。
命中条目带「已核实」标记与来源期；选中一条可复制 BibTeX 直接粘进 .bib。
这一页只读：查询不会改库，也不会调检索 / AI。"""

    def build(self, parent: ttk.Frame) -> None:
        self._generation = 0
        self._running = False
        self._hits: list[dict] = []
        self._hit_by_iid: dict[str, dict] = {}
        self._adv_rows: list[dict] = []

        # B-261：查库分「基本检索 / 高级检索」两个 Tab。基本检索 = 原有部件的
        # **原样迁移**：变量名、回调、search() 路径、复制 BibTeX 全保持原行为。
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="x", pady=(0, 4))
        basic_tab = ttk.Frame(self.notebook, padding=(2, 4))
        advanced_tab = ttk.Frame(self.notebook, padding=(2, 4))
        self.notebook.add(basic_tab, text="基本检索")
        self.notebook.add(advanced_tab, text="高级检索")

        bar = ttk.Frame(basic_tab)
        bar.pack(fill="x", pady=(0, 6))
        self.mode_var = tk.StringVar(value="key")
        self.mode_box = ttk.Combobox(bar, textvariable=self.mode_var, width=6,
                                     state="readonly",
                                     values=[label for label, _key in QUERY_MODES])
        self.mode_box.pack(side="left")
        self.text_var = tk.StringVar(value="")
        self.entry = ttk.Entry(bar, textvariable=self.text_var, width=52)
        self.entry.pack(side="left", padx=6)
        self.entry.bind("<Return>", lambda _e: self.search())
        self.search_btn = ttk.Button(bar, text="查询", width=10, style="Accent.TButton",
                                     command=self.search)
        self.search_btn.pack(side="left")
        self.busy = ttk.Label(bar, text="", style="Muted.TLabel")
        self.busy.pack(side="left", padx=8)
        self.hint = ttk.Label(basic_tab, text="", style="Muted.TLabel")
        self.hint.pack(fill="x")
        self._update_hint()

        self._build_advanced(advanced_tab)

        self.status = StatusLine(parent)
        self.status.pack(fill="x", pady=(2, 4))

        self.tree = _tree(parent,
                          ("title", "author", "year", "key", "verified", "source"),
                          ("题名", "作者", "年份", "key", "状态", "来源"),
                          (300, 130, 54, 130, 60, 170))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.rows_count = ttk.Label(parent, text="输入查询内容后按回车或点「查询」。",
                                    style="Muted.TLabel")
        self.rows_count.pack(fill="x", pady=(0, 2))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=6)
        detail_bar = ttk.Frame(parent)
        detail_bar.pack(fill="x")
        self.selected_label = ttk.Label(detail_bar, text="（未选中）", style="Section.TLabel")
        self.selected_label.pack(side="left")
        self.copy_btn = ttk.Button(detail_bar, text="复制 BibTeX", width=12,
                                   style="Accent.TButton", command=self.copy_bibtex,
                                   state="disabled")
        self.copy_btn.pack(side="right")
        self.log_btn = ttk.Button(detail_bar, text="在运行日志里显示", width=16,
                                  command=self.log_hit, state="disabled")
        self.log_btn.pack(side="right", padx=6)
        self.source_label = ttk.Label(parent, text="", style="Muted.TLabel",
                                      wraplength=800, justify="left")
        self.source_label.pack(fill="x", pady=(2, 2))
        # why-hit（高级检索）：只有命中带 matched[] 时才显示；基本检索永远隐藏，
        # 所以基本模式的界面与 B-259 逐像素一致。
        self.why_box = ttk.Frame(parent)
        self.why_label = ttk.Label(self.why_box, text="", style="Muted.TLabel")
        self.why_label.pack(anchor="w")
        self.why_text = _readonly_text(self.why_box, height=4)
        self.why_text.pack(fill="x", pady=(2, 2))
        self.bibtex = _readonly_text(parent, height=7)
        self.bibtex.pack(fill="both", expand=True, pady=(2, 2))

        self.mode_box.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())

    # ── 高级检索（B-261）──
    def _build_advanced(self, parent: ttk.Frame) -> None:
        self.adv_rows_frame = ttk.Frame(parent)
        self.adv_rows_frame.pack(fill="x")
        self._add_adv_row()          # 默认两行：作者 + 题名
        self._add_adv_row(field="title")

        add_bar = ttk.Frame(parent)
        add_bar.pack(fill="x", pady=(3, 4))
        ttk.Button(add_bar, text="+ 加一行", width=12,
                   command=self._add_adv_row).pack(side="left")

        controls = ttk.Frame(parent)
        controls.pack(fill="x")
        ttk.Label(controls, text="排序").pack(side="left")
        self.adv_sort_var = tk.StringVar(value="相关度")
        self.adv_sort_box = ttk.Combobox(controls, textvariable=self.adv_sort_var, width=6,
                                         state="readonly",
                                         values=[label for label, _key in ADVANCED_SORTS])
        self.adv_sort_box.pack(side="left", padx=(4, 12))
        ttk.Label(controls, text="最多").pack(side="left")
        self.adv_limit_var = tk.StringVar(value="20")
        self.adv_limit_spin = ttk.Spinbox(controls, from_=1, to=200, width=6,
                                          textvariable=self.adv_limit_var)
        self.adv_limit_spin.pack(side="left", padx=(4, 12))
        self.adv_strict_var = tk.BooleanVar(value=False)
        self.adv_strict = ttk.Checkbutton(controls, text="严格（模糊门槛 0.90）",
                                          variable=self.adv_strict_var)
        self.adv_strict.pack(side="left")
        self.adv_search_btn = ttk.Button(controls, text="查询", width=10,
                                         style="Accent.TButton", command=self.search_advanced)
        self.adv_search_btn.pack(side="left", padx=(12, 6))
        self.adv_busy = ttk.Label(controls, text="", style="Muted.TLabel")
        self.adv_busy.pack(side="left")
        self.adv_hint = ttk.Label(parent, text=plain(ADVANCED_HINT), style="Muted.TLabel",
                                  wraplength=800, justify="left")
        self.adv_hint.pack(fill="x", pady=(4, 0))

    def _add_adv_row(self, field: str = "author", mode: str = "contains",
                     value: str = "") -> None:
        row: dict = {}
        frame = ttk.Frame(self.adv_rows_frame)
        frame.pack(fill="x", pady=1)
        field_var = tk.StringVar(value=_ADV_FIELD_LABEL.get(field, "作者"))
        mode_var = tk.StringVar(value=_ADV_MODE_LABEL.get(mode, "包含"))
        field_box = ttk.Combobox(frame, textvariable=field_var, width=6, state="readonly",
                                 values=[label for label, _key in ADVANCED_FIELDS])
        field_box.pack(side="left")
        mode_box = ttk.Combobox(frame, textvariable=mode_var, width=6, state="readonly",
                                values=[label for label, _key in ADVANCED_MODES])
        mode_box.pack(side="left", padx=4)
        entry = ttk.Entry(frame, width=44)
        entry.insert(0, value)
        entry.pack(side="left", padx=(0, 4))
        del_btn = ttk.Button(frame, text="删除", width=6,
                             command=lambda r=row: self._remove_adv_row(r))
        del_btn.pack(side="left")
        row.update(frame=frame, field_var=field_var, mode_var=mode_var, entry=entry,
                   field_box=field_box, mode_box=mode_box, del_btn=del_btn)
        self._adv_rows.append(row)
        field_box.bind("<<ComboboxSelected>>", lambda _e, r=row: self._adv_field_changed(r))
        entry.bind("<Return>", lambda _e: self.search_advanced())

    def _adv_field_changed(self, row: dict) -> None:
        """字段一换就收紧可选模式：年份/DOI/别名不收 fuzzy，默认「精确」。"""
        field = _ADV_FIELD_KEY.get(row["field_var"].get(), "author")
        allowed = [(label, key) for label, key in ADVANCED_MODES
                   if not (key == "fuzzy" and field in ADVANCED_NO_FUZZY)]
        row["mode_box"].configure(values=[label for label, _key in allowed])
        if row["mode_var"].get() not in [label for label, _key in allowed]:
            row["mode_var"].set(_ADV_MODE_LABEL["exact"])
        elif field in ADVANCED_NO_FUZZY:
            row["mode_var"].set(_ADV_MODE_LABEL["exact"])

    def _remove_adv_row(self, row: dict) -> None:
        if row in self._adv_rows:
            self._adv_rows.remove(row)
        row["frame"].destroy()

    def _advanced_terms(self) -> list[dict]:
        terms: list[dict] = []
        for row in self._adv_rows:
            value = row["entry"].get().strip()
            if not value:
                continue
            field = _ADV_FIELD_KEY.get(row["field_var"].get(), "author")
            mode = _ADV_MODE_KEY.get(row["mode_var"].get(), "contains")
            terms.append({"field": field, "mode": mode, "value": value})
        return terms

    # ── 查询 ──
    def _mode_changed(self) -> None:
        self._generation += 1              # 换了方式：上一次结果不再对应当前查询
        self._hits = []
        self._hit_by_iid.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._update_hint()
        self.rows_count.config(text="输入查询内容后按回车或点「查询」。")
        self.status.clear()
        self._show_detail(None)

    def _update_hint(self) -> None:
        key = dict(QUERY_MODES).get(self.mode_var.get(), "key")
        self.hint.config(text=plain("例：" + QUERY_HINTS[key]))

    def _mode_key(self) -> str:
        return dict(QUERY_MODES).get(self.mode_var.get(), "key")

    def search(self) -> None:
        if self._running:
            return
        text = self.text_var.get().strip()
        if not text:
            self.status.show("warn", f"先填要查的内容（{self.mode_var.get()}）。")
            return
        notes = self.notes_root()
        db = E.bibdb_db_path(notes) if notes else None
        mode = self._mode_key()
        if not notes:
            self.status.show("warn", "找不到私有笔记仓。请先到「引用库 → 引用库设置」页指定笔记仓路径。")
            return
        try:
            call = E.bibdb_query_call(mode, text, db=db or "")
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        self._generation += 1
        generation = self._generation
        self._running = True
        self.search_btn.config(state="disabled")
        self.mode_box.config(state="disabled")
        self.busy.config(text="正在查询…")
        self.status.clear()
        self.log(f"查引用库（{self.mode_var.get()}）：{text}")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "mode": mode, "text": text, "seconds": time.monotonic() - started}

        run_async(self.post, work, lambda payload: self._on_result(payload, generation))

    def _on_result(self, result: dict, generation: int) -> None:
        if generation != self._generation:
            return
        self._running = False
        self.search_btn.config(state="normal")
        self.mode_box.config(state="readonly")
        self.busy.config(text="")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._hits = []
        self._hit_by_iid.clear()
        self._show_detail(None)
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        if code != 0 or not isinstance(payload, dict):
            self.status.show("error",
                             f"查库失败：{E.output_tail(result.get('lines') or [], count=2)}")
            return
        hits = payload.get("hits") or []
        self._fill_hits(hits)
        if hits:
            first = "hit:0"
            if self.tree.exists(first):
                self.tree.selection_set(first)
                self.tree.focus(first)
            self.rows_count.config(text=f"命中 {len(hits)} 条（用时 "
                                        f"{fmt_seconds(float(result.get('seconds') or 0))}）。")
            if payload.get("ambiguous") and result.get("mode") == "key":
                self.status.show("warn", "⚠ 有多条同 key/alias 的记录，需人工确认。")
        else:
            self.rows_count.config(text="没有命中。")
            query = payload.get("query") or {}
            subject = query.get("title") or query.get("doi") or query.get("key") or ""
            self.status.show("warn", f"没有命中：{subject}（用时 "
                                     f"{fmt_seconds(float(result.get('seconds') or 0))}）")
        if payload.get("error"):
            self.status.show("warn", f"（引擎说明：{payload['error']}）")

    def _fill_hits(self, hits: list) -> None:
        """把引擎 JSON 的 hits 填进现有 tree（基本/高级两条查询共用同一展示）。"""
        for index, hit in enumerate(hits):
            if not isinstance(hit, dict):
                continue
            record = hit.get("record") if isinstance(hit.get("record"), dict) else {}
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            title = str(fields.get("title") or fields.get("booktitle") or "（无题名）")
            author = str(fields.get("author") or fields.get("editor") or "（无作者）")
            year = str(fields.get("year") or "")
            if not year and fields.get("date"):
                year = str(fields["date"])[:4]
            sources = record.get("sources") or []
            where = ""
            if sources and isinstance(sources[0], dict):
                parts = [str(sources[0].get("issue") or ""), str(sources[0].get("slug") or ""),
                         str(sources[0].get("file") or "")]
                where = " / ".join(part for part in parts if part)
            verified = "已核实" if record.get("verified") is True else "未核实"
            confidence = hit.get("confidence")
            if confidence:
                verified += "（高置信）" if confidence == "high" else "（低置信）"
            iid = f"hit:{index}"
            self.tree.insert("", "end", iid=iid,
                             values=(title, author, year, str(record.get("key") or ""),
                                     verified, where))
            self._hit_by_iid[iid] = hit
            self._hits.append(hit)

    # ── 高级检索（B-261）：多条件 AND，argv 只由 engine.py 拼 ──
    def search_advanced(self) -> None:
        if self._running:
            return
        terms = self._advanced_terms()
        if not terms:
            self.status.show("warn", "高级检索至少填一行条件（值不能为空）。")
            return
        notes = self.notes_root()
        db = E.bibdb_db_path(notes) if notes else None
        if not notes:
            self.status.show("warn", "找不到私有笔记仓。请先到「引用库 → 引用库设置」页指定笔记仓路径。")
            return
        raw_limit = self.adv_limit_var.get().strip()
        if not raw_limit.isdigit():
            self.status.show("error", f"limit 必须是 1–200 的整数：{raw_limit!r}")
            return
        limit = int(raw_limit)
        strict = bool(self.adv_strict_var.get())
        sort = _ADV_SORT_KEY.get(self.adv_sort_var.get(), "relevance")
        try:
            call = E.bibdb_query_advanced_call(terms, sort=sort, limit=limit,
                                               strict=strict, db=db or "")
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        self._generation += 1
        generation = self._generation
        self._running = True
        self.adv_search_btn.config(state="disabled")
        self.adv_busy.config(text="正在查询…")
        self.search_btn.config(state="disabled")
        self.mode_box.config(state="disabled")
        self.status.clear()
        subject = "；".join(f"{term['field']}={term['value']}" for term in terms)
        self.log(f"高级检索：{subject}（排序 {self.adv_sort_var.get()}，最多 {limit} 条"
                 + ("，严格）" if strict else "）"))
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "subject": subject, "seconds": time.monotonic() - started}

        run_async(self.post, work,
                  lambda payload: self._on_advanced_result(payload, generation))

    def _on_advanced_result(self, result: dict, generation: int) -> None:
        if generation != self._generation:
            return
        self._running = False
        self.adv_search_btn.config(state="normal")
        self.adv_busy.config(text="")
        self.search_btn.config(state="normal")
        self.mode_box.config(state="readonly")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._hits = []
        self._hit_by_iid.clear()
        self._show_detail(None)
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        if code != 0 or not isinstance(payload, dict):
            self.status.show("error",
                             f"高级检索失败：{E.output_tail(result.get('lines') or [], count=2)}")
            return
        hits = payload.get("hits") or []
        self._fill_hits(hits)
        elapsed = fmt_seconds(float(result.get("seconds") or 0))
        if hits:
            first = "hit:0"
            if self.tree.exists(first):
                self.tree.selection_set(first)
                self.tree.focus(first)
            parts = [f"命中 {len(hits)} 条"]
            total = payload.get("total_matched")
            if isinstance(total, int) and total > len(hits):
                parts.append(f"共 {total} 条匹配")
            if payload.get("truncated"):
                parts.append("已截断")
            self.rows_count.config(text="，".join(parts) + f"（用时 {elapsed}）。")
        else:
            self.rows_count.config(text="没有命中。")
            self.status.show("warn", f"高级检索没有命中：{result.get('subject') or ''}"
                                     f"（用时 {elapsed}）")
        if payload.get("error"):
            self.status.show("warn", f"（引擎说明：{payload['error']}）")

    # ── 选中 + 复制 ──
    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._show_detail(self._hit_by_iid.get(selection[0]) if selection else None)

    def _show_detail(self, hit: dict | None) -> None:
        if not hit:
            self.selected_label.config(text="（未选中）")
            self.source_label.config(text="")
            self._show_why_hits(None)
            set_readonly_text(self.bibtex, "")
            self.copy_btn.config(state="disabled")
            self.log_btn.config(state="disabled")
            return
        record = hit.get("record") if isinstance(hit.get("record"), dict) else {}
        key = str(record.get("key") or "")
        verified = "已核实" if record.get("verified") is True else "未核实"
        self.selected_label.config(text=f"已选中：{key}（{verified}）")
        sources = record.get("sources") or []
        where = ""
        if sources and isinstance(sources[0], dict):
            parts = [str(sources[0].get("issue") or ""), str(sources[0].get("slug") or ""),
                     str(sources[0].get("file") or "")]
            where = " / ".join(part for part in parts if part)
        self.source_label.config(text=plain("来源：" + (where or "（无来源记录）")))
        self._show_why_hits(hit)
        bibtex = str(hit.get("bibtex") or "")
        set_readonly_text(self.bibtex, bibtex or "（引擎未返回 BibTeX）")
        self.copy_btn.config(state="normal" if bibtex else "disabled")
        self.log_btn.config(state="normal")

    def _show_why_hits(self, hit: dict | None) -> None:
        """高级检索的 why-hit：把 matched[] 的 field/mode/score/snippet 摊成文本。

        基本检索的命中没有 `matched` 键 → 这里直接把整块收起来（界面零变化）。
        """
        lines: list[str] = []
        matched = (hit or {}).get("matched")
        if isinstance(matched, list):
            for item in matched:
                if not isinstance(item, dict):
                    continue
                score = item.get("score")
                try:
                    score_text = f"{float(score):.3f}"
                except (TypeError, ValueError):
                    score_text = str(score or "")
                snippet = str(item.get("snippet") or "").replace("\n", " ").strip()
                lines.append(f"[{item.get('field') or '?'}] {item.get('mode') or '?'} · "
                             f"{score_text} · {snippet}")
        if not lines:
            if self.why_box.winfo_manager():
                self.why_box.pack_forget()
            set_readonly_text(self.why_text, "")
            return
        self.why_label.config(text=f"命中原因（{len(lines)} 项条件都满足）：")
        set_readonly_text(self.why_text, "\n".join(lines))
        if not self.why_box.winfo_manager():
            self.why_box.pack(fill="x", pady=(2, 2), before=self.bibtex)

    def copy_bibtex(self) -> None:
        selection = self.tree.selection()
        hit = self._hit_by_iid.get(selection[0]) if selection else None
        bibtex = str((hit or {}).get("bibtex") or "")
        if not bibtex:
            self.status.show("warn", "这一条没有 BibTeX 文本（引擎未返回）。")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(bibtex)
        except tk.TclError:
            self.status.show("error", "复制失败：剪贴板不可写。")
            return
        key = str(((hit or {}).get("record") or {}).get("key") or "")
        self.status.show("ok", f"已复制 BibTeX（{key}）到剪贴板。")
        self.log(f"已复制引用库条目 BibTeX：{key}")

    def log_hit(self) -> None:
        selection = self.tree.selection()
        hit = self._hit_by_iid.get(selection[0]) if selection else None
        if not hit:
            return
        record = hit.get("record") if isinstance(hit.get("record"), dict) else {}
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        key = str(record.get("key") or "")
        title = str(fields.get("title") or "（无题名）")
        author = str(fields.get("author") or fields.get("editor") or "（无作者）")
        self.log(f"引用库条目 {key}：{title} · {author}")
        for name in sorted(fields):
            self.log(f"  {name} = {{{fields[name]}}}")


# ── ③ 入藏 ──────────────────────────────────────────────────────────────

IMPORT_MODES = (("单篇 .bib", "single"), ("整期目录", "directory"))

STATUS_TITLES = {"new": "新增", "merged": "合并来源", "updated": "更新", "conflict": "记冲突"}


def _import_signature(mode: str, path: str, issue: str, slug: str) -> tuple:
    return (mode, path.strip(), issue.strip(), slug.strip())


class BibDbImportPage(BibDbPage):
    name = "入藏"        # 侧栏里显示的名字（组 → 页的第二层）
    """入藏：先 dry-run 预览（**一个字节都不写**），看清楚再确认导入。

    产品行为（与 mac 对齐）：给了来源期/篇的条目按「往期已出版、人工核对过」入
    `verified=true`；AI 结果**不要**走这个入口。预览之后**改过任何输入**，确认按钮
    会重新置灰 —— 绝不让编辑照着旧预览点确认。
    """

    page_title = "引用库 · 入藏"
    page_subtitle = """把往期成品 bib（或一整期目录）导入编辑部引用库：给了来源期/篇的条目按
「往期已出版、人工核对过」入 verified=true（AI 结果不要走这个入口）。
先点「预览（dry-run）」——引擎照常解析/查重/算冲突，但库一个字节都不写；
看清楚了再点「确认入藏」。"""

    def build(self, parent: ttk.Frame) -> None:
        self._running = False
        self._syncing = False
        self._preview: dict | None = None
        self._preview_signature: tuple | None = None

        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="入藏来源").pack(side="left")
        self.mode_var = tk.StringVar(value="单篇 .bib")
        self.mode_box = ttk.Combobox(bar, textvariable=self.mode_var, width=10,
                                     state="readonly",
                                     values=[label for label, _key in IMPORT_MODES])
        self.mode_box.pack(side="left", padx=6)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())
        ttk.Label(bar, text="（单篇 = 一份成品 .bib；整期目录 = 一期目录，或稿件库根）",
                  style="Muted.TLabel").pack(side="left")

        path_row = ttk.Frame(parent)
        path_row.pack(fill="x", pady=2)
        self.path_label = ttk.Label(path_row, text="成品 .bib", style="Muted.TLabel", width=12,
                                    anchor="e")
        self.path_label.pack(side="left", padx=(0, 8))
        self.path_var = tk.StringVar(value="")
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(path_row, text="浏览", width=8, command=self.browse).pack(side="left", padx=6)

        self.meta = ttk.Frame(parent)
        self.meta.pack(fill="x", pady=2)
        ttk.Label(self.meta, text="来源期", style="Muted.TLabel", width=12, anchor="e").pack(
            side="left", padx=(0, 8))
        self.issue_var = tk.StringVar(value="")
        ttk.Entry(self.meta, textvariable=self.issue_var, width=24).pack(side="left")
        ttk.Label(self.meta, text="篇目录名", style="Muted.TLabel").pack(side="left", padx=(12, 4))
        self.slug_var = tk.StringVar(value="")
        ttk.Entry(self.meta, textvariable=self.slug_var, width=20).pack(side="left")
        self.infer_hint = ttk.Label(parent, text="", style="Muted.TLabel",
                                    wraplength=800, justify="left")
        self.infer_hint.pack(fill="x", padx=(0, 0), pady=(0, 2))
        self.dir_hint = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=800,
                                  justify="left")
        self.dir_hint.pack(fill="x", pady=(0, 2))

        self.target = InfoRows(parent)
        self.target.pack(fill="x", pady=(2, 2))
        self.v_target = self.target.add("将写入")

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(4, 2))
        self.preview_btn = ttk.Button(buttons, text="预览（dry-run，不写库）", width=22,
                                      style="Accent.TButton", command=self.preview)
        self.preview_btn.pack(side="left")
        self.confirm_btn = ttk.Button(buttons, text="确认入藏", width=12, state="disabled",
                                      command=self.confirm)
        self.confirm_btn.pack(side="left", padx=8)
        self.report_btn = ttk.Button(buttons, text="重新生成清单", width=14, state="disabled",
                                     command=self.regenerate)
        self.report_btn.pack(side="left")
        self.busy = ttk.Label(buttons, text="", style="Muted.TLabel")
        self.busy.pack(side="left", padx=8)

        self.status = StatusLine(parent)
        self.status.pack(fill="x", pady=(2, 4))
        self.summary = ttk.Label(parent, text="", style="Section.TLabel", wraplength=800,
                                 justify="left")
        self.summary.pack(fill="x", pady=(2, 2))
        self.skipped = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=800,
                                 justify="left")
        self.skipped.pack(fill="x")
        self.warnings = ttk.Label(parent, text="", foreground=theme.COLOR["warn"],
                                  wraplength=800, justify="left")
        self.warnings.pack(fill="x")
        self.issues = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=800,
                                justify="left")
        self.issues.pack(fill="x")
        self.changes_label = ttk.Label(parent, text="", style="Muted.TLabel")
        self.changes_label.pack(fill="x", pady=(4, 0))
        self.changes = _tree(parent,
                             ("status", "key", "title", "author", "year", "note"),
                             ("状态", "key", "题名", "作者", "年份", "备注"),
                             (62, 150, 300, 130, 54, 160), height=7)
        self.report_message = ttk.Label(parent, text="", style="Muted.TLabel",
                                        wraplength=800, justify="left")
        self.report_message.pack(fill="x", pady=(2, 0))

        # 输入一改，旧预览作废（绝不让编辑照着旧预览点「确认入藏」）
        self.path_var.trace_add("write", lambda *_: self._input_changed())
        self.issue_var.trace_add("write", lambda *_: self._input_changed())
        self.slug_var.trace_add("write", lambda *_: self._input_changed())
        self._mode_changed()

    # ── 模式 / 路径 ──
    def _mode_key(self) -> str:
        return dict(IMPORT_MODES).get(self.mode_var.get(), "single")

    def _mode_changed(self) -> None:
        single = self._mode_key() == "single"
        self.path_label.config(text="成品 .bib" if single else "整期目录")
        if single:
            self.meta.pack(fill="x", pady=2, before=self.infer_hint)
            self.dir_hint.pack_forget()
        else:
            self.meta.pack_forget()
            self.dir_hint.pack(fill="x", pady=(0, 2), before=self.infer_hint)
        self._invalidate_preview()
        self._update_dir_hint()
        self._refresh_target()

    def browse(self) -> None:
        if self._mode_key() == "single":
            path = fd.askopenfilename(title="选择成品 .bib",
                                      filetypes=[("BibTeX", "*.bib"), ("全部文件", "*.*")])
        else:
            path = fd.askdirectory(title="选择一期目录（或稿件库根）")
        if path:
            self.path_var.set(path)

    def _input_changed(self) -> None:
        if self._syncing:
            return
        self._invalidate_preview()
        self._update_dir_hint()
        if self._mode_key() == "single":
            self._infer_from_path()

    def _infer_from_path(self) -> None:
        # ⚠️ 这里会回写 issue/slug 两个 StringVar，而它们的 trace 又调回 _input_changed：
        #    不加标志就是无限递归（Tk 的 write trace 每次 set 都会触发，哪怕值没变）。
        if self._syncing:
            return
        self._syncing = True
        try:
            issue, slug = E.infer_issue_and_slug(self.path_var.get())
            if issue:
                self.issue_var.set(issue)
            self.slug_var.set(slug)
        finally:
            self._syncing = False
        if issue:
            self.infer_hint.config(text=plain(
                f"已从路径推断来源：{issue} / {slug or '（篇目录名认不出，可手填）'}。"
                "来源是库里的审计凭据，写错比不写更糟：认不出就留空，别硬猜。"))
        else:
            self.infer_hint.config(text=plain(
                "没从路径认出「来源期」——留空表示不记录来源（不硬猜）。"
                "例：…/2026年第2期(87)/02 Hai_Xing/reference.bib"))

    def _update_dir_hint(self) -> None:
        if self._mode_key() == "single":
            return
        path = self.path_var.get().strip().rstrip("\\/")
        name = os.path.basename(path)
        if name and name[:4].isdigit() and "年第" in name:
            self.dir_hint.config(text=plain(
                f"识别到这是一期目录：{name}（只导入这一期；期名/篇名由引擎按目录名读取）。"))
        else:
            self.dir_hint.config(text=plain(
                "整期目录模式：选一期目录（如 2026年第2期(87)）只导入这一期；"
                "选稿件库根则导入其下全部期。哪份 bib 算成品 bib、成品期是否 verified，"
                "判据与种子导入同一份（引擎 scan_manuscripts）。"))

    def _refresh_target(self) -> None:
        notes = self.notes_root()
        db = E.bibdb_db_path(notes) if notes else None
        self.v_target.set(db or "（找不到私有笔记仓：请到「引用库设置」页指定）")
        self._target_db = db or ""

    def _input_signature(self) -> tuple:
        return _import_signature(self._mode_key(), self.path_var.get(),
                                 self.issue_var.get(), self.slug_var.get())

    def _invalidate_preview(self) -> None:
        had = self._preview is not None
        self._preview = None
        self._preview_signature = None
        self.confirm_btn.config(state="disabled")
        if had and not self._running:
            self.status.show("warn", "输入已改动：请重新预览。")

    # ── 预览 / 确认 ──
    def preview(self) -> None:
        self._run(dry_run=True)

    def confirm(self) -> None:
        if self._preview_signature != self._input_signature():
            self.status.show("warn", "先预览，且预览之后不要再改输入。")
            return
        self._run(dry_run=False)

    def _run(self, *, dry_run: bool) -> None:
        if self._running:
            return
        path = self.path_var.get().strip().strip('"')
        if not path:
            self.status.show("warn", "请先选择要入藏的文件 / 目录。")
            return
        mode = self._mode_key()
        if mode == "single":
            if not os.path.isfile(path):
                self.status.show("error", f"读不到要导入的 .bib：{path}（文件不存在，或这是个文件夹）")
                return
        elif not os.path.isdir(path):
            self.status.show("error", f"读不到目录：{path}（不存在或不是文件夹）")
            return
        notes = self.notes_root()
        if not notes:
            self.status.show("warn", "找不到私有笔记仓，无法确定引用库位置。"
                                     "请到「引用库 → 引用库设置」页指定笔记仓路径。")
            return
        self._refresh_target()
        try:
            if mode == "single":
                call = E.bibdb_import_bib_call(
                    bib=path, issue=self.issue_var.get().strip(),
                    slug=self.slug_var.get().strip(), dry_run=dry_run,
                    db=self._target_db, verified=True)
            else:
                call = E.bibdb_import_dir_call(root=path, dry_run=dry_run, db=self._target_db)
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        self._running = True
        signature = self._input_signature()      # 主线程取；工作线程不碰 Tk 变量
        self.preview_btn.config(state="disabled")
        self.confirm_btn.config(state="disabled")
        self.report_btn.config(state="disabled")
        self.busy.config(text="正在预览（dry-run，不写库）…" if dry_run else "正在写入引用库…")
        self.status.clear()
        self.summary.config(text="")
        self.skipped.config(text="")
        self.warnings.config(text="")
        self.issues.config(text="")
        self.changes_label.config(text="")
        for item in self.changes.get_children():
            self.changes.delete(item)
        self.log(f"引用库入藏 · {'预览（dry-run：不写库）' if dry_run else '确认导入（真正写库）'}："
                 f"{mode} {path}")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "dry_run": dry_run, "seconds": time.monotonic() - started,
                    "signature": signature}

        run_async(self.post, work, self._on_run_result)

    def _on_run_result(self, result: dict) -> None:
        self._running = False
        self.preview_btn.config(state="normal")
        self.busy.config(text="")
        dry_run = bool(result.get("dry_run"))
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        if code != 0 or not isinstance(payload, dict):
            self.status.show("error", ("预览失败：" if dry_run else "入藏失败：")
                             + E.output_tail(result.get("lines") or [], count=2))
            self.log(f"❌ 引用库{'预览' if dry_run else '入藏'}失败："
                     f"{E.output_tail(result.get('lines') or [], count=2)}")
            return
        report = payload.get("report") if isinstance(payload.get("report"), dict) else None
        if isinstance(payload.get("result"), dict):
            report = payload["result"].get("total") if isinstance(
                payload["result"].get("total"), dict) else report
        written = bool(payload.get("written"))
        if dry_run and written:
            # 引擎侧保证 dry-run 不写；真出现就是严重缺陷，界面必须喊出来而不是继续
            self.status.show("error", "⛔ 预览竟然写了库（引擎违反 dry-run 契约），请立即检查！")
            return
        self._fill_outcome(payload, report, dry_run=dry_run)
        if dry_run:
            self._preview = payload
            self._preview_signature = result.get("signature") or self._input_signature()
            self.confirm_btn.config(state="normal")
            self.status.show("warn", f"预览（dry-run）：未写库，库一个字节都没动，用时 "
                                     f"{fmt_seconds(float(result.get('seconds') or 0))}。"
                                     "确认无误再点「确认入藏」。")
        else:
            self._preview = None
            self._preview_signature = None
            self.report_btn.config(state="normal")
            self.status.show("ok", f"✅ 已入藏到引用库"
                                   + ("" if payload.get("db_existed") else "（库文件是本次新建的）")
                                   + f"，用时 {fmt_seconds(float(result.get('seconds') or 0))}。")
            self.log(f"✅ 引用库入藏完成：{summary_line(report)}")
            self.log("（库变了：到「引用库 → 待裁决」页重新生成清单。）")
            self.report_message.config(text="库变了，待裁决清单就该重算（清单只写文件、不改库）。")
        self._refresh_target()

    def _fill_outcome(self, payload: dict, report: dict | None, *, dry_run: bool) -> None:
        if report:
            self.summary.config(text=("（预览，未写库）" if dry_run else "✅ 已写库：")
                                + summary_line(report))
            if int(report.get("findings") or 0) > 0:
                self.warnings.config(text=f"⚠ bibparse 语法发现 {report['findings']} 处，未阻断导入")
            skip_text = skipped_line(report)
            self.skipped.config(text=plain("跳过原因：" + skip_text) if skip_text else "")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        issue_rows = result.get("issues") or []
        lines = []
        for issue in issue_rows:
            if not isinstance(issue, dict):
                continue
            files = issue.get("files") or []
            lines.append(f"[{'成品期' if issue.get('finished') else '未定稿'}] "
                         f"{issue.get('issue')}：{len(files)} 份 bib —— "
                         + summary_line(issue.get("report")))
        self.issues.config(text=plain("\n".join(lines)))
        warnings = list(result.get("warnings") or []) + list(payload.get("warnings") or [])
        if warnings:
            shown = "\n".join("⚠ " + str(item) for item in warnings[:6])
            self.warnings.config(text=plain(shown))
        changes = payload.get("changes") or []
        for item in self.changes.get_children():
            self.changes.delete(item)
        for change in changes[:60]:
            if not isinstance(change, dict):
                continue
            fields = change.get("conflict_fields") or []
            if change.get("status") == "conflict" and fields:
                note = "冲突字段：" + "、".join(str(name) for name in fields)
            elif change.get("status") == "merged" and int(change.get("sources_added") or 0):
                note = f"+{change['sources_added']} 来源"
            else:
                note = ""
            self.changes.insert("", "end", values=(
                STATUS_TITLES.get(str(change.get("status") or ""),
                                  str(change.get("status") or "")),
                str(change.get("key") or ""), str(change.get("title") or ""),
                str(change.get("author") or ""), str(change.get("year") or ""), note))
        if changes:
            shown = f"逐条变化（{len(changes)} 条，最多列 60）："
            if len(changes) > 60:
                shown += f"……另有 {len(changes) - 60} 条（完整明细见运行日志 / CLI --json）"
            self.changes_label.config(text=plain(shown))
        else:
            self.changes_label.config(text="逐条变化：无（库与输入完全一致）")

    # ── 清单 ──
    def regenerate(self) -> None:
        notes = self.notes_root()
        if not notes:
            self.status.show("warn", "找不到私有笔记仓，无法确定清单输出目录。")
            return
        db = E.bibdb_db_path(notes) or ""
        if not db or not os.path.isfile(db):
            self.status.show("warn", f"引用库文件还不存在：{db}（先入藏再来生成清单）")
            return
        try:
            call = E.bibdb_report_call(out_dir=E.notes_dir(notes) or "", db=db)
        except RuntimeError as exc:
            self.status.show("error", str(exc))
            return
        self.report_btn.config(state="disabled")
        self.report_message.config(text="正在生成 CONFLICTS.md / DUPLICATES.md …")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "seconds": time.monotonic() - started}

        run_async(self.post, work, self._on_report)

    def _on_report(self, result: dict) -> None:
        self.report_btn.config(state="normal")
        payload = result.get("payload")
        if int(result.get("code") or 0) != 0 or not isinstance(payload, dict):
            text = "⚠ 生成清单失败：" + E.output_tail(result.get("lines") or [], count=2)
            self.report_message.config(text=plain(text))
            self.log(plain(text))
            return
        conflicts = payload.get("conflicts") or {}
        reports = payload.get("reports") or []
        written = sum(1 for item in reports if isinstance(item, dict) and item.get("written"))
        try:
            conflict_records = int(conflicts.get("records") or 0)
            conflict_total = int(conflicts.get("total") or 0)
        except (TypeError, ValueError):
            conflict_records = conflict_total = 0
        text = (f"✅ 已重新生成清单（写入/更新 {written} 个文件；"
                f"字段冲突 {conflict_records} 条 / {conflict_total} 处差异，"
                f"同作品候选对 {int(payload.get('pairs') or 0)} 对）。")
        self.report_message.config(text=text)
        self.log(text)


# ── ④ 待裁决 ────────────────────────────────────────────────────────────

def adopt_confirm_detail(*, adopted, records, skipped, pending_before, pending_after,
                         verified_keys, limit: int = 10) -> str:
    """「全部采用」二次确认文本：计数 + 已核实风险 + 逐条 old → new（S7）。

    与 mac `BibDbReviewSheets.swift` 同口径：至少列出记录/字段计数、库内已核实
    条数与逐条 `key: 字段 旧值 → 新值`；超过 `limit` 条只列前 `limit` 条并注明总数。
    `verified_keys` 是库内 verified 记录的 record_key 集合（来自列表数据）。
    """
    changes: list[str] = []
    verified_count = 0
    for entry in adopted:
        if not isinstance(entry, dict):
            continue
        record_key = str(entry.get("record_key") or "")
        if record_key in verified_keys:
            verified_count += 1
        key = str(entry.get("bib_key") or record_key or "?")
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        for name, change in fields.items():
            pair = change if isinstance(change, dict) else {}
            old = str(pair.get("old") or "").strip() or "（空）"
            if pair.get("remove"):
                new = "（删除）"
            else:
                new = str(pair.get("new") or "").strip() or "（空）"
            changes.append(f"{key}: {name} {old} → {new}")
    lines = [
        f"将采用 {len(adopted)} 条冲突 / {int(records or 0)} 条记录 / "
        f"{len(changes)} 个字段；跳过 {int(skipped or 0)} 条。",
        f"待裁决 {pending_before} → {pending_after} 条。",
    ]
    if verified_count:
        lines.append(f"⚠ 其中已核实 {verified_count} 条：采用新值会改已出版核对结果，"
                     "请逐条核对下面的 old → new。")
    else:
        lines.append("其中已核实 0 条（本次不改已核实记录）。")
    lines.append("逐条变更（key: 字段 旧值 → 新值）：")
    lines.extend("  · " + line for line in changes[:limit])
    if len(changes) > limit:
        lines.append(f"  …等 {len(changes)} 条")
    lines.append("确认后单次写库，并只把 bib_db/bibliography.jsonl 提交到本地 git（不 push）。")
    return "\n".join(lines)


class BibDbReviewPage(BibDbPage):
    name = "待裁决"        # 侧栏里显示的名字（组 → 页的第二层）
    """待裁决：逐条裁决写库，写成功后只把库文件提交到本地 git（B-282a）。

    与 mac「待处理」对等：keep / adopt / defer 三按钮、编辑并替换、全部采用；
    每次写库都带 `--expect-db-sha256`（列表加载时的库 sha），过期时引擎退出 2、
    库一个字节都不动 → 提示刷新并临时置灰。写库成功后由 `bibdb_git.py` **只**
    `git add/commit -- bib_db/bibliography.jsonl`，绝不 push/pull/切分支；
    git 不可用/身份缺失只黄字提示，**不改写库成功与否的语义**。
    """

    page_title = "引用库 · 待裁决"
    page_subtitle = """引用库里已核实条目遇到「同一作品、不同期成品 bib 字段写法不同」时，库不会静默改掉
旧值，只把差异留档成 conflicts。这一页把待裁决清单逐条列出来：可以「保留旧值 / 采用新值 /
待人工」，也可以编辑后替换、或全部采用；写库会先带库指纹校验，过期只提示刷新、绝不盲写。
写库成功后只把 bib_db/bibliography.jsonl 提交到本地 git（不 push / 不 pull / 不切分支）。"""

    COLUMNS = ("no", "key", "title", "year", "state")
    HEADS = ("C 编号", "库内 key", "题名", "年份", "状态")
    WIDTHS = (66, 190, 330, 56, 80)
    ACTION_TITLES = {"keep": "保留旧值", "adopt": "采用新值", "defer": "待人工",
                     "replace": "替换"}
    RESOLUTION_TITLES = {"keep": "保留旧值", "adopt": "采用新值", "defer": "待人工"}
    FIELD_LABELS = {"title": "题名", "author": "作者", "editor": "编者", "year": "年份",
                    "journal": "期刊", "booktitle": "书名", "publisher": "出版者",
                    "volume": "卷", "number": "期", "pages": "页码", "doi": "DOI",
                    "url": "URL", "note": "附注", "edition": "版次", "address": "出版地"}

    # ── 建页 ──
    def build(self, parent: ttk.Frame) -> None:
        self._running = False
        self._items: list[dict] = []
        self._by_no: dict[str, dict] = {}
        self._db_sha = ""
        self._stale = False
        self._git: dict | None = None
        self._replace: dict | None = None
        self._notice: tuple[str, str] | None = None
        self._loaded_pending_only = True

        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        self.reload_btn = ttk.Button(bar, text="刷新", width=8, command=self.reload)
        self.reload_btn.pack(side="left")
        self.pending_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="只看待裁决", variable=self.pending_var,
                        command=self.reload).pack(side="left", padx=(8, 4))
        self.regen_btn = ttk.Button(bar, text="重新生成清单", width=14,
                                    command=self.regenerate)
        self.regen_btn.pack(side="left", padx=(10, 4))
        self.open_dir_btn = ttk.Button(bar, text="打开清单文件夹", width=14,
                                       command=self.open_folder)
        self.open_dir_btn.pack(side="left")
        self.busy = ttk.Label(bar, text="", style="Muted.TLabel")
        self.busy.pack(side="left", padx=8)

        self.count_label = ttk.Label(parent, text="", style="Section.TLabel")
        self.count_label.pack(anchor="w", pady=(2, 2))
        self.git_line = ttk.Label(parent, text="git：未检查", style="Muted.TLabel",
                                  wraplength=880, justify="left")
        self.git_line.pack(fill="x")
        self.status = StatusLine(parent)
        self.status.pack(fill="x", pady=(2, 0))

        split = ttk.Frame(parent)
        split.pack(fill="both", expand=True, pady=(4, 2))
        split.columnconfigure(0, weight=3)
        split.columnconfigure(1, weight=2)
        split.rowconfigure(0, weight=1)
        left = ttk.Frame(split)
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(split)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.tree = _tree(left, self.COLUMNS, self.HEADS, self.WIDTHS, height=13)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        detail_box = ttk.Frame(right)
        detail_box.pack(fill="both", expand=True)
        self.detail = tk.Text(detail_box, height=13, width=50, wrap="word",
                              background=theme.COLOR["text_bg"],
                              foreground=theme.COLOR["fg"],
                              insertbackground=theme.COLOR["fg"],
                              borderwidth=1, relief="solid", highlightthickness=0,
                              state="disabled")
        self.detail.tag_configure("risk", foreground=theme.COLOR["error"])
        detail_scroll = ttk.Scrollbar(detail_box, orient="vertical",
                                      command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(6, 2))
        self.keep_btn = ttk.Button(actions, text="保留旧值", width=10,
                                   command=lambda: self._write("keep"))
        self.keep_btn.pack(side="left")
        self.adopt_btn = ttk.Button(actions, text="采用新值", width=10,
                                    style="Accent.TButton",
                                    command=lambda: self._write("adopt"))
        self.adopt_btn.pack(side="left", padx=4)
        self.defer_btn = ttk.Button(actions, text="待人工", width=10,
                                    command=lambda: self._write("defer"))
        self.defer_btn.pack(side="left")
        self.replace_btn = ttk.Button(actions, text="编辑并替换…", width=12,
                                      command=self._open_replace)
        self.replace_btn.pack(side="left", padx=(12, 4))
        self.adopt_all_btn = ttk.Button(actions, text="全部采用…", width=12,
                                        command=self._adopt_all)
        self.adopt_all_btn.pack(side="left")
        self.hint = ttk.Label(actions, text="", style="Muted.TLabel")
        self.hint.pack(side="left", padx=8)

        self.message = ttk.Label(parent, text="", wraplength=880, justify="left",
                                 foreground=theme.COLOR["ok"])
        self.message.pack(fill="x", pady=(2, 0))
        self.git_notice = ttk.Label(parent, text="", wraplength=880, justify="left",
                                    foreground=theme.COLOR["warn"])
        self.git_notice.pack(fill="x")

        self.rows = InfoRows(parent)
        self.rows.pack(fill="x", pady=(6, 0))
        self.v_notes = self.rows.add("笔记仓")
        self.v_dir = self.rows.add("清单目录")
        self.v_db = self.rows.add("库文件")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(parent, text="清单文件（只读）", style="Section.TLabel").pack(anchor="w")
        self.file_rows: dict[str, dict] = {}
        for name in E.BIBDB_REPORT_FILES:
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=name, width=16).pack(side="left")
            state = ttk.Label(row, text="（还没生成）", style="Muted.TLabel")
            state.pack(side="left", padx=8)
            button = ttk.Button(row, text="打开", width=8, state="disabled",
                                command=lambda n=name: self.open_named(n))
            button.pack(side="left")
            self.file_rows[name] = {"state": state, "button": button}
        ttk.Label(parent, text=plain(
            "CONFLICTS.md 是同一批冲突的中文清单（逐条带旧值/新值与建议）；"
            "DUPLICATES.md / duplicates.csv 是同作品候选对，仅供参考、不会自动合并。"
            "重新生成清单只写这三个文件，不改库。"),
            style="Muted.TLabel", wraplength=880, justify="left").pack(fill="x", pady=(2, 0))

    def on_activate(self) -> None:
        self.reload()

    # ── 路径 ──
    def _paths(self) -> tuple[str | None, str | None, str | None]:
        notes = self.notes_root()
        return notes, E.notes_dir(notes), E.bibdb_db_path(notes)

    def _current_db(self) -> str:
        _notes, _directory, db = self._paths()
        return db or ""

    # ── 读列表 + git 状态 ──
    def reload(self, notice: tuple[str, str] | None = None) -> None:
        if self._running:
            return
        if notice is not None:
            self._notice = notice
        notes, directory, db = self._paths()
        self.v_notes.set(notes or "（未找到私有笔记仓）")
        self.v_dir.set(directory or "（找不到私有笔记仓：请到「引用库设置」页指定）")
        self.v_db.set(db or "（找不到私有笔记仓）")
        self.reload_files()
        self._loaded_pending_only = bool(self.pending_var.get())
        if not db or not os.path.isfile(db):
            self._items = []
            self._by_no = {}
            self._db_sha = ""
            self._git = None
            self._fill_tree()
            self._render_git()
            self.count_label.config(text="（还没有引用库文件）")
            self._sync_actions()
            self.status.show("warn", f"引用库文件还不存在：{db or '（未找到）'}\n"
                                     "先到「入藏」页导入一份 .bib，再来裁决。")
            return
        try:
            call = E.bibdb_conflicts_call(db=db,
                                          pending_only=self._loaded_pending_only)
        except RuntimeError as exc:
            self.status.show("error", str(exc))
            return
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")
        self._running = True
        self._stale = False
        self.busy.config(text="正在读取待裁决清单…")
        self.status.clear()
        self._sync_actions()
        self.log(f"引用库待裁决：读取列表（{db}）")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            payload = E.parse_json_lines(lines)
            try:
                git_info = G.status(db)
            except Exception as exc:  # noqa: BLE001  版本层失败只黄字，不影响读列表
                git_info = {"ok": False, "error": f"git 状态检查失败：{exc}"}
            return {"code": code, "lines": lines, "payload": payload,
                    "git": git_info, "seconds": time.monotonic() - started}

        run_async(self.post, work, self._on_loaded)

    def _on_loaded(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        self._git = result.get("git") if isinstance(result.get("git"), dict) else None
        self._render_git()
        notice, self._notice = self._notice, None
        if notice is not None:
            self.message.config(text=plain(notice[1]),
                                foreground=theme.COLOR.get(notice[0], theme.COLOR["ok"]))
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        if code != 0 or not isinstance(payload, dict):
            self._items = []
            self._fill_tree()
            self.count_label.config(text="")
            self.status.show("error", "❌ 读取待裁决清单失败（退出码 "
                             f"{code}）：{E.output_tail(result.get('lines') or [], count=2)}")
            self._sync_actions()
            return
        raw = payload.get("items") or []
        self._items = [item for item in raw if isinstance(item, dict)]
        self._by_no = {self._no(item): item for item in self._items}
        self._db_sha = str(payload.get("db_sha256") or "")
        self._stale = False
        totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
        try:
            total = int(totals.get("total") or 0)
            pending = int(totals.get("pending") or 0)
            resolved = int(totals.get("resolved") or 0)
        except (TypeError, ValueError):
            total = pending = resolved = 0
        shown = len(self._items)
        verified = sum(1 for item in self._items
                       if item.get("pending") and item.get("verified") is True)
        count_text = (f"待裁决 {pending} 条 / 已裁决 {resolved} 条 / 共 {total} 条"
                      + (f"（当前列表 {shown} 条）" if self._loaded_pending_only else "")
                      + (f" · ⚠ 已核实 {verified} 条" if verified
                         else " · 已核实 0 条"))
        self.count_label.config(
            text=plain(count_text),
            foreground=theme.COLOR["error"] if verified else theme.COLOR["fg"])
        self._fill_tree()
        if not self._items:
            self.status.show("ok", "当前没有待裁决条目。")
        else:
            self.status.show("ok", f"已读取列表：{shown} 条；库指纹 {self._db_sha[:12]}…")
        self._sync_actions()

    # ── 列表 / 详情 ──
    @staticmethod
    def _no(item: dict) -> str:
        try:
            return f"C{int(item.get('stable_no') or 0):03d}"
        except (TypeError, ValueError):
            return "C000"

    def _state_text(self, item: dict) -> str:
        if item.get("pending"):
            return "待裁决"
        resolution = str(item.get("resolution") or "")
        return "已裁决：" + self.RESOLUTION_TITLES.get(resolution, resolution or "—")

    def _fill_tree(self) -> None:
        previous = ""
        selection = self.tree.selection()
        if selection:
            previous = selection[0]
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for item in self._items:
            no = self._no(item)
            try:
                self.tree.insert("", "end", iid=no, values=(
                    no, str(item.get("record_key") or ""), str(item.get("title") or ""),
                    str(item.get("year") or ""), self._state_text(item)))
            except tk.TclError:      # 极端情况：重复编号（引擎不应发生）
                continue
        children = self.tree.get_children()
        target = previous if previous in children else (children[0] if children else "")
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
        else:
            self._on_select()

    def _on_select(self, _event=None) -> None:
        self._show_detail(self._selected_item())
        self._sync_actions()

    def _selected_item(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._by_no.get(selection[0])

    def _show_detail(self, item: dict | None) -> None:
        risk_text = ""
        if item is None:
            text = "在左侧选一条冲突，这里会显示字段差异与建议。"
        else:
            verified = item.get("verified") is True
            lines = [f"{self._no(item)} · {self._state_text(item)}", ""]
            if verified:
                risk_text = ("⚠ 库内已核实记录：采用新值会改已出版核对结果，"
                             "请先逐条核对。")
                lines.append(risk_text)
                lines.append("")
            fields = (("库内记录 key", item.get("record_key")),
                      ("来源 bib key", item.get("bib_key")),
                      ("题名", item.get("title")),
                      ("作者", item.get("author")),
                      ("年份", item.get("year")),
                      ("类型", item.get("entry_type")),
                      ("已核实", "是" if verified else "否"),
                      ("来源", "/".join(str(part) for part in
                                        (item.get("issue"), item.get("slug"))
                                        if part) or "（无期/篇）"),
                      ("来源文件", item.get("file")),
                      ("入库时间", item.get("at")),
                      ("新稿指纹", item.get("incoming_sha256")),
                      ("建议", item.get("advice")))
            for label, value in fields:
                if value:
                    lines.append(f"{label}：{value}")
            if not item.get("pending"):
                lines.append(f"裁决：{item.get('resolution') or '—'}"
                             f"（{item.get('resolved_at') or '—'}）")
            diff = item.get("diff") if isinstance(item.get("diff"), dict) else {}
            lines.append("")
            lines.append(f"字段差异（{len(diff)} 个；旧值 → 新稿值）：" if diff else
                         "字段差异：无（可能只是在来源/核实状态上不同）")
            for name, change in diff.items():
                label = self.FIELD_LABELS.get(str(name), str(name))
                if isinstance(change, dict):
                    old = str(change.get("old") or "").strip() or "（空）"
                    new = str(change.get("new") or "").strip() or "（空）"
                else:
                    old, new = "", str(change)
                lines.append(f"  · {label}：{old} → {new}")
            text = "\n".join(lines)
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", plain(text))
        if risk_text:
            needle = plain(risk_text)
            start = self.detail.search(needle, "1.0", "end")
            if start:
                self.detail.tag_add("risk", start, f"{start}+{len(needle)}c")
        self.detail.configure(state="disabled")

    # ── 按钮状态 / git 行 ──
    def _sync_actions(self) -> None:
        busy = self._running or self._replace is not None
        item = self._selected_item() if hasattr(self, "tree") else None
        pending = bool(item and item.get("pending"))
        fingerprint = bool(item and str(item.get("incoming_sha256") or "").strip())
        usable = bool(pending and not busy and not self._stale)
        for button in (self.keep_btn, self.defer_btn):
            button.config(state="normal" if usable else "disabled")
        self.adopt_btn.config(state="normal" if usable and fingerprint else "disabled")
        self.replace_btn.config(state="normal" if usable and fingerprint else "disabled")
        pending_count = sum(1 for it in self._items if it.get("pending"))
        self.adopt_all_btn.config(state="normal" if (
            pending_count and not busy and not self._stale) else "disabled")
        self.reload_btn.config(state="disabled" if busy else "normal")
        self.regen_btn.config(state="disabled" if busy else "normal")
        if self._stale:
            self.hint.config(text="库已变化：先刷新再裁决。")
        elif pending and not fingerprint:
            self.hint.config(text="这条没有新稿指纹：只能保留旧值 / 待人工。")
        elif busy:
            self.hint.config(text="正在处理…")
        else:
            self.hint.config(text="")

    def _render_git(self) -> None:
        git = self._git
        if not isinstance(git, dict):
            self.git_line.config(text="git：未检查（写库仍可用）",
                                 foreground=theme.COLOR["muted"])
            return
        if not git.get("ok"):
            self.git_line.config(text="git：" + plain(str(git.get("error") or "不可用"))
                                 + "（写库仍可用，只是不生成版本提交）",
                                 foreground=theme.COLOR["warn"])
            return
        text = f"git：{git.get('branch') or '（没有分支）'}"
        if git.get("upstream"):
            text += (f" · upstream {git['upstream']} ↓{git.get('behind')}"
                     f" ↑{git.get('ahead')}")
        else:
            text += " · 无 upstream"
        text += " · 库文件" + ("有未提交改动（本次提交会一并包含）"
                               if git.get("dirty_db") else "干净")
        if int(git.get("dirty_other") or 0):
            text += f" · 其它 {git['dirty_other']} 个改动不会提交"
        text += " · 身份" + ("已配置" if git.get("identity_ok") else "未配置（不会生成提交）")
        if git.get("conflict"):
            text += " · ⛔ 有未解决冲突"
        if git.get("rebase"):
            text += " · ⛔ rebase 进行中"
        if git.get("merge"):
            text += " · ⛔ merge 进行中"
        color = theme.COLOR["muted"]
        if git.get("conflict") or git.get("rebase") or git.get("merge"):
            color = theme.COLOR["error"]
        elif (git.get("dirty_db") or not git.get("identity_ok")
              or not git.get("upstream")):
            color = theme.COLOR["warn"]
        self.git_line.config(text=plain(text), foreground=color)

    def _refresh_git_status(self) -> bool:
        """写库入口重跑只读 git 预检：页面加载时缓存的 `self._git` 可能已过期（S4）。"""
        db = self._current_db()
        if not db or not os.path.isfile(db):
            return False
        try:
            git_info = G.status(db)
        except Exception as exc:  # noqa: BLE001  git 失败只黄字，不阻断写库
            git_info = {"ok": False, "error": f"git 状态检查失败：{exc}"}
        self._git = git_info
        self._render_git()
        return True

    def _git_blocked(self) -> bool:
        git = self._git if isinstance(self._git, dict) else {}
        if not git.get("ok"):
            return False
        if git.get("conflict") or git.get("rebase") or git.get("merge"):
            self.status.show("error", "⛔ 仓库有未解决的合并冲突 / rebase / merge 进行中："
                                     "先处理完 git 状态再写库。")
            return True
        return False

    def _behind_ok(self) -> bool:
        git = self._git if isinstance(self._git, dict) else {}
        behind = git.get("behind") if git.get("ok") else None
        if isinstance(behind, int) and behind > 0:
            return bool(mb.askyesno(
                "本地落后 upstream",
                f"当前分支落后 upstream {behind} 个提交。本工具不会自动 pull；"
                "仍要写库并只提交库文件吗？"))
        return True

    @staticmethod
    def _is_stale_text(text: str) -> bool:
        raw = str(text or "")
        return ("sha256" in raw and ("不符" in raw or "刷新" in raw)) or "库已变化" in raw

    # ── 逐条裁决（keep / adopt / defer）──
    def _write(self, action: str) -> None:
        item = self._selected_item()
        if item is None:
            self.status.show("warn", "先在列表里选一条冲突。")
            return
        if self._running or self._replace is not None:
            return
        if self._stale:
            self.status.show("warn", "库已变化：先点「刷新」再裁决（本次没有写库）。")
            return
        if not item.get("pending"):
            self.status.show("warn", "这条已经裁决过；要重做请重新生成清单后刷新。")
            return
        fingerprint = str(item.get("incoming_sha256") or "").strip()
        if action in ("adopt", "replace") and not fingerprint:
            self.status.show("warn", "这条没有新稿指纹：不能采用/替换；可保留旧值或待人工。")
            return
        db = self._current_db()
        if not db or not os.path.isfile(db):
            self.status.show("warn", "找不到引用库文件，无法写库。")
            return
        # S4：写库前重跑只读 git 预检（页面加载时缓存的 self._git 可能已过期）。
        self._refresh_git_status()
        if self._git_blocked() or not self._behind_ok():
            return
        try:
            call = E.bibdb_resolve_call(
                key=str(item.get("record_key") or ""), action=action,
                incoming_sha256=fingerprint, all_fields=not fingerprint,
                expect_db_sha256=self._db_sha, db=db)
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        no = self._no(item)
        self._running = True
        self.busy.config(text="正在写库…")
        self.status.clear()
        self.message.config(text="")
        self.git_notice.config(text="")
        self._sync_actions()
        self.log(f"引用库裁决：{no} {action}（key={item.get('record_key')}）")
        meta = {"label": f"裁决 {no} {self.ACTION_TITLES.get(action, action)}",
                "action_text": f"裁决 {no} {action}", "record_count": 1,
                "kind": "resolve", "action": action, "no": no}
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "meta": meta, "seconds": time.monotonic() - started}

        run_async(self.post, work, self._on_written)

    def _on_written(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        lines = result.get("lines") or []
        if code != 0 or not isinstance(payload, dict):
            tail = E.output_tail(lines, count=2)
            if self._is_stale_text(tail):
                self._stale = True
                self.status.show("warn", "库已变化（sha256 不符）：本次没有写库；"
                                         "请先「刷新」再裁决。")
                self.log("引用库裁决：库指纹已过期，未写库（请刷新）。")
            else:
                self.status.show("error", f"❌ {meta.get('label', '裁决')}失败（未写库，"
                                 f"退出码 {code}）：{tail}")
                self.log(f"❌ 引用库裁决失败：{tail}")
            self._sync_actions()
            return
        if payload.get("error"):
            self.status.show("error", "❌ " + plain(str(payload["error"])))
            self._sync_actions()
            return
        written = bool(payload.get("written"))
        already = bool(payload.get("already"))
        no = str(meta.get("no") or "裁决")
        if written and not already:
            self.status.show("ok", f"✅ 已写入 {no}（{self.ACTION_TITLES.get(meta.get('action'), '')}）："
                             f"库 sha {str(payload.get('db_sha256_before') or '')[:12]}… → "
                             f"{str(payload.get('db_sha256_after') or '')[:12]}…")
            self.log(f"✅ 引用库裁决已写库：{no} {meta.get('action')}")
            self._start_commit(meta, payload)
            return
        if already:
            self._notice = ("ok", f"这条已经裁决过（库未再改，未生成新提交）。")
        else:
            self._notice = ("warn", "引擎没有写库（库未改）：可能是这条已无差异或已被处理。")
        self._sync_actions()
        self.reload()

    # ── 本地提交（只提交库文件）──
    def _start_commit(self, meta: dict, payload: dict) -> None:
        db = self._current_db()
        message = G.commit_message(
            str(meta.get("action_text") or "裁决"),
            int(meta.get("record_count") or 1),
            str(payload.get("db_sha256_before") or ""),
            str(payload.get("db_sha256_after") or ""))
        self.busy.config(text="正在本地提交（只提交库文件；不 push）…")
        self._running = True
        self._sync_actions()

        def work() -> dict:
            try:
                commit = G.commit_db(db, message)
            except Exception as exc:  # noqa: BLE001  版本层失败绝不改写库语义
                commit = {"ok": False, "committed": False, "soft": True,
                          "error": f"版本层异常：{exc}"}
            try:
                git_info = G.status(db)
            except Exception as exc:  # noqa: BLE001
                git_info = {"ok": False, "error": f"git 状态检查失败：{exc}"}
            return {"commit": commit, "git": git_info, "meta": meta}

        run_async(self.post, work, self._on_committed)

    def _on_committed(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
        self._git = result.get("git") if isinstance(result.get("git"), dict) else None
        self._render_git()
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        no = str(meta.get("no") or "裁决")
        if commit.get("committed"):
            sha = G.short_sha(str(commit.get("sha_after") or ""))
            self._notice = ("ok", f"✅ 已写入 {no}；已本地提交 {sha}（未推送）。")
            self.git_notice.config(text="")
            self.log(f"引用库版本：已本地提交 {sha}（仅 {G.DB_RELATIVE_PATH}，未推送）")
        elif commit.get("ok"):
            self._notice = ("ok", f"✅ 已写入 {no}；库文件没有可提交改动（未空提交）。")
            self.git_notice.config(text="⚠ 写库已成功，但库文件没有可提交改动：未生成提交。")
            self.log("引用库版本：库文件无改动，未生成空提交。")
        else:
            error = plain(str(commit.get("error") or "未知原因"))
            self._notice = ("warn", f"已写入 {no}（写库成功）；本地提交未生成。")
            self.git_notice.config(text="⚠ 写库已成功，但未生成版本提交：" + error)
            self.log(f"⚠ 引用库版本：未生成提交：{error}")
        self._sync_actions()
        self.reload()

    # ── 全部采用 ──
    def _adopt_all(self) -> None:
        if self._running or self._replace is not None:
            return
        if self._stale:
            self.status.show("warn", "库已变化：先点「刷新」再全部采用。")
            return
        pending = [it for it in self._items if it.get("pending")]
        if not pending:
            self.status.show("warn", "当前没有待裁决条目。")
            return
        db = self._current_db()
        if not db or not os.path.isfile(db):
            self.status.show("warn", "找不到引用库文件，无法写库。")
            return
        # 预览前先刷新 git 状态；真正的 behind 二次确认放在确认后的真写入口。
        self._refresh_git_status()
        if self._git_blocked():
            return
        try:
            call = E.bibdb_adopt_all_call(expect_db_sha256=self._db_sha, dry_run=True,
                                          db=db)
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        self._running = True
        self.busy.config(text="正在预览「全部采用」（dry-run，不写库）…")
        self.status.clear()
        self.message.config(text="")
        self.git_notice.config(text="")
        self._sync_actions()
        self.log("引用库待裁决：全部采用 · 预览（dry-run）")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines)}

        run_async(self.post, work, self._on_adopt_preview)

    def _on_adopt_preview(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        lines = result.get("lines") or []
        if code != 0 or not isinstance(payload, dict):
            tail = E.output_tail(lines, count=2)
            if self._is_stale_text(tail):
                self._stale = True
                self.status.show("warn", "库已变化（sha256 不符）：预览没有写库；请先刷新。")
            else:
                self.status.show("error", f"❌ 全部采用预览失败（库未改，退出码 {code}）：{tail}")
            self._sync_actions()
            return
        if payload.get("written"):
            self.status.show("error", "⛔ 预览竟然写了库（引擎违反 dry-run 契约），请立即检查！")
            self._sync_actions()
            return
        adopted = payload.get("adopted") if isinstance(payload.get("adopted"), list) else []
        skipped = payload.get("skipped") if isinstance(payload.get("skipped"), list) else []
        if not adopted:
            self.status.show("warn", "没有可采用的待裁决冲突（预览 0 条）。")
            self._sync_actions()
            return
        before = payload.get("totals_before") or {}
        after = payload.get("totals_after") or {}
        verified_keys = {str(it.get("record_key") or "") for it in self._items
                         if it.get("pending") and it.get("verified") is True}
        detail = plain(adopt_confirm_detail(
            adopted=adopted, records=int(payload.get("records") or 0),
            skipped=len(skipped), pending_before=before.get("pending"),
            pending_after=after.get("pending"), verified_keys=verified_keys))
        self._sync_actions()
        if not mb.askyesno("确认全部采用（写库）", detail):
            self.status.show("warn", "已取消：未写库（预览没有改库）。")
            return
        self._adopt_all_write(payload)

    def _adopt_all_write(self, preview: dict) -> None:
        db = self._current_db()
        if not db or not os.path.isfile(db):
            self.status.show("warn", "找不到引用库文件，无法写库。")
            self._sync_actions()
            return
        # S4：确认后、真写前再刷新一次 git 状态（预览期间仓库可能变化）。
        self._refresh_git_status()
        if self._git_blocked() or not self._behind_ok():
            self._sync_actions()
            return
        try:
            call = E.bibdb_adopt_all_call(expect_db_sha256=self._db_sha, dry_run=False,
                                          db=db)
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        adopted = preview.get("adopted") if isinstance(preview.get("adopted"), list) else []
        # 与 mac 同口径：动作文案里的 N 是「记录数」payload.records（不是冲突条数）。
        records = int(preview.get("records") or len(adopted) or 0)
        meta = {"label": "全部采用", "action_text": f"一键采用 {records} 条",
                "record_count": records, "kind": "adopt-all", "no": "全部"}
        self._running = True
        self.busy.config(text="正在全部采用（写库）…")
        self.status.clear()
        self._sync_actions()
        self.log(f"引用库待裁决：全部采用（写库，{len(adopted)} 条冲突）")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "meta": meta}

        run_async(self.post, work, self._on_adopt_written)

    def _on_adopt_written(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        lines = result.get("lines") or []
        if code != 0 or not isinstance(payload, dict):
            tail = E.output_tail(lines, count=2)
            if self._is_stale_text(tail):
                self._stale = True
                self.status.show("warn", "库已变化（sha256 不符）：本次没有写库；请先刷新。")
            else:
                self.status.show("error", f"❌ 全部采用失败（库未改或已回滚，退出码 "
                                 f"{code}）：{tail}")
                self.log(f"❌ 全部采用失败：{tail}")
            self._sync_actions()
            return
        if payload.get("error"):
            self.status.show("error", "❌ " + plain(str(payload["error"])))
            self._sync_actions()
            return
        if not payload.get("written"):
            self._notice = ("warn", "全部采用没有写库（库未改）。")
            self._sync_actions()
            self.reload()
            return
        adopted = payload.get("adopted") if isinstance(payload.get("adopted"), list) else []
        before = payload.get("totals_before") or {}
        after = payload.get("totals_after") or {}
        self.status.show("ok", f"✅ 已全部采用：{len(adopted)} 条冲突 / "
                         f"{int(payload.get('records') or 0)} 条记录；"
                         f"待裁决 {before.get('pending')} → {after.get('pending')}")
        self.log(f"✅ 引用库全部采用：{len(adopted)} 条冲突")
        self._start_commit(meta, payload)

    # ── 编辑并替换 ──
    def _open_replace(self) -> None:
        if self._running or self._replace is not None:
            return
        item = self._selected_item()
        if item is None:
            self.status.show("warn", "先在列表里选一条冲突。")
            return
        if self._stale:
            self.status.show("warn", "库已变化：先点「刷新」再替换。")
            return
        if not item.get("pending"):
            self.status.show("warn", "这条已经裁决过。")
            return
        if not str(item.get("incoming_sha256") or "").strip():
            self.status.show("warn", "这条没有新稿指纹：不能用替换；可保留旧值或待人工。")
            return
        db = self._current_db()
        if not db or not os.path.isfile(db):
            self.status.show("warn", "找不到引用库文件，无法替换。")
            return
        # 打开编辑器前刷新一次；真正的 behind 二次确认在「确认替换」真写入口。
        self._refresh_git_status()
        if self._git_blocked():
            return
        try:
            call = E.bibdb_query_call("key", str(item.get("record_key") or ""), db=db)
        except (RuntimeError, ValueError) as exc:
            self.status.show("error", str(exc))
            return
        self._running = True
        self.busy.config(text="正在读取库内 BibTeX…")
        self._sync_actions()
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "item": item, "db": db}

        run_async(self.post, work, self._on_replace_loaded)

    def _on_replace_loaded(self, result: dict) -> None:
        self._running = False
        self.busy.config(text="")
        item = result.get("item") if isinstance(result.get("item"), dict) else None
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        if item is None:
            self._sync_actions()
            return
        if code != 0 or not isinstance(payload, dict):
            self.status.show("error", "读取库内 BibTeX 失败：" +
                             E.output_tail(result.get("lines") or [], count=2))
            self._sync_actions()
            return
        hits = payload.get("hits") if isinstance(payload.get("hits"), list) else []
        bibtex = ""
        for hit in hits:
            if isinstance(hit, dict) and str(hit.get("key") or "") == \
                    str(item.get("record_key") or ""):
                bibtex = str(hit.get("bibtex") or "")
                break
        if not bibtex and hits and isinstance(hits[0], dict):
            bibtex = str(hits[0].get("bibtex") or "")
        if not bibtex:
            self.status.show("error", "查不到这条记录的 BibTeX（库可能刚被改动）：请先刷新。")
            self._sync_actions()
            return
        self._build_replace_dialog(item, str(result.get("db") or self._current_db()), bibtex)
        self._sync_actions()

    def _build_replace_dialog(self, item: dict, db: str, bibtex: str) -> None:
        top = tk.Toplevel(self)
        no = self._no(item)
        top.title(f"编辑并替换 · {no}")
        top.transient(self.winfo_toplevel())
        top.geometry("820x580")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(2, weight=1)
        ttk.Label(top, text=plain(
            f"{no} · {item.get('record_key')} —— 修改下面的 BibTeX（必须恰好 1 条、"
            "key 不能改），先「预览（dry-run）」看字段变化，再「确认替换（写库）」。"),
            wraplength=780, justify="left").grid(row=0, column=0, sticky="w",
                                                 padx=10, pady=(8, 2))
        ttk.Label(top, text=plain(
            "替换走引擎 resolve --action replace：字段规范化/key 查重等判据全在引擎；"
            "写库成功后会只提交 bib_db/bibliography.jsonl（不 push）。"),
            style="Muted.TLabel", wraplength=780, justify="left").grid(
            row=1, column=0, sticky="w", padx=10, pady=(0, 4))
        text_frame = ttk.Frame(top)
        text_frame.grid(row=2, column=0, sticky="nsew", padx=10)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        editor = tk.Text(text_frame, wrap="none", undo=True, font=theme.mono_font(),
                         background=theme.COLOR["text_bg"], foreground=theme.COLOR["fg"],
                         insertbackground=theme.COLOR["fg"],
                         borderwidth=1, relief="solid", highlightthickness=0)
        ybar = ttk.Scrollbar(text_frame, orient="vertical", command=editor.yview)
        editor.configure(yscrollcommand=ybar.set)
        editor.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        editor.insert("1.0", bibtex)
        status = StatusLine(top)
        status.grid(row=3, column=0, sticky="w", padx=10, pady=(4, 0))
        buttons = ttk.Frame(top)
        buttons.grid(row=4, column=0, sticky="e", padx=10, pady=8)
        preview_btn = ttk.Button(buttons, text="预览（dry-run，不写库）",
                                 command=self._replace_preview)
        preview_btn.pack(side="left")
        confirm_btn = ttk.Button(buttons, text="确认替换（写库）", style="Accent.TButton",
                                 state="disabled", command=self._replace_confirm)
        confirm_btn.pack(side="left", padx=6)
        ttk.Button(buttons, text="取消", command=self._close_replace).pack(side="left")
        temp_dir = tempfile.mkdtemp(prefix="slcn-replace-")
        self._replace = {"top": top, "editor": editor, "item": item, "db": db,
                         "status": status, "preview_btn": preview_btn,
                         "confirm_btn": confirm_btn, "temp_dir": temp_dir,
                         "signature": "", "busy": False}
        top.protocol("WM_DELETE_WINDOW", self._close_replace)
        top.grab_set()
        editor.focus_set()

    def _replace_preview(self) -> None:
        state = self._replace
        if state is None or state["busy"]:
            return
        content = state["editor"].get("1.0", "end-1c").strip()
        if not content:
            state["status"].show("warn", "BibTeX 内容为空。")
            return
        bib_path = os.path.join(state["temp_dir"], "entry.bib")
        try:
            with open(bib_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content + "\n")
        except OSError as exc:
            state["status"].show("error", f"写临时文件失败：{exc}")
            return
        item = state["item"]
        try:
            call = E.bibdb_replace_call(
                key=str(item.get("record_key") or ""), bib=bib_path,
                incoming_sha256=str(item.get("incoming_sha256") or ""),
                expect_db_sha256=self._db_sha, dry_run=True, db=state["db"])
        except (RuntimeError, ValueError) as exc:
            state["status"].show("error", str(exc))
            return
        state["busy"] = True
        state["signature"] = content
        state["preview_btn"].config(state="disabled")
        state["confirm_btn"].config(state="disabled")
        state["status"].show("warn", "正在预览（dry-run，不写库）…")
        self._replace_call(call, dry_run=True)

    def _replace_confirm(self) -> None:
        state = self._replace
        if state is None or state["busy"]:
            return
        content = state["editor"].get("1.0", "end-1c").strip()
        if not state["signature"] or content != state["signature"]:
            state["status"].show("warn", "内容在预览后改过了：请重新预览。")
            state["confirm_btn"].config(state="disabled")
            return
        bib_path = os.path.join(state["temp_dir"], "entry.bib")
        item = state["item"]
        db = state["db"]
        if not db or not os.path.isfile(db):
            state["status"].show("warn", "找不到引用库文件，无法写库。")
            return
        # S4：真写前重跑只读 git 预检（打开编辑器后仓库可能变化）。
        self._refresh_git_status()
        if self._git_blocked():
            state["status"].show("error", "⛔ 仓库有未解决的合并冲突 / rebase / merge "
                                 "进行中：先处理完 git 状态再替换。")
            return
        if not self._behind_ok():
            return
        try:
            call = E.bibdb_replace_call(
                key=str(item.get("record_key") or ""), bib=bib_path,
                incoming_sha256=str(item.get("incoming_sha256") or ""),
                expect_db_sha256=self._db_sha, dry_run=False, db=state["db"])
        except (RuntimeError, ValueError) as exc:
            state["status"].show("error", str(exc))
            return
        state["busy"] = True
        state["preview_btn"].config(state="disabled")
        state["confirm_btn"].config(state="disabled")
        state["status"].show("warn", "正在写库（replace）…")
        self._replace_call(call, dry_run=False)

    def _replace_call(self, call: E.EngineCall, *, dry_run: bool) -> None:
        state = self._replace
        if state is None:
            return
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "dry_run": dry_run}

        run_async(self.post, work, self._on_replace_result)

    def _on_replace_result(self, result: dict) -> None:
        state = self._replace
        if state is None:
            return
        state["busy"] = False
        state["preview_btn"].config(state="normal")
        payload = result.get("payload")
        code = int(result.get("code") or 0)
        dry_run = bool(result.get("dry_run"))
        lines = result.get("lines") or []
        if code != 0 or not isinstance(payload, dict):
            tail = E.output_tail(lines, count=2)
            if self._is_stale_text(tail):
                self._stale = True
                state["status"].show("warn", "库已变化（sha256 不符）：本次没有写库；请刷新。")
                self._close_replace()
                self.reload()
                return
            state["status"].show("error", ("替换预览失败：" if dry_run else "替换失败（未写库）：")
                                 + tail)
            return
        if payload.get("error"):
            state["status"].show("error", "❌ " + plain(str(payload["error"])))
            return
        if dry_run:
            if payload.get("written"):
                state["status"].show("error", "⛔ 预览竟然写了库（引擎违反 dry-run 契约）！")
                return
            fields = (payload.get("replaced_fields") or payload.get("adopted_fields")
                      or payload.get("fields") or [])
            change_text = "、".join(str(name) for name in fields) or "（引擎未列出字段变化）"
            state["status"].show(
                "warn", f"预览（dry-run，库未改）：将替换字段 {change_text}。"
                "确认无误再点「确认替换（写库）」。")
            state["confirm_btn"].config(state="normal")
            return
        if not payload.get("written"):
            state["status"].show("warn", "引擎没有写库（库未改）。")
            return
        item = state["item"]
        no = self._no(item)
        self._close_replace()
        self._running = True
        self._sync_actions()
        self.status.show("ok", f"✅ 已替换 {no}（记录 {item.get('record_key')}）")
        self.log(f"✅ 引用库替换已写库：{no} {item.get('record_key')}")
        self._start_commit({"action_text": f"裁决 {no} replace", "record_count": 1,
                            "label": f"替换 {no}", "no": no}, payload)

    def _close_replace(self) -> None:
        state = self._replace
        if state is None:
            return
        self._replace = None
        try:
            state["top"].grab_release()
        except tk.TclError:
            pass
        try:
            state["top"].destroy()
        except tk.TclError:
            pass
        shutil.rmtree(state.get("temp_dir") or "", ignore_errors=True)
        self._sync_actions()

    # ── 清单文件（只读；沿用旧页能力）──
    def reload_files(self) -> None:
        notes, directory, db = self._paths()
        self.v_notes.set(notes or "（未找到私有笔记仓）")
        self.v_dir.set(directory or "（找不到私有笔记仓：请到「引用库设置」页指定）")
        self.v_db.set(db or "（找不到私有笔记仓）")
        infos = E.bibdb_report_files(notes)
        self._files = {info["name"]: info for info in infos}
        self._refresh_file_rows()
        self.open_dir_btn.config(state="normal" if directory else "disabled")

    def _refresh_file_rows(self) -> None:
        for name, widgets in self.file_rows.items():
            info = (getattr(self, "_files", {}) or {}).get(name) or {}
            if info.get("exists"):
                widgets["state"].configure(
                    text=f"{fmt_size(info.get('bytes', 0))}；修改时间 "
                         f"{fmt_time(info.get('mtime', 0))}",
                    foreground=theme.COLOR["muted"])
                widgets["button"].config(state="normal")
            else:
                widgets["state"].configure(text="（还没生成）",
                                           foreground=theme.COLOR["warn"])
                widgets["button"].config(state="disabled")

    def regenerate(self) -> None:
        if self._running:
            return
        notes, directory, db = self._paths()
        if not notes:
            self.status.show("warn", "找不到私有笔记仓，无法确定清单输出目录。"
                                     "请到「引用库设置」页指定笔记仓路径。")
            return
        if not db or not os.path.isfile(db):
            self.status.show("warn", f"引用库文件还不存在：{db}\n先到「入藏」页导入一份 .bib，"
                                     "再来生成清单。")
            return
        try:
            call = E.bibdb_report_call(out_dir=directory or "", db=db)
        except RuntimeError as exc:
            self.status.show("error", str(exc))
            return
        self._running = True
        self.regen_btn.config(state="disabled")
        self.busy.config(text="正在生成清单…")
        self.status.clear()
        self.message.config(text="")
        self.log(f"引用库待裁决清单：重新生成（{directory}）")
        api = self.app.cfg.get("api")
        manual = self.app.cfg.get("notesRepoPath", "")

        def work() -> dict:
            started = time.monotonic()
            code, lines = collect_call(call, api=api, notes=manual)
            return {"code": code, "lines": lines, "payload": E.parse_json_lines(lines),
                    "seconds": time.monotonic() - started}

        run_async(self.post, work, self._on_report)

    def _on_report(self, result: dict) -> None:
        self._running = False
        self.regen_btn.config(state="normal")
        self.busy.config(text="")
        payload = result.get("payload")
        if int(result.get("code") or 0) != 0 or not isinstance(payload, dict):
            text = "❌ 重新生成待裁决清单失败：" + E.output_tail(result.get("lines") or [], count=2)
            self.status.show("error", text)
            self.log(text)
            self._sync_actions()
            return
        conflicts = payload.get("conflicts") or {}
        reports = payload.get("reports") or []
        written = sum(1 for item in reports if isinstance(item, dict) and item.get("written"))
        try:
            conflict_records = int(conflicts.get("records") or 0)
            conflict_total = int(conflicts.get("total") or 0)
        except (TypeError, ValueError):
            conflict_records = conflict_total = 0
        text = (f"✅ 已重新生成清单：写入/更新 {written} 个文件；"
                f"字段冲突 {conflict_records} 条记录 / {conflict_total} 处差异；"
                f"同作品候选对 {int(payload.get('pairs') or 0)} 对。")
        self.message.config(text=text, foreground=theme.COLOR["ok"])
        self.reload_files()
        self.log(text)
        self._sync_actions()

    def open_named(self, name: str) -> None:
        info = (getattr(self, "_files", {}) or {}).get(name) or {}
        if info.get("exists"):
            self._open_path(str(info.get("path") or ""), select=True)

    def open_primary(self) -> None:
        for name in E.BIBDB_REPORT_FILES:
            info = (getattr(self, "_files", {}) or {}).get(name) or {}
            if info.get("exists"):
                self._open_path(str(info.get("path") or ""), select=True)
                return

    def open_folder(self) -> None:
        _notes, directory, _db = self._paths()
        if directory:
            self._open_path(directory)


# ── ⑤ 引用库设置 ────────────────────────────────────────────────────────

class BibDbSettingsPage(BibDbPage):
    name = "引用库设置"        # 侧栏里显示的名字（组 → 页的第二层）
    """引用库设置：核实成本闸门 + 查库开关 + 笔记仓路径（第 132 条）。

    为什么单独一页（而不是塞进「设置」）：它们不是 AI 连接参数 —— 核实上限/先算账/
    全量核实/查不查库回答的是「这次核实的成本与范围」；笔记仓路径回答「库在哪」。
    引用库是与「单篇稿件 / 整期装配」平行的顶级分组，它的设置也放在这个分组里。

    生效链路（只有一条）：本页写 `settings.json` → 「初排转换」在点击运行时**现取**
    （`ConvertTab.extra_args` 调 `settings.bibdb_convert_args`）→ `engine.convert_call`
    拼进 argv → 引擎 `convert.py`。改完下一次转换立即生效，不必重启。
    """

    page_title = "引用库 · 设置"
    page_subtitle = """核实成本闸门（上限 / 先算账 / 全量核实）、「核实前先查引用库」开关，
以及引用库（私有笔记仓）路径。这些值存在本机 settings.json，由「初排转换」页在运行时
现取并拼进引擎 argv：改完下一次转换立即生效，不必重启。
⚠ 它们只在（a）两段式协同第一段（文献核实任务书）或（b）勾选
「用 AI 处理参考文献（直连 API，会产生费用）」时才会被传给引擎 ——
本页设置不会自己发起核实。默认值刻意是「一个参数都不加」：没改过就是旧行为。"""

    def build(self, parent: ttk.Frame) -> None:
        self.bibdb = self.app.cfg.setdefault("bibdb", {})

        # ① 核实成本闸门
        ttk.Label(parent, text="① 核实成本闸门", style="Section.TLabel").pack(anchor="w",
                                                                               pady=(4, 2))
        ttk.Label(parent, text=plain(
            "第 128 条起默认只核实正文引用到的条目，并对条数设上限：超过上限且没开"
            "「允许全量核实」时，引擎会先退出 2 并打印范围与预计检索/AI 次数。"
            "看到退出 2 不是程序错误，是让你先看账再决定。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(fill="x")
        max_row = ttk.Frame(parent)
        max_row.pack(fill="x", pady=(6, 2))
        ttk.Label(max_row, text="核实上限", width=12, anchor="e").pack(side="left", padx=(0, 8))
        self.max_var = tk.StringVar(value=S.verify_max_text(self.app.cfg))
        self.max_entry = ttk.Entry(max_row, textvariable=self.max_var, width=10)
        self.max_entry.pack(side="left")
        ttk.Label(max_row, text=plain(f"条（0 = 不限；留空 = 引擎默认 {S.DEFAULT_VERIFY_MAX} 条，"
                                      "本次不传 --verify-max）"),
                  style="Muted.TLabel").pack(side="left", padx=8)
        self.max_hint = ttk.Label(parent, text="", foreground=theme.COLOR["error"],
                                  wraplength=800, justify="left")
        self.max_hint.pack(fill="x")
        self.max_var.trace_add("write", lambda *_: self._check_max())

        self.v_plan = tk.BooleanVar(value=bool(self.bibdb.get("verifyPlan")))
        _check(parent, "核实前先算账（--verify-plan：只算账，不花钱）", self.v_plan).pack(
            anchor="w", pady=(4, 0))
        ttk.Label(parent, text=plain(
            "开了它，转换照常写出产物，但不生成任务书、不检索、不调用 AI，只打印"
            "「本次会核实多少条、预计检索/AI 多少次、约多少钱」后退出 0。"
            "看完成本账关掉它再跑一次才会真正核实。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(
            fill="x", padx=(20, 0))

        self.v_all = tk.BooleanVar(value=bool(self.bibdb.get("verifyAllRefs")))
        _check(parent, "允许全量核实（--verify-all-refs：越过上限闸门）", self.v_all).pack(
            anchor="w", pady=(6, 0))
        ttk.Label(parent, text=plain(
            "越过上限闸门（正文 \\nocite{*} 本身也要求全量）。默认不开：全量核实会为"
            "没被正文引用的条目也花检索/AI 的钱（期 87 实测：376 条 $1.8+）。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(fill="x", padx=(20, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)

        # ② 先查引用库
        ttk.Label(parent, text="② 核实前先查引用库（省钱 + 体例统一）",
                  style="Section.TLabel").pack(anchor="w", pady=(2, 2))
        self.v_lookup = tk.BooleanVar(value=self.bibdb.get("lookupEnabled") is not False)
        _check(parent, "核实前先查引用库（默认开）", self.v_lookup).pack(anchor="w")
        ttk.Label(parent, text=plain(
            "开着：执行核实时若引用库存在，先按 DOI / 题名 / key 查库 —— 命中已核实条目的"
            "键不调检索 API、不调 AI（仍逐条写进任务书「五、库内已核实」，字段差异只提示、"
            "不改 reference.bib）。关掉 = 本次一律不查库（传 --no-bibdb，输出与接入引用库"
            "之前逐字节相同）。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(fill="x", padx=(20, 0))
        lookup_row = ttk.Frame(parent)
        lookup_row.pack(fill="x", pady=(4, 0))
        ttk.Label(lookup_row, text="库文件", style="Muted.TLabel", width=12,
                  anchor="e").pack(side="left", padx=(0, 8))
        self.v_db = tk.StringVar(value="—")
        ttk.Label(lookup_row, textvariable=self.v_db, wraplength=620, justify="left").pack(
            side="left")
        self.open_db_btn = ttk.Button(lookup_row, text="打开库文件夹", width=14,
                                      command=self.open_library_folder)
        self.open_db_btn.pack(side="right")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)

        # ③ 引用库路径（笔记仓）
        ttk.Label(parent, text="③ 引用库路径（私有笔记仓）", style="Section.TLabel").pack(
            anchor="w", pady=(2, 2))
        detect_row = ttk.Frame(parent)
        detect_row.pack(fill="x", pady=1)
        ttk.Label(detect_row, text="自动探测", style="Muted.TLabel", width=12,
                  anchor="e").pack(side="left", padx=(0, 8))
        self.v_detected = tk.StringVar(value="—")
        ttk.Label(detect_row, textvariable=self.v_detected, wraplength=680,
                  justify="left").pack(side="left")

        manual_row = ttk.Frame(parent)
        manual_row.pack(fill="x", pady=(4, 1))
        ttk.Label(manual_row, text="手动指定", style="Muted.TLabel", width=12,
                  anchor="e").pack(side="left", padx=(0, 8))
        self.notes_var = tk.StringVar(value=self.app.cfg.get("notesRepoPath", ""))
        ttk.Entry(manual_row, textvariable=self.notes_var).pack(side="left", fill="x",
                                                                expand=True)
        ttk.Button(manual_row, text="浏览…", width=8, command=self.browse_notes).pack(
            side="left", padx=4)
        self.reset_notes_btn = ttk.Button(manual_row, text="恢复自动探测", width=14,
                                          command=self.clear_notes)
        self.reset_notes_btn.pack(side="left")
        self.notes_var.trace_add("write", lambda *_: self._refresh_notes_status())
        self.notes_status = StatusLine(parent)
        self.notes_status.pack(fill="x", padx=(100, 0))
        ttk.Label(parent, text=plain(
            "手动路径存 settings.json（键 notesRepoPath）。外壳把它作为环境变量 SLCN_NOTES "
            "传给所有引擎子进程（引用库、核实链路都认这一个变量）；手动路径无效时**回落"
            "自动探测**（不会写到别处、也不会静默新建目录）。笔记仓目录里需要有 cases/ 或 "
            "bib_db/ —— 引擎要求该路径真实存在。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(fill="x", padx=(20, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)

        # ④ 当前生效值
        ttk.Label(parent, text="④ 当前生效值（下一次「初排转换」会用这些）",
                  style="Section.TLabel").pack(anchor="w", pady=(2, 2))
        self.effective = InfoRows(parent)
        self.effective.pack(fill="x")
        self.v_eff_max = self.effective.add("核实上限")
        self.v_eff_plan = self.effective.add("先算账")
        self.v_eff_all = self.effective.add("全量核实")
        self.v_eff_lookup = self.effective.add("先查引用库")
        self.v_eff_db = self.effective.add("库文件")
        ttk.Label(parent, text=plain(
            "补充：这些值只在「初排转换」勾了核实（任务书）时进 argv；「先算账」开着时"
            "引擎只打印核实计划就退出 0，本次不会真正核实。"),
            style="Muted.TLabel", wraplength=800, justify="left").pack(fill="x")

        save_row = ttk.Frame(parent)
        save_row.pack(fill="x", pady=(10, 2))
        ttk.Button(save_row, text="保存引用库设置", width=18, style="Accent.TButton",
                   command=self.save).pack(side="left")
        self.receipt = ttk.Label(save_row, text="", style="Muted.TLabel", wraplength=600,
                                 justify="left")
        self.receipt.pack(side="left", padx=10)

        self._check_max()
        self._refresh_auto_detect()
        self._refresh_notes_status()
        self.refresh_effective()

    # ── 校验 / 刷新 ──
    def _check_max(self) -> bool:
        _value, error = S.parse_verify_max(self.max_var.get())
        if error:
            self.max_hint.config(text="⚠ " + error)
            return False
        self.max_hint.config(text="")
        return True

    def _refresh_auto_detect(self) -> None:
        detected = E.detect_notes_repo()
        self.v_detected.set(detected or "（未探测到：笔记仓与工具仓并排时才能自动找到）")

    def _refresh_notes_status(self) -> None:
        text = self.notes_var.get().strip()
        if not text:
            self.notes_status.show("idle", "未手动指定：用自动探测到的笔记仓。")
        elif E.manual_notes_valid(text):
            self.notes_status.show("ok", "手动路径有效（它的 bib_db/ 就是引用库位置）。")
        else:
            self.notes_status.show(
                "error", "手动路径不存在或不像笔记仓：引擎会回落自动探测"
                         "（不会写到别处、也不会静默新建目录）。")
        self.refresh_effective()

    def refresh_effective(self) -> None:
        cfg = self.app.cfg
        # 用**界面上的**值预览（保存前也能看到「下一次会用什么」），但上限用已保存值：
        # 输入到一半的非法文本不该参与「当前生效值」。
        self.v_eff_max.set(S.verify_max_summary(cfg))
        self.v_eff_plan.set("开（本次只算账，不生成任务书 / 不检索 / 不调 AI）"
                            if self.v_plan.get() else "关（照常核实）")
        self.v_eff_all.set("开（越过上限闸门）" if self.v_all.get() else "关（受上限约束）")
        lookup_on = bool(self.v_lookup.get())
        notes = E.effective_notes_repo(self.notes_var.get())
        db = E.bibdb_db_path(notes) if notes else None
        exists = bool(db) and os.path.isfile(db)
        if lookup_on:
            text = ("开（但库文件不存在：引擎会优雅跳过查库）" if not exists
                    else f"开（库：{db}）")
        else:
            text = "关（本次传 --no-bibdb）"
        self.v_eff_lookup.set(text)
        self.v_eff_db.set(db or "（不存在或找不到笔记仓）")
        self.v_db.set(db or "（找不到私有笔记仓，见下面 ③）")
        self.open_db_btn.config(state="normal" if db else "disabled")

    # ── 动作 ──
    def browse_notes(self) -> None:
        path = fd.askdirectory(title="选择私有笔记仓（EditToolBox-notes）的根目录；"
                                     "它的 bib_db/ 就是引用库位置")
        if path:
            self.notes_var.set(path)
            self.log(f"引用库路径（手动指定）：{path}")

    def clear_notes(self) -> None:
        self.notes_var.set("")
        self.log("引用库路径：恢复自动探测")

    def open_library_folder(self) -> None:
        notes = E.effective_notes_repo(self.notes_var.get())
        directory = E.notes_dir(notes)
        if directory:
            self._open_path(directory)

    def save(self) -> None:
        if not self._check_max():
            self.receipt.config(text="未保存：核实上限输入有误（修好再保存）。")
            return
        value, _error = S.parse_verify_max(self.max_var.get())
        bibdb = self.app.cfg.setdefault("bibdb", {})
        bibdb["verifyMax"] = value
        bibdb["verifyPlan"] = bool(self.v_plan.get())
        bibdb["verifyAllRefs"] = bool(self.v_all.get())
        bibdb["lookupEnabled"] = bool(self.v_lookup.get())
        manual = self.notes_var.get().strip().strip('"')
        self.app.cfg["notesRepoPath"] = (os.path.abspath(os.path.expanduser(manual))
                                         if manual else "")
        try:
            path = S.save(self.app.cfg)
        except Exception as exc:  # noqa: BLE001  保存失败要让人看见（不是在日志里静默）
            self.receipt.config(text=f"❌ 保存失败：{exc}")
            return
        if manual and not E.manual_notes_valid(manual):
            self.receipt.config(
                text=f"⚠ 已保存，但手动路径无效：引擎会回落自动探测（{time.strftime('%H:%M:%S')}）")
        else:
            self.receipt.config(
                text=f"✅ 已保存 · {time.strftime('%H:%M:%S')}（仅存在本机：{path}）")
        self._refresh_auto_detect()
        self._refresh_notes_status()
        self.refresh_effective()
        # 「初排转换」页有一行只读回显：保存完立刻刷新，免得编辑切过去还看到旧范围。
        refresh_hint = getattr(getattr(self.app, "convert_tab", None),
                               "refresh_bibdb_hint", None)
        if callable(refresh_hint):
            refresh_hint()
        self.log(f"✅ 已保存引用库设置（{time.strftime('%H:%M:%S')}，仅存在本机：{path}）")
        self.log("  下一次「初排转换」生效：" + S.verify_max_summary(self.app.cfg))

    def on_activate(self) -> None:
        self._refresh_auto_detect()
        self._refresh_notes_status()


def _check(parent: tk.Widget, text: str, var: tk.BooleanVar) -> ttk.Checkbutton:
    """勾选框统一走 ttk（clam 才管得到它的底色/字体）；主文件里同名私有函数的副本。"""
    return ttk.Checkbutton(parent, text=plain(text), variable=var)
