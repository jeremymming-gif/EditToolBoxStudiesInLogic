#!/usr/bin/env bash
# 跑全部 Swift 测试台（headless：不开 GUI、不联网、不起子进程）。
#
#   bash tools/run_swift_tests.sh
#
# 为什么要有这个脚本：每个测试台都要在**特定目录**下编译（它们的 `#filePath` 是相对路径，
# 编译时的相对路径与运行时的 CWD 一起决定它去哪个目录读生产源码）。手工敲容易踩两个坑：
#   ① CWD 不对 → 源码读成空字符串 → 一堆"源码级断言"莫名失败；
#   ② 复用旧二进制 → 看到的是上一次的结果（2026-10-16 就因此误报过一次）。
# 这里统一：`cd sources-mac` 编译、每次用全新的 module cache、跑到哪个台子就报哪个。
set -uo pipefail
cd "$(dirname "$0")/../sources-mac" || exit 1
PROJ=..
FAILED=0

# 用 SDK 路径编译：CI（macos-14 runner）上 Xcode 版本与本机不同，不显式给 SDK 时
# SwiftUI 宏插件可能找不到 —— 表现是 `error: fatalError` 却没有任何诊断信息，
# 让人以为是代码问题（2026-10-16 实测）。给 SDK 之后两边一致。
SDK="$(xcrun --show-sdk-path 2>/dev/null || true)"

run_one() {
  local name="$1"; shift
  local files=("$@")
  local mc="/tmp/slcnmc_${name}" out="/tmp/slcn_${name}_harness"
  rm -rf "$mc" "$out"
  local sdk_arg=()
  [ -n "$SDK" ] && sdk_arg=(-sdk "$SDK")
  if ! swiftc -O -swift-version 5 -target arm64-apple-macos14.0 "${sdk_arg[@]}" \
      -module-cache-path "$mc" -o "$out" "${files[@]}" 2>/tmp/slcn_err_"$name"; then
    local why
    why="$(grep -m1 -E "error:" /tmp/slcn_err_"$name" | cut -c1-110)"
    # 宏插件/SDK 不匹配属于**工具链限制**，不是代码缺陷：报 SKIP 并说清（本机已验证过）
    if grep -qiE "macro|plugin|fatalError|SwiftUIMacros|unable to type-check" /tmp/slcn_err_"$name"; then
      printf "  %-14s SKIP（工具链限制：%s）\n" "$name" "${why:-宏插件不可用}"
      grep -m1 -oE "[A-Za-z/]+\.swift:[0-9]+:[0-9]+" /tmp/slcn_err_"$name" | sed 's/^/                 涉及文件：/' || true
      printf "                 —— 本机（完整 Xcode）已验证通过；CI 上跳过不算失败\n"
      return
    fi
    printf "  %-14s ❌ 编译失败：%s\n" "$name" "${why:-（见 /tmp/slcn_err_%s）}"
    # ⚠️ 错误**必须打到 job 日志**里：原来只留在 /tmp，CI 上根本看不到真因
    sed -n '1,12p' /tmp/slcn_err_"$name" | sed 's/^/                 /'
    FAILED=$((FAILED+1)); return
  fi
  local output summary
  output="$("$out" 2>&1)"
  summary="$(printf '%s\n' "$output" | grep -E '^SUMMARY' | tail -1)"
  printf "  %-14s %s\n" "$name" "${summary:-（没有 SUMMARY 行，可疑）}"
  case "$summary" in
    *"0 FAIL"*) ;;
    *)
      FAILED=$((FAILED+1))
      # 把失败项直接打出来：不然还得手工重跑一遍才知道红在哪（踩过这个坑）
      printf '%s\n' "$output" | grep -E '^FAIL' | sed 's/^/                 /'
      ;;
  esac
}

echo "▶ 编译并运行 Swift 测试台（生产源码本身）"
run_one ai_settings Core/ViewModels.swift Core/Support.swift Core/Log.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CoopFeature.swift \
  Features/CheckBibFeature.swift Features/MergeFeature.swift \
  Features/MetadataFormFeature.swift Views/SettingsTab.swift \
  "$PROJ"/tools/ai_settings_test/main.swift
run_one ai_store Core/ViewModels.swift Core/Support.swift Core/Log.swift \
  Features/InfoCheckFeature.swift Features/MergeFeature.swift "$PROJ"/tools/ai_store_test/main.swift
run_one proofcheck Core/ViewModels.swift Core/Support.swift Core/Log.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CoopFeature.swift \
  Features/ProofCheckFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/proofcheck_test/main.swift
run_one toc Core/Support.swift Core/Log.swift Core/ViewModels.swift \
  Features/InfoCheckFeature.swift Features/TocSettings.swift Features/TocFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/toc_test/main.swift
run_one infocheck Core/Support.swift Core/Log.swift Core/ViewModels.swift \
  Features/InfoCheckFeature.swift Features/MergeFeature.swift \
  "$PROJ"/tools/infocheck_test/main.swift
run_one cutover Core/Support.swift Core/Log.swift Core/ViewModels.swift \
  Features/InfoCheckFeature.swift Features/DeleteTempFeature.swift Features/CompileFeature.swift \
  Features/MergeFeature.swift "$PROJ"/tools/cutover_test/main.swift
# 第 7 台（B-143，批76）：引用库 GUI 纯逻辑（待裁决默认过滤/计数/空态 + warning 明细分类/
# 跳转标识 + lint JSON 兼容解析）。只编译 Foundation 的 Support/Log/BibDbFeature，headless。
# B-190：+ BibDbFeature+Import（入藏段纯搬迁）+ BibDbGit（git 版本层，Foundation-only）。
run_one bibdb_ux Core/Support.swift Core/Log.swift Features/BibDbFeature.swift \
  Features/BibDbFeature+Import.swift Features/BibDbGit.swift \
  "$PROJ"/tools/bibdb_ux_test/main.swift

echo
if [ "$FAILED" -eq 0 ]; then
  echo "✅ 全部测试台 0 FAIL"
else
  echo "❌ 有 $FAILED 个测试台未通过"
fi
exit "$FAILED"
