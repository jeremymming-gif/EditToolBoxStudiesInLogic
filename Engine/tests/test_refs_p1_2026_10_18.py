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
      "阿斯海姆（著），郑建国、万林（译）" in bib_author("阿斯海姆（著），郑建国、万林（译）"),
      bib_author("阿斯海姆（著），郑建国、万林（译）"))
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
    ("李雷. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.", None),  # 中文路径不动
]
for line, expect in _COUNTER:
    e = docxsource.parse_reference_line(line)
    if expect is None:      # 中文清单路径：不允许被英文倒装 helper 改动
        check(f"⑦ 反例（中文路径）：author 不被英文倒装", e is not None and e.author == "李雷",
              f"{getattr(e, 'author', None)!r}")
    else:
        check(f"⑦ 反例：`{expect[:24]}` 保持原样",
              e is not None and e.author == expect, f"{getattr(e, 'author', None)!r}")

# 无真实命中时产物逐字节不变：同一输入两次 refs_to_bib 完全一致（确定性）
_line = "Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall."
_b1 = docxsource.refs_to_bib([docxsource.parse_reference_line(_line)])
_b2 = docxsource.refs_to_bib([docxsource.parse_reference_line(_line)])
check("⑦ 无命中输入产物逐字节相同（确定性）", _b1 == _b2 and "@book" in _b1, _b1.splitlines()[0][:80])


# ══ B-217②：中文学位论文 → @phdthesis/@mastersthesis；URL → url 字段 ═════════
print()
print("── B-217② 中文学位论文 / 网络资源 URL ──")
_phd = docxsource.parse_reference_line(
    "吴梅红. 2009. 基于动态认知逻辑的多主体系统知识推理研究[D]. 博士论文，厦门大学，2009 年.")
_pb = docxsource.refs_to_bib([_phd]) if _phd else ""
check("B-217② 博士论文 → @phdthesis + school=厦门大学 + address=博士论文",
      "@phdthesis{" in _pb and "{厦门大学}" in _pb
      and "{博士论文}" in _pb and "note" not in _pb, repr(_pb))
_mas = docxsource.parse_reference_line(
    "李四. 2018. 某方向研究[D]. 硕士学位论文，北京大学，2018 年.")
_mb = docxsource.refs_to_bib([_mas]) if _mas else ""
check("B-217② 硕士学位论文 → @mastersthesis + address=硕士学位论文",
      "@mastersthesis{" in _mb and "{硕士学位论文}" in _mb
      and "{北京大学}" in _mb and "note" not in _mb, repr(_mb))
_old = docxsource.parse_reference_line(
    "王五. 2015. 某方向研究[D]. 北京：商务印书馆.")
_ob = docxsource.refs_to_bib([_old]) if _old else ""
check("B-217② 反例：无学位类型/学校信息 → 不写 school/address（旧行为原样）",
      "school" not in _ob and "address = {博士" not in _ob, repr(_ob))
_url = docxsource.parse_reference_line(
    "Hájek, A. 2023. Most counterfactuals are false[Z]. https://philarchive.org/rec/HAJMCA.")
_ub = docxsource.refs_to_bib([_url]) if _url else ""
check("B-217② 网络资源：url 字段保留（不丢网址）",
      "= {https://philarchive.org/rec/HAJMCA}" in _ub
      and "publisher" not in _ub and "address" not in _ub, repr(_ub))
_nourl = docxsource.parse_reference_line(
    "Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall.")
_nb = docxsource.refs_to_bib([_nourl]) if _nourl else ""
check("B-217② 反例：无网址条目无 url 字段（逐字节旧行为）",
      "url" not in _nb and _nb == docxsource.refs_to_bib([_nourl]), repr(_nb))


