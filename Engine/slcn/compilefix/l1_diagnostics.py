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
    # Private typed subject for rule dispatch / new-error judgment. Never emitted.
    subject: tuple[str, str] | None = None

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
    # Private subject: read from the raw bound error rows, never from a display
    # string, capture narrative or taskbook. Only the primary error frame counts.
    log_rows = log.splitlines()
    first = next((i for i, row in enumerate(log_rows) if row.startswith("! ")), len(log_rows))
    end = next((i for i in range(first + 1, len(log_rows)) if log_rows[i].startswith("! ")), len(log_rows))
    subject = _primary_subject(log_rows[first:end], text.splitlines())
    return Diagnosis(identity, fingerprint, phase, code, tuple(items), bound=bound,
                     subject=subject,
                     error_lines=(tuple(e[2] for e in errors[:1] if e[2] is not None)
                                  if any(d.category == "catcode" and d.confidence == "exact" for d in items)
                                  else tuple(e[2] for e in errors if e[2] is not None)))


# Private display-only event for bound XeLaTeX undefined-environment failures.
# This never enters Diagnosis.items/shareable()/error_lines/identity, never
# echoes raw log text, path, arbitrary exception, or suspected credential.
_ENV_NAME = re.compile(r"Environment ([A-Za-z0-9_*@:-]+) undefined\.")
_BEGIN_LINE = re.compile(r"l\.(\d{1,7})\s+(.*)")
_SENSITIVE = ("/Users", "sk-", "api_key", "SECRET_MANUSCRIPT")

# A2B-7: fixed, SAFE display vocabularies. A raw reason that is not in this
# mapping is NEVER echoed -- it degrades to the literal "原因未归类".
REASON_LABELS = {
    "imported": "迁入",
    "template_exists": "模板已有同名，未迁",
    "body_unused": "正文未用到，未迁",
    "kernel_exists": "内核已有，未迁",
    "overridden": "重定义覆盖提示，未迁",
    "converted_renew": "降级为 newcommand 后迁入",
    "closure_added": "依赖闭包引入",
    "seed_missed": "种子未命中，闭包待定",
    "name_conflict": "同类同名按口径取先",
    "source_unclear": "来源归属不清",
}
_ACTION_LABELS = {
    "imported": "迁入",
    "skipped": "跳过未迁",
    "overridden": "重定义覆盖提示，未迁",
    "converted_renew": "降级为 newcommand 后迁入",
    "closure_added": "依赖闭包引入",
    "seed_missed": "种子未命中，闭包待定",
}
_SOURCE_ROLE_LABELS = {
    "author_preamble": "作者前言",
    "local_support": "本地支撑文件",
    "expanded_input": "展开输入",
    "body": "正文",
    "metadata": "元数据",
    "skeleton_raw": "原始骨架",
    "skeleton_synced": "同步骨架",
    "style": "样式",
    "support": "支撑文件",
    "build_option": "构建选项",
    "normalization_param": "规范化参数",
}
_SAFE_NAME = re.compile(r"[A-Za-z0-9_*@:.-]{1,64}\Z")

# The migration ledger uses hashes, including for package names and TikZ keys.
# Only these labels and checked line numbers cross into the local display.
_MIGRATION_KINDS = {
    "package": "宏包报错", "environment": "未定义环境",
    "command": "未定义命令", "tikz_key": "未知 TikZ 键",
}
_MIGRATION_REASONS = REASON_LABELS | {
    "template_managed": "模板管理", "already_imported": "先前已迁入",
    "local_support": "本地支撑文件提供", "replaced": "模板替代",
    "conflict": "宏包冲突", "unrecognized": "形态未识别", "renamed": "已改名",
}
_CANDIDATE_STATES = {
    "present": "候选可见", "absent": "候选未见",
    "style_provided": "模板样式提供", "unknown": "候选状态未知",
}
_PGF_KEY_PREFIX = "! Package pgfkeys Error: I do not know the key '"
_TIKZ_KEY = re.compile(r"/tikz/([\w. :/+@*-]{1,128})'")
_END_CONTROL = re.compile(r"(?<!\\)(?:\\\\)*\\([A-Za-z@]{1,128})\s*$")


