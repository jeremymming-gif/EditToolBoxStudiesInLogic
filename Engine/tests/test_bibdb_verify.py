#!/usr/bin/env python3.14
r"""编辑部引用库接入核实链路（第 129 条，2026-10-18）的判据测试。

目标：**命中已核实条目 → 该键不调 Search API、不调 AI**（省钱 + 体例统一 + 可审计），
但判定必须保守（把不同文献误判成「已核实」比漏查更糟）：

  · 安全命中 = DOI 精确命中**已核实**记录；或题名高置信（bibdb 的 0.90 阈值）+
    第一作者姓 / 年份相容 + `verified=true`；
  · **key 命中只算候选**（BibTeX key 多为 per-paper），仍走检索 + AI；
  · 安全命中只跳过「检索/AI」，**绝不改 `reference.bib`**；字段差异只写进任务书提示；
  · 默认（不核实）路径：库值只写入 reference.bib（+既有核对清单/备份），.tex 与关库版
    逐字节一致（B-135 起白名单全量覆盖）；`--no-bibdb` 与接入前行为逐字节一致；
  · 库缺失/笔记仓缺失 → 优雅跳过（显式 `--bibdb` 才告警）。

断言分组：
  (a) 判定（`slcn/bibdb.py: Store.match_reference`）：安全 DOI / 安全题名 / 未核实不算安全 /
      key-only 候选 / 未命中 / 字段差异（语义等价不报）
  (b) 任务书「五、库内已核实（无需再查）」：默认不出现、给了才出现、含来源与差异提示
  (c) 合并记录：库内命中（未调 AI）与正文未引用（范围外）分开统计；默认参数逐字节不变
  (d) convert 端到端（合成 LaTeX + --bibdb-path，**不联网**）：跑前日志一行 / --verify-plan
      按扣减后算预计检索与 AI / 任务书与 reference.bib 行为；--no-bibdb 与库缺失；
      默认路径库值应用见 d6
  (e) 全部命中库内已核实 + mock LLM：**0 次 AI 调用**、任务书五节、合并记录分类、bib 未改
  (f) 部分命中 + mock LLM：安全命中键不请求、key-only 候选仍请求并正常并入；安全条目逐字节未动
  (g) 默认（不核实）路径与 `--no-bibdb` 的产物逐字节一致性
  (h) 真实稿只读抽查：`~/Desktop/macCaseTest87/1/MHYC.tex`（SKIP when absent）

不联网（LLM 用本地假端点；检索只走 `--verify-plan` 的账目，不发请求）、不依赖稿件库。
用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_bibdb_verify.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib, bibdb, bibnorm, bibparse, refmark  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []


def check(name: str, cond, detail="") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"SKIP  {name}  {why}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def sha(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def files_snapshot(root: str) -> dict[str, str]:
    """目录下所有文件的（相对路径 → sha256）快照：判「产物逐字节不变」。"""
    out: dict[str, str] = {}
    for current, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(current, name)
            out[os.path.relpath(path, root)] = sha(path)
    return out


PUBLISHED = {"kind": bibdb.KIND_PUBLISHED, "issue": "2025年第6期(85)",
             "slug": "01 Test_Author", "file": "reference.bib"}

LIB_BIB = """@article{Quine1970,
  author  = {Quine, Willard Van Orman},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}

@book{Other2020,
  author    = {Other, Olivia},
  title     = {An Unrelated Work on Logic},
  publisher = {Some Press},
  year      = {2020}
}

@article{Unverified2021,
  author  = {Draft, Dana},
  title   = {An Unverified Draft Entry},
  journal = {Journal of Drafts},
  year    = {2021},
  doi     = {10.1000/UNVERIFIED.2021}
}

@article{KeyHitOnly,
  author = {Doe, Jane},
  title  = {A Library Title That Differs Greatly From The Manuscript},
  year   = {1999}
}
"""


def build_library(tmp: str, name: str = "bib_db.jsonl") -> str:
    """合成引用库：3 条已核实（Quine1970 / Other2020 / KeyHitOnly）+ 1 条未核实。"""
    path = os.path.join(tmp, name)
    store = bibdb.Store.load(path, create=True)
    verified = """@article{Quine1970,
  author  = {Quine, Willard Van Orman},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}

