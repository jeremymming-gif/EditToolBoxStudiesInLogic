#!/usr/bin/env python3.14
r"""批70 B-001：Word 通道「脚注引文 ↔ 文末条目」同作品去重 + 未链文末条目 warning。

缺陷（案例 186，期90 真稿；本套件只用**合成夹具**，不含作者原文、不读稿件库）
--------------------------------------------------------------------------------
① **同作品双条目**：脚注引注被提升成 bib 条目 `Wolf1987`（author「Cf. Susan Wolf」，
   题名被解析器塞进半行出版信息），文末人年制清单里同一作品是 `Susan1987`
   （干净题名）。`docxsource` 的并集按**指纹**合并，题名一个「包含」另一个 → 指纹
   不同 → 两条都印进 `reference.bib`（11 条 vs 人工 10 条），正文只引 `Wolf1987`，
   `Susan1987` 成了未引用条目。
② **未链文末条目静默**：3 条文末条目只以说明性脚注/纯文本出现、未形成 `\parencite`
   → biblatex 不打印（PDF 参考文献区少 3 条），此前**无任何 warning**。

修法（`bibnorm` 内 ⑤ + 公开函数 ⑥；build 调用 ⑥；写范围见任务卡）
--------------------------------------------------------------
⑤ **同作品去重**（`repair_word_refs()` 内；判据全部满足才合并，不猜）：脚注条目键
   不在文末清单键集合内 + 作者姓归一相同（`footnotes._author_surname` 口径，
   「Cf.」已剥离）+ 年份相等 + 题名相似度 `_title_match_tier` ≥ 0.90 + 卷号/版次
   无冲突 + **候选唯一**。命中 → 删 bib 条目、正文 `\parencite` 键改写为文末键、
   文末字段优先；多候选 / 不足阈值 / 卷版冲突 → **保留原条目 + warning**（人工核对）。
⑥ **未链文末条目 warning**（`end_list_uncited_warning(doc, body)`；纯日志、零产物
   变化）：文末键集合 = 人年制 `refs` + 编号清单 `ref_key_by_num`（**不含**脚注生成的
   `bib_entries` 键）；与 `bibcheck.cited_keys(body)` 求差集；仅当「文末有条目 + 正文
   已有引用命令 + 存在未引条目」时输出，计数 + ≤8 条清单；不自动补 `\parencite`。
   ⚠️ **必须在 build 的引用生成之后调用**：人年制 Word 稿在 repair 时点正文引用还是
   纯文本（要等 `link_citations` 才变 `\parencite`），提前统计会把整份清单误报成
   「未引用」（批70 retest 86-07/88-01/88-06 实测误报 25/37/39 条——本套件钉住该形态）。

本套件钉住（spec §2.3 a–g + §2.4；纯内存、hermetic，无 /tmp 真稿依赖）：
  a. 同姓+同年+题名≥阈值+候选唯一 → 合并、正文键改写、文末字段优先；
  b–e. 反例：多候选 / 年份不同 / 题名不足阈值 / 卷号版次冲突 → 不合并（该告警的告警）；
  f. 无文末清单（全脚注体例）→ 行为与现状逐字节同；
  g. B-021 空题名碎片合并不回归（且不被 ⑤ 重复告警）；
  h–j. ⑥ 公开函数：人年制/编号清单命中、未渲染不误报、截断、\nocite{*}/0 引用/全已引不误报；
  k–n. ⑤+⑥ 联动、幂等、原「普通文档零变化」不回归、临时目录零写入。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        tests/test_batch70_b001.py
"""
import copy
import os
import sys
import tempfile
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


def _ref(author, year, title, kind="M", rest="", key="", raw=""):
    return RefEntry(raw=raw, author=author, year=str(year), title=title, kind=kind,
                    rest=rest, key=key)


def _doc(refs=(), bib_entries=(), body="", raw_by_key=None, num2key=None):
    """最小 DocxDoc 形状（只带 repair 需要的属性）。"""
    return SimpleNamespace(
        refs=copy.deepcopy(list(refs)),
        bib_entries=copy.deepcopy(list(bib_entries)),
        body=body,
        ref_raw_by_key=dict(raw_by_key or {}),
        ref_key_by_num=dict(num2key or {}),
    )


