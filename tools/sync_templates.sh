#!/usr/bin/env bash
# 把**稿件仓**的《编辑笔记/模板文件》同步到本仓（工具箱发布时会带上这份副本）。
#
#   bash tools/sync_templates.sh            # 预演（只列出会改什么）
#   bash tools/sync_templates.sh --apply    # 真同步
#
# 为什么是"稿件仓 → 本仓"这个方向：模板是本刊的资产，编辑在稿件仓的 `编辑笔记/模板文件/`
# 里维护；本仓这份是**运行时副本**（引擎打包/发布要用），不是源头。
set -euo pipefail
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

HERE="$(cd "$(dirname "$0")/.." && pwd)"
MANU="$(SLCN="${SLCN_MANUSCRIPTS:-}" /opt/homebrew/bin/python3.14 - "$HERE" <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "Engine"))
from slcn import paths
print(paths.manuscripts_root() or "")
PY
)"
[ -n "$MANU" ] || { echo "❌ 找不到稿件仓；设 SLCN_MANUSCRIPTS=<稿件仓路径> 再跑" >&2; exit 2; }
SRC="$MANU/编辑笔记/模板文件"
DST="$HERE/编辑笔记/模板文件"
[ -d "$SRC" ] || { echo "❌ 稿件仓里没有模板目录：$SRC" >&2; exit 2; }

echo "▶ 源头：$SRC"
echo "▶ 目标：$DST"
if [ "$APPLY" -eq 0 ]; then
  rsync -an --delete --itemize-changes "$SRC/" "$DST/" | sed 's/^/    /'
  echo "（预演结束，什么都没写。加 --apply 才真同步。）"
  exit 0
fi
rsync -a --delete "$SRC/" "$DST/"
echo "✅ 已同步；记得 git add/commit（如果模板没变，这里不会有改动）"
