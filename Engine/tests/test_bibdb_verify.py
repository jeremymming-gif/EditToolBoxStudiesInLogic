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
  (h) **匿名合成稿**量化：临时目录内合成 tex + 匿名 JSONL 库（Anon* 键/作者），
      库一律**显式 `--bibdb-path`**，隔离 HOME / 无笔记仓下仍要出「引用库：命中…」一行；
      另加两条反例（显式指错库 / `--no-bibdb`）必须 rc=0 且无库行、无伪命中；
      再加「无关默认库在场」三条反例 + 正向对照：证明显式库不会被默认库改写/混入，
      指错库与 `--no-bibdb` 也**不会回退**到默认库（默认库本身可被解析，反例才有意义）
  (h-real) **真实材料额外覆盖**：只在显式开关 `SLCN_BIBDB_REAL_DIR`（目录内同时给
      manuscript.tex / refs.bib / library.jsonl 三件）时才跑；未设开关一律打印
      `NOT_RUN (h-real) 真实材料额外覆盖：未启用（默认关闭）`，缺项则 FAIL + 用法错误，
      绝不降级成 NOT_RUN 或静默通过。本轮**不读取任何真实材料**，只用临时匿名目录
      自测这套入口机制，真实覆盖标 NOT_RUN。

不联网（LLM 用本地假端点；检索只走 `--verify-plan` 的账目，不发请求）、不依赖稿件库。
(h)/(h-real) 都不读取任何真实来稿/真实样本：本文件出现的「笔记仓」一律是临时匿名目录
（`<tmp>/notes/cases/` + `<tmp>/notes/bib_db/bibliography.jsonl` 现造），不碰本机真实
笔记仓，也不碰任何外部样本目录（真实材料一律只经 `SLCN_BIBDB_REAL_DIR` 显式授权，
本文件从不硬编码/探测其位置）。
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


# ══ (h) 匿名合成稿量化（Anon* 键 + 匿名 JSONL 库 + 显式 --bibdb-path）═══════
print("\n── (h) 匿名合成稿量化（Anon* 键 + 匿名 JSONL 库 + 显式 --bibdb-path）──")
# 全部样本都在临时目录里现造：不读任何真实来稿/真实样本，也不依赖默认库——
# 「引用库：命中…」这一行只由**显式 --bibdb-path** 提供，判据因此与机器环境无关。
ANON_SOURCE = {"kind": bibdb.KIND_PUBLISHED, "issue": "Anon Issue",
               "slug": "00 Anon_Sample", "file": "anon_refs.bib"}
ANON_LIB_VERIFIED = """@article{AnonVerified2024,
  author  = {AnonAuthor, Alice},
  title   = {An Anonymous Synthetic Study of Logic},
  journal = {Anon Journal},
  volume  = {1},
  pages   = {1--10},
  year    = {2024},
  doi     = {10.9999/anon.2024}
}

@book{AnonKeyOnly2023,
  author    = {AnonLibraryAuthor, Bob},
  title     = {A Completely Different Anonymous Library Title},
  publisher = {Anon Press},
  year      = {2023}
}
"""
ANON_LIB_DRAFT = """@article{AnonDraft2021,
  author  = {AnonDraftAuthor, Carol},
  title   = {An Anonymous Unverified Draft},
  journal = {Anon Drafts},
  year    = {2021},
  doi     = {10.9999/anon.2021}
}
"""
if not HAVE_TEMPLATES:
    skip("(h) 匿名合成稿量化", "模板目录不在本机（编辑笔记/模板文件）")