# ══ B-217②(F2/F3)：编号清单 detection —— `_numbered_refs` + apply_numbered_extras ══
# 真实路径：模板稿的参考文献是**编号清单** → `doc.bib_entries`（不是 doc.refs）。
# 这里只钉「识别出的 entry 字段」（.bib 字段输出由 footnotes._TYPE_ORDER 决定）。
print()
print("── B-217② F2/F3 编号清单 detection（学位论文 / SEP 章节 / URL）──")
_NUM_LINES = [
    "[1] 吴梅红，基于动态认知逻辑的多主体系统知识推理研究，博士论文，厦门大学，2009 年。",
    "[2] A. C. Hastings, 1963, A Reformulation of the Modes of Reasoning, PhD thesis, Northwestern University.",
    "[3] P. Bartha, 2019, “Analogy and analogical reasoning”, in E. N. Zalta (ed.), "
    "The Stanford Encyclopedia of Philosophy (Spring 2019 Edition), "
    "https://plato.stanford.edu/archives/spr2019/entries/reasoning-analogy/.",
    "[4] J. Smith, 2000, “A normal title”, Journal of Tests, 1(2): 3-4.",
    "[5] 李四，某方向研究，博士论文，2009 年。",
    "[6] P. Bartha, 2020, “No url chapter”, in E. N. Zalta (ed.), The Stanford Encyclopedia of Philosophy.",
]
_nout, _nmap, _ = docxsource._numbered_refs(_NUM_LINES)
_by_key = {k: e for k, e, _r in _nout}
_wu = _by_key.get(_nmap.get(1, ""), {})
check("F2 正例：CN 学位论文 → _type=phdthesis + school=厦门大学 + address=博士论文",
      _wu.get("_type") == "phdthesis" and _wu.get("school") == "厦门大学"
      and _wu.get("address") == "博士论文", repr({k: _wu.get(k) for k in ("_type", "school", "address")}))
_h = _by_key.get(_nmap.get(2, ""), {})
check("F3 正例：EN PhD thesis → _type=phdthesis + school=Northwestern University（不臆造 address）",
      _h.get("_type") == "phdthesis"
      and _h.get("school") == "Northwestern University" and not _h.get("address"),
      repr({k: _h.get(k) for k in ("_type", "school", "address")}))
_b = _by_key.get(_nmap.get(3, ""), {})
check("F3 正例：SEP 章节 → booktitle（去版次括注）+ editor + url 保留",
      _b.get("booktitle") == "The Stanford Encyclopedia of Philosophy"
      and _b.get("editor") == "E. N. Zalta"
      and str(_b.get("url", "")).startswith("https://plato.stanford.edu/"), repr(
          {k: _b.get(k) for k in ("booktitle", "editor", "url")}))
check("F3 正例：普通编号条目不被加字段（逐字节旧行为）",
      _by_key.get(_nmap.get(4, ""), {}).get("_type") == "article"
      and not any(_by_key.get(_nmap.get(4, ""), {}).get(k)
                  for k in ("school", "booktitle", "url")),
      repr(_by_key.get(_nmap.get(4, ""), {})))
# 反例①：CN 学位论文但没写学校 → 不猜 school/address，_type 不变
_ns = _by_key.get(_nmap.get(5, ""), {})
check("F3 反例：CN 学位论文无学校 → 不写 school/address、不改 _type（不猜）",
      not _ns.get("school") and not _ns.get("address") and _ns.get("_type") != "phdthesis",
      repr({k: _ns.get(k) for k in ("_type", "school", "address")}))
# 反例②：SEP 章节条目没有 URL → 有 booktitle、无 url（不臆造网址）
_nu = _by_key.get(_nmap.get(6, ""), {})
check("F3 反例：SEP 章节无网址 → 写 booktitle 但不写 url（不臆造）",
      _nu.get("booktitle") == "The Stanford Encyclopedia of Philosophy"
      and not _nu.get("url"), repr({k: _nu.get(k) for k in ("booktitle", "url")}))


