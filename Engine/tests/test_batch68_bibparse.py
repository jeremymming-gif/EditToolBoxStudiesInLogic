#!/usr/bin/env python3.14
r"""批68 B-021：Word 通道文献区解析修复（86-10 / 90-06 / 90-07 三条真实缺陷）。

缺陷（批67 B-019 抽样定性，三条都会把空题名/碎片印进参考文献区）
------------------------------------------------------------------
① **同段多文献只解析第一条**（86-10 型）：文末编号清单里一行写了 2–3 条文献
   （`[4] A,2014, …; B,2018, … .C,2019, …`），`_numbered_refs()` 一条编号 = 一条
   条目 → 第 2 条起被吞并（缺 Kelp/McGrath/Williamson 型条目）。生成 15 vs 人工 18。
② **译著拆条**（90-06 型）：`X著，Y译，《书名》，出版社，年。` 被人年制解析器按
   逗号切错 → `title = {Y译}`（题名位上是译者）、`rest = 《书名》…`；另有空题名
   的脚注引注条目（`ref01…`）没并回书目。生成 14 vs 人工 11。
③ **中文条目字段错位**（90-07 型）：编号清单里中文条目被切成
   `author = {作者：《题名》}` / `title = {期刊名}` / `title = {译者}`，另有 3 条
   空题名脚注碎片。生成 14 vs 人工 11。

修法（B-021，`bibnorm.repair_word_refs()`，**不重写解析器**）
----------------------------------------------------------
在 `parse_docx()` 之后做一次保守的「解析结果修复」：只认形状 + 只合并有证据的
条目；任何一步判据不满足 → **保留原条目 + warning**（不猜、不丢原文）。三条
判据分别是：多文献行按 `姓名,YYYY` 锚点切分并逐段复用现有行解析器；译著按
`著/译/《题》` 形状把 title/author/rest 归位；中文编号条目按
`作者：《题名》，《期刊》，年` / `作者：《书名》，译者译，出版地：出版社，年`
重解析；空题名碎片按「作者姓氏 + 题名相似度」匹配既有条目后合并，并把正文
`\parencite{旧键}` 改写成仍存活的键。

本套件钉住（纯离线合成夹具；**不读稿件库、不含作者原文**；只写内存）：
  ① 多文献行拆分：15 → 18 条、逐段标题正确、同题名重复段不重复加、宿主条目字段归位；
  ② 译著归位：title/author/kind 修正；③ 空题名碎片合并：14 → 11、无空题名、
     正文引用键改写；④ 中文编号条目重解析：字段归位、碎片合并、14 → 11；
  ⑤ 反例：普通文档零变化（repair 返回空、三个属性逐字段相同）；
  ⑥ 失败路径：拆不动的多文献行保留原条目 + warning（不新增条目）；
  ⑦ 幂等：连跑两次第二次零变化。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        tests/test_batch68_bibparse.py
"""
import copy
import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibnorm  # noqa: E402
from slcn.docxsource import RefEntry  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _ref(author, year, title, kind="M", rest="", key=""):
    return RefEntry(raw="", author=author, year=str(year), title=title, kind=kind,
                    rest=rest, key=key)


def _doc(refs=(), bib_entries=(), body="", raw_by_key=None, raw_lines=(), num2key=None):
    """最小 DocxDoc 形状（只带 repair 需要的属性）。"""
    return SimpleNamespace(
        refs=copy.deepcopy(list(refs)),
        bib_entries=copy.deepcopy(list(bib_entries)),
        body=body,
        ref_raw_by_key=dict(raw_by_key or {}),
        ref_raw_lines=list(raw_lines),
        ref_key_by_num=dict(num2key or {}),
    )


def _snap(doc):
    """可比较快照（refs 逐字段、bib_entries 深拷贝、body）。"""
    refs = [tuple(sorted((f, getattr(r, f, "")) for f in
                         ("author", "year", "title", "kind", "rest", "key")))
            for r in doc.refs]
    return (refs, copy.deepcopy(doc.bib_entries), doc.body)


def _titles(entries):
    return [str(e.get("title") or "") for _k, e, _r in entries]


