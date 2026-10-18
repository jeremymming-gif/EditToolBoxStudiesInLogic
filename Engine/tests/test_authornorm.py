#!/usr/bin/env python3.14
"""英文作者一律倒装（编辑部 2026-10-15 决定）的判据测试。

**要解决的问题**（实测，不是设想）：真实稿件 `2026年第5期(90)/01 Jingru_Kou`。
工具的 `reference.bib` 把作者写成**名在前**（`T. Burge`、`D. J. Chalmers`、`M. Dummett`），
编辑的成品 20 条英文条目 **20/20 是倒装式**（`Burge, T.`、`Chalmers, D. J.`、`Kvanvig, J.`）。
旧规范写着「**本名倒装**仍用逗号……两种写法都对」+「非必要不修改」，AI 照抄源稿 → 15 处里差 14 处。
现在「英文作者/编者一律倒装」是**硬规则**，本套件把它钉死。

八组断言（都是真串，取自该稿的 docx 解析结果与编辑成品）：
  ① 20 条真实作者串 → 倒装结果逐条比对（含无空格的 `M.Dummett`、`J.Kvanvig`）；
  ② 编辑成品的 5+2 条倒装式 → **幂等**（字节不变）；
  ③ 反例：中文姓名、中日译著串、`van~Fraassen`、已倒装、`{Salerno (ed.)}, J.`、
     `JC Beall`（手册点名「唯一正确写法，需特殊处理」）、`Sammy Davis Jr.`、组织名/混写串；
  ④ 多作者 ` and `：**每一个**都倒装；`others` 不动；全有或全无（不许出现半倒装混合条）；
  ⑤ 引擎侧真的接上了：`footref.parse_numbered_list`（编号清单）、`docxsource._bib_author`
     （文末人年制区）、`footnotes.entry_to_bib`（写 bib 的最后一道）；
  ⑥ 离线检查器：`bibcheck.author_form_issues` 把未倒装报成 **must**，且
     `bibcheck.check()` **不含**这条（冻结基线 `out/docx_audit.csv` 的口径不许动）；
  ⑦ 中文译著提示：只有「有（著）缺（译）」或「有（译）缺（著）」才报，普通中文专著不报；
  ⑧ `checkbib.py` 命令行：未倒装 → 问题（退出码 1）、译著缺标记 → 只提示（退出码 0）。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_authornorm.py
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck, bibnorm, footref  # noqa: E402
from slcn.docxsource import _bib_author  # noqa: E402
from slcn.footnotes import entry_to_bib  # noqa: E402

FAILS = []
SKIPS = []

N = bibnorm.normalize_author_field
INV = bibnorm.author_is_inverted


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ── ① 20 条真实作者串：工具现在解析出来的原文（`定稿.docx`，逐字抄） ────────
#    前 15 条英文来自 docx 的编号清单（第 16–20 条是中文）。
RAW_20 = [
    "A. J. Ayer", "T. Burge", "D. J. Chalmers", "D. J. Chalmers", "M.Dummett",
    "D. Edgington", "F. B. Fitch", "M.Jago", "D. Kaplan", "J.Kvanvig",
    "H.Leitgeb", "R. Nozick", "J. Salerno (ed.)", "R. Stalnaker", "T. Williamson",
    "阿斯海姆（著）", "党学哲", "党学哲", "克里普克（著）", "沃斯等（著） and 黄海婷（译）",
]
EXPECT_20 = [
    "Ayer, A. J.", "Burge, T.", "Chalmers, D. J.", "Chalmers, D. J.", "Dummett, M.",
    "Edgington, D.", "Fitch, F. B.", "Jago, M.", "Kaplan, D.", "Kvanvig, J.",
    "Leitgeb, H.", "Nozick, R.", "{Salerno (ed.)}, J.", "Stalnaker, R.", "Williamson, T.",
    "阿斯海姆（著）", "党学哲", "党学哲", "克里普克（著）", "沃斯等（著） and 黄海婷（译）",
]

print("── ① 20 条真实作者串（Jingru_Kou 的 docx 解析结果）──")
bad = [(a, N(a), b) for a, b in zip(RAW_20, EXPECT_20) if N(a) != b]
check("① 20/20 条都转成编辑成品的形态", not bad, f"不等 {bad}")
check("① 15 条英文全部变成「姓, 名」",
      all("," in N(a) for a in RAW_20[:15]))
check("① 无空格的 `M.Dummett` / `J.Kvanvig` / `H.Leitgeb` 也能拆开",
      N("M.Dummett") == "Dummett, M." and N("J.Kvanvig") == "Kvanvig, J."
      and N("H.Leitgeb") == "Leitgeb, H.")
check("① 编者标记 `(ed.)` 连姓带括号放进花括号（编辑成品原样）",
      N("J. Salerno (ed.)") == "{Salerno (ed.)}, J.")
check("① 中文作者逐字节不动（含 `and` 连接的中文译著串）",
      all(N(a) == a for a in RAW_20[15:]))

# ── ② 编辑成品里的倒装式：幂等 ────────────────────────────────────────────
FINAL_FORMS = ["Burge, T.", "Chalmers, D. J.", "Dummett, M.", "Kvanvig, J.",
               "{Salerno (ed.)}, J.", "Ayer, A. J.", "Fitch, F. B."]
print("\n── ② 编辑成品（reference.bib）里的倒装式：幂等 ──")
check("② 7/7 条字节不变", all(N(x) == x for x in FINAL_FORMS),
      f"{[x for x in FINAL_FORMS if N(x) != x]}")
check("② 都被判为「已倒装」", all(INV(x) for x in FINAL_FORMS))
check("② 全名缩写：保留全部中名首字母",
      N("Jonathan L. Kvanvig") == "Kvanvig, J. L." and N("W. V. O. Quine") == "Quine, W. V. O.")
check("② 漏写的句点补上：`Jerry L Morgan` → `Morgan, J. L.`",
      N("Jerry L Morgan") == "Morgan, J. L.")

# ── ③ 反例：本来就不该动的 ────────────────────────────────────────────────
print("\n── ③ 反例（不该动 / 不该误判）──")
COUNTER = {
    "党学哲": "党学哲",                          # 中文姓名
    "太田方": "太田方",                          # 日文（汉字）
    "阿斯海姆（著），张建军、万林（译）": "阿斯海姆（著），张建军、万林（译）",   # 中文译著：逗号/顿号保留
    "克里普克（著），梅文（译）": "克里普克（著），梅文（译）",
    "沃斯等（著） and 黄海婷（译）": "沃斯等（著） and 黄海婷（译）",             # 中文条目里已是 and
    "van~Fraassen": "van~Fraassen",              # 波浪号（手册：要按 V 排就加 ~）
    "van Fraassen, B. C.": "van Fraassen, B. C.",  # 已倒装 + 虚词
    "van~Fraassen, B. C.": "van~Fraassen, B. C.",
    "van Eemeren, F. H.": "van Eemeren, F. H.",
    "JC Beall": "JC Beall",                      # 手册：唯一正确写法，需特殊处理 → 不推断
    "Jc Beall": "Jc Beall",
    "Sammy Davis Jr.": "Sammy Davis Jr.",        # 后缀不是姓
    "J. Almog, J. Perry, and H. Wettstein (eds.)":
        "J. Almog, J. Perry, and H. Wettstein (eds.)",   # 逗号 + and 混写：不产出混合条
    "Kahneman D., Slovic P., & Tversky A.": "Kahneman D., Slovic P., & Tversky A.",
    "others": "others",
    "李晋霞": "李晋霞",
    "Sixu Li [韩笑、李思旭]": "Sixu Li [韩笑、李思旭]",   # 注记里的汉字/顿号不算
}
for src, want in COUNTER.items():
    check(f"③ 不动 {src!r}", N(src) == want, f"得到 {N(src)!r}")
check("③ 含花括号/LaTeX 命令的一律不动",
      N("Melli{\\`{e}}s, Paul{-}Andr{\\'{e}}") == "Melli{\\`{e}}s, Paul{-}Andr{\\'{e}}"
      and N("{\\AA}qvist, T.") == "{\\AA}qvist, T.")

# ── ④ 多作者 ──────────────────────────────────────────────────────────────
print("\n── ④ 多作者 ` and `：每一个都倒装 ──")
check("④ `J. P. Dillard and C. Segrin` → 两个都倒装",
      N("J. P. Dillard and C. Segrin") == "Dillard, J. P. and Segrin, C.")
check("④ 三个作者",
      N("J. P. Dillard and C. Segrin and J. M. Harden")
      == "Dillard, J. P. and Segrin, C. and Harden, J. M.")
check("④ `others` 原样保留（手册：>4 作者写 `{D. Lewis and others}`）",
      N("D. M. Gabbay and others") == "Gabbay, D. M. and others")
check("④ 虚词并进姓、虚词之后的姓大写", N("B. C. van Fraassen") == "van Fraassen, B. C."
      and N("W. van der Hoek") == "van der Hoek, W.")
check("④ 缺逗号的「姓 缩写」补逗号：`Kahneman D.` → `Kahneman, D.`",
      N("Kahneman D.") == "Kahneman, D." and N("van der Hoek W.") == "van der Hoek, W.")
check("④ **全有或全无**：一段判不出来就整条不动（不许半倒装）",
      N("T. Burge and Kahneman D., Slovic P.") == "T. Burge and Kahneman D., Slovic P.")
check("④ `&` 分隔也算多作者（分隔符原样保留）",
      N("T. Burge & D. Kaplan") == "Burge, T. & Kaplan, D.")

print("\n── ⑤ 幂等（对所有样本再跑一遍不变） ──")
ALL = RAW_20 + FINAL_FORMS + list(COUNTER) + [
    "J. P. Dillard and C. Segrin", "B. C. van Fraassen", "Kahneman D.",
]
finite = [(x, N(x)) for x in ALL if N(N(x)) != N(x)]
check("⑤ 全部样本满足 N(N(x)) == N(x)", not finite, f"{finite}")

# ── ⑤ 引擎侧接线 ──────────────────────────────────────────────────────────
print("\n── ⑤ 引擎侧真的接上了 ──")
check("⑤ docxsource._bib_author：逗号+and 混写先拆再倒装",
      _bib_author("J. P. Dillard, C. Segrin and J. M. Harden")
      == "Dillard, J. P. and Segrin, C. and Harden, J. M.")
check("⑤ docxsource._bib_author：中文条目照旧",
      _bib_author("党学哲") == "党学哲")
bib = entry_to_bib("Burge1984", {"_type": "article", "author": "Burge, T.",
                                "title": "Epistemic Paradox", "year": "1984"})
check("⑤ footnotes.entry_to_bib 不会把倒装名再拆开", "author = {Burge, T.}" in bib, bib.splitlines()[1])

# 编号清单通道：把该稿 20 行的**原文**喂给 footref（与 docx 解析同一入口）
RAW_LINES = [
    "[1] A. J. Ayer, 1946, Language, Truth and Logic, 2nd ed., London: Victor Gollancz.",
    "[2] T. Burge, 1984, “Epistemic Paradox”, The Journal of Philosophy, 81(1): 5–29.",
    "[5] M.Dummett, 1991, The Logical Basis of Metaphysics, Cambridge, MA: Harvard University Press.",
    "[9] D. Kaplan, 1989, “Demonstratives”, in J. Almog, J. Perry, and H. Wettstein (eds.),"
    " Themes from Kaplan, New York: Oxford University Press, pp. 481–563.",
    "[10] J.Kvanvig, 1995, “The Knowability Paradox and the Prospects for Anti-Realism”,"
    " Noûs, 29(4): 481–500.",
    "[16] 阿斯海姆（著），张建军、万林（译），指称与意向性，南京：南京大学出版社，2014年。",
]
parsed = footref.parse_numbered_list("\n".join(RAW_LINES))
got = [p.author for p in parsed]
check("⑤ 编号清单：英文作者倒装、中文作者不动",
      got == ["Ayer, A. J.", "Burge, T.", "Dummett, M.", "Kaplan, D.", "Kvanvig, J.",
              "阿斯海姆（著），张建军、万林（译）"],
      f"{got}")
check("⑤ 编号清单：条目数不变（没多没少）", len(parsed) == len(RAW_LINES), f"{len(parsed)}")

# ── ⑤b 中文译著体例：`原作者（著），译者（译）` 必须被识别（第 64 条，2026-10-15）────
#
# 从前 `阿斯海姆（著），张建军、万林（译），指称与意向性，…` 会被切成
# 「作者 = 阿斯海姆（著）／题名 = 张建军、万林（译）」——**丢了真题名、多了个假题名**
# （实测 2026年第5期(90)/01 Jingru_Kou 第 16 条，`title = {张建军、万林（译）}`）。
# 根因：无引号分支按中文逗号切，把「著者 → 译者」的那个逗号当成了「作者 → 题名」。
print("\n── ⑤b 中文著/译体例：作者 = 原作者（著），译者（译）──")
_TR_LINES = [
    "[16] 阿斯海姆（著），张建军、万林（译），指称与意向性，南京：南京大学出版社，2014年。",
    "[17] 焦循（著），沈文倬（点校），孟子正义，北京：中华书局，1987年。",
    "[18] 沃斯、费布鲁赫、库伊（著），黄海婷（译），从哈克标准看知道者悖论的解决方案，"
    "逻辑学动态与评论，2025年，第3–36页。",
    # 反例①：普通中文条目（两段都没有角色标记）——一个字节都不该变
    "[19] 付敏，亚相容解悖方案研究，2021年，北京：社会科学文献出版社。",
    # 反例②：只有（著）、第二段是**真题名**——不许把题名吞进作者
    "[20] 黑格尔（著），逻辑学，北京：商务印书馆，2004年。",
    # 反例③：只有（译）标记——作者段仍是 1 段
    "[21] 梅文（译），题名，北京：某社，2001年。",
]
_tr = footref.parse_numbered_list("\n".join(_TR_LINES))
check("⑤b （著）…（译）：作者保留中文逗号、顿号（不倒装、不拆）",
      _tr[0].author == "阿斯海姆（著），张建军、万林（译）", repr(_tr[0].author))
check("⑤b （著）…（译）：题名是真题名（不再被译者段顶掉）",
      _tr[0].title == "指称与意向性", repr(_tr[0].title))
check("⑤b （著）…（点校）：同样识别", _tr[1].author == "焦循（著），沈文倬（点校）"
      and _tr[1].title == "孟子正义", f"{_tr[1].author!r} / {_tr[1].title!r}")
check("⑤b 多个原作者（顿号）+ 译者：作者段完整",
      _tr[2].author == "沃斯、费布鲁赫、库伊（著），黄海婷（译）", repr(_tr[2].author))
check("⑤b 反例①：普通中文条目一字不变（付敏）",
      _tr[3].author == "付敏" and _tr[3].title == "亚相容解悖方案研究",
      f"{_tr[3].author!r} / {_tr[3].title!r}")
check("⑤b 反例②：只有（著）时题名不被吞（黑格尔）",
      _tr[4].author == "黑格尔（著）" and _tr[4].title == "逻辑学",
      f"{_tr[4].author!r} / {_tr[4].title!r}")
check("⑤b 反例③：只有（译）时作者段仍是一段（梅文）",
      _tr[5].author == "梅文（译）" and _tr[5].title == "题名",
      f"{_tr[5].author!r} / {_tr[5].title!r}")
check("⑤b 判据函数：只在「首段（著）+ 次段（译/点校）」同时成立时才认两段",
      footref._translation_author_segments(["阿斯海姆（著）", "张建军、万林（译）", "指称与意向性"]) == 2
      and footref._translation_author_segments(["黑格尔（著）", "逻辑学", "北京：商务印书馆"]) == 1
      and footref._translation_author_segments(["付敏", "亚相容解悖方案研究"]) == 1
      and footref._translation_author_segments(["梅文（译）", "题名"]) == 1)

# ⑤c 编号清单入口的「题名短于 4 字就丢掉」例外：**完整的**著/译形态下题名可能是 3 字书名
#     （实测 `亚里士多德（著），张竹明（译），物理学，…`；丢掉整条会让正文 \parencite 悬空）。
#     反例：只有（著）没有（译）时**不**放行——那时短「题名」多半是把译者/刊名切错了
#     （实测 06 Mengyun_Wu：`亚里士多德（著）` + `颜一`，放行会多出一条垃圾条目）。
from slcn.bibcheck import author_has_role_mark  # noqa: E402
from slcn.docxsource import _numbered_refs  # noqa: E402

check("⑤c 完整著/译形态 → 放行 3 字题名",
      [t for _k, _e, t in _numbered_refs(
          ["[1] 亚里士多德（著），张竹明（译），物理学，北京：商务印书馆，2016年。",
           "[2] 陈波，逻辑学导论，北京：中国人民大学出版社，2003年。"])[0]]
      == ["物理学", "逻辑学导论"])
check("⑤c 只有（著）→ 短题名一律丢掉（不放行垃圾条目），正常条目照旧",
      [t for _k, _e, t in _numbered_refs(
          ["[1] 亚里士多德（著），颜一，工具论，北京：商务印书馆，2003年。",
           "[2] 陈波，逻辑学导论，北京：中国人民大学出版社，2003年。"])[0]] == ["逻辑学导论"])
check("⑤c 判据 both=True：要求（著）与（译）同时出现",
      author_has_role_mark("亚里士多德（著），张竹明（译）", both=True)
      and not author_has_role_mark("亚里士多德（著）", both=True)
      and author_has_role_mark("亚里士多德（著）")
      and author_has_role_mark("赵汀阳（编）", both=True) is False)


# 文末人年制区（`doc.refs` 那条通道）也要接上
from slcn.docxsource import RefEntry, refs_to_bib  # noqa: E402

_e = RefEntry(raw="", author="T. Burge", year="1984", title="Epistemic Paradox", kind="J", rest="")
_e.key = "Burge1984"
_c = RefEntry(raw="", author="党学哲", year="2024", title="证明论语义", kind="J", rest="")
_c.key = "Dang2024"
_out = refs_to_bib([_e, _c])
check("⑤ refs_to_bib（文末人年制区）也倒装，中文不动",
      "author = {Burge, T.}" in _out and "author = {党学哲}" in _out, _out.splitlines()[3])

# 任务书：把未倒装**逐条点给 AI**（2026-10-15 的教训就是规范里没出声，AI 就照抄源稿）
from slcn import refmark  # noqa: E402

_md = refmark.build("@article{Burge1984,\n  author = {T. Burge},\n  title = {x},\n  year = {1984}\n}\n", "Demo")
check("⑤ 任务书头部点出未倒装条数", "作者体例" in _md and "未倒装" in _md)
check("⑤ 任务书逐条清单里给出改法", "T. Burge → 应为 Burge, T." in _md)
check("⑤ 任务书第三节的示例 bib 本身是倒装的",
      "{Dillard, J. P. and Segrin, C. and Harden, J. M.}" in refmark.FORMAT_MD)
check("⑤ 任务书第一节不再写「两种写法都对」", "两种写法都对" not in refmark.SPEC_MD)

# ── ⑥ 离线检查器：英文未倒装 = must ───────────────────────────────────────
print("\n── ⑥ bibcheck：英文未倒装是问题 ──")
BIB_BAD = """\
@article{Burge1984,
  author  = {T. Burge},
  title   = {Epistemic Paradox},
  journal = {The Journal of Philosophy},
  volume  = {81},
  number  = {1},
  pages   = {5--29},
  year    = {1984}
}
"""
BIB_OK = BIB_BAD.replace("{T. Burge}", "{Burge, T.}")
iss = bibcheck.author_form_issues(BIB_BAD)
check("⑥ 未倒装 → 1 条 must，且带上改法",
      len(iss) == 1 and iss[0].severity == "must" and "Burge, T." in iss[0].detail,
      f"{[(i.severity, i.detail) for i in iss]}")
check("⑥ 已倒装 → 0 条", bibcheck.author_form_issues(BIB_OK) == [])
check("⑥ 中文条目、`JC Beall` 不报",
      bibcheck.author_form_issues("@book{Chen2022,\n  author = {陈波},\n  title = {x}\n}\n") == []
      and bibcheck.author_form_issues("@book{B,\n  author = {JC Beall},\n  title = {x}\n}\n") == [])
check("⑥ editor 字段同样查",
      [i.detail for i in bibcheck.author_form_issues(
          "@book{S,\n  editor = {J. Salerno},\n  title = {x},\n  year = {2009}\n}\n")][:1]
      and "editor" in bibcheck.author_form_issues(
          "@book{S,\n  editor = {J. Salerno},\n  title = {x},\n  year = {2009}\n}\n")[0].detail)
# ⚠️ 冻结基线：`check()` 的口径不许被这条规则改动（docx_audit.csv 是冻结的）
check("⑥ bibcheck.check() **不含**这条（保护 docx_audit 冻结基线）",
      bibcheck.check(BIB_BAD).n_must == 0,
      f"n_must={bibcheck.check(BIB_BAD).n_must}")
check("⑥ author_is_inverted 与规范化器同源", INV("T. Burge") is False and INV("Burge, T.") is True)

# ── ⑦ 中文译著：只提示、判据收窄 ──────────────────────────────────────────
print("\n── ⑦ 中文译著缺「（著）/（译）」：提示 ──")
TR_BAD = "@book{A2014,\n  author = {阿斯海姆（著）},\n  title = {指称与意向性},\n  publisher = {南京大学出版社},\n  address = {南京},\n  year = {2014},\n  userf = {cn}\n}\n"
TR_OK = "@book{H2004,\n  author = {黑格尔（著），杨一之（译）},\n  title = {逻辑学},\n  publisher = {商务印书馆},\n  address = {北京},\n  year = {2004},\n  userf = {cn}\n}\n"
TR_PLAIN = "@book{Chen2022,\n  author = {陈波},\n  title = {逻辑例外论},\n  publisher = {北京大学出版社},\n  address = {北京},\n  year = {2022},\n  userf = {cn}\n}\n"
TR_ED = "@book{G2001,\n  author = {国家文物局古文献研究室（编）},\n  title = {吐鲁番出土文书},\n  publisher = {文物出版社},\n  address = {北京},\n  year = {2001},\n  userf = {cn}\n}\n"
check("⑦ 有（著）缺（译）→ 1 条提示（nice，不挡退出码）",
      [i.severity for i in bibcheck.translation_marking_issues(TR_BAD)] == ["nice"])
check("⑦ 著/译齐全 → 不报", bibcheck.translation_marking_issues(TR_OK) == [])
check("⑦ 普通中文专著（无标记）→ 不报", bibcheck.translation_marking_issues(TR_PLAIN) == [])
check("⑦ `（编）` 不算译著 → 不报", bibcheck.translation_marking_issues(TR_ED) == [])
check("⑦ 只有（译）缺（著）→ 也报",
      len(bibcheck.translation_marking_issues(
          "@book{X,\n  author = {黄海婷（译）},\n  title = {x},\n  year = {2000}\n}\n")) == 1)

# ── ⑧ checkbib.py 命令行 ──────────────────────────────────────────────────
print("\n── ⑧ checkbib.py 命令行 ──")


def run_cli(*args):
    p = subprocess.run([sys.executable, os.path.join(_ENGINE, "checkbib.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=_ENGINE)
    return p.returncode, p.stdout + p.stderr


with tempfile.TemporaryDirectory() as tmp:
    p_bad = os.path.join(tmp, "bad.bib")
    open(p_bad, "w", encoding="utf-8").write(BIB_BAD)
    code, out = run_cli("--returned", p_bad)
    check("⑧ 未倒装 → 退出码 1 且报告里点名", code == 1 and "未按编辑部体例倒装" in out,
          f"code={code}")
    p_ok = os.path.join(tmp, "ok.bib")
    open(p_ok, "w", encoding="utf-8").write(BIB_OK)
    code, out = run_cli("--returned", p_ok)
    check("⑧ 已倒装 → 退出码 0", code == 0, f"code={code} out={out[:120]!r}")
    p_tr = os.path.join(tmp, "tr.bib")
    open(p_tr, "w", encoding="utf-8").write(TR_BAD)
    code, out = run_cli("--returned", p_tr)
    check("⑧ 译著缺标记 → 退出码 0（只提示）且报告里有该提示",
          code == 0 and "疑似中文译著" in out, f"code={code}")

# ── 附：真实成品全库对照（缺文件就 SKIP，不写死内容）──────────────────────
# ⚠️ 相对本文件推仓库根（`tests/` → `Engine/` → `` → 仓库根）：写死某台机器的
#    绝对路径会让这条对照在别人机器上永远 SKIP（等于没测）。2026-10-15 拆仓预演时一并修。
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FINAL_BIB = str(_REPO_ROOT / "2026年第5期(90)" / "01 Jingru_Kou" / "reference.bib")
print("\n── 附：编辑成品 20/20 全倒装（真实文件，只读）──")
if os.path.isfile(FINAL_BIB):
    txt = open(FINAL_BIB, encoding="utf-8", errors="replace").read()
    au = re.findall(r"\b(?:author|editor)\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", txt)
    en = [a.strip() for a in au if re.search(r"[A-Za-z]", a) and not re.search(r"[\u4e00-\u9fff]", a)]
    bad2 = [a for a in en if not INV(a)]
    check(f"附 编辑成品英文 author/editor {len(en)} 条全部已倒装", not bad2, f"{bad2}")
    check("附 中文条目未被误判（含「（著），…（译）」串）",
          all(INV(a.strip()) for a in au if re.search(r"[\u4e00-\u9fff]", a)))
else:
    SKIPS.append("真实成品 reference.bib 不存在")

# ══ ⑥ 英文作者名规范：SURNAME Given → Given Surname（期90/05 Beibei_Gao）════
from slcn.naming import normalize_english_author  # noqa: E402
check("⑥ GAO Bei-bei（有中文名 高贝贝）→ Beibei Gao",
      normalize_english_author("GAO Bei-bei", "高贝贝") == "Beibei Gao",
      normalize_english_author("GAO Bei-bei", "高贝贝"))
check("⑥ 无中文名时交换顺序、保留常规连字符",
      normalize_english_author("SMITH John", "") == "John Smith",
      normalize_english_author("SMITH John", ""))
check("⑥ 已是 Given Surname 的写法原样返回",
      normalize_english_author("Beibei Gao", "高贝贝") == "Beibei Gao",
      normalize_english_author("Beibei Gao", "高贝贝"))

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
