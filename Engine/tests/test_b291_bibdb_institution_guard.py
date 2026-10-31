#!/usr/bin/env python3.14
r"""B-291：引用库机构名守卫 + lint 未倒装只读计数（hermetic：tempfile 造库）。

来源 `EditToolBox-notes/drafts/specs/b291-bibdb-institution-guard.md` C1–C4；证据
`/tmp/b248-1-20261030/report.md` §3（库→稿件回填 `bibdbapply.py:446` 无条件
`normalize_author_field`，实测 `Barcelona Neural Networking Center` → `Center, B. N. N.`）。

  (a) S1 守卫（`bibdbapply._render_library_candidates`，纯函数级）：
      7 个裸机构值 + 3 条拉丁机构名**逐字节不变**；`{...}` 花括号写法保留；
      `Oliver Board`（`_INSTITUTION_RE` 曾误判为人名以外）正常倒装；个人名
      （含 `\&`、逗号、带重音）与未守卫前 `normalize_author_field()` 输出一致。
  (b) 判据（`bibdb_reports.is_institution_author` / `_author_skip_reason`）：
      补罗马字日文机构词（长音/无长音）、`Board of …` 组合才算机构、`Oliver Board`
      不再命中；机构值跳过原因是「疑似机构/组织作者（不推断）」。
  (c) 端到端 lib→manuscript（`plan_library_fills()` + `apply_text()`）：机构值原样
      写进产物 bib；同为「库有值覆盖」，个人名仍按编辑部硬规则倒装。
  (d) S2 lint（`Store.lint` + `bibdb.py lint --json`）：合成库（机构 2、未倒装 3、
      正常 5）→ `counts.needs_inversion == {author:4, editor:1, institution:2}`、
      warnings 里出现「author/editor 未倒装 5 条（其中机构名 2 条）」、`warning_items`
      有 `needs_inversion` 项、lint 只读（库 sha 不变）、exit code 仍 0/1 口径。

真实库内容不写入夹具；不联网、不读 `bib_db/`。
用法：cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
      tests/test_b291_bibdb_institution_guard.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb, bibdb_reports, bibnorm  # noqa: E402

CLI = os.path.join(_ENGINE, "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=_ENGINE + os.pathsep + os.path.join(_ENGINE, "vendor"),
           PYTHONIOENCODING="utf-8")

FAILS: list[str] = []
SKIPS: list[str] = []


def check(name: str, cond, detail="") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def source(key: str) -> dict:
    return {"kind": bibdb.KIND_PUBLISHED, "issue": "2023年第1期(68)",
            "slug": "01 Fixture", "file": "fixture.bib", "key": key,
            "entry_sha256": "0" * 64}


def record(key: str, *, entry_type: str = "book", fields: dict) -> dict:
    """按 `validate_record` 要求造一条合格记录（verified=true + 正确 content_sha256）。"""
    fields = dict(sorted(fields.items()))
    return {
        "key": key, "version": 1, "entry_type": entry_type, "fields": fields,
        "verified": True, "verified_at": "2026-10-18T00:00:00Z", "method": "published-bib",
        "content_sha256": bibdb.content_sha256(entry_type, fields),
        "sources": [source(key)], "aliases": [], "conflicts": [],
    }


def write_db(path: str, records: "list[dict]") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    return path


def digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def candidates(value: str, *, role: str = "author", etype: str = "book") -> str:
    """库记录字段 → `_render_library_candidates()` 渲染终值（只取该字段）。"""
    from slcn import bibdbapply
    out = dict(bibdbapply._render_library_candidates(etype, {role: value}))
    return out.get(role, "")


def lib_apply(body: str, lib_fields: dict, *, etype: str = "book", key: str = "K"):
    """端到端：库记录字段 → `plan_library_fills()` → `apply_text()`（产物 bib 文本）。"""
    from slcn import bibdbapply
    match = {"status": bibdb.HIT_SAFE, "hit": "doi", "title_similarity": 1.0,
             "record": {"entry_type": etype, "fields": dict(lib_fields)}}
    plans, stats = bibdbapply.plan_library_fills(None, [(etype, key, body)], {key: match})
    return bibdbapply.apply_text(f"@{etype}{{{key},\n{body}\n}}\n", plans)


TMP = tempfile.mkdtemp(prefix="b291_institution_guard_")

# 真实库里的 7 个裸机构值 + 3 条拉丁机构名（B-248 report.md §1），夹具全合成、只借字符串
INSTITUTIONS = (
    "Barcelona Neural Networking Center",      # Center2020
    "Kokumin Bunko Kankōkai",                  # Koyanagi1924（长音写法）
    "Waseda Daigaku Henshūbu",                 # Makino1911 / 1911a
    "Nihon Eigaku Shiryō Kankōkai",            # NihonEigaku1982 / 1982a
    "Shūeisha",                                # Shueisha1993
    "Toyama房 Henshūbu",                       # Hattori1913（含 CJK 整串）
)
LATIN_INSTITUTIONS = (
    "Barcelona Neural Networking Center",
    "{Nature Editorial}",                      # Editorial2023（花括号写法）
    "{Oxford University Press}",               # c17-6（花括号写法）
)

# ══════════════════════════════════════════════════════════════════════════
# (a) S1 守卫：机构值逐字节不变 / 个人名行为不变 / 花括号保留
# ══════════════════════════════════════════════════════════════════════════
print("\n── (a) S1 守卫：机构值原样保留 ──")
for value in INSTITUTIONS:
    got = candidates(value)
    check(f"(a) 机构值原样：{value!r}", got == value, got)
for value in LATIN_INSTITUTIONS:
    got = candidates(value)
    check(f"(a) 拉丁机构名原样：{value!r}", got == value, got)
check("(a) 花括号写法保留：{Barcelona Neural Networking Center}",
      candidates("{Barcelona Neural Networking Center}")
      == "{Barcelona Neural Networking Center}")
check("(a) editor 角色同样守卫：Kokumin Bunko Kankōkai",
      candidates("Kokumin Bunko Kankōkai", role="editor") == "Kokumin Bunko Kankōkai")
check("(a) 机构名不再出现 `Center, B. N. N.` 式",
      "Center, B. N. N." not in candidates("Barcelona Neural Networking Center"))

print("\n── (a) 个人名：守卫不影响既有倒装 ──")
PERSONS = ("John Smith", "Oliver Board", "van der Waals", "Jean-Paul Sartre",
           "Smith, J.", "王小明", r"{\'E}mile Zola", "Smith \\& Jones")
for value in PERSONS:
    from slcn import bibdbapply as _apply
    expected = _apply._render_library_text(bibnorm.normalize_author_field(value))
    got = candidates(value)
    check(f"(a) 个人名与 normalize 同输出：{value!r}", got == expected, (got, expected))
check("(a) Board 真人名正常倒装：Oliver Board → Board, O.",
      candidates("Oliver Board") == "Board, O.", candidates("Oliver Board"))
check("(a) 普通人名正常倒装：John Smith → Smith, J.",
      candidates("John Smith") == "Smith, J.", candidates("John Smith"))

# ══════════════════════════════════════════════════════════════════════════
# (b) 判据：is_institution_author / _author_skip_reason
# ══════════════════════════════════════════════════════════════════════════
print("\n── (b) 判据：机构名 vs 人名 ──")
INST_REASON = "疑似机构/组织作者（不推断）"
CJK_REASON = "含中文/日文（不倒装）"
for value in INSTITUTIONS:
    check(f"(b) 机构命中：{value!r}", bibdb_reports.is_institution_author(value))
    reason = bibdb_reports._author_skip_reason(value)
    # 含 CJK 整串的值先命中「含中文/日文」判据（口径不变），纯罗马字值命中机构判据
    expected_reason = CJK_REASON if any("\u3040" <= ch <= "\u9fff" for ch in value) \
        else INST_REASON
    check(f"(b) 机构跳过原因：{value!r}", reason == expected_reason, reason)
for value in ("Kankokai", "Henshubu", "Shueisha"):     # 无长音变体
    check(f"(b) 无长音变体命中：{value!r}", bibdb_reports.is_institution_author(value))
check("(b) Board 单独不命中：Oliver Board", not bibdb_reports.is_institution_author("Oliver Board"))
check("(b) Board 单人跳过原因为空：Oliver Board",
      bibdb_reports._author_skip_reason("Oliver Board") == "")
check("(b) Board of … 组合命中：Board of Editors",
      bibdb_reports.is_institution_author("Board of Editors"))
check("(b) 机构词按词边界：KankokaiX 不命中",
      not bibdb_reports.is_institution_author("KankokaiX"))
check("(b) 人名 \\& 不误判：Smith \\& Jones",
      not bibdb_reports.is_institution_author("Smith \\& Jones"))
check("(b) 分号分隔机构仍命中：Kankōkai; Henshūbu",
      bibdb_reports.is_institution_author("Kankōkai; Henshūbu"))
check("(b) 旧拉丁机构词仍命中：World Health Organization",
      bibdb_reports.is_institution_author("World Health Organization"))

# ══════════════════════════════════════════════════════════════════════════
# (c) 端到端：lib → manuscript 回填
# ══════════════════════════════════════════════════════════════════════════
print("\n── (c) 端到端：库值回填产物 bib ──")
BODY = "  author = {Placeholder Name},\n  title = {A Sample Work},\n  year = {2020},\n"
out, applied = lib_apply(BODY, {"author": "Barcelona Neural Networking Center"})
check("(c) 机构 author 原样回填（不再倒装）",
      "Barcelona Neural Networking Center" in out and "Center, B. N. N." not in out, out)
check("(c) 机构回填算 1 条应用", len(applied or []) == 1, applied)
out, _applied = lib_apply(BODY, {"author": "Oliver Board"})
check("(c) 个人名仍正常倒装：Board, O.", "Board, O." in out, out)
out, _applied = lib_apply(BODY, {"editor": "Shūeisha"}, etype="collection")
check("(c) editor 机构值原样回填", "editor = {Shūeisha}" in out, out)

# ══════════════════════════════════════════════════════════════════════════
# (d) S2 lint：合成库计数 + 只读 + exit code
# ══════════════════════════════════════════════════════════════════════════
print("\n── (d) lint 未倒装计数 ──")
ROWS = [
    record("InstA", fields={"author": "Barcelona Neural Networking Center",
                            "title": "T1", "year": "2020"}),
    record("InstB", fields={"author": "X", "editor": "Kokumin Bunko Kankōkai",
                            "title": "T2", "year": "2021"}),
    record("P1", fields={"author": "John Smith", "title": "T3", "year": "2022"}),
    record("P2", fields={"author": "Maria Garcia Lopez", "title": "T4", "year": "2023"}),
    record("P3", fields={"author": "Alice Brown", "title": "T5", "year": "2024"}),
    record("N1", fields={"author": "Smith, J.", "title": "T6", "year": "2001"}),
    record("N2", fields={"author": "王小明", "title": "T7", "year": "2002"}),
    record("N3", fields={"author": "Doe, Jane", "title": "T8", "year": "2003"}),
    record("N4", fields={"author": "{Nature Editorial}", "title": "T9", "year": "2004"}),
    record("N5", fields={"author": "Lee, K.", "title": "T10", "year": "2005"}),
]
db = write_db(os.path.join(TMP, "counts", "bibliography.jsonl"), ROWS)
sha0 = digest(db)
store = bibdb.Store.load(db)
payload = store.lint()
counts = payload["counts"]
check("(d) 合成库 lint ok / errors 0", payload["ok"] and not payload["errors"], payload["errors"])
check("(d) counts.needs_inversion == {author:4, editor:1, institution:2}",
      counts.get("needs_inversion") == {"author": 4, "editor": 1, "institution": 2},
      counts.get("needs_inversion"))
check("(d) 顶层 needs_inversion 与 counts 内同值",
      payload.get("needs_inversion") == counts.get("needs_inversion"),
      payload.get("needs_inversion"))
line = "author/editor 未倒装 5 条（author 4 / editor 1；其中机构名 2 条）"
check("(d) warnings 出现未倒装统计行", line in payload["warnings"], payload["warnings"])
items = [item for item in payload["warning_items"] if item["code"] == "needs_inversion"]
check("(d) warning_items 有 needs_inversion 项且 count=5",
      len(items) == 1 and items[0]["count"] == 5, items)
check("(d) needs_inversion 项 keys_sample 含机构记录",
      "InstA" in items[0]["keys_sample"] and "InstB" in items[0]["keys_sample"],
      items[0]["keys_sample"] if items else None)
check("(d) counts.warnings == len(warnings)（B-143 一致性不变）",
      counts["warnings"] == len(payload["warnings"]))
check("(d) lint 只读：库 sha 不变", digest(db) == sha0)

print("\n── (d) CLI：--json / 文本行 / exit code 0 ──")
proc = subprocess.run([sys.executable, CLI, "lint", "--db", db, "--json"],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", cwd=_ENGINE, env=ENV)
check("(d) CLI lint --json rc0", proc.returncode == 0, proc.stderr[-300:])
parsed = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
check("(d) CLI JSON 带 counts.needs_inversion",
      parsed.get("counts", {}).get("needs_inversion")
      == {"author": 4, "editor": 1, "institution": 2},
      parsed.get("counts", {}).get("needs_inversion"))
check("(d) CLI JSON 顶层 needs_inversion 同值（spec C4 字面）",
      parsed.get("needs_inversion") == {"author": 4, "editor": 1, "institution": 2},
      parsed.get("needs_inversion"))
proc_text = subprocess.run([sys.executable, CLI, "lint", "--db", db],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=_ENGINE, env=ENV)
check("(d) CLI 文本输出含统计行", line in proc_text.stdout, proc_text.stdout[-400:])
check("(d) CLI 文本 lint rc0（新 warning 不改 exit code）", proc_text.returncode == 0)
check("(d) CLI lint 只读：库 sha 不变", digest(db) == sha0)

broken = [dict(row) for row in ROWS]
broken[0] = dict(broken[0], content_sha256="0" * 64)
db_broken = write_db(os.path.join(TMP, "broken", "bibliography.jsonl"), broken)
proc_bad = subprocess.run([sys.executable, CLI, "lint", "--db", db_broken, "--json"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=_ENGINE, env=ENV)
check("(d) 有 error 的库 lint 仍 rc1（0/1 口径不变）", proc_bad.returncode == 1,
      proc_bad.returncode)
check("(d) 有 error 的库仍给出 needs_inversion 计数（只读、与 error 正交）",
      json.loads(proc_bad.stdout)["counts"]["needs_inversion"]["institution"] == 2
      if proc_bad.stdout.strip().startswith("{") else False)

# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("失败 %d 项%s" % (len(FAILS), ("：" + "；".join(FAILS)) if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
