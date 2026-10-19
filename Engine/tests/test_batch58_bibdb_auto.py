#!/usr/bin/env python3
r"""第五十八批：初排转换**默认只读引用库比对**（+ `--no-bibdb-verify`）的判据测试。

Lead 2026-10-20 拍板口径（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 默认开：无新参数时自动做一次只读比对，**只打一行汇总**（命中/候选/未命中），
    不改 reference.bib / .tex / 任务书；
  · `--no-bibdb-verify` / `--no-bibdb` 关闭；无引用库时**完全静默**；
  · `--verify-refs` / `--verify-plan` 行为不变、也不重复打印默认汇总行。

**Hermetic（批58 CI 修复，2026-10-20）**：本套件**不再**用 `git archive HEAD` 造「旧引擎」
做对照——那依赖工作树 HEAD 与可达父提交：本地 HEAD 还是批57 时能过，推送后 HEAD 已含
批58，「旧引擎」= 新引擎，CI（depth=1）里连父提交都没有。现在改成**同一引擎自对照**：
同一 fixture、同一 Engine，跑两次只差一个开关（默认 vs `--no-bibdb-verify` / `--no-bibdb`），
断言「日志只差恰好一行汇总、其余行逐字节相同、产物树逐字节相同」。
因此结果与 HEAD、git 历史、CI checkout 深度都无关。

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
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
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


def run_convert(out_dir: str, extra: list[str], *, env: dict,
                source: str, bib: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "convert.py"),
         "--source", source, "--out", out_dir, "--bib", bib, "--name", "Test_Author",
         "--author", "张三", "--lang", "zh", "--no-original"] + extra,
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


_SUMMARY_RE = re.compile(r"引用库比对：命中 (\d+) / 候选 (\d+) / 未命中 (\d+)（只读，不改产物）")


def _strip_summary(stdout: str) -> str:
    # 用 split("\n")（不是 splitlines）保留结尾换行，才能做逐字节比较。
    return "\n".join(ln for ln in stdout.split("\n") if "引用库比对：" not in ln)


def run_pair(tmp: str, tag: str, extra_on: list[str], extra_off: list[str], *,
             env: dict, source: str, bib: str
             ) -> tuple[subprocess.CompletedProcess, dict,
                        subprocess.CompletedProcess, dict]:
    """**同一引擎**在同一 fixture 上连跑两次（只差开关），返回 (on_proc, on_snap, off_proc, off_snap)。

    `on` 默认就是「默认转换」（无开关）；`off` 是关闭默认比对的对照组。
    产物树两次都重建、各自快照——用来断言「比对只读、不改产物」。
    与 HEAD / git 历史无关（CI depth=1 也能跑）。
    """
    out = os.path.join(tmp, tag, "out")
    results: list[tuple[subprocess.CompletedProcess, dict[str, str]]] = []
    for extras in (extra_on, extra_off):
        shutil.rmtree(out, ignore_errors=True)
        proc = run_convert(out, extras, env=env, source=source, bib=bib)
        results.append((proc, snapshot(os.path.join(out, "Test_Author"))))
    return results[0][0], results[0][1], results[1][0], results[1][1]


# ── ① 参数与帮助 ─────────────────────────────────────────────────────────


def test_help(env: dict) -> None:
    r = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), "--help"],
                       cwd=_ENGINE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout + r.stderr
    check("①-1 --help 退出 0", r.returncode == 0, out[-300:])
    check("①-2 --help 含 --no-bibdb-verify 且说明默认开/只读",
          "--no-bibdb-verify" in out and "只读引用库比对" in out and "默认开" in out)


# ── ② 有库：默认打印一行汇总、只读不改产物（同一引擎自对照）──────────────


def test_default_with_db(tmp: str, env_db: dict, env_nodb: dict,
                         source: str, bib: str) -> None:
    on, on_snap, off, off_snap = run_pair(
        tmp, "withdb", [], ["--no-bibdb-verify"],
        env=env_db, source=source, bib=bib)
    m = _SUMMARY_RE.search(on.stdout)
    check("②-1 默认转换退出 0", on.returncode == 0, (on.stdout + on.stderr)[-400:])
    check("②-2 默认转换打印「引用库比对：命中 1 / 候选 1 / 未命中 1」",
          m is not None and m.group(1, 2, 3) == ("1", "1", "1"),
          on.stdout[-600:])
    check("②-3 汇总行注明「只读，不改产物」", m is not None and "只读，不改产物" in m.group(0))
    check("②-4 汇总行只出现一次", on.stdout.count("引用库比对：") == 1)
    check("②-5 默认与 --no-bibdb-verify 两次运行的产物树逐字节相同",
          on_snap == off_snap, f"on={len(on_snap)} off={len(off_snap)}")
    check("②-6 默认日志 = --no-bibdb-verify 日志 + 恰好一行汇总（其余逐字节相同）",
          _strip_summary(on.stdout) == off.stdout
          and on.stdout.count("引用库比对：") == 1)
    nodb = run_convert(os.path.join(tmp, "nodb_line", "out"), [],
                       env=env_nodb, source=source, bib=bib)
    check("②-7 无库 fixture 与 --no-bibdb-verify 输出都不含「引用库比对：」行",
          "引用库比对：" not in nodb.stdout
          and "引用库比对：" not in off.stdout)


# ── ③ 无库：完全静默，日志/产物与关闭时一致（同一引擎自对照）────────────


def test_default_no_db(tmp: str, env_nodb: dict, source: str, bib: str) -> None:
    on, on_snap, off, off_snap = run_pair(
        tmp, "nodb", [], ["--no-bibdb-verify"],
        env=env_nodb, source=source, bib=bib)
    check("③-1 无库时默认转换退出 0", on.returncode == 0, (on.stdout + on.stderr)[-400:])
    check("③-2 无库时没有任何「引用库」输出", "引用库" not in on.stdout)
    check("③-3 无库时默认与 --no-bibdb-verify 日志**逐字节**相同（静默零噪声）",
          on.stdout == off.stdout)
    check("③-4 无库时两次运行产物树逐字节相同", on_snap == off_snap)


# ── ④ 关闭开关（同一引擎自对照）──────────────────────────────────────────


def test_opt_out(tmp: str, env_db: dict, env_nodb: dict, source: str, bib: str) -> None:
    on, on_snap, off, off_snap = run_pair(
        tmp, "optout", [], ["--no-bibdb-verify"],
        env=env_db, source=source, bib=bib)
    check("④-1 --no-bibdb-verify 退出 0", off.returncode == 0)
    check("④-2 --no-bibdb-verify 不打印汇总行", "引用库比对：" not in off.stdout)
    check("④-3 --no-bibdb-verify 日志 = 默认日志删去汇总行（其余逐字节相同）",
          _strip_summary(on.stdout) == off.stdout)
    check("④-4 --no-bibdb-verify 产物树与默认转换逐字节相同", off_snap == on_snap)

    on2, on2_snap, off2, off2_snap = run_pair(
        tmp, "nobibdb", [], ["--no-bibdb"],
        env=env_db, source=source, bib=bib)
    check("④-5 --no-bibdb 关闭默认比对（静默、退出 0）",
          off2.returncode == 0 and "引用库比对：" not in off2.stdout)
    check("④-6 --no-bibdb 产物与默认转换逐字节相同", off2_snap == on2_snap)

    # 显式 --bibdb-path（不经 SLCN_NOTES）：默认比对仍生效
    lib_path = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    r3 = run_convert(os.path.join(tmp, "path", "out"), ["--bibdb-path", lib_path],
                     env=env_nodb, source=source, bib=bib)
    m3 = _SUMMARY_RE.search(r3.stdout)
    check("④-7 显式 --bibdb-path 时默认比对也生效（命中 1/候选 1/未命中 1）",
          m3 is not None and m3.group(1, 2, 3) == ("1", "1", "1"), r3.stdout[-500:])
    check("④-8 显式 --bibdb-path 时产物与默认转换逐字节相同",
          snapshot(os.path.join(tmp, "path", "out", "Test_Author")) == on2_snap)
    check("④-9 --no-bibdb-verify + --bibdb-path → 仍静默",
          "引用库比对：" not in run_convert(
              os.path.join(tmp, "both"), ["--bibdb-path", lib_path, "--no-bibdb-verify"],
              env=env_nodb, source=source, bib=bib).stdout)


# ── ⑤ --verify-refs / --verify-plan 行为不变、不重复打印 ─────────────────


def test_verify_paths(tmp: str, env_db: dict, source: str, bib: str) -> None:
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
        test_help(env_db)
        test_default_with_db(tmp, env_db, env_nodb, source, bib)
        test_default_no_db(tmp, env_nodb, source, bib)
        test_opt_out(tmp, env_db, env_nodb, source, bib)
        test_verify_paths(tmp, env_db, source, bib)
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
