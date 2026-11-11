#!/usr/bin/env python3
r"""P02H：源指纹加固（OBS-01 自动采纳 bib / OBS-02 coop 两段式）回归。

本文件随 P02H（批次外 FIX，2026-11-11）新增，覆盖卡 §5/§6 的 R01–R04：
  · R01 / OBS-01：指纹 schema 2 必须覆盖**全部实际被消费**的参考文献输入——
    显式 `--bib`（role=explicit）、源目录/父目录自动发现的 `.bib`（role=auto-dir，
    与 `find_bib()` 同口径）、自动采纳/显式给的参考文献附件（role=refs）。
    任一变化 → 同 `--name` 重跑 rc2 + 中文差异 + **零写**；`--force` 才覆盖并重写指纹。
    同输入重跑仍 rc0 且产物（含指纹文件）逐字节不变；同一输入在不同输出目录
    算出的指纹内容逐字节相同（不含绝对路径/时间戳）。
  · R02：schema 1 旧指纹可读（视为单 bib）；旧交付目录无指纹 → warn-only 继续，
    成功后补写 schema 2。
  · R03 / OBS-02：coop 两段式 ph1（`--coop-taskbook-only`）不写最终交付目录、不受
    final 守卫影响，并把本次输入指纹存进状态；ph2（`--coop-replies`）在 promote 与
    任何写盘之前用 final_dir 既有指纹核对 → 换源 rc2 时 final/暂存/状态零改动；
    `--force` 才允许整体替换。
  · R04：ph2 promote 成功后把本次输入指纹（schema 2）写进 final_dir；下一轮同输入
    ph2 不误拦（指纹一致 → rc0）。

纪律：全部离线——不联网、不调付费 API、不读真实稿件库/notes 库/设置、不跑 TeX
（不加 `--compile`）。夹具全部自建，写在临时目录里。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_pkg_p02h_guard_hardening.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_PUBLIC = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_PUBLIC, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CONVERT = os.path.join(_ENGINE, "convert.py")
_FP_NAME = ".slcn-源指纹.json"
_STATE_NAME = "_AI协同状态.json"

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  {detail}" if not cond and detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, detail: str = "") -> None:
    """环境缺依赖（如无 xelatex/pypdf）时跳过断言，不计失败（仓内既有口径）。"""
    print("SKIP  " + name + (f"  {detail}" if detail else ""))
    SKIPS.append(name)


def _env(tmp: str) -> dict[str, str]:
    """隔离环境：不联网、不读真实稿件库/notes/设置，启动日志写临时目录。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    env["SLCN_MANUSCRIPTS"] = os.path.join(tmp, "NO_MANUSCRIPTS")
    env["SLCN_NOTES"] = os.path.join(tmp, "NO_NOTES")
    env["P2_SETTINGS"] = os.path.join(tmp, "settings.json")
    env["SLCN_STARTUP_LOG"] = os.path.join(tmp, "startup.log")
    return env


def _run(tmp: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CONVERT, *args], cwd=_ENGINE, env=_env(tmp),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _tree(root: str) -> dict[str, str]:
    """目录清单：相对路径 → sha256（判「零写/逐字节不变」）。"""
    out: dict[str, str] = {}
    for dp, dns, fs in os.walk(root):
        dns[:] = [d for d in dns if d != "__MACOSX"]
        for f in sorted(fs):
            full = os.path.join(dp, f)
            out[os.path.relpath(full, root)] = _sha256(full)
    return out


# 覆盖既有文件时 build 会留带时间戳的 `.bak` 备份（既有行为、文件名含时间），
# 「同输入重跑逐字节一致」只在**交付内容**上断言，备份文件名单独排除。
_BAK_RE = re.compile(r"\.\d{8}-\d{6}-\d+\.bak$")


def _tree_content(root: str) -> dict[str, str]:
    return {k: v for k, v in _tree(root).items() if not _BAK_RE.search(k)}


def _tail(p: subprocess.CompletedProcess, n: int = 12) -> str:
    return "\n".join((p.stdout or "").splitlines()[-n:]
                     + (["[stderr]"] + (p.stderr or "").splitlines()[-n:]
                        if p.stderr else []))


def _fp(path: str) -> dict:
    try:
        data = json.loads(_read(path))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ── 夹具 ────────────────────────────────────────────────────────────────

_TEX = """\\documentclass{article}
\\title{指纹加固测试稿}
\\author{张三}
\\begin{document}
\\maketitle
正文引用\\cite{k1}。
\\end{document}
"""

_TEX_B = """\\documentclass{article}
\\title{乙版测试稿（完全不同的稿子）}
\\author{李四}
\\begin{document}
\\maketitle
乙版正文引用\\cite{k9}。
\\end{document}
"""

_BIB_A = ("@article{k1,\n  author = {张三},\n  title = {甲文献},\n"
          "  year = {2020},\n}\n")
_BIB_B = ("@article{k1,\n  author = {张三},\n  title = {乙文献（内容已改）},\n"
          "  year = {2022},\n}\n")
_BIB_B2 = ("@article{k9,\n  author = {李四},\n  title = {乙版文献},\n"
           "  year = {2021},\n}\n")


def _src_tex(base: str, name: str = "main.tex", text: str = _TEX) -> str:
    d = os.path.join(base, "src")
    p = os.path.join(d, name)
    _write_text(p, text)
    return p


