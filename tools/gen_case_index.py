#!/usr/bin/env python3
r"""生成案例法索引 `cases/INDEX.md`（"减脂"六件事的第 1 件）。

**为什么需要**：案例法是**只增不改**的复盘日志，写到第 99 条、4800 行时，
"某条约束到底写过没有"只能靠顺序搜。成熟做法（ADR / 事故复盘库）都有两样东西：
**索引**与**状态**（还活着 / 已被取代 / 只是历史）。这个脚本补上这两样。

    python3 tools/gen_case_index.py                 # 就地更新 <cases>/INDEX.md
    python3 tools/gen_case_index.py --check         # 只校验是否过期（CI 用；过期退 1）
    python3 tools/gen_case_index.py --query --keyword <词>   # 只读查询（见下）
    python3 tools/gen_case_index.py --cases <目录>   # 指定案例目录

案例目录的解析顺序：`--cases` → 环境变量 `SLCN_NOTES` 下的 `cases/` →
同级笔记仓 `EditToolBox-notes/cases/` → 引擎仓内的 `Engine/docs/cases/`（老布局）。

**状态怎么来**：案例正文里写一行引用块即可（可选）：

    > 状态：已被第 88 条取代

不写就是 `unknown`（= 生命周期未复核，可能是现行约束也可能是历史记录，机器不判定）。
B-390 起「现行/待判定」混合标签删除：无状态 = 显式 `unknown`，不产生 `live` 分类。
取代机制不需要额外数据库，写案例的人顺手就能标。

**只读查询子命令**：`--query` 与 `--keyword`/`--path`/`--status`/`--num` 搭配使用，
只 open(read) 案例正文；输出案例号/标题/生命周期状态/标签/命中片段/正文链接/取代关系。
`--path` 用**路径后缀匹配**（如 `WindowsShell/main.py`）。无结果返回码 3。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Engine"))

from slcn import paths as _paths  # noqa: E402

#: 案例标题：`### 119.` 为常规；`### 119补.` / `### 119补2.` 为同号补充案例
#: （批105 B-181：62+.md 有 2 条「119补」补充案例，旧正则漏收 → INDEX 227 vs 实际 229）。
CASE_RE = re.compile(r"^###\s+(\d+(?:补\d*)?)\.\s*(.+?)\s*$")
STATUS_RE = re.compile(r"^>\s*状态[:：]\s*(.+?)\s*$")
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

#: DIGEST 的主分组优先级（一个案例只进一个组；`判据`/`坑`/`红线` 太泛，放最后兜底）
DIGEST_PRIORITY = ["界面", "AI", "LaTeX", "Word", "平台", "发布", "测试", "文档", "PDF",
                   "判据", "坑", "红线"]

#: 状态前缀 → 分类（第二十二批；`> 状态：` 行是**元数据**，不改正文）。
#: 不写状态 = `unknown`（显式「未复核」，B-390 删除了旧的「现行/待判定」混合标签，
#: 也不再产生 `live`——`live` 本不是真实前缀，只是空状态桶）。
STATUS_KINDS: list[tuple[str, str]] = [
    ("已被取代", "superseded"),
    ("只作历史", "history"),
    ("部分历史", "history"),
    ("已发布", "released"),
]
#: 统一分类集（INDEX 汇总行顺序固定）；`unknown` 是无状态桶，其余取 STATUS_KINDS。
STATUS_SUMMARY_LABELS = (
    ("unknown", "未复核(unknown)"),
    ("released", "已发布"),
    ("history", "只作历史/部分历史"),
    ("superseded", "已被取代"),
    ("other", "其他状态"),
)
#: 查询 `--status` 接受的合法值（不含 `live`——它不是真实分类）。
QUERY_STATUS_VALUES = ("unknown", "released", "history", "superseded", "other")
#: 待复核候选清单固定取近 N 条（B-390 §2.3 写死 30）。
REVIEW_RECENT_N = 30
#: 固定指针文档（它们点名的案例优先进入待复核候选）。
POINTER_DOCS = ("LEAD-LESSONS.md", "BRIEF.md")
#: 取代目标提取（结构检查用；查询输出「取代关系」原样展示状态行）。
SUPERSEDED_RE = re.compile(r"已被第\s*(\d+(?:补\d*)?)\s*条取代")
_SUMMARY_LIMIT = 64

#: 标签判据：关键词 → 标签（**粗判**，只为人眼定位，不参与任何自动化判断）
TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("Word", ("Word 稿", "Word 通道", "docx", "脚注", "参考文献", "题名页", "摘要页",
              "OMML", "表格", "内嵌图片", "人年制")),
    ("LaTeX", ("LaTeX", "模板", "宏包", "排版", "编译", "xelatex", "biber", "骨架",
               r"\section", r"\input", "目录")),
    ("红线", ("原稿只读", "冻结基线", "不许", "红线", "不得")),
    ("判据", ("判据", "口径", "三态", "签名", "规范", "体例")),
    ("坑", ("踩到", "坑", "假阳性", "误报", "事故", "教训", "错")),
    ("界面", ("外壳", "界面", "标签页", "GUI", "Tkinter", "SwiftUI", "侧栏", "按钮",
              "设置页", "窗口", "App")),
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


def _case_files(cases_dir: str) -> list[str]:
    """案例正文文件列表：排除 INDEX/DIGEST/TEMPLATE 与 `_generated/` 生成物目录。

    B-390：`_generated/REVIEW-DRAFT.md` 是生成物，不进案例解析、不占编号、不算 265。
    """
    return sorted(f for f in os.listdir(cases_dir)
                  if f.endswith(".md")
                  and f not in ("INDEX.md", "DIGEST.md", "TEMPLATE.md")
                  and f != "_generated"                      # 显式跳过生成物目录
                  and not f.startswith("_generated" + os.sep)  # 防御：万一出现平铺命名
                  and os.path.isfile(os.path.join(cases_dir, f)))


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
        scores = {tag: sum(body.count(kw) for kw in kws) for tag, kws in TAGS}
        tags = [tag for tag in sorted(scores, key=lambda t: -scores[t]) if scores[tag] > 0]
        dm = DATE_RE.search(title)
        out.append({"num": num, "title": title, "summary": _summary_of(lines, i + 1),
                    "status": status, "tags": "、".join(tags[:4]),
                    "_scores": scores, "date": dm.group(1) if dm else "", "body": body})
    return out


def gh_anchor(title: str) -> str:
    """GitHub 标题锚点（尽力而为）：小写、空格转 `-`、去掉标点，中文字符保留。

    ⚠️ 只在"看着像"的程度上对齐 GitHub 的规则；万一不准，链接会停在文件顶部 ——
    所以索引表头里也写了"搜 `### N.`"这条退路。
    """
    s = title.lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", s)
    return s.strip().replace(" ", "-")


def case_link(c: dict[str, str]) -> str:
    """正文链接（相对 cases/ 目录，带锚点）。"""
    return f"{c['file']}#{c['num']}-{gh_anchor(c['title'])}"


def cell(s: str) -> str:
    """表格单元格：竖线要转义，换行要压平，否则整张表会塌。"""
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _case_sort_key(num: str) -> tuple[int, bool, int]:
    """案例号排序：`119` < `119补` < `119补2` < `120`（补充案例紧跟同号正条）。"""
    m = re.match(r"^(\d+)(?:补(\d*))?$", num)
    if not m:
        return (10 ** 9, False, 0)
    return (int(m.group(1)), m.group(2) is not None, int(m.group(2) or 0))


def status_kind(text: str) -> str:
    """状态文本 → 分类（无状态 = unknown；不产生 live）。"""
    if not text:
        return "unknown"
    for prefix, kind in STATUS_KINDS:
        if text.startswith(prefix):
            return kind
    return "other"


def load_cases(cases_dir: str) -> list[dict[str, str]]:
    """读取并排序全部案例（跳过 `_generated/` 与 INDEX/DIGEST/TEMPLATE）。"""
    rows: list[dict[str, str]] = []
    for f in _case_files(cases_dir):
        for c in parse_case_file(os.path.join(cases_dir, f)):
            c["file"] = f
            rows.append(c)
    rows.sort(key=lambda c: _case_sort_key(c["num"]))
    return rows


def build_index(cases_dir: str) -> str:
    """生成 INDEX.md 的全文。"""
    rows = load_cases(cases_dir)
    counts: dict[str, int] = {k: 0 for k, _ in STATUS_SUMMARY_LABELS}
    for c in rows:
        counts[status_kind(c["status"])] += 1
    count_txt = "；".join(f"{label} {counts[key]} 条" for key, label in STATUS_SUMMARY_LABELS)
    out = [
        "# 案例法索引",
        "",
        "> **本文件由 `tools/gen_case_index.py` 生成，不要手改。**",
        "> 案例正文在 `01-30.md` / `31-61.md` / `62+.md`（只增不改）。",
        "> 配套 `DIGEST.md` 是按主题分组的一句话速查（同脚本生成）。",
        "",
        f"共 **{len(rows)} 条**：{count_txt}。",
        "",
        "**读法**：先看「一句话」定位，再翻到对应条。`标签` 是粗判（脚本按关键词打），只为人眼筛选。",
        "链接指向标题锚点；万一跳不准，在对应文件里搜 `### <条号>.`（如 `### 99.`）。",
        "状态 `—` = `未复核(unknown)`：生命周期未经确认，可能是现行约束也可能是历史记录；看标题、日期与标签判断。",
        "状态由正文里的 `> 状态：…` 元数据行决定（见 `cases/TEMPLATE.md`），不改正文。",
        "",
        "| 条 | 日期 | 标题 | 一句话 | 标签 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for c in rows:
        out.append(f"| [{c['num']}]({case_link(c)}) | {cell(c['date']) or '—'} | {cell(c['title'])} | "
                   f"{cell(c['summary'])} | {cell(c['tags']) or '—'} | {cell(c['status']) or '—'} |")
    out.append("")
    return "\n".join(out)


def build_digest(cases_dir: str) -> str:
    """生成 `DIGEST.md`：按**主题标签**分组的一句话速查（第二十二批）。

    为什么不直接读 62+.md：121 条 / 27 万字符，整读成本太高；DIGEST 是
    "现行约束/最近修复"的入口，正文仍是唯一证据。
    """
    rows = load_cases(cases_dir)
    by_tag: dict[str, list[dict[str, str]]] = {}
    for c in rows:
        scores = c.get("_scores") or {}
        best = max(scores.values()) if scores else 0
        if best <= 0:
            primary = "其他"
        else:
            hit = [t for t, v in scores.items() if v == best]
            primary = next((t for t in DIGEST_PRIORITY if t in hit), hit[0])
        by_tag.setdefault(primary, []).append(c)
    out = [
        "# 案例法速查（DIGEST）",
        "",
        "> **本文件由 `tools/gen_case_index.py` 生成，不要手改。**",
        "> 按主题分组；每条只留标题与一句话，**根因/实测/验收看 `INDEX.md` 链接的正文**。",
        "> 状态行解释见 `TEMPLATE.md`；不确定某条是否仍生效，先看它的日期与状态。",
        "",
    ]
    order = sorted(by_tag, key=lambda t: (-len(by_tag[t]), t))
    for tag in order:
        items = by_tag[tag]
        out.append(f"## {tag}（{len(items)} 条）")
        out.append("")
        for c in items:
            suffix = f"（{c['status']}）" if c["status"] else ""
            out.append(f"- **{c['num']}** {cell(c['title'])}{suffix} — {cell(c['summary'])}")
        out.append("")
    return "\n".join(out)


def _public_head() -> str:
    """生成时公开仓 HEAD（作为 `source_head` 字段；不称「依据版本」）。"""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "(未知)"
    except OSError:
        return "(未知)"


def _pointer_hits(cases_dir: str) -> dict[str, dict[str, list[int]]]:
    """固定指针文档（LEAD-LESSONS.md / BRIEF.md）里点名的案例号 → {文档: [行号]}。

    解析 `案例 N` / `#N` / `第 N 条` 三种写法；笔记仓根 = cases_dir 的父目录。
    """
    notes_root = os.path.dirname(os.path.abspath(cases_dir))
    hits: dict[str, dict[str, list[int]]] = {}
    for name in POINTER_DOCS:
        path = os.path.join(notes_root, name)
        if not os.path.isfile(path):
            continue
        for lineno, line in enumerate(open(path, encoding="utf-8").read().split("\n"), 1):
            found = set()
            # 「案例 230」/「案例 230/231 为…」：一个「案例」前缀可带多个斜线分隔的号
            for m in re.finditer(r"案例法?\s*((?:\d+(?:补\d*)?\s*/\s*)*\d+(?:补\d*)?)", line):
                found.update(re.findall(r"\d+(?:补\d*)?", m.group(1)))
            # 「案例 230（…）与 231（…）」：「案例」之后的同级「与 N」也算点名
            for m in re.finditer(r"案例\s*\d+(?:补\d*)?[^。\n]*?[与、]\s*(\d+(?:补\d*)?)\s*[（(]", line):
                found.add(m.group(1))
            # 「案例 #263」（BRIEF 写法）
            for m in re.finditer(r"案例\s*#(\d+(?:补\d*)?)", line):
                found.add(m.group(1))
            # 独立 `#N`（排除 gen_docs 写 **#1**/#2 这类文档步骤编号：要求 #后不接 ** 且
            # 前后为空白/标点，且「写 #N」不算案例；仅保留与「案例」同句或 README 式引用）
            # 本批只接受「案例 #N」显式写法，避免把文档步骤编号误判为案例号。
            # 「第 N 条案例」显式写法（裸「第 1 条」常指文档章节，不算案例）：
            for m in re.finditer(r"第\s*(\d+(?:补\d*)?)\s*条案例", line):
                found.add(m.group(1))
            for num in found:
                hits.setdefault(num, {}).setdefault(name, []).append(lineno)
    return hits


def build_review_draft(cases_dir: str) -> str:
    """生成 `cases/_generated/REVIEW-DRAFT.md`：确定性待复核候选清单（B-390 §2.3）。

    排序键（完全确定性）：①固定指针文档点名优先 → ②编号降序取近 N 条（N=30）
    → ③仍为 unknown → ④案例号升序作最终稳定键。只产候选、不判定失效、不改状态行。
    """
    rows = load_cases(cases_dir)
    pointer = _pointer_hits(cases_dir)
    head = _public_head()

    by_key = {_case_sort_key(c["num"]): c for c in rows}
    all_keys = sorted(by_key)

    selected: set[tuple[int, bool, int]] = set()
    # ①固定指针文档点名（无论是否 unknown、是否在近 30 条内都入选）
    for c in rows:
        if c["num"] in pointer:
            selected.add(_case_sort_key(c["num"]))
    # ②编号降序取近 N 条
    selected.update(all_keys[-REVIEW_RECENT_N:])
    # ③④排序：指针优先（0/1）→ 近 30 条优先（0/1）→ unknown 优先（0/1）→ 案例号升序
    recent = set(all_keys[-REVIEW_RECENT_N:])

    def draft_sort_key(key: tuple[int, bool, int]) -> tuple[int, int, int, int, bool, int]:
        c = by_key[key]
        return (0 if c["num"] in pointer else 1,
                0 if key in recent else 1,
                0 if status_kind(c["status"]) == "unknown" else 1,
                key[0], key[1], key[2])

    chosen = [by_key[k] for k in sorted(selected, key=draft_sort_key)]
    out = [
        "# 待复核候选清单（REVIEW-DRAFT，生成物）",
        "",
        "> **本文件由 `tools/gen_case_index.py` 生成，不要手改；每次全量重写。**",
        "> 只按确定性规则挑选**候选**：固定指针文档点名优先 + 编号降序近 "
        f"{REVIEW_RECENT_N} 条；候选**不等于**失效，不判定生命周期、不改状态行。",
        f"> `source_head` 为生成时公开仓 HEAD（{head}），不表示该 HEAD 已复验。",
        "> 解析案例时显式跳过 `_generated/` 目录（本文件不占编号、不计入案例总数）。",
        "",
        "| 案例号 | case_date | released_status | source_head | pointer_hits | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for c in chosen:
        released = c["status"] if c["status"].startswith("已发布") else "—"
        per_doc = pointer.get(c["num"], {})
        hits = "、".join(f"{name}:{','.join(str(x) for x in per_doc[name])}"
                        for name in POINTER_DOCS if name in per_doc) or "—"
        out.append(f"| {c['num']} | {cell(c['date']) or '—'} | {cell(released)} | "
                   f"{head} | {hits} | [{c['num']}]({case_link(c)}) |")
    out.append("")
    return "\n".join(out)


def query_cases(cases_dir: str, *, keyword: str = "", path: str = "",
                status: str = "", num: str = "") -> tuple[list[dict[str, str]], list[str]]:
    """只读查询：返回 (命中案例列表, 每个命中的命中片段)。

    四类条件可组合（同时给出时取交集）；输出字段由调用方渲染。
    """
    rows = load_cases(cases_dir)
    matched: list[tuple[dict[str, str], str]] = []
    for c in rows:
        if num and c["num"] != num:
            continue
        if status and status_kind(c["status"]) != status:
            continue
        snippet = ""
        if keyword:
            kl = keyword.lower()
            for line in c["body"].split("\n"):
                if kl in line.lower():
                    snippet = line.strip()[:160]
                    break
            if not snippet:
                continue
        if path:
            for line in c["body"].split("\n"):
                # 路径后缀匹配：正文里的路径 token 以 `/<path>` 结尾或等于 `<path>`
                for m in re.finditer(r"[\w./\-]*\.\w+", line):
                    token = m.group(0)
                    if token == path or token.endswith("/" + path):
                        snippet = snippet or line.strip()[:160]
                        break
                if snippet:
                    break
            if not snippet:
                continue
        matched.append((c, snippet))
    return [c for c, _ in matched], [s for _, s in matched]


def render_query(cases_dir: str, rows: list[dict[str, str]], snippets: list[str]) -> str:
    """渲染查询结果（固定字段，含「适用范围」固定串，不输出模板没有的字段）。"""
    out = [f"案例查询：{len(rows)} 条命中", ""]
    if not rows:
        out.append("适用范围：未结构化，见正文")
        return "\n".join(out)
    for c, snip in zip(rows, snippets):
        sup = SUPERSEDED_RE.search(c["status"])
        rel = f"取代关系：{sup.group(0)}" if sup else "取代关系：—"
        out.append(f"- 案例号：{c['num']}")
        out.append(f"  标题：{c['title']}")
        out.append(f"  生命周期状态：{c['status'] or '—'}（{status_kind(c['status'])}）")
        out.append(f"  标签：{c['tags'] or '—'}")
        out.append(f"  命中片段：{snip or '—'}")
        out.append(f"  正文链接：[{c['num']}]({case_link(c)})")
        out.append(f"  {rel}")
        out.append("  适用范围：未结构化，见正文")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验案例法索引 INDEX.md，或只读查询案例")
    ap.add_argument("--cases", default="", help="案例目录（默认自动探测笔记仓）")
    ap.add_argument("--out", default="", help="输出文件（默认 <cases>/INDEX.md）")
    ap.add_argument("--check", action="store_true", help="只校验是否过期（过期退 1）")
    ap.add_argument("--no-digest", action="store_true", help="不生成/校验 DIGEST.md")
    ap.add_argument("--query", action="store_true", help="只读查询（与 --keyword/--path/--status/--num 搭配）")
    ap.add_argument("--keyword", default="", help="按正文关键词查询（大小写不敏感）")
    ap.add_argument("--path", default="", help="按路径后缀查询（如 WindowsShell/main.py）")
    ap.add_argument("--status", default="", help=f"按状态分类查询（{ '/'.join(QUERY_STATUS_VALUES) }）")
    ap.add_argument("--num", default="", help="按案例号精确查询（含补号 119补）")
    args = ap.parse_args(argv)

    if args.query and args.status and args.status not in QUERY_STATUS_VALUES:
        print(f"参数错误：--status 取值须为 { '/'.join(QUERY_STATUS_VALUES) }", file=sys.stderr)
        return 2
    if args.query and not (args.keyword or args.path or args.status or args.num):
        print("参数错误：--query 需至少给出 --keyword/--path/--status/--num 之一", file=sys.stderr)
        return 2

    cases_dir = find_cases_dir(args.cases)
    if not cases_dir:
        print(f"≡ gen_case_index：找不到案例目录。{_paths.describe_notes()}")
        return 3 if args.query else 0    # 没有案例库不是错误（公开仓里就是这样）

    if args.query:
        rows, snippets = query_cases(cases_dir, keyword=args.keyword, path=args.path,
                                     status=args.status, num=args.num)
        print(render_query(cases_dir, rows, snippets))
        return 0 if rows else 3          # 无结果：明确非零码

    out_path = os.path.abspath(args.out) if args.out else os.path.join(cases_dir, "INDEX.md")
    text = build_index(cases_dir)
    digest_path = os.path.join(cases_dir, "DIGEST.md")
    digest = build_digest(cases_dir) if not args.no_digest else ""
    review_dir = os.path.join(cases_dir, "_generated")
    review_path = os.path.join(review_dir, "REVIEW-DRAFT.md")
    review = build_review_draft(cases_dir)

    if args.check:
        ok = True
        cur = open(out_path, encoding="utf-8").read() if os.path.isfile(out_path) else ""
        if cur == text:
            print(f"PASS  案例索引与案例正文一致（{out_path}）")
        else:
            print("FAIL  案例索引过期；跑 python3 tools/gen_case_index.py 重新生成", file=sys.stderr)
            ok = False
        if not args.no_digest:
            cur_d = open(digest_path, encoding="utf-8").read() if os.path.isfile(digest_path) else ""
            if cur_d == digest:
                print(f"PASS  案例速查与案例正文一致（{digest_path}）")
            else:
                print("FAIL  案例速查过期；跑 python3 tools/gen_case_index.py 重新生成", file=sys.stderr)
                ok = False
        cur_r = open(review_path, encoding="utf-8").read() if os.path.isfile(review_path) else ""
        if cur_r == review:
            print(f"PASS  待复核候选清单与案例正文一致（{review_path}）")
        else:
            print("FAIL  待复核候选清单过期；跑 python3 tools/gen_case_index.py 重新生成",
                  file=sys.stderr)
            ok = False
        return 0 if ok else 1

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    n = text.count("\n| [")
    print(f"✅ 已生成 {out_path}（{n} 条）")
    if not args.no_digest:
        with open(digest_path, "w", encoding="utf-8") as fh:
            fh.write(digest)
        print(f"✅ 已生成 {digest_path}")
    os.makedirs(review_dir, exist_ok=True)
    with open(review_path, "w", encoding="utf-8") as fh:
        fh.write(review)
    print(f"✅ 已生成 {review_path}（{len(review.splitlines())} 行，候选不占编号）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
