"""Issue assembly result/view composition (structural extraction).

This module owns ONLY the presentation composition methods extracted verbatim
from ``pages.IssuePage``: step body/page assembly, section headers, and
per-step result cards.  Behaviour (routing, state transitions, run machinery)
stays in ``pages.py`` and ``assembly.py``.  ``IssuePage`` delegates to these
functions so the original method names, signatures, callbacks and render order
are preserved.
"""
from __future__ import annotations

import os
from pathlib import Path

import flet as ft

from ui import assembly as ASM
from ui import checks as CK
from ui import components as C
from ui import dirpush as DIRPUSH
from ui import task_feedback as TF


def _step_body(self, key: str) -> list:
    """功能页正文（不含期目录行）：空态卡，或结果条 + 1–2 个区块。"""
    if not self.field("issue_dir"):
        return [self._build_empty_detail()]
    controls: list = []
    if self._result is not None:
        controls.append(self._result.root)
    for step_key in self._view_steps(self._view_of_step(key)):
        controls.append(self._build_section(step_key))
    return controls


def _build_step_page(self, key: str) -> ft.Control:
    """功能页：最上方是内嵌的期目录行，其下是该功能自己的 1–2 个区块。

    期目录行与正文分成两个兄弟节点：键入/换期只重建 `_step_box`，
    聚焦中的期目录输入框不进任何 patch。
    """
    t = self.t
    self._step_box = ft.Column(spacing=0, controls=self._step_body(key))
    return ft.Column(spacing=t.sp("2"),
                     controls=[self._build_issue_row(), self._step_box])


def _section_body_controls(self, key: str) -> list:
    """区块正文列的内容（说明 + 参数/结果区）：整页构建与换期原地刷新共用。"""
    t = self.t
    body: list = [
        C.text(self._step_desc(key), t, "small", t.palette.text_muted),
        C.hairline(t),
        C.section_label("参数", t,
                        note=(None if key == "infocheck" else "改动即时保存")),
    ]
    if key == "toc" and not self._toc_report_ready():
        body.append(self._toc_report_cta())
    body.append(self._build_params(key))
    if key == "dirpush":
        reason = DIRPUSH.validation_error(self)
        if reason and reason != "先选择期目录":
            body.append(C.inline_note(reason, t, "warn"))
    if key == "merge":
        body.append(self._prep_card())
    body.append(self._result_zone(key, banner=False))
    return body


def _build_section(self, key: str) -> ft.Control:
    """功能区块：区块头常显（标题 + 状态 + 本功能主按钮），内容手风琴展开。

    同页两区块只展开一个（`expanded_step`），默认展开第一个；区块间用 1px
    hairline 分隔，最后一个区块不再画底边。
    """
    t = self.t
    # FR-20261115：flet 1.0.5 会把「显式 key 相同、对象不同」的列表项按
    # frozen 语义比较并冻结新子树；换期重建后状态词便无法更新。代次 key
    # 每次重建都不同，补丁改走 remove+add，新控件不再被冻结。
    uid = getattr(self, "_section_uid", 0) + 1
    self._section_uid = uid
    steps = self._view_steps(self._view_of_step(key))
    expanded = self.expanded_step(self._view_of_step(key)) == key
    head = self._detail_header(key, uid)
    body_col = ft.Column(spacing=t.sp("2"),
                         controls=self._section_body_controls(key),
                         visible=expanded)
    self._section_bodies[key] = body_col
    last = bool(steps) and steps[-1] == key
    wrapper = ft.Container(
        key=f"issue-section-{key}-{uid}",
        border=None if last else ft.Border(
            bottom=ft.BorderSide(width=t.px(1), color=t.palette.border)),
        padding=C._pad(t, 0, 6, 0, 6),
        content=ft.Column(spacing=t.sp("2"), controls=[head, body_col]))
    self._section_refs[key] = wrapper
    return wrapper


