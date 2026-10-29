#!/usr/bin/env python3.14
"""裸邮箱（item 4）的判据测试：行形态、三段范围护栏、占位符、真实反例。

三类断言：
  ① `collect_emails` 的行形态与取值范围——直接喂合成段落列表（不读仓库、毫秒级）；
  ② 现造的最小 docx（临时 zip，只含 word/document.xml）——证明**表格单元格**与
     **正文中段的符号 `@`** 结构性不可达（表格在 parse_docx 里就被滤掉、从不进 plains）；
  ③ 三份真实语料反例（语料不在本机则 SKIP）——符号 `@`、审稿人表格邮箱、模板占位符。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_bare_email.py
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
    META_SCAN, _EMAIL_TAIL_FALLBACK, collect_emails, parse_docx,
)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def emails(plains, tail_start=-1, fn=()):
    return collect_emails(plains, tail_start, list(fn))


# ══ ① 行形态（合成段落列表）═══════════════════════════════════════════════
print("── ① 行形态判据 ──")

# ① 地址独占一段（可带括号/全角空格/尾随句号；尾标点必须归一掉）
check("地址独占一段", emails(["题名", "徐宁", "author019@sample.edu.cn", "摘要：…"])
      == ["author019@sample.edu.cn"])
check("括号 + 尾标点归一", emails(["题名", "\u3000（author022@sample.com）。"])
      == ["author022@sample.com"])
check("带标签 <…> + 尾。归一", emails(["题名", "Email: <author016@sample.org>"])
      == ["author016@sample.org"])
check("带标签「邮箱：」+ 尾。归一", emails(["题名", "邮箱：author007@sample.com。"])
      == ["author007@sample.com"])

# ② 整行只有地址（空白分隔多个）——06 Jie Rong
check("一行两个地址（空格分隔）",
      emails(["题名", "author011@sample.com                      author004@sample.com"])
      == ["author011@sample.com", "author004@sample.com"])

# ③ 作者信息/单位行：行内**第一个**地址（这正是「第一作者邮箱」的取值口径）
check("作者简介行内取地址",
      emails(["题名", "作者简介：何明，武汉大学哲学学院博士生，author005@sample.com"])
      == ["author005@sample.com"])
_both = emails(["题名", "作者信息：张敏 author021@sample.edu.cn 王琳 邮箱：author001@sample.com。"])
check("同行裸地址在前、带标签在后 → 第一个是第一作者（其余候选按序返回）",
      _both == ["author021@sample.edu.cn", "author001@sample.com"], repr(_both))

# ④ 作者名 + 地址同行
check("作者名 + 地址同行",
      emails(["题名", "Hao Chen  author003@sample.edu.cn"])
      == ["author003@sample.edu.cn"])
check("长行（>80 字）即使以地址结尾也不算",
      emails(["题名", "甲" * 90 + " a@b.cn"]) == [])
check("同样的行只要 ≤80 字就按「短行以地址结尾」收（阈值边界）",
      emails(["题名", "甲" * 70 + " a@b.cn"]) == ["a@b.cn"])
check("地址前有句末标点 → 是正文句子，不收",
      emails(["题名", "本文认为该结论成立。进一步的讨论请见 a@b.cn"]) == [])

# ⑤ MDPI 式 `Name (addr):`
check("Name (addr):（MDPI 式）",
      emails(["题名", "1 Jing Niu (author012@sample.edu.cn): Center for Clinical Humanities"])
      == ["author012@sample.edu.cn"])

# ⑥ 短行以地址结尾（04 Chen_Xu 的通信地址行）
check("短行以地址结尾",
      emails(["题名", "Shanghai 200433, P. R. China author020@sample.edu.cn"])
      == ["author020@sample.edu.cn"])

# 符号 @ 在任何形态下都不该被当成地址
check("符号 @ 段落不产出地址",
      emails(["题名", "在且仅在@中为真。这样，主体似乎就可以通过掌握 □(C→",
              "确定@，它必须包含关于@的全部信息。"]) == [])

# ══ ② 三段范围护栏（结构性不可达）════════════════════════════════════════
print("\n── ② 取值范围护栏 ──")

HEAD20 = ["p%d" % i for i in range(META_SCAN)]          # 0..19
mid = HEAD20 + ["正文第 %d 段，现实世界 @ 中 p 为真。" % i for i in range(20, 60)]
check("正文中段的符号 @ 不可达（tail_start = -1）", emails(mid) == [])

# 文献区：地址夹在 head 区之后、tail_start 之前 → 不可达；挪进 tail 区就能取到
refdoc = HEAD20 + ["正文"] * 20 + ["somebody@elsewhere.org"] + ["x"] * 5
# 第 40 段是地址；tail_start 从第 41 段开始 → 第 40 段夹在正文/文献区里，不可达
check("正文/文献区（META_SCAN 之后、tail_start 之前）的地址不可达",
      emails(refdoc, tail_start=41) == [])
check("同一个地址只要 tail_start 挪到它前面就能取到（护栏是位置性的，不是看内容）",
      emails(refdoc, tail_start=40) == ["somebody@elsewhere.org"])

# 开头元数据区的上界 META_SCAN：第 20 段已经是正文，不该扫
far = ["题名"] + ["短行" for _ in range(19)] + ["late@body.cn"] + ["x"] * 20
check("开头元数据区上界（第 %d 段起是正文）" % META_SCAN, emails(far) == [])

# 文末兜底只有最后 _EMAIL_TAIL_FALLBACK 段
tail_doc = HEAD20 + ["正文" for _ in range(20)] + ["x@tail.cn"]
tail_doc = ["题名"] + tail_doc          # 长度 = 1+20+20+1 = 42，x@tail.cn 在第 41 段
check("tail_start = -1 时只看最后 %d 段" % _EMAIL_TAIL_FALLBACK,
      emails(tail_doc) == ["x@tail.cn"])

# 脚注通道 + 文档顺序（开头 > 文末 > 脚注）+ 去重
check("脚注里的地址能取到",
      emails(["题名", "正文"], fn=["作者信息：牛静  中山大学逻辑与认知研究所",
                                  "\u3000\u3000\u3000author014@sample.edu.cn"]) ==
      ["author014@sample.edu.cn"])
check("文档顺序：开头元数据区在前、脚注在后",
      emails(["题名", "first@head.cn"], fn=["second@foot.cn"]) ==
      ["first@head.cn", "second@foot.cn"])
check("同一地址去重",
      emails(["题名", "same@x.cn"], fn=["same@x.cn", "other@y.cn"]) ==
      ["same@x.cn", "other@y.cn"])

# 占位符黑名单
check("模板占位符 xxx@xxx.xxx 被拒绝", emails(["题名", "xxx@xxx.xxx"]) == [])
check("模板占位符 缩进 + 尾随人名", emails(["题名", "xxx@xxx.xxx Second Author XXX"]) == [])
check("模板占位符 yourdomain / test@example",
      emails(["题名", "test@example.com", "a@yourdomain.org"]) == [])
check("占位符被拒但不影响同一篇里的真地址",
      emails(["题名", "xxx@xxx.xxx", "real@author.cn"]) == ["real@author.cn"])

# ══ ③ 现造的最小 docx：表格 / 正文中段结构性不可达 ═══════════════════════
print("\n── ③ 最小 docx（表格 + 正文中段符号 @）──")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paras, table_cells=()):
    """只含 word/document.xml 的最小 docx（够跑取值路径即可，不需要真实 Word 文件）。"""
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    tbl = ""
    if table_cells:
        rows = "".join(
            "<w:tr>" + "".join(
                f"<w:tc><w:tcPr><w:tcW w:w=\"4000\" w:type=\"dxa\"/></w:tcPr>"
                f"<w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in row)
            + "</w:tr>" for row in table_cells)
        tbl = f"<w:tbl>{rows}</w:tbl>"
    xml = (f'<w:document xmlns:w="{W_NS}"><w:body>{ps}{tbl}</w:body></w:document>')
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


REVIEWER = "审稿人通讯处及邮编： 广州市中山大学哲学系 510275电   话： 电子邮件： author015@sample.edu.cn"
tmp1 = make_docx(
    # 题名 + 开头元数据区里的真地址 + 40 段正文（把符号 @ 顶进正文中段）
    ["论逻辑与数据库", "张三", "author@univ.edu.cn"] + [f"正文第 {i} 段。" for i in range(40)]
    + ["在且仅在@中为真，现实世界 @ 是固定的。", "设 @ 为现实世界。"],
    table_cells=[[REVIEWER]],
)
d1 = parse_docx(tmp1)
check("地址独占段被取到", d1.email == "author@univ.edu.cn", repr(d1.email))
check("表格单元格里的审稿人邮箱没有变成 doc.email",
      "shyping" not in d1.email, repr(d1.email))

tmp2 = make_docx(
    ["论逻辑与数据库", "张三"] + [f"正文第 {i} 段。" for i in range(40)]
    + ["在且仅在@中为真，现实世界 @ 是固定的。"],
    table_cells=[[REVIEWER]],
)
d2 = parse_docx(tmp2)
check("只有符号 @ 与表格邮箱时 doc.email 为空", d2.email == "", repr(d2.email))
check("正文的符号 @ 一个不少（2 个）且未被改写成别的东西",
      d2.body.count("@") == 2, f"body 内 @ × {d2.body.count('@')}")
for p in (tmp1, tmp2):
    os.unlink(p)

# ══ ④ 真实语料反例（语料不在本机则 SKIP）══════════════════════════════════
print("\n── ④ 真实语料反例 ──")

import glob  # noqa: E402


def find(d: str, needle: str) -> str:
    """在语料目录下按文件名片段找稿子（同一稿有多个版本时靠 needle 区分）。"""
    hits = [x for x in glob.glob(os.path.join(_REPO, d, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x)]
    return hits[0] if hits else ""


# (语料目录, 文件名片段, 期望 doc.email, 说明, 附加断言)
CASES = [
    ("2026年第5期(90)/01 Meimei_Han", "定稿", "author002@sample.edu.cn",
     "正文 9 段共 21 个符号 @ 不得进 doc.email（只取脚注里的真地址）", "at21"),
    ("2023年第2期(69)/04 Ming_Xianyu", "鲜于明", "",
     "表格里的审稿人邮箱 author015@sample.edu.cn 不得进 doc.email", None),
    ("2025年第3期(82)/02 Wei_Zhao", "New_Version_0", "",
     "模板占位符 xxx@xxx.xxx 必须留空（不能印进 \\myfirstemail）", None),
    ("2025年第6期(85)/05 Jing_Niu", "251119", "author012@sample.edu.cn",
     "脚注被判 pure_ref 也不能跳过（禁止用脚注分类当护栏）", None),
    ("2022年第4期(65)/06 Jie Rong", "格莱斯", "author011@sample.com",
     "一行两个地址取第一个", None),
    ("2025年第6期(85)/04 Chen_Xu", "作者信息", "author020@sample.edu.cn",
     "地址粘在通信地址行尾（短行以地址结尾）", None),
    ("2024年第4期(77)/05 Hai_Du", "杜海", "author009@sample.com",
     "「电子邮箱：」标签 + tail_start = -1（旧路径覆盖不到）", None),
]
for _dir, needle, want, why, extra in CASES:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    rd = parse_docx(path)
    check(f"{why}  [{os.path.basename(path)[:30]}]", rd.email == want,
          f"want={want!r} got={rd.email!r}")
    if extra == "at21":                    # 规则不得改写正文
        check("  正文里的 @ 未被改动（doc.body 仍含 21 个 @）",
              rd.body.count("@") == 21, f"body 内 @ × {rd.body.count('@')}")

print(f"\n{'=' * 60}\n失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
