#!/usr/bin/env python3.14
r"""批量实例测试驱动 `tools/batch_case_test.py` 的判据单测（Phase1，2026-10-18，case-lab）。

钉住：
① 判据分级：P0/P1/P2 的边界（rc / PDF / `!` 错误 / undefined / 缺字 / 确定性 / 零付费 /
   悬空引用 / 正文 0 引用 / 页数差 / 文献差 / 引用差 / 占位符 / 节结构 / 未引用条目）；
② 已知缺口标注（`KNOWN`）**不 suppress**，只打标；
③ 解析器（`--verify-plan` 的「预计 AI：0 次」、xelatex 日志、一校 md、回读校验 md、bib 闭合）；
④ 零付费安全网（`sanitize_env` 清付费变量、`assert_zero_cost_args` 黑名单）；
⑤ 内置 manifest（期 90 十篇在前 → 期 87 子案例 1–9 在后、编号↔作者映射）；
⑥ 源文件自动发现（manifest 覆盖 / main.tex 优先 / Word 排除「作者信息/英文摘要」）；
⑦ 报告渲染 smoke（总表/明细/附录都出现该案例）。

只写临时目录；不读真实语料、不跑 xelatex、不联网、不读稿件库 —— `cd Engine` 下秒级可跑。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_batch_case_test.py
"""

import importlib.util
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.dont_write_bytecode = True  # 不在 tools/ 下留 __pycache__（写范围只有两个文件）

_HERE = Path(__file__).resolve().parent          # Engine/tests
_ENGINE = _HERE.parent                            # Engine
_ROOT = _ENGINE.parent                            # 公开仓根
_TOOL = _ROOT / "tools" / "batch_case_test.py"
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    if not cond:
        FAILS.append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


# ── 导入被测脚本（按文件路径；脚本自己在 import 时把 Engine/vendor 放进 sys.path）──
spec = importlib.util.spec_from_file_location("batch_case_test", _TOOL)
assert spec and spec.loader, f"找不到被测脚本：{_TOOL}"
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch  # dataclass 依赖 cls.__module__ 在 sys.modules 里
spec.loader.exec_module(batch)
print(f"已加载 {_TOOL}")

# ─────────────────────────────────────────────────────────────────────────────
# 1) 内置 manifest
# ─────────────────────────────────────────────────────────────────────────────
cases = batch.default_cases()
check("默认清单 19 个案例", len(cases) == 19)
check("期 90 十篇在前、期 87 九篇在后",
      [c.issue for c in cases[:10]] == ["90"] * 10
      and [c.issue for c in cases[10:]] == ["87"] * 9)
check("期 87 子案例 1–9 ↔ 作者映射",
      [c.author for c in cases if c.issue == "87"] ==
      ["Kun_Xing", "Wenjing_Du", "Fayang_Ye", "Pengfei_Song", "Ruixue_Tang",
       "Guangwu_Feng", "Ran_Ren", "Runcheng_Liang", "Wei_Chen"])
check("期 87 作者目录 = 1..9（子案例号，不是作者名）",
      [c.author_dir for c in cases if c.issue == "87"] == [str(i) for i in range(1, 10)])
check("期 87 人工 bib 文件名特例（sub5/sub8 用作者名）",
      {c.key: c.manual_bib for c in cases if c.issue == "87"}["87-5"] == "Ruixue_Tang.bib"
      and {c.key: c.manual_bib for c in cases if c.issue == "87"}["87-8"] == "Runcheng_Liang.bib")
check("期 90 人工目录 = 01..10 作者名",
      {c.key: c.manual_dir for c in cases if c.issue == "90"}["90-05"] == "05 Beibei_Gao")

