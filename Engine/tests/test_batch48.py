#!/usr/bin/env python3.14
"""第四十八批①：WMF 图片确定性/临时目录、短论述脚注、同作品去重、algorithm 开关。

- WMF→PDF：soffice 导出含时间戳 → pypdf 归一化后两次转换逐字节一致；转换缓存按
  内容 sha 复用；临时目录在系统 temp 且 finally 清理（产物目录不留 `slcn_wmf_*`）。
- 86-07：短论述脚注（CJK 少但有行内引用，如「详细证明请参见(Schurz, 2022, p.
  41)。」）不再被 pure_ref 提升丢散文。
- 88-02：同一作者+题名、年份缺失/不同版次的引用 → bib 归并（不猜题名、不改条目）。
- algorithm 去浮动（可选开关默认关）：algorithm→breakablealgorithm + 导言区注入定义。
"""
import io
import os
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxxml  # noqa: E402

FAILS = []
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _pdf_with_date(date: str) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=72, height=72)
    w.add_metadata({"/CreationDate": date, "/ModDate": date, "/Producer": "soffice"})
    bio = io.BytesIO()
    w.write(bio)
    return bio.getvalue()


# ① metadata 归一化后两次输出逐字节一致
_a = docxxml._normalize_pdf_bytes(_pdf_with_date("D:20261019070000+08'00'"))
_b = docxxml._normalize_pdf_bytes(_pdf_with_date("D:20261019080000+08'00'"))
check("① WMF/PDF metadata 归一化：不同时间戳 → 归一化后逐字节一致", _a == _b,
      f"{len(_a)}B vs {len(_b)}B")

# ② 产物目录历史 slcn_wmf_* 残留会被清理（降级路径不写图、不抛异常）
_orig = docxxml._soffice_bin
_fd, _docx = tempfile.mkstemp(suffix=".docx"); os.close(_fd)
with zipfile.ZipFile(_docx, "w") as z:
    z.writestr("word/document.xml", f'<w:document {_NS}><w:body><w:p/></w:body></w:document>')
    z.writestr("word/_rels/document.xml.rels",
               '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
               'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
               'openxmlformats.org/officeDocument/2006/relationships/image" '
               'Target="media/image1.wmf"/></Relationships>')
    z.writestr("word/media/image1.wmf", b"fake-wmf")
try:
    docxxml._soffice_bin = lambda: ""
    with tempfile.TemporaryDirectory() as _tmp:
        os.makedirs(os.path.join(_tmp, "slcn_wmf_stale"))
        _exp = docxxml.extract_media(_docx, _tmp, {"r0": "image1.wmf"})
        _left = [d for d in os.listdir(_tmp) if d.startswith("slcn_wmf_")]
    check("② 无 soffice 降级：不写图、不抛异常、清理历史 slcn_wmf_* 残留",
          _exp == {} and not _left, f"exported={_exp} leftover={_left}")
finally:
    docxxml._soffice_bin = _orig
    try: os.unlink(_docx)
    except OSError: pass

# ③ algorithm 去浮动开关：默认关不改；开启后 env 改名 + 导言区注入定义（只一次）
_tex = "\\begin{document}\n\\begin{algorithm}[t]\n\\caption{X}\n\\begin{algorithmic}[1]\n\\end{algorithmic}\n\\end{algorithm}\n"
_off = build._to_breakable_algorithms(_tex)
check("③ 开关逻辑：algorithm→breakablealgorithm（保留 caption/算法体）",
      _off.count("\\begin{breakablealgorithm}") == 1 and "\\caption{X}" in _off
      and _off.count("\\end{breakablealgorithm}") == 1 and "\\end{algorithmic}" in _off)
_inj = build._inject_breakable_algorithm_def("\\documentclass{x}\n\\begin{document}\nA\n")
check("③ 环境定义注入 \\begin{document} 之前、仅一次",
      "newenvironment{breakablealgorithm}" in _inj
      and _inj.index("newenvironment{breakablealgorithm}") < _inj.index("\\begin{document}")
      and _inj.count("newenvironment{breakablealgorithm}") == 1)

# ④ 短论述脚注（86-07 fid21/fid26 型）：CJK 少但有行内作者-年引用 → 仍进 \footnote
_FN = ('<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
       '<w:footnote w:id="1"><w:p><w:r><w:t>详细的证明过程可参见(Schurz, 2022)第三节。'
       '</w:t></w:r></w:p></w:footnote></w:footnotes>')
_DOC = ('<w:document ' + _NS + '><w:body>'
        '<w:p><w:r><w:t>短论述脚注测试</w:t></w:r></w:p><w:p><w:r><w:t>张三</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>摘要：测试。</w:t></w:r></w:p><w:p><w:r><w:t>关键词：测试</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>正文段落。</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
        '</w:body></w:document>')
_fd, _d2 = tempfile.mkstemp(suffix=".docx"); os.close(_fd)
with zipfile.ZipFile(_d2, "w") as z:
    z.writestr("word/document.xml", _DOC)
    z.writestr("word/footnotes.xml", _FN)
try:
    with tempfile.TemporaryDirectory() as _tmp:
        _r = build.build_docx(_d2, _tmp, basename="S", lang="zh", verbose=False)
        _t = open(_r.tex_path, encoding="utf-8").read()
    check("④ 短论述脚注散文进 \\footnote（86-07 末条回归）",
          "\\footnote{" in _t and "详细的证明过程可参见" in _t, repr(_t[-300:]))
finally:
    try: os.unlink(_d2)
    except OSError: pass

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