def _primary_subject(error_rows, candidate_lines):
    """Extract the primary diagnostic's typed identifier for an exact lookup."""
    if not error_rows:
        return None
    row = error_rows[0]
    if len(row) > 1024:
        return None
    if row.startswith(_PGF_KEY_PREFIX):
        # TeX hard-wraps at max_print_line, even in the middle of a key.
        subject = "".join(error_rows[:3]) if all(len(s) <= 1024 for s in error_rows[:3]) else row
        key = _TIKZ_KEY.match(subject, len(_PGF_KEY_PREFIX))
        if subject[len(_PGF_KEY_PREFIX):].startswith("/tikz/"):
            return "tikz_key", key[1] if key else ""
    if match := re.match(r"! Package ([A-Za-z0-9_.-]{1,128}) Error:", row):
        return "package", match[1]
    if row.startswith("! LaTeX Error: ") and (match := _ENV_NAME.fullmatch(row[15:])):
        return "environment", match[1]
    if row != "! Undefined control sequence.":
        return None
    # In a direct error, l.N stops at the offending token. In an expansion,
    # the first frame stops at that token; a later l.N points at its caller.
    name = ""
    frame = error_rows[1] if len(error_rows) > 1 else ""
    if len(frame) <= 1024:
        if direct := _BEGIN_LINE.fullmatch(frame):
            n = int(direct[1])
            context = direct[2].strip()
            if 1 <= n <= len(candidate_lines) and candidate_lines[n-1].lstrip().startswith(context):
                word = _END_CONTROL.search(context)
                name = word[1] if word else ""
        elif re.match(r"(?:<[a-z ]+>\s|\\[A-Za-z@]+\s*->)", frame):
            word = _END_CONTROL.search(frame)
            name = word[1] if word else ""
    return "command", name


def normalize_subject(kind, name):
    """Canonical (kind, name) for 'new error' judgment and dead-loop guard.

    Strips leading backslashes, the /tikz/ prefix for tikz_key, and collapses
    TeX hard-wrap whitespace inside a name. Location/line numbers are never
    an input here, so line drift cannot make a subject look new.
    """
    if not isinstance(name, str):
        return kind, name
    name = name.lstrip("\\")
    if kind == "tikz_key" and name.startswith("/tikz/"):
        name = name[len("/tikz/"):]
    name = re.sub(r"\s+", "", name)
    return kind, name