def _detail_head_content(self, key: str, *, expanded: bool,
                         state: str) -> ft.Row:
    """区块头左半（标题 + 状态点/状态词 + chevron），不含右侧主按钮。

    MC-01：换期原地刷新只替换这段内容；`head` 容器与兄弟节点主按钮对象
    都保留，失焦后紧接着的点击不会落在被换掉的控件上。
    """
    t = self.t
    status = C.text(self._status_text(key), t, "caption", self._state_color(state))
    self._detail_statuses[key] = status
    chevron = C.text("▾" if expanded else "▸", t, "caption", t.palette.text_faint)
    self._chevrons[key] = chevron
    if key == self.selected:
        # 旧契约：`_detail_status` 指向当前选中区块的状态词。
        self._detail_status = status
    return ft.Row(spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        C.text(self._step_title(key), t, "section"),
        C.status_dot(self._tone(state), t),
        status,
        ft.Container(expand=True),
        chevron,
    ])


def _detail_header(self, key: str, uid: int) -> ft.Control:
    """区块头（40 高）：标题 + 状态 + 主按钮（展开 Filled / 收起 Outlined）+ chevron。

    点击区只包住左侧头部与 chevron；主按钮在点击区之外——点主按钮只运行
    该功能，不触发展开/收起。批 117 去掉了 01–06 序号列（不再按编号呈现）。
    """
    t = self.t
    expanded = self.expanded_step(self._view_of_step(key)) == key
    state = self._state_of(key)
    head = ft.Container(
        key=f"issue-head-{key}-{uid}", expand=True, height=t.px(40),
        alignment=ft.Alignment.CENTER_LEFT,
        on_click=lambda e, k=key: self._toggle_section(k),
        content=self._detail_head_content(key, expanded=expanded, state=state))
    self._section_heads[key] = head
    btn = self._primary_button(key, expanded=expanded)
    if key == self.selected:
        # 旧契约（`_detail_status`/`_step_run_primary`）指向当前选中区块。
        self._detail_primary = btn
        self._step_run_primary = btn
    return ft.Row(spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[head, btn])


def _result_zone(self, key: str, *, banner: bool = True) -> ft.Control:
    t = self.t
    parts: list = []
    if banner and self._result is not None:
        parts.append(self._result.root)
    if key == "infocheck":
        # 与 _toc_box/_merge_box 同思路：持久结果容器，步骤结束后
        # _sync_info_from_card 能原地刷新，不必等下一次整页重建。
        self._info_box = ft.Column(spacing=t.sp("2"), controls=[self._info_result()])
        parts.append(self._info_box)
    elif key == "toc":
        parts.append(self._toc_result_box())
    elif key == "merge":
        # P04 R05：结果卡原地刷新（on_step_started/_feed_merge/_merge_finish
        # 都通过 _merge_box 更新），不必等整页重建。
        self._merge_box = ft.Container(content=self._merge_result())
        parts.append(self._merge_box)
    elif key == "cleantemp":
        parts.append(self._cleanup.result())
    elif key == "dirpush":
        parts.append(self._dirpush_result())
    return ft.Column(spacing=t.sp("2"), controls=parts)


def _empty_result(self, title: str, desc: str, height: int = 72,
                  action: ft.Control | None = None) -> ft.Control:
    t = self.t
    inner = [C.text(title, t, "small", t.palette.text_muted),
             C.text(desc, t, "caption", t.palette.text_faint,
                    text_align=ft.TextAlign.CENTER)]
    if action is not None:
        inner.append(action)
        height = max(height, 120)
    return ft.Container(
        height=t.px(height), border=ft.Border.all(1, t.palette.border),
        border_radius=t.radius("md"), alignment=ft.Alignment.CENTER,
        content=ft.Column(spacing=t.sp("half"), horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=inner),
    )


