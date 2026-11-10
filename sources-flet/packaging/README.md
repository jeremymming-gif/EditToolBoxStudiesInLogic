# sources-flet 打包（P4）

本目录服务于 GitHub Actions 流水线 `.github/workflows/build-flet.yml`：把 Flet 单壳
（`sources-flet/app`）与引擎 sidecar（`SLCNEngine`）打成 **Windows / macOS** 两个平台的可下载产物。
正式发布（tag/Release）仍走 `release.yml`，本流水线只产 artifact，不发布、不签名。

## 产物布局

Windows（onefile exe + 同目录 sidecar）：

```
编辑部工具箱.exe         # flet pack 产物
SLCNEngine.exe             # 引擎 sidecar（由 Engine/tools/build_win_sidecar.ps1 生成）
FIRST-OPEN-Windows.txt
sha256.txt
→ EditToolBox-<Engine版本>-Windows-x64.zip
```

macOS（`.app` + 包内目录化 sidecar + 随包 Flet 客户端）：

```
编辑部工具箱.app/
  Contents/MacOS/编辑部工具箱               # flet pack 产物
  Contents/MacOS/SLCNEngine                 # sh launcher（转调 Resources 里的真身，旧路径命令照旧）
  Contents/Resources/SLCNEngine/            # PyInstaller --onedir 引擎（目录形态）
    SLCNEngine
    _internal/
    sha256.txt
  Contents/Resources/flet-client/Flet.app   # 官方 Flet 桌面客户端（运行期 FLET_VIEW_PATH 指过去）
  Contents/Resources/LICENSE                # 随包许可（M07；与构建提交仓库根逐字节一致）
  Contents/Resources/THIRD_PARTY.md         # 第三方组件与来源许可（M07）
  Contents/Resources/LICENSE-SCOPE.md       # 许可适用范围说明（M07）
编辑部工具箱                                # flet pack dist 原样带入的散装可执行文件（zip 根，与 .app 内主程序并存；workflow 既有形状，r1=76,228,160B）
FIRST-OPEN-macOS.txt
LICENSE                                     # 随包许可（M07；zip 根同样各一份）
THIRD_PARTY.md
LICENSE-SCOPE.md
→ EditToolBox-<Engine版本>-macOS-arm64.zip
```

`engine_bridge.find_engine_exe()` 的顺序：`P2_ENGINE_EXE/SLCN_ENGINE_EXE` → frozen 时先看
可执行文件同目录、再看 macOS 的 `Contents/Resources/`（onedir 形态 `SLCNEngine/SLCNEngine`）
→ 仓库根。所以 macOS 的 sidecar 必须放 `Contents/Resources/SLCNEngine/`（`Contents/MacOS/SLCNEngine`
只是 launcher；`.app` 外层则找不到）。

> 随包客户端是**官方缓存里未改动的 Flet.app**：flet pack 在 macOS 构建时会把内嵌 shell
> 用 `codesign --deep` 重签且不带 entitlements，会剥掉 file picker 需要的
> `com.apple.security.files.user-selected.read-write`，运行期报 `ENTITLEMENT_NOT_FOUND`。
> 因此构建后必须把 `flet_desktop.ensure_client_cached()` 的 `*.app` 用 `ditto` 复制到
> `Contents/Resources/flet-client/Flet.app`，运行期由 `main.py:_launch()` 设 `FLET_VIEW_PATH`
> 使用这份官方签名客户端（首启也不再下载/解压 ~135MB）。

### 签名与封条（R08）

macOS 产物是 **ad-hoc 签名**（`codesign -s -`，无 Team ID、未公证）。组装末尾必须补两步
重签，否则 `codesign --verify --deep --strict` 会报 `invalid Info.plist (plist or signature
have been modified)`（PlistBuddy 改 Info.plist 已使 PyInstaller 的 ad-hoc 封条失效）：

```bash
# launcher 是 Contents/MacOS 下的嵌套代码，先自签（否则外层报 code object is not signed at all）
codesign --force --sign - "编辑部工具箱.app/Contents/MacOS/SLCNEngine"
# PlistBuddy 改过 Info.plist：只重签外层（**绝不加 --deep**，否则会再次剥掉内嵌
# flet-client/Flet.app 的 entitlements，文件选择器会退回 ENTITLEMENT_NOT_FOUND）
codesign --force --sign - "编辑部工具箱.app"
# 自检必须 rc0（无输出）
codesign --verify --deep --strict "编辑部工具箱.app"
```

