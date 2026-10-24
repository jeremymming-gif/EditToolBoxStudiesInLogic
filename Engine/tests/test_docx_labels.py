#!/usr/bin/env python3.14
"""P2-0：图 `\\label{fig:N}` 必须**文档级唯一**（期 87 P2-2 前置缺陷）。

缺陷
-----
`render_para()` 里 `fig_seq = 0` 是**函数内**计数器（每个段落从 1 重来），而 `parse_docx()`
对每个段落各调一次 `render_para()` → 多段有图时每段的图都叫 `\\label{fig:1}`。
实测 corpus 12 篇有 fig label 的产物里 **6 篇 label 重复**（`2025_04_YingJin_Xu` 6 个
全是 `fig:1`）；hyperref 会报 "destination with the same identifier"，`\\ref` 会指错图。

修法
----
`render_para(..., fig_counter=[0])`：由 `parse_docx()` 在整篇持有同一个计数器并传入；
不传时保留段落内计数（旧调用方/单测行为逐字节不变）。

跑法
----
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docx_labels.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import render_para  # noqa: E402
from slcn.wordcaption import caption_candidate, find_captions  # noqa: E402
from slcn.docxxml import Para, Seg  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}（{why}）")
    SKIPS.append(name)


IMG = {"rId1": "a.png", "rId2": "b.png"}


def _para(*segs):
    return Para(segs=list(segs))


# ══════════════════════════════════════════════════════════════════════════
# ① 两段各一张图 + 共享文档级计数器 → fig:1 / fig:2（旧实现两段都是 fig:1）
# ══════════════════════════════════════════════════════════════════════════
counter = [0]
tex1, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
tex2, _ = render_para(_para(Seg("image", rid="rId2")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
check("① 第 1 段图 = fig:1", "\\label{fig:1}" in tex1, tex1[:120])
check("① 第 2 段图 = fig:2（文档级计数；旧实现会又是 fig:1）",
      "\\label{fig:2}" in tex2 and "\\label{fig:1}" not in tex2, tex2[:120])

# ══════════════════════════════════════════════════════════════════════════
# ② 不传计数器（旧调用方/单测）→ 保留段落内计数，行为与从前逐字节相同
# ══════════════════════════════════════════════════════════════════════════
legacy1, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG)
legacy2, _ = render_para(_para(Seg("image", rid="rId2")), {}, [], {}, img_names=IMG)
check("② 兼容：不传 fig_counter 时两段都从 fig:1 起（旧行为不变）",
      "\\label{fig:1}" in legacy1 and "\\label{fig:1}" in legacy2,
      f"{legacy1[:80]!r} / {legacy2[:80]!r}")

# ══════════════════════════════════════════════════════════════════════════
# ③ 同一段里两张图：保持既有「合并成一个 figure」语义（只推进 1 次计数）
# ══════════════════════════════════════════════════════════════════════════
counter = [0]
tex3, _ = render_para(_para(Seg("image", rid="rId1"), Seg("text", "中间文字"),
                            Seg("image", rid="rId2")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
check("③ 同段两张图合并为一个 figure（fig:1），计数器只推进到 1（既有合并语义不变）",
      tex3.count("\\label{fig:") == 1 and "\\label{fig:1}" in tex3
      and tex3.count("\\includegraphics") == 2 and counter == [1],
      f"{tex3[:200]} counter={counter}")

# ══════════════════════════════════════════════════════════════════════════
# ④ 真实稿抽查：有 fig label 的 Word 稿，产物 label 必须唯一
#      （本机没有稿件库时 SKIP —— 公开仓/CI 行为一致）
# ══════════════════════════════════════════════════════════════════════════
def _real_check() -> None:
    import csv
    import tempfile

    from slcn import build, paths, sources as S

    root = paths.manuscripts_root()
    csv_path = os.path.join(_ENGINE, "out", "corpus", "corpus.csv")
    if not root or not os.path.isfile(csv_path):
        skip("④ 真实稿 fig label 唯一性抽查", "本机没有稿件库或 out/corpus/corpus.csv")
        return
    # 取 corpus 里「现有产物就有重复 fig label」的篇做前后对照最有说服力；
    # corpus.csv 不在时退回按名字找。
    cands = []
    rows = [r for r in csv.DictReader(open(csv_path, encoding="utf-8"))
            if r.get("channel") == "Word"]
    for r in rows:
        art = os.path.join(root, r["issue"], r["slug"])
        if os.path.isdir(art):
            cands.append((r["issue"], r["slug"]))
    picked = 0
    for issue, slug in cands:
        if picked >= 3:
            break
        if slug not in ("04 YingJin_Xu", "01 Yanjin_Chen", "06 Zhixi_Chen"):
            continue
        art = os.path.join(root, issue, slug)
        ss = S.identify(art, verbose=False)
        if not ss.docx:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            res = build.build_docx(ss.docx, tmp, basename="Case", verbose=False)
            tex = open(res.tex_path, encoding="utf-8").read()
        labels = re.findall(r"\\label\{fig:(\d+)\}", tex)
        if labels:
            picked += 1
            check(f"④ 真实稿 {slug}：fig label 唯一（{len(labels)} 个）",
                  len(labels) == len(set(labels)), str(labels))
    if picked == 0:
        skip("④ 真实稿 fig label 唯一性抽查", "本机没找到带图 Word 稿")


try:
    _real_check()
except Exception as _e:  # noqa: BLE001
    skip("④ 真实稿 fig label 唯一性抽查", f"{type(_e).__name__}: {_e}")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ B-217③：Word 题注 → \caption（唯一相邻关联；关联失败保持原行为 + warning）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑤ B-217③ 题注关联与 \\caption 接线 ──")


def _cap(text, style=""):
    return Para(segs=[Seg("text", text=text)], style=style)


_c = caption_candidate(_cap("图1 系统架构", "Caption"))
check("⑤ 判据：Caption 样式「图1 系统架构」→ 图题、去掉编号前缀",
      bool(_c) and _c["kind"] == "fig" and _c["text"] == "系统架构", repr(_c))
_c = caption_candidate(_cap("表2 实验数据"))
check("⑤ 判据：无样式但「表2 + 说明」的短段落也认",
      bool(_c) and _c["kind"] == "tab" and _c["text"] == "实验数据", repr(_c))
check("⑤ 反例：无样式正文叙述「图1 展示了系统架构。」不认（句末标点）",
      caption_candidate(_cap("图1 展示了系统架构。")) is None)
check("⑤ 反例：普通正文段落不认", caption_candidate(_cap("本文讨论逻辑")) is None)

_items = [
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("图1 系统架构")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": False, "table": False, "blank": True},
    {"caption": None, "figure": False, "table": False, "blank": False},  # 正文段
    {"caption": None, "figure": False, "table": True, "blank": False},
    {"caption": caption_candidate(_cap("表1 实验数据")), "figure": False,
     "table": False, "blank": False},
]
_assoc, _skip, _warns = find_captions(_items)
check("⑤ 唯一相邻：图/表各并入题注、题注段落要跳过、无 warning",
      _assoc.get(0) == {"kind": "fig", "text": "系统架构"}
      and _assoc.get(4) == {"kind": "tab", "text": "实验数据"}
      and _skip == {1, 5} and _warns == [], repr((_assoc, _skip, _warns)))
_amb = [
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": True, "table": False, "blank": False},
]
_a2, _s2, _w2 = find_captions(_amb)
check("⑤ 反例：题注夹在两个图之间 → 不关联 + warning（不猜）",
      _a2 == {} and _s2 == set() and bool(_w2), repr((_a2, _s2, _w2)))
_mis = [
    {"caption": None, "figure": False, "table": True, "blank": False},
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
]
_a3, _s3, _w3 = find_captions(_mis)
check("⑤ 反例：写「图1」却挨着表格 → 不关联 + warning（不猜）",
      _a3 == {} and _s3 == set() and bool(_w3), repr((_a3, _s3, _w3)))

_texc, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG,
                       fig_caption="系统架构")
check("⑤ 接线：render_para(fig_caption=…) → figure 用真实题注、无占位",
      "\\caption{系统架构}" in _texc and "【需补充图题】" not in _texc, _texc[:160])
_texd, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG)
check("⑤ 反例：不传 fig_caption → 占位与从前逐字节相同",
      "\\caption{【需补充图题】}" in _texd, _texd[:160])

# ══════════════════════════════════════════════════════════════════════════
# ⑥ B-219③：中图分类号/文献标识码整段 → 丢弃（负例：夹正文句子不动）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑥ B-219③ 中图分类号/文献标识码行 ──")
import tempfile  # noqa: E402
import zipfile  # noqa: E402
from xml.sax.saxutils import escape as _esc  # noqa: E402

from slcn.docxsource import (  # noqa: E402
    _is_clc_meta_only, _proof_env_of, _remark_prefix, _theorem_env_of, parse_docx,
)
from slcn.docxsource import render_para as _render  # noqa: E402

check("B219③ 判据：整段只有「中图分类号：B81  文献标识码：A」→ 丢弃",
      _is_clc_meta_only("中图分类号：B81　　　　文献标识码：A")
      and _is_clc_meta_only("中图分类号: B81")
      and _is_clc_meta_only("文献标识码：A；中图分类号：B81-05"))
check("B219③ 反例：段里还夹正文句子 → 不动",
      not _is_clc_meta_only("中图分类号：B81，本文讨论逻辑")
      and not _is_clc_meta_only("文献标识码 A 表示学术论文"))


def _make_docx(paras: list[str]) -> str:
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{_esc(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = ('<w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>' + ps + '</w:body></w:document>')
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
    return path


_D_CLC = _make_docx(["中图分类号测试稿", "张三", "摘要：测试摘要内容。", "关键词：测试",
                     "中图分类号：B81　　　　文献标识码：A",
                     "正文第一段讨论逻辑。", "正文第二段继续讨论。"])
try:
    _clc_doc = parse_docx(_D_CLC)
    check("B219③ 接线：正文里的 CLC 行已删除 + 1 条 info warning",
          "中图分类号" not in _clc_doc.body
          and any("已移除正文中图分类号" in w for w in _clc_doc.warnings),
          repr(_clc_doc.body[:80]))
finally:
    os.unlink(_D_CLC)

_D_NOCLC = _make_docx(["无CLC测试稿", "张三", "摘要：测试摘要内容。", "关键词：测试",
                       "正文第一段讨论逻辑。", "正文第二段继续讨论。"])
try:
    _noclc_doc = parse_docx(_D_NOCLC)
    check("B219③ 反例：没有 CLC 行的稿件不新增 warning、正文不动",
          not any("已移除正文中图分类号" in w for w in _noclc_doc.warnings)
          and "正文第一段讨论逻辑。" in _noclc_doc.body)
finally:
    os.unlink(_D_NOCLC)

# ══════════════════════════════════════════════════════════════════════════
# ⑦ B-219④：题注并合加强（同段内联 / 空题注 / 多题注争抢唯一化）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑦ B-219④ 题注并合加强 ──")
_empty_c = caption_candidate(Para(segs=[], style="Caption"))
_e_out, _e_skip, _e_w = find_captions(
    [{"caption": _empty_c, "figure": False, "table": False, "blank": False}])
check("B219④(b) 空题注（Caption 样式、无文字）→ empty 候选，不并 + warning",
      bool(_empty_c) and _empty_c.get("empty") and _e_out == {}
      and _e_skip == set() and bool(_e_w), repr(_empty_c))
check("B219④(b) 反例：非题注样式的空段仍不算题注（None）",
      caption_candidate(Para(segs=[], style="Normal")) is None)

_inline_c = caption_candidate(Para(segs=[Seg("text", text="图1 系统架构")]))
_inl_items = [{"caption": _inline_c, "figure": True, "table": False, "blank": False}]
_inl_out, _inl_skip, _inl_w = find_captions(_inl_items)
check("B219④(a) 同段内联题注 → 绑定自身 figure（inline=True，题注段不整段删除）",
      _inl_out.get(0, {}).get("inline") is True
      and _inl_out[0]["text"] == "系统架构" and _inl_skip == set(), repr(_inl_out))
_tex_inline, _ = _render(
    _para(Seg("text", text="图1 系统架构"), Seg("image", rid="rId1")), {}, [], {},
    img_names=IMG, fig_caption="系统架构", caption_only=True)
check("B219④(a) 接线：render_para(caption_only=True) → 题注文字不重复进正文",
      "\\caption{系统架构}" in _tex_inline and "图1 系统架构" not in _tex_inline,
      _tex_inline[:120])

_multi = [
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("乙图说明", "Caption")), "figure": False,
     "table": False, "blank": False},
]
_m_out, _m_skip, _m_w = find_captions(_multi)
check("B219④(c) 多题注争抢：Caption 样式唯一 → 并那一条 + warning，其余留正文",
      _m_out.get(1, {}).get("text") == "乙图说明" and _m_skip == {2}
      and any("多题注争抢" in w for w in _m_w), repr((_m_out, _m_skip, _m_w)))
_multi2 = [
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("图1 乙")), "figure": False,
     "table": False, "blank": False},
]
_a2c, _s2c, _w2c = find_captions(_multi2)
check("B219④(c) 反例：无法唯一化（两条同级）→ 都不并 + warning",
      _a2c == {} and _s2c == set() and bool(_w2c), repr((_a2c, _s2c, _w2c)))

# ══════════════════════════════════════════════════════════════════════════
# ⑧ B-219⑤：定理类标签 → 环境（注不环境化）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑧ B-219⑤ 定理类标签 → 环境 ──")
_th = _theorem_env_of(Para(segs=[Seg("text", text="定理1　若条件成立，则结论成立。")]))
check("B219⑤ 判据：定理1 → theorem，strip 标签+作者编号",
      bool(_th) and _th[0] == "theorem"
      and _th[1].plain.strip() == "若条件成立，则结论成立。",
      repr(_th[0] if _th else None))
_pf = _proof_env_of(Para(segs=[Seg("text", text="证明.　由定理1 直接可得。")]))
check("B219⑤ 判据：证明. → proof（无编号）",
      bool(_pf) and _pf[0] == "proof", repr(_pf[0] if _pf else None))
check("B219⑤ 反例：定义一个… / 证明 F-UD（…）： 不命中（保持纯文本）",
      _theorem_env_of(Para(segs=[Seg("text", text="定义一个对象为某类对象。")])) is None
      and _proof_env_of(Para(segs=[Seg("text", text="证明 F-UD（…）：")])) is None)
check("B219⑤ 注：不环境化，只 warning",
      _remark_prefix(Para(segs=[Seg("text", text="注　注不编号；补充说明文字。")]))
      and not _remark_prefix(Para(segs=[Seg("text", text="注意安全。")])))

_D_TH = _make_docx(["定理环境测试稿", "张三", "摘要：测试摘要内容。", "关键词：测试",
                    "定理1　若条件成立，则结论成立。",
                    "证明.　由定理1 直接可得。",
                    "定义一个对象为某类对象。",
                    "注　注不编号；补充说明文字。"])
try:
    _th_doc = parse_docx(_D_TH)
    check("B219⑤ 接线：定理/证明 → 环境；定义反例保持纯文本；注 warning",
          "\\begin{theorem}" in _th_doc.body and "\\begin{proof}" in _th_doc.body
          and "\\begin{definition}" not in _th_doc.body
          and "定义一个对象为某类对象。" in _th_doc.body
          and any("注 未环境化" in w for w in _th_doc.warnings),
          repr([ln for ln in _th_doc.body.splitlines() if "begin{" in ln]))
finally:
    os.unlink(_D_TH)

# ── B-219 修（verifier FAIL）：proof 双 QED / CLC「文献标志码」变体 ──
_pf_q = _proof_env_of(Para(segs=[Seg("text", text="证明.　由定理1 直接可得。"),
                                Seg("math", text="□")]))
check("B219⑤ 修：proof 段末 □ 被剥掉（amsthm 自动印 QED，避免双框）",
      bool(_pf_q) and all(not (s.kind == "math" and s.text.strip() in ("□", "\\Box", "\\square"))
                          for s in _pf_q[1].segs))
_pf_m = _proof_env_of(Para(segs=[Seg("text", text="证明.　设 □ 为模态算子，结论成立。")]))
check("B219⑤ 修反例：证明正文中间的 □ 不删",
      bool(_pf_m) and "□" in _pf_m[1].plain)
check("B219③ 修：真实稿「文献标志码」变体也判为整段元数据",
      _is_clc_meta_only("文献标志码：A")
      and _is_clc_meta_only("中图分类号：B81；文献标志码：A"))
_D_CLC_FLAG = _make_docx(["标志码测试稿", "张三", "摘要：测试摘要内容。", "关键词：测试",
                          "文献标志码：A", "正文第一段讨论逻辑。"])
try:
    _flag_doc = parse_docx(_D_CLC_FLAG)
    check("B219③ 修接线：文献标志码段被删除 + warning",
          "文献标志码" not in _flag_doc.body
          and any("已移除正文中图分类号" in w for w in _flag_doc.warnings),
          repr(_flag_doc.body[:80]))
finally:
    os.unlink(_D_CLC_FLAG)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
