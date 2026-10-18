#!/usr/bin/env python3.14
"""T12（task-15）期 87 P1/P2 引用类修复的判据测试（Phase1，2026-10-18，bib-refs）。

钉住的六件事（与任务卡一一对应）：
  ① **P1-2 类型判定**：按「出版信息特征」判 `@type`（不是「卷期正则是否命中」）。
     `Oxford: Clarendon` / `Paramus: Prentice Hall` / `Springer` → @book；
     `in … (eds.), pp.` → @incollection；会议论文集 → @inproceedings；
     中文期刊、中文书、学位论文、英文书等**反例**行为不变。
  ② **P1-3 作者倒装入口统一**：`footnotes.entry_to_bib()` 出口对 `author` 先拆
     「逗号 + and」混写/`&` 再倒装（与 `bibnorm.normalize_author_field` 同源）；
     多作者/单作者/中文/花括号保护/已倒装各自的结果钉住；`editor` 不被本卡改动。
  ③ **P1-5 `--original` 假差异静音**：`bibnorm.DROP_FIELDS`（urldate/timestamp/…）与
     SEP 的 `howpublished → url` 不再报「字段被删掉」；报告头注明口径；
     非白名单字段（如 address）照旧是问题。
  ④ **P1-1/P2-5 title 匹配降级**：key 重叠率低且清单成规模（≥8 条）时自动降级
     title 匹配——不再报「疑似改了 key」，报告输出「按 key N / 按 title M」两套统计；
     小清单（1 条改名）仍按旧口径报问题（防回归）。
  ⑤ **P2-4**：bib>0 且正文引用=0 时 **problem**（文案含「PDF 参考文献区会是空的」）；
     bib>0 且有引用时**不**升级；`bibcheck.check_citations()` 同样只在 0 引用时升级。
  ⑥ **默认行为不变**：无 `--original`/`--tex` 的默认报告两次渲染逐字节相同，
     且不出现 diff/cite 小节；`bibcheck.check_citations()` 在有引用时返回值不含新 problem。

真实行夹具来自期 87 sub2/sub7 的原始编号清单（只读、inline，不依赖 /tmp）。
不联网、不读稿件库、不写 out/。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_refs_p1_2026_10_18.py
"""
import re
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck, bibnorm, bibreport, footnotes, footref  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def parse1(line):
    """按生产口径：先剥 `[N]` 前缀，再解析单条。"""
    seg = re.sub(r"^\s*[\[\(（]\s*\d{1,3}\s*[\]\)）]\s*", "", line.strip())
    return footref.parse_numbered_item(seg)


# ══ ① P1-2：类型判定按「出版信息特征」═══════════════════════════════════════
print("── ① P1-2 类型判定 ──")

# 真实行（期 87 sub2 编号清单）：出版地: 出版社 曾被当成刊名 → @misc 丢字段
REAL_BOOKS = [
    ("[11] H. Mortimer, 1988, The Logic of Induction, Paramus: Prentice Hall.",
     "book", "Paramus", "Prentice Hall"),
    ("[12] R. Nozick, 1981, Philosophical Explanations, Oxford: Clarendon.",
     "book", "Oxford", "Clarendon"),
]
for line, et, addr, pub in REAL_BOOKS:
    p = parse1(line)
    check(f"① 真实书 {line[:22]}… → @{et}（出版地/出版社从刊名格挪回）",
          p.etype == et and p.address == addr and p.publisher == pub and not p.journal,
          f"{p.etype} addr={p.address!r} pub={p.publisher!r} J={p.journal!r}")

# 出版社名落在 journal 格（期 87 sub7 真实行：Routledge）
for line, pub in (("[6] J. Ganeri, 2001, Indian Logic: A Reader, Routledge.", "Routledge"),):
    p = parse1(line)
    check(f"① 只有出版社 `{pub}` → @book（不是 @article）",
          p.etype == "book" and p.publisher == pub and not p.journal,
          f"{p.etype} pub={p.publisher!r} J={p.journal!r}")

# 真实行（期 87 sub2）：`in … (eds.), pp.` → @incollection
_INC = ("[3] V. Crupi, K. Tentori, 2016, “Confirmation theory”, in A. Hajek and "
        "C. Hitchcock (eds.), Oxford Handbook of Philosophy and Probability, "
        "pp. 650-665, Oxford: Oxford University Press.")
