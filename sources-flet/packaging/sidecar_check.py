"""P4/B113 打包自检：engine_bridge 在「sidecar 随包」布局下能否定位引擎（不进 GUI）。

用法：
    python sources-flet/packaging/sidecar_check.py <SLCNEngine 路径或目录> [--run-help]

路径两种形态都收（B113 起 sidecar 目录化）：
    · PyInstaller --onefile → 单个可执行文件 `SLCNEngine`；
    · PyInstaller --onedir  → 目录 `SLCNEngine/`（内含 `SLCNEngine[.exe]` 与 `_internal/`）；
      传目录时先解析出内部可执行文件，再做全部检查。

检查项：
  1. sidecar 文件存在且可执行；
  2. 设 P2_ENGINE_EXE 后 base_cmd('convert.py') == [sidecar, 'convert.py']；
  3. engine_status()：mode=frozen、ok=True、frozen_exe=sidecar、version≠未知；
  4. --run-help：真实执行 `SLCNEngine convert.py --help`，rc==0（证明包内入口可用）；
  5. binary：**不经 engine_bridge、不依赖任何 override env**，直接执行
     `<sidecar> --version`，记录 {exists, executable_bit, version_rc, version_tail}。

证据口径（P09 PRE-013）：G8/ACCEPT 的包内身份证据 = 包内 sidecar `--version` 输出 +
包内文件 SHA256 + manifest；`checks.status` 只是结构检查（会把 P2_ENGINE_EXE 设成
包内 sidecar 来模拟打包布局），其 `source` 字段标注了来源，不能单独当「包内二进制」证据。

退出码：0 通过 / 1 失败 / 2 用法错误。stdout 输出 JSON（便于 CI 与证据落盘）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _utf8_stdio() -> None:
    """B-246c P4 fix：Windows CI（cp1252 控制台）打印中文 JSON 会 UnicodeEncodeError。

    证据根/本机是 cp936 能过，GitHub Windows runner 默认 ANSI=1252 必炸；
    统一把 stdout/stderr 指到 UTF-8（不可用时退回 replace，保证自检结论可见）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def resolve_sidecar(path: Path) -> Path:
    """把「文件或目录」解析成真正可执行的 sidecar（B113 目录化）。

    --onedir 的产物根目录里是同名可执行：macOS/Linux 为 `SLCNEngine`，
    Windows 为 `SLCNEngine.exe`，旁边另有 `_internal/`。
    """
    path = Path(path)
    if path.is_dir():
        for name in ("SLCNEngine", "SLCNEngine.exe"):
            inner = path / name
            if inner.is_file():
                return inner.resolve()
    return path.resolve()


def run_binary_version(exe: Path) -> dict:
    """直接执行 `<sidecar> --version`：包内二进制的直接证据（不依赖 env/源码树）。"""
    info = {
        "exists": exe.is_file(),
        "executable_bit": os.access(exe, os.X_OK),
        "version_rc": None,
        "version_tail": "",
        "error": "",
    }
    try:
        proc = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        info["version_rc"] = proc.returncode
        info["version_tail"] = out.splitlines()[-1] if out else ""
    except (OSError, subprocess.SubprocessError) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def main() -> int:
    _utf8_stdio()
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    run_help = "--run-help" in sys.argv[1:]
    if not argv:
        print("usage: sidecar_check.py <path-to-SLCNEngine|onedir-dir> [--run-help]")
        return 2

    raw = Path(argv[0]).resolve()
    sidecar = resolve_sidecar(raw)
    app_dir = Path(__file__).resolve().parents[1] / "app"
    sys.path.insert(0, str(app_dir))

    result: dict = {
        "input": str(raw),
        "sidecar": str(sidecar),
        "onedir": sidecar != raw,
        "exists": sidecar.is_file(),
        "run_help": bool(run_help),
        "checks": {},
        # 显式记录本检查器为了「模拟打包布局」而设置的 env；status 结果因此只算结构检查。
        "env_overrides_used": ["P2_ENGINE_EXE"],
        "ok": False,
    }

    if not sidecar.is_file():
        result["error"] = "sidecar 不存在（传目录时，目录里也没有 SLCNEngine/SLCNEngine.exe）"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    # 包内二进制身份的直接证据：真实执行包内 sidecar 的 --version（先于任何 env 模拟）。
    result["checks"]["binary"] = run_binary_version(sidecar)

    # 模拟打包布局：强制走同目录 sidecar（清掉别的覆盖变量）
    os.environ["P2_ENGINE_EXE"] = str(sidecar)
    os.environ.pop("SLCN_ENGINE_EXE", None)
    os.environ.pop("P2_ENGINE", None)
    os.environ.pop("SLCN_ENGINE", None)

    from services import engine_bridge as EB  # noqa: E402

    cmd = EB.base_cmd("convert.py") or []
    status = EB.engine_status()
    checks = result["checks"]
    checks["base_cmd"] = {"argv": cmd, "ok": len(cmd) == 2 and cmd[1:] == ["convert.py"] and Path(cmd[0]) == sidecar}
    checks["status"] = {
        # PRE-013：status 走 engine_bridge（本检查器把 P2_ENGINE_EXE 设成包内 sidecar
        # 来模拟打包布局），只能当结构检查；包内身份以 checks.binary + SHA + manifest 为准。
        "source": "engine_bridge(env-simulated: P2_ENGINE_EXE set by checker)",
        "mode": status.mode,
        "ok": status.ok,
        "frozen_exe": status.frozen_exe,
        "version": status.version,
        "executable": status.executable,
        "problems": status.problems,
        "describe": status.describe(),
    }

    if run_help and cmd:
        try:
            proc = subprocess.run(
                cmd + ["--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            checks["run_help"] = {
                "rc": proc.returncode,
                "ok": proc.returncode == 0,
                "tail": out.strip().splitlines()[-1] if out.strip() else "",
            }
        except (OSError, subprocess.SubprocessError) as exc:
            checks["run_help"] = {"rc": None, "ok": False, "error": f"{exc}"}

    result["ok"] = bool(
        checks["base_cmd"]["ok"]
        and checks["status"]["ok"]
        and checks["status"]["mode"] == "frozen"
        and Path(checks["status"]["frozen_exe"] or "").resolve() == sidecar
        and checks["status"]["version"] not in ("", "未知")
        and checks["binary"]["version_rc"] == 0
        and checks["binary"]["executable_bit"]
        and (not run_help or checks.get("run_help", {}).get("ok"))
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
