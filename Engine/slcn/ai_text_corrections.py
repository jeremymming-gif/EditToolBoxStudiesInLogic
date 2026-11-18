r"""文字纠错的硬边界（DESIGN §7）：只改**可确认的局部错字/漏字/语法**。

为什么这条边界要写成代码
------------------------
初排的正文是编辑部要发的稿子。「顺手润色」听起来无害，但它会：
  · 改掉作者的语气与术语（学术事实的一部分）；
  · 悄悄动到公式、引用键、标签，让编译或键闭合崩掉；
  · 用「看起来更顺」当理由，把一个错字修成另一处错。

设计稿 §7 给的是**硬边界**，不是风格建议：

  · 只允许局部替换，位置必须能**唯一定位**（原文片段 + 上下文）；
  · 命令/参数/数学/引用键/标签/路径一律不碰；
  · 缺字必须有**明确证据**，纯属猜测的只报告；
  · 所有改动针对**同一份基线**计算（不做累积替换漂移）；
  · 源文件**只读**：本模块只产出新文本 + 判据，写不写由调用方决定。

本模块是**纯函数**：吃文本、吐文本与逐条判定，不写文件、不联网、不调模型。
这样「边界」能被逐条断言，而落到文件由 `slcn.ai_workflow` 负责（备份/原子替换/撤销）。
"""

from __future__ import annotations

import hashlib
import re

SCHEMA = "slcn.ai_text_corrections/1"

#: 判据分类（报告与界面共用；不要另造一套）
CAT_TYPO = "typo"          # 明显错字
CAT_MISSING = "missing"    # 漏字（必须有证据）
CAT_GRAMMAR = "grammar"    # 局部语法（缺虚词/搭配错误）
CAT_UNCERTAIN = "uncertain"  # 不确定 → 只报告，默认不采纳

#: 允许采纳的分类；uncertain 永远不自动采纳（§7 默认不采纳）
ADOPTABLE = frozenset((CAT_TYPO, CAT_MISSING, CAT_GRAMMAR))

ST_APPLIED = "applied"
ST_REPORTED = "reported"     # 只报告
ST_REJECTED = "rejected"     # 判据拒绝

#: 每处替换最多跨多少字符：超过就不是「局部替换」，是重写（§7 禁止）。
MAX_SPAN = 40
#: 上下文窗口：重复片段靠它唯一定位。
CTX = 24

PROTECT_NOTE = "命令/参数/数学/引用键/标签/路径内不做替换"


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── 保护面：这些区间里的字符一个都不许动 ────────────────────────────────

_RE_CMD = re.compile(r"\\[A-Za-z@]+\*?")
#: 命令**连同参数**：`\emph{teh}` / `\section{introdution}` / `\caption{a typo}`。
#: §7 ③ 与 `PROTECT_NOTE` 都写着「命令/参数内不做替换」，只保护命令名会漏掉参数区，
#: 让 `\caption{a typo}` 这种全小写参数被当成正文改掉——这是 B1 的成因。
#: 只吃**同一层**花括号（嵌套参数由内层再匹配一次），`[]` 可选参数一并算进去。
_RE_CMD_ARG = re.compile(
    r"\\[A-Za-z@]+\*?\s*(?:\[[^\[\]]*\]\s*)*\{[^{}]*\}")
#: `$$…$$` 是**行间**公式（独立成行），必须先于行内 `$…$` 匹配，
#: 否则会被当行内数学，甚至让两个 `$$` 之间的正文被整段吞进保护区。
_RE_MATH_DOUBLE = re.compile(r"\$\$.+?\$\$", re.S)
#: 行内 `$…$`：两头都不能是 `$$`，且中间不许再有裸 `$`——
#: 这样 `$` 的配对**绝不会跨过** `$$` 定界符（§7 ③ 数学不碰，正文照改）。
_RE_MATH_INLINE = re.compile(r"(?<!\\)\$(?!\$)(?:[^$]|\\\$)*?(?<!\\)\$(?!\$)", re.S)
_RE_MATH_DISPLAY = re.compile(r"\\\[.+?\\\]|\\\(.+?\\\)", re.S)
_RE_MATH_ENV = re.compile(
    r"\\begin\{(equation|align|gather|multline|eqnarray|math|displaymath)\*?\}.+?"
    r"\\end\{\1\*?\}", re.S)
