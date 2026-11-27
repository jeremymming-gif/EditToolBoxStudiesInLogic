"""Rule-family skeleton for source-located, reversible self-heal migrations.

Each strategy in this family follows the same spine:

    bound failure subject → unique source definition (namespace counted, pre-dedup)
    → two fresh selector runs (retained body rejects, covered body selects exactly
      the target) → same-round context verification → one continuous byte insert
    → byte-exact reverse.

The environment strategy already lives in ``environment_migration_rule.py``; this
module holds the macro strategy (macro-migration-v1) and the shared rebuild-proof
helpers that the later TikZ-strategy task can reuse.  It deliberately reuses
``EnvironmentEditPlan`` (one continuous insert slice) and never the two-interval
CATCODE ``EditPlan``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .. import preamble as PRE
from .. import preamble_blocks as PB
from .. import sources
from .environment_migration_rule import (
    EnvironmentContext,
    EnvironmentEditPlan,
    build_context,
)
from .files import digest
from .l1_diagnostics import mask_comments, normalize_subject
from .l1_rules import (
    MACRO_MIGRATION,
    MACRO_REGISTRY,
    TIKZKEY_MIGRATION,
    TIKZKEY_REGISTRY,
)

# v1 accepts only plain, non-conditional whole definitions that do not silently
# override a template command (spec §11 #4).
_MACRO_KINDS = frozenset({"newcommand", "providecommand"})
_REJECT_MACRO_KINDS = frozenset({"renewcommand"})
_COMMAND_NAME = re.compile(r"\\[A-Za-z@]{1,128}\Z")
_IF_PREFIX = re.compile(r"\\(?:if[a-zA-Z]*|unless)\b")
_FI = re.compile(r"\\fi\b")


@dataclass(frozen=True)
class MacroEditPlan(EnvironmentEditPlan):
    """EnvironmentEditPlan plus the selected declarations for introspection.

    It keeps the single continuous insert slice and the byte-exact ``reverse``
    of the base plan; ``lines`` is the exact list fed to ``insert_lines``.
    """

    lines: tuple[str, ...] = ()


def build_macro_context(out_dir, stem, source, capture):
    """Reuse the environment rule's private same-round context unchanged."""
    return build_context(out_dir, stem, source, capture)


def _text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unique_source_macro(context, name):
    r"""Locate the single source-side definition of ``\name``.

    Counting is done on ``top_level_macros`` (the raw, per-definition list), NOT
    on ``collect_top_level_custom_macros`` (which de-duplicates by name and hides
    a second competing definition).  The name is a (kind, name) namespace key:
    renewcommand/def/let/newif definitions never satisfy the key, and an
    unclosed conditional before the declaration makes its scope ambiguous.
    """
    found = []
    for support_name, text in context.support_texts:
        masked = mask_comments(text)
        for macro in PRE.top_level_macros(masked):
            if macro.name != name:
                continue
            if macro.kind in _REJECT_MACRO_KINDS or macro.kind not in _MACRO_KINDS:
                return None
            declaration = masked[macro.span[0]:macro.span[1]].strip()
            if "\\if" in declaration or "\\fi" in declaration:
                return None
            prefix = masked[:macro.span[0]]
            if len(_IF_PREFIX.findall(prefix)) > len(_FI.findall(prefix)):
                return None
            found.append((support_name, declaration))
    if len(found) != 1:
        return None
    return found[0]


def _rebuild_proof(context, candidate_text, name, declaration):
    r"""Two fresh ``select_custom_macros`` runs prove missing-vs-covered.

    Retained body: the macro is not referenced, so the selector must not select
    it.  Covered body: the macro is referenced, so the selector must select
    exactly the target and must not rename it (template does not provide it).
    A string-presence check is deliberately not used: selection is a genuine
    call into the shared selector on both bodies.
    """
    # Source definitions are the thing being migrated, not pre-existing names;
    # only the candidate and the bound template count as providers (spec §11 #4).
    existing = PRE.defined_names(context.style_text) | PRE.defined_names(candidate_text)
    macros = [(declaration_kind(declaration), name, declaration)]
    retained, retained_renames = PRE.select_custom_macros(
        list(macros), context.retained_body_text, set(existing)
    )
    if retained or retained_renames:
        return None
    candidate_body = sources.extract_body_text(candidate_text) or ""
    if ("\\" + name) not in candidate_body:
        return None
    covered_body = context.retained_body_text + "\n" + candidate_body + "\n"
    covered, covered_renames = PRE.select_custom_macros(
        list(macros), covered_body, set(existing)
    )
    if covered != macros or covered_renames:
        return None
    return [declaration]


