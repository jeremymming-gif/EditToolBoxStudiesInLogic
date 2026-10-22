#!/usr/bin/env python3.14
r"""B-188：引用库维护闭环的**引擎命令**契约（GUI 写库入口；不含 GUI）。

设计卡：`EditToolBox-notes/drafts/specs/b188-bibdb-maintenance-loop.md` §3/§5。
本套件钉住三条新命令 + 两条既有能力补强（hermetic：tempfile 造库，不碰真库/真稿）：

  (a) `edit`：dry-run 逐字段 old→new、库 sha 前后、必填缺字段影响；不改库。
      apply：sha 闸 →（可选）写前备份 → 重算 content_sha256/updated_at → 原子写 → 复核；
      改 key 默认不勾、`--new-key` 后旧 key 自动进 `aliases`（全局查重）；
      待裁决冲突记录拒绝编辑；`--bib` key 必须等于 `--key`；`--expect-db-sha256`
      不符退 2 且零写入；写库痕迹 `edits[]`。
      **B-190：`--apply` 的 `--backup` 改可选**（不传 = 不写备份、`backup_path=""`、
      `edits[].backup_sha256=""`；传了行为不变 —— GUI 改用 git 版本层）。
  (b) `merge-records`：2 条合并为 1 条（新规范 key、旧 key 全进 aliases、来源合并、
      记录数 -1、逐字段 `--field-source` 选源、dry-run 不写）；entry_type 不一致必须
      `--entry-type`；key 撞车/待裁决记录/未知 key → 退 2 且零写入；`--backup` 同
      `edit` 一样可选（B-190）。
  (c) `suggest-field-fixes`：只读（库逐字节不变）；A 类缺 title/year 候选（题名标记/
      年份正则/出版者形态）+ 异常 entry_type 建议。
  (d) `lint --with-records`：`warning_items[].records` 给全量受影响 key；**不带 flag 时
      输出与旧版逐字节相同**（无 `records` 键）。
  (e) `refresh_aliases()` 保留记录里已有 aliases（B-188 改名/合并的历史 key 不丢；
      全局唯一性仍强制）。

跑法（本机必须 Python 3.14 framework + PYTHONPATH=vendor）：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b188_bibdb_maintenance.py
"""

from __future__ import annotations

import datetime
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


def check(name: str, cond, detail: str = "") -> bool:
    global pass_n
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        pass_n += 1
    else:
        fails.append(name)
    return bool(cond)


CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep + str(_ENGINE / "vendor"),
           PYTHONIOENCODING="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def parse_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def sha(path: str) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def backup_files(root: str) -> list[str]:
    """临时目录树里所有写前备份文件（B-190：不带 `--backup` 时必须为空）。"""
    return sorted(str(p) for p in pathlib.Path(root).rglob("bibliography-*.jsonl"))


