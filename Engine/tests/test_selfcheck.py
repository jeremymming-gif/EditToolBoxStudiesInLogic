#!/usr/bin/env python3.14
r"""第二十二批（第 122 条）工具的回归测试：案例库治理 + 引擎自查。

覆盖：
  ① `tools/gen_case_index.py`：日期列、状态分类计数、`DIGEST.md` 生成与 `--check`；
  ② `tools/case_audit.py`：编号连续 / 状态目标存在 / INDEX 过期 三类错误 + 相似度警告；
  ③ `Engine/tools/selfcheck.py`：嵌套保护、纯静态检查端到端、`case_audit` 接线；
  ④ 第四十四批③a（O9）反例：App 包内一致性已从 AUTO 移出 ——
     ③ 用 `--skip-app` 拆掉「test_selfcheck × App 新鲜度」循环依赖；
     `gen_docs.py --cmp-app`（build.sh 末尾守卫）能抓「包内过期」；AUTO 数字不变。

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
# 第四十四批③a（O9）：**必须**加 `--skip-app`——本套件会在 gen_docs 算 AUTO 时被跑，
# 而「引擎改了、App 还没重建」是批内常态；不加就会把 App 瞬态 FAIL 写进 AUTO，
# 逼出「gen_docs×2 + build×2」循环。包内一致性的守卫在 build.sh 末尾（--cmp-app）。
r = subprocess.run([sys.executable, SELF, "--no-cases", "--skip-app"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
out = (r.stdout or "") + (r.stderr or "")
check("③ selfcheck 纯静态端到端 rc=0", r.returncode == 0, out[-500:])
check("③ selfcheck 输出「自查通过」", "自查通过" in out)
check("③ 嵌套调用时套件/文档被 SKIP（防递归）",
      "SKIP" in out and "防递归" in out)
check("③ App 包内检查被 --skip-app 跳过（O9）", "SKIP  App 包内一致性（--skip-app" in out)

# ── ④ O9 反例（第四十四批③a）：拆掉 test_selfcheck × App 新鲜度循环依赖 ─────
# 判据（三反例）：
#   (A) App 过期时，③ 用的命令（--skip-app）仍全绿；且同一过期包**不跳过**时 selfcheck 能红
#       —— 证明「拆依赖」不是把检查削弱。
#   (B) build.sh 末尾的守卫（gen_docs.py --cmp-app）对「包内过期」报错、对干净包 PASS；
#       用假目录模拟，不需要真 build。
#   (C) AUTO 数字不变：App 行移出后，`gen_docs --check --no-test-runs` 仍 PASS（非测试行一致）；
#       且新算的 AUTO 块不再含「App 包内一致性」行。
import gen_docs as G  # noqa: E402

GEN_DOCS = os.path.join(_ENGINE, "tools", "gen_docs.py")


def fake_app(name, corrupt=None):
    """在 TMP 下造一个「包内 Engine」：按 APP_FILES 从仓库拷贝，可选弄坏一个文件。"""
    root = os.path.join(TMP, name)
    shutil.rmtree(root, ignore_errors=True)
    for rel in G.APP_FILES:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(_ENGINE, rel), dst)
    if corrupt:
        with open(os.path.join(root, corrupt), "a", encoding="utf-8") as f:
            f.write("\n# 反例探针：包内已过期\n")
    return root


good_app = fake_app("app_good")
stale_app = fake_app("app_stale", corrupt="slcn/sources.py")

# (B) build.sh 守卫：干净包 PASS（28/28），过期包报错并点名文件
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", good_app])
check("④B1 包内干净 → --cmp-app PASS（28/28）", rc == 0 and "28/28" in out, out[-200:])
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", stale_app])
check("④B2 包内过期 → --cmp-app 报错（exit 1 + 点名 slcn/sources.py）",
      rc == 1 and "❌" in out and "slcn/sources.py" in out, out[-300:])
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", os.path.join(TMP, "no_such_app")])
check("④B3 包目录不存在 → --cmp-app 也报错（不静默通过）",
      rc == 1 and "❌" in out, out[-200:])

# (A) App 过期时：③ 的命令（--skip-app）全绿；不跳过则红（反证检查没被削弱）
env_stale = dict(os.environ, SLCN_SELFCHECK_NESTED="1", SLCN_APP_DIR=stale_app,
                 PYTHONIOENCODING="utf-8")
r = subprocess.run([sys.executable, SELF, "--no-cases", "--skip-app"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env_stale)
out = (r.stdout or "") + (r.stderr or "")
check("④A1 App 过期 + --skip-app → selfcheck rc=0（循环依赖已拆）",
      r.returncode == 0 and "自查通过" in out, out[-400:])
r = subprocess.run([sys.executable, SELF, "--no-cases"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env_stale)
out = (r.stdout or "") + (r.stderr or "")
check("④A2 反证：同一过期包、不带 --skip-app → selfcheck rc=1（检查本身没被削弱）",
      r.returncode == 1 and "App 包内一致性" in out, out[-400:])

# (C) AUTO 数字不变：App 行移出后非测试行仍一致；且新块不再含 App 行
body = G.build_block(run_test_suites=False)[0]
check("④C1 新算的 AUTO 块不再含「App 包内一致性」行", "App 包内一致性" not in body)
if G.AUDIT_CSV.is_file() and G.CORPUS_CSV.is_file():
    # 有稿件库产物时 README 的普查/corpus 行才是可比的（CI/新克隆没有这些产物，跳过）
    rc, out = run([sys.executable, GEN_DOCS, "--check", "--no-test-runs"])
    check("④C2 移出 App 行后 AUTO 非测试行仍全部一致（--check --no-test-runs PASS）",
          rc == 0, out[-400:])
else:
    skip("④C2 AUTO 非测试行 strict 比对", "本机无 out/ 产物（CI/新克隆形态），跳过")

shutil.rmtree(TMP, ignore_errors=True)
print("=" * 70)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
