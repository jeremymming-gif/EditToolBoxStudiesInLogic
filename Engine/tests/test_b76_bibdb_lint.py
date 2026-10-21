#!/usr/bin/env python3.14
"""B-143（批76）：引用库 lint 口径 + 结构化 warnings 契约测试（hermetic：tempfile 造库）。

覆盖 spec `EditToolBox-notes/drafts/specs/b76-b143-bibdb-ux.md` §2.4 / §2.7：

  (a) 「字段冲突」warning 只计 **pending**（判据 = `resolved_at`，与 `conflicts --json` 同源），
      文本里同时给「历史已裁决」数；pending=0 时不再写自相矛盾的「待裁决 0 条…」句，改
      「字段冲突：待裁决 0 条 / 历史已裁决 N 条记录（M 处差异，审计留痕，无需处理）」。
  (b) `warning_items` 结构化镜像：每项 `{code,registry,detail,action,count,keys_sample}`；
      conflicts 项另带 pending/resolved 拆分；registry ∈ 固定分类集合；keys_sample ≤ 5。
  (c) 向后兼容：老 JSON 键（ok/errors/warnings/counts）与老 counts 键（conflicts=全量、
      near_duplicates…）仍在且语义不变；`conflicts --json` 输出不变；老 warnings 字符串列表
      仍是 `list[str]`（GUI 旧引擎回退用）。
  (d) 默认零变化/只读：lint 前后库逐字节不变；CLI 人读输出口径与 JSON 一致；rc 0。

跑法（本机必须 Python 3.14 framework + PYTHONPATH=vendor）：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b76_bibdb_lint.py
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
ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join(
    [str(_ENGINE), str(_ENGINE / "vendor"), ENV.get("PYTHONPATH", "")]).strip(os.pathsep)

pass_n = 0
fails: list[str] = []

REGISTRY_ORDER = ("duplicates", "near_duplicates", "conflicts", "missing_field",
                  "unknown_entry_type", "alias", "unverified", "other")
ITEM_KEYS = {"code", "registry", "detail", "action", "count", "keys_sample"}


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def source() -> dict:
    return {"kind": bibdb.KIND_PUBLISHED, "issue": "2023年第1期(68)",
            "slug": "01 Fixture", "file": "fixture.bib"}


def record(key: str, *, entry_type: str = "article", fields: dict,
           conflicts: "list[dict] | None" = None, aliases: "list[str] | None" = None) -> dict:
    """按 `validate_record` 要求造一条合格记录（verified=true + 正确 content_sha256）。"""
    return {
        "key": key,
        "version": 1,
        "entry_type": entry_type,
        "fields": fields,
        "verified": True,
        "verified_at": "2026-10-18T00:00:00Z",
        "method": "published-bib",
        "content_sha256": bibdb.content_sha256(entry_type, fields),
        "sources": [source()],
        "aliases": list(aliases or []),
        "conflicts": list(conflicts or []),
    }


def pending_conflict(field: str = "journal") -> dict:
    return {"field_diff": {field: {"existing": "Old", "incoming": "New"}},
            "resolution": "kept-existing"}   # 导入策略，不算裁决：resolved_at 为空 = 待裁决


def resolved_conflict(field: str = "journal") -> dict:
    return {"field_diff": {field: {"existing": "Old", "incoming": "New"}},
            "resolution": "adopted", "resolved_at": "2026-10-18T02:00:00Z"}


def write_db(path: str, records: "list[dict]") -> str:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    return path


def digest(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def parse_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


# ─────────────────────────────────────────────────────────────────────────
# 夹具 A：既有 pending、又有历史已裁决（还有重复/缺字段/entry_type/alias 各一类）
# ─────────────────────────────────────────────────────────────────────────
def make_mixed_db(path: str) -> str:
    complete = {"author": "Alpha, Ann", "title": "Alpha Work", "year": "2020"}
    return write_db(path, [
        # 1 条待裁决冲突
        record("Alpha2020", fields=complete, conflicts=[pending_conflict()]),
        # 1 条历史已裁决冲突（year 缺）
        record("Beta2019", fields={"author": "Beta, Bob", "title": "Beta Work"},
               conflicts=[resolved_conflict("publisher")]),
        # 1 待裁决 + 1 已裁决（缺 author/editor、缺 year）
        record("Gamma2018", fields={"title": "Gamma Work"},
               conflicts=[pending_conflict(), resolved_conflict("pages")]),
        # 无冲突、字段完整
        record("Delta2017", fields={"author": "Delta, Dan", "title": "Delta Work",
                                    "year": "2017"}),
        # 内容完全相同的两条（不同 key）→ duplicate_content
        record("DupA", fields={"author": "Same, Sam", "title": "Same Work", "year": "2001"}),
        record("DupB", fields={"author": "Same, Sam", "title": "Same Work", "year": "2001"}),
        # 缺 title
        record("Eta2014", fields={"author": "Eta, Eve", "year": "2014"}),
        # entry_type 笔误
        record("Epsilon2022", entry_type="rticle",
               fields={"author": "Epsilon, Eve", "title": "Epsilon Work", "year": "2022"}),
        # alias 自身 + 重复
        record("Zeta2023", fields={"author": "Zeta, Zed", "title": "Zeta Work",
                                   "year": "2023"}, aliases=["Zeta2023", "Zeta2023"]),
    ])


# ─────────────────────────────────────────────────────────────────────────
# 夹具 B：全部冲突都已裁决（当前真实库的口径：pending=0 / resolved>0）
# ─────────────────────────────────────────────────────────────────────────
def make_all_resolved_db(path: str) -> str:
    return write_db(path, [
        record("Theta2015", fields={"author": "Theta, Tim", "title": "Theta Work",
                                   "year": "2015"},
               conflicts=[resolved_conflict(), resolved_conflict("volume")]),
    ])


# ─────────────────────────────────────────────────────────────────────────
# (a) lint 口径：字段冲突只计 pending，文本含历史已裁决数
# ─────────────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="b76_lint_") as tmp:
    mixed = make_mixed_db(os.path.join(tmp, "mixed.jsonl"))
    before = digest(mixed)
    store = bibdb.Store.load(mixed)
    result = store.lint()
    after = digest(mixed)

    check("(a) lint 只读：库逐字节不变", before == after)
    check("(a) lint 无 error（合成库结构合格）", result["ok"] and not result["errors"],
          str(result["errors"]))

    # 真实口径：冲突 3 条记录 / 4 处差异；待裁决 2 条记录 / 2 处；已裁决 2 条记录 / 2 处
    counts = result["counts"]
    check("(a) counts.conflicts 老键仍是**全量**（向后兼容，语义不变）",
          counts["conflicts"] == {"records": 3, "total": 4}, str(counts["conflicts"]))
    check("(a) counts.conflicts_pending 拆分（只计 resolved_at 为空的冲突）",
          counts["conflicts_pending"] == {"records": 2, "total": 2},
          str(counts.get("conflicts_pending")))
    check("(a) counts.conflicts_resolved 拆分",
          counts["conflicts_resolved"] == {"records": 2, "total": 2},
          str(counts.get("conflicts_resolved")))
    check("(a) pending 拆分 records+resolved 拆分 records ≥ 全量 records（同记录可两侧都有）",
          counts["conflicts_pending"]["records"] + counts["conflicts_resolved"]["records"]
          >= counts["conflicts"]["records"])

    conflict_warning = [w for w in result["warnings"] if "字段冲突" in w]
    check("(a) 有且只有一条「字段冲突」warning", len(conflict_warning) == 1,
          str(conflict_warning))
    text = conflict_warning[0] if conflict_warning else ""
    check("(a) warning 只计 pending：2 条记录 / 2 处差异（不再报全量 3/4）",
          "待裁决：2 条记录 / 2 处差异" in text and "待裁决：3 条记录" not in text, text)
    check("(a) warning 文本同时给历史已裁决 2 条记录 / 2 处差异",
          "历史已裁决 2 条记录 / 2 处差异" in text, text)

    # 全部已裁决 → “无需处理”句，不出现自相矛盾的「字段冲突待裁决：0 条…」
    resolved_db = make_all_resolved_db(os.path.join(tmp, "resolved.jsonl"))
    resolved_result = bibdb.Store.load(resolved_db).lint()
    resolved_counts = resolved_result["counts"]
    check("(a) pending=0：counts.conflicts_pending = {0,0}",
          resolved_counts["conflicts_pending"] == {"records": 0, "total": 0},
          str(resolved_counts.get("conflicts_pending")))
    check("(a) pending=0：counts.conflicts_resolved 保留历史",
          resolved_counts["conflicts_resolved"] == {"records": 1, "total": 2},
          str(resolved_counts.get("conflicts_resolved")))
    resolved_warning = [w for w in resolved_result["warnings"] if "字段冲突" in w]
    check("(a) pending=0：warning 文本为「字段冲突：待裁决 0 条 / 历史已裁决 1 条记录…无需处理」",
          len(resolved_warning) == 1
          and resolved_warning[0].startswith("字段冲突：待裁决 0 条 / 历史已裁决 1 条记录")
          and "无需处理" in resolved_warning[0] and "字段冲突待裁决" not in resolved_warning[0],
          str(resolved_warning))

# ─────────────────────────────────────────────────────────────────────────
# (b) warning_items 结构化契约
# ─────────────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="b76_items_") as tmp:
    mixed = make_mixed_db(os.path.join(tmp, "mixed.jsonl"))
    result = bibdb.Store.load(mixed).lint()
    warnings = result["warnings"]
    items = result.get("warning_items")
    check("(b) warning_items 存在且为数组", isinstance(items, list) and bool(items),
          type(items).__name__)
    items = items or []
    check("(b) warning_items 与 warnings 条目一一对应（结构化镜像）",
          len(items) == len(warnings), f"{len(items)} vs {len(warnings)}")
    check("(b) warning_items 每项必含 code/registry/detail/action/count/keys_sample",
          all(ITEM_KEYS <= set(item) for item in items),
          str([sorted(ITEM_KEYS - set(item)) for item in items if not ITEM_KEYS <= set(item)]))
    check("(b) registry 全在固定分类集合内",
          all(item["registry"] in REGISTRY_ORDER for item in items),
          str(sorted({item["registry"] for item in items})))
    check("(b) keys_sample 是 ≤5 个字符串（不把 219 条缺 title 全塞进 JSON）",
          all(isinstance(item["keys_sample"], list)
              and len(item["keys_sample"]) <= 5
              and all(isinstance(key, str) for key in item["keys_sample"])
              for item in items))
    check("(b) detail 与 warnings 字符串同文（GUI 回退/人读不漂移）",
          [item["detail"] for item in items] == warnings)
    check("(b) action 非空（每条都能给建议操作）",
          all(item["action"].strip() for item in items))

    by_registry: dict[str, list[dict]] = {}
    for item in items:
        by_registry.setdefault(item["registry"], []).append(item)
    check("(b) 分类齐全：duplicates / conflicts / missing_field / unknown_entry_type / alias",
          {"duplicates", "conflicts", "missing_field", "unknown_entry_type",
           "alias"} <= set(by_registry), str(sorted(by_registry)))

    conflict_items = by_registry.get("conflicts") or []
    check("(b) conflicts 项 code=conflict_pending", len(conflict_items) == 1
          and conflict_items[0]["code"] == "conflict_pending")
    conflict_item = conflict_items[0] if conflict_items else {}
    check("(b) conflicts 项带 pending/resolved 四数（GUI「待裁决 N / 历史已裁决 M」数据源）",
          conflict_item.get("pending_records") == 2
          and conflict_item.get("pending_total") == 2
          and conflict_item.get("resolved_records") == 2
          and conflict_item.get("resolved_total") == 2,
          str({k: conflict_item.get(k) for k in
               ("pending_records", "pending_total", "resolved_records", "resolved_total")}))
    check("(b) conflicts 项 count = 待裁决处差异数（0/2，不是全量 4）",
          conflict_item.get("count") == 2, str(conflict_item.get("count")))

    dup_items = by_registry.get("duplicates") or []
    check("(b) duplicates 项说明「P6 另卡、本页只报告、不自动合并」",
          len(dup_items) == 1 and "P6" in dup_items[0]["action"]
          and "不自动合并" in dup_items[0]["action"], str(dup_items))
    check("(b) duplicates 项 count=2、keys_sample 含两个 key",
          len(dup_items) == 1 and dup_items[0]["count"] == 2
          and sorted(dup_items[0]["keys_sample"]) == ["DupA", "DupB"], str(dup_items))

    missing = by_registry.get("missing_field") or []
    check("(b) missing_field 三个 code 齐全（title/author/year）",
          {item["code"] for item in missing} == {"missing_title", "missing_author",
                                                 "missing_year"},
          str(sorted(item["code"] for item in missing)))

    # pending=0 时 conflicts 项应标「无需处理（历史留痕）」
    resolved_db = make_all_resolved_db(os.path.join(tmp, "resolved.jsonl"))
    resolved_items = bibdb.Store.load(resolved_db).lint()["warning_items"]
    resolved_conflict_items = [item for item in resolved_items
                               if item["registry"] == "conflicts"]
    check("(b) pending=0 的 conflicts 项 action 标「无需处理」",
          len(resolved_conflict_items) == 1
          and "无需处理" in resolved_conflict_items[0]["action"],
          str(resolved_conflict_items))

# ─────────────────────────────────────────────────────────────────────────
# (c) 向后兼容 + CLI --json / 人读输出
# ─────────────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="b76_cli_") as tmp:
    mixed = make_mixed_db(os.path.join(tmp, "mixed.jsonl"))
    before = digest(mixed)
    human = run_cli("lint", "--db", mixed)
    human_bytes = digest(mixed)
    check("(c) CLI 人读 lint rc0、库只读", human.returncode == 0 and before == human_bytes,
          f"rc={human.returncode}")
    human_text = human.stdout
    check("(c) 人读输出摘要也用 pending 口径 + 历史已裁决数",
          "字段冲突：待裁决 2 条记录 / 2 处差异（历史已裁决 2 条记录 / 2 处差异）"
          in human_text, human_text.strip().splitlines()[-2:])
    check("(c) 人读输出仍逐条打印 warnings 字符串",
          "! " in human_text and "内容完全相同的多条记录" in human_text)

    result = run_cli("lint", "--json", "--db", mixed)
    payload = parse_json(result)
    check("(c) CLI lint --json rc0 且 JSON 合法", result.returncode == 0 and bool(payload),
          result.stderr[-200:])
    check("(c) CLI --json 老键全在：ok/errors/warnings/counts",
          {"ok", "errors", "warnings", "counts"} <= set(payload))
    check("(c) CLI --json 老 counts 键全在",
          {"records", "raw_lines", "verified", "unverified", "errors", "warnings",
           "conflicts", "near_duplicates"} <= set(payload.get("counts") or {}),
          str(sorted(payload.get("counts") or {})))
    check("(c) CLI --json warnings 仍是字符串数组（旧 GUI 回退可读）",
          isinstance(payload.get("warnings"), list)
          and all(isinstance(text, str) for text in payload["warnings"]))
    check("(c) CLI --json 新增 warning_items 与 conflicts_pending/resolved 拆分",
          isinstance(payload.get("warning_items"), list)
          and "conflicts_pending" in payload["counts"]
          and "conflicts_resolved" in payload["counts"])
    check("(c) CLI --json counts.warnings 与 warnings 条数一致",
          payload["counts"]["warnings"] == len(payload["warnings"]))
    check("(c) CLI --json 库只读（前后逐字节同）", digest(mixed) == before)

# ─────────────────────────────────────────────────────────────────────────
# (d) conflicts --json 输出不变（B-143 只动 lint 汇总，禁碰 conflicts）
# ─────────────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="b76_conf_") as tmp:
    mixed = make_mixed_db(os.path.join(tmp, "mixed.jsonl"))
    result = run_cli("conflicts", "--json", "--db", mixed)
    payload = parse_json(result)
    check("(d) conflicts --json rc0 且老键全在：db/db_sha256/totals/items",
          result.returncode == 0
          and {"db", "db_sha256", "totals", "items"} <= set(payload), result.stderr[-200:])
    check("(d) conflicts totals 口径不变（pending 2 / resolved 2 / total 4）",
          payload.get("totals") == {"pending": 2, "resolved": 2, "total": 4},
          str(payload.get("totals")))
    check("(d) conflicts items 含全部冲突（4 条，C 编号稳定）",
          len(payload.get("items") or []) == 4
          and [item["stable_no"] for item in payload["items"]] == [1, 2, 3, 4])

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
