#!/usr/bin/env python3.14
"""`bibdb.py merge-duplicates`（重复候选合并计划 dry-run）的判据测试（第四十三批）。

钉住：
  ① 图聚类：候选对 → 连通分量；`miaolitian`/`miaolitian2` 相关对**整对跳过**；
  ② 版本选择：**花括号保护优先**（canonical 取带 `{}` 的那条）；
  ③ 字段合并：缺失补全、写法差异取保护最多/信息最全的值；
  ④ 实质冲突（年份/第一作者姓氏/同秩条目类型不同）→ **人工**，不自动合并；
  ⑤ key 规范化：同作者同年按**题名字母序**加 a/b/c（APA 7）；同姓同年不同作者用
     「姓名首字母」区分（`WangY2010` / `WangX2010`）；旧 key 全部进 aliases；
  ⑥ dry-run 契约：**不写库**（库字节不变）、同一输入两次输出逐字节相同、`--apply` 拒绝。

不联网、不读稿件库、不跑 corpus。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_duplicates_merge_plan.py
"""
import csv
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

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep
           + str(_ENGINE / "vendor"), PYTHONIOENCODING="utf-8")


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def sha(path):
    return hashlib.sha256(read_bytes(path)).hexdigest()


BIB = r"""
@article{dupA1,
  author = {John Smith}, title = {Alpha Study}, journal = {J}, year = {2001},
  volume = {1}, pages = {1--10}
}
@article{dupA2,
  author = {Smith, John}, title = {{Alpha Study}}, journal = {J}, year = {2001},
  volume = {1}, pages = {1--10}
}
@article{dupB1,
  author = {John Smith}, title = {Beta Work}, journal = {J}, year = {2002},
  volume = {2}, pages = {11--20}
}
@article{dupB1x,
  author = {Smith, John}, title = {{Beta Work}}, journal = {J}, year = {2002}
}
@article{dupB2,
  author = {John Smith}, title = {Alpha Later}, journal = {J}, year = {2002},
  volume = {3}, pages = {21--30}
}
@article{dupB2x,
  author = {Smith, John}, title = {{Alpha Later}}, journal = {J}, year = {2002}
}
@article{dupC1,
  author = {Yanjing Wang}, title = {Gamma Work}, journal = {J}, year = {2010}
}
@article{dupC1x,
  author = {Wang, Yanjing}, title = {{Gamma Work}}, journal = {J}, year = {2010}
}
@article{dupC2,
  author = {Xiaoming Wang}, title = {Delta Work}, journal = {J}, year = {2010}
}
@article{dupC2x,
  author = {Wang, Xiaoming}, title = {{Delta Work}}, journal = {J}, year = {2010}
}
@article{dupD1,
  author = {Jane Doe}, title = {Conflict Work}, journal = {J}, year = {2005}
}
@article{dupD2,
  author = {Doe, Jane}, title = {Conflict Work}, journal = {J}, year = {2006}
}
@article{miaolitian,
  author = {Miao Litian}, title = {Miao One}, journal = {J}, year = {1999}
}
@article{miaolitian2,
  author = {Litian Miao}, title = {{Miao One}}, journal = {J}, year = {1999}
}
"""
SOURCE = {"kind": "published-bib", "issue": "2025年第1期(80)",
          "slug": "01 Base", "file": "base.bib"}
PAIRS = (
    "pair_no,similarity,key_a,key_b\n"
    "D0001,1.0000,dupA1,dupA2\n"
    "D0002,1.0000,dupB1,dupB1x\n"
    "D0003,1.0000,dupB2,dupB2x\n"
    "D0004,1.0000,dupC1,dupC1x\n"
    "D0005,1.0000,dupC2,dupC2x\n"
    "D0006,0.9700,dupD1,dupD2\n"
    "D0007,1.0000,miaolitian,miaolitian2\n"
)

