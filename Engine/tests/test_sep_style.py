#!/usr/bin/env python3
"""《斯坦福哲学百科》（SEP）条目的固定形状（第 84 条，2026-10-15 用户提的）。

三件事一起钉住：
  (a) **规范文本**（`slcn/refmark.py`，它同时是给 AI 的 system prompt 与编辑看的任务书）
      里必须写明 SEP 的形状与「照官网 bib 抄 + 按表改形状」；
  (b) **判据**（`slcn/bibcheck.py`）：SEP 词条**没有页码**，不许再报「必补 pages」；
  (c) **规范化**（`slcn/bibnorm.py`）：把官网那份 biblatex 抄进来以后，
      `howpublished = {\\url{…}}` 要变成我们的 `url`，而且**只**对 SEP 生效。

反例与正例并重：非 SEP 的条目必须**逐字节不变**。
"""
from __future__ import annotations

import re

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slcn import bibcheck, bibnorm, refmark  # noqa: E402

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


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 规范文本：SEP 形状必须写在 refmark 里（AI 与编辑看的是同一份）──")
spec = refmark.SPEC_MD
src = refmark.SOURCES_MD

check("(a) SPEC 写明类型是 @incollection",
      "@incollection{Steinberger2022" in spec)
check("(a) SPEC 给出固定 booktitle",
      "booktitle = {The Stanford Encyclopedia of Philosophy}" in spec)
check("(a) SPEC 给出固定 publisher",
      "Metaphysics Research Lab, Stanford University" in spec)
check("(a) SPEC 要求 url 是**存档**永久链接（archives/…），不是当前版短链接",
      "archives/win2022/entries/logic-normative/" in spec and "当前版" in spec)
check("(a) SPEC 说明 edition = 存档季节+年份，且**有就写、没有不补**",
      "Winter 2022" in spec and "没写就不补" in spec and "不要从 URL 里" in spec)
check("(a) SPEC 说明 editor 照该版存档页写（历年编者不同）",
      "更早的存档只有" in spec)
check("(a) SPEC 说明 SEP 不写 pages / address",
      "不写 `pages`" in spec and "不写 `address`" in spec)
check("(a) SPEC 有「官网 biblatex → 我们」的改写对照表（howpublished/花括号/urldate/key）",
      "howpublished = {\\url{…}}" in spec and "url = {…}" in spec
      and "urldate = {…}" in spec and "sep-logic-preference" in spec)
check("(a) SPEC 提醒改 key 必须同时确认正文（否则引用悬空）",
      "改 key 必须同时确认正文" in spec)
check("(a) SPEC 讲了作者倒装与「印出来一样」的实证",
      "两种写法印出来完全一样" in spec)
check("(a) SOURCES 把「SEP 词条页自带的官方 BibTeX」列为取数来源",
      "官方 BibTeX" in spec or "官方 BibTeX" in src, "见 §二 第 5 条")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 判据：SEP 词条不许再报「必补 pages」 ──")

SEP_OK = """\
@incollection{Steinberger2022,
  author    = {Steinberger, F.},
  title     = {The Normative Status of Logic},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  editor    = {Zalta, E. N. and Nodelman, U.},
  edition   = {Winter 2022},
  publisher = {Metaphysics Research Lab, Stanford University},
  year      = {2022},
  url       = {https://plato.stanford.edu/archives/win2022/entries/logic-normative/}
}
"""

rep = bibcheck.check(SEP_OK)
must_msgs = [i.detail for i in rep.issues if i.severity == "must"]
check("(b) 上面这条 SEP 不被报「必补 pages」",
      not any("pages" in m for m in must_msgs), f"{must_msgs}")
check("(b) 判据函数认得出它是 SEP（只看 booktitle，大小写不敏感）",
      bibcheck.is_sep_entry({"booktitle": "the stanford encyclopedia of philosophy"}))
check("(b) 官网那种**带保护花括号**的 booktitle 也认得出（第一次实现就栽在这里）",
      bibcheck.is_sep_entry({"booktitle": "The {Stanford} Encyclopedia of Philosophy"}))
check("(b) 反例：booktitle 不是 SEP 时不认",
      not bibcheck.is_sep_entry({"booktitle": "Handbook of Logic and Language"}))

# 反例：**同样形状**但不是 SEP 的 @incollection 缺 pages → 仍然必补（一个字都不许松）
NON_SEP = """\
@incollection{Someone2019,
  author    = {Someone, A.},
  title     = {A Chapter},
  booktitle = {Handbook of Logic and Language},
  publisher = {Elsevier},
  year      = {2019}
}
"""
rep2 = bibcheck.check(NON_SEP)
must2 = [i.detail for i in rep2.issues if i.severity == "must"]
check("(b) 反例：非 SEP 的 @incollection 缺 pages → **仍然**必补",
      any("pages" in m for m in must2), f"{must2}")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 规范化：把官网那份 biblatex 抄进来要能用 ──")

