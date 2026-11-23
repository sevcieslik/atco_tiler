@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv"
if not exist "%ENV_DIR%\python.exe" (
  echo ERROR: ATCO runtime missing. Run INSTALL_RUNTIME.bat first.
  exit /b 1
)
if not exist "%~dp0ensure_trajectory_support.py" (
  echo ERROR: Missing scripts\ensure_trajectory_support.py. Check the extracted package.
  exit /b 1
)
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 exit /b 1
echo Checking optional PyArrow via local ATCO Micromamba...
"%ENV_DIR%\python.exe" "%~dp0ensure_trajectory_support.py"
if errorlevel 1 (
  echo ERROR: PyArrow setup failed. Check scripts\logs\install_pyarrow_*.log
  exit /b 1
)
echo Optional trajectory support check finished.
exit /b 0
