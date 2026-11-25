"""Per-request compile-fix disclosure and explicit consent dialog."""
from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

import flet as ft

from ui import components as C

TERMS = (
    "我已核对本次候选稿、诊断摘要及完整脱敏请求正文。",
    "我确认本次显示的服务目标、模型、协议和工具效果。",
    "我已根据下方来源，人工核对适用于本次实际目标及账号的数据留存与训练条款。",
    "我同意本次发送，并知悉原始请求与回件按所示本机保留与删除规则处理。",
)
_SUMMARY_FIELDS = (
    "base", "target", "model", "protocol", "tools", "max_tokens", "thinking",
    "article_budget_tokens", "batch_budget_tokens", "identity_sha256", "scope_sha256",
    "body_sha256", "body_bytes",
)


def _dismiss_current(page, dialog) -> bool:
    if not C._open_dialogs or C._open_dialogs[-1] is not dialog:
        return False
    return C.dismiss_top_dialog(page)


def _terms_target(target) -> tuple | None:
    if (not isinstance(target, str) or not target.isascii()
            or any(not 33 <= ord(ch) <= 126 or ch in "?#@\\" for ch in target)):
        return None
    try:
        url = urlsplit(target)
        host, port = url.hostname, url.port
    except ValueError:
        return None
    if not host or url.scheme not in {"https", "http"}:
        return None
    return url.scheme, host, port if port is not None else (443 if url.scheme == "https" else 80)


def _budget_disclosure(summary, body: str) -> str:
    def budget(key):
        value = summary.get(key)
        if type(value) is not int or value < 0:
            return "未知"
        return f"{value:,} tokens" if value else "不限 (0)"

    chars = len(body)
    estimated = (chars + 2) // 3
    output = summary.get("max_tokens")
    if type(output) is int and output > 0:
        total = f"输出上限：{output:,} tokens；输入 + 输出上限粗估约 {estimated + output:,} tokens"
    elif output == 0:
        total = "输出上限：不限 (0)；输出无界，总量未知，不能给出有限总量估算"
    else:
        total = "输出上限未知；不能给出有限总量估算"
    return (f"全局配置：单篇 {budget('article_budget_tokens')}；批量 {budget('batch_budget_tokens')}。\n"
            f"本次冻结请求正文：{chars:,} 字符，按约 3 字符/token 向上取整，输入粗估约 {estimated:,} tokens。\n"
            f"{total}。字符/token 为粗略估算，仅供审阅，不代表服务商计费或实际用量。")


