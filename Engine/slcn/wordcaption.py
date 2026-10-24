r"""B-217③：Word 题注段落 → 图片/表格 ``\caption``（唯一相邻关联，不猜）。

背景（B-215 实测）：Word 稿里图下/表上的题注（「题注/Caption」样式段落，
或纯文本「图1 …」「表2 …」）既不进 ``\caption``，还**残留在正文**里——
产物同时出现「【需补充图题】」占位和一行正文题注。编辑每次都要手工并合。

本模块只做**判据与关联**（纯函数，不碰 LaTeX），由 ``docxsource`` 薄接线：
  · ``caption_candidate(para)``：这个段落是不是题注？返回
    ``{"kind": "fig"|"tab"|None, "text": 去掉「图N」「表N」编号前缀后的说明文字,
    "raw": 原文}``；不是题注返回 ``None``；
  · ``is_figure_para(para, img_names)``：这个段落里有**图片对象**吗
    （VML 公式预览图 ``0 < h_pt <= 60`` 不算，与 ``render_para`` 的 flush 判据一致）；
  · ``find_captions(items)``：按文档顺序做**唯一相邻**关联（±1 个非空块），
    返回 ``(目标下标 → {"kind", "text"}, 要跳过的题注下标集合, warning 清单)``。

判据（spec §2.3 / §4 风险②，宁缺勿编）：
  ① 题注候选 = Word 样式名含 ``Caption``/``题注`` 的段落，或**明确**以
     ``图N``/``表N``（英文 ``Figure N``/``Table N``）开头的短段落
     （无样式时要求：无句末标点、≤120 字，避免把正文里「图1 展示了…。」当题注）；
  ② 关联只认**唯一相邻**：题注前后各一个非空块里，图片/表格对象**恰好一个**；
     0 个（找不到）或 ≥2 个（有歧义）、或被多个题注争抢 → 保持原行为 + warning；
  ③ 题注写明「图/表」时的类型必须与相邻对象一致（写「图N」挨着表格 → 不并）；
  ④ 去掉「图N」「表N」编号前缀（编号由 ``\label``/LaTeX 生成），说明文字为空 → 不关联。
"""

from __future__ import annotations

import re

#: Word 题注样式名（中文版 Word 是「题注」，英文版是 Caption；样式 id 也是 Caption）
CAPTION_STYLE_RE = re.compile(r"caption|题注", re.I)

#: 「图N」「表N」「Figure N」「Table N」+ 可选分隔 + 说明文字
_KIND_RE = re.compile(
    r"^\s*(?P<kind>图|表|Figure|Fig\.?|Table)\s*"
    r"(?P<num>\d{1,3}(?:[.\-–—]\d{1,3})?)\s*[.．、:：\-–—]?\s*(?P<rest>.*)$",
    re.I)

_CN_KIND = {"图": "fig", "表": "tab"}
_EN_KIND = {"figure": "fig", "fig": "fig", "fig.": "fig", "table": "tab"}


def _kind_of(word: str) -> str | None:
    w = word.strip()
    return _CN_KIND.get(w) or _EN_KIND.get(w.lower()) or _EN_KIND.get(w.lower().rstrip("."))


def caption_candidate(para) -> dict | None:
    """段落 → 题注候选 dict；不是题注返回 ``None``（**不改变该段落的既有处理**）。

    B-219④(b)：``Caption/题注`` 样式段但文字为空或只剩「图N/表N」→ 返回
    ``{"empty": True, ...}``（find_captions 只发 warning，不并、不删除）。
    """
    text = (getattr(para, "plain", "") or "").strip()
    style = str(getattr(para, "style", "") or "")
    styled = bool(CAPTION_STYLE_RE.search(style))
    if not text:
        if styled:
            return {"kind": None, "text": "", "raw": "", "styled": True, "empty": True}
        return None
    m = _KIND_RE.match(text)
    if m:
        rest = (m.group("rest") or "").strip()
        if not rest:
            if styled:
                return {"kind": _kind_of(m.group("kind")), "text": "", "raw": text,
                        "styled": True, "empty": True}
            return None                     # 「图1」后面没说明文字：不并（保持原行为）
        if styled:
            return {"kind": _kind_of(m.group("kind")), "text": rest, "raw": text,
                    "styled": True}
        # 无样式：必须是**短、无句末标点**的「图N/表N + 说明」，避免正文叙述误伤
        if len(text) > 120 or re.search(r"[。！？；!?;]", text):
            return None
        return {"kind": _kind_of(m.group("kind")), "text": rest, "raw": text,
                "styled": False}
    if styled:
        # 题注样式但正文没写「图N/表N」：说明文字就是整段，类型由相邻对象决定
        if len(text) > 300:
            return None
        return {"kind": None, "text": text, "raw": text, "styled": True}
    return None


def is_figure_para(para, img_names: dict[str, str]) -> bool:
    """段落里有没有**图对象**（排除 VML 公式预览图：``0 < h_pt <= 60``）。"""
    for seg in getattr(para, "segs", []) or []:
        if getattr(seg, "kind", "") != "image":
            continue
        rid = getattr(seg, "rid", "")
        if rid not in (img_names or {}):
            continue
        h = getattr(seg, "h_pt", 0.0)
        if not (0 < h <= 60):
            return True
    return False


