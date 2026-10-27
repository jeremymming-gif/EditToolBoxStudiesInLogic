#!/usr/bin/env python3.14
"""O13（第四十六批）`tools/run_record.py` 单测：manifest 生成 / 字段 / 幂等 / 失败仍写。

覆盖卡面要求：
  ① 临时目录生成 manifest、**字段齐全**（9 个必需字段 + label/meta）；
  ② 二次运行**不覆盖**（文件名带时间戳，先前的文件逐字节不变）；
  ③ **失败退出仍写 manifest**（exit_code 透传）；
  ④ `--record-only`（build.sh 末尾用）按给定事实写；
  ⑤ `--artifact` 的 sha256 与文件内容一致；artifact 不存在 → null；
  ⑥ `engine_py_sha256` 确定性（同代码 = 同指纹、64 位 hex）；
  ⑦ `--tee` 日志落盘、`--meta k=v` 透传、原子写无残留 `.tmp`。
  ⑧ build.sh「源码树静默快照」：按标记抽出两个函数在 /tmp 夹具上验证（确定性 /
     源码改动 → changed=1 + 明示警告 / out、__pycache__、*.pyc 等产物改动不误报），
     并锁住 snapshot 四字段经既有 `--meta` 通道原样落盘的向后兼容契约。

全部离线、只写 /tmp；不需要稿件库。
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
RR = os.path.join(_ENGINE, "tools", "run_record.py")

FAILS = []
_TMP = tempfile.mkdtemp(prefix="slcn_run_record_")

REQUIRED = ("started_at", "finished_at", "duration_s", "command", "cwd",
            "git_head", "engine_py_sha256", "out_artifact_sha256", "exit_code")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def run_rr(args, cwd=_ENGINE, timeout=120):
    r = subprocess.run([sys.executable, RR, *args], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def manifests(d=_TMP):
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha256_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


try:
    # ── ① wrap 成功：manifest 字段齐全 + artifact/tee/meta ─────────────────
    d1 = os.path.join(_TMP, "ok")
    os.makedirs(d1)
    art = os.path.join(_TMP, "artifact.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("artifact-payload\n")
    tee = os.path.join(d1, "cmd.log")
    rc, out, err = run_rr(["--label", "unit-ok", "--out-dir", d1, "--artifact", art, "--tee", tee,
                           "--meta", "batch=46", "--meta", "k=v",
                           "--", sys.executable, "-c", "print('hello-manifest')"])
    ms = manifests(d1)
    check("① wrap 成功退出码 0", rc == 0, f"rc={rc} err={err[-200:]}")
    check("① 生成且只生成一份 manifest", len(ms) == 1, ms)
    m = load(ms[0]) if ms else {}
    check("① 9 个必需字段齐全", all(k in m for k in REQUIRED),
          f"缺 {[k for k in REQUIRED if k not in m]}")
    check("① label / exit_code / duration 正确",
          m.get("label") == "unit-ok" and m.get("exit_code") == 0 and float(m.get("duration_s", -1)) >= 0,
          {k: m.get(k) for k in ("label", "exit_code", "duration_s")})
    check("① command 记录完整", "print" in str(m.get("command", "")) and "hello-manifest" in str(m.get("command", "")),
          m.get("command"))
    check("① cwd 为 Engine 目录", os.path.realpath(str(m.get("cwd"))) == os.path.realpath(_ENGINE),
          m.get("cwd"))
    check("① git_head 是 40 位 sha（或空）",
          (len(str(m.get("git_head", ""))) == 40) or str(m.get("git_head")) == "", m.get("git_head"))
    check("① artifact sha256 与文件内容一致",
          m.get("out_artifact_sha256") == sha256_file(art), m.get("out_artifact_sha256"))
    check("① --meta 透传", (m.get("meta") or {}).get("batch") == "46", m.get("meta"))
    check("① --tee 日志已落盘且含输出",
          os.path.isfile(tee) and "hello-manifest" in open(tee, encoding="utf-8").read())
    check("① 无残留 .tmp（原子写）", not [x for x in os.listdir(d1) if x.endswith(".tmp")])
    check("① purpose 默认 unspecified", m.get("purpose") == "unspecified", m.get("purpose"))

    # ── ② 二次运行不覆盖 ────────────────────────────────────────────────
    first = ms[0] if ms else None
    first_bytes = open(first, "rb").read() if first else b""
    rc2, _, _ = run_rr(["--label", "unit-ok", "--out-dir", d1, "--purpose", "verify-build",
                        "--", sys.executable, "-c", "print('second')"])
    ms2 = manifests(d1)
    check("② 二次同 label 生成新文件（不覆盖）", rc2 == 0 and len(ms2) == 2, ms2)
    check("② 先前的 manifest 逐字节未变", bool(first) and os.path.isfile(first)
          and open(first, "rb").read() == first_bytes)
    new_manifests = [x for x in ms2 if x != first]
    check("② --purpose 写入新 manifest（verify-build）",
          bool(new_manifests) and load(new_manifests[0]).get("purpose") == "verify-build",
          load(new_manifests[0]).get("purpose") if new_manifests else None)

    # ── ③ 失败退出仍写 manifest ─────────────────────────────────────────
    d2 = os.path.join(_TMP, "fail")
    os.makedirs(d2)
    rc, out, err = run_rr(["--label", "unit-fail", "--out-dir", d2, "--purpose", "verify-build",
                           "--", sys.executable, "-c", "import sys; sys.exit(3)"])
    ms = manifests(d2)
    mf = load(ms[0]) if ms else {}
    check("③ 命令失败时包装器退出码透传（3）", rc == 3, f"rc={rc}")
    check("③ 失败仍写出 manifest 且 exit_code=3", len(ms) == 1 and mf.get("exit_code") == 3,
          {k: mf.get(k) for k in ("exit_code",)})
    check("③ 失败 manifest 仍带 purpose（verify-build）", mf.get("purpose") == "verify-build",
          mf.get("purpose"))

    # ── ④ record-only（build.sh 末尾用）─────────────────────────────────
    d3 = os.path.join(_TMP, "rec")
    started = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")
    precomputed_sha = "a" * 64
    rc, out, err = run_rr(["--record-only", "--label", "unit-rec", "--out-dir", d3,
                           "--started-at", started, "--command", "bash build.sh",
                           "--exit-code", "7", "--engine-sha", precomputed_sha,
                           "--purpose", "real-build", "--meta", "app_version=1.2.1"])
    ms = manifests(d3)
    mr = load(ms[0]) if ms else {}
    check("④ record-only 成功且不执行命令", rc == 0 and len(ms) == 1, f"rc={rc}")
    check("④ record-only 字段正确（command/exit_code=7/duration≥9s）",
          mr.get("command") == "bash build.sh" and mr.get("exit_code") == 7
          and float(mr.get("duration_s", 0)) >= 9, {k: mr.get(k) for k in ("command", "exit_code", "duration_s")})
    check("④ record-only meta 透传", (mr.get("meta") or {}).get("app_version") == "1.2.1", mr.get("meta"))
    check("④ --engine-sha（开始时算好的指纹）原样写入", mr.get("engine_py_sha256") == precomputed_sha,
          mr.get("engine_py_sha256"))
    check("④ record-only purpose（real-build）", mr.get("purpose") == "real-build", mr.get("purpose"))
    # 真跑 / 验证跑可机读区分：同目录里再来一条 verify-build 的**失败**构建
    rc, _, _ = run_rr(["--record-only", "--label", "unit-rec", "--out-dir", d3,
                       "--started-at", started, "--command", "bash build.sh",
                       "--exit-code", "1", "--purpose", "verify-build",
                       "--meta", "failed=true"])
    ms = manifests(d3)
    purposes = sorted(load(x).get("purpose") for x in ms)
    failed = [x for x in ms if load(x).get("exit_code") == 1]
    check("④ 同目录并存 real-build 与 verify-build（O5 计数可机读）",
          rc == 0 and purposes == ["real-build", "verify-build"], purposes)
    check("④ build 失败留痕：记录 exit_code=1 的 manifest", bool(failed), [load(x).get("exit_code") for x in ms])
    d3b = os.path.join(_TMP, "rec-bad")     # 独立目录：断言「参数错误 → 一个文件都不写」
    rc_bad, _, _ = run_rr(["--record-only", "--label", "x", "--out-dir", d3b])   # 缺 --started-at
    check("④ record-only 缺 --started-at → 参数错误（非 0，不写文件）",
          rc_bad != 0 and len(manifests(d3b)) == 0, f"rc={rc_bad}")

    # ── ⑤ artifact 不存在 → null ────────────────────────────────────────
    d4 = os.path.join(_TMP, "noart")
    os.makedirs(d4)
    rc, _, _ = run_rr(["--label", "unit-noart", "--out-dir", d4,
                       "--artifact", os.path.join(_TMP, "not-there.bin"),
                       "--", sys.executable, "-c", "pass"])
    mn = load(manifests(d4)[0]) if manifests(d4) else {}
    check("⑤ artifact 不存在 → out_artifact_sha256=null", mn.get("out_artifact_sha256") is None,
          mn.get("out_artifact_sha256"))

    # ── ⑥ engine_py_sha256 确定性 ───────────────────────────────────────
    spec = importlib.util.spec_from_file_location("_rr", RR)
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    h1, h2 = rr.engine_py_sha256(), rr.engine_py_sha256()
    check("⑥ engine_py_sha256 两次调用相同（确定性）", h1 == h2, f"{h1[:16]}…")
    check("⑥ engine_py_sha256 是 64 位 hex", len(h1) == 64 and all(c in "0123456789abcdef" for c in h1), h1[:16])
    with open(manifests(d1)[0], encoding="utf-8") as f:
        check("⑥ manifest 里的 engine_py_sha256 与库函数口径一致", json.load(f)["engine_py_sha256"] == h1,
              "（若引擎代码在本测试期间被其他写者改动，此项可能不同）")

    # ── ⑦ write_manifest 作为库调用（gen_docs 的路径）+ 缺省字段 ─────────
    d5 = os.path.join(_TMP, "lib")
    t0 = datetime.now().timestamp()
    path = rr.write_manifest(label="unit-lib", command="gen_docs.py --check", started_at=t0,
                             out_dir=d5, artifact=None, exit_code=0, meta={"mode": "check"})
    ml = load(path) if path and os.path.isfile(path) else {}
    check("⑦ write_manifest 库调用可用且字段齐全",
          bool(path) and all(k in ml for k in REQUIRED) and ml.get("meta", {}).get("mode") == "check",
          path)

    # ── ⑧ build.sh 源码树静默快照：函数级受控验证 + snapshot meta 契约 ─────
    # build.sh 在构建开始/结束各算一次源码树 sha（覆盖构建输入，排除 out/__pycache__ 等产物），
    # 不一致 → 明示警告 + manifest 记 source_tree_changed=1。这里按 build.sh 内的标记抽取
    # 两个函数，在 /tmp 小夹具上验证：确定性、改动源码触发、改动产物不误报；并锁住 build.sh
    # 依赖的 `--meta k=v` 透传契约（快照字段只追加，不改 run_record.py 的字段校验）。
    build_sh = os.path.join(_REPO, "build.sh")
    block8 = ""
    try:
        with open(build_sh, encoding="utf-8") as fh:
            blines = fh.read().splitlines()
        i0 = next(i for i, s in enumerate(blines)
                  if s.startswith("# >>> SOURCE-TREE-SNAPSHOT-BEGIN"))
        i1 = next(i for i, s in enumerate(blines)
                  if s.startswith("# <<< SOURCE-TREE-SNAPSHOT-END"))
        block8 = "\n".join(blines[i0 + 1:i1])
    except (OSError, StopIteration):
        block8 = ""
    check("⑧ build.sh 可抽取源码树快照函数（标记块存在）",
          bool(block8) and "_source_tree_sha()" in block8, f"block={len(block8)}B")
    fx = os.path.join(_TMP, "srctree-fixture")
    for rel in ("build.sh", "Engine/a.py", "sources-mac/A.swift", "sources-win/x.py",
                "编辑笔记/模板文件/t.tex", "Resources/i.icns",
                "Engine/out/generated.py", "Engine/__pycache__/old.pyc",
                "sources-win/__pycache__/bridge.pyc", "Engine/.DS_Store",
                "sources-mac/README.md"):
        p = os.path.join(fx, rel)
        os.makedirs(os.path.dirname(p) or fx, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("payload\n")
    probe = os.path.join(_TMP, "srctree_probe.sh")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/bash\nset -euo pipefail\n" + block8 + """
