#!/usr/bin/env python3
"""rule_dryrun.py —— 规则改动的全语料 dry-run + 两版 A/B 回归（B-146 波2-1 / 批105）

一句话：给定基线版 A（git ref，默认 `HEAD`）与候选版 B（默认当前工作树，也可给 git ref），
对语料逐篇跑「装配 + 打分」，输出逐篇 A/B 差异分类
（`unchanged` / `changed` / `new_failure` / `fixed`）；**只读稿件库、不污染 `Engine/out/`、
不 commit**。规则改动（`Engine/slcn/` 判据/解析/渲染代码）提交前跑它，替代「事后门禁兜底」。

为什么要有它
------------
规则改动提交前无法预知全语料影响：跑 `full` 只看回归红/绿，跑 `Engine/corpus.py` 只看
打分分布，都答不了「哪些篇的产出会变、变成什么、是否新增失败」。本工具把两版引擎各跑一遍
`corpus.py`（装配 + 打分），逐篇比较打分行与产出文件 sha 清单并分类。

做法（复用口径，不复制装配逻辑）
--------------------------------
1. A/B 版本：`git worktree add --detach <tmp> <ref>` 物化到临时目录（只读）；B 默认直接读
   当前工作树（含未提交改动）。结束 `git worktree remove --force` 清理。
2. 在**隔离的临时 OUT** 里跑版本自己的 `Engine/corpus.py`（子进程，钉
   `/opt/homebrew/bin/python3.14`，`PYTHONPATH=<版本>/Engine:<本工具仓>/Engine/vendor`）。
   ⚠️ 与规格的一处偏离：`corpus.py` 的 `OUT` 写死为 `Engine/out/corpus`、没有覆盖钩子；
   为满足「不污染 `Engine/out/`」，本工具用一个极薄的子进程入口 import 该版本的 `corpus`
   后把模块级 `OUT` 指到临时目录再调 `corpus.main()` —— 跑的是同一份代码路径。
   `Engine/vendor` 不入库（pip 本地安装物），故取**本工具所在仓**的 vendor，而不是版本树里的
   （worktree 里没有它；这不是被测规则的一部分）。
3. 比较：逐篇读 `corpus.csv` 打分行 + 扫描产出目录（`<OUT>/<期号>_<篇名>/`，跳过 `*.bak`）
   算文件 sha256 → 分类。篇目顺序固定为 A 版 `corpus.csv` 的顺序；串行/并行（`--jobs`）
   结果一致（`corpus.py` 自身保证，本工具按固定顺序汇总）。
4. 只读性自证：运行前后对稿件库整树、被测仓 `Engine/out/` 做元数据快照（路径/大小/mtime），
   被改则明确报错、不静默。

分类
----
- `unchanged`：两版都能装配打分，打分行与产出文件 sha 完全一致（含两版都失败的同一篇）。
- `changed`  ：两版都能跑，但打分行（通道/语料/指标/错误）或产出文件 sha 有差异。
- `new_failure`：A 成功、B 失败（新增失败）——最需要人看的一类。
- `fixed`    ：A 失败、B 成功（修好了）。不计入回归。

用法
----
  # A/A 恒等（本工具最常用：确认工具自身零漂移）
  python3 tools/rule_dryrun.py --a HEAD --b worktree --limit 5 --out /tmp/rule-aa

  # 规则改动提交前预检：A=HEAD，B=当前工作树，只看摘要
  python3 tools/rule_dryrun.py --check

  # 两版提交/分支对比
  python3 tools/rule_dryrun.py --a HEAD --b <git-ref> --jobs auto --out /tmp/ab

  # 机读
  python3 tools/rule_dryrun.py --a HEAD --b worktree --json > result.json

  # hermetic 自检（tempfile 假语料 + 注入假流水线；不读真实稿件库、不联网、不初始化 git）
  python3 tools/rule_dryrun.py --self-test

退出码（**写死**）
------------------
- `0` 分析完成：无 `new_failure`，且 `changed` 数 ≤ `--max-changed`（默认 0）。
- `1` 检出 `new_failure`，或 `changed` 数 > `--max-changed`。
- `2` 用法/输入错误（坏 ref、坏参数、读不到 `corpus.csv`、git 不可用…）。
  **补充**：若运行前后只读性不变量被破坏（稿件库或 `Engine/out/` 被改动），也返回 2
  —— 分析结论不可信，不能当绿放过（`--json` 的 `invariants` 字段给出明细）。
- `--self-test`：0 = 全绿；1 = 有失败用例。

只读 / 离线 / 确定性 / 纯 stdlib / 单文件（本仓工具 DoD）
--------------------------------------------------------
- 不写稿件库、不写被测仓（`Engine/out/` 只做快照校验）、不 commit、不联网、不跑 `full`。
- `--out` 是唯一写出口（JSON/MD/CSV）；不传 `--out` 时只打印摘要。
- 输出（JSON/MD/CSV）**不含时间戳/耗时/临时绝对路径**，同一状态两次运行逐字节相同。
- 自检 hermetic：fake 语料 + fake 版本目录 + 注入假流水线；不依赖 git 仓库初始化。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field

TOOL_VERSION = "1.0.0"
SCHEMA = 1

#: 工具假定自己位于 `<仓库>/tools/`（INVENTORY 口径：表内路径相对 tools/）
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
#: 解释器钉死（`Engine/vendor` 的 lxml 编译件按 3.14 装）；可用 `--python` 覆盖
DEFAULT_PYTHON = "/opt/homebrew/bin/python3.14"

WORKTREE = "worktree"

#: 分类（顺序即报告里的展示顺序）
CATEGORIES = ("unchanged", "changed", "new_failure", "fixed")

#: `Engine/corpus.py` 写出的 CSV 列（比较口径；缺列视为输入错误）
CSV_FIELDS = (
    "issue", "slug", "channel", "lang", "ok", "raw_ratio", "content_acc",
    "l2", "l3", "ref_chars", "src_tbl", "out_tbl", "src_img", "out_img",
    "src_shape", "out_shape", "error",
)
FLOAT_FIELDS = ("raw_ratio", "content_acc")
INT_FIELDS = ("l2", "l3", "ref_chars", "src_tbl", "out_tbl",
              "src_img", "out_img", "src_shape", "out_shape")
STR_FIELDS = ("channel", "lang")

#: 子进程入口（写进临时目录；见文件头「做法」第 2 条）
WRAPPER_SOURCE = '''#!/usr/bin/env python3
"""rule_dryrun 子进程入口：import 指定版本的 Engine/corpus.py，把模块级 OUT 指到隔离目录。

