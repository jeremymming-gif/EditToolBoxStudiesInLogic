"""Stable task and reply schemas, with local-only source anchoring."""

import difflib
import re
import unicodedata

from .files import RepairError, decode_json, digest, json_bytes


SCHEMA_VERSION = 1
PROMPT_VERSION = 1
MAX_EDITS = 3
MAX_CHARACTERS = 512
MAX_LINE_DELTA = 4
MAX_DIAGNOSTICS = 3
MAX_SUMMARY = 160
MAX_REASON = 300
MAX_ACTIONS = 6

_DOCUMENT_START = re.compile(r"(?m)^[ \t]*\\begin\{document\}[ \t]*\r?$")
_DOCUMENT_END = re.compile(r"(?m)^[ \t]*\\end\{document\}[ \t]*\r?$")
_LEGACY_PHASE = re.compile(r"^(xelatex|biber) 第 (\d+) 步失败（退出码 (-?\d+)）")
_LOG_ERROR = re.compile(r"^(.+?\.tex):(\d+):\s*(\S.*)$", re.IGNORECASE)
_LOG_ANCHOR = re.compile(r"^l\.(\d+)\s+(\S.*)$")
_FIXABLE = (
    ("Misplaced alignment tab character &", "Misplaced alignment tab character &.", "&"),
    ("Missing $ inserted", "Missing math-mode delimiter.", "_"),
)
_MANUAL = (
    ("undefined control sequence", "Unresolved TeX control sequence; inspect macros and the source."),
    ("not found", "A TeX dependency is missing."),
    ("font", "TeX font or toolchain diagnostic."),
    ("bib", "Bibliography or Biber diagnostic."),
    ("emergency stop", "TeX stopped without a usable body anchor."),
)
_SENSITIVE = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[\w-]+|(?:[A-Za-z]:[\\/]|[\\]{2}|~/|https?://|"
    r"(?<![\w\\/])/(?:[\w.~%-]+/)*[\w.~%-]+)|"
    r"(?i:\b(?:api[_ -]?key|access[_ -]?token|authorization|password|secret|bearer)\s*[:= ]\s*\S+|"
    r"\b(?:sk|ghp|github_pat)[-_][a-z0-9_-]{8,}))"
)
_STRUCTURED_SECRET = re.compile(
    r"\\(?:title|author|affiliation|institute|institution|email|address|thanks|keywords|"
    r"ctitle|etitle|cauthor|eauthor|caddress|eaddress)(?:\[[^\]\r\n]*\])?\s*\{([^{}\r\n]+)\}",
    re.IGNORECASE,
)
_UNSAFE_SOURCE_CONTEXT = re.compile(
    r"\\(?:catcode|mathcode|lccode|uccode|endlinechar|escapechar|scantokens|"
    r"verb|csname|ifcase|ifcat|afterassignment|aftergroup|obeylines|obeyspaces)\b|\^\^"
)
_LITERAL_ESCAPES = frozenset(r"&%_#$")
_PLAIN_PUNCTUATION = frozenset(" .,!?…:;-'\"()，。！？；：、‘’“”（）–—")


def text_candidate(data):
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise RepairError("候选必须是 UTF-8 TeX") from exc
    if re.search(r"\r(?!\n)|\0", text):
        raise RepairError("候选含不受支持的控制字符或换行")
    return text


