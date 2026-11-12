#!/usr/bin/env python3
r"""FIX-20261112 / Q5（EN-HEADING-01）+ Q6（CN-TAIL-01 及 §15 v2 双向）。

Q5：英文稿「N Title（无点）」被 `_HEAD_CN_ARABIC` 先接住、又被 `_is_cn_numbered_heading`
    筛掉 → 丢 `\section`。修复：wordbody 分派链加 Latin-only 无点分支
    （`^\d{1,2}\s+[A-Z]`，len<130、无句点/句末标点、无 CJK、下一段为长正文）。
Q6：文末元数据块原来只认英文 → 中文题名/作者/关键词全空。修复：wordmeta 新增
    `_tail_cn_block`（从含 CJK 的摘要行回溯），补齐 title_cn/author_cn/abstract_cn/
    keywords_cn 并接到 EN 模板既有槽位；反向（中文稿 + 文末英文块）已有
    `parse_tail_metadata` 支持，本测试一并钉住。全部只填空、不覆盖。

夹具全部用共享生成器 `tools/docx_fixture_gen.make_docx` 现场合成（hermetic、
不联网、不读稿件库）；断言走真实 `parse_docx` 与 `build.build_docx`（真模板）。
用法：cd Engine && PYTHONPATH=.:vendor:sources-flet/.venv/bin/python tests/<本文件>
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docx_fixture_gen import make_docx  # noqa: E402
from slcn import build  # noqa: E402
from slcn.wordbody import parse_docx  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"FAIL  {name}  {detail}")


TMP = tempfile.mkdtemp(prefix="fix20261112_q56_")


def _fixture(tag: str, paras: list[str]) -> str:
    return make_docx(os.path.join(TMP, f"{tag}.docx"), paras=paras)


def _build(docx: str, tag: str, lang: str):
    out = os.path.join(TMP, "out_" + tag)
    res = build.build_docx(docx_path=docx, out_dir=out, basename=tag,
                           lang=lang, verbose=False)
    return open(res.tex_path, encoding="utf-8").read()


def _macro(tex: str, name: str) -> str:
    m = re.search(r"\\newcommand\{\\" + re.escape(name) + r"\}[^\n]*\n\{([^\n]*)\}", tex)
    if not m:
        m = re.search(r"\\" + re.escape(name) + r"\{([^\n]*)\}", tex)
    return m.group(1) if m else ""


def _sections(tex: str) -> list[str]:
    return re.findall(r"\\section\{([^}]*)\}", tex)


# ═══════════════════════════════════════════════════════════════════════════
# Q5：英文「N Title（无点）」→ \section
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q5] 英文无点编号标题 → \\section")
_BODY = ("This body paragraph explains the section in detail and is long enough "
         "to look like a real body of prose text in the manuscript.")
_HEADS = [
    "1 Introduction",
    "2 The Dual-Level Coordination Structure of Human Cooperation",
    "3 Normative Coordination in Argumentation: Cultural Accumulation and Shared Resources",
    "4 Second-Personal Coordination in Argumentation: Individual Interaction and Dynamic Generation",
    "5 Understanding the Methodological Principles of Generalized Argumentation Theory through Dual-Level Coordination",
    "6 Conclusion",
]
paras = ["The Structure of Human Cooperation", "Wu Su",
         "Abstract: This paper argues that human cooperation rests on a dual-level "
         "structure of normative and second-personal coordination practices. " * 2]
for h in _HEADS:
    paras += [h, _BODY]
for i in range(12):
    paras += [f"{i + 1}.{i % 3 + 1} Subsection Title Number {i + 1}", _BODY]
paras += ["References",
          "Su, W. (2025). The structure of human cooperation. Journal of Philosophy, 1(1), 1-20."]
_q5_docx = _fixture("q5_en", paras)
_q5_tex = _build(_q5_docx, "Q5en", "en")
_secs = _sections(_q5_tex)
check("Q5-1 六个「N Title（无点）」全部识别为 \\section",
      len(_secs) == 6, f"{_secs}")
check("Q5-2 标题文本逐字保留（含冒号/长标题）",
      _secs == [h.split(" ", 1)[1] for h in _HEADS], f"{_secs}")
_n_sub = len(re.findall(r"\\subsection\{", _q5_tex))
check("Q5-3 12 个「N.M」仍是 \\subsection，未被无点分支误吞",
      _n_sub == 12, f"subsection={_n_sub}")

# 负例：编号列表 / `1.` / `References` / `2)` 不得变 \section
neg = ["The Structure of Human Cooperation", "Wu Su",
       "Abstract: This paper argues about cooperation norms and practices in detail. " * 2]
neg += ["1 This is a claim about human cooperation and its norms", _BODY,
        "2) A numbered item that looks like a heading but is not", _BODY,
        "1.", _BODY,
        "References",
        "Su, W. (2025). The structure of human cooperation. Journal of Philosophy, 1(1), 1-20."]
_neg_tex = _build(_fixture("q5_neg", neg), "Q5neg", "en")
check("Q5-4 负例：编号列表/`1.`/`2)`/References 均不产生 \\section",
      _sections(_neg_tex) == [], f"{_sections(_neg_tex)}")

# ═══════════════════════════════════════════════════════════════════════════
# Q6 正方向：英文稿 + 文末中文块
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q6-正向] 英文正文 + 文末中文块 → title_cn/author_cn/abstract_cn/keywords_cn")

_EN_TITLE = "The Structure of Human Cooperation"
_EN_AUTH = "Wu Su"
_CN_TITLE = "生活世界论证的双层协调结构：一种文化演化视角"
_CN_AUTH = "伍素"
_CN_ABS = ("摘  要：" + "本文论证生活世界的双层协调结构，并给出文化演化视角下的解释路径。" * 2)
_CN_KW = "关键词：文化演化、双层协调、论证理论"


def _en_body() -> list[str]:
    rows = [_EN_TITLE, _EN_AUTH,
            "Abstract: This paper argues that human cooperation rests on a dual-level "
            "structure of normative and second-personal coordination. " * 2]
    rows += [_BODY] * 6
    return rows


# ① 齐全：中文题名 + 作者 + 摘要 + 关键词
full = _en_body() + ["References",
                     "Su, W. (2025). The structure of human cooperation. "
                     "Journal of Philosophy, 1(1), 1-20.",
                     _CN_TITLE, _CN_AUTH, _CN_ABS, _CN_KW]
_d_full = parse_docx(_fixture("q6_fwd_full", full))
check("Q6-1a title_cn 从文末中文块补出", _d_full.title_cn == _CN_TITLE, repr(_d_full.title_cn))
check("Q6-1b author_cn 从文末中文块补出", _d_full.author_cn == _CN_AUTH, repr(_d_full.author_cn))
check("Q6-1c abstract_cn 补出（含中文）", "生活世界" in _d_full.abstract_cn, repr(_d_full.abstract_cn[:40]))
check("Q6-1d keywords_cn 补出并归一化顿号为分号",
      _d_full.keywords_cn == "文化演化；双层协调；论证理论", repr(_d_full.keywords_cn))
check("Q6-1e 英文原字段不被覆盖（title/author 仍是英文）",
      _d_full.title == _EN_TITLE and _d_full.author == _EN_AUTH
      and _d_full.author_cn != _d_full.author, f"{_d_full.title!r}/{_d_full.author!r}")

_tex_full = _build(_fixture("q6_fwd_full2", full), "Q6fwd", "en")
check("Q6-2a EN 模板 \\myfirstauthorCN 已接线", _macro(_tex_full, "myfirstauthorCN") == _CN_AUTH,
      repr(_macro(_tex_full, "myfirstauthorCN")))
check("Q6-2b EN 模板 \\mytitleCN 已接线", _macro(_tex_full, "mytitleCN") == _CN_TITLE,
      repr(_macro(_tex_full, "mytitleCN")))
check("Q6-2c EN 模板 \\myabstractCN 已接线", "生活世界" in _macro(_tex_full, "myabstractCN"),
      repr(_macro(_tex_full, "myabstractCN")[:40]))
check("Q6-2d 英文题名/作者槽位未被中文块污染",
      _macro(_tex_full, "mytitle") == _EN_TITLE
      and _macro(_tex_full, "myfirstauthor").strip().endswith(_EN_AUTH),
      f"{_macro(_tex_full, 'mytitle')!r}/{_macro(_tex_full, 'myfirstauthor')!r}")

# ② 只有摘要（+关键词）：无中文题名/作者行
only_abs = _en_body() + ["References",
                         "Su, W. (2025). The structure of human cooperation. "
                         "Journal of Philosophy, 1(1), 1-20.",
                         _CN_ABS, _CN_KW]
_d_abs = parse_docx(_fixture("q6_fwd_abs", only_abs))
check("Q6-3a 只有摘要：abstract_cn 补出", "生活世界" in _d_abs.abstract_cn)
check("Q6-3b 只有摘要：keywords_cn 补出", _d_abs.keywords_cn == "文化演化；双层协调；论证理论")
check("Q6-3c 只有摘要：不凭空造 title_cn/author_cn",
      _d_abs.title_cn == "" and _d_abs.author_cn == "", f"{_d_abs.title_cn!r}/{_d_abs.author_cn!r}")

# ③ 无文末块：字段保持空（不误报）
nob = _en_body() + ["References",
                    "Su, W. (2025). The structure of human cooperation. "
                    "Journal of Philosophy, 1(1), 1-20."]
_d_nob = parse_docx(_fixture("q6_fwd_none", nob))
check("Q6-4 无文末块：title_cn/author_cn/abstract_cn/keywords_cn 全空",
      _d_nob.title_cn == "" and _d_nob.author_cn == ""
      and _d_nob.abstract_cn == "" and _d_nob.keywords_cn == "",
      f"{_d_nob.title_cn!r}/{_d_nob.author_cn!r}/{_d_nob.abstract_cn!r}/{_d_nob.keywords_cn!r}")

# ═══════════════════════════════════════════════════════════════════════════
# Q6 反方向：中文稿 + 文末英文块 → title_en/author_en/abstract_en/keywords_en
# ═══════════════════════════════════════════════════════════════════════════
print("\n[Q6-反向] 中文正文 + 文末英文块 → title_en/author_en/abstract_en/keywords_en")

_CN_BODY = [_CN_TITLE, _CN_AUTH, _CN_ABS]
for i in range(6):
    _CN_BODY.append(f"这是正文第{i + 1}段，用于讨论双层协调结构与文化演化的关系。" * 3)
_EN_TITLE2 = "The Dual-Level Coordination Structure of the Lifeworld"
_EN_ABS2 = ("Abstract: This paper argues that the lifeworld rests on a dual-level "
            "coordination structure of argumentation practices. " * 2)
_EN_KW2 = "Keywords: cultural evolution; dual-level coordination; argumentation theory"


def _rev(with_title_author: bool = True, with_block: bool = True) -> list[str]:
    rows = list(_CN_BODY) + ["参考文献",
                             "伍素. (2025). 生活世界论证的双层协调结构. 哲学研究, 1(1), 1-20."]
    if with_block:
        if with_title_author:
            rows += [_EN_TITLE2, _EN_AUTH]
        rows += [_EN_ABS2, _EN_KW2]
    return rows


_d_rev = parse_docx(_fixture("q6_rev_full", _rev()))
check("Q6-5a title_en 从文末英文块补出", _d_rev.title_en == _EN_TITLE2, repr(_d_rev.title_en))
check("Q6-5b author_en 补出（不倒装）", _d_rev.author_en == _EN_AUTH, repr(_d_rev.author_en))
check("Q6-5c abstract_en 补出", "lifeworld" in _d_rev.abstract_en, repr(_d_rev.abstract_en[:50]))
check("Q6-5d keywords_en 补出",
      _d_rev.keywords_en == "cultural evolution; dual-level coordination; argumentation theory",
      repr(_d_rev.keywords_en))
check("Q6-5e 中文原字段不被覆盖",
      _d_rev.title_cn == _CN_TITLE and _d_rev.author == _CN_AUTH,
      f"{_d_rev.title_cn!r}/{_d_rev.author!r}")

_tex_rev = _build(_fixture("q6_rev_full2", _rev()), "Q6rev", "zh")
check("Q6-6a CN 模板 \\mytitleEN 已接线", _macro(_tex_rev, "mytitleEN") == _EN_TITLE2,
      repr(_macro(_tex_rev, "mytitleEN")))
check("Q6-6b CN 模板 \\myfirstauthorEN 已接线",
      _macro(_tex_rev, "myfirstauthorEN") == _EN_AUTH, repr(_macro(_tex_rev, "myfirstauthorEN")))
check("Q6-6c CN 模板 \\myabstractEN 已接线",
      "lifeworld" in _macro(_tex_rev, "myabstractEN"), repr(_macro(_tex_rev, "myabstractEN")[:40]))
check("Q6-6d 中文题名/作者槽位未被英文块污染",
      _macro(_tex_rev, "mytitle") == _CN_TITLE and _CN_AUTH in _macro(_tex_rev, "myfirstauthor"),
      f"{_macro(_tex_rev, 'mytitle')!r}/{_macro(_tex_rev, 'myfirstauthor')!r}")

# 反向·只有摘要
_d_rev2 = parse_docx(_fixture("q6_rev_abs", _rev(with_title_author=False)))
check("Q6-7 反向只有摘要：abstract_en 补出、title_en/author_en 留空",
      "lifeworld" in _d_rev2.abstract_en and _d_rev2.title_en == "" and _d_rev2.author_en == "",
      f"{_d_rev2.title_en!r}/{_d_rev2.author_en!r}")

# 反向·无块
_d_rev3 = parse_docx(_fixture("q6_rev_none", _rev(with_block=False)))
check("Q6-8 反向无块：英文四字段全空",
      _d_rev3.title_en == "" and _d_rev3.author_en == ""
      and _d_rev3.abstract_en == "" and _d_rev3.keywords_en == "",
      f"{_d_rev3.title_en!r}/{_d_rev3.author_en!r}/{_d_rev3.abstract_en!r}/{_d_rev3.keywords_en!r}")

# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print(f"全部 {PASS} 项断言通过")
sys.exit(0)
