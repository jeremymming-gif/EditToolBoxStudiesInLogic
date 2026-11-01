"""引用库 · 设置（批 109 W1a）：库文件只读统计 + Git 版本层（提交 / 推送 / 回退）。

旧壳 Tk 的设置页（`bibdb_ui.py::BibDbSettingsPage`）只有核实成本闸门与笔记仓路径；Git
版本层在旧壳「待处理」页。Flet 单壳把两件事并到「引用库 → 设置」：

* 库文件：路径、是否存在、条目数（`bibdb.py stats --json`）、文件大小、打开库文件夹；
* 版本层（复用 `services/bib_git.py`）：分支 / upstream / 未推送 / 脏否 / 最近 3 条提交；
* 写操作一律二次确认，且**只动本地 git**：提交只含 `bib_db/bibliography.jsonl`；推送只推
  「连续仅含 bib_db/ 文件」的前缀（遇其它提交整次拒绝，绝不 force）；回退 = checkout 上一
  个提交的库文件 + 生成新提交（不 hard reset、不推送）。绝不自动 push / pull / 切分支。

纯函数放上半部（文案与降级规则可单测）；控件由 `build()` 重建，状态留在视图对象上。
"""
from __future__ import annotations

import asyncio
import os

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import components as C


# ── 纯函数（可单测）────────────────────────────────────────────────────────
def fmt_size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def short_sha(sha: str, length: int = 8) -> str:
    return GIT.short_sha(str(sha or ""), length) if sha else "—"


def library_summary(stats: dict | None) -> dict:
    stats = stats or {}
    aliases = stats.get("aliases") or {}
    return {
        "db": str(stats.get("db") or "（未知；引擎未解析到笔记仓）"),
        "records": str(aliases.get("records") if aliases.get("records") is not None else "—"),
        "aliases_total": str(aliases.get("total") if aliases.get("total") is not None else "—"),
        "size": fmt_size(stats.get("bytes")),
    }


def _identity_text(identity) -> str:
    if isinstance(identity, dict):
        name = str(identity.get("name") or "").strip()
        email = str(identity.get("email") or "").strip()
        joined = f"{name} <{email}>" if name and email else (name or (f"<{email}>" if email else ""))
        return joined or "—"
    return str(identity or "—")


def probe_lines(probe: dict | None) -> list[str]:
    """版本层人读摘要（顺序：分支 → 落差 → 脏否 → 身份 → 最近提交）。"""
    if not probe:
        return ["尚未读取版本层状态。"]
    if not probe.get("ok"):
        return [f"读取失败：{probe.get('error') or '未知原因'}"]
    branch = str(probe.get("branch") or "—")
    upstream = str(probe.get("upstream") or "（无 upstream，不会自动推送）")
    ahead = int(probe.get("ahead") or 0)
    behind = int(probe.get("behind") or 0)
    dirty_db = bool(probe.get("dirty_db"))
    dirty_other = int(probe.get("dirty_other") or 0)
    commits = probe.get("commits") or []
    recent = "；".join(f"{short_sha(str(row.get('sha') or ''))} {row.get('subject') or ''}"
                      for row in commits[:3] if isinstance(row, dict)) or "—"
    lines = [
        f"分支 {branch} · upstream {upstream}",
        f"未推送 {ahead} 条 · 落后 {behind} 条",
        f"库文件：{'有未提交改动' if dirty_db else '干净'} · 其它改动 {dirty_other} 个",
        f"提交身份：{_identity_text(probe.get('identity'))}",
        f"最近库提交：{recent}",
    ]
    blocked = [name for key, name in (("conflict", "冲突"), ("rebase", "变基"),
                                     ("merge", "合并")) if probe.get(key)]
    if blocked:
        lines.append(f"仓库状态：{' / '.join(blocked)}进行中（提交/推送/回退都会被拒绝）")
    return lines


def library_confirm_text(probe: dict | None) -> str:
    probe = probe or {}
    branch = str(probe.get("branch") or "—")
    return (f"提交只含 bib_db/bibliography.jsonl；没有改动不会空提交。\n"
            f"分支 {branch}：提交在本地生成，不推送、不改其它文件。")


def commit_confirm_text(probe: dict | None) -> str:
    return library_confirm_text(probe)


def push_confirm_text(probe: dict | None) -> str:
    probe = probe or {}
    branch = str(probe.get("branch") or "—")
    ahead = int(probe.get("ahead") or 0)
    return (f"分支 {branch} · 未推送 {ahead} 条。\n"
            "只推「连续仅含 bib_db/ 文件」的提交；遇到含 notes 的提交就停在前面的 bib 前缀"
            "并列出剩余 sha。绝不 force、不推送其它文件。")


def rollback_confirm_text() -> str:
    return ("回退 = `git checkout HEAD~1 -- bib_db/bibliography.jsonl` 再生成一次新提交"
            "（不 hard reset、不推送、不改其它文件）。库内容会回到上一个版本，"
            "已写库的本次改动将不可从工作区恢复（历史仍在 git 里）。")


