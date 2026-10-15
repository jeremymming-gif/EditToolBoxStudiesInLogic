"""打分器：把「工具生成的 tex」与「编辑的人工成品」比对并分档。

这是整个 PoC 的核心。它不预测能不能做，而是用真实成品直接测出「做到多少」。

为什么要两级比对
----------------
中文论文里一个自然段就是一行，动辄上千字符。若只按行 diff，改一个字就会把
整个段落判成「实质差异」，得出的自动化率严重偏低（实测会从 ~50% 虚报成 ~28%）。
所以必须先按行对齐，再在改动过的行内部做字符级定位，才能把真正的改动孤立出来。

分档定义
--------
L1 完全一致   ：规范化后逐字符相同
L2 表层差异   ：只有标点、空白、连字符、排版宏等差异（可规则化消除）
L3 实质差异   ：涉及实词增删改（需要编辑判断或 AI）
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# ── 归一化 ────────────────────────────────────────────────────────────────

# 排版层面的宏：有或无都不改变文字内容，只改变间距/样式
TYPO_MACROS = [
    r"\\cndash\b",
    r"\\ldots\b",
    r"\\cdots\b",
    r"\\,",
    r"\\;",
    r"\\:",
    r"\\!",
    r"\\hfill\b",
    r"\\hspace\*?\{[^{}]*\}",
    r"\\phantom\{[^{}]*\}",
    r"\\vspace\*?\{[^{}]*\}",
    r"\\noindent\b",
    r"\\label\{[^{}]*\}",
    r"\\text(?:rm|bf|it|sf|up|sc|kaishu|heiti|songti)\b",
]

_PUNCT_CHARS = (
    "。，；：、（）()“”‘’《》〈〉—–~～·．,;:!?\"'`"
    "$%&*+-=/\\{}[]<>|_^ \t\u00a0\u2002\u2003\u3000"
)
_PUNCT_SET = set(_PUNCT_CHARS)


def normalize_layout(s: str) -> str:
    """抹掉一切排版层面的差异，只留「文字内容」，用于判定 L2。"""
    s = re.sub(r"(?<!\\)%.*", "", s)
    for pat in TYPO_MACROS:
        s = re.sub(pat, "", s)
    s = re.sub(r"\\[A-Za-z]+", "", s)  # 去掉命令名，保留其参数
    return "".join(ch for ch in s if ch not in _PUNCT_SET)


def normalize_ws(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


# ── 分词：让字符级比对对齐得更准 ───────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"\\[A-Za-z]+"          # LaTeX 命令
    r"|[A-Za-z]+(?:['’][A-Za-z]+)?"  # 英文单词
    r"|\d+"                  # 数字
    r"|[\u4e00-\u9fff]"      # 单个汉字（逐字对齐）
    r"|.",
    re.S,
)


def tokenize(s: str) -> list[str]:
    """切成便于比对的 token：汉字逐字、英文按词、命令整体。"""
    return _TOKEN_RE.findall(s)


# ── 结果结构 ──────────────────────────────────────────────────────────────


@dataclass
class Block:
    """一个差异块。left=人工成品侧，right=工具生成侧。"""

    kind: str
    left: str
    right: str
    level: str
    context: str = ""  # 所在行的前若干字符，便于定位


@dataclass
class ScoreResult:
    total_blocks: int = 0
    l2_blocks: int = 0
    l3_blocks: int = 0
    ref_chars: int = 0
    gen_chars: int = 0
    l3_ref_chars: int = 0
    blocks: list[Block] = field(default_factory=list)
    raw_ratio: float = 0.0  # 不做任何归一化的裸相似度（端到端总指标）

    @property
    def l2_rate(self) -> float:
        if not self.total_blocks:
            return 1.0
        return self.l2_blocks / self.total_blocks

    @property
    def char_accuracy(self) -> float:
        """以人工成品为基准的「内容」准确率——只惩罚实词增删改（L3）。

        注意：这个指标**不奖励**标点层面的修复，所以不能单独用来判断规则有没有用。
        判断规则效果要看 raw_ratio。
        """
        if not self.ref_chars:
            return 1.0
        return 1.0 - self.l3_ref_chars / self.ref_chars


def classify(left: str, right: str) -> str:
    return "L2" if normalize_layout(left) == normalize_layout(right) else "L3"


def _collect(res: ScoreResult, left: str, right: str, context: str) -> None:
    if not left and not right:
        return
    lv = classify(left, right)
    res.blocks.append(Block("op", left, right, lv, context))
    res.total_blocks += 1
    if lv == "L2":
        res.l2_blocks += 1
    else:
        res.l3_blocks += 1
        res.l3_ref_chars += len(left)


def score(reference: str, generated: str, *, context_len: int = 40) -> ScoreResult:
    """两级比对：先按行对齐，再在改动行内做 token 级定位。"""
    ref_lines = reference.split("\n")
    gen_lines = generated.split("\n")
    res = ScoreResult(
        ref_chars=len(normalize_ws(reference).replace("\n", "")),
        gen_chars=len(normalize_ws(generated).replace("\n", "")),
    )

    res.raw_ratio = difflib.SequenceMatcher(
        None, normalize_ws(reference), normalize_ws(generated), autojunk=False
    ).ratio()

    line_sm = difflib.SequenceMatcher(None, ref_lines, gen_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in line_sm.get_opcodes():
        if tag == "equal":
            continue
        left = "\n".join(ref_lines[i1:i2])
        right = "\n".join(gen_lines[j1:j2])
        ctx = normalize_ws(left)[:context_len] or normalize_ws(right)[:context_len]

        # 行内再做 token 级比对，把真正的改动孤立出来。
        # 但只有当两侧都不太长时才细分，否则 token 级 diff 会退化成整块。
        if len(left) < 20000 and len(right) < 20000 and (left or right):
            lt, rt = tokenize(left), tokenize(right)
            tsm = difflib.SequenceMatcher(None, lt, rt, autojunk=False)
            for t, a1, a2, b1, b2 in tsm.get_opcodes():
                if t == "equal":
                    continue
                _collect(res, "".join(lt[a1:a2]), "".join(rt[b1:b2]), ctx)
        else:
            _collect(res, left, right, ctx)
    return res


def report(res: ScoreResult, title: str, top: int = 20) -> str:
    lines = [
        f"══ {title} ══",
        f"  人工成品 {res.ref_chars} 字符 / 工具生成 {res.gen_chars} 字符",
        f"  差异块合计 {res.total_blocks} 个",
        f"    ├─ L2 表层（标点/空白/排版宏，可规则化）: {res.l2_blocks} 个 "
        f"（{res.l2_rate * 100:.1f}%）",
        f"    └─ L3 实质（实词增删改，需判断）        : {res.l3_blocks} 个 "
        f"（{(1 - res.l2_rate) * 100:.1f}%）",
        f"  内容准确率（只计 L3）：{res.char_accuracy * 100:.2f}%"
        f"（实质差异共 {res.l3_ref_chars} 字符）",
        f"  裸相似度（端到端总指标）：{res.raw_ratio * 100:.2f}%",
    ]
    l3 = [b for b in res.blocks if b.level == "L3"]
    if l3:
        lines += ["", f"  ── 实质差异样例（共 {len(l3)} 条，列前 {min(top, len(l3))} 条）──"]
        for n, blk in enumerate(l3[:top], 1):
            lines.append(f"  [{n}] -{blk.left[:120]!r}")
            lines.append(f"      +{blk.right[:120]!r}")
    return "\n".join(lines)
