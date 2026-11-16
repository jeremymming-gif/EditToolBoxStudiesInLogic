#!/usr/bin/env python3.14
r"""F-01 窄测：英文 DOCX 基金/致谢抽取进入有效 mythanks（2026-11-16）。

只覆盖本批修复的**抽取侧**（footnotes.py / wordbody.py 的英文基金识别）；
落槽侧（build.apply_editorial_fields）仅做集成断言，不修改。全部合成夹具，
不联网、不跑真实 PDF、不碰 corpus。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_f01_en_docx_funding.py
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
from slcn import build, templates as T                      # noqa: E402
from slcn.footnotes import classify, parse_author_info, parse_fund_line  # noqa: E402
from slcn.latex import parse_macros                         # noqa: E402

FAILS: list[str] = []
TMP = tempfile.mkdtemp(prefix="slcn_f01_")
EN_TPL = open(T.resolve("en").skeleton, encoding="utf-8").read()
EN_FIELDS = set(parse_macros(EN_TPL))


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


_REFS = [f"[{i}] Au{i}. Title M{i:02d} marker[J]. Journal of Logic, "
         f"20{10 + i % 10}, 1(1): {i}--{i + 5}." for i in range(1, 6)]


def _paras(fund_line=None):
    p = ["测试题名：合成英文题名", "Zhang San", "摘要：Synthetic abstract.",
         "关键词：test", "Body text end."]
    if fund_line:
        p.append(fund_line)
    p += ["参考文献"] + list(_REFS)
    return p


def build_en(case, *, footnotes=(), fund_line=None, meta=None):
    src = os.path.join(TMP, case + ".docx")
    make_docx(src, paras=_paras(fund_line), footnotes=list(footnotes),
              footnote_anchor=1 if footnotes else -1)
    r = build.build_docx(src, TMP, basename=case, lang="en", meta=meta,
                         compile_requested=False, verbose=False)
    return r, open(r.tex_path, encoding="utf-8").read()


# ── ① 英文基金脚注（带标签）→ mythanks ────────────────────────────────────
print("── ① 英文基金脚注（带标签）→ mythanks ──")
_r, _t = build_en("fn_labeled", footnotes=["Funding: NSFC Grant No. 12345678"])
_v = macro(_t, "mythanks")
check("① mythanks 非空且非 null",
      _v is not None and _v.strip() not in ("", "null"), repr(_v))
check("① 项目号进入 mythanks", bool(_v and "12345678" in _v), repr(_v))
check("① 脚注未被当文献提升", classify("Funding: NSFC Grant No. 12345678").kind == "pure_note",
      classify("Funding: NSFC Grant No. 12345678").kind)
_info = parse_author_info("Funding: NSFC Grant No. 12345678")
check("① parse_author_info 英文标注块产 funds", bool(_info and _info["grants"]), repr(_info))
check("① 不注入 \\mygrants",
      "\\newcommand{\\mygrants}" not in _t and "\\mygrants" not in _t, "")

# ── ② 正文英文基金独立行 → mythanks，并从正文移出 ─────────────────────────
print("── ② 正文英文基金独立行 → mythanks，并从正文移出 ──")
_r, _t = build_en("body_line", fund_line="Grant No.: 2026ABC90")
_v = macro(_t, "mythanks")
check("② 正文基金行进入 mythanks", bool(_v and "2026ABC90" in _v), repr(_v))
check("② 正文不再重复印基金行（全文仅出现一次）", _t.count("2026ABC90") == 1,
      _t.count("2026ABC90"))
check("② parse_fund_line 认英文独立行",
      parse_fund_line("Grant No.: 2026ABC90") == "2026ABC90",
      repr(parse_fund_line("Grant No.: 2026ABC90")))

# ── ③ 项目号/特殊字符保留且只转义一次 ────────────────────────────────────
print("── ③ 项目号/特殊字符保留且只转义一次 ──")
_r, _t = build_en("special", footnotes=["Funding: NSFC 50% & #1_2"])
_v = macro(_t, "mythanks")
check("③ 特殊字符进入 mythanks 且只转义一次",
      _v == "NSFC 50\\% \\& \\#1\\_2。" and "\\\\%" not in _v, repr(_v))

# ── ④ 与已有致谢去重 ──────────────────────────────────────────────────────
print("── ④ 与已有致谢去重 ──")
_r, _t = build_en("dedupe", footnotes=["Funding: NSFC Grant No. 12345678"],
                  meta={"thanks": "NSFC Grant No. 12345678"})
_v = macro(_t, "mythanks") or ""
check("④ 同一句只出现一次", _v.count("NSFC Grant No. 12345678") == 1, repr(_v))

# ── ⑤ 显式值优先，解析只补空 ─────────────────────────────────────────────
print("── ⑤ 显式值优先，解析只补空 ──")
_r, _t = build_en("explicit", footnotes=["Funding: NSFC Grant No. 12345678"],
                  meta={"grants": "Explicit Grant X1"})
_v = macro(_t, "mythanks") or ""
check("⑤ 显式 grants 优先，抽取值不覆盖",
      "Explicit Grant X1" in _v and "12345678" not in _v, repr(_v))

# ── ⑥ 中文基金行为不回退 ─────────────────────────────────────────────────
print("── ⑥ 中文基金行为不回退 ──")
_cn = ("作者简介：张三\n单位：示例大学\n邮箱：zs@example.edu.cn\n"
       "基金项目：国家社科基金 20BZX001")
_ci = parse_author_info(_cn)
check("⑥ 中文 parse_author_info 仍产 funds",
      bool(_ci and _ci["grants"] == "国家社科基金 20BZX001"), repr(_ci))
check("⑥ 中文 parse_fund_line 仍认独立行",
      parse_fund_line("基金项目：国家社科基金 20BZX001") == "国家社科基金 20BZX001", "")

# ── ⑦ 英文非基金块不被误判 ───────────────────────────────────────────────
print("── ⑦ 英文非基金块不被误判 ──")
_cite = "Au1. Title M01 marker. Journal of Logic, 2019, 1(1): 1--5."
_ci = parse_author_info(_cite)
check("⑦ 普通英文文献脚注不产基金", not _ci or not _ci["grants"], repr(_ci))
check("⑦ 普通英文文献行不被当基金行", parse_fund_line(_cite) == "",
      repr(parse_fund_line(_cite)))
check("⑦' Fund No. 变体认独立行且保留项目号",
      parse_fund_line("Fund No.: 2024ABC123") == "2024ABC123",
      repr(parse_fund_line("Fund No.: 2024ABC123")))

# ── ⑧ 无标签英文基金句 ───────────────────────────────────────────────────
print("── ⑧ 无标签英文基金句 ──")
_r, _t = build_en("fn_sentence",
                  footnotes=["This work was supported by NSFC (Grant No. 87654321)."])
_v = macro(_t, "mythanks")
check("⑧ 无标签基金句项目号进入 mythanks", bool(_v and "87654321" in _v), repr(_v))

# ── 汇总 ─────────────────────────────────────────────────────────────────
print("── 汇总 ──")
print("FAILS=%d" % len(FAILS))
sys.exit(1 if FAILS else 0)
