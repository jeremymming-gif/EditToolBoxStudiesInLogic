r"""把「整条塞进 note」的脚注体例文献拆成结构化条目。

问题背景
--------
脚注里的文献解析不出来时，先前按设计整条原样塞进 `note`。实测全库 244 条
（占 10%），它们其实是三类混杂：

  ① **脚注正文，根本不是文献**
     「针对传统营销的4P理论，4C理论是由美国营销专家劳特朋（R. F. Lauterborn，
       1990）提出的。该理论主张…」← 这是作者的论述，不是参考文献
  ② **一条 note 里塞了多条文献**
     「任晓明,李章吕. 贝叶斯决策理论…[J]. 科学技术哲学研究, 2013(2).
       熊卫. 期望效用理论…[J]. 现代哲学, 2013(5): 82-86」← 两条
  ③ **真文献，格式特殊**
     「Kahneman D., Slovic P., & Tversky A. Judgment under Uncertainty[M]. …」

主要抓手是 **GB/T 7714 的类型标记**：`[J]` 期刊、`[M]` 专著、`[C]` 论文集、
`[D]` 学位论文、`[N]` 报纸、`[Z]` 其他。有标记的就能定位题名与作者。

**不猜**：拆不出来的仍原样保留在 note，只是标注出来让人工处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# GB/T 7714 文献类型标记 → BibLaTeX 类型
TYPE_MARK = {
    "J": "article",
    "M": "book",
    "C": "incollection",
    "D": "phdthesis",
    "N": "misc",
    "Z": "misc",
    "R": "techreport",
    "S": "misc",
    "P": "misc",
    "A": "incollection",
}

# 开头的前缀词，拆之前先剥掉
_PREFIX = re.compile(r"^\s*(?:译自|参见|参阅|见|引自|转引自|参考|参|见前引|同前引)\s*")

# 类型标记：`[J].` / `[M] .` / `[C]//` / `[D].`
_MARK = re.compile(r"\[\s*([JMCDNZRSPA])\s*\]\s*(?:\.|//|／／)?")

# 拆多条文献：在 `[X].` 之后、下一个「作者名 + 年份」之前切开
_SPLIT = re.compile(r"(?<=[.。])\s*(?=[^\s，,。]{2,12}[，,]\s*[^\s，,。]{2,8}\s*[.。]\s*[^\s]{4,40}\[\s*[JMDCZ])\s*")


@dataclass
class Parsed:
    """拆出来的一条。"""

    etype: str = "misc"
    author: str = ""
    title: str = ""
    journal: str = ""
    booktitle: str = ""
    volume: str = ""
    number: str = ""
    pages: str = ""
    publisher: str = ""
    address: str = ""
    year: str = ""
    note: str = ""
    # B-264 v2：APA 书/章节的 `(Rev. ed.)`/`(2nd ed.)` 与 In-eds 编辑者。
    # 这两格只有 APA 分支会填；其它体例留空，`entry_to_bib` 对空值不输出。
    edition: str = ""
    editor: str = ""
    confidence: str = "low"  # 'high' | 'low'


def looks_like_reference(text: str) -> bool:
    """判断这段是不是文献条目（而不是作者的论述）。"""
    t = text.strip()
    if re.search(r"\[\s*[JMDCNDZSPA]\s*\]", t):
        return True
    if re.search(r"\d{4}\s*[.，,]", t) and re.search(r"[“”\"]", t):
        return True
    if re.search(r"[：:]\s*[^，,。]{2,20}(出版社|Press|University|Verlag)", t):
        return True
    # 像论述的：以中文虚词起头 + 多个句号 → 是作者的论述而非文献。
    # 注意**不能**再加「无年份」的条件：实测「针对传统营销的4P理论，4C理论是由
    # 美国营销专家劳特朋（R. F. Lauterborn，1990）提出的。该理论主张…」里有年份，
    # 早先的判据因此把它误判成了文献。
    if re.match(r"^(针对|因此|根据|由于|但是|然而|如果|所谓|这是|该|本文|笔者|我们)", t):
        if len(re.findall(r"[。！？；]", t)) >= 2:
            return False
    # 没有类型标记、又含大量句号的，基本是论述
    if not re.search(r"\[\s*[JMDCNDZSPA]\s*\]", t) and len(re.findall(r"[。！？]", t)) >= 3:
        return False
    return bool(re.search(r"(19|20)\d{2}", t))


def _year(text: str) -> str:
    ys = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
    return ys[-1] if ys else ""


#: 页码前的可选字母前缀（`S362-S378` 这种增刊页码，期 87 sub2 Fitelson 实测）。
_PAGE_NUM = r"[A-Z]?\d{1,5}"


def _pages(text: str) -> str:
    m = re.search(r"[：:]\s*(" + _PAGE_NUM + r")\s*[-–—~]\s*(" + _PAGE_NUM + r")", text)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    m = re.search(r"[，,]\s*(" + _PAGE_NUM + r")\s*[-–—~]\s*(" + _PAGE_NUM + r")\s*[.。]?$", text)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    m = re.search(r"\bpp?\.\s*(\d{1,4})\s*[-–—~]\s*(\d{1,4})", text)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    # 中文体例：`…，第24–43、204–205页。` —— 取第一段（人工成品也是取第一段）
    # B-220 N4c：允许尾部 `+N` 补充页（实测 90-05 费多益条 `第122-143+207页`，
    # 人工成品 pages={122--143}）——取主页码段，`+N` 是补充材料页、不进 pages。
    m = re.search(r"第\s*(\d{1,4})\s*[-–—~]\s*(\d{1,4})\s*(?:[、,，]\s*\d{1,4}\s*[-–—~]\s*\d{1,4})?\s*(?:[+＋]\s*\d{1,4}\s*)?页", text)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    return ""


def _vol_issue(text: str) -> tuple[str, str]:
    # 期号允许非纯数字：`66(Supplement)`（期 87 sub2 Fitelson 实测）。
    m = re.search(r"[，,]\s*(\d{1,4})\s*\(\s*([^)]+?)\s*\)", text)
    if m:
        return m.group(1), re.sub(r"^0+(?=\d)", "", m.group(2).strip())
    m = re.search(r"[，,]\s*(?:Vol\.?\s*)?(\d{1,3})\s*[，,]\s*(?:No\.?\s*)?(\d{1,3})?[：:]", text)
    if m:
        return m.group(1), (m.group(2) or "")
    m = re.search(r"第\s*(\d+)\s*卷", text)
    if m:
        return m.group(1), ""
    # 中文期刊常写「2026年第6期」——期号在「第N期」里，没有卷号
    m = re.search(r"第\s*(\d{1,3})\s*期", text)
    if m:
        # 期号不带前导零（同 vol(issue) 分支与 `第N卷` 的口径；bib 规范要求 `number = {6}`）
        return "", re.sub(r"^0+(?=\d)", "", m.group(1))
    # `No. 10: 14–24`（只有期号、没有卷号）。人工成品的做法是把它记进 volume，
    # 这里照同一口径（实测 09 Jiawei_Zhong 第 15 条：人工 `volume={10}`）。
    m = re.search(r"\bNo\.?\s*(\d{1,3})", text)
    if m:
        return m.group(1), ""
    return "", ""


# 出版社线索词（英文）：只有命中它们才敢把 `X: Y` 当成「出版地: 出版社」。
# 放宽的起因：实测 09 Jiawei_Zhong 的 `Mahwah, NJ: Lawrence Erlbaum Associates`、
# `New York: Peter Lang`、`London: Arnold` 都因为老名单（只认 Press/University/…）
# 而整个丢掉，条目退化成 `@misc`——而 misc 的字段序里没有 publisher/address，
# 信息就此消失，人工成品那边是 `@book` + 出版社。
_PUB_CUES = (
    r"Press|University|Verlag|Books?|Publishing|Publishers?|Associates|"
    r"Lang|Arnold|SAGE|Sage|Erlbaum|Foresman|Harcourt|Pearson|Longman|"
    r"Benjamins|Wiley|Blackwell|Springer|Routledge|Bloomsbury|Palgrave|"
    r"Gruyter|Kluwer|Reidel|Elsevier|Wadsworth|Waveland|Rowman|Parlor|"
    r"McGraw|Macmillan|Scribner|Holt|Rinehart|Winston|Harper|Collins|"
    r"Gollancz|"
    r"出版社|书局|印书馆"
)


#: 出版地候选里不该出现的「题名/机构」词：命中说明冒号左边其实是题名副题或机构，
#: 不是出版地（期 87 sub7 `…Dignaga School: Critique of Indian Realism, Bharatiya…Press`
#: 的 publisher 被错拆成 `Critique…Press`、address 被错拆成 `Dignaga School`）。
_PLACE_REJECT = re.compile(
    r"\b(?:School|Institute|University|Department|Faculty|Center|Centre|Society|"
    r"Association|Journal|Press|Publishing|Studies|Logic|Theory|Conflict|Critique|"
    r"Realism|Analysis|Buddhist|Indian|Philosophy|History|Edition|Vol|No)\b", re.I)


def _looks_like_place(candidate: str) -> bool:
    """这段文字像不像「出版地」（`New York` / `Cambridge, MA` / `London`）。"""
    text = candidate.strip(" ,，.")
    if not text or len(text) > 40:
        return False
    if _PLACE_REJECT.search(text):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text)
    if not 1 <= len(words) <= 4:
        return False
    allowed_lower = {"of", "the", "and", "on", "in", "upon", "de", "la", "van"}
    if any(w[0].islower() and w not in allowed_lower for w in words):
        return False
    return True


def _looks_pub_segment(seg: str) -> bool:
    """这一段是不是出版信息（出版社/年份/期号/页码/`in …`），而不是题名的延续。

    期 87 sub7 实测：`The Philosophy of Nyaya-Vaisesika, and Its Conflict with the
    Buddhist Dignaga School: Critique of Indian Realism, Bharatiya Vidya Prakashan
    Press.` 按逗号切后，题名被拆成 3 段；不并回去，title 只剩前半、后两段还会被
    `_pub_addr` 从冒号处误拆成 publisher/address。
    """
    text = seg.strip().strip(" .。")
    if not text:
        return True
    # B-222 B 段 C（N2 行内句点）：`from URL` / `available at https://…` 是出版信息
    # 残段，不是题名续接（实测 87-8 题名后 `, from URL` 被并进题名）。
    if re.match(r"(?:from|available\s+(?:at|from)|at)\s+"
                r"(?:URL\b|https?://|www\.|doi\b)", text, re.I):
        return True
    if re.search(_PUB_CUES, text):
        return True
    if re.match(r"^(?:1[89]|20)\d{2}\b", text):
        return True
    if re.match(r"^(?:第\s*\d+|pp?\.\s*\d|Vol\.?\s*\d|No\.?\s*\d|in\s|In\s|载\s|见\s)", text):
        return True
    if re.match(r"^[A-Z][A-Za-z.\- ]{1,20}[:：]\s*[A-Z]", text) and "," in text:
        return True
    return False


# ── B-220 N2/N3：英文题名逗号续接 + 多作者（编号/人年制两个通道共用判据）──────
#: 题名续段以这些功能词开头（小写）→ 一定是题名的一部分（`…, and What It Explains`）。
_LOWER_CONNECTIVE_RE = re.compile(
    r"^(?:and|or|of|the|a|an|for|with|from|as|in|on|at|to|by|under|between|"
    r"through|upon|into)\b")
#: 版次/卷期/页码残段——出现在题名之后时说明题名已结束（`2nd ed.` / `Vol. 2`）。
_EDITION_OR_NOISE_RE = re.compile(
    r"^(?:\d+(?:st|nd|rd|th)?\s*(?:ed|edn|edition)\.?\b|"
    r"(?:rev|revised|updated|enlarged|expanded)(?:\s+edition)?\.?\b|"
    r"(?:2nd|1st|3rd|4th|5th|6th|7th|8th|9th)\b|Vol\.?\s*\d|No\.?\s*\d|pp?\.\s*\d)",
    re.I)
#: 纯题名候选段：不含冒号/数字（出版信息几乎总有冒号或数字）。
_PLAIN_TITLE_SEG_RE = re.compile(r"^[^:：\d]+$")


def title_comma_continuation(segments: list[str]) -> int:
    """给「被逗号切开的题名候选段」，返回**应并入题名**的后续段数（0 = 不并）。

    只处理两类**可自证**的续接（B-220 N2；实测 90 期 Ayer1946 / Frankfurt1999 /
    vanEemeren1992 / Sosa2021）：
      ① 下一段以**小写功能词**开头（`and What It Explains`）；
      ② 下一段是**纯题名段**（无数字/冒号），且其后紧跟版次标记或另一小写功能词段。
    两种都要求该段本身不是出版信息（`_looks_pub_segment`）。宁可少并、不可错并：
    `2nd ed.` / `Oxford: OUP` / `New York: …` 等出版信息一律不并入。
    """
    k = 0
    for i in range(1, len(segments)):
        seg = segments[i].strip()
        if not seg:
            break
        if _looks_pub_segment(seg) or _EDITION_OR_NOISE_RE.match(seg):
            break
        nxt = segments[i + 1].strip() if i + 1 < len(segments) else ""
        if seg[:1].islower() and _LOWER_CONNECTIVE_RE.match(seg):
            k = i
            continue
        if _PLAIN_TITLE_SEG_RE.match(seg) and nxt and (
                (nxt[:1].islower() and _LOWER_CONNECTIVE_RE.match(nxt))
                or _EDITION_OR_NOISE_RE.match(nxt)):
            k = i
            continue
        break
    return k


#: 英文作者段的形态：`B. Garssen` / `A. F. Snoeck Henkemans` / `and J. H. M. Wagemans`
#: / `A. L. Becker and K. L. Pike`（编号清单多作者，B-220 N3）。
_EN_AUTHOR_SEG_RE = re.compile(
    r"^(?:and\s+)?(?:(?:van|von|de|der|den|del|di|da|le|la)\s+)?"
    r"(?:[A-Z]\.\s*)+"
    r"(?:[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?)"
    r"(?:\s*(?:,|&|and)\s*(?:[A-Z]\.\s*)*(?:[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?))*$")


def looks_like_en_author_segment(seg: str) -> bool:
    """这一段像不像「英文作者名」（首字母缩写 + 姓）。"""
    s = seg.strip()
    if not s or len(s) > 120 or re.search(r"[\u4e00-\u9fff]", s):
        return False
    return bool(_EN_AUTHOR_SEG_RE.match(s))


def _pub_addr(text: str) -> tuple[str, str]:
    """`出版地: 出版社` 或 `Place: Publisher`。"""
    m = re.search(r"([^，,。:：()（）]{2,28})\s*[：:]\s*([^，,。]{2,48})", text)
    if m:
        a, p = m.group(1).strip(), m.group(2).strip()
        if re.search(r"(出版社|印书馆|书局|出版公司|Press|University|Verlag|Book|Publishing|Wiley|Springer|Routledge|Blackwell)", p):
            return p, a
    m = re.search(r"\b([A-Z][A-Za-z.\- ]{2,24})[:：]\s*([A-Z][A-Za-z&.\- ]{2,36}(?:Press|University|Verlag|Books?|Publishing))", text)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    # 出版地里带逗号（`Mahwah, NJ` / `Thousand Oaks, CA`）或出版社名不含老线索词的，
    # 只要**出版社一侧命中线索词**就认，且要求位于条目末尾。
    # ⚠️ 逗号要同时认半角与全角：无引号分支会把逗号统一成全角（`Mahwah，NJ: …`）。
    m = re.search(
        r"([A-Z][A-Za-z.\- ]{2,20}(?:[,，]\s*[A-Z]{2})?)\s*[:：]\s*([A-Z][^:：]{2,60}?)\s*[.。]?\s*$",
        text,
    )
    if m and re.search(_PUB_CUES, m.group(2)):
        addr, pub = m.group(1).strip(), m.group(2).strip()
        # 出版社一侧若还带逗号（题名/副题混进来），只取最后一个命中出版社线索的段，
        # 且**不猜**出版地（宁缺勿错）。
        if "," in pub or "，" in pub:
            tails = [x.strip() for x in re.split(r"[,，]", pub) if x.strip()]
            if tails and re.search(_PUB_CUES, tails[-1]):
                return tails[-1], ""
        if _looks_like_place(addr):
            return pub, addr
        return pub, ""
    # 只有出版社、没写出版地：`…, Waveland Press.`（可能就在串首）
    m = re.search(r"(?:^|[,，])\s*([A-Z][A-Za-z&.\- ]{2,60}?(?:" + _PUB_CUES + r"))\s*[.。]?\s*$", text)
    if m:
        return m.group(1).strip(), ""
    # 中文「只有出版社」：`商务印书馆，1980年`（无冒号、无出版地）
    m = re.search(r"(?:^|[，,])\s*([\u4e00-\u9fff·]{2,30}(?:出版社|印书馆|书局|出版公司))", text)
    if m:
        return m.group(1).strip(), ""
    return "", ""


# ── B-248：英文姓名列表判据（Word 脚注书目的作者/题名切分兜底）──────────────
#
# 87-9 实测：`Christopher W. Tindale, The Philosophy…` 的第一个 `. ` 落在**中名缩写**
# `W.` 之后，旧实现于是把作者切成 `Christopher W.`、题名切成 `Tindale, The …`（6/8 条）。
# 旧实现**没有**可用的 `. ` 时直接返回 `("", head)`，整条没有作者。
# 这里补两件事：① 拒绝「名 + 中名缩写. + 姓,」这种切点；② 兜底走「完整姓名列表 +
# 逗号」切分（仅在无可用 `. ` 时），并用**保守的姓名形态**判断哪一段还是作者。
_EN_NAME_PARTICLE = r"(?:van|von|de|der|den|del|di|da|le|la)"
_EN_NAME_WORD = r"(?:[A-Z]\.|[A-Z][\w'’\-]+|" + _EN_NAME_PARTICLE + r")"
_EN_NAME_PART = re.compile(
    r"^(?:" + _EN_NAME_PARTICLE + r"\s+)?"
    r"(?:[A-Z]\.\s*)*"
    r"(?:[A-Z][\w'’\-]+|[A-Z])"
    r"(?:\s+" + _EN_NAME_WORD + r")*"
    r"\.?$"
)
#: 段首不能是这些词（题名常见开头，避免把 `The Philosophy of Argument` 当人名）
_EN_NAME_STOP = frozenset(
    "The A An Of In On And For From With Its His Her Their This That These Those".split())


def _looks_like_name_list_segment(seg: str) -> bool:
    """这一段像不像「英文姓名列表」（多作者用 `&`/`and`/`、`/`;` 连接）？

    刻意保守：含中文、过长、单词段（>2 字符）一律不算；每个名字段必须是
    「首字母缩写 + 姓」或「名 + 中名缩写 + 姓」，且词里除 `van/von/de…` 外都得大写。
    """
    s = seg.replace("\\&", "&")
    s = re.sub(r"\s*\(\s*eds?\.?\s*\)\s*$", "", s.strip(), flags=re.I).strip(" ,，")
    if not s or len(s) > 70 or re.search(r"[\u4e00-\u9fff]", s):
        return False
    if re.match(r"(?i)^et\s+al\.?$", s):
        return True                      # `et al` 是作者列表的收尾标记，不是题名
    pieces = [p.strip() for p in re.split(r"\s*(?:&|\band\b|、|;)\s*", s) if p.strip()]
    if not pieces:
        return False
    for piece in pieces:
        toks = piece.split()
        if not 1 <= len(toks) <= 5:
            return False
        if len(toks) == 1 and len(toks[0]) > 2:
            return False                 # 单词段（`Language`/`Logic`）不是完整姓名
        if toks[0] in _EN_NAME_STOP:
            return False
        if re.search(r"[’'']s\b", piece):
            return False                 # 所有格（`Plato’s Reasons`）是题名，不是姓名
        if not _EN_NAME_PART.match(piece):
            return False
    return True


def _has_name_list_marker(seg: str) -> bool:
    """多作者续段的**追加**证据：缩写、复姓介词或 `&/and/、` 连接词。

    单独一个 `Some Book`（两词都大写）可能只是题名；只有这些标记才当作者续段。
    """
    s = re.sub(r"\s*\(\s*eds?\.?\s*\)\s*$", "", seg.strip(), flags=re.I)
    return bool(re.search(r"\b[A-Z]\.", s)
                or re.search(r"(?:^|\s)(?:van|von|de|der|den|del|di|da|le|la)\s", s)
                or re.search(r"\s(?:&|and)\s|、", s))


def _looks_like_surname_continuation(text: str) -> bool:
    """切点后的内容是不是「姓氏 + 逗号/(eds.)/et al」——即在缩写后误切了。

    `Tindale, The …` / `van Eemeren, et al, …` / `Frank (eds.), …` 命中；
    `Judgment under Uncertainty, …`（正常题名）不命中。
    """
    return bool(re.match(
        r"^(?:(?:van|von|de|der|den|del|di|da|le|la)\s+)?[A-Za-z][\w'’\-]*\s*"
        r"(?:[,，]|\(\s*eds?\.?\s*\)\s*[,，]|et\s+al\b)", text))


def _split_author_title_by_comma(head: str) -> tuple[str, str]:
    """兜底：`姓名列表, 题名`（无可用 `. ` 时）。中文体例不在此列（仍走旧路径）。"""
    if re.search(r"[\u4e00-\u9fff]", head):
        return "", head
    parts = [p.strip() for p in re.split(r"\s*[,，]\s*", head)]
    if len(parts) < 2:
        return "", head
    auth: list[str] = []
    i = 0
    while i < len(parts):
        if re.match(r"(?i)^et\s+al\.?$", parts[i]):
            i += 1
            continue                     # `et al` 只作列表收尾，不进 author 字段
        if not _looks_like_name_list_segment(parts[i]):
            break
        if i > 0 and not _has_name_list_marker(parts[i]):
            break                        # 只有「Some Book」形态的多词段：是题名不是作者
        auth.append(parts[i])
        i += 1
    if not auth or i >= len(parts):
        return "", head
    author = re.sub(r"\s*\(\s*eds?\.?\s*\)\s*$", "",
                    ", ".join(auth)).strip(" ,，")
    title = ", ".join(parts[i:]).strip(" ,，")
    if len(author) < 2 or len(title) < 4:
        return "", head
    return author, title


def _split_author_title(head: str, *, comma_fallback: bool = True) -> tuple[str, str]:
    """把 `作者列表. 题名` 切开。

    难点是多作者：`Kahneman D., Slovic P., \\& Tversky A. Judgment under
    Uncertainty`。若按第一个 `. ` 切，作者只剩 `Kahneman D`、其余作者混进题名
    （实测就是这样错的）。

    判据：切点后的「题名」若以 `&`、`\\&`、`,` 开头，说明后面还有作者，继续往后找。
    B-248：切点前是**单大写字母**（中名缩写）且切点后紧接「姓 + 逗号/(eds.)/et al」
    时也跳过——这是 `Christopher W. Tindale, …` 的误切。全部 `. ` 都不可用（或没有）
    时，按「完整姓名列表后的第一个逗号」兜底；中文体例不受影响。
    """
    for m in re.finditer(r"[.。]\s+", head):
        a, t = head[: m.start()].strip(), head[m.end() :].strip()
        if len(a) < 2 or len(t) < 4:
            continue
        if t[0] in "&,，、":          # 后面还有作者
            continue
        if a.endswith((",", "，", "、")):  # 作者列表没写完
            continue
        if len(a) > 90:               # 作者部分过长，多半切错了
            continue
        if re.search(r"(?:^|\s)[A-Z]$", a) and _looks_like_surname_continuation(t):
            continue                  # B-248：`… Christopher W. Tindale, …` 的误切点
        return a, t
    if comma_fallback:
        a, t = _split_author_title_by_comma(head)
        if a:
            return a, t
    return "", head


def _title_quoted(text: str) -> str:
    """取第一个引号里的题名；没有引号（或引号里不够 4 字）返回 ""。

    ⚠️ 中文题名**里面还会嵌套引号**：实测 2026年第1期(86)/01 Yu_Chen
    `“广式早茶说理的功能结构分析——以“詏”为例”`。旧实现取「第一个右引号」，
    于是在 `以“詏` 处截断——不只题名残半句，题名之后那段还从 `詏”为例”` 开始，
    刊名判据（要求以刊名开头）随之落空，整条退化成 `@misc`。

    新判据：取**第一个**「内侧引号成对**平衡**」且**紧跟分隔符**（逗号/句号/条目末尾）
    的右引号——`“詏”` 内层成对、后面的引号才是收尾；不配平就继续往后找。
    一个合条件的候选都找不到时，退回旧行为（第一个右引号，仍要求 4–120 字），
    免得英文 `“题名”[J]` 这类写法把题名整个丢掉。
    """
    om = re.search(r"[“\"]", text)
    if not om:
        return ""
    start = om.end()
    fallback: str | None = None

    def _clean(value: str) -> str:
        # B-220 N1：西式引号把**题名尾逗号写在引号内**（`“Freedom of Will…,”`）——
        # 尾标点是分隔符、不是题名的一部分（旧实现原样返回，印出来是「…Person,」）。
        return value.strip().rstrip(" ,，、;；:：")

    for cm in re.finditer(r"[”\"]", text[start:]):
        inner = text[start : start + cm.start()]
        if fallback is None:
            fallback = inner
        # 内侧引号没配平 → 这个右引号属于嵌套的内层，不是题名的收尾
        if inner.count("“") != inner.count("”") or inner.count('"') % 2:
            continue
        nxt = text[start + cm.end() : start + cm.end() + 1]
        if nxt in ("", "，", ",", "。", ".", "；", ";", "、"):
            cleaned = _clean(inner)
            if 4 <= len(cleaned) <= 120:
                return cleaned
    if fallback is not None:
        cleaned = _clean(fallback)
        if 4 <= len(cleaned) <= 120:
            return cleaned
    return ""


def _invert_en_author(s: str) -> str:
    """英文作者一律倒装成 `姓, 名`（编辑部 2026-10-15 决定；权威实现 `slcn/bibnorm.py`）。

    中/日文姓名与判不准的一律原样返回（见 `bibnorm.normalize_author_field` 的注释）。
    P1-3（2026-10-18）：先走 `footnotes.normalize_authors` 把「逗号 + and」混写、
    `&` 分隔、中文逗号等**拆成 BibTeX `and`**，再整条倒装——否则含逗号的多作者串
    会被倒装器当成「已经倒装」而整条跳过（Word 通道部分条目没倒装的根因之一）。

    ⚠️ **函数内 import**：`bibnorm.expand_note_entries()` 会反过来调本模块的
    `parse_note()`，模块级互相 import 会成环——`bibnorm` 那边也是函数内 import 的同一理由。
    """
    from .bibnorm import normalize_author_field
    from .footnotes import normalize_authors

    return normalize_author_field(normalize_authors(s))


# ── 条目类型：按「出版信息特征」判定（P1-2，2026-10-18）────────────────────
#
# 旧判据以「卷期正则是否命中」为主，实测三类错（期 87 子案例 2/3/7/8/9）：
#   · 书只有「出版地: 出版社」（`Oxford: Clarendon` / `Paramus: Prentice Hall`）
#     → 这一格被当成 journal，条目落成 @misc，出版社整条丢失；
#   · 期刊条目没有页码/卷号 → 落成 @misc，journal 整个丢掉；
#   · `in … (eds.), pp. N` 的析出文献 → 落成 @book（排版样式错）。
# 现在把「出版信息特征」收成一个**可复用**判据：`classify_entry_type(fields, raw=…)`。
# 落点：`footref.parse_numbered_item`（编号清单/脚注体例）与
# `footnotes.parse_entry` 的出口都调用它；下一卡 `docxsource.refs_to_bib`（人年制通道）
# 可直接 import 本函数，签名保持 dict 字段 + 原文，不依赖本模块的 Parsed。
_PLACE_PUB = re.compile(
    r"^\s*([A-Z][A-Za-z.\- ]{1,28}?(?:,\s*[A-Z]{2})?)\s*:\s*"
    r"([A-Z][A-Za-z&.'\- ]{2,60}?)\s*[.。]?\s*$")
#: 期刊线索（英文刊名常见词 + 中文刊名后缀）；只在「没有刊名格」时才当补充证据。
_SERIAL_CUE = re.compile(
    r"\b(?:Journal|Review|Quarterly|Transactions|Proceedings|Bulletin|Annals|Letters|"
    r"Magazine|Zeitschrift|Revue)\b|学报|学刊|研究|评论|论丛", re.I)
#: 会议论文集线索 → @inproceedings（比 @incollection 更具体）
_PROCEEDINGS_CUE = re.compile(
    r"\b(?:Proceedings|Conference|Symposium|Workshop|Congress)\b|会议论文集|会议录|研讨会", re.I)
#: 容器线索：`in X (eds.)` / `（编）` / `主编` / `edited by`（析出文献）
_CONTAINER_CUE = re.compile(
    r"(?:\(eds?\.?\)|\(Eds?\.?\)|（编）|主编|edited\s+by)", re.I)
#: 页码线索：`pp. 1–2` / `(pp. 39–53)` —— 与容器线索同时出现才算 booktitle
_PAGES_CUE = re.compile(r"\(pp?\.\s*\d|\bpp?\.\s*\d", re.I)
#: 「只有出版社、没有刊名线索」的出版社词（`Springer` 被误当刊名的实测来源）
_PUBLISHER_ONLY_CUE = re.compile(
    r"^(?:[A-Z][A-Za-z&.'\- ]{1,40}?(?:Press|Publishing|Books?|Verlag|Publisher))$"
    r"|^(?:Springer|Routledge|Blackwell|Wiley|Elsevier|Palgrave|Macmillan|Pearson|"
    r"Longman|McGraw|Hackett|Rowman|Bloomsbury|SAGE|Sage|Clarendon|Erlbaum)$", re.I)


def looks_like_place_publisher(value: str) -> tuple[str, str] | None:
    """`Oxford: Clarendon` / `Paramus: Prentice Hall` → (出版地, 出版社)。

    只认「**ASCII 首字母大写**的短地名 : 短社名」且不含年份/页码/卷期/期刊线索词；
    不是这种形状（或像刊名）一律返回 None——宁可漏认，不瞎改字段。
    """
    text = (value or "").strip().strip(" .。")
    if not text or len(text) > 70:
        return None
    m = _PLACE_PUB.match(text)
    if not m:
        return None
    if re.search(r"(?:1[89]|20)\d{2}|pp?\.|\d+\s*\(", text):
        return None
    addr, pub = m.group(1).strip(), m.group(2).strip()
    if _SERIAL_CUE.search(pub):
        return None
    return addr, pub


def _publisher_only_journal(value: str) -> bool:
    """`journal` 那一格其实写着出版社（`Springer` / `Routledge` / `… Press`）。"""
    text = (value or "").strip()
    if not text or _SERIAL_CUE.search(text):
        return False
    return bool(_PUBLISHER_ONLY_CUE.match(text))


# ── B-248：无类型标记（misc）出口的出版社/刊名回填 ──────────────────────────
#
# 87-9 的源稿句末形如 `…, Cambridge University Press, 2015.`：出版社后**跟年份**，
# 旧的 `_pub_addr()` 只认「出版社收尾」，因此返回空（evidence.md §2 L2）。这里补一个
# **弱形态**：`… , <Publisher>, YYYY.`，但候选必须命中出版社线索词，且**不得是题名本身**
# （题名含冒号/逗号/尾部年份的形态一律不认，宁缺勿错）。
_PUB_WEAK_TAIL = re.compile(
    r"[,，]\s*((?:[Tt]he\s+)?(?:"
    r"[A-Z][A-Za-z&.'\- ]{2,48}?(?:Press|University|Verlag|Books?|Publishing|Publishers?|"
    r"Academic\s+Pub)"
    r"|(?:Springer|Routledge|Blackwell|Wiley|Elsevier|Palgrave|Macmillan|Pearson|Longman|"
    r"McGraw|Hackett|Rowman|Bloomsbury|SAGE|Sage|Clarendon|Brill)\b[A-Za-z&.'\- ]{0,48}"
    r"))\s*(?:[,，]\s*(?:19|20)\d{2}\s*[.。]?)?\s*$"
)


def _misc_pub_addr(text: str, title: str) -> tuple[str, str]:
    """misc 条目的出版社/出版地抽取（弱形态保护）。认不出返回 `("", "")`。

    候选按「`_pub_addr` 强形态 → 弱形态（出版社后跟年份 / 只有社名）」顺序试，
    **逐个**过弱形态保护：候选不得是题名（题名含冒号/逗号时 `_pub_addr` 可能把题名
    后半截当出版社，实测 87-9 ref06/ref08），也不得含年份。
    """
    cands: list[tuple[str, str]] = []
    pub, addr = _pub_addr(text)
    if pub:
        cands.append((pub, addr))
    m = _PUB_WEAK_TAIL.search(text)
    if m:
        cands.append((m.group(1).strip(), ""))
    tl = (title or "").strip().rstrip(" .。")
    tl = re.sub(r"[,，]\s*(?:19|20)\d{2}\s*[.。]?$", "", tl).strip(" ,，")
    for pub, addr in cands:
        if not pub or len(pub) < 3 or re.search(r"(?:19|20)\d{2}", pub):
            continue
        if tl and pub.lower() in tl.lower() and not tl.lower().endswith(pub.lower()):
            # 出现在题名中间 → 是题名里的词，不认
            continue
        return re.sub(r"^[Tt]he\s+", "", pub), addr
    return "", ""


def _strip_title_pub(title: str, pub: str) -> str:
    """把误并进题名的「, 出版社, 年份」后缀剪掉（B-248）。"""
    t = (title or "").strip()
    if not t or not pub:
        return title
    idx = t.lower().rfind(pub.lower())
    if idx > 0:
        t = t[:idx].rstrip(" ,，")
    t = re.sub(r"[,，]\s*(?:19|20)\d{2}\s*[.。]?\s*$", "", t).strip(" ,，")
    # 弱形态允许社名前带 `the`（`the University of South Carolina Press`）；剪题名时
    # 社名已按去掉 `the` 后的串匹配，这里再把残留的连接词剪掉。
    t = re.sub(r"[,，]?\s*[Tt]he\s*$", "", t).strip(" ,，")
    return t if len(t) >= 4 else title


def _vol_no_misc(text: str) -> tuple[str, str]:
    """`Vol. 1, No. 1` → `("1", "1")`（misc 出口用；`_vol_issue` 的 `No.` 兜底会记进 volume）。"""
    m = re.search(r"\bVol\.?\s*(\d{1,4})\s*[,，]\s*(?:No\.?\s*)?(\d{1,4})", text)
    if m:
        return m.group(1), re.sub(r"^0+(?=\d)", "", m.group(2))
    return _vol_issue(text)


def _has_container(fields: dict, raw: str = "") -> bool:
    """是不是「析出文献」（有容器书/论文集线索）。

    判据：`booktitle` 非空；或文中有 `(eds.)/（编）/主编/edited by` 且同时有
    页码线索/出版社/`in` 结构——只凭一个 `(eds.)` 不升级（可能只是注释）。

    B-222 B 段 B1/B2：`(ed.)/(eds.)` **只在 `in …` 容器段里**算线索。作者串里的
    编著标记（`Salerno (ed.), New Essays…` = 编著整书的作者位置）不是容器，
    否则整书被误判 @incollection（实测 90-01/90-08）。
    """
    if str(fields.get("booktitle") or "").strip():
        return True
    blob = " ".join(str(fields.get(k) or "") for k in ("address", "note", "publisher", "journal"))
    # 容器线索只看第一个 `in …` 之后；题名自带 in 时，其后的真容器段仍在区域内。
    in_tail = re.search(r"\b[Ii]n\s+", raw or "")
    cue_region = (raw or "")[in_tail.start():] if in_tail else ""
    cue_blob = f"{blob} {cue_region}".strip()
    if not _CONTAINER_CUE.search(cue_blob):
        return False
    return bool(_PAGES_CUE.search(blob) or _PAGES_CUE.search(raw or "")
                or str(fields.get("publisher") or "").strip()
                or re.search(r"\bin\s+", cue_blob, re.I))


#: SEP 词条判据（B-262）：booktitle/journal 命中 → incollection。
#: 兼容既有模板例外所认的宽口径 `Encyclopedia of Philosophy`（含 Stanford 限定）。
_SEP_CUE = re.compile(r"encyclopedia\s+of\s+philosophy", re.I)


def _sep_container(fields: dict) -> str:
    """返回命中的容器串（booktitle 优先，其次 journaltitle/journal）；未命中返回 ""。"""
    for key in ("booktitle", "journaltitle", "journal"):
        v = re.sub(r"[{}]", "", str(fields.get(key) or ""))
        if v and _SEP_CUE.search(v):
            return v
    return ""


def classify_entry_type(fields: dict | None = None, *, raw: str = "") -> tuple[str, str]:
    """按「出版信息特征」判定 BibTeX 类型，返回 `(etype, 依据)`。

    可复用（下一卡 `docxsource.refs_to_bib` 直接调用）：只读 `fields` 字典与原文，不碰解析器。
    优先级（前者更具体）：
      ① 会议论文集证据 → `inproceedings`
      ② 容器线索（`booktitle` / `in … (eds.)` + 页码）→ `incollection`
      ③ 刊名格（含「刊名格其实是出版社」的两种情况）→ `book` / `article`
      ④ 出版社 → `book`
      ⑤ 只有刊名线索词 + 页码/卷 → `article`
      ⑥ 其余 → `misc`（不猜）
    """
    f = fields or {}
    journal = str(f.get("journal") or "").strip()
    booktitle = str(f.get("booktitle") or "").strip()
    publisher = str(f.get("publisher") or "").strip()
    address = str(f.get("address") or "").strip()
    pages = str(f.get("pages") or "").strip()
    volume = str(f.get("volume") or "").strip()
    blob = " ".join(x for x in (journal, booktitle, address, publisher,
                                str(f.get("note") or ""), raw) if x)
    place_pub = looks_like_place_publisher(journal) if not publisher else None
    if place_pub:
        return "book", "刊名格实为『出版地: 出版社』"
    if _publisher_only_journal(journal) and not publisher:
        return "book", "刊名格实为出版社"
    if _PROCEEDINGS_CUE.search(blob) and (_has_container(f, raw) or publisher):
        return "inproceedings", "会议论文集线索"
    if booktitle or _has_container(f, raw):
        return "incollection", "容器（析出文献）线索"
    if journal:
        return "article", "有刊名"
    if publisher:
        return "book", "有出版社/出版地"
    if volume and pages:
        # 旧判据 `p.pages and v`（期 87 sub2 Taroni2021 实测）：刊名含逗号时解析器
        # 抓不到 journal，但卷号+页码齐全 → 仍是期刊文章（不能因此退化成 misc）。
        return "article", "有卷号+页码（刊名未捕获）"
    if _SERIAL_CUE.search(blob) and (pages or volume):
        return "article", "刊名线索 + 页码/卷"
    note = str(f.get("note") or "")
    if (re.search(r"\bVol\.?\s*\d|\bNo\.?\s*\d", note)
            and re.search(r"\bpp?\.\s*\d", note)):
        # 期 87 sub9 实测：编号清单的卷期页落在 note 里（`Argumentation, Vol. 37,
        # No. 3, 2003, pp. 341–376`），刊名没结构化 → 仍是期刊文章。
        return "article", "note 里有卷/期/页（刊名未结构化）"
    return "misc", "出版信息不足"


def refine_entry_fields(fields: dict, *, raw: str = "") -> dict:
    """按出版信息特征订正条目（供 `footnotes.parse_entry` 的出口调用）。

    · 刊名格里写成「出版地: 出版社」/ 出版社名的 → 挪到 `address`/`publisher`，类型按书；
    · 类型重判交给 `classify_entry_type()`；**只改 `_type`（与必要的字段挪位），其余字段不动**。
    """
    out = dict(fields or {})
    journal = str(out.get("journal") or "").strip()
    if journal and not out.get("publisher"):
        place_pub = looks_like_place_publisher(journal)
        if place_pub:
            out["address"], out["publisher"] = place_pub
            del out["journal"]
        elif _publisher_only_journal(journal):
            out["publisher"] = journal
            del out["journal"]
    # B-262：SEP 词条（booktitle/journal 含 `Encyclopedia of Philosophy`）判 @incollection；
    # 落在 journal 的容器名要挪到 booktitle，否则 incollection 驱动不排印它。
    if _sep_container(out) and not str(out.get("booktitle") or "").strip():
        for _k in ("journaltitle", "journal"):
            if str(out.get(_k) or "").strip():
                out["booktitle"] = out.pop(_k)
                break
    etype, _reason = classify_entry_type(out, raw=raw)
    if _sep_container(out):
        etype = "incollection"
    out["_type"] = etype
    return out

#
# 《排校手册》源码 `reference.bib` 的实例：`黑格尔（著），杨一之（译）`、
# `焦循（著），沈文倬（点校）`。这里的**中文逗号不是**「作者 → 题名」的分界，
# 而是「著者 → 译者」的分界；顿号也不是多作者分隔符，而是「姓名的一部分」。
#
# 为什么不识别就会坏（实测 `2026年第5期(90)/01 Meimei_Han` 第 16 条）：
# `[16] 阿斯海姆（著），郑建国、万林（译），指称与意向性，南京：南京大学出版社，2014年。`
# 按中文逗号切之后，`郑建国、万林（译）` 被当成了**题名** ——
# 条目于是既丢掉真题名（`指称与意向性`），又多出一个假题名。见第 64 条。
_ROLE_SRC = re.compile(r"[（(](?:著|原著)[)）]\s*$")
_ROLE_TRANS = re.compile(
    r"[（(](?:译|译者|点校|校注|校|编译|注释|整理|整理者|译注)[)）]\s*$"
)


def _translation_author_segments(parts: list[str]) -> int:
    """前 N 段合起来是「作者」段，N ∈ {1, 2}（不是著/译体例时就是 1）。

    只在形态**完整**时才认两段（刻意保守，宁可漏认也不改错）：
      · `parts[0]` 以「（著）」结尾 **且** `parts[1]` 以「（译）/（点校）…」结尾 → 2；
      · 其余一切情况 → 1（照旧把 `parts[0]` 当作者）。

    反例（必须**不**命中，见 `tests/test_authornorm.py`）：
      · `付敏，亚相容解悖方案研究，2021年，…` —— 两段都没有角色标记；
      · `黑格尔（著），逻辑学，北京：商务印书馆，2004年。` —— 第二段是题名；
      · `梅文（译），题名，…` —— 只有译者标记（作者段仍是 1 段）。
    """
    if len(parts) >= 2 and _ROLE_SRC.search(parts[0]) and _ROLE_TRANS.search(parts[1]):
        return 2
    return 1


def parse_one(text: str) -> Parsed:
    """拆一条（已确保是单条）文献。"""
    t = _PREFIX.sub("", text.strip()).strip()
    p = Parsed()

    # 类型标记定位题名结尾
    marks = list(_MARK.finditer(t))
    if marks:
        mk = marks[0]
        p.etype = TYPE_MARK.get(mk.group(1).upper(), "misc")
        head, tail = t[: mk.start()].strip(), t[mk.end() :].strip()
    else:
        head, tail = t, ""

    # 作者与题名：`作者. 题名` / `作者, 题名`
    q = _title_quoted(head)
    if q:
        p.title = q
        p.author = re.split(r"[，,]", head[: head.find(q)])[0].strip(" .。，,")
    else:
        # B-248：逗号兜底只开给**无类型标记**的条目（有 `[J]/[M]` 的既有行为不变）
        p.author, p.title = _split_author_title(head, comma_fallback=not marks)
    p.author = re.sub(r"\\&", "&", p.author).strip(" .。，,")
    p.author = _invert_en_author(p.author)
    p.title = re.sub(r"\\&", "&", p.title).strip(" .。，,")

    body = f"{head} {tail}"
    p.year = _year(t)
    p.pages = _pages(body)

    if p.etype == "article":
        # `刊名, 年(期): 页` 或 `刊名, 年, 卷(期): 页`
        m = re.search(r"[.。]\s*([^，,。\[\]]{2,60}?)\s*[，,]\s*(?:(?:19|20)\d{2})?\s*[，,]?\s*(\d{1,3})?\s*\(([^)]{1,12})\)", tail)
        if m:
            p.journal = m.group(1).strip()
            if m.group(2):
                p.volume = m.group(2)
            p.number = re.sub(r"^0+(?=\d)", "", m.group(3).strip())
        else:
            m = re.search(r"^[.。]?\s*([^，,。\[\]]{2,60})", tail)
            if m:
                p.journal = m.group(1).strip()
        v, n = _vol_issue(body)
        p.volume = p.volume or v
        p.number = p.number or n
    elif p.etype in ("book", "incollection"):
        m = re.search(r"\bin\s+([^，,。]{4,90})", tail) or re.search(r"\bin\s+([^，,。]{4,90})", head)
        if m:
            p.booktitle = m.group(1).strip()
            if p.etype == "book":
                p.etype = "incollection"
        p.publisher, p.address = _pub_addr(tail or t)
        if not p.publisher:
            m = re.search(r"[.。]\s*([A-Z][A-Za-z&.\- ]{3,40}(?:Press|University|Verlag|Books?|Publishing))", t)
            if m:
                p.publisher = m.group(1).strip()

    # B-248：**无类型标记**的 Word 脚注书目也要回填出版社/刊名并重判类型。
    # 旧实现只在 `[J]/[M]` 分支里抽出版信息，misc 条目因此永远丢出版社、永远 @misc
    # （87-9 的 8 条全中）。这里只对「无标记 + 非中文」动手：
    #   · 中文体例（`作者，题名，出版地：出版社，年`）走别的通道，本函数不改判；
    #   · 有 `[J]/[M]` 等标记的条目保持既有行为不变。
    if p.etype == "misc" and not marks and not re.search(r"[\u4e00-\u9fff]", t):
        after = t
        if p.title and p.title in t:
            after = t[t.index(p.title) + len(p.title):]
        if not p.publisher:
            pub, addr = _misc_pub_addr(t, p.title)
            if pub:
                p.publisher = pub
                p.address = p.address or addr
                p.title = _strip_title_pub(p.title, pub)
        if not p.publisher:
            # 期刊形态：题名之后紧跟「刊名, Vol./No./YYYY/第…」（Bitzer 引号条实测）
            m = re.match(
                r"^[\s”\"'’。,，.。]*([^，,。\d\[\]]{3,70}?)\s*[，,]\s*"
                r"(?:Vol\.?|No\.?|(?:19|20)\d{2}|第)", after)
            if m:
                p.journal = m.group(1).strip()
                v, n = _vol_no_misc(after)
                p.volume, p.number = p.volume or v, p.number or n
        _etype, _reason = classify_entry_type({
            "journal": p.journal, "booktitle": p.booktitle, "publisher": p.publisher,
            "address": p.address, "pages": p.pages, "volume": p.volume,
            "number": p.number, "note": p.note,
        }, raw=t)
        if _etype in ("book", "article", "incollection", "inproceedings"):
            p.etype = _etype

    got = sum(bool(x) for x in (p.author, p.title, p.journal, p.booktitle, p.publisher))
    p.confidence = "high" if got >= 3 else ("low" if got >= 2 else "low")
    return p


def parse_note(text: str) -> list[Parsed]:
    """拆一条 note，可能得到多条；不是文献则返回空列表。"""
    t = text.strip()
    if not t:
        return []
    if not looks_like_reference(t):
        return []

    # 多条文献：一条 note 里可能塞了好几篇（实测 2023_03_Ziming_Song 就是）。
    # 以类型标记为锚点：第 i 条的结束位置落在「标记之后的第一个句号」处。
    parts = [t]
    marks = list(_MARK.finditer(t))
    if len(marks) > 1:
        cuts = []
        for i, m in enumerate(marks[:-1]):
            # 两个类型标记之间的文字长这样：
            #   `. 科学技术哲学研究, 2013(2). 熊卫. 期望效用理论的两个悖论及其消解`
            # 下一条的作者紧跟在最后一个 `. ` 之后、且后面还有 `. 题名`。
            # 据此定位切点，才能把 `熊卫. 期望效用…` 整条留给下一部分。
            seg = t[m.end() : marks[i + 1].start()]
            m2 = re.search(
                r"[.。]\s*(?=[^\s，,。\[\]]{2,16}\s*[.。]\s*[^\[\]]{4,}$)", seg
            )
            if m2:
                cuts.append(m.end() + m2.start())
        if cuts:
            parts, prev = [], 0
            for c in cuts:
                if t[prev:c].strip():
                    parts.append(t[prev:c])
                prev = c
            if t[prev:].strip():
                parts.append(t[prev:])

    out = []
    for seg in parts:
        seg = seg.strip(" .。;；")
        if len(seg) < 12:
            continue
        p = parse_one(seg)
        if p.title or p.author:
            out.append(p)
    return out


# ── 第三种格式：编号清单 `[N] 作者, 年份, "题名", 刊名, 卷(期): 页` ──────────
#
# 实测 02 Lili_Zhao 的 `附件一 完整参考文献.docx`：
#     [1] Jc Beall, 2000, "Fitch' Proof, Verificationism, and the Knower Paradox",
#         Australasian Journal of Philosophy, 78(2): 241-247.
#     [8] 付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。
#
# 与已支持的 GB/T 7714（`作者. 题名[J]. 刊名, 年(期): 页`）**顺序不同**：
# 这里是「作者, 年份, 题名, ...」，年份在题名**之前**。

_NUM_ITEM = re.compile(r"^\s*[\[\(（]?\s*(\d{1,3})\s*[\]\)）]?\s*[.、]?\s*", re.M)


_BAD_MARKER = re.compile(r"(?m)^(\s*)\[(\d{1,3})(?=[ \t]+[^\s\[\]()（）])")


def _normalize_numbered_markers(text: str) -> str:
    """补全漏写的 `]`：`[13 J. Ross…` → `[13] J. Ross…`。

    实测期 90/05 Xiaoyu_Lin：源稿第 13 条写成 `[13 J. Ross, …`（缺右括号），
    旧正则按 `[13]` 切分只认到 22 条，正文 `[13]` 因此越界、清单少 1 条。
    只认「行首 `[数字` + 空格 + 非括号字符」，正常 `[13]` 不会命中。
    """
    return _BAD_MARKER.sub(lambda m: f"{m.group(1)}[{m.group(2)}] ", text)


def split_numbered_raw(text: str) -> list[tuple[str, str]]:
    """把编号清单拆成条目，并**一并带出原文**：`[(整条原文含 [N] 标号, 解析用段), …]`。

    为什么要多返回「原文」（2026-10-15，第 68 条）：解析器只要丢**一个**字段，任务书与
    AI 预跑拿到的就是残缺件，而模型/体检随后会「如实报告我们丢的信息」，看上去像 AI 不行。
    实测第 66 条 `[17] 党学哲，…，湖南科技大学学报（社会科学版），…` 丢了刊名，AI 只能回
    「源条目为 @misc，缺期刊信息」——所以我们把每条**源稿原文**一起交出去（原文里没有的
    信息仍然不许编，见 `refmark.SPEC_MD`）。

    ⚠️ 切分口径**与 `split_numbered` 完全同源**（同一个正则、同一个 `len(seg) > 12` 过滤）：
    两套切分迟早分叉，那时「这条原文」和「这条条目」就对不上了。`split_numbered` 是它的
    薄包装，只为兼容旧调用方（返回值形状不变）。
    """
    text = _normalize_numbered_markers(text)
    marks = list(re.finditer(r"(?:^|\n)\s*[\[\(（]\s*(\d{1,3})\s*[\]\)）]", text))
    if len(marks) < 2:
        return []
    out: list[tuple[str, str]] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end() : end].strip()
        if len(seg) > 12:
            # 原文从**标号本身**开始取（`[17] 党学哲，…`），开头的换行/空白由 strip 去掉；
            # 条目跨行时内部仍带 `\n`，展示前由 refmark 压成一行。
            out.append((text[m.start() : end].strip(), seg))
    return out


def split_numbered(text: str) -> list[str]:
    """把编号清单拆成条目。`[1] … [2] …`（原文由 `split_numbered_raw` 给）"""
    return [seg for _raw, seg in split_numbered_raw(text)]


# ── 中文期刊体例：`刊名，YYYY年第N期`（刊名在**年份之前**）──────────────────
#
# 为什么单列一条规则（第 65 条）：中文刊名常带**括号后缀**
# （`湖南科技大学学报（社会科学版）`、`山东科技大学学报(社会科学版)`、
# `浙江大学学报（人文社会科学版）`），也可能写成 `《逻辑学研究》2025年第4期`
# （书名号 + 刊名与年份之间没有逗号）。旧 m2 判据要求「纯汉字刊名 + 逗号 + 年/期」，
# 这几类全部落空 → `journal` 空 → 条目退化成 `@misc`；而 misc 的字段序里**没有**
# journal，刊名就此整个消失。实测 2026年第5期(90)/01 Meimei_Han 第 17 条
# `党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。`
# 就是这一条（author/title/number/pages/year 全对，只丢了刊名、类型也错成 misc）。
#
# ⚠️ 判据**只锚在「YYYY年第N期」上**，从锚点向**前**回退到上一个逗号取刊名：
#   · 「刊名，第N期」（不写年份）与「刊名，YYYY年，第N期」**不认**——那时刊名那一格
#     与题名无法区分（实测 `张三，题名，2020年，第3期，第1-5页。` 会把 `题名` 当刊名），
#     宁可不认：照旧留在原样，由 refmark 交给编辑的 AI 处理（**不猜**）。
#   · 锚点后面必须紧跟 `，`/`。`/`；`/条目末尾——全库 1248 条编号清单实测，
#     凡命中「YYYY年第N期」的锚点无一例外。
_CN_ISSUE_ANCHOR = re.compile(
    r"((?:19|20)\d{2})\s*年\s*第\s*(\d{1,3})\s*期\s*(?=[，,。.；;]|$)"
)

# 刊名候选里**不能**出现的东西：出了这些就不是刊名，而是出版社/学位论文/著译者标记。
# 这是「只在清楚证据上开火」的第二道闸——宁可漏认，也不把书或论文误判成期刊。
_CN_JOURNAL_NOT = re.compile(
    r"[:：]|出版社|出版公司|出版集团|学位论文|硕士|博士|"
    r"（著）|（译）|\(著\)|\(译\)|^载|^见|^收入"
)


def _cn_journal_before_year(t: str) -> tuple[str, str]:
    """`作者，“题名”，刊名，YYYY年第N期，…` → (刊名, 期号)；认不出返回 `("", "")`。

    刊名取「锚点前一个逗号之后」的那一段；`《刊名》YYYY年第N期` 这种没有逗号的
    按书名号取。锚点一律在**整条** `t` 上找，而不是在题名之后的 `rest` 上找——
    题名里带嵌套引号（`…以“詏”为例”`）时 `rest` 会以引号残渣开头，只有不依赖
    题名怎么切，这类条目才救得回来（`_title_quoted` 的注释）。
    """
    for m in _CN_ISSUE_ANCHOR.finditer(t):
        head = t[: m.start()].rstrip(" \t，,。.")
        if not head:
            continue
        mm = re.search(r"[《〈]([^《〈》〉]{2,40})[》〉]\s*$", head)
        if mm:
            cand = mm.group(1).strip()
        else:
            cand = re.split(r"[，,]", head)[-1].strip(" \t。.，,、")
        if not 2 <= len(cand) <= 40:
            continue
        if not re.search(r"[\u4e00-\u9fff]", cand):      # 必须是中文刊名
            continue
        if re.search(r"\d", cand):                       # 刊名里没有阿拉伯数字
            continue
        if re.search(r"[“”\"'‘’\[\]]", cand):            # 引号/方括号残留 → 是题名不是刊名
            continue
        if _CN_JOURNAL_NOT.search(cand):
            continue
        return cand, re.sub(r"^0+(?=\d)", "", m.group(2))
    return "", ""


# ── P0-4（期 87 sub8 实测）/ B-264 v2：APA 体例编号清单 ────────────────────
#   源稿：`Bailin, S., Case, R., Coombs, J. R., & Daniels, L. B. (1999a).
#          Common misconceptions of critical thinking. Journal of Curriculum
#          Studies, 31(3), 269–283.`
#   旧实现无引号分支按逗号切 → author=Bailin、title=S.，随后被 `len(title)<4`
#   整条丢弃（26 条里丢 5 条、且无 warning）。APA 有**可判定的固定形状**：
#   `作者段 (YYYY[a-z]?). 题名. 出版信息`，单独成一条分支最安全。
#
#   B-264 v2（期 87 sub9 实测 26 条）补齐的是**出版尾**——旧实现只认期刊尾 +
#   一张窄出版社线索词表，书/章节/在线词典全掉 misc 或被吞进题名：
#     · 书：`Place: Publisher` 一律认（Heath / Henry Holt / Insight Assessment
#       这类旧词表外的出版社；`Windsor, ON, Canada` 这种「城市, 州, 国家」双逗号
#       地名也认），`(Vol. N)`→volume、`(Rev. ed.)`→edition；
#     · 章节：`In <编辑者> (Eds.), <书名> (pp. X–Y). [Place: Publisher.]` →
#       @incollection（编辑者「首字母 姓」倒装成「姓, 首字母」）；
#     · 在线词典/百科：`In <词典>.`、`In <Eds.>, <词典> Online. <机构>. <URL>`
#       → @article，刊名=词典名（与 87-09 发布 bib 的 c15–c17 同口径）；
#       SEP 词条（booktitle 含 encyclopedia of philosophy）仍是 @incollection；
#     · `(Winter 2022 ed.)` 这类**季节性版本**不进 edition，落 note（发布 bib 即如此）；
#     · `Retrieved … from URL` / 裸 URL → note；机构句保留，URL 字段另由
#       `apply_numbered_extras` 的 `find_urls` 兜；
#     · 括号年扩到 18xx（1896 这类老文献），`n.d.` 与**无括号裸年**（`I. 1960.`）
#       也认；年份后缀 `1999a/b` 剥掉（BibTeX year 只要 4 位，发布 bib c3/c4 即 1999）；
#     · 类型只落 book/article/incollection/report；**书/章节/报告绝不落 misc**——
#       真解析不出才 misc，由交付层 `footnotes.entry_to_bib` 转「源稿原文逐字进
#       author」的 @misc（B-262 渲染，misc 驱动原样排印，编辑可据此人工订正）。
_APA_YEAR = re.compile(
    r"\(\s*(?P<year>(?:1[5-9]|20)\d{2})(?P<suffix>[a-z])?\s*\)\s*[.。]?\s*")
_APA_ND = re.compile(r"\(\s*n\.?\s*d\.?\s*\)\s*[.。]?\s*", re.I)
_APA_BARE_YEAR = re.compile(
    r"^(?P<pre>.+?[A-Z]\.)\s+(?P<year>(?:1[5-9]|20)\d{2})\.\s+(?P<after>\S.*)$")
_APA_INITIALS = re.compile(r"^(?:[A-Z]\.\s*)+$")
#: 期刊尾：`题名. 刊名, 卷(期), 页.`——刊名允许冒号/长名；题名允许以 `”`/`"` 收尾。
_APA_TAIL = re.compile(
    r"^(?P<title>.+?[.?!。？！][”\"'’]?)\s+"
    r"(?P<journal>[^,]{1,200}?)\s*,\s*"
    r"(?P<volume>\d{1,4})(?:\s*\((?P<number>[^)）]+)\))?\s*,\s*"
    r"(?P<pages>[A-Za-z]?\d{1,5}\s*[-–—]\s*[A-Za-z]?\d{1,5})[.。]?\s*$")
#: 章节：`题名. In <编辑者> (Eds.), <书名> …`
_APA_IN = re.compile(r"^(?P<title>.+?[.?!。？！][”\"'’]?)\s+In\s+(?P<rest>\S.*)$")
_APA_EDS = re.compile(r"^(?P<eds>.+?)\s*\((?:Eds?|eds?)\.\)\s*,\s*(?P<rest>\S.*)$")
_APA_PAREN = re.compile(r"\(([^()]{1,90})\)")
_APA_PAGES_PAREN = re.compile(
    r"^pp?\.\s*(?P<pages>[A-Za-z]?\d{1,5}\s*[-–—]\s*[A-Za-z]?\d{1,5})$")
_APA_VOL_PAREN = re.compile(r"^Vol\.?\s*(?P<volume>\d{1,4})$", re.I)
_APA_ED_PAREN = re.compile(
    r"^(?P<ed>Revised|Rev\.?|\d{1,2}(?:st|nd|rd|th))\s+ed\.?$", re.I)
_APA_SEASON_ED_PAREN = re.compile(
    r"^(?:Winter|Spring|Summer|Fall|Autumn)\s+\d{4}\s+(?:ed\.?|Edition)$", re.I)
_APA_REPORT_CUE = re.compile(r"\(\s*Report\s+No\.|Technical\s+[Rr]eport")
#: 在线参考工具（词典/百科）：`In <词典>` 无页码/版本时按 @article + 刊名=词典名。
_APA_REFWORK = re.compile(r"\b(?:Dictionary|Encyclopedia|Encyclopaedia)\b|\bOnline\b", re.I)
#: 尾部句子边界（`. ` / `? ` / `! `）——题名/书名与出版尾的分界候选。
_APA_SENT_END = re.compile(r"(?<=[.?!])\s+")


def _apa_pub_ok(addr: str, pub: str) -> bool:
    """出版地/出版者像不像——紧口径与宽松口径共用的一道否决（B-264 v2）。

    否决形状：出版地里有「句点后接空格」（`Speech acts . New York` 是把副题当出版地）；
    地址/出版者里有年份、`pp.`、数字括注（那是卷期/页码不是出版信息）；
    出版者命中刊名线索词（那是期刊不是出版社）。
    """
    if not addr or not pub:
        return False
    if re.search(r"\.\s|\s\.", addr):
        return False
    if re.search(r"(?:1[5-9]|20)\d{2}|\bpp?\.|\d\s*[\(（]", addr + " " + pub):
        return False
    if _SERIAL_CUE.search(pub):
        return False
    return True


def _apa_place_publisher(tail: str) -> tuple[str, str] | None:
    """`Indianapolis, IN: Hackett Publishing` → (Indianapolis, IN, Hackett Publishing)。

    先走 `looks_like_place_publisher()`（B-248 的紧口径），再造一条**宽松**口径兜
    「Windsor, ON, Canada」这种双逗号地名（`_PLACE_PUB` 只允许「城市, 州」一个逗号点）。
    宽松口径加两道保险：出版地像地名（大写开头、无年份/pp/数字括注、句点后不接空格），
    出版者像机构名（大写开头、不含刊名线索词）——宁可认不出，不把副题当出版地。
    """
    got = looks_like_place_publisher(tail)
    if got and _apa_pub_ok(*got):
        return got
    s = tail.strip().rstrip(".。 ")
    m = re.match(r"^(?P<addr>[A-Z][^:；;]{1,58}?)\s*:\s*(?P<pub>[A-Z][^:；;]{2,88}?)\s*$", s)
    if not m:
        return None
    addr = m.group("addr").strip().rstrip(",; ")
    pub = m.group("pub").strip().rstrip(".,; ")
    if not _apa_pub_ok(addr, pub):
        return None
    return addr, pub


def _apa_editors(text: str) -> str:
    """`B. Akpan & T. Kennedy` → `Akpan, B. and Kennedy, T.`（编辑者倒装）。

    APA 的 In-eds 编辑者写「首字母 姓」，BibTeX 要「姓, 首字母」。规则：
      · 「首字母 姓」→ 首字母之后的整体当姓（多词姓 `T. Northcote Toller` 正确）；
      · 先写全名再缩写（`Stephen P. Norris`）→ 末词当姓、前面整体当名。
    """
    out: list[str] = []
    for chunk in re.split(r"\s*(?:&|\band\b)\s*|\s*,\s*(?=[A-Z])", text or ""):
        chunk = chunk.strip().strip(",;")
        if not chunk:
            continue
        tokens = chunk.split()
        lead = 0
        while lead < len(tokens) and re.fullmatch(r"[A-Z]\.", tokens[lead]):
            lead += 1
        if lead and lead < len(tokens):
            out.append(f"{' '.join(tokens[lead:])}, {' '.join(tokens[:lead])}")
        elif len(tokens) >= 2:
            out.append(f"{tokens[-1]}, {' '.join(tokens[:-1])}")
        else:
            out.append(chunk)
    return " and ".join(out)


def _apa_split_retrieved(after: str) -> tuple[str, str]:
    """把 `… Retrieved October 30, 2025, from https://…` 拆成 (正文, note)。

    `Retrieved … from` 的 `from` 去掉（发布 bib 的 note 就是「Retrieved 日期, URL」）；
    URL 字段另有 `find_urls` 兜底，这里只负责正文边界。
    """
    m = re.search(r"\bRetrieved\b", after)
    if not m:
        return after, ""
    body = after[: m.start()].rstrip(" .;；")
    note = after[m.start():].strip()
    note = re.sub(r"\bfrom\s+(?=https?://)", "", note)
    return body, re.sub(r"\s{2,}", " ", note).strip(" .;；")


def _apa_take_parens(text: str, note_bits: list[str]) -> tuple[str, dict[str, str]]:
    """从书名/题名里抽 `(pp. X–Y)` `(Vol. N)` `(Rev. ed.)` 等括注。

    返回 (去掉括注的文本, 抽出的字段)。季节性版本（`(Winter 2022 ed.)`）不进
    edition，按发布 bib 的口径落 note；认不出的括注原样留在文本里。
    """
    fields: dict[str, str] = {}

    def _repl(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        mp = _APA_PAGES_PAREN.match(inner)
        if mp:
            fields["pages"] = re.sub(r"\s*[-–—]\s*", "--", mp.group("pages"))
            return " "
        mv = _APA_VOL_PAREN.match(inner)
        if mv:
            fields["volume"] = mv.group("volume")
            return " "
        me = _APA_ED_PAREN.match(inner)
        if me:
            ed = me.group("ed")
            fields["edition"] = "Revised" if ed.lower().startswith("rev") else ed
            return " "
        if _APA_SEASON_ED_PAREN.match(inner):
            note_bits.append(inner)
            return " "
        return m.group(0)

    cleaned = _APA_PAREN.sub(_repl, text)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" .;；"), fields


def _split_apa_container_tail(s: str) -> tuple[str, str, str]:
    """切「书名」与尾部：返回 (书名, 尾, "pub"|"note"|"")。

    逐个句点边界试：尾巴是 `Place: Publisher` → 出版尾（"pub"）；尾巴带机构句/
    URL/Retrieved → 在线词典注记（"note"，取最早的那个边界，书名最短）；
    都不像 → 整段都是书名。题名里的句点（`Vol. 3.`）靠「尾巴像不像出版尾」排除。
    """
    best_note: tuple[str, str, str] | None = None
    for m in _APA_SENT_END.finditer(s):
        head = s[: m.start()].strip().rstrip(" .;；")
        tail = s[m.end():].strip()
        if not head or not tail:
            continue
        if _apa_place_publisher(tail):
            return head, tail, "pub"
        if best_note is None and re.search(r"https?://|\bRetrieved\b", tail):
            best_note = (head, tail, "note")
    if best_note:
        return best_note
    return s.strip(), "", ""


def _apa_authors(pre: str) -> str:
    """`Bailin, S., Case, R., & Daniels, L. B.` → `Bailin, S. and Case, R. and Daniels, L. B.`"""
    text = re.sub(r"\s*&\s*", " and ", pre.strip())
    parts = [x.strip() for x in re.split(r",\s*", text) if x.strip()]
    names: list[str] = []
    i = 0
    while i < len(parts):
        surname = re.sub(r"^(?:and\s+)", "", parts[i], flags=re.I).strip()
        i += 1
        if not surname:
            continue
        given = ""
        if i < len(parts) and _APA_INITIALS.match(parts[i]):
            given = re.sub(r"\s+", " ", parts[i]).strip()
            i += 1
        names.append(f"{surname}, {given}" if given else surname)
    return " and ".join(names)


def _apa_author_gap(pre: str) -> str:
    """作者段像不像 APA；像就返回 BibTeX 作者串，不像返回空串。

    两种合法形状：
      · 普通作者列表：`姓, 首字母`（至少一个 `X.`，含逗号）；
      · 团体作者：`Oxford University Press`（无逗号、无缩写）——整段用花括号保护，
        强制 BibTeX 当一个名字（发布 bib c17 就是 `{Oxford University Press}`）。
    不以数字开头（防把年份/页码片段当作者）。
    """
    s = pre.strip().rstrip(", ")
    if not s or re.search(r"^\d", s):
        return ""
    if "," in s:
        if not re.search(r"[A-Z]\.", s):
            return ""
        return _apa_authors(s)
    words = s.rstrip(".").split()
    if 2 <= len(words) <= 10 and all(
            re.fullmatch(r"[A-Za-z][A-Za-z.'’\-]*", w) for w in words):
        return "{%s}" % " ".join(words)
    return ""


def _apa_clean_title(t: str) -> str:
    return re.sub(r"[.。]\s*$", "", (t or "").strip()).strip()


def _apa_join_note(note_bits: list[str], retrieved: str) -> str:
    """note 片段按原样拼接（各片段的句点保留，不把两句话粘成一句）。"""
    bits = [x.strip() for x in note_bits if x and x.strip()]
    if retrieved:
        bits.append(retrieved)
    return re.sub(r"\s{2,}", " ", " ".join(bits)).strip(" ,;；")


def _parse_apa_in(p: Parsed, title: str, rest: str,
                  retrieved: str, note_bits: list[str]) -> None:
    """`In …` 章节 / 在线词典条目的尾部解析（p.title 由调用方给）。"""
    body = rest.strip().rstrip(" .;；")
    body = re.sub(r"^(?:his|her|their)\s+", "", body, flags=re.I)
    eds = ""
    m = _APA_EDS.match(body)
    if m:
        eds = _apa_editors(m.group("eds"))
        body = m.group("rest").strip()
    book, fields = _apa_take_parens(body, note_bits)
    booktitle, tail, kind = _split_apa_container_tail(book)
    p.booktitle = booktitle
    for name in ("pages", "volume", "edition"):
        if fields.get(name):
            setattr(p, name, fields[name])
    if kind == "pub":
        got = _apa_place_publisher(tail)
        if got:
            p.address, p.publisher = got
    elif tail:
        note_bits.append(tail)
    struct = bool(p.pages or p.volume or p.edition or p.publisher
                  or _SEP_CUE.search(booktitle))
    if struct:
        p.etype = "incollection"
        p.title = _apa_clean_title(title)
        if eds:
            p.editor = eds
    else:
        # 无页码/版本/出版尾的 `In <词典>.`：在线词典条目（发布 bib c15–c17 口径）
        p.etype = "article"
        p.journal = booktitle
        p.booktitle = ""
        p.title = _apa_clean_title(title)
        if eds:
            # 编辑者串可能以缩写点收尾（`… Tichy, O.`），别再补一个句点
            note_bits.insert(0, "Edited by %s%s" % (eds, "" if eds.endswith(".") else "."))
    p.note = _apa_join_note(note_bits, retrieved)


def _parse_apa_book(p: Parsed, after: str, retrieved: str,
                    note_bits: list[str]) -> bool:
    """`题名. Place: Publisher`（可夹 `(Vol. N)`/`(Rev. ed.)`）→ @book。"""
    for m in _APA_SENT_END.finditer(after):
        head = after[: m.start()].strip()
        tail = after[m.end():].strip()
        if not head or not tail:
            continue
        got = _apa_place_publisher(tail)
        if not got:
            continue
        title, fields = _apa_take_parens(head, note_bits)
        title = _apa_clean_title(title)
        if not title:
            continue
        p.title = title
        p.address, p.publisher = got
        for name in ("volume", "edition"):
            if fields.get(name):
                setattr(p, name, fields[name])
        p.etype = "book"
        p.note = _apa_join_note(note_bits, retrieved)
        return True
    return False


def _parse_apa_item(t: str) -> Parsed | None:
    """识别并解析 APA 体例条目；不是 APA 形状返回 None（绝不抢其它体例）。"""
    m = _APA_YEAR.search(t)
    if m:
        author_pre = t[: m.start()]
        after = t[m.end():].strip()
        year = m.group("year")
    else:
        nd = _APA_ND.search(t)
        if nd:
            author_pre = t[: nd.start()]
            after = t[nd.end():].strip()
            year = ""
        else:
            bare = _APA_BARE_YEAR.match(t)
            if not bare:
                return None
            author_pre = bare.group("pre")
            after = bare.group("after").strip()
            year = bare.group("year")
    author = _apa_author_gap(author_pre)
    if not author:
        return None
    p = Parsed()
    p.author = author
    p.year = year
    note_bits: list[str] = []
    after, retrieved = _apa_split_retrieved(after)
    tail = _APA_TAIL.match(after)
    if tail:
        p.etype = "article"
        p.title = _apa_clean_title(tail.group("title"))
        p.journal = tail.group("journal").strip().rstrip(",;")
        p.volume = tail.group("volume")
        p.number = (tail.group("number") or "").strip()
        p.pages = re.sub(r"\s*[-–—]\s*", "--", tail.group("pages").strip())
        p.note = _apa_join_note(note_bits, retrieved)
    else:
        inm = _APA_IN.match(after)
        if inm:
            _parse_apa_in(p, inm.group("title"), inm.group("rest"),
                          retrieved, note_bits)
        elif _parse_apa_book(p, after, retrieved, note_bits):
            pass
        else:
            rep = _APA_REPORT_CUE.search(after)
            if rep:
                # 显式报告号才认 @report（26 条没有可解析的报告，c12 是人工替换条目）
                p.etype = "report"
                p.title = _apa_clean_title(after[: rep.start()])
                p.note = _apa_join_note(
                    [after[rep.start():].strip()] + note_bits, retrieved)
            else:
                # 在线词典兜底：`题名. Dictionary/Encyclopedia/Online …` + Retrieved/URL
                parts = re.split(r"(?<=[.?!])\s+", after, maxsplit=1)
                if len(parts) == 2 and _APA_REFWORK.search(parts[1]) and (
                        retrieved or re.search(r"https?://", after)):
                    p.etype = "article"
                    p.title = _apa_clean_title(parts[0])
                    p.journal = _apa_clean_title(parts[1])
                    p.note = _apa_join_note(note_bits, retrieved)
                else:
                    # 真解析不出：misc（交付层会转「源稿原文逐字进 author」，不冒充结构化）
                    p.etype = "misc"
                    p.title = _apa_clean_title(after)
                    p.note = _apa_join_note(note_bits, retrieved)
    p.confidence = "high" if (p.title and (p.journal or p.publisher or p.booktitle)) else "low"
    return p


# ── B-222 B 段 B3/D2：编号清单里两个「类型判据」入口 ───────────────────────
#: 中文集刊/辑刊名（书，不是期刊）：`逻辑学动态与评论`（2025年第1辑）。
_CN_COLLECTION_NAME_RE = re.compile(r"(?:集刊|辑刊|丛刊)")
_CN_COLLECTION_ISSUE_RE = re.compile(r"第\s*\d+\s*辑")


def _cn_collection_journal(journal: str, raw: str) -> bool:
    """`journal` 格是否其实是**中文集刊**（书名，应挪到 booktitle）。

    只认两种自证线索（真中文期刊仍走 @article）：刊名含「集刊/辑刊/丛刊」，或
    原文有 `第N辑` 期号形态（集刊按辑出版，期刊按「年第N期」）。英文/中英混排
    刊名不在此列（不猜）。
    """
    j = (journal or "").strip()
    if not j or not re.search(r"[\u4e00-\u9fff]", j) or re.search(r"[A-Za-z]", j):
        return False
    return bool(_CN_COLLECTION_NAME_RE.search(j)
                or _CN_COLLECTION_ISSUE_RE.search(raw or j))


def parse_numbered_item(seg: str) -> Parsed:
    """解析 `作者, 年份, "题名", 刊名, 卷(期): 页` 这种。"""
    t = seg.strip().rstrip(".。")
    apa = _parse_apa_item(t)
    if apa is not None:
        return apa
    if not _title_quoted(t):
        # `郑伟宏. 《佛家逻辑通论》. 上海：复旦大学出版社，1996年，第45-78页.`
        # 是「作者. 《题名》. 出版信息」句点体例；归一成逗号体例再走无引号分支，
        # 否则按逗号切会把作者/题名/出版地全塞进作者、把页码当题名（期 87 sub7）。
        _bm = re.match(
            r"^(?P<a>[^《》]{1,80}?)[.。]\s*(?P<t>《[^》]{2,80}》)[.。]\s*(?P<r>.+)$", t)
        if _bm:
            t = f"{_bm.group('a').strip()}，{_bm.group('t').strip()}，{_bm.group('r').strip()}"
    p = Parsed()
    q = _title_quoted(t)
    if q:
        p.title = q
        # 注意要把引号也剥掉：`Jc Beall, 2000, “` 里那个开引号若不剥，
        # 年份正则（要求年份在末尾）就匹配不上，作者会变成 `Jc Beall, 2000, “`。
        pre = t[: t.find(q)].strip(" ,，\"'“”‘’")
        post = t[t.find(q) + len(q) :].strip(" ,，")
        # pre 形如 `Jc Beall, 2000` 或 `Jc Beall`
        ym = re.search(r"[，,]?\s*((?:19|20)\d{2})\s*[，,]?\s*$", pre)
        if ym:
            p.year = ym.group(1)
            pre = pre[: ym.start()].strip(" ,，\"'“”‘’")
        p.author = re.sub(r"\\&", "&", pre).strip(" .。,，\"'“”‘’")
        # 中文体例把年份写在题名**之后**（`鞠实儿，“题名”，中国社会科学，2026年第6期…`），
        # pre 里没有年份，这里退一步从整条里找，否则 year 会丢。
        if not p.year:
            ym2 = re.search(r"((?:19|20)\d{2})", t)
            if ym2:
                p.year = ym2.group(1)
        # ⚠️ 题名后面紧跟的是**右引号**（`”, Australasian Journal…`）。
        # 不先剥掉，下面 `^[\s,，.。]*` 的开头锚点就匹配不上，刊名会整个丢掉。
        rest = post.lstrip(" ,，.。\"'“”‘’")
    else:
        # 无引号：`付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。`
        parts = [x.strip() for x in re.split(r"[，,]", t) if x.strip()]
        # ⚠️ 中文著/译体例（`原作者（著），译者（译），真题名，…`）：前**两**段都是作者，
        #    那个中文逗号是「著者 → 译者」的分界，不是「作者 → 题名」的分界。
        #    不识别的话，译者那段会被当成题名（真题名就此丢失、还多一个假题名），
        #    实测见第 64 条与 `_translation_author_segments()` 的注释。
        n_auth = _translation_author_segments(parts)
        # B-220 N3：英文多作者编号清单——`R. E. Young, A. L. Becker and K. L. Pike, 1970,
        # …`（旧实现只取第 1 作者，第 2 作者被当成题名；实测 90 期 Young1970/
        # vanEemeren2014/2009、90-04 Parr2022）。只在「续段像英文作者名、且其后紧跟
        # 一个裸年份」时合并，宁可少并、不可错并。
        if n_auth <= 1 and not re.search(r"[\u4e00-\u9fff]", t):
            _j = 1
            while _j < len(parts) and _j <= 8 and looks_like_en_author_segment(parts[_j]):
                _nxt = parts[_j + 1].strip() if _j + 1 < len(parts) else ""
                if re.fullmatch(r"(?:19|20)\d{2}[a-z]?", _nxt):
                    _j += 1
                    break
                _j += 1
            if _j > 1 and re.fullmatch(r"(?:19|20)\d{2}[a-z]?",
                                        parts[_j].strip() if _j < len(parts) else ""):
                n_auth = _j
        # 续接的作者段可能自带 `and`/`&` 连接词（`…, and J. H. M. Wagemans, 2014`）——
        # 去掉连接词再用统一分隔符拼接，避免 `… and and J. H. …`（正常化在出口做）。
        _auth_parts = [parts[0]] + [
            re.sub(r"^(?:and|&)\s+", "", x.strip()) for x in parts[1:n_auth]
        ]
        p.author = "，".join(_auth_parts) if parts else ""
        body = [x for x in parts[n_auth:] if not re.fullmatch(r"(19|20)\d{2}\s*年?", x)]
        # `作者：《题名》`（期 87 sub7 `[12] 虞愚：《印度逻辑推理与推论式的发展》,哲学研究，…`）
        # 没有逗号分隔作者与题名，整段被当成题名——按「冒号 + 书名号」拆开。
        if body:
            cm = re.match(r"^(.{1,30}?)[：:]\s*(《[^》]{2,80}》.*)$", body[0])
            if cm and not body[0].startswith("《"):
                p.author = cm.group(1).strip()
                body[0] = cm.group(2).strip()
        # `D.N.Shastri，1957. The Philosophy…`：无引号体例把「年份. 题名」整段当题名，
        # 年份混进 title（期 87 sub7 P0-5）。剥掉前缀年份并记进 year。
        year_prefix = ""
        if body:
            ym_prefix = re.match(r"^((?:19|20)\d{2}[a-z]?)\s*[.。]\s*(.+)$", body[0])
            if ym_prefix:
                year_prefix = ym_prefix.group(1)
                body[0] = ym_prefix.group(2).strip()
        if body:
            p.title = body[0].strip("《》〈〉").strip()
            # 英文题名常含逗号：把被逗号切开的副题并回 title，直到遇到出版信息。
            # B-220 N2：旧实现只在「题名段带年份前缀」时才并（罕见形态），实测 90 期
            # `Language, Truth and Logic` / `Necessity, Volition, and Love` /
            # `Argumentation, Communication, and Fallacies…` 全被截断。判据统一到
            # `title_comma_continuation()`（与 wordentry 人年制通道共用，单点）。
            _join_n = title_comma_continuation(body)
            if _join_n:
                joiner = "，" if re.search(r"[\u4e00-\u9fff]", p.title) else ", "
                for _k in range(1, _join_n + 1):
                    p.title = (p.title.rstrip(" ,，") + joiner
                               + body[_k].strip(" ,，"))
                del body[1:_join_n + 1]
        rest = "，".join(body[1:])
        ym = re.search(r"((?:19|20)\d{2})", year_prefix or t)
        if ym:
            p.year = ym.group(1)

    p.pages = _pages(rest)
    v, n = _vol_issue(rest)
    p.volume, p.number = v, n
    p.publisher, p.address = _pub_addr(rest)
    p.author = _invert_en_author(p.author)

    # 刊名：题名之后、到卷(期)或页码之前的那一段。
    # 实测 `…Knower Paradox", Australasian Journal of Philosophy, 78(2): 241-247.`
    # 这里若不出手，journal 就整个丢了（人工成品是有 journal 的）。
    if not p.publisher:
        m = re.match(
            r"^[\s,，.。]*([^,，.。\d]{3,70}?)\s*[，,]?\s*"
            r"(?:\d{1,3}\s*\(|(?:No\.?\s*)?\d{1,4}\s*[-–—:]|$)",
            rest,
        )
        if m:
            cand = m.group(1).strip()
            # B-220 N4a：旧判据用 `\bin\b` 排除「in Book」容器形态，但把刊名里**含
            # 单词 in** 的也误杀（实测 90-05 `The Amherst Lecture in Philosophy` 因此
            # journal 丢失、条目退化成 @misc、字段被 entry_to_bib 的 misc 字段序丢弃）。
            # 只拒「以 in 开头」的候选（那才是容器形态）。
            if not re.search(r"（", cand) and not re.match(r"(?i)^in\s", cand):
                p.journal = cand
        if not p.journal:
            # 中文期刊：`刊名，2026年第6期，第24–43页。`——刊名后面直接跟年份或期号
            # B-220 N4b：补 `2023第9期`（年份与「第N期」之间无「年」/逗号）形态
            # （实测 90-05 费多益条：`中国社会科学，2023第9期，第122-143+207页。`）。
            m2 = re.match(
                r"^[\s,，.。]*([\u4e00-\u9fff·]{2,30})\s*[，,]\s*"
                r"(?:\d{4}\s*年|第\s*\d+\s*期|\d{4}\s*第\s*\d+\s*期)",
                rest,
            )
            if m2:
                p.journal = m2.group(1).strip()
        if not p.journal:
            # 兜底（第 65 条）：刊名带括号后缀（`湖南科技大学学报（社会科学版）`）、
            # 写成书名号（`《逻辑学研究》2025年第4期`），或题名里有嵌套引号导致 `rest`
            # 不以刊名开头的——旧 m2 全落空，这里按「YYYY年第N期」锚点回退取刊名。
            cj, cnum = _cn_journal_before_year(t)
            # ⚠️ 若「刊名」与解析出来的题名是同一串，说明题名那一格本来就被切坏了
            #    （实测 2026年第5期(90)/07 Jiayi_He 的
            #     `徐佳明：《题名》，《武汉科技大学学报》，2024年第1期。`——无引号分支
            #     把 `作者：《题名》` 整段当成作者、把刊名当成了题名）。这时两条证据
            #     互相矛盾，**不升级成 @article**：照旧留作 @misc，由 refmark 把原文
            #     交给编辑的 AI（宁可不认，不猜）。
            if cj and p.title and (cj in p.title or p.title.strip("《》〈〉") == cj):
                cj = ""
            if cj:
                p.journal = cj
                p.number = p.number or cnum

    # B-222 B 段 B3/D2/D3：编号清单通道的**类型/字段归位**（本函数是编号清单出口）。
    #   B3：`北京师范大学博士论文`——`_cn_journal_before_year` 会把「校名+学位类型」
    #       抓成 journal；交 `wordrefs.thesis_fields` 单点（与编号清单写回同源）。
    #       只认**校名在学位类型之前**的语序；旧语序（`博士学位论文，校名`）仍由
    #       `apply_numbered_extras` 收口，既有断言 `test_refs_p1` 反例③不变。
    #   D2：中文集刊（`第N辑`/集刊/辑刊）是书不是期刊，刊名格挪到 booktitle。
    #   D3：`in 编者 (eds.), 书名` 的容器段（题名**之后**才认）补 booktitle。
    from . import wordrefs as _wordrefs

    _thesis = _wordrefs.thesis_fields(t)
    _thesis_before = bool(_thesis and _thesis.get("kind_order") == "school_before")
    _collection = False
    _type_locked = False
    if _thesis_before:
        p.etype = _thesis["etype"]
        p.journal = ""
        _type_locked = True
    elif _cn_collection_journal(p.journal, t):
        p.booktitle = p.journal
        p.journal = ""
        p.etype = "incollection"
        _collection = True
        _type_locked = True
    elif not p.booktitle:
        _container_bt, _ = _wordrefs.incollection_editor_list(
            _wordrefs.in_container_tail(t), allow_c=True)
        if _container_bt:
            p.booktitle = _container_bt

    # P1-2（2026-10-18）：类型统一走「出版信息特征」判据，不再按卷期正则命中与否。
    # 先纠正两个把出版社塞进 journal 格的老问题（`Oxford: Clarendon` / `Springer`），
    # 再交给可复用判据（下一卡 docxsource 也用同一个函数）。
    if p.journal and not p.publisher:
        place_pub = looks_like_place_publisher(p.journal)
        if place_pub:
            p.address, p.publisher = place_pub
            p.journal = ""
        elif _publisher_only_journal(p.journal):
            p.publisher, p.journal = p.journal, ""
    _etype, _etype_reason = classify_entry_type({
        "journal": p.journal, "booktitle": p.booktitle, "publisher": p.publisher,
        "address": p.address, "pages": p.pages, "volume": p.volume,
        "number": p.number, "note": p.note,
    }, raw=t)
    if not _type_locked:
        p.etype = _etype
    if not p.title:
        p.title = rest

    got = sum(bool(x) for x in (p.author, p.title, p.journal, p.booktitle, p.publisher))
    p.confidence = "high" if got >= 3 else "low"
    return p


def parse_numbered_list(text: str) -> list[Parsed]:
    """解析整份编号参考文献清单。"""
    return [parse_numbered_item(x) for x in split_numbered(text)]


# ═══════════════════════════════════════════════════════════════════════════
# 手写 `\bibitem` 条目解析（B-222 A 段：B-233 规则整体收编，判据单点）
# ═══════════════════════════════════════════════════════════════════════════

FIELD_ORDER = ("author", "title", "journal", "booktitle", "volume", "number",
               "pages", "publisher", "address", "school", "year", "note")
_TYPES = frozenset(("article", "book", "incollection", "inproceedings",
                    "phdthesis", "mastersthesis", "misc"))
_UNSAFE_RE = re.compile(r"(?<!\\)([&%#_])")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PYEAR_RE = re.compile(r"\(\s*((?:19|20)\d{2})\s*\)\s*[.,;]?\s*$")
_MARK_RE = re.compile(r"\[\s*([JMCDNZRSPA])\s*\]")
# B-222 B 段 B5：标记→类型曾有**第二份表**，与模块顶 `TYPE_MARK` 在 N/R/A 三键发散
# （`[N]` 被写成 @article、`[R]` 写成 @misc、`[A]` 写成 @book）。单点回 `TYPE_MARK`。
_MARK_TYPE = TYPE_MARK
_THESIS_RE = re.compile(
    r"(Ph\.?\s*D\.?|Doctoral|Master'?s?)\s+(thesis|dissertation)\b", re.I)
_IN_RE = re.compile(r"^In\s*:\s*(.+)$", re.S)
_VOL_RE = re.compile(r"\bVol\.?\s*~?\s*(\d+)", re.I)
_ITALIC_RE = re.compile(r"\\textit\{([^{}]*)\}")
_BOLD_RE = re.compile(r"\\textbf\{([^{}]*)\}")
_PAGES_RE = re.compile(
    r"(?:pp?\.\s*~?\s*|[,，]\s*)([A-Za-z]?\d{1,5})\s*(?:--|–|—|-|~)\s*([A-Za-z]?\d{1,5})")


# ── 文本小工具 ────────────────────────────────────────────────────────────

def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _year_of(s: str) -> str:
    m = _PYEAR_RE.search(s or "")
    return m.group(1) if m else ((_YEAR_RE.findall(s or "") or [""])[-1])


def _clean_text(t: str) -> str:
    return _collapse(t).strip(" .,;:")


def clean_bibitem_note(text: str) -> str:
    return _clean_text(_collapse(text))


def _clean_title(t: str) -> str:
    t = _clean_text(t)
    m = re.fullmatch(r"\\textit\{(.+)\}", t, re.S)
    t = m.group(1).strip() if m else t
    return re.sub(r"^[\s:：.]+", "", t).strip()


def empty_bibitem_fields() -> dict:
    return {"key": "", "_type": "misc", **dict.fromkeys(FIELD_ORDER, "")}


def _split_sentence(s: str) -> tuple[str, str]:
    """第一个 `. `（花括号深度 0、非转义）处切题名/后半段；切不出返回 `(s, "")`。"""
    depth, i, n = 0, 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == "." and depth == 0 and i + 1 < n and s[i + 1] in " \n\t":
            j = i + 1
            while j < n and s[j] in " \n\t":
                j += 1
            if j < n and (s[j].isupper() or s[j] in "\\{" or s[j].isdigit()):
                return s[:i].strip(), s[j:].strip()
        i += 1
    return s.strip(), ""


# ── 作者串 → BibTeX `and` ────────────────────────────────────────────────

def _join_authors(blob: str) -> str:
    """`Zhen, L., Seligman, J.` → `Zhen, L. and Seligman, J.`（不改姓名部件）。"""
    s = _collapse(blob).strip().strip(",;")
    if not s:
        return ""
    if re.search(r"[\u4e00-\u9fa5，、；]", s):  # 中文条目：逗号/顿号即作者分隔
        return " and ".join(p.strip() for p in re.split(r"[,，、;；]\s*", s) if p.strip())
    out, cur = [], ""
    for tok in (t.strip() for t in re.split(r",\s*", s) if t.strip()):
        # 缩写点被当分隔符吃掉（GB/T 英文条目 `Smith, J`）→ 补回
        if re.fullmatch(r"[A-Z]\.(?:[-\s]*[A-Z]\.)*|[A-Z]{1,3}", tok):
            init = tok if tok.endswith(".") else tok + "."
            out.append(f"{cur}, {init}" if cur else init)
            cur = ""
        else:
            if cur:
                out.append(cur)
            cur = tok
    if cur:
        out.append(cur)
    return " and ".join(out) if out else s


def _split_author_rest(s: str) -> tuple[str, str]:
    """LNCS 的 `作者列表: 正文` 切分；切不出返回 `("", s)`。"""
    m = re.search(r"\.\s*:\s+", s)
    if m:
        return s[: m.start() + 1].strip(), s[m.end():].strip()
    m = re.match(r"^([^:：]{2,240}?)\s*[：:]\s+(.+)$", s, re.S)
    if m:
        head = m.group(1).strip()
        if all(len(t) <= 40 for t in re.split(r",\s*", head)):
            return head, m.group(2).strip()
    return "", s


# ── GB/T 7714 / 中文条目 ─────────────────────────────────────────────────

def _fill_pub_addr(d: dict, s: str) -> None:
    """`出版地: 出版社` / `出版社, 城市` → address/publisher（拆不出留空）。"""
    m = _PYEAR_RE.search(s or "")
    s = (s[:m.start()] if m else (s or "")).strip(" ,.;")
    m = re.match(r"^(?P<a>[^:：,，]{2,60}?)\s*[:：]\s*(?P<p>[^,，.。]{2,100})", s)
    if m:
        d["address"], d["publisher"] = _clean_text(m.group("a")), _clean_text(m.group("p"))
        return
    parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    if parts:
        d["publisher"] = _clean_text(parts[0])
        if len(parts) > 1:
            d["address"] = _clean_text(", ".join(parts[1:]))


def _parse_marked(raw: str) -> dict | None:
    """GB/T 7714 条目（中英文都认）：类型看 `[X]` 标记，字段复用 footref 行解析器。"""
    mark = _MARK_RE.search(raw)
    if not mark:
        return None
    e = empty_bibitem_fields()
    e["_type"] = _MARK_TYPE.get(mark.group(1).upper(), "misc")
    try:
        p = parse_one(raw)
    except Exception:  # noqa: BLE001  解析器异常只是「这条不提升」
        p = None
    if p is not None:
        for name in ("author", "title", "journal", "booktitle", "volume", "number",
                     "pages", "publisher", "address", "year"):
            val = getattr(p, name, "")
            if val:
                e[name] = str(val)
    if not e["author"]:  # 行内兜底：`作者. 题名[X]. ...` 的最后一段 `. ` 切作者/题名
        head = raw[: mark.start()]
        last = None
        for m in re.finditer(r"[.。]\s+", head):
            last = m
        author, title = (head[:last.start()], head[last.end():]) if last else ("", head)
        e["author"] = _join_authors(author.strip(" .。"))
        e["title"] = e["title"] or _clean_title(title.strip(" .。"))
    if not e["year"]:
        e["year"] = _year_of(raw)
    if e["_type"] == "phdthesis" and not e["school"]:
        tail = raw[mark.end():]
        mt = _THESIS_RE.search(tail)
        school = tail[mt.end():] if mt else tail
        school = re.sub(r"[\s,，.。;；]*(?:19|20)\d{2}\s*[.。]?\s*$", "", school)
        e["school"] = _clean_text(school.strip(" ,.;"))
    return e


def _parse_cjk(raw: str) -> dict | None:
    """中文条目：复用 `bibnorm._parse_cn_numbered_line`（《题名》/plain book 体例）。"""
    try:
        from .bibnorm import _parse_cn_numbered_line
        d = _parse_cn_numbered_line(raw)
    except Exception:  # noqa: BLE001
        d = None
    if not d:
        return None
    e = empty_bibitem_fields()
    e.update({k: v for k, v in d.items() if k in e and v})
    return e


# ── LNCS / Springer 数字体例 ──────────────────────────────────────────────

def _fill_lncs_tail(e: dict, tail: str) -> None:
    mt = _THESIS_RE.search(tail)
    if mt:
        e["_type"] = "mastersthesis" if "master" in mt.group(0).lower() else "phdthesis"
        school = _clean_text(tail[mt.end():])
        pm = _PYEAR_RE.search(school)
        e["school"] = (school[: pm.start()] if pm else school).strip(" ,.;")
        e["school"] = re.sub(r"^(?:of|at|,)\s+", "", e["school"], flags=re.I)
        return
    mi = _IN_RE.match(tail)
    if mi:  # 章节：In: \textit{书名}, pp. N--M. 出版社, 城市 (年)
        e["_type"] = "incollection"
        s = mi.group(1).strip()
        pm = _PYEAR_RE.search(s)
        s = (s[: pm.start()] if pm else s).strip(" ,.;")
        im = _ITALIC_RE.search(s)
        if im:
            e["booktitle"] = _clean_text(im.group(1))
            s = (s[: im.start()] + " " + s[im.end():]).strip()
        else:
            bt, s2 = _split_sentence(s)
            e["booktitle"], s = _clean_title(bt), s2
        vm = _VOL_RE.search(s)
        if vm:
            e["volume"] = vm.group(1)
        pm = _PAGES_RE.search(s)
        if pm:
            e["pages"] = f"{pm.group(1)}--{pm.group(2)}"
            s = s[pm.end():]
        else:
            s = ""
        left = re.sub(r"^[\s,.;]+", "", s)
        pm = _PYEAR_RE.search(left)
        if pm:
            left = left[: pm.start()]
        if left.strip(" ,.;"):
            _fill_pub_addr(e, left)
        return
    if _PAGES_RE.search(tail) or "\\textbf{" in tail or "\\textit{" in tail:
        e["_type"] = "article"
        im, bm = _ITALIC_RE.search(tail), _BOLD_RE.search(tail)
        if im:
            e["journal"] = _clean_text(im.group(1))
        if bm:
            e["volume"] = _clean_text(bm.group(1))
        if not im and not bm:  # 无标记体例：`题名. 刊名 卷(期), 页 (年)`
            gm = re.match(
                r"^(?P<j>.+?)\s+(?P<v>\d{1,4})\s*(?:\((?P<n>[^)]{1,12})\))?\s*,\s*"
                r"(?P<p>[A-Za-z]?\d{1,5}\s*(?:--|–|—|-|~)\s*[A-Za-z]?\d{1,5})", tail)
            if gm:
                e["journal"], e["volume"] = _clean_text(gm.group("j")), gm.group("v")
                e["number"] = gm.group("n") or ""
                e["pages"] = re.sub(r"\s*(?:--|–|—|-|~)\s*", "--", gm.group("p"))
            return
        pm = _PAGES_RE.search(tail)
        if pm:
            e["pages"] = f"{pm.group(1)}--{pm.group(2)}"
        if not e["volume"]:
            vm = _VOL_RE.search(tail)
            if vm:
                e["volume"] = vm.group(1)
        for num in re.findall(r"\(\s*([^()]{1,12})\s*\)", tail):
            if re.fullmatch(r"\d{1,3}[a-z]?", num.strip()):
                e["number"] = num.strip()
                break
        return
    if tail:  # 书：\textit{题名}. 出版社, 城市 (年)
        e["_type"] = "book"
        _fill_pub_addr(e, tail)
        if e["publisher"]:
            return
    e["_type"], e["note"] = "misc", _clean_text(tail)


# ── 单条解析 / 渲染 / key / 主入口 ───────────────────────────────────────

def parse_bibitem_entry(body: str) -> dict:
    raw = _collapse(body)
    if not raw:
        return empty_bibitem_fields()
    d = _parse_marked(raw)
    if d is None and re.search(r"[\u4e00-\u9fa5]", raw):
        d = _parse_cjk(raw)
    if d is None:  # LNCS/Springer：`作者列表: 题名. 后端信息`
        author, rest = _split_author_rest(raw)
        if rest:
            d = empty_bibitem_fields()
            d["author"], d["year"] = _join_authors(author), _year_of(raw)
            title, tail = _split_sentence(rest)
            d["title"] = _clean_title(title)
            if tail:
                _fill_lncs_tail(d, tail)
            elif not author:
                # B-262：没有作者、也没有出版尾段 → 无从解析，整条走 verbatim 兜底
                # （不再把同一原文重复写进 note）。
                d = None
            else:
                d["note"] = _clean_text(rest)
    if d is not None:
        return d
    # B-262：解析不出的复杂条目 → @misc，原文逐字符进 author（渲染时做花括号保护）；
    # 不再写 note/title/year（misc 驱动只印 author）。
    e = empty_bibitem_fields()
    e["_verbatim"] = True
    e["author"] = raw
    return e


def bibitem_skeleton_fields(skel_bib: str) -> dict[str, dict]:
    """`bibitems_to_bib` 骨架 → {key: {title, year}}，仅作解析失败时的兜底。"""
    out: dict[str, dict] = {}
    for k, t, y in re.findall(
            r"@\w+\{([^,]*),\s*title\s*=\s*\{(.*?)\},\s*year\s*=\s*\{([^}]*)\}",
            skel_bib or "", re.S):
        out[k.strip()] = {"title": _clean_title(t), "year": y.strip()}
    return out


def render_bibitem_entry(e: dict) -> str:
    if e.get("_verbatim"):
        from .footnotes import _bib_escape_verbatim

        return "@misc{%s,\n  author = {{%s}}\n}" % (
            e.get("key") or "bibitem",
            _bib_escape_verbatim(_collapse(e.get("author") or "")))
    etype = str(e.get("_type") or "misc").lower()
    fields: list[str] = []
    for name in FIELD_ORDER:
        raw = _collapse(e.get(name) or "")
        # 作者串末位缩写点（`Seligman, J.`）是数据，不当句末标点剥
        val = _UNSAFE_RE.sub(r"\\\1", raw.strip(" ,;") if name == "author"
                             else _clean_text(raw))
        if val:
            fields.append(f"  {name} = {{{val}}}")
    return "@%s{%s,\n%s\n}" % (etype if etype in _TYPES else "misc",
                               e.get("key") or "bibitem", ",\n".join(fields))
