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

不联网、不依赖稿件库（除 (l) 的 SKIP 组）。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_bibdb.py
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
