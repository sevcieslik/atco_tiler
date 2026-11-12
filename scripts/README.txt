ATCO Retiler v1.06
==================

Launch:
    ATCO_RETILER.bat

Input layout:
    input\aoi\
    input\orthos\
    input\point_cloud\

Delivery layout (revision remains metadata, not a folder):
    output\<ENTITY>\TIFF\
    output\<ENTITY>\ECW\
    output\<ENTITY>\LAZ\
    output\<ENTITY>\zLAS\
    output\<ENTITY>\<CLIENT ENTITY NAME>_2026.gdb
    output\<ENTITY>\QA\

Central segments
----------------
Published segment geometry on W:\APPS\PIT\ATCO\segments is not rewritten.
Existing internal BlockNum values such as 9L137_MU_0001 remain the approved
central identifiers. Retiler derives client delivery names at runtime:
    9L137_MU_0001                  -> 9L137_0001
    UTIKUMA_OILFIELD_MU_0001       -> UTIKUMA OILFIELD_0001
DX client-facing entity names follow the SoW SQLite convention (spaces).

v1.05
-----
- Removed MU from client product names. Tile sequence remains stable and is
  derived from the approved central BlockNum.
- Removed the AOI revision folder (for example R01) from the physical delivery
  output structure. Revision remains recorded in QA metadata.
- Tile Index is now the TILES feature class in the entity delivery GDB rather
  than a standalone SHP.
- TX TILES schema:
      BlockNum, ZONE, YEAR, LINE, NAME, fldate, Flightline,
      Shape_Length, Shape_Area
  OBJECTID and Shape are geodatabase system fields.
- DX TILES schema (join artefacts removed):
      PRJ_NAME, YEAR, ZONE
  OBJECTID and Shape are geodatabase system fields.
- TX fldate / Flightline are populated best-effort from final LAZ GPS time
  matched to W:\APPS\PIT\ATCO\source\trajectories\trajectory_index.parquet.
  Blank values are retained if a trajectory match cannot be established.
- TIFF NoData now follows the ArcGIS Pro test validated with Luke:
      background only when RGBI == 255,255,255,255
      GDAL UNIFIED_SRC_NODATA=YES
  TIFF remains four-band RGBI; band 4 is NIR, not alpha.
- Synthetic flags are no longer edited. ReturnNumber=0 / Synthetic consistency
  is QA-only and reported by classification in:
      QA\LiDAR\return0_flag_by_class.csv
  Return-0 non-Synthetic points are marked REVIEW, not automatically changed.
- GUI action order is now:
      Validate Setup | Run Build | Rerun Missing | Pause/Resume | Stop
- Stop terminates the active process tree and cleans temporary / zero-byte
  partial files while keeping completed products.
- Build Details no longer duplicates circuit/revision/output. It shows mode,
  selected products, segment/source counts, workers, stage and elapsed time.

Runtime
-------
INSTALL_RUNTIME.bat creates/repairs the conda runtime with GeoPandas, Rasterio,
PyArrow and PDAL. TIFF NoData is written directly by Rasterio; no separate
gdalwarp dependency is required. Pyogrio is optional (Fiona is used as fallback). EzLAS.exe remains an external
tool under scripts\tools\EzLAS\EzLAS.exe. Global Mapper is required for ECW.

CLI fallback:
    scripts\ATCO_RETILER_CLI.bat

Bugs, improvements and issues:
    pit@nmgroup.com


v1.06 runtime hotfix
- Removed the explicit GDAL/gdalwarp and Pyogrio dependencies that caused slow Conda solving.
- Uses the same lightweight runtime footprint as v1.04 and prefers the libmamba solver.
- Writes the validated 4-band 255,255,255,255 NoData rule directly with Rasterio.
- OpenFileGDB writing uses Pyogrio when available and Fiona as fallback.

v1.06 runtime detection hotfix (November 2026)
---------------------------------------------
INSTALL_RUNTIME.bat uses the project-local scripts\.venv and detects Conda
installations in non-standard locations including C:\Tools\Miniconda3.
An optional ATCO_CONDA_BAT environment variable may identify another conda
batch/executable. The removed D:\ATCO\polygons\01\.venv is no longer used.
The installer creates/repairs a Python 3.11 environment within the project;
it does not change the system Python 3.14 or Miniconda base Python 3.10.
The default Conda solver is used for compatibility with Conda 22.11.1.
If setup fails after Conda detection, check package download/solver errors,
then verify the project source GDB and network AOI database are accessible.
