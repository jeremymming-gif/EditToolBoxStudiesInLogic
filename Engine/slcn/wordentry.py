"""参考文献条目解析（B-207 从 docxsource 纯搬运）：人年制/编号清单/APA/CN 各形态、make_key、refs_to_bib。"""

from __future__ import annotations

import re
import zlib

from .wordcore import (RefEntry, SURNAME_PINYIN, TRANSLATED_NAMES, TYPE_MAP, _strip_zero_width,
                       escape_bib_value)

# ── 参考文献条目解析 ──────────────────────────────────────────────────────

_REF_RE = re.compile(
    r"^(?P<author>.+?)[\.。]\s*"           # 作者
    r"(?P<year>\d{4}[a-z]?)[\.。]\s*"      # 年份
    r"(?P<title>.*?)"                       # 题名
    r"\[(?P<kind>[A-Z])\]\s*"               # 类型标识 [M]/[J]/[C]
    r"(?://)?\s*(?P<rest>.*)$"              # 出版信息
)


def parse_reference_line(line: str) -> RefEntry | None:
    """解析一条人年制参考文献，如：
    Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall.
    李雷. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.
    """
    s = re.sub(r"\s+", " ", line.strip())
    if len(s) < 8:
        return None
    m = _REF_RE.match(s)
    if not m:
        # 没有 [M]/[J] 标识的，退化为「作者. 年份. 其余」
        m2 = re.match(r"^(?P<author>.+?)[\.。]\s*(?P<year>\d{4}[a-z]?)[\.。]\s*(?P<rest>.*)$", s)
        if not m2:
            # 2026-10-17 校准：英文条目走专用分支，中文条目走中文分支，互不抢
            return (_parse_reference_line_en(s)
                    or _parse_reference_line_en_fullname(s)   # 英文全名 + 年份在后（06 型）
                    or _parse_reference_line_cn2(s)     # 中文、年份在第 2 段
                    or _parse_reference_line_cn(s)      # 中文、年份在其余部分
                    # 第四十五批 A2：个人年制鲁棒性（5 期 BIB_COUNT 缺口实测）
                    or _parse_reference_line_apa(s)       # APA：作者 (年份). 题名…
                    or _parse_reference_line_cn_colon(s)  # 中文冒号式：作者：书名，年，出版
                    or _parse_reference_line_cn_apa(s)   # 中文 APA：作者. (年). 题名. 刊名
                    or _parse_reference_line_year_end(s)  # 英文、年份在行尾（旧版 APA/德式）
                    or _parse_reference_line_loose(s))    # 引号题名 + URL/无卷期（SEP/philarchive）
        e = RefEntry(raw=s, author=m2.group("author").strip(), year=m2.group("year"))
        rest = m2.group("rest")
        # 从出版信息里猜类型：含「出版社/ Press/ University」→ 书，含卷期 → 期刊
        e.kind = "J" if re.search(r"\[\s*J\s*\]|\d+\s*\(\d+\)", rest) else "M"
        e.title = rest.split("[")[0].strip()
        e.rest = rest
        e.key = make_key(e)
        return e
    e = RefEntry(
        raw=s,
        author=m.group("author").strip(),
        year=m.group("year"),
        title=re.sub(r"\[[MJC]\].*$", "", m.group("title")).strip(),
        kind=m.group("kind"),
        rest=m.group("rest").strip(),
    )
    e.key = make_key(e)
    return e


#: 中文逗号风格（本刊作者来稿里极常见）：
#:   `陈清泚，“论证研究的认知社会学路径”，《自然辩证法研究》，2020年第2期，第103–108页。`
#:   `郑建国，逻辑悖论研究引论，北京：人民出版社，2014年。`
#:   `王雪君、郭静，“对‘意外考试’悖论的一种非确定论解释”，世界哲学，2022年第1期，第152-159页。`
#: 特征：作者到第一个**中文逗号**，题名到第二个，年份在后面的部分。英文/GB 风格走上面的 `_REF_RE`。
_REF_CN_RE = re.compile(
    r"^(?P<author>[^，。；;]{2,60})[，,]\s*"
    r"(?P<title>.+?)[，,]\s*"
    r"(?P<rest>.*?(?:19|20)\d{2}.*)$"
)


#: 英文条目在中文列表里的常见风格（本刊作者来稿实测）：
#:   `A. Aldini, S.A. Alexander, P. Graziani, 2022, “Knowledge-of-own-factivity, …”, Journal, 1(2): 3-4.`
#:   `T. Williamson, 2000, Knowledge and Its Limits, New York: Oxford University Press.`
#: 特征：**作者列表**（逗号分隔），然后 `, YYYY,`（年份是第 2 段），其后是题名与出版信息。
_REF_EN_RE = re.compile(
    r"^(?P<authors>.+?)\s*,\s*(?P<year>(?:19|20)\d{2}[a-z]?)\s*,\s*(?P<rest>.+)$"
)
_QUOTED = re.compile(r"^[“\"「『'](?P<title>.+?)[”\"」』'']")