说明：ad-hoc 重签不等于发行签名——Gatekeeper 首次打开仍需右键「打开」（见
`FIRST-OPEN-macOS.txt`）；它只保证包内封条自洽、可被 `codesign --verify` 校验。

### 随包许可（M07）

仓库根 `LICENSE`、`THIRD_PARTY.md`、`LICENSE-SCOPE.md` 三件必须**随包**：在「组装 pkg-mac」
段（复制 `FIRST-OPEN-macOS.txt` 之后，随包 Flet 客户端 / Info.plist 收口 / 重签**之前**）
复制到两处各一份：

- `编辑部工具箱.app/Contents/Resources/`（应用内可见）
- `pkg-mac/`（zip 根，解包即读）

并用 `test -f` + `cmp` 断言两处都存在且与**构建提交里的仓库根文件**逐字节一致（断言失败即
构建失败）。构建树由 `git archive <BUILD_COMMIT>` 导出，因此仓库根文件就是该提交的版本；
复核同一口径可用 `git show <BUILD_COMMIT>:<文件名>` 与包内文件 `cmp`。三件内容由发版/法务
维护，打包链**不得修改**其内容（不得追加/裁剪/换行归一）。

## CI 行为

- 触发：**仅 `workflow_dispatch` 手动**（发版前验证用，2026-11-01 去冗余后不再随 push 打包；日常 push 的 Windows 验证由 `ci.yml` 承担）。
- matrix：`windows-latest` + `macos-latest`，`fail-fast: false`；Python 3.14。
- 版本号唯一出处：`Engine/slcn/__init__.py` 的 `__version__`（zip 名与 Windows exe 元数据同源）。
- artifact：`actions/upload-artifact@v4`，name `flet-<os>-<版本>`；zip 的 sha256 打印在运行摘要里。
- **不做** tag / Release / 签名；E2E 发版仍需人工确认（B-246c 红线）。

## 品牌资产（图标）

`packaging/assets/` 是窗口图标、侧栏标志与托盘图标的单一来源。
**P3 起打包 / 窗口 / 托盘统一用玻璃 slate 版**（DECISIONS D1-115-03：默认母版
`glass-slate-ink-1024.png` —— 深色 ℵ + 浅灰玻璃板；`glass-slate-white-1024.png`
是白 ℵ 备选，切换只改 `make_pack_icons.py` 的 `MASTER` 一行）。

```bash
python sources-flet/packaging/assets/make_pack_icons.py   # 需 Pillow + 系统 iconutil（mac）
python sources-flet/packaging/assets/make_logo.py         # 只重写旧品牌 logo-1024/256.png
```

- `logo.icns`：macOS `flet pack --icon`；由 `iconutil` 打包 iconset（16/32/64/128/256/512 + @2x，含 1024；iconset 建在临时目录，不入库）；
- `logo.ico`：Windows `flet pack --icon` 与窗口/任务栏图标；Pillow 写 16/32/48/64/128/256 六档；
- `glass-slate-ink-64.png` / `glass-slate-ink-256.png`：托盘 / 窗口 PNG 派生（`ui/brand.py` 运行时读）；
- 文件名 `logo.icns` / `logo.ico` **保持不变**：`build-flet.yml`、`release.yml` 已按名引用，改名会动发版链；
- `logo-1024.png` / `logo-256.png`：旧品牌方形母版（`ui/brand.py` 的兼容回退链）；
- `aleph-slate-64.png` / `aleph-slate-light-64.png`：侧栏品牌 ℵ（浅色 / 深色主题，`ui/brand.py` 运行时读）；
- `aleph-slate-1024.png`：品牌 ℵ 的 1024 母版；`glass-slate-ink-1024.png` / `glass-slate-white-1024.png`：Dock/窗口图标 1024 母版（前者为默认）。

`flet pack` 命令里加 `--icon` 才会把图标写进 exe / `.app`（CI 的 `build-flet.yml`、`release.yml` 已带；
macOS 仅 `--icon <绝对或相对 logo.icns>`，无其它图标步骤）；
`ui/brand.py` 找不到资产时逐级回退（窗口图标 `.ico` → 256 PNG → 旧 logo-256；托盘 → 运行时绘制），不影响启动。

## 本地复刻（Windows）

