#!/usr/bin/env python3
r"""第五十八批：初排转换**默认引用库比对 + 库值全量应用**（B-217 ⑧ → B-135 P2）。

离线口径（Lead 2026-10-20 拍板）：默认开，HIT_SAFE 按 B-135 spec §3 白名单写入库内
非空值（覆盖/补齐），打汇总 + 逐条明细 + before/after 清单；`--no-bibdb-verify` /
`--no-bibdb` 关闭；`--no-bibdb-apply` 回旧只读口径（日志/产物逐字节等于 B-220）；
`--verify-refs` / `--verify-plan` 不变；无引用库时完全静默。

**Hermetic（同一引擎自对照）**：同一 fixture 跑两次只差开关，断言日志只差库值应用
新增行（精确三类）、产物树只差 reference.bib 库值差异 + 核对清单，其余逐字节同。

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
import types

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


# B-135 spec §3：默认 = 库值全量应用 → 汇总行带「默认回填 N 条 / M 项」。
_APPLY_RE = re.compile(r"引用库比对：命中 (\d+) / 候选 (\d+) / 未命中 (\d+)"
                       r"（默认回填 (\d+) 条 / (\d+) 项，候选与未命中不动）")
# 默认应用新增的三类日志行（精确锚点；①汇总 ②逐条明细 ③核对清单路径）。
_DETAIL_RE = re.compile(r"^     · \[[^\]]+\] .*$")
_WRITE_RE = re.compile(r"^     回填核对清单：\S+_参考文献核对\.txt$")
_BIB_NAME = "reference.bib"
_CHECKLIST_NAME = "Test_Author_参考文献核对.txt"


def _strip_bibdb_lines(stdout: str) -> str:
    """剔除默认应用新增的**精确三类**日志行（其余行逐字节保留，用于日志等价比较）。"""
    bad = (_APPLY_RE.search, _DETAIL_RE.match, _WRITE_RE.match)
    return "\n".join(ln for ln in stdout.split("\n") if not any(p(ln) for p in bad))


def _snap_rest(snap: dict) -> dict:
    """去掉库值应用允许触碰的产物（reference.bib/其备份/核对清单）后的快照。"""
    return {k: v for k, v in snap.items()
            if not (k in (_BIB_NAME, _CHECKLIST_NAME)
                    or (k.startswith(_BIB_NAME + ".") and k.endswith(".bak")))}


def _snap_no_bak(snap: dict) -> tuple[dict, list[str]]:
    """(去掉时间戳备份（名不确定量）后的快照, 备份内容 sha 列表)，用于两次「都会应用」对比。"""
    bak = sorted(v for k, v in snap.items()
                 if k.startswith(_BIB_NAME + ".") and k.endswith(".bak"))
    return ({k: v for k, v in snap.items()
             if not (k.startswith(_BIB_NAME + ".") and k.endswith(".bak"))}, bak)


def _read_prod_bundle(out_dir: str) -> tuple[str, list[str], str]:
    """(reference.bib, 其 atomic 备份文本[按文件名序], 核对清单文本)；时间戳名不参与比较。"""
    folder = os.path.join(out_dir, "Test_Author")
    try:
        names = sorted(n for n in os.listdir(folder)
                       if n.startswith(_BIB_NAME + ".") and n.endswith(".bak"))
    except OSError:
        names = []

    def _read(name: str) -> str:
        try:
            return open(os.path.join(folder, name), encoding="utf-8",
                        errors="replace").read()
        except OSError:
            return ""

    return _read(_BIB_NAME), [_read(n) for n in names], _read(_CHECKLIST_NAME)


def run_pair(tmp: str, tag: str, extra_on: list[str], extra_off: list[str], *,
             env: dict, source: str, bib: str):
    """**同一引擎**同 fixture 连跑两次（只差开关）；返回 (on_proc, on_snap, on_bundle,
    off_proc, off_snap, off_bundle)，bundle 见 `_read_prod_bundle`。"""
    out = os.path.join(tmp, tag, "out")
    results = []
    for extras in (extra_on, extra_off):
        shutil.rmtree(out, ignore_errors=True)
        proc = run_convert(out, extras, env=env, source=source, bib=bib)
        results.append((proc, snapshot(os.path.join(out, "Test_Author")),
                        _read_prod_bundle(out)))
    return results[0] + results[1]

# ── ① 参数与帮助 ─────────────────────────────────────────────────────────
def test_help(env: dict) -> None:
    r = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), "--help"],
                       cwd=_ENGINE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout + r.stderr
    check("①-1 --help 退出 0", r.returncode == 0, out[-300:])
    check("①-2 --help 含 --no-bibdb-verify（默认开/只读引用库比对）且 --no-bibdb-apply"
          "说明为「库值全量应用」",
          "--no-bibdb-verify" in out and "只读引用库比对" in out and "默认开" in out
          and "--no-bibdb-apply" in out and "库值全量应用" in out)

# ── ② 有库：默认应用库值（汇总 + 明细 + 清单），其余产物逐字节同 ───────────
def test_default_with_db(tmp: str, env_db: dict, env_nodb: dict,
                         source: str, bib: str) -> None:
    # B-135 spec §3（用户 2026-10-26 批准）：author/title 等 28 类进白名单后，
    # 「默认=只读不改产物」旧前提失效——本夹具库值只与产物 author 不同 → 1 条 / 1 项。
    (on, on_snap, (on_bib, on_baks, on_chk),
     off, off_snap, (off_bib, off_baks, off_chk)) = run_pair(
        tmp, "withdb", [], ["--no-bibdb-verify"],
        env=env_db, source=source, bib=bib)
    m = _APPLY_RE.search(on.stdout)
    check("②-1 默认转换退出 0", on.returncode == 0, (on.stdout + on.stderr)[-400:])
    check("②-2 默认转换打印新汇总「命中 1 / 候选 1 / 未命中 1（默认回填 1 条 / 1 项）」",
          m is not None and m.group(1, 2, 3, 4, 5) == ("1", "1", "1", "1", "1"),
          on.stdout[-600:])
    # B-135 spec §3：默认已不是只读 → 旧锚点「只读，不改产物」改为默认应用计数。
    check("②-3 汇总行注明默认回填计数与「候选与未命中不动」",
          m is not None and "默认回填 1 条 / 1 项" in m.group(0)
          and "候选与未命中不动" in m.group(0))
    check("②-4 汇总行只出现一次", on.stdout.count("引用库比对：") == 1)
    # B-135 spec §3/§7：允许改写的只有 reference.bib + 核对清单（及其 atomic 备份）。
    check("②-5a 除 reference.bib/核对清单/其备份外，产物树与 --no-bibdb-verify 逐字节相同",
          _snap_rest(on_snap) == _snap_rest(off_snap),
          f"rest_on={len(_snap_rest(on_snap))} rest_off={len(_snap_rest(off_snap))}")
    check("②-5b reference.bib 与关库版仅差库值 author（整文件逐字节可逆）",
          on_bib != off_bib
          and on_bib == off_bib.replace("{Quine, W. V. O.}",
                                        "{Quine, Willard Van Orman}")
          and on_baks == [off_bib] and not off_baks,
          f"on_baks={len(on_baks)} off_baks={len(off_baks)}")
    check("②-5c 核对清单：关库版不写；默认版新建 = 标题 + 库应用分节（分节在尾部、含 before/after）",
          off_chk == "" and on_chk.startswith("══ Test_Author 参考文献核对 ══\n\n")
          and "  ── 引用库回填（1 条 / 1 项）──" in on_chk
          and on_chk.rstrip().endswith(
              "author：（旧值 Quine, W. V. O.）→ Quine, Willard Van Orman"
              "\n  · B-189 网址清理：正式发表不写 doi/url 1 项；内嵌网址处理 0 项"),
          on_chk[-400:])
    check("②-6 默认日志 = --no-bibdb-verify 日志 + 精确三类库应用行（其余逐字节相同）",
          _strip_bibdb_lines(on.stdout) == off.stdout
          and on.stdout.count("引用库比对：") == 1)
    nodb = run_convert(os.path.join(tmp, "nodb_line", "out"), [],
                       env=env_nodb, source=source, bib=bib)
    check("②-7 无库 fixture 与 --no-bibdb-verify 输出都不含「引用库比对：」行",
          "引用库比对：" not in nodb.stdout
          and "引用库比对：" not in off.stdout)

# ── ③ 无库：完全静默，日志/产物与关闭时一致（同一引擎自对照）────────────
def test_default_no_db(tmp: str, env_nodb: dict, source: str, bib: str) -> None:
    on, on_snap, _on_b, off, off_snap, _off_b = run_pair(
        tmp, "nodb", [], ["--no-bibdb-verify"],
        env=env_nodb, source=source, bib=bib)
    check("③-1 无库时默认转换退出 0", on.returncode == 0, (on.stdout + on.stderr)[-400:])
    check("③-2 无库时没有任何「引用库」输出", "引用库" not in on.stdout)
    check("③-3 无库时默认与 --no-bibdb-verify 日志**逐字节**相同（静默零噪声）",
          on.stdout == off.stdout)
    check("③-4 无库时两次运行产物树逐字节相同", on_snap == off_snap)

# ── ④ 关闭开关（同一引擎自对照）──────────────────────────────────────────
def test_opt_out(tmp: str, env_db: dict, env_nodb: dict, source: str, bib: str) -> None:
    # B-135 spec §3（用户 2026-10-26 批准）：默认 = 库值应用 → 「产物逐字节同」旧前提失效；
    # 比较口径改为「除 spec §7 允许改写的 reference.bib/核对清单/其备份外逐字节同 +
    # reference.bib 只差库值 author 一行（整文件逐字节可逆）」。
    (on, on_snap, (on_bib, on_baks, _on_chk),
     off, off_snap, (off_bib, off_baks, _off_chk)) = run_pair(
        tmp, "optout", [], ["--no-bibdb-verify"],
        env=env_db, source=source, bib=bib)
    check("④-1 --no-bibdb-verify 退出 0", off.returncode == 0)
    check("④-2 --no-bibdb-verify 不打印汇总行", "引用库比对：" not in off.stdout)
    check("④-3 --no-bibdb-verify 日志 = 默认日志删去库应用精确三类行（其余逐字节相同）",
          _strip_bibdb_lines(on.stdout) == off.stdout
          and "引用库比对：" not in off.stdout)
    check("④-4 --no-bibdb-verify 产物：除允许改写三件外逐字节同、bib 仅差库值 author",
          _snap_rest(off_snap) == _snap_rest(on_snap)
          and off_bib == on_bib.replace("{Quine, Willard Van Orman}",
                                        "{Quine, W. V. O.}")
          and on_baks == [off_bib] and not off_baks)

    (on2, on2_snap, (on2_bib, on2_baks, _on2_chk),
     off2, off2_snap, (off2_bib, off2_baks, _off2_chk)) = run_pair(
        tmp, "nobibdb", [], ["--no-bibdb"],
        env=env_db, source=source, bib=bib)
    check("④-5 --no-bibdb 关闭默认比对（静默、退出 0）",
          off2.returncode == 0 and "引用库比对：" not in off2.stdout)
    check("④-6 --no-bibdb 产物：除允许改写三件外逐字节同、bib 仅差库值 author",
          _snap_rest(off2_snap) == _snap_rest(on2_snap)
          and off2_bib == on2_bib.replace("{Quine, Willard Van Orman}",
                                          "{Quine, W. V. O.}")
          and on2_baks == [off2_bib] and not off2_baks)

    # 显式 --bibdb-path（不经 SLCN_NOTES）：默认比对/应用仍生效
    lib_path = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    path_out = os.path.join(tmp, "path", "out")
    r3 = run_convert(path_out, ["--bibdb-path", lib_path],
                     env=env_nodb, source=source, bib=bib)
    p_snap = snapshot(os.path.join(path_out, "Test_Author"))
    m3 = _APPLY_RE.search(r3.stdout)
    check("④-7 显式 --bibdb-path 时默认应用也生效（命中 1/候选 1/未命中 1、回填 1 条/1 项）",
          m3 is not None and m3.group(1, 2, 3, 4, 5) == ("1", "1", "1", "1", "1"),
          r3.stdout[-500:])
    check("④-8 显式 --bibdb-path 产物（除 atomic 备份时间戳名外）与默认转换逐字节相同",
          _snap_no_bak(p_snap) == _snap_no_bak(on2_snap)
          and bool(_snap_no_bak(on2_snap)[1]))
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

        res_path = types.SimpleNamespace(bib_path=bibp[0] if bibp else "")

        def _args(**kw) -> types.SimpleNamespace:
            base = {"no_bibdb_verify": False, "no_bibdb": False, "bibdb": False,
                    "bibdb_path": ""}
            base.update(kw)
            return types.SimpleNamespace(**base)

        res = convert.bibdb_readonly_scan(_args(), res_path)
        check("⑦-1 helper 有库时 done=True 且计数 1/1/1",
              r.returncode == 0 and res["done"] and
              (len(res["safe"]), len(res["candidates"]), len(res["missed"])) == (1, 1, 1),
              str({k: type(v).__name__ for k, v in res.items()}))
        check("⑦-2 helper 返回库路径与条目", bool(res["path"]) and len(res["entries"]) == 3)
        res2 = convert.bibdb_readonly_scan(_args(no_bibdb_verify=True), res_path)
        check("⑦-3 helper 在 --no-bibdb-verify 时 done=False", not res2["done"])
        check("⑦-4 helper 在 --no-bibdb 时 done=False",
              not convert.bibdb_readonly_scan(_args(no_bibdb=True), res_path)["done"])
        check("⑦-5 helper 无 bib 时静默返回 done=False",
              not convert.bibdb_readonly_scan(
                  _args(), types.SimpleNamespace(bib_path=""))["done"])
    finally:
        os.environ.clear()
        os.environ.update(old_env)


# ── ⑧ B-217 默认回填 + B-135 P2 库值全量应用（--no-bibdb-apply 回退旧只读口径）──
_BIB_MISSING = """@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  pages   = {9--9},
  year    = {1970},
}