def declaration_kind(declaration):
    """Kind of an admitted declaration; only called after _unique_source_macro."""
    match = re.match(r"\\(newcommand|providecommand)\b", declaration.lstrip())
    return match[1] if match else ""


def _failure_subject(diagnosis, candidate):
    """Bind the failure round and return the normalized command subject."""
    if len(MACRO_REGISTRY) != 1 or MACRO_REGISTRY[0] != MACRO_MIGRATION:
        return None
    if MACRO_MIGRATION.max_edits != 1:
        return None
    if not diagnosis.bound or digest(candidate) != diagnosis.candidate_sha256:
        return None
    if not str(diagnosis.phase).startswith("xelatex-"):
        return None
    if not isinstance(diagnosis.subject, tuple) or len(diagnosis.subject) != 2:
        return None
    kind, name = normalize_subject(*diagnosis.subject)
    if kind != "command" or not isinstance(name, str) or not name:
        return None
    if not _COMMAND_NAME.fullmatch("\\" + name):
        return None
    return name


def _insert_offset(candidate, patched, added):
    """Recover the unique insert offset by exact forward+reverse equality."""
    common = 0
    while common < min(len(candidate), len(patched)) and candidate[common] == patched[common]:
        common += 1
    for offset in range(common, max(-1, common - len(added) - 1), -1):
        if (patched[offset:offset + len(added)] == added
                and patched[:offset] + patched[offset + len(added):] == candidate):
            return offset
    return None


def select_macro_plan(candidate, diagnosis, *, context, capture=None):
    """Admit macro-migration-v1 only when every gate holds in this bound round."""
    if not isinstance(context, EnvironmentContext):
        return None
    try:
        candidate_text = candidate.decode("utf-8")
    except UnicodeError:
        return None
    if "^^" in candidate_text or "\0" in candidate_text or re.search(r"\r(?!\n)", candidate_text):
        return None

    name = _failure_subject(diagnosis, candidate)
    if name is None:
        return None
    unique = _unique_source_macro(context, name)
    if unique is None:
        return None
    support_name, declaration = unique
    lines = _rebuild_proof(context, candidate_text, name, declaration)
    if lines is None:
        return None

    try:
        context.verify()
    except Exception:
        return None

    added = ("\n".join(lines) + "\n").encode("utf-8")
    patched_text = PRE.insert_lines(candidate_text, list(lines), "")
    patched = patched_text.encode("utf-8")
    if len(patched) != len(candidate) + len(added):
        return None
    offset = _insert_offset(candidate, patched, added)
    if offset is None:
        return None
    doc_at = candidate_text.find("\\begin{document}")
    if doc_at < 0 or offset > len(candidate_text[:doc_at].encode("utf-8")):
        return None

    separator = b"\r\n" if b"\r\n" in candidate else b"\n"
    target_binding = next((b for b in context.support_bindings if b.relative == support_name), None)
    if target_binding is None:
        return None
    plan = MacroEditPlan(
        identity=diagnosis.identity, rule_id=MACRO_MIGRATION.rule_id,
        version=MACRO_MIGRATION.version, diagnosis_identity=diagnosis.identity,
        diagnosis_candidate_sha256=diagnosis.candidate_sha256,
        context_fingerprint=hashlib.sha256(repr((
            context.source_binding.byte_sha256, target_binding.byte_sha256,
            _text_sha(context.retained_body_text),
        )).encode("utf-8")).hexdigest(),
        env_name=name, target_binding=target_binding, declaration=declaration,
        separator=separator, offset=offset, added_bytes=added,
        before_sha256=digest(candidate), after_sha256=digest(patched),
        original=candidate, patched=patched, lines=tuple(lines),
    )
    plan.reverse(patched)
    return plan


# ── tikzkey-migration-v1: whole TikZ style block as the indivisible unit ──────

# A key leaf is a plain pgfkeys name: no braces, control characters or TeX
# comment/parameter tokens may enter a selector pattern.
_TIKZ_KEY_LEAF = re.compile(r"[A-Za-z0-9@:.,/+*\-]{1,128}\Z")


@dataclass(frozen=True)
class TikZEditPlan(EnvironmentEditPlan):
    """EnvironmentEditPlan plus the whole source blocks fed to ``insert_lines``."""

    lines: tuple[str, ...] = ()


def _defines_key(text, leaf):
    """Structural key-definition probe over complete style blocks (spec §11 #5).

    A definition is a ``<leaf>/.style=`` / ``<leaf>/.append style=`` assignment
    or a ``\\tikzstyle{<leaf>}=[...]`` declaration inside a well-formed block.
    There is deliberately no
    per-key parser: only whole blocks returned by ``style_blocks`` are examined,
    and malformed blocks (``source == ""``) are invisible here.
    """
    pattern = re.compile(re.escape(leaf) + r"\s*/\.(?:append\s+)?style\s*=")
    style = re.compile(r"\\tikzstyle\s*\{\s*" + re.escape(leaf) + r"\s*\}\s*=\s*\[")
    for block in PB.style_blocks(text):
        if block.source and (pattern.search(block.source) or style.search(block.source)):
            return True
    return False


