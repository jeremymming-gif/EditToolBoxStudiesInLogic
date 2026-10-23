#!/usr/bin/env python3.14
r"""`slcn/bibdb.py`（编辑部引用库）与开发用 CLI `bibdb.py` 的判据测试。

为什么单开一组（而不是塞进 test_bibparse）：引用库的价值前提是**可信**——
「已核实」不能被导入悄悄改掉、幂等重跑不能产生 diff、模糊查询不能把不同文献报成命中、
纸面来源必须留在记录里。这几条都是**会静默出错**的，必须用合成用例钉住。

断言分组：
  (a) 规范化：DOI/题名/作者姓/年份/字段清理/内容摘要（幂等、与字段顺序无关）
  (b) 建库与导入：新条目、JSONL 排序、原子写无残留、重复导入逐字节不变
  (c) 冲突：同 key 同作品但字段不同 → **已核实条目字段不覆盖**、来源与冲突都留档、
      重跑不重复记冲突；`--force` 才覆盖（旧字段进 conflicts）
  (d) 升级：未核实 → 已核实（同一内容，不重复建记录）
  (e) key 撞车：不同作品同 key → 加后缀各留各的；同内容不同 key → 合并 + 别名可查
  (f) DOI 精确：大小写/前缀/url 里的 DOI 都归一；错 DOI 不命中
  (g) 模糊查询阈值：精确题名+作者+年份=高置信；题名不一致=低置信（明确标出）；
      无关题名不返回；短题名包含不得冒高置信
  (h) lint：正常库 0 error；手工改坏的库能报出重复 key / 缺来源 / verified 无核实记录 /
      摘要与字段不符 / 坏 JSON；缺 title、怪类型给 warning
  (i) import-dir（合成稿件库）：只读扫描、成品期=verified、tex 引用选 bib、
      跨期同 key 不同作品加后缀、半成品期不 verified、重复运行逐字节不变
  (j) 默认路径：笔记仓定位（显式且严格）；找不到时**明确报错**，不静默写别处
  (k) CLI 端到端：init / import-bib / query / stats / lint / import-dir 的退出码与输出
  (l) 真实稿件库抽查：**SKIP when absent**（公开仓/CI 没有稿件库），只读
  (m) 待裁决清单：`report` / `report-conflicts` / `report-duplicates` 生成
      CONFLICTS.md / DUPLICATES.md / duplicates.csv（中文、可勾选、按期→篇→条目、
      重复生成逐字节不变、空库/缺库处理）
  (n) 界面用的 CLI 能力（第 132 条）：`--dry-run` 预览（**一个字节都不写**、带逐条
      changes、库不存在也不新建）、`import-dir --root` 给**一期目录**（只导入这一期）、
      `query --bibtex`（命中带可直接复制的 BibTeX）
  (o) 冲突裁决持久化（第 130 条）：`resolve` keep/defer（不改字段值）、幂等、
      `--dry-run` 不写库、`--json`、未知 key/字段退出 2；`import-bib --force` 把
      被替换的旧冲突标 `forced-replaced` 并记 `resolved_at`；报告**编号稳定**
      （Cxxx 按全部冲突固定分配，裁决一条后其余编号不变）、已裁决不出现在待裁决区、
      头部统计区分待裁决/已裁决
  (p) B1 确定性规范化 dry-run（`normalize`）：逐条重建→`bibnorm.normalize_bib()`→比较；
      语义变更/仅格式/异常三类、规则与字段分类、高危形状风险、报告要素、
      幂等、**绝不写 bibliography.jsonl**、`--json` 摘要
  (q) `resolve --action adopt`（逐字段采用新稿值，第 135 条）：只改该冲突字段、删空字段、
      重算 content_sha256、`adopted` 标记、重叠默认拒绝 / `--allow-overlap` 标 `superseded`、
      幂等、dry-run 不写库、未知指纹/缺指纹/`--allow-overlap` 误用退出 2、报告统计
  (r) `normalize --apply`（B1 落库）：只改安全条目（entry_type/fields/content_sha256/
      updated_at）、保留其余元数据、跳过坏条目并列清单、apply 后二次校验、幂等、
      `--apply` 必须带匹配 sha 的 `--backup`、与 `--dry-run` 互斥
  (s) `normalize-authors`（B2 作者倒装）：Given Surname/多作者/particles → 姓, 名；
      CJK/机构/`and others`/花括号/已倒装/单名 → 跳过；apply 只改 author + sha/updated_at；
      dry-run 不写、`--backup` 门禁、幂等、二次校验 0；**不动 editor**（交给 (t)）
  (t) `normalize-editors`（B3 编者倒装：dry-run + apply）：扫 `editor` 字段（含
      「只有 editor 没有 author」的记录）、跳过分类、风险标记（多编者/`(eds.)`/
      editor-only/大写 `AND`/粘连缩写）、MD+CSV+JSON 三件报告、dry-run **绝不写库**；
      `--apply --backup` 只改 editor + sha/updated_at、二次 dry-run 0 残留、二次 apply 0 变更；
      报告无绝对路径/无时间戳、dry-run 两次逐字节相同（幂等）

不联网、不依赖稿件库（除 (l) 的 SKIP 组）。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_bibdb.py
"""

from __future__ import annotations

import datetime
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

from slcn import bibdb, paths  # noqa: E402

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


BASE_BIB = """@article{Smith2020,
  author   = {Smith, John},
  title    = {A Theory of Everything},
  journal  = {Journal of Logic},
  year     = {2020},
  volume   = {12},
  number   = {03},
  pages    = {45-67},
  doi      = {https://doi.org/10.1000/ABC123}
}

@book{Jones2019,
  author    = {Jones, Alice},
  title     = {Logic and Its Limits},
  publisher = {Oxford University Press},
  address   = {Oxford},
  year      = {2019}
}
"""

SOURCE = {"kind": bibdb.KIND_PUBLISHED, "issue": "2025年第6期(85)",
          "slug": "01 Test_Author", "file": "reference.bib"}


def new_store(tmp: str, name: str = "bibliography.jsonl") -> tuple[str, "bibdb.Store"]:
    path = os.path.join(tmp, name)
    return path, bibdb.Store.load(path, create=True)


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 规范化：DOI / 题名 / 作者姓 / 年份 / 字段 / 内容摘要 ──")

check("(a) normalize_doi 去 `https://doi.org/` 前缀并小写",
      bibdb.normalize_doi("https://doi.org/10.1000/ABC123") == "10.1000/abc123")
check("(a) normalize_doi 去 `doi:` 前缀与尾标点",
      bibdb.normalize_doi("  doi: 10.1000/ABC123. ") == "10.1000/abc123")
check("(a) normalize_doi 对空值返回空串", bibdb.normalize_doi("") == "")
check("(a) normalize_title 去 LaTeX/花括号/重音/标点",
      bibdb.normalize_title(r"\textup{The {\em Gödel} Case!}") == "the godel case")
check("(a) first_author_surname：`Last, First`",
      bibdb.first_author_surname("van Fraassen, B. C.") in ("vanfraassen", "fraassen"))
check("(a) first_author_surname：`First Last` 取末词",
      bibdb.first_author_surname("John Smith") == "smith")
check("(a) first_author_surname：中文姓名整个保留",
      bibdb.first_author_surname("鞠实儿 and 王小明") == "鞠实儿")
check("(a) first_author_surname：`Jr.` 不当姓",
      bibdb.first_author_surname("Sammy Davis Jr.") == "davis")
check("(a) entry_year 从脏值里抽四位年份",
      bibdb.entry_year({"year": r"\hspace{-0.3em}2021"}) == "2021")
check("(a) entry_year 支持 date 字段兜底",
      bibdb.entry_year({"date": "2019-05-01"}) == "2019")
canon = bibdb.canonical_fields({
    "JOURNALTITLE": "J", "issue": "03", "file": "x.pdf",
    "pages": "pp. 1-10", "doi": "HTTPS://DOI.ORG/10.1/AbC", "title": "T  itle",
})
check("(a) canonical_fields：字段别名归一（journaltitle→journal、issue→number）",
      canon.get("journal") == "J" and canon.get("number") == "3")
check("(a) canonical_fields：软件元数据字段被丢掉", "file" not in canon)
check("(a) canonical_fields：页码 `1-10` → `1--10`", canon.get("pages") == "1--10")
check("(a) canonical_fields：DOI 小写归一", canon.get("doi") == "10.1/abc")
check("(a) canonical_fields：空白折叠", canon.get("title") == "T itle")
sha_a = bibdb.content_sha256("article", {"title": "A  B", "year": "2020", "author": "X"})
sha_b = bibdb.content_sha256("ARTICLE", {"author": "X", "year": "2020", "title": "A B"})
sha_c = bibdb.content_sha256("article", {"title": "A B", "year": "2021", "author": "X"})
check("(a) content_sha256：字段顺序/空白/类型大小写不影响", sha_a == sha_b)
check("(a) content_sha256：字段值变了摘要就变", sha_a != sha_c)
check("(a) content_sha256：userf/urldate 这类排版层差异不影响",
      bibdb.content_sha256("article", {"title": "T", "userf": "cn"})
      == bibdb.content_sha256("article", {"title": "T"}))


# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 建库与导入：新条目 / JSONL 排序 / 原子写 / 重复导入逐字节不变 ──")

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    report = store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    check("(b) 导入 2 条新条目", report.new == 2 and report.entries == 2,
          f"new={report.new} entries={report.entries}")
    written = store.save()
    check("(b) 首次保存真的写盘", written and os.path.isfile(path))
    lines = read_bytes(path).decode("utf-8").splitlines()
    check("(b) JSONL 每行一个 JSON 对象", all(json.loads(line) for line in lines))
    keys = [json.loads(line)["key"] for line in lines]
    check("(b) 按 key 排序输出（便于 git diff）", keys == sorted(keys))
    rec = store.get("Smith2020")
    check("(b) 条目字段已规范化（number 去前导零、pages 双破折号、doi 小写）",
          rec["fields"]["number"] == "3" and rec["fields"]["pages"] == "45--67"
          and rec["fields"]["doi"] == "10.1000/abc123", json.dumps(rec["fields"], ensure_ascii=False))
    check("(b) 往期成品 bib 来源默认 verified=true、method=published-bib",
          rec["verified"] is True and rec["method"] == bibdb.KIND_PUBLISHED)
    check("(b) verified 条目有核实时间", bool(rec.get("verified_at")))
    check("(b) 来源记全 issue/slug/file/imported_at",
          rec["sources"][0]["issue"] == SOURCE["issue"]
          and rec["sources"][0]["slug"] == SOURCE["slug"]
          and rec["sources"][0]["file"] == SOURCE["file"]
          and rec["sources"][0]["imported_at"]
          and rec["sources"][0]["key"] == "Smith2020")
    check("(b) 原子写不留 .tmp/.bak 垃圾",
          not [n for n in os.listdir(tmp) if ".tmp" in n or ".bak" in n],
          str(os.listdir(tmp)))
    before = read_bytes(path)
    # 模拟「另一次进程」：重新 load 再导入同一份 bib
    store2 = bibdb.Store.load(path)
    report2 = store2.import_bib_text(BASE_BIB, SOURCE, verified=True)
    written2 = store2.save()
    check("(b) 重复导入全部命中已存在（new=0）", report2.new == 0 and report2.merged == 2,
          f"new={report2.new} merged={report2.merged}")
    check("(b) 重复导入**不重写文件**（git 零 diff）", written2 is False)
    check("(b) 重复导入后库文件逐字节相同", read_bytes(path) == before)
    check("(b) 重复导入不重复追加来源", len(bibdb.Store.load(path).get("Smith2020")["sources"]) == 1)

    # 未核实来源也能入库，但标记必须诚实
    unv = bibdb.Store.load(os.path.join(tmp, "unverified.jsonl"), create=True)
    unv.import_bib_text(BASE_BIB, {"kind": bibdb.KIND_IMPORTED, "file": "x.bib"},
                        verified=False)
    unv.save()
    rec_unv = unv.get("Smith2020")
    check("(b) 未核实来源：verified=false 且没有核实记录",
          rec_unv["verified"] is False and rec_unv["method"] is None
          and rec_unv["verified_at"] is None)

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    store.save()
    stats = store.stats()
    check("(b) stats：记录/已核实/来源/期数", stats["records"] == 2
          and stats["verified"] == 2 and stats["sources"] == 2 and stats["issues"] == 1,
          json.dumps({k: stats[k] for k in ("records", "verified", "sources", "issues")}))
    check("(b) stats：内容摘要重复为 0", stats["duplicate_content"] == 0)
    check("(b) stats：库文件 sha256 与磁盘一致",
          stats["sha256"] == hashlib.sha256(read_bytes(path)).hexdigest())


# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 冲突：已核实条目字段不被静默覆盖，来源/冲突留档，重跑幂等 ──")

