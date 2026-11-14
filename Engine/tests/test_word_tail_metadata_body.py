#!/usr/bin/env python3.14
"""D-1：文末第二语种元数据块不得吞掉正文（Word 通道，2026-11-14）。

缺陷
----
短英文稿 + 文末中文元数据块（14 段，`摘要：`/`关键词：` 落在第 13/14 段）：
`parse_docx` 的头部元数据扫描只按 `plains[:META_SCAN]`（20 段）取标签，于是
`meta_end = max(摘要/关键词标签下标)` 被文末块顶到 13，正文起点 = 14 = 段数，
`body` 为空；convert 仍 rc=0 并产出 PDF（静默丢失正文）。

判据（本文件锁定）
------------------
头部元数据只可能是**参考文献标题之前**的标签；参考文献标题之后（以及其后被
`locate_tail_block` 认定的文末块起点之后）的标签属于尾部第二语种元数据，由
③.2 / ③.5 / ③.5b 从文末取，不参与 `meta_end`。因此：
  · 正文与章节保留；
  · 中英双语题名/摘要/关键词都保留（头部一套 + 文末一套）；
  · 无尾块稿件 / 长稿（尾块在窗口外）逐字节不变。

五组断言
--------
  ① 短英文 + 窗口内中文尾块（原 D-1 形态）；
  ② 长英文 + 窗口外中文尾块（修短稿不能损长稿）；
  ③ 英文无尾块（原行为不变）；
  ④ 正常中文稿 + 英文尾部元数据（正文与双语元数据无退化）；
  ④b 短中文 + 窗口内英文尾块（与 D-1 对称的另一侧）；
  ⑤ 公开冻结反例（本机存在则跑，缺失 SKIP）：原 D-1 / 加长对照的正文、章节、
     脚注与 `[n]` 引用。

不写 out/、不联网、不跑 corpus.py。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_word_tail_metadata_body.py
"""
import os
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import parse_docx  # noqa: E402

FAILS = []
TMP = []

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")


def make_docx(paras):
    """只含 word/document.xml 的最小 docx（沿用 test_abstract_label.py 的构造）。"""
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    TMP.append(path)
    return path


EN_HEAD = [
    "Open Questions in the SLCN-2026 System: The Horn-Selb Debate Revisited",
    "Wei Zhang",
    "Abstract: This synthetic manuscript exercises the DOCX channel: unknown proper "
    "nouns such as Casimir von Horn and Otto Selb, citations [1], and footnotes.",
    "Keywords: formalization; proper names; escaping",
]
EN_BODY = [
    "1 Introduction",
    "Prior work[1] shows that proper-name handling affects the quality of the first "
    "proof; we add three observations[2].",
    "2 Conclusion",
    "Open questions still require human proofreading; escaping and proper names stay "
    "verbatim.",
]
EN_REFS = [
    "References",
    "[1] Casimir von Horn. On Open Questions[J]. Studies in Logic, 2024, 17(2): 1--12.",
    "[2] Otto Selb. Proper Names and Escaping[M]. Beijing: Example Press, 2023.",
]
CN_TAIL = [
    "系统未定问题研究：以霍恩与塞尔布的论辩为例",
    "张伟",
    "摘要：本文以 SLCN-2026 系统为例，讨论特殊符号的转义处理，并检验未识别专名"
    "（卡西米尔·冯·霍恩、奥托·塞尔布）能否原文保留。",
    "关键词：形式化；专名；转义",
]
CN_TITLE = CN_TAIL[0]


def assert_bilingual_en_tail(d, tag):
    check(f"{tag}：中文题名保留", d.title_cn == CN_TITLE, repr(d.title_cn))
    check(f"{tag}：中文作者保留", d.author_cn == "张伟", repr(d.author_cn))
    check(f"{tag}：中文摘要保留", d.abstract_cn.startswith("本文以 SLCN-2026 系统为例"),
          repr(d.abstract_cn)[:60])
    check(f"{tag}：中文关键词保留", d.keywords_cn == "形式化；专名；转义", repr(d.keywords_cn))
    check(f"{tag}：英文题名保留", d.title_en.startswith("Open Questions in the SLCN-2026"),
          repr(d.title_en)[:60])
    check(f"{tag}：英文摘要保留", d.abstract_en.startswith("This synthetic manuscript"),
          repr(d.abstract_en)[:60])
    check(f"{tag}：英文关键词保留", d.keywords_en == "formalization; proper names; escaping",
          repr(d.keywords_en))


