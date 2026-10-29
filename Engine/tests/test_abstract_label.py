#!/usr/bin/env python3.14
"""「摘要」标签形态放宽（task-7 第 3 项）的判据测试。

四类断言：
  ① 取值判据（合成段落列表，毫秒级）：段首空白（半角 / 全角 U+3000）照认；标签独占
     一段时向下取正文（跳空段、最多 3 段、最多往后看 6 段）；撞上关键词 / 下一个
     标签 / 节标题 / 英文段就收手；「摘要」当构词语素时不是标签；句中 `Abstract`
     不是标签；无分隔符的粘连标签（`摘要理想的人工…`）仍照认。
  ② 现造的最小 docx（临时 zip）端到端跑 `parse_docx`——四条反例 + 三条正例：
     (i)   正文段以「摘要式阅读」开头（在 META_SCAN 窗口**内**，不是靠位置侥幸过关）
     (ii)  `Abstract` 出现在句子中间（含段首缩进的句子）
     (iii) 缩进 / 独占的 `摘要：` 其实是**英文块**的标签 → 不得抢中文摘要槽
     (iv)  标签独占一段 + 紧跟关键词行 → 不得把关键词吞成摘要正文
     正例：同段（含尾随空白，逐字节不变）/ 段首缩进 / 标签独占一段 / 文末中文摘要。
  ③ 与旧规的一致性：把**改动前**的取值规则（`_label_body` 版）在测试里冻结成
     `old_abstract_cn()`，对真实语料逐篇断言「旧规非空 ⇒ 新取值逐字节相同」——
     这是「68 篇 ok 不许动一个字节」的可执行证据。
  ④ 真实语料（语料不在本机则 SKIP）：8 篇修复稿断言摘要正文；6 篇反例断言
     一动不动（含 2 篇**已知限制**，见各自说明）。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_abstract_label.py
"""
import glob
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
    META_SCAN, _ABS_LOOKAHEAD, _ABS_MIN_CHARS, _ABS_RE, _KW_RE, _META_LABEL_RE,
    _abs_body_cn, _has_cjk, _label_body, _looks_like_abs_prose, _tail_abstract_cn,
    parse_docx,
)
from slcn.docxxml import Table, read_blocks  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ══ ① 取值判据（合成段落列表）═════════════════════════════════════════════
print("── ① 取值判据 ──")

# 长句（> _ABS_MIN_CHARS）当摘要正文，短句用来试「不像正文」的判据
ABS_CN = "本文讨论数据库逻辑与数据库理论之间的关系，并给出一个形式刻画。"
HIT = "直觉主义认知逻辑IEL不仅为邱奇-费奇悖论提供了解悖思路。"

# ①-a 标签与正文同段：取值必须与 _label_body **逐字节**一致（含尾随空白）
SAME = ["论数据库逻辑", "张三", "摘要：本文讨论逻辑。  ", "关键词：逻辑；数据库"]
check("同一段：取冒号后的内容，且与 _label_body 逐字节相同",
      _abs_body_cn(SAME, 2) == _label_body(SAME, 2, _ABS_RE) == "本文讨论逻辑。  ",
      repr(_abs_body_cn(SAME, 2)))
check("同一段：全文稿标签（`摘 要：…`）不受影响",
      _abs_body_cn(["摘 要：本文讨论逻辑。"], 0) == "本文讨论逻辑。")

# ①-b 段首空白（这是本轮放宽的形态）
check("段首四个半角空格照认",
      _abs_body_cn(["    摘要：本文讨论逻辑。"], 0) == "本文讨论逻辑。")
check("段首全角空格（U+3000）照认",
      _abs_body_cn(["\u3000\u3000摘要：本文讨论逻辑。"], 0) == "本文讨论逻辑。")
check("段首空白 + 标签独占一段 → 向下取",
      _abs_body_cn(["  摘要", ABS_CN], 0) == ABS_CN)
