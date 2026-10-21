#!/usr/bin/env python3.14
"""B-127（批73）：引用库裁决/入库**引擎侧契约**测试（hermetic：tempfile 造库，不碰真库/真稿）。

覆盖 spec `EditToolBox-notes/drafts/specs/b73-b127-bibdb-gui-adjudication.md` §2 验收 2–6：

  (a) `conflicts --json`：条目数组 + 字段白名单、C 编号与 CONFLICTS.md 同源、
      `--pending-only` 过滤、库缺失/损坏退 2、无冲突退 0 + 空数组、只读（库逐字节不变）。
  (b) `resolve --action replace`：整条写 entry_type+fields、重算 content_sha256、
      key/verified/sources/method 不动、其它记录逐字节不变、lint error 0；
      0 条/多条 bib、key 不符、指纹不存在、重叠 → 退 2 且库零写入；dry-run 库逐字节不变；
      重复执行幂等；`replaced_fields` 逐字段 old→new（GUI 预览契约）。
  (c) 写前安全闸：`--expect-db-sha256`（完整 64 hex、不符退 2 零写）、`--backup DIR`
      （写前生成 `bibliography-<UTCts>-<sha12>.jsonl`、仅实际写入时、校验字节）。
  (d) `resolve --adopt-all-pending`：dry-run 逐条 preview + 库不变；真写单次备份单次原子写、
      每项 resolved_at；同记录待裁决冲突字段重叠 → 退 2 列冲突 #；幂等。
  (e) `restore --backup`：备份校验失败/expect 不符退 2 零写；成功恢复 + 前后 sha；
      **不 reopen**（已裁决冲突回到待裁决）；dry-run 不写。
  (f) 默认零变化：老 keep/defer/adopt 动作的人读输出与 `--json` 老键仍在（新增 3 键按
      Lead 批73 增补裁决），`report-conflicts --json` 老键集不变。

跑法（本机必须 Python 3.14 framework + PYTHONPATH=vendor）：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b73_bibdb_adjudication.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
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

SPEC_ITEM_KEYS = {
    "stable_no", "record_key", "bib_key", "entry_type", "title", "author",
    "year", "issue", "slug", "file", "at", "incoming_sha256", "pending",
    "resolution", "resolved_at", "diff", "advice", "verified", "method",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def read_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha(path: str) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep
           + str(_ENGINE / "vendor"), PYTHONIOENCODING="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def parse_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


# ─────────────────────────────────────────────────────────────────────────
# 夹具：合成库（与 test_bibdb.py 的 adopt 夹具同构）
# ─────────────────────────────────────────────────────────────────────────

BASE_SOURCE = {"kind": bibdb.KIND_PUBLISHED, "issue": "2023年第1期(68)",
               "slug": "01 Base", "file": "base.bib"}

#: 一个多记录夹具：
#:   Alpha2020 —— 2 条**字段不重叠**的待裁决冲突（journal / volume）
#:   Beta2019  —— 1 条待裁决冲突（publisher）
#:   Delta2016 —— 1 条 incoming 为空的冲突（pages 应被 adopt-all 删除）
MULTI_BASE = """@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal Old},
  volume  = {2},
  pages   = {1--10},
  year    = {2020}
}
@book{Beta2019,
  author    = {Beta, Bob},
  title     = {Beta Book},
  publisher = {Base Press},
  year      = {2019}
}
@article{Delta2016,
  author  = {Delta, Dan},
  title   = {Delta Work},
  journal = {Journal Old},
  pages   = {5--6},
  year    = {2016}
}
"""


def _source(issue: str, slug: str, file: str) -> dict:
    return {"kind": bibdb.KIND_PUBLISHED, "issue": issue, "slug": slug, "file": file}


def make_multi_db(path: str) -> str:
    """多记录夹具：Alpha 2 条不重叠冲突 / Beta 1 条 / Delta 1 条删字段冲突。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text(MULTI_BASE, BASE_SOURCE, verified=True)
    store.import_bib_text("""@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal New},
  volume  = {2},
  pages   = {1--10},
  year    = {2020}
}
""", _source("2025年第1期(80)", "02 A", "a.bib"), verified=True)
    store.import_bib_text("""@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work},
  journal = {Journal Old},
  volume  = {3},
  pages   = {1--10},
  year    = {2020}
}
""", _source("2025年第2期(81)", "03 B", "b.bib"), verified=True)
    store.import_bib_text("""@book{Beta2019,
  author    = {Beta, Bob},
  title     = {Beta Book},
  publisher = {New Press},
  year      = {2019}
}
""", _source("2026年第1期(86)", "04 C", "c.bib"), verified=True)
    store.import_bib_text("""@article{Delta2016,
  author  = {Delta, Dan},
  title   = {Delta Work},
  journal = {Journal Old},
  year    = {2016}
}
""", _source("2026年第2期(87)", "05 D", "d.bib"), verified=True)
    store.save()
    return path


