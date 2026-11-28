#!/usr/bin/env python3
"""Preview, apply, and recover an explicitly confirmed issue folder order."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slcn import platform as _plat  # noqa: E402
from slcn import issueorder, issuewrite


def _result(plan=None, result=None, *, issue=None, error=None, status=None):
    body = {"schema": 1, "operation": "renamefolders", "status": status or "blocked",
            "issue": issue, "warnings": [], "plan_id": None, "can_apply": False,
            "renames": [], "excluded": [], "conflicts": []}
    if plan:
        body.update({"issue": plan.issue, "plan_id": plan.plan_id,
                     "can_apply": plan.can_apply, "warnings": list(plan.warnings),
                     "renames": [asdict(row) for row in plan.renames],
                     "excluded": list(plan.excluded), "conflicts": list(plan.conflicts)})
        body["status"] = "preview" if plan.can_apply else "blocked"
    if result:
        body["status"] = result.status
        body["can_apply"] = False
        body["renames"] = [asdict(row) for row in result.renames] if result.renames else body["renames"]
        body["conflicts"] = list(result.conflicts)
        if result.directories is not None:
            body["directories"] = {name: list(identity)
                                    for name, identity in sorted(result.directories.items())}
        if result.recovery_id:
            body["recovery_id"] = result.recovery_id
        if result.recovery_path:
            body["recovery_path"] = result.recovery_path
        if result.recovery_operation:
            body["recovery_operation"] = result.recovery_operation
    if error:
        body["error"] = str(error)
        body["conflicts"] = [str(error)]
    return body


def main(argv=None):
    _plat.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--order", help="JSON 顺序文件，使用 - 从 stdin 读取")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-id")
    parser.add_argument("--recover")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        if bool(args.recover) == bool(args.order) or args.recover and (args.apply or args.plan_id):
            raise ValueError("恢复模式与顺序预览/应用参数互斥")
        issue = Path(args.issue).expanduser().resolve(strict=True)
        state_dir = issuewrite.default_state_dir()
        if args.recover:
            result = issueorder.recover_issue_rename(issue, args.recover, state_dir)
            body = _result(result=result, issue=issuewrite.issue_identity(issue))
            code = 0 if result.status == "rolled_back" else 1
        else:
            if args.apply != bool(args.plan_id):
                raise ValueError("应用时必须同时提供 --apply 和 --plan-id")
            raw = sys.stdin.read() if args.order == "-" else Path(args.order).read_text(encoding="utf-8")
            payload = json.loads(raw)
            if (not isinstance(payload, dict) or payload.get("schema") != 1
                    or not isinstance(payload.get("ordered"), list)
                    or not isinstance(payload.get("excluded"), list)):
                raise ValueError("顺序 JSON 格式无效")
            order = issueorder.OrderInput(tuple(payload["ordered"]), tuple(payload["excluded"]))
            pending = issuewrite.pending_issue_write(issue, state_dir)
            if pending:
                body = _result(issue=issuewrite.issue_identity(issue), status="recovery_required",
                               error="本期存在待恢复写入")
                body.update({"recovery_id": pending.get("recovery_id"), "recovery_path": pending.get("path")})
                body["recovery_operation"] = pending.get("operation")
                code = 1
            else:
                plan = issueorder.build_rename_plan(issue, order)
                if args.apply:
                    result = issueorder.apply_rename_plan(plan, plan_id=args.plan_id, state_dir=state_dir)
                    body = _result(plan, result=result)
                    code = 0 if result.status in {"completed", "unchanged"} else 1 if result.status in {"rolled_back", "recovery_required"} else 2
                else:
                    body = _result(plan)
                    code = 0 if plan.can_apply else 2
        print(json.dumps(body, ensure_ascii=False))
        return code
    except (OSError, ValueError, TypeError, issuewrite.IssueBusyError) as exc:
        print(json.dumps(_result(error=exc), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
