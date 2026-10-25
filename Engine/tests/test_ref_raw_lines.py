#!/usr/bin/env python3.14
"""「条目 → 源稿原文」映射（第 68 条）：三条解析通道 + 任务书 + AI 请求。

背景（第 66 条的真因）：任务书与 AI 预跑从前只拿得到**我们解析出来的条目**。解析器丢一个
字段（实测 `[17] 党学哲，…，湖南科技大学学报（社会科学版），…` 丢了刊名），编辑与模型
看到的就只有残缺件，模型再「如实」回一句「源条目为 @misc，缺期刊信息」——看上去像模型
不行，其实是我们喂进去的本身就是残缺件。所以本轮把**每条被解析条目的源稿原文**挂到
`DocxDoc.ref_raw_by_key`（{最终 bib 键: 原文}），并让任务书逐条清单与 ⑪ 的每条请求都带上它。

五组断言（都钉「原文优先」这条口径的两端：带上原文 / 没原文时一字不变）：
  (a) 编号清单通道（`[N] …`）：原文含标号、逐键对齐、**被跳过的条目没有映射**；
  (b) 脚注通道（脚注体例）：映射 = 真正喂给解析器的那段脚注文献文字；
  (c) 文末人年制通道：映射 = `RefEntry.raw`（`_dedupe_keys` 之后的最终键）；
  (d) 任务书：`build(raw_by_key=…)` 逐条带「源稿原文」行；没命中的键一个字都不多写；
      `raw_by_key={}` 与旧调用**逐字节相同**；
  (e) AI 请求：`entry_prompt(..., raw)` 含引导行与原文；`raw=""` 与旧形状逐字节相同；
  (f) 归一化：换行/制表符/全角空格压成一行、超长截断并**标注**、反引号不破行内代码；
  (g) 真实稿反例（语料不在本机则 SKIP）：`Dang2022` 的原文逐字等于那份 `[17]` 行。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_ref_raw_lines.py
"""
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import refmark  # noqa: E402
from slcn.docxsource import DocxDoc, _numbered_refs, parse_docx  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


LABEL = "- **源稿原文**（解析可能有遗漏，请以它为准）："

# 真实稿 `2026年第5期(90)/01 Meimei_Han/定稿.docx` 第 17 条（第 66/68 条的当事人）。
# ⚠️ 逐字抄自源稿：中文全角逗号、`第29-38页` 用的是**短横**（源稿如此，规范要求的是双短横）。
DANG_RAW = ("[17] 党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），"
            "2022年第6期，第29-38页。")

# ══ (a) 编号清单通道 ═══════════════════════════════════════════════════════
print("── (a) 编号清单通道：原文含 `[N]` 标号、逐键对齐 ──")

NUM_LINES = [
    "[1] T. Burge, 1984, “Epistemic Paradox”, The Journal of Philosophy, 81(1): 5–29.",
    DANG_RAW,
    "[18] 党学哲，“证明论语义及其意义颗粒度”，逻辑学研究，2024年第1期，第69–86页。",
]
entries, num2key, raw_by_key = _numbered_refs(NUM_LINES)
check("(a) 3 条全部解析出来（既有行为不变）", len(entries) == 3, f"{len(entries)}")
check("(a) 映射与条目数一致（每条都有原文）", len(raw_by_key) == len(entries),
      f"{len(raw_by_key)} vs {len(entries)}")
check("(a) 键来自最终 bib 键（与 `doc.bib_entries` 同源）",
      set(raw_by_key) == {k for k, _e, _t in entries}, repr(sorted(raw_by_key)))
check("(a) 编号映射一个都没丢（num2key 与条目数一致）", len(num2key) == 3, repr(num2key))
check("(a) `Dang2022` 的原文**逐字**等于源稿那一行",
      raw_by_key.get("Dang2022") == DANG_RAW, repr(raw_by_key.get("Dang2022")))
check("(a) 原文带 `[N]` 标号（不是去掉标号的解析用段）",
      all(v.startswith("[") for v in raw_by_key.values()), repr(sorted(raw_by_key.values())[:1]))
