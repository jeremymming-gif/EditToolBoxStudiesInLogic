#!/bin/bash
# 构建 macOS 原生「编辑部工具箱」
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ"

# O13（第四十六批）：构建起点（末尾 manifest 的 started_at / duration）。
# 只是证据，不参与任何构建判定。
BUILD_START_ISO="$(date "+%Y-%m-%dT%H:%M:%S%z")"
# B-119（第七十一批）：版本标识 = 版本号 + git commit short sha + build 时间，嵌进 App 的 Info.plist。
#   值只有两个来源，别处不另写一份：commit 取当前 HEAD 的 short sha；build 时间取**构建开始**时刻
#   （与上面的 started_at 同一口径，只截到分钟，例：2026-10-21T13:45）。
#   ⚠️ 只写 Info.plist 与 build manifest —— **不碰 Engine/**，所以包内 cmp 28 项与 engine_py_sha 口径不变。
BUILD_COMMIT="$(git -C "$PROJ" rev-parse --short HEAD 2>/dev/null || true)"
[ -n "$BUILD_COMMIT" ] || BUILD_COMMIT="unknown"
BUILD_TIME="${BUILD_START_ISO:0:16}"
# 工作树是否**脏**（有未提交/未跟踪改动；.gitignore 的构建产物不算）。批末构建顺序固定为
# 「gen_docs 写 → build → cmp → **最后才 commit**」，所以批末 build 时工作树必然是脏的：
# 只显示 HEAD short sha 会让人误以为包内代码 == 该 commit。脏时版本串标 `-dirty`；
# clean 树（发版 tag 构建）仍是不带标记的 `1.2.2+<sha>`。
BUILD_DIRTY="false"
if [ -n "$(git -C "$PROJ" status --porcelain 2>/dev/null || true)" ]; then BUILD_DIRTY="true"; fi
BUILD_DIRTY_PLIST="<false/>"
[ "$BUILD_DIRTY" = "true" ] && BUILD_DIRTY_PLIST="<true/>"
# 引擎 py 指纹也在**开始时**算（并发写者窗口里结束态可能已变；构建打包用的是开始附近那份）。
BUILD_ENGINE_SHA=""
RR_PY="$PROJ/Engine/tools/run_record.py"
if [ -f "$RR_PY" ]; then
  RR_PY_BIN="/opt/homebrew/bin/python3.14"
  [ -x "$RR_PY_BIN" ] || RR_PY_BIN="python3"
  BUILD_ENGINE_SHA="$("$RR_PY_BIN" -c "import sys; sys.path.insert(0, '$PROJ/Engine/tools'); import run_record; print(run_record.engine_py_sha256())" 2>/dev/null || true)"
fi

