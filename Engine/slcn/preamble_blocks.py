"""Bounded line-leading TeX commands used for preamble dependency migration."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import re

from .latex import match_brace, skip_ws_comments, strip_comment

# The syntax reader never expands a control sequence or infers package dependencies.
_HEAD = re.compile(r"[ \t]*\\([A-Za-z@]+)")
_TOKEN = re.compile(r"\\(?:[A-Za-z@]+|.)|[{}]")
_ENV_NAME = re.compile(r"\s*\{([A-Za-z@*]+)\}")
_PACKAGE = re.compile(r"\s*\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
_CONDITIONAL = frozenset((
    "if", "ifcat", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode",
    "ifinner", "ifvoid", "ifhbox", "ifvbox", "ifx", "ifeof", "iftrue", "iffalse",
    "ifcase", "ifdefined", "ifcsname", "iffontchar", "ifincsname",
))
_AFTER_HYPERREF = frozenset({"cleveref"})


def top_level_heads(text):
    """Yield (zero-based row, character offset, name) for literal line-leading commands.

    Braced arguments, explicit groups, environments and literal TeX conditionals
    have local or uncertain scope. The first active document start ends the scan.
    """
    depth = groups = conditions = offset = 0
    environments = []
    conditions_seen = set(_CONDITIONAL)
    for row, raw in enumerate(text.splitlines(keepends=True)):
        line = strip_comment(raw.rstrip("\r\n"))
        head = _HEAD.match(line)
        if head and not (depth or groups or conditions or environments):
            name = head[1]
            yield row, offset + head.start(1) - 1, name
            if name == "begin" and re.match(r"\s*\{document\}", line[head.end():]):
                return

        if environments and environments[-1] in {"comment", "verbatim", "verbatim*"}:
            if line.strip() == r"\end{" + environments[-1] + "}":
                environments.pop()
            offset += len(raw)
            continue

        declaring_if = False
        for token in _TOKEN.finditer(line):
            word = token[0]
            if word == "{":
                depth += 1
            elif word == "}":
                depth = max(0, depth - 1)
            elif not depth:
                name = word[1:]
                if declaring_if and name.startswith("if"):
                    conditions_seen.add(name)
                elif name in conditions_seen:
                    conditions += 1
                elif name == "fi":
                    conditions = max(0, conditions - 1)
                elif not conditions:
                    if name in ("begingroup", "bgroup"):
                        groups += 1
                    elif name in ("endgroup", "egroup"):
                        groups = max(0, groups - 1)
                    elif name in ("begin", "end"):
                        env = _ENV_NAME.match(line, token.end())
                        if env and name == "begin":
                            environments.append(env[1])
                        elif env and environments and environments[-1] == env[1]:
                            environments.pop()
                declaring_if = name == "newif"
        offset += len(raw)


@dataclass(frozen=True)
class StyleBlock:
    kind: str
    row: int
    last_row: int
    position: int
    source: str


def _group_end(text, start):
    """Exclusive end of a literal braced or bracketed argument, or None if incomplete."""
    if text[start] == "{":
        try:
            return match_brace(text, start) + 1
        except ValueError:
            return None
    # A bracket inside a braced value does not terminate a TikZ option list.
    i = start + 1
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
        elif ch == "%":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
        elif ch == "{":
            try:
                i = match_brace(text, i) + 1
            except ValueError:
                return None
        else:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif ch == "}":
                return None
            i += 1
    return None


# Each signature consumes complete command arguments. Only these commands may
# create migration text. Unknown commands still have observable head positions.
_STYLE_ARGS = {"tikzset": ("{",), "tikzstyle": ("{", "=", "[{")}


def style_blocks(text):
    """Capture whole physical lines, retaining comments/parameters and recording rejected spans."""
    rows = text.splitlines(keepends=True)
    starts = [0]
    for raw in rows:
        starts.append(starts[-1] + len(raw))
    result = []
    last_row = -1
    for row, position, kind in top_level_heads(text):
        if kind not in _STYLE_ARGS or row <= last_row:
            continue
        end = position + 1 + len(kind)
        valid = True
        for token in _STYLE_ARGS[kind]:
            i = skip_ws_comments(text, end)
            if i == len(text) or text[i] not in token:
                valid = False
                break
            if token == "=":
                end = i + 1
            else:
                close = _group_end(text, i)
                if close is None:
                    end = len(text)
                    valid = False
                    break
                end = close
        last_row = bisect_right(starts, end - 1) - 1
        physical_end = starts[last_row + 1]
        valid = valid and not strip_comment(text[end:physical_end]).strip()
        source = text[starts[row]:physical_end].strip() if valid else ""
        result.append(StyleBlock(kind, row, last_row, position, source))
    return result


def _packages(line):
    match = _PACKAGE.fullmatch(strip_comment(line).strip())
    return tuple(p.strip() for p in match[1].split(",")) if match else ()


def is_hyperref_line(line: str) -> bool:
    return "hyperref" in _packages(line)


def insert_lines(template_text: str, lines: list[str], anchor: str) -> str:
    """Insert a stable dependency block around real, top-level package loads.

    Keep an option-bearing package group intact. A constrained package and any
    following author packages stay in source order, along with their consumers.
    Without a crossed package, retain the existing positions of other definitions.
    """
    if not lines:
        return template_text
    rows = template_text.split("\n")
    first = last = document = None
    for row, _, head in top_level_heads(template_text):
        active = strip_comment(rows[row]).strip()
        if head == "begin" and re.match(r"\\begin\s*\{document\}", active):
            document = row
        if is_hyperref_line(active):
            if first is None:
                first = row
            last = row
        elif first is None and anchor and active == anchor.strip():
            first = last = row
    if first is None:
        block = "\n".join(lines)
        if document is None:
            return template_text.rstrip("\n") + "\n" + block + "\n"
        rows.insert(document, block)
        return "\n".join(rows)

    before = []
    after = []
    # Once an ordinary package would overtake a constrained load, moving the
    # remaining suffix together keeps its libraries and definitions after it.
    moving_suffix = False
    for line in lines:
        packages = _packages(line)
        constrained = not _AFTER_HYPERREF.isdisjoint(packages)
        if after and packages and not constrained:
            moving_suffix = True
        (after if constrained or moving_suffix else before).append(line)
    return "\n".join([*rows[:first], *before, *rows[first:last + 1], *after, *rows[last + 1:]])
