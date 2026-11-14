#!/usr/bin/env python3
"""R3 缺陷 5（B 方案）：单期留空输出根 = 当期 000 Table of Contents。

独立断言（匿名合成期目录；HOME 指向临时目录，绝不写真实主目录 / 稿件仓）：
  A 单期省略 --out：docx 与 目录摘要设置.json 直接落在 issue/000 Table of Contents，
    不再套 <YYYY 年第 N 期> 子目录；JSON outputs[0] 同路径。
  B 显式 --out：仍按 <out>/<YYYY 年第 N 期>/ 写（旧契约不变）。
  C 守卫：显式 --out=<当期>/000 允许（输出仍套期名子目录）；<当期> 本身、
    <当期>/其他子目录、<当期>/000/子目录 → rc=2、零写；dry-run 同语义。
  D 000 不存在：issue_dir 存在且可写 → 先建 000；issue_dir 不存在 → rc=2 报错、零写。
  E 年度模式不变：省略 --out 仍落到旧默认根/<YYYY 年>/，与 000 无关（dry-run）。
  F 跨模式兼容：单期写入 000 的体例设置，年度只读还原时能找到；默认 000 位于
    稿件仓内不误报「输出根位于稿件仓内」，显式仓内路径仍提示（dry-run）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKOUT = os.path.dirname(ENGINE)
VENDOR = os.path.join(ENGINE, "vendor")
if not os.path.isdir(VENDOR):   # 本机只读 vendor（同 test_r3_language_mode.py）
    VENDOR = "/Users/zhangwenming/Documents/GitHub/EditToolBoxStudiesInLogic/Engine/vendor"
sys.path.insert(0, ENGINE)
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
PY = sys.executable
WORK = tempfile.mkdtemp(prefix="r3_dirpush_000_")
HOME = os.path.join(WORK, "home")
os.makedirs(HOME)
ENV = dict(os.environ, HOME=HOME, USERPROFILE=HOME,
           PYTHONPATH=ENGINE + os.pathsep + VENDOR)

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}")
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(ENV, **(env_extra or {}))
    return subprocess.run([PY, os.path.join(ENGINE, "dirpush.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=CHECKOUT)


def jrun(*args: str, env_extra: dict | None = None) -> tuple[int, dict | None, str]:
    r = run(*args, env_extra=env_extra)
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = None
    return r.returncode, obj, r.stderr


def tree_sha(root: str) -> str:
    h = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            h.update(os.path.relpath(path, root).encode("utf-8"))
            with open(path, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def issue_dir(root: str, year: int, number: int, total: int) -> str:
    """合成一期（假标题/假作者/假摘要），000 下只有中文目录 tex + 两篇文章。"""
    d = os.path.join(root, f"{year}年第{number}期({total})")
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           f"第\\ 19 卷，第\\ {number} 期，总第\\ {total} 期}}\\\\\n"
           f"\\heiti{{{year} 年第\\ {number} 期，6月\\ 18 日出版}}\n\\end{{flushright}}\n")
    body = ("{\\large 假标题一}\n&\\textit{\\large 张三}\n& 1\\\\\n\\vdistance\n"
            "{\\large 假标题二}\n&\\textit{\\large 李四}\n& 2\\\\\n\\vdistance\n")
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + "%中间主体\n" + body + "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "Chinese Content.tex"), zh)
    for i, (title, author) in enumerate((("假标题一", "张三"),
                                         ("假标题二", "李四")), 1):
        tex = ("\\documentclass{article}\n\\begin{document}\n"
               f"\\newcommand{{\\mytitleCN}}{{{title}}}\n"
               f"\\newcommand{{\\myfirstauthorCN}}{{{author}}}\n"
               "\\newcommand{\\myfirstaffiliationCN}{假单位}\n"
               f"\\newcommand{{\\myabstractCN}}{{假摘要{i}。}}\n"
               "\\end{document}\n")
        w(os.path.join(d, f"{i:02d} Fake_{i}", f"Fake_{i}.tex"), tex)
    return d


def issue_dir_en(root: str, year: int, number: int, total: int) -> str:
    """合成纯英文一期（标题无 CJK）；000 下中英目录齐全，体例应为 bi。"""
    d = os.path.join(root, f"{year}年第{number}期({total})")
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           f"第\\ 19 卷，第\\ {number} 期，总第\\ {total} 期}}\\\\\n"
           f"\\heiti{{{year} 年第\\ {number} 期，6月\\ 18 日出版}}\n\\end{{flushright}}\n")
    body = ("{\\large Fake Title One}\n&\\textit{\\large Fake Author One}\n& 1\\\\\n\\vdistance\n"
            "{\\large Fake Title Two}\n&\\textit{\\large Fake Author Two}\n& 2\\\\\n\\vdistance\n")
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + "%中间主体\n" + body + "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "Chinese Content.tex"), zh)
    en = ("\\documentclass{ctexart}\n\\begin{document}\n"
          "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
          "\\large  \\bf{Fake Column}\n\\\\[6.2mm]\n%中间主体\n"
          "\\large Fake Title One\\\\\n\\textit{{Fake Author One}}\n& 1\\\\\n\\vdistance\n"
          "\\large Fake Title Two\\\\\n\\textit{{Fake Author Two}}\n& 2\\\\\n\\vdistance\n"
          "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "contents.tex"), en)
    for i, (title, author) in enumerate((("Fake Title One", "Fake Author One"),
                                         ("Fake Title Two", "Fake Author Two")), 1):
        tex = ("\\documentclass{article}\n\\begin{document}\n"
               f"\\newcommand{{\\mytitleCN}}{{{title}}}\n"
               f"\\newcommand{{\\myfirstauthorCN}}{{{author}}}\n"
               "\\newcommand{\\myfirstaffiliationCN}{Fake Affiliation}\n"
               f"\\newcommand{{\\myabstractCN}}{{Fake abstract {i}.}}\n"
               "\\end{document}\n")
        w(os.path.join(d, f"{i:02d} Fake_{i}", f"Fake_{i}.tex"), tex)
    return d


def out_names(year: int, number: int) -> tuple[str, str, str]:
    return (f"{year} 年第 {number} 期",
            f"逻辑学研究{year}年第{number}期目录摘要.docx",
            "目录摘要设置.json")


try:
    # ── A. 单期省略 --out → 直接写 issue/000 ──────────────────────────
    d_a = issue_dir(os.path.join(WORK, "a"), 2026, 3, 88)
    toc_a = os.path.join(d_a, "000 Table of Contents")
    sub_a, docx_a, settings_a = out_names(2026, 3)
    rc, obj, err = jrun("--issue", d_a, "--json")
    direct_docx = os.path.join(toc_a, docx_a)
    direct_settings = os.path.join(toc_a, settings_a)
    check("(A1) 留空 rc=0，docx 直接落在 000 根下",
          rc == 0 and os.path.isfile(direct_docx),
          f"rc={rc} err={err[-300:]!r} exists={os.path.isfile(direct_docx)}")
    check("(A2) 设置文件同样直接落在 000 根下",
          os.path.isfile(direct_settings), direct_settings)
    check("(A3) 000 下没有额外 <YYYY 年第 N 期> 子目录",
          not os.path.isdir(os.path.join(toc_a, sub_a)),
          os.path.join(toc_a, sub_a))
    check("(A4) JSON outputs[0] 就是 000 根下的 docx",
          (obj or {}).get("outputs") == [direct_docx],
          f"outputs={(obj or {}).get('outputs')}")
    check("(A5) 期目录根下也没有旧 <YYYY 年第 N 期> 子目录",
          not os.path.isdir(os.path.join(d_a, sub_a)),
          os.path.join(d_a, sub_a))

    # ── B. 显式 --out → 旧嵌套契约不变 ────────────────────────────────
    d_b = issue_dir(os.path.join(WORK, "b"), 2026, 4, 89)
    out_b = os.path.join(WORK, "b_out")
    sub_b, docx_b, settings_b = out_names(2026, 4)
    rc, obj, err = jrun("--issue", d_b, "--out", out_b, "--json")
    nested_b = os.path.join(out_b, sub_b)
    check("(B1) 显式 --out 仍写 <out>/<YYYY 年第 N 期>/",
          rc == 0 and os.path.isfile(os.path.join(nested_b, docx_b)),
          f"rc={rc} err={err[-300:]!r}")
    check("(B2) 显式 --out 的设置文件也在嵌套目录",
          os.path.isfile(os.path.join(nested_b, settings_b)))
    check("(B3) 显式 --out 不会直写 000",
          not os.path.isfile(os.path.join(d_b, "000 Table of Contents", docx_b)))

    # ── C. 守卫：只放行当期 000，其余期内路径仍拒绝 ───────────────────
    d_c = issue_dir(os.path.join(WORK, "c"), 2026, 5, 90)
    toc_c = os.path.join(d_c, "000 Table of Contents")
    sub_c, docx_c, settings_c = out_names(2026, 5)
    rc, obj, err = jrun("--issue", d_c, "--out", toc_c, "--json")
    check("(C1) 显式 --out=<当期>/000 允许，输出仍套期名子目录",
          rc == 0 and os.path.isfile(os.path.join(toc_c, sub_c, docx_c))
          and os.path.isfile(os.path.join(toc_c, sub_c, settings_c)),
          f"rc={rc} err={err[-300:]!r}")

    d_c2 = issue_dir(os.path.join(WORK, "c2"), 2026, 6, 91)
    toc_c2 = os.path.join(d_c2, "000 Table of Contents")
    before = tree_sha(d_c2)
    rc, _obj, err = jrun("--issue", d_c2, "--out", d_c2, "--json")
    check("(C2) 显式 --out=<当期> 仍 rc=2、零写",
          rc == 2 and tree_sha(d_c2) == before and "期目录内" in err,
          f"rc={rc} err={err.strip()[:120]!r}")
    other = os.path.join(d_c2, "outsub")
    rc, _obj, err = jrun("--issue", d_c2, "--out", other, "--json")
    check("(C3) 显式 --out=<当期>/其他子目录 仍 rc=2、零写",
          rc == 2 and tree_sha(d_c2) == before and "期目录内" in err,
          f"rc={rc} err={err.strip()[:120]!r}")
    under = os.path.join(toc_c2, "outsub")
    rc, _obj, err = jrun("--issue", d_c2, "--out", under, "--json")
    check("(C4) 显式 --out=<当期>/000/子目录 仍 rc=2、不创建",
          rc == 2 and tree_sha(d_c2) == before and not os.path.isdir(under)
          and "期目录内" in err,
          f"rc={rc} created={os.path.isdir(under)} err={err.strip()[:120]!r}")
    rc, _obj, err = jrun("--issue", d_c2, "--out", other, "--json", "--dry-run")
    check("(C5) dry-run 与真实写入同守卫语义",
          rc == 2 and tree_sha(d_c2) == before and not os.path.isdir(other),
          f"rc={rc} created={os.path.isdir(other)}")

    # ── D. 000 不存在 → 创建；issue_dir 不存在 → rc=2 ─────────────────
    d_d = os.path.join(WORK, "d", "2026年第7期(92)")
    os.makedirs(d_d)
    rc, _obj, err = jrun("--issue", d_d, "--json")
    check("(D1) issue 存在且可写、000 不存在 → 建出 000",
          os.path.isdir(os.path.join(d_d, "000 Table of Contents")),
          f"rc={rc} err={err.strip()[:160]!r}")
    check("(D2) 缺目录源文件仍是 rc=2（不伪成功）",
          rc == 2 and "Chinese Content.tex" in err,
          f"rc={rc} err={err.strip()[:160]!r}")

    missing = os.path.join(WORK, "d", "不存在的期")
    rc, _obj, err = jrun("--issue", missing, "--json")
    check("(D3) issue_dir 不存在 → rc=2、明确报「期目录不存在」、零写",
          rc == 2 and "期目录不存在" in err and not os.path.exists(missing),
          f"rc={rc} err={err.strip()[:160]!r}")

    # ── E. 年度模式不变：省略 --out 仍用旧默认根/年度目录 ──────────────
    e_root = os.path.join(WORK, "e")
    d_e = issue_dir(e_root, 2026, 3, 88)
    legacy = os.path.join(HOME, "Documents", "sysu", "编辑部", "目录摘要推送")
    expected_annual = os.path.join(legacy, "2026 年", "逻辑学研究2026年总目录.docx")
    rc, obj, err = jrun("--issue", d_e, "--annual", "--year", "2026",
                        "--json", "--dry-run")
    check("(E1) 年度省略 --out 仍是旧默认根/<YYYY 年>/，不落 000",
          rc == 0 and (obj or {}).get("outputs") == [expected_annual]
          and "000 Table of Contents" not in expected_annual,
          f"rc={rc} outputs={(obj or {}).get('outputs')} err={err[-200:]!r}")
    check("(E2) 年度 dry-run 未写年度 docx（旧默认根 / 000 都不落文件）",
          not os.path.isfile(expected_annual)
          and not os.path.exists(os.path.join(d_e, "000 Table of Contents",
                                              "2026 年", "逻辑学研究2026年总目录.docx")))

    # ── F. 跨模式兼容：000 设置可被年度只读还原；仓内默认不误报 ─────────
    d_f = issue_dir_en(os.path.join(WORK, "f"), 2026, 8, 93)
    toc_f = os.path.join(d_f, "000 Table of Contents")
    rc, obj, err = jrun("--issue", d_f, "--json")
    settings_f = os.path.join(toc_f, "目录摘要设置.json")
    stored: dict = {}
    if os.path.isfile(settings_f):
        with open(settings_f, encoding="utf-8") as fh:
            stored = json.load(fh)
    check("(F1) 单期默认写入 000 的设置记录 with_en_toc=true",
          rc == 0 and stored.get("with_en_toc") is True,
          f"rc={rc} stored={stored} err={err[-200:]!r}")
    rc, obj, err = jrun("--issue", d_f, "--annual", "--year", "2026",
                        "--json", "--dry-run")
    recs = {r["number"]: r for r in (obj or {}).get("annual", {}).get("issues", [])}
    check("(F2) 年度只读还原能找到 000 设置（8 期 with_en_toc=true，不误报缺设置）",
          rc == 0 and recs.get(8, {}).get("with_en_toc") is True
          and "8 期：未找到《目录摘要设置.json》" not in err,
          f"rc={rc} rec={recs.get(8)} err={err[-200:]!r}")

    manus = os.path.join(WORK, "f_manus")
    d_m = issue_dir(manus, 2026, 9, 94)
    rc, obj, err = jrun("--issue", d_m, "--json",
                        env_extra={"SLCN_MANUSCRIPTS": manus})
    check("(F3) 默认 000 位于稿件仓内不误报「输出根位于稿件仓内」",
          rc == 0 and "输出根位于稿件仓内" not in err,
          f"rc={rc} err={err[-300:]!r}")
    out_m = os.path.join(manus, "explicit_out")
    rc, obj, err = jrun("--issue", d_m, "--out", out_m, "--json", "--dry-run",
                        env_extra={"SLCN_MANUSCRIPTS": manus})
    check("(F4) 显式仓内输出根仍提示（不被 F3 一并吞掉）",
          rc == 0 and "输出根位于稿件仓内" in err,
          f"rc={rc} err={err[-300:]!r}")
finally:
    shutil.rmtree(WORK, ignore_errors=True)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")