check("(a) 逐条对齐：Burge1984 拿到的不是别条的原文",
      raw_by_key.get("Burge1984", "").startswith("[1] T. Burge")
      and "党学哲" not in raw_by_key.get("Burge1984", ""),
      repr(raw_by_key.get("Burge1984")))

# 反例：被 `_numbered_refs` **跳过**的条目（只有（著）、短题名）不许留下映射——
# 「有原文」与「有条目」必须严格一一对应，否则任务书会为不存在的条目写一段原文。
SKIP_LINES = [
    "[1] 亚里士多德（著），颜一，工具论，北京：商务印书馆，2003年。",   # 短题名、无（译）→ 跳过
    "[2] 李雷，逻辑学导论，北京：中国人民大学出版社，2003年。",
]
e2, _n2, raw2 = _numbered_refs(SKIP_LINES)
check("(a) 反例：被跳过的短题名条目不进映射（映射只含留下的条目）",
      len(e2) == 1 and list(raw2) == [e2[0][0]] and raw2[e2[0][0]].startswith("[2]"),
      f"entries={[k for k, _e, _t in e2]} raw={list(raw2)}")

# ══ (b)/(c) 合成最小 docx：脚注通道 + 人年制通道 ═══════════════════════════
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TMP = []

FN_REF = ('T. Burge, "Epistemic Paradox", The Journal of Philosophy 81(1984), '
          'pp. 5-29.')
FN_NOTE = "这是作者的论述，不是文献条目：针对传统营销的4P理论，4C理论由劳特朋提出。"


def make_docx(paras, footnotes=()):
    """最小 docx：word/document.xml（段落）+ 可选 word/footnotes.xml。

    `footnotes` = [(id, 文本)]；不写 `w:type`（真脚注的常见形态，见 test_footnote_id0.py）。
    """
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        if footnotes:
            fns = f'<w:footnotes xmlns:w="{W_NS}">' + "".join(
                f'<w:footnote w:id="{i}"><w:p><w:r>'
                f'<w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p></w:footnote>'
                for i, t in footnotes) + "</w:footnotes>"
            z.writestr("word/footnotes.xml", fns)
    TMP.append(path)
    return path


BODY = [f"正文第{i}段，讨论数据库逻辑与数据库理论之间的关系。" for i in range(12)]

print("\n── (b) 脚注通道：映射 = 真正喂给解析器的那段脚注文字 ──")
p_fn = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：数据库；逻辑"]
                 + BODY, footnotes=[(0, FN_REF), (1, FN_NOTE)])
d_fn = parse_docx(p_fn)
check("(b) 脚注文献条目解析出来了（既有行为不变，1 条）",
      len(d_fn.bib_entries) == 1, f"{len(d_fn.bib_entries)}")
if d_fn.bib_entries:
    _k = d_fn.bib_entries[0][0]
    check("(b) 该条的最终 bib 键在映射里，原文逐字等于脚注里的文献文字",
          d_fn.ref_raw_by_key.get(_k) == FN_REF,
          f"key={_k!r} raw={d_fn.ref_raw_by_key.get(_k)!r}")
    check("(b) 反例：纯论述脚注（不是文献）**不**进映射",
          all(v != FN_NOTE for v in d_fn.ref_raw_by_key.values()),
          repr(list(d_fn.ref_raw_by_key.values())))
check("(b) 没有文献条目时映射是空表（不是 None）", isinstance(d_fn.ref_raw_by_key, dict))

print("\n── (c) 文末人年制通道：映射 = RefEntry.raw（最终键） ──")
REF_LINE = "Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall."
p_yr = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：数据库；逻辑"]
                 + BODY + ["参考文献", REF_LINE])
d_yr = parse_docx(p_yr)
check("(c) 人年制条目解析出来了（既有行为不变，1 条）",
      len(d_yr.refs) == 1, f"{len(d_yr.refs)}")
if d_yr.refs:
    _e = d_yr.refs[0]
    check("(c) 该条最终键的原文 = `RefEntry.raw`（那一行）",
          d_yr.ref_raw_by_key.get(_e.key) == _e.raw == REF_LINE,
          f"key={_e.key!r} raw={d_yr.ref_raw_by_key.get(_e.key)!r}")
