"""直接读 Word 的 XML，而不是用 python-docx 的 `paragraph.text`。

为什么要绕开 `paragraph.text`
------------------------------
`paragraph.text` 只是把 `<w:t>`（常规文字）拼起来，它会**静默丢掉**三样东西：

  1. `<m:oMath>` —— OMML 公式。陈波稿的 7 个逻辑符号（¬ ∧ ∨ → ↔ ∀ ∃）就是这么消失的，
     人工成品是 `否定$\\neg$、合取$\\land$`，我们却输出成 `否定、合取`（残句）。
  2. run 级格式 `<w:i/>` —— 斜体。Lu_Jiang 正文有 414 处斜体，人工转成 185 处
     `\\emph/\\textit`，我们一处都拿不到。
  3. `word/footnotes.xml` —— **整个脚注文件**。`doc.paragraphs` 只读 `word/document.xml`。
     Lu_Jiang 的 90 条参考文献全在脚注里（12,757 字符），因此连 `reference.bib`
     都没能生成，正文 72 处引用全部漏掉。

这三样加起来，仅 Lu_Jiang 一篇就丢了 17.4% 的内容、少 4 页。

本模块按文档顺序遍历 XML，把这些信息完整取回来。
"""

from __future__ import annotations

import os
import re
import warnings
import zipfile
from dataclasses import dataclass, field

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

W_DRAWING = f"{{{W_NS}}}drawing"
A_BLIP = f"{{{A_NS}}}blip"
R_EMBED = f"{{{R_NS}}}embed"

# Unicode 数学符号 → LaTeX（与 slcn/rules.py 的 UNICODE_MATH 保持同源）
from .rules import UNICODE_MATH  # noqa: E402


# ── OMML → LaTeX ──────────────────────────────────────────────────────────


def _tag(el) -> str:
    """本地标签名（去掉命名空间），仅用于 OMML 内部的结构判断。"""
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


W_R = f"{{{W_NS}}}r"
W_T = f"{{{W_NS}}}t"
W_P = f"{{{W_NS}}}p"
M_OMATH = f"{{{M_NS}}}oMath"

# ── 第四十四批①：OMML 复杂结构（nary 求和体 / m:d 定界符）──
# nary 的算子字符 → LaTeX（OMML 用 Unicode 字符；缺省 ∑）
NARY_OP = {
    "∑": r"\sum", "∏": r"\prod", "∐": r"\coprod",
    "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
    "⋃": r"\bigcup", "⋂": r"\bigcap", "⋁": r"\bigvee", "⋀": r"\bigwedge",
    "⨁": r"\bigoplus", "⨂": r"\bigotimes", "⨀": r"\bigodot",
    "⨄": r"\biguplus", "⨆": r"\bigsqcup",
}

def _delim_tex(name: str, default: str) -> str:
    r"""定界符 → LaTeX，并在**控制词定界符后补分隔**。

    `\left\langle`/`\right\rangle` 等以字母结尾：后接字母内容（`\left\langlec`）会被
    TeX 并成未定义控制词 `\langlec` → `Undefined control sequence` + `Missing delimiter`
    （第四十四批回归：87-5 Ruixue_Tang l.387）。补一个数学模式空格即解决（空格不进版面）。
    """
    tex = DELIM_LATEX.get(name, default)
    return tex + " " if tex and tex[-1].isascii() and tex[-1].isalpha() else tex


def _script_base(b: str) -> str:
    """给 sSub/sSup 的**基**加分组，避免双重上下标。

    基已带上下标（以 `}` 结尾，如 `P_{r}`）时再挂 `_`/`^` 会 `! Double subscript.`
    （第四十四批回归：repo-69-01 CiHua_Xu 的 `{∑}_{t'∈T} P_{r}_{(t')}`）。
    人工/作者 LaTeX（`copy_fom_word.tex`）同款写法是 `{P_r}_{(t')}` → 这里输出 `{P_{r}}_{(t')}`。
    """
    return "{" + b + "}" if b and b.endswith("}") else b


# m:d 的 begChr/endChr → LaTeX 定界符（Word 两种尖括号码位都收）
DELIM_LATEX = {
    "(": "(", ")": ")", "[": "[", "]": "]",
    "{": r"\{", "}": r"\}", "": ".",
    "⟨": r"\langle", "⟩": r"\rangle",
    "〈": r"\langle", "〉": r"\rangle",
    "〈": r"\langle", "〉": r"\rangle",
    "|": "|", "‖": r"\|", "∥": r"\|",
    "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil",
}

# ⚠️ 必须按**带命名空间的完整标签**判断，不能用本地名：
#    Word 的文字 run 是 <w:r>，OMML 的公式 run 是 <m:r>，去掉命名空间后都叫 "r"，
#    只比本地名会把公式里的 <m:t>¬</m:t> 又当成普通文字读一遍，
#    结果输出成 `否定$\lnot$¬`（符号重复）。这是实测踩到的坑。


