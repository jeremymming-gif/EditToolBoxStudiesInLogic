"""Windows 外壳与引擎之间的**唯一**桥梁（不含任何界面代码）。

为什么单独一个模块、而且**不许 import tkinter**：
  1. 逻辑要能**离开窗口测试** —— `tests/test_bridge.py` 在 mac/CI 上跑得动，
     不必有显示器、不必真开窗口；
  2. 界面只负责「取值 → 拼参数 → 显示日志」，这条纪律与 mac 外壳一致
     （见 `Engine/docs/ENGINE_API.md`「外壳的义务」）。

**引擎怎么找**（与 mac 外壳同样的思路，但 Windows 上没有 Xcode/Homebrew 那套路径）：
  `slcn_engine()` 依次尝试
    ① 环境变量 `SLCN_ENGINE`（指到 `Engine/` 目录）
    ② **打包形态**：本 exe 同级的 `Engine/`（PyInstaller 会把它放进 `_MEIPASS` 或旁边）
    ③ 开发形态：本文件上一级的 `../Engine/`（仓库里 `sources-win/` 与 `Engine/` 并列）
  `slcn_python()` 依次尝试
    ① 环境变量 `SLCN_PYTHON`
    ② 已经装了 Python 的开发机：`python` / `python3` / Windows 的 `py` 启动器
    ③ **冻结引擎**：`SLCNEngine.exe`（把八个 CLI 冻成一个 exe，见 `Engine/engine_main.py`；
       本文件的 `ENTRIES` 必须与它的 `ENTRIES` 逐项一致 —— 由 tests/test_bridge.py 守着）
  —— 顺序上「先找真 Python」是因为开发机上改完代码就能验证；发布包走 ③，用户机器上不需要装 Python。
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field

#: CLI 入口（与 `Engine/docs/ENGINE_API.md` 一致）
ENTRIES = ("convert.py", "checkbib.py", "mergepdf.py",
           "cleantemp.py", "compileall.py", "proofcheck.py",
           "infocheck.py", "toc.py")


# ── 定位引擎与解释器 ─────────────────────────────────────────────────────

def _shell_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def slcn_engine() -> str | None:
    """找到 `Engine/` 目录（内含 `convert.py`），找不到返回 None。"""
    cands: list[str] = []
    env = os.environ.get("SLCN_ENGINE", "").strip()
    if env:
        cands.append(env)
    if getattr(sys, "frozen", False):                      # PyInstaller 打包后的形态
        cands.append(os.path.join(os.path.dirname(sys.executable), "Engine"))
        cands.append(os.path.join(getattr(sys, "_MEIPASS", ""), "Engine"))
    cands.append(os.path.join(os.path.dirname(_shell_dir()), "Engine"))   # 开发形态
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "convert.py")):
            return os.path.abspath(c)
    return None


def slcn_python() -> str | None:
    """找到能跑引擎的 Python 解释器；找不到返回 None（此时应改用冻结引擎 exe）。"""
    env = os.environ.get("SLCN_PYTHON", "").strip()
    if env and os.path.exists(env):
        return env
    if not getattr(sys, "frozen", False):
        return sys.executable                               # 外壳自己就是 Python 跑的
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return None


def frozen_engine_exe() -> str | None:
    """冻结引擎 exe（`SLCNEngine.exe`）：把八个 CLI 打成一个可执行文件，用户不必装 Python。"""
    env = os.environ.get("SLCN_ENGINE_EXE", "").strip()
    if env and os.path.exists(env):
        return env
    names = ("SLCNEngine.exe", "SLCNEngine")
    for base in (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _shell_dir(),
                 _shell_dir(),
                 slcn_engine() or ""):
        for n in names:
            p = os.path.join(base, n) if base else ""
            if p and os.path.exists(p):
                return os.path.abspath(p)
    return None


# ── 拼命令行（纯函数，测试直接打这些）──────────────────────────────────

@dataclass
class EngineCall:
    """一次引擎调用的全部信息（**密钥不在这里**，见 `env_for_run`）。"""
    argv: list[str]
    entry: str
    label: str

    @property
    def display(self) -> str:
        return " ".join(self.argv)


def _base_cmd(entry: str) -> list[str] | None:
    exe = frozen_engine_exe()
    engine = slcn_engine()
    if exe:
        # 冻结形态：`SLCNEngine.exe convert.py --source …`（见 Engine/engine_main.py）
        return [exe, entry]
    if not engine:
        return None
    py = slcn_python()
    if not py:
        return None
    return [py, os.path.join(engine, entry)]


def convert_call(*, source: str, out: str, compile_pdf: bool = False,
                 clean: bool = False, verify_refs: bool = False,
                 source_pdf: bool = False, bib: str = "", metadata: str = "",
                 author: str = "", name: str = "",
                 vol: str = "", issue: str = "", year: str = "", pages: str = "",
                 received: str = "", editor: str = "", grants: str = "",
                 lang: str = "", extra: str = "") -> EngineCall:
    """「初排转换」：`convert.py --source … --out …`（参数与 mac 界面一致）。

    ⚠️ `author` / `name` 不是可选项而是**某一类稿件的活路**：正文里读不出第一作者姓名时
    引擎会退出 2 并要求「请显式指定 --author "陈波" / --name Bo_Chen」——
    界面若没有这两个输入框，这类稿（本刊真实的 LaTeX 投稿里就有）**整页做不了**
    （2026-10-17 审计实测：`2026年第5期(90)/03 Liang_Zhang/…/main.tex` 等 2 篇 rc=2）。
    出版信息（vol/issue/…）缺了不报错，但 PDF 页眉会是「xxxx 年第 x 期，1–10」占位。
    """
    cmd = _base_cmd("convert.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--source", source, "--out", out]
    for flag, on in (("--compile", compile_pdf), ("--clean", clean),
                     ("--verify-refs", verify_refs), ("--source-pdf", source_pdf)):
        if on:
            cmd.append(flag)
    if bib:
        cmd += ["--bib", bib]
    if metadata:
        cmd += ["--metadata", metadata]
    for flag, val in (("--author", author), ("--name", name),
                      ("--vol", vol), ("--issue", issue), ("--year", year),
                      ("--pages", pages), ("--received", received),
                      ("--editor", editor), ("--grants", grants), ("--lang", lang)):
        if val:
            cmd += [flag, val]
    if extra.strip():
        cmd += extra.split()
    return EngineCall(cmd, "convert.py", "初排转换")


def checkbib_call(*, returned: str, original: str = "", tex: str = "",
                  merge_into: str = "", out: str = "") -> EngineCall:
    """「文献回读校验」：`checkbib.py --returned … [--merge-into …]`。"""
    cmd = _base_cmd("checkbib.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--returned", returned]
    if original:
        cmd += ["--original", original]
    if tex:
        cmd += ["--tex", tex]
    if merge_into:
        cmd += ["--merge-into", merge_into]
    if out:
        cmd += ["--out", out]
    return EngineCall(cmd, "checkbib.py", "文献回读校验")


def merge_call(*, folder: str, out: str = "", nest_outlines: bool = False,
               editors_page: bool = False, back_cover: bool = False) -> EngineCall:
    """「合并文件」：`mergepdf.py --folder …`（把原 Windows 版的合并规则换成引擎那份）。"""
    cmd = _base_cmd("mergepdf.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if out:
        cmd += ["--out", out]
    for flag, on in (("--nest-outlines", nest_outlines), ("--editors-page", editors_page),
                     ("--back-cover", back_cover)):
        if on:
            cmd.append(flag)
    return EngineCall(cmd, "mergepdf.py", "合并文件")


def cleantemp_call(*, folder: str, dry_run: bool = False) -> EngineCall:
    """「删除临时文件」：`cleantemp.py --folder …`（不删 .tex/.pdf/.log）。"""
    cmd = _base_cmd("cleantemp.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "cleantemp.py", "删除临时文件")


def compileall_call(*, folder: str, only: str = "", dry_run: bool = False) -> EngineCall:
    """「编译全部」：`compileall.py --folder …`（xelatex → biber → xelatex ×2）。"""
    cmd = _base_cmd("compileall.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if only.strip():
        cmd += ["--only", only.strip()]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "compileall.py", "编译全部")


def proofcheck_call(*, tex: str, fix: bool = False, lang: str = "") -> EngineCall:
    """「一校核查」：`proofcheck.py --tex …`（按《排校手册》《流程化经验书》逐条核查）。"""
    cmd = _base_cmd("proofcheck.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--tex", tex]
    if lang:
        cmd += ["--lang", lang]
    if fix:
        cmd.append("--fix")
    return EngineCall(cmd, "proofcheck.py", "一校核查")


def infocheck_call(*, folder: str, preview: bool = False) -> EngineCall:
    """「检查tex信息」：`infocheck.py --folder …`。

    `preview=True` 时加 `--preview`：引擎只把检查结果 JSON 打到 stdout、**不落文件**，
    界面用它填表格（口径与真跑一遍完全一致，不会出现"表里 10 篇、报告里 9 篇"）。
    """
    cmd = _base_cmd("infocheck.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if preview:
        cmd.append("--preview")
    return EngineCall(cmd, "infocheck.py", "检查tex信息（预览）" if preview else "检查tex信息")


def toc_call(*, report: str, template: str = "") -> EngineCall:
    """「生成目录」：`toc.py --report …`（本期设置由界面先写进 `目录设置.json`）。"""
    cmd = _base_cmd("toc.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--report", report]
    if template.strip():
        cmd += ["--template", template.strip()]
    return EngineCall(cmd, "toc.py", "生成目录")


def toc_preview_call(*, report: str) -> EngineCall:
    """目录预览：`toc.py --report … --preview`（篇目清单 + 模板来源，JSON，不落文件）。"""
    cmd = _base_cmd("toc.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--report", report, "--preview"]
    return EngineCall(cmd, "toc.py", "目录预览")


# ── 环境变量与执行 ───────────────────────────────────────────────────────

def env_for_run(api: dict | None = None, engine: str | None = None) -> dict:
    """子进程的环境：UTF-8、无缓冲、vendor 依赖，以及**只走环境变量的密钥**。

    ⚠️ 密钥**绝不进 argv**：命令行参数在任务管理器 / `wmic process` 里谁都能看见。
    """
    env = dict(os.environ)
    engine = engine or slcn_engine()
    if engine:
        vendor = os.path.join(engine, "vendor")
        env["PYTHONPATH"] = vendor + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"          # 否则日志按块缓冲，界面看着像卡死
    env["PYTHONIOENCODING"] = "utf-8"      # Windows 控制台默认 cp936，中文会乱码/报错
    api = api or {}
    for key, var in (("base", "SLCN_LLM_BASE"), ("key", "SLCN_LLM_KEY"),
                     ("model", "SLCN_LLM_MODEL"), ("protocol", "SLCN_LLM_PROTOCOL"),
                     ("searchTool", "SLCN_LLM_SEARCH_TOOL"),
                     ("responsesPath", "SLCN_LLM_RESPONSES_PATH")):
        val = str(api.get(key, "") or "").strip()
        if val:
            env[var] = val
    if api.get("thinking"):
        env["SLCN_LLM_THINKING"] = str(api["thinking"])
    if api.get("maxTokens"):
        env["SLCN_LLM_MAX_TOKENS"] = str(api["maxTokens"])
    if api.get("webSearch"):
        env["SLCN_LLM_WEB_SEARCH"] = "1"
    return env


def kill_tree(proc: subprocess.Popen) -> None:
    """停任务：杀**整棵**进程树，不只是直接子进程。

    为什么非这样不可（2026-10-17 审计实测）：直接子进程在发布形态里只是 PyInstaller
    的 bootloader，真正的引擎与它起的 `xelatex` 都是**孙进程** —— 只 `terminate()`
    会出现「界面已经显示不忙、TeX 还在写产物目录」，编辑再点一次运行就有两组 TeX
    同写一个目录。POSIX 上实测：按下停止后 xelatex 又跑了 ~12 秒并写出了 PDF。

    Windows 用 `taskkill /T /F`（连子孙一起），POSIX 用 `killpg`（`run_call` 里
    已 `start_new_session=True`）；两条路都失败才退回 `terminate()`。
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.terminate()
    except OSError:
        pass