def _body_bounds(text):
    starts = list(_DOCUMENT_START.finditer(text))
    ends = list(_DOCUMENT_END.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
        return None
    return starts[0].end(), ends[0].start()


def _wordless_excerpt(line):
    # Preview only structural tokens. Log prose and ordinary TeX words may
    # contain names even when they are neither tagged nor pattern-matchable.
    line = re.split(r"(?<!\\)%", line, maxsplit=1)[0]
    tokens = re.findall(r"(?<!\\)[&_\$]", line)
    return " ".join(tokens[:8])


def _diagnostic(error, source, log_line, phase, tool, exit_code, lines, body):
    classification, summary = "unknown", "Unrecognized compiler diagnostic; inspect the local log."
    if tool == "biber":
        classification, summary = "manual", "Biber failed; inspect local bibliography inputs and logs."
    elif tool == "environment":
        classification, summary = "manual", "TeX toolchain is unavailable or returned no usable output."
    else:
        for prefix, safe_summary, token in _FIXABLE:
            if error.startswith(prefix):
                summary = safe_summary
                classification = "manual"
                if source in body and token in lines[source - 1]:
                    classification = "editable"
                break
        else:
            for marker, safe_summary in _MANUAL:
                if marker in error.casefold():
                    classification, summary = "manual", safe_summary
                    break
    return {"classification": classification, "phase": phase, "tool": tool,
            "exit_code": exit_code, "summary": summary[:MAX_SUMMARY],
            "source_line": source, "log_line": log_line}


def diagnostics(candidate, log, stem, message=""):
    text = text_candidate(candidate)
    lines = text.split("\n")
    bounds = _body_bounds(text)
    body = range(0)
    if bounds:
        body = range(text.count("\n", 0, bounds[0]) + 2, text.count("\n", 0, bounds[1]) + 1)
    tool, phase, code = "xelatex", "compile", None
    if match := _LEGACY_PHASE.match(message):
        tool, step, raw_code = match.groups()
        phase, code = f"compile-{step}", int(raw_code)
    elif message.startswith(("找不到命令", "编译结束但未生成 PDF")):
        tool = "environment"
    results = []
    if tool == "xelatex" and log:
        log_lines = log.decode("utf-8", errors="replace").splitlines()
        for index, line in enumerate(log_lines):
            if match := _LOG_ERROR.match(line):
                filename, anchor, error = match.groups()
                if filename not in {f"{stem}.tex", f"./{stem}.tex"}:
                    continue
                source = int(anchor) if len(anchor) <= 7 else None
                source = source if source is not None and 0 < source <= len(lines) else None
            elif line.startswith("! "):
                source, error = None, line[2:]
                for context in log_lines[index + 1:index + 9]:
                    if context.startswith("! ") or _LOG_ERROR.match(context):
                        break
                    anchor = _LOG_ANCHOR.match(context)
                    if anchor and len(anchor[1]) <= 7:
                        number = int(anchor[1])
                        if 0 < number <= len(lines) and lines[number - 1].lstrip().startswith(anchor[2]):
                            source = number
                        break
            else:
                continue
            results.append(_diagnostic(error, source, index + 1, phase, tool, code, lines, body))
            if len(results) == MAX_DIAGNOSTICS:
                break
    elif tool == "biber" and log:
        for index, line in enumerate(log.decode("utf-8", errors="replace").splitlines(), 1):
            if re.search(r"\b(?:ERROR|FATAL)\s+-", line):
                results.append(_diagnostic("", None, index, phase, tool, code, lines, body))
                if len(results) == MAX_DIAGNOSTICS:
                    break
    if not results:
        results.append(_diagnostic("", None, None, phase, tool, code, lines, body))
    return results


def taskbook(target_key, candidate, summaries, task_id=None):
    candidate_hash = digest(candidate)
    diagnostic_hash = digest(json_bytes(summaries))
    identity = {
        "candidate_sha256": candidate_hash, "diagnostic_sha256": diagnostic_hash,
        "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
    }
    identity["task_id"] = task_id or digest(json_bytes([target_key, identity]))
    lines = text_candidate(candidate).split("\n")
    evidence = []
    editable = False
    for index, diagnostic in enumerate(summaries):
        number = diagnostic["source_line"]
        entry = {"evidence_id": f"e{index + 1}", "diagnostic_index": index, "source_line": number}
        if diagnostic["classification"] == "editable":
            editable = True
            if excerpt := _wordless_excerpt(lines[number - 1]):
                entry["excerpt"] = excerpt
        evidence.append(entry)
    return {
        "identity": identity, "diagnostics": summaries, "evidence": evidence,
        "actions": ["edit", "report_only"] if editable else ["report_only"],
        "instructions": ("Echo identity exactly. Return JSON with identity and actions. "
                         "Use a signed evidence_id. An edit has action, evidence_id, old, new, "
                         "before and after. Use exact local anchors when supplied by the editor. "
                         "A report_only item has action, evidence_id and reason."),
    }


def _shareable_text(value, private_values):
    return (type(value) is str and not _SENSITIVE.search(value)
            and not any(secret in value for secret in private_values)
            and not any(unicodedata.category(char) in {"Cc", "Cs", "Cf"}
                        and char not in "\r\n\t" for char in value))


def parse_reply(raw, book, candidate):
    reply = decode_json(raw)
    if type(reply) is not dict or set(reply) != {"identity", "actions"}:
        raise RepairError("回件顶层 schema 不符")
    supplied = reply["identity"]
    identity = book["identity"]
    if (type(supplied) is not dict or set(supplied) != set(identity)
            or any(type(supplied[key]) is not type(value) or supplied[key] != value
                   for key, value in identity.items())):
        raise RepairError("回件任务身份或版本不符")
    actions = reply["actions"]
    if type(actions) is not list or not 1 <= len(actions) <= MAX_ACTIONS:
        raise RepairError("回件动作数无效")
    evidence = {entry["evidence_id"]: entry for entry in book["evidence"]}
    private_values = set(_STRUCTURED_SECRET.findall(text_candidate(candidate)))
    for item in actions:
        if type(item) is not dict or type(item.get("evidence_id")) is not str:
            raise RepairError("动作缺少证据 ID")
        entry = evidence.get(item["evidence_id"])
        if entry is None or item.get("action") not in book["actions"]:
            raise RepairError("动作或证据未由任务书签发")
        diagnostic = book["diagnostics"][entry["diagnostic_index"]]
        if item["action"] == "report_only":
            if (set(item) != {"action", "evidence_id", "reason"}
                    or not _shareable_text(item.get("reason"), private_values)
                    or not 1 <= len(item["reason"].strip()) <= MAX_REASON):
                raise RepairError("报告动作仅接受证据 ID 和安全原因")
        elif (set(item) != {"action", "evidence_id", "old", "new", "before", "after"}
              or diagnostic["classification"] != "editable"
              or not all(_shareable_text(item.get(key), private_values)
                         for key in ("old", "new", "before", "after"))):
            raise RepairError("建议超出回件 schema 或可编辑证据范围")
    return actions


def _plain_lines(text, *, original=False):
    if re.search(r"\r(?!\n)", text):
        return False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text) or text[index + 1] not in _LITERAL_ESCAPES:
                return False
            index += 2
            continue
        if not (char in _PLAIN_PUNCTUATION or char in "\t\r\n"
                or unicodedata.category(char)[0] in {"L", "M", "N"}
                or (original and char in "&_$")):
            return False
        index += 1
    return True


