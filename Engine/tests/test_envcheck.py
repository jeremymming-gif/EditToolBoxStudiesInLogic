#!/usr/bin/env python3
"""首次运行环境自检（B-283）：`Engine/envcheck.py` 的判据与退出码。

这一套钉住八件事，全部**真跑子进程**（不是「代码看起来对」）：

  (a) 契约底座：`CHECK_IDS` 固定顺序、`detect_form()` 的 auto/exe/source；
  (b) **C1** `--json` 是合法 `envcheck.v1`：字段齐全、8 项、summary 与 checks 对得上；
  (c) **C2** 假 PATH 里什么都没有 → 人类输出列出缺失 + **当前平台**安装命令，退出 2；
  (d) **C3** 只有可选项缺失（无 git；`SLCN_SOFFICE` 指错）→ 必需项全 ok，退出 1；
  (e) **C4** 同一套假 PATH（有 TeX、有 soffice、无 python3.14）：source 判 fail 退出 2，exe 退出 0；
  (f) 单元级失败路径（注入探测）：soffice 真缺失 / 模板缺失 / 磁盘不足 / 输出目录不可写；
  (g) `--out-dir`：可写时落 `envcheck.json`；只读时判 fail 且**不**炸在半路；
  (h) dispatcher：`engine_main.ENTRIES` 原十项顺序不变、第 11 项是 envcheck，且真能跑通（PATH 同样备齐 soffice，理由见下）。

为什么用假 PATH 而不是「依赖本机装没装 TeX」：验收机器上三样工具**恰好都在**，
真缺的状态只能靠假 PATH 造；工具用 `#!/bin/sh` 小脚本 + `chmod 0o755`，测完自清。

为什么 (e)/(h) 的假 PATH 里还要放一个 `soffice`：`libreoffice` 是**可选**检查，缺它只把
退出码从 0 降到 1（不是失败）。本机 macOS 靠 `slcn.platform.find_soffice()` 的
`/Applications/LibreOffice.app/...` 兜底才碰巧找到；Linux CI 没有这条兜底，不造 stub 时
`--profile exe` 的 rc 会变成 1，C4/C8 的两条「退出 0」断言就会**随机器变红**。

`sources-win/tests/test_bridge.py` 的 (l) 组还从外壳侧断言同一份 argv 口径。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

import engine_main as _dispatcher  # noqa: E402
import envcheck as E  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


PY = sys.executable
PLAT = "windows" if os.name == "nt" else "mac"

STUBS = {
    "xelatex": '#!/bin/sh\necho "XeTeX 3.141592653-2.6-0.999995 (TeX Live 2024)"\n',
    "biber": '#!/bin/sh\necho "biber version: 2.19"\n',
    "python3.14": "#!/bin/sh\necho \"3.14.8\"\n",
    "git": '#!/bin/sh\necho "git version 2.39.3"\n',
    "soffice": '#!/bin/sh\necho "LibreOffice 24.8.1.2 420(Build:2)"\n',
}


def make_bin(tmp: pathlib.Path, names: tuple[str, ...]) -> pathlib.Path:
    """在 tmp 里放几个可执行假工具（`#!/bin/sh` + 固定输出）。"""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    for name in names:
        path = bin_dir / name
        path.write_text(STUBS[name], encoding="utf-8")
        os.chmod(path, 0o755)
    return bin_dir


def child_env(bin_dir: pathlib.Path, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)
    env["PYTHONPATH"] = str(ENGINE) + os.pathsep + str(ENGINE / "vendor")
    env.pop("SLCN_SOFFICE", None)
    env.pop("SLCN_PYTHON", None)
    env.update(extra or {})
    return env


def cli(args: list[str], bin_dir: pathlib.Path, extra: dict | None = None,
        script: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script or (ENGINE / "envcheck.py")), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ENGINE),
        env=child_env(bin_dir, extra),
    )


def by_id(report: dict) -> dict[str, dict]:
    return {c["id"]: c for c in report["checks"]}


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 契约底座：固定顺序 + 形态判定 ──")

check("(a) CHECK_IDS 是 spec 的八项、顺序固定",
      E.CHECK_IDS == ("engine_bundle", "xelatex", "biber", "libreoffice", "git",
                      "disk_space", "output_writable", "template_bundle"),
      repr(E.CHECK_IDS))
check("(a) detect_form：显式 profile 原样返回（这是 C4 的开关）",
      E.detect_form("exe") == "exe" and E.detect_form("source") == "source")
check("(a) detect_form(auto)：普通 python 进程判 source（本套件就是这么跑的）",
      E.detect_form("auto") == "source")
check("(a) 退出码口径：必需项 warn 算失败、可选项 warn 只降到 1、info 不参与",
      E.exit_code([{"level": "required", "status": "warn"}]) == 2
      and E.exit_code([{"level": "optional", "status": "warn"}]) == 1
      and E.exit_code([{"level": "info", "status": "ok"}]) == 0)

# ─────────────────────────────────────────────────────────────────────────
print("── (b) C1：--json 是合法 envcheck.v1（exe 形态、假 PATH 全 stub） ──")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    bin_dir = make_bin(tmp, ("xelatex", "biber"))
    out_dir = tmp / "out"
    r = cli(["--json", "--profile", "exe", "--out-dir", str(out_dir)], bin_dir)
    # 假 PATH 里没有 git → 可选项 warn → 退出 1（必需项仍全 ok；C1 不限定退出码）
    check("(b) 假 PATH 缺 git、必需项齐 → 退出 1", r.returncode == 1,
          f"rc={r.returncode} {r.stderr[-160:]!r}")
    report = {}
    try:
        report = json.loads(r.stdout)
    except Exception as e:                                    # noqa: BLE001
        check("(b) stdout 只有 JSON（不混人类输出）", False, f"{e}：{r.stdout[:120]!r}")
    check("(b) stdout 是纯 JSON，schema/profile/platform 齐全",
          report.get("schema") == "envcheck.v1" and report.get("profile") == "exe"
          and bool(report.get("platform")))
    checks = report.get("checks") or []
    check("(b) 恰好八项，顺序与 CHECK_IDS 一致",
          [c.get("id") for c in checks] == list(E.CHECK_IDS),
          repr([c.get("id") for c in checks]))
    fields_ok = all(
        isinstance(c.get(k), str) and isinstance(c.get("install"), dict)
        and isinstance(c["install"].get("mac"), str)
        and isinstance(c["install"].get("windows"), str)
        and c.get("level") in ("required", "optional", "info")
        and c.get("status") in ("ok", "warn", "fail")
        for c in checks for k in ("title", "detail", "hint", "version")
    )
    check("(b) 每项字段齐全（title/level/status/version/detail/hint/install×2）", fields_ok)
    summary = report.get("summary") or {}
    check("(b) summary 三个计数与 checks 逐项相加一致",
          summary == {"ok": sum(c["status"] == "ok" for c in checks),
                      "warn": sum(c["status"] == "warn" for c in checks),
                      "fail": sum(c["status"] == "fail" for c in checks)}, repr(summary))
    bundle = by_id(report).get("engine_bundle") or {}
    check("(b) exe 形态：engine_bundle=ok 且 detail 含「随包内置」",
          bundle.get("status") == "ok" and "随包内置" in bundle.get("detail", ""),
          bundle.get("detail", "")[:80])
    check("(b) 必需项全部 ok（磁盘/模板/TeX/biber/引擎）",
          all(c["status"] == "ok" for c in checks if c["level"] == "required"))
    check("(b) 报告同时落盘 <out-dir>/envcheck.json，内容与 stdout 一致",
          (out_dir / "envcheck.json").is_file()
          and json.loads((out_dir / "envcheck.json").read_text(encoding="utf-8")) == report)

# ─────────────────────────────────────────────────────────────────────────
print("── (c) C2：假 PATH 什么都没有 → 列缺失 + 当前平台安装命令，退出 2 ──")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    empty = tmp / "empty"
    empty.mkdir()
    r = cli(["--profile", "source"], empty)
    out = r.stdout
    check("(c) 退出 2", r.returncode == 2, f"rc={r.returncode}")
    check("(c) 人类输出点明缺 xelatex / biber", "xelatex" in out and "biber" in out)
    check("(c) 带「安装：」引导", "安装：" in out, out[-200:].replace("\n", " / "))
    cmd = E.INSTALL["xelatex"][PLAT]
    check(f"(c) hint 里是**当前平台**（{PLAT}）的安装命令", cmd in out, cmd)
    check("(c) 结论行说明有几项必需项缺失", "必需项缺失" in out)
    rj = cli(["--json", "--profile", "source"], empty)
    report = json.loads(rj.stdout)
    items = by_id(report)
    check("(c) JSON 同判：xelatex/biber/engine_bundle 都是 fail",
          all(items[i]["status"] == "fail" for i in ("xelatex", "biber", "engine_bundle")),
          repr({i: items[i]["status"] for i in ("xelatex", "biber", "engine_bundle")}))
    check("(c) 两项提示都带上可安装的路径（install 字段非空）",
          all(items[i]["install"][PLAT] for i in ("xelatex", "biber")))

# ─────────────────────────────────────────────────────────────────────────
print("── (d) C3：只有可选项缺失（无 git、SLCN_SOFFICE 指错）→ 退出 1 ──")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    bin_dir = make_bin(tmp, ("xelatex", "biber", "python3.14"))
    bad = str(tmp / "nowhere" / "soffice")
    r = cli(["--json", "--profile", "source"], bin_dir, {"SLCN_SOFFICE": bad})
    report = json.loads(r.stdout) if r.returncode in (0, 1, 2) and r.stdout.strip() else {}
    items = by_id(report)
    check("(d) 必需项全部 ok（TeX/biber/引擎都在）",
          all(c["status"] == "ok" for c in report.get("checks", [])
              if c["level"] == "required"),
          repr({c["id"]: c["status"] for c in report.get("checks", [])
                if c["level"] == "required"}))
    check("(d) 无 git → warn（不是 fail）", items.get("git", {}).get("status") == "warn")
    check("(d) SLCN_SOFFICE 指错 → libreoffice warn，且 detail 讲清是哪",
          items.get("libreoffice", {}).get("status") == "warn"
          and "SLCN_SOFFICE" in items.get("libreoffice", {}).get("detail", ""),
          items.get("libreoffice", {}).get("detail", ""))
    check("(d) libreoffice 的 hint 说明影响面（OLE / MathType）",
          "OLE" in items.get("libreoffice", {}).get("hint", "")
          and "MathType" in items.get("libreoffice", {}).get("hint", ""))
    check("(d) git 的 hint 说明影响面（同步）",
          "同步" in items.get("git", {}).get("hint", ""))
    check("(d) 退出 1（可选项缺失 ≠ 不能用）", r.returncode == 1, f"rc={r.returncode}")
    check("(d) summary 里 warn=2、fail=0",
          report.get("summary") == {"ok": 6, "warn": 2, "fail": 0},
          repr(report.get("summary")))

# ─────────────────────────────────────────────────────────────────────────
print("── (e) C4：同一 PATH（有 TeX、无 python3.14）source 失败 / exe 通过 ──")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    # soffice 也是 stub：libreoffice 可选，缺它 exe 侧会降到 1（见抬头「为什么…放 soffice」）
    bin_dir = make_bin(tmp, ("xelatex", "biber", "git", "soffice"))
    r_src = cli(["--json", "--profile", "source"], bin_dir)
    src = json.loads(r_src.stdout)
    bundle = by_id(src).get("engine_bundle") or {}
    check("(e) source + 无 python3.14 → engine_bundle=fail", bundle.get("status") == "fail",
          repr(bundle.get("detail", ""))[:100])
    check("(e) 且 detail 点名需要 Python 3.14", "3.14" in bundle.get("detail", ""))
    check("(e) 退出 2", r_src.returncode == 2, f"rc={r_src.returncode}")
    r_exe = cli(["--json", "--profile", "exe"], bin_dir)
    exe = json.loads(r_exe.stdout)
    check("(e) 同一条 PATH 下 --profile exe → engine_bundle=ok 且退出 0",
          by_id(exe)["engine_bundle"]["status"] == "ok" and r_exe.returncode == 0,
          f"rc={r_exe.returncode}")
    r_auto = cli(["--json"], bin_dir)
    check("(e) 不给 --profile（auto）跑在被测进程里 = source 判定",
          json.loads(r_auto.stdout)["profile"] == "source")

# ─────────────────────────────────────────────────────────────────────────
print("── (f) 单元级失败路径：真缺失 / 模板缺失 / 磁盘不足 / 输出不可写 ──")

_orig_soffice = E._soffice_probe
_orig_tpl = E._template_candidates
_orig_min = E.MIN_DISK_BYTES
try:
    E._soffice_probe = lambda: ("", "自动探测没找到 soffice")
    item = E.check_libreoffice()
    check("(f) soffice 真缺失（注入）→ optional/warn，hint 带安装命令",
          item["status"] == "warn" and item["level"] == "optional"
          and E.INSTALL["libreoffice"][PLAT] in item["hint"], item["hint"][:80])

    E._template_candidates = lambda: ["/nonexistent/tpl-a", "/nonexistent/tpl-b"]
    item = E.check_template_bundle()
    check("(f) 模板目录全丢 → required/fail，hint 指向「编辑笔记/模板文件」",
          item["status"] == "fail" and "编辑笔记/模板文件" in item["hint"])

    E.MIN_DISK_BYTES = 10 ** 30
    item = E.check_disk_space(str(ENGINE))
    check("(f) 可用空间低于阈值 → required/fail，hint 提到 2 GB",
          item["status"] == "fail" and "2 GB" in item["hint"], item["hint"])
finally:
    E._soffice_probe = _orig_soffice
    E._template_candidates = _orig_tpl
    E.MIN_DISK_BYTES = _orig_min

check("(f) 不给 --out-dir → output_writable 是 info（不参与结论）",
      E.check_output_writable("")["level"] == "info")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    afile = tmp / "not-a-dir"
    afile.write_text("x", encoding="utf-8")
    item = E.check_output_writable(str(afile))
    check("(f) --out-dir 是个文件 → required/fail（不是抛异常）",
          item["status"] == "fail", item["detail"][:80])

    if os.name != "nt" and os.geteuid() != 0:
        ro = tmp / "readonly"
        ro.mkdir()
        os.chmod(ro, 0o500)
        try:
            item = E.check_output_writable(str(ro))
            check("(f) 只读目录 → required/fail", item["status"] == "fail")
        finally:
            os.chmod(ro, 0o700)

# ─────────────────────────────────────────────────────────────────────────
print("── (g) --out-dir 只读：判 fail、报告写不进去也不能炸 ──")

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    bin_dir = make_bin(tmp, ("xelatex", "biber"))
    ro = tmp / "ro-out"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        r = cli(["--json", "--profile", "exe", "--out-dir", str(ro)], bin_dir)
        report = json.loads(r.stdout)
        check("(g) 只读 out-dir → output_writable=fail、退出 2",
              by_id(report)["output_writable"]["status"] == "fail"
              and r.returncode == 2, f"rc={r.returncode}")
        check("(g) 写不进报告只落一行 stderr，不影响 JSON", "报告没能写到" in r.stderr,
              r.stderr[:120])
        check("(g) 没有把半个文件留在只读目录里",
              not (ro / "envcheck.json").exists())
    finally:
        os.chmod(ro, 0o700)

# ─────────────────────────────────────────────────────────────────────────
print("── (h) dispatcher：ENTRIES 追加末尾、原十项顺序不变、真能跑通 ──")

check("(h) engine_main.ENTRIES 前 10 项与既有顺序逐项一致（C8）",
      _dispatcher.ENTRIES[:10] == (
          "convert.py", "checkbib.py", "mergepdf.py", "cleantemp.py", "compileall.py",
          "proofcheck.py", "infocheck.py", "toc.py", "publish.py", "bibdb.py"),
      repr(_dispatcher.ENTRIES))
check("(h) 第 11 项 envcheck.py、第 12 项 diagnose.py、第 13 项 dirpush.py（都在末尾追加）",
      _dispatcher.ENTRIES[10:] == ("envcheck.py", "diagnose.py", "dirpush.py"),
      repr(_dispatcher.ENTRIES))

with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    # 同上：soffice 必须在 PATH 里，否则可选项 warn 会把 rc=0 变成 rc=1
    bin_dir = make_bin(tmp, ("xelatex", "biber", "git", "soffice"))
    r = cli(["envcheck.py", "--json", "--profile", "exe"], bin_dir,
            script=ENGINE / "engine_main.py")
    ok = r.returncode == 0
    payload = {}
    try:
        payload = json.loads(r.stdout)
    except Exception:                                          # noqa: BLE001
        ok = False
    check("(h) `engine_main.py envcheck.py --json` 走 dispatcher 真跑通",
          ok and payload.get("schema") == "envcheck.v1",
          f"rc={r.returncode} {r.stdout[:120]!r} {r.stderr[-120:]!r}")

# ─────────────────────────────────────────────────────────────────────────
print("── (i) 文档对账：reference 页存在且逐项覆盖八个 id ──")

doc = ENGINE.parent / "docs" / "reference" / "environment-check.md"
check("(i) docs/reference/environment-check.md 存在", doc.is_file(), str(doc))
if doc.is_file():
    text = doc.read_text(encoding="utf-8")
    missing = [i for i in E.CHECK_IDS if i not in text]
    check("(i) reference 页逐项覆盖八个检查 id", not missing, repr(missing))
    check("(i) 页里写明三种退出码", all(s in text for s in ("退出码", "0", "1", "2")))

# ─────────────────────────────────────────────────────────────────────────
print("=" * 66)
print(f"{pass_n} PASS / {len(fails)} FAIL")
if fails:
    for name in fails:
        print(f"  - {name}")
    raise SystemExit(1)
