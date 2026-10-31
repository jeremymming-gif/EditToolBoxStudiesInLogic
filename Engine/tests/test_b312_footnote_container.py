"""B-312 修法 A：脚注提升通道补容器字段（B-313A 单测）。

覆盖：
  ① 中文「编著章节」C 形态（脚注独引、中文主编）→ booktitle/editor 补齐、note 不重复；
  ② 英文 A 形态（编者列表在前）/ ③ 英文 B 形态（eds. 起头）/ ④ 英文 C 形态（无编者标记）；
  ⑤ 无容器证据 → 不补（不猜）；
  ⑥ 负例：散文注 / 古典文献 / 引者注 → 不提升；
  ⑦ 门控：`c_class_union=False` 时整块不执行（无容器字段、无 B-312 warning）——
     与 `--no-c-class-union` 逐字节不变契约对应；
  ⑧ 编号清单通道（`[1]` + 正文编号引用证据）→ 装配阶段归位碎片字段（repair ② 不再重解析）。

全部 tempfile 合成 docx，不联网、不读稿件库、不写仓库。
用法：cd Engine && PYTHONPATH=.:vendor py -3.14 -X utf8 tests/test_b312_footnote_container.py
"""

import json
import os
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.wordbody import parse_docx  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


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


def _make_docx(path: str, footnotes: list[str], refs: list[str] | None = None,
               body_extra: str = "") -> str:
    parts = [_p("测试题名：脚注容器字段"), _p("张三"),
             _p("摘要：这是测试用摘要，只用于合成文档解析。"),
             _p("关键词：脚注；容器；提升"),
             _p("正文第一段。", fnrefs=tuple(range(1, len(footnotes) + 1)))]
    if body_extra:
        parts.append(_p(body_extra))
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


def _entries(doc) -> dict[str, dict]:
    return {key: ent for key, ent, _raw in doc.bib_entries if isinstance(ent, dict)}


def _only(entries: dict[str, dict]) -> dict:
    assert len(entries) == 1, entries
    return next(iter(entries.values()))


_TMP = tempfile.mkdtemp(prefix="b312-test-")
_CN_C = ("赖兴巴赫：《概率概念的逻辑基础》，洪谦（主编）：《逻辑经验主义》上卷，"
         "北京：商务印书馆，1982年，第400页。")
_EN_A = ("R. Carnap, \"The Methodological Character of Theoretical Concepts\", "
         "in H. Feigl and M. Scriven (eds.), Minnesota Studies in the Philosophy "
         "of Science, Minneapolis: University of Minnesota Press, 1956, pp. 38-76.")
_EN_B = ("B. de Finetti, \"Foresight: Its Logical Laws, Its Subjective Sources\", "
         "in H. E. Kyburg and H. E. Smokler (eds.), Studies in Subjective "
         "Probability, New York: Wiley, 1964, pp. 93-158.")
_EN_C = ("W. C. Salmon, \"The Foundations of Scientific Inference\", "
         "in Some Book, 1967, pp. 1-20.")

# ① 中文 C 形态（脚注独引，无文末清单）
print("── ① 中文编著章节 C 形态（脚注独引 + 中文主编）──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "cn_c.docx"), [_CN_C]),
                  c_class_union=True)
_e = _only(_entries(_doc))
check("① 条目升为 incollection 且有 booktitle/editor",
      str(_e.get("_type")) == "incollection"
      and "逻辑经验主义（上卷）" in str(_e.get("booktitle", ""))
      and str(_e.get("editor", "")) == "洪谦",
      f"entry={_e}")
check("① author/title 归位",
      str(_e.get("author", "")) == "赖兴巴赫"
      and str(_e.get("title", "")) == "概率概念的逻辑基础",
      f"entry={_e}")
check("① note 不重复（无「（主编）（主编）」/不再保留整段原文）",
      "（主编）（主编）" not in json.dumps(_e, ensure_ascii=False)
      and "洪谦（主编）" not in str(_e.get("note", "")),
      f"note={_e.get('note')!r}")
check("① 不猜缺失字段（无 pages 证据 → 不写 pages）",
      "pages" not in _e, f"entry={_e}")

# ② 英文 A 形态：编者列表在前，`(eds.)` 后接书名
print("── ② 英文 A 形态（编者列表在前）──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "en_a.docx"), [_EN_A]),
                  c_class_union=True)
_e = _only(_entries(_doc))
check("② booktitle/editor 补齐",
      "Minnesota Studies in the Philosophy of Science" in str(_e.get("booktitle", ""))
      and str(_e.get("editor", "")) == "H. Feigl and M. Scriven",
      f"entry={_e}")

