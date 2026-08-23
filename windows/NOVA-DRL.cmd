@echo off
setlocal
TITLE NOVA DRL
set "NOVA_SERVER=192.168.86.32"
set "NOVA_USER=drladmin"
set "SSH_EXE=%WINDIR%\System32\OpenSSH\ssh.exe"

if not exist "%SSH_EXE%" (
  echo Windows OpenSSH client was not found:
  echo   %SSH_EXE%
  echo.
  echo Install the Windows OpenSSH Client, then run NOVA DRL again.
  pause
  exit /b 2
)

echo Connecting to NOVA DRL at %NOVA_SERVER% ...
echo.
"%SSH_EXE%" -t %NOVA_USER%@%NOVA_SERVER% /usr/local/bin/nova-drl

echo.
echo NOVA DRL session ended.
pause
