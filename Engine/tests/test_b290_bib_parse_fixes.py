#!/usr/bin/env python3.14
r"""B-290：参考文献解析四处修复（F3 中文括号编号 / F5 biber 严格化 / P0-1 年末多作者 / F1 括号年位置）。

背景（B-180 复现，合成夹具，不读稿件库、不跑 xelatex）：
  * F3 `_NUMBERED_PREFIX_RE` 只认 `[1]`/`（1）`/`(1)`/`1.`，不认 `〔1〕/【1】/［1］`
    → 这类编号清单行被人年制兜底当正文解析（前缀吃进 author），`_numbered_refs`
    一条都收不到（真实稿 78/03：18 条只剩 7 条带 `〔N〕` 前缀的残条目）。
  * F5 `compile_pdf` 对 biber 退出码无条件容忍（`and i != 2`）→ 坏 .bib 时 `.bbl`
    为 0 字节仍报「编译成功」，PDF 参考文献区全空。
  * P0-1 英文多作者 + 年末无括号年（`Kahneman D., Slovic P., & Tversky A. … 1982.`）
    → 按逗号切后只取第 1 作者、第 2 作者被当题名。
  * F1 `_APA_YEAR` 抓题名内部第一个括号年（`… a year (2000) inside. …`）→ 题名当作者。

钉住（本文件断言全部基于合成夹具 `tests/fixtures/b290/`，无真实个人信息）：
  ① C1 F3：五种括号清单各 3 条——单行解析器一律让路（None），编号清单通道 3 条全出、
     author 无前缀；五种括号解析结果一致。
  ② C2/C3：`〔〕/【】/［］` 合成 docx 夹具 vs ASCII `[N]` 对照夹具 → 条数/编号→键/
     字段/`entry_to_bib` 文本**逐字节**相同，且与 B-290 开工前（base `afff0b6`）的
     ASCII 产物钉住的字面量逐字节相同（ASCII 通道零变化）。
  ③ C4：坏 .bib（空键，`normalize_bib` 修不好）→ 仿真 biber 失败判 False 且 detail 带
     `.blg` 的 ERROR 行；无 cite（无 `.bcf`）照旧放行；`.bbl` 非空且无 ERROR 时容忍
     非零退出码；xelatex 真错仍判 False。
  ④ C6：Kahneman 年末段 → 三作者 + 题名/出版社归位；括号年对照行仍三作者。
  ⑤ C7：题名内 `(2000)` 不当年（`_parse_apa_item` 返回 None）；`(n.d.)` 行 year 空、
     题名含 `(2000)`；正常 `(2000)` 行不受影响。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" \
      /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/test_b290_bib_parse_fixes.py
"""

import re
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import convert_compile as _cc  # noqa: E402
from slcn.bibnorm import normalize_bib  # noqa: E402
from slcn.docxsource import parse_docx  # noqa: E402
from slcn.footnotes import entry_to_bib  # noqa: E402
from slcn.footref import _parse_apa_item, parse_numbered_item  # noqa: E402
from slcn.wordentry import _numbered_refs, parse_reference_line  # noqa: E402

FIX = _HERE / "fixtures" / "b290"
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


def _fixture_text(name: str) -> list[str]:
    return [ln for ln in (FIX / name).read_text(encoding="utf-8").splitlines() if ln.strip()]


# ② 的钉住字面量：B-290 开工前 `afff0b6` 引擎对**同内容 ASCII 夹具** `entry_to_bib`
# 产物的逐字节结果（sha256 5e31b6105af63d767e569faa16ce5786f78e3cd44dca88aacf5d8e1b4407e48a）。
# 修 F3 只加中文括号字符类，ASCII 清单通道必须一个字节不变。
_PINNED_ASCII_BIB = (
    "@book{Zhang2020,\n"
    "  author    = {张三},\n"
    "  title     = {逻辑的观念},\n"
    "  publisher = {商务印书馆},\n"
    "  address   = {北京},\n"
    "  year      = {2020},\n"
    "  userf     = {cn}\n"
    "}\n\n\n"
    "@book{Li2019,\n"
    "  author    = {李四},\n"
    "  title     = {逻辑哲学},\n"
    "  publisher = {北京大学出版社},\n"
    "  address   = {北京},\n"
    "  year      = {2019},\n"
    "  userf     = {cn}\n"
    "}\n\n\n"
    "@article{Wang2026,\n"
    "  author  = {王五},\n"
    "  title   = {论证的语用维度},\n"
    "  journal = {逻辑学研究},\n"
    "  number  = {6},\n"
    "  year    = {2026},\n"
    "  pages   = {1--10},\n"
    "  userf   = {cn}\n"
    "}\n"
)

