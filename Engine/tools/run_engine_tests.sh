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
# 解释器优先级：`SLCN_TEST_PYTHON`（显式指定）→ /opt/homebrew/bin/python3.14（本机约定）
#   → python3 → python（Windows 的 Git Bash 只有 python.exe）。依赖走 PYTHONPATH=vendor。
#
# ⚠️ 为什么必须有 `SLCN_TEST_PYTHON` 这个出口：**「本机约定」在别的机器上不成立**。
#    2026-10-16 实测：macos-14 的 runner 镜像里就装着 /opt/homebrew/bin/python3.14，
#    但它没有 CI 用 pip 装的那几个依赖 → 19 套件全红（同一个脚本在本机全绿）。
#    CI 里 setup-python 备好的解释器叫 `python`，由 workflow 显式指定。
#
# 退出码：红 0 套 → 0；否则 = 红的套数（非 0）。
#
# CI 用：`SLCN_DUMP_RED_LOGS=1 bash tools/run_engine_tests.sh full`
#   红的套件会把**完整**日志（stdout+stderr）贴出来，不只是 FAIL 行。
#   为什么非这样不可：2026-10-16 的 release.yml 用的是 `python "$f" | tail -3`，
#   15 次 windows 运行里套件打印的全部诊断都被 `tail -3` 吃掉，**而且** `set -e`+pipefail
#   在第一套红时就退出 —— 排在 test_llm_finish_reason 之后的 17 套在 Windows 上从来没跑过。
#   这个脚本从设计上就是「跑完全部套件、逐套留日志」，CI 复用它才是唯一一份逻辑。
#
# B-309 并行档：`SLCN_TEST_JOBS=N`（≥1；默认 1 = 旧串行行为，输出/退出码逐字不变）。
#   · >1 时同时最多跑 N 套；**并行的红套件会再串行复核一遍**，只有串行仍红才计红
#     （并行红/串行绿 = 抖动：显式警告、日志留着，但不计红）。
#   · 固定端口 / 共享路径的套件要放 SERIAL_ALLOWLIST（串行跑，仍计入总数）。本批审计
#     Engine/tests 全库：无固定端口、无固定 /tmp 写、无仓库 out/ 写 → 默认空。
#   · 汇总行格式与退出码语义不变：`汇总：N 套件，红 R ｜ full 档耗时 S 秒`；退出码 = 红套数。
set -uo pipefail

# `-h` 要读自己的抬头：必须在 cd 之前把自身路径**绝对化**（cd 之后 $0 的相对路径就找不到自己了）
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")/.." || exit 2                     # → Engine/
if [ ! -d tests ]; then
  echo "❌ 找不到 tests/：本脚本要住在 Engine/tools/ 下（现在 cd 到了 ${PWD}）" >&2
  exit 2
fi

if [ -n "${SLCN_TEST_PYTHON:-}" ]; then
  PY="$SLCN_TEST_PYTHON"
  if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
    echo "❌ SLCN_TEST_PYTHON=$PY 既不在 PATH 里也不是可执行的路径" >&2
    exit 2
  fi
elif [ -x /opt/homebrew/bin/python3.14 ]; then
  PY=/opt/homebrew/bin/python3.14
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"                         # 回落：老 mac / Linux / CI
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"                          # Windows 的 Git Bash 只有 python.exe
else
  echo "❌ 既没有 /opt/homebrew/bin/python3.14，也没有 python3 / python" >&2
  exit 2
fi
export PYTHONPATH="$PWD/vendor"

MODE="${1:-full}"
case "$MODE" in
  quick|full) ;;
  -h|--help) awk 'NR>1 && /^set -uo pipefail$/{exit} NR>1{sub(/^# ?/,""); print}' "$SELF"; exit 0 ;;
  *) echo "用法：bash tools/run_engine_tests.sh [quick|full]（默认 full；CI 加 SLCN_DUMP_RED_LOGS=1）" >&2; exit 2 ;;
esac

# quick 档白名单（名字同上 tests/<name>.py；按字母序）。每一行的理由都是**核实过的**：
QUICK=(
  test_b285_contract            # 本批总契约：旧 CLI golden / 文档 / quick 白名单 / 冻结快照
  test_b285_diagnose            # B-285a diagnose.v1：17 类夹具 + CLI/只读/确定性
  test_b285_report_header       # B-285a 四类报告摘要头 + 旧正文逐行找回
  test_bib_protection_forms     # 纯字符串判据（bib 保护写法），无文件/子进程依赖
  test_infocheck_toc            # 合成期目录快照 + 仓库内 fixtures；Swift 真差分缺席即 SKIP
  test_latex_preamble           # 纯文本判据 + 合成 .tex 构建；不读语料、不跑 TeX
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

# ── B-309 并行档 ──────────────────────────────────────────────────────────
# 并发本身交给 `xargs -P`（macOS bash 3.2 没有 `wait -n`，自己写池子要绕 set -u 的
# 空数组展开坑）；结果落成 `<name>.par.log` + `.rc`，再按 FILES 原序统一裁决、打印，
# 所以输出顺序和旧版一致，只是行内耗时变短。
JOBS="${SLCN_TEST_JOBS:-1}"
case "$JOBS" in
  ''|*[!0-9]*)
    echo "❌ SLCN_TEST_JOBS 只能是 ≥1 的整数，收到：$JOBS" >&2
    exit 2 ;;
