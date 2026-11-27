@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv"
if not exist "%ENV_DIR%\python.exe" (
  echo ERROR: ATCO runtime missing. Launch ATCO_RETILER.bat first.
  exit /b 1
)
if not exist "%~dp0ensure_lidar_support.py" (
  echo ERROR: Required installation module is missing: ensure_lidar_support.py
  exit /b 1
)
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 exit /b 1
echo Checking optional PDAL support using ATCO-managed Conda...
"%ENV_DIR%\python.exe" "%~dp0ensure_lidar_support.py"
if errorlevel 1 (
  echo ERROR: Optional PDAL setup failed.
  exit /b 1
)
echo Optional PDAL support check completed.
exit /b 0