def _deliver(tmp: str, *args: str) -> subprocess.CompletedProcess:
    return _run(tmp, *args, "--no-original")


# ── R01：LaTeX 自动发现的 .bib 也进指纹（OBS-01 主反例）─────────────────


def test_r01_auto_dir_bib(tmp: str) -> None:
    base = os.path.join(tmp, "auto")
    src = _src_tex(base)
    bib = os.path.join(base, "src", "reference.bib")
    _write_text(bib, _BIB_A)
    out = os.path.join(base, "out")
    fp_path = os.path.join(out, "Same", _FP_NAME)
    common = ("--source", src, "--out", out, "--name", "Same")

    r1 = _deliver(tmp, *common)
    check("P02H-R01-1 LaTeX 自动发现 .bib 首次转换 rc0", r1.returncode == 0, _tail(r1))
    fp = _fp(fp_path)
    check("P02H-R01-2 指纹 schema=2 且字段为 source/bibs（无旧 bib 键）",
          fp.get("schema") == 2 and set(fp.keys()) == {"schema", "stem", "source", "bibs"},
          str(fp)[:300])
    check("P02H-R01-3 指纹记录源稿 name/size/sha256",
          (fp.get("source") or {}).get("name") == "main.tex"
          and (fp.get("source") or {}).get("size") == os.path.getsize(src)
          and (fp.get("source") or {}).get("sha256") == _sha256(src),
          str(fp.get("source"))[:200])
    _bib_expected = {"role": "auto-dir", "name": "reference.bib",
                     "size": os.path.getsize(bib), "sha256": _sha256(bib)}
    check("P02H-R01-4 自动发现的 .bib 以 role=auto-dir 进指纹",
          fp.get("bibs") == [_bib_expected], str(fp.get("bibs"))[:300])
    check("P02H-R01-5 指纹不含时间戳/绝对路径（无 created/时间字段、无目录前缀）",
          "created" not in fp and "time" not in json.dumps(fp, ensure_ascii=False)
          and base not in _read(fp_path), _read(fp_path)[:300])

    tree1 = _tree(os.path.join(out, "Same"))
    fp1_bytes = _read_bytes(fp_path)
    r2 = _deliver(tmp, *common)
    check("P02H-R01-6 同输入重跑 rc0（不误拦）", r2.returncode == 0, _tail(r2))
    check("P02H-R01-7 同输入重跑交付内容逐字节不变、指纹文件不变（排除时间戳 .bak）",
          _tree_content(os.path.join(out, "Same"))
          == {k: v for k, v in tree1.items() if not _BAK_RE.search(k)}
          and _read_bytes(fp_path) == fp1_bytes,
          str(sorted(_tree(os.path.join(out, "Same")))[:8]))
    # 同输入换输出目录：指纹内容必须与目录无关（确定、无绝对路径）。
    out_b = os.path.join(base, "out_b")
    rb = _deliver(tmp, "--source", src, "--out", out_b, "--name", "Same")
    check("P02H-R01-8 同输入不同输出目录的指纹逐字节相同",
          rb.returncode == 0
          and _read_bytes(os.path.join(out_b, "Same", _FP_NAME)) == _read_bytes(fp_path),
          _tail(rb))

    # 只改自动采纳的 bib 内容，同命令同 --name 重跑 → 必须 rc2 + 零写。
    _write_text(bib, _BIB_B)
    tree_before_r3 = _tree(os.path.join(out, "Same"))
    r3 = _deliver(tmp, *common)
    check("P02H-R01-9 自动发现 .bib 变更后同 --name 重跑 rc2", r3.returncode == 2, _tail(r3))
    check("P02H-R01-10 rc2 中文提示指出差异（拒绝覆盖 + 参考文献库）",
          "拒绝覆盖" in (r3.stderr or "") and "参考文献库" in (r3.stderr or ""),
          (r3.stderr or "")[:400])
    check("P02H-R01-11 rc2 时 stdout 不打印完成契约行",
          "完成。产物目录：" not in (r3.stdout or ""))
    check("P02H-R01-12 rc2 时既有交付目录零改动",
          _tree(os.path.join(out, "Same")) == tree_before_r3)

    r4 = _deliver(tmp, *common, "--force")
    check("P02H-R01-13 --force 视为显式确认：覆盖 rc0", r4.returncode == 0, _tail(r4))
    fp2 = _fp(fp_path)
    check("P02H-R01-14 --force 后指纹更新为本次 bib（schema 仍 2）",
          fp2.get("schema") == 2
          and [(b.get("role"), b.get("sha256")) for b in (fp2.get("bibs") or [])]
          == [("auto-dir", _sha256(bib))], str(fp2.get("bibs"))[:300])
    _deliv_bib = _read(os.path.join(out, "Same", "reference.bib"))
    check("P02H-R01-15 --force 后产物里的 reference.bib 已被替换（新标题在、旧 sha 消失）",
          "乙文献（内容已改）" in _deliv_bib
          and _sha256(os.path.join(out, "Same", "reference.bib")) != tree1["reference.bib"],
          _deliv_bib[:200])


# ── R01：源内声明 bib 与 find_bib 命中不一致时，只记**实际被消费**那份 ────


_TEX_DECL = """\\documentclass{article}
\\title{声明 bib 测试稿}
\\author{张三}
\\begin{document}
\\maketitle
正文引用\\cite{k1}。\\bibliography{custom}
\\end{document}
"""


