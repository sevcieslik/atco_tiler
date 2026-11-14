ATCO Retiler v1.08-dev | Operator and technical documentation
=============================================================

STATUS: Development branch, Windows UAT required. Stable main remains v1.07.
Launch: ATCO_RETILER.bat (project-local Python environment: scripts\.venv).
Questions, bugs, improvements: pit@nmgroup.com

OPERATOR WORKFLOW
-----------------
1. Populate the local project input folders:
     input\aoi\             polygon SHP sources for the intended area/circuit
     input\orthos\          RGBI GeoTIFF source imagery (when TIFF/ECW selected)
     input\point_cloud\     LAS/LAZ source cloud (when LAZ/zLAS selected)

2. Select deliverable products in Products & Progress:
     TIFF, ECW, LAZ, zLAS, Tile Index (circuits only).
   ECW requires TIFF; zLAS requires LAZ. Unselected products do not require
   their corresponding input data.

3. Click VALIDATE INPUT in Project Setup, directly below the AOI / Orthos /
   Point Cloud readiness indicators. These start GREY even when directories
   exist. The validation scans local AOI geometry and imagery/LiDAR footprints,
   compares candidates with the CURRENT AOI revision in the central W-drive
   database, and determines which entities have sufficient source coverage.
   Only selected/checked data types are turned GREEN after successful checks;
   an unread/unused data type remains GREY. An unsuccessful validation shows
   an error indication and details in Activity Log.

4. After validation, the Eligible Entities box lists only qualifying entity
   IDs, circuit/area type and central revision (e.g. 9L137 [circuit] R01).
   It does NOT list W-drive polygon paths or every central area. Select one or
   multiple eligible entities using Ctrl/Shift; Select All selects eligible
   entries only.

5. Click PROCESS to regenerate chosen products, or PROCESS MISSING to fill
   incomplete outputs without reprocessing completed files. Process remains
   disabled until successful validation and a valid entity/product selection.
   A changed input path or product selection invalidates previous eligibility;
   rerun Validate Input before processing.

6. Inspect output files and QA reports. Passing validation indicates that
   SOURCE FILE FOOTPRINTS cover the selected AOI; it does not prove that valid
   pixels/points exist everywhere inside those files. NoData gaps and sparse
   LiDAR must still be checked in processing QA.

VALIDATION LOGIC
----------------
- Current registry is READ-ONLY:
  W:\APPS\PIT\ATCO\database\atco_aoi_master.sqlite
  Existing entities and revisions tables contain circuit and area entries.
- The local input/aoi supplies geometric search coverage, never a remote
  directory pick-list. Only an eligible intersection with the local source
  footprints is offered for processing.
- Circuit: exact current registered polygon is required locally and the
  approved central segment index must exist.
- Area (Substation/Telecom): central current-revision polygon must be covered
  by the locally supplied AOI. Area is processed as one unsegmented unit.
- Minimum AOI/source footprint coverage: 99% for matched area, selected
  imagery and/or selected LiDAR. This is an extent-based preflight, not a
  raster-valid-data or point-density guarantee.
- Source geometry must carry a CRS. Four-band RGBI TIFFs are required for
  imagery. LiDAR footprint indexing requires PDAL; if LAZ/zLAS is selected
  and PDAL is absent, Retiler can install it automatically into scripts\.venv.
- Source availability is shown GREY before validation, GREEN only after
  successful evaluation, and an error state is used when validation fails.
  Skipped optional source types remain unvalidated rather than green.
- Activity Log documents excluded entities and validation errors. On failure,
  Process stays disabled.

AUTHORITATIVE AOI PUBLISHING
----------------------------
Retiler does NOT add or alter central registry records or segment geometries.
Use the existing ATCO AOI Registry publisher (atco_aoi_db.py /
BUILD_ATCO_AOI_DB.bat; local launcher may call the same original script).
Publisher scans:
  W:\NMCP26054\products\circuit\<ENTITY>\05_minimum_coverage_polygon\vN\
  W:\NMCP26054\products\area\<ENTITY>\05_minimum_coverage_polygon\vN\
