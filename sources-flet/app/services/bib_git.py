"""引用库本地版本层（B-190 / B-203）：写库后**只**提交 `bib_db/bibliography.jsonl`。

这是旧 Tk 壳 `sources-win/bibdb_git.py`（历史文件，2026-11-01 已删除）与 mac 历史实现
`Features/BibDbGit.swift` 的 Flet 对等物：
只做 git 调用与结果解析（**不 import flet**，测试与无窗口环境都能直接跑）：

* `notes_root()` / `default_db()` —— 按「设置里的笔记仓 → `SLCN_NOTES` → 仓库同级
  `EditToolBox-notes`」找库文件；找不到不落地、不猜别处，只把原因交给界面。
* `status()` —— 只读探针：分支 / HEAD / upstream / behind-ahead / 库脏否 / 其它脏
  文件 / 冲突 / merge / rebase / 身份 / 最近 3 条库提交。
* `commit_db()` —— 写库成功后 `git add -- 库文件` + `git commit -m … -- 库文件`；
  库没变就不空提交；冲突 / merge / rebase 进行中一律拒绝提交。
* `push()` —— **只**推「连续 bib-only 前缀」（B-203）：最旧未推送提交就不是 bib_db/
  → 整次拒绝；否则 push 到该前缀的 tip，绝不 `--force`，`notes` 的其它提交不推。
* `rollback()` —— 按库文件内容历史取「上一个版本」`git checkout <该版本提交> --
  库文件` + 一次新 commit（不 hard reset、不 push）；库文件 dirty / merge /
  rebase / 冲突时拒绝且零写，回退没产生新提交时 `ok=False`、不伪造成功。

硬约束：argv 一律列表、`shell=False`；`GIT_TERMINAL_PROMPT=0` 防卡在凭据输入；
只 `add`/`commit`/`push`（仅显式点推送）库文件，**绝不** `pull`/`fetch`/`checkout`
分支/`reset --hard`；出错一律返回 `{"ok": False, "soft": …, "error": …}`，不抛异常
（写库已成功时，版本层失败只能黄字提示，不能反过来把写库判成失败）。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess

#: 库文件在笔记仓里的**唯一**合法相对路径（与 mac / Windows 同值）
DB_RELATIVE_PATH = "bib_db/bibliography.jsonl"
#: 笔记仓的同级目录名（与 Engine/slcn/paths.py 的 NOTES_SIBLING 同值）
NOTES_SIBLING = "EditToolBox-notes"

READ_TIMEOUT = 15.0
WRITE_TIMEOUT = 30.0
PUSH_TIMEOUT = 60.0

IDENTITY_ERROR = ("git 未配置 user.name/email：已写库，"
                  "未生成提交；配置好后再写库即可补上。")


# ── 路径发现 ────────────────────────────────────────────────────────────

def _repo_root() -> str:
    """开发态仓库根：`sources-flet/app/services/bib_git.py` 往上 3 层。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def notes_root(notes_repo: str = "") -> str:
    """找笔记仓根：设置 → `SLCN_NOTES` → 仓库同级 `EditToolBox-notes`。

    判据与引擎 `paths.notes_root()` 一致：目录里要有 `cases/`。找不到返回空串。
    """
    candidates = []
    text = str(notes_repo or "").strip()
    if text:
        candidates.append(text)
    env = str(os.environ.get("SLCN_NOTES") or "").strip()
    if env:
        candidates.append(env)
    candidates.append(os.path.join(os.path.dirname(_repo_root()), NOTES_SIBLING))
    candidates.append(os.path.join(_repo_root(), NOTES_SIBLING))
    for candidate in candidates:
        try:
            root = os.path.realpath(os.path.abspath(candidate))
        except (OSError, ValueError):
            continue
        if os.path.isdir(root) and os.path.isdir(os.path.join(root, "cases")):
            return root
    return ""


def default_db(notes_repo: str = "") -> str:
    """笔记仓里的库文件绝对路径（不保证存在；空 = 找不到笔记仓）。"""
    root = notes_root(notes_repo)
    return os.path.join(root, *DB_RELATIVE_PATH.split("/")) if root else ""


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
    # 父进程若已有 GIT_ASKPASS（如 CI 的 /bin/false），setdefault 会让它漏进来，
    # 这里统一强制成「立即失败而不是挂住」。
    env["GIT_ASKPASS"] = "true"
    return env


