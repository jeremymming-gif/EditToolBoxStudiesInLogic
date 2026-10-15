#!/usr/bin/env python3
r"""参考文献里的**保护与固定写法**（第 85 条，2026-10-15 用户提的需求）。

四类要保护/固定的形式：
  1. 专名首字母：`title = {Two Dogmas of {E}mpiricism}`
  2. 副标题首字母：`title = {A Cognitive Approach to Human Communication: {A}n Action Assembly Theory}`
  3. 罗马数字/编号：`Part {I}`
  4. 英文书的版次写进 `title`：`title = {The Uses of Argument, (Updated Edition)}`
  ＋ 只有编者时编者写进 `author` 并加 `(eds.)`：`author = {{F. H. van Eemeren and P. Wu~(eds.)}}`

**机制**（不是风格偏好）：`编辑笔记/模板文件/SLCN-MIX.sty:733` 对
`article/inproceedings/incollection/misc` 的 `title` 走 `` ``\MakeSentenceCase{#1}'' ``，
而 `\cegen{中文}{英文}` 按 `userf` 选分支 —— 所以**只有不带 `userf = {cn}` 的条目**
会被压大小写，没保护的大写字母会变成小写。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slcn import bibcheck, refmark  # noqa: E402

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


def ent(etype="article", key="k1", **fields):
    body = "".join(f"  {k} = {{{v}}},\n" for k, v in fields.items())
    return f"@{etype}{{{key},\n{body}}}\n"


def details(bib):
    return [i.detail for i in bibcheck.style_form_issues(bib)]


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 规范文本：四类保护 + 版次 + 编者写法都要写进 refmark ──")
spec = refmark.SPEC_MD
check("(a) 讲清机制：英文标题走 \\MakeSentenceCase（并点出 sty 行号）",
      "MakeSentenceCase" in spec and "SLCN-MIX.sty" in spec)
check("(a) 点明「只有不带 userf = {cn} 的条目」才被压大小写",
      "不带 `userf = {cn}`" in spec)
check("(a) 专名首字母保护（例 1）", "Two Dogmas of {E}mpiricism" in spec)
check("(a) 副标题首字母保护（例 2）", "{A}n Action Assembly Theory" in spec)
check("(a) 罗马数字/编号/单字母变量保护（例 3）",
      "Part {I}" in spec and "Generalizing {L}" in spec)
check("(a) 提醒「只裹住那个字母，不要整段加括号」",
      "不要整段加括号" in spec)
check("(a) 英文书版次写进 title（例 4）",
      "The Uses of Argument, (Updated Edition)" in spec and "不单列 `edition`" in spec)
check("(a) 版次条把 SEP 列为例外", "例外：SEP 词条" in spec)
check("(a) 编者写进 author + (eds.)（例 5）",
      "{{F. H. van Eemeren and P. Wu~(eds.)}}" in spec)
check("(a) 说明外层多套一层花括号的理由（当一个整体名）", "一个整体名" in spec)
check("(a) 编者 > 3 人缩为 2–3 人", "超过 3 人时缩为 2–3 人" in spec)
check("(a) 手册的 `and others` 口径也写上（>4 作者 / >3 编者）",
      "and others" in spec and "排校手册" in spec)
check("(a) 说清 `(eds.)` 形态与 `and others` 的分工",
      "编文集" in spec and "普通多作者" in spec)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 判据：该报的报、不该报的不报（全部是 nice 级提示）──")

# 正例：副标题首字母没保护 → 报
d = details(ent(title="Logic: A Brief Introduction", author="Haack, S.", year="1978",
                journal="J", volume="1", number="2", pages="1--9"))
check("(b) 英文 @article 的副标题首字母没保护 → 报",
      any("冒号后的首字母" in x for x in d), f"{d}")

# 反例：已经保护了 → 不报
d = details(ent(title="Logic: {A} Brief Introduction", author="Haack, S.", year="1978",
                journal="J", volume="1", number="2", pages="1--9"))
check("(b) 反例：`: {A}` 已保护 → **不报**", not any("冒号后" in x for x in d), f"{d}")

# 正例：Part I 没保护 → 报
d = details(ent(title="The Continuum Hypothesis, Part I", author="Woodin, W. H.", year="2001",
                journal="J", volume="1", number="2", pages="1--9"))
check("(b) 英文标题里的 `Part I` 没保护 → 报",
      any("没保护" in x and "I" in x for x in d), f"{d}")

# 反例：Part {I} → 不报
d = details(ent(title="The Continuum Hypothesis, Part {I}", author="Woodin, W. H.", year="2001",
                journal="J", volume="1", number="2", pages="1--9"))
check("(b) 反例：`Part {I}` 已保护 → **不报**", not d, f"{d}")

# 正例：英文 @book 单列 edition → 报
d = details(ent("book", title="A Course in Game Theory", author="Osborne, M.", year="1994",
                publisher="MIT Press", address="Cambridge", edition="1st."))
check("(b) 英文 @book 单列 `edition` → 报（应写进 title）",
      any("版次" in x and "title" in x for x in d), f"{d}")

# 反例：SEP 词条的 edition 是季节，**不许**报
d = details(ent("incollection", title="The Normative Status of Logic", author="Steinberger, F.",
                year="2022", booktitle="The Stanford Encyclopedia of Philosophy",
                editor="Zalta, E. N.", publisher="Metaphysics Research Lab, Stanford University",
                edition="{W}inter 2022", url="https://plato.stanford.edu/archives/win2022/entries/x/"))
check("(b) 反例：SEP 词条的 `edition`（存档季节）→ **不报**", not d, f"{d}")

# 正例：只有编者 → 报
d = details(ent("book", title="Contextualizing Pragma-dialectics", year="2017",
                editor="F. H. van Eemeren and P. Wu", publisher="John Benjamins",
                address="Amsterdam"))
check("(b) @book 只有 editor 没有 author → 报（编者写进 author 并加 (eds.)）",
      any("只有编者" in x and "(eds.)" in x for x in d), f"{d}")

# 反例：有作者也有编者 → 不报「只有编者」
d = details(ent("book", title="X", author="A, B.", year="2017", editor="C, D.",
                publisher="P", address="Amsterdam"))
check("(b) 反例：有 author 也有 editor → **不报**「只有编者」",
      not any("只有编者" in x for x in d), f"{d}")

# 反例：中文条目（userf = {cn}）→ 一条都不报
d = details(ent(title="逻辑：一个导论", author="哈克", year="1978", journal="哲学研究",
                volume="1", number="2", pages="1--9", userf="cn",
                edition="2"))
check("(b) 反例：中文条目（`userf = {cn}`）→ 四类一律不报", not d, f"{d}")

# 反例：@inproceedings 带 edition → 不报（版次那条只管 @book）
d = details(ent("inproceedings", title="A Paper", author="A, B.", year="2019",
                booktitle="Proceedings", pages="1--9", edition="2nd"))
check("(b) 反例：非 @book 的 `edition` → 不报", not any("版次" in x for x in d), f"{d}")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 不许污染冻结基线：`check()` 的口径必须一个字没变 ──")
rep = bibcheck.check(ent("book", title="A Course in Game Theory", author="Osborne, M.",
                         year="1994", publisher="MIT Press", address="Cambridge", edition="1st."))
check("(c) `check()` 里**没有**「版次应写进 title」这类新规则",
      not any("版次" in i.detail for i in rep.issues), f"{[i.detail for i in rep.issues]}")
check("(c) `style_form_issues()` 是**单独入口**（不进 check）",
      bibcheck.style_form_issues(ent("book", title="T", author="A, B.", year="1994",
                                     publisher="P", address="C", edition="1st.")) != [])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 本刊范样例（用户给的那两条）不许被误报 ──")
A7 = """@book{a7,
    author={Toulmin, S. E.},
    year={2003},
    title={The Uses of Argument, (Updated Edition)},
    address={Cambridge},
    publisher={Cambridge University Press},
}
"""
A17 = """@book{a17,
    author={{F. H. van Eemeren and P. Wu~(eds.)}},
    year={2017},
    title={Contextualizing Pragma-dialectics},
    address={Amsterdam},
    publisher={John Benjamins},
}
"""
check("(d) 范样例 a7（版次在 title 里）→ 不报", details(A7) == [], f"{details(A7)}")
check("(d) 范样例 a17（编者写进 author + (eds.)）→ 不报", details(A17) == [], f"{details(A17)}")
check("(d) 两条范样例在 `check()` 里也没有 must 级问题",
      bibcheck.check(A7).n_must == 0 and bibcheck.check(A17).n_must == 0,
      f"{bibcheck.check(A7).n_must}/{bibcheck.check(A17).n_must}")

# 立即可编译性：`(eds.)` 外层双花括号必须原样穿过规范化
from slcn import bibnorm  # noqa: E402
out, _ = bibnorm.normalize_bib(A17)
check("(d) 规范化后 `{{…(eds.)}}` 的双花括号与 `~` 都原样保留",
      "author    = {{F. H. van Eemeren and P. Wu~(eds.)}}" in out
      or "author = {{F. H. van Eemeren and P. Wu~(eds.)}}" in out
      or "{{F. H. van Eemeren and P. Wu~(eds.)}}" in out, out.strip()[:120])
out7, _ = bibnorm.normalize_bib(A7)
check("(d) 规范化后 `title` 里的 `(Updated Edition)` 原样保留",
      "(Updated Edition)" in out7)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 第 86 条：英文书版次**自动**并进 title（用户 2026-10-15 拍板）──")
from slcn import bibnorm  # noqa: E402

EN = """@book{k1,
  author  = {Toulmin, S. E.},
  title   = {The Uses of Argument},
  edition = {Updated Edition},
  year    = {2003}
}
"""
out, notes = bibnorm.normalize_bib(EN)
check("(e) 英文 @book 的 edition 被并进 title（逗号 + 括号）",
      "title  = {The Uses of Argument, (Updated Edition)}" in out, out.strip()[:90])
check("(e) 原来那行 `edition` 消失（不重复印版次）",
      "edition" not in out.replace("(Updated Edition)", ""))
check("(e) 改动写进了 notes（会进「改动说明」，可追溯）",
      any("版次并入 title" in n for n in notes), f"{notes}")

out2, _ = bibnorm.normalize_bib(EN.replace("{Updated Edition}", "{(2nd ed.)}"))
check("(e) 版次本身已带括号 → 不出现双括号",
      "…" not in out2 and "((2nd ed.))" not in out2 and ", (2nd ed.)}" in out2, out2.strip()[:90])

# 反例：中文书（书名含汉字）→ 一个字都不许动
CN = """@book{k2,
  title   = {逻辑研究},
  edition = {第2版},
  year    = {2003}
}
"""
out3, _ = bibnorm.normalize_bib(CN)
check("(e) 反例：中文书的 `edition` **不动**（逐字保留）",
      "edition = {第2版}" in out3 and "（第2版）" not in out3, out3.strip()[:90])

# 反例：SEP 词条（edition 是存档季节）→ 不动
SEPB = """@incollection{k3,
  title     = {The Normative Status of Logic},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  edition   = {Winter 2022},
  year      = {2022}
}
"""
out4, _ = bibnorm.normalize_bib(SEPB)
check("(e) 反例：SEP 词条的 `edition` **不动**（存档季节）",
      "edition   = {Winter 2022}" in out4 or "edition = {Winter 2022}" in out4, out4.strip()[:100])

# 反例：没有 title → 不搬（不猜）
out5, _ = bibnorm.normalize_bib("""@book{k4,
  edition = {2nd},
  year = {2003}
}
""")
check("(e) 反例：没有 `title` → 不搬，`edition` 原样留着",
      re.search(r"(?m)^\s*edition\s*=\s*\{2nd\}", out5) is not None, out5.strip()[:80])

# 反例：书名里已经写了这个版次 → 不重复搬
out6, _ = bibnorm.normalize_bib("""@book{k5,
  title   = {The Uses of Argument, (Updated Edition)},
  edition = {Updated Edition},
  year    = {2003}
}
""")
check("(e) 反例：书名里已有该版次 → **不重复搬**（没有第二个括号）",
      out6.count("(Updated Edition)") == 1, out6.strip()[:110])

# 反例：非 @book → 不动
out7, _ = bibnorm.normalize_bib("""@incollection{k6,
  title     = {A Chapter},
  booktitle = {B},
  edition   = {2nd},
  year      = {2003}
}
""")
check("(e) 反例：非 `@book` 的 `edition` → 不动",
      re.search(r"(?m)^\s*edition\s*=\s*\{2nd\}", out7) is not None
      and "A Chapter" in out7, out7.strip()[:90])

# 不变量：本来就没有 edition 的英文书 → 逐字节不变
PLAIN = """@book{k7,
  author = {A, B.},
  title  = {A Book},
  year   = {2003}
}
"""
out8, _ = bibnorm.normalize_bib(PLAIN)
check("(e) 不变量：没有 `edition` 的英文书 → **逐字节不变**", out8 == PLAIN, repr(out8[:60]))

print("\n── (f) Word 通道也要搬：窄版本 `move_book_editions()`（只动命中那一条）──")
WORD = """% ── 由文末参考文献区生成 ──

