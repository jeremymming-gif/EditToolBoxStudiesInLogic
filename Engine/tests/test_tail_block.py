#!/usr/bin/env python3.14
"""文末英文元数据块定位（item 5）的判据测试。

三类断言：
  ① 定位判据（合成段落列表，毫秒级）：原有「参考文献之后」路径不变；没有
     「参考文献」标题时的文末兜底；标签必须带冒号或独占一段；只认文末 30 段；
     中文标签不算；回溯遇到汉字/空行/超长行即止。
  ② 现造的最小 docx（临时 zip，只含 word/document.xml[+footnotes.xml]）——证明
     脚注里的 `Abstract:` 结构性不可达、中段英文摘要不会被当文末块（宁 fail 不误判）；
     并端到端验证「没有参考文献标题的文末英文块」能填进 title_en/abstract_en。
  ③ 真实语料（语料不在本机则 SKIP）：6 篇修复稿的取值 + 4 类反例不动。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_tail_block.py
"""
import os
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
    META_SCAN, _TAIL_BLOCK_SCAN, _label_body, _ABS_RE, _KW_RE,
    locate_tail_block, parse_docx, parse_tail_metadata,
)
from slcn.docxxml import Table, read_blocks  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ══ ① 定位判据（合成段落列表）═════════════════════════════════════════════
print("── ① 定位判据 ──")

# 原有路径：`参考文献` 之后的第一处英文 Abstract，再向上带上题名（行为不得变）
OLD = (["中文题名", "作者", "摘要：本文讨论数据库。", "关键词：逻辑；数据库"]
       + ["正文第 %d 段。" % i for i in range(30)]
       + ["参考文献", "[1] 张三. 数据库逻辑[J]. 逻辑学研究, 2020(1): 1-10."]
       + ["Towards a Logic of Databases", "Zhang San",
          "Abstract: We study the logic of databases.", "Key words: logic; database"])
check("原有路径：块首回溯到英文题名（下标 36）", locate_tail_block(OLD, 34) == 36,
      f"got {locate_tail_block(OLD, 34)}")
check("原有路径：解析出题名/摘要/关键词/作者",
      (lambda t: (t["title_en"], t["author_en"], t["abstract_en"], t["keywords_en"]))(
          parse_tail_metadata(OLD, locate_tail_block(OLD, 34)))
      == ("Towards a Logic of Databases", "Zhang San",
          "We study the logic of databases.", "logic; database"))

# 原有路径优先：开头已有英文块时，不得改用文末那块
BOTH = (["中文题名", "作者", "参考文献", "[1] 文献"]
        + ["Early English Title", "Abstract: early body", "Keywords: early kw"]
        + ["正文第 %d 段。" % i for i in range(40)]
        + ["Late English Title", "Abstract: late body", "Keywords: late kw"])
check("原有路径优先于兜底（取参考文献之后那块，下标 4）",
      locate_tail_block(BOTH, 2) == 4, f"got {locate_tail_block(BOTH, 2)}")

# 兜底路径：没有「参考文献」标题，英文块在文末
TAIL = (["中文题名", "作者", "摘要：本文讨论数据库。", "关键词：逻辑；数据库"]
        + ["正文第 %d 段。" % i for i in range(40)]
        + ["Towards a Logic of Databases", "Zhang San",
           "Abstract: We study the logic of databases.", "Key words: logic; database"])
check("兜底：没有参考文献标题也能定位（下标 44）", locate_tail_block(TAIL, -1) == 44,
      f"got {locate_tail_block(TAIL, -1)}")
check("兜底：解析结果与原有路径同一套判据",
      parse_tail_metadata(TAIL, locate_tail_block(TAIL, -1))["title_en"]
      == "Towards a Logic of Databases")
check("英文块排在「参考文献」标题之前（01 Lili_Lu 的形态）也能定位",
      locate_tail_block(["中文题名", "作者"] + ["正文"] * 30
                        + ["On the Legal Argument", "Abstract: body", "Keywords: kw",
                           "参考文献", "[1] 文献"], 34) == 32)

# 标签判据：带冒号或独占一段
def label_doc(label: str) -> list[str]:
    """文末第 3 段换成 `label` 的多段列表（n = 40，窗口从第 10 段起）。"""
    plains = ["中文题名", "作者"] + ["正文第 %d 段。" % i for i in range(38)]
    plains[-2] = "An English Title"
    plains[-1] = label
    return plains


check("标签 `Abstract` 独占一段 → 命中", locate_tail_block(label_doc("Abstract"), -1) == 38)
check("标签 `Abstract:` 独占一段 → 命中", locate_tail_block(label_doc("Abstract:"), -1) == 38)
check("标签 `Abstract: 正文` → 命中",
      locate_tail_block(label_doc("Abstract: We study databases."), -1) == 38)
