"""gwcore.py —— gui_walkthrough 的核心：常量 / 错误类型 / 纯函数 / evidence pack / fixture 计划。

拆模块理由：入口 `gui_walkthrough.py` + 本文件 + `gwdriver.py`（命令实现）+ `gwselftest.py`（自测），
每个文件都在本仓「单文件 ≤1200 行」软上限内；纯函数集中在这里，`--self-test` 才能 hermetic 地测。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

TOOL_VERSION = "1.0.0"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HELPER_SOURCE = HERE / "axhelper.swift"
HELPER_CACHE_DIR = Path(tempfile.gettempdir()) / "gui-walkthrough-helper"
DEFAULT_APP = REPO_ROOT / "dist" / "编辑部工具箱.app"
DEFAULT_DOMAIN = "cn.edu.sysu.logic.toolbox"
DEFAULT_KEY = "notesRepoPath"
DEFAULT_NOTES_REPO = REPO_ROOT.parent / "EditToolBox-notes"
DEFAULT_EVIDENCE_DIR = Path(tempfile.gettempdir()) / "gui-walkthrough-evidence"

STATE_NAME = "state.json"
VERDICT_TXT = "VERDICT-SUMMARY.txt"
VERDICT_JSON = "VERDICT-SUMMARY.json"
ARCHIVE_PREFIX = "archive-"
SLUG_MAX = 40
AX_MAX_DEPTH = 30
DEFAULT_TIMEOUT = 5.0

EXIT_OK, EXIT_ASSERT, EXIT_USAGE, EXIT_NO_PERM, EXIT_ENV = 0, 1, 2, 3, 4

ALL_COMMANDS = ("select", "click", "field", "scroll", "shot", "assert-text",
                "assert-alert", "dump", "fixture")


# ---------------------------------------------------------------------------
# 错误类型（退出码语义单点）
# ---------------------------------------------------------------------------

class ToolError(Exception):
    """带退出码的失败；status 决定 evidence 里的 PASS/FAIL/SKIP。"""

    status = "FAIL"

    def __init__(self, message: str, code: int = EXIT_ENV, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


class AssertError(ToolError):
    status = "FAIL"

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message, EXIT_ASSERT, data)


class UsageError(ToolError):
    status = "FAIL"

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message, EXIT_USAGE, data)


class PermissionProblem(ToolError):
    status = "FAIL"

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message, EXIT_NO_PERM, data)


class EnvProblem(ToolError):
    status = "FAIL"

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message, EXIT_ENV, data)


# ---------------------------------------------------------------------------
# 纯函数：权限提示 / 命名 / 定位参数 / 解析
# ---------------------------------------------------------------------------

def permission_hint(process: str) -> str:
    """缺辅助功能权限时的可行动提示（纯函数，可自测）。"""
    return (
        "缺少「辅助功能（Accessibility）」权限：AX 查询/点击/取值都会失败。\n"
        f"  → 系统设置 → 隐私与安全性 → 辅助功能 → 把「{process}」加入并勾选（改完需重开该终端/会话）。\n"
        f"  → 实际调用进程：{process}（AX 请求来自它；本工具的 axhelper 与 osascript 都受它管辖）\n"
        f"  → 排查用：tccutil reset Accessibility {process}（重置后系统会重新弹授权窗）\n"
        "  → 权限未授予时本工具**不会**假报 PASS：退出码 3。"
    )


def slugify(text: str, fallback: str = "step", max_len: int = SLUG_MAX) -> str:
    """证据文件名 slug：保留 ASCII 字母数字 + CJK，其余折叠成 '-'（确定性、可读、无路径分隔符）。"""
    raw = (text or "").strip()
    if not raw:
        return fallback
    out = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", raw).strip("-").lower()
    out = re.sub(r"-{2,}", "-", out)
    if not out:
        return fallback
    return out[:max_len].strip("-") or fallback


def evidence_name(seq: int, cmd: str, slug: str, ext: str) -> str:
    """固定命名：`NN-<cmd>-<slug>.<ext>`（NN 两位零填充；≥100 自然变三位）。"""
    return f"{seq:02d}-{cmd}-{slug}.{ext}"


def locator_argv(loc: dict) -> list[str]:
    """把定位描述转成 axhelper 参数（纯函数：自测直接断言 argv）。"""
    if loc.get("path"):
        return ["--path", str(loc["path"])]
    argv: list[str] = []
    for flag, key in (("--role", "role"), ("--name", "name"), ("--title", "title"),
                      ("--desc", "desc"), ("--value", "value"), ("--help-text", "help_text")):
        if loc.get(key):
            argv += [flag, str(loc[key])]
    if loc.get("within"):
        argv += ["--within", str(loc["within"])]
    if loc.get("nth") is not None:
        argv += ["--nth", str(int(loc["nth"]))]
    if loc.get("exact"):
        argv += ["--exact"]
    if loc.get("subtree"):
        argv += ["--subtree"]
    if loc.get("max_depth"):
        argv += ["--max-depth", str(int(loc["max_depth"]))]
    return argv


def validate_locator(loc: dict) -> None:
    """定位参数自相矛盾/空定位 → UsageError（绝不「猜一个元素」）。"""
    if loc.get("path"):
        if loc.get("role") or loc.get("name") or loc.get("title"):
            raise UsageError("--path 与 --role/--name/--title 不能同时给（路径定位和名称定位二选一）")
        if not re.fullmatch(r"(-|\d+(\.\d+)*)", str(loc["path"])):
            raise UsageError(f"--path 只接受数字路径（如 0.6.3）或 '-'：{loc['path']!r}")
        return
    if not (loc.get("role") or loc.get("name") or loc.get("title") or loc.get("desc")
            or loc.get("value") or loc.get("help_text")):
        raise UsageError("缺少定位条件：给 --path，或 --role/--name（名称命中唯一才动作）")
    if loc.get("nth") is not None and int(loc["nth"]) < 0:
        raise UsageError("--nth 必须 ≥ 0")


# 屏幕休眠 / 会话锁定时，部分 App 的 AX 树会退化成「AXApplication 自身无限嵌套」，
# 表现为窗口 0 个、文本只剩 App 名。B-191 实测（Finder，01:10）复现；`caffeinate -u -t 2` 唤醒后恢复。
DEGENERATE_AX_HINT = (
    "AX 树退化成 AXApplication 自身嵌套（窗口 0 个/文本只剩 App 名）——"
    "通常是屏幕休眠或会话锁定；先 `caffeinate -u -t 2` 唤醒屏幕再跑，必要时把 App 窗口切到前台"
)


def ax_tree_degenerate(roles: list[str]) -> bool:
    """true = 树里 ≥4 个元素且**全是** AXApplication（正常树一定含 AXWindow/AXGroup/... ）。"""
    return len(roles) >= 4 and all(r == "AXApplication" for r in roles)


def validate_optional_locator(loc: dict) -> None:
    """定位可选（如 assert-text 扫全树）：给了任何定位字段才校验，没给就整棵树。"""
    if any(loc.get(k) for k in ("path", "role", "name", "title", "desc", "value", "help_text")):
        validate_locator(loc)


def filter_deepest(matches: list[dict]) -> list[dict]:
    """--subtree 命中祖先+后代时，只保留最深的（否则窗口/行/单元格会一起命中）。"""
    paths = [str(m.get("path", "")) for m in matches]
    keep = []
    for m, p in zip(matches, paths):
        if not any(other != p and other.startswith(p + ".") for other in paths):
            keep.append(m)
    return keep


def pick_pid(candidates: list[tuple[int, str]], exe_path: str) -> int | None:
    """从 (pid, 命令行) 里挑**命令行完全等于可执行文件路径**的那个。

    不能只用 `pgrep -f <bundle 路径>`：它会命中「命令行里包含该路径」的 shell/编辑器进程
    （B-191 实测：把 bash 当成了 App，AX 树只有 1 个元素）。这里按 ps 的 command 精确比对。
    """
    want = str(exe_path)
    for pid, command in candidates:
        if (command or "").strip() == want:
            return int(pid)
    return None


def parse_helper_output(stdout: str, stderr: str, returncode: int) -> dict:
    """解析 axhelper 的 JSON（取最后一行非空；容忍前面的告警/噪声行）。"""
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "error": "no_output", "message": (stderr or "").strip()[:400],
                "_rc": returncode}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": "bad_json",
                "message": f"{exc}: {(lines[-1] or '')[:200]}", "_rc": returncode}
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_json", "message": "helper 输出不是 JSON 对象",
                "_rc": returncode}
    data.setdefault("ok", False)
    data["_rc"] = returncode
    return data


def element_names(el: dict) -> list[str]:
    return [el.get(k, "") for k in ("title", "desc", "value", "help") if el.get(k)]


def element_name(el: dict) -> str:
    for name in element_names(el):
        return name
    return ""


def match_text(needle: str, hay: str, *, exact: bool = False, ignore_case: bool = False) -> bool:
    a, b = (hay or ""), (needle or "")
    if ignore_case:
        a, b = a.casefold(), b.casefold()
    return a == b if exact else b in a


# ---------------------------------------------------------------------------
# alert 解析（name-based；禁索引）
# ---------------------------------------------------------------------------

def alert_titles(alert: dict) -> list[str]:
    titles = []
    if alert.get("title"):
        titles.append(alert["title"])
    titles += [t for t in alert.get("texts", []) if t and t not in titles]
    return titles


def collapse_nested_alerts(alerts: list[dict]) -> list[dict]:
    """折叠「alert 套 alert」的外层候选：祖先候选的 texts 递归包含内层 alert 文本。

    B-197（2026-10-23 真点：嵌套 sheet alert 误报多命中）——「编辑引用库条目」editor sheet
    （AXSheet，无 desc=alert）内部嵌套真正的 alert（AXSheet desc=alert，标题「确认写入引用库？」）；
    axhelper 的 alertsPayload 把两层都收进来，外层 textsIn 递归含内层文本，于是 choose_alert 看到
    同一个标题命中 2 个。这里只丢弃「path 是另一个候选 path 的祖先」的外层候选
    （child.startswith(parent + ".")），相同 path 去重（留首个）；**并列兄弟多命中不折叠**，
    仍由 choose_alert 拒绝——B-191「绝不猜」的安全边界不变。
    """
    unique: list[dict] = []
    seen: set[str] = set()
    for alert in alerts:
        path = str(alert.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        unique.append(alert)
    paths = [str(a.get("path", "")) for a in unique]
    return [a for a, p in zip(unique, paths)
            if not any(other != p and other.startswith(p + ".") for other in paths)]


def alert_press_verdict(press_ok: bool, dismissed: bool | None,
                        verify_dismiss: bool) -> tuple[bool, str]:
    """B-197 附带：把「AXPress 结果 + 关闭验证」合成 alert 点击的最终判定。

    SwiftUI 的 `.cancel` 角色按钮（alert 里的「取消」）实测 AXPress 返回
    `-25205 attribute-unsupported`，但**窗口实际已关闭**（2026-10-23 真点，1.5 s 内
    dump 已无 alert）。因此 press 报错不等于点空：只要 `verify_dismiss` 且随后确认
    alert 消失，就按成功处理（log 记 tolerated）。若 alert 仍在，仍判失败；传
    `--no-verify-dismiss` 时没有「消失」证据，press 报错仍判失败（不猜）。

    返回 `(success, reason)`；reason ∈ pressed / tolerated / alert_still_present /
    press_error_and_alert_still_present / press_error_unverified。
    """
    if verify_dismiss:
        if dismissed is True:
            return True, ("pressed" if press_ok else "tolerated")
        return False, ("alert_still_present" if press_ok
                       else "press_error_and_alert_still_present")
    return press_ok, ("pressed" if press_ok else "press_error_unverified")


def choose_alert(alerts: list[dict], title: str, *, exact: bool = False,
                 body: str = "", ignore_case: bool = False) -> tuple[dict | None, str]:
    """按标题在 alert 候选里挑唯一一个；返回 (alert, 错误说明)。"""
    hits = [a for a in alerts if any(match_text(title, t, exact=exact, ignore_case=ignore_case)
                                     for t in alert_titles(a))]
    if not hits:
        seen = [t for a in alerts for t in alert_titles(a)[:2]]
        return None, f"没有标题匹配「{title}」的 alert；当前 alert 文本：{seen or '（无 alert）'}"
    if len(hits) > 1:
        return None, f"标题「{title}」命中 {len(hits)} 个 alert，拒绝猜测；请先用 dump 看 alerts 树"
    alert = hits[0]
    if body and not any(match_text(body, t, ignore_case=ignore_case)
                        for t in alert_titles(alert)):
        return None, f"alert 标题命中，但正文不含「{body}」；实际文本：{alert_titles(alert)}"
    return alert, ""


def choose_button(alert: dict, name: str, *, exact: bool = True) -> tuple[dict | None, str]:
    """按**按钮名**挑 alert 按钮（默认精确匹配）；0 命中 / 多命中都拒绝执行。"""
    buttons = alert.get("buttons", [])
    hits = [b for b in buttons if any(match_text(name, n, exact=exact) for n in element_names(b) + [b.get("name", "")])]
    listing = [b.get("name", "?") for b in buttons]
    if not hits:
        return None, f"alert 里没有名为「{name}」的按钮；现有按钮：{listing}"
    if len(hits) > 1:
        return None, f"按钮名「{name}」命中 {len(hits)} 个（{listing}），拒绝按索引点；请给更精确的 --button"
    return hits[0], ""


def button_path_sane(alert_path: str, button_path: str) -> bool:
    """按钮路径必须是 alert 的**后代绝对路径**（B-191 实测踩坑：helper 曾返回相对 "3"）。"""
    a, b = str(alert_path or ""), str(button_path or "")
    if not a or not b:
        return False
    return b == a or b.startswith(a + ".")


def validate_alert_args(ns) -> None:
    """alert 断言参数校验（禁索引点击：B-190 曾 2 次按索引误点）。"""
    if getattr(ns, "index", None) is not None:
        raise UsageError(
            "assert-alert **禁止按索引点按钮**（B-190 真点 2 次按索引误点：不同 alert 的按钮索引会漂移）；"
            "请用 --title <alert 标题> + --button <按钮名>")
    if not ns.title:
        raise UsageError("assert-alert 必须给 --title（alert 标题；用 dump / alerts 先看）")
    if ns.click and not ns.button:
        raise UsageError("--click 必须同时给 --button <按钮名>（按名字点，不按索引）")


# ---------------------------------------------------------------------------
# 子进程封装（self-test 期间会被替换成哨兵）
# ---------------------------------------------------------------------------

def run_cmd(argv: list[str], *, runner=None, timeout: int = 120):
    fn = runner or subprocess.run
    return fn(argv, capture_output=True, text=True, timeout=timeout)


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def status_digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# helper 编译/调用
# ---------------------------------------------------------------------------

def helper_cache_name(source_bytes: bytes) -> str:
    return "axhelper-" + hashlib.sha256(source_bytes).hexdigest()[:16]


def build_helper(source: Path = HELPER_SOURCE, cache_dir: Path = HELPER_CACHE_DIR,
                 *, runner=None) -> Path:
    """按源码 sha 编译 axhelper 到 /tmp 缓存（并发安全：先写 .tmp-<pid> 再原子替换）。"""
    src = Path(source)
    if not src.is_file():
        raise EnvProblem(f"缺 AX helper 源码：{src}")
    src_bytes = src.read_bytes()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / helper_cache_name(src_bytes)
    if target.is_file() and os.access(target, os.X_OK):
        return target
    tmp = cache_dir / f".tmp-{os.getpid()}-{target.name}"
    proc = run_cmd(["swiftc", "-O", "-swift-version", "5", "-o", str(tmp), str(src)], runner=runner)
    if proc.returncode != 0:
        raise EnvProblem("swiftc 编译 axhelper 失败（本机需要 Xcode Command Line Tools）：\n"
                         f"{(proc.stderr or '').strip()[:800]}")
    os.chmod(tmp, 0o755)
    os.replace(tmp, target)
    return target


def helper_call(args: list[str], *, helper: Path | None = None, runner=None,
                timeout: int = 60) -> dict:
    helper = helper or build_helper(runner=runner)
    proc = run_cmd([str(helper)] + args, runner=runner, timeout=timeout)
    return parse_helper_output(proc.stdout, proc.stderr, proc.returncode)


# ---------------------------------------------------------------------------
# AppleScript / pbcopy / screencapture（命令构造是纯函数）
# ---------------------------------------------------------------------------

def frontmost_script(process: str) -> str:
    safe = process.replace('"', '\\"')
    return f'tell application "System Events" to tell process "{safe}" to set frontmost to true'


def keystroke_script(key: str, using: str = "command down") -> str:
    safe = key.replace('"', '\\"')
    return f'tell application "System Events" to keystroke "{safe}" using {using}'


def pbcopy_argv() -> list[str]:
    return ["pbcopy"]


def shot_tmp_name(pid: int) -> str:
    """截图临时文件名：**不能**以 '.' 开头 —— screencapture 对隐藏文件名报
    「cannot write file to intended destination」（B-191 实测）；产物复制后立即删除。"""
    return f"tmp-shot-{pid}.png"


def screencapture_argv(rect: tuple[int, int, int, int] | None, out: Path) -> list[str]:
    """窗口矩形（AX frame）→ screencapture -R；rect=None 表示整屏。"""
    if rect is None:
        return ["screencapture", "-x", "-o", str(out)]
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise EnvProblem(f"窗口矩形非法（w={w}, h={h}）：AX frame 不可用；可退 --full（整屏）")
    return ["screencapture", "-x", "-o", "-R", f"{x},{y},{w},{h}", str(out)]


def window_rect(frame: dict, *, pad: int = 0) -> tuple[int, int, int, int]:
    x = int(round(float(frame.get("x", 0)))) - pad
    y = int(round(float(frame.get("y", 0)))) - pad
    w = int(round(float(frame.get("w", 0)))) + 2 * pad
    h = int(round(float(frame.get("h", 0)))) + 2 * pad
    return x, y, w, h


# ---------------------------------------------------------------------------
# evidence pack
# ---------------------------------------------------------------------------

def validate_evidence_dir(directory) -> Path:
    """evidence-dir 必须是**绝对路径**（V1 F2 medium：`.`/相对路径会把证据写进 cwd，污染工作树）。

    空值不走这里：CLI 层已把它回退到 `$TMPDIR/gui-walkthrough-evidence`；直接调 Evidence("") 仍报错（内部护栏）。
    """
    raw = str(directory or "")
    if not raw.strip():
        raise UsageError("--evidence-dir 不能为空；请传**绝对路径**"
                         "（例：--evidence-dir /tmp/gui-run-evidence；留空 = 默认 $TMPDIR/gui-walkthrough-evidence）")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise UsageError(
            f"--evidence-dir 只接受**绝对路径**（收到 {raw!r}）：相对路径/`.` 会把证据写进当前目录"
            f"（{Path.cwd()}）并污染工作树。例：--evidence-dir /tmp/b191/evidence/…；"
            f"留空 = 默认 $TMPDIR/gui-walkthrough-evidence")
    return path


class Evidence:
    """固定命名 + 一个 VERDICT 汇总；跨命令用 state.json 续编号。"""

    def __init__(self, directory: Path, *, app: str = "", pid: int | None = None,
                 process: str = "", reset: bool = False):
        self.dir = validate_evidence_dir(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / STATE_NAME
        self.state = self._load()
        if self.state is None or reset:
            if reset:
                self._archive_previous()
            self.state = {
                "tool": "gui_walkthrough", "version": TOOL_VERSION,
                "run": "run-" + datetime.now().astimezone().strftime("%Y%m%dT%H%M%S"),
                "started": _now(), "dir": str(self.dir), "seq": 0, "entries": [],
                "notes": [], "app": str(app or ""), "pid": pid, "process": process or "",
            }
            self._save()

    # -- state -------------------------------------------------------------
    def _load(self) -> dict | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2,
                                              sort_keys=True) + "\n", encoding="utf-8")

    def _archive_previous(self) -> None:
        """--reset：把旧证据移到 archive-<ts>/（不删，防丢证据）。"""
        old = [p for p in self.dir.iterdir()
               if p.is_file() and (re.match(r"^\d{2,}-", p.name) or p.name.startswith("VERDICT-SUMMARY")
                                   or p.name == STATE_NAME)]
        if not old:
            return
        dest = self.dir / (ARCHIVE_PREFIX + datetime.now().astimezone().strftime("%Y%m%dT%H%M%S"))
        dest.mkdir(parents=True, exist_ok=True)
        for p in old:
            shutil.move(str(p), str(dest / p.name))

    # -- record ------------------------------------------------------------
    def next_seq(self) -> int:
        return int(self.state.get("seq", 0)) + 1

    def note(self, text: str) -> None:
        if text and text not in self.state["notes"]:
            self.state["notes"].append(text)
            self._save()

    def record(self, cmd: str, slug: str, status: str, *, ms: float, note: str = "",
               text_log: str = "", json_data: dict | None = None,
               binary: tuple[str, Path] | None = None) -> dict:
        seq = self.next_seq()
        self.state["seq"] = seq
        files: list[str] = []
        if binary is not None:
            ext, src = binary
            name = evidence_name(seq, cmd, slug, ext)
            shutil.copyfile(src, self.dir / name)
            files.append(name)
        if text_log:
            name = evidence_name(seq, cmd, slug, "log")
            (self.dir / name).write_text(text_log, encoding="utf-8")
            files.append(name)
        if json_data is not None:
            name = evidence_name(seq, cmd, slug, "json")
            (self.dir / name).write_text(json.dumps(json_data, ensure_ascii=False, indent=2,
                                                    sort_keys=True) + "\n", encoding="utf-8")
            files.append(name)
        entry = {"seq": seq, "cmd": cmd, "slug": slug, "status": status,
                 "ms": int(round(ms)), "note": note, "files": files,
                 "at": _now()}
        self.state["entries"].append(entry)
        self._save()
        self.write_summary()
        return entry

    # -- summary -----------------------------------------------------------
    def summary(self) -> tuple[str, dict]:
        entries = self.state.get("entries", [])
        counts = {k: sum(1 for e in entries if e["status"] == k) for k in ("PASS", "FAIL", "SKIP")}
        if counts["FAIL"]:
            verdict = "FAIL"
        elif entries and counts["PASS"] == 0:
            verdict = "SKIP"
        else:
            verdict = "PASS"
        head = (f"# gui_walkthrough 证据包 VERDICT（B-191 T1 / v{TOOL_VERSION}）\n"
                f"run: {self.state.get('run')} | 目录: {self.dir} | started: {self.state.get('started')}\n"
                f"app: {self.state.get('app') or '—'} | pid: {self.state.get('pid')} | "
                f"process: {self.state.get('process') or '—'}\n"
                f"VERDICT: {verdict}   （PASS {counts['PASS']} / FAIL {counts['FAIL']} / "
                f"SKIP {counts['SKIP']}；{len(entries)} 步）\n")
        lines = [head, "## 步骤"]
        for e in entries:
            files = ", ".join(e.get("files", [])) or "—"
            lines.append(f"{e['seq']:02d}  {e['cmd']:<12} {e['slug']:<20} {e['status']:<5} "
                         f"{e['ms'] / 1000:6.2f}s  {files}"
                         + (f"  # {e['note']}" if e.get("note") else ""))
        if not entries:
            lines.append("（尚无步骤）")
        notes = self.state.get("notes", [])
        lines.append("## 备注")
        lines += [f"- {n}" for n in notes] or ["- （无）"]
        text = "\n".join(lines) + "\n"
        obj = {"tool": "gui_walkthrough", "version": TOOL_VERSION, "run": self.state.get("run"),
               "dir": str(self.dir), "started": self.state.get("started"),
               "app": self.state.get("app"), "pid": self.state.get("pid"),
               "process": self.state.get("process"), "verdict": verdict, "counts": counts,
               "steps": entries, "notes": notes}
        return text, obj

    def write_summary(self) -> tuple[str, dict]:
        text, obj = self.summary()
        (self.dir / VERDICT_TXT).write_text(text, encoding="utf-8")
        (self.dir / VERDICT_JSON).write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                                                        sort_keys=True) + "\n", encoding="utf-8")
        return text, obj


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------------------
# fixture（/tmp 隔离夹具）：计划 = 纯函数，执行 = 薄 runner
# ---------------------------------------------------------------------------

def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def guard_fixture_root(root: Path, notes_repo: Path) -> Path:
    """夹具根只准在临时目录（/tmp 或 $TMPDIR）；不许挨着真实 notes 仓。返回 realpath。"""
    real = Path(os.path.realpath(root))
    candidates = {Path(os.path.realpath(tempfile.gettempdir())), Path("/tmp"), Path("/private/tmp")}
    if not any(real == c or _is_under(real, c) for c in candidates):
        raise UsageError(f"fixture 根必须在 /tmp（或 $TMPDIR）下，拒绝在 {real} 建夹具（防误碰真实库/稿件仓）")
    notes_real = Path(os.path.realpath(notes_repo))
    if real == notes_real or _is_under(real, notes_real) or _is_under(notes_real, real):
        raise UsageError(f"fixture 根 {real} 与真实 notes 仓 {notes_real} 互相嵌套，拒绝执行")
    return real


def fixture_plan(root: Path, notes_repo: Path, domain: str, key: str) -> list[dict]:
    """fixture 的**命令计划**（纯函数；自测只断言 argv，不执行）。"""
    root = guard_fixture_root(root, notes_repo)
    notes_repo = Path(os.path.realpath(notes_repo))
    return [
        {"note": "bare clone 真实 notes → /tmp/remote.git",
         "argv": ["git", "clone", "--bare", str(notes_repo), str(root / "remote.git")]},
        {"note": "从 /tmp bare clone 出测试 clone",
         "argv": ["git", "clone", str(root / "remote.git"), str(root / "notes")]},
        {"note": "App defaults 指向测试 clone",
         "argv": ["defaults", "write", domain, key, str(root / "notes")]},
        {"note": "readback 测试 clone 路径",
         "argv": ["defaults", "read", domain, key]},
    ]


def teardown_plan(root: Path, domain: str, key: str, prior: str | None) -> list[dict]:
    """恢复计划（纯函数）：有原值 → 还原；无原值 → delete；最后清理 /tmp 夹具。"""
    real = Path(os.path.realpath(root))
    if not any(real == c or _is_under(real, c)
               for c in {Path(os.path.realpath(tempfile.gettempdir())), Path("/tmp"), Path("/private/tmp")}):
        raise UsageError(f"拒绝清理非 /tmp 路径：{real}")
    restore = (["defaults", "write", domain, key, prior] if prior is not None
               else ["defaults", "delete", domain, key])
    return [
        {"note": "恢复 App defaults（原值还原 / 无原值则 delete）", "argv": restore},
        {"note": "readback 恢复结果", "argv": ["defaults", "read", domain, key], "allow_rc": (0, 1)},
        {"note": "清理 /tmp 夹具目录", "argv": ["rm", "-rf", str(real)]},
    ]


def run_plan(plan: list[dict], *, runner=None) -> list[dict]:
    """按计划顺序执行；失败即停（后续步骤不跑）。"""
    results = []
    for step in plan:
        proc = run_cmd(step["argv"], runner=runner)
        allow = step.get("allow_rc", (0,))
        results.append({"note": step["note"], "argv": step["argv"], "rc": proc.returncode,
                        "out": (proc.stdout or "").strip(), "err": (proc.stderr or "").strip()})
        if proc.returncode not in allow:
            raise EnvProblem(f"{step['note']} 失败（rc={proc.returncode}）："
                             f"{(proc.stderr or proc.stdout or '').strip()[:400]}")
    return results


def snapshot_repo(repo: Path, *, runner=None) -> dict:
    """真实 notes 仓快照（HEAD + status 摘要 + 真实库 sha）：用于前后比对。"""
    repo = Path(repo)
    head = ""
    status = ""
    if (repo / ".git").exists():
        head_proc = run_cmd(["git", "-C", str(repo), "rev-parse", "HEAD"], runner=runner)
        head = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
        status_proc = run_cmd(["git", "-C", str(repo), "status", "--porcelain"], runner=runner)
        status = (status_proc.stdout or "") if status_proc.returncode == 0 else ""
    db = repo / "bib_db" / "bibliography.jsonl"
    return {"repo": str(repo), "head": head, "status_digest": status_digest(status),
            "db_sha256": sha256_file(db)}


def snapshot_equal(a: dict, b: dict) -> tuple[bool, list[str]]:
    diffs = [k for k in ("head", "status_digest", "db_sha256") if a.get(k) != b.get(k)]
    return (not diffs, diffs)


# ---------------------------------------------------------------------------
# AX 树渲染（纯函数：dump 的文本证据）
# ---------------------------------------------------------------------------

def _clip(text: str, limit: int = 400) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"…[+{len(text) - limit}]"


def render_dump(elements: list[dict], *, max_depth: int | None = None,
                header: str = "# AX dump", field_limit: int = 160) -> str:
    """elements（axhelper dump 的 JSON）→ 人类可读缩进树文本（确定性、可直接进证据）。"""
    lines = [f"{header}（count={len(elements)} max_depth={max_depth}）"]
    for el in elements:
        indent = "  " * int(el.get("depth", 0))
        parts = [f"[{el.get('path', '')}]", el.get("role", "")]
        if el.get("subrole"):
            parts.append(f"sub={el['subrole']}")
        for key in ("title", "desc", "value", "help"):
            if el.get(key):
                parts.append(f"{key}={_clip(el[key], field_limit)}")
        if el.get("selected"):
            parts.append("SELECTED")
        lines.append(indent + " | ".join(parts))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 命令行无关的小工具（入口/驱动共用）
# ---------------------------------------------------------------------------

def locator_from_ns(ns) -> dict:
    """argparse 命名空间 → 定位 dict（缺的字段一律空；不猜元素）。"""
    g = lambda k, d="": getattr(ns, k, d)  # noqa: E731
    return {"path": g("path"), "role": g("role"), "name": g("name"), "title": g("title"),
            "desc": g("desc"), "value": g("value"), "help_text": g("help_text"),
            "within": g("within"), "nth": g("nth", None), "exact": g("exact", False),
            "subtree": g("subtree", False), "max_depth": g("max_depth", 0) or None}


def default_slug(ns, cmd: str) -> str:
    for attr in ("slug", "name", "title", "contains", "equals", "text"):
        val = getattr(ns, attr, None)
        if val:
            return slugify(str(val), fallback=cmd)
    return cmd


def emit(ns, entry: dict, status: str, message: str, data: dict, *, code: int) -> int:
    if ns.json:
        payload = {"ok": status == "PASS", "cmd": entry["cmd"], "status": status,
                   "exit_code": code, "evidence": entry.get("files", []),
                   "evidence_dir": entry.get("_dir") or "", "message": message,
                   "data": {k: v for k, v in data.items() if not k.startswith("_")},
                   "seq": entry["seq"]}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "•"}.get(status, "?")
        files = ", ".join(entry.get("files", []))
        print(f"{mark} {entry['seq']:02d} {entry['cmd']} {status} "
              f"{entry['ms'] / 1000:.2f}s  {message}")
        if files:
            print(f"   证据: {files}")
    return code