# ══ B1（验收 FAIL 修）：编号清单 EN journal（footref 未解析 → extras 补）════════
print()
print("── B1 编号清单 EN journal 提取（apply_numbered_extras）──")
from slcn.wordrefs import apply_numbered_extras  # noqa: E402
from slcn.docxsource import _match_en_journal  # noqa: E402

_B1_RAW = ("[5] P. Benacerraf, 1981, “Frege: The last logicist”, "
           "Midwest Studies in Philosophy, 6(1): 17–35.")
_b1e = apply_numbered_extras(
    {"_type": "article", "author": "Benacerraf, P.", "year": "1981",
     "title": "Frege: The last logicist", "journal": "", "volume": "6",
     "number": "1", "pages": "17--35"},
    _B1_RAW, match_journal=_match_en_journal)
check("B1 正例：编号清单 `“题名”, 刊名, 卷(期): 页` → journal=Midwest Studies in Philosophy",
      _b1e.get("journal") == "Midwest Studies in Philosophy", repr(_b1e.get("journal")))
_b1nb = apply_numbered_extras(
    {"_type": "book", "author": "王五", "year": "2015", "title": "某书",
     "journal": ""},
    "[6] 王五，2015，某书，北京：商务印书馆。", match_journal=_match_en_journal)
check("B1 反例：无刊名形态的编号条目 → 不写 journal（不猜）",
      not _b1nb.get("journal"), repr(_b1nb.get("journal")))
_b1keep = apply_numbered_extras(
    {"_type": "article", "journal": "Already Known", "year": "1981"}, _B1_RAW,
    match_journal=_match_en_journal)
check("B1 反例：已有 journal 的条目不被覆盖（逐字节旧行为）",
      _b1keep.get("journal") == "Already Known", repr(_b1keep.get("journal")))

# ══ ⑦ B-220（90 期初排）：英文题目/作者/期刊形态修复（N1–N5）══════════════════
print("── ⑦ B-220：90 期文献解析修复（N1–N4）──")

from slcn.wordentry import _match_en_journal, parse_reference_line  # noqa: E402
from slcn.wordrefs import apply_numbered_extras  # noqa: E402


def parse_extras(line):
    """编号通道完整生产口径：parse_numbered_item + apply_numbered_extras。"""
    p = parse1(line)
    d = {"_type": p.etype, "author": p.author, "title": p.title, "journal": p.journal,
         "booktitle": p.booktitle, "volume": p.volume, "number": p.number,
         "pages": p.pages, "publisher": p.publisher, "address": p.address,
         "year": p.year, "note": p.note}
    return apply_numbered_extras(d, line, match_journal=_match_en_journal)


# N1：引号题名尾标点（西式 `…,”`）剥离；题名其余部分不动
_p = parse1('Harry Frankfurt, 1971, “Freedom of Will and the Concept of a Person,” '
            'The Journal of Philosophy, Vol. 68 (1), pp. 5-20.')
check("⑦ N1 引号题名尾逗号剥离", _p.title == "Freedom of Will and the Concept of a Person",
      repr(_p.title))
check("⑦ N1 题名内逗号不受影响（含逗号书名不截断）",
      parse_reference_line("A. J. Ayer, 1946, Language, Truth and Logic, 2nd ed., "
                           "London: Victor Gollancz.").title == "Language, Truth and Logic")

# N2：无引号英文题名逗号续接；出版信息（版次/地: 社）不并入
_p2 = parse1('A. J. Ayer, 1946, Language, Truth and Logic, 2nd ed., '
             'London: Victor Gollancz.')
check("⑦ N2 题名续接（Ayer1946）", _p2.title == "Language, Truth and Logic", repr(_p2.title))
check("⑦ N2 续接后出版信息仍解析", _p2.publisher == "Victor Gollancz" and
      _p2.address == "London", f"{_p2.publisher!r} {_p2.address!r}")
_p2n = parse1('L. Savage, 1972, The Foundations of Statistics. Second edition, '
              'New York: Dover Publishing.')
check("⑦ N2 反例：版次不并入题名", _p2n.title == "The Foundations of Statistics. Second edition",
      repr(_p2n.title))

