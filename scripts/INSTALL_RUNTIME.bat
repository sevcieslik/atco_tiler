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

rem Discover Conda independently of the system Python version or PATH order.
rem A known custom installation can be specified as ATCO_CONDA_BAT.
set "CONDA_BAT="
if defined ATCO_CONDA_BAT if exist "%ATCO_CONDA_BAT%" set "CONDA_BAT=%ATCO_CONDA_BAT%"
if not defined CONDA_BAT (
  for %%P in (
    "C:\Tools\Miniconda3\condabin\conda.bat"
    "C:\Tools\Miniconda3\Scripts\conda.exe"
    "C:\Tools\Miniforge3\condabin\conda.bat"
    "C:\Tools\Miniforge3\Scripts\conda.exe"
    "%USERPROFILE%\miniforge3\condabin\conda.bat"
    "%USERPROFILE%\miniconda3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniforge3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
    "C:\ProgramData\miniforge3\condabin\conda.bat"
    "C:\ProgramData\miniconda3\condabin\conda.bat"
  ) do (
    if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~P"
  )
)
if not defined CONDA_BAT (
  for %%E in (conda.bat conda.exe) do (
    if not defined CONDA_BAT for /f "delims=" %%P in ('where %%E 2^>nul') do (
      if not defined CONDA_BAT set "CONDA_BAT=%%P"
    )
  )
)

if "%ENV_EXISTS%"=="1" goto :validate_existing
if not defined CONDA_BAT goto :missing_conda

echo.
echo ATCO runtime: %ENV_DIR%
echo Conda executable: %CONDA_BAT%
echo Creating lightweight ATCO imagery runtime (Python 3.11). LiDAR is optional.
call "%CONDA_BAT%" create --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
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
if not defined CONDA_BAT goto :missing_conda
echo.
echo Repairing ATCO runtime at %ENV_DIR% ...
echo Using Conda: %CONDA_BAT%
call "%CONDA_BAT%" install --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas rasterio
if errorlevel 1 goto :fail

:validate_final
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :fail
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if errorlevel 1 goto :fail

:ready
echo.
echo ATCO runtime is ready.
echo To enable LAZ/zLAS, run INSTALL_LIDAR_SUPPORT.bat first.
echo EzLAS expected at: %~dp0tools\EzLAS\EzLAS.exe
if not exist "%~dp0tools\EzLAS\EzLAS.exe" echo NOTE: copy EzLAS.exe there before zLAS modes.
echo.
pause
exit /b 0

:missing_conda
echo.
echo ERROR: ATCO needs Conda to create or repair its GIS runtime.
echo Looked for C:\Tools\Miniconda3 and other common Conda locations.
echo If Conda is elsewhere, set ATCO_CONDA_BAT to its full path.
goto :fail

:fail
echo.
echo ERROR: ATCO runtime setup/validation failed.
pause
exit /b 1
