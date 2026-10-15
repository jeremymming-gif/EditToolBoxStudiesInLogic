#!/usr/bin/env python3.14
"""`N. 中文标题`（task-7 第 6 项）的判据测试。

症状：`classify_en_heading` 的 `_HEAD_EN1` 分支要求编号后的首字符**大写**
（为英文 `1. Introduction` 写的），于是中文稿里同形的 `1. 莱布尼茨` /
`1. 引言` 一个都认不出来，标题原样留在正文里（产物 0 个 `\\section`）。

三类断言：
  ① 形态判据 `_is_cn_dot_heading`（纯字符串，毫秒级）：认标题、挡列表项。
  ② 现造的最小 docx（临时 zip，只含 word/document.xml）——端到端证明
     「点式中文标题 → \\section / \\subsection」，以及四类反例一动不动：
     连排名单、整句列表项、引文里的编号行、文献清单行；外加英文侧逐字不变。
  ③ 真实语料（语料不在本机则 SKIP）：13 篇受影响稿子的标题数，
     并与**人工成品 .tex** 的 \\section/\\subsection 对齐（这是层级判据的根据：
     03 Ziming_Song 人工成品把 `1. 决策论与愿望/信念` 排成 \\subsection）。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_cn_dot_heading.py
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

from slcn.docxsource import (  # noqa: E402
    _HEAD_CN_DOT, classify_en_heading, _is_cn_dot_heading, parse_docx,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ══ ① 形态判据（纯字符串）═════════════════════════════════════════════════
print("── ① 形态判据 `_is_cn_dot_heading` ──")

# 必须认成标题的（全部来自真实语料 04 Bo_Chen / 07 Guangwu_Feng / 06 Lili_Lu …）
MUST = [
    "1. 莱布尼茨",                                  # 04 Bo_Chen：人名小节（人工成品是 \section）
    "5. 维特根斯坦",
    "1. 引言",                                      # 01 Pengshu_Shan 等
    "1.引言",                                       # 07 Guangwu_Feng：点后**没有空格**
    "3. 以非直接解读为基础的两例数学哲学研究进路",       # 02 Pengshu_Shan：18 字长标题
    "2. 决策论与愿望/信念",                           # 03 Ziming_Song：标题里有 `/`
    "4. 结语",
]
for t in MUST:
    check(f"认成标题：{t}", _is_cn_dot_heading(t))

# 必须挡住的（全部是真实语料里与标题同形的**列表项/文献行**）
MUST_NOT = [
    # 09 Jiawei_Chu：整句列表项（带 `。`、超 40 字）
    "1. 尚未形成对论证行动完整生成过程的统一解释。三条径路各有其代表性人物，",
    # 08 Ran_Ren：因明五支（带 `：`/`。`/引号）
    "1.命题（宗）：“此山有火。”",
    "2.理由（因）：“因有烟故。”",
    # 04 Yanjing_Zhang：审稿意见逐条回复（带 `，`）
    "1. 第一页第二段，提出周延问题的文献建议列出原始文献，因为本页第三段提出的",
    # 04 Yanjing_Zhang：短句列表项，**以 `。` 收尾**（标题不会带句号）
    "4. 第八页缺失“四种亚氏量词组成的对当方阵”。",
    # 67A 邱德钧：文末文献清单（带 `，`/`。`、超 40 字）
    "1. 陶景侃，《大学逻辑教程》，兰州大学出版社，1993年8月，第一版。",
    # 「20 世纪」式残句（_HEAD_CN_BAD_TAIL）
    "1. 2020年到2023年的统计数据如下表所示",
    # 点后是数字 = `N.M` 子节，归 _HEAD_EN3，不该被这条认走
    "2.2 表征定理",
    # 英文标题：行为必须逐字不变（`1. Introduction` 仍由 _HEAD_EN1 认）
    "1. Introduction",
    "2. Formal Examination of Priest’s view of nothing:",   # 04 Wenfang_Wang：英文+冒号，本轮**不管**
]
for t in MUST_NOT:
    check(f"不算标题：{t}", not _is_cn_dot_heading(t))

# 英文侧逐字不变：同一条输入在新旧代码下的返回值必须一致（旧行为在此硬编码）
EN_KEEP = {
    "1. Introduction": (1, "Introduction"),
    "2. 决策论与愿望/信念": (0, "2. 决策论与愿望/信念"),   # 英文判据本来就不该认中文
    "2. Formal Examination of Priest’s view of nothing:": (0, "2. Formal Examination of Priest’s view of nothing:"),
    "3.3.1 Problems for extrapolationists": (0, "3.3.1 Problems for extrapolationists"),
    "1.2 Title": (2, "Title"),
    "1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…": (0, "1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…"),
}
for t, want in EN_KEEP.items():
    got = classify_en_heading(t)
    check(f"classify_en_heading 不变：{t[:34]}", got == want, f"got {got!r} want {want!r}")

check("_HEAD_CN_DOT 不接受英文首字母", not _HEAD_CN_DOT.match("1. Introduction"))
check("_HEAD_CN_DOT 不接受 `N.M`（点后是数字）", not _HEAD_CN_DOT.match("1.2 Title"))

# ══ ② 最小 docx 端到端 ════════════════════════════════════════════════════
print("\n── ② 最小 docx 端到端 ──")

HEAD = ["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑的若干问题。", "关键词：逻辑；数据库"]
FILLER = ["正文第 %d 段，用来把正文撑起来。" % i for i in range(24)]


def make_docx(paras):
    """只含 word/document.xml 的最小 docx（与 test_tail_block.py 同款）。"""
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


TMP = []

# ②-a：5 种必须认出来的形态（点+空格 / 点无空格 / 人名 / 长标题 / 无 `一、` 时是 \\section）
p = make_docx(HEAD
              + ["1. 引言"] + FILLER[:4]
              + ["2. 哥德尔的数学认识论概念的三个层次"] + FILLER[4:9]
              + ["3.以非直接解读为基础的两例数学哲学研究进路"] + FILLER[9:14]
              + ["4. 莱布尼茨"] + FILLER[14:19]
              + ["5. 结语"] + FILLER[19:])
TMP.append(p)
d = parse_docx(p)
got = [h for _lv, h in d.headings]
check("端到端：5 个点式中文标题全部认成 \\section（层级 1）",
      got == ["引言", "哥德尔的数学认识论概念的三个层次", "以非直接解读为基础的两例数学哲学研究进路",
              "莱布尼茨", "结语"] and all(lv == 1 for lv, _h in d.headings), repr(got))
check("端到端：产物里确实是 \\section（不是留在正文里的编号行）",
      "\\section{莱布尼茨}" in d.body and "1. 莱布尼茨" not in d.body)

# ②-b：同篇还有 `一、` 一级编号 → `1.` 是子节（人工成品 03 Ziming_Song 就是这么排的）
p = make_docx(HEAD
              + ["一、引言"] + FILLER[:4]
              + ["二、经典决策论及其规范之源"] + FILLER[4:6]
              + ["1. 决策论与愿望/信念"] + FILLER[6:9]
              + ["2. 表征定理"] + FILLER[9:12]
              + ["三、结语"] + FILLER[12:])
TMP.append(p)
d = parse_docx(p)
lv = dict((h, l) for l, h in d.headings)
check("端到端：`一、` 在上时，`1. …` 排成 \\subsection（层级 2）",
      lv.get("决策论与愿望/信念") == 2 and lv.get("表征定理") == 2, repr(lv))
check("端到端：既有的一、式标题层级不受影响（仍是 \\section）",
      lv.get("引言") == 1 and lv.get("经典决策论及其规范之源") == 1)
check("端到端：\\subsection 行确实出现在产物里",
      "\\subsection{决策论与愿望/信念}" in d.body)

# ②-c 反例 1：连排名单（`N.` + 人名，但邻居也是同形行）→ 一个都不认。
#   ⚠️ 与 04 Bo_Chen 的区别就在**这里**：Bo_Chen 的 `1. 莱布尼茨`…`6. 艾耶尔`
#   每行后面都跟着 150–800 字的成段正文（见 ③ 的对账，人工成品 Bo_Chen.tex
#   把那 6 行排成 `\section`），所以是标题；连排、中间没有正文的才是名单。
p = make_docx(HEAD + FILLER[:6]
              + ["1. 莱布尼茨", "2. 休谟", "3. 康德"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：连排的 `N. 人名`（名单）→ 0 个标题，原样留在正文",
      d.headings == [] and "1. 莱布尼茨" in d.body and "\\section" not in d.body,
      repr([h for _l, h in d.headings]))

# ②-d 反例 2：整句列表项（带 。/，结尾）→ 不认
p = make_docx(HEAD + FILLER[:6]
              + ["1. 尚未形成对论证行动完整生成过程的统一解释。三条径路各有其代表性人物，",
                 "2. 第一页第二段，提出周延问题的文献建议列出原始文献，因为本页第三段提出的"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：整句编号列表项（带 。/，）→ 0 个标题", d.headings == [])

# ②-e 反例 3：文献清单行（`N. 作者，《书名》，…`）→ 不认
p = make_docx(HEAD + FILLER[:6]
              + ["1. 陶景侃，《大学逻辑教程》，兰州大学出版社，1993年8月，第一版。"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：文献清单行在正文里 → 0 个标题", d.headings == [])

# ②-f 反例 4：引文里的编号行。
#   本引擎的引文是**段内提升**（一个段落里的引文片段 → quotation 环境，见 render_para），
#   所以「引文里的编号行」只有两种形态：
#     (1) 编号行夹在段落中间 → 正则锚定段首（^），结构上不可能命中；
#     (2) 引文列表独占若干段落（引文常不加引号，直接连排）→ 必须由「连排」判据挡住。
QUOTE = ("有人把存在论要点概括如下：“存在即被感知。虚无不可言说。"
         "而这两句话本身又必须成立，否则整个论证就是空的，读者也无法判断它到底在说什么。”")
p = make_docx(HEAD + FILLER[:6]
              + [QUOTE]
              + ["1. 存在即被感知", "2. 虚无不可言说"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：引文列表独占段落（连排）→ 0 个标题", d.headings == [])
check("反例：那段引文确实被提升成了 quotation 环境（保证测试打到了引文这条路上）",
      "\\begin{quotation}" in d.body)
p = make_docx(HEAD + FILLER[:6]
              + ["他在引文里说“1. 存在即被感知，2. 虚无不可言说”，随后又反驳了这个说法。"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：编号行夹在段落中间（不在段首）→ 0 个标题", d.headings == [])

# ②-g 反例 5：`N.` 后面接整段正文（标题与正文写在同一段）→ 不认，不许截成标题
p = make_docx(HEAD + FILLER[:6]
              + ["1. 引言本文要讨论的是数据库逻辑的规范性问题，下面先交代背景，再给出论证，"
                 "最后是一个简短的结语。"]
              + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("反例：`1. 引言本文要讨论…`（标题后直接跟正文）→ 0 个标题", d.headings == [])

# ②-h 英文侧逐字不变：`1. Introduction` 仍认（原来就认），英文冒号标题仍不认（本轮不改）
p = make_docx(["A Logic of Databases", "Zhang San", "Abstract: we study databases.", "Keywords: logic"]
              + ["1. Introduction"] + FILLER[:6]
              + ["2. Formal Examination of Priest’s view of nothing:"] + FILLER[6:])
TMP.append(p)
d = parse_docx(p)
check("英文稿：`1. Introduction` 仍是一个 \\section（原有行为）",
      [h for _l, h in d.headings] == ["Introduction"], repr([h for _l, h in d.headings]))
check("英文稿：结尾带冒号的英文标题仍不认（本轮不碰英文判据，属已知漏认）",
      d.headings == [(1, "Introduction")] and "Formal Examination" in d.body)

for _p in TMP:
    os.unlink(_p)

# ══ ③ 真实语料 + 人工成品对账（语料不在本机则 SKIP）═══════════════════════
print("\n── ③ 真实语料与人工成品对账 ──")


def find(d: str, needle: str) -> str:
    import glob
    hits = [x for x in glob.glob(os.path.join(_REPO, d, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x) and zipfile.is_zipfile(x)]
    return hits[0] if hits else ""


def human_sections(tex: str) -> tuple[list[str], list[str]]:
    if not os.path.isfile(tex):
        return [], []
    txt = open(tex, encoding="utf-8", errors="replace").read()
    sec = re.findall(r"^\\section\{([^}]*)\}", txt, re.M)
    sub = re.findall(r"^\\subsection\{([^}]*)\}", txt, re.M)
    return sec, sub


# (目录, 文件名片段, 期望层级分布, 说明) —— 期望值全部来自该篇的人工成品 .tex
CASES = [
    ("2022年第5期(66)/04 Bo_Chen", "逻辑例外论",
     {"sections": ["莱布尼茨", "休谟", "康德", "弗雷格", "维特根斯坦", "艾耶尔"]},
     "04 Bo_Chen：6 个人名小节，人工成品 Bo_Chen.tex 全是 \\section"),
    ("2022年第4期(65)/02 Pengshu_Shan", "20160050580_O_editing",
     {"sections": ["哥德尔的数学认识论概念的三个层次", "以非直接解读为基础的两例数学哲学研究进路", "结语"]},
     "02 Pengshu_Shan：3 个点式标题（人工成品 shanpengshu.tex 同为 \\section）"),
    ("2023年第4期(71)/06 Lili_Lu", "20160050685_O_editing",
     {"sections": ["引言", "法律解释概述", "法律论证结构中的解释", "法律解释论证及其评价", "结论"]},
     "06 Lili_Lu：`1. 引言`…`6. 结论`（人工成品 Lili_Lu.tex 是 \\section，`2.1` 才是 \\subsection）"),
    ("2023年第5期(72)/03 Ziming_Song", "20160050777_O_editing",
     {"subsections": ["决策论与愿望/信念", "表征定理"]},
     "03 Ziming_Song：`一、` 在上 → `1.` 是 \\subsection（人工成品 Ziming_Song.tex 实证）"),
    ("2024年第1期(74)/07 Wenwen_Jiao", "202402文献标注",
     {"sections": ["引言", "弗雷格-吉奇问题与表达主义", "祈使句推理与约根森难题", "小结"]},
     "07 Wenwen_Jiao：`1.引言`（无空格）+ `3.弗雷格-…`"),
    ("2026年第1期(86)/07 Long_Chen", "返修版（无修订标记）",
     {"sections": ["引言", "不同形式的逻辑多元论", "反驳多元论的多重理由"]},
     "07 Long_Chen：自定义样式 `1` 的 4 个标题里认出 3 个"),
    ("2026年第2期(87)/07 Guangwu_Feng", "格赖斯和布兰顿",
     {"sections": ["引言", "格赖斯的语用推理", "布兰顿的语义推理", "共性与差异", "问题与挑战", "余论"]},
     "07 Guangwu_Feng：6 个标题全认（点后有空格/无空格混用）"),
    ("2026年第2期(87)/08 Ran_Ren", "陈那派与正理派",
     {"sections": ["从康德哲学的角度看陈那派知识论的特征", "从比较逻辑视角看印度逻辑的特异性", "结论"]},
     "08 Ran_Ren：3 个标题认出来，因明五支那 9 条列表项一条都不许进来"),
    ("2026年第2期(87)/09 Runcheng_Liang", "梁润成+最终",
     {"sections": ["引言", "约翰逊和汉比的标准", "新标准", "新标准的应用", "结论"]},
     "09 Runcheng_Liang：`1.`–`5.` 认出来（`2.1` 等本就由 _HEAD_EN3 认）"),
]
for _dir, needle, want, why in CASES:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    secs = [h for lv, h in rd.headings if lv == 1]
    subs = [h for lv, h in rd.headings if lv == 2]
    ok = all(w in secs for w in want.get("sections", [])) \
        and all(w in subs for w in want.get("subsections", []))
    check(f"{why}  [{os.path.basename(path)[:26]}]", ok,
          f"L1={secs[:6]} L2={subs[:4]}")

# ③-b 与人工成品逐条对账：新认出的标题必须**都在**人工成品的章节表里
PAIRS = [
    ("2022年第5期(66)/04 Bo_Chen", "逻辑例外论", "Bo_Chen.tex"),
    ("2022年第4期(65)/02 Pengshu_Shan", "20160050580_O_editing", "shanpengshu.tex"),
    ("2023年第4期(71)/06 Lili_Lu", "20160050685_O_editing", "Lili_Lu.tex"),
    ("2023年第5期(72)/03 Ziming_Song", "20160050777_O_editing", "Ziming_Song.tex"),
    ("2024年第1期(74)/07 Wenwen_Jiao", "202402文献标注", "Wenwen_Jiao.tex"),
    ("2026年第1期(86)/07 Long_Chen", "返修版（无修订标记）", "Long_Chen.tex"),
    ("2026年第2期(87)/07 Guangwu_Feng", "格赖斯和布兰顿", "Guangwu_Feng.tex"),
    ("2026年第2期(87)/08 Ran_Ren", "陈那派与正理派", "Ran_Ren.tex"),
    ("2026年第2期(87)/09 Runcheng_Liang", "梁润成+最终", "Runcheng_Liang.tex"),
]
for _dir, needle, texname in PAIRS:
    path = find(_dir, needle)
    tex = os.path.join(_REPO, _dir, texname)
    if not path or not os.path.isfile(tex):
        print(f"SKIP  {_dir}（语料或人工成品不在本机）")
        continue
    rd = parse_docx(path)
    sec, sub = human_sections(tex)
    known = {re.sub(r"\\cndash\s*", "-", x).strip() for x in sec + sub}
    new = [h for _lv, h in rd.headings]
    miss = [h for h in new if h and h not in known
            and h.replace("—", "-") not in {k.replace("—", "-") for k in known}
            and h.replace("-", "\\cndash ") not in known
            and h.replace("-", "\\cndash") not in known]
    check(f"人工成品对账：{texname} 里每个新标题都有人工对应", not miss, f"对不上的：{miss}")

# ③-c 反例：这些稿子的点式编号行**一条都不许**变成标题（全是列表项/英文稿，本轮不该动）
NEG = [
    ("2023年第4期(71)/04 Yingying_Wang", "20160050760_A_editing",
     "审稿意见逐条回复（`1、`/`2、` + 整句）"),
    ("2025年第6期(85)/03 QingHui_Su", "Two Recent Approaches",
     "03 QingHui_Su：`3.3.1 Problems…` 深编号，属已知漏认（英文判据不动）"),
    ("2024年第6期(79)/01 Wenfang_Wang", "A Careful inspection",
     "01 Wenfang_Wang：英文标题结尾带冒号，仍不认（英文判据不动）"),
]
for _dir, needle, why in NEG:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    bodies = [h for _lv, h in rd.headings]
    unwanted = [h for h in bodies if "审稿" in h or "Problems for" in h
                or "Philosophical reflections" in h]
    check(f"反例不动：{why}", not unwanted, f"混进来了：{unwanted}")

print()
if FAILS:
    print(f"FAILED  {len(FAILS)} 项：")
    for f in FAILS:
        print("   -", f)
    raise SystemExit(1)
print("全部 PASS")
