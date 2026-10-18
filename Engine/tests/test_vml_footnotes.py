#!/usr/bin/env python3.14
"""第四十七批：VML/MathType 公式图提取 + 中文论述性脚注降级（87-2 / 86-07）。

87-2：docx 里 0 OMML，561 个公式全是 MathType OLE（`<w:object>` + `<v:imagedata>`），
预览图 235 个 WMF；引擎只认 DrawingML → 公式全丢。修法：VML 提取（带 v:shape 尺寸）
+ 行内 `\\includegraphics[height=..pt]` + soffice 批量 WMF→PDF（可选外部依赖；缺失时
`\\IfFileExists` 占位 + warning，不阻断）。
86-07：中文论述性脚注（长散文+行内作者-年引用）被 classify() 误判 pure_ref → 提升成
行内 parencite，散文丢失；修法：按「行内引用或多次句读且非文献条目标记」降级为
pure_note 渲染脚注（文献条目型脚注不受影响，86-03 bib 保持）。
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

from lxml import etree  # noqa: E402

from slcn import build, docxxml  # noqa: E402
from slcn.docxxml import M_NS, W_NS, _para_segments, _run_segments  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ① VML 公式图：`<w:object><v:shape style="width:11.3pt;height:15.05pt">` → image Seg + 尺寸
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:v="urn:schemas-microsoft-com:vml"')
_r = etree.fromstring(
    f'<w:r {_NS}><w:object w:dxaOrig="226" w:dyaOrig="301">'
    '<v:shape id="_x1" style="width:11.3pt;height:15.05pt">'
    '<v:imagedata r:id="rId7"/></v:shape></w:object></w:r>')
_segs = _run_segments(_r, set())
_imgs = [s for s in _segs if s.kind == "image"]
check("① VML v:imagedata → image Seg（rId + v:shape 宽高 pt）",
      len(_imgs) == 1 and _imgs[0].rid == "rId7"
      and abs(_imgs[0].w_pt - 11.3) < 1e-6 and abs(_imgs[0].h_pt - 15.05) < 1e-6,
      repr([(s.kind, s.rid, getattr(s, "w_pt", 0), getattr(s, "h_pt", 0)) for s in _segs]))

# ② 行内渲染：有尺寸的图片按 height=..pt 内联 + 缺文件占位（soffice 缺失时）
_p = etree.fromstring(f'<w:p {_NS}><w:r><w:t>前</w:t></w:r></w:p>')
_para = docxxml.Para(segs=[docxxml.Seg("text", "前"), docxxml.Seg("image", rid="rId7", w_pt=11.3, h_pt=15.05)])
from slcn.docxsource import render_para  # noqa: E402
_tex, _notes = render_para(_para, {}, [], {}, {}, 105, {"rId7": "fig1.pdf"})
check("② 行内公式图：`\\includegraphics[height=15.05pt]` + `\\IfFileExists` 占位",
      "\\includegraphics[height=15.05pt]{fig1.pdf}" in _tex
      and "\\IfFileExists{fig1.pdf}" in _tex and "公式图缺失" in _tex, repr(_tex))

# ③ extract_media 降级：无 soffice 时 WMF 不写文件、不抛异常（渲染端占位）
_orig_soffice = docxxml._soffice_bin
_fd, _docx = tempfile.mkstemp(suffix=".docx")
os.close(_fd)
with zipfile.ZipFile(_docx, "w") as z:
    z.writestr("word/document.xml", f'<w:document {_NS}><w:body><w:p/></w:body></w:document>')
    z.writestr("word/_rels/document.xml.rels",
               '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
               'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
               'openxmlformats.org/officeDocument/2006/relationships/image" '
               'Target="media/image1.wmf"/></Relationships>')
    z.writestr("word/media/image1.wmf", b"fake-wmf-bytes")
try:
    docxxml._soffice_bin = lambda: ""
    with tempfile.TemporaryDirectory() as _tmp:
        _exp = docxxml.extract_media(_docx, _tmp, {"r0": "image1.wmf"})
    check("③ 降级：检测不到 soffice → 不写 WMF 文件、返回空 dict（不抛异常）",
          _exp == {}, repr(_exp))
finally:
    docxxml._soffice_bin = _orig_soffice
    try:
        os.unlink(_docx)
    except OSError:
        pass

# ④ 中文论述性脚注降级：合成 docx（正文引用 + 长散文脚注含行内 (陈波, 2018)）
_FN = ('<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
       '<w:footnote w:id="1"><w:p><w:r><w:t>这一看法其实与现代逻辑早期观念并不一致，'
       '有关这一观念的转变研究可参见(陈波, 2018)，另见相关讨论与回应。</w:t></w:r>'
       '</w:p></w:footnote></w:footnotes>')
_DOC = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:r><w:t>中文脚注测试稿</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>张三</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>摘要：测试。</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>关键词：测试</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>正文段落。</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
        '</w:body></w:document>')
_fd, _d2 = tempfile.mkstemp(suffix=".docx")
os.close(_fd)
with zipfile.ZipFile(_d2, "w") as z:
    z.writestr("word/document.xml", _DOC)
    z.writestr("word/footnotes.xml", _FN)
try:
    with tempfile.TemporaryDirectory() as _tmp:
        _res = build.build_docx(_d2, _tmp, basename="FN", lang="zh", verbose=False)
        _t = open(_res.tex_path, encoding="utf-8").read()
    check("④ 论述性脚注：散文整段进 \\footnote（不再被提升成行内 parencite 而丢失）",
          "\\footnote{" in _t and "这一看法其实与现代逻辑早期观念并不一致" in _t,
          repr(_t[-400:]))
finally:
    try:
        os.unlink(_d2)
    except OSError:
        pass

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
