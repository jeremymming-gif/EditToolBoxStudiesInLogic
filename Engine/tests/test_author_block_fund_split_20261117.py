#!/usr/bin/env python3.14
r"""F-10：英文稿「作者块 + 末尾基金句」同处一条脚注时被整条误判为基金（2026-11-17）。

缺陷（用户实报，Lead 已用合成夹具 100% 复现）
---------------------------------------------
英文稿的首页脚注常写成「逐作者一行的作者块，末尾再跟一句基金」：

    Test Author A, School of Philosophy, Test University, E-mail: authora@example.invalid
    Test Author B, Department of Philosophy, Test University, E-mail: authorb@example.invalid
    Test Author C, Institute of Logic, Test University, E-mail: authorc@example.invalid
    *Research leading to this paper was funded by Test Foundation (Grant No. TEST-0001).

旧 `parse_author_info()` 的分支顺序把整条判成 funds：

    if looks_like_author_info(t):            …   # 无段标签 → False
    elif _ai_unlabeled_ok(t, known_cn, known_en):  # known_* 为空 → 直接 False
    elif _AI_FUND_SENTENCE_RE.search(t) …:   …   # ← 含 `funded by`，先命中
    else: _ai_en_block_prefix(t)             …   # 永远走不到

于是 `grants` 拿到的是**第一作者的姓名 + 单位**（funds 分支只取第一行非空），
真正的基金句与整份作者列表一起丢失。产物首页脚注因此印成
`*Test Author A School of Philosophy, Test University。`。

修法（`slcn/footnotes.py::parse_author_info`，只改分支顺序）
------------------------------------------------------------
把「无标签英文作者块」判定提到基金句判定**之前**；块后尾部再单独判 funds。
判据本身一个字没改（`_ai_en_block_prefix` / `_AI_FUND_SENTENCE_RE` /
`_AI_CITE_CUE_RE` 原样复用），只是先问「这条脚注开头是不是作者块」。

本套件钉住
----------
  ① 主缺陷：作者块 + 末尾基金句 → 3 位作者 + 基金句进 grants，grants **不含**作者；
  ② 回归：纯基金句 / 中文作者信息脚注 / 英文作者块无基金 / `Funding: …` 四条 SAME；
  ③ 边界 1：`_ai_en_block_prefix` 的行数与 `lines` 前 n 项一一对应（空行、制表符、
     CRLF、全角空格、行尾空白、缩进都试过），尾部切分不吞不重；
  ④ `*` 前缀（脚注标记）**原样保留**在 grants 里 —— 不剥，理由见断言旁的注释；
  ⑤ 反例：文献脚注、带年份的基金句、块在基金句**之后**——一律不臆造作者；
  ⑥ 端到端：合成 docx 走真实 `build.build_docx(lang="en")`，产物 `\mythanks`
     含项目号、**不含**第一作者姓名。

全部合成值（Test Author A/B/C、authora@example.invalid、TEST-0001），不联网、
不写仓库、不读真实稿件库、不跑 TeX。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 \
        tests/test_author_block_fund_split_20261117.py
"""
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docx_fixture_gen import make_docx                       # noqa: E402
from slcn import build  # noqa: E402
from slcn.footnotes import (_ai_clean_text, _ai_en_block_prefix,  # noqa: E402
                            parse_author_info)
from slcn.latex import parse_macros                          # noqa: E402

FAILS: list[str] = []
TMP = tempfile.mkdtemp(prefix="slcn_f10_")


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


# ── 合成夹具（全部假值）─────────────────────────────────────────────────────
A_LINE = ("Test Author A, School of Philosophy, Test University, "
          "E-mail: authora@example.invalid")
B_LINE = ("Test Author B, Department of Philosophy, Test University, "
          "E-mail: authorb@example.invalid")
C_LINE = ("Test Author C, Institute of Logic, Test University, "
          "E-mail: authorc@example.invalid")
BLOCK3 = "\n".join((A_LINE, B_LINE, C_LINE))
FUND_TAIL = ("*Research leading to this paper was funded by Test Foundation "
             "(Grant No. TEST-0001).")