# ③ 英文 B 形态：`in 编者 (eds.), 书名…`（英文 in-editor 容器）
print("── ③ 英文 B 形态（eds. 起头的编者容器）──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "en_b.docx"), [_EN_B]),
                  c_class_union=True)
_e = _only(_entries(_doc))
check("③ booktitle/editor 补齐",
      "Studies in Subjective Probability" in str(_e.get("booktitle", ""))
      and str(_e.get("editor", "")) == "H. E. Kyburg and H. E. Smokler",
      f"entry={_e}")

# ④ 英文 C 形态：无编者标记，只有书名 + 出版信息
print("── ④ 英文 C 形态（无编者标记）──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "en_c.docx"), [_EN_C]),
                  c_class_union=True)
_e = _only(_entries(_doc))
check("④ booktitle 补齐（无编者证据 → 不写 editor）",
      "Some Book" in str(_e.get("booktitle", ""))
      and not str(_e.get("editor", "")),
      f"entry={_e}")

# ⑤ 无容器证据 → 不补（中文专著：没有「编者（主编）：《书名》」这条路）
print("── ⑤ 无证据不补（中文专著）──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "cn_book.docx"),
                             ["王浩：《逻辑与哲学》，北京：人民出版社，2000年。"]),
                  c_class_union=True)
_e = _only(_entries(_doc))
check("⑤ 不补 booktitle/editor（无容器证据）",
      not str(_e.get("booktitle", "")) and not str(_e.get("editor", "")),
      f"entry={_e}")

# ⑥ 负例：散文注 / 古典文献 / 引者注 → 不提升为条目
print("── ⑥ 负例：散文注 / 古典文献 / 引者注 ──")
_doc = parse_docx(_make_docx(
    os.path.join(_TMP, "neg.docx"),
    ["笔者以为，此处应作更严格的区分。",
     "Ockham, Summa Logicae, I, c. 1.",
     "笔者在另一篇论文中已经处理过这个问题，参见《科学革命的结构》。"]),
    c_class_union=True)
check("⑥ 三条负例都不产生 bib 条目", not _entries(_doc),
      f"entries={_entries(_doc)}")

# ⑦ 门控：c_class_union=False（= --no-c-class-union）时整块不执行
print("── ⑦ 门控：默认关口径整块不执行 ──")
_doc = parse_docx(_make_docx(os.path.join(_TMP, "cn_c_off.docx"), [_CN_C]),
                  c_class_union=False)
_e = _only(_entries(_doc))
check("⑦ 关闭时无 booktitle/editor（不执行 B-312 修法 A）",
      not str(_e.get("booktitle", "")) and not str(_e.get("editor", "")),
      f"entry={_e}")
check("⑦ 关闭时无 B-312 修法 A warning",
      not any("B-312 修法 A" in str(w) for w in (_doc.warnings or [])),
      f"warnings={_doc.warnings}")

# ⑧ 编号清单通道：`[1]` + 正文编号引用证据 → 装配阶段归位碎片字段
#    （合成稿里文末清单是 Word 自动编号；正文要有 ≥2 条 `[n]` 编号引用证据才会物化，
#      与 B-022 安全闸的口径一致）
print("── ⑧ 编号清单通道（[1] 中文编著章节）──")
_REF = ("赖兴巴赫：《概率概念的逻辑基础》，洪谦（主编）：《逻辑经验主义》上卷，"
        "北京：商务印书馆，1982年。")
_REF2 = "王浩：《逻辑与哲学》，北京：人民出版社，2000年。"
_doc = parse_docx(_make_docx(
    os.path.join(_TMP, "numbered.docx"), [],
    refs=[_REF, _REF2],
    body_extra="正文第二段，含编号引用（[1]）、（[2]）。"),
    c_class_union=True)
_entries_map = _entries(_doc)
_e = next(ent for ent in _entries_map.values()
          if "概率概念" in str(ent.get("title", "")))
check("⑧ 编号条目 title 不再带「》」、booktitle 含卷次、editor=洪谦",
      "》" not in str(_e.get("title", ""))
      and "逻辑经验主义（上卷）" in str(_e.get("booktitle", ""))
      and str(_e.get("editor", "")) == "洪谦",
      f"entry={_e}")
check("⑧ 编号条目 note 不重复", "（主编）（主编）" not in json.dumps(_e, ensure_ascii=False),
      f"note={_e.get('note')!r}")

print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
