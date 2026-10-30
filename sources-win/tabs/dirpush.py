"""「目录摘要推送」页（`DirPushTab`，B-289）：选当期文件夹 → 引擎预览 → 生成 docx。

**判据全在引擎**：期头/专栏/目录条目/摘要清洗、年度扫描/缺期/体例还原、输出路径与
同名覆盖都在 `Engine/slcn/pushcopy/` + `Engine/dirpush.py`。这一层只做三件事
（与 mac `DirPushFeature` 同一套分工）：

  ① 把界面状态交给 `engine.dirpush_call()` 拼 argv —— 参数拼装是**纯函数**，
     bridge (o) 组直接断言（与 mac `DirPushFeature.arguments` 逐项对齐）；
  ② 解析引擎 `--json` 的**冻结契约字段**（GUI 只读这些，不自己算期头/篇数/路径）；
  ③ 预览自己起一次子进程（`--dry-run`，不写盘）；生成走统一「运行」按钮
     （`main._call_for` → `engine.dirpush_call`，日志/停止/结果区都是现成的）。

与 mac 的**形态差异**（任务书 §1b 口径）：mac 勾「同时生成年度汇总」会顺序跑两趟；
Windows 按 CLI 契约**一次只跑一个模式** —— 模式单选决定跑当期还是年度。
"""

from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox, ttk

import engine as E
import settings as S
import theme
from .base import APP_TITLE, TabPage, _check, open_path, plain

#: 默认输出根（spec §2.5）：`~/Documents/sysu/编辑部/目录摘要推送`。
#: Windows 上 `expanduser("~")` = `%USERPROFILE%`（与引擎 `collect.py` 的默认根同一个）。
DEFAULT_OUT_ROOT_TAIL = os.path.join("Documents", "sysu", "编辑部", "目录摘要推送")

#: 记住「上次输出根」的配置键（`settings.DEFAULTS["options"]`；**不要改名**，
#: 改了老用户记住的路径就丢）。
OUT_ROOT_KEY = "dirpushOutRoot"

#: 输出根落在这些目录里 → 生成前二次确认（产物会变成未跟踪文件，污染工作树）。
#: 比 mac 的「`<home>/Documents/GitHub` 前缀」更保守：只认本项目三个仓的名字。
REPO_MARKERS = ("EditToolBoxStudiesInLogic", "EditToolBox-notes", "Studies_in_Logic_Database")

#: 引擎失败时从合并输出里认「这是原因行」的标记（与 mac `DirPushFeature.errorMessage` 同表）。
ERROR_MARKERS = ("错误", "ERROR", "Error:", "error:", "Traceback", "usage:",
                 "无法", "缺少", "不存在", "不是", "冲突")

MODE_ISSUE = "issue"
MODE_ANNUAL = "annual"


# ── 纯函数（无 tkinter；bridge (o) 组直接断言）─────────────────────────────

def default_out_root() -> str:
    """引擎默认输出根（`~` 展开成当前用户主目录；Windows 上就是 `%USERPROFILE%`）。"""
    return os.path.expanduser(os.path.join("~", DEFAULT_OUT_ROOT_TAIL))


def resolve_out_root(cfg) -> str:
    """配置里的输出根；空/缺 → 默认根（**只读**，不写盘）。"""
    options = (cfg or {}).get("options")
    raw = options.get(OUT_ROOT_KEY, "") if isinstance(options, dict) else ""
    text = str(raw or "").strip()
    return text or default_out_root()


def repo_warning(out_root: str) -> str:
    """输出根落在代码仓里 → 生成前确认框的文案；不在 → 空串。"""
    text = str(out_root or "").strip()
    if not text:
        return ""
    normalized = os.path.normpath(os.path.expanduser(text))
    probe = normalized.lower() if os.name == "nt" else normalized
    for marker in REPO_MARKERS:
        if (marker.lower() if os.name == "nt" else marker) in probe:
            return (f"输出根在代码仓内：\n{normalized}\n\n"
                    "生成会在仓库里留下未跟踪的 docx 文件（污染工作树）。仍要继续？")
    return ""