MAIN = BLOCK3 + "\n" + FUND_TAIL
PURE_FUND = "This work was supported by Test Foundation (Grant No. TEST-0001)."
CN_INFO = "作者信息：测试作者甲，测试大学哲学系\n基金项目：测试基金 TEST-0001。"
EN_BLOCK_ONLY = BLOCK3
EN_LABELED_FUND = "Funding: Test Foundation Grant No. TEST-0001"


def _names(info):
    """取返回值的英文姓名列表（英文块走 `name_en`）。"""
    return [a.get("name_en") or a.get("name") for a in (info or {}).get("authors", [])]


# ── ① 主缺陷：作者块 + 末尾基金句 ──────────────────────────────────────────
print("── ① 主缺陷：作者块 + 末尾基金句 → 作者进 authors、基金句进 grants ──")
_info = parse_author_info(MAIN)
check("① 返回非 None", _info is not None, repr(_info))
check("① 作者 3 位且顺序不变",
      _names(_info) == ["Test Author A", "Test Author B", "Test Author C"],
      _names(_info))
check("① 单位逐作者独立（不复制第一作者）",
      [a.get("affiliation_en") for a in _info["authors"]] == [
          ["School of Philosophy, Test University"],
          ["Department of Philosophy, Test University"],
          ["Institute of Logic, Test University"]],
      [a.get("affiliation_en") for a in _info["authors"]])
check("① 邮箱逐作者独立",
      [a.get("email") for a in _info["authors"]] == [
          "authora@example.invalid", "authorb@example.invalid",
          "authorc@example.invalid"],
      [a.get("email") for a in _info["authors"]])
check("① grants = 基金句原文",
      _info["grants"] == FUND_TAIL, repr(_info["grants"]))
check("① grants 不含第一作者姓名（旧缺陷的核心症状）",
      "Test Author A" not in _info["grants"], repr(_info["grants"]))
check("① grants 不含任何作者姓名",
      not any(n in _info["grants"] for n in ("Test Author A", "Test Author B",
                                             "Test Author C")),
      repr(_info["grants"]))
check("① 无 warning", _info["warnings"] == [], _info["warnings"])

# ── ② 四条回归夹具必须 SAME（既有行为一个字节都不动）─────────────────────
print("── ② 回归夹具：纯基金句 / 中文作者信息 / 英文块无基金 / Funding: 标签 ──")
_r1 = parse_author_info(PURE_FUND)
check("②-1 纯基金句仍走 funds 分支",
      _r1 is not None and _r1["authors"] == [] and _r1["grants"] == PURE_FUND,
      repr(_r1))
_r2 = parse_author_info(CN_INFO)
check("②-2 中文作者信息脚注仍走 looks_like_author_info 分支",
      _r2 is not None and _r2["grants"] == "测试基金 TEST-0001。", repr(_r2))
_r3 = parse_author_info(EN_BLOCK_ONLY)
check("②-3 英文作者块无基金：作者照旧、grants 为空",
      _r3 is not None and _names(_r3) == ["Test Author A", "Test Author B",
                                          "Test Author C"]
      and _r3["grants"] == "", repr(_r3))
_r4 = parse_author_info(EN_LABELED_FUND)
check("②-4 `Funding: …` 标签块仍产 grants",
      _r4 is not None and _r4["authors"] == []
      and _r4["grants"] == "Test Foundation Grant No. TEST-0001", repr(_r4))

