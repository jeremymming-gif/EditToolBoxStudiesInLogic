"""批 109 W2a：参考文献规范化页（原「文献回读校验」，NR-001..025）。

页面语义与旧壳 `sources-mac/Views/CheckBibView.swift` +
`Features/CheckBibFeature.swift` 对齐，但**只读、不并回**：

- 交回 .bib 两态：选择文件 / 粘贴文本（``--returned-stdin``，走标准输入，不落中间文件）；
- 「识别」用 ``--list-keys`` 只打印一行「N 条：key1, key2…」，永不写文件；
- 报告默认 ``<交回 .bib 同目录>/<基名>_回读校验报告.md``；报告输出可填目录或 .md；
- 粘贴模式且没填原件：报告落系统临时目录，并提示把「原件 .bib」指向产物里的
  reference.bib（NR-001：并回目标 = ``dirname(交回稿)/reference.bib``，**只提示不并回**）。

Wave 0 / S1 红线：不提供任何「并回」按钮，也不暴露 ``--merge-into`` 或永久删除参数。
"""

from __future__ import annotations

import os
import tempfile

import flet as ft

from . import components as C
from services import engine_bridge as EB

PAGE_TITLE = "参考文献规范化"
PAGE_SUBTITLE = "离线体检交回的 .bib（语法 / 编辑部规范 / 引用键闭合 / 与基准逐字段对比），产出《回读校验报告》。"
EXPLAIN = (
    "做什么：拿作者交回、人工改过的 .bib，与「任务书前的原件 .bib」逐字段对比，"
    "并检查语法、编辑部规范、引用键闭合，生成《回读校验报告》。"
)
NO_TOUCH_NOTE = "不改正文：正文 .tex 只作只读比对（--tex），本页不写回任何文件。"
LIBRARY_NOTE = (
    "写库边界：本页只认路径、只出报告，不写引用库；入藏请走「引用库」页（先并回、确认无误再入藏）。"
)
MERGE_OFF_NOTE = (
    "「并回」入口已停用（批 109 Wave 0 安全口径）：本页只出报告。确需并回请用 CLI "
    "`checkbib.py --merge-into <产物 reference.bib>`（会先备份 <原文件>.ai前）。"
)
TASKBOOK_NOTE = (
    "本页不生成《文献核实任务书》：需要请走「初排转换 → AI 协同初排（两段式）」第一段，"
    "或 CLI --verify-refs。AI 交回修订须人工核对；本页不联网、不调模型。"
)
REPORT_SUFFIX = "_回读校验报告.md"
PASTE_REPORT_NAME = "checkbib_粘贴_回读校验报告.md"
PRODUCT_BIB = "reference.bib"

MODE_FILE = "file"
MODE_PASTE = "paste"
MODES = ((MODE_FILE, "选择文件"), (MODE_PASTE, "粘贴文本"))

HINT_ORIGINAL = "留空则不做字段级对比（key 被改名、字段被删就查不出来）"
HINT_TEX = "留空则不查引用键闭合（悬空引用 / 从未被引用）"
HINT_OUT = "留空 = 自动（<名字>_回读校验报告.md）；也可填一个目录或 .md 路径"
HINT_PASTE = "粘贴交回的 .bib 文本（走标准输入，不落中间文件）"
HINT_PREVIEW_EMPTY = "粘贴后这里显示「识别到几条、键是什么」（不会写任何文件）。"
DRAG_HINT_FILE = (
    "三个路径都可以直接拖进来。选好「交回的 .bib」后，同目录下能找到的另一个 .bib、"
    "以及同目录下唯一的那份 .tex，会自动填进下面两栏（不对就直接改）。"
)
DRAG_HINT_PASTE = (
    "粘贴模式没有「交回文件」可猜：正文 .tex 会从「原件 .bib」的同目录里找唯一的那份自动填。"
)
OUT_NOTE = (
    "填已存在的目录 = 报告按默认文件名放进该目录；填文件路径 = 写到该文件；"
    "留空 = 自动落在交回 .bib / 产物目录旁边。"
)
OPEN_REPORT_MISSING = "报告还没生成：先跑一次「开始体检」。"
OPEN_REPORT_GONE = "报告文件不存在：{path}（可能已被移动或删除）"


