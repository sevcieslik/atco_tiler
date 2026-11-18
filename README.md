# ATCO Retiler v1.08-dev

ATCO delivery processor for classified LAS/LAZ and four-band RGB+NIR GeoTIFF imagery. The development branch is undergoing Windows UAT; **main remains the stable v1.07 release**.

## Operator workflow

1. Place local project data in `input/aoi/`, `input/orthos/` and `input/point_cloud/`. Only the source folders required by selected products must be populated.
2. Launch **ATCO_RETILER.bat** from the project root. The launcher uses the local `scripts/.venv` Conda runtime and configures GIS DLL, GDAL and PROJ paths.
3. Select TIFF, ECW, LAZ, zLAS and/or Tile Index in **Products & Progress**.
4. Click **Validate Input** in Project Setup, below the three source indicators. Before validation, they remain grey even if folders are present. Successfully validated source types turn green, unselected types stay grey, and invalid sources display a validation error.
5. Select one or more **Eligible Entities** discovered from local AOI/imagery/cloud coverage. The remote registry supplies authoritative geometry and revisions, not a list of W-drive paths for the operator.
6. Click **Process** to regenerate selected deliverables, or **Process Missing** to fill incomplete outputs. Review QA reports and open products in client software.

**Process stays disabled** until the current input and requested source products are validated. A new input directory or changed imagery/cloud product requirement needs revalidation. Selecting Tile Index for an already validated circuit does not invalidate source coverage.

## Circuits and areas

Circuits use current registered AOIs and approved central segment indexes. Telecoms/Substations are registered as `entity_kind=area` in the existing `entities`/`revisions` SQLite schema and are processed as a single whole-polygon area. No manual TX/DX-versus-area mode switch or additional registry table is needed. Tile Index is available only for segmented circuits.

The central registry on W: is **read-only to Retiler**. Revisions must be created with the existing ATCO AOI Registry publisher. Eligibility requires local AOI and selected source file footprints to cover the candidate geometry (99% area/source threshold; exact registered circuit AOI plus approved segment index). Footprint checks do not guarantee valid pixels or point density everywhere.

## Outputs

Each processed entity uses the same straightforward product directories:

```text
output/<RUN>/<ENTITY>/
  TIFF/
  ECW/
  LAZ/
  zLAS/
  QA/
  Tile_Index/    # circuits only
```

Do not append `_segments` or `_area` to these directories. Earlier v1.08 UAT folders with those suffixes are preserved but not reused as cache. A TX circuit's imagery naming example is `9L137_0001_2026_5cm_RGBN_v1.tif`; point clouds use `9L137_0001_v1.laz`. The TIFF retains georeferencing, four bands and NoData metadata, but **does not include custom `ATCO_*` tags**.

## Optional dependencies

The base Conda runtime stays lightweight. If LAZ/zLAS requires PDAL, or a TX Tile Index requires PyArrow for trajectory attribution, the application can install the optional dependency in the existing project-local `.venv`. Global Mapper is required for ECW and EzLAS.exe for zLAS. No second system Python is required.

See `scripts/README.txt`, `scripts/docs/technical_notes.html`, `scripts/docs/faq_troubleshooting_qa.html`, and the in-app **How to Start** page for full usage, troubleshooting and QA details.

Do not commit source data, output products, private configuration, runtime environments or separately licensed executables.