def _migration_hint(evidence, subject):
    if subject is None:
        return None
    kind, name = subject
    label = _MIGRATION_KINDS[kind]
    items = evidence.get("migration")
    if (not isinstance(items, list) or len(items) > 4096
            or evidence.get("migration_incomplete") is not False):
        return f"{label}，规划证据不完整，无法唯一定位"
    if not name:
        return f"{label}，未能核实出错名称"
    key = digest(name.encode("utf-8"))
    matches = [item for item in items if isinstance(item, dict) and item.get("kind") == kind
               and (item.get("name_sha256") == key or item.get("target_sha256") == key)]
    if not matches:
        return f"{label}，未找到对应决定"
    if len(matches) != 1:
        support = [item for item in matches if item.get("coordinate") == "support_file_line"
                   and isinstance(item.get("source_sha256"), str)
                   and re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"])]
        if len(support) == len(matches):
            locations = []
            for item in support[:3]:
                position = _num(item.get("position"))
                source = item["source_sha256"][:12]
                locations.append(f"第 {position} 行来源 {source}" if position else f"来源 {source}")
            return f"{label}，本地支撑文件有 {len(matches)} 条同名记录，无法唯一定位（{'；'.join(locations)}）"
        return f"{label}，匹配到 {len(matches)} 条规划记录，无法唯一定位"
    item = matches[0]

    def label_for(field, vocabulary, fallback):
        value = item.get(field)
        return vocabulary.get(value, fallback) if isinstance(value, str) else fallback

    role = label_for("source_role", _SOURCE_ROLE_LABELS, "来源不清")
    if item.get("coordinate") == "support_file_line":
        role = "本地支撑文件"
    position = _num(item.get("position"))
    location = f"第 {position} 行" if position else ""
    action = label_for("action", _ACTION_LABELS, "动作未归类")
    reason = label_for("reason", _MIGRATION_REASONS, "原因未归类")
    state = label_for("candidate_state", _CANDIDATE_STATES, "候选状态未知")
    order = "，相对位置已调整" if item.get("reordered") is True else ""
    return f"{label}，对应{role}{location}，决定{action}，理由{reason}，{state}{order}"


def _clean_hints(hints):
    cleaned = []
    for hint in hints:
        if not isinstance(hint, str) or any(mark in hint for mark in _SENSITIVE):
            continue
        cleaned.append(hint[:200])
        if len(cleaned) >= 4:
            break
    return tuple(cleaned)


def _safe_name(name):
    """Environment/declaration name from a fixed safe vocabulary, never free text."""
    return name if isinstance(name, str) and _SAFE_NAME.fullmatch(name) else ""


def _num(value):
    return value if type(value) is int and 0 <= value <= 1000000 else None


def _planning_hints(planning_evidence, names):
    """Render a bound, dict-shaped planning record using fixed labels only.

    No raw reason string, source id, path or free text from the evidence is ever
    echoed: every action/reason/role is mapped through a fixed SAFE vocabulary
    and unknown values degrade to a fixed Chinese placeholder. The record was
    captured by the planner in an earlier round -- it is presented as recorded,
    NOT re-verified now (no claim that source files are still unchanged).
    """
    decisions = planning_evidence.get("decisions")
    decisions = decisions if isinstance(decisions, dict) else {}
    counts = planning_evidence.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    declarations = _num(counts.get("declarations_seen"))
    lines = []
    if declarations is not None:
        lines.append(f"规划时观察到声明 {declarations} 个")
    seen = set()
    for name in names[:3]:
        safe = _safe_name(name)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        item = decisions.get(name)
        if not isinstance(item, dict):
            item = {}
        role = _SOURCE_ROLE_LABELS.get(item.get("source_role"), "来源不清")
        action = _ACTION_LABELS.get(item.get("action"), "动作未归类")
        reason = REASON_LABELS.get(item.get("reason"), "原因未归类")
        lines.append(f"{safe} 来源{role}，决定{action}，理由{reason}")
    if not lines:
        lines.append("本轮候选绑定一致")
    if planning_evidence.get("incomplete") is True:
        lines.append("证据不完整")
    return lines


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
    if type(step) is not int or step not in (1, 3, 4) or type(code) is not int:
        return ()
    log_rows = raw.decode("utf-8", errors="replace").splitlines()
    first = next((i for i, row in enumerate(log_rows) if row.startswith("! ")), len(log_rows))
    end = next((i for i in range(first + 1, len(log_rows)) if log_rows[i].startswith("! ")), len(log_rows))
    error_rows = log_rows[first:end]
    lines = candidate.decode("utf-8", errors="replace").splitlines()
    hints = []
    subject = _primary_subject(error_rows, lines)
    # Preserve the legacy environment observations across the captured log. The
    # migration lookup below remains bound to the primary error only.
    names = []
    for row in log_rows:
        if not row.startswith("! "):
            continue
        for match in _ENV_NAME.finditer(row):
            if match[1] not in names:
                names.append(match[1])
    anchors = []
    for row in error_rows:
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
        # A2B-7: bound + dict-shaped -> describe what the planner OBSERVED.
        # Values are mapped through SAFE vocabularies only (no raw echo) and
        # this is a recorded observation, not a re-check of source files now.
        try:
            if "migration" in planning_evidence:
                # The new ledger preserves both namespaces and repeated names;
                # looking in the legacy name-only dict could pick a wrong winner.
                details = _planning_hints(planning_evidence, [])
                migration_hint = _migration_hint(planning_evidence, subject)
                if migration_hint:
                    details.insert(0, migration_hint)
            else:
                details = _planning_hints(planning_evidence, names)
        except Exception:
            details = []
        hints.append("本轮规划时观察到：" + ("；".join(details) if details else "本轮候选绑定一致"))
    else:
        # Includes 2-tuples such as (None, evidence): a non-dict is treated as
        # no/stale record, never as evidence.
        hints.append("本轮规划未知或已过期")
    return _clean_hints(hints)
