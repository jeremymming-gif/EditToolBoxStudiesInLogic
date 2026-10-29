"""正文装配（B-207 从 docxsource 纯搬运）：Word 自动编号物化、定理环境辅助、parse_docx 主体。"""

from __future__ import annotations

import os
import re

from .wordcaption import caption_candidate as _caption_candidate
from .wordcaption import find_captions as _find_captions
from .wordcaption import is_figure_para as _is_figure_para
from .wordcite import CUE_LEAD_FAR_RE as _FN_LEAD_FAR_RE
from .wordcite import CUE_LEAD_NEAR_RE as _FN_LEAD_NEAR_RE
from .wordcite import fn_in_math as _fn_in_math
from .wordcite import fn_in_paren_depths as _fn_in_paren_depths
from .wordcore import (DocxDoc, _footnote_latex, _has_cjk, escape_latex)
from .wordentry import (_NUMBERED_PREFIX_RE, _citation_title_year, _dedupe_keys,
                        _entry_is_note_only, _looks_short_bibliographic_note, _numbered_refs,
                        _src_line_key, _src_line_keys_for_works, parse_reference_line)
from .wordcitebody import (_FN_BARE_NUM_RE, link_citations)
from .wordmeta import (AFFIL_EN_JOIN, META_SCAN, _ABS_RE, _HEAD1, _HEAD2, _HEAD_CN_ARABIC,
                       _HEAD_CN_DOT, _HEAD_CN_SPACE, _HEAD_EN1, _HEAD_EN2, _HEAD_EN2_HALF,
                       _HEAD_EN3, _KW_RE, _REF_HEADS, _abs_body_cn, _is_clc_meta_only,
                       _is_cn_dot_heading, _is_cn_numbered_heading, _is_subtitle,
                       _is_title_candidate, _known_cn_authors, _known_en_authors,
                       _label_body, _tail_abstract_cn, collect_affiliations, collect_emails,
                       head_author_after, head_title_index, locate_tail_block, parse_tail_metadata)
from .wordrender import (_looks_plain_en_title, classify_en_heading, find_char_diagrams,
                         render_para, table_env)

# ── B-022（批68）：Word 自动编号清单「物化」────────────────────────────────
#
# 根因（90-04 / 90-08 / 86-02 / 86-08 四例，批67 用户截图纠正后查 XML 确认）：
# 文末参考文献是 **Word 自动编号**（段落 `w:numPr` + `word/numbering.xml` 的
# `lvlText="[%1]"`、`numFmt=decimal`）——显示出来的 `[1][2]…` **不在段落文字里**。
# 引擎只读段落文字（`Para.plain`）→ 清单会被当成「无编号的人年制清单」→ 正文 `[n]`
# 不映射 → 0 条 `\cite` → PDF 参考文献区空白。
#
# 方案：解析文献区之前，按 Word 的计数规则把序号**物化**成 `[N] ` 前缀，交给既有的
# `_numbered_refs` 编号清单通道（案例 116 契约）；下游 `ref_key_by_num` →
# `link_numbered_citations` 的正文映射完全沿用，不另写一套映射。
#
# 安全闸（不满足 → **不物化、不猜**；只对「≥2 条且已对上清单」的才 warning）：
#   ① 只认 `numFmt=decimal`、`ilvl=0`、`lvlText` 为 `%1`/`[%1]`/`（%1）`/`[]%1` 类白名单
#      （多级/字母/罗马数字一律不猜）；
#   ② 编号必须从 **1 起连续**（Word 计数器跨列表续号 → 对不上 1..N → 不猜）；
#   ③ 文献区**每一条**带编号的段落都要对上原文（对不上 → 整份不物化，绝不部分编号）；
#   ④ **正文确实用 `[n]` 引用**（作者-年制的稿子物化了也用不上，保持默认零变化）。
# 物化后 parse_docx 末尾还有一道「正文 [n] 闭合」安全闸（条数/范围/键存在，见那里）。

#: 允许的 Word 编号格式（numFmt）。decimal 是本刊中文/英文文献清单的常见形态。
_AUTO_NUM_FMT_OK = frozenset({"decimal"})
#: 允许的 lvlText 形态：前缀 `[]`/`[`/`【`/`［`/`（`/`(` + `%1` + 后缀 `]`/`】`/`］`/`）`/`)`/`.`/`、`。
_AUTO_NUM_LVL_RE = re.compile(r"^(?:\[\]|\[|【|［|（|\()?%1(?:[\]】］）)]|\.|、)?$")
#: 正文里的纯文本编号引用（与后面的 `_NUM_GROUP` 同口径；这里只作「是否编号体例」证据）
_PRE_NUM_RE = re.compile(r"\[\s*(\d{1,3}(?:\s*[，,、]\s*\d{1,3})*)\s*\]")