else:
    _h_tmp = tempfile.mkdtemp(prefix="bibdb verify anon h ")
    _h_src = os.path.join(_h_tmp, "src")
    os.makedirs(_h_src, exist_ok=True)
    # 源稿 .bib 与 .tex 同目录（否则 convert 会按「多个完整稿件版本」改写源 bib）
    _h_tex = write(os.path.join(_h_src, "anon_manuscript.tex"), r"""\documentclass{article}
\title{匿名合成测试题名}
\author{Anon Author}
\begin{document}
\maketitle
正文引用\parencite{AnonVerified2024}、\parencite{AnonKeyOnly2023}、\parencite{AnonDraft2021}，
以及库里没有的\parencite{AnonMissing2022}。

\printbibliography
\end{document}
""")
    _h_bib = write(os.path.join(_h_src, "anon_refs.bib"), """@article{AnonVerified2024,
  author  = {AnonAuthor, Alice},
  title   = {An Anonymous Synthetic Study of Logic},
  journal = {Anon Journal},
  volume  = {1},
  pages   = {1--10},
  year    = {2024},
  doi     = {10.9999/anon.2024}
}

@book{AnonKeyOnly2023,
  author    = {AnonManuscriptAuthor, Dana},
  title     = {A Manuscript Title That Differs From The Library One},
  publisher = {Anon Manuscript Press},
  year      = {2023}
}

@article{AnonDraft2021,
  author  = {AnonDraftAuthor, Carol},
  title   = {An Anonymous Unverified Draft},
  journal = {Anon Drafts},
  year    = {2021},
  doi     = {10.9999/anon.2021}
}

@article{AnonMissing2022,
  author  = {AnonMissingAuthor, Eve},
  title   = {An Anonymous Paper Not In The Library At All},
  journal = {Anon Missing Journal},
  year    = {2022}
}
""")
    _h_lib = os.path.join(_h_tmp, "anon_h_db.jsonl")
    _h_store = bibdb.Store.load(_h_lib, create=True)
    _h_store.import_bib_text(ANON_LIB_VERIFIED, ANON_SOURCE, verified=True)
    _h_store.import_bib_text(ANON_LIB_DRAFT,
                             {"kind": bibdb.KIND_IMPORTED, "file": "anon_draft.bib"},
                             verified=False)
    _h_store.save()
    _h_records = bibdb.Store.load(_h_lib).records()
    _h_n = len(_h_records)
    _h_keys = [(r["key"], r["verified"]) for r in _h_records]
    check("(h) 匿名库只有 3 条匿名记录（2 已核实 + 1 未核实），键名全 Anon*",
          _h_n == 3 and _h_keys == [("AnonDraft2021", False),
                                    ("AnonKeyOnly2023", True),
                                    ("AnonVerified2024", True)], _h_keys)

    _h_sha_before = (sha(_h_tex), sha(_h_bib), sha(_h_lib))

    def _h_run(tag: str, extra: list[str],
               env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "convert.py", "--source", _h_tex,
             "--out", os.path.join(_h_tmp, tag), "--bib", _h_bib,
             "--name", "Anon_Author", "--author", "匿名作者",
             "--lang", "zh", "--no-original"] + extra,
            cwd=_ENGINE, env=env or ENV, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240)

    def _h_taskbook(tag: str) -> str:
        """(h) 段产物目录的任务书正文；没有就是空串（不抛异常）。"""
        books = glob.glob(os.path.join(_h_tmp, tag, "Anon_Author", "*_文献核实.md"))
        return read(books[0]) if books else ""

    # (h1) --verify-plan：查库 + 预计次数按扣减后算（只出账目，不检索、不调 AI）
    _h_plan = _h_run("plan", ["--verify-refs", "--verify-plan", "--search-refs",
                              "--bibdb-path", _h_lib,
                              # 指向本地空端口：cfg.ready=True 也绝不可能真连出去
                              "--llm-base", "http://127.0.0.1:9", "--llm-key", "k",
                              "--llm-model", "mock-model"])
    _h_plan_out = _h_plan.stdout + _h_plan.stderr
    check("(h) 匿名稿 --verify-refs --verify-plan 退出 0（只读、不联网）",
          _h_plan.returncode == 0 and "Traceback" not in _h_plan_out,
          _h_plan_out[-400:])
    _h_line = re.search(
        r"引用库：命中 (\d+) 条已核实（跳过检索\+AI）、候选 (\d+)、未命中 (\d+)"
        r"（库 (\d+) 条，" + re.escape(_h_lib) + r"）", _h_plan.stdout)
    check("(h) 匿名稿 + 显式 --bibdb-path 出「引用库：命中 X…候选 Y…未命中 Z（库 n 条，<库路径>）」一行",
          _h_line is not None, _h_plan.stdout[-900:])
    if _h_line is None:
        check("(h) 该行计数与匿名库自洽：命中 1（DOI 精确）+ 候选 2（key-only/未核实）+ 未命中 1，库 3 条",
              False, "日志里没有引用库行")
        check("(h) 该行 X+Y+Z 等于本次核实条数（4 条引用一条不漏）", False, "日志里没有引用库行")
    else:
        _h_x, _h_y, _h_z, _h_m = (int(_g) for _g in _h_line.groups())
        check("(h) 该行计数与匿名库自洽：命中 1（DOI 精确）+ 候选 2（key-only/未核实）+ 未命中 1，库 3 条",
              (_h_x, _h_y, _h_z, _h_m) == (1, 2, 1, _h_n),
              f"X={_h_x} Y={_h_y} Z={_h_z} n={_h_m}（匿名库实测 {_h_n} 条）")
        check("(h) 该行 X+Y+Z 等于本次核实条数（4 条引用一条不漏，也没有多算）",
              _h_x + _h_y + _h_z == 4
              and "正文引用 4 条 → 本次核实 4 条" in _h_plan.stdout,
              f"X+Y+Z={_h_x + _h_y + _h_z}")
    check("(h) --verify-plan 的预计检索/AI 按扣减后算（1 条安全命中不计：6 次查询 / 3 次调用）",
          "引用库已核实：1 条（不检索、不调 AI）" in _h_plan.stdout
          and "预计检索：≤ 6 次查询" in _h_plan.stdout
          and "预计 AI：≤ 3 次调用" in _h_plan.stdout, _h_plan.stdout[-600:])

    # (h2) --verify-refs（不配 LLM）：五节只列安全命中、无伪命中、源文件逐字节未改
    _h_refs = _h_run("refs", ["--verify-refs", "--bibdb-path", _h_lib])
    _h_books = glob.glob(os.path.join(_h_tmp, "refs", "Anon_Author", "*_文献核实.md"))
    _h_md = read(_h_books[0]) if _h_books else ""
    check("(h) 匿名稿 --verify-refs 退出 0、同一行计数仍是 1/2/1（库 3 条）",
          _h_refs.returncode == 0
          and re.search(r"引用库：命中 1 条已核实（跳过检索\+AI）、候选 2、未命中 1"
                        r"（库 3 条，" + re.escape(_h_lib) + r"）",
                        _h_refs.stdout) is not None,
          (_h_refs.stdout + _h_refs.stderr)[-400:])
    _h_five = (_h_md.split("## 五、库内已核实（无需再查）")[1]
               if "## 五、库内已核实（无需再查）" in _h_md else "")
    check("(h) 任务书五节只列 DOI 精确命中的 AnonVerified2024（含库内来源与差异提示）",
          bool(_h_five) and "`AnonVerified2024`" in _h_five
          and "Anon Issue/00 Anon_Sample/anon_refs.bib" in _h_five
          and "库版本字段差异（待编辑裁决）" in _h_five, _h_five[:700])
    check("(h) 候选/未命中那 3 条**没有**被写进五节（无伪命中）",
          bool(_h_five) and not any(_k in _h_five for _k in
                                    ("AnonKeyOnly2023", "AnonDraft2021", "AnonMissing2022")),
          _h_five[:700])
    check("(h) 匿名稿的源 tex / 源 bib / 匿名库跑完逐字节未改（只读）",
          (sha(_h_tex), sha(_h_bib), sha(_h_lib)) == _h_sha_before)

    # (h3) 反例·显式指错库：退出 0、告警照实说、无库行、任务书无五节
    _h_nope = os.path.join(_h_tmp, "not_here.jsonl")
    _h_bad = _h_run("nodb", ["--verify-refs", "--bibdb-path", _h_nope])
    _h_bad_out = _h_bad.stdout + _h_bad.stderr
    _h_bad_books = glob.glob(os.path.join(_h_tmp, "nodb", "Anon_Author", "*_文献核实.md"))
    _h_bad_md = read(_h_bad_books[0]) if _h_bad_books else ""
    check("(h) 反例·显式指错库 → 退出 0、照实告警「引用库文件不存在…本次不查库」",
          _h_bad.returncode == 0 and "引用库文件不存在" in _h_bad_out
          and "本次不查库" in _h_bad_out, _h_bad_out[-400:])
    check("(h) 反例·指错库的 stdout 没有「引用库：」行（不编造命中）",
          "引用库：" not in _h_bad.stdout)
    check("(h) 反例·指错库的任务书没有「五、库内已核实」",
          bool(_h_bad_md) and "库内已核实" not in _h_bad_md)

    # (h4) 反例·--no-bibdb：与「指错库」逐字节同（= 接入前行为）
    _h_off = _h_run("off", ["--verify-refs", "--no-bibdb"])
    _h_off_books = glob.glob(os.path.join(_h_tmp, "off", "Anon_Author", "*_文献核实.md"))
    _h_off_md = read(_h_off_books[0]) if _h_off_books else ""
    check("(h) 反例·`--no-bibdb` → 退出 0、无「引用库：」行、任务书无五节",
          _h_off.returncode == 0 and "引用库：" not in _h_off.stdout
          and bool(_h_off_md) and "库内已核实" not in _h_off_md,
          (_h_off.stdout + _h_off.stderr)[-300:])
    check("(h) 反例·`--no-bibdb` 与「指错库」的任务书逐字节相同",
          bool(_h_off_md) and _h_off_md == _h_bad_md)

    # (h5) 隔离 HOME + 无笔记仓：默认库解析不到，仍靠显式 --bibdb-path 出同一行
    _h_iso_home = os.path.join(_h_tmp, "iso_home")
    os.makedirs(_h_iso_home, exist_ok=True)
    _h_iso_env = dict(ENV, HOME=_h_iso_home,
                      SLCN_NOTES=os.path.join(_h_tmp, "no_notes_here"))
    _h_iso = _h_run("iso", ["--verify-refs", "--bibdb-path", _h_lib], env=_h_iso_env)
    check("(h) 隔离 HOME / 无笔记仓（默认库解析不到）→ 仍出同一行、退出 0",
          _h_iso.returncode == 0
          and re.search(r"引用库：命中 1 条已核实（跳过检索\+AI）、候选 2、未命中 1"
                        r"（库 3 条，" + re.escape(_h_lib) + r"）",
                        _h_iso.stdout) is not None,
          (_h_iso.stdout + _h_iso.stderr)[-400:])
    # 反向对照：同一隔离环境里**去掉** --bibdb-path → 默认库解析不到，就一行都没有。
    # 这才证明上面那一行来自显式 --bibdb-path，而不是本机某处的默认库（匿名性的核心）。
    _h_iso_nopath = _h_run("iso_nopath", ["--verify-refs"], env=_h_iso_env)
    check("(h) 隔离环境去掉 `--bibdb-path` → 退出 0 但**没有**引用库行（证明该行只来自显式路径）",
          _h_iso_nopath.returncode == 0 and "引用库：" not in _h_iso_nopath.stdout
          and "引用库文件不存在" not in (_h_iso_nopath.stdout + _h_iso_nopath.stderr),
          (_h_iso_nopath.stdout + _h_iso_nopath.stderr)[-400:])

    # ── (h) 无关默认库在场：三条反例 + 正向对照 ────────────────────────────────
    # 上面 (h5b) 证明的是「默认库解析不到时不串库」。(h5b) 单独看有个漏洞：
    # 万一实现是「默认库解析不到就忽略」，那它证明不了「默认库**解析得到但无关**时
    # 也不串库」——后者才是真实风险（编辑机上默认库通常存在）。
    # 所以这里现造一个可被解析的「笔记仓」形状：
    #   <tmp>/notes/cases/                  ← slcn/paths.py:notes_root() 的判据
    #   <tmp>/notes/bib_db/bibliography.jsonl ← slcn/bibdb.py:default_db_path()
    # 默认库里放**同名同 DOI 但内容无关**的记录（题名/作者/年份/刊名全不同）：
    # 一旦显式库被默认库改写、混入、或在「指错库 / --no-bibdb」时回退到默认库，
    # 计数、日志路径、以及任务书里的题名作者立刻会露馅。
    _h_notes = os.path.join(_h_tmp, "notes")
    os.makedirs(os.path.join(_h_notes, "cases"), exist_ok=True)
    _h_default_db = os.path.join(_h_notes, "bib_db", "bibliography.jsonl")
    os.makedirs(os.path.dirname(_h_default_db), exist_ok=True)
    _h_decoy_bib = """@article{AnonVerified2024,
  author  = {TotallyDifferent, Zoe},
  title   = {An Unrelated Decoy Work About Nothing},
  journal = {Decoy Journal},
  year    = {1899},
  doi     = {10.9999/anon.2024}
}
"""
    _h_decoy = bibdb.Store.load(_h_default_db, create=True)
    _h_decoy.import_bib_text(_h_decoy_bib,
                             {"kind": bibdb.KIND_PUBLISHED, "issue": "Decoy Issue",
                              "slug": "99 Decoy_Sample", "file": "decoy.bib"},
                             verified=True)
    _h_decoy.save()
    _h_decoy_n = len(bibdb.Store.load(_h_default_db).records())
    _h_decoy_marks = ("An Unrelated Decoy Work About Nothing", "TotallyDifferent",
                      "Decoy Journal")
    check("(h) 无关默认库就位：<tmp>/notes/cases/ + <tmp>/notes/bib_db/bibliography.jsonl"
          "（1 条同名同 DOI、内容全不同）",
          _h_decoy_n == 1
          and os.path.isdir(os.path.join(_h_notes, "cases"))
          and os.path.isfile(_h_default_db),
          f"默认库 {_h_decoy_n} 条：{_h_default_db}")
    # 反例的前提是「这个默认库真的解析得到」。用子进程按同一份 env 问一次生产代码，
    # 而不是自己猜路径（否则反例可能打在空气上）。
    _h_notes_env = dict(ENV, SLCN_NOTES=_h_notes)
    _h_resolve = subprocess.run(
        [sys.executable, "-c",
         "import json; from slcn import bibdb, paths;"
         " print(json.dumps({'notes': paths.notes_root(),"
         " 'db': bibdb.default_db_path()}))"],
        cwd=_ENGINE, env=_h_notes_env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    _h_resolved = {}
    try:
        _h_resolved = json.loads(_h_resolve.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001  解析不了就是空表 → 下面的 check 自然 FAIL
        _h_resolved = {}
    check("(h) 该无关默认库确实**解析得到**（notes_root()/default_db_path() 都指向它）"
          "→ 反例才有意义",
          os.path.abspath(str(_h_resolved.get("db") or "")) == _h_default_db
          and os.path.abspath(str(_h_resolved.get("notes") or "")) == _h_notes,
          f"notes_root={_h_resolved.get('notes')} default_db={_h_resolved.get('db')}"
          f" 期望={_h_default_db}")

    # 反例 ①：无关默认库在场 + 显式 --bibdb-path <匿名库> → 结果必须来自匿名库
    _h_x1 = _h_run("x_anon", ["--verify-refs", "--bibdb-path", _h_lib],
                   env=_h_notes_env)
    _h_x1_line = re.search(
        r"引用库：命中 (\d+) 条已核实（跳过检索\+AI）、候选 (\d+)、未命中 (\d+)"
        r"（库 (\d+) 条，(.*)）", _h_x1.stdout)
    _h_x1_counts = tuple(int(_g) for _g in _h_x1_line.groups()[:4]) \
        if _h_x1_line else None
    _h_x1_path = (_h_x1_line.group(5) if _h_x1_line else "")
    check("(h) 反例①·无关默认库在场 + 显式 --bibdb-path → 计数仍是匿名库的 1/2/1（库 3 条）",
          _h_x1.returncode == 0 and _h_x1_counts == (1, 2, 1, _h_n),
          f"计数={_h_x1_counts} 期望={(1, 2, 1, _h_n)}（匿名库 {_h_n} 条）"
          f"；退出 {_h_x1.returncode}")
    check("(h) 反例①·日志里的库路径是**匿名库**绝对路径，不是默认库（没被改写/混入）",
          os.path.abspath(_h_x1_path) == os.path.abspath(_h_lib)
          and _h_default_db not in _h_x1.stdout,
          f"日志路径={_h_x1_path} 期望={_h_lib}")
    _h_x1_md = _h_taskbook("x_anon")
    check("(h) 反例①·任务书里没有默认库那套无关内容（题名/作者/刊名全不出现）",
          all(_m not in _h_x1_md for _m in _h_decoy_marks),
          [m for m in _h_decoy_marks if m in _h_x1_md])

    # 反例 ②：无关默认库在场 + 显式指错库 → 照实告警，**不回退**到默认库
    _h_x2_nope = os.path.join(_h_tmp, "x_nope_sub", "no_such_lib.jsonl")
    _h_x2 = _h_run("x_nodb", ["--verify-refs", "--bibdb-path", _h_x2_nope],
                   env=_h_notes_env)
    _h_x2_out = _h_x2.stdout + _h_x2.stderr
    _h_x2_md = _h_taskbook("x_nodb")
    _h_x2_warn = next((ln.strip() for ln in _h_x2_out.splitlines()
                       if "引用库文件不存在" in ln), "")
    check("(h) 反例②·无关默认库在场 + 显式指错库 → 退出 0、照实告警「引用库文件不存在…"
          "本次不查库」",
          _h_x2.returncode == 0 and "引用库文件不存在" in _h_x2_out
          and "本次不查库" in _h_x2_out,
          f"退出={_h_x2.returncode}、告警行={_h_x2_warn or '（缺）'}")
    check("(h) 反例②·**没有**「引用库：」行、任务书无五节（证明不回退到无关默认库）",
          "引用库：" not in _h_x2.stdout
          and "## 五、库内已核实（无需再查）" not in _h_x2_md,
          f"有库行={'引用库：' in _h_x2.stdout}、"
          f"五节={'五节' if '## 五、库内已核实（无需再查）' in _h_x2_md else '无五节'}")

    # 反例 ③：无关默认库在场 + --no-bibdb → 同样一行都不出
    _h_x3 = _h_run("x_off", ["--verify-refs", "--no-bibdb"], env=_h_notes_env)
    _h_x3_md = _h_taskbook("x_off")
    check("(h) 反例③·无关默认库在场 + `--no-bibdb` → 退出 0、无「引用库：」行、任务书无五节",
          _h_x3.returncode == 0 and "引用库：" not in _h_x3.stdout
          and "## 五、库内已核实（无需再查）" not in _h_x3_md,
          f"退出={_h_x3.returncode}、有库行={'引用库：' in _h_x3.stdout}、"
          f"五节={'五节' if '## 五、库内已核实（无需再查）' in _h_x3_md else '无五节'}")

    # 正向对照：SLCN_NOTES=<tmp>/notes 且**不传** --bibdb-path → 默认库被自动启用。
    # 这一条是三个反例的地基：默认库明明解析得到，反例里的「没有库行」才说明是
    # 显式开关在起作用，而不是「本机压根没有默认库」。
    _h_ctl = _h_run("x_ctrl", ["--verify-refs"], env=_h_notes_env)
    _h_ctl_line = re.search(
        r"引用库：命中 (\d+) 条已核实（跳过检索\+AI）、候选 (\d+)、未命中 (\d+)"
        r"（库 (\d+) 条，(.*)）", _h_ctl.stdout)
    _h_ctl_counts = tuple(int(_g) for _g in _h_ctl_line.groups()[:4]) \
        if _h_ctl_line else None
    _h_ctl_path = (_h_ctl_line.group(5) if _h_ctl_line else "")
    check("(h) 正向对照·SLCN_NOTES 指向临时笔记仓、不传 --bibdb-path → 默认库被自动启用，"
          "「引用库：」行的路径正是 <tmp>/notes/bib_db/bibliography.jsonl（库 1 条）",
          _h_ctl.returncode == 0 and _h_ctl_line is not None
          and os.path.abspath(_h_ctl_path) == _h_default_db
          and _h_ctl_counts is not None and _h_ctl_counts[3] == _h_decoy_n,
          f"计数={_h_ctl_counts} 路径={_h_ctl_path}")
    # 实测记录（不粉饰）：该场景下命中的是 0 条而不是 1 条。
    # 原因已查清（不是测试写法问题）：产物 `reference.bib` 走
    # `bibnorm.normalize_bib(..., strip_urls=True)`（B-189：正式发表条目不写 url/doi），
    # 库里的 DOI 在稿件侧已被剥掉 → DOI 精确命中不触发，只剩 key 同名的**候选**。
    # 所以这里如实断言实测值 0/1/3，并单独断言「候选来自同名键」这一可解释性。
    check("(h) 正向对照·实测计数如实记录：命中 0（DOI 已在产物 bib 里被 B-189 剥掉）、"
          "候选 1（仅 key 同名）、未命中 3",
          _h_ctl_counts == (0, 1, 3, _h_decoy_n),
          f"计数={_h_ctl_counts}（默认库 {_h_decoy_n} 条）")
    _h_ctl_md = _h_taskbook("x_ctrl")
    check("(h) 正向对照·无安全命中 → 任务书不出五节，且默认库那套无关内容一个字都没进任务书",
          "## 五、库内已核实（无需再查）" not in _h_ctl_md
          and all(_m not in _h_ctl_md for _m in _h_decoy_marks),
          [m for m in _h_decoy_marks if m in _h_ctl_md] or "无五节、无无关内容")


# ══ (h-real) 真实材料额外覆盖：显式开关 + 默认关闭 ═══════════════════════════
print("\n── (h-real) 真实材料额外覆盖（显式开关，默认关闭；本轮一律 NOT_RUN）──")
# 这一段的**判据全部来自临时匿名目录**（本段内现造 tex/bib/jsonl 三件套）：
# 只有「入口机制」被验证，真实材料一份都不读。开关是唯一入口，绝不去猜
# 任何桌面/笔记仓/稿件仓位置——那既不合法授权，也让测试不可移植。
_REAL_ENV = "SLCN_BIBDB_REAL_DIR"
_REAL_REQUIRED = ("manuscript.tex", "refs.bib", "library.jsonl")


def _real_missing(directory: str) -> list[str]:
    """(h-real) 目录自检：返回缺项清单（空列表 = 三件齐全）。只看存在性，不读内容。"""
    if not os.path.isdir(directory):
        return list(_REAL_REQUIRED)
    return [n for n in _REAL_REQUIRED if not os.path.isfile(os.path.join(directory, n))]


def _real_usage(directory: str, missing: list[str]) -> str:
    """(h-real) 缺项时的用法错误文案：列出缺哪件 + 期望路径。"""
    return (f"用法错误：{_REAL_ENV}={directory or '<未设置>'} 不是可用的真实额外覆盖目录——"
            f"缺 {'、'.join(missing)}；期望该目录下同时提供 "
            + " / ".join(_REAL_REQUIRED)
            + "（manuscript.tex=稿件正文、refs.bib=稿件 bib、library.jsonl=引用库）")


def _real_run(directory: str, tag: str,
              env: dict | None = None) -> tuple[subprocess.CompletedProcess, str]:
    """用显式 --bibdb-path <library.jsonl> 跑 --verify-refs，返回 (进程结果, 任务书正文)。"""
    out = tempfile.mkdtemp(prefix=f"bibdb verify real out {tag} ")
    proc = subprocess.run(
        [sys.executable, "convert.py",
         "--source", os.path.join(directory, "manuscript.tex"),
         "--out", out,
         "--bib", os.path.join(directory, "refs.bib"),
         "--name", "Real_Author", "--author", "匿名作者", "--lang", "zh",
         "--no-original", "--verify-refs",
         "--bibdb-path", os.path.join(directory, "library.jsonl")],
        cwd=_ENGINE, env=env or ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    books = glob.glob(os.path.join(out, "Real_Author", "*_文献核实.md"))
    return proc, (read(books[0]) if books else "")


def _real_section(md: str) -> str:
    """任务书里「五、库内已核实」这一节的正文本（没有就是空串）。"""
    head = "## 五、库内已核实（无需再查）"
    if head not in md:
        return ""
    body = md.split(head, 1)[1]
    nxt = re.search(r"^## ", body, re.M)
    return body[:nxt.start()] if nxt else body


def _real_assert_flow(tag: str, directory: str, records_n: int,
                      *, env: dict | None = None, records=None) -> None:
    """(h-real) 进入后的四条断言：rc / 库行与 n / 五节只列安全命中 / 三件字节不变。"""
    lib = os.path.join(directory, "library.jsonl")
    before = {n: sha(os.path.join(directory, n)) for n in _REAL_REQUIRED}
    proc, md = _real_run(directory, tag, env=env)
    check(f"(h-real) [{tag}] 显式 --bibdb-path 跑 --verify-refs → 退出 0",
          proc.returncode == 0,
          f"rc={proc.returncode}"
          + ("" if proc.returncode == 0
             else "；stderr 尾：" + (proc.stdout + proc.stderr).strip().splitlines()[-1][:200]))
    pat = (r"引用库：命中 (\d+) 条已核实（跳过检索\+AI）、候选 (\d+)、未命中 (\d+)"
           r"（库 (\d+) 条，" + re.escape(lib) + r"）")
    hit = re.search(pat, proc.stdout)
    counts = tuple(int(x) for x in hit.groups()) if hit else None
    check(f"(h-real) [{tag}] 出现「引用库：…（库 n 条，<该 library.jsonl 绝对路径>）」"
          " 且 n == 库记录数",
          counts is not None and counts[3] == records_n,
          f"计数={counts}（库 {records_n} 条）")
    # 五节只列**安全命中**：五节 ⟺ 有安全命中；条目数 == 安全命中数；
    # 且列出的每个键都在库里且 verified（库为空 / 无已核实记录时安全命中必为 0）。
    body = _real_section(md)
    safe = counts[0] if counts else -1
    listed = re.findall(r"^- `([^`]+)`", body, re.M)
    verified_keys = {r["key"] for r in (records or []) if r.get("verified")}
    check(f"(h-real) [{tag}] 任务书五节 ⟺ 有安全命中（安全命中 {safe} 条），"
          "条目数 == 安全命中数，列出的键全是库内**已核实**记录",
          counts is not None and (safe > 0) == bool(body) and len(listed) == safe
          and (records is None or set(listed) <= verified_keys),
          f"安全命中={safe}、有五节={bool(body)}、五节列出={listed}")
    if records is not None:
        cand_or_miss = [r["key"] for r in records
                        if not r.get("verified") or r["key"] not in listed]
        check(f"(h-real) [{tag}] 候选 / 未命中一条都不进五节（不在 safe 里的键零出现）",
              all(k not in body for k in cand_or_miss if k not in listed),
              [k for k in cand_or_miss if k in body and k not in listed] or "候选/未命中均未进五节")
    after = {n: sha(os.path.join(directory, n)) for n in _REAL_REQUIRED}
    check(f"(h-real) [{tag}] 三件源文件（manuscript.tex/refs.bib/library.jsonl）跑完 sha256 不变",
          before == after,
          {k: (before[k][:12], after[k][:12]) for k in before if before[k] != after[k]})


# (h-real-1) 入口机制自检①：缺项必须被判出来（不许降级成 NOT_RUN / 静默通过）
_r_tmp = tempfile.mkdtemp(prefix="bibdb verify real h ")
_r_partial = os.path.join(_r_tmp, "partial")
write(os.path.join(_r_partial, "manuscript.tex"), "\\documentclass{article}\n")
_r_absent = os.path.join(_r_tmp, "not_here_at_all")
_r_partial_missing = _real_missing(_r_partial)
_r_absent_missing = _real_missing(_r_absent)
check("(h-real) 缺项自检·只放 manuscript.tex 的临时目录 → 判出缺 refs.bib / library.jsonl",
      _r_partial_missing == ["refs.bib", "library.jsonl"], _r_partial_missing)
check("(h-real) 缺项自检·目录根本不存在 → 三件都算缺",
      _r_absent_missing == list(_REAL_REQUIRED), _r_absent_missing)
_r_usage_text = _real_usage(_r_partial, _r_partial_missing)
check("(h-real) 缺项自检·用法错误文案写明缺哪件 + 期望路径 + 三件全名",
      all(w in _r_usage_text for w in ("用法错误", _REAL_ENV, "refs.bib", "library.jsonl",
                                       _r_partial, "manuscript.tex")), _r_usage_text)

# (h-real-2) 入口机制自检②：完整匿名三件套走显式 --bibdb-path，断言 3/4 全部成立
_r_anon = os.path.join(_r_tmp, "anon_real_dir")
write(os.path.join(_r_anon, "manuscript.tex"), r"""\documentclass{article}
\title{匿名额外覆盖自测题名}
\author{Real Anon}
\begin{document}
\maketitle
正文引用\parencite{RealAnonHit2025}、\parencite{RealAnonCand2024}，
以及库里没有的\parencite{RealAnonMiss2023}。

\printbibliography
\end{document}
""")
write(os.path.join(_r_anon, "refs.bib"), """@article{RealAnonHit2025,
  author  = {RealAnonAuthor, Alice},
  title   = {An Anonymous Extra Coverage Study},
  journal = {Manuscript Journal},
  volume  = {2},
  pages   = {11--20},
  year    = {2025}
}

@article{RealAnonCand2024,
  author  = {RealAnonCandAuthor, Bob},
  title   = {An Anonymous Candidate Study},
  journal = {Candidate Journal},
  year    = {2024}
}

@article{RealAnonMiss2023,
  author  = {RealAnonMissAuthor, Carol},
  title   = {An Anonymous Missing Study},
  journal = {Missing Journal},
  year    = {2023}
}
""")
_r_anon_lib = os.path.join(_r_anon, "library.jsonl")
_r_store = bibdb.Store.load(_r_anon_lib, create=True)
_r_store.import_bib_text("""@article{RealAnonHit2025,
  author  = {RealAnonAuthor, Alice},
  title   = {An Anonymous Extra Coverage Study},
  journal = {Library Journal},
  volume  = {9},
  pages   = {91--99},
  year    = {2025}
}
""", {"kind": bibdb.KIND_PUBLISHED, "issue": "Lib Issue",
      "slug": "01 Lib_Sample", "file": "lib.bib"}, verified=True)
_r_store.import_bib_text("""@article{RealAnonCand2024,
  author  = {RealAnonCandAuthor, Bob},
  title   = {An Anonymous Candidate Study},
  journal = {Candidate Journal},
  year    = {2024}
}
""", {"kind": bibdb.KIND_IMPORTED, "file": "cand.bib"}, verified=False)
_r_store.save()
_r_anon_records = bibdb.Store.load(_r_anon_lib).records()
_r_anon_n = len(_r_anon_records)
check("(h-real) 完整匿名三件套就位（manuscript.tex / refs.bib / library.jsonl），库 2 条 → 缺项自检为空",
      _real_missing(_r_anon) == [] and _r_anon_n == 2,
      f"缺项={_real_missing(_r_anon)}、库 {_r_anon_n} 条")
if not HAVE_TEMPLATES:
    skip("(h-real) 匿名三件套端到端",
         "模板目录不在本机（编辑笔记/模板文件），convert 无法出稿")
else:
    _real_assert_flow("anon", _r_anon, _r_anon_n, records=_r_anon_records)

# (h-real-3) 真实开关：未设置 → 只打 NOT_RUN（如实报告「真实覆盖未取得」）；
# 设置了 → 要么按缺项 FAIL + 用法错误，要么跑真流程。绝无第三条路。
# 注意：NOT_RUN 是**普通打印**而非 SKIP——「没跑」必须与「跑了但跳过」区分开，
# 更不能让 SKIP 计数把「真实覆盖未取得」粉饰成通过。
_r_dir = (os.environ.get(_REAL_ENV) or "").strip()
if not _r_dir:
    print("NOT_RUN (h-real) 真实材料额外覆盖：未启用（默认关闭）")
    print(f"   （如需启用：{_REAL_ENV}=<目录>，目录内需同时有 "
          + " / ".join(_REAL_REQUIRED)
          + "；本轮按卡要求不读取任何真实来稿，故真实覆盖标 NOT_RUN，不冒充通过）")
else:
    _r_real_missing = _real_missing(_r_dir)
    if _r_real_missing:
        check(f"(h-real) {_REAL_ENV} 指向的目录缺件 → FAIL（不降级成 NOT_RUN、不静默通过）",
              False, _real_usage(_r_dir, _r_real_missing))
    else:
        # 库结构坏了必须判 FAIL 并说清坏在哪，不能让 BibDBError 以 traceback 形式抛出去：
        # 那样退出码虽然也非零，但输出里没有「哪一件材料有问题、怎么修」的结论，
        # 也无法与「缺件」区分开（缺件是用法错误，坏库是材料问题，两者要分别报告）。
        try:
            _r_real_records = bibdb.Store.load(os.path.join(_r_dir, "library.jsonl")).records()
            _r_real_err = ""
        except Exception as _exc:                       # noqa: BLE001 —— 任何坏库都归为材料问题
            _r_real_records = []
            _r_real_err = f"{type(_exc).__name__}: {_exc}"
        _r_real_n = len(_r_real_records)
        check(f"(h-real) {_REAL_ENV} 指向的目录三件齐全、库可读（{_r_real_n} 条）",
              not _r_real_err,
              _r_dir if not _r_real_err else
              (f"library.jsonl 读不了（三件齐全但库本身不合法，属材料问题而非用法问题）："
               f"{_r_real_err}"))
        if _r_real_err:
            # 材料本身不合法：上面那条 check 已经如实判 FAIL 并给出原因，
            # 端到端流程不再往下跑（拿一个读不了的库去跑 convert 只会得到误导性的二次错误）。
            skip(f"(h-real) [{_REAL_ENV}] 端到端",
                 "library.jsonl 不合法（见上一条 FAIL），端到端不重复跑")
        elif not HAVE_TEMPLATES:
            skip(f"(h-real) [{_REAL_ENV}] 端到端",
                 "模板目录不在本机（编辑笔记/模板文件），convert 无法出稿")
        else:
            # 库为空也照跑：此时安全命中必为 0、五节必不出现——这同样是要断言的
            # 「只列安全命中」的一半，不能靠提前 FAIL 回避。
            _real_assert_flow("real", _r_dir, _r_real_n, records=_r_real_records)


# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