def test_r01_declared_vs_found(tmp: str) -> None:
    base = os.path.join(tmp, "declared")
    src = _src_tex(base, text=_TEX_DECL)
    # 源内声明 custom.bib（存在）→ 真 .bib 判定会指向它；但 find_bib() 命中
    # reference.bib（优先），build_latex 实际读入的也是 reference.bib。
    custom = os.path.join(base, "src", "custom.bib")
    found = os.path.join(base, "src", "reference.bib")
    _write_text(custom, _BIB_B)
    _write_text(found, _BIB_A)
    # 子目录里再放一个能被自动采纳的附件：有真 .bib 时**不得**合并。
    attach = os.path.join(base, "src", "附件", "refs.bib")
    _write_text(attach, _BIB_B2)
    out = os.path.join(base, "out")
    fp_path = os.path.join(out, "Same", _FP_NAME)
    common = ("--source", src, "--out", out, "--name", "Same")

    r1 = _deliver(tmp, *common)
    check("P02H-R01-25 声明 bib 与 find_bib 命中不一致时首次转换 rc0",
          r1.returncode == 0, _tail(r1))
    fp = _fp(fp_path)
    check("P02H-R01-26 指纹只记实际被 build_latex 消费的那份（reference.bib，不记声明未消费的 custom.bib）",
          [(b.get("role"), b.get("name"), b.get("sha256"))
           for b in (fp.get("bibs") or [])]
          == [("auto-dir", "reference.bib", _sha256(found))],
          str(fp.get("bibs"))[:400])
    check("P02H-R01-27 有真 .bib 时附件不自动合并（无 refs 角色）",
          all(b.get("role") != "refs" for b in (fp.get("bibs") or [])))

    tree1 = _tree(os.path.join(out, "Same"))
    fp1 = _read_bytes(fp_path)
    # 改「声明了但没被消费」的 custom.bib → 产物不变，守卫不得误拦。
    _write_text(custom, _BIB_A)
    r2 = _deliver(tmp, *common)
    check("P02H-R01-28 改未消费的声明 bib：同 --name 重跑 rc0（不误拦）且指纹不变",
          r2.returncode == 0 and _read_bytes(fp_path) == fp1
          and _tree_content(os.path.join(out, "Same"))
          == {k: v for k, v in tree1.items() if not _BAK_RE.search(k)}, _tail(r2))

    # 改实际消费的 reference.bib → 必须拦。
    tree_before = _tree(os.path.join(out, "Same"))
    _write_text(found, _BIB_B)
    r3 = _deliver(tmp, *common)
    check("P02H-R01-29 改实际消费的 bib：rc2 + 中文差异 + 零写",
          r3.returncode == 2 and "拒绝覆盖" in (r3.stderr or "")
          and _tree(os.path.join(out, "Same")) == tree_before,
          (r3.stderr or "")[:300])



# ── R01：自动采纳的参考文献附件（args.refs，role=refs）──────────────────


def test_r01_auto_refs(tmp: str) -> None:
    base = os.path.join(tmp, "refs")
    src = _src_tex(base)
    # 附件放在子目录：find_bib 不搜子目录 → 无「真 .bib」→ 会被自动采纳为附件。
    attach = os.path.join(base, "src", "附件", "refs.bib")
    _write_text(attach, _BIB_A)
    out = os.path.join(base, "out")
    fp_path = os.path.join(out, "Same", _FP_NAME)
    common = ("--source", src, "--out", out, "--name", "Same")

    r1 = _deliver(tmp, *common)
    check("P02H-R01-16 自动采用参考文献附件（rc0 + 日志点名）",
          r1.returncode == 0 and "自动采用参考文献附件" in (r1.stdout or ""), _tail(r1))
    fp = _fp(fp_path)
    expect = {"role": "refs", "name": "refs.bib",
              "size": os.path.getsize(attach), "sha256": _sha256(attach)}
    check("P02H-R01-17 自动采纳的附件以 role=refs 进指纹",
          fp.get("bibs") == [expect], str(fp.get("bibs"))[:300])
    tree1 = _tree(os.path.join(out, "Same"))

    _write_text(attach, _BIB_B)
    r2 = _deliver(tmp, *common)
    check("P02H-R01-18 附件内容变更后同 --name 重跑 rc2", r2.returncode == 2, _tail(r2))
    check("P02H-R01-19 rc2 提示参考文献附件（中文）+ 零写",
          "参考文献附件" in (r2.stderr or "")
          and _tree(os.path.join(out, "Same")) == tree1, (r2.stderr or "")[:300])

    r3 = _deliver(tmp, *common, "--force")
    fp2 = _fp(fp_path)
    check("P02H-R01-20 附件变更 --force rc0 且指纹更新",
          r3.returncode == 0
          and [(b.get("role"), b.get("sha256")) for b in (fp2.get("bibs") or [])]
          == [("refs", _sha256(attach))], _tail(r3))


# ── R01：显式 --bib 正对照（既有行为保持）────────────────────────────────


