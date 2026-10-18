"""预校正规则库。

规则来自编辑部沉淀的《流程化经验书》和《提示词》。每条规则都标注了
`safe`：

  safe=True   确定性替换，可直接自动套用
  safe=False  只提示、不自动改（可能误伤，或需要编辑判断）

这样工具的输出永远是可解释、可回退的：报告里会列出「改了哪些、还有哪些要你看」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Rule:
    id: str
    desc: str
    pattern: str
    repl: str
    lang: str  # 'zh' | 'en' | 'both'
    safe: bool = True
    flags: int = 0


# fmt: off
RULES: list[Rule] = [
    # ── 中文文章 ────────────────────────────────────────────────────────
    Rule("zh-pagerange-hyphen", "页码范围用抬高的 \\cndash",
         r"第\s*(\d+)\s*-{1,2}\s*(\d+)\s*页", r"第\1\\cndash \2页", "zh"),
    Rule("zh-pagerange-bare", "『x--y页』改用 \\cndash",
         r"(\d+)\s*--\s*(\d+)\s*页", r"\1\\cndash \2页", "zh"),
    Rule("zh-ldots", "$\\cdots$ 在中文语境应为 $\\ldots$",
         r"\$\\cdots\$", r"$\\ldots$", "zh"),
    Rule("zh-cite-paren-space", "引文命令内逗号后统一加空格",
         r"\\(parencite|cite|ct)\{([^{}]*?)\}", r"\\\1{PLACEHOLDER}", "both", safe=False),
    Rule("zh-footnote-comma", "脚注前的逗号应改为句号",
         r"，\\footnote", r"。\\footnote", "zh", safe=False),
    Rule("zh-bracket-comma", "引文括号前的逗号位置",
         r"，\s*\（", r"（", "zh", safe=False),
    Rule("zh-punct-colon", "中文里的英文冒号", r"([\u4e00-\u9fff])\s*:\s*", r"\1：", "zh", safe=False),
    Rule("zh-punct-bang", "中文里的英文叹号", r"([\u4e00-\u9fff])\s*!\s*", r"\1！", "zh", safe=False),
    Rule("zh-punct-quest", "中文里的英文问号", r"([\u4e00-\u9fff])\s*\?\s*", r"\1？", "zh", safe=False),
    Rule("zh-comma-han", "汉字前后的英文逗号",
         r"([\u4e00-\u9fff])\s*,\s*", r"\1，", "zh", safe=False),
    Rule("zh-period-han", "汉字前后的英文句号",
         r"([\u4e00-\u9fff])\s*\.\s*(?![a-zA-Z])", r"\1。", "zh", safe=False),
    Rule("zh-apostrophe", "中文引号误用", r"(?<=[\u4e00-\u9fff])[\"']", "”", "zh", safe=False),
    # Word 文稿里的 Unicode 符号（≤ → ∈ ⑴ ─ 零宽字符）。pattern 只为占位，
    # 实际由 normalize_unicode_math / normalize_unicode_punct 处理。
    Rule("uni-normalize", "Unicode 数学符号与括号数字规范化", r"(?!x)x", "", "both"),
    # ⛔ 已删除 Rule("zh-ascii-paren")：原以为「西文语词周围的全角括号应改半角」，
    #    实测恰好相反——本刊要求保留全角括号（docx 原文 `（Philosophical Logic）`，
    #    人工只改大小写、保留全角）。该规则命中 44 处，使相似度下降 0.361pp。
    #    这是消融实验（ablation.py）抓出来的，不要凭直觉加回来。

    # ── 英文文章 ────────────────────────────────────────────────────────
    Rule("en-page-pp", "范围页码写作 pp.\\,x--x",
         r"\bpp?\.\s*(\d+)\s*[-–]{1,2}\s*(\d+)", r"pp.\\,\1--\2", "en"),
    Rule("en-page-p", "单页写作 p.\\,x", r"\bp\.\s*(\d+)(?!\s*[-–])", r"p.\\,\1", "en"),
    Rule("en-name-paren", "英文人名与括号间需空格", r"([a-zA-Z])\((\d)", r"\1 (\2", "en"),
    Rule("en-dash", "英文破折号应为三条短横", r"(?<![-\w])—(?![-\w])", "---", "en"),
    # 第 121 条（期 88 实例测试）：字面 `...` 后**紧跟字母**时不能直接写 `\ldots`
    # —— 会粘成 `\ldotsx` 这种未定义命令（05 YouAn_Su 真稿编译失败）。只在「后接字母」
    # 这一种形态加 `{}` 隔离，其余形态输出保持逐字节不变。
    Rule("en-ellipsis-letter", "英文省略号后紧跟字母（用 { } 隔离）",
         r"\.\.\.(?!\.)(?=[A-Za-z])", r"\\ldots{}", "en"),
    Rule("en-ellipsis", "英文省略号", r"\.\.\.(?!\.)", r"\\ldots", "en"),
    # ⛔ 已删除 Rule("en-apostrophe")。
    #    它在单篇（03 Liang_Zhang）消融中贡献 +0.025pp，貌似有益；
    #    但全库逐项核对显示 ’ 在人工成品里是**保留**的
    #    （05 Wenzhong_Ma：docx 35 个 ’ → 成品保留 32 个，仅 6 处改直引号）。
    #    这是「单篇调参过拟合」的典型样本——规则取舍必须以全库证据为准。
    Rule("en-cjk-quote", "英文稿里混入中文引号",
         r"[“”‘’]", r"\"", "en", safe=False),
    Rule("en-colon-space", "冒号后缺空格", r":([a-zA-Z])", r": \1", "en", safe=False),
    Rule("en-abbrev-space", "人名缩写点后有空格", r"\b([A-Z])\.\s*([A-Z][a-z])", r"\1.\\,\2", "en", safe=False),
    Rule("en-range-tilde", "英文数字区间的波浪号", r"(\d)\s*~\s*(\d)", r"\1--\2", "en", safe=False),
]
# fmt: on

_BY_ID = {r.id: r for r in RULES}


def _apply_cite_spacing(text: str) -> tuple[str, int]:
    """把 \\parencite{a,b} 规范成 \\parencite{a, b}，正确处理嵌套花括号。"""
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        inner = m.group(2)
        new = re.sub(r",\s*", ", ", inner)
        if new != inner:
            n += 1
        return f"\\{m.group(1)}{{{new}}}"

    text = re.sub(r"\\(parencite|cite|ct)\{([^{}]*)\}", fix, text)
    return text, n


# ── Word 文稿带来的 Unicode 符号规范化 ────────────────────────────────────
#
# 这一组规则全部经过**逐项核对人工成品**才敢加，不是凭直觉：
#   扫描 89 份 docx 得到符号清单 → 在对应成品 tex 里数同义写法的出现次数。
#   实测对应关系（docx 次数 → 成品次数）：
#     ≤ 8→`\leq` 8 、→ 8→`\to` 8 、∈ 5→`\in` 10 、⑴ 37→`（1）` 38 、─ 22→`——` 36
#   数学符号在成品里是**包在 $...$ 内**的（如 `$w_{1} \leq_{w} w_{2}$`），
#   而 （1） 与 —— 是纯文本。
#
# 反例（差点又加错）：`’` 在成品里是**保留**的（32 保留 vs 6 改直引号），
# 所以不能像早先那样把所有 ’ 改成 ' —— 那条规则只在单篇上侥幸为正。

UNICODE_MATH = {
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv",
    "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow", "↦": r"\mapsto",
    # 同伦/同构「等价」符号：`≃` 在 Word 稿里常作为**普通文字**夹在两个公式之间
    # （`$A$≃$B$`），不映射就原样留在正文 → xelatex 报 Missing character（Times 无此字形）。
    # 实测期 87 子案例 3（04 Fayang_Ye，案例 141）；均由模板已加载的 amssymb 提供。
    "≃": r"\simeq", "≅": r"\cong", "∼": r"\sim", "≉": r"\not\approx",
    "∈": r"\in", "∉": r"\notin", "∋": r"\ni",
    "⊆": r"\subseteq", "⊂": r"\subset", "⊇": r"\supseteq", "⊃": r"\supset",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\varnothing",
    "∀": r"\forall", "∃": r"\exists", "¬": r"\lnot", "∧": r"\land", "∨": r"\lor",
    "⊥": r"\perp", "⊤": r"\top", "⊢": r"\vdash", "⊨": r"\models",
    # 强制（forcing / 语义后承）符号：`⊩` 是 OMML 稿里最常见的「双竖线带短横」，
    # 不映射就原样留在数学模式里 → xelatex 报 Missing character，PDF 里是空白
    # （期 87 子案例 5 / 06 Ruixue_Tang，案例 141）。后六个是对偶/否定形式，
    # 均由模板已加载的 amssymb 提供（模板第 21 行 `amsmath,amssymb,...`）。
    "⊩": r"\Vdash", "⊪": r"\Vvdash", "⊫": r"\VDash",
    "⊬": r"\nvdash", "⊭": r"\nvDash", "⊮": r"\nVdash", "⊯": r"\nVDash",
    "∞": r"\infty", "∑": r"\sum", "∏": r"\prod", "√": r"\sqrt{}",
    "ℵ": r"\aleph", "℘": r"\wp", "⊕": r"\oplus", "⊗": r"\otimes",
    "□": r"\Box", "◇": r"\Diamond", "⋄": r"\diamond",
    "∴": r"\therefore", "∵": r"\because", "∅": r"\varnothing",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "θ": r"\theta", "λ": r"\lambda", "μ": r"\mu",
    "π": r"\pi", "σ": r"\sigma", "φ": r"\varphi", "ϕ": r"\phi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Θ": r"\Theta", "Λ": r"\Lambda", "Π": r"\Pi", "Σ": r"\Sigma",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}

# ⑴…⒇ → （1）…（20）；①…⑳ 同理
_PAREN_DIGITS = {chr(0x2474 + i): f"（{i + 1}）" for i in range(20)}
_CIRCLED_DIGITS = {chr(0x2460 + i): f"（{i + 1}）" for i in range(20)}

# Word 文稿常把数学写成纯文本（`limn→∞`、`logx`）。这些函数名直接放进 $…$ 里
# 会因为 \\lim 未定义而编译失败——实测 07 Liangying_Zhao 就是这么炸的。
MATH_FUNCS = {
    "limsup": r"\limsup", "liminf": r"\liminf", "lim": r"\lim",
    "log": r"\log", "ln": r"\ln", "exp": r"\exp", "sin": r"\sin",
    "cos": r"\cos", "tan": r"\tan", "max": r"\max", "min": r"\min",
    "sup": r"\sup", "inf": r"\inf", "det": r"\det", "dim": r"\dim",
    "gcd": r"\gcd", "mod": r"\bmod", "arg": r"\arg", "deg": r"\deg",
}

_ZERO_WIDTH = "\ufeff\u200b\u200c\u200d"

_MATH_SPLIT = re.compile(r"(\$[^$]*\$)")


def normalize_unicode_math(text: str) -> tuple[str, int]:
    """把 Unicode 数学符号包成数学模式。跳过已有的 $...$ 区域。"""
    n = 0
    parts = _MATH_SPLIT.split(text)
    for i in range(0, len(parts), 2):  # 偶数下标 = 数学环境之外
        seg = parts[i]
        out = []
        for ch in seg:
            if ch in UNICODE_MATH:
                out.append(f"${UNICODE_MATH[ch]}$")
                n += 1
            else:
                out.append(ch)
        parts[i] = "".join(out)
    return "".join(parts), n


def normalize_unicode_punct(text: str) -> tuple[str, int]:
    """⑴→（1）、─→——、去掉零宽字符。"""
    n = 0
    for k in _ZERO_WIDTH:
        if k in text:
            n += text.count(k)
            text = text.replace(k, "")
    for mapping in (_PAREN_DIGITS, _CIRCLED_DIGITS):
        for k, v in mapping.items():
            if k in text:
                n += text.count(k)
                text = text.replace(k, v)
    # U+2500 / U+2015 被误用为破折号时**逐字符**换成 em dash。
    # docx 里通常写成 `──`（两个制表符），1:1 换正好得到 `——`，
    # 与人工成品一致。早先写成整体替换 `──`→`——` 会得到 `————`，是错的。
    for k in ("─", "―"):
        if k in text:
            n += text.count(k)
            text = text.replace(k, "—")
    return text, n


# 「引者注」：原稿写作 `（X——引者注）`，人工一律转成 `\footnote{X，引者注。}~`
# 实测 07 Liangying_Zhao 的 4 处 100% 一致；缺「即」时人工会补上（3/3）。
_YINZHEZHU = re.compile(r"（([^）]{1,60}?)——引者注）")


def fix_editor_notes(text: str) -> tuple[str, int]:
    """把「（X——引者注）」转成人工惯例的脚注形式。"""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        body = m.group(1).strip()
        if not body.startswith(("即", "亦", "也", "指")):
            body = "即" + body
        n += 1
        return f"\\footnote{{{body}，引者注。}}~"

    return _YINZHEZHU.sub(repl, text), n


_MATH_SPAN = re.compile(r"\$([^$]*)\$")


def fix_math_commands(text: str) -> tuple[str, int]:
    """修正 $…$ 里的裸函数名（`$limn\\to\\inftyF$` → `$\\lim n\\to\\infty F$`）。

    只在数学环境内动，避免误伤正文里的英文单词。
    """
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        inner = m.group(1)
        for word in sorted(MATH_FUNCS, key=len, reverse=True):
            # 函数名后必须紧跟字母/数字/括号才需要补命令
            pat = re.compile(r"(?<![A-Za-z\\])" + word + r"(?=[A-Za-z0-9(])")
            if pat.search(inner):
                # 用 lambda 而不是字符串替换：替换串里的 \lim 会被 re 当成转义序列
                repl = MATH_FUNCS[word] + " "
                inner = pat.sub(lambda _m, r=repl: r, inner)
                n += 1
        # 命令后缺空格（\\inftyF → \\infty F）
        inner2 = re.sub(r"(\\[A-Za-z]+)(?=[A-Z])", r"\1 ", inner)
        if inner2 != inner:
            n += 1
            inner = inner2
        return "$" + inner + "$"

    return _MATH_SPAN.sub(fix, text), n


@dataclass
class ApplyResult:
    text: str
    applied: dict[str, int]  # rule.id -> 命中次数


# 这些规则必须避开数学环境：$x'y'$ 里的撇号是导数符号，不是英文撇号
MATH_SAFE_IDS = {"en-apostrophe", "en-page-p", "en-page-pp"}


def _apply_outside_math(text: str, pat: str, repl: str) -> tuple[str, int]:
    """只在 $...$ 之外套用替换。"""
    parts = _MATH_SPLIT.split(text)
    n = 0
    for i in range(0, len(parts), 2):  # 偶数下标为数学环境之外
        parts[i], k = re.subn(pat, repl, parts[i])
        n += k
    return "".join(parts), n


def apply_rules(
    text: str,
    lang: str = "zh",
    safe_only: bool = True,
    disabled: set[str] | None = None,
    docx_mode: bool = False,
) -> ApplyResult:
    """套用规则。lang 决定哪些规则参与，disabled 用于消融实验。"""
    disabled = disabled or set()
    applied: dict[str, int] = {}
    for rule in RULES:
        if rule.lang not in (lang, "both"):
            continue
        if safe_only and not rule.safe:
            continue
        if rule.id in disabled:
            continue
        if rule.id == "uni-normalize":  # 需要数学环境感知，单独处理
            text, n1 = normalize_unicode_math(text)
            text, n2 = normalize_unicode_punct(text)
            n3 = 0
            if docx_mode:
                # ⚠️ 只对 **Word 来源** 做函数名修正。LaTeX 源稿本来就是规范 LaTeX，
                #    对它跑这步会误伤（实测 03 Liang_Zhang 立刻报 Missing $ inserted）。
                #    问题本身也只存在于 Word：作者把数学写成纯文本 `limn→∞`。
                text, n3 = fix_math_commands(text)
            text, n4 = fix_editor_notes(text)
            n = n1 + n2 + n3 + n4
        elif rule.id == "zh-cite-paren-space":  # 需要嵌套感知，单独处理
            text, n = _apply_cite_spacing(text)
        elif rule.id in MATH_SAFE_IDS:
            text, n = _apply_outside_math(text, rule.pattern, rule.repl)
        else:
            text, n = re.subn(rule.pattern, rule.repl, text)
        if n:
            applied[rule.id] = applied.get(rule.id, 0) + n
    return ApplyResult(text, applied)


def audit(text: str, lang: str = "zh") -> list[tuple[str, str, int, str]]:
    """只检查不修改：返回 [(规则id, 说明, 命中数, safe)]，供人工复核清单使用。"""
    out = []
    for rule in RULES:
        if rule.lang not in (lang, "both"):
            continue
        try:
            n = len(re.findall(rule.pattern, text))
        except re.error:
            n = 0
        if n:
            out.append((rule.id, rule.desc, n, "自动" if rule.safe else "待人工"))
    return sorted(out, key=lambda x: -x[2])
