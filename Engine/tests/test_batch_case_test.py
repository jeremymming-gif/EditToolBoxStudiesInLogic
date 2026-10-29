#!/usr/bin/env python3.14
r"""批量实例测试驱动 `tools/batch_case_test.py` 的判据单测（Phase1，2026-10-18，case-lab）。

钉住：
① 判据分级：P0/P1/P2 的边界（rc / PDF / `!` 错误 / undefined / 缺字 / 确定性 / 零付费 /
   悬空引用 / 正文 0 引用 / 页数差（B-019 起一律 P1）/ 文献差（88-06 案例级降 P1）/
   引用差 / 占位符 / 节结构 / 未引用条目）；
② 已知缺口标注（`KNOWN`）**不 suppress**，只打标（B-019 起 PAGE_DIFF 条目只作说明、不进 P0）；
③ 解析器（`--verify-plan` 的「预计 AI：0 次」、xelatex 日志、一校 md、回读校验 md、bib 闭合）；
④ 零付费安全网（`sanitize_env` 清付费变量、`assert_zero_cost_args` 黑名单）；
⑤ 内置 manifest（期 90 十篇在前 → 期 87 子案例 1–9 在后、编号↔作者映射）；
⑥ 源文件自动发现（manifest 覆盖 / main.tex 优先 / Word 排除「作者信息/英文摘要」）；
⑦ 报告渲染 smoke（总表/明细/附录都出现该案例）；
⑧ manifest 附加源 `metadata`（--metadata 拼装 / 文件不存在安全回退 / `--no-metadata` A2-only 口径 /
   JSON manifest 字段解析 / report 附录显示）；
⑨ 全量基准（repo 扫描 / 失败聚类 dashboard / dashboard.csv / cases.csv / 预计耗时 / 88+89 manifest）；
⑩ O10 相似度性能（auto/fast/full/none 口径、阈值降级、86 期 manifest、CLI 开关）；
⑪ 工具健壮性（`slcn_*` 临时目录清理 / 图片型产物 sha note，tex/bib 优先）；
⑫ B-117（批71，2026-10-21）验收口径重构：默认 L1–L4 新口径（L1 源稿一致性判据
   `SRC_BIB_MISSING`/`SRC_CITE_MISSING`/`SRC_CITE_UNMATCHED`/`SRC_CITE_UNRESOLVED`/
   `SRC_LIST_UNPARSED`/`META_UNEXTRACTED`；L3 三分类归因只有软件缺陷计 P1、
   页数差/版式差降 P2 信息项）；`--legacy-final` 恢复旧口径
   （`PAGE_DIFF` P1 / `BIB_COUNT_DIFF` / `CITE_DIFF` / `SECTION_DIFF` / `PLACEHOLDER` 等）且逐项可复现。

只写临时目录；不读真实语料、不跑 xelatex、不联网、不读稿件库 —— `cd Engine` 下秒级可跑。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_batch_case_test.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import shutil
import types
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
check("默认清单 41 个案例（90:10 + 87:9 + 88:7 + 89:5 + 86:10）", len(cases) == 41)
check("清单顺序：90(10)→87(9)→88(7)→89(5)→86(10)",
      [c.issue for c in cases[:10]] == ["90"] * 10
      and [c.issue for c in cases[10:19]] == ["87"] * 9
      and [c.issue for c in cases[19:26]] == ["88"] * 7
      and [c.issue for c in cases[26:31]] == ["89"] * 5
      and [c.issue for c in cases[31:]] == ["86"] * 10)
_86 = {c.key: c for c in cases if c.issue == "86"}
check("86 期 manifest：10 篇映射（1..10 ↔ 稿件仓 01..10，4/5 LaTeX）",
      len(_86) == 10
      and _86["86-01"].author == "Fan_Yang" and _86["86-01"].author_dir == "1"
      and _86["86-05"].author == "Wei_Qiu" and _86["86-05"].source.endswith("qiudejun0724.tex")
      and _86["86-10"].author == "Min_Wang" and _86["86-10"].author_dir == "10"
      and _86["86-04"].source.endswith(".tex") and _86["86-05"].source.endswith(".tex")
      and _86["86-01"].manual_dir == "01 Fan_Yang")
check("86 期人工 bib：05/08 用作者名 bib，其余 reference.bib",
      {k: c.manual_bib for k, c in _86.items()}["86-05"] == "Wei_Qiu.bib"
      and {k: c.manual_bib for k, c in _86.items()}["86-08"] == "Min_Cai.bib"
      and {k: c.manual_bib for k, c in _86.items()}["86-01"] == "reference.bib")
check("期 90 默认工作区 = ~/Desktop/macCaseTest90（用户已迁移）",
      batch.DEFAULT_WORKSPACES["90"].name == "macCaseTest90")
check("期 88/89 源映射与工作区（抽查 3 篇）",
      {c.key: c.source for c in cases}["88-01"] == "original/CONSEQ2026FINAL.docx"
      and {c.key: c.source for c in cases}["89-07"].endswith("WANGYIYAN.tex")
      and {c.key: c.author for c in cases}["88-06"] == "Hao_Chen")
check("期 87 子案例 1–9 ↔ 作者映射",
      [c.author for c in cases if c.issue == "87"] ==
      ["Hai_Xing", "Juan_Du", "Feng_Ye", "Tao_Song", "Xiaomei_Tang",
       "Hai_Feng", "Jing_Ren", "Chen_Liang", "Jie_Chen"])
check("期 87 作者目录 = 1..9（子案例号，不是作者名）",
      [c.author_dir for c in cases if c.issue == "87"] == [str(i) for i in range(1, 10)])
check("期 87 人工 bib 文件名特例（sub5/sub8 用作者名）",
      {c.key: c.manual_bib for c in cases if c.issue == "87"}["87-5"] == "Xiaomei_Tang.bib"
      and {c.key: c.manual_bib for c in cases if c.issue == "87"}["87-8"] == "Chen_Liang.bib")
_m90 = {c.key: c.manual_dir for c in cases if c.issue == "90"}
check("期 90 人工目录 = 01..10 篇序号（作者名由稿件仓发现，不硬编码）",
      _m90["90-05"].startswith("05 ")
      and [_m90[f"90-{i:02d}"].split(" ", 1)[0] for i in range(1, 11)]
      == [f"{i:02d}" for i in range(1, 11)])
_meta90 = {c.key: c.metadata for c in cases if c.issue == "90" and c.metadata}


def _independent_meta90_03() -> str:
    """独立重算 90-03 附加源名（不调用被测发现函数；无稿库 → ""）。"""
    try:
        from slcn import paths as _paths
        root = Path(_paths.manuscripts_root())
    except Exception:
        return ""
    dirs = sorted(d for d in (root / batch.MANUAL_ROOTS["90"]).glob("03 *") if d.is_dir())
    if not dirs:
        return ""
    for tex in sorted((dirs[0] / "original").glob("**/*.tex")):
        if tex.name != "main.tex":
            return tex.name
    return ""


_expected_meta90 = _independent_meta90_03()
check("期 90 附加源：manifest = 独立重算（只 90-03、非 main.tex；无稿库则无）",
      _meta90 == ({"90-03": _expected_meta90} if _expected_meta90 else {})
      and all(not v.endswith("main.tex") for v in _meta90.values()))
check("默认清单：无稿件库时 90 篇回落占位名 Paper01..Paper10（不硬编码真实作者）",
      all(_m90[f"90-{i:02d}"].endswith(f"Paper{i:02d}") for i in range(1, 11))
      if not batch._discover_issue_papers("90") else True)

# ── B-266（修 b257 D-04）：附加源发现的独立期望源（不靠发现函数自证）──
# 旧断言 `metadata == _discover_meta_tex(...)` 在发现选错文件/环境无稿库时都空通过；
# 这里在假稿件库里放多份同名 main.tex 诱饵 + 多份候选 .tex，用字面期望断言选中哪一份，
# 并加 1 条负例证明独立期望能捕获「发现选错」（旧式自洽断言在该负例下空通过=旧代码红）。
def _fake_ms_root(base: Path, papers: dict[str, list[str]]) -> Path:
    issue = base / batch.MANUAL_ROOTS["90"]
    for dirname, rels in papers.items():
        for rel in rels:
            p = issue / dirname / "original" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("% fixture（非真实稿件）", encoding="utf-8")
    return base


def _discover_with_root(root: Path) -> str:
    """在假稿件库上调用发现：临时把 slcn.paths 指向假根（引擎缺失也可测）。"""
    import types as _types
    fake_slcn = _types.ModuleType("slcn")
    fake_paths = _types.ModuleType("slcn.paths")
    fake_paths.manuscripts_root = lambda: str(root)
    fake_slcn.paths = fake_paths
    saved = {k: sys.modules.get(k) for k in ("slcn", "slcn.paths")}
    sys.modules["slcn"] = fake_slcn
    sys.modules["slcn.paths"] = fake_paths
    try:
        return batch._discover_meta_tex("90", "03")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


with tempfile.TemporaryDirectory(prefix="slcn_meta_fixture_") as _td:
    _root_a = _fake_ms_root(Path(_td) / "a", {
        "03 Fixture Author": ["main.tex", "chapters/main.tex", "zzz/main.tex",
                              "appendix/zzz_last.tex", "appendix/aaa_first.tex"],
        "03 Other Author": ["other_paper.tex"],
    })
    (_root_a / batch.MANUAL_ROOTS["90"] / "03 Fixture Author" / "root_only.tex").write_text(
        "% original/ 之外不参与", encoding="utf-8")
    check("⑧b 附加源发现独立期望：多份同名 main.tex 全跳过 → 选中 appendix/aaa_first.tex",
          _discover_with_root(_root_a) == "aaa_first.tex")
    _root_b = _fake_ms_root(Path(_td) / "b", {
        "03 Fixture Author": ["main.tex"], "03 Other Author": ["other_paper.tex"]})
    check("⑧b 附加源发现独立期望：首篇无候选 → 空，不跨篇取 other_paper.tex",
          _discover_with_root(_root_b) == "")

    # 负例：注入「总选诱饵 main.tex」的错误发现；旧式自洽断言仍空通过，独立期望判红。
    _orig_discover = batch._discover_meta_tex
    batch._discover_meta_tex = lambda issue_code, no: "main.tex"
    try:
        _d3_poisoned = next(c for c in batch.default_cases() if c.key == "90-03")
        _old_style_ok = _d3_poisoned.metadata == batch._discover_meta_tex("90", "03")
        _indep_ok = _d3_poisoned.metadata == "aaa_first.tex"
    finally:
        batch._discover_meta_tex = _orig_discover
    check("⑧b 负例：发现选错 → 旧式自洽断言空通过(True)、独立期望判红(False)（旧代码应红）",
          _old_style_ok is True and _indep_ok is False)

# ─────────────────────────────────────────────────────────────────────────────
# 2) 分级：干净案例 / 各类 P0 / P1 / P2
# ─────────────────────────────────────────────────────────────────────────────
CLEAN_L1 = {
    "ok": True, "channel": "word",
    "cite_keys": ["A"], "cite_keys_n": 1, "cite_total": 1,
    "cite_body_missing": [], "cite_missing": [],
    "note_markers": 0, "note_citation_entries": 0,
    "ref_titles": ["T"], "ref_titles_n": 1, "ref_source": "test",
    "unparsed_ref_lines": 0,
    "meta_present": {"title": True}, "meta_placeholder": {},
    "meta_unextracted": [], "meta_present_n": 1, "meta_extracted_n": 1,
    "meta_rate": 1.0,
    "ref_coverage": {"total": 1, "covered": 1, "missing": [], "missing_n": 0},
}
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
                "cited": ["A"], "bib_keys": ["A"], "dangling": [], "unused": []},
    "tex": {"cite": 20, "section": 5, "placeholder_any": 0, "placeholder_unrecognized": 0,
            "footnote": 3},
    "bib": {"entries": 20, "must_missing": 0, "titles": ["T"]},
    "proof": {"hits": 3, "fixable": 0, "rules": {}},
    "convert": {"compile_ok": True},
    "l1": CLEAN_L1,
}
MAN = {
    "pages": 16, "bib_entries": 20, "cite": 20, "section": 5, "placeholders": 0,
    "footnote": 3, "cite_keys": ["A"], "bib_titles": ["T"],
    "bib_must": 0, "closure": {"n_unused": 0, "n_dangling": 0},
    "proof": {"hits": 2, "fixable": 0, "rules": {}},
}


def codes(m, man=MAN, legacy=False):
    return [(f.level, f.code) for f in batch.grade_case(m, man, legacy_final=legacy)]


def has(m, code, level="P0", man=MAN, legacy=False):
    return (level, code) in codes(m, man, legacy=legacy)


check("干净案例无 finding（新口径：L1 无缺条 / L3 无软件缺陷）",
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
check("新口径：页数差 1/2/5 → P2 信息项 PAGE_DIFF（B-117 不再 P1）",
      has(dict(CLEAN, pdf_pages=17), "PAGE_DIFF", "P2")
      and has(dict(CLEAN, pdf_pages=18), "PAGE_DIFF", "P2")
      and has(dict(CLEAN, pdf_pages=21), "PAGE_DIFF", "P2")
      and not has(dict(CLEAN, pdf_pages=21), "PAGE_DIFF", "P1")
      and batch.PAGE_DIFF_INFO_LEVEL == "P2")
check("legacy：页数差 1/2/5 → 一律 P1 PAGE_DIFF（批70 旧口径可复现）",
      has(dict(CLEAN, pdf_pages=17), "PAGE_DIFF", "P1", legacy=True)
      and has(dict(CLEAN, pdf_pages=21), "PAGE_DIFF", "P1", legacy=True)
      and batch.PAGE_DIFF_LEVEL == "P1")
check("新口径：L1 源清单覆盖取代「BIB_COUNT vs 最终版」（不按人工条数判）",
      not has(dict(CLEAN, bib={"entries": 23, "must_missing": 0}), "BIB_COUNT_DIFF")
      and not any(c == "BIB_COUNT_DIFF" for _l, c in codes(dict(CLEAN, bib={"entries": 23}))))
check("legacy：文献差 1–2 → P1 / 差 3+ → P0（旧口径）",
      has(dict(CLEAN, bib={"entries": 21, "must_missing": 0}), "BIB_COUNT_DIFF", "P1",
          legacy=True)
      and has(dict(CLEAN, bib={"entries": 23, "must_missing": 0}), "BIB_COUNT_DIFF", "P0",
              legacy=True))
check("legacy：88-06 文献差 3 → P1 BIB_COUNT_DIFF（B-019 案例级降级保留）",
      has(dict(CLEAN, key="88-06", bib={"entries": 23, "must_missing": 0}),
          "BIB_COUNT_DIFF", "P1", legacy=True)
      and not has(dict(CLEAN, key="88-06", bib={"entries": 23, "must_missing": 0}),
                  "BIB_COUNT_DIFF", "P0", legacy=True)
      and batch.demote_to_p1("88-06", "BIB_COUNT_DIFF") is not None
      and batch.demote_to_p1("86-10", "BIB_COUNT_DIFF") is None)
check("legacy：正文引用数差 >10% → P1 CITE_DIFF（旧口径）",
      has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 16}), "CITE_DIFF", "P1", legacy=True))
check("新口径：CITE_DIFF 不再按人工数判（改由 L3 脚注转引用/信息项归因）",
      not has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 16}), "CITE_DIFF")
      and has(dict(CLEAN, tex={**CLEAN["tex"], "cite": 40, "footnote": 14}),
              "FOOTNOTE_CITE_GAP", "P1",
              man={**MAN, "cite": 49, "footnote": 7}))
check("legacy：占位符 >人工 → P1 PLACEHOLDER（旧口径）",
      has(dict(CLEAN, tex={**CLEAN["tex"], "placeholder_any": 10}), "PLACEHOLDER", "P1",
          legacy=True))
check("新口径：源稿有元数据却仍占位 → P1 META_UNEXTRACTED；否则只报告",
      has(dict(CLEAN, l1={**CLEAN_L1, "meta_unextracted": ["author"], "meta_rate": 0.5}),
          "META_UNEXTRACTED", "P1")
      and not has(dict(CLEAN, tex={**CLEAN["tex"], "placeholder_any": 10}), "PLACEHOLDER"))
check("新口径：节结构差 → P2 信息项（版式差）/ legacy 旧 P1 SECTION_DIFF",
      has(dict(CLEAN, tex={**CLEAN["tex"], "section": 4}), "SECTION_DIFF", "P2")
      and not has(dict(CLEAN, tex={**CLEAN["tex"], "section": 4}), "SECTION_DIFF", "P1")
      and has(dict(CLEAN, tex={**CLEAN["tex"], "section": 4}), "SECTION_DIFF", "P1",
              legacy=True))
check("legacy：未引用比人工多 >5 → P1 UNUSED_BIB_EXCESS / 1–5 → P2（旧口径）",
      has(dict(CLEAN, closure={**CLEAN["closure"], "n_unused": 8}), "UNUSED_BIB_EXCESS", "P1",
          legacy=True)
      and has(dict(CLEAN, closure={**CLEAN["closure"], "n_unused": 3}), "UNUSED_BIB_MINOR", "P2",
              legacy=True))
check("一校可自动改残留 → P1 PROOF_FIXABLE",
      has(dict(CLEAN, proof={"hits": 5, "fixable": 2, "rules": {}}), "PROOF_FIXABLE", "P1"))
check("ZH-08 比人工多 → P2 ZH08_HYPHEN",
      has(dict(CLEAN, proof={"hits": 5, "fixable": 0, "rules": {"ZH-08": 9}}),
          "ZH08_HYPHEN", "P2",
          man={**MAN, "proof": {"hits": 2, "fixable": 0, "rules": {"ZH-08": 2}}}))
check("legacy：必补字段比人工多 >10 → P1 BIB_FIELDS（旧口径）",
      has(dict(CLEAN, bib={"entries": 20, "must_missing": 15}), "BIB_FIELDS", "P1",
          legacy=True))

# ── B-117 L1 源稿一致性判据（无人工基线）──
check("L1：源参考文献清单缺条 → P1 SRC_BIB_MISSING（缺哪条必报）",
      has(dict(CLEAN, l1={**CLEAN_L1, "ref_titles": ["T", "U"], "ref_titles_n": 2,
                          "ref_coverage": {"total": 2, "covered": 1,
                                           "missing": ["U"], "missing_n": 1}}),
          "SRC_BIB_MISSING", "P1")
      and "U" in str([f.detail for f in batch.grade_case(
          dict(CLEAN, l1={**CLEAN_L1, "ref_coverage": {"total": 2, "covered": 1,
                                                       "missing": ["U"], "missing_n": 1}}), MAN)]))
check("L1：源引用未覆盖 → P1 SRC_CITE_MISSING；未进正文引用命令 → P2 显式信息项",
      has(dict(CLEAN, l1={**CLEAN_L1, "cite_missing": ["B"]}), "SRC_CITE_MISSING", "P1")
      and has(dict(CLEAN, l1={**CLEAN_L1, "cite_body_missing": ["C"]}),
              "SRC_CITE_BODY_INFO", "P2"))
check("L1：源清单未识别行 → P2 SRC_LIST_UNPARSED（显式报告，不静默）",
      has(dict(CLEAN, l1={**CLEAN_L1, "unparsed_ref_lines": 3}), "SRC_LIST_UNPARSED", "P2"))
check("L1：源引用未匹配/指向未解析条目 → P2 显式信息项（未识别/需人工）",
      has(dict(CLEAN, l1={**CLEAN_L1, "unmatched_citations": ["（Beal Restall 2006）"]}),
          "SRC_CITE_UNMATCHED", "P2")
      and has(dict(CLEAN, l1={**CLEAN_L1, "cite_unresolved": ["ref01"]}),
              "SRC_CITE_UNRESOLVED", "P2"))
check("L1：解析失败不误判（显式 notes）",
      batch.grade_case(dict(CLEAN, l1={"ok": False, "error": "boom"}), MAN) == [])

# ── B-117 L3 三分类归因（只有软件缺陷计 P1；页数/版式降 P2）──
_rows = batch.l3_attribution(dict(CLEAN, pdf_pages=18), MAN)
check("L3：页数差 → 人工后期修改 / P2 信息项",
      _rows and _rows[0]["cls"] == batch.L3_MANUAL and _rows[0]["level"] == "P2")
check("L3：源清单全覆盖而最终人工版多 1 条 → 人工后期修改（作者补充）",
      any(r["cls"] == batch.L3_MANUAL and r["code"] == "BIB_COUNT_INFO"
          for r in batch.l3_attribution(
              dict(CLEAN, l1={**CLEAN_L1, "ref_titles_n": 20}),
              {**MAN, "bib_entries": 21, "bib_titles": ["T", "Li2015"]})))
check("L3：CJK 译名 bib key 差异只报告（转 backlog B-120，不判 P1）",
      any(r["code"] == "BIB_KEY_INFO" and r["level"] is None
          for r in batch.l3_attribution(
              dict(CLEAN, closure={**CLEAN["closure"], "cited": ["Ai1979"]}),
              {**MAN, "cite_keys": ["Einstein1979"]}))
      and not has(dict(CLEAN, closure={**CLEAN["closure"], "cited": ["Ai1979"]}),
                  "BIB_KEY_INFO", man={**MAN, "cite_keys": ["Einstein1979"]}))

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
check("B-117：known 87-9 页差 3 → 新口径 P2 信息项 / legacy 复现旧 P1",
      batch.is_known("87-9", "PAGE_DIFF") is not None
      and ("P2", "PAGE_DIFF") in codes(dict(CLEAN, key="87-9", pdf_pages=19))
      and ("P1", "PAGE_DIFF") in codes(dict(CLEAN, key="87-9", pdf_pages=19), legacy=True)
      and ("P0", "PAGE_DIFF") not in codes(dict(CLEAN, key="87-9", pdf_pages=19)))

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
check("bib_stats：条数/类型/必补字段/题名（L1 覆盖用）",
      bs["entries"] == 2 and bs["must_missing"] >= 1
      and bs["types"].get("article") == 1 and bs["dup_keys"] == []
      and bs["titles"] == ["T", "U"])
check("B-117：题名归一化覆盖（L1.3）+ 占位符按类型枚举（L1.5）",
      batch.norm_title("Alpha  {Title}.") == batch.norm_title("alpha title")
      and batch.title_coverage(["Alpha Title", "Beta Book"], ["alpha title"])["missing_n"] == 1
      and batch.placeholder_types("【未识别：第一作者姓名，请手工填写】"
                                  "【未识别：表题，请手工填写】【未识别：引文出处】")
      == {"作者元数据": 1, "图表题": 1, "引文出处": 1})

# ── B-257：题名归一化顺序（90-03 SRC_BIB_MISSING 误报根因）/ 弯引号 / subset ──
check("B-257：norm_title 渲染形态等价（命令包裹版次/花括号/弯引号/CJK/版次）",
      batch.norm_title("逻辑悖论研究引论\\textup{（修订版）}")
      == batch.norm_title("逻辑悖论研究引论（修订版）")
      and batch.norm_title("维特根斯坦论“摩尔悖论”") == batch.norm_title("维特根斯坦论‘摩尔悖论’")
      and batch.norm_title("循环的“真”") == batch.norm_title("循环的‘真’")
      and batch.norm_title("{Principles of Neural Science}") == "principlesofneuralscience"
      and batch.norm_title("Topology, (2nd)") == batch.norm_title("Topology（第二版）")
      and batch.norm_title("An Introduction to Decision Theory, (Second edition)")
      == batch.norm_title("An Introduction to Decision Theory")
      and batch.norm_title("逻辑悖论研究引论") != batch.norm_title("逻辑悖论研究导论")
      and batch.norm_title("Topology") != batch.norm_title("Topology and Algebra"))
check("B-257：90-03 三组实测题名 title_coverage missing_n=0；负例仍缺 1",
      batch.title_coverage(["逻辑悖论研究引论\\textup{（修订版）}",
                            "维特根斯坦论“摩尔悖论”", "循环的“真”"],
                           ["逻辑悖论研究引论（修订版）", "维特根斯坦论‘摩尔悖论’",
                            "循环的‘真’"])["missing_n"] == 0
      and batch.title_coverage(["逻辑悖论研究引论"], ["逻辑悖论研究导论"])["missing_n"] == 1)

# B-257：--compare-subset（判据唯一实现在 tools/case_compare.compare_run）
with tempfile.TemporaryDirectory(prefix="b257_subset_unit_") as _td:
    import contextlib as _ctx
    import io as _io
    _tdp = Path(_td)

    def _ov_case() -> dict:
        return {"findings": [], "l1": {"ok": True, "cite_missing": [],
                                       "ref_coverage": {"missing_n": 0}}}

    _base = _tdp / "baseline.json"
    _base.write_text(json.dumps({"cases": {"90-03": _ov_case(), "87-1": _ov_case()}}),
                     encoding="utf-8")
    _cur = {"cases": {"90-03": _ov_case()}}
    _sub = batch._compare_run_impl(_cur, _base, _tdp / "subset", "unit-subset", subset=True)
    _def = batch._compare_run_impl(_cur, _base, _tdp / "default", "unit-default")
    _err = _io.StringIO()
    with _ctx.redirect_stderr(_err):
        _missing_rc = batch.main(["--compare-subset"])
    check("B-257：--compare-subset 正反例（skipped 不判违规/默认零回归/缺 --compare rc=2）",
          _sub["violations"] == [] and _sub["skipped_cases"] == ["87-1"]
          and _sub["summary_line"] == "COMPARE violations=0 allowed_changes=0 skipped=1"
          and "subset" not in _def and "skipped_cases" not in _def
          and len(_def["violations"]) == 1
          and _def["violations"][0]["category"] == "case_removed"
          and _missing_rc == 2 and "--compare" in _err.getvalue())

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

    mk("Xiaoyu_Lin/示例倾向性.docx", 5000)
    mk("Lei_Li/李雷 当代逻辑论题鸟瞰.docx", 9000)
    mk("Lei_Li/当代逻辑论题鸟瞰英文摘要.docx", 300)
    mk("Xiaoqing_Su/自我确认与示例性.docx", 8000)
    mk("Xiaoqing_Su/作者信息.docx", 200)
    mk("Jie_Zhou/main.tex", 500)
    mk("Jie_Zhou/子文件.tex", 9000)
    mk("Jie_Zhou/conditionals.bib", 100)
    mk("Empty_Dir/说明.txt", 10)

    def spec(key, author, author_dir, source="", manual_dir=""):
        return batch.CaseSpec(key=key, issue="t", author=author, author_dir=author_dir,
                              manual_dir=manual_dir, source=source)

    src, note = batch.discover_source(spec("t-1", "Lei_Li", "Lei_Li"), TMP)
    check("自动发现：Word 排除「英文摘要」第二源",
          src is not None and src.name == "李雷 当代逻辑论题鸟瞰.docx")
    src, note = batch.discover_source(spec("t-2", "Xiaoqing_Su", "Xiaoqing_Su"), TMP)
    check("自动发现：Word 排除「作者信息」辅助文件",
          src is not None and src.name == "自我确认与示例性.docx")
    src, note = batch.discover_source(spec("t-3", "Jie_Zhou", "Jie_Zhou"), TMP)
    check("自动发现：LaTeX 优先 main.tex（不是最大的子文件）",
          src is not None and src.name == "main.tex")
    src, _ = batch.discover_source(spec("t-4", "Ghost", "Ghost"), TMP)
    check("自动发现：作者目录不存在 → None", src is None)
    src, _ = batch.discover_source(
        spec("t-5", "Xiaoyu_Lin", "Xiaoyu_Lin", source="Xiaoyu_Lin/示例倾向性.docx"), TMP)
    check("manifest.source 覆盖自动发现", src is not None and src.name == "示例倾向性.docx")
    src, _ = batch.discover_source(
        spec("t-6", "Xiaoyu_Lin", "Xiaoyu_Lin", source="Xiaoyu_Lin/nope.docx"), TMP)
    check("manifest.source 不存在 → None（不静默换文件）", src is None)

    # .docx 不需要 LibreOffice；.doc 在没有 soffice 时才尝试（本机有也不在本测试里真转）
    docx_p, info = batch.ensure_docx(mk("Xiaoyu_Lin/a.docx"), TMP / "work", TMP / "logs",
                                     dict(os.environ))
    check("ensure_docx：.docx 原样返回（needed=False）",
          info["needed"] is False and info["ok"] is True and docx_p is not None)

    # ── ⑨ 全量基准：repo 扫描 / 聚类 / CSV / 估计 / 绝对 source ──
    check("_parse_issue_code 提取 issue code",
          batch._parse_issue_code("2026年第2期(87)") == "87"
          and batch._parse_issue_code("2022年第6A期(67A)") == "67A"
          and batch._parse_issue_code("abc") == "")
    check("输出名 sanitize：空格/非法字符/首位数字",
          batch.sanitize_output_name("Jie Rong") == "Jie_Rong"
          and batch.sanitize_output_name("Fan Yang") == "Fan_Yang"
          and batch.sanitize_output_name("Lei_Li") == "Lei_Li"
          and batch.sanitize_output_name("1st Case") == "A_1st_Case"
          and batch.sanitize_output_name("") == "Case")
    check("sha_verdict：成功才判 True/False，失败= None（不误判 NONDETERMINISTIC）",
          batch.sha_verdict(0, True, True, {"t": "a"}, {"t": "a"}) is True
          and batch.sha_verdict(0, True, True, {"t": "a"}, {"t": "b"}) is False
          and batch.sha_verdict(2, False, False, {}, {}) is None)
    _prod = TMP / "prod_media"
    _prod.mkdir(parents=True, exist_ok=True)
    (_prod / "slcn_wmf_abc").mkdir(exist_ok=True)
    (_prod / "slcn_wmf_abc" / "x.pdf").write_bytes(b"x")
    (_prod / "fig1.pdf").write_bytes(b"fig")
    (_prod / "Case.pdf").write_bytes(b"main")
    _removed = batch.clean_slcn_temp_dirs(_prod)
    _mm1 = batch.media_sha_map(_prod, "Case")
    (_prod / "fig1.pdf").write_bytes(b"fig2")
    _mm2 = batch.media_sha_map(_prod, "Case")
    check("工具：清理 slcn_* 临时目录 + 图片 sha 映射（主 PDF/slcn_* 排除）",
          _removed == ["slcn_wmf_abc"] and not (_prod / "slcn_wmf_abc").exists()
          and "fig1.pdf" in _mm1 and "Case.pdf" not in _mm1
          and batch.media_sha_diff(_mm1, _mm2) == ["fig1.pdf"])
    check("plan_ai_status：预计 AI=0→True、非0→False、plan 失败→None（不算违规）",
          batch.plan_ai_status(0, 0) is True
          and batch.plan_ai_status(0, 3) is False
          and batch.plan_ai_status(2, None) is None
          and batch.plan_ai_status(0, None) is None)
    _out = batch.convert_common_args(
        batch.CaseSpec(key="n", issue="repo", author="Jie Rong", author_dir="."),
        docx_p, TMP / "out", TMP)[0]
    check("convert_common_args：--name 用 sanitize 输出名",
          _out[_out.index("--name") + 1] == "Jie_Rong")

    # 合成「稿件仓」：不依赖引擎 identify 的内部判据（monkeypatch 成固定返回）
    repo = TMP / "repo"
    art = repo / "2020年第1期(60)" / "01 Alice"
    (art / "original").mkdir(parents=True)
    (art / "Alice.tex").write_text("manual", encoding="utf-8")
    fake_src = art / "original" / "Alice_source.docx"
    fake_src.write_text("src", encoding="utf-8")
    (repo / "2020年第1期(60)" / "02 Bob").mkdir(parents=True)
    (repo / "2020年第1期(60)" / "02 Bob" / "Bob.tex").write_text("x", encoding="utf-8")
    carol = repo / "2020年第1期(60)" / "03 Carol"
    carol.mkdir(parents=True)
    (carol / "Carol.tex").write_text("x", encoding="utf-8")
    (repo / "2020年第1期(60)" / "04 NoManual").mkdir(parents=True)
    (repo / "2026年第5期(90)" / "01 Skip").mkdir(parents=True)
    (repo / "2026年第5期(90)" / "01 Skip" / "Skip.tex").write_text("x", encoding="utf-8")

    _orig_identify = batch._sources.identify

    def _fake_identify(article_dir):
        p = Path(article_dir)
        if p.name == "01 Alice":
            return types.SimpleNamespace(body=types.SimpleNamespace(path=str(fake_src)))
        if p.name == "03 Carol":   # 识别的正文源 = 人工成品自身 → 必须跳过
            return types.SimpleNamespace(body=types.SimpleNamespace(path=str(p / "Carol.tex")))
        return types.SimpleNamespace(body=None)

    batch._sources.identify = _fake_identify
    try:
        repo_cases, repo_stats = batch.scan_repo_cases(repo)
    finally:
        batch._sources.identify = _orig_identify
    check("repo 扫描：可测 1 篇（60-01），Desktop 已覆盖期被跳过",
          [c.key for c in repo_cases] == ["repo-60-01"]
          and repo_cases[0].workspace == str(art.resolve())
          and repo_cases[0].manual_dir.endswith("01 Alice")
          and repo_cases[0].manual_root == str(repo.resolve())
          and repo_cases[0].source == str(fake_src.resolve())
          and repo_stats["skipped_no_manual_tex"] == 1
          and repo_stats["skipped_manual_is_source"] == 1
          and repo_stats["skipped_no_source"] == 1)

    # 人工 bib 兜底：目录里只有 rongliwu.bib 时也能找到
    art_bib = repo / "2020年第1期(60)" / "05 BibOnly"
    art_bib.mkdir(parents=True)
    (art_bib / "BibOnly.tex").write_text("manual", encoding="utf-8")
    (art_bib / "rongliwu.bib").write_text("@book{a, title={T}}", encoding="utf-8")
    fake_src2 = art_bib / "BibOnly_source.docx"
    fake_src2.write_text("src", encoding="utf-8")
    _orig2 = batch._sources.identify

    def _fake2(article_dir):
        p = Path(article_dir)
        if p.name == "05 BibOnly":
            return types.SimpleNamespace(body=types.SimpleNamespace(path=str(fake_src2)))
        return _orig2(article_dir)

    batch._sources.identify = _fake2
    try:
        cases_bib, _st = batch.scan_repo_cases(repo)
    finally:
        batch._sources.identify = _orig2
    bib_case = [c for c in cases_bib if c.author == "BibOnly"]
    check("repo 扫描：人工 bib 兜底目录内最大 .bib",
          len(bib_case) == 1 and bib_case[0].manual_bib == "rongliwu.bib")

    est = batch.estimate_bench(repo_cases + [batch.CaseSpec(
        key="w", issue="repo", author="W", author_dir=".", source="/tmp/a.docx")])
    check("estimate_bench：篇数/通道/预计耗时",
          est["cases"] == 2 and est["by_channel"]["word"] == 2
          and est["est_seconds"] == round(2 * batch.BENCH_EST_SEC_PER_CASE, 1))
    check("error_profile：rc≠0 / 编译错误 / 缺字 / 干净",
          batch.error_profile({"convert_rc": 2, "log": {"errors": ["Undefined control sequence."]}})["code"] == "E_CONVERT_RC"
          and batch.error_profile({"convert_rc": 0, "log": {"errors": ["x"], "n_errors": 1}})["code"] == "E_TEX"
          and batch.error_profile({"convert_rc": 0, "log": {"missing_char": 2,
                                                           "missing_char_samples": ["U+FF1D"]}})["code"] == "E_MISSING_CHAR"
          and batch.error_profile({"convert_rc": 0, "log": {}})["code"] == "")
    fake_run = {"case_order": ["a", "b"],
                "cases": {"a": {"key": "a", "author": "A", "issue": "repo", "channel": "word",
                                "convert_rc": 2, "pdf_pages": None, "level": "P0",
                                "log": {"errors": ["Undefined control sequence."], "n_errors": 1},
                                "findings": [{"level": "P0", "code": "CONVERT_RC", "detail": "rc=2"},
                                             {"level": "P1", "code": "PAGE_DIFF", "detail": "页差 1"}],
                                "manual": {"pages": 10}},
                          "b": {"key": "b", "author": "B", "issue": "repo", "channel": "latex",
                                "convert_rc": 0, "pdf_pages": 9, "level": "P1",
                                "log": {}, "tex": {"cite": 5}, "bib": {"entries": 3},
                                "manual": {"pages": 9, "bib_entries": 3, "cite": 5},
                                "findings": [{"level": "P1", "code": "PAGE_DIFF", "detail": "页差 1"}]}}}
    # ⑩ O10 相似度口径
    check("O10 similarity_choice：小稿 full / 大稿 fast / 显式覆盖",
          batch.similarity_choice("aa", "bb", "auto") == "full"
          and batch.similarity_choice("甲" * 20000, "乙" * 20000, "auto") == "fast"
          and batch.similarity_choice("aa", "bb", "full") == "full"
          and batch.similarity_choice("aa", "bb", "none") == "none"
          and batch.similarity_choice("aa", "bb", None) == "full")
    _sim_none = batch.compute_similarity("x", "x", "none")
    _sim_small = batch.compute_similarity("甲。\n乙。", "甲。\n乙。", "auto")
    _sim_large = batch.compute_similarity("a\n" * 20000, "a\n" * 20000, "auto")
    check("O10 compute_similarity：none=skipped / 小稿 full≈1 / 大稿 fast（精度标注）",
          _sim_none == {"mode": "skipped"}
          and _sim_small["mode"] == "full" and _sim_small["raw"] >= 0.99
          and _sim_large["mode"] == "fast" and _sim_large["raw"] >= 0.99
          and _sim_large.get("l2") is None)
    check("O10 compute_similarity：不同大稿 raw<1（可区分）",
          batch.compute_similarity("甲\n" * 20000, "乙\n" * 20000, "auto")["raw"] < 0.5)
    check("O12 分层验收：mid/full 档 + repo 扫描解析",
          batch.tier_issue_arg("mid") == "86,87,88,89,90"
          and batch.tier_repo_scan("mid", False) is False
          and batch.tier_repo_scan("mid", True) is True
          and batch.tier_repo_scan("full", False) is True)
    _c = {}
    _r1, _h1 = batch.similarity_cached("甲。", "甲。", "auto", _c)
    _r2, _h2 = batch.similarity_cached("甲。", "甲。", "auto", _c)
    _r3, _h3 = batch.similarity_cached("甲。乙。", "甲。", "auto", _c)
    check("O12 相似度缓存：按 (人工 sha, 生成 sha, 口径) 命中/失效",
          _h1 is False and _h2 is True and _h3 is False
          and _r2.get("mode") == _r1.get("mode")
          and batch.similarity_cache_key("m", "g", "auto") == batch.similarity_cache_key("m", "g", "auto")
          and batch.similarity_cache_key("m", "g", "auto") != batch.similarity_cache_key("m", "g", "full"))
    check("O12 CLI：--tier 默认 mid / full 可解析 / --no-similarity-cache",
          batch.build_parser().parse_args([]).tier == "mid"
          and batch.build_parser().parse_args(["--tier", "full"]).tier == "full"
          and batch.build_parser().parse_args(["--no-similarity-cache"]).no_similarity_cache is True)
    check("O10 CLI：--no-similarity / --full-similarity 互斥可解析",
          batch.build_parser().parse_args(["--no-similarity"]).no_similarity is True
          and batch.build_parser().parse_args(["--full-similarity"]).full_similarity is True
          and batch.build_parser().parse_args([]).no_similarity is False)
    check("B-117 CLI：--legacy-final 默认关/可解析；口径常量（旧 P1 仅 legacy 复现）",
          batch.build_parser().parse_args([]).legacy_final is False
          and batch.build_parser().parse_args(["--legacy-final"]).legacy_final is True
          and batch.ACCEPTANCE_VERSION.startswith("B-117")
          and batch.PAGE_DIFF_LEVEL == "P1" and batch.PAGE_DIFF_INFO_LEVEL == "P2"
          and batch.SECTION_DIFF_INFO_LEVEL == "P2")
    dash = batch.build_dashboard(fake_run)
    check("dashboard：按严重度加权排序 + owner/effort 元数据",
          [r["level"] for r in dash] == ["P0", "P1"]
          and dash[0]["cases_count"] == 1 and dash[1]["cases_count"] == 2
          and dash[0]["owner"] == "engine-core" and dash[1]["owner"] == "engine-core"
          and dash[0]["rank"] == 1)
    dpath = TMP / "dashboard.csv"; cpath = TMP / "cases.csv"
    batch.write_dashboard_csv(dpath, dash); batch.write_cases_csv(cpath, fake_run)
    check("dashboard.csv / cases.csv 写出且表头正确（含 sim_mode）",
          "cluster_code" in dpath.read_text(encoding="utf-8-sig").splitlines()[0]
          and "error_code" in cpath.read_text(encoding="utf-8-sig").splitlines()[0]
          and "sim_mode" in cpath.read_text(encoding="utf-8-sig").splitlines()[0]
          and len(dpath.read_text(encoding="utf-8-sig").splitlines()) == 3)
    check("绝对 source 的 discover_source 直接用",
          batch.discover_source(batch.CaseSpec(
              key="abs", issue="repo", author="A", author_dir=".",
              source=str(docx_p)), TMP)[0] == docx_p)
    check("CLI：--repo-scan / --inventory / --limit 可解析",
          batch.build_parser().parse_args(["--repo-scan"]).repo_scan is True
          and batch.build_parser().parse_args(["--inventory"]).inventory is True
          and batch.build_parser().parse_args(["--limit", "3"]).limit == 3)

    # ── B-221 A2：驱动解释器 ↔ Engine/vendor ABI 自检（注入探测；不启真实子进程）──
    import json as _json
    import contextlib as _ctx
    import io as _io

    def _dfake(file_name, version=(3, 14)):
        return lambda cmd, env: (0, _json.dumps(
            {"ok": True, "file": file_name, "version": list(version)}), "")

    check("B-221 驱动自检：vendor+ABI 匹配 → 通过 / ABI 不匹配 → 明确原因",
          batch.driver_interpreter_problem(
              "fake", runner=_dfake(str(batch.VENDOR / "lxml" / "etree.cpython-314-darwin.so"))) is None
          and "ABI 不匹配" in (batch.driver_interpreter_problem(
              "fake", runner=_dfake(str(batch.VENDOR / "lxml" / "etree.cpython-313-darwin.so"))) or ""))
    _orig_problem = batch.driver_interpreter_problem
    _orig_run = batch.run_batch
    batch.driver_interpreter_problem = lambda *a, **k: "ABI 不匹配（测试注入）"
    batch.run_batch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("自检失败时不应进入 run_batch"))
    try:
        _err = _io.StringIO()
        with _ctx.redirect_stderr(_err):
            _rc = batch.main([])
    finally:
        batch.driver_interpreter_problem = _orig_problem
        batch.run_batch = _orig_run
    check("B-221 main：驱动自检失败 → rc=2 fail-fast（未进入 run_batch）",
          _rc == 2 and "驱动自检" in _err.getvalue() and "ABI 不匹配" in _err.getvalue())

    # ── ⑧ manifest 附加源（metadata / --metadata / --no-metadata）──
    meta_p = mk("Jie_Zhou/example puzzle.tex", 400)
    main_p = mk("Jie_Zhou/main.tex", 600)

    def case_meta(**kw):
        base = dict(key="t-meta", issue="t", author="Jie_Zhou",
                    author_dir="Jie_Zhou", metadata="example puzzle.tex")
        base.update(kw)
        return batch.CaseSpec(**base)

    args, minfo = batch.convert_common_args(case_meta(), main_p, TMP / "out", TMP)
    check("附加源：文件存在 → 命令含 `--metadata <绝对路径>`",
          "--metadata" in args and args[args.index("--metadata") + 1] == str(meta_p)
          and minfo["found"] and minfo["declared"])
    check("附加源：相对路径按「工作区根 → 作者目录」解析到作者目录",
          minfo["path"] == str(meta_p))
    args0, minfo0 = batch.convert_common_args(
        case_meta(metadata=""), main_p, TMP / "out", TMP)
    check("附加源：字段为空 → 不传 --metadata（单源默认不变）",
          "--metadata" not in args0 and minfo0["declared"] is False)
    args_bad, minfo_bad = batch.convert_common_args(
        case_meta(metadata="nope/no-such.tex"), main_p, TMP / "out", TMP)
    check("附加源：文件不存在 → 安全回退（不传、fallback=True、note 含「回退」）",
          "--metadata" not in args_bad and minfo_bad["fallback"] is True
          and "回退" in minfo_bad["note"])
    args_abs, minfo_abs = batch.convert_common_args(
        case_meta(metadata=str(meta_p)), main_p, TMP / "out", TMP)
    check("附加源：绝对路径直接用",
          minfo_abs["path"] == str(meta_p) and "--metadata" in args_abs)
    # --no-metadata（A2-only 口径）
    args_nm, minfo_nm = batch.convert_common_args(
        case_meta(), main_p, TMP / "out", TMP, use_metadata=False)
    check("--no-metadata：不传 --metadata 且 info.disabled=True",
          "--metadata" not in args_nm and minfo_nm.get("disabled") is True)
    # extra_args 里已带 --metadata → 不重复追加
    args_dup, _ = batch.convert_common_args(
        case_meta(extra_args=("--metadata", "x.tex")), main_p, TMP / "out", TMP)
    check("extra_args 已带 --metadata → 不重复追加（只出现一次）",
          args_dup.count("--metadata") == 1)
    # --no-metadata 同时剔除 extra_args 里的 --metadata（含其值）
    args_nm2, _ = batch.convert_common_args(
        case_meta(extra_args=("--metadata", "x.tex", "--lang", "zh")),
        main_p, TMP / "out", TMP, use_metadata=False)
    check("--no-metadata 同时剔除 extra_args 的 --metadata 及值参数",
          "--metadata" not in args_nm2 and "x.tex" not in args_nm2 and "--lang" in args_nm2)
    # JSON manifest 字段解析
    man_json = TMP / "manifest.json"
    man_json.write_text(
        '{"cases": [{"key": "t-j", "issue": "t", "author": "A", "author_dir": "A", '
        '"manual_dir": "", "source": "", "metadata": "example puzzle.tex", '
        '"extra_args": []}]}', encoding="utf-8")
    loaded = batch.load_cases(types.SimpleNamespace(manifest=str(man_json), issues="", case=""))
    check("JSON manifest：metadata 字段被解析", len(loaded) == 1
          and loaded[0].metadata == "example puzzle.tex")
    check("CLI：--no-metadata 开关可解析（默认关）",
          batch.build_parser().parse_args([]).no_metadata is False
          and batch.build_parser().parse_args(["--no-metadata"]).no_metadata is True)

    # ── 6) 报告渲染 smoke（单案例，不跑任何子进程）
    run = {
        "run_id": "unit-test", "started": batch.now_iso(),
        "finished": batch.now_iso(), "out_dir": str(TMP / "out"),
        "engine_version": batch.ENGINE_VERSION, "git_head": "deadbeef",
        "python": sys.executable, "zero_cost": {"cleared": ["SLCN_LLM_KEY"]},
        "cases": {}, "case_order": ["t-1"],
    }
    m = {**CLEAN, "key": "t-1", "author": "Lei_Li", "channel": "word",
         "product_dir": "/tmp/x/t-1", "source": "/tmp/x/Lei_Li.docx",
         "source_note": "自动发现", "elapsed_s": 1.0, "level": "✅",
         "convert": {"compile_ok": True}, "findings": [],
         "metadata": {"declared": True, "found": True, "fallback": False,
                      "path": "/tmp/x/example puzzle.tex", "note": "附加源 --metadata"},
         "manual": {**MAN, "dir": "/tmp/man", "proof": {"hits": 2}}}
    m["sha_note"] = "tex/bib 相同，仅 2 个图片文件字节不同（测试）"
    run["cases"]["t-1"] = m
    batch.build_summary(run)
    run["zero_cost_unverified"] = ["t-1"]
    run["totals"]["zero_cost_unverified"] = ["t-1"]   # render_report 从 totals 读
    run["elapsed_total_s"] = 1.0
    run["throughput_per_min"] = 60.0
    run["dashboard"] = [{"rank": 1, "level": "P0", "cluster_code": "CONVERT_RC",
                         "cluster": "转换失败（rc≠0）", "owner": "engine-core",
                         "effort": "M", "cases_count": 1, "findings_count": 1,
                         "case_keys": "t-1", "signatures": "Undefined control sequence.",
                         "examples": "", "priority": 100}]
    report = batch.render_report(run)
    check("报告渲染：含标题/总表/附录/零付费段",
          all(s in report for s in ("批量实例测试报告", "总表", "每案例附录",
                                    "零付费", "t-1", "Lei_Li")))
    check("报告渲染：干净案例标 ✅",
          "**✅**" in report or "| ✅ |" in report)
    check("报告附录：显示附加源（--metadata）",
          "附加源（--metadata）" in report and "example puzzle.tex" in report)
    check("报告：零付费未验证案例单列（不误报违规）",
          "零付费断言未验证" in report)
    check("报告：图片型产物 sha note 可见 + totals.sha_notes 计数",
          "图片文件字节不同" in report and run["totals"].get("sha_notes") == 1)
    check("报告：全量基准看板与 Top10 根因段出现",
          "全量基准看板" in report and "前 10 根因" in report and "CONVERT_RC" in report)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

fi = batch.default_cases()[14]
check("默认清单第 15 个案例 = 87-5 Xiaomei_Tang（期 90 十篇之后）",
      fi.key == "87-5" and fi.author == "Xiaomei_Tang")

print()
print("=" * 66)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
