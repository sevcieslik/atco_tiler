ATCO Retiler v1.08-dev
======================

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

v1.08-dev (not merged / UAT required)
------------------------------------
Standard Retiler accepts multiple *_Polygon.shp files in input\\aoi.
Operators can select one or more entities in the GUI. Each must match the
central AOI registry and have its own published segments; all outputs remain
separate per entity. One failed entity should be reviewed before delivery.

Client delivery naming:
  TIFF, ECW: <Circuit>_<TileID>_<Year>_5cm_RGBN_<version>.<ext>
  LAZ, zLAS: <Circuit>_<TileID>_<version>.<ext>
  Tile Index: <Circuit>_<Year>_Tiles_<version>.shp
Example 9L137_0008_2026_5cm_RGBN_v1.tif / 9L137_0008_v1.laz.
The filename says RGBN per the client table, although band 4 remains NIR
(RGBI); this naming discrepancy is pending client confirmation.

Registered Substation/Telecom AOIs use the central SQLite registry. No separate
mode selection is required. Each AOI is delivered as a single un-tiled entity
with provisional stem <Entity>_2026_v1; only circuits produce Tile Index.
Large-area mosaics use bounded-window BigTIFF export (rather than loading a
complete raster into RAM), with output disk space preflight.

Faster first-run environment:
  INSTALL_RUNTIME.bat: base GeoPandas + Rasterio stack (TIFF/ECW).
  INSTALL_LIDAR_SUPPORT.bat: optional PDAL CLI for LAZ and zLAS.
  INSTALL_TRAJECTORY_SUPPORT.bat: optional PyArrow for TX trajectory matching.
  EzLAS.exe is still needed for zLAS and Global Mapper for ECW.
Existing complete runtimes remain usable and are not downgraded.

Mode-isolated output directories (v1.08)
----------------------------------------
Segmented circuits and standalone area entities are automatically separated
into their appropriate deliverable directories:
  output\\<ENTITY>\\TIFF_segments\\
  output\\<ENTITY>\\ECW_segments\\
  output\\<ENTITY>\\LAZ_segments\\
  output\\<ENTITY>\\zLAS_segments\\
  output\\<ENTITY>\\TIFF_area\\
  output\\<ENTITY>\\ECW_area\\
  output\\<ENTITY>\\LAZ_area\\
  output\\<ENTITY>\\zLAS_area\\
Circuits continue to publish Tile Index from central segments.
Standalone AOIs do not publish a Tile Index. Existing v1.07 output folders
TIFF/ECW/LAZ/zLAS are not migrated or auto-deleted, and are not counted
as v1.08 cached output. Rebuild using Full Build to generate new folders.

Large-area imagery is written as one windowed BigTIFF with NoData=255,
without loading the complete AOI into RAM. A full-area TIFF is still very
large on disk; ECW also requires an RGB intermediate and Global Mapper.

Unified AOI Registry (v1.08-dev)
--------------------------------
One processing screen and one authoritative version registry. No TX/DX versus
Substation/Telecoms switch. Both entity kinds use the EXISTING central
W:\\APPS\\PIT\\ATCO\\database\\atco_aoi_master.sqlite database.

Circuit entities (entity_kind='circuit') use approved central segments
and produce one set of output tiles plus Tile Index.
Area entities (entity_kind='area') use the polygon from the CURRENT revision
and produce one TIFF / ECW / LAZ / zLAS file per selected area, without a
segment index. Both products use their respective _segments / _area folders.

The pre-existing W:\\APPS\\PIT\\ATCO\\tools\\atco_aoi_db.py and
BUILD_ATCO_AOI_DB.bat are the ONLY supported publisher. It scans:
  W:\\NMCP26054\\products\\circuit\\<Entity>\\05_minimum_coverage_polygon\\vN
  W:\\NMCP26054\\products\\area\\<Entity>\\05_minimum_coverage_polygon\\vN
Use menu option 2 (Scan AOIs) for dry-run first. Review last_scan.csv
before option 3 (Publish new/changed versions). Existing publisher owns
schema, R01/R02 revision history, source fingerprints and snapshots,
and the _PUBLISH_LOCK coordination on the network drive.

Never create an independent area table or run a separate ad-hoc importer.
The Retiler's central_aoi_catalogue.py only READS the original entities
and revisions table and verifies the source polygon is still identical
to the currently published revision (including selected GPKG/GDB layers).
Any AOI revision must be published using the original registry tools.

After publishing, restart the Retiler or refresh source mapping. The GUI
shows registered area entries alongside local circuit AOIs but never selects
all area entries automatically. The operator selects intended entities.

Note: No central database writes or source polygons are performed by
the Retiler itself. This feature requires W: access and Windows UAT before
release. Main retains stable v1.07.