# ── 第四十四批②：纯 Python OMML→MathML→LaTeX 栈（task-50，**默认关闭**）────
#
# 为什么存在：现有 `_conv()` 是自研启发式，结构覆盖有限（组件评估实测缺 nary 体/
# m:d 定界符/oMathPara display 等）。`vendor_omml/` 里 vendored 了
#   omml2mml.xsl（Apache-2.0，Microsoft Office Beta 070708 派生）
#   + mathml_to_latex（MIT，纯标准库）→ 149/149 真实公式可转、结构覆盖更强。
#
# ⚠️ 切换纪律（Lead 2026-10-19 拍板）：**第一批默认关**（`USE_OMML_STACK = False`），
#    只落代码/测试/fixture，产物逐字节不变；是否默认切换由后续回归+五期对照决定。
#    开关打开后仍保留旧 `_conv()` 作 fallback：新栈异常/空串 → 回退旧实现并 warning。
USE_OMML_STACK = False

#: None = 未加载；False = 加载失败（不再重试）；tuple = (XSLT, MathMLToLaTeX 类)
_OMML_STACK: object = None
_MATHML_WRAP = '<math xmlns="http://www.w3.org/1998/Math/MathML">%s</math>'
_OMML_CJK_RUN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+"
    r"(?:[ \t]*[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)*")


def _omml_stack_mathit_cjk(tex: str) -> str:
    r"""`\mathit{会 碎}` → `\text{会碎}`（mathml-to-latex 会把 CJK 塞进 \mathit）。"""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if not _OMML_CJK_RUN.match(inner.replace(" ", "")):
            return m.group(0)
        return "\\text{" + inner.replace(" ", "") + "}"
    return re.sub(r"\\mathit\{([^{}]*)\}", repl, tex)


def _postprocess_omml_stack(tex: str) -> str:
    r"""OMML 栈输出的最小后处理（组件评估 §2.6 清单，~50 行）。

    CJK 两形态 → `\text{}`（去空格）；花括号定界符补转义；mfenced 空定界符清理；
    `\underset{…}{lim}` → `\lim_{…}`；去掉 U+2061；全角 ＝ → =；空白归一。
    """
    t = tex
    t = t.replace("\u2061", "")
    t = t.replace("\u2329", "\\langle ").replace("\u232a", "\\rangle ")
    t = t.replace("\u27e8", "\\langle ").replace("\u27e9", "\\rangle ")
    t = t.replace("\uff1d", "=")
    t = _omml_stack_mathit_cjk(t)
    t = _OMML_CJK_RUN.sub(lambda m: "\\text{" + re.sub(r"\s+", "", m.group(0)) + "}", t)
    t = t.replace("\\left{", "\\left\\{").replace("\\right}", "\\right\\}")
    t = re.sub(r"\\left\((?:\s*)\\right\.", r"\\left(", t)
    t = re.sub(r"\\left\.(?:\s*)\\right\)", r"\\right)", t)
    t = re.sub(r"\\underset\{([^{}]*)\}\{\\lim\}", r"\\lim_{\1}", t)
    t = re.sub(r"\\underset\{([^{}]*)\}\{lim\}", r"\\lim_{\1}", t)
    t = re.sub(r"\\left\s*\.\s*\\right\s*\.", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _load_omml_stack():
    """惰性加载 vendored 栈；失败返回 False（只尝试一次，不拖慢 Word 通道）。"""
    global _OMML_STACK
    if _OMML_STACK is not None:
        return _OMML_STACK
    try:
        import sys as _sys

        from lxml import etree as _etree

        vendor = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "vendor_omml")
        if vendor not in _sys.path:
            _sys.path.insert(0, vendor)
        from mathml_to_latex.converter import MathMLToLaTeX  # noqa: PLC0415

        xsl = _etree.XSLT(_etree.parse(os.path.join(vendor, "omml2mml.xsl")))
        _OMML_STACK = (xsl, MathMLToLaTeX)
    except Exception:  # noqa: BLE001 —— vendored 栈缺失/损坏时静默降级为旧实现
        _OMML_STACK = False
    return _OMML_STACK


def _omml_stack_to_latex(el) -> str | None:
    """用 vendored 栈转换一个公式元素；失败/空串返回 None（调用方回退旧实现）。"""
    loaded = _load_omml_stack()
    if not loaded:
        return None
    xsl, conv = loaded  # type: ignore[misc]
    try:
        from lxml import etree as _etree  # noqa: PLC0415

        frag = str(xsl(_etree.fromstring(_etree.tostring(el))))
    except Exception:  # noqa: BLE001
        return None
    frag = re.sub(r"^<\?xml[^>]*\?>", "", frag).strip()
    if not frag:
        return None
    try:
        tex = conv.convert(_MATHML_WRAP % frag)
    except Exception:  # noqa: BLE001
        return None
    tex = _postprocess_omml_stack(tex or "")
    return tex or None


def omml_to_latex(el, *, use_omml_stack: bool = False) -> str:
    """把一段 `<m:oMath>` 转成 LaTeX 片段（不含外层 $）。

    默认走自研启发式 `_conv()`（产物与从前逐字节相同）。`use_omml_stack=True`
    （或全局 `USE_OMML_STACK`）时先试 vendored 的 OMML→MathML→LaTeX 栈（task-50，
    **Lead 未批准前不得改默认**）；新栈失败/空串 → 回退旧实现并 warning。
    """
    if use_omml_stack or USE_OMML_STACK:
        tex = _omml_stack_to_latex(el)
        if tex:
            return tex
        warnings.warn("OMML 栈转换失败，已回退旧实现", RuntimeWarning, stacklevel=2)
    return _conv(el).strip()