def test_r01_explicit_bib(tmp: str) -> None:
    base = os.path.join(tmp, "explicit")
    src = _src_tex(base)
    bibdir = os.path.join(base, "bibs")
    bib1 = os.path.join(bibdir, "refs1.bib")
    bib2 = os.path.join(bibdir, "refs2.bib")
    _write_text(bib1, _BIB_A)
    _write_text(bib2, _BIB_B)
    out = os.path.join(base, "out")
    fp_path = os.path.join(out, "Same", _FP_NAME)

    r1 = _deliver(tmp, "--source", src, "--out", out, "--name", "Same", "--bib", bib1)
    check("P02H-R01-21 显式 --bib 首次转换 rc0", r1.returncode == 0, _tail(r1))
    fp = _fp(fp_path)
    check("P02H-R01-22 显式 --bib 以 role=explicit 进指纹",
          fp.get("bibs") == [{"role": "explicit", "name": "refs1.bib",
                              "size": os.path.getsize(bib1), "sha256": _sha256(bib1)}],
          str(fp.get("bibs"))[:300])

    tree1 = _tree(os.path.join(out, "Same"))
    r2 = _deliver(tmp, "--source", src, "--out", out, "--name", "Same", "--bib", bib2)
    check("P02H-R01-23 换显式 --bib 同 --name 重跑 rc2 + 中文差异 + 零写",
          r2.returncode == 2 and "参考文献库" in (r2.stderr or "")
          and _tree(os.path.join(out, "Same")) == tree1, (r2.stderr or "")[:300])
    r3 = _deliver(tmp, "--source", src, "--out", out, "--name", "Same", "--bib", bib2,
                  "--force")
    _deliv_bib2 = _read(os.path.join(out, "Same", "reference.bib"))
    check("P02H-R01-24 换 --bib 加 --force 覆盖 rc0、指纹更新、产物 bib 同步",
          r3.returncode == 0
          and [(b.get("role"), b.get("sha256"))
               for b in (_fp(fp_path).get("bibs") or [])] == [("explicit", _sha256(bib2))]
          and "乙文献（内容已改）" in _deliv_bib2
          and _sha256(os.path.join(out, "Same", "reference.bib")) != tree1["reference.bib"],
          _tail(r3))


# ── R02：schema 1 旧指纹可读（视为单 bib）＋ 无指纹 warn-only ────────────


def test_r02_legacy(tmp: str) -> None:
    base = os.path.join(tmp, "legacy")
    src = _src_tex(base)
    bibdir = os.path.join(base, "bibs")
    bib1 = os.path.join(bibdir, "refs1.bib")
    bib2 = os.path.join(bibdir, "refs2.bib")
    _write_text(bib1, _BIB_A)
    _write_text(bib2, _BIB_B)
    out = os.path.join(base, "out")
    fp_path = os.path.join(out, "Same", _FP_NAME)
    common = ("--source", src, "--out", out, "--name", "Same", "--bib", bib1)

    r1 = _deliver(tmp, *common)
    check("P02H-R02-1 显式 --bib 交付 rc0 且有 schema 2 指纹",
          r1.returncode == 0 and _fp(fp_path).get("schema") == 2, _tail(r1))
    # 手写 schema 1 旧指纹（旧版只记源稿 + 显式 --bib）。
    _write_text(fp_path, json.dumps({
        "schema": 1, "stem": "Same",
        "source": {"name": "main.tex", "size": os.path.getsize(src),
                   "sha256": _sha256(src)},
        "bib": {"name": "refs1.bib", "size": os.path.getsize(bib1),
                "sha256": _sha256(bib1)},
    }, ensure_ascii=False, indent=2) + "\n")
    r2 = _deliver(tmp, *common)
    check("P02H-R02-2 schema 1 旧指纹可读：同输入重跑 rc0（不误拦）",
          r2.returncode == 0, _tail(r2))
    check("P02H-R02-3 旧指纹路径成功后补写 schema 2",
          _fp(fp_path).get("schema") == 2, _read(fp_path)[:200])

    tree1 = _tree(os.path.join(out, "Same"))
    r3 = _deliver(tmp, "--source", src, "--out", out, "--name", "Same", "--bib", bib2)
    check("P02H-R02-4 schema 1 指纹同样拦换 --bib（rc2 + 零写）",
          r3.returncode == 2 and _tree(os.path.join(out, "Same")) == tree1,
          (r3.stderr or "")[:300])

    # 旧交付目录无指纹 → warn-only 继续，成功后补写 schema 2。
    out2 = os.path.join(base, "out2")
    q1 = _deliver(tmp, "--source", src, "--out", out2, "--name", "Same")
    fp2_path = os.path.join(out2, "Same", _FP_NAME)
    check("P02H-R02-5 另起交付并删掉指纹（构造旧目录）",
          q1.returncode == 0 and os.path.isfile(fp2_path), _tail(q1))
    os.remove(fp2_path)
    q2 = _deliver(tmp, "--source", src, "--out", out2, "--name", "Same")
    check("P02H-R02-6 无指纹旧目录：warn-only 继续 rc0",
          q2.returncode == 0 and "没有源指纹记录" in (q2.stdout or ""), _tail(q2))
    q_fp = _fp(fp2_path)
    check("P02H-R02-7 兼容路径成功后补写 schema 2 指纹",
          q_fp.get("schema") == 2
          and (q_fp.get("source") or {}).get("sha256") == _sha256(src),
          _read(fp2_path)[:200])


# ── R03/R04：coop 两段式（OBS-02 主反例）─────────────────────────────────