# N3：多作者编号清单——续段像「首字母+姓」且其后紧跟裸年份才合并
_p3 = parse1('R. E. Young, A. L. Becker and K. L. Pike, 1970, Rhetoric: Discovery '
             'and Change, New York: Harcourt, Brace & World.')
check("⑦ N3 多作者合并（Young1970）", "Becker" in _p3.author and "Pike" in _p3.author
      and _p3.title == "Rhetoric: Discovery and Change",
      f"{_p3.author!r} / {_p3.title!r}")
_p3n = parse1('J. Doe, 1990, B. Smith and K. Jones, The Book, New York: Routledge.')
check("⑦ N3 反例：作者形态段后无裸年份 → 不合并（维持旧行为）",
      "Becker" not in _p3n.author and "Smith" not in _p3n.author.split(",")[0],
      repr(_p3n.author))

# N4：Vol./No./pp. 简写期刊形态（旧正则只认 `68(1): 5-20`）
_e4 = parse_extras('Harry Frankfurt, 1971, “Freedom of Will and the Concept of a '
                   'Person,” The Journal of Philosophy, Vol. 68 (1), pp. 5-20.')
check("⑦ N4 期刊简写形态（Vol./pp.）→ journal/volume/number/pages",
      (_e4.get("journal"), _e4.get("volume"), _e4.get("number"), _e4.get("pages"))
      == ("The Journal of Philosophy", "68", "1", "5--20"),
      f"{_e4.get('journal')!r} {_e4.get('volume')!r} {_e4.get('number')!r} {_e4.get('pages')!r}")
# N4：刊名含单词 in 不再被容器判据误杀
_e4b = parse_extras('T. Williamson, 2009, “Probability and Danger”, The Amherst '
                    'Lecture in Philosophy, 4: 1–35.')
check("⑦ N4 刊名含 in（The Amherst Lecture in Philosophy）不被误杀",
      _e4b.get("journal") == "The Amherst Lecture in Philosophy"
      and _e4b.get("_type") == "article", f"{_e4b.get('journal')!r} {_e4b.get('_type')!r}")
# N4：中文期刊 `YYYY第N期` + `+N` 补充页
_e4c = parse_extras('费多益，“倾向：因果观念重建”，中国社会科学，2023第9期，第122-143+207页。')
check("⑦ N4 中文期刊 YYYY第N期 / +N 页",
      (_e4c.get("journal"), _e4c.get("number"), _e4c.get("pages"))
      == ("中国社会科学", "9", "122--143"),
      f"{_e4c.get('journal')!r} {_e4c.get('number')!r} {_e4c.get('pages')!r}")

# N5：`in 编者列表, eds., 书名, 地: 社` 容器形态（旧判据全落空）
_e5 = parse_extras('J. P. Dillard, 1990, “The Nature and Substance of Goals in '
                   'Tactical Communication”, in M. J. Cody and M. L. McLaughlin, eds., '
                   'The Psychology of Tactical Communication, Clevedon: Multilingual Matters.')
check("⑦ N5 编者列表在前 → booktitle/editor/类型",
      _e5.get("booktitle") == "The Psychology of Tactical Communication"
      and "Cody" in (_e5.get("editor") or "") and _e5.get("_type") == "incollection",
      f"{_e5.get('booktitle')!r} {_e5.get('editor')!r} {_e5.get('_type')!r}")
_e5n = parse_extras('J. Doe, 2000, “Some Chapter”, in Some Book Without Marker, '
                    'New York: Routledge, pp. 1-10.')
check("⑦ N5 反例：无编者标记 + 无年份锚点 → 不编 booktitle",
      not _e5n.get("booktitle"), repr(_e5n.get("booktitle")))

