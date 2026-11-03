#!/usr/bin/env python3.14
"""B-116（批 115）：review 逐条裁决必须按 `--incoming-sha256` 精确命中（hermetic）。

背景（F1 审计 P0）：旧 Flet 壳 `app/ui/bib_write.py` 的 `_keep`/`_defer` 只传
`--key/--action`，不传冲突指纹；引擎在没有任何 selector 时会退 2，但若调用方改用
`--fields`（或页面把整行字段拼进去），同一条记录里字段不重叠的多条待裁决冲突会被
一次全部标掉。引擎侧 `resolve` 一直支持 `--incoming-sha256`（`slcn/bibdb.py`
`resolve_conflicts`），本脚本把「精确一条 / 零写入边界」钉成契约，防止桥层回退。

覆盖 6 项：
  1. keep 无 selector → 退 2 且库逐字节不变；
  2. keep `--fields journal,volume`（旧写法的等价路径）dry-run → count=2（复现一次
     命中多条），但 dry-run 零写入；
  3. keep `--incoming-sha256 <前缀>` dry-run → count=1、只命中 journal、库不变；
  4. keep + sha 真写 → 只标目标冲突、同记录另一条仍待裁决；`db_sha256_after` 等于
     文件实际 sha；`--backup` 生成与写前逐字节相同的备份；
  5. defer + sha 真写 → 只标目标（volume），journal 仍待裁决；
  6. 不存在的 sha 前缀 → 退 2 且库逐字节不变。

跑法（本机 Python 3.14）：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b116_bib_review_sha.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb  # noqa: E402

pass_n = 0
fails: list[str] = []

BASE_BIB = """@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal Old},
  volume  = {2},
  pages   = {1--10},
  year    = {2020}
}
"""
JOURNAL_NEW_BIB = """@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal New},
  volume  = {2},
  pages   = {1--10},
  year    = {2020}
}
"""
VOLUME_NEW_BIB = """@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal Old},
  volume  = {3},
  pages   = {1--10},
  year    = {2020}
}
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    """记一条断言（PASS 立即打印；FAIL 留到最后汇总并退 1）。"""
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}" + (f"  {detail}" if detail else ""))


def sha256_file(path: str) -> str:
    """整库字节 sha256（与引擎 `--expect-db-sha256` 同口径）。"""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _source(issue: str, slug: str, file: str) -> dict:
    return {"kind": bibdb.KIND_PUBLISHED, "issue": issue, "slug": slug, "file": file}


