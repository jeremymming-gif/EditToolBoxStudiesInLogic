#!/usr/bin/env python3
"""「本期出刊」向导（第 71 批 B-116）：5 步编排、退出码传播、目录 PDF 落位。

钉住的契约（`docs/ENGINE_API.md` §五，2026-10-21 起 5 步）：
  (a) 步骤表固定 5 步且顺序为 ①infocheck ②toc ③compileall ④目录 PDF 落位 ⑤merge；
  (b) 退出码语义（INVARIANTS 三.2 / 用户 2026-10-17 拍板）：
      0 继续、1 橙色警告继续、2 中止；中止时 stdout 末行写「卡在第 N 步（步骤名）；前 N−1 步产物有效」；
  (c) `--only N` / `--from N` 对 1–5 生效；
  (d) 缺 ① 的报告 ⇒ 第 ② 步**退出码 2 中止**（不静默跳过）；
  (e) 第 ⑤ 步**显式传** `--nest-outlines --editors-page --back-cover`（默认开），
      `--no-*` 逐个关闭；而 `slcn/pdfmerge.merge_issue` 自己的默认值**仍是全关**（默认零变化）；
  (f) 第 ④ 步调 `slcn/tocdelivery.py`（唯一实现，toc.py --place --compile 共用）：
      取期目录根最新 `Chinese Content_<ts>.tex` / `contents_<ts>.tex` → 复制为
      `000*/Chinese Content.tex` / `contents.tex`（保留根下 .tex）→ 字体前置检查 → 在 000* 内
      xelatex ×2 → 页数 > 1 时单页适配；**缺 000 目录 ⇒ 自动创建**（B-226，不再 rc2）；
      `--skip-toc-pdf` 跳过④并对缺 PDF 告警；编译/字体失败 ⇒ 退出码 2 + 可操作原因
      （测试全部把编译/字体检查换成假实现，不依赖本机装 TeX）。
  (g) 第 ② 步**不再硬传 `--template <本期>`**：模板解析与「生成目录」同一套；显式 `--template` 才传。

测试全部用 tempfile 合成期目录 / 假步骤，**不读真实稿件库**（hermetic）。
"""
from __future__ import annotations

import contextlib
import glob
import inspect
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest.mock

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

import publish as P                              # noqa: E402
from slcn import pdfmerge as M                   # noqa: E402
from slcn import tocdelivery as TD               # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """跑 P.main，返回 (rc, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = P.main(argv)
    return rc, out.getvalue(), err.getvalue()


def last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def install_fake_steps(rc_by_no: dict[int, int] | None = None) -> tuple[list[int], callable]:
    """把五步全换成假实现（记录调用序，按步骤号返回指定 rc）。返回 (记录列表, 恢复函数)。"""
    orig = dict(P.STEP_FUNCS)
    calls: list[int] = []
    rcs = rc_by_no or {}

    def make(no: int):
        def fake(root: str, opts: P._Opts) -> P._StepResult:
            calls.append(no)
            return P._StepResult(rcs.get(no, 0))
        return fake

    for no in range(1, 6):
        P.STEP_FUNCS[no] = make(no)

    def restore() -> None:
        P.STEP_FUNCS.clear()
        P.STEP_FUNCS.update(orig)

    return calls, restore


@contextlib.contextmanager
def patched(attr: str, value):
    """替换模块级函数/属性，退出时还原（比 unittest.mock 少一层间接、意图更直白）。"""
    orig = getattr(P, attr)
    setattr(P, attr, value)
    try:
        yield
    finally:
        setattr(P, attr, orig)


def mkroot() -> str:
    return tempfile.mkdtemp(prefix="slcn_publish_")


def mkfile(path: str, text: str, mtime: float | None = None) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def mk_issue(root: str, *, with_000: bool = True, report: bool = True) -> str:
    """合成一个最小期目录：000 目录 + 报告 + 一篇假文章。"""
    os.makedirs(root, exist_ok=True)
    if with_000:
        os.makedirs(os.path.join(root, "000 Table of Contents"), exist_ok=True)
    if report:
        mkfile(os.path.join(root, "信息检查_20261021_130000.txt"), "合成报告\n")
    art = os.path.join(root, "01 A_Author")
    os.makedirs(art, exist_ok=True)
    mkfile(os.path.join(art, "A_Author.tex"), "% fake article\n")
    return root


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 步骤表：固定 5 步、顺序与入口 ──")
check("(a1) STEPS 恰为 1–5", sorted(P.STEPS) == [1, 2, 3, 4, 5], str(sorted(P.STEPS)))
_names = [P.STEPS[n][0] for n in range(1, 6)]
check("(a2) 步骤名与顺序：①检查tex信息 ②生成目录 ③编译全部 ④目录 PDF 落位 ⑤合并整期 PDF",
      _names == ["检查tex信息", "生成目录", "编译全部", "目录 PDF 落位", "合并整期 PDF"],
      str(_names))
