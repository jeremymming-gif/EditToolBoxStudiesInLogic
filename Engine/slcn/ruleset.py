"""B-288：任务书 v2 的**规则集**（29 条校对规则 + 11 条 bib 格式硬要求）。

为什么单独成模块：任务书 v2 的「免报清单」必须与 `proofcheck.RULES` **同源**——
手抄 29 条规则是这一批最容易漂的地方（PROOF_RULES.md 已因手抄与代码脱节过一次）。
这里只做三件事，全部是纯函数：

1. `RULESET_ID`：规则集版本串（`proof29` = 29 条校对规则，`fmt11` = 第三节 11 条
   格式硬要求，`v1` = 本模块口径第 1 版）。
2. `rules_digest()`：把 29 条的 id/标题/范围/默认动作压成短摘要，写进任务卡头。
   规则一改，摘要就变——编辑与验收方都能一眼看出「这张卡对应哪套规则」。
3. `no_report_lines(lang)`：给任务卡生成 29 行「`id` · 检查什么 · 默认动作」。
   **逐条从 `proofcheck.RULES` 现生成，本模块不保存第二份规则表**；英文标题只是
   翻译表（按 id 索引），改规则不需要改这里。

默认动作的判定口径在 `proofcheck.default_action()`（与 `docs/PROOF_RULES.md` §二
的「默认动作」列同义）。本模块只负责把它翻成任务卡要显示的样子。
"""

from __future__ import annotations

import hashlib

from . import proofcheck

#: 规则集版本（任务书 v2 的 `ruleset`；`fmt11` 指 `refmark.FORMAT_MD` 的 11 条硬要求）。
RULESET_ID = "proof29+fmt11+v1"

#: 第三节 bib 格式硬要求的条数（`refmark.FORMAT_MD` 里的编号项）。
#: 不 import refmark：refmark → taskcard → ruleset 会成环；测试里用
#: `refmark.FORMAT_MD` 现数一遍并断言与本常量相等（见 `tests/test_ruleset.py`）。
FORMAT_ITEM_COUNT = 11

#: 英文标题（仅任务卡显示用；中文标题是 `proofcheck.RULES` 的唯一出处）。
_EN_TITLES = {
    "ZH-01": "Half-width comma between Chinese characters",
    "ZH-02": "Half-width period between Chinese characters",
    "ZH-03": "Half-width colon between Chinese characters",
    "ZH-04": "Half-width semicolon between Chinese characters",
    "ZH-05": "Half-width question/exclamation mark between Chinese characters",
    "ZH-06": "Half-width quote right after a Chinese enumeration comma",
    "ZH-07": "Half-width parentheses next to Chinese text",
    "ZH-08": "Half-width hyphen next to Chinese text",
    "ZH-09": "Page range \"N-N\" without the double-dash command",
    "ZH-10": "Footnote after a comma/period (should come before the punctuation)",
    "ZH-11": "Citation after a comma (should come before the punctuation)",
    "ZH-12": "Footnote right after sentence-final punctuation, gap to CJK too small",
    "ZH-13": "Three ASCII dots used as an ellipsis",
    "ZH-14": "Vertical bar in a set expression should be \\mid",
    "ZH-15": "\\mathbf used for bold math",
    "ZH-16": "Missing ~ before figure/table/equation/Chinese-number references",
    "ZH-17": "\\\\ line break inside \\caption",
    "ZH-18": "Latin letters/digits in a kai run not upright",
    "ZH-19": "Missing space between personal-name initials (J.N.)",
    "ZH-20": "\\textbf used for bold inside a kai run",
    "ZH-21": "Footnote before sentence-final punctuation (should come after it)",
    "ZH-22": "\\footnote inside a table/float gets swallowed",
    "ZH-23": "Sentence-final punctuation placed after a parenthesized citation group",
    "EN-01": "Missing space before the parenthesized year (John(2009))",
    "EN-02": "Missing space after an English colon",
    "EN-03": "Curly quotes / CJK punctuation in English text",
    "EN-05": "Page range missing the \\, gap (target form `pp.\\,x--x`)",
    "EN-06": "Section-title capitalization to be checked",
    "ALL-01": "Bitmap figure (.jpg/.png) instead of vector",
}

#: 默认动作 → 英文（任务卡 en 版显示用；判定仍在 `proofcheck.default_action`）。
_EN_ACTIONS = {
    "初排自动改": "auto-fixed at initial layout",
    "初排自动搬位": "repositioned at initial layout",
    "仅 --fix": "--fix only",
    "只报（噪声大）": "report only (noisy)",
    "只报": "report only",
}


def rule_ids() -> tuple[str, ...]:
    """规则 id（按 `proofcheck.RULES` 顺序）；测试与文档核对用。"""
    return tuple(r.id for r in proofcheck.RULES)


def rules_digest() -> str:
    """29 条规则的短摘要（12 位十六进制）。

    输入是「规则集版本 + 每条 id/标题/范围/noisy/默认动作」，不含正文文案与正则——
    改文案不影响编辑手里的任务卡，改规则本身才影响。
    """
    h = hashlib.sha256()
    h.update(RULESET_ID.encode("utf-8"))
    for r in proofcheck.RULES:
        h.update((
            f"\x1f{r.id}\x1f{r.title}\x1f{r.scope}\x1f{int(r.noisy)}"
            f"\x1f{proofcheck.default_action(r)}"
        ).encode("utf-8"))
    return h.hexdigest()[:12]


def no_report_lines(lang: str = "zh") -> list[str]:
    """任务卡「免报清单」的 29 行（`id` · 检查什么 · 默认动作）。

    返回的是**行内容**（不带列表前缀），由 `taskcard.render_card` 加 `  - `。
    `lang="en"` 用英文标题/动作；条数与 id 集合与 zh **完全相同**（C4）。
    """
    en = str(lang).lower() == "en"
    out: list[str] = []
    for r in proofcheck.RULES:
        action = proofcheck.default_action(r)
        if en:
            title = _EN_TITLES.get(r.id, r.title)
            action = _EN_ACTIONS.get(action, action)
        else:
            title = r.title
        out.append(f"`{r.id}` · {title} · {action}")
    return out
