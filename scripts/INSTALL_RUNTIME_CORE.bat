@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "config" mkdir "config"

rem ATCO owns its environment and package manager; never invoke host Conda.
set "ENV_DIR=%CD%\.venv"
set "MAMBA_EXE=%CD%\.installer\micromamba.exe"
set "MAMBA_ROOT_PREFIX=%LOCALAPPDATA%\ATCO\micromamba"
set "MAMBA_NO_BANNER=1"
set "ENV_EXISTS=0"
if exist "%ENV_DIR%\python.exe" (
  set "ENV_EXISTS=1"
) else if exist "%ENV_DIR%" (
  echo Removing incomplete local ATCO runtime...
  rmdir /s /q "%ENV_DIR%"
  if exist "%ENV_DIR%" goto :fail
)

if "%ENV_EXISTS%"=="1" goto :validate_existing
call :ensure_mamba
if errorlevel 1 goto :fail

echo.
echo ATCO runtime: %ENV_DIR%
echo Package manager: %MAMBA_EXE%
echo Micromamba root: %MAMBA_ROOT_PREFIX%
echo Creating lightweight ATCO imagery runtime (Python 3.11). LiDAR is optional.
"%MAMBA_EXE%" create --yes --root-prefix "%MAMBA_ROOT_PREFIX%" --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
if errorlevel 1 goto :fail
>"config\runtime_env.txt" echo %ENV_DIR%
goto :validate_final

:validate_existing
>"config\runtime_env.txt" echo %ENV_DIR%
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :repair
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if not errorlevel 1 goto :ready

:repair
call :ensure_mamba
if errorlevel 1 goto :fail
echo.
echo Repairing ATCO runtime at %ENV_DIR% using standalone Micromamba...
"%MAMBA_EXE%" install --yes --root-prefix "%MAMBA_ROOT_PREFIX%" --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
if errorlevel 1 goto :fail

:validate_final
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :fail
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if errorlevel 1 goto :fail

:ready
echo.
echo ATCO runtime is ready.
echo Optional PDAL/PyArrow installs also use ATCO Micromamba.
echo EzLAS expected at: %~dp0tools\EzLAS\EzLAS.exe
if not exist "%~dp0tools\EzLAS\EzLAS.exe" echo NOTE: copy EzLAS.exe there before zLAS modes.
exit /b 0

:ensure_mamba
if exist "%MAMBA_EXE%" (
  "%MAMBA_EXE%" --version
  if not errorlevel 1 exit /b 0
  echo ERROR: Existing ATCO Micromamba cannot execute: %MAMBA_EXE%
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_micromamba.ps1"
if errorlevel 1 exit /b 1
if not exist "%MAMBA_EXE%" exit /b 1
exit /b 0

:fail
echo.
echo ERROR: ATCO runtime setup/validation failed.
exit /b 1