check("(a3) 四个外步入 1/2/3/5 分别指向 infocheck/toc/compileall/mergepdf",
      [P.STEPS[n][1] for n in (1, 2, 3, 5)]
      == ["infocheck.py", "toc.py", "compileall.py", "mergepdf.py"])
check("(a4) 第 4 步是内部步骤（无独立入口）", P.STEPS[4][1] == "")

print("\n── (b) 退出码传播：0 继续 / 1 橙色继续 / 2 中止 ──")
root = mk_issue(mkroot())
calls, restore = install_fake_steps()
try:
    rc, out, err = run_main(["--folder", root])
finally:
    restore()
check("(b1) 全 0：退出码 0，五步全跑、顺序 1→5", rc == 0 and calls == [1, 2, 3, 4, 5], f"rc={rc} calls={calls}")
check("(b2) 全 0：末行是契约行「5 步都跑完」",
      last_line(out).startswith("完成。出刊：5 步都跑完（目录："))

calls, restore = install_fake_steps({2: 1})
try:
    rc, out, _ = run_main(["--folder", root])
finally:
    restore()
check("(b3) 第 2 步 rc1：不中止、五步全跑", calls == [1, 2, 3, 4, 5], str(calls))
check("(b4) 第 2 步 rc1：整体退出码 1、末行带「警告 1 项」",
      rc == 1 and "警告 1 项" in last_line(out), f"rc={rc} last={last_line(out)}")
check("(b5) 第 2 步 rc1：警告行在契约行之前（契约行保持最后一行）",
      out.find("⚠ 第 2 步") < out.rfind("完成。出刊："))

calls, restore = install_fake_steps({3: 2})
try:
    rc, out, _ = run_main(["--folder", root])
finally:
    restore()
check("(b6) 第 3 步 rc2：立即中止（第 4/5 步不跑）", calls == [1, 2, 3], str(calls))
check("(b7) 第 3 步 rc2：退出码 2 + 末行「卡在第 3 步（编译全部）；前 2 步产物有效」",
      rc == 2 and last_line(out)
      == f"完成。出刊：卡在第 3 步（编译全部）；前 2 步产物有效（目录：{root}）",
      last_line(out))

calls, restore = install_fake_steps({5: 2})
try:
    rc, out, _ = run_main(["--folder", root])
finally:
    restore()
check("(b8) 第 5 步 rc2：退出码 2 + 末行「卡在第 5 步（合并整期 PDF）」",
      rc == 2 and calls == [1, 2, 3, 4, 5] and "卡在第 5 步（合并整期 PDF）；前 4 步产物有效" in last_line(out),
      last_line(out))

print("\n── (c) --only / --from 对 1–5 生效 ──")
cases = [
    (["--only", "1"], [1]),
    (["--only", "3"], [3]),
    (["--only", "5"], [5]),
    (["--from", "2"], [2, 3, 4, 5]),
    (["--from", "4"], [4, 5]),
    (["--from", "5"], [5]),
]
for extra, want in cases:
    calls, restore = install_fake_steps()
    try:
        rc, out, _ = run_main(["--folder", root, *extra])
    finally:
        restore()
    check(f"(c) {' '.join(extra)}：只跑 {want}",
          rc == 0 and calls == want, f"calls={calls}")
calls, restore = install_fake_steps()
try:
    rc, out, _ = run_main(["--folder", root, "--only", "4"])
finally:
    restore()
check("(c7) --only 4：末行写「只跑了第 4 步（目录 PDF 落位）」",
      "只跑了第 4 步（目录 PDF 落位）" in last_line(out), last_line(out))

