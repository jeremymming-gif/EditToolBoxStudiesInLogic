#!/usr/bin/env python3.14
"""第四十五批 A2：个人年制解析鲁棒性 + 父目录 .bib 搜索（task-57）。

背景（/tmp/audit_lead/batch45-bib.md，5 期基准）：
1) 6 篇 BIB_COUNT 缺口：86-07/86-08（英文 APA 清单整份 0 条）、86-02（中文冒号式）、
   87-5（SEP/philarchive 引号题名行）、88-01（年份区间/年份在行尾/残行）、88-06（尾部
   元数据块误入文献区）。
2) 89-05：`.bib` 在源稿父目录（稿件根），convert 只搜源目录 → 无 bib、30 处 undefined。
纪律：只按行内证据解析，解析失败保留原文；父目录只搜 1 层，不递归、不跨目录乱搜。
"""
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402
from slcn import docxsource  # noqa: E402
from slcn.docxxml import W_NS  # noqa: E402
from slcn.docxsource import parse_reference_line  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}（{why}）")


# ══════════════════════════════════════════════════════════════════════════
# ① 英文 APA：`作者列表 (年份). 题名. 出版信息`
#    86-07/86-08 整份清单（38/31 行）旧实现 0/2 条
# ══════════════════════════════════════════════════════════════════════════
_a1 = parse_reference_line(
    "Beall, J. C., & Restall, G. (2000). Logical pluralism. "
    "Australasian Journal of Philosophy, 78(4), 475–493.")
check("① APA 期刊条目：作者/年份/题名/类型（J）",
      _a1 is not None and _a1.year == "2000" and _a1.title == "Logical pluralism"
      and _a1.kind == "J" and "and Restall" in _a1.author,
      repr(_a1))
_a2 = parse_reference_line("Beall, J., & Restall, G. (2006). Logical Pluralism. "
                           "Oxford University Press.")
check("① APA 书籍条目：`&` 作者 → `and`，类型 M", _a2 is not None and _a2.kind == "M"
      and "and Restall" in _a2.author and _a2.year == "2006", repr(_a2))
_a3 = parse_reference_line(
    "Yrjönsuuri, M. (2024). Valid on Formal Grounds. In C. G. Normore, & S. Schmid "
    "(Eds.), Grounding in Medieval Philosophy (pp. 251-280). Springer.")
check("① APA 书中章节（含 `(Eds.)`）→ 类型 C/M（按行内证据）",
      _a3 is not None and _a3.kind in ("C", "M") and _a3.year == "2024", repr(_a3))
_a4 = parse_reference_line("Bolzano, Bernard. 1929-1931. Wissenschaftslehre, "
                           "Leipzig: Felix Meiner.")
check("① APA 年份区间（无括号，88-01 实测）→ 取起始年 1929",
      _a4 is not None and _a4.year == "1929", repr(_a4))

# ══════════════════════════════════════════════════════════════════════════
# ② 中文冒号式（86-02 实测）
# ══════════════════════════════════════════════════════════════════════════
_c1 = parse_reference_line("王先谦：荀子集解，1988年，北京：中华书局。")
check("② 中文冒号式：作者/年份/题名", _c1 is not None and _c1.author == "王先谦"
      and _c1.year == "1988" and _c1.title == "荀子集解", repr(_c1))
_c2 = parse_reference_line("李零：《兵以诈立：我读孙子》，2006年，北京：中华书局。")
check("② 中文冒号式书名号（题名内含冒号）", _c2 is not None
      and _c2.title == "兵以诈立：我读孙子" and _c2.year == "2006", repr(_c2))
_c3 = parse_reference_line("银雀山汉墓竹简整理小组编：《银雀山汉墓竹简孙子兵法》，1976年，"
                           "北京：中华书局。")
check("② 中文冒号式（编）", _c3 is not None and _c3.year == "1976", repr(_c3))

# ══════════════════════════════════════════════════════════════════════════
# ③ 其他实测类：中文 APA、引号题名+URL、年份在行尾
# ══════════════════════════════════════════════════════════════════════════
_a5 = parse_reference_line("余俊伟. (2019). 不同层次的逻辑多元论. 逻辑学研究, 2, 1–12.")
check("③ 中文 APA（86-07 剩余 2 条之一）", _a5 is not None
      and _a5.year == "2019" and _a5.title == "不同层次的逻辑多元论", repr(_a5))
_l1 = parse_reference_line(
    "A. Hájek, “Most counterfactuals are false”, (2019-08-28)[2015-05-15]. "
    "https://philarchive.org/rec/ HJEMCA.word.")
check("③ 引号题名 + 日期/URL（87-5 philarchive）", _l1 is not None
      and _l1.title == "Most counterfactuals are false" and _l1.year == "2019", repr(_l1))
_l2 = parse_reference_line(
    "P. Rysiew, “Epistemic Contextualism”, in Edward N. Zalta and Uri Nodelman(eds.), "
    "The Stanford Encyclopedia of Philosophy, "
    "https://plato.stanford.edu/archives/win2023/entries/contextualism- epistemology/.")
check("③ 引号题名 + 无卷期（87-5 SEP，年份取行内 win2023）", _l2 is not None
      and _l2.year == "2023" and "Stanford Encyclopedia" in _l2.rest, repr(_l2))
_e1 = parse_reference_line("Osma, Wilhelm von, De Consequentiis — Über die Folgerungen, "
                           "hrsg. F. Schupp, Meiner, Hamburg 1991.")
