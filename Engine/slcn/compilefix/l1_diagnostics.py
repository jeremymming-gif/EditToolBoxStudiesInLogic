"""Six report categories, bound to Stage A capture; exports use fixed vocabulary only."""
from dataclasses import asdict, dataclass, field
import re

from ..preamble import at_control_words
from .files import digest, json_bytes


@dataclass(frozen=True)
class Diagnostic:
    category: str
    confidence: str
    line: int | None
    log_line: int | None
    anchor: str
    locations: tuple[int, ...] = ()


@dataclass
class Diagnosis:
    identity: str
    candidate_sha256: str
    phase: str
    returncode: int | None
    items: tuple[Diagnostic, ...]
    attempts: list[dict] = field(default_factory=list)
    bound: bool = False
    # Private, local parser evidence. Never included in shareable().
    error_lines: tuple[int, ...] = ()

    def shareable(self):
        return {"identity": self.identity, "candidate_sha256": self.candidate_sha256,
                "phase": self.phase, "returncode": self.returncode,
                "diagnostics": [asdict(item) for item in self.items],
                "attempts": list(self.attempts)}


def mask_comments(text):
    return re.sub(r"%[^\n]*|\\[^\n]", lambda m: (" " * len(m[0]) if m[0].startswith("%") else m[0]), text)


def _anchors(log, text):
    lines = log.splitlines()
    source = text.splitlines()
    errors = []
    for index, row in enumerate(lines):
        if not row.startswith("! "):
            continue
        end = next((n for n in range(index + 1, len(lines)) if lines[n].startswith("! ")), len(lines))
        anchor = None
        for context in lines[index + 1:end]:
            if m := re.fullmatch(r"l\.(\d{1,7})\s+(.*)", context):
                n = int(m[1])
                if 1 <= n <= len(source) and m[2].strip() and source[n - 1].lstrip().startswith(m[2].strip()):
                    anchor = n
                break
        errors.append((row, index + 1, anchor))
    return errors


def diagnose(candidate, capture, *, template_results=(), available_paths=None):
    fingerprint = digest(candidate)
    text = candidate.decode("utf-8", errors="replace")
    masked = mask_comments(text)
    capture = capture or {}
    detailed = capture.get("detailed")
    failure = capture.get("failure")
    bound = (capture.get("candidate_sha256") == fingerprint and isinstance(failure, tuple)
             and len(failure) == 4 and isinstance(failure[3], bytes) and len(failure[3]) <= 2 * 1024 * 1024)
    phase, code, log = "unknown", None, ""
    if bound:
        _, step, code, raw = failure
        phase = "biber" if step == 2 else f"xelatex-{step}" if step in (1, 3, 4) else "unknown"
        log = raw.decode("utf-8", errors="replace")
    elif detailed is not None and detailed.candidate_sha256 == fingerprint:
        phase, code = detailed.phase, detailed.returncode
    errors = _anchors(log, text)
    items = []

    def add(category, anchor, line=None, log_line=None, confidence="report-only", locations=()):
        if len(items) < 32:
            items.append(Diagnostic(category, confidence, line, log_line, anchor, tuple(locations)[:32]))

    if re.search(r"\([^()\n]*\.(?:aux|bbl)\b[^()]*\n! ", log):
        add("stale_intermediate", "intermediate-file-error", log_line=next((e[1] for e in errors), None))
    if detailed is not None and detailed.cleaned:
        add("stale_intermediate", "existing-cleanup-recorded")
    if detailed is not None and detailed.cleanup_remaining:
        add("stale_intermediate", "cleanup-incomplete")
    words = at_control_words(text)
    if words:
        matching = [e for e in errors if "Undefined control sequence." in e[0]
                    or ("spacefactor" in e[0] and "vertical mode" in e[0])]
        # Nonstopmode may emit a deterministic Missing-$ cascade after the
        # spacefactor root error. Only anchored continuations at the same use
        # or the literal document terminator belong to this one signature.
        def continuation(error):
            row, _, line = error
            return (row == "! Missing $ inserted." and line is not None
                    and (line == matching[0][2] or text.splitlines()[line - 1].strip() == r"\end{document}"))
        high = (bound and phase.startswith("xelatex-") and len(matching) == 1
                and matching[0][2] is not None and errors[0] == matching[0]
                and all(continuation(error) for error in errors[1:]))
        add("catcode", "at-control-word", text.count("\n", 0, words[0][0]) + 1,
            matching[0][1] if matching else None, "exact" if high else "report-only")
    for match in re.finditer(r"\\(?:input|include|includegraphics)\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}", masked):
        target = match[1].strip()
        from pathlib import PurePosixPath
        if "\\" in target or "#" in target:
            anchor = "macro-path-review"
        elif target.startswith(("/", "~")) or ".." in PurePosixPath(target).parts or ":" in target:
            anchor = "external-path-review"
        elif available_paths is None:
            anchor = "path-needs-local-review"
        else:
            command = match[0].split("{", 1)[0]
            suffixes = ("", ".pdf", ".png", ".jpg", ".jpeg") if "includegraphics" in command else ("", ".tex")
            matches = sum(target + suffix in available_paths for suffix in suffixes)
            anchor = "relative-path-ambiguous" if matches > 1 else "relative-path-present" if matches else "relative-path-missing"
        add("dependency_path", anchor, masked.count("\n", 0, match.start()) + 1)
    if not any(d.category == "dependency_path" for d in items) and re.search(r"(?:File|file).*not found", log):
        add("dependency_path", "dependency-not-found", log_line=next((e[1] for e in errors), None))
    for result in template_results:
        if result.get("rule") in {f"T{n}" for n in range(1, 9)} and result.get("status") == "FAIL":
            locations = tuple(n for n in result.get("lines", ()) if type(n) is int and n >= 0)
            add("templatecheck", result["rule"], locations[0] if locations else 0, locations=locations)
    labels = {}
    for match in re.finditer(r"\\label\s*\{([^{}\\]*)\}", masked):
        labels.setdefault(match[1], []).append(masked.count("\n", 0, match.start()) + 1)
    for key, locations in labels.items():
        if len(locations) > 1:
            refs = [masked.count("\n", 0, m.start()) + 1 for m in re.finditer(
                r"\\(?:ref|pageref|eqref)\s*\{" + re.escape(key) + r"\}", masked)]
            add("duplicate_label", "multiple-label-definitions", locations[0], locations=locations + refs)
    for row, number, line in errors:
        if "Option clash for package" in row or re.search(r"Command .* already defined", row):
            add("package_conflict", "package-or-command-conflict", line, number)
    if not items:
        add("unknown", "inspect-local-compiler-log")
    identity = digest(json_bytes([fingerprint, phase, code, [asdict(d) for d in items]]))
    return Diagnosis(identity, fingerprint, phase, code, tuple(items), bound=bound,
                     error_lines=(tuple(e[2] for e in errors[:1] if e[2] is not None)
                                  if any(d.category == "catcode" and d.confidence == "exact" for d in items)
                                  else tuple(e[2] for e in errors if e[2] is not None)))