p = parse1(_INC)
check("① 真实行 `in … (eds.), pp.` → @incollection + publisher",
      p.etype == "incollection" and p.publisher == "Oxford University Press",
      f"{p.etype} bt={p.booktitle!r} pub={p.publisher!r}")

# classify_entry_type 是**可复用**判据（下一卡 docxsource 直接调用）
_CASES = [
    ({"journal": "Philosophy of Science", "volume": "66", "pages": "S362--S378"},
     "article", "有刊名"),
    ({"journal": "Oxford: Clarendon", "year": "1981"}, "book", "出版地: 出版社"),
    ({"journal": "Springer", "year": "1986"}, "book", "刊名格实为出版社"),
    ({"booktitle": "Oxford Handbook of Philosophy and Probability",
      "publisher": "Oxford University Press", "pages": "650--665"},
     "incollection", "容器"),
    ({"booktitle": "Proceedings of the 12th Conference on Logic",
      "publisher": "Springer"}, "inproceedings", "会议论文集"),
    ({}, "misc", "出版信息不足"),
]
for fields, et, why in _CASES:
    got, reason = footref.classify_entry_type(fields, raw="")
    check(f"① classify_entry_type({why}) → @{et}", got == et, f"{got} / {reason}")

# 反例：旧行为不许被带偏
_NEG = [
    ("[17] 党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。",
     "article", "湖南科技大学学报（社会科学版）"),
    ("[8] 付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。", "book", ""),
    ("[9] 张三，题名题名题名，博士学位论文，北京：北京大学，2020年。", "misc", ""),
    ("[5] M.Dummett, 1991, The Logical Basis of Metaphysics, Cambridge, MA: Harvard University Press.",
     "book", ""),
]
for line, et, journal in _NEG:
    p = parse1(line)
    check(f"① 反例 {line[:16]}… 仍是 @{et}、journal={journal or '（无）'}",
          p.etype == et and p.journal == journal,
          f"{p.etype} J={p.journal!r} pub={p.publisher!r}")

# ══ ② P1-3：author 出口统一倒装 ═════════════════════════════════════════════
print("\n── ② P1-3 作者倒装统一（entry_to_bib 出口）──")


def bib_author(author, etype="article"):
    """取 `entry_to_bib` 里 author 那一行的**值**（剥掉最外层花括号；内层保护花括号保留）。"""
    entry = {"_type": etype, "author": author, "title": "T", "journal": "J", "year": "2000"}
    bib = footnotes.entry_to_bib("K1", entry)
    for line in bib.splitlines():
        if line.strip().startswith("author"):
            val = line.split("=", 1)[1].strip()
            if val.startswith("{"):
                val = val[1:]
            if val.endswith(","):
                val = val[:-1].strip()
            if val.endswith("}"):
                val = val[:-1]
            return val.strip()
    return ""


check("② 单作者 Given Surname → Surname, Given",
      bib_author("John Smith").strip("{}") == "Smith, J.", bib_author("John Smith"))
check("② 多作者（and 分隔）每个都倒装",
      bib_author("J. P. Dillard and C. Segrin").strip("{}")
      == "Dillard, J. P. and Segrin, C.", bib_author("J. P. Dillard and C. Segrin"))
check("② 「逗号 + and」混写先拆再倒装（旧实现整条跳过）",
      bib_author("J. P. Dillard, C. Segrin and J. M. Harden").strip("{}")
      == "Dillard, J. P. and Segrin, C. and Harden, J. M.",
      bib_author("J. P. Dillard, C. Segrin and J. M. Harden"))
check("② `&` 分隔也拆并倒装",
      bib_author("A. Smith & B. Jones").strip("{}") == "Smith, A. and Jones, B.",
      bib_author("A. Smith & B. Jones"))
check("② 中文姓名原样（不倒装）", "张三" in bib_author("张三 and 李四"),
      bib_author("张三 and 李四"))
check("② 花括号保护原样（不拆不猜，整条保持）",
      "B. C. {van Fraassen}" in bib_author("B. C. {van Fraassen}"),
      bib_author("B. C. {van Fraassen}"))
