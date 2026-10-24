"""删除 LaTeX 编译中间文件 / 交付目录收尾 / 工作区旧件清理。

⚠️ 「默认」路径（`clean()` / 无新参数的 `cleantemp.py`）刻意与 EditToolBox 的
`sources-mac/Features/DeleteTempFeature.swift` **行为完全一致**，以便将来把本工具并入
EditToolBox 时两边不会打架。对齐的细节包括：

  · 后缀列表逐项相同（含 macOS 新增的 `.DS_Store`）
  · 递归遍历整棵目录树
  · 后缀**两边都转小写**再比，所以列表里可以写成易读的 `.DS_Store`
  · **故意不删 `.log`** —— 编译出错时要靠它排查；工具箱的「删除临时文件」也不删
  · 日志文案相同：`Deleted: <相对路径>` / `删除完成，共删除N个文件。`

不删的还有 `.tex` / `.pdf` / `.sty` / `.bib`。

**B-211（2026-10-24）加了两套「破坏性但可撤销」的模式**：

  · `delivery_clean()` 交付目录收尾：先预检 `\\includegraphics/\\input/\\include/
    \\addbibresource/\\bibliography` 引用闭包（缺失 → 整体拒绝、零写入），再按
    「白名单 > 清理名单 > 未识别保留」三步走；目录只删 `*_待核` 整棵。
  · `workspace_clean()` 工作区旧件：每个期目录顶层 `合并*.pdf` 只留最新
    （时间从文件名解析；解析不出的一律保守保留），再递归清 `信息检查*/ *.bak/
    *.coop-bak-*/ *_待核/ .DS_Store`；固定交付物（`000*`、`contents.*`、
    `Chinese Content.*`、`扉页*`、`编委*`、`封底*`）永不删。

两套模式**默认移入回收站**（`--delete` 才永久删）；回收站统一为
`<root>/.toolbox-trash/<basename(root)>-<YYYY_MM_DD_HHMMSS>/`，manifest schema=2
（文件 + 目录条目），`restore()` 兼容旧 v1 manifest（`files` 列表）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from collections.abc import Callable

from . import paths as P

# 与 EditToolBox DeleteTempFeature.suffixes 逐项一致
CLEAN_SUFFIXES = [
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".DS_Store",
]

# 明确声明「不删」的，防止以后有人手滑加进去
KEEP_SUFFIXES = [".tex", ".pdf", ".sty", ".bib", ".log"]

#: 交付收尾要清的 aux 全套 + 各类垃圾（比对时统一转小写）
DELIVERY_JUNK_SUFFIXES = [
    ".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz",
    ".fls", ".fdb_latexmk", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb",
    ".bbl-save-error", ".ds_store", ".ai前", ".log", ".bak",
]

#: 交付白名单后缀（`*_原稿.pdf` 是例外，按下面前缀单独判）
DELIVERY_KEEP_SUFFIXES = (".tex", ".bib", ".sty", ".cls")

#: 交付模式里按「名字含这些片段」判垃圾的人工产物
DELIVERY_JUNK_NAMES = (
    "_AI", "_文献核实.md", "_回读校验报告.md", "_参考文献核对.txt", "_原稿.pdf",
)

#: 递归解析引用闭包的最大层数（`\\input/\\include`）
CLOSURE_MAX_DEPTH = 5


def matches(name: str) -> bool:
    """判断文件名是否属于待清理的中间文件。"""
    low = name.lower()
    return any(low.endswith(s.lower()) for s in CLEAN_SUFFIXES)


#: **永不进入**的目录名（小写比较）：`original/` 是作者来稿归档，红线是"只读"
#: （`docs/INVARIANTS.md` 一.1、仓库根 `AGENTS.md` 红线 1）。
#: 为什么非拦不可：清理器的后缀表里有 `.bbl` / `.out` / `.bcf` 这些 ——
#: 作者交 LaTeX 稿时 `original/` 里**真的可能有同名文件**，删掉就是丢作者的东西。
#: 2026-10-17 在真期目录上实测：`--dry-run` 列出 5 个待删文件，其中 **3 个在 `original/` 里**
#: （清一色 `.DS_Store`）—— 那已经是在动"只读"目录了。
#: 历史期用过的大小写/别名（2026-10-17 全库普查：original 82 篇、Original 10 篇、
#: `origin` 8 篇、`原稿` 3 篇）。**规范名是 `original/`**，但历史目录不能因为名字不同
#: 就失去「作者来稿只读」保护 —— 清理器一律跳过这些别名（大小写不敏感，见下面 `d.lower()`）。
SKIP_DIRS = ("original", "origin", "原稿", "源稿", "原始稿")

#: 本工具自建的回收目录名（放在被清理目录的根下）。遍历时必须一起跳过 ——
#: 否则「移入回收 → 再清理」会把回收目录里的东西当中间文件再搬一次（嵌套回收）。
TRASH_DIR = ".toolbox-trash"


def clean(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """递归删除 root 下的编译中间文件。

    返回 (删除数量, 被删文件的相对路径列表)。
    log 传入一个可调用对象即可把过程写进界面日志（与 EditToolBox 的 Logger 对应）。
    """
    emit = log or (lambda _s: None)

    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, []

    deleted: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # 红线：`original/` 一律不进（剪掉分支，等于整棵子树都不看）
        # 回收目录也不进：防止「移入回收 → 再清理」把回收的东西再搬一遍
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and d != TRASH_DIR]
        for fname in filenames:
            if not matches(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = P.rel(full, root)
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
            if dry_run:
                emit(f"Will delete: {rel}")
                deleted.append(rel)
                continue
            try:
                os.remove(full)
                emit(f"Deleted: {rel}")
                deleted.append(rel)
            except OSError as e:
                emit(f"Error deleting {rel}: {e}")

    if dry_run:
        emit(f"（预演）将删除 {len(deleted)} 个文件，共 {_human_size(total)}。")
    else:
        emit(f"删除完成，共删除{len(deleted)}个文件。")
    return len(deleted), deleted


def _human_size(n: int) -> str:
    """给编辑看的体积（确认框里说"多大"比说"确定吗"有用得多）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y_%m_%d_%H%M%S")