# 用户给的官网原文（一字不改）
OFFICIAL = """\
@InCollection{sep-logic-preference,
\tauthor       =\t{Liu, Fenrong and van der Torre, Leon},
\ttitle        =\t{{Preference Logic}},
\tbooktitle    =\t{The {Stanford} Encyclopedia of Philosophy},
\teditor       =\t{Edward N. Zalta and Uri Nodelman},
\thowpublished =\t{\\url{https://plato.stanford.edu/archives/fall2025/entries/logic-preference/}},
\tyear         =\t{2025},
\tedition      =\t{{F}all 2025},
\tpublisher    =\t{Metaphysics Research Lab, Stanford University}
}
"""
out, notes = bibnorm.normalize_bib(OFFICIAL)
_has_url_field = lambda s: bool(re.search(r"(?m)^\s*url\s*=", s))
check("(c) howpublished 消失、换成我们的 url",
      "howpublished" not in out and _has_url_field(out)
      and "https://plato.stanford.edu/archives/fall2025/entries/logic-preference/" in out,
      [n for n in notes if "SEP" in n])
check("(c) key **不变**（工具绝不改 key，改 key 会让正文引用悬空）",
      "@InCollection{sep-logic-preference," in out.replace("@incollection", "@InCollection"))
check("(c) edition / booktitle 的**保护花括号原样保留**（工具不静默剥，交给规范去写）",
      "{The {Stanford} Encyclopedia of Philosophy}" in out and "{{F}all 2025}" in out)
check("(c) urldate 之类不写（既有 DROP_FIELDS 行为）",
      "urldate" not in out)

# 反例：同样的 howpublished，但 booktitle 不是 SEP → 一个字节都不许动
NON_SEP_HOW = """\
@incollection{Other2020,
  author       = {Other, A.},
  title        = {Something},
  booktitle    = {Handbook of Logic and Language},
  howpublished = {\\url{https://example.org/x}},
  year         = {2020}
}
"""
out3, notes3 = bibnorm.normalize_bib(NON_SEP_HOW)
check("(c) 反例：非 SEP 条目的 howpublished **原样保留**",
      "howpublished = {\\url{https://example.org/x}}" in out3,
      f"notes={notes3}")
check("(c) 反例：非 SEP 条目没有被加上 url",
      not _has_url_field(out3))

# 反例：SEP 条目**同时**有 url 与 howpublished → 以 url 为准，不覆盖
BOTH = """\
@incollection{SepBoth2024,
  author       = {Somebody, B.},
  title        = {A SEP Entry},
  booktitle    = {The Stanford Encyclopedia of Philosophy},
  year         = {2024},
  url          = {https://plato.stanford.edu/archives/win2024/entries/x/},
  howpublished = {\\url{https://plato.stanford.edu/entries/x/}}
}
"""
out4, _ = bibnorm.normalize_bib(BOTH)
check("(c) 反例：已有 url 时 howpublished 被删、**url 的值不被覆盖**",
      "howpublished" not in out4
      and len(re.findall(r"(?m)^\s*url\s*=", out4)) == 1
      and "archives/win2024/entries/x/" in out4
      and "plato.stanford.edu/entries/x/" not in out4)

# 反例：SEP 条目的 howpublished 里**取不出 URL** → 原样保留，绝不猜
NOURL = """\
@incollection{SepNoUrl2024,
  author       = {Somebody, C.},
  title        = {A SEP Entry},
  booktitle    = {The Stanford Encyclopedia of Philosophy},
  year         = {2024},
  howpublished = {见官网}
}
"""
out5, _ = bibnorm.normalize_bib(NOURL)
check("(c) 反例：取不出 URL → howpublished 原样留着（不删、不编）",
      "howpublished" in out5 and not _has_url_field(out5))

# 不变量：**已经是我们形状**的 SEP 条目（成品那份）跑一遍 → 逐字节相同
DONE = """\
@incollection{Steinberger2022,
  author    = {Steinberger, F.},
  title     = {The Normative Status of Logic},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  editor    = {Zalta, E. N. and Nodelman, U.},
  edition   = {Winter 2022},
  publisher = {Metaphysics Research Lab, Stanford University},
  year      = {2022},
  url       = {https://plato.stanford.edu/archives/win2022/entries/logic-normative/}
}
"""
out6, notes6 = bibnorm.normalize_bib(DONE)
check("(c) 不变量：已经合规范的 SEP 条目**逐字节不变**",
      out6.strip() == DONE.strip(), f"notes={notes6}")

# 端到端：仓库里那份成品的 SEP 条目跑 normalize_bib → 字段一个不少
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "..", "..", "2026年第5期(90)", "10 Lei_Li", "reference.bib")
BASE = os.path.normpath(BASE)
if os.path.isfile(BASE):
    text = open(BASE, encoding="utf-8", errors="replace").read()
    import re
    m = re.search(r"@incollection\{Steinberger2022,.*?\n\}", text, re.S)
    if m:
        out7, _ = bibnorm.normalize_bib(m.group(0) + "\n")
        for fld in ("booktitle", "editor", "edition", "publisher", "url"):
            check(f"(d) 成品里的 SEP 条目 {fld} 仍在", f"{fld} " in out7 or f"{fld}=" in out7)
    else:
        check("(d) 成品 reference.bib 里能找到 Steinberger2022", False, "没找到")
else:
    print("SKIP  (d) 成品 reference.bib 不在（换了机器/目录）")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