def _tone_of(result: dict) -> str:
    return "success" if result.get("ok") else ("warn" if result.get("soft") else "error")


def commit_notice(result: dict | None) -> tuple[str, str]:
    result = result or {}
    if result.get("ok") and result.get("committed"):
        return "success", f"已提交本地版本：{short_sha(str(result.get('sha') or ''))}（未推送）。"
    if result.get("ok") and not result.get("committed"):
        return "info", str(result.get("reason") or "库文件没有改动，未生成空提交。")
    return _tone_of(result), f"提交失败：{result.get('error') or result.get('reason') or '未知原因'}"


def push_notice(result: dict | None) -> tuple[str, str]:
    result = result or {}
    if result.get("ok") and result.get("pushed"):
        return "success", f"已推送 {int(result.get('count') or 0)} 条提交（tip {short_sha(str(result.get('tip') or ''))}）。"
    if result.get("ok") and not result.get("pushed"):
        return "info", str(result.get("reason") or "没有待推送提交。")
    return _tone_of(result), f"推送被拒绝：{result.get('error') or result.get('reason') or '未知原因'}"


def rollback_notice(result: dict | None) -> tuple[str, str]:
    result = result or {}
    if result.get("ok"):
        reason = str(result.get("reason") or "").strip()
        sha = short_sha(str(result.get("sha") or result.get("sha_after") or ""))
        text = reason or (f"已回退并生成新提交 {sha}（未推送）。" if sha != "—" else "已回退到上一个提交。")
        return "success", text
    return _tone_of(result), f"回退失败：{result.get('error') or result.get('reason') or '未知原因'}"


def git_available(probe: dict | None) -> bool:
    probe = probe or {}
    return bool(probe.get("ok")) and not probe.get("conflict") and not probe.get("merge") \
        and not probe.get("rebase")


