#!/usr/bin/env python3.14
r"""Word 作者启发式的**人名可信度闸门**（第 141 条，2026-10-18）。

缺陷
----
`convert.author_from_source()` 的 Word 通道逻辑是「第 1–5 段里找一段看着像姓名的」，
旧判据只有：长度 ≤ 40、不以句号/冒号结尾。于是**把正文当作者**：

  · 期 87 子案例 2（03 Wenjing_Du）：源稿没写作者名，第 4 段正文
    `贝叶斯主义认为，确证并非仅仅是全有或全无的定性概念……` 被当成作者，
    输出名推导成 `Yesizhuyirenwei_Bei`；
  · 子案例 3（04 Fayang_Ye）：第 2 段 `摘  要：…`（「摘」「要」之间有空格）
    没命中摘要行判据，单字「摘」被当作者。

这与 `naming.py` 的原则冲突：「能确定才输出，不能确定就报错让用户显式指定，绝不靠猜」。
修法：`naming.looks_like_person_name()` 闸门——中文候选必须 2–6 字纯汉字（含间隔号
的少数民族姓名 4–14 字）且不含中文标点；不是人名就继续找下一段，全不是则返回空串，
由上层报错要求 `--name`（编辑显式给名字，这是正确路径）。

本套件钉住：
  ① `looks_like_person_name()` 的正例/反例表；
  ② `convert.author_from_source()`：正文/摘要不再被当作者（返回空串）；
  ③ 真作者行照旧读出来（`张三 中山大学…` → 张三）；
  ④ 拉丁姓名照旧（宽松口径不回退）。

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_author_plausible.py
"""
import os
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402
from slcn.naming import looks_like_person_name  # noqa: E402

FAILS = []
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TMP = tempfile.mkdtemp(prefix="slcn_author_")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def make_docx(path, paras):
    from xml.sax.saxutils import escape

    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paras
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


# ══════════════════════════════════════════════════════════════════════════
# ① 人名判据正例/反例
# ══════════════════════════════════════════════════════════════════════════
for good in ("陈波", "唐瑞雪", "冯光武", "任然", "梁润成", "陈伟", "陈  波",
             "阿依古丽·买买提", "Michael Rathjen", "Wen-fang Wang", "F. H. van Eemeren",
             "John M. Koller", "Yingjin Xu", "Niu Ju"):
    check(f"① 正例：{good!r} 是人名", looks_like_person_name(good))
for bad in ("", "摘", "贝叶斯主义认为", "数学基础是数学哲学研究中的重要问题",
            "摘  要：数学基础是数学哲学研究中的重要问题", "关键词：逻辑；论证",
            "一、引言", "张三，", "1", "12", "1  确证测度的构造",
            "Over the past decades", "School of Philosophy",
            "§1. Introduction: What LLMs cannot do"):
    check(f"① 反例：{bad!r} 不是人名", not looks_like_person_name(bad))

# ══════════════════════════════════════════════════════════════════════════
# ② 正文/摘要不再被当作者（子案例 2、3 的真实形态）
# ══════════════════════════════════════════════════════════════════════════
_p2 = make_docx(os.path.join(TMP, "case2like.docx"), [
    "概率确证测度的评估与选择",
    "摘要：贝叶斯方法主张运用概率论的术语来描述证据对假说的确证度。",
    "关键词：确证；确证度；概率确证测度",
    "贝叶斯主义认为，确证并非仅仅是全有或全无的定性概念，而是有程度之分。",
    "本文致力于分析并评估各种确证测度的合理性，旨在排除不合理的测度。",
])
_author2, _why2 = convert.author_from_source(_p2)
check("② 子案例 2 形态：正文句不再被当作者（返回空串，要求 --name）",
      _author2 == "" and _why2 == "", repr((_author2, _why2)))

_p3 = make_docx(os.path.join(TMP, "case3like.docx"), [
    "作为数学基础的一价基础及其自主性辩护",
    "摘  要：数学基础是数学哲学研究中的重要问题，而数学基础的评价标准问题又是数学基础研究的核心。",
    "关键词：数学基础；一价基础；自主性",
    "数学基础是数学哲学研究中的重要问题，本文讨论一价基础的自主性辩护。",
])
_author3, _why3 = convert.author_from_source(_p3)
check("② 子案例 3 形态：`摘  要：` 之后的单字/正文不再被当作者",
      _author3 == "" and _why3 == "", repr((_author3, _why3)))

# ══════════════════════════════════════════════════════════════════════════
# ③ 真作者行照旧读出（不能为了拒绝正文而把真作者也挡掉）
# ══════════════════════════════════════════════════════════════════════════
_p_ok = make_docx(os.path.join(TMP, "ok.docx"), [
    "格赖斯和布兰顿的语言交流思想比较研究",
    "冯光武 广东外语外贸大学 陈水英 湖南工程学院",
    "摘要：本文比较格赖斯与布兰顿的语言交流思想。",
    "关键词：交流；意义",
])
_author_ok, _ = convert.author_from_source(_p_ok)
check("③ 姓名 + 单位行仍读出第一作者（冯光武）", _author_ok == "冯光武", repr(_author_ok))

_p_dash = make_docx(os.path.join(TMP, "dash.docx"), [
    "陈那派与正理派论战的跨文化思考",
    "任然  张晓翔",
    "摘要：本文讨论陈那派与正理派的论战。",
])
_author_dash, _ = convert.author_from_source(_p_dash)
check("③ 多作者空格分隔仍取第一个（任然）", _author_dash == "任然", repr(_author_dash))

# ④ 拉丁姓名照旧（旧宽松口径不回退）
_p_en = make_docx(os.path.join(TMP, "en.docx"), [
    "A Study of Logic",
    "Michael Rathjen",
    "Abstract: This paper studies logic.",
])
_author_en, _ = convert.author_from_source(_p_en)
check("④ 英文作者行照旧（Michael Rathjen）", _author_en == "Michael Rathjen", repr(_author_en))

import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
