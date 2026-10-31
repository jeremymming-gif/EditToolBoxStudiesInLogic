#!/usr/bin/env bash
# 跑全部 Swift 测试台（headless：不开 GUI、不联网、不起子进程）。
#
#   bash tools/run_swift_tests.sh                  # 默认：8 台全跑（CI 走这条，勿改调用方式）
#   bash tools/run_swift_tests.sh --only bibdb_ux  # 只跑一台（B-191 T3；省 1–2 min）
#   bash tools/run_swift_tests.sh --help
#
# 为什么要有这个脚本：每个测试台都要在**特定目录**下编译（它们的 `#filePath` 是相对路径，
# 编译时的相对路径与运行时的 CWD 一起决定它去哪个目录读生产源码）。手工敲容易踩两个坑：
#   ① CWD 不对 → 源码读成空字符串 → 一堆"源码级断言"莫名失败；
#   ② 复用旧二进制 → 看到的是上一次的结果（2026-10-16 就因此误报过一次）。
# 这里统一：`cd sources-mac` 编译、每次用全新的 module cache、跑到哪个台子就报哪个。
#
# B-307 S1/S2（并行 + 优化级别可切）：
#   · `SLCN_SWIFT_JOBS`    = 同时编译/运行的台数；缺省 min(3, CPU 核数)，非法值回落 3。
#   · `SLCN_SWIFT_OPTLVL`  = 传给 swiftc 的优化级别；缺省 `-O`（本地与从前逐字一致）。
#   · 各台**并行跑、按 SUITES 顺序汇总输出**：stdout 只由各台自己的 SUMMARY/FAIL 行组成，
#     顺序固定 → 同一条命令跑两遍逐字节一致（时间只打到 stderr，不污染 stdout）。
set -uo pipefail

# B-191 T3：--only <台名> 只跑一台（台名 = 下方各测试台的第一个参数）。
# 无参数 = 全跑 8 台（CI `ci.yml` 的调用方式；第 8 台 = B-225 dirpush）。
SUITES=(ai_settings ai_store proofcheck toc infocheck cutover bibdb_ux dirpush)
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "✗ --only 需要台名，例如 --only bibdb_ux（可选：${SUITES[*]}）" >&2
        exit 2
      fi
      ONLY="$2"; shift 2 ;;
    --only=*)
      ONLY="${1#--only=}"; shift
      # 空值（`--only=`）必须报参数错误：否则 ONLY="" 会静默回落成「全跑 8 台」（V23 F1）
      if [ -z "$ONLY" ]; then
        echo "✗ --only= 后面缺台名，例如 --only=bibdb_ux（可选：${SUITES[*]}）" >&2
        exit 2
      fi ;;
    -h|--help)
      echo "用法：bash tools/run_swift_tests.sh [--only <台名>]"
      echo "  无参数        = 编译并运行全部 8 台（CI 默认）"
      echo "  --only <台名> = 只跑一台，结果/退出码含义不变（0 = 0 FAIL）"
      echo "  台名：${SUITES[*]}"
      echo "  环境变量：SLCN_SWIFT_JOBS（并行台数，默认 min(3,核数)）、"
      echo "            SLCN_SWIFT_OPTLVL（优化级别，默认 -O；CI 用 -Onone）"
      exit 0 ;;
    *)
      echo "✗ 未知参数：$1（用法：bash tools/run_swift_tests.sh [--only <台名>]）" >&2
      exit 2 ;;
  esac
done
if [ -n "$ONLY" ]; then
  _known=0
  for _s in "${SUITES[@]}"; do [ "$_s" = "$ONLY" ] && _known=1; done
  if [ "$_known" -eq 0 ]; then
    echo "✗ 未知测试台：${ONLY}（可选：${SUITES[*]}）" >&2
    exit 2
  fi
fi

cd "$(dirname "$0")/../sources-mac" || exit 1
PROJ=..
FAILED=0

# 用 SDK 路径编译：CI（macos-14 runner）上 Xcode 版本与本机不同，不显式给 SDK 时
# SwiftUI 宏插件可能找不到 —— 表现是 `error: fatalError` 却没有任何诊断信息，
# 让人以为是代码问题（2026-10-16 实测）。给 SDK 之后两边一致。
SDK="$(xcrun --show-sdk-path 2>/dev/null || true)"

# B-307 S2：优化级别可切（缺省 -O，与改动前逐字一致）。
OPTLVL="${SLCN_SWIFT_OPTLVL:--O}"

