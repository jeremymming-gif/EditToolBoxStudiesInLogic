#!/usr/bin/env bash
# rename_to_edittoolbox.sh —— 拆仓之后，把「工具仓」的目录名与路径引用改成 EditToolBox 的布局。
#
# 只在**已经拆出来的工具仓**里跑（判据见下面 preflight）：那里 `Engine/` 在仓库根，
# 不存在 `MacToolbox/`，附近也没有期号目录。在稿件仓里误跑会直接拒绝，不会动到稿件。
#
#   tools/rename_to_edittoolbox.sh              # 预演（默认）：只打印将要改什么，不写盘
#   tools/rename_to_edittoolbox.sh --apply      # 真改（改完自己会跑一遍验收）
#
# 改什么（对照表，按从长到短依次替换，避免「短的先命中」）：
#   MacToolbox/WindowsShell → sources-win      MacToolbox/tools    → tools
#   MacToolbox/Engine       → Engine           MacToolbox/build.sh → build.sh
#   MacToolbox/Sources      → sources-mac      MacToolbox/dist     → dist
#   MacToolbox/<其它>        → <其它>           裸 MacToolbox（散文）→ EditToolBox
#   Sources/（裸）            → sources-mac/      WindowsShell（裸）→ sources-win
# 目录改名：Sources/ → sources-mac/、WindowsShell/ → sources-win/
#
# **有意不动**的地方（写在这里免得后人「顺手」改坏）：
#   · `Engine/docs/cases/**` —— 案例法是只增不改的历史记录，路径按**当时的**布局写才对；
#     看门狗也已把这一目录排除在路径存在性检查外。
#   · `dist/**`、`Engine/out/**`、`build/**`、`__pycache__/**` —— 产物，不参与。
#   · 二进制/图片等非文本文件一律不碰（替换核心只处理下面那几类扩展名）。
#
# ⚠️ 替换核心用 **Python** 而不是 bash：macOS 自带 bash 3.2（没有 `mapfile`），
#    而且 `$(cat)` 会吃掉文件结尾的换行 —— 2026-10-15 第一版把这两个坑都踩了。
set -euo pipefail

APPLY=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "不认识的参数：${a}（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── preflight：确认这是**拆出来的工具仓**，不是稿件仓 ──────────────────────
if [ ! -f "Engine/convert.py" ]; then
  echo "❌ 这里没有 Engine/convert.py —— 本脚本只能在拆出来的工具仓根目录跑。" >&2
  echo "   （当前目录：${ROOT}）" >&2
  exit 2
fi
if [ -d "MacToolbox" ]; then
  echo "❌ 这里还有 MacToolbox/ —— **还没拆仓**。本脚本拒绝执行，以免动到稿件仓。" >&2
  echo "   先按 RELEASING.md 跑 tools/split_repo.sh，在拆出来的新仓里再跑本脚本。" >&2
  exit 2
fi
# ⚠️ 光看「有没有 MacToolbox/」不够：从**稿件仓的 MacToolbox/ 里面**跑时，那里也不存在
#    嵌套的 MacToolbox/，脚本会以为自己是工具仓，把稿件仓的路径全改坏（实测踩到）。
if find . -maxdepth 1 -name '*年第*期(*' 2>/dev/null | grep -q . \
   || find .. -maxdepth 1 -name '*年第*期(*' 2>/dev/null | grep -q .; then
  echo "❌ 附近有期号目录（作者稿件）—— 这是**稿件仓**，不是拆出来的工具仓。" >&2
  echo "   在这里跑会把 MacToolbox/ 的相对路径全改坏。请在新仓里跑。" >&2
  exit 2
fi

echo "▶ 仓库根：$ROOT"
if [ "$APPLY" -eq 1 ]; then echo "▶ 模式：**真改**"; else echo "▶ 模式：预演（只打印，不写盘；加 --apply 才真改）"; fi
echo

# ── 文本替换（Python 核心：保留编码与结尾换行，只写有变化的文件）──────────
APPLY="$APPLY" ROOT="$ROOT" /usr/bin/env python3 - <<'PY'
import os, pathlib

APPLY = os.environ.get("APPLY") == "1"
ROOT = pathlib.Path(os.environ["ROOT"])

SUBS = [
    ("MacToolbox/WindowsShell", "sources-win"),
    ("MacToolbox/Engine", "Engine"),
    ("MacToolbox/Sources", "sources-mac"),
    ("MacToolbox/tools", "tools"),
    ("MacToolbox/build.sh", "build.sh"),
    ("MacToolbox/dist", "dist"),
    ("MacToolbox/", ""),
    ("MacToolbox", "EditToolBox"),
    # 裸目录名（不带 `MacToolbox/` 前缀）也要改 —— 否则新仓里 `build.sh` 仍指着
    # `Sources/*.swift`、工作流仍 `cd WindowsShell`，**mac 构建与 CI 会直接失败**
    # （2026-10-15 第一次拆仓后实测发现）。大小写敏感，所以不会误伤 `Resources/`。
    ("Sources/", "sources-mac/"),
    ("WindowsShell", "sources-win"),
]
EXTS = {".py", ".swift", ".sh", ".command", ".ps1", ".md", ".yml", ".yaml", ".json"}
SKIP_DIRS = {"dist", ".git", "build", "out", "__pycache__", "node_modules", "cases"}
# 别改本脚本自己：里面的 `MacToolbox/…` 是**文档示例**，改了读者就看不懂对照表了
SKIP_FILES = {"tools/rename_to_edittoolbox.sh", "tools/split_repo.sh"}
# ⚠️ 这个文件**只能定向改**，不能吃通用替换：它的 `PREFIXES` 元组里有一项就是
#    `"MacToolbox/"`，通用替换会把它变成**空串** —— 于是那个「路径前缀」正则多出一个
#    永远命中的空分支，看门狗会把每个字都当成路径，一次报出 9665 处假缺失
#    （2026-10-15 拆仓预演实测踩到）。下面单独处理它。
TARGETED_ONLY = {"Engine/tests/test_docs_consistency.py"}

