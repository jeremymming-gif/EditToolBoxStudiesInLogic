"""引用库版本与同步页：展示 Git 状态并提供显式确认的本地操作。"""
from __future__ import annotations

import asyncio
import os

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C
from ui.bib_settings import (
    commit_notice,
    git_available,
    library_summary,
    probe_lines,
    push_notice,
    rollback_notice,
)


TITLE = "版本与同步"


def _count(value, *, unknown: str = "未知") -> str:
    return unknown if value is None else str(value)


class BibVersionSyncView:
    """引用库 Git 状态与受控操作；所有变更由用户本次显式触发。"""

    def __init__(self, page) -> None:
        self.page = page
        self.stats: dict = {}
        self.probe: dict = {}
        self.loaded = False
        self.loading = False
        self.busy = False
        self.notices: list[tuple[str, str]] = []

    @property
    def t(self):
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(self.page.app, "notes_repo", "") or "")

    def _db(self) -> str:
        if hasattr(self.page, "stats_data"):
            stats = getattr(self.page, "stats_data", None) or {}
        else:
            stats = self.stats or {}
        path = str(stats.get("db") or stats.get("path") or "").strip()
        return path or GIT.default_db(self._notes())

    def _confirmed_target_matches(self, notes: str, snapshot: dict,
                                  operation: str) -> bool:
        current_db = self._db()
        expected_db = str(snapshot.get("db") or "")
        same_db = (os.path.realpath(os.path.abspath(current_db))
                   == os.path.realpath(os.path.abspath(expected_db)))
        if same_db and self._notes() == notes:
            return True
        self.busy = False
        self._notice(
            "warn",
            f"当前引用库目标或笔记仓设置已变化；未使用旧确认{operation}。"
            "请重新检查并确认。",
        )
        self._refresh_view()
        return False

    def _run(self, task) -> None:
        async def _await_task():
            await task

        self.page.page.run_task(_await_task)

    def _refresh_view(self) -> None:
        self.page._render()

    async def ensure_loaded(self, force: bool = False) -> None:
        if self.loading or (self.loaded and not force):
            return
        self.loading = True
        try:
            if not force:
                self.stats = getattr(self.page, "stats_data", None) or self.stats
            if force or not self.stats:
                previous_db = self._db()
                result = await asyncio.to_thread(
                    EB.read_json_command, EB.bibdb_stats_call(), notes=self._notes())
                if result.get("json") is not None:
                    self.stats = result["json"]
                elif force:
                    self.stats = {"db": previous_db, "records": None, "bytes": None,
                                  "aliases": {"total": None}}
                if not result.get("ok"):
                    self._notice("error", str(result.get("stderr") or
                                                 result.get("error") or "读取引用库统计失败"))
            self.probe = await asyncio.to_thread(
                GIT.status, self._db(), notes_repo=self._notes())
        except Exception as exc:  # noqa: BLE001 - 错误需在页内明示
            self.probe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.loading = False
            self.loaded = True
        self.page.mark("bib_version_sync_load", ok=bool(self.probe.get("ok")),
                       db=self._db(), error=self.probe.get("error", ""))
        self._refresh_view()

    async def refresh(self) -> None:
        await self.ensure_loaded(force=True)

    def activate(self) -> None:
        self._run(self.ensure_loaded())

    def build(self) -> ft.Control:
        if not self.loaded and not self.loading:
            self._run(self.ensure_loaded())
        t = self.t
        body = ft.Column(spacing=t.sp("2"), scroll=C.scrollbar(t), expand=True, controls=[
            self._target_card(), self._status_card(), self._actions_card(),
            *[C.notice_bar(message, t, tone) for tone, message in self.notices],
        ])
        return S.page_shell(
            t, title=TITLE,
            meta="只显示服务实际提供的 Git 状态；操作仅在本次显式确认后执行",
            role_chip=S.role_chip(t, "write"), body=body,
            on_gear=lambda e: self.page.navigate_route("bib:settings"),
        )

    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), wrap=False, controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text, selectable=True),
        ])

    def _target_card(self) -> ft.Control:
        t = self.t
        summary = library_summary(self.stats)
        path = self._db() or summary["db"]
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.section_title("目标库", t, note="操作只针对此库文件"),
            self._kv("库文件路径", path),
            self._kv("文件状态", "存在" if path and os.path.isfile(path) else "不存在"),
            self._kv("条目数", summary["records"]),
            C.secondary_button("刷新状态", lambda e: self._run(self.refresh()), t,
                               icon="REFRESH", disabled=self.busy or self.loading),
        ]), t)

    def _status_card(self) -> ft.Control:
        t = self.t
        probe = self.probe or {}
        lines = probe_lines(probe)
        if probe.get("ok"):
            ahead = _count(probe.get("ahead"))
            behind = _count(probe.get("behind"))
            rows = [
                self._kv("分支", probe.get("branch") or "（未知）"),
                self._kv("HEAD", probe.get("head") or "（未知）"),
                self._kv("upstream", probe.get("upstream") or "（未设置）"),
                self._kv("ahead / behind", f"{ahead} / {behind}"),
                self._kv("dirty_db", "是" if probe.get("dirty_db") else "否"),
                self._kv("dirty_other", str(probe.get("dirty_other", 0))),
                self._kv("冲突", "有" if probe.get("conflict") else "无"),
                self._kv("merge", "进行中" if probe.get("merge") else "无"),
                self._kv("rebase", "进行中" if probe.get("rebase") else "无"),
            ]
            commits = probe.get("commits") or []
            recent = [f"{GIT.short_sha(str(row.get('sha') or ''), 8)} "
                      f"{row.get('subject') or ''}"
                      for row in commits if isinstance(row, dict)]
            if not recent:
                recent = ["（没有可显示的库提交）"]
        else:
            rows = [C.notice_bar(lines[0], t, "error")]
            recent = []
        controls: list = [C.section_title("Git 状态", t)]
        controls.extend(rows)
        warnings = [str(item) for item in probe.get("warnings") or [] if str(item).strip()]
        controls.extend(C.notice_bar(item, t, "warn") for item in warnings)
        if recent:
            controls.append(C.section_title("最近库提交", t))
            controls.extend(C.text(item, t, "small", t.palette.text) for item in recent[:3])
        controls.append(C.text(
            "本页没有 diff 查看能力；不会执行 pull、fetch、冲突解决、force 或覆盖。",
            t, "caption", t.palette.text_muted))
        return C.card(ft.Column(spacing=t.sp("half"), controls=controls), t)

    def _actions_card(self) -> ft.Control:
        t = self.t
        probe = self.probe or {}
        available = git_available(probe)
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.section_title("本地版本操作", t,
                            note="不自动 push；每次操作只在本次运行生效"),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                C.primary_button("提交库改动…", lambda e: self._ask_commit(), t,
                                 disabled=self.busy or not available),
                C.secondary_button("推送…", lambda e: self._ask_push(), t,
                                   disabled=self.busy or not available),
                C.secondary_button("回退到上一个库版本…", lambda e: self._ask_rollback(), t,
                                   disabled=self.busy or not available),
                C.text("处理中…" if self.busy else "", t, "small", t.palette.text_faint),
            ]),
            C.text("提交仅包含引用库文件；推送沿用 bib_git 的受限 bib-only 前缀规则；"
                   "回退生成新提交，不 hard reset、不推送。",
                   t, "caption", t.palette.text_muted),
        ]), t)

    def _notice(self, tone: str, text: str) -> None:
        self.notices = (self.notices + [(tone, str(text))])[-4:]

    def _guard(self) -> bool:
        if git_available(self.probe):
            return True
        error = str(self.probe.get("error") or
                    "版本库不可用，或当前存在冲突/merge/rebase 状态。")
        self._notice("error", error)
        self._refresh_view()
        return False

    def _confirm(self, title: str, message: str, label: str, runner, *,
                 check_probe: bool = True) -> None:
        if self.busy or (check_probe and not self._guard()):
            return
        confirmed = False

        def _confirm_once() -> None:
            nonlocal confirmed
            if confirmed or self.busy:
                return
            confirmed = True
            self.busy = True
            self._run(runner())

        C.confirm(self.page.page, self.t, title=title, message=message,
                  confirm_label=label, on_confirm=_confirm_once)

    def _ask_commit(self) -> None:
        self._ask_mutation("commit")

    def _ask_mutation(self, operation: str) -> None:
        if self.busy or not self._guard():
            return
        db, notes = self._db(), self._notes()
        self.busy = True
        self._refresh_view()
        self._run(self._prepare_mutation(operation, db, notes))

    async def _prepare_mutation(self, operation: str, db: str, notes: str) -> None:
        try:
            snapshot = await asyncio.to_thread(
                GIT.mutation_snapshot, db, notes_repo=notes)
        except Exception as exc:  # noqa: BLE001 - 预览失败必须显示
            snapshot = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        if not snapshot.get("ok"):
            self._notice("error", str(snapshot.get("error") or "无法读取目标库状态。"))
            self._refresh_view()
            return
        blocked = [label for label, active in (
            ("冲突", snapshot.get("conflict")),
            ("merge", snapshot.get("merge")),
            ("rebase", snapshot.get("rebase"))) if active]
        if blocked or (operation == "rollback" and snapshot.get("dirty_db")):
            reason = ("仓库存在 " + "/".join(blocked) + " 状态，不能执行版本操作。"
                      if blocked else "库文件有未提交改动；请先处理后再回退。")
            self._notice("warn", reason)
            self._refresh_view()
            return
        db_sha = GIT.short_sha(snapshot.get("db_sha256", ""), 12) or "不可读"
        message = (
            f"目标库：{snapshot['db']}\n仓库：{snapshot['repo_root']}\n"
            f"分支：{snapshot['branch'] or '（未知）'} · "
            f"HEAD {GIT.short_sha(snapshot['head'], 12) or '（未知）'}\n"
            f"库文件：{'有未提交改动' if snapshot['dirty_db'] else '干净'} · "
            f"SHA256 {db_sha}\n"
            f"其他工作树改动：{snapshot['dirty_other']} 项；"
            f"提交身份：{snapshot['identity'] or '未配置'}\n"
            "确认后仅在上述目标与 Git 状态未变化时执行。"
        )
        if operation == "commit":
            self._confirm("提交引用库改动？", message + "\n提交仅包含引用库文件。", "提交",
                          lambda: self._do_commit(db, notes, snapshot), check_probe=False)
        else:
            self._confirm("回退引用库到上一个版本？", message + "\n回退会改写此库文件并生成新提交；不推送。",
                          "回退", lambda: self._do_rollback(db, notes, snapshot),
                          check_probe=False)

    def _ask_push(self) -> None:
        if self.busy or not self._guard():
            return
        db, notes = self._db(), self._notes()
        self.busy = True
        self._refresh_view()
        self._run(self._prepare_push(db, notes))

    async def _prepare_push(self, db: str, notes: str) -> None:
        try:
            snapshot = await asyncio.to_thread(GIT.push_snapshot, db, notes_repo=notes)
        except Exception as exc:  # noqa: BLE001 - preview errors must be visible
            snapshot = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        if not snapshot.get("ok"):
            self._notice("warn" if snapshot.get("soft") else "error",
                         str(snapshot.get("error") or "无法预览待推送提交。"))
            self._refresh_view()
            return
        if snapshot.get("kind") != "pushable":
            self._notice("info", "没有待推送提交。")
            self._refresh_view()
            return
        message = (
            f"目标库：{snapshot['db']}\n仓库：{snapshot['repo_root']}\n"
            f"分支：{snapshot['branch']} · HEAD {GIT.short_sha(snapshot['head'], 12)}\n"
            f"upstream：{snapshot['upstream']} · 当前基点 "
            f"{GIT.short_sha(snapshot['upstream_sha'], 12)}\n"
            f"推送目的地：{'；'.join(GIT.display_push_url(url) for url in snapshot['push_urls'])}\n"
            f"待推提交 SHA：{'、'.join(GIT.short_sha(sha, 12) for sha in snapshot['outgoing_shas'])}\n"
            f"待推送：{snapshot['count']} 条 bib-only 提交，精确 refspec tip "
            f"{GIT.short_sha(snapshot['tip'], 12)}。\n"
            "只推送此确认列出的目的地与 tip；若目标、HEAD 或 upstream 变化，会拒绝执行。"
            "不 force、不 pull、不 fetch。"
        )
        self._confirm("推送引用库提交？", message, "推送",
                      lambda: self._do_push(db, notes, snapshot))

    def _ask_rollback(self) -> None:
        self._ask_mutation("rollback")

    async def _perform(self, operation: str, call, formatter) -> None:
        self.busy = True
        self._refresh_view()
        try:
            result = await asyncio.to_thread(call)
            tone, text = formatter(result)
        except Exception as exc:  # noqa: BLE001 - 展示真实异常，不伪装成功
            tone, text = "error", f"{operation}失败：{type(exc).__name__}: {exc}"
        self._notice(tone, text)
        self.busy = False
        await self.ensure_loaded(force=True)

    async def _do_commit(self, db: str | None = None, notes: str | None = None,
                         snapshot: dict | None = None) -> None:
        target = db or self._db()
        notes_root = self._notes() if notes is None else notes
        if (snapshot is not None
                and not self._confirmed_target_matches(notes_root, snapshot, "提交")):
            return
        head = str((snapshot or self.probe).get("head") or "")
        message = f"引用库：手动提交（版本与同步页；sha {GIT.short_sha(head, 8)} 起）"
        await self._perform(
            "提交", lambda: GIT.commit_db(target, message, expected_snapshot=snapshot),
            commit_notice)

    async def _do_push(self, db: str, notes: str, snapshot: dict) -> None:
        if not self._confirmed_target_matches(notes, snapshot, "推送"):
            return
        await self._perform("推送", lambda: GIT.push(db, notes_repo=notes, expected=snapshot),
                            push_notice)

    async def _do_rollback(self, db: str | None = None, notes: str | None = None,
                           snapshot: dict | None = None) -> None:
        target = db or self._db()
        notes_root = self._notes() if notes is None else notes
        if (snapshot is not None
                and not self._confirmed_target_matches(notes_root, snapshot, "回退")):
            return
        await self._perform(
            "回退", lambda: GIT.rollback(target, notes_repo=notes_root,
                                         expected_snapshot=snapshot), rollback_notice)
