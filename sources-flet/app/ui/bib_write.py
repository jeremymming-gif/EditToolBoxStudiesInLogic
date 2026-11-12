"""引用库写操作视图（B-246c P6）：入藏（R1）/ 待处理（R2）/ 版本层（R3）。

页面只做三件事：取值（输入控件）→ 拼参数（services.engine_bridge）→ 显示结果
（services.bib_git 只管本地 git）。**不复制任何引擎判据**：

* 入藏：先 `import-bib/import-dir --dry-run --json` 预览（只算不写），把
  `db_sha256_before` 记下来；用户二次确认后去掉 `--dry-run` 并带
  `--expect-db-sha256` 真写。写成功后自动本地 commit（只提交库文件）。
* 待裁决：`conflicts --json --pending-only` 只读清单；逐条 keep / adopt / defer /
  replace 走 `resolve`，批量「全部采用…」走 `--adopt-all-pending`；写前同样带 sha 闸，
  写成功后同样自动本地 commit（每次写库合 1 条，批量「全部采用」也只 1 条；只提交库文件）。
* 版本层：分支 / 库脏否 / 未推送数 / 最近 3 条库提交；「推送…」只推连续仅含
  `bib_db/` 的提交（B-203），「回退…」= checkout 上一个提交 + 新 commit。
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import components as C

#: `changes[].status` → 中文（与 `_change_rows` 的状态口径一一对应）
CHANGE_LABEL = {"new": "新增", "conflict": "冲突", "updated": "更新", "merged": "并来源"}
#: 冲突裁决动作 → 中文
ACTION_LABEL = {"keep": "保留旧值", "defer": "待人工查证", "adopt": "采用新稿值",
                "replace": "采用裁决 bib"}


# ── 纯函数（可直接单测，不碰 flet / 不调引擎）──────────────────────────
def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summary_of(payload: dict) -> dict:
    """取入藏摘要：import-bib 在 `report`，import-dir 在 `result.total`。"""
    if not isinstance(payload, dict):
        return {}
    report = payload.get("report")
    if isinstance(report, dict):
        return report
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("total"), dict):
        return result["total"]
    return payload.get("total") if isinstance(payload.get("total"), dict) else {}


def import_counts(payload: dict) -> dict:
    """入藏预览计数（两种 payload 同口径）：新增 / 更新 / 合并 / 冲突 / 解析 / 跳过。"""
    report = _summary_of(payload)
    skipped = report.get("skipped") or {}
    skipped_n = sum(_as_int(v) for v in skipped.values()) if isinstance(skipped, dict) else 0
    return {"entries": _as_int(report.get("entries")), "new": _as_int(report.get("new")),
            "updated": _as_int(report.get("updated")),
            "merged": _as_int(report.get("merged")),
            "conflicts": _as_int(report.get("conflicts")), "skipped": skipped_n}


def import_warnings(payload: dict, limit: int = 6) -> list[str]:
    """预览里的警告（`report.warnings` + import-dir 的 `result.warnings`），去重限量。"""
    items: list[str] = []
    for source in (_summary_of(payload), (payload or {}).get("result") or {}):
        if isinstance(source, dict):
            items.extend(str(text) for text in (source.get("warnings") or []) if str(text).strip())
    seen: list[str] = []
    for text in items:
        if text not in seen:
            seen.append(text)
    return seen[:max(0, limit)]


def change_rows(payload: dict) -> list[dict]:
    """预览明细：`changes[]`（导入前后逐条变化；没有变化的条目本来就不进明细）。"""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("changes")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def describe_change(row: dict) -> str:
    """一行明细：`[新增] 题名（作者 年）· key`。"""
    status = CHANGE_LABEL.get(str(row.get("status") or ""), str(row.get("status") or "变化"))
    title = str(row.get("title") or row.get("key") or "（无题名）")
    who = " ".join(str(row.get(name) or "") for name in ("author", "year")).strip()
    key = str(row.get("key") or "")
    tail = f"（{who}）" if who else ""
    return f"[{status}] {title}{tail} · {key}" if key else f"[{status}] {title}{tail}"


def conflict_where(item: dict) -> str:
    """冲突条目出处：`期/篇`；没有就「（无期/篇）」。"""
    parts = [str(item.get("issue") or ""), str(item.get("slug") or "")]
    return "/".join(part for part in parts if part) or "（无期/篇）"


def conflict_title(item: dict) -> str:
    return str(item.get("title") or item.get("record_key") or "（无题名）")


def conflict_fields(item: dict) -> list[str]:
    diff = item.get("diff")
    return list(diff.keys()) if isinstance(diff, dict) else []


def conflict_diff_lines(item: dict, limit: int = 4) -> list[str]:
    """逐字段 old → new（与 CONFLICTS.md 同源；`remove=True` 表示新稿无此字段）。"""
    diff = item.get("diff")
    if not isinstance(diff, dict):
        return []
    lines: list[str] = []
    for name, change in list(diff.items())[:max(1, limit)]:
        change = change if isinstance(change, dict) else {}
        old = str(change.get("old") or "") or "（空）"
        new = "（新稿无此字段）" if change.get("remove") else (str(change.get("new") or "") or "（空）")
        lines.append(f"{name}：{old} → {new}")
    if len(diff) > limit:
        lines.append(f"… 另有 {len(diff) - limit} 个字段差异")
    return lines


def explain_result(result: dict) -> str:
    """把 `read_json_command` 的结果翻成一句人话（优先 JSON 里的 `error`）。"""
    payload = result.get("json") or {}
    text = str(payload.get("error") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        lines = [str(line).strip() for line in (result.get("lines") or []) if str(line).strip()]
        text = (lines[-1] if lines else str(result.get("error") or "命令失败")).lstrip("✗ ").strip()
    code = result.get("code")
    return f"{text}（退出码 {code}）" if code not in (None, "") else text


def sha_short(value: str, length: int = 12) -> str:
    text = str(value or "").strip()
    if not text:
        return "（空）"
    return text[:length] + "…" if len(text) > length else text


def resolve_notice(action: str, payload: dict, commit: dict | None) -> str:
    """裁决写成功后的提示：写库结果 + 版本层结果（commit 失败只提示，不回滚已写数据）。"""
    commit = commit or {}
    label = ACTION_LABEL.get(action or "", "")
    base = "裁决已记录：" + (label or "全部采用")
    if commit.get("committed"):
        return base + f"；已生成版本提交 {sha_short(str(commit.get('sha') or ''))}"
    if not (payload or {}).get("written"):
        return base + "（库文件没有改动，未生成版本提交）"
    reason = str(commit.get("error") or commit.get("reason") or "").strip()
    return base + ("；版本层提示：" + reason if reason else "；未生成版本提交")


def git_summary_lines(probe: dict) -> list[str]:
    """版本库状态区的人读摘要（纯函数，测试直接断言）。"""
    if not probe or not probe.get("ok"):
        return [str((probe or {}).get("error") or "版本库状态未读取")]
    lines = [f"分支 {probe.get('branch') or '（未知）'}"
             f" · upstream {probe.get('upstream') or '（无）'}"]
    ahead, behind = probe.get("ahead"), probe.get("behind")
    lines.append(f"未推送 {ahead if ahead is not None else '—'} 条"
                 f" · 落后 {behind if behind is not None else '—'} 条")
    lines.append("库文件：" + ("有未提交改动" if probe.get("dirty_db") else "干净")
                 + f" · 其它改动 {_as_int(probe.get('dirty_other'))} 个")
    if probe.get("identity"):
        lines.append(f"提交身份：{probe.get('identity')}")
    commits = probe.get("commits") or []
    if commits:
        lines.append("最近库提交：" + "；".join(
            f"{GIT.short_sha(str(row.get('sha') or ''), 8)} {str(row.get('subject') or '')}"
            for row in commits[:3]))
    else:
        lines.append("最近库提交：—")
    return lines


def _notes(app) -> str:
    return str(getattr(app, "notes_repo", "") or "")


# ── 入藏（R1）──────────────────────────────────────────────────────────
class BibImportView:
    """「入藏」页：3 种来源 → dry-run 预览 → 二次确认 → 写库 → 自动本地提交。"""

    MODES = [("file", "bib 文件"), ("dir", "整期目录"), ("text", "粘贴 bib")]

    def __init__(self, page) -> None:
        self.page = page
        self.t = page.t
        store = page.app.store
        self.mode = "file"
        self.file_path = str(store.get("recent.bib_import_file", "") or "")
        self.dir_path = str(store.get("recent.bib_import_dir", "") or "")
        self.text_value = ""
        # 批 109 Wave 0 / S3：入藏口径与旧壳对齐——表单提供 --issue/--slug，
        # 且默认视为已核实（--verified）；取消勾选则显式传 --unverified。
        self.issue = str(store.get("recent.bib_issue", "") or "")
        self.slug = str(store.get("recent.bib_slug", "") or "")
        self.verified = True
        self.preview: dict | None = None
        self.done: dict | None = None
        self.commit: dict | None = None
        self.error = ""
        self.busy = False
        self.expect_sha = ""
        self.stale = False            # dry-run 预览是否已因输入变更而作废
        self.preview_signature: tuple | None = None
        self._temp_dir = ""
        self._file_ref: list = []
        self._dir_ref: list = []
        self._text_ref: list = []

    # 状态小工具 ───────────────────────────────────────────────────────
    def _notes(self) -> str:
        return _notes(self.page.app)

    def _db(self) -> str:
        stats = self.page.stats_data or {}
        return str(stats.get("db") or "") or GIT.default_db(self._notes())

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.error = ""
        self._invalidate_preview()
        self.page.mark("bib_import_mode", mode=mode)
        self.page._render()

    def _picked(self, ref: list, fallback: str) -> str:
        return ((ref[0].value or "").strip() if ref else "") or fallback

    # 输入指纹 / 过期判定（批 109 Wave 0 / S3）────────────────────────
    def _signature(self) -> tuple:
        """当前输入指纹：来源模式 + 源路径/文本 + 入藏口径（issue/slug/verified）。"""
        path = ""
        if self.mode == "file":
            path = self._picked(self._file_ref, self.file_path).strip()
        elif self.mode == "dir":
            path = self._picked(self._dir_ref, self.dir_path).strip()
        text = self._text_value() if self.mode == "text" else ""
        meta = () if self.mode == "dir" else (
            self.issue.strip(), self.slug.strip(), bool(self.verified))
        return (self.mode, path, meta, text)

    def _invalidate_preview(self) -> None:
        """任何输入变更：已生成的 dry-run 预览立即过期，确认写入被禁用。"""
        if self.preview is not None:
            self.stale = True
            self.expect_sha = ""      # 旧库 sha 闸一并作废，重新预览才重新取

    def _preview_stale(self) -> bool:
        if self.stale:
            return True
        if self.preview is None:
            return False
        return self.preview_signature is not None and self._signature() != self.preview_signature

    def _on_file_change(self, e) -> None:
        self.file_path = (e.control.value or "").strip()
        self._invalidate_preview()

    def _on_dir_change(self, e) -> None:
        self.dir_path = (e.control.value or "").strip()
        self._invalidate_preview()

    def _on_text_change(self, e) -> None:
        self.text_value = e.control.value or ""
        self._invalidate_preview()

    def _on_issue_change(self, e) -> None:
        self.issue = (e.control.value or "").strip()
        self._invalidate_preview()

    def _on_slug_change(self, e) -> None:
        self.slug = (e.control.value or "").strip()
        self._invalidate_preview()

    def _on_verified_change(self, e) -> None:
        self.verified = bool(e.control.value)
        self._invalidate_preview()

    # 构建 ─────────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._file_ref, self._dir_ref, self._text_ref = [], [], []
        chips = ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.chip(label, t, active=(key == self.mode),
                   on_click=lambda e, k=key: self._set_mode(k))
            for key, label in self.MODES])
        source: list = [self._source_control()]
        actions = ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.primary_button("预览（不写库）", lambda e: self.page.page.run_task(self._preview),
                             t, icon="VISIBILITY", disabled=self.busy),
            C.secondary_button("清空", lambda e: self._reset(), t, icon="BACKSPACE",
                               disabled=self.busy),
        ])
        controls: list = [
            C.card(ft.Column(spacing=t.sp("1"), controls=[
                C.section_title("入藏", t, note="预览只算不写；确认后才写库"),
                C.text("三种来源：回读导出的 .bib / 整期成品目录 / 粘贴 bib 文本。"
                       "预览显示新增、更新、冲突条数；写库前校验库指纹，成功后自动本地提交"
                       "（只提交 bib_db/bibliography.jsonl）。",
                       t, "caption", t.palette.text_muted),
                chips,
                *source,
                *self._meta_controls(),
                actions,
            ]), t),
        ]
        if self.error:
            controls.append(C.notice_bar(self.error, t, "warn"))
        if self.preview:
            controls.append(self._preview_card())
        if self.done:
            controls.append(self._done_card())
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _meta_controls(self) -> list:
        """入藏口径：期 / 篇 / 已核实（整期目录入藏没有这些旗标，故不显示）。"""
        if self.mode == "dir":
            return []
        t = self.t
        return [
            ft.Row(spacing=t.sp("2"), wrap=True, controls=[
                C.form_field(t, "期（可空）", C.text_field(
                    t, value=self.issue, width=t.px(240),
                    hint_text="如 2026年第5期(90)", on_change=self._on_issue_change)),
                C.form_field(t, "篇（可空）", C.text_field(
                    t, value=self.slug, width=t.px(240),
                    hint_text="如 01 Meimei_Han", on_change=self._on_slug_change)),
            ]),
            C.option_row("已人工核实",
                         "默认与旧壳一致：入藏记为已核实；取消则记为未核实", t,
                         icon="VERIFIED", checked=bool(self.verified),
                         on_change=self._on_verified_change),
        ]

    def _source_control(self) -> ft.Control:
        t = self.t
        if self.mode == "file":
            return C.path_picker(
                "bib 文件", self.file_path, "选择回读校验导出的 .bib（只读源文件）", t,
                on_change=self._on_file_change,
                on_browse=lambda e: self.page.app.browse_field(
                    self._file_ref, "选择 bib 文件", "file", "bib_import_file"),
                required=True, field_ref=self._file_ref)
        if self.mode == "dir":
            return C.path_picker(
                "整期目录", self.dir_path, "选一期目录（如 20xx年第N期(…)）或稿件库根目录", t,
                on_change=self._on_dir_change,
                on_browse=lambda e: self.page.app.browse_field(
                    self._dir_ref, "选择整期目录", "dir", "bib_import_dir"),
                required=True, field_ref=self._dir_ref)
        paste_field = C.text_field(
            t, value=self.text_value, multiline=True, min_lines=6, max_lines=14,
            expand=True,
            hint_text="把 bib 条目粘贴到这里；预览/写入时写成临时文件，不改动任何源文件",
            on_change=self._on_text_change,
        )
        # Q11：按钮在多行框右侧顶部；form_field 的「标签上置 + ✕ 清空」语义不变
        # （✕ 已由 text_field/make_clearable 挂在框内，这里 clearable=False 防重挂）。
        body = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[paste_field,
                      C.paste_button(t, self.page, paste_field,
                                     height=t.control_h())])
        return C.form_field(t, "粘贴 bib 文本", body, expand=True, clearable=False)

    # 预览 / 写库 ─────────────────────────────────────────────────────
    def _source_args(self) -> tuple[dict, str, str]:
        """→ (参数字典, 错误, 写库动作标签)。文本模式返回 `bib` 字段（临时文件）。"""
        if self.mode == "file":
            path = self._picked(self._file_ref, self.file_path)
            if not path:
                return {}, "请先选择 .bib 文件", ""
            return {"bib": path}, "", "入藏"
        if self.mode == "dir":
            path = self._picked(self._dir_ref, self.dir_path)
            if not path:
                return {}, "请先选择整期目录", ""
            if not os.path.isdir(path):
                return {}, f"目录不存在：{path}", ""
            return {"root": path}, "", "整期入藏"
        text = self._text_value()
        if not text.strip():
            return {}, "请先粘贴 bib 文本", ""
        try:
            return {"bib": self._text_file(text)}, "", "入藏（粘贴）"
        except OSError as exc:
            return {}, f"写临时 bib 失败：{exc}", ""

    def _text_value(self) -> str:
        if self._text_ref:
            return self._text_ref[0].value or ""
        return self.text_value

    def _text_file(self, text: str) -> str:
        if not self._temp_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="slcn-bib-import-")
        path = os.path.join(self._temp_dir, "pasted.bib")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
        return path

    def _build_call(self, *, dry_run: bool, expect_sha: str) -> EB.EngineCall:
        args, _error, _label = self._source_args()
        if "root" in args:
            return EB.bibdb_import_dir_call(root=args["root"], dry_run=dry_run,
                                            expect_db_sha256=expect_sha)
        # 批 109 Wave 0 / S3：入藏必须带口径旗标（旧壳固定 --verified；引擎默认 False）。
        # 未勾选「已核实」时用 --unverified 显式压过「issue+slug 即算已核实」的引擎语义。
        issue = self.issue.strip()
        slug = self.slug.strip()
        return EB.bibdb_import_call(
            bib=args.get("bib", ""), dry_run=dry_run, expect_db_sha256=expect_sha,
            issue=issue, slug=slug, verified=bool(self.verified),
            unverified=not bool(self.verified),
        )

    async def _call(self, call: EB.EngineCall) -> dict:
        self.busy = True
        self.error = ""
        self.page._render()
        try:
            return await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:            # 找不到引擎
            return {"ok": False, "error": str(exc), "json": {}, "code": None}
        finally:
            self.busy = False

    async def _preview(self) -> None:
        _args, error, _label = self._source_args()
        if error:
            self.error = error
            self.page._render()
            return
        result = await self._call(self._build_call(dry_run=True, expect_sha=""))
        payload = result.get("json") or {}
        if result.get("ok") and payload:
            self.preview = payload
            self.expect_sha = str(payload.get("db_sha256_before") or "")
            self.preview_signature = self._signature()
            self.stale = False
            self.done = None
            self.commit = None
            counts = import_counts(payload)
            self.page.mark("bib_import_preview", ok=True, code=result.get("code"),
                           mode=self.mode, entries=counts["entries"], new=counts["new"],
                           updated=counts["updated"], conflicts=counts["conflicts"],
                           db_existed=payload.get("db_existed"), written=False)
        else:
            self.preview = None
            self.expect_sha = ""
            self.preview_signature = None
            self.stale = False
            self.error = explain_result(result)
            self.page.mark("bib_import_preview", ok=False, code=result.get("code"),
                           mode=self.mode, error=self.error[:200])
        self.page._render()

    def _confirm_write(self, e=None) -> None:
        if not self.preview:
            return
        if self._preview_stale():
            # 输入在预览后变了：不弹确认框，先把预览作废并要求重新预览（零写入）。
            self.stale = True
            self.page.mark("bib_import_stale_block", mode=self.mode, action="confirm")
            self.page._render()
            return
        counts = import_counts(self.preview)
        db = str(self.preview.get("db") or self._db())
        meta = ("整期目录（期/篇由目录名推导）" if self.mode == "dir"
                else f"期：{self.issue or '未指定'}；篇：{self.slug or '未指定'}；"
                     f"{'已核实' if self.verified else '未核实'}")
        message = (f"将写入：新增 {counts['new']} 条、更新 {counts['updated']} 条、"
                   f"冲突 {counts['conflicts']} 条（共解析 {counts['entries']} 条）。\n"
                   f"入藏口径：{meta}\n"
                   f"库：{db}\n"
                   "写前校验库指纹，不符即拒绝、零写入；成功后自动本地提交，"
                   "只提交 bib_db/bibliography.jsonl，不推送。")
        C.confirm(self.page.page, self.t, title="确认写入引用库？", message=message,
                  confirm_label="写入", on_confirm=lambda e: self.page.page.run_task(self._write))

    async def _write(self) -> None:
        if not self.preview:
            return
        if self._preview_stale():
            # 双保险：确认按钮之外再校验一次（写入调用绝不在过期预览上执行）。
            self.stale = True
            self.expect_sha = ""
            self.error = "输入已变更，预览已过期：请重新预览后再写入（本次未写库）。"
            self.page.mark("bib_import_stale_block", mode=self.mode, action="write")
            self.page._render()
            return
        result = await self._call(self._build_call(dry_run=False, expect_sha=self.expect_sha))
        payload = result.get("json") or {}
        ts = self.t
        if not (result.get("ok") and payload):
            # 写失败（含 sha 不符）：保留预览与输入，让用户刷新后重试；库零写入由引擎保证。
            self.error = explain_result(result)
            self.page.mark("bib_import_write", ok=False, code=result.get("code"),
                           error=self.error[:200], written=False)
            self.page._render()
            return
        self.done = payload
        self.preview = None
        self.expect_sha = ""
        self.stale = False
        self.preview_signature = None
        counts = import_counts(payload)
        self.page.stats_data = None            # 概况页下次进入时重新读
        self.page.mark("bib_import_write", ok=True, code=result.get("code"),
                       written=payload.get("written"), new=counts["new"],
                       updated=counts["updated"], conflicts=counts["conflicts"],
                       sha_before=payload.get("db_sha256_before"),
                       sha_after=payload.get("db_sha256_after"))
        db = str(payload.get("db") or "")
        if payload.get("written") and db:
            total = counts["new"] + counts["updated"] + counts["conflicts"]
            message = GIT.commit_message("入藏", total, str(payload.get("db_sha256_before") or ""),
                                         str(payload.get("db_sha256_after") or ""))
            self.commit = await asyncio.to_thread(GIT.commit_db, db, message)
            self.page.mark("bib_git_commit", **(self.commit or {}))
        self.page._render()
        _ = ts

    def _reset(self, e=None) -> None:
        self.preview = None
        self.done = None
        self.commit = None
        self.error = ""
        self.expect_sha = ""
        self.stale = False
        self.preview_signature = None
        self.page._render()

    # 渲染片段 ────────────────────────────────────────────────────────
    def _preview_card(self) -> ft.Control:
        t = self.t
        payload = self.preview or {}
        counts = import_counts(payload)
        cards = ft.Row(spacing=t.sp("1"), wrap=False, controls=[
            C.stat_card("新增", str(counts["new"]), "写库后新增条目", t),
            C.stat_card("更新", str(counts["updated"]), "已有条目被更新", t),
            C.stat_card("冲突", str(counts["conflicts"]), "写库后进「待处理」", t),
            C.stat_card("解析 / 跳过", f"{counts['entries']} / {counts['skipped']}",
                        "预览未写库", t),
        ])
        rows = change_rows(payload)
        details: list = []
        for row in rows[:20]:
            details.append(C.text(describe_change(row), t, "small", t.palette.text))
        if len(rows) > 20:
            details.append(C.text(f"… 其余 {len(rows) - 20} 条变化未展开", t, "caption",
                                  t.palette.text_faint))
        if not details:
            details.append(C.text("没有条目变化（这些内容已在库里）", t, "small",
                                  t.palette.text_muted))
        warnings = import_warnings(payload)
        controls: list = [
            C.section_title("预览结果", t, note="预览：库文件未改动"),
        ]
        if self._preview_stale():
            controls.append(C.notice_bar(
                "输入已变更，预览过期；请「重新预览」后再写入（写入按钮已禁用）。",
                t, "warn"))
        controls += [
            cards,
            C.text(f"库：{payload.get('db') or '（未知）'}"
                   f"（{'已存在' if payload.get('db_existed') else '写库时新建'}）· "
                   f"库指纹 {sha_short(str(payload.get('db_sha256_before') or ''), 16)}",
                   t, "caption", t.palette.text_faint),
            ft.Column(spacing=t.sp("half"), controls=details),
        ]
        if warnings:
            controls.append(C.notice_bar("；".join(warnings), t, "warn"))
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.primary_button("确认写入…", self._confirm_write, t, icon="SAVE",
                             disabled=self.busy or self._preview_stale()),
            C.secondary_button("重新预览", lambda e: self.page.page.run_task(self._preview), t,
                               icon="REFRESH", disabled=self.busy)]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _done_card(self) -> ft.Control:
        t = self.t
        payload = self.done or {}
        written = bool(payload.get("written"))
        counts = import_counts(payload)
        line = (f"已写库：新增 {counts['new']} 条、更新 {counts['updated']} 条、"
                f"冲突 {counts['conflicts']} 条；库指纹 "
                f"{sha_short(str(payload.get('db_sha256_before') or ''), 16)} → "
                f"{sha_short(str(payload.get('db_sha256_after') or ''), 16)}"
                if written else "库无变化（未重写、未生成提交）。")
        controls: list = [C.section_title("写入结果", t, note="已写库" if written else "无变化"),
                          C.text(line, t, "small", t.palette.text)]
        commit = self.commit or {}
        if written and commit:
            if commit.get("ok") and commit.get("committed"):
                controls.append(C.notice_bar(
                    f"已生成版本提交 {GIT.short_sha(str(commit.get('sha') or ''))}："
                    f"{commit.get('message') or ''}", t, "success"))
            else:
                controls.append(C.notice_bar(
                    "版本层提示：" + str(commit.get("error") or commit.get("reason") or "未生成提交"),
                    t, "warn"))
        controls.append(C.secondary_button("继续入藏", self._reset, t, icon="ADD"))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)


# ── 待处理（R2 裁决 + R3 版本层）────────────────────────────────────────
class BibReviewView:
    """「待处理」页：冲突清单逐条裁决 + 版本库状态区（提交 / 推送 / 回退）。"""

    def __init__(self, page) -> None:
        self.page = page
        self.t = page.t
        self.items: list[dict] = []
        self.totals: dict = {}
        self.db_sha = ""
        self.probe: dict = {}
        self.error = ""
        self.loaded = False
        self.busy = False
        self.commit: dict | None = None
        self._replace_refs: dict[str, list] = {}
        self._expanded: str = ""

    def _notes(self) -> str:
        return _notes(self.page.app)

    def _db(self) -> str:
        stats = self.page.stats_data or {}
        return str(stats.get("db") or "") or GIT.default_db(self._notes())

    def ensure_loaded(self) -> None:
        if not self.loaded and not self.busy:
            self.page.page.run_task(self._load)

    # 数据加载 ────────────────────────────────────────────────────────
    async def _load(self) -> None:
        self.busy = True
        self.error = ""
        self.page._render()
        try:
            call = EB.bibdb_conflicts_call(pending_only=True)
            result = await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:
            result = {"ok": False, "error": str(exc), "json": {}, "code": None}
        payload = result.get("json") or {}
        if result.get("ok") and payload:
            self.items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            self.totals = payload.get("totals") or {}
            self.db_sha = str(payload.get("db_sha256") or "")
            self.loaded = True
            self.error = ""
        else:
            self.items = []
            self.error = explain_result(result)
        probe = await asyncio.to_thread(GIT.status, self._db(), notes_repo=self._notes())
        self.probe = probe or {}
        self.busy = False
        self.page.mark("bib_review_load", ok=bool(result.get("ok")),
                       code=result.get("code"), pending=len(self.items),
                       totals=self.totals, git_ok=bool(self.probe.get("ok")))
        self.page._render()

    # 裁决 ────────────────────────────────────────────────────────────
    async def _resolve(self, *, key: str = "", action: str = "", fields: str = "",
                       incoming_sha256: str = "", bib: str = "",
                       adopt_all_pending: bool = False) -> None:
        call = EB.bibdb_resolve_call(
            key=key, action=action or "keep", fields=fields,
            incoming_sha256=incoming_sha256, bib=bib,
            adopt_all_pending=adopt_all_pending,
            expect_db_sha256=self.db_sha, dry_run=False)
        self.busy = True
        self.page._render()
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:
            result = {"ok": False, "error": str(exc), "json": {}, "code": None}
        self.busy = False
        payload = result.get("json") or {}
        ok = bool(result.get("ok") and payload)
        if not ok:
            self.error = explain_result(result)
            self.page.mark("bib_resolve", ok=False, action=action or "adopt-all",
                           key=key, code=result.get("code"), error=self.error[:200])
            self.page._render()
            return
        self.error = ""
        self.commit = None
        if payload.get("written"):
            db = str(payload.get("db") or "") or self._db()
            if db:
                if adopt_all_pending:
                    count = (_as_int(payload.get("records")) or _as_int(payload.get("count"))
                             or len(payload.get("adopted") or []))
                    label = f"一键采用 {count} 条"
                else:
                    count = 1
                    label = f"裁决 {ACTION_LABEL.get(action, action or 'keep')}"
                message = GIT.commit_message(label, count,
                                             str(payload.get("db_sha256_before") or ""),
                                             str(payload.get("db_sha256_after") or ""))
                try:
                    self.commit = await asyncio.to_thread(GIT.commit_db, db, message)
                except Exception as exc:  # noqa: BLE001 - 版本层异常一律降级为提示
                    self.commit = {"ok": False, "committed": False, "soft": True,
                                   "error": f"版本层异常：{exc}"}
                self.page.mark("bib_git_commit", **(self.commit or {}))
        self.page.mark("bib_resolve", ok=True, action=action or "adopt-all",
                       key=key, count=payload.get("count"),
                       written=payload.get("written"),
                       committed=bool((self.commit or {}).get("committed")))
        self.page.app.toast(resolve_notice(action, payload, self.commit))
        self.loaded = False
        await self._load()

    def _confirm(self, *, title: str, message: str, confirm_label: str, on_yes) -> None:
        C.confirm(self.page.page, self.t, title=title, message=message,
                  confirm_label=confirm_label, on_confirm=on_yes)

    def _keep(self, item: dict) -> None:
        self._confirm(
            title="保留库内旧值？",
            message=f"C{_as_int(item.get('stable_no')):03d} · {conflict_title(item)}\n"
                    f"记录：{item.get('record_key')}\n保留已核实的旧值（不采用新稿值）。\n"
                    "写成功后自动本地提交（只提交库文件）。",
            confirm_label="保留旧值",
            on_yes=lambda e: self.page.page.run_task(
                self._resolve, key=str(item.get("record_key") or ""), action="keep"))

    def _defer(self, item: dict) -> None:
        self._confirm(
            title="标记为待人工查证？",
            message=f"C{_as_int(item.get('stable_no')):03d} · {conflict_title(item)}\n"
                    f"记录：{item.get('record_key')}\n裁决记为 defer，先从待裁决清单移走。\n"
                    "写成功后自动本地提交（只提交库文件）。",
            confirm_label="待查证",
            on_yes=lambda e: self.page.page.run_task(
                self._resolve, key=str(item.get("record_key") or ""), action="defer"))

    def _adopt(self, item: dict) -> None:
        sha = str(item.get("incoming_sha256") or "")
        if not sha:
            self.page.app.toast("该条没有冲突指纹，不能采用新稿值")
            return
        lines = conflict_diff_lines(item, limit=6)
        self._confirm(
            title="逐字段采用新稿值？",
            message=(f"C{_as_int(item.get('stable_no')):03d} · {conflict_title(item)}\n"
                     f"记录：{item.get('record_key')}（指纹 {sha[:12]}…）\n"
                     + ("\n".join(lines) + "\n" if lines else "")
                     + "只改这些字段，key 不变；写库前校验库指纹。\n"
                     + "写成功后自动本地提交（只提交库文件）。"),
            confirm_label="采用新稿值",
            on_yes=lambda e: self.page.page.run_task(
                self._resolve, key=str(item.get("record_key") or ""), action="adopt",
                incoming_sha256=sha))

    def _replace(self, item: dict) -> None:
        ref = self._replace_refs.get(str(item.get("record_key") or "")) or []
        path = (ref[0].value or "").strip() if ref else ""
        if not path:
            self.page.app.toast("请先选择人工裁决 bib 文件")
            return
        self._confirm(
            title="用这份 bib 替换该记录？",
            message=(f"C{_as_int(item.get('stable_no')):03d} · {conflict_title(item)}\n"
                     f"记录：{item.get('record_key')}\n裁决 bib：{path}\n"
                     "整条写 entry_type + fields，key 不变；写库前校验库指纹。\n"
                     "写成功后自动本地提交（只提交库文件）。"),
            confirm_label="替换",
            on_yes=lambda e: self.page.page.run_task(
                self._resolve, key=str(item.get("record_key") or ""), action="replace",
                incoming_sha256=str(item.get("incoming_sha256") or ""), bib=path))

    def _adopt_all(self) -> None:
        pending = _as_int(self.totals.get("pending"))
        self._confirm(
            title="一键全部采用新稿值？",
            message=(f"对全部 {pending} 条待裁决冲突逐条按 adopt 语义应用新稿值。\n"
                     "同记录待裁决冲突字段重叠 → 整体拒绝并列出冲突号（请逐条处理）。\n"
                     "写库前校验库指纹；写成功后自动本地提交（只提交库文件）。"),
            confirm_label="全部采用",
            on_yes=lambda e: self.page.page.run_task(self._resolve, adopt_all_pending=True))

    # 版本层（R3）──────────────────────────────────────────────────
    async def _git_refresh(self) -> None:
        probe = await asyncio.to_thread(GIT.status, self._db(), notes_repo=self._notes())
        self.probe = probe or {}
        self.page.mark("bib_git_status", ok=bool(self.probe.get("ok")),
                       dirty_db=self.probe.get("dirty_db"), ahead=self.probe.get("ahead"))
        self.page._render()

    async def _push(self) -> None:
        probe = self.probe or {}
        confirm = self._confirm(
            title="推送库提交到远端？",
            message=(f"分支 {probe.get('branch') or '（未知）'} · 未推送 "
                     f"{probe.get('ahead') if probe.get('ahead') is not None else '—'} 条。\n"
                     "只推「连续仅含 bib_db/ 文件」的提交；遇到 notes 的提交就停在前面的 bib "
                     "前缀并列出剩余 sha。绝不 force、不推送其它文件。"),
            confirm_label="推送",
            on_yes=lambda e: self.page.page.run_task(self._push_async))
        _ = confirm

    async def _push_async(self) -> None:
        self.busy = True
        self.page._render()
        result = await asyncio.to_thread(GIT.push, self._db(), notes_repo=self._notes())
        self.busy = False
        self.page.mark("bib_git_push", **{k: v for k, v in (result or {}).items()
                                          if k in ("ok", "pushed", "count", "reason", "error")})
        if result.get("ok"):
            self.page.app.toast(str(result.get("reason") or "推送完成"))
        else:
            self.page.app.toast("推送未完成：" + str(result.get("error") or "未知原因"))
        await self._git_refresh()

    async def _rollback(self) -> None:
        probe = self.probe or {}
        self._confirm(
            title="回退库文件到上一个提交？",
            message=(f"分支 {probe.get('branch') or '（未知）'}。\n"
                     "回退 = 把库文件恢复到**上一次触及库文件的提交**再生成一次新提交"
                     "（不 hard reset、不推送、不改其它文件；不是整体 HEAD~1，"
                     "HEAD 被 notes 提交占住时也一样会回退上一次库改动）。"
                     "库内容会回到上一个版本，已写库的本次改动将不可从工作区恢复"
                     "（历史仍在 git 里）。"),
            confirm_label="回退",
            on_yes=lambda e: self.page.page.run_task(self._rollback_async))

    async def _rollback_async(self) -> None:
        self.busy = True
        self.page._render()
        result = await asyncio.to_thread(GIT.rollback, self._db(), notes_repo=self._notes())
        self.busy = False
        self.page.mark("bib_git_rollback", **{k: v for k, v in (result or {}).items()
                                              if k in ("ok", "reverted", "sha", "error")})
        if result.get("ok") and result.get("committed"):
            self.page.app.toast("已回退到上一个提交并生成新提交")
            self.page.stats_data = None
            self.loaded = False
            await self._load()
        elif result.get("ok"):
            # 防御：ok 但没有生成新提交时，不得显示「已生成新提交」。
            detail = str(result.get("error") or result.get("reason") or "未生成回退提交")
            self.page.app.toast("库内容已回退，但没有生成新提交：" + detail)
            self.page.stats_data = None
            self.loaded = False
            await self._load()
        else:
            self.page.app.toast("回退未完成：" + str(result.get("error") or "未知原因"))
            self.page._render()

    # 构建 ─────────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self._replace_refs = {}
        controls: list = [self._git_card(), self._conflicts_card()]
        if self.error:
            controls.append(C.notice_bar(self.error, t, "warn"))
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _git_card(self) -> ft.Control:
        t = self.t
        probe = self.probe or {}
        lines = [C.text(line, t, "small", t.palette.text) for line in git_summary_lines(probe)]
        warnings = probe.get("warnings") or []
        controls: list = [
            C.section_title("版本库", t, note="本地 git · 只提交 bib_db/bibliography.jsonl"),
            *lines,
        ]
        if not probe.get("ok"):
            controls.append(C.notice_bar(str(probe.get("error") or "版本库状态未读取"), t, "warn"))
        elif warnings:
            controls.append(C.notice_bar("；".join(str(item) for item in warnings[:3]), t, "warn"))
        commit = self.commit or {}
        if commit and not commit.get("committed"):
            detail = str(commit.get("error") or commit.get("reason") or "").strip()
            if detail:
                controls.append(C.notice_bar(
                    "版本层提示：上次写库已成功但没有生成版本提交（数据不回滚）：" + detail,
                    t, "warn"))
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("刷新", lambda e: self.page.page.run_task(self._git_refresh), t,
                               icon="REFRESH", disabled=self.busy),
            C.secondary_button("推送…", self._push, t, icon="CLOUD_UPLOAD",
                               disabled=self.busy or not probe.get("ok")),
            C.secondary_button("回退到上一个提交…", self._rollback, t, icon="UNDO",
                               disabled=self.busy or not probe.get("ok")),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _conflicts_card(self) -> ft.Control:
        t = self.t
        totals = self.totals or {}
        pending = _as_int(totals.get("pending"))
        total = _as_int(totals.get("total"))
        resolved = _as_int(totals.get("resolved"))
        controls: list = [
            C.section_title("待裁决", t, note="只读查询待裁决冲突"),
            ft.Row(spacing=t.sp("2"), wrap=False, controls=[
                C.stat_card("待裁决", str(pending), "写库后从此队列移除", t,
                            expand=False, width=t.px(150)),
                C.stat_card("冲突合计", str(total), f"已裁决 {resolved}", t,
                            expand=False, width=t.px(150)),
                C.stat_card("库指纹", sha_short(self.db_sha, 12), "写前校验库指纹", t,
                            expand=False, width=t.px(150)),
            ]),
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                C.secondary_button("刷新清单", lambda e: self.page.page.run_task(self._load), t,
                                   icon="REFRESH", disabled=self.busy),
                C.primary_button("一键全部采用…", self._adopt_all, t, icon="DONE_ALL",
                                 disabled=self.busy or not pending),
            ]),
        ]
        if self.busy and not self.loaded:
            controls.append(C.busy_bar(t, visible=True))
        if not self.items:
            controls.append(C.empty_state("CHECK_CIRCLE", "没有待裁决条目",
                                          "字段冲突都已处理（或库还没加载）。", t, height=140))
        else:
            cards = [self._conflict_card(item) for item in self.items[:50]]
            controls.append(ft.Column(spacing=t.sp("1"), controls=cards))
            if len(self.items) > 50:
                controls.append(C.text(f"… 其余 {len(self.items) - 50} 条未展开（先处理这 50 条）",
                                       t, "caption", t.palette.text_faint))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _conflict_card(self, item: dict) -> ft.Control:
        t = self.t
        key = str(item.get("record_key") or "")
        ref: list = []
        self._replace_refs[key] = ref
        diff_lines = conflict_diff_lines(item, limit=4)
        rows: list = [
            ft.Row(spacing=t.sp("1"), wrap=True, controls=[
                C.badge(f"C{_as_int(item.get('stable_no')):03d}", t, "accent"),
                C.text(conflict_title(item), t, "small", weight="medium"),
                ft.Container(width=t.px(8)),
                C.badge(conflict_where(item), t, "neutral"),
                C.badge(str(item.get("entry_type") or "—"), t, "neutral"),
            ]),
            C.text(f"记录 {key} · 作者 {item.get('author') or '—'} · 年份 "
                   f"{item.get('year') or '—'} · 冲突字段 {('、'.join(conflict_fields(item)) or '—')}",
                   t, "caption", t.palette.text_faint),
        ]
        if diff_lines:
            rows.append(ft.Column(spacing=t.sp("half"), controls=[
                C.text(line, t, "caption", t.palette.text) for line in diff_lines]))
        advice = str(item.get("advice") or "").strip()
        if advice:
            rows.append(C.text(advice, t, "caption", t.palette.text_muted))
        adopt_disabled = self.busy or not str(item.get("incoming_sha256") or "")
        rows.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("保留旧值…", lambda e, i=item: self._keep(i), t,
                               disabled=self.busy),
            C.primary_button("采用新稿值…", lambda e, i=item: self._adopt(i), t,
                             disabled=adopt_disabled),
            C.secondary_button("待查证…", lambda e, i=item: self._defer(i), t,
                               disabled=self.busy),
        ]))
        # 路径选择列（浮动字段 48）与「替换…」按钮同行等高（用户 m02816）。
        rows.append(C.field_row(
            C.path_picker("人工裁决 bib", "", "恰好 1 条条目，key 必须等于库内记录 key", t,
                          on_browse=lambda e, r=ref: self.page.app.browse_field(
                              r, "选择人工裁决 bib", "file", "bib_resolve_bib"),
                          required=False, show_note=False, field_ref=ref),
            C.secondary_button("替换…", lambda e, i=item: self._replace(i), t,
                               icon="SWAP_HORIZ", disabled=self.busy),
            t, height=t.px(48),
        ))
        return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)