def _snap(doc):
    """可比较快照（refs 逐字段、bib_entries 深拷贝、body、raw_by_key）。"""
    refs = [tuple(sorted((f, getattr(r, f, "")) for f in
                         ("author", "year", "title", "kind", "rest", "key")))
            for r in doc.refs]
    return (refs, copy.deepcopy(doc.bib_entries), doc.body,
            dict(getattr(doc, "ref_raw_by_key", {}) or {}))


def _repair(doc):
    return bibnorm.repair_word_refs(doc)


def _uncited(doc, body):
    """⑥：build 在**引用生成之后**调用的公开函数（纯日志，返回文本或 None）。"""
    return bibnorm.end_list_uncited_warning(doc, body)


def _keys(doc):
    return [k for k, _e, _r in doc.bib_entries]


def _body_keys(body):
    import re
    return sorted(set(re.findall(r"\\parencite(?:\[[^\]]*\])*\{([^}]*)\}", body)))


# ══════════════════════════════════════════════════════════════════════════
# 夹具：期90 形状（Wolf1987 脚注条目 vs Susan1987 文末人年制条目）
# ══════════════════════════════════════════════════════════════════════════
_WOLF_REF = _ref(
    "Wolf, S.", 1987, "Sanity and the Metaphysics of Responsibility", "C",
    "in Responsibility, Character, and the Emotions: New Essays in Moral Psychology, "
    "pp. 46-62. Cambridge: Cambridge University Press, 1987.",
    key="Susan1987",
    raw="Susan Wolf, 1987, “Sanity and the Metaphysics of Responsibility,” in "
        "Responsibility, Character, and the Emotions: New Essays in Moral Psychology, "
        "pp. 46-62. Cambridge: Cambridge University Press.")
_WOLF_ENTRY = ("Wolf1987", {
    "_type": "incollection",
    "author": "Cf. Susan Wolf",
    "title": "“Sanity and the Metaphysics of Responsibility, ” in Responsibility, "
             "Character, and the Emotions: New Essays in Moral Psychology",
    "year": "1987",
}, "Cf. Susan Wolf, “Sanity and the Metaphysics of Responsibility,” in Responsibility, "
   "Character, and the Emotions: New Essays in Moral Psychology, pp. 46-62")

# ══════════════════════════════════════════════════════════════════════════
# a. 同姓+同年+题名相似度≥0.90+候选唯一 → 合并、正文键改写、文末字段优先
# ══════════════════════════════════════════════════════════════════════════
_docA = _doc(refs=[_WOLF_REF], bib_entries=[_WOLF_ENTRY],
             body="……明智条件（the sanity condition）……\\parencite[46--62]{Wolf1987}\n",
             raw_by_key={"Wolf1987": _WOLF_ENTRY[2], "Susan1987": _WOLF_REF.raw})
_refs_beforeA = _snap(_docA)[0]
_notesA, _warnsA = _repair(_docA)
check("a 同作品：脚注条目从 bib_entries 删除（89 型 11→10 的那一步）",
      _docA.bib_entries == [] and _keys(_docA) == [], str(_keys(_docA)))
check("a 同作品：正文 \\parencite 键改写为文末键（Wolf1987→Susan1987）",
      _body_keys(_docA.body) == ["Susan1987"], str(_body_keys(_docA.body)))
check("a 同作品：可选参数（页码定位）原样保留",
      "\\parencite[46--62]{Susan1987}" in _docA.body, _docA.body.strip()[-60:])
check("a 同作品：文末条目字段优先——refs 逐字段一字不改",
      _snap(_docA)[0] == _refs_beforeA, "")
check("a 同作品：notes 记「合并」（键对可见）",
      any("同作品" in n and "Wolf1987" in n and "Susan1987" in n for n in _notesA),
      str(_notesA)[-120:])
check("a 同作品：raw_by_key 里的旧键一并删除",
      "Wolf1987" not in _docA.ref_raw_by_key and "Susan1987" in _docA.ref_raw_by_key,
      str(sorted(_docA.ref_raw_by_key)))
check("a 同作品：正文已引用文末键 → ⑥（引用生成后）不再误报未链",
      _uncited(_docA, _docA.body) is None, str(_warnsA)[:120])

