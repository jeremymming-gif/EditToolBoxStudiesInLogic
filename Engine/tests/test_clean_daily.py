#!/usr/bin/env python3
"""清理页日常模式（daily）T1+T2 回归：候选/保护/批次/恢复/中断/CLI 契约。

独立脚本（与 tests/test_b211_cleanup.py 同风格；不依赖 pytest）：

    cd Engine
    PYTHONPATH=".:<vendor>" PYTHONDONTWRITEBYTECODE=1 python3.14 tests/test_clean_daily.py

全部用真实临时目录与真 CLI（subprocess 起 cleantemp.py），不 skip、不弱化断言。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
CLEANTEMP = os.path.join(ENGINE, "cleantemp.py")
VENDOR = "/Users/zhangwenming/Documents/GitHub/EditToolBoxStudiesInLogic/Engine/vendor"

ENV = dict(os.environ)
ENV["PYTHONPATH"] = "." + os.pathsep + VENDOR
ENV["PYTHONDONTWRITEBYTECODE"] = "1"

sys.path.insert(0, ENGINE)
import cleantemp as ct  # noqa: E402
from slcn import clean_daily as cd  # noqa: E402

TOTAL = 0
PASS = 0
FAIL = 0
TEMP_DIRS: list[str] = []
TESTS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global TOTAL, PASS, FAIL
    TOTAL += 1
    if ok:
        PASS += 1
        print("PASS  " + name)
    else:
        FAIL += 1
        print("FAIL  " + name + ("  [" + detail + "]" if detail else ""))


def test(fn):
    TESTS.append(fn)
    return fn


# ── 夹具 / 快照 ──────────────────────────────────────────────────────────


def tmpdir(tag: str) -> str:
    d = tempfile.mkdtemp(prefix="clean_daily_" + tag + "_")
    TEMP_DIRS.append(d)
    return d


def _force_rmtree(path: str) -> None:
    if not os.path.exists(path):
        return
    for root, dirs, _files in os.walk(path):
        for name in dirs:
            try:
                os.chmod(os.path.join(root, name), 0o755)
            except OSError:
                pass
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def cleanup_all() -> None:
    for d in reversed(TEMP_DIRS):
        _force_rmtree(d)


def w(root: str, rel: str, data: bytes = b"x\n") -> str:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return full


def mkdirs(root: str, rel: str) -> str:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(full, exist_ok=True)
    return full


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snap(root: str, skip_trash: bool = True) -> dict:
    out: dict = {}

    def walk(abs_dir: str, rel_dir: str) -> None:
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
        except OSError:
            return
        for ent in entries:
            rel = ent.name if rel_dir == "" else rel_dir + "/" + ent.name
            if skip_trash and rel_dir == "" and ent.name == cd.TRASH_DIR:
                continue
            try:
                link = ent.is_symlink()
                isdir = ent.is_dir(follow_symlinks=False)
            except OSError:
                out[rel] = "unreadable"
                continue
            if link:
                try:
                    out[rel] = "link:" + os.readlink(ent.path)
                except OSError:
                    out[rel] = "link:?"
            elif isdir:
                out[rel] = "dir"
                walk(ent.path, rel)
            else:
                try:
                    out[rel] = sha(ent.path)
                except OSError:
                    out[rel] = "unreadable"

    walk(root, "")
    return out


# ── CLI ──────────────────────────────────────────────────────────────────


def run_cli(*args: str) -> tuple:
    proc = subprocess.run(
        [sys.executable, CLEANTEMP, *args],
        cwd=ENGINE,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def _json_last(out: str) -> dict:
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        raise AssertionError("stdout 没有 JSON 结果")
    return json.loads(lines[-1])


def run_cli_json(*args: str) -> tuple:
    rc, out, err = run_cli(*args, "--json")
    return rc, _json_last(out), err


def trash_path(root: str) -> str:
    return os.path.join(root, cd.TRASH_DIR)


def _manifest(batch: str) -> dict:
    with open(os.path.join(batch, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def make_batch(root: str, name: str, manifest: dict, files: dict | None = None) -> str:
    batch = os.path.join(root, cd.TRASH_DIR, name)
    os.makedirs(batch, exist_ok=True)
    for rel, data in (files or {}).items():
        w(batch, rel, data)
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False)
    return batch


def batch_payload(batch: str) -> list:
    """批次 payload（不含 manifest 自身），用于核对「磁盘事实 == 清单」。"""
    out: list = []
    for base, _dirs, files in os.walk(batch):
        for name in files:
            if name in ("manifest.json", "manifest.json.tmp"):
                continue
            out.append(os.path.relpath(os.path.join(base, name), batch))
    return sorted(out)


def cand_paths(scan: dict) -> list:
    return sorted(c["path"] for c in scan["candidates"])


def kept_kind(scan: dict) -> dict:
    return {k["path"]: k["kind"] for k in scan["kept"]}


def _bad(detail: str) -> str:
    return detail


# ── T1-1 范围与最新报告组 ────────────────────────────────────────────────


@test
def test_daily_scope_and_latest_report_group() -> None:
    d = tmpdir("scope")
    root = os.path.join(d, "2026年第5期(90)")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n\\input{sec/a}\n\\includegraphics{pic}\n")
    w(root, "sec/a.tex", b"text\n")
    w(root, "pic.pdf", b"pdf-bytes\n")
    for name in ("main.aux", "main.bcf", "main.blg", "main.fls", "main.toc", "main.synctex.gz"):
        w(root, name, b"aux\n")
    w(root, "main.log", b"This is XeTeX, Version 3.141592653\n")
    w(root, ".toolbox-tmp/x.tmp", b"tmp\n")
    w(root, "Chinese Content_2026-01-01_00-00-00.tex", b"gen\n")
    w(root, "目录结果.json", b"{}\n")
    old = ["信息检查2026_01_01_00_00_00.txt", "信息检查2026_01_01_00_00_00.json",
           "信息检查2026_01_01_00_00_00_摘要.txt"]
    new = ["信息检查2026_11_01_00_00_00.txt", "信息检查2026_11_01_00_00_00.json",
           "信息检查2026_11_01_00_00_00_摘要.md"]
    for name in old + new:
        w(root, name, (name + "\n").encode("utf-8"))
    w(root, "信息检查说明.txt", b"readme\n")
    w(root, "备份稿.aux", b"backup\n")
    w(root, "主稿.tex.bak", b"bak\n")
    w(root, "旧合并PDF.pdf", b"old-merged\n")
    w(root, "original/投稿.aux", b"author\n")
    w(root, "backup/old.aux", b"old\n")
    w(root, "历史稿/main.aux", b"history\n")
    w(root, "待核清单/note.txt", b"check\n")
    outside = os.path.join(d, "outside")
    os.makedirs(outside)
    w(outside, "keep.aux", b"outside-bytes\n")
    os.symlink(outside, os.path.join(root, "外部链接"))
    os.symlink(os.path.join(outside, "keep.aux"), os.path.join(root, "链接文件.aux"))

    expected = [
        ".toolbox-tmp/x.tmp",
        "Chinese Content_2026-01-01_00-00-00.tex",
        "main.aux",
        "main.bcf",
        "main.blg",
        "main.fls",
        "main.log",
        "main.synctex.gz",
        "main.toc",
        "信息检查2026_01_01_00_00_00.json",
        "信息检查2026_01_01_00_00_00.txt",
        "信息检查2026_01_01_00_00_00_摘要.txt",
        "目录结果.json",
    ]
    before = snap(root)
    scan = cd.scan_daily(root)
    check("scope.candidates_exact", cand_paths(scan) == expected,
          _bad(repr(cand_paths(scan))))
    kinds = kept_kind(scan)
    check("scope.latest_report_group_kept",
          all(kinds.get(n) == "info-report" for n in new), _bad(repr(kinds)))
    check("scope.nonstandard_report_kept", kinds.get("信息检查说明.txt") == "info-report")
    check("scope.protected_dirs_kept",
          kinds.get("backup") == "dir" and kinds.get("历史稿") == "dir"
          and kinds.get("待核清单") == "dir", _bad(repr(kinds)))
    check("scope.symlinks_kept",
          kinds.get("外部链接") == "symlink" and kinds.get("链接文件.aux") == "symlink")
    check("scope.backup_names_kept",
          kinds.get("备份稿.aux") == "backup" and kinds.get("主稿.tex.bak") == "backup")
    check("scope.skip_dirs_not_scanned",
          "original/投稿.aux" not in cand_paths(scan) and "original/投稿.aux" not in kinds)
    check("scope.scan_readonly", snap(root) == before and not os.path.exists(trash_path(root)))

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily", "--dry-run")
    check("scope.dry_run_rc", rc == 0, _bad(err))
    check("scope.dry_run_json",
          js.get("status") == "success" and js.get("dryRun") is True
          and js.get("count") == len(expected)
          and sorted(e["path"] for e in js.get("entries") or []) == expected
          and js.get("trashDir") == "" and js.get("operationId") == "", _bad(json.dumps(js)))
    check("scope.dry_run_no_write",
          snap(root) == before and not os.path.exists(trash_path(root)))

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("scope.actual_rc", rc == 0, _bad(err))
    check("scope.actual_json",
          js.get("status") == "success" and js.get("ok") is True
          and js.get("count") == len(expected)
          and sorted(e["path"] for e in js.get("entries") or []) == expected,
          _bad(json.dumps(js)))
    batch = js.get("trashDir") or ""
    check("scope.batch_created", bool(batch) and os.path.isdir(batch), _bad(batch))
    man = _manifest(batch) if batch else {}
    check("scope.manifest_daily",
          man.get("schema") == 2 and man.get("kind") == "daily"
          and man.get("status") == "success"
          and sorted(e["path"] for e in man.get("entries") or []) == expected,
          _bad(json.dumps(man)))
    after = snap(root)
    protected_ok = True
    for rel, val in before.items():
        if rel in expected:
            continue
        if after.get(rel) != val:
            protected_ok = False
            break
    check("scope.protected_bytes_unchanged", protected_ok, _bad("第一个不一致：" + rel))
    check("scope.old_reports_removed",
          all(not os.path.exists(os.path.join(root, n)) for n in old))
    check("scope.new_reports_kept",
          all(os.path.exists(os.path.join(root, n)) for n in new))
    check("scope.outside_untouched", open(os.path.join(outside, "keep.aux"), "rb").read() == b"outside-bytes\n")


# ── T1-2 引用闭包保护（含时间戳 tex 与 aux/bbl/out）─────────────────────


@test
def test_daily_protects_referenced_timestamp_tex_and_aux() -> None:
    d = tmpdir("refs")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex",
      b"\\documentclass{article}\n"
      b"\\input{sub/part}\n"
      b"\\includegraphics{pic}\n"
      b"\\input{refs.bbl}\n"
      b"\\input{custom.out}\n"
      b"\\input{contents_2026-01-01_00-00-00}\n"
      b"\\addbibresource{refs.bib}\n")
    w(root, "pic.pdf", b"pic\n")
    w(root, "refs.bib", b"@article{x}\n")
    w(root, "refs.bbl", b"bbl-ref\n")
    w(root, "custom.out", b"custom-out\n")
    w(root, "contents_2026-01-01_00-00-00.tex", b"gen-contents\n")
    w(root, "sub/part.tex", b"\\includegraphics{img}\n\\input{deep/leaf}\n")
    w(root, "sub/img.png", b"img\n")
    w(root, "sub/deep/leaf.tex", b"\\includegraphics{leafpic}\n\\input{leaf.aux}\n")
    w(root, "sub/deep/leafpic.png", b"leafpic\n")
    w(root, "sub/deep/leaf.aux", b"leaf-aux\n")
    w(root, "sub/deep/junk.aux", b"junk\n")
    w(root, "main.aux", b"main-aux\n")
    w(root, "main.log", b"This is pdfTeX, Version 3.14\n")
    w(root, "main.bbl", b"main-bbl\n")
    w(root, "Chinese Content_2026-02-02_00-00-00.tex", b"gen2\n")

    expected = [
        "Chinese Content_2026-02-02_00-00-00.tex",
        "main.aux",
        "main.bbl",
        "main.log",
        "sub/deep/junk.aux",
    ]
    before = snap(root)
    scan = cd.scan_daily(root)
    check("refs.no_scan_errors", scan["ok"] is True and scan["errors"] == [], _bad(repr(scan["errors"])))
    check("refs.candidates_exact", cand_paths(scan) == expected, _bad(repr(cand_paths(scan))))
    kinds = kept_kind(scan)
    referenced = ["contents_2026-01-01_00-00-00.tex", "custom.out", "refs.bbl",
                  "sub/deep/leaf.aux"]
    check("refs.referenced_kept",
          all(kinds.get(p) == "referenced" for p in referenced), _bad(repr(kinds)))
    reasons = {k["path"]: k["reason"] for k in scan["kept"]}
    prefix = "被 TeX 引用（保护）："
    bad_reasons = [p for p in referenced
                   if not reasons.get(p, "").startswith(prefix)
                   or reasons[p].count(prefix) != 1]
    check("refs.referenced_reason_single_prefix", bad_reasons == [],
          _bad(repr({p: reasons.get(p) for p in referenced})))

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("refs.cli_rc", rc == 0, _bad(err))
    check("refs.cli_count", js.get("count") == len(expected), _bad(json.dumps(js)))
    after = snap(root)
    check("refs.referenced_bytes_intact",
          all(after.get(p) == before.get(p) for p in referenced), _bad(repr(referenced)))
    check("refs.bib_and_graphics_intact",
          after.get("refs.bib") == before.get("refs.bib")
          and after.get("sub/img.png") == before.get("sub/img.png")
          and after.get("sub/deep/leafpic.png") == before.get("sub/deep/leafpic.png"))
    check("refs.unreferenced_moved",
          all(not os.path.exists(os.path.join(root, p)) for p in expected))


# ── T1-3 无法证明可再生则保留 ────────────────────────────────────────────


@test
def test_daily_preserves_unproven_regenerable_files() -> None:
    d = tmpdir("unproven")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "appendix.tex", b"appendix\n")
    w(root, "refs.bib", b"@book{y}\n")
    w(root, "appendix.bbl", b"proven\n")
    w(root, "sub/chapter.tex", b"chapter\n")
    w(root, "sub/chapter.aux", b"c-aux\n")
    w(root, "sub/chapter.log", b"This is XeTeX, Version 3.14\n")
    w(root, "sub/chapter.bbl", b"unproven-bbl\n")
    w(root, "sub/orphan.log", b"This is LuaTeX, Version 1.0\n")
    w(root, "sub/unknown.log", b"just a text file\n")
    w(root, "sub/custom.out", b"custom\n")
    w(root, "sub/stray.xyz", b"stray\n")
    w(root, "sub/备份稿.aux", b"backup\n")
    w(root, ".toolbox-tmp/keep.tmp", b"keep\n")
    w(root, ".toolbox-tmp/backup/inner.aux", b"inner\n")
    outside = os.path.join(d, "outside")
    os.makedirs(outside)
    w(outside, "keep.aux", b"outside\n")
    os.symlink(os.path.join(outside, "keep.aux"), os.path.join(root, ".toolbox-tmp/链接.aux"))
    w(root, "Chinese Content_2026-01-01_00-00-00.tex.bak", b"bak\n")

    expected = [".toolbox-tmp/keep.tmp", "appendix.bbl", "sub/chapter.aux", "sub/chapter.log"]
    before = snap(root)
    scan = cd.scan_daily(root)
    check("unproven.no_scan_errors", scan["ok"] is True, _bad(repr(scan["errors"])))
    check("unproven.candidates_exact", cand_paths(scan) == expected, _bad(repr(cand_paths(scan))))
    kinds = kept_kind(scan)
    check("unproven.bbl_without_source_kept", kinds.get("sub/chapter.bbl") == "aux")
    check("unproven.unknown_logs_kept",
          kinds.get("sub/orphan.log") == "log" and kinds.get("sub/unknown.log") == "log")
    check("unproven.custom_out_kept", kinds.get("sub/custom.out") == "aux")
    check("unproven.backup_subdir_kept", kinds.get(".toolbox-tmp/backup") == "dir")
    check("unproven.tmp_symlink_kept", kinds.get(".toolbox-tmp/链接.aux") == "symlink")
    check("unproven.timestamp_bak_kept",
          kinds.get("Chinese Content_2026-01-01_00-00-00.tex.bak") == "backup"
          and kinds.get("sub/备份稿.aux") == "backup")
    check("unproven.stray_suffix_untouched",
          "sub/stray.xyz" not in cand_paths(scan) and "sub/stray.xyz" not in kinds)

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("unproven.cli_rc", rc == 0, _bad(err))
    check("unproven.cli_count", js.get("count") == len(expected), _bad(json.dumps(js)))
    after = snap(root)
    check("unproven.kept_bytes_intact",
          all(after.get(p) == before.get(p) for p in kinds), _bad(repr(kinds)))


# ── T1-4 预检必须整体拒绝（扫描不完整 / 引用越界）───────────────────────


@test
def test_daily_rejects_incomplete_scan_and_boundary_escape() -> None:
    d = tmpdir("preflight")

    # (a) 引用缺失：整批拒绝、零写入
    root1 = os.path.join(d, "missing")
    os.makedirs(root1)
    w(root1, "main.tex", b"\\input{nope}\n")
    w(root1, "keep.aux", b"keep\n")
    before1 = snap(root1)
    rc, js, err = run_cli_json("--folder", root1, "--mode", "daily")
    check("preflight.missing_ref_rc", rc == 2, _bad(str(rc)))
    check("preflight.missing_ref_status",
          js.get("ok") is False and js.get("status") == "preflight_failed"
          and js.get("failures"), _bad(json.dumps(js)))
    check("preflight.missing_ref_zero_write",
          snap(root1) == before1 and not os.path.exists(trash_path(root1)))
    check("preflight.missing_ref_message",
          "引用缺失" in json.dumps(js, ensure_ascii=False) and "❌" in err, _bad(err))

    # (b) ../ 越界引用
    root2 = os.path.join(d, "escape")
    os.makedirs(root2)
    w(d, "outside/evil.tex", b"outside\n")
    w(root2, "main.tex", b"\\input{../outside/evil}\n")
    w(root2, "keep.aux", b"keep\n")
    before2 = snap(root2)
    outside_before = sha(os.path.join(d, "outside/evil.tex"))
    rc, js, err = run_cli_json("--folder", root2, "--mode", "daily")
    check("preflight.escape_ref_rc", rc == 2, _bad(str(rc)))
    check("preflight.escape_ref_rejected",
          js.get("status") == "preflight_failed"
          and "越界" in json.dumps(js, ensure_ascii=False), _bad(json.dumps(js)))
    check("preflight.escape_zero_write",
          snap(root2) == before2 and sha(os.path.join(d, "outside/evil.tex")) == outside_before)

    # (c) 绝对路径引用
    root3 = os.path.join(d, "absolute")
    os.makedirs(root3)
    w(root3, "main.tex", b"\\includegraphics{/tmp/clean_daily_abs_pic.png}\n")
    w(root3, "keep.aux", b"keep\n")
    before3 = snap(root3)
    rc, js, err = run_cli_json("--folder", root3, "--mode", "daily")
    check("preflight.absolute_ref_rc", rc == 2, _bad(str(rc)))
    check("preflight.absolute_ref_rejected",
          js.get("status") == "preflight_failed"
          and "绝对路径" in json.dumps(js, ensure_ascii=False), _bad(json.dumps(js)))
    check("preflight.absolute_zero_write", snap(root3) == before3)

    # (d) 扫描不完整（目录读取失败）→ scan 明确报错，且不写盘
    root4 = os.path.join(d, "lockedroot")
    os.makedirs(root4)
    w(root4, "main.tex", b"\\documentclass{article}\n")
    w(root4, "locked/x.aux", b"locked\n")
    w(root4, "keep.aux", b"keep\n")
    before4 = snap(root4)
    orig_scandir = cd.os.scandir

    def bad_scandir(path):
        if os.path.basename(os.path.abspath(str(path))) == "locked":
            raise OSError(13, "Permission denied")
        return orig_scandir(path)

    cd.os.scandir = bad_scandir
    try:
        scan4 = cd.scan_daily(root4)
    finally:
        cd.os.scandir = orig_scandir
    check("preflight.incomplete_scan_ok_false", scan4["ok"] is False, _bad(repr(scan4["errors"])))
    check("preflight.incomplete_scan_error",
          any("无法读取目录" in e.get("message", "") for e in scan4["errors"]),
          _bad(repr(scan4["errors"])))
    check("preflight.incomplete_scan_no_write",
          snap(root4) == before4 and not os.path.exists(trash_path(root4)))

    # (e) 符号链接目录不跟随、不越界
    root5 = os.path.join(d, "symlinkroot")
    os.makedirs(root5)
    w(root5, "main.tex", b"\\documentclass{article}\n")
    outside5 = os.path.join(d, "outside5")
    os.makedirs(outside5)
    w(outside5, "keep.aux", b"outside5\n")
    os.symlink(outside5, os.path.join(root5, "linkdir"))
    before5 = snap(root5)
    scan5 = cd.scan_daily(root5)
    check("preflight.symlink_not_followed",
          cand_paths(scan5) == [] and kept_kind(scan5).get("linkdir") == "symlink",
          _bad(repr(cand_paths(scan5)) + repr(kept_kind(scan5))))
    rc, js, err = run_cli_json("--folder", root5, "--mode", "daily")
    check("preflight.symlink_empty_rc", rc == 0 and js.get("status") == "empty", _bad(json.dumps(js)))
    check("preflight.symlink_zero_write", snap(root5) == before5
          and sha(os.path.join(outside5, "keep.aux")) == sha(os.path.join(outside5, "keep.aux")))


# ── T2-1 CLI dry-run 与实际路径集合一致 ─────────────────────────────────


@test
def test_daily_cli_dry_run_and_actual_paths_agree() -> None:
    d = tmpdir("agree")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "main.aux", b"aux\n")
    w(root, "main.log", b"This is XeTeX, Version 3.14\n")
    w(root, ".toolbox-tmp/a.tmp", b"tmp\n")
    w(root, "Chinese Content_2026-03-03_00-00-00.tex", b"gen\n")
    w(root, "目录结果.json", b"{}\n")
    for name in ("信息检查2026_01_01_00_00_00.txt", "信息检查2026_01_01_00_00_00.json",
                 "信息检查2026_01_01_00_00_00_摘要.txt"):
        w(root, name, b"old\n")
    for name in ("信息检查2026_11_01_00_00_00.txt", "信息检查2026_11_01_00_00_00.json",
                 "信息检查2026_11_01_00_00_00_摘要.md"):
        w(root, name, b"new\n")
    expected = [
        ".toolbox-tmp/a.tmp",
        "Chinese Content_2026-03-03_00-00-00.tex",
        "main.aux",
        "main.log",
        "信息检查2026_01_01_00_00_00.json",
        "信息检查2026_01_01_00_00_00.txt",
        "信息检查2026_01_01_00_00_00_摘要.txt",
        "目录结果.json",
    ]
    before = snap(root)

    rc, dry, err = run_cli_json("--folder", root, "--mode", "daily", "--dry-run")
    check("agree.dry_rc", rc == 0, _bad(err))
    check("agree.dry_json",
          dry.get("status") == "success" and dry.get("dryRun") is True
          and dry.get("trashDir") == "" and dry.get("operationId") == ""
          and dry.get("count") == len(expected), _bad(json.dumps(dry)))
    dry_paths = sorted(e["path"] for e in dry.get("entries") or [])
    check("agree.dry_paths", dry_paths == expected, _bad(repr(dry_paths)))
    check("agree.dry_zero_write", snap(root) == before and not os.path.exists(trash_path(root)))

    rc, actual, err = run_cli_json("--folder", root, "--mode", "daily")
    check("agree.actual_rc", rc == 0, _bad(err))
    actual_paths = sorted(e["path"] for e in actual.get("entries") or [])
    check("agree.actual_matches_dry", actual_paths == dry_paths, _bad(repr(actual_paths)))
    check("agree.actual_count", actual.get("count") == len(expected), _bad(json.dumps(actual)))
    batch = actual.get("trashDir") or ""
    check("agree.actual_batch_under_root",
          os.path.realpath(os.path.dirname(batch)) == os.path.realpath(trash_path(root)),
          _bad(batch))

    rc, restored, err = run_cli_json("--folder", root, "--restore", batch)
    check("agree.restore_rc", rc == 0, _bad(err))
    check("agree.restore_json",
          restored.get("mode") == "restore" and restored.get("status") == "success"
          and restored.get("count") == len(expected)
          and all(e["action"] == "restore" for e in restored.get("entries") or []),
          _bad(json.dumps(restored)))
    check("agree.restore_bytes_equal", snap(root) == before)


# ── T2-2 空候选不建批次；重复/同一秒两次批次互不覆盖 ────────────────────


@test
def test_daily_empty_repeat_and_same_second_batches() -> None:
    d = tmpdir("empty")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    before = snap(root)
    for i in range(2):
        rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
        check("empty.run%d_rc" % i, rc == 0, _bad(err))
        check("empty.run%d_json" % i,
              js.get("status") == "empty" and js.get("count") == 0
              and js.get("entries") == [] and js.get("trashDir") == "",
              _bad(json.dumps(js)))
    check("empty.no_batch_created",
          not os.path.exists(trash_path(root)) and snap(root) == before)

    # daily 明确拒绝会绕过撤销批次的旧开关
    for flag in ("--delete", "--trash", "--empty-trash", "--all-reports"):
        rc, out, err = run_cli("--folder", root, "--mode", "daily", flag)
        check("empty.rejects_" + flag.strip("-"), rc == 2 and "❌" in err and "daily" in err,
              _bad(err + out))
    check("empty.rejects_zero_write", snap(root) == before)

    # 同一秒内两次清理：operationId 相同也必须落在不同批次
    root2 = os.path.join(d, "same_second")
    os.makedirs(root2)
    w(root2, "main.tex", b"\\documentclass{article}\n")
    w(root2, "a.aux", b"A\n")
    w(root2, "b.aux", b"B\n")
    orig_op = cd._new_operation_id
    cd._new_operation_id = lambda: "2026_01_01_000000-deadbeef"
    try:
        first = cd.run_daily(root2)
        w(root2, "c.aux", b"C\n")
        w(root2, "d.aux", b"D\n")
        second = cd.run_daily(root2)
    finally:
        cd._new_operation_id = orig_op
    b1 = first.get("trashDir") or ""
    b2 = second.get("trashDir") or ""
    check("same_second.both_success",
          first.get("status") == "success" and second.get("status") == "success"
          and first.get("count") == 2 and second.get("count") == 2,
          _bad(json.dumps(first) + json.dumps(second)))
    check("same_second.distinct_batches",
          bool(b1) and bool(b2) and os.path.basename(b1) != os.path.basename(b2)
          and os.path.isdir(b1) and os.path.isdir(b2), _bad(b1 + " / " + b2))
    check("same_second.operation_id_shared",
          first.get("operationId") == second.get("operationId") == "2026_01_01_000000-deadbeef")
    m1 = _manifest(b1)
    m2 = _manifest(b2)
    check("same_second.manifests_complete",
          m1.get("schema") == 2 and m1.get("kind") == "daily"
          and m2.get("schema") == 2 and m2.get("kind") == "daily"
          and sorted(e["path"] for e in m1.get("entries") or []) == ["a.aux", "b.aux"]
          and sorted(e["path"] for e in m2.get("entries") or []) == ["c.aux", "d.aux"],
          _bad(json.dumps(m1) + json.dumps(m2)))


# ── T2-3 恢复所有字节与空目录 ───────────────────────────────────────────


@test
def test_daily_restore_all_bytes_and_empty_dirs() -> None:
    d = tmpdir("bytes")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "pic.pdf", b"pic\n")
    w(root, ".toolbox-tmp/t1.tmp", b"t1\n")
    w(root, "sub/deep/x.aux", b"X\x00\x01binary\n")
    w(root, "sub/y.tex", b"y\n")
    w(root, "sub/y.log", b"This is XeTeX, Version 3.14\n")
    w(root, "z.toc", b"z\n")
    mkdirs(root, "emptydir/inner")
    before = snap(root)

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("bytes.clean_rc", rc == 0, _bad(err))
    check("bytes.clean_count", js.get("count") == 4, _bad(json.dumps(js)))
    check("bytes.dirs_kept_after_move",
          os.path.isdir(os.path.join(root, ".toolbox-tmp"))
          and os.path.isdir(os.path.join(root, "sub/deep"))
          and os.path.isdir(os.path.join(root, "emptydir/inner")))
    check("bytes.files_gone",
          all(not os.path.exists(os.path.join(root, p)) for p in
              (".toolbox-tmp/t1.tmp", "sub/deep/x.aux", "sub/y.log", "z.toc")))

    rc, js, err = run_cli_json("--folder", root, "--restore", js.get("trashDir"))
    check("bytes.restore_rc", rc == 0, _bad(err))
    check("bytes.restore_count", js.get("count") == 4, _bad(json.dumps(js)))
    check("bytes.restore_exact_snapshot", snap(root) == before,
          _bad(repr(snap(root))))


# ── T2-4 部分失败：成功数准确 + 已搬可恢复 + 清单写失败 ────────────────


@test
def test_daily_partial_move_or_manifest_failure() -> None:
    d = tmpdir("partial")

    # (a) 清单在第一次搬移后落盘失败：partial，已搬项可恢复
    root_a = os.path.join(d, "manifest_fail")
    os.makedirs(root_a)
    w(root_a, "main.tex", b"\\documentclass{article}\n")
    w(root_a, "a.aux", b"AAA\n")
    w(root_a, "b.aux", b"BBB\n")
    orig_save = cd._save_manifest
    calls = [0]

    def flaky_save(trash, data):
        calls[0] += 1
        if calls[0] == 3:
            raise cd._ManifestWriteError("disk full")
        return orig_save(trash, data)

    cd._save_manifest = flaky_save
    try:
        res_a = cd.run_daily(root_a)
    finally:
        cd._save_manifest = orig_save
    check("partial.manifest_status", res_a.get("status") == "partial" and res_a.get("ok") is False,
          _bad(json.dumps(res_a)))
    check("partial.manifest_count", res_a.get("count") == 1, _bad(json.dumps(res_a)))
    check("partial.manifest_failures", len(res_a.get("failures") or []) == 1,
          _bad(json.dumps(res_a)))
    batch_a = res_a.get("trashDir") or ""
    man_a = _manifest(batch_a) if batch_a else {}
    check("partial.manifest_recoverable_manifest",
          man_a.get("status") == "partial"
          and [e["path"] for e in man_a.get("entries") or []] == ["a.aux"], _bad(json.dumps(man_a)))
    check("partial.manifest_other_file_kept",
          os.path.exists(os.path.join(root_a, "b.aux")))
    rc, js, err = run_cli_json("--folder", root_a, "--restore", batch_a)
    check("partial.manifest_restore_rc", rc == 0, _bad(err))
    check("partial.manifest_restore_count", js.get("count") == 1, _bad(json.dumps(js)))
    check("partial.manifest_restore_bytes",
          open(os.path.join(root_a, "a.aux"), "rb").read() == b"AAA\n")

    # (b) 单个文件搬移失败：partial，成功数准确，失败文件留在原处
    root_b = os.path.join(d, "move_fail")
    os.makedirs(root_b)
    w(root_b, "main.tex", b"\\documentclass{article}\n")
    w(root_b, "good.aux", b"GOOD\n")
    w(root_b, "bad.aux", b"BAD\n")
    orig_move = cd._move_into_trash

    def move_bad(src, dst):
        if os.path.basename(str(src)) == "bad.aux":
            raise OSError(13, "Permission denied")
        return orig_move(src, dst)

    cd._move_into_trash = move_bad
    try:
        res_b = cd.run_daily(root_b)
    finally:
        cd._move_into_trash = orig_move
    failures_b = res_b.get("failures") or []
    check("partial.move_status_count",
          res_b.get("status") == "partial" and res_b.get("count") == 1,
          _bad(json.dumps(res_b)))
    check("partial.move_failure_named",
          any("bad.aux" in f.get("path", "") for f in failures_b), _bad(json.dumps(res_b)))
    check("partial.move_bad_left_in_place",
          open(os.path.join(root_b, "bad.aux"), "rb").read() == b"BAD\n"
          and not os.path.exists(os.path.join(res_b.get("trashDir") or "", "bad.aux")))
    rc, js, err = run_cli_json("--folder", root_b, "--restore", res_b.get("trashDir"))
    check("partial.move_restore_rc", rc == 0, _bad(err))
    check("partial.move_restore_bytes",
          open(os.path.join(root_b, "good.aux"), "rb").read() == b"GOOD\n")

    # (c) 同一失败经 CLI 输出：rc 非 0 + ok=false + status=partial
    root_c = os.path.join(d, "cli_partial")
    os.makedirs(root_c)
    w(root_c, "main.tex", b"\\documentclass{article}\n")
    w(root_c, "a.aux", b"A\n")
    w(root_c, "b.aux", b"B\n")
    calls_c = [0]

    def flaky_c(trash, data):
        calls_c[0] += 1
        if calls_c[0] == 3:
            raise cd._ManifestWriteError("disk full")
        return orig_save(trash, data)

    cd._save_manifest = flaky_c
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            rc_c = ct.main(["--folder", root_c, "--mode", "daily", "--json"])
    finally:
        cd._save_manifest = orig_save
    js_c = _json_last(out_buf.getvalue())
    check("partial.cli_rc", rc_c == 2, _bad(str(rc_c) + " " + err_buf.getvalue()))
    check("partial.cli_json",
          js_c.get("ok") is False and js_c.get("status") == "partial"
          and js_c.get("count") == 1 and bool(js_c.get("trashDir")),
          _bad(json.dumps(js_c)))
    rc, js, err = run_cli_json("--folder", root_c, "--restore", js_c.get("trashDir"))
    check("partial.cli_restore_rc", rc == 0 and js.get("status") == "success", _bad(err + json.dumps(js)))


# ── C08 部分失败：回收区不得留未记账副本 ────────────────────────────────


@test
def test_daily_partial_move_leaves_no_unaccounted_trash_copies() -> None:
    """chmod 0555 源子目录 → rename 被拒、copy2 成功但 unlink 失败。

    修复前 shutil.move 会把副本留在批次里（payload > entries）；
    修复后失败项必须回滚干净：count==moved==payload，恢复后源树一致。
    """
    d = tmpdir("c08")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "good.aux", b"GOOD\n")
    w(root, "ro/a.aux", b"RO-A\n")
    w(root, "ro/b.aux", b"RO-B\n")
    ro = os.path.join(root, "ro")
    before = snap(root)

    os.chmod(ro, 0o555)
    try:
        if os.access(ro, os.W_OK):
            print("SKIP  c08.chmod_ineffective（当前用户可忽略 0555 目录权限，无法构造回退搬移场景）")
            return
        rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    finally:
        os.chmod(ro, 0o755)

    check("c08.rc_partial", rc == 2, _bad(str(rc) + " " + err))
    check("c08.status_partial", js.get("status") == "partial" and js.get("ok") is False,
          _bad(json.dumps(js)))
    fail_paths = sorted(f.get("path", "") for f in (js.get("failures") or []))
    check("c08.failures_named", fail_paths == ["ro/a.aux", "ro/b.aux"], _bad(repr(fail_paths)))
    batch = js.get("trashDir") or ""
    check("c08.batch_present", bool(batch) and os.path.isdir(batch), _bad(batch))
    man = _manifest(batch) if batch else {}
    moved = [e.get("path") for e in (man.get("entries") or []) if e.get("status") == "moved"]
    payload = batch_payload(batch) if batch else []
    check("c08.status_in_manifest", man.get("status") == "partial", _bad(json.dumps(man)))
    check("c08.count_eq_moved_eq_entries",
          js.get("count") == len(moved) == 1 and moved == ["good.aux"],
          _bad("count=%r moved=%r" % (js.get("count"), moved)))
    check("c08.payload_eq_moved", payload == moved, _bad(repr(payload)))
    check("c08.failed_no_trash_copy",
          all(p not in payload for p in ("ro/a.aux", "ro/b.aux")), _bad(repr(payload)))
    check("c08.failed_left_in_place",
          open(os.path.join(root, "ro", "a.aux"), "rb").read() == b"RO-A\n"
          and open(os.path.join(root, "ro", "b.aux"), "rb").read() == b"RO-B\n"
          and not os.path.exists(os.path.join(batch, "ro")), _bad(repr(payload)))

    rc2, js2, err2 = run_cli_json("--folder", root, "--restore", batch)
    check("c08.restore_rc", rc2 == 0 and js2.get("status") == "success",
          _bad(err2 + json.dumps(js2)))
    check("c08.restore_count", js2.get("count") == 1, _bad(json.dumps(js2)))
    check("c08.restore_bytes",
          open(os.path.join(root, "good.aux"), "rb").read() == b"GOOD\n")
    check("c08.restore_tree_identical", snap(root) == before, _bad(repr(snap(root))))
    check("c08.batch_no_payload_after_restore",
          batch_payload(batch) == [], _bad(repr(batch_payload(batch))))


# ── T2-5 中断（SIGTERM）可恢复 ──────────────────────────────────────────


@test
def test_daily_interrupted_move_is_recoverable() -> None:
    d = tmpdir("interrupt")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "one.aux", b"ONE\n")
    w(root, "two.aux", b"TWO\n")
    obs: dict = {}
    default_handler = signal.getsignal(signal.SIGTERM)
    orig_move = cd._move_into_trash

    def move_then_term(src, dst):
        if not obs:
            parent = trash_path(root)
            names = [n for n in os.listdir(parent) if os.path.isdir(os.path.join(parent, n))]
            batch = os.path.join(parent, names[0])
            man = _manifest(batch)
            obs["batch"] = batch
            obs["status"] = man.get("status")
            obs["entry_statuses"] = sorted(str(e.get("status")) for e in man.get("entries") or [])
            obs["intent_len"] = len(man.get("intent") or [])
            orig_move(src, dst)
            handler = signal.getsignal(signal.SIGTERM)
            obs["handler_installed"] = handler is not signal.SIG_DFL
            if handler is signal.SIG_DFL:
                cd.request_stop()
            else:
                os.kill(os.getpid(), signal.SIGTERM)
        else:
            raise AssertionError("取消后不应继续搬移第二个文件")

    cd._move_into_trash = move_then_term
    try:
        res = cd.run_daily(root)
    finally:
        cd._move_into_trash = orig_move

    check("interrupt.manifest_persisted_before_move",
          bool(obs) and obs.get("status") == "planned"
          and "moving" in (obs.get("entry_statuses") or [])
          and obs.get("intent_len") == 2, _bad(repr(obs)))
    check("interrupt.stop_handler_installed", obs.get("handler_installed") is True, _bad(repr(obs)))
    check("interrupt.status_cancelled",
          res.get("status") == "cancelled" and res.get("ok") is False
          and res.get("count") == 1 and "已停止" in (res.get("error") or ""),
          _bad(json.dumps(res)))
    batch = res.get("trashDir") or ""
    man = _manifest(batch) if batch else {}
    check("interrupt.manifest_cancelled",
          man.get("status") == "cancelled"
          and [e["status"] for e in man.get("entries") or []] == ["moved"]
          and len(man.get("intent") or []) == 2, _bad(json.dumps(man)))
    check("interrupt.unmoved_kept",
          os.path.exists(os.path.join(root, "two.aux")) and not os.path.exists(os.path.join(batch, "two.aux")))
    check("interrupt.handler_restored", signal.getsignal(signal.SIGTERM) is default_handler)
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("interrupt.restore_rc", rc == 0, _bad(err))
    check("interrupt.restore_count_and_bytes",
          js.get("count") == 1 and open(os.path.join(root, "one.aux"), "rb").read() == b"ONE\n",
          _bad(json.dumps(js)))


# ── T2-6 恢复冲突/重试与清单边界 ────────────────────────────────────────


@test
def test_daily_restore_conflict_retry_and_manifest_boundary() -> None:
    d = tmpdir("restore")

    # (a) 目标已存在 → 不覆盖；删掉冲突后可重试成功
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "a.aux", b"AAA\n")
    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("conflict.clean_rc", rc == 0, _bad(err))
    batch = js.get("trashDir") or ""
    w(root, "a.aux", b"NEW\n")
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("conflict.rc", rc == 2, _bad(str(rc)))
    check("conflict.partial",
          js.get("status") == "partial" and js.get("ok") is False and js.get("count") == 0,
          _bad(json.dumps(js)))
    check("conflict.no_overwrite",
          open(os.path.join(root, "a.aux"), "rb").read() == b"NEW\n")
    check("conflict.message_and_retryable",
          "不覆盖" in json.dumps(js, ensure_ascii=False)
          and os.path.exists(os.path.join(batch, "a.aux")), _bad(json.dumps(js)))
    os.remove(os.path.join(root, "a.aux"))
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("conflict.retry_rc", rc == 0, _bad(err))
    check("conflict.retry_success_bytes",
          js.get("status") == "success" and js.get("count") == 1
          and open(os.path.join(root, "a.aux"), "rb").read() == b"AAA\n",
          _bad(json.dumps(js)))

    # (b) 边界：跨 root / 路径穿越 / 绝对路径 / 批次不在回收区 → 预检拒绝且零写入
    root_b = os.path.join(d, "root_b")
    os.makedirs(root_b)
    w(root_b, "main.tex", b"\\documentclass{article}\n")
    w(root_b, "keep.aux", b"keep\n")

    cross = make_batch(root_b, "op-cross", {
        "schema": 2, "root": "/tmp/not-this-root", "createdAt": "x",
        "mode": "daily", "kind": "daily", "operationId": "op-cross", "status": "success",
        "entries": [{"path": "x.aux", "type": "file", "status": "moved"}],
    }, {"x.aux": b"x\n"})
    before_b = snap(root_b)
    res = cd.restore_daily(root_b, cross)
    check("boundary.cross_root_rejected",
          res.get("status") == "preflight_failed"
          and "不属于该目录" in (res.get("error") or ""), _bad(json.dumps(res)))
    check("boundary.cross_root_zero_write",
          snap(root_b) == before_b and not os.path.exists(os.path.join(root_b, "x.aux")))

    w(d, "evil.aux", b"EVIL\n")
    evil_sha = sha(os.path.join(d, "evil.aux"))
    trav = make_batch(root_b, "op-trav", {
        "schema": 2, "root": root_b, "createdAt": "x", "mode": "daily", "kind": "daily",
        "operationId": "op-trav", "status": "success",
        "entries": [{"path": "../evil.aux", "type": "file", "status": "moved"}],
    })
    res = cd.restore_daily(root_b, trav)
    check("boundary.traversal_rejected",
          res.get("status") == "preflight_failed"
          and "路径穿越" in json.dumps(res, ensure_ascii=False), _bad(json.dumps(res)))
    check("boundary.traversal_zero_write",
          sha(os.path.join(d, "evil.aux")) == evil_sha and snap(root_b) == before_b)

    absb = make_batch(root_b, "op-abs", {
        "schema": 2, "root": root_b, "createdAt": "x", "mode": "daily", "kind": "daily",
        "operationId": "op-abs", "status": "success",
        "entries": [{"path": "/tmp/clean_daily_abs_evil.aux", "type": "file", "status": "moved"}],
    })
    res = cd.restore_daily(root_b, absb)
    check("boundary.absolute_rejected",
          res.get("status") == "preflight_failed"
          and "绝对路径" in json.dumps(res, ensure_ascii=False), _bad(json.dumps(res)))

    other = os.path.join(d, "outside_batch")
    os.makedirs(other)
    w(other, "outside.aux", b"outside\n")
    with open(os.path.join(other, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "schema": 2, "root": root_b, "createdAt": "x", "mode": "daily",
            "kind": "daily", "operationId": "op-out", "status": "success",
            "entries": [{"path": "outside.aux", "type": "file", "status": "moved"}],
        }, fh, ensure_ascii=False)
    res = cd.restore_daily(root_b, other)
    check("boundary.outside_trash_rejected",
          res.get("status") == "preflight_failed"
          and "不在" in (res.get("error") or ""), _bad(json.dumps(res)))
    check("boundary.outside_trash_zero_write",
          snap(root_b) == before_b and not os.path.exists(os.path.join(root_b, "outside.aux")))

    # (c) 旧 v1 与通用 v2 清单仍可恢复
    v1 = make_batch(root_b, "op-v1", {"files": ["v1.aux"]}, {"v1.aux": b"V1\n"})
    res = cd.restore_daily(root_b, v1)
    check("boundary.v1_restored",
          res.get("status") == "success" and res.get("count") == 1
          and open(os.path.join(root_b, "v1.aux"), "rb").read() == b"V1\n",
          _bad(json.dumps(res)))
    v2 = make_batch(root_b, "op-v2", {
        "schema": 2, "root": root_b, "createdAt": "x", "mode": "aux",
        "entries": [{"path": "v2.aux", "type": "file"}],
    }, {"v2.aux": b"V2\n"})
    res = cd.restore_daily(root_b, v2)
    check("boundary.v2_restored",
          res.get("status") == "success" and res.get("count") == 1
          and open(os.path.join(root_b, "v2.aux"), "rb").read() == b"V2\n",
          _bad(json.dumps(res)))


# ── R01–R08 返工反例（冻结基线 a6b0390）──────────────────────────────


@test
def test_rework_r01_trash_symlink_and_batch_boundary() -> None:
    d = tmpdir("rework_r01")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "a.aux", b"A\n")
    outside = os.path.join(d, "outside")
    os.makedirs(outside)
    os.symlink(outside, trash_path(root))
    before = snap(root)

    res = cd.run_daily(root)
    check("r01.run_preflight_failed",
          res.get("status") == "preflight_failed" and res.get("count") == 0
          and res.get("ok") is False, _bad(json.dumps(res)))
    check("r01.run_zero_write_and_no_escape",
          os.path.exists(os.path.join(root, "a.aux")) and not os.listdir(outside)
          and snap(root) == before, _bad(json.dumps(res)))

    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("r01.cli_preflight",
          rc == 2 and js.get("ok") is False and js.get("status") == "preflight_failed"
          and js.get("count") == 0, _bad(err + json.dumps(js)))
    check("r01.cli_zero_write_and_no_escape",
          os.path.exists(os.path.join(root, "a.aux")) and not os.listdir(outside)
          and snap(root) == before)

    # 批次父目录是符号链接：恢复同样必须在写入前拒绝（R01/R06）。
    batch = make_batch(root, "op-r01", {
        "schema": 2, "root": root, "createdAt": "x", "mode": "daily",
        "kind": "daily", "operationId": "op-r01", "status": "success",
        "entries": [{"path": "x.aux", "type": "file", "status": "moved"}],
    }, {"x.aux": b"X\n"})
    check("r01.batch_created_through_link", os.path.exists(os.path.join(batch, "x.aux")))
    res = cd.restore_daily(root, batch)
    check("r01.restore_rejects_linked_parent",
          res.get("status") == "preflight_failed"
          and "符号链接" in (res.get("error") or ""), _bad(json.dumps(res)))
    check("r01.restore_zero_write",
          not os.path.exists(os.path.join(root, "x.aux"))
          and os.path.exists(os.path.join(batch, "x.aux"))
          and snap(root) == before)
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r01.restore_cli_preflight",
          rc == 2 and js.get("ok") is False and js.get("status") == "preflight_failed"
          and js.get("count") == 0, _bad(err + json.dumps(js)))
    check("r01.restore_cli_zero_write",
          not os.path.exists(os.path.join(root, "x.aux"))
          and os.path.exists(os.path.join(batch, "x.aux")))


@test
def test_rework_r02_unbraced_input_is_protected() -> None:
    d = tmpdir("rework_r02")
    root = os.path.join(d, "root")
    os.makedirs(root)
    ts = "contents_2026-01-01_00-00-00.tex"
    w(root, "main.tex",
      b"\\documentclass{article}\n\\input contents_2026-01-01_00-00-00.tex\n")
    w(root, ts, b"\\section{x}\n")
    scan = cd.scan_daily(root)
    check("r02.scan_ok", scan.get("ok") is True, _bad(json.dumps(scan)))
    check("r02.no_candidate", ts not in cand_paths(scan), _bad(str(cand_paths(scan))))
    check("r02.referenced_kept", kept_kind(scan).get(ts) == "referenced",
          _bad(json.dumps(kept_kind(scan))))
    res = cd.run_daily(root)
    check("r02.run_empty_and_file_kept",
          res.get("status") == "empty" and res.get("count") == 0
          and os.path.exists(os.path.join(root, ts)), _bad(json.dumps(res)))
    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("r02.cli_empty", rc == 0 and js.get("status") == "empty"
          and js.get("count") == 0, _bad(err + json.dumps(js)))
    check("r02.cli_file_kept", os.path.exists(os.path.join(root, ts)))


@test
def test_rework_r03_r04_reference_bases_and_aliases() -> None:
    d = tmpdir("rework_r03")
    # R03：文章目录优先，但同名多来源全部保护（不猜错后删除）。
    root = os.path.join(d, "r03")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n\\input{article/main}\n")
    w(root, "piece.aux", b"ROOT\n")
    w(root, "article/main.tex", b"\\documentclass{article}\n\\input{piece.aux}\n")
    w(root, "article/piece.aux", b"ART\n")
    scan = cd.scan_daily(root)
    kinds = kept_kind(scan)
    check("r03.multi_source_no_candidate", cand_paths(scan) == [],
          _bad(str(cand_paths(scan))))
    check("r03.article_dir_source_kept", kinds.get("article/piece.aux") == "referenced",
          _bad(json.dumps(kinds)))
    check("r03.root_source_kept", kinds.get("piece.aux") == "referenced",
          _bad(json.dumps(kinds)))
    res = cd.run_daily(root)
    check("r03.run_empty_both_kept",
          res.get("status") == "empty"
          and os.path.exists(os.path.join(root, "article/piece.aux"))
          and os.path.exists(os.path.join(root, "piece.aux")), _bad(json.dumps(res)))

    # R04：别名指向根内目标 → 保护别名与真实目标；指向根外 → 只保护别名，不跟随。
    root2 = os.path.join(d, "r04")
    os.makedirs(root2)
    w(root2, "used.aux", b"USED\n")
    os.symlink("used.aux", os.path.join(root2, "alias.aux"))
    outside = os.path.join(d, "outside")
    os.makedirs(outside)
    w(d, "ext.aux", b"EXT\n")
    os.symlink(os.path.join(d, "ext.aux"), os.path.join(root2, "extlink.aux"))
    w(root2, "main.tex",
      b"\\documentclass{article}\n\\input{alias.aux}\n\\input{extlink.aux}\n")
    scan = cd.scan_daily(root2)
    kinds = kept_kind(scan)
    check("r04.scan_ok", scan.get("ok") is True, _bad(json.dumps(scan)))
    check("r04.candidate_empty", cand_paths(scan) == [], _bad(str(cand_paths(scan))))
    check("r04.alias_target_kept", kinds.get("used.aux") == "referenced",
          _bad(json.dumps(kinds)))
    check("r04.alias_kept", kinds.get("alias.aux") == "symlink", _bad(json.dumps(kinds)))
    check("r04.external_alias_kept", kinds.get("extlink.aux") == "symlink",
          _bad(json.dumps(kinds)))
    res = cd.run_daily(root2)
    check("r04.run_empty_no_move",
          res.get("status") == "empty"
          and open(os.path.join(root2, "used.aux"), "rb").read() == b"USED\n"
          and os.path.exists(os.path.join(d, "ext.aux")), _bad(json.dumps(res)))


@test
def test_rework_r05_tmp_backup_priority() -> None:
    d = tmpdir("rework_r05")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, ".toolbox-tmp/final.tex.bak", b"BACKUP\n")
    w(root, ".toolbox-tmp/scratch.aux", b"SCRATCH\n")
    scan = cd.scan_daily(root)
    kinds = kept_kind(scan)
    check("r05.tmp_bak_kept", kinds.get(".toolbox-tmp/final.tex.bak") == "backup",
          _bad(json.dumps(kinds)))
    check("r05.tmp_scratch_candidate", ".toolbox-tmp/scratch.aux" in cand_paths(scan),
          _bad(str(cand_paths(scan))))
    res = cd.run_daily(root)
    check("r05.run_moves_scratch_keeps_bak",
          res.get("status") == "success" and res.get("count") == 1
          and os.path.exists(os.path.join(root, ".toolbox-tmp/final.tex.bak"))
          and not os.path.exists(os.path.join(root, ".toolbox-tmp/scratch.aux")),
          _bad(json.dumps(res)))


@test
def test_rework_r06_legacy_restore_validation_cli() -> None:
    d = tmpdir("rework_r06")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(d, "evil.aux", b"EVIL\n")

    # v1 路径穿越：统一 preflight 拒绝、零写入、批次保留。
    v1_bad = make_batch(root, "op-v1-bad", {"files": ["../evil.aux"]})
    before = snap(root)
    rc, js, err = run_cli_json("--folder", root, "--restore", v1_bad)
    check("r06.v1_traversal_rejected",
          rc == 2 and js.get("ok") is False and js.get("status") == "preflight_failed"
          and js.get("count") == 0 and "穿越" in (js.get("error") or ""),
          _bad(err + json.dumps(js)))
    check("r06.v1_traversal_zero_write",
          not os.path.exists(os.path.join(root, "evil.aux")) and snap(root) == before
          and os.path.isdir(v1_bad))

    # v1 绝对路径：同样拒绝。
    abs_bad = make_batch(root, "op-v1-abs", {"files": [os.path.join(d, "evil.aux")]})
    rc, js, err = run_cli_json("--folder", root, "--restore", abs_bad)
    check("r06.v1_absolute_rejected",
          rc == 2 and js.get("status") == "preflight_failed"
          and "绝对路径" in (js.get("error") or ""), _bad(err + json.dumps(js)))

    # v1 来源越界（批次内符号链接指向根外）：不跟随、拒绝。
    link_bad = make_batch(root, "op-v1-link", {"files": ["leak.aux"]})
    os.symlink(os.path.join(d, "evil.aux"), os.path.join(link_bad, "leak.aux"))
    rc, js, err = run_cli_json("--folder", root, "--restore", link_bad)
    check("r06.v1_source_escape_rejected",
          rc == 2 and js.get("status") == "preflight_failed"
          and "来源越界" in (js.get("error") or ""), _bad(err + json.dumps(js)))
    check("r06.v1_source_escape_zero_write",
          not os.path.exists(os.path.join(root, "leak.aux")))

    # 有效 v1 / v2 批次：全部 restore 入口仍兼容。
    v1 = make_batch(root, "op-v1", {"files": ["v1.aux"]}, {"v1.aux": b"V1\n"})
    rc, js, err = run_cli_json("--folder", root, "--restore", v1)
    check("r06.v1_valid_restored",
          rc == 0 and js.get("ok") is True and js.get("status") == "success"
          and js.get("count") == 1
          and open(os.path.join(root, "v1.aux"), "rb").read() == b"V1\n",
          _bad(err + json.dumps(js)))
    v2 = make_batch(root, "op-v2", {
        "schema": 2, "root": root, "createdAt": "x", "mode": "aux",
        "entries": [{"path": "v2.aux", "type": "file"}],
    }, {"v2.aux": b"V2\n"})
    rc, js, err = run_cli_json("--folder", root, "--restore", v2)
    check("r06.v2_valid_restored",
          rc == 0 and js.get("status") == "success" and js.get("count") == 1
          and open(os.path.join(root, "v2.aux"), "rb").read() == b"V2\n",
          _bad(err + json.dumps(js)))


@test
def test_rework_r07_broken_manifest_preflight_retry() -> None:
    d = tmpdir("rework_r07")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    batch = os.path.join(trash_path(root), "op-bad")
    os.makedirs(batch)
    w(batch, "x.aux", b"X\n")
    with open(os.path.join(batch, "manifest.json"), "wb") as fh:
        fh.write(b"{not json")
    before = snap(root)

    res = cd.restore_daily(root, batch)
    check("r07.direct_corrupt_preflight",
          res.get("status") == "preflight_failed" and res.get("count") == 0
          and res.get("ok") is False and "清单" in (res.get("error") or ""),
          _bad(json.dumps(res)))
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r07.cli_corrupt_preflight",
          rc == 2 and js.get("ok") is False and js.get("status") == "preflight_failed"
          and js.get("count") == 0, _bad(err + json.dumps(js)))
    check("r07.corrupt_batch_preserved",
          batch_payload(batch) == ["x.aux"] and not os.path.exists(os.path.join(root, "x.aux"))
          and snap(root) == before)

    missing = os.path.join(trash_path(root), "op-missing")
    os.makedirs(missing)
    w(missing, "y.aux", b"Y\n")
    rc, js, err = run_cli_json("--folder", root, "--restore", missing)
    check("r07.missing_manifest_preflight",
          rc == 2 and js.get("status") == "preflight_failed"
          and js.get("count") == 0, _bad(err + json.dumps(js)))
    check("r07.missing_batch_preserved",
          batch_payload(missing) == ["y.aux"] and not os.path.exists(os.path.join(root, "y.aux")))

    # 修复清单后同一批次仍可恢复（预检失败不消耗批次）。
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "schema": 2, "root": root, "createdAt": "x", "mode": "daily",
            "kind": "daily", "operationId": "op-bad", "status": "success",
            "entries": [{"path": "x.aux", "type": "file", "status": "moved"}],
        }, fh, ensure_ascii=False)
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r07.retry_after_repair_success",
          rc == 0 and js.get("status") == "success" and js.get("count") == 1
          and open(os.path.join(root, "x.aux"), "rb").read() == b"X\n",
          _bad(err + json.dumps(js)))


@test
def test_rework_r08_restore_ledger_and_retry() -> None:
    d = tmpdir("rework_r08")
    root = os.path.join(d, "root")
    os.makedirs(root)
    w(root, "main.tex", b"\\documentclass{article}\n")
    w(root, "a.aux", b"AAA\n")
    w(root, "b.aux", b"BBB\n")
    rc, js, err = run_cli_json("--folder", root, "--mode", "daily")
    check("r08.clean_rc", rc == 0 and js.get("count") == 2, _bad(err + json.dumps(js)))
    batch = js.get("trashDir") or ""

    w(root, "b.aux", b"NEW\n")
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r08.first_conflict_partial",
          rc == 2 and js.get("status") == "partial" and js.get("ok") is False
          and js.get("count") == 1 and js.get("alreadyRestored") == 0,
          _bad(err + json.dumps(js)))
    check("r08.first_conflict_skipped",
          [s.get("path") for s in (js.get("skipped") or [])] == ["b.aux"]
          and "不覆盖" in json.dumps(js, ensure_ascii=False), _bad(json.dumps(js)))
    man = _manifest(batch)
    states = {e.get("path"): e.get("status") for e in man.get("entries") or []}
    check("r08.first_ledger_persisted",
          states.get("a.aux") == "restored" and states.get("b.aux") == "moved",
          _bad(json.dumps(man, ensure_ascii=False)))
    check("r08.first_no_overwrite",
          open(os.path.join(root, "a.aux"), "rb").read() == b"AAA\n"
          and open(os.path.join(root, "b.aux"), "rb").read() == b"NEW\n")

    os.remove(os.path.join(root, "b.aux"))
    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r08.retry_success",
          rc == 0 and js.get("status") == "success" and js.get("count") == 1
          and js.get("alreadyRestored") == 1 and js.get("skipped") == [],
          _bad(err + json.dumps(js)))
    check("r08.retry_no_missing_warning",
          "Missing" not in json.dumps(js, ensure_ascii=False)
          and open(os.path.join(root, "b.aux"), "rb").read() == b"BBB\n",
          _bad(json.dumps(js)))
    check("r08.retry_summary",
          (js.get("summary") or {}).get("restored") == 1
          and (js.get("summary") or {}).get("alreadyRestored") == 1
          and (js.get("summary") or {}).get("skipped") == 0, _bad(json.dumps(js)))

    rc, js, err = run_cli_json("--folder", root, "--restore", batch)
    check("r08.third_empty_already_all",
          rc == 0 and js.get("status") == "empty" and js.get("count") == 0
          and js.get("alreadyRestored") == 2 and js.get("skipped") == [],
          _bad(err + json.dumps(js)))
    man = _manifest(batch)
    check("r08.all_entries_restored_in_manifest",
          all(e.get("status") == "restored" for e in man.get("entries") or [])
          and "Missing" not in json.dumps(js, ensure_ascii=False),
          _bad(json.dumps(man, ensure_ascii=False)))


def main() -> int:
    print("清理页 daily 测试（T1+T2）：真实临时目录 + 真 CLI")
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            check("test." + fn.__name__ + ".completed", False, repr(exc))
    cleanup_all()
    print()
    print("SUMMARY total=%d pass=%d fail=%d" % (TOTAL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
