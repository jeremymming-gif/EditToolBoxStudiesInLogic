"""T3 · CLI E2E (真实 XeLaTeX，无 xelatex/biber mock) · 2026-11-27.

证据分两层，均只用合成夹具（91 期真实稿件全部成功、无真实失败稿，禁止触碰稿件仓）：

1. 真 CLI 子进程层：python3.14 -B convert.py --source ... --compile
   - default-on 与 --no-compile-multiround / --coop-taskbook-only 的 rc 契约；
   - 子进程 stderr 的「L1 多轮暂存未修复」是 T2 接线（convert.py:676-680）被真实触发的证据。
2. 真 CLI 函数链层（同进程，convert_compile.compile_pdf + collecting_results，
   与 convert.py:674 完全同一入口）：env/macro/tikzkey/multiround chain 四条
   证明 raw rc!=0 → convert rc=2 → staged repair → convert rc=0，apply_plan 恰好 1 次，
   plan.reverse(plan.patched)==original_candidate，声明各出现一次。

已实测的诚实缺口（报告同步记录）：在当前完整期刊骨架候选 + 合成 source 下，
真 CLI 子进程路径 default 仍 rc=2（门槛②：产物目录无声明 .sty；source body 用声明时
门槛⑤ 失败）。因此子进程层只验证「接线触发 + 失败契约」，rc=2→0 用第 2 层同进程
真函数链作为主证据；五门槛不放宽，不 mock 编译器，不改 T2 代码。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from slcn import convert_compile  # noqa: E402
from slcn.compilefix import capture_compile  # noqa: E402
from slcn.compilefix.compiler import _environment  # noqa: E402
from slcn.compilefix.l1 import run_staged_repair_loop  # noqa: E402
from slcn.compilefix.l1_diagnostics import diagnose  # noqa: E402
from slcn.compilefix.l1_detailed import collecting_results  # noqa: E402

PY = "/opt/homebrew/bin/python3.14"
TEXBIN = "/Library/TeX/texbin"
ENV_DECL = r"\newenvironment{firstenv}{}{}"
MACRO_DECL = r"\newcommand{\othermacro}{Other text.}"
EXTRA_DECL = r"\newcommand{\thirdmacro}{Third text.}"
SHAREABLE_KEYS = {"identity", "candidate_sha256", "phase", "returncode",
                  "diagnostics", "attempts"}
CAPTURE_KEYS = ("candidate_sha256", "candidate_id", "source_id", "out_id")

TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{hyperref}
\begin{document}
SKELMARK
\end{document}
"""
BODY_2 = r"""\begin{firstenv}
inside first environment.
\end{firstenv}
\ifdefined\firstenv
\othermacro marker.
\fi
"""
TIKZ_BLOCK = r"""\tikzset{modal/.style={draw, rectangle, minimum width=#1},
  world/.style={modal={2cm}, fill=blue!20}}
"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _cli_env(root: Path) -> dict[str, str]:
    """§T3 指定的真 CLI 子进程环境（vendor 依赖 + 真 xelatex PATH）。"""
    env = dict(os.environ)
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        SLCN_MANUSCRIPTS="/dev/null",
        PYTHONPATH=str(REPO / "vendor"),
    )
    env["PATH"] = TEXBIN + os.pathsep + env.get("PATH", "")
    return env


class _CLISubprocessCase(unittest.TestCase):
    """真 CLI 子进程：失败契约 / 默认启用触发 / coop 闸门。"""

    maxDiff = None

    def setUp(self):
        if shutil.which("xelatex") is None or shutil.which("biber") is None:
            self.skipTest("BLOCKED: XeLaTeX/Biber unavailable")
        self.tmp = tempfile.mkdtemp(prefix="slcn-t3-cli-e2e-", dir="/tmp")
        self.root = Path(self.tmp)
        src_dir = self.root / "src"
        src_dir.mkdir(parents=True)
        self.source = src_dir / "main.tex"
        self.source.write_text(r"""\documentclass{article}