esac
if [ "$JOBS" -lt 1 ]; then
  echo "❌ SLCN_TEST_JOBS 只能是 ≥1 的整数，收到：$JOBS" >&2
  exit 2
fi
[ "$JOBS" -gt "${#FILES[@]}" ] && JOBS="${#FILES[@]}"

# 串行 allowlist（默认空；理由见文件抬头「并行安全审计」）。环境变量只用于追加/测试：
# `SLCN_TEST_SERIAL_ALLOWLIST=test_x,test_y`。这里的套件**照跑**、照计数，只是不并发。
SERIAL_ALLOWLIST=(
)
ALLOW=()
if [ -n "${SLCN_TEST_SERIAL_ALLOWLIST:-}" ]; then
  _old_ifs="$IFS"; IFS=','
  for _n in $SLCN_TEST_SERIAL_ALLOWLIST; do
    _n="$(printf '%s' "$_n" | tr -d '[:space:]')"
    [ -n "$_n" ] && ALLOW+=("$_n")
  done
  IFS="$_old_ifs"
fi

suite_rc() { if [ -f "$1" ]; then tr -d '[:space:]' < "$1"; else echo 1; fi; }
suite_bad() { grep -cE '^[[:space:]]*FAIL\b' "$1" 2>/dev/null | tr -d '[:space:]'; }
# 红 = 退出码非 0，或退出码 0 但套件自己打了 FAIL 行（旧版同一口径）
suite_red() {
  local rc bad
  rc="$(suite_rc "$2")"; bad="$(suite_bad "$1")"
  [ "${rc:-1}" -ne 0 ] || [ "${bad:-0}" -ne 0 ]
}

# 红的细节：贴 FAIL 行（最多 8 行）；CI 加 SLCN_DUMP_RED_LOGS=1 时贴完整日志
dump_red() {  # $1=name $2=log
  echo "         $1 的 FAIL 行"
  grep -E '^[[:space:]]*FAIL\b' "$2" | head -8 | sed 's/^/         /'
  if [ "${SLCN_DUMP_RED_LOGS:-0}" = "1" ]; then
    echo "         ──────── $1 完整日志（$(wc -l < "$2" | tr -d ' ') 行）────────"
    sed 's/^/         /' "$2"
    echo "         ──────── $1 完整日志结束 ────────"
  fi
}