# ══════════════════════════════════════════════════════════════════════════
# ① 86-10 型：同段多文献 —— 15 条 → 18 条（拆 [4] 与 [13]，重复题名段不加）
# ══════════════════════════════════════════════════════════════════════════
_LINES_8610 = [
    "[1] Aaa,B,1994, “Vagueness” ,London: Routledge.",
    "[2] Aaa,B,2000, “Knowledge and Limits”,Oxford: Oxford University Press.",
    "[3] Ccc,J. et al,2019, “Primates and Ignorance?”,Cognition,190(C):72-80.",
    "[4] Alpha,A,2014, “Alpha Book”; Beta.B,2018,“Beta Book”.Routledge."
    "Gamma,C,2019,Gamma Book,Oxford: Oxford University Press.",
    "[5] Delta,J,2012, “Mindreading Cases”, in Knowledge Ascription,Jessica Brown (ed.),"
    "pp. 171-190,Oxford University Press.",
    "[6] Epsilon,A & Zeta, J.H. (eds.), “Putting Knowledge to Work”, Oxford: "
    "Oxford University Press.",
    "[7] Eta,N,2021,“Epistemology without Guidance”, Philosophical Studies,179(1):163-196.",
    "[8] Theta, Yong (ed.),2022,“Sosa Encountering Chinese Philosophy”, New York: "
    "Bloomsbury Academic.",
    "[9] Iota, J., & Kappa, T,2001, “Knowing How”, The Journal of Philosophy, 98(8):411-444.",
    "[10] Lambda,C,2020, “Dialogical Roots of Deduction”, NY, USA: Cambridge University Press.",
    "[11] Echo, E. & Delta, D,2020, “Zeta Book”,Oxford: Oxford University Press.",
    "[12] Delta, T,2013, “Modal Logic as Metaphysics”,Oxford: Oxford University Press.",
    "[13] Delta, D,2021, “Delta Book”,enlarged ed,Oxford: Wiley-Blackwell；"
    "Delta, D,2020, “Epsilon Book”,Oxford: Oxford University Press；"
    "Echo, E. & Delta, D,2020, “Zeta Book”,Oxford: Oxford University Press.",
    "[14] 未名，“怎么理解辩证法？”，系统科学学报，2020年第2期，第1-6页。",
    "[15] 某甲，对与错的真相：四人对话录,徐某译,上海:上海人民出版社,2017年。",
]

_ENTRIES_8610 = []
_RAW8610 = {}
_NUM2KEY_8610 = {}
for _i, _line in enumerate(_LINES_8610, 1):
    _key = f"Fake{_i:02d}"
    _ENTRIES_8610.append((_key, {"_type": "book", "author": f"Author{_i}",
                                 "title": f"Title {_i}", "year": str(1900 + _i)}, ""))
    _RAW8610[_key] = _line
    _NUM2KEY_8610[_i] = _key
# 模拟解析器在 [4]/[13] 上把后续文献吞进第一段的字段（真实缺陷形状）
_ENTRIES_8610[3][1].update({"author": "Alpha,A",
                            "title": "Alpha Book",
                            "publisher": "Beta Book”.Routledge",
                            "address": "“Beta Book"})
_ENTRIES_8610[10][1].update({"author": "Echo, E. & Delta, D",
                             "title": "Zeta Book", "year": "2020"})
_ENTRIES_8610[12][1].update({"author": "Delta, D",
                             "title": "Delta Book",
                             "publisher": "Wiley-Blackwell；Delta",
                             "address": "Oxford"})
_doc8610 = _doc(bib_entries=_ENTRIES_8610, raw_by_key=_RAW8610,
                raw_lines=_LINES_8610, num2key=_NUM2KEY_8610)
_seen_before = _snap(_doc8610)
_notes8610 = bibnorm.repair_word_refs(_doc8610)
_notes8610, _warns8610 = _notes8610 if isinstance(_notes8610, tuple) else (_notes8610, [])
_entries8610 = _doc8610.bib_entries
check("① 多文献行拆分后 15 → 18 条（86-10 目标数）",
      len(_entries8610) == 18, str(len(_entries8610)))
_t8610 = _titles(_entries8610)
check("① 拆出 Beta Book / Gamma Book（第二段、第三段各 1 条）",
      "Beta Book" in _t8610 and "Gamma Book" in _t8610, str(_t8610[3:6]))
check("① 拆出 Epsilon Book（[13] 的第二段）",
      "Epsilon Book" in _t8610, "")
check("① 同题名重复段（Zeta Book）不重复成条：题名只出现 1 次",
      _t8610.count("Zeta Book") == 1, str(_t8610.count("Zeta Book")))
