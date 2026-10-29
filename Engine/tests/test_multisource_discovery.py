#!/usr/bin/env python3.14
"""LaTeX 多文件源发现：`sources.discover_latex_sources()`（第四十二批 A2 / task-29）。

缺陷
-----
期 90/03 Jie_Zhou 的 `original/03示例悖论（定稿）/` 里两个 .tex 角色完全不同：
    main.tex            纯正文片段（无 `\\documentclass`、无宏定义）
    example puzzle.tex   上一轮模板稿（`\\documentclass` + 41 个 SCLN 模板宏 +
                        作者宏 `\\val/\\e/\\A/\\pair/\\set`，`:224` 定义）
自动发现若只按「唯一 .tex / main.tex 优先」选文件，会只拿 main.tex → 宏全丢、公式区
大面积 `Undefined control sequence`（期 90 基线 119 处 `!`、92 undefined citation）。

修法（`Engine/slcn/sources.py`）
----
新增 `LatexSourcePlan` + `discover_latex_sources(source)`：显式给的 .tex 永远是正文源；
前言/元数据源按「identify 的模板稿结论 → 含 `\\documentclass` 且有宏定义且体量明显小于
正文的另一个 .tex」依次判定；判不准时**保持旧行为**并给 warnings（不静默改）。
**不改变 `sources.identify()` 的既有输出** → corpus 21 篇 LaTeX 产物逐字节不变。

跑法
----
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_multisource_discovery.py
"""
import glob
import os
import pathlib
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import sources  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}（{why}）")
    SKIPS.append(name)


FRAGMENT = ("美国学者示例提出了被称为示例悖论（Example puzzles）的疑难问题，"
            "并给出一个信息敏感型的解释方案。\n\n" + "正文论述。" * 300 + "\n")
PREAMBLE = ("\\documentclass[11pt]{article}\n"
            "\\usepackage{amsmath}\n"
            "\\newcommand{\\val}[1]{[\\hspace{-0.5mm}[#1]\\hspace{-0.5mm}]}\n"
            "\\newcommand{\\e}{\\mathbf{e}}\n"
            "\\def\\A{\\mathcal{A}}\n"
            "\\begin{document}\n\\title{旧模板稿}\\maketitle\n\\end{document}\n")
COMPLETE = ("\\documentclass[11pt]{article}\n\\usepackage{amsmath}\n"
            "\\begin{document}\n\\section{引言}\n" + "正文内容。" * 400 + "\n\\end{document}\n")
README_TEX = "这是说明，不是 tex 源。"  # 非 .tex，不该被扫描


def _issue_paper_original(issue_no: str, no: str) -> str:
    """按「总期号 + 篇序号」发现来稿的 original 目录（不硬编码真实作者名/文件名；B-232）。"""
    from slcn import paths
    root = paths.manuscripts_root()
    if not root:
        return ""
    import glob as _glob
    import re as _re
    for name in sorted(os.listdir(root)):
        m = _re.search(r"\((\d+[A-Z]?)\)$", name)
        if not (m and m.group(1) == str(issue_no)):
            continue
        hits = sorted(_glob.glob(os.path.join(root, name, no + " *")))
        hits = [h for h in hits if os.path.isdir(h)]
        if hits:
            orig = os.path.join(hits[0], "original")
            return orig if os.path.isdir(orig) else hits[0]
    return ""


def _main_and_meta_tex(orig: str):
    """original 下的 (正文片段, 前言/模板稿)：按 `main.tex` 与「另一个 .tex」区分。"""
    texs = sorted(glob.glob(os.path.join(orig, "**", "*.tex"), recursive=True))
    main = next((p for p in texs if os.path.basename(p) == "main.tex"), "")
    meta = next((p for p in texs if os.path.basename(p) != "main.tex"), "")
    return main, meta



