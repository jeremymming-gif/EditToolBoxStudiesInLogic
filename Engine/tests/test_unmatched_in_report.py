#!/usr/bin/env python3.14
r"""「正文里没转成 cite 的引用点」进留档报告（第 80 条）的判据测试。

缺陷（真稿 `2026年第5期(90)/10 Bo_Chen` 实测）：正文写 `（Beal  Restall 2006）`
（源稿把 `Beall` 少写了一个 l），`link_citations()` 的「姓+年」精确匹配落空 → 那处引用
**原样留在正文里**；bib 里的 `Beall2006` 于是被 `bibcheck.check_citations()` 的 ②
报成「冗余条目」——**误诊**：条目不多余，是那处引用没转。而这条「没转」从前只
`print()` 到 stdout（GUI 日志还有 400k 上限会被挤掉），**一个字都不落盘**，编辑照报告
去删条目就删错了。

本测试守住五件事：
  (a) `bibcheck.check_citations(tex, bib)` **旧调用逐字不变**（`unmatched` 缺省 None）；
  (b) ② 的原文案**保留**，追加的提示只在**四条可核证据全齐**时出现
      （拿到 unmatched 清单 / 引用原文逐字在正文里 / 年份 token 逐字相同 / 姓名共有前缀够长）；
  (c) 报告里新增的「正文里没转成引用的引用点」小节：逐条给引用原文 + 段落序号 + 段首，
      **空清单一个字都不出现**（不许有空小节 / 「0 处」噪声行）；
  (d) 报告门槛：字段有问题照旧出报告（第 61 条那条门槛没被改坏）；**只增加了**
      「没转成 cite 的引用」这一个出报告的理由；
  (e) 只报不改：写报告前后 `.tex` / `.bib` 逐字节不变；`checkbib.py` 的三种模式与
      退出码（0 / 1 / 2）语义不变。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_unmatched_in_report.py
"""
import os
import re
import subprocess
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

from slcn import bibcheck, bibreport, build  # noqa: E402
from slcn.docxsource import link_citations, parse_docx  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 语料（逐字抄自真稿的形态，见 `slcn/build.py` / `slcn/bibcheck.py` 的第 80 条注释）──
RAW_BEAL = "（Beal  Restall 2006）"          # 源稿把 `Beall` 写成了 `Beal`（两个空格）
RAW_HAACK = "（Haack 1996）"
RAW_WILL = "（Williamson 2017；修订扩充版，2021）"
BIB_BEALL = """\
@book{Beall2006,
  author    = {Beall and J C and Restall and G},
  title     = {Logical Pluralism},
  address   = {Oxford},
  publisher = {Oxford University Press},
  year      = {2006}
}
"""
TEX_BEALL = "……哈克从非形式论证和形式论证的角度解读逻辑多元论……" + RAW_BEAL + "。"
OLD_LINE = "② 冗余条目：{Beall2006} 在参考文献里，但正文从未引用"
SECTION_HEAD = "  ── 正文里没转成引用的引用点（"

# 含全部四种引用点的正文：给 (b) 的四个门槛用（每条都真实出现，便于逐个开合证据）
TEX_ALL = "\n".join(["第一段。" + RAW_BEAL, "第二段。（Smith 2006）。", "第三段。（陈波 2005）。",
                     "第四段。" + RAW_WILL])
BIB_CHEN = """\
@article{ChenBo2005,
  author  = {陈波},
  title   = {一个与归纳问题类似的演绎问题},
  journal = {哲学研究},
  volume  = {1},
  number  = {1},
  pages   = {1--10},
  year    = {2005}
}
"""
BIB_WILL = """\
@book{Williamson2007,
  author    = {Williamson, T},
  title     = {The Philosophy of Philosophy},
  address   = {Oxford},
  publisher = {Blackwell Publishing},
  year      = {2007}
}
"""

# ══ (a) 旧调用逐字不变（`unmatched` 缺省 = None）════════════════════════════
print("── (a) `check_citations(tex, bib)` 旧调用逐字不变 ──")

_old = bibcheck.check_citations(TEX_BEALL, BIB_BEALL)
check("(a) 两个位置参数的旧调用只报 ②，且文案逐字是历史那一句",
      _old == [OLD_LINE], repr(_old))