check("① 宿主条目字段归位：Alpha Book 的 address 不再是下一段的书名",
      "Beta Book" not in str(_doc8610.bib_entries[3][1].get("address", "")),
      str(_doc8610.bib_entries[3][1].get("address", "")))
check("① [13] 宿主条目字段归位：publisher 里不再有下一条作者",
      "Delta" not in str(_entries8610[12][1].get("publisher", "")),
      str(_entries8610[12][1].get("publisher", "")))
check("① 新增条目都带 author/title/year（不是空壳）",
      all((e.get("author") and e.get("title") and e.get("year"))
          for _k, e, _r in _entries8610[13:16]), "")
check("① 拆分有日志说明（≥1 条 note）", len(_notes8610) >= 1, str(_notes8610))
check("① 单文献行逐条不动（前 3 条标题/键不变）",
      [k for k, _e, _r in _entries8610[:3]] == ["Fake01", "Fake02", "Fake03"], "")

# 幂等：第二次跑零变化
_after_first = _snap(_doc8610)
_notes2, _warns2 = bibnorm.repair_word_refs(_doc8610)
check("① 幂等：同一文档第二次 repair 零变化（无新条目/无新日志）",
      _snap(_doc8610) == _after_first and not _notes2 and not _warns2,
      str(_notes2)[:120])

# ══════════════════════════════════════════════════════════════════════════
# ② + ③ 90-06 型：译著拆条 + 空题名脚注碎片 —— 14 条 → 11 条
# ══════════════════════════════════════════════════════════════════════════
_REF_TARGET = _ref("Frankfurt, H.", 1971, "Freedom of Will and a Concept of a Person",
                   "M", "The Journal of Philosophy, Vol. 68 (1), pp. 5-20.",
                   key="Target1971")
_REF_WATSON = _ref("Watson, G.", 1975, "Free Agency", "M",
                   "The Journal of Philosophy, Vol. 72 (8), pp. 205-220.",
                   key="Watson1975")
_REFS_9006 = [
    _ref("Writer著", 2012, "TransOne、TransTwo译", "J",
         "《Book One》，Some Press，2012。"),
    _ref("Writer2著", 1947, "TransThree译", "J",
         "《Book Two》，Other Press，1947。"),
    _ref("Audi, R.", 2013, "Moral Perception", "M", "Princeton: Princeton University Press.", key="Audi2013"),
    _ref("Baker, L. R.", 2012, "From Consciousness to Self-consciousness", "C",
         "in Facets of Self-Consciousness, New York: Rodopi."),
    _REF_TARGET,
    _ref("Frankfurt, H.", 1998, "The Importance of What We Care About", "M",
         "New York: Cambridge University Press."),
    _ref("Frankfurt, H.", 1999, "Necessity", "M",
         "Volition, and Love, Cambridge: Cambridge University Press."),
    _REF_WATSON,
    _ref("Watson, G.", 2004, "Agency and Answerability", "M",
         "New York: Oxford University Press."),
    _ref("Wolf, S.", 1987, "Sanity and the Metaphysics of Responsibility", "C",
         "in Responsibility, Character, and the Emotions, pp. 46-62. Cambridge: "
         "Cambridge University Press, 1987."),
]
_ENTRIES_9006 = [
    ("ref01", {"_type": "article",
               "note": "Cf. Frankfurt, H., “Freedom of Will and a Concept of a Person,” "
                       "The Journal of Philosophy, Vol. 68 (1), pp. 5-20"}, ""),
    ("ref02", {"_type": "article",
               "note": "Cf. Watson, G., “Free Agency,” The Journal of Philosophy, "
                       "Vol. 72 (8), pp. 205-220"}, ""),
    ("ref03", {"_type": "article",
               "note": "Frankfurt, H., “Freedom of the Will and a Concept of a Person,” "
                       "The Journal of Philosophy, Vol. 68 (1), p. 10"}, ""),
    ("Wolf1987", {"_type": "incollection", "author": "Cf. Wolf, S",
                  "title": "“Sanity and the Metaphysics of Responsibility, ” in "
                           "Responsibility, Character, and the Emotions"}, ""),
]
_BODY_9006 = (
    "甲\\parencite[5--20]{ref01}乙\\parencite[205--220]{ref02}"
    "丙\\parencite[10]{ref03}丁"
)
_doc9006 = _doc(refs=_REFS_9006, bib_entries=_ENTRIES_9006, body=_BODY_9006)
_notes9006, _warns9006 = bibnorm.repair_word_refs(_doc9006)
_entries9006 = _doc9006.bib_entries
check("② 译著 + 空题名碎片修复后 14 → 11 条（90-06 目标数）",
      len(_doc9006.refs) + len(_entries9006) == 11,
      f"{len(_doc9006.refs)}+{len(_entries9006)}")