def _info_result(self) -> ft.Control:
    t = self.t
    state = str(getattr(self, "_info_state", "idle") or "idle")
    payload = self._info_payload
    if state == "failed":
        code = getattr(self, "_info_code", None)
        suffix = f"（退出码 {code}）" if code is not None else ""
        return ft.Column(spacing=t.sp("1"), controls=[
            C.notice_bar(f"本次检查未完成{suffix}：原因见右上「整期日志」；"
                         "修好后可重新运行。", t, "error"),
        ])
    if state == "unreadable":
        report = self._info_report
        controls: list = [C.notice_bar(CK.UNREADABLE_INFO, t, "warn")]
        if report:
            controls.append(C.text(f"报告：{report}", t, "caption",
                                   t.palette.text_faint, max_lines=2))
            controls.append(C.secondary_button(
                "打开报告", self._artifact_open_action(report), t,
                icon="OPEN_IN_NEW", height=t.px(28)))
        return ft.Column(spacing=t.sp("1"), controls=controls)
    if state == "running":
        return self._empty_result(
            "正在检查…", "完成后这里显示结果；进度见右上「整期日志」。")
    if not payload:
        if not self.field("issue_dir"):
            return self._empty_result(
                "还没有选择期目录",
                "先选一期稿件的根目录，再运行检查；切到别的步再回来，结果还在。",
                action=C.primary_button("选择期目录", self._browse_issue, t,
                                        height=t.px(30), icon="FOLDER_OPEN"))
        return self._empty_result(
            "还没有检查结果",
            "点右上「运行检查」，或按 Enter；切到别的步再回来，结果还在。")
    articles = self._info_articles
    if not articles:
        # 已完成但 0 篇：与未运行分开说，给报告入口与下一步判据，不诱导重跑。
        report = self._info_report
        actions: list = []
        if report:
            actions.append(C.secondary_button(
                "打开报告", self._artifact_open_action(report), t,
                icon="OPEN_IN_NEW", height=t.px(28)))
        return ft.Column(spacing=t.sp("1"), controls=[
            C.section_label("检查结果", t, trailing=C.badge("只读", t, "success")),
            C.hairline(t),
            C.text(CK.info_summary(payload, articles=[], notes=[]), t, "small",
                   t.palette.text, max_lines=2),
            C.text("期目录里没有编号篇目文件夹（如「01 作者」）；报告已生成并保留，"
                   "可打开核对。若期目录确实有稿件，确认子目录以编号开头后再重新运行。",
                   t, "caption", t.palette.text_muted),
            C.text(f"报告：{report or '—'}", t, "caption", t.palette.text_faint,
                   max_lines=2),
            ft.Row(spacing=t.sp("1"), controls=actions) if actions else ft.Container(),
        ])
    verdict = CK.info_summary(payload, articles=articles, notes=self._info_notes)
    tone = "warn" if self._info_notes else "info"
    head = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[
        C.text(verdict, t, "small", t.palette.warn if self._info_notes else t.palette.text,
               max_lines=2, expand=True),
        CK.build_info_filter_chips(t, self._info_filter, self._set_info_filter),
    ])
    return ft.Column(spacing=t.sp("1"), controls=[
        C.section_label("检查结果", t, trailing=C.badge("只读", t, "success")),
        C.hairline(t),
        head,
        C.text(CK.info_counts(articles, self._info_notes), t, "caption",
               t.palette.text_muted),
        CK.build_info_table5(t, articles, active_filter=self._info_filter,
                             expanded=self._info_expanded,
                             on_toggle=self._toggle_info_row,
                             on_copy=self._copy_info_row),
        C.text(f"报告：{self._info_report or '—'}", t, "caption",
               t.palette.text_faint, max_lines=2),
    ])


def _toc_effective_payload(self) -> dict:
    """只认当前报告名下的结果：换报告后不沿用上一份的目录结果（OBS-2 / Lead C）。"""
    payload = self._toc_payload
    # Lead task-17(b)：与 _sync_toc_payload 同一口径解析（字段空时回退 latest 展示）。
    report = self._toc_report_path()
    owner = str(getattr(self, "_toc_payload_report", "") or "")
    if payload and report and owner and owner != report:
        self._toc_payload = {}
        self._toc_payload_report = ""
        self._toc_state_report = ""
        self._toc_run_failed = False
        self._toc_run_cancelled = False
        self._toc_run_code = None
        self._toc_run_report = ""
        self._toc_payload_this_run = False
        return {}
    return payload


def _toc_result_box(self) -> ft.Control:
    """结果区容器：_sync_toc_payload 原地重建它（与 _info_box 同思路）。"""
    box = getattr(self, "_toc_box", None)
    if box is None:
        box = ft.Column(spacing=self.t.sp("2"), controls=[])
        self._toc_box = box
    box.controls = [self._toc_result()]
    return box


def _toc_failure_note(self) -> ft.Control:
    t = self.t
    code = getattr(self, "_toc_run_code", None)
    return C.notice_bar(
        f"本次生成未成功（退出码 {code}）：原因见整期日志；修好后点「重新生成目录」。",
        t, "error")


