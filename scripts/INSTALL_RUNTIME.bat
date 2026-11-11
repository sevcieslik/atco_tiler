@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "config" mkdir "config"

rem Remove a local runtime left incomplete by a cancelled Conda solve/install.
if exist ".venv" if not exist ".venv\python.exe" (
  echo Removing incomplete local ATCO runtime...
  rmdir /s /q ".venv"
)

set "ENV_DIR="
set "ENV_EXISTS=0"
if exist "D:\ATCO\polygons\01\.venv\python.exe" (
  set "ENV_DIR=D:\ATCO\polygons\01\.venv"
  set "ENV_EXISTS=1"
) else if exist ".venv\python.exe" (
  set "ENV_DIR=%CD%\.venv"
  set "ENV_EXISTS=1"
) else (
  set "ENV_DIR=%CD%\.venv"
)

set "CONDA_BAT="
where conda.bat >nul 2>&1
if not errorlevel 1 set "CONDA_BAT=conda.bat"
if not defined CONDA_BAT (
  for %%P in ("%USERPROFILE%\miniforge3\condabin\conda.bat" "%USERPROFILE%\miniconda3\condabin\conda.bat" "%LOCALAPPDATA%\miniforge3\condabin\conda.bat" "%LOCALAPPDATA%\miniconda3\condabin\conda.bat" "C:\ProgramData\miniforge3\condabin\conda.bat" "C:\ProgramData\miniconda3\condabin\conda.bat") do (
    if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~P"
  )
)

if "%ENV_EXISTS%"=="1" goto :validate_existing

if not defined CONDA_BAT (
  echo ERROR: No existing ATCO GIS environment and Conda/Miniforge was not found.
  echo Install Miniforge or restore D:\ATCO\polygons\01\.venv.
  pause
  exit /b 1
)

echo Creating ATCO runtime in %ENV_DIR% ...
call "%CONDA_BAT%" create --yes --solver=libmamba --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas shapely pyproj rasterio numpy pandas pyarrow pdal libpdal
if errorlevel 1 (
  echo libmamba solver unavailable or failed; retrying with the default solver...
  call "%CONDA_BAT%" create --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas shapely pyproj rasterio numpy pandas pyarrow pdal libpdal
)
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
if not defined CONDA_BAT (
  echo.
  echo Existing ATCO runtime needs ATCO dependencies, but Conda/Miniforge was not found.
  echo Install Miniforge, then run this installer again.
  goto :fail
)
echo.
echo Repairing existing ATCO runtime ...
call "%CONDA_BAT%" install --yes --solver=libmamba --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas shapely pyproj rasterio numpy pandas pyarrow pdal libpdal
if errorlevel 1 (
  echo libmamba solver unavailable or failed; retrying with the default solver...
  call "%CONDA_BAT%" install --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge python=3.11 geopandas shapely pyproj rasterio numpy pandas pyarrow pdal libpdal
)
if errorlevel 1 goto :fail

:validate_final
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
if errorlevel 1 goto :fail
"%ENV_DIR%\python.exe" "%~dp0bootstrap.py"
if errorlevel 1 goto :fail

:ready
echo.
echo ATCO runtime is ready.
echo EzLAS expected at: %~dp0tools\EzLAS\EzLAS.exe
if not exist "%~dp0tools\EzLAS\EzLAS.exe" echo NOTE: copy EzLAS.exe there before zLAS modes.
echo.
pause
exit /b 0

:fail
echo.
echo ERROR: ATCO runtime setup/validation failed.
pause
exit /b 1