def _parse_reference_line_en(s: str) -> RefEntry | None:
    """英文逗号风格的单条文献（只在**不含中文**的行上调用）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    # ⚠️ 编号清单（`[12] …` / `12. …`）有专门通道 `_numbered_refs`，英文分支同样必须让路。
    #    2026-10-17 校准第一版没让路 → `test_cn_journal_numbered` 4 条端到端断言转红。
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_EN_RE.match(s)
    if not m:
        return None
    authors = m.group("authors").strip().rstrip(",").strip()
    year = m.group("year")
    rest = m.group("rest").strip()
    qm = _QUOTED.match(rest)
    if qm:                                   # 有引号 → 题名在引号里，出版信息在引号之后
        # B-220 N1：西式引号把**题名尾逗号写在引号内**（`“Freedom of Will…,”
        # The Journal…`）——尾逗号是分隔符、不是题名的一部分。旧实现把它留在
        # title 里，印出来是「…Person,」（实测 2026年第5期(90) 90-06 4 条）。
        title = qm.group("title").strip().rstrip(" ,，、;；:：")
        after = rest[qm.end():].lstrip(",，. ")
    else:                                    # 无引号 → 题名到下一个逗号（书常用）
        parts = [x.strip() for x in re.split(r"[,，]", rest, maxsplit=1)]
        title = parts[0]
        after = parts[1] if len(parts) > 1 else ""
        # B-220 N2：无引号英文题名常含逗号（`Language, Truth and Logic, 2nd ed.,
        # London: …`）——旧实现只取第一段，题名被截断（实测 90 期 Ayer1946 印成
        # 「Language」）。判据统一在 `footref.title_comma_continuation()`（编号通道共用）。
        if after:
            from . import footref as _footref

            segs = [title] + [x.strip() for x in re.split(r"[,，]", after) if x.strip()]
            join_n = _footref.title_comma_continuation(segs)
            if join_n:
                title = ", ".join(segs[: join_n + 1])
                after = ", ".join(segs[join_n + 1:])
    # ⚠️ P0-2（期 87 sub5/sub2 实测）：`rest` 必须是**题名之后**的出版信息；旧实现
    #    存的是**含引号题名的整段 rest**，而 `refs_to_bib()` 的期刊正则要求 rest 以刊名
    #    开头 → journal/volume/number/pages 永不命中（22 条里丢 18 条 pages）。
    # T13（2026-10-18）：类型不再用「卷期正则命中」猜，统一交给 `_finalize_en_entry()`
    # （`footref.classify_entry_type()` 同源判据 + author 统一倒装）。
    e = RefEntry(raw=s, author=authors, year=year, title=title.strip().strip("《》"),
                 kind="M", rest=after)
    e.key = make_key(e)          # 键在倒装**之前**生成：key 方案不变（产物差异最小）
    _finalize_en_entry(e)
    return e


def _looks_short_bibliographic_note(text: str) -> bool:
    """短英文脚注里「像文献条目」的（有 p./pp. 页码或出版社词）仍按文献条目处理。

    第 121 条回归：中文稿里常夹英文文献脚注（`H. Reichenbach, The Theory of
    Probability, p. 445.`），它们 parse_entry 只能得到 note，但**是文献**、必须保留
    原有「脚注条目 → bib」行为，不能按散文注释降级（否则正文引用全丢）。
    """
    t = text.strip()
    # 强信号（多条目文献列表常常很长，长度上限只对下面的弱信号生效）：
    # GB/T 7714 文献类型标识、网络地址。
    if re.search(r"\[[MJC]\]", t) or re.search(r"https?://|www\.", t):
        return True
    if len(t) > 400:
        return False
    if re.search(r"\bpp?\.\s*\d", t):
        return True
    if re.search(r"\b(?:Press|Publishing|Verlag|Routledge|Blackwell|Wiley|Springer|University)\b", t):
        return True
    # 带引号的题名（`“Deconstructing the Laws of Logic,” Philosophy 83 (323)…`）
    if len(re.findall(r"[“”]", t)) >= 2 or t.count('"') >= 2:
        return True
    # 期刊卷期写法（`83 (323)` / `Vol. 68 (1)`）
    if re.search(r"\b(?:Vol|vol|No|no)\.?\s*\d+|\d+\s*\(\d+\)", t):
        return True
    # P0-6（期 87 sub9 实测）：`M. Bolduc & David A. Frank (eds.), The Intellectual and
    # Cultural Origins of … 1933-1958, Brill Academic Pub, 2023.` —— 出版社写作
    # `Brill Academic Pub`，既没有页码/引号/卷期，旧信号全落空 → 被当成散文注释、
    # 整条不进 bib。补三个「像文献」信号（都要求**同时**出现年份，避免误伤散文）。
    if re.search(r"\(eds?\.\)|\beds?\.|\bEdited by\b", t, re.I):
        return True
    if (re.search(r"\b(?:19|20)\d{2}\b", t)
            and re.search(r"\b(?:Pub|Press|Publishing|Verlag|Books?|University|"
                          r"Academic|Brill|Routledge|Blackwell|Springer|Wiley)\b", t)):
        return True
    return False


def _entry_is_note_only(entry: dict | None) -> bool:
    """parse_entry() 的结果是否只有 note（= 没能结构化出作者/题名，不是文献条目）。"""
    if not entry:
        return True
    return not any(str(entry.get(k) or "").strip() for k in ("author", "title"))


# ── B-199（2026-10-23）：脚注引用提升的接线辅助 ─────────────────────────────
#
# 背景（B-198 批 A 分诊）：编号清单型 Word 稿里，bib 条目字段可能被解析成碎片
# （题名吸进 `author` 等），靠字段匹配不可靠；但 B-022 物化后的
# `doc.ref_raw_lines`（完整原文行）+ `doc.ref_key_by_num` 是可靠映射。题名逐字
# 落在**唯一**原文行内时才返回键——不编造、不改条目。

def _src_line_key(doc, title: str, year: str = "") -> str:
    """用文末编号清单的原文行给「题名（+年份）」找既有 bib 键；唯一命中才返回。"""
    from .footnotes import _norm_fp

    lines = list(getattr(doc, "ref_raw_lines", None) or [])
    key_by_num = dict(getattr(doc, "ref_key_by_num", None) or {})
    tnorm = _norm_fp(str(title or ""))
    if len(tnorm) < 4:
        return ""
    # ① 编号清单：原文行 → 编号 → 键（`ref_key_by_num` 只有编号清单才有）
    # 编号优先取行内 `[N]` 前缀（B-199 验收缺陷：清单前多一条说明行时，
    # 用 enumerate 会把行号当编号、返回邻条键）；无 `[N]` 前缀的行只有在
    # 「行数 == 键数且键连续 1..N」时才允许按位置兜底，否则放弃（不猜）。
    if lines and key_by_num:
        nums: list[int | None] = []
        for line in lines:
            m = re.match(r"^\s*\[\s*(\d+)\s*\]", str(line))
            nums.append(int(m.group(1)) if m else None)
        if all(n is None for n in nums):
            keys_int = sorted(int(k) for k in key_by_num)
            if len(lines) == len(key_by_num) and keys_int == list(range(1, len(lines) + 1)):
                nums = list(range(1, len(lines) + 1))
            else:
                nums = []
        hits: list[int] = []
        for line, num in zip(lines, nums):
            if num is None:
                continue
            if tnorm not in _norm_fp(line):
                continue
            if year and str(year) not in str(line):
                continue
            hits.append(num)
        if hits:
            return str(key_by_num.get(hits[0]) or "") if len(hits) == 1 else ""
    # 人年制文末清单（90-06 型）：字段可能被「译著拆条/字段归位」前的碎片占住
    # （题名位变成「方敬译」），但 `Ref.raw` 原文行是完整的 → 用原文行兜底。
    ref_hits: list[str] = []
    for ref in (getattr(doc, "refs", None) or []):
        raw = str(getattr(ref, "raw", "") or "")
        if not raw:
            continue
        if tnorm not in _norm_fp(raw):
            continue
        if year and str(year) not in raw:
            continue
        key = str(getattr(ref, "key", "") or "")
        if key:
            ref_hits.append(key)
    unique = sorted(set(ref_hits))
    return unique[0] if len(unique) == 1 else ""


def _citation_title_year(fn) -> tuple[str, str]:
    """从 Footnote 抽 (题名, 4 位年份)；字段全部来自原文（不编造）。"""
    from .footnotes import _extract_citation_fields

    try:
        fields = _extract_citation_fields(fn)
    except Exception:  # noqa: BLE001
        fields = {}
    year = re.sub(r"\D", "", str(fields.get("year") or ""))[:4]
    return str(fields.get("title") or ""), year


def _src_line_keys_for_works(doc, ref: str) -> list[str]:
    """多作品引用：逐作品用原文行找键；**全部**唯一命中才返回（否则空列表）。"""
    from .footnotes import _work_titles

    keys: list[str] = []
    for work in _work_titles(str(ref or "")):
        key = _src_line_key(doc, work["title"], work["year"])
        if not key:
            return []
        keys.append(key)
    return keys if len(keys) >= 2 else []


#: 英文「全名 + 年份在后」的参考文献区格式（期 88 / 06 Hao_Chen 真稿实测）：
#:   `Jacobs, Scott, Jackson, Sally, 1989. Building a model of conversational argument. In …`
#:   `Van Eemeren, Frans et al. 2014. Handbook of Argumentation Theory. …`
#: 特征：作者是「姓, 名」成对用逗号连写（可带 et al./&），**年份在作者后、后跟句点**。
#: 注意与 `_REF_EN_RE`（`作者, 年份, 其余`）区分：这条的年、作者之间用**句点**收尾。
_REF_EN_FULLNAME_RE = re.compile(
    r"^(?P<pre>.+?)\s*(?P<year>(?:19|20)\d{2})\.\s+(?P<rest>.+)$"
)
_EN_AUTHOR_SPLIT = re.compile(r"\s*(?:,|&|\band\b)\s*")


def _initials(given: str) -> str:
    """Given name → 缩写（`Jean-Claude` → `J.-C.`，`Merce` → `M.`）。"""
    out = []
    for part in given.split():
        part = part.strip(".")
        if not part:
            continue
        if "-" in part:
            out.append("-".join(p[0].upper() + "." for p in part.split("-") if p))
        else:
            out.append(part[0].upper() + ".")
    return " ".join(out)


def _parse_reference_line_en_fullname(s: str) -> RefEntry | None:
    """解析 `Surname, Given, Surname2, Given2, YYYY. Title…` 英文条目（06 Hao_Chen 型）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_EN_FULLNAME_RE.match(s)
    if not m:
        return None
    pre, year, rest = m.group("pre").strip(), m.group("year"), m.group("rest").strip()
    if not (5 <= len(pre) <= 240) or re.search(r"\d{4}", pre):
        return None
    if "," not in pre and "et al" not in pre:
        return None
    pre = re.sub(r"\bet al\.?$", "", pre).strip().rstrip(",.").strip()
    if not pre:
        return None
    tokens = [x for x in _EN_AUTHOR_SPLIT.split(pre) if x and x.lower() != "et al"]
    if len(tokens) < 2:
        return None
    # 「姓, 名」成对；奇数尾项当只有姓
    pairs: list[tuple[str, str]] = []
    for i in range(0, len(tokens), 2):
        surname = tokens[i].strip(" .")
        given = tokens[i + 1].strip(" .") if i + 1 < len(tokens) else ""
        if not surname or not surname[0].isupper():
            return None
        if len(surname.split()) > 3 or len(given.split()) > 4:
            return None
        pairs.append((surname, given))
    if not pairs:
        return None
    authors = " and ".join(
        f"{sn}, {_initials(gv)}" if gv else sn for sn, gv in pairs
    )
    # 题名 = 第一个句点+空格+大写 之前（没有就取全部 rest）
    tm = re.split(r"\.\s+(?=[A-Z])", rest, maxsplit=1)
    title = tm[0].rstrip(".").strip()
    after = tm[1] if len(tm) > 1 else ""
    # T13：类型走 `footref.classify_entry_type()`（出版信息特征），author 出口统一倒装
    e = RefEntry(raw=s, author=authors, year=year, title=title, kind="M", rest=after)
    e.key = make_key(e)          # 键在倒装**之前**生成：key 方案不变（产物差异最小）
    _finalize_en_entry(e)
    return e


#: 中文逗号风格但**年份在第 2 段**（实例测试里也出现）：
#:   `李四, 2021, 语用学导论, 北京: 商务印书馆.`
#: 与 `_parse_reference_line_cn`（年份在"其余部分"）互补；两者都只处理含中文的行。
_REF_CN2_RE = re.compile(
    r"^(?P<author>[^，。；;]{2,60})[，,]\s*"
    r"(?P<year>(?:19|20)\d{2}[a-z]?)\s*[，,]\s*"
    r"(?P<rest>.+)$"
)


def _parse_reference_line_cn2(s: str) -> RefEntry | None:
    """中文逗号风格、**年份在第 2 段**的条目。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_CN2_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    parts = re.split(r"[，,]", rest, maxsplit=1)
    title = parts[0].strip().strip("《》")
    kind = "M"
    if "学位论文" in s or "博士论文" in s or "硕士论文" in s:
        kind = "D"
    elif "《" in s or re.search(r"第?\d+[期卷]", s):
        kind = "J"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


def _parse_reference_line_cn(s: str) -> RefEntry | None:
    """中文逗号风格的单条文献（2026-10-17 实例测试：用户 10 篇里 2 篇整份列表都这种格式）。

    ⚠️ 只应在**参考文献区**的行上调用（正文段落也含逗号+年份，直接全篇跑会误判）。
    """
    # ⚠️ 编号清单（`[12] 作者，题名，…` / `12. 作者…`）有**专门的通道**（`_numbered_refs`），
    #    这里必须让路：2026-10-17 第一版没让路，把 `test_cn_journal_numbered` 的 4 条端到端
    #    断言打红了（编号条目被这里当成普通中文条目，通道就收不到了）。
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    # ⚠️ 只处理**含中文**的行：英文条目走 `_parse_reference_line_en`。
    #    2026-10-17 校准前，英文条目（`A. Aldini, S.A. Alexander, P. Graziani, 2022, “题名”, …`）
    #    被这里按"作者，题名，其余"切分，**合著者名被当成题名**（title=P. Graziani）。
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    m = _REF_CN_RE.match(s)
    if not m:
        return None
    rest = m.group("rest")
    ym = re.search(r"(?:19|20)\d{2}", rest)
    if not ym:
        return None
    title = m.group("title").strip().strip("《》")
    kind = "M"
    if "学位论文" in s or "博士论文" in s or "硕士论文" in s:
        kind = "D"
    elif "《" in s or re.search(r"第?\d+[期卷]", s):
        kind = "J"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=ym.group(0),
                 title=title, kind=kind, rest=rest.strip())
    e.key = make_key(e)
    return e


#: 编号清单行前缀（含**写歪的** `[2 Author…` 少括号）：人年制新兜底一律让路，
#: 由 `_numbered_refs()` 收（案例 116 的 ① 契约：编号清单 4 条必须进 bib_entries）。
_NUMBERED_PREFIX_RE = re.compile(
    r"^\s*(?:\[\s*\d+|（\s*\d+|\(\s*\d+|\d+\s*[.、])"
)


# ── 第四十五批 A2：个人年制解析鲁棒性（5 期 BIB_COUNT 6 篇实测） ────────────
# 失败行样例（bib-refs 报回 /tmp/audit_lead/batch45-bib.md）：
#   86-07/86-08（英文 APA 清单 38/31 行整份 0 条）：
#     `Beall, J. C., & Restall, G. (2000). Logical pluralism. Australasian Journal of
#      Philosophy, 78(4), 475–493.`
#   86-02（中文冒号式 4 条）：`王先谦：荀子集解，1988年，北京：中华书局。`
#   87-5（2 条）：`A. Hájek, “Most counterfactuals are false”, (2019-08-28)[…]. https://…`
#     `P. Rysiew, “Epistemic Contextualism”, in … (eds.), The Stanford Encyclopedia of
#      Philosophy, https://…/win2023/…`
#   88-01（4 条）：年份区间（`1929-1931`）、年份在行尾（德式）、APA 编辑行、残行。
# 纪律：**只按行内证据解析**（作者/年份/题名都要在行里出现）；解析失败保留原文，
# 由调用方 warning + `ref_raw_lines` 交人工/AI。全部为**新增兜底**，排在既有解析器
# 之后——已能解析的行不会走到这里，产物逐字节不变。
_APA_YEAR_RE = re.compile(
    r"^(?P<authors>.+?)\s*[(（]\s*(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[-–—]\s*(?:19|20)\d{2})?[a-z]?\s*[)）]\s*[.。]?\s*(?P<rest>.+)$"
)
_APA_YEAR_RANGE_RE = re.compile(
    r"^(?P<authors>.+?)[.。]\s*(?P<year>(?:19|20)\d{2})"
    r"\s*[-–—]\s*(?:19|20)\d{2}[.。]\s*(?P<rest>.+)$"
)
_EN_SENTENCE_END = re.compile(r"(?<=[.?!])[\s\n]+(?=[A-Z“\"(\[])")


def _clean_apa_authors(pre: str) -> str:
    """APA 作者串 → bib author 字段（`&` → `and`，去尾标点）。"""
    x = pre.strip()
    x = re.sub(r"[,，]\s*&\s*", " and ", x)      # `Beall, J. C., & Restall, G.`
    x = re.sub(r"\s+&\s+", " and ", x)          # `A & B`
    x = re.sub(r",\s*,", ",", x)
    return x.strip(" ,;.")


def _apa_kind(after: str) -> str:
    """按行内出版信息判类型：有卷(期)/页码像期刊，`In …(Eds.)`/出版社像书。"""
    a = after.strip()
    if re.search(r"\(\s*Eds?\.\s*\)|^\s*In\s+", a):
        return "M"
    if re.search(r",\s*\d+\s*(?:\(\d+\))?\s*,\s*\d+", a) or re.search(r"\d+\s*\(\d+\)\s*[,:]", a):
        return "J"
    return "M"


def _parse_reference_line_apa(s: str) -> RefEntry | None:
    r"""APA 风格：`作者列表 (年份). 题名. 出版信息`（英文；86-07/86-08 整份清单）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _APA_YEAR_RE.match(s) or _APA_YEAR_RANGE_RE.match(s)
    if not m:
        return None
    authors = _clean_apa_authors(m.group("authors"))
    if not authors or len(authors) > 240 or not re.search(r"[A-Za-z]", authors):
        return None
    rest = m.group("rest").strip()
    if len(rest) < 3:
        return None
    chunks = _EN_SENTENCE_END.split(rest, maxsplit=1)
    title = chunks[0].rstrip(". ").strip()
    after = chunks[1].strip() if len(chunks) > 1 else ""
    if not title or len(title) > 300:
        return None
    kind = _apa_kind(after)
    e = RefEntry(raw=s, author=authors, year=m.group("year"),
                 title=title, kind=kind, rest=after)
    e.key = make_key(e)
    _finalize_en_entry(e)
    e.kind = kind          # `_finalize_en_entry` 的通用分类会把期刊判成书，这里按行内证据保留
    return e


_CN_COLON_RE = re.compile(r"^(?P<author>[^：:]{2,60}?)\s*[：:]\s*(?P<rest>.+)$")


def _parse_reference_line_cn_colon(s: str) -> RefEntry | None:
    r"""中文冒号式：`作者（编/撰）：《书名》，年份，出版地：出版社。`（86-02 实测）。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _CN_COLON_RE.match(s)
    if not m:
        return None
    author = m.group("author").strip()
    rest = m.group("rest").strip()
    ym = re.search(r"((?:19|20)\d{2})", rest)
    if not ym or len(rest) < 6:
        return None
    qm = re.search(r"《([^》]+)》", rest)
    if qm:
        title = qm.group(1).strip()
    else:
        title = re.split(r"[，,。]", rest, maxsplit=1)[0].strip()
    if not title or len(title) > 200:
        return None
    kind = "M"
    if re.search(r"学位论文|博士论文|硕士论文", rest):
        kind = "D"
    elif re.search(r"学报|期刊|杂志|第?\s*\d+\s*[期卷]", rest):
        kind = "J"
    e = RefEntry(raw=s, author=author, year=ym.group(1), title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


_EN_YEAR_END_RE = re.compile(
    r"^(?P<author>[A-Z][^,]{1,40}(?:,\s*[^,]{1,60})?),\s*"
    r"(?P<rest>.+?(?P<year>(?:19|20)\d{2})[^\d]*)$"
)


def _parse_reference_line_year_end(s: str) -> RefEntry | None:
    r"""英文、**年份在行尾**（旧版 APA / 德式书目，88-01 Osma 实测）。

    `Osma, Wilhelm von, De Consequentiis — Über die Folgerungen, hrsg. F. Schupp,
     Meiner, Hamburg 1991.` → author=Osma, Wilhelm von / year=1991。
    """
    if re.search(r"[\u4e00-\u9fa5]", s) or len(s) < 20:
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    # 引号题名的行交给 loose 解析（作者/题名切分更可靠；本解析器会把题名当成作者一部分）
    if _QUOTED_TITLE_RE.match(s):
        return None
    m = _EN_YEAR_END_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    if len(rest) < 8:
        return None
    parts = [x.strip() for x in re.split(r"[,，]", rest, maxsplit=1)]
    title = parts[0]
    after = parts[1] if len(parts) > 1 else ""
    if not title:
        return None
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind="M", rest=after)
    e.key = make_key(e)
    _finalize_en_entry(e)
    return e


_CN_APA_RE = re.compile(
    r"^(?P<author>[\u4e00-\u9fa5、，,A-Za-z][^.。]{0,40}?)[.。]\s*"
    r"[(（](?P<year>(?:19|20)\d{2})[)）]\s*[.。]\s*(?P<rest>.+)$"
)


def _parse_reference_line_cn_apa(s: str) -> RefEntry | None:
    r"""中文 APA 风格：`作者. (年份). 题名. 刊名, 卷(期), 页码.`（86-07 实测）。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _CN_APA_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    if len(rest) < 4:
        return None
    title = re.split(r"[.。]", rest, maxsplit=1)[0].strip()
    if not title or len(title) > 200:
        return None
    kind = "J" if re.search(r"学报|期刊|杂志|,\s*\d+", rest) else "M"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


_QUOTED_TITLE_RE = re.compile(
    r'^(?P<author>.{2,160}?)[,，]\s*[“"「『](?P<title>.+?)[”"」』]\s*[,，]?\s*(?P<rest>.*)$'
)


def _parse_reference_line_loose(s: str) -> RefEntry | None:
    r"""引号题名 + URL/无常规卷期的行（87-5 SEP / philarchive 实测）。

    `A. Hájek, “Most counterfactuals are false”, (2019-08-28)[2015-05-15]. https://…`
    `P. Rysiew, “Epistemic Contextualism”, in … (eds.), The Stanford Encyclopedia of
     Philosophy, https://…/win2023/…`（年份取行内 `win2023` 的 2023）
    """
    if _NUMBERED_PREFIX_RE.match(s):     # 编号清单行走 `_numbered_refs()`（案例 116 契约）
        return None
    m = _QUOTED_TITLE_RE.match(s)
    if not m:
        return None
    author = m.group("author").strip().rstrip(",，")
    title = m.group("title").strip()
    rest = m.group("rest").strip()
    if not author or not title or len(title) > 300:
        return None
    ym = re.search(r"((?:19|20)\d{2})", rest) or re.search(r"(?:19|20)\d{2}", s)
    if not ym:
        return None
    year = ym.group(1) if ym.groups() else ym.group(0)
    kind = "J" if re.search(r"Journal|Review|Studies|Proceedings|Quarterly", rest) else "M"
    e = RefEntry(raw=s, author=author, year=year, title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    _finalize_en_entry(e)
    e.kind = kind          # 同 APA：类型以行内证据为准
    return e


def _numbered_refs(
    lines: list[str],
) -> tuple[list[tuple[str, dict, str]], dict[int, str], dict[str, str]]:
    """解析 `[N] 作者, 年份, "题名", 刊名, 卷(期): 页` 这种编号清单。

    返回 `(条目列表, {N: bib 键}, {bib 键: 整条 `[N] …` 原文})`。第二个返回值是给
    **正文里的纯文本引用**用的：正文写 `（[21]，第33页）`，编号 21 指的就是这份清单的第 21 条。
    第三个返回值是「条目 → 源稿原文」的映射（第 68 条），由 `parse_docx` 收进
    `doc.ref_raw_by_key`，再交给任务书与 AI 预跑——**只给被留下的条目**（跳过的不出现）。

    与「参考文献附件」用的是同一套解析器（`footref`，README 第 28 条）。
    条目形如 `(key, 字段 dict, 原文)`——与脚注文献那条通道**同形**，
    因此直接并进 `doc.bib_entries`，由 `footnotes.entry_to_bib` 写成 bib。

    实测 09 Jiawei_Zhong：文末 18 条全是这种体例，旧实现只认人年制（`作者. 年. …[J]. …`），
    于是「参考文献 0 条」、bib 里一条都没有。
    """
    from . import footref
    from .bibcheck import author_has_role_mark as _has_role_mark
    from .footnotes import make_key as _fn_make_key
    from .wordrefs import apply_numbered_extras

    out: list[tuple[str, dict, str]] = []
    num2key: dict[int, str] = {}
    raw_by_key: dict[str, str] = {}
    used: set[str] = set()
    # ⚠️ 用 `split_numbered_raw`（与 `parse_numbered_list` 同一套切分）：编号与原文**同源**，
    #    不会出现「这条原文配到另一条条目上」。`split_numbered_raw` 已按同一口径过滤短段。
    def _entry_dict(it) -> dict:
        return {
            "_type": it.etype,
            "author": it.author, "title": it.title,
            "journal": it.journal, "booktitle": it.booktitle,
            "volume": it.volume, "number": it.number, "pages": it.pages,
            "publisher": it.publisher, "address": it.address,
            "year": it.year, "note": it.note,
            # B-264 v2：APA 书/章节的 `(Vol. N)`→volume、`(Rev. ed.)`→edition、
            # In-eds 编辑者→editor；其它体例这两格恒空，`entry_to_bib` 不输出空值。
            "edition": it.edition, "editor": it.editor,
        }

    # P0-5a（期 87 sub7 实测）：编号清单**第一个标记之前**的无编号条目（作者漏编号）
    # 旧实现整段丢弃。这里把标题行去掉后、首标记之前的内容按行补成第 1..n 条；
    # 这类条目没有编号，**不写 num2key**（不能影响正文编号→键映射）。
    text_all = "\n".join(lines)
    first_mark = re.search(r"(?:^|\n)\s*[\[\(（]\s*\d{1,3}\s*[\]\)）]", text_all)
    # ⚠️ 只有「确实存在编号标记」时，首个标记前那一行才是「漏编号的第 1 条」；
    #    整份没有编号的（人年制英文/中文清单）必须交给 `parse_reference_line` 通道，
    #    否则会被当成 N 条无编号条目、覆盖掉正确解析（期 88 Avery_Blake 实测）。
    prefix = text_all[: first_mark.start()] if first_mark else ""
    prefix_lines = [
        line.strip() for line in prefix.splitlines()
        if line.strip() and not re.match(r"^\s*(?:参考文献|References?|参考书目)\s*$", line, re.I)
    ]
    for raw_line in prefix_lines:
        if len(raw_line) <= 12:
            continue
        it0 = footref.parse_numbered_item(raw_line)
        if not it0.title or not (it0.year or it0.author):
            continue
        entry0 = apply_numbered_extras(
            _entry_dict(it0), raw_line, match_journal=_match_en_journal)
        key0 = _fn_make_key(entry0, len(out) + 1, used)
        out.append((key0, entry0, raw_line))
        raw_by_key[key0] = raw_line

    for seq, (raw, seg) in enumerate(footref.split_numbered_raw("\n".join(lines)), 1):
        # 编号取**原文标号本身**（`[2]` → 2），不能用 enumerate 的位置：
        # 清单允许首条无编号（P0-5）时，位置与正文里的编号会差 1。
        _nm = re.match(r"^\s*[\[\(（]\s*(\d{1,3})\s*[\]\)）]", raw)
        num = int(_nm.group(1)) if _nm else seq
        it = footref.parse_numbered_item(seg)
        # ⚠️ 题名短于 4 字通常是「切错了」（把译者/刊名切成了题名）。
        #    但中文著/译体例是**例外**（第 64 条）：`亚里士多德（著），张竹明（译），工具论，…`
        #    的真题名可能只有 3 个字——此时作者段带「（著）/（译）」标记、题名是**下一**段，
        #    按长度丢掉整条会让正文的 `\parencite` 悬空（实测 1 篇：反推数学与无穷 kxj.docx）。
        #    P0-4b（期 87 sub8）：**不再静默丢**——切错的条目保留并写 note，交人工核对。
        if not it.title:
            continue
        short_title = (len(it.title) < 4
                       and not _has_role_mark(it.author, both=True))
        # 只有单侧著/译标记（如「（著）」没有「（译）」）时，短题名几乎肯定是把
        # 译者/刊名切错了——维持旧行为**丢弃**（测试 test_authornorm ⑤c /
        # test_ref_raw_lines 反例）；P0-4 的「保留+note」只针对无角色标记的情形。
        if short_title and _has_role_mark(it.author):
            continue
        entry = apply_numbered_extras(
            _entry_dict(it), seg, match_journal=_match_en_journal)
        if short_title:
            entry["note"] = (f"题名解析可疑（{it.title}），已保留原条目，"
                             "请按源稿原文核对题名")
        key = _fn_make_key(entry, len(out) + 1, used)
        # B-264 v2（用户 m00808 / Lead m00248）：交付为 @misc 的条目，第三元素
        # （`entry_to_bib` 的 `raw`）必须是**去标号的源稿原文**，不是 title——
        # 交付层据此写「author = 逐字原文」的极简 misc。非 misc 保持旧行为
        # （第三元素仍是 title；只在「所有字段都空」时才会用到 raw 写 note）。
        out.append((key, entry, seg if entry.get("_type") == "misc" else it.title))
        if _nm:                       # 只有带标号的条目才进「编号→键」映射
            num2key[num] = key
        raw_by_key[key] = raw
    return out, num2key, raw_by_key


def convert_bibitem_refs(doc) -> int:
    """B-233：Word 文末手写 `\\bibitem` 文本 → 规范 bib 条目（就地写 doc、fail-soft）。

    触发（保守）：无编号清单/脚注条目，且人年制通道零结果，或其条目原文**全部**以
    `\\bibitem` 开头（宽松解析器会把这种行误吃成 `ref…` 垃圾条目）。条目顺序=文献
    序号（`ref_key_by_num={n: 第 n 条键}`）、源键保留；正文 `[n]`→`\\parencite` 由
    build 既有 `link_numbered_citations` 完成（不猜）。返回写入条数。
    """
    try:
        from . import bibitem_convert

        if not bibitem_convert.enabled() or doc.bib_entries:
            return 0
        refs = list(getattr(doc, "refs", None) or [])
        if refs and not all(str(e.raw or "").strip().startswith("\\bibitem") for e in refs):
            return 0
        lines = [s for s in (getattr(doc, "ref_raw_lines", None) or []) if s.strip()]
        text = "\n".join(lines)
        if not bibitem_convert.looks_like_bibitems(text):
            return 0
        result = bibitem_convert.convert(text)
        entries = [e for e in (getattr(result, "entries", None) or []) if e.get("key")]
        if not entries:
            return 0
    except Exception:  # noqa: BLE001 —— fail-soft：转换失败不阻断出稿
        return 0

    def _raw(k: str) -> str:  # 条目 → 源稿原文行（build 的任务书/兜底用）
        pat = r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{" + re.escape(k) + r"\}"
        return next((s for s in lines if re.search(pat, s)), "")

    out = [(str(e["key"]), {k: v for k, v in e.items() if k != "key"}, _raw(str(e["key"])))
           for e in entries]
    if refs:  # 旧通道误吃：丢弃垃圾条目，改走 bibitem（warning 留痕，不静默）
        doc.warnings.append(f"文末 {len(refs)} 行 \\bibitem 被人年制通道误解析"
                            f"（如 {refs[0].key}），已改用 \\bibitem 自动转换，请核对")
        doc.refs, doc.ref_raw_by_key = [], {}
    doc.bib_entries.extend(out)
    doc.ref_key_by_num = {i: k for i, (k, _e, _r) in enumerate(out, 1)}
    doc.ref_raw_by_key.update({k: r for k, _e, r in out if r})
    doc.warnings.extend(str(w) for w in (getattr(result, "warnings", None) or []))
    doc.warnings.append(f"{bibitem_convert.WARNING_AUTO}（Word 文末共 {len(out)} 条）")
    return len(out)


def _make_key_core(a: str, year: str, disambiguate: str = "") -> str | None:
    """按「译名表 → 中文全拼音 → 西文姓」分类生成 bib 键。

    分不出来返回 `None`（由 `make_key` 决定是「剥零宽重判」还是「走兜底键」）——
    把「分类」与「兜底」拆开，才能保证 `make_key` 里那条
    「先按原串判、失败了再剥零宽重判」的顺序写出来（案例 74）。
    """
    for cn, en in TRANSLATED_NAMES.items():
        if a.startswith(cn):
            return f"{en}{year}{disambiguate}"
    m = re.match(r"^([\u4e00-\u9fff]{2,4})", a)
    if m:
        # 中文姓名取**全拼音**：李雷 → LiLei、刘靖贤 → LiuJingxian、哈瑞 → HaRui。
        # 早先只对姓查表、名留汉字，生成了 `Chen波2005` 这种混着中文的键
        # （BibLaTeX 虽能容忍，但难看且易出问题）。实测人工成品用的是全拼音。
        name = m.group(1)
        try:
            from .naming import chinese_to_folder

            folder = chinese_to_folder(name)   # Lei_Li
            given_py, sur_py = folder.split("_", 1)
            return f"{sur_py}{given_py}{year}{disambiguate}"
        except Exception:  # noqa: BLE001  pypinyin 缺失时退回旧逻辑
            sur = SURNAME_PINYIN.get(name[0], name[0])
            return f"{sur}{name[1:]}{year}{disambiguate}"
    if re.match(r"^[A-Za-z]", a):
        # 多作者：Beall, J. C. and Restall, G. → BeallRestall
        parts = re.split(r"\s+and\s+|&", a)
        names = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            w = re.match(r"([A-Za-z][A-Za-z'\-]+)", p)
            if w:
                names.append(w.group(1))
        stem = "".join(names[:2]) or re.sub(r"[^A-Za-z]", "", a)[:12]
        return f"{stem}{year}{disambiguate}"
    return None


def make_key(e: RefEntry, disambiguate: str = "") -> str:
    """生成 bib 键。优先中文译名表，其次中文姓取拼音，再次西文姓。

    案例 74（2024_01_Min_Jiao）：**分类之前先剥零宽/BOM**。
    这批西文文献的作者串以不可见字符开头（`'\\ufeffS. Blackburn'`、`'\\ufeffR. Audi'`），
    于是 `re.match(r"^[A-Za-z]", a)` 失配，本可以正常生成 `SBlackburn1998` 的条目
    只好走兜底键。实测该篇 16 条、全库 5 篇共 39 条落到兜底。

    ⚠️ 顺序是「**先按原串判，判不出来再剥零宽重判**」，不是「先剥再判」：
    只有这样才能保证**已经能分类的条目键逐字不变**（`LiLei2005`、`Beall2006`、
    `Xing1984` …）。逐字不变这条不变量是本缺陷的验收核心——先剥再判会顺手改掉
    「名字中间夹零宽」的条目：`'Jo\\ufeffhn Smith'` 今天走西文分支取到 `Jo2005`
    （`re.match` 在零宽处截断），先剥再判会变成 `John2005`。
    """
    a = e.author.strip()
    key = _make_key_core(a, e.year, disambiguate)
    if key is not None:
        return key
    a_clean = _strip_zero_width(a).strip()
    if a_clean and a_clean != a:
        key = _make_key_core(a_clean, e.year, disambiguate)
        if key is not None:
            return key
    # 兜底键必须**确定**（案例 74 的第二半）：
    # ⚠️ 不能用内置 `hash()`。CPython 对 str 的 `hash()` 受 **PYTHONHASHSEED 随机化**
    #    影响（每个进程都不同），因此同一个 `docxsource.py` 对同一份稿子在两个进程里
    #    会给出**不同的 bib 键**——实测同一篇 2024_01_Min_Jiao：
    #      进程 1：ref56162006, ref30821998, ref10261987 …
    #      进程 2：ref36242006, ref46911998, ref64241987 …
    #    后果是正文里的 `\parencite{ref…}` 与 `reference.bib` 一起抖，产物不可复现，
    #    「`.ai前` 与合并后逐字节比对」与 corpus 回归在这类稿子上永远失败。
    #    `zlib.crc32` 是纯函数：同一串在任何进程、任何 PYTHONHASHSEED 下都相同。
    #    取 8 位十六进制（定宽，便于肉眼比对），后面仍接年份以保持旧键的形状。
    return f"ref{zlib.crc32(a.encode('utf-8')):08x}{e.year}"


def _dedupe_keys(refs: list[RefEntry]) -> None:
    """同年同作者的条目加 a/b/c 后缀（本刊惯例，如 Chen2014a）。

    案例 74：分组名先剥零宽。`make_key` 修好之后，`'\\ufeffS. Blackburn'` 与
    `'S. Blackburn'` 同年会生成**同一个**键 `SBlackburn1998`；而 `RefEntry.surname`
    取的是原串，两者分组名不同、加不上 a/b 后缀，于是 bib 里出现两个同名条目。
    分组名归一（剥零宽）就把这个洞补上了。
    实测全库 101 篇里「归一后会改变分组的条目」= **0** 条，所以既有键一条都没动。
    """
    groups: dict[tuple[str, str], list[RefEntry]] = {}
    for e in refs:
        name = _strip_zero_width(e.surname_cn or e.surname)
        groups.setdefault((name, e.year), []).append(e)
    for entries in groups.values():
        if len(entries) > 1:
            for i, e in enumerate(entries):
                e.key = make_key(e, chr(ord("a") + i))


#: 期刊出版信息正则（refs_to_bib 与 T13 分类共用同一份——判据只有一处实现）。
_EN_JOURNAL_RE = re.compile(
    r"^(?P<journal>[^,，]+?)\s*[,，]\s*"
    r"(?:(?P<year>(?:19|20)\d{2})\s*[,，]?\s*)?"
    r"(?P<volume>\d+)\s*\((?P<number>[^)]*)\)\s*[:：]\s*"
    r"(?P<pages>[\d\-\u2013\u2014]+)"
)
#: 无卷号形态：`Mind 85: 112-119`
_EN_JOURNAL_RE2 = re.compile(
    r"^(?P<journal>[^,，]+?)\s*[,，]?\s*(?P<volume>\d+)?\s*[:：]\s*"
    r"(?P<pages>[\d\-\u2013\u2014]+)\s*[.。]?\s*$"
)

# ── T13（2026-10-18）：英文人年制条目接 T12 同源判据 ──────────────────────────
#
# 旧实现用「卷期正则是否命中 / 有没有 Press 词」猜 kind（只有 M/J/C 三种），
# 实测期 87 sub2/sub5/sub6/sub8 的类型错（@book→@misc、@article→@incollection、
# @misc→@book 等）。T13 把**唯一判据**接过来：
#   · 类型：`slcn/footref.py: classify_entry_type(fields, raw=原文)`（与编号清单/脚注通道同源）；
#   · author：出口统一 `normalize_authors → normalize_author_field`（与 T12 同一 helper，
#     也即 `_bib_author()` 的同源顺序），只改英文路径。
_KIND_BY_ETYPE = {"book": "M", "article": "J", "incollection": "C",
                  "inproceedings": "P", "phdthesis": "D", "report": "R"}


def _match_en_journal(rest: str):
    """英文期刊正则入口（B-217②）：`refs_to_bib` 与 `_en_entry_fields` 共用。

    旧两条正则（`_EN_JOURNAL_RE` / `_EN_JOURNAL_RE2`）**先试**，原样命中时行为与
    从前逐字节相同；都不中时由 `wordrefs.match_en_journal()` 对「剥前导逗号/
    引号残留」的候选再试——扩展判据只有一处实现。
    """
    from .wordrefs import match_en_journal

    return match_en_journal(rest, _EN_JOURNAL_RE, _EN_JOURNAL_RE2)


def _clean_journal_name(name: str) -> str:
    """刊名剥残留引号/逗号（`“Synthese”` → `Synthese`）；无残留时逐字节不变。"""
    from .wordrefs import clean_journal_name

    return clean_journal_name(name)


def _match_incollection(rest: str) -> tuple[str, str]:
    """`(booktitle, editor)` 入口（B-217②）。

    先试 `wordrefs.incollection_in_editors()` 的**结构化英文形态**
    （`in … (ed./eds.), Book, pp. a–b, Place: Publisher`）——旧判据在这一族上
    会把 `in ` 前缀吃进 editor（`editor=in Doe, K.`），所以这里必须先试新判据；
    新判据返回空（不是这族形态）→ 走旧判据，**旧判据能解析的条目逐字节不变**
    （中文 `[C]//`、`… (eds.), 书名, https:…` 等都不含英文 `in` 标记）。
    """
    from .wordrefs import (
        incollection_editor_list, incollection_in_editors, is_in_editor_form)

    if is_in_editor_form(rest):
        # 这一族形态**只用新判据**：旧判据会把 `in ` 前缀吃进 editor
        # （`editor=in Doe, K.`），宁可只写书名也不写脏 editor（宁缺勿编）。
        bt, ed = incollection_in_editors(rest)
        if bt:
            return bt, ed
        return incollection_editor_list(rest)
    bt, ed = _incollection_book_editor(rest)
    if bt or ed:
        # 旧判据只认出一半（如 `(eds.)` 后没有书名）时也原样返回：editor 仍要写，
        # booktitle 绝不可编（B-217② 反例 ⑤ 的既有口径，B-220 N5 不改）。
        return bt, ed
    # B-220 N5：`in 编者列表, eds., 书名, 地: 社` 等旧判据全落空的两族形态
    return incollection_editor_list(rest)


def _en_entry_fields(e: RefEntry) -> dict:
    """英文 RefEntry 的出版信息 → `classify_entry_type()` 需要的 fields。

    复用 `refs_to_bib()` 的同一批提取器（期刊正则 / `_split_publisher()` /
    `_incollection_book_editor()`），**不新写解析逻辑**；判据本身在 footref。
    """
    from . import footref

    rest = re.sub(r"^[.。]\s*", "", (e.rest or "").strip())
    fields: dict[str, str] = {}
    jm = _match_en_journal(rest)
    if jm:
        gd = jm.groupdict()
        fields["journal"] = _clean_journal_name((gd.get("journal") or "").strip())
        fields["volume"] = gd.get("volume") or ""
        fields["number"] = gd.get("number") or ""
        fields["pages"] = (gd.get("pages") or "").replace("-", "--")
    else:
        # 页码带字母前缀/期号带英文词的期刊形态（`66(Supplement): S362-S378`，
        # 期 87 sub2 Fitelson1999 实测）`_EN_JOURNAL_RE` 的纯数字页匹配不上；
        # 判**类型**时仍要认出这是期刊（字段提取仍走 refs_to_bib 原口径，不扩范围）。
        mj = re.match(
            r"^(?P<journal>[^,，]+?)\s*[,，]\s*\d+\s*\([^)]*\)\s*[:：]\s*"
            r"(?P<pages>[A-Za-z]?\d[A-Za-z0-9\-–—]*)\s*[.。]?\s*$", rest)
        if mj:
            fields["journal"] = mj.group("journal").strip()
            fields["pages"] = mj.group("pages").replace("-", "--")
    pub, addr = _split_publisher(rest)

    def _plausible_pub(v: str) -> bool:
        """出版社/出版地不可是 `S362-S378` / `66(Supplement)` 这类页码/卷号（期 87 Fitelson 实测）。"""
        return bool(v) and not re.search(r"\d", v) and bool(re.search(r"[A-Za-z\u4e00-\u9fff]", v))

    if _plausible_pub(pub):
        fields["publisher"] = pub
    if _plausible_pub(addr):
        fields["address"] = addr
    bt, _ed = _match_incollection(rest)
    if bt:
        fields["booktitle"] = bt
    if not fields.get("publisher"):
        # 「出版地: 出版社」形态（`Oxford: Clarendon.` / `New York, NY: Basic Books`）——
        # `_split_publisher()` 只认出版社线索词，Clarendon 这类会漏；这里用 T12 的
        # `looks_like_place_publisher()`（同一判据）补一次。
        pp = footref.looks_like_place_publisher(rest.strip().rstrip("."))
        if pp:
            fields["address"], fields["publisher"] = pp
        else:
            m = re.search(
                r"([A-Z][A-Za-z.\- ]{1,28}(?:,\s*[A-Z]{2})?)\s*[:：]\s*"
                r"([A-Z][A-Za-z&.'\- ]{2,60})\s*[.。]?\s*$", rest)
            if m:
                fields["address"], fields["publisher"] = (
                    m.group(1).strip(), m.group(2).strip())
    if re.search(r"\bin\b|\(eds?\.?\)|\(Eds?\.?\)|edited\s+by", rest, re.I):
        fields["note"] = rest
    return fields


#: 机构/组织作者（不推断倒装；人名里一般不会出现）
_EN_ORG_RE = re.compile(
    r"\b(?:Organization|Organisation|Association|Society|Institute|University|College|"
    r"Department|Faculty|Center|Centre|Committee|Council|Group|Consortium|Network|"
    r"Agency|Office|Bureau|Board|Panel|Project|Foundation|Corporation|Company|Inc|Ltd|"
    r"Press|Publishing|Ministry|Government|Laboratory|Commission|Federation|Union|"
    r"Academy|WHO|UNESCO|ISO)\b", re.I)


def _finalize_en_entry(e: RefEntry) -> RefEntry:
    """英文人年制条目**出口一次**：按出版信息特征重判类型 + author 统一倒装（T13）。

    author 倒装走 T12 同源 helper（`normalize_authors → normalize_author_field`）；
    **机构/组织作者不倒装**（反例：`World Health Organization` 保持原样）。
    """
    from . import footref
    from .bibnorm import normalize_author_field
    from .footnotes import normalize_authors

    etype, _reason = footref.classify_entry_type(_en_entry_fields(e), raw=e.raw or "")
    e.kind = _KIND_BY_ETYPE.get(etype, "M")
    if e.author and not _EN_ORG_RE.search(e.author):
        e.author = normalize_author_field(normalize_authors(e.author))
    return e


def refs_to_bib(refs: list[RefEntry]) -> str:
    """把条目列表转成 .bib 文本。

    按用户要求尽力补齐期刊的卷号/期号/页码、书籍的出版地/出版商。
    解析不出来的字段**不编造**，整条塞进 note 留给人工核对。
    """
    from .bibnorm import detect_userf

    out = ["% 由 Word 参考文献自动生成，请核对类型与字段。", ""]
    for e in refs:
        etype = TYPE_MAP.get(e.kind, "book")
        fields: list[tuple[str, str]] = []
        if e.author:
            fields.append(("author", _bib_author(e.author)))
        if e.title:
            fields.append(("title", escape_bib_value(e.title)))

        # rest 常以「. 」开头（`. Mind, 85(337): 112-119.`），必须先剥掉，
        # 否则下面的期刊正则匹配不到——实测所有期刊条目都因此丢了卷/期/页。
        rest = re.sub(r"^[.。]\s*", "", e.rest.strip())
        # B-217②：中文学位论文（「博士论文，厦门大学，2009 年」）→
        # @phdthesis/@mastersthesis（引用库口径：school=学校、address=学位类型）。
        # 认不出 `_thesis` 时 etype 不变，旧路径逐字节不变。
        _thesis = None
        if etype in ("phdthesis", "mastersthesis"):
            from .wordrefs import en_thesis_fields, thesis_fields

            _thesis = thesis_fields(rest) or en_thesis_fields(rest)
        elif etype == "book":
            # B-217② F3：英文 `PhD thesis, Northwestern University` 旧分类会落成
            # book（模板 [4] 实测）→ 识别为学位论文（须认出 school，宁缺勿编）。
            from .wordrefs import en_thesis_fields

            _thesis = en_thesis_fields(rest)
        if _thesis:
            etype = _thesis["etype"]
        # B-217② B2-2：人年制英文编著章节（`in <书名>, pp. a–b. 地: 社.`，可能
        # 没有编者标记）旧分类落成 @book，且把 `pp. a–b. 地` 当出版地——结构可辨
        # 时按 incollection 输出（书名/页码/出版信息由 incollection 分支写）。
        _chapter: tuple[str, str] = ("", "")
        if not _thesis:
            from .wordrefs import (
                incollection_editor_list, incollection_in_book_pages, is_in_editor_form)

            _chapter = (_match_incollection(rest) if is_in_editor_form(rest)
                        else incollection_in_book_pages(rest))
            if not _chapter[0]:
                # B-220 N5：`in 编者列表, eds., 书名, 地: 社` 等新判据兜底
                _chapter = incollection_editor_list(rest)
            if etype == "book" and _chapter[0]:
                etype = "incollection"
        out.append(f"@{etype}{{{e.key},")
        jm = _match_en_journal(rest)
        if jm and etype == "article":
            gd = jm.groupdict()
            fields.append(("journal", escape_bib_value(_clean_journal_name(gd["journal"]))))
            # 中文期刊常写成「逻辑学研究, 2022(5): 38-50」——括号前那个数是**年份**
            # 而不是卷号。若它等于条目的年份，就当卷号不填。
            vol = gd.get("volume") or ""
            if vol and re.fullmatch(r"(19|20)\d{2}", vol) and vol == e.year:
                vol = ""
            if vol:
                fields.append(("volume", vol))
            if gd.get("number"):
                fields.append(("number", gd["number"]))
            if gd.get("pages"):
                fields.append(("pages", gd["pages"].replace("-", "--")))
            fields.append(("year", gd.get("year") or e.year))
        else:
            # ⚠️ `pub` / `addr` 仍是「**note 判据**」用的那一份（`_split_publisher`），
            #    一个字没动 —— 这样 book/article 的 note 行为逐字不变（案例 81）。
            pub, addr = _split_publisher(rest)
            # B-217② B2-4：`http(s)://` 不是出版信息（旧输出 `address={https}`、
            # `publisher={//plato}`）。URL 只进 `url` 字段，出版地/社一律不写。
            from .wordrefs import find_urls as _find_urls

            _urls = _find_urls(rest)
            if etype == "book":
                # B-217② B2-4：`http(s)://` 不是出版信息（旧输出 `address={https}`、
                # `publisher={//plato}`）——有 URL 时只写 url 字段，不写出版地/社。
                # pub/addr 仍照旧算着（note 判据逐字不变；其余分支不受影响）。
                if addr and not _urls:
                    fields.append(("address", escape_bib_value(addr)))
                if pub and not _urls:
                    fields.append(("publisher", escape_bib_value(pub)))
            elif etype == "incollection":
                # ── 案例 81：`[C]//` 条目已解析到手的字段被静默丢掉 ──────────
                # 旧字段表里从来没有 booktitle/editor，而 address/publisher 又被
                # `etype == "book"` 挡住，于是 `Tarski2019` 的
                # `马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.` 解析出来的
                # 商务印书馆/北京一个字都没写（回读校验报告却还写「❗必补：booktitle」，
                # 而它下一行贴出的源稿原文里明明有 —— 与第 66/68 条同一型事故）。
                # 口径见 `_incollection_book_editor()` 的 docstring：宁缺勿编。
                bt, ed = _chapter if _chapter[0] else _match_incollection(rest)
                cpub, caddr = _incollection_pub_addr(rest)
                if bt:
                    fields.append(("booktitle", escape_bib_value(bt)))
                if ed:
                    fields.append(("editor", _bib_author(ed)))
                # 字段序照本项目规范：title → booktitle → editor → publisher → address
                # （见 docs/RULES.md 与 slcn/aibib.py 顶部的固定顺序）。
                if cpub:
                    fields.append(("publisher", escape_bib_value(cpub)))
                if caddr:
                    fields.append(("address", escape_bib_value(caddr)))
                # B-217② B2-2：章节页码（`pp. 46-62` → pages=46--62）
                from .wordrefs import pages_from_rest

                _pg = pages_from_rest(rest)
                if _pg:
                    fields.append(("pages", _pg))
            elif _thesis:
                # B-217②：学位论文 —— 引用库既有口径（`bib_db` 实测）：
                # `@phdthesis/@mastersthesis` + `school=学校`；中文学位论文另有
                # `address=学位类型`（英文条目引用库不写 address，不臆造）。
                fields.append(("school", escape_bib_value(_thesis["school"])))
                if _thesis.get("degree"):
                    fields.append(("address", escape_bib_value(_thesis["degree"])))
            fields.append(("year", e.year))
            # B-217②：网络资源 `http(s)://` → `url` 字段（不猜、原样保留；从 note
            # 里删掉同一网址，避免印刷出现两遍）。无 URL 的条目逐字节不变。
            from .wordrefs import strip_urls

            if _urls:
                fields.append(("url", escape_bib_value(_urls[0])))
            # P0-2 兜底：@article 但期刊卷期页没解析出来时，把 rest 原文塞进 note，
            # 不再静默丢（期 87 sub5 有 18 条 pages 丢失且无任何痕迹）。
            if etype == "article" and not any(k == "journal" for k, _ in fields) and rest:
                fields.append(("note", escape_bib_value(rest)))
            # ⚠️ 案例 81 的**第二半：本轮故意不动**（明写在此，不悄悄丢）。
            #     `rest` 里那些「版次 / 中译加注」现在仍只在这条 note 判据下兜底：
            #     `pub or addr` 一真就 `note = ""`，于是
            #       · `Haack1974` 的 `Second, expanded edition: … 1996`（@book）
            #       · `Quine1970` 的 `2nd edition, Cambridge, MA: Harvard … 1986`（@book）
            #       · `Williamson2007` 的 `Second and enlarged edition, … Wiley Blackwell, 2021`（@book）
            #       · `Haack1978` / `Kripke2024` / `Maddy2002` 括号里的中译注记（@book/@article）
            #     全都进不了 bib —— 与缺陷所述一致。**本轮不修**的理由是硬的：
            #     把它们放进 note 必须改 @book / @article 两个分支的输出，而本轮验收
            #     明令「`@book` 的既有输出逐字不变」「同一条目已有的字段值不许改坏」，
            #     全库量噪声也要求「改前有、改后没有 = 0、值被改坏 = 0」。
            #     这是一个**尚未修**的数据损失（不是已修），独立于第 81 条的
            #     `[C]//` 字段丢失；留给下一步（改 note 口径时需同步 book/article 分支
            #     与冻结基线 `out/docx_audit.csv` 的字段统计）。
            # B-217②：学位论文已写 school/address 时不留重复 note（引用库无 note）。
            note = rest if not (pub or addr or _thesis) else ""
            if note and _urls:
                note = strip_urls(note)
            if note:
                fields.append(("note", escape_bib_value(note)))

        uf = detect_userf(e.author, e.title)
        if uf:
            fields.append(("userf", uf))

        width = max((len(k) for k, _ in fields), default=4)
        for k, v in fields:
            out.append(f"  {k.ljust(width)} = {{{v}}},")
        if out[-1].endswith(","):
            out[-1] = out[-1][:-1]
        out.append("}")
        out.append("")
    return "\n".join(out)


def _split_publisher(rest: str) -> tuple[str, str]:
    """从 `出版地: 出版商` 里拆出两者。中文条目常见「北京: 商务印书馆」。"""
    m = re.search(r"([^:：,，]{2,30})[:：]\s*([^,，.。]{2,60})", rest)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    pub = _extract_publisher(rest)
    return pub, ""


def _bib_author(a: str) -> str:
    """规范成 `and` 分隔后**倒装**，再转义。

    ⚠️ 不能原样照抄：源稿常见「逗号 + and」混用（`J. P. Dillard, C. Segrin and J. M. Harden`），
    而 BibTeX 里**逗号是倒装符**，照抄会编译成「C. S. J. P. Dillard and J. M. Harden」。
    倒装名（`Haack, S.`）由 `normalize_authors` 识别并保持原样。

    ⚠️ 顺序不能反：**先** `normalize_authors`（拆「逗号 + and」混用、统一 `and`），
    **再** `normalize_author_field`（英文一律倒装成 `姓, 名`）。反过来的话，混用串里
    有逗号会被倒装器当成「已经倒装」而不动，拆完仍是名前姓后（实测就是这么漏的）。
    """
    from .bibnorm import normalize_author_field
    from .footnotes import normalize_authors

    return escape_bib_value(normalize_author_field(normalize_authors(a)))


def _extract_publisher(rest: str) -> str:
    m = re.search(r"[:：]\s*([^:：,，\.。]+(?:出版社|Press|University|Publishing|Blackwell|Routledge|商务印书馆|人民出版社|译文出版社|教育出版社)[^,，\.。]*)", rest)
    return m.group(1).strip() if m else ""


# ── 案例 81：`@incollection`（`[C]//`）的 booktitle / editor / 出版信息 ──────
#
# `[C]//` 是 GB/T 7714 的「析出文献」形态：
#     析出文献主要责任者. 析出文献题名[C]// 专著主要责任者. 专著题名[M]. 出版地: 出版者, 年: 页.
# 实测（2026年第5期(90)/10 Lei_Li）：
#     塔斯基. 2019. 真理的语义概念和语义学的基础[C]// 马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.
# 解析器把 rest 拿全了（`马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.`），
# 旧字段表却从来没有 booktitle/editor，而 address/publisher 又被 `etype == "book"` 挡住
# —— 于是**已经解析到手**的「商务印书馆 / 北京」被静默丢掉（`refs_to_bib` 里有接线）。
# 回读校验报告因此在这条上写「❗必补：booktitle」，而它下一行贴出的源稿原文里明明写着书名
# —— 与第 66 / 68 条同一型事故：喂给 AI 的本来就是残缺件。
#
# 三条判据（**宁缺勿编**，第 61 条口径「编错的字段比空缺更糟」）：
#   ① booktitle 只认两种**结构清楚**的形态，认不出就不写：
#        · 有 `[M]`/`[A]`/`[D]` 收尾 → 取 `[C]//` 到该标记之间、最后一个句读后的那一段；
#        · 源稿**明确**写了编者标记 → 取标记之后到下一个逗号/句号前的那一段。
#   ② editor 只在源稿明确给出编者标记（`(eds.)` / `（编）` / `主编`）时才写；
#      `[C]//` 后那个不带标记的「专著主要责任者」（如 `马蒂尼奇`）**不写**
#      —— 它按体例也可能是专著的作者，分不清就不猜。
#   ③ 出版地/出版者另走 `_incollection_pub_addr()`：通用 `_split_publisher()` 在这两种
#      真实形态上会给出**错值**（见该函数 docstring），所以只认能确定的那一对。
_BT_STOP_RE = re.compile(r"\[\s*[MAD]\s*\]")
# 源稿里**明确**的编者标记：`(eds.)` / `(ed.)` / `(editors)` / `（编）` / `主编`
_EDITOR_MARK_RE = re.compile(r"\(\s*eds?\.?\s*\)|\(\s*editors?\s*\)|（\s*编\s*）|主编")
# 版次 / 卷次等「不是书名」的片段
_BT_NON_TITLE_RE = re.compile(
    r"^(?:第\s*[\d一二三四五六七八九十]+\s*版"
    r"|(?:\d+\s*)?(?:st|nd|rd|th)?\s*ed(?:ition)?\.?"
    r"|(?:revised|enlarged|expanded|second|third|fourth|fifth|first)\s+ed(?:ition)?.*"
    r"|\d+\s*卷)$",
    re.I,
)
# 出版信息候选：冒号前是地名、冒号后是出版者（沿用 `_split_publisher` 的正则形状）
_PUB_COLON_RE = re.compile(r"([^:：,，]{1,40})[:：]\s*([^,，.。\[\]]{2,60})")


def _bt_candidate_ok(s: str) -> bool:
    """一个片段够不够格当书名（案例 81）：够格才写，拿不准返回 `False`。"""
    s = s.strip(" \t\u3000.。，,;；、")
    if not (1 <= len(s) <= 120):
        return False
    # 出版信息 / URL / 类型标记 / 逗号冒号混进来 = 切错了，不是书名
    if re.search(r"[\[\]{}]|https?|://|[,，;；:：]", s):
        return False
    if _BT_NON_TITLE_RE.match(s):
        return False
    if re.search(r"译|著|主编", s):        # 译者/著者标记 = 切到了责任者，不是书名
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 2:
        return True
    return bool(re.search(r"[A-Za-z]{4,}", s))


def _editor_candidate_ok(s: str) -> bool:
    """一个片段够不够格当编者串（案例 81）。"""
    s = s.strip(" \t\u3000.。，,;；、")
    if not (2 <= len(s) <= 120):
        return False
    if re.search(r"[\[\]{}]|https?|://|\d|译|著", s):
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 2:
        return True
    return bool(re.search(r"[A-Za-z]{2,}", s))


def _incollection_book_editor(rest: str) -> tuple[str, str]:
    """从 `[C]//` 之后的 `rest` 里取 `(booktitle, editor)`（案例 81）。

    只认**结构清楚**的两种形态，其余一律 `("", "")`（宁缺勿编）：

      `马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.`
          → booktitle=`语言哲学`、editor=``（`马蒂尼奇` 没有编者标记，不写）
      `Zalta, E N. & Nodelman, U (eds.), The Stanford Encyclopedia of Philosophy, https:…`
          → booktitle=`The Stanford Encyclopedia of Philosophy`、
            editor=`Zalta, E N. & Nodelman, U`（`(eds.)` 是明确编者标记）

    拿不准的一律不写，例如 `马蒂尼奇 语言哲学[M]`（无句读分隔，分不清责任者与书名）、
    `马蒂尼奇. 语言哲学. 第2版[M]`（末段是版次）。
    """
    stop = _BT_STOP_RE.search(rest)
    zone = (rest[: stop.start()] if stop else rest).strip(" \t\u3000")
    if not zone:
        return "", ""
    # `[C]// 编者.[M]`：句读之后没有书名（形态残缺）→ 不写，别把责任者当书名
    if stop and re.search(r"[.。]\s*$", zone):
        return "", ""

    # ① 源稿**明确**写了编者标记 → 编者 = 标记前，书名 = 标记后到下一个句读
    em = _EDITOR_MARK_RE.search(zone)
    if em:
        ed = zone[: em.start()].strip(" \t\u3000.。，,;；、")
        if _editor_candidate_ok(ed):
            tail = zone[em.end():].strip(" \t\u3000.。，,;；、")
            bt = re.split(r"[,，;；.。]", tail)[0].strip(" \t\u3000.。，,;；、")
            return (bt if _bt_candidate_ok(bt) else ""), ed
        return "", ""

    # ② `专著主要责任者. 专著题名[M]`：书名是最后一个句读后的那一段
    if stop:
        segs = [s for s in (x.strip(" \t\u3000.。，,;；、") for x in re.split(r"[.。]", zone)) if s]
        if not segs:
            return "", ""
        last = segs[-1]
        # 只有一段、且中间夹着空白（`甲 乙`）→ 分不清责任者与书名，不写
        if len(segs) == 1 and re.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", last):
            return "", ""
        return (last if _bt_candidate_ok(last) else ""), ""

    # ③ 既没有 `[M]`/`[A]`/`[D]`、也没有编者标记 → 拿不准，不写
    return "", ""


def _incollection_pub_addr(rest: str) -> tuple[str, str]:
    """`@incollection` 的 `(出版者, 出版地)`（案例 81）。认不出返回 `("", "")`。

    为什么不能直接用 `_split_publisher()`（它服务 book 分支，逐字不能动）——两条实测：
      · `…牟博, 译. 北京: 商务印书馆.` → 通用正则把「译. 北京」当地址
        （`[^:：,，]` 被逗号截断，`译. 北京` 整段当了地名）；
      · `…, https://plato.stanford.edu/…` → 通用正则拿 `https` 当地址、`//plato` 当出版者。
    所以这里只认第一对「冒号前是干净地名、冒号后是干净出版者」，并把地址收窄到最后一个
    句读之后（`译. 北京` → `北京`）；`://` 直接跳过。
    """
    for m in _PUB_COLON_RE.finditer(rest):
        raw_addr, pub = m.group(1), m.group(2).strip(" \t\u3000.。")
        if pub.startswith("//") or "http" in pub.lower():
            continue                        # URL 的冒号，不是出版信息
        addr = re.split(r"[.。]", raw_addr)[-1].strip(" \t\u3000,，、;；")
        if not (1 <= len(addr) <= 30) or not (2 <= len(pub) <= 60):
            continue
        if re.search(r"译|著|编|校|https?|www|\d", addr):
            continue                        # 「译. 北京」「2012」这类不是地名
        if re.fullmatch(r"[\d\-–—\s]+", pub):
            continue                        # `2012: 812-817` 是年:页，不是出版信息
        return pub, addr
    return "", ""