def _check_line_context(text, start, end, replacement):
    first = text.rfind("\n", 0, start) + 1
    stop = text.find("\n", end)
    stop = len(text) if stop < 0 else stop
    old_lines = text[first:stop]
    new_lines = text[first:start] + replacement + text[end:stop]
    if (not _plain_lines(old_lines, original=True) or not _plain_lines(replacement)
            or not _plain_lines(new_lines, original=True)):
        raise RepairError("变更涉及无法可靠分类的 TeX 语法")


def edit_candidate(candidate, book, actions):
    if digest(candidate) != book["identity"]["candidate_sha256"]:
        raise RepairError("候选哈希已变化")
    evidence = {entry["evidence_id"]: entry for entry in book["evidence"]}
    edits = [item for item in actions if item["action"] == "edit"]
    if not edits:
        return candidate, b""
    text = text_candidate(candidate)
    bounds = _body_bounds(text)
    if bounds is None or _UNSAFE_SOURCE_CONTEXT.search(text):
        raise RepairError("正文边界或 TeX 词法环境无法可靠确认")
    count = sum(len(item["old"]) + len(item["new"]) for item in edits)
    deltas = [item["new"].count("\n") - item["old"].count("\n") for item in edits]
    if (len(edits) > MAX_EDITS or count > MAX_CHARACTERS or count * 10 > len(text)
            or sum(max(delta, 0) for delta in deltas) > MAX_LINE_DELTA
            or sum(max(-delta, 0) for delta in deltas) > MAX_LINE_DELTA):
        raise RepairError("替换项、字符比例或增删行超过单轮上限")
    spans = []
    for item in edits:
        old, new = item["old"], item["new"]
        if not old or old == new:
            raise RepairError("旧片段为空或建议没有变化")
        start = text.find(old)
        if start < 0 or text.find(old, start + 1) >= 0:
            raise RepairError("旧片段在冻结候选中未唯一命中")
        end = start + len(old)
        before, after = item["before"], item["after"]
        if (start - len(before) < bounds[0] or end + len(after) > bounds[1]
                or text[start - len(before):start] != before or text[end:end + len(after)] != after):
            raise RepairError("上下文锚点不匹配或超出正文")
        anchor = evidence[item["evidence_id"]]["source_line"]
        if not text.count("\n", 0, start) + 1 <= anchor <= text.count("\n", 0, end - 1) + 1:
            raise RepairError("替换位置不在签发的诊断锚点")
        _check_line_context(text, start, end, new)
        spans.append((start, end, new))
    spans.sort()
    if any(previous[1] > next_span[0] for previous, next_span in zip(spans, spans[1:])):
        raise RepairError("替换区间交叠")
    patched = text
    for start, end, new in reversed(spans):
        patched = patched[:start] + new + patched[end:]
    diff = "".join(difflib.unified_diff(text.splitlines(keepends=True), patched.splitlines(keepends=True),
                                        fromfile="a/candidate.tex", tofile="b/candidate.tex"))
    if not diff or _body_bounds(patched) is None:
        raise RepairError("无法生成可审阅的正文差异")
    return patched.encode("utf-8"), diff.encode("utf-8")