check("(a) `unmatched=None` 与不传参逐字节相同",
      bibcheck.check_citations(TEX_BEALL, BIB_BEALL, unmatched=None) == _old)
check("(a) `unmatched=[]` 与不传参逐字节相同（空清单不是「有清单」）",
      bibcheck.check_citations(TEX_BEALL, BIB_BEALL, unmatched=[]) == _old)
check("(a) 旧的 `window` 关键字参数仍在（第三个位置参数语义没变）",
      bibcheck.check_citations(TEX_BEALL, BIB_BEALL, 80) == _old)

TEX_DANGLE = "正文引了 \\parencite{Ghost2020}，但 bib 里没有。"
check("(a) ① 悬空引用不受新参数影响",
      bibcheck.check_citations(TEX_DANGLE, "") ==
      ["① 悬空引用：正文引用了 {Ghost2020}，但参考文献里没有这一条"])
check("(a) ① + ② 一起时顺序与条数都与从前一致",
      len(bibcheck.check_citations(TEX_BEALL + TEX_DANGLE, BIB_BEALL)) == 2)

TEX_ATTR = "陈波提出社会历史因果描述论。"
_cites_attr = bibcheck.check_citations(TEX_ATTR, BIB_CHEN)
_cite3 = [c for c in _cites_attr if c.startswith("③")]
# ⚠️ 逐字复刻既有输出：那一句在 `bibcheck.py` 里是**原始 f-string**，所以两个反斜杠
#    会原样留着（`\\parencite`）。这是第 80 条之前的既有形态，本次一个字都没动。
check("(a) ③ 疑似漏引的文案逐字不变",
      _cite3 == ["③ 疑似漏引：正文写「陈波提出」，附近没有引用命令，"
                 "请核对是否需要补 \\\\parencite{ChenBo2005}"], repr(_cite3))
check("(a) ③ 不受新参数影响：给了 unmatched 也照样报",
      bibcheck.check_citations(TEX_ATTR, BIB_CHEN, unmatched=[RAW_BEAL]) == _cites_attr)

# ══ (b) ② 原文案保留；提示只在四条证据全齐时出现 ═════════════════════════════
print("\n── (b) ② 文案保留 + 提示只在可核证据全齐时追加 ──")

_hinted = bibcheck.check_citations(TEX_BEALL, BIB_BEALL, unmatched=[RAW_BEAL])
check("(b) 有证据 → 条数不变（还是 1 条）", len(_hinted) == 1, repr(_hinted))
check("(b) ② 的原文案**逐字保留**，提示只是追加在它后面",
      _hinted[0].startswith(OLD_LINE), repr(_hinted[0][:40]))
check("(b) 提示里逐字带上那处引用原文", RAW_BEAL in _hinted[0])
check("(b) 提示写明证据：年份 2006 一致 + 姓名「Beal」与作者姓氏「Beall」同前 4 个字符",
      "年份 2006" in _hinted[0] and "「Beal」" in _hinted[0] and "「Beall」" in _hinted[0]
      and "同前 4 个字符" in _hinted[0], repr(_hinted[0]))
check("(b) 提示明说「不是条目多余」（不许误诊）", "不是条目多余" in _hinted[0])

# 门槛①：引用原文必须**逐字**出现在正文里（找不到片段就不许提示）
_notintex = bibcheck.check_citations("正文里没有那处引用。", BIB_BEALL, unmatched=[RAW_BEAL])
check("(b) 反例：引用原文不在正文里 → 不提示（可在原文里核，是不许猜的底线）",
      _notintex == [OLD_LINE], repr(_notintex))
# 门槛②：年份 token 必须逐字相同
_will = bibcheck.check_citations(TEX_ALL, BIB_WILL, unmatched=[RAW_WILL])
check("(b) 反例：`Williamson 2017` vs 条目 year=2007 → 不提示",
      _will == ["② 冗余条目：{Williamson2007} 在参考文献里，但正文从未引用"], repr(_will))
# 门槛③：姓名必须有够长的共有前缀
_smith = bibcheck.check_citations(TEX_ALL, BIB_BEALL, unmatched=["（Smith 2006）"])
check("(b) 反例：`Smith 2006` 与本条作者 Beall 毫无共同前缀 → 不提示",
      _smith == [OLD_LINE], repr(_smith))