def show_authorization_dialog(page, tokens, prepared: dict, on_send) -> ft.AlertDialog:
    """Render one prepared request; every invocation starts with all terms unchecked."""
    t = tokens
    summary = prepared.get("summary") or prepared.get("authorization")
    if not isinstance(summary, dict):
        summary = {}
    body = str(prepared.get("body") or "")
    target, base = summary.get("target"), summary.get("base")
    summary_snapshot = copy.deepcopy(summary)
    origin = _terms_target(target)
    target_matches = (origin is not None and origin == _terms_target(base)
                      and target.startswith(base.rstrip("/") + "/"))
    target_label = f"目标主机：{origin[1]}" if origin else "目标主机：无效"
    terms_note = (f"{target_label} · 实际目标：{target}。需人工按下方来源核验适用条款；"
                  "工具不联网，也不验证来源真实性或条款内容。"
                  if target_matches else "实际发送目标无效或与配置地址不匹配；本次不能发送。")
    terms_fields = [C.text_field(t, label=label, hint_text=hint, max_length=240, clearable=False)
                    for label, hint in (
                        ("可核对条款来源（本次目标/账号）", "填政策名称/版本及可核对 URL 或章节定位；工具不验证真实性"),
                        ("留存条款核验记录", "按上述来源人工记录适用对象、期限/触发条件及例外"),
                        ("训练条款核验记录", "按上述来源人工记录允许、禁止或条件及其适用范围"),
                    )] if target_matches else []
    checks: list[ft.Checkbox] = []
    checks_column = ft.Column(spacing=t.sp("half"), scroll=C.scrollbar(t),
                              height=t.px(145))
    for clause in TERMS:
        checkbox = ft.Checkbox(value=False, label=clause)
        checks.append(checkbox)
        checks_column.controls.append(checkbox)
    send_button = C.primary_button("确认并发送一次", lambda e: None, t)
    send_button.disabled = True

    confirmed_terms = None

    def terms_record():
        return tuple((field.value or "").strip() for field in terms_fields)

    def summary_unchanged():
        return summary_snapshot == summary

    def terms_binding():
        return (target, base, summary_snapshot.get("model"), summary_snapshot.get("protocol"),
                summary_snapshot.get("tools"), summary_snapshot.get("identity_sha256"),
                summary_snapshot.get("scope_sha256"), summary_snapshot.get("body_sha256"))

    def can_send() -> bool:
        record = terms_record()
        return (target_matches and summary_unchanged() and bool(record) and all(record)
                and confirmed_terms == (terms_binding(), record)
                and all(box.value is True for box in checks))

    def update_send(_=None) -> None:
        send_button.disabled = not can_send()
        try:
            send_button.update()
        except Exception:  # noqa: BLE001 - dialog may not be mounted yet
            pass

    def close() -> None:
        _dismiss_current(page, dialog)

    def verify_terms(_=None) -> None:
        nonlocal confirmed_terms
        record = terms_record()
        confirmed_terms = ((terms_binding(), record) if checks[2].value is True and record
                           and all(record) and summary_unchanged() else None)
        if confirmed_terms is None:
            checks[2].value = False
            try:
                checks[2].update()
            except (AssertionError, RuntimeError):
                pass
        update_send()

    def invalidate_terms(_=None) -> None:
        checks[2].value = False
        verify_terms()

    async def send(e) -> None:
        if not can_send() or not _dismiss_current(page, dialog):
            return
        result = on_send(dict(prepared), True)
        if hasattr(result, "__await__"):
            await result

    send_button.on_click = send
    for box in checks:
        box.on_change = update_send
    checks[2].on_change = verify_terms
    for field in terms_fields:
        field.on_change = invalidate_terms
    details = json.dumps({key: summary[key] for key in _SUMMARY_FIELDS if key in summary},
                         ensure_ascii=False, indent=2)
    dialog = ft.AlertDialog(
        modal=True,
        title=C.text("审阅本次编译修复请求", t, role="dialog-title"),
        content=ft.Container(
            width=t.px(620), height=t.px(560),
            content=ft.Column(spacing=t.sp("1"), scroll=C.scrollbar(t), controls=[
                C.text("目标与有效设置", t, role="body-strong"),
                C.text(details, t, role="caption", selectable=True),
                C.text(_budget_disclosure(summary, body), t, role="caption", selectable=True),
                C.text("本次范围", t, role="body-strong"),
                C.text(json.dumps(prepared.get("scope") or [], ensure_ascii=False, indent=2),
                       t, role="body", selectable=True),
                C.text(f"稿件任务：{prepared.get('task_id', '')} · 预览：{prepared.get('preview_id', '')}", t, "caption"),
                C.text("完整脱敏请求正文", t, role="body-strong"),
                C.text(body, t, role="body", font_family="monospace", selectable=True),
                C.text(f"请求 SHA-256：{prepared.get('request_sha256', '')}", t, "caption"),
                C.text(f"本机 API Key：{'已配置' if summary.get('key_configured') is True else '未配置'}", t, "caption"),
                C.text("套接字操作超时：60 秒（不是整轮请求总时限）；原始回件上限：65536 字节。", t, "caption"),
                C.text("仅本机记录保留 30 天；可从编译修复状态删除，不代表服务商留存期限。", t, "caption"),
                C.text("按来源人工核验实际目标的留存与训练条款", t, role="body-strong"),
                C.text(terms_note, t, role="caption", selectable=True),
                *terms_fields,
                C.text("来源与核验说明须由用户人工核对；本工具不验证其真实性，也不保存这些说明。", t, "caption"),
                C.text("逐项确认后才会发送；关闭此窗口即取消本次授权。", t, "caption"),
                checks_column,
            ])),
        actions=[C.text_button("取消", lambda e: close(), t), send_button],
    )
    C.show_dialog(page, dialog)
    return dialog


def show_reply_dialog(page, t, on_import) -> None:
    reply = C.text_field(t, label="回件正文", multiline=True, min_lines=6, max_lines=12,
                         clearable=False)

    def submit(e):
        if reply.value and reply.value.strip() and _dismiss_current(page, dialog):
            on_import(reply.value)

    dialog = ft.AlertDialog(
        modal=True, title=C.text("导入编译修复回件", t, role="dialog-title"),
        content=ft.Container(width=t.px(540), content=reply),
        actions=[C.text_button("取消", lambda e: _dismiss_current(page, dialog), t),
                 C.primary_button("解析回件", submit, t)])
    C.show_dialog(page, dialog)


