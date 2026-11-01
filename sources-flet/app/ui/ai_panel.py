"""单篇稿件页的 AI 卡 + 设置页 AI 段（Flet 控件层，只做取值/拼参数/显示）。

红线（任务书批 107 P6-2）：
· 两个入口**默认关**；不勾就完全走普通初排，不拼任何 AI 参数；
· 协同初排零上传：第一段只出任务书，第二段只读本地回填文件；
· 直连核实会产生费用：Key 只从设置页/环境变量取，**argv 与日志里永远没有 Key**；
· 预算闸由引擎在发请求前执行（exit 2、零请求），界面把结果改写成「预算不足，未发起请求」；
· 不做 `--llm-fulltext`（整篇正文直连核查不在 Flet 范围）。
"""
from __future__ import annotations

import os

import flet as ft

from services import ai_prefs as AI
from services import engine_bridge as EB
from ui import components as C


class AiPanel:
    """「AI 协同初排（两段式）」+「AI 核实（直连 API）」两个入口。"""

    def __init__(self, page) -> None:  # page: SinglePage
        self.page = page
        self.app = page.app
        self.t = page.t
        self._coop = False
        self._direct = False
        self._fulltext = False
        self._scope = AI.FULLTEXT_CONTEXT
        self._apply = False
        self._no_ai_merge = True
        self._stage_field: ft.TextField | None = None
        self._stage_user = False
        self._reply_fields: list[ft.TextField] = []
        self._reply_box: ft.Column | None = None
        self._coop_box: ft.Column | None = None
        self._direct_box: ft.Column | None = None
        self._scope_row: ft.Row | None = None
        self._fulltext_chk: ft.Checkbox | None = None
        self._mode_ctl: ft.Text | None = None
        self._status_ctl: ft.Text | None = None
        self._summary_ctl: ft.Text | None = None
        self._buttons: list[ft.Control] = []
        self._lines: list[str] = []
        self._last_mode = AI.MODE_PLAIN

    # ── 构建 ───────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t, p = self.t, self.t.palette
        ai = AI.from_store(self.app.store)
        if not ai["enabled"]:
            return C.card(ft.Column(spacing=t.sp("1"), controls=[
                C.section_title("AI 助手", t, note="总开关关着，本页不出现任何 AI 入口"),
                C.notice_bar("AI 功能已在「设置 → AI」关闭：这里不显示任何 AI 选项，也不会有任何 API 调用。",
                             t, tone="info"),
            ]), t)

        self._coop_box = ft.Column(spacing=t.sp("1"), visible=False, controls=self._coop_controls())
        self._direct_box = ft.Column(spacing=t.sp("1"), visible=False, controls=self._direct_controls())
        self._mode_ctl = C.text("本次运行：普通初排（不碰 AI）", t, "small", p.accent, "medium")
        self._status_ctl = C.text("", t, "caption", p.text_muted, max_lines=12, selectable=True)
        self._summary_ctl = C.text("", t, "small", p.text_muted, max_lines=8, selectable=True)
        open_settings = C.text_button("打开 AI 设置", lambda e: self.app.goto("settings"), t)
        self._buttons.append(open_settings)
        card = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("AI 助手", t, note="默认关；勾了才拼 AI 参数"),
            C.option_row("AI 协同初排（两段式）",
                         "第一段出核实任务书 → 编辑交给任意 chat 模型 → 回填后第二段导入；零上传",
                         t, icon="SYNC_ALT", checked=self._coop, on_change=self._on_coop),
            self._coop_box,
            C.option_row("AI 核实（直连 API）",
                         "把文献元数据发给模型补全；会产生费用，禁止未刊稿",
                         t, icon="CLOUD_UPLOAD", checked=self._direct, on_change=self._on_direct),
            self._direct_box,
            C.notice_bar("直连核实会产生 API 费用；禁止把未刊稿发给任何外部模型（含 AI）。", t, tone="warn"),
            self._mode_ctl,
            self._status_ctl,
            self._summary_ctl,
            ft.Row(spacing=t.sp("1"), controls=[open_settings]),
        ]), t)
        self._refresh_mode()
        return card

    def _coop_controls(self) -> list[ft.Control]:
        t = self.t
        self._fulltext_chk = ft.Checkbox(value=self._fulltext, on_change=self._on_fulltext)
        self._fulltext_row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        self._fulltext_chk,
                                        C.text("把正文放进任务书（--coop-fulltext）", t, "small"),
                                        C.badge("谨慎", t, tone="warn"),
                                    ])
        self._scope_row = ft.Row(spacing=t.sp("1"), visible=self._fulltext,
                                 controls=self._scope_controls())
        self._stage_field = ft.TextField(
            value="", dense=True, text_size=t.font("body"), expand=True,
            hint_text="留空 = 按输出根目录与源稿自动发现",
            on_change=self._on_stage_input,
        )
        self._reply_fields = []
        self._reply_box = ft.Column(spacing=t.sp("1"), controls=[self._reply_row()])
        add = C.secondary_button("添加回填文件", lambda e: self._add_reply(None), t, icon="ADD")
        discover = C.secondary_button("自动发现暂存目录", lambda e: self._auto_discover(), t,
                                      icon="SEARCH")
        self._buttons.extend([add, discover])
        apply_row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Checkbox(value=self._apply, on_change=self._on_apply),
            C.text("第二段直接改产物（--coop-apply）", t, "small"),
            C.text("不勾 = 只记录建议，产物不动", t, "caption", t.palette.text_faint),
        ])
        return [
            C.text("第一段不交付、不联网；第二段只读本地回填文件。", t, "caption", t.palette.text_faint),
            self._fulltext_row,
            self._scope_row,
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[C.text("暂存目录", t, "small"), self._stage_field, discover]),
            C.text("回填文件（一份回复一个文件；路径可粘贴）", t, "small", t.palette.text_muted),
            self._reply_box,
            add,
            apply_row,
        ]

    def _direct_controls(self) -> list[ft.Control]:
        t, p = self.t, self.t.palette
        ai = AI.from_store(self.app.store)
        channel = AI.channel_state(ai)
        info = (f"{ai['base']} · {ai['model']} · {ai['protocol']} · "
                f"思考{'开' if ai['thinking'] else '关'} · 预算 {ai['article_budget_tokens']}"
                f"/{ai['batch_budget_tokens']}")
        key_hint = ("已配置 Key：" + channel["key_label"]) if ai["key"] else "未配置 Key：直连入口不可用，先去设置页填"
        preview = ft.Column(spacing=0, controls=[
            C.text("上传预览（只发文献元数据，正文永不发送）", t, "small", p.text, "medium"),
            *[C.text(f"· {label}：{text}", t, "caption", p.text_muted)
              for label, text in AI.upload_preview(self._upload_values(ai))],
            C.text("· " + AI.UPLOAD_ALWAYS, t, "caption", p.success),
        ])
        merge_row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Checkbox(value=self._no_ai_merge, on_change=self._on_merge),
            C.text("不自动并回产物（--no-ai-merge）", t, "small"),
        ])
        return [
            C.text(info, t, "caption", p.text_muted),
            C.text(key_hint, t, "caption", p.text_muted if ai["key"] else p.warn),
            preview,
            merge_row,
            C.text("核实范围上限在设置 → AI 的「单篇核实条数」；0 = 不限。", t, "caption", p.text_faint),
        ]

    def _upload_values(self, ai: dict) -> dict:
        source = ""
        try:
            source = os.path.basename(self.page.field("single_source") or "")
        except Exception:  # noqa: BLE001 - 未挂载/未选择时按空处理
            source = ""
        return {
            "source_name": source,
            "taskbook_brief": "核实规范、回答格式与逐条缺项提示（引擎任务书正文）",
            "budget_tokens": f"{ai['article_budget_tokens']}（批量 {ai['batch_budget_tokens']}）",
        }

    def _scope_controls(self) -> list[ft.Control]:
        t = self.t
        return [
            C.text("范围", t, "small", t.palette.text_muted),
            C.chip(AI.FULLTEXT_CONTEXT, t, active=self._scope == AI.FULLTEXT_CONTEXT,
                   on_click=lambda e: self._set_scope(AI.FULLTEXT_CONTEXT)),
            C.chip(AI.FULLTEXT_FULL, t, active=self._scope == AI.FULLTEXT_FULL,
                   on_click=lambda e: self._set_scope(AI.FULLTEXT_FULL)),
            C.text("开启后任务书会带正文片段/整篇；交外部模型前请确认未刊稿许可", t, "caption",
                   t.palette.text_faint),
        ]

    # ── 事件 ───────────────────────────────────────────────────────────
    def _on_coop(self, e) -> None:
        self._coop = bool(e.control.value)
        if self._coop_box is not None:
            self._coop_box.visible = self._coop
        self._refresh_mode()
        self._safe_update(self._coop_box)

    def _on_direct(self, e) -> None:
        self._direct = bool(e.control.value)
        if self._direct_box is not None:
            self._direct_box.visible = self._direct
        self._refresh_mode()
        self._safe_update(self._direct_box)

    def _on_fulltext(self, e) -> None:
        self._fulltext = bool(e.control.value)
        if self._fulltext_chk is not None:
            self._fulltext_chk.value = self._fulltext
        if self._scope_row is not None:
            self._scope_row.visible = self._fulltext
        self._refresh_mode()
        self._safe_update(self._scope_row)

    def _set_scope(self, scope: str) -> None:
        self._scope = scope if scope in AI.FULLTEXT_MODES else AI.FULLTEXT_CONTEXT
        if self._scope_row is not None:
            self._scope_row.controls = self._scope_controls()
        self._refresh_mode()
        self._safe_update(self._scope_row)

    def _on_apply(self, e) -> None:
        self._apply = bool(e.control.value)
        self._refresh_mode()

    def _on_merge(self, e) -> None:
        self._no_ai_merge = bool(e.control.value)

    def _on_stage_input(self, e) -> None:
        self._stage_user = True
        self._refresh_mode()

    def _reply_row(self, value: str = "") -> ft.Row:
        t = self.t
        field = ft.TextField(value=value, dense=True, text_size=t.font("body"), expand=True,
                             hint_text="回填文件路径", on_change=lambda e: self._refresh_mode())
        ref = [field]

        def _pick(e) -> None:
            self.app.browse_field(ref, "选择回填文件", "file", "ai_reply")

        row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            field,
            C.text_button("选择…", _pick, t),
            C.text_button("删除", lambda e, f=field: self._remove_reply(f), t),
        ])
        self._reply_fields.append(field)
        return row

    def _add_reply(self, e=None) -> None:
        if self._reply_box is None:
            return
        self._reply_box.controls.append(self._reply_row())
        self._refresh_mode()
        self._safe_update(self._reply_box)

    def _remove_reply(self, field) -> None:
        if self._reply_box is None or len(self._reply_fields) <= 1:
            self.app.toast("至少留一个回填文件输入框")
            return
        self._reply_fields = [item for item in self._reply_fields if item is not field]
        self._reply_box.controls = [row for row in self._reply_box.controls
                                    if field not in getattr(row, "controls", [])]
        self._refresh_mode()
        self._safe_update(self._reply_box)

    def _auto_discover(self) -> None:
        out = self.page.field("single_out")
        source = self.page.field("single_source")
        stage = AI.discover_stage(out, source=source)
        if stage is None:
            self.app.toast("没找到暂存目录：先运行第一段")
            self._set_status("没找到暂存目录。先勾「AI 协同初排」跑第一段，或手动填暂存目录。")
        else:
            self._fill_stage(stage, user=False)
            self._show_stage(stage)
            self.app.toast(f"已找到暂存目录：{stage.stem}")
        self._refresh_mode()

    # ── 运行编排 ───────────────────────────────────────────────────────
    def _stage_text(self) -> str:
        return (self._stage_field.value or "").strip() if self._stage_field is not None else ""

    def _fill_stage(self, stage, *, user: bool) -> None:
        if self._stage_field is None:
            return
        self._stage_field.value = stage.stage_dir
        self._safe_update(self._stage_field)
        if user:
            self._stage_user = True

    def _ensure_stage(self) -> None:
        if self._stage_text():
            return
        try:
            out = self.page.field("single_out")
            source = self.page.field("single_source")
        except (AttributeError, IndexError):
            return
        stage = AI.discover_stage(out, source=source)
        if stage is not None:
            self._fill_stage(stage, user=False)
            self._show_stage(stage)

    def _refresh_mode(self) -> None:
        cannot = ""
        mode = AI.MODE_PLAIN
        if self._mode_ctl is None:
            return
        if self._coop:
            self._ensure_stage()
        try:
            mode = AI.plan_mode(direct=self._direct, coop=self._coop,
                                replies=self._reply_values(), stage=self._stage_text())
            text = {
                AI.MODE_PLAIN: "本次运行：普通初排（不碰 AI）",
                AI.MODE_PH1: "本次运行：协同第一段（出任务书 · 尚未交付 · 零上传）",
                AI.MODE_PH2: "本次运行：协同第二段（导入回填 · "
                             + ("应用并在产物里生效" if self._apply else "仅记录建议") + "）",
                AI.MODE_DIRECT: "本次运行：AI 核实（直连 API · 会产生费用）",
            }[mode]
        except ValueError as exc:
            mode = AI.MODE_PLAIN
            text = "⚠ " + str(exc)
            cannot = "error"
        self._last_mode = mode
        self._mode_ctl.value = text
        self._mode_ctl.color = self.t.palette.error if cannot else self.t.palette.accent
        self._safe_update(self._mode_ctl)

    def _reply_values(self) -> list[str]:
        return AI.reply_paths([field.value for field in self._reply_fields])

    def build_call(self, **convert_kwargs) -> EB.EngineCall:
        """页面「运行」按钮的最终命令：普通 / 第一段 / 第二段 / 直连。"""
        ai = AI.from_store(self.app.store)
        self._ensure_stage()
        replies = self._reply_values()
        stage = self._stage_text()
        mode = AI.plan_mode(direct=self._direct, coop=self._coop, replies=replies, stage=stage)
        self._last_mode = mode
        self._lines = []
        if mode == AI.MODE_PLAIN:
            return EB.convert_call(**convert_kwargs)
        channel = AI.channel_state(ai)
        if not channel["enabled"]:
            raise ValueError("AI 功能总开关关着：先到「设置 → AI」打开，或取消勾选 AI 入口")
        source = str(convert_kwargs.pop("source") or "")
        out = str(convert_kwargs.pop("out") or "")
        if mode == AI.MODE_PH1:
            fulltext = AI.fulltext_mode(self._fulltext, self._scope)
            stage_arg = stage if self._stage_user else ""
            return EB.coop_ph1_call(source=source, out=out, fulltext=fulltext,
                                    stage=stage_arg, **convert_kwargs)
        if mode == AI.MODE_PH2:
            return EB.coop_ph2_call(source=source, out=out, replies=replies,
                                    stage=stage, apply=self._apply, **convert_kwargs)
        if not channel["direct"]:
            raise ValueError("直连核实未配置 API Key：请先到「设置 → AI」填好")
        return EB.ai_verify_call(source=source, out=out, ai=ai,
                                 no_ai_merge=self._no_ai_merge, **convert_kwargs)

    def api_env(self) -> dict:
        """直连才把 Key 经环境变量交给引擎；协同两段一律不传。"""
        if self._last_mode != AI.MODE_DIRECT:
            return {}
        return EB.ai_settings_env(AI.from_store(self.app.store))

    def on_line(self, line: str) -> None:
        self._lines.append(str(line or ""))
        if len(self._lines) > 600:
            del self._lines[:200]

    def reset_run(self) -> None:
        """每次点「运行」前清空上一轮的日志缓冲。"""
        self._lines = []

    def budget_notice(self) -> str:
        return AI.budget_notice(self._lines)

    def after_run(self, call, code: int) -> None:
        mode = self._last_mode
        out = self.page.field("single_out")
        source = self.page.field("single_source")
        if mode == AI.MODE_PH1 and code == 0:
            stage = AI.resolve_stage("", out_root=out, source=source)
            if stage is not None:
                self._fill_stage(stage, user=False)
                self._show_stage(stage)
                self._set_summary("第一段完成（尚未交付）：把任务书交给任意 chat 模型核实，"
                                  "再把回填文件加进来跑第二段。", "warn")
            else:
                self._set_summary("第一段结束了，但没找到暂存目录：看日志里的「暂存目录」一行。", "warn")
        elif mode == AI.MODE_PH2 and code in (0, 2):
            stage = AI.resolve_stage(self._stage_text(), out_root=out, source=source)
            if stage is not None:
                self._show_stage(stage)
                summary = stage.summary
                self._set_summary("第二段结束：" + summary["text"], "ok" if code == 0 else "warn")
            else:
                self._set_summary("第二段结束了，但读不到暂存目录的状态文件。", "warn")
        elif mode == AI.MODE_DIRECT:
            notice = self.budget_notice()
            if notice:
                self._set_summary(notice, "error")
            elif code == 0:
                self._set_summary("AI 核实完成：核实报告已写进产物目录。", "ok")
            else:
                self._set_summary("AI 核实未完成：看运行日志（预算/网络/Key 都可能）。", "error")
        self._refresh_mode()
        self._safe_update(self._summary_ctl)

    # ── 显示小工具 ─────────────────────────────────────────────────────
    def _set_status(self, text: str) -> None:
        if self._status_ctl is not None:
            self._status_ctl.value = text
            self._safe_update(self._status_ctl)

    def _set_summary(self, text: str, tone: str = "info") -> None:
        if self._summary_ctl is None:
            return
        colors = {
            "ok": self.t.palette.success,
            "warn": self.t.palette.warn,
            "error": self.t.palette.error,
            "info": self.t.palette.text_muted,
        }
        self._summary_ctl.value = text
        self._summary_ctl.color = colors.get(tone, self.t.palette.text_muted)
        self._safe_update(self._summary_ctl)

    def _show_stage(self, stage) -> None:
        summary = stage.summary
        lines = [f"暂存目录：{stage.stage_dir}",
                 f"阶段：{stage.phase or '（无）'} · 状态：{stage.status_text}"]
        if stage.taskbook:
            lines.append(f"任务书：{stage.taskbook}")
        if stage.final_dir:
            lines.append(f"最终目录：{stage.final_dir}")
        lines.append(summary["text"])
        self._set_status("\n".join(lines))

    def _safe_update(self, *controls) -> None:
        for control in controls:
            if control is None:
                continue
            try:
                control.update()
            except Exception:  # noqa: BLE001 - 未挂载/已销毁都无害
                pass

    def refresh_running(self) -> None:
        running = self.app.running
        for button in self._buttons:
            try:
                button.disabled = running
            except Exception:  # noqa: BLE001
                pass


