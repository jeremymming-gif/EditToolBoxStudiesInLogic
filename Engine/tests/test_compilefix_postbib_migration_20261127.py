#!/usr/bin/env python3.14
"""Regression: declarations used only after the bibliography still migrate.

Run from Engine with the repository's pinned Python/vendor environment.
"""
from pathlib import Path
import sys
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from slcn import build  # noqa: E402


class PostBibliographyMigrationTests(unittest.TestCase):
    def test_local_environment_used_only_in_appendix_is_migrated(self):
        with tempfile.TemporaryDirectory(prefix="postbib-migration-") as td:
            root = Path(td)
            src = root / "source.tex"
            out = root / "out"
            src.write_text(
                "\\documentclass{article}\n"
                "\\usepackage{localdefs}\n"
                "\\begin{document}\n"
                "Main text.\n"
                "\\printbibliography\n"
                "\\appendix\n"
                "\\begin{probeenv}Appendix-only content.\\end{probeenv}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "localdefs.sty").write_text(
                "\\newenvironment{probeenv}{\\par\\smallskip}{\\par\\smallskip}\n",
                encoding="utf-8",
            )

            result = build.build_latex(str(src), str(out), basename="postbib", verbose=False)
            template = Path(result.tex_path).read_text(encoding="utf-8")

            self.assertIn("\\newenvironment{probeenv}", template)
            self.assertIn("\\begin{probeenv}Appendix-only content.\\end{probeenv}", template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
