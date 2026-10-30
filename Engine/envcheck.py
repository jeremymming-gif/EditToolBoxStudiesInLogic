#!/usr/bin/env python3
"""首次运行环境自检（B-283）：八项检查 + 安装引导 + `--json`。

为什么在引擎里、不写进两个外壳：**判据只能有一份**。mac 的 SwiftUI 与 Windows 的
Tkinter 都要回答同一串问题（TeX 在不在？biber 在不在？LibreOffice 在不在？磁盘够不够？
模板随包了吗？），写两遍等于让两个平台的自检结果慢慢分叉。外壳只负责
「按当前形态拼参数 → 读回 JSON → 显示」，与 convert/proofcheck 等入口同一个契约。

三种退出码（`Engine/docs/INVARIANTS.md` 三.2 的口径：退出码 1 不是程序出错）：
  0 = 必需项全部就绪（可选项可以有缺失）
  1 = 必需项就绪，但**可选项**缺失（LibreOffice / git；功能受限，仍可用）
  2 = **必需项**缺失（TeX、biber、引擎运行时、磁盘、模板、输出目录）

形态（`--profile`）：
  auto（默认）= 冻结壳（`sys.frozen` 或可执行文件同目录有 SLCNEngine）判 exe，否则 source；
  exe    = 发布包形态：Python 运行时随包内置，**不要求**本机 PATH 里有 python3.14；
  source = 源码形态：要求 PATH（或 `SLCN_PYTHON`）上有 Python 3.14 + 引擎依赖可导入。

`--out-dir` 给了才检查「输出目录可写」，并把这份报告写成 `<out-dir>/envcheck.json`
（诊断包外壳拿它当素材，见 `docs/reference/environment-check.md`）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from slcn import platform as _plat  # noqa: E402

SCHEMA = "envcheck.v1"
MIN_DISK_BYTES = 2 * 1024 ** 3          # 2 GiB
MIN_PYTHON = (3, 14)

#: 检查项固定顺序（spec C1）：增删都要同步 `docs/reference/environment-check.md`
#: 与桥接测试里硬编码的清单。
CHECK_IDS = (
    "engine_bundle",
    "xelatex",
    "biber",
    "libreoffice",
    "git",
    "disk_space",
    "output_writable",
    "template_bundle",
)

#: 安装命令只用于**提示**：本工具绝不联网、绝不自动装东西（红线段）。
INSTALL = {
    "python": {
        "mac": "brew install python@3.14",
        "windows": "winget install --id Python.Python.3.14 -e",
    },
    "xelatex": {
        "mac": "brew install --cask mactex-no-gui（或按 https://tug.org/mactex/ 装 MacTeX）",
        "windows": "winget install --id MiKTeX.MiKTeX -e（或按 https://tug.org/texlive/windows.html 装 TeX Live）",
    },
    "biber": {
        "mac": "brew install biber",
        "windows": "随 MiKTeX / TeX Live 一起安装（winget install --id MiKTeX.MiKTeX -e）",
    },
    "libreoffice": {
        "mac": "brew install --cask libreoffice",
        "windows": "winget install --id TheDocumentFoundation.LibreOffice -e",
    },
    "git": {
        "mac": "xcode-select --install（或 brew install git）",
        "windows": "winget install --id Git.Git -e",
    },
}

_MARK = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def _platform_key() -> str:
    """安装命令按**当前平台**给（假 PATH 测试也走这一条判定）。"""
    return "windows" if os.name == "nt" else "mac"


def _install(key: str) -> dict[str, str]:
    return dict(INSTALL.get(key) or {"mac": "", "windows": ""})


def _missing_hint(title: str, install_key: str, impact: str) -> str:
    cmd = _install(install_key)[_platform_key()]
    tail = f"安装：{cmd}" if cmd else "安装方式见使用说明。"
    return f"{impact} {tail}"


# ── 小工具：跑一次 `--version` 并抽版本号（找不到/超时一律返回空串） ──────────

def _run_version(exe: str, args: tuple[str, ...] = ("--version",),
                 timeout: float = 20.0) -> str:
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _grep_version(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _tool_version(exe: str, pattern: str) -> str:
    return _grep_version(_run_version(exe), pattern)


# ── Python 解释器（仅源码形态需要） ────────────────────────────────────────

def _python_version(exe: str) -> str:
    text = _run_version(
        exe, ("-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"), timeout=15.0)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else ""


def _find_python_314() -> tuple[str, str]:
    """PATH（或 `SLCN_PYTHON`）上第一个 **≥ 3.14** 的解释器。

    故意**不**回落到 `sys.executable`：源码形态的自检要回答的是「编辑以后从
    终端 / 外壳启动时能不能找到 3.14」，而不是「现在这个进程是谁」。
    """
    cands: list[str] = []
    override = os.environ.get("SLCN_PYTHON", "").strip()
    if override:
        cands.append(override)
    for name in ("python3.14", "python3", "python"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    seen: set[str] = set()
    for exe in cands:
        if exe in seen:
            continue
        seen.add(exe)
        ver = _python_version(exe)
        try:
            major, minor = (int(x) for x in ver.split(".")[:2])
        except (ValueError, IndexError):
            continue
        if (major, minor) >= MIN_PYTHON:
            return exe, ver
    return "", ""


def _deps_ok() -> list[str]:
    """引擎依赖是否可导入；返回缺失的模块名（空 = 就位）。"""
    missing = []
    for name in ("lxml", "pypinyin", "pypdf"):
        try:
            __import__(name)
        except Exception:                                    # noqa: BLE001
            missing.append(name)
    return missing


# ── 形态判定 ──────────────────────────────────────────────────────────────

def detect_form(profile: str = "auto") -> str:
    """exe = 发布包形态；source = 源码形态。冻结壳与「同目录有 SLCNEngine」都算 exe。"""
    if profile in ("exe", "source"):
        return profile
    if getattr(sys, "frozen", False):
        return "exe"
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for name in ("SLCNEngine.exe", "SLCNEngine"):
        if os.path.isfile(os.path.join(exe_dir, name)):
            return "exe"
    return "source"


def _item(check_id: str, title: str, level: str, status: str, *,
          version: str = "", detail: str = "", hint: str = "",
          install: dict[str, str] | None = None) -> dict:
    return {
        "id": check_id,
        "title": title,
        "level": level,
        "status": status,
        "version": version,
        "detail": detail,
        "hint": hint,
        "install": install or {"mac": "", "windows": ""},
    }


# ── 八项检查 ──────────────────────────────────────────────────────────────

def check_engine_bundle(form: str) -> dict:
    title = "引擎运行时"
    engine_ver = _plat.engine_version()
    if form == "exe":
        return _item(
            "engine_bundle", title, "required", "ok", version=engine_ver,
            detail="exe 形态：随包内置 Python（引擎运行时已打包，无需在本机安装 Python）。",
            install={"mac": "", "windows": ""},
        )
    py, ver = _find_python_314()
    if not py:
        return _item(
            "engine_bundle", title, "required", "fail", version=engine_ver,
            detail=f"源码形态需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}，"
                   "但 PATH 上没有可用的 python3.14。",
            hint=_missing_hint(
                title, "python",
                f"这决定引擎能不能启动。安装后先用 python3.14 跑一次 `tools/gen_docs.py` 验证。"),
            install=_install("python"),
        )
    missing = _deps_ok()
    if missing:
        return _item(
            "engine_bundle", title, "required", "fail", version=engine_ver,
            detail=f"Python {ver}（{py}）在，但引擎依赖缺失：{', '.join(missing)}。",
            hint="在 Engine/ 里装依赖：python3.14 -m pip install -t vendor -r requirements.txt",
            install=_install("python"),
        )
    return _item(
        "engine_bundle", title, "required", "ok", version=engine_ver,
        detail=f"源码形态：Python {ver}（{py}），引擎依赖已就位。",
        install=_install("python"),
    )


def check_xelatex() -> dict:
    title = "XeLaTeX（xelatex）"
    exe = shutil.which("xelatex")
    if not exe:
        return _item(
            "xelatex", title, "required", "fail",
            detail="PATH 上找不到 xelatex。",
            hint=_missing_hint(title, "xelatex", "LaTeX 稿的初排稿只能靠它编译成 PDF。"),
            install=_install("xelatex"),
        )
    ver = _tool_version(exe, r"XeTeX\s+([\w.\-]+)")
    return _item("xelatex", title, "required", "ok", version=ver,
                 detail=f"已找到：{exe}", install=_install("xelatex"))


def check_biber() -> dict:
    title = "BibTeX 后端（biber）"
    exe = shutil.which("biber")
    if not exe:
        return _item(
            "biber", title, "required", "fail",
            detail="PATH 上找不到 biber。",
            hint=_missing_hint(title, "biber", "参考文献（biblatex）要靠它排序与回读。"),
            install=_install("biber"),
        )
    ver = _tool_version(exe, r"biber version:?\s*([\d.]+)")
    return _item("biber", title, "required", "ok", version=ver,
                 detail=f"已找到：{exe}", install=_install("biber"))


def _soffice_probe() -> tuple[str, str]:
    """返回 (路径, 说明)。说明非空 = 显式的 `SLCN_SOFFICE` 指向不存在的位置。

    为什么区分这一种：编辑显式设了路径却指错，是最常见的「明明装了却用不了」；
    它值得一句明确的警告，而不是悄悄回落到自动探测。
    """
    override = os.environ.get("SLCN_SOFFICE", "").strip()
    if override:
        if os.path.exists(override):
            return override, ""
        return "", f"环境变量 SLCN_SOFFICE 指向的路径不存在：{override}"
    path = _plat.find_soffice()
    return path, "" if path else "自动探测没找到 soffice"


def check_libreoffice() -> dict:
    title = "LibreOffice（soffice）"
    path, note = _soffice_probe()
    if not path:
        return _item(
            "libreoffice", title, "optional", "warn",
            detail=f"{note}。" if note else "未找到 soffice。",
            hint=_missing_hint(
                title, "libreoffice",
                "只影响含 OLE 对象 / MathType 公式的 Word 稿；纯 LaTeX 稿不受影响。"),
            install=_install("libreoffice"),
        )
    if note:
        return _item(
            "libreoffice", title, "optional", "warn", detail=f"{note}。",
            hint=_missing_hint(
                title, "libreoffice",
                "含 OLE 对象 / MathType 公式的 Word 稿会转换失败；清掉这个环境变量可回到自动探测。"),
            install=_install("libreoffice"),
        )
    ver = _tool_version(path, r"LibreOffice\s+([\d.]+)")
    return _item("libreoffice", title, "optional", "ok", version=ver,
                 detail=f"已找到：{path}", install=_install("libreoffice"))


def check_git() -> dict:
    title = "git（引用库同步用）"
    exe = shutil.which("git")
    if not exe:
        return _item(
            "git", title, "optional", "warn", detail="PATH 上找不到 git。",
            hint=_missing_hint(title, "git", "只影响笔记仓 / 引用库的版本与同步操作。"),
            install=_install("git"),
        )
    ver = _tool_version(exe, r"git version ([\d.]+)")
    return _item("git", title, "optional", "ok", version=ver,
                 detail=f"已找到：{exe}", install=_install("git"))


def check_disk_space(path: str) -> dict:
    title = "磁盘空间"
    try:
        free = shutil.disk_usage(path).free
    except OSError as e:
        return _item("disk_space", title, "required", "fail",
                     detail=f"读不到 {path} 的磁盘信息：{e}",
                     hint="确认这个路径还在、且有读权限。")
    gb = free / (1024 ** 3)
    if free < MIN_DISK_BYTES:
        return _item(
            "disk_space", title, "required", "fail",
            detail=f"可用 {gb:.1f} GB（阈值 2 GB）：{path}",
            hint="编译一期 PDF 需要至少 2 GB 的空余空间；清理磁盘后重跑自检。",
        )
    return _item("disk_space", title, "required", "ok", version=f"{gb:.1f} GB",
                 detail=f"可用 {gb:.1f} GB（阈值 2 GB）：{path}")


def check_output_writable(out_dir: str) -> dict:
    title = "输出目录可写"
    if not out_dir:
        return _item("output_writable", title, "info", "ok",
                     detail="未指定 --out-dir，跳过写入检查（不参与结论）。")
    try:
        os.makedirs(out_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".envcheck-", dir=out_dir)
        try:
            os.write(fd, b"ok")
        finally:
            os.close(fd)
            os.unlink(tmp)
    except OSError as e:
        return _item("output_writable", title, "required", "fail",
                     detail=f"写不进这个目录：{out_dir}（{e}）",
                     hint="换一个可写目录，或在系统设置里给这个目录写权限。")
    return _item("output_writable", title, "required", "ok",
                 detail=f"可写：{out_dir}")


def _template_candidates() -> list[str]:
    """模板目录候选：随包位置（`slcn.templates.TPL_DIR`）+ 两个历史/兼容位置。"""
    dirs: list[str] = []
    try:
        from slcn import templates as _tpl
        dirs.append(_tpl.TPL_DIR)
    except Exception:                                        # noqa: BLE001
        pass
    dirs.append(os.path.join(HERE, "slcn", "templates"))
    dirs.append(os.path.join(os.path.dirname(HERE), "templates"))
    return dirs


def check_template_bundle() -> dict:
    title = "排版模板"
    for d in _template_candidates():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        stys = [n for n in names if not n.startswith(".")]
        if stys:
            return _item("template_bundle", title, "required", "ok",
                         version=f"{len(stys)} 个文件",
                         detail=f"模板随包就位：{d}")
    return _item(
        "template_bundle", title, "required", "fail",
        detail="找不到排版模板目录（「编辑笔记/模板文件」）。",
        hint="发布包会自带模板；源码模式下确认仓库根有「编辑笔记/模板文件」后重跑。",
    )


# ── 汇总 / 渲染 ───────────────────────────────────────────────────────────

def _existing_ancestor(path: str) -> str:
    """`--out-dir` 还不存在时，磁盘检查要落在它最近的已存在祖先上（否则误判「读不到」）。"""
    cur = os.path.abspath(path)
    while cur and not os.path.isdir(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur or os.path.abspath(path)


def run_checks(form: str, out_dir: str = "") -> list[dict]:
    disk_base = _existing_ancestor(out_dir) if out_dir else os.path.expanduser("~")
    return [
        check_engine_bundle(form),
        check_xelatex(),
        check_biber(),
        check_libreoffice(),
        check_git(),
        check_disk_space(disk_base),
        check_output_writable(out_dir),
        check_template_bundle(),
    ]


def exit_code(checks: list[dict]) -> int:
    """0 = 必需项全 ok（warn 不影响）；2 = 有必需项非 ok；1 = 必需项 ok 但有可选项 warn。"""
    if any(c["level"] == "required" and c["status"] != "ok" for c in checks):
        return 2
    if any(c["status"] == "warn" for c in checks):
        return 1
    return 0


def build_report(form: str, out_dir: str = "") -> dict:
    checks = run_checks(form, out_dir)
    summary = {"ok": 0, "warn": 0, "fail": 0}
    for item in checks:
        summary[item["status"]] += 1
    return {
        "schema": SCHEMA,
        "profile": form,
        "platform": _plat.describe(),
        "checks": checks,
        "summary": summary,
    }


def render_text(report: dict) -> str:
    form = "发布包（exe）" if report["profile"] == "exe" else "源码（source）"
    lines = [
        f"环境自检（{report['schema']}）｜形态：{form}｜平台：{report['platform']}",
        "",
    ]
    for c in report["checks"]:
        ver = f"  {c['version']}" if c.get("version") else ""
        level = {"required": "", "optional": "（可选）", "info": "（信息）"}[c["level"]]
        lines.append(f"  {_MARK[c['status']]} {c['title']}{level}{ver}")
        if c.get("detail"):
            lines.append(f"      {c['detail']}")
        if c["status"] != "ok" and c.get("hint"):
            lines.append(f"      → {c['hint']}")
    s = report["summary"]
    lines += ["", f"小计：ok {s['ok']} · warn {s['warn']} · fail {s['fail']}"]
    code = exit_code(report["checks"])
    if code == 0:
        tail = f"；可选项缺 {s['warn']} 项，不影响主流程" if s["warn"] else ""
        lines.append(f"结论：✅ 必需项全部就绪{tail}")
    elif code == 1:
        lines.append("结论：⚠️ 必需项已就绪；可选项有缺失，相关功能受限（见上面的提示）")
    else:
        fails = [c["title"] for c in report["checks"]
                 if c["level"] == "required" and c["status"] != "ok"]
        lines.append(f"结论：❌ 有 {len(fails)} 项必需项缺失：{'、'.join(fails)}")
        lines.append("      按上面的安装提示处理，装好后重跑一次自检。")
    return "\n".join(lines)


def write_json(report: dict, out_dir: str) -> str:
    path = os.path.join(out_dir, "envcheck.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="envcheck.py",
        description="首次运行环境自检：TeX / biber / LibreOffice / git / 磁盘 / 输出目录 / 模板",
    )
    ap.add_argument("--json", action="store_true", help="只输出 JSON（供外壳读）")
    ap.add_argument("--profile", choices=("auto", "exe", "source"), default="auto",
                    help="引擎形态：auto（默认）/ exe（发布包）/ source（源码）")
    ap.add_argument("--out-dir", default="",
                    help="顺带检查这个目录可写，并把报告写成 <out-dir>/envcheck.json")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    form = detect_form(args.profile)
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir)) if args.out_dir else ""
    report = build_report(form, out_dir)

    written = ""
    if out_dir:
        try:
            written = write_json(report, out_dir)
        except OSError as e:
            # 写不进报告不算新的失败：output_writable 这一项已经把它讲清楚了。
            print(f"（报告没能写到 {out_dir}：{e}）", file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
        if written:
            print(f"\n报告已写入：{written}")
    return exit_code(report["checks"])


if __name__ == "__main__":
    sys.exit(main())