@book{Other2020,
  author    = {Other, Olivia},
  title     = {An Unrelated Work on Logic},
  publisher = {Some Press},
  year      = {2020}
}

@article{KeyHitOnly,
  author = {Doe, Jane},
  title  = {A Library Title That Differs Greatly From The Manuscript},
  year   = {1999}
}
"""
    store.import_bib_text(verified, PUBLISHED, verified=True)
    store.import_bib_text("""@article{Unverified2021,
  author  = {Draft, Dana},
  title   = {An Unverified Draft Entry},
  journal = {Journal of Drafts},
  year    = {2021},
  doi     = {10.1000/UNVERIFIED.2021}
}
""", {"kind": bibdb.KIND_IMPORTED, "file": "draft.bib"}, verified=False)
    store.save()
    return path


# ══ (a) 判定：match_reference（库层唯一实现）════════════════════════════════
print("── (a) match_reference：安全命中 / 候选 / 未命中 ──")
_TMP_A = tempfile.mkdtemp(prefix="bibdb_verify a ")
_LIB = build_library(_TMP_A)
_store = bibdb.Store.load(_LIB)

m = _store.match_reference(
    {"doi": "https://doi.org/10.1000/quine.1970", "title": "Philosophy of Logic",
     "author": "Quine, W. V. O.", "year": "1970"}, key="Quine1970")
check("(a) DOI 精确命中已核实记录 → safe（hit=doi）",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "doi"
      and m["record_key"] == "Quine1970" and m["record"]["verified"] is True,
      f"{m['status']}/{m['hit']}/{m['record_key']}")

m = _store.match_reference(
    {"title": "Philosophy of Logic", "author": "Quine, W. V. O.", "year": "1970"},
    key="Whatever")
check("(a) 题名高置信 + 第一作者姓/年份相容 + 已核实 → safe（hit=title）",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title"
      and m["title_similarity"] >= 0.99, f"{m['status']}/{m['hit']}/{m['title_similarity']}")

m = _store.match_reference(
    {"doi": "10.1000/unverified.2021", "title": "An Unverified Draft Entry",
     "author": "Draft, Dana", "year": "2021"}, key="Unverified2021")
check("(a) DOI 命中但库内**未核实** → 只是 candidate（不许跳过检索/AI）",
      m["status"] == bibdb.HIT_CANDIDATE and m["hit"] == "doi"
      and "未核实" in m["reason"], f"{m['status']}/{m['reason']}")

m = _store.match_reference(
    {"title": "Philosophy of Logic", "author": "Quine, W. V. O.", "year": "1998"},
    key="X1998")
check("(a) 题名高置信但**年份不符** → 不是 safe（误判比漏查更糟）",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['reason']}")

m = _store.match_reference(
    {"title": "Philosophy of Logic", "author": "Someone Else", "year": "1970"},
    key="X")
check("(a) 题名高置信但**第一作者不符** → 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['reason']}")

m = _store.match_reference(
    {"title": "A Totally Different Title", "author": "Nobody, N.", "year": "2001"},
    key="KeyHitOnly")
check("(a) key 命中但内容不同 → 只是 candidate（key 多为 per-paper，仍走检索）",
      m["status"] == bibdb.HIT_CANDIDATE and m["hit"] == "key"
      and "per-paper" in m["reason"], f"{m['status']}/{m['hit']}/{m['reason']}")

m = _store.match_reference({"title": "Zzzz Not A Real Work At All",
                            "author": "Nobody, N.", "year": "2001"}, key="Nope2001")
check("(a) 库内无任何线索 → miss", m["status"] == bibdb.HIT_MISS
      and m["record"] is None and m["reason"])

m = _store.match_reference(
    {"title": "Philosophy of Logic", "author": "Quine, W. V. O.", "year": "1970",
     "pages": "1-20"}, key="Q")
diff = m["field_diff"]
check("(a) 字段差异只提示：缺 volume/pages 会列出来（库值 vs 稿件值）",
      "volume" in diff and diff["volume"]["library"] == "79"
      and diff["volume"]["manuscript"] == "", diff)
check("(a) 语义等价的写法差异**不算**差异（pages `1-20` vs `1--20`）",
      "pages" not in diff, diff)
m2 = _store.match_reference(
    {"title": "Philosophy of Logic", "author": "Quine, W. V. O.", "year": "1970",
     "volume": "80"}, key="Q")
check("(a) 真正的字段差异照样列出（volume 79 vs 80）",
      m2["field_diff"].get("volume") == {"library": "79", "manuscript": "80"},
      m2["field_diff"].get("volume"))


# ══ (b) 任务书「五、库内已核实（无需再查）」════════════════════════════════
print("\n── (b) 任务书新增「五、库内已核实（无需再查）」一级 ──")
TASK_BIB = """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}
"""
md_plain = refmark.build(TASK_BIB, "Stem")
check("(b) 不传 bibdb_hits：任务书与旧签名逐字节相同、没有这一节",
      md_plain == refmark.build(TASK_BIB, "Stem", bibdb_hits=None)
      and "库内已核实" not in md_plain)
hits = [_store.match_reference({"doi": "10.1000/quine.1970",
                                "title": "Philosophy of Logic",
                                "author": "Quine, W. V. O.", "year": "1970",
                                "pages": "1-20"}, key="Quine1970")]
md_hits = refmark.build(TASK_BIB, "Stem", bibdb_hits=hits)
check("(b) 传 bibdb_hits：出现「五、库内已核实（无需再查）」一节",
      "## 五、库内已核实（无需再查）" in md_hits)
check("(b) 该节写明「未调用检索/AI」「reference.bib 未改动」「差异待编辑裁决」",
      "本次**未调用检索/AI**" in md_hits and "reference.bib" in md_hits
      and "由编辑裁决" in md_hits and "不改稿" in md_hits)
check("(b) 该条列出 key + 库内来源 + 差异提示",
      "`Quine1970`" in md_hits and "库内来源：2025年第6期(85)/01 Test_Author/reference.bib" in md_hits
      and "库版本字段差异（待编辑裁决）" in md_hits, md_hits[-700:])
check("(b) 新节只加在末尾（原任务书内容一个字节都没动）",
      md_hits.startswith(md_plain)
      and md_hits.index("## 五、库内已核实")
      > md_hits.index("## 四、逐条清单"))


# ══ (c) 合并记录：库内命中与范围外分开统计 ═════════════════════════════════
print("\n── (c) 合并记录：库内命中（未调 AI）与正文未引用（范围外）分开统计 ──")
MERGE_BIB = """@article{A2000,
  author = {A, One},
  title  = {Entry A},
  year   = {2000}
}
@article{B2000,
  author = {B, Two},
  title  = {Entry B},
  year   = {2000}
}
@article{C2000,
  author = {C, Three},
  title  = {Entry C},
  year   = {2000}
}
"""
_reply_a = "@article{A2000,\n  title = {Entry A Revised},\n}"
_result = aibib.merge(MERGE_BIB, {"A2000": _reply_a})
plain = aibib.render_merge_section(_result)
check("(c) 不传新参数：合并记录与旧调用逐字节相同（空集合等价）",
      plain == aibib.render_merge_section(_result, out_of_scope=set())
      == aibib.render_merge_section(_result, out_of_scope=set(), bibdb_hits=set()))
mixed = aibib.render_merge_section(_result, out_of_scope={"C2000"},
                                   bibdb_hits={"B2000"})
check("(c) 「本次（交给 AI）」按真正交给 AI 的键重算（只剩 A2000）",
      "本次（交给 AI）：**并入 1 条** / 跳过 0 条（逐条见下；共 1 个键）" in mixed, mixed[:700])
check("(c) 多一行「库内命中：另有 N 条……未调用 AI」",
      "库内命中：另有 **1 条**" in mixed and "未调用 AI" in mixed)
check("(c) 多一行「范围外：另有 N 条正文未引用……没有交给 AI」",
      "范围外：另有 **1 条**" in mixed and "没有**交给 AI" in mixed)
check("(c) 库内命中/范围外条目**不再**被写成「AI 未返回」",
      mixed.count("AI 未返回这一条") == 0
      and "`B2000`" not in mixed.split("### 已跳过")[1]
      and "`C2000`" not in mixed.split("### 已跳过")[1], mixed[-500:])
check("(c) 已并入清单照旧只有 A2000", "### 已并入（1 条）" in mixed
      and "`A2000`" in mixed.split("### 已并入")[1].split("###")[0])


# ══ 公共夹具：合成 LaTeX 稿 + 子进程跑 convert ════════════════════════════
print("\n── (d) convert 端到端（合成 LaTeX，不联网）──")
TEMPLATES = os.path.join(_REPO, "编辑笔记", "模板文件")
HAVE_TEMPLATES = os.path.isdir(TEMPLATES)
if not HAVE_TEMPLATES:
    skip("(d)-(g) convert 端到端", "模板目录不在本机（编辑笔记/模板文件）")

TMP = tempfile.mkdtemp(prefix="bibdb verify e2e ")
_SRC = os.path.join(TMP, "src")
os.makedirs(_SRC, exist_ok=True)
_DB = build_library(TMP, "e2e_db.jsonl")
_TEX = write(os.path.join(_SRC, "main.tex"), r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}，并引用一条库里没有的\parencite{New2024}。

\printbibliography
\end{document}
""")
_E2E_BIB = write(os.path.join(TMP, "refs.bib"), """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}

@article{New2024,
  author  = {Fresh, Fiona},
  title   = {A Brand New Paper Not In The Library},
  journal = {New Journal},
  year    = {2024}
}
""")
ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
for _key in list(ENV):
    if _key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
        ENV.pop(_key, None)          # 端到端测试不继承本机 LLM/检索/笔记仓配置


