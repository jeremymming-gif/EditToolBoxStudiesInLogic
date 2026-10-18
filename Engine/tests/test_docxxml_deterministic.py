#!/usr/bin/env python3.14
r"""docxxml 解析的**确定性**回归（R5；**第 125 条**，2026-10-18）。

缺陷
----
`slcn/docxxml.py` 曾用内建 `id()` 给 lxml 元素去重（`seen_drawings` / `seen_math`）。
lxml 的元素代理是临时对象：没有引用就会被回收，同一个 XML 节点再访问时会换一个新代理，
`id()` 跟着变化，而且旧地址还会被其它节点复用。后果不是「多算几遍」，而是：
  ① 同一篇稿子**不同进程**跑，产物里的公式数会飘。实测 05 Yang_Hu 的 math 段
     12 个进程里出现 **245 / 249 / 254** 三种结果（正确值应为 276 = 每个 oMath 收一次）；
  ② 靠 `id()` 巧合「去重」= 命中旧地址的整条公式**从产物里消失**，没命中的又可能**重复**；
  ③ corpus 的 Word 中位相似度因此 ±0.03pp 不可复现（`05 Yang_Hu/raw_ratio`，R5）。

修法（第 125 条，只改去重，不改处理范围）
------------------------------------------
1. 去重改用**元素身份**：集合里存元素本身（持有代理引用 ⇒ 同一节点恒为同一代理）；
2. 只登记 `_run_segments()` **真正处理过**的直接子 `<m:oMath>`；
   `<w:r>→<m:oMathPara>→<m:oMath>`（Word 文本框里的公式）不归它处理，归外层按文档顺序收一次。
   旧实现把「run 内所有 oMath」都登记，登记范围过大，是丢公式的另一半根因。

本套件钉住（纯离线；不跑 xelatex / corpus / build.sh）：
  ① 合成 docx 上「每个公式恰好收一次」：run 直接子公式 / run 内嵌 oMathPara /
     段落级 oMathPara / 段落直接子公式 / 文本框（mc:Fallback）公式 / 表格单元格公式 = 6 条；
     图形（run 直接子 + mc:AlternateContent 内嵌）恰好 2 个占位，不重复；
  ② 反复解析（中间丢引用 + `gc.collect()`）结果逐字相同；
  ③ 静态守卫：`docxxml.py` 不许再用内建 `id()` / `hash()` 做去重（AST 检查，注释里提到不受影响）；
  ④ 真实稿抽查（稿件库在时；CI 上 SKIP）：05 Yang_Hu 连解析三次签名一致、公式段 = 276、
     矢量形状占位 = 15。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docxxml_deterministic.py
"""
import ast
import gc
import os
import shutil
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import docxxml  # noqa: E402
from slcn.docxxml import Table, read_blocks, segs_to_text  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
TMP = tempfile.mkdtemp(prefix="slcn_det125_")
_DOCXXML = os.path.join(_ENGINE, "slcn", "docxxml.py")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 合成 docx 夹具：一个 zip + 一份手写 word/document.xml ─────────────────────
# read_blocks() 只读 word/document.xml，不需要完整的 OOXML 包。

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)
_WP = 'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
_A_GRAPHIC = (
    "<a:graphic><a:graphicData "
    'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
    "<wps:wsp><wps:cNvSpPr/><wps:spPr/></wps:wsp>"
    "</a:graphicData></a:graphic>"
)


def _om(t):
    return f"<m:oMath><m:r><m:t>{t}</m:t></m:r></m:oMath>"


def _omp(t):
    return f"<m:oMathPara>{_om(t)}</m:oMathPara>"


def _run(inner):
    return f"<w:r>{inner}</w:r>"


def _para(inner):
    return f"<w:p>{inner}</w:p>"


def _drawing():
    return f"<w:drawing><wp:inline {_WP}>{_A_GRAPHIC}</wp:inline></w:drawing>"


def _make_docx(path):
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>"
        # 1) run 的直接子 oMath（归 _run_segments 处理）
        + _para(_run("<w:t>甲</w:t>") + _run(_om("a")))
        # 2) run 内嵌 oMathPara（Word 文本框型：不归 _run_segments，归外层）
        + _para(_run("<w:t>乙</w:t>" + _omp("b")))
        # 3) 段落级 oMathPara（独立公式行）
        + _para(_omp("c"))
        # 4) 段落直接子 oMath
        + _para(_om("d"))
        # 5) run 里同时有「直接子 drawing」+「mc:AlternateContent 内嵌 drawing」
        #    + Fallback 里的文本框公式
        + _para(
            _run(
                "<w:t>戊</w:t>" + _drawing()
                + '<mc:AlternateContent><mc:Choice Requires="wps">'
                + _drawing()
                + "<mc:Fallback><w:pict><v:shape/><w:txbxContent>"
                + _para(_omp("e"))
                + "</w:txbxContent></w:pict></mc:Fallback>"
                + "</mc:Choice></mc:AlternateContent>"
            )
        )
        # 6) 表格单元格里的公式
        + "<w:tbl><w:tr><w:tc>" + _para(_om("f")) + "</w:tc></w:tr></w:tbl>"
        + "<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)


def _snapshot(blocks):
    """把 read_blocks() 的结果压成可逐字比较的签名（含公式文本与表格单元格）。"""
    snap = []
    for b in blocks:
        if isinstance(b, Table):
            snap.append(("T", tuple(
                tuple((s.kind, s.text) for s in c.segs) for row in b.rows for c in row)))
        else:
            snap.append(("P", b.style, tuple((s.kind, s.text) for s in b.segs)))
    return snap


