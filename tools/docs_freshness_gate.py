#!/usr/bin/env python3
"""文档一致性门禁：跨产物版本号 ↔ 功能覆盖联动检查。

为什么需要这份脚本
------------------
发布事故的典型形态是「版本号升了、三处用户载体还是旧口径」：唯一版本来源
`Engine/slcn/__init__.py` 已是新版本，而 `docs/reference/changelog.md` 顶部仍停在上一版。
仓库里已有的 `Engine/tests/test_docs_consistency.py` 与 `Engine/tools/gen_docs.py` 只管
文档新鲜度 / 数字注入，不查「版本号 ↔ 功能关键词 ↔ 禁用旧名」的联动；本脚本只补这一层，
不替代它们。

检查项（编号对应设计稿 §8.1 #1–#7）
------------------------------------
1. 版本来源：读 `Engine/slcn/__init__.py` 的 `__version__`（唯一来源）。用正则读文件、
   不 import 引擎，避免副作用，也能指向任意仓库根（测试夹具）。
2. changelog：`docs/reference/changelog.md` 顶部条目必须是当前版本，带日期与
   ✨/💎/🐞 三类人读分类。
3. `使用说明.md`：不含过时版本号陈述，且含新入口关键词。
4. 官网用户页（`docs/features/*`、`docs/guide/*`、`docs/index.md`）覆盖全套 V 关键词。
5. 软件内帮助（`sources-flet/app/ui/help_content.py`）覆盖新入口关键词，且 DOCS
   不再指向维护者文档 `Engine/docs/GUIDE.md`。
6. 禁用旧名 / 机器语汇：用户页不得出现旧名「参考文献规范化」、commit hash、`B-xxx`
   任务号、批次号、绝对路径、「诊断记录」（changelog 历史条目与 `docs/archive/**`
   属历史留痕，豁免）。
7. 规则数：用户页统一 29 条，不得再出现 26 条硬编码（changelog 历史不回填）。

只读：本脚本不写任何文件。退出码与末行约定同其它门禁：成功末行 `EXIT=0`，失败末行
`EXIT=1`；失败信息按设计稿 §8.2 的中文标签格式打印。

用法：`python3 tools/docs_freshness_gate.py`（在公开仓根执行）。
"""
from __future__ import annotations

import glob
import os
import re
import sys

#: 公开仓根（`tools/` 的上一级）；所有相对路径都相对它解析。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 版本唯一来源。
VERSION_SOURCE = "Engine/slcn/__init__.py"

#: V 关键功能关键词（单一数据表，供 #3/#4/#5 共用；取自 release.yml:421-431 与
#: 设计稿 §1 缺口清单的九组）。「案例法」是维护者 CLI、不是 App 用户功能，
#: 不在表内（§10.2 #10）。
V_KEYWORDS: tuple[str, ...] = (
    "AI 辅助编译修复",
    "确认并发送一次",
    "设置中心",
    "命令面板",
    "AI 辅助处理 Bib",
    "original/",
    "字段差异",
    "字段归位",
    "整期工作台",
)

#: 三处载体都必须出现的「新入口」关键词（设计稿 §8.2 的失败样例正是这三条）。
HEADLINE_KEYWORDS: tuple[str, ...] = ("AI 辅助编译修复", "设置中心", "命令面板")

#: 禁用旧名 / 机器语汇（同一份数据表，供脚本与人工核对共用）。
BANNED_TERMS: tuple[str, ...] = (
    "参考文献规范化",   # 旧名；ia_map.py 已定名「引用库 → AI 辅助处理 Bib」
    "诊断记录",         # 机器 / 内部语汇，用户文本不写
)

#: 正则型禁用模式：(标签, 模式)。豁免规则同 BANNED_TERMS。
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("commit hash", r"(?<![0-9a-f])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![0-9a-f])"),
    ("B-xxx 任务号", r"(?<![A-Za-z0-9])B-\d+"),
    ("批次号", r"批\s*\d+"),
    ("文件绝对路径", r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
)

