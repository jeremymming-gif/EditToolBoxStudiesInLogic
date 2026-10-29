#!/usr/bin/env python3
"""一校核查（第 92 条）：把《排校手册》《流程化经验书》的规范变成可回归的规则。

三条纪律要一起钉住：
  (a) **该报的报** —— 每条规则配正例（含真实稿里出现过的形态）；
  (b) **不该报的不报** —— 注释 / 数学 / 宏名 / 键参数 / 自带 `\\textup` 的命令里的东西一律不算；
      中文规则不去管英文正文，英文规则不去管英文模板里打印的中文题名摘要；
  (c) **自动改只碰无歧义的** —— 脚注里的标点只报不改；半角改全角要连空格一起吃掉；
      位置移动（脚注/引文移到标点之前）要按花括号配对、绝不改坏。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import proofcheck as P  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def hits_of(tex: str, rule_id: str, lang: str = "zh"):
    return [h for h in P.check(tex, lang=lang).hits if h.rule_id == rule_id]


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 每条规则：正例与反例 ──")

# 中文半角标点（经验书 预校正·中文）
for rid, bad, good in (("ZH-01", "甲,乙", "甲，乙"), ("ZH-02", "甲.乙", "甲。乙"),
                       ("ZH-03", "甲:乙", "甲：乙"), ("ZH-04", "甲;乙", "甲；乙"),
                       ("ZH-05", "甲?乙", "甲？乙")):
    check(f"(a) {rid} 命中半角并与中文相邻", len(hits_of(f"{bad}。", rid)) == 1, bad)
    check(f"(a) {rid} 反例：已是全角 → 不报", not hits_of(f"{good}。", rid))

check("(a) ZH-01 反例：英文之间的半角逗号**不报**（那是英文正文）",
      not hits_of("a, b and c\n", "ZH-01"))
check("(a) ZH-06 顿号后紧跟引号 → 报，且建议是「删掉顿号」",
      len(hits_of("甲、“乙”\n", "ZH-06")) == 1
      and hits_of("甲、“乙”\n", "ZH-06")[0].replacement == "")
check("(a) ZH-06 反例：正常顿号（甲、乙）不报", not hits_of("甲、乙。\n", "ZH-06"))

check("(a) ZH-07 中文旁的半角括号 → 报（标为噪声大）",
      len(hits_of("他说(是这样)。\n", "ZH-07")) == 1
      and next(r for r in P.RULES if r.id == "ZH-07").noisy)
check("(a) ZH-08 中文旁的半角短杠 → 报（标为噪声大）",
      len(hits_of("2-群胚。\n", "ZH-08")) == 1
      and next(r for r in P.RULES if r.id == "ZH-08").noisy)

# ZH-08 新口径（2026-10-28 用户拍板 A，冻结 spec b004-zh08-connector §2）：
# 汉字-汉字 / 小写英文邻 / 数学内 / 命令参数 / URL 等保持 ASCII；其余 → `\edash `。
check("(a) ZH-08 新口径：汉字-汉字（马丁-洛夫/社会-历史）→ 不动、不报",
      not hits_of("马丁-洛夫类型论\n", "ZH-08")
      and not hits_of("社会-历史研究\n", "ZH-08"))
check("(a) ZH-08 新口径：小写英文邻（would-/3-a/b-数据/not-）→ 保持、不报",
      not hits_of("would-反事实条件句\n", "ZH-08")
      and not hits_of("3-a 与 b-数据\n", "ZH-08")
      and not hits_of("not-条件句\n", "ZH-08"))
for _tex, _want in (("2-群胚\n", "2\\edash 群胚\n"),
                    ("MTL- 代数\n", "MTL\\edash 代数\n"),
                    ("表2-9\n", "表2\\edash 9\n"),
                    ("2021-06-16\n", "2021\\edash 06\\edash 16\n"),
                    ("A-105\n", "A\\edash 105\n"),
                    ("S5-关系\n", "S5\\edash 关系\n")):
    _out, _ap = P.apply_fixes(_tex, P.check(_tex, lang="zh"))
    check(f"(a) ZH-08 新口径：{_tex.strip()!r} → {_want.strip()!r}", _out == _want, _out)
check("(a) ZH-08 回归：右锚定命中不吞汉字（2-群胚 → 2\\edash 群胚，不是 2\\edash 胚；"
      "MTL- 代数 → MTL\\edash 代数，不是 MTL\\edash 数）",
      P.apply_fixes("2-群胚\n", P.check("2-群胚\n", lang="zh"))[0] == "2\\edash 群胚\n"
      and P.apply_fixes("MTL- 代数\n", P.check("MTL- 代数\n", lang="zh"))[0] == "MTL\\edash 代数\n")
check("(a) ZH-08 数学：数学区内部保持（不报）；数学紧邻（$\\phi$-世界）只报不改",
      not hits_of("$a-b$\n", "ZH-08")
      and [h.fixable for h in hits_of("$\\phi$-世界\n", "ZH-08")] == [False]
      and [h.fixable for h in hits_of("或$\\infty$-群胚\n", "ZH-08")] == [False])
for _tex, _why in (("\\item[模仿-1：]\n", "可选参数"),
                   ("\\vspace{-5.9ex}\n", "\\vspace 技术参数"),
                   ("\\cline{2-4}\n", "\\cline 技术参数"),
                   ("\\receiveddate{2025-06-12}\n", "模板日期字段"),
                   ("\\url{https://x.test/a-b}\n", "URL"),
                   ("见 pp.130-133 与 pp.130--133\n", "英文页码"),
                   ("\\draw (0,0)--(12,0);\n", "TikZ --")):
    check(f"(a) ZH-08 保持不报：{_why}", not hits_of(_tex, "ZH-08"), _tex)
check("(a) ZH-08 反例：`第8--12页` 归 ZH-09（扩 -{1,2}），ZH-08 不报",
      not hits_of("第8--12页。\n", "ZH-08")
      and [h.replacement for h in hits_of("第8--12页。\n", "ZH-09")] == ["第8\\cndash 12页"])
check("(a) ZH-08 默认启用：INITIAL_LAYOUT_FIXABLE 含 ZH-08，OPT_IN_FIXABLE 已置空",
      "ZH-08" in P.INITIAL_LAYOUT_FIXABLE and P.OPT_IN_FIXABLE == ())
check("(a) 同一份稿里两种连接号不混：数字+CJK → `\\edash `，页码范围 → `\\cndash `（手册口径）",
      P.apply_fixes("2-群胚类型论见第8--12页。\n",
                    P.check("2-群胚类型论见第8--12页。\n", lang="zh"))[0]
      == "2\\edash 群胚类型论见第8\\cndash 12页。\n")

check("(a) ZH-09 第8-12页 → 报，建议用 \\cndash",
      [h.replacement for h in hits_of("第8-12页。\n", "ZH-09")] == ["第8\\cndash 12页"])
check("(a) ZH-09 反例：已是第8\\cndash 12页 → 不报", not hits_of("第8\\cndash 12页。\n", "ZH-09"))

# 脚注与引文位置（手册 §常规 4 / 5；用户 2026-10-15 点名的两条）
check("(a) ZH-10 脚注在逗号之后 → 报（手册：逗号处放逗号前）",
      len(hits_of("这个说法，\\footnote{注}是错的。\n", "ZH-10")) == 1)
check("(a) ZH-10 正例：脚注在逗号之前 → 不报",
      not hits_of("这个说法\\footnote{注}，是错的。\n", "ZH-10"))
check("(a) ZH-11 引文在逗号之后 → 报",
      len(hits_of("见文献，\\parencite{Haack1974}的讨论。\n", "ZH-11")) == 1)
check("(a) ZH-11 正例：引文在逗号之前 → 不报",
      not hits_of("见文献\\parencite{Haack1974}，以及别的。\n", "ZH-11"))
check("(a) ZH-11 反例：**英文稿**里的 `work, \\cite{x}` 不报（手册这条是中文口径）",
      not hits_of("Besides Burgess' work, \\cite{Holliday17} considered it.\n", "ZH-11", lang="en"))
# S1（B-273）ZH-11 加括号内豁免：手册 §常规 5.2 的正例是「（作者，\\parencite{…}）」括注写法。
# 实测（/tmp/b272-analysis-20261030/handbook-ideas.md §1）：成品 96 处 `，\\cite` 有 82 处在中文括号内。
check("(a) ZH-11 反例：中文括号内的括注（手册正例 `（Sarah Moss，\\parencite{k}）`）不报",
      not hits_of("（Sarah Moss，\\parencite{k}）\n", "ZH-11"))
check("(a) ZH-11 正例：括号外的正文句读照报",
      len(hits_of("文本，\\cite{k}正文。\n", "ZH-11")) == 1)
check("(a) ZH-11 嵌套括号：按最近未闭合开括号判、命中整体在括号内 → 不报",
      not hits_of("（甲（乙，\\cite{k}）丙）\n", "ZH-11"))
check("(a) ZH-11 未闭合括号不豁免（照报，不把漏括号静默吞掉）",
      len(hits_of("（甲，\\cite{k}正文。\n", "ZH-11")) == 1)
# fix round（B-273 验收 V3-2）：数学区里的括号不参与配对——`$（$` 不能被当成正文括号。
check("(a) ZH-11 数学区左括号不参与豁免：`$（$正文，\\cite{k}）` → 报",
      len(hits_of("$（$正文，\\cite{k}）\n", "ZH-11")) == 1)
check("(a) ZH-11 数学区**外**的正文括注仍按括号豁免（回归）",
      not hits_of("（作者，\\cite{k}）\n", "ZH-11"))
check("(a) ZH-11 数学区括号闭合也不把正文句读豁免",
      len(hits_of("$（$正文，\\cite{k}）$。\n", "ZH-11")) == 1)
# 句末标点后的脚注（手册 §常规 4.3；B-272 起 ZH-12 升级为「可自动改」）
# 判据（spec b272-zh12-footnote-space §4）：句末标点（。！？…，可带闭引号/闭括号）
# 后紧跟 `\footnote{…}`，右邻**紧接**汉字 → 插 `\,`；花括号配对、支持嵌套。
for _tex, _why in (
    ("完成了。\\footnote{注}这就是结果。\n", "句号"),
    ("完成了？\\footnote{注}这就是结果。\n", "问号"),
    ("完成了！\\footnote{注}这就是结果。\n", "叹号"),
    ("完成了…\\footnote{注}这就是结果。\n", "省略号"),
    ("完成了\\ldots\\footnote{注}这就是结果。\n", r"`\ldots`"),
    ("完成了。”\\footnote{注}这就是结果。\n", "闭引号"),
    ("完成了。）\\footnote{注}这就是结果。\n", "闭括号"),
    ("完成了。《书名》\\footnote{注}这就是结果。\n", "闭书名号"),
    ("完成了。\\footnote{注}“下一句。\n", "右邻中文开引号"),
    ("完成了。 \\footnote{注}这就是结果。\n", "句末与脚注间夹空格"),
    ("完成了。\\footnote{注 {内层}}这就是结果。\n", "嵌套花括号"),
    ("完成了。\\footnote{见\\parencite{Wang2018}的注}这就是结果。\n", "嵌套命令"),
):
    _hs = hits_of(_tex, "ZH-12")
    check(f"(a) ZH-12 句末标点后脚注紧跟汉字 → 报：{_why}", len(_hs) == 1, _tex)
    check(f"(a) ZH-12 该处 fixable，替换覆盖整段脚注并加 `\\,`：{_why}",
          len(_hs) == 1 and _hs[0].fixable
          and _hs[0].replacement == _tex[_hs[0].start:_hs[0].end] + "\\,",
          repr([_h.replacement for _h in _hs]))
for _tex, _why in (
    ("完成了；\\footnote{注}这就是结果。\n", "左邻分号不是句末标点"),
    ("完成了：\\footnote{注}这就是结果。\n", "左邻冒号不是句末标点"),
    ("完成了，\\footnote{注}这就是结果。\n", "左邻逗号归 ZH-10、不归 ZH-12"),
    ("完成了。\\footnote{注}\\,这就是结果。\n", "已有 `\\,`"),
    ("完成了。\\footnote{注} 这就是结果。\n", "脚注后已有空格"),
    ("完成了。\\footnote{注}~这就是结果。\n", "脚注后已有 `~`"),
    ("完成了。\\footnote{注}\\parencite{Wang2018}这就是结果。\n", "右邻是命令"),
    ("完成了。\\footnote{注}ABC\n", "右邻 ASCII"),
    ("完成了。\\footnote{注}\n", "句末没有右邻"),
    ("完成了。\\footnote{注这就是结果。\n", "花括号不配对"),
    ("% 完成了。\\footnote{注}这就是结果。\n", "注释区"),
    ("$完成了。\\footnote{注}这就是结果。$\n", "数学区"),
):
    check(f"(a) ZH-12 反例不报：{_why}", not hits_of(_tex, "ZH-12"), _tex)
check("(a) ZH-12 反例：英文稿不按中文口径报",
      not hits_of("Done.\\footnote{note}This follows.\n", "ZH-12", lang="en"))
# S2（B-273）ZH-21「脚注在句号前（应放句号后）」：手册 279「若在句号处则放于句号后」。
for _tex, _why in (("文本\\footnote{注}。\n", "句号"),
                   ("文本\\footnote{注}！\n", "叹号"),
                   ("文本\\footnote{注}？\n", "问号"),
                   ("文本\\footnote{注 {内层}}。\n", "嵌套花括号")):
    _hs = hits_of(_tex, "ZH-21")
    check(f"(a) ZH-21 脚注在句末标点前 → 报：{_why}", len(_hs) == 1, _tex)
    check(f"(a) ZH-21 该处 fixable，替换把标点搬到脚注前：{_why}",
          len(_hs) == 1 and _hs[0].fixable
          and _hs[0].replacement == _tex[-2] + _tex[_hs[0].start:_hs[0].end - 1],
          repr([_h.replacement for _h in _hs]))
# 手册 §常规 4.2 的例外：一句话两处脚注、各对应一部分 → 都放标点前，整句不报。
check("(a) ZH-21 反例：同句两处脚注（手册 4.2 例外）→ 不报",
      not hits_of("张三说上海下雪了\\footnote{甲}，而李四说面包真好吃\\footnote{乙}。\n",
                  "ZH-21"))
for _tex, _why in (("文本。\\footnote{注}下一句。\n", "已是「句号在脚注前」的正确序"),
                   ("文本，\\footnote{注}。\n", "左邻逗号不是本规则目标（逗号另归 ZH-10）"),
                   ("\\footnote{注}。\n", "行首/段首没有前文"),
                   ("% 文本\\footnote{注}。\n", "注释区"),
                   ("$文本\\footnote{注}。$\n", "数学区"),
                   ("\\item[文本\\footnote{注}。]\n", "可选参数内")):
    check(f"(a) ZH-21 反例不报：{_why}", not hits_of(_tex, "ZH-21"), _tex)
check("(a) ZH-21 反例：英文稿不按中文口径报",
      not hits_of("Done\\footnote{note}.\n", "ZH-21", lang="en"))
# fix round（B-273 验收 V3-1）：句末标点恰为字符串末字符（无尾随换行）时也要报。
check("(a) ZH-21 无尾随换行（标点为末字符）→ 报",
      len(hits_of("文本\\footnote{注}。", "ZH-21")) == 1)
check("(a) ZH-21 无尾随换行：replacement 把标点搬到脚注前",
      (lambda hs: len(hs) == 1 and hs[0].fixable
       and hs[0].replacement == "。\\footnote{注}")(
          hits_of("文本\\footnote{注}。", "ZH-21")))
check("(a) ZH-21 无尾随换行：嵌套脚注同样报",
      len(hits_of("文本\\footnote{注 {内层}}。", "ZH-21")) == 1)

# 数学环境（手册 §数学环境）
check("(a) ZH-13 数学里的三个点 → 报", len(hits_of("$a_1,...,a_n$\n", "ZH-13")) == 1)
check("(a) ZH-13 反例：**正文文本**里的六个点不报（那条规则只管数学区）",
      not hits_of("这里......六个点\n", "ZH-13"))
check("(a) ZH-14 集合里的竖线 → 报，且建议改成 \\mid",
      [h.replacement for h in hits_of("$\\{x|x>5\\}$\n", "ZH-14")] == ["\\{x\\mid x>5\\}"])
check("(a) ZH-14 反例：条件概率的竖线不在集合里 → 不报", not hits_of("$P(A|B)$\n", "ZH-14"))
check("(a) ZH-15 \\mathbf → 报", len(hits_of("$\\mathbf{a+b}=c$\n", "ZH-15")) == 1)
# S4（B-273）ZH-22「表格/浮动体内 \\footnote 会被吞」：手册 285–294（改用 \\footnotemark+\\footnotetext）。
for _env, _why in (("table", "table 环境"), ("tabular", "tabular 环境"),
                   ("figure", "figure 环境"), ("longtable", "longtable 环境")):
    _tex = f"\\begin{{{_env}}}x\\footnote{{注}}\\end{{{_env}}}\n"
    check(f"(a) ZH-22 {_why}内的 \\footnote → 报", len(hits_of(_tex, "ZH-22")) == 1, _tex)
    check(f"(a) ZH-22 {_why}内命中仍**只报不改**",
          not hits_of(_tex, "ZH-22")[0].fixable)
for _tex, _why in (("表外\\footnote{注}。\n", "表格外"),
                   ("\\begin{table}\\footnotemark\\end{table}\n", "\\footnotemark 不报"),
                   ("\\begin{table}\\footnotetext{注}\\end{table}\n", "\\footnotetext 不报"),
                   ("% \\begin{table}\\footnote{注}\\end{table}\n", "注释区"),
                   ("\\begin{table}\\caption{a\\footnote{注}}\\end{table}\n",
                    "table 内命中（\\caption 参数里的也算环境内）")):
    _n = len(hits_of(_tex, "ZH-22"))
    if _why.startswith("table 内命中"):
        check(f"(a) ZH-22 正例（补充）：{_why}", _n == 1, _tex)
    else:
        check(f"(a) ZH-22 反例不报：{_why}", _n == 0, _tex)

# 引用与版式（手册 §排版指南）
check("(a) ZH-16 图表引用缺波浪号 → 报", len(hits_of("如表\\ref{table1}所示。\n", "ZH-16")) == 1)
# S3（B-273）ZH-16 扩到中文序号与 \\eqref：手册 594「中文文本中的序号引用还需要波浪号」。
for _tex, _why in (("第\\ref{sec}节。\n", "「第\\ref」缺前波浪号"),
                   ("\\eqref{eq1}所示。\n", "\\eqref 后缺波浪号"),
                   ("表\\ref{tab1}所示。\n", "「表\\ref」缺前波浪号"),
                   ("定理\\ref{thm1}所示。\n", "中文序号+\\ref"),
                   ("第\\eqref{sec}条。\n", "中文序号+\\eqref")):
    check(f"(a) ZH-16 扩判据 → 报：{_why}", len(hits_of(_tex, "ZH-16")) == 1, _tex)
for _tex, _why in (("第~\\ref{sec}~节。\n", "前后都有 `~`"),
                   ("表~\\ref{tab1}~所示。\n", "前后都有 `~`"),
                   ("式~\\eqref{eq1}~所示。\n", "eqref 前后都有 `~`"),
                   ("第~\\ref{sec}~条。\n", "前后都有 `~`「条」")):
    check(f"(a) ZH-16 反例不报：{_why}", not hits_of(_tex, "ZH-16"), _tex)
check("(a) ZH-17 caption 里用 \\\\ → 报",
      len(hits_of("\\caption{第一行\\\\第二行}\n", "ZH-17")) == 1)
check("(a) ZH-19 人名缩写 J.N. → 报", len(hits_of("J.N. Mohanty 说。\n", "ZH-19")) == 1)
check("(a) ZH-19 人名缩写 H.T / H.P（期 87 真实样本）→ 报，且仍**只报不改**",
      len(hits_of("（H.T.Colebrooke）\n", "ZH-19")) == 1
      and len(hits_of("（H.P. Grice）\n", "ZH-19")) == 1
      and not any(h.fixable for h in hits_of("（H.T.Colebrooke）\n", "ZH-19")))
# ZH-19 降噪（2026-10-18 rules-docs Phase1）：固定缩写不报。
# 依据：经验书「若为英文缩写如 e.g. 则忽略」＋稿件仓约稿成品层残留 U.S/M.A/Q.E 等（Phase0 报告 b2）。
for _tex in ("（U.S. Congress）\n", "U.K. 政府\n", "（U.N. 决议）\n", "（A.D. 500）\n",
             "（B.C. 300）\n", "M.A. thesis\n", "B.A. 学位\n", "$Q.E.D.$\n",
             "P.S.(9)\n", "（P.R.C. 民法典）\n"):
    check(f"(a) ZH-19 反例：固定缩写/标号 {_tex.strip()!r} → 不报",
          not hits_of(_tex, "ZH-19"))
check("(a) ZH-19 反例：`e.g.`（小写缩写）本来就不匹配", not hits_of("e.g. this\n", "ZH-19"))


# 英文稿（手册 §英文文章；经验书 预校正·英文）
# 替换只覆盖命中片段（`n(2` → `n (2`），所以断言也只看片段
check("(a) EN-01 `John(2009)` 缺空格 → 报并可自动改",
      [h.replacement for h in hits_of("John(2009) argues\n", "EN-01", lang="en")] == ["n (2"])
check("(a) EN-02 英文冒号后缺空格 → 报并可自动改",
      [h.replacement for h in hits_of("Note:this is it\n", "EN-02", lang="en")] == [": t"])
check("(a) EN-03 覆盖 Word 中文撇号（原 EN-04 已并入：同一批字符，不再重复报）",
      len(hits_of("Under Kaplan’s framework\n", "EN-03", lang="en")) == 1
      and not any(r.id == "EN-04" for r in P.RULES))
check("(a) EN-03 是**可自动改**的：`Kaplan’s` → `Kaplan's`",
      [h.replacement for h in hits_of("Under Kaplan’s framework\n", "EN-03", lang="en")] == ["'"])
check("(a) EN-03 的映射：`“x”` → `` ``x'' ``、`—` → `---`、`…` → `\\ldots{}`",
      [h.replacement for h in hits_of("He said “character” once\n", "EN-03", lang="en")] == ["``", "''"]
      and [h.replacement for h in hits_of("it is key — really\n", "EN-03", lang="en")] == ["---"]
      and [h.replacement for h in hits_of("and then … nothing\n", "EN-03", lang="en")] == ["\\ldots{}"])
check("(a) EN-03 反例：整行以中文为主时**不改**（英文模板里的中文题名摘要）",
      not hits_of("\mytitleCN{论逻辑的文化相对性——“甲”}\n", "EN-03", lang="en"))
check("(a) EN-05 `pp. 6` 缺 \\, → 报", len(hits_of("see pp. 6-9\n", "EN-05", lang="en")) == 1)
check("(a) ALL-01 位图插图 → 报",
      len(hits_of("\\includegraphics[width=.5\\textwidth]{a.jpg}\n", "ALL-01")) == 1)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 不该报的不报：屏蔽层 ──")
BAD = "排中律,弗协 与 \\footnote{注,释} 与 $a,b$ 与 \\cite{key,other}\n"
check("(b) 注释里的半角标点不报",
      not hits_of("% 注释：甲,乙 与 丙.丁\n", "ZH-01") and not hits_of("% 注释：甲.乙\n", "ZH-02"))
check("(b) 数学里的半角逗号不报（那是数学语法）", not hits_of("$f(x,y)=z$ 说完了。\n", "ZH-01"))
check("(b) `\\cite{key,other}` 的键不报", not hits_of("见\\cite{key,other}。\n", "ZH-01"))
check("(b) 宏名不算正文：`\\hfill` 不报 ZH-18",
      not hits_of("\\begin{quotation}\\hfill\\end{quotation}\n", "ZH-18"))
check("(b) 但 `\\textbf{bold}` 的**参数**是楷体环境里的英文 → 要报（并另有 ZH-20 提示改用 \\textsf）",
      len(hits_of("\\begin{quotation}\\textbf{bold}\\end{quotation}\n", "ZH-18")) == 1
      and len(hits_of("\\begin{quotation}\\textbf{bold}\\end{quotation}\n", "ZH-20")) == 1)
check("(b) 自带 `\\textup` 的命令参数不报 ZH-18",
      not hits_of("\\begin{quotation}\\textup{This is fine} \\ct{a6}\\end{quotation}\n", "ZH-18"))
check("(b) 楷体环境里**裸露的英文**要报 ZH-18",
      len(hits_of("\\begin{quotation}bare English here\\end{quotation}\n", "ZH-18")) == 1)
check("(b) 英文规则不去管英文模板里的中文题名摘要（整行以中文为主 → 跳过）",
      not hits_of("\\mytitleCN{论逻辑的文化相对性}\n", "EN-03", lang="en"))
check("(b) 英文规则管英文正文（整行 ASCII 为主 → 报）",
      len(hits_of("Under Kaplan’s framework, “character” is key.\n", "EN-03", lang="en")) >= 2)
check("(b) 反例：`（Beall 2006）` 这种中文括号括英文引用**不算**半角括号问题",
      not hits_of("（Beall Restall 2006）\n", "ZH-07"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 自动改：只碰无歧义的 ──")
tex = ("令 $P$ 命题变元的无穷集合, 公式集递归定义。\n"
       "页：第8-12页；顿号：“甲”、‘乙’。\n"
       "集合：$\\{x|x>5\\}$。\n"
       "脚注：\\footnote{[] 鞠实儿. 论逻辑的文化相对性. 中国社会科学}\n")
rep = P.check(tex, lang="zh")
out, applied = P.apply_fixes(tex, rep)
ids = sorted({h.rule_id for h in applied})
check("(c) 自动改覆盖 ZH-01/ZH-06/ZH-09/ZH-14", ids == ["ZH-01", "ZH-06", "ZH-09", "ZH-14"], f"{ids}")
check("(c) 半角改全角时**吃掉多余空格**：`集合, 公式集` → `集合，公式集`",
      "无穷集合，公式集递归定义" in out, out.splitlines()[0])
check("(c) 页码改成 `第8\\cndash 12页`", "第8\\cndash 12页" in out)
check("(c) 顿号被删（`“甲”、‘乙’` → `“甲”‘乙’`）",
      "顿号：“甲”‘乙’" in out, out.splitlines()[1])
check("(c) 集合竖线改成 `\\mid`", "\\{x\\mid x>5\\}" in out)
foot_hits = [h for h in rep.hits if h.rule_id == "ZH-02"]
check("(c) 脚注里的中文文献条目（`鞠实儿. 论逻辑…`）**只报不改**",
      foot_hits and all(not h.fixable for h in foot_hits), f"{[(h.text, h.fixable) for h in foot_hits]}")
check("(c) 英文弯引号整行替换：`Kaplan’s` 与 `“character”` 一次改对",
      P.apply_fixes("Under Kaplan’s framework, “character” is key — really.\n",
                    P.check("Under Kaplan’s framework, “character” is key — really.\n", lang="en"))[0]
      == "Under Kaplan's framework, ``character'' is key --- really.\n")
check("(c) 中文行**不因英文规则被改**（语言判据拦住）",
      P.apply_fixes("他说“甲”，这是‘乙’——对不对？\n",
                    P.check("他说“甲”，这是‘乙’——对不对？\n", lang="en"))[0]
      == "他说“甲”，这是‘乙’——对不对？\n")
check("(c) 输入文本没有被就地改动（apply_fixes 返回新字符串）",
      tex.startswith("令 $P$ 命题变元的无穷集合, 公式集"))

# ZH-08 新口径（--fix 口径） vs 初排接线（白名单口径）——2026-10-28 用户拍板 A
# 短横 `\edash `；汉字-汉字/小写英文邻/数学紧邻保持：数学紧邻只报不改。
mix = "马丁-洛夫类型论与 would-反事实条件句，以及 $\\phi$-世界、2-群胚。\n"
mix_rep = P.check(mix, lang="zh")
mix_out, mix_applied = P.apply_fixes(mix, mix_rep)
check("(c) ZH-08 新口径在 --fix 里自动改：只有 `2-群胚` 变成 `2\\edash 群胚`",
      mix_out == "马丁-洛夫类型论与 would-反事实条件句，以及 $\\phi$-世界、2\\edash 群胚。\n"
      and [h.rule_id for h in mix_applied] == ["ZH-08"], mix_out.strip())
w_out, w_applied = P.apply_fixes(mix, mix_rep, rule_ids=P.INITIAL_LAYOUT_FIXABLE)
check("(c) 初排白名单（INITIAL_LAYOUT_FIXABLE）下 ZH-08 同样改 `2-群胚`（默认初排启用）",
      w_out == mix_out and [h.rule_id for h in w_applied] == ["ZH-08"],
      f"applied={[h.rule_id for h in w_applied]}")
check("(c) 初排白名单仍报其他 fixable 规则（ZH-01/06/09/12/14 都在名单里）",
      set(P.INITIAL_LAYOUT_FIXABLE) >= {"ZH-01", "ZH-06", "ZH-09", "ZH-12", "ZH-14", "ZH-21"}
      and "ZH-08" in P.INITIAL_LAYOUT_FIXABLE and P.OPT_IN_FIXABLE == ())

# ZH-12（B-272）：句末脚注后补 `\,` —— --fix 与初排白名单两条口径都生效、且幂等
zh12_tex = "完成了。\\footnote{注}这就是结果。\n"
zh12_new, zh12_applied = P.apply_fixes(zh12_tex, P.check(zh12_tex, lang="zh"))
check("(c) ZH-12 --fix：在整段脚注后插 `\,`（`。\\footnote{注}这就是结果` → `…注}\\,这就是结果`）",
      zh12_new == "完成了。\\footnote{注}\\,这就是结果。\n"
      and [h.rule_id for h in zh12_applied] == ["ZH-12"], repr(zh12_new))
check("(c) ZH-12 幂等：对结果再跑不再新增",
      P.apply_fixes(zh12_new, P.check(zh12_new, lang="zh"))[0] == zh12_new
      and [h for h in P.check(zh12_new, lang="zh").hits if h.rule_id == "ZH-12"] == [])
check("(c) ZH-12 输入原文不被就地改动",
      zh12_tex == "完成了。\\footnote{注}这就是结果。\n")
zh12_w, zh12_wa = P.apply_fixes(zh12_tex, P.check(zh12_tex, lang="zh"),
                                rule_ids=P.INITIAL_LAYOUT_FIXABLE)
check("(c) ZH-12 在初排白名单（INITIAL_LAYOUT_FIXABLE）下同样自动改",
      zh12_w == zh12_new and [h.rule_id for h in zh12_wa] == ["ZH-12"])

# ZH-21（B-273）：脚注在句号前 → 把句号搬到脚注之前；幂等；初排白名单同样生效。
zh21_tex = "文本\\footnote{注}。\n"
zh21_new, zh21_applied = P.apply_fixes(zh21_tex, P.check(zh21_tex, lang="zh"))
check("(c) ZH-21 --fix：`文本\\footnote{注}。` → `文本。\\footnote{注}`",
      zh21_new == "文本。\\footnote{注}\n" and [h.rule_id for h in zh21_applied] == ["ZH-21"],
      repr(zh21_new))
check("(c) ZH-21 幂等：对结果再跑不再新增",
      P.apply_fixes(zh21_new, P.check(zh21_new, lang="zh"))[0] == zh21_new
      and not [h for h in P.check(zh21_new, lang="zh").hits if h.rule_id == "ZH-21"])
check("(c) ZH-21 嵌套脚注整段搬家、不破坏内容",
      P.apply_fixes("文本\\footnote{见{内层}的注}。\n",
                    P.check("文本\\footnote{见{内层}的注}。\n", lang="zh"))[0]
      == "文本。\\footnote{见{内层}的注}\n")
check("(c) ZH-21 无尾随换行 --fix：`文本\\footnote{注}。` → `文本。\\footnote{注}`",
      P.apply_fixes("文本\\footnote{注}。",
                    P.check("文本\\footnote{注}。", lang="zh"))[0]
      == "文本。\\footnote{注}")
zh21_w, zh21_wa = P.apply_fixes(zh21_tex, P.check(zh21_tex, lang="zh"),
                                rule_ids=P.INITIAL_LAYOUT_FIXABLE)
check("(c) ZH-21 在初排白名单（INITIAL_LAYOUT_FIXABLE）下同样自动改",
      zh21_w == zh21_new and [h.rule_id for h in zh21_wa] == ["ZH-21"])

# `apply_fixes(..., rule_ids=…)` 契约（engine-core E1 接线用）：**显式窄子集**只改这些 id。
# E1 的首批窄白名单 = ZH-01..06（EN-03/ZH-14 留后批）；这里直接按 id 元组验，不依赖宽常量。
e1_narrow = ("ZH-01", "ZH-02", "ZH-03", "ZH-04", "ZH-05", "ZH-06")
e1_tex = "甲,乙 与 丙、“丁”，以及 $\\{x|x\\}$ 和第8-12页。\n"
e1_out, e1_applied = P.apply_fixes(e1_tex, P.check(e1_tex, lang="zh"), rule_ids=e1_narrow)
check("(c) 显式窄子集 rule_ids：只改 ZH-01/ZH-06，ZH-09/ZH-14 **不动**（E1 契约）",
      "甲，乙" in e1_out and "丙“丁”" in e1_out
      and "第8-12页" in e1_out and "\\{x|x\\}" in e1_out
      and sorted({h.rule_id for h in e1_applied}) == ["ZH-01", "ZH-06"],
      f"applied={sorted({h.rule_id for h in e1_applied})}")
check("(c) rule_ids 按显式子集工作：传单个 id 只改它；传空元组 = 一处不改",
      sorted({h.rule_id for h in P.apply_fixes(e1_tex, P.check(e1_tex, lang="zh"),
                                               rule_ids=("ZH-06",))[1]}) == ["ZH-06"]
      and P.apply_fixes(e1_tex, P.check(e1_tex, lang="zh"), rule_ids=())[1] == [])



# 位置移动：脚注/引文移到标点之前（函数手册 §常规 4.1 / 5.1）
moved, logs = P.move_marks_before_punct("甲说，\\footnote{嵌套{花}括号}不对。见，\\parencite{a,b}那里。\n")
check("(c) 脚注被移到「，」之前，且**嵌套花括号**完整搬走",
      "甲说\\footnote{嵌套{花}括号}，不对。" in moved, moved.strip())
check("(c) 引文（键里带逗号）也被移到「，」之前",
      "见\\parencite{a,b}，那里。" in moved, moved.strip())
check("(c) 位置移动有日志（逐条说明改了哪一行）",
      len(logs) == 2 and all("移到" in x for x in logs), f"{logs}")
check("(c) 反例：无法配对的花括号 → 跳过并记日志，不改坏",
      P.move_marks_before_punct("甲，\\footnote{没关完\n")[1][0].startswith("跳过"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) CLI 契约 ──")
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"))


def run(*args):
    return subprocess.run([PY, str(ENGINE / "proofcheck.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV, cwd=str(ENGINE))


tmp = tempfile.mkdtemp(prefix="slcn_proof_")
tex_path = os.path.join(tmp, "Demo.tex")
with open(tex_path, "w", encoding="utf-8") as fh:
    fh.write(tex)

r = run("--tex", tex_path)
check("(d) 有命中 → 退出码 1（正常情况，不是错误）", r.returncode == 1, f"rc={r.returncode}")
check("(d) 契约行给出命中数与报告路径",
      "完成。一校核查：命中" in r.stdout and "_一校核查.md" in r.stdout,
      r.stdout.strip().splitlines()[-1])
check("(d) 报告写到源文件同目录", os.path.exists(os.path.join(tmp, "Demo_一校核查.md")))
md = open(os.path.join(tmp, "Demo_一校核查.md"), encoding="utf-8").read()
check("(d) 报告里有摘要表与依据", "## 摘要" in md and "依据" in md)
check("(d) 报告里逐条列了行号", "| 行 |" in md)

r2 = run("--tex", tex_path, "--fix")
check("(d) `--fix` 写出新文件、**输入文件字节不变**",
      os.path.exists(os.path.join(tmp, "Demo_一校核查后.tex"))
      and open(tex_path, encoding="utf-8").read() == tex)
check("(d) `--fix` 的产物里半角逗号已改", "集合，公式集" in
      open(os.path.join(tmp, "Demo_一校核查后.tex"), encoding="utf-8").read())

clean = os.path.join(tmp, "Clean.tex")
with open(clean, "w", encoding="utf-8") as fh:
    fh.write("这是一段干净的中文正文，标点全角，没有问题。\n")
r3 = run("--tex", clean)
check("(d) 没有命中 → 退出码 0", r3.returncode == 0, f"rc={r3.returncode}")
r4 = run("--tex", "/tmp/不存在_zzz.tex")
check("(d) 文件不存在 → 退出码 2", r4.returncode == 2 and "找不到文件" in r4.stderr)
r5 = run("--version")
check("(d) `--version` 可用", r5.returncode == 0 and "SLCN 引擎" in r5.stdout)
r6 = run("--tex", tex_path, "--lang", "en")
check("(d) `--lang en` 时中文规则让位（命中数下降）",
      "命中" in r6.stdout and "ZH-01" not in r6.stdout, r6.stdout[:60])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 模板合规（report-only）──")
import shutil  # noqa: E402
import templatecheck as TC  # noqa: E402

CANON_STY = ENGINE.parent / "编辑笔记" / "模板文件" / "SLCN-MIX.sty"
tpl_tmp = tempfile.mkdtemp(prefix="slcn_tplcheck_")
BIB_OK = ("@article{K1,\n  author = {A. B.},\n  title = {T},\n"
          "  journal = {J},\n  year = {2020}\n}\n")
FIG_BLOCK = ("\\begin{figure}[htbp]\\centering\\includegraphics{fig.png}"
             "\\caption{示意图}\\end{figure}")
CN_OK = (r"""\documentclass[UTF8]{article}
\usepackage{SLCN-MIX}
\newcommand{\mytitle}{论某问题}
\newcommand{\myfirstauthor}{张三}
\newcommand{\myfirstaffiliation}{某大学哲学系}
\newcommand{\myabstract}{这是一段摘要。}
\newcommand{\mykeywords}{甲；乙；丙}
\addbibresource{reference.bib}
\begin{document}
正文一段，引用\cite{K1}。
""" + FIG_BLOCK + r"""
\printbibliography
\end{document}
""")
EN_OK = r"""\documentclass[UTF8]{article}
\usepackage{SLEN}
\newcommand{\mytitle}{A Title}
\newcommand{\myabstract}{An abstract.}
\begin{document}
Body text.
\end{document}
"""


def tprod(name, tex, *, bib=True, sty=True, files=()):
    d = os.path.join(tpl_tmp, name)
    os.makedirs(d)
    tex_path = os.path.join(d, name + ".tex")
    with open(tex_path, "w", encoding="utf-8") as fh:
        fh.write(tex)
    if bib is not False:
        with open(os.path.join(d, "reference.bib"), "w", encoding="utf-8") as fh:
            fh.write(bib if isinstance(bib, str) else BIB_OK)
    if sty:
        shutil.copyfile(str(CANON_STY), os.path.join(d, "SLCN-MIX.sty"))
    for rel, data in files:
        with open(os.path.join(d, rel), "wb") as fh:
            fh.write(data)
    return tex_path


def trep(tex, name, template="", **kw):
    return TC.check_product(tprod(name, tex, **kw), template)


def trule(rep, rid):
    return next(r for r in rep.results if r.rule.id == rid)


def tmut(base, old, new):
    assert old in base, old[:40]
    return base.replace(old, new)


def run_tc(*args):
    return subprocess.run([PY, str(ENGINE / "templatecheck.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=ENV, cwd=str(ENGINE))


check("(e) 8 条规则、ID 恰为 T1..T8",
      [r.id for r in TC.RULES] == ["T%d" % i for i in range(1, 9)])
cn_rep = trep(CN_OK, "CnAll", files=[("fig.png", b"png")])
check("(e) 正例：CN 初排稿 8 条全 PASS", cn_rep.counts() == (8, 0, 0), str(cn_rep.counts()))
en_t3 = trule(trep(EN_OK, "EnAll"), "T3")
check("(e) EN 稿：关键词 3–8 是 SKIP 并在摘要里说明（不是 FAIL）",
      en_t3.status == "PASS" and "SKIP" in en_t3.summary, en_t3.summary)
en_aligned = ("\\documentclass[UTF8]{article}\n\\usepackage{SLEN}\n"
              "\\newcommand{\\mytitle}" + " " * 90 + "% comment\n{A Title}\n"
              "\\begin{document}\nx\n\\end{document}\n")
en_meta = TC.parse_metadata(en_aligned, TC.raw_mask(en_aligned))
check("(e) 题名的值写在下一行、行尾带注释对齐时仍能解析（EN 模板真实形态）",
      en_meta.get("mytitle", ("", 0))[0] == "A Title")

check("(e) T1 反例：缺 \\begin{document} → FAIL",
      trule(trep(tmut(CN_OK, "\\begin{document}\n", ""), "T1a"), "T1").status == "FAIL")
check("(e) T1 反例：document 环境里没有正文 → FAIL",
      trule(trep(tmut(CN_OK, "正文一段，引用\\cite{K1}。\n" + FIG_BLOCK + "\n", ""),
                 "T1b"), "T1").status == "FAIL")
check("(e) T2 反例：缺题名定义 → FAIL",
      trule(trep(tmut(CN_OK, "\\newcommand{\\mytitle}{论某问题}\n", ""), "T2a"), "T2").status
      == "FAIL")
check("(e) T2 反例：作者仍是模板占位符（\\kaishu xxx）→ FAIL",
      any("占位符" in f.issue for f in trule(
          trep(tmut(CN_OK, "{张三}", "{{\\kaishu xxx}}"), "T2b"), "T2").findings))
check("(e) T2 反例：单位仍是【未识别…】→ FAIL",
      any("未识别" in f.issue for f in trule(
          trep(tmut(CN_OK, "{某大学哲学系}", "{【未识别：第一作者单位，请手工填写】}"),
               "T2c"), "T2").findings))
t2_null = trep(tmut(CN_OK, "\\begin{document}\n",
                    "\\newcommand{\\mysecondauthor}{null}\n"
                    "%\\renewcommand{\\mysecondauthor}{xxx}\n\\begin{document}\n"),
               "T2d", files=[("fig.png", b"png")])
check("(e) T2 不误报：第二作者 null / 被注释 → PASS", trule(t2_null, "T2").status == "PASS")

check("(e) T3 反例：关键词 2 个 → FAIL",
      trule(trep(tmut(CN_OK, "{甲；乙；丙}", "{甲；乙}"), "T3a"), "T3").status == "FAIL")
check("(e) T3 边界：关键词 8 个 → PASS",
      trule(trep(tmut(CN_OK, "{甲；乙；丙}", "{甲；乙；丙；丁；戊；己；庚；辛}"),
                 "T3b"), "T3").status == "PASS")
check("(e) T3 反例：关键词 9 个 → FAIL",
      trule(trep(tmut(CN_OK, "{甲；乙；丙}", "{甲；乙；丙；丁；戊；己；庚；辛；壬}"),
                 "T3c"), "T3").status == "FAIL")
t3_long = trule(trep(tmut(CN_OK, "{这是一段摘要。}", "{" + "摘" * 1200 + "}"), "T3d"), "T3")
check("(e) T3：摘要只有字数 INFO、没有长度阈值（长摘要不 FAIL）",
      t3_long.status == "PASS" and any("摘要字数" in n for n in t3_long.notes))
check("(e) T3 反例：摘要为空 → FAIL",
      trule(trep(tmut(CN_OK, "{这是一段摘要。}", "{}"), "T3e"), "T3").status == "FAIL")

check("(e) T4 正例：bib 字段齐全、cite 闭合 → PASS",
      trule(trep(CN_OK, "T4a", files=[("fig.png", b"png")]), "T4").status == "PASS")
t4_miss = trule(trep(CN_OK, "T4b", bib="@article{K1,\n author = {A.},\n journal = {J}\n}\n",
                     files=[("fig.png", b"png")]), "T4")
check("(e) T4 反例：缺 author/title/year → FAIL",
      t4_miss.status == "FAIL" and any("缺字段" in f.issue for f in t4_miss.findings))
t4_misc = trule(trep(CN_OK, "T4c",
                     bib="@misc{K1,\n author = {A.},\n title = {T},\n year = {2020}\n}\n",
                     files=[("fig.png", b"png")]), "T4")
check("(e) T4 反例：misc 缺 note/howpublished/url → FAIL",
      t4_misc.status == "FAIL" and any("note/howpublished/url" in f.issue
                                       for f in t4_misc.findings))
t4_unknown = trule(trep(CN_OK, "T4d",
                        bib="@dataset{K1,\n author = {A.},\n title = {T},\n year = {2020}\n}\n",
                        files=[("fig.png", b"png")]), "T4")
check("(e) T4 反例：未知条目类型 → FAIL",
      t4_unknown.status == "FAIL" and any("未知条目类型" in f.issue
                                          for f in t4_unknown.findings))
t4_cite = trule(trep(tmut(CN_OK, "\\cite{K1}", "\\cite{NOPE}"), "T4e",
                     files=[("fig.png", b"png")]), "T4")
check("(e) T4 反例：cite 键在 bib 里没有条目 → FAIL",
      t4_cite.status == "FAIL" and any("没有对应条目" in f.issue for f in t4_cite.findings))
t4_dup = trule(trep(CN_OK, "T4f", bib=BIB_OK + BIB_OK, files=[("fig.png", b"png")]), "T4")
check("(e) T4 反例：bib 重复 key → FAIL",
      t4_dup.status == "FAIL" and any("重复" in f.issue for f in t4_dup.findings))
t4_unused = trule(trep(CN_OK, "T4g",
                       bib=BIB_OK + "@book{K2,\n author = {B.},\n title = {U},\n"
                                     " publisher = {P},\n year = {2021}\n}\n",
                       files=[("fig.png", b"png")]), "T4")
check("(e) T4：未引用条目只 INFO（不算规范）",
      t4_unused.status == "PASS" and any("未引用条目" in n for n in t4_unused.notes))
t4_nobib = trule(trep(tmut(tmut(CN_OK, "\\addbibresource{reference.bib}\n", ""),
                           "正文一段，引用\\cite{K1}。", "正文一段，没有引用。"), "T4h", bib=False,
                      files=[("fig.png", b"png")]), "T4")
check("(e) T4：没有 .bib → SKIP（字段检查做不了，不假报）",
      t4_nobib.status == "SKIP", t4_nobib.status)

check("(e) T5 正例：figure 有非空 \\caption → PASS",
      trule(trep(CN_OK, "T5a", files=[("fig.png", b"png")]), "T5").status == "PASS")
check("(e) T5 反例：figure 没有 \\caption → FAIL",
      trule(trep(tmut(CN_OK, FIG_BLOCK, tmut(FIG_BLOCK, "\\caption{示意图}", "")), "T5b",
                 files=[("fig.png", b"png")]), "T5").status == "FAIL")
check("(e) T5 反例：\\caption 被注释掉 → FAIL",
      trule(trep(tmut(CN_OK, "\\caption{示意图}", "%\\caption{示意图}"), "T5c",
                 files=[("fig.png", b"png")]), "T5").status == "FAIL")
check("(e) T5 反例：figure 没有闭合 \\end → FAIL",
      trule(trep(tmut(CN_OK, FIG_BLOCK, FIG_BLOCK.replace("\\end{figure}", "")), "T5d",
                 files=[("fig.png", b"png")]), "T5").status == "FAIL")
check("(e) T5 不误报：verbatim 里的 figure 不算（SKIP）",
      trule(trep(tmut(CN_OK, FIG_BLOCK,
                      "\\begin{verbatim}\n\\begin{figure}\n\\end{figure}\n\\end{verbatim}"),
                 "T5e", files=[("fig.png", b"png")]), "T5").status == "SKIP")

check("(e) T6 正例：\\usepackage 与模板精确一致 → PASS",
      trule(trep(CN_OK, "T6a", files=[("fig.png", b"png")]), "T6").status == "PASS")
check("(e) T6 反例：\\usepackage{SLCN-MIX4} ≠ 模板 SLCN-MIX → FAIL",
      trule(trep(tmut(CN_OK, "{SLCN-MIX}", "{SLCN-MIX4}"), "T6b", "SLCN-MIX",
                 files=[("fig.png", b"png")]), "T6").status == "FAIL")
check("(e) T6 反例：没有可识别的模板 \\usepackage → FAIL",
      trule(trep(tmut(CN_OK, "\\usepackage{SLCN-MIX}\n", ""), "T6c", "SLCN-MIX",
                 files=[("fig.png", b"png")]), "T6").status == "FAIL")

check("(e) T7 正例：产品目录的 .sty 与 canonical 一致 → PASS",
      trule(trep(CN_OK, "T7a", files=[("fig.png", b"png")]), "T7").status == "PASS")
check("(e) T7 反例：产品目录的 .sty 被改过（sha 不一致）→ FAIL",
      trule(trep(CN_OK, "T7b", sty=False,
                 files=[("SLCN-MIX.sty", b"% tampered\n"), ("fig.png", b"png")]), "T7").status
      == "FAIL")
check("(e) T7 反例：产品目录缺 .sty → FAIL",
      trule(trep(CN_OK, "T7c", sty=False, files=[("fig.png", b"png")]), "T7").status == "FAIL")

check("(e) T8 正例：\\includegraphics 的素材存在 → PASS",
      trule(trep(CN_OK, "T8a", files=[("fig.png", b"png")]), "T8").status == "PASS")
check("(e) T8 反例：图片素材不存在 → FAIL",
      trule(trep(tmut(CN_OK, "{fig.png}", "{nope.png}"), "T8b",
                 files=[("fig.png", b"png")]), "T8").status == "FAIL")
check("(e) T8 不误报：注释里的 \\includegraphics 不算（SKIP）",
      trule(trep(tmut(CN_OK, FIG_BLOCK, "%\\includegraphics{nope.png}"), "T8c",
                 files=[("fig.png", b"png")]), "T8").status == "SKIP")
check("(e) T8 不误报：\\IfFileExists 兜底 → 视为通过",
      "视为通过" in trule(trep(tmut(CN_OK, FIG_BLOCK,
                                    "\\begin{figure}[htbp]\\centering"
                                    "\\IfFileExists{fig.png}{}{\\includegraphics{fig.png}}"
                                    "\\caption{示意图}\\end{figure}"), "T8d"), "T8").notes[-1])

r_ver = run_tc("--version")
check("(e) CLI `--version` 退出码 0、报版本", r_ver.returncode == 0 and "SLCN 引擎"
      in r_ver.stdout)
r_self = run_tc("--self-test")
check("(e) CLI `--self-test` 退出码 0、8 条规则正例全过",
      r_self.returncode == 0 and "自检通过：8/8" in r_self.stdout, r_self.stdout.strip())
r_noin = run_tc()
check("(e) CLI 缺 --input → 退出码 2", r_noin.returncode == 2 and "--input" in r_noin.stderr)
r_badtpl = run_tc("--input", os.path.dirname(tprod("CliTpl", CN_OK)),
                  "--template", "NoSuch", "--out", os.path.join(tpl_tmp, "x.md"))
check("(e) CLI 未知模板 → 退出码 2", r_badtpl.returncode == 2 and "未知模板" in r_badtpl.stderr)
r_badout = run_tc("--input", os.path.dirname(tprod("CliOutBad", CN_OK)), "--out",
                  os.path.join(tpl_tmp, "x.txt"))
check("(e) CLI --out 不是 .md → 退出码 2", r_badout.returncode == 2 and ".md" in r_badout.stderr)
two = os.path.join(tpl_tmp, "TwoTex")
os.makedirs(two)
for stem in ("AB", "CD"):
    with open(os.path.join(two, stem + ".tex"), "w", encoding="utf-8") as fh:
        fh.write(CN_OK)
r_two = run_tc("--input", two)
check("(e) CLI 目录里有 2 个 .tex → 退出码 2（不替用户猜）",
      r_two.returncode == 2 and "2 个 .tex" in r_two.stderr, r_two.stderr[:80])

cli_dir = os.path.dirname(tprod("CliDir", CN_OK, files=[("fig.png", b"png")]))
r_dir = run_tc("--input", cli_dir)
check("(e) CLI 目录输入：默认报告写在产品目录、契约行 PASS 8 / FAIL 0 / SKIP 0",
      r_dir.returncode == 0 and "完成。模板合规：PASS 8 / FAIL 0 / SKIP 0" in r_dir.stdout
      and os.path.exists(os.path.join(cli_dir, "CliDir_模板合规.md")), r_dir.stdout[-90:].strip())

out_dir = os.path.dirname(tprod("CliOut", CN_OK, files=[("fig.png", b"png")]))
out_md = os.path.join(tpl_tmp, "cli_out.md")
r_out = run_tc("--input", out_dir, "--out", out_md)
check("(e) CLI --out 指到仓库外：报告落点正确、产品目录不多出报告",
      r_out.returncode == 0 and os.path.exists(out_md)
      and not os.path.exists(os.path.join(out_dir, "CliOut_模板合规.md")))

snap_dir = os.path.dirname(tprod("Snap", CN_OK, files=[("fig.png", b"png")]))
snap = {}
for fn in sorted(os.listdir(snap_dir)):
    with open(os.path.join(snap_dir, fn), "rb") as fh:
        snap[fn] = fh.read()
r_snap = run_tc("--input", snap_dir, "--out", os.path.join(tpl_tmp, "snap.md"))
after = {}
for fn in sorted(os.listdir(snap_dir)):
    with open(os.path.join(snap_dir, fn), "rb") as fh:
        after[fn] = fh.read()
check("(e) 只读保证：输入/产品文件字节零变化、产品目录不多出文件",
      r_snap.returncode == 0 and snap == after, f"rc={r_snap.returncode}")

many = CN_OK.replace(FIG_BLOCK, "".join(
    "\\begin{figure}[htbp]\\centering\\includegraphics{fig%d.png}\\end{figure}\n" % i
    for i in range(60)))
many_dir = os.path.dirname(tprod("Many", many))
many_md = os.path.join(tpl_tmp, "many.md")
r_many = run_tc("--input", many_dir, "--out", many_md)
md = open(many_md, encoding="utf-8").read()
check("(e) 报告结构：摘要表 + 依据 + 明细行",
      r_many.returncode == 1 and "## 摘要" in md and "依据：" in md and "| 行 |" in md)
check("(e) 报告单规则 50 条上限：60 条只列 50 条并注明省略",
      "另有 10 条已省略（共 60 条）" in md)

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