# ══ ① 短英文 + 窗口内中文尾块（原 D-1 形态）══════════════════════════════
print("── ① 短英文 + 窗口内中文尾块 ──")
tmp = make_docx(EN_HEAD + EN_BODY + EN_REFS + CN_TAIL)
d = parse_docx(tmp)
check("① 正文非空", len(d.body) > 0, f"{len(d.body)} 字符")
check("① 两个章节都在", len(d.headings) == 2, repr(d.headings))
check("① 正文含 Introduction 段", "\\section{Introduction}" in d.body)
check("① 正文含 Conclusion 段", "\\section{Conclusion}" in d.body)
check("① 正文含首段正文", "Prior work[1] shows" in d.body)
check("① 正文含末段正文", "Open questions still require" in d.body)
check("① 尾部中文块不进正文", CN_TITLE not in d.body and "系统未定" not in d.body)
assert_bilingual_en_tail(d, "①")


# ══ ② 长英文 + 窗口外中文尾块（尾块标签在 META_SCAN 之外）═══════════════
print("\n── ② 长英文 + 窗口外中文尾块 ──")
padded = list(EN_HEAD)
for k in range(1, 13):
    padded.append(f"{k} Section Heading {k}")
    padded.append(f"Body paragraph {k} discusses proper names such as Casimir von Horn "
                  f"and Otto Selb; citation [{(k % 2) + 1}] and macro characters % & # _.")
padded += EN_REFS + CN_TAIL
tmp = make_docx(padded)
d = parse_docx(tmp)
check("② 12 个章节都在", len(d.headings) == 12, repr([h for _, h in d.headings[:3]]))
check("② 正文含第 12 节", "\\section{Section Heading 12}" in d.body)
check("② 正文含第 12 节正文", "Body paragraph 12 discusses" in d.body)
assert_bilingual_en_tail(d, "②")


# ══ ③ 英文无尾块（原行为不变）═══════════════════════════════════════════
print("\n── ③ 英文无尾块 ──")
tmp = make_docx(EN_HEAD + EN_BODY + EN_REFS)
d = parse_docx(tmp)
check("③ 两个章节都在", len(d.headings) == 2, repr(d.headings))
check("③ 正文含 Intro/Conclusion",
      "\\section{Introduction}" in d.body and "\\section{Conclusion}" in d.body)
check("③ 英文摘要取到", d.abstract_en.startswith("This synthetic manuscript"),
      repr(d.abstract_en)[:50])
check("③ 英文关键词取到", d.keywords_en == "formalization; proper names; escaping")
check("③ 没有中文元数据被凭空造出",
      not d.title_cn and not d.abstract_cn and not d.keywords_cn and not d.author_cn,
      repr((d.title_cn, d.author_cn, d.abstract_cn, d.keywords_cn)))


# ══ ④ 正常中文稿 + 英文尾部元数据 ═══════════════════════════════════════
print("\n── ④ 正常中文稿 + 英文尾部元数据 ──")
CN_HEAD = [
    "论数据库逻辑的规范基础",
    "李雷",
    "摘要：本文讨论数据库逻辑的规范基础，并给出一个简短的说明。",
    "关键词：数据库逻辑；规范；语义",
]
NUM = "一二三四五六七八九十"
cn_body = []
for k in range(1, 13):
    label = NUM[k - 1] if k <= 10 else ("十一" if k == 11 else "十二")
    cn_body.append(f"{label}、第{k}节标题")
    cn_body.append(f"第{k}节正文，讨论数据库逻辑的规范基础、语义边界与适用范围，并给出例证。")
