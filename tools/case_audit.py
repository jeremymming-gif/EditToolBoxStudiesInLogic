#!/usr/bin/env python3
r"""案例库体检 `tools/case_audit.py`（第二十二批，第 122 条）。

**为什么需要**：案例法是只增不改的档案，121 条 / 27 万字符之后，人工很难保证
「编号没断、INDEX 没过期、状态行指得到、没有复制粘贴、旧路径没被误当现行」。
本脚本把这些变成可执行的检查；默认只读，不改任何文件。

用法（在公开仓根）：

    python3 tools/case_audit.py                       # 自动找笔记仓 cases/
    python3 tools/case_audit.py --cases ../EditToolBox-notes/cases
    python3 tools/case_audit.py --check                # CI/自查用：有错误退 1
    python3 tools/case_audit.py --no-similarity        # 跳过相似度扫描（更快）

**错误（--check 退 1）**：
  - 条目编号不连续 / 重复 / 与文件范围不符（01-30、31-61、62+）；
  - INDEX.md 或 DIGEST.md 过期（调 `tools/gen_case_index.py --check`）；
  - `> 状态：已被第 N 条取代` 指向不存在的条目。

**警告（只报告，不退非 0）**：
  - 两条正文 3-gram Jaccard > 0.5（疑似复制粘贴）；
  - 反引号里的「当前布局路径」不存在，且所在行附近没有历史叙述词；
  - 无法识别的状态前缀。

输出是本地报告，不含作者信息，不写文件。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from collections import Counter

CASE_RE = re.compile(r"^### (\d+)\. (.+)$")
STATUS_RE = re.compile(r"^>\s*状态[:：]\s*(.+?)\s*$")
SUPERSEDED_RE = re.compile(r"已被第\s*(\d+)\s*条取代")
STATUS_PREFIXES = ("已被取代", "只作历史", "部分历史", "已发布")
HIST_WORDS = ("已删", "删除", "已退役", "退役", "已移除", "移除", "历史", "曾", "当时",
              "作废", "废弃", "不再", "只作")
PATH_RE = re.compile(r"`((?:Engine|tools|sources-|slcn/|tests/|docs/|编辑笔记/)[\w./\-]+"
                     r"\.(?:py|md|sh|swift|json|tex|sty|yml|toml|txt))`")
CURRENT_PREFIXES = ("Engine/", "tools/", "sources-", "slcn/", "tests/", "docs/", "编辑笔记/")

#: 案例是历史档案：这些路径**曾经存在**、后来搬去了别的仓（排版手册 → 稿件仓，
#: Engine/HANDOFF.md → 笔记仓 HANDOFF.md）。它们不该被当成「当前布局失效」报警。
HIST_PATH_PREFIXES = ("编辑笔记/排版手册/", "编辑笔记/流程化经验书.md",
                      "Engine/HANDOFF.md")


def find_cases_dir(explicit: str = "") -> str | None:
    if explicit:
        p = os.path.abspath(os.path.expanduser(explicit))
        return p if os.path.isdir(p) else None
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 引擎的 paths 模块认识三个仓的布局；没有它也能用同级笔记仓兜底
    cands = [os.path.join(repo, "..", "EditToolBox-notes", "cases"),
             os.path.join(repo, "Engine", "docs", "cases")]
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isdir(c):
            return c
    return None


def parse_cases(cases_dir: str) -> list[dict]:
    """返回 [{num,title,file,status,body,line_start}]（只读）。"""
    rows: list[dict] = []
    files = sorted(f for f in os.listdir(cases_dir)
                   if f.endswith(".md") and f not in ("INDEX.md", "DIGEST.md", "TEMPLATE.md"))
    for f in files:
        lines = open(os.path.join(cases_dir, f), encoding="utf-8").read().split("\n")
        heads = [(i, int(m.group(1)), m.group(2)) for i, ln in enumerate(lines)
                 for m in [CASE_RE.match(ln)] if m]
        for k, (i, num, title) in enumerate(heads):
            end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
            body = "\n".join(lines[i:end])
            status = ""
            for ln in lines[i:end]:
                sm = STATUS_RE.match(ln)
                if sm:
                    status = sm.group(1)
                    break
            rows.append({"num": num, "title": title, "file": f, "status": status,
                         "body": body, "line_start": i})
    rows.sort(key=lambda r: r["num"])
    return rows


def check_numbering(rows: list[dict], errors: list[str], warns: list[str]) -> None:
    nums = [r["num"] for r in rows]
    if len(nums) != len(set(nums)):
        dup = [n for n, c in Counter(nums).items() if c > 1]
        errors.append(f"条目编号重复：{sorted(dup)}")
    if nums:
        missing = [n for n in range(1, max(nums) + 1) if n not in set(nums)]
        if missing:
            errors.append(f"条目编号不连续，缺号：{missing[:20]}{' …' if len(missing) > 20 else ''}")
    # 初始文件的内容范围（只增不改）
    ranges = {"01-30.md": range(1, 31), "31-61.md": range(31, 62)}
    for name, rng in ranges.items():
        bad = [r["num"] for r in rows if r["file"] == name and r["num"] not in rng]
        if bad:
            errors.append(f"{name} 里出现范围外条目号：{paid(bad)}")
    bad62 = [r["num"] for r in rows if r["file"] == "62+.md" and r["num"] < 62]
    if bad62:
        errors.append(f"62+.md 里出现 <62 的条目号：{paid(bad62)}")


def paid(xs: list[int]) -> str:
    return "、".join(str(x) for x in xs[:10])


def check_status(rows: list[dict], errors: list[str], warns: list[str]) -> None:
    nums = {r["num"] for r in rows}
    for r in rows:
        s = r["status"]
        if not s:
            continue
        m = SUPERSEDED_RE.search(s)
        if m and int(m.group(1)) not in nums:
            errors.append(f"第 {r['num']} 条状态指向不存在的第 {m.group(1)} 条")
        if not s.startswith(STATUS_PREFIXES):
            warns.append(f"第 {r['num']} 条状态前缀不认识：{s[:30]}")


def check_index(cases_dir: str, errors: list[str]) -> None:
    gen = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tools", "gen_case_index.py")
    if not os.path.isfile(gen):
        warns_msg = f"找不到 {gen}，跳过索引一致性检查"
        errors.append(warns_msg)
        return
    r = subprocess.run([sys.executable, gen, "--cases", cases_dir, "--check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        for ln in (r.stdout + r.stderr).splitlines():
            if ln.startswith("FAIL"):
                errors.append(ln[len("FAIL"):].strip() or "案例索引/速查过期")


def _grams(text: str, n: int = 3) -> Counter:
    t = re.sub(r"\s+", "", text)
    return Counter(t[i:i + n] for i in range(max(0, len(t) - n + 1)))


def check_similarity(rows: list[dict], warns: list[str], limit: int = 10,
                     threshold: float = 0.5) -> None:
    grams = [_grams(r["body"]) for r in rows]
    pairs = []
    for i in range(len(rows)):
        a = grams[i]
        for j in range(i + 1, len(rows)):
            b = grams[j]
            union = sum((a | b).values())
            if not union:
                continue
            s = sum((a & b).values()) / union
            if s > threshold:
                pairs.append((s, rows[i]["num"], rows[j]["num"]))
    pairs.sort(reverse=True)
    for s, a, b in pairs[:limit]:
        warns.append(f"第 {a} 条与第 {b} 条正文相似度 {s:.2f}（> {threshold}，疑似复制）")
    if len(pairs) > limit:
        warns.append(f"……另有 {len(pairs) - limit} 对高相似条目未列出")


def check_paths(cases_dir: str, rows: list[dict], warns: list[str]) -> None:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 案例里也会引用笔记仓自己的工具（如 tools/gui_fast_test.py）——把笔记仓根也纳入解析
    roots = [repo, os.path.join(repo, "Engine"), os.path.dirname(cases_dir)]
    texts = {r["num"]: r["body"].split("\n") for r in rows}
    for r in rows:
        lines = texts[r["num"]]
        for idx, ln in enumerate(lines):
            for m in PATH_RE.finditer(ln):
                p = m.group(1)
                if not p.startswith(CURRENT_PREFIXES):
                    continue
                if p.startswith(HIST_PATH_PREFIXES):
                    continue
                if any(os.path.exists(os.path.join(root, p)) for root in roots):
                    continue
                window = "\n".join(lines[max(0, idx - 2): idx + 3])
                if any(w in window for w in HIST_WORDS):
                    continue
                warns.append(f"第 {r['num']} 条引用的当前布局路径不存在：{p}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="案例库体检（只读）")
    ap.add_argument("--cases", default="", help="案例目录（默认自动探测笔记仓）")
    ap.add_argument("--check", action="store_true", help="有错误时退 1")
    ap.add_argument("--no-similarity", action="store_true", help="跳过相似度扫描")
    args = ap.parse_args(argv)

    cases_dir = find_cases_dir(args.cases)
    if not cases_dir:
        print("≡ case_audit：找不到案例目录（公开仓/CI 正常）——跳过")
        return 0

    rows = parse_cases(cases_dir)
    errors: list[str] = []
    warns: list[str] = []
    check_numbering(rows, errors, warns)
    check_status(rows, errors, warns)
    check_index(cases_dir, errors)
    if not args.no_similarity:
        check_similarity(rows, warns)
    check_paths(cases_dir, rows, warns)

    print(f"案例库体检：{cases_dir}")
    print(f"  条目 {len(rows)} 条；错误 {len(errors)}；警告 {len(warns)}")
    for e in errors:
        print(f"  FAIL  {e}")
    for w in warns[:20]:
        print(f"  WARN  {w}")
    if len(warns) > 20:
        print(f"  WARN  ……另有 {len(warns) - 20} 条警告未列出")
    if errors:
        print("结论：有错误（先修错误；警告可人工判断）")
        return 1 if args.check else 0
    print("结论：无错误" + ("；有警告，建议人工过一眼" if warns else "；无警告"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
