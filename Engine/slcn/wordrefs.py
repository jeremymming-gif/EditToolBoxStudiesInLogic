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


def journal_rest_variants(rest: str) -> list[str]:
    """``rest`` 的归一化候选（只**多剥**前导噪声，不改原始串本身）。"""
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
    for part in re.split(r"[，,。.；;、]", tail):
        p = part.strip(" \t\u3000，,。.；;、")
        if not p:
            continue
        if _SCHOOL_HINT_RE.search(p) and len(p) <= 40:
            school = p
            break
    if not school:
        return None
    return {"etype": etype, "degree": degree, "school": school}


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
    if bt and not entry.get("booktitle"):
        entry["booktitle"] = bt
    if ed and not entry.get("editor"):
        entry["editor"] = ed
    urls = find_urls(raw)
    if urls and not entry.get("url"):
        entry["url"] = urls[0]
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
