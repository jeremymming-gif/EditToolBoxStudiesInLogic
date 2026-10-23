#!/usr/bin/env python3.14
r"""B-204：重复告警忽略 sidecar（`health-ignore.json`）契约测试（hermetic：tempfile 造库）。

设计卡 `EditToolBox-notes/drafts/NEXT-SESSION-bibdb-4fix.md` §4 / 证据
`/tmp/b201/evidence/q4-dup-ack.md` 方案 A：库同目录的 `health-ignore.json` 记「人工裁决为
两条不同条目」，`lint` 据此不再提示（不改库、不动 content_sha256；`report-duplicates` 仍全量）。

  (a) `near_duplicates`：两个近重复 pair，ack 其一 → `counts.near_duplicates` 2→1、
      `counts.near_duplicates_acknowledged=1`（审计）、warning detail / keys_sample /
      records 只列未 ack pair；全部 ack → 该 warning 整条消失（`counts.warnings` 减 1）。
  (b) key 解析：先 canonical、再 alias（改名后旧 key 写的 ack 仍命中）；找不到 / alias 多指 /
      keys 重复 → 该条不生效（fail-soft，好条目照常生效）。
  (c) `duplicate_content`：组内 key 升序集合与 ack **完全一致**才整组抑制（3 条组必须列全
      3 个 key；部分 ack 不抑制）。
  (d) fail-soft：JSON 坏 / **非法 UTF-8** / version≠1 / 结构坏 / key 解析不了 → 不抛异常、
      lint **errors 仍 0**，
      只多一条聚合 warning（code=`health_ignore`、count=问题数）；未 ack 告警照常输出。
  (e) 只读 + 全量审计：lint 前后库文件逐字节相同；缺 sidecar 时口径与旧版一致；
      `report-duplicates` 不读 sidecar（仍报全部 pair）。
  (f) CLI：`lint --json` rc0、`warning_items` 带新计数键 / `health_ignore` 项。

跑法（本机必须 Python 3.14 framework + PYTHONPATH=vendor）：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b204_health_ignore.py
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

CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep + str(_ENGINE / "vendor"),
           PYTHONIOENCODING="utf-8")

pass_n = 0
fails: list[str] = []


def check(name: str, cond, detail: str = "") -> bool:
    global pass_n
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if cond:
        pass_n += 1
    else:
        fails.append(name)
    return bool(cond)


def source() -> dict:
    return {"kind": bibdb.KIND_PUBLISHED, "issue": "2023年第1期(68)",
            "slug": "01 Fixture", "file": "fixture.bib"}


def record(key: str, *, entry_type: str = "article", fields: dict,
           aliases: "list[str] | None" = None) -> dict:
    """按 `validate_record` 要求造一条合格记录（verified=true + 正确 content_sha256）。"""
    fields = dict(sorted(fields.items()))
    return {
        "key": key, "version": 1, "entry_type": entry_type, "fields": fields,
        "verified": True, "verified_at": "2026-10-18T00:00:00Z", "method": "published-bib",
        "content_sha256": bibdb.content_sha256(entry_type, fields),
        "sources": [dict(source(), key=key, entry_sha256="0" * 64)],
        "aliases": list(aliases or []), "conflicts": [],
    }


def write_db(path: str, records: "list[dict]") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    return path


def digest(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def sidecar_path(db: str) -> str:
    return bibdb.health_ignore_path(db)


def write_sidecar(db: str, payload) -> str:
    """写 sidecar（payload 为 str 时原样写，用于坏 JSON 用例）。"""
    path = sidecar_path(db)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return path


def remove_sidecar(db: str) -> None:
    path = sidecar_path(db)
    if os.path.isfile(path):
        os.remove(path)


def ack(code: str, keys: "list[str]", reason: str = "人工裁决：两条不同条目") -> dict:
    return {"code": code, "keys": keys, "reason": reason,
            "decided_at": "2026-10-23T12:00:00+08:00"}


def base_records() -> "list[dict]":
    return [
        # 近重复 pair 1：Segerberg 书版 vs 博士论文（题名归一同 = 1.0；entry_type 不参与判定）
        record("Segerberg1971b", entry_type="book", fields={
            "author": "Segerberg, Karl Krister",
            "title": "An essay in classical modal logic",
            "publisher": "Uppsala, Filosofiska Föreningen", "address": "Uppsala",
            "year": "1971"}),
        record("SegerbergPHD1971", entry_type="phdthesis", fields={
            "author": "Segerberg, K.",
            "title": "An Essay in Classical Modal Logic",
            "school": "Uppsala: Filosofiska Studier", "year": "1971"},
               aliases=["Segerberg1971"]),
        # 近重复 pair 2：同作者/同年/题名 0.95（未 ack 时应继续告警）
        record("MiaoA2015", fields={
            "author": "Miao, Litian", "title": "On the logical structure of Chinese philosophy",
            "journal": "Journal of Chinese Philosophy", "year": "2015"}),
        record("MiaoB2015", fields={
            "author": "Miao, Litian",
            "title": "On the logical structure of Chinese philosophy (revised)",
            "journal": "Journal of Chinese Philosophy", "year": "2015"}),
        # 内容完全相同的 2 条组（duplicate_content；sha 相同 → near_duplicates 跳过）
        record("DuplicateA", fields={"author": "Same, Sam", "title": "Same Work", "year": "2001"}),
        record("DuplicateB", fields={"author": "Same, Sam", "title": "Same Work", "year": "2001"}),
        # 内容完全相同的 3 条组（部分 ack 不抑制；列全 3 个 key 才抑制）
        record("TripleA", fields={"author": "Triple, Tim", "title": "Triple Work", "year": "1999"}),
        record("TripleB", fields={"author": "Triple, Tim", "title": "Triple Work", "year": "1999"}),
        record("TripleC", fields={"author": "Triple, Tim", "title": "Triple Work", "year": "1999"}),
    ]


def near_item(result: dict) -> dict:
    return next((item for item in result.get("warning_items") or []
                 if item["code"] == "near_duplicates"), {})


def dup_items(result: dict) -> "list[dict]":
    return [item for item in result.get("warning_items") or []
            if item["code"] == "duplicate_content"]


def health_item(result: dict) -> dict:
    return next((item for item in result.get("warning_items") or []
                 if item["code"] == "health_ignore"), {})


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def parse_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


TMP = tempfile.mkdtemp(prefix="b204_health_ignore_")

# ══════════════════════════════════════════════════════════════════════════
# (f) 基线：无 sidecar 时口径与旧版一致（near=2 / warnings=3 / 新计数键=0）
# ══════════════════════════════════════════════════════════════════════════
db = write_db(os.path.join(TMP, "base", "bibliography.jsonl"), base_records())
baseline_sha = digest(db)
store = bibdb.Store.load(db)
base = store.lint()
base_counts = base["counts"]
check("(f) 无 sidecar：errors 0 / near_duplicates=2 对 / warnings=3 条",
      base["ok"] and not base["errors"] and base_counts["near_duplicates"] == 2
      and base_counts["warnings"] == 3, base_counts)
check("(f) 无 sidecar：counts.near_duplicates_acknowledged=0（新键，老口径不变）",
      base_counts.get("near_duplicates_acknowledged") == 0)
check("(f) 无 sidecar：warning_items 里没有 health_ignore 项", not health_item(base))
check("(f) 无 sidecar：duplicate_content 2 组（2 条组 + 3 条组）",
      len(dup_items(base)) == 2, [item["keys_sample"] for item in dup_items(base)])
check("(f) 无 sidecar：lint 只读（库逐字节不变）", digest(db) == baseline_sha)

# ══════════════════════════════════════════════════════════════════════════
# (a) near_duplicates：ack 一个 pair → 2→1，detail/keys_sample/records 只列未 ack
# ══════════════════════════════════════════════════════════════════════════
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971b", "SegerbergPHD1971"],
        "两个不同条目：Uppsala 博士论文 vs Stanford 书版")]})
one = bibdb.Store.load(db).lint()
one_counts = one["counts"]
check("(a) ack 1 对：near_duplicates 2→1 / acknowledged=1 / warnings 仍 3",
      one_counts["near_duplicates"] == 1
      and one_counts["near_duplicates_acknowledged"] == 1
      and one_counts["warnings"] == 3, one_counts)
near_text = near_item(one).get("detail", "")
check("(a) near warning detail 只列未 ack pair（含 Miao，不含 Segerberg）",
      "MiaoA2015≈MiaoB2015" in near_text and "Segerberg1971b" not in near_text,
      near_text)
check("(a) keys_sample 只列未 ack pair",
      near_item(one).get("keys_sample") == ["MiaoA2015≈MiaoB2015"],
      near_item(one).get("keys_sample"))
with_rec = run_cli("lint", "--db", db, "--json", "--with-records")
d = parse_json(with_rec)
check("(a) CLI --with-records：near_duplicates.records 也只列未 ack pair",
      with_rec.returncode == 0
      and near_item(d).get("records") == ["MiaoA2015≈MiaoB2015"],
      near_item(d).get("records"))
check("(a) 被 ack 的 pair 不影响 duplicate_content 两组照常报",
      len(dup_items(one)) == 2)
check("(a) lint 只读（ack 生效时库仍逐字节不变）", digest(db) == baseline_sha)

# 全部 ack → 该 warning 整条消失（warnings 3→2）
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971b", "SegerbergPHD1971"]),
    ack("near_duplicates", ["MiaoA2015", "MiaoB2015"])]})
all_near = bibdb.Store.load(db).lint()
check("(a) 两对全 ack：near warning 整条消失 / warnings 3→2 / near=0 / acked=2",
      all_near["counts"]["near_duplicates"] == 0
      and all_near["counts"]["near_duplicates_acknowledged"] == 2
      and all_near["counts"]["warnings"] == 2
      and not near_item(all_near)
      and not any("同一作品的多条记录" in text for text in all_near["warnings"]),
      all_near["counts"])

# ══════════════════════════════════════════════════════════════════════════
# (b) key 解析：alias 写的 ack 仍命中（改名场景）；坏 ack 不生效但 fail-soft
# ══════════════════════════════════════════════════════════════════════════
# `Segerberg1971` 是 SegerbergPHD1971 的 alias（改名后 ack 仍应命中）
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971", "Segerberg1971b"])]})
alias_hit = bibdb.Store.load(db).lint()
check("(b) ack 写的是 alias（旧 key）→ 仍命中：near=1 / acked=1",
      alias_hit["counts"]["near_duplicates"] == 1
      and alias_hit["counts"]["near_duplicates_acknowledged"] == 1,
      alias_hit["counts"])
check("(b) alias 命中的 ack 不产生 health_ignore warning", not health_item(alias_hit))

# ack 之后把这两条合并了：两个 key 现在指向同一条记录 → ack 自然失效、但不算文件坏
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971", "SegerbergPHD1971"])]})
stale = bibdb.Store.load(db).lint()
check("(b) 两条 key 现指向同一记录（ack 后已合并）→ 不生效、但不算 sidecar 问题",
      stale["ok"] and not stale["errors"]
      and stale["counts"]["near_duplicates"] == 2
      and stale["counts"]["near_duplicates_acknowledged"] == 0
      and not health_item(stale), health_item(stale))

# near_duplicates 给 3 个 key → 结构问题（pair 只能是 2 条）
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971b", "SegerbergPHD1971", "MiaoA2015"])]})
three_keys = bibdb.Store.load(db).lint()
check("(b) near_duplicates 给 3 个 key → 结构问题（count=1，near 仍 2 对）",
      three_keys["ok"] and not three_keys["errors"]
      and health_item(three_keys).get("count") == 1
      and three_keys["counts"]["near_duplicates"] == 2,
      health_item(three_keys).get("detail"))

# ══════════════════════════════════════════════════════════════════════════
# (c) duplicate_content：整组集合相等才抑制；3 条组部分 ack 不抑制
# ══════════════════════════════════════════════════════════════════════════
remove_sidecar(db)
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("duplicate_content", ["DuplicateA", "DuplicateB"])]})
dup2 = bibdb.Store.load(db).lint()
check("(c) 2 条组整组 ack → 该 duplicate_content warning 消失（只剩 3 条组）",
      len(dup_items(dup2)) == 1 and dup_items(dup2)[0]["keys_sample"]
      and "TripleA" in dup_items(dup2)[0]["keys_sample"], dup_items(dup2))
check("(c) 组 ack 不影响 near_duplicates（仍 2 对）",
      dup2["counts"]["near_duplicates"] == 2)

write_sidecar(db, {"version": 1, "acknowledged": [
    ack("duplicate_content", ["TripleA", "TripleB"])]})
partial = bibdb.Store.load(db).lint()
check("(c) 3 条组只 ack 2 个 key（部分）→ 不抑制、两组照旧告警",
      len(dup_items(partial)) == 2 and partial["counts"]["warnings"] == 3,
      [item["keys_sample"] for item in dup_items(partial)])
check("(c) 部分 ack 不算 sidecar 问题（key 都能解析）", not health_item(partial))

write_sidecar(db, {"version": 1, "acknowledged": [
    ack("duplicate_content", ["TripleA", "TripleB", "TripleC"])]})
full_group = bibdb.Store.load(db).lint()
check("(c) 3 条组列全 3 个 key → 整组抑制（只剩 2 条组）",
      len(dup_items(full_group)) == 1
      and "DuplicateA" in dup_items(full_group)[0]["keys_sample"], dup_items(full_group))

# ══════════════════════════════════════════════════════════════════════════
# (d) fail-soft：坏 JSON / version≠1 / 结构坏 / key 解析不了 → errors 0 + 1 条聚合 warning
# ══════════════════════════════════════════════════════════════════════════
write_sidecar(db, "{ this is not json")
bad_json = bibdb.Store.load(db).lint()
check("(d) 坏 JSON：不抛异常、errors 仍 0、ok=true", bad_json["ok"] and not bad_json["errors"])
check("(d) 坏 JSON：warnings 3→4（只多一条聚合 warning）",
      bad_json["counts"]["warnings"] == 4, bad_json["counts"]["warnings"])
hb = health_item(bad_json)
check("(d) 坏 JSON：warning_items 有 code=health_ignore / registry=other / count=1",
      hb.get("code") == "health_ignore" and hb.get("registry") == "other"
      and hb.get("count") == 1 and hb.get("action"), hb)
check("(d) 坏 JSON：未 ack 告警照常输出（near=2、duplicate 2 组、字符串含坏文件说明）",
      bad_json["counts"]["near_duplicates"] == 2 and len(dup_items(bad_json)) == 2
      and any("UTF-8" in text or "JSON" in text for text in bad_json["warnings"]))

# t5（verifier t3 发现）：非法 UTF-8 字节不是「坏 JSON」而是**解码阶段**就抛
# UnicodeDecodeError（ValueError 子类）——同样必须 fail-soft，不能漏成 traceback / rc1。
with open(sidecar_path(db), "wb") as raw_handle:
    raw_handle.write(b'{"version":1,"acknowledged":\xff}')
utf8 = run_cli("lint", "--db", db, "--json", "--with-records")
utf8_payload = parse_json(utf8)
check("(d) 非法 UTF-8 sidecar：lint --json 不崩、rc0、errors 0、无 traceback",
      utf8.returncode == 0 and utf8_payload.get("ok") is True
      and not utf8_payload.get("errors") and "Traceback" not in utf8.stderr,
      (utf8.stderr or utf8.stdout)[-200:])
check("(d) 非法 UTF-8 sidecar：warnings 3→4（仅 +1）/ health_ignore 项 count≥1",
      utf8_payload.get("counts", {}).get("warnings") == 4
      and health_item(utf8_payload).get("count", 0) >= 1,
      health_item(utf8_payload))
check("(d) 非法 UTF-8 sidecar：未 ack 的 near/duplicate 告警照常 + 库字节 sha 不变",
      utf8_payload.get("counts", {}).get("near_duplicates") == 2
      and len(dup_items(utf8_payload)) == 2 and digest(db) == baseline_sha)

write_sidecar(db, {"version": 2, "acknowledged": [
    ack("near_duplicates", ["MiaoA2015", "MiaoB2015"])]})
bad_version = bibdb.Store.load(db).lint()
check("(d) version≠1：errors 0、ack 不生效（near 仍 2）、health_ignore warning 出现",
      bad_version["ok"] and not bad_version["errors"]
      and bad_version["counts"]["near_duplicates"] == 2
      and bad_version["counts"]["near_duplicates_acknowledged"] == 0
      and health_item(bad_version).get("count") == 1,
      health_item(bad_version).get("detail"))

write_sidecar(db, {"version": 1, "acknowledged": [
    {"code": "missing_field", "keys": ["MiaoA2015", "MiaoB2015"]},
    {"code": "near_duplicates", "keys": ["MiaoA2015"]},
    ack("near_duplicates", ["MissingX", "MiaoA2015"]),
    ack("near_duplicates", ["MiaoA2015", "MiaoA2015"]),
    ack("near_duplicates", ["MiaoA2015", "MiaoB2015"])]})
mixed = bibdb.Store.load(db).lint()
check("(d) 结构坏 4 条 + 好条目 1 条：errors 0、问题数 4、好 ack 照常生效（near 2→1）",
      mixed["ok"] and not mixed["errors"]
      and health_item(mixed).get("count") == 4
      and mixed["counts"]["near_duplicates"] == 1
      and mixed["counts"]["near_duplicates_acknowledged"] == 1,
      health_item(mixed).get("detail"))
check("(d) 问题摘要列出具体原因（不支持的 code / key 不在库里 / keys 重复）",
      any(word in health_item(mixed).get("detail", "")
          for word in ("不受支持", "不在库里", "重复项")),
      health_item(mixed).get("detail"))
check("(d) 坏 sidecar 不写库：库逐字节不变", digest(db) == baseline_sha)

# alias 多指 → 该 ack 不生效（fail-soft，另造一个库）
ambiguous_db = write_db(os.path.join(TMP, "ambiguous", "bibliography.jsonl"), [
    record("Alpha2020", fields={"author": "Alpha, Ann", "title": "Alpha Work", "year": "2020"},
           aliases=["shared"]),
    record("Beta2020", fields={"author": "Beta, Bob", "title": "Beta Work", "year": "2020"},
           aliases=["shared"]),
])
ambiguous_before = digest(ambiguous_db)
write_sidecar(ambiguous_db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["shared", "Alpha2020"])]})
ambiguous = bibdb.Store.load(ambiguous_db).lint()
check("(d) alias 多指：该 ack 不生效 + 1 条 health_ignore warning（errors 仍 0）",
      ambiguous["ok"] and not ambiguous["errors"]
      and health_item(ambiguous).get("count") == 1
      and "多指" in health_item(ambiguous).get("detail", ""),
      health_item(ambiguous).get("detail"))
check("(d) alias 多指：lint 只读（库逐字节不变）", digest(ambiguous_db) == ambiguous_before)

# ══════════════════════════════════════════════════════════════════════════
# (e) report-duplicates 仍全量审计（不读 sidecar）；CLI --json 契约
# ══════════════════════════════════════════════════════════════════════════
remove_sidecar(db)
write_sidecar(db, {"version": 1, "acknowledged": [
    ack("near_duplicates", ["Segerberg1971b", "SegerbergPHD1971"]),
    ack("near_duplicates", ["MiaoA2015", "MiaoB2015"])]})
out_dir = os.path.join(TMP, "reports")
report = run_cli("report-duplicates", "--db", db, "--out", out_dir, "--json")
report_payload = parse_json(report)
check("(e) report-duplicates rc0 且仍报全量 2 对（sidecar 只影响 lint）",
      report.returncode == 0 and report_payload.get("pairs") == 2,
      report_payload or report.stderr[-200:])
check("(e) report-duplicates 不改库、报告里仍含被 ack 的两对",
      digest(db) == baseline_sha
      and "Segerberg1971b" in pathlib.Path(out_dir, "DUPLICATES.md").read_text(encoding="utf-8")
      and "MiaoA2015" in pathlib.Path(out_dir, "DUPLICATES.md").read_text(encoding="utf-8"))

lint_cli = run_cli("lint", "--db", db, "--json", "--with-records")
lint_payload = parse_json(lint_cli)
check("(e) CLI lint --json rc0 / 老键全在 / 新键 near_duplicates_acknowledged=2",
      lint_cli.returncode == 0
      and {"ok", "errors", "warnings", "counts"} <= set(lint_payload)
      and lint_payload.get("counts", {}).get("near_duplicates_acknowledged") == 2,
      lint_payload.get("counts"))
check("(e) CLI counts.warnings 与 warnings 条数一致（acK 后 2 条）",
      lint_payload["counts"]["warnings"] == len(lint_payload["warnings"]) == 2,
      lint_payload.get("counts", {}).get("warnings"))
human = run_cli("lint", "--db", db)
check("(e) CLI 人读输出：near 0 对 + 审计提示「已确认不同」",
      human.returncode == 0 and "同一作品多条记录 0 对" in human.stdout
      and "已确认不同" in human.stdout, human.stdout.strip().splitlines()[-2:])

# ══════════════════════════════════════════════════════════════════════════
# 收尾：全程只读（base db 从测前到测后逐字节相同）
# ══════════════════════════════════════════════════════════════════════════
check("(e) 全程 lint/report 未改库：库字节 sha 与测前一致", digest(db) == baseline_sha)

print()
print("=" * 78)
print(f"通过 {pass_n} 项，失败 {len(fails)} 项" + (f"：{fails}" if fails else ""))
sys.exit(1 if fails else 0)
