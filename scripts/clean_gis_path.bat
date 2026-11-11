@echo off
if "%~1"=="" exit /b 2
set "GIS_ENV=%~1"
if not exist "%GIS_ENV%\python.exe" exit /b 3
set "PATH=%GIS_ENV%;%GIS_ENV%\Library\mingw-w64\bin;%GIS_ENV%\Library\usr\bin;%GIS_ENV%\Library\bin;%GIS_ENV%\Scripts;%GIS_ENV%\bin;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "GDAL_DATA=%GIS_ENV%\Library\share\gdal"
set "PROJ_DATA=%GIS_ENV%\Library\share\proj"
set "PROJ_LIB=%PROJ_DATA%"
exit /b 0