# ══ ⑧ B-248：Word 脚注书目（无类型标记）作者切分 + 类型/出版社回填 ════════════
# 87-9 实测 8 条只印作者：`_split_author_title` 在中名缩写 `W.` 后的 `. ` 切分，
# 且无 `[J]/[M]` 标记时 `parse_one` 不做出版社/类型回填。夹具串全部取自源稿 fn2–fn6。
print("\n── ⑧ B-248：87-9 脚注书目残缺修复（Tindale / van Eemeren / Frank / Bitzer / 中文反例）──")


def parse_note1(line):
    ps = footref.parse_note(line)
    return ps[0] if ps else None


_p8 = parse_note1("Christopher W. Tindale, The Philosophy of Argument and Audience "
                  "Reception, Cambridge University Press, 2015.")
check("⑧ ① Tindale：不在中名缩写后切（author 以 Tindale 起、题名不以 Tindale 起、@book+出版社）",
      _p8 is not None and _p8.author.startswith("Tindale,")
      and not _p8.title.startswith("Tindale") and _p8.etype == "book"
      and "Cambridge University Press" in _p8.publisher,
      f"{_p8 and _p8.author!r} / {_p8 and _p8.title!r} / {_p8 and _p8.etype!r} / "
      f"{_p8 and _p8.publisher!r}")

_p8b = parse_note1("Frans H. van Eemeren, et al, Handbook of Argumentation Theory, "
                   "Springer Reference, 2014.")
_p8c = parse_note1("M. Bolduc & David A. Frank (eds.), The Intellectual and Cultural "
                   "Origins of Chaïm Perelman, Brill Academic Pub, 2023.")
check("⑧ ② van Eemeren / Frank：作者 family 正确、题名不混作者、均 @book",
      _p8b is not None and _p8c is not None
      and "van Eemeren" in _p8b.author and _p8b.title.startswith("Handbook of Argumentation")
      and _p8b.etype == "book"
      and "Frank" in _p8c.author and _p8c.title.startswith("The Intellectual")
      and _p8c.etype == "book",
      f"{_p8b and _p8b.author!r}/{_p8b and _p8b.etype!r} | "
      f"{_p8c and _p8c.author!r}/{_p8c and _p8c.etype!r}")

_p8d = parse_note1("L. Bitzer, “The Rhetorical Situation”, Philosophy and Rhetoric, "
                   "Vol. 1, No. 1, 1968, pp. 1-14.")
check("⑧ ③ Bitzer 引号条：etype=article、author=Bitzer, L.、刊名/年/页",
      _p8d is not None and _p8d.etype == "article" and _p8d.author == "Bitzer, L."
      and _p8d.journal == "Philosophy and Rhetoric" and _p8d.year == "1968"
      and _p8d.pages == "1--14",
      f"{_p8d and _p8d.etype!r} {_p8d and _p8d.author!r} "
      f"{_p8d and _p8d.journal!r} {_p8d and _p8d.year!r} {_p8d and _p8d.pages!r}")

_p8e = parse_note1("黑格尔（著），逻辑学，北京：商务印书馆，2004年。")
check("⑧ ④ 中文反例：作者/类型不变（仍 @misc、不把中文出版信息当英文书目）",
      _p8e is not None and _p8e.etype == "misc" and _p8e.author == ""
      and _p8e.title.startswith("黑格尔"),
      f"{_p8e and _p8e.etype!r} {_p8e and _p8e.author!r} {_p8e and _p8e.title!r}")

# ══ ⑨ B-262：SEP→incollection + 解析失败原文兜底进 author ═════════════════════
print("\n── ⑨ B-262：SEP 类型判据 / 解析失败 verbatim author ──")

# SEP 判据：journal 命中 → @incollection，且容器名从 journal 挪到 booktitle（否则不排印）
_e9sep = footnotes.parse_entry(
    'Hansson, S. O., “Preferences”, The Stanford Encyclopedia of Philosophy (2022), pp. 1-10.')
