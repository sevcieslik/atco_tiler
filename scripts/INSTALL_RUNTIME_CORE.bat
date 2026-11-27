@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "config" mkdir "config"

rem ATCO owns scripts\.venv. Do not depend on older, unrelated projects.
set "ENV_DIR=%CD%\.venv"
set "ENV_EXISTS=0"
if exist "%ENV_DIR%\python.exe" (
  set "ENV_EXISTS=1"
) else if exist "%ENV_DIR%" (
  echo Removing incomplete local ATCO runtime...
  rmdir /s /q "%ENV_DIR%"
  if exist "%ENV_DIR%" goto :fail
)

rem A valid existing runtime never triggers a Conda download.
if "%ENV_EXISTS%"=="1" goto :validate_existing
call :ensure_conda
if errorlevel 1 goto :fail

if "%ENV_EXISTS%"=="1" goto :validate_existing
echo.
echo ATCO runtime: %ENV_DIR%
echo Conda executable: %CONDA_BAT%
echo Creating lightweight ATCO imagery runtime (Python 3.11). LiDAR is optional.
call "%CONDA_BAT%" create --solver=libmamba --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
if errorlevel 1 goto :fail
>"config\runtime_env.txt" echo %ENV_DIR%
goto :validate_final

:validate_existing
rem Recheck the environment and repair it in-place if dependencies are missing.
>"config\runtime_env.txt" echo %ENV_DIR%
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :repair
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if not errorlevel 1 goto :ready

:repair
call :ensure_conda
if errorlevel 1 goto :fail
echo.
echo Repairing ATCO runtime at %ENV_DIR% ...
echo Using Conda: %CONDA_BAT%
call "%CONDA_BAT%" install --solver=libmamba --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
if errorlevel 1 goto :fail

:validate_final
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :fail
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if errorlevel 1 goto :fail

:ready
echo.
echo ATCO runtime is ready.
echo LAZ/zLAS PDAL support is installed on demand via Conda when selected.
echo EzLAS expected at: %~dp0tools\EzLAS\EzLAS.exe
if not exist "%~dp0tools\EzLAS\EzLAS.exe" echo NOTE: copy EzLAS.exe there before zLAS modes.
echo.
rem No pause: log wrapper records exit status and can be launched unattended.
exit /b 0

:ensure_conda
rem Explicit administrator-approved Conda override is supported.
if defined ATCO_CONDA_BAT (
  if not exist "%ATCO_CONDA_BAT%" (
    echo ERROR: ATCO_CONDA_BAT does not exist: %ATCO_CONDA_BAT%
    exit /b 1
  )
  set "CONDA_BAT=%ATCO_CONDA_BAT%"
  echo Using explicitly selected Conda: %CONDA_BAT%
  exit /b 0
)
rem First-run bootstrap uses pinned Miniforge in the user profile, not system Conda.
powershell.exe -NoProfile -File "%~dp0bootstrap_miniforge.ps1"
if errorlevel 1 (
  echo ERROR: ATCO-managed Miniforge setup failed. Existing runtime preserved.
  exit /b 1
)
if not exist "config\conda_path.txt" exit /b 1
set /p "CONDA_BAT="<"config\conda_path.txt"
if not exist "%CONDA_BAT%" (
  echo ERROR: ATCO-managed Conda executable missing: %CONDA_BAT%
  exit /b 1
)
exit /b 0

:fail
echo.
echo ERROR: ATCO runtime setup/validation failed.
rem No pause: log wrapper records exit status and can be launched unattended.
exit /b 1