# 中文姓名：CJK 门槛是 2 个汉字
_chen = bibcheck.check_citations(TEX_ALL, BIB_CHEN, unmatched=["（陈波 2005）"])
check("(b) CJK：`（陈波 2005）` 与本条作者「陈波」命中（CJK 门槛 2 字）",
      len(_chen) == 1 and "⚠" in _chen[0] and "「陈波」" in _chen[0], repr(_chen))

check("(b) 前缀判据的边界：ASCII 要 ≥ 4 个字符（3 个不算证据）",
      bibcheck._prefix_evidence("Bea", "Beall") == 0
      and bibcheck._prefix_evidence("Beal", "Beall") == 4)
check("(b) 前缀判据的边界：CJK 要 ≥ 2 个汉字",
      bibcheck._prefix_evidence("陈", "陈波") == 0 and bibcheck._prefix_evidence("陈波", "陈波") == 2)
check("(b) token 切分留着数字 token（年份判据要靠它）",
      bibcheck._ref_tokens(RAW_BEAL) == ["Beal", "Restall", "2006"],
      repr(bibcheck._ref_tokens(RAW_BEAL)))
check("(b) `unmatched_hint()` 对查不到的键返回空串（不抛异常、不编）",
      bibcheck.unmatched_hint("NoSuchKey", "", [RAW_BEAL], TEX_BEALL) == "")

# ══ (c) 定位到源稿段落：`_citation_points()` ═════════════════════════════════
print("\n── (c) 未匹配引用 → 段落序号 + 段首片段 ──")

SRC = ("逻辑哲学的第一段。\n"
       "第二段提到" + RAW_BEAL + "与" + RAW_HAACK + "两处。\n"
       "第三段。\n"
       "第四段又见" + RAW_BEAL + "。")
_pts = build._citation_points(SRC, [RAW_BEAL, RAW_HAACK, RAW_BEAL])
check("(c) 三处未匹配各给一个点", len(_pts) == 3, repr(_pts))
check("(c) 段落序号按正文行计（第 2 / 2 / 4 段）",
      [p[1] for p in _pts] == [2, 2, 4], repr([p[1] for p in _pts]))
check("(c) 引用原文逐字保留", [p[0] for p in _pts] == [RAW_BEAL, RAW_HAACK, RAW_BEAL])
check("(c) 段首片段 = 该段开头（不是引用点前后文）",
      _pts[0][2].startswith("第二段提到") and "六" not in _pts[0][2], repr(_pts[0][2]))
check("(c) 同一处引用出现两次时按出现顺序各定位一次（第二次在第 4 段）",
      _pts[2][2].startswith("第四段又见"))
check("(c) 找不到片段时如实留空（段落 0 / 片段空串），不许编",
      build._citation_points("这里没有那个引用。", [RAW_BEAL]) == [(RAW_BEAL, 0, "")])
check("(c) 空清单 → 空结果（没有噪声行可写）", build._citation_points(SRC, []) == [])

# ══ (d) `_write_bib_report()`：新小节 + 出报告门槛 ═══════════════════════════
print("\n── (d) 报告：新小节逐条列出、空清单零噪声、门槛不变 ──")


def frozen_pre_80_report(bib_text, tex_text, title):
    r"""第 80 条**之前** `_write_bib_report()` 写出的报告正文（逐字复刻改动前的 445–458 行）。

    它在当时的分支结构是：
        body = format_report(...)
        if 有 tex: cites = check_citations(...); if cites: 追加「引用核对」小节
        写 body + "\n"
    本函数就是那段布局的独立复刻，用来证明「没有未匹配引用时逐字节不变」。
    """
    rep = bibcheck.check(bib_text)
    body = bibcheck.format_report(rep, title)
    if tex_text is not None:
        cites = bibcheck.check_citations(tex_text, bib_text)
        if cites:
            body += "\n\n  ── 引用核对（" + str(len(cites)) + " 条）──\n"
            body += "\n".join("  " + c for c in cites)
    return body + "\n"


def write_report(tmp, stem, bib_text, tex_text, points=None):
    bibp = os.path.join(tmp, f"{stem}.bib")
    with open(bibp, "w", encoding="utf-8", newline="\n") as f:
        f.write(bib_text)
    if tex_text is not None:
        with open(os.path.join(tmp, f"{stem}.tex"), "w", encoding="utf-8", newline="\n") as f:
            f.write(tex_text)
    say, warns = [], []
    build._write_bib_report(bibp, tmp, stem, say.append, warns, unmatched=points)
    rp = os.path.join(tmp, f"{stem}_参考文献核对.txt")
    txt = None
    if os.path.exists(rp):
        txt = open(rp, encoding="utf-8").read()
    return txt, warns, say