check("② 已倒装幂等（不再动）", "Haack, S." in bib_author("Haack, S."), bib_author("Haack, S."))
# 中文著/译体例（第 64 条）不被破坏
check("② 中文著/译体例整段保留",
      "阿斯海姆（著），张建军、万林（译）" in bib_author("阿斯海姆（著），张建军、万林（译）"),
      bib_author("阿斯海姆（著），张建军、万林（译）"))
# footref 路径同样走统一 helper（多作者 + 虚词）
p = parse1("[1] J. P. Dillard and C. Segrin, 1993, X, Journal of X, 1(2): 1-2.")
check("② footref.parse_numbered_item 的 author 也统一倒装",
      p.author == "Dillard, J. P. and Segrin, C.", p.author)
# 注：`footnotes._TYPE_ORDER` 各类型的字段序本来就不含 editor（既有结构，本卡不动），
# editor 归 B3 批处理；本卡只保证 author 的出口统一。

# ══ ③ P1-5：DROP_FIELDS / howpublished→url 静音 ════════════════════════════
print("\n── ③ P1-5 --original 假差异静音 ──")

_ORIG_DROP = """\
@article{Smith2020,
  author   = {Smith, John},
  title    = {A Theory},
  journal  = {J},
  year     = {2020},
  urldate  = {2021-01-01},
  timestamp = {2021-01-01},
  address  = {Oxford}
}
"""
_RET_DROP = """\
@article{Smith2020,
  author  = {Smith, John},
  title   = {A Theory},
  journal = {J},
  year    = {2020}
}
"""
_rep = bibreport.analyze(_RET_DROP, _ORIG_DROP)
_drop_probs = [f for f in _rep.findings if f.level == bibreport.LEVEL_PROBLEM
               and ("字段被删掉" in f.detail)
               and any(name in f.detail for name in ("urldate", "timestamp"))]
_md = bibreport.render(_rep)
check("③ DROP_FIELDS（urldate/timestamp）不再报「字段被删掉」", not _drop_probs,
      str([f.detail for f in _drop_probs])[:200])
check("③ 报告注明「静音口径」",
      "diff 静音" in _md and "DROP_FIELDS" in _md, _md[:300])
check("③ 非白名单字段（address）照旧是问题",
      any("address" in f.detail and f.level == bibreport.LEVEL_PROBLEM
          for f in _rep.findings), str([f.detail for f in _rep.findings])[:300])

_SEP_ORIG = """\
@incollection{Sep2020,
  author  = {Zalta, Edward N.},
  title   = {A Thing},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  howpublished = {\\url{https://plato.stanford.edu/archives/sum2024/entries/thing/}},
  year    = {2020}
}
"""
_SEP_RET = """\
@incollection{Sep2020,
  author  = {Zalta, Edward N.},
  title   = {A Thing},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  url     = {https://plato.stanford.edu/archives/sum2024/entries/thing/},
  year    = {2020}
}
"""
_rep_sep = bibreport.analyze(_SEP_RET, _SEP_ORIG)
check("③ SEP 的 howpublished→url 静音（不报字段被删）",
      not any("howpublished" in f.detail and f.level == bibreport.LEVEL_PROBLEM
              for f in _rep_sep.findings), str([f.detail for f in _rep_sep.findings])[:300])
_PLAIN_ORIG = _SEP_ORIG.replace("The Stanford Encyclopedia of Philosophy", "Some Book")
_rep_plain = bibreport.analyze(_SEP_RET, _PLAIN_ORIG)
check("③ 非 SEP 的 howpublished 被删仍是问题（白名单不扩大）",
      any("howpublished" in f.detail and f.level == bibreport.LEVEL_PROBLEM
          for f in _rep_plain.findings), str([f.detail for f in _rep_plain.findings])[:300])

# ══ ④ P1-1/P2-5：key 不重叠 → title 匹配降级 + 两套统计 ═════════════════════
print("\n── ④ P1-1/P2-5 title 匹配降级 ──")


def _mk_bib(prefix, n, year_shift=0):
    out = []
    for i in range(n):
        out.append(f"""@article{{{prefix}{i},
  author  = {{Author{i}, A.}},
  title   = {{Title Number {i}}},
  journal = {{J{i}}},
  year    = {{200{year_shift + i % 10}}}
}}
""")
    return "\n".join(out)


