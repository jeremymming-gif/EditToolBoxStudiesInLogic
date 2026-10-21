#!/usr/bin/env python3
r"""批79（B-131）：协同记录「交付记录（清单/引用/打印/未打印）」口径测试。

用户 2026-10-21 实测（案例 195）：交付 PDF 参考文献 19 条，用户不知道「为什么是 19」。
本卡只在 `_AI协同记录.md` 里补一节**可解释的交付口径**（报数，不改判据/不改产物）：
  · `源稿文末清单 N 条` / `正文引用键 M 个` / `打印条数 P 条` / `未打印 U 条（= N − P）`；
  · 未打印原因分类（未被正文链路引用 / 正文引用但清单缺条 / 解析未识别 / 库未命中）；
  · `引用库命中 H 条（未调 AI）`；
  · 拿不到的计数写 `未测` + 一句原因（禁止估算/编造）。
  · **既有节逐字节不变**（本节固定标题、不加中文序号）；**产物零变化**。

全部离线（tempfile + 合成夹具/状态；不读真实库、不联网、不跑 TeX）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b79_b131_delivery_record.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return env


ENV = make_env()

_STEM = "San_Zhang"
_FAR = "全文独有标记·不得进记录·ZZZ"

_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
开头一段正文。这里引用\parencite{Quine1970}，并带一个脚注。\footnote{脚注说明文字。}
""" + _FAR + r"""
\printbibliography
\end{document}
"""

_BIB = """@article{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  journal = {Mind},
  year = {1970},
}
@article{Other2020,
  author = {Other, Olivia},
  title = {An Unrelated Work on Logic},
  publisher = {Some Press},
  year = {2020},
}
"""


def _run(engine: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"), *args],
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _fixture(tmp: str, tag: str) -> tuple[str, str, str]:
    srcdir = os.path.join(tmp, tag, "src")
    source = write(os.path.join(srcdir, "main.tex"), _TEX)
    bibp = write(os.path.join(srcdir, "refs.bib"), _BIB)
    return source, bibp, os.path.join(tmp, tag, "out")


def _ph1(src: str, bibp: str, out: str) -> subprocess.CompletedProcess:
    return _run(_ENGINE, ["--source", src, "--bib", bibp, "--out", out,
                          "--author", "张三", "--no-original", "--coop-taskbook-only"])


def _ph2(src: str, out: str, reply: str) -> subprocess.CompletedProcess:
    return _run(_ENGINE, ["--source", src, "--out", out, "--coop-replies", reply,
                          "--no-original"])


def _stage(out: str) -> str:
    return os.path.join(out, _STEM + "_待核")


def _final(out: str) -> str:
    return os.path.join(out, _STEM)


def _state(out: str) -> dict:
    p = os.path.join(_stage(out), coop.STATE_NAME)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}


# ── ① 计数纯函数 ────────────────────────────────────────────────────────


def test_counts() -> None:
    d = coop.build_delivery_summary(
        bib_keys=["A", "B", "C"], cited_keys=["A", "X"])
    check("①-1 清单/引用/打印/未打印计数正确",
          (d["n_list"], d["n_cited"], d["n_printed"], d["n_unprinted"])
          == (3, 2, 1, 2), d)
    check("①-2 未打印明细 = 清单里未被正文引用的键（顺序同清单）",
          d["unprinted"] == ["B", "C"], d["unprinted"])
    check("①-3 正文引用但清单缺条 = 引用键里有、清单没有的（X）",
          d["n_missing"] == 1, d["n_missing"])
    d2 = coop.build_delivery_summary(
        bib_keys=["A", "B"], cited_keys=["A"], nocite_all=True)
    check("①-4 \\nocite{*} → 打印=全清单、未打印=0、明细空",
          (d2["n_printed"], d2["n_unprinted"], d2["unprinted"]) == (2, 0, []), d2)
    d3 = coop.build_delivery_summary(
        bib_keys=["A"], cited_keys=["A"],
        low_items=[{"kind": "bib_line"}, {"kind": "bib_line"},
                   {"kind": "footnote"}])
    check("①-5 解析未识别 = 低置信项里 kind=bib_line 的条数",
          d3["parse_fail"] == 2, d3["parse_fail"])


# ── ② 拿不到的计数写「未测」 ──────────────────────────────────────────────