def run_call(call: EngineCall, *, on_line=None, cwd: str | None = None,
             api: dict | None = None, on_proc=None) -> int:
    """跑一次引擎调用，逐行回调日志；返回退出码（**只看 0 / 非 0**）。

    `stderr` 合并进 `stdout`：引擎的致命错误走 stderr，编辑在同一个日志框里才看得见。
    `on_proc(proc)` 在进程刚起来时回调 —— 界面拿它做「停止」按钮（`proc.terminate()`）。
    """
    env = env_for_run(api)
    proc = subprocess.Popen(
        call.argv, cwd=cwd or None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),   # Windows：别弹黑框
        # POSIX：自成进程组，停止时能整组杀（见 kill_tree）
        start_new_session=(os.name != "nt"),
    )
    if on_proc:
        on_proc(proc)
    assert proc.stdout is not None
    for line in proc.stdout:                       # 逐行读 = 实时进度（第 82 条）
        if on_line:
            on_line(line.rstrip("\n"))
    proc.wait()
    return proc.returncode


#: 退出码 1 在引擎里**常常是"有结果"而不是失败**（`Engine/docs/INVARIANTS.md` 三.2）：
#: checkbib 并回 = 已并入 ≥1 条、compileall = 有失败、proofcheck = 有命中、toc = 只生成一份。
#: 外壳**不许**把它显示成错误 —— 这条口径与 mac 各页逐字对齐（`CheckBibFeature.swift` 等）。
_RESULT_1 = {
    "checkbib.py": "❗ 体检发现问题 / 已并入，请按报告逐条处理（不是程序出错）",
    "proofcheck.py": "❗ 有命中项，请按报告逐条处理（不是程序出错）",
    "compileall.py": "⚠ 有文章编译失败（每篇的错误摘要见上面的日志）",
    "toc.py": "⚠ 只生成了 1 份目录（另一份见上面的日志）",
}
_RESULT_1_DEFAULT = "❗ 完成，但有需要处理的结果（退出码 1，见上面的日志）"