def _xml_para_text(p) -> str:
    """段落里所有 `<w:t>` 的文字（与 `Para.plain` 同口径：不含公式/脚注标记）。"""
    texts = [t.text or "" for t in p.iter(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    return "".join(texts)


def _docx_numbering_defs(numbering_xml: bytes) -> dict[str, tuple[str, str, int]]:
    """读 `word/numbering.xml` → `{numId: (numFmt, lvlText, start)}`（只取 ilvl=0）。"""
    from lxml import etree

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def q(tag: str) -> str:
        return f"{{{W}}}{tag}"

    root = etree.fromstring(numbering_xml)
    abstract: dict[str, tuple[str, str, int]] = {}
    for an in root.iter(q("abstractNum")):
        aid = an.get(q("abstractNumId"))
        if aid is None:
            continue
        for lvl in an.findall(q("lvl")):
            if (lvl.get(q("ilvl")) or "0") != "0":
                continue
            fmt = lvl.find(q("numFmt"))
            txt = lvl.find(q("lvlText"))
            st = lvl.find(q("start"))
            start = 1
            if st is not None and (st.get(q("val")) or "").isdigit():
                start = int(st.get(q("val")))
            abstract[aid] = (
                (fmt.get(q("val")) or "") if fmt is not None else "",
                # Word 常把分隔空格写进 lvlText（`[%1] `、`%1 `，86-06/75-01 实测）
                ((txt.get(q("val")) or "").replace("\u00a0", " ").strip())
                if txt is not None else "",
                start,
            )
            break
    out: dict[str, tuple[str, str, int]] = {}
    for n in root.iter(q("num")):
        nid = n.get(q("numId"))
        a = n.find(q("abstractNumId"))
        if nid is None or a is None:
            continue
        defs = abstract.get(a.get(q("val")) or "", ("", "", 1))
        fmt, ltxt, start = defs
        # Word「重新开始编号」= 该 num 上带 lvlOverride/startOverride（案例仅出现于
        # 分节场景）；没有 override 时从 abstracts 的 start 起算。
        for lo in n.findall(q("lvlOverride")):
            if (lo.get(q("ilvl")) or "0") != "0":
                continue
            so = lo.find(q("startOverride"))
            if so is not None and (so.get(q("val")) or "").isdigit():
                start = int(so.get(q("val")))
        out[nid] = (fmt, ltxt, start)
    return out


def _auto_numbered_ref_items(
    path: str, docxml: str = "",
) -> tuple[list[tuple[str, str]], list[str], list[int]]:
    """取「第一个参考文献标题之后」的 Word 自动编号段落：`[(纯文字, 编号串)]`。

    返回 `(可物化条目, 可疑项说明, 标题之前的正文编号 [n])`。`可疑项` = 带编号但
    格式/层级不在白名单的段落（出现在文献区时整份不物化，见
    `_materialize_auto_numbered_refs`）；`标题之前的编号`是「正文是不是用 [n] 引用」
    的证据（作者-年制的稿子这里为空——那种稿子物化了也用不上，保持默认零变化）。
    编号按 Word 语义在**整篇文档**内按 numId 连续累计（标题之前的编号也推进计数器）。
    """
    if not docxml:
        try:
            import zipfile as _zipfile

            with _zipfile.ZipFile(path) as _z:
                docxml = _z.read("word/document.xml").decode("utf-8")
        except Exception:  # noqa: BLE001 —— docx 结构异常：不物化（不阻断出稿）
            return [], [], []
    if "numPr" not in docxml:
        return [], [], []      # 快路径：全文没有 Word 列表（绝大多数稿子）
    import zipfile

    from lxml import etree

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def q(tag: str) -> str:
        return f"{{{W}}}{tag}"

    try:
        with zipfile.ZipFile(path) as z:
            defs: dict[str, tuple[str, str, int]] = {}
            if "word/numbering.xml" in z.namelist():
                defs = _docx_numbering_defs(z.read("word/numbering.xml"))
    except Exception:  # noqa: BLE001 —— docx 结构异常：不物化（不阻断出稿）
        return [], [], []
    if not defs:
        return [], [], []
    try:
        doc = etree.fromstring(docxml.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return [], [], []
    body = doc.find(q("body"))
    if body is None:
        return [], [], []
    items: list[tuple[str, str]] = []
    bad: list[str] = []
    pre_nums: list[int] = []
    head_seen = False
    counters: dict[str, int] = {}
    for p in body:
        if p.tag != q("p"):
            continue
        text = _xml_para_text(p).strip()
        if not head_seen:
            # 标题之前的段落：收集正文 `[n]`（是否编号体例的证据）
            for grp in _PRE_NUM_RE.findall(text):
                pre_nums.extend(int(x) for x in re.split(r"[，,、]", grp) if x.strip())
        if not head_seen and text.lower().rstrip(":：") in _REF_HEADS:
            head_seen = True
            continue                    # 标题行本身不是条目
        pr = p.find(q("pPr"))
        num_pr = pr.find(q("numPr")) if pr is not None else None
        if num_pr is None:
            continue
        nid_el = num_pr.find(q("numId"))
        if nid_el is None:
            continue
        nid = nid_el.get(q("val")) or ""
        if nid not in defs:
            continue                    # numId=0 等「无列表」定义（86-08 实测 100 处）
        il_el = num_pr.find(q("ilvl"))
        ilvl = int(il_el.get(q("val")) or 0) if il_el is not None else 0
        if ilvl != 0:
            if head_seen:
                bad.append(f"「{text[:30]}」为多级编号（ilvl={ilvl}）")
            continue
        fmt, ltxt, start = defs[nid]
        counters[nid] = counters[nid] + 1 if nid in counters else start
        if not head_seen:
            continue                    # 标题之前：只推进计数器（Word 连续累计）
        if fmt not in _AUTO_NUM_FMT_OK or not _AUTO_NUM_LVL_RE.match(ltxt or ""):
            bad.append(f"「{text[:30]}」编号格式 {fmt or '?'}/{ltxt or '?'} 不在白名单")
            continue
        if not text:
            bad.append("文献区里有一条**空段落**带自动编号（无法对应原文）")
            continue
        items.append((text, str(counters[nid])))
    return items, bad, pre_nums


def _materialize_auto_numbered_refs(
    path: str, section: list[str], docxml: str = "",
) -> tuple[list[str], str, int]:
    """把文献区里 Word 自动编号的序号物化成 `[N] ` 前缀。

    返回 `(解析用 section, warning|'', 物化条数)`。物化为 **0** 时原样返回
    （默认路径零变化：不含自动编号清单的稿子走不到这里）。
    """
    try:
        items, bad, pre_nums = _auto_numbered_ref_items(path, docxml)
    except Exception:  # noqa: BLE001 —— 不阻断出稿
        return section, "", 0
    if len(items) < 2:                  # 1 条也走不了编号清单通道（_numbered_refs 要求 ≥2 标记）
        if bad and len(pre_nums) >= 2:
            return section, (
                f"文末参考文献带 Word 自动编号，但 {len(bad)} 条编号格式/层级不在白名单"
                f"（{bad[0]}）→ 不猜清单序号，正文 [n] 保持原文，请人工核对"
            ), 0
        return section, "", 0
    if len(pre_nums) < 2:
        # 正文没有 `[n]` 编号引用（作者-年制稿）：物化了也用不上，保持默认路径零变化。
        return section, "", 0
    # 目标：把 items 按**文档顺序**对到 section 行（空段落已被过滤），要求编号 1..N 连续。
    matched: list[tuple[int, str]] = []
    cursor = 0
    for text, label in items:
        found = -1
        for j in range(cursor, len(section)):
            if section[j] == text:
                found = j
                break
        if found < 0:
            break
        if label != str(len(matched) + 1):   # 不是从 1 起连续 → 续号/错序，不猜
            break
        matched.append((found, label))
        cursor = found + 1
    if len(matched) < 2:
        return section, (
            "Word 自动编号清单与文献区对不上（编号不是从 1 起的连续序号 / 续号 / "
            "清单不是文献列表）→ 不猜清单序号，正文 [n] 保持原文，请人工核对"
        ), 0
    n = len(matched)
    pre_ok = min(pre_nums) >= 1 and max(pre_nums) == n
    if len(matched) != len(items) or bad or not pre_ok:
        why: list[str] = []
        if len(matched) != len(items):
            why.append(f"清单 {len(items)} 条只对上文献区 {len(matched)} 条")
        if bad:
            why.append(f"{len(bad)} 条编号不可物化（{bad[0]}）")
        if not pre_ok:
            why.append(f"正文编号最大 [{max(pre_nums)}] 与清单 {n} 条不闭合")
        return section, (
            "Word 自动编号清单未物化（不猜，正文 [n] 保持原文）：" + "；".join(why)
            + "；请人工核对文末清单编号与正文引用"
        ), 0
    out = list(section)
    for j, label in matched:
        out[j] = f"[{label}] {out[j]}"
    return out, (
        f"文末参考文献为 Word 自动编号：已物化 {n} 条序号"
        f"（[1]…[{n}]），正文 [n] 按该清单映射"
    ), n


def _footnote_num2key_gate(doc: "DocxDoc") -> dict[int, str]:
    r"""B-130 gate：`doc.ref_key_by_num` 是**可靠 1..N 映射**时才返回它，否则 `{}`。

    可靠 = 二者之一（spec §2.2，Lead 2026-10-21 裁决 B）：
      ① Word 自动编号安全闸（B-022）**成功物化**（`doc.ref_auto_materialized > 0`
         —— 编号从 1 起连续、每一条都对上原文、正文 [n] 闭合，见
         `_materialize_auto_numbered_refs` 的白名单/安全闸注释）；
      ② 文末**显式写着** `[N]` 的清单文本解析一致，且 `num2key` 覆盖 1..N 连续
         （`_numbered_refs` 的编号取自清单原文标号本身，与正文 `（[N]）` 同一套映射）。
    两者都不满足（映射为空 / 编号不齐）→ **不猜**，脚注裸编号保持原文
    （「像引用」的裸编号由 `_footnote_bare_cite_gate_warning` 汇总成一条 warning）。
    """
    m = doc.ref_key_by_num
    if not m:
        return {}
    if doc.ref_auto_materialized > 0:
        return m
    if sorted(m) == list(range(1, len(m) + 1)):
        return m
    return {}


def _footnote_bare_cite_gate_warning(fn_objs: dict) -> str:
    """B-130：脚注里有「像引用」的裸编号、但文末清单没有可靠 1..N 映射 → 一条汇总 warning。

    只在 gate 不通过时调用（spec §2.2「两者都无 → 不猜、warning」）。判据与正转换
    通道同源：只算**括号外 + 带引用引导语证据**的裸编号，最多列 4 处；没有就返回空串
    （无此类脚注的稿子不新增 warning，默认零变化）。
    """
    hits: list[str] = []
    for fid in sorted(fn_objs):
        txt = str(getattr(fn_objs[fid], "text", "") or "")
        if not txt:
            continue
        depths = _fn_in_paren_depths(txt)
        in_math = _fn_in_math(txt)
        for m in _FN_BARE_NUM_RE.finditer(txt):
            s = m.start()
            if depths[s] > 0 or in_math[s]:
                continue
            pre = txt[max(0, s - 32):s]
            if not (_FN_LEAD_NEAR_RE.search(pre) or _FN_LEAD_FAR_RE.search(pre)):
                continue
            snip = txt[max(0, s - 10):min(len(txt), m.end() + 10)].replace("\n", " ")
            hits.append(f"「…{snip}…」")
            if len(hits) >= 4:
                break
        if len(hits) >= 4:
            break
    if not hits:
        return ""
    return ("脚注里有裸编号引用 " + "、".join(hits) + ("（仅列前 4 处）" if len(hits) >= 4 else "")
            + "，但文末清单没有形成可靠 1..N 编号映射（Word 自动编号未物化 / 显式清单"
            "编号不齐）→ 未转换（不猜），请人工核对")


# ── B-219⑤：段首定理类标签 → 定理环境（最简映射；「注」不环境化）─────────────
#
# 判据（spec §2⑤，段首、整段为一条）：标签 ∈ {定理,引理,定义,命题,推论,公理,例}
# **紧跟编号**（阿拉伯 1–2 位或 一…十），编号后允许分隔 `[：:．.、]` 或空白；
# 或 {证明} 紧跟 `[.．：:]`（无编号）。不命中（「定义一个…」「证明 F-UD：」）→ 纯文本。
# 编号由模板 sty 自动生成，这里只 strip 标签+作者编号，内容仍走 render_para
# （行内公式/脚注/引用等既有路径全部复用）。
_THEOREM_ENV = {
    "定理": "theorem", "引理": "lemma", "定义": "definition",
    "命题": "proposition", "推论": "corollary", "公理": "axiom", "例": "example",
}
_NUM_CHARS = r"(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})"
_THEOREM_HEAD_RE = re.compile(
    r"(?P<label>定理|引理|定义|命题|推论|公理|例)\s*" + _NUM_CHARS
    + r"\s*(?:[：:．.、]\s*|\s+)")
_PROOF_HEAD_RE = re.compile(r"证明\s*[.．：:]\s*")
_REMARK_HEAD_RE = re.compile(r"注\s*" + _NUM_CHARS + r"?\s*(?:[：:．.、]|\s+)")

#: B-219②：正文里的「式（N）/公式（N）」引用（全/半角括号；「（式（N））」也命中内层）
_EQ_REF_RE = re.compile(r"(?:式|公式)\s*[（(]\s*(\d{1,3})\s*[）)]")


def _drop_text_prefix(para, n_chars: int):
    """返回「去掉前 n 个纯文字字符」的 Para 副本（跨 run 也成立；不改原对象）。"""
    from .docxxml import Para, Seg

    segs = []
    left = n_chars
    for sg in para.segs:
        if left > 0 and sg.kind == "text":
            if len(sg.text) <= left:
                left -= len(sg.text)
                continue
            segs.append(Seg(sg.kind, sg.text[left:], italic=sg.italic,
                            bold=sg.bold, fid=sg.fid, rid=sg.rid,
                            display=sg.display, w_pt=sg.w_pt, h_pt=sg.h_pt))
            left = 0
        else:
            segs.append(sg)
    return Para(segs=segs, style=para.style)


def _theorem_env_of(para):
    """段首定理标签 → (环境名, 去掉标签后的 Para)；不命中 → None。"""
    text = para.plain
    lead = len(text) - len(text.lstrip())
    m = _THEOREM_HEAD_RE.match(text, lead)
    if not m:
        return None
    rest = _drop_text_prefix(para, m.end())
    if not rest.plain.strip() and not any(
            s.kind in ("math", "image") for s in rest.segs):
        return None                     # 只剩「定理1：」的空段：不环境化，保持原样
    return _THEOREM_ENV[m.group("label")], rest


# B-219 修（verifier FAIL 1）：amsthm 的 proof 环境会自动印 `\qedsymbol`（本刊 sty 为
# `\openbox`）；作者原稿段末常又写一个 □/`$\Box$` → 编译出两个框。这里**只去段末**的
# QED 符号（正文中间的 □ 不动），让 amsthm 只印一个。
_QED_TOKENS = ("□", "\\Box", "\\square", "\\boxempty", "\\qed")


def _strip_qed_tail(rest):
    """去掉证明段末尾的 QED 符号；无变化时原样返回同一个 Para。"""
    segs = list(rest.segs)
    changed = False
    while segs:
        sg = segs[-1]
        if sg.kind == "math" and sg.text.strip() in _QED_TOKENS:
            segs.pop(); changed = True; continue
        if sg.kind == "text":
            t = sg.text.rstrip()
            tok = next((x for x in _QED_TOKENS if t.endswith(x)), None)
            if tok is not None:
                sg.text = t[: -len(tok)].rstrip(); changed = True
                if not sg.text:
                    segs.pop()
                continue
        break
    if not changed:
        return rest
    from .docxxml import Para
    return Para(segs=segs, style=rest.style)


def _proof_env_of(para):
    """段首「证明.」→ ("proof", 去掉标签后的 Para)；不命中 → None（无编号证明环境）。"""
    text = para.plain
    lead = len(text) - len(text.lstrip())
    m = _PROOF_HEAD_RE.match(text, lead)
    if not m:
        return None
    rest = _drop_text_prefix(para, m.end())
    if not rest.plain.strip() and not any(
            s.kind in ("math", "image") for s in rest.segs):
        return None
    return "proof", _strip_qed_tail(rest)


def _remark_prefix(para) -> bool:
    """段首「注…」标签（不环境化，只 warning；模板无未编号 remark 环境）。"""
    text = para.plain
    lead = len(text) - len(text.lstrip())
    return bool(_REMARK_HEAD_RE.match(text, lead))


def parse_docx(path: str, quote_threshold: int = 105,
               c_class_union: bool = False,
               bibdb_store: object | None = None) -> DocxDoc:
    """解析 Word 文稿。

    bibdb_store   **B-220 N6**：引用库 Store（只读）；非 None 时，C 类短引在文末
                  清单配不上完整条目时再查一次库——仅 HIT_SAFE 命中才建条目并提升
                  （候选/未命中/多义一律不动）。None = 默认零变化（旧行为）。

    ⚠️ 用 slcn/docxxml 直接读 XML，**不用 python-docx 的 paragraph.text**。
    后者会静默丢掉 OMML 公式、run 级格式（斜体）和整个 footnotes.xml，
    实测让 Lu_Jiang 一篇丢掉 17.4% 内容和全部 72 处引用。
    """
    from .docxxml import Table, read_blocks, read_footnotes, segs_to_text
    from .footnotes import classify, make_key, parse_entry, split_numbered_bib_note

    doc = DocxDoc(path=path)
    blocks = read_blocks(path)
    paras = [b for b in blocks if not isinstance(b, Table)]
    n_tables = sum(1 for b in blocks if isinstance(b, Table))
    if not paras:
        doc.warnings.append("文档没有可读段落")
        return doc

    with __import__("zipfile").ZipFile(path) as z:
        _docxml = z.read("word/document.xml").decode("utf-8")
        doc.n_math = len(re.findall(r"<m:oMath(?![A-Za-z])[^>]*>", _docxml))
        # 期 87 子案例 2（03 Wenjing_Du）实测：作者用 MathType 写公式，docx 里是
        # `<w:object>` + `word/embeddings/*.bin`，不是 OMML → 解析不到、**静默丢失**，
        # PDF 里公式位置全空、页数少 2 页。这里如实计数并警告（不阻断出稿）。
        from .docxxml import ole_object_stats, symbol_run_stats

        n_ole, n_embed = ole_object_stats(_docxml, z.namelist())
        # 期 87 子案例 3（04 Fayang_Ye 的 .doc）实测：用 LogicA 等「逻辑符号字体」插入
        # 的 →/∼/∧ 是 `<w:sym>` run，既不是文字也不是 OMML → 静默丢失。
        # 字体的 char→Unicode 映射是字体私有的，不能硬猜；只计数并警告人工核对。
        _font = symbol_run_stats(_docxml)
        try:
            _font_fn = symbol_run_stats(z.read("word/footnotes.xml").decode("utf-8"))
        except KeyError:
            _font_fn = {}
        for k, v in _font_fn.items():
            _font[k] = _font.get(k, 0) + v
    if n_ole:
        doc.warnings.append(
            f"发现 {n_ole} 处 MathType/OLE 公式对象（word/embeddings 共 {n_embed} 个）："
            "它们是 OLE 包不是 OMML，Word 通道不解析 OLE 本体，但已自动取**预览图**"
            "（WMF/EMF）转 PDF 后按原尺寸出图；这类稿件需要 LibreOffice（条件必需依赖，"
            "不随工具箱分发）：若本机没有（soffice），公式图位置会显示「公式图缺失」占位、"
            "产物不宜直接送排，安装后重跑即可；"
            "如需公式可编辑，请让作者另存为 OMML（或提供 MathType→TeX 版）"
        )
    if _font:
        _n_sym = sum(_font.values())
        _fonts = "、".join(f"{k} {v} 处" for k, v in sorted(_font.items(), key=lambda kv: -kv[1]))
        doc.warnings.append(
            f"发现 {_n_sym} 处符号字体字符（<w:sym>：{_fonts}）：Word 通道不解析这类"
            "字符（既非文字也非 OMML，字体 char→Unicode 映射是字体私有的），"
            "→/∼/∧ 等逻辑符号可能从产物中丢失；请人工核对正文符号"
        )

    plains = [p.plain for p in paras]

    # ① 题名、副标题与作者
    #
    # 题名后面常另起一段放副标题（以破折号开头），再下一段才是作者。
    # 直接取「第 1 段」当作者会把副标题写进 `\myfirstauthor`——实测踩到
    # （09 Jiawei_Zhong：`\kaishu ——一个基于示例论证理论的生成模型`）。
    title_idx = head_title_index(plains)  # S1：跳过开头空段与模板页眉
    doc.title, author_idx = plains[title_idx].strip(), title_idx + 1
    if len(plains) > author_idx and _is_subtitle(plains[author_idx]):
        doc.subtitle = plains[author_idx].strip()
        doc.title = f"{doc.title}{doc.subtitle}"
        author_idx += 1
    # ⚠️ 题名与作者之间**可能是空段落**（实测 2026_05_Meimei_Han：`[1]` 是空行，
    #    作者在 `[2]`），所以不能只看 author_idx 那一段，要往后找到第一个非空段。
    for k in range(author_idx, min(len(plains), author_idx + 4)):
        cand = plains[k].strip()
        if not cand:
            continue
        if len(cand) <= 40 and not cand.endswith(("。", "：")):
            doc.author = re.sub(r"\s+", " ", cand).strip()
        break

    # ② 参考文献区（中英双认）
    ref_start = next(
        (i for i, t in enumerate(plains) if t.strip().lower().rstrip(":：") in _REF_HEADS), -1
    )

    # ②.5 文末中英元数据块（英文题名/摘要/关键词、作者、单位、邮箱）
    #
    # ⚠️ 它排在参考文献区**之后**（09 Jiawei_Zhong：第 115–125 段）。所以：
    #   · 参考文献区必须在它之前收口，否则英文摘要会被当成最后一条文献；
    #   · 元数据块只在这里扫，不能拿它去改「正文起点」（那是开头那套的事）。
    # ⚠️ 但**不能要求「参考文献」标题存在**：实测 16 篇源稿的英文块附近根本没有这个
    #    标题（或标题排在块之后），于是整条文末路径被跳过。定位判据见 locate_tail_block。
    tail_start = locate_tail_block(plains, ref_start)
    tail = parse_tail_metadata(plains, tail_start) if tail_start >= 0 else {}

    _auto_materialized = 0      # B-022：Word 自动编号清单物化条数（0 = 没命中）
    if ref_start >= 0:
        ref_end = tail_start if tail_start > ref_start else len(plains)
        section = [t.strip() for t in plains[ref_start + 1 : ref_end] if t.strip()]
        # 第四十五批 A2：① 纯「参考文献/References」标题行不是条目，剔掉（86-08 实测）；
        # ② 文末元数据块没被 locate_tail_block 认出来时（88-06 实测：标题/作者/摘要
        #    紧跟在文献区后且无「参考文献」标题），从「摘要/Abstract」标签行开始截断，
        #    否则题名、作者、摘要会被当成文献行（全是解析失败 + 污染 ref_raw_lines）。
        section = [t for t in section if t.strip().lower().rstrip(":：") not in _REF_HEADS]
        _tail_cut = next(
            (i for i, t in enumerate(section)
             if re.match(r"^\s*(?:摘\s*要|Abstract)\s*[:：]?", t) and i > 0),
            None,
        )
        if _tail_cut:
            # 从「摘要」标签往前吃掉紧邻的**元数据行**（中文题名 / 作者姓名）：
            # 短、含中文、不含年份、不是以文献年份/出版信息结尾——文献条目一定有年份或
            # 卷期/页码，这类行是文末块的表头（88-06 实测：`论证能力研究：…` / `陈志喜`）。
            _cut = _tail_cut
            while _cut > 0:
                _prev = section[_cut - 1].strip()
                if (len(_prev) <= 80 and re.search(r"[\u4e00-\u9fa5]", _prev)
                        and not re.search(r"(?:19|20)\d{2}", _prev)
                        and not re.search(r"\d+\s*[（(]\d", _prev)):
                    _cut -= 1
                    continue
                break
            section = section[:_cut]
        # 原文留档（解析器吃掉的那份 `section`，同一顺序、同一口径）：一条都没解析
        # 出来时，refmark 第四节会把这份原文交给编辑的 AI 结构化——**不是**在这里
        # 再写一个解析器。解析成功时它也照样留着，但下游用不到。
        doc.ref_raw_lines = list(section)
        # B-022（批68）：Word 自动编号清单物化——序号不在段落文字里（见上方常量注释）。
        # 只影响**解析输入**：`ref_raw_lines` 与 `parse_reference_line` 兜底仍用原文。
        _section_parse, _auto_warn, _auto_materialized = _materialize_auto_numbered_refs(
            path, section, _docxml)
        if _auto_warn:
            doc.warnings.append(_auto_warn)
        # 两条体例：`[N] 作者, 年份, "题名", 刊名…`（编号清单通道 `_numbered_refs`）
        # 与「作者. 年份. 题名[M]. …」/中文逗号风格（人年制通道 `parse_reference_line`）。
        # ⚠️ 取舍必须**按覆盖条数取优**，不能只看"人年制有没有解析出东西"：
        #    2026-10-17 实例测试回归（案例 116）——Xiaoyu_Lin 的文末 22 条编号清单里有一条
        #    编号写歪了（`[13 J. Ross…` 少了 `]`），逗号兜底把它误吃成 1 条 → `doc.refs` 非空
        #    → 旧逻辑「if not doc.refs」就**再也不敢走编号通道** → 整份 22 条只剩 1 条、
        #    正文引用全部没替换。裸的"先来先得"在两种格式混排/写歪时都会踩这个坑。
        entries, num2key, raw_by_key = _numbered_refs(_section_parse)
        if _auto_materialized and len(num2key) < _auto_materialized:
            # 物化成功但有条目解析不出键（编号→键不齐）→ 不猜映射：退回**原文口径**重解析，
            # 通道取舍与默认路径完全一致（只多一条 warning），正文 [n] 保持原文。
            doc.warnings.append(
                f"Word 自动编号清单 {_auto_materialized} 条中只解析出 {len(num2key)} 条"
                "编号条目 → 不猜编号映射（正文 [n] 保持原文），请人工核对文末清单"
            )
            _auto_materialized = 0
            entries, num2key, raw_by_key = _numbered_refs(section)
        for ln in section:
            e = parse_reference_line(ln)
            if e:
                doc.refs.append(e)
        _dedupe_keys(doc.refs)
        # 第四十五批 A2：没解析出来的文献行**保留原文 + warning**（不猜、不丢）。
        # B-219 Phase B 修：编号清单行（`[N] …`）由专用通道 `_numbered_refs` 解析，
        # 人年制兜底对带前缀的行必然 None —— 旧逻辑把它们全算「未解析」，模板 8/8 已
        # 进 bib 却仍误报 8 行。只对**两条通道都失败**的行告警（产品 .tex/.bib 不变）。
        _num_lines = [ln for ln in section if _NUMBERED_PREFIX_RE.match(ln)]
        _num_failed = max(0, len(_num_lines) - len(entries))
        _unparsed = [ln for ln in section
                     if parse_reference_line(ln) is None
                     and not _NUMBERED_PREFIX_RE.match(ln)]
        _n_unparsed = len(_unparsed) + _num_failed
        if _n_unparsed and (doc.refs or entries):
            _first = (_unparsed or _num_lines)[0]
            doc.warnings.append(
                f"文末参考文献区有 {_n_unparsed} 行未能按行内证据解析"
                f"（原文已保留、交人工/AI 核对）：首行「{_first[:80]}」"
            )
        if len(entries) > len(doc.refs) or (_auto_materialized and entries):
            # 编号清单解释力更强 → 以它为准；逗号兜底误吃的那几条丢掉（它们本就是清单行）。
            # B-022：物化成功时**等条数也优先编号通道**——只有它有「编号→键」映射，
            # 人年制通道对 Word 自动编号稿拿不到正文 [n] 的对应关系（86-08 实测 31 vs 31）。
            doc.refs = []
            doc.bib_entries.extend(entries)
            doc.ref_key_by_num = num2key
            doc.ref_auto_materialized = _auto_materialized   # B-130 gate：B-022 物化条数
            # 编号清单通道的「条目 → 原文」（整条 `[N] …`），见 `_numbered_refs`。
            doc.ref_raw_by_key.update(raw_by_key)
        else:
            # 「条目 → 源稿原文」：文末**人年制**区（第 68 条）。`RefEntry.raw` 就是
            # `parse_reference_line` 收下的那一行（已把连续空白压成单空格）；
            # 键在 `_dedupe_keys` 之后才是最终键，所以放在这里取。
            for e in doc.refs:
                if e.raw:
                    doc.ref_raw_by_key[e.key] = e.raw
            if not doc.refs and entries:
                # 编号清单只收下 1–2 条时上面取优不成立（例如真的只有一条文献）：
                # 人年制一条都没有就还是用编号通道的结果，别把唯一一条丢掉。
                doc.bib_entries.extend(entries)
                doc.ref_key_by_num = num2key
                doc.ref_auto_materialized = _auto_materialized   # B-130 gate（同上报错口径）
                doc.ref_raw_by_key.update(raw_by_key)

    # ③ 元数据块：中英各一套
    abs_blocks: list[tuple[int, bool, str]] = []
    kw_blocks: list[tuple[int, bool, str]] = []
    for i, t in enumerate(plains[:META_SCAN]):
        if _ABS_RE.match(t):
            # 中文摘要走 _abs_body_cn（多一条「标签独占一段 → 向下取正文」的放宽，
            # 见 _ABS_LOOKAHEAD 处）；英文标签由它内部转交 _label_body，行为不变。
            abs_blocks.append((i, _has_cjk(t), _abs_body_cn(plains, i)))
        elif _KW_RE.match(t):
            kw_blocks.append((i, _has_cjk(t), _label_body(plains, i, _KW_RE)))

    doc.abstract_cn = next((t for _, cjk, t in abs_blocks if cjk), "")
    doc.abstract_en = next((t for _, cjk, t in abs_blocks if not cjk), "")
    doc.keywords_cn = next((t for _, cjk, t in kw_blocks if cjk), "")
    doc.keywords_en = next((t for _, cjk, t in kw_blocks if not cjk), "")

    # ③.2 中文摘要的第二处位置：正文全英文、中文题名+摘要另附在**参考文献之后**的稿子
    #      （实测 7 篇，见 _tail_abstract_cn）。只在开头那套空手时才跑；它**不写**
    #      abs_blocks，所以 meta_end / 正文起点一个字都不受影响。
    if not doc.abstract_cn:
        doc.abstract_cn = _tail_abstract_cn(plains, ref_start)

    doc.title_cn = doc.title if _has_cjk(doc.title) else ""
    doc.title_en = "" if _has_cjk(doc.title) else doc.title
    _head_title_idx = title_idx if doc.title_en else -1  # head 英文作者窗口起点
    # ⚠️ `meta_end` 仍按**改动前**的口径算（只数紧贴段首的标签）：它管的是「正文起点」
    #    与「开头英文题名的扫描范围」，都是另外的判据，本轮只放宽摘要的**取值**，
    #    不该顺带把这两处也动了。实测 2022年第4期(65) 康孝军 的稿子：缩进的
    #    `    摘 要：…` 一旦计入 meta_end，扫描范围就从第 2 段涨到第 5 段，
    #    段首的邮箱行 ` kxj319@hotmail.com` 会被当成英文题名（title_en 变脏）。
    #    `_KW_RE` 本来就要求紧贴段首，所以这个过滤只对摘要标签生效。
    meta_end = max([i for i, _, _ in abs_blocks + kw_blocks if not plains[i][:1].isspace()],
                   default=-1)
    for i in range(1, meta_end + 1):
        t = plains[i]
        # ⚠️ 空白段不是题名：实测 07 Guangwu_Feng 的英文题名前面有一个只有空格的段，
        #    旧逻辑把 `' '` 当成了英文题名，于是 `\mytitleEN` 是空的、文末的真题名
        #    也因为「title_en 非空」不再来覆盖。
        if len(t) > 80 or _ABS_RE.match(t) or _KW_RE.match(t) or not t.strip():
            continue
        if _has_cjk(t) and not doc.title_cn:
            doc.title_cn = t
        elif not _has_cjk(t) and not doc.title_en and t != doc.author and _is_title_candidate(t):
            doc.title_en, _head_title_idx = t, i

    doc.author_en = doc.author_en or head_author_after(plains, _head_title_idx)

    # ③.5 文末那一套：开头没有的（英文题名/摘要/关键词/作者/单位/邮箱）在这里补
    #
    # 中文稿常见布局：英文题名、英文摘要、中英文单位、邮箱全放在**参考文献之后**。
    # 早先只扫开头 20 段，于是 `\mytitleEN` / `\myabstractEN` 一直是模板占位符
    # `Title` / `Abstract`（实测 09 Jiawei_Zhong 就是这样，编辑只能自己填）。
    #
    # ⚠️ title_cn / title_en 只放**主标题**，副标题留在 doc.subtitle / doc.subtitle_en，
    #    由 build.py 用 `\\` 拼起来。两边都带上副标题就会重复一次（实测踩到）。
    for attr in ("title_cn", "title_en"):
        v = getattr(doc, attr)
        if v and doc.subtitle and v.endswith(doc.subtitle):
            setattr(doc, attr, v[: -len(doc.subtitle)].strip())

    if tail:
        if not doc.abstract_en:
            doc.abstract_en = tail.get("abstract_en", "")
        if not doc.keywords_en:
            doc.keywords_en = tail.get("keywords_en", "")
        if not doc.title_en.strip():
            doc.title_en = tail.get("title_en", "")
            doc.subtitle_en = tail.get("subtitle_en", "")
        doc.author_en = tail.get("author_en", "") or doc.author_en  # S1：空值不覆盖
        doc.email = tail.get("email", "")

    # ③.6 单位：不再只认文末块里的「单位：」标签（旧实现全库只拿到 1/132）。
    #     开头元数据区、文末块、脚注里的作者信息都要看；判据见 collect_affiliations
    #     与 README 第 45 条①。带标签的旧路径优先（那是人工写明的），其余按下述顺序兜底。
    fn_segs_early = read_footnotes(path)
    fn_texts = [segs_to_text(s) for s in fn_segs_early.values()]
    # 邮箱：旧路径只认文末块里的「邮箱：」标签，41 篇 fail 里它只救得了 2 篇；
    # 裸地址要在这三段里另找（范围与判据见 collect_emails）。文档顺序第一个 = 第一作者，
    # 覆盖旧值；`tail["email"]`（上面 ③.5 写的）只在 collect_emails 空手而归时兜底。
    emails = collect_emails(plains, tail_start, fn_texts)
    if emails:
        doc.email = emails[0]
        if len(emails) > 1:
            doc.warnings.append(
                "检出多个邮箱候选（已取文档顺序第一个）：" + "、".join(emails[:4]))
    author_names = [x for x in re.split(r"[\s、,，·]+", doc.author) if len(x) >= 2]
    aff_cn, aff_en = collect_affiliations(plains, tail_start, fn_texts, author_names)
    doc.affiliation_cn = tail.get("affiliation_cn", "") or "\\\\ ".join(aff_cn)
    doc.affiliation_en = tail.get("affiliation_en", "") or AFFIL_EN_JOIN.join(aff_en)
    doc.abstract = doc.abstract_cn or doc.abstract_en
    doc.keywords = doc.keywords_cn or doc.keywords_en

    # 中文关键词的分隔符按**刊物惯例**归一：人工成品一律用分号
    # （全库实测 142 篇用「；」、0 篇用「、」），模板注释也写着「以中文分号分隔」，
    # 而作者原稿常用顿号。实测 09 Jiawei_Zhong：源稿「、」→ 人工「；」。
    if doc.keywords_cn:
        doc.keywords_cn = re.sub(r"\s*[、，,]\s*", "；", doc.keywords_cn).strip("；")

    # ③.7 作者信息脚注（批71 B-118）：姓名/单位/邮箱/收稿日期/基金项目 →
    #      DocxDoc.author_meta / received / grants。格式判据在 `slcn/footnotes.py`
    #      （单点维护）；这里只做「已知作者名」安全阀与落位。
    #      ⚠️ **只填空不覆盖**：doc.author / affiliation_* / email 已有识别值一个都不动；
    #         build 侧再以模板槽位「当前是否已解析」为最终判据（人工表单/CLI 优先）。
    #      ⚠️ 无此类脚注的稿子（90-06/09 实测）走不到任何赋值 → 产物逐字节不变。
    from .footnotes import (looks_like_author_info, merge_author_info,
                            parse_author_info, parse_fund_line)

    _known_cn = _known_cn_authors(doc.author)
    _known_en = _known_en_authors(doc.author_en)
    _ai_infos: list[dict] = []
    for _text in fn_texts:
        _info = parse_author_info(_text, known_cn=_known_cn, known_en=_known_en)
        if _info:
            _ai_infos.append(_info)
        elif looks_like_author_info(_text):
            doc.warnings.append(
                "作者信息脚注存在但无法判定作者（保持现有解析结果，请人工填写）："
                + _text.strip()[:60])
    # 正文里的独立一行 `基金项目：…`（90-08 实测：作者信息只在正文出现、且没有
    # 作者姓名/单位）→ **只填基金项目、绝不臆造作者**；该行从正文里移出（否则
    # 标题页基金脚注 + 正文会重复印一遍）。
    _drop_fund_para = None
    if not any(_i.get("grants") for _i in _ai_infos):
        for _i, _p in enumerate(plains[1:], start=1):
            _g = parse_fund_line(_p)
            if _g:
                _ai_infos.append({"authors": [], "received": "", "grants": _g,
                                  "warnings": []})
                _drop_fund_para = paras[_i]
                break
    _ai = merge_author_info(_ai_infos, known_cn=tuple(_known_cn),
                            known_en=tuple(_known_en))
    doc.author_meta = _ai["authors"]
    doc.grants = _ai["grants"]
    doc.received = _ai["received"]
    doc.warnings.extend(_ai["warnings"])
    if doc.author_meta:
        # 只填空：作者姓名/单位/邮箱为空的稿子才用脚注值（多作者并列时补全作者串）。
        _named = [a["name"] for a in doc.author_meta if a.get("name")]
        if not doc.author.strip() and _named:
            doc.author = " ".join(_named)
        _first = doc.author_meta[0]
        if not doc.affiliation_cn.strip() and _first.get("affiliation_cn"):
            doc.affiliation_cn = "\\\\ ".join(_first["affiliation_cn"])
        if not doc.affiliation_en.strip() and _first.get("affiliation_en"):
            doc.affiliation_en = AFFIL_EN_JOIN.join(_first["affiliation_en"])
        if not doc.email.strip() and _first.get("email"):
            doc.email = _first["email"]

    # ④ 正文起点：必须在**所有**元数据块之后
    start = meta_end + 1 if meta_end >= 0 else 2
    for i in range(start, min(start + 8, len(paras))):
        if plains[i] == doc.title:
            j = i + 1
            while j < len(paras) and (
                plains[j] == plains[i]
                or plains[j] == doc.author
                or plains[j].startswith(("School", "Department", "University", "Institute"))
            ):
                j += 1
            start = j
            break
    # B-105（87-9 实测）：文末英文题名/摘要/关键词块既是元数据来源，又会被模板
    # 2.11 再排一次——正文必须在这里截断。有「参考文献」标题且英文块在其后时仍以
    # 标题为界（与 HEAD 逐字节一致）；没有标题（ref_start<0）、或标题在英文块之后
    # （71/06 Lili_Lu 型）时，以英文块起点 tail_start 为界。
    _ends = [x for x in (ref_start, tail_start) if x > start]
    end = min(_ends) if _ends else len(paras)
    body_paras = paras[start:end]

    # 90-08 型：正文里那个独立的 `基金项目：…` 段落已提升到标题页基金项 → 从正文/块
    # 序列里移出（否则标题页脚注 + 正文重复印一遍）。位置索引全部基于移除后的列表，
    # 保持既有「body_paras ↔ blocks」下标口径一致。
    if _drop_fund_para is not None and any(p is _drop_fund_para for p in body_paras):
        body_paras = [p for p in body_paras if p is not _drop_fund_para]
        paras = [p for p in paras if p is not _drop_fund_para]
        blocks = [b for b in blocks if b is not _drop_fund_para]
        doc.warnings.append(
            f"正文中的「基金项目：…」行已移入元数据（标题页基金项）："
            f"{doc.grants[:60]}")

    # B-219③：正文里只由「中图分类号/文献标识码」组成的整段 → 删除（标题页由
    # SLCN-MIX.sty 固定印刷 B81/A，不得重复）。与上面基金行同口径：从 body_paras /
    # paras / blocks 三处一起移出，保持既有下标口径一致；没这行的稿件零变化。
    _clc_paras = [p for p in body_paras if _is_clc_meta_only(p.plain)]
    if _clc_paras:
        _clc_ids = {id(p) for p in _clc_paras}
        body_paras = [p for p in body_paras if id(p) not in _clc_ids]
        paras = [p for p in paras if id(p) not in _clc_ids]
        blocks = [b for b in blocks if id(b) not in _clc_ids]
        doc.warnings.append(
            f"已移除正文中图分类号/文献标识码行 {len(_clc_paras)} 段"
            "（标题页固定印刷）：" + "；".join(p.plain.strip()[:40] for p in _clc_paras))

    # 第 121 条：全英文稿（正文前 120 段无汉字）里，文末有参考文献区时，
    # 脚注里的散文/引注一律按注释处理（01 Tuomo_Aho 型）；中文稿/混合稿不受这条影响。
    doc_is_en = not any(_has_cjk(p.plain) for p in body_paras[:120] if p.plain.strip())

    # ⑤ 脚注：分类 → 生成 bib → 建立 id→key 映射
    #    （footnotes.xml 在 ③.6 已经读过一次，这里复用，不重复解压）
    fn_segs = fn_segs_early
    doc.n_footnotes = len(fn_segs)
    fn_objs = {fid: classify(segs_to_text(segs)) for fid, segs in fn_segs.items()}
    for fid, f in fn_objs.items():
        f.fid = fid  # classify() 不知道 id，这里回填，否则 keymap 查不到
    # 第四十四批①：含公式的脚注按 segs 渲染 LaTeX（渲染分支优先用 note_latex），
    # 避免 `$…$` 被后面的 escape_latex(fn.text) 二次转义（83/05、87/06 编译失败真因）。
    for _fid, _segs in fn_segs.items():
        if any(sg.kind == "math" for sg in _segs):
            fn_objs[_fid].note_latex = _footnote_latex(_segs)
    # 第四十七批（86-07 实测）：中文**论述性脚注**（长散文 + 行内引用）被 classify()
    # 误判成 pure_ref → 渲染时被提升成行内 `\parencite`，脚注散文整段丢失
    # （86-07：29 条脚注人工 PDF 全印、引擎只印 11 条，丢 18 条/2 页）。
    # 判据：含中文、汉字 ≥15、总长 ≥40 → 按**纯注释**处理（保留原文；引用在
    # ⑤.3b 用 link_citations 就地替换），且**不生成 bib 条目**（人工成品同样不把
    # 这些论述脚注当 bib 条目）。英文论述性脚注已有第 121 条同类降级。
    for _fid, _f in ([] if c_class_union else list(fn_objs.items())):
        if _f.kind != "pure_ref":
            continue
        _txt = _f.text or ""
        _cjk_n = len(re.findall(r"[\u4e00-\u9fa5]", _txt))
        # 必须同时有「行内作者-年引用」——这才是**论述性脚注**的特征；
        # 中文文献条目型脚注（《…》，出版社，年）不含括号引用，保持 pure_ref 进 bib
        # （否则 86-03 的 21 条中文文献脚注会被误降级、bib 从 22 掉到 1，实测回归）。
        # 论述性特征：① 行内**作者-年引用**（括号里任意位置含年份，覆盖
        # `(Cook, 2010)` / `(李雷, 2018，第三节)` / `(Burgess, 2009, p. 129)`）；
        # 或 ② 多句散文（≥2 个句号）。中文文献条目型脚注通常两者都不满足
        # （86-03 的 21 条文献脚注实测不满足 → 不会被误降级、bib 保持 22）。
        _inline_cite = re.search(
            r"[（(][^（）()]{0,100}?(?:19|20)\d{2}[^（）()]{0,40}?[）)]", _txt)
        _multi_sentence = (_txt.count("。") >= 2)
        # 中文文献条目还要认「书名号」与「年份+期号」形态：86-03 有 1 条脚注把
        # **两条中文文献**合在一起（`…，逻辑学研究，2018 年第 1 期。麦劲恒：《…》…`），
        # 旧标记漏了书名号 → 被误当论述脚注降级、bib 22→21。这里补上（86-07 的
        # 论述脚注实测都不含书名号/期号，不受影响）。
        _ref_marker = re.search(
            r"出版社|学报|期刊|\[[MJDC]\]|博士论文|硕士论文|《[^》]{2,}》|"
            r"(?:19|20)\d{2}\s*年?\s*第\s*\d+\s*[期卷]", _txt)
        # 第四十八批（86-07 实测）：短论述脚注（如「详细的证明过程可参见(Schurz,
        # 2022)第三节。」CJK 仅 10 字）此前 CJK≥15 门槛漏掉 → 散文被丢弃。
        # 放宽只对**半角括号 + 拉丁作者-年**的引用生效（86-07 型）；中文文献脚注
        # （90-06/90-07 的人年制条目、全角括号/中文作者）保持 pure_ref 进 bib，
        # 避免把文献脚注误降级（实测 90-06 bib 38→25、90-07 51→26 的回归）。
        _short_ascii_cite = (_cjk_n < 15 and re.search(
            r"\([^()]*[A-Za-z][^()]{0,80}?(?:19|20)\d{2}[^()]{0,40}\)", _txt))
        if _cjk_n >= 5 and len(_txt) >= 12 and (
                (_cjk_n >= 15 and (_inline_cite or (_multi_sentence and not _ref_marker)))
                or _short_ascii_cite):
            _f.kind = "pure_note"
            _f.note = _txt
            _f.ref = ""
            _f.entry = None
            setattr(_f, "_discursive_cjk", True)

    # 第四十九批（C 类新口径，**开关默认 OFF**）：ON 时用 bib-refs 新分类器
    # `classify_citation()` 重分类，并做短引用匹配（不编造）。映射结果复用旧渲染/条目
    # 机制：reference_entry → pure_ref；pure_citation 命中 → pure_citation_matched
    #（正文 parencite）；未命中 → citation_unmatched（保留脚注 + warning）；mixed →
    # mixed_manual（保留脚注 + 核对 warning）；note_only → pure_note。
    if c_class_union:
        from .footnotes import (CITATION_ENTRY, CITATION_MIXED, CITATION_MIXED_SPLIT,
                                CITATION_NOTE, CITATION_PURE, FootnoteWarning,
                                classify_citation, resolve_citation,
                                union_entries)
        _candidates: list[tuple[str, dict, str]] = []
        for _k, _e, _raw in doc.bib_entries:
            _candidates.append((_k, _e, _raw))
        for _r in doc.refs:
            _candidates.append((_r.key, {"author": _r.author, "year": _r.year,
                                         "title": _r.title, "type": _r.kind}, _r.raw))
        _guard_entries: list[int] = []   # 中文文献条目型兜底 fid（union 前按指纹会被合并）
        for _fid, _fn in list(fn_objs.items()):
            _new = classify_citation(_fn.text)
            _new.fid = int(_fid)
            # 第五十二批 U1（87-5、90-04 实测）：上面 ④.5 已在**旧对象**上按 math segs
            # 预渲染 note_latex（`$…$` 已受保护）；用 classify_citation 新建 Footnote 后
            # 必须继承，否则保留脚注的各分支（pure_note / citation_unmatched /
            # mixed_manual）渲染时回落 escape_latex(fn.text) → 公式被二次转义
            #（`$..$`→`\$..\$`、`\phi`→`\textbackslash{}phi`）。
            _old_note_latex = getattr(_fn, "note_latex", "") or ""
            if _old_note_latex:
                _new.note_latex = _old_note_latex
            _kind = _new.kind_new
            _fallback_mixed = False
            _was_split = _kind == CITATION_MIXED_SPLIT
            if _kind == CITATION_ENTRY:
                # B-199：引导语后剥出的完整条目（90-07「参阅徐佳明…」型）先按文末清单
                # 原文行找既有键——命中则只 cite（不改 bib），避免字段碎片导致重复条目。
                _key = ""
                if getattr(_new, "cue_stripped", False):
                    _t, _y = _citation_title_year(_new)
                    _key = _src_line_key(doc, _t, _y)
                if _key:
                    _new.kind = "pure_citation_matched"
                    _new.key = _key
                else:
                    _new.kind = "pure_ref"
                    _new.ref = _new.ref_raw or _new.ref
                    _new.entry = _new.entry or {}
            elif _kind == CITATION_PURE:
                # B-199：note_only 探测提升的短引用用 strict（唯一命中才提升）；
                # 其余短引用在原匹配失败后，再用文末清单原文行兜底（90-07 Einstein 型：
                # 无作者、题名+卷+年本可唯一，旧匹配在「作者不匹配」处早退）。
                _strict = bool(getattr(_new, "from_note_probe", False))
                _key, _warn = resolve_citation(_new, _candidates, strict=_strict)
                if not _key:
                    _t, _y = _citation_title_year(_new)
                    if _t:
                        _key = _src_line_key(doc, _t, _y)
                if _key:
                    _new.kind = "pure_citation_matched"
                    _new.key = _key
                elif _strict:
                    # 探测未唯一命中 → 保持纯注释（与旧行为一致，不改产物）
                    _new.kind = "pure_note"
                    _new.note = _new.text
                    _new.ref_raw = ""
                else:
                    # B-220 N6：短引在文末清单里配不上完整条目时，再问一次**引用库**
                    # （仅 HIT_SAFE + 题名 ≥0.90 才建条目并提升；候选/未命中不动）。
                    # 实测 90-06 4 处 → 库内 Watson1987；实现在 bibdbapply（单点）。
                    _lib_ok = False
                    if bibdb_store is not None:
                        from . import bibdbapply

                        _lib_ok = bibdbapply.attach_short_citation(
                            doc, _new, _warn, bibdb_store)
                    if not _lib_ok:
                        _new.kind = "citation_unmatched"
                        doc.c_class_warnings.append(_warn)
                        doc.footnote_notes += 1
            elif _kind == CITATION_MIXED_SPLIT:
                # B-199：真 mixed 拆分。多作品 → 逐作品按原文行匹配；单作品 → 完整
                # 条目进 bib / 短引用匹配既有键。都失败（或脚注含公式）→ 保持 mixed。
                _multi = bool(getattr(_new, "multi_work", False))
                _keys: list[str] = []
                if _multi:
                    _keys = _src_line_keys_for_works(doc, _new.ref_raw)
                _entry = _new.entry or {}
                if not _keys and not _entry and not _multi:
                    _key, _warn = resolve_citation(_new, _candidates)
                    if not _key:
                        _t, _y = _citation_title_year(_new)
                        if _t:
                            _key = _src_line_key(doc, _t, _y)
                    if _key:
                        _keys = [_key]
                if _old_note_latex:
                    # 含公式的脚注：拆分渲染会破坏已受保护的数学 → 保持 mixed。
                    _fallback_mixed = True
                elif _keys:
                    _new.kind = "mixed_split_cite"
                    _new.key = ",".join(_keys)
                elif _entry_is_note_only(_entry):
                    _fallback_mixed = True
                else:
                    _new.kind = "mixed_split_entry"
                    _new.ref = _new.ref_raw or ""
            elif _kind == CITATION_MIXED:
                _fallback_mixed = True
            elif _kind == CITATION_NOTE:
                _new.kind = "pure_note"
                _new.note = _new.note or _new.text
            if _new.kind in ("mixed_split_entry", "mixed_split_cite"):
                # B-199：拆分脚注渲染 = note_before + \parencite[loc]{key} + note_after；
                # keep_whole（多作品兜底）时保留整段原文、parencite 追加在末尾。
                try:
                    if getattr(_new, "keep_whole", False):
                        _new.note_before_latex = link_citations(
                            escape_latex(_new.text or ""), doc.refs)[0]
                        _new.note_after_latex = ""
                    else:
                        _new.note_before_latex = (
                            link_citations(escape_latex(_new.note_before), doc.refs)[0]
                            if _new.note_before else "")
                        _new.note_after_latex = (
                            link_citations(escape_latex(_new.note_after), doc.refs)[0]
                            if _new.note_after else "")
                except Exception:  # noqa: BLE001
                    _new.note_before_latex = _old_note_latex
                    _new.note_after_latex = ""
            if _fallback_mixed:
                _new.kind = "mixed_manual"
                try:
                    # U1：含公式的混合脚注同样复用旧对象预渲染的 note_latex
                    # （数学已受保护；引用替换照做，避免二次转义）。
                    # B-199 验收缺陷：mixed_split 回退时必须保留**整条原文**
                    # （note_before 也是脚注正文；只取 ref_raw 会吞掉说明文字）。
                    if _was_split:
                        _mixed_src = _old_note_latex or escape_latex(_new.text)
                    else:
                        _mixed_src = _old_note_latex or escape_latex(
                            _new.note or _new.ref_raw or _new.text)
                    _new.note_latex = link_citations(_mixed_src, doc.refs)[0]
                except Exception:  # noqa: BLE001
                    _new.note_latex = _old_note_latex
                doc.c_class_warnings.append(FootnoteWarning(
                    "mixed_manual_check", fid=int(_fid),
                    text=(_new.text or "")[:200],
                    reason="说明文字+引用：脚注保留，请人工核对引用与说明是否对应",
                    author=str((_new.entry or {}).get("author", "")),
                    title=str((_new.entry or {}).get("title", "")),
                    year=str((_new.entry or {}).get("year", "")),
                    locator=_new.locator))
            # 第四十九批引擎侧边界保护（86-03 实测）：中文**文献条目型**脚注
            # （源稿形如 `[] 胡炳年、陈彦瑾，“…”，逻辑学研究，2018 年第 1 期。`）会被
            # 新分类器判成 mixed/note_only → bib 22→1。判据单点在
            # `footnotes.split_numbered_bib_note`（带 `[编号]` 前缀 + 文献标记才认）。
            if _new.kind in ("mixed", "mixed_manual", "pure_note",
                             "mixed_split_entry", "mixed_split_cite"):
                _txt_clean = split_numbered_bib_note(_new.text or "")
                if _txt_clean is not None:
                    try:
                        _e = parse_entry(_txt_clean)
                    except Exception:  # noqa: BLE001
                        _e = {}
                    # 与旧口径一致：解析字段为空也照样进 bib（旧行为 86-03 就是 22 条），
                    # 空字段由 bibnorm/编辑侧兜底，不能因为 entry 空就丢条目。
                    _new.kind = "pure_ref"
                    _new.ref = _txt_clean
                    _new.entry = _e or {}
                    _guard_entries.append(int(_fid))
            fn_objs[_fid] = _new

    # 第四十九批：混合型脚注清单（渲染保留脚注 + build 汇总「请人工核对」warning）。
    # **仅新口径开关 ON 时**收集：默认 OFF 时旧行为/产物/warning 逐字节不变。
    if c_class_union:
        doc.mixed_footnotes = [
            (int(fid), (fn.text or "").strip()[:40])
            for fid, fn in fn_objs.items()
            if fn.kind in ("mixed", "mixed_manual")
        ]

    _end_entries_snapshot: list[tuple[str, dict, str]] = list(doc.bib_entries)
    used_keys: set[str] = set()
    keymap: dict[int, str] = {}
    if c_class_union:
        # 短引用命中 → keymap（正文渲染用 `\\parencite[locator]{key}`）
        for _fid, _fn in fn_objs.items():
            if _fn.kind in ("pure_citation_matched", "mixed_split_cite") \
                    and getattr(_fn, "key", ""):
                keymap[_fid] = _fn.key
    # 第四十七批（88-02 实测）：同一文献被**多条脚注反复引用**时逐条提升 → bib 重复
    # （88-02 有 19 条重复：同一作品不同页码各生成一条），参考文献区多 2 页。
    # 按「作者+年+题名前 30 字」指纹去重：重复引用复用已有键，不再新增 bib 条目。
    _bib_fp: dict[tuple[str, str, str], str] = {}
    _bib_loose: dict[tuple[str, str], tuple[str, str]] = {}
    seq = 0
    for fid in sorted(fn_objs):
        f = fn_objs[fid]
        if f.kind in ("pure_ref", "mixed", "mixed_split_entry"):
            # 第 121 条（期 88 实测）：英语**论述性脚注**（含年份/see 的散文）会被
            # classify() 误判成 pure_ref；parse_entry() 只能给出 note（没有 author/title），
            # 照旧当文献条目会 ① 生成垃圾 bib、② 把脚注散文从正文抽走。
            # 条目不成立且**不含中文**时降级为纯注释（中文脚注体例不动，避免回归）。
            # B-199：**只**对「引导语剥离条目」与「真 mixed 拆分条目」用分类阶段解出
            # 的字段（`parse_entry(f.ref)` 对「参阅 X, …」/中文条目只能回 note，会误触
            # 降级或生成垃圾条目）；其余路径保持旧行为（重解析 f.ref），避免改变已
            # 有稿子的键/碎片合并（90-07 ref03/04/05 靠旧路径与 repair 合并）。
            _use_cls_entry = (bool(getattr(f, "cue_stripped", False))
                              or f.kind == "mixed_split_entry")
            _e0 = (getattr(f, "entry", None) or {}) if _use_cls_entry else {}
            if _entry_is_note_only(_e0):
                _e0 = parse_entry(f.ref)
            # 「有文末参考文献区」的英文稿（refs 非空）里，脚注的散文/引注一律不算条目；
            # 没有文末区的短引注（`Cf. X 2012.`，中文稿里的英文脚注常见）保留旧行为，
            # 避免正文引用全丢。
            # B-022（批68）：文献区走 Word 自动编号物化时 `doc.refs` 会被清空、
            # 条目改由 `doc.bib_entries` 承载——判据里的「有文末区」必须把物化算上，
            # 否则 73/05、73/06 实测会把脚注散文当 bib 条目、原文从正文里抽走。
            _downgrade = (_entry_is_note_only(_e0) and not _has_cjk(f.text)
                          and ((doc_is_en and bool(doc.refs or _auto_materialized))
                               or (not _looks_short_bibliographic_note(f.text)
                                   and len(f.text.strip()) >= 90)))
            if _downgrade:
                f.kind = "pure_note"
                f.note = f.text
                f.ref = ""
                f.entry = None
                doc.footnote_notes += 1
                # P0-6b：降级不能静默——把「被判成注释、没进 bib」的脚注写进 warning，
                # 编辑/报告才看得见（sub9 少 1 条就是这么漏掉的）。
                doc.warnings.append(
                    f"脚注 {fid} 的文献条目未进 bib（parse_entry 只能得到 note，"
                    f"按注释处理）：{f.text.strip()[:80]}")
                continue
            seq += 1
            entry = _e0
            f.entry = entry
            # 第四十七批：重复引用同一文献 → 复用已有键（不新增 bib 条目）。
            # 只在 author/year/title 三者都有时去重（信息不足的不猜）。
            _fp = None
            # 优先级：**清洗后的原文**（去掉页码定位后比较）——同一作品被不同页码引用时
            # 只有 `p. 13` / `p. 6` 不同，归一到同一指纹；parsed 字段做兜底。
            _raw_norm = re.sub(
                r"\bpp?\.?\s*\d+(?:\s*[-–—]\s*\d+)?|第\s*\d+(?:\s*[-–—]\s*\d+)?\s*页",
                "", (f.ref or "").lower())
            _raw_norm = re.sub(r"\s+", "", _raw_norm)
            if _raw_norm:
                _fp = ("raw", _raw_norm, "")
            elif entry.get("author") and entry.get("title"):
                _fp = (
                    "fields",
                    re.sub(r"\W+", "", str(entry.get("author", "")).lower()),
                    re.sub(r"\W+", "", str(entry.get("title", "")).lower())[:30]
                    + "|" + str(entry.get("year", "") or ""),
                )
            # 第四十八批（88-02 实测 57 vs 人工 43）：版本/年份缺失导致同一作品指纹不同。
            # 宽松指纹 = 作者姓氏 + 题名（不含年份/版本），**仅当一方缺年份**时归并；
            # 两边年份都不同（不同版次/不同作品）不合并——不猜题名/不改条目内容。
            _loose = None
            _author = str(entry.get("author", "") or "")
            _title = str(entry.get("title", "") or "")
            _year = str(entry.get("year", "") or "")
            if _author and _title:
                _loose = (
                    re.sub(r"[^\w\u4e00-\u9fa5]+", "", _author.lower())[:40],
                    re.sub(r"[^\w\u4e00-\u9fa5]+", "", _title.lower())[:50],
                )
            if _fp and _fp in _bib_fp:
                f.key = _bib_fp[_fp]
                keymap[fid] = f.key
                if f.ref:
                    doc.ref_raw_by_key[f.key] = f.ref
                continue
            if _loose and _loose in _bib_loose:
                # 同作者+同题名（前后去标点/大小写归一，**不改题名、不猜年份**）视为
                # 同一作品的不同版次/页码引用 → 归并（不改变已保留条目内容）。
                _key0, _year0 = _bib_loose[_loose]
                f.key = _key0
                keymap[fid] = f.key
                if f.ref:
                    doc.ref_raw_by_key[f.key] = f.ref
                continue
            f.key = make_key(entry, seq, used_keys)
            if _fp:
                _bib_fp[_fp] = f.key
            if _loose and _loose not in _bib_loose:
                _bib_loose[_loose] = (f.key, _year)
            keymap[fid] = f.key
            doc.bib_entries.append((f.key, entry, f.ref))
            # 「条目 → 源稿原文」：脚注体例（第 68 条）。原文取 `f.ref` = `classify()`
            # 从脚注文字里切出来、**真正喂给解析器**的那段文献文字（作者的论述在
            # `f.note` 里，不混进来）；键是上面刚定的最终键。
            if f.ref:
                doc.ref_raw_by_key[f.key] = f.ref
        else:
            doc.footnote_notes += 1

    if c_class_union:
        # 第四十九批 C 类新口径：并集 = 文末清单（编号清单条目）∪ 脚注条目，
        # 由 bib-refs `union_entries()` 按「作者+题名+年份+类型」指纹合并（忽略页码/
        # 版次；文末版本优先、缺字段互补；冲突进 warning）。**开关默认 OFF**。
        from .footnotes import union_entries as _union_entries

        _new_entries = doc.bib_entries[len(_end_entries_snapshot):]
        # 第五十批（v7 键一致性）：union 可能把旧键并到保留键 → bib-refs 通过
        # `keymap` 回传 old_key→new_key；这里把正文/编号清单/脚注 keymap 同步改写，
        # 保证正文 cite 的键全部指向并集后的最终键（否则 undef/dangling）。
        _union_alias: dict[str, str] = {}
        doc.bib_entries = _union_entries(
            list(_end_entries_snapshot), list(_new_entries),
            warnings=doc.c_class_warnings, keymap=_union_alias)
        if _union_alias:
            def _alias_key(_k: str) -> str:
                # B-199：多作品键是 "K1,K2" 逗号串 → **逐键**改写（整串 .get 永不命中）
                return ",".join(_union_alias.get(p, p) for p in str(_k).split(","))

            for _fid, _k in list(keymap.items()):
                keymap[_fid] = _alias_key(_k)
            for _fid, _fn in fn_objs.items():
                if getattr(_fn, "key", ""):
                    _fn.key = _alias_key(_fn.key)
            for _num, _k in list(doc.ref_key_by_num.items()):
                doc.ref_key_by_num[_num] = _union_alias.get(_k, _k)
            for _old, _newk in _union_alias.items():
                if _old in doc.ref_raw_by_key and _newk not in doc.ref_raw_by_key:
                    doc.ref_raw_by_key[_newk] = doc.ref_raw_by_key.pop(_old)
        # 中文文献条目型兜底（86-03）：解析字段可能为空 → 指纹相同被 union 合并成 1 条；
        # 这里把丢掉的 guard 条目按原键补回（内容来自原文，仍由 entry_to_bib 兜底渲染）。
        _now_keys = {k for k, _e, _r in doc.bib_entries}
        for _gfid in _guard_entries:
            _gf = fn_objs.get(_gfid)
            if _gf is None or not getattr(_gf, "key", ""):
                continue
            if _gf.key in _now_keys:
                continue
            doc.bib_entries.append((_gf.key, _gf.entry or {}, getattr(_gf, "ref", "")))
            _now_keys.add(_gf.key)
        if doc.bib_entries:
            say_union = (f"   并集参考文献（C 类新口径）：文末清单 "
                         f"{len(_end_entries_snapshot)} 条 + 脚注条目 "
                         f"{len(_new_entries)} 条 → {len(doc.bib_entries)} 条")
            doc.warnings.append(say_union)

    # ⑤.3b 第 121 条：英文纯注释（含上一步由英文散文脚注降级来的）里的
    # 人年制引用也替换成 \parencite——人工成品在脚注里同样做了引用替换。
    # 只处理**不含中文**的注释，中文脚注体例保持逐字节不变。
    for _f in fn_objs.values():
        if (_f.kind in ("pure_note", "mixed") and not getattr(_f, "note_latex", "")
                and (not _has_cjk(_f.text) or getattr(_f, "_discursive_cjk", False))):
            # 第四十七批：英文注释（第 121 条既有）与**中文论述性脚注**（86-07：
            # classify 误判 pure_ref 后降级）都做行内引用替换，保留散文原文。
            try:
                _esc = escape_latex(_f.text)
                _f.note_latex = link_citations(_esc, doc.refs)[0]
            except Exception:  # noqa: BLE001 —— 注释替换失败不影响主流程
                _f.note_latex = ""

    # ⑤.4 内嵌图片：docx 里就存着原图，直接取出（比截图质量高）
    from .docxxml import read_media

    media_map = read_media(path)
    img_names: dict[str, str] = {}
    seq_img = 0
    # 第四十六批②续（86-01 实测）：图片不只在正文段落里——**表格单元格里也可能有**
    # （`<w:drawing>` 全在 `<w:tc>` 内时，旧实现只遍历 body_paras（不含 Table）→
    #  18 张全丢、只剩正文 1 张，而人工成品是 18 条 \includegraphics）。
    # 按 blocks 的文档顺序收集（含 Table 的 cell.segs）；fig 序号与 extract_media()
    # 的导出顺序一致（导出按 doc.images 顺序，二者同源）。
    from .docxxml import Table as _Table

    def _iter_block_segs(_b):
        if isinstance(_b, _Table):
            for _row in _b.rows:
                for _cell in _row:
                    yield from _cell.segs
        else:
            yield from _b.segs

    _pos = {id(b): k for k, b in enumerate(blocks)}
    _bstart = _pos.get(id(body_paras[0]), 0) if body_paras else len(blocks)
    _bend = (_pos.get(id(body_paras[-1]), len(blocks) - 1) + 1) if body_paras else len(blocks)
    for _b in blocks[_bstart:_bend]:
        for sg in _iter_block_segs(_b):
            if sg.kind == "image" and sg.rid in media_map and sg.rid not in img_names:
                seq_img += 1
                ext = os.path.splitext(media_map[sg.rid])[1].lower() or ".png"
                if ext in (".wmf", ".emf"):
                    # 第四十七批（87-2 实测）：MathType 公式预览图是 WMF/EMF，XeLaTeX
                    # 不能直接嵌入；`extract_media()` 会用 soffice 转成同名 figN.pdf。
                    ext = ".pdf"
                img_names[sg.rid] = f"fig{seq_img}{ext}"
    doc.images = [(media_map[r], n) for r, n in img_names.items() if r in media_map]

    # ⑤.5 检出「用字符拼的图」（见 find_char_diagrams）
    fig_marks: dict[int, int] = {st: c for st, c in find_char_diagrams(body_paras)}

    # ⑤.6 `N. 中文标题`（见 _HEAD_CN_DOT）的两条**上下文**判据。
    #
    # ① 层级：同篇还有 `一、`/`二、` 式一级编号时（`_HEAD1` → `\section`），`1.` 是**子节**。
    #    实测 03 Ziming_Song：`一、引言`…`六、结语` 是一级，`1. 决策论与愿望/信念`、
    #    `2. 表征定理` 挂在 `二、` 底下；人工成品 Ziming_Song.tex 给的正是
    #    `\subsection{决策论与愿望/信念}`。67A 邱德钧稿同构（`二、…` 下挂 `1.`–`5.`）。
    #    没有 `一、` 的稿子（04 Lei_Li、07 Long_Chen、07 Guangwu_Feng…）里 `N.` 本身就是
    #    一级标题，人工成品同样排 `\section` —— 所以这一层判定与人工做法一致。
    #    ⚠️ 只作用于**新增**的点式分支：`N 空格`/`N.M` 等既有分支的层级一字不改。
    # ② 列表：`1. 莱布尼茨` / `2. 休谟` / `3. 康德` 这种**连排**短行是名单，不是一串小节。
    #    判据是「前后邻居里只要还有一个同形行，整串都按列表处理」。语料 132 篇里这种
    #    连排 0 处（04 Lei_Li 那 6 行每行后面都跟着成段正文，照旧认成标题），
    #    这条纯粹是防「名单被拆成一堆 \section」的反例防线——只会少认，不会多认。
    cn_num_top = any(
        _HEAD1.match(escape_latex(p.plain).strip()) for p in body_paras if p.plain.strip()
    )
    nonempty_paras = [p for p in body_paras if p.plain.strip()]
    lone_cn_dot: set[int] = set()
    for k, p in enumerate(nonempty_paras):
        prev_t = escape_latex(nonempty_paras[k - 1].plain).strip() if k else ""
        next_t = (escape_latex(nonempty_paras[k + 1].plain).strip()
                  if k + 1 < len(nonempty_paras) else "")
        if not _is_cn_dot_heading(prev_t) and not _is_cn_dot_heading(next_t):
            lone_cn_dot.add(id(p))

    # ⑤.7 无样式英文小标题（第 121 条，07 Hu_Liu 型）：只对**全英文**稿件启用，
    # 且要求「前面是成段正文、后面是成段正文」，避免把正文里的短句误判成标题。
    plain_en_headings: dict[int, str] = {}
    if nonempty_paras and not any(_has_cjk(x.plain) for x in body_paras[:120] if x.plain.strip()):
        for k, p in enumerate(nonempty_paras):
            if k < 0:                     # （占位，实际判据见下：前 3 段多半是元数据）
                continue
            t = escape_latex(p.plain).strip()
            if _HEAD_EN1.match(t) or _HEAD_EN2.match(t) or _HEAD_EN2_HALF.match(t) \
                    or _HEAD_EN3.match(t) or not _looks_plain_en_title(t):
                continue
            prev_t = (escape_latex(nonempty_paras[k - 1].plain).strip() if k else "")
            next_t = (escape_latex(nonempty_paras[k + 1].plain).strip()
                      if k + 1 < len(nonempty_paras) else "")
            prev_ok = (
                k <= 3                                   # 稿子开头的题名/副题/作者区之后
                or len(prev_t) > 80                       # 两段正文之间
                or prev_t.endswith((".", "。", "!", "?", ":", "："))
            )
            if prev_ok and len(next_t) > 50:
                plain_en_headings[id(p)] = t

    # ⑥ 渲染正文（段落与表格按原顺序混排）
    fn_pages = {fid: f.locator for fid, f in fn_objs.items()}
    # B-130（批73）：只有「文末清单是可靠 1..N 编号映射」的编号体例稿才把**脚注文本里
    # 的裸 [N]** 转成 `\parencite`（gate 见 `_footnote_num2key_gate`）；其余稿传空 →
    # 渲染路径与从前逐字节相同（默认零变化）。正文裸编号不在本卡。
    fn_num2key = _footnote_num2key_gate(doc)
    if not fn_num2key:
        # spec §2.2：两个可靠映射条件都不满足 → 不猜；脚注里「像引用」的裸编号
        # 汇总成一条 warning（没有这类脚注的稿子不新增 warning，默认零变化）。
        _fn_gate_hint = _footnote_bare_cite_gate_warning(fn_objs)
        if _fn_gate_hint:
            doc.warnings.append(_fn_gate_hint)
    # B-219②（用户 2026-10-24 拍板 A：按引用联动）：先收集正文「式（N）/公式（N）」
    # 的引用编号集合 R 与独立公式数 D（`m:oMathPara`，文档顺序）。
    # 仅当 R 非空且 max(R) <= D 时启用全部编号（N=1..D，文档级计数器）；
    # R 为空 → 保持 `\[…\]`（与已刊一致）；R 非空但对不齐 → 不编号 + 1 条 warning。
    _eq_refs = {
        int(n) for p in body_paras for n in _EQ_REF_RE.findall(p.plain) if int(n) >= 1
    }
    _eq_display = sum(
        1 for p in body_paras for s in p.segs
        if s.kind == "math" and getattr(s, "display", False)
    )
    eq_counter: list[int] | None = None
    if _eq_refs:
        if max(_eq_refs) <= _eq_display:
            eq_counter = [0]
        else:
            doc.warnings.append(
                f"式引用编号无法与独立公式对齐（D={_eq_display}, "
                f"引用 max={max(_eq_refs)}），保持原样（需人工核对）")
    out: list[str] = []
    tab_idx = 0
    fig_counter = [0]   # P2-0：文档级图序号（整篇唯一；表序号 tab_idx 一直是文档级）
    # body_paras 不含表格，需换回 blocks 的下标才能让表格落到正确位置
    pos_in_blocks = {id(b): k for k, b in enumerate(blocks) if not isinstance(b, Table)}
    start_block = pos_in_blocks.get(id(body_paras[0]), 0) if body_paras else len(blocks)
    end_block = pos_in_blocks.get(id(body_paras[-1]), len(blocks) - 1) + 1 if body_paras else len(blocks)
    seq_blocks = blocks[start_block:end_block]
    # B-217③：Word 题注 → 图片/表格 `\caption`（判据与关联在 slcn/wordcaption.py）。
    # 只认「题注段与图片/表格**唯一相邻**（±1 个非空块）」；关联失败/有歧义 →
    # 保持现行为（占位 + 题注留正文）+ warning，绝不猜。
    _cap_items: list[dict] = []
    for _b in seq_blocks:
        if isinstance(_b, Table):
            _cap_items.append({"caption": None, "figure": False,
                               "table": True, "blank": False})
            continue
        _fig = _is_figure_para(_b, img_names)
        _cap = _caption_candidate(_b)
        _cap_items.append({"caption": _cap, "figure": _fig,
                           "table": False,
                           # B-219④(b)：空题注段不透明（要在 find_captions 里出 warning）
                           "blank": not _b.plain.strip() and not _fig
                                    and not (_cap and _cap.get("empty"))})
    _cap_by_idx, _cap_skip, _cap_warns = _find_captions(_cap_items)
    doc.warnings.extend(_cap_warns)
    idx = -1
    for para in seq_blocks:
        idx += 1
        if idx in _cap_skip:
            continue                     # 题注已并入 \caption：不再残留正文
        if isinstance(para, Table):
            tab_idx += 1
            _tc = _cap_by_idx.get(idx)
            out.append(table_env(para, tab_idx, img_names,
                                 caption=escape_latex(_tc["text"]) if _tc else None))
            doc.warnings.append(
                f"表格 {tab_idx}：{para.nrows} 行 × {para.ncols} 列已转为 tabular"
                + ("（含合并单元格，请核对）" if para.merged else "")
            )
            continue
        if idx in fig_marks:
            n = fig_marks[idx]
            doc.warnings.append(f"【需人工制图】原稿此处用 {n} 个字符拼成图示，已置为占位")
            out.append(
                "\n% " + "=" * 62 + "\n"
                f"% 【需人工制图】原稿此处用 {n} 个单字符段落拼出一张图，\n"
                "% Word 里没有内嵌图片对象，无法自动导出。请参照原稿重画后\n"
                "% 换成 figure 环境，例如：\n"
                "%   \\begin{figure}[htbp]\n"
                "%   \\centering\n"
                "%   \\includegraphics[width=0.85\\textwidth]{figX.pdf}\n"
                "%   \\caption{【需补充图题】}\\label{fig:X}\n"
                "%   \\end{figure}\n"
                "% " + "=" * 62 + "\n"
                "\n\\begin{center}\n"
                "  \\fbox{\\parbox{0.8\\textwidth}{\\centering "
                # 需要 3 个右花括号：关 textbf、关 parbox、关 fbox；
                # f-string 里每个字面 } 要写成 }}，所以这里写 6 个
                                                "\\textbf{【需人工制图：原稿此处为 %d 个字符拼成的图示】}}}" % n + "\n"
                "\\end{center}\n"
            )
            continue
        if any(idx > k and idx < k + c for k, c in fig_marks.items()):
            continue  # 已被上一张图的占位吸收
        _fc = _cap_by_idx.get(idx)
        # B-219⑤：段首定理类标签 → 定理环境（内容仍走 render_para，行内公式/脚注
        # 等既有路径复用；「注」不环境化，只 warning）。
        _theo = _theorem_env_of(para) or _proof_env_of(para)
        _theo_env, _render_obj = (_theo[0], _theo[1]) if _theo else (None, para)
        if _remark_prefix(para):
            doc.warnings.append(
                "注 未环境化（模板无未编号 remark 环境），保持原样（不猜）")
        latex, notes = render_para(_render_obj, fn_objs, doc.bib_entries, keymap, fn_pages,
                                   quote_threshold, img_names, fig_counter=fig_counter,
                                   c_class_union=c_class_union, num2key=fn_num2key,
                                   fig_caption=escape_latex(_fc["text"]) if _fc else None,
                                   eq_counter=eq_counter,
                                   caption_only=bool(_fc and _fc.get("inline")))
        doc.warnings.extend(notes)
        if not latex.strip():
            continue
        if _theo_env is not None:
            out.append(f"\n\\begin{{{_theo_env}}}\n{latex}\n\\end{{{_theo_env}}}\n")
            continue
        plain = escape_latex(para.plain).strip()
        if _HEAD1.match(plain):
            h = _HEAD1.sub("", plain).strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif (m_sp := _HEAD_CN_SPACE.match(plain)) and not plain.rstrip().endswith(
                ("。", "，", ",", "；", ";", "：", ":")) and not m_sp.group("t").startswith(
                ("个", "些", "种", "般", "旦", "直", "定", "方", "部", "边", "时",
                 "系", "起", "次", "年", "月", "日", "人", "事", "项", "条", "段",
                 "步", "层", "类", "是", "为", "般", "切")):
            # 87-9 访谈体：`一 非形式逻辑`（一/二/三 + 空格 + 短标题）
            h = m_sp.group("t").strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif _HEAD2.match(plain):
            h = _HEAD2.sub("", plain).strip()
            doc.headings.append((2, h))
            out.append(f"\n\\subsection{{{h}}}\n")
        elif (m_cn := _HEAD_CN_ARABIC.match(plain)) and _is_cn_numbered_heading(m_cn.group("t")):
            # 新式中文标题 `1 引言`：编号要去掉（人工成品就是 `\section{引言}`）
            h = m_cn.group("t").strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif _is_cn_dot_heading(plain) and id(para) in lone_cn_dot:
            # `1. 引言` / `1.引言`（点式编号 + 汉字）：与 `1 引言` 同族，判据共用
            # `_is_cn_numbered_heading`（反例见 _is_cn_dot_heading）。层级与「连排名单」
            # 两条上下文判据见 ⑤.6；`id(para)` 落选 = 这一行属于连排名单，留在正文里。
            h = _HEAD_CN_DOT.match(plain).group("t").strip()
            lv = 2 if cn_num_top else 1
            doc.headings.append((lv, h))
            out.append(f"\n\\subsection{{{h}}}\n" if lv == 2 else f"\n\\section{{{h}}}\n")
        elif id(para) in plain_en_headings:
            h = plain_en_headings[id(para)]
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        else:
            lv, h = classify_en_heading(plain)
            if lv == 1:
                doc.headings.append((1, h))
                out.append(f"\n\\section{{{h}}}\n")
            elif lv == 2:
                doc.headings.append((2, h))
                out.append(f"\n\\subsection{{{h}}}\n")
            else:
                out.append(latex + "\n")
    doc.body = "\n".join(out).strip()
    # B-233：Word 文末手写 `\bibitem`（旧两通道 0 条时）自动转换为规范 bib 条目；
    # 默认开，`--no-bibitem-convert` 在 convert.py 统一关闭；fail-soft 见函数内。
    from .wordentry import convert_bibitem_refs

    convert_bibitem_refs(doc)
    return doc


