#!/usr/bin/env python3.14
"""期 87 引用解析 P0 缺陷的最小回归（2026-10-18）。

覆盖 `EditToolBox-notes/期87-子案例核查-2026-10-18.md` §3 的 P0-2..P0-6：
  P0-2 作者-年份 + 引号题名条目：journal/volume/pages 丢失（Word 通道 refs_to_bib）。
  P0-3 编号清单非数字期号 `66(Supplement)` / 字母前缀页码 `S362-S378`。
  P0-4 APA 体例编号清单（作者缩写 + (YYYYa). Title. Journal, vol(issue), pages.），
       以及「题名切错不再静默丢条」。
  P0-5 混合体例：首条无编号不丢；`1957. 题名` 年份不混进题名；
       英文题名的冒号/逗号不被误拆成出版地/出版社。
  P0-6 英文脚注文献（`(eds.) …, … Pub, 2023.`）不再被降级成散文注释。

只跑断言，不联网、不写 out/、不碰真实库。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_refs_p0_2026_10_18.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footref  # noqa: E402
from slcn.docxsource import (  # noqa: E402
    _looks_short_bibliographic_note,
    _numbered_refs,
    parse_reference_line,
    refs_to_bib,
)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══ P0-2：作者-年份 + 引号题名 → 刊名/卷/页 ════════════════════════════════════
print("── P0-2 引号题名条目（Word 通道 refs_to_bib）──")
HAJEK = ('A. Hájek, 2020, “Counterfactual skepticism and antecedent-contextualism”, '
         'Synthese, 199: 637-659.')
e = parse_reference_line(HAJEK)
check("P0-2 parse_reference_line：rest 是题名之后的出版信息（不含引号题名）",
      e is not None and "Counterfactual" not in e.rest and "Synthese" in e.rest,
      repr(e.rest if e else None))
bib = refs_to_bib([e]) if e else ""
check("P0-2 refs_to_bib：@article 含 journal=Synthese / volume=199 / pages=637--659",
      "@article{AHjek2020," in bib and "journal = {Synthese}" in bib
      and "volume  = {199}" in bib and "pages   = {637--659}" in bib, bib)
# 反例：题名里没有引号的英文书籍（旧行为）不被破坏
book = parse_reference_line("T. Williamson, 2000, Knowledge and Its Limits, New York: Oxford University Press.")
bb = refs_to_bib([book]) if book else ""
check("P0-2 反例：英文书籍仍解析 publisher/address（不误伤）",
      book is not None and book.kind == "M"
      and "publisher = {Oxford University Press}" in bb and "address" in bb, bb)

# ══ P0-3：非数字期号 + 字母前缀页码 ═════════════════════════════════════════
print("\n── P0-3 编号清单：66(Supplement): S362-S378 ──")
FITELSON = ('B. Fitelson, 1999, “The plurality of bayesian measures of confirmation and '
            'the problem of measure sensitivity”, Philosophy of Science, '
            '66(Supplement): S362-S378.')
p = footref.parse_numbered_item(FITELSON)
check("P0-3 类型 @article（不再退化成 misc）", p.etype == "article", p.etype)
check("P0-3 journal=Philosophy of Science / volume=66 / number=Supplement",
      (p.journal, p.volume, p.number) == ("Philosophy of Science", "66", "Supplement"),
      f"{p.journal!r} {p.volume!r} {p.number!r}")
check("P0-3 pages=S362--S378（字母前缀页码）", p.pages == "S362--S378", repr(p.pages))

# ══ P0-4：APA 体例 ══════════════════════════════════════════════════════════
print("\n── P0-4 APA 编号清单 ──")
APA = ("Bailin, S., Case, R., Coombs, J. R., & Daniels, L. B. (1999a). "
       "Common misconceptions of critical thinking. Journal of Curriculum Studies, "
       "31(3), 269–283.")
a = footref.parse_numbered_item(APA)
check("P0-4 作者：Bailin, S. and Case, R. and Coombs, J. R. and Daniels, L. B.",
      a.author == "Bailin, S. and Case, R. and Coombs, J. R. and Daniels, L. B.",
      a.author)
check("P0-4 title 是完整题名（不是 `S.`）",
      a.title == "Common misconceptions of critical thinking", repr(a.title))
check("P0-4 journal/volume/number/pages 全对 + @article",
      (a.etype, a.journal, a.volume, a.number, a.pages)
      == ("article", "Journal of Curriculum Studies", "31", "3", "269--283"),
      f"{a.etype} {a.journal!r} {a.volume!r} {a.number!r} {a.pages!r}")
BROWN = ("Brown, M. J. (2012). John Dewey’s logic of science. HOPOS: The Journal of the "
         "International Society for the History of Philosophy of Science, 2(2), 258–306.")
b = footref.parse_numbered_item(BROWN)
check("P0-4 题名含句点/刊名含冒号的 APA 条目也切对",
      b.title == "John Dewey’s logic of science"
      and b.journal == "HOPOS: The Journal of the International Society for the History of Philosophy of Science",
      f"{b.title!r} / {b.journal!r}")
APA_BOOK = ("Bailin, S., & Battersby, M. (2015). Reason in the balance: An inquiry approach "
            "to critical thinking. Indianapolis, IN: Hackett Publishing.")
ab = footref.parse_numbered_item(APA_BOOK)
check("P0-4 APA 书：title 不含出版信息、address/publisher 正确",
      ab.title == "Reason in the balance: An inquiry approach to critical thinking"
      and ab.address == "Indianapolis, IN" and ab.publisher == "Hackett Publishing",
      f"{ab.title!r} / {ab.address!r} / {ab.publisher!r}")

# 不再静默丢：短题名条目保留并写 note（旧实现直接 continue）
lines = [
    "[1] J. Smith, 2000, “X”, Journal of Tests, 1(2): 3-4.",
    "[2] J. Doe, 2001, “A proper long title”, Journal of Tests, 2(3): 5-6.",
]
out, num2key, raw_by_key = _numbered_refs(lines)
check("P0-4 短题名不再静默丢：保留 2 条并给 note", len(out) == 2
      and [k for k, _e, _r in out], f"{len(out)} 条")
check("P0-4 短题名 note 写明「题名解析可疑」",
      any("题名解析可疑" in (e.get("note") or "") for _k, e, _r in out),
      str([e.get("note") for _k, e, _r in out]))

# ══ P0-5：混合体例/中文清单 ══════════════════════════════════════════════════
print("\n── P0-5 混合体例（首条无编号 / 年份混进题名 / 冒号错拆）──")
mix_lines = [
    "郑伟宏. 《佛家逻辑通论》. 上海：复旦大学出版社，1996年，第45-78页.",
    "[2] 舍尔巴茨基（著），宋立道等（译），《佛教逻辑》，北京：商务印书馆。2019年。",
    "[3] D.N.Shastri，1957. The Philosophy of Nyaya-Vaisesika, and Its Conflict with "
    "the Buddhist Dignaga School: Critique of Indian Realism, Bharatiya Vidya Prakashan Press.",
]
out5, num5, _raw5 = _numbered_refs(mix_lines)
first = out5[0][1] if out5 else {}
check("P0-5 首条无编号不丢：郑伟宏《佛家逻辑通论》进条目",
      any(e.get("author", "").startswith("郑伟宏") and "佛家逻辑通论" in e.get("title", "")
          for _k, e, _r in out5),
      str([(e.get("author"), e.get("title")) for _k, e, _r in out5]))
check("P0-5 首条出版信息正确：上海 / 复旦大学出版社 / 1996 / 45--78",
      first.get("address") == "上海" and first.get("publisher") == "复旦大学出版社"
      and first.get("year") == "1996" and first.get("pages") == "45--78",
      repr({k: first.get(k) for k in ("address", "publisher", "year", "pages")}))
shastri = next((e for _k, e, _r in out5 if "Shastri" in e.get("author", "")), {})
check("P0-5 Shastri：year=1957 不再混进 title",
      shastri.get("year") == "1957" and not shastri.get("title", "").startswith("1957")
      and shastri.get("title", "").startswith("The Philosophy of Nyaya-Vaisesika"),
      repr(shastri.get("title")))
check("P0-5 Shastri：publisher=Bharatiya Vidya Prakashan Press，不从题名冒号错拆 address",
      shastri.get("publisher") == "Bharatiya Vidya Prakashan Press"
      and shastri.get("address") == "", repr((shastri.get("publisher"), shastri.get("address"))))
check("P0-5 编号→键映射仍从 [2] 开始（首条无编号不占用编号）",
      2 in num5 and 3 in num5, str(sorted(num5)))

# ══ P0-6：英文脚注文献不再被判成散文 ═════════════════════════════════════════
print("\n── P0-6 英文脚注文献（Brill Academic Pub 型）──")
BOLDUC = ("M. Bolduc & David A. Frank (eds.), The Intellectual and Cultural Origins of "
          "Chaïm Perelman and Lucie Olbrechts-Tyteca’s New Rhetoric Project: Commentaries "
          "on and Translations of Texts, 1933-1958, Brill Academic Pub, 2023.")
check("P0-6 `(eds.) + 年份 + Pub` 脚注被认作文献（不再降级成注释）",
      _looks_short_bibliographic_note(BOLDUC) is True)
PROSE = ("See also the discussion in the previous chapter, which argues that the "
         "distinction is not fundamental and fails for various reasons in the end.")
check("P0-6 反例：普通英文散文仍不被认作文献",
      _looks_short_bibliographic_note(PROSE) is False)

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + repr(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