# ══════════════════════════════════════════════════════════════════════════
# b. 同姓+同年但有 ≥2 个不同作品候选 → 不合并 + warning（不猜）
# ══════════════════════════════════════════════════════════════════════════
_docB = _doc(
    refs=[_WOLF_REF, _ref("Wolf, S.", 1987, "The Body and the Soul", "M",
                          "Cambridge: Harvard University Press.", key="WolfAlt1987")],
    bib_entries=[_WOLF_ENTRY],
    body="\\parencite{Susan1987}",
)
_beforeB = _snap(_docB)
_notesB, _warnsB = _repair(_docB)
check("b 多候选：不合并（bib 条目保留、正文键不动）",
      _keys(_docB) == ["Wolf1987"] and _body_keys(_docB.body) == ["Susan1987"],
      f"{_keys(_docB)} {_body_keys(_docB.body)}")
check("b 多候选：进 warning（疑似同作品重复、无法唯一确定），不静默",
      any("多条同姓同年候选" in w and "无法唯一确定" in w for w in _warnsB),
      str(_warnsB)[:160])
check("b 多候选：refs/raw_by_key 不动",
      _snap(_docB)[0] == _beforeB[0] and _snap(_docB)[3] == _beforeB[3], "")

# ══════════════════════════════════════════════════════════════════════════
# c. 年份不同 → 不合并；且不误报（不同年是不同作品，不需要人工核对）
# ══════════════════════════════════════════════════════════════════════════
_docC = _doc(
    refs=[_ref("Wolf, S.", 1988, "Sanity and the Metaphysics of Responsibility", "C",
               "pp. 46-62. Cambridge: Cambridge University Press.", key="Susan1988")],
    bib_entries=[_WOLF_ENTRY],
    body="\\parencite{Susan1988}",
)
_beforeC = _snap(_docC)
_notesC, _warnsC = _repair(_docC)
check("c 年份不同：不合并（bib 条目/正文键/refs 均不动）",
      _keys(_docC) == ["Wolf1987"] and _snap(_docC) == _beforeC, str(_keys(_docC)))
check("c 年份不同：_notes/_warns 全空（不制造噪音）",
      not _notesC and not _warnsC, f"notes={_notesC} warns={_warnsC}")

# ══════════════════════════════════════════════════════════════════════════
# d. 题名相似度 < 0.90（同姓同年）→ 不合并 + warning「不足阈值」
# ══════════════════════════════════════════════════════════════════════════
_docD = _doc(
    refs=[_ref("Wolf, S.", 1987, "The Body and the Soul", "M",
               "Cambridge: Harvard University Press.", key="WolfBody1987")],
    bib_entries=[_WOLF_ENTRY],
    body="\\parencite{WolfBody1987}",
)
_beforeD = _snap(_docD)
_notesD, _warnsD = _repair(_docD)
check("d 题名不足阈值：不合并",
      _keys(_docD) == ["Wolf1987"] and _snap(_docD) == _beforeD, str(_keys(_docD)))
check("d 题名不足阈值：进 warning（同姓同年、题名相似度 < 0.90）",
      any("题名相似度" in w and "0.90" in w and "不足阈值" in w for w in _warnsD),
      str(_warnsD)[:180])

# ══════════════════════════════════════════════════════════════════════════
# e. 卷号/版次冲突 → 不合并 + warning；两边一致或一方缺失 → 照合（无冲突）
# ══════════════════════════════════════════════════════════════════════════
_META_REF = _ref("Aristotle", 1990, "Metaphysics", "M",
                 "Vol. 2. Oxford: Clarendon Press.", key="Arist1990b")
_META_ENTRY_V1 = ("Arist1990a", {"_type": "book", "author": "Aristotle",
                                 "title": "Metaphysics", "year": "1990",
                                 "volume": "1"},
                  "Aristotle, Metaphysics, Vol. 1. Oxford: Clarendon Press.")
_docE = _doc(refs=[_META_REF], bib_entries=[_META_ENTRY_V1],
             body="\\parencite{Arist1990b}")
_beforeE = _snap(_docE)
_notesE, _warnsE = _repair(_docE)
check("e 卷号冲突（脚注 Vol.1 vs 文末 Vol.2）：不合并",
      _keys(_docE) == ["Arist1990a"] and _snap(_docE) == _beforeE, str(_keys(_docE)))
