param(
    [Parameter(Mandatory = $true)]
    [string]$WslGatewayAddress,
    [Parameter(Mandatory = $true)]
    [string]$WslAddress,
    [ValidateRange(1, 65535)]
    [int]$ListenPort = 9223,
    [ValidateRange(1, 65535)]
    [int]$ChromePort = 9222
)

$Principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请在管理员 PowerShell 窗口中运行此脚本。"
}

$RuleName = "WSLJobChromeCDP"

netsh interface portproxy delete v4tov4 `
    listenaddress=$WslGatewayAddress `
    listenport=$ListenPort 2>$null

netsh interface portproxy add v4tov4 `
    listenaddress=$WslGatewayAddress `
    listenport=$ListenPort `
    connectaddress=127.0.0.1 `
    connectport=$ChromePort

Remove-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -Name $RuleName `
    -DisplayName "WSL 到求职 Chrome CDP" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $WslGatewayAddress `
    -LocalPort $ListenPort `
    -RemoteAddress $WslAddress `
    -Profile Any | Out-Null

Write-Host "Portproxy: ${WslGatewayAddress}:$ListenPort -> 127.0.0.1:$ChromePort"
Write-Host "防火墙远端地址： $WslAddress"

