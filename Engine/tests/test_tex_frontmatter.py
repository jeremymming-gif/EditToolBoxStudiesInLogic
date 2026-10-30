#!/usr/bin/env python3.14
r"""B-276 标准 LaTeX 前言元数据解析（`slcn/texfront.py`）的合成样例套件。

背景
----
本刊模板用 ``\myfirstauthor``/``\mytitle``/… 一批 ``\my*`` 宏；作者交来的普通
article 只写标准的 ``\title{}``/``\author{}``/``\begin{abstract}``/``关键词：``。
B-275 只修了**输出名**回退，③ 搬运元数据仍只认 ``\my*`` → 这类稿子有 11 项
【未识别】。B-276 新增 ``texfront.py``：从**主文件**（``\input`` 不展开）保守读出
title / first author / affiliation / email / abstract / keywords / thanks / grants，
识别不了返回 ``None``，不猜。

本套件钉住（spec §3 判据 + 尾部 B-275 限界补充）
  ① 中/英合成样例：title / 第一作者 / 单位 / 邮箱 / 摘要 / 关键词 / 致谢 / 基金分类；
  ② 作者分隔：``\and``/``\\``/``\quad``/字面 ``and``/中英文逗号/顿号/分号只取首；
  ③ 剥嵌套 ``\thanks``、``\footnote``、字体/上标；注释掉的行不算；
  ④ 摘要内 ``\cite`` / ``\footnote``、关键词的三种写法（``\keywords{}`` /
     ``keywords`` 环境 / ``关键词：`` 行 / ``Keywords:`` 行，可包 ``\textbf``）；
  ⑤ 缺字段 → ``None``（空值、``null``、纯注释均视为无值）；
  ⑥ ``\begin{document}`` 之后才写 ``\author{}`` 的正文回退；authblk 的 ``\author[1]{}``；
  ⑦ 宏定义体（``\def\blfootnote{\xdef…\@footnotetext}``）不得被当成实际调用；
  ⑧ 模板 ``\my*`` 优先（``naming.author_from_tex_metadata_ex`` 来源仍报 ``\myfirstauthor``）。

**样例一律合成名/合成单位**（李雷、韩梅梅、王小明、张三、李四、示例大学、
Example University、Alice Brown 等），不含真实作者、期号、稿件路径（B-269 红线）。

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_tex_frontmatter.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.naming import author_from_tex_metadata_ex  # noqa: E402
from slcn.texfront import (  # noqa: E402
    TEMPLATE_FIELDS,
    TexFrontmatter,
    author_from_document_author,
    first_author_from_tex,
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


def fm_of(text: str) -> TexFrontmatter | None:
    return parse_tex_frontmatter(text)


# ── ① 中文标准稿：单作者 + 单位/基金 \thanks + 摘要内关键词行 ──────────────
_ZH = r"""\documentclass{article}
\title{示例标题：一个测试}
\author{李雷\thanks{示例大学哲学学院}\thanks{本文受示例基金项目（编号：000000）资助}}
\begin{document}
\begin{abstract}
这是摘要正文，引用\cite{示例文献}。
\par\noindent\textbf{关键词：}甲；乙；丙
\end{abstract}
\end{document}
"""

print("① 中文标准稿")
fm = fm_of(_ZH)
check("能识别出前言", fm is not None)
fm = fm or TexFrontmatter()
check("title", fm.title == "示例标题：一个测试", repr(fm.title))
check("author 取第一作者", fm.author == "李雷", repr(fm.author))
check("单位来自第一个 \\thanks", fm.affiliation == "示例大学哲学学院", repr(fm.affiliation))
check("基金来自第二个 \\thanks", bool(fm.grants) and "示例基金" in fm.grants and "000000" in fm.grants,
      repr(fm.grants))
check("致谢字段为空（两个 thanks 都已分类）", fm.thanks is None, repr(fm.thanks))
check("无邮箱", fm.email is None, repr(fm.email))
check("摘要不含关键词行", fm.abstract is not None and "这是摘要正文" in fm.abstract
      and "关键词" not in fm.abstract, repr(fm.abstract))
check("摘要内 \\cite 原样保留", fm.abstract is not None and "\\cite{示例文献}" in fm.abstract)
check("关键词", fm.keywords == "甲；乙；丙", repr(fm.keywords))
tpl = fm.template_values()
check("映射到模板槽位", {"mytitle", "myfirstauthor", "myfirstaffiliation", "mygrants",
                     "myabstract", "mykeywords"} <= set(tpl), repr(sorted(tpl)))
check("不填 EN 槽", not any(k.endswith("EN") for k in tpl), repr(sorted(tpl)))
check("中文稿不产生 myfirstemail/mythanks", "myfirstemail" not in tpl and "mythanks" not in tpl)
check("TEMPLATE_FIELDS 覆盖 9 个源字段", len(TEMPLATE_FIELDS) == 9, repr(TEMPLATE_FIELDS))
check("TEMPLATE_FIELDS: title→mytitle", TEMPLATE_FIELDS["title"] == "mytitle")
check("TEMPLATE_FIELDS: author→myfirstauthor", TEMPLATE_FIELDS["author"] == "myfirstauthor")
check("TEMPLATE_FIELDS: affiliation→myfirstaffiliation",
      TEMPLATE_FIELDS["affiliation"] == "myfirstaffiliation")

# ── ② 英文标准稿：字面 and + 显式单位/邮箱 + Keywords 行 + 摘要内引用/脚注 ──
_EN = r"""\documentclass{article}
\title{An Example Title}
\author{Alice Brown and Bob Smith}
\affiliation{Department of Examples, Example University}
\email{alice@example.edu}
\begin{document}
\begin{abstract}
This is the abstract with \cite{ref1} and a \footnote{note text}.
\end{abstract}
\par\noindent\textbf{Keywords:} alpha, beta
\end{document}
"""

print("② 英文标准稿")
fm = fm_of(_EN)
check("能识别出前言", fm is not None)
fm = fm or TexFrontmatter()
check("title", fm.title == "An Example Title", repr(fm.title))
check("字面 and 只取首作者", fm.author == "Alice Brown", repr(fm.author))
check("显式 \\affiliation", fm.affiliation == "Department of Examples, Example University",
      repr(fm.affiliation))
check("显式 \\email", fm.email == "alice@example.edu", repr(fm.email))
check("摘要内 \\cite 原样保留", fm.abstract is not None and "\\cite{ref1}" in fm.abstract)
check("摘要内 \\footnote 有内容也没解析崩", fm.abstract is not None
      and "This is the abstract" in fm.abstract)
check("Keywords: 行", fm.keywords == "alpha, beta", repr(fm.keywords))

# ── ③ 作者分隔符：取第一个，不猜 ─────────────────────────────────────────
print("③ 作者分隔符")
_SEP_CASES = [
    (r"\author{张三 \and 李四}", "张三", "\\and"),
    (r"\author{张三\\李四}", "张三", "换行 \\\\"),
    (r"\author{张三、李四}", "张三", "顿号"),
    (r"\author{张三;李四}", "张三", "分号"),
    (r"\author{张三，李四}", "张三", "全角逗号"),
    (r"\author{张三 \quad 李四 译}", "张三", "\\quad 译者行"),
    (r"\author{Alice Brown and Bob Smith}", "Alice Brown", "字面 and"),
]
for author_tex, want, why in _SEP_CASES:
    got = author_from_document_author(r"\begin{document}" + author_tex + "\n\\end{document}")
    check(f"分隔符 {why}", got == want, repr(got))
check("first_author_from_tex 与 author_from_document_author 同源",
      first_author_from_tex(r"\author{张三\\李四}") == "张三")

# ── ④ 剥嵌套 \thanks / \footnote / 字体 / 上标；嵌套花括号 ────────────────
print("④ 剥离与嵌套")
_STRIP_CASES = [
    (r"\author{李雷\thanks{示例{\em 大学}学院}}", "李雷", "嵌套花括号的 thanks"),
    (r"\author{李雷\thanks{示例大学哲学学院}\thanks{本文受示例基金资助}}", "李雷", "两个 thanks"),
    (r"\author{\kaishu 韩梅梅\footnote{示例脚注}}", "韩梅梅", "\\footnote 剥离"),
    (r"\author{王小明\textsuperscript{1}}", "王小明", "上标剥离"),
    (r"\author[1]{李雷}", "李雷", "authblk [1]"),
    (r"\author{李雷 示例大学}", "李雷", "姓名后紧跟单位"),
]
for author_tex, want, why in _STRIP_CASES:
    got = author_from_document_author(author_tex + "\n\\begin{document}\n\\end{document}")
    check(f"剥离 {why}", got == want, repr(got))

check("作者行里的嵌套 \\thanks 也作单位分类",
      (lambda f: f is not None and f.author == "李雷"
       and f.affiliation is not None and "学院" in f.affiliation)(
          fm_of(r"\title{示例}\author{李雷\thanks{示例{\em 大学}学院}}\begin{document}\end{document}")))

# ── ⑤ 注释掉的不算；正文内 \author 回退 ──────────────────────────────────
print("⑤ 注释 / 正文回退")
_TXT = ("% \\author{王小明}\n"
        "% \\title{旧标题}\n"
        "\\title{新标题}\n"
        "\\author{李雷}\n"
        "\\begin{document}\n正文\n\\end{document}\n")
check("注释掉的作者被忽略", author_from_document_author(_TXT) == "李雷",
      repr(author_from_document_author(_TXT)))
fm = fm_of(_TXT)
check("注释掉的标题被忽略", fm is not None and fm.title == "新标题", repr(fm and fm.title))
_BODY_AUTHOR = r"""\documentclass{article}
\title{示例标题}
\begin{document}
\author{李雷}
正文
\end{document}
"""
check("正文内 \\author 作回退", author_from_document_author(_BODY_AUTHOR) == "李雷",
      repr(author_from_document_author(_BODY_AUTHOR)))
fm = fm_of(_BODY_AUTHOR)
check("正文回退的作者也进元数据", fm is not None and fm.author == "李雷", repr(fm and fm.author))

# ── ⑥ 关键词三种写法（+ 英文行）──────────────────────────────────────────
print("⑥ 关键词")
check("\\keywords{} 命令",
      (lambda f: f is not None and f.keywords == "甲；乙")(fm_of(
          r"\title{示例}\keywords{甲；乙}\begin{document}\end{document}")))
check("keywords 环境",
      (lambda f: f is not None and f.keywords == "甲；乙")(fm_of(
          r"\title{示例}\begin{document}\begin{keywords}甲；乙\end{keywords}\end{document}")))
check("关键词：行",
      (lambda f: f is not None and f.keywords == "甲；乙")(fm_of(
          r"\title{示例}\begin{document}关键词：甲；乙\end{document}")))
check("关键字：行",
      (lambda f: f is not None and f.keywords == "甲；乙")(fm_of(
          r"\title{示例}\begin{document}\noindent 关键字：甲；乙\end{document}")))
check("Keywords: 行",
      (lambda f: f is not None and f.keywords == "alpha, beta")(fm_of(
          r"\title{Example}\begin{document}Keywords: alpha, beta\end{document}")))

# ── ⑦ 缺字段 → None；空值/null/纯注释视为无值 ────────────────────────────
print("⑦ 缺字段")
check("空文本 → None", fm_of("") is None)
check("纯注释 → None", fm_of("% 只有注释\n% 没有元数据\n") is None)
check("空 title → None",
      fm_of(r"\title{}\begin{document}正文\end{document}") is None)
check("null 值 → 无值",
      fm_of(r"\title{null}\begin{document}正文\end{document}") is None)
fm = fm_of(r"\author{李雷}\begin{document}\end{document}")
check("只有作者时其余字段为空", fm is not None and fm.author == "李雷"
      and fm.title is None and fm.abstract is None and fm.keywords is None
      and fm.thanks is None and fm.grants is None, repr(fm and fm.as_dict()))
check("无前言的纯正文 → None",
      fm_of(r"\documentclass{article}\begin{document}正文\end{document}") is None)
check("as_dict 只含识别出的字段",
      fm is not None and set(fm.as_dict()) == {"author"}, repr(fm and fm.as_dict()))

# ── ⑧ 宏定义体不得当调用；英文真实形态合成复刻（无个人信息）──────────────
print("⑧ 宏定义体 / 英文形态")
_EN_FORM = r"""\documentclass{article}
\title{Example--Type Theorems for Set Theories}
\def\blfootnote{\xdef\@thefnmark{}\@footnotetext}
\author{Michael Brown and Jane Doe}
\begin{document}
\maketitle
\blfootnote{Department of Examples, Example University\\
  \hspace*{1.8em} example@example.edu}