fx="$1"
_source_tree_sha "$fx"; s1="$SOURCE_TREE_SHA_LAST"; n="$SOURCE_TREE_FILE_COUNT"
_source_tree_sha "$fx"; s2="$SOURCE_TREE_SHA_LAST"
echo "DET=$([ "$s1" = "$s2" ] && echo 1 || echo 0)"
echo "FILES=$n"
# 产物 / 非输入改动：out/、__pycache__（*.pyc）、Engine/.DS_Store、非 .swift —— 都不该改变 sha
printf 'y\\n' >> "$fx/Engine/out/generated.py"
printf 'w\\n' >> "$fx/Engine/__pycache__/old.pyc"
printf 'v\\n' >> "$fx/sources-win/__pycache__/bridge.pyc"
printf 'u\\n' >> "$fx/sources-mac/README.md"
_source_tree_sha "$fx"; s3="$SOURCE_TREE_SHA_LAST"
echo "EXCL_SAME=$([ "$s1" = "$s3" ] && echo 1 || echo 0)"
# 真源码改动 → 前后不一致 → changed=1 + 明示警告
printf 'x\\n' >> "$fx/Engine/a.py"
_source_tree_check "$fx" "$s1" 2>"$fx/warn.txt"
echo "MUT_CHANGED=$SOURCE_TREE_CHANGED"
grep -q '构建期间源码被修改（写手未静默）：source_tree changed' "$fx/warn.txt" \
  && echo "WARN_HIT=1" || echo "WARN_HIT=0"
