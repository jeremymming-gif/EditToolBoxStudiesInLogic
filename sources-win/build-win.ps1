# build-win.ps1 —— 在 Windows 上打出两个 exe（引擎 + 界面）
#
#   pwsh -ExecutionPolicy Bypass -File build-win.ps1
#
# 产出（都在 build\dist\ 下）：
#   SLCNEngine.exe      引擎（**八个** CLI 入口冻成一个；用户机器上不需要装 Python）
#   编辑部工具箱.exe     界面（Tkinter 外壳；它会在自己旁边找 SLCNEngine.exe）
#
# ⚠️ 本文件存为 **UTF-8 带 BOM**：Windows PowerShell 5.1 读不带 BOM 的 .ps1
#    会按 ANSI（简体中文机器上是 cp936）解码，下面的中文提示会变乱码。
#    （行尾交给 git/checkout 决定，别手动改。）
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

# ── ② 冻结引擎：八个 CLI 入口 → 一个 exe ────────────────────────────────
# ⚠️ 这张表必须与 `Engine/engine_main.py: ENTRIES` **逐项一致**（配对守卫在
#    tests/test_bridge.py 的 (b) 组）。少一项的后果不是"少个功能"，而是那个标签页
#    在用户机器上直接「❌ 不认识的入口」退出 2 —— 2026-10-17 v1.0.1 实测：
#    表停在"三个入口"，9 个标签页坏了 5 个（下面 hidden-import 与 ④ 自检都按它走）。
$Entries = @("convert.py", "checkbib.py", "mergepdf.py",
             "cleantemp.py", "publish.py", "compileall.py", "proofcheck.py",
             "infocheck.py", "toc.py")

Write-Host "▶ 冻结引擎 → SLCNEngine.exe…" -ForegroundColor Cyan
Push-Location $EngineDir
try {
    $PyArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
                "--name", "SLCNEngine",
                "--distpath", $DistDir, "--workpath", (Join-Path $BuildDir "work-engine"),
                "--specpath", $BuildDir)
    foreach ($e in $Entries) {
        # hidden-import 要用**模块名**（去掉 .py）：dispatcher 是动态 import，
        # PyInstaller 静态分析看不到它们，漏一个那个入口在 exe 里就是 ModuleNotFoundError。
        $PyArgs += @("--hidden-import", [IO.Path]::GetFileNameWithoutExtension($e))
    }
    $PyArgs += @("--add-data", "$TplSrc;编辑笔记\模板文件", "engine_main.py")
    & $VenvPy @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 冻结引擎失败（rc=$LASTEXITCODE）" }
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
$smoke = Join-Path $BuildDir "smoke"
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
Write-Host "✅ 打包完成（版本 ${Version}）" -ForegroundColor Green
Write-Host "   $DistDir\SLCNEngine.exe"
Write-Host "   $DistDir\编辑部工具箱.exe"
Write-Host ""
Write-Host "发布前请在**干净的 Windows** 上双击试一遍（最好是没有 Python 的机器）：" -ForegroundColor Yellow
Write-Host "   1. 双击「编辑部工具箱.exe」，设置页应显示「运行方式：冻结引擎 …」"
Write-Host "   2. 合并文件页选一期目录，跑一遍，看是否出「完成。合并文件：…」"
Write-Host "   3. 未签名 exe 会触发 SmartScreen：更多信息 → 仍要运行（已在文档里写明）"
