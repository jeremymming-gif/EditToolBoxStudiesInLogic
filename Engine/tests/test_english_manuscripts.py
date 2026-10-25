#!/usr/bin/env python3.14
r"""英文来稿（期 88 实例测试用例三）暴露问题的回归判据（**第 121 条**，2026-10-17）。

期 88 七篇英文来稿实测：编译 4/7、发现 3 个引擎 bug + 4 个英文 Word 解析缺陷。
本套件逐条钉住修复（纯离线；不跑 xelatex）：

  ① `\bibliographystyle` 在正文里（03 Zhentao_Zhang）→ 取正文时截掉；
  ② 无花括号 `\newcommand\revieww` 要能解析/迁移（04 Chen_Liu）；
  ③ 正文 `\includegraphics` 引用的图片要复制进产物目录（04 Chen_Liu）；
  ④ `...` 后紧跟字母时输出 `\ldots{}`，不能粘成 `\ldotsx`（05 YouAn_Su）；
  ⑤ 引擎自己写的 `<产物>.<时间戳>.bak` 算自己的产物（重跑不被拦）；
  ⑥ 英文「论述性脚注」降级为纯注释（01/02，且不含中文——中文脚注零变化）；
  ⑦ 英文文末参考文献「全名 + 年份在后」格式能解析（06 Zhixi_Chen）；
  ⑧ 英文无样式小标题：`N)` 编号 + 无编号 Title Case 短段（02/07）。
  ⑨ 真实稿语料在时做端到端抽查（不在则 SKIP）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_english_manuscripts.py
"""
import os
import re
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402
from slcn import build, rules  # noqa: E402
from slcn import docxsource as DS  # noqa: E402
from slcn import sources  # noqa: E402
from slcn.latex import (  # noqa: E402
    copy_referenced_graphics,
    find_all_macros,
    parse_macros,
)

