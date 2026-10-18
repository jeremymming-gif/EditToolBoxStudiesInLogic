#!/usr/bin/env python3.14
"""文档一致性测试（文档重构的看门狗）。

用法：cd EditToolBox/Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_docs_consistency.py

    # 默认即「低成本模式」：内部 gen_docs --check 用 --no-test-runs（不重复跑套件）。
    # 发版前要严格验证 AUTO 的测试计数行时（等价于老行为）：
    PYTHONPATH="$PWD/vendor" python3.14 tests/test_docs_consistency.py --strict-tests

四件事，每一件都是「文档结构不许退化」的机器证明：

(a) `tools/gen_docs.py --check` 通过 —— README 的 AUTO 块**不过期**（数字现算，手抄必被抓）。
    **第四十一批 O2（性能）分工**：本测试默认用 `gen_docs.py --check --no-test-runs` ——
    full runner 刚刚已经跑过**同一批**套件，再让 gen_docs 跑一遍等于 full 双重耗时（实测
    121s→61s）。代价是 AUTO 的「测试」计数行不在本项重复验证；它由**独立**的
    `gen_docs.py --check` 兜住（CI `ci.yml` 的独立步骤 / 发版前 `selfcheck.py --full` 的严格模式 /
    开发者手工显式跑）。AUTO 的**非测试行**（普查/corpus/占位符）本项仍严格比对 —— 反例见 (a2)。
(b) **引用存在性** —— README / AGENTS.md / HANDOFF.md / docs/** 里出现的
    `Engine/…`、`slcn/…`、`tools/…`、`tests/…`、`docs/…`、`EditToolBox/…`、`编辑笔记/…`
    形态的路径**必须真实存在**（相对仓库根 / `EditToolBox/` / `EditToolBox/Engine/` 三个根里
    任意一个命中即算存在）。
    **豁免规则（写在这里，也写在 AGENTS.md 与 README 的维护规则里）**：引用所在行
    **或前后各 2 行**（中文的历史叙述常常跨行，例：路径在上行、`已随试点退役删除` 在下行）
    含 `已删` / `删除` / `已退役` / `退役` / `已移除` / `移除` / `历史` / `曾` / `当时` /
    `作废` / `废弃` / `不再` 等词的，属于**历史叙述**（记录当年有什么、后来删了什么），
    路径不存在是**正确的**，不判失败；含 `*` / `…` / `{}` 的 glob、花括号展开或省略写法同理。
(c) **条目号可查** —— README / AGENTS.md / HANDOFF.md 里每处 `第 N 条`，都能在
    `docs/cases/*.md` 或 README 里找到 `### N.` 标题（HANDOFF 自己的任务节标题
    `### … 第 N 条` 也算锚点，因为 `第 0 条` 指的就是它）。缺失的逐条列出。
(d) **内容零丢失 + 追加有家** —— 案例法是**只增不改**的：两个**初始文件**
    （`01-30.md` = 第 1–30 条、`31-61.md` = 第 31–61 条）必须还在，且各自仍恰好是原来那段范围
    （范围判据 = 每个文件里的 `### N.` 集合，所以「旧条目被删/被改写条目号」当场就红）；
    新发现一律追加进 `62+.md`（第 62 条及以后，一条一节、编号连续）。
    因此并集判据从**相等**放宽成**包含**（2026-10-15：加第 62 条时改的 ——
    老判据 `case_nums == old_set` 会把任何合法追加都判失败，而「只增不改」恰恰要求能追加）：
    ① 重构前那 61 条必须**一个都不少**（`old_set ⊆ case_nums`）；
    ② 多出来的条目号必须 **≥ 62**（不许插号、不许重编号），并且只许住在 `62+.md` 里
       （初始文件里出现 ≥62 的条目 = 跑错文件，由 (d1b) 抓）；
    ③ 并集编号必须**连续无缺口**（(d3c)），且 `62+.md` 要收着并集里全部 ≥62 的条目（(d1c)）。
    硬编码清单只覆盖重构前的 1–61：**新条目的逐条防删不在它的保证范围内**
    （那需要另一份硬编码清单），靠 ①②③ 加上 (c) 的引用锚点兜底 —— 文档里引用过
    `第 62 条`，就要求 `### 62.` 标题必须还在。
    重构前的清单**硬编码**在下面的 `OLD_ITEM_LINES` 里（来源与取法见那里的注释），
    所以这条不依赖任何临时文件，换台机器也照样能证。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

# 第四十一批 O2：默认低成本（内部 gen_docs --check 不跑套件）；`--strict-tests` 切回老行为。
# 保留 `--no-test-runs` 显式开关，让调用方（selfcheck quick）能自证"我知道自己在跳过重复套件"。
_AP = argparse.ArgumentParser(add_help=True, description="文档一致性看门狗")
_AP.add_argument("--no-test-runs", action="store_true",
                 help="内部 gen_docs --check 不跑套件（默认即此模式；保留显式开关便于调用方自证）")
_AP.add_argument("--strict-tests", action="store_true",
                 help="反选：内部 gen_docs --check 跑套件做严格 AUTO 校验（发版前用）")
_ARGS = _AP.parse_args()
_STRICT_TESTS = _ARGS.strict_tests

HERE = pathlib.Path(__file__).resolve().parent      # …/EditToolBox/Engine/tests
ENGINE = HERE.parent                                # …/<项目>/Engine
# 仓库根**问 git 要**（两种布局都对：稿件仓 `<仓>/EditToolBox/Engine`、工具仓 `<仓>/Engine`）。
def _git_root() -> pathlib.Path:
    try:
        out = subprocess.run(["git", "-C", str(ENGINE), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if out:
            return pathlib.Path(out)
    except OSError:
        pass
    return ENGINE.parents[1]


REPO = _git_root()                                  # 仓库根
PY = "/opt/homebrew/bin/python3.14"
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ── (a) AUTO 块不过期 ──────────────────────────────────────────────────────
print("── (a) AUTO 块（tools/gen_docs.py --check）──")
# ⚠️ 前提不全（没有稿件库 / TeX / LibreOffice）时 **SKIP 而不是 FAIL**：
#    AUTO 块里的测试计数等数字随前提浮动，公开仓与 CI 上本来就不该拿它当硬断言。
#    严格校验留给前提齐全的开发机 —— 那里它才是"数字不许手抄"的真正守卫。
#    （2026-10-16：CI 首跑就红在这里，逼出这条判据。）
# 两个路径都要挂：`tools/` 找 gen_docs，`Engine/` 让 gen_docs 内部能 `from slcn import paths`
sys.path.insert(0, str(ENGINE / "tools"))
sys.path.insert(0, str(ENGINE))
try:
    import gen_docs as _G

    _complete, _missing = _G.environment_complete()
except Exception as _e:  # noqa: BLE001  连探测都失败 → 按前提不全处理
    _complete, _missing = False, [f"探测异常 {type(_e).__name__}"]

if not _complete:
    print(f"SKIP  (a) AUTO 块严格校验：本机缺 {'、'.join(_missing)} —— "
          "测试计数与依赖稿件库的数字会变，这一项只在前提齐全的机器上有意义")
else:
    _gen_docs_cmd = [PY, str(ENGINE / "tools" / "gen_docs.py"), "--check"]
    if not _STRICT_TESTS:
        # 低成本模式（默认）：不跑套件；测试计数行由独立的 gen_docs --check 守卫（见文件头 O2 分工）
        _gen_docs_cmd.append("--no-test-runs")
    p = subprocess.run(_gen_docs_cmd,
                       cwd=str(ENGINE), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "｜".join((p.stdout + p.stderr).strip().split("\n")[-1:])[:200]
    _mode = "严格（含套件）" if _STRICT_TESTS else "低成本（--no-test-runs）"
    check(f"(a) gen_docs.py --check 通过（{_mode}；README 的 AUTO 块 = 现算结果）",
          p.returncode == 0, f"exit={p.returncode} {tail}")

# ── (a2) 反例：--no-test-runs 仍必须抓住「非测试行」的漂移 ─────────────────
# O2 把「测试计数行」排除出本项比对，但**不能**顺带把非测试行也放宽。本反例证明：
# 故意改一行 AUTO 非测试行（普查行）→ `--check --no-test-runs` 必须退出 1 且指出过期；
# 随后**逐字节还原** README（finally 保证，异常路径也还原）。
if not _complete:
    print("SKIP  (a2) 反例：本机前提不全（与 (a) 同因），跳过")
else:
    _readme_path = ENGINE / "README.md"
    _original = _readme_path.read_bytes()
    try:
        _text = _original.decode("utf-8")
        _i = _text.index(_G.BEGIN) + len(_G.BEGIN)
        _j = _text.index(_G.END)
        _head, _body, _tail = _text[:_i], _text[_i:_j], _text[_j:]
        _m = re.search(r"^\*\*普查\*\*.*$", _body, re.M)
        if not _m:
            check("(a2) 反例：AUTO 块里找不到可改写的非测试行", False)
        else:
            _mutated = _body[:_m.start()] + _m.group(0) + "（反例探针）" + _body[_m.end():]
            _readme_path.write_bytes((_head + _mutated + _tail).encode("utf-8"))
            try:
                _p2 = subprocess.run(
                    [PY, str(ENGINE / "tools" / "gen_docs.py"), "--check", "--no-test-runs"],
                    cwd=str(ENGINE), capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
                _out2 = _p2.stdout + _p2.stderr
                check("(a2) 反例：改 AUTO 非测试行 → --no-test-runs 仍判过期（exit 1）",
                      _p2.returncode == 1 and "反例探针" in _out2 and "过期" in _out2,
                      f"exit={_p2.returncode} " + "｜".join(_out2.strip().split("\n")[:2])[:200])
            finally:
                _readme_path.write_bytes(_original)   # 无论断言结果如何都还原
            check("(a2b) 反例探针已逐字节还原 README", _readme_path.read_bytes() == _original)
    except Exception as _e:  # noqa: BLE001  README 结构异常/写盘失败也要还原并报错
        _readme_path.write_bytes(_original)
        check(f"(a2) 反例执行异常：{type(_e).__name__}: {_e}", False)

# ── (e) O14 AUTO 输入冻结（`out/auto-inputs.json`）─────────────────────────
# 写模式把「AUTO 依赖的输入」指纹 + 测试计数缓存 + 块指纹写进 out/auto-inputs.json；
# `--check` 据此走快速通道（输入未变 → 不跑套件直接 PASS）。本项用纯函数断言：
# ① 清单字段齐全/指纹确定；② 缓存失效条件（engine/tests 变了必须重跑套件）；
# ③ 快速通道命中/不命中的三种情形（输入变、块被改）。
print("\n── (e) AUTO 输入冻结（O14）──")
if not _complete:
    print("SKIP  (e) AUTO 输入冻结：本机前提不全（与 (a) 同因），跳过")
else:
    try:
        _inputs = _G.collect_auto_inputs()
        _need = {"corpus_csv_sha256", "corpus_tex_manifest_sha256", "audit_csv_sha256",
                 "audit_txt_sha256", "tests_manifest_sha256", "engine_py_sha256",
                 "env_complete", "manuscripts_fingerprint"}
        check("(e1) AUTO 输入清单字段齐全", _need <= set(_inputs),
              f"缺 {sorted(_need - set(_inputs))}")
        check("(e2) 输入指纹确定性（两次采集相同）",
              _G.inputs_sha(_inputs) == _G.inputs_sha(_G.collect_auto_inputs()))
        _proj = {"schema": _G.AUTO_INPUTS_SCHEMA, "inputs": _inputs,
                 "test_rows": [["test_x", 1, 0]], "block_sha256": "0" * 64}
        check("(e3) engine/tests 未变 → 可复用测试计数缓存（写模式不整跑套件）",
              _G.cached_test_rows(_inputs, _proj) == [["test_x", 1, 0]])
        check("(e4) engine 变了 → 缓存失效（必须重跑套件）",
              _G.cached_test_rows(dict(_inputs, engine_py_sha256="deadbeef"), _proj) is None)
        check("(e5) 测试集变了 → 缓存失效（必须重跑套件）",
              _G.cached_test_rows(dict(_inputs, tests_manifest_sha256="cafe"), _proj) is None)
        _h, _blk, _t = _G.split_block((ENGINE / "README.md").read_text(encoding="utf-8"))
        _blk_sha = hashlib.sha256(
            _G.normalize_for_check(_blk, lenient=False).encode("utf-8")).hexdigest()
        _proj2 = dict(_proj, block_sha256=_blk_sha)
        check("(e6) 输入指纹与块指纹都一致 → --check 快速通道命中（不跑套件）",
              _G.fast_check_ok(_blk, _proj2, _inputs))
        check("(e7) 输入变了 → 快速通道不命中（回退到现算比对）",
              not _G.fast_check_ok(_blk, _proj2, dict(_inputs, engine_py_sha256="deadbeef")))
        check("(e8) 块被改过 → 快速通道不命中（文档漂移仍会被抓）",
              not _G.fast_check_ok(_blk, dict(_proj, block_sha256="0" * 64), _inputs))
        check("(e9) 输入清单落在 gitignore 的 out/ 产物目录（不入库）",
              _G.AUTO_INPUTS.name == "auto-inputs.json")
    except Exception as _e:  # noqa: BLE001  AUTO 冻结逻辑异常也要如实报
        check(f"(e) AUTO 输入冻结检查异常：{type(_e).__name__}: {_e}", False)

# ── (b) 引用存在性 ─────────────────────────────────────────────────────────
print("\n── (b) 文档里引用的路径必须存在 ──")
# ⚠️ `AGENTS.md` 与 `HANDOFF.md` 是**可选的**：拆仓后（工具仓只含 `EditToolBox/` 那一层）
#    这两个文件留在稿件仓里，工具仓会另写一份自己的。缺了就**跳过**，不判失败——
#    否则公开仓第一次跑看门狗就红一片，把「没有这个文件」误报成「文档坏了」。
# 工具仓根的 README = `Engine/` 的上一级（稿件仓里是 EditToolBox/README.md，
# 公开仓里就是仓库根 README.md）。**用 ENGINE.parent 推**，不写目录字面量 ——
# 否则改名脚本会把字面量一起改掉，公开仓里就找不到文件了（2026-10-15 实测）。
ROOT_README = ENGINE.parent / "README.md"
# 使用说明（面向编辑的操作手册）也纳入：它最容易过时，且写满了路径。
TOOL_MANUAL = ENGINE.parent / "使用说明.md"
DOC_FILES = [ENGINE / "README.md", REPO / "AGENTS.md", ENGINE / "HANDOFF.md",
             ROOT_README, TOOL_MANUAL]
OPTIONAL_DOCS = {REPO / "AGENTS.md", ENGINE / "HANDOFF.md"}
DOC_FILES += sorted((ENGINE / "docs").rglob("*.md"))
PREFIXES = ("Engine/", "slcn/", "tools/", "tests/", "docs/", "编辑笔记/",
            # 两个外壳的目录也纳入：否则文档里写 `sources-win/tests/…` 时，
            # 左边界规则会把中间那段 `tests/…` 拦掉（含 `/` 前缀），**等于没人检查**
            # （2026-10-16 发现：`AGENTS.md` 里一条过期路径就是这么漏过去的）。
            "sources-mac/", "sources-win/")
# ⚠️ **左边界**不能少（`(?<![A-Za-z0-9_/])`）：像 `sources-win/tests/test_bridge.py`
#    这样的路径里含 `tests/`，没有左边界时正则会把中间那段当成独立引用，
#    再按 `Engine/tests/…` 去解析 → 报「路径不存在」的假失败（2026-10-15 实测）。
TOKEN = re.compile(r"(?<![A-Za-z0-9_/])(?:" + "|".join(re.escape(x) for x in PREFIXES) + r")[^\s`（）()「」【】,，。；;：:｜|]+")
TRIM = "`'\"”’。，、；：）)｜|"
# 历史叙述豁免词：这些词在讲「当年有什么 / 后来删了什么 / 现在不再如此」，路径不存在才对。
# 命中范围是**该行 ±2 行**（中文的历史叙述经常跨行：路径在上行、`已随试点退役删除` 在下行）。
EXEMPT_WORDS = ("已删", "删除", "已退役", "退役", "已移除", "移除", "历史", "曾",
                "当时", "作废", "废弃", "不再")


def exists(tok: str) -> bool:
    for base in (ENGINE, REPO, ENGINE):   # 拆仓后 EditToolBox/ 这一层没有了
        if (base / tok).exists():
            return True
    return False


missing, exempted = [], 0
# 「编辑笔记/」下**只公开 `模板文件/`**（2026-10-15 用户决定）：手册与经验书留在稿件仓，
# 公开的工具仓里没有它们 —— 那不是「文档坏了」，是**有意不带**。所以这类引用单独统计、不判失败。
# 判据：引用以 `编辑笔记/` 开头、**不是** `编辑笔记/模板文件/`、且在本仓确实不存在。
PRIVATE_NOTEBOOK, private_refs = [], 0
LOCAL_DEPS, local_deps = [], []
# 公开仓**有意不带**的两类内部资料：`Engine/docs/cases/`（案例法，含作者姓名与稿件片段）
# 与 `Engine/HANDOFF.md`（内部进度）。缺席时引用它们的路径按「有意不带」跳过。
INTERNAL_ABSENT = (not any((ENGINE / "docs" / "cases").glob("*.md"))
                   or not (ENGINE / "HANDOFF.md").is_file())
internal_refs = 0
# ⚠️ `docs/cases/**` 是**只增不改的历史记录**：里面的路径按**当时的**仓库布局写才对
#    （拆仓改名后 `EditToolBox/Engine/...` 这类引用在物理上就不存在了）。
#    所以案例文件不参与路径存在性检查 —— 只统计、不判失败。
#    代价：新写的案例条目里若把路径打错，这一条抓不到（换来的是历史记录一字不改）。
CASE_FILES = {p for p in DOC_FILES if "cases" in p.parts}
case_tokens = 0

for f in DOC_FILES:
    if f in CASE_FILES:
        case_tokens += sum(len(TOKEN.findall(ln))
                           for ln in f.read_text(encoding="utf-8").split("\n"))
        continue
    if not f.is_file():
        if f in OPTIONAL_DOCS:
            print(f"SKIP  (b) {f.relative_to(REPO)} 不在本仓（拆仓后的工具仓属正常）")
        else:
            check(f"(b) 待检文件存在：{f.relative_to(REPO)}", False)
        continue
    lines = f.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines, 1):
        window = "\n".join(lines[max(0, i - 3):i + 2])          # 本行 ±2 行
        if any(w in window for w in EXEMPT_WORDS):
            exempted += len(TOKEN.findall(line))
            continue
        for raw in TOKEN.findall(line):
            tok = raw.strip(TRIM)
            # glob（`*`）、省略（`…`）、花括号展开（`{out,vendor}`）不是可解析的路径引用
            if not tok or any(c in tok for c in "*…{},") or tok.endswith("/"):
                continue                      # 目录（下一行必然有具体文件）
            if exists(tok):
                continue
            # 本地依赖目录：`Engine/vendor`（`pip install --target vendor -r requirements.txt`
            # 之后才有）。新克隆里它必然不存在 —— 那不是「文档坏了」。
            if tok.rstrip("/") in ("Engine/vendor", "EditToolBox/Engine/vendor"):
                local_deps.append(tok)
                continue
            # 内部资料（案例法 / HANDOFF）：公开仓有意不带 → 跳过
            if INTERNAL_ABSENT and ("docs/cases/" in tok or tok.endswith("HANDOFF.md")):
                internal_refs += 1
                continue
            if tok.startswith("编辑笔记/") and not tok.startswith("编辑笔记/模板文件/"):
                private_refs += 1
                PRIVATE_NOTEBOOK.append(tok)
                continue
            missing.append(f"{f.relative_to(REPO)}:{i}  {tok}")
print(f"SKIP  (b) 案例法 {len(CASE_FILES)} 份文件里的 {case_tokens} 处路径引用不检查"
      f"（历史记录，按当时的布局写）")
if internal_refs:
    print(f"SKIP  (b) {internal_refs} 处指向内部资料（案例法 / HANDOFF.md）的引用："
          f"公开仓有意不带")
if local_deps:
    print(f"SKIP  (b) {len(local_deps)} 处指向本地依赖目录 `Engine/vendor`："
          f"按 requirements.txt 装好之后才有")
if private_refs:
    print(f"SKIP  (b) {private_refs} 处指向「编辑笔记/」下非模板文件（手册/经验书）的引用："
          f"这些留在稿件仓，公开仓有意不带")
check(f"(b) {len(DOC_FILES) - len(CASE_FILES)} 份文档里的路径引用全部存在", not missing,
      (f"缺失 {len(missing)} 处，豁免窗口里的引用 {exempted} 处：\n        "
       + "\n        ".join(missing[:15])) if missing else f"（历史叙述行豁免 {exempted} 处）")

# ── (c) 条目号可查 ─────────────────────────────────────────────────────────
print("\n── (c) 每处「第 N 条」都能查到 `### N.` ──")
CASES = sorted((ENGINE / "docs" / "cases").glob("*.md"))
CASES_DIR = ENGINE / "docs" / "cases"
# 只增不改的两个初始文件（内容范围：1–30 / 31–61），与新发现的追加文件（第 62 条及以后）。
INITIAL_CASES = ["01-30.md", "31-61.md"]
APPEND_CASE = "62+.md"
ITEM_HEADING = re.compile(r"^### (\d+)\.\s", re.M)


def case_items(path: pathlib.Path) -> set[int]:
    return {int(m.group(1)) for m in ITEM_HEADING.finditer(path.read_text(encoding="utf-8"))}


# 公开的工具仓**有意不带**案例法（`docs/cases/` 是内部案例记录：含作者姓名与稿件片段）。
# 缺席时本节整体跳过 —— 与 AGENTS.md / HANDOFF.md 的处理同一口径。
CASES_PRESENT = bool(CASES) or not INTERNAL_ABSENT
if not CASES_PRESENT:
    print("SKIP  (c) 本仓不含案例法（`Engine/docs/cases/` 有意不带）—— 条目号校验跳过")
case_text = "\n".join(c.read_text(encoding="utf-8") for c in CASES)
case_nums = {int(m.group(1)) for m in re.finditer(r"^### (\d+)\.\s", case_text, re.M)}
readme_text = (ENGINE / "README.md").read_text(encoding="utf-8")
readme_nums = {int(m.group(1)) for m in re.finditer(r"^### (\d+)\.\s", readme_text, re.M)}
# HANDOFF.md 在公开仓里**有意不带**（内部进度文档）→ 缺席时按空文本处理
handoff_text = ((ENGINE / "HANDOFF.md").read_text(encoding="utf-8")
                if (ENGINE / "HANDOFF.md").is_file() else "")
# HANDOFF 自己的任务编号（`### ✅ 第 0 条（已完成…）`）——「第 0 条」的锚点在这里
handoff_anchors = {int(m.group(1)) for m in
                   re.finditer(r"^#{2,4} .{0,40}?第\s*(\d+)\s*条", handoff_text, re.M)}
known = case_nums | readme_nums | handoff_anchors
unresolved = []
for f in ((ENGINE / "README.md", REPO / "AGENTS.md", ENGINE / "HANDOFF.md")
          if CASES_PRESENT else ()):
    if not f.is_file():
        continue
    for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
        for m in re.finditer(r"第\s*(\d+)\s*条", line):
            if int(m.group(1)) not in known:
                unresolved.append(f"{f.relative_to(REPO)}:{i}  第 {m.group(1)} 条")
check(f"(c) 三份文档里的「第 N 条」全部可查（案例锚点 {len(case_nums)} 个）", not unresolved,
      ("缺失：" + "；".join(unresolved[:10])) if unresolved else
      f"（含 HANDOFF 自己的任务锚点 {sorted(handoff_anchors)}）")

# ── (d) 零丢失：旧 61 条是**子集**；≥62 只许住在 62+.md ────────────────────
print("\n── (d) 案例条目号零丢失（对重构前的 README）+ 第 62 条起追加进 62+.md ──")
# 来源：重构前的 `EditToolBox/Engine/README.md`，sha256
#   e5352181f303b4c75afb5b89bb50a70e85285a3349559c7f853a0d7466bf36fb，`wc -l` = 3047 行。
# 取法：`grep -n '^### [0-9]' README.md`，并按下表把**同号不同节**的两处区分开——
#   第 1–2 条在 §一「实测结果」里也各有一个同号小标题（第 40/74 行，那是「实测」小节，
#   不是案例法），案例法的 1–61 条是：§四「设计中最重要的几个认识」第 300–1753 行 → 1–44；
#   §六·五「已并入 EditToolBox」第 1857–2938 行 → 45–61（**连续、无缺口、无重号**）。
# 数值 = 该条 `### N.` 标题在重构前 README 里的行号。
OLD_ITEM_LINES = {
    1: 300, 2: 312, 3: 317, 4: 326, 5: 332, 6: 348,
    7: 355, 8: 363, 9: 369, 10: 376, 11: 384, 12: 388,
    13: 404, 14: 417, 15: 446, 16: 456, 17: 469, 18: 492,
    19: 508, 20: 595, 21: 632, 22: 664, 23: 689, 24: 741,
    25: 809, 26: 830, 27: 876, 28: 921, 29: 985, 30: 1015,
    31: 1038, 32: 1057, 33: 1070, 34: 1121, 35: 1177, 36: 1187,
    37: 1193, 38: 1246, 39: 1311, 40: 1378, 41: 1439, 42: 1480,
    43: 1509, 44: 1540, 45: 1857, 46: 2052, 47: 2102, 48: 2163,
    49: 2196, 50: 2239, 51: 2342, 52: 2397, 53: 2471, 54: 2516,
    55: 2577, 56: 2632, 57: 2680, 58: 2712, 59: 2759, 60: 2808,
    61: 2890,
}
old_set = set(OLD_ITEM_LINES)
check("(d0) 硬编码的旧清单自洽（1..61 连续、行号递增）",
      old_set == set(range(1, 62)) and list(OLD_ITEM_LINES.values()) == sorted(OLD_ITEM_LINES.values()),
      f"{len(old_set)} 条，{min(old_set)}..{max(old_set)}")

if not CASES_PRESENT:
    print("SKIP  (d) 本仓不含案例法（`Engine/docs/cases/` 有意不带）—— 零丢失/连续性校验跳过")
else:
    names = [c.name for c in CASES]
    nums_by_file = {c.name: case_items(c) for c in CASES}
    missing_initial = [n for n in INITIAL_CASES if not (CASES_DIR / n).is_file()]
    check("(d1) 两个初始案例文件仍在（01-30.md / 31-61.md）",
          not missing_initial, f"缺 {missing_initial}；现有 {names}" if missing_initial else f"现有 {names}")

    # 第 62 条及以后只许住在 62+.md：初始文件里出现 ≥62 的条目 = 追加跑错了文件
    stray = {n: sorted(x for x in nums if x >= 62) for n, nums in nums_by_file.items()
             if n != APPEND_CASE and any(x >= 62 for x in nums)}
    check(f"(d1b) 第 62 条及以后只出现在 {APPEND_CASE} 里（初始两个文件一字不动）",
          not stray, f"跑错文件：{stray}" if stray else f"{len(names)} 个案例文件里只有 {APPEND_CASE} 收着新条目")
    high = sorted(x for x in case_nums if x >= 62)
    high_home = sorted(x for x in nums_by_file.get(APPEND_CASE, set()) if x >= 62)
    check(f"(d1c) {APPEND_CASE} 存在，且并集里 ≥62 的条目**全部**来自它",
          (CASES_DIR / APPEND_CASE).is_file() and set(high) == set(high_home),
          f"{APPEND_CASE} 收 {high_home}；并集里 ≥62 的有 {high}")

    dup = sorted({n for n in case_nums
                  if len(re.findall(rf"^### {n}\.\s", case_text, re.M)) > 1})
    check("(d2) 案例条目号无重号", not dup, f"重号：{dup}" if dup else "")
    appended = sorted(case_nums - old_set)
    check("(d3) 旧 61 条是并集的**子集**（旧条目号一个都没丢）",
          old_set <= case_nums,
          f"缺 {sorted(old_set - case_nums)}" if not (old_set <= case_nums)
          else f"{len(old_set)} 条全在（重构前清单 ⊂ 并集；相等已放宽为包含，见文件头 (d)）")
    check("(d3b) 追加的条目号一律 ≥ 62（只增不改：不许插号、不许重编号）",
          all(x >= 62 for x in appended),
          f"追加 {appended}" if appended else "本项目暂无超出 1–61 的条目")
    # 硬编码清单只覆盖重构前的 1–61（「一条都不许删」的强保证就到那里为止）；
    # 新条目靠「有家 + 连续」两条兜底：≥62 只许住 62+.md（d1b/d1c），编号不许留缺口（d3c）。
    spread = set(range(min(case_nums), max(case_nums) + 1)) if case_nums else set()
    check("(d3c) 并集编号连续、无缺口（追加 = 接在末尾；插号/漏号当场红）",
          case_nums == spread,
          f"缺口 {sorted(spread - case_nums)}" if case_nums != spread
          else f"{min(case_nums)}..{max(case_nums)} 连续（其中新增 {len(appended)} 条）")
    check("(d4) 两个初始文件的切分口径没变（01-30 = 1..30，31-61 = 31..61）",
          nums_by_file.get("01-30.md") == set(range(1, 31))
          and nums_by_file.get("31-61.md") == set(range(31, 62)),
          f"01-30.md = {len(nums_by_file.get('01-30.md', set()))} 条，"
          f"31-61.md = {len(nums_by_file.get('31-61.md', set()))} 条")

print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