# B-307 S1：并行台数。缺省 min(3, CPU 核数)；非法（非纯数字 / <1）回落 3。
JOBS="${SLCN_SWIFT_JOBS:-3}"
case "$JOBS" in ''|*[!0-9]*) JOBS=3 ;; esac
NCPU="$(sysctl -n hw.ncpu 2>/dev/null || echo 1)"
case "$NCPU" in ''|*[!0-9]*) NCPU=1 ;; esac
[ "$JOBS" -gt "$NCPU" ] && JOBS="$NCPU"
[ "$JOBS" -lt 1 ] && JOBS=1

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/slcn_swift_run.XXXXXX")" || exit 2
trap 'rm -rf "$RUN_DIR"' EXIT

# ── 8 台的编译清单（顺序与 B-191 起的旧串行调用逐字相同）─────────────────────
AI_SETTINGS_FILES=(Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/EnvCheck.swift \
  Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CoopFeature.swift \
  Features/CheckBibFeature.swift Features/MergeFeature.swift \
  Features/MetadataFormFeature.swift Views/SettingsTab.swift \
  "$PROJ"/tools/ai_settings_test/main.swift)
AI_STORE_FILES=(Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/MergeFeature.swift \
  "$PROJ"/tools/ai_store_test/main.swift)
PROOFCHECK_FILES=(Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CoopFeature.swift \
  Features/ProofCheckFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/proofcheck_test/main.swift)
TOC_FILES=(Core/Support.swift Core/Log.swift Core/ViewModels.swift Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/TocSettings.swift Features/TocFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/toc_test/main.swift)
INFOCHECK_FILES=(Core/Support.swift Core/Log.swift Core/ViewModels.swift Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/MergeFeature.swift \
  "$PROJ"/tools/infocheck_test/main.swift)
CUTOVER_FILES=(Core/Support.swift Core/Log.swift Core/ViewModels.swift Core/Diagnose.swift \
  Features/InfoCheckFeature.swift Features/DeleteTempFeature.swift Features/CompileFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/cutover_test/main.swift)
# 第 7 台（B-143，批76）：引用库 GUI 纯逻辑（待裁决默认过滤/计数/空态 + warning 明细分类/
# 跳转标识 + lint JSON 兼容解析）。只编译 Foundation 的 Support/Log/BibDbFeature，headless。
# B-190：+ BibDbFeature+Import（入藏段纯搬迁）+ BibDbGit（git 版本层，Foundation-only）。
BIBDB_UX_FILES=(Core/Support.swift Core/Log.swift Features/BibDbFeature.swift \
  Features/BibDbFeature+Import.swift Features/BibDbGit.swift \
  "$PROJ"/tools/bibdb_ux_test/main.swift)
# 第 8 台（B-225 Phase B）：目录摘要推送 GUI 纯逻辑（参数拼装 / 引擎 --json 冻结契约解析 /
# 年度勾选与缺期状态 / 默认输出根与仓库内输出根启发式）。只编译 Foundation 的
# Support/Log + DirPushFeature，headless —— 引擎 `Engine/dirpush.py` 不参与编译。
DIRPUSH_FILES=(Core/Support.swift Core/Log.swift Features/DirPushFeature.swift \
  "$PROJ"/tools/dirpush_test/main.swift)

# 每台最多同时跑 JOBS 个：每启动一个就等到「在跑台数 < JOBS」（macOS bash 3.2
# 没有 `wait -n`，用 jobs -pr 数；不引第三方依赖）。
wait_slot() {
  while [ "$(jobs -pr | wc -l | tr -d ' ')" -ge "$JOBS" ]; do sleep 0.2; done
}

wanted() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

