#!/usr/bin/env python3
r"""SUPPLEMENT-SIGTERM-01：promote_stage() 覆盖交付的信号/中断安全判据。

覆盖（全部 hermetic tmpdir；S1/S2 用子进程注入真实 SIGTERM/SIGINT/SIGKILL）：
  · S1 SIGTERM/SIGINT 落在 rename(final→backup) 与 rename(tmp→final) 之间：
    final 必为**完整旧交付**或**完整新交付**，进程退出，无 journal/tmp 残留；
  · S2 SIGKILL（不可捕获）后：入口 promote_stage（stage 可不存在）按 journal
    把 final 恢复成**完整旧交付**；只动 journal 记录路径，无关 `.coop-bak-*` 不动；
    且下一次正常 promote 仍返回 backup=旧交付；
  · S3 正常覆盖：返回 `{final,backup,excluded}` 不变、换名期间 journal 在位、
    结束后无 journal/tmp 残留；
  · S4 第二次 rename 抛错：旧交付原样换回、tmp/journal 清理、异常照抛；
  · S5 首次交付成功/失败行为不变；失败只清 tmp、不写 journal。

用法：
    cd Engine && PYTHONPATH=. /opt/homebrew/bin/python3.14 tests/test_pkg_p02_promote_sigterm.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from slcn import coop  # noqa: E402

FAILS: list[str] = []
PASS = 0
CHILD_SCRIPT = ""


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


# ── 小工具 ───────────────────────────────────────────────────────────────

def _write(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)


def _entries(root: str) -> list[list]:
    """目录树快照：[[相对路径, sha256, 字节数], ...]（排序后比较即「一个字节不差」）。"""
    out: list[list] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                data = fh.read()
            out.append([rel, hashlib.sha256(data).hexdigest(), len(data)])
    return out


def _make_old_final(final: str) -> None:
    _write(os.path.join(final, "old.txt"), "OLD-1")
    _write(os.path.join(final, "nested", "old2.bin"), "OLD-2")


def _make_stage(stage: str, tag: str = "NEW") -> None:
    _write(os.path.join(stage, "new.txt"), f"{tag}-1")
    _write(os.path.join(stage, "sub", "new2.bin"), f"{tag}-2")


def _journal_path(root: str, final: str) -> str:
    return os.path.join(root, f".{os.path.basename(final)}.coop-promote-journal.json")


def _read_journal(root: str, final: str):
    try:
        with open(_journal_path(root, final), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _residue(root: str) -> dict:
    return {
        "journal": sorted(glob.glob(os.path.join(root, ".*.coop-promote-journal.json"))),
        "tmp": sorted(glob.glob(os.path.join(root, ".*.coop-tmp-*"))),
        "bak": sorted(glob.glob(os.path.join(root, "*.coop-bak-*"))),
    }


def _real_baks(root: str) -> list[str]:
    """真实 promote 备份（排除测试故意放的无关 `*.coop-bak-manual-keep`）。"""
    return [p for p in _residue(root)["bak"]
            if not os.path.basename(p).endswith("coop-bak-manual-keep")]


# ── 子进程注入（父进程定时投递信号，保证落在两步 rename 之间）─────────────

_CHILD_SRC = r'''# -*- coding: utf-8 -*-
"""由 tests/test_pkg_p02_promote_sigterm.py 生成的信号注入子进程。"""
import json, os, sys, time

engine, final, stage, marker, go = sys.argv[1:6]
sys.path.insert(0, engine)
from slcn import coop  # noqa: E402

calls = {"n": 0}


def rename(src, dst):
    os.replace(src, dst)
    calls["n"] += 1
    if calls["n"] == 1:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("go")
        deadline = time.time() + 20.0
        while not os.path.exists(go) and time.time() < deadline:
            time.sleep(0.002)


try:
    result = coop.promote_stage(stage, final, rename=rename)
    payload = {"outcome": "returned", "result": result, "rename_calls": calls["n"]}
except BaseException as exc:  # noqa: BLE001  信号/注入异常后如实上报
    payload = {"outcome": "exception", "exc_type": type(exc).__name__,
               "exc_msg": str(exc)[:200], "rename_calls": calls["n"]}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
'''


def _child_cmd(engine: str, final: str, stage: str, marker: str, go: str) -> list[str]:
    return [sys.executable, CHILD_SCRIPT, engine, final, stage, marker, go]


def _run_window_kill(work: str, sig: int) -> dict:
    """子进程在第一次 rename 后阻塞；父进程确认进入窗口后投递 sig。"""
    root = os.path.join(work, "root")
    final = os.path.join(root, "Ming_Li")
    stage = os.path.join(work, "stage")
    _make_old_final(final)
    _make_stage(stage)
    manual = os.path.join(root, "Ming_Li.coop-bak-manual-keep")
    _write(os.path.join(manual, "keep.txt"), "MANUAL")
    manual_entries = _entries(manual)
    marker, go = os.path.join(work, "marker"), os.path.join(work, "go")
    proc = subprocess.Popen(_child_cmd(_ENGINE, final, stage, marker, go),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    in_window = False
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if os.path.exists(marker):
            in_window = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.002)
    if in_window and proc.poll() is None:
        os.kill(proc.pid, sig)
    with open(go, "w", encoding="utf-8") as fh:
        fh.write("go")
    out, err = proc.communicate(timeout=30)
    time.sleep(0.05)
    res = {
        "root": root, "final": final, "stage": stage,
        "in_window": in_window, "returncode": proc.returncode,
        "signaled": proc.returncode is not None and proc.returncode < 0,
        "child_stdout": out.strip()[-1200:], "child_stderr": err.strip()[-600:],
        "final_exists": os.path.isdir(final),
        "final_entries": _entries(final) if os.path.isdir(final) else None,
        "manual_ok": _entries(manual) == manual_entries,
        "residue": _residue(root),
    }
    return res


def _prepare_ref(work: str) -> tuple[list, list]:
    """返回 (旧交付快照, 新交付快照)；用独立临时目录造，避免被被测目录污染。"""
    ref = os.path.join(work, "ref")
    old = os.path.join(ref, "old")
    new = os.path.join(ref, "new")
    _make_old_final(old)
    _make_stage(new)
    return _entries(old), _entries(new)


# ── S1：窗口内 SIGTERM / SIGINT ──────────────────────────────────────────

def test_s1_signals(tmp: str) -> None:
    for sig_name, sig in (("SIGTERM", signal.SIGTERM), ("SIGINT", signal.SIGINT)):
        work = os.path.join(tmp, f"s1_{sig_name.lower()}")
        os.makedirs(work, exist_ok=True)
        old_entries, new_entries = _prepare_ref(work)
        r = _run_window_kill(work, sig)
        tag = f"S1-{sig_name}"
        check(f"{tag} 信号确实落在两步 rename 窗口内", r["in_window"] and r["returncode"] is not None)
        check(f"{tag} 进程已退出（可被信号终结）", r["returncode"] is not None)
        check(f"{tag} final 仍存在", r["final_exists"])
        check(f"{tag} final 是**完整新交付**（无半成品）", r["final_entries"] == new_entries)
        check(f"{tag} 无 journal 残留", r["residue"]["journal"] == [])
        check(f"{tag} 无 tmp 残留", r["residue"]["tmp"] == [])
        check(f"{tag} 旧交付备份仍在（调用方负责回收）", len(_real_baks(r["root"])) == 1)
        check(f"{tag} 无关 `.coop-bak-*` 不动", r["manual_ok"])
        if sig_name == "SIGTERM":
            check(f"{tag} 默认处置：进程被 SIGTERM 终结", r["signaled"] and r["returncode"] == -signal.SIGTERM)
        else:
            child = json.loads(r["child_stdout"].splitlines()[-1]) if r["child_stdout"] else {}
            check(f"{tag} 解除屏蔽后 KeyboardInterrupt 如实上抛",
                  child.get("outcome") == "exception" and child.get("exc_type") == "KeyboardInterrupt")
            check(f"{tag} 两次 rename 都已执行", child.get("rename_calls") == 2)


# ── S2：SIGKILL 后入口恢复 / 下一次 promote ──────────────────────────────

def test_s2_sigkill_recovery(tmp: str) -> None:
    work = os.path.join(tmp, "s2")
    os.makedirs(work, exist_ok=True)
    old_entries, _ = _prepare_ref(work)
    r = _run_window_kill(work, signal.SIGKILL)
    tag = "S2-SIGKILL"
    check(f"{tag} 进程被硬杀（不可捕获）", r["signaled"] and r["returncode"] == -signal.SIGKILL)
    check(f"{tag} 崩后 final 缺失", not r["final_exists"])
    check(f"{tag} 崩后 journal 在位（恢复凭据）", len(r["residue"]["journal"]) == 1)
    check(f"{tag} 崩后 tmp 在位（待清理）", len(r["residue"]["tmp"]) == 1)
    real_baks = _real_baks(r["root"])
    check(f"{tag} 崩后旧交付完整躺在 backup 里", len(real_baks) == 1
          and _entries(real_baks[0]) == old_entries)

    final = r["final"]
    raised = False
    try:
        coop.promote_stage(os.path.join(work, "__absent_stage__"), final)
    except FileNotFoundError:
        raised = True
    after = _residue(r["root"])
    check(f"{tag} 入口恢复先执行、仍照抛 stage 不存在", raised)
    check(f"{tag} 恢复后 final = 完整旧交付", os.path.isdir(final) and _entries(final) == old_entries)
    check(f"{tag} 恢复后 journal 清理", after["journal"] == [])
    check(f"{tag} 恢复后 tmp 清理", after["tmp"] == [])
    check(f"{tag} 恢复只动记录路径（无关 backup 不动）", r["manual_ok"] and os.path.isdir(
        os.path.join(r["root"], "Ming_Li.coop-bak-manual-keep")))


def test_s2_next_promote(tmp: str) -> None:
    work = os.path.join(tmp, "s2b")
    os.makedirs(work, exist_ok=True)
    old_entries, _ = _prepare_ref(work)
    r = _run_window_kill(work, signal.SIGKILL)
    tag = "S2-next"
    stage_b = os.path.join(work, "stage_b")
    _write(os.path.join(stage_b, "new_b.txt"), "NEW-B")
    new_b_entries = _entries(stage_b)
    result = coop.promote_stage(stage_b, r["final"])
    final_entries = _entries(r["final"])
    backup = result.get("backup") or ""
    after = _residue(r["root"])
    check(f"{tag} 硬中断后下一次 promote 正常返回", sorted(result) == ["backup", "excluded", "final"])
    check(f"{tag} final = 完整新交付 B", final_entries == new_b_entries)
    check(f"{tag} 返回的 backup 是旧交付", bool(backup) and _entries(backup) == old_entries)
    check(f"{tag} backup 名仍符合 `<final>.coop-bak-<stamp>`",
          os.path.basename(backup).startswith("Ming_Li.coop-bak-"))
    check(f"{tag} 结束后无 journal/tmp", after["journal"] == [] and after["tmp"] == [])


# ── S3：正常覆盖交付（journal 在位、返回契约不变）───────────────────────

def test_s3_normal(tmp: str) -> None:
    work = os.path.join(tmp, "s3")
    os.makedirs(work, exist_ok=True)
    root = os.path.join(work, "root")
    final = os.path.join(root, "Ming_Li")
    stage = os.path.join(work, "stage")
    _make_old_final(final)
    _make_stage(stage)
    _write(os.path.join(stage, coop.STATE_NAME), "{}")   # 状态文件必须不被复制
    old_entries = _entries(final)
    expected = [e for e in _entries(stage) if e[0] != coop.STATE_NAME]
    observed: list[dict] = []

    def rename(src: str, dst: str) -> None:
        observed.append({"src": os.path.basename(src), "dst": os.path.basename(dst),
                         "journal": _read_journal(root, final),
                         "final_exists": os.path.exists(final),
                         "doc": os.path.basename(final)})
        os.replace(src, dst)

    result = coop.promote_stage(stage, final, rename=rename)
    tag = "S3"
    check(f"{tag} 返回键仍是 final/backup/excluded", sorted(result) == ["backup", "excluded", "final"])
    check(f"{tag} 默认 excluded = 状态文件", result["excluded"] == [coop.STATE_NAME])
    check(f"{tag} final 内容 = 暂存目录（状态文件除外）", _entries(final) == expected)
    check(f"{tag} backup 内容 = 完整旧交付", _entries(result["backup"]) == old_entries)
    check(f"{tag} 两次 rename：先搬旧、再换新", len(observed) == 2
          and observed[0]["src"] == "Ming_Li" and observed[1]["dst"] == "Ming_Li")
    journal_ok = True
    for idx, item in enumerate(observed):
        j = item["journal"]
        if not isinstance(j, dict):
            journal_ok = False
            continue
        if (j.get("schema") != "slcn.coop.promote-journal/1" or j.get("phase") != "swapping"
                or j.get("final") != final or j.get("backup") != result["backup"]
                or not str(j.get("tmp", "")).startswith(os.path.join(root, ".Ming_Li.coop-tmp-"))
                or not isinstance(j.get("pid"), int)):
            journal_ok = False
    check(f"{tag} 两次换名期间 journal 在位且字段完整", journal_ok)
    check(f"{tag} 换名顺序：旧交付先搬走、新交付换入", observed[0]["final_exists"])
    residue = _residue(root)
    check(f"{tag} 结束后无 journal/tmp 残留", residue["journal"] == [] and residue["tmp"] == [])


def test_s3_journal_safety(tmp: str) -> None:
    """journal 不属于本次 final / 记录路径不合法时，入口恢复不碰任何目录。"""
    work = os.path.join(tmp, "s3safety")
    os.makedirs(work, exist_ok=True)
    root = os.path.join(work, "root")
    final = os.path.join(root, "Ming_Li")
    _make_old_final(final)
    unrelated = os.path.join(root, "Ming_Li.coop-bak-manual-keep")
    _write(os.path.join(unrelated, "keep.txt"), "MANUAL")
    unrelated_entries = _entries(unrelated)
    final_entries = _entries(final)
    journal = _journal_path(root, final)
    with open(journal, "w", encoding="utf-8") as fh:
        json.dump({"schema": "slcn.coop.promote-journal/1", "pid": 1, "phase": "swapping",
                   "final": os.path.join(root, "Other_Final"),     # 不属于本次 final
                   "backup": unrelated, "tmp": os.path.join(root, ".Ming_Li.coop-tmp-x")}, fh)
    raised = False
    try:
        coop.promote_stage(os.path.join(work, "__absent_stage__"), final)
    except FileNotFoundError:
        raised = True
    check("S3-safety 非法/串目录 journal 不触发恢复（仅照抛 stage 不存在）", raised)
    check("S3-safety 无关 `.coop-bak-*` 一个字节不动", _entries(unrelated) == unrelated_entries)
    check("S3-safety final 不被误动", _entries(final) == final_entries)
    os.remove(journal)


# ── S4：第二步 rename 抛错（旧交付必须原样换回）────────────────────────

def test_s4_exception(tmp: str) -> None:
    work = os.path.join(tmp, "s4")
    os.makedirs(work, exist_ok=True)
    root = os.path.join(work, "root")
    final = os.path.join(root, "Ming_Li")
    stage = os.path.join(work, "stage")
    _make_old_final(final)
    _make_stage(stage)
    old_entries = _entries(final)
    calls = {"n": 0}

    def flaky(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("模拟换新失败")
        os.replace(src, dst)

    raised = ""
    try:
        coop.promote_stage(stage, final, rename=flaky)
    except OSError as exc:
        raised = str(exc)
    residue = _residue(root)
    check("S4 第二步 rename 抛错原样上抛", raised == "模拟换新失败")
    check("S4 旧交付原样换回（一字节不动）", _entries(final) == old_entries)
    check("S4 journal 已清理", residue["journal"] == [])
    check("S4 tmp 已清理", residue["tmp"] == [])
    check("S4 无多余 backup 残留（旧目录已换回）", residue["bak"] == [])


# ── S5：首次交付成功 / 失败 ──────────────────────────────────────────────

def test_s5_first_delivery(tmp: str) -> None:
    work = os.path.join(tmp, "s5a")
    os.makedirs(work, exist_ok=True)
    root = os.path.join(work, "root")
    final = os.path.join(root, "Ming_Li")
    stage = os.path.join(work, "stage")
    _make_stage(stage)
    new_entries = _entries(stage)
    result = coop.promote_stage(stage, final)
    residue = _residue(root)
    check("S5 首次交付成功返回 backup 为空串", result["backup"] == "")
    check("S5 首次交付 final 内容 = 暂存目录", _entries(final) == new_entries)
    check("S5 首次交付无 journal/tmp 残留", residue["journal"] == [] and residue["tmp"] == [])

    work2 = os.path.join(tmp, "s5b")
    os.makedirs(work2, exist_ok=True)
    root2 = os.path.join(work2, "root")
    final2 = os.path.join(root2, "Ming_Li")
    stage2 = os.path.join(work2, "stage")
    _make_stage(stage2)

    def raise_first(src: str, dst: str) -> None:
        raise OSError("模拟首次交付换名失败")

    raised = ""
    try:
        coop.promote_stage(stage2, final2, rename=raise_first)
    except OSError as exc:
        raised = str(exc)
    residue2 = _residue(root2)
    check("S5 首次交付失败原样上抛", raised == "模拟首次交付换名失败")
    check("S5 首次交付失败 final 未出现", not os.path.exists(final2))
    check("S5 首次交付失败只清 tmp", residue2["tmp"] == [])
    check("S5 首次交付失败不写 journal", residue2["journal"] == [])
    check("S5 首次交付失败无 backup", residue2["bak"] == [])


# ── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    global CHILD_SCRIPT
    with tempfile.TemporaryDirectory(prefix="slcn_p02sig_") as tmp:
        CHILD_SCRIPT = os.path.join(tmp, "_promote_sig_child.py")
        with open(CHILD_SCRIPT, "w", encoding="utf-8") as fh:
            fh.write(_CHILD_SRC)
        test_s1_signals(tmp)
        test_s2_sigkill_recovery(tmp)
        test_s2_next_promote(tmp)
        test_s3_normal(tmp)
        test_s3_journal_safety(tmp)
        test_s4_exception(tmp)
        test_s5_first_delivery(tmp)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
