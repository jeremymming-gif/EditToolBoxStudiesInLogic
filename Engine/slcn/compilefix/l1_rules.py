"""Auditable L1 whitelist: a total parser for a deliberately small LaTeX subset."""
from dataclasses import dataclass
import re

from ..preamble import at_control_words
from .files import RepairError, digest
from .l1_diagnostics import mask_comments


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    max_edits: int
    precondition: str
    allowed_region: str
    invariants: str
    rollback: str
    positive: str
    negative: str
    test_script: str


CATCODE = Rule(
    "catcode-top-level-at-v1", 1, 2,
    "One bound XeLaTeX catcode signature and one anchored, complete top-level newcommand; "
    "known standard article preamble grammar; @ inactive on entry; no scope ambiguity.",
    "Two zero-width UTF-8 byte spans immediately before and after the complete definition.",
    "Definition body and every original byte unchanged; balanced wrapper; remove insertions "
    "to recover exact input; surrounding @ scope unchanged; one matching definition only.",
    "Journaled native exchange; compare displaced inode; restore original TeX/PDF/state on failure.",
    "tests/test_compilefix_l1_diagnostics.py:POSITIVE",
    "tests/test_compilefix_l1_rules.py:RuleTests",
    "Engine/tests/test_compilefix_l1_rules.py",
)
REGISTRY = (CATCODE,)


ENV_MIGRATION = Rule(
    "environment-migration-v1", 1, 1,
    "One bound XeLaTeX failure round with a single primary Environment X undefined error, "
    "a uniquely paired anchored begin/end, a single pre-dedup top-level newenvironment "
    "declaration in local support, verifiable same-round inputs, a missed migration "
    "explained by retained-body coverage, and a shared-migrator rebuild yielding exactly "
    "one declaration.",
    "One continuous inserted byte slice in the candidate preamble; no original byte is "
    "modified, removed or reordered.",
    "Insert exactly one declaration plus separator; verify original hash and position "
    "before apply, verify post hash and exact added bytes on reverse, delete the slice "
    "and verify the original hash and bytes.",
    "Journaled L1 transaction; isolated compile and full delivery checks; restore "
    "original TeX/PDF/state on failure; recovery-required if rollback is unsafe.",
    "tests/test_compilefix_l15_environment_migration.py:TEST_ENV_MIGRATION",
    "tests/test_compilefix_l15_environment_migration.py:TestL15NegativeGuards",
    "Engine/tests/test_compilefix_l15_environment_migration.py",
)
ENV_REGISTRY = (ENV_MIGRATION,)


MACRO_MIGRATION = Rule(
    "macro-migration-v1", 1, 1,
    "One bound XeLaTeX failure round with a single primary Undefined control "
    "sequence subject, a single top-level \\newcommand/\\providecommand definition "
    "of that exact name in source support, no template/candidate definition, a "
    "retained-body/covered-body rebuild proving the macro is needed, and "
    "verifiable same-round inputs.",
    "One continuous inserted byte slice in the candidate preamble; no original "
    "byte is modified, removed or reordered.",
    "Insert exactly one declaration plus separator; verify original hash and "
    "position before apply, verify post hash and exact added bytes on reverse, "
    "delete the slice and verify the original hash and bytes.",
    "Journaled L1 transaction; isolated compile and full delivery checks; restore "
    "original TeX/PDF/state on failure; recovery-required if rollback is unsafe.",
    "tests/test_compilefix_migration_family_20261127.py:MacroMigrationPositiveTests",
    "tests/test_compilefix_migration_family_20261127.py:MacroMigrationNegativeTests",
    "Engine/tests/test_compilefix_migration_family_20261127.py",
)
MACRO_REGISTRY = (MACRO_MIGRATION,)


TIKZKEY_MIGRATION = Rule(
    "tikzkey-migration-v1", 1, 1,
    "One bound XeLaTeX failure round with a single primary unknown TikZ key "
    "subject, exactly one top-level \\tikzset/\\tikzstyle block in local support "
    "defining that key (whole block as the indivisible unit), no candidate or "
    "template definition of that key, a structural rebuild proof through "
    "style_blocks, and verifiable same-round inputs.",
    "One continuous inserted byte slice in the candidate preamble carrying the "
    "entire source block; no original byte is modified, removed or reordered.",
    "Insert exactly the whole source block plus separator; verify original hash "
    "and position before apply, verify post hash and exact added bytes on "
    "reverse, delete the slice and verify the original hash and bytes; preserve "
    "#1 and nested braces verbatim.",
    "Journaled L1 transaction; isolated compile and full delivery checks; restore "
    "original TeX/PDF/state on failure; recovery-required if rollback is unsafe.",
    "tests/test_compilefix_migration_family_20261127.py:TikzKeyMigrationPositiveTests",
    "tests/test_compilefix_migration_family_20261127.py:TikzKeyMigrationNegativeTests",
    "Engine/tests/test_compilefix_migration_family_20261127.py",
)
TIKZKEY_REGISTRY = (TIKZKEY_MIGRATION,)

_INTERNAL = {"@gobble", "@firstofone", "@firstoftwo", "@secondoftwo", "@empty"}
_PURE = {"textbf", "textit", "emph", "relax", "space"}
_RESERVED = _PURE | {"makeatletter", "makeatother", "begin", "end", "documentclass", "newcommand"}


