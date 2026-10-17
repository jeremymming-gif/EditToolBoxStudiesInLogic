#!/usr/bin/env python3.14
"""「未解析的文献区原文」兜底（任务书第五节）的判据测试。

为什么要有这条兜底：全库 **14 篇稿件有文献区却 `n_bib = 0`**，其中 9 篇的文献区里
**确实有 10–27 行候选条目**（8 篇人年制无编号 + 1 篇日文 `[N]` 清单）。编辑部口径是
「编错的卷号比空缺更糟」，文献核实本来就交给**编辑各自的 AI**（README 第 41 条）——
所以工具**不写**第二种清单解析器，只把这些行**原样**交出去，由 AI 按第一节规范结构化。

本测试守住三件事：
  ① **只在**「bib 一条条目都切不出来 **且** 有原文」时追加第五节；其余情形（有条目 /
     没给原文 / 给了空列表）返回的 markdown 与旧签名调用**逐字节相同**；
  ② 第五节里原文**逐行、按序、完整**，且代码围栏绝不会被原文里的 ``` / ```` 提前关掉
     （围栏必须**严格长于**原文里最长的反引号串）；
  ③ `DocxDoc.ref_raw_lines` 的采集口径：就是解析器吃掉的那份 `section`（同一顺序、
     解析成功与失败的行都在），没有文献区时是空表——用合成的最小 docx 端到端验。

四组断言： (a) 有条目 → 无第五节且逐字节不变；(b) 0 条 + 原文 → 第五节完整、围栏不破；
(c) 0 条 + 无原文 → 无第五节；(d) `raw_ref_lines=[]` 与 `None` 逐字节等价。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_refmark_raw_refs.py
"""
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import refmark  # noqa: E402
from slcn.docxsource import DocxDoc, parse_docx  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


HEAD = "## 五、未解析的文献区原文（供结构化）"

# 有条目的 bib（真实体例，两个条目）
BIB_OK = """\
@article{Quine1970,
  author  = {W. V. O. Quine},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970}
}

@book{Chen2022,
  author    = {陈波},
  title     = {逻辑例外论},
  publisher = {北京大学出版社},
  address   = {北京},
  year      = {2022},
  userf     = {cn}
}
"""

# 8 篇「人年制无编号」那类的原文：解析器一条都认不出来（没有 `[M]` 类型标识）
RAW_LINES = [
    "A. Capone, 2019, Pragmatics, Oxford: Oxford University Press.",
    "张三、李四, 2021, 语用学导论, 北京: 商务印书馆.",
    "   ",
    "王五, 无年份, 集刊里的文章, 逻辑学研究 第 5 辑.",
]
RAW_LINES = [ln for ln in RAW_LINES if ln.strip()]      # docx 侧已滤掉空段，测试同口径


def fence_and_body(md, head=HEAD):
    """从任务书里取出第五节围栏 + 围栏内的原文行。取不到返回 (None, [])。"""
    lines = md.split("\n")
    if head not in lines:
        return None, []
    i = lines.index(head)
    j = next((k for k in range(i + 1, len(lines))
              if lines[k] and set(lines[k]) == {"`"}), None)
    if j is None:
        return None, []
    k = next((k for k in range(j + 1, len(lines)) if lines[k] == lines[j]), None)
    if k is None:
        return lines[j], []          # 围栏没关上：调用方按失败处理
    return lines[j], lines[j + 1:k]


print("── (a) bib 有条目：第五节不出现，且与旧调用逐字节相同 ──")

md_old = refmark.build(BIB_OK, "Some_Stem")
md_new = refmark.build(BIB_OK, "Some_Stem", raw_ref_lines=RAW_LINES)
check("(a) 有条目 + 给了原文 → 无第五节", HEAD not in md_new)
check("(a) 有条目 → 新参数下逐字节等于旧调用", md_new == md_old,
      f"len {len(md_new)} vs {len(md_old)}")
check("(a) 有条目 → 逐条清单还在（正常路径没被动过）", "## 四、逐条清单" in md_old)
check("(a) 第三个位置参数仍是 note（旧调用不破）",
      refmark.build(BIB_OK, "S", "备注行") == refmark.build(BIB_OK, "S", "备注行", None)
      and "备注行" in refmark.build(BIB_OK, "S", "备注行"))

print("\n── (b) 0 条 + 原文：第五节完整、顺序正确、围栏不破 ──")

EMPTY_BIB = ""      # 这些稿子 res.bib_path 是 None，convert 传进来的就是空串
md_b = refmark.build(EMPTY_BIB, "Raw_Stem", raw_ref_lines=RAW_LINES)
check("(b) 0 条 + 原文 → 第五节出现", HEAD in md_b)
check("(b) 第五节排在既有内容**之后**（逐条清单在前）",
      md_b.index("## 四、逐条清单") < md_b.index(HEAD))
check("(b) 含「未经解析」的告知", "未经解析" in md_b)
check("(b) 说明了为什么（解析器要求 `作者. 年. 题名[M]. 出版信息`）",
      "作者. 年. 题名[M]. 出版信息" in md_b)
check("(b) 指明按第一节规范结构化、守第三节要求",
      "第一节的 bib 规范" in md_b and "第三节的全部要求" in md_b)

pos, order_ok, missing = -1, True, []
for ln in RAW_LINES:
    k = md_b.find(ln)
    if k < 0:
        missing.append(ln)
        order_ok = False
    elif k < pos:
        order_ok = False
    else:
        pos = k