""")
    r8 = subprocess.run(["bash", probe, fx], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=120)
    v8 = {}
    for line in (r8.stdout or "").splitlines():
        if "=" in line:
            k, val = line.split("=", 1)
            v8[k] = val
    check("⑧ 快照确定性：同态两次同 sha、夹具恰 6 个输入文件",
          r8.returncode == 0 and v8.get("DET") == "1" and v8.get("FILES") == "6",
          {"rc": r8.returncode, "out": (r8.stdout or "")[-200:], "err": (r8.stderr or "")[-200:]})
    check("⑧ 产物/非输入改动不改变 sha（out/__pycache__/*.pyc/.DS_Store/非 .swift 均排除）",
          v8.get("EXCL_SAME") == "1", v8.get("EXCL_SAME"))
    check("⑧ 源码改动 → source_tree_changed=1 且警告文本命中",
          v8.get("MUT_CHANGED") == "1" and v8.get("WARN_HIT") == "1",
          {k: v8.get(k) for k in ("MUT_CHANGED", "WARN_HIT")})
    d6 = os.path.join(_TMP, "srctree-meta")
    rc6, _, _ = run_rr(["--record-only", "--label", "unit-srctree", "--out-dir", d6,
                        "--started-at", started, "--command", "bash build.sh",
                        "--exit-code", "0", "--engine-sha", precomputed_sha,
                        "--purpose", "verify-build",
                        "--meta", "source_tree_sha_start=" + "b" * 64,
                        "--meta", "source_tree_sha_end=" + "c" * 64,
                        "--meta", "source_tree_changed=1",
                        "--meta", "source_tree_files=6"])
    m8 = load(manifests(d6)[0]) if manifests(d6) else {}
    meta8 = m8.get("meta") or {}
    check("⑧ 4 个快照字段经既有 --meta 通道原样落盘（字符串，向后兼容）",
          rc6 == 0 and meta8.get("source_tree_sha_start") == "b" * 64
          and meta8.get("source_tree_sha_end") == "c" * 64
          and meta8.get("source_tree_changed") == "1" and meta8.get("source_tree_files") == "6"
          and all(k in m8 for k in REQUIRED),
          {k: meta8.get(k) for k in ("source_tree_changed", "source_tree_files")})
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print("=" * 70)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