def test_r03_coop(tmp: str) -> None:
    base = os.path.join(tmp, "coop")
    src_a = _src_tex(os.path.join(base, "A"))
    bib_a = os.path.join(base, "A", "src", "reference.bib")
    _write_text(bib_a, _BIB_A)
    src_b = _src_tex(os.path.join(base, "B"), text=_TEX_B)
    bib_b = os.path.join(base, "B", "src", "reference.bib")
    _write_text(bib_b, _BIB_B2)
    out = os.path.join(base, "out")
    final = os.path.join(out, "Same")
    stage = os.path.join(out, "Same_待核")
    replies = os.path.join(base, "replies")
    _write_text(os.path.join(replies, "r1.md"), _BIB_B2)

    # ① 常规交付 A（带 schema 2 指纹）。
    ra = _deliver(tmp, "--source", src_a, "--out", out, "--name", "Same",
                  "--author", "张三")
    final_a = _tree(final)
    check("P02H-R03-1 常规交付 A rc0 且有指纹",
          ra.returncode == 0 and _fp(os.path.join(final, _FP_NAME)).get("schema") == 2,
          _tail(ra))

    # ② 换源 B 跑 ph1：不写 final_dir，也不受 final 守卫影响（A 的指纹还在）。
    p1 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--author", "李四", "--verify-refs", "--no-ai-merge",
                  "--coop-taskbook-only", "--coop-stage", stage)
    check("P02H-R03-2 ph1（--coop-taskbook-only）换源 rc0，不受 final 守卫影响",
          p1.returncode == 0, _tail(p1))
    check("P02H-R03-3 ph1 不写最终交付目录（A 逐字节不变）", _tree(final) == final_a)
    state_path = os.path.join(stage, _STATE_NAME)
    state = _fp(state_path)
    _incoming = state.get("input_fingerprint") or {}
    check("P02H-R03-4 ph1 把本次输入指纹（schema 2 内容）存进状态（只增键）",
          (_incoming.get("source") or {}).get("sha256") == _sha256(src_b)
          and [(b.get("role"), b.get("sha256"))
               for b in (_incoming.get("bibs") or [])] == [("auto-dir", _sha256(bib_b))]
          and state.get("schema") == 1, str(_incoming)[:400])

    stage_before = _tree(stage)
    state_sha_before = _sha256(state_path)

    # ③ ph2：final 里是 A 的指纹，本次输入是 B → 必须在 promote/写盘之前 rc2。
    p2 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--coop-replies", replies)
    check("P02H-R03-5 ph2 换源：rc2 + 中文差异（拒绝覆盖/源稿）",
          p2.returncode == 2 and "拒绝覆盖" in (p2.stderr or "")
          and "源稿" in (p2.stderr or ""), (p2.stderr or "")[:400])
    check("P02H-R03-6 ph2 rc2 时 stdout 不打印完成契约行",
          "完成。产物目录：" not in (p2.stdout or ""))
    check("P02H-R03-7 ph2 rc2 时最终交付目录零改动（A 仍在、A 的指纹未变）",
          _tree(final) == final_a)
    check("P02H-R03-8 ph2 rc2 时暂存目录零改动、状态文件未被改写",
          _tree(stage) == stage_before and _sha256(state_path) == state_sha_before)

    # ④ --force 才允许整体替换；promote 成功后 final_dir 记录本次指纹（R04）。
    p3 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--coop-replies", replies, "--force")
    check("P02H-R03-9 ph2 加 --force 覆盖 rc0", p3.returncode == 0, _tail(p3))
    final_tex = _read(os.path.join(final, "Same.tex"))
    check("P02H-R03-10 --force 后最终交付整体是 B（乙版在、甲版消失）",
          "乙版" in final_tex and "甲版" not in final_tex)
    fp_after = _fp(os.path.join(final, _FP_NAME))
    check("P02H-R03-11 promote 成功后 final_dir 写入本次指纹（schema 2，指向 B）",
          fp_after.get("schema") == 2
          and (fp_after.get("source") or {}).get("sha256") == _sha256(src_b)
          and [(b.get("role"), b.get("sha256"))
               for b in (fp_after.get("bibs") or [])] == [("auto-dir", _sha256(bib_b))],
          _read(os.path.join(final, _FP_NAME))[:300])
    check("P02H-R03-12 交付成功后暂存目录被清理（既有行为不变）",
          not os.path.isdir(stage))

    # ⑤ 同输入再走一轮两段式：指纹一致 → ph2 不误拦（rc0）。
    stage2 = os.path.join(out, "Same_待核")
    q1 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--author", "李四", "--verify-refs", "--no-ai-merge",
                  "--coop-taskbook-only", "--coop-stage", stage2)
    q2 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--coop-replies", replies)
    check("P02H-R03-13 同输入第二轮 ph1+ph2：指纹一致 → ph2 rc0（不误拦）",
          q1.returncode == 0 and q2.returncode == 0, _tail(q1) + "\n" + _tail(q2))
    check("P02H-R03-14 第二轮交付后指纹仍指向 B（未被污染）",
          (lambda d: d.get("schema") == 2
           and (d.get("source") or {}).get("sha256") == _sha256(src_b))(
              _fp(os.path.join(final, _FP_NAME))))


    # ⑥ 旧版暂存状态没有 input_fingerprint → 只按源稿 sha256 回退核对（不崩、披露）。
    stage3 = os.path.join(out, "Same_待核")
    t1 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--author", "李四", "--verify-refs", "--no-ai-merge",
                  "--coop-taskbook-only", "--coop-stage", stage3)
    state3_path = os.path.join(stage3, _STATE_NAME)
    state3 = _fp(state3_path)
    state3.pop("input_fingerprint", None)
    _write_text(state3_path, json.dumps(state3, ensure_ascii=False, indent=2) + "\n")
    t2 = _deliver(tmp, "--source", src_b, "--out", out, "--name", "Same",
                  "--coop-replies", replies)
    check("P02H-R03-15 旧状态缺 input_fingerprint：同源 ph2 rc0（source-only 回退不崩）",
          t1.returncode == 0 and t2.returncode == 0, _tail(t1) + "\n" + _tail(t2))
    check("P02H-R03-16 旧状态回退路径在 stdout 披露「没有输入指纹记录」",
          "没有输入指纹记录" in (t2.stdout or ""), (t2.stdout or "")[-600:])

    # ⑦ 旧状态 + 换源：source-only 回退也必须挡住换稿（rc2），final 零改动。
    final_before = _tree(final)
    stage4 = os.path.join(out, "Same_待核")
    u1 = _deliver(tmp, "--source", src_a, "--out", out, "--name", "Same",
                  "--author", "张三", "--verify-refs", "--no-ai-merge",
                  "--coop-taskbook-only", "--coop-stage", stage4)
    state4_path = os.path.join(stage4, _STATE_NAME)
    state4 = _fp(state4_path)
    state4.pop("input_fingerprint", None)
    _write_text(state4_path, json.dumps(state4, ensure_ascii=False, indent=2) + "\n")
    u2 = _deliver(tmp, "--source", src_a, "--out", out, "--name", "Same",
                  "--coop-replies", replies)
    check("P02H-R03-17 旧状态缺键 + 换源：ph2 仍 rc2（source-only 回退挡换稿）",
          u1.returncode == 0 and u2.returncode == 2 and "拒绝覆盖" in (u2.stderr or ""),
          _tail(u2))
    check("P02H-R03-18 旧状态回退 rc2 时最终交付目录零改动",
          _tree(final) == final_before)


