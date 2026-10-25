#!/usr/bin/env python3.14
r"""批82 / B-159 回归测试：`gen_docs` 的「红线只读快照」行**确定性**（写模式幂等）。

批81 根因（笔记仓 `drafts/b81-b159-auto-state-analysis.md`）：`--check` 侧早就把这一行
整行归一化（`GIT_LINE` → `_GIT_PLACEHOLDER`，**从不参与任何判定**），但写入侧仍把本机
`.git/index` mtime / `HEAD` / `git status` 行数 materialize 进**被提交的** README →
不参与判定，却保证每次写入 1 行 diff（批80/81 的 notes-only 批次只能写完再 revert）。

本测试钉住四件事（hermetic：fixture README + tempfile；不写真实仓库、不读稿件库）：

(a) `git_block()` 恰好 1 行、两次调用**逐字相同**，且**不含**本机状态字段；
(b) 该行经 `--check` 归一化后**等于占位符**（校验语义不变；历史 README 的旧格式行同样归一化）；
(c) `_flush_block` 在 clean 状态下**连写两次 → 第二次不改写、字节不变**（幂等 = B-159 判据）；
(d) 本机现况仍留证：落 `out/gen_docs-state.txt`（fixture 内），含 mtime / HEAD / status 三行；
(e) 快照写盘失败只告警、**不抛异常**（不影响计算与退出码）。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
for _p in (str(ENGINE), str(ENGINE / "tools"), str(ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TMP = Path(tempfile.mkdtemp(prefix="slcn_b159_"))
# ⚠️ 必须在 import gen_docs 之前设：AUTO_INPUTS 在模块导入时读取环境变量（同 test_b74）。
os.environ["SLCN_AUTO_INPUTS"] = str(TMP / "out" / "auto-inputs.json")
os.environ["SLCN_RUN_RECORD"] = "0"          # 纯测试：不写 run manifest 证据

import gen_docs as G  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)


# ── (a) 确定性：同一行、逐字相同、不含本机状态 ──────────────────────────────
_a = G.git_block()
_b = G.git_block()
check("(a) `git_block()` 恰好 1 行（AUTO 块结构不变）", len(_a) == 1, f"{len(_a)} 行")
check("(a) 两次调用**逐字相同**（确定性 = 不再随本机状态漂移）", _a == _b)
LINE = _a[0]
check("(a) 行首仍是 AUTO 的「红线只读快照」锚点（块结构与 --check 依赖不变）",
      LINE.startswith("**红线只读快照**"), LINE[:40])
_bad = {
    "`.git/index` mtime": r"\.git/index`\s*mtime",
    "`HEAD` = <sha>": r"`HEAD`\s*=\s*`[0-9a-f]{6,}`",
    "status 行数": r"porcelain`\s*\*\*\d+\*\*",
}
_hits = {k: bool(re.search(v, LINE)) for k, v in _bad.items()}
check("(a) 不含本机状态字段（.git/index mtime / HEAD sha / status 行数）",
      not any(_hits.values()), _hits)

# ── (b) 校验语义不变：归一化 = 占位符（向后兼容历史 README）─────────────────
check("(b) 新行经 `--check` 归一化 == `_GIT_PLACEHOLDER`",
      G.normalize_for_check(LINE) == G._GIT_PLACEHOLDER,
      G.normalize_for_check(LINE))
_OLD = ("**红线只读快照**（只跑 `git rev-parse` / `git status`，不写）：`.git/index` mtime "
        "`2026-10-22 01:26:20`；`HEAD` = `41c68ff`；`git status --porcelain` **1** 行")
check("(b) 旧格式行同样归一化成占位符（未重生成的 README 也不会误红）",
      G.normalize_for_check(_OLD) == G._GIT_PLACEHOLDER)

# ── fixture：把 README 指到临时目录（保持 hermetic，快照跟着落 fixture/out/）──
FIX = TMP / "engine"
FIX.mkdir(parents=True, exist_ok=True)
G.README = FIX / "README.md"
HEAD = "\n<!-- AUTO:STATE:BEGIN -->"
TAIL = "<!-- AUTO:STATE:END -->\n\n块外尾部：必须逐字节不动。\n"
G.README.write_text(HEAD + "\n**旧状态行**（本测试的起点）\n" + TAIL, encoding="utf-8")
BODY = "\n" + LINE + "\n"

# ── (c) 幂等：连写两次 → 第二次不改写、字节不变 ────────────────────────────
_new1, _chg1 = G._flush_block(BODY, HEAD, TAIL)
_after1 = G.README.read_bytes()
_new2, _chg2 = G._flush_block(BODY, HEAD, TAIL)
_after2 = G.README.read_bytes()
_body_after = G.split_block(G.README.read_text(encoding="utf-8"))[1]
check("(c) 第一次写：changed=True 且块内 = 确定性行（无旧状态行残留）",
      _chg1 and (_new1 == BODY) and _body_after.strip() == LINE and "旧状态行" not in _body_after)
check("(c) 第二次写（同一输入）：changed=False（**幂等** —— B-159 判据）", not _chg2)
check("(c) 第二次写后 README **逐字节不变**（clean 树上 0 行 diff）", _after1 == _after2)
check("(c) 块外内容逐字节未动", G.README.read_text(encoding="utf-8").endswith(TAIL))

# ── (d) 现况留证：out/gen_docs-state.txt（不进提交）─────────────────────────
_state = G.state_file()
check("(d) 快照落在 README 同级的 `out/gen_docs-state.txt`",
      str(_state).replace("\\", "/").endswith("/out/gen_docs-state.txt"), _state)
_txt = _state.read_text(encoding="utf-8") if _state.is_file() else ""
check("(d) 快照含 generated_at + mtime / HEAD / status 三行",
      all(k in _txt for k in ("generated_at:", ".git/index mtime:", "HEAD:",
                             "git status --porcelain 行数:")), _txt.replace("\n", " | "))
check("(d) 快照**不在** README 里（提交物与证据分离）", ".git/index mtime" not in LINE)

# ── (e) 写盘失败只告警、不抛（不改退出码）───────────────────────────────────
G.state_file = lambda: FIX / "README.md" / "out" / "gen_docs-state.txt"   # 父“目录”是个文件
try:
    G.record_git_state()
    _no_raise = True
    _err = ""
except Exception as e:  # noqa: BLE001 —— 这里**就是要**证明不会抛
    _no_raise = False
    _err = f"{type(e).__name__}: {e}"
check("(e) 快照写盘失败 → 只告警、不抛异常（不影响计算/退出码）", _no_raise, _err)

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