check("⑨ SEP 词条（journal 位）：@incollection、booktitle 承接、journal 清空",
      _e9sep.get("_type") == "incollection"
      and _e9sep.get("booktitle") == "The Stanford Encyclopedia of Philosophy"
      and not _e9sep.get("journal") and _e9sep.get("pages") == "1--10",
      repr({k: _e9sep.get(k) for k in ("_type", "booktitle", "journal", "pages")}))

# 宽口径（模板例外同款）：`Encyclopedia of Philosophy` 也判 incollection
_e9sep2 = footnotes.parse_entry(
    'Hansson, S. O., “Preferences”, Encyclopedia of Philosophy (2022).')
check("⑨ SEP 宽口径：`Encyclopedia of Philosophy` 亦判 @incollection",
      _e9sep2.get("_type") == "incollection"
      and _e9sep2.get("booktitle") == "Encyclopedia of Philosophy",
      repr({k: _e9sep2.get(k) for k in ("_type", "booktitle")}))

# 反例：普通刊名仍是 @article（判据不能误伤）
_e9art = footnotes.parse_entry(
    'Smith, J., “A Paper”, Journal of Philosophy (2020), pp. 1-10.')
check("⑨ 反例：普通期刊仍是 @article（SEP 判据不误伤）",
      _e9art.get("_type") == "article" and _e9art.get("journal") == "Journal of Philosophy",
      repr({k: _e9art.get(k) for k in ("_type", "journal")}))

# 解析失败兜底：@misc + 原文逐字符进 author（不再重复塞 note）
# 初排出口先把原文记在 note；`expand_note_entries()` 再拆不动时才落 author。
def _fallback_bib(key, raw):
    first = footnotes.entry_to_bib(key, footnotes.parse_entry(raw), raw)
    second, _notes = bibnorm.expand_note_entries(first)
    return second


_src9 = 'Some complex reference, with commas; and “quotes” -- and more.'
_b9f = _fallback_bib("Fallback9", _src9)
check("⑨ 解析失败：@misc、author 为原文逐字符（花括号保护）、无 note 重复",
      _b9f.startswith("@misc{Fallback9,")
      and "{{" + _src9 + "}}" in _b9f and "  note" not in _b9f,
      _b9f.replace("\n", " "))

# 中文复杂文献（著/译体例，解析器本来不认）→ 同一兜底
_src9cn = "这是一条无法拆分的复杂中文文献说明，包含著者、译者、书名、出版地与出版社等信息，但没有年份。"
_b9fc = _fallback_bib("FallbackCN9", _src9cn)
check("⑨ 解析失败（中文）：author 逐字符保留、无 note",
      "{{" + _src9cn + "}}" in _b9fc and "  note" not in _b9fc,
      _b9fc.replace("\n", " "))

# BibTeX 必要保护：{} 会让 biber 括号失衡 → textbraceleft/textbraceright；% & ~ 转义
_src9b = "Braces {a}, 50% and tilde ~ — plus a long tail to exceed forty characters."
_b9fb = _fallback_bib("FallbackB9", _src9b)
check("⑨ 兜底转义：花括号用 \textbraceleft{}/\textbraceright{}，% ~ 转义",
      "\\textbraceleft{}a\\textbraceright{}" in _b9fb
      and "50\\%" in _b9fb and "\\textasciitilde{}" in _b9fb,
      _b9fb.replace("\n", " "))

# \bibitem 兜底（手写参考文献 → 初排转换）：同一形态，render 出口也只写 author
_body9 = "A discursive reference line with commas, no colon and no bibliographic tail."
_e9bit = footref.parse_bibitem_entry(_body9)
_b9bit = footref.render_bibitem_entry({**_e9bit, "key": "BibFallback9"})
check("⑨ \\bibitem 解析失败：@misc、author 逐字符、无 title/note",
      _b9bit.startswith("@misc{BibFallback9,")
      and "author = {{" + _body9 + "}}" in _b9bit
      and "title =" not in _b9bit and "note =" not in _b9bit,
      _b9bit.replace("\n", " "))

# 汇总 ──
print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
