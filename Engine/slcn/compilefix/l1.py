"""Default-on deterministic failure adapter, with no AI channel or settings."""
import json
from pathlib import Path
import re
import sys

from .files import RepairError, digest, directory, read, regular
from .l1_diagnostics import diagnose, environment_hint, mask_comments
from .l1_rules import select_plan
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
