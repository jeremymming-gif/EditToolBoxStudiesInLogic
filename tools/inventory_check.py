#!/usr/bin/env python3
"""inventory_check.py —— `tools/INVENTORY.md` 看门狗（B-157 / 批104）

一句话：只读、离线、确定性地校验「INVENTORY 登记表 ↔ 盘面 ↔ 计数」三方一致；
**不一致就 exit 1，绝不静默放行**。外部 Qoder 试点产物只作设计输入，本文件按本仓 DoD 重写。

为什么要有它
------------
INVENTORY 是「新增工具不登记不得合入」的唯一台账。人写的表会漂：工具改名/归档/行数变了、
加了文件忘登记、表头计数是手抄的旧数。本工具把能机器判的部分全部机器判。

校验项（finding 类型；全部只读，不改任何文件）
---------------------------------------------
- `missing`             登记了但盘面不存在（本地生成物「本地/非工具」类跳过）
- `unregistered`        盘面有但没登记（顶级项；`archive/` 按**逐件**登记，不按目录整包）
- `line_drift`          行数注记（「实测 1333 行」「`main.swift` 3517 行」「（569 行）」）≠ 实测
- `line_target_missing` 行数注记指向的文件不存在/不可读
- `invalid_status`      状态不是 active/dormant/legacy/archived/非工具
- `missing_field`       非本地项缺 用途 / owner / 最后提交 / 测试验证
- `private_path`        登记了私有仓路径（`notes/`、`../`、绝对路径）——公开仓禁
- `path_style`          表内路径写成 `tools/…` / `Engine/tools/…`（应为相对本节目录的路径）
- `duplicate`           同一路径登记两次
- `count_drift`         表头声明的登记项数 / 工具项+本地生成物拆分 / 第 2 节标题项数 ≠ 实测

退出码约定（**写死；`--check` 与 `--json` 语义分离**）
----------------------------------------------------
- `--check`   : 0 = 一致（可含 WARN）；1 = 发现 finding；2 = 无法分析（INVENTORY 缺失/不可读/内部错误）
- `--json`    : 0 = **分析完成**（无论是否一致，结果看 `ok` / `findings` 字段）；1 = 无法分析；2 = 用法错误（argparse）
- `--self-test`: 0 = 全绿；1 = 有失败用例
=> 分离的好处：CI 要「不一致就红」用 `--check`；要把 JSON 落盘再由脚本判，用 `--json`，
   不会因为「发现不一致」拿到非 0（把「数据结论」和「工具坏没坏」混在一个退出码里是常见坑）。

硬化点（相对外部试点参考实现的差异，只作设计输入、非整文件复制）
--------------------------------------------------------------
1. **纯 stdlib、单文件**：不 import 仓内其他模块，不联网；`--self-test` 自带 tempfile 夹具。
2. **subprocess 白名单**：本工具**只**调一次 `git ls-files`（子命令白名单常量 + 参数全部写死、
   不经 shell、`timeout=10`）。git 不存在/非 git 仓/超时/非 0 退出 → **降级为文件系统扫描**
   并给明确 WARN，仍出结论、不崩溃（`tracked_source=filesystem-fallback`）。
3. **自测不依赖 git**：tracked 文件列表通过 `tracked_provider` 注入，self-test **不初始化 git、
   不 commit、不启动任何子进程**（参考实现每个用例 4 次 git 子进程）。
4. **--check / --json 退出码分离**：见上；self-test 直接测 `exit_code()` 两个分支。
5. **计数口径写死**（见下），表头数字由本工具校验，不再手抄旧数。

计数口径（docstring 写死）
--------------------------
- 登记项数 = 第 1 节表行数（= 表头 `**N 项 = A 个工具项 + B 个本地生成物**` 的 N；A/B 同校验）；
  第 2 节 `Engine/tools/` 项数 = 该节行数（= 标题「（N 项）」）。
- `git ls-files tools` 只作**实测输出**、不强制比对：本批新增工具在 commit 前不会出现在索引里，
  强制比会在「已交付未提交」窗口误红。登记项计数才是硬判据。
- 本地生成物判定：状态含「本地 / 非工具」或备注含「不入仓 / 未跟踪」→ 跳过存在性与必填字段校验。

用法
----
  python3 tools/inventory_check.py --check       # 人类可读；不一致 exit 1
  python3 tools/inventory_check.py --json        # 机器可读；分析完成 exit 0
  python3 tools/inventory_check.py --self-test   # hermetic 自测（tempfile 假仓，≥10 用例）
  python3 tools/inventory_check.py --version
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

TOOL_VERSION = "1.0.0"

INVENTORY_REL = "tools/INVENTORY.md"
TOOLS_DIR = "tools"
ENGINE_TOOLS_DIR = "Engine/tools"

VALID_STATUSES = frozenset({"active", "dormant", "legacy", "archived", "非工具"})
LOCAL_STATUS_HINTS = ("本地", "非工具", "不入仓", "未跟踪")
REQUIRED_FIELDS = (
    ("purpose", "用途"),
    ("owner", "owner"),
    ("last_commit", "最后提交"),
    ("test_method", "测试/验证"),
)
EMPTY_VALUES = frozenset({"", "—", "-", "–", "无"})

# 盘面忽略项：清单自身 + 本地生成物（.gitignore 覆盖，不入仓、不计入台账）
IGNORED_ENTRIES = frozenset({"INVENTORY.md", "__pycache__", "dump_outline", ".DS_Store"})
IGNORED_SUFFIXES = (".pyc",)

# subprocess 白名单：只允许 `git ls-files`（命令与参数写死，不经 shell）
GIT_ALLOWED_SUBCOMMANDS = frozenset({"ls-files"})


# ---------------------------------------------------------------------------
# 路径 / 解析
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """仓根 = 本文件所在 tools/ 的上一级。"""
    return Path(__file__).resolve().parent.parent


def _split_row(line: str) -> list[str]:
    """按「反引号外的 |」切分 markdown 表格行；容忍单元格里的 `a|b` 字面量。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    escaped = False
    for ch in body:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            buf.append(ch)
            escaped = True
        elif ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def _parse_status(raw: str) -> tuple[str, bool]:
    """状态列 → (规范状态, 是否本地生成物)。`**dormant**（批91…）` → ('dormant', False)。"""
    is_local = any(h in raw for h in LOCAL_STATUS_HINTS)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", raw.strip())
    cleaned = cleaned.replace("__", "").strip(" *_")
    head = re.split(r"[（(]", cleaned, maxsplit=1)[0].strip()
    return head.lower(), is_local