def run_convert(out_dir: str, extra: list[str], *, env: dict | None = None,
                source: str = _TEX) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "convert.py", "--source", source, "--out", out_dir,
         "--bib", _E2E_BIB, "--name", "Test_Author", "--author", "张三",
         "--lang", "zh", "--no-original"] + extra,
        cwd=_ENGINE, env=env or ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def products(out_dir: str) -> tuple[str, str]:
    folder = os.path.join(out_dir, "Test_Author")
    books = glob.glob(os.path.join(folder, "*_文献核实.md"))
    return folder, (books[0] if books else "")


if HAVE_TEMPLATES:
    # (d1) --verify-plan：查库 + 预计次数按扣减后算（不生成任务书、不检索、不调 AI）
    out_plan = os.path.join(TMP, "plan")
    r = run_convert(out_plan, ["--verify-refs", "--verify-plan", "--search-refs",
                               "--bibdb-path", _DB,
                               # cfg.ready=True 让「预计 AI」也有数字；plan 不会真调用
                               "--llm-base", "http://127.0.0.1:9", "--llm-key", "k",
                               "--llm-model", "mock-model"])
    _d, tb_plan = products(out_plan)
    check("(d) --verify-plan 退出 0、不生成任务书", r.returncode == 0 and not tb_plan,
          (r.stdout + r.stderr)[-300:])
    check("(d) 跑前日志含「引用库：命中 X 条已核实（跳过检索+AI）、候选 Y、未命中 Z（库 n 条，path）」",
          re.search(r"引用库：命中 1 条已核实（跳过检索\+AI）、候选 \d+、未命中 \d+"
                    r"（库 4 条，.*e2e_db\.jsonl）", r.stdout) is not None, r.stdout[-700:])
    check("(d) --verify-plan 的预计检索按扣减后算（1 条非安全命中 × 2 次 = 2 次）",
          "预计检索：≤ 2 次查询" in r.stdout, r.stdout[-500:])
    check("(d) --verify-plan 的预计 AI 按扣减后算（≤ 1 次）",
          "引用库已核实：1 条（不检索、不调 AI）" in r.stdout
          and "预计 AI：≤ 1 次调用" in r.stdout, r.stdout[-500:])

    # (d2) --verify-refs（不配 LLM）：任务书五节 + bib 逐字节不变 + 不联网
    before_bib = sha(_E2E_BIB)
    out_refs = os.path.join(TMP, "refs")
    r2 = run_convert(out_refs, ["--verify-refs", "--bibdb-path", _DB])
    folder2, tb2 = products(out_refs)
    md2 = read(tb2) if tb2 else ""
    check("(d) --verify-refs 退出 0、日志有引用库行",
          r2.returncode == 0 and "引用库：命中 1 条已核实" in r2.stdout,
          (r2.stdout + r2.stderr)[-400:])
    check("(d) 任务书写入「五、库内已核实（无需再查）」并列出 key 与来源",
          "## 五、库内已核实（无需再查）" in md2 and "`Quine1970`" in md2
          and "2025年第6期(85)/01 Test_Author/reference.bib" in md2, md2[-600:])
    check("(d) 安全命中的条目**没有**调 AI（未配 LLM，也没有 AI 建议段）",
          "AI 建议（**须人工核实**" not in md2)
    check("(d) reference.bib **逐字节未改**", sha(_E2E_BIB) == before_bib)

    # (d3) --no-bibdb：与「库缺失自动跳过」逐字节一致（= 接入前行为）
    env_off = dict(ENV, SLCN_NOTES=os.path.join(TMP, "no_notes_here"))
    os.makedirs(os.path.join(TMP, "no_notes_here"), exist_ok=True)
    out_nodb = os.path.join(TMP, "nodb")
    r3 = run_convert(out_nodb, ["--verify-refs"], env=env_off)
    out_flag = os.path.join(TMP, "flag")
    r4 = run_convert(out_flag, ["--verify-refs", "--no-bibdb"])
    _d3, tb3 = products(out_nodb)
    _d4, tb4 = products(out_flag)
    check("(d) 库缺失（自动启用失败）→ 优雅跳过：退出 0、日志没有引用库行",
          r3.returncode == 0 and "引用库：" not in r3.stdout, r3.stdout[-300:])
    check("(d) `--no-bibdb` 与「库缺失自动跳过」的任务书逐字节相同（= 接入前行为）",
          bool(tb3) and bool(tb4) and read(tb3) == read(tb4))
    check("(d) `--no-bibdb` 的任务书里没有「五、库内已核实」",
          "库内已核实" not in read(tb4))
    check("(d) `--no-bibdb` 的 stdout 没有引用库行",
          "引用库：" not in r4.stdout)

    # (d4) 显式 --bibdb-path 指错 → 告警但继续（不中断出稿）
    r5 = run_convert(os.path.join(TMP, "bad"), ["--verify-refs",
                                                "--bibdb-path", os.path.join(TMP, "nope.jsonl")])
    out5 = r5.stdout + r5.stderr
    check("(d) 显式指库但文件不存在 → 告警照实说、退出 0、照常出任务书",
          r5.returncode == 0 and "引用库文件不存在" in out5
          and "不查库" in out5 and bool(products(os.path.join(TMP, "bad"))[1]),
          out5[-400:])

    # (d5) 开关互斥 / 不配合核实时忽略
    r6 = run_convert(os.path.join(TMP, "conflict"), ["--verify-refs", "--bibdb", "--no-bibdb"])
    check("(d) --bibdb 与 --no-bibdb 同时给 → 用法错误退出 2",
          r6.returncode == 2 and "不能同时给" in (r6.stdout + r6.stderr), (r6.stdout + r6.stderr)[-200:])
    r7 = run_convert(os.path.join(TMP, "ignored"), ["--bibdb-path", _DB])
    check("(d) 没有 --verify-refs 时 `--bibdb-path` 只提示忽略（退出 0）",
          r7.returncode == 0 and "只配合 --verify-refs" in (r7.stdout + r7.stderr),
          (r7.stdout + r7.stderr)[-300:])

    # (d6) 默认（不核实）路径：库值只写入 reference.bib（+既有核对清单/备份）；
    #     .tex 与关库版逐字节同、条目 key 集合不变（B-135 spec §3/§5/§7：默认路径按白名单
    #     全量应用库值；B-222 B 起默认即回填，旧 8 字段在 fixture 里无差异故旧断言曾为绿）。
    out_default_a = os.path.join(TMP, "default_db")
    out_default_b = os.path.join(TMP, "default_nodb")
    rd_a = run_convert(out_default_a, ["--bibdb-path", _DB])
    rd_b = run_convert(out_default_b, [], env=env_off)
    snap_a, snap_b = files_snapshot(out_default_a), files_snapshot(out_default_b)
    tex_a = {k: v for k, v in snap_a.items() if k.endswith((".tex", ".sty"))}
    tex_b = {k: v for k, v in snap_b.items() if k.endswith((".tex", ".sty"))}
    allowed = {os.path.join("Test_Author", "reference.bib"),
               os.path.join("Test_Author", "Test_Author_参考文献核对.txt")}
    # 「快照值差异集」（含内容差异/单侧存在）：两侧相同的 .tex/.sty 不算 extra，
    # 只允许 reference.bib、核对清单与既有 .bak 备份不同。
    extra = {k for k in set(snap_a) | set(snap_b)
             if snap_a.get(k) != snap_b.get(k) and k not in allowed
             and not (k.startswith(os.path.join("Test_Author", "reference.bib."))
                      and k.endswith(".bak"))}
    bib_a = read(os.path.join(out_default_a, "Test_Author", "reference.bib"))
    bib_b = read(os.path.join(out_default_b, "Test_Author", "reference.bib"))
    check("(d) 默认（不核实）路径：库值只写 reference.bib（author 取库值）、.tex 与关库版逐字节同",
          rd_a.returncode == 0 and rd_b.returncode == 0
          and tex_a == tex_b and extra == set()
          and "Quine, Willard Van Orman" in bib_a and "Quine, W. V. O." in bib_b,
          f"tex_same={tex_a == tex_b} extra={extra}")


