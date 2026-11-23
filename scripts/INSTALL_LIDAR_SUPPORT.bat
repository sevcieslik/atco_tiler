@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv"
if not exist "%ENV_DIR%\python.exe" (
  echo ERROR: ATCO runtime missing. Run INSTALL_RUNTIME.bat first.
  exit /b 1
)
if not exist "%~dp0ensure_lidar_support.py" (
  echo ERROR: Missing scripts\ensure_lidar_support.py. Check the extracted package.
  exit /b 1
)
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 exit /b 1
echo Installing optional PDAL via local ATCO Micromamba...
"%ENV_DIR%\python.exe" "%~dp0ensure_lidar_support.py"
if errorlevel 1 (
  echo ERROR: PDAL setup failed. Check scripts\logs\install_pdal_*.log
  exit /b 1
)
if not exist "%ENV_DIR%\Library\bin\pdal.exe" (
  echo ERROR: PDAL executable missing from local runtime.
  exit /b 1
)
"%ENV_DIR%\Library\bin\pdal.exe" --version
if errorlevel 1 exit /b 1
echo Optional PDAL support is ready.
exit /b 0