def test_unmeasured() -> None:
    d = coop.build_delivery_summary(bib_keys=None, cited_keys=["A"])
    rec = coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir="/tmp/final", no_ai_merge=True, compiled="未请求 --compile",
        delivery=d)
    check("②-1 清单计数拿不到 → 写「未测」+ 原因",
          "`未测`" in rec and "源稿文末清单：`未测`" in rec, rec[:400])
    check("②-2 库未比对 → 库命中/库未命中都写「未测」",
          "引用库命中：`未测`（本次未比对编辑部引用库）" in rec
          and "库未命中：`未测`（本次未比对编辑部引用库）" in rec)
    d2 = coop.build_delivery_summary(
        bib_keys=["A"], cited_keys=["A"], bibdb_checked=False,
        bibdb_safe=5, bibdb_missed=1)
    rec2 = coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir="/tmp/final", no_ai_merge=True, compiled="未请求 --compile",
        delivery=d2)
    check("②-3 bibdb_checked=False 时即使有数值也不报（不许假报）",
          "引用库命中：`未测`" in rec2 and "**5** 条（未调 AI）" not in rec2)


# ── ③ 渲染：各计数 / 库命中 / 明细截断 ──────────────────────────────────


def _render(delivery: dict | None, **over) -> str:
    kw = dict(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir="/tmp/final", no_ai_merge=True, compiled="未请求 --compile",
        delivery=delivery)
    kw.update(over)
    return coop.render_record(**kw)


def test_render() -> None:
    d = coop.build_delivery_summary(
        bib_keys=["A", "B", "C"], cited_keys=["A", "X"],
        bibdb_checked=True, bibdb_safe=2, bibdb_missed=1)
    rec = _render(d)
    check("③-1 节标题固定（不带中文序号，既有节编号不动）",
          coop.DELIVERY_TITLE in rec
          and "## 交付记录（清单/引用/打印/未打印）" in rec)
    check("③-2 四项主计数写对",
          "- 源稿文末清单：**3** 条" in rec
          and "- 正文引用键：**2** 个" in rec
          and "- 打印条数：**1** 条" in rec
          and "- 未打印：**2** 条（= 清单 − 打印）" in rec, rec)
    check("③-3 未打印原因分类计数写对",
          "未被正文链路引用：**2** 条" in rec
          and "正文引用但清单无对应条目：**1** 个键" in rec
          and "解析未识别：**0** 行" in rec
          and "库未命中：**1** 条" in rec)
    check("③-4 库命中 H（未调 AI）写对",
          "- 引用库命中：**2** 条（未调 AI）" in rec)
    check("③-5 未打印逐条明细（键名）",
          "`B`（未被正文引用）" in rec and "`C`（未被正文引用）" in rec)
    check("③-6 库 safe / AI 覆盖二选一口径一句",
          "两者**二选一**" in rec and "不调 AI" in rec and "reference.bib" in rec)


def test_detail_truncate() -> None:
    keys = [f"K{i:02d}" for i in range(25)]
    d = coop.build_delivery_summary(bib_keys=keys, cited_keys=[])
    rec = _render(d)
    check("③-7 未打印明细 ≤20 行，超出写「另有 n 条」",
          rec.count("（未被正文引用）") == coop.DELIVERY_DETAIL_LIMIT
          and "另有 5 条" in rec
          and f"共 {len(keys)} 条，显示前 {coop.DELIVERY_DETAIL_LIMIT} 条" in rec,
          f"detail={rec.count('（未被正文引用）')}")


# ── ④ 既有节逐字节不变 / 记录不落正文 / 默认无本节 ─────────────────────


def _strip_block(rec: str, block: list[str]) -> str | None:
    lines = rec.split("\n")
    n = len(block)
    for i in range(len(lines) - n + 1):
        if lines[i:i + n] == block:
            return "\n".join(lines[:i] + lines[i + n:])
    return None


def test_existing_sections_unchanged() -> None:
    # 构造「所有既有节都出现」的记录（一/二/三/四/五/六/七 全在）。
    parse_report = coop.ParseReport()
    parse_report.replies = {"K1": {"verdict": "confirmed"}}
    low_report = coop.LowConfReport()
    low_report.accepted = [{"id": "FN-0"}]
    analysis = coop.FulltextAnalysisReport()
    analysis.accepted = [{"id": "AN-1"}]
    low_items = [{"id": "FN-0", "kind": "footnote", "subkind": "mixed_manual",
                  "evidence": "张三 2020 论逻辑"}]
    base = dict(
        stem=_STEM, phase="done", parse_report=parse_report,
        low_report=low_report,
        merge_summary={"n_merged": 1, "n_skipped": 0, "rollback": "b.bak"},
        apply_summary={"n_applied": 1, "skipped": [], "applied": [], "rollback": ""},
        final_dir="/tmp/final", no_ai_merge=False, compiled="✅ x.pdf（1 KB）",
        backup="/tmp/final.coop-bak-1",
        analysis_report=analysis, low_items=low_items)
    rec_without = coop.render_record(**base)
    d = coop.build_delivery_summary(bib_keys=["A", "B"], cited_keys=["A"])
    rec_with = coop.render_record(**base, delivery=d)
    block = coop._delivery_lines(d)
    check("④-1 交付记录节确实插入了",
          coop.DELIVERY_TITLE in rec_with and coop.DELIVERY_TITLE not in rec_without)
    check("④-2 删掉交付记录节后，其余记录与无本节**逐字节相同**",
          _strip_block(rec_with, block) == rec_without)
    check("④-3 既有节标题一个都没改（六=全文分析、七=清单缺条）",
          "## 六、全文分析（附四，report-only）" in rec_with
          and "## 七、清单缺条" in rec_with)
    check("④-4 本节不含正文全文",
          _FAR not in rec_with and "开头一段正文" not in rec_with)


