@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "SCRIPTS=%~dp0scripts"
set "ENV_DIR="

if exist "%SCRIPTS%\config\runtime_env.txt" set /p ENV_DIR=<"%SCRIPTS%\config\runtime_env.txt"
if defined ENV_DIR if not exist "%ENV_DIR%\python.exe" set "ENV_DIR="

if not defined ENV_DIR (
  call "%SCRIPTS%\INSTALL_RUNTIME.bat"
  if errorlevel 1 exit /b 1
  if exist "%SCRIPTS%\config\runtime_env.txt" set /p ENV_DIR=<"%SCRIPTS%\config\runtime_env.txt"
)

if not exist "%ENV_DIR%\python.exe" (
  echo ERROR: Runtime Python not found: %ENV_DIR%
  pause
  exit /b 1
)

call "%SCRIPTS%\clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 (
  echo ERROR: Could not configure GIS runtime PATH.
  pause
  exit /b 1
)

"%ENV_DIR%\python.exe" "%SCRIPTS%\bootstrap.py"
if errorlevel 1 (
  echo.
  echo ATCO Retiler runtime/source validation failed.
  pause
  exit /b 1
)

rem Keep Python attached to this console during UAT so GUI startup errors
rem are visible rather than silently swallowed by pythonw.exe.
"%ENV_DIR%\python.exe" "%SCRIPTS%\atco_retiler_gui.py"
if errorlevel 1 (
  echo.
  echo ERROR: ATCO Retiler GUI exited with an error. See traceback above.
  pause
  exit /b 1
)
exit /b 0
