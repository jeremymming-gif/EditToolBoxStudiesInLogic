#!/usr/bin/env python3
r"""B-375R 目录链鲁棒性回归测试（D-P13-01 折行劈宏 / 真实旧模板兜底 / D-P13-04 不可写）。

对应缺陷（批次外最小返工 B375-R13-20261111）：
  D-P13-01 形态①：题名按**字符**折行会在 TeX 控制序列内部下刀（`\edash` 被切成
                 `\` + newline + `edash`），落位 tex 变成 `{\large …\}`，xelatex 报
                 `! File ended while scanning use of \TX@get@body.`。
  D-P13-01 形态②：真实旧 000 模板（期 90 等）只有 `\cndash`、没有 `\edash` 定义，
                 产物含 `\edash` 时 xelatex 报 `! Undefined control sequence.`。
  D-P13-04：期根目录不可写（chmod 0555）时 `os.makedirs(…/.toolbox-tmp)` 抛
            PermissionError，CLI 裸 traceback + rc1；契约要求 rc2 + 中文原因 + 零写。

契约断言（不写死实现细节，只用公开 API）：
  R01 折行只在 TeX 原子边界：`\edash`/`\cndash`/`\textit{…}` 参数组/平衡 `{}`/
      `--`/`——` 不得被劈开；两行拼回后控制序列数与原文一致；代表用例可被 xelatex 编译。
  R02 真实旧 000 模板缺 `\edash` 时兜底注入 `\providecommand{\edash}`/`\cndash`：
      位置在 `\begin{document}` 之前、所有 `\usepackage` 之后；模板已定义则不重复注入/
      不覆盖；只在标题实际用到时才注入；端到端可编译。
  R03 期根 0555：CLI rc2，stderr 含中文「目录生成失败」「权限不足」、不含 Traceback，
      期目录前后文件清单+SHA 完全一致（零写），不写 result JSON。
  R04 原有断点语义不回归（期 90「知识论回应」金标准断点）。

跑法（工程口径）：
  cd <checkout>/Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
      tests/test_pkg_b375r_toc_robustness.py
纯标准库；xelatex 不可用时编译类用例 SKIP（不计失败）。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import toc as TOC          # noqa: E402
from slcn import toctemplate as TT    # noqa: E402

PY = sys.executable
XELATEX = shutil.which("xelatex") or (
    "/Library/TeX/texbin/xelatex"
    if os.path.exists("/Library/TeX/texbin/xelatex") else None)

pass_n = 0
fails: list[str] = []
skips: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def skip(name: str, detail: str = "") -> None:
    skips.append(name)
    print(f"SKIP  {name}" + (f"  {detail}" if detail else ""))


# ── 真实旧 000 模板（期 90 `000 Table of Contents/Chinese Content.tex` 逐字副本）──
# 来源：稿件仓 Studies_in_Logic_Database/2026年第5期(90)/000 Table of Contents/Chinese Content.tex
# 特征：head 只有 `\cndash` 定义、没有 `\edash`（本轮 D-P13-01 形态② 的真实素材）。
# 下面 `LEGACY_TEMPLATE` 由构建脚本从该文件逐字注入；再抄一遍易漏字节，故不手打。
LEGACY_TEMPLATE = '\\documentclass[b5paper,11pt,onecolumn,twoside]{ctexart}\n\\pagestyle{empty}\n\\usepackage[total={130mm,203mm}, top=25.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式\n\\usepackage{tabularx}\n\\usepackage{amssymb}\n\\usepackage{xeCJK}\n\\setmainfont{Times New Roman}\n\n\\newcommand{\\vdistance}{\\\\[0.13ex]}%调整行间隔\n\\newcommand{\\cndash}{\\raise0.1em\\hbox{--}}\n\n\\begin{document}\n\n\\begin{flushright}\n\\renewcommand{\\baselinestretch}{1.3}\n\\zihao{5}\n\\heiti{逻辑学研究（双月刊）\\\\\n第\\ 19 卷，第\\ 5 期，总第\\ 90 期}\\\\\n\\heiti{2026 年第\\ 5 期，10月\\ 18 日出版}\n\\end{flushright}\n\\vspace{-3.5ex}{\\songti{\\huge{\\bfseries  目 \\ \\ \\ \\ \\ \\ \\ \\ 录}}}\n\n\\noindent\\hrulefill \\vspace{2.5ex}\\\\\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.5em}}r@{\\hspace{-4.8em}}r}\t\n{\\large \\bf{专栏：认知悖论研究}}\n&&\\textit{\\large 主持人：张建军}\n\\\\[3.2 ex]\n\n\n%中间主体\n\n{\\large ~~~~ 可知性悖论、形式可证与认知实现}\n&\\textit{\\large ~~~~ 寇静茹，刘叶涛}\n& 1\\\\\n\n\\vdistance\n\n{\\large ~~~~ 可知性悖论的“真矛盾论”解决方案探析}\n&\\textit{\\large ~~~~ 雒自新，王璇}\n& 17\\\\\n\n\\vdistance\n\n{\\large ~~~~ 论雅辛之谜及其解决方案}\n\\\\{\\large ~~~~ ——从扩展的RZH标准看}\n&\\textit{\\large ~~~~ 张亮}\n& 28\\\\\n\n\\vdistance\n\n{\\large ~~~~ 从“主动推理”看“意外”}\n\\\\{\\large ~~~~ ——意外考试悖论新探}\n&\\textit{\\large ~~~~ 黄海婷，张建军}\n& 46\\\\\n\n\\vdistance\n\n{\\large 推理倾向性：对紧缩真理论概括难题的}\n\\\\{\\large 知识论回应}\n&\\textit{\\large 高贝贝}\n& 63\\\\\n\n\\vdistance\n\n{\\large 自我确认与能动性}\n&\\textit{\\large 唐英英}\n& 77\\\\\n\n\\vdistance\n\n{\\large 经验贝叶斯主义的基本方法及其理论前景}\\\\\n{\\large ——评赖兴巴赫的概率归纳理论}\n&\\textit{\\large 赵亮英，陈晓平}\n& 95\\\\\n\n\\vdistance\n\n{\\large 重建与生成：语用论辩学与广义论证之比较}\n&\\textit{\\large 廖彦霖}\n& 119\\\\\n\n\\vdistance\n\n{\\large 论证行动如何生成？}\\\\\n{\\large ——一个基于广义论证理论的生成模型}\n&\\textit{\\large 鉏佳威}\n& 137\\\\\n\n\\vdistance\n\n{\\large 当代逻辑哲学鸟瞰}\n&\\textit{\\large 陈波}\n& 153\\\\\n\n\\end{tabularx}\n\\end{document}\n\n'
LEGACY_SRC_SHA256 = "02d16f63035818d83e73a399535713a8227d2b4dc61fc17f04e20e40cb2ec61e"
LEGACY_HEAD = LEGACY_TEMPLATE.split("\\begin{tabularx}", 1)[0]

# 合成报告里的代表标题（D-P13-01 形态① 的最小复现标题，base 上折行点恰好落在 `\` 后）
D01A_TITLE = (
    r"目录折行安全边界测试用例甲乙丙丁戊己AB \edash "
    r"11模型构造与证明论方法研究若干问题")


# ══════════════════════════════════════════════════════════════════════
# 测试侧独立的 TeX 结构检查器（不复用实现里的私有函数，避免「自己验自己」）
# ══════════════════════════════════════════════════════════════════════

def _control_tokens(text: str) -> list[str]:
    r"""抽出 TeX 控制序列：控制词 `\edash` 或控制符 `\%`（末尾孤立 `\` 也返回 `"\\"`）。"""
    toks: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "\\":
            i += 1
            continue
        j = i + 1
        if j < n and (text[j].isalpha() or text[j] == "@"):
            while j < n and (text[j].isalpha() or text[j] == "@"):
                j += 1
            toks.append(text[i:j])
            i = j
        elif j < n:
            toks.append(text[i:j + 1])
            i = j + 1
        else:
            toks.append("\\")
            i = j
    return toks


def _skip_group(text: str, start: int) -> int | None:
    """从 `{` 起找配对的 `}`，返回闭括号之后的下标；不配对返回 None。"""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _protected_spans(text: str) -> list[tuple[int, int]]:
    r"""TeXV 上「不能在内部折行」的区间：控制序列+紧跟参数组、平衡 `{}`、`--`/`——` 串。"""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            start = i
            i += 1
            if i < n and (text[i].isalpha() or text[i] == "@"):
                while i < n and (text[i].isalpha() or text[i] == "@"):
                    i += 1
                if i < n and text[i] == "*":
                    i += 1
                # 控制词后紧跟的 `{组}` / `[组]` 参数并进同一原子（`\textit{文字}`）
                while True:
                    j = i
                    while j < n and text[j] in " \t":
                        j += 1
                    if j < n and text[j] == "{":
                        end = _skip_group(text, j)
                        if end is None:
                            i = n
                        else:
                            i = end
                    elif j < n and text[j] == "[":
                        k = text.find("]", j)
                        i = n if k < 0 else k + 1
                    else:
                        break
            elif i < n:
                i += 1
            spans.append((start, i))
        elif ch == "{":
            end = _skip_group(text, i)
            spans.append((i, n if end is None else end))
            i = spans[-1][1]
        elif ch in "—-" and i + 1 < n and text[i + 1] == ch:
            start = i
            while i < n and text[i] == ch:
                i += 1
            spans.append((start, i))
        else:
            i += 1
    return spans


def _brace_balance(text: str) -> int:
    """未转义花括号的净深度（跳过 `\\` 与其后一个字符）。"""
    depth = 0
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return depth


def _wrap_violations(title: str, limit: int, is_english: bool) -> list[str]:
    """对一次 `wrap_title` 的结果做全部结构断言，返回违约描述列表（空 = 通过）。"""
    wrapped = TOC.wrap_title(title, limit, is_english)
    if wrapped is None:
        return []                      # 不断行 = 合法（宁可不折也不劈宏）
    line0, line1 = wrapped
    bad: list[str] = []

    # (1) 断了就必须是原文前缀 + 后缀（拼接只在交接处允许丢空白）
    if not title.startswith(line0):
        bad.append(f"第一行不是原文前缀：{line0!r}")
        return bad
    if title[len(line0):].strip() != line1:
        bad.append(f"第二行与原文后缀不一致（可能吞字/重字）：{line1!r}")
        return bad

    # (2) 断点不得落在任何 TeX 原子内部（控制序列/参数组/连接号）
    idx = len(line0)
    for start, end in _protected_spans(title):
        if start < idx < end:
            bad.append(f"断点 {idx} 落在 TeX 原子 {title[start:end]!r} 内部")
            break

    # (3) 任何一行都不得以孤立反斜杠结尾（`\` + newline 的劈宏现场）
    if line0.endswith("\\"):
        bad.append(f"第一行以孤立反斜杠结尾：{line0!r}")
    if line1.endswith("\\") and not title.rstrip().endswith("\\"):
        bad.append(f"第二行以孤立反斜杠结尾：{line1!r}")

    # (4) 原文字面花括号平衡时，每行也必须平衡（`{\large …}` 拼装才不会失衡）
    if _brace_balance(title) == 0:
        for label, line in (("第一行", line0), ("第二行", line1)):
            if _brace_balance(line) != 0:
                bad.append(f"{label}花括号不平衡（净深度 {_brace_balance(line)}）：{line!r}")

    # (5) 两行拼回后控制序列逐个与原文一致（劈开的宏会让 token 对不上）
    if _control_tokens(line0) + _control_tokens(line1) != _control_tokens(title):
        bad.append(f"控制序列切分不一致：原文 {_control_tokens(title)!r}"
                   f" ≠ {_control_tokens(line0)!r}+{_control_tokens(line1)!r}")

    # (6) 逐个点名本轮涉及的宏/参数组/连接号必须整串落在某一行里
    for macro in ("edash", "cndash"):
        token = "\\" + macro
        expected = _control_tokens(title).count(token)
        got = _control_tokens(line0).count(token) + _control_tokens(line1).count(token)
        if expected != got:
            bad.append(f"`{token}` 被劈开（原文 {expected} 处、两行合起来只找到 {got} 处）")
    for macro in ("textit", "textbf", "emph", "hbox", "text"):
        needle = "\\" + macro + "{"
        expected = title.count(needle)
        got = line0.count(needle) + line1.count(needle)
        if expected != got:
            bad.append(f"`{needle}…}}` 参数组被劈开（原文 {expected} 处、两行合起来 {got} 处）")
    for seq in ("--", "——"):
        expected = title.count(seq)
        got = line0.count(seq) + line1.count(seq)
        if expected != got:
            bad.append(f"连接号 {seq!r} 被劈开（原文 {expected} 处、两行合起来 {got} 处）")
    return bad


# ── 造期目录 + 《信息检查》报告（CLI 端到端用）─────────────────────────
def make_issue(root: str, chinese_title: str,
               english_title: str = "A Study of Wrapping Safety",
               article: str = "Li_Si") -> tuple[str, str]:
    """建一期合法期目录（名字带总期号）+ 配套报告 + 稿件子目录，返回 (期目录, 报告)。"""
    issue = os.path.join(root, "2026年第5期(90)")
    os.makedirs(issue, exist_ok=True)
    report = os.path.join(issue, "信息检查2026_01_01_00_00_00.txt")
    content = (
        f"{article}\n"
        "卷号：19  期号：5  年份：2026\n"
        "起始页码：1  终止页码：10  页数：10\n"
        "字号 10.5t\n"
        "第一作者中文: 李四\n"
        f"中文标题: {chinese_title}\n"
        f"英文标题: {english_title}\n"
        "第一作者英文: Li Si\n"
        "全文行距: 13\n")
    with open(report, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    art_dir = os.path.join(issue, "10 " + article)
    os.makedirs(art_dir, exist_ok=True)
    with open(os.path.join(art_dir, article + ".tex"), "w", encoding="utf-8") as fh:
        fh.write("% placeholder article tex fixture（报告校验只要求存在）\n")
    return issue, report


def run_cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ENGINE), str(ENGINE / "vendor"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return subprocess.run([PY, str(ENGINE / "toc.py"), *args],
                          cwd=cwd or str(ENGINE), capture_output=True, text=True,
                          env=env, timeout=600)


def manifest(root: str) -> tuple:
    """期目录完整清单：相对路径 + 权限位 + 类型 + 大小 + 文件 SHA256（排序后可比对）。"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            items.append((os.path.relpath(path, root), stat.S_IMODE(st.st_mode), "dir", st.st_size, ""))
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode):
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            else:
                digest = ""
            items.append((os.path.relpath(path, root), stat.S_IMODE(st.st_mode),
                          "file", st.st_size, digest))
    return tuple(sorted(items))


def read_tex(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


TMPDIRS: list[str] = []


def new_tmp(prefix: str) -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    TMPDIRS.append(path)
    return path


# ══════════════════════════════════════════════════════════════════════
# R01：折行只在 TeX 原子边界，不劈宏
# ══════════════════════════════════════════════════════════════════════
print("── R01 折行不劈宏：标题矩阵 × 多档断行上限 ──")

TITLE_MATRIX = [
    ("edash-i1", r"中国逻辑研究P13\edash V模型与2026年新进展的若干基础问题", False),
    ("edash-d01a", D01A_TITLE, False),
    ("edash-i1b", r"集合论模型论交叉领域基础问题研讨进展综述当代逻辑前沿123 \edash "
                  r"11模型构造与证明论方法研究若干问题", False),
    ("cndash", r"从模态逻辑到动态认知逻辑\cndash 当代逻辑前沿的若干基础问题研究", False),
    ("textit", r"\textit{斜体文字}与\textbf{粗体文字}以及\emph{强调文字}的断行安全边界测试", False),
    ("dash--", "Model Construction -- Proof Theory and -- Model Theory Cross Domain Studies", True),
    ("emdash——", "中国逻辑研究的新进展——从模型论到证明论的若干基础问题研究", False),
    ("escape", r"百分号\% 与 \& 与 \_ 与 \# 以及 \$ 的转义测试标题若干基础问题研究", False),
    ("group{}", r"\textbf{粗体文字}与\emph{强调文字}以及{平衡参数组}的断行安全测试研究", False),
    ("mixed", r"Mixed 中英混排 Title with \edash 11 Model Construction and Proof Theory Methods", False),
    ("mixed-en", r"Mixed 中英混排 Title with \edash 11 Model Construction and Proof Theory Methods", True),
    ("long-cjk", "中国逻辑研究若干基础问题" * 3, False),
    ("long-en", r"A Very Long English Title about \edash Model Construction and Proof Theory "
                r"Methods in Contemporary Logic", True),
    ("short", "短标题", False),
    ("no-macro", "结构性规范与动态偏好的公理化研究及其元理论性质", False),
    # 控制词紧跟 CJK（无空格）：XeTeX 下 CJK 也是 letter，`\edash以` 会把「以」吞进控制词名；
    # parse_entry 先转义成 `\edash{}以`，因此 wrap_title 实际输入是下面这条已转义形态；
    # 未转义原串的端到端行为由 R01 末尾「控制词紧跟 CJK」用例覆盖（断言生成 tex 为 `\edash{}`）。
    ("edash-cjk", r"中国逻辑研究\edash{}以模型论与证明论为中心的基础问题研究", False),
]

_r01_total = 0
_r01_bad = 0
for _label, _title, _is_en in TITLE_MATRIX:
    _limits = (30, 20, 12, 8) if _is_en else (20, 12, 8, 6)
    for _limit in _limits:
        _r01_total += 1
        _bad = _wrap_violations(_title, _limit, _is_en)
        if _bad:
            _r01_bad += 1
        _w = TOC.wrap_title(_title, _limit, _is_en)
        check(f"R01 折行安全 [{_label}] limit={_limit}",
              not _bad,
              "; ".join(_bad) if _bad else f"→ {_w}")

print(f"R01 矩阵：{_r01_total} 组断行用例，{_r01_bad} 组违约")

# R01 代表用例端到端：生成 + 落位 + xelatex 编译（TeX 不可用则 SKIP）
if XELATEX:
    _root = new_tmp("slcn_b375r_r01_")
    _issue, _report = make_issue(_root, D01A_TITLE)
    _p = run_cli("--report", _report, "--place", "--compile")
    check("R01 代表用例 --place --compile rc0（折行后的 tex 可编译）",
          _p.returncode == 0,
          f"rc={_p.returncode}; stderr={_p.stderr[-600:]!r}")
    _tex_path = os.path.join(_issue, "000 Table of Contents", "Chinese Content.tex")
    if os.path.isfile(_tex_path):
        _tex = read_tex(_tex_path)
        _lines = [ln for ln in _tex.splitlines()]
        _bad_lines = [ln for ln in _lines
                      if ln.rstrip().endswith("\\") and not ln.rstrip().endswith("\\\\")]
        _tokens = []
        for _ln in _lines:
            _tokens.extend(_control_tokens(_ln))
        check("R01 落位 tex 无「行尾孤立反斜杠」的劈宏现场",
              not _bad_lines, f"{_bad_lines[:3]}")
        check("R01 落位 tex 里 `\\edash` 整串存在（未被拆到两行）",
              _tokens.count("\\edash") >= 1 and "\\edash" in _tex,
              f"edash tokens={_tokens.count('\\edash')}")
        check("R01 落位 tex 花括号整体平衡（`{\\large …}` 未失衡）",
              _brace_balance(_tex) == 0, f"net={_brace_balance(_tex)}")
        _pdf = os.path.join(_issue, "000 Table of Contents", "Chinese Content.pdf")
        check("R01 落位后确实产出目录 PDF（非 0 字节）",
              os.path.isfile(_pdf) and os.path.getsize(_pdf) > 0,
              f"exists={os.path.isfile(_pdf)}")
    else:
        check("R01 落位 tex 存在", False, _tex_path)
    # R01 补充端到端：标题里控制词紧跟 CJK（无空格），验证转义插入 `{}` 且可编译
    _root2 = new_tmp("slcn_b375r_r01c_")
    _issue2, _report2 = make_issue(
        _root2, r"中国逻辑研究\edash以模型论与证明论为中心的基础问题研究")
    _p2 = run_cli("--report", _report2, "--place", "--compile")
    check("R01 控制词紧跟 CJK（`\\edash以`）⇒ 转义插入 `{}`、不吞成控制词名",
          _p2.returncode == 0,
          f"rc={_p2.returncode}; stderr={_p2.stderr[-500:]!r}")
    _tex2 = os.path.join(_issue2, "000 Table of Contents", "Chinese Content.tex")
    if os.path.isfile(_tex2):
        _body2 = read_tex(_tex2)
        check("R01 落位 tex 出现 `\\edash{}`（转义成功）",
              r"\edash{}" in _body2,
              "" if r"\edash{}" in _body2 else "未见 `\\edash{}`")
        check("R01 落位 tex 不出现被吞形态 `\\edash以`",
              r"\edash以" not in _body2,
              "" if r"\edash以" not in _body2 else "出现 `\\edash以`（会被 XeTeX 吞进控制词名）")
        check("R01 控制词紧跟 CJK 用例花括号整体平衡",
              _brace_balance(_body2) == 0, f"net={_brace_balance(_body2)}")
        _pdf2 = os.path.join(_issue2, "000 Table of Contents", "Chinese Content.pdf")
        check("R01 控制词紧跟 CJK 用例产出目录 PDF（非 0 字节）",
              os.path.isfile(_pdf2) and os.path.getsize(_pdf2) > 0)
    else:
        check("R01 控制词紧跟 CJK 用例落位 tex 存在", False, _tex2)
else:
    skip("R01 代表用例编译", "未找到 xelatex")


# ══════════════════════════════════════════════════════════════════════
# R02：真实旧 000 模板（无 `\edash`）兜底注入
# ══════════════════════════════════════════════════════════════════════
print("\n── R02 真实旧模板注入兜底（`\\providecommand{\\edash}`/`\\cndash`）──")

check("R02 内嵌真实旧模板素材完整（head 有 \\cndash、无 \\edash、含 \\begin{document}）",
      "\\newcommand{\\cndash}" in LEGACY_HEAD and "\\edash" not in LEGACY_HEAD
      and "\\begin{document}" in LEGACY_HEAD,
      f"src_sha={LEGACY_SRC_SHA256}")
check("R02 真实旧模板副本与稿件仓真原件 SHA256 一致（防夹具漂移）",
      hashlib.sha256(LEGACY_TEMPLATE.encode("utf-8")).hexdigest() == LEGACY_SRC_SHA256,
      hashlib.sha256(LEGACY_TEMPLATE.encode("utf-8")).hexdigest())

_ensure = getattr(TT, "ensure_toc_macros", None)
if _ensure is None:
    check("R02 slcn.toctemplate.ensure_toc_macros 存在（公开 API）", False, "缺函数")
else:
    # 真实旧模板：缺 `\edash`、已定义 `\cndash` → 只补缺的那一个，不覆盖/不重复
    _out = _ensure(LEGACY_HEAD, {"edash", "cndash"})
    check("R02 (a) 真实旧模板缺 `\\edash` ⇒ 注入 `\\providecommand{\\edash}`",
          "\\providecommand{\\edash}" in _out)
    check("R02 (b) 真实旧模板已有 `\\newcommand{\\cndash}` ⇒ 不重复注入、原定义保留",
          "\\providecommand{\\cndash}" not in _out
          and "\\newcommand{\\cndash}" in _out)
    _last_usepackage = _out.rindex("\\usepackage")
    check("R02 (c) 注入位置在**所有** `\\usepackage` 之后",
          _out.index("\\providecommand{\\edash}") > _last_usepackage)
    check("R02 (d) 注入位置在 `\\begin{document}` 之前（定义先于使用）",
          _out.index("\\providecommand{\\edash}") < _out.index("\\begin{document}"))
    check("R02 (e) 幂等：对已注入 head 再注入不再增加内容",
          _ensure(_out, {"edash", "cndash"}) == _out)
    # 两个宏都缺的「裸」head（构造用例，验证按 names 补齐两种宏）
    _bare_head = ("\\documentclass[b5paper,11pt]{ctexart}\n"
                  "\\usepackage{xeCJK}\n\\begin{document}\n")
    _bare_out = _ensure(_bare_head, {"edash", "cndash"})
    check("R02 (f) head 两宏都缺 ⇒ `\\edash` 与 `\\cndash` 都补，且都在 `\\begin{document}` 前、`\\usepackage` 后",
          "\\providecommand{\\edash}" in _bare_out
          and "\\providecommand{\\cndash}" in _bare_out
          and _bare_out.index("\\providecommand{\\edash}") > _bare_out.rindex("\\usepackage")
          and _bare_out.index("\\providecommand{\\cndash}") < _bare_out.index("\\begin{document}"))
    _edash_defined = LEGACY_HEAD.replace(
        "\\begin{document}",
        "\\newcommand{\\edash}{\\raise0.1em\\hbox{-}}\n\\begin{document}")
    _edash_out = _ensure(_edash_defined, {"edash"})
    check("R02 (g) 模板已定义 `\\edash` ⇒ 不重复注入、不覆盖原定义",
          "\\providecommand{\\edash}" not in _edash_out
          and "\\newcommand{\\edash}" in _edash_out)
    _def_head = LEGACY_HEAD.replace("\\begin{document}",
                                    "\\def\\edash{\\raise0.1em\\hbox{-}}\n\\begin{document}")
    check("R02 (h) 模板用 `\\def\\edash` 定义也算已定义（不重复注入）",
          "\\providecommand{\\edash}" not in _ensure(_def_head, {"edash"}))
    check("R02 (i) names 只含已定义的 `cndash` ⇒ 原样返回（不引入无用宏）",
          _ensure(LEGACY_HEAD, {"cndash"}) == LEGACY_HEAD)
    check("R02 (j) names 为空集 ⇒ 原样返回",
          _ensure(LEGACY_HEAD, set()) == LEGACY_HEAD)
    _truncated = LEGACY_HEAD[:LEGACY_HEAD.index("\\begin{document}")]
    check("R02 (k) 模板被截断（无 `\\begin{document}`）⇒ 原样返回，交给上层校验",
          _ensure(_truncated) == _truncated)

    # 端到端①：真实旧模板文件落在 000 目录 + 含 `\edash` 的标题 → 生成产物自动带兜底宏
    _root = new_tmp("slcn_b375r_r02_")
    _issue, _report = make_issue(_root, D01A_TITLE)
    _toc_dir = os.path.join(_issue, "000 Table of Contents")
    os.makedirs(_toc_dir, exist_ok=True)
    _tpl_path = os.path.join(_toc_dir, "Chinese Content.tex")
    with open(_tpl_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(LEGACY_TEMPLATE)
    _outs = TOC.run(_report, TOC.TocSettings(), log=lambda _s: None, stamp="R02")
    _ch_path = [p for p in _outs if "Chinese Content" in os.path.basename(p)]
    check("R02 (l) 真实旧模板 + `\\edash` 标题 ⇒ 生成成功（返回产物路径）",
          len(_outs) == 2 and len(_ch_path) == 1, f"outs={[os.path.basename(p) for p in _outs]}")
    if _ch_path:
        _gen = read_tex(_ch_path[0])
        check("R02 (m) 生成的中文目录 tex 注入 `\\providecommand{\\edash}`，且在 `\\begin{document}` 前",
              "\\providecommand{\\edash}" in _gen
              and _gen.index("\\providecommand{\\edash}") < _gen.index("\\begin{document}")
              and _gen.index("\\providecommand{\\edash}") > _gen.rindex("\\usepackage"),
              "位置检查通过" if "\\providecommand{\\edash}" in _gen else "未见注入")
        check("R02 (n) 注入后的条目里 `\\edash` 完整（不劈宏）",
              "\\edash" in _gen and not any(
                  ln.rstrip().endswith("\\") and not ln.rstrip().endswith("\\\\")
                  for ln in _gen.splitlines()))
    # 端到端②：标题不含 `\edash`/`\cndash` 时**不**注入（不污染既有成品）
    _root2 = new_tmp("slcn_b375r_r02b_")
    _issue2, _report2 = make_issue(_root2, "结构性规范与动态偏好的公理化研究及其元理论性质")
    _toc_dir2 = os.path.join(_issue2, "000 Table of Contents")
    os.makedirs(_toc_dir2, exist_ok=True)
    with open(os.path.join(_toc_dir2, "Chinese Content.tex"), "w",
              encoding="utf-8", newline="") as fh:
        fh.write(LEGACY_TEMPLATE)
    _outs2 = TOC.run(_report2, TOC.TocSettings(), log=lambda _s: None, stamp="R02b")
    _ch2 = [p for p in _outs2 if "Chinese Content" in os.path.basename(p)]
    if _ch2:
        _gen2 = read_tex(_ch2[0])
        check("R02 (o) 标题不含宏 ⇒ 不注入任何 providecommand（按内容触发、不污染成品）",
              "providecommand" not in _gen2, "出现了无用注入" if "providecommand" in _gen2 else "")
    else:
        check("R02 (o) 标题不含宏 ⇒ 不注入任何 providecommand", False, "中文产物缺失")

    # 端到端③：真实旧模板 + `\edash` 标题走 CLI --place --compile 真编译
    if XELATEX:
        _root3 = new_tmp("slcn_b375r_r02c_")
        _issue3, _report3 = make_issue(_root3, D01A_TITLE)
        _toc3 = os.path.join(_issue3, "000 Table of Contents")
        os.makedirs(_toc3, exist_ok=True)
        with open(os.path.join(_toc3, "Chinese Content.tex"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(LEGACY_TEMPLATE)
        _p = run_cli("--report", _report3, "--place", "--compile")
        _pdf = os.path.join(_toc3, "Chinese Content.pdf")
        check("R02 (p) 真实旧模板端到端 --place --compile rc0（兜底宏让 xelatex 编过）",
              _p.returncode == 0 and os.path.isfile(_pdf) and os.path.getsize(_pdf) > 0,
              f"rc={_p.returncode}; stderr={_p.stderr[-600:]!r}")
        _placed = os.path.join(_toc3, "Chinese Content.tex")
        if os.path.isfile(_placed) and _p.returncode == 0:
            _pt = read_tex(_placed)
            check("R02 (q) 落位 000 的 tex 带 `\\providecommand{\\edash}` 且在 `\\begin{document}` 前",
                  "\\providecommand{\\edash}" in _pt
                  and _pt.index("\\providecommand{\\edash}") < _pt.index("\\begin{document}"))
    else:
        skip("R02 (p) 真实旧模板端到端编译", "未找到 xelatex")


# ══════════════════════════════════════════════════════════════════════
# R03：期根不可写（0555）→ rc2 + 中文原因 + 零写 + 无 traceback
# ══════════════════════════════════════════════════════════════════════
print("\n── R03 期根 0555：CLI rc2 + 中文原因 + 零写 + 无 traceback ──")

_root = new_tmp("slcn_b375r_r03_")
_issue, _report = make_issue(_root, "普通标题若干问题研究")
_result_json = os.path.join(_root, "result.json")
_before = manifest(_issue)
os.chmod(_issue, 0o555)
try:
    _p = run_cli("--report", _report, "--place", "--result-json", _result_json)
finally:
    os.chmod(_issue, 0o755)
_after = manifest(_issue)

check("R03 (a) 不可写期根 ⇒ CLI rc2（不再是裸 traceback 的 rc1）",
      _p.returncode == 2, f"rc={_p.returncode}")
check("R03 (b) stderr 含中文「目录生成失败」",
      "目录生成失败" in _p.stderr, repr(_p.stderr[-400:]))
check("R03 (c) stderr 含中文「权限不足」（可操作原因）",
      "权限不足" in _p.stderr, repr(_p.stderr[-400:]))
check("R03 (d) 不甩 traceback（stdout/stderr 均无 Traceback）",
      "Traceback" not in _p.stderr and "Traceback" not in _p.stdout,
      repr(_p.stderr[-600:]))
check("R03 (e) 零写：期目录前后文件清单+权限+SHA 完全一致",
      _before == _after,
      f"before={len(_before)} after={len(_after)}")
check("R03 (f) 未创建 `.toolbox-tmp` 工作目录",
      not os.path.exists(os.path.join(_issue, ".toolbox-tmp")))
check("R03 (g) 失败时不写 result JSON",
      not os.path.exists(_result_json))

# 直接 API 层：generate_tex/run 把 OSError 翻成 TocWriteError（CLI 中文原因的来源）
_write_error_cls = getattr(TOC, "TocWriteError", None)
check("R03 (h) 公开类 `slcn.toc.TocWriteError` 存在", _write_error_cls is not None)
if _write_error_cls is not None:
    os.chmod(_issue, 0o555)
    _caught: BaseException | None = None
    try:
        try:
            TOC.run(_report, TOC.TocSettings(), log=lambda _s: None, stamp="R03")
        except _write_error_cls as exc:            # noqa: PERF203
            _caught = exc
    finally:
        os.chmod(_issue, 0o755)
    check("R03 (i) 不可写时 API 抛 TocWriteError（而非裸 PermissionError）",
          isinstance(_caught, _write_error_cls), repr(_caught))
    check("R03 (j) TocWriteError 文案含中文「权限不足」与受阻路径",
          _caught is not None and "权限不足" in str(_caught)
          and ".toolbox-tmp" in str(_caught), str(_caught))


# ══════════════════════════════════════════════════════════════════════
# R04：原有断点语义不回归（期 90 成品同款断点）
# ══════════════════════════════════════════════════════════════════════
print("\n── R04 原断点语义不回归（期 90「知识论回应」金标准）──")

_wc = TOC.wrap_title("示例倾向性：对紧缩真理论概括难题的知识论回应", 20, False)
check("R04 (a) 中文长标题在虚词后断、不切词（与期 90 成品一致）",
      _wc == ("示例倾向性：对紧缩真理论概括难题的", "知识论回应"), f"{_wc}")
_w12 = TOC.wrap_title("认知悖论的形式可证性与认知实现的若干问题研究", 12, False)
check("R04 (b) 12 字上限下仍断成两行且两行都不超上限",
      _w12 is not None and TOC.display_width(_w12[0]) <= 12
      and TOC.display_width(_w12[1]) <= 12, f"{_w12}")
_en_title = "The Knowability Paradox and Its Formal Treatment in Modal Logic"
_we = TOC.wrap_title(_en_title, 20, True)
check("R04 (c) 英文仍在空格处断（单词序列与原文一致、不劈词）",
      _we is not None and not _we[0].endswith(" ") and not _we[1].startswith(" ")
      and _we[0].split() + _we[1].split() == _en_title.split(), f"{_we}")


# ══════════════════════════════════════════════════════════════════════
# 覆盖矩阵 + 收尾
# ══════════════════════════════════════════════════════════════════════
print("\n── R01 覆盖矩阵（标题 → 断言点）──")
for _label, _title, _is_en in TITLE_MATRIX:
    print(f"  {_label:<10} is_english={_is_en!s:<5} {_title[:52]}")
print("  断言点：①原文前缀/后缀一致 ②断点不在控制序列·参数组·连接号内部 "
      "③无行尾孤立反斜杠 ④每行花括号平衡 ⑤两行控制序列数=原文 "
      "⑥`\\edash`/`\\cndash`/`\\textit{…}`/`--`/`——` 整串落在某一行")

for _d in TMPDIRS:
    shutil.rmtree(_d, ignore_errors=True)

print()
if skips:
    print(f"SKIP {len(skips)} 项：{skips}")
if fails:
    print(f"FAIL {len(fails)} 项：")
    for _name in fails:
        print(f"  - {_name}")
    print(f"\n通过 {pass_n} 项，失败 {len(fails)} 项，跳过 {len(skips)} 项")
    sys.exit(1)
print(f"全部通过（{pass_n} 项检查，{len(skips)} 项跳过）")
