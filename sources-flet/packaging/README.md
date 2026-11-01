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

macOS（`.app` + 包内 sidecar）：

```
编辑部工具箱.app/
  Contents/MacOS/编辑部工具箱      # flet pack 产物
  Contents/MacOS/SLCNEngine         # PyInstaller onefile 引擎（必须同目录，bridge 才能找到）
FIRST-OPEN-macOS.txt
sha256.txt
→ EditToolBox-<Engine版本>-macOS-arm64.zip
```

`engine_bridge.find_engine_exe()` 的顺序：`P2_ENGINE_EXE/SLCN_ENGINE_EXE` → 可执行文件同目录
`SLCNEngine.exe|SLCNEngine` → 仓库根。macOS 放 `.app` 外层是找不到的，必须 `Contents/MacOS/`。

## CI 行为

- 触发：`workflow_dispatch` 手动 + `push`（paths：`sources-flet/**`、本 workflow、`编辑笔记/模板文件/**`）。
- matrix：`windows-latest` + `macos-latest`，`fail-fast: false`；Python 3.14。
- 版本号唯一出处：`Engine/slcn/__init__.py` 的 `__version__`（zip 名与 Windows exe 元数据同源）。
- artifact：`actions/upload-artifact@v4`，name `flet-<os>-<版本>`；zip 的 sha256 打印在运行摘要里。
- **不做** tag / Release / 签名；E2E 发版仍需人工确认（B-246c 红线）。

## 品牌资产（图标）

`packaging/assets/` 是窗口图标、侧栏标志与托盘图标的单一来源，全部由脚本从矢量参数重新生成：

```powershell
py -3.14 sources-flet/packaging/assets/make_logo.py   # 需 Pillow，重写下列文件
```

- `logo-1024.png` / `logo-256.png`：方形母版与侧栏标志（`ui/brand.py` 运行时读）；
- `logo.ico`（7 个尺寸）：Windows `flet pack --icon`；
- `logo.icns`：macOS `flet pack --icon`。

`flet pack` 命令里加 `--icon` 才会把图标写进 exe / `.app`（CI 的 `build-flet.yml`、`release.yml` 已带）；
`ui/brand.py` 找不到资产时按图标回退，托盘也回退到运行时绘制，不影响启动。

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
# 3) 组装：把 sidecar 输出目录里的 SLCNEngine.exe 复制到 build-flet\dist\ 再试运行
```

macOS 引擎 sidecar 等价命令（CI 里就是这么做的）：

```bash
cd Engine
python -m PyInstaller --noconfirm --clean --onefile --console --name SLCNEngine \
  --distpath ../build-flet/dist-engine --workpath ../build-flet/work-engine --specpath ../build-flet \
  --hidden-import convert --hidden-import checkbib --hidden-import mergepdf \
  --hidden-import cleantemp --hidden-import compileall --hidden-import proofcheck \
  --hidden-import infocheck --hidden-import toc --hidden-import publish \
  --hidden-import bibdb --hidden-import envcheck --hidden-import diagnose --hidden-import dirpush \
  --add-data "$GITHUB_WORKSPACE/编辑笔记/模板文件:编辑笔记/模板文件" \
  engine_main.py
```

注：入口表必须与 `Engine/engine_main.py: ENTRIES` 逐项一致（Windows 侧由 `build-win.ps1` 维护，
两边同表）；模板目录 `编辑笔记/模板文件` 必须打进包，否则 convert 生成不了初排稿。

> 图标路径说明：传 `--pyinstaller-build-args=--workpath/--specpath` 时，PyInstaller 按 spec 文件目录解析 `--icon`，请给**绝对路径**；直接 `flet pack`（默认 specpath=当前目录）时相对路径即可。