# ─────────────────────────────────────────────────────────────────────────────
# 2) 分级：干净案例 / 各类 P0 / P1 / P2
# ─────────────────────────────────────────────────────────────────────────────
CLEAN = {
    "convert_rc": 0, "pdf_pages": 16,
    "log": {"errors": [], "n_errors": 0, "undefined_citation": 0,
            "undefined_reference": 0, "missing_char": 0, "overfull": 0,
            "underfull": 0, "log_pages": 16, "warning_cats": {}},
    "biber": {"errors": 0, "warnings": 0, "error_lines": []},
    "sha_ok": True, "sha_before": {"tex": "a", "bib": "b"},
    "sha_after": {"tex": "a", "bib": "b"},
    "plan": {"ai_zero": True, "ai": 0, "rc": 0},
    "closure": {"n_cited": 20, "n_bib": 20, "n_dangling": 0, "n_unused": 0,
                "dangling": [], "unused": []},
    "tex": {"cite": 20, "section": 5, "placeholder_any": 0, "placeholder_unrecognized": 0},
    "bib": {"entries": 20, "must_missing": 0},
    "proof": {"hits": 3, "fixable": 0, "rules": {}},
    "convert": {"compile_ok": True},
}
MAN = {
    "pages": 16, "bib_entries": 20, "cite": 20, "section": 5, "placeholders": 0,
    "bib_must": 0, "closure": {"n_unused": 0, "n_dangling": 0},
    "proof": {"hits": 2, "fixable": 0, "rules": {}},
}


def codes(m, man=MAN):
    return [(f.level, f.code) for f in batch.grade_case(m, man)]


def has(m, code, level="P0", man=MAN):
    return (level, code) in codes(m, man)


check("干净案例无 finding（生成 16 页/20 文献/20 引用/5 节 = 人工）",
      batch.grade_case(CLEAN, MAN) == [])
check("skipped 案例不判级", batch.grade_case({"skipped": "x"}, MAN) == [])

check("rc≠0 → P0 CONVERT_RC", has(dict(CLEAN, convert_rc=2), "CONVERT_RC"))
check("无 PDF → P0 NO_PDF", has(dict(CLEAN, pdf_pages=None), "NO_PDF"))
check("编译 ! 错误 → P0 TEX_ERROR",
      has(dict(CLEAN, log={**CLEAN["log"], "n_errors": 1, "errors": ["x"]}), "TEX_ERROR"))
check("undefined citation → P0 UNDEF_CITE",
      has(dict(CLEAN, log={**CLEAN["log"], "undefined_citation": 2}), "UNDEF_CITE"))
check("undefined reference → P0 UNDEF_REF",
      has(dict(CLEAN, log={**CLEAN["log"], "undefined_reference": 1}), "UNDEF_REF"))
check("Missing character → P0 MISSING_CHAR",
      has(dict(CLEAN, log={**CLEAN["log"], "missing_char": 52}), "MISSING_CHAR"))
check("biber ERROR → P0 BIBER_ERROR",
      has(dict(CLEAN, biber={"errors": 1, "warnings": 0, "error_lines": ["ERROR x"]}),
          "BIBER_ERROR"))
check("两遍 sha 不一致 → P0 NONDETERMINISTIC",
      has(dict(CLEAN, sha_ok=False), "NONDETERMINISTIC"))
check("零付费断言失败 → P0 ZERO_COST",
      has(dict(CLEAN, plan={"ai_zero": False, "ai": 3, "rc": 0}), "ZERO_COST"))
check("悬空引用 → P0 DANGLING_CITE",
      has(dict(CLEAN, closure={"n_cited": 21, "n_bib": 20, "n_dangling": 1,
                               "n_unused": 0, "dangling": ["X"], "unused": []}),
          "DANGLING_CITE"))
check("正文 0 引用但 bib>0 → P0 NO_CITES",
      has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 0}), "NO_CITES"))
check(".doc→docx 失败 → P0 DOC_CONVERT",
      has(dict(CLEAN, doc_convert={"needed": True, "ok": False, "error": "no soffice"}),
          "DOC_CONVERT"))
check("页数差 1 → P1 PAGE_DIFF / 差 2 → P0",
      has(dict(CLEAN, pdf_pages=17), "PAGE_DIFF", "P1")
      and has(dict(CLEAN, pdf_pages=18), "PAGE_DIFF", "P0"))
check("文献差 1–2 → P1 / 差 3+ → P0",
      has(dict(CLEAN, bib={"entries": 21, "must_missing": 0}), "BIB_COUNT_DIFF", "P1")
      and has(dict(CLEAN, bib={"entries": 23, "must_missing": 0}), "BIB_COUNT_DIFF", "P0"))
check("正文引用数差 >10% → P1 CITE_DIFF",
      has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 16}), "CITE_DIFF", "P1"))
check("正文引用差 ≤10% 不判",
      not has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 18}), "CITE_DIFF", "P1"))
