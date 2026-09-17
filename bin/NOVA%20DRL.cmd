@echo off
setlocal
title NOVA DRL

set "NOVA_SERVER=192.168.86.32"
set "NOVA_USER=drladmin"
set "NOVA_KEY=%USERPROFILE%\.ssh\nova_drl_ed25519"
set "NOVA_REPORTS=\\192.168.86.25\Public\NOVA_REPORTS"
set "SSH_EXE=%WINDIR%\System32\OpenSSH\ssh.exe"

if not exist "%NOVA_KEY%" (
    echo.
    echo NOVA DRL SSH key not found:
    echo   %NOVA_KEY%
    echo.
    echo Run Setup-NOVA-SSH-Key.cmd once first.
    echo.
    pause
    exit /b 1
)

if not exist "%NOVA_REPORTS%\" (
    echo.
    echo WARNING: NOVA_REPORTS is not currently reachable:
    echo   %NOVA_REPORTS%
    echo.
    echo NOVA will still start, but automatic Word opening will be unavailable.
    echo.
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='SilentlyContinue';" ^
 "$reports=$env:NOVA_REPORTS;" ^
 "$started=Get-Date;" ^
 "$watcher=Start-Job -ArgumentList $reports,$started -ScriptBlock {" ^
 " param($dir,$start);" ^
 " $opened=@{};" ^
 " while($true) {" ^
 "   Get-ChildItem -LiteralPath $dir -Filter '*.docx' -File -ErrorAction SilentlyContinue ^|" ^
 "     Where-Object { $_.CreationTime -ge $start.AddSeconds(-2) } ^|" ^
 "     Sort-Object CreationTime ^|" ^
 "     ForEach-Object {" ^
 "       if(-not $opened.ContainsKey($_.FullName)) {" ^
 "         $opened[$_.FullName]=$true;" ^
 "         Start-Sleep -Milliseconds 500;" ^
 "         Start-Process -FilePath $_.FullName;" ^
 "       }" ^
 "     };" ^
 "   Start-Sleep -Milliseconds 750;" ^
 " }" ^
 "};" ^
 "try {" ^
 " & $env:SSH_EXE -i $env:NOVA_KEY -o BatchMode=yes -t ($env:NOVA_USER + '@' + $env:NOVA_SERVER) /usr/local/bin/nova-drl;" ^
 "} finally {" ^
 " Stop-Job $watcher -ErrorAction SilentlyContinue;" ^
 " Remove-Job $watcher -Force -ErrorAction SilentlyContinue;" ^
 "}"

echo.
echo NOVA DRL session ended.
pause
