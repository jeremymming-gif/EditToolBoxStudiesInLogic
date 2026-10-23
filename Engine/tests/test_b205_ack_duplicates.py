#!/usr/bin/env python3.14
r"""B-205：`ack-duplicates` 命令契约测试（hermetic：tempfile 造库 + sidecar）。

设计卡 `EditToolBox-notes/drafts/specs/b205-ack-duplicate-gui.md` §2/§4：GUI「确认不同…」
按钮背后只有一条引擎命令 `bibdb.py ack-duplicates --code near_duplicates|duplicate_content
--keys A,B[,C] [--reason TEXT] [--apply] [--json]`。本套件按 CLI 契约逐条钉死：

  ① 默认 dry-run：不建/不改 sidecar，JSON 报「计划」（added=true / written=false）；
  ② `--apply`：sidecar 出现条目、`lint` near 计数下降 / 该 pair 消失、库字节 sha 不变；
  ③ 幂等：再 apply（含用 alias 写 key）→ rc0 / added=false / written=false / sidecar 字节不变；
  ④ key 解析：alias → canonical；输入去重 + 顺序无关；写盘用 canonical **排序**；
  ⑤ 非当前近重复对（含 3 个 key / 不存在的 key / 同一条记录的两个 key）→ rc2 零写入；
  ⑥ `duplicate_content`：整组 ack 后该组 warning 消失（部分 key → rc2 零写入）；
  ⑦ 既有条目保留：第二条 ack 追加时，第一条原文（reason/decided_at/额外字段）一字不动；
  ⑧ 坏 sidecar（坏 JSON / version≠1 / 条目非法）→ rc2 且 sidecar 字节不变（绝不覆盖）；
  ⑨ CLI JSON 字段集 / exit code / 人读模式一句摘要；
  ⑩ 库不存在、坏 keys 用法（空 / 单 key / 重复 canonical / 多指 alias / 非法 code）→ rc2。

跑法（本机必须 Python 3.14 framework + PYTHONPATH=vendor）：
    cd Engine && PYTHONPATH="$PWD:$PWD/vendor" tests/test_b205_ack_duplicates.py
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

CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep + str(_ENGINE / "vendor"),
           PYTHONIOENCODING="utf-8")

#: 写入 sidecar 的缺省理由（契约写死的文案）
ACK_REASON = "人工确认：两条不同条目（不再提示）"
#: `decided_at` = 本地 ISO 8601 到秒（带时区偏移）
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

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


# ══════════════════════════════════════════════════════════════════════════
# hermetic 夹具：与 test_b204_health_ignore 同一份库（near=2 对 / duplicate=2 组）
# ══════════════════════════════════════════════════════════════════════════


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


def base_records() -> "list[dict]":
    return [
        # 近重复 pair 1：Segerberg 书版 vs 博士论文（题名归一同；`Segerberg1971` 是后者 alias）
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
        # 近重复 pair 2：同作者/同年/题名 0.95
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


def ambiguous_records() -> "list[dict]":
    """在基础库上再加两条共享 alias `shared` 的记录（用于 alias 多指 → rc2）。"""
    return base_records() + [
        record("Alpha2020", fields={"author": "Alpha, Ann", "title": "Alpha Work",
                                    "year": "2020"}, aliases=["shared"]),
        record("Beta2020", fields={"author": "Beta, Bob", "title": "Beta Work",
                                   "year": "2020"}, aliases=["shared"]),
    ]


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


def read_sidecar(db: str) -> dict:
    with open(sidecar_path(db), encoding="utf-8") as handle:
        return json.load(handle)


def sidecar_bytes(db: str) -> bytes:
    return pathlib.Path(sidecar_path(db)).read_bytes()


def remove_sidecar(db: str) -> None:
    path = sidecar_path(db)
    if os.path.isfile(path):
        os.remove(path)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def parse_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


TMP = tempfile.mkdtemp(prefix="b205_ack_duplicates_")
_seq = 0


def new_db(*, records: "list[dict] | None" = None) -> str:
    """每个场景一个独立目录（互不污染），返回库路径。"""
    global _seq
    _seq += 1
    return write_db(os.path.join(TMP, f"case{_seq:02d}", "bibliography.jsonl"),
                    base_records() if records is None else records)


def lint_payload(db: str) -> tuple[int, dict]:
    result = run_cli("lint", "--db", db, "--json", "--with-records")
    return result.returncode, parse_json(result)


def near_item(payload: dict) -> dict:
    return next((item for item in payload.get("warning_items") or []
                 if item["code"] == "near_duplicates"), {})


def dup_items(payload: dict) -> "list[dict]":
    return [item for item in payload.get("warning_items") or []
            if item["code"] == "duplicate_content"]


def ack(db: str, code: str, keys: str, *extra: str) -> subprocess.CompletedProcess:
    return run_cli("ack-duplicates", "--db", db, "--code", code, "--keys", keys, *extra)


# ══════════════════════════════════════════════════════════════════════════
# ① dry-run：不建、不改 sidecar；JSON 报「计划」（added=true / written=false）
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
dry = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--json")
planned = parse_json(dry)
check("① dry-run 近重复对：rc0", dry.returncode == 0, dry.stderr[-200:])
check("① dry-run 不创建 sidecar（首次 ack 前连文件都不许有）",
      not os.path.exists(sidecar_path(db)))
check("① dry-run JSON：dry_run=true / added=true / written=false / 条目 0→1",
      planned.get("dry_run") is True and planned.get("added") is True
      and planned.get("written") is False
      and planned.get("entries_before") == 0 and planned.get("entries_after") == 1,
      {k: planned.get(k) for k in ("dry_run", "added", "written",
                                   "entries_before", "entries_after")})
check("① dry-run：canonical_keys 排序 / keys 保留输入顺序 / code 回显",
      planned.get("canonical_keys") == ["MiaoA2015", "MiaoB2015"]
      and planned.get("keys") == ["MiaoA2015", "MiaoB2015"]
      and planned.get("code") == "near_duplicates", planned)
check("① dry-run：库字节 sha 不变（JSON db_sha256 = 实测）",
      planned.get("db_sha256") == sha0 == digest(db))

# 已有 sidecar 时 dry-run 也不许重写（既有条目字节级不变）
custom = {"code": "duplicate_content", "keys": ["DuplicateA", "DuplicateB"],
          "reason": "既有裁决：保留原文", "decided_at": "2026-10-01T09:00:00+08:00"}
write_sidecar(db, {"version": 1, "acknowledged": [custom]})
side_before = sidecar_bytes(db)
dry2 = ack(db, "near_duplicates", "MiaoB2015,MiaoA2015", "--json")
p2 = parse_json(dry2)
check("① 已有 sidecar：dry-run rc0 / 条目 1→2 / sidecar 字节不变",
      dry2.returncode == 0 and p2.get("added") is True and p2.get("written") is False
      and p2.get("entries_before") == 1 and p2.get("entries_after") == 2
      and sidecar_bytes(db) == side_before, p2)
check("① dry-run 输入去重 + 顺序无关：重复 key 折成 2 个 canonical（rc0）",
      ack(db, "near_duplicates", "MiaoB2015,MiaoA2015,MiaoB2015", "--json").returncode == 0
      and parse_json(ack(db, "near_duplicates", "MiaoB2015,MiaoA2015,MiaoB2015",
                         "--json")).get("canonical_keys") == ["MiaoA2015", "MiaoB2015"])
remove_sidecar(db)

# ══════════════════════════════════════════════════════════════════════════
# ② apply：sidecar 落盘 + lint 计数下降 / pair 消失 + 库 sha 不变
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
rc0, lp0 = lint_payload(db)
check("② 基线：near=2 对 / warnings=3 / errors=0",
      rc0 == 0 and lp0["counts"]["near_duplicates"] == 2
      and lp0["counts"]["warnings"] == 3 and not lp0["errors"], lp0["counts"])

applied = ack(db, "near_duplicates", "Segerberg1971b,SegerbergPHD1971", "--apply", "--json")
pa = parse_json(applied)
check("② apply 近重复对：rc0 / added=true / written=true / 条目 0→1",
      applied.returncode == 0 and pa.get("added") is True and pa.get("written") is True
      and pa.get("entries_before") == 0 and pa.get("entries_after") == 1, pa)
check("② apply 后库字节 sha 不变（JSON 与实测都等于测前）",
      digest(db) == sha0 and pa.get("db_sha256") == sha0)
side = read_sidecar(db)
entries = side.get("acknowledged") or []
check("② sidecar 结构：version=1 + 恰好 1 条 ack",
      side.get("version") == 1 and len(entries) == 1, side)
entry = entries[0] if entries else {}
check("② 条目字段：code / canonical 排序 keys / 缺省 reason / decided_at ISO 秒",
      entry.get("code") == "near_duplicates"
      and entry.get("keys") == ["Segerberg1971b", "SegerbergPHD1971"]
      and entry.get("reason") == ACK_REASON
      and bool(ISO_RE.match(str(entry.get("decided_at") or ""))), entry)
raw_text = pathlib.Path(sidecar_path(db)).read_text(encoding="utf-8")
check("② 落盘格式：2 空格缩进 + 末尾换行 + 中文不转义（ensure_ascii=False）",
      raw_text.startswith('{\n  "version": 1,') and raw_text.endswith("\n")
      and ACK_REASON in raw_text and "\\u4eba\\u5de5" not in raw_text, raw_text[:80])

rc1, lp1 = lint_payload(db)
check("② lint 复读：near 2→1 / 已确认不同 1 对 / errors 仍 0",
      rc1 == 0 and lp1["counts"]["near_duplicates"] == 1
      and lp1["counts"]["near_duplicates_acknowledged"] == 1
      and not lp1["errors"], lp1["counts"])
check("② lint near warning 只剩未 ack 的 pair（Miao 在、Segerberg 不在）",
      "MiaoA2015≈MiaoB2015" in near_item(lp1).get("detail", "")
      and "Segerberg1971b" not in near_item(lp1).get("detail", ""),
      near_item(lp1).get("detail"))

# 第二对也 ack → near warning 整条消失（warnings 3→2），库仍逐字节不变
second = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply", "--json")
rc2, lp2 = lint_payload(db)
check("② 两对全 ack：near 0 对 / warning 整条消失 / warnings 3→2",
      second.returncode == 0 and rc2 == 0
      and lp2["counts"]["near_duplicates"] == 0
      and lp2["counts"]["near_duplicates_acknowledged"] == 2
      and lp2["counts"]["warnings"] == 2 and not near_item(lp2), lp2["counts"])
check("② 全程 apply 不改库：库字节 sha 仍等于测前", digest(db) == sha0)
report = run_cli("report-duplicates", "--db", db, "--out", os.path.join(TMP, "r"),
                 "--json")
check("② report-duplicates 仍全量审计（sidecar 只影响 lint）：2 对",
      report.returncode == 0 and parse_json(report).get("pairs") == 2)

# ══════════════════════════════════════════════════════════════════════════
# ③ 幂等：同 code + 同 canonical 键集 → rc0 / added=false / 零写入
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply", "--json")
side_before = sidecar_bytes(db)
again = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply", "--json")
pg = parse_json(again)
check("③ 再 apply（canonical 键集相同）：rc0 / added=false / written=false / 条目 1→1",
      again.returncode == 0 and pg.get("added") is False and pg.get("written") is False
      and pg.get("entries_before") == 1 and pg.get("entries_after") == 1, pg)
check("③ 幂等零写入：sidecar 字节不变 / 库字节不变",
      sidecar_bytes(db) == side_before and digest(db) == sha0)
alias_again = ack(db, "near_duplicates", "Segerberg1971,Segerberg1971b", "--apply", "--json")
# ↑ 该 pair 尚未 ack：这里验证的是「alias 写 key 也解析成同一 canonical 集」——
#   本库未 ack 过它，所以应 added=true；随后再用 canonical 写一次验证幂等。
check("③ alias 输入与 canonical 等价：added=true 且 canonical 集排序稳定",
      alias_again.returncode == 0 and parse_json(alias_again).get("added") is True
      and parse_json(alias_again).get("canonical_keys")
      == ["Segerberg1971b", "SegerbergPHD1971"], parse_json(alias_again))
side_after_alias = sidecar_bytes(db)
canon_again = ack(db, "near_duplicates", "SegerbergPHD1971,Segerberg1971b", "--apply", "--json")
pc = parse_json(canon_again)
check("③ alias 写过之后用 canonical 再 apply：rc0 / added=false / sidecar 字节不变",
      canon_again.returncode == 0 and pc.get("added") is False
      and pc.get("written") is False and sidecar_bytes(db) == side_after_alias, pc)
dry_idem = ack(db, "near_duplicates", "Segerberg1971,Segerberg1971b", "--json")
pd = parse_json(dry_idem)
check("③ dry-run 也判幂等：added=false / written=false / rc0",
      dry_idem.returncode == 0 and pd.get("dry_run") is True
      and pd.get("added") is False and pd.get("written") is False, pd)

# ══════════════════════════════════════════════════════════════════════════
# ④ key 解析：alias → canonical；顺序无关；写盘用 canonical 排序
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
order1 = ack(db, "near_duplicates", "Segerberg1971,Segerberg1971b", "--apply", "--json")
p1 = parse_json(order1)
entry = (read_sidecar(db).get("acknowledged") or [{}])[0]
check("④ alias（Segerberg1971 是 PHD 的旧 key）解析成 canonical 并落盘",
      order1.returncode == 0
      and p1.get("canonical_keys") == ["Segerberg1971b", "SegerbergPHD1971"]
      and entry.get("keys") == ["Segerberg1971b", "SegerbergPHD1971"], entry)
reverse = ack(db, "near_duplicates", "Segerberg1971b,Segerberg1971", "--json")
pr = parse_json(reverse)
check("④ 输入顺序无关：反序输入的 canonical_keys 相同（写盘排序）",
      reverse.returncode == 0 and pr.get("canonical_keys") == p1.get("canonical_keys")
      and pr.get("keys") == ["Segerberg1971b", "Segerberg1971"], pr)
check("④ alias 写 key 的 ack 让 lint 生效：near 2→1 / acknowledged=1",
      lint_payload(db)[1]["counts"]["near_duplicates"] == 1
      and lint_payload(db)[1]["counts"]["near_duplicates_acknowledged"] == 1)
check("④ apply 仍不改库：库字节 sha 不变", digest(db) == sha0)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 非当前近重复对 → rc2 + 零写入
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
bad_pair = ack(db, "near_duplicates", "MiaoA2015,DuplicateA", "--apply", "--json")
check("⑤ 不是近重复对：rc2 / JSON error / 不建 sidecar / 库不变",
      bad_pair.returncode == 2 and parse_json(bad_pair).get("error")
      and not os.path.exists(sidecar_path(db)) and digest(db) == sha0,
      parse_json(bad_pair).get("error"))
check("⑤ 错误文案指明「不是当前未确认的近重复对」且 stderr 有 ✗",
      "不是当前未确认的近重复对" in bad_pair.stderr and "✗" in bad_pair.stderr,
      bad_pair.stderr[-160:])
three = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015,DuplicateA", "--apply", "--json")
check("⑤ near_duplicates 给 3 个 key：rc2 / 零写入",
      three.returncode == 2 and "恰好 2 个" in three.stderr
      and not os.path.exists(sidecar_path(db)) and digest(db) == sha0, three.stderr[-160:])
unknown = ack(db, "near_duplicates", "MiaoA2015,NoSuchKey", "--apply", "--json")
check("⑤ 不存在的 key：rc2 / 零写入（key 先解析、后判 pair）",
      unknown.returncode == 2 and "不在库里" in unknown.stderr
      and not os.path.exists(sidecar_path(db)))
same_canonical = ack(db, "near_duplicates", "Segerberg1971,SegerbergPHD1971",
                     "--apply", "--json")
check("⑤ alias + canonical 指向同一条记录 → 只剩 1 个 canonical key：rc2 / 零写入",
      same_canonical.returncode == 2 and "恰好 2 个" in same_canonical.stderr
      and not os.path.exists(sidecar_path(db)))
not_dup_group = ack(db, "duplicate_content", "MiaoA2015,MiaoB2015", "--apply", "--json")
check("⑤ duplicate_content 但两条内容不同：rc2 / 零写入",
      not_dup_group.returncode == 2 and "内容完全相同" in not_dup_group.stderr
      and not os.path.exists(sidecar_path(db)) and digest(db) == sha0)

# ══════════════════════════════════════════════════════════════════════════
# ⑥ duplicate_content：整组 ack 后该组 warning 消失；部分 key → rc2
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
ack2 = ack(db, "duplicate_content", "DuplicateB,DuplicateA", "--apply", "--json")
rc6, lp6 = lint_payload(db)
check("⑥ 2 条组整组 ack：rc0 / 该组 warning 消失（只剩 3 条组）/ 库 sha 不变",
      ack2.returncode == 0 and rc6 == 0 and len(dup_items(lp6)) == 1
      and "TripleA" in (dup_items(lp6)[0].get("keys_sample") or [])
      and digest(db) == sha0, dup_items(lp6))
check("⑥ 2 条组 ack 后 near 不受影响（近重复 pair 照旧 2 对）",
      lp6["counts"]["near_duplicates"] == 2, lp6["counts"])
triple = ack(db, "duplicate_content", "TripleC,TripleA,TripleB", "--apply", "--json")
rc7, lp7 = lint_payload(db)
check("⑥ 3 条组列全 3 个 key（乱序输入）：rc0 / 该组也消失 / 库 sha 不变",
      triple.returncode == 0 and rc7 == 0 and not dup_items(lp7)
      and digest(db) == sha0, [item.get("keys_sample") for item in dup_items(lp7)])
check("⑥ sidecar 两条条目都保留（2 条组 + 3 条组）",
      len(read_sidecar(db).get("acknowledged") or []) == 2)

partial_db = new_db()
partial_sha = digest(partial_db)
partial = ack(partial_db, "duplicate_content", "TripleA,TripleB", "--apply", "--json")
check("⑥ 3 条组只给部分 key：rc2 / 零写入 / 库不变",
      partial.returncode == 2 and "列全该组全部 canonical key" in partial.stderr
      and not os.path.exists(sidecar_path(partial_db))
      and digest(partial_db) == partial_sha, partial.stderr[-160:])

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 既有条目保留：第二次 ack 追加时第一条原文一字不动（reason/decided_at/额外字段）
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
original = {"code": "near_duplicates", "keys": ["Segerberg1971b", "SegerbergPHD1971"],
            "reason": "自定义理由：Uppsala 博士论文 vs Stanford 书版",
            "decided_at": "2026-10-01T09:00:00+08:00", "note": "keep-me"}
write_sidecar(db, {"version": 1, "acknowledged": [original]})
appended = ack(db, "near_duplicates", "MiaoB2015,MiaoA2015", "--apply", "--json")
pa = parse_json(appended)
side = read_sidecar(db)
entries = side.get("acknowledged") or []
check("⑦ 追加第二条：rc0 / 条目 1→2 / 第一条原文逐字段不变（含额外字段）",
      appended.returncode == 0 and pa.get("entries_before") == 1
      and pa.get("entries_after") == 2 and len(entries) == 2
      and entries[0] == original, entries[:1])
check("⑦ 第二条写 canonical 排序的 keys / 缺省 reason",
      entries[1].get("keys") == ["MiaoA2015", "MiaoB2015"]
      and entries[1].get("reason") == ACK_REASON, entries[1:])
third = ack(db, "duplicate_content", "DuplicateA,DuplicateB", "--apply", "--json")
entries3 = read_sidecar(db).get("acknowledged") or []
check("⑦ 再追加第三条：前两条都还在且原文不变（先 ack 一对再 ack 另一对都保留）",
      third.returncode == 0 and len(entries3) == 3
      and entries3[0] == original and entries3[1] == entries[1], entries3)
check("⑦ 追加过程不改库：库字节 sha 不变", digest(db) == sha0)

# 自定义 reason：给了就用给的（不覆盖成缺省）
db_reason = new_db()
ack(db_reason, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply",
    "--reason", "两条不同：题名只是修订版", "--json")
reason_entry = (read_sidecar(db_reason).get("acknowledged") or [{}])[0]
check("⑦ --reason 写进条目原文（不落缺省文案）",
      reason_entry.get("reason") == "两条不同：题名只是修订版", reason_entry)

# ══════════════════════════════════════════════════════════════════════════
# ⑧ 坏 sidecar → rc2 且 sidecar 字节不变（写动作绝不覆盖）
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
bad_cases = [
    ("坏 JSON", "{ this is not json"),
    ("version≠1", '{"version": 2, "acknowledged": []}'),
    ("顶层不是对象", '[{"code": "near_duplicates"}]'),
    ("acknowledged 不是数组", '{"version": 1, "acknowledged": {}}'),
    ("条目不是对象", '{"version": 1, "acknowledged": ["oops"]}'),
    ("条目 code 不受支持",
     '{"version": 1, "acknowledged": [{"code": "missing_field", "keys": ["A", "B"]}]}'),
    ("条目 key 不在库里",
     '{"version": 1, "acknowledged": [{"code": "near_duplicates",'
     ' "keys": ["MissingX", "MiaoA2015"]}]}'),
    ("条目 keys 重复",
     '{"version": 1, "acknowledged": [{"code": "near_duplicates",'
     ' "keys": ["MiaoA2015", "MiaoA2015"]}]}'),
]
ok_bad = True
for label, payload in bad_cases:
    with open(sidecar_path(db), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    before_bytes = sidecar_bytes(db)
    result = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply", "--json")
    kept = sidecar_bytes(db) == before_bytes
    good = (result.returncode == 2 and "先修" in result.stderr and kept
            and parse_json(result).get("error"))
    ok_bad = ok_bad and good
    print(f"      · {label}: rc={result.returncode} 未覆盖={kept}")
check("⑧ 坏 sidecar 全类：rc2 / 提示先修 / 文件字节不变 / JSON error", ok_bad)
check("⑧ 坏 sidecar 时库也不动（库字节 sha 不变）", digest(db) == sha0)
# 坏文件修好后可以正常写（不因一次坏文件而永久拒绝）
write_sidecar(db, {"version": 1, "acknowledged": []})
fixed = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply", "--json")
check("⑧ 修好 sidecar 后可正常 apply：rc0 / 条目 0→1",
      fixed.returncode == 0 and parse_json(fixed).get("written") is True
      and len(read_sidecar(db).get("acknowledged") or []) == 1)

# ══════════════════════════════════════════════════════════════════════════
# ⑨ CLI 契约：JSON 字段集 / exit code / 人读模式一句摘要
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
required_fields = {"command", "dry_run", "db", "sidecar_path", "code", "keys",
                   "canonical_keys", "added", "written", "entries_before",
                   "entries_after", "db_sha256"}
r = ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--json")
p = parse_json(r)
check("⑨ JSON 字段集与契约完全一致（12 个键，不多不少）",
      set(p) == required_fields, sorted(set(p) ^ required_fields))
check("⑨ JSON 取值：command/db/sidecar_path/code/keys/canonical_keys",
      p.get("command") == "ack-duplicates" and p.get("db") == db
      and p.get("sidecar_path") == sidecar_path(db)
      and p.get("code") == "near_duplicates"
      and p.get("keys") == ["MiaoA2015", "MiaoB2015"]
      and p.get("canonical_keys") == ["MiaoA2015", "MiaoB2015"], p)
check("⑨ dry-run rc0（未写）；apply rc0（written=true）；幂等 rc0（added=false）",
      r.returncode == 0 and p.get("written") is False
      and ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply",
              "--json").returncode == 0
      and parse_json(ack(db, "near_duplicates", "MiaoA2015,MiaoB2015", "--json")
                     ).get("added") is False)

human_db = new_db()
human = ack(human_db, "near_duplicates", "MiaoA2015,MiaoB2015")
human_lines = [line for line in human.stdout.splitlines() if line.strip()]
check("⑨ 人读 dry-run：rc0 / 恰好一句摘要 / 含 dry-run 提示 / 不建 sidecar",
      human.returncode == 0 and len(human_lines) == 1
      and "dry-run" in human.stdout and "计划确认不同" in human.stdout
      and not os.path.exists(sidecar_path(human_db)), human_lines)
human_apply = ack(human_db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply")
human_apply_lines = [line for line in human_apply.stdout.splitlines() if line.strip()]
check("⑨ 人读 apply：rc0 / 恰好一句摘要 / 含「已写入」",
      human_apply.returncode == 0 and len(human_apply_lines) == 1
      and "已写入" in human_apply.stdout, human_apply_lines)
human_idem = ack(human_db, "near_duplicates", "MiaoA2015,MiaoB2015", "--apply")
check("⑨ 人读幂等：rc0 / 恰好一句摘要 / 含「无需重复写入」",
      human_idem.returncode == 0
      and len([line for line in human_idem.stdout.splitlines() if line.strip()]) == 1
      and "无需重复写入" in human_idem.stdout, human_idem.stdout)

# ══════════════════════════════════════════════════════════════════════════
# ⑩ 库不存在 / 坏 keys 用法 → rc2（且零写入）
# ══════════════════════════════════════════════════════════════════════════
db = new_db()
sha0 = digest(db)
missing_dir = os.path.join(TMP, "missing", "bibliography.jsonl")
missing = run_cli("ack-duplicates", "--db", missing_dir, "--code", "near_duplicates",
                  "--keys", "A,B", "--json")
check("⑩ 库不存在：rc2 / JSON error / 不创建 sidecar",
      missing.returncode == 2 and parse_json(missing).get("error")
      and not os.path.exists(os.path.join(TMP, "missing", "health-ignore.json")),
      missing.stderr[-160:])

bad_usage = [
    ("空 --keys", ("--code", "near_duplicates", "--keys", "  ,  ")),
    ("只有 1 个 key（near）", ("--code", "near_duplicates", "--keys", "MiaoA2015")),
    ("near 给 3 个 key", ("--code", "near_duplicates",
                          "--keys", "MiaoA2015,MiaoB2015,DuplicateA")),
    ("同一 canonical 重复（near）", ("--code", "near_duplicates",
                                    "--keys", "MiaoA2015,MiaoA2015")),
    ("duplicate_content 只有 1 个 key", ("--code", "duplicate_content",
                                          "--keys", "TripleA")),
    ("alias 多指", ("--code", "near_duplicates", "--keys", "shared,MiaoA2015")),
]
ok_usage = True
for label, extra in bad_usage:
    target_db = db
    if label == "alias 多指":
        target_db = new_db(records=ambiguous_records())
    target_sha = digest(target_db)
    result = run_cli("ack-duplicates", "--db", target_db, *extra, "--apply", "--json")
    good = (result.returncode == 2 and not os.path.exists(sidecar_path(target_db))
            and digest(target_db) == target_sha)
    ok_usage = ok_usage and good
    print(f"      · {label}: rc={result.returncode} "
          f"sidecar={os.path.exists(sidecar_path(target_db))}")
check("⑩ 坏 keys 用法全类：rc2 / 零写入（不建 sidecar）", ok_usage)
check("⑩ 主库经全部坏用法后仍逐字节不变", digest(db) == sha0)
check("⑩ alias 多指错误文案可定位（「多指」）",
      "多指" in run_cli("ack-duplicates", "--db", new_db(records=ambiguous_records()),
                        "--code", "near_duplicates", "--keys", "shared,MiaoA2015",
                        "--apply").stderr)

invalid_code = run_cli("ack-duplicates", "--db", db, "--code", "no_such_code",
                       "--keys", "MiaoA2015,MiaoB2015")
check("⑩ --code 非契约值：argparse rc2 / usage / 不建 sidecar",
      invalid_code.returncode == 2 and "invalid choice" in invalid_code.stderr
      and not os.path.exists(sidecar_path(db)), invalid_code.stderr[-160:])
missing_code = run_cli("ack-duplicates", "--db", db, "--keys", "MiaoA2015,MiaoB2015")
check("⑩ 缺 --code：argparse rc2", missing_code.returncode == 2)
missing_keys = run_cli("ack-duplicates", "--db", db, "--code", "near_duplicates")
check("⑩ 缺 --keys：argparse rc2", missing_keys.returncode == 2)
both_modes = run_cli("ack-duplicates", "--db", db, "--code", "near_duplicates",
                     "--keys", "MiaoA2015,MiaoB2015", "--dry-run", "--apply")
check("⑩ --dry-run 与 --apply 互斥：rc2", both_modes.returncode == 2)

print()
print("=" * 78)
print(f"通过 {pass_n} 项，失败 {len(fails)} 项" + (f"：{fails}" if fails else ""))
sys.exit(1 if fails else 0)
