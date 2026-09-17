$ErrorActionPreference = "Stop"

$Repo = 'D:\My Documents\GitHub\nova-drl'
$Client = Join-Path $Repo 'NOVA-DRL-Windows-Tech-v1.9.4.ps1'
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'NOVA DRL.lnk'
$OldShortcutPath = Join-Path $Desktop 'NOVA DRL v1.5.8 OLD.lnk'
$PowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $Client)) {
    throw "NOVA DRL Windows client was not found:`n$Client"
}

if (-not (Test-Path -LiteralPath $PowerShell)) {
    throw "Windows PowerShell was not found:`n$PowerShell"
}

# Preserve the existing shortcut once, if present.
if ((Test-Path -LiteralPath $ShortcutPath) -and -not (Test-Path -LiteralPath $OldShortcutPath)) {
    Copy-Item -LiteralPath $ShortcutPath -Destination $OldShortcutPath
    Write-Host "Preserved old shortcut as:" -ForegroundColor Yellow
    Write-Host "  $OldShortcutPath"
    Write-Host ""
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $Client + '"'
$Shortcut.WorkingDirectory = $Repo
$Shortcut.Description = 'NOVA DRL Windows Technician Client v1.9.4'
$Shortcut.IconLocation = "$PowerShell,0"
$Shortcut.WindowStyle = 1
$Shortcut.Save()

Write-Host ""
Write-Host "NOVA DRL shortcut created successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Desktop shortcut:"
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "PowerShell client:"
Write-Host "  $Client"
Write-Host ""
Write-Host "Shortcut target:"
Write-Host "  $PowerShell"
Write-Host ""
Write-Host "Arguments:"
Write-Host ('  -NoProfile -ExecutionPolicy Bypass -File "' + $Client + '"')
Write-Host ""
Write-Host "You can now double-click NOVA DRL on the Desktop."