# ── 纯逻辑（可测；不依赖控件）─────────────────────────────────────────────
def report_path_for_returned(bib: str) -> str:
    """NR-004：默认报告 = 交回 bib 同目录 + 基名 + _回读校验报告.md。"""
    path = os.path.abspath(bib) if bib else ""
    if not path:
        return ""
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(os.path.dirname(path), stem + REPORT_SUFFIX)


def paste_report_path(original: str) -> str:
    """NR-004/015：粘贴模式默认报告 = 原件同目录；原件为空 → 系统临时目录。"""
    if original:
        return report_path_for_returned(original)
    return os.path.join(tempfile.gettempdir(), PASTE_REPORT_NAME)


def paste_report_note(original: str) -> str:
    if original:
        return ""
    return ("（粘贴模式且没填「原件 .bib」：报告落到系统临时目录 "
            f"{paste_report_path('')}）；要让它挨着产物，请在「原件 .bib」一栏选择产物里的 "
            f"{PRODUCT_BIB}。）")


def resolved_report_path(raw: str, auto: str) -> str:
    """NR-018：留空 = 自动；已存在的目录 = 按默认文件名放进该目录；否则按文件路径。"""
    value = (raw or "").strip()
    if not value:
        return auto
    if os.path.isdir(value):
        return os.path.join(os.path.abspath(value), os.path.basename(auto or PASTE_REPORT_NAME))
    return os.path.abspath(value)


def product_bib_target(returned: str) -> str:
    """NR-001：并回目标 = 交回稿同目录的 reference.bib（**只用于提示**，界面不并回）。"""
    if not returned:
        return ""
    return os.path.join(os.path.dirname(os.path.abspath(returned)), PRODUCT_BIB)


def autofill_siblings(returned: str, original: str, tex: str) -> tuple[str, str, str]:
    """NR-020：交回 .bib 同目录的另一个 .bib / 唯一 .tex 自动填（不覆盖已填值）。"""
    if not returned or not os.path.isdir(os.path.dirname(os.path.abspath(returned))):
        return original, tex, ""
    folder = os.path.dirname(os.path.abspath(returned))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return original, tex, ""
    bibs = [n for n in names if n.lower().endswith(".bib")
            and os.path.abspath(os.path.join(folder, n)) != os.path.abspath(returned)]
    notes: list[str] = []
    if not original and bibs:
        pick = os.path.join(folder, bibs[0])
        original = pick
        if len(bibs) > 1:
            notes.append(f"（同目录有 {len(bibs)} 个 .bib，先按文件名把 {bibs[0]} 填进「原件」，"
                         "不对请直接改）")
        else:
            notes.append(f"（同目录的 {bibs[0]} 已填进「原件」，不对请直接改）")
    if not tex:
        found = [n for n in names if n.lower().endswith(".tex")
                 and os.path.isfile(os.path.join(folder, n))]
        if len(found) == 1:
            tex = os.path.join(folder, found[0])
            notes.append(f"（同目录唯一 .tex「{found[0]}」已填进「正文」，不对请直接改）")
    return original, tex, " ".join(notes)


