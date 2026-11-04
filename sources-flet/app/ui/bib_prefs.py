"""引用库 · 设置（批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/settings/spec.md`。本页不写库内容；
单列 720、四组（01 库 / 02 核实 / 03 Git / 04 关于），只有「保存设置」是主色，
提交/推送是次要动作，回退单独放在页尾危险区（56 高，生成新提交，不 push）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "settings"
TITLE = "设置"
ROLE = "readonly"

COLUMN_W = 720
VERIFY_KEYS = ("bibdb.verify_max", "bibdb.verify_plan",
               "bibdb.verify_all_refs", "bibdb.lookup_enabled")
SWITCHES = (("bibdb.verify_plan", "先算账（不花钱）"),
            ("bibdb.verify_all_refs", "允许全量核实"),
            ("bibdb.lookup_enabled", "先查引用库"))

# 每个开关的真实语义（键名来自 settings_store；说明来自引擎 --help / bibdb 文档）。
SWITCH_HINTS = {
    "bibdb.verify_plan": "只算范围与费用，不检索、不调 AI。",
    "bibdb.verify_all_refs": "越过 120 条上限；可能产生费用。",
    "bibdb.lookup_enabled": "命中本地已核实库就跳过检索与费用。",
}


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _text(value) -> str:
    return "" if value is None else str(value)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _count(value, *keys) -> int | None:
    """统计值可能是 int，也可能是 {records,total} 这类字典（引擎 stats 口径）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in (*keys, "total", "records", "count"):
            item = value.get(key)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return int(item)
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _size_text(size) -> str:
    try:
        number = float(size)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return ""