check("② 译著题名归位：title 不再是「译者」串",
      _doc9006.refs[0].title == "Book One" and _doc9006.refs[1].title == "Book Two",
      repr((_doc9006.refs[0].title, _doc9006.refs[1].title)))
check("② 译著作者归位：author 去掉「著」字",
      _doc9006.refs[0].author == "Writer" and _doc9006.refs[1].author == "Writer2",
      repr((_doc9006.refs[0].author, _doc9006.refs[1].author)))
check("② 译著类型归位为书籍（原被错判为期刊 J）",
      _doc9006.refs[0].kind == "M" and _doc9006.refs[1].kind == "M", "")
check("② 空题名脚注碎片合并：ref01/ref02/ref03 不再单独成条",
      [k for k, _e, _r in _entries9006] == ["Wolf1987"],
      str([k for k, _e, _r in _entries9006]))
check("② 参考文献区无空题名条目（refs + entries 全有 title）",
      all((r.title or "").strip() for r in _doc9006.refs)
      and all((e.get("title") or "").strip() for _k, e, _r in _entries9006), "")
check("② 正文引用键改写：ref01/ref02/ref03 → 存活条目的键（不留悬空 cite）",
      "ref01" not in _doc9006.body and "ref02" not in _doc9006.body
      and "ref03" not in _doc9006.body and _doc9006.body.count("\\parencite") == 3,
      _doc9006.body[:120])
check("② 引用位置参数保留（[5--20] 还在）",
      "\\parencite[5--20]{" in _doc9006.body and "\\parencite[10]{" in _doc9006.body, "")
check("② 有日志说明修复了译著/碎片（≥2 条 note）", len(_notes9006) >= 2, str(_notes9006))
check("② 未命中判据的普通条目逐字不动（第 3–9 条 refs 字段不变）",
      all(_REFS_9006[i].title == _doc9006.refs[i].title for i in range(2, 10))
      and _REFS_9006[8].author == "Watson, G.", "")