CONFLICT_BIB = """@article{Smith2020,
  author   = {Smith, John},
  title    = {A Theory of Everything},
  journal  = {Journal of Logic},
  year     = {2020},
  volume   = {12},
  number   = {4},
  pages    = {45--67},
  doi      = {10.1000/abc123}
}
"""

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    store.save()
    other_source = dict(SOURCE, issue="2026年第1期(86)", slug="02 Other")
    report = store.import_bib_text(CONFLICT_BIB, other_source, verified=True)
    store.save()
    rec = store.get("Smith2020")
    check("(c) 同 key 同作品字段不同 → 记 1 条冲突", report.conflicts == 1,
          f"conflicts={report.conflicts}")
    check("(c) **已核实条目的字段不被静默覆盖**（number 仍是 3）",
          rec["fields"]["number"] == "3", json.dumps(rec["fields"], ensure_ascii=False))
    check("(c) 两份来源都保留（2 条 sources）", len(rec["sources"]) == 2)
    check("(c) 冲突留档：冲突来源/两侧 sha/字段 diff 都在",
          rec["conflicts"][0]["resolution"] == "kept-existing"
          and rec["conflicts"][0]["source"]["issue"] == "2026年第1期(86)"
          and rec["conflicts"][0]["field_diff"]["number"] == {"existing": "3", "incoming": "4"}
          and rec["conflicts"][0]["existing_sha256"] != rec["conflicts"][0]["incoming_sha256"])
    digest_after_conflict = read_bytes(path)
    # 重跑同一份冲突 bib（新进程语义）：冲突不重复记、文件不变
    store2 = bibdb.Store.load(path)
    report2 = store2.import_bib_text(CONFLICT_BIB, other_source, verified=True)
    written2 = store2.save()
    rec2 = store2.get("Smith2020")
    check("(c) 重跑同一冲突 bib：不重复记冲突", len(rec2["conflicts"]) == 1,
          f"conflicts={len(rec2['conflicts'])}")
    check("(c) 重跑同一冲突 bib：文件不变", written2 is False and read_bytes(path) == digest_after_conflict)
    # --force 才允许覆盖已核实条目；旧字段全量进 conflicts
    store3 = bibdb.Store.load(path)
    report3 = store3.import_bib_text(CONFLICT_BIB, other_source, verified=True, force=True)
    store3.save()
    rec3 = store3.get("Smith2020")
    check("(c) --force 才覆盖已核实条目", rec3["fields"]["number"] == "4")
    check("(c) --force 的旧字段进 conflicts（resolution=forced-replaced）",
          any(c["resolution"] == "forced-replaced"
              and c["field_diff"].get("number") == {"existing": "3", "incoming": "4"}
              for c in rec3["conflicts"]))
    check("(c) 同 key 但**不同作品**（题名/年份都不同）→ 不是冲突，是 key 撞车",
          bibdb.works_match(
              {"title": "A Theory of Everything", "author": "Smith, John", "year": "2020"},
              {"title": "Quantum Mechanics", "author": "Smith, John", "year": "1999"},
          ) is False)
    check("(c) 同 key 同作品但年份不同 → 判为不同作品（宁可多留一条）",
          bibdb.works_match(
              {"title": "A Theory of Everything", "author": "Smith, John", "year": "2020"},
              {"title": "A Theory of Everything", "author": "Smith, John", "year": "2021"},
          ) is False)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 升级：未核实 → 已核实（不重复建记录） ──")

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(BASE_BIB, {"kind": bibdb.KIND_IMPORTED, "file": "draft.bib"},
                          verified=False)
    store.save()
    check("(d) 先入未核实：2 条 verified=false",
          all(r["verified"] is False for r in store.records()))
    report = store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    store.save()
    rec = store.get("Smith2020")
    check("(d) 同内容被已核实来源命中 → 升级为 verified", rec["verified"] is True
          and rec["method"] == bibdb.KIND_PUBLISHED, f"{rec['verified']} {rec['method']}")
    check("(d) 升级不重复建记录（仍 2 条）", len(store.records()) == 2)
    check("(d) 升级追加来源（draft + published 两份）", len(rec["sources"]) == 2)
    check("(d) 升级报告计为 merged/updated 而不是 new", report.new == 0)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) key 撞车：不同作品同 key 各留各的；同内容不同 key 合并 + 别名 ──")

WORK_A = """@article{a1,
  author = {Alpha, Ann},
  title  = {First Work},
  year   = {2001}
}
"""
WORK_B = """@article{a1,
  author = {Beta, Bob},
  title  = {Completely Different Work},
  year   = {2002}
}
"""
ALIAS_BIB = """@article{Smith2020alt,
  author   = {Smith, John},
  title    = {A Theory of Everything},
  journal  = {Journal of Logic},
  year     = {2020},
  volume   = {12},
  number   = {3},
  pages    = {45--67},
  doi      = {10.1000/abc123}
}
"""

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(WORK_A, dict(SOURCE, issue="2024年第1期(74)", slug="01 A"),
                          verified=True)
    store.import_bib_text(WORK_B, dict(SOURCE, issue="2025年第1期(80)", slug="02 B"),
                          verified=True)
    store.import_bib_text(BASE_BIB, dict(SOURCE, issue="2024年第1期(74)", slug="09 Base"),
                          verified=True)
    store.save()
    keys = sorted(store._by_key)
    check("(e) 同 key 不同作品：两条都保留（第二条加后缀）",
          "a1" in keys and "a1-2" in keys, str(keys))
    got_a, got_b = store.get("a1"), store.get("a1-2")
    check("(e) 各留各的字段（没有串味）",
          got_a["fields"]["title"] == "First Work"
          and got_b["fields"]["title"] == "Completely Different Work")
    check("(e) 后缀条目保留原始 key 的来源（source.key=a1）",
          got_b["sources"][0]["key"] == "a1")
    # 同内容不同 key → 合并到已有记录（不新增记录），key 唯一时记成 alias
    report = store.import_bib_text(ALIAS_BIB,
                                   dict(SOURCE, issue="2023年第1期(68)", slug="03 C"),
                                   verified=True)
    store.save()
    check("(e) 同内容不同 key：不新增记录，并来源", report.new == 0 and len(store.records()) == 4,
          f"new={report.new} records={len(store.records())}")
    rec = store.get("Smith2020")
    check("(e) 合并后的记录 aliases 里有另一个 key", rec is not None and rec["aliases"] == ["Smith2020alt"],
          json.dumps(rec["aliases"] if rec else None, ensure_ascii=False))
    store2 = bibdb.Store.load(path)
    hit = store2.query_key("Smith2020alt")
    check("(e) 按别名查询能命中（hit=alias）",
          hit["count"] == 1 and hit["hits"][0]["hit"] == "alias"
          and hit["hits"][0]["record"]["key"] == "Smith2020")
    check("(e) 撞车的 key `a1` 不当别名（避免假命中）",
          bibdb.Store.load(path).query_key("a1")["hits"][0]["hit"] == "key")


# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) DOI 精确查询 ──")

DOI_BIB = """@article{DoiPaper,
  author  = {Doe, Jane},
  title   = {A Study of Things},
  journal = {Some Journal},
  year    = {2018},
  doi     = {https://doi.org/10.5555/XYZ.42}
}
@misc{UrlDoiPaper,
  author = {Roe, Richard},
  title  = {A Web Thing},
  year   = {2019},
  url    = {https://doi.org/10.7777/QQQ.1}
}
"""
with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(DOI_BIB, SOURCE, verified=True)
    store.save()
    for variant in ("10.5555/xyz.42", "https://doi.org/10.5555/XYZ.42",
                    "doi:10.5555/xyz.42", " 10.5555/XYZ.42. "):
        hit = store.query_doi(variant)
        check(f"(f) DOI 归一后命中：{variant!r}", hit["count"] == 1
              and hit["hits"][0]["key"] == "DoiPaper")
    hit = store.query_doi("10.7777/qqq.1")
    check("(f) url 里写的 doi.org 链接也能按 DOI 命中",
          hit["count"] == 1 and hit["hits"][0]["hit"] == "url-doi"
          and hit["hits"][0]["key"] == "UrlDoiPaper")
    check("(f) 错的 DOI 不命中", store.query_doi("10.5555/wrong")["count"] == 0)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) 模糊查询阈值：高置信 / 低置信 / 不返回 ──")

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    store.save()
    hit = store.query_title("A Theory of Everything", author="Smith", year="2020")
    top = hit["hits"][0]
    check("(g) 精确题名+作者+年份 → 高置信且 score≈1",
          top["confidence"] == "high" and top["score"] >= 0.99
          and top["verified"] is True and top["key"] == "Smith2020",
          f"score={top['score']} conf={top['confidence']}")
    check("(g) score_parts 带判据明细",
          set(top["score_parts"]) == {"title", "author", "year"}
          and top["author_match"] is True and top["year_match"] is True)
    # 题名改一个词：题名相似度掉到阈值以下 → 即使作者/年份对也必须标 low
    loose = store.query_title("A Theory of Nothing", author="Smith", year="2020")
    check("(g) 题名不一致 → 标 confidence=low（不当命中）",
          loose["count"] == 1 and loose["hits"][0]["confidence"] == "low"
          and loose["hits"][0]["title_similarity"] < bibdb.FUZZY_TITLE_THRESHOLD,
          f"title_sim={loose['hits'][0]['title_similarity'] if loose['hits'] else None}")
    # 作者不符 → low
    wrong_author = store.query_title("A Theory of Everything", author="Brown", year="2020")
    check("(g) 第一作者不符 → 标 low",
          wrong_author["count"] == 1 and wrong_author["hits"][0]["confidence"] == "low"
          and wrong_author["hits"][0]["author_match"] is False)
    unrelated = store.query_title("Proof Theory and Ordinal Analysis")
    check("(g) 无关题名不返回", unrelated["count"] == 0)
    short = store.query_title("Logic")
    check("(g) 短题名包含不得冒高置信（`Logic` 不命中 `Logic and Its Limits`）",
          short["count"] == 0,
          str([(h["key"], h["title_similarity"]) for h in short["hits"]]))
    # 高置信阈值可调
    strict = store.query_title("A Theory of Everything", threshold=1.01)
    check("(g) 阈值可调：把高置信阈值抬到 1.01 → 命中变 low",
          strict["hits"][0]["confidence"] == "low")


# ─────────────────────────────────────────────────────────────────────────
print("\n── (h) lint：正常库 0 error；改坏的库逐项报出 ──")

with tempfile.TemporaryDirectory(prefix="bibdb_") as tmp:
    path, store = new_store(tmp)
    store.import_bib_text(BASE_BIB, SOURCE, verified=True)
    store.save()
    result = store.lint()
    check("(h) 正常库 lint：0 error", result["ok"] and result["counts"]["errors"] == 0,
          json.dumps(result["counts"], ensure_ascii=False))
    check("(h) 正常库 lint：全库已核实 → 没有「未核实」warning",
          not any("未核实条目" in warning for warning in result["warnings"]))

    # 手工造一份坏库（模拟手工编辑/旧版本遗留）
    good = read_records(path)
    bad_rec = json.loads(json.dumps(good[0]))
    bad_rec["key"] = "BadNoSources"
    bad_rec["sources"] = []
    verifyless = json.loads(json.dumps(good[1]))
    verifyless["key"] = "BadVerifyless"
    verifyless["verified"] = True
    verifyless["verified_at"] = None
    verifyless["method"] = None
    shabad = json.loads(json.dumps(good[0]))
    shabad["key"] = "BadSha"
    shabad["content_sha256"] = "0" * 64
    weird = json.loads(json.dumps(good[0]))
    weird["key"] = "BadType"
    weird["entry_type"] = "artical"
    weird["fields"] = {"author": "X", "year": "2020"}   # 缺 title
    bad_lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in good]
    bad_lines += [json.dumps(r, ensure_ascii=False, sort_keys=True)
                  for r in (bad_rec, verifyless, shabad, weird)]
    bad_lines.append(json.dumps(good[0], ensure_ascii=False, sort_keys=True))  # 重复 key
    bad_lines.append("{ 这不是 JSON")
    bad_path = write(os.path.join(tmp, "broken.jsonl"), "\n".join(bad_lines) + "\n")
    broken = bibdb.Store.load(bad_path, lenient=True)
    lint = broken.lint()
    text = "\n".join(lint["errors"] + lint["warnings"])
    check("(h) lint：重复 key 报 error", "key 重复" in text, text[:200])
    check("(h) lint：缺来源报 error", "缺来源" in text)
    check("(h) lint：verified 无核实记录报 error", "verified_at" in text or "method" in text)
    check("(h) lint：内容摘要与字段不符报 error", "content_sha256 与字段内容不一致" in text)
    check("(h) lint：坏 JSON 行报 error", "不是合法 JSON" in text)
    check("(h) lint：怪 entry_type 给 warning", "不常见的 entry_type" in text)
    check("(h) lint：缺 title 给 warning", "缺 title" in text)
    check("(h) lint 结论 ok=false", lint["ok"] is False)
    # 严格 load 对坏库必须拒绝（防止把坏库当好的用/写回去）
    refused = False
    try:
        bibdb.Store.load(bad_path)
    except bibdb.BibDBError:
        refused = True
    check("(h) 严格 load 遇到结构问题直接报错（不静默带病运行）", refused)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (i) import-dir（合成稿件库）：只读扫描 / 成品期 / tex 选 bib / 幂等 ──")


def make_issue(root: str, issue: str, slug: str, bib_name: str, bib_body: str,
               *, toc: bool = True, tex_body: str | None = None) -> str:
    issue_dir = os.path.join(root, issue)
    author_dir = os.path.join(issue_dir, slug)
    os.makedirs(author_dir, exist_ok=True)
    if toc:
        os.makedirs(os.path.join(issue_dir, "000 Table of Contents"), exist_ok=True)
    tex_name = slug.split(" ", 1)[-1].replace(" ", "_") + ".tex"
    if tex_body is None:
        tex_body = ("\\documentclass{article}\n"
                    f"\\addbibresource{{{bib_name}}}\n"
                    "\\begin{document}x\\end{document}\n")
    write(os.path.join(author_dir, tex_name), tex_body)
    return write(os.path.join(author_dir, bib_name), bib_body)


ISSUE_BIB_1 = """@article{SharedKey,
  author = {Smith, John},
  title  = {A Theory of Everything},
  year   = {2020}
}
"""
ISSUE_BIB_2 = """@article{SharedKey,
  author = {Other, Olivia},
  title  = {An Unrelated Paper},
  year   = {2021}
}
@article{DupKey,
  author = {Dup, Dan},
  title  = {Duplicate In File},
  year   = {2022}
}
@article{DupKey,
  author = {Dup, Dan},
  title  = {Duplicate In File},
  year   = {2022}
}
"""

