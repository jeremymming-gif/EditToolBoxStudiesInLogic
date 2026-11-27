"""Default-on deterministic failure adapter, with no AI channel or settings."""
import json
from pathlib import Path
import re
import sys

import subprocess
import tempfile
import uuid

from .files import RepairError, digest, directory, read, regular
from .l1_diagnostics import diagnose, environment_hint, mask_comments, normalize_subject
from .environment_migration_rule import (EnvironmentEditPlan,
                                         build_context, select_environment_plan)
from .migration_rule_family import select_macro_plan, select_tikzkey_plan
from .l1_rules import CATCODE, select_plan
from .l1_transaction import RecoveryRequired, apply_plan, recover


def template_results(candidate, out_dir, stem):
    """Reuse T1--T8 checks; refuse external/macro/symlink file references first."""
    import templatecheck as tc
    from ..templates import TPL_DIR
    from .compiler import _STYLES
    text = candidate.decode("utf-8", errors="strict")
    masked = tc.raw_mask(text)
    names = [name for name in tc._usepackage_names(masked) if name + ".sty" in _STYLES]
    name = names[0] if len(names) == 1 else ""
    canonical = str(Path(TPL_DIR) / (name + ".sty")) if name else ""
    ctx = tc.Context(str(out_dir / (stem + ".tex")), str(out_dir), text, masked, name,
                     canonical, tc.guess_lang(text, name), tc.parse_metadata(text, masked))

    def safe_targets(pattern, extensions):
        for match in re.finditer(pattern, masked):
            bounds = tc.group_at(masked, match.end())
            if bounds is None:
                return False
            for target in text[bounds[0]:bounds[1]].split(","):
                target = target.strip()
                if not target or not re.fullmatch(r"[A-Za-z0-9_ ./-]+", target):
                    return False
                parts = Path(target).parts
                if Path(target).is_absolute() or any(p in {"..", "."} or p.startswith(".") for p in parts):
                    return False
                for ext in extensions:
                    path = out_dir / (target + ext)
                    try:
                        directory(path.parent)
                        regular(path, optional=True)
                    except FileNotFoundError:
                        pass
                    except (OSError, RepairError):
                        return False
        return True

    safe_bib = safe_targets(r"\\(?:addbibresource|bibliography)\s*", ("", ".bib"))
    safe_image = safe_targets(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*",
                              ("", *tc._IMG_EXT))
    safe_style = True
    if name:
        try:
            regular(out_dir / (name + ".sty"), optional=True)
        except (OSError, RepairError):
            safe_style = False
    results = []
    for index, check in enumerate(tc.CHECKS, 1):
        if (index == 4 and not safe_bib or index == 7 and not safe_style or index == 8 and not safe_image):
            results.append({"rule": f"T{index}", "status": "SKIP", "lines": []})
            continue
        result = check(ctx)
        results.append({"rule": f"T{index}", "status": result.status,
                        "lines": [f.line for f in result.findings]})
    return tuple(results)


