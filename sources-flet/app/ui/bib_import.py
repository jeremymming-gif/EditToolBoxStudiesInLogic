"""引用库 · 入藏（写库视图；批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/import/spec.md`。四步固定：
预览（dry-run，不写盘）→ 页内二次确认（影响 + 库 sha）→ 原子写
（`--expect-db-sha256` + `--backup`）→ 本地提交（不 push）→ 可回退本次提交。

纪律：只走 `EB.*` 拼命令、`EB.read_json_command` 执行；本模块不碰 subprocess，
不写库（写库是引擎的事），不 reset / 不 force push。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui.bib_write import _summary_of
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "import"
TITLE = "入藏"
ROLE = "write"

#: 入藏来源（段控 28 高）
MODES = (("bib", "单个 .bib"), ("paste", "粘贴文本"))
#: 预览结果筛选（chip 28）
FILTERS = (("all", "全部"), ("new", "新增"), ("updated", "更新"),
           ("conflict", "冲突"), ("skipped", "跳过"))
_STATE_KIND = {"ready": None, "empty": "empty", "loading": "loading",
               "running": "running", "failure": "failure", "stale": "stale",
               "cancelled": "cancelled", "success": "success"}
_STATUS_LABEL = {"new": ("新增", "success"), "add": ("新增", "success"),
                 "added": ("新增", "success"), "updated": ("更新", "accent"),
                 "update": ("更新", "accent"), "merged": ("合并", "accent"),
                 "conflict": ("冲突", "warn"), "conflicts": ("冲突", "warn"),
                 "skipped": ("跳过", "muted"), "skip": ("跳过", "muted")}


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _commit_notice(base: str, commit: dict) -> str:
    """写库成功的提示：只有真的生成了提交才说「已提交」，否则显式报版本层原因。"""
    commit = commit or {}
    if commit.get("committed") and commit.get("sha"):
        return f"{base}（已提交 {_sha8(str(commit['sha']))} · 未推送）"
    reason = str(commit.get("error") or commit.get("reason") or "未生成提交").strip()
    return f"{base}；版本层提示：{reason}"


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class _ImportInputs:
    revision: int
    mode: str
    source: str
    issue: str
    slug: str
    verified: bool
    force: bool
    db: str
    notes: str


def _db_fingerprint(db: str) -> str | None:
    if not db:
        return ""
    try:
        with open(db, "rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return ""
    except OSError:
        return None


class BibImportView:
    """入藏页：左参数 356 / 右预览 520（1000 宽收单列）。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.mode = "bib"
        self.path = self._store_get("recent.bib_import", "") or ""
        self.issue = ""
        self.slug = ""
        self.verified = False
        self.force = False
        self.more_open = False
        self.paste_text = ""
        self.params_open = False
        self.payload: dict | None = None
        self.expect_sha = ""
        self._input_revision = 0
        self._preview_inputs: _ImportInputs | None = None
        self.state = "empty"
        self.filter = "all"
        self.error = ""
        self.stderr = ""
        self.elapsed = 0.0
        self.result: dict | None = None
        self.commit: dict | None = None
        self.no_write_note = ""
        self.confirm_layer = False
        self.busy = False
        self.dirty_reason = ""
        self.shown = 100
        self.expanded_change = ""
        self._root: ft.Container | None = None
        self._layout: ft.ListView | ft.Row | None = None
        self._confirmation_slot: ft.Container | None = None
        self._confirm_holder: ft.Container | None = None
        self._preview_button: ft.Control | None = None
        # N01（DESIGN §11）：浏览入口必须接回真实文件选择服务。这里保留本次渲染
        # 创建的来源输入框引用，`app.browse_field` 直接往里写值（旧 `page.file_picker`
        # 在 Flet 1.0.5 上根本不存在，是永久失效的分支）。
        self._source_ref: list = []
        self._picking = False

    # ── 宿主 / 状态小工具 ──────────────────────────────────────────────
    def _flet(self):
        return getattr(self.page, "page", None)

    def _store(self):
        store = getattr(self.page, "store", None)
        if store is None and self.app is not None:
            store = getattr(self.app, "store", None)
        return store

    def _store_get(self, key: str, default=None):
        store = None
        try:
            store = self._store()
        except Exception:  # noqa: BLE001 - 宿主未接线时保持可用
            store = None
        if store is None or not hasattr(store, "get"):
            return default
        try:
            return store.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _notes(self) -> str:
        return str(getattr(self.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = getattr(self.page, "stats_data", None)
        if isinstance(stats, dict):
            # 引擎 stats --json 的真实路径字段是 `path`。保留 `db` 兼容
            # 宿主测试/旧状态，避免预览快照与引擎回执使用不同库路径。
            db = stats.get("db") or stats.get("path") or ""
        else:
            db = ""
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
            except Exception:  # noqa: BLE001 - 遥测失败不影响写库
                pass

    def _render(self) -> None:
        if self._root is not None:
            self._root.content = self._body()
            replant = getattr(self.page, "_replant_nav", None)
            if callable(replant):
                replant()
        self._update_page()

    def _update_page(self) -> None:
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001 - 控件未挂载时忽略
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
        """宿主切到本页时调用；入藏不需要预取，只在首次把路径回填进表单。"""
        if self.path:
            self._store_set("recent.bib_import", self.path)

    def refresh_running(self) -> None:
        """宿主重绘时调用：不主动重启任何引擎任务。"""

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    def _store_set(self, key: str, value) -> None:
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

    # ── 表单状态 ─────────────────────────────────────────────────────
    def _source(self) -> str:
        if self.mode == "paste":
            return self.paste_text if self.paste_text.strip() else ""
        return self.path if self.path.strip() else ""

    def _inputs(self) -> _ImportInputs:
        db = self._db()
        return _ImportInputs(self._input_revision, self.mode, self._source(),
                             self.issue, self.slug, self.verified, self.force,
                             os.path.abspath(db) if db else "", self._notes())

    def _can_preview(self) -> bool:
        return bool(self._source()) and not self.busy

    def _is_stale(self) -> bool:
        return bool(self.dirty_reason) and self.payload is not None

    def _can_confirm(self) -> bool:
        return bool(self.payload) and bool(self.expect_sha) and not self._is_stale() \
            and not self.busy and self.state not in ("loading", "running", "failure",
                                                     "stale", "success", "nothing")

    def _invalidate(self, reason: str) -> None:
        self._input_revision += 1
        refresh_gate = self.confirm_layer
        if self.payload is not None and not self.dirty_reason:
            self.dirty_reason = reason
            refresh_gate = True
        self.confirm_layer = False
        if refresh_gate and self._confirm_holder is not None:
            self._confirm_holder.content = self._bottom()
            self._refresh_confirmation()

    def _set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self._invalidate("来源已切换，预览已失效")
        self.state = "empty" if self.payload is None else self.state
        self._render()

    def _on_path(self, e) -> None:
        self.path = e.control.value or ""
        self._store_set("recent.bib_import", self.path)
        self._invalidate("来源已修改，请重新预览")
        self._refresh_preview_button()

    def _on_issue(self, e) -> None:
        self.issue = e.control.value or ""
        self._invalidate("期号已修改，请重新预览")

    def _on_slug(self, e) -> None:
        self.slug = e.control.value or ""
        self._invalidate("slug 已修改，请重新预览")

    def _on_paste(self, e) -> None:
        self.paste_text = e.control.value or ""
        self._invalidate("粘贴内容已修改，请重新预览")
        self._refresh_preview_button()

    def _refresh_preview_button(self) -> None:
        button = self._preview_button
        if button is None:
            return
        button.disabled = not self._can_preview()
        update = getattr(button, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001 - 控件尚未挂载时忽略
                page = self._flet()
                page_update = getattr(page, "update", None)
                if callable(page_update):
                    try:
                        page_update()
                    except Exception:  # noqa: BLE001
                        pass

    def _toggle(self, name: str, e=None) -> None:
        value = bool(getattr(e.control, "value", False))
        setattr(self, name, value)
        self._invalidate("选项已修改，请重新预览")

    def _toggle_more(self, e=None) -> None:
        self.more_open = not self.more_open
        self._refresh_params()

    def _clear(self, e=None) -> None:
        self._input_revision += 1
        self._preview_inputs = None
        self.path = ""
        self.paste_text = ""
        self.issue = ""
        self.slug = ""
        self.verified = False
        self.force = False
        self.payload = None
        self.expect_sha = ""
        self.state = "empty"
        self.dirty_reason = ""
        self.confirm_layer = False
        self.filter = "all"
        self.result = None
        self.commit = None
        self._notify("表单已清空")

    # ── 引擎命令 ─────────────────────────────────────────────────────
    def _build_call(self, inputs: _ImportInputs, *, dry_run: bool,
                    expect_sha: str = "") -> EB.EngineCall:
        bib = self._paste_file(inputs.source) if inputs.mode == "paste" else inputs.source
        call = EB.bibdb_import_call(
            bib=bib, issue=inputs.issue, slug=inputs.slug, dry_run=dry_run,
            force=inputs.force, verified=inputs.verified, unverified=not inputs.verified,
            expect_db_sha256=expect_sha)
        # 预览与写入都显式绑定页面当前统计到的库路径。只依赖
        # SLCN_NOTES 推导默认库会让隔离运行中的预览回执与页面库校验失配。
        EB._append_opt(call.argv, "--db", inputs.db)
        return call

    def _paste_file(self, text: str) -> str:
        """粘贴文本落一个临时 .bib（temp dir，不碰笔记仓）。"""
        folder = Path(tempfile.mkdtemp(prefix="bib-import-"))
        target = folder / "pasted.bib"
        target.write_text(text, encoding="utf-8")
        return str(target)

    def _discard_preview(self, reason: str) -> None:
        self._preview_inputs = None
        self.confirm_layer = False
        self.state = "stale" if self.payload is not None else "empty"
        self.dirty_reason = self.dirty_reason or reason
        if self.payload is None and self._source():
            self._notify(reason)
        self._render()

    async def _preview(self) -> None:
        if self.busy:
            return
        if not self._source():
            self._notify("先选择要导入的来源")
            return
        inputs = self._inputs()
        self.busy = True
        self.state = "loading"
        self.error = ""
        self.stderr = ""
        self.confirm_layer = False
        self._render()
        started = time.time()
        res = None
        end_sha = None
        try:
            start_sha = await asyncio.to_thread(_db_fingerprint, inputs.db)
            if inputs == self._inputs():
                res = await self._call(self._build_call(inputs, dry_run=True))
                if res.get("ok"):
                    end_sha = await asyncio.to_thread(_db_fingerprint, inputs.db)
        except (OSError, RuntimeError) as exc:
            res = {"ok": False, "error": str(exc)}
        finally:
            self.busy = False
        if res is None or inputs != self._inputs():
            self._discard_preview("来源、参数或库路径已变化，请重新预览")
            return
        self.elapsed = max(0.0, time.time() - started)
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_import_preview", ok=False, code=res.get("code"),
                       error=self.error[:200])
            self._render()
            return
        payload = res.get("json") or {}
        # 旧版/测试替身可能不回传 sha，但本地前后指纹仍能证明库未变。
        # 生产引擎回传 sha 时继续以响应值为准，来源与库绑定不放松。
        preview_sha = str(payload.get("db_sha256_before") or start_sha or "")
        reply_db = str(payload.get("db") or "")
        reply_db_matches = (not reply_db or os.path.normcase(os.path.abspath(reply_db))
                            == os.path.normcase(inputs.db))
        if preview_sha and (start_sha is None or end_sha != start_sha
                            or payload.get("db_sha256_after", preview_sha) != preview_sha
                            or not reply_db_matches):
            self._discard_preview("库路径或指纹已变化，请重新预览")
            return
        if not preview_sha:
            # 没有引擎回执且本地也无法取得指纹时，不能把结果标成可写入；
            # 否则确认条会展示一份没有库绑定的预览。保留来源输入，要求重新检查路径。
            self.payload = None
            self.expect_sha = ""
            self.state = "empty"
            self.dirty_reason = "无法确认库指纹，请检查库路径后重新预览"
            self._notify(self.dirty_reason)
            self._render()
            return
        summary = _summary_of(payload) or payload
        if _int(summary.get("entries")) == 0:
            self.state = "failure"
            self.error = "没有解析到 BibTeX 条目；检查来源内容后重试。"
            self._mark("bib_import_preview", ok=False, code=res.get("code"),
                       entries=0, error=self.error)
            self._render()
            return
        self.payload = payload
        self.expect_sha = preview_sha
        self._preview_inputs = inputs
        self.dirty_reason = ""
        self.state = "ready"
        self.shown = 100
        self.filter = "all"
        self._mark("bib_import_preview", ok=True, code=res.get("code"),
                   entries=_int(payload.get("entries")),
                   sha=_sha8(self.expect_sha))
        self._render()

    def _open_confirm(self, e=None) -> None:
        if not self._can_confirm():
            return
        self.confirm_layer = True
        self._refresh_confirmation()

    def _cancel_confirm(self, e=None) -> None:
        self.confirm_layer = False
        self._refresh_confirmation()

    def _refresh_confirmation(self) -> None:
        if self._confirmation_slot is not None:
            slot = self._confirmation_slot
            slot.content = self._confirm_layer() if self.confirm_layer else None
            slot.visible = self.confirm_layer
        self._update_page()

    async def _write(self) -> None:
        if self.busy or not self._can_confirm():
            return
        inputs = self._inputs()
        # 兼容旧的已挂载状态恢复。正常预览路径总会记录快照，恢复态没有快照
        # 时仍由引擎的 expect-db-sha256 闸保护写入。
        if self._preview_inputs is not None and inputs != self._preview_inputs:
            self._discard_preview("来源、参数或库路径已变化，请重新预览")
            return
        self.busy = True
        self.state = "running"
        self.confirm_layer = False
        self._render()
        try:
            res = await self._call(self._build_call(inputs, dry_run=False,
                                                    expect_sha=self.expect_sha))
        except (OSError, RuntimeError) as exc:
            res = {"ok": False, "error": str(exc)}
        code = res.get("code")
        if not res.get("ok"):
            if self._sha_mismatch(res):
                self.state = "stale"
                self.dirty_reason = "库指纹已变化，预览不再可信"
                self.error = self._error_text(res)
            else:
                self.state = "failure"
                self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self.busy = False
            self._mark("bib_import_write", ok=False, code=code,
                       error=self.error[:200])
            self._render()
            return
        payload = res.get("json") or {}
        self.result = payload
        self.payload = payload
        counts = self._counts(payload)
        added, updated, skipped = counts["new"], counts["updated"], counts["skipped"]
        db = inputs.db
        self.expect_sha = str(payload.get("db_sha256_after")
                               or payload.get("db_sha256_before")
                               or self.expect_sha)
        # G-6：引擎 `written=false` = 幂等跳过（来源已入藏过/内容相同），实际没有
        # 写库。这时不能进 success 样式，也不能说「已写入 0 条新增」。
        if payload.get("written") is False:
            detail_bits = []
            if skipped:
                detail_bits.append(f"跳过 {skipped} 条")
            if updated:
                detail_bits.append(f"已是库内内容 {updated} 条")
            suffix = f"（{' · '.join(detail_bits)}）" if detail_bits else ""
            self.no_write_note = ("没有需要写入的内容：该来源条目已入藏过或内容相同"
                                  f"{suffix}，库文件未改动")
            self.commit = {}
            self.state = "nothing"
            self.busy = False
            self._mark("bib_import_write", ok=True, code=code, added=0,
                       updated=0, written=False, skipped=skipped)
            self._notify(self.no_write_note)
            self._render()
            return
        message = f"引用库：入藏 {added} 条新增 · {updated} 条更新"
        commit: dict = {}
        if db:
            try:
                commit = await asyncio.to_thread(GIT.commit_db, db, message)
            except Exception as exc:  # noqa: BLE001 - 提交失败要让人看见
                commit = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.commit = commit if isinstance(commit, dict) else {}
        self.state = "success"
        self.busy = False
        self._mark("bib_import_write", ok=True, code=code, added=added,
                   updated=updated, written=True,
                   commit=_sha8(str((self.commit or {}).get("sha") or "")))
        self._notify(_commit_notice(f"已写入 {added} 条新增 · {updated} 条更新",
                                    self.commit))
        self._render()

    def _sha_mismatch(self, res: dict) -> bool:
        text = f"{res.get('error') or ''} {res.get('stderr') or ''}"
        return res.get("code") == 2 or "sha256" in text or "sha 不符" in text

    def _revert_confirm(self, e=None) -> None:
        commit = self.commit or {}
        if not (commit.get("ok") and commit.get("committed")):
            self._notify("没有可回退的已提交入藏")
            return
        impact = "影响 1 个文件（bib_db/bibliography.jsonl）· 1 条回退提交"
        dialog = S.danger_confirm(
            self._flet(), self.t,
            title="回退本次入藏提交？",
            message=("把 bibliography.jsonl 恢复到本次写入前的版本，再生成一条新提交；"
                     "其它文件不动。"),
            impact=impact, confirm_label="回退这次提交",
            on_confirm=lambda e: self._task(self._revert))
        if dialog is not None:
            self._render()

    async def _revert(self) -> None:
        db = self._db()
        if not db:
            self._notify("找不到库路径")
            return
        try:
            result = await asyncio.to_thread(GIT.rollback, db)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("ok") and result.get("committed"):
            self.state = "empty"
            self.payload = None
            self.expect_sha = ""
            self.commit = None
            self._notify("已回退本次写入（生成了一条新提交，未推送）")
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else ""
            if isinstance(result, dict) and result.get("ok"):
                error = error or "未生成回退提交"
            self._notify(f"回退失败：{error or '未知错误'}")
        self._render()

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return str(value).strip().splitlines()[0][:300]
        return "引擎没有返回原因。"

    # ── 计数 / 行 ───────────────────────────────────────────────────
    def _changes(self, payload: dict | None = None) -> list[dict]:
        if payload is None:
            payload = self.payload or {}
        raw = payload.get("changes")
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            record = item.get("record") if isinstance(item.get("record"), dict) else {}
            fields = record.get("fields") or {}
            key = str(item.get("key") or record.get("key") or "")
            status = str(item.get("status") or item.get("action") or item.get("kind") or "")
            title = str(item.get("title") or fields.get("title") or item.get("message") or "")
            conflict = item.get("conflict") if isinstance(item.get("conflict"), dict) else {}
            field_diff = (item.get("field_diff") or item.get("field_diffs")
                          or item.get("diff") or conflict.get("field_diff"))
            out.append({
                "change_id": str(item.get("change_id") or ""),
                "line": _int(item.get("line")),
                "skip_code": str(item.get("skip_code") or ""),
                "key": key,
                "incoming_key": str(item.get("incoming_key") or ""),
                "status": status,
                "title": title,
                "author": str(item.get("author") or fields.get("author")
                               or fields.get("editor") or ""),
                "year": str(item.get("year") or fields.get("year") or ""),
                "identity": str(item.get("identity") or ""),
                "reason": str(item.get("reason") or ""),
                "verified": item.get("verified"),
                "conflict_fields": item.get("conflict_fields") or [],
                "field_diff": field_diff if isinstance(field_diff, dict) else {},
            })
        return out

    @staticmethod
    def _field_diff_values(row: dict) -> list[tuple[str, str, str]]:
        """Accept current and legacy engine diff shapes without inventing values."""
        diff = row.get("field_diff")
        if not isinstance(diff, dict):
            return []
        values = []
        for name, change in diff.items():
            if isinstance(change, dict):
                existing = change.get("existing", change.get("old", change.get("existing_value", "")))
                incoming = change.get("incoming", change.get("new", change.get("incoming_value", "")))
                if change.get("remove"):
                    incoming = "（新稿无此字段）"
            else:
                existing, incoming = "", change
            values.append((str(name), str(existing or "（空）"),
                           str(incoming or "（空）")))
        return values

    def _change_detail(self, row: dict) -> ft.Control:
        t = self.t
        p = t.palette
        details: list[ft.Control] = []
        key_line = " · ".join(part for part in (
            f"库键 {row['key']}" if row.get("key") else "",
            f"输入键 {row['incoming_key']}" if row.get("incoming_key") else "",
            f"第 {row['line']} 行" if row.get("line") else "",
            f"分类 {row['skip_code']}" if row.get("skip_code") else "",
            row.get("identity", "")) if part)
        if key_line:
            details.append(C.text(key_line, t, "caption", p.text_muted,
                                  font_family=t.mono_family, selectable=True))
        if row.get("reason"):
            details.append(C.text("原因：" + str(row["reason"]),
                                  t, "caption", p.warn, selectable=True))
        bibliographic = " · ".join(part for part in (
            row.get("author", ""), row.get("year", "")) if part)
        if bibliographic:
            details.append(C.text(bibliographic, t, "caption", p.text_muted))
        diffs = self._field_diff_values(row)
        conflict_fields = row.get("conflict_fields")
        if isinstance(conflict_fields, str):
            conflict_fields = [conflict_fields]
        if not diffs and isinstance(conflict_fields, (list, tuple)) and conflict_fields:
            details.append(C.text("差异字段：" + "、".join(map(str, conflict_fields)),
                                  t, "caption", p.warn))
            details.append(C.text("引擎未返回字段值；此处不推测库内值或输入值。",
                                  t, "caption", p.text_faint))
        elif diffs:
            details.append(ft.Row(spacing=0, controls=[
                ft.Container(expand=1, content=C.text("字段", t, "caption", p.text_faint)),
                ft.Container(expand=2, content=C.text("库内值", t, "caption", p.text_faint)),
                ft.Container(expand=2, content=C.text("本次输入", t, "caption", p.text_faint)),
            ]))
            for name, existing, incoming in diffs:
                details.append(ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.START,
                                      controls=[
                    ft.Container(expand=1, content=C.text(name, t, "caption", p.warn,
                                                          font_family=t.mono_family)),
                    ft.Container(expand=2, content=C.text(existing, t, "caption", p.text_muted,
                                                          selectable=True)),
                    ft.Container(expand=2, content=C.text(incoming, t, "caption", p.text,
                                                          selectable=True)),
                ]))
        elif _STATUS_LABEL.get(row.get("status"), ("", ""))[0] == "冲突":
            details.append(C.text("引擎未提供字段级差异；请在「待处理」中查看冲突记录。",
                                  t, "caption", p.warn))
        else:
            details.append(C.text("本条没有字段差异数据。", t, "caption", p.text_faint))
        return ft.Container(
            padding=ft.Padding(left=t.px(12), top=t.px(8), right=t.px(12), bottom=t.px(8)),
            bgcolor=p.surface_2,
            content=ft.Column(spacing=t.sp("half"), controls=details))

    def _counts(self, payload: dict | None = None) -> dict:
        # G-5：两种 payload 的计数都在嵌套层（import-bib 在 report，import-dir 在
        # result.total），读顶层会永远显示 0 新增 0 更新。口径复用 bib_write 的
        # _summary_of，保证预览、确认条、成功横幅三处一致。
        if payload is None:
            payload = self.payload or {}
        summary = _summary_of(payload) or payload
        changes = self._changes(payload)
        new = _int(summary.get("new"))
        updated = _int(summary.get("updated")) + _int(summary.get("merged"))
        conflicts = _int(summary.get("conflicts"))
        raw_skipped = summary.get("skipped")
        skipped = (sum(_int(value) for value in raw_skipped.values())
                   if isinstance(raw_skipped, dict) else _int(raw_skipped))
        if not (new or updated or conflicts or skipped) and changes:
            for row in changes:
                label, _tone = _STATUS_LABEL.get(row["status"], ("", "muted"))
                if label == "新增":
                    new += 1
                elif label in ("更新", "合并"):
                    updated += 1
                elif label == "冲突":
                    conflicts += 1
                elif label == "跳过":
                    skipped += 1
        return {"entries": _int(summary.get("entries")) or len(changes),
                "new": new, "updated": updated, "conflicts": conflicts,
                "skipped": skipped}

    def _rows(self, rows: list[dict] | None = None) -> list[dict]:
        if rows is None:
            rows = self._changes()
        if self.filter == "updated":
            # 更新筛选也包含只合并来源的记录，行上仍保留「合并」标签。
            return [row for row in rows if row["status"] in ("updated", "update", "merged")]
        if self.filter != "all":
            want = _STATUS_LABEL.get(self.filter, ("", ""))[0]
            rows = [row for row in rows if _STATUS_LABEL.get(row["status"], ("", ""))[0] == want]
        return rows

    def _consequence(self) -> str:
        counts = self._counts()
        parts = []
        if counts["new"]:
            parts.append(f"将写入 {counts['new']} 条新增")
        if counts["updated"]:
            parts.append(f"{counts['updated']} 条更新")
        if counts["conflicts"]:
            parts.append(f"{counts['conflicts']} 条冲突（进待处理，不覆盖）")
        if not parts:
            parts.append("预览没有可写入的变化")
        return " · ".join(parts)

    # ── 渲染：左参数 ─────────────────────────────────────────────────
    def _body(self) -> ft.Control:
        t = self.t
        width = int(getattr(self._flet(), "width", 1160) or 1160)
        narrow = width <= S.NARROW_W
        params = self._params(narrow=narrow)
        if narrow:
            # The narrow page has one bounded scroll owner.  Keeping the
            # parameters and preview in the same list lets the footer stay
            # fixed while the complete preview remains reachable.
            layout = ft.ListView(
                expand=True, spacing=t.sp("2"), padding=0,
                scroll=C.scrollbar(t),
                controls=[params, self._preview_col(narrow=True)],
            )
            layout._bib_narrow = True
            layout._bib_left = params
            layout._bib_preview = layout.controls[1]
        else:
            params = ft.ListView(
                expand=True, spacing=t.sp("2"), padding=0,
                scroll=C.scrollbar(t), controls=list(params.controls))
            layout = S.write_layout(t, params=params, preview=self._preview_col(),
                                    confirm=None, narrow=False)
        self._layout = layout
        # P1-7：确认条满宽常驻在页面底部，不再缩在右预览栏；窄屏同样通栏。
        self._confirm_holder = ft.Container(content=self._bottom())
        body = ft.Column(spacing=0, expand=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                         controls=[
                             ft.Container(content=layout, expand=True),
                             self._confirm_holder,
                         ])
        return S.page_shell(
            t, title=TITLE,
            meta="预览 → 确认 → 写入；只提交本地",
            role_chip=S.role_chip(t, "write"), body=body,
            notice=None, dock=self._dock())

    def _dock(self) -> ft.Control:
        t = self.t
        if not self.stderr and not self.error:
            return ft.Container(height=S.DOCK_H)
        text = (self.stderr or self.error).strip()
        return ft.Container(
            height=S.DOCK_H, bgcolor=t.palette.log_bg,
            padding=ft.Padding(left=t.px(12), top=t.px(6), right=t.px(12), bottom=t.px(6)),
            content=C.text(text.splitlines()[0][:400], t, role="log",
                           color=t.palette.log_text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))

    def _params(self, *, narrow: bool) -> ft.Control:
        t = self.t
        if narrow and not self.params_open:
            return ft.Container(
                padding=ft.Padding(left=0, top=t.px(4), right=0, bottom=t.px(4)),
                content=ft.Column(spacing=t.sp("half"), controls=[
                    C.text(f"来源：{dict(MODES)[self.mode]} · "
                           f"{self.path or ('粘贴文本 %d 行' % len(self.paste_text.splitlines())) or '未选择'}",
                           t, "small", t.palette.text_muted, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                    C.text_button("编辑参数…", lambda e: self._open_params(), t),
                ]))
        controls: list[ft.Control] = [self._segment()]
        if self.mode == "paste":
            # N03（DESIGN §11）：多行框不能用 `floating_field`——它把 height 钉成 48，
            # 6～10 行的框被压扁，右侧 28 高「粘贴」按钮看着像错位。改成
            # `text_field`（多行 height=None）+ `form_field`（标签上置），与
            # bib_write/normalize 的多行粘贴区同一套布局。
            paste_field = C.text_field(
                t, value=self.paste_text,
                hint_text="整段 BibTeX 条目；长文本可在框内滚动",
                mono=True,
                multiline=True, min_lines=6, max_lines=10,
                on_change=self._on_paste, expand=True)
            # 按钮固定在多行框右侧（顶部对齐），不嵌 suffix、不改多行框几何。
            body = ft.Row(
                spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[paste_field,
                          C.paste_button(t, self.page, paste_field,
                                         height=t.control_h())])
            controls.append(C.form_field(t, "粘贴 .bib 文本", body,
                                         clearable=False))
        else:
            field = C.floating_field(
                t, "来源",
                value=self.path,
                hint=".bib 文件路径",
                on_change=self._on_path, expand=True)
            # N01：登记本次渲染的来源框，供真实文件选择器回填。
            self._source_ref = [field]
            controls.append(ft.Container(
                height=t.px(48),
                content=ft.Row(spacing=t.sp("1"), controls=[
                    field,
                    C.secondary_button("浏览…", lambda e: self._browse(), t,
                                       icon="FOLDER_OPEN", height=t.px(48)),
                ])))
        if self.mode in ("bib", "paste"):
            controls.append(self._check("verified", "标为已核实",
                                        self.verified))
        more_label = "来源信息与选项 ▾" if self.more_open else "来源信息与选项 ▸"
        controls.append(C.text_button(more_label, self._toggle_more, t))
        if self.more_open:
            controls.append(C.text("来源追溯（可选）", t, "caption",
                                   t.palette.text_faint))
            controls.append(ft.Row(spacing=t.sp("1"), controls=[
                C.floating_field(t, "来源期号", value=self.issue,
                                 hint="如 2026年第1期", on_change=self._on_issue,
                                 width=t.px(174)),
                C.floating_field(t, "稿件目录", value=self.slug,
                                 hint="如 01 Lei_Wang", on_change=self._on_slug,
                                 tooltip="填写期目录下的稿件文件夹名；用于来源追溯，不影响条目匹配。",
                                 width=t.px(174)),
            ]))
            controls.append(self._check("force", "覆盖待裁决冲突",
                                        self.force))
        controls.append(ft.Container(height=t.px(32), content=ft.Row(
            spacing=t.sp("half"), controls=[
                C.text("写入范围", t, "caption", t.palette.text_faint),
                C.text("bib_db/bibliography.jsonl", t, role="mono",
                       color=t.palette.text_muted),
                C.text("· 1 个文件", t, "caption", t.palette.text_faint),
            ])))
        busy = self.state == "loading"
        preview_label = "运行中" if busy else "预览（不写库）"
        preview_btn = (C.primary_button if self.payload is None
                       else C.secondary_button)(
            preview_label, lambda e: self._task(self._preview), t,
            disabled=not self._can_preview())
        self._preview_button = preview_btn
        # P1-7：预览前页内只此一个 accent 主操作；预览成功后让位给确认条。
        preview_btn._bib_primary = self.payload is None
        controls.append(ft.Row(spacing=t.sp("1"), controls=[
            preview_btn,
            C.text("⌘↵", t, "caption", t.palette.text_faint),
        ]))
        controls.append(C.text_button("清空表单", self._clear, t))
        controls.append(C.text(
            "预览不写库；写入前校验库指纹。",
            t, "caption", t.palette.text_faint, max_lines=3))
        if not narrow:
            controls.append(C.text("", t, "caption", t.palette.text_faint))
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _open_params(self) -> None:
        self.params_open = True
        self._refresh_params()

    def _refresh_params(self) -> None:
        layout = self._layout
        if isinstance(layout, ft.ListView):
            params = self._params(narrow=True)
            layout.controls[0] = params
            layout._bib_left = params
        elif isinstance(layout, ft.Row):
            layout._bib_left.controls = self._params(narrow=False).controls
        self._update_page()

    def _browse(self) -> None:
        """N01：调真实的文件选择服务（`app.browse_field`），选完回填并失效旧预览。

        旧实现取 `page.file_picker`——Flet 1.0.5 的 Page 上没有这个属性，
        于是永远落到「可直接粘贴路径」的提示分支，按钮等于死的。
        `browse_field` 只写 `ref[0].value`、**不触发 on_change**，所以这里
        自己轮询回读，再走与手工输入同一条 `_on_path` 失效逻辑。
        """
        if self._picking:
            return
        app = getattr(self.page, "app", None)
        browse = getattr(app, "browse_field", None)
        if not callable(browse):
            self._notify("可直接粘贴路径，或点「浏览…」")
            return
        if not self._source_ref:
            self._notify("来源输入框尚未就绪；请重新展开参数区再选。")
            return
        kind = "file"
        title = "选择来源 .bib"
        before = (self._source_ref[0].value or "").strip()
        try:
            browse(self._source_ref, title, kind, "bib_import_src")
        except Exception as exc:  # noqa: BLE001 - 选择器实现因平台而异
            self._notify(f"文件选择器不可用：{type(exc).__name__}。可直接粘贴路径。")
            return
        self._picking = True
        source_ref = self._source_ref
        self._task(lambda: self._watch_pick(before, source_ref))

    async def _watch_pick(self, before: str, source_ref: list | None = None) -> None:
        """等真实选择结果写回输入框；取消时保持原值、不报错。

        选择结果异步写回且不触发 on_change，因此固定使用发起选择时的控件引用；
        等待期间若选择器仍为 opening 就继续轮询，结束但值未变化则视为取消。
        """
        try:
            for _ in range(240):
                await asyncio.sleep(0.25)
                host = self._host()
                # The picker updates the field asynchronously and does not emit on_change.
                # Keep the exact field reference passed to browse_field across rerenders.
                ref = source_ref if source_ref is not None else self._source_ref
                field = ref[0] if ref else None
                if field is None:
                    return
                after = (field.value or "").strip()
                if after != before and after:
                    self.path = after
                    self._store_set("recent.bib_import", after)
                    self._invalidate("来源已修改，请重新预览")
                    self._mark("bib_import_browse", mode=self.mode,
                               picked=True)
                    self._render()
                    return
                # `opening` means the result has not arrived yet, not that the picker failed.
                if host is None or self._picker_idle(host):
                    self._mark("bib_import_browse", mode=self.mode, picked=False)
                    return
        finally:
            self._picking = False

    def _host(self):
        """取当前渲染用的宿主控件（重画后可能已经换了一个）。"""
        return self._source_ref[0] if self._source_ref else None

    def _picker_idle(self, host) -> bool:
        """宿主是否已经离开「选择中」状态。

        `App.browse_field` 会维护 `_picker_state`（opening → idle）：取消、失败、
        选中都会把它放回 idle。假宿主没有这个字段时按「已空闲」处理，保持旧行为。
        """
        app = getattr(self.page, "app", None)
        state = getattr(app, "_picker_state", None)
        return state != "opening"

    def _segment(self) -> ft.Control:
        t = self.t
        p = t.palette
        cells = []
        for key, label in MODES:
            active = key == self.mode
            cells.append(ft.Container(
                height=t.px(28), border_radius=t.radius("control"),
                padding=ft.Padding(left=t.px(10), top=0, right=t.px(10), bottom=0),
                alignment=ft.Alignment.CENTER,
                bgcolor=p.accent_soft if active else None,
                content=C.text(label, t, "small",
                               p.on_accent_soft if active else p.text_muted,
                               weight="medium" if active else "regular"),
                on_click=lambda e, k=key: self._set_mode(k)))
        return ft.Container(
            height=t.px(28), border=C.box(t), border_radius=t.radius("control"),
            content=ft.Row(spacing=0, controls=cells))

    def _check(self, attr: str, label: str, value: bool) -> ft.Control:
        t = self.t
        return ft.Container(
            height=t.px(28), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(spacing=t.sp("1"), controls=[
                ft.Checkbox(value=value, splash_radius=0,
                            visual_density=ft.VisualDensity.COMPACT,
                            on_change=lambda e, name=attr: self._toggle(name, e)),
                C.text(label, t, "small", t.palette.text_muted),
            ]))

    # ── 渲染：右预览 ─────────────────────────────────────────────────
    def _preview_col(self, *, narrow: bool = False) -> ft.Control:
        t = self.t
        controls: list[ft.Control] = [
            C.section_label("预览结果", t,
                            note="预览" if self.payload is None else "预览 · 未写库"),
            ft.Container(height=t.px(6)),
        ]
        if self.state == "success":
            controls.append(self._success_banner())
            controls.append(ft.Container(height=t.px(8)))
            controls.append(self._payload_block(dim=True, narrow=narrow))
        elif self.state == "nothing":
            controls.append(S.state_banner(
                t, "empty", message=self.no_write_note or "没有需要写入的内容"))
            controls.append(ft.Container(height=t.px(8)))
            controls.append(self._payload_block(dim=True, narrow=narrow))
        elif self.state in ("loading",):
            controls.append(S.state_banner(t, "loading", message="运行中"))
        elif self.state == "failure":
            controls.append(S.state_banner(
                t, "failure", message=self.error or "预览失败",
                stderr=self.stderr, page=self._flet()))
        elif self.state == "stale":
            controls.append(S.stale_notice(
                t, title="预览已过期，本次未写库",
                detail=self.dirty_reason or "库已被其他操作修改",
                on_repreview=lambda e: self._task(self._preview)))
        elif self.payload is None:
            controls.append(S.state_banner(
                t, "empty", message="尚未预览：选好来源后点「预览（不写库）」"))
        if self.payload is not None and self.state in ("ready", "stale", "running",
                                                       "cancelled"):
            controls.append(self._payload_block(narrow=narrow))
        self._confirmation_slot = ft.Container(
            padding=ft.Padding(0, t.px(8), 0, 0),
            content=self._confirm_layer() if self.confirm_layer else None,
            visible=self.confirm_layer)
        controls.append(self._confirmation_slot)
        controls.append(ft.Container(expand=not narrow))
        # 确认条由外层 _body 通栏挂在页面底部，右栏不再重复。
        return ft.Column(spacing=0, expand=not narrow, controls=controls)

    def _payload_block(self, *, dim: bool = False, narrow: bool = False) -> ft.Control:
        t = self.t
        counts = self._counts()
        payload = self.payload or {}
        sha = _sha8(str(payload.get("db_sha256_before") or ""))
        band = S.summary_band(t, [
            {"label": "新增", "value": str(counts["new"]), "hint": "条 · 写进库", "tone": "success"},
            {"label": "更新", "value": str(counts["updated"]), "hint": "条 · 覆盖旧值", "tone": "accent"},
            {"label": "冲突", "value": str(counts["conflicts"]), "hint": "条 · 进待处理", "tone": "warn"},
            {"label": "跳过", "value": str(counts["skipped"]), "hint": "条 · 未改动", "tone": "neutral"},
        ])
        meta = (f"库指纹 {sha or '—'} · 解析 {counts['entries']} 条 · "
                f"耗时 {self.elapsed:.1f}s")

        def make_chip(key: str, label: str) -> ft.Container:
            return C.chip(f"{label} {self._chip_count(key)}", t, active=self.filter == key,
                          on_click=lambda e: select_filter(key))

        def select_filter(key: str) -> None:
            previous = self.filter
            if key == previous:
                return
            self.filter = key
            for (chip_key, label), chip in zip(FILTERS, chips.controls):
                if chip_key in (previous, key):
                    styled = make_chip(chip_key, label)
                    chip.bgcolor, chip.content = styled.bgcolor, styled.content
            refresh_rows()
            block.update()

        chips = ft.Row(spacing=t.sp("half"), controls=[
            make_chip(key, label) for key, label in FILTERS]) if not dim else ft.Container()
        if dim:
            results = ft.Container(expand=not narrow)
        else:
            table, refresh_rows = self._preview_table(narrow=narrow)
            results = ft.Container(expand=not narrow, content=table)
        warnings = payload.get("warnings")
        warn_lines = [str(w) for w in warnings if str(w).strip()][:2] \
            if isinstance(warnings, list) else []
        controls: list[ft.Control] = [
            ft.Container(opacity=0.55 if dim else 1.0, content=band),
            C.text(meta, t, "caption", t.palette.text_faint),
            ft.Container(height=t.px(4)),
            chips,
            ft.Container(height=t.px(4)),
            results,
        ]
        for line in warn_lines:
            controls.append(C.text(f"注意 · {line}", t, "caption", t.palette.warn,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
        block = ft.Column(spacing=t.sp("half"), controls=controls)
        return block

    def _preview_table(self, *, narrow: bool) -> tuple[ft.Column, Callable[[], None]]:
        t = self.t
        rows = self._changes()
        visible_rows = self._rows(rows)
        body = ft.Column(spacing=0)

        def toggle_detail(key: str) -> None:
            self.expanded_change = "" if self.expanded_change == key else key
            # Keep both the narrow page scroller and the wide table scroller mounted.
            body.controls = self._preview_rows(visible_rows, toggle_detail)
            body.update()

        # 宽窗按未筛选行数选择滚动布局。筛选只更新表体，短结果自然限制滚动范围。
        scrolling = len(rows) > 8 and not narrow
        table = ft.Column(spacing=0, expand=scrolling,
                          scroll=C.scrollbar(t) if scrolling else None,
                          controls=[
                              S.table_header_row(t, ["状态", "key", "题名", "审阅"],
                                                 weights=[0.72, 1.2, 3.2, 1.0],
                                                 mono=(1,)),
                              body,
                          ])
        table._bib_shrink = len(rows) <= 8

        def more_footer() -> list[ft.Control]:
            remaining = len(visible_rows) - self.shown
            if remaining <= 0:
                return []
            return [ft.Container(
                height=t.px(36), alignment=ft.Alignment.CENTER_LEFT,
                content=C.text_button(f"载入更多（还有 {remaining} 条）",
                                      load_more, t))]

        def load_more(e) -> None:
            old_shown = self.shown
            self.shown += 100
            body.controls.extend(self._preview_rows(visible_rows, toggle_detail,
                                                    start=old_shown))
            table.controls[2:] = more_footer()
            table.update()

        def refresh_rows() -> None:
            nonlocal visible_rows
            visible_rows = self._rows(rows)
            body.controls = self._preview_rows(visible_rows, toggle_detail)
            table.controls[2:] = more_footer()

        body.controls = self._preview_rows(visible_rows, toggle_detail)
        table.controls.extend(more_footer())
        return table, refresh_rows

    def _preview_rows(self, rows: list[dict], toggle_detail, *, start: int = 0) -> list[ft.Control]:
        t = self.t
        body_rows = []
        for row in rows[start : self.shown]:
            label, _tone = _STATUS_LABEL.get(row["status"], (row["status"] or "—", "muted"))
            detail_id = (row["change_id"] or
                         f"{row['key']}::{row['incoming_key']}::{row['line']}")
            open_detail = self.expanded_change == detail_id
            review = C.text_button("收起" if open_detail else "查看明细",
                                   lambda e, k=detail_id: toggle_detail(k), t)
            title = row["title"] or "（无题名）"
            body_rows.append(S.table_row(
                t, [label, row["key"] or row["incoming_key"], title, review],
                weights=[0.72, 1.2, 3.2, 1.0], mono=(1,),
                align=["left", "left", "left", "right"],
                selected=open_detail,
                on_click=lambda e, k=detail_id: toggle_detail(k)))
            if open_detail:
                body_rows.append(self._change_detail(row))
        if not rows:
            body_rows.append(S.empty_row(t, "没有变化记录"))
        return body_rows

    def _chip_count(self, key: str) -> str:
        counts = self._counts()
        count_key = {"new": "new", "updated": "updated",
                     "conflict": "conflicts", "skipped": "skipped"}.get(key)
        if key == "all":
            return str(len(self._changes()))
        return str(counts.get(count_key, 0))

    def _confirm_layer(self) -> ft.Control:
        t = self.t
        p = t.palette
        counts = self._counts()
        sha = _sha8(self.expect_sha)
        lines = [
            f"新增 {counts['new']} 条 · 更新 {counts['updated']} 条 · "
            f"冲突 {counts['conflicts']} 条（进待处理）",
            f"库指纹 {sha} · 1 条本地提交 · 不推送",
            "页内不可撤销，只能回退本次提交。",
        ]
        return ft.Container(
            bgcolor=p.accent_soft, border=C.box(t, p.accent),
            border_radius=t.radius("sm"),
            padding=ft.Padding(left=t.px(12), top=t.px(8), right=t.px(12), bottom=t.px(8)),
            content=ft.Column(spacing=t.sp("half"), controls=[
                C.text("确认写入引用库？", t, role="body-strong"),
                *[C.text(line, t, "caption", p.on_accent_soft) for line in lines],
                ft.Row(spacing=t.sp("1"), controls=[
                    C.text_button("取消", self._cancel_confirm, t),
                    C.primary_button("确认写入", lambda e: self._task(self._write), t),
                ]),
            ]))

    def _success_banner(self) -> ft.Control:
        t = self.t
        counts = self._counts(self.result or {})
        commit = self.commit or {}
        sha = _sha8(str(commit.get("sha") or ""))
        detail = (f"已提交 {sha} · 未推送" if commit.get("committed") and sha
                  else "已写入；未生成版本提交")
        result = S.result_banner(
            t, title=f"已写入 {counts['new']} 新增 · {counts['updated']} 更新", detail=detail,
            on_review=lambda e: self._goto_pending(),
            on_revert=self._revert_confirm if commit.get("committed") else None)
        version_link = C.secondary_button(
            "查看版本与同步",
            lambda e: self.page.navigate_route("bib:version_sync"),
            t, icon="HISTORY", height=28)
        return ft.Column(spacing=t.sp("half"), controls=[
            result,
            ft.Row(alignment=ft.MainAxisAlignment.END, controls=[version_link]),
        ])

    def _goto_pending(self) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav("review")
            return
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set("bib_view", "review")
            except Exception:  # noqa: BLE001
                pass
        self._notify("去「待处理」裁决冲突")

    def _bottom(self) -> ft.Control:
        t = self.t
        if self.state in ("success", "nothing"):
            return ft.Container(height=S.BANNER_H)
        has_preview = self.payload is not None
        stale = has_preview and (self._is_stale() or self.state == "stale")
        if not has_preview:
            consequence = "尚未预览"
            detail = "先预览（不写库），才能确认写入"
        elif stale:
            consequence = self._consequence()
            detail = f"预览已过期：{self.dirty_reason or '库指纹已变化'}，先重新预览"
        else:
            consequence = self._consequence()
            detail = "只写 bibliography.jsonl · 不推送"
        bar = S.confirm_bar(
            t, consequence=consequence,
            detail=detail, sha=_sha8(self.expect_sha) if has_preview else None,
            on_confirm=self._open_confirm,
            confirm_disabled=not self._can_confirm(),
            confirm_label="运行中" if self.state == "running" else "确认写入",
            # P1-7：重新预览只在已有陈旧预览时出现；首次预览/已就绪都不占位。
            on_repreview=(lambda e: self._task(self._preview)) if stale else None,
            stale=stale)
        if not stale:
            bar._bib_repreview_btn.visible = False
        if not has_preview:
            bar._bib_confirm_btn.tooltip = "尚未预览：先点「预览（不写库）」"
        return bar