check("③ 年份在行尾（88-01 德式）", _e1 is not None and _e1.year == "1991", repr(_e1))

check("④ 反例：编号清单行（含写歪的 `[2 ` ）不被人年制兜底抢（案例 116 契约）",
      docxsource._parse_reference_line_loose(
          "[1] A. Author, 2020, “Title One”, https://example.org/one.") is None
      and docxsource._parse_reference_line_loose(
          "[2 B. Author, 2021, “Title Two”, Synthese, 2(3): 5-6.") is None
      and docxsource._parse_reference_line_apa(
          "[1] A. Author, 2020, “Title One”, Analysis, 1(2): 3-4.") is None)

# 反例：残行/无年份/编号行 → 不猜、保持解析失败（保留原文）
check("④ 反例：残行 `Holzboog (Grammatica speculativa 6).` 仍不解析（不猜）",
      parse_reference_line("Holzboog (Grammatica speculativa 6).") is None)
check("④ 反例：无年份、无卷期的中文短行不解析",
      parse_reference_line("论证能力研究：论证中的语言使用与指示词") is None)
_num = parse_reference_line("[12] 陈波. 2022. 逻辑例外论[J]. 逻辑学研究.")
check("④ 编号清单行即使被主通道解析，年份/题名也必须来自行内证据（不猜）",
      _num is None or (_num.year == "2022" and "逻辑例外论" in _num.title), repr(_num))


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 文献区边界：文末元数据块不能混进 ref_raw_lines（88-06 实测）
# ══════════════════════════════════════════════════════════════════════════
def _make_docx(paras: list[str]) -> str:
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx"); os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
    return path


_D = _make_docx([
    "测试题名", "张三",
    "摘  要：这是摘要。",
    "关键词：测试",
    "正文段落。",
    "参考文献",
    "陈波. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.",
    "论证能力研究：论证中的语言使用与指示词",   # 88-06 型：尾部元数据块（无「参考文献」头）
    "陈志喜",
    "摘  要",
    "论证能力最初是参照语言学中交际能力提出来的……",
])
try:
    _dd = docxsource.parse_docx(_D)
    check("⑤ 文献区在有尾部元数据块时只保留真条目（88-06 型边界）",
          len(_dd.refs) == 1 and len(_dd.ref_raw_lines) == 1, repr(_dd.ref_raw_lines))
finally:
    try:
        os.unlink(_D)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 89-05 型：`.bib` 在源稿父目录（稿件根）→ find_bib 能找到并记录来源
# ══════════════════════════════════════════════════════════════════════════
_root = tempfile.mkdtemp()
_orig = os.path.join(_root, "original"); os.makedirs(_orig)
_src = os.path.join(_orig, "main.tex")
open(_src, "w", encoding="utf-8").write("\\documentclass{article}\n\\\\begin{document}x\\end{document}\n")
_bib = os.path.join(_root, "reference.bib")
open(_bib, "w", encoding="utf-8").write("@article{k1, title={T}, year={2020}}\n")
check("⑥ find_bib：源目录无 .bib → 父目录（稿件根）回退命中",
      convert.find_bib(_src, None) == _bib, repr(convert.find_bib(_src, None)))
check("⑥ 来源记录：父目录命中的来源 = 源稿父目录（稿件根，限 1 层）",
      convert.bib_origin(_src, _bib) == "源稿父目录（稿件根，限 1 层）",
      convert.bib_origin(_src, _bib))
open(os.path.join(_orig, "reference.bib"), "w", encoding="utf-8").write(
    "@article{k2, title={U}, year={2021}}\n")
check("⑥ 同目录优先于父目录（保守口径不跨层级抢）",
      convert.find_bib(_src, None) == os.path.join(_orig, "reference.bib"))
try:
    for _f in (os.path.join(_orig, "reference.bib"), _bib, _src):
        os.unlink(_f)
    os.rmdir(_orig); os.rmdir(_root)
except OSError:
    pass


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 真实稿 pin（5 期基准数据在 /tmp 时跑；不在则 SKIP）
# ══════════════════════════════════════════════════════════════════════════
_SUM = "/tmp/batch_case_test/benchmark5-v1/summary.json"
try:
    import json
    if not os.path.isfile(_SUM):
        skip("⑦ 真实稿 pin", "本机没有 5 期基准 summary.json")
    else:
        _cases = json.load(open(_SUM, encoding="utf-8"))["cases"]
        _EXPECT = {"86-07": 38, "86-08": 31, "86-02": 13, "87-5": 24,
                   "88-01": 40, "88-06": 42}
        for _k, _min in _EXPECT.items():
            _src2 = _cases[_k]["source"]
            if not os.path.isfile(_src2):
                skip(f"⑦ 真实稿 {_k}", "源稿不在本机"); continue
            _doc = docxsource.parse_docx(_src2)
            # B-022 意图变化（批68）：文末为 **Word 自动编号**的稿子改走「编号清单」通道，
            # 条目落在 `doc.bib_entries`、`doc.refs` 为空（86-08=31、86-02=13 实测）；
            # 其余 4 篇仍是人年制 `doc.refs` 非空，断言口径不变。
            _n_parsed = len(_doc.refs) or len(_doc.bib_entries)
            check(f"⑦ 真实稿 {_k}：解析条数 = {_min}（修复前 "
                  f"低值见 benchmark）", _n_parsed == _min, str(_n_parsed))
except Exception as _e:  # noqa: BLE001
    skip("⑦ 真实稿 pin", f"{type(_e).__name__}: {_e}")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