def _count(blocks, kind):
    n = 0
    for b in blocks:
        if isinstance(b, Table):
            n += sum(1 for row in b.rows for c in row for s in c.segs if s.kind == kind)
        else:
            n += sum(1 for s in b.segs if s.kind == kind)
    return n


# ══════════════════════════════════════════════════════════════════════════
# ① 每个公式 / 图形恰好收一次（合成夹具）
# ══════════════════════════════════════════════════════════════════════════
FIXTURE = os.path.join(TMP, "fixture.docx")
_make_docx(FIXTURE)
_blocks = read_blocks(FIXTURE)
_paras = [b for b in _blocks if not isinstance(b, Table)]
_tables = [b for b in _blocks if isinstance(b, Table)]

check("① 段落数 = 5、表格数 = 1",
      len(_paras) == 5 and len(_tables) == 1, f"{len(_paras)} / {len(_tables)}")
check("① 公式段合计 = 6（每个 oMath 恰好一次，不多不少）",
      _count(_blocks, "math") == 6, f"{_count(_blocks, 'math')}")
check("① 图形占位 = 2（直接子 + mc:AlternateContent 内嵌各一次）",
      _count(_blocks, "shape") == 2, f"{_count(_blocks, 'shape')}")
check("① 没有图片段（夹具里没有 a:blip）", _count(_blocks, "image") == 0)
check("① 段落 1：文字 + run 直接子公式各一次",
      [s.kind for s in _paras[0].segs] == ["text", "math"]
      and segs_to_text(_paras[0].segs) == "甲$a$", repr(segs_to_text(_paras[0].segs)))
check("① 段落 2：run 内嵌 oMathPara 的公式不丢",
      [s.kind for s in _paras[1].segs] == ["text", "math"]
      and segs_to_text(_paras[1].segs) == "乙$b$", repr(segs_to_text(_paras[1].segs)))
check("① 段落 3/4：段落级 oMathPara / 直接子公式各一次",
      segs_to_text(_paras[2].segs) == "$c$" and segs_to_text(_paras[3].segs) == "$d$",
      repr((segs_to_text(_paras[2].segs), segs_to_text(_paras[3].segs))))
check("① 段落 5：直接子图形 + 内嵌图形 + Fallback 文本框公式",
      [s.kind for s in _paras[4].segs] == ["text", "shape", "shape", "math"]
      and segs_to_text(_paras[4].segs) == "戊$e$", repr(segs_to_text(_paras[4].segs)))
check("① 表格单元格公式 = $f$（且只收一次）",
      segs_to_text(_tables[0].rows[0][0].segs) == "$f$",
      repr(segs_to_text(_tables[0].rows[0][0].segs)))

# ══════════════════════════════════════════════════════════════════════════
# ② 反复解析（丢引用 + gc）结果逐字相同 —— 旧实现会随机丢/重公式
# ══════════════════════════════════════════════════════════════════════════
_sigs = []
for _ in range(4):
    _sigs.append(_snapshot(read_blocks(FIXTURE)))
    gc.collect()  # 让 lxml 元素代理彻底回收，逼出「换新代理、id() 漂移」
check("② 合成夹具连解析 4 次签名完全一致（确定性）",
      all(s == _sigs[0] for s in _sigs[1:]),
      f"{len({repr(s) for s in _sigs})} 种结果" if any(s != _sigs[0] for s in _sigs[1:]) else "")

# ══════════════════════════════════════════════════════════════════════════
# ③ 静态守卫：不许再用内建 id() / hash() 做元素去重
# ══════════════════════════════════════════════════════════════════════════
with open(_DOCXXML, encoding="utf-8") as _f:
    _src = _f.read()
_offenders = []
for _node in ast.walk(ast.parse(_src)):
    if (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name)
            and _node.func.id in ("id", "hash")):
        _offenders.append(f"docxxml.py:{_node.lineno} {_node.func.id}()")
check("③ docxxml.py 不用 id()/hash() 给 lxml 元素去重（AST 守卫）",
      not _offenders, "；".join(_offenders[:5]))

# ══════════════════════════════════════════════════════════════════════════
# ④ 真实稿抽查（需要稿件库；CI 上 SKIP）—— 05 Yang_Hu 是 R5 的重灾区
# ══════════════════════════════════════════════════════════════════════════
from slcn.paths import manuscripts_root  # noqa: E402

_root = manuscripts_root()
_yanghu = ""
if _root:
    _cand = os.path.join(_root, "2023年第6期(73)", "05 Yang_Hu", "original",
                         "20160050863 revised version (2).docx")
    if os.path.exists(_cand):
        _yanghu = _cand
if not _yanghu:
    skip("④ 真实稿 05 Yang_Hu 确定性", "本机没有稿件库 / 该 docx 不在原路径")
else:
    _real_sigs = []
    for _ in range(3):
        _real_sigs.append(_snapshot(read_blocks(_yanghu)))
        gc.collect()
    check("④ 05 Yang_Hu 连解析 3 次签名完全一致",
          all(s == _real_sigs[0] for s in _real_sigs[1:]),
          f"{len({repr(s) for s in _real_sigs})} 种结果"
          if any(s != _real_sigs[0] for s in _real_sigs[1:]) else "")
    _real_blocks = read_blocks(_yanghu)
    check("④ 05 Yang_Hu 公式段 = 276（每个 oMath 恰好一次；曾随机丢到 245–254）",
          _count(_real_blocks, "math") == 276, f"{_count(_real_blocks, 'math')}")
    check("④ 05 Yang_Hu 矢量形状占位 = 15",
          _count(_real_blocks, "shape") == 15, f"{_count(_real_blocks, 'shape')}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