# ══════════════════════════════════════════════════════════════════════════
print("\n── ① C1 F3：五种括号清单都被编号清单通道收下、author 无前缀 ──")
# ══════════════════════════════════════════════════════════════════════════
_BRACKETS = [("[", "]"), ("（", "）"), ("〔", "〕"), ("【", "】"), ("［", "］")]
_AUTHORS = ["赵甲", "钱乙", "孙丙"]
_by_style: dict[str, list] = {}
for _o, _c in _BRACKETS:
    _lines = [f"{_o}{i}{_c} {_au}，合成题名{i}，{2015 + i}年，北京：商务印书馆。"
              for i, _au in enumerate(_AUTHORS, 1)]
    _style = f"{_o}N{_c}"
    check(f"①「{_style}」单行解析器让路（否则编号通道收不到）",
          all(parse_reference_line(ln) is None for ln in _lines),
          str([parse_reference_line(ln) for ln in _lines]))
    _entries = _numbered_refs(_lines)[0]
    _by_style[_style] = [(k, d.get("author"), d.get("title"), d.get("year"))
                         for k, d, _raw in _entries]
    check(f"①「{_style}」3 条全解析", len(_entries) == 3, f"{len(_entries)} 条")
    check(f"①「{_style}」author 无标号前缀",
          [d.get("author") for _k, d, _r in _entries] == _AUTHORS,
          repr([d.get("author") for _k, d, _r in _entries]))
    check(f"①「{_style}」键不重复",
          len({k for k, _, _ in _entries}) == 3, repr([k for k, _, _ in _entries])) 
check("① 五种括号的解析结果完全一致（中文括号 == ASCII）",
      all(v == _by_style["[N]"] for v in _by_style.values()),
      repr({k: v for k, v in _by_style.items() if v != _by_style["[N]"]}))

# ══════════════════════════════════════════════════════════════════════════
print("\n── ② C2/C3：合成 docx —— 中文括号清单 == ASCII 对照（逐字节）──")
# ══════════════════════════════════════════════════════════════════════════
_doc_cn = parse_docx(str(FIX / "cn_bracket_numbered.docx"))
_doc_ascii = parse_docx(str(FIX / "cn_bracket_numbered_ascii.docx"))
check("② 中文括号 docx：编号通道 3 条（base 是 3 条带前缀残条目、通道 0 条）",
      len(_doc_cn.bib_entries) == 3 and not _doc_cn.refs,
      f"bib_entries={len(_doc_cn.bib_entries)} refs={len(_doc_cn.refs)}")
check("② ASCII 对照 docx：编号通道 3 条", len(_doc_ascii.bib_entries) == 3,
      f"{len(_doc_ascii.bib_entries)} 条")
check("② 两份夹具：编号→键映射一致",
      _doc_cn.ref_key_by_num == _doc_ascii.ref_key_by_num ==
      {1: "Zhang2020", 2: "Li2019", 3: "Wang2026"},
      repr(_doc_cn.ref_key_by_num))
check("② 两份夹具：author 都不是标号前缀（`〔1〕张三` 一类）",
      [e.get("author") for _k, e, _r in _doc_cn.bib_entries] == ["张三", "李四", "王五"],
      repr([e.get("author") for _k, e, _r in _doc_cn.bib_entries]))
_cn_bib = "\n\n".join(entry_to_bib(k, e) for k, e, _r in _doc_cn.bib_entries)
_ascii_bib = "\n\n".join(entry_to_bib(k, e) for k, e, _r in _doc_ascii.bib_entries)
check("③ C3：ASCII 对照夹具产物与 base `afff0b6` 钉住字面量逐字节相同",
      _ascii_bib == _PINNED_ASCII_BIB, repr(_ascii_bib[:120]))
check("② C2：中文括号夹具产物与 base ASCII 钉住字面量逐字节相同",
      _cn_bib == _PINNED_ASCII_BIB, repr(_cn_bib[:120]))

# ══════════════════════════════════════════════════════════════════════════
print("\n── ③ C4：坏 .bib 的 biber 失败不再被容忍（仿真，不跑 xelatex）──")
# ══════════════════════════════════════════════════════════════════════════
_bad_bib = (FIX / "bad_biber.bib").read_text(encoding="utf-8")
_norm, _notes = normalize_bib(_bad_bib, strip_urls=True)
check("③ 坏 bib 夹具的语法错 normalize_bib 修不掉（空键原样保留）",
      _norm.strip().startswith("@article{, "), repr(_norm))
check("③ 坏 bib 夹具不是合法条目键（`@type{key,`）",
      re.match(r"^@\w+\{\s*[^,\s]", _norm.strip()) is None, repr(_norm[:40]))


class _FakeRun:
    """替身 subprocess.run：按第几次调用造中间产物并返回指定退出码。"""

    def __init__(self, out_dir: Path, stem: str, spec: dict[int, tuple[int, dict]]):
        self.out_dir, self.stem, self.spec = out_dir, stem, spec
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(cmd))
        i = len(self.calls)
        rc, files = self.spec.get(i, (0, {}))
        for name, content in files.items():
            (self.out_dir / f"{self.stem}{name}").write_bytes(content)
        return _Proc(rc)


class _Proc:
    def __init__(self, rc: int):
        self.returncode = rc