def _toc_cancel_note(self) -> ft.Control:
    # Lead task-22（原生取消观察）：取消不回滚，000 目录可能已有本轮部分产物；
    # 结果区与参数区反馈统一改为「未生成结果摘要」的准确表述。
    return C.notice_bar(
        "本次已停止，未生成结果摘要；000 目录可能已部分更新，请重新生成后再使用。"
        "参数区保持可用，可重新运行。",
        self.t, "warn")


def _toc_summary_lines(self, payload: dict) -> list[str]:
    placed = payload.get("placed") or {}
    outputs = payload.get("outputs") or {}
    column = payload.get("column") or {}
    shown = (placed.get("zhPdf") or placed.get("zhTex")
             or outputs.get("zhTex") or outputs.get("enTex") or "—")
    # frozen #1 冒烟：无栏目时旧文案会印成「栏目：False（）」，改为读得懂的两态；
    # 启用栏目仍显示引擎给出的栏目名（不回落中文）。
    if column.get("enabled"):
        column_line = f"栏目：{column.get('title') or '—'}"
    else:
        column_line = "栏目：无"
    lines = [
        f"输出：{shown}",
        f"篇目：{(payload.get('articles') or {}).get('inToc', '—')} 篇",
        column_line,
        # frozen #1 冒烟：仅 tex 结论只出一行（旧版重复印了两次）。
        ("已编译 / 已写入" if payload.get("compiled")
         else "仅 tex · 未写入、未编译；合并需要 000 目录 PDF"),
    ]
    return lines


def _toc_result(self) -> ft.Control:
    """生成结果区（OBS-2）：只认当前报告 + 本轮的退出状态。

    本轮失败/取消时不渲染旧产物的打开按钮，旧结果只作「上次结果（非本轮）」
    文字参考（Lead m00363 B/D）；成功时只提供本次结果 JSON 里记录的产物。
    """
    t = self.t
    failed = bool(getattr(self, "_toc_run_failed", False))
    cancelled = bool(getattr(self, "_toc_run_cancelled", False))
    payload = self._toc_effective_payload()
    if not payload and not failed and not cancelled:
        return self._empty_result("还没有生成目录",
                                  "点右上「生成目录」；默认取最新信息检查报告。")
    head: list = [C.section_label("生成结果", t), C.hairline(t)]
    if not failed and not cancelled:
        # Lead round2 C1：只有本会话内、当前报告、rc 0/1 真跑完的那次才叫「本次」；
        # 重启/初次加载/换报告后读到的本期最近结果只能标「上次」（产物/按钮保留）。
        if bool(getattr(self, "_toc_payload_this_run", False)):
            head.append(C.notice_bar("本次生成完成。", t, "success"))
        else:
            head.append(C.text(
                "上次生成结果（非本轮，可能由其他检查报告触发），仅作参考。",
                t, "caption", t.palette.text_muted))
    if failed or cancelled:
        head.append(self._toc_cancel_note() if cancelled else self._toc_failure_note())
        if not payload:
            return ft.Column(spacing=t.sp("1"), controls=head)
        head.append(C.text("上次结果（非本轮，仅供参考）：", t, "caption",
                           t.palette.text_muted))
        return ft.Column(spacing=t.sp("1"), controls=[
            *head,
            *[C.text(line, t, "caption", t.palette.text_faint)
              for line in self._toc_summary_lines(payload)],
        ])
    placed = payload.get("placed") or {}
    outputs = payload.get("outputs") or {}
    lines = self._toc_summary_lines(payload)
    actions: list = []
    copy_path = ""
    zh_pdf = str(placed.get("zhPdf") or "")
    if zh_pdf:
        actions.append(C.text_button("打开 PDF", self._artifact_open_action(zh_pdf), t))
        copy_path = zh_pdf
    en_pdf = str(placed.get("enPdf") or "")
    if en_pdf:
        # 独立验收 task-6(2)：英文 PDF 与中文 PDF 同规格提供打开入口；仅 tex/
        # 失败/取消状态不会走到这里（placed 为空或前面已提前 return）。
        actions.append(C.text_button("打开英文 PDF",
                                     self._artifact_open_action(en_pdf), t))
        copy_path = copy_path or en_pdf
    shown: set[str] = set()
    for key, label in (("zhTex", "打开中文 tex"), ("enTex", "打开英文 tex")):
        path = str(placed.get(key) or outputs.get(key) or "")
        if not path or path in shown:
            continue
        shown.add(path)
        copy_path = copy_path or path
        actions.append(C.text_button(label, self._artifact_open_action(path), t))
    # Lead task-17(b)：渲染路径用非副作用解析（字段空时 latest 仅用于展示/打开），
    # 绝不能在一次渲染里把 toc_report 字段改回最新报告。
    if report := self._toc_report_path():
        from ui import toc as TOC  # 延后导入，避免 ui 模块循环依赖
        result_json = TOC.existing_result_json_path(report)
        if os.path.exists(result_json):
            actions.append(C.text_button("打开结果 JSON",
                                         self._artifact_open_action(result_json), t))
        else:  # B-121 B-357：可能已被「清理临时文件」移入回收目录
            lines.append("结果 JSON 不在期目录（可能随临时文件清入回收站）；需要时重新生成。")
        if copy_path:
            actions.append(C.text_button("复制结果路径",
                                         lambda e: self.app.copy_text(copy_path), t))
    return ft.Column(spacing=t.sp("1"), controls=[
        *head,
        *[C.text(line, t, "caption", t.palette.text_muted) for line in lines],
        ft.Row(spacing=t.sp("1"), wrap=True, controls=actions) if actions else ft.Container(),
    ])


