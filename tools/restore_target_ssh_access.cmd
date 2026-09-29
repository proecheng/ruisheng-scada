@echo off
setlocal
cd /d "%~dp0"
echo Run this on WIN-OAUCM8UQUGH using right-click / Run as administrator.
echo This restores the existing support public key and its file permissions.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore_target_ssh_access.ps1"
echo.
echo Keep this window open. RESULT.txt contains the result for the engineer.
pause