print("\n── (d) 缺 ① 报告 ⇒ 第 ② 步退出码 2（不静默跳过）──")
root = mk_issue(mkroot(), report=False)
rc, out, err = run_main(["--folder", root, "--from", "2"])
check("(d1) 没有报告：退出码 2", rc == 2, f"rc={rc}")
check("(d2) 末行「卡在第 2 步（生成目录）」", "卡在第 2 步（生成目录）" in last_line(out), last_line(out))
check("(d3) stderr 给出可操作原因「请先跑第 1 步」",
      "信息检查" in err and "请先跑第 1 步" in err, err.strip())
check("(d4) 缺报告时不调用 toc.py（_step2_toc 直接返回 2）",
      P._step2_toc(root, P._Opts(root=root)).rc == 2)

print("\n── (e) 第 ⑤ 步三开关：默认显式传、--no-* 关闭；mergepdf 自身默认仍是全关 ──")
root = mk_issue(mkroot())
calls_argv: list[list[str]] = []


def fake_run(argv: list[str]) -> P._StepResult:
    calls_argv.append(list(argv))
    return P._StepResult(0)


with patched("_run_script", fake_run):
    P._step5_merge(root, P._Opts(root=root))
    argv_default = calls_argv[-1]
    P._step5_merge(root, P._Opts(root=root, nest_outlines=False,
                                 editors_page=False, back_cover=False))
    argv_off = calls_argv[-1]
    run_main(["--folder", root, "--from", "5"])       # 走 main 的默认路径
    argv_main = calls_argv[-1]

for flag in ("--nest-outlines", "--editors-page", "--back-cover"):
    check(f"(e1) 默认传 {flag}", flag in argv_default, str(argv_default))
check("(e2) 默认 argv = 入口 + --folder + --out<合并.pdf> + 三开关（不缺不多）",
      os.path.basename(argv_default[0]) == "mergepdf.py"
      and argv_default[1:4] == ["--folder", root, "--out"]
      and argv_default[4] == os.path.join(root, "合并.pdf")
      and argv_default[5:] == ["--nest-outlines", "--editors-page", "--back-cover"],
      str(argv_default))
check("(e3) --no-* 三个全关：三个开关一个都不传",
      not any(f in argv_off for f in ("--nest-outlines", "--editors-page", "--back-cover")),
      str(argv_off))
check("(e4) main 默认路径同样显式传三开关", argv_main == argv_default, str(argv_main))
_sig = inspect.signature(M.merge_issue)
check("(e5) mergepdf 默认零变化：merge_issue 三开关默认仍为 False",
      _sig.parameters["nest_outlines"].default is False
      and _sig.parameters["editors_page"].default is False
      and _sig.parameters["back_cover"].default is False,
      str({k: _sig.parameters[k].default
           for k in ("nest_outlines", "editors_page", "back_cover")}))
with open(os.path.join(str(ENGINE), "mergepdf.py"), encoding="utf-8") as fh:
    _merge_src = fh.read()
check("(e6) mergepdf.py CLI 三开关仍是 store_true（默认 False 的原始契约）",
      _merge_src.count('action="store_true"') >= 4
      and not any(f"--no-{x}" in _merge_src
                  for x in ("nest-outlines", "editors-page", "back-cover")))

print("\n── (f) 第 ④ 步目录 PDF 落位（调 tocdelivery；编译/字体检查用假实现，不依赖 TeX）──")


def mk_toc_root(newer_content: str = "NEW-CONTENT", newer_en: str = "NEW-CONTENTS") -> tuple[str, str]:
    root = mkroot()
    os.makedirs(os.path.join(root, "000 目录"), exist_ok=True)
    mkfile(os.path.join(root, "Chinese Content_20260101_000000.tex"), "OLD", mtime=1000)
    mkfile(os.path.join(root, "contents_20260101_000000.tex"), "OLD-EN", mtime=1000)
    mkfile(os.path.join(root, "Chinese Content_20261021_130000.tex"), newer_content, mtime=2000)
    mkfile(os.path.join(root, "contents_20261021_130000.tex"), newer_en, mtime=2000)
    # 000 里已有旧成品（要被覆盖）
    mkfile(os.path.join(root, "000 目录", "Chinese Content.tex"), "STALE", mtime=1500)
    mkfile(os.path.join(root, "000 目录", "contents.tex"), "STALE-EN", mtime=1500)
    return root, os.path.join(root, "000 目录")


compiled: list[tuple[str, str]] = []


