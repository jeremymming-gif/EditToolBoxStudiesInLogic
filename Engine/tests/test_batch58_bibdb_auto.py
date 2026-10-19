#!/usr/bin/env python3
r"""第五十八批：初排转换**默认只读引用库比对**（+ `--no-bibdb-verify`）的判据测试。

Lead 2026-10-20 拍板口径（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 默认开：无新参数时自动做一次只读比对，**只打一行汇总**（命中/候选/未命中），
    不改 reference.bib / .tex / 任务书；
  · `--no-bibdb-verify` / `--no-bibdb` 关闭；无引用库时**完全静默**；
  · `--verify-refs` / `--verify-plan` 行为不变、也不重复打印默认汇总行；
  · 产物与 HEAD（`git archive HEAD` 旧引擎）逐字节相同；无库时**日志也**逐字节相同。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch58_bibdb_auto.py
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0

PUBLISHED = {"kind": bibdb.KIND_PUBLISHED, "issue": "2025年第6期(85)",
             "slug": "01 Test_Author", "file": "reference.bib"}

_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}，并引用一条库里没有的\parencite{New2024}。

\printbibliography
\end{document}
"""

_BIB = """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
}

@article{KeyHitOnly,
  author = {Doe, Jane},
  title  = {A Totally Different Manuscript Title About Other Things},
  year   = {1999},
}

@article{New2024,
  author = {New, Nina},
  title  = {A Work Not In The Library At All},
  year   = {2024},
}
"""


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"SKIP  {name}  {why}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot(folder: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = sha(p)
    return out


