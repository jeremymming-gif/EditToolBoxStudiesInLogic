#!/usr/bin/env python3.14
"""编号清单里的**中文期刊体例**（刊名在年份之前）必须解析出 `journal` + `@article`。

第 65 条（2026-10-15 实跑暴露）。真实来稿 `2026年第5期(90)/01 Meimei_Han/定稿.docx`
文末编号清单：

    [17] 党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。
    [18] 党学哲，“证明论语义及其意义颗粒度”，逻辑学研究，2024年第1期，第69–86页。

旧实现第 17 条变成 `@misc`：author/title/number/pages/year 全对，**唯独 journal 空**
（`@misc` 的字段序里没有 journal，刊名就此整个消失）；第 18 条却正常——差别只在
刊名带不带括号后缀（`（社会科学版）`）。旧 m2 判据要求「纯汉字刊名 + 逗号 + 年/期」，
于是括号后缀、书名号 `《逻辑学研究》2025年第4期`、以及题名里带嵌套引号
（`…以“詏”为例”`，把 `rest` 的开头污染成引号残渣）三类全落空。

本测试守住：
  ① 主诉两条（真实原文）→ `@article` + 正确 journal/number/pages/year，且 `userf = {cn}`；
  ② 刊名形态变体（全/半角括号、书名号有/无逗号、数字与「年/期」之间的空格）；
  ③ 题名里的嵌套引号不再截断（题名完整 + journal 也拿到）；
  ④ **反例**：书（出版社/出版地）、学位论文、journal 那一格其实是书名、
     「刊名 == 题名」的自相矛盾条目、英文条目——一律**不**升成 @article；
  ⑤ 端到端：现造最小 docx（参考文献区 + 编号清单）→ `parse_docx` → `entry_to_bib`；
  ⑥ 真实语料（不在本机则 SKIP）：Meimei_Han 的定稿两条 Dang。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_cn_journal_numbered.py
"""
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footref  # noqa: E402
from slcn.docxsource import parse_docx  # noqa: E402
from slcn.footnotes import entry_to_bib  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def parse1(line: str) -> footref.Parsed:
    """按生产口径：先剥掉 `[N]` 前缀，再解析单条。"""
    seg = re.sub(r"^\s*[\[\(（]\s*\d{1,3}\s*[\]\)）]\s*", "", line.strip())
    return footref.parse_numbered_item(seg)


# ══ ① 主诉：Meimei_Han 第 17/18 条（真实原文）════════════════════════════════
print("── ① 主诉：刊名在年份之前的中文期刊 ──")

DANG17 = "[17] 党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。"
DANG18 = "[18] 党学哲，“证明论语义及其意义颗粒度”，逻辑学研究，2024年第1期，第69–86页。"
p17, p18 = parse1(DANG17), parse1(DANG18)

check("① 第 17 条：类型是 @article（不再是 @misc）", p17.etype == "article", p17.etype)
check("① 第 17 条：journal 拿到带括号后缀的刊名",
      p17.journal == "湖南科技大学学报（社会科学版）", repr(p17.journal))
check("① 第 17 条：author/title/year/number/pages 一字不变",
      (p17.author, p17.title, p17.year, p17.number, p17.pages)
      == ("党学哲", "论作为证据逻辑的核证逻辑", "2022", "6", "29--38"),
      f"{p17.author!r} {p17.title!r} {p17.year!r} {p17.number!r} {p17.pages!r}")
check("① 第 18 条（本来就对）：不被新规则改动",
      (p18.etype, p18.journal, p18.number, p18.pages)
      == ("article", "逻辑学研究", "1", "69--86"),
      f"{p18.etype} {p18.journal!r} {p18.number!r} {p18.pages!r}")

# 经 entry_to_bib：中文条目自动补 userf = {cn}
bib17 = entry_to_bib("Dang2022", {
    "_type": p17.etype, "author": p17.author, "title": p17.title,
    "journal": p17.journal, "number": p17.number, "pages": p17.pages, "year": p17.year,
})
check("① entry_to_bib：@article 里带 journal 字段",
      "@article{Dang2022," in bib17 and "journal = {湖南科技大学学报（社会科学版）}" in bib17, bib17)
check("① entry_to_bib：中文条目自动补 userf = {cn}",
      re.search(r"userf\s*=\s*\{cn\}", bib17) is not None, bib17)

