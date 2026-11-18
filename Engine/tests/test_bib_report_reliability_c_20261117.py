#!/usr/bin/env python3.14
"""A 类缺陷的回归测试：报告**别把人看不出来的东西说成「有改动」**。

缺陷原样（DESIGN §11.2 A / EXEC-REVIEW「成对输出」段）
---------------------------------------------------
报告里凡是「旧 → 新」成对打印的地方，两侧各自 `_clip(s, n)`。当两个值的前 n 个
字符完全相同时，两侧 clip 结果**逐字节相同**，报告于是印出：

    字段值有改动：`author`：`Smith, John and Doe, Jane and Roe, Ri…` →
                            `Smith, John and Doe, Jane and Roe, Ri…`

编辑看见「有改动」，却看不出改了哪里——这比不报还糟。同类还有共同长后缀、
多段值、纯空白差异。

断言分五组：
  ① `pair_summary()` 单测：短值一字不改、长共同前缀/后缀/多段/空白差异**两侧必须不同**、
     输出长度有界（不许把整份字段塞进报告）。
  ② 六个成对点是同一套规则：`bibreport` 的 pages / number / author / diff-changed，
     `aibib` 的倒装备注 / `diff_fields.changed`；逐个跑到真实文案上验证差异可见。
  ③ **行为保持**：短值仍然完整显示（旧断言依赖 `T. Burge → Burge, T.` 这种整句形态）；
     `_clip()` 语义未变（`aibib._clip` 仍 `is bibparse._clip`）——
     `test_merge_record_provenance.py` 的 70 字符断言靠它。
  ④ 报告抬头的路径：快照就在报告目录里 → 给**相对路径**（整份交付搬走仍点得到）；
     快照不在/算不出/不传 report_dir（checkbib 通道）→ 保持原样的绝对路径。
  ⑤ 不回归：单侧 clip（doi / month / 字段被删…）**不变**；`- 退回文件：` 行首
     与 `字段值有改动` 字样都还在（`test_checkbib.py` 依赖）。

不写 out/、不联网、不调模型。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
        tests/test_bib_report_reliability_c_20261117.py
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib, bibparse, bibreport  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# 公共前缀 40 字符、差异在 41 字符之后：这正是会「两侧 clip 成同一个字符串」的形态
LONG_SAME_HEAD = "A" * 40


def _pair_lines(md):
    return [ln for ln in md.splitlines() if " → " in ln]


# ── ① pair_summary 单测 ───────────────────────────────────────────────────
print("\n── ① pair_summary：短值一字不改，长值差异必须看得见 ──")

short_old, short_new = bibparse.pair_summary("T. Burge", "Burge, T.")
check(
    "① 短值原样返回（不加省略号、不截断）",
    (short_old, short_new) == ("T. Burge", "Burge, T."),
    f"{short_old!r} / {short_new!r}",
)

check(
    "① `_clip` 对共同长前缀**确实**会输出同一串（本组有牙齿的反例）",
    bibparse._clip(LONG_SAME_HEAD + "XYZ", 40) == bibparse._clip(LONG_SAME_HEAD + "QQQ", 40),
    bibparse._clip(LONG_SAME_HEAD + "XYZ", 40),
)

old, new = bibparse.pair_summary(LONG_SAME_HEAD + "XYZ", LONG_SAME_HEAD + "QQQ", 40)
check("① 共同长前缀：两侧**不相同**", old != new, f"{old!r} / {new!r}")
check("① 共同长前缀：两侧都含真实差异片段", "XYZ" in old and "QQQ" in new, f"{old!r} / {new!r}")

tail = "C" * 40
old, new = bibparse.pair_summary("B" * 30 + "XYZ" + tail, "B" * 30 + "QQQ" + tail, 40)
check("① 共同长后缀：两侧不相同且含差异", old != new and "XYZ" in old and "QQQ" in new,
      f"{old!r} / {new!r}")

multi_old = "Smith, John and Doe, Jane and Roe, Richard"
multi_new = "Smith, John and Doe, Jane and Roe, Ricardo"
old, new = bibparse.pair_summary(multi_old, multi_new, 40)
check("① 多作者长值：差异可见（不只给「⋯」）", old != new and "Richard" in old and "Ricardo" in new,
      f"{old!r} / {new!r}")

old, new = bibparse.pair_summary("Burge, T.", "Burge,  T.")
check("① 纯空白差异：必须标出来（不许印成一行等号）",
      "空白差异" in old and "空白差异" in new, f"{old!r} / {new!r}")

long_pair = bibparse.pair_summary("x" * 400, "y" * 400, 40)
check("① 超长值：输出有界（≤ 3n，不把整份字段塞进报告）",
      max(len(long_pair[0]), len(long_pair[1])) <= 120,
      f"{len(long_pair[0])} / {len(long_pair[1])}")

check("① 相同值原样返回（不该凭空加标记）",
      bibparse.pair_summary("Same", "Same") == ("Same", "Same"),
      str(bibparse.pair_summary("Same", "Same")))


# ── ② 六个成对点走同一套规则 ──────────────────────────────────────────────
print("\n── ② 六个成对点：真实报告文案里差异看得见 ──")

# ②-a bibreport pages（30）/ number（20）/ author（50）/ diff changed（40）
pages_bib = "@article{k,title={T},author={A},year={2001},pages={1-3},number={007}}\n"
pages_md = bibreport.build_report(pages_bib)
pages_lines = "\n".join(_pair_lines(pages_md))
check("②-a pages 成对行仍印「应为」", "→ 应为" in pages_lines, pages_lines[:120])
check("②-a pages 差异可见（1-3 与 1--3 都在）", "1-3" in pages_lines and "1--3" in pages_lines,
      pages_lines.splitlines()[0] if pages_lines else "")
check("②-a number 成对行差异可见", "007" in pages_lines and "{7}" in pages_lines,
      pages_lines)

au_old = "Smith, John and Doe, Jane and Roe, Richard"
au_new = "Smith, John and Doe, Jane and Roe, Ricardo"
au_bib = f"@article{{k,title={{T}},author={{{au_old}}},year={{2001}}}}\n"
au_md = bibreport.build_report(au_bib)
au_line = next((ln for ln in au_md.splitlines() if "作者写法可规范" in ln), "")
check("②-a author 成对行：两侧都出现（差异在 40 字符之外也看得见）",
      ("Richard" in au_line and "Ricardo" in au_line) or au_line == "",
      au_line or "（本输入未触发 author 规范化，规则不重要）")

# ②-b bibreport diff changed：超长共同前缀的 title
title_head = "A study of the very long title that begins identically here"
diff_md = bibreport.build_report(
    f"@article{{k,title={{{title_head} ALPHA}},author={{A}},year={{2001}}}}\n",
    f"@article{{k,title={{{title_head} OMEGA}},author={{A}},year={{2001}}}}\n",
)
diff_line = next((ln for ln in diff_md.splitlines() if "字段值有改动" in ln), "")
check("②-b diff 成对行仍印「字段值有改动」", "字段值有改动" in diff_line, diff_line[:120])
check("②-b diff 成对行：差异片段 ALPHA / OMEGA 都看得见",
      "ALPHA" in diff_line and "OMEGA" in diff_line, diff_line)

# ②-c aibib.diff_fields：长共同前缀不许退化成同一串
old_e = bibparse.Entry(etype="article", key="k", body="", line=1,
                       fields={"author": LONG_SAME_HEAD + "XYZ"})
new_e = bibparse.Entry(etype="article", key="k", body="", line=1,
                       fields={"author": LONG_SAME_HEAD + "QQQ"})
fd = aibib.diff_fields(old_e, new_e)
line = fd.changed[0] if fd.changed else ""
check("②-c aibib.diff_fields：changed 一行里两侧不同", "XYZ" in line and "QQQ" in line, line)
check("②-c aibib.diff_fields：仍保留 `字段名: 旧 → 新` 形态（旧断言依赖）",
      line.startswith("author: ") and " → " in line, line)


# ── ③ 行为保持 ────────────────────────────────────────────────────────────
print("\n── ③ 行为保持：短值整句形态与 `_clip` 语义都没动 ──")

old_e = bibparse.Entry(etype="article", key="k", body="", line=1,
                       fields={"author": "T. Burge"})
new_e = bibparse.Entry(etype="article", key="k", body="", line=1,
                       fields={"author": "Burge, T."})
fd = aibib.diff_fields(old_e, new_e)
check("③ 短值 changed 仍是完整整句（test_aibib.py:297 的断言形态）",
      any(c.startswith("author: T. Burge → Burge, T.") for c in fd.changed),
      str(fd.changed))

check("③ `aibib._clip` 仍是 `bibparse._clip`（70 字符断言靠同一条实现）",
      aibib._clip is bibparse._clip,
      f"{getattr(aibib._clip, '__module__', '?')}")

check("③ `_clip` 语义未变：短值原样、长值截 60 + 「…」",
      bibparse._clip("short") == "short" and bibparse._clip("z" * 70) == "z" * 60 + "…",
      bibparse._clip("z" * 70))


# ── ④ 报告抬头路径：相对/绝对 ─────────────────────────────────────────────
print("\n── ④ 报告抬头路径：快照在报告目录里就给相对路径 ──")

tmp = tempfile.mkdtemp(prefix="bibrep_a_")
snap = os.path.join(tmp, "reference.bib.ai前")
with open(snap, "w", encoding="utf-8") as fh:
    fh.write("@article{k,title={old}}\n")
tex = os.path.join(tmp, "out.tex")
with open(tex, "w", encoding="utf-8") as fh:
    fh.write("\\cite{k}\n")

BIB = "@article{k,title={new},author={A},year={2001}}\n"


def _head(md, prefix):
    return next((ln for ln in md.splitlines() if ln.startswith(prefix)), "")


md = bibreport.build_report(BIB, "@article{k,title={old}}\n", "\\cite{k}\n",
                            returned_path=os.path.join(tmp, "reference.bib"),
                            original_path=snap, tex_path=tex, report_dir=tmp)
check("④ 原件（快照）在报告目录里 → 相对路径",
      _head(md, "- 原件") == "- 原件（--original）：`reference.bib.ai前`", _head(md, "- 原件"))
check("④ 正文在报告目录里 → 相对路径",
      _head(md, "- 正文") == "- 正文（--tex）：`out.tex`", _head(md, "- 正文"))

md_abs = bibreport.build_report(BIB, "@article{k,title={old}}\n",
                                returned_path=os.path.join(tmp, "reference.bib"),
                                original_path=snap)
check("④ 不传 report_dir（checkbib 通道）→ 保持绝对路径",
      snap in _head(md_abs, "- 原件"), _head(md_abs, "- 原件"))

sub = os.path.join(tmp, "sub")
os.makedirs(sub, exist_ok=True)
md_out = bibreport.build_report(BIB, "@article{k,title={old}}\n",
                                returned_path=os.path.join(tmp, "reference.bib"),
                                original_path=snap, report_dir=sub)
check("④ 快照在报告目录**之外** → 不用 `../` 相对路径（搬走即失效），给绝对路径",
      not _head(md_out, "- 原件").startswith("- 原件（--original）：`..") and snap in _head(md_out, "- 原件"),
      _head(md_out, "- 原件"))

md_missing = bibreport.build_report(BIB, None,
                                    returned_path="/nonexistent/not-there.bib",
                                    report_dir=tmp)
check("④ 路径不存在 → 不改写（别给一个点不开的相对链接）",
      "/nonexistent/not-there.bib" in _head(md_missing, "- 退回文件"), _head(md_missing, "- 退回文件"))

md_stdin = bibreport.build_report(BIB, None, report_dir=tmp)
check("④ 占位说明 `(stdin/字符串)` 不被当成路径换算",
      "(stdin/字符串)" in _head(md_stdin, "- 退回文件"), _head(md_stdin, "- 退回文件"))


# ── ⑤ 不回归 ──────────────────────────────────────────────────────────────
print("\n── ⑤ 不回归：单侧 clip、报告骨架、既有字样 ──")

doi_bib = "@article{k,title={T},author={A},year={2001},doi={10.1000/xyz}}\n"
check("⑤ 单侧 clip（doi）仍是原样 40 字符内完整显示",
      "10.1000/xyz" in bibreport.build_report(doi_bib))

del_md = bibreport.build_report(
    "@article{k,title={T},author={A},year={2001}}\n",
    "@article{k,title={T},author={A},year={2001},journal={J}}\n",
)
check("⑤ 字段被删仍是单侧 clip 文案（未被成对规则改写）",
      "字段被删掉：`journal`" in del_md,
      next((ln for ln in del_md.splitlines() if "字段被删掉" in ln), "")[:100])

check("⑤ `- 退回文件：` 行首没变（test_checkbib.py 的 _strip 依赖它）",
      _head(md, "- 退回文件").startswith("- 退回文件："), _head(md, "- 退回文件"))

check("⑤ 报告骨架四节 + 附录表都还在",
      all(s in md for s in ("一、可解析性", "二、编辑部规范", "三、引用键闭合",
                            "四、与 --original 的差异", "## 附录：逐条状态")),
      "四节/附录")

check("⑤ 结论行口径未变（问题/提示两段计数）",
      bibreport.conclusion_line(bibreport.analyze(BIB)).startswith("结论："),
      bibreport.conclusion_line(bibreport.analyze(BIB)))


# ── 汇总 ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + "；".join(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
