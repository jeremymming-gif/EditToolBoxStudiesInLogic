"""正文引用替换（B-207 从 docxsource 纯搬运）：link_citations / link_numbered_citations / 脚注裸编号通道。"""

from __future__ import annotations

import re

from .wordcite import cue_evidence as _cue_evidence
from .wordcite import fn_in_math as _fn_in_math
from .wordcite import fn_in_paren_depths as _fn_in_paren_depths
from .wordcite import link_body_cue_citations as _link_body_cue_citations
from .wordcore import (RefEntry, TRANSLATED_NAMES)

# ── 正文引用替换 ──────────────────────────────────────────────────────────

# （Haack 1974） （Martin 2025，31-44） （参见李雷 2022） （例\parencite{Wen2025}）
_CITE_RE = re.compile(
    r"[（(]\s*(?:参见|见|例如|如|引自|转引自)?\s*"
    r"(?P<names>[^（）()\d]{1,40}?)\s*"
    r"(?P<year>\d{4}[a-z]?)"
    r"(?P<tail>[^（）()]{0,24}?)"
    r"[）)]"
)


def _normalize_locator(tail: str) -> str:
    r"""把引用尾巴里的页码统一成中文惯用式。

    人工成品的写法：`第3页`、`第632页`、`第31\cndash 44页`。
    源稿里则是 `p.632`、`P632`、`632-635`、`第 3 页` 各种混着来。

    ⚠️ 还要**去掉前导的逗号**：tail 常以 `，` 开头，而模板里已经有一个 `，`，
    不去掉就会输出 `（\parencite{key}，，p.632）`——实测踩到过。
    """
    t = tail.strip().strip("，,、;；:：").strip()
    if not t:
        return ""
    # 已经是「第 N 页」的形式
    m = re.fullmatch(r"第\s*(\d{1,5})\s*[-–—~]\s*(\d{1,5})\s*页?", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"第\s*(\d{1,5})\s*页", t)
    if m:
        return f"第{m.group(1)}页"
    # p.632 / pp.632-635 / P632
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*(\d{1,5})\s*[-–—~]\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}页"
    # 裸数字 / 数字区间
    m = re.fullmatch(r"(\d{1,5})\s*[-–—~]\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}页"
    # 其他（章节号之类）原样保留
    return t


# ── B-130（批73）：**脚注文本**里的裸编号引用 `参见[12]。` ────────────────────
# 与正文通道（`link_numbered_citations`，匹配中文括号组）分开：脚注里的裸编号
# `参见[12]。` / `参见吉尔伯特在[13]的批评` / `参见[13]，第8页。` **没有括号包裹**，
# 旧实现原样残留（86-08 实测 3 处：成品 PDF 悬空标记，且 Gilbert2002 因此被判未引）。
# 判据保守（spec §2.2，Lead 2026-10-21 裁决 B）：
#   ① 只在**脚注文本**里（调用方只在编号体例稿时传映射，见 `_footnote_num2key_gate`）；
#   ② `[N]` 必须是**裸编号**：不落在中文/英文括号组里（括号组归 `link_numbered_citations`）；
#   ③ `N ∈ ref_key_by_num`（映射可靠 1..N，否则不猜）；
#   ④ 逐处要有**引用引导语/语境证据**（参见/参看/详见/转引自/引自/参见文献/参照/参考/
#      例如/见/据）——「规则[3]条」这类非引用语义不得转换。
# 拿不准 → 原样保留 + warning（不猜、不丢信息）；转换只换 `[N]` 本身，
# 前后中文文字与页码定位（`参见[x]，第8页。`）原样保留。
# B-137（批75，B-130 残余）：同句引导语继承/枚举续引——见下方 `_FN_*` 常量与
# `link_footnote_bare_citations` 的 docstring；每处 N 仍独立校验映射与越界。
_FN_BARE_NUM_RE = re.compile(r"\[\s*(\d{1,3})\s*\]")
#: B-217：引导语判据（`_FN_LEAD_NEAR_RE` / `_FN_LEAD_FAR_RE`）与位置判据
#: （`_fn_in_paren_depths` / `_fn_in_math`）已抽到 `slcn/wordcite.py`——正文通道
#: （`link_body_cue_citations`）与脚注通道共用**同一份**判据（LEAD-LESSONS §4
#: 重复实现零容忍）；本文件顶部 import 保持既有名字，行为与从前逐字节相同。

# ── B-137（批75）：同句引导语继承（枚举/续引）──────────────────────────────────
# 现象（B-130 残余）：同一脚注里只有一个引导语、随后用 `；`/`，` 续列多个编号时，
# 只有**首处**有逐处引导语证据，其余原样保留——实测 kxj fn11
# 「可参见[6]的第二节或者[10]的第2.1.2节」只转 [6]；87-3「可参见[6]，第385-389页；
# [17]，第423-427页」只转 [6]；81/03、81/06、72/01 页码枚举同族。
# 规则（保守）：一句 = 句号/问号/叹号/换行分隔（**分号、逗号不结束句子**，枚举在句内）；
# 句内一旦有裸 [N] 按 B-130 原判据成功转换，就记锚点；锚点之后、同句内、且与锚点之间
# 只隔「位置语/连接语 + 标点/数字/西文字母」的裸 [N]（N∈映射、括号外、公式外）→ 继承
# 引导语转换；每个 N 独立校验映射与越界。中间夹普通叙述文字、跨句、或 [N] 后紧跟
# `条/款/项/个/种/次…`（「规则[3]条」类非引用）→ 不转（原样 + warning，不猜）。
_FN_SENT_END = frozenset("。！？!?\n")
_FN_GAP_ASCII_OK = frozenset(" \t\u3000\u00a0")
#: 锚点→候选之间**只允许**这些位置语/连接语（+标点/空白/数字/西文字母）
_FN_GAP_TOKEN_RE = re.compile(
    r"(?:注释|参见|参看|详见|另见|又见|亦见|并见|转引自|引自|参照|参考"
    r"|或者|以及|第|的|或|和|与|及|者|等|注|章|节|卷|页|册|版|篇|部|项|条|款"
    r"|[一二三四五六七八九十百千零两])")
_FN_GAP_PUNCT = frozenset(
    "，,、；;：:．.·…–—-－~～/／§#$&%*+=<>《》「」『』“”‘’\"'()（）[]【】")
#: 候选 `[N]` 后紧跟量词/单位（`[3]条`）→ 更像非引用，不继承
_FN_NONCITE_SUFFIX_RE = re.compile(r"^\s*(?:条|款|项|个|种|次|名|位|章|节|卷|页)")
#: 锚点与候选之间最多隔的字符数（实测最长 14；留余量到 32，防远距离误伤）
_FN_INHERIT_MAX_GAP = 32


def _fn_inherit_gap_ok(gap: str) -> bool:
    """B-137：同句继承判据——`gap`（锚点 → 候选之间的原文）是否只是位置/连接语 + 标点。"""
    if len(gap) > _FN_INHERIT_MAX_GAP:
        return False
    rest = gap
    while rest:
        m = _FN_GAP_TOKEN_RE.match(rest)
        if m:
            rest = rest[m.end():]
            continue
        ch = rest[0]
        if ch in _FN_GAP_ASCII_OK or ch in _FN_GAP_PUNCT or ch.isdigit() \
                or (ch.isascii() and ch.isalpha()):
            rest = rest[1:]
            continue
        return False
    return True


def link_footnote_bare_citations(
    text: str, num2key: dict[int, str]
) -> tuple[str, int, list[str]]:
    r"""把**脚注文本里的裸 `[N]`** 保守转换成 `\parencite{键}`（B-130 / B-137）。

    返回 `(新文本, 转换处数, 问题清单)`。只换 `[N]` 本身：
    `参见[12]。` → `参见\parencite{Gilbert2002}。`、
    `参见吉尔伯特在[13]的批评` → `参见吉尔伯特在\parencite{Gilbert2007}的批评`、
    `参见[13]，第8页。` → `参见\parencite{Gilbert2007}，第8页。`。

    B-137（批75）同句引导语继承：一句内（句号/问号/叹号/换行分隔；分号、逗号不结束）
    已有一处按引导语转换后，同句内只隔「位置语/连接语 + 标点/数字/西文」的后续裸 `[N]`
    继承引导语继续转换（枚举/续引）：
    `可参见[6]的第二节或者[10]的第2.1.2节` → 两处都转；
    `可参见[6]，第385-389页；[17]，第423-427页` → 两处都转。
    每个 N 仍独立校验映射（越界不转）；中间夹普通叙述文字、跨句、或 `[N]` 后紧跟
    `条/款/项/个/种/次…`（「规则[3]条」类非引用）→ 不继承。

    不转换（原样保留 + 一条 warning，**不猜**）：
      · `N` 不在 `num2key`（越界/清单里没有这个编号）；
      · 缺引用引导语证据且无同句锚点（如「规则[3]条」）；
      · 有锚点但与上一处引用之间不是枚举/位置续引结构（夹叙述文字等）。
    静默跳过（不是引用，也不出 warning）：
      · `[N]` 在括号组里（交给 `link_numbered_citations`，避免二次转换）；
      · `[N]` 在 `$…$` 公式里（区间/下标等数学记号）。
    """
    if not text or not num2key:
        return text, 0, []
    depths = _fn_in_paren_depths(text)
    in_math = _fn_in_math(text)
    hi = max(num2key)
    parts: list[str] = []
    pos = 0
    n_done = 0
    issues: list[str] = []
    anchor = -1     # B-137：同句内上一处已转换引用的 `]` 之后位置；-1 = 本句无锚点
    for m in _FN_BARE_NUM_RE.finditer(text):
        s, e = m.span()
        if anchor >= 0 and any(ch in _FN_SENT_END for ch in text[anchor:s]):
            anchor = -1                  # 跨句：继承失效（跨句需新的引导语）
        if depths[s] > 0:
            continue                     # `（[6]，第434页）`：括号组归正文编号通道
        if in_math[s]:
            continue                     # 公式里的 `[3]` 是数学记号，不是文献编号
        num = int(m.group(1))
        pre = text[max(0, s - 32):s]
        snippet = text[max(0, s - 12):min(len(text), e + 12)].replace("\n", " ")
        key = num2key.get(num)
        if not key:
            issues.append(
                f"脚注内编号 [{num}] 不在文末清单编号范围（1..{hi}）内，已原样保留"
                f"（不猜）：「…{snippet}…」")
            continue
        lead = _cue_evidence(pre)     # B-217：与正文通道同一份引导语判据
        inherit = (
            anchor >= 0 and not lead
            and _fn_inherit_gap_ok(text[anchor:s])
            and not _FN_NONCITE_SUFFIX_RE.match(text[e:])
        )
        if not (lead or inherit):
            if not lead and anchor < 0:
                issues.append(
                    f"脚注内编号 [{num}] 缺引用引导证据（参见/见/引自…），疑似非引用，"
                    f"已原样保留（不猜）：「…{snippet}…」")
            else:
                issues.append(
                    f"脚注内编号 [{num}] 与上一处引用之间不是枚举/位置续引结构，"
                    f"疑似非引用，已原样保留（不猜）：「…{snippet}…」")
            continue
        parts.append(text[pos:s])
        parts.append(f"\\parencite{{{key}}}")
        pos = e
        n_done += 1
        anchor = e                       # 同句锚点（继承可链式：枚举第三、第四处）
    if not n_done:
        return text, 0, issues
    parts.append(text[pos:])
    return "".join(parts), n_done, issues


# 正文里的纯文本编号引用：`（[21]，第33页）` / `([21], p. 33)` / `（见[3]）`
# ⚠️ 一个方括号里可以有**多个编号**：实测 `（[45，46，47]）`、`([1, 2])`。
_NUM_GROUP = re.compile(r"\[\s*(\d{1,3}(?:\s*[，,、]\s*\d{1,3})*)\s*\]")


def _cite_nums(text: str) -> list[int]:
    """取出文本里所有 `[N]` / `[N，M]` 的编号。"""
    out: list[int] = []
    for grp in _NUM_GROUP.findall(text):
        out.extend(int(x) for x in re.split(r"[，,、]", grp) if x.strip())
    return out


# 整组：括号里含至少一个 `[N]`（不跨括号，避免吞掉整段）
_GRP_CN = re.compile(r"[（(](?P<inner>[^（()）]{0,100}?\[[^（()）]{0,100}?)[）)]")
_GRP_EN = re.compile(r"\((?P<inner>[^()]{0,100}?\[[^()]{0,100}?)\)")


def link_numbered_citations(
    body: str, num2key: dict[int, str], lang: str = "zh"
) -> tuple[str, int, list[str]]:
    r"""把正文里的 `（[N]，第M页）` 这类**纯文本编号引用**换成 `\parencite`。

    实测（全库 91 份 Word 源稿）：**24 份**用这种写法，形态是
    `（[N]）` 261 处、`（[N]，第M页）` 608 处、多引用 `（[45，46，47]）`。
    人工成品的写法（139 篇中文稿实测）：`（\parencite{键}，第21页）` **1919 处**、
    `（\parencite{键}）` 1357 处，页码区间用 `\cndash`（**1245 处**，手册规范）；
    `\parencite[页码]{键}` 那种可选参数形式中文稿里只有 12 处（例外）。

    ⚠️ **英文稿完全不同**（48 篇实测）：`\parencite[p.~332]{键}` **207 处**，
    而中文式「（…，第N页）」**0 处**。所以按 lang 分两套：
      · 中文：`（\parencite{键}，第21页）`——定位写在括号里、命令外面；
      · 英文：`\parencite[p.~332]{键}`——定位写进命令的可选参数，`~` 是不换行空格。

    多条目按手册第 33 条第 4 款：**只用一个引用命令**（`\parencite{a,b}`）；
    但**各自带页码时不能合并**，人工成品的写法是
    `（\parencite{a26}；\parencite{a27}，第245--253页）`。
    编号前的引导语（`（见[3]）`）保留在命令前面。

    B-217①：括号组**之外**的「引导语 + 裸编号」也走本函数（`参见文献[4]`、
    `参看[5，6]`、`详见[7]第8–12 页`、`引自[8]`）——只换编号本身为
    `\parencite{键}`，引导语/页码定位原样保留；判据与反例见
    `slcn/wordcite.py: link_body_cue_citations`（编号越界 → 原样 + warning）。

    返回 (新正文, 转换处数, 问题清单)。编号超出清单范围的原样保留并记一条问题。
    """
    if not num2key or not body:
        return body, 0, []

    issues: list[str] = []
    n_done = 0
    is_en = lang == "en"

    def resolve(nums: list[int]) -> list[str] | None:
        keys: list[str] = []
        for n in nums:
            k = num2key.get(n)
            if not k:
                return None
            if k not in keys:
                keys.append(k)
        return keys

    def repl(m: re.Match) -> str:
        nonlocal n_done
        inner = m.group("inner")
        # 每段：([编号…], 编号前的引导语, 编号后的定位)
        segs: list[tuple[list[int], str, str]] = []
        for raw in re.split(r"[；;]", inner):
            nums = _cite_nums(raw)
            if not nums:
                segs.append(([], "", raw.strip()))
                continue
            first = _NUM_GROUP.search(raw)
            pre = raw[: first.start()].strip().strip("，,、:：").strip()
            post = _NUM_GROUP.sub("", raw[first.end():]).strip().strip("，,、:：").strip()
            segs.append((nums, pre, post))
        nums_all = [n for nums, _p, _q in segs for n in nums]
        if not nums_all:
            return m.group(0)
        if resolve(nums_all) is None:
            issues.append(f"正文引用的编号 {nums_all} 超出文末清单范围，已原样保留")
            return m.group(0)
        has_loc = any(q for _n, _p, q in segs) or (is_en and any(p for _n, p, _q in segs))
        if not has_loc:
            keys = resolve(nums_all) or []
            n_done += 1
            # 手册第 33 条第 4 款：多个参考文献只用一个引用命令
            cmd = "\\parencite{" + ",".join(keys) + "}"
            pre = next((p for _n, p, _q in segs if p), "")
            if is_en:
                return f"({cmd})" if not pre else f"{pre} ({cmd})"
            return f"（{pre}{cmd}）" if pre else f"（{cmd}）"
        parts: list[str] = []
        tail_pre = ""
        for nums, pre, post in segs:
            keys = resolve(nums) or []
            if not keys:                      # 纯定位段（如「前言」）→ 并到上一段
                if parts and post:
                    parts[-1] += f"，{post}"
                continue
            cmd = "\\parencite{" + ",".join(keys) + "}"
            if is_en:
                parts.append(_en_locator_cmd(cmd, post) if post else cmd)
                if pre:
                    tail_pre = pre
            else:
                if pre:
                    tail_pre = pre
                norm = _normalize_locator(post) if post else ""
                parts.append(f"{cmd}，{norm}" if norm else cmd)
        if not parts:
            return m.group(0)
        n_done += 1
        if is_en:
            if len(parts) == 1 and parts[0].startswith("\\parencite["):
                return (f"{tail_pre} " if tail_pre else "") + parts[0]
            return (f"{tail_pre} " if tail_pre else "") + "(" + "；".join(parts) + ")"
        return f"（{tail_pre}" + "；".join(parts) + "）"

    grp = _GRP_EN if is_en else _GRP_CN
    new_body = grp.sub(repl, body)
    # B-217①：括号组之外的「引导语 + 裸编号」（参见文献[4] / 参看[5，6] /
    # 详见[7]第8–12 页 / 引自[8]）→ `\parencite`（判据在 slcn/wordcite.py，
    # 与脚注通道共用同一份引导语判据）。括号组已由上面的 pass 处理：转换过的
    # 已无 `[N]`；未转换的仍在括号内 → 下面按括号深度跳过（不重复转/warning）。
    # 脚注参数里的裸编号归 `link_footnote_bare_citations`，本通道不越界。
    new_body, n_cue, cue_issues = _link_body_cue_citations(
        new_body, num2key, lang, _en_locator_cmd)
    issues.extend(cue_issues)
    return new_body, n_done + n_cue, issues


def _en_locator_cmd(cmd: str, loc: str) -> str:
    r"""英文引用的定位：`p. 33` / `pp. 33-40` / `Sec. 7.1` → 命令的可选参数。

    人工成品实测（48 篇英文稿）：`\parencite[p.~332]{键}` 207 处，`~` 是不换行空格；
    区间形式全库没有先例，这里按同一路子写 `pp.~33--40`（双短横）。
    """
    t = loc.strip().strip("，,;；:：").strip()
    keys = cmd[len("\\parencite"):]
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*~?\s*(\d{1,5})\s*[-–—~]{1,2}\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[pp.~{m.group(1)}--{m.group(2)}]{keys}"
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*~?\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[p.~{m.group(1)}]{keys}"
    m = re.fullmatch(r"(\d{1,5})\s*[-–—~]{1,2}\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[pp.~{m.group(1)}--{m.group(2)}]{keys}"
    if re.fullmatch(r"\d{1,5}", t):
        return f"\\parencite[p.~{t}]{keys}"
    return f"\\parencite[{t}]{keys}" if t else cmd


# ── 年份唯一兜底前的「姓名证据」（第 73 条） ────────────────────────────────
# 年份唯一**只是候选范围，不是匹配依据**。早先这里不看姓名就直接
# `hit = by_year[year][0]`，实测全库 23 次触发里 5 次是硬配
# （`（Williamson 2017）`→Hjortland2017、`（Kripke 2007: 301）`→Recanati2007、
# `（何朝安，2021）`→AGregory2021、`（IPCAS，1955年正式成立）`→Shen1955），
# 印进 PDF 就是引错文献，而且真正的 Williamson2007 从未被引用。
# 另 18 次姓名其实**对得上**，只是 `RefEntry.surname` 把西文名的首字母算进了姓
# （`S. Blackburn` / `H.Kelsen` / `J.Woods`），或中文名写在方括号里
# （`Xing, Fuyi [邢福义]` / `Ma, Mingyan [马明艳]`），精确匹配才落空——这些得留住。
# 所以兜底只在**有硬证据**时才配，并把证据写进 logs 留档。

_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+")
_NAME_TOKEN_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _cite_name_tokens(s: str) -> list[str]:
    r"""引用名/作者串 → 比对用词元：去掉 LaTeX 命令与标点，全部小写。

    `\textit{Peter of Spain` → ['peter', 'of', 'spain']；`邢福义` → ['邢福义']。
    """
    s = _LATEX_CMD_RE.sub(" ", s)
    return [t.lower() for t in _NAME_TOKEN_RE.split(s) if t]


def _author_surname_token(entry: RefEntry) -> str:
    """条目作者里最可能当「姓」的那个词。

    西文取**第一个逗号段**的最后一个词（位置取末，不排序）：
    `S. Blackburn`→blackburn、`H.Kelsen`→kelsen、`D. N. Walton`→walton、
    `Recanati, F`→recanati、`Hjortland, O`→hjortland；中文姓由 surname_cn 给。
    """
    if entry.surname_cn:
        return entry.surname_cn
    head = re.split(r"[,，]", entry.author)[0]
    toks = _cite_name_tokens(head)
    return toks[-1] if toks else ""


def _year_fallback_evidence(names: str, cands: list[str], entry: RefEntry) -> str:
    r"""年份唯一兜底前的姓名证据：引用名与条目作者必须真能对上。

    返回证据描述（进日志留档）或空串（＝姓名完全对不上，**不许**替换）。
    只认三类硬证据：
      ① 条目作者的姓出现在引用名词元里（西文按**词边界**，避免 `Li` 命中 `Little`）；
      ② 条目作者整串出现在引用名里（机构作者 `Qinghua xuexiao (清华学校)`）；
      ③ 引用名出现在条目作者串里（`Xing, Fuyi [邢福义]` 的方括号译名、
         `S. Blackburn` 这类「首字母+姓」写法）；中文按子串、西文按词边界。
    """
    name_toks = set(_cite_name_tokens(names))
    name_norm = " ".join(_cite_name_tokens(names))
    au_norm = " ".join(_cite_name_tokens(entry.author))
    sur = _author_surname_token(entry)
    if sur and sur.lower() in name_toks:
        return f"引用名含条目姓「{sur}」"
    if au_norm and re.search(
        r"(?<![A-Za-z])" + re.escape(au_norm) + r"(?![A-Za-z])", name_norm
    ):
        return f"引用名含条目作者「{entry.author}」"
    for c in cands:
        c = c.strip()
        if not c:
            continue
        if re.search(r"[\u4e00-\u9fff]", c):
            if c in entry.author:
                return f"条目作者含引用名「{c}」"
        elif re.search(
            r"(?<![A-Za-z])" + re.escape(c.lower()) + r"(?![A-Za-z])", entry.author.lower()
        ):
            return f"条目作者含引用名「{c}」"
    return ""


def link_citations(body: str, refs: list[RefEntry]) -> tuple[str, list[str], list[str]]:
    """把正文里的人年制引用换成 \\parencite{key}。

    返回 (新正文, 未匹配上的引用, 日志)。
    匹配策略：先用「姓+年」精确匹配；再试译名表；最后**只在该年唯一且有姓名证据**
    时按年兜底（证据写进日志）；仍然落空就保留原文并报告，**绝不按年份硬配**。
    """
    by_sur_year: dict[tuple[str, str], RefEntry] = {}
    by_sur_cn_year: dict[tuple[str, str], RefEntry] = {}
    by_year: dict[str, list[RefEntry]] = {}
    for e in refs:
        if e.surname:
            by_sur_year.setdefault((e.surname.lower(), e.year), e)
        if e.surname_cn:
            by_sur_cn_year.setdefault((e.surname_cn, e.year), e)
        by_year.setdefault(e.year, []).append(e)

    unmatched: list[str] = []
    logs: list[str] = []
    used: set[str] = set()

    def repl(m: re.Match) -> str:
        names = re.sub(r"\\[A-Za-z]+\*?|[{}]", " ", m.group("names")).strip()
        year = m.group("year")
        tail = m.group("tail").strip()
        # 第 121 条（01 Tuomo_Aho 真稿）：括号里带 LaTeX 标记的（如
        # `(edited in \textit{Peter of Spain 1992})`）不是规范引用点；照替换会把
        # 收尾花括号当页码塞进 `\parencite{…，}）`，产物直接 `Too many }'s`。
        # 这种**保留原文**并进 unmatched，交给编辑处理。
        _probe_t = re.sub(r"\\([&%_#$])", r"\1", tail)
        if re.search(r"[\\{}]", _probe_t):
            # tail 里带 LaTeX 花括号/命令 → 页码不干净，保留原文（否则会出
            # `\parencite{…，}）` 这类不平衡产物；01 Tuomo_Aho 实测）。
            unmatched.append(m.group(0))
            return m.group(0)
        # names 可能是「苏珊·哈克」「李雷」「Haack」「Beall 和 Restall」
        cands = re.split(r"[、,，和与&\s]+", names)
        cands = [c for c in cands if c]
        hit = None
        for c in cands:
            c2 = re.sub(r"^[\u4e00-\u9fff]{0,3}·", "", c)  # 去掉「苏珊·」这类译名
            if (c, year) in by_sur_cn_year:
                hit = by_sur_cn_year[(c, year)]
                break
            if (c2, year) in by_sur_cn_year:
                hit = by_sur_cn_year[(c2, year)]
                break
            if (c.lower(), year) in by_sur_year:
                hit = by_sur_year[(c.lower(), year)]
                break
            if c in TRANSLATED_NAMES:
                pass
        if hit is None:
            for c in cands:
                for cn, en in TRANSLATED_NAMES.items():
                    if c.startswith(cn) or cn.endswith(c):
                        for e in refs:
                            if e.key.startswith(en) and e.year == year:
                                hit = e
                                break
                        if hit:
                            break
                if hit:
                    break
        if hit is None and len(by_year.get(year, [])) == 1:
            # ⚠️ 第 73 条：年份唯一只是候选范围，**不是**匹配依据。这里必须拿到姓名证据
            #    才配（见 _year_fallback_evidence），否则让该引用点进入 unmatched，
            #    而不是静默替换成同年的另一条文献。
            ev = _year_fallback_evidence(names, cands, by_year[year][0])
            if ev:
                hit = by_year[year][0]
                logs.append(
                    f"『{names} {year}』按年份唯一兜底到 {hit.key}"
                    f"（姓+年精确匹配落空；证据：{ev}，请核对）"
                )

        if hit is None:
            unmatched.append(m.group(0))
            return m.group(0)
        used.add(hit.key)
        # 页码：统一成中文惯用式「第N页」「第N\\cndash M页」。
        # 源稿里常见 `p.632`、`P632`、`632-635`，人工成品一律写作「第632页」。
        loc = _normalize_locator(tail)
        if loc:
            return f"（\\parencite{{{hit.key}}}，{loc}）"
        # ⚠️ **没有页码时也必须包外层括号**。_CITE_RE 要求源稿里本来就带
        # `（`/`(`，所以输出永远应该有括号——早先只写 `\\parencite{key}`，
        # 结果正文里出现「……《逻辑的认识论》\\parencite{Martin2025}一书中」，
        # 与人工成品的「（\\parencite{Martin2025}）」不一致。
        return f"（\\parencite{{{hit.key}}}）"

    new_body = _CITE_RE.sub(repl, body)
    logs.append(f"引用替换命中 {len(used)} 个不同条目；未匹配 {len(unmatched)} 处")
    return new_body, unmatched, logs