def _run(git: str, args: list[str], *, timeout: float = READ_TIMEOUT,
         cwd: str | None = None, extra: list[str] | None = None
         ) -> tuple[int, str, str]:
    """跑一次 git（列表 argv、无 shell）。启动失败/超时也变成可读的返回码。"""
    argv = [git, "-c", "core.quotepath=false"]
    argv += list(extra or [])
    if cwd:
        argv += ["-C", cwd]
    argv += list(args)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
    return _one_line(stderr) or _one_line(stdout) or fallback


# ── 定位：库必须在仓库里的固定相对路径 ──────────────────────────────────

def locate_repo(db: str) -> dict:
    """确认 `db` 在 git 仓库里、且仓库相对路径恰为 `bib_db/bibliography.jsonl`。

    返回 `ok / git / repo_root / db_abs / db_rel / error`。只读，不改变任何状态。
    """
    text = str(db or "").strip()
    if not text:
        return {"ok": False, "error": "没指定引用库路径，无法定位 git 仓库。"}
    # 统一取真实路径：目录联接 / 符号链接会让 `rev-parse --show-toplevel` 与输入
    # 路径写法不一致，先归一化再算相对路径。
    db_abs = os.path.realpath(os.path.abspath(text))
    if not os.path.isfile(db_abs):
        return {"ok": False, "error": f"引用库文件不存在：{db_abs}"}
    git = find_git()
    if not git:
        return {"ok": False, "error": "找不到 git：已写库，未生成提交。"}
    directory = os.path.dirname(db_abs) or os.curdir
    code, out, err = _run(git, ["rev-parse", "--show-toplevel"], cwd=directory)
    if code != 0:
        return {"ok": False,
                "error": ("引用库不在 git 仓库内：已写库，"
                          "未生成提交。" + (" " + _one_line(err) if err else ""))}
    repo_root = os.path.realpath(os.path.abspath(out.strip()))
    if not repo_root or not os.path.isdir(repo_root):
        return {"ok": False, "error": f"git 返回的仓库根目录不可用：{_one_line(out)}"}
    rel = os.path.relpath(db_abs, repo_root)
    if os.path.normcase(rel) != os.path.normcase(os.path.join(*DB_RELATIVE_PATH.split("/"))):
        return {"ok": False,
                "error": (f"引用库须在笔记仓的 {DB_RELATIVE_PATH}"
                          f"（当前 {rel.replace(os.sep, '/')}）："
                          "为避免误提交别的文件，未生成提交。")}
    return {"ok": True, "git": git, "repo_root": repo_root, "db_abs": db_abs,
            "db_rel": DB_RELATIVE_PATH}


# ── 只读探针：分支 / upstream / behind-ahead / 脏文件 / 身份 / 最近提交 ──

_UNMERGED = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


def _porcelain_entries(text: str) -> list[str]:
    return [line for line in str(text or "").splitlines() if line.strip()]