# ══ ② 刊名形态变体 ═══════════════════════════════════════════════════════════
print("\n── ② 刊名形态变体 ──")
VARIANTS = [
    ("[2] 汤明，“同时性因果和物理学论证”，《哲学研究》2022年第4期，第108-117页。",
     "哲学研究", "4", "108--117"),
    ("[4]李雷，逻辑例外论的演变，《逻辑学研究》2022年第5期，第38页。",
     "逻辑学研究", "5", ""),
    # `第1-10+20页`（不连续页码）里的 `+20`：B-220 N4c 起 `_pages` 取主页码段
    #   （与「第24–43、204–205页」同口径：取第一段；`+N` 是补充材料页、不进 pages）。
    ("[6]万小龙，陈明益，冉奎，“真值函数与非真值函数的等值变换——一元算符逻辑理论五探”，"
     "山东科技大学学报(社会科学版)， 2012年第6期，第1-10+20页。",
     "山东科技大学学报(社会科学版)", "6", "1--10"),
    ("[34] 周明，“功能主义多元论”，中山大学学报（社会科学版），2020年第4期，第134–140页。",
     "中山大学学报（社会科学版）", "4", "134--140"),
    # 数字与「年/期」之间有空格（实测 09 Jiawei_Zhong 体例）
    ("[17] 顿新国，“确证逻辑的研究困境及其解决原则”，江西社会科学，2018 年第 12 期，第5-11页。",
     "江西社会科学", "12", "5--11"),
    # 期号带前导零 → 归一为不带零
    ("[20] 张三，“题名题名题名”，逻辑学研究，2019年第06期，第1-9页。",
     "逻辑学研究", "6", "1--9"),
]
for i, (line, j, n, pg) in enumerate(VARIANTS):
    p = parse1(line)
    check(f"② 变体 {i + 1}：journal={j} / number={n} / pages={pg or '（无）'}",
          p.etype == "article" and p.journal == j and p.number == n and p.pages == pg,
          f"{p.etype} {p.journal!r} {p.number!r} {p.pages!r}")

# ══ ③ 题名里的嵌套中文引号 ═══════════════════════════════════════════════════
#
# 实测 2026年第1期(86)/01 Fan_Yang：`“广式早茶说理的功能结构分析——以“詏”为例”`。
# 旧 `_title_quoted` 取第一个右引号 → 题名残成 `…——以“詏`；`rest` 又以 `詏”为例”`
# 开头，刊名判据跟着全部落空（条目退化成 @misc、刊名整个丢掉）。
print("\n── ③ 题名里的嵌套引号 ──")
_nest = parse1("[16] 麦劲恒，“广式早茶说理的功能结构分析——以“詏”为例”，逻辑学研究，"
               "2019年第2期，第86-97页。")
check("③ 嵌套引号：题名完整到最外层收尾引号",
      _nest.title == "广式早茶说理的功能结构分析——以“詏”为例", repr(_nest.title))
check("③ 嵌套引号：author / journal / number / pages 都对",
      (_nest.author, _nest.journal, _nest.number, _nest.pages)
      == ("麦劲恒", "逻辑学研究", "2", "86--97"),
      f"{_nest.author!r} {_nest.journal!r} {_nest.number!r} {_nest.pages!r}")
# 反例：引号里不足 4 字 / 收尾引号后紧跟 `[J]`（英文体例）——旧行为不许变
check("③ 反例：英文 `“题名”[J]` 形态题名照旧拿到",
      footref._title_quoted('Burge, T. “Epistemic Paradox”[J]. The Journal of Philosophy')
      == "Epistemic Paradox")

# ══ ④ 反例：不许被误判成期刊 ═════════════════════════════════════════════════
print("\n── ④ 反例：本来不该命中 ──")
NEG = [
    # 书：出版社/出版地，没有「YYYY年第N期」
    ("[8] 付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。", "book",
     "", "社会科学文献出版社", "北京"),
    # 书：第二段是书名（journal 那一格实际是书名）
    ("[10] 张三，“题名题名”，现代逻辑导论，北京：北京大学出版社，2020年。", "book",
     "", "北京大学出版社", "北京"),
    # 学位论文
    ("[9] 张三，题名题名题名，博士学位论文，北京：北京大学，2020年。", "misc", "", "", ""),
    # 「刊名 == 题名」的自相矛盾条目：刊名那一格其实是题名 → 不升级（保持原样）
    ("[6] 徐强：《归纳使真者与真之定义》，《武汉科技大学学报》，2024年第1期。", "misc",
     "", "", ""),
    # 没有年份的 `刊名，第N期`（刊名与题名分不开）→ 不认
    ("[13] 张三，题名题名题名，2020年，第3期，第1-5页。", "misc", "", "", ""),
]
for i, (line, et, j, pub, addr) in enumerate(NEG):
    p = parse1(line)
    check(f"④ 反例 {i + 1}：仍是 @{et}、journal 不写死",
          p.etype == et and p.journal == j,
          f"{p.etype} J={p.journal!r} pub={p.publisher!r} addr={p.address!r}")
    if pub:
        check(f"④ 反例 {i + 1}：出版社/出版地照旧识别", p.publisher == pub and p.address == addr,
              f"pub={p.publisher!r} addr={p.address!r}")

