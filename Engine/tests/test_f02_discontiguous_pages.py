#!/usr/bin/env python3.14
r"""F-02（冻结候选集业务试用）：不连续多页码引用落成纯文本 → 单个 \\parencite。

夹具逐字取自问题单（**不改名、不改字符**）：

  · ``([2, pp. 154, 165])``                 —— 逗号分隔的不连续页码；
  · ``([12, pp.\,75--76, 88–89])``          —— 含 LaTeX 细空格 ``\,``、双连字符
    ``--``、Unicode en-dash ``–``；
  · 对照（本批已支持、不得回退）：``([18, p. 398])``、``([18, pp. 398-401])``。

本文件只测**纯文本 → 单个 \\parencite 命令**这一层（真实引擎函数、无 mock）：

  A 两个原样样本各转成 1 个 \\parencite，页码可选参数原样保留、无双括号、
    无二次包装；
  B 特殊字符变体（en-dash / 双连字符 / 细空格 / 页数列表长度）不丢字符；
  C 键走编号→键映射（num2key），不按字母序猜；越界编号原样保留 + 一条 issue；
  D 不误转：数学、\\verb、\\url、TeX 注释、已有 \\parencite 可选参；
  E 既有行为回归：``([18, p. 398])``、``([18, pp. 398-401])``、
    ``([18,19])`` 多编号、中文 ``（[18，第398页]）``；
  F ②.5 先于 ⑤ 的规则：转换后的可选参数经 ``apply_rules`` 不被破坏。

不联网、不写仓库、不碰 corpus、不编译。用法（cp314 ABI）：

    cd Engine && PYTHONPATH="$PWD/vendor:$PWD" /opt/homebrew/bin/python3.14 \
        tests/test_f02_discontiguous_pages.py
"""

from __future__ import annotations

import os
import re
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import rules as R                        # noqa: E402
from slcn.wordcitebody import (                    # noqa: E402
    _NUM_GROUP_LOC,
    link_numbered_citations,
)

FAILS: list[str] = []


def check(name: str, cond, detail="") -> None:
    ok = bool(cond)
    line = ("PASS  " if ok else "FAIL  ") + name
    if not ok:
        msg = " ⏎ ".join(str(detail).splitlines()) if detail != "" else ""
        line += ("  " + msg) if msg else ""
        FAILS.append(name)
    print(line)


def cites(tex: str) -> list[str]:
    """取出所有 \\parencite 的完整命令（含可选参数）。"""
    return re.findall(r"\\parencite(?:\[[^\]]*\])?\{[^{}]*\}", tex)


# 编号→键映射：刻意让键名与编号排序无关（证明不是按字母序猜）。
# 2→Zed、12→Alpha、18→Middle、19→Last。
N2K = {2: "ZedKey", 12: "AlphaKey", 18: "MiddleKey", 19: "LastKey"}

# ── A 两个原样样本 ────────────────────────────────────────────────────────
SAMPLE_A = r"([2, pp. 154, 165])"
SAMPLE_B = r"([12, pp.\,75--76, 88–89])"

print("── A 原样样本转成单个 \\parencite ──")
for name, src, key, loc in (
    ("A1 `([2, pp. 154, 165])`", SAMPLE_A, "ZedKey", "pp. 154, 165"),
    ("A2 `([12, pp.\\,75--76, 88–89])`", SAMPLE_B, "AlphaKey",
     r"pp.\,75--76, 88–89"),
    # 对照（本批已支持、不得回退）：定位串本身按既有口径规范化（~ / --）。
    ("A3 `([18, p. 398])` 对照", r"([18, p. 398])", "MiddleKey", "p.~398"),
    ("A4 `([18, pp. 398-401])` 对照", r"([18, pp. 398-401])", "MiddleKey",
     "pp.~398--401"),
):
    out, n, issues = link_numbered_citations(src, N2K, "en")
    found = cites(out)
    check(name + " 转成 1 处引用", n == 1, (n, out, issues))
    check(name + " 恰好一个 \\parencite 命令", len(found) == 1, found)
    check(name + " 键来自编号映射（非字母序）", found == [rf"\parencite[{loc}]{{{key}}}"],
          (found, out))
    check(name + " 页码可选参数原样保留", ("[" + loc + "]") in out, out)
    check(name + " 无双括号/原始方括号残留", "(" not in out.replace("\\parencite", "")
          and "[2," not in out and "[12," not in out, out)
    check(name + " 无 issue", issues == [], issues)