def parse_log_oneline(output: str, limit: int = 3) -> list[dict]:
    """`git log --oneline` → `[{sha, subject}]`（最多 limit 条）。"""
    rows: list[dict] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        sha = parts[0].strip()
        if not sha:
            continue
        rows.append({"sha": sha, "subject": parts[1].strip() if len(parts) > 1 else ""})
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _probe(loc: dict) -> dict:
    """在 `locate_repo` 成功的基础上做只读检查；任何单步失败都只降级该字段。"""
    git, root, rel = loc["git"], loc["repo_root"], loc["db_rel"]
    result = {"ok": True, "git": git, "repo_root": root, "db_rel": rel,
              "branch": "", "head": "", "upstream": "", "behind": None, "ahead": None,
              "dirty_db": False, "dirty_other": 0, "dirty_paths": [],
              "conflict": False, "rebase": False, "merge": False,
              "identity_ok": False, "identity": "", "commits": [], "warnings": []}
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
        result["warnings"].append("当前分支没有 upstream：只提交本地，不推送。")
    # `--no-optional-locks`：内容没变只 stale mtime 时，普通 `git status` 会刷新并
    # 改写 .git/index；status()/preflight() 承诺只读，所以禁止这类可选写。
    code, out, _err = _run(git, ["--no-optional-locks", "status", "--porcelain",
                                 "--", rel], cwd=root)
    if code == 0:
        result["dirty_db"] = bool(out.strip())
    code, out, _err = _run(git, ["--no-optional-locks", "status", "--porcelain"], cwd=root)
    if code == 0:
        # git porcelain 路径一律用 `/`；Windows 下 os.sep 是 `\`，两边归一化再比。
        rel_norm = os.path.normcase(rel.replace("\\", "/"))
        other: list[str] = []
        for line in _porcelain_entries(out):
            path = line[3:].strip() if len(line) > 3 else line.strip()
            if " -> " in path:                # 重命名条目取新路径
                path = path.split(" -> ", 1)[1].strip()
            path_norm = os.path.normcase(path.replace("\\", "/"))
            if path and path_norm != rel_norm:
                other.append(path)
            if len(line) >= 2 and line[:2] in _UNMERGED:
                result["conflict"] = True
        result["dirty_other"] = len(other)
        result["dirty_paths"] = other[:10]
        if result["conflict"]:
            result["warnings"].append("仓库有未解决的合并冲突：先处理再写库。")
    code, out, _err = _run(git, ["rev-parse", "--git-dir"], cwd=root)
    if code == 0 and out.strip():
        git_dir = out.strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(root, git_dir)
        result["rebase"] = (os.path.isdir(os.path.join(git_dir, "rebase-merge"))
                            or os.path.isdir(os.path.join(git_dir, "rebase-apply")))
        result["merge"] = os.path.isfile(os.path.join(git_dir, "MERGE_HEAD"))
        if result["rebase"]:
            result["warnings"].append("仓库正在 rebase：先结束后再写库。")
        if result["merge"]:
            result["warnings"].append("仓库正在 merge：先结束后再写库。")
    code, name, _err = _run(git, ["config", "user.name"], cwd=root)
    code2, email, _err2 = _run(git, ["config", "user.email"], cwd=root)
    name_s = name.strip() if code == 0 else ""
    email_s = email.strip() if code2 == 0 else ""
    result["identity_ok"] = bool(name_s and email_s)
    if result["identity_ok"]:
        result["identity"] = f"{name_s} <{email_s}>"
    else:
        result["warnings"].append("git 未配置 user.name/email："
                                  "写库仍会成功，但不会生成提交。")
    code, out, _err = _run(git, ["log", "--oneline", "-3", "--", rel], cwd=root)
    if code == 0:
        result["commits"] = parse_log_oneline(out, 3)
    return result


def status(db: str = "", *, notes_repo: str = "") -> dict:
    """只读 git 状态：`locate_repo` + `_probe`（失败时 `{ok: False, error: …}`）。"""
    loc = locate_repo(db or default_db(notes_repo))
    if not loc.get("ok"):
        return {"ok": False, "error": loc.get("error", "无法定位 git 仓库。")}
    return _probe(loc)


def preflight(db: str = "", *, notes_repo: str = "") -> dict:
    """写库前的只读预检；与 `status` 同一份探针，名字让调用点读起来就是「写前检查」。"""
    return status(db, notes_repo=notes_repo)


# ── 提交：只 add/commit 库文件 ──────────────────────────────────────────

def short_sha(sha: str, length: int = 12) -> str:
    text = str(sha or "").strip()
    return text[:length] if len(text) > length else text


