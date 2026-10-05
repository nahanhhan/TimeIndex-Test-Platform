@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
set "result=%ERRORLEVEL%"
if not "%result%"=="0" (
    echo.
    echo Startup failed. See "%~dp0launch.log" for details.
    pause
)
exit /b %result%
