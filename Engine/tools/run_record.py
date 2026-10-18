#!/usr/bin/env python3.14
"""重活「指纹 + 日志」落盘工具（O13，第四十六批）。

WHY：批 45 的 O5=0（同一批 build 了两次）在审计时无法从日志复核——重活的起止时间、
命令、引擎代码指纹、产物指纹、退出码都没有固定落盘，只能靠 Lead 口述。本工具把每次重活
（build / gen_docs / full / corpus / audit …）的 manifest 写成一个 JSON，放在
`/tmp/audit_lead/runs/`，供审计与 O12 缓存复用。

设计约束（卡面）：
* **无副作用**：本脚本只读仓库（git 只读命令 + 读 Engine/**/*.py）并只写 `/tmp`（或 --out-dir）；
  不改任何仓库文件、不参与任何判定。
* **失败也要留痕**：wrap 模式下被包裹命令非 0 退出时，manifest 仍写出并记 exit_code。
* **不覆盖**：文件名 = `<本地时间戳>-<label>.json`（含微秒与 pid），同一 label 连跑两次得到两个文件；
  原子写（tmp + os.replace），并发安全。
* **绝不把调用方搞挂**：写 manifest 的任何异常都只告警（stderr），不影响调用方退出码。

用法
----
    # ① wrap 模式：包住一条重活命令（tee 日志 + 落盘 manifest；退出码 = 命令退出码）
    /opt/homebrew/bin/python3.14 Engine/tools/run_record.py --label full \
        --tee /tmp/audit_lead/full.log --meta batch=46 -- bash Engine/tools/run_engine_tests.sh full

    # ② record-only 模式：脚本末尾补记（build.sh 用；构建已在前面完成）
    /opt/homebrew/bin/python3.14 Engine/tools/run_record.py --record-only --label build \
        --started-at 2026-10-19T02:20:00+0800 --command "bash build.sh" --exit-code 0 \
        --artifact "dist/编辑部工具箱.app/Contents/MacOS/Toolbox" --meta app_version=1.2.1

    # ③ 作为库调用（gen_docs.py）
    from run_record import write_manifest
    write_manifest(label="gen_docs-write", command="gen_docs.py", started_at=t0,
                   duration_s=..., artifact=ENGINE / "README.md", exit_code=rc,
                   meta={"mode": "write"})

manifest 字段（卡面要求的 9 个，另加 label/meta）：
    started_at / finished_at / duration_s / command / cwd / git_head /
    engine_py_sha256 / out_artifact_sha256 / exit_code
扩展说明：
    * `engine_py_sha256` = 对 `Engine/**/*.py`（排除 `vendor/`、`out/`、`__pycache__`）按相对路径
      排序后「路径 + NUL + 内容 + NUL」聚合的 sha256 —— 引擎代码指纹（同代码 = 同指纹）。
    * `out_artifact_sha256` = 产物文件（或目录树）的 sha256；不存在时 null。
    * `meta` = 调用方补的自由键值（build.sh 记 app_version / app_mtime / app_py_files / cmp_result；
      gen_docs 记 mode / check / tests_ran / no_test_runs / readme_changed / auto_ok）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]        # …/Engine
REPO = ENGINE.parent                                # 公开仓根（与 gen_docs 的口径一致）
DEFAULT_OUT_DIR = Path("/tmp/audit_lead/runs")

REQUIRED_FIELDS = (
    "started_at", "finished_at", "duration_s", "command", "cwd",
    "git_head", "engine_py_sha256", "out_artifact_sha256", "exit_code",
)
_SKIP_PARTS = {"vendor", "out", "__pycache__", ".git", ".build", ".mc", "node_modules"}


# ── 指纹 ──────────────────────────────────────────────────────────────────
def _git(args: list[str], cwd: Path = REPO) -> str:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout.strip()
    except OSError:
        return ""


def git_head(repo: Path = REPO) -> str:
    """当前 HEAD（短 sha 之外的完整 sha；不可用时返回空串，不抛）。"""
    return _git(["rev-parse", "HEAD"], repo) or ""


def git_dirty_count(repo: Path = REPO) -> int:
    return len([x for x in _git(["status", "--porcelain"], repo).split("\n") if x.strip()])


def engine_py_sha256(engine: Path = ENGINE) -> str:
    """引擎 Python 代码指纹：`Engine/**/*.py`（排除 vendor/out/__pycache__）聚合 sha256。"""
    h = hashlib.sha256()
    files = []
    for p in engine.rglob("*.py"):
        rel = p.relative_to(engine)
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        files.append((rel.as_posix(), p))
    for rel, p in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def out_artifact_sha256(artifact: str | os.PathLike | None) -> str | None:
    """产物指纹：文件算内容 sha256；目录算「相对路径+内容」聚合 sha256；不存在 → None。"""
    if not artifact:
        return None
    p = Path(artifact)
    if not p.exists():
        return None
    if p.is_file():
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            return None
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(f.relative_to(p).as_posix().encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


# ── 时间 ──────────────────────────────────────────────────────────────────
def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def parse_started_at(value: str | None) -> float | None:
    """接受 ISO（含 +0800/+08:00）或 epoch 秒；解析失败返回 None。"""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        # Python 3.11+ 的 fromisoformat 能处理 ±HH:MM 与 ±HHMM
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError:
        return None


# ── manifest 写入 ─────────────────────────────────────────────────────────
def _safe_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(label)).strip("_") or "run"


def _manifest_path(out_dir: Path, label: str) -> Path:
    """`<timestamp>-<label>.json`；同一微秒/pid 撞名时加序号，**绝不覆盖已有文件**。"""
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    usec = datetime.now().microsecond
    stem = f"{ts}-{usec:06d}-{os.getpid()}-{_safe_label(label)}"
    cand = out_dir / f"{stem}.json"
    n = 1
    while cand.exists():
        n += 1
        cand = out_dir / f"{stem}-{n}.json"
    return cand


def write_manifest(*, label: str, command: str, started_at: float, finished_at: float | None = None,
                   duration_s: float | None = None, out_dir: str | os.PathLike | None = None,
                   artifact: str | os.PathLike | None = None, exit_code: int = 0,
                   meta: dict | None = None, engine_sha: str | None = None,
                   purpose: str = "unspecified",
                   repo: Path = REPO, engine: Path = ENGINE) -> str | None:
    """写一份 manifest，返回路径（失败返回 None —— **绝不抛异常**）。

    约定：调用方传入 `started_at`（epoch 秒）；`finished_at` 缺省 = 现在；`duration_s` 缺省
    = finished_at - started_at。本函数只在 `out_dir`（默认 /tmp/audit_lead/runs）写文件。

    `engine_sha`：**建议调用方在重活开始时算好传入**（`engine_py_sha256()`）—— 并发写者
    窗口里「结束态」的指纹可能已经不是重活跑的那份代码；收口冻结时两者相同。

    `purpose`（O14，第四十七批）：这次重活的**目的**，用于机读区分「真跑」与「验证跑」，
    例如 `real-build` / `verify-build` / `real-corpus` / `verify-corpus` / `gen-docs` / `full`。
    没有该字段时 O5 审计会把验证构建也算进「真实重活次数」（批46 实测 6 条 build vs 真实 3 次）。
    """
    try:
        fin = time.time() if finished_at is None else finished_at
        dur = (fin - started_at) if duration_s is None else duration_s
        target_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        m = dict(meta or {})
        m.setdefault("git_dirty_count", git_dirty_count(repo))
        payload = {
            "label": label,
            "purpose": purpose,
            "started_at": _iso(started_at),
            "finished_at": _iso(fin),
            "duration_s": round(float(dur), 3),
            "command": command,
            "cwd": str(Path.cwd()),
            "git_head": git_head(repo),
            "engine_py_sha256": engine_sha or engine_py_sha256(engine),
            "out_artifact_sha256": out_artifact_sha256(artifact),
            "exit_code": int(exit_code),
            "meta": m,
        }
        missing = [k for k in REQUIRED_FIELDS if k not in payload]
        if missing:  # 理论上不可达；真出现也宁可写出来 + 告警，不要吞掉一次重活的证据
            print(f"⚠ run_record: manifest 缺字段 {missing}", file=sys.stderr)
        path = _manifest_path(target_dir, label)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)          # 原子替换（目标名已保证不存在）
        return str(path)
    except Exception as e:  # noqa: BLE001 —— 证据工具绝不能把调用方搞挂
        print(f"⚠ run_record: 写 manifest 失败（忽略，不影响调用方）：{type(e).__name__}: {e}",
              file=sys.stderr)
        return None


# ── 命令行 ────────────────────────────────────────────────────────────────
def _parse_meta(items: list[str]) -> dict:
    meta = {}
    for item in items or []:
        if "=" in item:
            k, v = item.split("=", 1)
            meta[k.strip()] = v.strip()
        else:
            meta[item.strip()] = True
    return meta


def _run_wrapped(cmd: list[str], tee: str | None) -> int:
    """跑被包裹命令；tee 非空时把 stdout+stderr 同步写进日志文件。返回命令退出码。"""
    if not tee:
        try:
            return subprocess.run(cmd).returncode
        except FileNotFoundError as e:
            print(f"✗ run_record: 命令不存在：{e}", file=sys.stderr)
            return 127
    tpath = Path(tee)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    with tpath.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            fh.write(line)
        return proc.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="重活指纹/日志落盘（O13）：写 run-manifest.json 到 /tmp/audit_lead/runs/")
    ap.add_argument("--label", required=True, help="manifest 标签（如 build/gen_docs-write/full/corpus）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 /tmp/audit_lead/runs）")
    ap.add_argument("--artifact", default=None, help="产物路径（文件或目录）；记录其 sha256")
    ap.add_argument("--tee", default=None, help="wrap 模式：同时把命令输出写入该日志文件")
    ap.add_argument("--meta", action="append", default=[], metavar="K=V", help="附加键值（可重复）")
    ap.add_argument("--record-only", action="store_true",
                    help="不跑命令，只按给定事实写 manifest（build.sh 末尾用）")
    ap.add_argument("--started-at", default=None,
                    help="record-only 必填：开始时间（ISO，如 2026-10-19T02:20:00+0800，或 epoch 秒）")
    ap.add_argument("--command", default=None, help="record-only：记录的命令字符串")
    ap.add_argument("--exit-code", type=int, default=None, help="record-only：退出码（默认 0）")
    ap.add_argument("--engine-sha", default=None,
                    help="重活**开始**时算好的 Engine py 指纹（并发写者下应尽量传；缺省=当前态现算）")
    ap.add_argument("--purpose", default="unspecified",
                    help="这次重活的目的（O14，机读区分真跑/验证跑）：real-build / verify-build / "
                         "real-corpus / verify-corpus / gen-docs / full …（默认 unspecified）")
    ap.add_argument("--quiet", action="store_true", help="不打印 manifest 路径")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="（wrap 模式）-- 之后的命令")
    a = ap.parse_args(argv)
    meta = _parse_meta(a.meta)

    if a.record_only:
        started = parse_started_at(a.started_at)
        if started is None:
            print("✗ --record-only 需要有效的 --started-at（ISO 或 epoch 秒）", file=sys.stderr)
            return 2
        path = write_manifest(label=a.label, command=a.command or "", started_at=started,
                              out_dir=a.out_dir, artifact=a.artifact,
                              exit_code=(0 if a.exit_code is None else a.exit_code), meta=meta,
                              engine_sha=a.engine_sha or None, purpose=a.purpose)
        if path and not a.quiet:
            print(f"◆ manifest: {path}", file=sys.stderr)
        return 0

    cmd = list(a.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("wrap 模式需要 `-- <命令> [参数…]`（或改用 --record-only）")
    started = time.time()
    rc = _run_wrapped(cmd, a.tee)
    path = write_manifest(label=a.label, command=" ".join(shlex.quote(x) for x in cmd),
                          started_at=started, out_dir=a.out_dir, artifact=a.artifact,
                          exit_code=rc, meta=meta, purpose=a.purpose)
    if path and not a.quiet:
        print(f"◆ manifest: {path}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