class AiSettings:
    """设置页「AI」卡：总开关 / Key / 连接参数 / 预算。"""

    def __init__(self, page) -> None:  # page: SettingsPage
        self.page = page
        self.app = page.app
        self.t = page.t
        self._ai = AI.from_store(self.app.store)
        self._protocol_row: ft.Row | None = None
        self._key_label: ft.Text | None = None
        self._enabled_row = None

    def build(self) -> ft.Control:
        t, p = self.t, self.t.palette
        ai = self._ai
        self._key_label = C.text(AI.mask_key(ai["key"]), t, "caption", p.text_faint)
        key_row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.text("API Key", t, "small", p.text_muted),
            ft.TextField(value=ai["key"], password=True, can_reveal_password=True, dense=True,
                         text_size=t.font("body"), expand=True,
                         hint_text="只存本机设置 JSON；不进命令行、不进日志",
                         on_change=self._on_key_typing, on_blur=self._on_key_commit),
            C.text_button("清除", lambda e: self._clear_key(), t),
        ])
        self._protocol_row = ft.Row(spacing=t.sp("1"), controls=self._protocol_controls())
        enabled = C.option_row("AI 功能总开关", "关 = 所有 AI 入口隐藏、零调用", t,
                               icon="POWER_SETTINGS_NEW", checked=ai["enabled"],
                               on_change=self._on_enabled)
        self._enabled_row = enabled
        rows = [
            enabled,
            key_row,
            self._key_label,
            self._text_row("base", "接口地址", ai["base"]),
            self._text_row("model", "模型", ai["model"]),
            self._protocol_row,
            C.option_row("思考模式", "关 = 显式 SLCN_LLM_THINKING=0（抽取任务更快更省）", t,
                         icon="PSYCHOLOGY_OUTLINED", checked=ai["thinking"],
                         on_change=lambda e: self._save({"thinking": bool(e.control.value)})),
            self._budget_row("article_budget_tokens", "单篇预算 tokens", ai),
            self._budget_row("batch_budget_tokens", "批量预算 tokens", ai),
            self._budget_row("verify_max", "单篇核实条数（0 = 不限）", ai),
            C.text("预算由引擎在发请求前执行：超限直接退出 2、零请求（界面显示「预算不足，未发起请求」）。",
                   t, "caption", p.text_faint),
        ]
        return C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("AI", t, note="默认关；密钥只在本机"),
            *rows,
        ]), t)

    def _text_row(self, key: str, label: str, value: str) -> ft.Row:
        t, p = self.t, self.t.palette
        field = ft.TextField(value=str(value or ""), dense=True, text_size=t.font("body"),
                             expand=True, on_blur=lambda e, k=key: self._save({k: e.control.value}))
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", p.text_muted),
            field,
        ])

    def _budget_row(self, key: str, label: str, ai: dict) -> ft.Row:
        t, p = self.t, self.t.palette
        field = ft.TextField(value=str(ai.get(key, 0)), dense=True, width=130,
                             text_size=t.font("body"),
                             on_blur=lambda e, k=key: self._save_budget(k, e))
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", p.text_muted),
            ft.Container(expand=True),
            field,
            C.text("tokens", t, "caption", p.text_faint),
        ])

    def _save(self, patch: dict) -> None:
        self._ai = AI.save(self.app.store, patch)
        self.app.persist()

    def _save_budget(self, key: str, e) -> None:
        try:
            value = AI.parse_budget(e.control.value, label="预算")
        except ValueError as exc:
            self.app.toast(str(exc))
            e.control.value = str(self._ai.get(key, 0))
            try:
                e.control.update()
            except Exception:  # noqa: BLE001
                pass
            return
        self._save({key: value})

    @staticmethod
    def _event_text(e) -> str:
        """取事件对应的控件文本：Flet 1.0.5 的 on_change/on_blur 传 Event。

        取值顺序 `e.control.value` → `e.data` → 直接传进来的 str（老测试/脚本）；
        `control` 未挂载（None）时退回事件自带 data，绝不 str(Event) 落库。
        返回值一律 strip：Key 首尾空白永远不落盘。
        """
        if isinstance(e, str):
            return e.strip()
        control = getattr(e, "control", None)
        raw = getattr(control, "value", None)
        if raw is None:
            raw = getattr(e, "data", None)
        return str(raw or "").strip()

    def _on_key_typing(self, e) -> None:
        # Flet 传 Event，必须取 e.control.value；只进内存（不落盘），等 on_blur/「立即保存」再写文件。
        value = self._event_text(e)
        self.app.store.set("ai.key", value)
        if self._key_label is not None:
            self._key_label.value = AI.mask_key(value)
            self._safe_update(self._key_label)

    def _on_key_commit(self, e) -> None:
        value = self._event_text(e)
        self._save({"key": value})
        self.app.mark("ai_key_set", key_set=bool(value))

    def _clear_key(self) -> None:
        self._save({"key": ""})
        if self._key_label is not None:
            self._key_label.value = AI.mask_key("")
            self._safe_update(self._key_label)
        self.app.toast("已清除本机保存的 API Key")
        self.app.mark("ai_key_cleared")

    def _protocol_controls(self) -> list[ft.Control]:
        t = self.t
        return [
            C.text("协议", t, "small", t.palette.text_muted),
            C.chip("chat", t, active=self._ai.get("protocol") == "chat",
                   on_click=lambda e: self._set_protocol("chat")),
            C.chip("responses", t, active=self._ai.get("protocol") == "responses",
                   on_click=lambda e: self._set_protocol("responses")),
        ]

    def _set_protocol(self, protocol: str) -> None:
        self._save({"protocol": protocol})
        if self._protocol_row is not None:
            self._protocol_row.controls = self._protocol_controls()
            self._safe_update(self._protocol_row)

    def _on_enabled(self, e) -> None:
        enabled = bool(e.control.value)
        self._save({"enabled": enabled})
        self.app.mark("ai_master", enabled=enabled)
        self.app.toast("AI 功能已" + ("开启" if enabled else "关闭（所有 AI 入口隐藏，零调用）"))
        self.app.rebuild_shell()

    def _safe_update(self, *controls) -> None:
        for control in controls:
            if control is None:
                continue
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass
