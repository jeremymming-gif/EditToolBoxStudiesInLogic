r"""B-217②：参考文献字段解析的**扩展判据**（`refs_to_bib` 解析面，宁缺勿编）。

背景（B-215 实测，见 memo `drafts/b217-engine-word-gaps.md` 第 2–4/7 条）：
  · 英文期刊条目 `journal` 丢：编号条目 `rest` 常以**逗号/引号残留**开头；
  · 英文编著章节 `booktitle/editor` 丢：`in … (ed./eds.), Book, pp. a–b,
    Place: Publisher` 变体（含 `et al.`/多编者）旧判据不认；
  · 中文学位论文「博士论文，厦门大学，2009 年」→ 旧实现只当杂项；
  · 网络资源 `http(s)://` URL 不进 `url` 字段。

本模块只放**新增的兜底判据**（旧判据仍在 ``docxsource``，且一律**先试旧判据**）：
旧路径能解析的条目一个字都不变，这里只在旧路径返回空/不命中时补。
字段口径：
  · 学位论文按引用库既有约定（`EditToolBox-notes/bib_db`）：`@phdthesis` /
    `@mastersthesis`，`school=学校`，**`address=学位类型原字串`**（「博士论文」）；
  · 期刊名剥掉残留引号/逗号（`“Synthese”` → `Synthese`）；
  · URL 进 `url` 字段，并从 `note` 里删掉（避免同一网址印两遍）。
"""

from __future__ import annotations

import re

_LEAD_SEP = " \t\u3000"
_LEAD_NOISE = ",，、;；:："
_QUOTES = "\"'“”‘’「」『』"


# ── 英文期刊：rest 前导噪声 / 引号残留 ─────────────────────────────────────


#: 手写刊名简写形态：`Vol. 68 (1), pp. 5-20` / `Vol. 68, No. 1, pp. 5-20` /
#: `Vol. 68, pp. 5-20`（B-220 N4d；实测 2026年第5期(90) 90-06 两条）。
#: 旧两条期刊正则要求 `68(1): 5-20` 的冒号形态，这族一律落空 → journal/volume/
#: number/pages 全丢、条目退化成 @misc（字段被 misc 字段序丢弃）。这里只**改写
#: 成旧判据能认的形态**再交给同一份 `pat1`，判据本身仍只有一处。
_VOL_SHORT_RE = re.compile(
    r"^(?P<journal>.+?)\s*[,，]\s*Vol\.?\s*(?P<vol>\d{1,4})\s*"
    r"(?:\(\s*(?P<num>[^)]+?)\s*\))?"
    r"(?:\s*[,，]\s*No\.?\s*(?P<num2>\d{1,4}))?\s*[,，]\s*"
    r"(?:pp?\.?\s*)?(?P<page1>[A-Za-z]?\d{1,5})\s*[-–—~]\s*(?P<page2>[A-Za-z]?\d{1,5})",
    re.I,
)


def _vol_short_candidate(rest: str) -> str:
    """`Journal, Vol. 68 (1), pp. 5-20.` → `Journal, 68(1): 5-20`（认不出返回 ""）。"""
    m = _VOL_SHORT_RE.match((rest or "").strip())
    if not m:
        return ""
    num = (m.group("num") or m.group("num2") or "").strip()
    head = f"{m.group('vol')}({num})" if num else m.group("vol")
    return f"{m.group('journal').strip()}, {head}: {m.group('page1')}-{m.group('page2')}"


def journal_rest_variants(rest: str) -> list[str]:
    """``rest`` 的归一化候选（只**多剥**前导噪声 / 改写简写形态，不改原始串本身）。"""
    out: list[str] = []
    r = (rest or "").lstrip(_LEAD_SEP)
    if r != rest:
        out.append(r)
    r2 = r.lstrip(_LEAD_NOISE)
    if r2 and r2 != r:
        out.append(r2)
    r3 = r
    while r3 and r3[0] in _QUOTES:
        r3 = r3[1:].lstrip(_LEAD_SEP + _LEAD_NOISE)
    if r3 and r3 not in out:
        out.append(r3)
    # 引号题名残留：`“Title”, Journal, 85: 1-2.` → 题名之后的出版信息
    m = re.match(r"^[\"'“「『].+?[\"'”」』]\s*[,，]\s*(?P<rest>.+)$", r)
    if m and m.group("rest").strip() not in out:
        out.append(m.group("rest").strip())
    # B-220 N4d：`Vol./No./pp.` 简写形态 → 旧判据能认的 `卷(期): 页` 形态
    for cand in (r, r2):
        v = _vol_short_candidate(cand)
        if v and v not in out:
            out.append(v)
    return out


def match_en_journal(rest: str, pat1, pat2):
    """旧两条期刊正则优先；都不中时对归一化候选再试（B-217②）。

    ``pat1``/``pat2`` 由调用方传入（``docxsource._EN_JOURNAL_RE`` /
    ``_EN_JOURNAL_RE2``）——**判据只有那一份**，本函数只负责「换 rest 再试」。
    """
    m = pat1.match(rest) or pat2.match(rest)
    if m:
        return m
    for cand in journal_rest_variants(rest):
        m = pat1.match(cand) or pat2.match(cand)
        if m:
            return m
    return None


def journal_raw_candidates(raw: str) -> list[str]:
    """编号清单原文里可能承载「刊名, 卷(期): 页」的候选片段（B1）。

    优先取**题名引号之后**的那一段（`… ”, Midwest Studies of Philosophy, 6(1): 17–35.`），
    再退回「从第 2 个逗号段起」的累积串（无引号体例 / 引号残留）。
    """
    t = re.sub(r"^\s*[\[\(（]\s*\d{1,3}\s*[\]\)）]\s*", "", (raw or "").strip())
    out: list[str] = []
    m = re.search(r"[”\"」』]\s*[,，]\s*(?P<tail>.+)$", t)
    if m:
        out.append(m.group("tail").strip())
    parts = [x for x in re.split(r"[,，]", t) if x.strip()]
    for i in range(1, len(parts)):
        cand = ",".join(parts[i:]).strip()
        if cand and cand not in out:
            out.append(cand)
    if t and t not in out:
        out.insert(0, t)
    return out


_PP_PAGES_RE = re.compile(r"\bpp?\.?\s*(\d{1,5})\s*[-–—]\s*(\d{1,5})")


def pages_from_rest(rest: str) -> str:
    """`pp. 46-62` / `p. 7` → bib `pages`（区间用 `--`）；没有返回空串。"""
    m = _PP_PAGES_RE.search(rest or "")
    if not m:
        return ""
    return f"{m.group(1)}--{m.group(2)}"


def incollection_in_book_pages(rest: str) -> tuple[str, str]:
    """`in <书名>, pp. a–b. 地: 社.`（**无编者标记**的章节）→ ``(booktitle, "")``。

    B2-2 实测：`…, in Responsibility, Character, and the Emotions, pp. 46-62.
    Cambridge: Cambridge University Press.` —— 旧判据只认 `(ed./eds.)`，于是整条
    落成 @book 且把 `pp. 46-62. Cambridge` 当地址。这里要求**同时**出现 `in …`,
    逗号后紧跟 `pp.`，书名取到 `pp.` 之前；认不出 → `("", "")`（不猜）。
    """
    if not rest:
        return "", ""
    m = re.search(
        r"(?:^|(?<=[\s,，;；.。]))[Ii]n\s*[:：]?\s+(?P<book>.+?),\s*"
        r"(?P<tail>pp?\.?\s*\d.*)$", rest)
    if not m:
        return "", ""
    book = _clean_booktitle(m.group("book").strip(" \t\u3000,，.。;；"))
    if not _plausible_book(book):
        return "", ""
    return book, ""


def clean_journal_name(name: str) -> str:
    """刊名剥残留引号/前后标点（`“Synthese”` → `Synthese`）。"""
    s = (name or "").strip()
    s = s.strip(_QUOTES + " ,，、;；")
    return s.strip()


# ── 英文编著章节：`in … (ed./eds.), Book, pp. a–b, Place: Publisher` ──────


def _plausible_editor(s: str) -> bool:
    s = s.strip()
    if not (2 <= len(s) <= 120):
        return False
    if not re.search(r"[A-Za-z]", s) or re.search(r"\d|https?:|://|pp?\.\s*\d", s):
        return False
    return True


def _plausible_book(s: str) -> bool:
    s = s.strip(" \t\u3000,，.。;；")
    if not (2 <= len(s) <= 200):
        return False
    if re.search(r"https?:|://|pp?\.\s*\d|\b(?:eds?|Editors?)\.?\b", s):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", s))


def _looks_like_pub_seg(s: str) -> bool:
    """片段是「页码 / 出版信息 / 网址」而不是书名续段。"""
    t = s.strip()
    if "http" in t or "://" in t or t.lower().startswith("www."):
        return True
    if re.match(r"^(?:pp?\.?\s*)?\d", t):
        return True
    if re.search(r"\b(?:Press|Publishing|Publishers?|Verlag|Routledge|Blackwell|"
                 r"Wiley|Springer|University|Books?|Co\.|Inc\.)\b", t):
        return True
    if re.match(r"^[A-Z][A-Za-z.\- ]{1,30}[:：]\s*[A-Z]", t):
        return True
    return False


_IN_MARK_RE = re.compile(r"(?:^|(?<=[\s,，;；.。]))[Ii]n\s*[:：]?\s+(?=\S)")
_ED_MARK_RE = re.compile(r"\(\s*(?:eds?|Editors?|EDs?)\.?\s*\)")


def is_in_editor_form(rest: str) -> bool:
    """``rest`` 是否属于 `in … (ed./eds.), …` 这一族形态（结构识别）。"""
    return bool(rest and _IN_MARK_RE.search(rest) and _ED_MARK_RE.search(rest))


def incollection_in_editors(rest: str) -> tuple[str, str]:
    """``in 编者 (eds.), 书名, pp. a–b, 出版地: 出版社`` → ``(booktitle, editor)``。

    只在 ``docxsource._incollection_book_editor()`` 返回空时调用（旧路径优先）。
    拿不准（结构不完整/书名像出版信息）→ ``("", "")``，绝不猜。
    """
    if not rest:
        return "", ""
    m = _IN_MARK_RE.search(rest)
    if not m:
        return "", ""
    tail = rest[m.end():].strip()
    em = _ED_MARK_RE.search(tail)
    if not em:
        return "", ""
    editor = tail[:em.start()].strip(" \t\u3000,，.。;；")
    if not _plausible_editor(editor):
        editor = ""                     # 编者不可辨 → 只写书名（不猜 editor）
    after = tail[em.end():].strip(" \t\u3000,，.。;；")
    if not after:
        return "", ""
    # 书名：吃到「页码/出版信息」之前；书名内部的逗号/冒号保留（副题常见）
    segs = [s.strip() for s in re.split(r"[,，]", after) if s.strip()]
    if not segs:
        return "", ""
    # 编者标记之后第一个片段**一定**是书名开头（结构已由 `in … (eds.)` 锁定）；
    # 之后的片段一旦像页码/出版信息就停（书名内部的逗号/副题保留）。
    book_parts: list[str] = [segs[0]]
    for seg in segs[1:]:
        if _looks_like_pub_seg(seg):
            break
        book_parts.append(seg)
    book = _clean_booktitle(_join_book_parts(book_parts))
    if not _plausible_book(book):
        return "", ""
    return book, editor


_EDITION_PAREN_RE = re.compile(
    r"\s*\(\s*[^()]*\b(?:edition|edn|ed\.|eds\.)\b[^()]*\)", re.I)


def _clean_booktitle(s: str) -> str:
    """书名去掉末尾版次括注（`The Stanford Encyclopedia of Philosophy
    (Spring 2019 Edition)` → 去掉括注；不改书名正文）。"""
    return _EDITION_PAREN_RE.sub("", s).strip()


def _join_book_parts(parts: list[str]) -> str:
    """书名的续段拼接：中文逗号/西文逗号都按原文连接。"""
    out = ""
    for p in parts:
        p = p.strip(" \t\u3000.。;；")
        if not p:
            continue
        if not out:
            out = p
        elif re.search(r"[\u4e00-\u9fff]", out + p):
            out += "，" + p
        else:
            out += ", " + p
    return out


# ── B-220 N5：`in 编者列表, eds., 书名, 地: 社` / `in 书名, eds. 编者, 地: 社` ──
#
# 实测 2026年第5期(90)：`in M. J. Cody and M. L. McLaughlin, eds., The Psychology of
# Tactical Communication, Clevedon: Multilingual Matters.`（编者在前）与
# `in Logic, Methodology and Philosophy of Science, eds. E. Nagel, P. Suppes, and
# A. Tarski, Stanford: …`（书名在前）——旧判据只认 `(eds.)` 括注形态，这两族全落空：
# 条目退化成 @book/@misc，booktitle/editor 整段丢失（人工成品是 @incollection）。
# 只认**结构可辨**的形态；认不出返回 ("","")，绝不猜。
_EDS_ALONE_RE = re.compile(r"^(?:eds?|Eds?|editors?|Editors?)\.?$", re.I)
_EDS_HEAD_RE = re.compile(r"^(?:eds?|Eds?|editors?|Editors?)\.?\s+(?P<rest>.+)$")
#: 编者标记挂在**段尾**：`J. W. Davis, D. J. Hockney and W. K. Wilson (eds.)`（B-222 D3）。
_EDS_TAIL_RE = re.compile(
    r"^(?P<eds>.+?)\s*\((?P<mark>eds?|Eds?|editors?|Editors?)\.?\)\s*$", re.I)
#: 原始条目里 `in` 标记（前面可能是空格/逗号/句号/**引号收尾** —— `”in Logic` 实测）。
_IN_ANY_RE = re.compile(r"(?:^|(?<=[\s,，;；.。“”\"'「」『』]))[Ii]n\s*[:：]?\s+(?=\S)")
_AUTHORISH_SEG_RE = re.compile(
    r"^(?:(?:van|von|de|der|den|del|di|da|le|la)\s+)?(?:[A-Z]\.\s*)+"
    r"[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?$")


def _split_segs(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，]", text or "") if x.strip()]


def _take_book_parts(segs: list[str]) -> str:
    """`eds.` 之后的片段 → 书名：第一段**结构上就是书名**（可能含 Routledge 等
    出版社线索词，如 `The Routledge Handbook of…`），不能按「像出版社」丢掉；
    之后遇到页码/出版信息才停。"""
    if not segs:
        return ""
    parts: list[str] = [segs[0]]
    for seg in segs[1:]:
        if _looks_like_pub_seg(seg):
            break
        parts.append(seg)
        if len(parts) >= 8:
            break
    return _clean_booktitle(_join_book_parts(parts))


def _take_editor_parts(segs: list[str]) -> str:
    if not segs:
        return ""
    parts: list[str] = [re.sub(r"^(?:and|&)\s+", "", segs[0])]
    for seg in segs[1:]:
        if _looks_like_pub_seg(seg):
            break
        parts.append(re.sub(r"^(?:and|&)\s+", "", seg))
        if len(parts) >= 8:
            break
    ed = ", ".join(parts).strip(" \t\u3000,，.。;；")
    # 编者段若混进出版信息（源稿缺逗号：`… and Robert J. Stainton Oxford: OUP`）
    # → 不写 editor（宁缺勿错）。
    if not ed or re.search(r"[:：]", ed):
        return ""
    return ed if _plausible_editor(ed) else ""


def _editor_list_from_tail(tail: str, *, allow_c: bool = False) -> tuple[str, str]:
    """对 `in ` 之后的一段做 A/B/C/D 四形态识别（供 ``incollection_editor_list`` 用）。

    ``allow_c`` 保留供调用方表达通道语义（编号清单 = 容器标记一定在题名之后）。
    B-222 B 段 D1 起 C 形态不再按通道开关：安全条件（年份锚点 + 非期刊
    `N(M):p` 形态）本身就足以排除短引与期刊（实测 90-07 人年制 `in 书名, 2005`）。
    """
    segs = _split_segs(tail)
    if len(segs) < 2:
        return "", ""
    # A：编者列表在前，`eds./ed.` 独占一段
    for i, seg in enumerate(segs):
        if i >= 1 and _EDS_ALONE_RE.match(seg):
            editor = ", ".join(
                re.sub(r"^(?:and|&)\s+", "", x) for x in segs[:i]).strip()
            if not _plausible_editor(editor):
                editor = ""
            book = _take_book_parts(segs[i + 1:])
            if _plausible_book(book):
                return book, editor
            return "", ""
    # B：书名在前，`eds. 编者…` 段起头
    for i, seg in enumerate(segs):
        if i < 1:
            continue
        em = _EDS_HEAD_RE.match(seg)
        if not em:
            continue
        book = _clean_booktitle(_join_book_parts(segs[:i]))
        editor = _take_editor_parts([em.group("rest")] + segs[i + 1:])
        if _plausible_book(book):
            return book, editor
        return "", ""
    # D：`in 编者列表, 书名 (eds.), …` —— 编者标记挂在某段**尾部**（既非独占段、
    #    也非段首）。B-222 B 段 D3 实测 90-04 Harrison1969 / 90-05 Horwich2005
    #    （`in J. W. Davis, D. J. Hockney and W. K. Wilson (eds.), Philosophical Logic`）。
    for i, seg in enumerate(segs):
        em = _EDS_TAIL_RE.match(seg)
        if not em:
            continue
        editor = _take_editor_parts(segs[:i] + [em.group("eds")])
        book = _take_book_parts(segs[i + 1:])
        if editor and _plausible_book(book):
            return book, editor
        return "", ""
    # C：无编者标记——书名不像作者名、不含数字，且其后第一段确是出版信息
    #    （裸年份 / 页码 / 地: 社）；期刊的 `Journal, 68(1): 5-20` 形态一律排除。
    #    B-222 B 段 D1：年份锚点 + 非期刊形态即启用（人年制通道也认，
    #    旧 gate 只在编号清单通道开，实测 90-07 短引容器漏判）。
    if not re.search(r"(?:19|20)\d{2}", tail):
        return "", ""
    head = segs[0].strip()
    if (not _AUTHORISH_SEG_RE.match(head) and not re.search(r"\d", head)
            and not re.search(r"\d+\s*\([^)]*\)\s*[:：]", tail)
            and len(segs) >= 2):
        nxt = segs[1].strip()
        if (re.fullmatch(r"(?:19|20)\d{2}[a-z]?", nxt)
                or re.match(r"^(?:pp?\.?\s*)?\d", nxt)
                or re.match(r"^[A-Z][A-Za-z.\- ]{1,30}[:：]\s*[A-Z]", nxt)):
            book = _take_book_parts(segs)
            if _plausible_book(book):
                return book, ""
    return "", ""


def in_container_tail(raw: str) -> str:
    """从整条原文里取「引号题名之后、以 in 开头」的容器段（B-220 N5）。

    编号清单通道传的是整条 `[N] 作者, 年, “题名”, in 编者…`——容器判据只能看
    题名**之后**那一段，否则题名里自带的单词 in（如 *Tales … in the Metaphysics
    of Intentionality*）会造成误判。认不出（无引号题名）→ ""（不猜）。
    """
    m = re.search(r"[”\"」』]\s*[,，]?\s*(?P<tail>[Ii]n\s+.+)$", (raw or "").strip())
    return m.group("tail").strip() if m else ""


def incollection_editor_list(rest: str, *, allow_c: bool = False) -> tuple[str, str]:
    """B-220 N5：`in …` 起头的编者/书名列表形态 → ``(booktitle, editor)``。

    三种子形态（均要求结构清楚）：
      A. `in 编者列表, eds., 书名, 地: 社`  —— 编者段在前、`eds.` 独占一段；
      B. `in 书名, eds. 编者, 地: 社`        —— 书名在前、`eds. …` 段起头；
      C. `in 书名, <年份/卷期/地: 社>`      —— 无编者标记；要求书名段不含数字、
         其后的段确为出版信息（裸年份/页码/地: 社），且整体不是期刊的
         `Journal, 68(1): 5-20` 形态 —— 否则会误把期刊/题名里的 in 当容器。
    输入从 `in` 开头（调用方用 `in_container_tail()` 从整条原文取，或 rest 本就
    以 in 开头）；认不出/拿不准 → ``("", "")``。
    """
    s = re.sub(r"^[\s,，.。]+", "", (rest or "").strip())
    if not re.match(r"(?i)^in\s", s):
        return "", ""
    tail = re.sub(r"(?i)^in\s*[:：]?\s*", "", s)
    return _editor_list_from_tail(tail, allow_c=allow_c)


# ── 中文学位论文：`博士论文，厦门大学，2009 年` ────────────────────────────
_THESIS_KIND_RE = re.compile(r"(?P<kind>博士(?:学位)?论文|硕士(?:学位)?论文)")
_SCHOOL_HINT_RE = re.compile(r"大学|学院|研究院|研究所|学校|科学院")


def thesis_fields(rest: str) -> dict | None:
    """``rest`` 里的中文学位论文信息 → ``{"etype","degree","school"}``；不确定返回 ``None``。

    口径（引用库 `bib_db` 实测）：``@phdthesis``/``@mastersthesis``，
    ``school=学校``，``address=学位类型原字串``（如「博士论文」「硕士学位论文」）。
    学校缺失 → ``None``（不猜，交旧路径 note 兜底）。
    """
    if not rest:
        return None
    m = _THESIS_KIND_RE.search(rest)
    if not m:
        return None
    degree = m.group("kind")
    etype = "mastersthesis" if degree.startswith("硕士") else "phdthesis"
    tail = rest[m.end():]
    school = ""
    kind_order = "school_after"
    for part in re.split(r"[，,。.；;、]", tail):
        p = part.strip(" \t\u3000，,。.；;、")
        if not p:
            continue
        if _SCHOOL_HINT_RE.search(p) and len(p) <= 40:
            school = p
            break
    if not school:
        # B-222 B 段 B3：编号清单实测 `北京师范大学博士论文` —— 校名在学位类型
        # **之前**。只认紧邻、结构上像校名的一段（不猜）；旧语序仍返回
        # ``school_after``，由 `apply_numbered_extras` 收口（既有断言不动）。
        before = [p.strip(" \t\u3000，,。.；;、")
                  for p in re.split(r"[，,。.；;、\s]", rest[:m.start()])]
        for part in reversed(before):
            if not part:
                continue
            if _SCHOOL_HINT_RE.search(part) and len(part) <= 40:
                school = part
                kind_order = "school_before"
            break
    if not school:
        return None
    return {"etype": etype, "degree": degree, "school": school,
            "kind_order": kind_order}


# ── 英文学位论文：`PhD thesis, Northwestern University` ────────────────────

_EN_THESIS_RE = re.compile(
    r"(?i)\b(?P<kind>ph\.?\s*d\.?\s+(?:thesis|dissertation)"
    r"|doctoral\s+(?:thesis|dissertation)"
    r"|master'?s?\s+(?:thesis|dissertation)|m\.?\s*a\.?\s+thesis"
    r"|undergraduate\s+(?:honors\s+)?thesis|bachelor'?s?\s+thesis)\b")
_EN_SCHOOL_RE = re.compile(
    r"(?i)\b(?:University|College|Institute|Polytechnic|Academy|School)\b")


def en_thesis_fields(rest: str) -> dict | None:
    """英文学位论文 `rest` → ``{"etype","degree","school"}``；不确定返回 ``None``。

    口径同引用库：`@phdthesis/@mastersthesis` + `school=学校`；学位类型串留在
    ``degree``（EN 条目按引用库**不**写 address，避免臆造出版地）。
    """
    if not rest:
        return None
    m = _EN_THESIS_RE.search(rest)
    if not m:
        return None
    degree = m.group("kind")
    etype = ("mastersthesis"
             if re.search(r"(?i)master|m\.?\s*a\.?\s+thesis|undergraduate|bachelor",
                          degree) else "phdthesis")
    school = ""
    for part in re.split(r"[,，;；.。]", rest[m.end():]):
        p = part.strip(" \t\u3000,，;；.。")
        if _EN_SCHOOL_RE.search(p) and len(p) <= 80:
            school = p
            break
    if not school:
        return None
    # 英文条目引用库口径只有 school（address 是出版地，不拿学位类型顶替，不臆造）。
    return {"etype": etype, "school": school}


# ── 网络资源 URL ──────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s，,。；;、）)\]】>\"'“”‘’「」『』]+")


def find_urls(text: str) -> list[str]:
    """``text`` 里的 http(s) URL（去掉尾部句点/逗号）。"""
    return [u.rstrip(".;,") for u in _URL_RE.findall(text or "") if u.rstrip(".;,")]


def apply_numbered_extras(entry: dict, raw: str, match_journal=None) -> dict:
    """编号清单条目（`doc.bib_entries`）补 B-217② 字段；认不出原样返回（不猜）。

    覆盖模板实测三族：
      · CN 学位论文 `博士论文，厦门大学，2009 年` → `_type` + school + address(=学位类型)；
      · EN 学位论文 `PhD thesis, Northwestern University` → `_type` + school；
      · 英文编著章节 / SEP `in E. N. Zalta (ed.), …, https://…` → booktitle/editor/url。

    只填**空字段**、只改能识别的 `_type`；字段最终能否写进 .bib 由
    `footnotes.entry_to_bib()` 的 `_TYPE_ORDER` 决定（impl-meta 名下）。
    """
    if not isinstance(entry, dict) or not raw:
        return entry
    tf = thesis_fields(raw)
    if tf:
        entry["_type"] = tf["etype"]
        entry.setdefault("school", "")
        entry["school"] = tf["school"]
        entry.setdefault("address", "")
        entry["address"] = tf["degree"]
        return entry
    etf = en_thesis_fields(raw)
    if etf:
        entry["_type"] = etf["etype"]
        entry.setdefault("school", "")
        entry["school"] = etf["school"]
        return entry
    bt, ed = incollection_in_editors(raw)
    if not bt:
        # B-220 N5：`(eds.)` 括注之外的两族编者/书名列表形态（旧判据全落空）。
        # 只把**引号题名之后**的 in 段交给容器判据（题名里自带 in 的误判防线）。
        bt, ed = incollection_editor_list(in_container_tail(raw), allow_c=True)
    _filled_container = False
    if bt and not entry.get("booktitle"):
        entry["booktitle"] = bt
        _filled_container = True
    if ed and not entry.get("editor"):
        entry["editor"] = ed
        _filled_container = True
    if _filled_container:
        # 补到容器字段后类型随证据升级（否则 `entry_to_bib` 按 misc/book 的字段序
        # 丢掉 booktitle/editor——实测 90-09 Dillard/Jackson/Stryker、90-07 Salmon）。
        from . import footref as _footref

        _etype, _why = _footref.classify_entry_type(entry, raw=raw)
        if _etype in ("incollection", "inproceedings") and entry.get("_type") not in (
                "incollection", "inproceedings"):
            entry["_type"] = _etype
    urls = find_urls(raw)
    if urls and not entry.get("url"):
        entry["url"] = urls[0]
    # B-270 A2：编号/APA 路径**写 url 后从 note 删掉同一链接**（旧实现只写 url，
    # 解析层又把同一 URL（含 `Retrieved 日期, URL`）拼进 note，于是 url+note 双写）。
    # 与通用分支 `wordentry.py` 的 `strip_urls(note)` 同口径，但只删 identity 相同者。
    note = entry.get("note")
    if note and entry.get("url"):
        from .bibnorm import _drop_duplicate_urls, _url_identity

        cleaned, dups = _drop_duplicate_urls(str(note), {_url_identity(entry["url"])})
        if dups:
            entry["note"] = cleaned
    # B1：编号清单 `作者, 年, “题名”, 刊名, 卷(期): 页.` —— footref 没解析出 journal
    # 时用同一份期刊正则（调用方传入 `docxsource._match_en_journal`）补，只填空字段。
    if match_journal and not entry.get("journal"):
        for cand in journal_raw_candidates(raw):
            m = match_journal(cand)
            if not m:
                continue
            gd = m.groupdict()
            journal = clean_journal_name(gd.get("journal") or "")
            if not journal:
                continue
            entry["journal"] = journal
            for name, val in (("volume", gd.get("volume")), ("number", gd.get("number"))):
                if val and not entry.get(name):
                    entry[name] = val
            pages = (gd.get("pages") or "").replace("-", "--")
            if pages and not entry.get("pages"):
                entry["pages"] = pages
            if gd.get("year") and not entry.get("year"):
                entry["year"] = gd["year"]
            break
    return entry


def strip_urls(text: str) -> str:
    """删掉 ``text`` 里的 URL 并收拾残留分隔符；没有 URL 时**逐字节原样返回**。"""
    if not text or not _URL_RE.search(text):
        return text
    out = _URL_RE.sub("", text)
    out = re.sub(r"\s*([，,。；;、])\s*", r"\1", out)
    out = re.sub(r"[\s，,、;；]+$", "", out)
    out = re.sub(r"^[\s，,、;；]+", "", out)
    return out.strip()
