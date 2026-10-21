#!/usr/bin/env python3.14
r"""批74 / B-110（RB-08 自指循环）回归测试：gen_docs 写模式「数据块先行」一次写绿。

为什么有这个测试（RB-08，连续 3 次复发）：数据变了而 README 的 AUTO 块还是旧值时，
以前写模式**先跑套件**算测试计数；套件里的 `test_docs_consistency.py` / `test_selfcheck.py`
会调用 `gen_docs.py --check --no-test-runs`，那一刻 README 的数据块还是旧的 → 记 FAIL，
AUTO 块写出「FAIL 1」瞬态；必须再写一次（或删 `out/auto-inputs.json` 缓存）才绿。
修复后写模式改为「先落盘非测试数据块，再跑套件补写测试计数」。

全部 **hermetic**：`SLCN_AUTO_INPUTS` 指临时文件，README 与 out 产物全部 tempfile fixture，
**不碰真实 `Engine/out/`、不写真实 `Engine/README.md`、不跑真实套件**（用假 `run_tests`）。
覆盖：
  ① 数据块陈旧 + 写模式 → 一次调用写绿（rc0、AUTO 测试计数 FAIL 0），且**跑套件那一刻**
     README 的非测试数据块已经是最新（这正是 RB-08 的根因，用假 run_tests 卡点）；
  ② 写后 AUTO 非测试行 = 现算值；`--check --no-test-runs` rc0；故意改一个数字 → rc1（口径不变）；
  ③ 幂等 + 测试计数缓存语义不变：第二次写复用缓存（不跑套件）且 README 字节不变；
  ④ 缓存里带 FAIL 计数的快照**不再复用**（否则瞬态 FAIL 会被缓存「粘住」，正是删 out/ 缓存的根因）。
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
for _p in (str(ENGINE), str(ENGINE / "tools"), str(ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TMP = Path(tempfile.mkdtemp(prefix="slcn_b110_"))
# ⚠️ 必须在 import gen_docs 之前设：AUTO_INPUTS 在模块导入时读取环境变量。
os.environ["SLCN_AUTO_INPUTS"] = str(TMP / "out" / "auto-inputs.json")
os.environ["SLCN_RUN_RECORD"] = "0"      # 纯测试：不写 run manifest 证据

import gen_docs as G  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def quiet():
    """吞掉被调 gen_docs 的 stdout/stderr：它内部会打印 `PASS`/`FAIL` 行，
    原样透出会被外层 gen_docs 的套件统计误当成断言行（同 test_selfcheck 的 `_flat` 教训）。

    ⚠️ 本测试会被 `gen_docs.py` 的写模式当成套件跑，counterexample 里故意触发的
    `FAIL  AUTO 块已过期…` 若不吞，就会把「FAIL 1」写进 AUTO 块（实测踩到）。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def nontest(body: str) -> str:
    """块里**非测试**行的归一化文本（git 自指行替占位符；测试两行整段删除）。"""
    return G.strip_test_lines(G.normalize_for_check(body, lenient=False))


# ── hermetic fixture：临时「Engine」的 out 产物 + README ──────────────────────
ROOT = TMP / "fake_engine"
README = ROOT / "README.md"
OUT = ROOT / "out"
CSV_HEADER = ([f"{f}_state" for f, _ in G.FIELDS] + [f"{f}_ok" for f, _ in G.FIELDS]
              + ["n_headings", "n_bib"])
write(OUT / "docx_audit.csv",
      ",".join(CSV_HEADER) + "\n"
      + ",".join(["ok"] * 9 + ["1"] * 9 + ["3", "4"]) + "\n"
      + ",".join(["absent"] * 9 + ["0"] * 9 + ["2", "1"]) + "\n")
write(OUT / "docx_audit.txt", "fixture audit txt\n")
write(OUT / "corpus" / "corpus.csv",
      "channel,ok,raw_ratio\nLaTeX,1,0.990000\nWord,1,0.900000\n")
write(OUT / "corpus" / "01_sample.tex", "正文【未识别：占位符】\n")
# 块内「测试」段是**旧值**、数据行是**旧值**（模拟「数据已变、README 还没写」的 RB-08 状态）
write(README,
      "# 临时 README（B-110 测试）\n\n"
      f"{G.BEGIN}\n"
      "**测试**（`python3.14` + `PYTHONPATH=Engine/vendor`）："
      "**1 套件 / 1 项断言 / FAIL 0 行** ✅（不含 `test_docs_consistency.py` 自身：它调用本脚本，属自指）\n\n"
      "套件明细：`test_old` 1\n\n"
      "**普查**：旧数据（陈旧 —— RB-08 要复现的状态）\n"
      f"{G.END}\n\n"
      "块外尾部内容：必须逐字节不动。\n")

