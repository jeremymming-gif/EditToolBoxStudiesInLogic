#!/usr/bin/env python3
"""批 112 S2 · GUI-03 基准：一次启动，连续切主题，量「换肤不重建」热路径。

判据（rc=0 才算过）：
- `shell_built` 只出现 1 次（启动那次；热路径禁止 rebuild_shell）；
- `theme_retint_fallback` = 0（没有退回重建）；
- `theme_changed.duration_ms` 中位 ≤ 40ms、P95 ≤ 80ms（Python 侧，含 page.update 发送）。

输出：证据目录下 bench.json / markers.jsonl / run.log，stdout 摘要 + `EXIT=` 行。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
# 默认值按环境推导（不写死本机路径）；可用 --python/--out 覆盖。
DEFAULT_PY = Path(os.environ.get("SLCN_BENCH_PYTHON", sys.executable))
DEFAULT_OUT = Path(os.environ.get("SLCN_BENCH_OUT", str(Path.cwd() / "bench-theme-out")))


def p95(values) -> float:
    """最近秩 P95（无插值）：单次 UI 延迟只看尾部实测值，不做平滑。"""
    data = sorted(float(value) for value in values)
    return data[max(0, math.ceil(0.95 * len(data)) - 1)] if data else 0.0


def summarize(durations, *, shell_built: int, fallbacks: int,
              median_max: float = 40.0, p95_max: float = 80.0) -> dict:
    """纯函数：把 marker 数字变成 PASS/FAIL（测试直接 import 这一支）。"""
    data = sorted(float(value) for value in durations)
    median = round(statistics.median(data), 1) if data else None
    tail = round(p95(data), 1)
    checks = {
        "samples": len(data) >= 2,
        "shell_built_once": shell_built == 1,
        "no_fallback": fallbacks == 0,
        "median": median is not None and median <= median_max,
        "p95": bool(data) and tail <= p95_max,
    }
    return {"ok": all(checks.values()), "n": len(data), "median_ms": median,
            "p95_ms": tail, "max_ms": round(max(data), 1) if data else None,
            "shell_built": shell_built, "fallbacks": fallbacks,
            "thresholds": {"median_ms": median_max, "p95_ms": p95_max},
            "checks": checks}


def read_markers(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def run_bench(args) -> dict:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="b112-bench-"))
    settings = work / "settings.json"
    settings.write_text(json.dumps({"env_check_done": True, "theme": "light"}),
                        encoding="utf-8")
    marker = work / "markers.jsonl"
    env = dict(os.environ, P2_SETTINGS=str(settings), P2_MARKER=str(marker),
               P2_THEME="light", P2_WINSIZE="1000x700",
               P2_THEME_CYCLES=str(args.cycles),
               P2_AUTO_CLOSE_MS=str(max(9000, args.cycles * 1200 + 5000)))
    log_path = out / "run.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen([str(args.python), "main.py"], cwd=str(APP), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + args.timeout
    done = False
    while time.time() < deadline:
        if any(row.get("event") == "theme_cycles_done" for row in read_markers(marker)):
            done = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    try:
        proc.wait(timeout=max(5.0, deadline - time.time() + 5.0))
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=8)
    rows = read_markers(marker)
    if marker.exists():
        (out / "markers.jsonl").write_text(marker.read_text(encoding="utf-8"),
                                           encoding="utf-8")
    durations = [row["duration_ms"] for row in rows
                 if row.get("event") == "theme_changed" and "duration_ms" in row]
    report = summarize(durations,
                       shell_built=sum(1 for row in rows if row.get("event") == "shell_built"),
                       fallbacks=sum(1 for row in rows
                                    if row.get("event") == "theme_retint_fallback"),
                       median_max=args.median_max, p95_max=args.p95_max)
    report.update({
        "done": done, "cycles": args.cycles, "rc": proc.returncode,
        "modes": sorted({str(row.get("mode")) for row in rows
                         if row.get("event") == "theme_changed"}),
        "markers": str(out / "markers.jsonl"), "log": str(log_path),
    })
    if not done:
        report["ok"] = False
        report["error"] = "theme_cycles_done 未出现（应用未起 / 钩子未生效 / 超时）"
    elif proc.returncode not in (0, -15):
        report["ok"] = False
        report["error"] = f"app rc={proc.returncode}（见 {log_path}）"
    (out / "bench.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument("--median-max", type=float, default=40.0)
    parser.add_argument("--p95-max", type=float, default=80.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--python", default=str(DEFAULT_PY))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    report = run_bench(args)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"BENCH n={report['n']} median={report['median_ms']}ms p95={report['p95_ms']}ms "
          f"max={report['max_ms']}ms shell_built={report['shell_built']} "
          f"fallbacks={report['fallbacks']} -> {'PASS' if report['ok'] else 'FAIL'}")
    print(f"EXIT={0 if report['ok'] else 1}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