with tempfile.TemporaryDirectory() as tmp:
    # ① 有未匹配引用 → 新小节逐条列出
    txt, warns, say = write_report(tmp, "WithPts", BIB_BEALL, TEX_BEALL,
                                   points=[(RAW_BEAL, 7, "第七段的开头若干字")])
    check("(d) 有未匹配引用 → 报告写了", txt is not None)
    check("(d) 新小节标题在报告里，且带处数", txt is not None and SECTION_HEAD + "1 处）──" in txt)
    check("(d) 逐条列出引用原文（原文逐字）", txt is not None and f"· 「{RAW_BEAL}」" in txt)
    check("(d) 给位置线索：段落序号 + 段首片段",
          txt is not None and "源稿正文第 7 段" in txt and "段首「第七段的开头若干字…」" in txt)
    check("(d) 说明了「段落序号不计标题/摘要区」，编辑据此定位",
          txt is not None and "标题行与摘要/关键词区不计入" in txt)
    check("(d) ② 的可核提示也在同一份报告里（能区分「条目多余」与「引用没转」）",
          txt is not None and OLD_LINE in txt and "不是条目多余" in txt)
    check("(d) 新增一条指向报告的 warning（stdout/GUI 也能找到留档）",
          any("未匹配引用的位置已写入" in w for w in warns), repr(warns))

    # ② 无证据的未匹配引用照样**逐条列进新小节**（要求 1），只是 ② 不加提示
    txt2, _w2, _s2 = write_report(tmp, "NoEvid", BIB_WILL, TEX_ALL,
                                  points=[(RAW_WILL, 4, "第四段")])
    check("(d) 年份对不上的引用点：不进 ② 的提示，但**仍进新小节**（要求 1 逐条列出）",
          txt2 is not None and f"· 「{RAW_WILL}」" in txt2
          and "② 冗余条目：{Williamson2007} 在参考文献里，但正文从未引用\n" in txt2
          and "⚠" not in txt2.split(SECTION_HEAD)[0], repr(txt2[-200:]))

    # ③ `unmatched=None` / `[]` → 与改动前**逐字节相同**（新小节一个字都不出现）
    for tag, pts in (("None", None), ("Empty", [])):
        txt3, _w3, _s3 = write_report(tmp, f"Old{tag}", BIB_BEALL, TEX_BEALL, points=pts)
        check(f"(d) unmatched={tag} → 与改动前报告逐字节相同",
              txt3 == frozen_pre_80_report(BIB_BEALL, TEX_BEALL, f"Old{tag} 参考文献核对"),
              f"len {len(txt3)} vs {len(frozen_pre_80_report(BIB_BEALL, TEX_BEALL, 'x 参考文献核对'))}")
        check(f"(d) unmatched={tag} → 新小节标题一个字都不出现", SECTION_HEAD not in txt3)
        check(f"(d) unmatched={tag} → 没有「0 处」这类噪声行", "0 处）" not in txt3)

    # ④ 门槛：字段有问题（无未匹配）→ 照旧出报告；第 61 条那条靠 bib_path / 原文行，
    #    这里守的是「有字段问题就必须出报告」这一半
    bib_issue = BIB_BEALL.replace("  year      = {2006}\n", "")     # 缺 year → must
    txt4, _w4, _s4 = write_report(tmp, "Issue", bib_issue, TEX_BEALL, points=None)
    check("(d) 有必补字段 + 无未匹配 → 照旧出报告（门槛没被改坏）",
          txt4 is not None and "必补项  ：1" in txt4, repr(txt4 and txt4[:60]))
    check("(d) 有必补字段 + 无未匹配 → 仍无新小节", SECTION_HEAD not in txt4)

    # ⑤ 门槛的另一半（本次修的）：bib 字段**零问题**时，从前连报告都不出；
    #    有未匹配引用就必须出——否则这条缺陷对「干净 bib」的稿子完全无解
    CLEAN_BIB = """\
@book{She2019,
  author    = {Shastri, D. N.},
  title     = {The Philosophy of Nyaya-Vaisesika},
  publisher = {Motilal Banarsidass},
  address   = {Delhi},
  edition   = {2},
  year      = {1957}
}
"""
    assert bibcheck.check(CLEAN_BIB).issues == [], "fixture 该是零问题"
    txt5, _w5, _s5 = write_report(tmp, "CleanNoPts", CLEAN_BIB, TEX_BEALL, points=None)
    check("(d) bib 零问题 + 无未匹配 → **不出**报告（与改动前一致）",
          txt5 is None, repr(txt5 and txt5[:40]))
    check("(d) 但打印了「未发现问题 ✅」的既有那一行",
          any("未发现问题" in s for s in _s5), repr(_s5))
    txt6, _w6, _s6 = write_report(tmp, "CleanPts", CLEAN_BIB, TEX_BEALL,
                                  points=[(RAW_BEAL, 1, "第一段")])
    check("(d) bib 零问题 + **有**未匹配 → 出报告（第 80 条修的就是这条缺口）",
          txt6 is not None and SECTION_HEAD in txt6 and "✅ 未发现问题" in txt6)

    # ⑥ 定位失败时如实说明，不编
    txt7, _w7, _s7 = write_report(tmp, "NoLoc", BIB_BEALL, TEX_BEALL,
                                  points=[(RAW_BEAL, 0, "")])
    check("(d) 定位失败 → 写明「未能在源稿正文里定位」并提示全文检索（不编位置）",
          txt7 is not None and "未能在源稿正文里定位到该片段" in txt7 and "全文检索" in txt7)
    check("(d) 定位失败的条目**没有**假的「第 0 段」", txt7 is not None and "第 0 段" not in txt7)

    # ⑦ 只报不改：写报告前后 .tex / .bib 逐字节不变
    bibp = os.path.join(tmp, "Pure.bib")
    texp = os.path.join(tmp, "Pure.tex")
    _t8, _w8, _s8 = write_report(tmp, "Pure", BIB_BEALL, TEX_BEALL,
                                 points=[(RAW_BEAL, 1, "x")])
    check("(d) 只报不改：.tex 逐字节不变",
          open(texp, encoding="utf-8").read() == TEX_BEALL)
    check("(d) 只报不改：.bib 逐字节不变",
          open(bibp, encoding="utf-8").read() == BIB_BEALL)

