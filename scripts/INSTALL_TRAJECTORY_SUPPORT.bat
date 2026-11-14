@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv"
if not exist "%ENV_DIR%\python.exe" (
  echo ATCO runtime missing. Run INSTALL_RUNTIME.bat first.
  goto :fail
)
set "CONDA_BAT="
if defined ATCO_CONDA_BAT if exist "%ATCO_CONDA_BAT%" set "CONDA_BAT=%ATCO_CONDA_BAT%"
if not defined CONDA_BAT (
  for %%P in (
    "C:\Tools\Miniconda3\condabin\conda.bat"
    "C:\Tools\Miniforge3\condabin\conda.bat"
    "%USERPROFILE%\miniforge3\condabin\conda.bat"
    "%USERPROFILE%\miniconda3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
    "C:\ProgramData\miniconda3\condabin\conda.bat"
  ) do if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~P"
)
if not defined CONDA_BAT (
  for %%E in (conda.bat conda.exe) do (
    if not defined CONDA_BAT for /f "delims=" %%P in ('where %%E 2^>nul') do (
      if not defined CONDA_BAT set "CONDA_BAT=%%P"
    )
  )
)
if not defined CONDA_BAT (
  echo Conda not detected. Set ATCO_CONDA_BAT to the full path of conda.bat.
  goto :fail
)
echo Installing optional PyArrow for TX flightline/trajectory attribution...
call "%CONDA_BAT%" install --yes --prefix "%ENV_DIR%" --override-channels --strict-channel-priority -c conda-forge pyarrow
if errorlevel 1 goto :fail
call "%~dp0clean_gis_path.bat" "%ENV_DIR%"
"%ENV_DIR%\python.exe" -c "import pyarrow; print(pyarrow.__version__)"
if errorlevel 1 goto :fail
echo.
echo TX trajectory attribution support is ready.
pause
exit /b 0
:fail
echo.
echo ERROR: optional PyArrow setup failed.
pause
exit /b 1
