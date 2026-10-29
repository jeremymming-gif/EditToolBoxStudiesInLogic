#!/usr/bin/env python3.14
r"""B-199（2026-10-23）：**脚注引用提升**——假 mixed 修正 / note_only 探测 /
真 mixed 拆分 / container 匹配。

缺陷（B-198 批 A 分诊，真实 41 篇实测；本套件只用**脱敏合成**文本与 docx）
--------------------------------------------------------------------------
· 90-07：4 条「参阅 X, …」纯引用被 `_PROSE_CUES` 的「参阅」误判混合型
  （prose 判定在 parse 之前）；Kuhn 无年份条目被 legacy `classify()` 判 pure_note
  后静默 note_only（日志零记录）。
· 90-06：`“Volitional Necessities”, in Agency and Answerability` 的**容器题名**
  在引注原文里，但 resolve 只比题名 → 漏；Tolstoy 无年份中文条目同上被静默。
· 86-08：混合脚注里的 Hacking 1999 条目不进 bib、注内无 `\parencite`。
· 88-02：混合脚注里的 Novaes 2018 条目不进 bib。

修法（全部字段来自原文，宁可不提升，不可误提升）
------------------------------------------------
① 引导语（参阅/参见/See/Cf.）先剥再判完整条目；长条目即使被散文判据命中，
   只要整段是一条完整条目也不判 mixed；
② legacy pure_note 里有「作者/年份 + 题名」证据、且无引者注/古典标记时，
   升为待匹配短引用（docxsource 用 `strict=True` 唯一匹配；匹配不上仍按纯注释）；
③ 题名不匹配时允许 **container/booktitle** 匹配（候选题名在引注原文里逐字出现，
   归一后 ≥8 字符，作者姓仍须匹配）；
④ 真 mixed 用 note_before/ref/note_after 拆分：ref 入 bib（或匹配既有键），
   脚注保留并在原位注入 `\parencite`；古典文献（SL/OP/罗马卷次）与「散文式
   英文论述脚注」不拆；多作品（≥2 题名）拆分时保留原文、parencite 追加在末尾。

本套件钉住（≥8 条断言，含负例）
------------------------------
① 假 mixed：英文/中文「参阅…完整条目」→ reference_entry；真说明+引用不误判；
   引者注/古典不误提升；
② note_only 探测：无年份但有题名+作者 → 可探测；纯说明/引者注/无题名 → 不探测；
③ 混合拆分：Hacking 型（APA 条目）拆出干净字段；多作品计数；
   古典（SL/OP）、散文式英文论述脚注**不拆**；
④ container 匹配：析出文献题名不同但容器题名在原文 → 命中；无证据不命中；
⑤ strict 唯一匹配：唯一命中才提升；同档多候选/无题名 → 拒绝；
⑥ 文末清单原文行匹配：唯一命中/年份不符/多行歧义；
⑦ 端到端合成 docx：假 mixed → 正文 parencite 且 bib 不重复；混合拆分 → bib +1
   且脚注内 parencite；note_only 探测 → 正文 parencite；纯说明脚注保留不动。

不联网、不读真实稿件库、不写仓库（全部 tempfile）。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
      tests/test_b199_footnote_promotion.py
"""