_RE_CITE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?(?:\[[^\]]*\])*\{[^{}]*\}")
_RE_LABEL = re.compile(r"\\(?:label|ref|eqref|pageref|autoref|cref|Cref)\{[^{}]*\}")
_RE_URL = re.compile(r"\\(?:url|href|path|includegraphics)\*?(?:\[[^\]]*\])?\{[^{}]*\}")
_RE_VERB = re.compile(r"\\begin\{verbatim\}.+?\\end\{verbatim\}|\\verb\|[^|]*\|", re.S)
_RE_VERBATIMISH = re.compile(r"\\begin\{(lstlisting|minted)\}.+?\\end\{\1\}", re.S)
_RE_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
_RE_PATHY = re.compile(r"(?:\.{0,2}/)?(?:[\w.-]+/){2,}[\w.-]*")
#: 单个首字母大写的词：多半是人名/地名/机构名——改它就是改术语（§7 禁止），只报告。
_RE_PROPER = re.compile(r"^[A-Z][A-Za-z'\-]{1,30}$")

#: 顺序即优先级：更「外层」的保护先出现（`$$…$$` 必须早于行内 `$…$`，
#: 命令连同参数必须早于裸命令名），否则区间原因会标错。
_PROTECT_PATTERNS = (
    _RE_VERB, _RE_VERBATIMISH, _RE_MATH_ENV, _RE_MATH_DISPLAY, _RE_MATH_DOUBLE,
    _RE_MATH_INLINE, _RE_CITE, _RE_LABEL, _RE_URL, _RE_COMMENT, _RE_CMD_ARG,
    _RE_CMD, _RE_PATHY,
)
_PROTECT_REASONS = ("verbatim", "代码块", "数学环境", "行间数学", "行间数学",
                    "行内数学", "引用键", "标签/引用", "URL/路径命令", "注释",
                    "命令", "命令", "路径")


def protected_spans(text: str) -> list[tuple[int, int, str]]:
    """返回不可改动区间 [(start, end, 原因)]；已按起点排序、互不依赖。

    同一段文字可能同时被多条规则命中（`\\cite{…}` 既是「引用键」，也落在
    「命令+参数」里）。**原因取优先级更高的那条**：完全被前面的区间包住的后到区间丢弃，
    否则 `\\cite` 会被误标成「命令」，判定理由就跟着错。
    """
    raw: list[tuple[int, int, int, str]] = []
    for idx, (pat, why) in enumerate(zip(_PROTECT_PATTERNS, _PROTECT_REASONS)):
        for m in pat.finditer(text or ""):
            if m.end() > m.start():
                raw.append((m.start(), m.end(), idx, why))
    kept: list[tuple[int, int, int, str]] = []
    for start, end, idx, why in sorted(raw, key=lambda r: (r[0], r[2], -r[1])):
        covered = any(ks <= start and ke >= end and kidx < idx
                      for ks, ke, kidx, _ in kept)
        if not covered:
            kept.append((start, end, idx, why))
    spans = sorted((s, e, why) for s, e, _, why in kept)
    return spans


def _inside(spans, start: int, end: int) -> str | None:
    for s, e, why in spans:
        if start < e and end > s:
            return why
    return None


# ── 逐条判定 ──────────────────────────────────────────────────────────────


class Item:
    """一条候选改动。字段都是可 JSON 化的纯数据。"""

    __slots__ = ("item_id", "category", "old", "new", "before", "after",
                 "context", "status", "reason", "start", "end")

    def __init__(self, *, category: str, old: str, new: str,
                 before: str = "", after: str = "", context: str = "",
                 item_id: str = ""):
        self.category = category
        self.old = old
        self.new = new
        self.before = before
        self.after = after
        self.context = context
        self.item_id = item_id or _item_id(category, old, new, context)
        self.status = ST_REPORTED
        self.reason = ""
        self.start = -1
        self.end = -1

    def as_dict(self) -> dict:
        return {"item_id": self.item_id, "category": self.category,
                "old": self.old, "new": self.new, "before": self.before,
                "after": self.after, "context": self.context,
                "status": self.status, "reason": self.reason,
                "start": self.start, "end": self.end}