_ORIG8 = _mk_bib("Old", 8)
_NEW8 = _mk_bib("Gen", 8)   # key 100% 不同、题名/作者/年份相同
_rep_dg = bibreport.analyze(_NEW8, _ORIG8)
_md_dg = bibreport.render(_rep_dg)
check("④ 降级模式：不再报「疑似改了 key」",
      not any("疑似改了 key" in f.detail for f in _rep_dg.findings),
      str([f.detail for f in _rep_dg.findings if "key" in f.detail])[:300])
check("④ 降级模式：报告输出「按 key 0 条 / 按 title 8 条」两套统计",
      "按 key 0 条" in _md_dg and "按 title 8 条" in _md_dg,
      str([ln for ln in _md_dg.splitlines() if "diff 匹配" in ln][:2]))
check("④ 降级模式：title 配上的对子仍逐字段比对（新增/变化照旧提示）",
      any("按 title 一致" in f.detail for f in _rep_dg.findings),
      str([f.detail for f in _rep_dg.findings if "匹配" in f.detail])[:200])
# 小清单（1 条改名）不降级 → 仍按旧口径报问题（防回归）
_ONE_OLD = _mk_bib("Old", 1)
_ONE_NEW = _mk_bib("Gen", 1)
_rep_one = bibreport.analyze(_ONE_NEW, _ONE_OLD)
check("④ 小清单改名仍报「疑似改了 key」（不降级）",
      any("疑似改了 key" in f.detail for f in _rep_one.findings),
      str([f.detail for f in _rep_one.findings])[:200])

# ══ ⑤ P2-4：bib>0 且正文 0 引用 → problem ═══════════════════════════════════
print("\n── ⑤ P2-4「正文 0 引用」升 problem ──")

_BIB = _mk_bib("K", 3)
_TEX_NO_CITE = "\\begin{document}\n正文没有任何引用命令。\n\\end{document}\n"
_TEX_ONE_CITE = "\\begin{document}\n正文\\parencite{K0}。\n\\end{document}\n"

_rep_no = bibreport.analyze(_BIB, tex_text=_TEX_NO_CITE)
_probs_no = [f for f in _rep_no.findings if f.level == bibreport.LEVEL_PROBLEM]
check("⑤ bib>0 且正文 0 引用 → problem，文案含「PDF 参考文献区会是空的」",
      any("PDF 参考文献区会是空的" in f.detail for f in _probs_no), str(_probs_no)[:300])
_rep_yes = bibreport.analyze(_BIB, tex_text=_TEX_ONE_CITE)
check("⑤ 反例：bib>0 且有引用 → 不升级（无该 problem）",
      not any("PDF 参考文献区会是空的" in f.detail for f in _rep_yes.findings),
      str([f.detail for f in _rep_yes.findings])[:300])
_rep_empty = bibreport.analyze("", tex_text=_TEX_NO_CITE)
check("⑤ 反例：bib=0 且正文 0 引用 → 只有提示（不是 problem）",
      not [f for f in _rep_empty.findings if f.level == bibreport.LEVEL_PROBLEM
           and "参考文献区" in f.detail])

_cites_no = bibcheck.check_citations(_TEX_NO_CITE, _BIB)
check("⑤ bibcheck.check_citations：0 引用 → 升级为一条 problem 文案",
      any("PDF 参考文献区会是空的" in c for c in _cites_no), str(_cites_no)[:300])
_cites_yes = bibcheck.check_citations(_TEX_ONE_CITE, _BIB)
check("⑤ bibcheck.check_citations：有引用时行为不变（无升级文案）",
      not any("PDF 参考文献区会是空的" in c for c in _cites_yes), str(_cites_yes)[:300])

# ══ ⑥ 默认行为：无 original/tex 时报告逐字节不变 ═════════════════════════════
print("\n── ⑥ 默认行为（无 original/tex）逐字节不变 ──")

_rep_a = bibreport.analyze(_BIB)
_rep_b = bibreport.analyze(_BIB)
_md_a, _md_b = bibreport.render(_rep_a), bibreport.render(_rep_b)
check("⑥ 同一输入两次渲染逐字节相同", _md_a == _md_b and len(_md_a) > 0)
check("⑥ 无 --original 时不出 diff 小节 / 无 --tex 时不出 cite 小节",
      "## 三、与任务书原件的差异" not in _md_a and "## 四、正文引用闭合" not in _md_a[:2000],
      _md_a[:200])