def compile_with_l1(compile_pdf, out_dir, stem, *, source, enabled=True, planning_evidence=None):
    """Separate opt-out for internal staging; ordinary CLI/R3 attempt the whitelist.

    ``planning_evidence`` is an optional, display-only local record. It never
    enters capture/identity/taskbook and never aborts recovery, compilation,
    admission or export. Batch 2A callers pass nothing (``None``).
    """
    from . import compile_with_capture
    if enabled:
        # A2A-1/A2A-2: recovery keeps its own independent stage record; a
        # display failure must only drop the hint, never abort the real flow.
        recovery_log = []
        try:
            recover(out_dir, stem, source=source, stage_log=recovery_log)
        except (OSError, RepairError):
            try:
                if recovery_log:
                    print("   L1 恢复未完成，失败阶段：" + recovery_log[-1], file=sys.stderr)
            except Exception:
                pass
            return False, "L1 恢复未完成；请人工检查受限事务材料", None
    ok, info, capture = compile_with_capture(compile_pdf, out_dir, stem, source=source)
    if ok or not enabled:
        return ok, info, capture
    try:
        path = directory(out_dir)
        candidate = read(path / f"{stem}.tex")
        try:
            checks = template_results(candidate, path, stem)
        except (OSError, RepairError, ValueError, UnicodeError):
            checks = ()
        try:
            from .compiler import support_manifest
            available = set(support_manifest(path, stem)) | {stem + ".tex"}
        except (OSError, RepairError):
            available = None
        report = diagnose(candidate, capture, template_results=checks, available_paths=available)
        plan = select_plan(candidate, report)
        env_attempted = False
        if plan is None and capture is not None:
            # L1.5: only after the CATCODE rule declines do we consider the
            # narrow environment-migration rule.  Its private input context is
            # built from the live, source-bound inputs of THIS round (the same
            # ``source`` that capture_compile binds via source_id), never from
            # display strings, capture narratives or a taskbook.
            try:
                from .l1_rules import ENV_MIGRATION
                from .environment_migration_rule import build_context, select_environment_plan
                env_attempted = True
                env_context = build_context(path, stem, source, capture)
                plan = select_environment_plan(candidate, report, context=env_context,
                                               capture=capture)
            except Exception:
                # FINDING 2: an unexpected exception from the NEW calls must not
                # escape to the outer handler (which would drop all not-admitted
                # accounting and the full failure JSON). KeyboardInterrupt is
                # deliberately not caught.
                plan = None
            if plan is None and env_attempted:
                # FINDING 3: attribute the env-path rejection to the env rule;
                # the CATCODE not-admitted attempt below stays intact.
                report.attempts.append({"rule": ENV_MIGRATION.rule_id,
                                        "result": "not-admitted"})
        if plan is None or capture is None:
            report.attempts.append({"rule": "catcode-top-level-at-v1", "result": "not-admitted"})
        else:
            try:
                pdf = apply_plan(path, stem, source=source, capture=capture, plan=plan, report=report)
            except RecoveryRequired:
                report.attempts.append({"rule": plan.rule_id, "result": "recovery-required"})
                info = "L1 回滚未完成，禁止交付；请人工检查受限事务材料"
                # A2A-2: this post-publish rollback refusal has independent
                # semantics; keep its own visible notice (never "编译尚未启动").
                try:
                    print("   L1 回滚未完成，禁止交付；请人工检查受限事务材料", file=sys.stderr)
                except Exception:
                    pass
            except (Exception, KeyboardInterrupt):
                report.attempts.append({"rule": plan.rule_id, "result": "failed-rolled-back"})
                from .state import capture_compile
                rebound = capture_compile(path, stem, source=source)
                if (rebound is not None and rebound["candidate_sha256"] == capture["candidate_sha256"]
                        and rebound["source_id"] == capture["source_id"] and rebound["out_id"] == capture["out_id"]):
                    capture.update(rebound)
            else:
                report.attempts.append({"rule": plan.rule_id, "result": "passed"})
                capture["l1"] = report.shareable()
                capture["l1_repaired"] = plan.after_sha256
                print("   L1 编译通过、局部不变量与交付事务通过；PDF 版面仍待人工检查。")
                return True, pdf, capture
        if capture is not None:
            capture["l1"] = report.shareable()
        print("   L1 未修复：" + json.dumps(report.shareable(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        # A2A-1/A2A-3: local display only. A failure here may only drop the
        # extra hint; it must never abort the existing failure contract.
        try:
            for hint in environment_hint(candidate, capture, planning_evidence=planning_evidence):
                print("   " + hint, file=sys.stderr)
        except Exception:
            pass
    except Exception:
        print("   L1 未修复：诊断条件不足，请人工检查本地日志。", file=sys.stderr)
    return False, info, capture


# ── Task 5: multi-round staged repair loop (one publish, ≤3 rounds) ──────────
# Wiring decision: ``compile_with_l1`` above stays the unchanged single-round
# driver (byte-identical attempts/shareable for its callers).  This loop is an
# internal opt-in; with ``max_rounds=1`` it reproduces the frozen one-round
# shareable shape (one flat ``{"rule","result"}`` entry, no round number).
_STAGED_ROUND_TIMEOUT = 90


def _staged_lines(plan):
    """Private declaration lines carried by an admitted one-edit plan."""
    lines = getattr(plan, "lines", None)
    if lines:
        return list(lines)
    declaration = getattr(plan, "declaration", None)
    if declaration:
        return [declaration]
    return None


def _staged_insert(original, patched, preferred=None):
    """Recover the single continuous inserted slice by exact forward/reverse.

    Common-prefix-only or forward-plus-backward matching can overshoot into the
    inserted block when the block shares leading/trailing bytes with the text at
    the insertion point (as preamble declarations do). Prefer the offset already
    proved by the first selector, then fall back to the smallest offset whose
    removal restores the original byte-for-byte — the same proof the selectors
    use. Smallest wins over a shifted equivalent slice so the added bytes stay
    the exact accumulated declaration block.
    """
    added_len = len(patched) - len(original)
    if added_len <= 0:
        raise RepairError("L1 多轮暂存不是单一连续插入切片")
    if preferred is not None and 0 <= preferred <= len(patched) - added_len:
        if patched[:preferred] + patched[preferred + added_len:] == original:
            return preferred, patched[preferred:preferred + added_len]
    prefix = 0
    while (prefix < min(len(original), len(patched))
           and original[prefix] == patched[prefix]):
        prefix += 1
    low = max(0, prefix - added_len - 1)
    for offset in range(low, prefix + 1):
        if patched[:offset] + patched[offset + added_len:] == original:
            return offset, patched[offset:offset + added_len]
    raise RepairError("L1 多轮暂存不是单一连续插入切片")


def _stage_dry_compile(out_dir, stem, candidate, capture, *, private_dir):
    """REAL XeLaTeX in a private dir; returns (code, round_capture, raw)."""
    from .compiler import _environment, copy_support, support_manifest
    work = Path(private_dir) / "candidate"
    home = Path(private_dir) / "home"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    try:
        manifest = support_manifest(out_dir, stem)
        copy_support(out_dir, work, manifest)
    except (OSError, RepairError):
        manifest = {}
    (work / f"{stem}.tex").write_bytes(candidate)
    proc = subprocess.run(
        ["xelatex", "-no-shell-escape", "-interaction=nonstopmode", f"{stem}.tex"],
        cwd=work, env=_environment(home), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=_STAGED_ROUND_TIMEOUT,
    )
    raw = proc.stdout[:2 * 1024 * 1024]
    round_capture = {key: capture[key] for key in
                     ("candidate_sha256", "candidate_id", "source_id", "out_id")
                     if key in capture}
    round_capture["candidate_sha256"] = digest(candidate)
    if proc.returncode:
        round_capture["failure"] = (uuid.uuid4().hex, 1, proc.returncode, raw)
    return proc.returncode, round_capture, raw, set(manifest) | {f"{stem}.tex"}


def _stage_attempt(report, rule_id, result):
    """Flat, private-round-free attempt row (frozen shareable shape)."""
    report.attempts.append({"rule": rule_id, "result": result})


def _repeat_key(subject, acc):
    """Flat private death-loop key: (kind, normalized name, acc digest)."""
    if not subject:
        return None
    kind, name = normalize_subject(*subject)
    return (kind, name, digest(acc))


def _stage_publish(out_dir, stem, *, source, capture, report, plan):
    """The ONE publish call with the four attempt states.

    Shared by the CATCODE direct-publish path and the combined-slice path so
    both use exactly one ``apply_plan`` call and identical rollback semantics.
    Returns ``(ok, info)``; a successful publish fills capture["l1"] and
    capture["l1_repaired"]. Never swallows KeyboardInterrupt.
    """
    info = "L1 未修复：多轮暂存未收敛；请人工检查本地日志。"
    try:
        pdf = apply_plan(out_dir, stem, source=source, capture=capture,
                         plan=plan, report=report)
    except RecoveryRequired:
        _stage_attempt(report, plan.rule_id, "recovery-required")
        info = "L1 回滚未完成，禁止交付；请人工检查受限事务材料"
        try:
            print("   " + info, file=sys.stderr)
        except Exception:
            pass
        return False, info
    except Exception:
        _stage_attempt(report, plan.rule_id, "failed-rolled-back")
        from .state import capture_compile
        rebound = capture_compile(out_dir, stem, source=source)
        if (rebound is not None
                and rebound.get("candidate_sha256") == capture.get("candidate_sha256")
                and rebound.get("source_id") == capture.get("source_id")
                and rebound.get("out_id") == capture.get("out_id")):
            capture.update(rebound)
        return False, info
    _stage_attempt(report, plan.rule_id, "passed")
    capture["l1"] = report.shareable()
    capture["l1_repaired"] = plan.after_sha256
    return True, pdf


def run_staged_repair_loop(out_dir, stem, *, source, capture, candidate, report, max_rounds=3):
    """Stage missing definitions in memory across real XeLaTeX dry compiles.

    ``apply_plan`` publishes only when its isolated recompile passes, so an
    intermediate failed patch can never be the next round's baseline.  Every
    round therefore dry-compiles the accumulated candidate in a private dir,
    carries the accumulated lines forward in memory, and only the converged
    candidate is handed to ONE ``apply_plan`` call as one continuous insert.
    Round numbers and the typed subject are private: only flat
    ``{"rule","result"}`` rows reach ``report.shareable()``.
    """
    from .. import preamble as PRE

    out_dir = directory(out_dir)
    original = candidate
    original_text = original.decode("utf-8", errors="strict")
    staged_lines = []
    acc = original
    seen = set()
    previous_subject = None
    last_plan = None
    anchor_offset = None
    converged = False
    rounds = max(0, min(int(max_rounds), 3))
    info = "L1 未修复：多轮暂存未收敛；请人工检查本地日志。"

    try:
        with tempfile.TemporaryDirectory(prefix="compilefix-staged-") as private:
            for _round in range(rounds):
                try:
                    code, round_capture, raw, available = _stage_dry_compile(
                        out_dir, stem, acc, capture, private_dir=private)
                except (Exception,):
                    _stage_attempt(report, CATCODE.rule_id, "dry-compile-failed")
                    break
                if code == 0:
                    converged = True
                    break

                try:
                    round_report = diagnose(acc, round_capture, available_paths=available)
                except Exception:
                    _stage_attempt(report, CATCODE.rule_id, "not-admitted")
                    break

                subject = round_report.subject
                # FLAT (kind, normalized name), used by the no-new-subject
                # comparison. ``normalize_subject`` already returns (kind, name).
                normalized = normalize_subject(*subject) if subject else None
                # Death-loop key: FLAT (kind, normalized name, acc digest).
                # Line-number drift changes neither component, so it is not new.
                key = _repeat_key(subject, acc)
                if key is not None and key in seen:
                    _stage_attempt(report, CATCODE.rule_id, "stopped-repeat")
                    break
                if key is not None:
                    seen.add(key)

                plan = None
                try:
                    plan = select_plan(acc, round_report)
                except Exception:
                    plan = None
                if plan is not None:
                    # CATCODE is a two-wrapper byte-span edit (prefix/suffix),
                    # NOT a preamble line, so it cannot be folded into the
                    # single-continuous-slice combined plan below (that would
                    # break reverse()). Publish this exact plan directly with
                    # exactly one apply_plan call and the same four states.
                    ok, published = _stage_publish(
                        out_dir, stem, source=source, capture=capture,
                        report=report, plan=plan)
                    if ok:
                        print("   L1 多轮暂存修复通过、单次发布完成；"
                              "PDF 版面仍待人工检查。")
                        return True, published, capture
                    if published != info:
                        info = published  # RecoveryRequired carries its own message.
                    capture["l1"] = report.shareable()
                    return False, info, capture

                context = None
                try:
                    context = build_context(out_dir, stem, source, capture)
                except Exception:
                    context = None
                if context is not None:
                    for selector in (select_environment_plan, select_macro_plan,
                                     select_tikzkey_plan):
                        try:
                            plan = selector(acc, round_report, context=context,
                                            capture=round_capture)
                        except Exception:
                            plan = None
                        if plan is not None:
                            break
                if plan is None:
                    _stage_attempt(report, CATCODE.rule_id, "not-admitted")
                    if previous_subject is not None and normalized == previous_subject:
                        _stage_attempt(report, CATCODE.rule_id, "stopped-no-new-subject")
                        break
                    previous_subject = normalized
                    continue

                lines = _staged_lines(plan)
                if not lines:
                    _stage_attempt(report, plan.rule_id, "not-admitted")
                    break
                # Remember the insertion offset proved on the ORIGINAL candidate:
                # later selectors' offsets are relative to an accumulated candidate,
                # so only the first one anchors the final continuous slice.
                if anchor_offset is None:
                    anchor_offset = getattr(plan, "offset", None)
                staged_lines.extend(lines)
                acc = PRE.insert_lines(original_text, staged_lines, "").encode("utf-8")
                last_plan = plan
                previous_subject = normalized
                _stage_attempt(report, plan.rule_id, "staged")

        if converged:
            if last_plan is None or acc == original:
                _stage_attempt(report, CATCODE.rule_id, "passed")
                capture["l1"] = report.shareable()
                return True, info, capture
            offset, added = _staged_insert(original, acc, preferred=anchor_offset)
            if isinstance(last_plan, EnvironmentEditPlan):
                combined = EnvironmentEditPlan(
                    identity=last_plan.identity, rule_id=last_plan.rule_id,
                    version=last_plan.version,
                    diagnosis_identity=last_plan.diagnosis_identity,
                    diagnosis_candidate_sha256=last_plan.diagnosis_candidate_sha256,
                    context_fingerprint=last_plan.context_fingerprint,
                    env_name=last_plan.env_name, target_binding=last_plan.target_binding,
                    declaration=last_plan.declaration, separator=last_plan.separator,
                    offset=offset, added_bytes=added, before_sha256=digest(original),
                    after_sha256=digest(acc), original=original, patched=acc,
                )
            else:
                from .l1_rules import EditPlan
                combined = EditPlan(
                    identity=last_plan.identity, rule_id=last_plan.rule_id,
                    before_sha256=digest(original), after_sha256=digest(acc),
                    start=offset, end=offset + len(added), prefix=b"", suffix=b"",
                    original=original, patched=acc,
                )
            if combined.reverse(acc) != original:
                raise RepairError("L1 多轮合并补丁不可逆")
            ok, published = _stage_publish(
                out_dir, stem, source=source, capture=capture,
                report=report, plan=combined)
            if ok:
                print("   L1 多轮暂存修复通过、单次发布完成；PDF 版面仍待人工检查。")
                return True, published, capture
            if published != info:
                info = published  # RecoveryRequired carries its own message.
    except (Exception,):
        _stage_attempt(report, CATCODE.rule_id, "not-admitted")

    capture["l1"] = report.shareable()
    print("   L1 多轮暂存未修复：" + json.dumps(report.shareable(), ensure_ascii=False,
                                          sort_keys=True), file=sys.stderr)
    return False, info, capture


def staged_repair_after_failure(out_dir, stem, *, source, capture,
                                 max_rounds=3) -> tuple[bool, str, dict | None]:
    """After a failed single-round compile, run the A+C staged repair loop.

    Rebuilds the diagnosis triple with the exact compile_with_l1 recipe
    (l1.py:101-112) so the report is bound to the CURRENT candidate bytes.
    Opt-in/gating lives at the CLI call site; this function only refuses
    an unbound (None) capture, per the five-gate admission rule.
    """
    if capture is None:
        return False, "L1 多轮暂存未启用：本轮无绑定 capture。", None
    path = directory(out_dir)
    candidate = read(path / f"{stem}.tex")
    try:
        checks = template_results(candidate, path, stem)
    except (OSError, RepairError, ValueError, UnicodeError):
        checks = ()
    try:
        from .compiler import support_manifest
        available = set(support_manifest(path, stem)) | {stem + ".tex"}
    except (OSError, RepairError):
        available = None
    report = diagnose(candidate, capture, template_results=checks,
                      available_paths=available)
    ok, published, capture = run_staged_repair_loop(
        out_dir, stem, source=source, capture=capture, candidate=candidate,
        report=report, max_rounds=max_rounds)
    if ok:
        # H1: the staged loop's no-edit convergence branch can report success
        # with a failure message instead of a PDF path; never hand a non-path
        # to the CLI success branch (it would crash in os.path.getsize()).
        import os  # local import: keep the frozen byte region untouched
        try:
            valid = (isinstance(published, (str, os.PathLike))
                     and Path(published).is_file()
                     and Path(published).stat().st_size > 0)
        except (OSError, TypeError, ValueError):
            valid = False
        if not valid:
            return (False, "L1 多轮暂存未修复为有效 PDF，沿旧失败路径。", capture)
        return True, published, capture
    return False, published, capture


def maybe_staged_repair(ok, info, out_dir, stem, *, source, capture, enabled):
    """CLI-facing wrapper: adopt the staged-loop result only on success.

    On failure return the ORIGINAL info/capture unchanged: export_failure
    binds its diagnostic via _initial_transcript(capture, message) (state.py),
    which requires message to match capture['failure'].  Replacing either
    with staged-loop values breaks the binding and downgrades an editable
    failure to report-only 'unknown'.
    """
    if ok or not enabled or capture is None:
        return ok, info, capture
    staged_ok, staged_info, staged_capture = staged_repair_after_failure(
        out_dir, stem, source=source, capture=capture)
    if staged_ok:
        return True, staged_info, staged_capture
    return False, info, capture
