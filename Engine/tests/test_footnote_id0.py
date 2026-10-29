#!/usr/bin/env python3.14
"""脚注 id=0 的判据测试：伪脚注按 `w:type` 丢，**不按 id 丢**。

背景：OOXML 里 id −1/0 通常正是 `separator` / `continuationSeparator`（脚注区那条
横线之类的排版元数据，正文从不引用），旧实现写的是 `if i < 1: continue`，于是
**真脚注恰好编号 0** 的稿子整条丢失：本库实测 23 篇的作者信息/收稿日期脚注就是
「不带 `w:type` 的 `w:id="0"`」，正文里另有 `w:footnoteReference w:id="0"` 真引用它，
其中 7 篇脚注里含邮箱（普查还把「解析器没拿到」误记成「原稿本来就没有」= `absent`）。

三类断言：
  ① 现造的最小 docx（临时 zip，只含 word/document.xml + word/footnotes.xml）：
     判据只看 `w:type`，且正常 id（1,2,…）的 id→文字映射一个字节都不许变；
  ② 伪脚注**即使带文字**也不得泄漏——真实语料里它们恰好都是空串，测不出这一点；
  ③ 真实语料反例（语料不在本机则 SKIP）：3 篇 id=0 真脚注（邮箱）+ 2 篇分隔符稿。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_footnote_id0.py
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

from slcn.docxxml import read_footnotes, segs_to_text  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f'xmlns:w="{W_NS}"'


def fn_el(fid, text="", ftype=None, ref_in_body=True):
    """一个 <w:footnote>；`ftype=None` 表示**不写 w:type**（真脚注的常见形态）。"""
    t = f' w:type="{ftype}"' if ftype else ""
    p = f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" if text else "<w:p/>"
    return f'<w:footnote w:id="{fid}"{t}>{p}</w:footnote>'


def make_docx(footnotes, body_refs=()):
    """最小 docx：word/document.xml（正文 + 脚注引用）+ word/footnotes.xml。"""
    refs = "".join(
        f'<w:r><w:footnoteReference w:id="{i}"/></w:r>' for i in body_refs)
    doc = (f'<w:document {W}><w:body>'
           f'<w:p><w:r><w:t>题名</w:t></w:r></w:p>'
           f'<w:p><w:r><w:t>正文</w:t></w:r>{refs}</w:p>'
           f'</w:body></w:document>')
    fns = f'<w:footnotes {W}>' + "".join(footnotes) + "</w:footnotes>"
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/footnotes.xml", fns)
    return path


def texts(path):
    return {i: segs_to_text(s) for i, s in read_footnotes(path).items()}


# ══ ① 判据只看 w:type ══════════════════════════════════════════════════════
print("── ① 判据：按 w:type 丢，不按 id 丢 ──")

# 真脚注编号 0（不带 w:type）+ 正常脚注 1：两者都要在，且 id 0 的内容要读出来
REAL0 = "作者信息：王强  四川师范大学哲学学院  author017@sample.com"
p = make_docx([
    fn_el(-1, "", "separator"),
    fn_el(0, REAL0),
    fn_el(1, "正常脚注一", "normal"),
], body_refs=(0, 1))
got = texts(p)
check("不带 w:type 的 id=0 真脚注被读进来", 0 in got and got.get(0) == REAL0, repr(got.get(0))[:60])
check("正常 id=1 未受影响", got.get(1) == "正常脚注一", repr(got.get(1)))
check("separator(-1) 不出现", -1 not in got, repr(sorted(got)))
check("顺序 = 文档顺序（-1 被丢后剩 0,1）", list(got) == [0, 1], repr(list(got)))
os.unlink(p)

# id=0 显式 w:type="normal"：也是真脚注，必须留（锁「判据不是『有没有 type』」）
p = make_docx([fn_el(0, "显式 normal 的 0 号", "normal"), fn_el(1, "一", "normal")])
got = texts(p)
check('id=0 且 w:type="normal" 是真脚注，保留', got.get(0) == "显式 normal 的 0 号", repr(got))
os.unlink(p)

# id=0 是 continuationSeparator（本库 73 篇的形态）→ 丢
p = make_docx([fn_el(-1, "", "separator"), fn_el(0, "", "continuationSeparator"),
               fn_el(1, "一", "normal"), fn_el(2, "二", "normal")])
got = texts(p)
check("id=0 的 continuationSeparator 被丢", 0 not in got and list(got) == [1, 2], repr(sorted(got)))
os.unlink(p)

# id=0 是 separator、continuationSeparator 在 id=1（本库 13 篇的形态）→ 两个都丢
p = make_docx([fn_el(0, "", "separator"), fn_el(1, "", "continuationSeparator"),
               fn_el(2, "一", "normal"), fn_el(3, "二", "normal")])
got = texts(p)
check("id=0 separator + id=1 continuationSeparator 都被丢（旧实现漏掉了 id=1 那个）",
      0 not in got and 1 not in got and list(got) == [2, 3], repr(sorted(got)))
os.unlink(p)

# 编号不漂移：同一批正常脚注，加不加那条第 0 号真脚注，映射必须逐字节相同
NORMALS = [fn_el(1, "第一条文献", "normal"), fn_el(2, "第二条文献", "normal"),
           fn_el(3, "第三条文献", "normal")]
p_no = make_docx([fn_el(-1, "", "separator"), fn_el(0, "", "continuationSeparator"), *NORMALS])
p_yes = make_docx([fn_el(-1, "", "separator"), fn_el(0, REAL0), *NORMALS], body_refs=(0,))
t_no, t_yes = texts(p_no), texts(p_yes)
check("加进 0 号真脚注后，正常脚注 1/2/3 的 id→文字映射逐字节不变",
      {i: t_no[i] for i in (1, 2, 3)} == {i: t_yes[i] for i in (1, 2, 3)},
      f"{t_no} vs {t_yes}")
check("加进 0 号真脚注后，id 集合只多出 0", set(t_yes) - set(t_no) == {0}, repr(sorted(t_yes)))
os.unlink(p_no)
os.unlink(p_yes)

# ══ ② 伪脚注即使带文字也不得泄漏 ═══════════════════════════════════════════
print("\n── ② 伪脚注带文字也不泄漏 ──")

p = make_docx([
    fn_el(-1, "SEPARATOR-TEXT", "separator"),
    fn_el(0, "CONTINUATION-SEPARATOR-TEXT", "continuationSeparator"),
    fn_el(1, "真脚注", "normal"),
])
got = texts(p)
blob = "\n".join(got.values())
check("带文字的 continuationSeparator(id=0) 没进结果",
      "CONTINUATION-SEPARATOR-TEXT" not in blob and 0 not in got, repr(got))
check("带文字的 separator(id=-1) 没进结果", "SEPARATOR-TEXT" not in blob, repr(got))
check("同文件里的真脚注照常读出（不是「整体为空」蒙对的）", got.get(1) == "真脚注", repr(got))
os.unlink(p)

# ══ ③ 真实语料反例（语料不在本机则 SKIP）══════════════════════════════════
print("\n── ③ 真实语料反例 ──")

import glob  # noqa: E402

from slcn.docxsource import parse_docx  # noqa: E402


def find(d: str, needle: str) -> str:
    hits = [x for x in glob.glob(os.path.join(_REPO, d, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x)]
    return hits[0] if hits else ""


# (语料目录, 文件名片段, id=0 脚注里必须出现的邮箱, doc.email 期望值)
ID0_CASES = [
    ("2023年第4期(71)/01 Jing_Chen", "传播语境下论证设计的方法",
     "author006@sample.edu.cn", "author006@sample.edu.cn"),
    ("2024年第4期(77)/02 Qiang_Wang", "印度正理派", "author017@sample.com", "author017@sample.com"),
    ("2026年第1期(86)/10 Min_Wang", "专访蒂莫西", "author018@sample.edu.cn", "author018@sample.edu.cn"),
]
for _dir, needle, mail, want_email in ID0_CASES:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    t = texts(path)
    check(f"id=0 真脚注被读出且含 {mail}  [{os.path.basename(path)[:24]}]",
          0 in t and mail in t[0], f"ids={sorted(t)[:4]}… id0={t.get(0, '')[:50]!r}")
    check(f"  parse_docx 的 doc.email 就是 {mail}",
          parse_docx(path).email == want_email, repr(parse_docx(path).email))

# 分隔符稿：id=0（或 id=1）是伪脚注，一个字符都不许变成脚注内容
SEP_CASES = [
    # 目录, 片段, 说明, 期望的脚注 id 集合
    ("2026年第3期(88)/02 Ning_Jiang", "Ockham Semantics", "continuationSeparator 在 id=0", set(range(1, 91))),
    ("2026年第5期(90)/07 Jiayi_He", "示例贝叶斯主义", "separator 在 id=0、continuationSeparator 在 id=1",
     set(range(2, 52))),
]
for _dir, needle, why, want_ids in SEP_CASES:
    path = find(_dir, needle)
    if not path:
        print(f"SKIP  {_dir}（本机没有这份语料）")
        continue
    t = texts(path)
    check(f"{why}：伪脚注不进结果，真脚注 {min(want_ids)}..{max(want_ids)} 一个不少"
          f"  [{os.path.basename(path)[:24]}]",
          set(t) == want_ids and all(t[i].strip() for i in want_ids),
          f"ids={len(t)} 个，缺 {sorted(want_ids - set(t))[:5]}，多 {sorted(set(t) - want_ids)[:5]}")
    check("  伪脚注的文字（本来就是空串）没混进任何脚注",
          not any("separator" in v.lower() for v in t.values()))

print(f"\n{'=' * 60}\n失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