def _conv(el) -> str:
    name = _tag(el)
    kids = list(el)

    if name in ("oMath", "oMathPara", "e", "num", "den", "sub", "sup", "fName", "lim", "deg"):
        return "".join(_conv(k) for k in kids)

    if name == "r":  # 一个数学 run
        return _math_text_latex("".join((k.text or "") for k in kids if _tag(k) == "t"))

    if name == "t":
        return el.text or ""

    if name == "f":  # 分数
        num = den = ""
        for k in kids:
            if _tag(k) == "num":
                num = _conv(k)
            elif _tag(k) == "den":
                den = _conv(k)
        return f"\\frac{{{num}}}{{{den}}}"

    if name == "sSub":
        b = s = ""
        for k in kids:
            if _tag(k) == "e":
                b = _conv(k)
            elif _tag(k) == "sub":
                s = _conv(k)
        return f"{_script_base(b)}_{{{s}}}"

    if name == "sSup":
        b = s = ""
        for k in kids:
            if _tag(k) == "e":
                b = _conv(k)
            elif _tag(k) == "sup":
                s = _conv(k)
        return f"{_script_base(b)}^{{{s}}}"

    if name == "sSubSup":
        b = sb = sp = ""
        for k in kids:
            t = _tag(k)
            if t == "e":
                b = _conv(k)
            elif t == "sub":
                sb = _conv(k)
            elif t == "sup":
                sp = _conv(k)
        return f"{_script_base(b)}_{{{sb}}}^{{{sp}}}"

    if name == "rad":  # 根号
        deg = e = ""
        for k in kids:
            if _tag(k) == "deg":
                deg = _conv(k)
            elif _tag(k) == "e":
                e = _conv(k)
        return f"\\sqrt[{deg}]{{{e}}}" if deg else f"\\sqrt{{{e}}}"

    if name == "d":  # 定界符：读 m:begChr / m:endChr（缺省圆括号）
        # 第四十四批①（87/06 实测）：旧实现硬编码 `\left(...\right)`，把
        # 6 个花括号 / 2 个方括号 / 1 个尖括号**全变成圆括号**（定界符与内容一起丢）。
        inner = ""
        beg, end = "(", ")"
        for k in kids:
            t = _tag(k)
            if t == "dPr":
                for c in k:
                    ct = _tag(c)
                    if ct == "begChr":
                        beg = str(c.get(f"{{{M_NS}}}val") or "")
                    elif ct == "endChr":
                        end = str(c.get(f"{{{M_NS}}}val") or "")
            elif t == "e":
                inner = _conv(k)
        lb = _delim_tex(beg, "(")   # 未知定界符回落圆括号（与旧行为一致）
        rb = _delim_tex(end, ")")
        return f"\\left{lb}{inner}\\right{rb}"

    if name in ("acc", "bar"):  # 重音
        base = ""
        for k in kids:
            if _tag(k) == "e":
                base = _conv(k)
        return f"\\overline{{{base}}}"

    if name == "nary":  # 求和/积分等：算子 + 上下限 + **求和体**
        # 第四十四批①（90/07 实测）：旧实现只输出算子，`m:sub`/`m:sup`/`m:e`
        # 全丢 → 生成 `{∑ }`，公式内容整段消失。
        op = r"\sum"
        sub = sup = body = ""
        for k in kids:
            t = _tag(k)
            if t == "naryPr":
                for c in k:
                    if _tag(c) == "chr":
                        val = str(c.get(f"{{{M_NS}}}val") or "")
                        op = NARY_OP.get(val, r"\sum" if not val else op)
            elif t == "sub":
                sub = _conv(k)
            elif t == "sup":
                sup = _conv(k)
            elif t == "e":
                body = _conv(k)
        out = op
        if sub:
            out += f"_{{{sub}}}"
        if sup:
            out += f"^{{{sup}}}"
        if body:
            out = out + " " + body
        return out

    if name in ("func", "limLow", "limUpp", "groupChr", "box", "borderBox", "phant"):
        return "".join(_conv(k) for k in kids)

    if name == "ctrlPr" or name.endswith("Pr"):
        return ""

    # 未知结构：退化取出文字，绝不丢内容
    return "".join(_conv(k) for k in kids) if kids else (el.text or "")