# ── R06：元数据槽位的 TeX 文本模式转义（OBS-INC-01 / P02-ESC）───────────
# 作者稿题名/摘要里的裸 % & # _ 直接写进 \mytitle{}/\myabstract{} 时，base 上
# xelatex 编译失败（% 注释整行、& 对齐错、# 宏参数错、_ 缺 $），rc2。
# 候选在写槽位前统一转义，同时保留作者刻意写的控制序列与数学。
# 编译类断言在无 xelatex / 无 pypdf 时 SKIP（与 b375r 等既有测试同口径）。

_XELATEX = shutil.which("xelatex") or (
    "/Library/TeX/texbin/xelatex"
    if os.path.exists("/Library/TeX/texbin/xelatex") else None)

try:
    import pypdf  # noqa: F401  # 只为判定 PDF 文本断言能否执行
    _PDF_OK = True
except Exception:  # noqa: BLE001 —— 缺 pypdf 只影响 PDF 断言
    _PDF_OK = False

_TEX_R06 = """\\documentclass{article}
\\title{占位题名}
\\author{张三}
\\begin{document}
\\maketitle
正文。
\\end{document}
"""


def _pdf_compact(path: str) -> str:
    """PDF 全文（去掉所有空白，便于断言「原文命中」）。"""
    from pypdf import PdfReader
    text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    return re.sub(r"\s+", "", text)


