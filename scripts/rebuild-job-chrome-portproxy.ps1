param(
    [Parameter(Mandatory = $true)]
    [string]$WslHostIp,
    [Parameter(Mandatory = $true)]
    [string]$WslIp,
    [int]$ListenPort = 9223,
    [int]$ChromePort = 9222
)

$ErrorActionPreference = "Stop"

netsh interface portproxy delete v4tov4 `
    listenaddress=$WslHostIp `
    listenport=$ListenPort 2>$null

netsh interface portproxy add v4tov4 `
    listenaddress=$WslHostIp `
    listenport=$ListenPort `
    connectaddress=127.0.0.1 `
    connectport=$ChromePort

Remove-NetFirewallRule `
    -Name "WSLJobChromeCDP" `
    -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -Name "WSLJobChromeCDP" `
    -DisplayName "WSL to Job Chrome CDP" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $WslHostIp `
    -LocalPort $ListenPort `
    -RemoteAddress $WslIp `
    -Profile Any | Out-Null

Write-Output "已重建端口转发：$WslHostIp`:$ListenPort -> 127.0.0.1`:$ChromePort（WSL $WslIp）"