check("占位符 >人工 → P1 PLACEHOLDER",
      has(dict(CLEAN, tex={**CLEAN["tex"], "placeholder_any": 10}), "PLACEHOLDER", "P1"))
check("节结构差 → P1 SECTION_DIFF",
      has(dict(CLEAN, tex={**CLEAN["tex"], "section": 4}), "SECTION_DIFF", "P1"))
check("未引用比人工多 >5 → P1 UNUSED_BIB_EXCESS",
      has(dict(CLEAN, closure={**CLEAN["closure"], "n_unused": 8}), "UNUSED_BIB_EXCESS", "P1"))
check("未引用比人工多 1–5 → P2 UNUSED_BIB_MINOR",
      has(dict(CLEAN, closure={**CLEAN["closure"], "n_unused": 3}), "UNUSED_BIB_MINOR", "P2"))
check("一校可自动改残留 → P1 PROOF_FIXABLE",
      has(dict(CLEAN, proof={"hits": 5, "fixable": 2, "rules": {}}), "PROOF_FIXABLE", "P1"))
check("ZH-08 比人工多 → P2 ZH08_HYPHEN",
      has(dict(CLEAN, proof={"hits": 5, "fixable": 0, "rules": {"ZH-08": 9}}),
          "ZH08_HYPHEN", "P2",
          man={**MAN, "proof": {"hits": 2, "fixable": 0, "rules": {"ZH-08": 2}}}))
check("必补字段比人工多 >10 → P1 BIB_FIELDS",
      has(dict(CLEAN, bib={"entries": 20, "must_missing": 15}), "BIB_FIELDS", "P1"))

# 分级汇总优先级
check("case_level：含 P0 即 P0", batch.case_level(
    [batch.Finding("P1", "X", ""), batch.Finding("P0", "Y", "")]) == "P0")
check("case_level：无 P0 含 P1 → P1", batch.case_level([batch.Finding("P2", "X", "")]) == "P2")
check("case_level：干净 → ✅", batch.case_level([]) == "✅")

# 已知缺口：标注但不 suppress
check("KNOWN 登记 87-5 NO_CITES（P0 仍会被判出）",
      batch.is_known("87-5", "NO_CITES") is not None
      and ("P0", "NO_CITES") in codes(dict(CLEAN, tex={**CLEAN["tex"], "cite": 0})))
check("KNOWN 没登记的案例不误标", batch.is_known("90-05", "NO_CITES") is None)

# ─────────────────────────────────────────────────────────────────────────────
# 3) 解析器
# ─────────────────────────────────────────────────────────────────────────────
check("parse_plan_ai：预计 AI：0 次 → (True, 0)",
      batch.parse_plan_ai("⑪·核实计划\n   预计检索：≤ 4 次查询\n"
                          "   预计 AI：0 次（未配置 LLM：只出任务书…）") == (True, 0))
check("parse_plan_ai：≤ N 次调用 → (False, N)",
      batch.parse_plan_ai("   预计 AI：≤ 3 次调用（每条一次请求）") == (False, 3))
check("parse_plan_ai：没有该行 → (False, None)", batch.parse_plan_ai("无") == (False, None))

_log = (
    "This is XeTeX\n"
    "! Undefined control sequence.\n"
    "LaTeX Warning: Citation 'a' on page 1 undefined on input line 5.\n"
    "LaTeX Warning: Reference 'b' on page 2 undefined on input line 9.\n"
    "Missing character: There is no ⊩ (U+22A9) in font X!\n"
    "Missing character: There is no 是 (U+662F) in font X!\n"
    "Overfull \\hbox (5.0pt too wide) in paragraph at lines 1--2\n"
    "Output written on X.pdf (14 pages).\n"
)
ls = batch.log_stats(_log)
check("log_stats：! 错误/undefined/缺字/页数",
      ls["n_errors"] == 1 and ls["undefined_citation"] == 1
      and ls["undefined_reference"] == 1 and ls["missing_char"] == 2
      and ls["log_pages"] == 14 and ls["overfull"] == 1)

pc = batch.parse_proofcheck_md(
    "| 规则 | 条数 | 说明 | 依据 |\n|---|---|---|---|\n"
    "| `ZH-06` | **1** | 顿号 | x |\n| `ZH-08` | **3** | 短杠 | y |\n\n"
    "**合计命中 4 处。**")