check("e 卷号冲突：进 warning（证据冲突、请人工核对）",
      any("卷号/版次冲突" in w and "Arist1990b" in w for w in _warnsE),
      str(_warnsE)[:180])

_ED_REF = _ref("Kant", 1998, "Critique of Pure Reason", "M",
               "3rd ed. Cambridge: Cambridge University Press.", key="Kant1998")
_ED_ENTRY = ("Kant1998a", {"_type": "book", "author": "Kant, I.",
                           "title": "Critique of Pure Reason", "year": "1998",
                           "edition": "2"}, "Kant, Critique of Pure Reason, 2nd ed.")
_docE2 = _doc(refs=[_ED_REF], bib_entries=[_ED_ENTRY], body="\\parencite{Kant1998}")
_beforeE2 = _snap(_docE2)
_notesE2, _warnsE2 = _repair(_docE2)
check("e 版次冲突（2nd ed. vs 3rd ed.）：不合并 + warning",
      _keys(_docE2) == ["Kant1998a"] and _snap(_docE2) == _beforeE2
      and any("卷号/版次冲突" in w for w in _warnsE2), str(_warnsE2)[:160])

# 一边缺卷号（脚注有 Vol.1、文末清单没写卷号）＝「不知道」不算冲突 → 合并
_REF_NOVOL = _ref("Aristotle", 1990, "Metaphysics", "M",
                  "Oxford: Clarendon Press.", key="Arist1990")
_docE3 = _doc(refs=[_REF_NOVOL], bib_entries=[_META_ENTRY_V1],
              body="\\parencite[12]{Arist1990a}")
_notesE3, _warnsE3 = _repair(_docE3)
check("e 一边缺卷号不算冲突 → 合并（不猜也不过度拦截）",
      _docE3.bib_entries == [] and _body_keys(_docE3.body) == ["Arist1990"]
      and "\\parencite[12]{Arist1990}" in _docE3.body,
      f"{_keys(_docE3)} {_body_keys(_docE3.body)}")

# ══════════════════════════════════════════════════════════════════════════
# f. 无文末清单（全脚注体例）→ 行为与现状逐字节同（不引入 ⑤/⑥）
# ══════════════════════════════════════════════════════════════════════════
_docF = _doc(refs=[], bib_entries=[_WOLF_ENTRY], body="全脚注体例：\\parencite{Wolf1987}",
             num2key={})
_beforeF = _snap(_docF)
_notesF, _warnsF = _repair(_docF)
check("f 无文末清单：repair 返回空、三属性逐字节同（默认零变化）",
      _notesF == [] and _warnsF == [] and _snap(_docF) == _beforeF,
      f"notes={_notesF} warns={_warnsF}")

# 编号清单（num2key）不是文末人年制 refs：⑤ 不以它为匹配目标；repair 也不得出
# 未链 warning（⑥ 已移到 build 的引用生成之后，见 h 节——那里的 body 才是链接后的）。
_docF2 = _doc(
    refs=[],
    bib_entries=[("N01", {"_type": "book", "author": "Alpha, A.", "title": "Book One",
                          "year": "2014"}, "")],
    body="[1] 见文献\\parencite{N01}",
    num2key={1: "N01"},
)
_beforeF2 = _snap(_docF2)
_notesF2, _warnsF2 = _repair(_docF2)
check("f 编号清单体例：不崩、不误合并、产物不变",
      _snap(_docF2) == _beforeF2 and not any("未被正文链路引用" in w for w in _warnsF2),
      f"notes={_notesF2} warns={_warnsF2}")

# 90-07 反例形状：编号清单 3 条、正文只引其中 1 条（其余是纯文本 [2]/[3]，构建期才转）
_docF3 = _doc(
    refs=[],
    bib_entries=[("C1", {"_type": "book", "author": "Aaa, A.", "title": "Book One",
                         "year": "2001"}, ""),
                 ("C2", {"_type": "book", "author": "Bbb, B.", "title": "Book Two",
                         "year": "2002"}, ""),
                 ("C3", {"_type": "book", "author": "Ccc, C.", "title": "Book Three",
                         "year": "2003"}, "")],
    body="见 [2]、[3] 与\\parencite[400]{C1}",
    num2key={1: "C1", 2: "C2", 3: "C3"},
)
_notesF3, _warnsF3 = _repair(_docF3)
check("f 90-07 反例：编号清单体例不误报「未链文末条目」（[N] 构建期才转 cite）",
      not any("未被正文链路引用" in w for w in _warnsF3), str(_warnsF3)[:160])

