"""目录摘要推送（当期 / 年度）：只拼参数、读引擎 JSON、做代码仓内确认。

生成逻辑全在 `Engine/dirpush.py`；本模块不解析目录、不复制推送排版。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C

MODE_ISSUE = "issue"
MODE_ANNUAL = "annual"


def build_call(*, issue: str, out_root: str = "", annual: bool = False,
               year: str = "", include_issues: str = "",
               with_en_toc: bool = False, dry_run: bool = False) -> EB.EngineCall:
    return EB.dirpush_call(
        issue=issue, out_root=out_root, with_en_toc=with_en_toc, annual=annual,
        year=year, include_issues=include_issues, dry_run=dry_run,
    )


def repo_warning(out_root: str, repo=None) -> str:
    """输出根落在代码仓内时返回中文警告；空输出根 / 仓外返回空串。"""
    text = str(out_root or "").strip()
    if not text:
        return ""
    try:
        root = Path(repo).resolve() if repo else EB.repo_root().resolve()
        target = Path(text).expanduser().resolve()
    except (OSError, RuntimeError):
        return ""
    try:
        if target == root or root in target.parents:
            return (f"输出根在代码仓内：{target}。生成会在仓库里留下未跟踪的 docx 文件，"
                    "污染工作树。")
    except OSError:
        return ""
    return ""


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(str(path or "").strip())).resolve()


def validate_out_root(out_root: str, issue: str) -> tuple[str, str]:
    """输出根校验（按 spec 顺序）；error 阻断，warn 只提示（仓内需二次确认）。"""
    text = str(out_root or "").strip()
    if not text:
        return "ok", ""
    if not (text.startswith("~/") or os.path.isabs(text)):
        return "error", "请用绝对路径，或以 ~/ 开头的路径"
    target = _resolve(text)
    if target.exists() and target.is_file():
        return "error", "这个路径是文件，请选目录"
    try:
        issue_path = _resolve(issue)
        if target == issue_path or issue_path in target.parents:
            return "error", "输出根不能放在期目录内（作者来稿只读）"
    except OSError:
        pass
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if probe.exists() and not probe.is_dir():
        return "error", "这个路径是文件，请选目录"
    if probe.exists() and not os.access(probe, os.W_OK):
        return "error", "目录不可写，请换一个位置"
    warn = repo_warning(text)
    if warn:
        return "warn", warn
    return "ok", ""


def year_error(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}", text) or not (2000 <= int(text) <= 2100):
        return "年度请填 4 位数字，如 2026"
    return ""


def normalize_issues(text: str) -> tuple[list[int], list[str]]:
    """年度「只含这些期号」：接受 , ， 、 空格；非法片段原样列出，不静默丢弃。"""
    raw = str(text or "").strip()
    if not raw:
        return [], []
    good: list[int] = []
    bad: list[str] = []
    for part in re.split(r"[,，、\s]+", raw):
        if not part:
            continue
        if part.isdigit() and int(part) > 0:
            good.append(int(part))
        else:
            bad.append(part)
    return good, bad


def issues_caption(text: str) -> str:
    good, bad = normalize_issues(text)
    if bad:
        return "只填期号，用逗号分隔，如 1,2,3"
    if not good:
        return "留空 = 纳入该年度全部期"
    return "将纳入第 " + "、".join(str(n) for n in good) + " 期"


def _mode(page) -> str:
    value = str(page.app.store.get("form.dirpush_mode", MODE_ISSUE) or MODE_ISSUE)
    return value if value in (MODE_ISSUE, MODE_ANNUAL) else MODE_ISSUE


def validation_error(page) -> str:
    """运行前校验：返回一条中文错误；空串表示可运行。"""
    issue = str(page.field("issue_dir") or "").strip()
    if not issue:
        return "先选择期目录"
    level, message = validate_out_root(page.field("dirpush_out"), issue)
    if level == "error":
        return message
    if _mode(page) == MODE_ANNUAL:
        err = year_error(page.app.store.get("form.dirpush_year", ""))
        if err:
            return err
        _good, bad = normalize_issues(page.app.store.get("form.dirpush_issues", ""))
        if bad:
            return "只填期号，用逗号分隔，如 1,2,3"
    return ""


def output_files(payload: dict) -> list[dict]:
    files: list[dict] = []
    for raw in (payload or {}).get("outputs") or []:
        path = str(raw or "")
        if not path:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        files.append({"path": path, "name": os.path.basename(path), "size": size})
    return files


def size_text(size: int) -> str:
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def summary_lines(payload: dict) -> list[str]:
    """把引擎 JSON 转成给人看的中文摘要（不含任何排版判断）。"""
    payload = payload or {}
    lines: list[str] = []
    issue = payload.get("issue") or {}
    if issue:
        head = (f"{issue.get('year')} 年第 {issue.get('number')} 期"
                f"（第 {issue.get('vol')} 卷，总第 {issue.get('total')} 期）")
        lines.append(head)
    annual = payload.get("annual") or {}
    if annual:
        included = annual.get("included") or []
        lines.append(f"年度：{annual.get('year')} 年，纳入 {len(included)} 期："
                     + "、".join(str(n) for n in included))
        if annual.get("missing_issues"):
            lines.append("缺期：" + "、".join(str(n) for n in annual.get("missing_issues") or []))
    counts = payload.get("counts") or {}
    if counts:
        lines.append(f"目录条目：中文 {counts.get('zh_entries', 0)} / "
                     f"英文 {counts.get('en_entries', 0)}；"
                     f"文章 {counts.get('articles', 0)} 篇")
    for item in output_files(payload):
        lines.append(f"{'计划输出' if payload.get('dry_run') else '输出'}："
                     f"{item['path']}（{size_text(item['size'])}）")
    if payload.get("dry_run"):
        lines.append("dry-run：未写任何文件。")
    if not payload.get("ok", True):
        lines.append("引擎报告：本次没有生成（详见日志与下面的提示）。")
    for text in payload.get("warnings") or []:
        lines.append(f"⚠️ {text}")
    return lines


class DirPushPanel:
    """当期 / 年度推送参数与结果摘要；输出根记忆在 recent.dirpush_out。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = page.app
        self.t = page.t
        self.feedback = ft.Container()
        self.mode = C.select(
            self.t, value=self.app.store.get("form.dirpush_mode", MODE_ISSUE) or MODE_ISSUE,
            width=self.t.px(180), label="推送范围",
            options=[(MODE_ISSUE, "当期目录摘要"), (MODE_ANNUAL, "年度总目录")],
            on_select=self._mode_changed,
        )
        self.year = C.text_field(
            self.t, value=self.app.store.get("form.dirpush_year", "") or "",
            width=self.t.px(150),
            on_change=lambda e: self._store_value("form.dirpush_year", e.control.value),
        )
        self.issues = C.text_field(
            self.t, value=self.app.store.get("form.dirpush_issues", "") or "",
            expand=True,
            on_change=lambda e: self._store_value("form.dirpush_issues", e.control.value),
        )
        self.annual_rows = ft.Column(spacing=self.t.sp("1"), visible=False)
        self.annual_rows.controls = [ft.Row(spacing=self.t.sp("1"), controls=[
            C.form_field(self.t, "年度（yyyy，可空）", self.year),
            C.form_field(self.t, "只含这些期号（如 1,2,3；年度可空）", self.issues,
                         expand=True),
        ])]

    def _store_value(self, key: str, value) -> None:
        self.app.store.set(key, str(value or "").strip())

    def _mode_changed(self, e=None) -> None:
        value = (self.mode.value or MODE_ISSUE)
        self._store_value("form.dirpush_mode", value)
        self.annual_rows.visible = value == MODE_ANNUAL
        try:
            self.annual_rows.update()
        except Exception:  # noqa: BLE001
            pass

    def _switch_row(self, label: str, desc: str, checked: bool, on_change,
                    *, disabled: bool = False, reason: str = ""):
        """开关行：复用 C.option_row（禁止在页面里裸建 Switch）。

        年度模式下英文开关按 spec 置灰并给原因：on_change 置空（点了不生效）
        + 0.55 透明度 + 原因文案，保留可见而不是隐藏。
        """
        row = C.option_row(label, reason if disabled else desc, self.t,
                           icon="TRANSLATE" if "英文" in label else "SCIENCE_OUTLINED",
                           checked=checked,
                           on_change=None if disabled else on_change)
        if disabled:
            row.opacity = 0.55
            row.tooltip = reason
        return row

    def params(self) -> list:
        t = self.t
        options = self.app.store.get("options", {}) or {}
        mode = self.mode.value or MODE_ISSUE
        annual = mode == MODE_ANNUAL
        self.annual_rows.visible = annual
        out_root = self.page.field("dirpush_out")
        default_root = EB.dirpush_default_out_root()
        target = out_root or default_root
        level, message = validate_out_root(out_root, self.page.field("issue_dir"))
        rows: list = [
            C.notice_bar("先预演（只预演开关）不写文件；输出根落在代码仓内会二次确认。",
                         t, "info"),
            self.page.add_picker("输出根", "dirpush_out",
                                 f"目录摘要 docx 输出根；留空 = 引擎默认 {default_root}",
                                 kind="dir", required=False),
            C.text(f"留空 = 引擎默认：{default_root}", t, "caption",
                   t.palette.text_faint, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=t.px(10), vertical=t.px(6)),
                bgcolor=t.palette.surface_2, border_radius=t.radius("sm"),
                content=ft.Column(spacing=2, controls=[
                    C.text("将写入", t, "caption", t.palette.text_muted),
                    C.text(target, t, "mono", t.palette.text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                    C.text("预演：只校验、不写文件" if self._dry_run() else
                           "同根下同名 docx 会被覆盖（不保留 .bak）· 先预演一次",
                           t, "caption", t.palette.text_faint, max_lines=2),
                ]),
            ),
        ]
        if level == "error":
            rows.append(C.notice_bar(message, t, "error"))
        elif level == "warn":
            rows.append(C.notice_bar("仓内输出：" + message, t, "warn"))
        rows += [
            ft.Row(spacing=t.sp("1"), controls=[C.form_field(t, "推送范围", self.mode)]),
            self.annual_rows,
            self._switch_row("含英文目录",
                             "当期模式加 --with-en-toc",
                             bool(options.get("dirpush_with_en_toc", False)),
                             lambda e: self.app.set_option("dirpush_with_en_toc",
                                                           e.control.value),
                             disabled=annual,
                             reason="年度体例按各期《目录摘要设置.json》还原，此开关只作用于当期"),
            self._switch_row("只预演",
                             "采集并校验，不写文件（--dry-run）",
                             bool(options.get("dirpush_dry_run", False)),
                             lambda e: self.app.set_option("dirpush_dry_run",
                                                           e.control.value)),
            C.text(issues_caption(self.app.store.get("form.dirpush_issues", ""))
                   if annual else "", t, "caption", t.palette.text_faint),
            self.feedback,
        ]
        return rows

    def _dry_run(self) -> bool:
        return bool((self.app.store.get("options", {}) or {}).get("dirpush_dry_run", False))

    def values(self) -> dict:
        return {
            "out_root": self.page.field("dirpush_out"),
            "annual": (self.mode.value or MODE_ISSUE) == MODE_ANNUAL,
            "year": (self.year.value or "").strip(),
            "include_issues": (self.issues.value or "").strip(),
            "with_en_toc": bool((self.app.store.get("options", {}) or {})
                                .get("dirpush_with_en_toc", False)),
            "dry_run": bool((self.app.store.get("options", {}) or {})
                            .get("dirpush_dry_run", False)),
        }

    def start(self, dry_run: bool = False) -> None:
        if self.app.running:
            self.app.toast("上一任务仍在运行，先停止或等它结束")
            self.page.mark("validation_error", step="dirpush", reason="busy")
            return
        issue = self.page.field("issue_dir")
        if not issue:
            self.app.toast("请先选择期目录")
            self.page.mark("validation_error", step="dirpush", reason="missing issue dir")
            return
        values = self.values()
        call = build_call(issue=issue, out_root=values["out_root"],
                          annual=values["annual"], year=values["year"],
                          include_issues=values["include_issues"],
                          with_en_toc=values["with_en_toc"],
                          dry_run=bool(dry_run or values["dry_run"]))
        warn = repo_warning(values["out_root"])
        if warn:
            self.feedback.content = C.notice_bar(warn, self.t, "warn")
            try:
                self.feedback.update()
            except Exception:  # noqa: BLE001
                pass

        def _go() -> None:
            self.page.mark("dirpush_submit",
                           mode=MODE_ANNUAL if values["annual"] else MODE_ISSUE,
                           dry_run=call.argv.count("--dry-run") > 0)
            self.page._status["dirpush"] = "running"
            self.page._refresh_steps()
            self.page.page.run_task(self.page._run_calls, [("dirpush", call)])

        if warn and not (dry_run or values["dry_run"]) and not getattr(self.app, "auto_confirm", False):
            def _confirmed() -> None:
                self.page.mark("confirm_repo_out", path=values["out_root"])
                _go()

            C.confirm(self.page.page, self.t, title="输出根在代码仓内",
                      message=warn + " 仍要生成吗？", confirm_label="继续生成",
                      on_confirm=_confirmed)
            return
        _go()