# 把 gen_docs 的路径/数据源指向 fixture（模块全局，helper 函数按名字查全局）
G.README = README
G.AUDIT_CSV = OUT / "docx_audit.csv"
G.AUDIT_TXT = OUT / "docx_audit.txt"
G.CORPUS_CSV = OUT / "corpus" / "corpus.csv"
G.CORPUS_DIR = OUT / "corpus"
# B-136（批77）起「普查」的唯一来源是**笔记仓冻结基线**：fixture 用同一份假 csv/txt 冒充基线，
# 保持 hermetic（不读真实笔记仓；CI 上本来也没有笔记仓 → 否则这里会走信息性降级分支）。
G.notes_audit_paths = lambda: (OUT / "docx_audit.csv", OUT / "docx_audit.txt")

FAKE_ROWS = [("test_a", 3, 0), ("test_b", 4, 0)]
CALLS: list[bool] = []


def fake_run_tests():
    """假套件：跑「套件」的那一刻检查 README 的非测试数据块**已经**是最新的。"""
    body = G.split_block(G.read_readme())[1]
    expect = "\n" + G.build_block(run_test_suites=False)[0]
    CALLS.append(nontest(body) == nontest(expect))
    return FAKE_ROWS, sum(p for _, p, _ in FAKE_ROWS)


G.run_tests = fake_run_tests


def auto_body() -> str:
    return G.split_block(G.read_readme())[1]


def test_line(body: str) -> str:
    return next((ln for ln in body.split("\n") if ln.startswith("**测试**")), "")


# ── ① 数据块陈旧 + 写模式 → 一次调用写绿（RB-08 修复的核心）────────────────
with quiet():
    rc = G._main_impl([])
body = auto_body()
check("① 写模式 rc0（一次调用即绿）", rc == 0, f"rc={rc}")
check("① 跑套件那一刻 README 数据块已是最新（RB-08 根因已消除）",
      CALLS == [True], f"套件内自指检查看到的数据块新鲜={CALLS}")
check("① AUTO 测试计数 FAIL 0", "FAIL 0 行" in test_line(body), test_line(body)[:120])
check("① AUTO 测试计数 = 本次现跑（2 套件 / 7 项断言）",
      "2 套件 / 7 项断言" in test_line(body), test_line(body)[:120])
expect = "\n" + G.build_block(run_test_suites=False, test_rows_in=FAKE_ROWS)[0]
check("① 写出的块 = 现算块（测试行现跑、数据行现算）",
      nontest(body) == nontest(expect) and test_line(body) == test_line(expect))

# ── ② 写后校验口径不变：--check --no-test-runs 绿；改一个数字 → 红 ──────────
with quiet():
    rc_chk = G._main_impl(["--check", "--no-test-runs"])
check("② 写后 --check --no-test-runs rc0", rc_chk == 0, f"rc={rc_chk}")
_orig = README.read_bytes()
try:
    text = _orig.decode("utf-8")
    i = text.index(G.BEGIN) + len(G.BEGIN)
    j = text.index(G.END)
    _body = text[i:j]
    assert "| 题名 `title` |" in _body
    _mut = _body.replace("| 题名 `title` |", "| 题名 `title` 反例探针 |", 1)
    README.write_bytes((text[:i] + _mut + text[j:]).encode("utf-8"))
    with quiet():
        rc_bad = G._main_impl(["--check", "--no-test-runs"])
finally:
    README.write_bytes(_orig)      # 无论断言结果如何都还原
check("② 反例：改一个 AUTO 数字 → --check 仍 rc1（口径不变）", rc_bad == 1, f"rc={rc_bad}")
check("② 反例探针已逐字节还原 README", README.read_bytes() == _orig)

# ── ③ 幂等 + 测试计数缓存语义不变 ───────────────────────────────────────────
before = README.read_bytes()


def _no_suite_expected():
    raise AssertionError("第二次写不应跑套件：测试计数缓存应命中")


G.run_tests = _no_suite_expected
with quiet():
    rc2 = G._main_impl([])
check("③ 第二次写 rc0 且复用测试计数缓存（未跑套件）", rc2 == 0, f"rc={rc2}")
check("③ 第二次写 README 逐字节不变（幂等）", README.read_bytes() == before)
_proj = G.load_auto_inputs()
_inputs = G.collect_auto_inputs()
check("③ 全绿缓存可复用（engine/tests/稿件库/环境未变 → 返回缓存行）",
      G.cached_test_rows(_inputs, _proj) == _proj.get("test_rows"))
check("③ engine 变 → 缓存失效（语义不变）",
      G.cached_test_rows(dict(_inputs, engine_py_sha256="deadbeef"), _proj) is None)
check("③ 测试集变 → 缓存失效（语义不变）",
      G.cached_test_rows(dict(_inputs, tests_manifest_sha256="cafe"), _proj) is None)

# ── ④ 带 FAIL 计数的缓存快照不复用（否则 FAIL 会「粘住」）───────────────────
_bad_proj = dict(_proj, test_rows=[["test_a", 3, 1], ["test_b", 4, 0]])
check("④ 缓存快照里带 FAIL 计数 → 不复用（写模式必须重跑套件）",
      G.cached_test_rows(_inputs, _bad_proj) is None)

import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
