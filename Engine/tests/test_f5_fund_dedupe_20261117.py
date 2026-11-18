#!/usr/bin/env python3.14
r"""F5 窄测：脚注/标题页基金项与正文基金行「双源重复」去重（2026-11-17）。

缺陷（修前 `Engine/slcn/wordbody.py` 的 `if not any(_i.get("grants") ...)` 守卫）：
只要作者信息脚注里已经解析出基金项，正文里那条独立的 `基金项目：…` / `Funding: …`
行就**完全不处理**——既不移入元数据、也不从正文删除 → 同一基金号在 .tex 里出现
两次（`\newcommand{\mythanks}{…}` + 正文原行），PDF 里也印两次。

本套件钉住：
  ① 脚注基金 + 正文同一基金行 → 正文行删除，基金号全文只出现一次；
  ② 脚注基金 + 正文**不同**基金行 → 仍删除正文行，且 grants 取脚注那份（不合并）；
  ③ 无脚注基金 + 正文基金行 → 仍走原「移入元数据」路径（批71 B-118 ⑤b 不回退）；
  ④ 保守边界：正文里无标签的致谢句（`This work was supported by …`）留在正文，
     不被当成基金行删除；
  ⑤ 两种路径的 warning 文案各自出现，不串台。

全部合成夹具（Test Author A / TEST-0001 这类占位值），不联网、不跑真实 PDF、
不碰 corpus。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_f5_fund_dedupe_20261117.py
"""
import os
import sys
import tempfile

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docx_fixture_gen import make_docx                      # noqa: E402
from slcn import build, docxsource                          # noqa: E402
from slcn.latex import parse_macros                         # noqa: E402

FAILS: list[str] = []
TMP = tempfile.mkdtemp(prefix="slcn_f5_fund_")

FUND = "Funding: Test Foundation Grant No. TEST-0001"

_REFS = [f"[{i}] Au{i}. Title M{i:02d} marker[J]. Journal of Logic, "
         f"20{10 + i % 10}, 1(1): {i}--{i + 5}." for i in range(1, 4)]


def check(name, cond, detail="") -> None:
    ok = bool(cond)
    line = ("PASS  " if ok else "FAIL  ") + name
    if not ok:
        line += "  " + " ⏎ ".join(str(detail).splitlines())
        FAILS.append(name)
    print(line)


def macro(tex, name):
    m = parse_macros(tex).get(name)
    return None if m is None else m.value


def _paras(body_fund=None, body_tail=None):
    """合成英文稿：题名 / 作者 / 单位 / 摘要 / 关键词 / 正文 / [基金行] / 正文 / 文献。"""
    p = ["Test Title of a Synthetic Paper",
         "Test Author A",
         "Test University, Department of Logic",
         "Abstract. This is a synthetic abstract long enough to be recognized as an "
         "abstract by the parser, purely for testing the funding de-duplication path.",
         "Keywords: test; synthetic; funding",
         "Body first paragraph with no funding information at all, just filler text."]
    if body_fund:
        p.append(body_fund)
    if body_tail:
        p.append(body_tail)
    p.append("Body second paragraph after the funding line, also plain filler text.")
    p += ["References"] + list(_REFS)
    return p


def build_en(case, *, footnotes=(), body_fund=None, body_tail=None):
    """造 docx → 解析（取 doc.body/warnings）+ 构建（取 .tex）。"""
    src = os.path.join(TMP, case + ".docx")
    make_docx(src, paras=_paras(body_fund, body_tail), footnotes=list(footnotes),
              footnote_anchor=1 if footnotes else -1)
    doc = docxsource.parse_docx(src)
    r = build.build_docx(src, TMP, basename=case, lang="en", meta=None,
                         compile_requested=False, verbose=False)
    return doc, r, open(r.tex_path, encoding="utf-8").read()


def _has(warnings, needle):
    return any(needle in w for w in warnings)


# ── ① 脚注基金 + 正文同一基金行 → 正文行删除 ──────────────────────────────
print("── ① 脚注基金 + 正文同一基金行（本批缺陷主体）──")
_d1, _r1, _t1 = build_en("dup_same", footnotes=[FUND], body_fund=FUND + ".")
_v1 = macro(_t1, "mythanks") or ""
check("① 基金号在 .tex 中只出现一次（修前为 2）", _t1.count("TEST-0001") == 1,
      "_t1.count=%d" % _t1.count("TEST-0001"))
