#!/usr/bin/env python3.14
"""B-307 S4：顿号编号 `1、引言` 与无空格 `1.1引言` 的标题判据（+ 端到端）。

症状（B-303 B1，见 `evidence/proofrules-eval.md`）：
  · `1、引言`（编号后是顿号、没有空格）：旧 `_HEAD_CN_ARABIC` 只认「空格」或「紧跟
    汉字」，顿号两个都不满足；`_HEAD_CN_DOT` 只认点号。于是 04 Yingying_Wang 的 6 个
    真标题 `1、引言`…`6、结论` 全部漏认，产物 `n_headings = 0`，标题原样留在正文。
  · `1.1引言`（点号编号后**没有空格**）：旧 `_HEAD_EN3` 要求 `\\s+`，同样漏认。

本轮只放宽这两处形态，判据仍复用既有函数：
  · `N、` → `_is_cn_numbered_heading`（长度 2–40、无句读、不以数字/坏尾字收尾）；
  · `N.M` → `classify_en_heading` 判为 2（子节），与 `1.2 标题` 一致。

反例必须一动不动：`1、2、3` 这类编号枚举（顿号后紧跟数字）**不认**；英文 `1.2Title`
仍不认；`_HEAD_CN_DOT` 的 `N.M` 边界仍拒绝。

D-09（上一轮 BLOCKED 的修正）：顿号形式还要过**结构**判据——相邻两段都是 `N、`、
都过 `_is_cn_numbered_heading`、编号正好 +1（`1、2、3` 连排）时它们是列表项/定义条款，
不是章节标题（`_cn_arabic_run_ids`）。旧 `N 空格` 分支不经过这条判据，行为零改动。
"""
import os
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_GITHUB = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import _HEAD_CN_DOT, classify_en_heading, parse_docx  # noqa: E402
from slcn.wordmeta import (  # noqa: E402
    _HEAD_CN_ARABIC, _HEAD_CN_ART_RE, _HEAD_EN3, _cn_arabic_run_ids,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ══ ① 形态判据（纯字符串）═════════════════════════════════════════════════
print("── ① 形态判据 ──")

MUST = [
    ("1、引言", "引言"),
    ("1、 引言", "引言"),
    ("3、“要不是”NC条件句的句法分析", "“要不是”NC条件句的句法分析"),
    ("2、因果模型与“if not for”NC条件句的解释", "因果模型与“if not for”NC条件句的解释"),
    ("6、结论", "结论"),
]
for raw, want in MUST:
    m = _HEAD_CN_ARABIC.match(raw)
    check(f"顿号编号认成标题：{raw}", bool(m) and m.group("t") == want, repr(m and m.group("t")))

# 编号枚举/行内引用：顿号后紧跟数字 → 不是标题（`_is_cn_numbered_heading` 后面的
# 长度/句读判据够不着这里，必须在分隔符这一层就挡住）
for raw in ("1、2、3", "1、2、3 个问题", "1、2、3、4"):
    check(f"顿号枚举不算标题：{raw}", _HEAD_CN_ARABIC.match(raw) is None)

# 无空格点号编号：只在下一个字符是汉字时放宽（英文 `1.2Title` 不动）
check("`1.1引言` → 子节（层级 2）", classify_en_heading("1.1引言") == (2, "引言"))
check("`1.2标题` → 子节（层级 2）", classify_en_heading("1.2标题") == (2, "标题"))
check(
    "`1.2Title` 仍不认（英文侧逐字不变）",
    classify_en_heading("1.2Title") == (0, "1.2Title"),
)
check(
    "`3.3.1 Problems…` 仍不认（既有边界）",
    classify_en_heading("3.3.1 Problems for extrapolationists")
    == (0, "3.3.1 Problems for extrapolationists"),
)
check("_HEAD_EN3 本身不认 `1.2Title`", _HEAD_EN3.match("1.2Title") is None)

# 既有形态零变化
check("`1 引言`（空格）仍是标题", (_HEAD_CN_ARABIC.match("1 引言") or "").group("t") == "引言")
check("`1.2 Title`（空格）仍是子节", classify_en_heading("1.2 Title") == (2, "Title"))
check("`1. Introduction` 仍是 \\section", classify_en_heading("1. Introduction") == (1, "Introduction"))
# `_HEAD_CN_DOT` 的边界断言（task-7）：`N.M` 归 `_HEAD_EN3`，不被点式中文判据认走
check("_HEAD_CN_DOT 仍不接受 `N.M`", not _HEAD_CN_DOT.match("1.1引言"))
check("_HEAD_CN_DOT 仍接受 `1.引言`", bool(_HEAD_CN_DOT.match("1.引言")))

# ── ①-b D-09：`N、` 连续编号段（列表项/定义条款）不是标题 ──────────────────
print("\n── ①-b D-09 连续编号段 ──")
check("_HEAD_CN_ART_RE 认 `N、`（含两位数）",
      bool(_HEAD_CN_ART_RE.match("1、引言")) and bool(_HEAD_CN_ART_RE.match("11、标题")))
check("_HEAD_CN_ART_RE 不认 `N `（空格）", _HEAD_CN_ART_RE.match("1 引言") is None)
check("_HEAD_CN_ART_RE 不认 `N.`（点号）", _HEAD_CN_ART_RE.match("1.引言") is None)


class _P:
    def __init__(self, s):
        self.plain = s


def run_ids(texts):
    ps = [_P(t) for t in texts]
    hit = _cn_arabic_run_ids(ps)
    return {i for i in hit if i in {id(p) for p in ps}}


check("连排 `1、2、3` → 三段全进集合（链式覆盖）",
      len(run_ids(["1、g满足当且仅当g满足",
                   "2、g满足当且仅当并非g满足",
                   "3、g满足当且仅当g满足或者g满足"])) == 3)
check("单条 `1、引言` + 成段正文 → 不进集合",
      run_ids(["1、引言", "正文第 1 段，用来把正文撑起来。"]) == set())
check("`1 引言`/`2 结论`（空格）→ 不进集合（旧分支零改动）",
      run_ids(["1 引言", "2 结论"]) == set())
check("编号跳跃 `1、`→`3、` → 不进集合", run_ids(["1、甲乙丙", "3、丁戊己"]) == set())
check("带句读的列表项不参与（`_is_cn_numbered_heading` 原判据不动）",
      run_ids(["1、甲乙，丙丁", "2、戊己庚"]) == set())

# ══ ② 最小 docx 端到端 ════════════════════════════════════════════════════
print("\n── ② 最小 docx 端到端 ──")

HEAD = ["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑的若干问题。", "关键词：逻辑；数据库"]
FILLER = ["正文第 %d 段，用来把正文撑起来。" % i for i in range(24)]


def make_docx(paras):
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


TMP = []
try:
    # ②-a 04 Yingying_Wang 的三类形态（顿号 + 引号开头 + 长标题）
    p = make_docx(HEAD
                  + ["1、引言"] + FILLER[:4]
                  + ["2、因果模型与“if not for”NC条件句的解释"] + FILLER[4:9]
                  + ["3、“要不是”NC条件句的句法分析"] + FILLER[9:])
    TMP.append(p)
    d = parse_docx(p)
    got = [h for _lv, h in d.headings]
    check("端到端：3 个顿号标题全部认成 \\section（层级 1）",
          got == ["引言", "因果模型与“if not for”NC条件句的解释", "“要不是”NC条件句的句法分析"]
          and all(lv == 1 for lv, _h in d.headings), repr(got))
    check("端到端：产物里是 \\section{引言}，编号行不再留在正文",
          "\\section{引言}" in d.body and "1、引言" not in d.body)

    # ②-b 编号枚举不误收
    p = make_docx(HEAD + FILLER[:6]
                  + ["1、2、3"] + FILLER[6:12]
                  + ["1、2、3 个问题"] + FILLER[12:])
    TMP.append(p)
    d = parse_docx(p)
    check("反例：顿号枚举 `1、2、3` / `1、2、3 个问题` → 0 个标题",
          d.headings == [] and "1、2、3" in d.body, repr([h for _l, h in d.headings]))

    # ②-c 无空格点号编号 → 子节
    p = make_docx(HEAD + FILLER[:4]
                  + ["一、引言"] + FILLER[4:6]
                  + ["1.1研究背景"] + FILLER[6:10]
                  + ["1.2Title"] + FILLER[10:])
    TMP.append(p)
    d = parse_docx(p)
    lv = dict((h, l) for l, h in d.headings)
    check("端到端：`1.1研究背景` 排成 \\subsection（层级 2）", lv.get("研究背景") == 2, repr(lv))
    check("端到端：`1.2Title` 仍留在正文（英文侧不动）",
          "1.2Title" in d.body and "\\subsection{Title}" not in d.body)

    # ②-d D-09：紧跟在「…定义如下：」后面的连排 `N、` 定义条款不是标题
    p = make_docx(HEAD + FILLER[:6]
                  + ["这里给出满足关系定义如下：",
                     "1、g满足vivj当且仅当gigj",
                     "2、g满足当且仅当并非g满足",
                     "3、g满足当且仅当g满足或者g满足"]
                  + FILLER[6:])
    TMP.append(p)
    d = parse_docx(p)
    check("反例：连排 `1、2、3` 定义条款 → 0 个标题，三条都留在正文",
          d.headings == [] and all(t in d.body for t in ("1、g满足vivj", "2、g满足", "3、g满足")),
          repr([h for _l, h in d.headings]))

    # ②-e 顿号单条（后面跟成段正文）仍然认成标题
    p = make_docx(HEAD + FILLER[:6] + ["1、引言"] + FILLER[6:])
    TMP.append(p)
    d = parse_docx(p)
    check("端到端：单条 `1、引言` + 正文仍是 \\section（D-09 不误伤）",
          [h for _l, h in d.headings] == ["引言"] and "\\section{引言}" in d.body,
          repr(d.headings))

    # ②-f 旧 `N 空格` 分支零改动：连排 `1 引言`/`2 结论` 仍各是一个标题
    p = make_docx(HEAD + FILLER[:6] + ["1 引言"] + FILLER[6:9] + ["2 结论"] + FILLER[9:])
    TMP.append(p)
    d = parse_docx(p)
    check("端到端：`1 引言`/`2 结论`（空格）连排仍是两个 \\section（旧行为不动）",
          [h for _l, h in d.headings] == ["引言", "结论"], repr(d.headings))

finally:
    for _p in TMP:
        if os.path.exists(_p):
            os.unlink(_p)

# ══ ③ 真实语料（不在本机则 SKIP）═══════════════════════════════════════════
print("\n── ③ 真实语料 04 Yingying_Wang ──")

REAL = os.path.join(
    _GITHUB, "Studies_in_Logic_Database", "2023年第4期(71)", "04 Yingying_Wang",
    "original", "20160050760_O_editing.docx",
)
if not os.path.exists(REAL):
    print("SKIP  2023年第4期(71)/04 Yingying_Wang（本机没有这份语料）")
else:
    d = parse_docx(REAL)
    got = [h for _lv, h in d.headings]
    check("真实稿：6 个 `N、` 标题全部恢复（B-303 B1 的验收样本）",
          got == ["引言", "因果模型与“if not for”NC条件句的解释", "“要不是”NC条件句的句法分析",
                  "“要不是”NC条件句的真值条件", "“要不是”NC条件句的使用条件", "结论"],
          repr(got))
    check("真实稿：编号行不再留在正文", "1、引言" not in d.body and "6、结论" not in d.body)

print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部 PASS")
sys.exit(0)