check("(c) 编号清单映射与人年制映射挂在**同一个** dict 上（口径统一）",
      d_yr.ref_raw_by_key is not d_fn.ref_raw_by_key)

# ══ (d) 任务书逐条清单带原文 ═══════════════════════════════════════════════
print("\n── (d) 任务书：逐条带「源稿原文」，没原文的一字不写 ──")

BIB2 = """\
@misc{Dang2022,
  author = {党学哲},
  title  = {论作为证据逻辑的核证逻辑},
  year   = {2022}
}

@book{Li2022,
  author    = {李雷},
  title     = {逻辑例外论},
  publisher = {北京大学出版社},
  year      = {2022},
  userf     = {cn}
}
"""

md_old = refmark.build(BIB2, "S")
md_new = refmark.build(BIB2, "S", raw_by_key={"Dang2022": DANG_RAW})
check("(d) 命中的条目带「源稿原文」行，且原文逐字在那一行里",
      f"{LABEL}`{DANG_RAW}`" in md_new, repr(md_new[md_new.find(LABEL):][:60]))
check("(d) 没命中的条目（Li2022）**没有**「源稿原文」行",
      md_new.count(LABEL) == 1, f"count={md_new.count(LABEL)}")
check("(d) `raw_by_key={}` 与旧调用逐字节相同", refmark.build(BIB2, "S", raw_by_key={}) == md_old)
check("(d) 「以原文为准」的口径写进了规范正文（是给 AI 的指令，不只是排版）",
      "源稿原文" in refmark.SPEC_MD and "以它为准" in refmark.SPEC_MD.replace("*", "")
      and "不许编" in refmark.SPEC_MD.replace("*", ""))
check("(d) 规范逐字包含「以源稿原文为准」的硬要求",
      "按**源稿原文**补全或纠正字段" in refmark.SPEC_MD)
check("(d) 规范同时保留「原文没有的不许编」的下限",
      "原文里没有的信息仍然不许编" in refmark.SPEC_MD)
check("(d) 旧的 `raw_ref_lines` 行为未受影响（第五节仍在 0 条时追加）",
      "## 五、未解析的文献区原文（供结构化）" in refmark.build("", "S", raw_ref_lines=["x"]))

# ══ (e) AI 请求含原文 ═══════════════════════════════════════════════════════
print("\n── (e) ⑪ 逐条请求：含引导行与原文；无原文时形状不变 ──")

BODY_FIELDS = "  author = {党学哲},\n  title  = {论作为证据逻辑的核证逻辑},\n  year   = {2022}"
# ⚠️ 与 `convert.py` 的调用口径一致：`body` 来自 `bibcheck.split_entries`，两边都先 strip。
BODY_STRIPPED = BODY_FIELDS.strip()
p_old_shape = "@misc{Dang2022,\n" + BODY_STRIPPED + "}"
p_raw = refmark.entry_prompt("misc", "Dang2022", BODY_FIELDS, DANG_RAW)
p_none = refmark.entry_prompt("misc", "Dang2022", BODY_FIELDS)
check("(e) 无原文 → 与旧形状（`@type{key,…}`）**逐字节相同**", p_none == p_old_shape,
      repr(p_none[:40]))
check("(e) 无原文 → 不含任何引导行", refmark.RAW_PROMPT_HEADER not in p_none)
check("(e) 有原文 → 含「解析得到的条目」与那条 bibtex", "解析得到的条目：" in p_raw
      and p_old_shape in p_raw)
check("(e) 有原文 → 含引导行（原文优先 + 不许编）",
      refmark.RAW_PROMPT_HEADER in p_raw
      and "以它为准" in refmark.RAW_PROMPT_HEADER
      and "不许编" in refmark.RAW_PROMPT_HEADER)
check("(e) 有原文 → 原文逐字在请求里", DANG_RAW in p_raw)
check("(e) 请求里仍只有**一条** bibtex 条目（回复解析口径不变）",
      p_raw.count("@misc{") == 1 and p_raw.count("条目 key：") == 1)