check("英文标签（`   Abstract`）转交旧路径，行为不变",
      _abs_body_cn(["   Abstract: We study databases."], 0) == "We study databases.")

# ①-c 标签独占一段 → 向下取正文
check("`摘 要 `（字间空格）独占一段 → 向下取",
      _abs_body_cn(["摘 要 ", HIT], 0) == HIT)
check("`摘    要`（多空格）/ `摘  要` / `摘要：` 独占一段 → 都向下取",
      _abs_body_cn(["摘    要", HIT], 0) == HIT
      and _abs_body_cn(["摘  要", HIT], 0) == HIT
      and _abs_body_cn(["摘要：", HIT], 0) == HIT)
check("空段跳过（不占额度）",
      _abs_body_cn(["摘要", "", "  ", "\u3000", ABS_CN], 0) == ABS_CN)
check(f"最多取 {_ABS_LOOKAHEAD} 段（第 4 段不吞）",
      _abs_body_cn(["摘要"] + [f"第{i}段讨论数据库逻辑与数据库理论之间的关系。" for i in range(1, 6)], 0)
      == " ".join(f"第{i}段讨论数据库逻辑与数据库理论之间的关系。" for i in range(1, 4)))
check("最多往后看 6 段（空段撑开的更远段落不看）",
      _abs_body_cn(["摘要"] + [""] * 6 + [ABS_CN], 0) == "")
check("后面什么都没有 → 空（绝不凭空造正文）", _abs_body_cn(["摘要"], 0) == "")

# ①-d 收手判据：撞上下一个标签 / 不像正文 / 不同语言
check("撞上关键词标签就收手（反例 iv 的判据层）",
      _abs_body_cn(["摘要", "关键词：逻辑；数据库"], 0) == "")
check("撞上缩进的关键词标签也收手",
      _abs_body_cn(["摘要", "    关键词：逻辑；数据库"], 0) == "")
check("撞上下一个「摘要」标签就收手",
      _abs_body_cn(["摘要", "摘要：本文讨论逻辑。"], 0) == "")
check("撞上题名 / 作者 / 单位 / 邮箱标签就收手",
      all(_abs_body_cn(["摘要", lab], 0) == ""
          for lab in ("标题：论数据库逻辑", "作者：张三", "单位：中山大学哲学系",
                      "邮箱：a@b.edu.cn", "中图分类号：NO31")))
check("节标题不是正文 → 不取（宁 fail 不误判）",
      _abs_body_cn(["摘要", "一、引言"], 0) == ""
      and _abs_body_cn(["摘要", "1. Introduction"], 0) == ""
      and _abs_body_cn(["摘要", "（一）广义论证的语用基础"], 0) == "")
check("下一段是英文正文 → 不取（同语言判据，反例 iii 的判据层）",
      _abs_body_cn(["摘要", "This paper discusses the logic of databases."], 0) == ""
      and _abs_body_cn(["摘要：", "Fallacies are a crucial component in argumentation."], 0) == "")
check("太短的段不算正文（< _ABS_MIN_CHARS）",
      not _looks_like_abs_prose("本文讨论逻辑。") and _abs_body_cn(["摘要", "本文讨论逻辑。"], 0) == "")

# ①-e 形态反例：「摘要」当构词语素 / 句中 Abstract
check("反例 i（判据层）：`摘要式阅读…` 不是标签",
      _ABS_RE.match("摘要式阅读是本文讨论的主题。") is None
      and _abs_body_cn(["摘要式阅读是本文讨论的主题。"], 0) == "")
check("反例 i（判据层）：`摘要性`/`摘要化` 同理",
      _ABS_RE.match("摘要性分析") is None and _ABS_RE.match("摘要化学方法") is None)
check("粘连标签仍照认（04 Chen_Xu 的 `摘要理想的人工…`）",
      _abs_body_cn(["摘要理想的人工通用智能系统应具备足够的智能。"], 0)
      == "理想的人工通用智能系统应具备足够的智能。")
