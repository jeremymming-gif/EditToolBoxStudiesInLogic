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
    m = re.search(r"第\s*(\d{1,4})\s*[-–—~]\s*(\d{1,4})\s*(?:[、,，]\s*\d{1,4}\s*[-–—~]\s*\d{1,4})?\s*页", text)
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
    # 这里照同一口径（实测 09 Jiawei_Chu 第 15 条：人工 `volume={10}`）。
    m = re.search(r"\bNo\.?\s*(\d{1,3})", text)
    if m:
        return m.group(1), ""
    return "", ""


# 出版社线索词（英文）：只有命中它们才敢把 `X: Y` 当成「出版地: 出版社」。
# 放宽的起因：实测 09 Jiawei_Chu 的 `Mahwah, NJ: Lawrence Erlbaum Associates`、
# `New York: Peter Lang`、`London: Arnold` 都因为老名单（只认 Press/University/…）
# 而整个丢掉，条目退化成 `@misc`——而 misc 的字段序里没有 publisher/address，
# 信息就此消失，人工成品那边是 `@book` + 出版社。
_PUB_CUES = (
    r"Press|University|Verlag|Books?|Publishing|Publishers?|Associates|"
    r"Lang|Arnold|SAGE|Sage|Erlbaum|Foresman|Harcourt|Pearson|Longman|"
    r"Benjamins|Wiley|Blackwell|Springer|Routledge|Bloomsbury|Palgrave|"
    r"Gruyter|Kluwer|Reidel|Elsevier|Wadsworth|Waveland|Rowman|Parlor|"
    r"McGraw|Macmillan|Scribner|Holt|Rinehart|Winston|Harper|Collins|"
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
    if re.search(_PUB_CUES, text):
        return True
    if re.match(r"^(?:1[89]|20)\d{2}\b", text):
        return True
    if re.match(r"^(?:第\s*\d+|pp?\.\s*\d|Vol\.?|No\.?|in\s|In\s|载\s|见\s)", text):
        return True
    if re.match(r"^[A-Z][A-Za-z.\- ]{1,20}[:：]\s*[A-Z]", text) and "," in text:
        return True
    return False


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


def _split_author_title(head: str) -> tuple[str, str]:
    """把 `作者列表. 题名` 切开。

    难点是多作者：`Kahneman D., Slovic P., \\& Tversky A. Judgment under
    Uncertainty`。若按第一个 `. ` 切，作者只剩 `Kahneman D`、其余作者混进题名
    （实测就是这样错的）。

    判据：切点后的「题名」若以 `&`、`\\&`、`,` 开头，说明后面还有作者，继续往后找。
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
    for cm in re.finditer(r"[”\"]", text[start:]):
        inner = text[start : start + cm.start()]
        if fallback is None:
            fallback = inner
        # 内侧引号没配平 → 这个右引号属于嵌套的内层，不是题名的收尾
        if inner.count("“") != inner.count("”") or inner.count('"') % 2:
            continue
        nxt = text[start + cm.end() : start + cm.end() + 1]
        if nxt in ("", "，", ",", "。", ".", "；", ";", "、"):
            if 4 <= len(inner) <= 120:
                return inner.strip()
    if fallback is not None and 4 <= len(fallback) <= 120:
        return fallback.strip()
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


def _has_container(fields: dict, raw: str = "") -> bool:
    """是不是「析出文献」（有容器书/论文集线索）。

    判据：`booktitle` 非空；或文中有 `(eds.)/（编）/主编/edited by` 且同时有
    页码线索/出版社/`in` 结构——只凭一个 `(eds.)` 不升级（可能只是注释）。
    """
    if str(fields.get("booktitle") or "").strip():
        return True
    blob = " ".join(str(fields.get(k) or "") for k in ("address", "note", "publisher", "journal"))
    blob = f"{blob} {raw}".strip()
    if not _CONTAINER_CUE.search(blob):
        return False
    return bool(_PAGES_CUE.search(blob) or str(fields.get("publisher") or "").strip()
                or re.search(r"\bin\s+", blob, re.I))


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
    etype, _reason = classify_entry_type(out, raw=raw)
    out["_type"] = etype
    return out

#
# 《排校手册》源码 `reference.bib` 的实例：`黑格尔（著），杨一之（译）`、
# `焦循（著），沈文倬（点校）`。这里的**中文逗号不是**「作者 → 题名」的分界，
# 而是「著者 → 译者」的分界；顿号也不是多作者分隔符，而是「姓名的一部分」。
#
# 为什么不识别就会坏（实测 `2026年第5期(90)/01 Jingru_Kou` 第 16 条）：
# `[16] 阿斯海姆（著），张建军、万林（译），指称与意向性，南京：南京大学出版社，2014年。`
# 按中文逗号切之后，`张建军、万林（译）` 被当成了**题名** ——
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
        p.author, p.title = _split_author_title(head)
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
# 实测 02 Zixin_Luo 的 `附件一 完整参考文献.docx`：
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

    实测期 90/05 Beibei_Gao：源稿第 13 条写成 `[13 J. Ross, …`（缺右括号），
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
# journal，刊名就此整个消失。实测 2026年第5期(90)/01 Jingru_Kou 第 17 条
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


# ── P0-4（期 87 sub8 实测）：APA 体例编号清单 ──────────────────────────────
#   源稿：`Bailin, S., Case, R., Coombs, J. R., & Daniels, L. B. (1999a).
#          Common misconceptions of critical thinking. Journal of Curriculum
#          Studies, 31(3), 269–283.`
#   旧实现无引号分支按逗号切 → author=Bailin、title=S.，随后被 `len(title)<4`
#   整条丢弃（26 条里丢 5 条、且无 warning）。APA 有**可判定的固定形状**：
#   `作者段 (YYYY[a-z]?). 题名. 刊名, 卷(期), 页.`，单独成一条分支最安全。
_APA_YEAR = re.compile(r"\(\s*(?P<year>(?:19|20)\d{2}[a-z]?)\s*\)\s*[.。]?\s*")
_APA_INITIALS = re.compile(r"^(?:[A-Z]\.\s*)+$")
_APA_TAIL = re.compile(
    r"^(?P<title>.+?[.?!。？！])\s+"
    r"(?P<journal>[A-Z][^,.]{1,90}?)\s*,\s*"
    r"(?P<volume>\d{1,4})(?:\s*\((?P<number>[^)]+)\))?\s*,\s*"
    r"(?P<pages>[A-Z]?\d{1,5}\s*[-–—]\s*[A-Z]?\d{1,5})\s*[.。]?\s*$")


def _split_apa_title_pub(after: str) -> tuple[str, str]:
    """APA 书/章节：`Title (Vol. 7). Windsor, ON, Canada: University of Windsor.`
    → (Title (Vol. 7), Windsor, ON, Canada: University of Windsor)。

    判据：切点后的尾巴含出版社线索词（Press/Publishing/University/…）——宁可认不出，
    也不把副题里的句点当切点（题名里带句点的书不少）。
    """
    for m in re.finditer(r"\.\s+", after):
        head = after[: m.start()]
        if head.count("(") > head.count(")"):     # 括号里的句点（`(Vol. 7)`）不是切点
            continue
        tail = after[m.end():].strip()
        if not tail or len(tail) > 120:
            continue
        if re.search(r"\b(?:Press|Publishing|Publishers?|Pub|University|Universit[äa]t|"
                     r"Routledge|Blackwell|Springer|Wiley|Books?|Verlag|Hackett|SAGE|Sage|"
                     r"Erlbaum|Pearson|Longman|McGraw|Elsevier|Rowman|Bloomsbury|Palgrave)\b",
                     tail):
            return after[: m.start()].strip(), tail
    return after.strip(), ""


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


def _parse_apa_item(t: str) -> Parsed | None:
    """识别并解析 APA 体例条目；不是 APA 形状返回 None（绝不抢其它体例）。"""
    m = _APA_YEAR.search(t)
    if not m:
        return None
    pre = t[: m.start()].strip().rstrip(", ")
    after = t[m.end():].strip()
    # 作者段必须像 APA：有逗号 + 至少一个「X.」缩写，且不以年份/数字开头。
    if "," not in pre or not re.search(r"[A-Z]\.", pre) or re.search(r"^\d", pre):
        return None
    author = _apa_authors(pre)
    if not author:
        return None
    p = Parsed()
    p.author = author
    p.year = m.group("year")
    tail = _APA_TAIL.match(after)
    if tail:
        p.title = re.sub(r"[.。]\s*$", "", tail.group("title").strip())
        p.journal = tail.group("journal").strip().rstrip(",;")
        p.volume = tail.group("volume")
        p.number = (tail.group("number") or "").strip()
        p.pages = re.sub(r"\s*[-–—]\s*", "--", tail.group("pages").strip())
        p.etype = "article"
    else:
        title, pubpart = _split_apa_title_pub(after)
        p.title = title
        if pubpart:
            if re.search(r"[:：]", pubpart):
                p.address, p.publisher = [
                    x.strip().rstrip(".") for x in re.split(r"[:：]", pubpart, maxsplit=1)]
            else:
                p.publisher = pubpart.strip().rstrip(".")
        p.etype = "book" if p.publisher else "misc"
    p.confidence = "high" if (p.title and (p.journal or p.publisher)) else "low"
    return p


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
        p.author = "，".join(parts[:n_auth]) if parts else ""
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
            if year_prefix:
                joiner = "，" if re.search(r"[\u4e00-\u9fff]", p.title) else ", "
                while len(body) > 1 and not _looks_pub_segment(body[1]):
                    p.title = p.title.rstrip(" ,，") + joiner + body.pop(1).strip()
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
            if not re.search(r"（|\bin\b", cand):
                p.journal = cand
        if not p.journal:
            # 中文期刊：`刊名，2026年第6期，第24–43页。`——刊名后面直接跟年份或期号
            m2 = re.match(
                r"^[\s,，.。]*([\u4e00-\u9fff·]{2,30})\s*[，,]\s*(?:\d{4}\s*年|第\s*\d+\s*期)",
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
            #    （实测 2026年第5期(90)/07 Liangying_Zhao 的
            #     `陈晓平：《题名》，《武汉科技大学学报》，2024年第1期。`——无引号分支
            #     把 `作者：《题名》` 整段当成作者、把刊名当成了题名）。这时两条证据
            #     互相矛盾，**不升级成 @article**：照旧留作 @misc，由 refmark 把原文
            #     交给编辑的 AI（宁可不认，不猜）。
            if cj and p.title and (cj in p.title or p.title.strip("《》〈〉") == cj):
                cj = ""
            if cj:
                p.journal = cj
                p.number = p.number or cnum

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
    p.etype, _etype_reason = classify_entry_type({
        "journal": p.journal, "booktitle": p.booktitle, "publisher": p.publisher,
        "address": p.address, "pages": p.pages, "volume": p.volume,
        "number": p.number, "note": p.note,
    }, raw=t)
    if not p.title:
        p.title = rest

    got = sum(bool(x) for x in (p.author, p.title, p.journal, p.booktitle, p.publisher))
    p.confidence = "high" if got >= 3 else "low"
    return p


def parse_numbered_list(text: str) -> list[Parsed]:
    """解析整份编号参考文献清单。"""
    return [parse_numbered_item(x) for x in split_numbered(text)]
