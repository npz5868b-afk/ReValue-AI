@echo off
setlocal
set "REVALUE_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REVALUE_ROOT%scripts\revalue-local.ps1" Stop
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Stop workflow finished. Press any key to close.
) else (
  echo Stop workflow needs attention. Press any key to close.
)
pause >nul
exit /b %EXIT_CODE%
