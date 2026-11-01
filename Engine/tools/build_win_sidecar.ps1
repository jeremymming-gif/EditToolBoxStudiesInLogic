# build_win_sidecar.ps1 —— 在 Windows 上打出**引擎 sidecar**：SLCNEngine.exe
#
#   pwsh -ExecutionPolicy Bypass -File Engine\tools\build_win_sidecar.ps1
#
# 来历（历史）：本脚本原是从 sources-win\build-win.ps1 拆出来的「引擎」那一段
#   （旧 Windows Tkinter 外壳已于批108 删除；sidecar 本身仍是 Flet 外壳的必需件）。
#   与旧版相比只少了「冻结界面 exe」一节，引擎冻结与自检逻辑逐项照旧。
#
# 产出（默认在仓根 dist\win-sidecar\dist\ 下，dist/ 已 gitignore）：
#   SLCNEngine.exe      引擎（**十三个入口**冻成一个；用户机器上不需要装 Python）
#
# ⚠️ **绝不要** `pip install --target ..\Engine\vendor`：那会把 mac 的编译轮子
#    （lxml 是 .so）覆盖掉，仓库里的开发环境就废了。本脚本一律在 dist\win-sidecar\
#    下另建 venv 与 work 目录，仓库源码一个字节都不改。
#
# 参数（CI 不用传，默认值即可）：
#   -BaseDir  默认 <仓根>\dist\win-sidecar；venv/work/dist 都在这下面
#   -DistDir  默认 <BaseDir>\dist
#   -WorkDir  默认 <BaseDir>\work
#   -VenvDir  默认 <BaseDir>\venv

param(
    [string]$BaseDir = "",
    [string]$DistDir = "",
    [string]$WorkDir = "",
    [string]$VenvDir = ""
)

$ErrorActionPreference = "Stop"
$ToolDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$EngineDir = Split-Path -Parent $ToolDir
$RepoRoot  = Split-Path -Parent $EngineDir

if (-not $BaseDir) { $BaseDir = Join-Path $RepoRoot "dist\win-sidecar" }
if (-not $DistDir) { $DistDir = Join-Path $BaseDir "dist" }
if (-not $WorkDir) { $WorkDir = Join-Path $BaseDir "work" }
if (-not $VenvDir) { $VenvDir = Join-Path $BaseDir "venv" }

# 模板位置两种布局（同旧 build-win.ps1 / build.sh）：拆仓后的工具仓里模板就在仓内；
# 稿件仓里则在仓根的上一级。先看仓内，再看上一级。
$TplInRepo   = Join-Path $RepoRoot "编辑笔记\模板文件"
$TplInParent = Join-Path (Split-Path -Parent $RepoRoot) "编辑笔记\模板文件"
$TplSrc      = if (Test-Path $TplInRepo) { $TplInRepo } else { $TplInParent }

Write-Host "▶ 编辑部工具箱 · Windows 引擎 sidecar 打包" -ForegroundColor Cyan
Write-Host "  仓库根：$RepoRoot"

if (-not (Test-Path (Join-Path $EngineDir "engine_main.py"))) {
    throw "找不到 $EngineDir\engine_main.py —— 请把本脚本放在 Engine\tools\ 下"
}
if (-not (Test-Path $TplSrc)) {
    throw "找不到模板目录：${TplSrc}（引擎靠它生成初排稿，必须一起打包）"
}

