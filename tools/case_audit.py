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
  - INDEX.md / DIGEST.md / _generated/REVIEW-DRAFT.md 过期（调 gen_case_index.py --check）；
  - `> 状态：已被第 N 条取代` 指向不存在的条目 / 目标编号不大于源 / 自指 /
    同一状态行多个取代目标（结构性损坏才升为错误）。

**警告（只报告，不退非 0）**：
  - 两条正文 3-gram Jaccard > 0.5（疑似复制粘贴）；
  - 反引号里的「当前布局路径」不存在，且所在行附近没有历史叙述词；
  - 无法识别的状态前缀。

**补号兼容（B-390）**：`119补` / `119补2` 是第 119 条的补充案例，与生成器同口径解析：
`CASE_RE` 支持补号、排序用 `_case_sort_key`（119 < 119补 < 119补2 < 120）、
`check_numbering` 的缺号检查只看普通号（补号不占普通号）。`_generated/` 是生成物目录，
显式跳过，不进案例解析、不占编号、不计入 265。

输出是本地报告，不含作者信息，不写文件。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

#: 与 gen_case_index.py 同口径：`### 119.` / `### 119补.` / `### 119补2.`
CASE_RE = re.compile(r"^###\s+(\d+(?:补\d*)?)\.\s*(.+?)\s*$")
STATUS_RE = re.compile(r"^>\s*状态[:：]\s*(.+?)\s*$")
SUPERSEDED_RE = re.compile(r"已被第\s*(\d+(?:补\d*)?)\s*条取代")
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


def _case_sort_key(num: str) -> tuple[int, bool, int]:
    """案例号排序键（与 gen_case_index.py 一致）：119 < 119补 < 119补2 < 120。"""
    m = re.match(r"^(\d+)(?:补(\d*))?$", num)
    if not m:
        return (10 ** 9, False, 0)
    return (int(m.group(1)), m.group(2) is not None, int(m.group(2) or 0))


