param(
    [string]$Server = "192.168.86.32",
    [string]$User = "drladmin",
    [string]$SharedReportDirectory = "Z:\NOVA DRL Reports"
)
& (Join-Path $PSScriptRoot "Install-NOVA-DRL-Engineer-Client.ps1") -Server $Server -User $User -SharedReportDirectory $SharedReportDirectory
exit $LASTEXITCODE
