#!/usr/bin/env python3
r"""FIX-20261112 / Q7（BIB-QUOTE-01）+ Q8（EN-06） + Q9（--fix 只在有改动时写副本）。

Q7：`footnotes.entry_to_bib` 在 `_bib_escape` 之前**按字段**归一化弯引号：
    author/editor 只 ’‘“”→ASCII `'`；title/journal/note 用与 EN-03 同源映射
    （’→'、‘→`、“→``、”→''）；misc 逐字不动；只映射非 ASCII → 幂等。
Q8：`\subsection` 句首式 fixer —— 只改纯文本词（命令/数学/引号/冒号后首字母/专名/
    缩写/连字符按手册），CLI `--en-subsection-case=auto|report|off`（英文默认 auto）；
    报告固定行 `自动改 N 处／待人工 M 词`，section 与 subsection 分表；幂等。
Q9：`--fix` 仅在（自动改＋位序搬移）实际处数 > 0 时 atomic_write 副本；0 处不写、
    不覆盖既有产物；报告固定可机读行（有改 / 未另写）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footnotes as F  # noqa: E402
from slcn import proofcheck as P  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"FAIL  {name}  {detail}")


TMP = tempfile.mkdtemp(prefix="fix20261112_q789_")

# ═══════════════════════════════════════════════════════════════════════════
# Q7：BIB-QUOTE-01
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q7] 参考文献弯引号按字段归一化")

_a = F.entry_to_bib("k1", {
    "_type": "article",
    "author": "John O’Brien and “Bob” Smith",
    "title": "The ‘Great’ Debate: “Norms”",
    "journal": "Journal of ‘X’",
    "note": "see “p. 3”",
})
check("Q7-1 author：’‘“” 全转 ASCII 单引号",
      "author  = {John O'Brien and 'Bob' Smith}" in _a, repr(_a))
check("Q7-2 title/journal/note：按 EN-03 同源映射（‘→`、“→``、’→'、”→''）",
      "title   = {The `Great' Debate: ``Norms''}" in _a
      and "journal = {Journal of `X'}" in _a
      and "note    = {see ``p. 3''}" in _a, repr(_a))

_e = F.entry_to_bib("k2", {"_type": "book", "editor": "Ann O’Neil",
                           "title": "A ‘Book’", "publisher": "P"})
check("Q7-3 editor：也只转 ASCII 单引号",
      re.search(r"editor\s+= \{Ann O'Neil\}", _e) is not None, repr(_e))
check("Q7-4 editor 与 author 规则隔离（editor 不走 title 的 LaTeX 引号）",
      "``" not in _e.split("editor")[1].split("\n")[0], repr(_e))

_misc_raw = "“引文”—不动：O’Brien 与 Smith"
_m = F.entry_to_bib("k3", {"_type": "misc", "author": "x", "title": "y"}, _misc_raw)
check("Q7-5 misc 分支逐字不动（弯引号/破折号原样）", _misc_raw in _m, repr(_m))
check("Q7-6 misc 不产生 ASCII 引号替换", '"' not in _m and "``" not in _m, repr(_m))

_asc = "John O'Brien and 'Bob' Smith"
check("Q7-7 ASCII 输入映射为无操作（_bib_map_quotes 幂等）",
      F._bib_map_quotes(_asc, F._BIB_AUTHOR_PUNCT) == _asc)
check("Q7-8 ASCII 题名映射为无操作（_bib_title_punct）",
      F._bib_title_punct("The 'Great' Debate") == "The 'Great' Debate")

check("Q7-9 同一 entry 反复调用逐字节相同（幂等）",
      F.entry_to_bib("k1", {
          "_type": "article", "author": "John O’Brien",
          "title": "The ‘Great’ Debate", "journal": "J",
          "note": "n"}) == F.entry_to_bib("k1", {
              "_type": "article", "author": "John O’Brien",
              "title": "The ‘Great’ Debate", "journal": "J", "note": "n"}))

_ascii_entry = {"_type": "article", "author": "John O'Brien",
                "title": "The 'Great' Debate: \"Norms\"",
                "journal": "Journal of 'X'", "note": 'see "p. 3"'}
_b1 = F.entry_to_bib("k4", _ascii_entry)
check("Q7-10 全 ASCII entry：输出不含非 ASCII 弯引号",
      not any(c in _b1 for c in "’‘“”"), repr(_b1))

# ═══════════════════════════════════════════════════════════════════════════
# Q8：EN-06 句首式 fixer（API 层）
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q8] \\subsection 句首式大小写 fixer（保护类/冒号/连字符/幂等/off）")


def _case(tex: str, body: str | None = None, mode: str = "auto"):
    bw = P._en06_body_words(tex + "\n\n" + body) if body else None
    out, info = P.apply_en_subsection_case(tex, mode, bw)
    return out, info


t = "\\subsection{From Individual Action to Shared Intentionality}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-1 普通 Title Case → 句首式",
      _o.splitlines()[0] == "\\subsection{From individual action to shared intentionality}"
      and len(_i["fixed"]) == 1, repr(_o))

t = "\\subsection{On Dual-Level Structures and Second-Personal Coordination}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-2 连字符按句首式：Dual-Level→Dual-level；非首词整体小写",
      _o.splitlines()[0] == ("\\subsection{On dual-level structures and "
                        "second-personal coordination}"), repr(_o))

t = "\\subsection{Testing: A Study of Things}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-3 冒号后保留原首字母（A 仍大写），其后按句首式",
      _o.splitlines()[0] == "\\subsection{Testing: A study of things}", repr(_o))

t = "\\subsection{Testing: a Study of Things}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-4 冒号后原首字母是小写就保持小写（不擅自大写）",
      _o.splitlines()[0] == "\\subsection{Testing: a study of things}", repr(_o))

t = ("\\subsection{The Role of AI and the U.S. Case}\n\n"
     "Body text mentioning AI and the U.S. Case here in full sentences.\n")
_o, _i = _case(t)
check("Q8-5 保护全大写缩写 AI / U.S.（原样保留，进待人工清单）",
      "AI" in _o and "U.S." in _o
      and {it["old"] for it in _i["manual"]} >= {"AI", "U.S."}, repr(_o))

t = "\\subsection{McDowell and the Space of Reasons}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-6 保护含内部大写的混合词（McDowell）", "McDowell" in _o, repr(_o))

t = "\\subsection{Notes on Plato and the Good}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-7 内置专名表保护（Plato）+ 待人工清单可追溯",
      "Plato" in _o and any(it["old"] == "Plato" for it in _i["manual"]), repr(_o))

t = ("\\subsection{Notes on Practice and Coordination}\n\n"
     "This body keeps Practice capitalised because it is a term we use often.\n")
_o, _i = _case(t)
check("Q8-8 本文正文出现过的大写词不降级（Practice 保留、进清单）",
      "Practice" in _o and any(it["old"] == "Practice" for it in _i["manual"]),
      repr(_o))
check("Q8-8b 同标题其余纯文本词照常改小写", "notes on" in _o.lower(), repr(_o))

t = r"""\subsection{Testing \emph{Emphasis} and $x^2$ Math and \cite{Key}}