# ══════════════════════════════════════════════════════════════════════════
# g. B-021 空题名碎片合并不回归（④ 仍在 ⑤ 之前正常工作，且 ⑤ 不重复告警）
# ══════════════════════════════════════════════════════════════════════════
_HARRY_REF = _ref("Frankfurt, H.", 1971, "Freedom of Will and the Concept of a Person",
                  "M", "The Journal of Philosophy, Vol. 68 (1), pp. 5-20.",
                  key="Harry1971")
_FRAG = ("ref06", {"_type": "article", "note": "Cf. Harry Frankfurt, “Freedom of Will "
                  "and the Concept of a Person,” The Journal of Philosophy, "
                  "Vol. 68 (1), pp. 5-20"},
         "Cf. Harry Frankfurt, “Freedom of Will and the Concept of a Person,” "
         "The Journal of Philosophy, Vol. 68 (1), pp. 5-20.")
_docG = _doc(refs=[_HARRY_REF], bib_entries=[_FRAG],
             body="法兰克福……\\parencite[5--20]{ref06}\n",
             raw_by_key={"ref06": _FRAG[2], "Harry1971": _HARRY_REF.raw})
_notesG, _warnsG = _repair(_docG)
check("g B-021 空题名碎片：并入文末条目、正文键改写（不回归）",
      _docG.bib_entries == [] and _body_keys(_docG.body) == ["Harry1971"],
      f"{_keys(_docG)} {_body_keys(_docG.body)}")
check("g B-021 空题名碎片：notes 仍是「空题名碎片并入」",
      any("空题名碎片并入" in n for n in _notesG), str(_notesG)[-120:])

_FRAG_BAD = ("ref11", {"_type": "misc", "note": "Gary Waston, “Free Action and Free "
             "Will,” p. 151"}, "Gary Waston, “Free Action and Free Will,” p. 151.")
_docG2 = _doc(refs=[_ref("Watson, G.", 1975, "Free Agency", "M",
                         "The Journal of Philosophy, Vol. 72 (8), pp. 205-220.",
                         key="Gary1975")],
              bib_entries=[_FRAG_BAD], body="\\parencite{Gary1975}",
              raw_by_key={"ref11": _FRAG_BAD[2]})
_notesG2, _warnsG2 = _repair(_docG2)
check("g 无题名又匹配不上的碎片：保留 + ④ 的 warning 恰 1 条（⑤ 不重复告警）",
      _keys(_docG2) == ["ref11"]
      and sum("ref11" in w for w in _warnsG2) == 1, str(_warnsG2))

# ══════════════════════════════════════════════════════════════════════════
# h. ⑥ 公开函数（build 引用生成之后调用）：人年制命中 + 零产物变化
#    ⚠️ 批70 retest 的误报根因就是「在 repair 时点统计」——这里钉住两种时点形态：
#    引用已渲染（\parencite）才统计；未渲染（纯文本）时不得报。
# ══════════════════════════════════════════════════════════════════════════
_REFS_H = [
    _ref("Audi, R.", 2013, "Moral Perception", "M", key="Audi2013"),
    _ref("Sartre, J.", 2012, "Existentialism Is a Humanism", "M", key="Sartre2012"),
    _ref("Tolstoy, L.", 1947, "The Death of Ivan Ilyich", "M", key="Tolstoy1947"),
]
_docH = _doc(refs=_REFS_H, bib_entries=[], body="")
_beforeH = _snap(_docH)
_msgH = _uncited(_docH, "感知……\\parencite{Audi2013}")
check("h 人年制命中：报「文末有 2 条未被正文链路引用」",
      bool(_msgH) and "文末有 2 条未被正文链路引用" in _msgH, str(_msgH)[:200])
check("h 人年制命中：清单含两个键名、不含已引的 Audi2013",
      bool(_msgH) and "Sartre2012" in _msgH and "Tolstoy1947" in _msgH
      and "Audi2013" not in _msgH, str(_msgH)[:200])