def make_overlap_db(path: str) -> str:
    """字段重叠夹具：Gamma2021 两条待裁决冲突都改 journal（同一记录 → 必须拒绝批量）。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text("""@article{Gamma2021,
  author  = {Gamma, Gil},
  title   = {Gamma Work},
  journal = {Journal One},
  year    = {2021}
}
""", BASE_SOURCE, verified=True)
    store.import_bib_text("""@article{Gamma2021,
  author  = {Gamma, Gil},
  title   = {Gamma Work},
  journal = {Journal Two},
  year    = {2021}
}
""", _source("2025年第1期(80)", "02 A", "a.bib"), verified=True)
    store.import_bib_text("""@article{Gamma2021,
  author  = {Gamma, Gil},
  title   = {Gamma Work},
  journal = {Journal Three},
  year    = {2021}
}
""", _source("2025年第2期(81)", "03 B", "b.bib"), verified=True)
    store.save()
    return path


def pending_conflicts(db: str, key: str) -> list[dict]:
    record = bibdb.Store.load(db).get(key)
    return [c for c in (record.get("conflicts") or [])
            if bibdb.conflict_is_pending(c)]


def conflict_with_field(db: str, key: str, field: str) -> dict:
    for conflict in pending_conflicts(db, key):
        if field in (conflict.get("field_diff") or {}):
            return conflict
    raise AssertionError(f"{key} 没有含字段 {field} 的待裁决冲突")


def records_by_key(path: str) -> dict:
    return {record["key"]: record for record in read_records(path)}


CUSTOM_ALPHA = """@article{Alpha2020,
  author  = {Alpha, Ann},
  title   = {Alpha Work: Revised},
  journal = {Journal Custom},
  volume  = {2},
  pages   = {1--10},
  year    = {2020},
  doi     = {10.1000/custom}
}
"""

CUSTOM_GAMMA = """@article{Gamma2021,
  author  = {Gamma, Gil},
  title   = {Gamma Work},
  journal = {Journal Custom},
  year    = {2021}
}
"""


# ─────────────────────────────────────────────────────────────────────────
print("── (a) conflicts --json：条目数组 / 字段白名单 / C 编号 / 过滤 / 错误────────────────")

with tempfile.TemporaryDirectory(prefix="b73_conflicts_") as tmp:
    db = make_multi_db(os.path.join(tmp, "multi.jsonl"))
    digest = read_bytes(db)
    r = run_cli("conflicts", "--db", db, "--json")
    payload = parse_json(r)
    items = payload.get("items") or []
    check("(a) `conflicts --json` 退出 0、给 db/db_sha256/totals/items",
          r.returncode == 0
          and set(payload) == {"db", "db_sha256", "totals", "items"},
          json.dumps(sorted(payload), ensure_ascii=False))
    check("(a) db_sha256 = 库文件字节 sha256（64 位十六进制）",
          payload.get("db_sha256") == hashlib.sha256(digest).hexdigest()
          and re.fullmatch(r"[0-9a-f]{64}", str(payload.get("db_sha256") or "")))
    check("(a) totals：total 4 / pending 4 / resolved 0",
          payload.get("totals") == {"total": 4, "pending": 4, "resolved": 0},
          str(payload.get("totals")))
    check("(a) 条目字段**恰好**是 spec §4.1 白名单（不含内部 record/incoming_source）",
          len(items) == 4
          and all(set(item) == SPEC_ITEM_KEYS for item in items),
          str(sorted(items[0]) if items else "无条目"))
    check("(a) 条目内容：stable_no 连续、record_key/bib_key/指纹/diff/advice/pending",
          sorted(item["stable_no"] for item in items) == [1, 2, 3, 4]
          and {item["record_key"] for item in items}
          == {"Alpha2020", "Beta2019", "Delta2016"}
          and all(item["pending"] is True for item in items)
          and all(re.fullmatch(r"[0-9a-f]{64}", item["incoming_sha256"]) for item in items)
          and all(item["diff"] for item in items)
          and all(item["advice"] for item in items))
    check("(a) 只读：跑 `conflicts --json` 后库文件逐字节不变", read_bytes(db) == digest)

    # 裁掉一条（keep）→ 已裁决条目仍列在 items（stable_no 不重排）、--pending-only 过滤
    target = conflict_with_field(db, "Alpha2020", "journal")["incoming_sha256"]
    r_keep = run_cli("resolve", "--key", "Alpha2020", "--action", "keep",
                     "--incoming-sha256", target[:12], "--db", db)
    r = run_cli("conflicts", "--db", db, "--json")
    payload_all = parse_json(r)
    items_all = payload_all.get("items") or []
    resolved_item = [item for item in items_all if not item["pending"]]
    check("(a) 裁决后：total 仍 4、pending 3 / resolved 1（C 编号不重排）",
          r.returncode == 0 and r_keep.returncode == 0
          and payload_all.get("totals") == {"total": 4, "pending": 3, "resolved": 1}
          and len(items_all) == 4
          and len(resolved_item) == 1
          and resolved_item[0]["resolution"] == "kept-existing"
          and resolved_item[0]["resolved_at"]
          and sorted(item["stable_no"] for item in items_all) == [1, 2, 3, 4],
          str(payload_all.get("totals")))
    r = run_cli("conflicts", "--db", db, "--pending-only", "--json")
    payload_pending = parse_json(r)
    items_pending = payload_pending.get("items") or []
    check("(a) `--pending-only`：只返回 3 条待裁决，totals 仍是全库口径",
          r.returncode == 0 and len(items_pending) == 3
          and all(item["pending"] is True for item in items_pending)
          and payload_pending.get("totals") == {"total": 4, "pending": 3, "resolved": 1})
    check("(a) 同一库重复 `conflicts --json` 输出逐字节相同（确定性）",
          run_cli("conflicts", "--db", db, "--json").stdout
          == run_cli("conflicts", "--db", db, "--json").stdout)

    # C 编号与 CONFLICTS.md 同源：待裁决条目的 Cxxx 出现在清单里，已裁决条目的编号不出现
    digest_before_report = read_bytes(db)
    out_rep = os.path.join(tmp, "reports")
    r_rep = run_cli("report-conflicts", "--db", db, "--out", out_rep)
    report_text = open(os.path.join(out_rep, "CONFLICTS.md"), encoding="utf-8").read()
    pending_nos = [item["stable_no"] for item in items_pending]
    resolved_no = resolved_item[0]["stable_no"]
    check("(a) C 编号与 CONFLICTS.md 一致（待裁决 Cxxx 全在清单；已裁决编号不出现）",
          r_rep.returncode == 0
          and all(f"C{no:03d}" in report_text for no in pending_nos)
          and f"C{resolved_no:03d}" not in report_text,
          f"pending={pending_nos} resolved={resolved_no}")
    check("(a) 库文件在 report-conflicts 后仍逐字节不变（清单只写 .md）",
          read_bytes(db) == digest_before_report)

    # 无冲突库 → 退出 0 + 空数组；库缺失/损坏 → 退出 2
    clean = os.path.join(tmp, "clean.jsonl")
    store = bibdb.Store.load(clean, create=True)
    store.import_bib_text(MULTI_BASE, BASE_SOURCE, verified=True)
    store.save()
    r = run_cli("conflicts", "--db", clean, "--json")
    check("(a) 无冲突库：退出 0 + items 空数组 + totals 全 0",
          r.returncode == 0
          and parse_json(r).get("items") == []
          and parse_json(r).get("totals") == {"total": 0, "pending": 0, "resolved": 0},
          (r.stdout + r.stderr)[-200:])
    r = run_cli("conflicts", "--db", os.path.join(tmp, "missing.jsonl"), "--json")
    check("(a) 库缺失 → 退出 2", r.returncode == 2,
          (r.stdout + r.stderr)[-200:])
    broken = write(os.path.join(tmp, "broken.jsonl"),
                   '{"version":1,"key":"X","entry_type":"article"}\n不是 JSON\n')
    r = run_cli("conflicts", "--db", broken, "--json")
    check("(a) 库损坏 → 退出 2", r.returncode == 2,
          (r.stdout + r.stderr)[-200:])


# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) resolve --action replace：人工裁决 bib 整条入库 ──")

with tempfile.TemporaryDirectory(prefix="b73_replace_") as tmp:
    db = make_multi_db(os.path.join(tmp, "replace.jsonl"))
    digest = read_bytes(db)
    before_records = records_by_key(db)
    custom = write(os.path.join(tmp, "custom_alpha.bib"), CUSTOM_ALPHA)
    custom_sha = sha(custom)
    target = conflict_with_field(db, "Alpha2020", "journal")["incoming_sha256"]
    zero_entry = write(os.path.join(tmp, "zero.bib"), "% 只有注释，没有条目\n")
    two_entry = write(os.path.join(tmp, "two.bib"),
                      CUSTOM_ALPHA + CUSTOM_ALPHA.replace("Alpha2020", "Other2020"))
    wrong_key = write(os.path.join(tmp, "wrong_key.bib"),
                      CUSTOM_ALPHA.replace("Alpha2020", "Zeta9999"))

    # ── 失败门禁：0 条/多条 bib、key 不符、指纹不存在、文件缺失、缺 --bib → 退 2 且库零写入 ──
    cases = [
        ("0 条条目", ("--bib", zero_entry)),
        ("2 条条目", ("--bib", two_entry)),
        ("bib key 与库内 record key 不符", ("--bib", wrong_key)),
        ("bib 文件不存在", ("--bib", os.path.join(tmp, "nope.bib"))),
    ]
    for label, extra in cases:
        r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                    "--incoming-sha256", target[:12], *extra, "--db", db)
        check(f"(b) replace {label} → 退出 2 且库零写入",
              r.returncode == 2 and read_bytes(db) == digest,
              (r.stdout + r.stderr)[-250:])
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--db", db)
    check("(b) replace 缺 --bib → 退出 2", r.returncode == 2
          and "--bib" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--fields", "title", "--db", db)
    check("(b) replace 误给 --fields（与 --incoming-sha256 互斥）→ 退出 2",
          r.returncode == 2
          and ("不要同时给" in (r.stdout + r.stderr)
               or "not allowed with argument" in (r.stdout + r.stderr)),
          (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", "00000000", "--bib", custom, "--db", db)
    check("(b) replace 指纹不存在 → 退出 2 且库零写入",
          r.returncode == 2 and read_bytes(db) == digest
          and "没有以" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "NoSuchKey", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom, "--db", db)
    check("(b) replace 未知 key → 退出 2", r.returncode == 2
          and "没有记录 key" in (r.stdout + r.stderr))

    # ── dry-run：GUI 预览契约（replaced_fields/指纹/overlap/sha/totals）+ 库逐字节不变 ──
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--dry-run", "--json", "--db", db)
    payload = parse_json(r)
    fields = payload.get("replaced_fields") or {}
    check("(b) replace --dry-run --json：action=replace、dry_run、count=1",
          r.returncode == 0 and payload.get("action") == "replace"
          and payload.get("dry_run") is True and payload.get("count") == 1,
          (r.stdout + r.stderr)[-300:])
    check("(b) --dry-run 给 `replaced_fields` 逐字段 old→new（GUI 预览依赖）",
          fields.get("journal") == {"old": "Journal Old", "new": "Journal Custom",
                                    "remove": False}
          and fields.get("title", {}).get("new") == "Alpha Work: Revised"
          and fields.get("doi", {}).get("old") == ""
          and fields.get("doi", {}).get("new") == "10.1000/custom",
          json.dumps(fields, ensure_ascii=False)[:400])
    check("(b) --dry-run 给 incoming_sha256 / content_sha256 before→after / totals",
          payload.get("incoming_sha256") == target[:12]
          and payload.get("content_sha256_before")
          != payload.get("content_sha256_after")
          and payload.get("totals") == {"total": 4, "pending": 4, "resolved": 0},
          json.dumps({k: payload.get(k) for k in
                      ("incoming_sha256", "content_sha256_before",
                       "content_sha256_after", "totals")}, ensure_ascii=False)[:300])
    check("(b) --dry-run：overlapping/superseded 为空、written=False、库逐字节不变",
          payload.get("overlapping") == [] and payload.get("superseded") == []
          and payload.get("written") is False
          and payload.get("db_sha256_before") == payload.get("db_sha256_after")
          == hashlib.sha256(digest).hexdigest()
          and payload.get("backup_path") == ""
          and read_bytes(db) == digest)
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--dry-run", "--db", db)
    check("(b) 人读 dry-run：明说「人工裁决 bib」「新建/修改字段」「未写库」",
          r.returncode == 0 and "采用人工裁决 bib" in r.stdout
          and "content_sha256" in r.stdout
          and "只算不写库" in r.stdout and read_bytes(db) == digest)

    # ── 写前安全闸：--expect-db-sha256 完整 64 hex、不符/格式错 → 退 2 零写入 ──
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--expect-db-sha256", "0" * 64, "--db", db)
    check("(b) --expect-db-sha256 不符 → 退出 2 且库零写入",
          r.returncode == 2 and "不符" in (r.stdout + r.stderr)
          and read_bytes(db) == digest, (r.stdout + r.stderr)[-250:])
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--expect-db-sha256", "abc123", "--db", db)
    check("(b) --expect-db-sha256 不是完整 64 hex → 退出 2 零写入",
          r.returncode == 2 and "64 位十六进制" in (r.stdout + r.stderr)
          and read_bytes(db) == digest, (r.stdout + r.stderr)[-250:])

    # ── dry-run 带 --backup：只算不写 → 不建备份目录/文件 ──
    dry_backup_dir = os.path.join(tmp, "backup_dry")
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--dry-run", "--backup", dry_backup_dir, "--db", db)
    check("(b) dry-run + --backup：不创建备份目录、库不变",
          r.returncode == 0 and not os.path.exists(dry_backup_dir)
          and read_bytes(db) == digest)

    # ── 真正 replace（备份 + expect 正 sha）：整条写字段、元数据不动、其它记录不变 ──
    backup_dir = os.path.join(tmp, "backups")
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--note", "用户裁决：按人工 bib 修订",
                "--expect-db-sha256", hashlib.sha256(digest).hexdigest(),
                "--backup", backup_dir, "--json", "--db", db)
    payload = parse_json(r)
    after_records = records_by_key(db)
    record = after_records["Alpha2020"]
    expected_fields = bibdb.canonical_fields({
        "author": "Alpha, Ann", "title": "Alpha Work: Revised",
        "journal": "Journal Custom", "volume": "2", "pages": "1--10",
        "year": "2020", "doi": "10.1000/custom",
    })
    backups = sorted(os.listdir(backup_dir)) if os.path.isdir(backup_dir) else []
    check("(b) replace 成功：退出 0、written=True、动作/来源标记正确",
          r.returncode == 0 and payload.get("written") is True
          and payload.get("action") == "replace"
          and payload.get("resolved_by") == "custom-bib"
          and payload.get("resolution") == "adopted",
          (r.stdout + r.stderr)[-300:])
    check("(b) 只写 entry_type+fields（= 人工 bib 规范化后内容、key 不变）",
          record["entry_type"] == "article"
          and record["fields"] == expected_fields
          and record["key"] == "Alpha2020",
          json.dumps(record["fields"], ensure_ascii=False)[:300])
    check("(b) content_sha256 已重算且与字段内容一致",
          record["content_sha256"]
          == bibdb.content_sha256(record["entry_type"], record["fields"])
          and record["content_sha256"]
          != before_records["Alpha2020"]["content_sha256"])
    check("(b) verified/sources/method/verified_at/aliases/created_at 一字不动",
          all(record[key] == before_records["Alpha2020"][key]
              for key in ("verified", "verified_at", "method", "sources",
                          "aliases", "created_at")),
          "元数据对比")
    check("(b) 目标冲突：resolution=adopted + resolved_by=resolved_bib_sha256 + note",
          (lambda conflict: conflict is not None and conflict.get("resolution") == "adopted"
           and conflict.get("resolved_by") == "custom-bib"
           and conflict.get("resolved_bib_sha256") == custom_sha
           and conflict.get("resolved_at")
           and "采用自定义裁决 bib" in (conflict.get("note") or "")
           and "用户裁决：按人工 bib 修订" in (conflict.get("note") or "")
           )([c for c in record["conflicts"]
              if c.get("incoming_sha256") == target][0]))
    check("(b) 其它记录逐条 JSON 不变（Beta2019/Delta2016）",
          after_records["Beta2019"] == before_records["Beta2019"]
          and after_records["Delta2016"] == before_records["Delta2016"])
    check("(b) 写后 lint 无 error（退出 0）",
          run_cli("lint", "--db", db).returncode == 0,
          (run_cli("lint", "--db", db).stdout + run_cli("lint", "--db", db).stderr)[-300:])
    check("(b) 写前备份：目录里恰好 1 份 `bibliography-<UTCts>-<sha12>.jsonl`、内容=写前库",
          len(backups) == 1
          and re.fullmatch(r"bibliography-\d{8}T\d{6}Z-[0-9a-f]{12}\.jsonl", backups[0])
          is not None
          and backups[0].endswith(f"-{hashlib.sha256(digest).hexdigest()[:12]}.jsonl")
          and read_bytes(os.path.join(backup_dir, backups[0])) == digest,
          str(backups))
    check("(b) --json 回填 db_sha256_before/after + backup_path（GUI 状态条）",
          payload.get("db_sha256_before") == hashlib.sha256(digest).hexdigest()
          and payload.get("db_sha256_after") == hashlib.sha256(read_bytes(db)).hexdigest()
          and payload.get("backup_path") == os.path.join(backup_dir, backups[0]),
          json.dumps({k: payload.get(k) for k in
                      ("db_sha256_before", "db_sha256_after", "backup_path")})[:300])

    # ── 幂等：同指纹 + 同 bib 重复 replace → 无变化、不写库、不产生新备份 ──
    digest_after = read_bytes(db)
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--backup", backup_dir, "--json", "--db", db)
    payload2 = parse_json(r)
    check("(b) 重复 replace 幂等：退出 0、already=True、written=False、库不变、无新备份",
          r.returncode == 0 and payload2.get("already") is True
          and payload2.get("written") is False
          and read_bytes(db) == digest_after
          and len(os.listdir(backup_dir)) == 1,
          json.dumps({k: payload2.get(k) for k in ("already", "written")},
                     ensure_ascii=False))

    # ── 已裁决冲突不能改判（不做 reopen）：换一份 bib 再 replace → 退 2 零写入 ──
    changed_bib = write(os.path.join(tmp, "custom_alpha2.bib"),
                        CUSTOM_ALPHA.replace("Journal Custom", "Journal Again"))
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", changed_bib, "--db", db)
    check("(b) 已裁决冲突不能再用 replace 改判 → 退出 2、库不变、提示 restore",
          r.returncode == 2 and "不能再用 replace 改判" in (r.stdout + r.stderr)
          and "restore" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_after, (r.stdout + r.stderr)[-250:])

    # ── --backup 参数是已存在文件 → 退 2；确认未写库 ──
    r = run_cli("resolve", "--key", "Beta2019", "--action", "keep", "--all-fields",
                "--backup", custom, "--db", db)
    check("(b) --backup 指向已存在文件 → 退出 2、库不变",
          r.returncode == 2 and "同名文件" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_after, (r.stdout + r.stderr)[-200:])


# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) resolve --action replace：重叠冲突保护（默认拒绝 / --allow-overlap → superseded）──")

with tempfile.TemporaryDirectory(prefix="b73_replace_overlap_") as tmp:
    db = make_overlap_db(os.path.join(tmp, "overlap.jsonl"))
    digest = read_bytes(db)
    target = conflict_with_field(db, "Gamma2021", "journal")["incoming_sha256"]
    custom = write(os.path.join(tmp, "gamma.bib"), CUSTOM_GAMMA)
    r = run_cli("resolve", "--key", "Gamma2021", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom, "--db", db)
    check("(c) replace 与其它待裁决冲突字段重叠 → 默认拒绝、退出 2、给指引、库不变",
          r.returncode == 2 and "重叠" in (r.stdout + r.stderr)
          and "allow-overlap" in (r.stdout + r.stderr)
          and read_bytes(db) == digest, (r.stdout + r.stderr)[-300:])
    # 与 adopt 一致：预览重叠冲突需要显式 --allow-overlap（默认拒绝连 dry-run 也拒）
    r = run_cli("resolve", "--key", "Gamma2021", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--dry-run", "--allow-overlap", "--json", "--db", db)
    payload = parse_json(r)
    check("(c) dry-run 报出重叠冲突（#索引/共享字段）且 superseded 为空、库不变",
          r.returncode == 0 and len(payload.get("overlapping") or []) == 1
          and "journal" in payload["overlapping"][0].get("shared")
          and payload.get("superseded") == []
          and read_bytes(db) == digest,
          json.dumps(payload.get("overlapping"), ensure_ascii=False)[:250])
    r = run_cli("resolve", "--key", "Gamma2021", "--action", "replace",
                "--incoming-sha256", target[:12], "--bib", custom,
                "--allow-overlap", "--json", "--db", db)
    record = bibdb.Store.load(db).get("Gamma2021")
    superseded = [c for c in record["conflicts"]
                  if c.get("resolution") == "superseded"]
    check("(c) --allow-overlap → 重叠冲突标 superseded + resolved_at",
          r.returncode == 0 and len(superseded) == 1
          and superseded[0].get("resolved_at")
          and "replace" in (superseded[0].get("note") or "")
          and record["fields"]["journal"] == "Journal Custom",
          json.dumps([c.get("resolution") for c in record["conflicts"]]))


# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) resolve --adopt-all-pending：一键全部采用 ──")

with tempfile.TemporaryDirectory(prefix="b73_adopt_all_") as tmp:
    db = make_multi_db(os.path.join(tmp, "all.jsonl"))
    digest = read_bytes(db)
    before_records = records_by_key(db)

    # 参数互斥/非法组合 → 退 2 且零写入
    for label, extra in [
        ("与 --key 同时给", ("--key", "Alpha2020")),
        ("与 --action 同时给", ("--action", "keep")),
        ("误给 --incoming-sha256", ("--incoming-sha256", "abcdef12")),
        ("误给 --allow-overlap", ("--allow-overlap",)),
    ]:
        r = run_cli("resolve", "--adopt-all-pending", *extra, "--db", db)
        check(f"(d) --adopt-all-pending {label} → 退出 2、库不变",
              r.returncode == 2 and read_bytes(db) == digest,
              (r.stdout + r.stderr)[-200:])

    # dry-run：逐条 preview（字段 old→new、删除标记）+ 总账 + 库逐字节不变
    r = run_cli("resolve", "--adopt-all-pending", "--dry-run", "--json", "--db", db)
    payload = parse_json(r)
    adopted = payload.get("adopted") or []
    delta_row = [row for row in adopted if row["record_key"] == "Delta2016"]
    check("(d) dry-run：退出 0、adopted 4 条冲突、库逐字节不变",
          r.returncode == 0 and len(adopted) == 4
          and payload.get("dry_run") is True and payload.get("written") is False
          and read_bytes(db) == digest,
          json.dumps([row["record_key"] for row in adopted], ensure_ascii=False))
    check("(d) dry-run 逐条 preview：fields old→new + incoming_sha256 + record_key/bib_key",
          all(set(row) >= {"record_key", "bib_key", "incoming_sha256", "fields"}
              for row in adopted)
          and all(re.fullmatch(r"[0-9a-f]{64}", row["incoming_sha256"]) for row in adopted)
          and adopted[0]["fields"])
    check("(d) dry-run 删除标记：Delta2016.pages remove=True（incoming 为空）",
          bool(delta_row) and delta_row[0]["fields"].get("pages", {}).get("remove")
          is True and delta_row[0]["fields"]["pages"]["old"] == "5--6",
          json.dumps(delta_row, ensure_ascii=False)[:300])
    check("(d) dry-run totals_before/after + 前后 sha + backup_path 空 + superseded 空",
          payload.get("superseded") == []
          and payload.get("totals_before") == {"total": 4, "pending": 4, "resolved": 0}
          and payload.get("totals_after") == {"total": 4, "pending": 0, "resolved": 4}
          and payload.get("db_sha256_before") == payload.get("db_sha256_after")
          == hashlib.sha256(digest).hexdigest()
          and payload.get("backup_path") == "",
          json.dumps({k: payload.get(k) for k in
                      ("totals_before", "totals_after", "backup_path")},
                     ensure_ascii=False)[:300])
    dry_backup = os.path.join(tmp, "backup_dry")
    r = run_cli("resolve", "--adopt-all-pending", "--dry-run", "--backup", dry_backup,
                "--db", db)
    check("(d) dry-run + --backup：不创建备份目录、库不变",
          r.returncode == 0 and not os.path.exists(dry_backup)
          and read_bytes(db) == digest)

    # 真写：单次备份 + 单次原子写 + 每项 adopted/resolved_at
    backup_dir = os.path.join(tmp, "backups")
    r = run_cli("resolve", "--adopt-all-pending",
                "--expect-db-sha256", hashlib.sha256(digest).hexdigest(),
                "--backup", backup_dir, "--json", "--db", db)
    payload = parse_json(r)
    store = bibdb.Store.load(db)
    backups = sorted(os.listdir(backup_dir)) if os.path.isdir(backup_dir) else []
    alpha = store.get("Alpha2020")
    beta = store.get("Beta2019")
    delta = store.get("Delta2016")
    all_pending = [c for key in ("Alpha2020", "Beta2019", "Delta2016")
                   for c in (store.get(key).get("conflicts") or [])
                   if bibdb.conflict_is_pending(c)]
    check("(d) 真写：退出 0、written=True、totals_after 待裁决 0 / 已裁决 4",
          r.returncode == 0 and payload.get("written") is True
          and payload.get("totals_after") == {"total": 4, "pending": 0, "resolved": 4}
          and not all_pending,
          json.dumps(payload.get("totals_after"), ensure_ascii=False))
    check("(d) 逐字段采用：Alpha journal/volume + Beta publisher 全按 incoming 写库",
          alpha["fields"]["journal"] == "Journal New"
          and alpha["fields"]["volume"] == "3"
          and beta["fields"]["publisher"] == "New Press"
          and store.get("Alpha2020")["fields"]["title"] == "Alpha Work")
    check("(d) incoming 为空 → 删除字段（Delta2016.pages 被删）",
          "pages" not in delta["fields"]
          and delta["fields"]["title"] == "Delta Work")
    check("(d) 每条冲突标 adopted + resolved_at + note（一键全部采用）",
          all(c.get("resolution") == "adopted" and c.get("resolved_at")
              and "一键全部采用" in (c.get("note") or "")
              for c in (alpha["conflicts"] + beta["conflicts"] + delta["conflicts"])),
          str([c.get("resolution") for c in alpha["conflicts"]]))
    check("(d) content_sha256 全部按新 fields 重算",
          all(record["content_sha256"]
              == bibdb.content_sha256(record["entry_type"], record["fields"])
              for record in (alpha, beta, delta)))
    check("(d) 写前备份：恰好 1 份、内容=写前库字节",
          len(backups) == 1
          and re.fullmatch(r"bibliography-\d{8}T\d{6}Z-[0-9a-f]{12}\.jsonl", backups[0])
          is not None
          and read_bytes(os.path.join(backup_dir, backups[0])) == digest,
          str(backups))
    check("(d) --json：backup_path + 前后 sha 回填",
          payload.get("backup_path") == os.path.join(backup_dir, backups[0])
          and payload.get("db_sha256_before") == hashlib.sha256(digest).hexdigest()
          and payload.get("db_sha256_after") == sha(db))
    check("(d) 写后 lint 无 error、其它记录（未涉及冲突的）内容不变",
          run_cli("lint", "--db", db).returncode == 0
          and set(records_by_key(db)) == set(before_records))

    # 幂等：再跑一次 → 无变化、不产生新备份
    digest_after = read_bytes(db)
    r = run_cli("resolve", "--adopt-all-pending", "--backup", backup_dir,
                "--json", "--db", db)
    payload = parse_json(r)
    check("(d) 重复 --adopt-all-pending 幂等：adopted=[]、written=False、库不变、无新备份",
          r.returncode == 0 and payload.get("adopted") == []
          and payload.get("written") is False
          and read_bytes(db) == digest_after
          and len(os.listdir(backup_dir)) == 1,
          json.dumps({k: payload.get(k) for k in ("adopted", "written")},
                     ensure_ascii=False)[:200])

    # 同记录待裁决冲突字段重叠 → 整体拒绝（列出冲突 #、提示逐条）
with tempfile.TemporaryDirectory(prefix="b73_adopt_all_block_") as tmp:
    db = make_overlap_db(os.path.join(tmp, "block.jsonl"))
    digest = read_bytes(db)
    r = run_cli("resolve", "--adopt-all-pending", "--db", db)
    check("(d) 同记录待裁决冲突字段重叠 → 退出 2、列出记录与冲突 #、库不变",
          r.returncode == 2 and "字段重叠" in (r.stdout + r.stderr)
          and "Gamma2021" in (r.stdout + r.stderr)
          and "#" in (r.stdout + r.stderr)
          and "逐条" in (r.stdout + r.stderr)
          and read_bytes(db) == digest, (r.stdout + r.stderr)[-300:])
    r = run_cli("resolve", "--adopt-all-pending", "--dry-run", "--json", "--db", db)
    check("(d) 重叠时 --json 也给 {\"error\": 中文原因}（GUI 回显）",
          r.returncode == 2
          and "字段重叠" in str(parse_json(r).get("error") or ""),
          (r.stdout + r.stderr)[-200:])


# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) restore：从备份整库恢复（不 reopen）──")

with tempfile.TemporaryDirectory(prefix="b73_restore_") as tmp:
    db = make_multi_db(os.path.join(tmp, "restore.jsonl"))
    digest_before = read_bytes(db)
    target = conflict_with_field(db, "Alpha2020", "journal")["incoming_sha256"]
    backup_dir = os.path.join(tmp, "backups")
    # 用引擎自身的写前备份生成一份「裁决前」备份
    r = run_cli("resolve", "--key", "Alpha2020", "--action", "keep",
                "--incoming-sha256", target[:12], "--backup", backup_dir, "--db", db)
    backup_file = os.path.join(backup_dir, os.listdir(backup_dir)[0])
    digest_after = read_bytes(db)
    check("(e) 夹具：keep 后库已变、备份内容=裁决前库",
          r.returncode == 0 and digest_after != digest_before
          and read_bytes(backup_file) == digest_before)
    resolved = [c for c in bibdb.Store.load(db).get("Alpha2020")["conflicts"]
                if not bibdb.conflict_is_pending(c)]
    check("(e) 夹具：keep 后 Alpha2020 有 1 条已裁决冲突", len(resolved) == 1)

    # 校验失败 / expect 不符 → 退 2 零写
    broken_backup = write(os.path.join(tmp, "broken_backup.jsonl"),
                         read_bytes(backup_file).decode("utf-8") + "不是 JSON\n")
    r = run_cli("restore", "--backup", broken_backup, "--db", db)
    check("(e) 备份结构校验失败 → 退出 2、库不变",
          r.returncode == 2 and "备份文件校验失败" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_after, (r.stdout + r.stderr)[-250:])
    r = run_cli("restore", "--backup", backup_file,
                "--expect-db-sha256", "0" * 64, "--db", db)
    check("(e) --expect-db-sha256 不符 → 退出 2、库不变",
          r.returncode == 2 and "不符" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_after, (r.stdout + r.stderr)[-250:])
    r = run_cli("restore", "--backup", backup_file, "--dry-run", "--json", "--db", db)
    payload = parse_json(r)
    check("(e) restore --dry-run：只校验、written=False、库不变、前后 sha 相同",
          r.returncode == 0 and payload.get("dry_run") is True
          and payload.get("written") is False
          and payload.get("db_sha256_before") == payload.get("db_sha256_after")
          == hashlib.sha256(digest_after).hexdigest()
          and read_bytes(db) == digest_after,
          json.dumps({k: payload.get(k) for k in
                      ("written", "db_sha256_before", "db_sha256_after")})[:250])

    # 成功恢复：整库回到备份内容 + 前后 sha + 已裁决冲突回到待裁决（不 reopen 单条）
    r = run_cli("restore", "--backup", backup_file,
                "--expect-db-sha256", hashlib.sha256(digest_after).hexdigest(),
                "--json", "--db", db)
    payload = parse_json(r)
    record = bibdb.Store.load(db).get("Alpha2020")
    pending_again = [c for c in record["conflicts"]
                     if bibdb.conflict_is_pending(c)]
    check("(e) 恢复成功：退出 0、written=True、库逐字节=备份内容",
          r.returncode == 0 and payload.get("written") is True
          and read_bytes(db) == digest_before, (r.stdout + r.stderr)[-250:])
    check("(e) 输出前后 sha / backup_path / records 正确",
          payload.get("db_sha256_before") == hashlib.sha256(digest_after).hexdigest()
          and payload.get("db_sha256_after") == hashlib.sha256(digest_before).hexdigest()
          and payload.get("backup_path") == backup_file
          and payload.get("records") == 3,
          json.dumps({k: payload.get(k) for k in
                      ("db_sha256_before", "db_sha256_after", "backup_path",
                       "records")}, ensure_ascii=False)[:300])
    check("(e) 不 reopen：已裁决冲突随整库恢复回到**待裁决**（裁决元数据消失）",
          len(pending_again) == 2
          and not any(c.get("resolved_at") for c in record["conflicts"]))
    r = run_cli("restore", "--backup", backup_file, "--json", "--db", db)
    check("(e) 库已与备份一致 → 退出 0、written=False（不重写）",
          r.returncode == 0 and parse_json(r).get("written") is False
          and read_bytes(db) == digest_before)
    r = run_cli("restore", "--backup", os.path.join(tmp, "nope.jsonl"), "--db", db)
    check("(e) 备份文件不存在 → 退出 2", r.returncode == 2
          and "找不到备份文件" in (r.stdout + r.stderr))
    r = run_cli("restore", "--db", db)
    check("(e) restore 缺 --backup → 用法错误退出 2", r.returncode == 2)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) 默认零变化：老动作/老命令的输出键与语义不变（新增键只加不改）──")

with tempfile.TemporaryDirectory(prefix="b73_zerochange_") as tmp:
    db = make_multi_db(os.path.join(tmp, "zero.jsonl"))
    target = conflict_with_field(db, "Beta2019", "publisher")["incoming_sha256"]
    r = run_cli("resolve", "--key", "Beta2019", "--action", "keep",
                "--incoming-sha256", target[:12], "--json", "--db", db)
    payload = parse_json(r)
    check("(f) 老 `resolve keep --json`：老键仍在（key/action/resolution/count/matched/"
          "changed/already/resolved_at/totals 等）",
          r.returncode == 0
          and {"db", "written", "key", "action", "resolution", "count", "matched",
               "changed", "already", "resolved_at", "totals"} <= set(payload),
          str(sorted(payload))[:300])
    check("(f) 按 Lead 批73 增补：写动作 --json 统一带 3 个新键（无备份时空串）",
          {"db_sha256_before", "db_sha256_after", "backup_path"} <= set(payload)
          and payload["backup_path"] == ""
          and payload["db_sha256_after"] == sha(db)
          and payload["db_sha256_before"] != payload["db_sha256_after"],
          json.dumps({k: payload.get(k) for k in
                      ("db_sha256_before", "db_sha256_after", "backup_path")})[:200])
    r = run_cli("resolve", "--key", "Delta2016", "--action", "defer",
                "--all-fields", "--db", db)
    check("(f) 老人读输出不变：keep/defer 仍打印「已写库」「当前：冲突」与提示行",
          r.returncode == 0 and "已写库" in r.stdout and "当前：冲突" in r.stdout
          and "重新生成清单不会丢" in r.stdout, r.stdout[-400:])
    r = run_cli("report-conflicts", "--db", db, "--out",
                os.path.join(tmp, "rep"), "--json")
    check("(f) `report-conflicts --json` 老键集不变（db/reports/conflicts/totals）",
          r.returncode == 0
          and set(parse_json(r)) == {"db", "reports", "conflicts", "totals"},
          str(sorted(parse_json(r))))
    r = run_cli("report", "--db", db, "--out", os.path.join(tmp, "rep2"), "--json")
    check("(f) `report --json` 老键集不变（+pairs）",
          r.returncode == 0
          and set(parse_json(r)) == {"db", "reports", "conflicts", "totals", "pairs"},
          str(sorted(parse_json(r))))
    # 干跑/幂等不产生备份目录：默认零变化的直接证据
    no_backup = os.path.join(tmp, "never_created")
    run_cli("resolve", "--key", "Delta2016", "--action", "keep", "--all-fields",
            "--dry-run", "--backup", no_backup, "--db", db)
    check("(f) dry-run 不创建备份目录（默认 CLI 用法零副作用）",
          not os.path.exists(no_backup))


# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) 备份文件名与「同名已存在拒绝覆盖」（spec §4.2 `--backup`）──")

import importlib.util  # noqa: E402

_CLI_SPEC = importlib.util.spec_from_file_location("bibdb_cli_b73", CLI)
cli_module = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(cli_module)

with tempfile.TemporaryDirectory(prefix="b73_backup_name_") as tmp:
    db = make_multi_db(os.path.join(tmp, "bk.jsonl"))
    digest = read_bytes(db)
    backup_dir = os.path.join(tmp, "bk")
    first = cli_module._write_db_backup(db, backup_dir, sha(db),
                                        stamp="20260101T000000Z")
    check("(g) 备份文件名 = `bibliography-<UTCts>-<库sha前12位>.jsonl`、内容=库字节",
          os.path.basename(first)
          == f"bibliography-20260101T000000Z-{sha(db)[:12]}.jsonl"
          and read_bytes(first) == digest, os.path.basename(first))
    raised = None
    try:
        cli_module._write_db_backup(db, backup_dir, sha(db),
                                    stamp="20260101T000000Z")
    except Exception as exc:  # noqa: BLE001 - 断言类型与消息
        raised = exc
    check("(g) 同名备份已存在 → 拒绝覆盖（BibDBError、目录仍只有 1 份）",
          isinstance(raised, bibdb.BibDBError) and "已存在" in str(raised)
          and len(os.listdir(backup_dir)) == 1, str(raised))


# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