# ══ 本地假 LLM 端点（只数请求，回复一条合法 bib）══════════════════════════
class MockLLM:
    """本地 chat 端点：记录请求体；回复「把该条 title 改成 AI Revised Title」。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                # ⚠️ 只从**最后一条用户消息**里取请求的条目 key：system 规范里也举了
                #    `@article{…}` 的例子（第一版从整份 JSON 里搜，永远回错 key）。
                messages = body.get("messages") or []
                user = messages[-1].get("content") if messages else ""
                match = re.search(r"@\w+\{([^,\s]+),", str(user))
                key = match.group(1) if match else "K2020"
                reply = (f"@article{{{key},\n  author = {{Mock, Author}},\n"
                         "  title = {AI Revised Title},\n  year = {2020}\n}")
                payload = {"choices": [{"message": {"content": reply}}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def ai_requests(self) -> list[dict]:
        return [body for body in self.requests
                if "messages" in body or "input" in body]

    def close(self) -> None:
        self.srv.shutdown()


def entry_block(bib_text: str, key: str) -> str:
    """取 bib 文本里某一条的原文切片（判「这条逐字节未动」）。"""
    match = re.search(r"@\w+\{" + re.escape(key) + r",.*?\n\}", bib_text, re.S)
    return match.group(0) if match else ""


if HAVE_TEMPLATES:
    # ══ (e) 全部命中库内已核实：0 次 AI 调用 + 合并记录分类 + bib 未改 ══════
    print("\n── (e) 全部命中库内已核实 + mock LLM：0 次 AI 调用 ──")
    TEX_SAFE = write(os.path.join(TMP, "safe", "main.tex"), r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
只引用库里已核实的那条\parencite{Quine1970}。

\printbibliography
\end{document}
""")
    BIB_SAFE = write(os.path.join(TMP, "safe", "refs.bib"), """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}
""")
    mock = MockLLM()
    try:
        out_all = os.path.join(TMP, "all_safe")
        r = subprocess.run(
            [sys.executable, "convert.py", "--source", TEX_SAFE, "--out", out_all,
             "--bib", BIB_SAFE, "--name", "Test_Author", "--author", "张三",
             "--lang", "zh", "--no-original", "--verify-refs", "--search-refs",
             "--bibdb-path", _DB,
             "--llm-base", mock.base, "--llm-key", "k-test", "--llm-model", "mock-chat"],
            cwd=_ENGINE, env=ENV, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240)
        folder, tb = products(out_all)
        md = read(tb) if tb else ""
        check("(e) 全部已核实：**0 次 AI 调用**（也不发检索请求）",
              r.returncode == 0 and len(mock.ai_requests()) == 0,
              f"rc={r.returncode} n={len(mock.ai_requests())} {(r.stdout + r.stderr)[-300:]}")
        check("(e) 控制台明说「全部命中已核实 → 不调用检索/AI」",
              "全部命中已核实记录" in r.stdout and "不调用检索/AI" in r.stdout,
              r.stdout[-500:])
        check("(e) 任务书有五节、且合并记录写明「库内命中：另有 1 条……未调用 AI」",
              "## 五、库内已核实（无需再查）" in md and "## 六、合并记录" in md
              and "库内命中：另有 **1 条**" in md and "未调用 AI" in md, md[-900:])
        check("(e) 合并记录把「交给 AI」计为 0 个键（没把库内命中算成 AI 未返回）",
              "本次（交给 AI）：**并入 0 条** / 跳过 0 条（逐条见下；共 0 个键）" in md
              and "AI 未返回这一条" not in md, md[-900:])
        check("(e) reference.bib **逐字节未改**", sha(BIB_SAFE) ==
              hashlib.sha256(("""@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}
""").encode()).hexdigest())
        check("(e) reference.bib 没有 `.ai前` 回滚副本（0 条并入不落盘）",
              not os.path.exists(BIB_SAFE + ".ai前"))
    finally:
        mock.close()

    # ══ (f) 部分命中：安全键不请求、key-only 候选仍请求并正常并入 ═════════
    print("\n── (f) 部分命中 + mock LLM：安全键 0 请求，候选照常并入 ──")
    TEX_MIX = write(os.path.join(TMP, "mix", "main.tex"), r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
已核实的\parencite{Quine1970}，以及库里 key 撞车但内容不同的\parencite{Other2020}。

\printbibliography
\end{document}
""")
    BIB_MIX = write(os.path.join(TMP, "mix", "refs.bib"), """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}

