#!/usr/bin/env python3
"""删除编译临时文件（对应 mac 版的「删除临时文件」，第 91 条搬进引擎）。

    python3 cleantemp.py --folder "<期目录或任意目录>" [--dry-run] [--trash] [--json] [--version]
    python3 cleantemp.py --folder "<期目录>" --restore "<.toolbox-trash/时间戳 目录>"
    python3 cleantemp.py --folder "<目录>" --mode {delivery,workspace,tocwork} [--dry-run] [--delete]
    python3 cleantemp.py --folder "<期目录>" --mode daily [--dry-run] [--json]   # 清理页日常入口
    python3 cleantemp.py --folder "<期目录>" --empty-trash

两种"真动手"的方式（aux 模式，旧行为）：
  · **默认**：直接删除（与老版/mac 版逐字节一致的旧行为，外壳不传 `--trash` 时走这条）；
  · `--trash`：移入 `<目录>/.toolbox-trash/<目录名>-<时间戳>/`（可撤销，`--restore` 搬回来）。

B-211 起新增两种可撤销模式（默认移入回收站，`--delete` 才永久删）：
  · `--mode delivery`：交付目录收尾（先预检 tex 引用闭包；白名单优先；未识别保留）；
  · `--mode workspace`：工作区旧件（每期 `合并*.pdf` 只留最新；固定交付物永不删）；
  · `--mode tocwork`（B-121）：生成中间件与旧检查报告 —— `<期目录>/.toolbox-tmp/` 内容、
    期目录根的时间戳中英目录 tex、`目录结果.json`、旧 `信息检查*`；默认保留最新一组
    信息检查报告（txt/json/_摘要 同 stamp 一起留），`--all-reports` 才连最新一组全清。
    与 delivery/workspace 一样默认移入回收站（`--restore` 可撤销），`--delete` 才永久删。
  · `--empty-trash`：清空 `<目录>/.toolbox-trash/` 下的回收目录。

`--json`（批 117）：人类可读日志改走 stderr，stdout 只留**一个** JSON 结果对象（最后一行）。
结果 schema 见 `docs/ENGINE_API.md`。不传 `--json` 时输出与旧版逐字节一致。

删哪些：`.aux .bbl .bcf .blg .out .run.xml .synctex.gz` 与 macOS 的 `.DS_Store`。
**不删**：`.tex .pdf .sty .bib .log`（`.log` 是排错用的，故意留着）。
名单与判据在 `slcn/clean.py`（与 mac 那份 Swift 逐项一致）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import clean as _clean  # noqa: E402
from slcn import clean_daily as _clean_daily  # noqa: E402
from slcn import platform as _plat  # noqa: E402


def _die(msg: str) -> int:
    print(f"\n❌ {msg}\n", file=sys.stderr)
    return 2


# 引擎日志里表示「真搬运」的行前缀；JSON 的 entries 直接从这里取，
# 避免再写一套清点逻辑（计数与列表同源，不会一个 12 一个 11）。
_ENTRY_ACTIONS = {
    "Will move: ": "move",
    "Moved: ": "move",
    "Will delete: ": "delete",
    "Deleted: ": "delete",
    "Will restore: ": "restore",
    "Restored: ": "restore",
}


def _collect_entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for line in lines:
        for prefix, action in _ENTRY_ACTIONS.items():
            if line.startswith(prefix):
                entries.append({"path": line[len(prefix):], "action": action})
                break
    return entries


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="cleantemp.py",
        description="递归删除目录下的 LaTeX 编译临时文件（不删 .tex / .pdf / .log）；"
                    "B-211 起另有交付收尾 / 工作区旧件两种可撤销模式",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="目录", help="【必填】要清理的目录（递归）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会删哪些，不真删")
    ap.add_argument("--trash", action="store_true",
                    help="aux 模式：移入回收目录（<目录>/.toolbox-trash/<目录名>-<时间戳>/），"
                         "可用 --restore 撤销")
    ap.add_argument("--restore", metavar="回收目录", default="",
                    help="把某个回收目录里的文件搬回原处（不覆盖已存在的文件；兼容旧 manifest）")
    ap.add_argument("--mode", choices=("aux", "delivery", "workspace", "tocwork", "daily"),
                    default="aux",
                    help="aux=编译中间文件（默认，旧行为）；delivery=交付目录收尾；"
                         "workspace=工作区旧件（合并 PDF 只留最新）；"
                         "tocwork=生成中间件与旧检查报告（默认保留最新一组报告）；"
                         "daily=清理页日常入口（唯一候选器，默认移入回收站，可 --restore 撤销）")
    ap.add_argument("--delete", action="store_true",
                    help="delivery/workspace/tocwork 模式：永久删除（默认移入回收站）")
    ap.add_argument("--all-reports", action="store_true",
                    help="tocwork 模式：旧检查报告也全清（默认保留最新一组 txt/json/_摘要）")
    ap.add_argument("--empty-trash", action="store_true",
                    help="清空 <目录>/.toolbox-trash/ 下的所有回收目录")
    ap.add_argument("--json", action="store_true",
                    help="stdout 只输出一个 JSON 结果对象（人类日志改走 stderr）")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 路径不是文件夹：{root}\n", file=sys.stderr)
        return 2

    # 批 117：`--json` 时人类日志走 stderr，stdout 只留结果 JSON。
    # `logged` 收集引擎的搬运行，供 entries 使用（与计数同源）。
    json_mode = bool(args.json)
    human = sys.stderr if json_mode else sys.stdout
    logged: list[str] = []

    def say(line: str = "") -> None:
        print(line, file=human)

    def log(line) -> None:
        text = str(line)
        logged.append(text)
        print(text, file=human)

    def finish(*, mode: str, count: int, ok: bool = True, trash: str = "",
               restore: str = "", error: str = "",
               failures: list[tuple[str, str]] | None = None,
               entries: list[dict] | None = None, status: str = "",
               operation_id: str = "", kept: list[dict] | None = None,
               warnings: list[str] | None = None,
               already_restored: int = 0,
               skipped: list[dict] | None = None,
               summary: dict | None = None) -> None:
        if not json_mode:
            return
        items = entries if entries is not None else _collect_entries(logged)
        data = {
            "ok": ok,
            "command": "cleantemp",
            "mode": mode,
            "dryRun": bool(args.dry_run),
            "root": root,
            "count": count,
            "entryCount": len(items),
            "entries": items,
            "trashDir": trash,
            "restoreDir": restore,
            "failures": [{"path": p, "error": e} for p, e in (failures or [])],
            "warnings": list(warnings) if warnings is not None else [],
            "error": error,
        }
        # daily 新字段：旧 mode 不传 status，输出与旧版逐字节一致。
        if status:
            data["operationId"] = operation_id
            data["status"] = status
            data["kept"] = kept or []
            if mode == "restore":
                # R08：撤销账本（INTERFACE-REWORK 冻结字段）。
                data["alreadyRestored"] = int(already_restored or 0)
                data["skipped"] = list(skipped or [])
                data["summary"] = summary or {
                    "restored": count,
                    "alreadyRestored": int(already_restored or 0),
                    "skipped": len(skipped or []),
                    "failed": len(failures or []),
                }
        print(json.dumps(data, ensure_ascii=False), file=sys.stdout)

    if args.restore:
        trash_root = os.path.abspath(os.path.expanduser(args.restore))
        if not os.path.isdir(trash_root):
            print(f"\n❌ 回收目录不存在：{trash_root}\n", file=sys.stderr)
            return 2
        # R06：所有 --restore（daily / v2 / v1）统一走同一套安全校验与恢复账本；
        # 不再对「读不到 manifest」回退旧 _clean.restore（那会绕过边界校验）。
        return _run_restore_daily(root, trash_root, args, say=say, log=log, finish=finish)

    if args.mode == "daily":
        if args.trash:
            return _die("--mode daily 只回收，不接受 --trash（daily 默认移入回收站，可用 --restore 撤销）")
        if args.delete:
            return _die("--mode daily 只回收，不接受 --delete（daily 默认移入回收站，可撤销；不提供永久删除）")
        if args.empty_trash:
            return _die("--empty-trash 不能与 --mode daily 同时用（会绕过日常清理的撤销批次）")
        if args.all_reports:
            return _die("--all-reports 只用于 tocwork 模式（daily 固定保留最新一组检查报告）")

    if args.mode != "aux" and args.trash:
        return _die("--trash 只用于 aux 模式；delivery/workspace/tocwork 默认就移入回收站"
                    "（永久删用 --delete）")
    if args.all_reports and args.mode != "tocwork":
        return _die("--all-reports 只用于 tocwork 模式")
    if args.trash and args.delete:
        return _die("--trash 与 --delete 不能同时用（一个移入回收站、一个永久删）")

    if args.empty_trash:
        say("清空回收站\n")
        say(f"路径：{root}\n")
        n = _clean.empty_trash(root, log=log, dry_run=args.dry_run)
        say()
        verb = "将清空" if args.dry_run else "已清空"
        say(f"完成。{verb}回收站 {n} 个回收目录（目录：{root}）")
        finish(mode="empty-trash", count=n,
               trash=os.path.join(root, _clean.TRASH_DIR))
        return 0

    if args.mode == "daily":
        return _run_daily_cli(root, args, say=say, log=log, finish=finish)

    if args.mode == "aux":
        return _run_aux(root, args, say=say, log=log, finish=finish)

    if args.mode == "tocwork":
        say("清理生成中间件与旧检查报告\n")
        say(f"路径：{root}\n")
        failures: list[tuple[str, str]] = []
        n, trash_root = _clean.toc_work_clean(
            root, all_reports=args.all_reports, log=log,
            dry_run=args.dry_run, delete=args.delete, failures=failures)
        if failures:
            print(f"\n❌ 清理未完成：成功 {n} 项，失败 {len(failures)} 项",
                  file=sys.stderr)
            finish(mode=args.mode, count=n, ok=False, trash=trash_root,
                   error=f"清理未完成：成功 {n} 项，失败 {len(failures)} 项",
                   failures=failures)
            return 2
        say()
        if args.delete:
            verb = "将删除" if args.dry_run else "已删除"
            say(f"完成。{verb} {n} 项（目录：{root}）")
            finish(mode=args.mode, count=n)
        else:
            verb = "将移入回收目录" if args.dry_run else "已移入回收目录"
            where = trash_root or "（无可清理文件）"
            say(f"完成。{verb} {n} 项（回收目录：{where}）")
            finish(mode=args.mode, count=n, trash=trash_root)
        return 0

    failures: list[tuple[str, str]] = []
    if args.mode == "delivery":
        try:
            _files, _dirs, missing = _clean.delivery_closure(root)
        except _clean.DeliveryError as e:
            return _die(str(e))
        if missing:  # 预检失败：整体拒绝、零写入
            print(f"\n❌ 引用缺失 {len(missing)} 项（整体拒绝，未写入任何文件）：",
                  file=sys.stderr)
            for m in missing:
                print(f"  - {m}", file=sys.stderr)
            print("", file=sys.stderr)
            finish(mode=args.mode, count=0, ok=False,
                   error=f"引用缺失 {len(missing)} 项（整体拒绝，未写入任何文件）")
            return 2
        say("交付目录收尾清理\n")
        say(f"路径：{root}\n")
        n, trash_root = _clean.delivery_clean(
            root, log=log, dry_run=args.dry_run, delete=args.delete,
            failures=failures)
    else:
        say("工作区旧件清理\n")
        say(f"路径：{root}\n")
        n, trash_root = _clean.workspace_clean(
            root, log=log, dry_run=args.dry_run, delete=args.delete,
            failures=failures)

    if failures:
        # 有失败项：不打成功契约行（外壳据此判「已完成」），非零退出码。
        print(f"\n❌ 清理未完成：成功 {n} 项，失败 {len(failures)} 项（失败项见上方）",
              file=sys.stderr)
        finish(mode=args.mode, count=n, ok=False, trash=trash_root,
               error=f"清理未完成：成功 {n} 项，失败 {len(failures)} 项",
               failures=failures)
        return 2

    say()
    if args.delete:
        verb = "将删除" if args.dry_run else "已删除"
        say(f"完成。{verb} {n} 项（目录：{root}）")
        finish(mode=args.mode, count=n)
    else:
        verb = "将移入回收目录" if args.dry_run else "已移入回收目录"
        where = trash_root or "（无可清理文件）"
        say(f"完成。{verb} {n} 项（回收目录：{where}）")
        finish(mode=args.mode, count=n, trash=trash_root)
    return 0


def _run_daily_cli(root: str, args: argparse.Namespace, *, say, log, finish) -> int:
    """daily 模式：清理页日常入口（候选由 slcn/clean_daily.py 唯一定义）。

    与旧 mode 的差别：候选集合更保守（引用闭包/最新报告组/不可再生一律保留），
    执行前用冻结的扫描指纹复核，先落盘可恢复清单再搬移；--dry-run 只诊断。
    """
    say("清理临时文件（日常规则）\n")
    say(f"路径：{root}\n")
    res = _clean_daily.run_daily(root, dry_run=args.dry_run, log=log)
    status = res.get("status") or ""
    operation_id = res.get("operationId") or ""
    kept = res.get("kept") or []
    warnings = res.get("warnings") or []
    failures = [(str(f.get("path") or ""), str(f.get("error") or ""))
                for f in (res.get("failures") or [])]
    entries = [{"path": e["path"], "action": e.get("action", "move")}
               for e in (res.get("entries") or [])]
    n = int(res.get("count") or 0)
    error = res.get("error") or ""

    if status == "preflight_failed":
        # 预检失败：零写入，逐条给原因（外壳据此显示「未改动任何文件」）。
        print(f"\n❌ {error or '预检未通过（未写入任何文件）'}", file=sys.stderr)
        for path, err in failures[:30]:
            print(f"  - {path}：{err}", file=sys.stderr)
        if len(failures) > 30:
            print(f"  … 还有 {len(failures) - 30} 项", file=sys.stderr)
        print("", file=sys.stderr)
        finish(mode="daily", count=0, ok=False, error=error, failures=failures,
               entries=[], status=status, operation_id=operation_id,
               kept=kept, warnings=warnings)
        return 2

    if status == "cancelled":
        # 用户点了全局「停止」（SIGTERM）：已搬的部分在回收批次里，可 --restore 撤销。
        print(f"\n❌ {error or '已停止'}", file=sys.stderr)
        finish(mode="daily", count=n, ok=False, trash=res.get("trashDir") or "",
               error=error, failures=failures, entries=entries, status=status,
               operation_id=operation_id, kept=kept, warnings=warnings)
        return 2

    if status == "partial":
        print(f"\n❌ 清理未完成：成功 {n} 项，失败 {len(failures)} 项（失败项见上方）",
              file=sys.stderr)
        finish(mode="daily", count=n, ok=False, trash=res.get("trashDir") or "",
               error=error, failures=failures, entries=entries, status=status,
               operation_id=operation_id, kept=kept, warnings=warnings)
        return 2

    say()
    if kept:
        say(f"保留 {len(kept)} 项（按判据未回收）")
    if status == "empty":
        say(f"完成。没有可清理的临时文件（目录：{root}）")
        finish(mode="daily", count=0, ok=True, entries=[], status=status,
               operation_id=operation_id, kept=kept, warnings=warnings)
        return 0

    trash = res.get("trashDir") or ""
    if args.dry_run:
        say(f"完成。将回收 {n} 项（预演，未写入磁盘）")
    else:
        say(f"完成。已回收 {n} 项（回收目录：{trash}）")
    finish(mode="daily", count=n, ok=True, trash=trash, entries=entries,
           status=status, operation_id=operation_id, kept=kept, warnings=warnings)
    return 0


def _run_restore_daily(root: str, trash_root: str, args: argparse.Namespace,
                       *, say, log, finish) -> int:
    """daily 批次的安全撤销：校验批次归属/条目边界，不覆盖，冲突可重试。"""
    say("撤销清理（从回收目录搬回）\n")
    say(f"路径：{root}\n")
    res = _clean_daily.restore_daily(root, trash_root, dry_run=args.dry_run, log=log)
    status = res.get("status") or ""
    operation_id = res.get("operationId") or ""
    warnings = res.get("warnings") or []
    failures = [(str(f.get("path") or ""), str(f.get("error") or ""))
                for f in (res.get("failures") or [])]
    skipped = [{"path": str(s.get("path") or ""), "reason": str(s.get("reason") or "")}
               for s in (res.get("skipped") or [])]
    already = int(res.get("alreadyRestored") or 0)
    n = int(res.get("count") or 0)
    summary = res.get("summary") or {
        "restored": n, "alreadyRestored": already,
        "skipped": len(skipped), "failed": len(failures),
    }
    entries = [{"path": e["path"], "action": "restore",
                "status": e.get("status", "restored")}
               for e in (res.get("entries") or [])]
    error = res.get("error") or ""
    verb = "将恢复" if args.dry_run else "已恢复"

    def done(*, ok: bool, count: int, with_entries: bool = True) -> None:
        finish(mode="restore", count=count, ok=ok, restore=trash_root,
               error=error, failures=failures,
               entries=entries if with_entries else [], status=status,
               operation_id=operation_id, kept=[], warnings=warnings,
               already_restored=already, skipped=skipped, summary=summary)

    if status == "preflight_failed":
        print(f"\n❌ {error or '撤销预检未通过（未写入任何文件）'}", file=sys.stderr)
        for path, err in failures[:30]:
            print(f"  - {path}：{err}", file=sys.stderr)
        if len(failures) > 30:
            print(f"  … 还有 {len(failures) - 30} 项", file=sys.stderr)
        print("", file=sys.stderr)
        done(ok=False, count=0, with_entries=False)
        return 2

    if status == "cancelled":
        print(f"\n❌ {error or '已停止'}", file=sys.stderr)
        say(f"完成。{verb} {n} 个文件（目录：{root}）")
        done(ok=False, count=n)
        return 2

    say()
    if status == "partial":
        unsaved = len(skipped) + len(failures)
        print(f"\n❌ {error or '撤销未完成'}", file=sys.stderr)
        say(f"完成。{verb} {n} 个文件，{unsaved} 项未恢复（目录：{root}）")
        done(ok=False, count=n)
        return 2

    if status == "empty":
        say(f"完成。没有可恢复的文件（目录：{root}）")
        done(ok=True, count=0, with_entries=False)
        return 0

    say(f"完成。{verb} {n} 个文件（目录：{root}）")
    done(ok=True, count=n)
    return 0


def _run_aux(root: str, args: argparse.Namespace, *, say, log, finish) -> int:
    """aux 模式：默认路径输出与 B-211 之前**逐字节一致**（旧外壳只认这一段）。"""
    say("删除临时文件\n")
    say(f"路径：{root}\n")
    if args.trash:
        n, trash_root = _clean.trash(root, log=log, dry_run=args.dry_run)
        say()
        # 契约行：外壳靠它拿「移了几个」与「回收目录在哪」（撤销按钮要用）
        verb = "将移入回收目录" if args.dry_run else "已移入回收目录"
        say(f"完成。{verb} {n} 个临时文件（回收目录：{trash_root or '（无可清理文件）'}）")
        finish(mode="aux", count=n, trash=trash_root)
        return 0

    n, _files = _clean.clean(root, log=log, dry_run=args.dry_run)

    say()
    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿「删了几个」
    verb = "将删除" if args.dry_run else "已删除"
    say(f"完成。{verb} {n} 个临时文件（目录：{root}）")
    finish(mode="aux", count=n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
