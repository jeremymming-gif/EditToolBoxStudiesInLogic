#!/usr/bin/env bash
# 引擎测试两档：quick（纯逻辑、秒级）/ full（全部套件，默认）。
#
#   cd Engine
#   bash tools/run_engine_tests.sh quick    # 日常改一行：白名单那几套，约 1 秒
#   bash tools/run_engine_tests.sh full     # 不给参数 = full：全部 tests/test_*.py
#
# 为什么分档：full 里有**真跑 xelatex**（test_issue_tools）、**起 mock 端点并真跑 convert**
# （test_llm_*）、**读真实稿件语料**（多数规则套件）的，稿件库齐全时要几分钟；日常改一行
# 只需要 quick 那几套。反过来，quick 全绿**不能**当作可以发版。
#
# quick 白名单的入选标准（2026-10-16 逐套件核实：读文件头注释 + 逐个计时）：
#   ① 不读稿件库（没有「语料不在本机则 SKIP」这类分支）；
#   ② 不需要 TeX / LibreOffice；
#   ③ 不真跑 convert / 全库 corpus.py / docx_audit.py；127.0.0.1 上**自己的瞬时 mock** 允许
#      （把 mock 接到 convert 端到端链路上的 `test_llm_*` 不收）；
#   ④ 子进程只允许一两次「很短」的（--version、合成输入的 CLI），单套 < 1 秒。
# 凡在真语料上还会解析 docx 的套件一律留在 full：本机没稿件库时它们 0.1 秒（整段 SKIP），
# 稿件库在时就要几十秒 —— 放进 quick 会让「秒级」名不副实。
#
# 解释器：优先 /opt/homebrew/bin/python3.14，找不到回落 python3；依赖走 PYTHONPATH=vendor。
# 退出码：红 0 套 → 0；否则 = 红的套数（非 0）。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2                     # → Engine/
if [ ! -d tests ]; then
  echo "❌ 找不到 tests/：本脚本要住在 Engine/tools/ 下（现在 cd 到了 ${PWD}）" >&2
  exit 2
fi

if [ -x /opt/homebrew/bin/python3.14 ]; then
  PY=/opt/homebrew/bin/python3.14
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"                         # 回落：老 mac / Linux / CI
else
  echo "❌ 既没有 /opt/homebrew/bin/python3.14，也没有 python3" >&2
  exit 2
fi
export PYTHONPATH="$PWD/vendor"

MODE="${1:-full}"
case "$MODE" in
  quick|full) ;;
  -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "用法：bash tools/run_engine_tests.sh [quick|full]（默认 full）" >&2; exit 2 ;;
esac

# quick 档白名单（名字同上 tests/<name>.py；按字母序）。每一行的理由都是**核实过的**：
QUICK=(
  test_bib_protection_forms     # 纯字符串判据（bib 保护写法），无文件/子进程依赖
  test_infocheck_toc            # 合成期目录快照 + 仓库内 fixtures；Swift 真差分缺席即 SKIP
  test_merge_into               # 合成 bib，全在 mktemp 目录里；不碰真实产物
  test_merge_record_provenance  # 同上；真产物那组靠 SLCN_BO_CHEN_DIR，默认缺席即 SKIP
  test_mock_keyed_responses     # 只打 127.0.0.1 的瞬时 mock（自起自停），不跑 convert/TeX/语料
  test_paths                    # 自己造假稿件库；重活工具那组把 SLCN_MANUSCRIPTS 钉到不存在
  test_platform                 # 只起短子进程（--version / 合成输入），不真跑 soffice
  test_proofcheck               # 合成 .tex，一两次短 CLI，不跑 TeX
)

FILES=()
if [ "$MODE" = quick ]; then
  for n in "${QUICK[@]}"; do
    if [ -f "tests/$n.py" ]; then
      FILES+=("tests/$n.py")
    else
      echo "❌ quick 白名单里的 tests/$n.py 不存在（改名了？请同步本脚本）" >&2
      exit 2
    fi
  done
else
  for f in tests/test_*.py; do
    [ -f "$f" ] && FILES+=("$f")
  done
fi

# 日志留在临时目录：全绿就删掉，有红就留着并把路径打给用户（不然还得手工重跑才知道红在哪）
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/slcn_engine_tests.XXXXXX")"
KEEP_LOGS=0
cleanup() { [ "$KEEP_LOGS" -eq 0 ] && rm -rf "$LOG_DIR"; return 0; }
trap cleanup EXIT

# 计时：要**带小数**，否则 quick 的「1 秒 / 2 秒」分不出快慢。bash 5 有 EPOCHREALTIME，
# 而 macOS 自带的 bash 3.2 没有（实测为空）→ 退回借解释器取 time.time()。
now() {
  if [ -n "${EPOCHREALTIME:-}" ]; then printf '%s' "$EPOCHREALTIME"
  else "$PY" -c 'import time; print(time.time())'
  fi
}

T0="$(now)"
if [ "$MODE" = quick ]; then
  echo "▶ quick 档：${#FILES[@]} 套（秒级：不碰稿件库 / TeX / LibreOffice，不起长期子进程）"
else
  echo "▶ full 档：全部 ${#FILES[@]} 套（含真跑 TeX、mock 端点、真实稿件语料的套件）"
fi
echo "  解释器 $PY ｜ PYTHONPATH=$PYTHONPATH"

TOTAL=0
RED=0
for f in "${FILES[@]}"; do
  name="$(basename "$f" .py)"
  log="$LOG_DIR/$name.log"
  "$PY" -u "$f" >"$log" 2>&1
  rc=$?
  # 套件自己的末行就是它的汇总（`失败 0 项（全部通过）` / `SUMMARY: 0 FAIL` / `全部 PASS`）
  last="$(grep -v '^[[:space:]]*$' "$log" | tail -1 | tr -d '\r')"
  bad_lines="$(grep -cE '^[[:space:]]*FAIL\b' "$log")"
  TOTAL=$((TOTAL + 1))
  if [ "$rc" -eq 0 ] && [ "$bad_lines" -eq 0 ]; then
    printf '  %-4s %-34s %s\n' "PASS" "$name" "${last:-（没有输出，可疑）}"
  else
    RED=$((RED + 1))
    if [ "$rc" -ne 0 ]; then why="退出码 $rc"; else why="退出码 0 但有 $bad_lines 行 FAIL（套件自己的退出码没跟上）"; fi
    printf '  %-4s %-34s %s｜%s\n' "FAIL" "$name" "$why" "${last:-（没有输出）}"
    # 红在哪：把这套自己的 FAIL 行直接贴出来（最多 8 行），省得再手工重跑一遍
    grep -E '^[[:space:]]*FAIL\b' "$log" | head -8 | sed 's/^/         /'
  fi
done

T1="$(now)"
ELAPSED="$(LC_NUMERIC=C awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.1f", b-a}')"
echo
echo "汇总：$TOTAL 套件，红 $RED ｜ $MODE 档耗时 ${ELAPSED} 秒"
if [ "$RED" -eq 0 ]; then
  echo "✅ 全部通过"
  exit 0
fi
KEEP_LOGS=1
echo "❌ 有 $RED 套红（完整日志：${LOG_DIR}）"
if [ "$MODE" = quick ]; then
  echo "   ⚠️ quick 只覆盖纯逻辑套件；修完请再跑一次 full"
fi
exit "$RED"
