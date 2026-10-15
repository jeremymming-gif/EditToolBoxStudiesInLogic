#!/usr/bin/env bash
# split_repo.sh —— 把工具箱从**稿件仓**里拆成一个独立的工具仓（EditToolBox）。
#
# 设计原则：**绝不动稿件仓**。所有 git 操作都在 `mktemp -d` 出来的临时克隆里做，
# 原仓只被**读**（clone / 拷模板）。跑完脚本会打印临时克隆的路径与后续 push 命令，
# 由仓库所有者自己决定要不要推。
#
#   tools/split_repo.sh                 # 预演（默认）：打印将执行的命令与检查结果
#   tools/split_repo.sh --apply         # 真跑：在临时克隆里拆出分支并补上模板
#   tools/split_repo.sh --apply --keep  # 同上，并保留临时目录（默认也保留，便于检查）
#
# 拆出来的新仓内容（= `MacToolbox/` 的内容 + 运行必需的模板）：
#   Engine/  Sources/（改名后 sources-mac/）  WindowsShell/（改名后 sources-win/）
#   build.sh  使用说明.md  tools/  spec/  Resources/  THIRD_PARTY.md …
#   编辑笔记/模板文件/     ← **必须有**：引擎生成初排稿时要用 .sty 与骨架（见 slcn/templates.py）
#                          （2026-10-15 用户决定：模板可以随公开仓发布）
# 有意**不带**：编辑笔记/排版手册/ 与 编辑笔记/流程化经验书.md —— 留在稿件仓；
#   引用它们的那几处由文档看门狗按「有意不带」跳过（见 test_docs_consistency.py (b)）。
#
# 之后还要做（脚本会打印清单）：
#   1) 在新仓里跑 tools/rename_to_edittoolbox.sh --apply（改目录名与路径引用）
#   2) tools/gen_docs.py 重算 README 的 AUTO 块
#   3) 全套件 + ./build.sh + Windows 外壳套件
#   4) 建远端仓库并 push（**这步由所有者做**）
set -euo pipefail

APPLY=0
DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --keep) : ;;                       # 默认就保留临时目录（便于人工检查）
    --dest) shift; DEST="${1:-}" ;;
    --dest=*) DEST="${1#--dest=}" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "不认识的参数：${1}（用 --help 看用法）" >&2; exit 2 ;;
  esac
  shift
done

# 稿件仓根：本脚本在 MacToolbox/tools/ 下，所以根在往上两级
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
BRANCH="${SLCN_SPLIT_BRANCH:-edittoolbox-split}"

echo "▶ 稿件仓：${SRC}"

# ── preflight ─────────────────────────────────────────────────────────────
[ -d "${SRC}/MacToolbox/Engine" ] || { echo "❌ 找不到 ${SRC}/MacToolbox/Engine —— 请在稿件仓里跑本脚本。" >&2; exit 2; }
[ -d "${SRC}/.git" ] || { echo "❌ ${SRC} 不是 git 仓库（拆仓要靠 git 历史）。" >&2; exit 2; }
command -v git >/dev/null || { echo "❌ 没有 git。" >&2; exit 2; }
DIRTY="$(git -C "$SRC" status --porcelain | wc -l | tr -d ' ')"
UNTRACKED_ENGINE="$(git -C "$SRC" status --porcelain --untracked-files=all MacToolbox/Engine 2>/dev/null | wc -l | tr -d ' ')"
if [ "$UNTRACKED_ENGINE" -gt 0 ]; then
  cat >&2 <<WARN
❌ **引擎代码还没进版本库**：MacToolbox/Engine 下有 ${UNTRACKED_ENGINE} 个文件未被 git 跟踪
   （git ls-files MacToolbox/Engine 一个都没有）。subtree split 只取已提交的内容，
   这样拆出来的新仓**不会有引擎** —— 2026-10-15 实测正是如此。
   ⚠️ 顺带提醒：这也意味着这些代码**目前只存在这台机器上**，没有历史备份。

   请先由仓库所有者提交（本脚本不代劳，git 写操作按红线归所有者）：
       git add MacToolbox/Engine MacToolbox/WindowsShell MacToolbox/THIRD_PARTY.md
       git commit -m "把引擎、Windows 外壳与第三方声明纳入版本库"
   然后重跑本脚本。
WARN
  exit 2
fi
if [ "$DIRTY" -gt 0 ]; then
  echo "⚠️  稿件仓工作区**不干净**（${DIRTY} 处改动）。"
  echo "    subtree split 只取**已提交**的历史：未提交的改动不会进新仓。"
  echo "    建议先由所有者把要发布的改动提交，再跑本脚本。"
fi

