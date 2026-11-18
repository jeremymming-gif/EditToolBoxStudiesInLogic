#!/usr/bin/env python3.14
r"""F6 别家文档类多作者 + 多单位（`slcn/texfront.py` + `slcn/build.py`）的合成样例套件。

背景
----
Springer Nature 的 `sn-jnl.cls`（以及 authblk 一类）把作者写成**重复的 `\author`**，
用可选参数标单位号：

    \author{Test Author A}
    \author[2]{Test Author B}
    \affil[1]{Test University, Test City}
    \affil[2]{Another Test University}

B-276 的 `author_from_document_author()` 只取**第一**个 `\author`，
`parse_tex_frontmatter()` 的 `_first_cmd_value(pre, "affil", …)` 只取**第一个**
`\affil`，`TexFrontmatter` 也只有 `author`/`affiliation` 两个单字段 → `--lang en`
转出来 `\mysecondauthor` 仍是模板默认 `null`，`\affil[2]` 的内容整个消失。
`build.py` 的 `author_fields()` 早就支持多作者（`_ORDINALS`/`_AUTHOR_SLOTS`），
缺的是「把 texfront 解出来的多作者列表接到槽位上」。

修法与分工（本套件钉住的设计）
------------------------------
* `texfront.py` **只解析、不命名槽位**：新增 `authors_from_document_author()`、
  `TexFrontmatter.authors` / `author_affiliations` / `affiliations`，以及
  `unmapped_affiliations()`；序数词表（second/third/…）**只在 `build.py` 有一份**
  （`_ORDINALS`/`_AUTHOR_MACRO`），避免两处各拼一套互相错位。
* `build.py` 的 B-276 回退块读 `authors[1:]` / `author_affiliations[1:]`，用
  `_AUTHOR_MACRO()` 填 `\mysecondauthor`…`\myfifthauthor` 与对应 `\my<N>affiliation`；
  只填「模板真有、且还没被写过」的槽。
* 第二单位若**无法**按 `\author[N]`↔`\affil[N]` 对上号：**不猜、不编**，
  在 `warnings` 里如实报出来。

本套件钉住（①–⑧）
  ① SN 形态：两位作者、两个单位、`author_affiliations` 对齐、`unmapped` 为空；
  ② 编号对齐：`\author[1]/[2]` ↔ `\affil[1]/[2]`；多出来的 `\affil[3]` 进 unmapped；
  ③ 保守边界：英文倒装 `Han, Meimei` 与中文 `张三，李四` 都**不拆**（B-217 ④）；
     单作者稿的 `authors` 必须是空元组（老口径逐字节不变）；
  ④ `as_dict()` 只出 str（下游 `test_tex_frontmatter.py` 会遍历值），
     且 `template_values()` **不出** `mysecondauthor`（槽位命名归 build.py）；
  ⑤ `build_latex(--lang en)` 端到端：产物含第二作者 + 第二单位，第一作者/单位/题名不坏；
  ⑥ 单作者稿回归：产物里 `\mysecondauthor` 仍是 `null`，没有被多作者逻辑碰到；
  ⑦ 对不上号的第三单位：产物里不出现，`warnings` 里有一句可读的说明；
  ⑧ 案例 71 的空白折叠（`author_fields()` 单作者分支）没被改坏。

**样例一律合成名/合成单位**（Test Author A/B、Test University、Another Test
University 等），不含真实作者、稿件内容、邮箱（B-269 红线）。

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh、**不跑 xelatex**
（`compile_requested=False`）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_sn_multi_author_20261117.py
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402
from slcn.texfront import (  # noqa: E402
    TexFrontmatter,
    authors_from_document_author,
    parse_tex_frontmatter,
)

FAILS: list[str] = []


def check(label: str, cond: object, detail: str = "") -> None:
    """一条断言；失败记进 FAILS（末行汇总，退出码红）。"""
    if cond:
        print(f"  ✓ {label}")
    else:
        FAILS.append(label)
        print(f"  ✗ {label}  {detail}")


TMP = tempfile.mkdtemp(prefix="slcn_snauthor_")


def write(name: str, text: str) -> str:
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def build_en(tex: str, tag: str):
    """把 tex 走一遍 `build_latex(lang="en")`（不编译），返回 (BuildResult, 产物 tex)。"""
    src = write(f"{tag}.tex", tex)
    res = build.build_latex(
        src, os.path.join(TMP, "out_" + tag), basename=tag, lang="en",
        verbose=False, compile_requested=False,
    )
    with open(res.tex_path, encoding="utf-8") as f:
        return res, f.read()


def live_lines(tex: str, macro: str) -> list[str]:
    """产物里**未被注释**的定义行（`\\newcommand{\\x}` / `\\renewcommand{\\x}`）。"""
    needle = "\\" + macro + "}"
    return [ln for ln in tex.splitlines()
            if not ln.lstrip().startswith("%") and needle in ln]


def macro_value(tex: str, macro: str) -> str:
    """取产物里某个 `\\my*` 宏的实际值。

    模板的排版是 `\\newcommand{\\myfirstauthor}` 一行、`{值}` 下一行，也允许
    值写在同一行（`\\newcommand{\\myfirstaffiliation}{Test University}`）。
    只认**未被注释**的定义；同一宏既有 `\\newcommand` 又有 `\\renewcommand` 时
    **以后者为准**（模板惯例：`\\newcommand{\\mysecondauthor}{null}` 打底，
    `\\renewcommand{\\mysecondauthor}{…}` 才是本稿真值）。
    `{\\myfirstauthorCN}` 这类 CN 宏不会被 `\\myfirstauthor}` 命中（右花括号挡住）。
    """
    lines = tex.splitlines()
    needle = "\\" + macro + "}"
    found = ""
    for i, line in enumerate(lines):
        if line.lstrip().startswith("%") or needle not in line:
            continue
        after = line.partition(needle)[2].lstrip()
        if after.startswith("{"):
            depth, buf = 0, []
            for ch in after:
                if ch == "{":
                    depth += 1
                    if depth == 1:
                        continue
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        found = "".join(buf).strip()
                        break
                if depth >= 1:
                    buf.append(ch)
            continue
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j].strip()
            if not nxt or nxt.startswith("%"):
                continue
            if nxt.startswith("{") and nxt.endswith("}"):
                found = nxt[1:-1].strip()
            break
    return found


# ══════════════════════════════════════════════════════════════════════════
# ① SN 形态：两位作者 + 两个单位
# ══════════════════════════════════════════════════════════════════════════
_SN = r"""\documentclass{sn-jnl}
\title{Synthetic SN Article Title}
\author{Test Author A}
\author[2]{Test Author B}
\affil[1]{Test University, Test City}
\affil[2]{Another Test University}
\keywords{synthetic, acceptance}
\begin{document}
\maketitle
\section{Intro}
Synthetic body text with a citation \citep{key1}.
\end{document}
"""

print("① SN 形态（\\author / \\author[2] / \\affil[1] / \\affil[2]）")
fm = parse_tex_frontmatter(_SN)
check("① 能解析出前言", fm is not None)
fm = fm or TexFrontmatter()
check("① authors 有两位（原来是 1 位）",
      fm.authors == ("Test Author A", "Test Author B"), repr(fm.authors))
check("① author 仍是第一作者（老字段不变）",
      fm.author == "Test Author A", repr(fm.author))
check("① affiliations 两个都在（原来只剩第一个）",
      fm.affiliations == ("Test University, Test City", "Another Test University"),
      repr(fm.affiliations))
check("① author_affiliations 与 authors 逐位对齐",
      fm.author_affiliations == ("Test University, Test City", "Another Test University"),
      repr(fm.author_affiliations))
check("① 没有对不上号的单位（unmapped 为空）",
      fm.unmapped_affiliations() == (), repr(fm.unmapped_affiliations()))
check("① 第一作者单位沿用第一个 \\affil",
      fm.affiliation == "Test University, Test City", repr(fm.affiliation))
check("① authors_from_document_author() 与字段一致",
      authors_from_document_author(_SN) == ("Test Author A", "Test Author B"),
      repr(authors_from_document_author(_SN)))

# ══════════════════════════════════════════════════════════════════════════
# ② 编号对齐：\author[N] ↔ \affil[N]；多出来的单位进 unmapped
# ══════════════════════════════════════════════════════════════════════════
print("② 编号对齐与多余单位")
_NUM = r"""\documentclass{article}
\title{Synthetic Numbered Title}
\author[1]{Test Author A}
\author[2]{Test Author B}
\affil[1]{First Test University}
\affil[2]{Second Test University}
\affil[3]{Third Test University}
\begin{document}
\maketitle
Body.
\end{document}
"""
fm2 = parse_tex_frontmatter(_NUM) or TexFrontmatter()
check("② 两位作者", fm2.authors == ("Test Author A", "Test Author B"), repr(fm2.authors))
check("② 前两个单位按编号对上作者",
      fm2.author_affiliations == ("First Test University", "Second Test University"),
      repr(fm2.author_affiliations))
check("② 第三个单位没人认领 → unmapped（不硬塞给某人）",
      fm2.unmapped_affiliations() == ("Third Test University",),
      repr(fm2.unmapped_affiliations()))

# 作者没写编号（SN 的第一位作者就是这样），\affil[1] 仍应归第一位
_NOOPT_FIRST = r"""\documentclass{sn-jnl}
\author{Test Author A}
\author[2]{Test Author B}
\affil[1]{Test University, Test City}
\affil[2]{Another Test University}
\begin{document}
\maketitle
\end{document}
"""
fm3 = parse_tex_frontmatter(_NOOPT_FIRST) or TexFrontmatter()
check("② 第一位作者没写 [1]，仍按序号取到 \\affil[1]",
      fm3.author_affiliations[:1] == ("Test University, Test City",),
      repr(fm3.author_affiliations))

# ══════════════════════════════════════════════════════════════════════════
# ③ 保守边界：逗号不拆；单作者稿 authors 为空
# ══════════════════════════════════════════════════════════════════════════
print("③ 保守边界（B-217 ④：逗号是姓名的一部分）")
_FMT = r"""\documentclass{{article}}
\author{{{who}}}
\begin{{document}}
x
\end{{document}}
"""


def authors_of(who: str) -> tuple[str, ...]:
    fmx = parse_tex_frontmatter(_FMT.format(who=who)) or TexFrontmatter()
    return fmx.authors


check("③ 英文倒装 `Han, Meimei` 不拆（切了就凭空多个人）",
      authors_of("Han, Meimei") == (), repr(authors_of("Han, Meimei")))
check("③ 中文逗号 `张三，李四` 不拆",
      authors_of("张三，李四") == (), repr(authors_of("张三，李四")))
check("③ 单作者稿 authors 是空元组（老口径逐字节不变）",
      authors_of("Test Author A") == (), repr(authors_of("Test Author A")))
check("③ 单中文作者 authors 为空",
      authors_of("李雷") == (), repr(authors_of("李雷")))
check("③ 顶层 \\and 能拆",
      authors_of(r"Test Author A \and Test Author B")
      == ("Test Author A", "Test Author B"),
      repr(authors_of(r"Test Author A \and Test Author B")))
check("③ 字面 ` and ` 能拆",
      authors_of("Test Author A and Test Author B")
      == ("Test Author A", "Test Author B"),
      repr(authors_of("Test Author A and Test Author B")))
check("③ 中文顿号能拆",
      authors_of("李雷、韩梅梅") == ("李雷", "韩梅梅"), repr(authors_of("李雷、韩梅梅")))
check("③ \\thanks 不进作者名",
      authors_of(r"Test Author A\thanks{Synthetic Funder}")
      == (), repr(authors_of(r"Test Author A\thanks{Synthetic Funder}")))

# ══════════════════════════════════════════════════════════════════════════
# ④ 契约：as_dict() 只出 str；template_values() 不出第二作者槽
# ══════════════════════════════════════════════════════════════════════════
print("④ 旧契约不变")
_d = fm.as_dict()
check("④ as_dict() 的值全是 str（下游会遍历值做子串判断）",
      all(isinstance(v, str) for v in _d.values()), repr({k: type(v) for k, v in _d.items()}))
check("④ as_dict() 没有多出元组字段（值全是 str 即可）",
      set(_d) <= {"title", "running_title", "author", "affiliation", "email",
                  "abstract", "keywords", "thanks", "grants"}, repr(sorted(_d)))
check("④ 单作者稿的 as_dict() 仍只多出它自己的字段",
      set((parse_tex_frontmatter(_FMT.format(who="李雷")) or TexFrontmatter()).as_dict())
      == {"author"})
_TV = fm.template_values({"mytitle", "myfirstauthor", "myfirstaffiliation",
                          "mysecondauthor", "mysecondaffiliation"})
check("④ template_values() 仍给第一作者",
      _TV.get("myfirstauthor") == "Test Author A", repr(_TV))
check("④ template_values() **不**给 mysecondauthor（槽位命名只在 build.py）",
      "mysecondauthor" not in _TV, repr(sorted(_TV)))

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 端到端：--lang en 产物填上第二作者 + 第二单位
# ══════════════════════════════════════════════════════════════════════════
print("⑤ build_latex(lang=en) 端到端（SN 稿）")
res, tex = build_en(_SN, "sn_multi")
check("⑤ 第二作者进了模板槽（原来一直是 null）",
      macro_value(tex, "mysecondauthor") == "Test Author B",
      repr(macro_value(tex, "mysecondauthor")))
check("⑤ 第二作者走的是 \\renewcommand（模板留的注释槽被启用）",
      any("\\renewcommand{\\mysecondauthor}" in ln for ln in live_lines(tex, "mysecondauthor")),
      repr(live_lines(tex, "mysecondauthor")))
check("⑤ 第二单位不再丢（原来整个消失）",
      macro_value(tex, "mysecondaffiliation") == "Another Test University",
      repr(macro_value(tex, "mysecondaffiliation")))
check("⑤ 模板示例单位没被当成本稿内容留下",
      "Department 2, University 2" not in tex)
check("⑤ 第一作者没坏", macro_value(tex, "myfirstauthor") == "Test Author A",
      repr(macro_value(tex, "myfirstauthor")))
check("⑤ 第一单位没坏",
      macro_value(tex, "myfirstaffiliation") == "Test University, Test City",
      repr(macro_value(tex, "myfirstaffiliation")))
check("⑤ 题名没坏", macro_value(tex, "mytitle") == "Synthetic SN Article Title",
      repr(macro_value(tex, "mytitle")))
check("⑤ 第三作者槽仍是模板默认（没有凭空造人）",
      "\\newcommand{\\mythirdauthor}{null}" in tex)
check("⑤ 正文与引用都在",
      "Synthetic body text with a citation" in tex and "key1" in tex)
check("⑤ 没把第二单位报成「未识别」",
      not any("第二作者单位" in lbl for lbl, _m in res.undetected_fields),
      repr(res.undetected_fields))

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 单作者稿回归：多作者逻辑不得碰它
# ══════════════════════════════════════════════════════════════════════════
print("⑥ 单作者稿回归")
_ONE = r"""\documentclass{article}
\title{Synthetic Single Title}
\author{Test Author A}
\affil{Test University}
\begin{document}
\maketitle
Body.
\end{document}
"""
res1, tex1 = build_en(_ONE, "single")
check("⑥ \\mysecondauthor 仍是模板默认 null",
      "\\newcommand{\\mysecondauthor}{null}" in tex1)
check("⑥ 没有为第二作者写 \\renewcommand",
      not any("\\renewcommand{\\mysecondauthor}{" in ln for ln in live_lines(tex1, "mysecondauthor")),
      repr(live_lines(tex1, "mysecondauthor")))
check("⑥ 第一作者/单位照旧",
      macro_value(tex1, "myfirstauthor") == "Test Author A"
      and macro_value(tex1, "myfirstaffiliation") == "Test University",
      f"{macro_value(tex1, 'myfirstauthor')!r} / {macro_value(tex1, 'myfirstaffiliation')!r}")
check("⑥ 没有多余的单位告警",
      not any("未能对应到作者" in w for w in res1.warnings), repr(res1.warnings))

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 对不上号的单位：不写进产物，但要如实告警
# ══════════════════════════════════════════════════════════════════════════
print("⑦ 对不上号的单位：不编，只告警")
res2, tex2 = build_en(_NUM, "numbered")
check("⑦ 第二作者/第二单位仍正确落槽",
      macro_value(tex2, "mysecondauthor") == "Test Author B"
      and macro_value(tex2, "mysecondaffiliation") == "Second Test University",
      f"{macro_value(tex2, 'mysecondauthor')!r} / {macro_value(tex2, 'mysecondaffiliation')!r}")
check("⑦ 第三单位没被硬塞进任何槽", "Third Test University" not in tex2)
check("⑦ warnings 里有一句可读说明",
      any("未能对应到作者" in w and "Third Test University" in w for w in res2.warnings),
      repr([w for w in res2.warnings if "单位" in w]))

# ══════════════════════════════════════════════════════════════════════════
# ⑧ 案例 71：author_fields() 单作者空白折叠没被改坏
# ══════════════════════════════════════════════════════════════════════════
print("⑧ 案例 71 空白折叠（author_fields 单作者分支）")
check("⑧ 单中文作者 + 两侧空格 → 折叠成 `\\kaishu 李雷`",
      build.author_fields("  李雷  ") == {"myfirstauthor": "\\kaishu 李雷"},
      repr(build.author_fields("  李雷  ")))
check("⑧ 空作者 → null",
      build.author_fields("") == {"myfirstauthor": "null"},
      repr(build.author_fields("")))
check("⑧ 多作者分支照旧（`\\and` 分隔）",
      build.author_fields("Test Author A and Test Author B")
      == {"myfirstauthor": "Test Author A", "mysecondauthor": "Test Author B"},
      repr(build.author_fields("Test Author A and Test Author B")))
check("⑧ 多作者中文分支照旧",
      build.author_fields("李雷、韩梅梅")
      == {"myfirstauthor": "\\kaishu 李雷", "mysecondauthor": "\\kaishu 韩梅梅"},
      repr(build.author_fields("李雷、韩梅梅")))

# ── 汇总 ────────────────────────────────────────────────────────────────
print("=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print(f"  · {f}")
    sys.exit(1)
print("失败 0 项（全部通过）")
sys.exit(0)