\begin{document}
\begin{firstenv}
inside first environment.
\end{firstenv}
\end{document}
""", encoding="utf-8")
        (src_dir / "probe.sty").write_text(
            ENV_DECL + "\n" + MACRO_DECL + "\n" + EXTRA_DECL + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, out: Path, *args: str):
        cmd = [PY, "-B", "convert.py",
               "--source", str(self.source), "--out", str(out),
               "--name", "demo", "--lang", "en", "--force", "--no-original",
               "--compile", "--no-bibdb", "--verify-max", "0", *args]
        return subprocess.run(
            cmd, cwd=str(REPO), env=_cli_env(self.root),
            capture_output=True, text=True, timeout=300,
        )

    def test_optout_failure_contract_real_cli_rc2(self):
        out = self.root / "out-optout"
        proc = self._run_cli(out, "--no-compile-multiround")
        self.assertEqual(proc.returncode, 2, proc.stderr[-3000:])
        self.assertEqual(
            proc.stdout.strip().splitlines()[-1],
            f"完成。产物目录：{out}/demo",
        )
        self.assertIn("❌ 转换已完成，但 ⑨ 编译未通过（退出码 2）", proc.stderr)
        self.assertIn("xelatex 第 1 步失败（退出码 1）", proc.stderr)
        self.assertIn("Environment firstenv undefined.", proc.stderr)
        self.assertNotIn("L1 多轮暂存", proc.stderr,
                         "--no-compile-multiround 必须不走多轮")
        candidate = (out / "demo" / "demo.tex").read_bytes()
        self.assertEqual(candidate.count(ENV_DECL.encode()), 0)

    def test_default_cli_wires_staged_loop_and_coop_never_does(self):
        default_out = self.root / "out-default"
        proc = self._run_cli(default_out)
        self.assertEqual(proc.returncode, 2, proc.stderr[-3000:])
        self.assertEqual(
            proc.stdout.strip().splitlines()[-1],
            f"完成。产物目录：{default_out}/demo",
        )
        # T2 接线的子进程级证据：默认启用时 convert.py:676-680 确实进入多轮。
        self.assertIn("L1 多轮暂存未修复", proc.stderr)
        self.assertIn("退出码 2", proc.stderr)

        coop_out = self.root / "out-coop"
        coop = self._run_cli(coop_out, "--coop-taskbook-only")
        self.assertEqual(coop.returncode, 2, coop.stderr[-3000:])
        self.assertEqual(
            coop.stdout.strip().splitlines()[-1],
            f"暂存目录：{coop_out}/demo_待核",
        )
        self.assertIn("协同初排已完成转换，但 ⑨ 编译未通过（退出码 2）", coop.stderr)
        self.assertNotIn("L1 多轮暂存", coop.stderr,
                         "coop-taskbook-only 闸门必须拦住多轮")
        self.assertNotIn("退出码 2）：L1 未修复：多轮暂存未收敛", coop.stderr)


class _RealChainCase(unittest.TestCase):
    """convert.py:674 同一真函数链 + 真 xelatex；apply_plan spy 只计数，不替编译。"""

    maxDiff = None
    body = BODY_2
    candidate_override = None
    support_sty = ENV_DECL + "\n" + MACRO_DECL + "\n" + EXTRA_DECL + "\n"

    def setUp(self):
        if shutil.which("xelatex") is None or shutil.which("biber") is None:
            self.skipTest("BLOCKED: XeLaTeX/Biber unavailable")
        self.tmp = tempfile.mkdtemp(prefix="slcn-t3-chain-e2e-", dir="/tmp")
        self.root = Path(self.tmp)
        # 注意：不可在 setUp 里全局 patch.dict(os.environ, TMPDIR=...)——tempfile.tempdir
        # 会缓存第一次的 TMPDIR，后续测试的 staged loop 会把工作目录建在上一个已删除的
        # 临时根下，导致 run_staged_repair_loop 在 l1.py:460 直接 except 判定不收敛。
        # 真 xelatex 子进程由 compiler._environment(home) 构造，自带隔离 HOME/TMPDIR。
        for name in ("in", "out/demo", "private"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.source = self.root / "in" / "main.tex"
        self.sty = self.root / "in" / "probe.sty"
        self.out = self.root / "out" / "demo"
        self.stem = "demo"
        # 定义只在 source 侧支撑文件；source 正文干净，候选正文用到 → 门槛⑤。
        self.source.write_text(r"""\documentclass{article}
