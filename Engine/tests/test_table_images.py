#!/usr/bin/env python3.14
"""第四十六批②续：表格单元格内的图片（86-01 PAGE_DIFF 根因）。

缺陷（task-60 实测）：86-01 的 18 个 `<w:drawing>`（带 r:embed）全在**表格单元格**里；
旧实现 ①图片收集只遍历 body_paras（不含 Table）→ 18 张全丢、只剩正文 1 张；
②`render_cell()` 只渲 text/math → 表格里的图即使收集了也不会进产物。
人工成品是 18 条 `\\includegraphics`、18 页。

修法：①图片收集遍历 blocks（含 Table cells）；②`render_cell()` 支持 image seg
（每张图独占一行）；③**含图表格走 longtable**（图片纵向排列后单行会超一页，
普通 tabular 不能跨页 → 图被裁、页数不增）；无图表格保持原 `table` 逐字节不变。
"""
import hashlib
import json
import os
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxsource  # noqa: E402
from slcn.docxxml import W_NS  # noqa: E402

FAILS = []

# 1×1 PNG（最小合法 PNG）
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f7d0000000049454e44ae426082")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/image" Target="media/image1.png"/></Relationships>'
)


def make_docx(paras, table_xml="", tail=(), with_image=False) -> str:
    ps = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paras)
    ts = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in tail)
    doc = (f'<w:document {_NS}><w:body>{ps}{table_xml}{ts}</w:body></w:document>')
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        if with_image:
            z.writestr("word/_rels/document.xml.rels", _RELS)
            z.writestr("word/media/image1.png", _PNG)
    return path


_IMG_CELL = (
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>列一</w:t></w:r></w:p></w:tc></w:tr>'
    '<w:tr><w:tc><w:p><w:r><w:drawing><a:graphic><a:graphicData>'
    '<a:blip r:embed="rId1"/></a:graphicData></a:graphic></w:drawing></w:r></w:p></w:tc></w:tr>'
    '</w:tbl>')
_PLAIN_CELL = (
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')

# ① 合成 docx：表格单元格内 1 张图 → doc.images 收录 + 产物 longtable + includegraphics
_D1 = make_docx(["表格图片测试稿", "张三", "摘要：测试。", "关键词：测试", "正文前。"],
                table_xml=_IMG_CELL, tail=["正文后。"], with_image=True)
try:
    _doc1 = docxsource.parse_docx(_D1)
    check("① 表格单元格内的图片被收集（doc.images=1）",
          len(_doc1.images) == 1, repr(_doc1.images))
    with tempfile.TemporaryDirectory() as _t1:
        _r1 = build.build_docx(_D1, _t1, basename="T1", lang="zh", verbose=False)
        _tex1 = open(_r1.tex_path, encoding="utf-8").read()
        _exported = [f for f in os.listdir(_t1) if f.lower().endswith(".png")]
    check("① 含图表格走 longtable（可跨页）且只出现一次",
          _tex1.count("\\begin{longtable}") == 1 and "\\begin{table}" not in _tex1,
          repr(_tex1[_tex1.find("longtable") - 30:_tex1.find("longtable") + 60]) if "longtable" in _tex1 else "")
    check("① 图在表格内输出 \\includegraphics，且图片文件已导出",
          "\\includegraphics" in _tex1 and len(_exported) == 1, repr(_exported))
finally:
    try:
        os.unlink(_D1)
    except OSError:
        pass

# ② 反例：无图表格输出保持原 `table`+`tabular`（不误用 longtable）
_D2 = make_docx(["无图表格测试稿", "张三", "摘要：测试。", "关键词：测试", "正文前。"],
                table_xml=_PLAIN_CELL, tail=["正文后。"])
try:
    _doc2 = docxsource.parse_docx(_D2)
    with tempfile.TemporaryDirectory() as _t2:
        _r2 = build.build_docx(_D2, _t2, basename="T2", lang="zh", verbose=False)
        _tex2 = open(_r2.tex_path, encoding="utf-8").read()
    check("② 反例：无图表格仍用原 table/tabular（不写 longtable、不写 includegraphics）",
          "\\begin{table}" in _tex2 and "longtable" not in _tex2
          and "includegraphics" not in _tex2 and "A & B" in _tex2, repr(_tex2[-200:]))
finally:
    try:
        os.unlink(_D2)
    except OSError:
        pass

# ③ 真实稿 pin：86-01（18 张图全在表格里）；无源稿时 SKIP
_SRC = "/Users/zhangwenming/Desktop/macCaseTest86/1/广义论证理论视域下视频公益广告的多模态说服传播【修改稿3】.docx"
if not os.path.isfile(_SRC):
    print("SKIP  ③ 真实稿 86-01（源稿不在本机）")
else:
    _doc3 = docxsource.parse_docx(_SRC)
    check("③ 真实稿 86-01：doc.images = 18（修复前 1）", len(_doc3.images) == 18,
          str(len(_doc3.images)))
    check("③ 真实稿 86-01：产物 includegraphics = 18、含 1 张 longtable",
          _doc3.body.count("\\includegraphics") == 18
          and "\\begin{longtable}" in _doc3.body, str(_doc3.body.count("\\includegraphics")))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
