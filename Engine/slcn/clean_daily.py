"""日常清理（daily）：**唯一候选器** + 可恢复批次 + 安全恢复。

为什么单独一个模块（B-3xx「清理页」）：
  · 旧 `cleantemp.py` 的四种 mode（aux / delivery / workspace / tocwork）各有一套名单，
    外壳要用户先选「删哪一档」。清理页改为**一个日常入口**：范围判定全部收进 Engine，
    壳只传目录、显示结果、提供撤销 —— 界面与 CLI 不可能再各写一套判据。
  · 日常范围 = 可识别、可再生、且**不被成品引用**的生成残留：
    编译辅助文件、经证明的编译日志、`.toolbox-tmp/` 内容、根级时间戳中英目录 tex、
    根级 `目录结果.json`、旧 `信息检查*` 报告组（最新一组整组保留）。
  · 判据的总纲是**保守**：无法证明可再生的（未知 .log / 无文献来源的 .bbl / 自定义 .out /
    解析不了的引用 / 符号链接 / 备份与历史稿）一律**保留并给原因**，绝不为凑数量放宽。

可恢复性（C09/C10）：
  · 一个 `operationId` 一个批次：`<root>/.toolbox-trash/<basename>-<YYYY_MM_DD_HHMMSS>-<8hex>/`，
    同秒连续清理也不会撞名/覆盖；
  · **先把可恢复清单/意图原子落盘并 fsync，再搬第一个文件**：每条搬移前把该条置
    `moving` 落盘，搬完置 `moved`；清单写入失败就不搬（启动时）或立即中止（中途）；
  · 中断（SIGTERM / KeyboardInterrupt）时：扫描期零写入；搬移期已搬条目仍有清单，可撤销。
  · `restore_daily()` 校验 root 与批次归属、逐条拒绝绝对路径/`..`/越界链接，目标已存在**不覆盖**；
    恢复前**整批只读预检**：版本闸（不认识的 schema 整批拒绝）→ 路径边界 → 内容摘要闸
    （schema=3 逐文件核对 sha256，不符零搬运）；旧 v1/v2 允许恢复但打 RESTORE_UNVERIFIED
    告警（无内容摘要可校验）；`--dry-run` 同样完整预检。

对外接口（GUI 依赖，冻结版见运行期 EVID/INTERFACE-DAILY.md）：
  · `scan_daily(root)`          只读候选器 → {root, candidates, kept, errors, ok}
  · `run_daily(root, ...)`      执行/预演 → 与 CLI JSON 同构的 dict
  · `restore_daily(root, trash)` 安全撤销 → 与 CLI JSON 同构的 dict
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import signal
import stat as _stat
import threading
import uuid
from collections.abc import Callable

from . import clean as _clean
from . import paths as P

# ── 常量（判据单点；RULES.md 与本文件同步）────────────────────────────────

TRASH_DIR = _clean.TRASH_DIR
TOC_WORK_DIR = ".toolbox-tmp"

#: 编译辅助后缀（统一小写比较）。`.log/.bbl/.out` 不在表里：它们要过「可再生证明」。
AUX_SUFFIXES = (
    ".aux",
    ".bcf",
    ".blg",
    ".run.xml",
    ".synctex.gz",
    ".fls",
    ".fdb_latexmk",
    ".toc",
    ".lof",
    ".lot",
    ".nav",
    ".snm",
    ".vrb",
    ".bbl-save-error",
    ".ds_store",
)

#: 「备份/历史/待核」目录名片段（整棵不遍历、整棵保留）
_PROTECTED_DIR_MARKERS = ("backup", "history", "历史", "待核", "备份")

#: 无论出现在哪都不进入的目录（回收区另判）
_ALWAYS_SKIP_DIRS = frozenset({".git", ".svn", ".hg", "__pycache__"})

#: 根级「中英目录时间戳 tex」与它的备份变体（时间戳格式由 toc 生成器决定）
TIMESTAMP_TEX_RE = re.compile(
    r"^(?:Chinese Content|contents)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tex$"
)
TIMESTAMP_TEX_BAK_RE = re.compile(
    r"^(?:Chinese Content|contents)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tex\.bak$"
)

#: `信息检查<stamp>(_摘要)?.txt|json|md` —— 同 stamp 一条三件组
INFO_CHECK_RE = re.compile(
    r"^信息检查(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})(?:_摘要)?\.(?:txt|json|md)$"
)
TOC_RESULT_NAMES = ("目录结果.json",)

#: TeX 引擎日志横幅（编译 .log 的第一行附近）
_TEX_LOG_BANNERS = (b"This is XeTeX", b"This is pdfTeX", b"This is LuaTeX", b"This is e-TeX")

CLOSURE_MAX_DEPTH = 20
_GRAPHICS_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")

_TEX_INPUT_RE = re.compile(r"\\(?:input|include)(?![A-Za-z])\s*(?:\{([^{}]*)\}|\"([^\"]*)\"|([^\s{}%\\]+))")
_TEX_GRAPHICS_RE = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_TEX_ADDBIB_RE = re.compile(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_TEX_BIBLIO_RE = re.compile(r"\\bibliography\s*\{([^{}]*)\}")
_TEX_GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\{[^{}]*\})+)\}")
_TEX_PATH_ITEM_RE = re.compile(r"\{([^{}]*)\}")

_STATUS_OK = ("success", "empty")
_STATUS_ALL = ("success", "empty", "partial", "preflight_failed", "cancelled")

# ── 停止信号（C09）：GUI 的「停止」= 对进程组发 SIGTERM ──────────────────


class _Cancelled(Exception):
    """内部停止哨兵：只在 daily 扫描/搬移循环里抛，由 run/restore 转成 status=cancelled。"""


_STOP = {"requested": False}


def request_stop() -> None:
    """测试/嵌入方主动请求停止（与 SIGTERM 同一路径）。"""
    _STOP["requested"] = True


def _reset_stop() -> None:
    _STOP["requested"] = False


def _check_stop() -> None:
    if _STOP["requested"]:
        raise _Cancelled()


class _StopGuard:
    """临时安装 SIGTERM/SIGINT handler（只置标志，不立即 die），退出时还原。"""

    def __init__(self) -> None:
        self._old: dict[int, object] = {}

    def __enter__(self) -> "_StopGuard":
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    self._old[sig] = signal.signal(sig, self._handler)
                except (ValueError, OSError):
                    pass
        return self

    @staticmethod
    def _handler(_signum, _frame) -> None:  # noqa: ANN001
        request_stop()

    def __exit__(self, *_exc) -> None:
        for sig, old in self._old.items():
            try:
                signal.signal(sig, old)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass


# ── 路径小工具 ───────────────────────────────────────────────────────────


def _abs(root: str, rel: str) -> str:
    """POSIX 相对路径 → 绝对路径（跨平台：Windows 也接受 "/"）。"""
    return os.path.normpath(os.path.join(root, rel))


def _rel(root: str, full: str) -> str:
    """绝对路径 → 相对 root 的 **POSIX** 路径（manifest 与 JSON 统一用 "/"）。"""
    rel = P.rel(full, root)
    return rel.replace(os.sep, "/")


def _inside(parent_real: str, child: str) -> bool:
    """child（realpath）是否在 parent_real 之内（含自身）。"""
    try:
        return os.path.commonpath([parent_real, child]) == parent_real
    except ValueError:  # 跨盘符
        return False


def _protected_dir_name(name: str) -> bool:
    low = name.lower()
    if any(m in name for m in _PROTECTED_DIR_MARKERS) or any(m in low for m in ("backup", "history")):
        return True
    return low.endswith(("_bak", "-bak", ".bak"))


def _looks_backup_file(name: str) -> bool:
    low = name.lower()
    if low.endswith((".bak", ".backup", ".orig")):
        return True
    if any(m in name for m in ("备份", "历史", "待核")):
        return True
    if "backup" in low:
        return True
    return low.endswith(("_bak", "-bak"))


def _strip_tex_comments(text: str) -> str:
    """去掉 `%` 注释（`\\%` 转义不算），避免注释掉的引用被当成缺图。"""
    out = []
    for line in text.splitlines():
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == "%" and (i == 0 or line[i - 1] != "\\"):
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def _same_stem_tex(info: dict, suffix: str) -> bool:
    stem = info["name"][: -len(suffix)]
    cand = os.path.join(os.path.dirname(info["full"]), stem + ".tex")
    return os.path.isfile(cand) and not os.path.islink(cand)


# ── 引用闭包（保护面优先，C04/C07）──────────────────────────────────────


class _RefScanner:
    """读全部「源 tex」，解析引用闭包；解析不到/越界/动态 → errors（预检失败、零写入）。"""

    def __init__(self, root: str, errors: list[dict]) -> None:
        self.root = root
        self.root_real = os.path.realpath(root)
        self.errors = errors
        self.refs: set[str] = set()
        self._texts: dict[str, str | None] = {}
        self._seen_errors: set[tuple[str, str]] = set()
        self._visited: set[str] = set()

    # -- 文本读取 ------------------------------------------------------
    def text(self, full: str) -> str | None:
        key = os.path.normpath(full)
        if key in self._texts:
            return self._texts[key]
        try:
            with open(key, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            self._texts[key] = None
            return None
        value = _strip_tex_comments(raw)
        self._texts[key] = value
        return value

    def _error(self, path: str, message: str) -> None:
        key = (path, message)
        if key not in self._seen_errors:
            self._seen_errors.add(key)
            self.errors.append({"path": path, "message": message})

    # -- 单条引用解析 --------------------------------------------------
    @staticmethod
    def _split_bases(bases: list[str]) -> list[str]:
        out = []
        for b in bases:
            if b and b not in out:
                out.append(b)
        return out

    def resolve(self, raw: str, bases: list[str], exts: tuple[str, ...]) -> list[str]:
        """成功 → 相对 POSIX 路径列表（同名多来源全部保护）；失败 → 记 error 并返回 []。"""
        p = raw.strip().strip('"')
        if not p:
            return []
        if any(ch in p for ch in ("\\", "{", "}", "#", "$")):
            self._error(p, f"无法解析的引用（动态路径）：{p}")
            return []
        if os.path.isabs(p) or re.match(r"^[A-Za-z]:[\\/]", p):
            self._error(p, f"绝对路径引用（不安全）：{p}")
            return []
        cands = [p] if os.path.splitext(p)[1] else [p + e for e in exts]
        found: list[str] = []
        escaped = False
        for base in self._split_bases(bases):
            for c in cands:
                full = os.path.normpath(os.path.join(base, c))
                real = os.path.realpath(full)
                if not _inside(self.root_real, real):
                    # R04：根内别名指向根外——只保护别名本身，不跟随、不递归。
                    root_abs = os.path.abspath(self.root)
                    full_abs = os.path.abspath(full)
                    if _inside(root_abs, full_abs) and os.path.lexists(full_abs):
                        rel = P.rel(full_abs, root_abs).replace(os.sep, "/")
                        if rel not in found:
                            found.append(rel)
                    else:
                        escaped = True
                    continue
                if not os.path.isfile(real) and not os.path.isfile(full):
                    continue
                for target in (full, real):
                    rel = P.rel(target, self.root_real).replace(os.sep, "/")
                    if rel.startswith("../"):
                        continue
                    if rel not in found:
                        found.append(rel)
        if found:
            return found
        if escaped:
            self._error(p, f"引用越界（不在期目录内）：{p}")
        else:
            self._error(p, f"引用缺失：{p}")
        return []

    # -- 递归解析一个 tex ----------------------------------------------
    def scan_tex(self, info: dict, depth: int = 0) -> None:
        rel = info["rel"]
        if rel in self._visited:
            return
        self._visited.add(rel)
        text = self.text(info["full"])
        if text is None:
            self._error(rel, "无法读取 .tex（引用闭包无法安全判定）")
            return
        tex_dir = os.path.dirname(os.path.abspath(info["full"]))
        # R03：按真实编译 cwd 解析（文章目录优先）；同名多来源全部保护，不猜错后删除。
        bases = [tex_dir, self.root]
        gbases = list(bases)
        for m in _TEX_GRAPHICSPATH_RE.finditer(text):
            for pm in _TEX_PATH_ITEM_RE.finditer(m.group(1)):
                raw = pm.group(1).strip()
                if not raw:
                    continue
                for base in (tex_dir, self.root):
                    full = os.path.normpath(os.path.join(base, raw))
                    if os.path.isdir(full):
                        gbases.append(full)
        for m in _TEX_GRAPHICS_RE.finditer(text):
            for got in self.resolve(m.group(1), gbases, _GRAPHICS_EXTS):
                self.refs.add(got)
        for m in _TEX_INPUT_RE.finditer(text):
            # R02：同时接受 \input{file}、\input "file" 与合法的 \input file。
            raw = next((g for g in m.groups() if g is not None), "")
            for got in self.resolve(raw, bases, (".tex",)):
                self.refs.add(got)
                if not got.lower().endswith(".tex"):
                    continue  # 显式 input 一个 .bbl/.out：保护即可，不递归
                if not _inside(self.root_real, os.path.realpath(_abs(self.root, got))):
                    continue  # 别名指向根外（外链）：保护别名，不读目标、不递归
                if depth + 1 >= CLOSURE_MAX_DEPTH:
                    self._error(got, "引用嵌套过深（无法安全判定）")
                    continue
                self.scan_tex(
                    {"rel": got, "full": _abs(self.root, got), "name": os.path.basename(got)},
                    depth + 1,
                )
        for m in _TEX_ADDBIB_RE.finditer(text):
            for got in self.resolve(m.group(1), bases, (".bib",)):
                self.refs.add(got)
        for m in _TEX_BIBLIO_RE.finditer(text):
            for part in m.group(1).split(","):
                if not part.strip():
                    continue
                for got in self.resolve(part, bases, (".bib",)):
                    self.refs.add(got)


# ── 扫描（只读）──────────────────────────────────────────────────────────


def scan_daily(root: str) -> dict:
    """**只读**候选器：返回 {root, candidates, kept, errors, ok}（不写任何文件）。

    这是 daily 范围的**唯一**定义；`run_daily` / CLI / GUI 都只消费它。
    """
    root = os.path.abspath(os.path.expanduser(root))
    return _scan(root)["public"]


def _scan(root: str, stop_check: Callable[[], None] | None = None) -> dict:
    stop = stop_check or (lambda: None)
    public: dict = {"root": root, "candidates": [], "kept": [], "errors": [], "ok": True}
    meta: dict = {"refs": set(), "fp": {}, "paths": set()}
    if not os.path.isdir(root):
        public["errors"].append({"path": root, "message": "路径不是文件夹"})
        public["ok"] = False
        return {"public": public, "meta": meta}

    files: list[dict] = []
    ref_infos: list[dict] = []
    kept: list[dict] = [{"path": "", "kind": "", "reason": ""}][:0]
    errors: list[dict] = []
    cands: dict[str, dict] = {}
    info_groups: dict[str, list[dict]] = {}

    def add_kept(path: str, kind: str, reason: str) -> None:
        kept.append({"path": path, "kind": kind, "reason": reason})

    def walk(abs_dir: str, rel_dir: str, in_tmp: bool) -> None:
        stop()
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: e.name.lower())
        except OSError as exc:
            errors.append({
                "path": rel_dir or ".",
                "message": f"无法读取目录：{exc.strerror or exc}",
            })
            return
        for ent in entries:
            name = ent.name
            rel = name if rel_dir == "" else f"{rel_dir}/{name}"
            try:
                is_link = ent.is_symlink()
                is_dir = ent.is_dir(follow_symlinks=False)
            except OSError:
                add_kept(rel, "unrecognized", "无法读取属性（保守保留）")
                continue
            if is_link:
                add_kept(rel, "symlink", "符号链接（不跟随、不回收）")
                continue
            if is_dir:
                low = name.lower()
                if low in _ALWAYS_SKIP_DIRS or name == TRASH_DIR:
                    continue
                if low in {d.lower() for d in _clean.SKIP_DIRS}:
                    continue  # 作者来稿目录：不遍历
                if _protected_dir_name(name):
                    add_kept(rel, "dir", "备份/历史/待核目录（整棵保留）")
                    continue
                if rel_dir == "" and name == TOC_WORK_DIR:
                    walk(ent.path, rel, True)
                else:
                    walk(ent.path, rel, in_tmp)
                continue
            try:
                st = ent.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append({"path": rel, "message": f"无法读取文件属性：{exc.strerror or exc}"})
                continue
            if not _stat.S_ISREG(st.st_mode):
                add_kept(rel, "unrecognized", "非普通文件（保守保留）")
                continue
            info = {
                "rel": rel,
                "full": ent.path,
                "name": name,
                "rel_dir": rel_dir,
                "in_tmp": in_tmp,
                "st": st,
            }
            files.append(info)
            if not in_tmp and low_ends_tex(name):
                if not (rel_dir == "" and TIMESTAMP_TEX_RE.match(name)):
                    ref_infos.append(info)

    def low_ends_tex(name: str) -> bool:
        return name.lower().endswith(".tex")

    walk(root, "", False)

    # 1) 引用闭包（先算，因为「被引用」压过一切后缀判据）
    ref_scanner = _RefScanner(root, errors)
    for info in ref_infos:
        try:
            ref_scanner.scan_tex(info)
        except _Cancelled:
            raise
    refs = ref_scanner.refs
    texts = ref_scanner._texts

    # 2) 逐个文件分类
    for info in files:
        name = info["name"]
        rel = info["rel"]
        low = name.lower()
        # R05：备份/历史判据优先于 .toolbox-tmp —— tmp 里的 .bak 同样是不可证明可再生的备份。
        if _looks_backup_file(name):
            add_kept(rel, "backup", "备份/历史稿命名（保留）")
            continue
        if info["in_tmp"]:
            cands[rel] = {"path": rel, "kind": "tmp", "reason": "工具临时工作目录残留"}
            continue
        if info["rel_dir"] == "" and name in TOC_RESULT_NAMES:
            cands[rel] = {"path": rel, "kind": "toc-result", "reason": "目录生成结果（可重新生成）"}
            continue
        if info["rel_dir"] == "" and TIMESTAMP_TEX_RE.match(name):
            cands[rel] = {
                "path": rel,
                "kind": "timestamp-tex",
                "reason": "根级时间戳中英目录 tex（生成残留）",
            }
            continue
        if info["rel_dir"] == "" and TIMESTAMP_TEX_BAK_RE.match(name):
            add_kept(rel, "backup", "时间戳 tex 的备份变体（保留）")
            continue
        m = INFO_CHECK_RE.match(name)
        if info["rel_dir"] == "" and m:
            info_groups.setdefault(m.group(1), []).append(info)
            continue
        if name.startswith("信息检查") and low.endswith((".txt", ".json", ".md")):
            add_kept(rel, "info-report", "非标准命名的检查报告（保留）")
            continue
        if low.endswith(AUX_SUFFIXES):
            cands[rel] = {"path": rel, "kind": "aux", "reason": "编译辅助文件（可重新编译生成）"}
            continue
        if low.endswith(".log"):
            banner = _read_log_banner(info)
            if banner is None:
                add_kept(rel, "log", "无法读取日志（保守保留）")
            elif not banner:
                add_kept(rel, "log", "无法证明是编译日志（保留）")
            elif not _same_stem_tex(info, ".log"):
                add_kept(rel, "log", "编译日志缺少同名 .tex（无法证明可再生，保留）")
            else:
                cands[rel] = {"path": rel, "kind": "log", "reason": "编译日志（可重新编译生成）"}
            continue
        if low.endswith(".bbl"):
            bib = _has_bib_source(info, texts)
            if bib is None:
                add_kept(rel, "aux", "无法读取文献来源（保守保留）")
            elif bib:
                cands[rel] = {"path": rel, "kind": "aux", "reason": "文献编译产物（有文献来源）"}
            else:
                add_kept(rel, "aux", "仅有 .bbl 且缺文献来源（无法证明可再生，保留）")
            continue
        if low.endswith(".out"):
            if _same_stem_tex(info, ".out"):
                cands[rel] = {"path": rel, "kind": "aux", "reason": "编译辅助文件（可重新编译生成）"}
            else:
                add_kept(rel, "aux", "自定义 .out（无同名 .tex，保留）")
            continue

    # 3) 信息检查报告组：最新一组整组保留，旧组回收
    if info_groups:
        latest = max(info_groups)
        for stamp, items in info_groups.items():
            if stamp == latest:
                for info in items:
                    add_kept(info["rel"], "info-report", "最新一组信息检查报告（保留）")
            else:
                for info in items:
                    cands[info["rel"]] = {
                        "path": info["rel"],
                        "kind": "info-report",
                        "reason": "旧信息检查报告组（最新一组已保留）",
                    }

    # 4) 引用保护压过后缀：被 TeX 直接/间接引用的候选一律保留
    for rel in list(cands):
        if rel in refs:
            cand = cands.pop(rel)
            add_kept(rel, "referenced", f"被 TeX 引用（保护）：{cand['reason']}")

    # 4b) 已因其它判据保留、但同时被引用的文件：保护理由升级为 referenced
    #     （保留原判据文字，GUI 可据此解释「为什么没回收」）。
    for item in kept:
        if (
            item["path"] in refs
            and item["kind"] not in ("dir", "symlink", "referenced")
            and not item["reason"].startswith("被 TeX 引用（保护）：")
        ):
            item["reason"] = f"被 TeX 引用（保护）：{item['reason']}"
            item["kind"] = "referenced"

    # 5) 指纹（执行前复核候选身份用；不暴露给 GUI）
    for cand in cands.values():
        info = next((f for f in files if f["rel"] == cand["path"]), None)
        if info is not None:
            st = info["st"]
            meta["fp"][cand["path"]] = (st.st_size, st.st_mtime_ns, info["st"].st_ino)
    meta["refs"] = refs

    public["candidates"] = sorted(cands.values(), key=lambda c: c["path"])
    # kept 去重 + 稳定排序
    seen: set[tuple[str, str, str]] = set()
    uniq = []
    for item in kept:
        key = (item["path"], item["kind"], item["reason"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    public["kept"] = sorted(uniq, key=lambda c: (c["path"], c["kind"]))
    public["errors"] = errors
    public["ok"] = not errors
    meta["paths"] = {c["path"] for c in public["candidates"]}
    return {"public": public, "meta": meta}


def _read_log_banner(info: dict) -> bool | None:
    """True=头部是 TeX 引擎横幅；False=不是；None=读不了（保守保留）。"""
    try:
        with open(info["full"], "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    return any(b in head for b in _TEX_LOG_BANNERS)


def _has_bib_source(info: dict, texts: dict) -> bool | None:
    """同目录有 .bib，或同目录某 .tex 声明了 \\addbibresource/\\bibliography → True。"""
    d = os.path.dirname(info["full"])
    try:
        names = os.listdir(d)
    except OSError:
        return None
    if any(n.lower().endswith(".bib") for n in names):
        return True
    for name in names:
        if not name.lower().endswith(".tex"):
            continue
        full = os.path.normpath(os.path.join(d, name))
        text = texts.get(full)
        if text is None:
            text = _read_text_quiet(full)
        if text and (_TEX_ADDBIB_RE.search(text) or _TEX_BIBLIO_RE.search(text)):
            return True
    return False


def _read_text_quiet(full: str) -> str | None:
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return _strip_tex_comments(fh.read())
    except OSError:
        return None


# ── 结果字典 ─────────────────────────────────────────────────────────────


def _mk_result(
    root: str,
    *,
    status: str,
    count: int = 0,
    paths: list[str] | None = None,
    action: str = "move",
    dry_run: bool = False,
    trash: str = "",
    restore: str = "",
    operation_id: str = "",
    failures: list[dict] | None = None,
    warnings: list[str] | None = None,
    error: str = "",
    kept: list[dict] | None = None,
    entries: list[dict] | None = None,
    already_restored: int = 0,
    skipped: list[dict] | None = None,
    summary: dict | None = None,
) -> dict:
    paths = paths or []
    if entries is None:
        items = [{"path": p, "action": action} for p in paths]
    else:
        items = [dict(e) for e in entries]
    result = {
        "ok": status in _STATUS_OK,
        "command": "cleantemp",
        "mode": "daily" if action == "move" else "restore",
        "dryRun": bool(dry_run),
        "root": root,
        "count": count,
        "entryCount": len(items),
        "entries": items,
        "trashDir": trash,
        "restoreDir": restore,
        "failures": failures or [],
        "warnings": warnings or [],
        "error": error,
        "operationId": operation_id,
        "status": status,
        "kept": kept or [],
    }
    if action == "restore":
        # R08：撤销账本对外字段（INTERFACE-REWORK 冻结）。
        result["alreadyRestored"] = already_restored
        result["skipped"] = skipped or []
        result["summary"] = summary or {
            "restored": count,
            "alreadyRestored": already_restored,
            "skipped": len(skipped or []),
            "failed": len(failures or []),
        }
    return result


def _failures_from_errors(errors: list[dict]) -> list[dict]:
    return [{"path": str(e.get("path") or ""), "error": str(e.get("message") or "")} for e in errors]


# ── 批次 / 清单 ──────────────────────────────────────────────────────────


class _ManifestWriteError(OSError):
    pass


def _save_manifest(trash_root: str, data: dict) -> None:
    """原子写 manifest + fsync（先意图后搬移的关键）。失败抛 _ManifestWriteError。"""
    path = os.path.join(trash_root, "manifest.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(trash_root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except OSError as exc:
        raise _ManifestWriteError(str(exc)) from exc


def _rmtree_quiet(path: str) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _ensure_trash_parent(path: str) -> list[str]:
    """创建回收区目标父目录，返回本次新建的目录（由浅到深）。"""
    missing: list[str] = []
    cur = path
    while cur and not os.path.isdir(cur):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    created: list[str] = []
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            continue
        except OSError:
            for made in reversed(created):
                try:
                    os.rmdir(made)
                except OSError:
                    pass
            raise
        created.append(d)
    return created


def _discard_trash_copy(dst: str, created: list[str], dst_existed: bool) -> None:
    """失败回滚：删除刚复制进回收区的副本（及本次新建的空父目录）。"""
    if not dst_existed and os.path.lexists(dst):
        try:
            os.unlink(dst)
        except OSError:
            pass
    for d in reversed(created):
        try:
            os.rmdir(d)
        except OSError:
            pass


def _move_into_trash(src: str, dst: str) -> None:
    """把 src 搬进回收区 dst；任何失败都不在回收区留下未入账文件。

    优先 os.rename（同盘、原子）。rename 不可用时回退 copy2 + unlink；
    若 unlink 失败（如源目录只读不可写），先删除刚复制到 dst 的副本和本次
    新建的空父目录再抛错 —— 保证失败项不会作为孤儿副本留在批次 payload 里。
    """
    parent = os.path.dirname(dst)
    created = _ensure_trash_parent(parent) if parent else []
    dst_existed = os.path.lexists(dst)
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass  # 跨设备/rename 被拒：回退 copy + unlink
    try:
        shutil.copy2(src, dst)
    except OSError:
        _discard_trash_copy(dst, created, dst_existed)
        raise
    try:
        os.unlink(src)
    except OSError as exc:
        _discard_trash_copy(dst, created, dst_existed)
        code = exc.errno if exc.errno is not None else 0
        raise OSError(
            code, f"已复制到回收区但无法删除源文件（已回滚副本）：{exc}"
        ) from exc


def _same_root(a: str, b: str) -> bool:
    """root 比较用真实路径：同一目录经符号链接/相对路径传入时不误判为「不属于」。"""
    try:
        ra = os.path.realpath(os.path.abspath(os.path.expanduser(a)))
        rb = os.path.realpath(os.path.abspath(os.path.expanduser(b)))
    except OSError:
        return False
    return ra == rb


def _trash_parent_problem(root: str) -> str:
    """R01：清理执行前校验 <root>/.toolbox-trash 本身可信（拒绝符号链接/越界）。"""
    parent = os.path.join(root, TRASH_DIR)
    if not os.path.lexists(parent):
        return ""
    if os.path.islink(parent):
        return f"回收目录 {TRASH_DIR} 是符号链接（拒绝跟随）"
    if not os.path.isdir(parent):
        return f"回收目录 {TRASH_DIR} 不是文件夹（拒绝使用）"
    if not _inside(os.path.realpath(root), os.path.realpath(parent)):
        return f"回收目录 {TRASH_DIR} 的真实路径不在期目录内（拒绝使用）"
    return ""


def _batch_problem(root: str, trash_root: str) -> str:
    """R01/R06/R07：恢复前校验批次归属与边界；返回空串=通过。"""
    if os.path.islink(trash_root):
        return "回收批次是符号链接（拒绝跟随）"
    if not os.path.isdir(trash_root):
        return f"回收目录不存在：{trash_root}"
    parent = os.path.dirname(os.path.normpath(trash_root))
    expected = os.path.normpath(os.path.join(root, TRASH_DIR))
    if parent != expected:
        return f"回收批次不在 {os.path.join(root, TRASH_DIR)} 下（拒绝恢复）"
    if os.path.islink(parent):
        return f"回收目录 {TRASH_DIR} 是符号链接（拒绝恢复）"
    root_real = os.path.realpath(root)
    parent_real = os.path.realpath(parent)
    if not _inside(root_real, parent_real):
        return f"回收目录 {TRASH_DIR} 的真实路径不在期目录内（拒绝恢复）"
    trash_real = os.path.realpath(trash_root)
    if trash_real == parent_real or not _inside(parent_real, trash_real):
        return f"回收批次的真实路径不在 {TRASH_DIR} 内（拒绝恢复）"
    return ""


def _new_operation_id() -> str:
    stamp = _dt.datetime.now().strftime("%Y_%m_%d_%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _new_batch_dir(root: str, operation_id: str) -> str:
    parent = os.path.join(root, TRASH_DIR)
    os.makedirs(parent, exist_ok=True)
    if not _inside(os.path.realpath(root), os.path.realpath(parent)):
        raise OSError(f"回收目录 {TRASH_DIR} 的真实路径不在期目录内")
    base = os.path.basename(os.path.abspath(root)) or "root"
    cand = os.path.join(parent, f"{base}-{operation_id}")
    n = 1
    while os.path.exists(cand):
        n += 1
        cand = os.path.join(parent, f"{base}-{operation_id}-{n}")
    return cand


def read_manifest(trash_root: str) -> dict | None:
    """读回收清单；读不到/坏 JSON 返回 None（CLI 据此回退旧兼容路径）。"""
    path = os.path.join(os.path.abspath(os.path.expanduser(trash_root)), "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── 执行（run_daily）─────────────────────────────────────────────────────


def run_daily(
    root: str,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    """日常清理：预检 → 先持久化清单/意图 → 逐条搬移；返回与 CLI JSON 同构的 dict。"""
    emit = log or (lambda _s: None)
    root = os.path.abspath(os.path.expanduser(root))
    with _StopGuard():
        _reset_stop()
        return _run_daily_inner(root, dry_run=dry_run, emit=emit)


def _run_daily_inner(root: str, *, dry_run: bool, emit: Callable[[str], None]) -> dict:
    # R01：先验证回收区自身可信；不通过则零写入。
    problem = _trash_parent_problem(root)
    if problem:
        return _mk_result(
            root,
            status="preflight_failed",
            dry_run=dry_run,
            failures=[{"path": os.path.join(root, TRASH_DIR), "error": problem}],
            error=f"预检未通过（未写入任何文件）：{problem}",
        )
    try:
        scanned = _scan(root, stop_check=_check_stop)
    except _Cancelled:
        msg = "已停止：扫描已取消，未写入任何文件"
        return _mk_result(root, status="cancelled", dry_run=dry_run, warnings=[msg], error=msg)

    public = scanned["public"]
    meta = scanned["meta"]
    kept = public["kept"]

    if public["errors"]:
        failures = _failures_from_errors(public["errors"])
        msg = f"预检未通过（未写入任何文件）：{len(failures)} 项扫描/引用错误"
        return _mk_result(
            root,
            status="preflight_failed",
            dry_run=dry_run,
            failures=failures,
            kept=kept,
            error=msg,
        )

    candidates = public["candidates"]
    if not candidates:
        return _mk_result(root, status="empty", dry_run=dry_run, kept=kept)

    if dry_run:
        for cand in candidates:
            emit(f"Will move: {cand['path']}")
        return _mk_result(
            root,
            status="success",
            count=len(candidates),
            paths=[c["path"] for c in candidates],
            dry_run=True,
            kept=kept,
        )

    # 执行前复核候选身份（冻结根；文件变化/消失 → 保留，绝不盲搬）
    ready: list[dict] = []
    extra_kept: list[dict] = list(kept)
    warnings: list[str] = []
    for cand in candidates:
        try:
            _check_stop()
        except _Cancelled:
            msg = "已停止：扫描已取消，未写入任何文件"
            return _mk_result(root, status="cancelled", dry_run=False, kept=extra_kept,
                              warnings=[msg], error=msg)
        fp = meta["fp"].get(cand["path"])
        full = _abs(root, cand["path"])
        try:
            st = os.lstat(full)
        except OSError:
            warnings.append(f"候选已消失：{cand['path']}")
            continue
        if not _stat.S_ISREG(st.st_mode) or os.path.islink(full):
            warnings.append(f"候选已不是普通文件：{cand['path']}（保留）")
            extra_kept.append({"path": cand["path"], "kind": cand["kind"],
                               "reason": "执行前复核：已不是普通文件（保留）"})
            continue
        if fp is not None and (st.st_size, st.st_mtime_ns, st.st_ino) != fp:
            warnings.append(f"候选已变化：{cand['path']}（保留）")
            extra_kept.append({"path": cand["path"], "kind": cand["kind"],
                               "reason": "执行前复核：文件已变化（保留）"})
            continue
        ready.append(cand)

    if not ready:
        return _mk_result(root, status="empty", kept=extra_kept, warnings=warnings)

    operation_id = _new_operation_id()
    try:
        trash_root = _new_batch_dir(root, operation_id)
        os.makedirs(trash_root, exist_ok=True)
        if not _inside(os.path.realpath(root), os.path.realpath(trash_root)):
            try:
                os.rmdir(trash_root)  # 刚建的空目录才删；绝不递归删链接目标
            except OSError:
                pass
            raise OSError("回收批次真实路径不在期目录内")
    except OSError as exc:
        failures = [{"path": os.path.join(root, TRASH_DIR), "error": f"无法创建回收批次：{exc}"}]
        msg = "预检未通过（未写入任何文件）：无法创建回收批次"
        return _mk_result(root, status="preflight_failed", failures=failures,
                          warnings=warnings, error=msg, kept=extra_kept)

    # 意图清单（**先落盘再搬第一个文件**）
    intent: list[dict] = [{"path": c["path"], "type": "file", "status": "planned"} for c in ready]
    manifest: dict = {
        "schema": 3,
        "root": root,
        "createdAt": operation_id.rsplit("-", 1)[0],
        "mode": "daily",
        "kind": "daily",
        "operationId": operation_id,
        "status": "planned",
        "entries": intent,
        "intent": intent,
    }
    try:
        _save_manifest(trash_root, manifest)
    except OSError as exc:
        _rmtree_quiet(trash_root)
        failures = [{"path": trash_root, "error": f"无法写入回收清单：{exc}"}]
        msg = "预检未通过（未写入任何文件）：无法写入可恢复清单"
        return _mk_result(root, status="preflight_failed", failures=failures,
                          warnings=warnings, error=msg, kept=extra_kept)

    moved: list[str] = []
    failures = []
    cancelled = False
    abort_error = ""
    try:
        for cand in ready:
            _check_stop()
            rel = cand["path"]
            entry = next(e for e in intent if e["path"] == rel)
            full = _abs(root, rel)
            # 复核（清单写入与真正搬移之间还有时间窗）
            try:
                st = os.lstat(full)
            except OSError as exc:
                entry["status"] = "failed"
                failures.append({"path": rel, "error": f"搬移前复核失败：{exc}"})
                continue
            if not _stat.S_ISREG(st.st_mode) or os.path.islink(full):
                entry["status"] = "failed"
                failures.append({"path": rel, "error": "搬移前复核：已不是普通文件"})
                continue
            try:
                # C1.2：搬移前先算内容 sha256，随 moving 状态一起原子落盘，再 move。
                entry["sha256"] = _clean._sha256_file(full)
            except OSError as exc:
                entry["status"] = "failed"
                failures.append({"path": rel, "error": f"搬移前计算内容摘要失败：{exc}"})
                emit(f"Error moving {rel}: {exc}")
                continue
            entry["status"] = "moving"
            try:
                _save_manifest(trash_root, manifest)
            except OSError as exc:
                abort_error = f"回收清单写入失败：{exc}"
                raise
            dst = os.path.join(trash_root, rel)
            try:
                _move_into_trash(full, dst)
            except OSError as exc:
                entry["status"] = "failed"
                failures.append({"path": rel, "error": str(exc)})
                emit(f"Error moving {rel}: {exc}")
                try:
                    _save_manifest(trash_root, manifest)
                except OSError as exc2:
                    abort_error = f"回收清单写入失败：{exc2}"
                    break
                continue
            entry["status"] = "moved"
            moved.append(rel)
            emit(f"Moved: {rel}")
            try:
                _save_manifest(trash_root, manifest)
            except OSError as exc:
                # 文件已搬走，但清单最后落盘状态是 moving —— 仍可恢复；立即中止。
                abort_error = f"回收清单写入失败：{exc}"
                failures.append({"path": rel, "error": "已搬移但清单状态写入失败（仍可恢复）"})
                break
    except _Cancelled:
        cancelled = True
    except KeyboardInterrupt:
        cancelled = True
    except _ManifestWriteError:
        cancelled = False  # abort_error 已记录

    # 收尾清单：entries 只留已搬（GUI 的撤销按钮/清单计数都只看 entries）
    final_status = "cancelled" if cancelled else ("partial" if (failures or abort_error) else "success")
    manifest["status"] = final_status
    manifest["entries"] = [e for e in intent if e["status"] == "moved"]
    try:
        _save_manifest(trash_root, manifest)
    except OSError:
        warnings.append("最终回收清单写入失败；已搬条目仍可按上一次落盘状态恢复")

    if cancelled:
        if moved:
            msg = f"已停止，部分文件已回收（成功 {len(moved)} 项，可撤销）"
        else:
            msg = "已停止：未回收任何文件"
            _rmtree_quiet(trash_root)
            trash_root = ""
        return _mk_result(root, status="cancelled", count=len(moved), paths=moved,
                          trash=trash_root, operation_id=operation_id if trash_root else "",
                          failures=failures, warnings=warnings, error=msg, kept=extra_kept)

    if failures or abort_error:
        if not moved:
            _rmtree_quiet(trash_root)
            msg = abort_error or f"清理未完成：成功 0 项，失败 {len(failures)} 项"
            return _mk_result(root, status="preflight_failed", failures=failures,
                              warnings=warnings, error=msg, kept=extra_kept)
        msg = f"清理未完成：成功 {len(moved)} 项，失败 {len(failures)} 项"
        if abort_error:
            warnings.append(abort_error)
        return _mk_result(root, status="partial", count=len(moved), paths=moved,
                          trash=trash_root, operation_id=operation_id,
                          failures=failures, warnings=warnings, error=msg, kept=extra_kept)

    return _mk_result(root, status="success", count=len(moved), paths=moved,
                      trash=trash_root, operation_id=operation_id, warnings=warnings,
                      kept=extra_kept)


# ── 恢复（restore_daily）─────────────────────────────────────────────────


def restore_daily(
    root: str,
    trash_root: str,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    """按清单把 daily 批次搬回 root；校验归属/边界/版本/内容摘要，同名不覆盖，可重试。

    恢复 = 两遍：pass1 全只读预检（归属/结构/版本/逐条边界/v3 摘要），任一失败整批拒绝、
    零搬运；pass2 逐条搬回并持久账本。`--dry-run` 只走 pass1（仍然完整校验）。
    """
    emit = log or (lambda _s: None)
    root = os.path.abspath(os.path.expanduser(root))
    trash_root = os.path.abspath(os.path.expanduser(trash_root))
    with _StopGuard():
        _reset_stop()
        return _restore_inner(root, trash_root, dry_run=dry_run, emit=emit)


def _restore_inner(root: str, trash_root: str, *, dry_run: bool, emit: Callable[[str], None]) -> dict:
    def preflight(msg: str, path: str = "") -> dict:
        return _mk_result(root, status="preflight_failed", action="restore", dry_run=dry_run,
                          trash=trash_root, restore=trash_root,
                          failures=[{"path": path, "error": msg}], error=msg)

    if not os.path.isdir(root):
        return preflight(f"路径不是文件夹：{root}", root)
    root_real = os.path.realpath(root)
    # R01/R06/R07：批次归属、父目录、链接与边界校验 —— 全部先于任何写入。
    problem = _batch_problem(root, trash_root)
    if problem:
        return preflight(problem, trash_root)
    data = read_manifest(trash_root)
    if data is None:
        return preflight("回收清单缺失或不是合法 JSON（无法验证批次；批次保留可重试）", trash_root)

    # 结构存在性 → 版本闸（C2）：不认识的 schema 整批拒绝，先于任何写入/搬移。
    try:
        label, plan_items = _clean._manifest_plan(data)
    except _clean._ManifestProblem as exc:
        return preflight(str(exc), trash_root)

    manifest_root = data.get("root")
    if manifest_root and not _same_root(str(manifest_root), root):
        return preflight(
            f"回收批次不属于该目录：清单 root={manifest_root}，本次 root={root}", trash_root
        )

    trash_real = os.path.realpath(trash_root)
    items: list[dict] = []
    for plan_item in plan_items:
        rel = plan_item["path"]
        bad = _validate_entry_rel(rel, root_real, trash_real)
        if bad:
            return preflight(f"清单条目不安全（{bad}）：{rel or '（空路径）'}", rel)
        items.append(plan_item)

    # 内容摘要闸（C1.2/C3.2/C3.8）：版本判定后、任何写入（含台账持久化）前，整批只读预检。
    digest_failures = _clean._verify_entries_digests(items, root, trash_root, label)
    if digest_failures:
        return _mk_result(root, status="preflight_failed", action="restore", dry_run=dry_run,
                          trash=trash_root, restore=trash_root,
                          failures=digest_failures, error=str(digest_failures[0]["error"]))

    legacy_warnings: list[str] = []
    if label != "3":
        # 旧 v1/v2：允许恢复但显式告警「无内容摘要，无法校验是否被篡改」。
        n_legacy = _clean._restorable_count(items, root, trash_root)
        if n_legacy:
            legacy_warnings.append(_clean._legacy_restore_warning(label, n_legacy))
            emit(legacy_warnings[-1])

    raw_items = [item["raw"] for item in items]

    done: list[str] = []
    failures: list[dict] = []
    warnings: list[str] = list(legacy_warnings)
    skipped: list[dict] = []
    already = 0

    def add_skip(rel: str, reason: str) -> None:
        skipped.append({"path": rel, "reason": reason})

    def persist() -> None:
        # R08：每条成功搬回后把该条 status 原子持久为 restored（v1 也升级为 entries，
        # files 字段保留以继续兼容旧读取方）。
        data["entries"] = raw_items
        try:
            _save_manifest(trash_root, data)
        except OSError as exc:
            warnings.append(f"恢复清单状态写入失败（仍可重试）：{exc}")

    def stopped() -> dict:
        msg = f"已停止：恢复 {len(done)} 项（可重试）"
        return _mk_result(
            root, status="cancelled", count=len(done), paths=done,
            action="restore", dry_run=dry_run, trash=trash_root,
            restore=trash_root, operation_id=str(data.get("operationId") or ""),
            failures=failures, warnings=warnings, error=msg,
            entries=[{"path": p, "action": "restore", "status": "restored"} for p in done],
            already_restored=already, skipped=skipped,
            summary={"restored": len(done), "alreadyRestored": already,
                     "skipped": len(skipped), "failed": len(failures)},
        )

    try:
        for item in items:
            _check_stop()
            rel = item["path"]
            typ = item["type"]
            state = item["status"]
            raw = item["raw"]
            src = os.path.join(trash_root, rel)
            dst = os.path.join(root, rel)

            if state == "restored":
                if os.path.lexists(dst):
                    already += 1
                    emit(f"Already restored: {rel}")
                    continue
                if not os.path.lexists(src):
                    add_skip(rel, "清单记为已恢复，但目标与回收区均缺失")
                    warnings.append(f"Missing: {rel}")
                    emit(f"Missing: {rel}")
                    continue
                # 账本过期（目标又被删除）：按普通恢复继续，避免永久卡住。
            elif state not in (None, "", "moved", "moving"):
                continue  # planned/failed：从未搬过，不恢复也不计失败

            src_ok = os.path.isdir(src) if typ == "dir" else os.path.exists(src)
            if not src_ok:
                add_skip(rel, "回收区缺失该条目")
                warnings.append(f"Missing in trash: {rel}")
                emit(f"Missing in trash: {rel}")
                continue
            if os.path.lexists(dst):
                add_skip(rel, "目标已存在（不覆盖）")
                warnings.append(f"Skip (已经存在，不覆盖): {rel}")
                emit(f"Skip (已经存在，不覆盖): {rel}")
                continue
            if dry_run:
                emit(f"Will restore: {rel}")
                done.append(rel)
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            except OSError as exc:
                failures.append({"path": rel, "error": str(exc)})
                emit(f"Error restoring {rel}: {exc}")
                continue
            emit(f"Restored: {rel}")
            done.append(rel)
            if isinstance(raw, dict):
                raw["status"] = "restored"
                persist()
    except _Cancelled:
        return stopped()
    except KeyboardInterrupt:
        return stopped()

    summary = {"restored": len(done), "alreadyRestored": already,
               "skipped": len(skipped), "failed": len(failures)}
    entries_out = [{"path": p, "action": "restore", "status": "restored"} for p in done]
    # C13：旧 v1/v2 批次保留旧 CLI 语义（冲突/缺失 → 警告，退出 0）；
    # daily 批次按 INTERFACE-REWORK 报 partial（ok=false、rc2），GUI 据此提示重试。
    strict = data.get("mode") == "daily" or data.get("kind") == "daily"
    if failures or (strict and skipped):
        status = "partial"
        error = f"撤销未完成：恢复 {len(done)} 项，跳过/失败 {len(skipped) + len(failures)} 项"
    elif done:
        status = "success"
        error = ""
    else:
        status = "empty"
        error = ""
    return _mk_result(
        root,
        status=status,
        count=len(done),
        paths=done,
        action="restore",
        dry_run=dry_run,
        trash=trash_root,
        restore=trash_root,
        operation_id=str(data.get("operationId") or ""),
        failures=failures,
        warnings=warnings,
        error=error,
        entries=entries_out,
        already_restored=already,
        skipped=skipped,
        summary=summary,
    )


def _validate_entry_rel(rel: str, root_real: str, trash_real: str) -> str:
    """返回空串=安全；否则返回原因（用于整批 preflight 拒绝）。"""
    if not rel or not rel.strip():
        return "空路径"
    if os.path.isabs(rel) or rel.startswith("/"):
        return "绝对路径"
    if rel.startswith("\\") or re.match(r"^[A-Za-z]:", rel):
        return "绝对路径"
    # 中段 .. 必须在归一化之前拒绝：normpath 会抹掉 symlink 敏感段，而实际 IO 用原始 rel。
    if ".." in rel.replace("\\", "/").split("/"):
        return "路径穿越"
    norm = os.path.normpath(rel)
    if norm in (".", "..") or norm.startswith(".." + os.sep) or norm.startswith("../"):
        return "路径穿越"
    dst_real = os.path.realpath(os.path.join(root_real, norm))
    if not _inside(root_real, dst_real):
        return "目标越界"
    src_real = os.path.realpath(os.path.join(trash_real, norm))
    if not _inside(trash_real, src_real):
        return "来源越界"
    return ""