Body.
"""
_o, _i = _case(t)
check("Q8-9 命令/数学/引用参数原样（只改纯文本词 Math→math）",
      r"\emph{Emphasis}" in _o and "$x^2$" in _o and r"\cite{Key}" in _o
      and "math" in _o, repr(_o))

t = r"""\subsection{The ``We'' and the \emph{Other}}

Body.
"""
_o, _i = _case(t)
check("Q8-10 引号内原文原样（``We'' 不被改小写）", "``We''" in _o, repr(_o))

t = "\\subsection{What I Mean by This}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-11 英文代词 I（也是罗马数字）保持大写", " I " in _o, repr(_o))

t = "\\subsection{On Being and Time}\n\nBody.\n"
_out1, _i1 = _case(t)
_out2, _i2 = _case(_out1)
check("Q8-12 幂等：二次调用 0 改且文本不变",
      _out1 == _out2 and not _i2["fixed"], f"{_i2}")

# ── V OBS-1：所有格专名（Quine's）保护回落到词干 ─────────────────────────────
t = "\\subsection{The Role of Quine's Naturalism in Modern Philosophy}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-12a 所有格专名保留（Quine's），其余词仍按句首式",
      _o.splitlines()[0] == ("\\subsection{The role of Quine's naturalism in "
                             "modern philosophy}"), repr(_o))
check("Q8-12b 所有格专名进待人工清单可追溯（Quine's）",
      any(it["old"] == "Quine's" for it in _i["manual"]), f"{_i['manual']}")
_o2, _i2 = _case(_o)
check("Q8-12c 所有格专名修复幂等：同输入二次调用逐字相同且 0 改",
      _o2 == _o and not _i2["fixed"], f"{_i2}")

t = "\\subsection{The Role of the State}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-12d 对照负例：普通词 State 不因所有格词干规则被过度保护",
      _o.splitlines()[0] == "\\subsection{The role of the state}", repr(_o))

# 句首所有格仍走 start 分支还原首字母大写（不是误保留原小写）
t = "\\subsection{quine's Naturalism and Modern Philosophy}\n\nBody.\n"
_o, _i = _case(t)
check("Q8-12e 句首所有格：quine's → Quine's（首词大写逻辑不受影响）",
      _o.splitlines()[0] == "\\subsection{Quine's naturalism and modern philosophy}",
      repr(_o))

t = "\\subsection{Second-Personal Coordination in Practice}\n\nBody.\n"
_o, _i = _case(t, mode="off")
check("Q8-13 off：不改、info 空", _o == t and not _i["fixed"] and not _i["candidates"])
_o, _i = _case(t, mode="report")
check("Q8-14 report：不改文本，只给候选",
      _o == t and not _i["fixed"] and len(_i["candidates"]) == 1, f"{_i}")

sec = "\\section{The structure of human cooperation}\n\nBody.\n"
_rep = P.check(sec, lang="en")
check("Q8-15 只改 subsection：\\section 不参与自动改（EN-06 只报）",
      P.apply_en_subsection_case(sec, "auto")[0] == sec
      and any(h.rule_id == "EN-06" and h.text.startswith("\\section") for h in _rep.hits))

# ═══════════════════════════════════════════════════════════════════════════
# Q8/Q9：CLI 端到端
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q8/Q9] proofcheck.py CLI 端到端")


def _run(*args: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _ENGINE + os.pathsep + os.path.join(_ENGINE, "vendor")
    p = subprocess.run([sys.executable, os.path.join(_ENGINE, "proofcheck.py"), *args],
                       cwd=_ENGINE, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def _write(name: str, text: str) -> str:
    d = tempfile.mkdtemp(prefix=name + "_", dir=TMP)
    p = os.path.join(d, name + ".tex")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


_EN_TEX = """\\section{Introduction}

