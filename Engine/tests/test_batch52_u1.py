#!/usr/bin/env python3.14
r"""第五十二批 U1：C 类 ON 重分类不得丢失含公式脚注的预渲染 note_latex。

缺陷（v9 ON 全量实测：87-5 五处、90-04 一处脚注公式二次转义）
--------------------------------------------------------------
`parse_docx()` 的 ④.5 会先对**含公式的脚注**按 segs 预渲染 `note_latex`
（`$…$` 原文保留、只转义文字），供渲染分支优先使用（第四十四批①）。
第四十九批 C 类 ON 路径随后用 `classify_citation(fn.text)` **新建** Footnote
替换 `fn_objs`，新对象上没有 `note_latex`；保留脚注的分支（pure_note /
citation_unmatched / mixed_manual）渲染时回落 `escape_latex(fn.text)`，
于是 `$…$` → `\$…\$`、`\phi` → `\textbackslash{}phi`，PDF 里公式变字面文本。

本套件用内存 docx 钉住（不跑 xelatex、不读稿件库、只写临时目录）：
  ① pure_note + 公式：ON 保留脚注、公式原样，且与 OFF 逐字相同；
  ② mixed_manual + 公式：ON 保留脚注、公式原样（不二次转义），混合 warning 仍在；
  ③ citation_unmatched + 公式：ON 保留脚注、公式原样；
  ④ 全文不出现 `\$` / `\textbackslash{}` 二次转义。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_batch52_u1.py
"""
import os
import re
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402

FAILS = []
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"')

# 公式片段用最小 OMML：<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>
_OM = "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>"

# fid → (片段列表；None 表示行内公式)
_FOOTNOTES = {
    1: ["这里只有公式 ", None, "，没有引用。"],                    # legacy/new = pure_note
    2: ["公式 ", None, " 的定义参见 Smith (2010)。"],               # new = mixed
    3: ["说明文字 ", None, " 见 Cook（2010）。"],                   # new = pure_citation（无候选→unmatched）
    4: ["这是普通说明脚注，没有公式。"],                              # pure_note 无公式：ON/OFF 不变
}


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _fn_xml(fid, runs):
    body = ""
    for i, t in enumerate(runs):
        if t is None:
            body += f"<w:r>{_OM}</w:r>"
        else:
            body += f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'
    return (f'<w:footnote w:id="{fid}"><w:p>'
            f'<w:r><w:t xml:space="preserve">[{fid}]</w:t></w:r>{body}'
            f'</w:p></w:footnote>')


def _make_docx(path):
    fns = "".join(_fn_xml(fid, runs) for fid, runs in _FOOTNOTES.items())
    refs = "".join(f'<w:r><w:footnoteReference w:id="{fid}"/></w:r>'
                   for fid in _FOOTNOTES)
    doc = (
        f'<w:document {_NS}><w:body>'
        '<w:p><w:r><w:t>测试标题</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>张三</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>摘要：测试摘要。</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>关键词：测试</w:t></w:r></w:p>'
        f'<w:p><w:r><w:t xml:space="preserve">正文段落</w:t></w:r>{refs}'
        '<w:r><w:t>。</w:t></w:r></w:p>'
        '</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/footnotes.xml",
                   f'<w:footnotes {_NS}>{fns}</w:footnotes>')


def _build(path, out_dir, on):
    r = build.build_docx(path, out_dir, basename="S", lang="zh",
                         verbose=False, c_class_union=on)
    with open(r.tex_path, encoding="utf-8") as f:
        return f.read(), r


def _bodies(tex):
    """取正文里的 \\footnote{…}（模板里的 \\footnotetext 不算）。"""
    body = tex.split("\\begin{document}", 1)[-1]
    return re.findall(r"\\footnote\{([^{}]*)\}", body)


_fd, _docx = tempfile.mkstemp(suffix=".docx")
os.close(_fd)
try:
    _make_docx(_docx)
    with tempfile.TemporaryDirectory() as _tmp1:
        _off_tex, _ = _build(_docx, _tmp1, on=False)
    with tempfile.TemporaryDirectory() as _tmp2:
        _on_tex, _on_res = _build(_docx, _tmp2, on=True)
finally:
    try:
        os.unlink(_docx)
    except OSError:
        pass

_off_body = _bodies(_off_tex)
_on_body = _bodies(_on_tex)

# ① pure_note + 公式：ON 保留脚注、公式原样，且与 OFF 逐字相同
_note_on = [b for b in _on_body if "这里只有公式" in b]
_note_off = [b for b in _off_body if "这里只有公式" in b]
check("① ON：含公式 pure_note 脚注保留（未被提升/丢弃）",
      len(_note_on) == 1, repr(_on_body))
check("① ON：公式原样 $x$（非 \\$x\\$ / \\textbackslash{}）",
      bool(_note_on) and "$x$" in _note_on[0]
      and r"\$" not in _note_on[0] and r"\textbackslash{}" not in _note_on[0],
      repr(_note_on[0]) if _note_on else "缺失")
check("① ON 与 OFF 的该脚注逐字相同（公式路径已有 note_latex，ON 不得改变产物）",
      len(_note_on) == 1 and len(_note_off) == 1 and _note_on[0] == _note_off[0],
      f"ON={_note_on!r} OFF={_note_off!r}")

# ② mixed_manual + 公式：保留脚注、公式原样（此前的 escape_latex 会二次转义）
_mixed_on = [b for b in _on_body if "Smith (2010)" in b]
check("② ON：mixed_manual 含公式脚注保留（不提升为正文 parencite）",
      len(_mixed_on) == 1, repr(_on_body))
check("② ON：混合脚注公式原样 $x$、无二次转义",
      bool(_mixed_on) and "$x$" in _mixed_on[0]
      and r"\$" not in _mixed_on[0] and r"\textbackslash{}" not in _mixed_on[0],
      repr(_mixed_on[0]) if _mixed_on else "缺失")
check("② ON：混合型 warning 仍在（请人工核对）",
      any("混合型脚注" in w and "请人工核对" in w for w in _on_res.warnings),
      repr([w for w in _on_res.warnings if "混合" in w][:2]))

# ③ citation_unmatched + 公式：保留脚注、公式原样
_unmatched_on = [b for b in _on_body if "Cook（2010）" in b]
check("③ ON：citation_unmatched 含公式脚注保留",
      len(_unmatched_on) == 1, repr(_on_body))
check("③ ON：未匹配脚注公式原样 $x$、无二次转义",
      bool(_unmatched_on) and "$x$" in _unmatched_on[0]
      and r"\$" not in _unmatched_on[0]
      and r"\textbackslash{}" not in _unmatched_on[0],
      repr(_unmatched_on[0]) if _unmatched_on else "缺失")

# ④ 全文无二次转义；无公式的 pure_note 脚注保持原样
_on_full_body = _on_tex.split("\\begin{document}", 1)[-1]
check("④ ON 正文无 \\$ 二次转义", r"\$" not in _on_full_body)
check("④ ON 正文无 \\textbackslash{} 二次转义",
      r"\textbackslash{}" not in _on_full_body)
check("④ 无公式 pure_note 脚注 ON/OFF 都不变",
      any("这是普通说明脚注" in b for b in _on_body)
      and any("这是普通说明脚注" in b for b in _off_body), repr(_on_body))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
