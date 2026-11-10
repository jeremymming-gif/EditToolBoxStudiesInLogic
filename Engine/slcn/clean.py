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
`<root>/.toolbox-trash/<basename(root)>-<YYYY_MM_DD_HHMMSS>/`，manifest schema=3
（文件 + 目录条目；文件条目带内容 sha256），`restore()` 恢复前校验版本与内容摘要，
兼容旧 v1/v2 清单（`files` 列表或 schema=1/2），不认识的版本整批拒绝。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
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

#: 回收清单允许的 schema 值。键缺失 = 旧 v1；其余任何值一律整批拒绝（版本闸）。
ALLOWED_SCHEMAS = (1, 2, 3)

#: 「可能恢复」的条目状态：planned/failed 从未搬出，永不恢复（SIGKILL 意图条目）。
RESTORABLE_STATES = (None, "", "moved", "moving", "restored")

#: manifest 里文件条目的内容摘要（小写 64 位十六进制）。
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _ManifestProblem(Exception):
    """清单结构/版本不合法：调用方据此整批拒绝（预检失败、零搬运）。"""


def _json_repr(value: object) -> str:
    """把任意清单值渲染成紧凑 JSON（未知版本/schema 告警用）。"""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _sha256_file(path: str) -> str:
    """流式计算文件内容 SHA-256（小写十六进制）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_version_label(data: dict) -> str:
    """返回清单版本标签：``"v1"``（无 schema 键）/``"1"``/``"2"``/``"3"``。

    schema 键存在但值不是 1/2/3（或不是 int，如 True/"3"/null/3.0）→ 抛
    ``_ManifestProblem``（未知清单版本：整批拒绝）。
    """
    if "schema" not in data:
        return "v1"
    value = data["schema"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALLOWED_SCHEMAS:
        raise _ManifestProblem(
            f"不认识的回收清单版本（schema={_json_repr(value)}；支持 1/2/3；"
            "批次保留、未搬运任何文件）"
        )
    return str(value)


def _manifest_plan(data: dict) -> tuple[str, list[dict]]:
    """结构存在性校验 + 版本判定，返回 ``(版本标签, 归一化条目)``。

    顺序固定（契约 C2/C3.1）：先看 ``entries``/``files`` 存在性，再看 schema 版本。
    ``files`` 元素可为字符串路径或 ``{"path": ...}`` 对象（旧 v1 两种写法都兼容）。
    """
    entries = data.get("entries")
    files = data.get("files")
    if isinstance(entries, list):
        label = _manifest_version_label(data)
        items: list[dict] = []
        for raw in entries:
            if not isinstance(raw, dict):
                raise _ManifestProblem(f"清单条目不是对象：{raw!r}")
            items.append({
                "raw": raw,
                "path": str(raw.get("path") or ""),
                "type": str(raw.get("type") or "file"),
                "status": raw.get("status"),
                "sha256": raw.get("sha256"),
            })
        return label, items
    if isinstance(files, list):
        label = _manifest_version_label(data)
        items = []
        for raw in files:
            rel = str(raw.get("path") or "") if isinstance(raw, dict) else str(raw)
            upgraded = {"path": rel, "type": "file", "status": "moved"}
            items.append({
                "raw": upgraded,
                "path": rel,
                "type": "file",
                "status": "moved",
                "sha256": None,
            })
        return label, items
    raise _ManifestProblem("不认识的回收清单（既无 entries 也无 files）")


def _is_file_entry(item: dict) -> bool:
    return str(item.get("type") or "file") != "dir"


def _restorable_count(items: list[dict], root: str, batch_dir: str) -> int:
    """本次可恢复的 file 条目数（旧批告警里的 N；只看真正会搬回的文件）。"""
    n = 0
    for item in items:
        if not _is_file_entry(item) or item.get("status") not in RESTORABLE_STATES:
            continue
        rel = item["path"]
        src = os.path.join(batch_dir, rel)
        dst = os.path.join(root, rel)
        if os.path.exists(src) and not os.path.lexists(dst):
            n += 1
    return n


def _verify_entries_digests(items: list[dict], root: str, batch_dir: str,
                            label: str) -> list[dict]:
    """恢复前的整批内容摘要预检（契约 C3.2/C3.8），返回失败项（空 = 通过）。

    · 只校验「可能恢复」且本次真会搬回的 file 条目（src 存在、dst 不存在）；
      ``planned``/``failed``、src 缺失、dst 已存在一律不校验；
    · schema=3 的 file 条目必须先有合法 sha256（缺失/非字符串/格式不符 → 整批拒绝）；
    · 摘要不符 → 逐条给出 rel、清单 sha256、实算 sha256。
    """
    failures: list[dict] = []
    if label != "3":
        return failures
    # 摘要字段完整性：与 src/dst 状态无关，覆盖所有可能恢复的 file 条目。
    for item in items:
        if not _is_file_entry(item) or item.get("status") not in RESTORABLE_STATES:
            continue
        expected = item.get("sha256")
        if not isinstance(expected, str) or not _SHA256_RE.match(expected):
            failures.append({
                "path": item["path"],
                "error": f"回收清单条目缺少有效 sha256（{item['path']}；"
                         "批次保留、未搬运任何文件）",
            })
    if failures:
        return failures
    for item in items:
        if not _is_file_entry(item) or item.get("status") not in RESTORABLE_STATES:
            continue
        rel = item["path"]
        src = os.path.join(batch_dir, rel)
        dst = os.path.join(root, rel)
        if not os.path.exists(src) or os.path.lexists(dst):
            continue
        expected = str(item.get("sha256") or "")
        try:
            actual = _sha256_file(src)
        except OSError as exc:
            failures.append({
                "path": rel,
                "error": f"回收清单内容摘要不符（{rel}：清单 sha256={expected}，"
                         f"实算=读取失败（{exc}）；批次保留、未搬运任何文件）",
            })
            continue
        if actual != expected:
            failures.append({
                "path": rel,
                "error": f"回收清单内容摘要不符（{rel}：清单 sha256={expected}，"
                         f"实算={actual}；批次保留、未搬运任何文件）",
            })
    return failures


def _legacy_restore_warning(label: str, count: int) -> str:
    """旧 v1/v2 批次的可预期告警（进 warnings 数组 + 人读行）。"""
    return (f"RESTORE_UNVERIFIED: 清单为旧版本（schema={label}），{count} 个文件"
            "无内容摘要，无法校验是否被篡改；批次保留")


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
    """写 manifest schema=3（目录条目无摘要；文件条目带内容 sha256，撤销时校验）。"""
    os.makedirs(trash_root, exist_ok=True)
    manifest_entries: list[dict] = []
    for rel, typ in entries:
        item: dict = {"path": rel, "type": typ}
        if typ != "dir":
            try:
                # 搬移后从批次内文件算；内容即搬移前内容（同一文件只算一次）。
                item["sha256"] = _sha256_file(os.path.join(trash_root, rel))
            except OSError:
                # 极端情况（并发删除等）：条目照写；恢复时摘要闸会整批拒绝该批次。
                pass
        manifest_entries.append(item)
    data = {
        "schema": 3,
        "root": os.path.abspath(root),
        "createdAt": stamp,
        "mode": mode,
        "entries": manifest_entries,
    }
    with open(os.path.join(trash_root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def trash(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """把中间文件**移入** `<root>/.toolbox-trash/<basename(root)>-<时间戳>/`（保留相对目录结构）。

    返回 (文件数, 回收目录)。回收目录里另写 `manifest.json`（schema=3，文件条目带内容 sha256），供 `restore()` 校验撤销。

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


