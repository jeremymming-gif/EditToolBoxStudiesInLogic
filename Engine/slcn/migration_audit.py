"""In-memory, bounded comparison of planning decisions and literal preamble evidence."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import re

from .latex import find_all_macros, strip_comments
from .preamble_blocks import style_blocks, top_level_heads


_MAX_ROWS = 4096
_MAX_CHARS = 2 * 1024 * 1024
_SEPARATE_CHANNEL = {
    "documentclass", "title", "author", "date", "thanks", "input", "include",
    "makeatletter", "makeatother", "begin", "end",
}
_WORDS = {"env": "environment", "macro": "command"}


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _style_keys(block):
    """Literal keys only. A comma inside a style value never starts a definition."""
    text = strip_comments(block.source)
    if block.kind == "tikzstyle":
        match = re.match(r"\\tikzstyle\s*\{([^{}]+)\}", text)
        entries = [match[1]] if match else []
    else:
        entries = []
        start = text.index("{") + 1
        depth = 0
        for match in re.finditer(r"\\.|[{},]", text[start:]):
            token = match[0]
            if token == "{":
                depth += 1
            elif depth and token == "}":
                depth -= 1
            elif not depth and token in (",", "}"):
                end = text.index("{") + 1 + match.start()
                item = text[start:end]
                head = re.match(r"\s*([^{}=,]+?)/\.(?:style|style args|style n args)\s*=", item)
                if head:
                    entries.append(head[1])
                start = end + 1
                if token == "}":
                    break
    for key in entries:
        key = key.strip().removeprefix("/tikz/")
        if re.fullmatch(r"[a-zA-Z0-9_:. /-]{1,128}", key) and not key.startswith("/"):
            yield key


@dataclass
class MigrationAudit:
    """Rows retain only digests and planner codes; positions are expanded lines (1-based)."""

    rows: list[dict] = field(default_factory=list)
    incomplete: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    _heads: dict[int, tuple[int, str]] = field(default_factory=dict, repr=False)
    _support_files: dict[str, tuple[str, list[int]]] = field(default_factory=dict, repr=False)
    _covered: set[int] = field(default_factory=set, repr=False)
    _source: str = field(default="", repr=False)

    @contextmanager
    def guard(self):
        # Diagnostic failure must not change conversion or compiler behavior.
        try:
            yield
        except Exception:
            self.incomplete = True

    def begin(self, pre):
        self.rows.clear()
        self.counts.clear()
        self._covered.clear()
        self._heads.clear()
        self._support_files.clear()
        self._source = ""
        self.incomplete = len(pre) > _MAX_CHARS
        if self.incomplete:
            return
        # Keep a digest, never raw source text, between planning and finalization.
        self._source = _digest(pre)
        for row, offset, command in top_level_heads(pre):
            if len(self._heads) >= _MAX_ROWS:
                self.incomplete = True
                break
            self._heads[row] = (offset, command)

    def register_support_file(self, relative_id, text):
        if len(text) > _MAX_CHARS:
            self.incomplete = True
            return
        starts = [0]
        starts.extend(match.end() for match in re.finditer("\n", text))
        if len(starts) > _MAX_ROWS:
            self.incomplete = True
            starts = starts[:_MAX_ROWS]
        self._support_files[relative_id] = (_digest(relative_id), starts)

    def record(self, kind, name, position, action, reason, *, role="author_preamble",
               target=None, coordinate="author_preamble", source_sha256=None):
        if role == "author_preamble" and position and position - 1 not in self._heads:
            return
        if len(self.rows) >= _MAX_ROWS:
            self.incomplete = True
            return
        if role == "author_preamble" and position:
            self._covered.add(position - 1)
        self.rows.append({
            "kind": kind, "name_sha256": _digest(name), "action": action, "reason": reason,
            "source_role": role, "position": position, "candidate_state": "unknown",
            "candidate_position": 0, "reordered": False, "coordinate": coordinate,
        })
        if target is not None:
            self.rows[-1]["target_sha256"] = _digest(target)
        if source_sha256 is not None:
            self.rows[-1]["source_sha256"] = source_sha256

    def record_block(self, block):
        if not block.source:
            self.record("unrecognized", block.kind, block.row + 1, "skipped", "unrecognized")
            return
        self.record("tikz_block", block.source, block.row + 1, "imported", "imported")
        for key in _style_keys(block):
            self.record("tikz_key", key, block.row + 1, "imported", "imported")

    def declarations(self, decisions, selected_counters, pre):
        if not self._source:
            return
        offsets = {pos: row + 1 for row, (pos, _) in self._heads.items()}
        for decision in decisions:
            role = decision.source_role
            coordinate = "author_preamble"
            source_sha256 = None
            if role == "author_preamble":
                position = offsets.get(decision.position, 0)
            elif role == "local_support":
                support = self._support_files.get(decision.source_id)
                if support:
                    source_sha256, starts = support
                    position = bisect_right(starts, decision.position)
                    coordinate = "support_file_line"
                else:
                    position = 0
            else:
                position = 0
            if role == "author_preamble" and not position:
                continue
            action, reason = decision.action, decision.reason
            if role == "author_preamble" and decision.kind == "counter" and decision.name in selected_counters:
                action, reason = "imported", "imported"
            self.record(_WORDS.get(decision.namespace, decision.namespace), decision.name,
                        position, action, reason, role=role, coordinate=coordinate,
                        source_sha256=source_sha256)
        # Ordinary macros are selected later in build_latex. Known metadata
        # slots also belong to another stage of that build.
        for macro in find_all_macros(pre):
            if macro.span[0] in offsets:
                self._covered.add(offsets[macro.span[0]] - 1)
        for row, (_, cmd) in self._heads.items():
            if row not in self._covered and cmd not in _SEPARATE_CHANNEL:
                self.record("unrecognized", cmd, row + 1, "skipped", "unrecognized")

    def macros(self, pre, custom, selected, renames, added):
        if not self._source or _digest(pre) != self._source:
            return
        offsets = {pos: row + 1 for row, (pos, _) in self._heads.items()}
        available = {name for _, name, _ in custom}
        chosen = {name for _, name, _ in selected}
        added = set(added)
        for macro in find_all_macros(pre):
            if macro.name not in available or macro.span[0] not in offsets:
                continue
            target = renames.get(macro.name, macro.name)
            if macro.name not in chosen:
                action, reason = "skipped", "body_unused"
            elif target in added:
                action, reason = "imported", "renamed" if target != macro.name else "imported"
            else:
                action, reason = "skipped", "template_exists"
            self.record("command", macro.name, offsets[macro.span[0]], action, reason,
                        target=target if target != macro.name else None)
            available.remove(macro.name)

    def compare(self, candidate, style, style_name):
        """Compare visible names/blocks only, without claiming TeX runtime availability."""
        if max(len(candidate), len(style)) > _MAX_CHARS:
            self.incomplete = True
            return
        visible = _inventory(candidate)
        provided = (_inventory(style) if ("package", _digest(style_name)) in visible else {})
        order = []
        for r in self.rows:
            key = (r["kind"], r.get("target_sha256", r["name_sha256"]))
            matches = visible.get(key, ())
            if r["kind"] == "unrecognized" or len(matches) > 1:
                state = "unknown"
            elif matches:
                state = "present"
            elif key in provided:
                state = "style_provided"
            else:
                state = "absent"
            r["candidate_state"] = state
            r["candidate_position"] = matches[0][0] if state == "present" else 0
            r["reordered"] = False
            if (state == "present" and r["position"]
                    and r["coordinate"] in ("author_preamble", "support_file_line")
                    and r["kind"] != "tikz_key"
                    and r["reason"] != "already_imported"):
                # Positions are comparable only within the same source coordinate.
                # Local declarations from different support files each have their
                # own line-number space, so keep those groups separate.
                source_group = (r["coordinate"], r.get("source_sha256"))
                order.append((source_group, r["position"], matches[0], r))
        # Every endpoint of an inversion is counted, including an earlier item
        # displaced by a later one. Equal positions are the same physical block.
        groups = defaultdict(list)
        for group, source_position, candidate_position, row in order:
            groups[group].append((source_position, candidate_position, row))
        for group in groups.values():
            group.sort(key=lambda x: x[0])  # stable within a comma-separated source package line
            high = (-1, -1)
            for _, pos, row in group:
                row["reordered"] = pos < high
                high = max(pos, high)
            low = (float("inf"), 0)
            for _, pos, row in reversed(group):
                row["reordered"] |= pos > low
                low = min(pos, low)
        primary = [r for r in self.rows if r["kind"] != "tikz_key"]
        self.counts = {
            "observed": len(primary),
            "absent": sum(r["candidate_state"] == "absent" for r in primary),
            "missing_imports": sum(r["candidate_state"] == "absent" and r["action"] in
                                   ("imported", "closure_added", "converted_renew") for r in primary),
            "style_provided": sum(r["candidate_state"] == "style_provided" for r in primary),
            "reordered": sum(r["reordered"] for r in primary),
            "unrecognized": sum(r["kind"] == "unrecognized" for r in primary),
        }

    def display(self):
        return {"migration": [dict(row) for row in self.rows], "migration_counts": dict(self.counts),
                "migration_incomplete": self.incomplete}


def _inventory(text):
    from . import preamble as pre

    heads = {}
    rows = text.splitlines()
    for row, pos, cmd in top_level_heads(text):
        if cmd == "begin" and re.match(r"\\begin\s*\{document\}", text[pos:pos + 32]):
            text = text[:pos]
            break
        if len(heads) >= _MAX_ROWS:
            raise ValueError("preamble limit")
        heads[pos] = row
    visible = defaultdict(list)

    def add(kind, name, row, column=0):
        visible[kind, _digest(name)].append((row + 1, column))

    for pos, row in heads.items():
        clean = pre._clean_line(rows[row])
        for kind, regex in (("package", pre._PKG_LINE_RE), ("tikz_library", pre._TIKZ_LINE_RE)):
            if m := regex.match(clean):
                for n, name in enumerate(m[m.lastindex].split(",")):
                    add(kind, name.strip(), row, n)
                break
    for block in style_blocks(text):
        if block.source:
            add("tikz_block", block.source, block.row)
            for key in _style_keys(block):
                add("tikz_key", key, block.row)
    for item in pre.scan_support_definitions(text):
        if item.pos in heads:
            namespace, name = pre._def_key(item)
            add(_WORDS.get(namespace, namespace), name, heads[item.pos])
    return visible