with tempfile.TemporaryDirectory(prefix="bibdb_ms_") as tmp:
    root = os.path.join(tmp, "稿件库")
    make_issue(root, "2024年第1期(74)", "01 Smith_John", "reference.bib", ISSUE_BIB_1)
    make_issue(root, "2025年第1期(80)", "01 Other_Olivia", "name.bib", ISSUE_BIB_2)
    # 未定稿期：没有 000 目录 → 只入未核实（内容必须与成品期不同，否则会并到已核实记录上）
    make_issue(root, "2026年第9期(99)", "01 Draft_Amy", "reference.bib",
               "@article{DraftOnly,\n  author = {Draft, Amy},\n"
               "  title = {A Draft in Progress},\n  year = {2026}\n}\n",
               toc=False)
    # 没有 bib 的作者目录 → 跳过并报原因
    os.makedirs(os.path.join(root, "2024年第1期(74)", "02 No_Bib", "original"))
    write(os.path.join(root, "2024年第1期(74)", "02 No_Bib", "original", "x.docx"), "x")
    # 隐藏备份 .bib.un~ 不是 .bib，不能被选中
    write(os.path.join(root, "2024年第1期(74)", "01 Smith_John", ".reference.bib.un~"), "@article{x,}")
    snapshot = {str(p): hashlib.sha256(read_bytes(str(p))).hexdigest()
                for p in pathlib.Path(root).rglob("*") if p.is_file()}

    scans = bibdb.scan_manuscripts(root)
    check("(i) 扫描到 3 期、3 份 bib", len(scans) == 3
          and sum(len(scan.files) for scan in scans) == 3,
          str([(s.issue, len(s.files)) for s in scans]))
    smith = next(s for s in scans if s.issue == "2024年第1期(74)")
    check("(i) 无 bib 的作者目录进了 skipped（不猜）",
          any("02 No_Bib" in item["slug"] for item in smith.skipped))
    check("(i) 成品期判据 = `000` 目录", smith.finished is True
          and next(s for s in scans if s.issue.startswith("2026")).finished is False)
    check("(i) 选中终稿 tex 引用那份 bib（tex-ref）",
          smith.files[0].file == "reference.bib" and smith.files[0].how == "tex-ref")

    path, store = new_store(tmp)
    result = bibdb.import_manuscripts(store, root)
    store.save()
    total = result["total"]
    check("(i) 合计：条目/新入库/冲突/skipped", total["entries"] == 5
          and total["new"] == 4 and total["conflicts"] == 0
          and total["skipped"].get("同一份 bib 里 key 重复") == 1,
          json.dumps(total, ensure_ascii=False))
    check("(i) 成品期条目 verified=true", store.get("SharedKey")["verified"] is True)
    check("(i) 未定稿期条目只入未核实",
          store.get("DraftOnly") is not None and store.get("DraftOnly")["verified"] is False)
    check("(i) 跨期同 key 不同作品各自保留（后缀）",
          "SharedKey" in store._by_key and "SharedKey-2" in store._by_key)
    check("(i) 来源里带期/篇/文件", store.get("SharedKey")["sources"][0]["issue"]
          == "2024年第1期(74)")
    after = {str(p): hashlib.sha256(read_bytes(str(p))).hexdigest()
             for p in pathlib.Path(root).rglob("*") if p.is_file()}
    check("(i) 扫描/导入**只读**：稿件库文件一个字节都没变", snapshot == after)
    digest = read_bytes(path)
    # 重复运行（新进程语义）→ 库无变化、逐字节相同
    store2 = bibdb.Store.load(path)
    result2 = bibdb.import_manuscripts(store2, root)
    written2 = store2.save()
    check("(i) 重复 import-dir：没有新条目", result2["total"]["new"] == 0,
          str(result2["total"]))
    check("(i) 重复 import-dir：库文件逐字节相同", written2 is False and read_bytes(path) == digest)
    # through-year / finished-only 过滤
    scans_2024 = bibdb.scan_manuscripts(root, through_year=2024)
    check("(i) through-year 只扫 ≤2024 的期", [s.issue for s in scans_2024] == ["2024年第1期(74)"])
    scans_finished = bibdb.scan_manuscripts(root, finished_only=True)
    check("(i) finished-only 跳过未定稿期", all(s.finished for s in scans_finished)
          and len(scans_finished) == 2)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (j) 默认路径：笔记仓定位（显式且严格） ──")

_old_notes = os.environ.get("SLCN_NOTES")


def with_notes_env(value: str | None):
    class _Ctx:
        def __enter__(self):
            if value is None:
                os.environ.pop("SLCN_NOTES", None)
            else:
                os.environ["SLCN_NOTES"] = value

        def __exit__(self, *args):
            if _old_notes is None:
                os.environ.pop("SLCN_NOTES", None)
            else:
                os.environ["SLCN_NOTES"] = _old_notes
    return _Ctx()


with tempfile.TemporaryDirectory(prefix="bibdb_notes_") as notes:
    os.makedirs(os.path.join(notes, "cases"))
    with with_notes_env(notes):
        expected = os.path.join(notes, "bib_db", "bibliography.jsonl")
        check("(j) 默认库 = <笔记仓>/bib_db/bibliography.jsonl",
              bibdb.default_db_path() == expected, bibdb.default_db_path())
        db_path, created = bibdb.init_db()
        check("(j) init 建目录 + 空文件", created and os.path.isfile(db_path)
              and os.path.getsize(db_path) == 0)
        check("(j) 重复 init 幂等（不报错、不重建）", bibdb.init_db()[1] is False)
        stats = bibdb.Store.load(db_path).stats()
        check("(j) 空库 stats 正常", stats["records"] == 0 and stats["verified"] == 0)

with tempfile.TemporaryDirectory(prefix="bibdb_badnotes_") as bad_notes:
    with with_notes_env(bad_notes):        # 目录存在但没有 cases/ → 不是笔记仓
        try:
            bibdb.default_db_path()
            raised = False
        except bibdb.NotesRootMissing as exc:
            raised = "SLCN_NOTES" in str(exc) and "cases/" in str(exc)
        check("(j) 笔记仓定位失败 → 明确报错（给出怎么配），绝不静默写别处", raised)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (k) CLI 端到端：init / import-bib / query / stats / lint ──")

