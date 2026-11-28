#!/usr/bin/env python3
"""预检和刷新一期正文、目录的页码，或恢复一次中断的发布。"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys
import uuid

from slcn import issuepaging, issuepaging_commit as commit, issuepaging_toc, issuewrite
from slcn import pdfmerge, platform, tocdelivery, toctemplate
from slcn.clean_workspaces import checked_stat, is_link
from slcn.orderdraft import list_order_candidates
from slcn.pageplan import PageRange, PagePlanError


class Pending(RuntimeError):
    def __init__(self, record):
        super().__init__("本期有待恢复事务，请先使用对应的恢复命令")
        self.record = record


class RefreshFailure(RuntimeError):
    def __init__(self, error, run):
        super().__init__(str(error))
        self.error = error
        self.run = run


@dataclass(frozen=True)
class RefreshPlan:
    issue: Path
    regenerate_toc: bool
    order_result: Path | bytes | None
    on_change: bool
    only: tuple[int, ...]
    plan_id: str
    expected: dict
    selection: dict | None
    excluded: tuple[str, ...]
    toc_inputs: tuple[Path, ...]
    dependencies: tuple
    directories: dict
    preview: dict


def _info(path, *, return_bytes=False):
    """Bind actual bytes and file identity, checking the open descriptor and the path."""
    before = path.lstat()
    if is_link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError(f"计划输入不是独立普通文件：{path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            raise ValueError(f"输入身份变化：{path}")
        if return_bytes:
            raw = stream.read()
            digest = hashlib.sha256(raw).hexdigest()
        else:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    if (not os.path.samestat(after, path.lstat()) or
            (before.st_ctime_ns, before.st_size) != (after.st_ctime_ns, after.st_size)):
        raise ValueError(f"输入在读取期间变化：{path}")
    identity = (str(path), before.st_dev, before.st_ino, digest)
    return (identity, raw) if return_bytes else identity


def _order(issue, state, order_result, directories):
    if order_result is None:
        saved = commit.read_selection(issue, state)
        excluded = commit.merge_exclusions(issue, state)
        return excluded, saved, None, excluded
    if isinstance(order_result, bytes):
        raw = order_result
        source = ("stdin", hashlib.sha256(raw).hexdigest())
    else:
        source, raw = _info(order_result, return_bytes=True)
    result = json.loads(raw)
    if (not isinstance(result, dict) or result.get("schema") != 1
            or result.get("operation") != "renamefolders"
            or result.get("issue") != issuewrite.issue_identity(issue)
            or result.get("status") not in {"completed", "unchanged"}):
        raise ValueError("排序结果不是本期已完成的改名记录")
    renames = result.get("renames")
    excluded = result.get("excluded")
    if (not isinstance(renames, list) or not isinstance(excluded, list)
            or not all(isinstance(name, str) for name in excluded)):
        raise ValueError("排序结果缺少篇目或排除清单")
    ordered = []
    for i, row in enumerate(renames, 1):
        name = row.get("new") if isinstance(row, dict) else None
        if (not isinstance(name, str) or not name.startswith(f"{i:02d} ")
                or name not in directories or row.get("main_tex") != name[3:] + ".tex"):
            raise ValueError("排序结果与当前篇序或主 TeX 不符")
        ordered.append(name)
    names = {x.folder for x in list_order_candidates(issue) if not x.folder.startswith("000")}
    if (set(ordered + excluded) != names or len(ordered + excluded) != len(names)):
        raise ValueError("排序结果未完整、唯一覆盖当前直属目录")
    actual = {}
    for name in names:
        info = checked_stat(str(issue), name)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"排序目录身份无效：{name}")
        actual[name] = [info.st_dev, info.st_ino]
    if result.get("directories") != actual:
        raise ValueError("排序结果中的目录身份与当前本期不符，请重新确认排序")
    selected_exclusions = tuple(sorted(set(excluded) & directories.keys()))
    saved = (dict(issue=issuewrite.issue_identity(issue), directories=directories,
                  excluded=list(selected_exclusions)) if selected_exclusions else None)
    return selected_exclusions, saved, source, tuple(excluded)


def _row(issue, article):
    return dict(folder=article.folder, main_tex=article.tex.relative_to(issue).as_posix(),
                pdf=article.pdf.relative_to(issue).as_posix(),
                old=dict(first=article.first, last=article.last, count=article.count),
                new=None, physical_pages=None, compiled=False, recompiled=False)


def _root(path):
    path = Path(path).expanduser().absolute()
    if is_link(path.lstat()):
        raise PagePlanError("期目录不能是链接")
    return path.resolve(strict=True)


def _dependencies(articles, toc_inputs, targets):
    issue = articles[0].directory.parent
    files = set(toc_inputs)
    for article in articles:
        files.update(issuepaging.article_inputs(article))
    return tuple(_info(path) for path in sorted(files) if path not in targets)


def _current_selection(issue, state_dir, order_result):
    """当前期与选择解析：摘要与预览共用，不在此处读正文或建写事务。"""
    issue = _root(issue)
    if Path(state_dir).resolve().is_relative_to(issue):
        raise ValueError("期状态目录必须位于期目录之外")
    pending = issuewrite.pending_issue_write(issue, state_dir)
    if pending:
        raise Pending(pending)
    directories = commit.numbered_directories(issue)
    excluded, selection, order_stamp, reported_exclusions = _order(
        issue, state_dir, order_result, directories)
    return issue, directories, excluded, selection, order_stamp, reported_exclusions


def selection_summary(issue, *, state_dir, order_result=None):
    """只读选择计数；编号目录与排除解析与预览同源，不读正文 PDF/TeX、不编译、不写盘。"""
    issue, directories, excluded, selection, order_stamp, _reported = _current_selection(
        issue, state_dir, order_result)
    excluded_numbered = len(set(excluded) & set(directories))
    return dict(schema=1, operation="refreshpages", status="selection_summary",
                issue=issuewrite.issue_identity(issue),
                order_source="provided" if order_stamp else "saved" if selection else "implicit_all",
                numbered_count=len(directories),
                excluded_numbered_count=excluded_numbered,
                article_count=len(directories) - excluded_numbered)


def build_refresh_plan(issue: Path, *, state_dir: Path, regenerate_toc=False, order_result=None,
                       on_change=False, only=()):
    issue, directories, excluded, selection, order_stamp, reported_exclusions = _current_selection(
        issue, state_dir, order_result)
    articles = issuepaging.inspect_articles(issue, excluded=excluded)
    if set(directories) - set(excluded) != {a.folder for a in articles}:
        raise PagePlanError("合刊读入的编号目录与可刷新篇目不一致，请核对排序和排除清单")
    if only and (not on_change or len(set(only)) != len(only)
                 or any(type(n) is not int or n < 1 or n > len(articles) for n in only)):
        raise ValueError("按序号编译需要有效且不重复的 --on-change 篇目序号")
    inputs = []
    for article in articles:
        inputs.append(issuepaging.article_fingerprint(article))
    old_ranges = [PageRange(a.folder, a.first, a.last, a.count) for a in articles]
    warnings = []
    if not regenerate_toc:
        reason = issuepaging_toc.check_issue_toc(issue, old_ranges)
        if reason:
            if not on_change:
                raise PagePlanError(reason)
            warnings.append(f"如果需要刷新整期，必须先处理目录：{reason}")
    toc_inputs = issuepaging_toc.toc_source_paths(issue, regenerate=regenerate_toc)
    existing = tocdelivery.find_toc_dir(str(issue))
    toc_dir = Path(existing).name if existing else toctemplate.TOC_DIR
    rows = [_row(issue, a) for a in articles]
    writes = [row[key] for row in rows for key in ("main_tex", "pdf")]
    toc_writes = [f"{toc_dir}/{stem}.{ext}" for _, stem in tocdelivery.TEX_STEMS for ext in ("tex", "pdf")]
    writes += toc_writes
    expected = {rel: commit.target_stamp(issue, rel) for rel in writes}
    strategy = ("regenerated" if regenerate_toc else "updated") if any(
        expected[rel]["sha"] is not None for rel in toc_writes) else "generated"
    identity = issuewrite.issue_identity(issue)
    dependencies = _dependencies(articles, toc_inputs, {issue / rel for rel in expected})
    data = [identity, directories, inputs, dependencies, expected, regenerate_toc, order_stamp,
            excluded, selection, on_change, only]
    key = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    preview = dict(schema=1, operation="refreshpages", status="preview", can_apply=True,
                   plan_id=key, issue=identity, articles=rows, rounds=0, writes=writes,
                   toc_outputs=[x for x in toc_writes if x.endswith(".pdf")], toc_strategy=strategy,
                   warnings=warnings, scope="on_change" if on_change else "issue",
                   only=list(only), excluded=list(reported_exclusions),
                   order_source="provided" if order_stamp else "saved" if selection else "implicit_all")
    return RefreshPlan(issue, regenerate_toc, order_result, on_change, tuple(only), key, expected,
                       selection, excluded, toc_inputs, dependencies, directories, preview)


def _published(plan, articles, toc, sources):
    issue = plan.issue
    if issuewrite.issue_identity(issue) != plan.preview["issue"]:
        raise ValueError("提交期间期目录身份已变化")
    if commit.numbered_directories(issue) != plan.directories:
        raise ValueError("发布期间合刊所用的编号目录发生变化")
    actual = issuepaging.inspect_articles(issue, excluded=plan.excluded)
    if _dependencies(actual, plan.toc_inputs, {issue / rel for rel in plan.expected}) != plan.dependencies:
        raise ValueError("发布期间编译依赖已变化，请重新预览")
    for rel, stamp in plan.expected.items():
        if rel not in sources and commit.target_stamp(issue, rel) != stamp:
            raise ValueError(f"发布期间未参与写入的原位成品已变化：{rel}")
    if [a.folder for a in actual] != [r.folder for r in articles.page_ranges]:
        raise ValueError("提交期间篇目或顺序发生变化")
    from pypdf import PdfReader
    for article, span in zip(actual, articles.page_ranges, strict=True):
        found = pdfmerge.resolve_issue_article_pdf(str(article.directory), article.folder)
        if (not found or Path(found[0]) != article.pdf or len(PdfReader(article.pdf).pages) != span.count
                or (article.first, article.last, article.count) != (span.first, span.last, span.count)):
            raise ValueError(f"合刊读取的正文、物理页数或页码字段与候选不符：{article.folder}")
    if toc is None:
        return
    if Path(tocdelivery.find_toc_dir(str(issue))) != toc.toc_dir:
        raise ValueError("实际合刊使用了其他目录")
    for key, stem in tocdelivery.TEX_STEMS:
        if len(PdfReader(toc.toc_dir / (stem + ".pdf")).pages) != toc.pages[key]:
            raise ValueError(f"已发布的目录物理页数不符：{stem}")


def refresh_issue(plan: RefreshPlan, *, plan_id: str, state_dir: Path):
    issue = plan.issue
    state = Path(state_dir).resolve()
    def recheck():
        current = build_refresh_plan(issue, state_dir=state, regenerate_toc=plan.regenerate_toc,
                                     order_result=plan.order_result, on_change=plan.on_change, only=plan.only)
        if current.plan_id != plan_id or plan.plan_id != plan_id:
            raise PagePlanError("预览后的输入、写入目标或选项已变化，请重新预览")
        return current
    with issuewrite.issue_write_lock(issue, state):
        current = recheck()
        run = state / "runs" / uuid.uuid4().hex
        if run.resolve() != run or run.is_relative_to(issue):
            raise ValueError("隔离工作区不安全")
        run.mkdir(parents=True)
        try:
            only = current.only
            if current.on_change and not only:
                only = tuple(range(1, len(current.preview["articles"]) + 1))
            articles = issuepaging.prepare_articles(issue, run / "articles", excluded=current.excluded, only=only)
            full = not current.on_change or articles.full_refresh
            toc = None
            if full:
                metadata = {item.source.folder: item.tex for item in articles.articles}
                toc = issuepaging_toc.stage_issue_toc(issue, articles.page_ranges, work_dir=run / "toc",
                                                     regenerate=plan.regenerate_toc, metadata_tex=metadata)
            recheck()
            rows = []
            sources = {}
            for item, span in zip(articles.articles, articles.page_ranges, strict=True):
                row = _row(issue, item.source)
                if full:
                    sources[row["main_tex"]] = item.tex
                if item.compiled:
                    sources[row["pdf"]] = item.pdf
                row.update(new=asdict(span), physical_pages=item.physical_pages,
                           compiled=item.compiled, recompiled=item.recompiled)
                rows.append(row)
            if toc:
                for key, stem in tocdelivery.TEX_STEMS:
                    for ext, paths in (("tex", toc.tex), ("pdf", toc.pdf)):
                        sources[f"{toc.toc_dir.name}/{stem}.{ext}"] = paths[key]
            planned = (current.expected.keys() if full else
                       {current.preview["articles"][n - 1]["pdf"] for n in only})
            if sources.keys() != planned:
                raise ValueError("候选落盘清单与预览不符")
            changed = commit.publish(
                issue, state, run, sources, current.expected, selection=current.selection,
                verify=lambda: _published(current, articles, toc, sources))
        except Exception as exc:
            (run / "error.txt").write_text(str(exc), encoding="utf-8")
            raise RefreshFailure(exc, run) from exc
        shutil.rmtree(run, ignore_errors=True)
    return dict(current.preview, status="completed" if changed else "unchanged", can_apply=False,
                articles=rows, writes=changed, rounds=articles.rounds, warnings=[],
                toc_strategy=toc.mode if toc else "unchanged",
                toc_outputs=current.preview["toc_outputs"] if toc else [],
                **({"maxPages": toc.max_pages, "compressionSkipped": toc.compression_skipped}
                   if toc else {}),
                scope="issue" if full else "selected_pdfs")


def main(argv=None):
    platform.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--selection-summary", action="store_true",
                        help="只读输出当前期编号目录的选择计数，不编译也不写盘")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-id")
    parser.add_argument("--recover")
    parser.add_argument("--regenerate-toc", action="store_true")
    parser.add_argument("--order-result", type=Path)
    parser.add_argument("--on-change", action="store_true", help="隔离编译后按实际页数选择发布范围")
    parser.add_argument("--only", help="仅编译指定篇目，以逗号分隔序号，例如 1,3")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    issue = None
    state = issuewrite.default_state_dir()
    body = dict(schema=1, operation="refreshpages", status="blocked", issue=None, can_apply=False)
    code = 0
    try:
        with redirect_stdout(sys.stderr):
            issue = _root(args.issue)
            body["issue"] = issuewrite.issue_identity(issue)
            if args.recover or args.selection_summary:
                if args.recover and args.selection_summary:
                    raise ValueError("只读摘要与恢复模式互斥")
                if args.selection_summary:
                    if (args.apply or args.plan_id or args.recover or args.regenerate_toc
                            or args.on_change or args.only is not None):
                        raise ValueError("只读摘要与刷新选项互斥")
                    order = args.order_result
                    if order == Path("-"):
                        order = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
                    body = selection_summary(issue, state_dir=state, order_result=order)
                else:
                    if args.apply or args.plan_id or args.regenerate_toc or args.order_result or args.on_change or args.only:
                        raise ValueError("恢复模式与刷新选项互斥")
                    with issuewrite.issue_write_lock(issue, state):
                        pending = issuewrite.pending_issue_write(issue, state)
                        if (not pending or pending.get("operation") != "refreshpages"
                                or pending.get("recovery_id") != args.recover):
                            raise ValueError("没有匹配的页码恢复记录")
                        commit.recover(issue, state, args.recover)
                    body.update(status="rolled_back", recovery_id=args.recover, warnings=[])
            else:
                if args.apply != bool(args.plan_id):
                    raise ValueError("应用时必须同时提供 --apply 和 --plan-id")
                only = ()
                if args.only is not None:
                    if not re.fullmatch(r"[0-9]{1,2}(,[0-9]{1,2})*", args.only):
                        raise ValueError("--only 需要以逗号分隔的篇目序号")
                    only = tuple(int(n) for n in args.only.split(","))
                order = args.order_result
                if order == Path("-"):
                    order = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
                plan = build_refresh_plan(issue, state_dir=state, regenerate_toc=args.regenerate_toc,
                                          order_result=order, on_change=args.on_change, only=only)
                body = refresh_issue(plan, plan_id=args.plan_id, state_dir=state) if args.apply else plan.preview
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        error = exc.error if isinstance(exc, RefreshFailure) else exc
        if isinstance(exc, RefreshFailure):
            body["diagnostic_dir"] = str(exc.run)
        try:
            pending = issuewrite.pending_issue_write(issue, state) if issue else None
        except (ValueError, OSError):
            pending = None
        if pending:
            body.update(status="recovery_required", recovery_id=pending.get("recovery_id"),
                        recovery_path=pending.get("path"), pending_operation=pending.get("operation"))
        elif isinstance(error, commit.RolledBack):
            body["status"] = "rolled_back"
        body.update(error=str(error), warnings=[str(error)])
        code = 1 if pending or isinstance(exc, RefreshFailure) else 2
    print(json.dumps(body, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