# ── O14（第四十七批）：purpose 标记 + **失败也留 manifest**（ERR trap）────────
# 真跑 / 验证跑用环境变量区分（O5 计数可机读）：
#   SLCN_BUILD_PURPOSE=verify-build bash build.sh    # 验证构建
#   （不设时默认 real-build；其它候选值见 run_record.py --purpose 的说明）
# 失败路径也写一条 manifest（failed=true + 真实 exit_code + 失败命令），纯证据：
# 记录代码全部 `|| true`，**不改变构建判定与退出码**（trap 执行完 shell 仍以原码退出）。
BUILD_PURPOSE="${SLCN_BUILD_PURPOSE:-real-build}"
_record_build_manifest() {
  local rc="${1:-1}"
  local failed_step="${2:-}"
  if [ ! -f "$RR_PY" ]; then
    echo "⚠ 未找到 Engine/tools/run_record.py，跳过 build manifest（不影响构建）"
    return 0
  fi
  local rec_py="/opt/homebrew/bin/python3.14"
  [ -x "$rec_py" ] || rec_py="python3"
  local app="${APP:-$PROJ/dist/${APP_NAME:-编辑部工具箱}.app}"
  local app_bin="$app/Contents/MacOS/${BINARY:-Toolbox}"
  local app_mtime="unknown" app_py_files="0" failed="false"
  if [ -f "$app_bin" ]; then
    app_mtime="$(stat -f "%Sm" -t "%Y-%m-%dT%H:%M:%S%z" "$app_bin" 2>/dev/null || echo unknown)"
  fi
  if [ -d "$app/Contents/Resources/Engine" ]; then
    app_py_files="$(find "$app/Contents/Resources/Engine" -name '*.py' 2>/dev/null | wc -l | tr -d ' ')"
  fi
  [ "$rc" = "0" ] || failed="true"
  "$rec_py" "$RR_PY" --record-only --label build --purpose "$BUILD_PURPOSE" \
    --started-at "$BUILD_START_ISO" --command "bash build.sh" --exit-code "$rc" \
    --artifact "$app_bin" --engine-sha "$BUILD_ENGINE_SHA" \
    --meta "app_version=${APP_VERSION:-unknown}" --meta "app_mtime=$app_mtime" \
    --meta "build_commit=${BUILD_COMMIT:-unknown}" --meta "build_time=${BUILD_TIME:-unknown}" \
    --meta "build_dirty=${BUILD_DIRTY:-unknown}" \
    --meta "app_py_files=$app_py_files" --meta "cmp_result=${CMP_RESULT:-not-run}" \
    --meta "failed=$failed" --meta "failed_step=${failed_step:0:120}" || true
  return 0
}
trap '_record_build_manifest $? "$BASH_COMMAND"' ERR

export CLANG_MODULE_CACHE_PATH="$PROJ/.mc"
export SWIFT_MODULE_CACHE_PATH="$PROJ/.mc"
mkdir -p "$PROJ/.mc" "$PROJ/.build"

APP_NAME="编辑部工具箱"
BINARY="Toolbox"