def autofill_tex_from_original(original: str, tex: str) -> tuple[str, str]:
    """粘贴模式：从原件同目录找唯一 .tex 自动填。"""
    if tex or not original:
        return tex, ""
    folder = os.path.dirname(os.path.abspath(original))
    try:
        found = [n for n in sorted(os.listdir(folder)) if n.lower().endswith(".tex")
                 and os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return tex, ""
    if len(found) == 1:
        return os.path.join(folder, found[0]), f"（原件同目录唯一 .tex「{found[0]}」已填进「正文」。）"
    return tex, ""


def preview_text(status: int, output: list[str], pasted: str) -> str:
    """粘贴模式「识别」结果 → 一行文案（永不写文件）。"""
    text = (pasted or "").strip()
    if not text:
        return ""
    last = (output or [""])[-1].strip() if output else ""
    if status == 0:
        return f"识别：{last}" if last else "识别：0 条"
    return f"识别失败（退出码 {status}）：{last}" if last else f"识别失败（退出码 {status}）"


def preview_call(returned_text: str = "") -> EB.EngineCall:
    """NR-003：--list-keys + --returned-stdin（只读一行）。"""
    return EB.checkbib_call(returned_stdin=True, list_keys=True)


def build_argv(returned: str, *, mode: str, original: str = "", tex: str = "",
               out: str = "") -> EB.EngineCall:
    """体检 argv：文件模式 --returned；粘贴模式 --returned-stdin（stdin 由管线喂）。"""
    paste = mode == MODE_PASTE
    return EB.checkbib_call(
        returned="" if paste else returned, original=original, tex=tex, out=out,
        returned_stdin=paste, list_keys=False,
    )


def open_state(path: str) -> tuple[bool, str]:
    if not path:
        return False, OPEN_REPORT_MISSING
    if not os.path.isfile(path):
        return False, OPEN_REPORT_GONE.format(path=path)
    return True, ""


# ── 页面面板 ──────────────────────────────────────────────────────────────
class NormalizePanel:
    """三态子流程：交回来源（文件/粘贴）→ 只读比对 → 报告。"""

    MODE_KEY = "form.bib_mode"
    PASTE_KEY = "form.bib_paste"
    OUT_KEY = "form.bib_out_raw"

    def __init__(self, page) -> None:
        self.page = page
        self.mode = self.page.app.store.get(self.MODE_KEY, MODE_FILE) or MODE_FILE
        if self.mode not in (MODE_FILE, MODE_PASTE):
            self.mode = MODE_FILE
        self._preview_line = ""
        self._filled_note = ""
        self._box = None
        self._mode_row = None
        self._returned_box = None
        self._preview_lbl = None
        self._preview_btn = None
        self.last_report = ""

    # ── 构建 ────────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.page.t
        self._mode_row = ft.Row(spacing=t.sp("1"), controls=[])
        self._returned_box = ft.Column(spacing=t.sp("1"), controls=[])
        self._preview_lbl = C.text(HINT_PREVIEW_EMPTY, t, "caption", t.palette.text_faint)
        self._filled_note_lbl = C.text("", t, "caption", t.palette.text_faint)
        self._render_modes()
        self._render_returned()
        info = [
            C.section_title(PAGE_TITLE, t, note="离线 · 只读"),
            C.text(PAGE_SUBTITLE, t, "small", t.palette.text_muted),
            C.card(ft.Column(spacing=t.sp("1"), controls=[
                C.text(EXPLAIN, t, "caption", t.palette.text_faint),
                C.text(NO_TOUCH_NOTE, t, "caption", t.palette.text_faint),
                C.text(LIBRARY_NOTE, t, "caption", t.palette.text_faint),
                C.text(TASKBOOK_NOTE, t, "caption", t.palette.text_faint),
            ]), t),
            C.notice_bar(MERGE_OFF_NOTE, t, tone="info"),
            C.section_title("交回来源", t, note="必填：文件或粘贴文本二选一"),
            self._mode_row,
            self._returned_box,
            self._filled_note_lbl,
            C.text(DRAG_HINT_FILE if self.mode == MODE_FILE else DRAG_HINT_PASTE,
                   t, "caption", t.palette.text_faint),
            C.section_title("对比与报告", t, note="都可留空"),
            self.page.add_picker("任务书前的原件 .bib", "bib_original", HINT_ORIGINAL,
                                 kind="file", required=False,
                                 on_change=lambda e: self._autofill_from_original()),
            self.page.add_picker("正文 .tex（只读）", "bib_tex", HINT_TEX,
                                 kind="file", required=False),
        ]
        self._out_field = ft.TextField(
            label="报告输出（可选）", value=self.page.app.store.get(self.OUT_KEY, "") or "",
            hint_text=HINT_OUT, dense=True, text_size=t.font("body"),
            border_color=t.palette.border, focused_border_color=t.palette.accent,
            content_padding=C._pad(t, 10, 8, 10, 8),
            on_change=self._on_out_change,
        )
        self.page.fields["bib_out"] = [self._out_field]
        self._auto_lbl = C.text(self.auto_report_line(), t, "caption", t.palette.text_faint)
        self._preview_btn = C.secondary_button(
            "识别粘贴的键", self._on_preview_paste, t, disabled=(self.mode != MODE_PASTE))
        actions = ft.Row(spacing=t.sp("1"), controls=[
            C.primary_button("开始体检", self.page._on_run, t, icon="PLAY_ARROW",
                             disabled=not self.page.app.engine_status.ok),
            C.secondary_button("打开报告", self._on_open_report, t, icon="OPEN_IN_NEW"),
            self._preview_btn,
        ])
        return ft.Column(spacing=t.sp("2"), controls=[
            *info,
            C.card(ft.Column(spacing=t.sp("1"), controls=[
                self._out_field, self._auto_lbl,
                C.text(OUT_NOTE, t, "caption", t.palette.text_faint),
            ]), t),
            actions,
            self._preview_lbl,
        ])

    def _render_modes(self) -> None:
        t = self.page.t
        self._mode_row.controls = [
            C.chip(label, t, active=(key == self.mode),
                   on_click=lambda e, k=key: self._switch_mode(k))
            for key, label in MODES
        ]

    def _render_returned(self) -> None:
        self._returned_box.controls = []
        if self.mode == MODE_FILE:
            self._returned_box.controls.append(self.page.add_picker(
                "交回的 .bib", "bib_returned", "编辑改完交回来的 .bib", kind="file",
                on_change=lambda e: self._autofill_from_returned()))
            return
        t = self.page.t
        paste = ft.TextField(
            label="交回的 .bib 文本", value=self.page.app.store.get(self.PASTE_KEY, "") or "",
            hint_text=HINT_PASTE, multiline=True, min_lines=6, max_lines=12,
            text_size=t.font("body"), border_color=t.palette.border,
            focused_border_color=t.palette.accent,
            content_padding=C._pad(t, 10, 8, 10, 8), on_change=self._on_paste_change,
        )
        self.page.fields["bib_paste"] = [paste]
        self._returned_box.controls.extend([
            paste,
            ft.Row(spacing=t.sp("1"), controls=[
                C.secondary_button("从剪贴板粘贴", self._on_paste_clipboard, t, icon="CONTENT_PASTE"),
            ]),
        ])

    # ── 切换 / 事件 ─────────────────────────────────────────────────────
    def _switch_mode(self, key: str) -> None:
        if self.page.app.running:
            self.page.app.toast("运行中不能切换来源，先停止")
            return
        self.mode = key if key in (MODE_FILE, MODE_PASTE) else MODE_FILE
        self.page.app.store.set(self.MODE_KEY, self.mode)
        self._preview_line = ""
        self.last_report = ""
        self._render_modes()
        self._render_returned()
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        if self._preview_btn is not None:
            self._preview_btn.disabled = (self.mode != MODE_PASTE)
        if self._preview_lbl is not None:
            self._preview_lbl.value = self._preview_line or HINT_PREVIEW_EMPTY
        if self._auto_lbl is not None:
            self._auto_lbl.value = self.auto_report_line()
        if self._filled_note_lbl is not None:
            self._filled_note_lbl.value = self._filled_note
        try:
            self.page.root.update()
        except Exception:  # noqa: BLE001
            pass

    def _on_out_change(self, e) -> None:
        self.page.app.store.set(self.OUT_KEY, e.control.value.strip())
        self._refresh_mode_ui()

    def _autofill_from_returned(self) -> None:
        returned = self.page.field("bib_returned")
        if not returned:
            self._filled_note = ""
            self._refresh_mode_ui()
            return
        original = self.page.field("bib_original")
        tex = self.page.field("bib_tex")
        new_original, new_tex, note = autofill_siblings(returned, original, tex)
        if new_original != original:
            self.page.set_field("bib_original", new_original)
        if new_tex != tex:
            self.page.set_field("bib_tex", new_tex)
        self._filled_note = note
        self._refresh_mode_ui()

    def _autofill_from_original(self) -> None:
        original = self.page.field("bib_original")
        tex = self.page.field("bib_tex")
        new_tex, note = autofill_tex_from_original(original, tex)
        if new_tex != tex:
            self.page.set_field("bib_tex", new_tex)
        self._filled_note = note
        self._refresh_mode_ui()

    def _on_paste_change(self, e) -> None:
        self.page.app.store.set(self.PASTE_KEY, e.control.value)
        text = (e.control.value or "").strip()
        self._preview_line = f"{len(text)} 字符；点「识别粘贴的键」先跑一次只读识别。" if text else ""
        if self._filled_note_lbl is not None:
            self._filled_note = ""
        self._refresh_mode_ui()

    def _on_paste_clipboard(self, e=None) -> None:
        try:
            text = self.page.clipboard if hasattr(self.page, "clipboard") else ""
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            self.page.app.toast("剪贴板里没有可导入的文本：先把交回的 .bib 复制到剪贴板再点这里。")
            return
        self.page.app.store.set(self.PASTE_KEY, text)
        self._render_returned()
        self._preview_line = f"{len(text.strip())} 字符"
        self._refresh_mode_ui()
        self.page.toast("已粘贴剪贴板文本")

    def _on_open_report(self, e=None) -> None:
        ok, message = open_state(self.last_report)
        if not ok:
            self.page.app.toast(message)
            return
        self.page.app.open_path(self.last_report)

    def _on_preview_paste(self, e=None) -> None:
        text = self.pasted_text()
        if not text.strip():
            self.page.app.toast("先粘贴交回的 .bib 文本")
            return
        self.page._on_paste_preview()

    def set_preview_line(self, text: str) -> None:
        self._preview_line = text
        self._refresh_mode_ui()

    def refresh_after_run(self) -> None:
        if self._preview_lbl is not None:
            self._preview_lbl.value = self._preview_line or HINT_PREVIEW_EMPTY
        if self._auto_lbl is not None:
            self._auto_lbl.value = self.auto_report_line()
        try:
            self.page.root.update()
        except Exception:  # noqa: BLE001
            pass

    # ── 数据 ────────────────────────────────────────────────────────────
    def returned(self) -> str:
        return self.page.field("bib_returned") if self.mode == MODE_FILE else ""

    def pasted_text(self) -> str:
        if self.mode != MODE_PASTE:
            return ""
        return self.page.app.store.get(self.PASTE_KEY, "") or ""

    def original(self) -> str:
        return self.page.field("bib_original")

    def tex(self) -> str:
        return self.page.field("bib_tex")

    def out_raw(self) -> str:
        return self.page.app.store.get(self.OUT_KEY, "") or ""

    def auto_report_line(self) -> str:
        auto = self.default_report()
        return f"自动报告路径：{auto}" if auto else "自动报告路径：（先选交回 .bib 或粘贴文本）"

    def default_report(self) -> str:
        if self.mode == MODE_PASTE:
            return paste_report_path(self.original())
        return report_path_for_returned(self.returned())

    def resolved_report(self) -> str:
        return resolved_report_path(self.out_raw(), self.default_report())

    def build_call(self) -> EB.EngineCall:
        """校验 + 组 argv；`last_report` 记下本次报告落点。"""
        if self.mode == MODE_FILE and not self.returned():
            raise ValueError("请先选择交回的 .bib 文件")
        if self.mode == MODE_PASTE and not self.pasted_text().strip():
            raise ValueError("请先在「粘贴文本」框里粘贴交回的 .bib 文本")
        self.last_report = self.resolved_report()
        return build_argv(
            self.returned(), mode=self.mode, original=self.original(), tex=self.tex(),
            out=self.last_report,
        )

    def stdin_text(self) -> str | None:
        return self.pasted_text() if self.mode == MODE_PASTE else None