@book{Other2020,
  author    = {Other, Olivia},
  title     = {A Manuscript Version With A Different Title},
  publisher = {Some Press},
  year      = {2020}
}
""")
    quine_before = entry_block(read(BIB_MIX), "Quine1970")
    other_before = entry_block(read(BIB_MIX), "Other2020")
    # B-189：产物写出会按渲染规范删掉**正式发表条目**的 doi/url（Quine1970 = article +
    # journal；模板本身从来不印 doi）。断言「AI 没碰它」要比较同口径的摘要：把 before
    # 也过一遍同一清洗函数，否则这条断言会把 B-189 的预期变化误报成回归。
    _quine_notes: list[str] = []
    _quine_before_fields = bibnorm.strip_published_urls_fields(
        "Quine1970", "article",
        list(bibparse.parse_entries(quine_before)[0][0].fields.items()), _quine_notes)
    quine_sha_before = bibdb.content_sha256("article", dict(_quine_before_fields))
    mock = MockLLM()
    try:
        out_mix = os.path.join(TMP, "mixed")
        r = subprocess.run(
            [sys.executable, "convert.py", "--source", TEX_MIX, "--out", out_mix,
             "--bib", BIB_MIX, "--name", "Test_Author", "--author", "张三",
             "--lang", "zh", "--no-original", "--verify-refs",
             "--bibdb-path", _DB,
             "--llm-base", mock.base, "--llm-key", "k-test", "--llm-model", "mock-chat"],
            cwd=_ENGINE, env=ENV, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240)
        folder, tb = products(out_mix)
        md = read(tb) if tb else ""
        # ⚠️ 并入的是**产物目录里的** reference.bib（convert 会把源 bib 复制到产物目录再改），
        #    断言必须读产物那份——读源文件会永远看到「未改动」（本套件初版就踩了）。
        bib_after = read(os.path.join(folder, "reference.bib"))
        requests = mock.ai_requests()
        check("(f) 只对非安全命中条目调 AI（1 次）", r.returncode == 0 and len(requests) == 1,
              f"rc={r.returncode} n={len(requests)} {(r.stdout + r.stderr)[-300:]}")
        joined = json.dumps(requests, ensure_ascii=False)
        check("(f) 请求里只有候选键 Other2020，**没有**安全命中键 Quine1970",
              "Other2020" in joined and "Quine1970" not in joined)
        check("(f) 日志：命中 1 / 候选 1 / 未命中 0",
              "引用库：命中 1 条已核实（跳过检索+AI）、候选 1、未命中 0" in r.stdout,
              r.stdout[-500:])
        check("(f) 候选条目被正常并入（AI 改写 title）",
              "AI Revised Title" in bib_after and "已并入 1 条" in r.stdout,
              (r.stdout + bib_after)[-500:])
        quine_after_text = entry_block(bib_after, "Quine1970")
        quine_after_sha = bibdb.content_sha256(
            "article", bibparse.parse_entries(quine_after_text)[0][0].fields)
        check("(f) 安全命中条目**内容逐字节未动**（AI 没碰它）",
              quine_after_sha == quine_sha_before, quine_after_text[:200])
        check("(f) 被 AI 改写的只有候选条目",
              "AI Revised Title" in entry_block(bib_after, "Other2020")
              and "AI Revised Title" not in quine_after_text)
        check("(f) 合并记录：库内命中 1 条（未调 AI）与「已并入 1 条」分开",
              "库内命中：另有 **1 条**" in md and "已并入（1 条）" in md
              and "本次（交给 AI）：**并入 1 条**" in md, md[-800:])
        check("(f) 任务书五节把安全命中那条列出来（含差异提示）",
              "## 五、库内已核实（无需再查）" in md and "`Quine1970`" in md)
    finally:
        mock.close()


# ══ (h) 真实稿只读抽查（SKIP when absent）══════════════════════════════════
print("\n── (h) 真实稿只读抽查（MHYC.tex，SKIP when absent）──")
_REAL = os.path.expanduser("~/Desktop/macCaseTest87/1/MHYC.tex")
_REAL_BIB = os.path.expanduser("~/Desktop/macCaseTest87/1/dissertation6.bib")
if not (os.path.isfile(_REAL) and os.path.isfile(_REAL_BIB)):
    skip("(h) 真实稿只读抽查", "本机没有 ~/Desktop/macCaseTest87/1/MHYC.tex")
elif not HAVE_TEMPLATES:
    skip("(h) 真实稿只读抽查", "模板目录不在本机")
else:
    real_sha = sha(_REAL)
    out_real = os.path.join(TMP, "real")
    rr = subprocess.run(
        [sys.executable, "convert.py", "--source", _REAL, "--out", out_real,
         "--bib", _REAL_BIB, "--name", "MHYC_Test", "--author", "测试作者",
         "--lang", "zh", "--no-original", "--verify-refs", "--verify-plan",
         "--search-refs"],
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    check("(h) 真实稿 --verify-refs --verify-plan 退出 0（只读、不联网）",
          rr.returncode == 0, (rr.stdout + rr.stderr)[-400:])
    check("(h) 真实稿量化之后有「引用库：命中…候选…未命中…」一行",
          re.search(r"引用库：命中 \d+ 条已核实（跳过检索\+AI）、候选 \d+、未命中 \d+"
                    r"（库 \d+ 条，", rr.stdout) is not None, rr.stdout[-1200:])
    check("(h) 真实稿的源文件逐字节未改（只读）", sha(_REAL) == real_sha)


# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