CLI = str(_ENGINE / "bibdb.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep
           + str(_ENGINE / "vendor"), PYTHONIOENCODING="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


with tempfile.TemporaryDirectory(prefix="bibdb_cli_") as tmp:
    db = os.path.join(tmp, "cli.jsonl")
    bib = write(os.path.join(tmp, "in.bib"), BASE_BIB)
    r = run_cli("init", "--db", db)
    check("(k) `bibdb.py init` 退出 0 且建出文件", r.returncode == 0 and os.path.isfile(db),
          (r.stdout + r.stderr)[:120])
    r = run_cli("import-bib", "--bib", bib, "--issue", "2025年第6期(85)",
                "--slug", "01 Test_Author", "--db", db)
    check("(k) `import-bib --issue/--slug` 退出 0、输出新入库 2",
          r.returncode == 0 and "新入库 2" in r.stdout, (r.stdout + r.stderr)[:200])
    first = read_bytes(db)
    r = run_cli("import-bib", "--bib", bib, "--issue", "2025年第6期(85)",
                "--slug", "01 Test_Author", "--db", db)
    check("(k) 重复 import-bib → 输出「无变化」且文件字节不变",
          r.returncode == 0 and "无变化" in r.stdout and read_bytes(db) == first,
          (r.stdout + r.stderr)[:200])
    r = run_cli("query", "--doi", "10.1000/abc123", "--json", "--db", db)
    payload = json.loads(r.stdout)
    check("(k) `query --doi --json` 命中且 verified=true",
          r.returncode == 0 and payload["count"] == 1
          and payload["hits"][0]["record"]["verified"] is True)
    r = run_cli("query", "--title", "A Theory of Everything", "--author", "Smith", "--json",
                "--db", db)
    payload = json.loads(r.stdout)
    check("(k) `query --title --author --json` 高置信",
          r.returncode == 0 and payload["hits"][0]["confidence"] == "high")
    r = run_cli("stats", "--json", "--db", db)
    payload = json.loads(r.stdout)
    check("(k) `stats --json`", r.returncode == 0 and payload["records"] == 2
          and payload["verified"] == 2)
    r = run_cli("lint", "--db", db)
    check("(k) `lint` 正常库退出 0", r.returncode == 0 and "error 0 项" in r.stdout,
          (r.stdout + r.stderr)[-160:])
    r = run_cli("query", "--key", "Smith2020", "--db", os.path.join(tmp, "missing.jsonl"))
    check("(k) 库不存在时 query 退出 2（明确报错）", r.returncode == 2
          and "引用库不存在" in (r.stdout + r.stderr))
    r = run_cli("import-bib", "--bib", os.path.join(tmp, "nope.bib"), "--db", db)
    check("(k) bib 不存在时 import-bib 退出 2", r.returncode == 2)
    r = run_cli("query", "--db", db)
    check("(k) 没给查询条件 → 用法错误退出 2", r.returncode == 2)

    # CLI import-dir 也走同一套逻辑
    root = os.path.join(tmp, "稿件库")
    make_issue(root, "2024年第1期(74)", "01 Smith_John", "reference.bib", ISSUE_BIB_1)
    db2 = os.path.join(tmp, "cli_dir.jsonl")
    r = run_cli("import-dir", "--root", root, "--db", db2)
    check("(k) `import-dir` 退出 0 且把成品期当已核实",
          r.returncode == 0 and "新入库 1" in r.stdout
          and bibdb.Store.load(db2).get("SharedKey")["verified"] is True,
          (r.stdout + r.stderr)[-300:])
    r = run_cli("import-dir", "--root", root, "--db", db2)
    check("(k) 重复 `import-dir` → 无变化", r.returncode == 0 and "无变化" in r.stdout,
          (r.stdout + r.stderr)[-200:])
    r = run_cli("import-dir", "--root", os.path.join(tmp, "不存在"), "--db", db2)
    check("(k) `import-dir` 根不存在 → 退出 2", r.returncode == 2)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (m) 待裁决清单：report / report-conflicts / report-duplicates ──")


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


with tempfile.TemporaryDirectory(prefix="bibdb_report_") as tmp:
    db = os.path.join(tmp, "db.jsonl")
    store = bibdb.Store.load(db, create=True)
    # 记录 A：已核实基线（来源期 74）
    store.import_bib_text("""@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970}
}
""", {"kind": "published-bib", "issue": "2024年第1期(74)",
      "slug": "01 A", "file": "a.bib"}, verified=True)
    # 记录 B：同作品不同 key（供「重复候选对」；内容不同才会成为两条记录）
    store.import_bib_text("""@article{Quine70,
  author    = {Quine, Willard Van Orman},
  title     = {Philosophy of logic},
  publisher = {Prentice-Hall},
  year      = {1970}
}
""", {"kind": "published-bib", "issue": "2024年第2期(75)",
      "slug": "02 B", "file": "b.bib"}, verified=True)
    # 同 key 同作品、字段不同（新一期）→ 产生 1 条冲突留档
    store.import_bib_text("""@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind (new series)},
  volume  = {79},
  pages   = {1--20},
  year    = {1970}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "03 C", "file": "c.bib"}, verified=True)
    store.save()

    out = os.path.join(tmp, "reports")
    r = run_cli("report", "--db", db, "--out", out)
    conflicts_md = os.path.join(out, "CONFLICTS.md")
    duplicates_md = os.path.join(out, "DUPLICATES.md")
    duplicates_csv = os.path.join(out, "duplicates.csv")
    check("(m) `report` 退出 0 且生成三个清单文件",
          r.returncode == 0 and os.path.isfile(conflicts_md)
          and os.path.isfile(duplicates_md) and os.path.isfile(duplicates_csv),
          (r.stdout + r.stderr)[-300:])
    text = _read_text(conflicts_md) if os.path.isfile(conflicts_md) else ""
    check("(m) CONFLICTS.md：中文标题 + 按期→篇→条目组织 + 编号",
          "字段冲突待裁决清单" in text and "### 期：2025年第1期(80)" in text
          and "#### 篇：03 C" in text and "##### C001 · `Quine1970`" in text, text[:300])
    check("(m) CONFLICTS.md：列 bib key / 题名 / 作者 / 年份",
          "`Quine1970`" in text and "Philosophy of Logic" in text
          and "Quine, W. V. O." in text and "1970" in text)
    check("(m) CONFLICTS.md：**已核实旧值 vs 新稿值** 都写清",
          "已核实旧值 = `Mind`" in text and "新稿值 = `Mind (new series)`" in text
          and "journal" in text, text[-1500:])
    check("(m) CONFLICTS.md：两个来源（新稿期/篇/文件 + 库内已核实来源）",
          "**冲突来源（新稿）**：2025年第1期(80) / 03 C / c.bib" in text
          and "2024年第1期(74) / 01 A / a.bib" in text)
    check("(m) CONFLICTS.md：逐条可勾选（保留旧值/采用新稿值/仍需查证）",
          "- [ ] 保留旧值（建议默认）" in text
          and "- [ ] 采用新稿值" in text and "- [ ] 仍需人工查证" in text)
    check("(m) CONFLICTS.md：带统计摘要 + 默认建议 + 裁决方法章节",
          "待裁决冲突项：**1** 条" in text and "默认保留旧值 **1** 条" in text
          and "## 裁决方法" in text and "--force" in text)
    dup_text = _read_text(duplicates_md) if os.path.isfile(duplicates_md) else ""
    check("(m) DUPLICATES.md：明确「仅供参考，不自动合并」+ 候选对表",
          "仅供参考，不自动合并" in dup_text and "| # | 相似度 | key A | key B |" in dup_text)
    check("(m) DUPLICATES.md：列出 key/题名/作者/年份/来源期/相似度",
          "Quine1970" in dup_text and "Quine70" in dup_text
          and "1.0000" in dup_text and "2024年第1期(74)" in dup_text
          and "Philosophy of Logic" in dup_text, dup_text[-900:])
    raw_csv = read_bytes(duplicates_csv) if os.path.isfile(duplicates_csv) else b""
    check("(m) duplicates.csv：UTF-8 带 BOM（Excel 打开中文不乱码）",
          raw_csv.startswith(b"\xef\xbb\xbf"))
    rows: list[dict] = []
    if raw_csv:
        import csv
        with open(duplicates_csv, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    expected_columns = {"pair_no", "similarity", "key_a", "key_b", "title_a", "title_b",
                        "author_a", "author_b", "year_a", "year_b", "issues_a", "issues_b"}
    check("(m) duplicates.csv：全量行 + 列齐全（相似度降序）",
          len(rows) == 1 and expected_columns <= set(rows[0].keys())
          and rows[0]["key_a"] == "Quine1970" and rows[0]["key_b"] == "Quine70",
          str(rows[0] if rows else None)[:300])

    # 幂等：同一份库重复生成 → 文件逐字节相同、CLI 明说未重写
    digest_before = {pathlib.Path(path).name: hashlib.sha256(read_bytes(path)).hexdigest()
                     for path in (conflicts_md, duplicates_md, duplicates_csv)}
    r2 = run_cli("report", "--db", db, "--out", out)
    digest_after = {pathlib.Path(path).name: hashlib.sha256(read_bytes(path)).hexdigest()
                    for path in (conflicts_md, duplicates_md, duplicates_csv)}
    check("(m) 重复 `report`：三个文件逐字节不变（清单无生成时间）",
          r2.returncode == 0 and digest_before == digest_after)
    check("(m) 重复 `report`：CLI 明说「内容未变（未重写）」",
          "未重写" in r2.stdout, r2.stdout[-300:])

    # 单独子命令
    out_conf = os.path.join(tmp, "only_conflicts")
    r3 = run_cli("report-conflicts", "--db", db, "--out", out_conf)
    check("(m) `report-conflicts` 只生成 CONFLICTS.md",
          r3.returncode == 0 and os.path.isfile(os.path.join(out_conf, "CONFLICTS.md"))
          and not os.path.exists(os.path.join(out_conf, "DUPLICATES.md")))
    out_dup = os.path.join(tmp, "only_duplicates")
    r4 = run_cli("report-duplicates", "--db", db, "--out", out_dup)
    check("(m) `report-duplicates` 只生成 DUPLICATES.md + duplicates.csv",
          r4.returncode == 0 and os.path.isfile(os.path.join(out_dup, "DUPLICATES.md"))
          and os.path.isfile(os.path.join(out_dup, "duplicates.csv"))
          and not os.path.exists(os.path.join(out_dup, "CONFLICTS.md")))

    # 空库：仍能出清单（0 条），不报错
    empty_db = os.path.join(tmp, "empty.jsonl")
    bibdb.Store.load(empty_db, create=True).save()
    out_empty = os.path.join(tmp, "empty_reports")
    r5 = run_cli("report", "--db", empty_db, "--out", out_empty)
    empty_text = _read_text(os.path.join(out_empty, "CONFLICTS.md")) if r5.returncode == 0 else ""
    check("(m) 空库 `report`：退出 0、冲突清单写「（无…）」",
          r5.returncode == 0 and "（无：引用库当前没有字段冲突。）" in empty_text,
          (r5.stdout + r5.stderr)[-200:])
    r6 = run_cli("report", "--db", os.path.join(tmp, "missing.jsonl"), "--out", out)
    check("(m) 库不存在时 `report` 退出 2（明确报错）", r6.returncode == 2
          and "引用库不存在" in (r6.stdout + r6.stderr))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (n) 界面用的 CLI 能力：--dry-run / 单期 import-dir / --bibtex ──")

with tempfile.TemporaryDirectory(prefix="bibdb_gui_") as tmp:
    db = os.path.join(tmp, "gui.jsonl")
    bib = write(os.path.join(tmp, "gui.bib"), BASE_BIB)

    # ── dry-run：只算不写（库不存在也不能被新建）──
    r = run_cli("import-bib", "--bib", bib, "--issue", "2026年第2期(87)",
                "--slug", "02 Kun_Xing", "--verified", "--dry-run", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(n) import-bib --dry-run --json 退出 0、标 dry_run/written/db_existed",
          r.returncode == 0 and payload.get("dry_run") is True
          and payload.get("written") is False and payload.get("db_existed") is False,
          (r.stdout + r.stderr)[:300])
    check("(n) dry-run **不新建库文件**（一个字节都不写）", not os.path.exists(db))
    check("(n) dry-run 的 report 计数与逐条 changes 都在",
          payload.get("report", {}).get("new") == 2
          and len(payload.get("changes") or []) == 2
          and {row["key"] for row in payload["changes"]} == {"Smith2020", "Jones2019"},
          json.dumps(payload.get("report"), ensure_ascii=False))

    # ── 真正导入 → 再 dry-run：库文件不变、明细为空 ──
    r = run_cli("import-bib", "--bib", bib, "--issue", "2026年第2期(87)",
                "--slug", "02 Kun_Xing", "--verified", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(n) 真导入：written=true、库文件出现", r.returncode == 0
          and payload.get("written") is True and os.path.isfile(db))
    digest = read_bytes(db)
    r = run_cli("import-bib", "--bib", bib, "--issue", "2026年第2期(87)",
                "--slug", "02 Kun_Xing", "--verified", "--dry-run", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(n) 幂等输入的 dry-run：merged=2、changes 为空、库文件逐字节不变",
          r.returncode == 0 and payload.get("report", {}).get("merged") == 2
          and not payload.get("changes") and read_bytes(db) == digest,
          (r.stdout + r.stderr)[:300])

    # ── 人读输出：明说「预览 / 未写库」──
    db_human = os.path.join(tmp, "gui_human.jsonl")
    r = run_cli("import-bib", "--bib", bib, "--issue", "2026年第2期(87)",
                "--slug", "02 Kun_Xing", "--verified", "--dry-run", "--db", db_human)
    check("(n) 人读 dry-run：明说「预览」「未写库」，且库没被建出来",
          r.returncode == 0 and "预览（dry-run" in r.stdout and "未写库" in r.stdout
          and not os.path.exists(db_human), (r.stdout + r.stderr)[:300])

    # ── import-dir：--root 给**一期目录**时只导入这一期（界面「入藏」选整期根目录）──
    root = os.path.join(tmp, "稿件库")
    make_issue(root, "2026年第2期(87)", "02 Kun_Xing", "reference.bib", BASE_BIB)
    make_issue(root, "2025年第1期(80)", "01 Other_Author", "reference.bib",
               "@article{Other2020, author = {Other, O.}, title = {Elsewhere}, year = {2020}}\n")
    issue_dir = os.path.join(root, "2026年第2期(87)")
    db_issue = os.path.join(tmp, "gui_issue.jsonl")
    r = run_cli("import-dir", "--root", issue_dir, "--dry-run", "--json", "--db", db_issue)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(n) import-dir --root 一期目录：scope=issue、只列这一期、dry-run 不建库",
          r.returncode == 0 and payload.get("scope") == "issue"
          and [issue["issue"] for issue in payload["result"]["issues"]]
          == ["2026年第2期(87)"]
          and payload.get("written") is False and not os.path.exists(db_issue),
          (r.stdout + r.stderr)[:400])
    r = run_cli("import-dir", "--root", issue_dir, "--json", "--db", db_issue)
    check("(n) import-dir 单期真导入：成品期入 verified",
          r.returncode == 0 and bibdb.Store.load(db_issue).get("Smith2020")["verified"] is True)
    check("(n) 单期导入**只**有这一期的条目（别的期没被带进来）",
          bibdb.Store.load(db_issue).get("Other2020") is None)

    # ── query --bibtex ──
    r = run_cli("query", "--key", "Smith2020", "--bibtex", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    hit = (payload.get("hits") or [{}])[0]
    text = hit.get("bibtex") or ""
    check("(n) query --bibtex --json：命中带可直接复制的 BibTeX 段",
          r.returncode == 0 and text.startswith("@article{Smith2020,")
          and "title = {A Theory of Everything}" in text
          and "doi = " in text, text[:200])
    r = run_cli("query", "--key", "Smith2020", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(n) 不带 --bibtex 时 JSON 与从前一样（没有 bibtex 键）",
          "bibtex" not in (payload.get("hits") or [{}])[0])
    r = run_cli("query", "--key", "Smith2020", "--bibtex", "--db", db)
    check("(n) 人读 --bibtex：打「BibTeX：」块", r.returncode == 0
          and "BibTeX：" in r.stdout and "@article{Smith2020," in r.stdout,
          (r.stdout + r.stderr)[-200:])

    # ── dry-run 不碰稿件库（只读）──
    snapshot = {str(path): hashlib.sha256(read_bytes(str(path))).hexdigest()
                for path in pathlib.Path(root).rglob("*") if path.is_file()}
    r = run_cli("import-dir", "--root", root, "--dry-run", "--json",
                "--db", os.path.join(tmp, "gui_root.jsonl"))
    after = {str(path): hashlib.sha256(read_bytes(str(path))).hexdigest()
             for path in pathlib.Path(root).rglob("*") if path.is_file()}
    check("(n) 稿件库 dry-run 只读：文件一个字节都没变",
          r.returncode == 0 and snapshot == after)
    empty_root = os.path.join(tmp, "空目录")
    os.makedirs(empty_root, exist_ok=True)
    r = run_cli("import-dir", "--root", empty_root, "--dry-run", "--json",
                "--db", os.path.join(tmp, "gui_root2.jsonl"))
    check("(n) 既不是稿件库根也不是期目录：扫到 0 期、退出 0（不崩）",
          r.returncode == 0 and json.loads(r.stdout)["result"]["issues"] == [])


# ─────────────────────────────────────────────────────────────────────────
print("\n── (o) 冲突裁决持久化（resolve）与清单稳定编号 ──")


def _make_conflict_db(path: str) -> str:
    """合成库：Alpha2000（2025 期新稿起冲突）+ Beta2001（2026 期新稿起冲突）。"""
    store = bibdb.Store.load(path, create=True)
    base = {"kind": "published-bib", "issue": "2023年第1期(68)",
            "slug": "01 Base", "file": "base.bib"}
    store.import_bib_text("""@article{Alpha2000,
  author  = {Alpha, Ann},
  title   = {First Work},
  journal = {Mind},
  volume  = {1},
  year    = {2000}
}
@article{Beta2001,
  author  = {Beta, Bob},
  title   = {Second Work},
  journal = {Journal One},
  year    = {2001}
}
""", base, verified=True)
    # Alpha 的冲突来源期 2025 → 排序在前（C001）
    store.import_bib_text("""@article{Alpha2000,
  author  = {Alpha, Ann},
  title   = {First Work},
  journal = {Mind (new series)},
  volume  = {2},
  year    = {2000}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "02 A2", "file": "a2.bib"}, verified=True)
    # Beta 的冲突来源期 2026 → 排序在后（C002）
    store.import_bib_text("""@article{Beta2001,
  author  = {Beta, Bob},
  title   = {Second Work},
  journal = {Journal Two},
  year    = {2001}
}
""", {"kind": "published-bib", "issue": "2026年第1期(86)",
      "slug": "03 B2", "file": "b2.bib"}, verified=True)
    store.save()
    return path


def _cmap(report_path: str) -> list[tuple[str, str]]:
    """从生成的清单里取 [(C 编号, 库内 key), ...]（明细区顺序）。"""
    import re as _re
    text = _read_text(report_path)
    return _re.findall(r"^##### C(\d+) · `([^`]+)`", text, _re.M)


with tempfile.TemporaryDirectory(prefix="bibdb_resolve_") as tmp:
    db = _make_conflict_db(os.path.join(tmp, "resolve.jsonl"))
    out1 = os.path.join(tmp, "r1")
    r = run_cli("report-conflicts", "--db", db, "--out", out1)
    text1 = _read_text(os.path.join(out1, "CONFLICTS.md"))
    check("(o) 初始清单：两条冲突按 C001/C002 排列、均为待裁决",
          r.returncode == 0 and _cmap(os.path.join(out1, "CONFLICTS.md"))
          == [("001", "Alpha2000"), ("002", "Beta2001")]
          and "待裁决冲突项：**2** 条" in text1
          and "已裁决（不在下面明细里）：**0** 条" in text1, text1[:600])
    check("(o) 清单里写明「裁决结果存进库、勾选只是草稿」",
          "裁决结果会存进库" in text1 and "勾选只是草稿" in text1
          and "resolve --key" in text1)
    check("(o) 每条待裁决项带「冲突指纹」（可用 --incoming-sha256 精确定位）",
          text1.count("- **冲突指纹**：`") == 2
          and "--incoming-sha256" in text1, text1[:800])

    # ── 错误路径：未知 key / 未知字段 → 退出 2 ──
    r = run_cli("resolve", "--key", "NoSuchKey", "--action", "keep",
                "--all-fields", "--db", db)
    check("(o) 未知 key → 退出 2 且给提示", r.returncode == 2
          and "没有记录 key" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep",
                "--fields", "no-such-field", "--db", db)
    check("(o) 未知字段 → 退出 2 且列出可用字段", r.returncode == 2
          and "没有这些字段" in (r.stdout + r.stderr)
          and "journal" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-300:])
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep", "--db", db)
    check("(o) 既不给 --fields 也不给 --all-fields → 用法错误退出 2",
          r.returncode == 2, (r.stdout + r.stderr)[-200:])

    # ── --dry-run：只算不写（库文件逐字节不变）──
    digest_before = read_bytes(db)
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep",
                "--all-fields", "--dry-run", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(o) resolve --dry-run --json：退出 0、count=1、changed=false、dry_run=true",
          r.returncode == 0 and payload.get("count") == 1
          and payload.get("changed") is False and payload.get("dry_run") is True
          and payload.get("written") is False, json.dumps(payload, ensure_ascii=False)[:400])
    check("(o) resolve --dry-run **不写库**（库文件逐字节不变）",
          read_bytes(db) == digest_before)
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep",
                "--all-fields", "--dry-run", "--db", db)
    check("(o) 人读 --dry-run 明说「只算不写库」", r.returncode == 0
          and "--dry-run" in r.stdout and "只算不写库" in r.stdout, r.stdout[-300:])

    # ── keep：写进库（resolution=kept-existing + resolved_at），字段值不变 ──
    store_before = bibdb.Store.load(db)
    fields_before = json.loads(json.dumps(store_before.get("Alpha2000")["fields"]))
    sha_before = store_before.get("Alpha2000")["content_sha256"]
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep",
                "--all-fields", "--note", "编辑确认：保留出版版本", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    store_after = bibdb.Store.load(db)
    conflict = store_after.get("Alpha2000")["conflicts"][0]
    check("(o) resolve keep：退出 0、written=true、命中 1 条",
          r.returncode == 0 and payload.get("written") is True
          and payload.get("count") == 1 and payload.get("changed") is True,
          json.dumps(payload, ensure_ascii=False)[:300])
    check("(o) keep 写入 resolution=kept-existing + resolved_at + note（备注保留）",
          conflict["resolution"] == "kept-existing" and conflict.get("resolved_at")
          and "编辑确认：保留出版版本" in (conflict.get("note") or ""),
          json.dumps(conflict, ensure_ascii=False)[:400])
    check("(o) resolve **不改字段值**（fields/content_sha256 逐字不变）",
          store_after.get("Alpha2000")["fields"] == fields_before
          and store_after.get("Alpha2000")["content_sha256"] == sha_before)

    # ── 幂等：同一动作再执行 → 无变化、库字节不变 ──
    digest_after_keep = read_bytes(db)
    r = run_cli("resolve", "--key", "Alpha2000", "--action", "keep",
                "--all-fields", "--db", db)
    check("(o) 重复 keep 幂等：退出 0、说「无变化」、库文件不动",
          r.returncode == 0 and "无变化" in r.stdout
          and read_bytes(db) == digest_after_keep, r.stdout[-300:])

    # ── 编号稳定：裁决 C001 后，剩余条目仍是 C002（不重排）──
    out2 = os.path.join(tmp, "r2")
    r = run_cli("report-conflicts", "--db", db, "--out", out2)
    text2 = _read_text(os.path.join(out2, "CONFLICTS.md"))
    check("(o) 裁决一条后：明细只剩 C002（编号不变、不重排）",
          r.returncode == 0 and _cmap(os.path.join(out2, "CONFLICTS.md"))
          == [("002", "Beta2001")] and "C001" not in text2.split("## 待裁决明细")[1],
          str(_cmap(os.path.join(out2, "CONFLICTS.md"))))
    check("(o) 头部统计区分待裁决/已裁决（1 条待裁决 / 1 条已裁决保留旧值）",
          "待裁决冲突项：**1** 条" in text2
          and "已裁决（不在下面明细里）：**1** 条"
          and "保留旧值 1 / 采用新值·逐字段 0 / 采用新值·整条 0 / 待人工 0 / "
              "被 adopt 取代 0" in text2, text2[:800])

    # ── defer：标记待人工查证 → 两条都从待裁决区消失 ──
    r = run_cli("resolve", "--key", "Beta2001", "--action", "defer",
                "--fields", "journal", "--db", db)
    check("(o) resolve defer --fields journal：退出 0、命中 1 条、人读明说已写库",
          r.returncode == 0 and "命中" in r.stdout and "已写库" in r.stdout, r.stdout[-400:])
    check("(o) resolve 人读输出带当前「待裁决 / 已裁决」计数",
          "当前：冲突 2 条（待裁决 0 / 已裁决 2）" in r.stdout, r.stdout[-400:])
    out3 = os.path.join(tmp, "r3")
    r = run_cli("report-conflicts", "--db", db, "--out", out3)
    text3 = _read_text(os.path.join(out3, "CONFLICTS.md"))
    store3 = bibdb.Store.load(db)
    check("(o) defer 写入 resolution=deferred + resolved_at",
          store3.get("Beta2001")["conflicts"][0]["resolution"] == "deferred"
          and store3.get("Beta2001")["conflicts"][0].get("resolved_at"))
    check("(o) 全部裁决后：待裁决明细为空、统计为 0 / 2（保留旧值1、待人工1）",
          "待裁决冲突项：**0** 条" in text3
          and "已裁决（不在下面明细里）：**2** 条" in text3
          and "保留旧值 1 / 采用新值·逐字段 0 / 采用新值·整条 0 / 待人工 1 / "
              "被 adopt 取代 0" in text3
          and "（无：所有冲突都已裁决" in text3, text3[:900])

    # ── 报告在「已裁决」状态下重复生成逐字节相同 ──
    digest = {pathlib.Path(path).name: hashlib.sha256(read_bytes(path)).hexdigest()
              for path in (os.path.join(out3, "CONFLICTS.md"),)}
    r = run_cli("report-conflicts", "--db", db, "--out", out3)
    digest2 = {pathlib.Path(path).name: hashlib.sha256(read_bytes(path)).hexdigest()
               for path in (os.path.join(out3, "CONFLICTS.md"),)}
    check("(o) 已裁决状态重复生成清单逐字节相同", r.returncode == 0 and digest == digest2)

