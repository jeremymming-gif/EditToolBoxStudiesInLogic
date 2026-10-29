#!/usr/bin/env python3
r"""B-268 S1：Word 通道英文前置（titleEN/authorEN）的合法性校验与恢复。

覆盖（fixtures 全为抽象样例，不含任何真实稿件/作者/邮箱）：
  · tail 块里邮箱行/单位行不得被当成 titleEN，作者行在邮箱/单位行之上仍可命中；
  · 模板页眉（`Studies in Logic, Vol. …`）与纯单位行不得当题名；
  · head 块在 tail 未命中时按「题名下一段姓名 + 后随块」恢复 authorEN；
  · 无法可靠恢复时**保留空**（绝不猜名）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b268_frontmatter.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if os.path.join(_REPO, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO, "tools"))

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


_ABS = "This abstract states the synthetic problem and the synthetic method. " * 12

# ───────────────────────── 1. title/author 合法性校验（纯函数） ─────────────────────────
check("title: 正常英文题名可当候选", wordmeta._is_title_candidate(
    "A Synthetic Study of Example Things"))
check("title: 邮箱不得当候选", not wordmeta._is_title_candidate("author@example.com"))
check("title: 含 @ 的串不得当候选", not wordmeta._is_title_candidate("A Title @ Example"))
check("title: 模板页眉不得当候选", not wordmeta._is_title_candidate(
    "Studies in Logic, Vol. 1, No. 1 (2020): 1-10"))
check("title: 纯单位行不得当候选", not wordmeta._is_title_candidate(
    "Department of Philosophy, Example University"))
check("title: Abstract 标签不得当候选", not wordmeta._is_title_candidate("Abstract"))

# ───────────────────────── 2. tail 块：邮箱/单位行挡在 Abstract 正上方 ─────────────────────────
_tail = [
    "A Synthetic Title for the Tail Block",
    "Example Personname",
    "Department of Philosophy, Example University",
    "person@example.edu",
    "Abstract",
    _ABS,
]
_t = wordmeta.parse_tail_metadata(_tail, 0)
check("tail: titleEN 命中（不是邮箱）", _t.get("title_en") ==
      "A Synthetic Title for the Tail Block", _t.get("title_en"))
check("tail: authorEN 命中（跨过单位/邮箱行）", _t.get("author_en") ==
      "Example Personname", _t.get("author_en"))

# 单位行紧贴 Abstract、作者行缺失 → title 不得变成单位行
_tail2 = ["Another Synthetic Title", "Some Institute of Technology", "Abstract", _ABS]
_t2 = wordmeta.parse_tail_metadata(_tail2, 0)
check("tail: 单位行不当 title", _t2.get("title_en") == "Another Synthetic Title",
      _t2.get("title_en"))
check("tail: 没有姓名行就保持空（不猜名）", not _t2.get("author_en"), _t2.get("author_en"))

# 邮箱独占一行紧贴 Abstract → 不得当 title
_tail3 = ["Third Synthetic Title", "Third Personname", "someone@example.edu", "Abstract", _ABS]
_t3 = wordmeta.parse_tail_metadata(_tail3, 0)
check("tail: 邮箱行不当 title（单行邮箱）", _t3.get("title_en") == "Third Synthetic Title",
      _t3.get("title_en"))
check("tail: 邮箱行上方作者仍可命中", _t3.get("author_en") == "Third Personname",
      _t3.get("author_en"))

# ───────────────────────── 3. head 块：题名之后恢复 authorEN ─────────────────────────
_head = ["A Synthetic Title", "Example Personname",
         "Department of Philosophy, Example University", "Abstract", _ABS]
check("head: 题名下一段姓名命中", wordmeta.head_author_after(_head, 0) == "Example Personname")
check("head: 没有姓名段则空", wordmeta.head_author_after(
    ["A Synthetic Title", "Department of Philosophy, Example University", "Abstract"], 0) == "")

# ───────────────────────── 4. 模板页眉 + 真标题（head 题名跳过页眉） ─────────────────────────
with tempfile.TemporaryDirectory() as _td:
    _docx = os.path.join(_td, "synthetic.docx")
    make_docx(_docx, paras=(
        "Studies in Logic, Vol. 1, No. 1 (2020): 1-10",
        "A Synthetic Title Below the Template Header",
        "Example Personname",
        "Department of Philosophy, Example University",
        "Abstract",
        _ABS,
        "1 Introduction",
        "Synthetic body text.",
        "References",
        "[1] Example. A Synthetic Book. 2000.",
    ))
    _doc = wordbody.parse_docx(_docx)
    check("parse_docx: 题名跳过模板页眉",
          _doc.title == "A Synthetic Title Below the Template Header", _doc.title)
    check("parse_docx: titleEN 不是页眉/邮箱",
          "@" not in (_doc.title_en or "") and "Studies in Logic, Vol" not in (_doc.title_en or ""),
          _doc.title_en)

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
