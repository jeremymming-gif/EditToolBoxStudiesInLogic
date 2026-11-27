"""Bounded package placement and style capture using public synthetic inputs."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn import preamble as PRE

ANCHOR = r"\usepackage{hyperref}"
DOCUMENT = r"\begin{document}"


def plan_lines(preamble):
    source = preamble + "\n" + DOCUMENT + "\nx\n" + r"\end{document}"
    return PRE.plan_migration(source, "", "").lines


class PackageOrderTests(unittest.TestCase):
    def test_option_group_and_following_dependencies_keep_input_order(self):
        group = r"\usepackage[capitalize]{cleveref,extra}"
        deps = [r"\usepackage{before}", group, r"\usepackage{tikz}",
                r"\usetikzlibrary{arrows}", r"\tikzset{x/.style={draw}}"]
        result = PRE.insert_lines(ANCHOR + "\n" + DOCUMENT, deps, ANCHOR)
        self.assertEqual(result.splitlines(), [deps[0], ANCHOR, *deps[1:], DOCUMENT])

    def test_hyperref_in_comment_other_name_or_macro_body_is_not_anchor(self):
        rows = ["% " + ANCHOR, r"\usepackage{hyperref-tools}", r"\newcommand{\demo}{",
                ANCHOR, "}", r"\RequirePackage[colorlinks]{hyperref}", DOCUMENT]
        deps = [r"\usepackage{sample}", r"\usepackage{cleveref}"]
        result = PRE.insert_lines("\n".join(rows), deps, ANCHOR)
        self.assertEqual(result.splitlines(), [*rows[:5], deps[0], rows[5], deps[1], DOCUMENT])

    def test_partial_name_does_not_imply_order_constraint(self):
        dep = r"\usepackage{no-cleveref-tools}"
        self.assertEqual(PRE.insert_lines(ANCHOR, [dep], ANCHOR), dep + "\n" + ANCHOR)

    def test_body_cannot_supply_a_preamble_anchor(self):
        dep = r"\usepackage{extra}"
        template = "\n".join([DOCUMENT, ANCHOR, r"\end{document}"])
        self.assertEqual(PRE.insert_lines(template, [dep], ANCHOR), dep + "\n" + template)

    def test_ordinary_options_and_fallback_preserve_bytes(self):
        dep = r"\usepackage[option]{a,b}"
        template = "% header\n" + DOCUMENT + "\n"
        self.assertEqual(PRE.insert_lines(template, [dep], ANCHOR),
                         "% header\n" + dep + "\n" + DOCUMENT + "\n")
        self.assertEqual(PRE.insert_lines("% header\n", [dep], ANCHOR), "% header\n" + dep + "\n")


class StyleBlockTests(unittest.TestCase):
    def test_multiline_bracket_value_is_one_complete_block(self):
        style = "\n".join([r"\tikzstyle{node}=[", r" label={a]b}, % ]",
                           r" label={\{#1\}},", " draw] % done"])
        self.assertEqual(plan_lines(style), [style])

    def test_split_head_and_comment_before_groups(self):
        style = "\n".join([r"\tikzset % parameter styles",
                           "{test/.style={draw=#1}}"])
        self.assertEqual(plan_lines(style), [style])

    def test_rejects_trailing_command_without_losing_next_valid_style(self):
        good = r"\tikzset{y/.style={draw}}"
        for value in (r"\tikzset{x/.style={draw}}\other{}",
                      r"\tikzstyle{x}=[draw]\other{}",
                      r"\tikzstyle{x}=value", r"\tikzset{x}}{"):
            with self.subTest(value=value):
                self.assertNotIn(value, plan_lines(value + "\n" + good))
        self.assertEqual(plan_lines(r"\tikzstyle{x}=[draw]\other{}" + "\n" + good), [good])

    def test_does_not_activate_style_from_macro_conditional_or_group(self):
        good = r"\tikzset{good/.style={draw}}"
        nested = r"\tikzset{nested/.style={draw}}"
        for before, after in ((r"\newcommand{\outer}{", "}"), (r"\iffalse", r"\fi"),
                              (r"\begingroup", r"\endgroup"),
                              (r"\begin{comment}", r"\end{comment}")):
            with self.subTest(before=before):
                self.assertEqual(plan_lines("\n".join([before, nested, after, good])), [good])

    def test_unclosed_bracket_never_migrates_partial_block_or_inner_package(self):
        self.assertEqual(plan_lines("\n".join([r"\tikzstyle{node}=[", r"\usepackage{nested}"])), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
