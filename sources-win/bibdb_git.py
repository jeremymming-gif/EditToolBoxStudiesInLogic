"""引用库本地版本层（B-282a）：写库后**只**提交 `bib_db/bibliography.jsonl`。

这一层是 mac `sources-mac/Features/BibDbGit.swift` 的 Windows 对等物，只做 git
调用与结果解析，**不 import tkinter**（桥测试可在无窗口环境直接测）：

* `find_git()`    —— 找 git 可执行文件；找不到返回 None（写库照常，只是不生成提交）。
* `locate_repo()` —— 库文件必须在一个 git 仓库里，且仓库相对路径恰为
  `bib_db/bibliography.jsonl`；否则拒绝提交并把原因说清楚（绝不猜别的路径）。
* `preflight()`   —— **只读**预检：分支 / upstream / behind-ahead / 脏文件 / 身份 /
  冲突与 rebase 进行中。绝不 fetch、pull、checkout、切分支。
* `commit_db()`   —— status → `git add -- 库文件`（pathspec 精确到这一个文件）→
  `git commit -m … -- 库文件` → 取 HEAD sha。没有改动就不空提交。
* `status()`      —— `preflight` 的只读探针（保留给界面单独刷新 git 状态行）。

硬约束（与 Lead 决策 D-01/D-02 一致）：
* 所有 argv 都是**列表**、`shell=False`；路径按 UTF-8 解码（`errors="replace"`）。
* 每个 git 子进程都带 `GIT_TERMINAL_PROMPT=0`，不会卡在凭据输入；另有超时。
* 只 `add`/`commit` 库文件；**绝不 push / pull / fetch / 切分支**。
* 出错一律返回 `{"ok": False, "soft": …, "error": …}`，**不抛异常** —— 写库已经成功时，
  版本层失败只能在界面上黄字提示，不能反过来把写库判成失败。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

#: 库文件在笔记仓里的**唯一**合法相对路径（与 mac `BibDbGit.dbRelativePath` 同值）
DB_RELATIVE_PATH = "bib_db/bibliography.jsonl"

#: 单次 git 调用超时（秒）；commit 要跑钩子，给得比只读探针宽一点
READ_TIMEOUT = 15.0
WRITE_TIMEOUT = 30.0

#: git 身份缺失时的统一话术（Windows 页没有 mac 的「提交…」按钮，不引导用户去找）
IDENTITY_ERROR = ("git 身份未配置（user.name / user.email）：本次写库已成功，"
                  "但未生成版本提交；配置好身份后再次写库即可补上提交。")


# ── 基础：找 git / 跑 git ────────────────────────────────────────────────

def find_git() -> str | None:
    """在 PATH 里找 git；Windows 再兜底两个常见安装位置。找不到返回 None。"""
    found = shutil.which("git")
    if found:
        return found
    if os.name == "nt":                       # 从资源管理器启动的 GUI 常常没有完整 PATH
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Git", "cmd", "git.exe"),
            os.path.join(os.environ.get("LocalAppData", ""),
                         "Programs", "Git", "cmd", "git.exe"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
    return None


def _git_env() -> dict:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"          # 绝不卡在用户名/密码提示
    # 用赋值覆盖继承值：父进程若已有 GIT_ASKPASS（如 CI 的 /bin/false），
    # setdefault 会让它漏进来；这里统一强制成「立即失败而不是挂住」。
    env["GIT_ASKPASS"] = "true"
    return env


def _run(git: str, args: list[str], *, timeout: float = READ_TIMEOUT,
         cwd: str | None = None) -> tuple[int, str, str]:
    """跑一次 git（列表 argv、无 shell）。启动失败/超时也变成可读的返回码。"""
    argv = [git, "-c", "core.quotepath=false"]
    if cwd:
        argv += ["-C", cwd]
    argv += list(args)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, env=_git_env(), shell=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"git 调用超时（>{timeout:.0f}s）：{' '.join(args)}"
    except OSError as exc:                    # 可执行文件被删、权限不足等
        return 127, "", f"无法启动 git：{exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _error_of(stdout: str, stderr: str, fallback: str) -> str:
    text = _one_line(stderr) or _one_line(stdout)
    return text or fallback


# ── 定位：库必须在仓库里的固定相对路径 ────────────────────────────────────

def locate_repo(db: str) -> dict:
    """确认 `db` 在一个 git 仓库里、且仓库相对路径恰为 `bib_db/bibliography.jsonl`。

    返回 dict：`ok / git / repo_root / db_abs / db_rel / error`。只读，不改变任何状态。
    """
    text = str(db or "").strip()
    if not text:
        return {"ok": False, "error": "没有指定引用库路径（db 为空），无法定位 git 仓库。"}
    # 统一取真实路径：macOS 的 /var → /private/var、Windows 的目录联接都会让
    # `rev-parse --show-toplevel` 与输入路径的写法不一致，先归一化再算相对路径。
    db_abs = os.path.realpath(os.path.abspath(text))
    if not os.path.isfile(db_abs):
        return {"ok": False, "error": f"引用库文件不存在：{db_abs}"}
    git = find_git()
    if not git:
        return {"ok": False, "error": "找不到 git 可执行文件：本次写库仍成功，但不会生成版本提交。"}
    directory = os.path.dirname(db_abs) or os.curdir
    if not os.path.isdir(directory):
        return {"ok": False, "error": f"引用库所在目录不存在：{directory}"}
    code, out, err = _run(git, ["rev-parse", "--show-toplevel"], cwd=directory)
    if code != 0:
        return {"ok": False,
                "error": ("引用库不在 git 仓库内（或仓库不可读）：本次写库仍成功，"
                          "但不会生成版本提交。" + (" " + _one_line(err) if err else ""))}
    repo_root = os.path.realpath(os.path.abspath(out.strip()))
    if not repo_root or not os.path.isdir(repo_root):
        return {"ok": False, "error": f"git 返回的仓库根目录不可用：{_one_line(out)}"}
    # Windows 大小写不敏感：比较时 normcase；对外显示统一用 `/`
    rel = os.path.relpath(db_abs, repo_root)
    if os.path.normcase(rel) != os.path.normcase(os.path.join(*DB_RELATIVE_PATH.split("/"))):
        rel_shown = rel.replace(os.sep, "/")
        return {"ok": False,
                "error": (f"引用库必须位于笔记仓的 {DB_RELATIVE_PATH}（当前相对路径是 "
                          f"{rel_shown}）：为防误提交别的文件，本次不生成版本提交。")}
    return {"ok": True, "git": git, "repo_root": repo_root, "db_abs": db_abs,
            "db_rel": DB_RELATIVE_PATH}


# ── 只读探针：分支 / upstream / behind-ahead / 脏文件 / 身份 ───────────────

_UNMERGED = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


def _porcelain_entries(text: str) -> list[str]:
    return [line for line in str(text or "").splitlines() if line.strip()]


def _probe(loc: dict) -> dict:
    """在 `locate_repo` 成功的基础上做只读检查；任何单步失败都只降级该字段。"""
    git = loc["git"]
    root = loc["repo_root"]
    rel = loc["db_rel"]
    result = {"ok": True, "git": git, "repo_root": root, "db_rel": rel,
              "branch": "", "head": "", "upstream": "", "behind": None, "ahead": None,
              "dirty_db": False, "dirty_other": 0, "dirty_paths": [],
              "conflict": False, "rebase": False, "merge": False,
              "identity_ok": False, "identity": "", "warnings": []}

    code, out, _err = _run(git, ["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if code == 0:
        result["branch"] = out.strip()
    code, out, _err = _run(git, ["rev-parse", "HEAD"], cwd=root)
    if code == 0:
        result["head"] = out.strip()

    code, out, _err = _run(git, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                 "@{upstream}"], cwd=root)
    if code == 0 and out.strip():
        result["upstream"] = out.strip()
        code, out, _err = _run(git, ["rev-list", "--left-right", "--count",
                                     "@{upstream}...HEAD"], cwd=root)
        if code == 0:
            parts = out.split()
            if len(parts) >= 2:
                try:
                    result["behind"] = int(parts[0])
                    result["ahead"] = int(parts[1])
                except ValueError:
                    pass
    if not result["upstream"]:
        result["warnings"].append("当前分支没有 upstream：本地提交不会推送（本工具也不 push）。")

    code, out, _err = _run(git, ["status", "--porcelain", "--", rel], cwd=root)
    if code == 0:
        result["dirty_db"] = bool(out.strip())
    code, out, _err = _run(git, ["status", "--porcelain"], cwd=root)
    if code == 0:
        # git 的 porcelain 路径一律用 `/`；Windows 下 os.sep 是 `\`，两边都归一化再比，
        # 否则库文件会被误算进「其它脏文件」。
        rel_norm = os.path.normcase(rel.replace("\\", "/"))
        other = []
        for line in _porcelain_entries(out):
            path = line[3:].strip() if len(line) > 3 else line.strip()
            # 重命名条目形如 `R  old -> new`，取新路径
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            path_norm = os.path.normcase(path.replace("\\", "/"))
            if path and path_norm != rel_norm:
                other.append(path)
            if len(line) >= 2 and line[:2] in _UNMERGED:
                result["conflict"] = True
        result["dirty_other"] = len(other)
        result["dirty_paths"] = other[:10]
        if result["conflict"]:
            result["warnings"].append("仓库有未解决的合并冲突：先处理冲突再写库。")

    code, out, _err = _run(git, ["rev-parse", "--git-dir"], cwd=root)
    if code == 0 and out.strip():
        git_dir = out.strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(root, git_dir)
        result["rebase"] = (os.path.isdir(os.path.join(git_dir, "rebase-merge"))
                            or os.path.isdir(os.path.join(git_dir, "rebase-apply")))
        result["merge"] = os.path.isfile(os.path.join(git_dir, "MERGE_HEAD"))
        if result["rebase"]:
            result["warnings"].append("仓库正在 rebase：先结束 rebase 再写库。")
        if result["merge"]:
            result["warnings"].append("仓库正在 merge：先结束 merge 再写库。")

    code, name, _err = _run(git, ["config", "user.name"], cwd=root)
    code2, email, _err2 = _run(git, ["config", "user.email"], cwd=root)
    name_s = name.strip() if code == 0 else ""
    email_s = email.strip() if code2 == 0 else ""
    result["identity_ok"] = bool(name_s and email_s)
    if result["identity_ok"]:
        result["identity"] = f"{name_s} <{email_s}>"
    else:
        result["warnings"].append("git 身份未配置（user.name / user.email）："
                                  "写库仍会成功，但不会生成版本提交。")
    return result


def status(db: str) -> dict:
    """只读 git 状态：`locate_repo` + `_probe`。失败时 `{ok: False, error: …}`。"""
    loc = locate_repo(db)
    if not loc.get("ok"):
        return {"ok": False, "error": loc.get("error", "无法定位 git 仓库。")}
    return _probe(loc)


def preflight(db: str) -> dict:
    """写库前的只读预检（分支/upstream/behind-ahead/dirty/身份/冲突/rebase）。

    与 `status` 同一份探针；保留独立名字是为了让调用点读起来就是「写前检查」。
    """
    return status(db)


# ── 提交：只 add/commit 库文件 ───────────────────────────────────────────

def short_sha(sha: str, length: int = 12) -> str:
    text = str(sha or "").strip()
    return text[:length] if len(text) > length else text


def commit_message(label: str, record_count: int, sha_before: str, sha_after: str,
                   note: str = "") -> str:
    """与 mac `BibDbGit.commitMessage` 同格式：

    `引用库：{label}（记录 N 条；sha {before12}→{after12}）`
    `label` 为空 → 「写库」；`note` 非空追加第二行 `备注：…`。
    """
    action = str(label or "").strip() or "写库"
    try:
        count = int(record_count)
    except (TypeError, ValueError):
        count = 0
    text = (f"引用库：{action}（记录 {count} 条；"
            f"sha {short_sha(sha_before)}→{short_sha(sha_after)}）")
    note_text = str(note or "").strip()
    if note_text:
        text += f"\n备注：{note_text}"
    return text


def commit_db(db: str, message: str) -> dict:
    """把**库文件**的当前改动提交成一次本地 commit。绝不 push/pull。

    返回 `{ok, committed, sha_before, sha_after, sha, message, reason, soft, error}`：
    * `committed=True` 只在真的生成了新提交时；
    * 库文件没有改动 → `committed=False`（不空提交）；
    * 没有 git / 非仓库 / 路径不符 / 身份缺失 → `ok=False, soft=True`，只黄字提示；
    * 其它 git 失败 → `ok=False, soft=False`，同样**不抛出**。
    """
    text = str(db or "").strip()
    subject = str(message or "").strip()
    if not subject:
        return {"ok": False, "committed": False, "soft": True,
                "error": "缺少提交说明（message 为空）：本次不生成版本提交。"}
    loc = locate_repo(text)
    if not loc.get("ok"):
        return {"ok": False, "committed": False, "soft": True,
                "error": loc.get("error", "无法定位 git 仓库。")}
    git = loc["git"]
    root = loc["repo_root"]
    rel = loc["db_rel"]

    # 独立写前检查（S4）：commit_db 在写库成功后才被调用，调用方页面加载时的 git
    # 状态可能早已过期；这里不看调用方，自己重跑一次只读探针。merge / rebase /
    # 冲突未解决时绝不提交（否则会把未完成的合并状态一并提交进去）。
    probe = _probe(loc)
    blocked = []
    if probe.get("conflict"):
        blocked.append("有未解决的合并冲突")
    if probe.get("merge"):
        blocked.append("merge 进行中")
    if probe.get("rebase"):
        blocked.append("rebase 进行中")
    if blocked:
        return {"ok": False, "committed": False, "soft": True,
                "error": ("git 仓库" + "、".join(blocked)
                          + "：本次写库已成功，但为避免把未完成的合并/rebase 状态"
                            "提交进去，未生成版本提交；请先处理完 git 状态再重试。")}

    code, out, err = _run(git, ["status", "--porcelain", "--", rel], cwd=root)
    if code != 0:
        return {"ok": False, "committed": False, "soft": False,
                "error": "git status 失败：" + _error_of(out, err, "未知错误")}
    if not out.strip():
        code, head, _err = _run(git, ["rev-parse", "HEAD"], cwd=root)
        sha = head.strip() if code == 0 else ""
        return {"ok": True, "committed": False, "sha_before": sha, "sha_after": sha,
                "sha": sha, "soft": True, "reason": "库文件没有改动，未生成空提交。"}

    code, head, err = _run(git, ["rev-parse", "HEAD"], cwd=root)
    sha_before = head.strip() if code == 0 else ""

    code, out, err = _run(git, ["add", "--", rel], cwd=root, timeout=WRITE_TIMEOUT)
    if code != 0:
        return {"ok": False, "committed": False, "soft": False,
                "sha_before": sha_before,
                "error": "git add 失败（未生成提交）：" + _error_of(out, err, "未知错误")}

    code, commit_out, err = _run(git, ["commit", "-m", subject, "--", rel], cwd=root,
                                 timeout=WRITE_TIMEOUT)
    if code != 0:
        blob = ((err or "") + "\n" + (commit_out or "")).lower()
        identity_missing = any(mark in blob for mark in (
            "author identity unknown", "please tell me who you are",
            "unable to auto-detect email address", "user.email", "user.name"))
        return {"ok": False, "committed": False, "soft": bool(identity_missing),
                "sha_before": sha_before,
                "error": IDENTITY_ERROR if identity_missing else
                         "git commit 失败（未生成提交）："
                         + _error_of(commit_out, err, "未知错误")}

    code, head, err = _run(git, ["rev-parse", "HEAD"], cwd=root)
    sha_after = head.strip() if code == 0 else ""
    if not sha_after:
        # 极少数环境里 commit 成功、随后的 rev-parse 却失败：从 commit 输出
        # （`[master abc1234] subject`）回退取 sha，避免「已提交却报版本失败」。
        match = re.search(r"\[[^\]]*?\b([0-9a-f]{7,40})\b\]", commit_out or "")
        if match:
            return {"ok": True, "committed": True, "sha_before": sha_before,
                    "sha_after": match.group(1), "sha": match.group(1),
                    "message": subject, "soft": False,
                    "reason": "rev-parse 失败，sha 取自 git commit 输出。"}
        return {"ok": False, "committed": True, "soft": True,
                "sha_before": sha_before, "sha_after": "",
                "error": "提交已生成，但读不到新的 HEAD sha：请用 `git log -1` 核对。"}
    return {"ok": True, "committed": True, "sha_before": sha_before,
            "sha_after": sha_after, "sha": sha_after, "message": subject, "soft": False}
