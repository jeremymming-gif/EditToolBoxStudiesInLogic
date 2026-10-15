#!/usr/bin/env bash
# sync_to_public.sh —— 把**稿件仓**里的工具（）同步到公开的工具仓。
#
#   tools/sync_to_public.sh                        # 预演：打印将要做什么
#   tools/sync_to_public.sh --apply                # 真同步到本地（不推）
#   tools/sync_to_public.sh --apply --push         # 真同步并推送（在已有历史上加一个提交）
#   tools/sync_to_public.sh --rebuild --push       # **重建**公开仓：清空历史，只留一个快照提交
#
# 默认目标：`~/Documents/GitHub/EditToolBoxStudiesInLogic`
# 默认远端：`https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic.git`
#
# **哪个是源头**：`<稿件仓>/` 是**唯一**的工作主场——编辑在这里跑工具、案例法在这里长、
# 普查与 corpus 基线也在这里。公开仓是**衍生物**：谁直接在公开仓里改代码，下次同步就会覆盖掉。
#
# ## 公开仓里**不带**什么（有意）
#
#   · `Engine/HANDOFF.md`      —— 内部进度文档（稿件代号、待办、踩坑记录）
#   · `Engine/docs/cases/`     —— 案例法（含作者姓名与稿件片段）
#   · `编辑笔记/排版手册/`、`编辑笔记/流程化经验书.md` —— 内部规范文档
#   · `dist/`、`Engine/vendor/`、`__pycache__/`      —— 产物与本地依赖
#
#   带什么：引擎 + 两个外壳 + `编辑笔记/模板文件/`（运行时必需，2026-10-15 用户授权公开）。
#   看门狗（`Engine/tests/test_docs_consistency.py`）对这些缺席**逐条 SKIP**，不会误报。
#
# ## 什么时候用 --rebuild
#
#   公开仓没有协作者时，**重建**最干净：历史里不会残留内部资料
#   （普通同步只是在历史之上加提交，删掉的文件仍留在旧提交里）。
#   重建会 `rm -rf` 目标目录并 `git init`，推送需要 `--force`。
set -euo pipefail

APPLY=0
PUSH=0
REBUILD=0
DEST="${HOME}/Documents/GitHub/EditToolBoxStudiesInLogic"
REMOTE="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic.git"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --push) PUSH=1 ;;
    --rebuild) REBUILD=1; APPLY=1 ;;
    --dest) shift; DEST="${1:-}" ;;
    --dest=*) DEST="${1#--dest=}" ;;
    --remote) shift; REMOTE="${1:-}" ;;
    --remote=*) REMOTE="${1#--remote=}" ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "不认识的参数：${1}（用 --help 看用法）" >&2; exit 2 ;;
  esac
  shift
done

SRC_TOOL="$(cd "$(dirname "$0")/.." && pwd)"          # <稿件仓>/EditToolBox
MANUSCRIPTS="$(cd "${SRC_TOOL}/.." && pwd)"           # 稿件仓根
TPL="${MANUSCRIPTS}/编辑笔记/模板文件"
PY=/opt/homebrew/bin/python3.14
[ -x "$PY" ] || PY=python3

echo "▶ 源头（工作主场）：${SRC_TOOL}"
echo "▶ 目标（公开仓）：  ${DEST}"
[ "$REBUILD" -eq 0 ] && echo "▶ 远端：            ${REMOTE}"
[ -d "$TPL" ] || { echo "❌ 找不到模板目录：${TPL}" >&2; exit 2; }

if [ -n "$(git -C "$MANUSCRIPTS" status --porcelain)" ]; then
  echo "⚠️  稿件仓工作区不干净（$(git -C "$MANUSCRIPTS" status --porcelain | wc -l | tr -d ' ') 处）——"
  echo "    同步的是**当前工作区**（含未提交改动）。"
fi

# ── 一个函数管「拷什么」：普通同步与重建都走它，免得两条路漂移 ──────────────
sync_content() {
  # 目标里改名后的派生目录先清掉：源里是 `sources-mac/`、`sources-win/`。
  # 不清的话 rsync 会报 “not empty, cannot delete”，改名脚本还会因目录已存在而跳过。
  rm -rf "${DEST}/sources-mac" "${DEST}/sources-win"
  rsync -a --delete \
    --exclude '.git/' --exclude 'dist/' --exclude '.build/' --exclude '.mc/' \
    --exclude 'out/' --exclude 'vendor/' --exclude '__pycache__/' --exclude '.DS_Store' \
    --exclude 'HANDOFF.md' --exclude 'docs/cases/' \
    "${SRC_TOOL}/" "${DEST}/"
  mkdir -p "${DEST}/编辑笔记/模板文件"
  rsync -a --delete "${TPL}/" "${DEST}/编辑笔记/模板文件/"
  # 内部资料：公开仓**有意不带**（历史上带过的，这里一并清掉）
  rm -rf "${DEST}/编辑笔记/排版手册" "${DEST}/编辑笔记/流程化经验书.md"
  rm -rf "${DEST}/Engine/docs/cases" "${DEST}/Engine/HANDOFF.md"
}

# ── 预演 ─────────────────────────────────────────────────────────────────
if [ "$APPLY" -eq 0 ]; then
  cat <<PLAN