if [ -n "$DEST" ]; then
  # 放到持久位置（例如 ~/Documents/GitHub/EditToolBoxStudiesInLogic），便于用 GitHub Desktop 推。
  [ "$APPLY" -eq 1 ] || { echo "▶ 目标目录：${DEST}（加 --apply 才会真的建）"; }
  if [ -e "$DEST" ] && [ "$APPLY" -eq 1 ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
    echo "❌ 目标目录已存在且非空：${DEST}（换个名字或先清空）" >&2; exit 2
  fi
  TMP="$(dirname "$DEST")"
  CLONE="$DEST"
else
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/ett_split.XXXXXX")"
  CLONE="${TMP}/EditToolBox"
fi

echo "▶ 临时目录：${TMP}"
echo "▶ 计划（全部在临时克隆里做，稿件仓只读）："
cat <<PLAN
    git clone "${SRC}" "${CLONE}"
    cd "${CLONE}" && git subtree split -P MacToolbox -b ${BRANCH}
    git checkout ${BRANCH}
    cp -R "${SRC}/编辑笔记/模板文件" "${CLONE}/编辑笔记/模板文件"      # 运行必需，且**可公开**
    git add -A && git commit -m "拆分：带上引擎运行必需的模板与规范文档"
PLAN

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "（预演结束。加 --apply 才真跑；跑完临时目录会保留，便于你检查后再推。）"
  exit 0
fi

# ── 真跑 ──────────────────────────────────────────────────────────────────
echo
echo "▶ 克隆稿件仓（只读）…"
git clone --quiet "$SRC" "$CLONE"
cd "$CLONE"

echo "▶ subtree split：把 MacToolbox/ 拆成独立历史…"
git subtree split -P MacToolbox -b "$BRANCH" >/dev/null
git checkout --quiet "$BRANCH"

echo "▶ 补上引擎运行必需的模板与规范文档…"
mkdir -p 编辑笔记
cp -R "${SRC}/编辑笔记/模板文件" 编辑笔记/模板文件
git add -A
git -c user.name="split_repo.sh" -c user.email="noreply@example.invalid" \
    commit --quiet -m "拆分：带上引擎运行必需的模板（编辑笔记/模板文件）与规范文档" || echo "   （没有新文件要提交）"

# ── 守卫：构建产物与本地依赖**不许**被跟踪（2026-10-15 实测踩到）────────────
#    忽略规则原来只在稿件仓根的 .gitignore 里，拆仓带不过来 —— 于是 `git add -A`
#    把 373 个 dist 文件与 460 个 vendor 文件一起提交了。
BAD_TRACKED="$(git ls-files | grep -E '^(dist/|Engine/vendor/|Engine/out/|.*__pycache__/)' | head -5 || true)"
if [ -n "$BAD_TRACKED" ]; then
  echo >&2
  echo "❌ 新仓里跟踪了不该进版本库的文件（构建产物 / 本地依赖）：" >&2
  echo "${BAD_TRACKED}" >&2
  echo "   检查 MacToolbox/.gitignore 是否带上了 dist/ 与 Engine/vendor/ 的规则。" >&2
  exit 4
fi

# ── 产物验证：新仓里必须真有引擎与外壳，否则这次拆分是「空壳」 ──────────────
MISSING=""
for need in Engine/convert.py Engine/slcn/pdfmerge.py Engine/engine_main.py build.sh ''; do
  [ -n "$need" ] && [ ! -e "$need" ] && MISSING="${MISSING} ${need}"
done
if [ -n "$MISSING" ]; then
  echo >&2
  echo "❌ 拆出来的仓缺关键文件：${MISSING}" >&2
  echo "   多半是这些路径还没被 git 跟踪（见上面 preflight 的提示）。新仓不可用，已停在临时目录：" >&2
  echo "   ${CLONE}" >&2
  exit 3
fi

# ── 瘦身：把与工具无关的历史对象清掉（否则新仓 .git 里躺着整个稿件仓，实测 2.2G）──
#    `git clone` 会把稿件仓的**全部对象**拷过来；`subtree split` 只新建一个分支。
#    push 单个分支时确实只传可达对象（不会泄露稿件），但本地 2.2G 既占地又容易误操作
#    （`git push --all` / `--mirror` 就会把稿件一起推上去）。所以这里直接清干净：
#    删掉其它分支与 origin 引用 → 过期 reflog → gc --prune=now。
echo "▶ 瘦身：清掉与工具无关的分支与对象（稿件历史不留在新仓里）…"
BEFORE="$(du -sh .git | cut -f1)"
git remote remove origin 2>/dev/null || true
for b in $(git for-each-ref --format='%(refname:short)' refs/heads | grep -v "^${BRANCH}$"); do
  git branch -D "$b" >/dev/null 2>&1 || true
done
git for-each-ref --format='%(refname)' refs/remotes | while read -r r; do
  git update-ref -d "$r" 2>/dev/null || true
done
git reflog expire --expire=now --all >/dev/null 2>&1 || true
git gc --prune=now --quiet >/dev/null 2>&1 || true
echo "   .git：${BEFORE} → $(du -sh .git | cut -f1)"

echo
echo "✅ 拆好了：${CLONE}（分支 ${BRANCH}）"
echo "   提交数：$(git rev-list --count HEAD)   体积：$(du -sh . | cut -f1)"
echo "   顶层内容：$(ls | tr '\n' ' ')"
echo
echo "▶ 接下来（**由仓库所有者执行**，脚本不代劳）："
cat <<NEXT
    1) 在新仓里改目录名与路径引用（不会动稿件仓）：
         cd "${CLONE}" && bash tools/rename_to_edittoolbox.sh --apply
    2) 重算 README 的 AUTO 块、跑全套件与看门狗：
         cd "${CLONE}/Engine" && PYTHONPATH="\$PWD/vendor" python3 tools/gen_docs.py
         PYTHONPATH="\$PWD/vendor" python3 tests/test_docs_consistency.py    # 应「失败 0 项」
         cd .. && ./build.sh                                                 # mac 侧 .app
    3) 在 GitHub 建一个**空**的公开仓库 EditToolBox，然后：
         cd "${CLONE}"
         git remote add origin git@github.com:<你的账号>/EditToolBox.git
         git push -u origin ${BRANCH}:main
    4) 之后在 GitHub 上打 tag（如 v1.0.0）即触发 .github/workflows/release.yml 出双平台 Release。
NEXT