#: 官网用户页（#4 的覆盖范围；#6/#7 也扫这些页面）。
SITE_PAGE_GLOBS = ("docs/features/*.md", "docs/guide/*.md")
SITE_INDEX = "docs/index.md"

#: 参考类用户页（#6/#7 扫描；changelog 由豁免规则跳过，维护者文档不在用户载体范围）。
REFERENCE_USER_PAGES = (
    "docs/reference/faq.md",
    "docs/reference/known-limitations.md",
    "docs/reference/environment-check.md",
)

MANUAL = "使用说明.md"
HELP_CONTENT = "sources-flet/app/ui/help_content.py"
CHANGELOG = "docs/reference/changelog.md"

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)
_PRODUCT_VERSION_RE = re.compile(r"(?<![\d.])v?(1\.\d+\.\d+)(?![\d.])")
_TOP_ENTRY_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)", re.M)
_DATE_RE = re.compile(r"[(（](\d{4}-\d{2}-\d{2})[)）]")
_RULE_26_RE = re.compile(r"(?<!\d)26\s*条")


def get_current_version(path: str) -> str:
    """读 `__version__`；相对路径同时按当前目录与公开仓根解析，读不到返回空串。"""
    candidates = [path]
    if not os.path.isabs(path):
        candidates.append(os.path.join(REPO_ROOT, path))
    for candidate in candidates:
        try:
            with open(candidate, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        match = _VERSION_RE.search(text)
        if match:
            return match.group(1)
    return ""


def check_changelog(text: str, version: str) -> list[str]:
    """#2：changelog 顶部条目必须是 `version`，带日期与 ✨/💎/🐞 分类。"""
    top = _TOP_ENTRY_RE.search(text)
    if not top:
        return ["未找到 `## <版本号>` 形式的版本条目"]
    top_version = top.group(1)
    if top_version != version:
        return [f"顶部条目为 {top_version}，缺 {version}"]

    line_end = text.find("\n", top.start())
    heading = text[top.start(): line_end if line_end != -1 else len(text)]
    failures: list[str] = []
    if not _DATE_RE.search(heading):
        failures.append(f"{version} 条目缺日期（形如「（2026-11-26）」）")

    body = text[top.end():]
    next_entry = re.search(r"^##\s+\d+\.\d+\.\d+", body, re.M)
    section = body[: next_entry.start()] if next_entry else body
    for mark, label in (("✨", "✨ 新增"), ("💎", "💎 改进"), ("🐞", "🐞 修复")):
        if mark not in section:
            failures.append(f"{version} 条目缺「{label}」分类")
    return failures


def check_keywords(text: str, path: str) -> list[str]:
    """#3/#4/#5：报告 `text` 缺失的关键词（消息里带关键词原文）。

    使用说明与软件内帮助是「入口速查」，只要求三条新入口关键词；官网用户页要求
    覆盖整套 `V_KEYWORDS`。
    """
    return [f"缺关键词: {keyword}" for keyword in _missing_keywords(text, path)]


def _missing_keywords(text: str, path: str) -> list[str]:
    """缺失关键词原表；`main` 用它把同类缺失拼成一行（§8.2 的 `A / B / C` 写法）。"""
    required = HEADLINE_KEYWORDS if _is_entry_carrier(path) else V_KEYWORDS
    return [keyword for keyword in required if keyword not in text]


def check_banned(text: str, path: str) -> list[str]:
    """#6：用户页禁用旧名 / 机器语汇；changelog 历史与 `docs/archive/**` 豁免。"""
    if _is_history_exempt(path):
        return []
    failures = _old_name_hits(text, path)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pattern in BANNED_PATTERNS:
            for match in re.finditer(pattern, line):
                failures.append(f"{path}:{lineno} 含 {label}「{match.group(0)}」")
    return failures


def _old_name_hits(text: str, path: str) -> list[str]:
    """只扫禁用旧名 / 固定词（参考类用户页用它，不套机器语汇正则）。"""
    return [
        f"{path}:{lineno} 仍写「{term}」"
        for lineno, line in enumerate(text.splitlines(), start=1)
        for term in BANNED_TERMS
        if term in line
    ]


def check_rule_count(text: str, path: str) -> list[str]:
    """#7：用户页规则数统一 29；出现 26 条即失败。"""
    return [
        f"{path} 仍写「26 条」，应为 29 条（唯一来源 Engine/docs/PROOF_RULES.md:14）"
        for _ in _RULE_26_RE.finditer(text)
    ]


def run_all(repo_root: str) -> list[str]:
    """跑 #1–#7，返回全部失败信息（空列表 = 全绿）。"""
    failures: list[str] = []
    for _label, items, _value in _run_checks(repo_root):
        failures.extend(items)
    return failures


def _run_checks(repo_root: str) -> list[tuple[str, list[str], str]]:
    """按 #1–#7 逐项返回 (标签, 失败信息, 展示值)；main 用它打印逐项 PASS/FAIL。"""
    results: list[tuple[str, list[str], str]] = []

    version = get_current_version(os.path.join(repo_root, VERSION_SOURCE))
    results.append((
        "版本来源",
        [] if version else [f"读不到 {VERSION_SOURCE} 的 __version__"],
        f"{VERSION_SOURCE}:10  -> {version}" if version
        else f"{VERSION_SOURCE}:10  读不到 __version__",
    ))

    changelog_text = _read(repo_root, CHANGELOG)
    if changelog_text is None:
        results.append(("changelog", [f"缺 {CHANGELOG}"], ""))
    elif not version:
        results.append(("changelog", [f"无法比对：{VERSION_SOURCE} 读不到版本"], ""))
    else:
        results.append((
            "changelog",
            [f"{CHANGELOG} {msg}" for msg in check_changelog(changelog_text, version)],
            "",
        ))

    manual_text = _read(repo_root, MANUAL)
    if manual_text is None:
        results.append(("使用说明", [f"缺 {MANUAL}"], ""))
    else:
        items: list[str] = []
        if version:
            stale = sorted({v for v in _PRODUCT_VERSION_RE.findall(manual_text) if v != version})
            if stale:
                items.append(
                    f"{MANUAL} 含手写版本号: {' / '.join(stale)}（用户文本不写版本，唯一来源 {VERSION_SOURCE}，当前 {version}）"
                )
        missing = _missing_keywords(manual_text, MANUAL)
        if missing:
            items.append("缺关键词: " + " / ".join(missing))
        results.append(("使用说明", items, ""))

    site_pages = _site_pages(repo_root)
    if not site_pages:
        results.append(("官网用户页", ["未找到 docs/features、docs/guide 与 docs/index.md 用户页"], ""))
    else:
        joined = "\n".join(site_pages[rel] for rel in sorted(site_pages))
        missing = _missing_keywords(joined, "docs/features/*")
        results.append((
            "官网用户页",
            ["用户页整体缺关键词: " + " / ".join(missing)] if missing else [],
            "",
        ))

    help_text = _read(repo_root, HELP_CONTENT)
    if help_text is None:
        results.append(("软件内帮助", [f"缺 {HELP_CONTENT}"], ""))
    else:
        missing = _missing_keywords(help_text, HELP_CONTENT)
        items = [f"{HELP_CONTENT} 缺关键词: " + " / ".join(missing)] if missing else []
        if "Engine/docs/GUIDE.md" in help_text:
            items.append(f"{HELP_CONTENT} DOCS 仍指向维护者文档 Engine/docs/GUIDE.md")
        results.append(("软件内帮助", items, ""))

    user_pages = _user_page_texts(repo_root)
    banned: list[str] = []
    for rel, text in user_pages:
        # 参考类用户页（faq / 已知限制 / 环境检查）只清旧名：存量任务号本批不清，
        # 属设计稿 §8.1 #6 的「上述用户页」之外，避免门禁长期红在存量上。
        if _is_reference_page(rel):
            banned.extend(_old_name_hits(text, rel))
        else:
            banned.extend(check_banned(text, rel))
    results.append(("旧名残留", banned, ""))

    counts: list[str] = []
    for rel, text in user_pages:
        counts.extend(check_rule_count(text, rel))
    results.append(("规则数", counts, ""))

    return results


def _is_reference_page(path: str) -> bool:
    """参考类用户页（faq / 已知限制 / 环境检查）。

    设计稿 §8.1 #6 的扫描范围是「上述用户页」（features / guide / index / 使用说明）；
    参考页仍有少量历史任务号（如已知限制表里的 B-xxx），属待治理的存量而非本批
    要清的对象，避免门禁长期红在存量上。旧名仍照查（Task 5 会把它们清零）。
    """
    normalized = path.replace(os.sep, "/")
    return any(normalized == rel for rel in REFERENCE_USER_PAGES)


def _is_entry_carrier(path: str) -> bool:
    """使用说明与软件内帮助是入口速查，只要求三条新入口关键词。"""
    normalized = path.replace(os.sep, "/")
    return normalized.endswith(("使用说明.md", "help_content.py"))


def _is_history_exempt(path: str) -> bool:
    """changelog 历史条目与 docs/archive/** 属历史留痕，不查禁用语汇。"""
    normalized = path.replace(os.sep, "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == CHANGELOG or normalized.startswith("docs/archive/")


def _read(repo_root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(repo_root, rel), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _site_pages(repo_root: str) -> dict[str, str]:
    """官网用户页：features / guide 的 .md 与首页。"""
    pages: dict[str, str] = {}
    for pattern in SITE_PAGE_GLOBS:
        for absolute in sorted(glob.glob(os.path.join(repo_root, pattern))):
            rel = os.path.relpath(absolute, repo_root).replace(os.sep, "/")
            text = _read(repo_root, rel)
            if text is not None:
                pages[rel] = text
    index_text = _read(repo_root, SITE_INDEX)
    if index_text is not None:
        pages[SITE_INDEX] = index_text
    return pages


def _user_page_texts(repo_root: str) -> list[tuple[str, str]]:
    """#6/#7 扫描范围：官网用户页 + 参考类用户页 + 使用说明（changelog 由豁免规则跳过）。"""
    pages = _site_pages(repo_root)
    for rel in REFERENCE_USER_PAGES:
        text = _read(repo_root, rel)
        if text is not None:
            pages[rel] = text
    manual_text = _read(repo_root, MANUAL)
    if manual_text is not None:
        pages[MANUAL] = manual_text
    return sorted(pages.items())


def main() -> int:
    """CLI 入口：成功打印逐项 PASS 并以 `EXIT=0` 结尾，失败按 §8.2 标签格式打印 `EXIT=1`。"""
    results = _run_checks(REPO_ROOT)
    version = get_current_version(os.path.join(REPO_ROOT, VERSION_SOURCE))
    header = results[0][2]
    body = results[1:]
    failed = (not version) or any(items for _label, items, _value in body)

    if failed:
        print("[docs-freshness] FAIL")
        print(f"  版本来源        {header}")
        for label, items, _value in body:
            for item in items:
                print(f"  ✗ {label} {item}")
        print("  修复提示: 按 docs/reference/changelog.md 顶部补当前版本条目（日期 + ✨/💎/🐞 分类），")
        print("            同步 使用说明.md / help_content.py / 官网用户页 的用户文案，再重跑本脚本。")
        print("EXIT=1")
        return 1

    print("[docs-freshness] PASS")
    print(f"  ✓ 版本来源        {header}")
    for label, _items, _value in body:
        print(f"  ✓ {label} PASS")
    print("EXIT=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