# ══ (e) 门槛/出口没被改坏 + `checkbib.py` 三模式退出码 ══════════════════════
print("\n── (e) LaTeX 通道不受影响 + checkbib.py 三模式退出码 ──")

check("(e) `build_latex()` 里没有引用核对报告（LaTeX 通道产物与从前一致）",
      "_write_bib_report" not in __import__("inspect").getsource(build.build_latex))
check("(e) `checkbib.py` 用的 `bibreport.check_citations` 是**另一个**函数（本改动碰不到它）",
      bibreport.check_citations is not bibcheck.check_citations)
# 第 61 条修好的两处门槛/名单：本改动一个字都没动（convert.py 未修改）
_CONVERT_SRC = open(os.path.join(_ENGINE, "convert.py"), encoding="utf-8").read()
check("(e) 第 61 条的任务书门槛原样在（有文献区但 0 条目也要出任务书）",
      "if res.bib_path or raw_ref_lines:" in _CONVERT_SRC)
check("(e) 报告仍算「本工具产物」（重跑不会被自己的报告拦住）",
      '"_参考文献核对.txt"' in _CONVERT_SRC)

CHECKBIB = os.path.join(_ENGINE, "checkbib.py")
MIN_BIB = """\
@book{She2019,
  author    = {Shastri, D. N.},
  title     = {The Philosophy of Nyaya-Vaisesika},
  publisher = {Motilal Banarsidass},
  address   = {Delhi},
  year      = {1957}
}
"""


