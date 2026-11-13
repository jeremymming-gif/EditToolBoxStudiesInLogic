#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""MC-02：英文初排不得把只在正文句首/只在标题出现的专名静默小写。

oracle 全部是卡片给出的期望字面量（非实现自证）。本文件既可由 pytest 收集，
也可按仓库惯例 `python tests/test_mc02...py` 直接运行。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import proofcheck as P  # noqa: E402

_REPRO = ("\\subsection{Comparison With Zermelo}\n\n"
          "Zermelo argues that the axiom is independent.\n")


def _first(tex: str) -> str:
    return tex.splitlines()[0]


def test_zermelo_sentence_initial_proper_name_is_kept():
    new, info = P.apply_en_subsection_case(_REPRO, "auto")
    assert _first(new) == "\\subsection{Comparison with Zermelo}", new
    assert "zermelo" not in _first(new), new
    assert {m["old"] for m in info["manual"]} >= {"Zermelo"}, info
    assert len(info["fixed"]) == 1, info


def test_title_only_unknown_proper_name_is_kept():
    tex = "\\subsection{The Bloomfield Problem}\n\nBody text without the name.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{The Bloomfield problem}", new
    assert "bloomfield" not in _first(new), new
    assert {m["old"] for m in info["manual"]} >= {"Bloomfield"}, info


def test_known_ordinary_words_are_lowercased_without_manual_noise():
    tex = "\\subsection{The Nature of the Good}\n\nHuman nature is good.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{The nature of the good}", new
    assert info["manual"] == [], info

    tex = ("\\subsection{The Nature of the Good and the Induction}\n\n"
           "Human nature is good and induction works.\n")
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{The nature of the good and the induction}", new
    assert info["manual"] == [], info


def test_known_proper_names_are_kept_and_traceable():
    tex = "\\subsection{Notes on Plato and Kant}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{Notes on Plato and Kant}", new
    assert {m["old"] for m in info["manual"]} >= {"Plato", "Kant"}, info

    tex = "\\subsection{English and Chinese Thought}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{English and Chinese thought}", new
    assert "English" in _first(new) and "Chinese" in _first(new), new
    assert {m["old"] for m in info["manual"]} >= {"English", "Chinese"}, info


def test_possessives_keep_stems():
    tex = ("\\subsection{A Study of Zermelo's Theorem}\n\n"
           "This study proves the theorem.\n")
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{A study of Zermelo's theorem}", new
    assert {m["old"] for m in info["manual"]} >= {"Zermelo's"}, info
    assert "zermelo's" not in _first(new), new

    tex = "\\subsection{Kant\u2019s Critical Method}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{Kant\u2019s critical method}", new
    assert {m["old"] for m in info["manual"]} >= {"Kant\u2019s"}, info

    tex = "\\subsection{The Theory of Jones' Action}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{The theory of Jones' action}", new
    assert {m["old"] for m in info["manual"]} >= {"Jones'"}, info


def test_first_word_unknown_possessive_is_traceable():
    tex = "\\subsection{Zermelo's Theorem}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{Zermelo's theorem}", new
    assert {m["old"] for m in info["manual"]} >= {"Zermelo's"}, info


def test_hyphen_compounds_follow_q8_contract():
    tex = ("\\subsection{On Dual-Level and Second-Personal Coordination}\n\nBody.\n")
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == ("\\subsection{On dual-level and second-personal "
                           "coordination}"), new
    assert not [m for m in info["manual"] if "-" in m["old"]], info

    tex = "\\subsection{Neo-Kantian Themes in Philosophy}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert "Neo-Kantian" in _first(new), new
    assert "neo-kantian" not in _first(new), new
    assert "philosophy" in _first(new), new
    assert {m["old"] for m in info["manual"]} >= {"Neo-Kantian"}, info

    tex = "\\subsection{On Zermelo-Based Arguments}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert "Zermelo-Based" in _first(new), new
    assert "zermelo-based" not in _first(new), new
    assert {m["old"] for m in info["manual"]} >= {"Zermelo-Based"}, info


def test_abbreviations_and_mixed_case_are_kept():
    tex = "\\subsection{The Role of AI and the U.S. Case}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{The role of AI and the U.S. case}", new
    assert {m["old"] for m in info["manual"]} >= {"AI", "U.S."}, info

    tex = "\\subsection{McDowell and the Space of Reasons}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{McDowell and the space of reasons}", new
    assert {m["old"] for m in info["manual"]} >= {"McDowell"}, info


def test_section_is_never_auto_changed_and_is_still_reported():
    sec = "\\section{The structure of human cooperation}"
    sub = "\\subsection{On Shared Action}"
    tex = f"{sec}\n\n{sub}\n\nBody.\n"
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert sec in new, new
    assert "\\subsection{On shared action}" in new, new
    assert info["fixed"], info
    assert all("\\section" not in it["old"] for it in info["fixed"]), info

    hits = [h for h in P.check(tex, lang="en").hits if h.rule_id == "EN-06"]
    assert any(h.text.startswith("\\section") for h in hits), hits


def test_modes_auto_report_off_and_idempotence():
    new, info = P.apply_en_subsection_case(_REPRO, "auto")
    assert _first(new) == "\\subsection{Comparison with Zermelo}", new

    new2, info2 = P.apply_en_subsection_case(new, "auto")
    assert new2 == new, new2
    assert info2["fixed"] == [], info2

    report, rinfo = P.apply_en_subsection_case(_REPRO, "report")
    assert report == _REPRO, report
    assert rinfo["fixed"] == [], rinfo
    assert any(c["new"] == "Comparison with Zermelo" for c in rinfo["candidates"]), rinfo
    assert {m["old"] for m in rinfo["manual"]} >= {"Zermelo"}, rinfo

    off, oinfo = P.apply_en_subsection_case(_REPRO, "off")
    assert off == _REPRO, off
    assert oinfo == {"fixed": [], "candidates": [], "manual": []}, oinfo


def test_commands_math_and_quotes_are_untouched():
    tex = (r"\subsection{Testing \emph{Emphasis} and $x^2$ Math with "
           r"\cite{Key} and ``We''}" + "\n\nBody.\n")
    new, info = P.apply_en_subsection_case(tex, "auto")
    line = _first(new)
    assert r"\emph{Emphasis}" in line, line
    assert "$x^2$" in line, line
    assert r"\cite{Key}" in line, line
    assert "``We''" in line, line
    assert " math " in line, line
    assert info["manual"] == [], info


def test_body_lowercase_evidence_handles_plurals():
    tex = ("\\subsection{On Arguments and Principles}\n\n"
           "The arguments rest on principles.\n")
    new, info = P.apply_en_subsection_case(tex, "auto")
    assert _first(new) == "\\subsection{On arguments and principles}", new
    assert info["manual"] == [], info


if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}  {exc}")
        else:
            print(f"PASS  {name}")
    print("=" * 66)
    if failures:
        print(f"失败 {failures} 项")
        sys.exit(1)
    print(f"全部 {len(tests)} 项断言通过")
    sys.exit(0)
