r"""稿件库锚点：**唯一**一处决定"稿件（期号目录）在哪"。

为什么需要它：2026-10-16 起开发主场搬到公开仓（`EditToolBoxStudiesInLogic`），
而稿件库仍在私有仓（`Studies_in_Logic_Database`）。引擎的**重活验收**
（全库普查 `docx_audit.py`、全库回归 `corpus.py`、代表稿 `run_poc.py`）必须能读到稿件，
但**没有稿件也必须能跑**——公开仓的日常开发、CI、外壳打包都不该依赖它。

解析顺序（第一个"看起来像稿件库"的命中）：

  1. **环境变量 `SLCN_MANUSCRIPTS`** —— 最明确，CI 或多份稿件库时用它；
  2. **从本文件向上 4 层** —— 老布局：开发就在稿件仓里（`<稿件仓>/Engine/slcn/`）；
  3. **引擎仓根的同级目录** —— 新布局：两个仓并排
     （`GitHub/EditToolBoxStudiesInLogic/` 与 `GitHub/Studies_in_Logic_Database/`），无需任何配置。

判据是"该目录下真的有 `20\d\d年第N期(…)` 这样的期号目录"，而不是"目录存在" ——
否则会把引擎仓自己（它也含 `编辑笔记/模板文件/`）误当成稿件库。
"""

from __future__ import annotations

import os
import re

#: 期号目录名：`2026年第5期(90)` / `2022年第6A期(67A)`
ISSUE_RE = re.compile(r"^\d{4}年第")

#: 环境变量名（文档、工具提示、测试都用它）
ENV_VAR = "SLCN_MANUSCRIPTS"

#: 同级目录里可能出现的稿件仓目录名（新布局；按顺序试）
SIBLING_NAMES = ("Studies_in_Logic_Database", "EditToolBoxStudiesInLogic-manuscripts")

#: 私有笔记仓（案例法 / 交接说明 / 冻结基线）的环境变量与目录名
NOTES_ENV = "SLCN_NOTES"
NOTES_SIBLING = "EditToolBox-notes"


def rel(path: str, start: str | None = None) -> str:
    r"""**跨盘符安全**的相对路径：算不出相对路径就回落成绝对路径，**绝不抛异常**。

    为什么需要它：`os.path.relpath()` 在两个不同盘符 / 挂载点之间会抛
    `ValueError: path is on mount 'C:', start on mount 'D:'`。调用它的地方几乎都是**汇报**
    （「⑦ 写出文件」把产物路径打给编辑看），于是一句汇报就把整个转换打断：
    2026-10-16 windows-latest 上 `%TEMP%` 在 C:、仓库在 D: → `convert.py` 退出 2，
    release 链路 15 次运行全红（案例第 103 条）。编辑真会遇到同一个坑：
    工具装在 C:、稿件与产物放 D:。

    判据（**行为保持**）：同盘符时与 `os.path.relpath` 逐字节相同（POSIX 上永远走不到
    except）；只有「算不出来」这一种情况从「抛异常」改成「给绝对路径」——
    汇报里给绝对路径比崩掉有用得多。**交付模块里不要再直接调 `os.path.relpath`**，
    静态守卫在 `Engine/tests/test_platform.py` 的 (f) 组。
    """
    try:
        return os.path.relpath(path, start)
    except ValueError:          # 跨盘符（Windows）/ 跨挂载点：本来就没有相对路径可言
        return os.path.abspath(path)


def looks_like_manuscripts(path: str) -> bool:
    """该目录下**真的有期号目录**才算稿件库。"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(ISSUE_RE.match(n) and os.path.isdir(os.path.join(path, n)) for n in names)


def _parents(start: str, levels: int = 4) -> list[str]:
    out: list[str] = []
    d = start
    for _ in range(levels):
        d = os.path.dirname(d)
        if not d or d == os.path.dirname(d):
            break
        out.append(d)
    return out


def engine_root() -> str:
    """引擎目录（`…/Engine`）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def engine_repo_root() -> str:
    """**含引擎的那个 git 仓**的根目录。

    为什么要它而不是"Engine 的上一级"：老布局里引擎在 `<稿件仓>/Engine`，
    上一级是 `EditToolBox` 而不是仓根 —— 拿它去算"同级目录"会得到
    `<稿件仓>/EditToolBox-notes`，而笔记仓其实在 `<GitHub>/EditToolBox-notes`（实测踩到）。
    新布局（公开仓）里 `Engine` 的上一级就是仓根，两种布局用同一个函数才不会各错一次。
    """
    d = engine_root()
    for _ in range(4):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(engine_root())        # 退路：Engine 的上一级