⚠️ 只做这一件事：跑的还是该版本 `corpus.py` 的 main()（同一份装配+打分工序），
不改产品代码。`RULE_DRYRUN_ENGINE` / `RULE_DRYRUN_OUT` 由父进程经环境变量传入
（spawn 出来的进程池子进程也会继承，从而同样写到隔离目录）。
"""

import os
import sys

ENGINE = os.environ["RULE_DRYRUN_ENGINE"]
OUT = os.environ["RULE_DRYRUN_OUT"]
sys.path.insert(0, ENGINE)
import corpus  # noqa: E402

corpus.OUT = OUT

if __name__ == "__main__":
    sys.argv = ["corpus.py"] + sys.argv[1:]
    raise SystemExit(corpus.main())
'''

#: 快照时跳过的目录（稿件库 `.git` 有 2GB；`__pycache__` 是本地缓存）
SNAPSHOT_SKIP_DIRS = frozenset({".git", "__pycache__", ".DS_Store"})

#: 单个版本 corpus 子进程的超时（全库约 2 分钟量级；给足余量）
CORPUS_TIMEOUT_S = 3600
GIT_TIMEOUT_S = 300


class ToolError(Exception):
    """用法/输入/环境错误 → 退出码 2（不静默、不吞异常）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slug_dir(issue: str, slug: str) -> str:
    """篇目产出目录名：与 `Engine/corpus.py` 的 `run_one()` 口径一致。

    `issue[:4]` = 期号年份；篇名空格换下划线：`2026年第5期(90)` + `03 Liang_Zhang`
    → `2026_03_Liang_Zhang`。仅用于定位产出目录，不复制任何装配/扫描逻辑。
    """
    return f"{issue[:4]}_{slug.replace(' ', '_')}"


def _git_executable() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ToolError("找不到 git（A/B 版本物化需要 git worktree）")
    return exe


