#!/usr/bin/env python3
"""P05 目录摘要推送：CLI 只读边界 / 年度诚实提示 / 中文摘要计数 / 只读预检。

独立断言（不 import 实现内部判据，只走 CLI 契约 + 合成夹具）：
  A  P05-BUG-01 输出根落在期目录内（当期 / 年度；含 dry-run）→ rc=2、零写、中文原因
  B  P05-BUG-03 年度扫描遇到无法识别期号的期目录（`2022年第6A期(67A)`）→ warnings 诚实提示；
     当期模式读该期仍是既有 rc=2（期头解析失败），不扩大业务
  C  P05-BUG-04 `counts.abstracts_ok` 只计中文来源；中文模板计数不变；docx 英文回落事实不变
  D  `dirpush.py --check-out-root <path> [--json]`：只读预检，rc=0、零写、
     stdout `{"schema":1,"ok":true,"warnings":[...]}`，判据与 dirpush 仓库保护同源

全部夹具为合成内容（假标题/假作者/假摘要），不读真实稿件；临时目录在系统 temp 下。
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
# 官方 runner（Engine/tools/run_engine_tests.sh）的 PYTHONPATH 只有 Engine/vendor；
# 脚本目录 tests/ 在 sys.path 但 Engine 根不在，故显式引导（同 tests/test_infocheck_toc.py:26）。
sys.path.insert(0, ENGINE)
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=ENGINE + os.pathsep + os.path.join(ENGINE, "vendor"))

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


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, os.path.join(ENGINE, "dirpush.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=ENV, cwd=CHECKOUT)


def jrun(*args: str) -> tuple[int, dict | None, str]:
    r = run(*args)
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = None
    return r.returncode, obj, r.stderr


def tree_sha(root: str) -> str:
    h = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(base, f)
            h.update(os.path.relpath(p, root).encode("utf-8"))
            h.update(open(p, "rb").read())
    return h.hexdigest()


def issue_dir(root: str, year: int, number: int, total: int, *,
              label: str | None = None, cn: bool = True, en: bool = True,
              header_number: str | None = None,
              header_total: str | None = None) -> str:
    """合成一期（假数据）：000 目录两份 tex + 两篇假文章。"""
    d = os.path.join(root, label or f"{year}年第{number}期({total})")
    num_s = header_number or str(number)
    tot_s = header_total or str(total)
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           f"第\\ 19 卷，第\\ {num_s} 期，总第\\ {tot_s} 期}}\\\\\n"
           f"\\heiti{{{year} 年第\\ {num_s} 期，6月\\ 18 日出版}}\n\\end{{flushright}}\n")
    body = ("{\\large ~~~~ 假标题一}\n&\\textit{\\large ~~~~ 张三，李四}\n& 1\\\\\n\\vdistance\n"
            "{\\large 假标题二}\n&\\textit{\\large 王五}\n& 2\\\\\n\\vdistance\n")
    host = "{\\large \\bf{专栏：假专栏}}&&\\textit{\\large 主持人：假主持}\n\\\\[3.0 ex]\n"
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + host + "%中间主体\n" + body + "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "Chinese Content.tex"), zh)
    if en:
        en_tex = ("\\documentclass{ctexart}\n\\begin{document}\n"
                  "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
                  "\\large  \\bf{Column: Fake Column}\n\\\\[6.2mm]\n%中间主体\n"
                  "\\large ~~~~ Fake Title One\\\\\n\\textit{{~~~~  San Zhang}}\n& 1\\\\\n\\vdistance\n"
                  "\\large Fake Title Two\\\\\n\\textit{{Wu Wang}}\n& 2\\\\\n\\vdistance\n"
                  "\\end{tabularx}\n\\end{document}\n")
        w(os.path.join(toc, "contents.tex"), en_tex)
    for i, (t, a, ab) in enumerate((("假标题一", "张三", "假摘要甲。"),
                                    ("假标题二", "王五", "假摘要乙。")), 1):
        if cn:
            tex = ("\\documentclass{article}\n\\begin{document}\n"
                   f"\\newcommand{{\\mytitleCN}}{{{t}}}\n"
                   f"\\newcommand{{\\myfirstauthorCN}}{{{a}}}\n"
                   f"\\newcommand{{\\myfirstaffiliationCN}}{{假单位}}\n"
                   f"\\newcommand{{\\myabstractCN}}{{{ab}}}\n"
                   "\\end{document}\n")
        else:  # 无中文字段：英文回落模板
            tex = ("\\documentclass{article}\n\\begin{document}\n"
                   f"\\newcommand{{\\mytitle}}{{Fake Title {i}}}\n"
                   f"\\newcommand{{\\myfirstauthor}}{{Author {i}}}\n"
                   f"\\newcommand{{\\myfirstaffiliation}}{{Fake Aff}}\n"
                   f"\\newcommand{{\\myabstract}}{{Fake abstract {i}.}}\n"
                   "\\end{document}\n")
        w(os.path.join(d, f"{i:02d} Fake_{i}", f"Fake_{i}.tex"), tex)
    return d


work = tempfile.mkdtemp(prefix="p05_pushcopy_")
try:
    # ── A. P05-BUG-01：输出根落在输入期目录内（当期）────────────────────
    a_root = os.path.join(work, "a")
    d = issue_dir(a_root, 2026, 3, 88)
    before = tree_sha(d)
    rc, obj, err = jrun("--issue", d, "--out", d, "--json")
    after = tree_sha(d)
    check("(A1) --out=<期目录> → rc=2、零写、stderr 含「期目录内/作者来稿只读」",
          rc == 2 and before == after
          and "期目录内" in err and "作者来稿只读" in err
          and not any(x.startswith(("逻辑学研究", "目录摘要")) for x in os.listdir(d)),
          f"rc={rc} changed={before != after} err={err.strip()[:80]!r}")
    sub = os.path.join(d, "outsub")
    before = tree_sha(d)
    rc, obj, err = jrun("--issue", d, "--out", sub, "--json")
    after = tree_sha(d)
    check("(A2) --out=<期目录>/outsub → rc=2、子目录未创建、零写",
          rc == 2 and before == after and not os.path.isdir(sub) and "期目录内" in err,
          f"rc={rc} changed={before != after} sub={os.path.isdir(sub)}")
    rc, obj, err = jrun("--issue", d, "--out", sub, "--json", "--dry-run")
    check("(A3) BUG-01 dry-run 同样拒绝（预览=生成），不创建子目录",
          rc == 2 and not os.path.isdir(sub) and "期目录内" in err,
          f"rc={rc} sub={os.path.isdir(sub)}")
    check("(A4) 拒绝时 JSON 仍是同 schema 的 ok:false（issue=null）",
          obj is not None and obj.get("ok") is False and obj.get("issue") is None
          and isinstance(obj.get("warnings"), list) and obj["warnings"],
          f"obj={obj}")

    # ── A5. P05-BUG-01：年度模式（--issue 目录 / 任何被扫描的期目录）
    a2_root = os.path.join(work, "a2")
    d3 = issue_dir(a2_root, 2026, 3, 88)
    d4 = issue_dir(a2_root, 2026, 4, 89)
    before_root = tree_sha(a2_root)
    rc, obj, err = jrun("--issue", d3, "--annual", "--year", "2026",
                        "--out", d3, "--json")
    check("(A5) 年度 --out=<--issue 目录> → rc=2、零写",
          rc == 2 and tree_sha(a2_root) == before_root and "期目录内" in err,
          f"rc={rc} err={err.strip()[:80]!r}")
    rc, obj, err = jrun("--issue", d3, "--annual", "--year", "2026",
                        "--out", d4, "--json")
    check("(A6) 年度 --out=<被扫描的兄弟期目录> → rc=2、零写",
          rc == 2 and tree_sha(a2_root) == before_root and "期目录内" in err,
          f"rc={rc} err={err.strip()[:80]!r}")
    rc, obj, err = jrun("--issue", d3, "--annual", "--year", "2026",
                        "--out", os.path.join(d4, "annual_out"), "--json", "--dry-run")
    check("(A7) 年度 dry-run 落在兄弟期目录子路径 → 同样拒绝",
          rc == 2 and not os.path.isdir(os.path.join(d4, "annual_out")),
          f"rc={rc}")

    # ── B. P05-BUG-03：无法识别期号的期目录 → 年度 warnings 诚实提示 ──
    b_root = os.path.join(work, "b", "annual_src")
    issue_dir(b_root, 2022, 4, 65)
    issue_dir(b_root, 2022, 5, 66)
    issue_dir(b_root, 2022, 6, 67)
    d6a = os.path.join(b_root, "2022年第6A期(67A)")
    issue_dir(b_root, 2022, 6, 67, label="2022年第6A期(67A)",
              header_number="6A", header_total="67A")
    out_b = os.path.join(work, "b", "out")
    rc, obj, err = jrun("--issue", os.path.join(b_root, "2022年第4期(65)"),
                        "--annual", "--year", "2022", "--out", out_b, "--json")
    ann = (obj or {}).get("annual") or {}
    warn = "\n".join((obj or {}).get("warnings") or [])
    check("(B1) 年度 2022：6A 期未纳入（included=[4,5,6]，不造业务）",
          rc == 0 and ann.get("included") == [4, 5, 6],
          f"rc={rc} included={ann.get('included')}")
    check("(B2) 年度 JSON warnings[] 诚实提示「发现期目录 2022年第6A期(67A) 无法识别期号，未纳入年度」",
          rc == 0 and "发现期目录" in warn and "2022年第6A期(67A)" in warn
          and "无法识别期号" in warn and "未纳入年度" in warn,
          f"warn={warn[:200]!r}")
    r = run("--issue", os.path.join(b_root, "2022年第4期(65)"),
            "--annual", "--year", "2022", "--out", out_b)
    check("(B3) 年度非 JSON 模式：stderr 也出现同一条提示",
          r.returncode == 0 and "2022年第6A期(67A)" in r.stderr
          and "无法识别期号" in r.stderr,
          f"rc={r.returncode} stderr={r.stderr.strip()[:160]!r}")
    rc, obj, err = jrun("--issue", d6a, "--out", os.path.join(work, "b", "out6a"),
                        "--json", "--dry-run")
    check("(B4) 当期模式读 6A 期 = 既有 rc=2 期头解析失败（不扩大业务）",
          rc == 2 and "期头解析失败" in err,
          f"rc={rc} err={err.strip()[:100]!r}")

    # ── C. P05-BUG-04：abstracts_ok 只计中文来源 ─────────────────────
    c_root = os.path.join(work, "c")
    d_cn = issue_dir(c_root, 2026, 3, 88)
    rc, obj, err = jrun("--issue", d_cn, "--out", os.path.join(work, "c", "out_cn"),
                        "--json", "--dry-run")
    cnt = (obj or {}).get("counts") or {}
    check("(C1) 中文模板计数不变：abstracts_ok=2、abstracts_missing=0",
          rc == 0 and cnt.get("abstracts_ok") == 2 and cnt.get("abstracts_missing") == 0,
          f"counts={cnt}")
    d_fb = issue_dir(c_root, 2026, 7, 92, cn=False, en=False)
    out_fb = os.path.join(work, "c", "out_fb")
    rc, obj, err = jrun("--issue", d_fb, "--out", out_fb, "--json", "--dry-run")
    cnt = (obj or {}).get("counts") or {}
    warn = "\n".join((obj or {}).get("warnings") or [])
    check("(C2) 英文回落模板：abstracts_ok=0（只计中文来源）",
          rc == 0 and cnt.get("abstracts_ok") == 0,
          f"counts={cnt}")
    check("(C2b) fallback 口径（Lead 裁决 A）：abstracts_missing == articles == 2",
          cnt.get("articles") == 2 and cnt.get("abstracts_missing") == 2,
          f"counts={cnt}")
    check("(C2c) fallback：JSON 顶层 warnings[] 含「回落」提示（不静默降级）",
          "回落" in warn and "模板无中文字段" in warn,
          f"warn={warn[:200]!r}")
    # 回落事实（warning 记在 Article 上、docx 仍渲染英文摘要）用公开数据模型断言
    from slcn.pushcopy import collect as PC  # noqa: E402
    fb = PC.collect_issue(d_fb, with_en_toc=False, out_root=out_fb)
    fb_warns = "\n".join(w for a in fb.articles for w in a.warnings)
    check("(C3) 英文回落事实不变：仍给「模板无中文字段」warning",
          "模板无中文字段" in fb_warns,
          f"warnings={fb_warns[:160]!r}")
    rr = run("--issue", d_fb, "--out", out_fb, "--json")
    docx = os.path.join(out_fb, "2026 年第 7 期",
                        "逻辑学研究2026年第7期目录摘要.docx")
    xml = ""
    if os.path.isfile(docx):
        import zipfile
        with zipfile.ZipFile(docx) as z:
            xml = z.read("word/document.xml").decode("utf-8")
    check("(C4) 英文回落摘要仍写进 docx（摘要段不是空）",
          rr.returncode == 0 and os.path.isfile(docx) and "Fake abstract 1." in xml,
          f"rc={rr.returncode} exists={os.path.isfile(docx)}")

    # ── D. 只读预检 --check-out-root ─────────────────────────────────
    inside_repo = os.path.join(CHECKOUT, "Engine", "out", "p05_check_out_probe")
    rc, obj, err = jrun("--check-out-root", inside_repo, "--json")
    warn = (obj or {}).get("warnings") or []
    check("(D1) --check-out-root <公开仓内> --json → rc=0、schema=1、ok=true、warnings 非空",
          rc == 0 and obj is not None and obj.get("schema") == 1
          and obj.get("ok") is True and isinstance(warn, list) and warn
          and any("公开仓" in x for x in warn),
          f"rc={rc} obj={obj}")
    check("(D2) --check-out-root 零写：不创建被检查的路径",
          not os.path.exists(inside_repo))
    outside = os.path.join(work, "outside_out")
    rc, obj, err = jrun("--check-out-root", outside, "--json")
    check("(D3) 仓库外路径 → warnings 为空、rc=0",
          rc == 0 and obj is not None and obj.get("ok") is True
          and (obj.get("warnings") or []) == [] and not os.path.exists(outside),
          f"rc={rc} obj={obj}")
    r = run("--check-out-root", inside_repo)
    check("(D4) 非 JSON 模式打印 warning 行（stderr 含 WARNING + 公开仓），rc=0",
          r.returncode == 0 and "WARNING" in r.stderr and "公开仓" in r.stderr
          and not os.path.exists(inside_repo),
          f"rc={r.returncode} err={r.stderr.strip()[:120]!r}")
    check("(D5) --check-out-root 不要求 --issue（顶层只读模式）",
          rc == 0 and "--issue" not in r.stderr,
          f"rc={r.returncode}")
    # argparse 不再强制 --issue，普通模式缺参数仍须 rc=2 而非吞掉
    rc6, obj6, err6 = jrun("--json")
    check("(D6) 普通模式缺 --issue → rc=2、JSON ok:false、stderr 中文原因",
          rc6 == 2 and (obj6 or {}).get("ok") is False and "缺少 --issue" in err6,
          f"rc={rc6} obj={(obj6 or {}).get('ok')} err={err6.strip()[:120]!r}")
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
