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
echo Installing optional PyArrow via ATCO standalone Micromamba...
"%ENV_DIR%\python.exe" "%~dp0ensure_trajectory_support.py"
if errorlevel 1 (
  echo ERROR: optional PyArrow setup failed.
  exit /b 1
)
"%ENV_DIR%\python.exe" -c "import pyarrow; print(pyarrow.__version__)"
if errorlevel 1 exit /b 1
echo Optional PyArrow support is ready.
exit /b 0