def fake_compile_all(folder: str, stems) -> dict:
    """假的 tocdelivery.compile_pdfs：写假 PDF + 1 页 log，记录调用。"""
    out = {}
    for key, stem in stems:
        compiled.append((folder, stem))
        pdf = os.path.join(folder, stem + ".pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4 fake\n")
        with open(os.path.join(folder, stem + ".log"), "w", encoding="utf-8") as fh:
            fh.write(f"Output written on {stem}.pdf (1 page).\n")
        out[key] = (True, pdf)
    return out


def install_fake_delivery(*, font_fail: bool = False, compile_fail_stem: str = "",
                          pages: dict | None = None):
    """替换 tocdelivery 的字体检查/编译/单页适配（交付链其余部分用真实现）。返回恢复函数。"""
    orig = (TD.check_windows_fonts, TD.compile_pdfs, TD.fit_single_page)
    page_map = pages or {"zh": 1, "en": 1}

    def fake_fonts():
        return (False, "缺少 Windows 字体（SimSun）") if font_fail else (True, "")

    def fake_compile(folder: str, stems) -> dict:
        out = fake_compile_all(folder, stems)
        for key, stem in stems:
            if compile_fail_stem and stem == compile_fail_stem:
                out[key] = (False, "xelatex 第 1/2 次失败（退出码 1）：! Fake error")
        return out

    def fake_fit(folder, stems, compile_func, log=lambda _s: None):
        return dict(page_map), ""

    TD.check_windows_fonts, TD.compile_pdfs, TD.fit_single_page = fake_fonts, fake_compile, fake_fit

    def restore() -> None:
        TD.check_windows_fonts, TD.compile_pdfs, TD.fit_single_page = orig

    return restore


root, toc_dir = mk_toc_root()
compiled.clear()
_restore = install_fake_delivery()
try:
    res = P._step4_toc_pdf(root, P._Opts(root=root))
finally:
    _restore()
check("(f1) 落位成功：退出码 0", res.rc == 0, f"rc={res.rc} reason={res.reason}")
check("(f2) 取最新一份 tex 复制为 000*/Chinese Content.tex",
      open(os.path.join(toc_dir, "Chinese Content.tex"), encoding="utf-8").read() == "NEW-CONTENT")
check("(f3) 取最新一份 tex 复制为 000*/contents.tex",
      open(os.path.join(toc_dir, "contents.tex"), encoding="utf-8").read() == "NEW-CONTENTS")
check("(f4) 在 000* 内依次编译这两份（先中文后英文）",
      [s for _d, s in compiled] == ["Chinese Content", "contents"]
      and all(d == toc_dir for d, _s in compiled), str(compiled))
check("(f5) 根下的带时间戳 .tex 全部保留（供下一期继承）",
      len(glob.glob(os.path.join(root, "Chinese Content_*.tex"))) == 2
      and len(glob.glob(os.path.join(root, "contents_*.tex"))) == 2)

# 缺最新 tex（只有旧 000 成品）⇒ 不能拿旧 PDF 糊弄，退出码 2
root2 = mkroot()
os.makedirs(os.path.join(root2, "000 目录"), exist_ok=True)
res = P._step4_toc_pdf(root2, P._Opts(root=root2))
check("(f6) 根下没有目录 tex：退出码 2 + 原因提示先生成目录",
      res.rc == 2 and "先生成目录" in res.reason, f"rc={res.rc} reason={res.reason}")

# 编译失败 ⇒ 退出码 2（不静默用旧 PDF）
root3, _toc3 = mk_toc_root()
compiled.clear()
_restore = install_fake_delivery(compile_fail_stem="contents")
try:
    res = P._step4_toc_pdf(root3, P._Opts(root=root3))
finally:
    _restore()
check("(f7) 目录 PDF 编译失败：退出码 2 + 原因说明不拿旧 PDF 合并",
      res.rc == 2 and "不会拿旧 PDF 继续合并" in res.reason, f"rc={res.rc} reason={res.reason}")

# 字体前置检查失败 ⇒ 退出码 2 + 原因（不编译、绝不静默换字体）
root3b, _toc3b = mk_toc_root()
compiled.clear()
_restore = install_fake_delivery(font_fail=True)
try:
    res = P._step4_toc_pdf(root3b, P._Opts(root=root3b))
finally:
    _restore()
