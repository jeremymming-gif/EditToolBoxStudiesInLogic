#!/usr/bin/env python3.14
r"""批68 B（B-022）：Word 自动编号清单物化 + 正文 `[n]` 安全映射。

根因（90-04 / 90-08 / 86-02 / 86-08 四例，批67 用户截图纠正后查 XML 确认）：
文末参考文献用 **Word 自动编号**（段落 `w:numPr` + `word/numbering.xml` 的
`lvlText="[%1]"`、`numFmt=decimal`）——显示出来的 `[1][2]…` **不在段落文字里**。
引擎只读段落文字 → 清单被当成「无编号人年制清单」→ 正文 `[n]` 不映射 → 0 `\cite`
→ PDF 参考文献区空白。批68 在 `slcn/docxsource.py` 里按 Word 计数器把序号**物化**成
`[N] ` 前缀，交给既有 `_numbered_refs` / `ref_key_by_num` / `link_numbered_citations`。

钉住（合成 docx，内存/临时目录；不跑 xelatex、不读稿件库）：
  ① `decimal` + `lvlText="[%1]"` 的 15 条清单 → 物化 `[1]…[15]`、进 bib、编号→键齐全；
     `lvlText` 带 Word 常见尾随空格（`"[%1] "`）同样接受；
  ② 正文 `[n]` 映射：每个 n 命中对应条目（`link_numbered_citations`），越界编号不猜；
  ③ 安全闸反例（**不物化、不猜 + warning**）：编号续号不连续 / 正文 max([n]) 超条数 /
     编号格式不在白名单（字母序）/ 多级列表；
  ④ 默认零变化：无编号普通清单路径不物化、正文/refs 不变；正文用人年制（无 `[n]`）的
     自动编号稿也不物化（保持旧路径，产物不变）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" \
      /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/test_batch68_word_numbering.py
"""

import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import (  # noqa: E402
    _auto_numbered_ref_items,
    link_numbered_citations,
    parse_docx,
)