check("① mythanks 仍带基金号（标题页那份没丢）", "TEST-0001" in _v1, repr(_v1))
check("① 正文里不再有那条基金行", "Funding: Test Foundation" not in _d1.body,
      repr([ln for ln in _d1.body.splitlines() if "TEST-0001" in ln]))
check("① doc.grants 取标题页那份（无尾句号）",
      _d1.grants == "Test Foundation Grant No. TEST-0001", repr(_d1.grants))
check("① warning 记为「重复→去重删除」",
      _has(_d1.warnings, "与标题页基金项重复，已去重删除"),
      repr(_d1.warnings))
check("① 不再误报「已移入元数据」（两条路径不串台）",
      not _has(_d1.warnings, "已移入元数据"), repr(_d1.warnings))

# ── ② 脚注基金 + 正文另一条基金行 → 删正文行，grants 取脚注 ────────────────
print("── ② 脚注基金 + 正文不同基金行（不合并成怪东西）──")
_d2, _r2, _t2 = build_en("dup_diff", footnotes=[FUND], body_fund="Grant No.: TEST-0002")
_v2 = macro(_t2, "mythanks") or ""
check("② 正文那条不同基金号已从产物消失", "TEST-0002" not in _t2,
      [ln for ln in _t2.splitlines() if "TEST-0002" in ln])
check("② 标题页基金号仍在", _t2.count("TEST-0001") == 1,
      "_t2.count=%d" % _t2.count("TEST-0001"))
check("② grants 只取脚注那份，未被正文覆盖",
      _d2.grants == "Test Foundation Grant No. TEST-0001", repr(_d2.grants))
check("② mythanks 不含正文那条", "TEST-0002" not in _v2, repr(_v2))
check("② 正文里不再有那条基金行", "Grant No.: TEST-0002" not in _d2.body,
      repr([ln for ln in _d2.body.splitlines() if "TEST-0002" in ln]))

# ── ③ 无脚注基金 + 正文基金行 → 原「移入元数据」路径不回退 ────────────────
print("── ③ 无脚注基金 + 正文基金行（批71 B-118 ⑤b 不回退）──")
_d3, _r3, _t3 = build_en("body_only", body_fund="Grant No.: TEST-0003")
_v3 = macro(_t3, "mythanks") or ""
check("③ grants 取自正文那条", _d3.grants == "TEST-0003", repr(_d3.grants))
check("③ 正文里已移出该行", "TEST-0003" not in _d3.body,
      repr([ln for ln in _d3.body.splitlines() if "TEST-0003" in ln]))
check("③ 产物里只出现一次", _t3.count("TEST-0003") == 1,
      "_t3.count=%d" % _t3.count("TEST-0003"))
check("③ mythanks 带上正文基金号", "TEST-0003" in _v3, repr(_v3))
check("③ warning 记为「已移入元数据」",
      _has(_d3.warnings, "已移入元数据"), repr(_d3.warnings))
check("③ 不误报「去重删除」",
      not _has(_d3.warnings, "已去重删除"), repr(_d3.warnings))

# ── ④ 保守边界：无标签致谢句留在正文 ─────────────────────────────────────
print("── ④ 保守边界：正文无标签致谢句不被当基金行删掉 ──")
_thanks = ("This work was supported by the Test Foundation under "
           "Grant No. TEST-0004.")
_d4, _r4, _t4 = build_en("thanks_stays", footnotes=[FUND], body_tail=_thanks)
check("④ 致谢句仍留在正文", _thanks in _d4.body,
      repr([ln for ln in _d4.body.splitlines() if "supported by" in ln]))
check("④ 致谢句基金号在 .tex 中仍出现", "TEST-0004" in _t4, "")
check("④ 未因致谢句触发基金行删除 warning",
      not _has(_d4.warnings, "已去重删除") and not _has(_d4.warnings, "已移入元数据"),
      repr(_d4.warnings))
check("④ 标题页基金号未被致谢句干扰",
      _d4.grants == "Test Foundation Grant No. TEST-0001", repr(_d4.grants))

# ── ⑤ 无任何基金行 → 零变化、零 warning ──────────────────────────────────
print("── ⑤ 无基金来源 → 不产生基金 warning ──")
_d5, _r5, _t5 = build_en("no_fund")
check("⑤ grants 为空", not _d5.grants, repr(_d5.grants))
check("⑤ 无基金相关 warning",
      not _has(_d5.warnings, "基金项目"), repr(_d5.warnings))

# ── 汇总 ─────────────────────────────────────────────────────────────────
print("── 汇总 ──")
print("FAILS=%d" % len(FAILS))
sys.exit(1 if FAILS else 0)