run_parallel() {
  local f name log rcf slog last why bad_lines done_allow=0
  local TOTAL="${#FILES[@]}" RED=0 RED_PAR=0 RED_ALLOW=0 FLAKY=0 PAR_RED=0
  local ALLOW_FILES=() PAR_FILES=()
  for f in "${FILES[@]}"; do
    name="$(basename "$f" .py)"
    _hit=0
    for _a in ${ALLOW[@]+"${ALLOW[@]}"}; do
      [ "$_a" = "$name" ] && _hit=1
    done
    if [ "$_hit" -eq 1 ]; then ALLOW_FILES+=("$f"); else PAR_FILES+=("$f"); fi
  done
  echo "  并行档：SLCN_TEST_JOBS=${JOBS}（串行 allowlist ${#ALLOW_FILES[@]} 套 / 并行 ${#PAR_FILES[@]} 套）"

  if [ "${#ALLOW_FILES[@]}" -gt 0 ]; then
    echo "▶ 串行 allowlist：${#ALLOW_FILES[@]} 套（固定端口/共享路径，理由见脚本注释）"
    for f in "${ALLOW_FILES[@]}"; do
      name="$(basename "$f" .py)"
      log="$LOG_DIR/$name.allow.log"
      SLCN_TEST_PHASE=serial "$PY" -u "$f" >"$log" 2>&1
      echo $? >"$log.rc"
      printf '  … %s（串行）\n' "$name"
    done
  fi

  if [ "${#PAR_FILES[@]}" -gt 0 ]; then
    echo "▶ 并行：最多 $JOBS 路（红套件随后串行复核）"
    export SLCN_B309_PY="$PY" SLCN_B309_LOGDIR="$LOG_DIR"
    printf '%s\n' "${PAR_FILES[@]}" | xargs -n 1 -P "$JOBS" bash -c '
      f="$1"; n="$(basename "$f" .py)"
      SLCN_TEST_PHASE=parallel "$SLCN_B309_PY" -u "$f" > "$SLCN_B309_LOGDIR/$n.par.log" 2>&1
      echo $? > "$SLCN_B309_LOGDIR/$n.par.log.rc"
    ' _
    unset SLCN_B309_PY SLCN_B309_LOGDIR
  fi

  # 1) 并行红 → 逐套串行复核（同一份 PY、同一套环境；phase 标记便于套件自检/复现）
  for f in ${PAR_FILES[@]+"${PAR_FILES[@]}"}; do
    name="$(basename "$f" .py)"
    log="$LOG_DIR/$name.par.log"
    if suite_red "$log" "$log.rc"; then
      PAR_RED=$((PAR_RED + 1))
      echo "  ↺ 串行复核 ${name}（并行红）"
      SLCN_TEST_PHASE=serial "$PY" -u "$f" >"$LOG_DIR/$name.serial.log" 2>&1
      echo $? >"$LOG_DIR/$name.serial.log.rc"
    fi
  done
  if [ "$PAR_RED" -gt 0 ]; then
    echo "  并行红 $PAR_RED 套已串行复核"
  fi

  # 2) 按 FILES 原序打印（和串行档同样的 PASS/FAIL 行格式）
  for f in "${FILES[@]}"; do
    name="$(basename "$f" .py)"
    if [ -f "$LOG_DIR/$name.allow.log" ]; then
      log="$LOG_DIR/$name.allow.log"; rcf="$log.rc"
      if suite_red "$log" "$rcf"; then
        RED_ALLOW=$((RED_ALLOW + 1))
        bad_lines="$(suite_bad "$log")"
        if [ "$(suite_rc "$rcf")" -ne 0 ]; then why="退出码 $(suite_rc "$rcf")"
        else why="退出码 0 但有 $bad_lines 行 FAIL（套件自己的退出码没跟上）"; fi
        last="$(grep -v '^[[:space:]]*$' "$log" | tail -1 | tr -d '\r')"
        printf '  %-4s %-34s %s｜%s\n' "FAIL" "$name" "$why" "${last:-（没有输出）}"
        dump_red "$name" "$log"
      else
        last="$(grep -v '^[[:space:]]*$' "$log" | tail -1 | tr -d '\r')"
        printf '  %-4s %-34s %s\n' "PASS" "$name" "${last:-（没有输出，可疑）}"
      fi
      continue
    fi
    log="$LOG_DIR/$name.par.log"
    if ! suite_red "$log" "$log.rc"; then
      last="$(grep -v '^[[:space:]]*$' "$log" | tail -1 | tr -d '\r')"
      printf '  %-4s %-34s %s\n' "PASS" "$name" "${last:-（没有输出，可疑）}"
      continue
    fi
    slog="$LOG_DIR/$name.serial.log"
    if suite_red "$slog" "$slog.rc"; then
      RED_PAR=$((RED_PAR + 1))
      bad_lines="$(suite_bad "$slog")"
      if [ "$(suite_rc "$slog.rc")" -ne 0 ]; then why="退出码 $(suite_rc "$slog.rc")"
      else why="退出码 0 但有 $bad_lines 行 FAIL（套件自己的退出码没跟上）"; fi
      last="$(grep -v '^[[:space:]]*$' "$slog" | tail -1 | tr -d '\r')"
      printf '  %-4s %-34s %s｜%s\n' "FAIL" "$name" "$why" "${last:-（没有输出）}"
      dump_red "$name" "$slog"
    else
      FLAKY=$((FLAKY + 1))
      printf '  %-4s %-34s %s\n' "⚠️" "$name" "并行红 / 串行绿（抖动，不计红；并行日志 ${log}）"
    fi
  done
  RED=$((RED_PAR + RED_ALLOW))

  T1="$(now)"
  ELAPSED="$(LC_NUMERIC=C awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.1f", b-a}')"
  echo
  echo "并行：$JOBS 路，串行 allowlist ${#ALLOW_FILES[@]} 套"
  if [ "$PAR_RED" -gt 0 ]; then
    echo "并行红 $PAR_RED 套 → 串行复核红 $RED_PAR 套（抖动 $FLAKY 套）"
  fi
  echo "汇总：$TOTAL 套件，红 $RED ｜ $MODE 档耗时 ${ELAPSED} 秒"
  if [ "$FLAKY" -gt 0 ]; then
    KEEP_LOGS=1
    echo "⚠️ 抖动 $FLAKY 套（并行红/串行绿；日志：${LOG_DIR}）"
  fi
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
}

if [ "$JOBS" -gt 1 ]; then
  run_parallel
fi

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
    # CI 要看**完整**日志：套件自己的诊断（比如 test_llm_finish_reason 的 [DIAG]）
    # 大多打在最后 3 行之外，只贴 FAIL 行等于没贴。
    if [ "${SLCN_DUMP_RED_LOGS:-0}" = "1" ]; then
      echo "         ──────── $name 完整日志（$(wc -l < "$log" | tr -d ' ') 行）────────"
      sed 's/^/         /' "$log"
      echo "         ──────── $name 完整日志结束 ────────"
    fi
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
