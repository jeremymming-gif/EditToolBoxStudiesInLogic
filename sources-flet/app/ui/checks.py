"""批 109 W2b：一校与检查页的结构化结果（PC-*）。

数据来源只有两处引擎产物，本模块**不复制判据**：

* 检查 tex 信息 → `Engine/infocheck.py --preview` 的 JSON（`articles` / `inconsistencies`）；
* 一校核查 → `Engine/proofcheck.py` 写的 `<stem>_一校核查.md`（摘要表 + 计数行）。

页面只负责取值、拼 argv、显示；表格/摘要/结果卡都在这里，`pages.py` 只留钩子，
避免把排版细节再堆回 1500 行的页面文件。
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from ui import components as C

# ── 检查 tex 信息：字段与文案 ────────────────────────────────────────────────
INFO_COLUMNS = (
    "文件夹", "卷", "期", "年份", "起–止", "声明页数", "实际页数",
    "字号/行距", "第一作者", "中文标题",
)
EMPTY_INFO = "（还没检查：选一期的目录，点「运行」）"
UNREADABLE_INFO = "⚠️ 报告已生成，但读不回结构化数据（表格留空）。报告文件仍在目录里，可直接打开查看。"
INFO_DIR_HINT = (
    "期目录要选**整期根目录**（内含 `01 作者/` 等篇目子目录），"
    "不要选 `000 Table of Contents` 那一层。"
)
INFO_RUN_HINT = (
    "运行后在期目录写 `信息检查<时间戳>.txt / .json`；下方结果表的 10 列与引擎 "
    "`--preview` JSON 逐字段对齐（表格不自己解析 tex）。"
)


def _clean(value) -> str:
    """单行化：JSON 里的标题/作者可能带换行，表格里不允许撑开行高。"""
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _int(value) -> int | None:
    text = _clean(value)
    return int(text) if text.isdigit() else None


def page_mismatch(declared: int | None, actual: int | None) -> bool:
    """声明页数与 PDF 实际页数不符（两边都有数字时才算）。"""
    return declared is not None and actual is not None and declared != actual


def normalize_article(raw: dict | None) -> dict:
    """把引擎 JSON 的一篇摊平成表格字段；缺失值统一成「—」。"""
    raw = raw or {}
    pages = _clean(raw.get("pages"))
    start = _clean(raw.get("startPage"))
    end = _clean(raw.get("endPage"))
    declared = _int(pages)
    actual = _int(raw.get("actualPDFPages"))
    title_zh = _clean(raw.get("chineseTitle"))
    title_en = _clean(raw.get("englishTitle"))
    author = _clean(raw.get("firstAuthorChinese")) or _clean(raw.get("firstAuthorEnglish"))
    font = " / ".join(part for part in (_clean(raw.get("fontSize")),
                                       _clean(raw.get("lineSpacing"))) if part)
    if start and end:
        span = f"{start}–{end}"
    else:
        span = start or end or "—"
    return {
        "folder": _clean(raw.get("folder")) or _clean(raw.get("folderName")) or "—",
        "folder_name": _clean(raw.get("folderName")),
        "volume": _clean(raw.get("volume")) or "—",
        "issue": _clean(raw.get("issue")) or "—",
        "year": _clean(raw.get("year")) or "—",
        "start": start,
        "end": end,
        "span": span,
        "pages": pages or "—",
        "declared": declared,
        "actual": actual,
        "actual_text": str(actual) if actual is not None else "—",
        "font_line": font or "—",
        "author": author or "—",
        "title": title_zh or title_en or "（无标题）",
        "has_pdf": bool(raw.get("hasPDF")) or actual is not None,
        "mismatch": page_mismatch(declared, actual),
    }


def normalize_articles(payload) -> list[dict]:
    if isinstance(payload, dict):
        raw = payload.get("articles") or []
    elif isinstance(payload, list):
        raw = payload
    else:
        raw = []
    return [normalize_article(item) for item in raw if isinstance(item, dict)]


def inconsistencies(payload, articles: list[dict] | None = None) -> list[str]:
    """优先用引擎给的 `inconsistencies`；旧 JSON 没这个键时按同一口径补算。"""
    if isinstance(payload, dict) and isinstance(payload.get("inconsistencies"), list):
        return [_clean(note) for note in payload["inconsistencies"] if _clean(note)]
    arts = articles if articles is not None else normalize_articles(payload)
    notes: list[str] = []
    for key, label in (("volume", "卷号"), ("issue", "期号"), ("year", "年份")):
        values = sorted({a[key] for a in arts if a[key] and a[key] != "—"})
        if len(values) > 1:
            notes.append(f"{label}不一致：{' / '.join(values)}")
    for a in arts:
        if a["mismatch"]:
            name = a["folder_name"] or a["folder"]
            notes.append(f"{name}：声明页数 {a['pages']}，实际 {a['actual']} 页")
    return notes


def info_summary(payload, *, articles: list[dict] | None = None,
                 notes: list[str] | None = None) -> str:
    """检查结果摘要（PC-005/006/028/029），与 Tk 版三种句式一致。"""
    arts = articles if articles is not None else normalize_articles(payload)
    if not arts:
        return EMPTY_INFO
    notes = notes if notes is not None else inconsistencies(payload, arts)
    if notes:
        head = f"共 {len(arts)} 篇；发现 {len(notes)} 处需要核对：" + "；".join(notes[:3])
        return head + ("；…" if len(notes) > 3 else "")
    no_pdf = sum(1 for a in arts if a["actual"] is None)
    if no_pdf:
        return (f"共 {len(arts)} 篇，卷号/期号/年份一致；但 {no_pdf} 篇没有可比对的 PDF 页数"
                "（PDF 缺失或打不开，见日志）——页数一致性未验证。")
    return f"共 {len(arts)} 篇，卷号/期号/年份一致，页数与 PDF 实际页数相符。"


def info_level(payload, articles: list[dict] | None = None,
               notes: list[str] | None = None) -> str:
    arts = articles if articles is not None else normalize_articles(payload)
    if not arts:
        return "idle"
    notes = notes if notes is not None else inconsistencies(payload, arts)
    return "warn" if notes else "done"


# ── 一校核查：读报告摘要（不是 stdout 人类文案）──────────────────────────────
@dataclass(frozen=True)
class ProofRule:
    rid: str
    title: str
    count: int
    basis: str = ""


@dataclass
class ProofReport:
    """`<stem>_一校核查.md` 的结构化摘要（解析失败给空对象，不猜）。"""

    manual: int = 0
    fixable: int = 0
    total: int = 0
    rule_count: int = 0
    rules: tuple[ProofRule, ...] = field(default_factory=tuple)
    conclusion: str = ""
    ok: bool = False

    def top_rules(self, limit: int = 5) -> list[ProofRule]:
        return sorted(self.rules, key=lambda r: (-r.count, r.rid))[:limit]


_HEADER_COUNTS = re.compile(
    r"\*\*计数\*\*：\s*需人工\s*(\d+)\s*｜\s*可自动改\s*(\d+)\s*｜\s*"
    r"命中合计\s*(\d+)\s*｜\s*规则数\s*(\d+)"
)
_RULE_ROW = re.compile(
    r"^\|\s*`(?P<rid>[A-Z]{2}-\d+)`\s*\|\s*\*{0,2}(?P<count>\d+)\*{0,2}\s*\|"
    r"\s*(?P<title>.*?)\s*\|\s*(?P<basis>.*?)\s*\|\s*$"
)
_TOTAL = re.compile(r"合计命中\s*(\d+)\s*处")
_FIXABLE = re.compile(r"可自动修\s*(\d+)\s*处")
_HEADLINE = re.compile(r"^\*\*摘要\*\*：(.+)$", re.MULTILINE)
# 规则表只在 `## 摘要` 段扫：`## 已自动改的部分（--fix）` 是「规则|行|原文|改成」，
# 若全文扫会把行号当条数。找不到该标题（旧报告）才退回全文。
_SUMMARY_SECTION = re.compile(r"(?ms)^##\s*摘要\s*$.*?(?=^## |\Z)")


def parse_proof_report(text: str) -> ProofReport:
    """解析引擎报告：摘要表（人工命中）+ `合计命中` + 可选 `可自动修`。"""
    if not text:
        return ProofReport()
    header = _HEADER_COUNTS.search(text)
    total = _TOTAL.search(text)
    fixable = _FIXABLE.search(text)
    headline = _HEADLINE.search(text)
    section = _SUMMARY_SECTION.search(text)
    rule_text = section.group(0) if section else text
    rules: list[ProofRule] = []
    for line in rule_text.splitlines():
        match = _RULE_ROW.match(line.strip())
        if match:
            rules.append(ProofRule(
                rid=match.group("rid"),
                title=_clean(match.group("title")),
                count=int(match.group("count")),
                basis=_clean(match.group("basis")),
            ))
    manual = int(header.group(1)) if header else sum(r.count for r in rules)
    parsed_total = int(header.group(3)) if header else (int(total.group(1)) if total else manual)
    parsed_fixable = int(header.group(2)) if header else (int(fixable.group(1)) if fixable else 0)
    return ProofReport(
        manual=manual,
        fixable=parsed_fixable,
        total=parsed_total,
        rule_count=int(header.group(4)) if header else len(rules),
        rules=tuple(rules),
        conclusion=_clean(headline.group(1)) if headline else "",
        ok=bool(header or total or rules),
    )


def read_proof_report(path: str) -> ProofReport:
    if not path:
        return ProofReport()
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ProofReport()
    return parse_proof_report(text)


def proof_report_name(tex: str) -> str:
    stem = Path(tex).stem if tex else "<名字>"
    return f"{stem}_一校核查.md"


def proof_fix_name(tex: str) -> str:
    stem = Path(tex).stem if tex else "<名字>"
    return f"{stem}_一校核查后.tex"


def proof_lang_label(value: str) -> str:
    return {"": "自动", "zh": "中文（zh）", "en": "英文（en）"}.get(value or "", value or "自动")


def proof_header_lines(tex: str, lang: str, fix: bool) -> list[str]:
    """PC-014：运行前打印核查抬头（稿件/模式/语种）。"""
    return [
        "══ 一校核查 ══",
        "工具：proofcheck.py",
        f"稿件：{tex or '（未选择）'}",
        "模式：" + ("报告 + 修正稿（--fix）" if fix else "只出报告"),
        f"语种：{proof_lang_label(lang)}",
    ]


def proof_result_text(code: int, *, tex: str = "", fix: bool = False) -> str:
    """PC-011 / 041–045：完成与失败的文案（1 = 有命中，不是失败）。"""
    if code == 0:
        text = "✅ 完成：没有命中任何规则。"
    elif code == 1:
        text = f"✅ 完成：有命中。报告已写到稿件同目录的 {proof_report_name(tex)}。"
    elif code < 0:
        return "❌ 启动失败。"
    else:
        return f"❌ 核查失败（退出码 {code}）。请把上面的报错发给技术人员。"
    if fix:
        text += f"已另写修正稿：{proof_fix_name(tex)}（输入文件未改动）。"
    return text


# ── UI：检查 tex 信息结果卡 ─────────────────────────────────────────────────
def _cell(value: str, t, *, muted: bool = False, strong: bool = False,
          color: str | None = None, width: float | None = None,
          tooltip: str | None = None) -> ft.DataCell:
    p = t.palette
    text = C.text(
        value, t, "small", color or (p.text_muted if muted else None),
        "semibold" if strong else "regular",
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
    )
    if tooltip:
        text.tooltip = tooltip
    return ft.DataCell(ft.Container(width=t.px(width), content=text) if width else text)


def build_info_table(articles: list[dict], t) -> ft.Control:
    p = t.palette
    columns = [ft.DataColumn(C.text(name, t, "caption", p.text_muted, "semibold"))
               for name in INFO_COLUMNS]
    rows: list[ft.DataRow] = []
    for a in articles:
        cells = [
            _cell(a["folder"], t, muted=True, width=130),
            _cell(a["volume"], t),
            _cell(a["issue"], t),
            _cell(a["year"], t),
            _cell(a["span"], t),
            _cell(a["pages"], t),
            _cell(a["actual_text"], t, strong=a["mismatch"],
                  color=p.error if a["mismatch"] else None),
            _cell(a["font_line"], t, muted=True),
            _cell(a["author"], t),
            _cell(a["title"], t, width=240, tooltip=a["title"]),
        ]
        rows.append(ft.DataRow(cells=cells, color=p.error_soft if a["mismatch"] else None))
    table = ft.DataTable(
        columns=columns,
        rows=rows,
        column_spacing=14,
        horizontal_margin=6,
        heading_row_height=30,
        data_row_min_height=24,
        data_row_max_height=36,
        show_bottom_border=True,
        border=C.box(t),
        border_radius=t.radius("sm"),
    )
    return ft.Row(scroll=ft.ScrollMode.AUTO, controls=[table])


class InfoCheckCard:
    """一期信息检查的结果卡：摘要 + 10 列表格 + 打开报告。"""

    def __init__(self, t, *, on_open=None) -> None:
        self.t = t
        self.on_open = on_open
        self.report = ""
        self.articles: list[dict] = []
        self.notes: list[str] = []
        self.chip = C.status_chip("还没检查", t, "idle")
        self.summary = C.text(EMPTY_INFO, t, "small", t.palette.text_muted)
        self.warning = C.text("", t, "small", t.palette.warn, visible=False)
        self.open_btn = C.secondary_button("打开报告", self._open, t, icon="OPEN_IN_NEW",
                                           disabled=True)
        self.table_box = ft.Column(spacing=0, controls=[], visible=False)
        self.empty = C.empty_state(
            "TABLE_CHART", "还没有检查结果",
            "选一期的目录，点「运行」；表格字段来自引擎，外壳不自己解析 tex。",
            t, height=120,
        )
        self.root = C.card(ft.Column(spacing=t.sp("1"), controls=[
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("信息检查结果", t, "h3", weight="semibold"),
                self.chip,
                ft.Container(expand=True),
                self.open_btn,
            ]),
            self.summary,
            self.warning,
            self.empty,
            self.table_box,
        ]), t)

    def _open(self, e=None) -> None:
        if self.report and self.on_open:
            self.on_open(self.report)

    def busy(self) -> None:
        self.reset()
        self.chip.label = "检查中"
        self.summary.value = "正在扫描整期 tex…（完成后这里出表格）"
        self._update()

    def reset(self) -> None:
        self.report = ""
        self.articles = []
        self.notes = []
        self._set_chip("还没检查", "idle")
        self.summary.value = EMPTY_INFO
        self.summary.color = self.t.palette.text_muted
        self.warning.visible = False
        self.table_box.controls = []
        self.table_box.visible = False
        self.empty.visible = True
        self.open_btn.disabled = True
        self._update()

    def show(self, payload, *, report: str = "") -> None:
        self.report = report or ""
        self.articles = normalize_articles(payload)
        self.notes = inconsistencies(payload, self.articles)
        self.summary.value = info_summary(payload, articles=self.articles, notes=self.notes)
        self.summary.color = self.t.palette.text
        state = info_level(payload, self.articles, self.notes)
        self._set_chip({"done": "一致", "warn": "需要核对", "idle": "还没检查"}[state], state)
        self.warning.visible = False
        self.table_box.controls = [build_info_table(self.articles, self.t)] if self.articles else []
        self.table_box.visible = bool(self.articles)
        self.empty.visible = not self.articles
        self.open_btn.disabled = not self.report
        self._update()

    def show_unreadable(self, report: str = "") -> None:
        self.report = report or ""
        self.articles = []
        self.notes = []
        self._set_chip("读不回", "warn")
        self.summary.value = "表格留空：报告已生成，但结构化数据没读到。"
        self.warning.value = UNREADABLE_INFO
        self.warning.visible = True
        self.table_box.controls = []
        self.table_box.visible = False
        self.empty.visible = True
        self.open_btn.disabled = not self.report
        self._update()

    def show_failed(self, code: int) -> None:
        self.report = ""
        self.articles = []
        self._set_chip("检查失败", "error")
        self.summary.value = f"❌ 检查失败（退出码 {code}）。请把上面的报错发给技术人员。"
        self.warning.visible = False
        self.table_box.controls = []
        self.table_box.visible = False
        self.empty.visible = True
        self.open_btn.disabled = True
        self._update()

    def _set_chip(self, label: str, state: str) -> None:
        self.chip.content.value = label
        color = {"done": self.t.palette.success, "warn": self.t.palette.warn,
                 "error": self.t.palette.error}.get(state, self.t.palette.text_muted)
        self.chip.bgcolor = {"done": self.t.palette.success_soft,
                             "warn": self.t.palette.warn_soft,
                             "error": self.t.palette.error_soft}.get(
                                 state, self.t.palette.surface_2)
        self.chip.content.color = color

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass


# ── UI：一校核查结果卡 ─────────────────────────────────────────────────────
def _stat(label: str, value: str, t) -> ft.Container:
    return ft.Container(
        expand=True,
        bgcolor=t.palette.surface_2,
        border_radius=t.radius("sm"),
        padding=C._pad(t, 10, 8, 10, 8),
        content=ft.Column(spacing=0, controls=[
            C.text(label, t, "caption", t.palette.text_muted),
            C.text(value, t, "h3", weight="semibold"),
        ]),
    )


class ProofCheckCard:
    """一校核查结果卡：四项计数 + 命中规则前 5 + 打开报告。"""

    def __init__(self, t, *, on_open=None) -> None:
        self.t = t
        self.on_open = on_open
        self.report_path = ""
        self.counts = {"manual": 0, "fixable": 0, "total": 0, "rule_count": 0}
        self.chip = C.status_chip("还没检查", t, "idle")
        self.conclusion = C.text("还没有核查结果", t, "small", t.palette.text_muted)
        self.stats_row = ft.Row(spacing=t.sp("1"), controls=[])
        self.rules_box = ft.Column(spacing=t.sp("half"), controls=[])
        self.warning = C.text("", t, "small", t.palette.warn, visible=False)
        self.open_btn = C.secondary_button("打开报告", self._open, t, icon="OPEN_IN_NEW",
                                           disabled=True)
        self.root = C.card(ft.Column(spacing=t.sp("1"), controls=[
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                C.text("一校核查结果", t, "h3", weight="semibold"),
                self.chip,
                ft.Container(expand=True),
                self.open_btn,
            ]),
            self.conclusion,
            self.stats_row,
            self.warning,
            self.rules_box,
        ]), t)
        self.reset()

    def _open(self, e=None) -> None:
        if self.report_path and self.on_open:
            self.on_open(self.report_path)

    def reset(self) -> None:
        self.report_path = ""
        self.counts = {"manual": 0, "fixable": 0, "total": 0, "rule_count": 0}
        self._set_chip("还没检查", "idle")
        self.conclusion.value = "还没有核查结果；跑完这里显示计数与命中规则前 5。"
        self.conclusion.color = self.t.palette.text_muted
        self.stats_row.controls = [
            _stat("需人工", "—", self.t), _stat("可自动改", "—", self.t),
            _stat("命中合计", "—", self.t), _stat("规则数", "—", self.t),
        ]
        self.rules_box.controls = []
        self.warning.visible = False
        self.open_btn.disabled = True
        self.root.visible = False
        self._update()

    def show(self, report: ProofReport, *, report_path: str = "") -> None:
        self.report_path = report_path or ""
        self.counts = {
            "manual": report.manual,
            "fixable": report.fixable,
            "total": report.total,
            "rule_count": report.rule_count,
        }
        self.root.visible = True
        if not report.ok:
            self._set_chip("读不回", "warn")
            self.conclusion.value = "报告已生成，但摘要没读到（计数留空）。可直接打开报告。"
            self.conclusion.color = self.t.palette.warn
            self.warning.value = "⚠️ 报告已生成，但读不回结构化摘要（计数留空）。"
            self.warning.visible = True
            self.stats_row.controls = [_stat(label, "—", self.t) for label in
                                       ("需人工", "可自动改", "命中合计", "规则数")]
            self.rules_box.controls = []
            self.open_btn.disabled = not self.report_path
            self._update()
            return
        self.warning.visible = False
        state = "warn" if report.manual else "done"
        self._set_chip("有命中" if report.manual else "没有命中", state)
        self.conclusion.value = report.conclusion or (
            f"❗ 有 {report.manual} 处需要人工处理" if report.manual
            else "✅ 没有需要人工处理的命中")
        self.conclusion.color = self.t.palette.warn if report.manual else self.t.palette.success
        self.stats_row.controls = [
            _stat("需人工", str(report.manual), self.t),
            _stat("可自动改", str(report.fixable), self.t),
            _stat("命中合计", str(report.total), self.t),
            _stat("规则数", str(report.rule_count), self.t),
        ]
        rules = report.top_rules()
        if rules:
            self.rules_box.controls = [
                ft.Row(spacing=self.t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           C.badge(rule.rid, self.t, "warn"),
                           C.text(rule.title, self.t, "small", self.t.palette.text,
                                  expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                           C.text(f"{rule.count} 处", self.t, "small",
                                  self.t.palette.text_muted, "semibold"),
                       ]) for rule in rules
            ]
        else:
            self.rules_box.controls = [C.text(
                "没有需要人工判断的条目（可自动改项已静默）。", self.t, "small",
                self.t.palette.text_muted)]
        self.open_btn.disabled = not self.report_path
        self._update()

    def _set_chip(self, label: str, state: str) -> None:
        self.chip.content.value = label
        color = {"done": self.t.palette.success, "warn": self.t.palette.warn,
                 "error": self.t.palette.error}.get(state, self.t.palette.text_muted)
        self.chip.bgcolor = {"done": self.t.palette.success_soft,
                             "warn": self.t.palette.warn_soft,
                             "error": self.t.palette.error_soft}.get(
                                 state, self.t.palette.surface_2)
        self.chip.content.color = color

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass


# ── 页面钩子（pages.py 只调这些，不复制排版）────────────────────────────────
def single_card(page) -> ProofCheckCard:
    card = ProofCheckCard(page.t, on_open=page.app.open_path)
    card.root.visible = page.flow_key == "proofcheck"
    return card


def issue_card(page) -> InfoCheckCard:
    return InfoCheckCard(page.t, on_open=page.app.open_path)


def on_flow_switch(page) -> None:
    card = getattr(page, "_proof", None)
    if card is not None:
        card.reset()
        card.root.visible = page.flow_key == "proofcheck"


def on_proof_log(page) -> None:
    if page.flow_key != "proofcheck":
        return
    options = page.app.store.get("options", {}) or {}
    for line in proof_header_lines(
            page.field("proof_tex"), page.field("proof_lang"),
            bool(options.get("proofcheck_fix", False))):
        page._on_line(line)


def before_run(page) -> None:
    card = getattr(page, "_proof", None)
    if card is not None and page.flow_key == "proofcheck":
        card.reset()
        card.root.visible = True


def after_proof_run(page, call, code: int, artifact: str, text: str,
                    level: str) -> tuple[str, str]:
    """跑完一校：读报告 → 结果卡；并把完成文案换成 PC-011 的句式。"""
    if page.flow_key != "proofcheck":
        return text, level
    card = getattr(page, "_proof", None)
    if page.app.cancel_requested:
        if card is not None:
            card.reset()
        return text, level
    tex = page.field("proof_tex")
    options = page.app.store.get("options", {}) or {}
    fix = bool(options.get("proofcheck_fix", False))
    if code in (0, 1):
        report = read_proof_report(artifact)
        if card is not None:
            card.show(report, report_path=artifact)
    else:
        if card is not None:
            card.reset()
            card.root.visible = True
            card.conclusion.value = f"❌ 核查失败（退出码 {code}）"
            card.conclusion.color = page.t.palette.error
            card._update()
    return proof_result_text(code, tex=tex, fix=fix), level


def proofcheck_section(page) -> ft.Control:
    """PC-012/015/016/019/048：一校步骤的输入卡（语种下拉 + 帮助）。"""
    t = page.t
    options = page.app.store.get("options", {}) or {}
    current = page.app.store.get("form.proof_lang", "") or ""
    if current not in ("", "zh", "en"):
        current = ""

    dropdown = ft.Dropdown(
        label="语种",
        value=current,
        width=t.px(150),
        dense=True,
        text_size=t.font("body"),
        options=[ft.DropdownOption(key="", text="自动"),
                 ft.DropdownOption(key="zh", text="中文"),
                 ft.DropdownOption(key="en", text="英文")],
        on_select=lambda e: _store_lang(page, dropdown),
    )
    page.fields["proof_lang"] = [dropdown]
    help_lines = [
        "输入初排后的 tex；报告默认写到同目录 `<名字>_一校核查.md`，**不覆盖稿件**。",
        "语种默认「自动」由引擎按正文判断；中英混排识别不稳时再手动选中文 / 英文。",
        "默认只出报告；勾「顺手把无歧义的改掉」才另写 `<名字>_一校核查后.tex`，输入文件不动。",
        "可自动改项在初排已默认处理，本报告只列需要人工判断的条目（每条给依据与建议）。",
    ]
    return ft.Column(spacing=t.sp("2"), controls=[
        C.section_title("输入", t, note="初排后的 tex · 只读"),
        page.add_picker("初排 tex", "proof_tex", "初排产物 .tex（可拖入）", kind="file"),
        ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            dropdown,
            C.text("运行中会在下方日志实时滚动；完成后看结果卡。", t, "caption",
                   t.palette.text_faint),
        ]),
        C.option_row(
            "顺手把无歧义的改掉（另存新文件）",
            "传 --fix：另写 <stem>_一校核查后.tex；输入文件不改",
            t, icon="AUTO_FIX_HIGH",
            checked=bool(options.get("proofcheck_fix", False)),
            on_change=lambda e: page.app.set_option("proofcheck_fix", e.control.value),
        ),
        C.card(ft.Column(spacing=t.sp("half"), controls=[
            C.text(line, t, "caption", t.palette.text_muted) for line in help_lines
        ]), t),
    ])


def _store_lang(page, dropdown) -> None:
    value = dropdown.value or ""
    if value not in ("", "zh", "en"):
        value = ""
    page.app.store.set("form.proof_lang", value)
    page.mark("proof_lang", value=value)


def info_params(page) -> list[ft.Control]:
    t = page.t
    return [
        C.text(INFO_DIR_HINT.replace("**", ""), t, "caption", t.palette.text_muted),
        C.text(INFO_RUN_HINT.replace("`", ""), t, "caption", t.palette.text_faint),
    ]


def before_issue_step(page, key: str) -> None:
    card = getattr(page, "_info", None)
    if card is not None and key == "infocheck":
        card.busy()


async def after_issue_step(page, key: str, call, code: int, cancelled: bool,
                           artifact: str) -> None:
    """PC-030/035：信息检查成功后，用 `--preview` 取同一套解析结果填表。"""
    if key != "infocheck":
        return
    card = getattr(page, "_info", None)
    if card is None:
        return
    if cancelled:
        card.reset()
        return
    if code != 0:
        card.show_failed(code)
        return
    folder = page._issue_dir()
    payload = await asyncio.to_thread(
        EB.read_json_command,
        EB.infocheck_call(folder=folder, preview=True),
        notes=page.app.notes_repo,
        timeout=180.0,
    )
    if not payload.get("ok"):
        card.show_unreadable(artifact)
        return
    card.show(payload.get("json") or {}, report=artifact)
