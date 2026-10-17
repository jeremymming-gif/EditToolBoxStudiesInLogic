#!/usr/bin/env python3.14
r"""第二十二批（第 122 条）工具的回归测试：案例库治理 + 引擎自查。

覆盖：
  ① `tools/gen_case_index.py`：日期列、状态分类计数、`DIGEST.md` 生成与 `--check`；
  ② `tools/case_audit.py`：编号连续 / 状态目标存在 / INDEX 过期 三类错误 + 相似度警告；
  ③ `Engine/tools/selfcheck.py`：嵌套保护、纯静态检查端到端、`case_audit` 接线。

全部离线、不需要稿件库；平台中立（Windows/mac 的路径分隔符都在断言里归一到 `/`）。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_ENGINE, "tools"),
           os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FAILS = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="slcn_case_tools_")


def _flat(text) -> str:
    """把多行详情压成一行：子进程输出里可能有以 `FAIL`/`PASS` 开头的行，
    若原样打印会被 `gen_docs.py` / 套件统计误当成断言行（第二十二批实测）。"""
    return " ⏎ ".join(str(text).replace("\r", "").splitlines())


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + _flat(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(cmd, env=None):
    e = dict(os.environ)
    e.update(env or {})
    e["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e, cwd=_REPO)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


GEN = os.path.join(_REPO, "tools", "gen_case_index.py")
AUDIT = os.path.join(_REPO, "tools", "case_audit.py")
SELF = os.path.join(_ENGINE, "tools", "selfcheck.py")

CASE_A = """# 案例法（测试用）

### 1. 第一条（2026-10-17，第二十二批）

> 状态：已发布：v1.1.1

**背景**：测试条目一。这里是正文，包含「测试」关键词。

### 2. 第二条（2026-10-17，第二十二批）

**修复**：测试条目二。正文里也有解析与门禁相关的词。
"""


def new_cases_dir(name):
    d = os.path.join(TMP, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    write(os.path.join(d, "01-30.md"), CASE_A)
    return d


# ── ① 索引生成：日期 / 状态分类 / DIGEST ──────────────────────────────────
d1 = new_cases_dir("gen")
rc, out = run([sys.executable, GEN, "--cases", d1])
check("① gen_case_index 生成成功", rc == 0, out[-200:])
idx = open(os.path.join(d1, "INDEX.md"), encoding="utf-8").read()
check("① INDEX 含日期列与日期值", "2026-10-17" in idx and "| 条 | 日期 |" in idx)
check("① INDEX 状态分类计数生效（已发布 1 条）", "已发布 1 条" in idx, idx.splitlines()[3])
dig = os.path.join(d1, "DIGEST.md")
check("① DIGEST.md 已生成", os.path.isfile(dig))
if os.path.isfile(dig):
    dtext = open(dig, encoding="utf-8").read()
    check("① DIGEST 按主题分组（含「测试」组）", "## 测试" in dtext, dtext[:200])
check("① --check 在索引新鲜时通过",
      run([sys.executable, GEN, "--cases", d1, "--check"])[0] == 0)

# ── ② case_audit：好库 / 编号缺号 / 状态目标失效 / 索引过期 / 相似度警告 ──
rc, out = run([sys.executable, AUDIT, "--cases", d1, "--check"])
check("② 正常案例库审过（rc=0）", rc == 0, out[-300:])
check("② 正常库报告「无错误」", "无错误" in out)

d2 = new_cases_dir("badnum")
write(os.path.join(d2, "01-30.md"), CASE_A.replace("### 2.", "### 9."))
rc, out = run([sys.executable, AUDIT, "--cases", d2, "--check"])
check("② 编号缺号 → rc=1 且指出缺号", rc == 1 and "缺号" in out, out[-400:])

d3 = new_cases_dir("badstatus")
write(os.path.join(d3, "01-30.md"),
      CASE_A.replace("> 状态：已发布：v1.1.1", "> 状态：已被第 99 条取代"))
rc, out = run([sys.executable, AUDIT, "--cases", d3, "--check"])
check("② 状态指向不存在的条目 → rc=1", rc == 1 and "第 99 条" in out, out[-400:])

d4 = new_cases_dir("stale")
run([sys.executable, GEN, "--cases", d4])
write(os.path.join(d4, "01-30.md"),
      CASE_A.replace("第一条", "第一条（标题已改）"))
rc, out = run([sys.executable, AUDIT, "--cases", d4, "--check"])
check("② INDEX 过期 → rc=1", rc == 1 and "过期" in out, out[-300:])

d5 = new_cases_dir("sim")
dup_body = "**修复**：这是一段完全相同的正文。" * 3
write(os.path.join(d5, "01-30.md"),
      CASE_A + "\n### 3. 第三条（2026-10-17，第二十二批）\n\n" + dup_body
      + "\n### 4. 第四条（2026-10-17，第二十二批）\n\n" + dup_body)
run([sys.executable, GEN, "--cases", d5])
rc, out = run([sys.executable, AUDIT, "--cases", d5, "--check"])
check("② 相似度只警告、不判错（rc=0 且出现 WARN）", rc == 0 and "相似度" in out, out[-400:])

# ── ③ selfcheck：嵌套保护 + 纯静态端到端 ─────────────────────────────────
import selfcheck as S  # noqa: E402

nums = S._case_numbers(d1)
check("③ selfcheck._case_numbers 解析 1/2", {1, 2} <= nums, sorted(nums)[:6])
check("③ selfcheck 的案例引用正则认「第 12 条」",
      [int(m.group(1)) for m in S.CASE_REF_RE.finditer("见第 12 条与第 0 条")] == [12, 0])

env = dict(os.environ, SLCN_SELFCHECK_NESTED="1", PYTHONIOENCODING="utf-8")
r = subprocess.run([sys.executable, SELF, "--no-cases"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
out = (r.stdout or "") + (r.stderr or "")
check("③ selfcheck 纯静态端到端 rc=0", r.returncode == 0, out[-500:])
check("③ selfcheck 输出「自查通过」", "自查通过" in out)
check("③ 嵌套调用时套件/文档被 SKIP（防递归）",
      "SKIP" in out and "防递归" in out)

shutil.rmtree(TMP, ignore_errors=True)
print("=" * 70)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