# ── import-bib --force：被替换的旧冲突 → forced-replaced + resolved_at ──
with tempfile.TemporaryDirectory(prefix="bibdb_resolve_force_") as tmp:
    db = os.path.join(tmp, "force.jsonl")
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text("""@article{Gamma2010,
  author  = {Gamma, Gina},
  title   = {Third Work},
  journal = {Journal Old},
  volume  = {1},
  year    = {2010}
}
""", {"kind": "published-bib", "issue": "2023年第1期(68)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    store.import_bib_text("""@article{Gamma2010,
  author  = {Gamma, Gina},
  title   = {Third Work},
  journal = {Journal New},
  volume  = {2},
  year    = {2010}
}
""", {"kind": "published-bib", "issue": "2024年第1期(74)",
      "slug": "02 Newer", "file": "newer.bib"}, verified=True)
    # 第二条记录（冲突来源期更晚 → 编号 C002）：force 之后它的编号必须仍是 C002
    store.import_bib_text("""@article{Zeta2012,
  author  = {Zeta, Zoe},
  title   = {Fifth Work},
  journal = {Journal Z1},
  year    = {2012}
}
""", {"kind": "published-bib", "issue": "2023年第1期(68)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    store.import_bib_text("""@article{Zeta2012,
  author  = {Zeta, Zoe},
  title   = {Fifth Work},
  journal = {Journal Z2},
  year    = {2012}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "04 Z", "file": "z.bib"}, verified=True)
    store.save()
    before_dir = os.path.join(tmp, "before_reports")
    run_cli("report-conflicts", "--db", db, "--out", before_dir)
    before_map = _cmap(os.path.join(before_dir, "CONFLICTS.md"))
    check("(o) force 前：Gamma=C001、Zeta=C002",
          before_map == [("001", "Gamma2010"), ("002", "Zeta2012")], str(before_map))
    one = write(os.path.join(tmp, "one.bib"), """@article{Gamma2010,
  author  = {Gamma, Gina},
  title   = {Third Work},
  journal = {Journal Chosen},
  volume  = {3},
  year    = {2010}
}
""")
    r = run_cli("import-bib", "--bib", one, "--issue", "2026年第1期(86)",
                "--slug", "09 Adjudicated", "--force", "--db", db)
    store_force = bibdb.Store.load(db)
    conflicts = store_force.get("Gamma2010")["conflicts"]
    check("(o) import-bib --force：退出 0、记录字段采用新值",
          r.returncode == 0 and store_force.get("Gamma2010")["fields"]["journal"]
          == "Journal Chosen")
    forced = [c for c in conflicts if c.get("resolution") == "forced-replaced"]
    check("(o) force 后被替换的旧冲突与本次替换都标 forced-replaced + resolved_at",
          len(forced) >= 2 and all(c.get("resolved_at") for c in forced),
          json.dumps(conflicts, ensure_ascii=False)[:600])
    check("(o) force 后没有遗留「待裁决」冲突（都带了 resolved_at）",
          all(c.get("resolved_at") for c in conflicts),
          str([c.get("resolution") for c in conflicts]))
    out = os.path.join(tmp, "force_reports")
    r = run_cli("report-conflicts", "--db", db, "--out", out)
    force_text = _read_text(os.path.join(out, "CONFLICTS.md"))
    after_map = _cmap(os.path.join(out, "CONFLICTS.md"))
    check("(o) force 后清单：待裁决只剩 1 条、已裁决含「采用新值」≥1",
          r.returncode == 0 and "待裁决冲突项：**1** 条" in force_text
          and "采用新值" in force_text
          and "已裁决（不在下面明细里）：" in force_text, force_text[:800])
    check("(o) force 不挤动后面条目的编号：Zeta 仍是 C002（force 审计条目不编号）",
          after_map == [("002", "Zeta2012")], str(after_map))

# ── 冲突指纹：同一记录多条来源不同的冲突，精确裁决一条（--fields 会误伤）──
with tempfile.TemporaryDirectory(prefix="bibdb_resolve_sha_") as tmp:
    db = os.path.join(tmp, "sha.jsonl")
    store = bibdb.Store.load(db, create=True)
    store.import_bib_text("""@article{Delta2015,
  author  = {Delta, Dan},
  title   = {Fourth Work},
  journal = {Journal Old},
  year    = {2015}
}
""", {"kind": "published-bib", "issue": "2023年第1期(68)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    # 两条来源不同的冲突：字段都含 author（--fields author 会同时命中两条）
    store.import_bib_text("""@article{Delta2015,
  author  = {Delta, Daniel},
  title   = {Fourth Work},
  journal = {Journal Old},
  year    = {2015}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "02 A", "file": "a.bib"}, verified=True)
    store.import_bib_text("""@article{Delta2015,
  author  = {Delta, D.},
  title   = {Fourth Work},
  journal = {Journal Old},
  year    = {2015}
}
""", {"kind": "published-bib", "issue": "2026年第1期(86)",
      "slug": "03 B", "file": "b.bib"}, verified=True)
    store.save()
    record = bibdb.Store.load(db).get("Delta2015")
    pending = [c for c in record["conflicts"] if bibdb.conflict_is_pending(c)]
    check("(o) 同一记录有 2 条待裁决冲突（都含 author 字段）",
          len(pending) == 2
          and all("author" in (c.get("field_diff") or {}) for c in pending),
          str([c.get("incoming_sha256", "")[:8] for c in pending]))
    target_sha = str(pending[1].get("incoming_sha256") or "")
    r = run_cli("resolve", "--key", "Delta2015", "--action", "keep",
                "--incoming-sha256", target_sha[:10], "--db", db)
    record_after = bibdb.Store.load(db).get("Delta2015")
    still_pending = [c for c in record_after["conflicts"]
                     if bibdb.conflict_is_pending(c)]
    check("(o) --incoming-sha256 只裁决指纹那一条（另一条仍待裁决）",
          r.returncode == 0 and len(still_pending) == 1
          and still_pending[0].get("incoming_sha256") == pending[0].get("incoming_sha256"),
          (r.stdout + r.stderr)[-300:])
    r = run_cli("resolve", "--key", "Delta2015", "--action", "keep",
                "--incoming-sha256", "00000000", "--db", db)
    check("(o) 指纹不匹配 → 退出 2", r.returncode == 2
          and "没有以" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "Delta2015", "--action", "keep",
                "--incoming-sha256", "abc", "--db", db)
    check("(o) 指纹太短（<8 位）→ 退出 2", r.returncode == 2
          and "至少 8 位" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (p) B1 确定性规范化 dry-run（normalize）：只出报告、绝不写库 ──")


def _make_normalize_db(path: str) -> str:
    """合成库：仅格式 / 语义变更（补 userf、版次并入）/ 高危字段名 / 异常值。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text("""
@article{Format2001,
  author  = {Doe, Jane},
  title   = {A Plain English Article},
  journal = {Some Journal},
  year    = {2001}
}
@article{Cn2020,
  author  = {张三},
  title   = {中文题名测试},
  journal = {逻辑学研究},
  year    = {2020}
}
@book{EngBook1999,
  author    = {Smith, John},
  title     = {A Book About Logic},
  publisher = {Oxford University Press},
  year      = {1999},
  edition   = {2nd Edition}
}
@article{Risk2001,
  author  = {Doe, Jane},
  title   = {Native Field Name},
  journal = {Journal},
  year    = {2001},
  posted-at = {2007-10-08 10:42:18}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    # 异常：字段值里有未配对引号 → normalize_bib 的 parse_fields 抛 ValueError
    store.import_bib_text("""@article{Bad1999,
  author = {Smith, John},
  title  = {Bad Entry},
  annote = {doi = "10.1/x},
  year   = {1999}
}
""", {"kind": "published-bib", "issue": "2025年第2期(81)",
      "slug": "02 Bad", "file": "bad.bib"}, verified=True)
    store.save()
    return path


with tempfile.TemporaryDirectory(prefix="bibdb_normalize_") as tmp:
    db = _make_normalize_db(os.path.join(tmp, "norm.jsonl"))
    digest_before = read_bytes(db)
    out = os.path.join(tmp, "b1")
    r = run_cli("normalize", "--db", db, "--out-dir", out, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    report_md = os.path.join(out, "B1-normalize-dryrun.md")
    report_json = os.path.join(out, "B1-normalize-dryrun.json")
    text = _read_text(report_md) if os.path.isfile(report_md) else ""
    check("(p) `normalize --out-dir --json` 退出 0、生成 md + json 报告",
          r.returncode == 0 and os.path.isfile(report_md) and os.path.isfile(report_json),
          (r.stdout + r.stderr)[-400:])
    # 第四十五批：连字符字段（posted-at）现在能被正确解析 → Risk2001 由「语义变更」
    # 降为「仅格式变化」；语义 2 / 仅格式 2 / 异常 1。
    check("(p) --json 摘要：记录数 / 语义 2 / 仅格式 2 / 异常 1",
          payload.get("records") == 5 and payload.get("semantic") == 2
          and payload.get("format_only") == 2 and payload.get("errors") == 1
          and payload.get("db_unchanged") is True,
          json.dumps({k: payload.get(k) for k in
                      ("records", "semantic", "format_only", "errors", "db_unchanged")},
                     ensure_ascii=False))
    check("(p) --json 摘要：库 sha 跑前 = 跑后（未写库）",
          payload.get("sha256_before") == payload.get("sha256_after")
          and payload.get("sha256_before") == hashlib.sha256(digest_before).hexdigest())
    check("(p) **绝不写 bibliogranphy.jsonl**：库文件逐字节不变",
          read_bytes(db) == digest_before)
    check("(p) 报告头部：标题 + 跑前/跑后 sha256 + 三类统计",
          "B1 确定性规范化 dry-run 报告" in text
          and "库 sha256（跑前）" in text and "库 sha256（跑后）" in text
          and "语义变更 2 条" in text and "仅格式变化 2 条" in text
          and "异常 1 条" in text and "未改动库" in text, text[:800])
    check("(p) 报告头部写明「dry-run 不写库 / 无时间戳 / --apply 留给后续批次」",
          "dry-run 只算不写" in text and "不含生成时间戳" in text
          and "--apply" in text and "未修改库" in text)
    check("(p) 规则分类表：补 userf / 英文书版次并入 title",
          "补 userf = {cn/jp}" in text and "英文书版次并入 title" in text
          and "| 规则 | 语义变更条目数 |" in text)
    check("(p) 字段分布表：userf / title / edition / posted-at",
          "标点体例（`userf`）" in text and "题名（`title`）" in text
          and "版次（`edition`）" in text, text[:1800])
    check("(p) 高危形状风险：非标准字段名 `posted-at` 被点名",
          "高危形状风险" in text and "`posted-at`" in text and "拆分/改名" in text)
    check("(p) 逐条语义变更明细含两个 key 与旧值→新值",
          "逐条语义变更（2 条" in text
          and all(key in text for key in ("Cn2020", "EngBook1999"))
          and "→" in text, text[-1800:])
    check("(p) 异常清单：Bad1999 + 引号未闭合原因（不静默吞）",
          "异常清单（1 条" in text and "Bad1999" in text
          and ("未配对的引号" in text or "引号值未闭合" in text), text[-1200:])
    payload_json = json.loads(_read_text(report_json)) if os.path.isfile(report_json) else {}
    check("(p) JSON 报告：语义/异常逐条明细 + 库未改动",
          payload_json.get("db_unchanged") is True
          and {row["key"] for row in payload_json.get("semantic_entries") or []}
          == {"Cn2020", "EngBook1999"}
          and [row["key"] for row in payload_json.get("error_entries") or []] == ["Bad1999"],
          json.dumps(payload_json, ensure_ascii=False)[:300])
    check("(p) JSON 报告带高风险记录数与风险分布（posted-at 与 值内嵌 name= 都被点名）",
          payload_json.get("risky_records") == 2
          and any("posted-at" in label for label in (payload_json.get("risks") or {}))
          and any("annote" in label for label in (payload_json.get("risks") or {})),
          str(payload_json.get("risks"))[:300])
    # 幂等：同一份库重复生成 → md/json 逐字节相同、CLI 明说未重写
    md_digest = hashlib.sha256(read_bytes(report_md)).hexdigest()
    json_digest = hashlib.sha256(read_bytes(report_json)).hexdigest()
    r2 = run_cli("normalize", "--db", db, "--out-dir", out)
    check("(p) 重复 `normalize`：md / json 逐字节相同 + 明说「内容未变」",
          r2.returncode == 0
          and hashlib.sha256(read_bytes(report_md)).hexdigest() == md_digest
          and hashlib.sha256(read_bytes(report_json)).hexdigest() == json_digest
          and "内容未变" in r2.stdout, r2.stdout[-300:])
    check("(p) 重复运行后库文件仍逐字节不变", read_bytes(db) == digest_before)
    # --dry-run 显式开关 + --report 自定义路径
    custom = os.path.join(tmp, "custom_report.md")
    r3 = run_cli("normalize", "--db", db, "--dry-run", "--report", custom)
    check("(p) `--dry-run --report PATH`：退出 0、写到指定路径、不写库",
          r3.returncode == 0 and os.path.isfile(custom)
          and read_bytes(db) == digest_before, (r3.stdout + r3.stderr)[-300:])
    # 库不存在 → 退出 2
    r4 = run_cli("normalize", "--db", os.path.join(tmp, "missing.jsonl"))
    check("(p) 库不存在时 normalize 退出 2", r4.returncode == 2
          and "引用库不存在" in (r4.stdout + r4.stderr))
    # 不提供 --apply（本任务不做）→ 不认识的参数退出 2
    r5 = run_cli("normalize", "--db", db, "--apply")
    check("(p) 不认识的 `--apply` → 用法错误退出 2（本任务不实现 apply）",
          r5.returncode == 2)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (q) resolve --action adopt：逐字段采用新稿值 ──")


def _make_adopt_db(path: str) -> str:
    """合成库：一条记录 3 个来源不同、字段有重叠的待裁决冲突 + 一条「新稿删字段」。"""
    store = bibdb.Store.load(path, create=True)
    base = {"kind": "published-bib", "issue": "2023年第1期(68)",
            "slug": "01 Base", "file": "base.bib"}
    store.import_bib_text("""@article{Adopt3000,
  author    = {Adopt, Ann},
  title     = {Adoption Work},
  journal   = {Journal Old},
  volume    = {3},
  pages     = {1--10},
  publisher = {Base Press},
  year      = {2000}
}
@article{DropField2001,
  author = {Drop, Dan},
  title  = {Droppable Pages},
  journal = {Journal Old},
  pages  = {5--6},
  year   = {2001}
}
""", base, verified=True)
    # 冲突 1（来源期 2025-1）：只差 journal；与冲突 3 的 journal 重叠
    store.import_bib_text("""@article{Adopt3000,
  author    = {Adopt, Ann},
  title     = {Adoption Work},
  journal   = {Journal New},
  volume    = {3},
  pages     = {1--10},
  publisher = {Base Press},
  year      = {2000}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "02 A", "file": "a.bib"}, verified=True)
    # 冲突 2（来源期 2025-2）：差 pages/volume，不与冲突 1 重叠
    store.import_bib_text("""@article{Adopt3000,
  author    = {Adopt, Ann},
  title     = {Adoption Work},
  journal   = {Journal Old},
  volume    = {4},
  pages     = {20--30},
  publisher = {Base Press},
  year      = {2000}
}
""", {"kind": "published-bib", "issue": "2025年第2期(81)",
      "slug": "03 B", "file": "b.bib"}, verified=True)
    # 冲突 3（来源期 2026-1）：差 journal/publisher；journal 与冲突 1 重叠
    store.import_bib_text("""@article{Adopt3000,
  author    = {Adopt, Ann},
  title     = {Adoption Work},
  journal   = {Journal Third},
  volume    = {3},
  pages     = {1--10},
  publisher = {Third Press},
  year      = {2000}
}
""", {"kind": "published-bib", "issue": "2026年第1期(86)",
      "slug": "04 C", "file": "c.bib"}, verified=True)
    # DropField：新稿没有 pages → 冲突 incoming 为空（adopt 应删除该字段）
    store.import_bib_text("""@article{DropField2001,
  author  = {Drop, Dan},
  title   = {Droppable Pages},
  journal = {Journal Old},
  year    = {2001}
}
""", {"kind": "published-bib", "issue": "2025年第3期(82)",
      "slug": "05 D", "file": "d.bib"}, verified=True)
    store.save()
    return path


def _pending(store, key):
    record = store.get(key)
    return [c for c in (record.get("conflicts") or [])
            if bibdb.conflict_is_pending(c)]


with tempfile.TemporaryDirectory(prefix="bibdb_adopt_") as tmp:
    db = _make_adopt_db(os.path.join(tmp, "adopt.jsonl"))
    store = bibdb.Store.load(db)
    adopt_conflicts = _pending(store, "Adopt3000")
    drop_conflicts = _pending(store, "DropField2001")
    check("(q) 夹具：Adopt3000 三条待裁决冲突、DropField2001 一条（incoming 空）",
          len(adopt_conflicts) == 3 and len(drop_conflicts) == 1
          and drop_conflicts[0]["field_diff"].get("pages", {}).get("incoming") == "",
          f"{len(adopt_conflicts)} / {len(drop_conflicts)}")
    sha_journal = adopt_conflicts[0]["incoming_sha256"]
    sha_pages = adopt_conflicts[1]["incoming_sha256"]
    sha_overlap = adopt_conflicts[2]["incoming_sha256"]
    record_before = json.loads(json.dumps(store.get("Adopt3000")))
    digest_before = read_bytes(db)

    # ── 默认拒绝重叠（不 --allow-overlap）→ 退出 2 且库不变 ──
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", sha_journal[:12], "--db", db)
    check("(q) adopt 与其它待裁决冲突字段重叠 → 默认拒绝、退出 2、给指引",
          r.returncode == 2
          and "重叠" in (r.stdout + r.stderr)
          and "allow-overlap" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-400:])
    check("(q) 拒绝时库文件逐字节不变", read_bytes(db) == digest_before)

    # ── dry-run + --allow-overlap：只报将改哪些字段，不写库 ──
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", sha_journal[:12], "--allow-overlap",
                "--dry-run", "--json", "--db", db)
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(q) adopt --dry-run --json：退出 0、标 dry_run、只列本条冲突",
          r.returncode == 0 and payload.get("dry_run") is True
          and payload.get("action") == "adopt" and payload.get("count") == 1,
          json.dumps(payload, ensure_ascii=False)[:400])
    check("(q) --dry-run 输出将改的字段旧→新（journal）与将重算的 content_sha256",
          payload.get("adopted_fields", {}).get("journal", {}).get("new") == "Journal New"
          and payload.get("adopted_fields", {}).get("journal", {}).get("old") == "Journal Old"
          and payload.get("content_sha256_before") != payload.get("content_sha256_after"))
    check("(q) --dry-run 报出重叠冲突（将被标 superseded）且库文件不变",
          len(payload.get("overlapping") or []) == 1
          and payload.get("superseded") == []
          and read_bytes(db) == digest_before)
    r_human = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                      "--incoming-sha256", sha_journal[:12], "--allow-overlap",
                      "--dry-run", "--db", db)
    check("(q) 人读 dry-run：明说「逐字段」「将被标 superseded」、不写库",
          r_human.returncode == 0 and "逐字段" in r_human.stdout
          and "将被标 `superseded`" in r_human.stdout
          and read_bytes(db) == digest_before, r_human.stdout[-500:])

    # ── 真正 adopt（--allow-overlap）：只改本条字段 + 重叠标 superseded ──
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", sha_journal[:12], "--allow-overlap",
                "--note", "用户裁决：采用新稿 journal", "--db", db)
    store_after = bibdb.Store.load(db)
    record_after = store_after.get("Adopt3000")
    check("(q) adopt 成功：退出 0、journal 采用新值、其它字段不动",
          r.returncode == 0
          and record_after["fields"]["journal"] == "Journal New"
          and record_after["fields"]["volume"] == "3"
          and record_after["fields"]["pages"] == "1--10"
          and record_after["fields"]["publisher"] == "Base Press"
          and record_after["fields"]["title"] == record_before["fields"]["title"],
          json.dumps(record_after["fields"], ensure_ascii=False))
    check("(q) content_sha256 已按新 fields 重算（与 content_sha256() 一致且已变）",
          record_after["content_sha256"]
          == bibdb.content_sha256(record_after["entry_type"], record_after["fields"])
          and record_after["content_sha256"] != record_before["content_sha256"])
    adopted_conflict = [c for c in record_after["conflicts"]
                        if c.get("incoming_sha256") == sha_journal][0]
    check("(q) 本条冲突标 adopted + resolved_at + note 追加",
          adopted_conflict["resolution"] == "adopted"
          and adopted_conflict.get("resolved_at")
          and "采用新稿值（逐字段" in (adopted_conflict.get("note") or ""))
    overlap_conflict = [c for c in record_after["conflicts"]
                        if c.get("incoming_sha256") == sha_overlap][0]
    check("(q) 重叠冲突被标 superseded + resolved_at + 说明「被 adopt 取代」",
          overlap_conflict["resolution"] == "superseded"
          and overlap_conflict.get("resolved_at")
          and "取代" in (overlap_conflict.get("note") or ""),
          overlap_conflict.get("note", "")[:120])
    check("(q) 非重叠的待裁决冲突保持 pending（不被误标）",
          any(c.get("incoming_sha256") == sha_pages and bibdb.conflict_is_pending(c)
              for c in record_after["conflicts"]))
    check("(q) 人读输出显示字段旧→新与 content_sha256 变化",
          "Journal Old" in r.stdout and "Journal New" in r.stdout
          and "content_sha256" in r.stdout, r.stdout[-600:])

    # ── 幂等：同指纹重复 adopt → 无变化、库文件不动 ──
    digest_after_adopt = read_bytes(db)
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", sha_journal[:12], "--db", db)
    check("(q) 重复同指纹 adopt 幂等：退出 0、说「无变化」、库文件不动",
          r.returncode == 0 and "无变化" in r.stdout
          and read_bytes(db) == digest_after_adopt, r.stdout[-300:])

    # ── 报告：已裁决统计分 adopted / superseded ──
    out_rep = os.path.join(tmp, "reports")
    run_cli("report-conflicts", "--db", db, "--out", out_rep)
    report_text = _read_text(os.path.join(out_rep, "CONFLICTS.md"))
    check("(q) 报告已裁决统计区分 adopted / superseded",
          "采用新值·逐字段 1" in report_text and "被 adopt 取代 1" in report_text,
          [line for line in report_text.splitlines() if "已裁决（不在下面明细里）" in line])
    check("(q) 报告待裁决区只剩非重叠那条（C 编号稳定的 3 条里只显示 1 条）",
          report_text.count("`Adopt3000`") >= 1
          and "采用新稿值（逐字段" not in report_text)

    # ── 非重叠冲突可正常 adopt ──
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", sha_pages[:12], "--db", db)
    record2 = bibdb.Store.load(db).get("Adopt3000")
    check("(q) 非重叠冲突 adopt：pages/volume 按新稿更新",
          r.returncode == 0 and record2["fields"]["pages"] == "20--30"
          and record2["fields"]["volume"] == "4",
          json.dumps(record2["fields"], ensure_ascii=False))

    # ── incoming 为空 → adopt 删除该字段 ──
    drop_sha = drop_conflicts[0]["incoming_sha256"]
    r = run_cli("resolve", "--key", "DropField2001", "--action", "adopt",
                "--incoming-sha256", drop_sha[:12], "--db", db)
    drop_record = bibdb.Store.load(db).get("DropField2001")
    check("(q) incoming 为空 → adopt 把该字段从记录里删掉",
          r.returncode == 0 and "pages" not in drop_record["fields"]
          and drop_record["fields"].get("title") == "Droppable Pages",
          json.dumps(drop_record["fields"], ensure_ascii=False))

    # ── 参数/指纹错误 → 退出 2 ──
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--incoming-sha256", "00000000", "--db", db)
    check("(q) 未知指纹 → 退出 2", r.returncode == 2
          and "没有以" in (r.stdout + r.stderr))
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "adopt",
                "--all-fields", "--db", db)
    check("(q) adopt 不给指纹（给 --all-fields）→ 退出 2",
          r.returncode == 2 and "incoming-sha256" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "Adopt3000", "--action", "keep",
                "--all-fields", "--allow-overlap", "--db", db)
    check("(q) `--allow-overlap` 误用于 keep → 退出 2", r.returncode == 2
          and "只用于" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    r = run_cli("resolve", "--key", "NoSuchKey", "--action", "adopt",
                "--incoming-sha256", "abcdef12", "--db", db)
    check("(q) 未知 key → 退出 2", r.returncode == 2
          and "没有记录 key" in (r.stdout + r.stderr))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (r) normalize --apply：B1 落库（安全条目）──")

with tempfile.TemporaryDirectory(prefix="bibdb_apply_") as tmp:
    db = _make_normalize_db(os.path.join(tmp, "apply.jsonl"))
    store = bibdb.Store.load(db)
    for key in [rec["key"] for rec in store.records()]:
        store.get(key)["updated_at"] = "2000-01-01T00:00:00+08:00"
    store.dirty = True
    store.save()
    store = bibdb.Store.load(db)
    before_records = {key: json.loads(json.dumps(store.get(key)))
                      for key in ("Format2001", "Cn2020", "EngBook1999",
                                  "Risk2001", "Bad1999")}
    digest_before = read_bytes(db)
    backup = os.path.join(tmp, "backup.jsonl")
    with open(backup, "wb") as fh:
        fh.write(read_bytes(db))
    report = os.path.join(tmp, "B1-apply.md")
    json_out = os.path.join(tmp, "B1-apply.json")

    # ── 缺备份 / 备份 sha 不符 / 与 --dry-run 互斥 → 退出 2，库不变 ──
    r = run_cli("normalize", "--db", db, "--apply", "--report", report)
    check("(r) --apply 不带 --backup → 退出 2，库文件不变",
          r.returncode == 2 and "backup" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_before)
    wrong = write(os.path.join(tmp, "wrong_backup.jsonl"), '{"version":1}\n')
    r = run_cli("normalize", "--db", db, "--apply", "--backup", wrong,
                "--report", report)
    check("(r) --backup 与当前库 sha 不一致 → 退出 2（防误 apply）",
          r.returncode == 2 and "不一致" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_before)
    r = run_cli("normalize", "--db", db, "--apply", "--backup", backup,
                "--dry-run", "--report", report)
    check("(r) --apply 与 --dry-run 互斥 → 退出 2", r.returncode == 2
          and ("互斥" in (r.stdout + r.stderr)
               or "not allowed with argument" in (r.stdout + r.stderr))
          and read_bytes(db) == digest_before)

    # ── 真正 apply ──
    r = run_cli("normalize", "--db", db, "--apply", "--backup", backup,
                "--report", report, "--json-out", json_out, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    store_after = bibdb.Store.load(db)
    cn = store_after.get("Cn2020")
    eng = store_after.get("EngBook1999")
    check("(r) apply 成功：退出 0、只 apply 安全条目（语义 2 条）+ 跳过 2 条",
          r.returncode == 0 and payload.get("applied") == 2
          and payload.get("applied_semantic") == 2
          and payload.get("skipped") == 2, json.dumps(payload, ensure_ascii=False)[:400])
    check("(r) 安全条目：补 userf / 版次并入 title 真正落库",
          cn["fields"].get("userf") == "cn"
          and eng["fields"].get("title") == "A Book About Logic, (2nd Edition)"
          and "edition" not in eng["fields"],
          f"{cn['fields']} / {eng['fields']}")
    check("(r) content_sha256 已按新 fields 重算（等于 content_sha256()）",
          cn["content_sha256"]
          == bibdb.content_sha256(cn["entry_type"], cn["fields"])
          and eng["content_sha256"]
          == bibdb.content_sha256(eng["entry_type"], eng["fields"]))
    check("(r) 只改了 fields/type/sha/updated_at：verified/sources/conflicts/aliases 不变",
          all(cn[k] == before_records["Cn2020"][k]
              for k in ("verified", "verified_at", "method", "sources",
                        "conflicts", "aliases", "created_at"))
          and all(eng[k] == before_records["EngBook1999"][k]
                  for k in ("verified", "sources", "conflicts", "aliases")),
          "元数据对比")
    check("(r) updated_at 已更新", cn["updated_at"] != before_records["Cn2020"]["updated_at"])
    check("(r) 坏条目未 apply：Risk2001 / Bad1999 字段逐字不变",
          store_after.get("Risk2001")["fields"]
          == before_records["Risk2001"]["fields"]
          and store_after.get("Bad1999")["fields"]
          == before_records["Bad1999"]["fields"])
    check("(r) 无需改动的 Format2001 未被 apply（fields/updated_at 不变）",
          store_after.get("Format2001")["fields"]
          == before_records["Format2001"]["fields"]
          and store_after.get("Format2001")["updated_at"]
          == before_records["Format2001"]["updated_at"])
    check("(r) 二次校验：排除未 apply 后剩余语义变更 = 0",
          payload["verification"]["semantic_excluding_skipped"] == 0
          and payload["verification"]["error_keys"] == ["Bad1999"],
          str(payload.get("verification"))[:300])
    check("(r) skips 清单点名 Risk2001（高危）与 Bad1999（解析失败）",
          {item["key"] for item in payload["skipped_entries"]}
          == {"Risk2001", "Bad1999"}
          and any("posted-at" in item["reason"] for item in payload["skipped_entries"]))
    report_text = _read_text(report) if os.path.isfile(report) else ""
    check("(r) apply 报告：sha 前/后、备份路径、apply 明细、未 apply 清单、二次校验",
          "apply 报告" in report_text and "库 sha256（apply 前）" in report_text
          and "备份" in report_text and "apply 明细" in report_text
          and "未 apply / 需人工处理清单" in report_text
          and "二次校验" in report_text
          and "semantic_excluding_skipped" not in report_text, report_text[:600])
    payload_json = json.loads(_read_text(json_out)) if os.path.isfile(json_out) else {}
    check("(r) apply JSON：applied/skipped/verification/backup 都在",
          payload_json.get("applied") == 2 and payload_json.get("skipped") == 2
          and payload_json.get("backup_sha256")
          == payload_json.get("sha256_before")
          and payload_json["verification"]["semantic_excluding_skipped"] == 0)
    # ── 幂等：再 apply 一次（用 apply 后的库做备份）→ 0 变更、库字节不变 ──
    backup2 = os.path.join(tmp, "backup2.jsonl")
    with open(backup2, "wb") as fh:
        fh.write(read_bytes(db))
    digest_after_apply = read_bytes(db)
    r = run_cli("normalize", "--db", db, "--apply", "--backup", backup2,
                "--json", "--report", report)
    payload2 = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(r) 二次 apply 幂等：applied=0、库文件逐字节不变",
          r.returncode == 0 and payload2.get("applied") == 0
          and read_bytes(db) == digest_after_apply, json.dumps(payload2, ensure_ascii=False)[:300])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (s) normalize-authors：B2 作者倒装（dry-run + apply）──")


def _make_author_db(path: str) -> str:
    """合成库：可倒装 / 多作者 / 虚词 / 跳过（CJK、机构、others、花括号、已倒装、单名）。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text("""
@article{Good1,
  author  = {John Smith},
  title   = {First},
  journal = {J},
  year    = {2001}
}
@article{Multi2,
  author  = {J. P. Dillard and C. Segrin},
  title   = {Second},
  journal = {J},
  year    = {2002}
}
@article{Particle3,
  author  = {B. C. van Fraassen},
  title   = {Third},
  journal = {J},
  year    = {2003}
}
@article{Inverted4,
  author  = {Smith, John},
  title   = {Fourth},
  journal = {J},
  year    = {2004}
}
@article{Chinese5,
  author  = {张三 and 李四},
  title   = {第五},
  journal = {J},
  year    = {2005}
}
@article{Institution6,
  author  = {World Health Organization},
  title   = {Sixth},
  journal = {J},
  year    = {2006}
}
@article{Others7,
  author  = {J. Smith and others},
  title   = {Seventh},
  journal = {J},
  year    = {2007}
}
@article{Braces8,
  author  = {{van Fraassen}, B. C.},
  title   = {Eighth},
  journal = {J},
  year    = {2008}
}
@article{Single9,
  author  = {Plato},
  title   = {Ninth},
  journal = {J},
  year    = {2009}
}
@book{EditorOnly10,
  editor  = {John Smith},
  title   = {Tenth},
  publisher = {P},
  year    = {2010}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    store.save()
    return path


with tempfile.TemporaryDirectory(prefix="bibdb_authors_") as tmp:
    db = _make_author_db(os.path.join(tmp, "authors.jsonl"))
    store = bibdb.Store.load(db)
    for key in [rec["key"] for rec in store.records()]:
        store.get(key)["updated_at"] = "2000-01-01T00:00:00+08:00"
    store.dirty = True
    store.save()
    store = bibdb.Store.load(db)
    before = {key: json.loads(json.dumps(store.get(key)))
              for key in ("Good1", "Multi2", "Particle3", "Inverted4",
                          "Chinese5", "Institution6", "Others7", "Braces8",
                          "Single9", "EditorOnly10")}
    digest_before = read_bytes(db)
    out_dry = os.path.join(tmp, "dry")
    r = run_cli("normalize-authors", "--db", db, "--out-dir", out_dry, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    dry_text = _read_text(os.path.join(out_dry, "B2-author-normalize-dryrun.md"))         if os.path.isfile(os.path.join(out_dry, "B2-author-normalize-dryrun.md")) else ""
    check("(s) dry-run：退出 0、生成 B2 dry-run 报告、库文件不变",
          r.returncode == 0 and os.path.isfile(os.path.join(out_dry, "B2-author-normalize-dryrun.md"))
          and read_bytes(db) == digest_before)
    check("(s) dry-run 统计：3 条将倒装（Given Surname / 多作者 / particles），其余跳过",
          payload.get("with_author") == 9 and payload.get("changes") == 3
          and payload.get("skips") == 6 and payload.get("db_unchanged") is True,
          json.dumps(payload, ensure_ascii=False)[:400])
    check("(s) 跳过原因分类覆盖 CJK/机构/and others/花括号/已倒装/单名",
          {"含中文/日文（不倒装）", "疑似机构/组织作者（不推断）",
           "含 `and others`（保持原样）", "含花括号/LaTeX 保护（保持原样）",
           "已倒装或含逗号（保持原样）", "单名/只有一个词（保持原样）"}
          <= set(payload.get("reasons") or {}), str(payload.get("reasons"))[:400])
    check("(s) dry-run 报告：标题/样例/跳过原因/说明不写库",
          "B2 作者倒装 dry-run 报告" in dry_text and "将倒装样例" in dry_text
          and "跳过原因分类" in dry_text and "dry-run 只算不写" in dry_text
          and "editor" in dry_text, dry_text[:500])
    # ── apply 门禁 ──
    r = run_cli("normalize-authors", "--db", db, "--apply")
    check("(s) --apply 不带 --backup → 退出 2，库不变",
          r.returncode == 2 and "backup" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_before)
    wrong = write(os.path.join(tmp, "wrong.jsonl"), '{"version":1}\n')
    r = run_cli("normalize-authors", "--db", db, "--apply", "--backup", wrong)
    check("(s) --backup sha 不符 → 退出 2", r.returncode == 2
          and "不一致" in (r.stdout + r.stderr))
    backup = os.path.join(tmp, "backup.jsonl")
    with open(backup, "wb") as fh:
        fh.write(read_bytes(db))
    r = run_cli("normalize-authors", "--db", db, "--apply", "--backup", backup,
                "--dry-run")
    check("(s) --apply 与 --dry-run 互斥 → 退出 2", r.returncode == 2
          and ("互斥" in (r.stdout + r.stderr)
               or "not allowed with argument" in (r.stdout + r.stderr)))
    # ── 真正 apply ──
    report = os.path.join(tmp, "B2-apply.md")
    r = run_cli("normalize-authors", "--db", db, "--apply", "--backup", backup,
                "--report", report, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    after = bibdb.Store.load(db)
    check("(s) apply：3 条倒装、6 条跳过、退出 0",
          r.returncode == 0 and payload.get("applied") == 3
          and payload.get("skipped") == 6 and payload.get("verification", {})
          .get("remaining") == 0, json.dumps(payload, ensure_ascii=False)[:400])
    check("(s) Given Surname → Surname, Given（含中名首字母）",
          after.get("Good1")["fields"]["author"] == "Smith, J."
          and after.get("Multi2")["fields"]["author"]
          == "Dillard, J. P. and Segrin, C."
          and after.get("Particle3")["fields"]["author"] == "van Fraassen, B. C.",
          f"{after.get('Good1')['fields']['author']} | "
          f"{after.get('Multi2')['fields']['author']} | "
          f"{after.get('Particle3')['fields']['author']}")
    check("(s) 跳过项 author 一字不变（CJK/机构/others/花括号/已倒装/单名）",
          all(after.get(key)["fields"].get("author") == before[key]["fields"]["author"]
              for key in ("Inverted4", "Chinese5", "Institution6", "Others7",
                          "Braces8", "Single9")))
    check("(s) apply 只改 author：其它字段/verified/sources/conflicts/aliases 不变",
          after.get("Good1")["fields"] == {**before["Good1"]["fields"],
                                           "author": "Smith, J."}
          and all(after.get("Good1")[k] == before["Good1"][k]
                  for k in ("verified", "verified_at", "method", "sources",
                            "conflicts", "aliases", "created_at")))
    check("(s) content_sha256 重算 + updated_at 更新",
          after.get("Good1")["content_sha256"]
          == bibdb.content_sha256(after.get("Good1")["entry_type"],
                                  after.get("Good1")["fields"])
          and after.get("Good1")["updated_at"]
          != before["Good1"]["updated_at"])
    check("(s) normalize-authors 不动 editor（editor 归 B3 normalize-editors 批处理；"
          "EditorOnly10 原样）",
          after.get("EditorOnly10")["fields"].get("author") is None
          and after.get("EditorOnly10")["fields"]["editor"] == "John Smith"
          and after.get("EditorOnly10")["updated_at"]
          == before["EditorOnly10"]["updated_at"])
    applied_text = _read_text(report) if os.path.isfile(report) else ""
    check("(s) apply 报告：sha 前/后、备份、已倒装样例、跳过原因、二次校验",
          "B2 作者倒装 apply 报告" in applied_text and "库 sha256（apply 前）" in applied_text
          and "备份" in applied_text and "已倒装样例" in applied_text
          and "跳过原因分类" in applied_text and "二次校验" in applied_text,
          applied_text[:500])
    # ── 幂等：再 apply 一次（用当前库做备份）→ applied=0、库字节不变 ──
    backup2 = os.path.join(tmp, "backup2.jsonl")
    with open(backup2, "wb") as fh:
        fh.write(read_bytes(db))
    digest_after = read_bytes(db)
    r = run_cli("normalize-authors", "--db", db, "--apply", "--backup", backup2, "--json")
    payload2 = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(s) 二次 apply 幂等：applied=0、库文件逐字节不变",
          r.returncode == 0 and payload2.get("applied") == 0
          and read_bytes(db) == digest_after, json.dumps(payload2, ensure_ascii=False)[:300])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (t) normalize-editors：B3 编者倒装 dry-run（只出报告、绝不写库）──")


def _make_editor_db(path: str) -> str:
    """合成库：editor 可倒装/多编者/虚词/(eds.)/大写 AND/editor-only + 各类跳过。"""
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text("""
@book{EdGood1,
  editor    = {John Smith},
  title     = {Editor One},
  publisher = {P},
  year      = {2001}
}
@book{EdMulti2,
  editor    = {J. P. Dillard and C. Segrin},
  title     = {Editor Two},
  publisher = {P},
  year      = {2002}
}
@book{EdParticle3,
  editor    = {B. C. van Fraassen},
  title     = {Editor Three},
  publisher = {P},
  year      = {2003}
}
@book{EdInverted4,
  editor    = {Smith, John},
  title     = {Editor Four},
  publisher = {P},
  year      = {2004}
}
@book{EdChinese5,
  editor    = {张三 and 李四},
  title     = {第五},
  publisher = {P},
  year      = {2005}
}
@book{EdInstitution6,
  editor    = {World Health Organization},
  title     = {Editor Six},
  publisher = {P},
  year      = {2006}
}
@book{EdOthers7,
  editor    = {J. Smith and others},
  title     = {Editor Seven},
  publisher = {P},
  year      = {2007}
}
@book{EdBraces8,
  editor    = {{van Fraassen}, B. C.},
  title     = {Editor Eight},
  publisher = {P},
  year      = {2008}
}
@book{EdSingle9,
  editor    = {Plato},
  title     = {Editor Nine},
  publisher = {P},
  year      = {2009}
}
@book{EdOnly10,
  editor    = {Gottlob Frege},
  title     = {Editor Ten},
  publisher = {P},
  year      = {2010}
}
@book{EdParen11,
  editor    = {H. Hermes and F. Kambartel and F. Kaulbach(eds.)},
  title     = {Editor Eleven},
  publisher = {P},
  year      = {2011}
}
@book{EdAnd12,
  editor    = {J.A. Carter AND E. Gordon AND B. Jarvis},
  title     = {Editor Twelve},
  publisher = {P},
  year      = {2012}
}
@book{EdAuthor13,
  author    = {John Smith},
  editor    = {Jane Doe},
  title     = {Editor Thirteen},
  publisher = {P},
  year      = {2013}
}
""", {"kind": "published-bib", "issue": "2025年第1期(80)",
      "slug": "01 Base", "file": "base.bib"}, verified=True)
    store.save()
    return path


with tempfile.TemporaryDirectory(prefix="bibdb_editors_") as tmp:
    db = _make_editor_db(os.path.join(tmp, "editors.jsonl"))
    _seed = bibdb.Store.load(db)
    for _rec in _seed.records():
        _seed.get(_rec["key"])["updated_at"] = "2000-01-01T00:00:00+08:00"
    _seed.dirty = True
    _seed.save()
    digest_before = read_bytes(db)
    out_dry = os.path.join(tmp, "dry")
    md_path = os.path.join(out_dry, "B3-editor-normalize-dryrun.md")
    csv_path = os.path.join(out_dry, "B3-editor-normalize-dryrun.csv")
    json_path = os.path.join(out_dry, "B3-editor-normalize-dryrun.json")
    r = run_cli("normalize-editors", "--db", db, "--out-dir", out_dry, "--json")
    payload = json.loads(r.stdout) if r.returncode == 0 else {}
    dry_text = _read_text(md_path) if os.path.isfile(md_path) else ""
    check("(t) dry-run：退出 0、MD/CSV/JSON 三件都生成、库文件逐字节不变",
          r.returncode == 0 and os.path.isfile(md_path) and os.path.isfile(csv_path)
          and os.path.isfile(json_path) and read_bytes(db) == digest_before,
          (r.stdout + r.stderr)[:300])
    check("(t) 统计：13 条有 editor（12 条 editor-only）→ 7 将倒装 / 6 跳过；"
          "跑前=跑后 sha、db_unchanged",
          payload.get("with_editor") == 13 and payload.get("editor_only") == 12
          and payload.get("changes") == 7 and payload.get("skips") == 6
          and payload.get("db_unchanged") is True
          and payload.get("sha256_before") == payload.get("sha256_after")
          and payload.get("mode") == "dry-run"
          and payload.get("apply_implemented") is True,
          json.dumps(payload, ensure_ascii=False)[:400])
    check("(t) 跳过原因分类覆盖 CJK/机构/and others/花括号/已倒装/单名",
          {"含中文/日文（不倒装）", "疑似机构/组织作者（不推断）",
           "含 `and others`（保持原样）", "含花括号/LaTeX 保护（保持原样）",
           "已倒装或含逗号（保持原样）", "单名/只有一个词（保持原样）"}
          <= set(payload.get("reasons") or {}), str(payload.get("reasons"))[:400])
    risk = payload.get("risk_flags") or {}
    check("(t) 风险标记：多编者/(eds.)/仅 editor/大写 AND 都标出",
          risk.get("(eds.) 形态") == 1 and risk.get("仅 editor") == 6
          and risk.get("大写 AND") == 1
          and any(key.startswith("多编者(") for key in risk), str(risk)[:400])
    json_text = _read_text(json_path) if os.path.isfile(json_path) else ""
    json_payload = json.loads(json_text) if json_text else {}
    change_map = {row["key"]: row for row in json_payload.get("change_entries") or []}
    check("(t) B2 漏扫的 editor-only 记录被扫到：EdOnly10 → Frege, G.（标记「仅 editor」）",
          change_map.get("EdOnly10", {}).get("new") == "Frege, G."
          and "仅 editor" in (change_map.get("EdOnly10", {}).get("flags") or []), 
          json.dumps(change_map.get("EdOnly10"), ensure_ascii=False)[:300])
    check("(t) 报告：标题/sha 跑前=跑后=未改动库/拍板 3 点/逐条明细/二次校验",
          "B3 编者（editor）倒装 dry-run 报告" in dry_text
          and payload.get("sha256_before") in dry_text
          and "未改动库" in dry_text and "需要用户拍板的 3 个点" in dry_text
          and "(eds.)" in dry_text and "大写分隔符" in dry_text
          and "逐条明细" in dry_text and "二次校验" in dry_text,
          dry_text[:400])
    check("(t) (eds.) 两条形态与「自动改 vs 列人工」两方案、大写 AND 保留单列",
          "EdParen11" in dry_text and "{Kaulbach (eds.)}, F." in dry_text
          and "方案 A" in dry_text and "方案 B" in dry_text
          and "EdAnd12" in dry_text
          and "Carter, J. A. AND Gordon, E. AND Jarvis, B." in dry_text)
    csv_text = _read_text(csv_path) if os.path.isfile(csv_path) else ""
    check("(t) CSV：UTF-8 BOM、7 行全量「将倒装」+ 表头 9 列",
          os.path.isfile(csv_path) and read_bytes(csv_path)[:3] == b"\xef\xbb\xbf"
          and len(csv_text.lstrip("\ufeff").splitlines()) == 7 + 1
          and csv_text.lstrip("\ufeff").splitlines()[0].count(",") == 8,
          (csv_text.splitlines()[0] if csv_text else "")[:120])
    check("(t) 报告/CSV/JSON 无绝对路径、无时间戳（可逐字节复现）",
          tmp not in dry_text and tmp not in csv_text and tmp not in json_text
          and datetime.date.today().isoformat() not in dry_text)
    check("(t) 报告逐条明细覆盖全部 13 条 editor 记录",
          sum(1 for line in dry_text.splitlines() if line.startswith("| 00")
              or (line.startswith("| 0") and "`Ed" in line)) >= 13,
          str(sum(1 for line in dry_text.splitlines() if "| `Ed" in line)))
    # ── 幂等：同库连跑两次，MD/CSV/JSON 逐字节相同、库仍不变 ──
    out_dry2 = os.path.join(tmp, "dry2")
    r2 = run_cli("normalize-editors", "--db", db, "--out-dir", out_dry2, "--json")
    same = all(read_bytes(os.path.join(out_dry, name))
               == read_bytes(os.path.join(out_dry2, name))
               for name in ("B3-editor-normalize-dryrun.md",
                            "B3-editor-normalize-dryrun.csv",
                            "B3-editor-normalize-dryrun.json"))
    check("(t) 二次 dry-run 幂等：三件输出逐字节相同、库文件逐字节不变",
          r2.returncode == 0 and same and read_bytes(db) == digest_before,
          (r2.stdout + r2.stderr)[:300])
    # ── 无 --out-dir 只打印摘要（不写任何文件；apply 前跑）──
    before_files = set(os.listdir(tmp))
    r = run_cli("normalize-editors", "--db", db)
    check("(t) 不给 --out-dir：退出 0、只打印摘要、不落任何报告文件",
          r.returncode == 0 and "将倒装 7 条 / 跳过 6 条" in r.stdout
          and set(os.listdir(tmp)) == before_files, r.stdout[:300])
    # ── apply 门禁（第四十三批 B3 用户拍板后实现 --apply）──
    r = run_cli("normalize-editors", "--db", db, "--apply")
    check("(t) --apply 不带 --backup → 退出 2、库不变",
          r.returncode == 2 and "backup" in (r.stdout + r.stderr)
          and read_bytes(db) == digest_before, (r.stdout + r.stderr)[:300])
    wrong = write(os.path.join(tmp, "wrong.jsonl"), '{"version":1}\n')
    r = run_cli("normalize-editors", "--db", db, "--apply", "--backup", wrong)
    check("(t) --backup sha 不符 → 退出 2", r.returncode == 2
          and "不一致" in (r.stdout + r.stderr))
    backup = os.path.join(tmp, "backup.jsonl")
    with open(backup, "wb") as fh:
        fh.write(read_bytes(db))
    r = run_cli("normalize-editors", "--db", db, "--apply", "--backup", backup,
                "--dry-run")
    check("(t) --apply 与 --dry-run 互斥 → 退出 2", r.returncode == 2
          and ("互斥" in (r.stdout + r.stderr)
               or "not allowed with argument" in (r.stdout + r.stderr)))
    # ── 真正 apply ──
    _before_apply = {k: json.loads(json.dumps(bibdb.Store.load(db).get(k)))
                     for k in ("EdGood1", "EdMulti2", "EdOnly10", "EdParen11",
                               "EdAnd12", "EdInverted4")}
    report_apply = os.path.join(tmp, "B3-apply.md")
    r = run_cli("normalize-editors", "--db", db, "--apply", "--backup", backup,
                "--report", report_apply, "--json")
    ap = json.loads(r.stdout) if r.returncode == 0 else {}
    after_ed = bibdb.Store.load(db)
    check("(t) apply：7 条倒装、6 条跳过、二次校验 0 残留、退出 0",
          r.returncode == 0 and ap.get("applied") == 7 and ap.get("skipped") == 6
          and ap.get("verification", {}).get("remaining") == 0
          and ap.get("db_unchanged") is False,
          json.dumps(ap, ensure_ascii=False)[:400])
    check("(t) apply 结果：Given Surname / 多作者整组 / editor-only 都倒装",
          after_ed.get("EdGood1")["fields"]["editor"] == "Smith, J."
          and after_ed.get("EdMulti2")["fields"]["editor"]
          == "Dillard, J. P. and Segrin, C."
          and after_ed.get("EdOnly10")["fields"]["editor"] == "Frege, G.")
    check("(t) (eds.) 2 条按 B2 同规则自动改（花括号保护形态）",
          "{Kaulbach (eds.)}, F." in after_ed.get("EdParen11")["fields"]["editor"],
          after_ed.get("EdParen11")["fields"]["editor"])
    check("(t) 大写 AND 分隔符保留（只倒装姓名）",
          after_ed.get("EdAnd12")["fields"]["editor"]
          == "Carter, J. A. AND Gordon, E. AND Jarvis, B.",
          after_ed.get("EdAnd12")["fields"]["editor"])
    check("(t) 跳过项 editor 一字不变（已倒装/逗号）",
          after_ed.get("EdInverted4")["fields"]["editor"]
          == _before_apply["EdInverted4"]["fields"]["editor"])
    check("(t) apply 只改 editor：其它字段/元数据不变、content_sha 重算、updated_at 更新",
          after_ed.get("EdGood1")["fields"]
          == {**_before_apply["EdGood1"]["fields"], "editor": "Smith, J."}
          and all(after_ed.get("EdGood1")[k] == _before_apply["EdGood1"][k]
                  for k in ("verified", "verified_at", "method", "sources",
                            "conflicts", "aliases", "created_at"))
          and after_ed.get("EdGood1")["content_sha256"]
          == bibdb.content_sha256(after_ed.get("EdGood1")["entry_type"],
                                  after_ed.get("EdGood1")["fields"])
          and after_ed.get("EdGood1")["updated_at"]
          != _before_apply["EdGood1"]["updated_at"])
    applied_md = _read_text(report_apply) if os.path.isfile(report_apply) else ""
    check("(t) apply 报告：apply 前/后 sha、备份、已倒装明细、二次校验、回滚命令",
          "B3 编者（editor）倒装 apply 报告" in applied_md
          and "库 sha256（apply 前）" in applied_md and "备份" in applied_md
          and "已倒装样例" in applied_md and "二次校验" in applied_md
          and "回滚方法" in applied_md, applied_md[:400])
    # 二次 dry-run 归零 + 二次 apply 0 变更
    r = run_cli("normalize-editors", "--db", db, "--json")
    p2 = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(t) apply 后二次 dry-run：将倒装 0 条、库未变",
          r.returncode == 0 and p2.get("changes") == 0
          and p2.get("db_unchanged") is True, json.dumps(p2, ensure_ascii=False)[:300])
    backup2 = os.path.join(tmp, "backup2.jsonl")
    with open(backup2, "wb") as fh:
        fh.write(read_bytes(db))
    digest_after_apply = read_bytes(db)
    r = run_cli("normalize-editors", "--db", db, "--apply", "--backup", backup2,
                "--json")
    p3 = json.loads(r.stdout) if r.returncode == 0 else {}
    check("(t) 二次 apply 幂等：applied=0、库文件逐字节不变",
          r.returncode == 0 and p3.get("applied") == 0
          and read_bytes(db) == digest_after_apply,
          json.dumps(p3, ensure_ascii=False)[:300])
    # ── 真库只读抽查（SKIP when absent）：数字与 Phase0 审计一致 ──
    try:
        real_db = bibdb.default_db_path()
    except Exception:                     # 笔记仓不存在（CI/公开仓）→ 正常跳过
        real_db = ""
    if not real_db or not os.path.isfile(real_db):
        skip("(t) 真实引用库只读抽查", "库文件不存在（CI 上正常）")
    else:
        real_sha = bibdb.db_sha256(real_db)
        r = run_cli("normalize-editors", "--db", real_db, "--json")
        real = json.loads(r.stdout) if r.returncode == 0 else {}
        # 兼容多种真实库状态：B3 apply 前 / B3 后 / 合并 apply 后（第四十三批起；之后每次
        # 用户合并在本集合加一行当前状态即可 —— 断言仍是「dry-run 零变化 + 统计自洽」）
        _states = {
            (7950, 971, 29, 498, 473),      # B3 apply 前
            (7950, 971, 29, 0, 971),        # B3 apply 后
            (7067, 908, 24, 0, 908),        # 第一批合并 apply 后（7950-883）
            (7051, 905, 24, 0, 905),        # 第二批合并 apply 后（7067-16，含 Segerberg 书版子集）
            (7045, 904, 24, 0, 904),        # 用户 2026-10-23 引用库合并提交后（7051-6；B-201 批同步）
            (7043, 904, 24, 0, 904),        # 用户用 B-201 新功能再合并 2 对后（7045-2；B-205 批同步）
        }
        _state = (real.get("records"), real.get("with_editor"), real.get("editor_only"),
                  real.get("changes"), real.get("skips"))
        check("(t) 真实库只读：dry-run 不写库、统计自洽（兼容 B3/合并 apply 前后状态）",
              r.returncode == 0 and _state in _states
              and real.get("db_unchanged") is True
              and bibdb.db_sha256(real_db) == real_sha,
              json.dumps({k: real.get(k) for k in
                          ("records", "with_editor", "editor_only", "changes",
                           "skips", "db_unchanged")}, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────
print("\n── (l) 真实稿件库抽查（SKIP when absent，只读） ──")

root = paths.manuscripts_root()
if root is None:
    skip("(l) 真实稿件库只读扫描", paths.describe())
else:
    scans = bibdb.scan_manuscripts(root)
    files = sum(len(scan.files) for scan in scans)
    check(f"(l) 真实稿件库扫描：{len(scans)} 期 / {files} 份成品 bib",
          len(scans) >= 20 and files >= 100,
          f"{len(scans)} 期 / {files} 份")
    check("(l) 每份成品 bib 都有终稿 tex 依据（how 非空）",
          all(bf.how for scan in scans for bf in scan.files))
    bad_how = [bf for scan in scans for bf in scan.files if bf.how not in (
        "tex-ref", "tex-ref-basename", "tex-ref-case",
        "fallback-author", "fallback-only", "fallback-reference")]
    check("(l) 选 bib 的判据都在白名单里", not bad_how, str([b.rel for b in bad_how[:3]]))


# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if _old_notes is None:
    os.environ.pop("SLCN_NOTES", None)
else:
    os.environ["SLCN_NOTES"] = _old_notes
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过" + (f"（SKIP {len(skips)} 组）" if skips else ""))
print("失败 0 项（全部通过）")
sys.exit(0)