#: 契约行里可能「像路径起点」的位置：Unix 的 `/`，或 Windows 的 `C:\\` / `C:/`。
#: ⚠️ **不能靠"路径字符集"去截断**：本项目的期目录名里就带半角括号
#: （`2026年第5期(90)`），字符集一排除括号就把路径截断了（第一版就这么错的）。
_PATH_START = re.compile(r"(?:[A-Za-z]:[\\/]|/)")


def artifact_path(last_line: str) -> str:
    """从引擎的**契约行**（stdout 最后一行）里抠出产物路径；抠不到返回空串。

    为什么需要它：编辑最常问的两句是「跑完了吗」「产物在哪」。契约行（`ENGINE_API.md` 五）
    本来就把路径写在里面了，界面把它做成**可点开 / 可复制**的，比让人翻日志强得多。

    抠法：对每个「路径起点」取**磁盘上真的存在的最长前缀** —— 这样路径里的括号、
    尾巴上的 `（共 176 页）` / `）` / `。` 都不会误伤，也不需要给路径定字符集。
    抠不到（dry-run、路径已被移走）就返回空串，界面只显示状态、不给按钮 —— **不猜**。
    """
    line = last_line or ""
    best = ""
    for m in _PATH_START.finditer(line):
        rest = line[m.start():]
        for end in range(len(rest), 0, -1):
            cand = rest[:end].rstrip("。.…")
            if os.path.exists(cand):
                if len(cand) > len(best):
                    best = cand
                break
    return best