def _item_id(category: str, old: str, new: str, context: str) -> str:
    raw = "\x1f".join((category, old, new, context))
    return "t" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def locate(text: str, old: str, *, before: str = "", after: str = "") -> tuple[int, int, str]:
    """在 `text` 里唯一定位 `old`；返回 (start, end, 失败原因)。

    重复片段必须靠上下文定：只出现一次直接用；出现多次但 `before`/`after` 能筛到
    唯一一处才算定位成功，否则**报告而不猜**（§7「重复锚点不能只取第一处」）。
    """
    if not old:
        return -1, -1, "原文片段为空"
    hits = [m.start() for m in re.finditer(re.escape(old), text)]
    if not hits:
        return -1, -1, "在正文里找不到这段原文（可能已被别的改动动过）"
    if len(hits) == 1:
        return hits[0], hits[0] + len(old), ""
    if before or after:
        # 上下文只比**靠锚点那侧**的 CTX 个字符（前文比尾部、后文比头部）：给长给短
        # 不该改变判定结果，否则同一处能不能定位取决于模型随手多写几个字
        # （B2：之前整串比较，`CTX` 就成了没用的常量）。文件首尾不够长时只比能比的部分。
        head = before[-CTX:] if before else ""
        tail_expect = after[:CTX] if after else ""
        narrowed = []
        for h in hits:
            if head:
                take = min(len(head), h)
                if take <= 0 or text[h - take:h] != head[len(head) - take:]:
                    continue
            if tail_expect:
                tail = h + len(old)
                take = min(len(tail_expect), max(0, len(text) - tail))
                if take <= 0 or text[tail:tail + take] != tail_expect[:take]:
                    continue
            narrowed.append(h)
        if not narrowed:
            return -1, -1, "重复片段与给定上下文对不上"
        if len(narrowed) == 1:
            return narrowed[0], narrowed[0] + len(old), ""
        return -1, -1, f"重复片段出现 {len(narrowed)} 次，上下文不足以唯一定位（不猜）"
    return -1, -1, f"片段在正文里出现 {len(hits)} 次，缺上下文无法唯一定位（不猜）"


def judge(text: str, item: Item) -> Item:
    """给一条候选项定状态（不改文本、不改 item 之外的东西）。"""
    if item.category == CAT_UNCERTAIN:
        item.status = ST_REPORTED
        item.reason = "不确定的改动：按 §7 只报告，默认不采纳"
        return item
    if item.category not in ADOPTABLE:
        item.status = ST_REJECTED
        item.reason = f"不在允许采纳的分类里（{item.category}）"
        return item
    if not item.old or not item.new:
        item.status = ST_REJECTED
        item.reason = "原文或替换文本为空"
        return item
    if item.old == item.new:
        item.status = ST_REJECTED
        item.reason = "原文与替换文本相同（空改动）"
        return item
    if len(item.old) > MAX_SPAN or len(item.new) > MAX_SPAN:
        item.status = ST_REJECTED
        item.reason = (f"跨度超过 {MAX_SPAN} 字：这不是局部替换，属于重写"
                       "（§7 禁止润色/改写）")
        return item
    if _RE_PROPER.match(item.old.strip()) and _RE_PROPER.match(item.new.strip()):
        item.status = ST_REPORTED
        item.reason = ("两端都是首字母大写的单词：像人名/地名/机构名，改它属于改术语"
                       "（§7 禁止）——只报告，请人工判断")
        return item
    start, end, why = locate(text, item.old, before=item.before, after=item.after)
    if start < 0:
        item.status = ST_REPORTED
        item.reason = why
        return item
    spans = protected_spans(text)
    hit = _inside(spans, start, end)
    if hit:
        item.status = ST_REJECTED
        item.reason = f"落在保护区内（{hit}）：{PROTECT_NOTE}"
        return item
    hit_new = _inside(spans, start, start + len(item.new))
    if hit_new and hit_new != hit:
        item.status = ST_REJECTED
        item.reason = f"替换后会伸进保护区（{hit_new}）"
        return item
    item.start, item.end = start, end
    item.status = ST_APPLIED
    item.reason = "局部替换，位置唯一，不在保护区"
    return item