# 批106（B-146 波2-2）dogfood：内联 _make_docx 改为共享夹具生成器（支持 numPr
# 编号段落 + 自定义 numbering.xml 原样写入）。其余断言一字未动。
_TOOLS = str(_ENGINE.parent / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from docx_fixture_gen import make_docx as _make_fixture_docx  # noqa: E402

FAILS: list[str] = []
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


# ── 合成 docx ────────────────────────────────────────────────────────────────

def _p(text: str, num: tuple[str, int] | None = None) -> str:
    ppr = ""
    if num is not None:
        nid, ilvl = num
        ppr = (f'<w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{nid}"/>'
               f'</w:numPr></w:pPr>')
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _abstract(num_abs: str, fmt: str, lvl_text: str, start: int = 1, levels: int = 1) -> str:
    lvls = []
    for il in range(levels):
        if il == 0:
            lvls.append(f'<w:lvl w:ilvl="0"><w:start w:val="{start}"/>'
                        f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{lvl_text}"/></w:lvl>')
        else:
            lvls.append(f'<w:lvl w:ilvl="{il}"><w:start w:val="1"/>'
                        f'<w:numFmt w:val="decimal"/><w:lvlText w:val="%{il + 1}"/></w:lvl>')
    return f'<w:abstractNum w:abstractNumId="{num_abs}">{"".join(lvls)}</w:abstractNum>'


def _numbering_xml(defs: list[tuple[str, str, str, int, int]], levels: int = 1) -> str:
    """defs: [(numId, abstractNumId, numFmt, lvlText, start)]；levels=1/2（多级反例用 2）。"""
    parts = []
    for num_id, abs_id, fmt, lvl_text, start in defs:
        parts.append(_abstract(abs_id, fmt, lvl_text, start, levels=levels))
        parts.append(f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abs_id}"/></w:num>')
    return f'<w:numbering {_NS}>{"".join(parts)}</w:numbering>'


def _refs15() -> list[str]:
    out = []
    for i in range(1, 16):
        out.append(f'A. Author{i}, {2000 + i}, “Test Title {i}”, Journal of Testing, '
                   f'{i}(1): {i}-{i + 9}.')
    return out


def _make_docx(path: Path, refs: list[str], body: list[str],
               numbering: str = "", ref_num: tuple[str, int] | None = ("1", 0),
               extra_body_pre: list[str] | None = None,
               pre_numbered: bool = False) -> Path:
    """合成一份 Word 稿：题名/作者/摘要/关键词 + 正文段 + 参考文献标题 + 清单。

    批106（B-146 波2-2）dogfood：段落模型交给共享夹具生成器
    `tools/docx_fixture_gen.py`（题名/作者/摘要/关键词 + 正文 + 参考文献 + 清单），
    编号条目 = `(text, numId, ilvl)` 元组；`numbering` 非空时作为自定义
    `word/numbering.xml` 原样写入。原 inline 实现手拼 document.xml，
    本文件既有断言口径不变。
    """
    paras: list[object] = ["测试自动编号参考文献", "张三",
                           "摘要：这是测试用摘要。", "关键词：测试；自动编号"]
    if ref_num is None:
        paras.extend(extra_body_pre or [])
    else:
        paras.extend((t, ref_num[0], ref_num[1]) if pre_numbered else t
                     for t in (extra_body_pre or []))
    paras.extend(body)
    paras.append("参考文献")
    if ref_num is None:
        paras.extend(refs)
    else:
        paras.extend((t, ref_num[0], ref_num[1]) for t in refs)
    _make_fixture_docx(str(path), paras=paras,
                       numbering_xml=(numbering or None))
    return path


def _auto_warnings(doc) -> list[str]:
    return [w for w in doc.warnings if "自动编号" in w or "未物化" in w]


# ── ① 15 条物化 + 进 bib（含 lvlText 尾随空格变体）────────────────────────────

NUM_OK = _numbering_xml([("1", "1", "decimal", "[%1]", 1)])
NUM_TRAIL = _numbering_xml([("1", "1", "decimal", "[%1] ", 1)])
BODY_N = ["本文引用见（[1]）与（[2]），另见（[15]）。"]
BODY_AY = ["本文引用见 Author1（2001），另有叙述性引用。"]


def _positive(td: Path, tag: str, numbering: str) -> None:
    p = _make_docx(td / f"{tag}.docx", _refs15(), BODY_N, numbering=numbering)
    items, bad, pre = _auto_numbered_ref_items(str(p))
    check(f"{tag}：自动编号条目 15 条、编号 1..15、无不可物化",
          len(items) == 15 and [lab for _t, lab in items] == [str(i) for i in range(1, 16)]
          and bad == [] and max(pre) == 15, f"items={len(items)} bad={bad[:1]} pre_max={max(pre) if pre else None}")
    doc = parse_docx(str(p))
    warns = _auto_warnings(doc)
    keys = [k for k, _e, _r in doc.bib_entries]
    check(f"{tag}：物化 15 条进 bib、ref_key_by_num 1..15 齐全",
          len(doc.refs) == 0 and len(keys) == 15 and len(doc.ref_key_by_num) == 15
          and sorted(doc.ref_key_by_num) == list(range(1, 16))
          and all(doc.ref_key_by_num[n] in keys for n in range(1, 16)),
          f"refs={len(doc.refs)} bib={len(keys)} map={len(doc.ref_key_by_num)}")
    check(f"{tag}：物化成功有 warning、原文 ref_raw_lines 不带合成编号",
          any("已物化 15 条" in w for w in warns)
          and len(doc.ref_raw_lines) == 15
          and not any(ln.startswith("[") for ln in doc.ref_raw_lines), repr(warns))
    check(f"{tag}：编号→原文映射保留整条 `[N] …` 原文",
          all(doc.ref_raw_by_key.get(doc.ref_key_by_num[n], "").startswith(f"[{n}] ")
              for n in (1, 8, 15)))
    mapped, n_map, issues = link_numbered_citations(doc.body, doc.ref_key_by_num, "zh")
    check(f"{tag}：正文 [1]/[2]/[15] → 对应条目的 \\parencite",
          n_map == 3 and issues == []
          and f"\\parencite{{{doc.ref_key_by_num[1]}}}" in mapped
          and f"\\parencite{{{doc.ref_key_by_num[2]}}}" in mapped
          and f"\\parencite{{{doc.ref_key_by_num[15]}}}" in mapped, f"n={n_map} issues={issues}")


# ── 反例：不物化、不猜 + warning ─────────────────────────────────────────────

def _negative(td: Path, tag: str, numbering: str, body: list[str],
              ref_num: tuple[str, int] | None = ("1", 0),
              extra_body_pre: list[str] | None = None,
              pre_numbered: bool = False,
              expect: str = "", want_warn: bool = True) -> None:
    p = _make_docx(td / f"{tag}.docx", _refs15(), body, numbering=numbering,
                   ref_num=ref_num, extra_body_pre=extra_body_pre,
                   pre_numbered=pre_numbered)
    doc = parse_docx(str(p))
    warns = [w for w in _auto_warnings(doc) if "已物化" not in w]
    check(f"{tag}：不物化（ref_key_by_num 空）",
          len(doc.ref_key_by_num) == 0 and not any("已物化" in w for w in _auto_warnings(doc)),
          f"map={len(doc.ref_key_by_num)}")
    if want_warn:
        check(f"{tag}：有「不猜」warning（含「{expect}」）",
              bool(warns) and any(expect in w for w in warns), repr(warns))


with tempfile.TemporaryDirectory(prefix="slcn_b68_") as _td:
    td = Path(_td)

    print("── ① 15 条物化 + 正文映射（decimal / `[%1]`）──")
    _positive(td, "pos", NUM_OK)
    print("── ①b lvlText 尾随空格（Word 常见 `\"[%1] \"`）也接受 ──")
    _positive(td, "pos_trail", NUM_TRAIL)

    print("── ② 正文映射的安全闸（越界编号不猜，原样保留 + issue）──")
    _mapped, _n, _issues = link_numbered_citations("见（[99]）", {1: "K1"}, "zh")
    check("② 单个越界 [99] → 不映射、原文保留、有 issue",
          _n == 0 and "（[99]）" in _mapped and bool(_issues), repr(_issues))

    print("── ③ 反例：续号不连续（同 numId 的列表在正文先用了 2 条）──")
    # 正文前置的甲/乙也带同一 numId 编号 → Word 计数器把清单续到 3 起
    _negative(td, "neg_seq", NUM_OK, BODY_N, extra_body_pre=["甲", "乙"],
              pre_numbered=True, expect="对不上")

    print("── ③ 反例：正文 max([n]) 超清单条数 ──")
    _negative(td, "neg_max", NUM_OK, ["本文引用见（[16]）与（[1]）。"], expect="不闭合")

    print("── ③ 反例：编号格式不在白名单（lowerLetter）──")
    NUM_LETTER = _numbering_xml([("1", "1", "lowerLetter", "%1)", 1)])
    _negative(td, "neg_fmt", NUM_LETTER, BODY_N, expect="不在白名单")
    _doc_fmt = parse_docx(str(td / "neg_fmt.docx"))
    check("③ 格式反例：默认人年制路径不变（refs 15 条、bib 无编号清单条目）",
          len(_doc_fmt.refs) == 15 and _doc_fmt.bib_entries == []
          and len(_doc_fmt.ref_key_by_num) == 0)

    print("── ③ 反例：多级列表（ilvl=1）──")
    NUM_MULTI = _numbering_xml([("1", "1", "decimal", "[%1]", 1)], levels=2)
    _negative(td, "neg_multi", NUM_MULTI, BODY_N, ref_num=("1", 1),
              expect="多级编号")

    print("── ④ 默认零变化：作者-年制正文（无 [n]）的自动编号稿不物化 ──")
    p_ay = _make_docx(td / "ay.docx", _refs15(), BODY_AY, numbering=NUM_OK)
    d_ay = parse_docx(str(p_ay))
    check("④ 正文无 [n] → 不物化、无自动编号 warning、人年制通道照旧",
          len(d_ay.ref_key_by_num) == 0 and _auto_warnings(d_ay) == []
          and len(d_ay.refs) == 15 and d_ay.bib_entries == [])

    print("── ④ 普通无编号清单：不物化、无 warning、路径逐字节同旧口径 ──")
    p_plain = _make_docx(td / "plain.docx", _refs15(), BODY_N, numbering="",
                         ref_num=None)
    d_plain = parse_docx(str(p_plain))
    items_plain, bad_plain, pre_plain = _auto_numbered_ref_items(str(p_plain))
    check("④ 普通清单：helper 快路径返回空（无 numbering.xml/numPr）",
          items_plain == [] and bad_plain == [] and pre_plain == []
          and _auto_warnings(d_plain) == [])
    check("④ 普通清单：15 条人年制 refs 照旧、bib_entries 空、无编号映射",
          len(d_plain.refs) == 15 and d_plain.bib_entries == []
          and len(d_plain.ref_key_by_num) == 0)
    from slcn import docxsource as _ds
    _orig = _ds._materialize_auto_numbered_refs
    _ds._materialize_auto_numbered_refs = lambda path, section, docxml="": (section, "", 0)
    try:
        d_pos_off = parse_docx(str(td / "pos.docx"))
    finally:
        _ds._materialize_auto_numbered_refs = _orig
    d_pos_on = parse_docx(str(td / "pos.docx"))
    check("④ 物化只改 bib/编号映射，不改正文渲染与原文留档（ON/OFF body/ref_raw 逐字节相同）",
          d_pos_off.body == d_pos_on.body
          and d_pos_off.ref_raw_lines == d_pos_on.ref_raw_lines
          and "\\parencite" not in d_pos_on.body)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