def result_text(entry: str, code: int) -> str:
    """把退出码翻成给编辑看的一句话（H3：1 ≠ 失败）。"""
    if code == 0:
        return "✅ 完成"
    if code == 1:
        return _RESULT_1.get(entry, _RESULT_1_DEFAULT)
    if code < 0:
        return f"❌ 启动失败或被中止（退出码 {code}）"
    return f"❌ 失败（退出码 {code}）"


@dataclass
class EngineStatus:
    """给「设置」页显示的引擎状态。"""
    engine_dir: str | None = None
    python: str | None = None
    frozen_exe: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        if self.engine_dir is None and self.frozen_exe is None:
            return "❌ 没找到引擎"
        # ⚠️ 冻结形态下 `engine_dir` **本来就是 None**（发布包不带 Engine/ 源码），
        #    从前照样拼出「引擎目录 None」—— 而那正是发布版设置页的默认画面
        #    （2026-10-17 审计实测；README 的发布前检查还要求它显示「运行方式：冻结引擎 …」）。
        if self.frozen_exe and self.engine_dir is None:
            return f"✅ 运行方式：冻结引擎 {self.frozen_exe}"
        if self.frozen_exe:
            return f"✅ 引擎目录 {self.engine_dir}；运行方式：冻结引擎 {self.frozen_exe}"
        return f"✅ 引擎目录 {self.engine_dir}；运行方式：Python {self.python}"


def status() -> EngineStatus:
    st = EngineStatus(engine_dir=slcn_engine(), python=slcn_python(),
                      frozen_exe=frozen_engine_exe())
    if st.engine_dir is None and st.frozen_exe is None:
        st.problems.append("找不到 Engine 目录（可用环境变量 SLCN_ENGINE 指定）")
    if st.frozen_exe is None and st.python is None:
        st.problems.append("既没有冻结引擎 exe，也没有可用的 Python 解释器")
    return st
