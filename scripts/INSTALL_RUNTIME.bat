@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
rem Keep installation observable and persist its complete console output.
rem No Python is required before the base environment has been created.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_runtime_logger.ps1"
set "ATCO_SETUP_RC=%ERRORLEVEL%"
if not "%ATCO_SETUP_RC%"=="0" (
  echo ERROR: ATCO runtime setup failed. See scripts\logs\install_runtime_*.log
)
exit /b %ATCO_SETUP_RC%