check("(b) 原文每一行都出现，且**按原顺序**", order_ok and not missing,
      f"missing={missing}")

fence, body = fence_and_body(md_b)
check("(b) 围栏闭合，围栏内就是原文逐行", fence is not None and body == RAW_LINES,
      f"fence={fence!r} body={body[:1]}")
check("(b) 围栏长度只由内容决定且够用", fence == "```", f"{fence!r}")

# 原文里自带 ```（行内、以及独占一行的收尾样式）：围栏必须加长
TRICKY = [
    "第一条：``` 这一段里出现了三个反引号 ```",
    "```",
    "````",
    "最后一条：正常文献行.",
]
md_c = refmark.build("", "Tricky", raw_ref_lines=TRICKY)
fence2, body2 = fence_and_body(md_c)
check("(b) 原文含 ``` / ```` → 围栏自动加长到 5", fence2 == "`````", f"{fence2!r}")
check("(b) 加长后仍闭合，且围栏内逐行等于原文", body2 == TRICKY)
check("(b) 原文里的 ``` 那几行**仍在**第五节里（没被挤出去）",
      all(ln in md_c for ln in TRICKY))
check("(b) 围栏严格长于原文里最长的反引号串",
      fence2 is not None
      and len(fence2) > max(len(m) for m in re.findall(r"`+", "\n".join(TRICKY))))

print("\n── (c) 0 条 + 没原文：第五节不出现 ──")

md_c1 = refmark.build("", "Raw_Stem")
md_c2 = refmark.build("", "Raw_Stem", raw_ref_lines=None)
check("(c) 0 条 + 未给原文 → 无第五节", HEAD not in md_c1)
check("(c) 0 条 + None → 无第五节，且与旧调用逐字节相同", HEAD not in md_c2 and md_c2 == md_c1)
check("(c) 0 条 + 注释型 bib（切不出条目）→ 同样无第五节",
      HEAD not in refmark.build("% 由参考文献附件生成\n\n", "S"))

print("\n── (d) raw_ref_lines=[] 与 None 逐字节等价 ──")

check("(d) 空列表 == None（空 bib）", refmark.build("", "S", raw_ref_lines=[]) == refmark.build("", "S"))
check("(d) 空列表 == None（有条目的 bib）",
      refmark.build(BIB_OK, "S", raw_ref_lines=[]) == refmark.build(BIB_OK, "S"))
check("(d) 空列表 → 即使 0 条也不出现第五节", HEAD not in refmark.build("", "S", raw_ref_lines=[]))

print("\n── ③ DocxDoc.ref_raw_lines 的采集口径（合成最小 docx）──")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TMP = []


def make_docx(paras):
    """只含 word/document.xml 的最小 docx（与 test_abstract_label.py 同法）。"""
    ps = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(t)}</w:t></w:r></w:p>"
                 for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    TMP.append(path)
    return path


BODY = [f"正文第{i}段，讨论数据库逻辑与数据库理论之间的关系。" for i in range(20)]
REF_AREA = [
    "张三. 2020. 数据库逻辑导论[M]. 北京: 科学出版社.",      # 人年制，解析得出来
    "A. Capone, 2019, Pragmatics, Oxford: Oxford University Press.",   # 解析不出来
    " 李四, 2021, 语用学导论, 北京: 商务印书馆. ",          # 解析不出来（有首尾空白）
]
p = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：数据库；逻辑"]
              + BODY + ["参考文献"] + REF_AREA)
d = parse_docx(p)
check("③ 有文献区：ref_raw_lines = 解析器吃掉的那份 section（含解析失败的行、按序、已 strip）",
      d.ref_raw_lines == [ln.strip() for ln in REF_AREA], repr(d.ref_raw_lines))
# 2026-10-17 校准（实例测试）：第 2 条英文逗号风格（`A. Capone, 2019, …`）以前解析不出来，
# 现在能解析了 —— 这是**改进**，断言随之更新；第 3 条（年份在中间的 `李四, 2021, 语用学导论, …`）
# 仍解析不出来（中文兜底要求年份在"其余部分"，这是已知限制，见 AUDIT）。
check("③ 采集是**附加**的：三条文献区原文现在都能解析（人年制 2020 + 英文 2019 + 中文年份第2段 2021）",
      len(d.refs) == 3 and {r.year for r in d.refs} == {"2020", "2019", "2021"},
      f"refs={[r.year for r in d.refs]}")
check("③ 二次校准后：年份在第 2 段的中文逗号条目（`李四, 2021, 语用学导论, …`）也能解析了",
      any(r.year == "2021" for r in d.refs), f"refs={[r.year for r in d.refs]}")
check("③ 采集是**附加**的：正文/题名不受影响",
      d.title == "论数据库逻辑" and len(d.body) > 100, f"title={d.title!r}")

p2 = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。"] + BODY)
d2 = parse_docx(p2)
check("③ 没有文献区 → ref_raw_lines 是空表（不是 None）", d2.ref_raw_lines == [])
check("③ 字段有默认值，且各 DocxDoc 实例互不共享（default_factory）",
      DocxDoc(path="x").ref_raw_lines == [] and DocxDoc(path="y").ref_raw_lines == []
      and DocxDoc(path="x").ref_raw_lines is not DocxDoc(path="y").ref_raw_lines)
check("③ 生成的任务书里，这段原文可直接用（0 条 + 原文 → 第五节）",
      HEAD in refmark.build("", "Syn", raw_ref_lines=d.ref_raw_lines))

for _t in TMP:
    try:
        os.unlink(_t)
    except OSError:
        pass

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
