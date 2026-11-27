"""Bound migration evidence from synthetic LaTeX and independently inspected candidates."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn import build, preamble as pre


def fingerprint(name):
    return hashlib.sha256(name.encode()).hexdigest()


def article(*lines):
    return "\n".join([r"\documentclass{article}", *lines, r"\begin{document}",
                      "x", r"\end{document}"])


def lookup(rows, kind, name):
    return [r for r in rows if r["kind"] == kind and r["name_sha256"] == fingerprint(name)]


class AuditTests(unittest.TestCase):
    def plan(self, src, skeleton="", style="", source_dir=""):
        obs = pre.PlanningObservation()
        plan = pre.plan_migration(src, skeleton, style, "teststyle", source_dir=source_dir,
                                  observation=obs)
        self.assertIn("migration", obs.to_display())
        return plan, obs

    def test_actual_decisions_have_coordinates_and_only_digests(self):
        src = article(r"\usepackage{teststyle,hyperref,custompkg}",
                      r"\RequirePackage{custompkg,bbding,natbib,localpkg}",
                      r"\usetikzlibrary{calc,views}", r"\MysteryShape{private note}",
                      r"\tikzset{one/.style={text={nested/.style=ignored}},",
                      r"  two words/.style={draw=#1}}")
        skeleton = article(r"\usepackage{teststyle,marvosym,biblatex}", r"\usetikzlibrary{calc}")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "localpkg.sty").write_text(
                "% synthetic support fixture\n\\newenvironment{localsupportenv}{}{}\n",
                encoding="utf-8",
            )
            plan, obs = self.plan(src, skeleton, source_dir=d)
        rows = obs.to_display()["migration"]
        for key, why in [("teststyle", "template_exists"), ("hyperref", "template_managed"),
                         ("bbding", "conflict"), ("natbib", "replaced"), ("localpkg", "local_support")]:
            self.assertEqual([r["reason"] for r in lookup(rows, "package", key)], [why])
        self.assertEqual([(r["position"], r["reason"]) for r in lookup(rows,"package","custompkg")],
                         [(2, "imported"), (3, "already_imported")])
        support_env = lookup(rows, "environment", "localsupportenv")[0]
        self.assertEqual(support_env["source_role"], "local_support")
        self.assertEqual(support_env["position"], 2)
        self.assertEqual(support_env["coordinate"], "support_file_line")
        self.assertEqual(support_env["source_sha256"], fingerprint("localpkg.sty"))
        self.assertEqual([r["reason"] for r in lookup(rows, "tikz_library", "calc")], ["template_exists"])
        self.assertEqual(len(lookup(rows, "unrecognized", "MysteryShape")), 1)
        self.assertEqual(len(lookup(rows, "tikz_key", "two words")), 1)
        self.assertFalse(lookup(rows, "tikz_key", "nested"))
        blob = json.dumps(rows)
        for raw in ("custompkg", "two words", "private note", "MysteryShape", ".sty"):
            self.assertNotIn(raw, blob)
        self.assertEqual(obs.to_display()["counts"]["declarations_seen"], 1)
        self.assertIn(r"\usepackage{custompkg}", plan.lines)

    def test_candidate_comparison_uses_active_positions_and_style(self):
        src = article(r"\usepackage{aaa,bbb,ccc}", r"\newtheorem{marker}{M}",
                      r"\usepackage{ddd}", r"\UnknownName{",
                      r"\AnotherUnknown{ignored}}")
        plan, obs = self.plan(src, article(r"\usepackage{teststyle}"), r"\RequirePackage{ddd}")
        candidate = article(r"\usepackage{teststyle}", r"\usepackage{bbb}", r"% \usepackage{ccc}",
                            r"\usepackage{aaa}", r"\newcommand{\wrap}{",
                            r"\usepackage{ccc}}", r"% \newtheorem{marker}{M}")
        obs.migration.compare(candidate, r"\RequirePackage{ddd}", "teststyle")
        disp = obs.to_display()
        rows = disp["migration"]
        self.assertTrue(lookup(rows, "package", "aaa")[0]["reordered"])
        self.assertTrue(lookup(rows, "package", "bbb")[0]["reordered"])
        self.assertEqual(lookup(rows, "package", "ccc")[0]["candidate_state"], "absent")
        self.assertEqual(lookup(rows, "environment", "marker")[0]["candidate_state"], "absent")
        self.assertEqual(lookup(rows, "package", "ddd")[0]["candidate_state"], "style_provided")
        self.assertFalse(lookup(rows, "unrecognized", "AnotherUnknown"))
        self.assertEqual(disp["migration_counts"]["missing_imports"], 2)
        self.assertEqual(disp["migration_counts"]["unrecognized"], 1)
        self.assertFalse(disp["migration_incomplete"])

    def test_local_support_reordering_is_compared_within_file_coordinates(self):
        audit = pre.MigrationAudit()
        source_id = fingerprint("support.sty")
        audit.record("environment", "first", 2, "imported", "imported",
                     role="local_support", coordinate="support_file_line",
                     source_sha256=source_id)
        audit.record("environment", "second", 3, "imported", "imported",
                     role="local_support", coordinate="support_file_line",
                     source_sha256=source_id)
        candidate = article(r"\newenvironment{second}{}{}", r"\newenvironment{first}{}{}")
        audit.compare(candidate, "", "teststyle")
        self.assertEqual([row["candidate_state"] for row in audit.rows], ["present", "present"])
        self.assertEqual([row["reordered"] for row in audit.rows], [True, True])

    def test_different_support_files_do_not_share_line_coordinates(self):
        audit = pre.MigrationAudit()
        for name, position, source in (("first", 2, "one.sty"), ("second", 3, "two.sty")):
            audit.record("environment", name, position, "imported", "imported",
                         role="local_support", coordinate="support_file_line",
                         source_sha256=fingerprint(source))
        candidate = article(r"\newenvironment{second}{}{}", r"\newenvironment{first}{}{}")
        audit.compare(candidate, "", "teststyle")
        self.assertFalse(any(row["reordered"] for row in audit.rows))

    def test_build_exposes_bound_numeric_summary_and_regular_macros(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "source.tex"
            src.write_text(article(r"\newcommand{\livecmd}{X}", r"\newcommand{\deadcmd}{Y}",
                                   r"\UnknownShape{abc}").replace("\nx\n", "\n\\livecmd\n"),
                           encoding="utf-8")
            result = build.build_latex(str(src), str(root / "out"), basename="demo", verbose=False)
            ev = result.planning_evidence
            self.assertIn("migration_counts", ev)
            rows = ev["migration"]
            self.assertEqual(ev["candidate_sha256"], hashlib.sha256(Path(result.tex_path).read_bytes()).hexdigest())
            self.assertEqual(lookup(rows,"command","livecmd")[0]["candidate_state"], "present")
            self.assertEqual(lookup(rows,"command","deadcmd")[0]["reason"], "body_unused")
            messages = [line for line in result.log if "前言迁移审计" in line]
            self.assertEqual(len(messages), 1)
            self.assertNotIn("UnknownShape", messages[0])
            with patch.object(type(pre.PlanningObservation().migration), "compare", side_effect=ValueError):
                other = build.build_latex(str(src), str(root / "other"), basename="demo", verbose=False)
            self.assertEqual(Path(other.tex_path).read_bytes(), Path(result.tex_path).read_bytes())
            self.assertTrue(other.planning_evidence["migration_incomplete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
