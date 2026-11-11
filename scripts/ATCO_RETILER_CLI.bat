@echo off
setlocal
for /f "usebackq delims=" %%i in ("%~dp0config\runtime_env.txt") do set "ENV_DIR=%%i"
if not exist "%ENV_DIR%\python.exe" (
  echo Runtime not configured. Run ATCO_RETILER.bat first.
  pause
  exit /b 1
)
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
"%ENV_DIR%\python.exe" "%~dp0atco_processor.py"
pause