with tempfile.TemporaryDirectory(prefix="bibdb_merge_") as tmp:
    db = os.path.join(tmp, "merge.jsonl")
    pairs_path = os.path.join(tmp, "duplicates.csv")
    with open(pairs_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(PAIRS)
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text(BIB, SOURCE, verified=True)
    store.save()
    digest = read_bytes(db)
    out1 = os.path.join(tmp, "plan1")
    out2 = os.path.join(tmp, "plan2")
    r = run_cli("merge-duplicates", "--db", db, "--pairs", pairs_path,
                "--out-dir", out1, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("① dry-run 退出 0、库文件逐字节不变（不写库）",
          r.returncode == 0 and read_bytes(db) == digest, (r.stdout + r.stderr)[:300])
    check("① 跳过 miaolitian 相关对，其余 6 对 → 6 簇（合并 5 / 人工 1）",
          payload.get("pairs_total") == 7
          and payload.get("pairs_excluded_miaolitian") == 1
          and payload.get("clusters_total") == 6
          and payload.get("clusters_merge") == 5
          and payload.get("clusters_manual") == 1,
          json.dumps(payload, ensure_ascii=False)[:400])
    check("① 预计条数：14 条 → 9 条（合并消失 5 条）",
          payload.get("records_total") == 14
          and payload.get("records_merged_away") == 5
          and payload.get("predicted_records") == 9,
          str(payload.get("records_total")) + "/" + str(payload.get("predicted_records")))
    md = ""
    plan_path = os.path.join(out1, "DUPLICATES-MERGE-PLAN.json")
    if os.path.isfile(plan_path):
        plan = json.load(open(plan_path, encoding="utf-8"))
        md = open(os.path.join(out1, "DUPLICATES-MERGE-PLAN.md"), encoding="utf-8").read()
    else:
        plan = {}
    clusters = {tuple(c["keys"]): c for c in plan.get("clusters", [])}
    # ── ② 花括号保护优先 ──
    a_entry = next((c for c in plan.get("clusters", []) if "dupA1" in c["keys"]), {})
    check("② 版本选择：canonical 取带花括号保护的那条（dupA2），新 key=Smith2001",
          a_entry.get("canonical_key") == "dupA2"
          and a_entry.get("new_key") == "Smith2001"
          and "dupA1" in (a_entry.get("aliases") or []),
          json.dumps(a_entry, ensure_ascii=False)[:300])
    # ── ③ 字段合并：缺失字段补全 ──
    b1_entry = next((c for c in plan.get("clusters", []) if "dupB1" in c["keys"]), {})
    check("③ 字段合并：dupB1x 缺的 volume/pages 从 dupB1 补全",
          b1_entry.get("merged_fields", {}).get("pages") == "11--20"
          and b1_entry.get("merged_fields", {}).get("volume") == "2",
          json.dumps(b1_entry.get("merged_fields", {}), ensure_ascii=False)[:200])
    # ── ⑤ key 规范化 ──
    b2_keys = {c["new_key"] for c in plan.get("clusters", [])
               if "dupB2" in c["keys"] or "dupB1" in c["keys"]}
    check("⑤ 同作者同年按题名字母序加 a/b：Alpha Later→Smith2002a / Beta Work→Smith2002b",
          b2_keys == {"Smith2002a", "Smith2002b"}, str(sorted(b2_keys)))
    c_keys = {c["new_key"] for c in plan.get("clusters", [])
              if "dupC1" in c["keys"] or "dupC2" in c["keys"]}
    check("⑤ 同姓同年不同作者用姓名首字母区分：WangY2010 / WangX2010",
          c_keys == {"WangY2010", "WangX2010"}, str(sorted(c_keys)))
    # ── ④ 实质冲突 → 人工 ──
    manual = plan.get("manual_clusters") or []
    check("④ 年份实质冲突 → 人工清单（不自动合并）",
          len(manual) == 1 and set(manual[0]["keys"]) == {"dupD1", "dupD2"}
          and "年份" in manual[0]["reason"], json.dumps(manual, ensure_ascii=False)[:200])
    check("④ miaolitian/miaolitian2 不在任何合并簇里",
          all(not ({"miaolitian", "miaolitian2"} & set(c["keys"]))
              for c in plan.get("clusters", [])))
    # ── aliases：所有旧 key 都映射到新 key（不冲突时） ──
    aliases = {a for c in plan.get("clusters", []) if c["action"] == "merge"
               for a in c.get("aliases") or []}
    check("⑤ 旧 key 进 aliases（dupA1/dupB1/dupB1x/dupC1/dupC1x …）",
          {"dupA1", "dupB1", "dupB1x", "dupC1", "dupC1x"} <= aliases,
          str(sorted(aliases)))
    # ── ⑥ 幂等：同输入两次输出逐字节相同 ──
    r2 = run_cli("merge-duplicates", "--db", db, "--pairs", pairs_path,
                 "--out-dir", out2)
    same = all(sha(os.path.join(out1, name)) == sha(os.path.join(out2, name))
               for name in ("DUPLICATES-MERGE-PLAN.md", "DUPLICATES-MERGE-PLAN.csv",
                            "DUPLICATES-MERGE-RENAMES.csv", "DUPLICATES-MERGE-MANUAL.csv",
                            "DUPLICATES-MERGE-PLAN.json"))
    check("⑥ 二次 dry-run 逐字节相同（确定性）且库仍不变",
          r2.returncode == 0 and same and read_bytes(db) == digest)
    check("⑥ 报告含 APA 7 规则与官方 URL、以及决策统计",
          "APA 7" in md and "apastyle.apa.org" in md and "预计合并后条数" in md,
          md[:200])
    # ── ⑦ --apply 门禁（第四十三批已实现；不带 --backup 必须拒绝） ──
    r = run_cli("merge-duplicates", "--db", db, "--pairs", pairs_path, "--apply")
    check("⑦ --apply 不带 --backup → 退出 2、库不变",
          r.returncode == 2 and "backup" in (r.stdout + r.stderr)
          and read_bytes(db) == digest, (r.stdout + r.stderr)[:200])

# ══ ⑧ apply（第四十三批 task-44）：合并 + key 改名 + aliases + 人工簇不动 + 幂等 ══
print("\n── ⑧ merge-duplicates --apply ──")

with tempfile.TemporaryDirectory(prefix="bibdb_merge_apply_") as tmp:
    db = os.path.join(tmp, "apply.jsonl")
    pairs_path = os.path.join(tmp, "duplicates.csv")
    with open(pairs_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(PAIRS)
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text(BIB, SOURCE, verified=True)
    store.save()
    plan_dir = os.path.join(tmp, "plan")
    r = run_cli("merge-duplicates", "--db", db, "--pairs", pairs_path,
                "--out-dir", plan_dir, "--json")
    check("⑧ 计划生成（退出 0）", r.returncode == 0)
    backup = os.path.join(tmp, "backup.jsonl")
    with open(backup, "wb") as fh:
        fh.write(read_bytes(db))
    digest_before = read_bytes(db)
    r = run_cli("merge-duplicates", "--db", db, "--apply", "--json")
    check("⑧ --apply 不带 --backup → 退出 2、库不变",
          r.returncode == 2 and read_bytes(db) == digest_before,
          (r.stdout + r.stderr)[:200])
    wrong = os.path.join(tmp, "wrong.jsonl")
    with open(wrong, "wb") as fh:
        fh.write(b"{}")
    r = run_cli("merge-duplicates", "--db", db, "--apply", "--backup", wrong)
    check("⑧ --backup sha 不符 → 退出 2", r.returncode == 2
          and "不一致" in (r.stdout + r.stderr))
    r = run_cli("merge-duplicates", "--db", db, "--apply", "--backup", backup,
                "--plan", os.path.join(plan_dir, "DUPLICATES-MERGE-PLAN.json"),
                "--out-dir", os.path.join(tmp, "applied"), "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    after = bibdb.Store.load(db)
    check("⑧ apply：5 簇合并、记录 14→9、人工簇/ miaolitian 不动",
          r.returncode == 0 and payload.get("clusters_applied") == 5
          and payload.get("records_after") == 9
          and after.get("dupD1") is not None and after.get("dupD2") is not None
          and after.get("miaolitian") is not None and after.get("miaolitian2") is not None,
          json.dumps(payload, ensure_ascii=False)[:300])
    merged = after.get("Smith2001")
    check("⑧ 合并记录：canonical 字段 + aliases（旧 key）+ 重算 content_sha",
          merged is not None and merged["fields"]["title"] == "{Alpha Study}"
          and "dupA1" in merged["aliases"]
          and merged["content_sha256"]
          == bibdb.content_sha256(merged["entry_type"], merged["fields"]))
    check("⑧ key 规范化落库：Smith2002a/b + WangY2010/WangX2010",
          all(after.get(key) is not None
              for key in ("Smith2002a", "Smith2002b", "WangY2010", "WangX2010")))
    archive = os.path.join(tmp, "applied", "MERGE-APPLY-ARCHIVE.csv")
    applied_md = os.path.join(tmp, "applied", "MERGE-APPLY-2026-10-18.md")
    check("⑧ apply 报告/归档 CSV 生成（旧 key→新 key 映射、回滚命令）",
          os.path.isfile(archive) and os.path.isfile(applied_md)
          and "dupA1,Smith2001" in open(archive, encoding="utf-8-sig").read()
          and "回滚" in open(applied_md, encoding="utf-8").read())
    # 重生成清单 → 残余 dry-run（只剩人工簇）→ 二次 apply 0
    r = run_cli("report", "--db", db, "--out", tmp)
    check("⑧ apply 后重生成清单：候选对 0（合成库人工簇年份不同、不再构成同作品对）",
          r.returncode == 0
          and "- 候选对：**0** 对" in open(os.path.join(tmp, "DUPLICATES.md"),
                                        encoding="utf-8").read())
    r = run_cli("merge-duplicates", "--db", db, "--pairs",
                os.path.join(tmp, "duplicates.csv"), "--out-dir",
                os.path.join(tmp, "replan"), "--json")
    replan = json.loads(r.stdout) if r.returncode == 0 else {}
    check("⑧ 二次 dry-run：合并候选 0（合成库无残余簇）",
          r.returncode == 0 and replan.get("clusters_merge") == 0
          and replan.get("clusters_total") == 0, json.dumps(replan, ensure_ascii=False)[:200])
    backup2 = os.path.join(tmp, "backup2.jsonl")
    with open(backup2, "wb") as fh:
        fh.write(read_bytes(db))
    digest_after = read_bytes(db)
    r = run_cli("merge-duplicates", "--db", db, "--apply", "--backup", backup2,
                "--plan", os.path.join(tmp, "replan", "DUPLICATES-MERGE-PLAN.json"), "--json")
    p2 = json.loads(r.stdout) if r.returncode == 0 else {}
    check("⑧ 二次 apply 幂等：applied 0、库文件逐字节不变",
          r.returncode == 0 and p2.get("clusters_applied") == 0
          and read_bytes(db) == digest_after, json.dumps(p2, ensure_ascii=False)[:200])

# ══ ⑨ 第二批：人工簇按 decisions 合并（第四十四批） ══
print("\n── ⑨ --decisions：人工簇按建议类型合并 ──")

with tempfile.TemporaryDirectory(prefix="bibdb_merge_decisions_") as tmp:
    db = os.path.join(tmp, "decisions.jsonl")
    pairs_path = os.path.join(tmp, "duplicates.csv")
    # 两条同作品记录但类型不同（article vs report）→ dry-run 判 manual
    TYPED = """\
@article{confA1,
  author = {John Smith}, title = {Typed Work}, journal = {J}, year = {2003}
}
@report{confA2,
  author = {Smith, John}, title = {{Typed Work}}, year = {2003}
}
@article{keepB1,
  author = {Ann Lee}, title = {Keep Thesis}, journal = {J}, year = {2004}
}
@report{keepB2,
  author = {Lee, Ann}, title = {{Keep Thesis}}, year = {2004}
}
"""
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text(TYPED, SOURCE, verified=True)
    store.save()
    with open(pairs_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("pair_no,similarity,key_a,key_b\n"
                 "D0001,1.0000,confA1,confA2\nD0002,1.0000,keepB1,keepB2\n")
    out1 = os.path.join(tmp, "plan")
    run_cli("merge-duplicates", "--db", db, "--pairs", pairs_path,
            "--out-dir", out1)
    plan = json.load(open(os.path.join(out1, "DUPLICATES-MERGE-PLAN.json"), encoding="utf-8"))
    manual = {tuple(c["keys"]): c for c in plan["manual_clusters"]}
    check("⑨ dry-run：两簇因类型冲突判人工（未合并）",
          len(manual) == 2 and all(c["action"] == "manual" for c in plan["clusters"]))
    decisions = [{"keys": list(manual[("confA1", "confA2")]["keys"]),
                  "entry_type": "report", "note": "按建议合并"}]
    decisions_path = os.path.join(tmp, "decisions.json")
    with open(decisions_path, "w", encoding="utf-8") as fh:
        json.dump(decisions, fh, ensure_ascii=False)
    backup = os.path.join(tmp, "backup.jsonl")
    with open(backup, "wb") as fh:
        fh.write(read_bytes(db))
    r = run_cli("merge-duplicates", "--db", db, "--apply", "--backup", backup,
                "--plan", os.path.join(out1, "DUPLICATES-MERGE-PLAN.json"),
                "--decisions", decisions_path,
                "--out-dir", os.path.join(tmp, "applied2"), "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    after = bibdb.Store.load(db)
    merged = next((rec for rec in after.records()
                   if "Typed Work" in str((rec.get("fields") or {}).get("title", ""))), None)
    check("⑨ decisions 合并：1 簇、记录 4→3、类型=report、人工簇（未给 decision）保持两条",
          r.returncode == 0 and payload.get("clusters_applied") == 1
          and payload.get("records_after") == 3
          and merged is not None and merged["entry_type"] == "report"
          and after.get("keepB1") is not None and after.get("keepB2") is not None,
          json.dumps(payload, ensure_ascii=False)[:300])
    md2 = os.path.join(tmp, "applied2", "MERGE-APPLY2-2026-10-19.md")
    check("⑨ 第二批报告：含决策表/类型存疑标注/回滚命令",
          os.path.isfile(md2) and "本次人工簇决策" in open(md2, encoding="utf-8").read()
          and "回滚" in open(md2, encoding="utf-8").read())

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