@book{Garson2013,
  author    = {Garson, J. W.},
  title     = {Modal Logic for Philosophers},
  edition   = {2nd ed.},
  publisher = {Cambridge University Press},
  address   = {Cambridge},
  year      = {2013}
}

@book{Other2013,
  author    = {Someone, A.},
  title     = {逻辑研究},
  edition   = {第2版},
  year      = {2013}
}
"""
w_out, w_notes = bibnorm.move_book_editions(WORD)
check("(f) 英文书那一本被搬（title 末尾加 `, (2nd ed.)`，edition 行消失）",
      "title     = {Modal Logic for Philosophers, (2nd ed.)}" in w_out
      and "edition   = {2nd ed.}" not in w_out, w_out[:160])
check("(f) 改动有记录（会打进运行日志）", any("版次并入 title" in n for n in w_notes), f"{w_notes}")
check("(f) 反例：中文书那一条**逐字节未动**（含缩进与字段顺序）",
      """@book{Other2013,
  author    = {Someone, A.},
  title     = {逻辑研究},
  edition   = {第2版},
  year      = {2013}
}""" in w_out, "")
check("(f) 其它字段逐字节保留（author/publisher/address/year 与缩进都没被重排）",
      "  author    = {Garson, J. W.}," in w_out
      and "  publisher = {Cambridge University Press}," in w_out
      and "  address   = {Cambridge}," in w_out, "")
check("(f) 不变量：没有可搬的条目时**整份文本逐字节不变**",
      bibnorm.move_book_editions("""@book{k9,
  title = {A Book},
  year  = {2001}
}
""")[0] == """@book{k9,
  title = {A Book},
  year  = {2001}
}
""", "")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
