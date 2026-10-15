# build-win.ps1 —— 在 Windows 上打出两个 exe（引擎 + 界面）
#
#   pwsh -ExecutionPolicy Bypass -File build-win.ps1
#
# 产出（都在 build\dist\ 下）：
#   SLCNEngine.exe      引擎（三个 CLI 入口冻成一个；用户机器上不需要装 Python）
#   编辑部工具箱.exe     界面（Tkinter 外壳；它会在自己旁边找 SLCNEngine.exe）
#
# ⚠️ 本文件存为 **UTF-8 带 BOM + CRLF**：Windows PowerShell 5.1 读不带 BOM 的 .ps1
#    会按 ANSI（简体中文机器上是 cp936）解码，下面的中文提示会变乱码。
#
# ⚠️ **绝不要** `pip install --target ..\Engine\vendor`：那会把 mac 的编译轮子
#    （lxml 是 .so）覆盖掉，仓库里的开发环境就废了。本脚本一律在 build\ 下另建
#    venv 与 staging 目录，仓库源码一个字节都不改。

$ErrorActionPreference = "Stop"
$ShellDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ShellDir          # EditToolBox\（拆仓后就是 EditToolBox\）
$EngineDir = Join-Path $RepoRoot "Engine"
# 模板位置两种布局（同 build.sh）：拆仓后的工具仓里模板就在仓内；
# 稿件仓里则在 `` 的上一级。先看仓内，再看上一级。
$TplInRepo   = Join-Path $RepoRoot "编辑笔记\模板文件"
$TplInParent = Join-Path (Split-Path -Parent $RepoRoot) "编辑笔记\模板文件"
$TplSrc      = if (Test-Path $TplInRepo) { $TplInRepo } else { $TplInParent }
$BuildDir  = Join-Path $ShellDir "build"
$VenvDir   = Join-Path $BuildDir "venv"
$DistDir   = Join-Path $BuildDir "dist"

Write-Host "▶ 编辑部工具箱 · Windows 打包" -ForegroundColor Cyan
Write-Host "  仓库根：$RepoRoot"

if (-not (Test-Path (Join-Path $EngineDir "engine_main.py"))) {
    throw "找不到 $EngineDir\engine_main.py —— 请把本脚本放在 sources-win\ 下"
}
if (-not (Test-Path $TplSrc)) {
    throw "找不到模板目录：${TplSrc}（引擎靠它生成初排稿，必须一起打包）"
}

# 版本号只有一处：Engine\slcn\__init__.py 的 __version__（与 mac 的 build.sh 同源）
$Version = (Select-String -Path (Join-Path $EngineDir "slcn\__init__.py") `
                          -Pattern '^__version__ = "(.*)"').Matches.Groups[1].Value
if (-not $Version) { $Version = "0.0.0" }
Write-Host "  版本：$Version"

New-Item -ItemType Directory -Force -Path $BuildDir, $DistDir | Out-Null

# ── ① 构建用 venv（装 Windows 轮子 + PyInstaller）─────────────────────────
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Host "▶ 建构建用 venv…" -ForegroundColor Cyan
    python -m venv $VenvDir
}
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
Write-Host "▶ 装依赖（Windows 轮子）…" -ForegroundColor Cyan
& $VenvPip install --disable-pip-version-check --quiet --upgrade pip
& $VenvPip install --disable-pip-version-check --quiet lxml pypdf pypinyin pyinstaller

# ── ② 冻结引擎：三个 CLI 入口 → 一个 exe ────────────────────────────────
Write-Host "▶ 冻结引擎 → SLCNEngine.exe…" -ForegroundColor Cyan
Push-Location $EngineDir
try {
    & $VenvPy -m PyInstaller --noconfirm --clean --onefile --console `
        --name SLCNEngine `
        --distpath $DistDir --workpath (Join-Path $BuildDir "work-engine") `
        --specpath $BuildDir `
        --hidden-import convert --hidden-import checkbib --hidden-import mergepdf `
        --add-data "$TplSrc;编辑笔记\模板文件" `
        engine_main.py
} finally { Pop-Location }

# ── ③ 冻结界面（把引擎 exe 放在它旁边）──────────────────────────────────
Write-Host "▶ 冻结界面 → 编辑部工具箱.exe…" -ForegroundColor Cyan
Push-Location $ShellDir
try {
    & $VenvPy -m PyInstaller --noconfirm --clean --onefile --noconsole `
        --name "编辑部工具箱" `
        --distpath $DistDir --workpath (Join-Path $BuildDir "work-shell") `
        --specpath $BuildDir `
        --add-binary "$DistDir\SLCNEngine.exe;." `
        --add-data "$TplSrc;编辑笔记\模板文件" `
        main.py
} finally { Pop-Location }

# ── ④ 自检：引擎 exe 必须能报版本 ───────────────────────────────────────
Write-Host "▶ 自检…" -ForegroundColor Cyan
$engineExe = Join-Path $DistDir "SLCNEngine.exe"
if (-not (Test-Path $engineExe)) { throw "没打出 SLCNEngine.exe" }
$ver = & $engineExe --version
Write-Host "  $ver"
if ($ver -notmatch [regex]::Escape($Version)) {
    throw "引擎 exe 报的版本与源码不一致（源码 ${Version}，exe：${ver}）"
}

Write-Host ""
Write-Host "✅ 打包完成（版本 ${Version}）" -ForegroundColor Green
Write-Host "   $DistDir\SLCNEngine.exe"
Write-Host "   $DistDir\编辑部工具箱.exe"
Write-Host ""
Write-Host "发布前请在**干净的 Windows** 上双击试一遍（最好是没有 Python 的机器）：" -ForegroundColor Yellow
Write-Host "   1. 双击「编辑部工具箱.exe」，设置页应显示「运行方式：冻结引擎 …」"
Write-Host "   2. 合并文件页选一期目录，跑一遍，看是否出「完成。合并文件：…」"
Write-Host "   3. 未签名 exe 会触发 SmartScreen：更多信息 → 仍要运行（已在文档里写明）"