# ══ (f) 归一化 ═════════════════════════════════════════════════════════════
print("\n── (f) 归一化：压成一行、去首尾、截断标注、反引号不破行内代码 ──")

check("(f) 换行/制表符/全角空格压成单个半角空格，且去首尾空白",
      refmark._norm_raw_line("\n\t  第一行\u3000 第二行\t \n") == "第一行 第二行",
      repr(refmark._norm_raw_line("\n\t  第一行\u3000 第二行\t \n")))
long_raw = "长" * (refmark.RAW_LINE_MAX + 50)
normed = refmark._norm_raw_line(long_raw)
check("(f) 超长截断到上限并**标注**（不静默丢弃）",
      len(normed) == refmark.RAW_LINE_MAX + len("…（已截断）") and normed.endswith("…（已截断）"),
      f"len={len(normed)}")
check("(f) 恰好等于上限的原文**不**标注截断",
      refmark._norm_raw_line("长" * refmark.RAW_LINE_MAX) == "长" * refmark.RAW_LINE_MAX)
check("(f) 空串/None 都归成空串（下游据此判「没有原文」）",
      refmark._norm_raw_line("") == "" and refmark._norm_raw_line(None) == "")
check("(f) 原文里的反引号不会提前关掉行内代码（分隔符严格更长）",
      refmark._code_span("含 ``` 三条") == "````含 ``` 三条````",
      repr(refmark._code_span("含 ``` 三条")))
md_tick = refmark.build(BIB2, "S", raw_by_key={"Dang2022": "原文含 ``` 三个反引号"})
check("(f) 含反引号的原文进任务书后行内代码仍闭合",
      "````原文含 ``` 三个反引号````" in md_tick)

# ══ (g) 真实稿 ═════════════════════════════════════════════════════════════
print("\n── (g) 真实稿：`Dang2022` 的原文逐字等于那份 `[17]` 行 ──")

REAL = os.path.join(_REPO, "2026年第5期(90)", "01 Meimei_Han", "定稿.docx")
if not os.path.exists(REAL):
    skip("(g) 真实稿 01 Meimei_Han/定稿.docx", "语料不在本机")
else:
    d = parse_docx(REAL)
    check("(g) 真实稿：20 条编号清单全部拿到原文",
          len(d.bib_entries) == 20 and len(d.ref_raw_by_key) == 20,
          f"entries={len(d.bib_entries)} raw={len(d.ref_raw_by_key)}")
    check("(g) 真实稿：`Dang2022` 的原文逐字等于 `[17] …` 那一行",
          d.ref_raw_by_key.get("Dang2022") == DANG_RAW,
          repr(d.ref_raw_by_key.get("Dang2022")))
    check("(g) 真实稿：映射键集合 == bib 条目键集合",
          set(d.ref_raw_by_key) == {k for k, _e, _r in d.bib_entries})
    # 任务书端到端：用**引擎自己写出的** bib（`build.py` 走的是同一个 `entry_to_bib`）。
    # ⚠️ 不能用稿件目录里那份 `reference.bib`：那是**编辑的人工成品**，键是 `1`…`20`，
    #    与引擎生成的 `Dang2022` 这类键**不是一套**（第 63 条已记过这件事）。
    from slcn.footnotes import entry_to_bib

    _bib = "\n".join(entry_to_bib(k, e, r) for k, e, r in d.bib_entries)
    if "@" in _bib:
        _md = refmark.build(_bib, "Meimei_Han", raw_by_key=d.ref_raw_by_key)
        check("(g) 真实稿：任务书里 `Dang2022` 那一节带上了它的原文",
              f"{LABEL}`{DANG_RAW}`" in _md, repr(_md[_md.find(LABEL):][:50]))
        check("(g) 真实稿：20 条都带上了原文（逐条清单里 20 行）",
              _md.count(LABEL) == 20, f"count={_md.count(LABEL)}")

for _t in TMP:
    try:
        os.unlink(_t)
    except OSError:
        pass

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
