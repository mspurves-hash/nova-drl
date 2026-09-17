@echo off
setlocal
title NOVA DRL - One-Time SSH Key Setup

set "KEY=%USERPROFILE%\.ssh\nova_drl_ed25519"
set "PUB=%KEY%.pub"
set "SERVER=192.168.86.32"
set "USER=drladmin"

echo.
echo NOVA DRL - One-Time Passwordless SSH Setup
echo ============================================
echo.

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"

if not exist "%KEY%" (
    echo Creating a dedicated NOVA DRL SSH key...
    ssh-keygen -t ed25519 -f "%KEY%" -N "" -C "NOVA-DRL-%COMPUTERNAME%"
    if errorlevel 1 goto :fail
) else (
    echo Existing NOVA DRL SSH key found:
    echo   %KEY%
)

echo.
echo The server password will be requested ONE LAST TIME
echo so this public key can be installed.
echo.

type "%PUB%" | ssh %USER%@%SERVER% "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; cat >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"
if errorlevel 1 goto :fail

echo.
echo Testing passwordless login...
ssh -i "%KEY%" -o BatchMode=yes %USER%@%SERVER% "echo NOVA DRL passwordless SSH: PASS"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo PASS - This PC can now open NOVA DRL without a password.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo SETUP FAILED. No NOVA server files were changed other than
echo the SSH authorized_keys entry attempted above.
echo.
pause
exit /b 1