# 版本号只有一处：Engine\slcn\__init__.py 的 __version__
$Version = (Select-String -Path (Join-Path $EngineDir "slcn\__init__.py") `
                          -Pattern '^__version__ = "(.*)"').Matches.Groups[1].Value
if (-not $Version) { $Version = "0.0.0" }
Write-Host "  版本：$Version"

New-Item -ItemType Directory -Force -Path $BaseDir, $DistDir, $WorkDir | Out-Null

# ── ① 构建用 venv（装 Windows 轮子 + PyInstaller）─────────────────────────
# B-300-01：这里**绝不裸调 `python`**——PATH 上的 python 可能是 3.10 或
# WindowsApps 的占位档。先找 py 启动器 / python3.14 并**真跑一次**
# 验证 >= 3.14；找不到就报错退出，不猜。
function Get-PyVersion([string]$ExePath, [string[]]$LauncherArgs) {
    try {
        $out = & $ExePath @LauncherArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    } catch { return "" }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return "" }
    return ([string]($out | Select-Object -Last 1)).Trim()
}

$PyExe = $null
$PyLaunch = @()
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) {
    $ver = Get-PyVersion $pyCmd.Source @("-3.14")
    if ($ver -match '^3\.(1[4-9]|[2-9][0-9])\.') { $PyExe = $pyCmd.Source; $PyLaunch = @("-3.14") }
}
if (-not $PyExe) {
    foreach ($cand in @("python3.14", "python")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $c) { continue }
        $ver = Get-PyVersion $c.Source @()
        if ($ver -match '^3\.(1[4-9]|[2-9][0-9])\.') { $PyExe = $c.Source; $PyLaunch = @(); break }
    }
}
if (-not $PyExe) {
    throw "找不到 Python 3.14（需要 3.14 及以上；装好后重跑，或用 py -3.14 验证）"
}
Write-Host "  构建解释器：$PyExe $($PyLaunch -join ' ')" -ForegroundColor DarkGray

# 旧 venv 可能是别的版本建的（B-300-01 现场就是 3.10 venv）——版本不对就删掉重建。
$needVenv = $true
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path $VenvPy) {
    $venvVer = Get-PyVersion $VenvPy @()
    if ($venvVer -match '^3\.(1[4-9]|[2-9][0-9])\.') { $needVenv = $false }
    else {
        Write-Host "▶ 旧 venv 是 Python $venvVer，不是 3.14+：删掉重建…" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir
    }
}
if ($needVenv) {
    Write-Host "▶ 建构建用 venv…" -ForegroundColor Cyan
    & $PyExe @PyLaunch -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "建 venv 失败（解释器：$PyExe，rc=$LASTEXITCODE）" }
}
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
Write-Host "▶ 装依赖（Windows 轮子）…" -ForegroundColor Cyan
& $VenvPip install --disable-pip-version-check --quiet --upgrade pip
& $VenvPip install --disable-pip-version-check --quiet lxml pypdf pypinyin pyinstaller

# ── ② exe 版本资源（B-300-04：属性里不能是空的）──────────────────────────
# 版本号仍只来自 Engine\slcn\__init__.py；PyInstaller 用 --version-file 注入。
# 企业/作者信息用中性占位值（公开仓不放真实个人信息）。
if ($Version -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "版本号格式不是 X.Y.Z：$Version（Engine\slcn\__init__.py）"
}
$V1 = [int]$Matches[1]; $V2 = [int]$Matches[2]; $V3 = [int]$Matches[3]
$FileVer = "$V1, $V2, $V3, 0"
$VersionFileEngine = Join-Path $WorkDir "version-slcengine.txt"

function New-VersionFile([string]$Path, [string]$InternalName, [string]$Description, [string]$OriginalFilename) {
    $body = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($FileVer),
    prodvers=($FileVer),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'SLCN Toolbox'),
        StringStruct('FileDescription', '$Description'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', '$InternalName'),
        StringStruct('OriginalFilename', '$OriginalFilename'),
        StringStruct('ProductName', '编辑部工具箱'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    [IO.File]::WriteAllText($Path, $body, (New-Object System.Text.UTF8Encoding($false)))
}
New-VersionFile $VersionFileEngine "SLCNEngine" "SLCNEngine（编辑部工具箱引擎）" "SLCNEngine.exe"

# ── ③ 冻结引擎：十三个 CLI 入口 → 一个 exe ──────────────────────────────
# ⚠️ 这张表必须与 `Engine/engine_main.py: ENTRIES` **逐项一致**（配对守卫在
#    Engine/tests/test_platform.py 的 (g) 组：逐项比对本表的 $Entries）。
#    少一项的后果不是"少个功能"，而是那个页面在用户机器上直接「❌ 不认识的入口」退出 2 ——
#    2026-10-17 v1.0.1 实测：表停在"三个入口"，9 个标签页坏了 5 个
#    （下面 hidden-import 与 ④ 自检都按它走）。
$Entries = @("convert.py", "checkbib.py", "mergepdf.py",
             "cleantemp.py", "compileall.py", "proofcheck.py",
             "infocheck.py", "toc.py", "publish.py", "bibdb.py",
             "envcheck.py", "diagnose.py", "dirpush.py")

Write-Host "▶ 冻结引擎 → SLCNEngine.exe…" -ForegroundColor Cyan
Push-Location $EngineDir
try {
    $PyArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
                "--name", "SLCNEngine",
                "--distpath", $DistDir, "--workpath", $WorkDir,
                "--specpath", $WorkDir)
    foreach ($e in $Entries) {
        # hidden-import 要用**模块名**（去掉 .py）：dispatcher 是动态 import，
        # PyInstaller 静态分析看不到它们，漏一个那个入口在 exe 里就是 ModuleNotFoundError。
        $PyArgs += @("--hidden-import", [IO.Path]::GetFileNameWithoutExtension($e))
    }
    $PyArgs += @("--add-data", "$TplSrc;编辑笔记\模板文件", "engine_main.py")
    $PyArgs += @("--version-file", $VersionFileEngine)
    & $VenvPy @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 冻结引擎失败（rc=$LASTEXITCODE）" }
} finally { Pop-Location }

# ── ④ 自检：**逐入口**真跑，不是只问一句版本号 ──────────────────────────
Write-Host "▶ 自检…" -ForegroundColor Cyan
$engineExe = Join-Path $DistDir "SLCNEngine.exe"
if (-not (Test-Path $engineExe)) { throw "没打出 SLCNEngine.exe" }
$ver = & $engineExe --version
Write-Host "  $ver"
if ($ver -notmatch [regex]::Escape($Version)) {
    throw "引擎 exe 报的版本与源码不一致（源码 ${Version}，exe：${ver}）"
}

# ⚠️ 每个入口都要**被这个 exe 真的认下来**：`--help` 会触发该模块的 import，
#    所以「ENTRIES 少一项」与「hidden-import 漏一个」都会当场红。
#    2026-10-17 的教训：v1.0.1 只查了 `--version`，于是 5 个入口在用户机器上才暴露。
foreach ($e in $Entries) {
    # 不用 `2>&1`：PS 5.1 在 `$ErrorActionPreference='Stop'` 下会把原生命令的 stderr 变成
    # 终止性错误（NativeCommandError）。「不认识的入口」走 stderr，但那种情况 rc=2 已经拦住了。
    $out = (& $engineExe $e --help | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "入口 $e 在冻结 exe 里不可用（rc=$LASTEXITCODE）：$($out.Trim())"
    }
    Write-Host "  ✓ $e" -ForegroundColor DarkGray
}

# 真跑一次最小转换 —— 这一步才是「包是完整的」的判据：它同时验证 dispatcher 转发、
# 模板打进去了（`编辑笔记\模板文件`）、lxml / pypinyin 在包里。只查 `--help` 查不出模板缺失。
$smoke = Join-Path $WorkDir "smoke"
New-Item -ItemType Directory -Force -Path $smoke | Out-Null
$tex = Join-Path $smoke "main.tex"
$texLines = @('\documentclass{article}', '\title{冒烟测试}', '\author{张三}',
              '\begin{document}', '\maketitle', '正文。', '\end{document}')
# 显式写**不带 BOM** 的 UTF-8（PS 5.1 的 `-Encoding UTF8` 会带 BOM，引擎会读到 \ufeff）
[IO.File]::WriteAllText($tex, ($texLines -join "`n"), (New-Object System.Text.UTF8Encoding($false)))
& $engineExe convert.py --source $tex --out (Join-Path $smoke "out") --author "张三" --no-original | Out-Host
if ($LASTEXITCODE -ne 0) { throw "convert.py 冒烟转换失败（rc=$LASTEXITCODE）" }
$made = @(Get-ChildItem -Path (Join-Path $smoke "out") -Recurse -Filter "*.tex" -ErrorAction SilentlyContinue)
if ($made.Count -eq 0) { throw "冒烟转换没有产出 .tex（模板没打进包，或 pypinyin 缺失）" }
Write-Host "  ✓ convert.py 冒烟：$($made[0].Name)（模板与依赖都在包里）" -ForegroundColor DarkGray

Write-Host ""
Write-Host "✅ 引擎 sidecar 打包完成（版本 ${Version}）" -ForegroundColor Green
Write-Host "   $engineExe"