FAILS = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="slcn_en88_")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def _w(name, text):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _build(body_name, out_name, **kw):
    out = os.path.join(TMP, "out_" + out_name)
    res = build.build_latex(os.path.join(TMP, body_name), out,
                            basename=out_name, verbose=False, **kw)
    return res, open(res.tex_path, encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════════
# ① \bibliographystyle 截断
# ══════════════════════════════════════════════════════════════════════════
BODY = "\\begin{document}\n正文。\n\\bibliographystyle{plain}\n\\bibliography{refs}\n\\end{document}\n"
check("① extract_body 在 \\bibliographystyle 处截断",
      "bibliographystyle" not in sources.extract_body_text(BODY))

# ══════════════════════════════════════════════════════════════════════════
# ② 无花括号 \newcommand
# ══════════════════════════════════════════════════════════════════════════
TEX = r"""\documentclass{article}
\newcommand\revieww[1]{{\color{red}#1}}
\newcommand*{\starred}{S}
\newcommand{\normal}{N}
\begin{document}
用 \revieww{修订} \starred \normal。
\end{document}
"""
_w("macros/main.tex", TEX)
_names = set(parse_macros(TEX))
check("② 无花括号 \\newcommand\\name 能解析", "revieww" in _names)
check("② \\newcommand* 变体能解析", "starred" in _names)
_res, _tex = _build("macros/main.tex", "macros")
check("② 无花括号定义会迁进产物", "\\newcommand\\revieww" in _tex or "\\newcommand{\\revieww}" in _tex)

# ══════════════════════════════════════════════════════════════════════════
# ③ \includegraphics 引用的图片复制
# ══════════════════════════════════════════════════════════════════════════
os.makedirs(os.path.join(TMP, "gfx/Figs"), exist_ok=True)
for rel in ("Archit.pdf", "Figs/figure3.pdf", "pic.png"):
    with open(os.path.join(TMP, "gfx", rel), "wb") as f:
        f.write(b"%PDF-1.4\n" if rel.endswith(".pdf") else b"\x89PNG\r\n")
_w("gfx/main.tex", r"""\documentclass{article}
\begin{document}
\includegraphics[width=\linewidth]{Archit.pdf}
\includegraphics{Figs/figure3.pdf}
\includegraphics{pic}
\end{document}
""")
_out = os.path.join(TMP, "out_gfx")
os.makedirs(_out, exist_ok=True)
_warns = []
_copied = copy_referenced_graphics(
    open(os.path.join(TMP, "gfx/main.tex"), encoding="utf-8").read(), "",
    os.path.join(TMP, "gfx"), _out, _warns,
)
# 期望值用 `/` 归一：Windows 上 relpath 返回 `Figs\figure3.pdf`，引擎产物里的
# `\includegraphics` 路径仍是源稿的 `/`，所以这里比的是「相对路径语义」而非分隔符。
_copied_norm = {x.replace("\\", "/") for x in _copied}
check("③ 图片复制：带路径/不带扩展名/子目录都复制成功",
      _copied_norm == {"Archit.pdf", "Figs/figure3.pdf", "pic.png"}, str(_copied))
check("③ 复制位置保持相对路径",
      os.path.isfile(os.path.join(_out, "Figs/figure3.pdf")))

# ══════════════════════════════════════════════════════════════════════════
# ④ 省略号后接字母
# ══════════════════════════════════════════════════════════════════════════
r = rules.apply_rules("value t(x_1,...x_n) and a... b", lang="en")
check("④ `...x` → `\\ldots{}x`（隔离）", "\\ldots{}x" in r.text, r.text)
check("④ 普通 `... b` 仍是 `\\ldots b`", "\\ldots b" in r.text)
r_zh = rules.apply_rules("省略号...不处理", lang="zh")
check("④ 中文通道不受影响", "..." in r_zh.text and "\\ldots" not in r_zh.text)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 自己的 .bak 算自己的产物
# ══════════════════════════════════════════════════════════════════════════
check("⑤ `<产物>.<时间戳>.bak` 算自己的产物",
      convert.is_own_product("Lei_Li.tex.20261017-211835-551078.bak", "Lei_Li"))
check("⑤ 非本工具文件仍会被拦",
      not convert.is_own_product("别人的稿子.docx", "Lei_Li"))

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 英文论述性脚注 → 纯注释
# ══════════════════════════════════════════════════════════════════════════
check("⑥ 只有 note 的解析结果被判为「不是文献条目」",
      DS._entry_is_note_only({"note": "Some prose about King 2001."}))
check("⑥ 有 author/title 的条目不是 note-only",
      not DS._entry_is_note_only({"author": "A, B", "title": "T"}))

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 英文「全名 + 年份在后」参考文献格式
# ══════════════════════════════════════════════════════════════════════════
LINES = [
    "Jacobs, Scott, Jackson, Sally, 1989. Building a model of conversational argument. "
    "In Dervin, Brenda, et al. (Eds.), Rethinking communication, 153-171.",
    "Perelman, Chaim, Olbrechts-Tyteca, Lucie, 1969. The new rhetoric: A treatise on "
    "argumentation, Trans. By Wilkinson, John, Notre Dame: University of Notre Dame Press.",
    "Van Eemeren, Frans et al. 2014. Handbook of Argumentation Theory. New York: Springer.",
]
_e = DS.parse_reference_line(LINES[0])
check("⑦ 全名 + 年份在后的英文条目能解析", _e is not None and _e.year == "1989",
      str(_e))
check("⑦ 作者转成编辑体例（Surname, I. and …）",
      _e is not None and "Jacobs, S." in _e.author and "Jackson, S." in _e.author,
      getattr(_e, "author", ""))
_e2 = DS.parse_reference_line(LINES[2])
check("⑦ `et al.` 形式也能解析", _e2 is not None and _e2.year == "2014",
      str(_e2))
# 反例：既有格式不能被抢
_e3 = DS.parse_reference_line("Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall.")
check("⑦ 既有 GB 英文格式不受影响（[M] 仍解析）", _e3 is not None and _e3.year == "1970")
_e4 = DS.parse_reference_line("李雷. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.")
check("⑦ 中文条目不受影响", _e4 is not None and _e4.year == "2022")

# ══════════════════════════════════════════════════════════════════════════
# ⑧ 英文小标题
# ══════════════════════════════════════════════════════════════════════════
lv, h = DS.classify_en_heading("2) Problems with Falessi and Schang’s Reconstruction")
check("⑧ 半角 `N)` 英文小标题识别为二级", lv == 2 and "Problems" in h, f"{lv} {h}")
lv2, _ = DS.classify_en_heading("3. Introduction")
check("⑧ 既有 `N.` 英文标题不受影响", lv2 == 1)
check("⑧ 无样式 Title Case 短段被判为「像标题」",
      DS._looks_plain_en_title("On Logical Constants and the Metaphysics of Logic"))
check("⑧ 带冒号的正文句子不被误判",
      not DS._looks_plain_en_title("On the status of non-classical logical systems: Of course…"))
check("⑧ 普通英文句子不被误判",
      not DS._looks_plain_en_title("This is just a normal sentence in the body"))

# ══════════════════════════════════════════════════════════════════════════
# ⑨ 真实稿抽查（需要稿件库；CI 上 SKIP）
# ══════════════════════════════════════════════════════════════════════════
from slcn.paths import manuscripts_root  # noqa: E402

_root = manuscripts_root()
_period = os.path.join(_root, "2026年第3期(88)") if _root else ""
if not _period or not os.path.isdir(_period):
    skip("⑨ 真实稿抽查（期 88 英文来稿）", "本机没有稿件库 / 期 88 目录")
else:
    def _docx(rel):
        for d in os.listdir(_period):
            p = os.path.join(_period, d, rel)
            if os.path.exists(p):
                return p
        return ""

    tuomo = _docx("original/CONSEQ2026FINAL.docx")
    zhixi = _docx("original/Revised Manuscript 5.23.docx")
    hu = _docx("Approved+2025+Williamson+Interview.docx")
    lu = _docx("original/Ockham Semantics Submission Studies in Logic revised version May 25 2026 source file.docx")
    if all((tuomo, zhixi, hu, lu)):
        d1 = DS.parse_docx(tuomo)
        # 短英文文献脚注（有 p./pp. 或出版社词）仍按文献条目保留；散文脚注降级为注释。
        check("⑨ 01 Tuomo_Aho：垃圾 bib 大幅减少（散文脚注不再进 bib）",
              len(d1.bib_entries) <= 12, f"{len(d1.bib_entries)}")
        check("⑨ 01 Tuomo_Aho：散文脚注保留为注释", d1.footnote_notes >= 40,
              f"{d1.footnote_notes}")
        d2 = DS.parse_docx(zhixi)
        check("⑨ 06 Zhixi_Chen：英文全名参考文献能批量解析", len(d2.refs) >= 40,
              f"{len(d2.refs)}")
        d3 = DS.parse_docx(hu)
        check("⑨ 07 Hu_Liu：无样式小标题识别 ≥ 8 个", len(d3.headings) >= 8,
              f"{len(d3.headings)}")
        d4 = DS.parse_docx(lu)
        check("⑨ 02 Lu_Jiang：`N)` 小标题补上（总数 ≥ 10）", len(d4.headings) >= 10,
              f"{len(d4.headings)}")
    else:
        skip("⑨ 真实稿抽查（期 88 英文来稿）", "期 88 有的 docx 不在原路径")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
