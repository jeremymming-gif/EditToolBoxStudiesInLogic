"""P07 引用库资产（R01 采录来源与 CAS / R06 健康与核实 / R07 旧资产升级与回退）。

纯本地、无网络、可重复：所有库都建在临时目录里，CLI 一律显式 `--db`，
绝不触碰真实笔记仓/真实库/真实稿件仓。断言全部是磁盘级（库字节 sha、
mtime/大小、目录文件集、sidecar 内容、逐记录内容 sha 账），不依赖人读文案
之外的产品实现细节；人读/stderr 只做「有可执行指路」的存在性断言。

断言组：
  R01 (a) 新库 dry-run 零写（库不创建、目录无新文件）
      (b) 已有库 dry-run 零写（sha/mtime/大小不变；--backup 目录不创建）
      (c) --expect-db-sha256 不符 → rc2 零写、备份目录不创建
      (d) 题名/DOI 与已核实条目相同也不伪造 verified（新记录未核实）
      (e) 已核实条目遇同作品不同内容 → 字段保留、冲突入 conflicts（带前后 sha+来源）
  R06 (f) lint --json 可追溯（counts.conflicts_pending / warning_items 字段）+ 只读
      (g) ack-duplicates 写 sidecar 不改库、lint 提示消失
      (h) 坏 sidecar fail-soft（lint errors 仍 0、出 health_ignore warning、不改文件）
      (i) rehash --apply 只改 content_sha256+updated_at（verified/来源/alias 不动）
      (j) edit 已核实记录不碰 verified/verified_at/method；未核实记录仍 false
      (k) merge-records 保留 verified 证据、来源并集、旧 key 进 aliases
      (l) restore 用备份副本字节级恢复；expect-db-sha256 不符 rc2 零写
      (m) 核实预算闸零请求（纯函数超限判定 + 只读指引）
  R07 (n) 旧格式缺 content_sha256：读命令拒绝并指路 rehash；rehash 迁移逐记录只改 sha+updated_at
      (o) 记录 version 高于引擎支持：lint 报出、所有写路径 rc2 零写
      (p) 更旧格式缺 sources：拒绝破坏性写入（rehash/edit rc2 零写）
      (q) merge 不静默丢被并记录的已裁决 conflicts（CONTRACT §4）

跑法（cwd = Engine）：
  PYTHONPATH=.:vendor <venv>/python tests/test_pkg_p07_bib_asset.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import bibdb  # noqa: E402

CLI = str(ENGINE / "bibdb.py")
ENV = dict(os.environ)
ENV["PYTHONPATH"] = str(ENGINE) + (
    os.pathsep + ENV["PYTHONPATH"] if ENV.get("PYTHONPATH") else "")
ENV["PYTHONIOENCODING"] = "utf-8"

NOW = "2026-01-01T00:00:00+08:00"
pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}")
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], cwd=str(ENGINE), env=ENV,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


def payload(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except Exception:
        return {}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def facts(path: Path) -> tuple:
    if not path.is_file():
        return ("missing",)
    stat = path.stat()
    return (sha256_file(path), stat.st_size, stat.st_mtime_ns)


def dir_entries(path: Path) -> list[str]:
    return sorted(child.name for child in path.iterdir()) if path.is_dir() else []


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def keyed(path: Path) -> dict[str, dict]:
    return {str(record.get("key")): record for record in records(path)}


def write_records(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")) + "\n" for record in rows),
                    encoding="utf-8")
    return path


def record(key: str, entry_type: str, fields: dict, *, verified: bool,
           method: str = "", source_file: str = "seed.bib",
           source_kind: str = "published-bib", issue: str | None = None,
           slug: str | None = None, aliases: list[str] | None = None) -> dict:
    sha = bibdb.content_sha256(entry_type, fields)
    return {
        "version": 1, "key": key, "entry_type": entry_type, "fields": fields,
        "verified": verified, "verified_at": NOW if verified else None,
        "method": method if verified else None, "content_sha256": sha,
        "aliases": list(aliases or []),
        "sources": [{"kind": source_kind, "issue": issue, "slug": slug,
                     "file": source_file, "key": key,
                     "entry_sha256": hashlib.sha256(key.encode()).hexdigest(),
                     "imported_at": NOW}],
        "conflicts": [], "created_at": NOW, "updated_at": NOW,
    }


def base_records() -> list[dict]:
    alpha = record("Alpha2020", "article", {
        "author": "Smith, John", "title": "Alpha Study of Things",
        "journal": "Journal of Examples", "year": "2020", "doi": "10.1000/alpha.2020",
    }, verified=True, method="published-bib", issue="2020年第1期", slug="alpha")
    beta = record("Beta2019", "book", {
        "author": "Doe, Jane", "title": "Beta Book of Methods",
        "publisher": "Example Press", "year": "2019",
    }, verified=True, method="published-bib", issue="2019年第2期", slug="beta")
    beta_copy = record("BetaCopy2019", "book", dict(beta["fields"]), verified=True,
                       method="published-bib", issue="2019年第2期", slug="beta")
    gamma = record("Gamma2018", "article", {
        "author": "Roe, Richard", "title": "Gamma Note on Facts",
        "journal": "Journal of Notes", "year": "2018",
    }, verified=False, source_kind="imported", source_file="loose.bib")
    return [alpha, beta, beta_copy, gamma]


def make_base_db(folder: Path) -> Path:
    return write_records(folder / "bibliography.jsonl", base_records())


def full_fields(db: Path, key: str, **changes: str) -> str:
    fields = dict(keyed(db)[key].get("fields") or {})
    fields.update(changes)
    return json.dumps(fields, ensure_ascii=False)


def normalized(record_: dict) -> dict:
    """旧格式缺字段等价归一（缺 aliases/conflicts/sources/edits = 空列表）。"""
    body = dict(record_)
    for field in ("aliases", "conflicts", "sources", "edits"):
        body[field] = body.get(field) or []
    return body


def _evidence(record_: dict) -> dict:
    """核实/来源/冲突证据（edit/merge 等维护动作不得改动的字段）。"""
    body = normalized(record_)
    return {field: body[field] for field in
            ("verified", "verified_at", "method", "sources", "conflicts")}


def content_only(record_: dict) -> dict:
    """逐记录证据账：除 content_sha256/updated_at 外的全部字段（含来源/冲突/核实）。"""
    body = normalized(record_)
    return {key: value for key, value in body.items()
            if key not in ("content_sha256", "updated_at")}


# ── R01 采录来源与 CAS ──────────────────────────────────────────────────────


def case_r01_cas(work: Path) -> Path:
    seed = work / "db"
    seed.mkdir()
    db = make_base_db(seed)

    # (a) 新库 dry-run：库不创建、目录无新文件
    fresh = work / "fresh"
    fresh.mkdir()
    bib = work / "delta.bib"
    bib.write_text("@article{Delta2023,\n  author = {Lee, Ann},\n  title = {Delta},"
                   "\n  year = {2023}\n}\n", encoding="utf-8")
    result = run_cli("import-bib", "--db", str(fresh / "bibliography.jsonl"),
                     "--bib", str(bib), "--dry-run", "--json")
    body = payload(result)
    check("R01-a 新库 dry-run：rc0、dry_run=true、written=false",
          result.returncode == 0 and body.get("dry_run") is True
          and body.get("written") is False, result.stderr[:300])
    check("R01-a 新库 dry-run：库不被创建、目录零新文件",
          not (fresh / "bibliography.jsonl").exists() and dir_entries(fresh) == [],
          str(dir_entries(fresh)))

    # (b) 已有库 dry-run：sha/mtime/大小不变；--backup 目录不创建
    before = facts(db)
    seed_entries = dir_entries(seed)
    result = run_cli("import-bib", "--db", str(db), "--bib", str(bib),
                     "--dry-run", "--json")
    check("R01-b 已有库 dry-run：rc0、written=false、库 sha/mtime/大小全不变",
          result.returncode == 0 and payload(result).get("written") is False
          and facts(db) == before, result.stderr[:300])
    check("R01-b 已有库 dry-run：库目录文件集不变（无备份/临时文件副作用）",
          dir_entries(seed) == seed_entries, str(dir_entries(seed)))

    # (c) --expect-db-sha256 不符：rc2 零写、目录无备份副作用
    result = run_cli("import-bib", "--db", str(db), "--bib", str(bib),
                     "--expect-db-sha256", "0" * 64, "--json")
    check("R01-c expect-db-sha256 不符：rc2 + stderr 指出 sha 闸、库零写、无备份",
          result.returncode == 2 and "--expect-db-sha256" in result.stderr
          and facts(db) == before and dir_entries(seed) == seed_entries,
          result.stderr[:300])

    # (d) 题名/DOI 与已核实条目相同也不伪造 verified
    forge = work / "forge.bib"
    forge.write_text("@article{AlphaNewKey2022,\n  author = {Smith, John},\n"
                     "  title = {Alpha Study of Things},\n  journal = {Journal of Examples},\n"
                     "  year = {2022},\n  doi = {10.1000/alpha.2020}\n}\n",
                     encoding="utf-8")
    keys_before = set(keyed(db))
    result = run_cli("import-bib", "--db", str(db), "--bib", str(forge), "--json")
    rows = keyed(db)
    new_keys = set(rows) - keys_before
    new_record = rows.get(next(iter(new_keys))) if new_keys else {}
    check("R01-d 题名/DOI 相同但内容不同、key 不同：新增记录而非并源",
          result.returncode == 0 and len(new_keys) == 1, result.stderr[:300])
    check("R01-d 新记录不伪造 verified（verified=false 且无 verified_at/method）",
          new_record.get("verified") is False
          and not str(new_record.get("verified_at") or "").strip()
          and not str(new_record.get("method") or "").strip(), str(new_record)[:300])
    check("R01-d 库内已核实 Alpha2020 的字段与 verified 未被改动",
          rows["Alpha2020"].get("verified") is True
          and rows["Alpha2020"].get("method") == "published-bib"
          and (rows["Alpha2020"].get("fields") or {}).get("year") == "2020",
          str(rows["Alpha2020"].get("fields"))[:200])

    # (e) 已核实条目遇同作品不同内容：字段保留、冲突入 conflicts（前后 sha + 来源）
    conflict_bib = work / "alpha-both.bib"
    conflict_bib.write_text("@article{Alpha2020,\n  author = {Smith, John},\n"
                            "  title = {Alpha Study of Things},\n"
                            "  journal = {Journal of Counterexamples},\n  year = {2020},\n"
                            "  doi = {10.1000/alpha.2020}\n}\n", encoding="utf-8")
    alpha_before = keyed(db)["Alpha2020"]
    result = run_cli("import-bib", "--db", str(db), "--bib", str(conflict_bib),
                     "--issue", "2020年第1期", "--slug", "alpha", "--json")
    alpha = keyed(db)["Alpha2020"]
    conflicts = alpha.get("conflicts") or []
    check("R01-e 已核实条目遇不同内容：rc0、字段保留（journal 不被覆盖）",
          result.returncode == 0
          and (alpha.get("fields") or {}).get("journal") == "Journal of Examples",
          result.stderr[:300])
    check("R01-e 冲突入 conflicts：带 existing_sha256/incoming_sha256/source 且待裁决",
          len(conflicts) == 1
          and all(conflicts[0].get(name) for name in
                  ("existing_sha256", "incoming_sha256", "source"))
          and not str(conflicts[0].get("resolved_at") or "").strip(),
          json.dumps(conflicts, ensure_ascii=False)[:400])
    check("R01-e verified/verified_at/method 不因冲突被重标",
          alpha.get("verified") is True
          and alpha.get("verified_at") == alpha_before.get("verified_at")
          and alpha.get("method") == "published-bib", str(alpha)[:300])
    return db


# ── R06 健康与核实 ─────────────────────────────────────────────────────────


def case_r06_health(db: Path) -> None:
    db_dir = db.parent
    # (f) lint --json 可追溯 + 只读
    before = facts(db)
    result = run_cli("lint", "--db", str(db), "--json")
    body = payload(result)
    items = body.get("warning_items") or []
    conflict_item = next((item for item in items
                          if item.get("code") == "conflict_pending"), {})
    check("R06-f lint --json：rc0、顶层键齐全、counts.conflicts_pending>=1",
          result.returncode == 0
          and {"counts", "errors", "warning_items", "warnings", "ok"} <= set(body)
          and isinstance((body.get("counts") or {}).get("conflicts_pending"), dict)
          and (body["counts"]["conflicts_pending"].get("records") or 0) >= 1,
          stale := (result.stderr[:300]))
    check("R06-f lint warning_items 可追溯（code/registry/detail/action/count/keys_sample）",
          {"code", "registry", "detail", "action", "count", "keys_sample"}
          <= set(conflict_item), json.dumps(conflict_item, ensure_ascii=False)[:300])
    check("R06-f lint 只读：库字节 sha/大小/mtime 不变",
          facts(db) == before, str(facts(db)))
    result = run_cli("lint", "--db", str(db), "--with-records", "--json")
    keys = (payload(result).get("counts") or {}).get("records")
    check("R06-f lint --with-records：rc0 且记录数可查",
          result.returncode == 0 and keys == len(records(db)), result.stderr[:300])

    # (g) ack-duplicates：写 sidecar 不改库；lint 提示消失
    sidecar = db_dir / "health-ignore.json"
    before = facts(db)
    result = run_cli("ack-duplicates", "--db", str(db), "--code", "duplicate_content",
                     "--keys", "Beta2019,BetaCopy2019", "--apply", "--json")
    side_body = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    check("R06-g ack --apply：rc0、written=true、写库同目录 sidecar（version 1）",
          result.returncode == 0 and payload(result).get("written") is True
          and side_body.get("version") == 1, result.stderr[:300])
    check("R06-g ack 不改库字节；sidecar 条目含 code/keys/decided_at",
          facts(db) == before
          and any(entry.get("code") == "duplicate_content"
                  and sorted(entry.get("keys") or []) == ["Beta2019", "BetaCopy2019"]
                  and entry.get("decided_at")
                  for entry in side_body.get("acknowledged") or []), str(side_body)[:300])
    result = run_cli("lint", "--db", str(db), "--json")
    check("R06-g ack 后 lint 不再提示 duplicate_content（rc0、库仍不变）",
          result.returncode == 0 and facts(db) == before
          and not any(item.get("code") == "duplicate_content"
                      for item in payload(result).get("warning_items") or []),
          result.stderr[:300])

    # (h) 坏 sidecar fail-soft：lint errors 仍 0、出 health_ignore warning、不改文件
    sidecar.write_text("{not json", encoding="utf-8")
    broken_facts = facts(sidecar)
    before = facts(db)
    result = run_cli("lint", "--db", str(db), "--json")
    body = payload(result)
    check("R06-h 坏 sidecar：lint rc0、errors=0、出 code=health_ignore warning",
          result.returncode == 0 and not body.get("errors")
          and any(item.get("code") == "health_ignore"
                  for item in body.get("warning_items") or []),
          json.dumps(body.get("errors"), ensure_ascii=False)[:200])
    check("R06-h 坏 sidecar：lint 不修不写（sidecar 与库字节都不变）",
          facts(sidecar) == broken_facts and facts(db) == before,
          f"sidecar={facts(sidecar)} db={facts(db)}")
    sidecar.write_text(json.dumps({"version": 1, "acknowledged": []}),
                       encoding="utf-8")

    # (i) rehash --apply 只改 content_sha256+updated_at
    rehash_dir = db.parent.parent / "rehash"
    rehash_dir.mkdir(exist_ok=True)
    stale = base_records()
    stale[0]["content_sha256"] = "0" * 64
    rehash_db = write_records(rehash_dir / "bibliography.jsonl", stale)
    before_rows = keyed(rehash_db)
    result = run_cli("rehash", "--db", str(rehash_db), "--apply", "--json")
    after_rows = keyed(rehash_db)
    deltas = {key: {field for field in set(before_rows[key]) | set(after_rows[key])
                    if before_rows[key].get(field) != after_rows[key].get(field)}
              for key in before_rows}
    check("R06-i rehash --apply：rc0、applied=true、stale=1",
          result.returncode == 0 and payload(result).get("applied") is True
          and payload(result).get("stale") == 1, result.stderr[:300])
    check("R06-i rehash 只改 content_sha256+updated_at（verified/来源/alias/冲突逐条不动）",
          all(delta <= {"content_sha256", "updated_at"} for delta in deltas.values())
          and content_only(after_rows["Alpha2020"]) == content_only(before_rows["Alpha2020"]),
          str({key: sorted(delta) for key, delta in deltas.items() if delta})[:400])
    check("R06-i rehash 后 sha 与独立复算一致、verified 证据保留",
          after_rows["Alpha2020"]["content_sha256"]
          == bibdb.content_sha256("article", after_rows["Alpha2020"]["fields"])
          and after_rows["Alpha2020"]["verified"] is True
          and after_rows["Alpha2020"]["method"] == "published-bib",
          after_rows["Alpha2020"]["content_sha256"])

    # (j) edit 已核实记录：不碰 verified/verified_at/method/sources/conflicts
    beta_before = keyed(db)["Beta2019"]
    result = run_cli("edit", "--db", str(db), "--key", "Beta2019", "--apply",
                     "--json-fields", full_fields(db, "Beta2019",
                                                  title="Beta Book of Methods (rev)"),
                     "--json")
    beta = keyed(db)["Beta2019"]
    check("R06-j edit 已核实记录：rc0、只改声明字段（title）",
          result.returncode == 0
          and (beta.get("fields") or {}).get("title") == "Beta Book of Methods (rev)",
          result.stderr[:300])
    check("R06-j edit 不换 verified/verified_at/method/来源/冲突",
          beta.get("verified") is True
          and beta.get("verified_at") == beta_before.get("verified_at")
          and beta.get("method") == beta_before.get("method")
          and _evidence(beta) == _evidence(beta_before),
          json.dumps({"verified": beta.get("verified"),
                      "method": beta.get("method")}, ensure_ascii=False))

    # (k) merge-records：verified 证据/来源并集/旧 key→aliases/冲突并入
    merged_rows = keyed(db)
    merged_rows["BetaCopy2019"]["sources"][0]["file"] = "copy.bib"
    merged_rows["BetaCopy2019"]["conflicts"] = [{
        "at": NOW, "resolution": "kept-existing",
        "field_diff": {"year": {"existing": "2019", "incoming": "2020"}},
        "existing_sha256": merged_rows["BetaCopy2019"]["content_sha256"],
        "incoming_sha256": "0" * 64,
        "source": dict(merged_rows["BetaCopy2019"]["sources"][0]),
        "resolved_at": "2026-01-02T00:00:00+08:00", "note": "人工确认保留现有",
    }]
    merged_conflicts = list(merged_rows["BetaCopy2019"]["conflicts"])
    write_records(db, list(merged_rows.values()))
    beta_before = keyed(db)["Beta2019"]
    result = run_cli("merge-records", "--db", str(db), "--keys",
                     "Beta2019,BetaCopy2019", "--new-key", "Beta2019",
                     "--entry-type", "book", "--apply", "--json")
    rows = keyed(db)
    survivor = rows.get("Beta2019") or {}
    check("R06-k merge：rc0、记录数 -1、旧 key 进 aliases",
          result.returncode == 0 and "BetaCopy2019" not in rows
          and "BetaCopy2019" in (survivor.get("aliases") or []), result.stderr[:300])
    check("R06-k merge 不静默丢被并记录的已裁决 conflicts（并入幸存者）",
          survivor.get("conflicts") == merged_conflicts,
          json.dumps(survivor.get("conflicts"), ensure_ascii=False)[:400])
    check("R06-k merge 保留幸存者 verified 证据、来源并集去重",
          survivor.get("verified") is True
          and survivor.get("verified_at") == beta_before.get("verified_at")
          and sorted(str(source.get("file")) for source in survivor.get("sources") or [])
          == ["copy.bib", "seed.bib"], str(survivor.get("sources"))[:300])

    # (l) restore 用备份副本字节级恢复；expect sha 不符 rc2 零写
    backup_dir = db.parent / "backups"
    before = facts(db)
    result = run_cli("edit", "--db", str(db), "--key", "Gamma2018", "--apply",
                     "--json-fields", full_fields(db, "Gamma2018", title="Gamma (rev)"),
                     "--backup", str(backup_dir), "--json")
    backup_path = Path(payload(result).get("backup_path") or "")
    check("R06-l edit --apply --backup 生成备份副本（文件名带库 sha 前缀）",
          result.returncode == 0 and backup_path.is_file()
          and backup_path.name.startswith("bibliography-"), str(backup_path))
    result = run_cli("restore", "--db", str(db), "--backup", str(backup_path), "--json")
    check("R06-l restore：rc0、库字节 sha 等于备份副本 sha",
          result.returncode == 0 and sha256_file(db) == sha256_file(backup_path),
          result.stderr[:300])
    before = facts(db)
    result = run_cli("restore", "--db", str(db), "--backup", str(backup_path),
                     "--expect-db-sha256", "0" * 64, "--json")
    check("R06-l restore expect-db-sha256 不符：rc2 零写",
          result.returncode == 2 and facts(db) == before, result.stderr[:300])

    # (m) 核实预算闸零请求（纯函数；超限在任何检索/AI 之前返回）
    from slcn import refmark  # noqa: E402  （纯本地函数，不联网）
    scope = refmark.resolve_verify_scope(["A", "B", "C"], ["A", "B", "C"],
                                         verify_max=1)
    guidance = str(refmark.limit_guidance(scope))
    check("R06-m 预算闸纯函数：3 条引用、verify_max=1 → over_limit=true、n_scope=3",
          scope.over_limit is True and scope.n_scope == 3, repr(scope)[:300])
    check("R06-m 超预算指引给三条路（--verify-max / --verify-all-refs / --verify-plan）",
          all(flag in guidance for flag in
              ("--verify-max", "--verify-all-refs", "--verify-plan")), guidance[:300])


# ── R07 旧引用资产升级与回退 ───────────────────────────────────────────────


def case_r07_legacy(work: Path) -> None:
    folder = work / "legacy"
    folder.mkdir()
    legacy = [{key: value for key, value in record_.items()
               if key not in ("content_sha256", "conflicts", "aliases", "edits")}
              for record_ in base_records()]
    db = write_records(folder / "bibliography.jsonl", legacy)
    before = facts(db)

    # (n) 旧格式缺 content_sha256：读命令拒绝并指路 rehash（零写）
    result = run_cli("query", "--db", str(db), "--key", "Alpha2020", "--json")
    check("R07-n 旧格式库 query：rc2、stderr 有结构问题 + rehash --apply 迁移指路",
          result.returncode == 2 and "结构问题" in result.stderr
          and "rehash" in result.stderr and "--apply" in result.stderr,
          result.stderr[:400])
    check("R07-n 旧格式库 query 零写", facts(db) == before, str(facts(db)))
    result = run_cli("lint", "--db", str(db), "--json")
    body = payload(result)
    check("R07-n 旧格式库 lint：rc1、逐行报 content_sha256、只读",
          result.returncode == 1 and body.get("ok") is False
          and any("content_sha256" in str(error)
                  for error in body.get("errors") or [])
          and facts(db) == before, json.dumps(body.get("errors"), ensure_ascii=False)[:300])

    # rehash 迁移：只改 content_sha256+updated_at，逐记录证据不动
    before_rows = keyed(db)
    result = run_cli("rehash", "--db", str(db), "--apply", "--json")
    after_rows = keyed(db)
    check("R07-n rehash --apply 迁移旧格式：rc0、applied=true、stale=4、记录数不变",
          result.returncode == 0 and payload(result).get("applied") is True
          and payload(result).get("stale") == 4 and len(after_rows) == 4,
          result.stderr[:300])
    deltas = {key: {field for field in set(before_rows[key]) | set(after_rows[key])
                    if before_rows[key].get(field) != after_rows[key].get(field)}
              for key in before_rows}
    check("R07-n 迁移逐记录只改 content_sha256+updated_at（key/来源/核实/别名逐条保留）",
          all(delta <= {"content_sha256", "updated_at"} for delta in deltas.values())
          and all(content_only(after_rows[key]) == content_only(before_rows[key])
                  for key in before_rows),
          str({key: sorted(delta) for key, delta in deltas.items() if delta})[:400])
    check("R07-n 迁移后新 sha 与独立复算一致（逐记录）",
          all(after_rows[key]["content_sha256"]
              == bibdb.content_sha256(after_rows[key]["entry_type"],
                                      after_rows[key]["fields"]) for key in after_rows),
          str({key: after_rows[key]["content_sha256"][:12] for key in after_rows}))
    result = run_cli("query", "--db", str(db), "--key", "Alpha2020", "--json")
    check("R07-n 迁移后 query rc0、原记录可取", result.returncode == 0
          and payload(result).get("count") == 1, result.stderr[:300])

    # (o) 未来格式：version 高于本引擎支持 → 全线拒绝破坏性写入
    future_folder = work / "future"
    future_folder.mkdir()
    future = base_records()
    future[3]["version"] = 2                       # Gamma2018 变成未来格式
    future[3]["fields"]["language"] = "en"
    future[3]["provenance_v2"] = {"writer": "v2-tool"}
    future_db = write_records(future_folder / "bibliography.jsonl", future)
    before = facts(future_db)
    result = run_cli("lint", "--db", str(future_db), "--json")
    body = payload(result)
    check("R07-o 未来版本：lint rc1 并指出 version 高于本引擎支持（只读）",
          result.returncode == 1 and any(
              "version" in str(error) and "高于" in str(error)
              for error in body.get("errors") or [])
          and facts(future_db) == before,
          json.dumps(body.get("errors"), ensure_ascii=False)[:300])
    for command in (["query", "--key", "Gamma2018"],
                    ["edit", "--key", "Gamma2018", "--apply", "--json-fields",
                     full_fields(future_db, "Gamma2018", title="X")],
                    ["merge-records", "--keys", "Beta2019,BetaCopy2019",
                     "--new-key", "Beta2019", "--entry-type", "book", "--apply"]):
        result = run_cli(command[0], "--db", str(future_db), *command[1:], "--json")
        check(f"R07-o 未来版本：{command[0]} rc2 零写、提示升级/迁移",
              result.returncode == 2
              and ("升级" in result.stderr or "迁移" in result.stderr
                   or "version" in result.stderr)
              and facts(future_db) == before, result.stderr[:300])
    result = run_cli("rehash", "--db", str(future_db), "--apply", "--json")
    check("R07-o 未来版本：rehash --apply 也拒绝（rc2 零写，不按旧规则改写）",
          result.returncode == 2 and facts(future_db) == before, result.stderr[:300])
    check("R07-o 未来版本：v2 专属字段原样保留（全程零写）",
          keyed(future_db)["Gamma2018"].get("fields", {}).get("language") == "en"
          and "provenance_v2" in keyed(future_db)["Gamma2018"], "v2 字段被改动")

    # (p) 更旧格式缺 sources：拒绝破坏性写入
    older_folder = work / "older"
    older_folder.mkdir()
    older = [{key: value for key, value in record_.items()
              if key not in ("sources", "content_sha256")}
             for record_ in base_records()]
    older_db = write_records(older_folder / "bibliography.jsonl", older)
    before = facts(older_db)
    result = run_cli("query", "--db", str(older_db), "--key", "Alpha2020", "--json")
    check("R07-p 缺 sources 旧格式：query rc2、stderr 有可执行处理指路",
          result.returncode == 2 and "结构问题" in result.stderr
          and ("rehash" in result.stderr or "迁移" in result.stderr),
          result.stderr[:400])
    result = run_cli("rehash", "--db", str(older_db), "--apply", "--json")
    check("R07-p 缺 sources：rehash 拒绝执行（rc2 零写，不替旧记录造来源）",
          result.returncode == 2 and "拒绝执行" in result.stderr
          and facts(older_db) == before, result.stderr[:300])
    result = run_cli("edit", "--db", str(older_db), "--key", "Alpha2020", "--apply",
                     "--json-fields", json.dumps({"title": "X"}, ensure_ascii=False),
                     "--json")
    check("R07-p 缺 sources：edit 拒绝（rc2 零写）",
          result.returncode == 2 and facts(older_db) == before, result.stderr[:300])


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="p07-bib-asset-"))
    try:
        db = case_r01_cas(work)
        case_r06_health(db)
        case_r07_legacy(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    total = pass_n + len(fails)
    print(f"\n== P07 引用库资产: {pass_n}/{total} 通过，{len(fails)} 失败 ==")
    for name in fails:
        print(f"FAILED  {name}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