\begin{abstract}
A famous result, due to \cite{ref1}, characterizes the examples.
\end{abstract}
\end{document}
"""
fm = fm_of(_EN_FORM)
check("英文形态：能识别", fm is not None)
fm = fm or TexFrontmatter()
check("英文形态：title 保留内部 -- 与大小写", fm.title == "Example--Type Theorems for Set Theories",
      repr(fm.title))
check("英文形态：第一作者", fm.author == "Michael Brown", repr(fm.author))
check("英文形态：单位来自脚注地址块",
      fm.affiliation is not None and "Department of Examples" in fm.affiliation,
      repr(fm.affiliation))
check("英文形态：邮箱来自脚注地址块", fm.email == "example@example.edu", repr(fm.email))
check("英文形态：宏定义体不进致谢", fm.thanks is None, repr(fm.thanks))
check("英文形态：任何字段都不含定义体垃圾",
      all("xdef" not in v and "@footnote" not in v for v in fm.as_dict().values()),
      repr(fm.as_dict()))
check("英文形态：摘要内 \\cite 保留", fm.abstract is not None and "\\cite{ref1}" in fm.abstract)

# ── ⑨ 模板 \my* 优先（B-275 契约不变）────────────────────────────────────
print("⑨ 模板 \\my* 优先")
_MY = r"""\newcommand{\myfirstauthor}{王小明}
\author{李雷}
\begin{document}\end{document}
"""
check("naming 链 my* 优先", author_from_tex_metadata_ex(_MY) == ("王小明", "\\myfirstauthor"),
      repr(author_from_tex_metadata_ex(_MY)))
check("texfront 自身只解释 \\author（S3 才决定是否采用）",
      author_from_document_author(_MY) == "李雷", repr(author_from_document_author(_MY)))
check("\\myfirstauthor{null} 时回退 \\author",
      author_from_tex_metadata_ex(
          "\\newcommand{\\myfirstauthor}{null}\n\\author{李雷}") == ("李雷", "\\author"),
      repr(author_from_tex_metadata_ex(
          "\\newcommand{\\myfirstauthor}{null}\n\\author{李雷}")))

# ── ⑩ 中文形态合成复刻：摘要环境内结尾关键词行 + 两个 \thanks（05 结构）──
print("⑩ 中文形态复刻")
_ZH_FORM = r"""\documentclass{article}
\title{示例中文标题}
\author{张三\thanks{示例大学哲学学院}\thanks{本文受示例社科基金项目（编号：000000）资助}}
\begin{document}
\maketitle
\begin{abstract}
这是中文摘要正文，引用\cite{ref1}。
\par\noindent\textbf{关键词：}甲；乙；丙
\end{abstract}
\end{document}
"""
fm = fm_of(_ZH_FORM)
check("中文形态：能识别", fm is not None)
fm = fm or TexFrontmatter()
check("中文形态：author", fm.author == "张三", repr(fm.author))
check("中文形态：affiliation", fm.affiliation == "示例大学哲学学院", repr(fm.affiliation))
check("中文形态：grants", bool(fm.grants) and "社科基金" in fm.grants, repr(fm.grants))
check("中文形态：keywords", fm.keywords == "甲；乙；丙", repr(fm.keywords))
check("中文形态：thanks 空", fm.thanks is None, repr(fm.thanks))
check("中文形态：不产生 EN 槽",
      not any(k.endswith("EN") for k in fm.template_values()), repr(fm.template_values()))

# ── 汇总 ────────────────────────────────────────────────────────────────
print("=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print(f"  · {f}")
    sys.exit(1)
print("失败 0 项（全部通过）")
sys.exit(0)