def _dirpush_result(self) -> ft.Control:
    t = self.t
    status = self._status.get("dirpush")
    artifact = str(getattr(status, "artifact", "") or "")
    state = self._state_of("dirpush")
    payload = getattr(self, "_dirpush_payload", None)
    if not artifact and state != "error" and not payload:
        return self._empty_result("还没有推送记录",
                                  "在上方选好输出根与范围，点右上「生成摘要」。")
    rows = [C.section_label("推送结果", t), C.hairline(t)]
    if artifact:
        rows.append(C.text(os.path.basename(artifact), t, "small", t.palette.text))
    if payload:
        # BUG-07：结果卡只渲染本次引擎 JSON；ok=false 明确说没生成，绝不显示成功卡。
        if not payload.get("ok", False):
            rows.append(C.inline_note("推送失败：引擎报告本次没有生成。", t, "error"))
        rows += [C.text(line, t, "caption", t.palette.text_muted)
                 for line in DIRPUSH.summary_lines(payload)]
    if state == "cancelled":
        rows.append(C.inline_note("已取消当前步骤：本次没有生成推送产物。", t, "warn"))
    if state == "error":
        # 失败通常没有产物；先渲染原因，不能因为 artifact 为空退回空态（B P2-1）。
        tail = self._tail_lines("dirpush", 3)
        rows.append(C.inline_note("推送失败：原因在下面末 3 行（日志可展开看全文）。",
                                  t, "error"))
        rows += [C.text(line, t, "mono", t.palette.error, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS) for line in tail]
    if artifact:
        rows.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.text_button("打开 docx", self._artifact_open_action(artifact), t),
            C.text_button("打开所在目录",
                          self._artifact_open_action(os.path.dirname(artifact)), t),
            C.text_button("复制路径", lambda e: self.app.copy_text(artifact), t),
        ]))
    return ft.Column(spacing=t.sp("1"), controls=rows)


def _merge_shell(self, rows: list, *, path: str = "",
                 actions: list | None = None) -> ft.Control:
    """合并结果卡骨架：标题 + 分隔线 + 文案行 + 产物路径 + 打开动作。"""
    t = self.t
    controls: list = [C.section_label("合并结果", t), C.hairline(t)]
    controls.extend(rows)
    if path:
        controls.append(C.text(path, t, "mono", t.palette.text_faint,
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
    if actions:
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=actions))
    return ft.Column(spacing=t.sp("1"), controls=controls)


def _merge_line(self, text: str, tone: str = "") -> ft.Control:
    t = self.t
    color = {"warn": t.palette.warn, "error": t.palette.error}.get(
        tone, t.palette.text_muted)
    return C.text(text, t, "caption", color)


def _merge_rows(self, rows: list) -> list:
    return [self._merge_line(text, tone) for text, tone in rows]