def _git(args: list[str], *, cwd: str, timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """跑一条只读/版本管理 git 命令（`-C` 指定仓；不经 shell）。"""
    try:
        return subprocess.run(
            [_git_executable(), "-C", cwd, *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"git {' '.join(args)} 超时（{timeout}s）")
    except OSError as e:
        raise ToolError(f"git 不可用：{e}")


def _resolve_ref(repo: str, ref: str) -> str:
    """把 ref 解析成完整 commit sha；解析不了 → ToolError（明确报错，不静默）。"""
    proc = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo)
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or not sha:
        detail = _short(proc.stderr or "未知错误")
        raise ToolError(f"无法解析 git ref {ref!r}（{detail}）")
    return sha


def detect_repo(preferred: str | None = None) -> str:
    """定位被测仓：`--repo` 优先；否则用当前目录所在 git 工作树；再退回本工具所在仓。"""
    if preferred:
        return os.path.abspath(os.path.expanduser(preferred))
    try:
        proc = subprocess.run(
            [_git_executable(), "-C", os.getcwd(), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
        top = (proc.stdout or "").strip()
        if proc.returncode == 0 and top and os.path.isdir(os.path.join(top, "Engine")):
            return top
    except (OSError, subprocess.TimeoutExpired, ToolError):
        pass
    return REPO_ROOT


def validate_repo(repo: str) -> None:
    if not os.path.isdir(repo):
        raise ToolError(f"--repo 不是目录：{repo}")
    if not os.path.isfile(os.path.join(repo, "Engine", "corpus.py")):
        raise ToolError(f"{repo} 不像本仓（缺 Engine/corpus.py）")
    proc = _git(["rev-parse", "--is-inside-work-tree"], cwd=repo)
    if proc.returncode != 0 or (proc.stdout or "").strip() != "true":
        raise ToolError(f"{repo} 不是 git 工作树（A/B 版本物化需要）")


def load_paths_module(engine_dir: str):
    """从指定版本的 `Engine/slcn/paths.py` 加载稿件库锚点（import 复用口径，不复制逻辑）。"""
    path = os.path.join(engine_dir, "slcn", "paths.py")
    if not os.path.isfile(path):
        raise ToolError(f"找不到 {path}（无法解析稿件库）")
    spec = importlib.util.spec_from_file_location("_rule_dryrun_paths", path)
    if spec is None or spec.loader is None:
        raise ToolError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 语料产出解析 / 快照
# ---------------------------------------------------------------------------

def parse_corpus_csv(path: str) -> list[dict]:
    """读 `corpus.csv` → 逐行 dict（值为字符串，保留原始精度）；坏输入 → ToolError。"""
    try:
        fh = open(path, encoding="utf-8", errors="replace", newline="")
    except OSError as e:
        raise ToolError(f"读不到语料结果 {path}：{e}")
    with fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ToolError(f"{path} 为空或没有表头")
        missing = [f for f in CSV_FIELDS if f not in reader.fieldnames]
        if missing:
            raise ToolError(f"{path} 缺列：{', '.join(missing)}")
        rows: list[dict] = []
        for lineno, raw in enumerate(reader, 2):
            row = {k: (raw.get(k) if raw.get(k) is not None else "") for k in CSV_FIELDS}
            if not row["issue"] or not row["slug"]:
                raise ToolError(f"{path} 第 {lineno} 行缺 issue/slug")
            if row["ok"] not in ("0", "1"):
                raise ToolError(f"{path} 第 {lineno} 行 ok 非 0/1：{row['ok']!r}")
            rows.append(row)
    if not rows:
        raise ToolError(f"{path} 没有数据行")
    return rows


def scan_outputs(out_root: str) -> dict[str, dict[str, str]]:
    """扫描隔离 OUT：`{篇目目录名: {相对路径: sha256}}`；跳过 `*.bak`。"""
    files: dict[str, dict[str, str]] = {}
    try:
        names = sorted(os.listdir(out_root))
    except OSError:
        return files
    for name in names:
        d = os.path.join(out_root, name)
        if not os.path.isdir(d):
            continue
        mapping: dict[str, str] = {}
        for root, dirs, fnames in os.walk(d):
            dirs.sort()
            fnames.sort()
            for fn in fnames:
                if fn.endswith(".bak"):          # 覆盖备份带时间戳 → 与内容无关，不比
                    continue
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, d).replace(os.sep, "/")
                try:
                    mapping[rel] = _sha256_file(p)
                except OSError as e:
                    raise ToolError(f"读不到产出文件 {p}：{e}")
        files[name] = mapping
    return files


def snapshot_tree(root: str) -> dict[str, tuple[int, int]]:
    """只读元数据快照：`{相对路径: (size, mtime_ns)}`；不读文件内容、不跟 `.git`。"""
    snap: dict[str, tuple[int, int]] = {}
    if not root or not os.path.isdir(root):
        return snap
    for cur, dirs, fnames in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SNAPSHOT_SKIP_DIRS)
        for fn in sorted(fnames):
            p = os.path.join(cur, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            snap[rel] = (st.st_size, st.st_mtime_ns)
    return snap


def _invariant(before: dict | None, after: dict | None) -> dict:
    """快照对比 → 不变量结论（稳定的布尔 + 相对路径明细）。"""
    if before is None:
        return {"configured": False, "files": 0, "unchanged": True, "changed_paths": []}
    after = after or {}
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "configured": True,
        "files": len(before),
        "unchanged": not changed,
        "changed_paths": changed[:20],
    }


# ---------------------------------------------------------------------------
# 两版运行
# ---------------------------------------------------------------------------

@dataclass
class VersionResult:
    """一版语料的运行结果（只留可确定性比较的部分）。"""
    label: str
    ref: str
    sha: str
    rows: list[dict]
    files: dict[str, dict[str, str]]
    csv_sha: str
    elapsed_s: float = field(default=0.0, repr=False)


@dataclass
class Config:
    """一次 dry-run 的输入（self-test 也用它注入假流水线）。"""
    repo: str
    a_ref: str = "HEAD"
    b_ref: str = WORKTREE
    a_sha: str = ""
    b_sha: str = ""
    limit: int = 0
    jobs: object = "auto"
    max_changed: int = 0
    out_dir: str | None = None
    python: str = DEFAULT_PYTHON
    manuscripts: str | None = None
    work_root: str | None = None
    vendor: str = field(default_factory=lambda: os.path.join(REPO_ROOT, "Engine", "vendor"))


def _remove_worktree(repo: str, path: str) -> None:
    """清理临时 worktree；失败只告警（不吞，但也不掩盖真实错误）。"""
    if not os.path.isdir(path):
        return
    proc = subprocess.run(
        [_git_executable(), "-C", repo, "worktree", "remove", "--force", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        print(
            f"⚠ rule_dryrun：git worktree remove 失败，已直接删目录 {path}"
            f"（{_short(proc.stderr, 120)}）",
            file=sys.stderr,
        )


def _prepare_wrapper(work_root: str) -> str:
    path = os.path.join(work_root, "run_corpus.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER_SOURCE)
    return path


def _run_corpus_version(cfg: Config, side: str, version_root: str) -> VersionResult:
    """在 `version_root` 版本上跑隔离的 corpus 子进程，读回打分行与产出 sha 清单。"""
    engine = os.path.join(version_root, "Engine")
    if not os.path.isfile(os.path.join(engine, "corpus.py")):
        raise ToolError(f"{version_root} 没有 Engine/corpus.py")
    assert cfg.work_root
    wrapper = _prepare_wrapper(cfg.work_root)
    out_root = tempfile.mkdtemp(prefix=f"out-{side}-", dir=cfg.work_root)
    scratch = os.path.join(cfg.work_root, f"cwd-{side}")
    os.makedirs(scratch, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"        # 不往被测仓写 __pycache__
    path_parts = [engine]
    if cfg.vendor and os.path.isdir(cfg.vendor):
        path_parts.append(cfg.vendor)
    env["PYTHONPATH"] = os.pathsep.join(path_parts)
    env["RULE_DRYRUN_ENGINE"] = engine
    env["RULE_DRYRUN_OUT"] = out_root
    if cfg.manuscripts:
        env["SLCN_MANUSCRIPTS"] = cfg.manuscripts

    cmd = [cfg.python, wrapper]
    if cfg.limit:
        cmd += ["--limit", str(cfg.limit)]
    cmd += ["--jobs", str(cfg.jobs)]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=scratch, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CORPUS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{side.upper()} 版 corpus 运行超时（{CORPUS_TIMEOUT_S}s）")
    except OSError as e:
        raise ToolError(f"{side.upper()} 版 corpus 启动失败：{e}")
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        tail = _short(proc.stderr or proc.stdout or "", 400)
        raise ToolError(f"{side.upper()} 版 corpus 退出码 {proc.returncode}：{tail}")

    csv_path = os.path.join(out_root, "corpus.csv")
    if not os.path.isfile(csv_path):
        tail = _short(proc.stdout or proc.stderr or "", 400)
        raise ToolError(
            f"{side.upper()} 版 corpus 没有产出 corpus.csv（稿件库不可用？）：{tail}"
        )
    rows = parse_corpus_csv(csv_path)
    files = scan_outputs(out_root)
    ref = cfg.a_ref if side == "a" else cfg.b_ref
    sha = cfg.a_sha if side == "a" else cfg.b_sha
    return VersionResult(
        label=side.upper(), ref=ref, sha=sha, rows=rows, files=files,
        csv_sha=_sha256_file(csv_path), elapsed_s=elapsed,
    )


def run_version(cfg: Config, side: str) -> VersionResult:
    """真实流水线：按需物化 worktree（A 一定物化；B=worktree 时直接读被测工作树）。"""
    assert cfg.work_root
    ref = cfg.a_ref if side == "a" else cfg.b_ref
    needs_worktree = side == "a" or ref != WORKTREE
    if not needs_worktree:
        return _run_corpus_version(cfg, side, cfg.repo)

    dest = os.path.join(cfg.work_root, f"worktree-{side}")
    proc = _git(["worktree", "add", "--detach", dest, ref], cwd=cfg.repo)
    if proc.returncode != 0:
        detail = _short(proc.stderr or proc.stdout or "", 300)
        raise ToolError(
            f"{side.upper()} 版物化失败（git worktree add --detach {ref}）：{detail}"
        )
    try:
        return _run_corpus_version(cfg, side, dest)
    finally:
        _remove_worktree(cfg.repo, dest)


# ---------------------------------------------------------------------------
# 比较 / 汇总
# ---------------------------------------------------------------------------

def _limit_rows(rows: list[dict], limit: int) -> list[dict]:
    return rows[:limit] if limit and limit > 0 else list(rows)


def _diff_rows(a_row: dict, b_row: dict) -> list[str]:
    """比较同一篇的两行；返回人类可读的变化项（空 = 一致）。"""
    changes: list[str] = []
    for f in STR_FIELDS:
        if a_row.get(f, "") != b_row.get(f, ""):
            changes.append(f"{f} {a_row.get(f)!r} → {b_row.get(f)!r}")
    for f in FLOAT_FIELDS:
        av, bv = _as_float(a_row.get(f)), _as_float(b_row.get(f))
        if av is None or bv is None:
            if a_row.get(f) != b_row.get(f):
                changes.append(f"{f} {a_row.get(f)!r} → {b_row.get(f)!r}")
        elif av != bv:
            changes.append(f"{f} {av:.6f} → {bv:.6f}")
    for f in INT_FIELDS:
        av, bv = _as_int(a_row.get(f)), _as_int(b_row.get(f))
        if av is None or bv is None:
            if a_row.get(f) != b_row.get(f):
                changes.append(f"{f} {a_row.get(f)!r} → {b_row.get(f)!r}")
        elif av != bv:
            changes.append(f"{f} {av} → {bv}")
    if (a_row.get("error") or "") != (b_row.get("error") or ""):
        changes.append(f"error {_short(a_row.get('error'), 60)!r} → "
                       f"{_short(b_row.get('error'), 60)!r}")
    return changes


def _diff_files(a_files: dict[str, str], b_files: dict[str, str]) -> tuple[list, list, list]:
    added = sorted(set(b_files) - set(a_files))
    removed = sorted(set(a_files) - set(b_files))
    changed = sorted(k for k in set(a_files) & set(b_files) if a_files[k] != b_files[k])
    return changed, added, removed


def compare_versions(a: VersionResult, b: VersionResult, limit: int = 0) -> list[dict]:
    """逐篇分类，顺序固定为 A 版顺序（B 多出的篇目排在后面、按篇名排序）。"""
    a_rows = _limit_rows(a.rows, limit)
    b_rows = _limit_rows(b.rows, limit)
    b_by_key = {(r["issue"], r["slug"]): r for r in b_rows}
    order = [(r["issue"], r["slug"]) for r in a_rows]
    seen = set(order)
    for r in b_rows:
        key = (r["issue"], r["slug"])
        if key not in seen:
            seen.add(key)
            order.append(key)

    entries: list[dict] = []
    for issue, slug in order:
        a_row = next((r for r in a_rows if (r["issue"], r["slug"]) == (issue, slug)), None)
        b_row = b_by_key.get((issue, slug))
        entry = {
            "issue": issue, "slug": slug, "status": "", "a_ok": None, "b_ok": None,
            "channel": "", "changes": [], "files_changed": [], "files_added": [],
            "files_removed": [], "a_error": "", "b_error": "",
        }
        d = _slug_dir(issue, slug)
        a_f = a.files.get(d, {})
        b_f = b.files.get(d, {})

        if a_row is None:
            entry.update(status="changed", b_ok=b_row.get("ok") == "1",
                         channel=b_row.get("channel", ""),
                         changes=["A 版 corpus.csv 缺该篇（B 版新增）"])
            entry["files_added"] = sorted(b_f)
        elif b_row is None:
            ok = a_row.get("ok") == "1"
            entry.update(status="new_failure" if ok else "changed",
                         a_ok=ok, channel=a_row.get("channel", ""),
                         a_error=a_row.get("error", ""),
                         changes=["B 版 corpus.csv 缺该篇"])
            entry["files_removed"] = sorted(a_f)
        else:
            a_ok, b_ok = a_row.get("ok") == "1", b_row.get("ok") == "1"
            entry.update(a_ok=a_ok, b_ok=b_ok, channel=b_row.get("channel") or a_row.get("channel"),
                         a_error=a_row.get("error", ""), b_error=b_row.get("error", ""))
            if a_ok and not b_ok:
                entry["status"] = "new_failure"
                entry["changes"] = [f"B 版失败：{_short(b_row.get('error'), 100) or '（无错误信息）'}"]
            elif not a_ok and b_ok:
                entry["status"] = "fixed"
                entry["changes"] = [f"A 版失败：{_short(a_row.get('error'), 100) or '（无错误信息）'}"
                                    " → B 版成功"]
            elif not a_ok and not b_ok:
                changes = _diff_rows(a_row, b_row)
                entry["status"] = "changed" if changes else "unchanged"
                entry["changes"] = changes
            else:
                changes = _diff_rows(a_row, b_row)
                f_changed, f_added, f_removed = _diff_files(a_f, b_f)
                if f_changed:
                    changes.append(f"产出文件 sha 变化 {len(f_changed)} 个")
                if f_added:
                    changes.append(f"产出文件新增 {len(f_added)} 个")
                if f_removed:
                    changes.append(f"产出文件缺失 {len(f_removed)} 个")
                entry["files_changed"] = f_changed
                entry["files_added"] = f_added
                entry["files_removed"] = f_removed
                entry["status"] = "changed" if changes else "unchanged"
                entry["changes"] = changes
        entries.append(entry)
    return entries


def summarize(entries: list[dict]) -> dict:
    counts = {cat: 0 for cat in CATEGORIES}
    for e in entries:
        counts[e["status"]] += 1
    counts["total"] = len(entries)
    return counts


def exit_code_for(counts: dict, max_changed: int) -> int:
    """退出码语义（不含只读性不变量；那一条由 run_analysis 覆盖成 2）。"""
    if counts["new_failure"] > 0:
        return 1
    if counts["changed"] > max_changed:
        return 1
    return 0


def run_analysis(cfg: Config, *, runner=None, guards: bool = True) -> dict:
    """跑 A/B 两版 → 比较 → 报告 dict（纯确定性字段；runner 可注入假流水线供自检）。"""
    runner = runner or run_version
    repo_engine_out = os.path.join(cfg.repo, "Engine", "out")
    eo_before = snapshot_tree(repo_engine_out) if (guards and cfg.repo) else None
    ms_before = snapshot_tree(cfg.manuscripts) if (guards and cfg.manuscripts) else None

    a = runner(cfg, "a")
    b = runner(cfg, "b")

    eo_after = snapshot_tree(repo_engine_out) if eo_before is not None else None
    ms_after = snapshot_tree(cfg.manuscripts) if ms_before is not None else None
    invariants = {
        "manuscripts": _invariant(ms_before, ms_after),
        "engine_out": _invariant(eo_before, eo_after),
    }

    entries = compare_versions(a, b, cfg.limit)
    counts = summarize(entries)
    code = exit_code_for(counts, cfg.max_changed)
    if code == 0 and not (invariants["manuscripts"]["unchanged"]
                          and invariants["engine_out"]["unchanged"]):
        code = 2
    return {
        "schema": SCHEMA,
        "tool": "rule_dryrun",
        "tool_version": TOOL_VERSION,
        "a": {"ref": cfg.a_ref, "sha": a.sha, "corpus_csv_sha": a.csv_sha},
        "b": {"ref": cfg.b_ref, "sha": b.sha, "corpus_csv_sha": b.csv_sha},
        "limit": cfg.limit,
        "jobs": str(cfg.jobs),
        "max_changed": cfg.max_changed,
        "counts": counts,
        "exit_code": code,
        "invariants": invariants,
        "articles": entries,
    }


# ---------------------------------------------------------------------------
# 渲染（确定性：无时间戳/耗时/临时路径）
# ---------------------------------------------------------------------------

def _b_desc(ref: str) -> str:
    return "worktree（当前工作树）" if ref == WORKTREE else ref


def verdict_text(report: dict) -> str:
    counts = report["counts"]
    inv = report["invariants"]
    if not (inv["manuscripts"]["unchanged"] and inv["engine_out"]["unchanged"]):
        broken = []
        if not inv["manuscripts"]["unchanged"]:
            broken.append("稿件库")
        if not inv["engine_out"]["unchanged"]:
            broken.append("Engine/out")
        return f"✗ 只读性不变量被破坏（{'、'.join(broken)} 有改动）→ 分析结论不可信（exit 2）"
    if counts["new_failure"]:
        return f"❌ 检出 new_failure {counts['new_failure']} 篇（exit 1）"
    if counts["changed"] > report["max_changed"]:
        return (f"❌ changed {counts['changed']} 篇 > --max-changed "
                f"{report['max_changed']}（exit 1）")
    return "✅ 无回归（exit 0）"


def render_report_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _md_cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_report_markdown(report: dict) -> str:
    a, b = report["a"], report["b"]
    counts = report["counts"]
    scope = f"limit={report['limit']}" if report["limit"] else "全库"
    lines = [
        "# rule_dryrun：A/B 全语料 dry-run",
        "",
        f"- A 版：`{a['ref']}` @ `{a['sha'][:12]}`",
        f"- B 版：{_b_desc(b['ref'])} @ `{b['sha'][:12]}`",
        f"- 范围：{scope}（实际比较 {counts['total']} 篇）；jobs={report['jobs']}；"
        f"max-changed={report['max_changed']}",
        f"- 分类：**unchanged {counts['unchanged']} / changed {counts['changed']} / "
        f"new_failure {counts['new_failure']} / fixed {counts['fixed']}**",
        f"- 结论：{verdict_text(report)}",
        f"- corpus.csv sha（证据）：A `{a['corpus_csv_sha']}` / B `{b['corpus_csv_sha']}`",
        "",
        "## 逐篇分类",
        "",
        "| 篇 | 期 | 通道 | A | B | 分类 | 变化 |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in report["articles"]:
        a_ok = "✅" if e["a_ok"] else ("❌" if e["a_ok"] is False else "—")
        b_ok = "✅" if e["b_ok"] else ("❌" if e["b_ok"] is False else "—")
        changes = "；".join(e["changes"]) if e["changes"] else "—"
        lines.append(
            f"| `{_md_cell(e['slug'])}` | {_md_cell(e['issue'])} | {_md_cell(e['channel'])} "
            f"| {a_ok} | {b_ok} | {e['status']} | {_md_cell(changes)} |"
        )

    file_rows = [e for e in report["articles"]
                 if e["files_changed"] or e["files_added"] or e["files_removed"]]
    if file_rows:
        lines += ["", "## 变化文件清单", ""]
        for e in file_rows:
            bits = []
            if e["files_changed"]:
                bits.append("变更：" + "、".join(f"`{x}`" for x in e["files_changed"]))
            if e["files_added"]:
                bits.append("新增：" + "、".join(f"`{x}`" for x in e["files_added"]))
            if e["files_removed"]:
                bits.append("缺失：" + "、".join(f"`{x}`" for x in e["files_removed"]))
            lines.append(f"- `{_md_cell(_slug_dir(e['issue'], e['slug']))}`：" + "；".join(bits))

    lines += ["", "## 只读性", ""]
    for label, key in (("稿件库", "manuscripts"), ("Engine/out", "engine_out")):
        inv = report["invariants"][key]
        if not inv["configured"]:
            lines.append(f"- {label}：未配置/不存在，跳过快照校验")
        elif inv["unchanged"]:
            lines.append(f"- {label}：未变（快照 {inv['files']} 个文件）")
        else:
            sample = "、".join(f"`{p}`" for p in inv["changed_paths"])
            lines.append(f"- {label}：**被改动（{len(inv['changed_paths'])}）** {sample}")
    lines.append("")
    return "\n".join(lines)


def render_report_csv(report: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["issue", "slug", "status", "a_ok", "b_ok", "changes",
                     "files_changed", "files_added", "files_removed"])
    for e in report["articles"]:
        writer.writerow([
            e["issue"], e["slug"], e["status"],
            "" if e["a_ok"] is None else int(bool(e["a_ok"])),
            "" if e["b_ok"] is None else int(bool(e["b_ok"])),
            "; ".join(e["changes"]),
            "; ".join(e["files_changed"]),
            "; ".join(e["files_added"]),
            "; ".join(e["files_removed"]),
        ])
    return buf.getvalue()


def render_console(report: dict, *, check: bool = False) -> str:
    """终端摘要：默认/`--check` 都给计数 + 非 unchanged 明细；`--check` 加预检抬头。"""
    a, b, counts = report["a"], report["b"], report["counts"]
    scope = f"limit={report['limit']}" if report["limit"] else "全库"
    lines = [
        f"rule_dryrun {TOOL_VERSION}（{'提交前预检' if check else 'A/B dry-run'}）",
        f"  A：{a['ref']} @ {a['sha'][:12]}",
        f"  B：{_b_desc(b['ref'])} @ {b['sha'][:12]}",
        f"  范围：{scope}（{counts['total']} 篇）；jobs={report['jobs']}；"
        f"max-changed={report['max_changed']}",
        f"  分类：unchanged {counts['unchanged']} | changed {counts['changed']} | "
        f"new_failure {counts['new_failure']} | fixed {counts['fixed']}",
    ]
    interesting = [e for e in report["articles"] if e["status"] != "unchanged"]
    if check and len(interesting) > 12:
        interesting = interesting[:12]
        lines.append("  （非 unchanged 明细仅列前 12 篇，全量见 --json/--out）")
    for e in interesting:
        detail = "；".join(e["changes"]) if e["changes"] else "—"
        lines.append(f"    - [{e['status']}] {e['issue']} / {e['slug']}：{_short(detail, 160)}")
    lines.append(f"  结论：{verdict_text(report)}")
    lines.append(f"  exit code：{report['exit_code']}")
    return "\n".join(lines)


def write_outputs(out_dir: str, report: dict) -> list[str]:
    """唯一写出口：JSON / MD / CSV 三件（确定性内容，可复跑逐字节比对）。"""
    os.makedirs(out_dir, exist_ok=True)
    payloads = {
        "rule-dryrun.json": render_report_json(report),
        "rule-dryrun.md": render_report_markdown(report),
        "rule-dryrun.csv": render_report_csv(report),
    }
    written = []
    for name, text in payloads.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

def resolve_jobs(value) -> str:
    """`--jobs`：auto / 正整数；其余 → ToolError（corpus.py 同样口径）。"""
    text = str(value).strip()
    if text.lower() == "auto":
        return "auto"
    try:
        n = int(text)
    except (TypeError, ValueError):
        raise ToolError(f"--jobs 只能是 auto / 1 / 正整数，收到：{value!r}")
    if n < 1:
        raise ToolError(f"--jobs 只能是 auto / 1 / 正整数，收到：{value!r}")
    return str(n)


def check_non_negative(name: str, value: int) -> None:
    if value < 0:
        raise ToolError(f"{name} 不能为负：{value}")


def resolve_python(preferred: str) -> str:
    """解释器：优先 `--python`/钉死的 3.14；不存在时降级为当前解释器并明确告警。"""
    for cand in (preferred, DEFAULT_PYTHON):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    print(f"⚠ rule_dryrun：{preferred} 不存在/不可执行，降级用当前解释器 {sys.executable}",
          file=sys.stderr)
    return sys.executable


# ---------------------------------------------------------------------------
# 自检（hermetic：tempfile 假语料 + 注入假流水线；不读真实库、不联网、不初始化 git）
# ---------------------------------------------------------------------------

def _mk_row(issue: str = "2026年第1期(1)", slug: str = "01 Alpha", ok: str = "1",
            raw: str = "0.900000", content: str = "0.800000", error: str = "",
            l2: str = "10", l3: str = "20", channel: str = "Word",
            lang: str = "zh") -> dict:
    return {
        "issue": issue, "slug": slug, "channel": channel, "lang": lang, "ok": ok,
        "raw_ratio": raw, "content_acc": content, "l2": l2, "l3": l3,
        "ref_chars": "1000", "src_tbl": "0", "out_tbl": "0", "src_img": "0",
        "out_img": "0", "src_shape": "0", "out_shape": "0", "error": error,
    }


def _write_corpus_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        writer.writerows(rows)


def _fixture_version(td: str, tag: str, rows: list[dict],
                     files: dict[str, dict[str, str]] | None = None) -> VersionResult:
    """造一版假产出（corpus.csv + 产物文件），再用真实解析函数读回。"""
    out_root = os.path.join(td, f"ver-{tag}", "out")
    _write_corpus_csv(os.path.join(out_root, "corpus.csv"), rows)
    for slug_dir, mapping in (files or {}).items():
        for rel, text in mapping.items():
            p = os.path.join(out_root, slug_dir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
    return VersionResult(
        label=tag.upper(), ref="HEAD", sha="a" * 40,
        rows=parse_corpus_csv(os.path.join(out_root, "corpus.csv")),
        files=scan_outputs(out_root),
        csv_sha=_sha256_file(os.path.join(out_root, "corpus.csv")),
    )


def _fake_cfg(td: str, *, limit: int = 0, max_changed: int = 0) -> Config:
    """假配置：假仓（含 Engine/out）+ 假稿件库，供注入式流水线使用。"""
    repo = os.path.join(td, "repo")
    os.makedirs(os.path.join(repo, "Engine", "out"), exist_ok=True)
    manuscripts = os.path.join(td, "manuscripts", "2026年第1期(1)", "01 Alpha")
    os.makedirs(manuscripts, exist_ok=True)
    with open(os.path.join(manuscripts, "source.txt"), "w", encoding="utf-8") as fh:
        fh.write("fake source\n")
    return Config(repo=repo, manuscripts=os.path.join(td, "manuscripts"),
                  limit=limit, max_changed=max_changed, work_root=td)


def _self_test() -> int:
    """hermetic 自检（≥10 用例）。只写 tempfile；不读真实稿件库、不联网、不初始化 git。"""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001 —— 自检要把失败原因说清楚而不是崩掉
            ok, msg = False, f"{type(e).__name__}: {e}"
        results.append((name, ok, msg))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}：{msg}")

    def fake_runner(versions):
        return lambda cfg, side: versions[side]

    print("[rule_dryrun self-test] hermetic 夹具（tempfile 假语料 + 注入假流水线）")
    with tempfile.TemporaryDirectory(prefix="rule-dryrun-selftest-") as td:
        rows = [
            _mk_row(slug="01 Alpha"),
            _mk_row(issue="2026年第2期(2)", slug="02 Beta", raw="0.500000"),
            _mk_row(issue="2026年第2期(2)", slug="03 Gamma", ok="0",
                    raw="0.000000", content="0.000000", error="原稿缺失"),
        ]
        files = {
            "2026_01_Alpha": {"Alpha.tex": "A", "Alpha_参考文献核对.txt": "R"},
            "2026_02_Beta": {"Beta.tex": "B"},
            "2026_02_Gamma": {},
        }

        # 1) A/A 恒等：全 unchanged、exit 0、只读性不变量成立
        def t_identity():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "aa1", rows, files)
            b = _fixture_version(td, "aa2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            c = rep["counts"]
            if rep["exit_code"] != 0:
                return False, f"exit {rep['exit_code']}"
            if (c["unchanged"], c["changed"], c["new_failure"], c["fixed"]) != (3, 0, 0, 0):
                return False, f"分类错误 {c}"
            if not rep["invariants"]["manuscripts"]["unchanged"]:
                return False, "稿件库快照被判改动"
            if not rep["invariants"]["engine_out"]["unchanged"]:
                return False, "Engine/out 快照被判改动"
            return True, "3/3 unchanged；exit 0；两处只读性快照未变"

        # 2) changed：打分行指标变化
        def t_changed_metric():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "cm1", rows, files)
            rows2 = [dict(r) for r in rows]
            rows2[1]["raw_ratio"] = "0.400000"
            b = _fixture_version(td, "cm2", rows2, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            e = {x["slug"]: x for x in rep["articles"]}["02 Beta"]
            if e["status"] != "changed" or rep["counts"]["changed"] != 1:
                return False, f"status={e['status']} / counts={rep['counts']}"
            if rep["exit_code"] != 1:
                return False, f"exit {rep['exit_code']}"
            if not any("raw_ratio" in ch for ch in e["changes"]):
                return False, f"变化项未提 raw_ratio：{e['changes']}"
            return True, "raw_ratio 0.9→0.5 → changed、exit 1"

        # 3) changed：产出文件 sha 变化
        def t_changed_file():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "cf1", rows, files)
            files2 = {k: dict(v) for k, v in files.items()}
            files2["2026_02_Beta"] = {"Beta.tex": "B-changed"}
            b = _fixture_version(td, "cf2", rows, files2)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            e = {x["slug"]: x for x in rep["articles"]}["02 Beta"]
            if e["status"] != "changed" or e["files_changed"] != ["Beta.tex"]:
                return False, f"status={e['status']} files_changed={e['files_changed']}"
            return True, "Beta.tex sha 变化 → changed（文件清单准确）"

        # 4) 产出文件新增/缺失
        def t_file_added_removed():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "fa1", rows, files)
            files2 = {k: dict(v) for k, v in files.items()}
            files2["2026_01_Alpha"] = {"Alpha.tex": "A", "fig1.png": "new"}
            files2["2026_02_Gamma"] = {}
            b = _fixture_version(td, "fa2", rows, files2)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            e = {x["slug"]: x for x in rep["articles"]}["01 Alpha"]
            if e["files_added"] != ["fig1.png"] or e["files_removed"] != ["Alpha_参考文献核对.txt"]:
                return False, f"added={e['files_added']} removed={e['files_removed']}"
            return True, "新增 fig1.png / 缺失 参考文献核对.txt 均被列出"

        # 5) new_failure：A 成功、B 失败
        def t_new_failure():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "nf1", rows, files)
            rows2 = [dict(r) for r in rows]
            rows2[0].update(ok="0", error="lxml 解析失败")
            b = _fixture_version(td, "nf2", rows2, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            e = {x["slug"]: x for x in rep["articles"]}["01 Alpha"]
            if e["status"] != "new_failure" or rep["counts"]["new_failure"] != 1:
                return False, f"status={e['status']} counts={rep['counts']}"
            if rep["exit_code"] != 1:
                return False, f"exit {rep['exit_code']}"
            return True, "A ok → B fail → new_failure、exit 1"

        # 6) fixed：A 失败、B 成功
        def t_fixed():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "fx1", rows, files)
            rows2 = [dict(r) for r in rows]
            rows2[2].update(ok="1", error="", raw_ratio="0.900000", content_acc="0.800000")
            b = _fixture_version(td, "fx2", rows2, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            e = {x["slug"]: x for x in rep["articles"]}["03 Gamma"]
            if e["status"] != "fixed" or rep["exit_code"] != 0:
                return False, f"status={e['status']} exit={rep['exit_code']}"
            return True, "A fail → B ok → fixed（不计入回归，exit 0）"

        # 7) 两版都失败：错误一致 = unchanged；错误不同 = changed
        def t_both_failed():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "bf1", rows, files)
            rows_same = [dict(r) for r in rows]
            b_same = _fixture_version(td, "bf2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b_same}))
            if {x["slug"]: x for x in rep["articles"]}["03 Gamma"]["status"] != "unchanged":
                return False, "同一失败的篇未判 unchanged"
            rows_diff = [dict(r) for r in rows_same]
            rows_diff[2]["error"] = "另一种失败"
            b_diff = _fixture_version(td, "bf3", rows_diff, files)
            rep2 = run_analysis(cfg, runner=fake_runner({"a": a, "b": b_diff}))
            e = {x["slug"]: x for x in rep2["articles"]}["03 Gamma"]
            if e["status"] != "changed":
                return False, f"失败模式变化未判 changed（{e['status']}）"
            return True, "同错 unchanged / 异错 changed"

        # 8) 退出码三态（0/1/2 的 0 与 1 分支；2 由坏输入用例覆盖）
        def t_exit_codes():
            clean = {"total": 3, "unchanged": 3, "changed": 0, "new_failure": 0, "fixed": 0}
            reg = dict(clean, unchanged=0, changed=3)
            nf = dict(clean, unchanged=2, new_failure=1)
            if exit_code_for(clean, 0) != 0:
                return False, "clean 非 0"
            if exit_code_for(nf, 99) != 1:
                return False, "new_failure 非 1"
            if exit_code_for(reg, 2) != 1:
                return False, "changed 超阈值非 1"
            if exit_code_for(reg, 3) != 0:
                return False, "changed 未超阈值非 0"
            return True, "0 / new_failure→1 / changed 超阈值→1 / 未超→0"

        # 9) --json：可解析、字段稳定、两次渲染一致
        def t_json():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "j1", rows, files)
            b = _fixture_version(td, "j2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            t1, t2 = render_report_json(rep), render_report_json(rep)
            if t1 != t2:
                return False, "两次渲染不同"
            doc = json.loads(t1)
            for key in ("schema", "tool", "a", "b", "counts", "exit_code",
                        "invariants", "articles"):
                if key not in doc:
                    return False, f"缺字段 {key}"
            return True, "JSON 可解析、字段齐全、两次渲染逐字节相同"

        # 10) --limit：只比较前 N 篇
        def t_limit():
            cfg = _fake_cfg(td, limit=2)
            a = _fixture_version(td, "lm1", rows, files)
            b = _fixture_version(td, "lm2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            if rep["counts"]["total"] != 2:
                return False, f"total={rep['counts']['total']}"
            if [x["slug"] for x in rep["articles"]] != ["01 Alpha", "02 Beta"]:
                return False, "limit 结果顺序/范围不对"
            return True, "limit=2 → 只比前 2 篇（顺序固定）"

        # 11) 排序确定性：B 顺序打乱，汇总顺序仍按 A 固定
        def t_ordering():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "or1", rows, files)
            b = _fixture_version(td, "or2", list(reversed(rows)), files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            order = [x["slug"] for x in rep["articles"]]
            if order != ["01 Alpha", "02 Beta", "03 Gamma"]:
                return False, f"顺序 {order}"
            if rep["counts"]["unchanged"] != 3:
                return False, f"乱序导致误判 {rep['counts']}"
            return True, "B 行乱序不影响分类与顺序（按 A 版顺序汇总）"

        # 12) 输出三件 + 复跑逐字节一致
        def t_outputs():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "o1", rows, files)
            b = _fixture_version(td, "o2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            d1 = os.path.join(td, "out1")
            d2 = os.path.join(td, "out2")
            p1 = write_outputs(d1, rep)
            p2 = write_outputs(d2, rep)
            if len(p1) != 3:
                return False, f"写文件数 {len(p1)}"
            for f in ("rule-dryrun.json", "rule-dryrun.md", "rule-dryrun.csv"):
                if not os.path.isfile(os.path.join(d1, f)):
                    return False, f"缺 {f}"
            for x, y in zip(sorted(p1), sorted(p2)):
                if _sha256_file(x) != _sha256_file(y):
                    return False, f"{os.path.basename(x)} 两次不同"
            with open(os.path.join(d1, "rule-dryrun.json"), encoding="utf-8") as fh:
                json.load(fh)
            with open(os.path.join(d1, "rule-dryrun.csv"), encoding="utf-8", newline="") as fh:
                got = list(csv.DictReader(fh))
            if [r["slug"] for r in got] != ["01 Alpha", "02 Beta", "03 Gamma"]:
                return False, "CSV 行顺序/内容不对"
            return True, "JSON/MD/CSV 三件写出；复跑逐字节一致；CSV 可解析"

        # 13) --check 摘要含预检抬头与计数
        def t_console():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "c1", rows, files)
            b = _fixture_version(td, "c2", rows, files)
            rep = run_analysis(cfg, runner=fake_runner({"a": a, "b": b}))
            text = render_console(rep, check=True)
            if "预检" not in text or "unchanged 3" not in text or "exit code：0" not in text:
                return False, text[:120]
            return True, "预检抬头 + 分类计数 + exit code 均在摘要中"

        # 14) 只读性破坏（稿件库被写）→ 不变量失败、exit 2
        def t_guard_manuscripts():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "gm1", rows, files)
            b = _fixture_version(td, "gm2", rows, files)

            def bad_runner(c, side):
                target = os.path.join(c.manuscripts, "2026年第1期(1)", "01 Alpha", "extra.txt")
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("x")
                return {"a": a, "b": b}[side]

            rep = run_analysis(cfg, runner=bad_runner)
            if rep["invariants"]["manuscripts"]["unchanged"]:
                return False, "稿件库写入未被检出"
            if rep["exit_code"] != 2:
                return False, f"exit {rep['exit_code']}（应 2）"
            return True, "稿件库被写 → 不变量 False、exit 2"

        # 15) 只读性破坏（Engine/out 被写）→ 不变量失败、exit 2
        def t_guard_engine_out():
            cfg = _fake_cfg(td)
            a = _fixture_version(td, "ge1", rows, files)
            b = _fixture_version(td, "ge2", rows, files)
            out_file = os.path.join(cfg.repo, "Engine", "out", "leak.txt")

            def bad_runner(c, side):
                with open(out_file, "w", encoding="utf-8") as fh:
                    fh.write("leak")
                return {"a": a, "b": b}[side]

            rep = run_analysis(cfg, runner=bad_runner)
            if rep["invariants"]["engine_out"]["unchanged"]:
                return False, "Engine/out 写入未被检出"
            if rep["exit_code"] != 2:
                return False, f"exit {rep['exit_code']}（应 2）"
            return True, "Engine/out 被写 → 不变量 False、exit 2"

        # 16) 坏输入：corpus.csv 缺列 / 空 / 参数非法 → ToolError（main 折成 exit 2）
        def t_bad_input():
            bad = os.path.join(td, "bad", "corpus.csv")
            os.makedirs(os.path.dirname(bad), exist_ok=True)
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("issue,slug\nx,y\n")
            try:
                parse_corpus_csv(bad)
                return False, "缺列未报错"
            except ToolError:
                pass
            empty = os.path.join(td, "bad2", "corpus.csv")
            os.makedirs(os.path.dirname(empty), exist_ok=True)
            open(empty, "w", encoding="utf-8").close()
            try:
                parse_corpus_csv(empty)
                return False, "空文件未报错"
            except ToolError:
                pass
            for fn, val in ((resolve_jobs, "bogus"), (resolve_jobs, "0")):
                try:
                    fn(val)
                    return False, f"{fn.__name__}({val!r}) 未报错"
                except ToolError:
                    pass
            for name, val in (("--limit", -1), ("--max-changed", -2)):
                try:
                    check_non_negative(name, val)
                    return False, f"{name} 负值未报错"
                except ToolError:
                    pass
            return True, "缺列/空 CSV/非法 jobs/负参数 → ToolError（exit 2 口径）"

        # 17) 产出扫描跳过 *.bak（覆盖备份带时间戳，不能参与比较）
        def t_skip_bak():
            root = os.path.join(td, "baktree")
            d = os.path.join(root, "2026_01_Alpha")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "Alpha.tex"), "w", encoding="utf-8") as fh:
                fh.write("content")
            with open(os.path.join(d, "Alpha.tex.20261022-120000-1.bak"),
                      "w", encoding="utf-8") as fh:
                fh.write("old")
            files = scan_outputs(root)
            if list(files["2026_01_Alpha"]) != ["Alpha.tex"]:
                return False, f"未跳过 .bak：{files}"
            return True, "扫产出跳过 *.bak"

        check("identity_aa", t_identity)
        check("changed_metric", t_changed_metric)
        check("changed_output_file", t_changed_file)
        check("file_added_removed", t_file_added_removed)
        check("new_failure", t_new_failure)
        check("fixed", t_fixed)
        check("both_failed", t_both_failed)
        check("exit_codes", t_exit_codes)
        check("json_render", t_json)
        check("limit", t_limit)
        check("ordering", t_ordering)
        check("outputs_deterministic", t_outputs)
        check("console_check", t_console)
        check("guard_manuscripts", t_guard_manuscripts)
        check("guard_engine_out", t_guard_engine_out)
        check("bad_input", t_bad_input)
        check("skip_bak", t_skip_bak)

    ok = sum(1 for _, passed, _ in results if passed)
    print(f"[rule_dryrun self-test] {ok}/{len(results)} PASS"
          + ("" if ok == len(results) else " ← 有失败"))
    return 0 if ok == len(results) else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="规则改动全语料 dry-run + 两版规则 A/B 回归（只读；不写稿件库/Engine/out）")
    ap.add_argument("--a", default="HEAD", metavar="REF",
                    help="基线版 git ref（默认 HEAD）")
    ap.add_argument("--b", default=WORKTREE, metavar="REF|worktree",
                    help="候选版：worktree（默认，当前工作树，含未提交改动）或 git ref")
    ap.add_argument("--repo", default=None, metavar="DIR",
                    help="被测仓目录（默认：当前目录所在 git 工作树；退路=本工具所在仓）")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="只跑前 N 篇（0=全部；调试/验收用小值）")
    ap.add_argument("--jobs", default="auto", metavar="auto|N",
                    help="传给 corpus.py 的进程数（默认 auto = min(8, CPU)；1=串行）")
    ap.add_argument("--max-changed", type=int, default=0, metavar="N",
                    help="允许的 changed 篇数上限（默认 0；超过则 exit 1）")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="输出目录：写 rule-dryrun.json / .md / .csv（唯一写出口）")
    ap.add_argument("--json", action="store_true", help="stdout 输出机读 JSON")
    ap.add_argument("--check", action="store_true", help="人类可读摘要（规则改动提交前预检）")
    ap.add_argument("--python", default=DEFAULT_PYTHON, metavar="PATH",
                    help=f"跑 corpus 的解释器（默认钉 {DEFAULT_PYTHON}）")
    ap.add_argument("--self-test", action="store_true", help="hermetic 自检（≥10 用例）")
    ap.add_argument("--version", action="version", version=f"rule_dryrun {TOOL_VERSION}")
    return ap


