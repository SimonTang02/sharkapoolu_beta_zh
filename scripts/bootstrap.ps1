# Windows 原生入口；不会安装 Python、Git 或系统级依赖。
param([switch]$WithBrowser, [switch]$WithResume, [switch]$SkipTests, [string]$Python = "python")
$ErrorActionPreference = "Stop"
$ProjectDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $ProjectDirectory
try {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        throw "请先安装 Python 3.10+，重新打开 PowerShell，然后再次运行 bootstrap.ps1。"
    }
    & $Python -c 'import sys; assert sys.version_info >= (3, 10), "需要 Python 3.10+"'
    if ($LASTEXITCODE -ne 0) { throw "Python 版本检查失败" }
    $EnvironmentPython = Join-Path $ProjectDirectory ".venv\Scripts\python.exe"
    if (-not (Test-Path $EnvironmentPython)) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
    }
    $InstallTarget = "."
    if ($WithBrowser -and $WithResume) { $InstallTarget = ".[browser,resume]" }
    elseif ($WithBrowser) { $InstallTarget = ".[browser]" }
    elseif ($WithResume) { $InstallTarget = ".[resume]" }
    & $EnvironmentPython -m pip install -e $InstallTarget
    if ($LASTEXITCODE -ne 0) { throw "安装软件包失败" }
    if ($WithBrowser) {
        # 保留 Playwright 标准缓存目录，以便后续命令能够找到浏览器。
        & $EnvironmentPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "安装 Chromium 失败" }
    }
    & $EnvironmentPython -m job_bot.private_config init
    if ($LASTEXITCODE -ne 0) { throw "初始化私有目录失败" }
    & $EnvironmentPython -m job_bot.private_config check
    if ($LASTEXITCODE -ne 0) { throw "私有目录校验失败" }
    & $EnvironmentPython -m job_bot.config_inspect --config job_bot/config.china_hk_ic_foreign.json
    if ($LASTEXITCODE -ne 0) { throw "运行时配置校验失败" }
    if (-not $SkipTests) {
        # SSH RPC 测试需要 Unix 管道；此处仅运行 Windows 原生核心契约测试套件。
        & $EnvironmentPython -m unittest cv.test_resume_import job_bot.test_manual_database job_bot.test_private_config cv.bot.test_bot application_bot.test_manual_kit
        if ($LASTEXITCODE -ne 0) { throw "测试失败；实际使用前请检查结果" }
    }
    Write-Host "初始化完成。请使用 .venv\Scripts\python.exe，或激活 .venv\Scripts\Activate.ps1。"
    Write-Host "Windows 核心环境已准备就绪；实际门户和 TeX 兼容性仍需验证。"
} finally { Pop-Location }