def make_two_conflict_db(path: str) -> str:
    """Alpha2020 两条不重叠待裁决冲突：journal（旧值）与 volume（旧值）。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text(BASE_BIB, _source("2023年第1期(68)", "01 Base", "base.bib"),
                          verified=True)
    store.import_bib_text(JOURNAL_NEW_BIB,
                          _source("2025年第1期(80)", "02 A", "a.bib"), verified=True)
    store.import_bib_text(VOLUME_NEW_BIB,
                          _source("2025年第2期(81)", "03 B", "b.bib"), verified=True)
    store.save()
    return path


def conflict_shas(path: str) -> dict[str, str]:
    """按字段名取 Alpha2020 的冲突指纹：{"journal": sha, "volume": sha}。"""
    record = next(item for item in bibdb.Store.load(path).records()
                  if item.get("key") == "Alpha2020")
    out: dict[str, str] = {}
    for conflict in record.get("conflicts") or []:
        for field in conflict.get("field_diff") or {}:
            out[str(field)] = str(conflict.get("incoming_sha256") or "")
    return out


def pending_by_field(path: str) -> dict[str, bool]:
    """按字段名给出是否仍待裁决（以 resolved_at 为准，与引擎 conflict_is_pending 同口径）。"""
    record = next(item for item in bibdb.Store.load(path).records()
                  if item.get("key") == "Alpha2020")
    return {
        str(field): bibdb.conflict_is_pending(conflict)
        for conflict in record.get("conflicts") or []
        for field in (conflict.get("field_diff") or {})
    }


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    """跑 `Engine/bibdb.py` 子进程（3.14 + vendor，不碰默认笔记仓：--db 显式）。"""
    env = dict(os.environ)
    paths = [str(_ENGINE), str(_ENGINE / "vendor")]
    old = env.get("PYTHONPATH", "")
    if old:
        paths.append(old)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(_ENGINE / "bibdb.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120,
    )


def payload(proc: subprocess.CompletedProcess) -> dict:
    """解析 CLI 的 `--json` stdout（失败时给空 dict，让断言打印原始 stderr）。"""
    try:
        value = json.loads((proc.stdout or "").strip())
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


with tempfile.TemporaryDirectory(prefix="b116_bib_review_sha_") as tmp:
    db = make_two_conflict_db(os.path.join(tmp, "bibliography.jsonl"))
    shas = conflict_shas(db)
    journal_sha, volume_sha = shas["journal"], shas["volume"]
    before = sha256_file(db)
    check("(0) 夹具：journal/volume 两条待裁决冲突各有独立指纹",
          len(journal_sha) >= 8 and len(volume_sha) >= 8
          and journal_sha != volume_sha,
          f"journal={journal_sha[:12]} volume={volume_sha[:12]}")

    # 1) 无 selector：引擎主动退 2，零写入。
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "keep",
                    "--dry-run", "--json", "--db", db])
    body = payload(proc)
    check("(1) keep 无 selector → rc2、库逐字节不变、报错点名 --incoming-sha256",
          proc.returncode == 2 and sha256_file(db) == before
          and "--incoming-sha256" in (proc.stdout + proc.stderr)
          and body.get("error", "").find("incoming-sha256") >= 0,
          f"rc={proc.returncode}")

    # 2) 旧写法等价路径：--fields 一次命中两条（复现 P0），dry-run 仍零写入。
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "keep",
                    "--fields", "journal,volume", "--dry-run", "--json", "--db", db])
    body = payload(proc)
    check("(2) keep --fields journal,volume dry-run → rc0、count=2（复现一次命中多条）、零写",
          proc.returncode == 0 and body.get("count") == 2
          and body.get("written") is False and sha256_file(db) == before,
          f"rc={proc.returncode} count={body.get('count')}")

    # 3) 精确指纹 dry-run：只命中 journal。
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "keep",
                    "--incoming-sha256", journal_sha[:12], "--dry-run",
                    "--json", "--db", db])
    body = payload(proc)
    matched = body.get("matched") or []
    check("(3) keep + sha 前缀 dry-run → rc0、count=1、只命中 journal、零写",
          proc.returncode == 0 and body.get("count") == 1
          and len(matched) == 1 and matched[0].get("fields") == ["journal"]
          and body.get("written") is False and sha256_file(db) == before,
          f"matched={[item.get('fields') for item in matched]}")

    # 4) 精确指纹真写：只标 journal；volume 仍待裁决；sha 闸 + 备份字节一致。
    backup_dir = os.path.join(tmp, "backup")
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "keep",
                    "--incoming-sha256", journal_sha[:12],
                    "--expect-db-sha256", before, "--backup", backup_dir,
                    "--json", "--db", db])
    body = payload(proc)
    backup_path = str(body.get("backup_path") or "")
    pending = pending_by_field(db)
    after = sha256_file(db)
    check("(4) keep + sha 真写 → 只标 journal、volume 仍待裁决、sha/备份对齐",
          proc.returncode == 0 and body.get("written") is True
          and body.get("count") == 1
          and pending.get("journal") is False and pending.get("volume") is True
          and body.get("db_sha256_after") == after and after != before
          and bool(backup_path) and os.path.isfile(backup_path)
          and sha256_file(backup_path) == before,
          f"pending={pending} backup={os.path.basename(backup_path)}")

    # 5) defer 同样精确：新库上只标 volume，journal 仍待裁决。
    db2 = make_two_conflict_db(os.path.join(tmp, "defer.jsonl"))
    shas2 = conflict_shas(db2)
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "defer",
                    "--incoming-sha256", shas2["volume"][:12],
                    "--json", "--db", db2])
    body = payload(proc)
    pending2 = pending_by_field(db2)
    check("(5) defer + sha 真写 → 只标 volume、journal 仍待裁决",
          proc.returncode == 0 and body.get("count") == 1
          and pending2.get("volume") is False and pending2.get("journal") is True,
          f"pending={pending2}")

    # 6) 不存在的指纹：退 2 且零写入（前缀校验边界）。
    db3 = make_two_conflict_db(os.path.join(tmp, "badsha.jsonl"))
    before3 = sha256_file(db3)
    proc = run_cli(["resolve", "--key", "Alpha2020", "--action", "keep",
                    "--incoming-sha256", "deadbeefcafe", "--dry-run",
                    "--json", "--db", db3])
    body = payload(proc)
    check("(6) 不存在的 sha 前缀 → rc2、库逐字节不变",
          proc.returncode == 2 and sha256_file(db3) == before3
          and "incoming_sha256" in json.dumps(body, ensure_ascii=False),
          f"rc={proc.returncode}")


print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