def _math_to_tex(raw: str) -> str:
    """把公式纯文字里的 Unicode 符号换成 LaTeX 命令。"""
    out = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch in UNICODE_MATH:
            cmd = UNICODE_MATH[ch]
            # 命令与后面的字母之间要有空格，避免 `\landA` 这种粘连。
            #
            # ⚠️ 判据是**命令最后一个字符**是不是字母，不是 `cmd.isalpha()`：
            #    `cmd` 以反斜杠开头，`"\exists".isalpha()` 恒为 False，于是永远不加空格，
            #    实测把 `∃q` 变成 `\existsq`、`¬q` 变成 `\lnotq` → 未定义控制序列、
            #    整篇编译失败（期 87 子案例 5 / 06 Ruixue_Tang，案例 141）。
            #    `\sqrt{}` 这类以 `}` 结尾的命令自带定界，后面直接接字母是对的。
            if cmd.endswith("}") or not cmd[-1].isalpha():
                out.append(cmd)
            else:
                out.append(cmd + " ")
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def ole_object_stats(document_xml: str, names: list[str]) -> tuple[int, int]:
    """数 Word 里的 **MathType / OLE 内嵌对象**，返回 (w:object 数, embeddings 文件数)。

    为什么需要它：作者用 MathType 写的公式在 docx 里是「OLE 包 + 预览图」
    （`<w:object>` + `word/embeddings/oleObject*.bin`），**不是** OMML；
    解析器只认 `<m:oMath>`，于是这些公式会**静默从产物里消失**——不报错、日志也不提。
    期 87 子案例 2（03 Wenjing_Du）实测：`w:object` 561 处、embeddings 567 个、
    OMML 0 处，产物 PDF 少 2 页且正文里所有公式位置都是空的。

    调用方（`docxsource.parse_docx`）据此给编辑一条警告；本函数保持纯函数，便于测试。
    """
    n_objects = document_xml.count("<w:object")
    n_embeddings = sum(
        1 for n in names if n.startswith("word/embeddings/") and not n.endswith("/")
    )
    return n_objects, n_embeddings


_SYM_TAG = re.compile(r"<w:sym\b[^>]*?/?>")
_W_SYM_LOCAL = "sym"
#: LogicA 字体是「私有序号」字体；下列序号是从期 87 子案例 3（04 Fayang_Ye）
#: 的 docx 与人工成品 .tex 逐处对照得出的（不是猜测）：示例 `f:A→B`、`0=∅`、
#: `∞-群胚`、`a∈A`、`f∼g`。未列出的字体/序号仍走 `symbol_run_stats()` 警告。
LOGICA_SYM_CHARS = {
    "0022": "→",  # f:A→B / H:A×[0,1]→B
    "0040": "=",  # 0=∅
    "004E": "∼",  # f∼g
    "0052": "∈",  # a∈A
    "005D": "∅",  # 0=∅ / {∅}
    "005E": "∞",  # ∞-群胚
    # 期 90/05 Beibei_Gao 实测：Word 把 LogicA 的 ⟨/⟩ 存成 F072/F073，
    # 人工成品写成 `\langle` / `\rangle`（`命题⟨雪是白的→…⟩`）。
    "72": "⟨",
    "73": "⟩",
}
#: 其它字体里语义明确的符号（只收无歧义的）。
FONT_SYM_CHARS = {
    "Symbol": {"20": " "},  # Symbol F020 = 空格占位
}


def _sym_code(code: str) -> str:
    """Word 的 `w:char` 常带字体私有前缀（LogicA 的 `F072` → 实际 0x72）。"""
    code = str(code or "").strip().upper()
    if len(code) == 4 and code.startswith("F0"):
        return code[2:]
    return code


#: 公式 run 里按「文字」处理、包进 `\text{}` 的标点/符号。
#: CJK 基本区/扩展 A/扩展 B+、CJK 兼容表意、〇(U+3007) 走范围判断，不在这里列。
_MATH_TEXT_PUNCT = "（）［］｛｝，。；：：、“”‘’《》〈〉「」『』【】…—～·！？"

#: 留在数学模式会**把产物改坏**的 ASCII 特殊字符：`%` 会把本行余下全变注释、`$` 破坏
#: 数学开关、`#` 是宏参数、`&` 是对齐符 → 按文字处理并转义。
#: ⚠️ `_ ^ { } ~ \` 刻意不在这里：`_`/`^` 在公式里通常就是作者要的上标/下标语义
#:    （`$x_1$`），`{`/`}`/`\` 则永远走数学分支 → 本批「改动要小」不动（卡要求）。
_MATH_TEXT_SPECIALS = {"%": "\\%", "&": "\\&", "#": "\\#", "$": "\\$"}


def _is_math_text_char(ch: str) -> bool:
    """公式 run 里的字符是否该包进 `\text{}`（文字语境）。"""
    if not ch:
        return False
    if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf":   # CJK 基本区 / 扩展 A
        return True
    if ch in ("\u3007", "\u3000"):                                 # 〇 / 全角空格
        return True
    if "\uf900" <= ch <= "\ufaff":                                 # CJK 兼容表意
        return True
    if "\U00020000" <= ch <= "\U0002fa1f":                          # CJK 扩展 B+
        return True
    if "\uff00" <= ch <= "\uffef":                                 # 全角块（含 ＝ U+FF1D）
        return True
    if ch in _MATH_TEXT_SPECIALS:                                  # % & # $ → 转义
        return True
    return ch in _MATH_TEXT_PUNCT