def _merge_product_ok(path: str) -> bool:
    """R06：产物是否仍在原位且可识别（存在 + 可读 + PDF 头）。

    只做读取判断，不移动/改名/扫描历史合并文件；读不了就当「无法识别」。
    """
    if not path:
        return False
    try:
        target = Path(path)
        if not target.is_file():
            return False
        with open(target, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def _merge_actions(self, path: str) -> list:
    return [
        C.text_button("打开文件", self._artifact_open_action(path), self.t),
        C.text_button("打开所在目录",
                      self._artifact_open_action(os.path.dirname(path)),
                      self.t),
        C.text_button("复制路径", lambda e: self.app.copy_text(path), self.t),
    ]


def _merge_result(self) -> ft.Control:
    t = self.t
    info = dict(self._merge_info or {})
    run = dict(getattr(self, "_merge_run", None) or {})
    state = str(run.get("state") or "")
    # R05-1/R05-2：本次运行进行中/被取消/失败时，结果卡只描述本次，绝不回落到
    # 上一轮成功结果（旧 _merge_info 已在 on_step_started 清空）。
    if state == "running":
        return self._merge_shell(self._merge_rows([
            ("本次合并正在运行…", ""),
            ("跑完前不显示上一轮结果；要中止点右上「停止」。", ""),
        ]))
    if state == "cancelled":
        return self._merge_cancelled_card(info)
    if state == "error":
        return self._merge_failed_card(info)
    if not info:
        return self._merge_previous_card(run)
    try:
        pages = int(info.get("pages") or 0)
    except (TypeError, ValueError):
        pages = 0
    path = str(info.get("path") or "")
    if pages <= 0:
        return self._merge_shell([
            C.inline_note("没有可合并的 PDF：先完成编译，或用「先检查」"
                          "确认期目录里的 PDF 是否可读。", t, "warn"),
        ], path=path)
    # R04：只有 rc=0 才能说「合并完成」；rc=1 显示「需人工核对」。
    rows: list = []
    if run.get("code") == 1:
        rows.append((f"需人工核对 · {pages} 页", "warn"))
    else:
        rows.append((f"合并完成 · {pages} 页", ""))
    try:
        missing = int(info.get("missing") or 0)
    except (TypeError, ValueError):
        missing = 0
    if missing:
        rows.append((f"缺 {missing} 篇：{info.get('names', '')}", "warn"))
    # P04-O2：引擎的 ⚠️ 会在文本开头留下 U+FE0F，渲染前统一清掉。
    warnings = TF.clean_warnings(info.get("warnings") or [])
    if warnings:
        rows.append((f"warning {len(warnings)} 条", "warn"))
        rows.extend((f"· {w}", "warn") for w in warnings[:5])
        if len(warnings) > 5:
            rows.append((f"……另有 {len(warnings) - 5} 条，见日志", "warn"))
    # R06：只有本次运行（有归属）才校验产物；旧格式（无 run）保持原样兼容。
    ok = self._merge_product_ok(path) if state else bool(path)
    if state and path and not ok:
        rows.append((f"产物不在原位/无法识别：{path}", "warn"))
    actions = self._merge_actions(path) if (path and ok) else []
    return self._merge_shell(self._merge_rows(rows), path=path, actions=actions)


def _merge_cancelled_card(self, info: dict) -> ft.Control:
    t = self.t
    rows: list = [("本次已取消/未完成：没有生成本次合并产物。", "warn")]
    target = str(self.field("merge_out") or "")
    # R05-2：原目标（上一次的产物）仍在时，只能明确标注「原有目标未改动」，
    # 不把它当成本次产物、不自动打开。
    if target and self._merge_product_ok(target):
        rows.append((f"原有目标未改动（上次结果在 {target}）", ""))
    elif target:
        rows.append((f"输出路径未改变：{target}（本次没有写出文件）", ""))
    else:
        rows.append(("输出路径未改变（本次没有写出文件）。", ""))
    return self._merge_shell(self._merge_rows(rows))


def _merge_failed_card(self, info: dict) -> ft.Control:
    rows: list = [("本次合并失败：没有生成本次产物。", "error")]
    reason = str(info.get("error") or "").strip()
    if reason:
        rows.append((reason, "error"))
    tail = [ln for ln in (self._step_tail.get("merge") or [])
            if ln and not ln.startswith(("▶", "■"))]
    for line in tail[-2:]:
        rows.append((line, ""))
    return self._merge_shell(self._merge_rows(rows))


def _merge_previous_card(self, run: dict) -> ft.Control:
    """没有本次运行记录时的结果卡（R06：上次产物需明确标注，不可静默冒充）。"""
    status = self._status.get("merge")
    artifact = str(getattr(status, "artifact", "") or "")
    state = str(getattr(status, "state", "") or "")
    if artifact and state in ("done", "warn"):
        if self._merge_product_ok(artifact):
            return self._merge_shell(self._merge_rows([
                ("上次合并结果（本次没有重新合并）", ""),
                ("上次产物仍在原位；重新合并后以本次结果为准。", ""),
            ]), path=artifact, actions=self._merge_actions(artifact))
        return self._merge_shell(self._merge_rows([
            ("上次产物不在原位/无法识别：", "warn"),
            (artifact, "warn"),
            ("本次没有重新合并；旧路径没有改写。", ""),
        ]))
    return self._empty_result("还没有运行记录。",
                              "第 5 步还没跑 —— 先在「刷新页码」编译，合并才不会缺篇。")


def _prep_card(self) -> ft.Control:
    """只读前置页卡：mergepdf --detect 的扉页/编委页/封底结果。

    批 116 范围（用户拍板 I-2）：**只显示不编辑**；编辑前置页是新引擎子系统
    （数据/模板/编译预览），已入想法池。空态说明「点检测」；有数据态显示
    文件名/页数/缺失，并提供「打开」。
    """
    t = self.t
    self._prep_box = ft.Container(
        content=ft.Column(spacing=t.sp("1"), controls=self._prep_rows()))
    return ft.Column(spacing=t.sp("1"), controls=[
        C.section_label("前置页", t, note="只读 · 不提供编辑"),
        self._prep_box,
        ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   C.secondary_button("检测前置页", lambda e: ASM.run_detect(self), t,
                                      icon="SEARCH", height=t.px(28)),
                   C.text("只探测扉页 / 编委页 / 封底，不合并；缺失的页合并时跳过。",
                          t, "caption", t.palette.text_faint, expand=True),
               ]),
    ])