def abstracts_rate(payload) -> str:
    """摘要取到率：`abstracts_ok/articles`；0 篇不除零 → `0/0`（与 mac 同一文案）。"""
    counts = (payload or {}).get("counts") or {}
    try:
        ok = int(counts.get("abstracts_ok") or 0)
        total = int(counts.get("articles") or 0)
    except (TypeError, ValueError):
        return "0/0"
    return f"{ok}/{total}"


def parse_result(lines) -> dict | None:
    """从混合输出里抠出引擎那个 JSON 对象（多行缩进也认；解析不了 → None）。

    `run_call` 把 stderr 并进了 stdout，所以不能整段 `json.loads`：先前先用
    `engine.parse_json_lines()`（JSON 在末尾的常见情形），失败再从最后一个行首 `{`
    起**逐行截断**重试 —— stderr 可能刷在 JSON 之后（例如引擎写完 JSON 才报 traceback）。
    """
    rows = [str(line) for line in (lines or [])]
    try:
        payload = E.parse_json_lines(rows)
    except Exception:  # noqa: BLE001  结果解析失败不许把界面弄断
        payload = None
    if isinstance(payload, dict):
        return payload
    for start in range(len(rows) - 1, -1, -1):
        if not rows[start].lstrip().startswith("{"):
            continue
        for end in range(len(rows), start, -1):
            try:
                candidate = json.loads("\n".join(rows[start:end]))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(candidate, dict):
                return candidate
    return None


def error_message(lines, code: int) -> str:
    """引擎失败时给编辑看的一句话：含错误关键词的最后一行优先，否则最后一条日志。"""
    rows = [str(line).strip() for line in (lines or [])]
    rows = [row for row in rows if row and not row.startswith(("{", "[", "}"))]
    for row in reversed(rows):
        if any(marker in row for marker in ERROR_MARKERS):
            return row
    if rows:
        return rows[-1]
    return f"引擎退出码 {code}"


def summary_text(payload, code: int, *, dry_run: bool = False, tail: str = "") -> str:
    """结果摘要区（期头/篇数/摘要取到率/warnings/输出）；只读引擎字段，不自己算。"""
    if not payload:
        head = ("❌ 引擎没有返回结果 JSON（见运行日志）" if code != 0
                else "⚠️ 引擎没有返回结果 JSON")
        return head + (f"\n{tail}" if tail else "")
    ok = bool(payload.get("ok"))
    lines: list[str] = []
    if not ok:
        lines.append(f"❌ 引擎报告失败（退出码 {code}）")
    elif dry_run:
        lines.append("🔍 预览（dry-run：未写任何文件）")
    issue = payload.get("issue") or {}
    if isinstance(issue, dict) and issue:
        lines.append(f"期次：{issue.get('year')} 年第 {issue.get('number')} 期"
                     f"（第 {issue.get('vol')} 卷，总第 {issue.get('total')} 期）")
        for header in issue.get("header_lines") or []:
            lines.append(f"  {header}")
        lines.append("体例：" + ("中英文对照" if issue.get("with_en_toc") else "仅中文"))
    annual = payload.get("annual") or {}
    if isinstance(annual, dict) and annual:
        included = [str(n) for n in (annual.get("included") or [])]
        lines.append(f"年度：{annual.get('year')} 年，纳入 {len(included)} 期"
                     + (f"（{','.join(included)}）" if included else ""))
        missing = [str(n) for n in (annual.get("missing_issues") or [])]
        if missing:
            lines.append("  缺期：" + "、".join(f"第 {n} 期" for n in missing)
                         + "（只警告；生成时跳过）")
    counts = payload.get("counts") or {}
    if isinstance(counts, dict) and counts:
        lines.append(f"目录条目：中文 {counts.get('zh_entries', 0)} / 英文 "
                     f"{counts.get('en_entries', 0)}；文章 {counts.get('articles', 0)}"
                     f"（摘要取到 {abstracts_rate(payload)}）")
    for warning in payload.get("warnings") or []:
        lines.append(f"⚠ {warning}")
    for path in payload.get("outputs") or []:
        lines.append(("计划输出：" if dry_run else "输出：") + str(path))
    return "\n".join(lines)