total, files = 0, []
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or p.suffix not in EXTS:
        continue
    rel = p.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        continue                      # 产物 / 历史记录（cases）/ 缓存
    if str(rel) in SKIP_FILES or str(rel) in TARGETED_ONLY:
        continue
    try:
        txt = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    new = txt
    for a, b in SUBS:
        new = new.replace(a, b)
    if new == txt:
        continue
    n = sum(1 for x, y in zip(txt.split("\n"), new.split("\n")) if x != y)
    print(f"  {str(rel):<58} {n} 行有改动")
    total += n
    files.append(p)
    if APPLY:
        p.write_text(new, encoding="utf-8")

print(f"\n▶ 文本替换：{total} 行有改动，涉及 {len(files)} 个文件")

wd = ROOT / "Engine/tests/test_docs_consistency.py"
if wd.is_file():
    t = wd.read_text(encoding="utf-8")
    t2 = t.replace('for base in (ENGINE, REPO, REPO / "MacToolbox", ENGINE):',
                   'for base in (ENGINE, REPO, ENGINE):   # 拆仓后 MacToolbox/ 这一层没有了')
    t2 = t2.replace('PREFIXES = ("Engine/", "slcn/", "tools/", "tests/", "docs/", "MacToolbox/", "编辑笔记/")',
                    'PREFIXES = ("Engine/", "slcn/", "tools/", "tests/", "docs/", "编辑笔记/")')
    # 仓库根的推导也要改：稿仓里 Engine 在 `MacToolbox/Engine`（所以是 parents[1]），
    # 拆仓后 `Engine/` 就在根下（应改 parents[0]）。不改的话看门狗把根算到上一层，
    # 一次报出几十处「路径不存在」（2026-10-15 拆仓预演实测）。
    t2 = t2.replace('REPO = ENGINE.parents[1]                            # 仓库根',
                    'REPO = ENGINE.parent                                # 仓库根（拆仓后 Engine/ 就在根下）')
    t2 = t2.replace("MacToolbox", "EditToolBox")     # 剩下的都是散文里的项目名
    if t2 != t:
        print("▶ Engine/tests/test_docs_consistency.py：定向改（解析根 + 路径前缀 + 散文名）")
        if APPLY:
            wd.write_text(t2, encoding="utf-8")
PY

# ── Swift 测试台里单独一行常量（源码目录名）──────────────────────────────
for f in tools/ai_settings_test/main.swift tools/ai_store_test/main.swift; do
  if [ -f "$f" ] && grep -q 'let SRC_DIR_NAME = "Sources"' "$f"; then
    echo "▶ ${f}：SRC_DIR_NAME → \"sources-mac\""
    if [ "$APPLY" -eq 1 ]; then
      /usr/bin/env python3 - "$f" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text(encoding="utf-8")
p.write_text(t.replace('let SRC_DIR_NAME = "Sources"', 'let SRC_DIR_NAME = "sources-mac"'),
             encoding="utf-8")
PY
    fi
  fi
done

# ── 目录改名 ──────────────────────────────────────────────────────────────
echo "▶ 目录改名："
for pair in "Sources:sources-mac" "WindowsShell:sources-win"; do
  from="${pair%%:*}"; to="${pair#*:}"
  if [ -d "$from" ]; then
    echo "  $from/ → $to/"
    if [ "$APPLY" -eq 1 ]; then
      if [ -e "$to" ]; then echo "    ⚠️ 目标已存在，跳过（请人工确认）" >&2; else mv "$from" "$to"; fi
    fi
  else
    echo "  （没有 $from/，跳过）"
  fi
done

echo
if [ "$APPLY" -eq 0 ]; then
  echo "（预演结束，什么都没写。加 --apply 才真改。）"
  exit 0
fi

# ── 改完自检 ──────────────────────────────────────────────────────────────
echo "▶ 自检："
PY=/opt/homebrew/bin/python3.14
[ -x "$PY" ] || PY=python3
if [ -d Engine ]; then
  ( cd Engine && PYTHONPATH="$PWD/vendor" "$PY" tools/gen_docs.py >/dev/null 2>&1 \
      && echo "  ✅ gen_docs 重算了 README 的 AUTO 块" || echo "  ⚠️ gen_docs 没跑成，请看一眼" )
  ( cd Engine && PYTHONPATH="$PWD/vendor" "$PY" tests/test_docs_consistency.py 2>&1 | tail -1 )
fi
echo
echo "✅ 改名完成。接着做："
echo "   1) cd Engine && PYTHONPATH=\"\$PWD/vendor\" $PY tests/test_docs_consistency.py   # 应「失败 0 项」"
echo "   2) cd .. && ./build.sh，再比对包内引擎文件与仓库同名文件"
echo "   3) 全库套件 + Windows 外壳套件（sources-win/tests/test_bridge.py）"
echo "   4) git add -A && git commit -m '拆仓：MacToolbox → EditToolBox 布局'"
