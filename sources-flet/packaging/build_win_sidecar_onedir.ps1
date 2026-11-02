# build_win_sidecar_onedir.ps1 —— Windows 引擎 sidecar（PyInstaller --onedir）
#
# 批113 起 sidecar **目录化**：产出 `SLCNEngine\SLCNEngine.exe` + `SLCNEngine\_internal\`。
# 与 `Engine/tools/build_win_sidecar.ps1`（仍产 onefile，供手工/历史用法）保持同一张
# 入口表、同一份模板数据、同一套版本资源；CI（.github/workflows/release.yml）用本脚本。
#
# 用法（CI 不传参数，默认值即可）：
#   pwsh -NoProfile -ExecutionPolicy Bypass -File sources-flet/packaging/build_win_sidecar_onedir.ps1
#
# 参数：
#   -Python   构建解释器（默认 python；CI 上已由 setup-python 装好依赖）
#   -DistDir  产出目录（默认 <仓根>\build-flet\dist-engine）
#   -WorkDir  工作目录（默认 <仓根>\build-flet\work-engine）
#   -SpecDir  spec 目录（默认 <仓根>\build-flet\spec-engine）
#
# ⚠️ 入口表必须与 `Engine/engine_main.py: ENTRIES` **逐项一致**（配对守卫在
#    Engine/tests/test_platform.py 的 (g) 组）。少一项的后果不是「少个功能」，
#    而是那个页面在用户机器上直接「❌ 不认识的入口」退出 2。

param(
    [string]$Python = "python",
    [string]$DistDir = "",
    [string]$WorkDir = "",
    [string]$SpecDir = ""
)

$ErrorActionPreference = "Stop"
$ToolDir   = Split-Path -Parent $MyInvocation.MyCommand.Path   # sources-flet\packaging
$RepoRoot  = Split-Path -Parent (Split-Path -Parent $ToolDir)  # 仓根
$EngineDir = Join-Path $RepoRoot "Engine"

if (-not $DistDir) { $DistDir = Join-Path $RepoRoot "build-flet\dist-engine" }
if (-not $WorkDir) { $WorkDir = Join-Path $RepoRoot "build-flet\work-engine" }
if (-not $SpecDir) { $SpecDir = Join-Path $RepoRoot "build-flet\spec-engine" }

# 模板位置两种布局（同 Engine\tools\build_win_sidecar.ps1）：先看仓内，再看上一级。
$TplInRepo   = Join-Path $RepoRoot "编辑笔记\模板文件"
$TplInParent = Join-Path (Split-Path -Parent $RepoRoot) "编辑笔记\模板文件"
$TplSrc      = if (Test-Path $TplInRepo) { $TplInRepo } else { $TplInParent }

if (-not (Test-Path (Join-Path $EngineDir "engine_main.py"))) {
    throw "找不到 $EngineDir\engine_main.py"
}
if (-not (Test-Path $TplSrc)) {
    throw "找不到模板目录：${TplSrc}（引擎靠它生成初排稿，必须一起打包）"
}

# 版本号只有一处：Engine\slcn\__init__.py 的 __version__
$Version = (Select-String -Path (Join-Path $EngineDir "slcn\__init__.py") `
                          -Pattern '^__version__ = "(.*)"').Matches.Groups[1].Value
if (-not $Version) { $Version = "0.0.0" }
if ($Version -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "版本号格式不是 X.Y.Z：$Version（Engine\slcn\__init__.py）"
}
$V1 = [int]$Matches[1]; $V2 = [int]$Matches[2]; $V3 = [int]$Matches[3]
$FileVer = "$V1, $V2, $V3, 0"

New-Item -ItemType Directory -Force -Path $DistDir, $WorkDir, $SpecDir | Out-Null

# ── exe 版本资源（B-300-04：属性里不能是空的；格式同 Engine\tools\build_win_sidecar.ps1）
$VersionFile = Join-Path $WorkDir "version-slcengine.txt"
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
        StringStruct('FileDescription', 'SLCNEngine（编辑部工具箱引擎）'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'SLCNEngine'),
        StringStruct('OriginalFilename', 'SLCNEngine.exe'),
        StringStruct('ProductName', '编辑部工具箱'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
[IO.File]::WriteAllText($VersionFile, $body, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "▶ 冻结引擎（--onedir）→ $DistDir" -ForegroundColor Cyan
Push-Location $EngineDir
try {
    $PyArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
                "--name", "SLCNEngine",
                "--distpath", $DistDir, "--workpath", $WorkDir, "--specpath", $SpecDir,
                "--version-file", $VersionFile)
    foreach ($e in @("convert.py", "checkbib.py", "mergepdf.py",
                     "cleantemp.py", "compileall.py", "proofcheck.py",
                     "infocheck.py", "toc.py", "publish.py", "bibdb.py",
                     "envcheck.py", "diagnose.py", "dirpush.py")) {
        $PyArgs += @("--hidden-import", [IO.Path]::GetFileNameWithoutExtension($e))
    }
    $PyArgs += @("--add-data", "$TplSrc;编辑笔记\模板文件", "engine_main.py")
    & $Python @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 冻结引擎失败（rc=$LASTEXITCODE）" }
} finally {
    Pop-Location
}

$OutExe      = Join-Path $DistDir "SLCNEngine\SLCNEngine.exe"
$OutInternal = Join-Path $DistDir "SLCNEngine\_internal"
if (-not (Test-Path $OutExe))      { throw "onedir 产物缺可执行文件：$OutExe" }
if (-not (Test-Path $OutInternal)) { throw "onedir 产物缺 _internal：$OutInternal" }

& $OutExe --version | Out-Host
if ($LASTEXITCODE -ne 0) { throw "sidecar 自检失败（SLCNEngine.exe --version，rc=$LASTEXITCODE）" }
Write-Host "✅ onedir sidecar：$OutExe" -ForegroundColor Green
