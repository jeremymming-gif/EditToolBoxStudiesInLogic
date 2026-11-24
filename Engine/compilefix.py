"""Manual local export, review and application of compile-repair taskbooks."""

import argparse
import sys

from slcn.compilefix.files import RepairError, json_bytes, write_stdout
from slcn.compilefix.state import Store
from slcn.compilefix.transaction import apply, mark_output


def _parser():
    parser = argparse.ArgumentParser(description="本地编译修复任务书与人工回填")
    parser.add_argument("--state-dir", metavar="ROOT", help="私有任务状态目录")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="从已签发的产物导出任务书")
    export.add_argument("out_dir", metavar="OUT_DIR")
    export.add_argument("stem", metavar="STEM")
    export.add_argument("--synthetic", action="store_true", help="授权使用系统临时区的合成候选")
    export.add_argument("--round-limit", type=int, metavar="N", help="目标轮次上限，1–3，默认 1")
    for name, description in (
        ("preview", "输出可分享的脱敏任务书原字节"), ("confirm", "逐稿确认预览 SHA-256"),
        ("import", "导入回件待人工审阅"), ("review", "输出本地完整差异"),
        ("apply", "采纳已审阅差异并通过两次真编译"), ("status", "只读查看任务状态"),
        ("mark", "标记已确认、冻结或显式重新开放"), ("cancel", "取消待审任务"),
        ("purge", "删除本地任务材料或清理过期材料"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("task_id", metavar="ID", **({"nargs": "?"} if name == "purge" else {}))
        if name in {"confirm", "apply"}:
            command.add_argument("fingerprint", metavar="SHA256" if name == "confirm" else "DIFF_SHA256")
        if name == "import":
            command.add_argument("reply", metavar="REPLY.json")
        if name == "mark":
            command.add_argument("kind", choices=("confirmed", "frozen", "reopen"))
            command.add_argument("--actor", metavar="NAME", help="操作者标记，默认使用本地 OS 身份")
    return parser


def _dispatch(store, args, lease):
    if args.command == "export":
        task_id, _ = store.issue(args.out_dir, args.stem, synthetic=args.synthetic, limit=args.round_limit)
        print(task_id)
        return
    task_id = args.task_id
    if args.command == "purge":
        store.purge(task_id)
    elif args.command == "preview":
        write_stdout(store.preview(task_id))
        store.record_preview(task_id)
    elif args.command == "confirm":
        store.confirm(task_id, args.fingerprint)
    elif args.command == "import":
        store.import_reply(task_id, args.reply)
    elif args.command == "review":
        state = store.load(task_id)
        _, _, diff, _, _ = store.review(task_id, state)
        write_stdout(diff)
        state["reviewed_sha256"] = state["review_sha256"]
        store.save(task_id, state)
    elif args.command == "mark":
        mark_output(store, task_id, store.load(task_id), args.kind, args.actor)
    elif args.command == "cancel":
        store.cancel(task_id)
    elif args.command == "apply":
        apply(store, task_id, args.fingerprint, lease)


def main(argv=None):
    args = _parser().parse_args(argv)
    out_dir = None
    try:
        store = Store(args.state_dir)
        if args.command == "status":
            write_stdout(json_bytes(store.status(args.task_id)))
            return 0
        if args.command == "export":
            store.preflight_export(args.out_dir, args.stem, synthetic=args.synthetic)
        if args.command == "apply":
            state = store.load(args.task_id)
            out_dir = state["out_dir"]
        with store.operation() as lease:
            _dispatch(store, args, lease)
        return 0
    except RepairError as exc:
        print(f"编译修复 · {args.command}：{exc}", file=sys.stderr)
        return 2
    except (OSError, KeyboardInterrupt, KeyError, TypeError, ValueError, OverflowError) as exc:
        print(f"编译修复 · {args.command}：本地操作失败（{type(exc).__name__}），请检查状态与产物。",
              file=sys.stderr)
        return 2
    finally:
        if out_dir is not None:
            print(f"完成。产物目录：{out_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