import os
import sys
import tempfile
import zipfile
from types import SimpleNamespace

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footnotes as F  # noqa: E402
from slcn.convert_bib import run_refs_attach  # noqa: E402
from slcn.docxsource import (  # noqa: E402
    _src_line_key,
    _src_line_keys_for_works,
    link_numbered_citations,
    parse_docx,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


# ══ ① 假 mixed 修正（分类器） ═══════════════════════════════════════════════
print("── ① classify_citation：引导语后的完整条目不判 mixed ──")
_en = F.classify_citation(
    "参阅 R. Camap, “The Aim of Inductive Logic”in Logic, Methodology and "
    "Philosophy of Science, eds. E. Nagel, P. Suppes, and A. Tarski, "
    "Stanford: Stanford University Press, 1962.")
check("① 英文「参阅 X, “题名”, 出版信息…」→ reference_entry（90-07 #3）",
      _en.kind_new == F.CITATION_ENTRY and _en.cue_stripped
      and str(_en.entry.get("title", "")) == "The Aim of Inductive Logic"
      and str(_en.entry.get("year", "")) == "1962",
      f"kind={_en.kind_new} entry={_en.entry}")

_cn = F.classify_citation("参阅徐强：《贝叶斯方法的基本原理及其哲学意蕴》，"
                          "《哲学分析》2026年第1期。")
check("① 中文「参阅 作者：《题名》，《刊名》年份」→ reference_entry（90-07 #45）",
      _cn.kind_new == F.CITATION_ENTRY
      and str(_cn.entry.get("author", "")) == "徐强"
      and str(_cn.entry.get("title", "")) == "贝叶斯方法的基本原理及其哲学意蕴",
      f"kind={_cn.kind_new} entry={_cn.entry}")

# 负例：说明文字 + 引用（86-08 型）不能整条当完整条目
_mixed = F.classify_citation(
    "艾金关于无关型类别和互动型类别的命名引用自哈金，参见Hacking, Ian. (1999) "
    "The Social Construction of What? Cambridge: Harvard University Press.")
check("① 负例：说明文字 + 引用不判 reference_entry（会拆 note/ref）",
      _mixed.kind_new != F.CITATION_ENTRY
      and "艾金" in (_mixed.note_before or "") and "Hacking" in _mixed.ref_raw,
      f"kind={_mixed.kind_new}")

# 负例：引者注不能因为含「参见 + 书名号」被提升
_disc = F.classify_citation("笔者在另一篇论文中已经处理过这个问题，参见《科学革命的结构》。")
check("① 负例：引者注（笔者…参见《…》）→ note_only，不探测提升",
      _disc.kind_new == F.CITATION_NOTE and not _disc.from_note_probe,
      f"kind={_disc.kind_new} probe={_disc.from_note_probe}")

# 负例：古典文献不提升（legacy classical 分支）
_class = F.classify_citation("Ockham, SL II, 1, OP I, 242.")
check("① 负例：古典文献（SL/OP 卷次）→ note_only",
      _class.kind_new == F.CITATION_NOTE, f"kind={_class.kind_new}")

# ══ ② note_only 探测 ═══════════════════════════════════════════════════════
print("── ② note_only 探测（有题名证据才探测） ──")
_kuhn = F.classify_citation("托马斯•库恩：《科学革命的结构》，第66页。")
check("② 无年份但有作者+题名 → pure_citation + from_note_probe（待 strict 匹配）",
      _kuhn.kind_new == F.CITATION_PURE and _kuhn.from_note_probe,
      f"kind={_kuhn.kind_new} probe={_kuhn.from_note_probe}")

_plain = F.classify_citation("在有关文献中，常把主观主义概率论叫做“私人主义”。")
check("② 负例：纯说明（无题名/作者证据）→ note_only，不探测",
      _plain.kind_new == F.CITATION_NOTE and not _plain.from_note_probe,
      f"kind={_plain.kind_new}")

_prose_note = F.classify_citation("库恩在《科学革命的结构》中指出范式不可通约。")
check("② 负例：中文散文注（库恩在《…》中指出…）→ note_only，不探测",
      _prose_note.kind_new == F.CITATION_NOTE and not _prose_note.from_note_probe,
      f"kind={_prose_note.kind_new} probe={_prose_note.from_note_probe}")

_quote = F.classify_citation("参见《哲学研究》2024年第3期。")
check("② 负例：只有《刊名》+年份、无作者 → 不探测/不提为条目（保留脚注）",
      _quote.kind_new in (F.CITATION_NOTE, F.CITATION_MIXED)
      and not _quote.from_note_probe,
      f"kind={_quote.kind_new} ref={_quote.ref_raw!r}")

# ══ ③ 真 mixed 拆分 ════════════════════════════════════════════════════════
print("── ③ 真 mixed 拆分（note_before/ref/note_after） ──")
_hack = F.classify_citation(
    "艾金关于无关型类别和互动型类别的命名引用自哈金，参见Hacking, Ian. (1999) "
    "The Social Construction of What? Cambridge: Harvard University Press.")
check("③ Hacking 型：mixed_split + 干净 APA 字段 + note_before 保留「参见」",
      _hack.kind_new == F.CITATION_MIXED_SPLIT
      and str(_hack.entry.get("author", "")) == "Hacking, Ian"
      and str(_hack.entry.get("title", "")) == "The Social Construction of What?"
      and str(_hack.entry.get("year", "")) == "1999"
      and _hack.note_before.endswith("参见") and _hack.note_after == "",
      f"kind={_hack.kind_new} entry={_hack.entry} before={_hack.note_before[-6:]!r}")

_multi = F.classify_citation(
    "笔者在若干关于真理的论文中，已经把枚举归纳法作为确立事实的方法。"
    "参阅徐强：《归纳使真者与真之定义——基于系统本体论的统一多元真理论》，"
    "《武汉科技大学学报》，2024年第1期；《功能主义真理论的新进展：归纳-符合"
    "使真者理论》，《哲学分析》2025年第1期。")
check("③ 多作品混合：multi_work + 两段题名（同段《刊名》不算第二部作品）",
      _multi.kind_new == F.CITATION_MIXED_SPLIT and _multi.multi_work
      and _multi.note_before.startswith("笔者在若干关于真理的论文中")
      and len(F._work_titles(_multi.ref_raw)) == 2,
      f"kind={_multi.kind_new} works={F._work_titles(_multi.ref_raw)}")

# 负例：古典（SL/OP）即使带年份也不拆
_cls1 = F.classify_citation(
    "Ockham, SL II, 1, OP I, 242.  We use SL as abbreviation for Ockham’s "
    "Summa Logicae, and OP for his Opera Philosophica et Theologica edited by "
    "Franciscan Institute at University St. Bonaventure, NY., 1967-1988. "
    "We will quote Summa Logicae as SL, chapter number, and OP, volume number, "
    "page number.")
check("③ 负例：古典引注（SL/OP）不拆（保持 mixed）",
      _cls1.kind_new == F.CITATION_MIXED, f"kind={_cls1.kind_new}")

_cls2 = F.classify_citation(
    "SL II, 10, Alfred Freddoso and Henry Shuurman, Ockham’s Theory of "
    "Propositions, South Bend, Ind.: St. Augustine Press, 1980p. 112.")
check("③ 负例：以 SL 开头的条目（带年份）也不拆（保持 mixed）",
      _cls2.kind_new == F.CITATION_MIXED, f"kind={_cls2.kind_new}")

# 负例：英文散文式论述脚注（88-01 型）不拆
_prose = F.classify_citation(
    "We discuss Burley before Ockham although De puritate artis logicae seems "
    "to react to Ockham’s theory (see Archambault 2018 and Dutilh Novaes and "
    "Crimi 2020). To us, it seems that the mainlines of Burley’s theory were "
    "quite different.")
check("③ 负例：英文散文脚注（We … (see Author 2018)）不拆",
      _prose.kind_new == F.CITATION_MIXED, f"kind={_prose.kind_new}")

# ══ ④ container/booktitle 匹配 ═════════════════════════════════════════════
print("── ④ resolve_citation：container/booktitle 匹配 ──")
_entries = [
    ("Gary1975", {"author": "Watson, G.", "title": "Free Agency", "year": "1975"}, ""),
    ("Gary2004", {"author": "Watson, G.", "title": "Agency and Answerability",
                  "year": "2004"}, ""),
    ("Harry1971", {"author": "Frankfurt, H.", "year": "1971",
                   "title": "Freedom of Will and the Concept of a Person"}, ""),
]
_cit = F.Footnote(fid=1, text="Gary Watson, “Volitional Necessities”, in "
                              "Agency and Answerability, pp. 106-107.",
                  ref_raw="Gary Watson, “Volitional Necessities”, in "
                          "Agency and Answerability, pp. 106-107.")
_key, _w = F.resolve_citation(_cit, _entries)
check("④ 析出文献题名不同、容器题名在原文 → 命中 Gary2004（90-06）",
      _key == "Gary2004" and _w is None, f"key={_key} warn={_w}")

_cit2 = F.Footnote(fid=2, text="Gary Watson, “Volitional Necessities”, pp. 106-107.",
                   ref_raw="Gary Watson, “Volitional Necessities”, pp. 106-107.")
_key2, _w2 = F.resolve_citation(_cit2, _entries)
check("④ 负例：原文无容器题名 → 不命中（不猜）",
      _key2 is None and _w2 is not None, f"key={_key2}")

# ══ ⑤ strict 唯一匹配（note_only 探测） ═════════════════════════════════════
print("── ⑤ resolve_citation(strict=True)：唯一命中才提升 ──")
_probe = F.Footnote(fid=3, text="托马斯•库恩：《科学革命的结构》，第66页。",
                    ref_raw="托马斯•库恩：《科学革命的结构》，第66页。")
_kuhn_entries = [("Tuo2012", {"author": "托马斯•库恩（著），金吾伦、胡新和（译）",
                              "title": "科学革命的结构（第四版）", "year": "2012"}, "")]
_key3, _w3 = F.resolve_citation(_probe, _kuhn_entries, strict=True)
check("⑤ 唯一命中 → 提升（无年份允许 tier 3）",
      _key3 == "Tuo2012" and _w3 is None, f"key={_key3} warn={_w3}")

_amb = _kuhn_entries + [("Tuo2012b", {"author": "托马斯•库恩（著）",
                                     "title": "科学革命的结构（第四版）",
                                     "year": "2012"}, "")]
_key4, _w4 = F.resolve_citation(_probe, _amb, strict=True)
check("⑤ 负例：同档多候选（不同键）→ strict 拒绝（不猜）",
      _key4 is None and _w4 is not None, f"key={_key4}")

_notitle = F.Footnote(fid=4, text="参见徐强，第66页。", ref_raw="参见徐强，第66页。")
_ty_entries = [("Xu2024", {"author": "徐强", "title": "归纳使真者与真之定义",
                             "year": "2024"}, ""),
               ("Xu2025", {"author": "徐强", "title": "功能主义真理论的新进展",
                             "year": "2025"}, "")]
_key5, _w5 = F.resolve_citation(_notitle, _ty_entries, strict=True)
check("⑤ 负例：无题名（仅作者+页码）→ strict 拒绝（tier 4 不算）",
      _key5 is None, f"key={_key5}")

# ══ ⑥ 文末清单原文行匹配 ═══════════════════════════════════════════════════
print("── ⑥ 文末清单原文行 → 既有键（唯一命中才算） ──")
_fake_doc = SimpleNamespace(
    ref_raw_lines=[
        "[1] 托马斯•库恩：《科学革命的结构》（第四版），北京大学出版社，2012年。",
        "[2] 徐强：《贝叶斯方法的基本原理及其哲学意蕴》，《哲学分析》2026年第1期。",
        "[3] 徐强：《归纳使真者与真之定义》，《武汉科技大学学报》，2024年第1期。",
    ],
    ref_key_by_num={1: "Tuo2012", 2: "Xu2026", 3: "Xu2024"},
    refs=[],
)
check("⑥ 题名唯一命中 → 既有键（Kuhn）",
      _src_line_key(_fake_doc, "科学革命的结构") == "Tuo2012",
      _src_line_key(_fake_doc, "科学革命的结构"))
check("⑥ 题名 + 年份一致 → 既有键（Chen）",
      _src_line_key(_fake_doc, "贝叶斯方法的基本原理及其哲学意蕴", "2026") == "Xu2026",
      _src_line_key(_fake_doc, "贝叶斯方法的基本原理及其哲学意蕴", "2026"))
check("⑥ 负例：年份不符 → 不命中",
      _src_line_key(_fake_doc, "贝叶斯方法的基本原理及其哲学意蕴", "2019") == "",
      _src_line_key(_fake_doc, "贝叶斯方法的基本原理及其哲学意蕴", "2019"))
check("⑥ 负例：题名过短（归一后 <4 字符）→ 不命中",
      _src_line_key(_fake_doc, "逻辑") == "", _src_line_key(_fake_doc, "逻辑"))
check("⑥ 多作品 → 逐作品唯一命中才返回（90-07 #27）",
      _src_line_keys_for_works(
          _fake_doc,
          "徐强：《归纳使真者与真之定义》，《武汉科技大学学报》，2024年第1期；"
          "《贝叶斯方法的基本原理及其哲学意蕴》，《哲学分析》2026年第1期。")
      == ["Xu2024", "Xu2026"],
      _src_line_keys_for_works(
          _fake_doc,
          "徐强：《归纳使真者与真之定义》，《武汉科技大学学报》，2024年第1期；"
          "《贝叶斯方法的基本原理及其哲学意蕴》，《哲学分析》2026年第1期。"))

_fake_prefix = SimpleNamespace(
    ref_raw_lines=["说明行（非清单）",
                   "[1] 托马斯•库恩：《科学革命的结构》，北京大学出版社，2012年。",
                   "[2] 徐强：《贝叶斯方法的基本原理及其哲学意蕴》，《哲学分析》2026年第1期。"],
    ref_key_by_num={1: "Tuo2012", 2: "Xu2026"},
    refs=[],
)
check("⑥ 编号清单前有说明行：按行内 [N] 取号，不返回邻条键",
      _src_line_key(_fake_prefix, "科学革命的结构") == "Tuo2012",
      _src_line_key(_fake_prefix, "科学革命的结构"))
check("⑥ 编号清单前有说明行：第二条仍取对",
      _src_line_key(_fake_prefix, "贝叶斯方法的基本原理及其哲学意蕴", "2026") == "Xu2026",
      _src_line_key(_fake_prefix, "贝叶斯方法的基本原理及其哲学意蕴", "2026"))

# 人年制清单：字段碎片但 raw 完整（90-06 译著拆条前）
_fake_human = SimpleNamespace(
    ref_raw_lines=["托尔斯泰著，方敬译，《伊凡伊里奇之死》，文化生活出版社，1947。"],
    ref_key_by_num={},
    refs=[SimpleNamespace(key="TuoErsitai1947",
                          raw="托尔斯泰著，方敬译，《伊凡伊里奇之死》，文化生活出版社，1947。")],
)
check("⑥ 人年制清单：字段碎片但 raw 行完整 → 命中（90-06 Tolstoy）",
      _src_line_key(_fake_human, "伊凡伊里奇之死") == "TuoErsitai1947",
      _src_line_key(_fake_human, "伊凡伊里奇之死"))

# ══ ⑦ 端到端合成 docx ═════════════════════════════════════════════════════
print("── ⑦ 端到端合成 docx（自动编号清单 + 4 条脚注） ──")
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _xesc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, num: bool = False, fnrefs: tuple[int, ...] = ()) -> str:
    ppr = ('<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
           if num else "")
    runs = f'<w:r><w:t xml:space="preserve">{_xesc(text)}</w:t></w:r>' if text else ""
    for fid in fnrefs:
        runs += f'<w:r><w:footnoteReference w:id="{fid}"/></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def _make_docx(path: str, footnotes: list[str], refs: list[str]) -> str:
    parts = [_p("测试题名：脚注引用提升"), _p("张三"),
             _p("摘要：这是测试用摘要，只用于合成文档解析。"),
             _p("关键词：脚注；引用；提升"),
             # 正文带编号引用证据（[1]/[2]/[3]），让 B-022 安全闸把文末自动编号物化
             _p("正文第一段，含正文编号引用（[1]）、（[2]）与（[3]）。",
                fnrefs=tuple(range(1, len(footnotes) + 1))),
             ]
    if refs:
        parts.append(_p("参考文献"))
        parts.extend(_p(t, num=True) for t in refs)
    doc_xml = f'<w:document {_NS}><w:body>' + "".join(parts) + "</w:body></w:document>"
    fn_xml = f'<w:footnotes {_NS}>' + "".join(
        f'<w:footnote w:id="{i}">{_p(t)}</w:footnote>'
        for i, t in enumerate(footnotes, 1)) + "</w:footnotes>"
    num_xml = (f'<w:numbering {_NS}><w:abstractNum w:abstractNumId="1">'
               '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
               '<w:lvlText w:val="[%1]"/></w:lvl></w:abstractNum>'
               '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num></w:numbering>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/footnotes.xml", fn_xml)
        if refs:
            z.writestr("word/numbering.xml", num_xml)
    return path


_TMP = tempfile.mkdtemp(prefix="b199-test-")
_docx = _make_docx(
    os.path.join(_TMP, "b199.docx"),
    [
        # 1) 假 mixed：参阅 + 完整条目 → 应 cite 已有键 Salmon1966（不再新建条目）
        "参阅 W. C. Salmon, The Foundations of Scientific Inference, "
        "Pittsburgh: University of Pittsburgh Press, 1966.",
        # 2) 真 mixed：说明 + Hacking 条目 → 拆出 bib 条目 + 注内 parencite
        "艾金关于无关型类别和互动型类别的命名引用自哈金，参见Hacking, Ian. (1999) "
        "The Social Construction of What? Cambridge: Harvard University Press.",
        # 3) note_only 探测：无年份中文条目 → cite 已有键 Tuo2012
        "托马斯•库恩：《科学革命的结构》，第66页。",
        # 4) 纯说明（引者注：前注）→ 原样保留脚注，不提升
        "这里不再展开，参见前注。",
        # 5) 中文散文注（书名号前有「在…指出」）→ 必须保留脚注，不得按清单键提升
        "库恩在《科学革命的结构》中指出范式不可通约。",
    ],
    [
        "W. C. Salmon, The Foundations of Scientific Inference, Pittsburgh: "
        "University of Pittsburgh Press, 1966.",
        "徐强：《贝叶斯方法的基本原理及其哲学意蕴》，《哲学分析》2026年第1期。",
        "托马斯•库恩：《科学革命的结构》（第四版），金吾伦、胡新和 译，"
        "北京：北京大学出版社，2012年。",
    ],
)
_doc = parse_docx(_docx, c_class_union=True)
_body = _doc.body
_keys = [k for k, _e, _r in _doc.bib_entries]

check("⑦ 假 mixed：正文出现 \\parencite{Salmon1966}",
      "\\parencite{Salmon1966}" in _body, _body[_body.find("Salmon") - 40:_body.find("Salmon") + 40])
check("⑦ 假 mixed：不新建重复条目（Salmon 作条目仅 1 条）",
      sum(1 for k, e, _r in _doc.bib_entries
          if "salmon" in str(e.get("author", "")).lower()) == 1,
      [(k, e.get("author")) for k, e, _r in _doc.bib_entries])
check("⑦ 真 mixed：bib 新增 Hacking1999（题名干净）",
      "Hacking1999" in _keys
      and any(str(e.get("title", "")) == "The Social Construction of What?"
              for k, e, _r in _doc.bib_entries if k == "Hacking1999"),
      _keys)
check("⑦ 真 mixed：脚注保留且注内注入 \\parencite{Hacking1999}",
      "\\footnote{" in _body and "\\parencite{Hacking1999}" in _body
      and "\\footnote{艾金关于" in _body.replace("\\footnote{ ", "\\footnote{"),
      _body[:200])
check("⑦ note_only 探测：正文出现 \\parencite{Tuo2012}",
      "\\parencite{Tuo2012}" in _body, _body[_body.find("Tuo") - 40:_body.find("Tuo") + 40])
check("⑦ 负例：纯说明脚注保持 \\footnote 原文、未生造引用",
      "这里不再展开，参见前注。" in _body
      and "parencite{前注" not in _body,
      _body[-160:])
check("⑦ 负例：中文散文注（库恩在《…》中指出…）保留脚注、不按清单键删除",
      "库恩在《科学革命的结构》中指出范式不可通约。" in _body,
      _body[-220:])
check("⑦ 混合型清单归零（4 条脚注全部有去处）",
      list(_doc.mixed_footnotes) == [], list(_doc.mixed_footnotes))

# 无文末清单：mixed 拆分失败回退 mixed_manual 时，必须保留整条脚注原文
_docx2 = _make_docx(
    os.path.join(_TMP, "b199-norefs.docx"),
    ["沃森认为自己的观点与柏拉图对灵魂的划分异曲同工。Gary Watson, "
     "“Free Action and Free Will,” p. 150."],
    [],
)
_doc2 = parse_docx(_docx2, c_class_union=True)
check("⑦ 负例：无文末清单时 mixed 回退保留 note_before（说明文字不丢）",
      "沃森认为自己的观点与柏拉图对灵魂的划分异曲同工。" in _doc2.body,
      _doc2.body[-220:])

# ══ ⑧ B-233：Word 文末手写 `\bibitem` → 规范 bib 条目（合成 docx） ═════════
print("── ⑧ B-233：Word 文末 \\bibitem 自动转换 ──")


def _make_bibitem_docx(path: str, lines: list[str], body_line: str) -> str:
    parts = [_p("测试题名：bibitem 合成稿"), _p("张三"),
             _p("摘要：这是测试用摘要，只用于合成文档解析。"),
             _p("关键词：文献；转换"), _p(body_line), _p("参考文献")]
    parts.extend(_p(t) for t in lines)
    doc_xml = f'<w:document {_NS}><w:body>' + "".join(parts) + "</w:body></w:document>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc_xml)
    return path


_BIB_ITEMS = [
    r"\bibitem{a1} Smith J. The Synthetic Title. In: Jones M (ed.) Synthetic LNCS, "
    r"vol. 1234, pp. 10-20. Springer, Berlin (2001)",
    r"\bibitem{a2} Doe A, Roe B. Another Synthetic Paper. Journal of Tests, "
    r"vol. 3, no. 2, pp. 1-9 (2002)",
    r"\bibitem{a3} Roe B. Synthetic Dissertation. PhD thesis, Test University (2003)",
]
_doc3 = parse_docx(_make_bibitem_docx(
    os.path.join(_TMP, "b233-bibitem.docx"), _BIB_ITEMS,
    "正文第一段，含正文编号引用（[1]）、（[2]）与（[3]）。"))
_keys3 = [k for k, _e, _r in _doc3.bib_entries]
check("⑧ 单 docx 文末 3 条 bibitem → bib_entries 3 条（源键保留）",
      len(_doc3.bib_entries) == 3 and _keys3 == ["a1", "a2", "a3"], f"{_keys3}")
check("⑧ 条目顺序=文献序号：ref_key_by_num={1:a1,2:a2,3:a3}",
      _doc3.ref_key_by_num == {1: "a1", 2: "a2", 3: "a3"},
      str(_doc3.ref_key_by_num))
_body3, _n_num, _num_issues = link_numbered_citations(
    _doc3.body, _doc3.ref_key_by_num, "zh")
check("⑧ 正文 （[1]）（[2]）（[3]）→ \\parencite{a1/a2/a3}（走既有映射链，不猜）",
      _n_num == 3 and all(f"\\parencite{{a{i}}}" in _body3 for i in (1, 2, 3)),
      f"n={_n_num} issues={_num_issues} body={_body3[:120]}")
check("⑧ 旧人年制通道无条目（不产生 ref… 垃圾键）",
      list(_doc3.refs) == [], [e.key for e in _doc3.refs])
check("⑧ doc.warnings 含「由 \\bibitem 自动转换」提示（交接单④自动收录）",
      any("由 \\bibitem 自动转换" in w for w in _doc3.warnings), str(_doc3.warnings))
check("⑧ ref_raw_by_key 收录源稿原文行",
      str(_doc3.ref_raw_by_key.get("a1", "")).startswith("\\bibitem{a1}"),
      str(_doc3.ref_raw_by_key.get("a1", ""))[:60])

# 放宽（Lead 2026-10-25 裁决）：人年制宽松解析器会把 `\bibitem … (2001). …` 行误吃
# 成条目；只有**全部** raw 都以 `\bibitem` 开头时才视为未解析 → 仍转换、丢弃垃圾条目。
_MIS_BIB = [
    r"\bibitem{b1} Smith, J. (2001). Synthetic Title Two. Synthetic Press, 10-20.",
    r"\bibitem{b2} Doe, A. (2002). Another Synthetic Paper. Journal of Tests 3(2), 1-9.",
]
_doc4 = parse_docx(_make_bibitem_docx(
    os.path.join(_TMP, "b233-bibitem-misparsed.docx"), _MIS_BIB,
    "正文第二段，引用 [1] 与 [2]。"))
check("⑧ 旧通道误吃（refs 非空但全为 \\bibitem 行）→ 仍转换且垃圾 refs 丢弃",
      len(_doc4.bib_entries) == 2 and list(_doc4.refs) == []
      and [k for k, _e, _r in _doc4.bib_entries] == ["b1", "b2"],
      f"keys={[k for k, _e, _r in _doc4.bib_entries]} refs={[e.key for e in _doc4.refs]}")

# 负例（Lead 裁决的严格边界）：refs 里只要有一条不是从 `\bibitem` 行来的 → 不触发
_MIXED = [r"\bibitem{c1} Smith J. A Synthetic Title. Springer (2004)",
          "李雷. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50."]
_doc5 = parse_docx(_make_bibitem_docx(
    os.path.join(_TMP, "b233-bibitem-mixed.docx"), _MIXED, "正文第三段。"))
check("⑧ 负例：refs 含非 \\bibitem 原文行 → 不触发转换（不猜）",
      not _doc5.bib_entries and bool(_doc5.refs),
      f"entries={len(_doc5.bib_entries)} refs={[e.key for e in _doc5.refs]}")

# 开关：模块 enabled()=False（`--no-bibitem-convert` 的接线口径）→ 回旧行为
from slcn import bibitem_convert as _BB  # noqa: E402

try:
    _BB.set_enabled(False)
    _doc6 = parse_docx(_make_bibitem_docx(
        os.path.join(_TMP, "b233-bibitem-off.docx"), _BIB_ITEMS, "正文第四段。"))
finally:
    _BB.set_enabled(True)
check("⑧ 开关关闭 → 不转换（旧行为：0 条、无 warning）",
      not _doc6.bib_entries and not _doc6.ref_key_by_num
      and not any("由 \\bibitem 自动转换" in w for w in _doc6.warnings),
      f"entries={len(_doc6.bib_entries)} warns={_doc6.warnings}")

# ══ ⑨ B-233：多文件 refs 附件（bibitem 文本）→ ⑦ 转出条目 ══════════════════
print("── ⑨ B-233：多文件 refs 附件（\\bibitem 文本）──")
_attach = os.path.join(_TMP, "refs-bibitems.txt")
with open(_attach, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(_BIB_ITEMS) + "\n")


_res9 = run_refs_attach(SimpleNamespace(refs=_attach, bib=""),
                        SimpleNamespace(bib_path=None, warnings=[]),
                        source=_docx, out_dir=_TMP, stem="b233-attach",
                        refs_auto=False)
_bib9 = open(_res9.bib_path, encoding="utf-8").read() if _res9.bib_path else ""
check("⑨ 多文件 bibitem 附件 → ⑦ 写出 reference.bib 3 条（键保留 a1/a2/a3）",
      _res9.bib_path is not None and _bib9.count("\n@") + _bib9.startswith("@") == 3
      and all(f"{{{k}," in _bib9 for k in ("a1", "a2", "a3")),
      f"bib_path={_res9.bib_path} n@={_bib9.count('@')}")
check("⑨ 附件转换 warning「引用键需人工对齐」进 res.warnings（交接单）",
      any("引用键需人工对齐" in w for w in _res9.warnings), str(_res9.warnings))
check("⑨ 转换条目过结构校验、无 \\bibitem 残留",
      "\\bibitem{" not in _bib9 and "@phdthesis{a3" in _bib9, _bib9[:400])

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
