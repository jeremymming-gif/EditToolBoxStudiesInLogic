#!/usr/bin/env python3
r"""B-301 S2：模板**占位作者**不得被当成真实作者（案例 82/02）。

缺陷
----
82/02 的模板稿在题名下一段留着 `First AuthorSecond Author`（docx 里两行
`First Author` / `Second Author` 被 plain 连写）。`wordbody.parse_docx` 的
head 作者直赋值（`Engine/slcn/wordbody.py:505-518`）对该段**无形态校验**，
原样写进 `doc.author` → docx_audit 把 82/02 误判为 `author_ok=1`
（B-292 冻结时作为假阳性披露）。`wordmeta._author_row_name()` 的单段分支
同样缺占位校验（`Author Name` 这类形态会被当人名）。

断言
----
  ① 黑名单逐条：`_looks_like_placeholder_author` 判真（含 NFKC/大小写/去空白
     标点数字后的连写形态，如 `First AuthorSecond Author`、`xxx@xxx.xxx`）。
  ② 正例不误伤：`Alex Sample` / `Mei Lin` / `Oliver Board` 等真实姓名判假，
     `_author_row_name` 原样返回。
  ③ `_author_row_name` / `head_author_after` 对 82/02 形态返回空。
  ④ `parse_docx` 端到端（根因入口 wordbody）：占位作者 → `doc.author` 为空；
     同一 fixture 换真实姓名 → 正常赋值。
  ⑤ `_is_title_candidate` 行为不变（本次补丁没有改它）。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 tests/test_b301_placeholder_author.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import wordbody, wordmeta  # noqa: E402
from docx_fixture_gen import make_docx  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


# ───────────────────────── 1. 黑名单逐条 ─────────────────────────
_BLACKLIST = (
    "First Author",
    "Second Author",
    "First AuthorSecond Author",      # 82/02 实测连写形态
    "first author",                   # 小写
    "FIRST AUTHOR",                   # 大写
    "First  Author",                  # 多空白
    "First Author 1, Second Author 2",  # 去尾标数字后仍是占位
    "Author",
    "Author Name",
    "Authors",
    "Corresponding Author",
    "Sample Author",
    "xxx",
    "xxx@",
    "xxx@xxx.xxx",
)
for _s in _BLACKLIST:
    check(f"placeholder: {_s!r}", wordmeta._looks_like_placeholder_author(_s))
check("placeholder: 全角 NFKC", wordmeta._looks_like_placeholder_author("Ａｕｔｈｏｒ Ｎａｍｅ"))
check("placeholder: 空串不是占位", not wordmeta._looks_like_placeholder_author(""))
check("placeholder: 纯标点不是占位", not wordmeta._looks_like_placeholder_author("---"))

# ───────────────────────── 2. 正例不误伤 ─────────────────────────
for _s in ("Alex Sample", "Mei Lin", "Oliver Board", "Li Lei", "Zhang Wei"):
    check(f"real name: {_s!r} 不判占位", not wordmeta._looks_like_placeholder_author(_s))
    check(f"real name: {_s!r} 原样返回", wordmeta._author_row_name(_s) == _s,
          wordmeta._author_row_name(_s))
# 姓/名里出现 author 词但不是完整占位形态 → 不误伤（只是既有判据可能不认它）
check("real name: 'John Author' 不判占位",
      not wordmeta._looks_like_placeholder_author("John Author"))

# ───────────────────────── 3. 82/02 形态：返回空 ─────────────────────────
check("_author_row_name: 82/02 连写 → 空",
      wordmeta._author_row_name("First AuthorSecond Author") == "")
check("_author_row_name: 'First Author' → 空",
      wordmeta._author_row_name("First Author") == "")
_head82 = ["A Synthetic Title", "First AuthorSecond Author",
           "Received 2020-01-01 First Author XXX", "Abstract"]
check("head_author_after: 82/02 形态 → 空",
      wordmeta.head_author_after(_head82, 0) == "")

# ───────────────────────── 4. parse_docx 端到端（根因入口） ─────────────────────────
_ABS = "This abstract states the synthetic problem and the synthetic method. " * 12


def _parse_with_author(author_line: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "synthetic.docx")
        make_docx(p, paras=(
            "Studies in Logic, Vol. 1, No. 1 (2020): 1-10",
            "A Synthetic Title on Placeholder Authors",
            author_line,
            "Department of Philosophy, Example University",
            "Abstract", _ABS,
            "1 Introduction", "Synthetic body text.",
            "References", "[1] Example. A Synthetic Book. 2000.",
        ))
        return wordbody.parse_docx(p).author or ""


check("parse_docx: 占位作者不写 doc.author",
      _parse_with_author("First AuthorSecond Author") == "")
check("parse_docx: 真实姓名照常写 doc.author",
      _parse_with_author("Alex Sample") == "Alex Sample")

# ───────────────────────── 5. _is_title_candidate 行为不变 ─────────────────────────
check("title: 正常英文题名仍可当候选",
      wordmeta._is_title_candidate("A Synthetic Study of Example Things"))
check("title: 模板样例题名仍可当候选",
      wordmeta._is_title_candidate(wordmeta._TPL_SAMPLE_TITLE))
check("title: 邮箱仍不得当候选",
      not wordmeta._is_title_candidate("author@example.com"))
check("title: 模板页眉仍不得当候选",
      not wordmeta._is_title_candidate("Studies in Logic, Vol. 1, No. 1 (2020): 1-10"))
check("title: 纯单位行仍不得当候选",
      not wordmeta._is_title_candidate("Department of Philosophy, Example University"))
check("title: 占位作者行仍不得当候选",
      not wordmeta._is_title_candidate("First Author"))

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
