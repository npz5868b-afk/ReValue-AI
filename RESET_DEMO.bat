@echo off
setlocal
set "REVALUE_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REVALUE_ROOT%scripts\revalue-local.ps1" Reset
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Reset workflow finished. Press any key to close.
) else (
  echo Reset workflow needs attention. Press any key to close.
)
pause >nul
exit /b %EXIT_CODE%