def build_library(tmp: str) -> str:
    """合成引用库：Quine1970（已核实）/ KeyHitOnly（库内同名键但不同作品）。"""
    notes = os.path.join(tmp, "notes")
    os.makedirs(os.path.join(notes, "cases"), exist_ok=True)   # notes_root 判据
    path = os.path.join(notes, "bib_db", "bibliography.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text("""@article{Quine1970,
  author  = {Quine, Willard Van Orman},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}

@article{KeyHitOnly,
  author = {Doe, Jane},
  title  = {A Library Title That Differs Greatly From The Manuscript},
  year   = {1999},
}
""", PUBLISHED, verified=True)
    store.save()
    return path


def run_convert(out_dir: str, extra: list[str], *, env: dict, engine: str = _ENGINE,
                source: str, bib: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"),
         "--source", source, "--out", out_dir, "--bib", bib, "--name", "Test_Author",
         "--author", "张三", "--lang", "zh", "--no-original"] + extra,
        # cwd 固定为当前 Engine（新旧引擎都从同一处起算相对路径），否则日志里的
        # `../../…` 层数会因 cwd 不同而非「只多一行汇总」。
        cwd=_ENGINE, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def make_env(notes: str | None) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    if notes:
        env["SLCN_NOTES"] = notes
    return env


def ensure_head_engine(tmp: str) -> str:
    """`git archive HEAD` 解到临时目录 → 旧引擎路径（对照用）。失败返回空串（SKIP）。"""
    dest = os.path.join(tmp, "head")
    try:
        tar = subprocess.run(["git", "-C", _REPO, "archive", "HEAD"],
                             capture_output=True, timeout=120)
        if tar.returncode != 0:
            return ""
        os.makedirs(dest, exist_ok=True)
        p = os.path.join(tmp, "head.tar")
        with open(p, "wb") as f:
            f.write(tar.stdout)
        with tarfile.open(p) as tf:
            tf.extractall(dest)
        if not os.path.isfile(os.path.join(dest, "Engine", "convert.py")):
            return ""
        return os.path.join(dest, "Engine")
    except Exception:  # noqa: BLE001
        return ""


_SUMMARY_RE = re.compile(r"引用库比对：命中 (\d+) / 候选 (\d+) / 未命中 (\d+)（只读，不改产物）")


def _strip_summary(stdout: str) -> str:
    # 用 split("\n")（不是 splitlines）保留结尾换行，才能与 HEAD stdout 逐字节比较。
    return "\n".join(ln for ln in stdout.split("\n") if "引用库比对：" not in ln)


def _same_run_pair(tmp: str, tag: str, extra: list[str], *, env: dict,
                   source: str, bib: str, head: str,
                   head_extra: list[str] | None = None
                   ) -> tuple[subprocess.CompletedProcess, dict,
                              subprocess.CompletedProcess, dict]:
    """同路径先后跑「新引擎 / HEAD 引擎」，返回 (new_proc, new_snap, head_proc, head_snap)。

    `head_extra`：HEAD 旧引擎不认识第五十八批的新参数，对照跑默认用 `[]`
    （语义上就是「批57 的默认转换」）。
    """
    out = os.path.join(tmp, tag, "out")
    shutil.rmtree(out, ignore_errors=True)
    new = run_convert(out, extra, env=env, source=source, bib=bib)
    new_snap = snapshot(os.path.join(out, "Test_Author"))
    shutil.rmtree(out, ignore_errors=True)
    old = run_convert(out, list(head_extra or []), env=env, engine=head,
                      source=source, bib=bib)
    old_snap = snapshot(os.path.join(out, "Test_Author"))
    return new, new_snap, old, old_snap


# ── ① 参数与帮助 ─────────────────────────────────────────────────────────


def test_help(env: dict) -> None:
    r = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), "--help"],
                       cwd=_ENGINE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout + r.stderr
    check("①-1 --help 退出 0", r.returncode == 0, out[-300:])
    check("①-2 --help 含 --no-bibdb-verify 且说明默认开/只读",
          "--no-bibdb-verify" in out and "只读引用库比对" in out and "默认开" in out)


# ── ② 有库：默认打印一行汇总、产物与 HEAD 逐字节相同 ──────────────────────


def test_default_with_db(tmp: str, env_db: dict, head: str, source: str, bib: str) -> None:
    new, new_snap, old, old_snap = _same_run_pair(
        tmp, "withdb", [], env=env_db, source=source, bib=bib, head=head)
    m = _SUMMARY_RE.search(new.stdout)
    check("②-1 默认转换退出 0", new.returncode == 0, (new.stdout + new.stderr)[-400:])
    check("②-2 默认转换打印「引用库比对：命中 1 / 候选 1 / 未命中 1」",
          m is not None and m.group(1, 2, 3) == ("1", "1", "1"),
          new.stdout[-600:])
    check("②-3 汇总行注明「只读，不改产物」", m is not None and "只读，不改产物" in m.group(0))
    check("②-4 汇总行只出现一次", new.stdout.count("引用库比对：") == 1)
    check("②-5 有库时产物树与 HEAD 旧引擎逐字节相同", new_snap == old_snap,
          f"new={len(new_snap)} head={len(old_snap)}")
    check("②-6 有库时日志 = HEAD 日志 + 恰好一行汇总（其余逐字节相同）",
          _strip_summary(new.stdout) == old.stdout)
    check("②-7 HEAD 旧引擎（批57）默认转换不打印该行",
          "引用库比对：" not in old.stdout)


# ── ③ 无库：完全静默，日志/产物都与 HEAD 逐字节相同 ──────────────────────


def test_default_no_db(tmp: str, env_nodb: dict, head: str, source: str, bib: str) -> None:
    new, new_snap, old, old_snap = _same_run_pair(
        tmp, "nodb", [], env=env_nodb, source=source, bib=bib, head=head)
    check("③-1 无库时默认转换退出 0", new.returncode == 0, (new.stdout + new.stderr)[-400:])
    check("③-2 无库时没有任何「引用库」输出", "引用库" not in new.stdout)
    check("③-3 无库时日志与 HEAD **逐字节**相同（静默零噪声）", new.stdout == old.stdout)
    check("③-4 无库时产物树与 HEAD 逐字节相同", new_snap == old_snap)


# ── ④ 关闭开关 ───────────────────────────────────────────────────────────


def test_opt_out(tmp: str, env_db: dict, env_nodb: dict, head: str,
                 source: str, bib: str) -> None:
    new, new_snap, old, old_snap = _same_run_pair(
        tmp, "optout", ["--no-bibdb-verify"], env=env_db, source=source, bib=bib,
        head=head, head_extra=[])
    check("④-1 --no-bibdb-verify 退出 0", new.returncode == 0)
    check("④-2 --no-bibdb-verify 不打印汇总行", "引用库比对：" not in new.stdout)
    check("④-3 --no-bibdb-verify 时日志与 HEAD 逐字节相同", new.stdout == old.stdout)
    check("④-4 --no-bibdb-verify 时产物树与 HEAD 逐字节相同", new_snap == old_snap)

    new2, new2_snap, old2, old2_snap = _same_run_pair(
        tmp, "nobibdb", ["--no-bibdb"], env=env_db, source=source, bib=bib,
        head=head, head_extra=[])
    check("④-5 --no-bibdb 关闭默认比对（静默、退出 0）",
          new2.returncode == 0 and "引用库比对：" not in new2.stdout)
    check("④-6 --no-bibdb 时产物与 HEAD 逐字节相同", new2_snap == old2_snap)

    # 显式 --bibdb-path（不经 SLCN_NOTES）：默认比对仍生效
    lib_path = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    new3, new3_snap, old3, old3_snap = _same_run_pair(
        tmp, "path", ["--bibdb-path", lib_path], env=env_nodb,
        source=source, bib=bib, head=head, head_extra=[])
    check("④-7 显式 --bibdb-path 时默认比对也生效（命中 1/候选 1/未命中 1）",
          _SUMMARY_RE.search(new3.stdout) is not None
          and _SUMMARY_RE.search(new3.stdout).group(1, 2, 3) == ("1", "1", "1"))
    check("④-8 显式 --bibdb-path 时产物与 HEAD 逐字节相同", new3_snap == old3_snap)
    check("④-9 --no-bibdb-verify + --bibdb-path → 仍静默",
          "引用库比对：" not in run_convert(
              os.path.join(tmp, "both"), ["--bibdb-path", lib_path, "--no-bibdb-verify"],
              env=env_nodb, source=source, bib=bib).stdout)


# ── ⑤ --verify-refs / --verify-plan 行为不变、不重复打印 ─────────────────


def test_verify_paths(tmp: str, env_db: dict, head: str, source: str, bib: str) -> None:
    lib_path = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    out = os.path.join(tmp, "verify", "out")
    shutil.rmtree(out, ignore_errors=True)
    bib_sha = sha(bib)
    r = run_convert(out, ["--verify-refs", "--bibdb-path", lib_path],
                    env=env_db, source=source, bib=bib)
    folder = os.path.join(out, "Test_Author")
    books = glob.glob(os.path.join(folder, "*_文献核实.md"))
    md = open(books[0], encoding="utf-8").read() if books else ""
    check("⑤-1 --verify-refs 退出 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("⑤-2 --verify-refs 仍有旧的「引用库：命中 1 条已核实」行",
          "引用库：命中 1 条已核实（跳过检索+AI）" in r.stdout)
    check("⑤-3 --verify-refs 不重复打印默认汇总行", "引用库比对：" not in r.stdout)
    check("⑤-4 --verify-refs 任务书「五、库内已核实」照旧", "## 五、库内已核实" in md
          and "`Quine1970`" in md)
    check("⑤-5 --verify-refs 不改输入 reference.bib", sha(bib) == bib_sha)

    r2 = run_convert(os.path.join(tmp, "plan", "out"),
                     ["--verify-refs", "--verify-plan", "--bibdb-path", lib_path],
                     env=env_db, source=source, bib=bib)
    check("⑤-6 --verify-plan 退出 0 且不重复打印默认汇总行",
          r2.returncode == 0 and "引用库：命中 1 条已核实" in r2.stdout
          and "引用库比对：" not in r2.stdout)
    check("⑤-7 --verify-plan 的预计条目仍按命中扣减（0 次 AI）",
          "预计 AI：0 次" in r2.stdout, r2.stdout[-500:])

    r3 = run_convert(os.path.join(tmp, "verify2", "out"),
                     ["--verify-refs", "--bibdb-path", lib_path, "--no-bibdb-verify"],
                     env=env_db, source=source, bib=bib)
    check("⑤-8 --no-bibdb-verify 不影响 --verify-refs 的核实（旧引用库行仍在）",
          r3.returncode == 0 and "引用库：命中 1 条已核实" in r3.stdout)


# ── ⑥ 降级：库坏/缺 都不崩、静默 ─────────────────────────────────────────


def test_degrade(tmp: str, env_nodb: dict, source: str, bib: str) -> None:
    bad_notes = os.path.join(tmp, "badnotes")
    os.makedirs(os.path.join(bad_notes, "cases"), exist_ok=True)
    os.makedirs(os.path.join(bad_notes, "bib_db"), exist_ok=True)
    write(os.path.join(bad_notes, "bib_db", "bibliography.jsonl"), "{ 这不是 JSONL\n")
    env_bad = make_env(bad_notes)
    r = run_convert(os.path.join(tmp, "bad", "out"), [], env=env_bad,
                    source=source, bib=bib)
    check("⑥-1 引用库损坏时默认转换仍退出 0（只读通道 fail-soft）", r.returncode == 0,
          (r.stdout + r.stderr)[-400:])
    check("⑥-2 引用库损坏时不打印汇总、不抛 traceback",
          "引用库比对：" not in r.stdout and "Traceback" not in (r.stdout + r.stderr))


# ── ⑦ helper 单测（直接调 convert.bibdb_readonly_scan）───────────────────


def test_helper(tmp: str, env_db: dict, source: str, bib: str) -> None:
    try:
        import convert  # noqa: E402  convert.py 模块级只有定义/main 守卫
    except Exception as e:  # noqa: BLE001
        skip("⑦-1 helper 可导入", f"{type(e).__name__}: {e}")
        return
    out = os.path.join(tmp, "helper", "out")
    old_env = dict(os.environ)
    try:
        os.environ.update({k: v for k, v in env_db.items() if k.startswith("SLCN_")})
        r = run_convert(out, [], env=env_db, source=source, bib=bib)
        folder = os.path.join(out, "Test_Author")
        bibp = glob.glob(os.path.join(folder, "*.bib")) or glob.glob(
            os.path.join(folder, "reference.bib"))

        class _Res:
            bib_path = bibp[0] if bibp else ""

        class _Args:
            no_bibdb_verify = False
            no_bibdb = False
            bibdb = False
            bibdb_path = ""

        res = convert.bibdb_readonly_scan(_Args(), _Res())
        check("⑦-1 helper 有库时 done=True 且计数 1/1/1",
              r.returncode == 0 and res["done"] and
              (len(res["safe"]), len(res["candidates"]), len(res["missed"])) == (1, 1, 1),
              str({k: type(v).__name__ for k, v in res.items()}))
        check("⑦-2 helper 返回库路径与条目", bool(res["path"]) and len(res["entries"]) == 3)

        class _ArgsOff(_Args):
            no_bibdb_verify = True

        res2 = convert.bibdb_readonly_scan(_ArgsOff(), _Res())
        check("⑦-3 helper 在 --no-bibdb-verify 时 done=False", not res2["done"])

        class _ArgsNo(_Args):
            no_bibdb = True

        check("⑦-4 helper 在 --no-bibdb 时 done=False",
              not convert.bibdb_readonly_scan(_ArgsNo(), _Res())["done"])

        class _ResEmpty:
            bib_path = ""

        check("⑦-5 helper 无 bib 时静默返回 done=False",
              not convert.bibdb_readonly_scan(_Args(), _ResEmpty())["done"])
    finally:
        os.environ.clear()
        os.environ.update(old_env)


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b58_bibdb ")
    try:
        src_dir = os.path.join(tmp, "src")
        source = write(os.path.join(src_dir, "main.tex"), _TEX)
        bib = write(os.path.join(src_dir, "refs.bib"), _BIB)
        lib = build_library(tmp)
        check("⓪-1 合成引用库已建立",
              os.path.isfile(lib) and os.path.isfile(
                  os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")))
        env_db = make_env(os.path.join(tmp, "notes"))
        empty_notes = os.path.join(tmp, "empty_notes")
        os.makedirs(os.path.join(empty_notes, "cases"), exist_ok=True)  # 无 bib_db/
        env_nodb = make_env(empty_notes)
        head = ensure_head_engine(tmp)
        if not head:
            skip("HEAD 对照（②/③/④)", "git archive HEAD 失败")
        else:
            test_help(env_db)
            test_default_with_db(tmp, env_db, head, source, bib)
            test_default_no_db(tmp, env_nodb, head, source, bib)
            test_opt_out(tmp, env_db, env_nodb, head, source, bib)
        test_verify_paths(tmp, env_db, head or "", source, bib)
        test_degrade(tmp, env_nodb, source, bib)
        test_helper(tmp, env_db, source, bib)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项"
          + (f"，SKIP {len(SKIPS)} 项" if SKIPS else ""))
    for f in FAILS:
        print("  FAIL:", f)
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