check("标签 `Key words: a; b` → 命中",
      locate_tail_block(label_doc("Key words: logic; database"), -1) == 38)
check("标签 `Key words：a；b`（全角冒号）→ 命中",
      locate_tail_block(label_doc("Key words：logic；database"), -1) == 38)
# 反例：文献条目/正文里以 Abstract(s) 开头的行
check("`Abstracts of the Fifth Logic Conference, 2019.`（文献条目）→ 不命中",
      locate_tail_block(label_doc("Abstracts of the Fifth Logic Conference, 2019."), -1) == -1)
check("`Abstract art in Chinese philosophy`（正文行）→ 不命中",
      locate_tail_block(label_doc("Abstract art in Chinese philosophy"), -1) == -1)
check("`Keyword extraction: a survey`（正文行）→ 不命中",
      locate_tail_block(label_doc("Keyword extraction: a survey"), -1) == -1)

# 反例：中文标签（中文那一套归开头那套管，不走英文块）
check("中文 `摘要：…` 在文末 → 兜底不命中",
      locate_tail_block(label_doc("摘要：本文讨论数据库逻辑。"), -1) == -1)
check("中文 `关键词：…` 在文末 → 兜底不命中",
      locate_tail_block(label_doc("关键词：逻辑；数据库"), -1) == -1)

# 窗口：只认文末 _TAIL_BLOCK_SCAN 段
def window_doc(label_at: int, n: int = 40) -> list[str]:
    plains = ["中文题名", "作者"] + ["正文第 %d 段。" % i for i in range(n - 2)]
    plains[label_at - 1] = "An English Title"
    plains[label_at] = "Abstract: body here"
    return plains


check(f"标签正好在文末第 {_TAIL_BLOCK_SCAN} 段 → 命中",
      locate_tail_block(window_doc(40 - _TAIL_BLOCK_SCAN), -1) == 40 - _TAIL_BLOCK_SCAN - 1)
check(f"标签在文末第 {_TAIL_BLOCK_SCAN + 1} 段（窗口外）→ 不命中",
      locate_tail_block(window_doc(40 - _TAIL_BLOCK_SCAN - 1), -1) == -1)

# 回溯边界：空行 / 汉字 / 超长行都阻断
GAP = label_doc("Abstract: body")
GAP[-2] = ""
check("块首上一段是空行 → 只从标签本身起（不吞更上面的行）",
      locate_tail_block(GAP, -1) == 39)
LONG = label_doc("Abstract: body")
LONG[-2] = "A" * 161
check("块首上一段超过 160 字 → 不吞", locate_tail_block(LONG, -1) == 39)
check("没有英文标签时返回 -1",
      locate_tail_block(["中文题名", "作者"] + ["正文"] * 40, -1) == -1)

# ══ ② 标签正文 + 文末块解析（合成）════════════════════════════════════════
print("\n── ② 标签正文与文末块解析 ──")

BARE = ["Frege-Geach Problem and Practical Reasoning", "Wenwen Jiao", "Abstract",
        "Non-cognitivist explanations face the Frege-Geach problem."]
check("标签独占一段 → 取下一段正文",
      _label_body(BARE, 2, _ABS_RE) == "Non-cognitivist explanations face the Frege-Geach problem.")
check("同一段有正文 → 取冒号后的内容",
      _label_body(["摘要：本文讨论逻辑。"], 0, _ABS_RE) == "本文讨论逻辑。")
check("段首 BOM（U+FEFF）不是正文",
      _label_body(["Abstract", "\ufeffUnder the framework of generalized argumentation theory"], 0, _ABS_RE)
      == "Under the framework of generalized argumentation theory")
check("下一段是另一个标签 → 不取（宁可空）",
      _label_body(["Abstract", "Keywords: a; b"], 0, _ABS_RE) == "")
# 反例：中文标签后面跟英文段，是「中英两套」的另一套，不能写进中文摘要
check("中文标签 `摘要：` 后跟英文段 → 不填（避免英文进 \\myabstractCN）",
      _label_body(["摘要：", "Fallacies are a crucial component in the study of argumentation"], 0, _ABS_RE) == "")
check("中文标签 `摘要` 后跟中文段 → 也不填（宁 fail 不误判，中文那套归开头扫描）",
      _label_body(["摘要", "本文讨论逻辑。"], 0, _ABS_RE) == "")
check("标签带正文时不受语言影响",
      _label_body(["摘要：本文讨论逻辑。"], 0, _ABS_RE) == "本文讨论逻辑。")