# Private display-only event for bound XeLaTeX undefined-environment failures.
# This never enters Diagnosis.items/shareable()/error_lines/identity, never
# echoes raw log text, path, arbitrary exception, or suspected credential.
_ENV_NAME = re.compile(r"Environment ([A-Za-z0-9_*@:-]+) undefined\.")
_BEGIN_LINE = re.compile(r"l\.(\d{1,7})\s+(.*)")
_SENSITIVE = ("/Users", "sk-", "api_key", "SECRET_MANUSCRIPT")
_ENV_ACTIONS = {"imported", "admitted", "report_only", "ignored", "included"}


def _clean_hints(hints):
    cleaned = []
    for hint in hints:
        if not isinstance(hint, str) or any(mark in hint for mark in _SENSITIVE):
            continue
        cleaned.append(hint[:200])
        if len(cleaned) >= 4:
            break
    return tuple(cleaned)


def environment_hint(candidate, capture, *, planning_evidence=None):
    """Local-only, bounded hints for bound XeLaTeX failures. Never mutates shared state."""
    capture = capture or {}
    fingerprint = digest(candidate)
    failure = capture.get("failure")
    bound = (capture.get("candidate_sha256") == fingerprint and isinstance(failure, tuple)
             and len(failure) == 4 and isinstance(failure[3], bytes) and len(failure[3]) <= 2 * 1024 * 1024)
    if not bound:
        return ()
    _, step, code, raw = failure
    if step not in (1, 3, 4):
        return ()
    log = raw.decode("utf-8", errors="replace")
    lines = candidate.decode("utf-8", errors="replace").splitlines()
    hints = []
    # Environment names come from a fixed vocabulary pattern, never raw log text.
    names = []
    for row in log.splitlines():
        if not row.startswith("! "):
            continue
        for match in _ENV_NAME.finditer(row):
            if match.group(1) not in names:
                names.append(match.group(1))
    anchors = []
    for row in log.splitlines():
        if match := _BEGIN_LINE.fullmatch(row):
            anchors.append((int(match.group(1)), match.group(2).strip()))
    for name in names[:3]:
        anchor = next(((n, txt) for n, txt in anchors
                       if 1 <= n <= len(lines) and txt.startswith("\\begin{")), None)
        if anchor is None:
            hints.append(f"环境 {name} 未定义，行号未核实")
            continue
        n, txt = anchor
        inline = re.fullmatch(r"\\begin\{([^{}]+)\}(.*)", txt)
        begins = [i + 1 for i, line in enumerate(lines)
                  if re.search(r"\\begin\{" + re.escape(name) + r"\}", line)]
        ends = [i + 1 for i, line in enumerate(lines)
                if re.search(r"\\end\{" + re.escape(name) + r"\}", line)]
        if (inline is None or inline.group(1) != name or len(begins) != 1
                or len(ends) != 1 or begins[0] != n):
            hints.append(f"环境 {name} 未定义，行号未核实")
            continue
        hints.append(f"环境 {name} 未定义：已核实候选第 {n} 行")
    hints.append(f"XeLaTeX 第 {step} 步失败（退出码 {code}），候选哈希与本轮一致")
    if planning_evidence is None:
        hints.append("无本轮规划记录")
    elif (isinstance(planning_evidence, dict)
          and planning_evidence.get("candidate_sha256") == fingerprint):
        decisions = planning_evidence.get("decisions")
        summary = []
        for name in names[:3]:
            if isinstance(decisions, dict) and name in decisions:
                item = decisions[name]
                action = item.get("action") if isinstance(item, dict) else None
                summary.append(f"{name}({action})" if action in _ENV_ACTIONS else name)
            else:
                summary.append(name)
        hints.append("本轮规划匹配：" + (", ".join(summary) if summary else "本轮候选绑定一致"))
    else:
        hints.append("本轮规划未知或已过期")
    return _clean_hints(hints)
