"""2026-11-17 修复（R1）：`env_for_run` 不得用**未校验**的笔记仓路径覆盖外部环境。

用户报障：设置页「私有笔记仓」填成 `<笔记仓>/bib_db`（没有 `cases/`），
之后引用库整条链全部「找不到私有笔记仓」——哪怕启动它的终端里
`SLCN_NOTES` 已经指向正确的笔记仓根。

根因两段拼起来才致命：
1. `engine_bridge.py: env_for_run()` 从 `dict(os.environ)` 起手（外部值本来就在），
   但 `if notes: env["SLCN_NOTES"] = notes` 是**赋值不是 setdefault** ——
   未校验的用户输入把外部正确值覆盖掉了；
2. 引擎 `Engine/slcn/paths.py: notes_root()` 对 `SLCN_NOTES` 是"显式优先且严格"：
   设了但里面没有 `cases/` 就返回 None，**且不再回退自动探测**。

修法（与 `app/main.py: _apply_notes_repo()` 同一口径，那里刻意不写 os.environ）：
只有 `isdir(<notes>/cases)` 通过才写进子进程环境，否则保留外部已设的值。

本测试锁四件事（纯函数 + 假路径，不跑引擎、不建窗口）：
1. 非法 notes **不覆盖**外部已设的正确 `SLCN_NOTES`；
2. 合法 notes **照旧覆盖**（用户显式填对了就要生效，不能因为保守而失效）；
3. 没有外部值 + 非法 notes → 环境里**不出现** `SLCN_NOTES`（让引擎走自动探测）；
4. 判据与引擎逐字一致：`<notes>/cases` 必须是**目录**，光有同名文件不算；
   `~` 与相对路径按 `abspath(expanduser())` 解析（引擎侧同款）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import engine_bridge as EB

ENV_KEY = "SLCN_NOTES"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """别让跑测试的这台机器上真实的 SLCN_NOTES 干扰判断。"""
    monkeypatch.delenv(ENV_KEY, raising=False)


# ── 1. 核心回归：非法 notes 不许覆盖外部正确值 ────────────────────────────
def test_invalid_notes_does_not_clobber_external_env(tmp_path, monkeypatch):
    good = tmp_path / "notes-good"
    (good / "cases").mkdir(parents=True)
    bad = good / "bib_db"                      # 用户实际填错的那一层：没有 cases/
    bad.mkdir()

    monkeypatch.setenv(ENV_KEY, str(good))
    env = EB.env_for_run(notes=str(bad))

    assert env[ENV_KEY] == str(good), (
        "非法 notes 覆盖了外部已设的正确 SLCN_NOTES —— 引擎会因此判「找不到笔记仓」")


# ── 2. 合法 notes 照旧生效（修复不能把正确用法也一起废掉） ────────────────
def test_valid_notes_still_overrides(tmp_path, monkeypatch):
    old = tmp_path / "notes-old"
    (old / "cases").mkdir(parents=True)
    new = tmp_path / "notes-new"
    (new / "cases").mkdir(parents=True)

    monkeypatch.setenv(ENV_KEY, str(old))
    env = EB.env_for_run(notes=str(new))

    assert env[ENV_KEY] == str(new), "用户显式填了合法笔记仓，应当覆盖外部值"


def test_valid_notes_used_when_no_external_env(tmp_path):
    good = tmp_path / "notes"
    (good / "cases").mkdir(parents=True)

    env = EB.env_for_run(notes=str(good))

    assert env[ENV_KEY] == str(good)


# ── 3. 非法 notes + 无外部值 → 不写这个键（交给引擎自动探测） ─────────────
def test_invalid_notes_without_external_env_sets_nothing(tmp_path):
    bad = tmp_path / "not-notes"
    bad.mkdir()

    env = EB.env_for_run(notes=str(bad))

    assert ENV_KEY not in env, (
        "非法路径被写进环境会让引擎「显式优先且严格」直接失败，"
        "连自动探测的机会都没有")


# ── 4. 判据与引擎一致：要 cases/ 目录，不要同名文件；支持 ~ 展开 ──────────
def test_cases_must_be_directory_not_file(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "cases").write_text("这不是目录", encoding="utf-8")

    env = EB.env_for_run(notes=str(notes))

    assert ENV_KEY not in env, "`cases` 是文件不是目录，不算笔记仓"


def test_tilde_and_relative_paths_are_expanded(tmp_path, monkeypatch):
    good = tmp_path / "notes"
    (good / "cases").mkdir(parents=True)
    # os.path.expanduser 在每个平台读取的家目录变量不同：POSIX 读 HOME，
    # Windows(ntpath) 读 USERPROFILE（HOME 被忽略）。产品校验与引擎解析都用
    # expanduser，所以这里必须按平台设置，否则 Windows 上 "~" 展开不到 tmp_path，
    # 校验失败 -> SLCN_NOTES 根本不写入 -> 测试误报 KeyError（非产品缺陷）。
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    else:
        monkeypatch.setenv("HOME", str(tmp_path))

    env = EB.env_for_run(notes="~/notes")
    assert env[ENV_KEY] == "~/notes", "校验通过时应原样写入（与引擎 expanduser 对齐）"

    # 相对路径：校验按 abspath 做，别因为 cwd 不同而误判
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_KEY, raising=False)
    env = EB.env_for_run(notes=os.path.join(".", "notes"))
    assert env[ENV_KEY] == os.path.join(".", "notes")


# ── 5. 空 notes 一律不动外部值（老行为，别退化） ─────────────────────────
def test_empty_notes_keeps_external_env(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "/somewhere/else")
    env = EB.env_for_run()
    assert env[ENV_KEY] == "/somewhere/else"


# ── 6. R3：打包态探测必然失败，提示要给「能执行」的动作 ────────────────────
# 结论（实测见交付）：打包版是 PyInstaller one-dir，`engine_root()` 落在
# `<包>.app/Contents/Resources/SLCNEngine/_internal`，向上 4 层只到 `Contents/`，
# 同级候选也只到 `Contents/Resources/` —— **探测范围被冻结在包内**，包里没有笔记仓
# 就必然找不到，与这台机器上有没有笔记仓无关，也与 cwd 无关。
# 所以这里**不造探测路径**（包里根本没有），只锁提示文案：必须①不再说「本机没有笔记仓」
# 这种假话，②给出 GUI 用户真能做的动作（设置页字段），③保留 CLI/CI 的环境变量路。
def _engine_paths():
    """按需加载 `Engine/slcn/paths.py`（纯 stdlib，不需要引擎依赖）。

    ⚠️ 刻意**不**往 `sys.path` 里插 `Engine/`：那里有 `compileall.py` / `toc.py`
    这类与标准库同名的顶层模块，插进去会污染整个 pytest 会话。
    `paths.py` 只 import `os`/`re`，按文件路径加载即可。
    """
    import importlib.util

    engine = Path(__file__).resolve().parents[2] / "Engine"
    source = engine / "slcn" / "paths.py"
    if not source.is_file():
        pytest.skip(f"没有引擎树：{engine}")
    spec = importlib.util.spec_from_file_location("_slcn_paths_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_layout_never_discovers_and_says_what_to_do(tmp_path, monkeypatch):
    engine_paths = _engine_paths()
    # 仿打包结构：<包>.app/Contents/Resources/SLCNEngine/_internal/slcn/paths.py
    internal = tmp_path / "编辑部工具箱.app/Contents/Resources/SLCNEngine/_internal"
    (internal / "slcn").mkdir(parents=True)
    fake = internal / "slcn" / "paths.py"
    fake.write_text("# 冻结产物\n", encoding="utf-8")

    monkeypatch.setattr(engine_paths, "__file__", str(fake))
    monkeypatch.delenv(ENV_KEY, raising=False)

    # 不 patch `engine_repo_root()`：让真代码自己走。`engine_root()` 落在 `_internal`，
    # 向上 4 层只在 `_internal` / `SLCNEngine` / `Resources` / `Contents` 里找 `.git`（都没有）
    # → 退路 `dirname(engine_root())` = `SLCNEngine` → 两个同级候选都落在
    # `Contents/Resources/` 和 `Contents/Resources/SLCNEngine/` 里，**出不了包**。
    assert engine_paths.engine_root() == str(internal)
    assert engine_paths.engine_repo_root() == str(internal.parent)
    assert engine_paths.notes_root() is None, "打包态不该凭空探到包外的笔记仓"

    text = engine_paths.describe_notes()
    assert "本机没有笔记仓" not in text, (
        "对打包用户说「本机没有笔记仓」是假话：包内探测范围有限，机器上有没有仓它都看不到")
    assert "设置" in text, "必须给出 GUI 用户能做的动作（设置页填笔记仓）"
    assert ENV_KEY in text, "CLI/CI 用户仍需知道环境变量这条"
    assert "cases/" in text, "判据要写清：填的是含 cases/ 的那一层"


def test_wrong_env_hint_points_at_root_not_bib_db(tmp_path, monkeypatch):
    engine_paths = _engine_paths()
    bad = tmp_path / "EditToolBox-notes" / "bib_db"
    bad.mkdir(parents=True)

    monkeypatch.setenv(ENV_KEY, str(bad))
    text = engine_paths.describe_notes()

    assert str(bad) in text, "要回显用户实际填的那个路径，他才认得出自己填错了"
    assert "cases/" in text
    assert "bib_db/" in text, "最常见的错值就是它下一层，提示要点名"