def _escape_math_text(seg: str) -> str:
    r"""`\text{}` 内的转义。

    · ASCII 特殊字符按 `_MATH_TEXT_SPECIALS` 转义（`\text{%}` 仍是 LaTeX 注释符，必须 `\%`）；
    · **U+3000 全角空格 → `\hspace{1em}`**：实测在 `\text{}` 里会走拉丁字体报
      `Missing character: no 　 (U+3000)`（xelatex + ctex 探针，2026-10-18 验证）；
    · 其余字符原样。`\`、`}` 永远走数学分支、进不到这里——旧实现那两条
      `.replace("\\", "\\textbackslash{}")` / `.replace("}", "\\}")` 是**死代码**，
      本函数取代它，行为逐字节不变（C-4 清理，产物不变由测试 ⑪ 钉住）。
    """
    out: list[str] = []
    for ch in seg:
        if ch == "\u3000":
            out.append("\\hspace{1em}")
        else:
            out.append(_MATH_TEXT_SPECIALS.get(ch, ch))
    return "".join(out)


def _math_text_latex(text: str) -> str:
    """公式 run 里的中文/全角字符/ASCII 特殊字符包进 `\text{}`（或转义）。

    Word 公式里常把「（x是具有“p→p”形式的命题→x是真的）」整段塞进 `<m:t>`；
    不包 `\text{}` 时 xelatex 会在 Times New Roman 里找汉字/全角字符 → Missing character，
    PDF 乱码/缺字（期 90/05 Beibei_Gao；期 90/07 Liangying_Zhao 的 `＝` U+FF1D 实测 2 处）。
    分类判据见 `_is_math_text_char`；数学符号（→、∈ 等）仍留给 `_math_to_tex` 处理。
    """
    if not text:
        return text
    out: list[str] = []
    buf: list[str] = []
    in_text = False

    def flush() -> None:
        nonlocal buf, in_text
        if buf:
            seg = "".join(buf)
            if in_text:
                out.append("\\text{" + _escape_math_text(seg) + "}")
            else:
                # 第四十四批①补：`<m:t>` 里的字面花括号必须转义——OMML 的分组/上下标
                # 都是 XML 元素，文本里的 `{`/`}` 是作者手打的字面字符；原样输出会在
                # 数学模式里提前闭合分组 → `! Extra }, or forgotten $` / `Missing $`
                # （repo-73-05 Yang_Hu：`\{$f(α)$,$f(β₃)}\subset P$` 型）。
                out.append(seg.replace("{", "\\{").replace("}", "\\}"))
            buf = []
            in_text = False

    for ch in text:
        is_text = _is_math_text_char(ch)
        if is_text != in_text:
            flush()
            in_text = is_text
        buf.append(ch)
    flush()
    return "".join(out)

_SYM_FONT = re.compile(r'w:font="([^"]*)"')
_SYM_CHAR = re.compile(r'w:char="([0-9A-Fa-f]+)"')


def count_omml(xml_text: str) -> int:
    """数 `<m:oMath>` 个数（**带属性也算**；`<m:oMathPara>` 不计）。

    旧代码用 `xml.count("<m:oMath>")`，Word 给 oMath 带属性时（或 .doc→docx 后
    公式被拆成 `<m:oMath ...>`）会漏计——期 87 子案例 3 实测 74 个 oMath 只数到 1。
    """
    return len(re.findall(r"<m:oMath(?![A-Za-z])[^>]*>", xml_text))


def symbol_run_stats(xml_text: str) -> dict[str, int]:
    """数 Word **符号字体字符**（`<w:sym w:font="LogicA" w:char="0022"/>`），按字体累计。

    为什么需要它：Word 里用「逻辑符号字体」（LogicA 等）插入的字符**不是** `<w:t>`
    文字、也不是 OMML 公式，而是 `<w:sym>` 符号 run。解析器只读 `<w:t>`/`<m:t>`，
    这些字符会**静默丢失**（不报错、日志也不提）。

    期 87 子案例 3（04 Fayang_Ye 的 .doc）实测 39 处 LogicA 符号：`→`（3）、
    `∼`、`∧`/`⊢` 等全部消失，产物正文变成 `$f:A$$B$` 这种缺符号的样子；
    人工成品是 `$f:A\to B$`。字体的 char→Unicode 映射是**字体私有**的（LogicA 的
    0x22 才是 `→`），不能靠猜硬编；所以这里只计数、由调用方警告编辑人工核对。

    返回 {字体名: 次数}；无 `<w:sym>` 返回空 dict。纯函数，便于测试。
    """
    out: dict[str, int] = {}
    for tag in _SYM_TAG.findall(xml_text):
        m = _SYM_FONT.search(tag)
        font = m.group(1) if m else "(unknown)"
        ch = _SYM_CHAR.search(tag)
        code = _sym_code(ch.group(1)) if ch else ""
        if font == "LogicA" and code in LOGICA_SYM_CHARS:
            continue  # 已映射进正文，不再算「静默丢失」
        if font in FONT_SYM_CHARS and code in FONT_SYM_CHARS[font]:
            continue
        out[font] = out.get(font, 0) + 1
    return out


# ── 段落读取 ──────────────────────────────────────────────────────────────


@dataclass
class Seg:
    """段落里的一个片段。"""

    kind: str  # 'text' | 'math' | 'footnote'
    text: str = ""
    italic: bool = False
    bold: bool = False
    fid: int | None = None  # kind == 'footnote' 时的脚注 id
    rid: str = ""  # kind == 'image' 时的关系 id（rIdN）
    # 第四十四批①：行间公式（`m:oMathPara` 里的 `m:oMath`）→ 渲染成 `\[...\]`
    # （旧实现整篇都按行内 `$...$`；90/07 的 display 公式因此丢失）。
    display: bool = False


