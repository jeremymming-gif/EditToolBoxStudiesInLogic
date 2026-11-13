#!/usr/bin/env python3
"""MC-04：推送 DOCX 的可见文本不得残留 TeX 宏 `\\edash` / `\\#` / `\\_`。

模板依据（`\\edash` 不是 en dash）：
  `编辑笔记/模板文件/SLEN.sty:34`、`SLCN.sty:37`、`SLCN-MIX.sty:38`
  均为 `\\newcommand{\\edash}{\\raise0.1em\\hbox{-}}` → 实际字形是短横 `-`。
  `\\cndash` 为 `\\hbox{--}`，但既有推送映射 `-` 属冻结契约，本批不得回退。

独立 oracle（不拿实现生成期望值）：
  · `\\#` → `#`、`\\_` → `_`、`\\edash` → `-`（模板定义）；
  · `\\%` / `\\&` / `\\cndash` / `\\naraba` 既有映射不回退；
  · 无法识别的宏保留原文并 warning；
  · 标题、作者、单位、专栏/主持人、中英文目录、摘要走同一条清洗链；
  · 端到端 `dirpush.py` 生成的 docx `word/document.xml` 无三个宏字面。

本文件既可用 pytest 跑，也可直接 `python tests/test_mc04_...py`（套件 runner 口径）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from html import unescape

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKOUT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

from slcn.pushcopy.collect import (  # noqa: E402
    _clean_abstract,
    _clean_inline,
    collect_issue,
)

PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=ENGINE + os.pathsep + os.path.join(ENGINE, "vendor"))

MACROS = (r"\edash", r"\#", r"\_")


def _w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _issue_dir(root: str, year: int = 2026, number: int = 11, total: int = 213) -> str:
    """合成一期（全假数据）；形态同 P05 夹具，但不 import 其断言。"""
    d = os.path.join(root, f"{year}年第{number}期({total})")
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           f"第\\ 19 卷，第\\ {number} 期，总第\\ {total} 期}}\\\\\n"
           f"\\heiti{{{year} 年第\\ {number} 期，6月\\ 18 日出版}}\n\\end{{flushright}}\n")
    body = ("{\\large ~~~~ 甲 \\edash 乙 50\\# 与 a\\_b}\n"
            "&\\textit{\\large ~~~~ 张\\#三，李\\_四}\n& 1\\\\\n\\vdistance\n"
            "{\\large 假标题二}\n&\\textit{\\large 王五}\n& 2\\\\\n\\vdistance\n")
    host = ("{\\large \\bf{专栏：假\\#专栏}}&&\\textit{\\large 主持人：假\\_主持}\n"
            "\\\\[3.0 ex]\n")
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + host + "%中间主体\n" + body + "\\end{tabularx}\n\\end{document}\n")
    _w(os.path.join(toc, "Chinese Content.tex"), zh)
    en_tex = ("\\documentclass{ctexart}\n\\begin{document}\n"
              "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
              "\\large  \\bf{Column: Fake Column}\n\\\\[6.2mm]\n%中间主体\n"
              "\\large ~~~~ A \\edash B 50\\# x\\_y\\\\\n"
              "\\textit{{~~~~  San\\# Zhang}}\n& 1\\\\\n\\vdistance\n"
              "\\large Fake Title Two\\\\\n\\textit{{Wu Wang}}\n& 2\\\\\n\\vdistance\n"
              "\\end{tabularx}\n\\end{document}\n")
    _w(os.path.join(toc, "contents.tex"), en_tex)
    articles = (
        ("01 Fake_1", r"甲 \edash 乙 50\# 与 a\_b", r"张\#三", r"单位\_甲",
         r"摘要含 \edash、\#、\_ 与 50\% 和 \& 及 \cndash、\naraba，未知命令 \unknowncmd{X} 保留。"),
        ("02 Fake_2", "假标题二", "王五", "假单位乙", "假摘要乙。"),
    )
    for folder, title, author, aff, abstract in articles:
        name = folder.split(" ", 1)[1]
        tex = ("\\documentclass{article}\n\\begin{document}\n"
               f"\\newcommand{{\\mytitleCN}}{{{title}}}\n"
               f"\\newcommand{{\\myfirstauthorCN}}{{{author}}}\n"
               f"\\newcommand{{\\myfirstaffiliationCN}}{{{aff}}}\n"
               f"\\newcommand{{\\myabstractCN}}{{{abstract}}}\n"
               "\\end{document}\n")
        _w(os.path.join(d, folder, f"{name}.tex"), tex)
    return d


def _docx_xml(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def _visible_text(xml: str) -> str:
    return unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", xml, re.S)))


def test_clean_inline_tex_macros() -> None:
    warns: list[str] = []
    out = _clean_inline(r"甲 \edash 乙 50\% 与 \& \# \_ 边界", warns)
    assert out == "甲 - 乙 50% 与 & # _ 边界", repr(out)
    assert warns == [], warns


def test_clean_inline_adjacent_chars() -> None:
    out = _clean_inline(r"50\# 与 a\_b 与 甲\edash乙")
    assert out == "50# 与 a_b 与 甲-乙", repr(out)


def test_existing_aliases_not_regressed() -> None:
    out = _clean_inline(r"\cndash 与 \naraba 与 \to")
    assert out == "- 与 → 与 →", repr(out)


def test_unknown_macro_kept_with_warning() -> None:
    warns: list[str] = []
    out = _clean_inline(r"\unknowncmd{X} 前后", warns)
    assert out == r"\unknowncmdX 前后", repr(out)
    assert any("\\unknowncmd" in x for x in warns), warns
    # 别名只认完整命令名：更长的未知命令不得被截成 "-word"
    warns = []
    out = _clean_inline(r"\edashword 前后", warns)
    assert out == r"\edashword 前后", repr(out)
    assert any("\\edashword" in x for x in warns), warns


def test_clean_abstract_tex_macros() -> None:
    raw = (r"摘要含 \edash、\#、\_ 与 50\% 和 \& 及 \cndash、\naraba，"
           r"未知命令 \unknowncmd{X} 保留。")
    warns: list[str] = []
    out = _clean_abstract(raw, "mc04", warns, [])
    assert out == ("摘要含 -、#、_ 与 50% 和 & 及 -、→，"
                   r"未知命令 \unknowncmdX 保留。"), repr(out)
    assert not any("\\edash" in x for x in warns), warns
    assert any("\\unknowncmd" in x for x in warns), warns


def test_collect_fields_and_docx_end_to_end() -> None:
    work = tempfile.mkdtemp(prefix="mc04_pushcopy_")
    try:
        issue = _issue_dir(os.path.join(work, "src"))
        data = collect_issue(issue, with_en_toc=True)
        assert data.column is not None
        assert data.column.name == "假#专栏", data.column.name
        assert data.column.host == "假_主持", data.column.host
        assert data.zh_entries[0].title_lines == ["甲 - 乙 50# 与 a_b"], data.zh_entries[0]
        assert data.zh_entries[0].authors == ["张#三，李_四"], data.zh_entries[0]
        assert data.en_entries[0].title_lines == ["A - B 50# x_y"], data.en_entries[0]
        assert data.en_entries[0].authors == ["San# Zhang"], data.en_entries[0]
        art = data.articles[0]
        assert art.title_lines == ["甲 - 乙 50# 与 a_b"], art
        assert art.authors == ["张#三"], art
        assert art.affiliations == ["单位_甲"], art
        assert "摘要含 -、#、_ 与 50% 和 & 及 -、→" in art.abstract, art.abstract
        assert any("\\unknowncmd" in w for w in art.warnings), art.warnings
        assert not any("\\edash" in w for w in art.warnings), art.warnings
        for value in ([data.column.name, data.column.host]
                      + data.zh_entries[0].title_lines + data.zh_entries[0].authors
                      + data.en_entries[0].title_lines + data.en_entries[0].authors
                      + art.title_lines + art.authors + art.affiliations + [art.abstract]):
            assert not any(m in value for m in MACROS), value

        out_root = os.path.join(work, "out")
        r = subprocess.run(
            [PY, os.path.join(ENGINE, "dirpush.py"), "--issue", issue,
             "--out", out_root, "--with-en-toc", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=ENV, cwd=CHECKOUT)
        assert r.returncode == 0, r.stderr[-800:]
        obj = json.loads(r.stdout)
        assert obj["ok"] is True
        warns = "\n".join(obj.get("warnings") or [])
        assert "\\unknowncmd" in warns and "无法映射的命令" in warns, warns
        assert "\\edash" not in warns, warns

        docx = os.path.join(out_root, "2026 年第 11 期",
                            "逻辑学研究2026年第11期目录摘要.docx")
        xml = _docx_xml(docx)
        text = _visible_text(xml)
        for macro in MACROS:
            assert macro not in xml, f"{macro} 残留在 document.xml"
            assert macro not in text, f"{macro} 残留在可见文本"
        assert "\\cndash" not in xml and "\\naraba" not in xml
        for expected in ("甲 - 乙 50# 与 a_b", "张#三，李_四", "专栏：假#专栏",
                         "主持人：假_主持", "单位_甲", "A - B 50# x_y", "San# Zhang",
                         "摘要含 -、#、_ 与 50% 和 & 及 -、→", "→"):
            assert expected in text, f"缺少 {expected!r}"
        # 未知宏按契约保留原文（不是静默删除）
        assert r"\unknowncmd" in text, "未知命令被静默吞掉"
    finally:
        shutil.rmtree(work, ignore_errors=True)


_TESTS = (
    test_clean_inline_tex_macros,
    test_clean_inline_adjacent_chars,
    test_existing_aliases_not_regressed,
    test_unknown_macro_kept_with_warning,
    test_clean_abstract_tex_macros,
    test_collect_fields_and_docx_end_to_end,
)


def main() -> int:
    fails: list[str] = []
    for fn in _TESTS:
        try:
            fn()
        except AssertionError as exc:
            fails.append(fn.__name__)
            print(f"FAIL  {fn.__name__}  {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print("\n" + "=" * 66)
    if fails:
        print(f"失败 {len(fails)} 项：{fails}")
        return 1
    print(f"全部 {len(_TESTS)} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
