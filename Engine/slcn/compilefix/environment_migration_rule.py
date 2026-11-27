"""L1.5 narrow self-heal rule: migrate one missed local-support environment.

This module is intentionally independent of the CATCODE ``EditPlan`` in
``l1_rules.py``.  The CATCODE plan wraps a definition with two zero-width byte
spans; this rule inserts exactly ONE continuous byte slice into the candidate
preamble and removes that same slice on reverse.  No wrapping semantics are
borrowed.  Admission requires five gates to hold simultaneously in the bound
failure round; private input context arrives through an explicit internal
parameter (``EnvironmentContext``), never through display strings, capture
narratives or a taskbook.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from .. import preamble as PRE
from .. import sources
from .compiler import _STYLES, support_manifest
from .files import RepairError, digest, read, regular
from .l1_diagnostics import mask_comments
from .l1_rules import ENV_MIGRATION, ENV_REGISTRY

_ENV_NAME = re.compile(r"Environment\s+([A-Za-z@][A-Za-z@0-9-]*)\s+undefined\.")
_ANCHOR = re.compile(r"l\.(\d{1,7})\s+(.*)")
_IF_TOKEN = re.compile(r"\\(?:if[a-zA-Z]*|unless|else|fi)\b")


def _text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stamp(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)


def _decode(raw, what):
    try:
        return raw.decode("utf-8")
    except UnicodeError as error:
        raise RepairError(f"L1.5 {what} 非 UTF-8，拒绝准入") from error


@dataclass(frozen=True)
class InputBinding:
    """Byte + text + inode identity of one actually consumed input file."""

    relative: str
    byte_sha256: str
    text_sha256: str | None
    stamp: tuple

    def matches(self, path):
        if not os.path.isfile(path):
            return False
        raw = read(Path(path))
        if digest(raw) != self.byte_sha256:
            return False
        if self.stamp and tuple(_stamp(path)) != tuple(self.stamp):
            return False
        if self.text_sha256 is not None:
            try:
                if _text_sha(_decode(raw, self.relative)) != self.text_sha256:
                    return False
            except RepairError:
                return False
        return True


@dataclass(frozen=True)
class EnvironmentContext:
    """Private, same-round input context; carried as an internal parameter only."""

    source_binding: InputBinding
    support_bindings: tuple[InputBinding, ...]
    style_binding: InputBinding | None
    source_text: str
    support_texts: tuple[tuple[str, str], ...]
    style_text: str
    source_dir: str
    out_dir: str
    retained_body_text: str
    template_sty_name: str

    def verify(self):
        """Re-read every bound file immediately before apply; any change refuses."""
        if not self.source_binding.matches(os.path.join(self.source_dir,
                                                        self.source_binding.relative)):
            raise RepairError("L1.5 源稿同轮身份变化")
        for name, text in self.support_texts:
            binding = next((b for b in self.support_bindings if b.relative == name), None)
            if binding is None or not binding.matches(os.path.join(self.source_dir, name)):
                raise RepairError("L1.5 本地支撑文件同轮身份变化")
            if _text_sha(text) != binding.text_sha256:
                raise RepairError("L1.5 本地支撑文件文本绑定变化")
        if self.style_binding is not None:
            style_path = os.path.join(self.out_dir, self.style_binding.relative)
            if not self.style_binding.matches(style_path):
                raise RepairError("L1.5 样式文件同轮身份变化")
        return True


@dataclass(frozen=True)
class EnvironmentEditPlan:
    """Independent reversible byte diff: one continuous inserted slice."""

    identity: str
    rule_id: str
    version: int
    diagnosis_identity: str
    diagnosis_candidate_sha256: str
    context_fingerprint: str
    env_name: str
    target_binding: InputBinding
    declaration: str
    separator: bytes
    offset: int           # verified byte offset in the ORIGINAL candidate
    added_bytes: bytes    # the exact continuous slice (declaration + separator)
    before_sha256: str
    after_sha256: str
    original: bytes
    patched: bytes

    @property
    def start(self):
        return self.offset

    @property
    def end(self):
        return self.offset + len(self.added_bytes)

    def reverse(self, patched):
        if digest(patched) != self.after_sha256:
            raise RepairError("L1.5 编辑后候选指纹不符")
        a, b = self.offset, self.offset + len(self.added_bytes)
        if patched[a:b] != self.added_bytes:
            raise RepairError("L1.5 新增字节切片不精确")
        restored = patched[:a] + patched[b:]
        if digest(restored) != self.before_sha256 or restored != self.original:
            raise RepairError("L1.5 逆编辑未逐字节还原原候选")
        return restored


def build_context(out_dir, stem, source, capture):
    """Build the private context from the live, source-bound inputs of this round.

    ``source`` is the very argument bound by ``capture_compile``'s ``source_id``
    and rechecked by ``apply_plan``.  ``out_dir`` holds the candidate whose
    ``candidate_sha256`` is bound in ``capture``.  Nothing here reads display
    strings, the capture narrative or a taskbook.
    """
    source_path = Path(source)
    regular(source_path)
    source_bytes = read(source_path)
    source_text = _decode(source_bytes, "源稿")
    source_dir = str(source_path.parent)
    try:
        from ..build import _latex_post_bibliography_tail as _post_tail
        tail = _post_tail(source_text) or ""
    except Exception:
        tail = ""
    body = sources.extract_body_text(source_text) or ""
    retained_body = body + ("\n" + tail if tail else "")

    support_texts = tuple(
        (name, text) for name, text in PRE.local_support_files(source_text, source_dir)
    )
    support_bindings = tuple(
        InputBinding(name, digest(text.encode("utf-8")), _text_sha(text), ())
        for name, text in support_texts
    )

    style_text, style_binding = "", None
    try:
        manifest = support_manifest(Path(out_dir), stem)
    except (OSError, RepairError):
        manifest = {}
    style_name = next((name for name in sorted(manifest) if name in _STYLES), "")
    if style_name:
        style_path = Path(out_dir) / style_name
        regular(style_path)
        raw = read(style_path)
        style_text = _decode(raw, "样式")
        style_binding = InputBinding(style_name, digest(raw), _text_sha(style_text),
                                     tuple(_stamp(style_path)))

    fingerprint = hashlib.sha256(repr((
        digest(source_bytes), [(n, _text_sha(t)) for n, t in support_texts],
        None if style_binding is None else (style_binding.relative, style_binding.byte_sha256),
        _text_sha(retained_body),
    )).encode("utf-8")).hexdigest()
    source_binding = InputBinding(source_path.name, digest(source_bytes),
                                  _text_sha(source_text), tuple(_stamp(source_path)))
    return EnvironmentContext(
        source_binding=source_binding, support_bindings=support_bindings,
        style_binding=style_binding, source_text=source_text,
        support_texts=support_texts, style_text=style_text,
        source_dir=source_dir, out_dir=str(out_dir), retained_body_text=retained_body,
        template_sty_name=Path(style_name).stem,
    )


def _failure_round(report, capture, candidate):
    """Gate ①: one bound primary error plus only its attributable cascade."""
    if len(ENV_REGISTRY) != 1 or ENV_REGISTRY[0] != ENV_MIGRATION:
        return None
    if ENV_MIGRATION.max_edits != 1:
        return None
    if not report.bound or digest(candidate) != report.candidate_sha256:
        return None
    if capture is None or not str(report.phase).startswith("xelatex-"):
        return None
    failure = capture.get("failure") if isinstance(capture, dict) else None
    if not isinstance(failure, tuple) or len(failure) != 4 or not isinstance(failure[3], bytes):
        return None
    if len(failure[3]) > 2 * 1024 * 1024 or failure[2] == 0:
        return None
    log = failure[3].decode("utf-8", errors="replace")
    rows = log.splitlines()
    source_lines = candidate.decode("utf-8", errors="replace").splitlines()
    env_errors, other_errors = [], []
    for index, row in enumerate(rows):
        if not row.startswith("! "):
            continue
        match = _ENV_NAME.search(row)
        if match:
            env_errors.append((index, match.group(1)))
        else:
            other_errors.append((index, row))
    if len(env_errors) != 1:
        return None
    index, name = env_errors[0]
    end = next((n for n in range(index + 1, len(rows)) if rows[n].startswith("! ")), len(rows))
    anchor = None
    for context in rows[index + 1:end]:
        match = _ANCHOR.fullmatch(context.strip())
        if match:
            n = int(match.group(1))
            if 1 <= n <= len(source_lines) and match.group(2).strip() and \
                    source_lines[n - 1].lstrip().startswith(match.group(2).strip()):
                anchor = n
            break
    if anchor is None:
        return None
    masked = mask_comments(candidate.decode("utf-8", errors="replace")).splitlines()
    pattern_begin = re.compile(r"^\s*\\begin\s*\{\s*" + re.escape(name) + r"\s*\}")
    pattern_end = re.compile(r"^\s*\\end\s*\{\s*" + re.escape(name) + r"\s*\}")
    begins = [i + 1 for i, line in enumerate(masked) if pattern_begin.match(line)]
    ends = [i + 1 for i, line in enumerate(masked) if pattern_end.match(line)]
    if len(begins) != 1 or len(ends) != 1 or begins[0] != anchor or ends[0] <= begins[0]:
        return None
    for _index, row in other_errors:
        if "ended by" not in row or "\\end{" + name + "}" not in row:
            return None  # an independent primary error; refuse this rule
        cascade_end = None
        stop = next((n for n in range(_index + 1, len(rows)) if rows[n].startswith("! ")),
                    len(rows))
        for context in rows[_index + 1:stop]:
            match = _ANCHOR.fullmatch(context.strip())
            if match:
                cascade_end = int(match.group(1))
                break
        if cascade_end != ends[0]:
            return None
    return name, begins[0], ends[0]


def _unique_declaration(context, name):
    """Gate ②: exactly one pre-dedup, top-level, unambiguous env declaration.

    FINDING 1: the shared scanner's ``scan_support_definitions`` observer hands
    out the *de-duplicated* list (it calls ``scan_preamble`` without an observer
    and only then notifies with the deduped result).  The card demands counting
    on the **pre-dedup** result: two competing declarations of the same env in
    one file must be rejected even if dedup leaves a single winner.  Use the
    ``scan_preamble`` observer directly — it receives ``(pos, DefItem)`` tuples
    from the raw ``found`` list, sorted by position, before dedup.
    """
    found = []
    for support_name, text in context.support_texts:
        masked = mask_comments(text)
        raw = []
        PRE.scan_preamble(masked, observer=lambda items: raw.extend(items))
        for _pos, item in raw:
            if item.kind in {"newenvironment", "newtheorem"} and item.name == name:
                found.append((support_name, _pos, item))
    if len(found) != 1:
        return None
    support_name, pos, item = found[0]
    # Conditional / ambiguous scope: an unclosed conditional before the decl.
    # ``pos`` is the byte offset in the *masked* text from the observer, so use
    # the same masked text for the prefix check (comments already blanked).
    masked = mask_comments(dict(context.support_texts)[support_name])
    prefix = masked[:pos]
    opens = len(re.findall(r"\\(?:if[a-zA-Z]*|unless)\b", prefix))
    closes = len(re.findall(r"\\fi\b", prefix))
    if opens > closes:
        return None
    if "\\if" in item.source or "\\fi" in item.source:
        return None
    # A renewenvironment degrade must not masquerade as a fresh declaration.
    if item.source.lstrip().startswith("\\renewenvironment"):
        return None
    return support_name, item.source.strip()


def _rebuild(context, candidate_text, name, begin_line, end_line, declaration):
    """Gates ③④⑤: fresh shared-migrator runs prove the missed migration."""
    if declaration and declaration in PRE.defined_names(candidate_text):
        return None
    if declaration.encode("utf-8") in candidate_text.encode("utf-8"):
        return None
    lines = candidate_text.splitlines()
    usage = "\n".join(lines[begin_line - 1:end_line])
    if "\\begin{" + name + "}" not in usage or "\\end{" + name + "}" not in usage:
        return None
    if name not in PRE.used_environments(usage):
        return None
    covered_body = context.retained_body_text + "\n" + usage + "\n"

    def run(body_text):
        observation = PRE.PlanningObservation()
        plan = PRE.plan_migration(
            context.source_text, candidate_text, context.style_text,
            context.template_sty_name, body_text, context.source_dir,
            observation=observation,
        )
        return plan, observation

    plan0, obs0 = run(context.retained_body_text)
    if list(plan0.lines) or name in list(getattr(plan0, "definitions", [])):
        return None
    decision0 = next((d for d in obs0.decisions if d.name == name), None)
    if decision0 is None or getattr(decision0, "action", "") != "skipped" or \
            getattr(decision0, "reason", "") != "body_unused":
        return None

    plan1, obs1 = run(covered_body)
    if list(plan1.lines) != [declaration] or list(getattr(plan1, "definitions", [])) != [name]:
        return None
    if name in list(getattr(plan1, "converted_renews", [])):
        return None
    decision1 = next((d for d in obs1.decisions if d.name == name), None)
    if decision1 is None or getattr(decision1, "action", "") not in {"imported", "seed_missed"}:
        return None
    if getattr(decision1, "source_role", "") != "local_support":
        return None
    if getattr(decision1, "scope_confidence", "") == "unknown":
        return None
    return plan1.lines


def select_environment_plan(candidate, diagnosis, *, context, capture=None):
    """Admit the L1.5 rule only when all five gates hold in this bound round."""
    if context is None or not isinstance(context, EnvironmentContext):
        return None
    try:
        candidate_text = candidate.decode("utf-8")
    except UnicodeError:
        return None
    if "^^" in candidate_text or "\0" in candidate_text or re.search(r"\r(?!\n)", candidate_text):
        return None

    round_info = _failure_round(diagnosis, capture, candidate)
    if round_info is None:
        return None
    name, begin_line, end_line = round_info
    unique = _unique_declaration(context, name)
    if unique is None:
        return None
    support_name, declaration = unique
    target_binding = next((b for b in context.support_bindings if b.relative == support_name), None)
    if target_binding is None:
        return None

    lines = _rebuild(context, candidate_text, name, begin_line, end_line, declaration)
    if lines is None:
        return None

    try:
        context.verify()
    except Exception:
        return None
    separator = b"\r\n" if b"\r\n" in candidate else b"\n"
    added = ("\n".join(lines) + "\n").encode("utf-8")
    patched_text = PRE.insert_lines(candidate_text, list(lines), "")
    patched = patched_text.encode("utf-8")
    if len(patched) != len(candidate) + len(added):
        return None
    common = 0
    while common < min(len(candidate), len(patched)) and candidate[common] == patched[common]:
        common += 1
    # The common prefix can overshoot the true insertion point when the added
    # slice and the candidate tail share leading bytes (e.g. both start with a
    # backslash). Recover the unique offset by exact forward+reverse equality.
    offset = None
    for candidate_offset in range(common, max(-1, common - len(added) - 1), -1):
        if (patched[candidate_offset:candidate_offset + len(added)] == added and
                patched[:candidate_offset] + patched[candidate_offset + len(added):] == candidate):
            offset = candidate_offset
            break
    if offset is None:
        return None
    if digest(candidate) != diagnosis.candidate_sha256:
        return None
    doc_at = candidate_text.find("\\begin{document}")
    if doc_at < 0 or offset > len(candidate_text[:doc_at].encode("utf-8")):
        return None

    plan = EnvironmentEditPlan(
        identity=diagnosis.identity, rule_id=ENV_MIGRATION.rule_id,
        version=ENV_MIGRATION.version, diagnosis_identity=diagnosis.identity,
        diagnosis_candidate_sha256=diagnosis.candidate_sha256,
        context_fingerprint=hashlib.sha256(repr((
            context.source_binding.byte_sha256, target_binding.byte_sha256,
            _text_sha(context.retained_body_text),
        )).encode("utf-8")).hexdigest(),
        env_name=name, target_binding=target_binding, declaration=declaration,
        separator=separator, offset=offset, added_bytes=added,
        before_sha256=digest(candidate), after_sha256=digest(patched),
        original=candidate, patched=patched,
    )
    plan.reverse(patched)
    return plan
