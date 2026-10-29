#!/usr/bin/env python3.14
"""第四十七批②：访谈体标题 / 中文文献脚注边界 / 重复引用 bib 去重（task-66）。

- 87-9 访谈体：`一 非形式逻辑`（一/二/三 + 空格，无顿号）→ 一级标题；普通正文
  「一 个…」不得误判。
- 86-03 边界：中文**文献条目型**脚注（含书名号 / 年份+期号）不得被当论述脚注降级
  （task-65 曾 1 条误判、bib 22→21）；86-07 型论述脚注仍进 \\footnote。
- 88-02 重复引用：同一文献被多条脚注反复引用时，bib 按「作者+年+题名」去重
  （曾 67 条 → 58 条、页数 27→26；全量重复清理属后续 C 类/去重卡）。
"""
import os
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
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def make_docx(paras, footnotes=""):
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paras)
    doc = f'<w:document {_NS}><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        if footnotes:
            z.writestr("word/footnotes.xml", footnotes)
    return path


# ① 87-9 访谈体：`一 标题` → \section；「一 个重要问题」反例不算
_D1 = make_docx(["访谈体标题测试", "张三", "摘要：测试。", "关键词：测试",
                 "正文引言。", "一 非形式逻辑", "这是第一节正文内容。",
                 "一 个重要问题需要讨论。"])
try:
    with tempfile.TemporaryDirectory() as t:
        _r1 = build.build_docx(_D1, t, basename="H", lang="zh", verbose=False)
        _t1 = open(_r1.tex_path, encoding="utf-8").read()
    check("① 访谈体 `一 非形式逻辑` → \\section{非形式逻辑}",
          "\\section{非形式逻辑}" in _t1, repr(_t1[_t1.find("非形式逻辑")-20:_t1.find("非形式逻辑")+30]))
    check("① 反例：`一 个重要问题…` 留在正文、不误判标题",
          "\\section{个重要问题" not in _t1 and "一 个重要问题需要讨论" in _t1)
finally:
    try: os.unlink(_D1)
    except OSError: pass

# ② 86-03 边界：合并两条中文文献的脚注（含书名号/年份+期号）→ 仍进 bib、不降级为脚注散文
_FN_MERGED = ('<w:footnotes " + xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
# 直接写正确字符串
_FN = ('<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
       '<w:footnote w:id="1"><w:p><w:r><w:t>胡炳年、陈静，“广义论证视角下的农村彩礼说理”，'
       '逻辑学研究，2018 年第 1 期。麦劲恒：《广式早茶说理的功能结构分析》，'
       '《逻辑学研究》2019 年第 2 期。</w:t></w:r></w:p></w:footnote></w:footnotes>')
_DOC = ('<w:document ' + _NS + '><w:body>'
        '<w:p><w:r><w:t>中文文献脚注测试</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>张三</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>摘要：测试。</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>关键词：测试</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>正文段落。</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
        '</w:body></w:document>')
_fd, _d2 = tempfile.mkstemp(suffix=".docx"); os.close(_fd)
with zipfile.ZipFile(_d2, "w") as z:
    z.writestr("word/document.xml", _DOC)
    z.writestr("word/footnotes.xml", _FN)
try:
    with tempfile.TemporaryDirectory() as t:
        # 该边界属**旧口径**（C 类默认 ON 后由新分类器接管，另测）；这里固定旧路径
        _r2 = build.build_docx(_d2, t, basename="F", lang="zh", verbose=False,
                               c_class_union=False)
        _t2 = open(_r2.tex_path, encoding="utf-8").read()
        _bib2 = open(_r2.bib_path, encoding="utf-8").read() if _r2.bib_path else ""
    check("② 中文文献条目型脚注（含书名号/期号）不被降级成脚注散文（86-03 边界）",
          "胡炳年" in _bib2 and "\\footnote{胡炳年" not in _t2,
          f"bib含={('胡炳年' in _bib2)} foot散={'\\\\footnote{胡炳年' in _t2}")
finally:
    try: os.unlink(_d2)
    except OSError: pass

# ③ 88-02 型：同一文献被两条脚注引用 → bib 去重为 1 条
_FN2 = ('<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:id="1"><w:p><w:r><w:t>D. Falessi and F. Schang, “A Relational Semantics '
        'for Ockham’s Modalities”, in Axioms 12, 2023, p. 13.</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:r><w:t>D. Falessi and F. Schang, “A Relational Semantics '
        'for Ockham’s Modalities”, in Axioms 12, 2023, p. 6.</w:t></w:r></w:p></w:footnote>'
        '</w:footnotes>')
_DOC2 = ('<w:document ' + _NS + '><w:body>'
         '<w:p><w:r><w:t>重复引用测试</w:t></w:r></w:p>'
         '<w:p><w:r><w:t>张三</w:t></w:r></w:p>'
         '<w:p><w:r><w:t>摘要：测试。</w:t></w:r></w:p>'
         '<w:p><w:r><w:t>关键词：测试</w:t></w:r></w:p>'
         '<w:p><w:r><w:t>正文一。</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
         '<w:p><w:r><w:t>正文二。</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
         '</w:body></w:document>')
_fd, _d3 = tempfile.mkstemp(suffix=".docx"); os.close(_fd)
with zipfile.ZipFile(_d3, "w") as z:
    z.writestr("word/document.xml", _DOC2)
    z.writestr("word/footnotes.xml", _FN2)
try:
    with tempfile.TemporaryDirectory() as t:
        _r3 = build.build_docx(_d3, t, basename="D", lang="zh", verbose=False)
        _bib3 = open(_r3.bib_path, encoding="utf-8").read() if _r3.bib_path else ""
    _n = _bib3.count("@")
    check("③ 同一文献两条脚注引用 → bib 去重为 1 条（88-02 型）", _n == 1, f"bib @ 数={_n}")
finally:
    try: os.unlink(_d3)
    except OSError: pass

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