@dataclass
class Para:
    segs: list[Seg] = field(default_factory=list)
    style: str = ""

    @property
    def plain(self) -> str:
        """不含公式/脚注标记的纯文字（用于结构判断）。"""
        return "".join(s.text for s in self.segs if s.kind == "text")

    @property
    def has_math(self) -> bool:
        return any(s.kind == "math" for s in self.segs)

    @property
    def footnote_ids(self) -> list[int]:
        return [s.fid for s in self.segs if s.kind == "footnote" and s.fid]

    @property
    def italic_ratio(self) -> float:
        t = [s for s in self.segs if s.kind == "text" and s.text.strip()]
        if not t:
            return 0.0
        it = sum(len(s.text) for s in t if s.italic)
        return it / sum(len(s.text) for s in t)


# ── 第四十五批 A：源稿正文里的「手打数学符号 / 不可渲染字符」─────────────
# 88-02 Lu_Jiang 实测（5 期基准 P0 MISSING_CHAR）：作者把模态必然算子手打成 ◻
# （U+25FB，Times/宋体都没有字形）→ xelatex 报 12 处 Missing character；人工成品
# 同一位置写作 `$\Box$`。同一段还夹带 1 个变体选择符 U+E0176（不可见，无字形）。
# 88-07 Hu_Liu 实测：正文有 1 个私用区字符 U+F0A7（任何字体都没有；人工成品此位置
# 没有该字符，直接是 `Q11: Your book…`）→ 丢弃。
# ⚠️ 只收「有实测证据」的字符，不做全量私用区清理（避免误删正常内容）。
TEXT_MATH_SYMBOLS = {"\u25fb": r"\Box"}
_DROP_TEXT_CHARS = {"\uf0a7"}


def _is_variation_selector(ch: str) -> bool:
    """变体选择符（不可见格式字符）：U+FE00–FE0F / U+E0100–E01EF。"""
    o = ord(ch)
    return 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF


def _split_text_run(text: str, italic: bool, bold: bool) -> list["Seg"]:
    """把一段 `<w:t>` 拆成 text/math 片段：手打数学符号转命令、不可渲染字符丢弃。

    没有特殊字符时输出与从前完全相同的一个 text Seg（逐字节不变）。
    """
    out: list[Seg] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(Seg("text", "".join(buf), italic, bold))
            buf.clear()

    for ch in text:
        sym = TEXT_MATH_SYMBOLS.get(ch)
        if sym is not None:
            flush()
            out.append(Seg("math", sym, italic, bold))
        elif ch in _DROP_TEXT_CHARS or _is_variation_selector(ch):
            continue
        else:
            buf.append(ch)
    flush()
    return out


def _run_segments(r, handled_math: set) -> list[Seg]:
    """拆一个 <w:r>。里面可能有普通文字，也可能内嵌 <m:oMath>。

    `handled_math` 是本段落里**已经由这里处理过**的 <m:oMath>，供外层
    `_para_segments()` 跳过、避免同一节点被处理两次（见那里的注释）。
    """
    rpr = None
    for c in r:
        if c.tag == f"{{{W_NS}}}rPr":
            rpr = c
            break
    italic = bold = False
    if rpr is not None:
        for c in rpr:
            t = c.tag.split("}", 1)[-1]
            if t == "i":
                italic = str(c.get(f"{{{W_NS}}}val", "1")) not in ("0", "false")
            elif t == "b":
                bold = str(c.get(f"{{{W_NS}}}val", "1")) not in ("0", "false")

    out: list[Seg] = []

    def handle_drawing(c) -> None:
        """处理一个 <w:drawing>：有 blip 就是内嵌图片，否则是矢量形状。"""
        for blip in c.iter(A_BLIP):
            rid = blip.get(R_EMBED)
            if rid:
                out.append(Seg("image", rid=rid))
                return
        out.append(Seg("shape"))

    # ⚠️ 这里存**元素本身**而不是 id()：lxml 的元素代理是临时对象，没有引用会被回收，
    #    同一个 XML 节点再访问时会换一个新代理、id() 也随之变化（还会被别的节点复用）。
    #    用 id() 去重就成了「看内存分配脸色」的随机行为（R5，第 125 条）。集合持有元素 =
    #    持有代理引用，同一节点始终是同一个代理，身份比较才是确定的。
    seen_drawings: set = set()
    for c in r:
        t = c.tag
        if t == W_T:
            if c.text:
                out.extend(_split_text_run(c.text, italic, bold))
        elif t == f"{{{W_NS}}}{_W_SYM_LOCAL}":
            font = c.get(f"{{{W_NS}}}font", "")
            raw_char = str(c.get(f"{{{W_NS}}}char", "")).upper()
            char = _sym_code(raw_char)
            mapped = ""
            if font == "LogicA":
                mapped = LOGICA_SYM_CHARS.get(char) or LOGICA_SYM_CHARS.get(raw_char, "")
            elif font in FONT_SYM_CHARS:
                mapped = FONT_SYM_CHARS[font].get(char) or FONT_SYM_CHARS[font].get(raw_char, "")
            if mapped:
                out.append(Seg("text", mapped, italic, bold))
        elif t == M_OMATH:
            handled_math.add(c)
            tex = _math_to_tex(omml_to_latex(c))
            if tex:
                out.append(Seg("math", tex, italic, bold))
        elif t == f"{{{W_NS}}}footnoteReference":
            fid = c.get(f"{{{W_NS}}}id")
            if fid is not None:
                out.append(Seg("footnote", fid=int(fid)))
        elif t == W_DRAWING:
            seen_drawings.add(c)
            handle_drawing(c)

    # ⚠️ <w:drawing> 常被包在 <mc:AlternateContent><mc:Choice> 里，
    #    不是 <w:r> 的直接子元素——只遍历直接子节点会完全漏掉它们。
    #    实测 03 HaoJian_Xiao 的 6 个矢量形状就是这么漏掉的。
    for d in r.iter(W_DRAWING):
        if d not in seen_drawings:
            seen_drawings.add(d)
            handle_drawing(d)
    return out


