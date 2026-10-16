#!/bin/bash
# 构建命令行测试工具（与 App 共用同一份功能代码）
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CLANG_MODULE_CACHE_PATH="$PROJ/.mc"
export SWIFT_MODULE_CACHE_PATH="$PROJ/.mc"
mkdir -p "$PROJ/.mc" "$PROJ/.build"

swiftc -O -swift-version 5 \
  -target arm64-apple-macos14.0 \
  -module-cache-path "$PROJ/.mc" \
  "$PROJ/sources-mac/Core/Log.swift" \
  "$PROJ/sources-mac/Core/Support.swift" \
  "$PROJ/sources-mac/Core/ViewModels.swift" \
  "$PROJ/sources-mac/Features/MergeFeature.swift" \
  "$PROJ/sources-mac/Features/DeleteTempFeature.swift" \
  "$PROJ/sources-mac/Features/CompileFeature.swift" \
  "$PROJ/sources-mac/Features/InfoCheckFeature.swift" \
  "$PROJ/sources-mac/Features/ContentTemplates.swift" \
  "$PROJ/sources-mac/Features/TocSettings.swift" \
  "$PROJ/sources-mac/Features/TocFeature.swift" \
  "$PROJ/tools/cli/main.swift" \
  -o "$PROJ/.build/toolbox-cli"

echo "✅ 已构建 $PROJ/.build/toolbox-cli"