CN_REFS = ["参考文献", "[1] 李雷. 数据库逻辑研究[J]. 逻辑学研究, 2024, 17(2): 1-12."]
EN_TAIL = [
    "On the Normative Foundations of Database Logic",
    "Lei Li",
    "Abstract: This paper discusses the normative foundations of database logic.",
    "Keywords: database logic; norm; semantics",
]
tmp = make_docx(CN_HEAD + cn_body + CN_REFS + EN_TAIL)
d = parse_docx(tmp)
check("④ 12 个章节都在", len(d.headings) == 12, repr([h for _, h in d.headings[:3]]))
check("④ 正文含第 12 节正文", "第12节正文" in d.body)
check("④ 中文题名/摘要/关键词保留",
      d.title_cn == CN_HEAD[0]
      and d.abstract_cn.startswith("本文讨论数据库逻辑的规范基础")
      and d.keywords_cn == "数据库逻辑；规范；语义",
      repr((d.title_cn, d.abstract_cn[:20], d.keywords_cn)))
check("④ 英文题名/摘要/关键词保留",
      d.title_en == EN_TAIL[0]
      and d.abstract_en.startswith("This paper discusses")
      and d.keywords_en == "database logic; norm; semantics",
      repr((d.title_en, d.abstract_en[:30], d.keywords_en)))
check("④ 尾部英文块不进正文", "On the Normative Foundations" not in d.body)


# ══ ④b 短中文 + 窗口内英文尾块（与 D-1 对称的另一侧）═══════════════════
print("\n── ④b 短中文 + 窗口内英文尾块 ──")
CN_SHORT = CN_HEAD + cn_body[:6] + CN_REFS + EN_TAIL
tmp = make_docx(CN_SHORT)
d = parse_docx(tmp)
check("④b 三个章节都在", len(d.headings) == 3, repr(d.headings))
check("④b 正文含三节正文",
      all(f"第{k}节正文" in d.body for k in (1, 2, 3)), repr(d.body[:80]))
check("④b 中文/英文元数据都保留",
      d.title_cn == CN_HEAD[0] and d.abstract_cn.startswith("本文讨论")
      and d.title_en == EN_TAIL[0] and d.abstract_en.startswith("This paper"),
      repr((d.title_cn, d.title_en, d.abstract_cn[:16], d.abstract_en[:16])))
check("④b 尾部英文块不进正文", "On the Normative Foundations" not in d.body)


# ══ ⑤ 公开冻结反例（本机存在则跑）══════════════════════════════════════
print("\n── ⑤ 公开冻结反例 ──")
PUBLIC = [
    ("D-1 原反例",
     "/Users/zhangwenming/.codex/evid/MAC-R1R6-20261114/synthetic/r2/in/"
     "SLCN-2026-EN-tail.docx"),
    ("D-1 加长对照",
     "/Users/zhangwenming/.codex/evid/MAC-R1R6-AMEND-20261114/outputs/in/"
     "EN-tail-padded.docx"),
]
for tag, path in PUBLIC:
    if not os.path.exists(path):
        skip(f"⑤ {tag}", "本机没有公开冻结输入")
        continue
    d = parse_docx(path)
    if tag == "D-1 原反例":
        check("⑤ 原反例：正文与两节保留",
              len(d.body) > 0 and len(d.headings) == 2
              and "\\section{Introduction}" in d.body
              and "\\section{Conclusion}" in d.body,
              f"{len(d.body)} 字符 / {len(d.headings)} 节")
        check("⑤ 原反例：脚注与 [n] 引用随正文保留",
              "\\footnote{" in d.body and "[1]" in d.body and "[2]" in d.body,
              repr(d.body[:80]))
        assert_bilingual_en_tail(d, "⑤ 原反例")
    else:
        check("⑤ 加长对照：12 节正文保留",
              len(d.headings) == 12 and "\\section{Section Heading 12}" in d.body,
              f"{len(d.body)} 字符 / {len(d.headings)} 节")
        assert_bilingual_en_tail(d, "⑤ 加长对照")

for p in TMP:
    try:
        os.unlink(p)
    except OSError:
        pass

print(f"\n{'=' * 60}\n失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