def utc_stamps(count: int = 6) -> list[str]:
    """当前 UTC 时间戳及随后几秒（预造同名备份，跨秒也不闪断）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return [(now + datetime.timedelta(seconds=offset)).strftime("%Y%m%dT%H%M%SZ")
            for offset in range(count)]


SOURCE = {"kind": bibdb.KIND_PUBLISHED, "issue": "2023年第1期(68)",
          "slug": "01 Base", "file": "base.bib"}


def mk_record(key: str, etype: str, fields: dict, *, aliases=None,
              conflicts=None, source_key: str = "") -> dict:
    fields = dict(sorted(fields.items()))
    now = "2026-10-18T12:00:00+08:00"
    source = dict(SOURCE)
    source["key"] = source_key or key
    source["entry_sha256"] = bibdb.content_sha256(etype, fields)
    return {
        "key": key, "version": 1, "entry_type": etype, "fields": fields,
        "content_sha256": bibdb.content_sha256(etype, fields),
        "verified": True, "verified_at": now, "method": "published-bib",
        "created_at": now, "updated_at": now,
        "aliases": list(aliases or []),
        "sources": [source],
        "conflicts": list(conflicts or []),
    }


def pending_conflict() -> dict:
    return {"at": "2026-10-18T12:00:00+08:00", "resolution": "kept-existing",
            "note": "导入策略保留旧值（等人工裁决）", "existing_sha256": "0" * 64,
            "incoming_sha256": "1" * 64,
            "field_diff": {"journal": {"existing": "Journal Old", "incoming": "Journal New"}},
            "source": dict(SOURCE)}


def write_db(path: str, records: list[dict]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) + "\n" for record in records]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(lines))
    return path


TMP = tempfile.mkdtemp(prefix="b188_maint_")

ARTICLE = mk_record("Alpha2020", "article", {
    "author": "Alpha, Ann", "title": "Alpha Work", "journal": "Journal of Alpha",
    "volume": "2", "number": "1", "pages": "1--10", "year": "2020",
})
BOOK_A = mk_record("1-24", "book", {
    "author": "Tindale, C. W.", "title": "The Philosophy of Argument and Audience Reception",
    "publisher": "Cambridge University Press", "address": "Cambridge", "year": "2015",
})
BOOK_B = mk_record("t15", "book", {
    "author": "Tindale, C. W.", "title": "The Philosophy of Argument and Audience Reception",
    "publisher": "Cambridge University Press", "address": "Cambridge", "year": "2015",
})
SEP = mk_record("sep-preferences", "incollection", {
    "author": "Hansson, Sven Ove", "title": "Preferences",
    "booktitle": "The Stanford Encyclopedia of Philosophy",
    "publisher": "Metaphysics Research Lab", "year": "2022",
    "url": "https://plato.stanford.edu/archives/spr2022/entries/preferences/",
}, aliases=["liupreferencelogic2025"])
CONFLICTED = mk_record("Gamma2019", "article", {
    "author": "Gamma, G.", "title": "Gamma Work", "journal": "Journal Old", "year": "2019",
}, conflicts=[pending_conflict()])
NOTITLE = mk_record("NoTitle", "misc", {
    "author": "Nobody, N.", "year": "2018"})


def fresh_db(name: str, records=None) -> str:
    return write_db(os.path.join(TMP, name, "bibliography.jsonl"),
                    records if records is not None else [ARTICLE, BOOK_A, BOOK_B, SEP, CONFLICTED,
                                                        NOTITLE])


# ══════════════════════════════════════════════════════════════════════════
# (a) edit：dry-run 预览
# ══════════════════════════════════════════════════════════════════════════
db = fresh_db("edit_dry")
before = sha(db)
r = run_cli("edit", "--db", db, "--key", "Alpha2020",
            "--json-fields", json.dumps({"entry_type": "article",
                                         "fields": {"author": "Alpha, A.",
                                                    "title": "Alpha Work Revised",
                                                    "journal": "Journal of Alpha",
                                                    "volume": "2", "number": "1",
                                                    "pages": "1--10", "year": "2020"}}),
            "--dry-run", "--json")
d = parse_json(r)
check("(a) edit --dry-run 退出 0 且 dry_run=true", r.returncode == 0 and d.get("dry_run") is True,
      r.stderr[-200:])
check("(a) dry-run 报逐字段 old→new", d.get("field_changes", {}).get("title") == {
    "old": "Alpha Work", "new": "Alpha Work Revised"}
    and d.get("field_changes", {}).get("author") == {"old": "Alpha, Ann", "new": "Alpha, A."},
    d.get("field_changes"))
check("(a) dry-run 报库 sha 前后", re.fullmatch(r"[0-9a-f]{64}", d.get("db_sha256_before", ""))
      and re.fullmatch(r"[0-9a-f]{64}", d.get("db_sha256_after", ""))
      and d["db_sha256_before"] == before, d.get("db_sha256_before", "")[:12])
check("(a) dry-run 零写入（库逐字节不变）", sha(db) == before)
check("(a) dry-run 不给 backup_path/written=false",
      d.get("backup_path") == "" and d.get("written") is False)
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--new-key", "Alpha2020x",
            "--json-fields", json.dumps({"entry_type": "article", "fields":
                                         {"title": "Alpha Work Revised", "year": "2020"}}),
            "--dry-run", "--json")
d = parse_json(r)
check("(a) 改 key 预览：aliases_added = [旧 key]、new_key 正确",
      d.get("aliases_added") == ["Alpha2020"] and d.get("new_key") == "Alpha2020x", d)

# 新 key 撞车（canonical key / alias）
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--new-key", "t15",
            "--json-fields", json.dumps({"title": "X", "year": "2020"}), "--dry-run", "--json")
check("(a) 新 key 撞 canonical key → 退 2 + 未写库",
      r.returncode == 2 and sha(db) == before and "占用" in (parse_json(r).get("error") or ""),
      (parse_json(r).get("error") or r.stderr)[:120])
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--new-key", "liupreferencelogic2025",
            "--json-fields", json.dumps({"title": "X", "year": "2020"}), "--dry-run", "--json")
check("(a) 新 key 撞 alias → 退 2",
      r.returncode == 2 and "别名" in (parse_json(r).get("error") or ""), r.returncode)

# 输入校验
r = run_cli("edit", "--db", db, "--key", "Alpha2020",
            "--json-fields", json.dumps({"title": "Alpha Work"}),
            "--dry-run", "--json")
check("(a) --json-fields 不给 entry_type 时回落到记录现有类型",
      r.returncode == 0 and parse_json(r).get("entry_type_after") == "article",
      r.returncode)
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--entry-type", "rticle",
            "--json-fields", json.dumps({"title": "T"}), "--dry-run", "--json")
check("(a) 未知 entry_type → 退 2", r.returncode == 2 and "未知" in r.stderr, r.stderr[-120:])
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--dry-run", "--json")
check("(a) --bib/--json-fields 都不给 → 退 2", r.returncode == 2)
r = run_cli("edit", "--db", db, "--key", "NoSuchKey", "--entry-type", "article",
            "--json-fields", json.dumps({"title": "T"}), "--dry-run", "--json")
check("(a) 未知 key → 退 2", r.returncode == 2 and "没有 key" in r.stderr, r.stderr[-120:])
r = run_cli("edit", "--db", db, "--key", "Gamma2019", "--entry-type", "article",
            "--json-fields", json.dumps({"title": "T"}), "--dry-run", "--json")
check("(a) 有待裁决冲突的记录拒绝编辑（报 C 编号）",
      r.returncode == 2 and "C" in r.stderr and "待裁决" in r.stderr, r.stderr[-160:])

# --bib 输入
bib_ok = os.path.join(TMP, "edit_dry", "ok.bib")
with open(bib_ok, "w", encoding="utf-8") as handle:
    handle.write("@article{Alpha2020,\n  author = {Alpha, Ann},\n  title = {Alpha Work},\n"
                 "  journal = {Journal of Alpha},\n  volume = {2},\n  number = {1},\n"
                 "  pages = {1--10},\n  year = {2020}\n}\n")
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--bib", bib_ok, "--dry-run", "--json")
check("(a) --bib 单条且 key 一致：可用", r.returncode == 0 and parse_json(r).get("dry_run"))
bib_badkey = os.path.join(TMP, "edit_dry", "badkey.bib")
with open(bib_badkey, "w", encoding="utf-8") as handle:
    handle.write("@article{OtherKey,\n  title = {T}\n}\n")
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--bib", bib_badkey, "--dry-run", "--json")
check("(a) --bib 的 key 与 --key 不符 → 退 2", r.returncode == 2 and "--new-key" in r.stderr)
bib_two = os.path.join(TMP, "edit_dry", "two.bib")
with open(bib_two, "w", encoding="utf-8") as handle:
    handle.write("@article{Alpha2020,\n  title = {T}\n}\n@book{B},\n  title = {U}\n}\n")
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--bib", bib_two, "--dry-run", "--json")
check("(a) --bib 多条/语法坏 → 退 2", r.returncode == 2)

# apply 前安全闸（B-190：--backup 可选；不给备份也必须拦住 sha 不符）
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--entry-type", "article",
            "--json-fields", json.dumps({"title": "T", "year": "2020"}),
            "--apply", "--expect-db-sha256", "f" * 64, "--json")
check("(a) apply 不带 --backup 且 sha 不符 → 退 2 + 零写入 + 无备份文件",
      r.returncode == 2 and sha(db) == before and "不符" in r.stderr
      and not backup_files(os.path.join(TMP, "edit_dry")), r.stderr[-140:])
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--entry-type", "article",
            "--json-fields", json.dumps({"title": "T", "year": "2020"}),
            "--apply", "--backup", os.path.join(TMP, "edit_dry", "bk"),
            "--expect-db-sha256", "f" * 64, "--json")
check("(a) 带 --backup 但 --expect-db-sha256 不符 → 退 2 + 零写入 + 无备份文件",
      r.returncode == 2 and sha(db) == before and "不符" in r.stderr
      and not backup_files(os.path.join(TMP, "edit_dry")), r.stderr[-140:])

# ══════════════════════════════════════════════════════════════════════════
# (b) edit：apply
# ══════════════════════════════════════════════════════════════════════════
db = fresh_db("edit_apply")
before = sha(db)
backup_dir = os.path.join(TMP, "edit_apply", "backups")
fields_new = {"author": "Alpha, A.", "title": "Alpha Work Revised",
              "journal": "Journal of Alpha", "volume": "2", "number": "1",
              "pages": "1--10", "year": "2020"}
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--new-key", "Alpha2020x",
            "--json-fields", json.dumps({"entry_type": "article", "fields": fields_new}),
            "--apply", "--expect-db-sha256", before, "--backup", backup_dir,
            "--note", "测试改名", "--json")
d = parse_json(r)
check("(b) edit --apply 退出 0 + written=true", r.returncode == 0 and d.get("written") is True,
      r.stderr[-200:])
check("(b) apply 生成自动备份且 sha 与写前一致",
      os.path.isfile(d.get("backup_path", "")) and sha(d["backup_path"]) == before,
      d.get("backup_path"))
check("(b) 库 sha 真变了", sha(db) != before and d.get("db_sha256_after") == sha(db))
store = bibdb.Store.load(db)
record = store.get("Alpha2020x")
check("(b) 新 key 写入、旧 key 不在了", record is not None and store.get("Alpha2020") is None)
check("(b) 字段按输入写入且 content_sha256 自洽",
      record["fields"] == dict(sorted(bibdb.canonical_fields(fields_new).items()))
      and record["content_sha256"] == bibdb.content_sha256("article", record["fields"]))
check("(b) 旧 key 自动进 aliases 且可按别名查",
      "Alpha2020" in record.get("aliases", [])
      and store.query_key("Alpha2020")["hits"][0]["key"] == "Alpha2020x")
check("(b) 写库痕迹 edits[]：action/note/backup sha",
      (record.get("edits") or [{}])[-1].get("action") == "edit"
      and (record.get("edits") or [{}])[-1].get("note") == "测试改名"
      and (record.get("edits") or [{}])[-1].get("backup_sha256") == before,
      (record.get("edits") or [{}])[-1])
check("(b) 复核通过（lint error 0 / 记录数不变）",
      d.get("verification", {}).get("ok") is True
      and len(store.records()) == 6 and not store.lint()["errors"], d.get("verification"))
check("(b) 其它记录逐字节未动（除被编辑那条）",
      json.dumps(store.get("t15"), ensure_ascii=False, sort_keys=True)
      == json.dumps(BOOK_B, ensure_ascii=False, sort_keys=True))
# 再跑一次 dry-run：应无字段变化（幂等预览）
r = run_cli("edit", "--db", db, "--key", "Alpha2020x",
            "--json-fields", json.dumps({"entry_type": "article", "fields": fields_new}),
            "--dry-run", "--json")
check("(b) 同内容再 preview：field_changes 为空",
      parse_json(r).get("field_changes") == {} and parse_json(r).get("aliases_added") == [])

# B-190：带 --backup 的旧行为不变（文件名口径 + 同名冲突拒绝）
check("(b) 备份文件名 = `bibliography-<UTCts>-<库sha前12位>.jsonl`、目录里 1 份",
      bool(re.fullmatch(r"bibliography-\d{8}T\d{6}Z-[0-9a-f]{12}\.jsonl",
                        os.path.basename(d.get("backup_path", ""))))
      and len(backup_files(backup_dir)) == 1, backup_files(backup_dir))
db = fresh_db("edit_backup_collision")
before = sha(db)
collision_dir = os.path.join(TMP, "edit_backup_collision", "backups")
os.makedirs(collision_dir)
for stamp in utc_stamps():
    with open(os.path.join(collision_dir,
                           f"bibliography-{stamp}-{before[:12]}.jsonl"), "wb") as handle:
        handle.write(b"")
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--entry-type", "article",
            "--json-fields", json.dumps({"title": "T", "year": "2020"}),
            "--apply", "--backup", collision_dir, "--json")
check("(b) 同名备份已存在 → 退 2 且库零写入",
      r.returncode == 2 and sha(db) == before and "已存在" in r.stderr, r.stderr[-140:])
# 这份撞名库不再往下用（避免污染后续断言的临时目录树）

# ══════════════════════════════════════════════════════════════════════════
# (b2) edit：apply 不带 --backup（B-190：不写备份文件；sha 闸/复核/edits[] 不变）
# ══════════════════════════════════════════════════════════════════════════
db = fresh_db("edit_apply_nobackup")
nobackup_root = os.path.join(TMP, "edit_apply_nobackup")
before = sha(db)
fields_new = {"author": "Alpha, A.", "title": "Alpha Work Revised",
              "journal": "Journal of Alpha", "volume": "2", "number": "1",
              "pages": "1--10", "year": "2020"}
r = run_cli("edit", "--db", db, "--key", "Alpha2020", "--new-key", "Alpha2020x",
            "--json-fields", json.dumps({"entry_type": "article", "fields": fields_new}),
            "--apply", "--expect-db-sha256", before, "--note", "无备份改名", "--json")
d = parse_json(r)
check("(b2) edit --apply 不带 --backup：退出 0 + written=true",
      r.returncode == 0 and d.get("written") is True, r.stderr[-200:])
check("(b2) 不产生任何备份文件、payload backup_path 为空串",
      d.get("backup_path") == "" and not backup_files(nobackup_root),
      backup_files(nobackup_root))
check("(b2) 库 sha 真变了且 reported after = 实测",
      sha(db) != before and d.get("db_sha256_after") == sha(db))
store = bibdb.Store.load(db)
record = store.get("Alpha2020x")
check("(b2) 写后复核 ok、新 key/旧 key→aliases 正常",
      d.get("verification", {}).get("ok") is True
      and record is not None and store.get("Alpha2020") is None
      and "Alpha2020" in record.get("aliases", []), d.get("verification"))
check("(b2) edits[] 正常追加且 backup_sha256/backup_path 均为空串",
      (record.get("edits") or [{}])[-1].get("action") == "edit"
      and (record.get("edits") or [{}])[-1].get("note") == "无备份改名"
      and (record.get("edits") or [{}])[-1].get("backup_sha256") == ""
      and (record.get("edits") or [{}])[-1].get("backup_path") == "",
      (record.get("edits") or [{}])[-1])
check("(b2) 其余记录逐字节未动（除被编辑那条）",
      json.dumps(store.get("t15"), ensure_ascii=False, sort_keys=True)
      == json.dumps(BOOK_B, ensure_ascii=False, sort_keys=True))

# ══════════════════════════════════════════════════════════════════════════
# (c) merge-records：dry-run + apply
# ══════════════════════════════════════════════════════════════════════════
db = fresh_db("merge")
before = sha(db)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "Tindale2015",
            "--dry-run", "--json")
d = parse_json(r)
check("(c) merge dry-run 退出 0 / dry_run=true", r.returncode == 0 and d.get("dry_run") is True,
      r.stderr[-160:])
check("(c) dry-run 记录数 6→5 且库零写入",
      d.get("records_after") == 5 and sha(db) == before, d.get("records_after"))
check("(c) 逐字段对照含两记录的值与取值来源",
      d.get("field_comparison", {}).get("title", {}).get("1-24")
      == "The Philosophy of Argument and Audience Reception"
      and d["field_comparison"]["title"]["chosen"] in ("1-24", "t15"))
check("(c) aliases = 两条旧 key", d.get("aliases") == ["1-24", "t15"], d.get("aliases"))
r = run_cli("merge-records", "--db", db, "--keys", "1-24", "--new-key", "X", "--dry-run", "--json")
check("(c) 少于 2 条 key → 退 2", r.returncode == 2)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,nope", "--new-key", "X", "--dry-run", "--json")
check("(c) 未知 key → 退 2 + 零写入", r.returncode == 2 and sha(db) == before)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "sep-preferences",
            "--dry-run", "--json")
check("(c) 新 key 撞已有 canonical key → 退 2", r.returncode == 2 and "占用" in r.stderr)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "Tindale2015",
            "--field-source", "title=NoSuch", "--dry-run", "--json")
check("(c) --field-source 来源不在 --keys → 退 2", r.returncode == 2)

# apply
db = fresh_db("merge_apply")
before = sha(db)
backup_dir = os.path.join(TMP, "merge_apply", "backups")
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "Tindale2015",
            "--apply", "--expect-db-sha256", before, "--backup", backup_dir,
            "--note", "测试合并", "--json")
d = parse_json(r)
check("(d) merge --apply 退出 0 / written=true", r.returncode == 0 and d.get("written") is True,
      r.stderr[-200:])
check("(d) 备份存在且 sha = 写前库", os.path.isfile(d.get("backup_path", ""))
      and sha(d["backup_path"]) == before)
check("(d) 备份文件名 = `bibliography-<UTCts>-<库sha前12位>.jsonl`",
      bool(re.fullmatch(r"bibliography-\d{8}T\d{6}Z-[0-9a-f]{12}\.jsonl",
                        os.path.basename(d.get("backup_path", "")))),
      os.path.basename(d.get("backup_path", "")))
store = bibdb.Store.load(db)
survivor = store.get("Tindale2015")
check("(d) 记录数 6→5、两条旧 key 都不在",
      len(store.records()) == 5 and store.get("1-24") is None and store.get("t15") is None)
check("(d) 旧 key 全进 aliases、来源合并（2 条）",
      survivor is not None and set(survivor.get("aliases") or []) == {"1-24", "t15"}
      and len(survivor.get("sources") or []) >= 2, survivor and survivor.get("aliases"))
check("(d) 合并后按旧 key 能查到新记录",
      store.query_key("t15")["hits"][0]["key"] == "Tindale2015")
check("(d) 合并痕迹 edits[].merged_keys 全留",
      (survivor.get("edits") or [{}])[-1].get("merged_keys") == ["1-24", "t15"],
      (survivor.get("edits") or [{}])[-1])
check("(d) 复核 ok / lint error 0", d.get("verification", {}).get("ok") is True
      and not store.lint()["errors"], d.get("verification"))

# B-190：merge-records --apply 不带 --backup（不写备份；sha 闸/复核/edits[] 不变）
db = fresh_db("merge_apply_nobackup")
nobackup_root = os.path.join(TMP, "merge_apply_nobackup")
before = sha(db)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "Tindale2015",
            "--apply", "--expect-db-sha256", before, "--note", "无备份合并", "--json")
d = parse_json(r)
check("(d2) merge --apply 不带 --backup：退出 0 + written=true",
      r.returncode == 0 and d.get("written") is True, r.stderr[-200:])
check("(d2) 不产生任何备份文件、payload backup_path 为空串",
      d.get("backup_path") == "" and not backup_files(nobackup_root),
      backup_files(nobackup_root))
store = bibdb.Store.load(db)
survivor = store.get("Tindale2015")
check("(d2) 记录数 6→5、旧 key 全进 aliases、复核 ok",
      len(store.records()) == 5 and survivor is not None
      and set(survivor.get("aliases") or []) == {"1-24", "t15"}
      and d.get("verification", {}).get("ok") is True, d.get("verification"))
check("(d2) edits[] 正常追加且 backup_sha256/backup_path 均为空串",
      (survivor.get("edits") or [{}])[-1].get("action") == "merge"
      and (survivor.get("edits") or [{}])[-1].get("note") == "无备份合并"
      and (survivor.get("edits") or [{}])[-1].get("backup_sha256") == ""
      and (survivor.get("edits") or [{}])[-1].get("backup_path") == "",
      (survivor.get("edits") or [{}])[-1])
# sha 闸：不带 backup 时同样拦（rc2 + 零写入 + 无备份文件）
db = fresh_db("merge_nobackup_gate")
before = sha(db)
r = run_cli("merge-records", "--db", db, "--keys", "1-24,t15", "--new-key", "Tindale2015",
            "--apply", "--expect-db-sha256", "f" * 64, "--json")
check("(d2) merge --apply 不带 --backup 且 sha 不符 → 退 2 + 零写入 + 无备份文件",
      r.returncode == 2 and sha(db) == before and "不符" in r.stderr
      and not backup_files(os.path.join(TMP, "merge_nobackup_gate")), r.stderr[-140:])

# 近重复：逐字段选源 + entry_type 不一致必须显式给
nr_a = mk_record("Segerberg1971", "phdthesis", {
    "author": "Segerberg, K.", "title": "An Essay in Classical Modal Logic",
    "school": "Uppsala: Filosofiska Studier", "year": "1971"})
nr_b = mk_record("Segerberg1971b", "book", {
    "author": "Segerberg, Karl Krister", "title": "An essay in classical modal logic",
    "publisher": "Uppsala, Filosofiska F\\\"oreningen", "address": "Uppsala",
    "year": "1971"}, aliases=["s71"])
db = fresh_db("merge_near", [nr_a, nr_b])
r = run_cli("merge-records", "--db", db, "--keys", "Segerberg1971,Segerberg1971b",
            "--new-key", "Segerberg1971", "--dry-run", "--json")
check("(d) 近重复 entry_type 不一致且没给 --entry-type → 退 2", r.returncode == 2
      and "entry_type" in r.stderr)
r = run_cli("merge-records", "--db", db, "--keys", "Segerberg1971,Segerberg1971b",
            "--new-key", "Segerberg1971", "--entry-type", "book",
            "--field-source", "author=Segerberg1971", "--field-source", "title=Segerberg1971",
            "--field-source", "school=Segerberg1971", "--dry-run", "--json")
d = parse_json(r)
check("(d) 近重复逐字段选源生效（author/title 取 A；publisher 取 B）",
      d.get("fields", {}).get("author") == "Segerberg, K."
      and d["fields"].get("title") == "An Essay in Classical Modal Logic"
      and d["fields"].get("publisher", "").startswith("Uppsala, Filosofiska"), d.get("fields"))
check("(d) 近重复 aliases = B 的旧 key + 原 aliases", d.get("aliases") == [
    "Segerberg1971b", "s71"], d.get("aliases"))

# 待裁决记录拒绝合并 + 零写入
db = fresh_db("merge_conflict")
before = sha(db)
r = run_cli("merge-records", "--db", db, "--keys", "Gamma2019,t15", "--new-key", "Gamma2019",
            "--entry-type", "article", "--dry-run", "--json")
check("(d) 待裁决记录拒绝合并（报 C 编号）",
      r.returncode == 2 and "待裁决" in r.stderr and sha(db) == before, r.stderr[-160:])

# ══════════════════════════════════════════════════════════════════════════
# (e) lint --with-records + alias 保留
# ══════════════════════════════════════════════════════════════════════════
db = fresh_db("lint")
plain = run_cli("lint", "--db", db, "--json")
with_rec = run_cli("lint", "--db", db, "--json", "--with-records")
dp, dw = parse_json(plain), parse_json(with_rec)
check("(e) lint 退出 0 / error 0", plain.returncode == 0 and dp.get("ok") is True)
check("(e) 不带 flag：warning_items 里没有 records 键（逐字节兼容）",
      all("records" not in item for item in dp.get("warning_items") or []))
items = {item["code"]: item for item in dw.get("warning_items") or []}
check("(e) --with-records：missing_title.records = 全部受影响 key",
      items.get("missing_title", {}).get("records") == ["NoTitle"],
      items.get("missing_title", {}).get("records"))
check("(e) --with-records：conflict_pending.records 给待裁决记录 key",
      items.get("conflict_pending", {}).get("records") == ["Gamma2019"],
      items.get("conflict_pending", {}).get("records"))
check("(e) --with-records：near_duplicates.records 用 A≈B 形态",
      (items.get("near_duplicates", {}).get("records") or []) == [],
      items.get("near_duplicates", {}).get("records"))

# refresh_aliases 保留手工 aliases（模拟改名/合并后的历史 key）
db = fresh_db("alias_keep")
store = bibdb.Store.load(db)
record = store.get("Alpha2020")
record["aliases"] = ["old-alpha", "liupreferencelogic2025"]
store.dirty = True
check("(e) save() 后手工 aliases 不被重算丢掉",
      store.save() is True and "old-alpha" in bibdb.Store.load(db).get("Alpha2020")["aliases"])
reloaded = bibdb.Store.load(db)
ambiguous = [alias for alias, owners in reloaded._by_alias.items() if len(owners) > 1]
check("(e) 同别名不会指向两条记录", not ambiguous, ambiguous)
check("(e) alias 不会与另一条 canonical key 同名",
      not [alias for alias in reloaded._by_alias
           if alias in reloaded._by_key and alias not in
           {a for a in reloaded.get("sep-preferences").get("aliases") or []}])

# ══════════════════════════════════════════════════════════════════════════
# (f) suggest-field-fixes（只读）
# ══════════════════════════════════════════════════════════════════════════
mixed = mk_record("1-6", "misc", {
    "author": r"坂出祥伸 and 「明治哲学における中国古代論理学の理解—桑木厳翼を中心として—」 and "
              r"船山信一（編著） and \emph{明治論理学史研究} and 1966年 and 東京：理想社 and 第242--268页。",
    "userf": "jp"})
badtype = mk_record("badtype1", "rticle", {"author": "X", "title": "T", "year": "2020"})
doi_key = mk_record("10.2307/2274828", "article", {"author": "Gettier, Edmund",
                                                   "year": "1963"})
db = fresh_db("suggest", [mixed, badtype, doi_key, ARTICLE])
before = sha(db)
r = run_cli("suggest-field-fixes", "--db", db, "--registry", "missing_field", "--json")
d = parse_json(r)
check("(f) suggest 退出 0 且只读（库逐字节不变）", r.returncode == 0 and sha(db) == before)
row = next((item for item in d.get("rows") or [] if item["key"] == "1-6"), {})
titles = [c for c in row.get("candidates") or [] if c["field"] == "title"]
years = [c for c in row.get("candidates") or [] if c["field"] == "year"]
check("(f) 题名标记 → title 候选（\\emph/「」）",
      any("明治哲学" in c["value"] for c in titles)
      and any("明治論理学史研究" in c["value"] for c in titles), titles[:3])
check("(f) 年份正则 → year 候选 1966", any(c["value"] == "1966" for c in years), years)
check("(f) 候选带来源/证据/风险等级",
      all(c.get("source") and c.get("evidence") and c.get("risk") in ("low", "medium", "high")
          for c in titles + years))
r = run_cli("suggest-field-fixes", "--db", db, "--registry", "unknown_entry_type", "--json")
d2 = parse_json(r)
row2 = next((item for item in d2.get("rows") or [] if item["key"] == "badtype1"), {})
check("(f) 异常 entry_type → 建议已知类型（rticle→article）",
      any(c.get("value") == "article" for c in row2.get("candidates") or []), row2)
row3 = next((item for item in d2.get("rows") or []
             if item["key"] == "10.2307/2274828"), {})
check("(f) C 类 key 异常（DOI 当 key）→ unknown_entry_type registry 也列出 + 建议规范 key",
      bool(row3) and row3.get("key_candidate") == "Gettier1963"
      and "DOI" in (row3.get("key_reason") or ""), row3.get("key_candidate"))
r = run_cli("suggest-field-fixes", "--db", db, "--registry", "nope", "--json")
check("(f) 未知 registry → 退 2", r.returncode == 2)
check("(f) suggest 全程零写入", sha(db) == before)

print()
print("=" * 78)
print(f"通过 {pass_n} 项，失败 {len(fails)} 项" + (f"：{fails}" if fails else ""))
sys.exit(1 if fails else 0)