def run_checkbib(args, stdin_text=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(_ENGINE, "vendor")
    p = subprocess.run([sys.executable, CHECKBIB] + args,
                       input=stdin_text, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=_ENGINE, timeout=180)
    return p.returncode, p.stdout + p.stderr


with tempfile.TemporaryDirectory() as tmp:
    good = os.path.join(tmp, "good.bib")
    with open(good, "w", encoding="utf-8", newline="\n") as f:
        f.write(MIN_BIB)
    bad = os.path.join(tmp, "bad.bib")
    with open(bad, "w", encoding="utf-8", newline="\n") as f:
        f.write(MIN_BIB + "\n" + MIN_BIB)          # 重复键 → 至少一处问题
    code0, _out0 = run_checkbib(["--returned", good])
    code1, _out1 = run_checkbib(["--returned", bad])
    code2, _out2 = run_checkbib(["--returned-stdin"], stdin_text="")
    check("(e) 体检模式：无问题 → 退出码 0", code0 == 0, f"code={code0}")
    check("(e) 体检模式：有问题 → 退出码 1", code1 == 1, f"code={code1}")
    check("(e) 用法错误（空 stdin）→ 退出码 2", code2 == 2, f"code={code2}")

# ══ (f) 端到端：合成最小 docx 走完 `build_docx` ══════════════════════════════
print("\n── (f) 端到端（合成 docx）：未匹配引用进报告、② 加提示、tex 不动 ──")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TMP = []


def make_docx(paras):
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
    TMP.append(path)
    return path


# ⚠️ 同一年要有**两条**参考文献：`link_citations()` 的「年份唯一兜底」只在
#    `len(by_year[year]) == 1` 时才可能触发；两条同年 → 兜底不成立 → 引用原样留下
#    （真稿 Bo_Chen 的 2006 年就是 Beall2006 + Hanna2006 两条）。
REF_BEALL = "Beall, J C, Restall, G. 2006. Logical Pluralism[M]. Oxford: Oxford University Press."
REF_HANNA = "Hanna, R. 2006. Rationality and Logic[M]. Cambridge, MA: The MIT Press."
BODY_P = [f"正文第{i}段，讨论逻辑多元论与逻辑哲学之间的关系。" for i in range(11)]
CITE_P = "逻辑多元论也存在着不同的版本和观点，例如毕尔和雷斯托尔" + RAW_BEAL + "。"
SRC_DOCX = make_docx(["当代逻辑哲学鸟瞰", "陈波", "摘要：本文讨论逻辑哲学。", "关键词：逻辑；哲学"]
                     + BODY_P + [CITE_P] + ["参考文献", REF_BEALL, REF_HANNA])

with tempfile.TemporaryDirectory() as tmp:
    d = parse_docx(SRC_DOCX)
    _b, _un, _logs = link_citations(d.body, d.refs)
    check("(f) 合成稿：那处引用确实进了 unmatched（两条同年 → 不兜底）",
          _un == [RAW_BEAL], repr(_un))
    res = build.build_docx(SRC_DOCX, tmp, basename="Syn", verbose=False)
    rp = os.path.join(tmp, "Syn_参考文献核对.txt")
    check("(f) 报告落盘", os.path.exists(rp))
    txt = open(rp, encoding="utf-8").read()
    check("(f) 报告里有新小节且处数正确", SECTION_HEAD + "1 处）──" in txt)
    check("(f) 逐条列出引用原文", f"· 「{RAW_BEAL}」" in txt)
    check("(f) 段落序号 = 那一段在正文里的行号（含段首可核）",
          f"源稿正文第 {d.body.split(chr(10)).index(CITE_P) + 1} 段" in txt
          and "段首「逻辑多元论也存在着不同的版本和观点" in txt, repr(txt[-260:]))
    check("(f) ② 的提示只给 Beall2006（同年但姓名对不上的 Hanna2006 不给）",
          "② 冗余条目：{Beall2006} 在参考文献里，但正文从未引用  ⚠" in txt
          and "② 冗余条目：{Hanna2006} 在参考文献里，但正文从未引用\n" in txt)
    check("(f) stdout 侧的既有 warning 仍在（3 个字面量到 unmatched 的告知）",
          any("处引用未能匹配到参考文献" in w for w in res.warnings), repr(res.warnings[:3]))
    check("(f) 新增 warning 指向报告文件",
          any("Syn_参考文献核对.txt" in w and "未匹配引用的位置已写入" in w
              for w in res.warnings), repr(res.warnings))
    check("(f) 只报不改：产物 .tex 里那处引用**原样**留着（没被硬配成别的条目）",
          RAW_BEAL in open(res.tex_path, encoding="utf-8").read())
    check("(f) 只报不改：没有凭空出现 \\parencite{Beall2006}",
          "\\parencite{Beall2006}" not in open(res.tex_path, encoding="utf-8").read())

    # 第 61 条的当事人形态：**有文献区、但 0 条解析得出来**（`res.bib_path is None`）。
    # 任务书的门槛是 `res.bib_path or raw_ref_lines`（convert.py），靠的就是这份原文兜底——
    # 本次改动一个字都没碰它，这条断言把它钉住（报告不出是正常的：没有 bib 可核对）。
    # ⚠️ 夹具必须用**当前解析器仍解析不出来**的行，否则这句话就没在测"0 条"这个形态：
    #    2026-10-17 校准后英文逗号风格能解析了（`A. Capone, 2019, …` 不再是反例），
    #    而"年份在中间的中文逗号风格"仍是已知限制（见 AUDIT），用它继续钉住第 61 条的原文兜底。
    # ⚠️ 必须用**当前解析器仍解析不出来**的行（2026-10-17 二次校准后"年份在第 2 段"、
    #    "年份在其余部分"两种中文逗号风格都能解析了）：用**没有年份**的行保住"0 条"这个形态。
    raw_lines = ["某书稿，无年份，某出版社。",
                 "另一位作者，也没有年份，另一家出版社。"]
    src61 = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：数据库；逻辑"]
                      + BODY_P + ["参考文献"] + raw_lines)
    res61 = build.build_docx(src61, tmp, basename="RawOnly", verbose=False)
    check("(f) 第 61 条：0 条 + 有文献区原文 → `bib_path is None` 且 `ref_raw_lines` 非空"
          "（任务书门槛 `bib_path or raw_ref_lines` 仍为真）",
          res61.bib_path is None and res61.ref_raw_lines == raw_lines,
          f"bib_path={res61.bib_path} raw={res61.ref_raw_lines}")
    check("(f) 第 61 条：这类稿子本来就没有 bib → 不写核对报告（也没有新小节可写）",
          not os.path.exists(os.path.join(tmp, "RawOnly_参考文献核对.txt")))