# ── 页面 ─────────────────────────────────────────────────────────────────

class DirPushTab(TabPage):
    name = "目录摘要推送"
    pick = "dir"
    instruction = (
        "使用说明：选**本期期目录**（如「2026年第4期(89)」），确认输出根，先点「预览（不写盘）」核对"
        "期头/篇数/摘要取到率，再点「运行」生成 docx。\n"
        "产物：当期 → `<输出根>/<期目录名>/逻辑学研究YYYY年第N期目录摘要.docx`（另写一份"
        "《目录摘要设置.json》记体例）；年度 → `<输出根>/<年>/逻辑学研究YYYY年总目录.docx`。"
        "同名覆盖、不留备份、不产 PDF。\n"
        "「附英文目录」只作用于**当期**；年度按期还原各期体例（该期生成时记下的设置）。\n"
        "年度模式要填「年份」（可先预览自动带入），「纳入期」留空 = 该年全部找到的期；"
        "缺期只警告、不可勾选。\n"
        "生成成功后这里会记住输出根；输出根落在代码仓内会先弹确认（会留下未跟踪文件）。"
    )

    def __init__(self, parent, name, on_run, on_stop, cfg=None):
        # 与「初排转换」页同一写法：cfg 与设置页共用同一份 dict，保存时整份写回。
        self.cfg = cfg or {}
        self.out_root = tk.StringVar(value=resolve_out_root(self.cfg))
        self.mode = tk.StringVar(value=MODE_ISSUE)
        self.year = tk.StringVar(value="")
        self.include_issues = tk.StringVar(value="")
        self.with_en_toc = tk.BooleanVar(value=False)
        self.outputs: list[str] = []          # 只认引擎 `outputs[]`（界面不拼路径）
        self.last_payload: dict | None = None
        super().__init__(parent, name, on_run, on_stop)

    # ── 界面 ──
    def build_options(self, parent: ttk.Frame) -> None:
        row_out = ttk.Frame(parent)
        row_out.pack(side="top", fill="x")
        ttk.Label(row_out, text="输出根").pack(side="left")
        ttk.Entry(row_out, textvariable=self.out_root, width=52).pack(side="left", padx=(4, 4))
        ttk.Button(row_out, text="浏览…", width=8,
                   command=self.choose_out_root).pack(side="left")

        row_mode = ttk.Frame(parent)
        row_mode.pack(side="top", fill="x", pady=(4, 0))
        ttk.Label(row_mode, text="模式").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(row_mode, text="当期目录摘要", value=MODE_ISSUE,
                        variable=self.mode, command=self._sync_mode).pack(side="left")
        ttk.Radiobutton(row_mode, text="年度总目录", value=MODE_ANNUAL,
                        variable=self.mode, command=self._sync_mode).pack(side="left", padx=(8, 0))
        self.toc_check = _check(row_mode, "附英文目录（仅当期）", self.with_en_toc)
        self.toc_check.pack(side="left", padx=(16, 0))

        self.annual_row = ttk.Frame(parent)
        self.annual_row.pack(side="top", fill="x", pady=(4, 0))
        ttk.Label(self.annual_row, text="年份").pack(side="left")
        ttk.Entry(self.annual_row, textvariable=self.year, width=8).pack(side="left", padx=(4, 10))
        ttk.Label(self.annual_row, text="纳入期（可选，如 1,2,3；留空 = 全年）").pack(side="left")
        ttk.Entry(self.annual_row, textvariable=self.include_issues, width=18).pack(
            side="left", padx=(4, 0))

        row_btn = ttk.Frame(parent)
        row_btn.pack(side="top", fill="x", pady=(6, 0))
        self.preview_btn = ttk.Button(row_btn, text="预览（不写盘）", width=14,
                                      command=self.preview)
        self.preview_btn.pack(side="left")
        self.open_file_btn = ttk.Button(row_btn, text="打开文件", width=10,
                                        command=self.open_output_file, state="disabled")
        self.open_file_btn.pack(side="left", padx=(8, 0))
        self.open_dir_btn = ttk.Button(row_btn, text="打开输出目录", width=13,
                                       command=self.open_out_dir)
        self.open_dir_btn.pack(side="left", padx=(8, 0))
        ttk.Label(row_btn, text="默认：%s" % default_out_root(),
                  style="Muted.TLabel").pack(side="left", padx=(12, 0))

        # 结果摘要区（期头/篇数/摘要取到率/warnings/输出）：颜色只表状态，判据在引擎。
        self.summary = ttk.Label(parent, text="", justify="left", wraplength=820)
        self.summary.pack(side="top", fill="x", pady=(6, 0))
        self._sync_mode()

    def _sync_mode(self) -> None:
        """年度行只在年度模式可用；「附英文目录」只作用于当期（年度同期还原体例）。"""
        annual = self.mode_value() == MODE_ANNUAL
        if annual:
            self.annual_row.pack(side="top", fill="x", pady=(4, 0))
        else:
            self.annual_row.pack_forget()
        state = {"state": "disabled"} if annual else {"state": "normal"}
        try:
            self.toc_check.configure(**state)
        except tk.TclError:       # 主题不支持 disabled 外观时也不许崩
            pass

    def show_summary(self, text: str, *, level: str = "") -> None:
        color = {"error": theme.COLOR["error"], "warn": theme.COLOR["warn"],
                 "ok": theme.COLOR["ok"]}.get(level, theme.COLOR["fg"])
        self.summary.config(text=plain(text), foreground=color)

    # ── 取值（main._call_for 用；纯读控件）──
    def mode_value(self) -> str:
        return MODE_ANNUAL if str(self.mode.get()) == MODE_ANNUAL else MODE_ISSUE

    def out_root_value(self) -> str:
        return str(self.out_root.get()).strip()

    def year_value(self) -> str:
        return str(self.year.get()).strip()

    def include_value(self) -> str:
        return str(self.include_issues.get()).strip()

    def with_en_toc_value(self) -> bool:
        # 年度模式一律不带（引擎契约：`--with-en-toc` 仅当期）。
        return bool(self.with_en_toc.get()) and self.mode_value() == MODE_ISSUE

    def clear_result(self) -> None:
        self.outputs = []
        self.last_payload = None
        self.open_file_btn.config(state="disabled")
        self.show_summary("")

    def begin_run(self) -> None:
        """每次运行开始：清掉上一次的产物按钮（别让人点到过期路径）。"""
        self.outputs = []
        self.last_payload = None
        self.open_file_btn.config(state="disabled")

    def apply_result(self, payload, code: int, *, tail: str = "") -> None:
        """引擎跑完（`main._worker` 排队回主线程）：显示摘要 + 备好「打开文件」。"""
        self.last_payload = payload if isinstance(payload, dict) else None
        self.outputs = [str(p) for p in (self.last_payload or {}).get("outputs") or []]
        text = summary_text(self.last_payload, code, tail=tail)
        if not self.last_payload or code != 0 or not (self.last_payload or {}).get("ok"):
            level = "error"
        elif (self.last_payload or {}).get("warnings"):
            level = "warn"
        else:
            level = "ok"
        self.show_summary(text, level=level)
        self.open_file_btn.config(state="normal" if self.outputs else "disabled")

    # ── 按钮 ──
    def choose_out_root(self) -> None:
        chosen = fd.askdirectory(title="选择输出根目录（docx 会写到这里）")
        if chosen:
            self.out_root.set(os.path.normpath(chosen))

    def preview(self) -> None:
        """预览 = `--dry-run --json`：只显示期头/篇数/计划路径，**不写任何文件**。"""
        issue = self.path()
        if not os.path.isdir(issue):
            messagebox.showwarning(APP_TITLE, "请先选择有效的当期文件夹（期目录）。")
            return
        out_root = self.out_root_value()
        if not out_root:
            messagebox.showwarning(APP_TITLE, "输出根不能为空。")
            return
        try:
            call = E.dirpush_call(issue=issue, out_root=out_root,
                                  with_en_toc=self.with_en_toc_value(),
                                  annual=self.mode_value() == MODE_ANNUAL,
                                  year=self.year_value(),
                                  include_issues=self.include_value(),
                                  dry_run=True)
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.show_summary("⏳ 正在向引擎要预览（dry-run，不写盘）…")
        self.update_idletasks()
        try:
            proc = subprocess.run(call.argv, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  env=E.env_for_run(None), timeout=180,
                                  # Windows：与 engine.run_call 同一口径，别弹黑框
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as e:
            self.show_summary(f"❌ 预览启动失败：{e}", level="error")
            return
        lines = (proc.stdout or "").splitlines()
        payload = parse_result(lines)
        if payload is None:
            reason = error_message(lines, proc.returncode)
            self.show_summary(f"❌ 预览失败（退出码 {proc.returncode}）：{reason}", level="error")
            return
        self.last_payload = payload
        text = summary_text(payload, proc.returncode, dry_run=True)
        warn = repo_warning(out_root)
        if warn:
            text += "\n⚠ " + warn.splitlines()[0]
        self.show_summary(text, level="warn" if not payload.get("ok") or warn else "")
        # 年度模式：预览带回年份 → 自动填（可改；mac 同一行为）。
        issue_payload = payload.get("issue") or {}
        if (self.mode_value() == MODE_ANNUAL and not self.year_value()
                and issue_payload.get("year")):
            self.year.set(str(issue_payload["year"]))

    def confirm_repo_root(self) -> bool:
        """生成前的代码仓内二次确认（False = 用户取消，主流程抛 ValueError 不跑）。"""
        warning = repo_warning(self.out_root_value())
        if not warning:
            return True
        return bool(messagebox.askyesno(APP_TITLE, warning))

    def remember_out_root(self) -> None:
        """生成成功后记住输出根（写进与设置页共用的 cfg；失败只提示不改流程）。"""
        text = self.out_root_value()
        if not text:
            return
        try:
            options = self.cfg.setdefault("options", {})
            if options.get(OUT_ROOT_KEY) == text:
                return
            options[OUT_ROOT_KEY] = text
            S.save(self.cfg)
        except Exception as e:  # noqa: BLE001  配置写不进去不影响产物
            self.show_summary(f"⚠ 输出根没记住（配置写入失败）：{e}", level="warn")

    def open_output_file(self) -> None:
        if not self.outputs:
            messagebox.showinfo(APP_TITLE, "还没有产物：先点「运行」生成 docx。")
            return
        target = self.outputs[0]
        if not os.path.exists(target):
            self.show_summary(f"⚠ 生成结果路径不存在：{target}", level="warn")
            return
        open_path(target)

    def open_out_dir(self) -> None:
        """打开输出目录：优先引擎给的第一份产物的所在目录，否则输出根本身。"""
        target = ""
        if self.outputs:
            target = os.path.dirname(os.path.abspath(self.outputs[0]))
        if not target or not os.path.isdir(target):
            target = self.out_root_value()
        if not os.path.isdir(target):
            messagebox.showinfo(APP_TITLE, "输出目录还不存在：先点「运行」生成。")
            return
        open_path(target)