▶ 计划（预演）：
    $([ "$REBUILD" -eq 1 ] && echo "rm -rf \"${DEST}\" && mkdir -p \"${DEST}\"   # 重建：清空历史" || echo "# 目标须已是 git 仓、且工作区干净")
    rsync -a --delete （排除 .git/ dist/ .build/ .mc/ out/ vendor/ __pycache__/
                      HANDOFF.md docs/cases/）"${SRC_TOOL}/" → "${DEST}/"
    rsync 模板 "${TPL}/" → "${DEST}/编辑笔记/模板文件/"
    清掉内部资料：编辑笔记/{排版手册,流程化经验书.md}、Engine/{docs/cases,HANDOFF.md}
    cd "${DEST}" && bash tools/rename_to_edittoolbox.sh --apply
    $([ "$REBUILD" -eq 1 ] && echo "git init -b main && git add -A && git commit   # 单一快照提交")
    cd "${DEST}/Engine" && PYTHONPATH="\$PWD/vendor" ${PY} tools/gen_docs.py
    cd "${DEST}/Engine" && PYTHONPATH="\$PWD/vendor" ${PY} tests/test_docs_consistency.py
$([ "$PUSH" -eq 1 ] && echo "    git remote set-url/add origin ${REMOTE} && git push$([ "$REBUILD" -eq 1 ] && echo ' --force') origin main" || echo "    （不加 --push 就只同步到本地）")
PLAN
  echo
  echo "（预演结束，什么都没写。加 --apply 才真同步。）"
  exit 0
fi

# ── 目标检查 / 重建 ───────────────────────────────────────────────────────
if [ "$REBUILD" -eq 1 ]; then
  echo "▶ 重建：清空目标目录与历史…"
  rm -rf "$DEST"
  mkdir -p "$DEST"
else
  [ -d "${DEST}/.git" ] || { echo "❌ ${DEST} 不是 git 仓库（用 --rebuild 重建，或先 clone 一份）" >&2; exit 2; }
  if [ -n "$(git -C "$DEST" status --porcelain)" ]; then
    echo "❌ 公开仓里有未提交改动（$(git -C "$DEST" status --porcelain | wc -l | tr -d ' ') 处）。" >&2
    echo "   先处理掉（在公开仓里直接改代码是**徒劳**的：它是衍生物，下次同步会覆盖）。" >&2
    exit 2
  fi
fi

echo "▶ 同步文件…"
sync_content

echo "▶ 改布局（Sources→sources-mac、sources-win→sources-win、路径引用）…"
( cd "$DEST" && bash tools/rename_to_edittoolbox.sh --apply >/dev/null 2>&1 || echo "  ⚠️ 改名脚本报错，手工看一眼" )

if [ "$REBUILD" -eq 1 ]; then
  echo "▶ 初始化 git（单一快照提交）…"
  ( cd "$DEST" && { git init -q -b main 2>/dev/null || git init -q; } )
fi

echo "▶ 重算 AUTO 块…（公开仓没有稿件库，那几行会写成「本仓不含稿件库」）"
if [ ! -d "${DEST}/Engine/vendor" ]; then
  "${PY}" -m pip install --quiet --target "${DEST}/Engine/vendor" -r "${DEST}/Engine/requirements.txt"
fi
( cd "${DEST}/Engine" && PYTHONPATH="$PWD/vendor" "$PY" tools/gen_docs.py | tail -1 )

echo "▶ 文档看门狗…"
( cd "${DEST}/Engine" && PYTHONPATH="$PWD/vendor" "$PY" tests/test_docs_consistency.py | tail -1 )

echo
echo "▶ 自检：公开仓里不许有内部资料"
LEAK="$(cd "$DEST" && git ls-files 2>/dev/null | grep -E 'HANDOFF\.md|docs/cases/|排版手册|流程化经验书' || true)"
if [ -n "$LEAK" ]; then echo "❌ 仍在版本库里：${LEAK}" >&2; exit 3; fi
echo "  ✅ 没有 HANDOFF.md / docs/cases / 手册 / 经验书"

echo
if [ "$PUSH" -eq 1 ]; then
  ( cd "$DEST" && { git remote get-url origin >/dev/null 2>&1 && git remote set-url origin "$REMOTE" || git remote add origin "$REMOTE"; } )
  STAMP="$(date '+%Y-%m-%d %H:%M')"
  if [ "$REBUILD" -eq 1 ]; then
    ( cd "$DEST" && git add -A && \
      git -c user.name="sync_to_public" -c user.email="noreply@example.invalid" commit --quiet -F - <<MSG
公开快照（${STAMP}）

由 <稿件仓>/tools/sync_to_public.sh --rebuild 生成：**单一提交、无历史**。
内容 = 工具目录（引擎 + 两个外壳 + tools/）+ 编辑笔记/模板文件；
不含内部资料：HANDOFF.md、docs/cases/、排校手册、流程化经验书。
布局按公开仓口径：sources-mac/（原 sources-mac/）、sources-win/（原 sources-win/）。
MSG
      git push --force origin main 2>&1 | tail -2 )
  else
    ( cd "$DEST" && git add -A && \
      git -c user.name="sync_to_public" -c user.email="noreply@example.invalid" \
        commit --quiet -m "同步自稿件仓（${STAMP}）" && git push origin main 2>&1 | tail -2 )
  fi
  echo "✅ 已推送"
else
  echo "✅ 已同步到本地（未推送）"
fi
