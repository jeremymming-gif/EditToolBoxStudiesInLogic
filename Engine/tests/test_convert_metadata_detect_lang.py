#!/usr/bin/env python3.14
r"""AIW 初排语种误判修复（detect_lang tex 分支低 CJK 早退）的 TDD 套件。

背景：英文稿 NCML-LaTeX-En/main.tex 前 4000 字只含 30 个汉字（0.75%），
旧 detect_lang 的 tex 分支只要看见一个汉字就判 zh → 选中文 SLCN-MIX.sty +
CN_template.tex → 迁入的 \newtheorem{fact}[theorem] 撞上 theorem[lem] 共享计数器
→ xelatex 报 No counter 'theorem' defined。本套件钉住修复后的判据与反例。

用法（不读稿件库；夹具全部写在 /tmp/slcn-aiw-lang-* 下）：
    cd Engine
    export PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor"
    /opt/homebrew/bin/python3.14 -B tests/test_convert_metadata_detect_lang.py

集成复验（可选，需授权路径的真实英文来源稿 + xelatex；默认跳过）：
    SLCN_AIW_LANG_INTEGRATION=1 /opt/homebrew/bin/python3.14 -B \
        tests/test_convert_metadata_detect_lang.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.convert_metadata import detect_lang  # noqa: E402

FAILS = []
TMPROOT = tempfile.mkdtemp(prefix="slcn-aiw-lang-")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def cjk_ratio(text):
    n = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return n, n / max(len(text), 1)


def write_tex(name, text):
    p = os.path.join(TMPROOT, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


_EN_PREAMBLE = (
    "\\documentclass[b5paper,10.5t]{article}\n"
    "\\usepackage{amsmath}\n"
    "\\newtheorem{theorem}{{\\bf{Theorem}}}\n"
    "\\newtheorem{fact}[theorem]{{\\bf{Fact}}}\n"
    "\\begin{document}\n"
)
_EN_FILLER = (
    "We study a family of modal logics and establish completeness for several "
    "of their fragments. The proof uses canonical models and a filtration "
    "argument. Related work is discussed in the introduction. "
)
_ZH_FILLER = (
    "本文研究一类模态逻辑的完备性问题，并给出若干片段的证明。 "
    "证明使用典范模型与过滤方法，相关工作在引言中讨论。 "
)

try:
    # ── 红：英文稿含少量中文参考文献（前 4000 字 CJK 约 0.75%）必须判 en ──
    _en_refs = (
        "\\bibitem{r1} 张 三, Some Studies on Logic, 2020.\n"
        "\\bibitem{r2} 李 四, Journal of Philosophical Logic, 2021.\n"
    )
    _en_body = _EN_PREAMBLE + _EN_FILLER * 60
    _en_body = _en_body[:3900] + "\n" + _en_refs + "\n\\end{document}\n"
    _en_cjk, _en_ratio = cjk_ratio(_en_body[:4000])
    check("夹具自检：英文稿前 4000 字 CJK 比例 < 5%",
          _en_ratio < 0.05, f"cjk={_en_cjk} ratio={_en_ratio:.2%}")
    _en_path = write_tex("english_with_cjk_refs.tex", _en_body)
    _lang, _why = detect_lang(_en_path, None)
    check("红：英文 .tex（含少量中文参考文献）判 en", _lang == "en",
          f"lang={_lang} why={_why}")

    # ── 反例 1：中文稿正文汉字比例高，仍须判 zh ──
    _zh_body = (
        "\\documentclass[b5paper,10.5t]{article}\n"
        "\\usepackage{SLCN-MIX}\n"
        "\\begin{document}\n"
        + _ZH_FILLER * 60 + "\\end{document}\n"
    )
    _zh_cjk, _zh_ratio = cjk_ratio(_zh_body[:4000])
    check("夹具自检：中文稿前 4000 字 CJK 比例 > 5%",
          _zh_ratio > 0.05, f"cjk={_zh_cjk} ratio={_zh_ratio:.2%}")
    _zh_path = write_tex("chinese_body.tex", _zh_body)
    _lang, _why = detect_lang(_zh_path, None)
    check("反例：中文 .tex 仍判 zh", _lang == "zh", f"lang={_lang} why={_why}")

    # ── 反例 2：中文摘要在 4000 字之外；开头汉字约 7.5%（>5% 但远低于 30%）仍判 zh ──
    # 钉住阈值必须足够低（5% 而非 Word 分支的 30%）：否则这类中文稿会被误判 en。
    _zh_title = "逻辑学中的模态完备性与过滤方法研究综述" * 16  # 约 304 汉字
    _late_head = (
        "\\documentclass[b5paper,10.5t]{article}\n"
        "\\usepackage{SLCN-MIX}\n"
        "\\title{" + _zh_title + "}\n"
        "\\begin{document}\n" + _EN_FILLER * 60 + "\n"
    )
    _late = _late_head[:4000] + "\n\\begin{abstract}" + _ZH_FILLER * 20 + "\\end{abstract}\n"
    _late_cjk, _late_ratio = cjk_ratio(_late[:4000])
    check("夹具自检：中文稿摘要在 4000 字外，开头 CJK 介于 5% 与 30%",
          0.05 < _late_ratio < 0.30, f"cjk={_late_cjk} ratio={_late_ratio:.2%}")
    _late_path = write_tex("chinese_abstract_after_4000.tex", _late)
    _lang, _why = detect_lang(_late_path, None)
    check("反例：中文摘要在 4000 字之外仍判 zh（阈值不能过高）",
          _lang == "zh", f"lang={_lang} why={_why}")

    # ── 既有行为：无汉字的英文稿仍判 en；模板字段宏优先级不变 ──
    _pure_en = write_tex("pure_english.tex", _EN_PREAMBLE + _EN_FILLER * 40 + "\\end{document}\n")
    _lang, _why = detect_lang(_pure_en, None)
    check("既有：纯英文 .tex 判 en", _lang == "en", f"lang={_lang} why={_why}")

    _cn_field = write_tex("mytitleCN.tex", "\\newcommand{\\mytitleCN}{Title}\n" + _ZH_FILLER * 60)
    _lang, _why = detect_lang(_pure_en, _cn_field)  # 宏来自 --metadata 路径
    check("契约：mytitleCN 仍判 en（英文模板字段）", _lang == "en", f"lang={_lang} why={_why}")

    _en_field = write_tex("mytitleEN.tex", "\\newcommand{\\mytitleEN}{题目}\n" + _EN_FILLER * 60)
    _lang, _why = detect_lang(_pure_en, _en_field)  # 宏来自 --metadata 路径
    check("契约：mytitleEN 仍判 zh（中文模板字段）", _lang == "zh", f"lang={_lang} why={_why}")

    # ── 集成复验（opt-in）：授权路径的真实英文稿，默认不传 --lang 也应走 EN ──
    if os.environ.get("SLCN_AIW_LANG_INTEGRATION") == "1":
        _src = "/Users/zhangwenming/Desktop/che/NCML-LaTeX-En/main.tex"
        _bib = "/Users/zhangwenming/Desktop/che/NCML-LaTeX-En/NCML.bib"
        if not os.path.isfile(_src):
            check("集成：授权来源稿存在", False, f"NOT_RUN: {_src} 不存在")
        else:
            _lang, _why = detect_lang(_src, None)
            check("集成：真实英文稿 detect_lang 返回 en", _lang == "en",
                  f"lang={_lang} why={_why}")
            for _tag, _extra in (("explicit", ["--lang", "en"]), ("default", [])):
                _out = os.path.join(TMPROOT, f"out_{_tag}")
                _cmd = [
                    sys.executable, "-B", os.path.join(_ENGINE, "convert.py"),
                    "--source", _src, "--out", _out, "--name", f"NCML_{_tag}",
                    "--bib", _bib, "--compile", *_extra,
                ]
                _env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                            SLCN_MANUSCRIPTS="/dev/null",
                            PYTHONPATH=os.path.join(_ENGINE, "vendor"))
                _p = subprocess.run(_cmd, env=_env, capture_output=True, text=True, timeout=1800)
                _log = _p.stdout + "\n" + _p.stderr
                check(f"集成[{_tag}]：convert.py EXIT 0", _p.returncode == 0,
                      f"rc={_p.returncode} tail={_log[-400:]!r}")
                # 产物在 <out>/<name>/ 下；以实际生成的 .tex/.pdf/.log 为准。
                _produced = []
                for _root, _dirs, _files in os.walk(_out):
                    _produced.extend(os.path.join(_root, f) for f in _files)
                _texs = [f for f in _produced if f.endswith(".tex")]
                _pdfs = [f for f in _produced if f.endswith(".pdf")]
                _logs = [f for f in _produced if f.endswith(".log")]
                check(f"集成[{_tag}]：生成 tex/pdf/log", bool(_texs and _pdfs and _logs),
                      f"tex={len(_texs)} pdf={len(_pdfs)} log={len(_logs)}")
                if _texs and _pdfs and _logs:
                    _tex_text = open(_texs[0], encoding="utf-8", errors="replace").read()
                    _log_text = open(_logs[0], encoding="utf-8", errors="replace").read()
                    check(f"集成[{_tag}]：走 EN 骨架（SLEN.sty）",
                          "SLEN" in _tex_text and "SLCN-MIX" not in _tex_text)
                    check(f"集成[{_tag}]：0 个未解析 ??", "??" not in _tex_text)
                    check(f"集成[{_tag}]：fact 解析（无 No counter / Environment fact undefined）",
                          "No counter 'theorem' defined" not in _log_text
                          and "Environment fact undefined" not in _log_text)
                    try:
                        from pypdf import PdfReader
                        _pages = len(PdfReader(_pdfs[0]).pages)
                    except Exception:  # noqa: BLE001 — 回退 pdfinfo
                        _pi = subprocess.run(["pdfinfo", _pdfs[0]], capture_output=True,
                                             text=True, timeout=60)
                        _pages = -1
                        for _line in _pi.stdout.splitlines():
                            if _line.startswith("Pages:"):
                                _pages = int(_line.split(":", 1)[1].strip())
                                break
                    check(f"集成[{_tag}]：PDF 17 页", _pages == 17, f"pages={_pages}")
    else:
        print("SKIP  集成复验（未设 SLCN_AIW_LANG_INTEGRATION=1）")
finally:
    shutil.rmtree(TMPROOT, ignore_errors=True)

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
