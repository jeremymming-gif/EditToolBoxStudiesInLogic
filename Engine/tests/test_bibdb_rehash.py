#!/usr/bin/env python3.14
r"""B-115：`bibdb.py rehash`（规范化规则升级后的 content_sha256 迁移）测试。

为什么单开一个套件：`content_sha256` 是「同一条文献」的内容判据，它依赖
`slcn/bibnorm.py` 的清理规则（`canonical_fields()` → `_clean_field_value()`）。
批45 `6a6ff4e` 改了 `DROP_FIELDS` 却没迁移 10-18 冻结的真实库 → 6 条 stale-sha
潜伏到批71 才被真实库 lint 抓到。这个套件钉三件事：

  (a) **冻结 fixture**：当前规范化规则下两条合成条目的 content_sha256 是常量。
      未来任何改 `DROP_FIELDS` / 值清理的提交都会让它红 —— 逼开发者按
      `bibdb.py`/`bibnorm.py` docstring 迁移真实库（rehash --apply）并更新本 fixture。
  (b) **dry-run 绝不写库**；`--apply` 只改 `content_sha256` + `updated_at`
      （fields/sources/verified/verified_at/method/created_at/aliases/conflicts 一字不动）；
      apply 后 lint error 归零、二次 apply 幂等（0 变更、库字节不变）。
  (c) **失败不留半库**：库不存在 / 有坏 JSON 行 → 退出 2 且库文件字节不变；
      写盘失败（只读目录注入）→ 退出 2、旧库完好；原子写不留 `.tmp-*` / `.bak` 垃圾。

不联网、不依赖真实稿库/真实引用库；全部用 tempfile 合成库（hermetic）。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_bibdb_rehash.py
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
skips: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    skips.append(name)
    print(f"SKIP  {name}  {why}")


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


CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep
           + str(_ENGINE / "vendor"), PYTHONIOENCODING="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def _fingerprint(records: list[dict]) -> dict[str, dict]:
    """除 content_sha256/updated_at 外的整条记录（rehash 不许动这些）。"""
    return {
        str(record.get("key")): {name: value for name, value in record.items()
                                 if name not in ("content_sha256", "updated_at")}
        for record in records
    }


def collect_rows(path: str) -> list[dict]:
    """测试侧独立重算（不读 rehash 的 collect 实现）：库存 sha → 当前规则 sha。"""
    rows = []
    for record in read_records(path):
        stored = record.get("content_sha256")
        stored = stored if isinstance(stored, str) else ""
        actual = bibdb.content_sha256(str(record.get("entry_type") or ""),
                                      record.get("fields") or {})
        if stored != actual:
            rows.append({"key": str(record.get("key") or ""), "old": stored,
                         "new": actual})
    return rows


# ─────────────────────────────────────────────────────────────────────────
# (a) 冻结 fixture：content_sha256 是常量（防「改规范化规则忘了迁移」）
# ─────────────────────────────────────────────────────────────────────────
#
# 这两条字段集是**故意**挑的：
#   · A 覆盖 pages（pp./1-9→1--9）、month 小写、number 前导零、DOI 大小写+前缀、
#     publisher 方括号、空白折叠，以及被 _IDENTITY_IGNORE 忽略的 userf；
#   · B 覆盖 WoS 导出垃圾（affiliation/da/unique-id/bdsk-*/abstract/file）——
#     只要 DROP_FIELDS 或前缀规则变了，B 的摘要立刻变。
# 失败时**不要**直接改期望值：先读 bibdb.py / bibnorm.py docstring 的迁移纪律。

FROZEN_A_TYPE = "article"
FROZEN_A_FIELDS = {
    "author": "Smith, John and Jones, Alice",
    "title": "A  Theory of Everything",
    "journal": "Journal of Logic",
    "year": "2020",
    "volume": "12",
    "number": "03",
    "pages": "pp. 45-67",
    "doi": "https://doi.org/10.1000/ABC123",
    "month": "NOV",
    "publisher": "[Oxford University Press, Mind Association]",
    "userf": "cn",
}
FROZEN_A_SHA = "49cd1b23db2bdc79443e5dd87bdc0ac77c9127b36faceafd5334203323805843"

FROZEN_B_TYPE = "article"
FROZEN_B_FIELDS = {
    "author": "Doe, Jane",
    "title": "The Limits of Logic",
    "journal": "Studia Logica",
    "year": "2019",
    "pages": "1-10",
    "affiliation": "Department of Philosophy",
    "da": "2019-03-01",
    "unique-id": "WOS:000123",
    "bdsk-url-1": "https://example.org",
    "abstract": "We argue that ...",
    "file": "Doe2019.pdf",
}
FROZEN_B_SHA = "2aa81fd8447e6d2aa688f7e0fdf9b40dc3320f3557601c96ccd317fcf0cde6d4"

MIGRATE_HINT = ("若失败：通常说明改了 bibnorm 的规范化规则（DROP_FIELDS/值清理）。"
                "按 docstring 迁移真实库（bibdb.py rehash --apply）并更新本文件期望 sha")

print("── (a) 冻结 fixture：content_sha256 常量 ──")

_actual_a = bibdb.content_sha256(FROZEN_A_TYPE, FROZEN_A_FIELDS)
check("(a) 冻结 fixture A（版式/值清理敏感）content_sha256 不变",
      _actual_a == FROZEN_A_SHA,
      f"实际 {_actual_a}；期望 {FROZEN_A_SHA}。{MIGRATE_HINT}")
_actual_b = bibdb.content_sha256(FROZEN_B_TYPE, FROZEN_B_FIELDS)
check("(a) 冻结 fixture B（WoS 垃圾字段敏感）content_sha256 不变",
      _actual_b == FROZEN_B_SHA,
      f"实际 {_actual_b}；期望 {FROZEN_B_SHA}。{MIGRATE_HINT}")
check("(a) fixture 的规范化结果：WoS 字段被丢、pages/DOI 被清理",
      bibdb.canonical_fields(FROZEN_B_FIELDS)
      == {"author": "Doe, Jane", "journal": "Studia Logica", "pages": "1--10",
          "title": "The Limits of Logic", "year": "2019"}
      and bibdb.canonical_fields(FROZEN_A_FIELDS).get("doi") == "10.1000/abc123"
      and bibdb.canonical_fields(FROZEN_A_FIELDS).get("pages") == "45--67",
      str(bibdb.canonical_fields(FROZEN_B_FIELDS)))


# ─────────────────────────────────────────────────────────────────────────
# (b) 合成库：stale-sha → dry-run 不写 / --apply 修复 / lint error 归零 / 幂等
# ─────────────────────────────────────────────────────────────────────────

SOURCE = {"kind": bibdb.KIND_PUBLISHED, "issue": "2026年第2期(87)",
          "slug": "01 Test_Author", "file": "reference.bib"}

STALE_DB_BIB = """@article{StaleOne2001,
  author  = {Alpha, Ann},
  title   = {Stale On Purpose},
  journal = {Journal of Logic},
  year    = {2001},
  pages   = {pp. 1-9}
}
@article{CleanTwo2002,
  author  = {Beta, Bob},
  title   = {Already Consistent},
  journal = {Studia Logica},
  year    = {2002}
}
@article{NoshaThree2003,
  author  = {Gamma, Carl},
  title   = {Missing Sha},
  journal = {Synthese},
  year    = {2003}
}
"""


def make_stale_db(tmp: str) -> str:
    """合成库：一条「旧规则留下的错误 sha」+ 一条正常 + 一条 sha 缺失。

    错误 sha 是合法 64 位十六进制（`0*64`）——这正是真实库 6 条 stale 的形状；
    缺 sha 那条覆盖「rehash 允许修 content_sha256 类结构问题」的 lenient 路径。
    """
    db = os.path.join(tmp, "bibliography.jsonl")
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text(STALE_DB_BIB, SOURCE, verified=True)
    store.save()
    by_key = {record["key"]: record for record in store.records()}
    by_key["StaleOne2001"]["content_sha256"] = "0" * 64
    by_key["StaleOne2001"]["updated_at"] = "2020-01-01T00:00:00+08:00"
    by_key["NoshaThree2003"]["content_sha256"] = ""
    by_key["NoshaThree2003"]["updated_at"] = "2020-01-02T00:00:00+08:00"
    store.dirty = True
    store.save()
    return db


# 用显式 TemporaryDirectory 句柄（而不是 with）：(b) 的 CLI 子进程要跨多段代码复用
# 同一个库文件，块尾统一 cleanup 更直观、也不会忘关。
_tmp_holder = tempfile.TemporaryDirectory(prefix="bibdb_rehash_")
tmp = _tmp_holder.name

db = make_stale_db(tmp)
before_bytes = read_bytes(db)
before_records = read_records(db)
before_by_key = {record["key"]: record for record in before_records}
before_fp = _fingerprint(before_records)
# 同一份 stale 库的副本：专门用来核对 `--apply --json` 的写回证据
db_json = write(os.path.join(tmp, "apply_json.jsonl"),
                before_bytes.decode("utf-8"))
expected = {
    record["key"]: bibdb.content_sha256(record["entry_type"], record["fields"])
    for record in before_records
}
store_lenient = bibdb.Store.load(db, lenient=True)
lint_before = store_lenient.lint()

check("(b) 合成库：apply 前 lint 报 stale-sha error（点名叫 StaleOne2001）",
      lint_before["counts"]["errors"] >= 1
      and any("StaleOne2001" in error and "content_sha256" in error
              for error in lint_before["errors"]),
      str(lint_before["errors"])[:300])
check("(b) 合成库：只有 StaleOne2001/NoshaThree2003 两条与当前规则不一致",
      {row["key"] for row in collect_rows(db)} == {"StaleOne2001", "NoshaThree2003"})


# (b1) 默认 dry-run + 显式 --dry-run：都一个字节不写
for label, extra in (("默认 dry-run", ()), ("显式 --dry-run", ("--dry-run",))):
    r = run_cli("rehash", "--db", db, *extra)
    out = r.stdout + r.stderr
    check(f"(b) {label}：退出 0、报出 2 条 stale、明确「未写库」",
          r.returncode == 0 and "2 条" in out and "dry-run" in out
          and "dry-run：未写库" in out,
          out[:300])
    check(f"(b) {label}：库文件逐字节不变（一个字节都不写）",
          read_bytes(db) == before_bytes)

r = run_cli("rehash", "--db", db, "--json")
payload = json.loads(r.stdout) if r.returncode == 0 else {}
check("(b) dry-run --json：dry_run=true / applied=false / stale=2 / records=3",
      payload.get("dry_run") is True and payload.get("applied") is False
      and payload.get("stale") == 2 and payload.get("records") == 3,
      json.dumps(payload, ensure_ascii=False)[:400])
check("(b) dry-run --json：changes 带 old/new（old 空串保留原样，供审计）",
      {change["key"]: (change["old"], change["new"])
       for change in payload.get("changes") or []}
      == {"StaleOne2001": ("0" * 64, expected["StaleOne2001"]),
          "NoshaThree2003": ("", expected["NoshaThree2003"])},
      json.dumps(payload.get("changes"), ensure_ascii=False)[:400])
check("(b) dry-run --json：sha256_before == sha256_after == 当前库 sha",
      payload.get("sha256_before") == bibdb.db_sha256(db)
      and payload.get("sha256_after") == payload.get("sha256_before")
      and payload.get("updated_at") is None)
check("(b) 人读输出：old 为空的那条显示「（空）」（不是空白/None）",
      "（空）" in run_cli("rehash", "--db", db).stdout)

r = run_cli("rehash", "--db", db, "--dry-run", "--apply")
check("(b) --dry-run 与 --apply 互斥：退出 2、库文件不变",
      r.returncode == 2 and read_bytes(db) == before_bytes,
      (r.stdout + r.stderr)[:200])

# (b2) --apply：只改 content_sha256 + updated_at，lint error 归零
r = run_cli("rehash", "--db", db, "--apply")
out = r.stdout + r.stderr
check("(b) --apply：退出 0、输出「已重算并写回 2 条」",
      r.returncode == 0 and "已重算并写回 2 条" in out, out[:300])
after_bytes = read_bytes(db)
check("(b) --apply：库文件确实变了", after_bytes != before_bytes)
after_records = read_records(db)
after_by_key = {record["key"]: record for record in after_records}
check("(b) --apply：除 content_sha256/updated_at 外，整条记录（含 fields/sources/"
      "verified/verified_at/method/created_at/aliases/conflicts）一字不动",
      _fingerprint(after_records) == before_fp)
check("(b) --apply：两条 stale 的 sha 都等于按当前规则重算的值",
      all(after_by_key[key]["content_sha256"] == expected[key]
          for key in ("StaleOne2001", "NoshaThree2003")),
      str({key: after_by_key[key]["content_sha256"] for key in
           ("StaleOne2001", "NoshaThree2003")}))
check("(b) --apply：被改的两条 updated_at 刷新；没 stale 的 CleanTwo2002 整条不变",
      after_by_key["StaleOne2001"]["updated_at"]
      != before_by_key["StaleOne2001"]["updated_at"]
      and after_by_key["NoshaThree2003"]["updated_at"]
      != before_by_key["NoshaThree2003"]["updated_at"]
      and after_by_key["CleanTwo2002"] == before_by_key["CleanTwo2002"])
check("(b) --apply：不产生 .tmp-* / .bak 垃圾（原子写清理干净）",
      all(".tmp-" not in name and not name.endswith(".bak")
          for name in os.listdir(tmp)), str(os.listdir(tmp)))

store_after = bibdb.Store.load(db)          # 严格载入：结构必须完全合法
lint_after = store_after.lint()
check("(b) --apply 后 lint error 归零（真实库门禁的同一判据）",
      lint_after["counts"]["errors"] == 0 and lint_after["ok"] is True,
      str(lint_after["errors"])[:300])
check("(b) --apply 后逐条 sha 与字段一致（0 条 stale）", collect_rows(db) == [])

# apply 的 --json 证据：在 stale 库的副本上跑，核对写回前后 sha 与 updated_at
r = run_cli("rehash", "--db", db_json, "--apply", "--json")
apply_payload = json.loads(r.stdout) if r.returncode == 0 else {}
check("(b) apply --json：applied=true / dry_run=false / stale=2 / updated_at 非空",
      r.returncode == 0 and apply_payload.get("applied") is True
      and apply_payload.get("dry_run") is False
      and apply_payload.get("stale") == 2
      and isinstance(apply_payload.get("updated_at"), str)
      and apply_payload["updated_at"], json.dumps(apply_payload,
                                                  ensure_ascii=False)[:400])
check("(b) apply --json：sha256_before/after 是写回前后的真实库 sha",
      apply_payload.get("sha256_before") == hashlib.sha256(before_bytes).hexdigest()
      and apply_payload.get("sha256_after") == bibdb.db_sha256(db_json)
      and apply_payload.get("sha256_before") != apply_payload.get("sha256_after"))
check("(b) apply --json：notes 点明「改规范化规则必须迁移库 + 更新 fixture」",
      "rehash" in (apply_payload.get("notes") or "")
      and "test_bibdb_rehash" in (apply_payload.get("notes") or ""))

r = run_cli("rehash", "--db", db, "--apply", "--json")
payload2 = json.loads(r.stdout) if r.returncode == 0 else {}
check("(b) 二次 --apply 幂等：applied=false / stale=0 / 库字节不变",
      r.returncode == 0 and payload2.get("stale") == 0
      and payload2.get("applied") is False and read_bytes(db) == after_bytes
      and payload2.get("sha256_before") == payload2.get("sha256_after"),
      json.dumps(payload2, ensure_ascii=False)[:300])
check("(b) 二次 --apply 人读输出「0 条需迁移」",
      "0 条需迁移" in run_cli("rehash", "--db", db).stdout)

# (c) 要拿一条合法记录造坏库：先留一份，再释放本轮临时目录
good_line = json.dumps(read_records(db)[0], ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"))
_tmp_holder.cleanup()


# ─────────────────────────────────────────────────────────────────────────
# (c) 失败不留半库：库不存在 / 坏 JSON 行 / 写盘失败
# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 失败不留半库：拒绝执行 + 原子写 ──")

with tempfile.TemporaryDirectory(prefix="bibdb_rehash_bad_") as tmp:
    missing = os.path.join(tmp, "no_such.jsonl")
    r = run_cli("rehash", "--db", missing, "--apply")
    check("(c) 库不存在：退出 2、**不会**悄悄新建库文件",
          r.returncode == 2 and not os.path.exists(missing),
          (r.stdout + r.stderr)[:200])

    # 坏 JSON 行（rehash 若照常写回会把这行悄悄丢掉）→ 必须拒绝且库字节不变
    broken = write(os.path.join(tmp, "broken.jsonl"),
                   good_line + "\n{ this is not json\n")
    broken_bytes = read_bytes(broken)
    r = run_cli("rehash", "--db", broken, "--apply")
    check("(c) 库有坏 JSON 行：退出 2、报「结构问题」、库文件逐字节不变",
          r.returncode == 2 and "结构问题" in (r.stdout + r.stderr)
          and read_bytes(broken) == broken_bytes,
          (r.stdout + r.stderr)[:300])

    # 写盘失败注入：只读目录里的**stale** 库 → 退出 2、旧库完好、不留临时文件
    ro_dir = os.path.join(tmp, "readonly")
    stale_line = json.dumps({**json.loads(good_line), "content_sha256": "0" * 64},
                            ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"))
    ro_db = write(os.path.join(ro_dir, "bibliography.jsonl"), stale_line + "\n")
    frozen = read_bytes(ro_db)
    try:
        os.chmod(ro_dir, 0o500)
        r = run_cli("rehash", "--db", ro_db, "--apply")
        detail = (r.stdout + r.stderr)[:300]
        enforced = r.returncode != 0
        if not enforced:
            skip("(c) 写盘失败注入（只读目录）", "当前用户不受目录权限限制，无法注入失败")
        else:
            check("(c) 写盘失败：退出 2、旧库逐字节不变、失败消息说明「库未改动」",
                  r.returncode == 2 and read_bytes(ro_db) == frozen
                  and "库未改动" in (r.stdout + r.stderr), detail)
            check("(c) 写盘失败：不留 .tmp-* / .bak 垃圾",
                  all(".tmp-" not in name and not name.endswith(".bak")
                      for name in os.listdir(ro_dir)),
                  str(os.listdir(ro_dir)))
    finally:
        os.chmod(ro_dir, 0o700)


# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过" + (f"（SKIP {len(skips)} 组）" if skips else ""))
print("失败 0 项（全部通过）")
sys.exit(0)