def _prep_rows(self) -> list:
    """前置页卡内容（空态 / 有数据态），供 `_prep_card` 与检测后刷新共用。"""
    t = self.t
    payload = self._prep_pages
    if not payload:
        return [
            C.text("还没检测前置页", t, "small", t.palette.text_muted),
            C.text("点「检测前置页」列出扉页、编委页与封底；暂不支持编辑。", t, "caption", t.palette.text_faint),
        ]
    return [
        self._prep_row("扉页", payload.get("coverPages"), count=True),
        self._prep_row("编委页", payload.get("editorsPage")),
        self._prep_row("封底", payload.get("backCover")),
    ]


def _prep_row(self, label: str, value, *, count: bool = False) -> ft.Row:
    t = self.t
    path = ""
    if count:
        pages = int(value or 0)
        found = pages > 0
        detail = f"扉页.pdf · {pages} 页" if found else "未检测到扉页.pdf"
        if found:
            path = os.path.join(self._issue_dir(), "扉页.pdf")
    else:
        info = value if isinstance(value, dict) else {}
        name = str(info.get("fileName") or "")
        pages = int(info.get("pageCount") or 0)
        found = bool(name)
        detail = f"{name} · {pages} 页" if found else "未检测到"
        if found:
            path = os.path.join(self._issue_dir(), name)
    controls: list = [
        ft.Container(width=t.px(48),
                     content=C.text(label, t, "small", t.palette.text_muted)),
        C.text(detail, t, "small",
               t.palette.text if found else t.palette.text_faint,
               expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        C.badge("已找到" if found else "缺失", t,
                "success" if found else "warn"),
    ]
    if path:
        controls.append(C.text_button(
            "打开", self._artifact_open_action(path), t))
    return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=controls)


def _tail_lines(self, key: str, n: int = 3) -> list[str]:
    """该功能最近 n 行输出（失败时内联给读者，不必展开日志）。"""
    lines = [line for line in (self._step_tail.get(key) or []) if line]
    if not lines:
        lines = [line for line in self._log_buffer[-n:] if str(line).strip()]
    return [str(line) for line in lines[-n:]]


def _cleantemp_result(self) -> ft.Control:
    """兼容旧调用：结果区已由 CleanupPanel 自更新（绑 root+operationId）。"""
    return self._cleanup.result()