@article{KeyHitOnly,
  author = {Doe, Jane},
  title  = {A Totally Different Manuscript Title About Other Things},
  year   = {1999},
}
"""


def _read_bib(out_dir: str) -> str:
    return _read_prod_bundle(out_dir)[0]


def _safe_match(fields: dict, *, etype: str = "article", hit: str = "doi",
                sim: float = 1.0) -> dict:
    """B-135 单测用：`Store.match_reference()` 形状的 HIT_SAFE 结果（只给规划需要的键）。"""
    return {"status": bibdb.HIT_SAFE, "hit": hit, "title_similarity": sim,
            "record": {"entry_type": etype, "fields": dict(fields)}}


def _lib(body: str, record: dict, *, etype: str = "article", key: str = "K",
         record_key: str | None = None):
    """单条目便捷：字段体 + 库 record → (应用后整条文本, applied, stats, plans)。"""
    from slcn import bibdbapply
    match = _safe_match(record, etype=etype)
    if record_key:
        match["record"]["key"] = match["record_key"] = record_key
    plans, stats = bibdbapply.plan_library_fills(
        None, [(etype, key, body)], {key: match})
    out, applied = bibdbapply.apply_text(f"@{etype}{{{key},\n{body}\n}}\n", plans)
    return out, applied, stats, plans


def _lib_fields(text: str) -> dict:
    """整条 bib 文本 → 字段名→值映射。"""
    from slcn import bibdbapply
    _et, _key, _ms, bs, cl = bibdbapply.entry_spans(text)[0]
    return bibdbapply._field_text_map(text[bs:cl])


def test_default_apply(tmp: str, env_db: dict, env_nodb: dict,
                       source: str) -> None:
    from slcn import bibdbapply  # noqa: E402

    src_dir = os.path.dirname(source)
    bib2 = write(os.path.join(src_dir, "missing_fields.bib"), _BIB_MISSING)
    lib = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    bib_sha, lib_sha = sha(bib2), sha(lib)
    out_on = os.path.join(tmp, "apply", "out")
    out_off = os.path.join(tmp, "apply_off", "out")
    out_silent = os.path.join(tmp, "apply_silent", "out")
    r_on = run_convert(out_on, [], env=env_db, source=source, bib=bib2)
    r_off = run_convert(out_off, ["--no-bibdb-apply"], env=env_db,
                        source=source, bib=bib2)
    r_silent = run_convert(out_silent, ["--no-bibdb-verify"], env=env_db,
                           source=source, bib=bib2)
    bib_on = _read_bib(out_on)
    folder_on, folder_off = (os.path.join(out_on, "Test_Author"),
                             os.path.join(out_off, "Test_Author"))
    chk_on = os.path.join(folder_on, _CHECKLIST_NAME)
    chk_txt = open(chk_on, encoding="utf-8").read() if os.path.isfile(chk_on) else ""
    check("⑧-1 默认回填：退出 0", r_on.returncode == 0,
          (r_on.stdout + r_on.stderr)[-400:])
    # B-135 spec §3：白名单 28 类（author/title 等）→ 本夹具 journal+volume 补缺失、
    # author+pages 按库值覆盖 = 4 项；doi 被 B-189 删除不计项。
    check("⑧-2 默认回填日志：命中 1 / 候选 1 / 未命中 0 + 回填 1 条 / 4 项（B-135 spec §3）",
          "默认回填 1 条 / 4 项" in r_on.stdout and "命中 1 / 候选 1" in r_on.stdout,
          r_on.stdout[-600:])
    check("⑧-3 只补缺失字段：journal=Mind、volume=79",
          "journal = {Mind}" in bib_on and "volume = {79}" in bib_on, bib_on)
    # B-222 B/U3（用户 2026-10-26 12:1x 批准）：改为**库值覆盖**——pages 现值 9--9
    # 与库值 1--20 不同 → 整值覆盖；同值才不重写（不是放宽判据，是契约变更）。
    check("⑧-4 已有非空字段按库值覆盖（pages 9--9 → 库值 1--20；B-222 B/U3 契约）",
          "pages" in bib_on and "{1--20}" in bib_on
          and "9--9" not in bib_on, bib_on)
    check("⑧-5 候选条目不动（KeyHitOnly 字段体不变）",
          "A Totally Different Manuscript Title About Other Things" in bib_on
          and "KeyHitOnly" in bib_on)
    check("⑧-6 回填核对清单写出 before/after（沿用参考文献核对体例）",
          "引用库回填" in chk_txt and "journal：（缺失）→ Mind" in chk_txt,
          chk_txt[-400:])
    check("⑧-7 默认回填不改输入 .bib / 不写引用库",
          sha(bib2) == bib_sha and sha(lib) == lib_sha)
    check("⑧-8 --no-bibdb-apply：旧只读口径（汇总行「只读，不改产物」、无回填）",
          r_off.returncode == 0 and "只读，不改产物" in r_off.stdout
          and "默认回填" not in r_off.stdout, r_off.stdout[-500:])
    check("⑧-9 --no-bibdb-apply：产物逐字节 = --no-bibdb-verify（都不写）",
          snapshot(folder_off) == snapshot(os.path.join(out_silent, "Test_Author")))
    check("⑧-10 --no-bibdb-apply：核对清单无回填分节（旧只读口径）",
          "引用库回填" not in _read_prod_bundle(out_off)[2])
    _nod = os.path.join(tmp, "apply_nodb", "out")
    _r_nod_on = run_convert(_nod, [], env=env_nodb, source=source, bib=bib2)
    shutil.rmtree(_nod, ignore_errors=True)
    _r_nod_off = run_convert(_nod, ["--no-bibdb-apply"], env=env_nodb,
                             source=source, bib=bib2)
    check("⑧-11 无库时默认转换与 --no-bibdb-apply 日志逐字节同（静默零变化）",
          _r_nod_on.returncode == 0 and _r_nod_on.stdout == _r_nod_off.stdout)

    # ── 旧 `plan_fills()` 窄语义单测（断言内容不变，编号 ⑧-12b…⑧-12e）────────
    entries = [("article", "K", " author = {A}, title = {T}, year = {2020}, ")]
    safe_c = {"K": {"status": bibdb.HIT_CANDIDATE, "record": {"fields": {"journal": "J"}}}}
    check("⑧-12 候选（低置信）不回填", bibdbapply.plan_fills(None, entries, safe_c) == [])
    safe_s = {"K": {"status": bibdb.HIT_SAFE,
                    "record": {"fields": {"journal": "J", "volume": "1"}}}}
    _pl = bibdbapply.plan_fills(None, entries, safe_s)
    check("⑧-12b 安全命中只补缺失字段（旧窄语义）",
          _pl and _pl[0]["fills"] == {"journal": "J", "volume": "1"}, f"{_pl}")
    entries2 = [("article", "K",
                 " author = {A}, journal = {Old}, title = {T}, year = {2020} ")]
    _pl2 = bibdbapply.plan_fills(None, entries2, safe_s)
    check("⑧-12c 已有非空字段（旧窄语义）不覆盖",
          _pl2 and _pl2[0]["fills"] == {"volume": "1"}, f"{_pl2}")
    _raw = "@article{K,\n  author = {A},\n  journal = {},\n  year = {2020}\n}\n"
    _new, _applied = bibdbapply.apply_text(
        _raw, [{"etype": "article", "key": "K", "fills": {"journal": "J"}}])
    check("⑧-12d 空值字段原位替换（不产生重复字段）",
          _new.count("journal") == 1 and "journal = {J}" in _new
          and _new.replace("journal = {J}", "journal = {}") == _raw,
          _new)
    check("⑧-12e 找不到的条目 / 未知类型 → 不动（fail-soft）",
          bibdbapply.apply_text(_raw, [{"etype": "misc", "key": "NOPE",
                                        "fills": {"journal": "J"}}]) == (_raw, []))

    # ── B-135 余项 P2：`plan_library_fills()` 全量应用（spec §3/§4/§6）──────
    _t13, _a13, _st13, _p13 = _lib(  # ⑧-13 覆盖生效（与条目类型无关）
        "  author = {Old},\n  journal = {Old},\n  pages = {9--9},\n  year = {2020}",
        {"author": "Quine, Willard Van Orman", "journal": "Mind",
         "pages": "1--20", "volume": "79", "year": "2020"})
    check("⑧-13 覆盖生效：fills=volume、replaces=author+journal+pages，应用后逐字节=库值",
          _p13 and _p13[0]["fills"] == {"volume": "79"}
          and _p13[0]["replaces"] == {"author": "Quine, Willard Van Orman",
                                      "journal": "Mind", "pages": "1--20"}
          and _lib_fields(_t13) == {"author": "Quine, Willard Van Orman",
                                    "journal": "Mind", "pages": "1--20",
                                    "year": "2020", "volume": "79"}
          and _a13 and _a13[0]["replaced"] == {"author": "Old", "journal": "Old",
                                              "pages": "9--9"},
          f"{_p13} / {_t13}")
    _t14, _a14, _st14, _p14 = _lib(  # ⑧-14 库缺/空不抹除
        "  author = {A},\n  note = {keep me},\n  title = {T},\n  year = {2020}",
        {"title": "T", "year": "2020", "note": ""})
    check("⑧-14 库缺/空字段 → 保留产物原值（note/author 不抹除、不写空值）",
          _p14 == [] and _a14 == [] and _lib_fields(_t14)["note"] == "keep me")
    _c15 = bibdbapply._render_library_candidates(  # ⑧-15 白名单外永不写入
        "article", {"userf": "wang", "crossref": "X", "keywords": "kw",
                    "abstract": "abs", "wang": "W", "id": "42",
                    "priority": "9", "abstractnote": "n", "title": "T"})
    _t15, _a15, _st15, _p15 = _lib(
        "  title = {T}", {"title": "T", "userf": "wang", "crossref": "X",
                          "abstract": "abs", "keywords": "kw", "wang": "W",
                          "id": "42", "priority": "9", "abstractnote": "n",
                          "journal": "Mind"})
    # B-135 spec §3：库导入残留点名 wang/id/priority/abstractnote（原仅用 userf/
    # crossref 等价覆盖）→ 全部进夹具，断言 render/plan/applied 三处都不出现。
    check("⑧-15 白名单外永不应用（render 只留 title；plan 只补 journal）；"
          "spec §3 点名库导入残留 wang/id/priority/abstractnote 也不写入",
          [n for n, _v in _c15] == ["title"]
          and _p15 and set(_p15[0]["fills"]) == {"journal"}
          and _a15 and _a15[0]["filled"] == {"journal": "Mind"},
          f"{_c15} / {_p15}")
    _t16, _a16, _st16, _p16 = _lib(  # ⑧-16 B-189 正式发表不写 doi/url
        "  author = {A},\n  journal = {Mind},\n  title = {T},\n  year = {2020}",
        {"title": "T", "year": "2020", "journal": "Mind",
         "doi": "10.1000/x", "url": "https://e/x"})
    _t16b, _a16b, _st16b, _p16b = _lib(
        "  author = {A},\n  title = {T},\n  year = {2020}", {
            "title": "T", "year": "2020", "doi": "10.1000/y", "url": "https://e/y",
            "eprint": "2201.0001", "archiveprefix": "arXiv",
            "primaryclass": "cs.LO"}, etype="misc")
    _m16, _m16b = _lib_fields(_t16), _lib_fields(_t16b)
    check("⑧-16 B-189：正式发表不写 doi/url（库值有也删留痕）；misc 保留 doi/url/eprint",
          "doi" not in _m16 and "url" not in _m16
          and any("已删除" in n for n in _st16["b189"])
          and _m16b.get("doi") == "10.1000/y" and _m16b.get("url") == "https://e/y"
          and _m16b.get("eprint") == "2201.0001"
          and _m16b.get("archiveprefix") == "arXiv"
          and _m16b.get("primaryclass") == "cs.LO", f"{_m16} / {_m16b}")
    _t17, _a17, _st17, _p17 = _lib(  # ⑧-17 key 不改（record_key 带 -2 后缀）
        "  author = {A},\n  title = {T},\n  year = {2020}",
        {"title": "T", "year": "2020", "journal": "Mind"}, record_key="K-2")
    _sp17 = bibdbapply.entry_spans(_t17)
    check("⑧-17 key 不改（record_key='K-2' 仍写回原条目 K、不新增条目）",
          len(_sp17) == 1 and _sp17[0][1] == "K" and "K-2" not in _t17
          and "journal = {Mind}" in _t17, _t17)
    _p18, _ = bibdbapply.plan_library_fills(  # ⑧-18 幂等
        None, [(et, k, _t17[bs:cl]) for et, k, _st, bs, cl in _sp17],
        {"K": _safe_match({"title": "T", "year": "2020", "journal": "Mind"})})
    _t18, _a18 = bibdbapply.apply_text(_t17, _p18)
    check("⑧-18 幂等：第二遍 applied=0 且文本逐字节同（库值即终态、不二次 normalize）",
          _p18 == [] and _a18 == [] and _t18 == _t17)
    _bib19 = ("@article{Safe,\n  title = {T}\n}\n\n"  # ⑧-19 候选/未命中逐字节不变
              "@article{Cand,\n  title = {CandT}\n}\n\n"
              "@article{Miss,\n  title = {MissT}\n}\n")
    _p19, _ = bibdbapply.plan_library_fills(
        None, [(et, k, _bib19[bs:cl])
               for et, k, _st, bs, cl in bibdbapply.entry_spans(_bib19)],
        {"Safe": _safe_match({"journal": "Mind"}),
         "Cand": {"status": bibdb.HIT_CANDIDATE, "hit": "title", "title_similarity": 1.0,
                  "record": {"entry_type": "article",
                             "fields": {"journal": "CandJ"}}}})
    _t19, _a19 = bibdbapply.apply_text(_bib19, _p19)
    _f19 = [bibdbapply._field_text_map(_t19[s:c]) for _et, _k, _ms, s, c
            in bibdbapply.entry_spans(_t19)]
    check("⑧-19 候选/未命中条目逐字节不变（只有 HIT_SAFE 被改写）",
          [a["key"] for a in _a19] == ["Safe"]
          and _f19 == [{"title": "T", "journal": "Mind"}, {"title": "CandT"},
                       {"title": "MissT"}], f"{_f19}")
    _snap_off20 = snapshot(os.path.join(out_off, "Test_Author"))  # ⑧-20 spec §6 回旧
    check("⑧-20 --no-bibdb-apply 回旧只读口径（spec §6）：无回填明细/清单/备份，产物=关库版",
          "只读，不改产物" in r_off.stdout and "默认回填" not in r_off.stdout
          and "回填核对清单" not in r_off.stdout
          and not any(k.endswith(".bak") for k in _snap_off20)
          and _snap_off20 == snapshot(os.path.join(out_silent, "Test_Author")))
    _bad21 = "@article{BROKEN,\n  author = {A},\n"  # ⑧-21 结构异常整条跳过
    _st21: dict = {}
    _t21, _a21 = bibdbapply.apply_text(
        _bad21, [{"etype": "article", "key": "BROKEN", "fills": {"journal": "J"}}], _st21)
    check("⑧-21 结构异常条目整条跳过（文本一字不改 + struct 记录 reason）",
          _t21 == _bad21 and _a21 == []
          and [r.get("reason") for r in _st21.get("struct", [])]
          == ["entry-not-found"], f"{_st21}")
    _bad21b = "@article{B21,\n  title = {X}{Y}\n}\n"  # ⑧-21b `}{` → render-failed
    _st21b: dict = {}
    _t21b, _a21b = bibdbapply.apply_text(
        _bad21b, [{"etype": "article", "key": "B21", "fills": {"journal": "J"}}],
        _st21b)
    check("⑧-21b `}{` 渲染结构失败整条跳过（文本不变 + struct=render-failed）",
          _t21b == _bad21b and _a21b == []
          and [r.get("reason") for r in _st21b.get("struct", [])] == ["render-failed"],
          f"{_st21b}")

# ── ⑤b --verify-plan 单独也回填（B-217 修复：41 篇两遍确定性；回填与核实模式无关）─
def test_verify_plan_apply(tmp: str, env_db: dict, source: str) -> None:
    src_dir = os.path.dirname(source)
    bib2 = write(os.path.join(src_dir, "missing_fields_plan.bib"), _BIB_MISSING)
    lib = os.path.join(tmp, "notes", "bib_db", "bibliography.jsonl")
    out_def = os.path.join(tmp, "planapply", "default")
    out_plan = os.path.join(tmp, "planapply", "plan")
    for d in (out_def, out_plan):
        shutil.rmtree(d, ignore_errors=True)
    r_def = run_convert(out_def, ["--bibdb-path", lib], env=env_db,
                        source=source, bib=bib2)
    r_plan = run_convert(out_plan, ["--verify-plan", "--bibdb-path", lib],
                         env=env_db, source=source, bib=bib2)
    bib_def = _read_bib(out_def)
    bib_plan = _read_bib(out_plan)
    check("⑤b-1 --verify-plan 单独运行退出 0", r_plan.returncode == 0,
          (r_plan.stdout + r_plan.stderr)[-400:])
    check("⑤b-2 --verify-plan 单独运行也回填（打默认汇总行「默认回填 1 条」）",
          "引用库比对：" in r_plan.stdout and "默认回填 1 条" in r_plan.stdout,
          r_plan.stdout[-500:])
    check("⑤b-3 --verify-plan 产物 .bib 含回填字段（journal=Mind）",
          "journal = {Mind}" in bib_plan, bib_plan)
    check("⑤b-4 默认 vs --verify-plan 的 reference.bib 逐字节相同（两遍确定性）",
          bib_def != "" and bib_def == bib_plan)
    check("⑤b-5 默认转换退出 0（对照组）", r_def.returncode == 0)

# ── main ────────────────────────────────────────────────────────────────
def test_short_cite_library(tmp: str) -> None:
    """B-220 N6：纯引用脚注 → 引用库 HIT_SAFE 唯一命中（不编造）。"""
    from slcn import bibdb, bibdbapply  # noqa: E402

    # 合成库：① 已核实且题名唯一；② 未核实同名题名；③ 同题名第二条（唯一性反例）
    notes2 = os.path.join(tmp, "notes2")
    path2 = os.path.join(notes2, "bib_db", "bibliography.jsonl")
    os.makedirs(os.path.dirname(path2), exist_ok=True)
    st = bibdb.Store.load(path2, create=True)
    st.import_bib_text("""@article{Watson1987,
  author  = {Gary Watson},
  title   = {Free Action and Free Will},
  journal = {Mind},
  volume  = {96},
  number  = {382},
  pages   = {145--172},
  year    = {1987}
}