check("⑥ 默认报告结论行照旧可用", bibreport.conclusion_line(_rep_a).startswith("结论："),
      bibreport.conclusion_line(_rep_a))

# ⑤ 与 ⑥ 的交界：bibcheck.check_citations 在 unmatched=None 时其它文字不变
_base_cites = bibcheck.check_citations(_TEX_ONE_CITE, _BIB)
check("⑥ 有引用时 check_citations 只保留旧的「冗余条目」提示",
      all(c.startswith("② 冗余条目") for c in _base_cites), str(_base_cites)[:300])

# ══ ⑦ T13：docxsource 英文人年制路径接同源判据 ═══════════════════════════════
print("\n── ⑦ T13 docxsource 英文文献路径（类型/倒装同源）──")

from slcn import docxsource  # noqa: E402

_EN_REAL = [
    # (真实/等价行, 期望 kind, 期望 etype, 期望 author 开头)
    ("R. Nozick, 1981, Philosophical Explanations, Oxford: Clarendon.",
     "M", "book", "Nozick, R."),
    ("B. Fitelson, 1999, “The plurality of bayesian measures of confirmation and the "
     "problem of measure sensitivity”, Philosophy of Science, 66(Supplement): S362-S378.",
     "J", "article", "Fitelson, B."),
    ("V. Crupi, K. Tentori, 2016, “Confirmation theory”, in A. Hajek and C. Hitchcock "
     "(eds.), Oxford Handbook of Philosophy and Probability, pp. 650-665, "
     "Oxford: Oxford University Press.",
     "C", "incollection", "Crupi, V. and Tentori, K."),
    ("J. P. Dillard, C. Segrin and J. M. Harden, 1993, “The Persistence of the Leveling "
     "Effect”, Journal of X, 1(2): 1-2.",
     "J", "article", "Dillard, J. P. and Segrin, C. and Harden, J. M."),
]
for line, kind, etype, author in _EN_REAL:
    e = docxsource.parse_reference_line(line)
    ok = e is not None and e.kind == kind and e.author.startswith(author)
    bib = docxsource.refs_to_bib([e]).splitlines() if e is not None else []
    bib_line = next((ln for ln in bib if ln.startswith("@")), "")
    check(f"⑦ 真实行 → @{etype} / kind={kind} / 倒装 author",
          ok and bib_line.startswith(f"@{etype}{{"), f"{getattr(e, 'kind', None)} | {bib_line[:40]}")
check("⑦ TYPE_MAP 增加 inproceedings（kind=P）", docxsource.TYPE_MAP.get("P") == "inproceedings")

# 反例 ≥5：已倒装 / 中文 / 花括号保护 / 单名 / 机构 —— author 一律不动
_COUNTER = [
    ("Haack, S., 1974, Deviant Logic, Cambridge: Cambridge University Press.", "Haack, S."),
    ("Plato, 1992, Republic, Cambridge: Cambridge University Press.", "Plato"),
    ("World Health Organization, 2020, Global Report, Geneva: World Health Organization.",
     "World Health Organization"),
    ("{van Fraassen}, B. C., 1980, The Scientific Image, Oxford: Clarendon.",
     "{van Fraassen}, B. C."),
    ("陈波. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.", None),  # 中文路径不动
]
for line, expect in _COUNTER:
    e = docxsource.parse_reference_line(line)
    if expect is None:      # 中文清单路径：不允许被英文倒装 helper 改动
        check(f"⑦ 反例（中文路径）：author 不被英文倒装", e is not None and e.author == "陈波",
              f"{getattr(e, 'author', None)!r}")
    else:
        check(f"⑦ 反例：`{expect[:24]}` 保持原样",
              e is not None and e.author == expect, f"{getattr(e, 'author', None)!r}")

# 无真实命中时产物逐字节不变：同一输入两次 refs_to_bib 完全一致（确定性）
_line = "Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall."
_b1 = docxsource.refs_to_bib([docxsource.parse_reference_line(_line)])
_b2 = docxsource.refs_to_bib([docxsource.parse_reference_line(_line)])
check("⑦ 无命中输入产物逐字节相同（确定性）", _b1 == _b2 and "@book" in _b1, _b1.splitlines()[0][:80])

# 汇总 ──
print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