for _t in TMP:
    try:
        os.unlink(_t)
    except OSError:
        pass

# ══ (g) 真实稿（语料不在本机则 SKIP）═══════════════════════════════════════
print("\n── (g) 真实稿 10 Bo_Chen：三处未匹配引用都进报告 ──")

REAL = os.path.join(_REPO, "2026年第5期(90)", "10 Bo_Chen", "original",
                    "陈波 当代逻辑哲学鸟瞰.docx")
if not os.path.exists(REAL):
    skip("(g) 真实稿 10 Bo_Chen", "语料不在本机")
else:
    with tempfile.TemporaryDirectory() as tmp:
        res = build.build_docx(REAL, tmp, basename="Bo_Chen", verbose=False)
        txt = open(os.path.join(tmp, "Bo_Chen_参考文献核对.txt"), encoding="utf-8").read()
        check("(g) 三处未匹配引用都逐条列出",
              all(f"· 「{r}」" in txt for r in (RAW_WILL, RAW_BEAL, RAW_HAACK)),
              repr([r for r in (RAW_WILL, RAW_BEAL, RAW_HAACK) if f"· 「{r}」" not in txt]))
        check("(g) 三处都给了「第 N 段」，位置线索与源稿对得上",
              txt.count("源稿正文第 ") == 3 and "第 45 段" in txt
              and "第 72 段" in txt and "第 102 段" in txt, repr(re.findall(r"第 \d+ 段", txt)))
        check("(g) 第 73 条修掉错配后进入 unmatched 的 Williamson 那处也在",
              "· 「（Williamson 2017；修订扩充版，2021）」" in txt)
        check("(g) Beall2006 的 ② 后面有「引用没转」的可核提示",
              "② 冗余条目：{Beall2006} 在参考文献里，但正文从未引用  ⚠" in txt
              and RAW_BEAL in txt)
        check("(g) Haack 那处**不**误配到 Haack1978（年份不同 → 不给提示）",
              "② 冗余条目：{Haack1978} 在参考文献里，但正文从未引用  ⚠" not in txt)
        check("(g) 只报不改：源稿那三处引用在产物 .tex 里逐字保留",
              all(r in open(res.tex_path, encoding="utf-8").read()
                  for r in (RAW_WILL, RAW_BEAL, RAW_HAACK)))

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