def _run_compile(spec: dict[int, tuple[int, dict]]) -> tuple[bool, str, list[list[str]]]:
    """在临时目录里以替身 subprocess.run + which 跑一次 compile_pdf。"""
    old_run, old_which = _cc.subprocess.run, _cc.shutil.which
    with tempfile.TemporaryDirectory(prefix="b290_compile_") as td:
        out = Path(td)
        (out / "s.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
        fake = _FakeRun(out, "s", spec)
        try:
            _cc.subprocess.run = fake
            _cc.shutil.which = lambda _name: "/usr/bin/true"
            ok, info = _cc.compile_pdf(str(out), "s")
        finally:
            _cc.subprocess.run, _cc.shutil.which = old_run, old_which
        return ok, info, fake.calls


_BLG_ERR = (b"[67] Utils.pm:479> ERROR - BibTeX subsystem: line 1, syntax error: "
            b"found \",\"\n[68] Biber.pm:137> INFO - ERRORS: 1\n")
# biber 失败 + 空 .bbl + .blg 有 ERROR → False，detail 带 ERROR 原文
_ok, _info, _calls = _run_compile({
    1: (0, {".bcf": b"</bcf:controlfile>\n"}),          # 第一次 xelatex 产出 .bcf
    2: (2, {".bbl": b"", ".blg": _BLG_ERR}),            # biber 失败
})
check("③ 坏 bib：compile_pdf 判 False（不再拿空 .bbl 假成功）", not _ok, _info)
check("③ 坏 bib：detail 指明 biber 步 + .blg 的 ERROR 行",
      "biber 第 2 步失败" in _info and "ERROR - BibTeX subsystem" in _info, _info)
check("③ 坏 bib：.bbl 为空时即便 .blg 无 ERROR 也判 False（空参考文献）",
      not _run_compile({1: (0, {".bcf": b"x"}), 2: (2, {".bbl": b""})})[0])
# 无 cite（biber 非零但没有 .bcf）→ 照旧放行（合成稿/无参考文献稿不能误伤）
_ok, _info, _calls = _run_compile({
    2: (2, {}),
    4: (0, {".pdf": b"%PDF-1.4\n"}),
})
check("③ 无 cite：biber 非零、无 .bcf → 照旧成功", _ok, _info)
check("③ 无 cite：确实是走了 4 步（biber 非零未中断）", len(_calls) == 4, f"{len(_calls)} 步")
# biber 非零但 .bbl 非空且 .blg 无 ERROR → 容忍（偶发告警不能误伤出稿）
_ok, _info, _ = _run_compile({
    1: (0, {".bcf": b"x"}),
    2: (2, {".bbl": b"% biblatex bbl\n", ".blg": b"[1] Biber.pm:1> INFO - ok\n"}),
    4: (0, {".pdf": b"%PDF-1.4\n"}),
})
check("③ biber 非零但 .bbl 非空、.blg 无 ERROR → 容忍放行", _ok, _info)
# xelatex 真错仍判 False，并读 .log 的 `!` 行
_ok, _info, _ = _run_compile({
    1: (1, {".log": b"! Undefined control sequence.\n"}),
})
check("③ xelatex 真错：判 False 且 detail 带 `!` 行",
      not _ok and "! Undefined control sequence." in _info, _info)

# ══════════════════════════════════════════════════════════════════════════
print("\n── ④ C6 P0-1：年末无括号年 + 连接词多作者（Kahneman 三人）──")
# ══════════════════════════════════════════════════════════════════════════
_kan = _fixture_text("kahneman_year_end.txt")
_p = parse_numbered_item(_kan[0])
check("④ Kahneman 年末行：三作者", _p.author == "Kahneman, D. and Slovic, P. and Tversky, A.",
      repr(_p.author))
check("④ Kahneman 年末行：题名不被第 2 作者顶掉、出版社归位",
      _p.title == "Judgment under uncertainty: Heuristics and biases"
      and _p.publisher == "Cambridge University Press" and _p.year == "1982",
      repr((_p.title, _p.publisher, _p.year)))
_p2 = parse_numbered_item(_kan[1])
check("④ 括号年对照行：仍三作者（`_parse_apa_item` 先返回，零变化）",
      _p2.author == _p.author and _p2.year == "1982", repr(_p2.author))

# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑤ C7 F1：括号年必须紧接作者列表末尾，题名内的 `(2000)` 不当年 ──")
# ══════════════════════════════════════════════════════════════════════════
_f1 = _fixture_text("apa_paren_year_counterexamples.txt")
check("⑤ 题名内 `(2000)`：不按 APA 年解析（返回 None，交给通用体例）",
      _parse_apa_item(_f1[0]) is None, repr(_parse_apa_item(_f1[0])))
_pa = _parse_apa_item(_f1[1])
check("⑤ 正常括号年行：year=2000（未被反例判据误伤）",
      _pa is not None and _pa.year == "2000", repr(getattr(_pa, "year", None)))
_pb = _parse_apa_item(_f1[2])
check("⑤ `(n.d.)` 行：按 n.d. 解析、year 空", _pb is not None and _pb.year == "",
      repr(getattr(_pb, "year", None)))
check("⑤ `(n.d.)` 行：`(2000)` 留在题名里、不再被当年",
      _pb is not None and "(2000)" in _pb.title, repr(getattr(_pb, "title", None)))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