# ── ③ 边界 1：`_ai_en_block_prefix` 行数 与 `lines` 前 n 项一一对应 ────────
print("── ③ 边界 1：块行数与清洗后行号一一对应（空行/制表符/CRLF/全角空格）──")
_VARIANTS = {
    "LF": A_LINE + "\n" + B_LINE + "\n" + FUND_TAIL,
    "CRLF": A_LINE + "\r\n" + B_LINE + "\r\n" + FUND_TAIL,
    "CR": A_LINE + "\r" + B_LINE + "\r" + FUND_TAIL,
    "空行": A_LINE + "\n\n\n" + B_LINE + "\n\n" + FUND_TAIL,
    "制表符缩进": "\t" + A_LINE + "\n  \t " + B_LINE + "\n\t" + FUND_TAIL,
    "全角空格": "\u3000" + A_LINE + "\n\u3000\u3000" + B_LINE + "\n" + FUND_TAIL,
    "行尾空白": A_LINE + "   \n" + B_LINE + "\t\n" + FUND_TAIL + "  ",
    "开头空行": "\n" + A_LINE + "\n" + B_LINE + "\n" + FUND_TAIL,
    "姓名行内多空格": A_LINE.replace(", ", ",  ") + "\n"
                      + B_LINE.replace(", ", ",   ") + "\n" + FUND_TAIL,
}
for _tag, _text in _VARIANTS.items():
    _blk = _ai_en_block_prefix(_text)
    _lines = [x for x in re.split(r"[\n\r]+", _text) if _ai_clean_text(x)]
    _n = len(_blk.splitlines()) if _blk else 0
    _aligned = bool(_blk) and [_ai_clean_text(x) for x in _lines[:_n]] \
        == _blk.splitlines()
    _rest = "\n".join(_lines[_n:]).strip() if _blk else ""
    check(f"③ [{_tag}] 块行数与前 n 行逐行相等（n={_n}）", _aligned,
          (repr(_blk), [repr(x) for x in _lines]))
    check(f"③ [{_tag}] 尾部只剩基金句（不吞作者、不重复）",
          _rest == FUND_TAIL, repr(_rest))
    _vi = parse_author_info(_text)
    check(f"③ [{_tag}] 解析结果与主夹具一致",
          _vi is not None and _names(_vi) == ["Test Author A", "Test Author B"]
          and _vi["grants"] == FUND_TAIL, repr(_vi))

# 单位续行 / 纯邮箱续行也要计入块行数（否则尾部会从作者块中间切开）
_UNIT_CONT = A_LINE + "\nDepartment of Logic, Test University\n" + B_LINE \
    + "\n" + FUND_TAIL
_blk = _ai_en_block_prefix(_UNIT_CONT)
_lines = [x for x in re.split(r"[\n\r]+", _UNIT_CONT) if _ai_clean_text(x)]
check("③ [单位续行] 块含续行、尾部仍只切出基金句",
      bool(_blk) and len(_blk.splitlines()) == 3
      and "\n".join(_lines[3:]).strip() == FUND_TAIL,
      (repr(_blk), repr(_lines)))
_EMAIL_CONT = A_LINE + "\nauthora@example.invalid\n" + B_LINE + "\n" + FUND_TAIL
_blk = _ai_en_block_prefix(_EMAIL_CONT)
_lines = [x for x in re.split(r"[\n\r]+", _EMAIL_CONT) if _ai_clean_text(x)]
check("③ [纯邮箱续行] 块含续行、尾部仍只切出基金句",
      bool(_blk) and len(_blk.splitlines()) == 3
      and "\n".join(_lines[3:]).strip() == FUND_TAIL,
      (repr(_blk), repr(_lines)))

# ── ④ `*` 前缀（脚注标记）原样保留 ────────────────────────────────────────
print("── ④ `*` 脚注标记：原样保留在 grants 里（保守，不剥）──")
check("④ grants 保留 `*` 前缀", _info["grants"].startswith("*"),
      repr(_info["grants"]))
_starless = parse_author_info(BLOCK3 + "\n" + FUND_TAIL.lstrip("*"))
check("④ 无 `*` 的同一句 → grants 同样只差这个 `*`",
      _starless is not None and _starless["grants"] == FUND_TAIL.lstrip("*"),
      repr(_starless and _starless["grants"]))
# 为什么不剥：`*` 是**源稿脚注标记**，`\mythanks` 在 SLEN.sty 里排成标题的
# `\thanks{…}` 脚注（`:800`），模板自己会生成标记；此处剥掉等于替模板决定标记，
# 且 `parse_author_info` 对中文 `作者简介`/`基金项目` 的取值一律**逐字搬运**、
# 不做展示层清理（清理在 build.FIELD_CLEANUPS，那里也没剥 `*`）。保守 = 不改。