check("反例 ii（判据层）：句中 `Abstract` 不是标签",
      _ABS_RE.match("We discuss the Abstract and Keywords sections.") is None
      and _ABS_RE.match("   We also discuss the Abstract in passing.") is None)
check("标签必须仍然在段首：`本文的摘要：…` 不命中",
      _ABS_RE.match("本文的摘要：讨论数据库。") is None)
check("_META_LABEL_RE 只认标签行（不是标签的正文行不误判）",
      bool(_META_LABEL_RE.match("标题：论数据库逻辑"))
      and not _META_LABEL_RE.match("标题党现象研究"))

# ①-f 文末中文摘要（第二处位置）
TAIL = (["中文题名", "作者"] + [f"正文第{i}段。" for i in range(20)]
        + ["参考文献", "[1] 张三. 数据库逻辑[J]. 2020."]
        + ["论数据库逻辑", "张三", "摘  要", ABS_CN, "关键词：逻辑；数据库"])
check("文末区（参考文献之后）的中文摘要能取到",
      _tail_abstract_cn(TAIL, 22) == ABS_CN)
check("没有参考文献区时只在文末 30 段里找",
      _tail_abstract_cn(["论文题名", "作者", "摘要", ABS_CN], -1) == ABS_CN
      and _tail_abstract_cn(["摘要", ABS_CN] + ["正文。"] * 40, -1) == "")
check("文末的纯英文块不会被当中文摘要（语言判据）",
      _tail_abstract_cn(["Title", "Author", "Abstract:", "We study databases.",
                         "Keywords: logic"], -1) == "")
check("开头段里的中文摘要不是它的活（只扫文末区）",
      _tail_abstract_cn(["论文题名", "作者", "摘要：" + ABS_CN] + ["正文。"] * 40, 45) == "")

# ══ ② 现造的最小 docx（端到端）═══════════════════════════════════════════
print("\n── ② 最小 docx（反例 i–iv + 正例）──")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paras):
    """只含 word/document.xml 的最小 docx。"""
    ps = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(t)}</w:t></w:r></w:p>"
                 for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


TMP = []
BODY = [f"正文第{i}段，讨论数据库逻辑与数据库理论之间的关系。" for i in range(40)]

