#!/usr/bin/env python3.14
r"""B-264 v2：APA 参考文献解析层结构化 + 「misc 逐字原文」交付契约。

背景（期 87 sub9 实测的六类缺口，全部在 APA 编号清单通道）
----------------------------------------------------------
旧 `_parse_apa_item()` 只认两种尾巴：`_APA_TAIL` 期刊正则（刊名不许含逗号/冒号、
题名必须以 `.?!` 收尾）与 `_split_apa_title_pub()`（出版社线索词表很窄）。于是：

  ① 18xx 年份（`(1896)`）直接失配 → 整条落 misc；
  ② 年份后缀（`1999a`）没剥 → year 字段带字母；
  ③ 出版尾 `Place: Publisher` 认不出 Heath / Henry Holt / Charles C. Thomas 一类型号；
  ④ `In <eds> (Eds.), <booktitle> (pp. X–Y).` 章节、`(Vol. N)`、`(Rev. ed.)`、
     n.d.、团体作者、在线词典 `Retrieved … URL` 全无处理 → 题名吞掉整个出版尾；
  ⑤ 题名以引号收尾（`… “critical thinking.”`）时期刊尾正则失配；
  ⑥ 长刊名（含冒号+逗号）被截断。

本批新增 / 收紧的出口约定（用户 m00808 / Lead m00248）
------------------------------------------------------
**凡最终交付 type=misc 的条目，`author` = 源稿参考文献逐字原文，且不得含任何其它
字段**（title/year/url 全空）——两条来源都覆盖：①解析失败兜底（`_verbatim`）；
②解析成功但类型无法归类。`_numbered_refs()` 为此把编号清单条目的 `raw` 第三元素
改成**去标号原文**，`footnotes.entry_to_bib()` 在 misc 分支单点强制。

本套件钉住（纯离线**合成夹具**；不读稿件库、不含任何真实文献原文；只写内存）：
  ① 期刊尾：18xx 年份、后缀剥除、长刊名（含冒号逗号）、题名尾引号；
  ② 书尾：出版地:出版社 三类真实型号（Heath / Henry Holt / Charles C. Thomas）；
  ③ `(Vol. N)`→volume、`(Rev. ed.)`→edition、裸年 `1960.`；
  ④ 章节：`In <eds> (Eds.), <booktitle> (pp. …)` → incollection（editor/pages）；
     `In his …` 去前缀；无编者 `In The …`；编辑者「首字母 姓」→「姓, 首字母」；
  ⑤ 在线词典：无页码的 `In <词典名>` → article（刊名=词典名）、`Retrieved`→note；
     条目级团体作者（`Synthetic University Press.`）不加花括号也认；
  ⑥ `(Season YYYY ed.)` 版本信息进 note（不落 edition，与发布 bib 口径一致）；
  ⑦ 类型完整性：书/章节/报告不得落 misc；显式 `(Report No. …)` → report；
  ⑧ misc 逐字交付：解析失败兜底与「解析成功但类型无法归类」两条来源，
     经 `_numbered_refs()` → `entry_to_bib()` 后都是 `author = {{整行原文}}`、
     无 title/year/url；非 misc 条目 `raw` 仍是题名（旧行为不变）；
  ⑨ 反例：题名里出现 4 位数字不被当成年份；普通条目字段逐字不变。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_refs_apa_struct.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footref  # noqa: E402
from slcn.footnotes import entry_to_bib  # noqa: E402
from slcn.wordentry import _numbered_refs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _p(line):
    return footref.parse_numbered_item(line)


def _fields(p, *names):
    return {n: getattr(p, n) for n in names}


# ── 合成夹具（全部为虚构条目，任何真实文献都不在此出现）─────────────────────
_JOURNAL_18XX = ("Doe, J., & Roe, R. (1896). A synthetic title about perception. "
                 "Synthetic Journal of Studies: An International Review, 3(4), 357–370.")
_JOURNAL_SUFFIX = ("Doe, J. (1999a). A synthetic title one. "
                   "Synthetic Journal, 31(3), 269–283.")
_JOURNAL_QUOTE_TITLE = ("Roe, R. (2015). The problem of “synthetic thinking.” "
                        "Synthetic Journal, 29, 417–430.")
_BOOK_HEATH = "Doe, J. (1910). A synthetic book title. Boston, MA: D. C. Heath & Co."
_BOOK_HOLT = "Roe, R. (1938). A synthetic logic title. New York, NY: Henry Holt and Company."
_BOOK_THOMAS = "Poe, E. 1960. The synthetic language of education. Springfield: Charles C. Thomas."
_BOOK_VOL = ("Doe, J. (2018). A synthetic inquiry title. (Vol. 7). "
             "Windsor, ON, Canada: University of Windsor.")
_BOOK_EDITION = "Doe, J. (1933). A synthetic reflective title. (Rev. ed.). Boston, MA: Heath."
_CHAPTER = ("Doe, J. (1992). Defining synthetic thinking. In S. P. Norris (Ed.), "
            "The synthetic generalizability of critical thinking (pp. 39–53). "
            "New York, NY: Synthetic College Press.")
_CHAPTER_SEP = ("Roe, R. (2022). A synthetic encyclopedia entry. In E. N. Zalta & "
                "U. Nodelman (Eds.), The Synthetic Encyclopedia of Philosophy (Winter 2022 ed.). "
                "Retrieved from https://example.org/entries/synthetic/")
_CHAPTER_ONLINE = ("Poe, E. (2014). A synthetic headword. In T. Northcote Toller, C. Sean, "
                   "& O. Tichy (Eds.), A Synthetic Dictionary Online. Faculty of Synthetic Arts, "
                   "Example University. https://example.org/word/1")
_GROUP_ND = ("Synthetic University Press. (n.d.). A made-up term, v.1, Etymology. "
             "In A Synthetic English Dictionary. Retrieved November 1, 2025, "
             "from https://example.org/doi/1")
_CHAPTER_HIS = ("Roe, R. (1968). A synthetic essay on thinking. In his A synthetic collection, "
                "volume 2: Collected synthetic essays 1929–1968 (pp. 494–510). "
                "Abingdon: Synthetic Routledge.")
_CHAPTER_NOED = ("Doe, J. (1973). A synthetic description. In The synthetic interpretation of "
                 "cultures: Selected essays (pp. 3–30). New York, NY: Synthetic Books.")
_REPORT = ("Roe, R. (1990). Synthetic report title (Report No. SYN-123). "
           "Synthetic Institution.")
_UNCLASSIFIABLE = ("Unknown, A. (2020). A synthetic unclassifiable entry without any "
                   "bibliographic tail at all.")

# ── ① 期刊尾 ────────────────────────────────────────────────────────────────
p = _p(_JOURNAL_18XX)
check("① 18xx 年份可解析（1896）", p.etype == "article" and p.year == "1896",
      str(_fields(p, "etype", "year")))
check("① 长刊名（含冒号+逗号）完整保留",
      p.journal == "Synthetic Journal of Studies: An International Review",
      repr(p.journal))
check("① 期刊卷期页码", (p.volume, p.number, p.pages) == ("3", "4", "357--370"),
      str((p.volume, p.number, p.pages)))
check("① 字符串 DOI/连字符转成 BibTeX 双连字符", "--" in p.pages, repr(p.pages))

p = _p(_JOURNAL_SUFFIX)
check("① 年份后缀剥除（1999a → 1999）", p.year == "1999" and "a" not in p.year, repr(p.year))
check("① 后缀条目类型仍为 article", p.etype == "article", p.etype)

p = _p(_JOURNAL_QUOTE_TITLE)
check("① 题名以引号收尾时仍走期刊尾", p.etype == "article", p.etype)
check("① 题名尾引号不被删掉", p.title.endswith("”"), repr(p.title))
check("① 题名不吞刊名", "Synthetic Journal" not in p.title, repr(p.title))

# ── ② 书尾：出版地: 出版社 ──────────────────────────────────────────────────
for name, line, pub, addr in (
        ("Heath", _BOOK_HEATH, "D. C. Heath & Co", "Boston, MA"),
        ("Henry Holt", _BOOK_HOLT, "Henry Holt and Company", "New York, NY"),
        ("Charles C. Thomas", _BOOK_THOMAS, "Charles C. Thomas", "Springfield")):
    p = _p(line)
    check(f"② 书尾 {name}：etype=book", p.etype == "book", p.etype)
    check(f"② 书尾 {name}：publisher", p.publisher == pub, repr(p.publisher))
    check(f"② 书尾 {name}：address", p.address == addr, repr(p.address))
    check(f"② 书尾 {name}：题名不含出版信息", ":" not in p.title and pub not in p.title,
          repr(p.title))
p = _p(_BOOK_THOMAS)
check("② 裸年（`I. 1960.`）可解析", p.year == "1960", repr(p.year))

# ── ③ Vol / edition ────────────────────────────────────────────────────────
p = _p(_BOOK_VOL)
check("③ (Vol. 7) → volume=7", p.volume == "7", repr(p.volume))
check("③ 双逗号出版地（Windsor, ON, Canada）可解析",
      p.address == "Windsor, ON, Canada" and p.publisher == "University of Windsor",
      str((p.address, p.publisher)))
check("③ (Vol. 7) 不进题名", "Vol" not in p.title, repr(p.title))

p = _p(_BOOK_EDITION)
check("③ (Rev. ed.) → edition=Revised", p.edition == "Revised", repr(p.edition))
check("③ 版次不进题名", "Rev" not in p.title, repr(p.title))

# ── ④ 章节：In <eds> (Eds.), <booktitle> (pp. …) ────────────────────────────
p = _p(_CHAPTER)
check("④ 章节 etype=incollection", p.etype == "incollection", p.etype)
check("④ 章节 booktitle", p.booktitle == "The synthetic generalizability of critical thinking",
      repr(p.booktitle))
check("④ 编辑者「首字母 姓」→「姓, 首字母」", p.editor == "Norris, S. P.", repr(p.editor))
check("④ 章节 pages", p.pages == "39--53", repr(p.pages))
check("④ 章节 publisher/address",
      (p.publisher, p.address) == ("Synthetic College Press", "New York, NY"),
      str((p.publisher, p.address)))
check("④ 章节题名不吞 booktitle", "generalizability" not in p.title, repr(p.title))

p = _p(_CHAPTER_SEP)
check("④ 季节版（Winter 2022 ed.）→ 进 note 不落 edition",
      p.edition == "" and "Winter 2022 ed." in p.note, str((p.edition, p.note)))
check("④ 章节 SEP 形态 etype=incollection", p.etype == "incollection", p.etype)
check("④ 多编辑者：Zalta, E. N. and Nodelman, U.",
      p.editor == "Zalta, E. N. and Nodelman, U.", repr(p.editor))
check("④ Retrieved URL 进 note", "https://example.org/entries/synthetic/" in p.note,
      repr(p.note))

p = _p(_CHAPTER_HIS)
check("④ `In his …` 去角色前缀", p.etype == "incollection" and p.booktitle.startswith("A synthetic collection"),
      str((p.etype, p.booktitle)))
check("④ `In his …` 无编者时 editor 为空", p.editor == "", repr(p.editor))
check("④ `In his …` pages", p.pages == "494--510", repr(p.pages))

p = _p(_CHAPTER_NOED)
check("④ 无编者 `In The …` 仍判 incollection", p.etype == "incollection", p.etype)
check("④ 无编者 booktitle/pages/出版尾",
      (p.booktitle, p.pages, p.publisher) == (
          "The synthetic interpretation of cultures: Selected essays", "3--30", "Synthetic Books"),
      str((p.booktitle, p.pages, p.publisher)))

# ── ⑤ 在线词典 / 团体作者 / n.d. ────────────────────────────────────────────
p = _p(_CHAPTER_ONLINE)
check("⑤ 无页码 In-eds 在线词典 → article（刊名=词典名）",
      p.etype == "article" and p.journal == "A Synthetic Dictionary Online",
      str((p.etype, p.journal)))
check("⑤ 在线词典 URL 不进 publisher",
      p.publisher == "" and "example.org" in p.note, str((p.publisher, p.note)))
check("⑤ 编辑者多词姓正确（Northcote Toller, T.）",
      "Northcote Toller, T." in p.note, repr(p.note))

p = _p(_GROUP_ND)
check("⑤ 团体作者 + n.d. 可解析", p.etype == "article" and p.year == "", str((p.etype, p.year)))
check("⑤ 团体作者写成花括号保护 {…}", p.author == "{Synthetic University Press}",
      repr(p.author))
check("⑤ `In <词典名>` 作刊名", p.journal == "A Synthetic English Dictionary", repr(p.journal))
check("⑤ n.d. 条目不写假年份", p.year == "", repr(p.year))
check("⑤ Retrieved 网址进 note", "https://example.org/doi/1" in p.note, repr(p.note))

# ── ⑥ 报告 / 类型完整性 ─────────────────────────────────────────────────────
p = _p(_REPORT)
check("⑥ 显式 (Report No. …) → report", p.etype == "report", p.etype)
check("⑥ report 题名不含报告号", "Report No" not in p.title, repr(p.title))

for name, line in (("书", _BOOK_HEATH), ("章节", _CHAPTER), ("在线章节", _CHAPTER_ONLINE),
                   ("报告", _REPORT)):
    p = _p(line)
    check(f"⑥ {name}不得落 misc", p.etype != "misc", p.etype)

# ── ⑦ 反例 ─────────────────────────────────────────────────────────────────
p = _p("Doe, J. (2019). A synthetic title with 1896 inside it. Synthetic Journal, 1, 1–2.")
check("⑦ 题名里的 4 位数字不被当成年份", p.year == "2019", repr(p.year))
check("⑦ 反例：题型名/卷页正确", p.title == "A synthetic title with 1896 inside it", repr(p.title))

# ── ⑧ misc 逐字交付契约（两条来源，端到端到 .bib 文本）──────────────────────
_LINES = [
    "[1] " + _CHAPTER,
    "[2] " + _UNCLASSIFIABLE,
    "[3] " + "Completely unparseable synthetic reference line, no structure at all",
]
_entries, _num2key, _raw_by_key = _numbered_refs(_LINES)


def _by_type(ts):
    return [e for _k, e, _r in _entries if e.get("_type") == ts]


_misc = _by_type("misc")
check("⑧ 两条来源都落 misc（类型不可归类 + 解析失败兜底）", len(_misc) == 2, str(len(_misc)))
check("⑧ 章节仍为 incollection（不误落 misc）", len(_by_type("incollection")) == 1,
      str([e.get("_type") for _k, e, _r in _entries]))

_misc_item = next(((k, e, r) for k, e, r in _entries if e.get("_type") == "misc"), None)
check("⑧ misc 条目的 raw 是**去标号原文**",
      _misc_item is not None and _misc_item[2].startswith("Unknown, A."),
      repr(_misc_item[2] if _misc_item else None))
_bib = entry_to_bib(*_misc_item)
check("⑧ misc 交付只有 author 一个字段", "@misc" in _bib and "title" not in _bib
      and "year" not in _bib and "url" not in _bib, _bib)
check("⑧ misc 交付 author = {{逐字原文}}（花括号保护）",
      "author    = {{" + _UNCLASSIFIABLE + "}}" in _bib, _bib)

# 解析失败兜底（来源①）：源行短得解析不出题名，`_numbered_refs` 的短题名分支会丢；
# 这里直接构造 `_verbatim` 形态条目，验证交付层单点强制。
_verbatim_entry = {"_type": "misc", "_verbatim": True, "author": "Some synthetic fallback line",
                   "title": "should-not-be-printed"}
_bib2 = entry_to_bib("Fallback", _verbatim_entry, raw="Some synthetic fallback line")
check("⑧ 解析失败兜底也走逐字交付", "@misc{Fallback,\n  author    = {{Some synthetic fallback line}}\n}"
      in _bib2 and "title" not in _bib2, _bib2)

# 非 misc 条目 raw 仍是题名（旧行为不变）
_chapter_item = next(((k, e, r) for k, e, r in _entries if e.get("_type") == "incollection"), None)
check("⑧ 非 misc 条目 raw 仍为题名（旧行为）",
      _chapter_item is not None and _chapter_item[2] == "Defining synthetic thinking",
      repr(_chapter_item[2] if _chapter_item else None))
_bib3 = entry_to_bib(*_chapter_item)
check("⑧ 非 misc 条目照常输出结构化字段",
      "@incollection" in _bib3 and "booktitle" in _bib3 and "title" in _bib3, _bib3)

# ⑨ edition/editor 两格确实进了条目 dict（渲染层据此写字段）
_entries2, _n2, _r2 = _numbered_refs(["[1] " + _BOOK_EDITION, "[2] " + _CHAPTER])
_ed_entry = next(((k, e) for k, e, _r in _entries2
                  if e.get("title") == "A synthetic reflective title"), None)
check("⑨ edition 进条目 dict", _ed_entry is not None and _ed_entry[1].get("edition") == "Revised",
      repr(_ed_entry[1].get("edition") if _ed_entry else None))
_ed_item = next(((k, e, r) for k, e, r in _entries if e.get("title") == "Defining synthetic thinking"), None)
check("⑨ editor 进条目 dict",
      _ed_item is not None and _ed_item[1].get("editor") == "Norris, S. P.",
      repr(_ed_item[1].get("editor") if _ed_item else None))

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