# 英文条目（同一份清单里混排）一个字都不许变
EN = [
    ('[1] Jc Beall, 2000, "Fitch\' Proof, Verificationism, and the Knower Paradox", '
     "Australasian Journal of Philosophy, 78(2): 241-247.",
     "article", "Australasian Journal of Philosophy", "2", "241--247"),
    ('[2] T. Burge, 1984, “Epistemic Paradox”, The Journal of Philosophy, 81(1): 5–29.',
     "article", "The Journal of Philosophy", "1", "5--29"),
    ("[5] M.Dummett, 1991, The Logical Basis of Metaphysics, Cambridge, MA: "
     "Harvard University Press.", "book", "", "", ""),
]
for i, (line, et, j, n, pg) in enumerate(EN):
    p = parse1(line)
    check(f"④ 英文条目 {i + 1}：{et} / journal={j or '（无）'} 不变",
          p.etype == et and p.journal == j and p.number == n and p.pages == pg,
          f"{p.etype} {p.journal!r} {p.number!r} {p.pages!r}")

# 中文著/译体例（第 64 条）必须与旧行为逐字段一致
_tr = parse1("[16] 阿斯海姆（著），郑建国、万林（译），指称与意向性，南京：南京大学出版社，2014年。")
check("④ 反例：中文著/译体例（第 64 条）作者/题名不变",
      _tr.author == "阿斯海姆（著），郑建国、万林（译）" and _tr.title == "指称与意向性",
      f"{_tr.author!r} {_tr.title!r}")
check("④ 反例：中文著/译体例仍是 @book + 出版社",
      _tr.etype == "book" and _tr.publisher == "南京大学出版社", f"{_tr.etype} {_tr.publisher!r}")

# ══ ⑤ 端到端：最小 docx → parse_docx → entry_to_bib ═════════════════════════
print("\n── ⑤ 端到端（合成最小 docx）──")
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TMP = []


def make_docx(paras):
    """只含 word/document.xml 的最小 docx（与 test_refmark_raw_refs.py 同法）。"""
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>' for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    TMP.append(path)
    return path


BODY = [f"正文第{i}段，讨论核证逻辑与证据逻辑之间的关系。" for i in range(20)]
REF = [
    "党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。",
    "党学哲，“证明论语义及其意义颗粒度”，逻辑学研究，2024年第1期，第69–86页。",
    "付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。",
    "Jc Beall, 2000, \"Fitch' Proof, Verificationism, and the Knower Paradox\", "
    "Australasian Journal of Philosophy, 78(2): 241-247.",
]
src = make_docx(["论核证逻辑", "党学哲", "摘要：本文讨论核证逻辑。", "关键词：核证逻辑；证据"]
                + BODY + ["参考文献"] + [f"[{i + 1}] {t}" for i, t in enumerate(REF)])
doc = parse_docx(src)
by_author = {}
for key, ent, _raw in doc.bib_entries:
    by_author.setdefault(ent.get("author", ""), []).append((key, ent))
check("⑤ 端到端：4 条都被编号清单通道解析出来", len(doc.bib_entries) == 4,
      f"{len(doc.bib_entries)} 条 / refs={len(doc.refs)}")
check("⑤ 端到端：第 1 条是 @article 且带括号后缀刊名",
      any(e["_type"] == "article" and e["journal"] == "湖南科技大学学报（社会科学版）"
          for _k, e in by_author.get("党学哲", [])),
      repr(by_author.get("党学哲")))
_bib = "\n".join(entry_to_bib(k, e) for k, e, _ in doc.bib_entries)
check("⑤ 端到端：写的 bib 里 `journal = {湖南科技大学学报（社会科学版）}` 与 `userf = {cn}` 同时出现",
      "journal = {湖南科技大学学报（社会科学版）}" in _bib
      and re.search(r"userf\s*=\s*\{cn\}", _bib) is not None, _bib)
check("⑤ 端到端：书与英文条目没被新规则带偏",
      "@book{" in _bib and "publisher = {社会科学文献出版社}" in _bib
      and "journal = {Australasian Journal of Philosophy}" in _bib, _bib)