def test_default_no_section() -> None:
    rec = coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir="/tmp/final", no_ai_merge=True, compiled="未请求 --compile")
    rec_none = coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir="/tmp/final", no_ai_merge=True, compiled="未请求 --compile",
        delivery=None)
    check("④-5 delivery 缺省/None == 旧调用方：记录无本节且两次逐字节相同",
          coop.DELIVERY_TITLE not in rec and rec == rec_none)


# ── ⑤ CLI：ph1 写标记、ph2 出节、产物零变化、不落正文 ──────────────────


def test_cli(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "cli")
    c = _ph1(src, bibp, out)
    st = _state(out)
    check("⑤-1 ph1 退出 0 且状态有 delivery 标记（schema=1）",
          c.returncode == 0 and (st.get("delivery") or {}).get("schema")
          == coop.DELIVERY_SCHEMA, c.stderr[-300:])
    check("⑤-2 delivery 标记不进参数指纹（schema 仍 1）",
          st.get("schema") == 1 and "delivery" not in (st.get("params") or {}))
    sp = _stage(out)
    cp = os.path.join(tmp, "cli", "stage.bak")
    shutil.copytree(sp, cp)
    item = (st.get("low_confidence") or [{}])[0]
    reply = write(os.path.join(tmp, "cli", "reply.json"), json.dumps(
        {"id": item.get("id", ""), "verdict": "unresolved",
         "span": (item.get("evidence") or "")[:10]}, ensure_ascii=False))
    r1 = _ph2(src, out, reply)
    rec1 = read(os.path.join(_final(out), coop.RECORD_NAME))
    bib_sha1 = sha(os.path.join(_final(out), "reference.bib"))
    tex_sha1 = sha(os.path.join(_final(out), _STEM + ".tex"))
    check("⑤-3 ph2 退出 0 且记录含交付记录节", r1.returncode == 0
          and coop.DELIVERY_TITLE in rec1, (r1.stderr + r1.stdout)[-400:])
    check("⑤-4 CLI 记录计数与产物一致（清单 2 / 引用 1 / 打印 1 / 未打印 1）",
          "- 源稿文末清单：**2** 条" in rec1
          and "- 正文引用键：**1** 个" in rec1
          and "- 打印条数：**1** 条" in rec1
          and "- 未打印：**1** 条" in rec1)
    check("⑤-5 本次未比对引用库 → 库命中写「未测」",
          "引用库命中：`未测`" in rec1)
    check("⑤-6 记录不含正文全文（附三独有标记）", _FAR not in rec1)
    # 双跑：同一暂存目录重跑，产物与记录逐字节同（记录写盘不影响产物）
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(cp, sp)
    r2 = _ph2(src, out, reply)
    rec2 = read(os.path.join(_final(out), coop.RECORD_NAME))
    check("⑤-7 双跑幂等：reference.bib / .tex / 记录逐字节相同",
          r2.returncode == 0 and sha(os.path.join(_final(out), "reference.bib")) == bib_sha1
          and sha(os.path.join(_final(out), _STEM + ".tex")) == tex_sha1
          and rec2 == rec1, r2.stderr[-300:])


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b79_b131_")
    try:
        # 隔离：把 SLCN_NOTES 指到**没有 bib_db/ 的空目录**，确保全程不读真实引用库。
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)
        ENV["SLCN_NOTES"] = notes
        test_counts()
        test_unmeasured()
        test_render()
        test_detail_truncate()
        test_existing_sections_unchanged()
        test_default_no_section()
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项"
          + (f"，SKIP {len(SKIPS)} 项" if SKIPS else ""))
    for f in FAILS:
        print("  FAIL:", f)
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