def _parse_name_cell(raw: str) -> tuple[str, bool, int | None, str | None]:
    """项列 → (name, is_dir, 行数注记, 行数注记所指文件)。

    `batch_accept.py`（**v1.2.0**；实测 1333 行）      → ('batch_accept.py', False, 1333, None)
    `ai_settings_test/`（`main.swift` 3517 行，…）     → ('ai_settings_test', True, 3517, 'main.swift')
    """
    match = re.search(r"`([^`]+)`", raw)
    if match:
        name = match.group(1).strip()
    else:
        name = re.split(r"[（(]", raw, maxsplit=1)[0].strip().strip("`").strip()
    if not name:
        return "", False, None, None
    is_dir = name.endswith("/")
    name = name.rstrip("/")
    line_count: int | None = None
    line_file: str | None = None
    count_match = re.search(r"(\d+)\s*行", raw)
    if count_match:
        line_count = int(count_match.group(1))
        for fm in re.finditer(r"`([^`]+)`", raw):
            candidate = fm.group(1).strip()
            if candidate in (name, name + "/"):
                continue
            line_file = candidate
            break
    return name, is_dir, line_count, line_file


def _parse_inventory(text: str) -> dict:
    """解析 INVENTORY：preamble 声明值 + 第 1/2 节登记项 + malformed 行。"""
    lines = text.splitlines()
    preamble: list[str] = []
    body: list[str] = []
    seen_section = False
    for line in lines:
        if line.strip().startswith("## "):
            seen_section = True
        if seen_section:
            body.append(line)
        else:
            preamble.append(line)

    items: list[dict] = []
    malformed: list[dict] = []
    current: str | None = None
    for offset, line in enumerate(body, 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            if re.match(r"##\s*1[.．、\s]", stripped):
                current = "tools"
            elif re.match(r"##\s*2[.．、\s]", stripped):
                current = "engine_tools"
            else:
                current = None
            continue
        if current is None or not stripped.startswith("|"):
            continue
        if stripped.startswith("|---"):
            continue
        cells = _split_row(stripped)
        if cells and cells[0] == "项":
            continue  # 表头行
        if len(cells) < 7:
            malformed.append({"line": offset, "cells": len(cells), "raw": stripped[:80]})
            continue
        name, is_dir, line_count, line_file = _parse_name_cell(cells[0])
        if not name:
            malformed.append({"line": offset, "cells": len(cells), "raw": stripped[:80]})
            continue
        status, status_local = _parse_status(cells[5])
        notes = "|".join(cells[6:]) if len(cells) > 7 else cells[6]
        is_local = status_local or any(h in notes for h in LOCAL_STATUS_HINTS)
        prefix = TOOLS_DIR if current == "tools" else ENGINE_TOOLS_DIR
        items.append({
            "name": name,
            "is_dir": is_dir,
            "path": f"{prefix}/{name}",
            "section": current,
            "purpose": cells[1],
            "owner": cells[2],
            "last_commit": cells[3],
            "test_method": cells[4],
            "status": status,
            "raw_status": cells[5],
            "notes": notes,
            "is_local": is_local,
            "line_count": line_count,
            "line_file": line_file,
        })

    preamble_text = "\n".join(preamble)
    declared_items = None
    match = re.search(r"\*\*(\d+)\s*项", preamble_text)
    if match:
        declared_items = int(match.group(1))
    declared_parts = None
    match = re.search(r"(\d+)\s*个工具项\s*\+\s*(\d+)\s*个本地生成物", preamble_text)
    if match:
        declared_parts = (int(match.group(1)), int(match.group(2)))
    engine_heading_count = None
    match = re.search(r"##\s*2\.[^\n]*?[（(]\s*(\d+)\s*项\s*[）)]", text)
    if match:
        engine_heading_count = int(match.group(1))

    tool_items = [it for it in items if it["section"] == "tools"]
    engine_items = [it for it in items if it["section"] == "engine_tools"]
    return {
        "items": items,
        "tool_items": tool_items,
        "engine_items": engine_items,
        "malformed": malformed,
        "declared_items": declared_items,
        "declared_parts": declared_parts,
        "engine_heading_count": engine_heading_count,
    }


# ---------------------------------------------------------------------------
# git（唯一子进程：白名单 `git ls-files`）
# ---------------------------------------------------------------------------

def _git_ls_files(repo: Path) -> tuple[list[str] | None, str | None]:
    """返回 (`git ls-files tools` 路径列表, 错误说明)；失败返回 (None, 原因)，绝不抛异常。"""
    command = ["git", "ls-files", "-z", "--", TOOLS_DIR]
    assert command[1] in GIT_ALLOWED_SUBCOMMANDS, "subprocess 白名单只允许 git ls-files"
    try:
        proc = subprocess.run(
            command, cwd=str(repo), capture_output=True, timeout=10, check=False,
        )
    except FileNotFoundError:
        return None, "git 不存在（PATH 里没有）"
    except subprocess.TimeoutExpired:
        return None, "git ls-files 超时（>10s）"
    except OSError as exc:  # noqa: BLE001 —— 任何启动失败都降级，不崩
        return None, f"git 启动失败（{type(exc).__name__}）"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = f"：{err[0]}" if err else ""
        return None, f"git ls-files 退出码 {proc.returncode}{detail}"
    text = proc.stdout.decode("utf-8", "replace")
    return [p for p in text.split("\0") if p], None


# ---------------------------------------------------------------------------
# 盘面扫描 / 行数
# ---------------------------------------------------------------------------

def _iter_entries(root: Path) -> list[tuple[str, bool]]:
    """root 下直接子项 (name, is_dir)，已剔除忽略项；目录不存在 → []。"""
    if not root.is_dir():
        return []
    entries: list[tuple[str, bool]] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        name = path.name
        if name in IGNORED_ENTRIES or name.endswith(IGNORED_SUFFIXES):
            continue
        entries.append((name, path.is_dir()))
    return entries


def _count_lines(path: Path) -> int | None:
    """wc -l 等价；不可读/目录 → None。"""
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def _scan_unregistered(repo: Path, base: str, items: list[dict]) -> list[tuple[str, str]]:
    """盘面有、登记表无 → [(相对仓根路径, 原因)]。目录被整目录登记则不追究子文件；
    目录只被 `dir/child` 形式部分登记（如 `archive/`）时，要求**每个直接子项**都有登记。"""
    registered = {item["name"] for item in items}
    registered_dirs = {item["name"] for item in items if item["is_dir"]}
    pending: list[tuple[str, str]] = []

    for name, is_dir in _iter_entries(repo / base):
        if not is_dir:
            if name not in registered:
                pending.append((f"{base}/{name}", "文件未登记"))
            continue
        if name in registered_dirs:
            continue  # 整目录已登记，子文件不再逐件要求
        covered = any(reg.startswith(name + "/") for reg in registered)
        if not covered:
            pending.append((f"{base}/{name}", "目录未登记"))
            continue
        for child, child_is_dir in _iter_entries(repo / base / name):
            rel = f"{name}/{child}"
            covered_child = rel in registered or (
                child_is_dir and any(reg.startswith(rel + "/") for reg in registered)
            )
            if not covered_child:
                pending.append((f"{base}/{rel}", "目录内文件未登记"))
    return pending


# ---------------------------------------------------------------------------
# 主校验
# ---------------------------------------------------------------------------

def _summarize(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        key = finding["type"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def _error_result(message: str) -> dict:
    return {
        "tool": "inventory_check",
        "version": TOOL_VERSION,
        "ok": False,
        "error": message,
        "inventory": INVENTORY_REL,
        "tracked_source": "unknown",
        "counts": {},
        "findings": [],
        "summary": {},
        "warnings": [message],
    }


def run_check(repo: Path, tracked_provider=None) -> dict:
    """只读校验；repo = 仓根。tracked_provider 供 self-test 注入（返回 (paths|None, err|None)）。"""
    findings: list[dict] = []
    warnings: list[str] = []

    def add(kind: str, path: str, detail: str, **extra) -> None:
        finding = {"type": kind, "path": path, "detail": detail}
        finding.update(extra)
        findings.append(finding)

    inventory_path = repo / INVENTORY_REL
    if not inventory_path.is_file():
        return _error_result(f"INVENTORY.md 不存在：{INVENTORY_REL}")
    try:
        text = inventory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # noqa: BLE001
        return _error_result(f"INVENTORY.md 不可读（{type(exc).__name__}）")

    parsed = _parse_inventory(text)
    items: list[dict] = parsed["items"]

    provider = tracked_provider if tracked_provider is not None else _git_ls_files
    try:
        tracked_list, git_error = provider(repo)
    except Exception as exc:  # noqa: BLE001 —— 注入/外部实现异常也降级，不崩
        tracked_list, git_error = None, f"{type(exc).__name__}: {exc}"
    if tracked_list is None:
        tracked: set[str] | None = None
        tracked_source = "filesystem-fallback"
        warnings.append(
            f"git 不可用/失败（{git_error or '未知原因'}）→ 降级为文件系统扫描；"
            "tracked 计数与「未跟踪」告警跳过"
        )
    else:
        tracked = {str(p).replace("\\", "/") for p in tracked_list}
        tracked_source = "git"

    # ---- malformed 行 ----
    for row in parsed["malformed"]:
        add("malformed_row", INVENTORY_REL,
            f"第 {row['line']} 行表格列数 {row['cells']} < 7（需 7 列）", item=row["raw"])

    # ---- 逐项 ----
    seen: dict[str, str] = {}
    for item in items:
        path = item["path"]
        name = item["name"]
        if path in seen:
            add("duplicate", path, f"同一路径重复登记（另见「{seen[path]}」）", item=name)
        else:
            seen[path] = item["raw_status"]

        if name.startswith(("notes/", "../", "./", "/")) or name.startswith("notes" + "/"):
            add("private_path", path, "私有仓/相对外部路径不得登记进公开 INVENTORY", item=name)
        if name.startswith((TOOLS_DIR + "/", ENGINE_TOOLS_DIR + "/")):
            add("path_style", path, "表内路径应相对本节目录（如 `archive/x`，不写 `tools/archive/x`）",
                item=name)

        if item["status"] not in VALID_STATUSES:
            add("invalid_status", path,
                f"状态「{item['raw_status']}」不合法（期望 active/dormant/legacy/archived/非工具）",
                item=name)

        if item["is_local"]:
            continue  # 本地生成物：不查存在性/必填字段/行数注记

        target = repo / path
        exists = target.is_dir() if item["is_dir"] else target.is_file()
        if not exists:
            add("missing", path,
                f"登记的{'目录' if item['is_dir'] else '文件'}在盘面不存在", item=name)
            continue

        for field, label in REQUIRED_FIELDS:
            if item[field] in EMPTY_VALUES:
                add("missing_field", path, f"缺「{label}」", item=name, field=field)

        if item["line_count"] is not None:
            if item["line_file"]:
                line_target = target / item["line_file"] if item["is_dir"] else target.parent / item["line_file"]
            else:
                line_target = target
            actual = _count_lines(line_target)
            if actual is None:
                add("line_target_missing", path,
                    f"行数注记指向 {line_target.relative_to(repo)}，但该文件不存在/不可读",
                    item=name, expected=item["line_count"])
            elif actual != item["line_count"]:
                add("line_drift", path,
                    f"行数漂移：登记 {item['line_count']} 行，实测 {actual} 行"
                    f"（差 {actual - item['line_count']:+d}）",
                    item=name, expected=item["line_count"], actual=actual)

        if tracked is not None and item["section"] == "tools":
            stamped = path in tracked or (
                item["is_dir"] and any(t.startswith(path + "/") for t in tracked)
            )
            if not stamped:
                warnings.append(f"登记项未被 git 跟踪：{path}（新工具尚未 commit？本批应提交）")

    # ---- 未登记项（盘面为准；git 只做计数与跟踪告警） ----
    for base, section_items in ((TOOLS_DIR, parsed["tool_items"]),
                                (ENGINE_TOOLS_DIR, parsed["engine_items"])):
        for path, reason in _scan_unregistered(repo, base, section_items):
            add("unregistered", path, f"{reason}：盘面存在但登记表没有", item=path)

    # ---- 计数 ----
    tool_rows = len(parsed["tool_items"])
    engine_rows = len(parsed["engine_items"])
    local_rows = sum(1 for item in parsed["tool_items"] if item["is_local"])
    if parsed["declared_items"] is None:
        warnings.append("表头未声明登记项数（`**N 项 = A 个工具项 + B 个本地生成物**`）→ 计数校验跳过")
    elif parsed["declared_items"] != tool_rows:
        add("count_drift", INVENTORY_REL,
            f"表头声明 {parsed['declared_items']} 项，实际第 1 节表 {tool_rows} 行",
            expected=parsed["declared_items"], actual=tool_rows)
    if parsed["declared_parts"] is not None:
        declared_tools, declared_local = parsed["declared_parts"]
        actual_tools = tool_rows - local_rows
        if (declared_tools, declared_local) != (actual_tools, local_rows):
            add("count_drift", INVENTORY_REL,
                f"表头拆分 {declared_tools} 个工具项 + {declared_local} 个本地生成物，"
                f"实测 {actual_tools} + {local_rows}", expected=declared_tools, actual=actual_tools)
    if parsed["engine_heading_count"] is None:
        warnings.append("第 2 节标题未写「（N 项）」→ Engine/tools 计数校验跳过")
    elif parsed["engine_heading_count"] != engine_rows:
        add("count_drift", f"## 2. {ENGINE_TOOLS_DIR}",
            f"第 2 节标题声明 {parsed['engine_heading_count']} 项，实际表 {engine_rows} 行",
            expected=parsed["engine_heading_count"], actual=engine_rows)

    findings.sort(key=lambda f: (f["type"], f.get("path", "")))
    counts = {
        "registered_items": len(items),
        "tools_items": tool_rows,
        "engine_tools_items": engine_rows,
        "local_items": local_rows,
        "tracked_paths": len(tracked) if tracked is not None else None,
    }
    return {
        "tool": "inventory_check",
        "version": TOOL_VERSION,
        "ok": not findings,
        "inventory": INVENTORY_REL,
        "tracked_source": tracked_source,
        "counts": counts,
        "findings": findings,
        "summary": _summarize(findings),
        "warnings": sorted(set(warnings)),
    }


# ---------------------------------------------------------------------------
# 输出 / 退出码
# ---------------------------------------------------------------------------

def format_human(result: dict) -> str:
    lines = [f"[inventory_check v{TOOL_VERSION}] 只读 / 离线 / 纯 stdlib"]
    if result.get("error"):
        lines.append(f"ERROR: {result['error']}")
        return "\n".join(lines)
    counts = result["counts"]
    lines.append(
        f"INVENTORY: {result['inventory']}（登记 {counts['registered_items']} 项 = "
        f"tools {counts['tools_items']} + Engine/tools {counts['engine_tools_items']}；"
        f"本地生成物 {counts['local_items']}）"
    )
    tracked = counts["tracked_paths"]
    lines.append(
        f"实测: git ls-files tools = {tracked if tracked is not None else '不可用'} "
        f"个 tracked 路径（source={result['tracked_source']}）"
    )
    for warning in result["warnings"]:
        lines.append(f"WARN: {warning}")
    if result["ok"]:
        lines.append(f"✓ 一致：0 finding（WARN {len(result['warnings'])} 条）")
    else:
        lines.append(f"✗ 发现 {len(result['findings'])} 项不一致：")
        for index, finding in enumerate(result["findings"], 1):
            lines.append(f"  [{index}] {finding['type']}: {finding['path']} — {finding['detail']}")
        summary = ", ".join(f"{k}={v}" for k, v in sorted(result["summary"].items()))
        lines.append(f"汇总: {summary}")
    return "\n".join(lines)


def format_json(result: dict) -> str:
    """确定性 JSON（无时间戳；findings/warnings 已排序）。"""
    return json.dumps(result, ensure_ascii=False, indent=2)


def exit_code(mode: str, result: dict) -> int:
    """写死的退出码约定；`--check` 与 `--json` 分离（见文件头 docstring）。"""
    if result.get("error"):
        return 2 if mode == "check" else 1
    if mode == "check":
        return 0 if result["ok"] else 1
    return 0  # --json：分析完成即 0，结论看 ok/findings


# ---------------------------------------------------------------------------
# --self-test（hermetic：tempfile 假仓 + 注入 tracked provider；不初始化 git）
# ---------------------------------------------------------------------------

_HEADER = "| 项 | 用途 | owner | 最后提交 | 测试/验证 | 状态 | 备注 |"
_SEP = "|---|---|---|---|---|---|---|"


def _content(lines: int) -> str:
    return "\n".join(f"line{index}" for index in range(lines)) + "\n"


def _row(
    name_cell: str,
    *,
    purpose: str = "测试工具",
    owner: str = "engine-core",
    last: str = "2026-10-22",
    test: str = "`--self-test`",
    status: str = "active",
    notes: str = "—",
) -> str:
    return f"| {name_cell} | {purpose} | {owner} | {last} | {test} | {status} | {notes} |"


def _inventory_text(
    *,
    tools_rows: list[str],
    engine_rows: list[str] | None = None,
    declared_items: int | None = None,
    declared_parts: tuple[int, int] | None = None,
    engine_count: int | None = None,
) -> str:
    engine_rows = engine_rows or []
    if declared_items is None:
        declared_items = len(tools_rows)
    if declared_parts is None:
        local = sum(1 for row in tools_rows if "非工具" in row or "本地" in row)
        declared_parts = (declared_items - local, local)
    if engine_count is None:
        engine_count = len(engine_rows)
    return "\n".join([
        "# 工具清单（self-test 夹具）",
        "",
        f"> 范围：本仓 `tools/`（**{declared_items} 项 = {declared_parts[0]} 个工具项 + "
        f"{declared_parts[1]} 个本地生成物**）+ `Engine/tools/`（{engine_count} 项）。",
        "",
        "## 1. 本仓 `tools/`",
        "",
        _HEADER, _SEP, *tools_rows,
        "",
        f"## 2. `Engine/tools/`（{engine_count} 项）",
        "",
        _HEADER, _SEP, *engine_rows,
        "",
    ])


def _make_repo(
    td: str,
    *,
    files: dict[str, str | None] | None = None,
    tools_rows: list[str] | None = None,
    engine_rows: list[str] | None = None,
    declared_items: int | None = None,
    declared_parts: tuple[int, int] | None = None,
    engine_count: int | None = None,
) -> Path:
    repo = Path(td) / "repo"
    (repo / TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    (repo / ENGINE_TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {}).items():
        target = repo / rel
        if rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_content(3) if content is None else content, encoding="utf-8")
    (repo / INVENTORY_REL).write_text(
        _inventory_text(
            tools_rows=tools_rows or [],
            engine_rows=engine_rows or [],
            declared_items=declared_items,
            declared_parts=declared_parts,
            engine_count=engine_count,
        ),
        encoding="utf-8",
    )
    return repo


def _provider(tracked: list[str] | None):
    def provide(_repo: Path):
        if tracked is None:
            return None, "夹具：git 不可用"
        return list(tracked), None
    return provide


def _by_type(result: dict, kind: str) -> list[dict]:
    return [f for f in result["findings"] if f["type"] == kind]


def _std_tracked(*extra: str) -> list[str]:
    return [INVENTORY_REL, *extra]


def _assert_codes(result: dict, check: int, machine: int) -> str:
    got_check = exit_code("check", result)
    got_json = exit_code("json", result)
    if (got_check, got_json) != (check, machine):
        raise AssertionError(f"退出码 check={got_check}/{machine}, json={got_json}/{machine}")
    return f"check={got_check} json={got_json}"


def run_self_test() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 —— 自测失败也要有名字，不中断整轮
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        print(f"[inventory_check self-test] {name:<28} {'PASS' if ok else 'FAIL'} {detail}".rstrip())

    # 1) 完全一致
    def case_consistent() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/my_tool.py": _content(3), "Engine/tools/gen.py": _content(2)},
                tools_rows=[_row("`my_tool.py`（3 行）")],
                engine_rows=[_row("`gen.py`", owner="rules-docs")],
            )
            tracked = _std_tracked("tools/my_tool.py", "Engine/tools/gen.py")
            result = run_check(repo, _provider(tracked))
            if result["findings"]:
                return False, f"不应有 finding：{result['findings']}"
            if result["warnings"]:
                return False, f"不应有 WARN：{result['warnings']}"
            if not result["ok"]:
                return False, "ok 应为 True"
            if result["counts"]["tracked_paths"] != len(tracked):
                return False, f"tracked 计数 {result['counts']['tracked_paths']}"
            human = format_human(result)
            if "✓ 一致" not in human or "0 finding" not in human:
                return False, "人类可读输出缺一致性行"
            doc = json.loads(format_json(result))
            for key in ("ok", "inventory", "tracked_source", "counts", "findings",
                        "summary", "warnings", "version"):
                if key not in doc:
                    return False, f"JSON 缺字段 {key}"
            if format_json(result) != format_json(run_check(repo, _provider(tracked))):
                return False, "同一输入两次 JSON 不一致（非确定性）"
            codes = _assert_codes(result, 0, 0)
            return True, f"0 finding；{codes}；JSON 确定"

    # 2) 登记项缺文件
    def case_missing() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, tools_rows=[_row("`ghost.py`")])
            result = run_check(repo, _provider([]))
            if not _by_type(result, "missing"):
                return False, "缺 missing finding"
            return True, f"missing 1 项；{_assert_codes(result, 1, 0)}"

    # 3) 未登记文件
    def case_unregistered() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/undocumented.py": _content(1)})
            result = run_check(repo, _provider(_std_tracked()))
            hits = _by_type(result, "unregistered")
            if not hits or hits[0]["path"] != "tools/undocumented.py":
                return False, f"unregistered 未命中：{result['findings']}"
            return True, "未登记文件命中；exit 1"

    # 4) 行数漂移（文件）
    def case_line_drift() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/growing.py": _content(100)},
                tools_rows=[_row("`growing.py`（80 行）")],
            )
            result = run_check(repo, _provider(_std_tracked("tools/growing.py")))
            hits = _by_type(result, "line_drift")
            if not hits or (hits[0]["expected"], hits[0]["actual"]) != (80, 100):
                return False, f"line_drift 未命中：{result['findings']}"
            return True, "登记 80 / 实测 100 → exit 1"

    # 5) 非法状态
    def case_invalid_status() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/weird.py": _content(1)},
                              tools_rows=[_row("`weird.py`", status="broken")])
            result = run_check(repo, _provider(_std_tracked("tools/weird.py")))
            if not _by_type(result, "invalid_status"):
                return False, "缺 invalid_status"
            return True, "broken → invalid_status"

    # 6) 缺必填字段
    def case_missing_field() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/no_owner.py": _content(1)},
                              tools_rows=[_row("`no_owner.py`", owner="—")])
            result = run_check(repo, _provider(_std_tracked("tools/no_owner.py")))
            if not _by_type(result, "missing_field"):
                return False, "缺 missing_field"
            return True, "owner=— → missing_field"

    # 7) 私有仓路径
    def case_private_path() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/notes/tools/secret.py": _content(1)},
                tools_rows=[_row("`notes/tools/secret.py`")],
            )
            result = run_check(repo, _provider(_std_tracked()))
            if not _by_type(result, "private_path"):
                return False, f"缺 private_path：{result['findings']}"
            return True, "notes/ 路径 → private_path"

    # 8) 登记目录缺失
    def case_missing_dir() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, tools_rows=[_row("`ghost_dir/`")])
            result = run_check(repo, _provider(_std_tracked()))
            if not _by_type(result, "missing"):
                return False, "缺 missing（目录）"
            return True, "目录缺失 → missing"

    # 9) 目录项行数漂移
    def case_dir_line_drift() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/mytest/main.swift": _content(50)},
                tools_rows=[_row("`mytest/`（`main.swift` 40 行）")],
            )
            result = run_check(repo, _provider(_std_tracked("tools/mytest/main.swift")))
            hits = _by_type(result, "line_drift")
            if not hits or (hits[0]["expected"], hits[0]["actual"]) != (40, 50):
                return False, f"目录行数漂移未命中：{result['findings']}"
            return True, "main.swift 登记 40 / 实测 50"

    # 10) 表头项数漂移
    def case_count_drift_items() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/one.py": _content(1)},
                              tools_rows=[_row("`one.py`")], declared_items=2)
            result = run_check(repo, _provider(_std_tracked("tools/one.py")))
            hits = _by_type(result, "count_drift")
            if not hits or hits[0]["actual"] != 1:
                return False, f"count_drift 未命中：{result['findings']}"
            return True, "声明 2 / 实际 1 行"

    # 11) 表头「工具项 + 本地生成物」拆分漂移
    def case_count_drift_parts() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/one.py": _content(1)},
                              tools_rows=[_row("`one.py`")], declared_parts=(2, 0))
            result = run_check(repo, _provider(_std_tracked("tools/one.py")))
            if not _by_type(result, "count_drift"):
                return False, f"拆分漂移未命中：{result['findings']}"
            return True, "声明 2+0 / 实测 1+0"

    # 12) 第 2 节标题项数漂移
    def case_count_drift_engine() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, tools_rows=[], engine_rows=[_row("`gen.py`")], engine_count=2)
            result = run_check(repo, _provider(_std_tracked()))
            if not _by_type(result, "count_drift"):
                return False, f"Engine 计数漂移未命中：{result['findings']}"
            return True, "标题 2 项 / 实际 1 行"

    # 13) 重复登记
    def case_duplicate() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/twice.py": _content(1)},
                              tools_rows=[_row("`twice.py`"), _row("`twice.py`")])
            result = run_check(repo, _provider(_std_tracked("tools/twice.py")))
            if not _by_type(result, "duplicate"):
                return False, "缺 duplicate"
            return True, "同一路径两行 → duplicate"

    # 14) 本地生成物：忽略项不查、不误报
    def case_local_ignored() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/__pycache__/x.pyc": "", "tools/dump_outline": "binary"},
                tools_rows=[
                    _row("`dump_outline`（本地二进制）", status="legacy（本地）",
                         last="未跟踪（.gitignore）", test="无"),
                    _row("`__pycache__/`（本地）", status="非工具", owner="—",
                         last="未跟踪（.gitignore）", test="无"),
                ],
            )
            result = run_check(repo, _provider([]))
            if result["findings"]:
                return False, f"本地项不应产生 finding：{result['findings']}"
            return True, "本地2项/missing0/未登记0"

    # 15) git 不可用 → 降级 WARN，结论不崩
    def case_git_fallback_ok() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/my_tool.py": _content(1)},
                              tools_rows=[_row("`my_tool.py`")])
            result = run_check(repo, _provider(None))
            if not result["ok"] or result["tracked_source"] != "filesystem-fallback":
                return False, f"降级结果不对：ok={result['ok']} source={result['tracked_source']}"
            if not result["warnings"] or result["counts"]["tracked_paths"] is not None:
                return False, f"WARN/tracked 字段不对：{result['warnings']}"
            if "WARN:" not in format_human(result):
                return False, "人类可读输出缺 WARN 行"
            return True, "source=filesystem-fallback；WARN 明确；exit 0"

    # 16) git 不可用仍能抓未登记
    def case_git_fallback_unregistered() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/new_thing.py": _content(1)})
            result = run_check(repo, _provider(None))
            if not _by_type(result, "unregistered"):
                return False, "降级后漏抓未登记"
            return True, "降级仍抓未登记；exit 1"

    # 17) 退出码语义分离（不一致 vs 无法分析）
    def case_exit_semantics() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, tools_rows=[_row("`ghost.py`")])
            inconsistent = run_check(repo, _provider([]))
            if (exit_code("check", inconsistent), exit_code("json", inconsistent)) != (1, 0):
                return False, "不一致时退出码未分离"
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            empty = Path(td) / "no-inventory"
            (empty / TOOLS_DIR).mkdir(parents=True)
            broken = run_check(empty, _provider([]))
            if (exit_code("check", broken), exit_code("json", broken)) != (2, 1):
                return False, "无法分析时退出码不对"
            if "ERROR" not in format_human(broken) or not broken.get("error"):
                return False, "错误结果缺 ERROR/error"
            doc = json.loads(format_json(broken))
            if doc["ok"] is not False or not doc["error"]:
                return False, "错误 JSON 不对"
        return True, "不一致 check=1/json=0；无法分析 check=2/json=1"

    # 18) 已登记但未跟踪 → WARN（不红）
    def case_untracked_warn() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/tracked_later.py": _content(1)},
                              tools_rows=[_row("`tracked_later.py`")])
            result = run_check(repo, _provider(_std_tracked()))
            if result["findings"] or not any("未被 git 跟踪" in w for w in result["warnings"]):
                return False, f"未跟踪 WARN 不对：{result['warnings']} / {result['findings']}"
            return True, "未 commit 只 WARN；exit 0"

    # 19) 路径口径：表内 `tools/…` 前缀 → path_style
    def case_path_style() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(td, files={"tools/x.py": _content(1)},
                              tools_rows=[_row("`tools/x.py`")])
            result = run_check(repo, _provider(_std_tracked("tools/x.py")))
            if not _by_type(result, "path_style"):
                return False, "缺 path_style"
            return True, "tools/x.py → path_style + unregistered"

    # 20) archive/ 逐件登记算已登记
    def case_archive_ok() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/archive/old.py": _content(1),
                       "tools/archive/sub/main.swift": _content(1)},
                tools_rows=[_row("`archive/old.py`", status="archived"),
                            _row("`archive/sub/main.swift`", status="archived", owner="mac-shell")],
            )
            result = run_check(repo, _provider(_std_tracked("tools/archive/old.py",
                                                           "tools/archive/sub/main.swift")))
            if _by_type(result, "unregistered"):
                return False, f"archive 子项被误判未登记：{result['findings']}"
            return True, "archive 2 件登记/0 未登记"

    # 21) 行数注记指向文件不存在
    def case_line_target_missing() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/mytool/other.swift": _content(1)},
                tools_rows=[_row("`mytool/`（`main.swift` 12 行）")],
            )
            result = run_check(repo, _provider(_std_tracked("tools/mytool/other.swift")))
            if not _by_type(result, "line_target_missing"):
                return False, f"缺 line_target_missing：{result['findings']}"
            return True, "注记文件不存在 → line_target_missing"

    # 22) 含 `|` 字面量的备注列仍能正确解析（本仓真实第 23 行形态）
    def case_pipe_in_cell() -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="inventory-selftest-") as td:
            repo = _make_repo(
                td,
                files={"tools/piped.py": _content(1)},
                tools_rows=[_row("`piped.py`", notes="见 `db_sha_before|after:` 形态")],
            )
            result = run_check(repo, _provider(_std_tracked("tools/piped.py")))
            if result["findings"]:
                return False, f"反引号内 | 被误切列：{result['findings']}"
            return True, "反引号内 | 不切列"

    print("[inventory_check self-test] hermetic 夹具（tempfile；不初始化 git、不碰真实仓）")
    check("consistent", case_consistent)
    check("missing_file", case_missing)
    check("unregistered", case_unregistered)
    check("line_drift", case_line_drift)
    check("invalid_status", case_invalid_status)
    check("missing_field", case_missing_field)
    check("private_path", case_private_path)
    check("missing_dir", case_missing_dir)
    check("dir_line_drift", case_dir_line_drift)
    check("count_drift_items", case_count_drift_items)
    check("count_drift_parts", case_count_drift_parts)
    check("count_drift_engine", case_count_drift_engine)
    check("duplicate", case_duplicate)
    check("local_ignored", case_local_ignored)
    check("git_fallback_ok", case_git_fallback_ok)
    check("git_fallback_unregistered", case_git_fallback_unregistered)
    check("exit_semantics", case_exit_semantics)
    check("untracked_warn", case_untracked_warn)
    check("path_style", case_path_style)
    check("archive_registered", case_archive_ok)
    check("line_target_missing", case_line_target_missing)
    check("pipe_in_cell", case_pipe_in_cell)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"[inventory_check self-test] {passed}/{total} PASS"
          + ("" if passed == total else " ← 有失败"))
    return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="inventory_check.py",
        description="tools/INVENTORY.md 看门狗：登记项 ↔ 盘面 ↔ 计数一致性（只读/离线/确定性）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        退出码（写死；--check 与 --json 分离）:
          --check     0 = 一致（可含 WARN）; 1 = 发现不一致; 2 = 无法分析
          --json      0 = 分析完成（无论是否一致，看 ok/findings）; 1 = 无法分析; 2 = 用法错误
          --self-test 0 = 全绿; 1 = 有失败用例

        示例:
          python3 tools/inventory_check.py --check
          python3 tools/inventory_check.py --json > /tmp/inventory.json
          python3 tools/inventory_check.py --self-test
        """),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="人类可读 + 退出码（不一致 exit 1）")
    group.add_argument("--json", action="store_true", help="机器可读 JSON（分析完成即 exit 0）")
    group.add_argument("--self-test", action="store_true", help="hermetic 自测（tempfile，≥10 用例）")
    parser.add_argument("--version", action="version", version=f"inventory_check {TOOL_VERSION}")
    args = parser.parse_args(argv)

    if not (args.check or args.json or args.self_test):
        parser.error("需指定 --check / --json / --self-test 之一")

    if args.self_test:
        sys.dont_write_bytecode = True
        return run_self_test()

    try:
        result = run_check(repo_root())
    except Exception as exc:  # noqa: BLE001 —— 内部异常也转成明确错误，不抛栈
        result = _error_result(f"内部错误（{type(exc).__name__}: {exc}）")

    if args.json:
        print(format_json(result))
        return exit_code("json", result)
    print(format_human(result))
    return exit_code("check", result)


if __name__ == "__main__":
    sys.exit(main())