def _unique_source_style_block(context, leaf):
    """Locate the single source block defining ``leaf``; two or none refuse."""
    found = []
    for support_name, text in context.support_texts:
        masked = mask_comments(text)
        for block in PB.style_blocks(masked):
            if not block.source or not _defines_key(block.source, leaf):
                continue
            found.append((support_name, block.source.strip()))
    if len(found) != 1:
        return None
    return found[0]


def _rebuild_tikzkey_proof(context, candidate_text, leaf, block_source):
    """Structural rebuild proof through two fresh ``style_blocks`` observations.

    Call 1 observes the candidate and the bound template: neither may define
    the key.  Call 2 observes the source support again: exactly one whole block
    must define the key, and it must be the selected block.  This is a genuine
    structural re-observation, not a substring gate (spec §11 #2/#5), and it
    mirrors Task 2's two-call discipline.
    """
    if _defines_key(candidate_text, leaf) or _defines_key(context.style_text, leaf):
        return None
    again = _unique_source_style_block(context, leaf)
    if again is None or again[1] != block_source:
        return None
    return [block_source]


def _tikzkey_failure_subject(diagnosis, candidate):
    """Bind the failure round and return the normalized TikZ key leaf."""
    if len(TIKZKEY_REGISTRY) != 1 or TIKZKEY_REGISTRY[0] != TIKZKEY_MIGRATION:
        return None
    if TIKZKEY_MIGRATION.max_edits != 1:
        return None
    if not diagnosis.bound or digest(candidate) != diagnosis.candidate_sha256:
        return None
    if not str(diagnosis.phase).startswith("xelatex-"):
        return None
    if not isinstance(diagnosis.subject, tuple) or len(diagnosis.subject) != 2:
        return None
    kind, name = normalize_subject(*diagnosis.subject)
    if kind != "tikz_key" or not isinstance(name, str) or not name:
        return None
    if not _TIKZ_KEY_LEAF.fullmatch(name):
        return None
    return name


def select_tikzkey_plan(candidate, diagnosis, *, context, capture=None):
    """Admit tikzkey-migration-v1 only when every gate holds in this bound round."""
    if not isinstance(context, EnvironmentContext):
        return None
    try:
        candidate_text = candidate.decode("utf-8")
    except UnicodeError:
        return None
    if "^^" in candidate_text or "\0" in candidate_text or re.search(r"\r(?!\n)", candidate_text):
        return None

    leaf = _tikzkey_failure_subject(diagnosis, candidate)
    if leaf is None:
        return None
    unique = _unique_source_style_block(context, leaf)
    if unique is None:
        return None
    support_name, block_source = unique
    lines = _rebuild_tikzkey_proof(context, candidate_text, leaf, block_source)
    if lines is None:
        return None

    try:
        context.verify()
    except Exception:
        return None

    added = ("\n".join(lines) + "\n").encode("utf-8")
    patched_text = PRE.insert_lines(candidate_text, list(lines), "")
    patched = patched_text.encode("utf-8")
    if len(patched) != len(candidate) + len(added):
        return None
    offset = _insert_offset(candidate, patched, added)
    if offset is None:
        return None
    doc_at = candidate_text.find("\\begin{document}")
    if doc_at < 0 or offset > len(candidate_text[:doc_at].encode("utf-8")):
        return None

    target_binding = next((b for b in context.support_bindings if b.relative == support_name), None)
    if target_binding is None:
        return None
    plan = TikZEditPlan(
        identity=diagnosis.identity, rule_id=TIKZKEY_MIGRATION.rule_id,
        version=TIKZKEY_MIGRATION.version, diagnosis_identity=diagnosis.identity,
        diagnosis_candidate_sha256=diagnosis.candidate_sha256,
        context_fingerprint=hashlib.sha256(repr((
            context.source_binding.byte_sha256, target_binding.byte_sha256,
            _text_sha(context.retained_body_text),
        )).encode("utf-8")).hexdigest(),
        env_name=leaf, target_binding=target_binding, declaration=block_source,
        separator=b"\r\n" if b"\r\n" in candidate else b"\n",
        offset=offset, added_bytes=added,
        before_sha256=digest(candidate), after_sha256=digest(patched),
        original=candidate, patched=patched, lines=tuple(lines),
    )
    plan.reverse(patched)
    return plan