def _main(args: argparse.Namespace) -> int:
    check_non_negative("--limit", args.limit)
    check_non_negative("--max-changed", args.max_changed)
    jobs = resolve_jobs(args.jobs)
    if args.json and args.check:
        raise ToolError("--json 与 --check 不能同时使用（机读与人类摘要二选一）")

    repo = detect_repo(args.repo)
    validate_repo(repo)
    engine_dir = os.path.join(repo, "Engine")
    paths_mod = load_paths_module(engine_dir)
    manuscripts = paths_mod.manuscripts_root()
    if manuscripts is None:
        print(f"≡ rule_dryrun：{paths_mod.describe()}")
        return 0

    python = resolve_python(args.python)
    a_sha = _resolve_ref(repo, args.a)
    if args.b != WORKTREE:
        b_sha = _resolve_ref(repo, args.b)
    else:
        b_sha = _resolve_ref(repo, "HEAD")
    if not os.path.isfile(os.path.join(engine_dir, "corpus.py")):
        raise ToolError(f"{repo} 缺 Engine/corpus.py")

    cfg = Config(
        repo=repo, a_ref=args.a, b_ref=args.b, a_sha=a_sha, b_sha=b_sha,
        limit=args.limit, jobs=jobs, max_changed=args.max_changed,
        out_dir=args.out, python=python, manuscripts=manuscripts,
    )
    work_root = tempfile.mkdtemp(prefix="rule-dryrun-")
    cfg.work_root = work_root
    failed = True
    try:
        report = run_analysis(cfg)
        failed = False
    finally:
        if failed:
            print(f"⚠ rule_dryrun：临时目录保留供排查：{work_root}", file=sys.stderr)
        else:
            shutil.rmtree(work_root, ignore_errors=True)

    if args.out:
        written = write_outputs(os.path.abspath(args.out), report)
        print(f"◆ 已写出：{'、'.join(os.path.basename(p) for p in written)}"
              f" → {os.path.abspath(args.out)}", file=sys.stderr)
    if args.json:
        sys.stdout.write(render_report_json(report))
    else:
        print(render_console(report, check=args.check))
    return int(report["exit_code"])


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        return _main(args)
    except ToolError as e:
        print(f"✗ rule_dryrun：{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("✗ rule_dryrun：被中断", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 —— 内部错误也不静默：带栈退出 2
        traceback.print_exc()
        print(f"✗ rule_dryrun：内部错误 {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