def commit_message(label: str, record_count: int, sha_before: str, sha_after: str,
                   note: str = "") -> str:
    """与 mac `BibDbGit.commitMessage` 同格式：

    `引用库：{label}（记录 N 条；sha {before12}→{after12}）`；`label` 空 → 「写库」；
    `note` 非空追加第二行 `备注：…`（第一行格式不变）。
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


def revert_message(sha_before: str, sha_after: str) -> str:
    """回退提交说明（与 mac `BibDbGit.revertMessage` 同格式）。"""
    return f"引用库：回退到上一个提交（sha {short_sha(sha_before)}→{short_sha(sha_after)}）"


def file_sha256(path: str) -> str:
    """库文件字节 sha256；读不到返回空串（界面只显示，不做安全判据）。"""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def commit_db(db: str, message: str) -> dict:
    """把**库文件**的当前改动提交成一次本地 commit。绝不 push/pull。

    返回 `{ok, committed, sha_before, sha_after, sha, message, reason, soft, error}`：
    `committed=True` 只在真的生成了新提交时；库文件没有改动 → 不空提交；没有 git /
    非仓库 / 路径不符 / 身份缺失 → `soft=True`（界面黄字）；其它失败 → `soft=False`。
    """
    text = str(db or "").strip()
    subject = str(message or "").strip()
    if not subject:
        return {"ok": False, "committed": False, "soft": True,
                "error": "缺少提交说明：未生成提交。"}
    loc = locate_repo(text)
    if not loc.get("ok"):
        return {"ok": False, "committed": False, "soft": True,
                "error": loc.get("error", "无法定位 git 仓库。")}
    git, root, rel = loc["git"], loc["repo_root"], loc["db_rel"]
    # 独立写前检查：commit_db 在写库成功后才被调用，调用方页面加载时的 git 状态
    # 可能早已过期；这里不看调用方，自己重跑一次只读探针。merge / rebase / 冲突未
    # 解决时绝不提交（否则会把未完成的合并状态一并提交进去）。
    probe = _probe(loc)
    blocked = [name for name, flag in (
        ("有未解决的合并冲突", probe.get("conflict")),
        ("merge 进行中", probe.get("merge")),
        ("rebase 进行中", probe.get("rebase"))) if flag]
    if blocked:
        return {"ok": False, "committed": False, "soft": True,
                "error": ("git 仓库" + "、".join(blocked)
                          + "：已写库，但为避免把未完成的合并/rebase 状态提交进去，"
                            "未生成提交；请先处理完 git 状态再重试。")}
    code, out, err = _run(git, ["status", "--porcelain", "--", rel], cwd=root)
    if code != 0:
        return {"ok": False, "committed": False, "soft": False,
                "error": "git status 失败：" + _error_of(out, err, "未知错误")}
    if not out.strip():
        code, head, _err = _run(git, ["rev-parse", "HEAD"], cwd=root)
        sha = head.strip() if code == 0 else ""
        return {"ok": True, "committed": False, "sha_before": sha, "sha_after": sha,
                "sha": sha, "soft": True, "reason": "库文件没有改动，未生成空提交。"}
    code, head, _err = _run(git, ["rev-parse", "HEAD"], cwd=root)
    sha_before = head.strip() if code == 0 else ""
    code, out, err = _run(git, ["add", "--", rel], cwd=root, timeout=WRITE_TIMEOUT)
    if code != 0:
        return {"ok": False, "committed": False, "soft": False, "sha_before": sha_before,
                "error": "git add 失败：" + _error_of(out, err, "未知错误")}
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
                         "git commit 失败："
                         + _error_of(commit_out, err, "未知错误")}
    code, head, _err = _run(git, ["rev-parse", "HEAD"], cwd=root)
    sha_after = head.strip() if code == 0 else ""
    if not sha_after:
        # 极少数环境里 commit 成功、随后的 rev-parse 却失败：从 commit 输出
        # （`[master abc1234] subject`）回退取 sha，避免「已提交却报版本失败」。
        match = re.search(r"\[[^\]]*?\b([0-9a-f]{7,40})\b\]", commit_out or "")
        if match:
            return {"ok": True, "committed": True, "sha_before": sha_before,
                    "sha_after": match.group(1), "sha": match.group(1),
                    "message": subject, "soft": False,
                    "reason": "读不到 HEAD，sha 取自提交输出。"}
        return {"ok": False, "committed": True, "soft": True, "sha_before": sha_before,
                "sha_after": "", "error": "已提交，但读不到 HEAD sha：请用 `git log -1` 核对。"}
    return {"ok": True, "committed": True, "sha_before": sha_before,
            "sha_after": sha_after, "sha": sha_after, "message": subject, "soft": False}


# ── 推送（B-203）：只推「连续 bib-only 前缀」 ───────────────────────────

def parse_push_log(output: str) -> list[dict]:
    """解析推送预检日志（`\\x1e` 记录 / `\\x1f` 字段 / 其后每行一个路径）。

    顺序 = 从旧到新；坏记录（缺字段 / sha 不合法）跳过。返回
    `[{sha, parents, subject, paths}]`。
    """
    commits: list[dict] = []
    for chunk in str(output or "").split("\x1e")[1:]:     # 第一块是首个分隔符前的噪声
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        fields = lines[0].split("\x1f")
        if len(fields) < 3:
            continue
        sha = fields[0].strip()
        if len(sha) < 7 or not all(ch in "0123456789abcdefABCDEF" for ch in sha):
            continue
        parents = [item for item in re.split(r"[ \t]+", fields[1].strip()) if item]
        subject = "\x1f".join(fields[2:]).strip()
        commits.append({"sha": sha, "parents": parents, "subject": subject,
                        "paths": lines[1:]})
    return commits


def is_merge(commit: dict) -> bool:
    return len(commit.get("parents") or []) > 1


def is_bib_path(path: str) -> bool:
    return str(path or "").startswith("bib_db/")


def first_non_bib_path(commit: dict) -> str:
    return next((path for path in commit.get("paths") or [] if not is_bib_path(path)), "")


def is_bib_only(commit: dict) -> bool:
    """「只含 bib_db/」：空提交（0 路径）允许；**merge 一律不算**（推 merge 会把
    另一父的整段历史带过去）。"""
    if is_merge(commit):
        return False
    return all(is_bib_path(path) for path in commit.get("paths") or [])


def plan_push(commits: list[dict]) -> dict:
    """推送预检计划（纯逻辑）：从最旧未推送提交起取**最长 bib-only 前缀**。

    返回 `{kind: "nothing"}` / `{kind: "blocked", commit, path}` /
    `{kind: "pushable", tip, count, remainder}`；首条就不满足 → blocked（绝不静默
    带上 notes 的提交）。
    """
    if not commits:
        return {"kind": "nothing"}
    prefix: list[dict] = []
    for commit in commits:
        if not is_bib_only(commit):
            if not prefix:
                return {"kind": "blocked", "commit": commit,
                        "path": first_non_bib_path(commit)}
            return {"kind": "pushable", "tip": prefix[-1], "count": len(prefix),
                    "remainder": commits[len(prefix):]}
        prefix.append(commit)
    return {"kind": "pushable", "tip": prefix[-1], "count": len(prefix), "remainder": []}


def looks_like_proxy_failure(output: str) -> bool:
    """push 失败文本是否像「代理不可用」（本机全局 gitconfig 的失效代理是已知现场）。"""
    text = str(output or "").lower()
    if ("could not resolve proxy" in text or "proxy connect" in text
            or "unsupported proxy" in text):
        return True
    if "failed to connect to" in text:
        return " port " in text or "proxy" in text
    return False


def parse_upstream(output: str) -> tuple[str, str] | None:
    """`origin/main` → `(origin, main)`：首个 `/` 拆开（分支名可含 `/`）。"""
    line = ""
    for raw in str(output or "").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line or line.lower().startswith("fatal:") or "/" not in line:
        return None
    remote, branch = line.split("/", 1)
    remote, branch = remote.strip(), branch.strip()
    return (remote, branch) if remote and branch else None


def _failure_text(output: str, max_lines: int = 3) -> str:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if not lines:
        return "（git 没有输出）"
    marks = ("fatal:", "error:", "rejected", "permission denied", "no such remote",
             "failed to push", "could not read", "does not appear to be a git repository")
    for index, line in enumerate(lines):
        if any(mark in line.lower() for mark in marks):
            return " / ".join(lines[index:index + max_lines])
    return " / ".join(lines[-max_lines:])


def push(db: str = "", *, notes_repo: str = "") -> dict:
    """把「连续 bib-only 前缀」推到 upstream（**只**在用户显式点「推送…」时调用）。

    返回 `{ok, pushed, count, tip, remainder, reason, error, soft}`；绝不 `--force`。
    """
    loc = locate_repo(db or default_db(notes_repo))
    if not loc.get("ok"):
        return {"ok": False, "pushed": False, "soft": True,
                "error": loc.get("error", "无法定位 git 仓库。")}
    git, root = loc["git"], loc["repo_root"]
    probe = _probe(loc)
    blocked = [name for name, flag in (
        ("有未解决的合并冲突", probe.get("conflict")),
        ("merge 进行中", probe.get("merge")),
        ("rebase 进行中", probe.get("rebase"))) if flag]
    if blocked:
        return {"ok": False, "pushed": False, "soft": True,
                "error": "git 仓库" + "、".join(blocked) + "：先处理完 git 状态再推送。"}
    code, out, _err = _run(git, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                 "@{upstream}"], cwd=root)
    upstream = parse_upstream(out) if code == 0 else None
    if not upstream:
        return {"ok": False, "pushed": False, "soft": True,
                "error": "没有 upstream：先 `git push -u` 再推送。"}
    remote, branch = upstream
    code, out, err = _run(git, ["log", "--reverse", "--format=%x1e%H%x1f%P%x1f%s",
                                "--name-only", "@{upstream}..HEAD"], cwd=root)
    if code != 0:
        return {"ok": False, "pushed": False, "soft": False,
                "error": "读取未推送提交失败：" + _error_of(out, err, "未知错误")}
    plan = plan_push(parse_push_log(out))
    if plan["kind"] == "nothing":
        return {"ok": True, "pushed": False, "reason": "没有待推送提交。"}
    if plan["kind"] == "blocked":
        commit = plan["commit"]
        where = plan["path"] or "（merge 提交）"
        return {"ok": False, "pushed": False, "soft": True,
                "error": (f"最旧的未推送提交 {short_sha(commit['sha'])} 含非 bib_db/ 文件"
                          f"（{where}）：整次拒绝（本工具只推仅含 bib_db/ 的连续提交，"
                          "不 force、不推送 notes 的提交）。")}
    tip = plan["tip"]
    count = plan["count"]
    refspec = f"{tip['sha']}:refs/heads/{branch}"
    code, out, err = _run(git, ["push", remote, refspec], cwd=root, timeout=PUSH_TIMEOUT)
    if code != 0 and looks_like_proxy_failure((err or "") + "\n" + (out or "")):
        # 失效代理是已知现场：清掉两个代理键重试一次（只清本次命令，不改配置）。
        code, out, err = _run(git, ["push", remote, refspec], cwd=root,
                              timeout=PUSH_TIMEOUT,
                              extra=["-c", "http.https://github.com.proxy=",
                                     "-c", "http.proxy="])
    if code != 0:
        return {"ok": False, "pushed": False, "soft": False,
                "error": "推送失败：" + _failure_text((err or "") + "\n" + (out or ""))}
    remainder = plan.get("remainder") or []
    return {"ok": True, "pushed": True, "count": count, "tip": tip["sha"],
            "remainder": [short_sha(item["sha"]) for item in remainder],
            "reason": f"已推送 {count} 条提交（tip {short_sha(tip['sha'])}）。"}


# ── 回退（B-190）：checkout 上一个提交 + 新 commit，不 hard reset ────────

def rollback(db: str = "", *, notes_repo: str = "") -> dict:
    """把库文件退回到**上一个版本**并生成一次新 commit。

    「上一个版本」按库文件内容的历史取：先找到当前内容最早出现的那个库提交，
    再取它更旧一个改过库文件的提交（所以内容未变的回退提交会被跳过，连续回退
    会一路向更早版本走，而不是在最近两个内容之间来回切换）。只做 checkout +
    新 commit（**不** hard reset、**不** push）；库文件 dirty、仓库
    merge/rebase/冲突进行中一律拒绝且零写；没有更早版本、或 checkout 后内容
    没变时返回 ok=False / reverted=False，不伪造成功。返回
    `{ok, reverted, message, db_sha256, error, soft}`。
    """
    loc = locate_repo(db or default_db(notes_repo))
    if not loc.get("ok"):
        return {"ok": False, "reverted": False, "soft": True,
                "error": loc.get("error", "无法定位 git 仓库。")}
    git, root, rel = loc["git"], loc["repo_root"], loc["db_rel"]
    probe = _probe(loc)
    if probe.get("conflict") or probe.get("merge") or probe.get("rebase"):
        return {"ok": False, "reverted": False, "soft": True,
                "error": "仓库有未解决的合并冲突或正在 merge/rebase："
                         "先处理再回退。"}
    if probe.get("dirty_db"):
        return {"ok": False, "reverted": False, "soft": True,
                "error": "库文件有未提交改动：回退会覆盖这些改动，"
                         "请先提交或丢弃后再回退。"}
    code, out, err = _run(git, ["rev-list", "HEAD", "--", rel], cwd=root)
    if code != 0:
        return {"ok": False, "reverted": False, "soft": False,
                "error": "读取库提交历史失败：" + _error_of(out, err, "未知错误")}
    history = [line.strip() for line in (out or "").splitlines() if line.strip()]
    if len(history) < 2:
        return {"ok": False, "reverted": False, "soft": True,
                "error": f"库文件只有 {len(history)} 次提交，"
                         "没有可回退的上一个版本。"}
    blobs: list[str] = []
    for sha in history:
        code, blob, blob_err = _run(git, ["rev-parse", f"{sha}:{rel}"], cwd=root)
        if code != 0:
            return {"ok": False, "reverted": False, "soft": False,
                    "error": "读取库文件历史内容失败："
                             + _error_of(blob, blob_err, "未知错误")}
        blobs.append(blob.strip())
    code, head_blob, head_err = _run(git, ["rev-parse", f"HEAD:{rel}"], cwd=root)
    if code != 0:
        return {"ok": False, "reverted": False, "soft": False,
                "error": "读取库文件当前内容失败："
                         + _error_of(head_blob, head_err, "未知错误")}
    current = head_blob.strip()
    older = [i for i, blob in enumerate(blobs) if blob == current]
    if not older:
        return {"ok": False, "reverted": False, "soft": True,
                "error": "当前库内容不在库提交历史里：没有可回退的上一个版本。"}
    # history 新→旧；当前内容「最早出现」的提交是最靠后的同 blob 项，
    # 再往后（更旧）的第一个库提交就是上一个版本。
    target_index = older[-1] + 1
    if target_index >= len(history):
        return {"ok": False, "reverted": False, "soft": True,
                "error": "库内容已是最早可回退的版本，未生成回退提交。"}
    target = history[target_index]
    sha_before = file_sha256(loc["db_abs"])
    code, out, err = _run(git, ["checkout", target, "--", rel], cwd=root,
                          timeout=WRITE_TIMEOUT)
    if code != 0:
        # checkout 失败：索引/工作区退回 HEAD，保证零净写入。
        _run(git, ["checkout", "HEAD", "--", rel], cwd=root, timeout=WRITE_TIMEOUT)
        return {"ok": False, "reverted": False, "soft": False,
                "error": "git checkout 失败（库未改动）："
                         + _error_of(out, err, "未知错误")}
    sha_after = file_sha256(loc["db_abs"])
    if sha_after == sha_before:
        _run(git, ["checkout", "HEAD", "--", rel], cwd=root, timeout=WRITE_TIMEOUT)
        return {"ok": False, "reverted": False, "soft": True,
                "db_sha256": sha_after,
                "error": "库内容没有变化（已是最早可回退的版本），"
                         "未生成回退提交。"}
    message = revert_message(sha_before, sha_after)
    commit = commit_db(loc["db_abs"], message)
    if not commit.get("ok") or not commit.get("committed"):
        # 提交没生成：索引/工作区退回 HEAD，不留「已 checkout 未提交」的半写状态；
        # 库字节因此与回退前一致（零净变化）。
        restored = (_run(git, ["checkout", "HEAD", "--", rel], cwd=root,
                         timeout=WRITE_TIMEOUT)[0] == 0)
        detail = str(commit.get("error") or commit.get("reason") or "未知错误")
        error = "库文件已退回上一个版本，但未生成新提交：" + detail
        if restored:
            error += "（已恢复回退前的库内容，没有留下未提交的半写状态）"
        return {"ok": False, "reverted": False, "message": message,
                "db_sha256": file_sha256(loc["db_abs"]),
                "soft": bool(commit.get("soft", True)), "error": error}
    return {"ok": True, "reverted": True, "message": message, "db_sha256": sha_after,
            "sha": commit.get("sha"), "committed": True}