# ② 英文题名：括号单位行不能当题名（05 Guoping_Du 的形态）
GPD = ["The Law of Excluded Middle as an Analytical Tool", "Du Guo-ping",
       "(Institute of Philosophy, Chinese Academy of Social Sciences, Beijing 100732, China)",
       "Abstract: The law of excluded middle is one of the three fundamental laws of logic.",
       "Key words: the law of excluded middle; possibility; false dilemma"]
_t = parse_tail_metadata(GPD, 0)
check("作者行下面的括号单位行不被当成英文题名",
      _t["title_en"] == "The Law of Excluded Middle as an Analytical Tool", repr(_t["title_en"]))
check("括号单位行上面的那行才像人名 → author_en",
      _t["author_en"] == "Du Guo-ping", repr(_t.get("author_en")))
check("摘要/关键词取值不受影响",
      _t["abstract_en"].startswith("The law of excluded middle")
      and _t["keywords_en"].startswith("the law of excluded middle"))

# ② 单位：`联系电话/通信地址/邮政编码` 不得混进 affiliation（05 Guoping_Du 的形态）
BRIEF = ["The Law of Excluded Middle as an Analytical Tool", "Du Guo-ping",
         "Abstract: body", "Key words: kw",
         "作者简介：", "作者：杜国平，1965年生。", "学位：博士", "职称：二级教授",
         "研究方向：现代逻辑及其应用",
         "工作单位：中国社会科学院哲学研究所、中国社会科学院大学哲学院",
         "联系电话：18901393186", "电子邮箱：dgpnju@163.com",
         "通信地址：北京市通州区北苑广通小区7号楼1单元502室", "邮政编码：101100"]
_b = parse_tail_metadata(BRIEF, 0)
check("电话/地址/邮编不混进 affiliation_cn",
      "18901393186" not in (_b.get("affiliation_cn") or "")
      and "通信地址" not in (_b.get("affiliation_cn") or "")
      and "邮政编码" not in (_b.get("affiliation_cn") or ""), repr(_b.get("affiliation_cn")))
check("带标签的邮箱仍能取到", _b.get("email") == "dgpnju@163.com", repr(_b.get("email")))

# ② 但「中英文所属单位」那种正常单位块必须照旧（09 Jiawei_Chu 的形态）
JC = ["How Are Argumentative Actions Generated?", "Jiawei Chu", "Abstract: body",
      "Keywords: kw", "邮箱：chujw3@mail2.sysu.edu.cn", "中英文所属单位: ",
      "中山大学逻辑与认知研究所", "中山大学哲学系",
      "Institute of Logic and Cognition, Sun Yat-sen University ",
      "Department of Philosophy, Sun Yat-sen University"]
_j = parse_tail_metadata(JC, 0)
check("中英文单位块：中文单位照旧",
      _j["affiliation_cn"] == "中山大学逻辑与认知研究所\\\\ 中山大学哲学系", repr(_j["affiliation_cn"]))
check("中英文单位块：英文单位照旧（作者名 phantom 缩进）",
      _j["affiliation_en"].startswith("Institute of Logic and Cognition, Sun Yat-sen University")
      and "Department of Philosophy" in _j["affiliation_en"], repr(_j["affiliation_en"][:60]))
check("中英文单位块：邮箱照旧", _j["email"] == "chujw3@mail2.sysu.edu.cn")

# ══ ③ 现造的最小 docx ═════════════════════════════════════════════════════
print("\n── ③ 最小 docx（脚注/中段/文末块）──")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paras, footnote_texts=()):
    """只含 word/document.xml（可选 footnotes.xml）的最小 docx。"""
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fns = ""
    if footnote_texts:
        fns = "".join(
            f'<w:footnote w:id="{i + 1}"><w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p></w:footnote>'
            for i, t in enumerate(footnote_texts))
        fns = f'<w:footnotes xmlns:w="{W_NS}">{fns}</w:footnotes>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
        if fns:
            z.writestr("word/footnotes.xml", fns)
    return path


CN_HEAD = ["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：逻辑；数据库"]
TMP = []

# ③-a 没有「参考文献」标题的文末英文块 → 端到端填上
p = make_docx(CN_HEAD + ["正文第 %d 段。" % i for i in range(40)]
              + ["Towards a Logic of Databases", "Zhang San",
                 "Abstract: We study the logic of databases.", "Key words: logic; database"])
TMP.append(p)
d = parse_docx(p)
check("端到端：无参考文献标题的文末英文块 → title_en",
      d.title_en == "Towards a Logic of Databases", repr(d.title_en))
