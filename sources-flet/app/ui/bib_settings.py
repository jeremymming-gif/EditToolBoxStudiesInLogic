"""引用库 · 设置（批 109 W1a + W1b R6）：库文件只读统计 + 核实成本闸门 + Git 版本层。

旧壳 Tk 的设置页（`bibdb_ui.py::BibDbSettingsPage`）只有核实成本闸门与笔记仓路径；Git
版本层在旧壳「待处理」页。Flet 单壳把两件事并到「引用库 → 设置」：

* 核实成本闸门（W1b R6）：核实上限 / 先算账（--verify-plan）/ 允许全量核实
  （--verify-all-refs，开时二次确认）/ 先查引用库（--no-bibdb）+ 当前生效值；值存在
  `settings.json` 的 `bibdb` 段（P2_SETTINGS 隔离，旧配置 `verifyMax` 等键兼容）；
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
import time

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from services import settings_store as SS
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
        "records": str(stats.get("records") if stats.get("records") is not None else "—"),
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
        lines.append(f"仓库状态：{' / '.join(blocked)}进行中（提交/推送/回退都会失败）")
    return lines


def library_confirm_text(probe: dict | None) -> str:
    probe = probe or {}
    branch = str(probe.get("branch") or "—")
    return (f"提交只含 bib_db/bibliography.jsonl；没有改动不会空提交。\n"
            f"分支 {branch}：只在本地提交，不推送。")


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
            "（不 hard reset、不推送、不改其它文件）。库内容回到上一个版本；"
            "本次改动将从工作区丢失（历史仍在 git 里）。")


def _tone_of(result: dict) -> str:
    return "success" if result.get("ok") else ("warn" if result.get("soft") else "error")


def commit_notice(result: dict | None) -> tuple[str, str]:
    result = result or {}
    if result.get("ok") and result.get("committed"):
        return "success", f"已提交 {short_sha(str(result.get('sha') or ''))}（未推送）。"
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
        # 批 109 W1b（R6）：核实成本闸门 —— 值存在 settings.bibdb 段（P2_SETTINGS 隔离）。
        self.bibdb_text = SS.verify_max_text(self._bibdb())
        self.bibdb_plan = bool(self._bibdb().get("verify_plan"))
        self.bibdb_all = bool(self._bibdb().get("verify_all_refs"))
        self.bibdb_lookup = bool(self._bibdb().get("lookup_enabled", True))
        self.bibdb_error = ""
        self.bibdb_receipt = ""

    # -- 小工具 ---------------------------------------------------------
    def _notes(self) -> str:
        return str(getattr(self.page.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = self.stats or self.page.stats_data or {}
        return str(stats.get("db") or "") or GIT.default_db(self._notes())

    def _bibdb(self) -> dict:
        store = self.page.app.store
        return SS.bibdb_section(getattr(store, "data", None) or {})

    def _refresh(self) -> None:
        self.page._render()

    def _run(self, task) -> None:
        """调度协程对象（调用点写 `self._run(self.refresh())`）。

        flet 1.0.5 的 `Page.run_task` 只收 coroutine function（GUI-04b），所以把协程
        对象包成 async 函数再交给它；签名与行为不变，调用点无需改动。
        """
        async def _await_task() -> None:
            await task

        self.page.page.run_task(_await_task)

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
        controls: list = [self._bibdb_card(), self._library_card(), self._git_card(),
                          self._boundary_card()]
        controls += [C.notice_bar(message, t, tone) for tone, message in self.notices]
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.page.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text, selectable=True),
        ])

    # -- 核实成本闸门（R6，文案与旧壳 BibDbSettingsPage 对齐）--------------
    def _bibdb_card(self) -> ft.Control:
        t = self.page.t
        border_col = t.palette.error if self.bibdb_error else t.palette.border
        focus_col = t.palette.error if self.bibdb_error else t.palette.accent
        db = self._db()
        exists = bool(db) and os.path.exists(db)
        section = self._bibdb()
        self._max_hint = C.text(self.bibdb_error, t, "caption",
                                t.palette.error if self.bibdb_error else t.palette.text_faint)
        max_row = ft.Row(spacing=t.sp("half"), wrap=True,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.form_field(t, "核实上限", C.text_field(
                t, value=self.bibdb_text, width=120,
                on_change=self._on_verify_max)),
            C.text(f"条（0 = 不限；留空 = 引擎默认 {SS.DEFAULT_VERIFY_MAX} 条）",
                   t, "small", t.palette.text_muted),
        ])
        rows = [self._kv(label, value) for label, value in
                SS.bibdb_effective_rows(section, db=db, db_exists=exists)]
        rows.append(self._kv("笔记仓路径",
                             self._notes() or "（未设置：在「设置」页指定）"))
        rows.append(self._kv("库文件", db or "（未找到：先到「入藏」导入）"))
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.section_title("核实费用", t, note="引用库 · 核实选项；值存在本机 settings.json"),
            C.text("从第 128 条起只核实正文引用的条目。超过上限且未开「允许全量核实」时，"
                   "引擎退出 2 并列出范围与预计费用——不是程序出错，先看账再决定。",
                   t, "small", t.palette.text_muted),
            max_row, self._max_hint,
            C.option_row("核实前先算账（--verify-plan）",
                         "只算账、不核实：转换照常写产物，但不生成任务书、不检索、不调 AI，只打印"
                         "「核实多少条、预计检索/AI 多少次、约多少钱」后退出 0。关掉再跑才真正核实。",
                         t, icon="CALCULATE_OUTLINED",
                         checked=self.bibdb_plan, on_change=self._on_verify_plan),
            C.option_row("允许全量核实（--verify-all-refs）",
                         "越过上限（正文 \\nocite{*} 也要求全量）。默认不开：没被正文引用的条目"
                         "也会花检索/AI 的钱（期 87：376 条 $1.8+）。", t,
                         icon="ALL_INCLUSIVE", checked=self.bibdb_all, on_change=self._on_verify_all),
            C.option_row("核实前先查引用库（默认开）",
                         "开：核实时若引用库存在，先按 DOI / 题名 / key 查库；命中的键不调检索、"
                         "不调 AI（仍写进任务书「五、库内已核实」，字段差异只提示）。"
                         "关 = 传 --no-bibdb，一律不查库。", t,
                         icon="SEARCH", checked=self.bibdb_lookup, on_change=self._on_lookup),
            C.section_title("当前生效值", t, note="下一次「初排转换」会用"),
            *rows,
            C.text("只影响「初排转换 → AI 协同初排」的核实；普通初排不传这些参数。",
                   t, "caption", t.palette.text_faint),
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.primary_button("保存设置", lambda e: self._save_bibdb(), t,
                                 icon="SAVE_OUTLINED"),
                C.text(self.bibdb_receipt, t, "small", t.palette.text_muted),
            ]),
        ]), t)

    def _on_verify_max(self, e=None) -> None:
        self.bibdb_text = "" if e is None else str(e.control.value or "")
        _, error = SS.parse_verify_max(self.bibdb_text)
        self.bibdb_error = error or ""
        hint = getattr(self, "_max_hint", None)
        if hint is not None:
            hint.value = self.bibdb_error
            hint.color = self.page.t.palette.error if self.bibdb_error else self.page.t.palette.text_faint
            try:  # 未挂载时 update 会抛 RuntimeError：忽略，下次 build 生效
                hint.update()
            except Exception:  # noqa: BLE001
                pass

    def _on_verify_plan(self, e) -> None:
        self.bibdb_plan = bool(e.control.value)

    def _on_verify_all(self, e) -> None:
        if not bool(e.control.value):
            self.bibdb_all = False
            return
        C.confirm(self.page.page, self.page.t, title="允许全量核实？",
                  message="全量核实会为没被正文引用的条目也花检索/AI 的钱（期 87 实测："
                          "376 条 $1.8+）。只影响核实，不改库文件。", confirm_label="允许",
                  on_confirm=lambda: self._set_all_refs(True))
        # 复选框先回到关：确认后由 _set_all_refs 重建为开。
        self.bibdb_all = False
        self._refresh()

    def _on_lookup(self, e) -> None:
        self.bibdb_lookup = bool(e.control.value)

    def _set_all_refs(self, value: bool) -> None:
        self.bibdb_all = bool(value)
        self._refresh()

    def _save_bibdb(self) -> None:
        value, error = SS.parse_verify_max(self.bibdb_text)
        if error:
            self.bibdb_error = error
            self.bibdb_receipt = "未保存：核实上限输入有误（修好再保存）。"
            self._refresh()
            return
        store = self.page.app.store
        try:
            store.set("bibdb.verify_max", value)
            store.set("bibdb.verify_plan", bool(self.bibdb_plan))
            store.set("bibdb.verify_all_refs", bool(self.bibdb_all))
            store.set("bibdb.lookup_enabled", bool(self.bibdb_lookup))
            store.save()
        except Exception as exc:  # noqa: BLE001
            self.bibdb_receipt = f"保存失败：{exc}"
            self._refresh()
            return
        self.bibdb_receipt = f"已保存 · {time.strftime('%H:%M:%S')}（仅存在本机：{store.path}）"
        self.page.mark("bib_settings_bibdb", ok=True, verify_max=value,
                       verify_plan=bool(self.bibdb_plan), verify_all_refs=bool(self.bibdb_all),
                       lookup_enabled=bool(self.bibdb_lookup))
        self._refresh()

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
    def _git_guard(self) -> bool:
        """版本层不可用 → 统一提示并刷新（提交/推送/回退共用，避免三份同文）。"""
        if git_available(self.probe or {}):
            return True
        self._notice("error", "版本库不可用（未就绪，或有冲突/变基/合并）。先点「刷新」重试。")
        self._refresh()
        return False

    def _ask_commit(self) -> None:
        if self.busy:
            return
        if not self._git_guard():
            return
        C.confirm(self.page.page, self.page.t, title="提交引用库改动？",
                  message=commit_confirm_text(self.probe or {}), confirm_label="提交",
                  on_confirm=lambda: self._run(self._do_commit()))

    def _ask_push(self) -> None:
        if self.busy:
            return
        if not self._git_guard():
            return
        C.confirm(self.page.page, self.page.t, title="推送引用库提交？",
                  message=push_confirm_text(self.probe or {}), confirm_label="推送",
                  on_confirm=lambda: self._run(self._do_push()))

    def _ask_rollback(self) -> None:
        if self.busy:
            return
        if not self._git_guard():
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