echo "▶ 编译 Swift 源码…"
swiftc -O -swift-version 5 -parse-as-library \
  -target arm64-apple-macos14.0 \
  -module-cache-path "$PROJ/.mc" \
  sources-mac/*.swift sources-mac/Core/*.swift sources-mac/Features/*.swift sources-mac/Views/*.swift \
  -o "$PROJ/.build/$BINARY"

APP="$PROJ/dist/$APP_NAME.app"
echo "▶ 组装 .app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PROJ/.build/$BINARY" "$APP/Contents/MacOS/$BINARY"

# 版本号**只有一处**：`Engine/slcn/__init__.py` 的 `__version__`（第 86 条）。
# 从前这里写死 "1.0"，加了 Windows 版之后两份版本必然漂移——所以改成现取。
# 取不到就退回 0.0（宁可显示得难看，也不要让构建挂掉）。
APP_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJ/Engine/slcn/__init__.py" | head -1)"
[ -n "$APP_VERSION" ] || APP_VERSION="0.0"
echo "   版本 ${APP_VERSION}（取自 Engine/slcn/__init__.py）"
BUILD_DISPLAY="${APP_VERSION}+${BUILD_COMMIT}"
[ "$BUILD_DIRTY" = "true" ] && BUILD_DISPLAY="${BUILD_DISPLAY}-dirty"
echo "   构建标识 ${BUILD_DISPLAY}（${BUILD_TIME}；commit=HEAD short sha，时间=构建开始，dirty=工作树有未提交改动）"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$BINARY</string>
    <key>CFBundleIdentifier</key><string>cn.edu.sysu.logic.toolbox</string>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
    <key>CFBundleVersion</key><string>$APP_VERSION</string>
    <!-- B-119：构建标识（设置页读它显示「1.2.2+commit[-dirty] (build 时间)」）→
         由上面的 BUILD_COMMIT / BUILD_TIME / BUILD_DIRTY 注入；缺失时 App 只显示版本号，不假装。
         dirty=true = 构建时工作树有未提交改动（批末 build 属于这种：commit 在 build 之后）。 -->
    <key>SLCNBuildCommit</key><string>$BUILD_COMMIT</string>
    <key>SLCNBuildTime</key><string>$BUILD_TIME</string>
    <key>SLCNBuildDirty</key>$BUILD_DIRTY_PLIST
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict>
</plist>
PLIST

if [ -f "$PROJ/Resources/AppIcon.icns" ]; then
  cp "$PROJ/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$APP/Contents/Info.plist" >/dev/null 2>&1 || true
fi

# 把 Python 转换引擎打进 app 包（Swift 无法直接调仓库里相对路径，且用户可能把 app 单独拷走）
# 引擎与界面同属一个项目：Engine/
CONVERT_SRC="$PROJ/Engine"
if [ -f "$CONVERT_SRC/convert.py" ]; then
  DEST="$APP/Contents/Resources/Engine"
  echo "▶ 打包转换引擎…"
  rm -rf "$DEST"
  mkdir -p "$DEST"
  cp "$CONVERT_SRC/convert.py" "$DEST/"
  [ -f "$CONVERT_SRC/README.md" ] && cp "$CONVERT_SRC/README.md" "$DEST/"
  # 参考文献回读校验（A1，2026-10-14）：编辑拿《文献核实任务书》改完交回 .bib 后由它体检。
  # 纯离线、只用标准库 + slcn/，所以放进包内；GUI 加按钮时直接调它，不必再改打包逻辑。
  # （docx_audit.py / refaudit.py / corpus.py 等开发期工具仍不进包。）
  [ -f "$CONVERT_SRC/checkbib.py" ] && cp "$CONVERT_SRC/checkbib.py" "$DEST/"
  # 合并一期的 PDF（第 88 条，2026-10-15）：合并规则从 mac 外壳搬进引擎，
  # 好让将来的 Windows 外壳复用同一份逻辑。mac 目前仍走 Swift + PDFKit 那条，
  # 但把它打进包零成本，将来 GUI 切过来时不必再改打包逻辑。
  [ -f "$CONVERT_SRC/mergepdf.py" ] && cp "$CONVERT_SRC/mergepdf.py" "$DEST/"
  # 期目录级动作与一校核查（第 91/92 条，2026-10-15 从 mac 版搬进引擎）：
  # mac 界面暂时还走 Swift 那份，但打进包零成本，将来加标签页时不必再改打包逻辑。
  # ⚠️ 白名单必须覆盖**所有交付 CLI**：2026-10-17 加了 `publish.py`（本期出刊向导）但这里漏了，
  #    包里的 app 报「未找到出刊编排引擎 Engine/publish.py」——实例测试当场抓到。
  #    加新入口时，`Engine/tests/test_platform.py` 的 ENTRY_FILES 与这里**两处都要改**。
  # 第 132 条：`bibdb.py` 是两个平台界面「引用库」5 页（库概况/查库/入藏/待裁决/设置）
  # 的数据源，漏了它 = mac 包里这 5 页全部报「未找到 Engine/bibdb.py」。
  # Windows 包同样交付它（`engine_main.ENTRIES` / `sources-win/engine.py` 四处入口表）。
  # 它也是**交付入口**：`test_platform.py` 的 ENTRY_FILES 与这里两处都要改。
  for _extra in cleantemp.py compileall.py proofcheck.py infocheck.py toc.py publish.py engine_main.py bibdb.py dirpush.py; do
    [ -f "$CONVERT_SRC/$_extra" ] && cp "$CONVERT_SRC/$_extra" "$DEST/"
  done
  cp -R "$CONVERT_SRC/slcn" "$DEST/slcn"
  # pypinyin 是项目本地依赖，一并带上，免得用户还要自己装
  if [ -d "$CONVERT_SRC/vendor" ]; then
    cp -R "$CONVERT_SRC/vendor" "$DEST/vendor"
  fi
  find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  # 模板文件：引擎按「从 slcn/ 逐级向上找 编辑笔记/模板文件」定位
  # （见 Engine/slcn/templates.py），所以放到 Resources 下同名位置即可，无需改代码。
  # 模板位置有**两种布局**（2026-10-15 拆仓后）：
  #   稿件仓里：<稿件仓>/  ← 模板在上一级 <稿件仓>/编辑笔记/模板文件
  #   工具仓里：<工具仓>/             ← 模板就在仓内 编辑笔记/模板文件
  # 先看仓内、再看上一级，两种布局都能构建出「包内带模板」的 app。
  if [ -d "$PROJ/编辑笔记/模板文件" ]; then
    TPL_SRC="$PROJ/编辑笔记/模板文件"
  else
    TPL_SRC="$PROJ/../编辑笔记/模板文件"
  fi
  if [ -d "$TPL_SRC" ]; then
    mkdir -p "$APP/Contents/Resources/编辑笔记"
    cp -R "$TPL_SRC" "$APP/Contents/Resources/编辑笔记/模板文件"
    echo "  模板 $(ls "$APP/Contents/Resources/编辑笔记/模板文件" | wc -l | tr -d ' ') 个文件"
  else
    echo "  ⚠ 未找到 ${TPL_SRC}，app 内将无法解析模板"
  fi
  find "$DEST" -name '*.pyc' -delete 2>/dev/null || true
  echo "  $(find "$DEST" -name '*.py' | wc -l | tr -d ' ') 个 Python 文件"
else
  echo "⚠ 未找到 $CONVERT_SRC/convert.py，跳过打包（app 将依赖仓库内路径）"
fi

echo "▶ ad-hoc 签名…"
codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "  （签名跳过，不影响本机运行）"

# ── 包内引擎一致性 cmp（第四十四批③a / O9，毫秒级）────────────────────────
# 从前这条检查在 gen_docs 的 AUTO 块里（要跑 test_selfcheck→selfcheck 才触发）：
# 引擎改动后、App 尚未重建时出现 FAIL 瞬态 → AUTO 记下 FAIL → 必须再 gen_docs、再 build，
# 形成「gen_docs×2 + build×2」循环。现在：AUTO 不再含这行，**构建末尾直接 cmp** 白名单
# （`gen_docs.APP_FILES`，28 个文件，单一出处）。失败 → 本脚本非 0 退出（不打印「构建完成」），
# 成功 → 打印「✅ 28/28 … 毫秒级」。只读、不依赖稿件库。
if [ -d "$APP/Contents/Resources/Engine" ]; then
  echo "▶ 包内引擎一致性 cmp（白名单 28 个文件）…"
  PY_CMP="/opt/homebrew/bin/python3.14"
  [ -x "$PY_CMP" ] || PY_CMP="python3"
  # O13：包一层只为了**拿到结果写 manifest**（判定/输出/退出码语义完全不变：
  # 失败照样非 0 退出、不打印「构建完成」）。
  if CMP_OUT="$("$PY_CMP" "$PROJ/Engine/tools/gen_docs.py" --cmp-app 2>&1)"; then
    CMP_RESULT="ok"
  else
    CMP_RESULT="FAIL"
  fi
  echo "$CMP_OUT"
  if [ "$CMP_RESULT" != "ok" ]; then
    echo "❌ 包内引擎 cmp 失败 —— 构建未通过（包内引擎与仓库不一致）"
    _record_build_manifest 1 "cmp-app 失败"      # O14：失败也留 manifest
    exit 1
  fi
else
  echo "⚠ 包内没有 Engine/（上面的打包被跳过），跳过包内 cmp"
  CMP_RESULT="skipped"
fi

SIZE=$(du -sh "$APP" | cut -f1)
echo "✅ 构建完成：$APP  ($SIZE)"

# ── O13/O14（第四十六/四十七批）：build 重活指纹落盘 ──────────────────────
# 写 /tmp/audit_lead/runs/<时间戳>-build.json：purpose（real-build / verify-build）、
# App 时间/版本/包内文件数/cmp 结果、命令/引擎 py 指纹/二进制 sha/退出码。
# **纯证据**：run_record 缺失、写失败都只告警，不影响构建结果与退出码。
_record_build_manifest 0
trap - ERR