def trash_dir(root: str, stamp: str | None = None) -> str:
    """回收目录：`<root>/.toolbox-trash/<basename(root)>-<时间戳>/`（B-211 起）。"""
    base = os.path.basename(os.path.abspath(root)) or "root"
    stamp = stamp or _stamp()
    parent = os.path.join(root, TRASH_DIR)
    cand = os.path.join(parent, f"{base}-{stamp}")
    n = 1
    while os.path.exists(cand):  # 同一秒内连续清理：加序号，绝不覆盖已有回收目录
        n += 1
        cand = os.path.join(parent, f"{base}-{stamp}-{n}")
    return cand


def _write_manifest(trash_root: str, root: str, stamp: str, mode: str,
                    entries: list[tuple[str, str]]) -> None:
    """写 manifest schema=2（文件 + 目录条目；`restore()` 按 entries 搬回）。"""
    os.makedirs(trash_root, exist_ok=True)
    data = {
        "schema": 2,
        "root": os.path.abspath(root),
        "createdAt": stamp,
        "mode": mode,
        "entries": [{"path": rel, "type": typ} for rel, typ in entries],
    }
    with open(os.path.join(trash_root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def trash(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """把中间文件**移入** `<root>/.toolbox-trash/<basename(root)>-<时间戳>/`（保留相对目录结构）。

    返回 (文件数, 回收目录)。回收目录里另写 `manifest.json`（schema=2，文件条目），供 `restore()`。

    与 `clean()` 的三点不同：① 不 `os.remove`，而是 `shutil.move`；② 建回收目录；
    ③ 目录结构保留（`01 A/xx.aux` → `.toolbox-trash/<名>-<ts>/01 A/xx.aux`），还原时不用猜。
    """
    emit = log or (lambda _s: None)
    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, ""
    stamp = _stamp()
    trash_root = trash_dir(root, stamp)
    moved: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and d != TRASH_DIR]
        for fname in filenames:
            if not matches(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = P.rel(full, root)
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
            if dry_run:
                emit(f"Will move: {rel}")
                moved.append(rel)
                continue
            target = os.path.join(trash_root, rel)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(full, target)
                emit(f"Moved: {rel}")
                moved.append(rel)
            except OSError as e:
                emit(f"Error moving {rel}: {e}")
    if dry_run:
        emit(f"（预演）将移入回收目录 {len(moved)} 个文件，共 {_human_size(total)}。")
        return len(moved), trash_root
    if moved:
        _write_manifest(trash_root, root, stamp, "aux",
                        [(rel, "file") for rel in moved])
    emit(f"已移入回收目录 {len(moved)} 个文件。")
    return len(moved), trash_root if moved else ""


def restore(
    root: str,
    trash_root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """按 `manifest.json` 把回收目录里的文件/目录搬回 `<root>`；返回 (恢复数, 相对路径)。

    · schema=2：按 `entries` 搬（`dir` 条目整棵 move 回）；
    · 旧 v1：按 `files` 列表搬（只有文件；旧回收目录照样能撤销）。
    ⚠️ 原位置**已经有同名文件/目录时不覆盖**（跳过并记一条）—— 还原不能变成第二次破坏。
    """
    emit = log or (lambda _s: None)
    manifest = os.path.join(trash_root, "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        emit(f"读不到回收清单：{e}")
        return 0, []
    entries = data.get("entries")
    if isinstance(entries, list):
        items: list[tuple[str, str]] = []
        for e in entries:
            if isinstance(e, dict) and str(e.get("path", "")):
                items.append((str(e["path"]), str(e.get("type") or "file")))
    else:  # v1 兼容：{"files": [...]}
        items = [(str(x), "file") for x in data.get("files", [])]
    done: list[str] = []
    for rel, typ in items:
        src = os.path.join(trash_root, rel)
        dst = os.path.join(root, rel)
        if typ == "dir" and not os.path.isdir(src):
            emit(f"Missing in trash: {rel}")
            continue
        if typ != "dir" and not os.path.exists(src):
            emit(f"Missing in trash: {rel}")
            continue
        if os.path.exists(dst):
            emit(f"Skip (已经存在，不覆盖): {rel}")
            continue
        if dry_run:
            emit(f"Will restore: {rel}")
            done.append(rel)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            emit(f"Restored: {rel}")
            done.append(rel)
        except OSError as e:
            emit(f"Error restoring {rel}: {e}")
    if dry_run:
        emit(f"（预演）将恢复 {len(done)} 个文件。")
    else:
        emit(f"恢复完成，共恢复{len(done)}个文件。")
    return len(done), done


# ── B-211：交付目录收尾 ───────────────────────────────────────────────────


class DeliveryError(ValueError):
    """交付预检失败（不是交付目录 / 引用缺失）—— 外壳据此 exit 2、零写入。"""


_TEX_INPUT_RE = re.compile(r"\\(?:input|include)(?![A-Za-z])\s*\{([^{}]*)\}")
_TEX_GRAPHICS_RE = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_TEX_ADDBIB_RE = re.compile(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_TEX_BIBLIO_RE = re.compile(r"\\bibliography\s*\{([^{}]*)\}")

_GRAPHICS_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")


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


def _resolve_ref(raw: str, bases: list[str], exts: tuple[str, ...]) -> str:
    """把 tex 引用解析成绝对路径；解析不到返回 ""（相对 bases 逐个、按扩展名顺序试）。"""
    p = raw.strip().strip('"')
    if not p:
        return ""
    cands = [p]
    if exts and not os.path.splitext(p)[1].lower():
        cands = [p + e for e in exts]
    for base in bases:
        for c in cands:
            full = os.path.normpath(os.path.join(base, c))
            if os.path.isfile(full):
                return full
    return ""


def delivery_closure(root: str) -> tuple[set[str], set[str], list[str]]:
    """预检交付目录的引用闭包 → `(闭包文件, 闭包目录, 缺失引用)`（相对 root）。

    · 根顶层必须有 ≥1 个 `*.tex`，否则 raise DeliveryError（不是交付目录）；
    · `\\includegraphics` 无扩展依次试 `.pdf/.png/.jpg/.jpeg/.eps`；
    · `\\input/\\include` 递归解析（≤5 层）、`\\addbibresource/\\bibliography` 收 bib；
    · 任一解析不到 → 全部收进「缺失」，由调用方整体拒绝（**零写入**）。
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise DeliveryError(f"路径不是文件夹：{root}")
    tops = sorted(f for f in os.listdir(root)
                  if os.path.isfile(os.path.join(root, f)) and f.lower().endswith(".tex"))
    if not tops:
        raise DeliveryError("不是交付目录：未找到 .tex")

    closure_files: set[str] = set()
    closure_dirs: set[str] = set()
    missing: list[str] = []

    def _add_file(full: str) -> None:
        rel = P.rel(os.path.normpath(full), root)
        closure_files.add(rel)
        d = os.path.dirname(rel)
        while d and d != ".":
            closure_dirs.add(d)
            d = os.path.dirname(d)

    def _bases(tex_dir: str) -> list[str]:
        return [root] + ([tex_dir] if tex_dir and tex_dir != root else [])

    def _walk(tex_file: str, depth: int) -> None:
        if depth > CLOSURE_MAX_DEPTH:
            return
        try:
            with open(tex_file, encoding="utf-8", errors="replace") as fh:
                text = _strip_tex_comments(fh.read())
        except OSError:
            return
        tex_dir = os.path.dirname(tex_file)
        for m in _TEX_GRAPHICS_RE.finditer(text):
            full = _resolve_ref(m.group(1), _bases(tex_dir), _GRAPHICS_EXTS)
            if not full:
                missing.append(m.group(1).strip())
            else:
                _add_file(full)
        for m in _TEX_INPUT_RE.finditer(text):
            full = _resolve_ref(m.group(1), _bases(tex_dir), (".tex",))
            if not full:
                missing.append(m.group(1).strip())
            else:
                _add_file(full)
                _walk(full, depth + 1)
        for m in _TEX_ADDBIB_RE.finditer(text):
            full = _resolve_ref(m.group(1), _bases(tex_dir), (".bib",))
            if not full:
                missing.append(m.group(1).strip())
            else:
                _add_file(full)
        for m in _TEX_BIBLIO_RE.finditer(text):
            for part in m.group(1).split(","):
                if not part.strip():
                    continue
                full = _resolve_ref(part, _bases(tex_dir), (".bib",))
                if not full:
                    missing.append(part.strip())
                else:
                    _add_file(full)

    for top in tops:
        _walk(os.path.join(root, top), 0)
    return closure_files, closure_dirs, sorted(set(missing))


def _delivery_junk(name: str) -> bool:
    low = name.lower()
    if any(low.endswith(s) for s in DELIVERY_JUNK_SUFFIXES):
        return True
    if "_AI" in name:
        return True
    if "_一校核查" in name and low.endswith(".md"):
        return True
    return any(part in name for part in DELIVERY_JUNK_NAMES)


def _delivery_whitelisted(rel: str, name: str, closure_files: set[str]) -> bool:
    if rel in closure_files:
        return True
    low = name.lower()
    if low.endswith(DELIVERY_KEEP_SUFFIXES):
        return True
    if low.endswith(".pdf"):
        return not name.endswith("_原稿.pdf")
    if low.endswith(".md") and "交付清单" in name:
        return True
    return False


def _contains_whitelisted(root: str, rel_dir: str, closure_files: set[str]) -> bool:
    """`_待核` 整棵里若有白名单文件 → 整棵保留（白名单优先于清理）。"""
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, rel_dir)):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and d != TRASH_DIR]
        for fname in filenames:
            rel = P.rel(os.path.join(dirpath, fname), root)
            if _delivery_whitelisted(rel, fname, closure_files):
                return True
    return False


class _Sink:
    """统一搬运口：移入回收站（默认）或永久删除；dry-run 只记账零写入。"""

    def __init__(self, root: str, mode: str, *, dry_run: bool, delete: bool,
                 log: Callable[[str], None]):
        self.root = root
        self.mode = mode
        self.dry_run = dry_run
        self.delete = delete
        self.emit = log
        self.stamp = _stamp()
        self.trash_root = "" if delete else trash_dir(root, self.stamp)
        self.entries: list[tuple[str, str]] = []
        #: 失败项：(相对路径, 失败原因)。成功计数 entries 照旧只记成功项。
        self.failures: list[tuple[str, str]] = []

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def take(self, full: str, rel: str, typ: str) -> bool:
        if self.dry_run:
            self.emit(("Will delete: " if self.delete else "Will move: ") + rel)
        else:
            try:
                if self.delete:
                    if typ == "dir":
                        shutil.rmtree(full)
                    else:
                        os.remove(full)
                    self.emit(f"Deleted: {rel}")
                else:
                    dst = os.path.join(self.trash_root, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(full, dst)
                    self.emit(f"Moved: {rel}")
            except OSError as e:
                self.failures.append((rel, str(e)))
                self.emit(f"Error {'deleting' if self.delete else 'moving'} {rel}: {e}")
                return False
        self.entries.append((rel, typ))
        return True

    def commit(self) -> str:
        if self.delete or not self.entries:
            return ""
        if self.dry_run:
            return self.trash_root
        _write_manifest(self.trash_root, self.root, self.stamp, self.mode, self.entries)
        return self.trash_root


def delivery_clean(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
    delete: bool = False,
    failures: list[tuple[str, str]] | None = None,
) -> tuple[int, str]:
    """交付目录收尾：白名单 > 清理名单 > 未识别保留；目录只删 `*_待核` 整棵。

    返回 (搬运条目数, 回收目录)。引用缺失时 raise DeliveryError（**零写入**）。
    调用前请先跑 `delivery_closure()` 拿闭包（本函数内部也会再算一次，保证独立可用）。

    向后兼容：可选 `failures`（输出参数）收「(相对路径, 原因)」失败项；
    不传则行为与旧版完全一致，返回值 tuple 长度/顺序不变。成功计数仍只数成功项。
    """
    emit = log or (lambda _s: None)
    closure_files, closure_dirs, missing = delivery_closure(root)
    if missing:
        raise DeliveryError("引用缺失：" + "、".join(missing))
    sink = _Sink(root, "delivery", dry_run=dry_run, delete=delete, log=emit)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = P.rel(dirpath, root)
        if rel_dir and rel_dir in closure_dirs:  # 闭包目录整棵保护
            dirnames[:] = []
            continue
        keep_dirs: list[str] = []
        for d in sorted(dirnames):
            if d.lower() in SKIP_DIRS or d == TRASH_DIR:
                continue  # 红线目录 / 回收站：整棵跳过
            full = os.path.join(dirpath, d)
            rel = P.rel(full, root)
            if d.endswith("_待核"):
                if (rel in closure_dirs
                        or _contains_whitelisted(root, rel, closure_files)):
                    keep_dirs.append(d)
                    emit(f"保留（白名单）：{rel}/")
                    continue
                if sink.take(full, rel, "dir"):
                    continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            rel = P.rel(full, root)
            wl = _delivery_whitelisted(rel, fname, closure_files)
            junk = _delivery_junk(fname)
            if wl:
                if junk:  # 白名单优先于清理命中
                    emit(f"保留（白名单）：{rel}")
                continue
            if junk:
                sink.take(full, rel, "file")
                continue
            emit(f"保留（未识别）：{rel}")
    if failures is not None:
        failures.extend(sink.failures)
    return sink.count, sink.commit()


def _merge_time(name: str) -> tuple[int, ...] | None:
    """从 `合并<时间>.pdf` 解析时间；固定名 `合并.pdf` 或无时间返回 None。"""
    if not name.startswith("合并") or not name.lower().endswith(".pdf"):
        return None
    stem = name[len("合并"):-len(".pdf")]
    if stem.endswith("（修改目录）"):
        stem = stem[:-len("（修改目录）")]
    m = re.fullmatch(r"(\d{4})_(\d{2})_(\d{2})(?:_(\d{2})_(\d{2})(?:_(\d{2}))?)?", stem)
    if not m:
        return None
    return tuple(int(g) if g is not None else 0 for g in m.groups())


def _workspace_protected(name: str) -> bool:
    """工作区固定交付物（保护即整棵跳过；`000*` 目录整棵）。"""
    if name.startswith("000"):
        return True
    if name.startswith("contents."):
        return True
    if name.startswith("Chinese Content."):
        return True
    return name.startswith(("扉页", "编委", "封底"))


def _workspace_merge_unit(unit: str, root: str, sink: _Sink,
                          emit: Callable[[str], None]) -> None:
    """单元顶层 `合并*.pdf`：固定名保留、有时间只留最新、解析不出的一律保守保留。"""
    files = sorted(f for f in os.listdir(unit)
                   if f.startswith("合并") and f.lower().endswith(".pdf")
                   and os.path.isfile(os.path.join(unit, f)))
    parsed = {f: _merge_time(f) for f in files if f != "合并.pdf"}
    have_time = {f: t for f, t in parsed.items() if t is not None}
    no_time = [f for f, t in parsed.items() if t is None]
    if have_time:
        best = max(have_time, key=lambda f: (have_time[f], f))
        for f in files:
            if f == best or f == "合并.pdf":
                continue
            full = os.path.join(unit, f)
            rel = P.rel(full, root)
            if f in have_time:
                sink.take(full, rel, "file")
            else:
                emit(f"保留（无法解析时间，不猜）：{rel}")
        return
    # 一份都解析不出时间：仅 1 份或固定名 → 保留；多份 → 全保留 + warning
    if len(no_time) > 1:
        emit(f"保留（{len(no_time)} 份均无法解析时间，未删除）：{unit}")


def workspace_clean(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
    delete: bool = False,
    failures: list[tuple[str, str]] | None = None,
) -> tuple[int, str]:
    """工作区旧件：每期 `合并*.pdf` 只留最新 + 递归清 `信息检查*/ *.bak/*.coop-bak-*/ *_待核/`。

    向后兼容：可选 `failures`（输出参数）收「(相对路径, 原因)」失败项；
    不传则行为与旧版完全一致，返回值 tuple 长度/顺序不变。成功计数仍只数成功项。
    """
    emit = log or (lambda _s: None)
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, ""
    sink = _Sink(root, "workspace", dry_run=dry_run, delete=delete, log=emit)
    # ① 每单元顶层合并 PDF（单元 = root 自身 + root 直接子目录）
    units = [root]
    for d in sorted(os.listdir(root)):
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        if d.lower() in SKIP_DIRS or d == TRASH_DIR or _workspace_protected(d):
            continue
        units.append(full)
    for unit in units:
        _workspace_merge_unit(unit, root, sink, emit)
    # ② 递归清垃圾（跳过 original 系 / 回收站 / 固定交付物整棵）
    for dirpath, dirnames, filenames in os.walk(root):
        keep_dirs: list[str] = []
        for d in sorted(dirnames):
            if d.lower() in SKIP_DIRS or d == TRASH_DIR or _workspace_protected(d):
                continue
            full = os.path.join(dirpath, d)
            rel = P.rel(full, root)
            if ".coop-bak-" in d or d.endswith("_待核"):
                if sink.take(full, rel, "dir"):
                    continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for fname in sorted(filenames):
            if _workspace_protected(fname):
                continue
            junk = (fname.startswith("信息检查")
                    or fname.lower().endswith(".bak")
                    or fname == ".DS_Store"
                    or ".coop-bak-" in fname)
            if not junk:
                continue
            full = os.path.join(dirpath, fname)
            sink.take(full, P.rel(full, root), "file")
    if failures is not None:
        failures.extend(sink.failures)
    return sink.count, sink.commit()


def empty_trash(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> int:
    """清空 `<root>/.toolbox-trash/` 下所有回收目录，返回清掉的个数（不存在 → 0）。"""
    emit = log or (lambda _s: None)
    trash_root = os.path.join(root, TRASH_DIR)
    if not os.path.isdir(trash_root):
        return 0
    n = 0
    for name in sorted(os.listdir(trash_root)):
        full = os.path.join(trash_root, name)
        if dry_run:
            emit(f"Will delete: {name}")
            n += 1
            continue
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            emit(f"Deleted: {name}")
            n += 1
        except OSError as e:
            emit(f"Error deleting {name}: {e}")
    return n