@dataclass(frozen=True)
class EditPlan:
    identity: str
    rule_id: str
    before_sha256: str
    after_sha256: str
    start: int
    end: int
    prefix: bytes
    suffix: bytes
    original: bytes
    patched: bytes

    def reverse(self, patched):
        if digest(patched) != self.after_sha256:
            raise RepairError("L1 编辑后候选指纹不符")
        a = self.start + len(self.prefix)
        b = self.end + len(self.prefix)
        if patched[self.start:a] != self.prefix or patched[b:b + len(self.suffix)] != self.suffix:
            raise RepairError("L1 包装不满足允许区间")
        restored = patched[:self.start] + patched[a:b] + patched[b + len(self.suffix):]
        if restored != self.original:
            raise RepairError("L1 逆编辑未逐字节还原")
        return restored


def _ws(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _group(text, pos, opening="{", closing="}"):
    pos = _ws(text, pos)
    if pos >= len(text) or text[pos] != opening:
        raise ValueError("unrecognized argument")
    start, depth = pos + 1, 1
    pos += 1
    while pos < len(text):
        if text[pos] == "\\":
            match = re.match(r"\\(?:[A-Za-z@]+|[^\n])", text[pos:])
            if not match:
                raise ValueError("incomplete escape")
            pos += len(match[0])
            continue
        if text[pos] == opening:
            depth += 1
        if text[pos] == closing:
            depth -= 1
            if not depth:
                return text[start:pos], pos + 1
        pos += 1
    raise ValueError("incomplete group")


def _pure_body(body):
    if not re.fullmatch(r"[A-Za-z0-9@ \\{}\t\n\r.,:;!?()\-]*", body):
        return False
    return all(word[1:] in _INTERNAL | _PURE or len(word) == 2 and word[1] in "{} "
               for word in re.findall(r"\\(?:[A-Za-z@]+|[^\n])", body))


def _definitions(text):
    """Parse every preamble token; unknown structures return no write plan.

    The first admitted grammar is standard article + metadata + newcommand.
    Packages/custom classes/conditionals/groups/environments require a separate
    rule admission. This is a positive grammar, not a dangerous-token blacklist.
    """
    masked = mask_comments(text)
    start = masked.find(r"\begin{document}")
    if start < 0 or masked.count(r"\begin{document}") != 1 or masked.count(r"\end{document}") != 1:
        return ()
    pre = masked[:start]
    pos, active, class_seen = 0, False, False
    definitions, names = [], set()
    try:
        while (pos := _ws(pre, pos)) < len(pre):
            first = pos
            token = re.match(r"\\([A-Za-z]+)(?![A-Za-z@])", pre[pos:])
            if not token:
                return ()
            command = token[1]
            pos += len(token[0])
            if command == "documentclass" and not class_seen and not active and not definitions:
                pos = _ws(pre, pos)
                if pos < len(pre) and pre[pos] == "[":
                    options, pos = _group(pre, pos, "[", "]")
                    if not re.fullmatch(r"[A-Za-z0-9,= .-]*", options):
                        return ()
                name, pos = _group(pre, pos)
                if name != "article":
                    return ()
                class_seen = True
            elif command in {"makeatletter", "makeatother"} and class_seen:
                if active == (command == "makeatletter"):
                    return ()  # Duplicate or unmatched toggles are ambiguous.
                active = command == "makeatletter"
            elif command in {"title", "author", "date"} and class_seen:
                value, pos = _group(pre, pos)
                if not re.fullmatch(r"[\w .,;:!?()-]*", value):
                    return ()
            elif command == "newcommand" and class_seen:
                name, pos = _group(pre, pos)
                if not re.fullmatch(r"\\[A-Za-z]+", name) or name[1:] in names | _RESERVED:
                    return ()
                names.add(name[1:])
                body, pos = _group(pre, pos)
                if not _pure_body(body):
                    return ()
                if at_control_words(body):
                    definitions.append((first, pos, name, active))
            else:
                return ()
    except ValueError:
        return ()
    if not class_seen or active:
        return ()
    return tuple(item for item in definitions if not item[3])


def select_plan(candidate, diagnosis):
    if (not diagnosis.bound or digest(candidate) != diagnosis.candidate_sha256
            or len(diagnosis.error_lines) != 1 or not diagnosis.phase.startswith("xelatex-")):
        return None
    eligible = [d for d in diagnosis.items if d.category == "catcode" and d.confidence == "exact"]
    # Other report-only findings (including template scores) cannot prove or disprove this rule.
    if len(eligible) != 1 or len(REGISTRY) != 1 or REGISTRY[0] != CATCODE:
        return None
    try:
        text = candidate.decode("utf-8")
    except UnicodeError:
        return None
    if "^^" in text or "\0" in text or re.search(r"\r(?!\n)", text):
        return None
    definitions = _definitions(text)
    if len(definitions) != 1:
        return None
    first, last, name, _ = definitions[0]
    line = diagnosis.error_lines[0]
    lines = mask_comments(text).splitlines()
    on_definition = text.count("\n", 0, first) + 1 <= line <= text.count("\n", 0, last - 1) + 1
    at_use = 0 < line <= len(lines) and lines[line - 1].strip() == name
    if not (on_definition or at_use):
        return None
    a, b = len(text[:first].encode("utf-8")), len(text[:last].encode("utf-8"))
    newline = b"\r\n" if b"\r\n" in candidate else b"\n"
    prefix, suffix = b"\\makeatletter" + newline, newline + b"\\makeatother" + newline
    patched = candidate[:a] + prefix + candidate[a:b] + suffix + candidate[b:]
    plan = EditPlan(diagnosis.identity, CATCODE.rule_id, digest(candidate), digest(patched),
                    a, b, prefix, suffix, candidate, patched)
    plan.reverse(patched)
    return plan
