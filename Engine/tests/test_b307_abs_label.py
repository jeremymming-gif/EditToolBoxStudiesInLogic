#!/usr/bin/env python3.14
"""B-307 S6：`_ABS_RE` / `_label_body` 的三处收紧（摘要标签判据）。

症状（B-303 B3，见 `evidence/proofrules-eval.md`）：
  · 校对说明行 `摘要部分，共2处` 被当成摘要标签（2 篇**不在本轮写域**的稿子因此
    `abstract_cn` 取到校对语）；
  · `Abstract. ` 的句点没被吃掉：`_ABS_RE.sub` 之后 `abstract_en` 读成 `'. '`
    （2023年第6期(73) 06 Xiaoqi_Zhang，真摘要在下一段）。

本轮的判据（只收紧，不放宽任何正文判据）：
  ① 标签所在行**行尾附近**出现 `共N处` → 不当标签（只挡校对语；正文不受影响）；
  ② `Abstract.` 的句点吃掉；点后还有正文照旧取正文，**标签独占一段**仍是标签
     （正文由 `_label_body` 往下取一段——既有断言零改动）；
  ③ 标签后只允许**非标点**内容开头（`摘要：，…` 不是标签）。

既有测试 `test_abstract_label.py` 一条断言都不改；本文件只**追加**新形态的回归。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_GITHUB = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import parse_docx  # noqa: E402
from slcn.wordmeta import _ABS_RE, _abs_body_cn, parse_tail_metadata  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ══ ① 标签形态（纯字符串）═════════════════════════════════════════════════
print("── ① 标签形态 ──")

MUST = [
    "摘要：本文讨论逻辑。",
    "摘要理想的人工通用智能系统（与正文之间没有分隔符，全库 1 处）",
    "Abstract: We study databases.",
    "Abstract. The paper studies databases.",   # 句点被吃掉
    "Abstract.",                                # 句点后为空 → 仍是标签
    "Abstract",                                 # 标签独占一段（既有）
]
for raw in MUST:
    check(f"认成摘要标签：{raw[:36]}", bool(_ABS_RE.match(raw)))

MUST_NOT = [
    "摘要部分，共2处",           # ① 校对说明行（B-303 B3 的 2 篇）
    "摘要：，讨论逻辑。",        # ③ 标签后是标点
    "摘要：。",
    "摘要式阅读是本文讨论的主题。",
    "摘要性分析",
    "摘要化学方法",
    "本文的摘要：讨论数据库。",
    "We discuss the Abstract and Keywords sections.",
]
for raw in MUST_NOT:
    check(f"不认成摘要标签：{raw[:36]}", _ABS_RE.match(raw) is None)

# 句点确实进入匹配（否则 `abstract_en` 会拿到 `. `）
m = _ABS_RE.match("Abstract. The paper")
check("`Abstract.` 的句点被吃掉", bool(m) and m.group(0) == "Abstract. ", repr(m and m.group(0)))

# ══ ② 取正文（既有路径零改动 + 新形态）════════════════════════════════════
print("\n── ② 取正文 ──")

HIT = "本文讨论逻辑的若干问题，并给出一个简短的结语和若干例子。"  # >_ABS_MIN_CHARS、成句
check("标签独占一行 + 下行正文 → 取下一行", _abs_body_cn(["摘要：", HIT], 0) == HIT)
check("只写「摘要」+ 下行正文 → 取下一行", _abs_body_cn(["摘要", HIT], 0) == HIT)
check("空标签（无下行正文）→ 空", _abs_body_cn(["摘要"], 0) == "")
check("英文正文不写进中文摘要", _abs_body_cn(["摘要：", "Fallacies are mistakes."], 0) == "")
check("校对说明行不再当摘要", _abs_body_cn(["摘要部分，共2处"], 0) == "")
check("同段正文逐字保留（含尾随空白）", _abs_body_cn(["摘要：本文讨论逻辑。  "], 0) == "本文讨论逻辑。  ")
check("`Abstract: …` 同段取值", _abs_body_cn(["Abstract: We study databases."], 0) == "We study databases.")

# 文末元数据：`Abstract. ` 独占一段 → 取下一段真摘要（B-303 B3 的真实形态）
out = parse_tail_metadata(
    ["A Logic of Databases", "", "Abstract. ",
     "Sleep has become a prime example of medicalization discussed by sociologists."], 0)
check("`Abstract. ` 独占一段 → abstract_en 取下一段（不是 '. '）",
      out.get("abstract_en", "").startswith("Sleep has become"), repr(out.get("abstract_en")))

out = parse_tail_metadata(["A Logic of Databases", "Abstract. The paper studies databases."], 0)
check("`Abstract. The…` → abstract_en 从 The 开始",
      out.get("abstract_en") == "The paper studies databases.", repr(out.get("abstract_en")))

out = parse_tail_metadata(["A Logic of Databases", "Abstract", "We study databases."], 0)
check("`Abstract` 独占一段仍取下一段（既有行为）",
      out.get("abstract_en") == "We study databases.", repr(out.get("abstract_en")))

# ══ ③ 真实语料（不在本机则 SKIP）═══════════════════════════════════════════
print("\n── ③ 真实语料 06 Xiaoqi_Zhang ──")

REAL = os.path.join(
    _GITHUB, "Studies_in_Logic_Database", "2023年第6期(73)", "06 Xiaoqi_Zhang", "全文gaigai.docx",
)
if not os.path.exists(REAL):
    print("SKIP  2023年第6期(73)/06 Xiaoqi_Zhang（本机没有这份语料）")
else:
    d = parse_docx(REAL)
    check("真实稿：abstract_en 是真摘要（不再是 '. '）",
          d.abstract_en.startswith("Sleep has become a prime example of medicalization"),
          repr(d.abstract_en[:60]))
    check("真实稿：英文子节标题仍照旧（`1 Introduction` + `3.1 …`）",
          d.headings[:2] == [(1, "Introduction"), (2, "The Current Situation of Sleep Problems")],
          repr(d.headings[:2]))

print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部 PASS")
sys.exit(0)