Scan AOIs is a dry run; review the report before publishing new/changed
versions. Published records use R01, R02, etc. and preserve snapshots,
history and network publish locking. All client output revisions remain QA
metadata; there is no extra R01 folder in the delivery layout.

PRODUCTS AND FOLDERS
--------------------
Circuits (segmented outputs):
  output\<ENTITY>\TIFF_segments\
  output\<ENTITY>\ECW_segments\
  output\<ENTITY>\LAZ_segments\
  output\<ENTITY>\zLAS_segments\
  output\<ENTITY>\Tile_Index\
  output\<ENTITY>\QA\

Areas (single AOI outputs):
  output\<ENTITY>\TIFF_area\
  output\<ENTITY>\ECW_area\
  output\<ENTITY>\LAZ_area\
  output\<ENTITY>\zLAS_area\
  output\<ENTITY>\QA\

Segmented and unsegmented products never share a product directory.
Legacy output/<ENTITY>/{TIFF,ECW,LAZ,zLAS} directories from v1.07 are
not migrated or counted as v1.08 cache entries.
Circuit Tile Index is an intermediate SHP under Tile_Index; it becomes
the TILES feature class when the final File GDB is assembled. Areas have
no Tile Index and no required circuit alignment/segmentation.

Circuit delivery filename examples (central BlockNum retained internally):
  9L137_MU_0001 -> 9L137_0001
  TIFF/ECW: 9L137_0001_2026_5cm_RGBN_v1.tif/.ecw
  LAZ/zLAS: 9L137_0001_v1.laz/.zlas
  Tile Index: 9L137_2026_Tiles_v1.shp
Area filename stem (provisional): <ENTITY>_2026_v1
The client table uses "RGBN" in filenames although fourth TIFF band is NIR.
Client naming discrepancy remains to be confirmed.

TIFF outputs are tiled LZW RGBI with an explicit 255 NoData convention.
Both 254,254,254,254 and 255,255,255,255 source background tuples are
suppressed at overlap blending. Valid 255 samples are adjusted to 254.
Very large standalone areas use streamed windowed BigTIFF and require
sufficient disk space. ECW uses three-band RGB output with a white,
non-transparent background via Global Mapper. LAZ filters Class 7 only
and preserves source classifications and consistent vertical CRS;
zLAS requires EzLAS.exe and verification.

INSTALLATION AND DEPENDENCIES
-----------------------------
INSTALL_RUNTIME.bat creates a lean Python 3.11 Conda scripts\.venv with
GeoPandas and Rasterio. PDAL is installed on demand when LAZ/zLAS needs
coverage validation/processing. INSTALL_LIDAR_SUPPORT.bat remains an
optional manual fallback. INSTALL_TRAJECTORY_SUPPORT.bat adds optional
PyArrow for TX trajectory matching. Global Mapper is required for ECW,
EzLAS.exe for zLAS. TIFF/ECW can run without PDAL if LAZ/zLAS is unselected.
No new system Python or Conda-base update is required.

PROCESSING AND QA
-----------------
PROCESS deliberately overwrites existing selected output products after
confirmation; PROCESS MISSING leaves completed products and fills gaps.
Pause suspends the active process tree; Stop terminates it and cleans
temporary/zero-byte files without deleting valid completed deliverables.
Activity Log is available as a dedicated full-width page as well as a
collapsible on-screen preview. Inspect TIFF NoData/colour at seams, ECW in
PLS/MicroStation, Class 7 and return-zero QA for LAZ, EzLAS verification and
Tile Index attributes before delivery.

RELEASE NOTE
------------
This branch is not merged into main. Passing regression/GUI tests do not
constitute Windows end-to-end UAT or client delivery acceptance.

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