```powershell
# 1) 引擎 sidecar（只构建 SLCNEngine.exe）
pwsh -ExecutionPolicy Bypass -File Engine/tools/build_win_sidecar.ps1
# 2) Flet 应用（等价于 CI 命令）
New-Item -ItemType Directory -Force build-flet | Out-Null
Push-Location build-flet
flet pack ..\sources-flet\app\main.py --name 编辑部工具箱 `
  --product-name "编辑部工具箱" --product-version "<Engine版本>" --file-version "<Engine版本>.0" `
  --company-name "SLCN Toolbox" --copyright "SLCN Toolbox" --distpath dist --yes `
  --icon ..\sources-flet\packaging\assets\logo.ico
Pop-Location
# 3) 组装：workflow 用 `dist/win-sidecar/dist/SLCNEngine.exe`（build_win_sidecar.ps1 产物）复制到 build-flet\dist\ 再试运行
```

macOS 本地复刻（CI 里就是这么做的；产物布局见上，步骤与 `.github/workflows/build-flet.yml`
的 macOS 段逐条一致，**不要改回 onefile**）：

```bash
# 1) 引擎 sidecar（目录形态）
cd Engine
python -m PyInstaller --noconfirm --clean --onedir --console --name SLCNEngine \
  --distpath ../build-flet/dist-engine --workpath ../build-flet/work-engine --specpath ../build-flet \
  --hidden-import convert --hidden-import checkbib --hidden-import mergepdf \
  --hidden-import cleantemp --hidden-import compileall --hidden-import proofcheck \
  --hidden-import infocheck --hidden-import toc --hidden-import publish \
  --hidden-import bibdb --hidden-import envcheck --hidden-import diagnose --hidden-import dirpush \
  --add-data "../编辑笔记/模板文件:编辑笔记/模板文件" \
  engine_main.py
cd ..
rm -rf build-flet/SLCNEngine
cp -R build-flet/dist-engine/SLCNEngine build-flet/SLCNEngine
chmod +x build-flet/SLCNEngine/SLCNEngine
./build-flet/SLCNEngine/SLCNEngine --version

# 2) Flet 应用（.app；版本/包标识等第 3 步用 PlistBuddy 收口，不用 flet pack 参数）
( cd build-flet && flet pack ../sources-flet/app/main.py --name 编辑部工具箱 \
    --icon ../sources-flet/packaging/assets/logo.icns --distpath dist --yes \
    --hidden-import ui.bib_overview ui.bib_query ui.bib_normalize ui.bib_import ui.bib_review \
    --hidden-import ui.bib_edit ui.bib_dupes ui.bib_fieldfix ui.bib_health ui.bib_prefs )

# 3) 组装（照抄 workflow 的 macOS 段）：
#    ditto dist → pkg-mac；sidecar 目录 → Contents/Resources/SLCNEngine/；
#    Contents/MacOS/SLCNEngine 写 sh launcher；FIRST-OPEN-macOS.txt → pkg-mac/；
#    仓库根 LICENSE / THIRD_PARTY.md / LICENSE-SCOPE.md → pkg-mac/ 与
#      Contents/Resources/ 各一份，并 cmp 断言与构建树逐字节一致（M07）；
#    官方缓存客户端 → Contents/Resources/flet-client/Flet.app（运行期 FLET_VIEW_PATH 用）；
#    PlistBuddy 写 LSMultipleInstancesProhibited + CFBundleVersion/CFBundleShortVersionString=引擎版本；
#    launcher + 外层 ad-hoc 重签（codesign --force --sign - …；不要 --deep）；
#    codesign --verify --deep --strict 应 rc0；
#    zip = EditToolBox-<引擎版本>-macOS-arm64.zip。
```

> 注意：`flet pack` 构建时会重签内嵌 Flet shell 且不带 entitlements。包内必须另带
> `flet_desktop.ensure_client_cached()` 的官方 Flet.app 并由运行期 `FLET_VIEW_PATH` 使用
> （见上「产物布局」），否则文件选择器报 `ENTITLEMENT_NOT_FOUND`；`flet pack` 不要加
> `--codesign-identity`（本流水线不签名）。

注：入口表必须与 `Engine/engine_main.py: ENTRIES` 逐项一致（Windows 侧由 `Engine/tools/build_win_sidecar.ps1` 维护，
两边同表）；模板目录 `编辑笔记/模板文件` 必须打进包，否则 convert 生成不了初排稿。

> 图标路径说明：传 `--pyinstaller-build-args=--workpath/--specpath` 时，PyInstaller 按 spec 文件目录解析 `--icon`，请给**绝对路径**；直接 `flet pack`（默认 specpath=当前目录）时相对路径即可。
