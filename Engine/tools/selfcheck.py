#!/usr/bin/env python3.14
r"""引擎自查 `Engine/tools/selfcheck.py`（第二十二批，第 122 条）。

**为什么需要**：项目已经把门禁拆成很多条命令（full/quick 套件、文档看门狗、gen_docs、
App 包内 cmp、docx_audit、corpus、Swift/桥梁测试），但**没有一条「发版前自查」入口**，
顺序与参数靠人记；CI 只覆盖能离线跑的那部分。本脚本把「不依赖外部服务、几分钟内能跑完」
的检查收进一条命令，输出 PASS/FAIL/WARN，出错退 1。

**只读**：不写仓库文件（`--deep` 只写 /tmp 报告）。

用法（在 `Engine/` 下）：

    PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py            # quick 套件 + 文档 + 代码健康
    PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py --full     # full 套件（发版前用）
    PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py --skip-tests --skip-docs   # 只跑静态检查
    PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py --deep     # 再加 docx_audit 全库体检（~6s）
    PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py --corpus   # 再加 corpus 全库回归（约 2-9 分钟）

**检查清单**：
  1. 环境（Python 版本、vendor、目录结构）；
  2. 全部引擎模块 import 冒烟（`slcn/*.py`）；
  3. 引擎套件 quick/full（`tools/run_engine_tests.sh`）；
  4. 文档看门狗（AUTO 块 / 路径 / 编号 / App 包内一致性，`tests/test_docs_consistency.py`）；
  5. 代码健康（超长函数 >150 行、全仓只出现 1 次的函数=死代码候选、TODO/FIXME）——**只警告**；
  6. 案例引用（公开仓代码/文档里的 `第 N 条` 是否都能在笔记仓案例里找到）+ 案例库体检
     （`tools/case_audit.py --check`）；没有笔记仓时 SKIP（公开仓/CI 正常）；
  7. `--deep`：`docx_audit.py` 全库体检（需要稿件库）；
  8. `--corpus`：`corpus.py` 全库回归（需要稿件库，慢）。
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)                 # Engine/
REPO = os.path.dirname(ENGINE)                 # 公开仓根
VND = os.path.join(ENGINE, "vendor")

CASE_REF_RE = re.compile(r"第\s*(\d+)\s*条")
CASE_NUM_RE = re.compile(r"^### (\d+)\. ", re.M)
TEXT_EXTS = {".py", ".md", ".sh", ".swift", ".json", ".yml", ".yaml", ".tex", ".sty"}
SKIP_DIRS = {".git", "dist", "vendor", "out", "__pycache__", "node_modules", ".build", ".mc"}
LONG_FUNC_LINES = 150

FAILS: list[str] = []
WARNS: list[str] = []
SKIPS: list[str] = []


def _env() -> dict:
    env = dict(os.environ)
    paths = [VND, ENGINE]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["SLCN_SELFCHECK_NESTED"] = "1"        # 防自检->套件->自检递归
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(cmd: list[str], cwd: str | None = None, timeout: int = 1800) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd or ENGINE, env=_env(), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_env() -> None:
    if sys.version_info < (3, 10):
        FAILS.append(f"Python 版本太低：{sys.version.split()[0]}（需要 ≥ 3.10）")
        return
    if not os.path.isdir(VND):
        # CI/新克隆没有 vendor（依赖用 pip 装），只要关键第三方包能 import 就行
        try:
            import lxml.etree  # noqa: F401
            import pypdf  # noqa: F401
            import pypinyin  # noqa: F401
            print("PASS  环境：vendor 缺失但 pip 依赖可用（CI 形态）")
        except Exception as e:  # noqa: BLE001
            FAILS.append(f"vendor/ 不存在且 pip 依赖缺失：{VND}（{e}）")
    if not os.path.isdir(os.path.join(ENGINE, "slcn")):
        FAILS.append("Engine/slcn/ 不存在（cwd 不对？应在 Engine/ 下运行）")
        return
    print(f"PASS  环境：Python {sys.version.split()[0]}；vendor {'存在' if os.path.isdir(VND) else '缺失'}")


def check_imports() -> None:
    sys.path.insert(0, ENGINE)
    sys.path.insert(0, VND)
    bad = []
    files = sorted(f for f in os.listdir(os.path.join(ENGINE, "slcn")) if f.endswith(".py"))
    for f in files:
        mod = "slcn." + f[:-3]
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{mod}: {type(e).__name__}: {e}")
    if bad:
        FAILS.extend(f"import 失败：{b}" for b in bad[:10])
        print(f"FAIL  模块 import 冒烟：{len(bad)}/{len(files)} 个失败")
    else:
        print(f"PASS  模块 import 冒烟：{len(files)}/{len(files)}")


def check_tests(level: str) -> None:
    rc, out = run(["bash", os.path.join(ENGINE, "tools", "run_engine_tests.sh"), level])
    tail = [ln for ln in out.splitlines() if "汇总" in ln or "❌" in ln or "✅" in ln]
    if rc == 0:
        print(f"PASS  引擎套件（{level}）：{tail[-1].strip() if tail else 'rc=0'}")
    else:
        FAILS.append(f"引擎套件（{level}）红：{tail[-2:] if tail else out[-500:]}")
        print(f"FAIL  引擎套件（{level}）")


def check_docs() -> None:
    rc, out = run([sys.executable, os.path.join(ENGINE, "tests", "test_docs_consistency.py")])
    if rc == 0 and "失败 0 项" in out:
        print("PASS  文档看门狗（AUTO/路径/编号/App 包内一致）")
    else:
        FAILS.append("文档看门狗红")
        print("FAIL  文档看门狗")
        for ln in out.splitlines()[-8:]:
            print("   ", ln)


def _iter_text_files() -> list[str]:
    out: list[str] = []
    for base in (REPO,):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1] in TEXT_EXTS:
                    out.append(os.path.join(root, f))
    return out


def scan_code_health() -> None:
    """超长函数 / 死代码候选 / TODO —— **只警告**，不阻断。"""
    src_files = sorted(
        [os.path.join(ENGINE, f) for f in os.listdir(ENGINE) if f.endswith(".py")]
        + [os.path.join(ENGINE, "slcn", f) for f in os.listdir(os.path.join(ENGINE, "slcn"))
           if f.endswith(".py")]
        + [os.path.join(HERE, f) for f in os.listdir(HERE) if f.endswith(".py")]
    )
    all_text = ""
    for f in _iter_text_files():
        if f.endswith(".py"):
            try:
                all_text += open(f, encoding="utf-8", errors="replace").read() + "\n"
            except OSError:
                pass
    long_funcs: list[tuple[int, str, str]] = []
    dead: list[str] = []
    todos = 0
    for f in src_files:
        try:
            tree = ast.parse(open(f, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ln = (node.end_lineno or node.lineno) - node.lineno + 1
                if ln > LONG_FUNC_LINES:
                    long_funcs.append((ln, os.path.relpath(f, REPO), node.name))
                if not node.name.startswith("_") or node.name.startswith("__"):
                    continue
    for f in _iter_text_files():
        if f.endswith(".py"):
            try:
                text = open(f, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            todos += len(re.findall(r"\b(TODO|FIXME|XXX|HACK)\b", text))
    # 死代码候选：函数名在全仓（含测试/工具）只出现一次（= 只有定义）
    defs: list[tuple[str, str]] = []
    for f in src_files:
        try:
            tree = ast.parse(open(f, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        if "/tests/" in f.replace("\\", "/"):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append((os.path.relpath(f, REPO), node.name))
    for path, name in defs:
        if name in ("main", "__init__"):
            continue
        if len(re.findall(r"\b" + re.escape(name) + r"\b", all_text)) <= 1:
            dead.append(f"{path}::{name}")
    long_funcs.sort(reverse=True)
    print(f"WARN  代码健康：超长函数(>{LONG_FUNC_LINES} 行) {len(long_funcs)} 个；"
          f"死代码候选 {len(dead)} 个；TODO/FIXME {todos} 处（只警告，不阻断）")
    for ln, path, name in long_funcs[:5]:
        WARNS.append(f"超长函数 {path}::{name}() = {ln} 行")
    for d in dead[:10]:
        WARNS.append(f"死代码候选（全仓仅出现一次）：{d}")
    if len(dead) > 10:
        WARNS.append(f"……另有 {len(dead) - 10} 个死代码候选未列出")


def _case_numbers(notes_cases: str) -> set[int]:
    nums: set[int] = set()
    for f in os.listdir(notes_cases):
        if not f.endswith(".md") or f in ("INDEX.md", "DIGEST.md", "TEMPLATE.md"):
            continue
        try:
            text = open(os.path.join(notes_cases, f), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        nums.update(int(m.group(1)) for m in CASE_NUM_RE.finditer(text))
    return nums


def _find_notes_cases() -> str | None:
    cands = [os.path.join(REPO, "..", "EditToolBox-notes", "cases"),
             os.path.join(ENGINE, "docs", "cases")]
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isdir(c):
            return c
    return None


def check_cases() -> None:
    notes = _find_notes_cases()
    if not notes:
        SKIPS.append("案例检查（本机没有笔记仓 cases/；CI/公开仓正常）")
        print("SKIP  案例引用/案例库体检（没有笔记仓 cases/）")
        return
    nums = _case_numbers(notes)
    refs: dict[int, list[str]] = {}
    for f in _iter_text_files():
        if os.path.abspath(f).startswith(os.path.abspath(notes)):
            continue
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in CASE_REF_RE.finditer(text):
            n = int(m.group(1))
            if n == 0:                       # 「第 0 条」= HANDOFF 自己的任务锚点，合法
                continue
            refs.setdefault(n, []).append(os.path.relpath(f, REPO))
    missing = {n: v for n, v in refs.items() if n not in nums}
    if missing:
        FAILS.append(f"案例引用指向不存在的条目：{sorted(missing)[:10]}")
        print(f"FAIL  案例引用：{len(missing)} 个编号在笔记仓找不到")
    else:
        print(f"PASS  案例引用：{len(refs)} 个不同编号全部可解析（公开仓 {len(refs)} 处引用点）")
    gen = os.path.join(REPO, "tools", "case_audit.py")
    rc, out = run([sys.executable, gen, "--cases", notes, "--check"], cwd=REPO, timeout=300)
    if rc == 0:
        print("PASS  案例库体检（编号/索引/状态/相似度/路径）")
    else:
        FAILS.append("案例库体检有错误（见 case_audit 输出）")
        print("FAIL  案例库体检")
        for ln in out.splitlines()[-10:]:
            print("   ", ln)


def check_app_bundle() -> None:
    app_engine = os.path.join(REPO, "dist", "编辑部工具箱.app", "Contents", "Resources", "Engine")
    if not os.path.isdir(app_engine):
        SKIPS.append("App 包内一致性（本机没有 dist/App；CI 正常）")
        print("SKIP  App 包内一致性（没有 dist/App）")
        return
    # 包内引擎文件白名单与 `tools/gen_docs.py` 的 APP_FILES **同源**，
    # 避免把开发专用脚本（ablation/corpus/docx_audit…）误判成「包内过期」。
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_slcn_gen_docs", os.path.join(HERE, "gen_docs.py"))
    gd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gd)
    bad = []
    for rel in gd.APP_FILES:
        if not _same(os.path.join(ENGINE, rel), os.path.join(app_engine, rel)):
            bad.append(rel)
    if bad:
        FAILS.append(f"App 包内引擎过期：{bad[:5]}")
        print(f"FAIL  App 包内一致性（{len(bad)} 个文件不同）")
    else:
        print("PASS  App 包内一致性（引擎文件与仓库逐字节相同）")


def _same(a: str, b: str) -> bool:
    try:
        return open(a, "rb").read() == open(b, "rb").read()
    except OSError:
        return False


def check_deep() -> None:
    out_dir = os.path.join("/tmp", "slcn_selfcheck_audit")
    rc, out = run([sys.executable, os.path.join(ENGINE, "docx_audit.py"),
                   "--out-dir", out_dir], cwd=ENGINE, timeout=1800)
    summary = [ln for ln in out.splitlines() if "口径内成功解析" in ln]
    if rc == 0:
        print(f"PASS  docx_audit 全库体检：{summary[-1].strip() if summary else 'rc=0'}")
    elif "不含稿件库" in out or "找不到稿件" in out:
        SKIPS.append("docx_audit（本机没有稿件库）")
        print("SKIP  docx_audit（没有稿件库）")
    else:
        FAILS.append("docx_audit 失败")
        print("FAIL  docx_audit")


def check_corpus() -> None:
    rc, out = run([sys.executable, os.path.join(ENGINE, "corpus.py"), "--jobs", "auto"],
                  cwd=ENGINE, timeout=3600)
    if rc == 0:
        lines = [ln for ln in out.splitlines() if "成功 " in ln or "中位数" in ln]
        print("PASS  corpus 全库回归：" + ("；".join(x.strip() for x in lines[:3]) or "rc=0"))
    elif "不含稿件库" in out or "找不到稿件" in out:
        SKIPS.append("corpus（本机没有稿件库）")
        print("SKIP  corpus（没有稿件库）")
    else:
        FAILS.append("corpus 失败")
        print("FAIL  corpus 全库回归")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="引擎自查（只读）")
    ap.add_argument("--quick", action="store_true", help="quick 套件（默认）")
    ap.add_argument("--full", action="store_true", help="full 套件（发版前）")
    ap.add_argument("--skip-tests", action="store_true", help="不跑引擎套件")
    ap.add_argument("--skip-docs", action="store_true", help="不跑文档看门狗")
    ap.add_argument("--no-cases", action="store_true", help="不查案例库（CI/无笔记仓时）")
    ap.add_argument("--deep", action="store_true", help="加 docx_audit 全库体检")
    ap.add_argument("--corpus", action="store_true", help="加 corpus 全库回归（慢）")
    args = ap.parse_args(argv)
    if os.environ.get("SLCN_SELFCHECK_NESTED"):
        # 被套件/自查嵌套调用时不做任何子进程检查，只跑静态项（防递归）
        pass
    t0 = time.time()
    print("=" * 70)
    print("引擎自查（selfcheck）")
    print("=" * 70)
    check_env()
    check_imports()
    if not args.skip_tests and os.environ.get("SLCN_SELFCHECK_NESTED") != "1":
        check_tests("full" if args.full else "quick")
    elif not args.skip_tests:
        SKIPS.append("引擎套件（嵌套调用时跳过，防递归）")
        print("SKIP  引擎套件（嵌套调用，防递归）")
    if not args.skip_docs and os.environ.get("SLCN_SELFCHECK_NESTED") != "1":
        check_docs()
    elif not args.skip_docs:
        SKIPS.append("文档看门狗（嵌套调用时跳过）")
        print("SKIP  文档看门狗（嵌套调用，防递归）")
    scan_code_health()
    if not args.no_cases:
        check_cases()
    else:
        SKIPS.append("案例检查（--no-cases）")
    check_app_bundle()
    if args.deep:
        check_deep()
    if args.corpus:
        check_corpus()

    print("-" * 70)
    print(f"汇总：失败 {len(FAILS)} 项；警告 {len(WARNS)} 项；SKIP {len(SKIPS)} 项；"
          f"耗时 {time.time() - t0:.1f}s")
    for f in FAILS:
        print(f"  FAIL  {f}")
    for w in WARNS[:15]:
        print(f"  WARN  {w}")
    if len(WARNS) > 15:
        print(f"  WARN  ……另有 {len(WARNS) - 15} 条警告未列出")
    if FAILS:
        print("❌ 自查未通过")
        return 1
    print("✅ 自查通过" + ("（有警告，人工过一眼）" if WARNS else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