check("端到端：→ abstract_en / keywords_en / author_en",
      d.abstract_en == "We study the logic of databases."
      and d.keywords_en == "logic; database" and d.author_en == "Zhang San",
      f"{d.abstract_en!r} / {d.keywords_en!r} / {d.author_en!r}")
check("端到端：中文那套没被改动",
      d.abstract_cn == "本文讨论数据库逻辑。" and d.keywords_cn == "逻辑；数据库",
      f"{d.abstract_cn!r} / {d.keywords_cn!r}")

# ③-b 文末是中文元数据块 → 兜底不命中（英文侧留空，不许拿中文冒充）
p = make_docx(CN_HEAD + ["正文第 %d 段。" % i for i in range(40)]
              + ["论数据库逻辑的哲学基础", "张三", "摘要：文末这一套也是中文。", "关键词：数据库；哲学"])
TMP.append(p)
d = parse_docx(p)
check("端到端：文末中文块不写成英文元数据",
      d.title_en == "" and d.abstract_en == "" and d.keywords_en == "",
      f"{d.title_en!r} / {d.abstract_en!r} / {d.keywords_en!r}")
check("端到端：文末中文块也没覆盖开头的中文摘要/关键词",
      d.abstract_cn == "本文讨论数据库逻辑。" and d.keywords_cn == "逻辑；数据库")

# ③-c 脚注里的 `Abstract:` 结构性不可达
p = make_docx(CN_HEAD + ["正文第 %d 段。" % i for i in range(40)],
              footnote_texts=["Abstract: this footnote must not become the abstract.",
                              "Key words: footnote, not metadata"])
TMP.append(p)
d = parse_docx(p)
check("端到端：脚注里的英文 Abstract 不进 abstract_en",
      d.abstract_en == "", repr(d.abstract_en))
check("端到端：脚注也没被当成文末块（title_en/keywords_en 空）",
      d.title_en == "" and d.keywords_en == "")

# ③-d 英文摘要在**中段**（第 25 段，META_SCAN 之外、文末窗口之外）→ 两条路都不取
MID = (CN_HEAD + ["正文第 %d 段。" % i for i in range(20)]
       + ["An English Abstract in the Middle", "Abstract: mid body", "Keywords: mid kw"]
       + ["正文第 %d 段。" % i for i in range(META_SCAN + 20, META_SCAN + 60)])
p = make_docx(MID)
TMP.append(p)
d = parse_docx(p)
check("端到端：中段英文摘要不填 abstract_en（头上 20 段与文末 30 段之间的不取）",
      d.abstract_en == "" and d.title_en == "", f"{d.abstract_en!r} / {d.title_en!r}")

# ③-e 英文摘要正好落在开头元数据区（第 19 段，META_SCAN 之内、文末窗口之外）
HEADZONE = (CN_HEAD + ["正文第 %d 段。" % i for i in range(14)]
            + ["Head-zone English Title", "Abstract: head-zone body", "Keywords: head-zone kw"]
            + ["正文第 %d 段。" % i for i in range(45)])
p = make_docx(HEADZONE)
TMP.append(p)
d = parse_docx(p)
check("端到端：开头元数据区内的英文摘要归开头那套（abstract_en 取到）",
      d.abstract_en == "head-zone body", repr(d.abstract_en))
for _p in TMP:
    os.unlink(_p)

# ══ ④ 真实语料（不在本机则 SKIP）═════════════════════════════════════════
print("\n── ④ 真实语料 ──")

import glob  # noqa: E402


