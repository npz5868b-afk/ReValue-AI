@echo off
setlocal
set "REVALUE_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REVALUE_ROOT%scripts\revalue-local.ps1" Start
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo ReValue AI did not start. Review the message above, then press any key to close.
  pause >nul
  exit /b %EXIT_CODE%
)
echo.
echo ReValue AI is running locally. You can close this window; use STOP_REVALUE_AI.bat when finished.
pause
exit /b 0