# ── B 特殊字符变体 ────────────────────────────────────────────────────────
print("── B 特殊字符变体（不丢字符）──")
# 不连续列表（≥2 段）按问题单要求逐字保留 ``\,`` / ``--`` / en-dash / 逗号。
# 单区间（仅 1 段）沿用本批既有口径规范化为 `~` / `--`，不属 F-02 改动范围。
DISCONTIGUOUS = [
    SAMPLE_B,                          # 细空格 + -- + en-dash + 逗号列表
    r"([2, pp.\,154, 165])",           # 细空格 + 逗号列表
    r"([2, pp. 154, 165, 170])",       # 三段不连续
    r"([2, pp. 154, 165–170])",        # 列表 + en-dash 区间混排
]
for src in DISCONTIGUOUS:
    out, n, issues = link_numbered_citations(src, N2K, "en")
    found = cites(out)
    check("B " + src + " 幂等转成单个命令",
          n == 1 and len(found) == 1 and issues == [], (out, n, issues))
    # src 形如 `([2, pp. 154, 165])` / `([12, pp.\,75--76, 88–89])`：
    # 抠出从 `pp.` 到倒数第二个 `]` 的整段定位串（含 pp. 与细空格，逐字）。
    loc_full = src[src.index("pp."):src.rindex("]")].strip()
    check("B " + src + " 定位串逐字保留",
          ("[" + loc_full + "]") in out, (loc_full, out))
for src, expected in (
    (r"([2, pp. 154–165])", r"\parencite[pp.~154--165]{ZedKey}"),
    (r"([2, pp. 154--165])", r"\parencite[pp.~154--165]{ZedKey}"),
):
    out, n, issues = link_numbered_citations(src, N2K, "en")
    check("B 单区间沿用既有规范化 " + src,
          out == expected and n == 1 and issues == [], (out, n, issues))

# ── C 键映射与越界 ────────────────────────────────────────────────────────
print("── C 编号→键映射（不猜键）──")
out, n, issues = link_numbered_citations(SAMPLE_A, {18: "MiddleKey"}, "en")
check("C 越界编号 2 原样保留、不猜键、记一条 warning",
      out == SAMPLE_A and n == 0 and len(issues) == 1 and "超出" in issues[0],
      (out, n, issues))
out, n, issues = link_numbered_citations(SAMPLE_A, N2K, "en")
check("C 2 映射到 ZedKey 而非按字母序取 AlphaKey",
      out == r"\parencite[pp. 154, 165]{ZedKey}", out)

# ── D 不误转（保护项）─────────────────────────────────────────────────────
print("── D 保护项不误转 ──")
for src in (
    r"$[2, pp. 154, 165]$",
    r"\verb|[2, pp. 154, 165]|",
    r"\url{http://example.org/[2, pp. 154, 165]}",
    r"% [2, pp. 154, 165]",
    r"(\parencite[2]{AlphaKey})",
):
    out, n, issues = link_numbered_citations(src, N2K, "en")
    check("D 原样保留 " + src, out == src and n == 0, (out, n, issues))
check("D 正则自身不命中纯编号多引用 `[18,19]`",
      _NUM_GROUP_LOC.search(r"[18,19]") is None, "")

# ── E 既有行为回归 ────────────────────────────────────────────────────────
print("── E 既有行为回归 ──")
for src, expected in (
    (r"([18, p. 398])", r"\parencite[p.~398]{MiddleKey}"),
    (r"([18, pp. 398-401])", r"\parencite[pp.~398--401]{MiddleKey}"),
    (r"([18,19])", r"(\parencite{MiddleKey,LastKey})"),
):
    out, n, issues = link_numbered_citations(src, N2K, "en")
    check("E " + src + " 行为保持", out == expected and n == 1, (out, n, issues))
out, n, issues = link_numbered_citations("（[18，第398页]）", N2K, "zh")
check("E 中文 `（[18，第398页]）` 行为保持",
      out == "（\\parencite{MiddleKey}，第398页）" and n == 1, (out, n, issues))

# ── F ②.5 在 ⑤ 之前：规则不破坏转换后的可选参数 ──────────────────────────
print("── F 转换后可选参数经 apply_rules 保持 ──")
for src in (SAMPLE_A, SAMPLE_B):
    converted, _n, _iss = link_numbered_citations(src, N2K, "en")
    after = R.apply_rules(converted, lang="en", safe_only=True).text
    check("F ⑤ 后仍完整保留 " + src,
          after == converted, (converted, after))

print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