check("(f8) 字体检查失败：退出码 2 + 可操作原因，且**没有启动编译**（不静默换字体）",
      res.rc == 2 and "缺少 Windows 字体" in res.reason and not compiled,
      f"rc={res.rc} reason={res.reason} compiled={compiled}")

print("\n── (g) 缺 000 目录 ⇒ **自动创建**（B-226）；--skip-toc-pdf 仍跳过 ──")
root4 = mkroot()
mkfile(os.path.join(root4, "Chinese Content_20261021_130000.tex"), "NEW")
mkfile(os.path.join(root4, "contents_20261021_130000.tex"), "NEW-EN")
compiled.clear()
_restore = install_fake_delivery()
try:
    res = P._step4_toc_pdf(root4, P._Opts(root=root4))
finally:
    _restore()
check("(g1) 无 000* 目录且未 --skip-toc-pdf：**自动创建** 000 Table of Contents 后照常落位编译",
      res.rc == 0 and os.path.isdir(os.path.join(root4, "000 Table of Contents"))
      and [s for _d, s in compiled] == ["Chinese Content", "contents"],
      f"rc={res.rc} reason={res.reason} compiled={compiled}")
check("(g2) 自动创建的 000 目录里出现两份固定名 tex+pdf",
      all(os.path.isfile(os.path.join(root4, "000 Table of Contents", n))
          for n in ("Chinese Content.tex", "contents.tex",
                    "Chinese Content.pdf", "contents.pdf")))
compiled.clear()
_restore = install_fake_delivery()
try:
    rc, out, err = run_main(["--folder", root4, "--from", "4"])
finally:
    _restore()
check("(g3) main 级：缺 000 不再中止（rc=0，末行「5 步都跑完」）",
      rc == 0 and last_line(out).startswith("完成。出刊："), f"rc={rc} last={last_line(out)}")

# --skip-toc-pdf：000 目录齐 → 跳过且不复制不编译，退出码 0
root5, toc5 = mk_toc_root()
mkfile(os.path.join(toc5, "Chinese Content.pdf"), "%PDF fake\n")
mkfile(os.path.join(toc5, "contents.pdf"), "%PDF fake\n")
# 恢复 000 里旧的 .tex 内容标记，验证 --skip-toc-pdf 连复制都不做
mkfile(os.path.join(toc5, "Chinese Content.tex"), "STALE")
compiled.clear()


def must_not_compile(folder: str, stems):
    raise AssertionError("--skip-toc-pdf 时不应编译目录 PDF")


_restore = install_fake_delivery()
TD.compile_pdfs = must_not_compile
try:
    res = P._step4_toc_pdf(root5, P._Opts(root=root5, skip_toc_pdf=True))
finally:
    _restore()
check("(g4) --skip-toc-pdf 且 000 里 PDF 齐：退出码 0", res.rc == 0, f"rc={res.rc}")
check("(g5) --skip-toc-pdf 跳过整个第 4 步（连 tex 都不复制）",
      open(os.path.join(toc5, "Chinese Content.tex"), encoding="utf-8").read() == "STALE")
check("(g6) --skip-toc-pdf 时没有调用编译", not compiled)

os.remove(os.path.join(toc5, "contents.pdf"))
res = P._step4_toc_pdf(root5, P._Opts(root=root5, skip_toc_pdf=True))
check("(g7) --skip-toc-pdf 且 000 缺目录 PDF：退出码 1 + 明确告警缺哪份",
      res.rc == 1 and any("缺" in w and "contents.pdf" in w for w in res.warnings),
      f"rc={res.rc} warnings={res.warnings}")
# --skip-toc-pdf 且期目录根本没有 000：仍不自动创建（跳过就是跳过），告警两份都缺
root5b = mkroot()
mkfile(os.path.join(root5b, "Chinese Content_20261021_130000.tex"), "X")
mkfile(os.path.join(root5b, "contents_20261021_130000.tex"), "Y")
res = P._step4_toc_pdf(root5b, P._Opts(root=root5b, skip_toc_pdf=True))
check("(g7b) --skip-toc-pdf 反对称：无 000 时不自动创建目录、告警缺两份 PDF",
      res.rc == 1 and not os.path.isdir(os.path.join(root5b, "000 Table of Contents"))
      and len(res.warnings) == 1 and "Chinese Content.pdf" in res.warnings[0],
      f"rc={res.rc} warnings={res.warnings}")