check("parse_proofcheck_md：规则分布 + 合计",
      pc == {"rules": {"ZH-06": 1, "ZH-08": 3}, "total": 4})
check("parse_proofcheck_console：命中/可自动改",
      batch.parse_proofcheck_console("完成。一校核查：命中 5 处（可自动改 2 处）｜…")
      == {"hits": 5, "fixable": 2})
check("parse_checkbib_md：问题/提示/条目数",
      batch.parse_checkbib_md("**结论：42 处问题 / 29 处提示** —— x（解析出 17 条条目）")
      == {"problems": 42, "hints": 29, "parse_entries": 17})

tc = batch.tex_stats(r"\documentclass{article}\begin{document}"
                     r"\section{A}\subsection{B}表\ref{x} "
                     r"\begin{tabular}{c}x\end{tabular} \parencite{k1} \cite{k2} "
                     r"【未识别：第一作者】\end{document}")
check("tex_stats：节/表格/引用/占位符",
      tc["section"] == 1 and tc["subsection"] == 1 and tc["tabular"] == 1
      and tc["cite"] == 2 and tc["placeholder_unrecognized"] == 1)

bib_txt = "@article{A,\n title={T},\n year={2020}\n}\n@book{B,\n title={U},\n publisher={P}\n}\n"
bs = batch.bib_stats(bib_txt)
check("bib_stats：条数/类型/必补字段", bs["entries"] == 2 and bs["must_missing"] >= 1
      and bs["types"].get("article") == 1 and bs["dup_keys"] == [])

clo = batch.citations_closure(r"\parencite{A} \parencite{B}", bib_txt)
check("citations_closure：引用键全部命中 → 无悬空",
      clo["n_cited"] == 2 and clo["n_bib"] == 2 and clo["dangling"] == []
      and clo["n_dangling"] == 0)
check("citations_closure：悬空引用（正文键不在 bib）",
      batch.citations_closure(r"\parencite{A} \parencite{C}", bib_txt)["dangling"] == ["C"])
check("citations_closure：未引用条目",
      batch.citations_closure(r"\parencite{A}", bib_txt)["unused"] == ["B"])

# ─────────────────────────────────────────────────────────────────────────────
# 4) 零付费安全网
# ─────────────────────────────────────────────────────────────────────────────
env, cleared = batch.sanitize_env({
    "SLCN_LLM_KEY": "x", "SLCN_SEARCH_KEY": "y", "SLCN_LLM_MODEL": "m",
    "OPENAI_API_KEY": "z", "HOME": "/tmp", "PYTHONPATH": "",
})
check("sanitize_env：清空付费变量、保留普通变量",
      "SLCN_LLM_KEY" not in env and "SLCN_SEARCH_KEY" not in env
      and "SLCN_LLM_MODEL" not in env and "OPENAI_API_KEY" not in env
      and env["HOME"] == "/tmp")
check("sanitize_env：cleared 列表完整", set(cleared) == {
    "SLCN_LLM_KEY", "SLCN_SEARCH_KEY", "SLCN_LLM_MODEL", "OPENAI_API_KEY"})
check("sanitize_env：PYTHONPATH 注入 Engine+vendor",
      str(batch.ENGINE) in env["PYTHONPATH"] and str(batch.VENDOR) in env["PYTHONPATH"])
check("assert_zero_cost_args：合法命令通过",
      batch.assert_zero_cost_args(
          ["python3.14", "convert.py", "--source", "x", "--verify-plan", "--search-refs"])
      == (True, []))
check("assert_zero_cost_args：拒绝 --verify-refs",
      batch.assert_zero_cost_args(["convert.py", "--verify-refs"]) == (False, ["--verify-refs"]))
check("assert_zero_cost_args：拒绝 --llm-key",
      batch.assert_zero_cost_args(["convert.py", "--llm-key", "k"]) == (False, ["--llm-key"]))

