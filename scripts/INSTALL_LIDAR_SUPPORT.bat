@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv"
if not exist "%ENV_DIR%\python.exe" (
  echo ATCO runtime missing. Run INSTALL_RUNTIME.bat first.
  exit /b 1
)
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 exit /b 1
echo Installing optional PDAL via ATCO standalone Micromamba...
"%ENV_DIR%\python.exe" "%~dp0ensure_lidar_support.py"
if errorlevel 1 (
  echo ERROR: optional PDAL setup failed.
  exit /b 1
)
pdal --version
if errorlevel 1 exit /b 1
echo Optional PDAL support is ready.
exit /b 0