def _validate_manifest_rel(rel: str) -> str:
    """恢复前路径安全校验：返回空串=安全，否则原因（与 clean_daily 文案一致）。"""
    if not rel or not rel.strip():
        return "空路径"
    if os.path.isabs(rel) or rel.startswith("/"):
        return "绝对路径"
    if rel.startswith("\\") or re.match(r"^[A-Za-z]:", rel):
        return "绝对路径"
    if ".." in rel.replace("\\", "/").split("/"):
        return "路径穿越"
    norm = os.path.normpath(rel)
    if norm in (".", "..") or norm.startswith(".." + os.sep) or norm.startswith("../"):
        return "路径穿越"
    return ""


def restore(
    root: str,
    trash_root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """按 `manifest.json` 把回收目录里的文件/目录搬回 `<root>`；返回 (恢复数, 相对路径)。

    · 版本闸：schema=3 逐文件核对内容 sha256；schema=1/2 或旧 v1（无 schema）允许恢复
      并打 RESTORE_UNVERIFIED 告警；不认识的版本整批拒绝、零搬运（与 cleantemp
      --restore 同一套判定，不留旧行为旁路）。
    · 搬移任何条目前先做全量只读预检（结构/版本/路径边界/内容摘要），失败 → (0, [])。
    · schema=3 的 `dir` 条目无摘要（整棵搬回，不校验内容——已知限制，文档写明）。
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
    try:
        label, items = _manifest_plan(data)
    except _ManifestProblem as exc:
        emit(str(exc))
        return 0, []
    for item in items:
        bad = _validate_manifest_rel(item["path"])
        if bad:
            emit(f"清单条目不安全（{bad}）：{item['path'] or '（空路径）'}")
            return 0, []
    digest_failures = _verify_entries_digests(items, root, trash_root, label)
    if digest_failures:
        for failure in digest_failures:
            emit(str(failure["error"]))
        return 0, []
    if label != "3":
        n_legacy = _restorable_count(items, root, trash_root)
        if n_legacy:
            emit(_legacy_restore_warning(label, n_legacy))
    done: list[str] = []
    for item in items:
        status = item.get("status")
        if status not in RESTORABLE_STATES:
            continue  # planned/failed：从未搬出，不恢复
        rel = item["path"]
        typ = item["type"]
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


# ── B-303：coop 旧交付目录回收 ─────────────────────────────────────────────
#
# `coop.promote_stage()` 覆盖既有交付时把旧目录 rename 成 `<交付>.coop-bak-<时间>` 留在
# 交付根同级——原子交付要它（失败能 rollback），但交付成功后它再没人用，编辑器眼里就是
# 一堆看不见的重复目录。用户 2026-10-31 拍板方案 A：**交付成功后自动移入交付根回收站**
# （同一交付只留最近 1 份），promote 的原子语义、失败回滚一个字面都不动 —— 这里只做
# 交付**之后**的收尾，走的是 `--trash/--restore/--empty-trash` 同一套回收站。

#: promote 给旧交付目录留的备份名标记：`<交付名>.coop-bak-<时间戳>`
COOP_BAK_MARK = ".coop-bak-"

#: 同一交付在回收站里保留的旧版本份数（S2：只留最近 1 份）
COOP_BAK_KEEP = 1


def _entry_rel(entry: object) -> str:
    return str(entry.get("path") or "") if isinstance(entry, dict) else ""


def _entry_type(entry: object) -> str:
    return str(entry.get("type") or "") if isinstance(entry, dict) else ""


def _manifest_entries(trash_root: str) -> list[dict]:
    """读回收目录的 manifest `entries`；读不到/结构不对 → 空（回收站要能容忍残缺）。"""
    try:
        with open(os.path.join(trash_root, "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = data.get("entries")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _drop_manifest_entries(trash_root: str, rels: set[str]) -> None:
    """从 manifest 摘掉 `rels` 条目；条目摘空后清单与该回收目录一起收掉（不留空壳/悬空项）。"""
    manifest = os.path.join(trash_root, "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    entries = data.get("entries")
    if not isinstance(entries, list):
        return
    left = [e for e in entries if _entry_rel(e) not in rels]
    if left:
        data["entries"] = left
        try:
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return
    try:  # 与 `empty_trash()` 的「清空后不留空壳」同口径
        os.remove(manifest)
        os.rmdir(trash_root)
    except OSError:
        pass


def _coop_bak_hits(root: str, prefix: str) -> list[tuple[str, str]]:
    """回收站里所有 `<prefix>` 开头的**目录**条目 → [(回收目录, 条目相对路径)]，按目录名排序。"""
    parent = os.path.join(root, TRASH_DIR)
    if not os.path.isdir(parent):
        return []
    hits: list[tuple[str, str]] = []
    for name in sorted(os.listdir(parent)):
        trash_root = os.path.join(parent, name)
        if not os.path.isdir(trash_root):
            continue
        for entry in _manifest_entries(trash_root):
            rel = _entry_rel(entry)
            if _entry_type(entry) == "dir" and rel and rel.startswith(prefix):
                hits.append((trash_root, rel))
    return hits


def _prune_coop_bak(root: str, prefix: str, *, newest: str = "",
                    keep: int = COOP_BAK_KEEP) -> list[str]:
    """同一交付只留最近 `keep` 份：删多余条目 + 同步 manifest（**只动 `.coop-bak-` 条目**）。"""
    if keep < 1:
        return []
    hits = _coop_bak_hits(root, prefix)
    # 本次新建的回收目录排在最后 = 一定保住（不靠时间戳/时钟排序）
    hits.sort(key=lambda h: (h[0] == newest, h[0]))
    dropped: list[str] = []
    for trash_root, rel in hits[:max(0, len(hits) - keep)]:
        target = os.path.join(trash_root, rel)
        try:
            shutil.rmtree(target)
        except OSError:
            if os.path.isdir(target):
                continue  # 还在（删不掉）：条目也留着，manifest 不悬空
        _drop_manifest_entries(trash_root, {rel})
        dropped.append(rel)
    return dropped


def retire_coop_bak(final_dir: str, backup_dir: str, *,
                    stamp: str | None = None) -> str:
    """B-303：把 promote 留下的旧交付目录移入回收站，同一交付只留最近 1 份。

    · 落点：`<root>/.toolbox-trash/<basename(root)>-<stamp>/<basename(backup_dir)>`
      （`root` = 交付目录的父目录；manifest schema=3、条目 `type="dir"`（目录无摘要），`restore()` 可整棵搬回）；
    · 剪枝：回收站里其他 `'<交付名>.coop-bak-'` 目录条目一并清掉并同步各自 manifest
      （**不碰**回收站里别的文件/目录；删空的回收目录连清单一起收掉）；
    · 移动失败照常抛（`FileNotFoundError`/`OSError`）——调用方降级成 warning：
      「回收旧备份」是收尾动作，绝不能连累已经成功的交付。
    返回回收站内的绝对路径。
    """
    final = os.path.abspath(final_dir)
    backup = os.path.abspath(backup_dir)
    if not os.path.isdir(backup):
        raise FileNotFoundError(f"旧交付目录不存在：{backup}")
    root = os.path.dirname(final) or os.curdir
    stamp = stamp or _stamp()
    trash_root = trash_dir(root, stamp)
    os.makedirs(trash_root, exist_ok=True)
    name = os.path.basename(backup)
    dst = os.path.join(trash_root, name)
    shutil.move(backup, dst)
    _write_manifest(trash_root, root, stamp, "coop-bak", [(name, "dir")])
    _prune_coop_bak(root, os.path.basename(final) + COOP_BAK_MARK, newest=trash_root)
    return dst


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


def _trash_manifest_unknown_schema(trash_root: str) -> tuple[bool, object]:
    """回收目录的 manifest 是否带未知 schema。返回 (是否未知, schema 值)。"""
    try:
        with open(os.path.join(trash_root, "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict) or "schema" not in data:
        return False, None
    value = data["schema"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALLOWED_SCHEMAS:
        return True, value
    return False, None


def empty_trash(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
    *,
    warnings: list[str] | None = None,
) -> int:
    """清空 `<root>/.toolbox-trash/` 下所有回收目录，返回清掉的个数（不存在 → 0）。

    B-281：真跑清空后若回收站目录**已空**，把它本身也删掉（否则每清一次都会在期目录
    里留一个空的 `.toolbox-trash/`，Windows 资源管理器里看着像没清干净）。目录里还剩
    东西（某个回收目录删失败，或**清单版本不认识而被保守保留**）或权限不允许时**静默
    保留**。预演（`dry_run`）不删目录。给外壳的契约行完全不变；「已删除空的回收站目录」
    这行提示只打在契约行**之前**（`cleantemp.py` 在 `empty_trash` 返回后才打印契约行）。

    · 未知清单版本（schema 键存在且值不是 1/2/3）：**保留**该回收目录、追加告警、
      不计入 n（避免把新版引擎写的批次当垃圾删掉）；
    · 清单缺失/坏 JSON/既无 entries 也无 files（历史残留）：保持既有行为**照删**；
    · 告警经 `warnings`（可选输出参数）与 `log` 输出；返回值/CLI 计数语义不变。
    """
    emit = log or (lambda _s: None)
    trash_root = os.path.join(root, TRASH_DIR)
    if not os.path.isdir(trash_root):
        return 0
    n = 0
    for name in sorted(os.listdir(trash_root)):
        full = os.path.join(trash_root, name)
        is_dir = os.path.isdir(full) and not os.path.islink(full)
        unknown, value = _trash_manifest_unknown_schema(full) if is_dir else (False, None)
        if unknown:
            msg = f"保留未知版本的回收目录：{name}（schema={_json_repr(value)}）"
            emit(msg)
            if warnings is not None:
                warnings.append(msg)
            continue
        if dry_run:
            emit(f"Will delete: {name}")
            n += 1
            continue
        try:
            if is_dir:
                shutil.rmtree(full)
            else:
                os.remove(full)
            emit(f"Deleted: {name}")
            n += 1
        except OSError as e:
            emit(f"Error deleting {name}: {e}")
    if dry_run:
        return n
    try:
        if os.path.isdir(trash_root) and not os.listdir(trash_root):
            os.rmdir(trash_root)
            emit(f"已删除空的回收站目录：{TRASH_DIR}")
    except OSError:
        pass  # 权限/竞态：目录留着就行，不向编辑报错
    return n


# ── B-121 B2/B3：生成中间件与旧检查报告（默认移回收站，可 restore）────────
#: 生成目录的中间件目录（与 `slcn/toc.py` 的 `TOC_WORK_DIR` 同一口径；两边独立常量
#: 是为了 clean.py 不反向依赖 toc.py，清理器能单独跑）。
TOC_WORK_DIR = ".toolbox-tmp"

#: `信息检查<stamp>.txt/.json/_摘要.md`：一组报告三件套共用同一个 stamp。
_INFO_CHECK_RE = re.compile(
    r"^信息检查(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})(?:_摘要)?\.(?:txt|json|md)$")

#: 期目录根的生成中间 tex（zh `Chinese Content_<stamp>.tex` / en `contents_<stamp>.tex`）；
#: 兼容 atomic_write 的 `.bak` 备份。
_TIMESTAMP_TEX_RE = re.compile(
    r"^(?:Chinese Content|contents)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tex(?:\.bak)?$")

#: 生成目录结果 JSON（壳/引擎都会写；旧文件散落在期目录根）。
_TOC_RESULT_NAMES = ("目录结果.json",)


def toc_work_candidates(root: str, *, keep_reports: int = 1,
                        all_reports: bool = False) -> list[tuple[str, str]]:
    """列出「生成中间件与旧检查报告」类目待清理的文件。

    返回 `[(相对路径, 类别)]`，类别取 `toc-temp`（隐藏工作目录/根时间戳 tex）、
    `toc-result`（目录结果 JSON）、`info-report`（旧信息检查报告）。

    · `.toolbox-tmp/` 内容全部算中间件；
    · 期目录根的时间戳 zh/en tex（B1 之前的旧散落文件）算中间件；
    · `目录结果.json` 算生成结果残留；
    · `信息检查*` 按 stamp 分组：默认**保留最新一组**（txt/json/_摘要 同 stamp 一起留），
      `all_reports=True` 时连最新一组也清（用户显式选择全清）。
    """
    if not os.path.isdir(root):
        return []
    items: list[tuple[str, str]] = []

    work = os.path.join(root, TOC_WORK_DIR)
    if os.path.isdir(work):
        for dirpath, dirnames, filenames in os.walk(work):
            dirnames[:] = sorted(d for d in dirnames if d != TRASH_DIR)
            for fname in sorted(filenames):
                items.append((P.rel(os.path.join(dirpath, fname), root), "toc-temp"))

    for fname in sorted(os.listdir(root)):
        if _TIMESTAMP_TEX_RE.match(fname):
            items.append((fname, "toc-temp"))
        elif fname in _TOC_RESULT_NAMES:
            items.append((fname, "toc-result"))

    groups: dict[str, list[str]] = {}
    for fname in sorted(os.listdir(root)):
        m = _INFO_CHECK_RE.match(fname)
        if m:
            groups.setdefault(m.group(1), []).append(fname)
    stamps = sorted(groups, reverse=True)
    drop = stamps if all_reports else stamps[max(0, keep_reports):]
    for stamp in sorted(drop):
        for fname in sorted(groups[stamp]):
            items.append((fname, "info-report"))
    return items


def toc_work_clean(
    root: str,
    *,
    keep_reports: int = 1,
    all_reports: bool = False,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
    delete: bool = False,
    failures: list[tuple[str, str]] | None = None,
) -> tuple[int, str]:
    """B-121：清理生成中间件与旧检查报告，返回 `(清理数, 回收目录)`。

    默认**移入** `<root>/.toolbox-trash/<basename>-<stamp>/`（manifest schema=3，
    文件条目带内容 sha256，可 `restore()` 校验撤销）；`delete=True` 才永久删。绝不触碰 `000*` 成品、最终
    `.tex/.pdf/.sty/.bib` 与 `目录设置.json`（候选表只认白名单模式）。
    """
    emit = log or (lambda _s: None)
    fails = failures if failures is not None else []
    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, ""
    cands = toc_work_candidates(root, keep_reports=keep_reports,
                                all_reports=all_reports)
    if not cands:
        return 0, ""

    stamp = _stamp()
    trash_root = "" if delete else trash_dir(root, stamp)
    moved: list[str] = []
    total_size = 0
    toc_dir = os.path.join(root, TOC_WORK_DIR)
    for rel, _kind in cands:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        try:
            total_size += os.path.getsize(full)
        except OSError:
            pass
        if dry_run:
            emit(("Will delete: " if delete else "Will move: ") + rel)
            moved.append(rel)
            continue
        try:
            if delete:
                os.remove(full)
                emit(f"Deleted: {rel}")
            else:
                target = os.path.join(trash_root, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(full, target)
                emit(f"Moved: {rel}")
            moved.append(rel)
        except OSError as e:
            emit(f"Error {'deleting' if delete else 'moving'} {rel}: {e}")
            fails.append((rel, str(e)))

    # 空壳的 .toolbox-tmp 顺手收掉；里面还有东西/权限不允许时静默保留
    if not dry_run and os.path.isdir(toc_dir):
        try:
            if not os.listdir(toc_dir):
                os.rmdir(toc_dir)
        except OSError:
            pass

    if dry_run:
        emit(f"（预演）将{'删除' if delete else '移入回收目录'} {len(moved)} 个文件，"
             f"共 {_human_size(total_size)}。")
        return len(moved), trash_root

    if moved and not delete:
        _write_manifest(trash_root, root, stamp, "tocwork",
                        [(rel, "file") for rel in moved])
        emit(f"已移入回收目录 {len(moved)} 个文件。")
    elif moved:
        emit(f"已删除 {len(moved)} 个文件。")
    return len(moved), trash_root if moved else ""