def find(d: str, needle: str) -> str:
    """在语料目录下按文件名片段找稿子；跳过 Word 的 `~$` 锁文件/坏 zip。"""
    hits = [x for x in glob.glob(os.path.join(_REPO, d, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x) and zipfile.is_zipfile(x)]
    return hits[0] if hits else ""


def tail_of(path: str) -> int:
    from slcn.docxsource import _REF_HEADS, _has_cjk
    plains = [b.plain for b in read_blocks(path) if not isinstance(b, Table)]
    ref = next((i for i, t in enumerate(plains)
                if t.strip().lower().rstrip(":：") in _REF_HEADS), -1)
    return locate_tail_block(plains, ref)


# (语料目录, 文件名片段, 断言函数, 说明)
CASES = [
    ("2022年第5期(66)/04 Bo_Chen", "逻辑例外论",
     lambda d, p: d.title_en == "The Development of Logical Exceptionalism"
     and d.abstract_en.startswith("The main viewpoints of logical exceptionalism")
     and d.author_en == "CHEN Bo" and tail_of(p) == 50,
     "文末英文块 + 全文没有「参考文献」标题"),
    ("2023年第1期(68)/02 Liwu_Rong", "20160050669_O_editing",
     lambda d, p: d.title_en == "Conversational Hypothesis on Indicative Conditionals"
     and d.abstract_en.startswith("There is a common idea in contemporary logicians"),
     "文末英文块 + 没有「参考文献」标题"),
    ("2023年第4期(71)/06 Lili_Lu", "20160050685_O_editing",
     lambda d, p: d.title_en == "On the Legal Argument in Legal Interpretation"
     and tail_of(p) == 71,
     "英文块排在「参考文献」标题**之前**"),
    ("2024年第4期(77)/05 Guoping_Du", "杜国平",
     lambda d, p: d.title_en == "The Law of Excluded Middle as an Analytical Tool"
     and d.author_en == "Du Guo-ping"
     and d.affiliation_en.startswith("Institute of Philosophy, Chinese Academy of Social Sciences")
     and d.affiliation_cn == "中国社会科学院大学哲学院\\\\ 中国社会科学院哲学研究所"
     and d.email == "dgpnju@163.com",
     "文末英文块 + 块后中文「作者简介」（电话/地址/邮编不得进单位）"),
    ("2024年第6期(79)/06 Yuyu_Liu", "Mengzi on Rectification",
     lambda d, p: tail_of(p) == -1
     and d.abstract_en.startswith("Set in the context of pre-Qin period"),
     "英文块在开头（标签独占一段）+ 文末是英文文献表（不得当文末块）"),
    ("2025年第6期(85)/04 YingJin_Xu", "Time-budget-sensitive",
     lambda d, p: d.abstract_en.startswith("An idealized artificial general intelligence")
     and d.keywords_en.startswith("Need-for-Closure"),
     "开头英文块标签独占一段（Abstract / Keywords:）"),
    ("2026年第2期(87)/07 Guangwu_Feng", "格赖斯和布兰顿",
     lambda d, p: d.title_en == "Grice and Brandom on Verbal Communication"
     and d.abstract_en.startswith("This article compares Grice"),
     "开头英文块 + 英文题名前有一个只有空格的段"),
]
for _dir, needle, ok, why in CASES:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    try:
        good = bool(ok(rd, path))
    except Exception as e:                      # 断言里的属性缺失也算失败
        good, why = False, f"{why} [{type(e).__name__}: {e}]"
    check(f"{why}  [{os.path.basename(path)[:28]}]", good)

# 反例：这些稿子必须一动不动（定位不变 / 不拿错误段落冒充）
NEG = [
    ("2025年第3期(82)/02 Zhiguang_Zhao", "New_Version_0",
     lambda d, p: tail_of(p) == -1
     and d.abstract_en.startswith(". In the present paper"),   # 开头那套取到的英文摘要
     "n=936：英文摘要在第 3 段、参考文献在第 908 段 → 兜底不命中"),
    ("2024年第6期(79)/01 Wenfang_Wang", "A Careful inspection",
     lambda d, p: tail_of(p) == -1 and "普里斯特" not in (d.abstract_en or ""),
     "文末是**中文**元数据块 → 兜底只认英文标签，不命中"),
    ("2023年第6期(73)/05 Yang_Hu", "revised version",
     lambda d, p: tail_of(p) == -1,
     "英文摘要@1（开头）、参考文献@118 → 原有路径不变"),
    ("2025年第6期(85)/04 YingJin_Xu", "汉语摘要",
     lambda d, p: tail_of(p) == -1 and not (d.abstract_en or ""),
     "纯中文摘要片段（无英文块）→ 不命中"),
    ("2026年第1期(86)/08 LingXin_Cai", "补充材料 20260117",
     lambda d, p: "Fallacies are a crucial component" not in (d.abstract_cn or ""),
     "中文 `摘要：` 独占一段、下一段是英文 → 不许写进 abstract_cn"),
    ("2026年第5期(90)/10 Bo_Chen", "当代逻辑哲学鸟瞰英文摘要",
     lambda d, p: d.title_en == "A Survey of Contemporary Philosophy of Logic"
     and d.abstract_en.startswith("Three research models")
     and d.author_en == "Chen Bo",
     "中英两套摘要的英文片段（文末窗口内、开头那套也已取到）不得被覆盖"),
]
for _dir, needle, ok, why in NEG:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    try:
        good = bool(ok(rd, path))
    except Exception as e:
        good, why = False, f"{why} [{type(e).__name__}: {e}]"
    check(f"反例：{why}  [{os.path.basename(path)[:26]}]", good)

print(f"\n{'=' * 60}\n失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