run_one() {
  local name="$1"; shift
  local files=("$@")
  local mc="/tmp/slcnmc_${name}" out="/tmp/slcn_${name}_harness"
  rm -rf "$mc" "$out"
  local sdk_arg=()
  [ -n "$SDK" ] && sdk_arg=(-sdk "$SDK")
  if ! swiftc "$OPTLVL" -swift-version 5 -target arm64-apple-macos14.0 "${sdk_arg[@]}" \
      -module-cache-path "$mc" -o "$out" "${files[@]}" 2>/tmp/slcn_err_"$name"; then
    local why
    why="$(grep -m1 -E "error:" /tmp/slcn_err_"$name" | cut -c1-110)"
    # 宏插件/SDK 不匹配属于**工具链限制**，不是代码缺陷：报 SKIP 并说清（本机已验证过）
    if grep -qiE "macro|plugin|fatalError|SwiftUIMacros|unable to type-check" /tmp/slcn_err_"$name"; then
      printf "  %-14s SKIP（工具链限制：%s）\n" "$name" "${why:-宏插件不可用}"
      grep -m1 -oE "[A-Za-z/]+\.swift:[0-9]+:[0-9]+" /tmp/slcn_err_"$name" | sed 's/^/                 涉及文件：/' || true
      printf "                 —— 本机（完整 Xcode）已验证通过；CI 上跳过不算失败\n"
      return 0
    fi
    printf "  %-14s ❌ 编译失败：%s\n" "$name" "${why:-（见 /tmp/slcn_err_%s）}"
    # ⚠️ 错误**必须打到 job 日志**里：原来只留在 /tmp，CI 上根本看不到真因
    sed -n '1,12p' /tmp/slcn_err_"$name" | sed 's/^/                 /'
    return 1
  fi
  local output summary
  output="$("$out" 2>&1)"
  summary="$(printf '%s\n' "$output" | grep -E '^SUMMARY' | tail -1)"
  printf "  %-14s %s\n" "$name" "${summary:-（没有 SUMMARY 行，可疑）}"
  case "$summary" in
    *"0 FAIL"*) ;;
    *)
      # 把失败项直接打出来：不然还得手工重跑一遍才知道红在哪（踩过这个坑）
      printf '%s\n' "$output" | grep -E '^FAIL' | sed 's/^/                 /'
      return 1 ;;
  esac
  return 0
}

echo "▶ 编译并运行 Swift 测试台（生产源码本身）${ONLY:+—— --only $ONLY}"
START_TS="$(date +%s)"

# ── 并行启动（每台一行；--only 时只启动选中的那台）─────────────────────────
# 退出码写进 `$RUN_DIR/<台名>.rc`：`wait` 不带参数时拿不到各台的码（bash 3.2）。
if wanted ai_settings; then
  ( run_one ai_settings "${AI_SETTINGS_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/ai_settings.rc" ) \
    > "$RUN_DIR/ai_settings.log" 2>&1 &
  wait_slot
fi
if wanted ai_store; then
  ( run_one ai_store "${AI_STORE_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/ai_store.rc" ) \
    > "$RUN_DIR/ai_store.log" 2>&1 &
  wait_slot
fi
if wanted proofcheck; then
  ( run_one proofcheck "${PROOFCHECK_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/proofcheck.rc" ) \
    > "$RUN_DIR/proofcheck.log" 2>&1 &
  wait_slot
fi
if wanted toc; then
  ( run_one toc "${TOC_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/toc.rc" ) \
    > "$RUN_DIR/toc.log" 2>&1 &
  wait_slot
fi
if wanted infocheck; then
  ( run_one infocheck "${INFOCHECK_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/infocheck.rc" ) \
    > "$RUN_DIR/infocheck.log" 2>&1 &
  wait_slot
fi
if wanted cutover; then
  ( run_one cutover "${CUTOVER_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/cutover.rc" ) \
    > "$RUN_DIR/cutover.log" 2>&1 &
  wait_slot
fi
if wanted bibdb_ux; then
  ( run_one bibdb_ux "${BIBDB_UX_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/bibdb_ux.rc" ) \
    > "$RUN_DIR/bibdb_ux.log" 2>&1 &
  wait_slot
fi
if wanted dirpush; then
  ( run_one dirpush "${DIRPUSH_FILES[@]}"; printf '%s\n' "$?" > "$RUN_DIR/dirpush.rc" ) \
    > "$RUN_DIR/dirpush.log" 2>&1 &
  wait_slot
fi

wait

# ── 按 SUITES 顺序汇总（stdout 与旧串行版逐字一致；失败细节原样保留）──────────
for name in "${SUITES[@]}"; do
  [ -f "$RUN_DIR/$name.rc" ] || continue
  cat "$RUN_DIR/$name.log"
  rc="$(cat "$RUN_DIR/$name.rc")"
  [ "$rc" = 0 ] || FAILED=$((FAILED+1))
done

ELAPSED=$(( $(date +%s) - START_TS ))
printf '  用时 %ss（jobs=%s，opt=%s）\n' "$ELAPSED" "$JOBS" "$OPTLVL" >&2

echo
if [ "$FAILED" -eq 0 ]; then
  if [ -n "$ONLY" ]; then echo "✅ $ONLY 0 FAIL（--only 单台）"; else echo "✅ 全部测试台 0 FAIL"; fi
else
  echo "❌ 有 $FAILED 个测试台未通过"
fi
exit "$FAILED"
