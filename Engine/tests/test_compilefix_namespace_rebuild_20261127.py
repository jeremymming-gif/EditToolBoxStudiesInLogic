"""TDD for the two deferred pre-existing namespace-flat-lookup defects.

Finding A: environment_migration_rule._rebuild looked up decisions by bare
``d.name == name``; with a same-name macro declared BEFORE the environment in
the local support file, the macro decision wins and the five-gate rule refuses
a legitimate migration.  RED test drives the real build_context ->
select_environment_plan -> _rebuild -> PRE.plan_migration path.

Finding B: the :313 gate used PRE.defined_names(), a flat union of macro and
environment names.  RED test demands the namespace-scoped helper on the real
PRE module (it does not exist yet); the flat defined_names() must remain
byte-identical in behaviour for the build.py rename predicate.

All tests are written BEFORE any production change.  No hand-built Decision
tuples: decisions come from real PRE.plan_migration calls with a real
PlanningObservation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from slcn import preamble as PRE  # noqa: E402
from slcn.compilefix import environment_migration_rule as ENV_RULE  # noqa: E402


DECL = "\\newenvironment{foo}{}{}"
MACRO = "\\newcommand{\\foo}{X}"


def _failure_diagnosis(candidate: bytes, env: str, begin: int, end: int):
    """A minimal bound diagnosis object (the shape _failure_round reads)."""
    diag = types.SimpleNamespace()
    diag.bound = True
    diag.candidate_sha256 = hashlib.sha256(candidate).hexdigest()
    diag.identity = "namespace-red-test"
    diag.phase = "xelatex-test"
    log = (
        f"! LaTeX Error: Environment {env} undefined.\n"
        f"l.{begin} \\begin{{{env}}}\n"
        f"! LaTeX Error: \\begin{{document}} ended by \\end{{{env}}}.\n"
        f"l.{end} \\end{{{env}}}\n"
    ).encode("utf-8")
    capture = {"failure": (b"", "xelatex", 1, log)}
    return diag, capture


def _candidate(extra_preamble: str = "") -> bytes:
    text = (
        "\\documentclass{article}\n"
        + (extra_preamble + "\n" if extra_preamble else "")
        + "\\usepackage{probe}\n"
        + "\\begin{document}\n"
        + "\\begin{foo}\n"
        + "X\n"
        + "\\end{foo}\n"
        + "\\end{document}\n"
    )
    return text.encode("utf-8")


class _NamespaceCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slcn-namespace-deferred-")
        self.root = Path(self.tmp)
        (self.root / "main.tex").write_text(
            "\\documentclass{article}\n\\usepackage{probe}\n"
            "\\begin{document}\nMain text.\n\\end{document}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _context(self, support_text: str):
        (self.root / "probe.sty").write_text(support_text, encoding="utf-8")
        return ENV_RULE.build_context(
            str(self.root), "main", str(self.root / "main.tex"),
            {"support_files": ["probe.sty"]},
        )

    def _plan(self, support_text: str, extra_preamble: str = ""):
        cx = self._context(support_text)
        candidate = _candidate(extra_preamble)
        diag, capture = _failure_diagnosis(candidate, "foo", 4, 6)
        return ENV_RULE.select_environment_plan(
            candidate, diag, context=cx, capture=capture
        ), cx


class TestFindingADecisionNamespace(_NamespaceCase):
    """The production five-gate path must match (name, namespace)==(foo, env)."""

    def test_macro_declared_before_env_in_support_still_migrates(self):
        # Real same-name collision: macro FIRST, so the buggy bare d.name
        # lookup picks the macro decision and gate ④ fails.
        support = MACRO + "\n" + DECL + "\n"
        plan, cx = self._plan(support)
        self.assertIsNotNone(
            plan,
            "same-name macro before env declaration must not suppress the "
            "environment migration (pre-dedup decisions include both)",
        )
        self.assertEqual(plan.env_name, "foo")
        self.assertEqual(plan.declaration, DECL)

        # Prove the observation really contains both namespaces and that the
        # env decision is the one the fix must select.
        candidate = _candidate()
        lines = candidate.decode("utf-8").splitlines()
        begin = next(i + 1 for i, line in enumerate(lines) if line.strip() == "\\begin{foo}")
        end = next(i + 1 for i, line in enumerate(lines) if line.strip() == "\\end{foo}")
        usage = "\n".join(lines[begin - 1:end])
        obs = PRE.PlanningObservation()
        PRE.plan_migration(
            cx.source_text, candidate.decode("utf-8"), cx.style_text,
            cx.template_sty_name, cx.retained_body_text + "\n" + usage + "\n",
            cx.source_dir, observation=obs,
        )
        namespaces = [(d.name, d.namespace, d.action) for d in obs.decisions if d.name == "foo"]
        self.assertIn(("foo", "macro", "skipped"), namespaces)
        self.assertIn(("foo", "env", "imported"), namespaces)

    def test_env_only_support_still_migrates(self):
        # Regression guard: the normal, non-colliding path stays green.
        plan, _cx = self._plan(DECL + "\n")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.env_name, "foo")
        self.assertEqual(plan.declaration, DECL)


class TestFindingBNamespaceScopedPredicate(unittest.TestCase):
    """The flat defined_names() must not be used as an env namespace gate."""

    def test_defined_environment_names_excludes_macros_and_counters(self):
        text = (
            "\\newcommand{\\foo}{X}\n"
            "\\newcommand{\\onlymacro}{Y}\n"
            "\\newenvironment{foo}{}{}\n"
            "\\newtheorem{bar}{}\n"
            "\\newcounter{onlycounter}\n"
            "\\newcolumntype{Z}{c}\n"
            "\\definecolor{onlycolor}{gray}{0.5}\n"
        )
        env_names = PRE.defined_environment_names(text)
        self.assertEqual(env_names, {"foo", "bar"})
        # The flat predicate keeps its historical, byte-identical semantics:
        # it still reports macro names (build.py's rename depends on it).
        flat = PRE.defined_names(text)
        self.assertIn("onlymacro", flat)
        self.assertIn("foo", flat)
        self.assertNotIn("onlymacro", env_names)
        self.assertNotIn("onlycounter", env_names)

    def test_defined_environment_names_ignores_commented_declarations(self):
        text = (
            "% \\newenvironment{commented}{}{}\n"
            "\\newcommand{\\foo}{X}\n"
            "\\newenvironment{foo}{}{}\n"
        )
        self.assertEqual(PRE.defined_environment_names(text), {"foo"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
