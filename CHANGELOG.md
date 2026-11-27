# ATCO Retiler – Release notes

## v1.08.04 (Windows UAT)

- TX/DX Tile Index now uses classified LAS PointSourceId for Flightline and DX flightid. Acquisition dates and DX DDDYY remain GPS-range based. No point attribute edits or new line-number QA.

- Windowed segmented TIFF mosaic and read-back QA (1024 × 1024 pixels); 128 MiB GDAL cache in the orthophoto CLI.
- DX Tile Index: ten agreed fields; integer DDDYY for earliest matched acquisition date; flightid matches Flightline; blank WONum.
- Shared TX/DX temporal attribution and optional PDAL/PyArrow setup for index-only runs. Spatial confirmation is not implemented.
- Fix ortho indentation, sibling imports and AOI preflight error reporting.
- No change to output tile naming version (v1), AOI registry schema, or published segment geometries.

## Historical release notes

These notes describe older versions. Refer to README.md and scripts/README.txt for the current v1.08 operator workflow.

HISTORICAL RELEASE NOTES (v1.05-v1.06; superseded where v1.08 differs)
----------------------------------------------------------------------
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