# ── ⑤ 反例：不臆造作者 ───────────────────────────────────────────────────
print("── ⑤ 反例：文献脚注 / 带年份基金句 / 块在基金句之后 ──")
_cite = "Test Author A. A synthetic title[J]. Test Journal, 2019, 1(1): 1--5."
check("⑤ 普通英文文献脚注仍返回 None", parse_author_info(_cite) is None,
      repr(parse_author_info(_cite)))
_year_fund = "This work was supported by Test Foundation (Grant No. TEST-0001, 2024)."
check("⑤ 带年份的基金句仍不产作者（文献线索优先）",
      parse_author_info(_year_fund) is None, repr(parse_author_info(_year_fund)))
_fund_first = FUND_TAIL + "\n" + BLOCK3
_fi = parse_author_info(_fund_first)
check("⑤ 基金句在前、作者块在后：不臆造作者（块判定只认开头连续段）",
      _fi is not None and _fi["authors"] == [] and _fi["grants"] == FUND_TAIL,
      repr(_fi))
_ack_tail = BLOCK3 + "\nAcknowledgements: thanks to Test Reviewer A."
_ai = parse_author_info(_ack_tail)
check("⑤ 块后跟 Acknowledgements 尾部：作者照旧、尾部不当基金",
      _ai is not None and _names(_ai) == ["Test Author A", "Test Author B",
                                          "Test Author C"]
      and _ai["grants"] == "", repr(_ai))
_two_tails = BLOCK3 + "\n" + FUND_TAIL + "\nAcknowledgements: thanks to Test Reviewer A."
_ti = parse_author_info(_two_tails)
check("⑤ 块 + 基金句 + 致谢句：作者与基金各就各位",
      _ti is not None and _names(_ti) == ["Test Author A", "Test Author B",
                                          "Test Author C"]
      and _ti["grants"] == FUND_TAIL, repr(_ti))
_si = parse_author_info(A_LINE + "\n" + FUND_TAIL)
check("⑤ 单作者（带邮箱）+ 基金句：作者与基金都认",
      _si is not None and _names(_si) == ["Test Author A"]
      and _si["grants"] == FUND_TAIL, repr(_si))

# ── ⑥ 端到端：真实 build_docx(lang="en") 的 `\mythanks` ───────────────────
print("── ⑥ 端到端：合成 docx → build_docx(lang=en) → 产物 \\mythanks ──")
_REFS = [f"[{i}] Au{i}. Title M{i:02d} marker[J]. Test Journal, "
         f"20{10 + i % 10}, 1(1): {i}--{i + 5}." for i in range(1, 6)]
_src = os.path.join(TMP, "f10.docx")
make_docx(_src,
          paras=["测试题名：合成英文题名", "Test Author A",
                 "摘要：Synthetic abstract.", "关键词：test",
                 "Body text end.", "参考文献"] + _REFS,
          footnotes=[MAIN], footnote_anchor=1)
_res = build.build_docx(_src, TMP, basename="f10", lang="en", meta=None,
                        compile_requested=False, verbose=False)
_tex = open(_res.tex_path, encoding="utf-8").read()
_v = macro(_tex, "mythanks")
check("⑥ 产物 \\mythanks 非空", bool(_v and _v.strip() not in ("", "null")),
      repr(_v))
check("⑥ \\mythanks 含基金项目号/基金会名（真正的基金句没丢）",
      bool(_v and ("TEST-0001" in _v or "Test Foundation" in _v)), repr(_v))
check("⑥ \\mythanks 不含第一作者姓名（旧缺陷的核心症状）",
      bool(_v) and "Test Author A" not in _v, repr(_v))
check("⑥ 产物不给英文模板塞 \\mygrants",
      "\\mygrants" not in _tex, "")

# ── 汇总 ─────────────────────────────────────────────────────────────────
print("── 汇总 ──")
print("FAILS=%d" % len(FAILS))
sys.exit(1 if FAILS else 0)