def show_scope_dialog(page, t, evidence: list[dict], diagnostics: list[dict], on_prepare,
                      selected: list[str] | None = None) -> None:
    selected_ids = set(selected or (item.get("evidence_id") for item in evidence))
    checks: list[ft.Checkbox] = []
    rows = []
    for index, item in enumerate(evidence):
        evidence_id = str(item.get("evidence_id") or "")
        diagnostic_index = item.get("diagnostic_index", index)
        diagnostic = diagnostics[diagnostic_index] if isinstance(diagnostic_index, int) and 0 <= diagnostic_index < len(diagnostics) else {}
        line = diagnostic.get("source_line", "?")
        message = diagnostic.get("message") or diagnostic.get("summary") or diagnostic.get("text") or "编译诊断"
        box = ft.Checkbox(value=evidence_id in selected_ids,
                          label=f"{evidence_id} · 第 {line} 行 · {message}")
        checks.append(box)
        rows.append(box)

    async def prepare(e):
        selected = [str(item.get("evidence_id")) for item, box in zip(evidence, checks) if box.value is True]
        if not selected or not _dismiss_current(page, dialog):
            return
        result = on_prepare(selected)
        if hasattr(result, "__await__"):
            await result

    dialog = ft.AlertDialog(
        modal=True, title=C.text("选择本轮外发诊断范围", t, role="dialog-title"),
        content=ft.Container(width=t.px(560), height=t.px(420), content=ft.Column(
            scroll=C.scrollbar(t), controls=[C.text("只可选择本轮签发的诊断证据；范围变更后须重新准备并授权。", t, "caption"), *rows])),
        actions=[C.text_button("取消", lambda e: _dismiss_current(page, dialog), t),
                 C.primary_button("准备所选范围", prepare, t)])
    C.show_dialog(page, dialog)


def show_review_dialog(page, t, result: dict, on_apply) -> None:
    async def apply(e):
        if _dismiss_current(page, dialog):
            await on_apply()

    actions = [C.text_button("关闭", lambda e: _dismiss_current(page, dialog), t)]
    if on_apply is not None:
        actions.append(C.primary_button("采纳并隔离复编", apply, t))
    detail = json.dumps(result.get("actions") or [], ensure_ascii=False, indent=2)
    dialog = ft.AlertDialog(
        modal=True, title=C.text("审阅编译修复差异", t, role="dialog-title"),
        content=ft.Container(width=t.px(600), height=t.px(440), content=ft.Column(
            scroll=C.scrollbar(t), controls=[
                C.text(result.get("diff") or "没有可采纳的正文差异。", t, role="body", selectable=True),
                C.text(detail, t, role="body", selectable=True),
            ])), actions=actions)
    C.show_dialog(page, dialog)


def show_delivery_dialog(page, t, on_confirm) -> None:
    def confirm(e):
        if _dismiss_current(page, dialog):
            on_confirm()

    dialog = ft.AlertDialog(
        modal=True, title=C.text("确认验版并交付", t, role="dialog-title"),
        content=ft.Container(width=t.px(360), content=C.text(
            "确认已检查本轮复编 PDF 的版面，并将本轮修复写入最终交付目录。", t)),
        actions=[C.text_button("取消", lambda e: _dismiss_current(page, dialog), t),
                 C.primary_button("确认交付", confirm, t)])
    C.show_dialog(page, dialog)


def show_retained_dialog(page, t, *, message, record=None, records=None, on_delete=None) -> None:
    content = [C.text(message, t)]
    actions = [C.text_button("关闭", lambda e: _dismiss_current(page, dialog), t)]
    entries = list(records) if records is not None else ([record] if record is not None else [])
    for index, item in enumerate(entries, 1):
        content.append(C.text(
            f"记录 {index} · 预览：{item['preview_id']}\n状态：{item['status']} · "
            f"已保存回件：{'有' if item['has_reply'] else '无'}\n"
            f"本机记录到期：{item['expires_at'] if item['expires_at'] is not None else '未提供'}\n"
            f"记录键：{item['record_key']}", t, role="body", selectable=True))

        def delete(e, selected=item):
            if _dismiss_current(page, dialog) and callable(on_delete):
                on_delete(selected)

        actions.append(C.secondary_button(f"删除记录 {index} · {item['preview_id']}", delete, t))
    dialog = ft.AlertDialog(modal=True, title=C.text("编译修复本机留存", t, role="dialog-title"),
                            content=ft.Container(width=t.px(440), content=ft.Column(tight=True, controls=content)),
                            actions=actions)
    C.show_dialog(page, dialog)