# ─────────────────────────────────────────────────────────────────────────────
# 5) 源文件自动发现（临时工作区；只写 /tmp）
# ─────────────────────────────────────────────────────────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="slcn_batchcase_"))
try:
    def mk(rel: str, size: int = 10) -> Path:
        p = TMP / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    mk("Beibei_Gao/推理倾向性.docx", 5000)
    mk("Bo_Chen/陈波 当代逻辑哲学鸟瞰.docx", 9000)
    mk("Bo_Chen/当代逻辑哲学鸟瞰英文摘要.docx", 300)
    mk("Yingying_Tang/自我确认与能动性.docx", 8000)
    mk("Yingying_Tang/作者信息.docx", 200)
    mk("Liang_Zhang/main.tex", 500)
    mk("Liang_Zhang/子文件.tex", 9000)
    mk("Liang_Zhang/conditionals.bib", 100)
    mk("Empty_Dir/说明.txt", 10)

    def spec(key, author, author_dir, source="", manual_dir=""):
        return batch.CaseSpec(key=key, issue="t", author=author, author_dir=author_dir,
                              manual_dir=manual_dir, source=source)

    src, note = batch.discover_source(spec("t-1", "Bo_Chen", "Bo_Chen"), TMP)
    check("自动发现：Word 排除「英文摘要」第二源",
          src is not None and src.name == "陈波 当代逻辑哲学鸟瞰.docx")
    src, note = batch.discover_source(spec("t-2", "Yingying_Tang", "Yingying_Tang"), TMP)
    check("自动发现：Word 排除「作者信息」辅助文件",
          src is not None and src.name == "自我确认与能动性.docx")
    src, note = batch.discover_source(spec("t-3", "Liang_Zhang", "Liang_Zhang"), TMP)
    check("自动发现：LaTeX 优先 main.tex（不是最大的子文件）",
          src is not None and src.name == "main.tex")
    src, _ = batch.discover_source(spec("t-4", "Ghost", "Ghost"), TMP)
    check("自动发现：作者目录不存在 → None", src is None)
    src, _ = batch.discover_source(
        spec("t-5", "Beibei_Gao", "Beibei_Gao", source="Beibei_Gao/推理倾向性.docx"), TMP)
    check("manifest.source 覆盖自动发现", src is not None and src.name == "推理倾向性.docx")
    src, _ = batch.discover_source(
        spec("t-6", "Beibei_Gao", "Beibei_Gao", source="Beibei_Gao/nope.docx"), TMP)
    check("manifest.source 不存在 → None（不静默换文件）", src is None)

    # .docx 不需要 LibreOffice；.doc 在没有 soffice 时才尝试（本机有也不在本测试里真转）
    docx_p, info = batch.ensure_docx(mk("Beibei_Gao/a.docx"), TMP / "work", TMP / "logs",
                                     dict(os.environ))
    check("ensure_docx：.docx 原样返回（needed=False）",
          info["needed"] is False and info["ok"] is True and docx_p is not None)

    # ── 6) 报告渲染 smoke（单案例，不跑任何子进程）
    run = {
        "run_id": "unit-test", "started": batch.now_iso(),
        "finished": batch.now_iso(), "out_dir": str(TMP / "out"),
        "engine_version": batch.ENGINE_VERSION, "git_head": "deadbeef",
        "python": sys.executable, "zero_cost": {"cleared": ["SLCN_LLM_KEY"]},
        "cases": {}, "case_order": ["t-1"],
    }
    m = {**CLEAN, "key": "t-1", "author": "Bo_Chen", "channel": "word",
         "product_dir": "/tmp/x/t-1", "source": "/tmp/x/Bo_Chen.docx",
         "source_note": "自动发现", "elapsed_s": 1.0, "level": "✅",
         "convert": {"compile_ok": True}, "findings": [],
         "manual": {**MAN, "dir": "/tmp/man", "proof": {"hits": 2}}}
    run["cases"]["t-1"] = m
    batch.build_summary(run)
    report = batch.render_report(run)
    check("报告渲染：含标题/总表/附录/零付费段",
          all(s in report for s in ("批量实例测试报告", "总表", "每案例附录",
                                    "零付费", "t-1", "Bo_Chen")))
    check("报告渲染：干净案例标 ✅",
          "**✅**" in report or "| ✅ |" in report)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

fi = batch.default_cases()[14]
check("默认清单第 15 个案例 = 87-5 Ruixue_Tang（期 90 十篇之后）",
      fi.key == "87-5" and fi.author == "Ruixue_Tang")

print()
print("=" * 66)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