def _para_segments(p) -> list[Seg]:
    """按文档顺序遍历段落，收集文字 / 公式 / 脚注引用。

    注意 <m:oMath> 既可能出现在 <w:r> 内部，也可能是 <w:p> 的直接子元素，
    所以统一用 iter() 按顺序走一遍，再用 <w:r> 边界处理格式。

    ⚠️ 去重必须用**元素身份**、且只登记 `_run_segments()` 真正处理过的公式
    （<w:r> 的直接子 <m:oMath>）。曾经的做法是「把 run 里所有 oMath 都登记、
    靠 id() 判断」，但：① lxml 代理会被回收重建，id() 随机漂移（R5，第 125 条）；
    ② 登记范围过大——`<w:r>→<m:oMathPara>→<m:oMath>`（Word 文本框里的公式）并不由
    `_run_segments()` 处理，却会被当成已处理，命中 id 巧合时整条公式从产物里消失。
    """
    segs: list[Seg] = []
    handled_math: set = set()
    for el in p.iter():
        if el.tag == W_R:
            # <w:r> 里可能内嵌 <m:oMath>，由 _run_segments 取走并登记，避免下面重复处理
            segs.extend(_run_segments(el, handled_math))
        elif el.tag == M_OMATH and el not in handled_math:
            # <m:oMath> 也可能不在 run 的直接子级（独立公式行 / 文本框里的公式）
            tex = _math_to_tex(omml_to_latex(el))
            if tex:
                # 父节点是 <m:oMathPara> = 行间公式（display），渲染成 \[...\]
                parent = el.getparent()
                display = parent is not None and _tag(parent) == "oMathPara"
                segs.append(Seg("math", tex, display=display))
    return segs


def read_body(path: str):
    """读 word/document.xml，返回 (段落列表, 样式名->样式id 无关)。"""
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    paras: list[Para] = []
    for p in doc.iter(W_P):
        # 只取正文层级的段落（表格里的也算）
        style = ""
        ppr = p.find(f"{{{W_NS}}}pPr")
        if ppr is not None:
            ps = ppr.find(f"{{{W_NS}}}pStyle")
            if ps is not None:
                style = str(ps.get(f"{{{W_NS}}}val") or "")
        para = Para(segs=merge_segs(_para_segments(p)), style=style)
        if para.segs:
            paras.append(para)
    return paras


def read_media(path: str) -> dict[str, str]:
    """读关系表，返回 {rId: word/media 内的文件名}。

    docx 本身就是 zip，**原图就存在里面**，直接取出即可——比「截图 Word 区域」
    质量更高：截图会重新采样，还可能带上 Word 的显示缩放。
    """
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        try:
            root = etree.fromstring(z.read("word/_rels/document.xml.rels"))
        except KeyError:
            return {}
    out: dict[str, str] = {}
    for rel in root:
        target = rel.get("Target") or ""
        if "media/" in target:
            out[rel.get("Id") or ""] = os.path.basename(target)
    return out


def extract_media(path: str, out_dir: str, mapping: dict[str, str]) -> dict[str, str]:
    """把用到的图片从 docx 里解出来，返回 {原文件名: 导出后的文件名}。

    导出名统一为 fig1.png / fig2.png …（按在正文里出现的先后），
    因为原文件名（image1.png）没有信息量。
    """
    import shutil

    os.makedirs(out_dir, exist_ok=True)
    exported: dict[str, str] = {}
    seq = 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for _rid, fname in mapping.items():
            if fname in exported:
                continue
            src = f"word/media/{fname}"
            if src not in names:
                continue
            seq += 1
            ext = os.path.splitext(fname)[1].lower() or ".png"
            new_name = f"fig{seq}{ext}"
            with z.open(src) as fsrc, open(os.path.join(out_dir, new_name), "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            exported[fname] = new_name
    return exported


W_TBL = f"{{{W_NS}}}tbl"
W_TR = f"{{{W_NS}}}tr"
W_TC = f"{{{W_NS}}}tc"
W_GRIDSPAN = f"{{{W_NS}}}gridSpan"
W_VMERGE = f"{{{W_NS}}}vMerge"


@dataclass
class Cell:
    segs: list[Seg] = field(default_factory=list)
    colspan: int = 1
    vmerge: str = ""  # '' | 'restart' | 'continue'

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segs if s.kind == "text").strip()

    @property
    def has_math(self) -> bool:
        return any(s.kind == "math" for s in self.segs)


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)
    ncols: int = 0

    @property
    def nrows(self) -> int:
        return len(self.rows)

    @property
    def merged(self) -> bool:
        return any(c.colspan > 1 or c.vmerge for r in self.rows for c in r)

    @property
    def nested(self) -> bool:
        return False