def find_captions(items: list[dict]) -> tuple[dict[int, dict], set[int], list[str]]:
    """按文档顺序做「题注 ↔ 图片/表格」的**唯一相邻**关联。

    ``items``：每个正文块一个 dict，键为 ``caption``（``caption_candidate`` 的
    返回值或 ``None``）、``figure``（bool）、``table``（bool）、``blank``（bool）。
    空段落（``blank=True``）在求相邻时**透明**（Word 稿常见图/表与题注之间夹空行）。

    返回 ``(关联表 {目标下标: {"kind", "text"}}, 跳过的题注下标集合, warning 清单)``。

    B-219④ 增强（默认路径仍只在命中新形态时生效）：
      (a) 同段内联：段落既有图对象、又以「图N…」说明文字开头 → 该段自身的图用
          这一条题注（返回项带 ``"inline": True``，调用方据此不重复渲染题注文字）；
      (b) 空题注：Caption 样式但无说明文字（或只剩「图N/表N」）→ 只 warning，
          不并、不删除（保持占位）；
      (c) 多题注争抢同一目标 → 先按「Caption 样式唯一」、再按「类型与目标一致唯一」
          挑 1 条（并 + warning），其余候选留在正文；无法唯一化 → 都不并 + warning。
    """
    n = len(items)
    order = [i for i in range(n) if not items[i].get("blank")]
    pos = {idx: k for k, idx in enumerate(order)}
    out: dict[int, dict] = {}
    skip: set[int] = set()
    warns: list[str] = []

    # (b) 空题注：只 warning，绝不并、绝不删（题注段本身照常留在正文）
    for i in order:
        _c = items[i].get("caption")
        if _c and _c.get("empty"):
            raw = (_c.get("raw") or "").strip()
            warns.append(f"空题注「{raw[:20] or '（空）'}」保持占位（保持原样（不猜））")

    cand = {i: items[i]["caption"] for i in order
            if items[i].get("caption") and not items[i]["caption"].get("empty")}
    target: dict[int, str] = {}
    for i in order:
        if items[i].get("table"):
            target[i] = "tab"
        elif items[i].get("figure"):
            target[i] = "fig"

    # (a) 同段内联题注：段落 = 图对象 + 段首「图N…」文字 → 绑定自身
    inline: set[int] = set()
    for i in sorted(cand):
        c = cand[i]
        if items[i].get("figure") and (not c["kind"] or c["kind"] == "fig"):
            out[i] = {"kind": "fig", "text": c["text"], "inline": True}
            inline.add(i)

    # 每个题注候选的 ±1 非空邻居里有哪些目标。
    # B-217③ 修（模板实测）：题注**写明了「图N/表N」**时只统计**同类型**目标——
    # Word 模板里图与表常紧邻，只按 ±1 会把「图1」判成同时挨着图+表（CN/EN 两模板
    # 全部因此不并）。这里先按前缀做类型过滤，再要求该类型内 ±1 唯一。
    adj: dict[int, list[int]] = {}
    for i in cand:
        if i in inline:
            continue                     # 已绑定自身，不再抢邻居
        c = cand[i]
        k = pos[i]
        ns: list[int] = []
        for dk in (-1, 1):
            if 0 <= k + dk < len(order):
                j = order[k + dk]
                if j in target and (not c["kind"] or target[j] == c["kind"]):
                    ns.append(j)
        adj[i] = ns

    # 先把候选按目标分组（每个候选仍要求「相邻目标唯一 + 类型一致」）。
    by_target: dict[int, list[int]] = {}
    for i in sorted(adj):
        c = cand[i]
        ns = adj[i]
        if not ns:
            _what = ("图" if c["kind"] == "fig" else "表") if c["kind"] else "图片/表格"
            warns.append(
                f"题注「{c['raw'][:20]}」旁边没有{_what}（±1 段、同类），保持原样（不猜）")
            continue
        if len(ns) > 1:
            warns.append(
                f"题注「{c['raw'][:20]}」同时挨着 {len(ns)} 个图片/表格，关联不唯一，"
                "保持原样（不猜）")
            continue
        j = ns[0]
        if c["kind"] and c["kind"] != target[j]:
            what = "图" if c["kind"] == "fig" else "表"
            warns.append(
                f"题注「{c['raw'][:20]}」写的是{what}题，但相邻对象不是{what}，"
                "保持原样（不猜）")
            continue
        by_target.setdefault(j, []).append(i)

    # (c) 每个目标最多并 1 条；争抢时按样式/类型唯一化，否则都不并。
    for j in sorted(by_target):
        ids = by_target[j]
        tkind = target[j]
        if j in out:
            warns.append(
                f"图片/表格同时被同段内联题注与相邻题注候选关联，保持原样（不猜）")
            continue
        if len(ids) == 1:
            i = ids[0]
            out[j] = {"kind": tkind, "text": cand[i]["text"]}
            skip.add(i)
            continue
        _styled = [i for i in ids if cand[i].get("styled")]
        _typed = [i for i in ids if cand[i]["kind"] == tkind]
        if len(_styled) == 1:
            picked, basis = _styled[0], "Caption 样式唯一"
        elif len(_typed) == 1:
            picked, basis = _typed[0], "题注类型与目标一致唯一"
        else:
            warns.append(
                f"题注「{cand[ids[0]]['raw'][:20]}」等 {len(ids)} 条同时被同一图片/表格"
                "关联，无法唯一确定，保持原样（不猜）")
            continue
        out[j] = {"kind": tkind, "text": cand[picked]["text"]}
        skip.add(picked)
        warns.append(
            f"多题注争抢，已并入 1 条（判据：{basis}），其余 {len(ids) - 1} 条保留正文")
    return out, skip, warns