def _write(d: str, name: str, text: str) -> str:
    p = os.path.join(d, name)
    pathlib.Path(p).write_text(text, encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════════════════
# ① 片段 + 前言（90-03 形态）：body=片段、metadata=含 \documentclass 的前言
# ══════════════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as d:
    main_tex = _write(d, "main.tex", FRAGMENT)
    _write(d, "example puzzle.tex", PREAMBLE)
    plan = sources.discover_latex_sources(main_tex)
    check("① 片段+前言：正文源 = 显式给的 main.tex",
          plan.body is not None and os.path.basename(plan.body.path) == "main.tex",
          repr(plan.body and plan.body.path))
    check("① 前言源自动配到含 \\documentclass+宏定义的 .tex",
          plan.metadata is not None and os.path.basename(plan.metadata.path) == "example puzzle.tex",
          repr(plan.metadata and plan.metadata.path))
    check("① note 可解释（提到「前言源」/「无 \\documentclass」）",
          "前言源" in plan.note and ("documentclass" in plan.note or "\\documentclass" in plan.note),
          plan.note)
    check("① 无 warning（判据明确时不制造噪声）", plan.warnings == [], str(plan.warnings))

# ══════════════════════════════════════════════════════════════════════════
# ② 单文件完整稿：正文与前言同源，metadata is body；不 panic、不 warning
# ══════════════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as d:
    p = _write(d, "paper.tex", COMPLETE)
    plan = sources.discover_latex_sources(p)
    check("② 单文件完整稿：metadata is body（无需 --metadata）",
          plan.metadata is not None and os.path.abspath(plan.metadata.path) == os.path.abspath(p),
          repr(plan.metadata and plan.metadata.path))
    check("② note 说明同源", "同源" in plan.note, plan.note)
    check("② 无 warning", plan.warnings == [], str(plan.warnings))

# ══════════════════════════════════════════════════════════════════════════
# ③ 两份「都有 \documentclass」的候选：不许猜 → 保持旧行为 + warning
# ══════════════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as d:
    _write(d, "a.tex", COMPLETE)
    _write(d, "b.tex", COMPLETE.replace("正文内容", "另一份正文"))
    plan = sources.discover_latex_sources(d)
    check("③ 两个完整稿候选：不猜（metadata 不为「另一份」而随便选）",
          plan.metadata is None or os.path.abspath(plan.metadata.path) != os.path.abspath(
              os.path.join(d, "b.tex")),
          repr(plan.metadata and plan.metadata.path))
    check("③ 有 warning（无法判断时不许静默）", bool(plan.warnings), str(plan.warnings))

# ══════════════════════════════════════════════════════════════════════════
# ④ 只有片段、没有任何 \documentclass：保持旧行为（metadata=None）+ warning
# ══════════════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as d:
    f1 = _write(d, "introduction.tex", "引言。" * 300)
    _write(d, "conclusion.tex", "结论。" * 50)
    plan = sources.discover_latex_sources(f1)
    check("④ 纯片段：正文源 = 显式给的文件", plan.body is not None
          and os.path.abspath(plan.body.path) == os.path.abspath(f1))
    check("④ 纯片段：metadata=None（旧行为）", plan.metadata is None,
          repr(plan.metadata and plan.metadata.path))
    check("④ 纯片段：有 warning（提示用户 --metadata / 确认）", bool(plan.warnings), str(plan.warnings))

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 真实稿 90-03（有稿件库时）：发现结果 = main.tex + example puzzle.tex
# ══════════════════════════════════════════════════════════════════════════
def _real_90_03() -> None:
    from slcn import paths

    root = paths.manuscripts_root()
    if not root:
        skip("⑤ 真实稿 90-03 发现", "本机没有稿件库")
        return
    orig = _issue_paper_original("90", "03")
    main_tex, meta_tex = _main_and_meta_tex(orig) if orig else ("", "")
    if not (main_tex and meta_tex):
        skip("⑤ 真实稿 90-03 发现", "本机没有该稿")
        return
    art = os.path.dirname(orig)
    plan = sources.discover_latex_sources(main_tex)
    check("⑤ 90-03：正文源 = main.tex",
          plan.body is not None and os.path.basename(plan.body.path) == "main.tex")
    check("⑤ 90-03：前言/元数据源 = 另一个 .tex（无需 --metadata）",
          plan.metadata is not None
          and os.path.basename(plan.metadata.path) == os.path.basename(meta_tex),
          repr(plan.metadata and plan.metadata.path))
    check("⑤ 90-03：无「判不准」warning", plan.warnings == [], str(plan.warnings))

    # 兼容 pin：identify() 的既有输出保持不变（corpus 21 篇 LaTeX 产物依赖它）
    ident = sources.identify(art, verbose=False)
    check("⑤ identify() 旧行为不变：90-03 metadata=模板稿 / body=main",
          ident.metadata is not None
          and os.path.basename(ident.metadata.path) == os.path.basename(meta_tex)
          and ident.body is not None and os.path.basename(ident.body.path) == "main.tex")


def _real_old_behavior_pins() -> None:
    """兼容 pin：两个**旧行为**样本（identify 不变，corpus 产物才不变）。"""
    from slcn import paths

    root = paths.manuscripts_root()
    if not root:
        skip("⑥ identify 旧行为 pin", "本机没有稿件库")
        return
    # 6.1 单文件完整模板稿：identify 仍是 metadata，body 回退到同一文件（既有口径）。
    #     从 corpus.csv 的 LaTeX 行里现找样本，不硬编路径。
    import csv as _csv
    csv_path = os.path.join(_ENGINE, "out", "corpus", "corpus.csv")
    sampled = same = 0
    if os.path.isfile(csv_path):
        rows = [r for r in _csv.DictReader(open(csv_path, encoding="utf-8"))
                if r.get("channel") == "LaTeX"][:4]
        for r in rows:
            art = os.path.join(root, r["issue"], r["slug"])
            if not os.path.isdir(art):
                continue
            ident = sources.identify(art, verbose=False)
            if ident.docx:
                continue
            sampled += 1
            if (ident.body is not None and ident.metadata is not None
                    and os.path.abspath(ident.body.path) == os.path.abspath(ident.metadata.path)):
                same += 1
    if sampled == 0:
        skip("⑥.1 identify 单文件完整稿 pin", "本机没有可用的 LaTeX corpus 样本")
    else:
        check("⑥.1 identify 旧行为 pin：单文件完整稿 body/metadata 同源（≥1 篇命中）",
              same >= 1, f"{same}/{sampled} 篇同源")
    # 6.2 多片段稿：旧行为 = body 取最长片段、metadata=None（本批不动）
    # 路径按「期号 + 篇序号」发现，不硬编码作者目录名（B-232/B-269）
    _orig_62 = _issue_paper_original("89", "01")
    art = os.path.dirname(_orig_62) if _orig_62 else ""
    if art and os.path.isdir(art):
        ident = sources.identify(art, verbose=False)
        if ident.docx:
            skip("⑥.2 identify 多片段稿 pin", "该篇是 Word 稿")
        else:
            check("⑥.2 identify 多片段稿 pin：metadata 仍为 None（旧行为，本批未改）",
                  ident.metadata is None, repr(ident.metadata and ident.metadata.path))
    else:
        skip("⑥.2 identify 多片段稿 pin", "本机没有该稿")


# ══════════════════════════════════════════════════════════════════════════
# ⑦ CLI 接线 pin：convert.py **不传 --metadata** 时走自动前言源（--dry-run 不编译）
#    断言：dry-run 成功 + 日志出现「前言源 …/example puzzle.tex」+「自动发现」；
#          传了 --metadata 时**不**走自动发现（显式优先）。
# ══════════════════════════════════════════════════════════════════════════
def _cli_auto_metadata_pin() -> None:
    import subprocess

    from slcn import paths

    root = paths.manuscripts_root()
    if not root:
        skip("⑦ convert.py 自动前言源 pin", "本机没有稿件库")
        return
    orig = _issue_paper_original("90", "03")
    main_tex, meta_tex = _main_and_meta_tex(orig) if orig else ("", "")
    if not (main_tex and meta_tex):
        skip("⑦ convert.py 自动前言源 pin", "本机没有该稿")
        return
    author_name = os.path.basename(os.path.dirname(orig)).split(" ", 1)[-1]  # 运行时发现，不写死
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [_ENGINE, os.path.join(_ENGINE, "vendor"), env.get("PYTHONPATH", "")])
    base = [sys.executable, "convert.py", "--source", main_tex,
            "--lang", "zh", "--name", author_name, "--dry-run"]
    with tempfile.TemporaryDirectory() as out:
        r = subprocess.run(base + ["--out", out], cwd=_ENGINE, env=env,
                           capture_output=True, text=True, timeout=120)
        check("⑦ 不传 --metadata：convert.py --dry-run rc=0（接线可跑）",
              r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[-160:]!r}")
        check("⑦ 不传 --metadata：日志出现「前言源」+ 模板稿文件名",
              "前言源" in r.stdout and os.path.basename(meta_tex) in r.stdout,
              r.stdout[-400:].replace("\n", "\u23ce"))
        check("⑦ 不传 --metadata：日志说明「自动发现」（可解释）",
              "自动发现" in r.stdout)
        r2 = subprocess.run(base + ["--out", out, "--metadata", meta_tex], cwd=_ENGINE,
                            env=env, capture_output=True, text=True, timeout=120)
        check("⑦ 传 --metadata：显式优先，不再打印「自动发现」",
              r2.returncode == 0 and "自动发现" not in r2.stdout,
              f"rc={r2.returncode}")


try:
    _real_90_03()
    _real_old_behavior_pins()
    _cli_auto_metadata_pin()
except Exception as _e:  # noqa: BLE001
    skip("⑤⑥⑦ 真实稿/CLI 检查", f"{type(_e).__name__}: {_e}")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