# ══════════════════════════════════════════════════════════════════════════
# ④ 90-07 型：中文编号条目字段错位 + 空题名碎片 —— 14 条 → 11 条
# ══════════════════════════════════════════════════════════════════════════
_CN_LINES = {
    5: "[5] 丙：《丙题名较长》，丁（主编）：《戊书》上卷，北京：商务印书馆，1982年。",
    6: "[6] 己：《己文题一》，《庚学报》，2024年第1期。",
    7: "[7] 己：《己文题二》，《辛分析》2025年第1期。",
    8: "[8] 壬•癸：《子书》（第四版），丑、寅 译，北京：北京大学出版社，2012年。",
    9: "[9] 卯：《卯文集》第三卷，辰等译，北京：商务印书馆，1979年。",
    11: "[11] 己：《己文题三》，《辛分析》2026年第1期。",
}
_EN_LINES = {
    1: "[1] R. Camap, “The Aim of Inductive Logic”in Logic, Methodology and Philosophy "
       "of Science, Stanford: Stanford University Press, 1962.",
    2: "[2] B.de Finetti, “Foresight: Its Logical Laws”, in Studies in Subjective "
       "Probability, New York: John Wiley, 1964.",
    3: "W. C. Salmon, The Foundations of Scientific Inference, Pittsburgh : "
       "University of Pittsburgh Press, 1966.",
    4: "H. Reichenbach, The Theory of Probability, Berkeley: University of California "
       "Press, 1949.",
    10: "[10] W. C. Salmon, “The Appraisal of Theories”, in Proceedings of the "
        "Biennial Meeting, 1990, Vol. 2.",
}
_ENTRIES_9007 = [
    ("C1", {"_type": "book", "author": "Camap, R.", "title": "The Aim of Inductive Logic",
            "publisher": "Stanford University Press", "address": "Stanford",
            "year": "1962"}, ""),
    ("C2", {"_type": "incollection", "author": "de Finetti, B.",
            "title": "Foresight: Its Logical Laws", "publisher": "John Wiley",
            "address": "New York", "year": "1964"}, ""),
    ("C3", {"_type": "book", "author": "Salmon, W. C.",
            "title": "The Foundations of Scientific Inference",
            "publisher": "University of Pittsburgh Press", "address": "Pittsburgh",
            "year": "1966"}, ""),
    ("C4", {"_type": "book", "author": "Reichenbach, H.",
            "title": "The Theory of Probability",
            "publisher": "University of California Press", "address": "Berkeley",
            "year": "1949"}, ""),
    # ── 以下 5 条是中文条目被切错字段的真实形状（作者含：《》、题名=期刊/译者）──
    ("C5", {"_type": "incollection", "author": "丁（主编）", "title": "戊书》上卷",
            "publisher": "商务印书馆", "address": "北京", "year": "1982"}, ""),
    ("C6", {"_type": "misc", "author": "己：《己文题一》", "title": "庚学报",
            "number": "1", "year": "2024"}, ""),
    ("C7", {"_type": "misc", "author": "己：《己文题二》", "title": "辛分析》2025年第1期",
            "year": "2025"}, ""),
    ("C8", {"_type": "book", "author": "壬•癸：《子书》（第四版）", "title": "丑、寅 译",
            "publisher": "北京大学出版社", "address": "北京", "year": "2012"}, ""),
    ("C9", {"_type": "book", "author": "卯：《卯文集》第三卷", "title": "辰等译",
            "publisher": "商务印书馆", "address": "北京", "year": "1979"}, ""),
    ("C10", {"_type": "misc", "author": "Salmon, W. C.",
             "title": "The Appraisal of Theories", "year": "1990"}, ""),
    ("C11", {"_type": "misc", "author": "己：《己文题三》", "title": "辛分析》2026年第1期",
             "year": "2026"}, ""),
    # ── 3 条空题名脚注碎片（note-only，正文里有 \parencite 指向）──
    ("ref03", {"_type": "misc",
               "note": _CN_LINES[5].split("] ", 1)[1] + "第400页。"}, ""),
    ("ref04", {"_type": "misc",
               "note": _CN_LINES[8].split("] ", 1)[1] + "第123页。"}, ""),
    ("ref05", {"_type": "article",
               "note": "W. C. Salmon, “The Appraisal of Theories”, in Proceedings of "
                       "the Biennial Meeting, 1990, Vol. 2, p. 331"}, ""),
]
_RAW_9007 = dict(_CN_LINES)
_RAW_9007.update(_EN_LINES)
_RAW_9007_BY_KEY = {k: _RAW_9007[i] for i, k in enumerate(
    ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"], 1)}
_NUM2KEY_9007 = {i: k for i, k in enumerate(
    ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"], 1)}
_BODY_9007 = "甲\\parencite{ref03}乙\\parencite{ref04}丙（\\parencite{ref05}，第 331 页）"
_doc9007 = _doc(bib_entries=_ENTRIES_9007, body=_BODY_9007,
                raw_by_key=_RAW_9007_BY_KEY, raw_lines=list(_RAW_9007.values()),
                num2key=_NUM2KEY_9007)
_notes9007, _warns9007 = bibnorm.repair_word_refs(_doc9007)
_entries9007 = _doc9007.bib_entries
check("④ 中文条目重解析 + 碎片合并后 14 → 11 条（90-07 目标数）",
      len(_entries9007) == 11, str(len(_entries9007)))
_by_key = {k: e for k, e, _r in _entries9007}
check("④ 中文期刊条目字段归位：[6] author/title/journal/number/year",
      _by_key["C6"].get("author") == "己" and _by_key["C6"].get("title") == "己文题一"
      and _by_key["C6"].get("journal") == "庚学报"
      and _by_key["C6"].get("number") == "1"
      and _by_key["C6"].get("year") == "2024"
      and _by_key["C6"].get("_type") == "article",
      str(_by_key["C6"]))
check("④ 中文编著条目字段归位：[5] author/title/booktitle/publisher",
      _by_key["C5"].get("author") == "丙"
      and _by_key["C5"].get("title") == "丙题名较长"
      and "戊书" in str(_by_key["C5"].get("booktitle", ""))
      and _by_key["C5"].get("publisher") == "商务印书馆"
      and _by_key["C5"].get("_type") == "incollection",
      str(_by_key["C5"]))
check("④ 中文译著条目字段归位：[8] title=子书（第四版）、author 不再是《题》串",
      _by_key["C8"].get("title", "").startswith("子书")
      and "《" not in str(_by_key["C8"].get("author", ""))
      and _by_key["C8"].get("publisher") == "北京大学出版社",
      str(_by_key["C8"]))
check("④ [9] 卷次并入题名（第三卷）、author 干净",
      "卯文集" in str(_by_key["C9"].get("title", ""))
      and "第三卷" in str(_by_key["C9"].get("title", ""))
      and "《" not in str(_by_key["C9"].get("author", "")), str(_by_key["C9"]))
check("④ 碎片 ref03/ref04/ref05 不再单独成条",
      not any(k in ("ref03", "ref04", "ref05") for k in _by_key), str(list(_by_key)))
check("④ 参考文献区无碎片题名（无「》」残片、无「译」题名）",
      not any(("》" in t) or t.strip().endswith("译") for t in _titles(_entries9007)),
      str([t for t in _titles(_entries9007) if "》" in t or t.strip().endswith("译")]))
check("④ 正文引用键改写：ref03/ref04/ref05 → 存活条目的键",
      "ref03" not in _doc9007.body and "ref04" not in _doc9007.body
      and "ref05" not in _doc9007.body and _doc9007.body.count("\\parencite") == 3,
      _doc9007.body[:120])
check("④ 英文条目逐字不动（C1–C4/C10 字段不变）",
      _by_key["C1"] == _ENTRIES_9007[0][1] and _by_key["C10"] == _ENTRIES_9007[9][1], "")
check("④ 有日志说明重解析/合并（≥2 条 note）", len(_notes9007) >= 2, str(_notes9007))
check("④ 无 warning（三条判据全部按证据走通）", not _warns9007, str(_warns9007))

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 反例：普通文档零变化（默认路径逐字节不变）
# ══════════════════════════════════════════════════════════════════════════
_NORMAL_REFS = [
    _ref("Russell, B.", 1919, "Introduction to Mathematical Philosophy", "M",
         "London: George Allen and Unwin."),
    _ref("Kripke, S.", 1980, "Naming and Necessity", "M", "Oxford: Blackwell."),
]
_NORMAL_ENTRIES = [
    ("ref01", {"_type": "misc", "note": "一条普通脚注引注，不是文献碎片"},
     "一条普通脚注引注，不是文献碎片"),
    ("X2001", {"_type": "article", "author": "X, Y", "title": "Normal Article",
               "year": "2001"}, ""),
]
_NORMAL_BODY = "正文\\parencite{ref01}引用"
_docN = _doc(refs=_NORMAL_REFS, bib_entries=_NORMAL_ENTRIES, body=_NORMAL_BODY)
_beforeN = _snap(_docN)
_notesN, _warnsN = bibnorm.repair_word_refs(_docN)
check("⑤ 反例：普通文档 repair 无 note/warning，refs/entries/body 逐字段不变",
      not _notesN and not _warnsN and _snap(_docN) == _beforeN,
      f"notes={_notesN} warns={_warnsN}")
check("⑤ 反例：空文档也不报错",
      bibnorm.repair_word_refs(_doc()) == ([], []), "")

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 失败路径：拆不动的多文献行 → 保留原条目 + warning（不猜、不新增）
# ══════════════════════════════════════════════════════════════════════════
_BAD_LINE = "[1] Alpha,A,2014, “Alpha Book”; Beta.B,2018,"
_BAD_ENTRIES = [("B1", {"_type": "book", "author": "Alpha,A",
                        "title": "Alpha Book", "year": "2014"}, "")]
_docBad = _doc(bib_entries=_BAD_ENTRIES, raw_by_key={"B1": _BAD_LINE},
               raw_lines=[_BAD_LINE], num2key={1: "B1"})
_beforeBad = _snap(_docBad)
_notesBad, _warnsBad = bibnorm.repair_word_refs(_docBad)
check("⑥ 拆不动的多文献行：条目数不变（保留原条目，不新增）",
      len(_docBad.bib_entries) == 1, str(len(_docBad.bib_entries)))
check("⑥ 拆不动的多文献行：字段逐字不变",
      _snap(_docBad) == _beforeBad, "")
check("⑥ 拆不动的多文献行：进 warning（不静默丢/不静默猜）",
      bool(_warnsBad), str(_warnsBad)[:160])

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
