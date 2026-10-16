#!/usr/bin/env python3
"""稿件库锚点（第 100 条）：开发主场搬到公开仓后，重活工具怎么找到稿件库。

要钉住的三件事：
  (a) **判据是"真的有期号目录"**，不是"目录存在" —— 否则引擎仓自己（也含
      `编辑笔记/模板文件/`）会被误当成稿件库；
  (b) **显式配置优先且严格**：设了 `SLCN_MANUSCRIPTS` 就以它为准，指错了返回 None
      （不许悄悄回退到别的库），并给出可读的原因；
  (c) **没有稿件库时重活工具优雅退出（0）**，不是报错 —— 公开仓与 CI 上本来就没有。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import paths as P  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def fake_manuscripts(root: str, *, issues: bool = True) -> str:
    """造一个"像/不像"稿件库的目录。"""
    if issues:
        os.makedirs(os.path.join(root, "2026年第5期(90)", "01 Lu_Wang"), exist_ok=True)
    os.makedirs(os.path.join(root, "编辑笔记", "模板文件"), exist_ok=True)   # 引擎仓也有这个
    return root


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 判据：真的有期号目录才算稿件库 ──")
tmp = tempfile.mkdtemp(prefix="slcn_paths_")
with_issues = fake_manuscripts(os.path.join(tmp, "有稿件"))
only_tpl = fake_manuscripts(os.path.join(tmp, "只有模板"), issues=False)
check("(a) 有 `2026年第5期(90)/` → 是稿件库", P.looks_like_manuscripts(with_issues))
check("(a) 只有 `编辑笔记/模板文件/`（引擎仓也长这样）→ **不是**稿件库",
      not P.looks_like_manuscripts(only_tpl))
check("(a) 目录不存在 → 不是", not P.looks_like_manuscripts(os.path.join(tmp, "没有这个目录")))
check("(a) 期号目录是**文件**而不是目录 → 不算",
      not P.looks_like_manuscripts(
          (lambda d: (os.makedirs(d, exist_ok=True),
                      open(os.path.join(d, "2026年第5期(90)"), "w").close(), d)[2])(os.path.join(tmp, "文件冒充"))))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 环境变量：显式优先且严格 ──")
_env = os.environ.get(P.ENV_VAR)


def with_env(value: str | None):
    """临时设置/清除环境变量（yield 后恢复）。"""
    class _Ctx:
        def __enter__(self):
            self.old = os.environ.get(P.ENV_VAR)
            if value is None:
                os.environ.pop(P.ENV_VAR, None)
            else:
                os.environ[P.ENV_VAR] = value

        def __exit__(self, *a):
            if self.old is None:
                os.environ.pop(P.ENV_VAR, None)
            else:
                os.environ[P.ENV_VAR] = self.old
    return _Ctx()


with with_env(with_issues):
    check("(b) 指向真的稿件库 → 用它，且说明写「环境变量」",
          P.manuscripts_root() == with_issues and "环境变量" in P.describe(),
          P.describe())
with with_env(only_tpl):
    check("(b) 指向一个**不是**稿件库的目录（如引擎仓自己）→ None，**不回退**",
          P.manuscripts_root() is None)
    check("(b) 原因里点名环境变量与去向，便于自查",
          P.ENV_VAR in P.describe() and only_tpl in P.describe(), P.describe())
with with_env("/tmp/绝对不存在的稿件库_zzz"):
    check("(b) 指向不存在 → None（不回退到自动探测）", P.manuscripts_root() is None)
with with_env(None):
    auto = P.manuscripts_root()
    check("(b) 不设环境变量 → 自动探测：要么找到真稿件库，要么干净地 None（公开仓里就是这样）",
          auto is None or P.looks_like_manuscripts(auto),
          f"{auto}")
    check("(b) 自动探测的候选里含「同级目录」（新布局：两仓并排）",
          any(str(ENGINE.parent.parent) == os.path.dirname(c) or
              "Studies_in_Logic_Database" in c for c in P.candidates()),
          f"{len(P.candidates())} 个候选")

print("\n── (c) require()：找到返回路径，找不到打一行说明并返回 None ──")
lines: list[str] = []
with with_env("/tmp/绝对不存在的稿件库_zzz"):
    check("(c) 找不到 → None", P.require("测试工具", log=lines.append) is None)
    check("(c) 并且打了一行说明（工具不该静默跳过）",
          len(lines) == 1 and "测试工具" in lines[0] and P.ENV_VAR in lines[0], f"{lines}")
with with_env(with_issues):
    lines.clear()
    check("(c) 找得到 → 返回路径且**不打**跳过说明",
          P.require("测试工具", log=lines.append) == with_issues and not lines)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 重活工具在没有稿件库时：退出码 0 + 一句说明 ──")
ENV = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"), SLCN_MANUSCRIPTS="/tmp/不存在_zzz")


def run(script: str, *args):
    return subprocess.run([sys.executable, str(ENGINE / script), *args],
                          capture_output=True, text=True, env=ENV, cwd=str(ENGINE))


for script, args in (("corpus.py", []), ("docx_audit.py", ["--out-dir", tempfile.mkdtemp()])):
    r = run(script, *args)
    check(f"(d) `{script}` 没有稿件库 → 退出码 0（不是错误）", r.returncode == 0, f"rc={r.returncode}")
    check(f"(d) `{script}` 打了一句可读的说明",
          "没有稿件库" in r.stdout or P.ENV_VAR in r.stdout, (r.stdout or r.stderr).strip()[:90])

r = run("run_poc.py", "--list-templates")
check("(d) `run_poc.py --list-templates` **不需要**稿件库（它只列模板）",
      r.returncode == 0 and "可用模板" in r.stdout, (r.stdout or "").strip()[:60])
r = run("run_poc.py")
# 注意提示语有两种形态：没配环境变量时是「本机没有稿件库…」，配错时是「⚠️ …指向…会跳过」。
# 断言别锁死其中一种（第一版就锁死了，假红一次）。
check("(d) `run_poc.py` 真要跑代表稿时 → 跳过并退出 0",
      r.returncode == 0 and ("没有稿件库" in r.stdout or P.ENV_VAR in r.stdout)
      and "跳过" in r.stdout, f"rc={r.returncode} {(r.stdout or '').strip()[:80]}")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if _env is not None:
    os.environ[P.ENV_VAR] = _env
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