def test_r06_metadata_escape(tmp: str) -> None:
    from docx_fixture_gen import make_docx
    from slcn.latex import escape_metadata_value as esc

    # ① 纯函数契约（不依赖 TeX）
    check("P02H-R06-1 裸 % & # _ → \\% \\& \\# \\_",
          esc("100% 与 A&B 与 C#D 与 E_F")
          == "100\\% 与 A\\&B 与 C\\#D 与 E\\_F",
          esc("100% 与 A&B 与 C#D 与 E_F"))
    check("P02H-R06-2 幂等：已转义写法不再重复加反斜杠",
          esc("\\%\\&\\#\\_") == "\\%\\&\\#\\_"
          and esc(esc("100% & # _")) == esc("100% & # _"),
          esc(esc("100% & # _")))
    check("P02H-R06-3 保留控制序列与参数组（\\textit{ABC} / {\\kaishu 张三}）",
          esc("\\textit{ABC}") == "\\textit{ABC}"
          and esc("{\\kaishu 张三}") == "{\\kaishu 张三}"
          and esc("\\textbf{A_B}") == "\\textbf{A\\_B}",
          esc("\\textbf{A_B}"))
    check("P02H-R06-4 数学模式内不转义（$…$ / \\[…\\]）",
          esc("$a \\& b$ 与 \\[c \\& d\\] 与 $x_1$")
          == "$a \\& b$ 与 \\[c \\& d\\] 与 $x_1$",
          esc("$a \\& b$ 与 \\[c \\& d\\] 与 $x_1$"))
    check("P02H-R06-5 无特殊字符时逐字节 no-op",
          esc("逻辑与哲学研究：以数据库为例") == "逻辑与哲学研究：以数据库为例")

    base = os.path.join(tmp, "esc")
    os.makedirs(os.path.join(base, "src"), exist_ok=True)
    _old_path = os.environ.get("PATH", "")
    if _XELATEX:  # 编译子进程要能找到 xelatex（测试进程自己的 PATH 未必带）
        os.environ["PATH"] = os.path.dirname(_XELATEX) + os.pathsep + _old_path
    compile_args = ["--compile"] if _XELATEX else []
    try:
        # ② Word 通道：题名/摘要含裸 % & # _ → rc0 + tex 转义 + PDF 原文命中
        dx = os.path.join(base, "src", "esc.docx")
        make_docx(dx, paras=[
            "和号&井号#下划线_与百分号%的题名",
            "张三",
            "摘要：本文讨论元数据里的特殊字符 100% 与 A&B 与 C#D 与 E_F。",
            "关键词：转义；TeX",
            "一、引言",
            "本文讨论元数据里的特殊字符。",
            "二、结论",
            "需要正确转义。",
        ])
        out_docx = os.path.join(base, "out_docx")
        r1 = _deliver(tmp, "--source", dx, "--out", out_docx, *compile_args)
        check("P02H-R06-6 docx 题名含裸 % & # _：转换 rc0（base 此处 xelatex rc2）",
              r1.returncode == 0, _tail(r1))
        tex1 = _read(os.path.join(out_docx, "San_Zhang", "San_Zhang.tex"))
        check("P02H-R06-7 题名槽位写成转义形式（\\& \\# \\_ \\%）",
              "{和号\\&井号\\#下划线\\_与百分号\\%的题名}" in tex1,
              [ln for ln in tex1.splitlines() if "下划线" in ln][:2])
        check("P02H-R06-8 摘要槽位转义后不再被 % 截断（尾部字段仍在）",
              "100\\% 与 A\\&B 与 C\\#D 与 E\\_F" in tex1,
              [ln for ln in tex1.splitlines() if "E\\_F" in ln][:2])
        if _XELATEX and _PDF_OK:
            pdf1 = os.path.join(out_docx, "San_Zhang", "San_Zhang.pdf")
            txt1 = _pdf_compact(pdf1)
            check("P02H-R06-9 PDF 文本命中题名与摘要原文（& % # _ 正常显示）",
                  "和号&井号#下划线_与百分号%的题名" in txt1
                  and "100%与A&B与C#D与E_F" in txt1, txt1[:200])
        else:
            skip("P02H-R06-9 PDF 文本命中（无 xelatex 或 pypdf）",
                 f"xelatex={bool(_XELATEX)} pypdf={_PDF_OK}")

        # ③ LaTeX 通道 + CLI 覆盖：保留作者内联命令与数学，裸字面量照转义
        src2 = os.path.join(base, "src", "main.tex")
        _write_text(src2, _TEX_R06)
        out_cli = os.path.join(base, "out_cli")
        r2 = _deliver(tmp, "--source", src2, "--out", out_cli, "--name", "Esc",
                      "--title-cn", "\\textit{ABC} 与 {\\kaishu 张三} 与 & 题名",
                      "--abstract-cn",
                      "公式 $x_1$ 与 \\[a \\& b\\] 与裸_下划线 100%",
                      *compile_args)
        check("P02H-R06-10 CLI 覆盖含内联命令/数学：rc0", r2.returncode == 0, _tail(r2))
        tex2 = _read(os.path.join(out_cli, "Esc", "Esc.tex"))
        check("P02H-R06-11 保留 \\textit{ABC} 与 {\\kaishu 张三} 原样（不拆组、不二次转义）",
              "{\\textit{ABC} 与 {\\kaishu 张三} 与 \\& 题名}" in tex2
              and "\\textbackslash{}textit" not in tex2,
              [ln for ln in tex2.splitlines() if "ABC" in ln][:2])
        check("P02H-R06-12 数学 $x_1$ / \\[a \\& b\\] 原样保留",
              "$x_1$ 与 \\[a \\& b\\] 与裸\\_下划线 100\\%" in tex2,
              [ln for ln in tex2.splitlines() if "x_1" in ln][:2])
        if _XELATEX and _PDF_OK:
            txt2 = _pdf_compact(os.path.join(out_cli, "Esc", "Esc.pdf"))
            check("P02H-R06-13 PDF 命中 ABC/张三/&/裸_下划线100%",
                  "ABC与张三与&题名" in txt2 and "裸_下划线100%" in txt2,
                  txt2[:200])
        else:
            skip("P02H-R06-13 PDF 命中内联命令与字面量（无 xelatex 或 pypdf）",
                 f"xelatex={bool(_XELATEX)} pypdf={_PDF_OK}")

        # ④ --metadata-json 也走同一出口（§15.2⑤）：结构化值先清洗、不预先全转义，
        #    否则 `\textit{ABC}` 会被 escape_latex 变成 `\textbackslash{}textit`。
        meta_json = os.path.join(base, "src", "meta.json")
        _write_text(meta_json, json.dumps({
            "title_cn": "论 \\textit{ABC} 与 {\\kaishu 张三} 的 & # _ 50% 检验",
            "abstract_cn": ("摘要含数学 $x_1$、$a \\& b$ 与 \\[a \\& b\\] 以及已转义 \\% "
                            "和 A\\&B 对照，另见 \\url{https://example.org/x_y}。"),
            "keywords_cn": "论证；转义；检验",
        }, ensure_ascii=False) + "\n")
        out_json = os.path.join(base, "out_json")
        rj = _deliver(tmp, "--source", src2, "--out", out_json, "--name", "Json",
                      "--metadata-json", meta_json, "--author", "张三", *compile_args)
        texj = _read(os.path.join(out_json, "Json", "Json.tex"))
        check("P02H-R06-17 --metadata-json 保留 \\textit{ABC} 与 {\\kaishu 张三}（"
              "不出现 \\textbackslash{}textit）",
              rj.returncode == 0
              and "{论 \\textit{ABC} 与 {\\kaishu 张三} 的 \\& \\# \\_ 50\\% 检验}" in texj
              and "\\textbackslash{}textit" not in texj, _tail(rj))
        check("P02H-R06-18 --metadata-json 数学/已转义/\\url 原样保留",
              "$x_1$" in texj and "$a \\& b$" in texj and "\\[a \\& b\\]" in texj
              and "已转义 \\%" in texj and "\\url{https://example.org/x_y}" in texj,
              [ln for ln in texj.splitlines() if "x_1" in ln][:2])
        from slcn.build import structured_fields as _sf_fn
        _raw_zh = {"mytitle": "x", "myfirstauthor": "张三"}
        _f_direct = _sf_fn({"title_cn": "A & B"}, False, _raw_zh)
        _f_raw = _sf_fn({"title_cn": "A & B"}, False, _raw_zh, escape=False)
        check("P02H-R06-19 structured_fields 直调默认仍转义（①-6 契约不变），"
              "escape=False 只清洗",
              _f_direct[0].get("mytitle") == "A \\& B"
              and _f_raw[0].get("mytitle") == "A & B", str(_f_direct[0])[:120])
        if _XELATEX and _PDF_OK:
            txtj = _pdf_compact(os.path.join(out_json, "Json", "Json.pdf"))
            check("P02H-R06-20 --metadata-json 编译 rc0 且 PDF 命中 & # _ 50%",
                  "&#_50%检验" in txtj and "ABC" in txtj and "张三" in txtj, txtj[:200])
        else:
            skip("P02H-R06-20 --metadata-json PDF 命中（无 xelatex 或 pypdf）",
                 f"xelatex={bool(_XELATEX)} pypdf={_PDF_OK}")

        # ④ 幂等：CLI 里已经写好的 \% 不得被二次转义成 \\%
        out_idem = os.path.join(base, "out_idem")
        r3 = _deliver(tmp, "--source", src2, "--out", out_idem, "--name", "Idem",
                      "--title-cn", "100\\% 与 A\\&B 与 C\\#D 与 E\\_F")
        tex3 = _read(os.path.join(out_idem, "Idem", "Idem.tex"))
        check("P02H-R06-14 幂等：已转义写法原样进槽位（不出现 \\\\%）",
              r3.returncode == 0
              and "{100\\% 与 A\\&B 与 C\\#D 与 E\\_F}" in tex3
              and "\\\\%" not in tex3, _tail(r3) if r3.returncode else "")

        # ⑤ 无特殊字符夹具：槽位内容与输入逐字符一致（产物字节不变的可观测面）
        dx2 = os.path.join(base, "src", "plain.docx")
        make_docx(dx2, paras=[
            "逻辑与哲学研究", "张三", "摘要：本文讨论逻辑与哲学的关系。",
            "关键词：逻辑；哲学", "一、引言", "本文讨论逻辑与哲学的关系。",
        ])
        out_plain = os.path.join(base, "out_plain")
        r4 = _deliver(tmp, "--source", dx2, "--out", out_plain)
        tex4 = _read(os.path.join(out_plain, "San_Zhang", "San_Zhang.tex"))
        # ⑥ 元数据源文件（--metadata *.tex）：同样在写槽位前转义。
        #    注意 `.tex` 元数据文件本身是 LaTeX 源，值里写裸 `%` 会被解析阶段当注释
        #    （既有行为，卡 §15 不改），作者应写 `\%`；这里用 `& # _` 验出口。
        meta_tex = os.path.join(base, "src", "meta.tex")
        _write_text(meta_tex, "\\newcommand{\\mytitle}{含&与#与_的题名}\n")
        src_meta = os.path.join(base, "src", "meta_src.tex")
        _write_text(src_meta, _TEX_R06)
        out_meta = os.path.join(base, "out_meta")
        r5 = _deliver(tmp, "--source", src_meta, "--out", out_meta,
                      "--name", "Meta", "--metadata", meta_tex)
        tex5 = _read(os.path.join(out_meta, "Meta", "Meta.tex"))
        check("P02H-R06-16 元数据源文件（--metadata *.tex）写槽位前同样转义",
              r5.returncode == 0 and "{含\\&与\\#与\\_的题名}" in tex5,
              _tail(r5))

        check("P02H-R06-15 无特殊字符夹具：题名/摘要槽位与输入逐字符一致（无多余转义）",
              r4.returncode == 0
              and "{逻辑与哲学研究}" in tex4
              and "本文讨论逻辑与哲学的关系。" in tex4
              and "\\textbackslash" not in tex4, _tail(r4))
    finally:
        os.environ["PATH"] = _old_path


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slcn_p02h_") as tmp:
        test_r01_auto_dir_bib(tmp)
        test_r01_declared_vs_found(tmp)
        test_r01_auto_refs(tmp)
        test_r01_explicit_bib(tmp)
        test_r02_legacy(tmp)
        test_r03_coop(tmp)
        test_r06_metadata_escape(tmp)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项，跳过 {len(SKIPS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
