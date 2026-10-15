#!/bin/bash
# 构建 macOS 原生「编辑部工具箱」
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ"

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

SIZE=$(du -sh "$APP" | cut -f1)
echo "✅ 构建完成：$APP  ($SIZE)"