# ── 视图 ────────────────────────────────────────────────────────────────────
class BibSettingsView:
    """设置视图；状态在对象上，`build()` 只按状态重建控件。"""

    def __init__(self, page) -> None:
        self.page = page  # pages.BibPage
        self.stats: dict | None = None
        self.probe: dict | None = None
        self.loaded = False
        self.loading = False
        self.busy = False
        self.error = ""
        self.notices: list[tuple[str, str]] = []

    # -- 小工具 ---------------------------------------------------------
    def _notes(self) -> str:
        return str(getattr(self.page.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = self.stats or self.page.stats_data or {}
        return str(stats.get("db") or "") or GIT.default_db(self._notes())

    def _refresh(self) -> None:
        self.page._render()

    def _run(self, task) -> None:
        self.page.page.run_task(task)

    def _notice(self, tone: str, message: str) -> None:
        self.notices.append((tone, message))
        self.notices = self.notices[-4:]

    # -- 加载 -----------------------------------------------------------
    async def ensure_loaded(self, force: bool = False) -> None:
        if self.loading or (self.loaded and not force):
            return
        self.loading = True
        try:
            stats = self.page.stats_data
            if not stats:
                result = await asyncio.to_thread(
                    EB.read_json_command, EB.bibdb_stats_call(), notes=self._notes())
                stats = result.get("json") or {}
                self.error = "" if result.get("ok") else str(result.get("error") or "读取失败")
            self.stats = stats
            self.probe = await asyncio.to_thread(
                GIT.status, self._db(), notes_repo=self._notes())
        except Exception as exc:  # noqa: BLE001  读不到也只是降级展示，不影响写操作确认
            self.error = f"{type(exc).__name__}: {exc}"
            self.probe = {"ok": False, "error": self.error}
        finally:
            self.loading = False
            self.loaded = True
        self.page.mark("bib_settings_load", ok=bool((self.probe or {}).get("ok")))
        self._refresh()

    async def refresh(self) -> None:
        await self.ensure_loaded(force=True)

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.page.t
        if not self.loaded and not self.loading:
            self._run(self.ensure_loaded())
        controls: list = [self._library_card(), self._git_card(), self._boundary_card()]
        controls += [C.notice_bar(message, t, tone) for tone, message in self.notices]
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.page.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text, selectable=True),
        ])

    def _library_card(self) -> ft.Control:
        t = self.page.t
        info = library_summary(self.stats)
        db = info["db"]
        exists = bool(db) and os.path.exists(db)
        rows = [
            self._kv("库文件", info["db"]),
            self._kv("文件状态", "存在" if exists else "不存在（先到「入藏」页导入，或检查笔记仓路径）"),
            self._kv("条目数", info["records"]),
            self._kv("别名合计", info["aliases_total"]),
            self._kv("文件大小", info["size"]),
        ]
        if self.error:
            rows.append(self._kv("读取状态", self.error))
        buttons = ft.Row(spacing=t.sp("1"), controls=[
            C.secondary_button("打开库文件夹", lambda e: self._open_folder(), t,
                               disabled=not exists),
            C.secondary_button("刷新", lambda e: self._run(self.refresh()), t),
            C.text("正在读取…" if self.loading else "", t, "small", t.palette.text_faint),
        ])
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.section_title("引用库 · 设置", t, note="只读统计 + 版本层；写操作都要二次确认"),
            *rows, buttons,
        ]), t)

    def _git_card(self) -> ft.Control:
        t = self.page.t
        probe = self.probe or {}
        lines = probe_lines(probe)
        controls: list = [C.section_title(
            "Git 版本层", t, note="本地 git · 只提交 bib_db/bibliography.jsonl")]
        controls += [C.text(line, t, "small", t.palette.text) for line in lines]
        warnings = [str(item) for item in probe.get("warnings") or [] if str(item).strip()]
        for warning in warnings[:3]:
            controls.append(C.notice_bar(warning, t, "warn"))
        if probe and not probe.get("ok") and probe.get("error"):
            controls.append(C.notice_bar(f"版本层不可用：{probe['error']}", t, "warn"))
        available = git_available(probe)
        buttons = ft.Row(spacing=t.sp("1"), wrap=True,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.secondary_button("刷新", lambda e: self._run(self.refresh()), t,
                               disabled=self.busy),
            C.primary_button("提交…", lambda e: self._ask_commit(), t, disabled=self.busy or not available),
            C.secondary_button("推送…", lambda e: self._ask_push(), t, disabled=self.busy or not available),
            C.secondary_button("回退到上一个提交…", lambda e: self._ask_rollback(), t,
                               disabled=self.busy or not available),
            C.text("处理中…" if self.busy else "", t, "small", t.palette.text_faint),
        ])
        controls.append(buttons)
        return C.card(ft.Column(spacing=t.sp("half"), controls=controls), t)

    def _boundary_card(self) -> ft.Control:
        t = self.page.t
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.section_title("边界", t, note="本页不写引用库内容"),
            C.text("提交 / 推送 / 回退都要二次确认；本页绝不自动 push、pull 或切分支。",
                   t, "small", t.palette.text_muted),
            C.text("回退 = 把库文件恢复到上一个提交再生成一条新提交；不 hard reset、不推送，"
                   "其它文件不动。", t, "small", t.palette.text_muted),
        ]), t)

    def _open_folder(self) -> None:
        db = str((self.stats or {}).get("db") or "")
        if db:
            self.page.app.open_path(os.path.dirname(db))

    # -- 动作（全部二次确认）--------------------------------------------
    def _ask_commit(self) -> None:
        if self.busy:
            return
        probe = self.probe or {}
        if not git_available(probe):
            self._notice("error", "版本层不可用（未就绪或有冲突/变基/合并），先点「刷新」重试。")
            self._refresh()
            return
        C.confirm(self.page.page, self.page.t, title="提交引用库改动？",
                  message=commit_confirm_text(probe), confirm_label="提交",
                  on_confirm=lambda: self._run(self._do_commit()))

    def _ask_push(self) -> None:
        if self.busy:
            return
        probe = self.probe or {}
        if not git_available(probe):
            self._notice("error", "版本层不可用（未就绪或有冲突/变基/合并），先点「刷新」重试。")
            self._refresh()
            return
        C.confirm(self.page.page, self.page.t, title="推送引用库提交？",
                  message=push_confirm_text(probe), confirm_label="推送",
                  on_confirm=lambda: self._run(self._do_push()))

    def _ask_rollback(self) -> None:
        if self.busy:
            return
        if not git_available(self.probe or {}):
            self._notice("error", "版本层不可用（未就绪或有冲突/变基/合并），先点「刷新」重试。")
            self._refresh()
            return
        C.confirm(self.page.page, self.page.t, title="回退引用库到上一个提交？",
                  message=rollback_confirm_text(), confirm_label="回退",
                  on_confirm=lambda: self._run(self._do_rollback()))

    async def _do_commit(self) -> None:
        self.busy = True
        self._refresh()
        try:
            head = str((self.probe or {}).get("head") or "")
            message = f"引用库：手动提交（设置页；sha {GIT.short_sha(head, 8)} 起）"
            result = await asyncio.to_thread(GIT.commit_db, self._db(), message)
            tone, text = commit_notice(result)
        except Exception as exc:  # noqa: BLE001
            tone, text = "error", f"提交失败：{exc}"
        self.busy = False
        self._notice(tone, text)
        await self.ensure_loaded(force=True)

    async def _do_push(self) -> None:
        self.busy = True
        self._refresh()
        try:
            result = await asyncio.to_thread(GIT.push, self._db(), notes_repo=self._notes())
            tone, text = push_notice(result)
        except Exception as exc:  # noqa: BLE001
            tone, text = "error", f"推送失败：{exc}"
        self.busy = False
        self._notice(tone, text)
        await self.ensure_loaded(force=True)

    async def _do_rollback(self) -> None:
        self.busy = True
        self._refresh()
        try:
            result = await asyncio.to_thread(GIT.rollback, self._db(), notes_repo=self._notes())
            tone, text = rollback_notice(result)
        except Exception as exc:  # noqa: BLE001
            tone, text = "error", f"回退失败：{exc}"
        self.busy = False
        self._notice(tone, text)
        await self.ensure_loaded(force=True)