check("h 纯读：调用前后 doc 快照不变（零产物变化）", _snap(_docH) == _beforeH, "")
check("h 误报回归：引用未渲染（repair 时点形态，body 无 \\parencite）→ 返回 None",
      _uncited(_docH, "感知……（Audi 2013）……（Sartre 2012）……（托尔斯泰 1947）")
      is None, "")

# 编号清单（build ②.5 把纯文本 [N] 转成 \parencite 之后）：refs 空、编号键也算文末
_H2_ENTRIES = [("C1", {"_type": "book", "title": "Book One"}, ""),
               ("C2", {"_type": "book", "title": "Book Two"}, ""),
               ("C3", {"_type": "book", "title": "Book Three"}, "")]
_docH2 = _doc(refs=[], bib_entries=_H2_ENTRIES, num2key={1: "C1", 2: "C2", 3: "C3"})
_msgH2 = _uncited(_docH2, "见\\parencite[12]{C1}")
check("h 编号清单命中：post-link body 只引 C1 → 报 C2/C3",
      bool(_msgH2) and "文末有 2 条" in _msgH2 and "C2" in _msgH2
      and "C3" in _msgH2 and "C1" not in _msgH2, str(_msgH2)[:200])
check("h 误报回归：编号清单纯文本 [N]（未走 ②.5）→ 返回 None",
      _uncited(_docH2, "见 [2]、[3] 与 [1]") is None, "")

# 脚注生成的 bib_entries 键**不算**文末条目：不该因为脚注条目没被正文引用而报
_docH3 = _doc(refs=[_REFS_H[0]], bib_entries=[("FootX", {"title": "Footnote Only"}, "")],
              body="")
check("h 脚注 bib_entries 键不计入文末键集合",
      _uncited(_docH3, "\\parencite{Audi2013}") is None, "")

# ══════════════════════════════════════════════════════════════════════════
# i. ⑥ 清单截断：≤8 条 + 计数 + 省略号
# ══════════════════════════════════════════════════════════════════════════
_refs12 = [_ref(f"Author{i:02d}", 2000 + i, f"Title {i:02d}", "M", key=f"K{i:02d}")
           for i in range(1, 13)]
_docI = _doc(refs=_refs12, bib_entries=[], body="")
_msgI = _uncited(_docI, "\\parencite{K01}")
check("i 截断：计数 11 条（12 条 - 已引 1 条）", bool(_msgI) and "文末有 11 条" in _msgI,
      str(_msgI)[:120])
check("i 截断：清单只列 8 条 + 省略号（K02…K09 列出、K10 起省略）",
      bool(_msgI) and "K02" in _msgI and "K09" in _msgI and "K10" not in _msgI
      and _msgI.rstrip().endswith("…"), str(_msgI)[-120:])

# ══════════════════════════════════════════════════════════════════════════
# j. ⑥ 不误报：正文 0 引用（既有提示负责）、全已引、\nocite{*}、无文末条目
# ══════════════════════════════════════════════════════════════════════════
_docJ = _doc(refs=_REFS_H, bib_entries=[], body="")
check("j 正文 0 引用：返回 None（不重复告警）",
      _uncited(_docJ, "全文没有任何引用命令的纯文本正文。") is None, "")
check("j 全部已引：返回 None",
      _uncited(_docJ, "\\parencite{Audi2013}\\parencite{Sartre2012}"
                       "\\parencite{Tolstoy1947}") is None, "")
check("j \\nocite{*}（biblatex 打印全部条目）：返回 None",
      _uncited(_docJ, "\\parencite{Audi2013}\\nocite{*}") is None, "")
check("j 无文末条目：返回 None",
      _uncited(_doc(refs=[], bib_entries=[]), "\\parencite{AnyKey}") is None, "")

# ══════════════════════════════════════════════════════════════════════════
# k. ⑤+⑥ 联动：合并后文末键成为已引，未链条目只剩真正没引的那条
# ══════════════════════════════════════════════════════════════════════════
_docK = _doc(
    refs=[_WOLF_REF, _REFS_H[1], _REFS_H[2]],
    bib_entries=[_WOLF_ENTRY],
    body="伍尔夫……\\parencite[46--62]{Wolf1987}",
    raw_by_key={"Wolf1987": _WOLF_ENTRY[2], "Susan1987": _WOLF_REF.raw},
)
_notesK, _warnsK = _repair(_docK)
_msgK = _uncited(_docK, _docK.body)
check("k 联动：⑤ 合并后正文键=文末键（Susan1987 不再算未引）",
      _body_keys(_docK.body) == ["Susan1987"] and _docK.bib_entries == [],
      str(_body_keys(_docK.body)))