orig_step4 = P._step4_toc_pdf
calls, restore = install_fake_steps()
P.STEP_FUNCS[4] = orig_step4          # 第 4 步保留真实现，其余步骤用假实现
_restore = install_fake_delivery()
try:
    rc, out, _ = run_main(["--folder", root5, "--only", "4", "--skip-toc-pdf"])
finally:
    _restore()
    restore()
check("(g8) main：--skip-toc-pdf 告警计入汇总、退出码 1，契约行仍最后一行",
      rc == 1 and "只跑了第 4 步" in last_line(out)
      and "⚠" in out and out.find("⚠") < out.rfind("完成。出刊："),
      f"rc={rc} last={last_line(out)}")

print("\n── (h) xelatex 缺失时按「缺工具」失败（测试不依赖本机装 TeX）──")
import compileall as CA  # noqa: E402  引擎目录在 sys.path[0]，取到的是 Engine/compileall.py
check("(h1) compileall 模块来自引擎（不是标准库同名模块）",
      pathlib.Path(CA.__file__).resolve().parent == ENGINE, str(CA.__file__))
_tmp = mkroot()
with unittest.mock.patch("shutil.which", return_value=None):
    ok, msg = CA.compile_tex_xelatex(_tmp, "whatever")
check("(h2) 找不到 xelatex：返回失败 + 可操作错误信息（不抛异常）",
      ok is False and "xelatex" in msg and "TeX Live" in msg, msg)

print("\n── (i) 端到端（合成期目录 + 假子进程）：五步真编排一次 ──")
root6 = mk_issue(mkroot())
order_scripts: list[str] = []
argv_by_script: dict[str, list[str]] = {}


def fake_run_e2e(argv: list[str]) -> P._StepResult:
    script = os.path.basename(argv[0])
    order_scripts.append(script)
    argv_by_script[script] = list(argv)
    if script == "toc.py":      # 模拟 toc 在期目录根产出两份带时间戳的目录 tex
        mkfile(os.path.join(root6, "Chinese Content_20261021_140000.tex"), "E2E-CN")
        mkfile(os.path.join(root6, "contents_20261021_140000.tex"), "E2E-EN")
    return P._StepResult(0)


compiled.clear()
_restore = install_fake_delivery()
try:
    with patched("_run_script", fake_run_e2e):
        rc, out, err = run_main(["--folder", root6])
finally:
    _restore()
check("(i1) 退出码 0", rc == 0, f"rc={rc} err={err.strip()}")
check("(i2) 子进程调用顺序 = infocheck → toc → compileall → mergepdf",
      order_scripts == ["infocheck.py", "toc.py", "compileall.py", "mergepdf.py"], str(order_scripts))
check("(i3) 第 ② 步只传 `--report`、**不硬传 `--template`**（模板解析与生成目录同一套，E8）",
      argv_by_script["toc.py"][1] == "--report" and "--template" not in argv_by_script["toc.py"]
      and argv_by_script["compileall.py"][1:] == ["--folder", root6],
      str(argv_by_script))
# 显式 --template 时才透传
_restore = install_fake_delivery()
try:
    with patched("_run_script", fake_run_e2e):
        run_main(["--folder", root6, "--only", "2", "--template", "/x/2026年第4期(89)"])
finally:
    _restore()
check("(i3b) 显式 `--template` 时第 ② 步透传（口径不变，仍可用）",
      argv_by_script["toc.py"][1:] == ["--report",
                                       os.path.join(root6, "信息检查_20261021_130000.txt"),
                                       "--template", "/x/2026年第4期(89)"],
      str(argv_by_script["toc.py"]))
check("(i4) 第 ④ 步真跑了：000 里出现复制来的目录 tex 与编译出的 PDF",
      open(os.path.join(root6, "000 Table of Contents", "Chinese Content.tex"),
           encoding="utf-8").read() == "E2E-CN"
      and os.path.isfile(os.path.join(root6, "000 Table of Contents", "contents.pdf")))
check("(i5) 第 ⑤ 步在编译（③）之后、且目录 PDF 已就绪",
      compiled and order_scripts.index("mergepdf.py") == 3)
check("(i6) 末行契约行「5 步都跑完」", last_line(out).startswith("完成。出刊：5 步都跑完（目录："),
      last_line(out))
check("(i7) 输出不含旧的「四步」口径", "四步" not in out)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