def candidates() -> list[str]:
    """按解析顺序列出所有候选路径（含来源说明，供 `describe()` 与测试用）。"""
    here = os.path.dirname(os.path.abspath(__file__))          # …/Engine/slcn
    out: list[str] = []

    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        out.append(os.path.abspath(os.path.expanduser(env)))

    out += _parents(here, 4)                                    # 老布局：稿件仓 → Engine/slcn

    base = os.path.dirname(engine_repo_root())                  # 引擎仓的上一级（通常是 GitHub/）
    for name in SIBLING_NAMES:                                  # 新布局：两仓并排
        out.append(os.path.join(base, name))

    # 去重且保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def manuscripts_root() -> str | None:
    """稿件库根目录；找不到返回 None（调用方**必须**优雅跳过，不许崩）。

    ⚠️ **显式配置优先且严格**：一旦设了 `SLCN_MANUSCRIPTS`，就以它为准 ——
    指错了就返回 None（配合 `describe()` 说明原因），**不再往下回退**。
    否则"我明明指了另一个稿件库，程序却悄悄用了旧的"这种事迟早发生。
    """
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        return p if looks_like_manuscripts(p) else None
    for c in candidates():
        if looks_like_manuscripts(c):
            return c
    return None


def describe() -> str:
    """给工具打印的一句话：从哪找到的，或者为什么没找到（含怎么配）。"""
    env = os.environ.get(ENV_VAR, "").strip()
    found = manuscripts_root()
    if found:
        how = f"环境变量 {ENV_VAR}" if env and os.path.abspath(os.path.expanduser(env)) == found \
            else "自动探测"
        return f"稿件库：{found}（{how}）"
    if env:
        return (f"⚠️ {ENV_VAR} 指向 {env}，但那里没有 `20xx年第N期(…)` 这样的期号目录 —— "
                "该功能会跳过。")
    return ("本机没有稿件库（该功能需要各期作者来稿），已跳过。"
            f"若稿件库在别处，设 {ENV_VAR}=<稿件仓路径> 再跑。")


def notes_root() -> str | None:
    """私有笔记仓（案例法 / HANDOFF / 冻结基线）的根目录；找不到返回 None。

    与稿件库同样的规矩：**显式配置优先且严格**（`SLCN_NOTES`），找不到就交给调用方优雅跳过。
    判据是"里面有 `cases/` 目录" —— 光有同名目录不算。
    """
    env = os.environ.get(NOTES_ENV, "").strip()
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        return p if os.path.isdir(os.path.join(p, "cases")) else None

    base = os.path.dirname(engine_repo_root())
    for cand in (os.path.join(base, NOTES_SIBLING),
                 os.path.join(engine_repo_root(), NOTES_SIBLING)):
        if os.path.isdir(os.path.join(cand, "cases")):
            return cand
    return None


def describe_notes() -> str:
    """给工具打印的一句话：笔记仓在哪，或者为什么没找到。"""
    env = os.environ.get(NOTES_ENV, "").strip()
    found = notes_root()
    if found:
        return f"笔记仓：{found}" + ("（环境变量）" if env else "（自动探测）")
    if env:
        return f"⚠️ {NOTES_ENV} 指向 {env}，但那里没有 `cases/` 目录。"
    return (f"本机没有笔记仓（案例法/交接说明/冻结基线在那里）。"
            f"若在别处，设 {NOTES_ENV}=<笔记仓路径>。")


def require(tool: str, log=print) -> str | None:
    """重活工具的统一入口：找到就返回路径；没找到就打一行说明并返回 None。

    ⚠️ 返回 None 时调用方要**正常退出（0）**，不要报错 —— 公开仓/CI 上本来就没有稿件库。
    """
    root = manuscripts_root()
    if root is None:
        log(f"≡ {tool}：{describe()}")
        return None
    return root