\\subsection{Joint Intentionality: Second-Personal Coordination Between Particular Individuals}

\\subsection{Argumentative Norms and the Evaluative Perspective of ``We''}

\\subsection{Understanding the Methodological Principles of Generalized Argumentation Theory through Dual-Level Coordination}

Body text of the manuscript, long enough to look like prose.
"""
_p = _write("CaseEn", _EN_TEX)

# 非 --fix：报告也给 EN-06 表（0 改 / 候选）
_rc, _out = _run("--tex", _p, "--lang", "en")
_md = open(os.path.join(os.path.dirname(_p), "CaseEn_一校核查.md"), encoding="utf-8").read()
check("Q8-16 CLI 非 --fix：stdout 有固定行「自动改 N 处／待人工 M 词」",
      re.search(r"自动改 0 处／待人工 0 词", _out) is not None, _out[-200:])
check("Q8-17 CLI 报告含 EN-06 段与候选表（0 改也给表）",
      "## EN-06 标题大小写" in _md and "自动改 0 处／待人工 0 词" in _md
      and "可自动改候选 3 处" in _md)
check("Q8-18 CLI 非 --fix：报告无「修正稿：」行（未请求 --fix）",
      "修正稿：" not in _md)
check("Q8-19 CLI 报告 section/subsection 分表（不混排）",
      "section（实词大写；只报不改）" in _md
      and "subsection（句首式；自动改/候选见上节）" in _md
      and "section 0 处 / subsection 3 处" in _md)

# --fix 默认 auto：写副本、subsection 改句首式
_rc, _out = _run("--tex", _p, "--lang", "en", "--fix")
_copy = os.path.join(os.path.dirname(_p), "CaseEn_一校核查后.tex")
_md = open(os.path.join(os.path.dirname(_p), "CaseEn_一校核查.md"), encoding="utf-8").read()
_ctex = open(_copy, encoding="utf-8").read()
check("Q8-20 CLI --fix auto：写出副本且 3 个 subsection 句首式",
      os.path.isfile(_copy)
      and "\\subsection{Joint intentionality: Second-personal coordination between "
          "particular individuals}" in _ctex
      and _ctex.count("\\subsection{") == 3, _ctex[:200])
check("Q8-21 CLI --fix auto：报告「自动改 3 处」",
      "自动改 3 处／待人工 0 词" in _md)

# off：不出现 EN-06 段、不发散写文件
_p_off = _write("CaseEnOff", _EN_TEX.replace("\\section{Introduction}",
                                             "\\section{The structure of things}"))
_rc, _out = _run("--tex", _p_off, "--lang", "en", "--en-subsection-case", "off")
_md_off = open(os.path.join(os.path.dirname(_p_off), "CaseEnOff_一校核查.md"),
               encoding="utf-8").read()
check("Q8-22 CLI off：不再有 EN-06 标题大小写段",
      "EN-06 标题大小写" not in _md_off, _md_off[:200])
check("Q8-23 CLI off + --fix：只有 EN-06 候选时不写副本",
      not os.path.isfile(os.path.join(os.path.dirname(_p_off),
                                      "CaseEnOff_一校核查后.tex"))
      if _run("--tex", _p_off, "--lang", "en", "--fix",
              "--en-subsection-case", "off") else False)

# ── Q9：0 改不写、有改写副本、报告行逐字 ────────────────────────────────────
print("\n[Q9] --fix 写副本条件与固定报告行")
_CLEAN = "\\section{Introduction}\n\nBody text of the manuscript, long enough to look like prose.\n"
_pc = _write("Clean", _CLEAN)
_rc0, _out0 = _run("--tex", _pc, "--lang", "en", "--fix")
_d = os.path.dirname(_pc)
_md0 = open(os.path.join(_d, "Clean_一校核查.md"), encoding="utf-8").read()
check("Q9-1 0 改：不产生 `_一校核查后.tex`",
      not os.path.isfile(os.path.join(_d, "Clean_一校核查后.tex")))
check("Q9-2 0 改：报告固定行逐字「未另写」",
      "修正稿：未另写（未发现可自动修改项；输入文件未动）" in _md0, _md0[-300:])
check("Q9-3 0 改：stdout 明确「未另写文件」",
      "未发现可自动修改项，未另写文件（输入文件未动）" in _out0)
check("Q9-4 0 命中退出码 0", _rc0 == 0, f"rc={_rc0}")

# 既有同名产物不被覆盖
with open(os.path.join(_d, "Clean_一校核查后.tex"), "w", encoding="utf-8") as f:
    f.write("PRESENT\n")
_run("--tex", _pc, "--lang", "en", "--fix")
check("Q9-5 0 改：既有的同名产物不被覆盖",
      open(os.path.join(_d, "Clean_一校核查后.tex"), encoding="utf-8").read() == "PRESENT\n")

# zh mover：有改动 → 写副本、报告「位序调整 1 处」
_pm = _write("Mv", "甲说，\\footnote{注}乙说。\n")
_rcm, _outm = _run("--tex", _pm, "--lang", "zh", "--fix")
_dm = os.path.dirname(_pm)
_copy_m = os.path.join(_dm, "Mv_一校核查后.tex")
_mdm = open(os.path.join(_dm, "Mv_一校核查.md"), encoding="utf-8").read()
check("Q9-6 有改（位序搬移）：写副本且内容正确",
      _rcm == 1 and os.path.isfile(_copy_m)
      and open(_copy_m, encoding="utf-8").read() == "甲说\\footnote{注}，乙说。\n",
      repr(open(_copy_m, encoding="utf-8").read() if os.path.isfile(_copy_m) else ""))
check("Q9-7 有改：报告固定行逐字（自动改 0 处＋位序调整 1 处）",
      re.search(r"^修正稿：已另写 `" + re.escape(_copy_m)
                + r"`（自动改 0 处＋位序调整 1 处；输入文件未动）$",
                _mdm, re.M) is not None, _mdm[-300:])

# en 自动改（EN-03 弯引号）→ 报告「自动改 2 处＋位序调整 0 处」
_pt = _write("Typo", "He said “hi”.\n")
_rct, _outt = _run("--tex", _pt, "--lang", "en", "--fix")
_dt = os.path.dirname(_pt)
_copy_t = os.path.join(_dt, "Typo_一校核查后.tex")
_mdt = open(os.path.join(_dt, "Typo_一校核查.md"), encoding="utf-8").read()
check("Q9-8 有改（自动改）：副本内容正确、报告条数=实际",
      os.path.isfile(_copy_t)
      and open(_copy_t, encoding="utf-8").read() == "He said ``hi''.\n"
      and re.search(r"^修正稿：已另写 `" + re.escape(_copy_t)
                    + r"`（自动改 2 处＋位序调整 0 处；输入文件未动）$",
                    _mdt, re.M) is not None,
      _mdt[-300:])

# 没加 --fix：报告不出现「修正稿：」行（fix_requested 区分）
_rct2, _ = _run("--tex", _pt, "--lang", "en")
_mdt2 = open(os.path.join(_dt, "Typo_一校核查.md"), encoding="utf-8").read()
check("Q9-9 没加 --fix ≠ 加了但 0 改：报告不出现「修正稿：」行",
      "修正稿：" not in _mdt2)

print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print(f"全部 {PASS} 项断言通过")
sys.exit(0)