class BibPrefsView:
    """设置：单列 720 / 四组 / 保存 dirty 才启用 / 危险区在页尾。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.state = "loading"
        self.error = ""
        self.stderr = ""
        self.busy = False
        self.stage = ""
        self.stats: dict = {}
        self.git: dict = {}
        self.loaded_head = ""
        self.stale = False
        self.verify_max = ""
        self.switch_values = {key: False for key, _label in SWITCHES}
        self.saved_max = ""
        self.saved_switches = dict(self.switch_values)
        self.receipt = ""
        self.receipt_tone = "success"
        self.verify_error = ""
        self._root: ft.Container | None = None

    # ── 宿主小工具 ───────────────────────────────────────────────────
    def _flet(self):
        return getattr(self.page, "page", None)

    def _store(self):
        store = getattr(self.page, "store", None)
        if store is None and self.app is not None:
            store = getattr(self.app, "store", None)
        return store

    def _store_get(self, key, default=None):
        store = self._store()
        if store is None or not hasattr(store, "get"):
            return default
        try:
            return store.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _store_set(self, key, value) -> bool:
        store = self._store()
        if store is None or not hasattr(store, "set"):
            return False
        try:
            store.set(key, value)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _notes(self) -> str:
        return str(getattr(self.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = getattr(self.page, "stats_data", None)
        db = stats.get("db") if isinstance(stats, dict) else ""
        if not db:
            try:
                db = GIT.default_db(self._notes())
            except Exception:  # noqa: BLE001
                db = ""
        return str(db or "")

    def _task(self, fn) -> None:
        runner = getattr(self.page, "run_task_ui", None)
        if callable(runner):
            runner(fn)
            return
        page = self._flet()
        if page is not None and hasattr(page, "run_task"):
            page.run_task(fn)
            return
        asyncio.ensure_future(fn())

    def _notify(self, message: str) -> None:
        notify = getattr(self.page, "notify", None)
        if callable(notify):
            notify(message)
            return
        toast = getattr(self.app, "toast", None)
        if callable(toast):
            toast(message)

    def _mark(self, event: str, **fields) -> None:
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            try:
                mark(event, **fields)
            except Exception:  # noqa: BLE001
                pass

    def _render(self) -> None:
        if self._root is not None:
            self._root.content = self._body()
            replant = getattr(self.page, "_replant_nav", None)
            if callable(replant):
                replant()
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001
                pass

    async def _call(self, call: EB.EngineCall) -> dict:
        try:
            return await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:
            return {"ok": False, "json": {}, "error": str(exc), "code": None}

    # ── 宿主接口 ─────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        self._root = ft.Container(content=self._body(), expand=True)
        return self._root

    def activate(self) -> None:
        if not self.stats and not self.busy:
            self._task(self._load)
        else:
            self._load_form()

    def refresh_running(self) -> None:
        return

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    # ── 读取 ─────────────────────────────────────────────────────────
    def _load_form(self) -> None:
        raw = self._store_get("bibdb.verify_max", "")
        self.verify_max = "" if raw in (None, "") else str(raw)
        if self.verify_max in ("0", "0.0"):
            self.verify_max = "0"
        for key, _label in SWITCHES:
            default = key == "bibdb.lookup_enabled"
            self.switch_values[key] = bool(self._store_get(key, default))
        self.saved_max = self.verify_max
        self.saved_switches = dict(self.switch_values)

    async def _load(self) -> None:
        self.busy = True
        self.state = "loading"
        self.error = ""
        self._render()
        stats_res, status = await asyncio.gather(
            self._call(EB.bibdb_stats_call()),
            asyncio.to_thread(GIT.status, self._db(), notes_repo=self._notes()))
        self.busy = False
        if not isinstance(status, dict):
            status = {"ok": False, "error": "status 返回异常"}
        self.git = status
        self.loaded_head = _text(status.get("head"))
        if stats_res.get("ok"):
            self.stats = stats_res.get("json") or {}
        else:
            self.stats = dict(getattr(self.page, "stats_data", {}) or {})
            if not self.stats:
                self.stats = {"db": self._db()}
            self.error = self._error_text(stats_res)
            self.stderr = _text(stats_res.get("stderr"))
        self.state = "ready"
        self._load_form()
        self._mark("bib_prefs_load", ok=bool(stats_res.get("ok")),
                   git=bool(status.get("ok")))
        self._render()

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return _text(value).strip().splitlines()[0][:300]
        return "引擎没有返回原因。"

    # ── 01 库 ────────────────────────────────────────────────────────
    def _db_stats(self) -> tuple[str, str, str, str]:
        stats = self.stats or {}
        path = _text(stats.get("path") or stats.get("db") or self._db())
        records = _count(stats.get("records")) if stats.get("records") is not None \
            else _count(stats.get("total"))
        aliases = _count(stats.get("aliases"), "total") \
            or _count(stats.get("alias_total")) or 0
        size = _text(stats.get("size_text")) \
            or _size_text(stats.get("bytes") or stats.get("size"))
        if not size:
            try:
                size = _size_text(Path(path).stat().st_size) if path else ""
            except OSError:
                size = ""
        exists = bool(stats.get("exists")) if "exists" in stats \
            else (Path(path).exists() if path else False)
        count_text = (f"{records:,} 条 · 别名合计 {aliases:,} 条"
                      if records is not None else "—")
        file_text = f"存在 · {size}" if exists and size else ("存在" if exists else "—")
        commits = (self.git or {}).get("commits") or []
        if commits and isinstance(commits[0], dict):
            first = commits[0]
            when = _text(first.get("date") or first.get("time") or "")
            subject = _text(first.get("subject") or "")
            last = " · ".join(part for part in (
                _sha8(_text(first.get("sha"))), when, subject) if part) or "—"
        else:
            last = "—"
        return path, count_text, file_text, last

    def _row(self, label: str, value: str, *, mono=False,
             actions: list[ft.Control] | None = None) -> ft.Control:
        t = self.t
        p = t.palette
        shown = value or "—"
        controls: list[ft.Control] = [
            ft.Container(width=t.px(132), content=C.text(label, t, "small",
                                                         p.text_faint, max_lines=1)),
            ft.Container(expand=True, content=C.text(
                shown, t, "mono" if mono else "small",
                p.text if mono else p.text_muted, max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                # 长路径 / 最后提交 subject 被 32 高行截断时，悬停仍能看全。
                tooltip=shown)),
        ]
        if actions:
            controls.append(ft.Row(spacing=t.sp("half"), controls=actions))
        return ft.Container(height=t.px(32), padding=ft.Padding(
            left=t.px(4), top=0, right=t.px(4), bottom=0),
            content=ft.Row(spacing=t.sp("1"),
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=controls))

    def _copy(self, value: str) -> None:
        page = self._flet()
        if page is None or not value:
            self._notify("没有可复制的路径")
            return
        async def _run():
            try:
                await page.set_clipboard(value)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                try:
                    page.set_clipboard(value)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
        try:
            page.run_task(_run)
        except Exception:  # noqa: BLE001
            pass
        self._notify("已复制路径")

    def _reveal(self, path: str) -> None:
        open_path = getattr(self.app, "open_path", None)
        if callable(open_path) and path:
            try:
                open_path(str(Path(path).parent))
                return
            except Exception:  # noqa: BLE001
                pass
        self._notify("无法打开访达")

    def _group_db(self) -> ft.Control:
        t = self.t
        path, count_text, file_text, last = self._db_stats()
        exists = bool(path) and Path(path).exists()
        rows: list[ft.Control] = [
            self._row("库文件", path or "—", mono=True, actions=[
                C.text_button("复制路径", lambda e: self._copy(path), t),
                C.text_button("在访达中显示", lambda e: self._reveal(path), t),
            ]),
            self._row("条目 / 别名", count_text),
            self._row("文件", file_text),
            self._row("最后提交", last),
        ]
        if not exists:
            rows.append(C.inline_note(
                "库文件不存在；先导入或检查路径。", t, tone="warn",
                action=C.text_button("去入藏", lambda e: self._goto("import"), t)))
        return ft.Column(spacing=0, controls=[
            C.section_label("01 库", t), C.hairline(t), *rows])

    # ── 02 核实 ──────────────────────────────────────────────────────
    def _group_verify(self) -> ft.Control:
        t = self.t
        field = C.floating_field(
            t, "核实上限", value=self.verify_max, width=t.px(200),
            hint="留空 120 条；0 = 不限",
            error=self.verify_error or None,
            on_change=self._on_verify_change, on_submit=lambda e: self._save())
        switches = [self._switch_row(key, label) for key, label in SWITCHES]
        receipt = None
        if self.receipt:
            receipt = C.inline_note(self.receipt, t,
                                    tone="success" if self.receipt_tone == "success"
                                    else "warn")
        save = C.primary_button("保存设置", lambda e: self._save(), t,
                                disabled=not self._dirty())
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("02 核实", t), C.hairline(t),
            ft.Row(spacing=t.sp("2"), controls=[field, save]),
            *switches, *( [receipt] if receipt is not None else []),
        ])

    def _switch_row(self, key: str, label: str) -> ft.Control:
        """页内 Row/Text 自绘标签：C.switch 的 label 只在 labels=True 时才渲染。

        左侧两行（名称 + 一句人话说明）保证开关在 1160/1000 宽都有可见标签；
        不传 label 给 C.switch，避免依赖 components.py 的 label 行为（写域禁止改
        那里）。tooltip 暴露真实 settings 键，便于排障。
        """
        t = self.t
        p = t.palette
        switch = C.switch(t, value=bool(self.switch_values.get(key)),
                          on_change=lambda e, k=key: self._on_switch(k, e))
        switch.tooltip = key
        row = ft.Container(
            height=t.px(44),
            padding=ft.Padding(left=t.px(4), top=0, right=t.px(4), bottom=0),
            content=ft.Row(spacing=t.sp("2"),
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Column(spacing=0, expand=True, controls=[
                                   C.text(label, t, role="body-strong", color=p.text,
                                          max_lines=1),
                                   C.text(SWITCH_HINTS.get(key, ""), t, "caption",
                                          p.text_muted, max_lines=2),
                               ]),
                               switch,
                           ]))
        row._bib_switch_row = key
        return row

    def _on_verify_change(self, e) -> None:
        self.verify_max = (e.control.value or "").strip()
        self.verify_error = self._validate(self.verify_max)
        self._render()

    def _validate(self, value: str) -> str:
        if value == "":
            return ""
        try:
            number = int(value)
        except ValueError:
            return "请输入 0–10000 的整数"
        if number < 0 or number > 10000:
            return "请输入 0–10000 的整数"
        return ""

    def _on_switch(self, key: str, e) -> None:
        value = bool(e.control.value)
        if key == "bibdb.verify_all_refs" and value:
            dialog = S.danger_confirm(
                self._flet(), self.t, title="允许全量核实？",
                message="会为未被正文引用的条目付费；按次计费。",
                impact="按次计费 · 可随时关掉", confirm_label="仍然开启",
                on_confirm=lambda e: self._force_switch(key, True))
            if dialog is not None:
                self.switch_values[key] = False
                self._render()
                return
        self.switch_values[key] = value
        self._render()

    def _force_switch(self, key: str, value: bool) -> None:
        self.switch_values[key] = value
        self._render()

    def _dirty(self) -> bool:
        if self._validate(self.verify_max):
            return False
        if self.verify_max != self.saved_max:
            return True
        return any(bool(self.switch_values.get(key)) != bool(self.saved_switches.get(key))
                   for key, _label in SWITCHES)

    def _save(self) -> None:
        if not self._dirty():
            self._notify("没有改动")
            return
        error = self._validate(self.verify_max)
        if error:
            self.verify_error = error
            self._render()
            return
        value = "" if self.verify_max == "" else int(self.verify_max)
        ok = True
        if value == "":
            ok = self._store_set("bibdb.verify_max", "") and ok
        else:
            ok = self._store_set("bibdb.verify_max", value) and ok
        for key, _label in SWITCHES:
            ok = self._store_set(key, bool(self.switch_values.get(key))) and ok
        self.saved_max = self.verify_max
        self.saved_switches = dict(self.switch_values)
        now = datetime.now().strftime("%H:%M")
        if ok:
            self.receipt = f"已保存 · {now} · settings.json"
            self.receipt_tone = "success"
            self._notify("设置已保存")
        else:
            self.receipt = "未保存 · 设置存储不可用"
            self.receipt_tone = "warn"
        self._mark("bib_prefs_save", ok=ok, value=self.verify_max)
        self._render()

    # ── 03 Git ───────────────────────────────────────────────────────
    def _git_ok(self) -> bool:
        return bool((self.git or {}).get("ok"))

    def _git_problem(self) -> str:
        git = self.git or {}
        if git.get("ok"):
            return ""
        if git.get("conflict"):
            return "工作区有未解决冲突"
        if git.get("merge"):
            return "仓库正在合并中"
        if git.get("rebase"):
            return "仓库正在变基"
        return _text(git.get("error")) or "不是 git 仓库"

    def _ahead(self) -> int:
        return _int((self.git or {}).get("ahead"))

    def _group_git(self) -> ft.Control:
        t = self.t
        git = self.git or {}
        ok = self._git_ok()
        problem = self._git_problem()
        head = _text(git.get("head"))
        branch = _text(git.get("branch"))
        dirty = bool(git.get("dirty_db") or git.get("dirty_other"))
        line = " · ".join(part for part in (
            f"{branch or '—'} · HEAD {_sha8(head)}",
            "工作区干净" if not dirty else "工作区有未提交改动") if part)
        actions = ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("提交…", self._commit_click, t, disabled=not ok),
            C.secondary_button("推送…", self._push_click, t,
                               disabled=not ok or self._ahead() <= 0),
        ])
        hint = "" if ok else problem
        controls = [
            C.section_label("03 Git", t, trailing=ft.Row(
                spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    C.text("只提交本地", t, "caption", t.palette.text_muted),
                    C.badge("git 可用" if ok else "不可用",
                            t, tone="success" if ok else "warn"),
                ])),
            C.hairline(t),
            self._row("仓库", line),
            ft.Container(height=t.px(12)),
            actions,
        ]
        if hint:
            controls.append(C.text(hint, t, "caption", t.palette.text_muted))
        controls.append(ft.Container(height=t.px(12)))
        controls.append(self._danger_zone(ok, head))
        return ft.Column(spacing=t.sp("half"), controls=controls)

    def _danger_zone(self, ok: bool, head: str) -> ft.Control:
        t = self.t
        p = t.palette
        live = f"HEAD {_sha8(head)} · 与页面载入时一致" if head == self.loaded_head \
            else "HEAD 已变化 · 重新读取后再回退"
        return ft.Container(
            height=t.px(56), bgcolor=p.danger_soft,
            border=C.box(t, p.error), border_radius=t.radius("control"),
            padding=ft.Padding(left=t.px(12), top=t.px(6), right=t.px(12), bottom=t.px(6)),
            content=ft.Row(spacing=t.sp("1"),
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                ft.Column(spacing=0, expand=True, controls=[
                    C.text("回退 = 新提交（不推送），只动 bibliography.jsonl。",
                           t, "caption", p.text, max_lines=2),
                    C.text(live, t, "caption",
                           p.success if head == self.loaded_head else p.error),
                ]),
                C.danger_button("回退…", self._rollback_click, t, disabled=not ok),
            ]))

    def _commit_message(self) -> str:
        head = _sha8(_text((self.git or {}).get("head")))
        return f"只提交 bibliography.jsonl · 库 sha {head} · 不推送。"

    def _commit_click(self, e=None) -> None:
        if not self._git_ok():
            self._notify(f"不可用：{self._git_problem()}")
            return
        C.confirm(
            self._flet(), self.t, title="提交引用库？",
            message=self._commit_message(), confirm_label="提交",
            on_confirm=lambda e: self._task(lambda: self._git_write("commit")))

    def _push_click(self, e=None) -> None:
        if not self._git_ok():
            self._notify(f"不可用：{self._git_problem()}")
            return
        if self._ahead() <= 0:
            self._notify("没有待推送的提交")
            return
        ahead = self._ahead()
        C.confirm(
            self._flet(), self.t, title="推送到 origin/main？",
            message=(f"推送 {ahead} 条提交到 origin/main · 库 sha "
                     f"{_sha8(_text((self.git or {}).get('head')))}。"),
            confirm_label="推送", on_confirm=lambda e: self._task(
                lambda: self._git_write("push")))

    async def _git_write(self, action: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.state = "running"
        self.stage = "运行中"
        self._render()
        db = self._db()
        try:
            if action == "commit":
                result = await asyncio.to_thread(
                    GIT.commit_db, db, "引用库：手动提交")
            else:
                result = await asyncio.to_thread(GIT.push, db, notes_repo=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        if isinstance(result, dict) and result.get("ok"):
            self.state = "ready"
            self.receipt = (f"已提交 {_sha8(_text(result.get('sha')))} · 未推送"
                            if action == "commit" else "已推送")
            self.receipt_tone = "success"
            self._notify(self.receipt)
            self._task(self._load)
        else:
            error = _text((result or {}).get("error")).splitlines()
            self.state = "ready"
            self.receipt = (f"{'提交' if action == 'commit' else '推送'}失败："
                            f"{error[0][:120] if error else '未知错误'}")
            self.receipt_tone = "warn"
            self._notify(self.receipt)
        self._render()

    async def _rollback_click(self, e=None) -> None:
        if self.busy or not self._git_ok():
            return
        status = await asyncio.to_thread(GIT.status, self._db(),
                                         notes_repo=self._notes())
        head = _text((status or {}).get("head"))
        if head and self.loaded_head and head != self.loaded_head:
            self.stale = True
            self.git = status if isinstance(status, dict) else self.git
            self._notify("HEAD 已变化，请先重新读取")
            self._render()
            return
        dialog = S.danger_confirm(
            self._flet(), self.t, title="回退到上一个提交？",
            message="恢复到上一个提交的版本，只动 bibliography.jsonl。",
            impact="1 个文件 · 1 条新提交",
            confirm_label="回退并提交",
            on_confirm=lambda e: self._task(self._rollback))
        if dialog is not None:
            self._render()

    async def _rollback(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.state = "running"
        self.stage = "运行中"
        self._render()
        try:
            result = await asyncio.to_thread(GIT.rollback, self._db(),
                                             notes_repo=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        self.state = "ready"
        if isinstance(result, dict) and result.get("ok"):
            self.receipt = f"已回退 · 新提交 {_sha8(_text(result.get('sha')))} · 未推送"
            self.receipt_tone = "success"
            self._notify(self.receipt)
            self._task(self._load)
        else:
            error = _text((result or {}).get("error")).splitlines()
            self.receipt = f"回退失败：{error[0][:120] if error else '未知错误'}"
            self.receipt_tone = "warn"
            self._notify(self.receipt)
        self._render()

    # ── 04 关于 ──────────────────────────────────────────────────────
    def _footer(self) -> ft.Control:
        t = self.t
        engine = _text((self.stats or {}).get("engine_version")) or "1.6.1"
        return ft.Container(content=C.text(
            f"推送/回退只在此页 · v1.6.1 · settings.json", t, "caption", t.palette.text_faint,
            max_lines=1))

    # ── 渲染 ─────────────────────────────────────────────────────────
    def _impact_bar(self) -> ft.Control:
        """常驻影响条：不进滚动区，底部留白，两行短句不省略。

        复评里这条粉色后果条只露出上沿——原因是滚动 Column 没有高度约束，被
        page_shell 0 底距裁掉。现在它固定在滚动区下方，滚动内容末还留 12px。
        """
        t = self.t
        p = t.palette
        bar = ft.Container(
            height=t.px(56), bgcolor=p.surface_2,
            border=C.box(t, p.border), border_radius=t.radius("control"),
            padding=ft.Padding(left=t.px(12), top=t.px(6),
                               right=t.px(12), bottom=t.px(6)),
            content=ft.Row(spacing=t.sp("1"),
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(C.ic("INFO_OUTLINE", "INFO"),
                                       size=t.px(16), color=p.text_muted),
                               ft.Column(spacing=0, expand=True,
                                         alignment=ft.MainAxisAlignment.CENTER,
                                         controls=[
                                             C.text("1 个文件 · 1 条新提交 · 不推送",
                                                    t, "caption", p.text, max_lines=2),
                                             C.text("只含 bibliography.jsonl；"
                                                    "推送 / 回退只在设置页",
                                                    t, "caption", p.text_muted,
                                                    max_lines=2),
                                         ]),
                           ]))
        bar._bib_impact_bar = True
        return bar

    def _body(self) -> ft.Control:
        t = self.t
        meta = "保存只改设置，不动库"
        scroll = ft.Column(
            spacing=0, expand=True, scroll=S.scrollbar(), controls=[
                self._state_line(),
                self._group_db(),
                ft.Container(height=t.px(12)),
                self._group_verify(),
                ft.Container(height=t.px(12)),
                self._group_git(),
                ft.Container(height=t.px(12)),
                self._footer(),
            ])
        bottom_pad = ft.Container(height=t.px(12))
        bottom_pad._bib_bottom_pad = True
        scroll.controls.append(bottom_pad)
        scroll._bib_scroll_body = True
        body = ft.Container(
            width=t.px(COLUMN_W), expand=True,
            content=ft.Column(spacing=t.sp("1"), expand=True, controls=[
                scroll,
                self._impact_bar(),
                ft.Container(height=t.px(8)),
            ]))
        return S.page_shell(
            t, title=TITLE, meta=meta, role_chip=S.role_chip(t, "readonly"),
            body=body,
            dock=ft.Container(height=S.DOCK_H,
                              content=C.text(self.stderr or "", t, role="log",
                                             color=t.palette.log_text, max_lines=1)
                              if self.stderr else None))

    def _state_line(self) -> ft.Control:
        t = self.t
        if self.state == "loading":
            return S.state_banner(t, "loading", message="运行中")
        if self.state == "running":
            return S.state_banner(t, "running", stage=self.stage or "运行中",
                                  page=self._flet())
        if self.error:
            return S.state_banner(t, "failure", message=self.error,
                                  stderr=self.stderr or None, page=self._flet())
        if self.stale:
            return S.stale_notice(t, title="HEAD 已变化",
                                  detail="载入后 HEAD 变了，请重新读取。",
                                  on_repreview=lambda e: self._task(self._load))
        return ft.Container(height=t.px(4))

    def _goto(self, view: str) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav(view)
            return
        self._store_set("bib_view", view)
        self._notify("暂不可跳转")
