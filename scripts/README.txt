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
Every entity uses the same delivery structure, regardless of whether
its approved geometry is a segmented circuit or a whole-polygon area:

  output\<ENTITY>\TIFF\
  output\<ENTITY>\ECW\
  output\<ENTITY>\LAZ\
  output\<ENTITY>\zLAS\
  output\<ENTITY>\QA\

Circuits additionally produce:
  output\<ENTITY>\Tile_Index\

The entity folder isolates outputs; no _segments or _area suffix is
needed. Area entities have no Tile Index and do not need circuit alignment.
Existing *_segments / *_area directories from earlier v1.08 UAT runs
are left intact, never silently moved or deleted. They are NOT treated as
cached outputs after this change. Start a fresh output revision or rebuild
the required products in their new standard folders. Circuit Tile Index
remains an intermediate SHP, later assembled into final TILES feature class.

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

RELEASE HISTORY
---------------
Historical v1.05-v1.06 release notes are preserved in the repository's
CHANGELOG.md. They are not operating instructions for v1.08.