check("k 联动：⑥ 未引恰 2 条（Sartre2012/Tolstoy1947），Susan1987 不在清单",
      bool(_msgK) and "文末有 2 条" in _msgK and "Sartre2012" in _msgK
      and "Tolstoy1947" in _msgK and "Susan1987" not in _msgK, str(_msgK)[:200])

# ══════════════════════════════════════════════════════════════════════════
# l. 幂等：repair 与 ⑥ 都是纯函数式（同状态同结果）
# ══════════════════════════════════════════════════════════════════════════
_afterK = _snap(_docK)
_notesK2, _warnsK2 = _repair(_docK)
check("l 幂等：第二次 repair 产物零变化且不再产生 warning（⑥ 已移出 repair）",
      _snap(_docK) == _afterK and not _notesK2 and not _warnsK2,
      f"notes={_notesK2} warns={_warnsK2}")
check("l 幂等：⑥ 公开函数同输入两次返回同一文本（纯日志、不改 doc）",
      _uncited(_docK, _docK.body) == _msgK
      and _snap(_docK) == _afterK, "")

# ⑤ 合并过、无未链条目的文档：连跑第二次连 warning 都不再产生
_afterA2 = _snap(_docA)
_notesA2, _warnsA2 = _repair(_docA)
check("l 幂等：⑤ 合并后连跑第二次零 note/warning、快照相同",
      _snap(_docA) == _afterA2 and not _notesA2 and not _warnsA2,
      f"notes={_notesA2} warns={_warnsA2}")

# ══════════════════════════════════════════════════════════════════════════
# m. 原「普通文档零变化」不回归（批68 反例）+ 作者姓不同 → 静默不合并
# ══════════════════════════════════════════════════════════════════════════
_docN = _doc(
    refs=[_ref("Baker, L. R.", 2012, "From Consciousness to Self-consciousness", "C",
               "in Facets of Self-Consciousness, New York: Rodopi.", key="Baker2012"),
          _ref("Audi, R.", 2013, "Moral Perception", "M",
               "Princeton: Princeton University Press.", key="Audi2013")],
    bib_entries=[("BakerAlt", {"_type": "incollection", "author": "Bakker, L.",
                               "title": "From Consciousness to Self-consciousness",
                               "year": "2012"}, "Bakker, L. From Consciousness …")],
    body="\\parencite{Baker2012}\\parencite{Audi2013}",
)
_beforeN = _snap(_docN)
_notesN, _warnsN = _repair(_docN)
check("m 反例：作者姓不同（Baker vs Bakker）→ 不合并、不告警（不猜）",
      _keys(_docN) == ["BakerAlt"] and _snap(_docN) == _beforeN
      and not _warnsN, f"{_keys(_docN)} warns={_warnsN}")
check("m 反例：普通文档 repair 无 note、三属性逐字段不变",
      not _notesN and _snap(_docN) == _beforeN, f"notes={_notesN}")
check("m 反例：空文档也不报错", _repair(_doc()) == ([], []), "")

# ══════════════════════════════════════════════════════════════════════════
# n. hermetic：临时目录里跑 ⑤（repair）+ ⑥（公开函数）不写任何文件
#    （无 /tmp 真稿、无稿件库、无笔记仓依赖）
# ══════════════════════════════════════════════════════════════════════════
_cwd = os.getcwd()
_tmp = tempfile.TemporaryDirectory(prefix="b70_b001_")
try:
    os.chdir(_tmp.name)
    _docO = _doc(refs=[_WOLF_REF, _REFS_H[1]], bib_entries=[_WOLF_ENTRY],
                 body="\\parencite{Wolf1987}", raw_by_key={"Wolf1987": _WOLF_ENTRY[2]})
    _ = _repair(_docO)
    _ = _uncited(_docO, _docO.body)
    _left = sorted(os.listdir("."))
finally:
    os.chdir(_cwd)
    _tmp.cleanup()
check("n hermetic：⑤/⑥ 只在内存读改 doc，临时工作目录零写入", _left == [], str(_left))

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
