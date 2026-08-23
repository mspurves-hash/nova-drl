param(
    [string]$Server = "192.168.86.32",
    [string]$User = "drladmin"
)

$ErrorActionPreference = "Stop"
$ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
if (-not (Test-Path $ssh)) {
    throw "Windows OpenSSH client was not found at $ssh. Install OpenSSH Client first."
}

$installDir = Join-Path $env:LOCALAPPDATA "NOVA-DRL"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$launcher = Join-Path $installDir "NOVA-DRL.cmd"

$cmd = @"
@echo off
setlocal
TITLE NOVA DRL
set "NOVA_SERVER=$Server"
set "NOVA_USER=$User"
set "SSH_EXE=%WINDIR%\System32\OpenSSH\ssh.exe"
echo Connecting to NOVA DRL at %NOVA_SERVER% ...
echo.
"%SSH_EXE%" -t %NOVA_USER%@%NOVA_SERVER% /usr/local/bin/nova-drl
echo.
echo NOVA DRL session ended.
pause
"@
Set-Content -Path $launcher -Value $cmd -Encoding ASCII

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "NOVA DRL.lnk"
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = "Open NOVA DRL Unified Knowledge Search"
$shortcut.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
$shortcut.Save()

Write-Host "NOVA DRL desktop shortcut created:" -ForegroundColor Green
Write-Host "  $shortcutPath"
Write-Host "Server: $Server"
Write-Host "User:   $User"
Write-Host "No password is stored in the shortcut. SSH will prompt normally."