# ②-反例 i：正文段以「摘要式阅读」开头——而且落在 META_SCAN 窗口**内**（第 3 段）
p = make_docx(["论数据库逻辑", "张三",
               "摘要式阅读是本文讨论的主题，数据库逻辑的教科书往往这样开头。",
               "摘要性分析与摘要化学方法也不是标签。"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 i：正文段以「摘要式阅读」开头（第 3 段，窗口内）→ abstract_cn 必须为空",
      d.abstract_cn == "", repr(d.abstract_cn))

# ②-反例 ii：句中 `Abstract`
p = make_docx(["On the Logic of Databases", "Zhang San",
               "We discuss the Abstract and Keywords sections of a paper.",
               "   We also mention the Abstract in passing."] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 ii：句中 `Abstract`（第 3/4 段，窗口内）→ abstract_en 必须为空",
      d.abstract_en == "", repr(d.abstract_en))

# ②-反例 iii-a：`摘要：` 独占一段，但下一段是英文（英文块的标签，实机 08 Min_Cai）
p = make_docx(["On the Logic of Databases", "Zhang San", "英文标题及摘要",
               "摘要：", "Fallacies are a crucial component in the study of argumentation.",
               "Abstract:", "This paper discusses the logic of databases."] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 iii-a：独占的 `摘要：` 后面是英文 → 不抢中文摘要槽",
      d.abstract_cn == "", repr(d.abstract_cn))
check("反例 iii-a：同一份稿子的英文摘要照旧取到",
      d.abstract_en == "This paper discusses the logic of databases.", repr(d.abstract_en))

# ②-反例 iii-b：**缩进**的 `摘要： ` + 英文正文（新放宽的形态，不得抢中文槽）
p = make_docx(["On the Logic of Databases", "Zhang San",
               "   摘要： The logical education at Huazhong University is an important part."]
              + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 iii-b：缩进的 `摘要：` + 英文正文 → 不抢中文摘要槽",
      d.abstract_cn == "", repr(d.abstract_cn))

# ②-对照：同样的段落**不缩进** → 旧路径逐字节不变（实机 10 Ming Xu 的既成事实）
p = make_docx(["On the Logic of Databases", "Zhang San",
               "摘要： The logical education at Huazhong University is an important part."]
              + BODY)
TMP.append(p)
d = parse_docx(p)
check("对照（旧路径不变）：无缩进的同段英文正文仍照旧写进 abstract_cn",
      d.abstract_cn == "The logical education at Huazhong University is an important part.",
      repr(d.abstract_cn))

# ②-反例 iv：标签独占一段 + 紧跟关键词行
p = make_docx(["论数据库逻辑", "张三", "摘要", "关键词：逻辑；数据库"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 iv：标签独占一段 + 紧跟关键词 → 摘要空、关键词不丢",
      d.abstract_cn == "" and d.keywords_cn == "逻辑；数据库",
      f"{d.abstract_cn!r} / {d.keywords_cn!r}")
p = make_docx(["论数据库逻辑", "张三", "    摘要", "    关键词：逻辑；数据库"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 iv（缩进版）：摘要为空、关键词行没被吞成摘要正文",
      d.abstract_cn == "" and "逻辑；数据库" not in d.abstract_cn,
      f"{d.abstract_cn!r}（注：`_KW_RE` 本轮未放宽，缩进的关键词本来就不认，"
      f"但它是向下取正文的**收手条件**）")

# ②-反例 v（本轮自己加的）：缩进标签**不得**把「开头英文题名」的扫描范围一起放大。
#     实测 2022年第4期(65) 康海的校对稿就是这个形态：缩进的 `    摘 要：…` 一旦计入
#     meta_end，扫描范围从第 2 段涨到第 5 段，段首那行邮箱会被当成英文题名。
p = make_docx(["反推数学与无穷", "康海", " author013@sample.com",
               "    摘 要：无穷一直以来都是数学哲学中的一个基本问题，但不同的无穷观都未令人满意。",
               "    关键词：反推数学；无穷"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("反例 v：缩进标签不放大 meta_end → 邮箱行不会被当成英文题名",
      d.abstract_cn.startswith("无穷一直以来") and d.title_en == "",
      f"{d.abstract_cn[:14]!r} / title_en={d.title_en!r}")

# ②-正例 1：同段（含尾随空白）——取值逐字节不变
p = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。  ", "关键词：逻辑；数据库"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("正例：同段取值（尾随空白也不动）",
      d.abstract_cn == "本文讨论数据库逻辑。  ", repr(d.abstract_cn))

# ②-正例 2：段首缩进（半角 / 全角）
for pad, why in (("    ", "四个半角空格"), ("\u3000\u3000", "两个全角空格")):
    p = make_docx(["论数据库逻辑", "张三", pad + "摘要：本文讨论数据库逻辑。",
                   "关键词：逻辑；数据库"] + BODY)
    TMP.append(p)
    d = parse_docx(p)
    check(f"正例：段首缩进（{why}）→ 取到摘要",
          d.abstract_cn == "本文讨论数据库逻辑。", repr(d.abstract_cn))

# ②-正例 3：标签独占一段 → 下一段
p = make_docx(["论数据库逻辑", "张三", "摘 要 ", ABS_CN, "关键词：逻辑；数据库"] + BODY)
TMP.append(p)
d = parse_docx(p)
check("正例：标签独占一段 → 取下一段", d.abstract_cn == ABS_CN, repr(d.abstract_cn))

# ②-正例 4：正文全英文 + 文末中文摘要
p = make_docx(["On the Logic of Databases", "Zhang San",
               "Abstract: We study the logic of databases.", "Keywords: logic; database"]
              + [f"Body paragraph {i}." for i in range(40)]
              + ["参考文献", "[1] Zhang San. Logic of databases[J]. 2020."]
              + ["论数据库逻辑", "张三", "摘  要", ABS_CN, "关键词：逻辑；数据库"])
TMP.append(p)
d = parse_docx(p)
check("正例：文末中文摘要（正文全英文的稿子）", d.abstract_cn == ABS_CN, repr(d.abstract_cn))
check("正例：文末中文摘要不影响开头的英文摘要",
      d.abstract_en == "We study the logic of databases.", repr(d.abstract_en))

# ②-正例 5：开头已有中文摘要时，文末那段不许来覆盖
p = make_docx(["论数据库逻辑", "张三", "摘要：开头这一套。", "关键词：逻辑；数据库"]
              + BODY + ["参考文献", "[1] 张三. 数据库逻辑[J]. 2020."]
              + ["论数据库逻辑的哲学基础", "摘要：" + ABS_CN])
TMP.append(p)
d = parse_docx(p)
check("正例：开头优先（文末的中文摘要不覆盖开头那套）",
      d.abstract_cn == "开头这一套。", repr(d.abstract_cn))
for _p in TMP:
    os.unlink(_p)

# ══ ③ 旧规一致性：旧规非空 ⇒ 新取值逐字节相同 ═════════════════════════════
print("\n── ③ 与改动前的取值规则一致（真实语料）──")


def old_abstract_cn(path: str):
    """**改动前**中文摘要的取值规则：开头 META_SCAN 段内第一个含汉字的标签块，
    正文用 `_label_body`（中文标签不向下看）。返回 None = 旧规空手而归。

    这段代码是历史的冻结版本，故意不给生产代码复用——它证明的是「旧规非空的地方
    新代码一个字节都没动」。
    """
    plains = [b.plain for b in read_blocks(path) if not isinstance(b, Table)]
    blocks = [(i, _has_cjk(t), _label_body(plains, i, _ABS_RE))
              for i, t in enumerate(plains[:META_SCAN]) if _ABS_RE.match(t)]
    val = next((t for _, cjk, t in blocks if cjk), "")
    return val or None


def find(d: str, needle: str) -> str:
    """在语料目录下按文件名片段找稿子；跳过 Word 的 `~$` 锁文件/坏 zip。"""
    hits = [x for x in glob.glob(os.path.join(_REPO, d, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x) and zipfile.is_zipfile(x)]
    return hits[0] if hits else ""


UNCHANGED = [
    ("2022年第6A期(67A)/10 Ming Xu", "英文中文摘要等信息",
     "同段、无缩进、正文是英文 → 旧规就写进中文摘要（本次不得改）"),
    ("2023年第1期(68)/05 Ming_Zhou", "周明 真之多元论",
     "开头有中文摘要、文末是英文块 → 一处都不许动"),
    ("2025年第6期(85)/04 Chen_Xu", "汉语摘要",
     "无分隔符的粘连标签 `摘要理想的人工…` → 旧规取到的必须逐字节不变"),
    ("2026年第5期(90)/10 Lei_Li", "李雷 当代逻辑论题鸟瞰",
     "中英两套摘要都在开头 → 文末兜底不许介入"),
]
for _dir, needle, why in UNCHANGED:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    old = old_abstract_cn(path)
    got = parse_docx(path).abstract_cn
    check(f"旧规取值逐字节保留：{why}  [{os.path.basename(path)[:24]}]",
          old is not None and got == old,
          f"old={len(old or '')}字 got={len(got)}字 逐字节相同={got == old}")

# ══ ④ 真实语料：修复稿 + 反例 ═════════════════════════════════════════════
print("\n── ④ 真实语料（修复稿 / 反例）──")

# (语料目录, 文件名片段, 断言函数, 说明)
FIXED = [
    ("2023年第1期(68)/04 Jing_Cheng", "20160050645_flow",
     lambda d, p: d.abstract_cn.startswith("直觉主义认知逻辑IEL"),
     "`摘 要 `（字间空格）独占一段 → 取下一段"),
    ("2023年第5期(72)/03 Jie_Song", "最新！——录用稿",
     lambda d, p: d.abstract_cn.startswith("决策论哲学中的一个重要问题"),
     "段首四个半角空格缩进"),
    ("2023年第6期(73)/06 Lei_Zhang", "全文gaigai",
     lambda d, p: d.abstract_cn.startswith("睡眠已成为社会学家讨论的医学化的一个典型例子"),
     "文末 `摘    要` 独占一段（开头英文标签是 `Abstract. `，英文侧另有既存问题，"
     "不在本轮范围）"),
    ("2024年第1期(74)/07 Min_Jiao", "202302",
     lambda d, p: d.abstract_cn.startswith("对道德判断的条件句推理")
     and d.keywords_cn.startswith("弗雷格-吉奇问题"),
     "开头 `摘要` 独占一段 + 关键词没被吞进摘要"),
    ("2024年第6期(79)/06 Min_Liu", "Mengzi on Rectification",
     lambda d, p: d.abstract_cn.startswith("近代以来")
     and d.abstract_en.startswith("Set in the context of pre-Qin period"),
     "中英两套：英文块照旧取、中文槽补上"),
    ("2024年第6期(79)/01 Xue_Wang", "A Careful inspection",
     lambda d, p: d.abstract_cn.startswith("普里斯特近期")
     and d.abstract_en.startswith("Graham Priest"),
     "文末中文摘要（参考文献之后）+ 开头英文摘要"),
    ("2025年第6期(85)/06 Ning_Gan", "JIN GAN",
     lambda d, p: d.abstract_cn.startswith("日本近世关于Rhetoric"),
     "文末 `摘    要` 独占一段"),
    ("2026年第3期(88)/06 Hao_Chen", "Revised Manuscript 5.23",
     lambda d, p: d.abstract_cn.startswith("论证能力最初是参照语言学中交际能力"),
     "文末 `摘  要` 独占一段"),
]
for _dir, needle, ok, why in FIXED:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    try:
        good = bool(ok(rd, path))
    except Exception as e:                      # 断言里的属性缺失也算失败
        good, why = False, f"{why} [{type(e).__name__}: {e}]"
    check(f"{why}  [{os.path.basename(path)[:26]}]", good,
          f"abstract_cn={rd.abstract_cn[:26]!r}")

NEG = [
    ("2026年第1期(86)/08 Min_Cai", "补充材料 20260117",
     lambda d, p: (d.abstract_cn or "") == ""
     and "Fallacies are a crucial component" not in (d.abstract_cn or "")
     and d.abstract_cn == (old_abstract_cn(p) or ""),
     "`摘要：` 独占一段、下一段是英文（英文块）→ 仍然 fail，不许拿英文冒充"),
    ("2025年第6期(85)/04 Chen_Xu", "Time-budget-sensitive",
     lambda d, p: d.abstract_en.startswith("An idealized artificial general intelligence")
     and d.keywords_en.startswith("Need-for-Closure"),
     "英文块的标签独占一段（Abstract / Keywords:）→ 英文侧不动"),
    ("2023年第3期(70)/06 Hao_Chen", "赵晓梅稿件",
     lambda d, p: d.abstract_cn == "" and d.abstract_en.startswith("Current research"),
     "已知限制：文末标签是 `中文摘要`（不在 _ABS_RE 词表内）→ 仍 fail"),
    ("2024年第5期(78)/03 Chen_Xiao", "点、区间与含数字句子真值",
     lambda d, p: d.abstract_cn == "",
     "已知限制：`摘要: …` 在第 24 段、且排在参考文献**之前**（两头都够不着）→ 仍 fail"),
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
    check(f"反例：{why}  [{os.path.basename(path)[:24]}]", good)

print(f"\n{'=' * 60}\n失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
