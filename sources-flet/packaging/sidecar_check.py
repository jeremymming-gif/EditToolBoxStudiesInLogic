"""P4 打包自检：engine_bridge 在「sidecar 同目录」布局下能否定位引擎（不进 GUI）。

用法：
    python sources-flet/packaging/sidecar_check.py <SLCNEngine 路径> [--run-help]

检查项：
  1. sidecar 文件存在且可执行；
  2. 设 P2_ENGINE_EXE 后 base_cmd('convert.py') == [sidecar, 'convert.py']；
  3. engine_status()：mode=frozen、ok=True、frozen_exe=sidecar、version≠未知；
  4. --run-help：真实执行 `SLCNEngine convert.py --help`，rc==0（证明包内入口可用）。

退出码：0 通过 / 1 失败 / 2 用法错误。stdout 输出 JSON（便于 CI 与证据落盘）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    run_help = "--run-help" in sys.argv[1:]
    if not argv:
        print("usage: sidecar_check.py <path-to-SLCNEngine> [--run-help]")
        return 2

    sidecar = Path(argv[0]).resolve()
    app_dir = Path(__file__).resolve().parents[1] / "app"
    sys.path.insert(0, str(app_dir))

    result: dict = {
        "sidecar": str(sidecar),
        "exists": sidecar.exists(),
        "run_help": bool(run_help),
        "checks": {},
        "ok": False,
    }

    if not sidecar.exists():
        result["error"] = "sidecar 不存在"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

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
        "mode": status.mode,
        "ok": status.ok,
        "frozen_exe": status.frozen_exe,
        "version": status.version,
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
        and (not run_help or checks.get("run_help", {}).get("ok"))
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
