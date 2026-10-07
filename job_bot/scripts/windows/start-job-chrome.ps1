param(
    [ValidateRange(1, 65535)]
    [int]$Port = 9222,
    [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "JobApplyChrome")
)

$Candidates = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

if (-not $Candidates) {
    throw "在 Program Files 或 Program Files (x86) 下未找到 Google Chrome。"
}

$Chrome = @($Candidates)[0]
$Arguments = @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=`"$UserDataDir`"",
    "--no-first-run"
)

Write-Host "Chrome 可执行文件： $Chrome"
Write-Host "专用档案： $UserDataDir"
Write-Host "Windows 本机 CDP 端点： http://127.0.0.1:$Port"
Write-Host "此脚本不会停止或替换任何已运行的 Chrome 进程。"

Start-Process -FilePath $Chrome -ArgumentList $Arguments

# 返回 WSL 前检查启动状态；Start-Process 命令本身不能证明已就绪。
$Deadline = (Get-Date).AddSeconds(10)
do {
    try {
        $Version = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
        if ($Version.Browser) {
            Write-Host "Chrome 已就绪： $($Version.Browser)"
            return
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
} while ((Get-Date) -lt $Deadline)
throw "Chrome 已启动，但本机 CDP 端点未能在端口 $Port 上就绪。"