def apply_items(text: str, items, *, baseline_sha: str = "") -> dict:
    """对**同一份基线**应用所有被判为 ST_APPLIED 的改动（§7 不累积漂移）。

    返回 {text, applied, reported, rejected, baseline_sha, source_sha, unchanged}。
    重放顺序从后往前，避免偏移互相影响；重叠改动**后者让位**并转成只报告。
    """
    base = text or ""
    base_sha = baseline_sha or sha256_text(base)
    judged = [judge(base, it) if isinstance(it, Item) else it for it in (items or [])]
    ok = [it for it in judged if it.status == ST_APPLIED]
    ok.sort(key=lambda it: it.start, reverse=True)
    out = base
    kept: list[Item] = []
    last_start = len(base) + 1
    for it in ok:
        if it.end > last_start:  # 与已采纳的改动重叠
            it.status = ST_REPORTED
            it.reason = "与另一处已采纳改动重叠（同一基线，不叠加）"
            continue
        out = out[:it.start] + it.new + out[it.end:]
        last_start = it.start
        kept.append(it)
    return {
        "schema": SCHEMA,
        "text": out,
        "baseline_sha256": base_sha,
        "source_sha256": sha256_text(base),
        "unchanged": out == base,
        "applied": [it.as_dict() for it in kept],
        "reported": [it.as_dict() for it in judged if it.status == ST_REPORTED],
        "rejected": [it.as_dict() for it in judged if it.status == ST_REJECTED],
    }


def parse_suggestions(payload) -> list[Item]:
    """把 AI 回复里的结构化建议解析成 Item 列表（字段名宽松，未知即忽略）。

    接受 [{category|kind, old|find|original, new|replace|suggestion, before, after,
    context}]；分类名做**保守**映射：认不出的类别一律归 uncertain（只报告）。
    """
    out: list[Item] = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        cat = str(raw.get("category") or raw.get("kind") or "").strip().lower()
        if cat in ("typo", "错字", "错别字"):
            cat = CAT_TYPO
        elif cat in ("missing", "漏字", "缺字"):
            cat = CAT_MISSING
        elif cat in ("grammar", "语法"):
            cat = CAT_GRAMMAR
        else:
            cat = CAT_UNCERTAIN
        old = str(raw.get("old") or raw.get("find") or raw.get("original") or "")
        new = str(raw.get("new") or raw.get("replace") or raw.get("suggestion") or "")
        out.append(Item(category=cat, old=old, new=new,
                        before=str(raw.get("before") or ""),
                        after=str(raw.get("after") or ""),
                        context=str(raw.get("context") or ""),
                        item_id=str(raw.get("item_id") or "")))
    return out


def summarise(result: dict) -> list[str]:
    """人话摘要（三类分别说，不合并成「全部核实」）。"""
    n_ok = len(result.get("applied") or [])
    n_rep = len(result.get("reported") or [])
    n_bad = len(result.get("rejected") or [])
    lines = [f"**文字纠错**：可采纳 {n_ok} 处；只报告 {n_rep} 处；判据拒绝 {n_bad} 处"
             "（源文件只读，是否写入由调用方决定）"]
    if result.get("unchanged"):
        lines.append("本次没有实际文本改动。")
    return lines


__all__ = [
    "SCHEMA", "ADOPTABLE", "CAT_GRAMMAR", "CAT_MISSING", "CAT_TYPO", "CAT_UNCERTAIN",
    "Item", "MAX_SPAN", "PROTECT_NOTE", "ST_APPLIED", "ST_REJECTED",
    "ST_REPORTED", "apply_items", "judge", "locate", "parse_suggestions",
    "protected_spans", "sha256_text", "summarise",
]