""", PUBLISHED, verified=True)
    st.import_bib_text("""@article{Unverified2099,
  author = {Doe, Jane},
  title  = {A Totally Unverified Work For The Test},
  year   = {2099},
}
""", PUBLISHED, verified=False)
    st.save()
    store = bibdb.Store.load(path2)
    r = bibdbapply.resolve_short_citation(store, author="Gary Watson",
                                          title="Free Action and Free Will,")
    check("⑨-1 短引库解析：已核实唯一题名 → 命中且字段/类型可用",
          bool(r) and r["key"] == "Watson1987" and r["entry"].get("_type") == "article"
          and r["entry"].get("journal") == "Mind", repr(r and r.get("key")))
    check("⑨-2 短引库解析：无 Store（--no-bibdb 等）→ None（默认零变化）",
          bibdbapply.resolve_short_citation(None, author="Gary Watson",
                                            title="Free Action and Free Will") is None)
    r_typo = bibdbapply.resolve_short_citation(store, author="Gary Waston",
                                               title="Free Action and Free Will")
    check("⑨-3 短引库解析：作者姓拼错 + 题名唯一 → 只按题名兜底（唯一性判据）",
          bool(r_typo) and r_typo["key"] == "Watson1987", repr(r_typo and r_typo.get("key")))
    r_unv = bibdbapply.resolve_short_citation(
        store, author="Jane Doe", title="A Totally Unverified Work For The Test")
    check("⑨-4 短引库解析：库内未核实 → None（候选不动，不编造）", r_unv is None, repr(r_unv))
    # 唯一性反例：同题名再进一条已核实记录（不同作者）→ 拼错作者不再兜底
    st2 = bibdb.Store.load(path2)
    st2.import_bib_text("""@article{Other1987,
  author = {Other, Ann},
  title  = {Free Action and Free Will},
  year   = {1987}
}
""", PUBLISHED, verified=True)
    st2.save()
    store2 = bibdb.Store.load(path2)
    r_amb = bibdbapply.resolve_short_citation(store2, author="Gary Waston",
                                              title="Free Action and Free Will")
    check("⑨-5 短引库解析：同题名多条 → 拼错作者时拒绝（唯一性反例）", r_amb is None, repr(r_amb))
    # 开关：--no-bibdb-apply 时 Store 不解析（convert 侧门槛）
    import argparse  # noqa: E402
    try:
        from convert import resolve_bibdb_apply_store  # noqa: E402
        for flag in ("no_bibdb_apply", "no_bibdb_verify", "no_bibdb"):
            ns = argparse.Namespace(no_bibdb_verify=False, no_bibdb=False,
                                    no_bibdb_apply=False)
            setattr(ns, flag, True)
            check(f"⑨-6 {flag} → 短引库 Store 不解析（回旧行为）",
                  resolve_bibdb_apply_store(ns) is None)
    except ImportError as exc:  # pragma: no cover - 隔离环境
        SKIPS.append(f"convert 侧开关自检（{exc}）")


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
        test_default_apply(tmp, env_db, env_nodb, source)
        test_verify_plan_apply(tmp, env_db, source)
        test_short_cite_library(tmp)
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
