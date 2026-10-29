#!/usr/bin/env python3
r"""编辑校对：模板合规校验（B-002，report-only MVP）。

    python3 templatecheck.py --input <产品目录|初排稿.tex> [--out 报告.md]
                             [--template SLCN-MIX|SLEN|SLCN-MIX4|SLCN|/path/x.sty]
                             [--self-test] [--version]

**用在哪一步**：初排稿生成之后、人工一校之前（也可编辑随时自查）。它只回答一个问题：
「这份产物还符不符合本刊模板的硬要求？」——题名/作者/单位有没有落成占位符、摘要关键词
是否填了、参考文献与引用是否闭合、图/表有没有题注、`\usepackage` 与附带 `.sty` 是否
和模板一致、图片素材齐不齐。

**默认只报告，不阻断、不自动改**（与 `proofcheck.py` / `checkbib.py` 同一口径）：
本工具**没有** `--fix`；它只写一份 `_模板合规.md`，输入 `.tex`、`.bib`、`.sty` 与图片
一个字节都不动（测试用前后 sha256 全等钉住）。调用方**不得**把它当闸门用。

8 条规则（T1–T8）与 4 处降级（T2 EN 中文对应 INFO、T3 摘要长度 INFO、T4 未引用 INFO、
T5 编号连续 SKIP）见 `docs/TEMPLATE_RULES.md`。

退出码：`0` 没有 FAIL（PASS/SKIP）；`1` ≥1 条 FAIL（报告已写，正常情况）；
`2` 用法/输入/写报告错误。

canonical 模板 sha256 取 **public 仓** `编辑笔记/模板文件/`（Lead 2026-10-27 决策 D-07；
`--template` 可显式覆盖）。稿件仓同名副本**不作** canonical：两仓模板文件已分叉（B-189），
报告里如实披露，避免把「稿件仓副本更新」误判成产物不合规。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402
from slcn import fsutil as _fsutil  # noqa: E402
from slcn import bibparse as _bibparse  # noqa: E402
from slcn import templates as _templates  # noqa: E402
from slcn.proofcheck import raw_mask  # noqa: E402


# ── 规则表（唯一出处；报告与文档都从这里取标题/依据/建议）────────────────

@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    basis: str
    advice: str


RULES: tuple[Rule, ...] = (
    Rule("T1", "必备结构",
         "初排产物口径（`docs/ENGINE_API.md` 产物段、初排交接单）：documentclass + "
         "begin/end{document} + 参考文献 + 正文非空；不强制 \\section（实测 66/69 有，"
         "余 3 篇是书评/短文，不能当 FAIL）",
         "按模板重新生成（模板合规是生成器的责任，不是人工逐条补）"),
    Rule("T2", "题名/作者/单位",
         "本刊模板 CN_template.tex / EN_template.tex 的字段注释；占位符清单逐字取自模板"
         "（「在这里填上你的标题」「单位1（到系所）」「First Author」「Insert Your Title Here」等）",
         "回到原稿或 Word 源确认这几项，不要留模板占位符"),
    Rule("T3", "摘要/关键词",
         "摘要：模板字段；关键词 3–8：**task79-B-plan 内部口径**（手册/经验书均无这一条，"
         "见 docs/TEMPLATE_RULES.md §三降级清单）；摘要长度无阈值，只报字数（INFO）",
         "关键词按中文分号分隔列 3–8 个；摘要按模板字段填写"),
    Rule("T4", "参考文献",
         "PROOF_RULES 参考文献口径 + slcn/bibparse.py 的字段解析：author/title/year（date 可代 "
         "year）+ 类型化 venue；重复 key；\\cite 键与 bib 闭合。未引用条目只 INFO（不是规范）",
         "补全 bib 字段；删重复项；给无条目的 cite 键补条目或改键"),
    Rule("T5", "图/表题注",
         "模板 figure/table 环境搭配 \\caption；编号连续性与「图??」由 LaTeX 生成，源码不可判 → SKIP",
         "给每个 figure/table 补 \\caption{…}（内容不能为空）"),
    Rule("T6", "模板一致",
         "slcn/templates.py 的坑：骨架 tex 的 \\usepackage 必须与所选 sty 完全一致，否则编译报"
         "「File `X.sty' not found」且离真正原因很远",
         "把 \\usepackage 改成与模板同名（SLCN-MIX ≠ SLCN-MIX4 ≠ SLCN）"),
    Rule("T7", "模板 sha",
         "canonical 取 public 仓 `编辑笔记/模板文件/`（Lead 2026-10-27 D-07，实测 69/69 一致）；"
         "稿件仓副本不作 canonical —— 两仓分叉见 B-189，报告只对 public 侧负责",
         "从 `编辑笔记/模板文件/` 重新复制同名 .sty 到产品目录"),
    Rule("T8", "图片素材",
         "模板 \\includegraphics 的相对路径解析（本语料 0 份用 \\graphicspath）；"
         "同文件有 \\IfFileExists{target} 兜底视为通过（编译不会失败）",
         "把缺失图片补进产品目录，或改成实际存在的素材名"),
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"

MAX_LINES = 50          # 单规则明细上限（设计 §4）
SNIPPET = 52            # 表格「原文片段」截断长度


# ── 数据结构 ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """一条 FAIL 明细：行号 + 原文片段 + 问题。"""

    line: int
    snippet: str
    issue: str


@dataclass
class RuleResult:
    """一条规则的结论。status ∈ {PASS, FAIL, SKIP}；INFO 一律进 notes，不改变计数。"""

    rule: Rule
    status: str = STATUS_PASS
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_fail(self, line: int, snippet: str, issue: str) -> None:
        self.status = STATUS_FAIL
        self.findings.append(Finding(line=line, snippet=snippet, issue=issue))

    def add_note(self, text: str) -> None:
        self.notes.append(text)

    def mark_skip(self, why: str) -> None:
        self.status = STATUS_SKIP
        self.summary = why

    @property
    def count(self) -> int:
        return len(self.findings)


@dataclass
class Context:
    tex_path: str
    product_dir: str
    tex: str
    masked: str
    template_name: str
    template_path: str
    lang: str
    meta: dict[str, tuple[str, int]]


@dataclass
class Report:
    ctx: Context
    results: list[RuleResult]
    canonical_sha: str = ""
    product_sha: str = ""

    @property
    def stem(self) -> str:
        return os.path.splitext(os.path.basename(self.ctx.tex_path))[0]

    def counts(self) -> tuple[int, int, int]:
        return tuple(sum(1 for r in self.results if r.status == s)
                     for s in (STATUS_PASS, STATUS_FAIL, STATUS_SKIP))  # type: ignore[return-value]


class UsageError(Exception):
    """用法/输入错误 → rc2（消息直接打给用户，不带 traceback）。"""


# ── 小工具（纯函数，测试可直接调）────────────────────────────────────────

_DECOR_RE = re.compile(
    r"\\(?:kaishu|heiti|songti|fangsong|sffamily|bfseries|itshape|upshape|normalfont|"
    r"zihao|small|large|Large|bf|it|rm|tt|sf)\b\s*")
_CMD_RE = re.compile(r"\\[A-Za-z@]+\*?")


def strip_decor(value: str) -> str:
    """去掉 `\\kaishu` 之类字体装饰（只去命令名，不动参数文本）。"""
    return _DECOR_RE.sub("", value).strip()


def flat(value: str) -> str:
    """压成一行、去花括号/多余空白，给占位符判断用（显示仍用原文）。"""
    s = strip_decor(value)
    s = re.sub(r"[{}\s\\]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_blank(value: str | None) -> bool:
    """只有命令名/花括号/空白 = 空值。"""
    if value is None:
        return True
    s = _CMD_RE.sub("", strip_decor(value))
    return not re.sub(r"[{}\s]+", "", s)


# 占位符逐字取自模板（CN/EN）与真实产物里的「未识别」形态；见 docs/TEMPLATE_RULES.md §二
_PLACEHOLDER_RES = (
    re.compile(r"【\s*未识别"),
    re.compile(r"请手工填写"),
    re.compile(r"在这里填上"),
    re.compile(r"^null$"),
    re.compile(r"^第[一二三四五]作者$"),
    re.compile(r"^单位[一二三四五\d]*\s*(?:（[^）]*）|\([^)]*\))?$"),
    re.compile(r"^x{2,}$"),
    re.compile(r"^x{2,}@"),
    re.compile(r"^简短标题$"),
    re.compile(r"^标题$"),
    re.compile(r"^摘要$"),
    re.compile(r"^Insert\s"),
    re.compile(r"^First Author$"),
    re.compile(r"^Department\s+\d"),
    re.compile(r"本文讨论了一个非常重要的问题"),
    re.compile(r"^关键；很关键"),
)


def looks_placeholder(value: str | None) -> bool:
    """是不是还没填的模板占位符。"""
    if value is None:
        return False
    s = flat(value)
    return any(p.search(s) for p in _PLACEHOLDER_RES)


def group_at(text: str, i: int, reach: int = 80) -> tuple[int, int] | None:
    """从 i 往后找第一个 `{`，返回配对内容区间 (start, end)；不配平/太远 → None。

    只扫**掩码后**文本：注释里的花括号已被挖成空格，不会把配平带偏。
    """
    j = text.find("{", i)
    if j < 0 or j - i > reach:
        return None
    depth = 0
    k = j
    while k < len(text):
        ch = text[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1, k
        k += 1
    return None


def value_group_at(text: str, i: int, reach: int = 400) -> tuple[int, int] | None:
    """`\\newcommand{\\name}` 之后的**值**：i 到 `{` 之间必须只有空白/换行。

    模板把值写在下一行、注释在行尾（掩码后剩一大段对齐空格），所以不能用固定的
    「找第一个 `{`」：那会把下一个宏的值错认成这个宏的值。只认「紧跟着」的花括号组。
    """
    j = text.find("{", i, i + reach)
    if j < 0 or text[i:j].strip():
        return None
    return group_at(text, i, reach=reach)


def split_keywords(value: str) -> list[str]:
    """切关键词：先按 [;；,，、]；只有一个整体时再按 2+ 空格切。"""
    v = strip_decor(value).strip()
    if not v:
        return []
    parts = [p.strip() for p in re.split(r"[;；,，、]", v)]
    if len(parts) == 1:
        parts = [p.strip() for p in re.split(r"\s{2,}", v)]
    return [p for p in parts if p]


def text_length(value: str) -> tuple[int, int]:
    """返回 (汉字数, 拉丁词数)；只用于 INFO 报告，不判阈值。"""
    s = _CMD_RE.sub(" ", strip_decor(value))
    s = s.replace("{", "").replace("}", "")
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    words = len(re.findall(r"[A-Za-z0-9]+", s))
    return cjk, words


def line_of(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def cell(text: str, limit: int = SNIPPET) -> str:
    """表格单元格：压一行、转义 `|`、截断。"""
    s = re.sub(r"\s+", " ", str(text)).strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s.replace("|", "\\|")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 元数据解析（`\begin{document}` 前最后一次定义；注释不算）──────────────

_WATCHED = (
    "mytitle", "myfirstauthor", "myfirstaffiliation", "myabstract", "mykeywords",
    "mytitleEN", "myabstractEN", "mytitleCN", "myabstractCN",
    "myfirstauthorCN", "myfirstaffiliationCN",
)
_DEF_RE = re.compile(r"\\(?:re)?newcommand\*?\s*\{?\s*\\([A-Za-z]+)\s*\}?")


def parse_metadata(tex: str, masked: str) -> dict[str, tuple[str, int]]:
    """name → (原文值, 定义所在行)。同一名字多次定义取**最后**一次（模板惯例）。"""
    begin = masked.find("\\begin{document}")
    limit = begin if begin >= 0 else len(masked)
    out: dict[str, tuple[str, int]] = {}
    for m in _DEF_RE.finditer(masked, 0, limit):
        name = m.group(1)
        if name not in _WATCHED:
            continue
        g = value_group_at(masked, m.end())
        if not g:
            continue
        out[name] = (tex[g[0]:g[1]], line_of(masked, m.start()))
    return out


# ── 输入与模板解析 ───────────────────────────────────────────────────────

def _sty_names() -> tuple[str, ...]:
    return tuple(os.path.splitext(n)[0] for n in _templates.AVAILABLE_STY)


def resolve_input(path: str) -> tuple[str, str]:
    """`--input` → (tex 路径, 产品目录)。目录里优先 `^\\d{4}_\\d{2}_` 去掉后的 `<名_姓>.tex`。"""
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(p):
        raise UsageError(f"找不到输入：{p}")
    if os.path.isfile(p):
        if not p.lower().endswith(".tex"):
            raise UsageError(f"--input 必须是产品目录或 .tex 文件：{p}")
        return p, os.path.dirname(p)
    base = os.path.basename(p.rstrip(os.sep))
    m = re.match(r"^\d{4}_\d{2}_(.+)$", base)
    if m:
        want = os.path.join(p, m.group(1) + ".tex")
        if os.path.isfile(want):
            return want, p
    cands = sorted(os.path.join(p, n) for n in os.listdir(p)
                   if n.lower().endswith(".tex") and os.path.isfile(os.path.join(p, n)))
    if len(cands) == 1:
        return cands[0], p
    if not cands:
        raise UsageError(f"目录里没有 .tex：{p}")
    raise UsageError(f"目录里有 {len(cands)} 个 .tex，无法判断该查哪个（用 --input 指定）："
                     + "、".join(os.path.basename(c) for c in cands[:6]))


def _usepackage_names(masked: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", masked):
        for name in re.split(r"\s*,\s*", m.group(1)):
            name = name.strip()
            if name and name not in out:
                out.append(name)
    return out


def resolve_template(tex: str, arg: str) -> tuple[str, str]:
    """返回 (模板名, canonical 路径)；推不出来时返回 ("", "")，交给 T6/T7 报。"""
    names = _sty_names()
    if arg:
        raw = arg.strip()
        abs_like = os.sep in raw or (os.altsep is not None and os.altsep in raw)
        if not abs_like and raw in _templates.AVAILABLE_STY:
            # `--template SLCN-MIX.sty` 是模板目录里的名字，不是 cwd 的相对路径
            return os.path.splitext(raw)[0], os.path.join(_templates.TPL_DIR, raw)
        if abs_like or raw.lower().endswith(".sty"):
            p = os.path.abspath(os.path.expanduser(raw))
            if not os.path.isfile(p):
                raise UsageError(f"--template 指向的 .sty 不存在：{p}"
                                 f"（可用模板：{'、'.join(names)}）")
            if not p.lower().endswith(".sty"):
                raise UsageError("--template 必须是 .sty 文件")
            return os.path.splitext(os.path.basename(p))[0], p
        if raw in names:
            return raw, os.path.join(_templates.TPL_DIR, raw + ".sty")
        raise UsageError(f"未知模板：{raw}（可用：{'、'.join(names)}，或给 .sty 路径）")
    found = [n for n in _usepackage_names(raw_mask(tex)) if n in names]
    if len(found) == 1:
        return found[0], os.path.join(_templates.TPL_DIR, found[0] + ".sty")
    return "", ""


def guess_lang(tex: str, template_name: str) -> str:
    """语种只用来决定 T2/T3 查哪几组字段（不是汉字比例；英文稿也带中文字段）。"""
    if template_name == "SLEN":
        return "en"
    if template_name in ("SLCN-MIX", "SLCN-MIX4", "SLCN"):
        return "zh"
    masked = raw_mask(tex)
    if "\\mykeywords" in masked:
        return "zh"
    if "\\mytitleCN" in masked:
        return "en"
    return "zh"


def build_context(tex_path: str, product_dir: str, template_arg: str) -> Context:
    try:
        with open(tex_path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise UsageError(f"读不了文件：{exc}") from exc
    if b"\x00" in data:
        raise UsageError(f"输入像是二进制文件（含 NUL 字节）：{tex_path}")
    tex = data.decode("utf-8", errors="replace")
    masked = raw_mask(tex)
    name, path = resolve_template(tex, template_arg)
    return Context(
        tex_path=tex_path, product_dir=product_dir, tex=tex, masked=masked,
        template_name=name, template_path=path, lang=guess_lang(tex, name),
        meta=parse_metadata(tex, masked),
    )


# ── T1 必备结构 ──────────────────────────────────────────────────────────

def _has_text(s: str) -> bool:
    t = _CMD_RE.sub(" ", s)
    t = re.sub(r"[{}\[\]$&\\~^_`']", " ", t)
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", t)) >= 2


def check_structure(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[0])
    masked = ctx.masked
    if masked.count("\\documentclass") == 0:
        res.add_fail(0, "（全文）", "缺少 \\documentclass")
    begin = masked.find("\\begin{document}")
    end = masked.find("\\end{document}")
    n_begin = masked.count("\\begin{document}")
    n_end = masked.count("\\end{document}")
    if n_begin == 0:
        res.add_fail(0, "（全文）", "缺少 \\begin{document}")
    elif n_begin > 1:
        res.add_fail(line_of(masked, begin), "\\begin{document}",
                     f"出现 {n_begin} 次（应恰好 1 次）")
    if n_end == 0:
        res.add_fail(0, "（全文）", "缺少 \\end{document}")
    elif n_end > 1:
        res.add_fail(line_of(masked, end), "\\end{document}",
                     f"出现 {n_end} 次（应恰好 1 次）")
    if begin >= 0 and end >= 0 and end < begin:
        res.add_fail(line_of(masked, end), "\\end{document}", "\\end{document} 在 \\begin{document} 之前")
    if "\\printbibliography" not in masked and "\\begin{thebibliography}" not in masked:
        res.add_fail(0, "（全文）", "缺少参考文献输出（\\printbibliography 或 thebibliography）")
    body = ""
    if begin >= 0 and end > begin:
        body = masked[begin + len("\\begin{document}"):end]
    paras = [ln for ln in body.splitlines() if _has_text(ln)]
    if not paras:
        res.add_fail(0, "（全文）", "document 环境里没有正文（≥1 段）")
    if res.status == STATUS_FAIL:
        res.summary = f"{res.count} 项结构问题"
    else:
        res.summary = (f"结构齐全：documentclass、begin/end document、参考文献、正文 {len(paras)} 段")
    res.add_note(f"实测 66/69 篇有 \\section；不强制 \\section（余 3 篇是书评/短文）")
    return res


# ── T2 题名/作者/单位 ───────────────────────────────────────────────────

def check_metadata(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[1])
    fields = (("mytitle", "题名"), ("myfirstauthor", "第一作者"),
              ("myfirstaffiliation", "第一作者单位"))
    for name, label in fields:
        item = ctx.meta.get(name)
        if item is None:
            res.add_fail(0, f"\\{name}", f"缺少 {label} 定义")
            continue
        value, line = item
        if is_blank(value):
            res.add_fail(line, f"\\{name}", f"{label}为空")
        elif looks_placeholder(value):
            res.add_fail(line, value, f"{label}仍是模板占位符：{flat(value)[:30]}")
    # EN 模板的中文字段：模板明示可空/可为 null → 只 INFO
    if ctx.lang == "en":
        for name, label in (("mytitleCN", "中文题名"), ("myfirstauthorCN", "中文第一作者"),
                            ("myfirstaffiliationCN", "中文第一作者单位")):
            item = ctx.meta.get(name)
            if item is None:
                res.add_note(f"{label}（{name}）未定义——EN 模板允许，INFO")
            elif is_blank(item[0]) or flat(item[0]) == "null":
                res.add_note(f"{label}（{name}）为空/null——EN 模板允许，INFO")
            elif looks_placeholder(item[0]):
                res.add_note(f"{label}（{name}）仍是占位符「{flat(item[0])[:24]}」——INFO")
    if res.status != STATUS_FAIL:
        res.summary = "题名/第一作者/第一作者单位均已填写（无占位符）"
        res.add_note("只看 active 第一作者；第二作者及以后常为 null/注释，不判")
    else:
        res.summary = f"{res.count} 项未填/占位"
    return res


# ── T3 摘要/关键词 ───────────────────────────────────────────────────────

def check_abstract(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[2])
    item = ctx.meta.get("myabstract")
    if item is None:
        res.add_fail(0, "\\myabstract", "缺少摘要定义")
    else:
        value, line = item
        if is_blank(value):
            res.add_fail(line, "\\myabstract", "摘要为空")
        elif looks_placeholder(value):
            res.add_fail(line, value, f"摘要仍是模板占位符：{flat(value)[:30]}")
        else:
            cjk, words = text_length(value)
            res.add_note(f"摘要字数：汉字 {cjk} / 拉丁词 {words}（仅记录；手册无长度阈值，INFO）")
    kw = ctx.meta.get("mykeywords")
    if kw is None:
        if ctx.lang == "en":
            res.add_note("EN 模板无 \\mykeywords —— 关键词 3–8 检查 SKIP（这是 SKIP，不是 FAIL）")
        else:
            res.add_fail(0, "\\mykeywords", "缺少关键词定义")
    elif is_blank(kw[0]) or looks_placeholder(kw[0]):
        res.add_fail(kw[1], kw[0], f"关键词为空/占位：{flat(kw[0])[:30]}")
    else:
        keys = split_keywords(kw[0])
        if len(keys) < 3 or len(keys) > 8:
            res.add_fail(kw[1], kw[0],
                         f"关键词 {len(keys)} 个（应为 3–8）：{('、'.join(keys))[:40]}")
        else:
            res.add_note(f"关键词 {len(keys)} 个：" + "、".join(keys)[:60])
    if res.status != STATUS_FAIL:
        res.summary = "摘要已填；关键词检查通过" + ("（EN 无 keywords，SKIP）" if ctx.lang == "en" else "")
    else:
        res.summary = f"{res.count} 项摘要/关键词问题"
    return res


# ── T4 参考文献 ──────────────────────────────────────────────────────────

_VENUE_FIELDS = {
    "article": ("journal", "journaltitle"),
    "book": ("publisher",),
    "incollection": ("booktitle",),
    "inproceedings": ("booktitle",),
    "conference": ("booktitle",),
    "thesis": ("school", "institution"),
    "phdthesis": ("school", "institution"),
    "mastersthesis": ("school", "institution"),
    "online": ("url", "urldate"),
    "misc": ("note", "howpublished", "url"),
    "techreport": ("institution",),
    # 标准 BibTeX 类型也认（不认的才按「未知类型」报 FAIL）
    "unpublished": ("note",),
    "proceedings": ("publisher",),
    "inbook": ("booktitle", "publisher"),
    "manual": ("organization",),
    "booklet": ("howpublished",),
}
_CITE_RE = re.compile(r"\\(?:no)?cite\w*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")


def _first_group_arg(masked: str, m: re.Match) -> str:
    g = group_at(masked, m.end())
    return masked[g[0]:g[1]].strip() if g else ""


def _bib_files(ctx: Context) -> tuple[list[str], int]:
    """返回 (存在的 bib 相对路径清单, 首个引用所在行)；没有引用 → ([], 0)。"""
    paths: list[str] = []
    line = 0
    for pat in (r"\\addbibresource\s*", r"\\bibliography\s*"):
        for m in re.finditer(pat, ctx.masked):
            arg = _first_group_arg(ctx.masked, m)
            for raw in re.split(r"\s*,\s*", arg):
                raw = raw.strip()
                if not raw:
                    continue
                if not raw.lower().endswith(".bib"):
                    raw += ".bib"
                full = raw if os.path.isabs(raw) else os.path.join(ctx.product_dir, raw)
                if full not in paths:
                    paths.append(full)
                    line = line or line_of(ctx.masked, m.start())
    return paths, line


def check_references(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[3])
    bib_files, bib_line = _bib_files(ctx)
    missing = [p for p in bib_files if not os.path.isfile(p)]
    for p in missing:
        res.add_fail(bib_line, os.path.basename(p), f"引用的 .bib 不存在：{p}")
    entries: dict[str, _bibparse.Entry] = {}
    if bib_files and not missing:
        for path in bib_files:
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                res.add_fail(bib_line, os.path.basename(path), f"读不了 .bib：{exc}")
                continue
            parsed, problems = _bibparse.parse_entries(text)
            for e in parsed:
                if e.key in entries:
                    res.add_fail(e.line, f"@{e.etype}{{{e.key}",
                                 f"重复的 bib key（同 {os.path.basename(path)}）")
                else:
                    entries[e.key] = e
            for prob in problems:
                if prob.level == _bibparse.LEVEL_PROBLEM:
                    res.add_fail(0, prob.key or "（整份文件）", prob.detail)
        for key, e in entries.items():
            have = {k.lower() for k in e.fields}
            lacks = []
            if not ({"author", "editor"} & have):
                lacks.append("author")
            if "title" not in have:
                lacks.append("title")
            if not ({"year", "date"} & have):
                lacks.append("year")
            venue = _VENUE_FIELDS.get(e.etype.lower())
            if venue is None:
                res.add_fail(e.line, f"@{e.etype}{{{e.key}",
                             f"未知条目类型 @{e.etype}（无法判断必填 venue）")
            elif not (set(venue) & have):
                lacks.append("/".join(venue))
            if lacks:
                res.add_fail(e.line, f"@{e.etype}{{{e.key}",
                             "缺字段：" + "、".join(lacks))
    cited: list[tuple[str, int]] = []
    for m in _CITE_RE.finditer(ctx.masked):
        for key in re.split(r"\s*,\s*", m.group(1)):
            key = key.strip()
            if key and key != "*":
                cited.append((key, line_of(ctx.masked, m.start())))
    unknown = [(k, ln) for k, ln in cited if entries and k not in entries]
    for key, ln in unknown:
        res.add_fail(ln, f"\\cite{{{key}}}", "cite 键在 bib 里没有对应条目")
    bibitems: set[str] = set()
    if not bib_files:
        for m in re.finditer(r"\\bibitem\s*(?:\[[^\]]*\])?\s*", ctx.masked):
            arg = _first_group_arg(ctx.masked, m)
            if arg:
                bibitems.add(arg)
        for key, ln in cited:
            if key not in bibitems:
                res.add_fail(ln, f"\\cite{{{key}}}", "cite 键没有对应 \\bibitem")
        res.add_note("没有 .bib 引用：字段检查 SKIP，只查 \\bibitem 闭合")
    if entries:
        uncited = [k for k in entries if k not in {c for c, _ in cited}]
        if uncited:
            res.add_note(f"未引用条目 {len(uncited)} 条（INFO，不算 FAIL）："
                         + "、".join(uncited[:5]) + ("…" if len(uncited) > 5 else ""))
    if res.status == STATUS_FAIL:
        res.summary = f"{res.count} 处参考文献问题"
    elif not bib_files:
        res.mark_skip("无 .bib 引用；只查 \\cite ↔ \\bibitem 闭合（字段检查 SKIP）")
    else:
        res.summary = f"{len(entries)} 条 bib 条目字段齐全、cite 键全闭合"
    return res


# ── T5 图/表题注 ─────────────────────────────────────────────────────────

def _caption_ok(block: str) -> tuple[bool, bool]:
    """返回 (有 caption, 至少一个非空)；块已掩码。"""
    found = False
    for m in re.finditer(r"\\caption\*?", block):
        found = True
        g = group_at(block, m.end())
        if not g:
            continue
        body = re.sub(r"\\label\s*\{[^{}]*\}", " ", block[g[0]:g[1]])
        if _has_text(body):
            return True, True
    return found, False


def check_captions(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[4])
    pat = re.compile(r"\\begin\{(figure|table)(\*?)\}(.*?)(\\end\{\1\2\}|\Z)", re.S)
    n_blocks = 0
    for m in pat.finditer(ctx.masked):
        n_blocks += 1
        line = line_of(ctx.masked, m.start())
        block = m.group(3)
        if not m.group(4):
            res.add_fail(line, m.group(0)[:40], f"{m.group(1)} 环境没有闭合 \\end")
            continue
        found, nonempty = _caption_ok(block)
        if not found:
            res.add_fail(line, m.group(0)[:40], f"{m.group(1)} 没有 \\caption")
        elif not nonempty:
            res.add_fail(line, m.group(0)[:40], f"{m.group(1)} 的 \\caption 内容为空")
    if n_blocks == 0:
        res.mark_skip("没有 figure/table 环境，无可查")
        res.add_note("编号连续性 / 「图??」由 LaTeX 生成，源码不可判 → SKIP")
        return res
    res.add_note("编号连续性 / 「图??」由 LaTeX 生成，源码不可判 → SKIP")
    if res.status == STATUS_FAIL:
        res.summary = f"{res.count} 个图/表块缺题注或未闭合（共 {n_blocks} 块）"
    else:
        res.summary = f"{n_blocks} 个 figure/table 块都有非空 \\caption"
    return res


# ── T6 模板一致 ─────────────────────────────────────────────────────────

def check_usepackage(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[5])
    names = [n for n in _usepackage_names(ctx.masked) if n in _sty_names()]
    if not names:
        res.add_fail(0, "（全文）", "没有可识别的模板 \\usepackage（SLCN-MIX/SLEN/…）")
    elif len(names) > 1:
        res.add_fail(0, "、".join(names), f"同时加载了 {len(names)} 个模板：{'、'.join(names)}")
    elif not ctx.template_name:
        res.add_fail(0, names[0], "模板名无法确定（--template 未给，且推断失败）")
    elif names[0] != ctx.template_name:
        res.add_fail(0, f"\\usepackage{{{names[0]}}}",
                     f"与模板 {ctx.template_name} 不一致（精确比对，SLCN-MIX ≠ SLCN-MIX4）")
    if res.status == STATUS_FAIL:
        res.summary = f"{res.count} 处模板加载不一致"
    else:
        res.summary = f"\\usepackage{{{names[0]}}} 与模板 {ctx.template_name} 完全一致"
    return res


# ── T7 模板 sha ─────────────────────────────────────────────────────────

def check_template_sha(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[6])
    canonical = ctx.template_path
    if not canonical or not os.path.isfile(canonical):
        res.mark_skip(f"canonical 模板不存在：{canonical or '（未确定模板）'}，无法比对 sha")
        res.add_note("canonical 取 public 仓 `编辑笔记/模板文件/`；稿件仓副本不作 canonical（B-189）")
        return res
    name = os.path.basename(canonical)
    product = os.path.join(ctx.product_dir, name)
    canon_sha = sha256_file(canonical)
    if not os.path.isfile(product):
        res.add_fail(0, name, f"产品目录缺少 {name}（初排产物应随稿附带模板）")
        res.summary = f"产品目录缺少 {name}"
    else:
        prod_sha = sha256_file(product)
        if prod_sha != canon_sha:
            res.add_fail(0, name, f"sha256 不一致：产物 {prod_sha[:12]}… ≠ canonical {canon_sha[:12]}…")
            res.summary = f"{name} 与 canonical 不一致"
        else:
            res.summary = f"{name} 与 canonical 一致（sha256 {canon_sha[:12]}…）"
    res.add_note(f"canonical：{canonical}")
    res.add_note("canonical 取 public 仓 `编辑笔记/模板文件/`；稿件仓副本不作 canonical（B-189 两仓分叉）")
    return res


# ── T8 图片素材 ─────────────────────────────────────────────────────────

_IMG_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")


def _image_exists(product_dir: str, target: str) -> bool:
    p = target if os.path.isabs(target) else os.path.join(product_dir, target)
    if os.path.isfile(p):
        return True
    if not os.path.splitext(target)[1]:
        return any(os.path.isfile(p + ext) for ext in _IMG_EXT)
    return False


def _has_if_exists(ctx: Context, target: str) -> bool:
    for m in re.finditer(r"\\IfFileExists\s*", ctx.masked):
        g = group_at(ctx.masked, m.end())
        if not g:
            continue
        arg = ctx.masked[g[0]:g[1]].strip()
        if arg == target or os.path.splitext(os.path.basename(arg))[0] == \
                os.path.splitext(os.path.basename(target))[0]:
            return True
    return False


def check_images(ctx: Context) -> RuleResult:
    res = RuleResult(RULES[7])
    unparsed = 0
    n = 0
    for m in re.finditer(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*", ctx.masked):
        g = group_at(ctx.masked, m.end())
        if not g:
            continue
        target = ctx.tex[g[0]:g[1]].strip().strip("{}").strip()
        if not target:
            continue
        if "\\" in target or "#" in target:
            unparsed += 1
            continue
        n += 1
        line = line_of(ctx.masked, m.start())
        if _image_exists(ctx.product_dir, target):
            continue
        if _has_if_exists(ctx, target):
            res.add_note(f"缺 {target}，但同文件有 \\IfFileExists 兜底 → 视为通过")
            continue
        res.add_fail(line, f"\\includegraphics{{{target}}}", f"图片不存在：{target}")
    if n == 0 and unparsed == 0:
        res.mark_skip("没有可解析的 \\includegraphics，无可查")
        return res
    if unparsed:
        res.add_note(f"{unparsed} 处 \\includegraphics 的目标是宏/无法解析（SKIP）")
    if res.status == STATUS_FAIL:
        res.summary = f"{res.count} 张图片缺失（可解析 {n} 张）"
    else:
        res.summary = f"{n} 张图片素材齐全" + ("（含 \\IfFileExists 兜底）" if res.notes else "")
    return res


CHECKS = (check_structure, check_metadata, check_abstract, check_references,
          check_captions, check_usepackage, check_template_sha, check_images)


# ── 主流程 ──────────────────────────────────────────────────────────────

def check_product(tex_path: str, template_arg: str = "") -> Report:
    """对一份产物跑 8 条规则（纯读，不写任何文件）。"""
    tex_path = os.path.abspath(tex_path)
    ctx = build_context(tex_path, os.path.dirname(tex_path), template_arg)
    results = [fn(ctx) for fn in CHECKS]
    rep = Report(ctx=ctx, results=results)
    if ctx.template_path and os.path.isfile(ctx.template_path):
        rep.canonical_sha = sha256_file(ctx.template_path)
        prod = os.path.join(ctx.product_dir, os.path.basename(ctx.template_path))
        if os.path.isfile(prod):
            rep.product_sha = sha256_file(prod)
    return rep


def render_report(rep: Report) -> str:
    ctx = rep.ctx
    p, f, s = rep.counts()
    out: list[str] = []
    out.append(f"# 模板合规报告：{rep.stem}")
    out.append("")
    out.append("**只报告，不阻断、不自动改；输入文件未修改。** 本报告由 "
               "`Engine/templatecheck.py` 生成（report-only MVP，B-002）。")
    out.append("")
    out.append("| 项目 | 值 |")
    out.append("|---|---|")
    out.append(f"| 源文件 | `{cell(ctx.tex_path, 200)}` |")
    out.append(f"| 产品目录 | `{cell(ctx.product_dir, 200)}` |")
    tpl = f"`{ctx.template_name}`" if ctx.template_name else "（未确定，见 T6）"
    out.append(f"| 模板 | {tpl} |")
    out.append(f"| 语种 | {ctx.lang} |")
    out.append(f"| 结论 | PASS {p} / FAIL {f} / SKIP {s} |")
    out.append(f"| 生成时间 | {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')} |")
    out.append("")
    out.append("canonical 模板 sha256（取 public 仓 `编辑笔记/模板文件/`，Lead 2026-10-27 "
               "决策 D-07）：")
    out.append("")
    if rep.canonical_sha:
        out.append(f"- canonical：`{cell(ctx.template_path, 200)}`")
        out.append(f"- canonical sha256：`{rep.canonical_sha}`")
        prod = os.path.join(ctx.product_dir, os.path.basename(ctx.template_path))
        out.append(f"- 产物副本：`{cell(prod, 200)}`"
                   + (f"，sha256：`{rep.product_sha}`" if rep.product_sha else "（缺失）"))
        out.append("- 稿件仓同名副本**不作** canonical：两仓模板文件已分叉（B-189），"
                   "本报告只对 public 侧负责。")
    else:
        out.append("- （未能定位 canonical 模板，见 T7）")
    out.append("")
    out.append("## 摘要")
    out.append("")
    out.append("| # | 规则 | 结果 | 说明 |")
    out.append("|---|---|---|---|")
    for r in rep.results:
        out.append(f"| {r.rule.id} | {r.rule.title} | **{r.status}** | {cell(r.summary, 90)} |")
    out.append("")
    out.append("## 逐条")
    out.append("")
    for r in rep.results:
        out.append(f"### `{r.rule.id}` {r.rule.title} —— {r.status}")
        out.append("")
        out.append(f"依据：{r.rule.basis}")
        out.append("")
        out.append(f"建议：{r.rule.advice}")
        out.append("")
        if r.status == STATUS_SKIP:
            out.append(f"原因：{r.summary}")
            out.append("")
        elif r.status == STATUS_PASS:
            out.append(f"证据：{r.summary}")
            out.append("")
        if r.findings:
            out.append("| 行 | 原文片段 | 问题 |")
            out.append("|---|---|---|")
            for fd in r.findings[:MAX_LINES]:
                out.append(f"| {fd.line or '—'} | `{cell(fd.snippet)}` | {cell(fd.issue, 110)} |")
            out.append("")
            if len(r.findings) > MAX_LINES:
                out.append(f"另有 {len(r.findings) - MAX_LINES} 条已省略（共 {len(r.findings)} 条）。")
                out.append("")
        for note in r.notes:
            out.append(f"- 备注（INFO）：{note}")
        if r.notes:
            out.append("")
    return "\n".join(out) + "\n"


def _default_out(ctx: Context) -> str:
    stem = os.path.splitext(os.path.basename(ctx.tex_path))[0]
    return os.path.join(ctx.product_dir, f"{stem}_模板合规.md")


def _resolve_out(out_arg: str, ctx: Context) -> str:
    p = os.path.abspath(os.path.expanduser(out_arg))
    if not p.lower().endswith(".md"):
        raise UsageError(f"--out 必须是 .md 文件：{p}")
    if os.path.abspath(ctx.tex_path) == p:
        raise UsageError("--out 不能指向输入的 .tex")
    if not os.path.isdir(os.path.dirname(p)):
        raise UsageError(f"--out 的父目录不存在：{os.path.dirname(p)}")
    if os.path.isdir(p):
        raise UsageError(f"--out 指向的是目录：{p}")
    return p


# ── 自检（临时目录，不碰仓库）────────────────────────────────────────────

_SELFTEST_TEX = r"""\documentclass[UTF8]{article}
\usepackage{SLCN-MIX}
\newcommand{\mytitle}{模板合规自检标题}
\newcommand{\myfirstauthor}{\kaishu 陈自检}
\newcommand{\myfirstaffiliation}{中山大学逻辑与认知研究所}
\newcommand{\myabstract}{这是一段用于自检的中文摘要，讨论模板合规校验本身。}
\newcommand{\mykeywords}{逻辑；模态；证明论}
\addbibresource{reference.bib}
\begin{document}
\section{引言}
这是一段正文，引用一条文献\cite{Cariani2016}。
\begin{figure}
\centering
\includegraphics[width=.5\textwidth]{fig.png}
\caption{自检插图}
\end{figure}
\begin{table}
\caption{自检表格}
\end{table}
\printbibliography
\end{document}
"""

_SELFTEST_BIB = """@article{Cariani2016,
  author = {Cariani, Fabrizio},
  title = {A Note on Models},
  journal = {Journal of Logic},
  year = {2016}
}
"""


def self_test() -> int:
    """造一份干净 CN 产品 + 3 个关键反例，验证 8/8 正例与 3/3 反例。"""
    tpl = os.path.join(_templates.TPL_DIR, "SLCN-MIX.sty")
    if not os.path.isfile(tpl):
        print(f"❌ 自检失败：找不到 canonical 模板 {tpl}", file=sys.stderr)
        return 1
    ok_n = 0
    bad_n = 0
    with tempfile.TemporaryDirectory(prefix="slcn_tpl_") as tmp:
        product = os.path.join(tmp, "2024_03_Kai_Chen")
        os.makedirs(product)
        tex = os.path.join(product, "Kai_Chen.tex")
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write(_SELFTEST_TEX)
        with open(os.path.join(product, "reference.bib"), "w", encoding="utf-8") as fh:
            fh.write(_SELFTEST_BIB)
        with open(os.path.join(product, "fig.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n selftest")
        with open(tpl, "rb") as src, open(os.path.join(product, "SLCN-MIX.sty"), "wb") as dst:
            dst.write(src.read())

        rep = check_product(tex)
        for r in rep.results:
            if r.status == STATUS_PASS:
                ok_n += 1
            else:
                print(f"  ❌ 正例里 {r.rule.id} 不是 PASS：{r.status} {r.summary}")
        # 反例 1：去掉 \begin{document} → T1 FAIL
        bad_n += _selftest_case(tex, lambda t: t.replace("\\begin{document}", ""), "T1",
                                "删 \\begin{document}")
        # 反例 2：关键词只剩 2 个 → T3 FAIL
        bad_n += _selftest_case(tex, lambda t: t.replace("逻辑；模态；证明论", "逻辑；模态"),
                                "T3", "关键词 2 个")
        # 反例 3：删掉 fig.png → T8 FAIL（改完恢复）
        os.remove(os.path.join(product, "fig.png"))
        r = {x.rule.id: x for x in check_product(tex).results}["T8"]
        if r.status == STATUS_FAIL:
            bad_n += 1
        else:
            print(f"  ❌ 反例 T8（缺 fig.png）没有 FAIL：{r.status}")
    if ok_n == 8 and bad_n == 3:
        print("自检通过：8/8 规则正例 + 3/3 反例")
        return 0
    print(f"自检失败：正例 {ok_n}/8，反例 {bad_n}/3", file=sys.stderr)
    return 1


def _selftest_case(tex: str, mutate, rule_id: str, label: str) -> int:
    """在临时副本上改一处再跑，命中指定规则记 1 分。"""
    with open(tex, encoding="utf-8") as fh:
        original = fh.read()
    try:
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write(mutate(original))
        hit = {r.rule.id: r for r in check_product(tex).results}[rule_id]
    finally:
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write(original)
    if hit.status == STATUS_FAIL:
        return 1
    print(f"  ❌ 反例 {rule_id}（{label}）没有 FAIL：{hit.status}")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="templatecheck.py",
        description="模板合规校验：8 条硬规则，只报告、不阻断、不自动改（B-002 MVP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 无 FAIL / 1 ≥1 FAIL（报告已写）/ 2 用法或输入/写报告错误",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--input", default=None, metavar="产品目录|初排稿.tex",
                    help="要校验的产品目录或 .tex（目录里优先 <名_姓>.tex）")
    ap.add_argument("--out", default=None, metavar="报告.md",
                    help="报告写到这个 .md（默认 <产品目录>/<stem>_模板合规.md）")
    ap.add_argument("--template", default="", metavar="SLCN-MIX|SLEN|/path/x.sty",
                    help="模板名或 .sty 路径（默认按 \\usepackage 推断）")
    ap.add_argument("--self-test", action="store_true",
                    help="用临时目录造正例+3 个反例自检（不碰仓库、不写 cwd）")
    args = ap.parse_args(argv)

    if args.self_test:
        if args.input:
            print("\n❌ --self-test 与 --input 互斥\n", file=sys.stderr)
            return 2
        return self_test()
    if not args.input:
        print("\n❌ 缺少 --input（产品目录或初排稿 .tex）；--self-test / --version 除外\n",
              file=sys.stderr)
        return 2
    try:
        tex_path, _product_dir = resolve_input(args.input)
        rep = check_product(tex_path, args.template)
        ctx = rep.ctx
        out_path = _default_out(ctx) if not args.out else _resolve_out(args.out, ctx)
    except UsageError as exc:
        print(f"\n❌ {exc}\n", file=sys.stderr)
        return 2

    print(f"模板合规：{rep.stem}（模板 {ctx.template_name or '未确定'}，语种 {ctx.lang}）\n")
    for r in rep.results:
        print(f"  {r.rule.id}  {r.status:<4}  {r.rule.title} —— {r.summary}")
        for fd in r.findings[:2]:
            where = f"第 {fd.line} 行" if fd.line else "全文"
            print(f"         {where}：{fd.issue}")
    try:
        _fsutil.atomic_write(out_path, render_report(rep), newline="", log=print)
    except OSError as exc:
        print(f"\n❌ 写报告失败：{exc}\n   换一个可写位置：--out /tmp/<名>_模板合规.md\n",
              file=sys.stderr)
        return 2
    p, f, s = rep.counts()
    print()
    # 契约行（`docs/ENGINE_API.md`）：外壳/脚本靠它拿三个计数与报告路径
    print(f"完成。模板合规：PASS {p} / FAIL {f} / SKIP {s}（报告：{out_path}）")
    return 1 if f else 0


if __name__ == "__main__":
    raise SystemExit(main())