# ══ ⑦ 回归：编号清单里**一条写歪**也不能把整份清单让给逗号兜底（案例 116）═════
print("\n── ⑦ 编号清单写歪一条 → 两条通道按覆盖条数取优 ──")
# 2026-10-17 实例测试回归：Xiaoyu_Lin 文末 22 条编号清单里有一条编号写歪了
# （`[13 J. Ross…` 少了 `]`），中文/英文逗号兜底把它误吃成 1 条 → `doc.refs` 非空
# → 旧逻辑「if not doc.refs」再也没走编号通道 → 整份 22 条只剩 1 条、正文引用全部没替换。
# 修法：两条通道都跑，**编号清单收得更多就以它为准**（见 docxsource.parse_docx ②）。
# 2026-10-18 第三十八批：写歪的那条已由 `_normalize_numbered_markers()` 补 `]` 收下
# （期 90/05 Xiaoyu_Lin 实测），因此期望从 3 条改为 4 条。
MALFORMED = [
    "[1] A. Author, 2020, “Title One”, Analysis, 1(2): 3-4.",
    "[2 B. Author, 2021, “Title Two”, Synthese, 2(3): 5-6.",   # ← 少了 `](写歪一条)
    "[3] C. Author, 2022, “Title Three”, Mind, 3(4): 7-8.",
    "[4] D. Author, 2023, “Title Four”, Noûs, 4(5): 9-10.",
]
src2 = make_docx(["论编号清单与逗号兜底的取舍", "作者",
                  "摘要：测试。", "关键词：测试"]
                 + BODY + ["参考文献"] + MALFORMED)
doc2 = parse_docx(src2)
_titles2 = [e.get("title") or "" for _k, e, _r in doc2.bib_entries]
check("⑦ 写歪一条后，编号清单收下 4 条（漏括号已补，不再被逗号兜底抢走）",
      len(doc2.bib_entries) == 4 and len(doc2.refs) == 0,
      f"bib_entries={len(doc2.bib_entries)} refs={len(doc2.refs)} titles={_titles2}")
check("⑦ 四条都在（含写歪的 Title Two）",
      sum(1 for t in _titles2 if t in ("Title One", "Title Two", "Title Three", "Title Four")) == 4,
      str(_titles2))
# 第四十五批 A2 回归（task-58）：新增的「引号题名 + URL / APA」人年制兜底也不得
# 抢编号清单行（曾把 `[1] … “Title”, …` 解析成 refs=4 → 编号通道被覆盖条数挤掉）。
NUM_LOOSE_SHAPED = [
    "[1] A. Author, 2020, “Title One”, https://example.org/one.",
    "[2] B. Author, 2021, “Title Two”, Unpublished manuscript.",
]
src2b = make_docx(["论编号清单与引号题名兜底", "作者",
                   "摘要：测试。", "关键词：测试"] + BODY + ["参考文献"] + NUM_LOOSE_SHAPED)
doc2b = parse_docx(src2b)
check("⑦ 新兜底（引号题名+URL 形态）不抢编号清单：bib_entries=2、refs=0",
      len(doc2b.bib_entries) == 2 and len(doc2b.refs) == 0,
      f"bib_entries={len(doc2b.bib_entries)} refs={len(doc2b.refs)}")

# 反例：真正的中文逗号风格（**不带编号**）仍走人年制通道，不能反过来被编号通道吞掉
CN_PLAIN = [
    "陈清泚，“论证研究的认知社会学路径”，《自然辩证法研究》，2020年第2期，第103–108页。",
    "郑建国，逻辑悖论研究引论，北京：人民出版社，2014年。",
]
src3 = make_docx(["论中文逗号风格参考文献", "作者",
                  "摘要：测试。", "关键词：测试"] + BODY + ["参考文献"] + CN_PLAIN)
doc3 = parse_docx(src3)
check("⑦ 反例：纯中文逗号风格（无编号）仍走人年制通道（bib_entries=0、refs=2）",
      len(doc3.refs) == 2 and len(doc3.bib_entries) == 0,
      f"refs={len(doc3.refs)} bib_entries={len(doc3.bib_entries)}")

# ══ ⑥ 真实语料（不在本机则 SKIP）════════════════════════════════════════════
print("\n── ⑥ 真实语料 ──")
REAL = os.path.join(_REPO, "2026年第5期(90)", "01 Meimei_Han", "定稿.docx")
if not os.path.exists(REAL):
    SKIPS.append("真实语料 2026年第5期(90)/01 Meimei_Han/定稿.docx 不在本机")
    print("SKIP  真实语料不在本机（2026年第5期(90)/01 Meimei_Han/定稿.docx）")
else:
    d = parse_docx(REAL)
    dang = {k: e for k, e, _ in d.bib_entries if k.startswith("Dang")}
    check("⑥ 真实稿：Dang2022 = @article + 湖南科技大学学报（社会科学版）",
          dang.get("Dang2022", {}).get("_type") == "article"
          and dang["Dang2022"]["journal"] == "湖南科技大学学报（社会科学版）",
          repr(dang.get("Dang2022")))
    check("⑥ 真实稿：Dang2024 照旧 = @article + 逻辑学研究",
          dang.get("Dang2024", {}).get("journal") == "逻辑学研究"
          and dang["Dang2024"]["_type"] == "article", repr(dang.get("Dang2024")))

for f in TMP:
    try:
        os.unlink(f)
    except OSError:
        pass

print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + repr(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
