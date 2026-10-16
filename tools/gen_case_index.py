#!/usr/bin/env python3
r"""生成案例法索引 `cases/INDEX.md`（"减脂"六件事的第 1 件）。

**为什么需要**：案例法是**只增不改**的复盘日志，写到第 99 条、4800 行时，
"某条约束到底写过没有"只能靠顺序搜。成熟做法（ADR / 事故复盘库）都有两样东西：
**索引**与**状态**（还活着 / 已被取代 / 只是历史）。这个脚本补上这两样。

    python3 tools/gen_case_index.py                 # 就地更新 <cases>/INDEX.md
    python3 tools/gen_case_index.py --check         # 只校验是否过期（CI 用；过期退 1）
    python3 tools/gen_case_index.py --cases <目录>   # 指定案例目录

案例目录的解析顺序：`--cases` → 环境变量 `SLCN_NOTES` 下的 `cases/` →
同级笔记仓 `EditToolBox-notes/cases/` → 引擎仓内的 `Engine/docs/cases/`（老布局）。

**状态怎么来**：案例正文里写一行引用块即可（可选）：

    > 状态：已被第 88 条取代

不写就是 `—`（= 仍然是现行约束或历史记录，由读者结合标签判断）。这样"取代"机制
不需要额外数据库，写案例的人顺手就能标。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Engine"))

from slcn import paths as _paths  # noqa: E402

CASE_RE = re.compile(r"^###\s+(\d+)\.\s*(.+?)\s*$")
STATUS_RE = re.compile(r"^>\s*状态[:：]\s*(.+?)\s*$")
_SUMMARY_LIMIT = 64

#: 标签判据：关键词 → 标签（**粗判**，只为人眼定位，不参与任何自动化判断）
TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("红线", ("原稿只读", "冻结基线", "不许", "红线", "不得")),
    ("判据", ("判据", "口径", "三态", "签名", "规范", "体例")),
    ("坑", ("踩到", "坑", "假阳性", "误报", "事故", "教训", "错")),
    ("界面", ("外壳", "界面", "标签页", "GUI", "App", "Tkinter")),
    ("平台", ("平台", "macOS", "Windows", "Xcode", "路径", "锚点")),
    ("发布", ("公开仓", "Release", "发布", "拆仓", "LICENSE", "CI")),
    ("文档", ("文档", "README", "看门狗", "AUTO", "案例法", "索引")),
    ("AI", ("AI", "LLM", "联网", "检索", "密钥", "任务书")),
    ("PDF", ("PDF", "书签", "大纲", "合并", "页数")),
    ("测试", ("测试", "断言", "快照", "差分", "回归", "套件")),
]


def find_cases_dir(explicit: str = "") -> str | None:
    """按解析顺序找案例目录。"""
    if explicit:
        p = os.path.abspath(os.path.expanduser(explicit))
        return p if os.path.isdir(p) else None
    notes = _paths.notes_root()
    if notes:
        cand = os.path.join(notes, "cases")
        if os.path.isdir(cand):
            return cand
    legacy = os.path.join(_paths.engine_root(), "docs", "cases")
    return legacy if os.path.isdir(legacy) else None


def _summary_of(lines: list[str], start: int) -> str:
    """标题之后第一段有内容的文字（去掉 markdown 强调与列表符号）。"""
    for ln in lines[start:]:
        s = ln.strip()
        if not s or s.startswith(("#", ">", "|", "```")):
            continue
        s = re.sub(r"^[-*]\s+", "", s)
        s = s.replace("**", "").replace("`", "")
        return s if len(s) <= _SUMMARY_LIMIT else s[: _SUMMARY_LIMIT - 1] + "…"
    return ""


def parse_case_file(path: str) -> list[dict[str, str]]:
    """解析一个案例文件，返回 [{num, title, summary, status, tags}]。"""
    lines = open(path, encoding="utf-8").read().split("\n")
    heads = [(i, m.group(1), m.group(2)) for i, ln in enumerate(lines)
             for m in [CASE_RE.match(ln)] if m]
    out: list[dict[str, str]] = []
    for idx, (i, num, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i:end])
        status = ""
        for ln in lines[i:end]:
            m = STATUS_RE.match(ln)
            if m:
                status = m.group(1)
                break
        tags = [tag for tag, kws in TAGS if any(kw in body for kw in kws)]
        out.append({"num": num, "title": title, "summary": _summary_of(lines, i + 1),
                    "status": status, "tags": "、".join(tags[:4])})
    return out


def gh_anchor(title: str) -> str:
    """GitHub 标题锚点（尽力而为）：小写、空格转 `-`、去掉标点，中文字符保留。

    ⚠️ 只在"看着像"的程度上对齐 GitHub 的规则；万一不准，链接会停在文件顶部 ——
    所以索引表头里也写了"搜 `### N.`"这条退路。
    """
    s = title.lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", s)
    return s.strip().replace(" ", "-")


def cell(s: str) -> str:
    """表格单元格：竖线要转义，换行要压平，否则整张表会塌。"""
    return s.replace("|", "\\|").replace("\n", " ").strip()


def build_index(cases_dir: str) -> str:
    """生成 INDEX.md 的全文。"""
    files = sorted(f for f in os.listdir(cases_dir)
                   if f.endswith(".md") and f != "INDEX.md")
    rows: list[dict[str, str]] = []
    for f in files:
        for c in parse_case_file(os.path.join(cases_dir, f)):
            c["file"] = f
            rows.append(c)
    rows.sort(key=lambda c: int(c["num"]))

    live = [c for c in rows if not c["status"]]
    superseded = [c for c in rows if c["status"]]
    out = [
        "# 案例法索引",
        "",
        "> **本文件由 `tools/gen_case_index.py` 生成，不要手改。**",
        "> 案例正文在 `01-30.md` / `31-61.md` / `62+.md`（只增不改）。",
        "",
        f"共 **{len(rows)} 条**：现行/历史 {len(live)} 条，标了「已被取代」{len(superseded)} 条。",
        "",
        "**读法**：先看「一句话」定位，再翻到对应条。`标签` 是粗判（脚本按关键词打），只为人眼筛选。",
        "链接指向标题锚点；万一跳不准，在对应文件里搜 `### <条号>.`（如 `### 99.`）。",
        "状态为 `—` 表示没有标「取代」——**是现行约束还是纯粹历史，看标题与标签自己判断**。",
        "",
        "| 条 | 标题 | 一句话 | 标签 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for c in rows:
        anchor = f"{c['file']}#{c['num']}-{gh_anchor(c['title'])}"
        out.append(f"| [{c['num']}]({anchor}) | {cell(c['title'])} | "
                   f"{cell(c['summary'])} | {cell(c['tags']) or '—'} | {cell(c['status']) or '—'} |")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验案例法索引 INDEX.md")
    ap.add_argument("--cases", default="", help="案例目录（默认自动探测笔记仓）")
    ap.add_argument("--out", default="", help="输出文件（默认 <cases>/INDEX.md）")
    ap.add_argument("--check", action="store_true", help="只校验是否过期（过期退 1）")
    args = ap.parse_args(argv)

    cases_dir = find_cases_dir(args.cases)
    if not cases_dir:
        print(f"≡ gen_case_index：找不到案例目录。{_paths.describe_notes()}")
        return 0                                   # 没有案例库不是错误（公开仓里就是这样）
    out_path = os.path.abspath(args.out) if args.out else os.path.join(cases_dir, "INDEX.md")
    text = build_index(cases_dir)

    if args.check:
        cur = open(out_path, encoding="utf-8").read() if os.path.isfile(out_path) else ""
        if cur == text:
            print(f"PASS  案例索引与案例正文一致（{out_path}）")
            return 0
        print("FAIL  案例索引过期；跑 python3 tools/gen_case_index.py 重新生成", file=sys.stderr)
        return 1

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    n = text.count("\n| [")
    print(f"✅ 已生成 {out_path}（{n} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