\usepackage{probe}
\begin{document}
Main text.
\end{document}
""", encoding="utf-8")
        self.sty.write_text(self.support_sty, encoding="utf-8")
        if self.candidate_override is not None:
            self._candidate_path.write_bytes(self.candidate_override)
        else:
            self._candidate_path.write_text(
                TEMPLATE.replace("SKELMARK", self.body), encoding="utf-8")
        self.original_candidate = self._candidate_path.read_bytes()
        self.capture = capture_compile(self.out, self.stem, source=self.source)
        assert self.capture is not None
        code, raw = self._dry_compile(self.original_candidate, "orig")
        self.assertNotEqual(code, 0, "pre-repair candidate must really fail")
        self.capture["failure"] = ("c" * 32, 1, code, raw)
        self.report = diagnose(self.original_candidate, self.capture,
                               available_paths={"demo.tex"})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def _candidate_path(self):
        return self.out / (self.stem + ".tex")

    def _dry_compile(self, candidate: bytes, stage: str):
        work = self.root / stage
        work.mkdir(exist_ok=True)
        (work / (self.stem + ".tex")).write_bytes(candidate)
        with tempfile.TemporaryDirectory(prefix="compiler-home-",
                                         dir=self.root / "private") as home:
            proc = subprocess.run(
                ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
                 self.stem + ".tex"],
                cwd=str(work), env=_environment(Path(home)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
            )
        raw = proc.stdout[:2 * 1024 * 1024]
        return proc.returncode, raw

    def _real_convert_compile(self):
        """真 convert.py 编译入口：convert_rc 契约 失败=2 / 成功=0。"""
        with collecting_results() as results:
            ok, info = convert_compile.compile_pdf(str(self.out), self.stem)
        self.assertEqual(len(results), 1)
        result = results[0]
        return (0 if ok else 2), result, info

    def _assert_rc2_to_rc0(self, decl: str, kind: str):
        before_rc, before_result, before_info = self._real_convert_compile()
        self.assertEqual(before_rc, 2, before_info)
        self.assertFalse(before_result.ok)
        self.assertTrue(str(before_result.phase).startswith("xelatex-"))
        self.assertNotEqual(before_result.returncode, 0, "raw rc must be real nonzero")
        before_raw_rc = before_result.returncode
        self.assertEqual(before_result.candidate_sha256,
                         _sha(self.original_candidate))
        self.assertEqual(self.report.subject[0], kind)

        calls = []
        import slcn.compilefix.l1 as l1mod
        real_apply = l1mod.apply_plan

        def spy(*args, **kwargs):
            calls.append(kwargs["plan"])
            return real_apply(*args, **kwargs)

        with patch.object(l1mod, "apply_plan", side_effect=spy):
            ok, info, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report,
                max_rounds=3,
            )
        self.assertTrue(ok, info)
        self.assertEqual(len(calls), 1, "exactly one apply_plan publish")
        plan = calls[0]
        published = self._candidate_path.read_bytes()
        self.assertEqual(published, plan.patched)
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)
        self.assertEqual(published.count(decl.encode()), 1)
        self.assertIn("l1_repaired", new_capture)
        for key in CAPTURE_KEYS:
            self.assertIn(key, new_capture)
        l1 = new_capture["l1"]
        self.assertEqual(set(l1), SHAREABLE_KEYS)
        dumped = json.dumps(l1, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("subject", dumped)
        self.assertNotIn("round", dumped)

        after_rc, after_result, after_info = self._real_convert_compile()
        self.assertEqual(after_rc, 0, after_info)
        self.assertTrue(after_result.ok, after_info)
        self.assertEqual(after_result.phase, "complete")
        self.assertEqual(after_result.returncode, 0)
        self.assertEqual(after_result.candidate_sha256, _sha(published))
        self.assertTrue((self.out / (self.stem + ".pdf")).exists())
        return before_raw_rc, after_result, plan


class TestRealChainEnvironment(_RealChainCase):
    support_sty = ENV_DECL + "\n"
    body = r"""\begin{firstenv}
inside first environment.
\end{firstenv}
"""

    def test_cli_chain_env_real_rc2_to_rc0(self):
        raw_rc, after, plan = self._assert_rc2_to_rc0(ENV_DECL, "environment")
        self.assertNotEqual(raw_rc, 0)
        self.assertEqual(after.returncode, 0)


class TestRealChainMacro(_RealChainCase):
    support_sty = MACRO_DECL + "\n"
    # 无条件使用：\ifdefined 会藏住命令主题的锚点 l.N 帧。
    body = r"""\othermacro marker.
"""

    def test_cli_chain_macro_real_rc2_to_rc0(self):
        raw_rc, after, plan = self._assert_rc2_to_rc0(MACRO_DECL, "command")
        self.assertNotEqual(raw_rc, 0)
        self.assertEqual(after.returncode, 0)


class TestRealChainTikzKey(_RealChainCase):
    support_sty = TIKZ_BLOCK
    body = r"""\begin{tikzpicture}
\node[modal]{modal node};
\node[world]{world node};
\end{tikzpicture}
"""

    def setUp(self):
        # tikz 必须先加载：insert_lines 把 \tikzset 放在第一个 \usepackage 之后。
        self.candidate_override = TEMPLATE.replace(
            r"""\usepackage{amsmath}
""", r"""\usepackage{tikz}
\usepackage{amsmath}
""",
        ).replace("SKELMARK", self.body).encode("utf-8")
        super().setUp()

    def test_cli_chain_tikzkey_real_rc2_to_rc0(self):
        raw_rc, after, plan = self._assert_rc2_to_rc0("modal/.style", "tikz_key")
        self.assertNotEqual(raw_rc, 0)
        self.assertEqual(after.returncode, 0)


class TestRealChainMultiroundChain(_RealChainCase):
    support_sty = ENV_DECL + "\n" + MACRO_DECL + "\n"
    body = BODY_2  # env → gated macro → ≤3 轮收敛

    def test_cli_chain_multiround_single_publish_byte_exact(self):
        raw_rc, after, plan = self._assert_rc2_to_rc0(ENV_DECL, "environment")
        published = self._candidate_path.read_bytes()
        self.assertEqual(published.count(ENV_DECL.encode()), 1)
        self.assertEqual(published.count(MACRO_DECL.encode()), 1)
        self.assertIn(MACRO_DECL.encode(), plan.added_bytes)
        self.assertEqual(plan.start + len(plan.added_bytes), plan.end)
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