def read_table(tbl) -> Table:
    """读一张 Word 表格。"""
    t = Table()
    for tr in tbl.findall(W_TR):
        row: list[Cell] = []
        for tc in tr.findall(W_TC):
            cell = Cell()
            for p in tc.iter(W_P):
                cell.segs.extend(_para_segments(p))
            cell.segs = merge_segs(cell.segs)
            tcpr = tc.find(f"{{{W_NS}}}tcPr")
            if tcpr is not None:
                gs = tcpr.find(W_GRIDSPAN)
                if gs is not None:
                    try:
                        cell.colspan = max(1, int(gs.get(f"{{{W_NS}}}val") or 1))
                    except ValueError:
                        pass
                vm = tcpr.find(W_VMERGE)
                if vm is not None:
                    cell.vmerge = vm.get(f"{{{W_NS}}}val") or "continue"
            row.append(cell)
        if row:
            t.rows.append(row)
    t.ncols = sum(c.colspan for c in t.rows[0]) if t.rows else 0
    return t


def read_blocks(path: str):
    """按文档顺序返回正文块：段落 (Para) 与表格 (Table) 混排。

    必须**只取顶层元素**：`root.iter(W_P)` 会把表格单元格里的段落也当成正文段落，
    那样表格内容会以纯文字形式散进正文，而表格结构本身丢失。
    """
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        return []

    blocks = []
    for el in body:
        if el.tag == W_P:
            style = ""
            ppr = el.find(f"{{{W_NS}}}pPr")
            if ppr is not None:
                ps = ppr.find(f"{{{W_NS}}}pStyle")
                if ps is not None:
                    style = str(ps.get(f"{{{W_NS}}}val") or "")
            para = Para(segs=merge_segs(_para_segments(el)), style=style)
            if para.segs:
                blocks.append(para)
        elif el.tag == W_TBL:
            t = read_table(el)
            if t.rows:
                blocks.append(t)
    return blocks


def read_footnotes(path: str) -> dict[int, list[Seg]]:
    """读 word/footnotes.xml，返回 {脚注id: 片段列表}。

    `separator` / `continuationSeparator` 是脚注区那条横线之类的排版元数据
    （正文从不引用），按 `w:type` 丢掉。
    """
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        if "word/footnotes.xml" not in z.namelist():
            return {}
        root = etree.fromstring(z.read("word/footnotes.xml"))

    out: dict[int, list[Seg]] = {}
    for fn in root.iter(f"{{{W_NS}}}footnote"):
        # ⚠️ 判据只能是 `w:type`，**不能按 id 丢**：−1/0 通常确实就是下面这两个伪脚注，
        #    但本库实测 23 篇稿子的「作者信息/收稿日期」脚注正是一个**不带 w:type 的
        #    w:id="0"**，而且正文里另有 `w:footnoteReference w:id="0"` 真引用它。
        #    按 id 丢会连着正文引用一起丢内容（那 23 篇里 7 篇脚注含邮箱），
        #    还会让普查把「解析器没拿到」误记成「原稿本来就没有」（`absent`）。
        if (fn.get(f"{{{W_NS}}}type") or "") in ("separator", "continuationSeparator"):
            continue
        fid = fn.get(f"{{{W_NS}}}id")
        if fid is None:
            continue
        try:
            i = int(fid)
        except ValueError:
            continue
        segs: list[Seg] = []
        for p in fn.iter(W_P):
            segs.append(Seg("text", "\n"))
            segs.extend(_para_segments(p))
        # 去掉开头的换行
        while segs and segs[0].kind == "text" and not segs[0].text.strip():
            segs.pop(0)
        out[i] = segs
    return out


def merge_segs(segs: list[Seg]) -> list[Seg]:
    """合并相邻且格式相同的文字片段。

    Word 会把一段连续斜体拆成很多 <w:r>（每个词一个），不合并的话会输出
    `\\textit{dictum} \\textit{omnem} \\textit{hominem}` 这种碎片，
    而人工成品是一个完整的 `\\textit{...}`。
    """
    out: list[Seg] = []
    for s in segs:
        if (out and s.kind == "text" and out[-1].kind == "text"
                and out[-1].italic == s.italic and out[-1].bold == s.bold):
            out[-1].text += s.text
        else:
            out.append(Seg(s.kind, s.text, s.italic, s.bold, s.fid, s.rid,
                           getattr(s, "display", False)))
    return out


def segs_to_text(segs: list[Seg], math_inline: bool = True) -> str:
    """把片段列表拼成可读文本；公式用 $…$ 包裹。"""
    out = []
    for s in segs:
        if s.kind == "text":
            out.append(s.text)
        elif s.kind == "math":
            out.append(f"${s.text}$" if math_inline else s.text)
    return "".join(out)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