def parse_cases(cases_dir: str) -> list[dict]:
    """返回 [{num,title,file,status,body,line_start}]（只读，跳过 `_generated/`）。"""
    rows: list[dict] = []
    files = sorted(f for f in os.listdir(cases_dir)
                   if f.endswith(".md") and f not in ("INDEX.md", "DIGEST.md", "TEMPLATE.md")
                   and f != "_generated"                      # 显式跳过生成物目录
                   and not f.startswith("_generated" + os.sep)
                   and os.path.isfile(os.path.join(cases_dir, f)))
    for f in files:
        lines = open(os.path.join(cases_dir, f), encoding="utf-8").read().split("\n")
        heads = [(i, m.group(1), m.group(2)) for i, ln in enumerate(lines)
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
    rows.sort(key=lambda r: _case_sort_key(r["num"]))
    return rows


def check_numbering(rows: list[dict], errors: list[str], warns: list[str]) -> None:
    nums = [r["num"] for r in rows]
    if len(nums) != len(set(nums)):
        dup = [n for n, c in Counter(nums).items() if c > 1]
        errors.append(f"条目编号重复：{sorted(dup, key=_case_sort_key)}")
    # 补号不占普通号：缺号检查只看普通编号（普通号缺号仍能发现）
    plain = [int(n) for n in nums if re.fullmatch(r"\d+", n)]
    if plain:
        missing = [n for n in range(1, max(plain) + 1) if n not in set(plain)]
        if missing:
            errors.append(f"条目编号不连续，缺号：{missing[:20]}{' …' if len(missing) > 20 else ''}")
    # 初始文件的内容范围（只增不改）；补号按主号判断范围
    ranges = {"01-30.md": range(1, 31), "31-61.md": range(31, 62)}
    for name, rng in ranges.items():
        bad = [r["num"] for r in rows if r["file"] == name
               and _case_sort_key(r["num"])[0] not in rng]
        if bad:
            errors.append(f"{name} 里出现范围外条目号：{paid(bad)}")
    bad62 = [r["num"] for r in rows if r["file"] == "62+.md"
             and _case_sort_key(r["num"])[0] < 62]
    if bad62:
        errors.append(f"62+.md 里出现 <62 的条目号：{paid(bad62)}")


def paid(xs: list[str]) -> str:
    return "、".join(str(x) for x in xs[:10])


def check_status(rows: list[dict], errors: list[str], warns: list[str]) -> None:
    """取代关系最小结构检查（B-390 §2.5）：目标存在 / 目标编号必更大 / 不自指 /
    同一状态行最多一个取代目标。循环在「目标编号必更大」下天然不发生，不实现图算法。
    只警告，不改判定门槛为 fail（除结构性损坏：目标不存在、目标不更大、自指、多目标）。
    """
    by_num = {r["num"]: r for r in rows}
    for r in rows:
        s = r["status"]
        if not s:
            continue
        targets = [m.group(1) for m in SUPERSEDED_RE.finditer(s)]
        if len(targets) > 1:
            errors.append(f"第 {r['num']} 条状态行含多个取代目标：{targets}（最多一个）")
        for t in targets:
            if t not in by_num:
                errors.append(f"第 {r['num']} 条状态指向不存在的第 {t} 条")
                continue
            if t == r["num"]:
                errors.append(f"第 {r['num']} 条状态自指（取代自己）")
                continue
            if _case_sort_key(t) <= _case_sort_key(r["num"]):
                errors.append(f"第 {r['num']} 条状态取代目标第 {t} 条编号不大于源（方向错误）")
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
                errors.append(ln[len("FAIL"):].strip() or "案例索引/速查/候选清单过期")


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


def run_self_test() -> int:
    """内置自检（hermetic、只增不减、秒级）：全部读写只在临时目录内进行。

    构造约 5 条小案例库，覆盖补号解析、缺号、取代方向、_generated 跳过，
    以及「正文变更后 --check 由 0 翻 1」真门控。三产物由同目录的
    gen_case_index 生成到临时目录，check_index 的子进程也只读临时目录。
    """
    import contextlib
    import io
    import gen_case_index as gci  # 同目录兄弟工具；仅调其纯函数，不写公开仓

    print("case_audit --self-test")
    checks: list[bool] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append(bool(cond))
        print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))

    def capture(argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def write_lib(cases: Path, entries: list[tuple[str, str, str]]) -> None:
        """entries: [(num, title, status 行文本或 '')]，写入 01-30.md。"""
        lines: list[str] = []
        for i, (num, title, status) in enumerate(entries):
            if i:
                lines.append("")
            lines.append(f"### {num}. {title}")
            lines.append("")
            if status:
                lines.append(f"> 状态：{status}")
                lines.append("")
            lines.append(f"正文第 {num} 条：{title} 的独立描述与验证细节 {i * 17}。")
        (cases / "01-30.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def gen_artifacts(cases: Path) -> None:
        (cases / "INDEX.md").write_text(gci.build_index(str(cases)), encoding="utf-8")
        (cases / "DIGEST.md").write_text(gci.build_digest(str(cases)), encoding="utf-8")
        gdir = cases / "_generated"
        gdir.mkdir(exist_ok=True)
        (gdir / "REVIEW-DRAFT.md").write_text(gci.build_review_draft(str(cases)), encoding="utf-8")

    base = [("1", "第一条", ""), ("2", "第二条", "已发布 v1"),
            ("3", "第三条", "已被取代"), ("3补", "第三条补充", ""),
            ("4", "第四条", "只作历史")]

    with tempfile.TemporaryDirectory(prefix="case_audit_self_") as tmpdir:
        root = Path(tmpdir)

        # ①补号解析：parse_cases 得到预期条数（含 3补），正常库 --check rc=0。
        d1 = root / "ok"
        d1.mkdir()
        write_lib(d1, base)
        gen_artifacts(d1)
        rows = parse_cases(str(d1))
        nums = [r["num"] for r in rows]
        check("补号解析：parse_cases 得 5 条（含 3补）", len(rows) == 5 and "3补" in nums, f"nums={nums}")
        rc, out = capture(["--cases", str(d1), "--check"])
        check("正常库 --check rc=0、错误 0", rc == 0 and "错误 0" in out, f"rc={rc} out={out[:120]!r}")

        # ②缺号检出：普通号断号（缺 3）。
        d2 = root / "gap"
        d2.mkdir()
        write_lib(d2, [("1", "一", ""), ("2", "二", ""), ("4", "四", "")])
        gen_artifacts(d2)
        rc, out = capture(["--cases", str(d2), "--check"])
        check("缺号检出：断号库 --check rc=1 且输出含「缺号」", rc == 1 and "缺号" in out, f"rc={rc} out={out[:160]!r}")

        # ③取代方向：第 3 条写「已被第 2 条取代」（目标编号不大于源）。
        d3 = root / "direction"
        d3.mkdir()
        write_lib(d3, [("1", "一", ""), ("2", "二", ""),
                       ("3", "三", "已被第 2 条取代"), ("4", "四", "")])
        gen_artifacts(d3)
        rc, out = capture(["--cases", str(d3), "--check"])
        check("取代方向：目标编号不大于源 → --check rc=1、含「方向错误」",
              rc == 1 and "方向错误" in out, f"rc={rc} out={out[:160]!r}")

        # ④_generated 假案例跳过：造 ### 99.，审计条数不变、--check 仍 rc=0。
        d4 = root / "fake"
        d4.mkdir()
        write_lib(d4, base)
        gen_artifacts(d4)
        (d4 / "_generated" / "FAKE.md").write_text("### 99. 生成物假案例\n\n不应被计入。\n", encoding="utf-8")
        rows4 = parse_cases(str(d4))
        check("_generated 假案例跳过：99 不在列、条数仍 5",
              len(rows4) == 5 and "99" not in [r["num"] for r in rows4], f"nums={[r['num'] for r in rows4]}")
        rc, out = capture(["--cases", str(d4), "--check"])
        check("_generated 假案例不妨碍 --check rc=0", rc == 0, f"rc={rc} out={out[:120]!r}")

        # ⑤真门控：正常库 rc=0 后篡改正文使 INDEX 过期，--check 必须翻红 rc=1 且含 FAIL/过期。
        d5 = root / "gate"
        d5.mkdir()
        write_lib(d5, base)
        gen_artifacts(d5)
        rc, out = capture(["--cases", str(d5), "--check"])
        check("门控前置：正常库 --check rc=0", rc == 0, out)
        p = d5 / "01-30.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n### 5. 新增未入索引\n", encoding="utf-8")
        rc, out = capture(["--cases", str(d5), "--check"])
        check("真门控：篡改正文后 --check rc=1 且含 FAIL/过期",
              rc == 1 and "FAIL" in out and "过期" in out, f"rc={rc} out={out[:160]!r}")

    failed = checks.count(False)
    print()
    print(f"SELF-TEST {'FAIL' if failed else 'PASS'} n={len(checks)}" + (f" failed={failed}" if failed else ""))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="案例库体检（只读）")
    ap.add_argument("--self-test", action="store_true", help="跑内置自检（hermetic，秒级，只写临时目录）")
    ap.add_argument("--cases", default="", help="案例目录（默认自动探测笔记仓）")
    ap.add_argument("--check", action="store_true", help="有错误时退 1")
    ap.add_argument("--no-similarity", action="store_true", help="跳过相似度扫描")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

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
