"""Standalone single-AOI TIFF/ECW/LAZ/zLAS converter for Telecoms/Substations.

Standalone area geometry is validated against the existing ATCO operational
AOI registry before processing. No segment index is required. Large imagery
AOIs use windowed BigTIFF export to keep memory bounded.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
from pyproj import CRS
from shapely.ops import unary_union

import process_orthos as ortho
from atco_delivery import product_folder
import process_lidar as lidar


def read_aoi(path: Path, layer=None):
    path = Path(path)
    if not path.exists() or path.suffix.lower() not in (".shp", ".gpkg", ".geojson", ".gdb"):
        raise ValueError(f"Expected polygon SHP/GPKG/GeoJSON/GDB: {path}")
    frame = gpd.read_file(path, **({"layer": layer} if layer else {}))
    if frame.empty or not frame.crs:
        raise ValueError(f"AOI is empty or missing CRS: {path}")
    if not frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError(f"Only polygon AOIs are supported: {path}")
    if not frame.geometry.is_valid.all():
        raise ValueError(f"AOI has invalid polygons: {path}")
    geom = unary_union([g for g in frame.geometry if g is not None and not g.is_empty])
    if geom.is_empty:
        raise ValueError(f"AOI has no non-empty geometry: {path}")
    return geom, CRS.from_user_input(frame.crs)


def process(aoi_path: Path, output_root: Path, *, ortho_source=None, cloud_source=None,
            make_tiff=False, make_ecw=False, make_laz=False, make_zlas=False,
            overwrite=False, global_mapper=None, ezlas_exe=None, entity_name=None, revision=None, aoi_layer=None):
    if make_ecw:
        make_tiff = True
    if make_zlas:
        make_laz = True
    if not (make_tiff or make_laz):
        raise ValueError("Select at least one imagery or point-cloud product")
    if make_tiff and not ortho_source:
        raise ValueError("Imagery source required for TIFF/ECW")
    if make_laz and not cloud_source:
        raise ValueError("LiDAR source required for LAZ/zLAS")

    polygon = Path(aoi_path)
    geom, crs = read_aoi(polygon, layer=aoi_layer)
    entity = polygon.stem
    if entity.lower().endswith("_polygon"):
        entity = entity[:-8]
    if not entity or entity in {".", ".."}:
        raise ValueError("Invalid entity name")
    if entity_name:
        entity = str(entity_name)
    if revision:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from central_aoi_catalogue import get_standalone_records, validate_standalone
        matches = [r for r in get_standalone_records()
                   if r["kind"] == "area" and r["entity"] == entity
                   and r["revision"] == revision]
        if len(matches) != 1:
            raise ValueError(f"Unregistered or ambiguous standalone AOI: {entity} {revision}")
        if (Path(matches[0]["path"]) != polygon or matches[0].get("layer") != aoi_layer):
            raise ValueError("AOI source and layer do not match central registry")
        validate_standalone(matches[0])
    stem = f"{entity}_2026_v1"
    base = Path(output_root) / entity
    qa = base / "QA" / "Converter"
    qa.mkdir(parents=True, exist_ok=True)
    results = {"mode": "AOI Converter", "aoi": str(polygon), "entity": entity,
               "products": {}, "tile_index": None, "source_segments": None}

    if make_tiff:
        files = ortho.source_tiffs(Path(ortho_source))
        _inventory, transform, res, bands = ortho.raster_inventory(files, crs)
        bounds = ortho.align_bounds(geom.bounds, transform, res)
        pixels = round((bounds[2] - bounds[0]) / res[0]) * round((bounds[3] - bounds[1]) / res[1])
        tif = base / product_folder("TIFF", converter=True) / f"{stem}.tif"
        if pixels > 150_000_000:
            from process_large_ortho import export_streamed_rgb_i, preflight_storage
            if overwrite or not tif.is_file() or tif.stat().st_size == 0:
                preflight_storage(tif.parent, pixels, ecw=make_ecw)
            print(f"Large AOI: {pixels:,} pixels; exporting one BigTIFF in bounded windows.", flush=True)
            status, valid, coverage = export_streamed_rgb_i(
                stem, bounds, geom, files, tif, crs, res, overwrite=overwrite)
        else:
            status, valid, coverage = ortho.export_tile(
                stem, bounds, geom, files, tif, bands, crs, res, overwrite=overwrite)
        if not tif.is_file() or tif.stat().st_size == 0:
            raise RuntimeError(f"TIFF could not be produced ({status}) for {entity}")
        results["products"]["TIFF"] = {"file": str(tif), "status": status, "coverage_pct": coverage}
        if make_ecw:
            gm = Path(global_mapper) if global_mapper else ortho.find_global_mapper()
            if gm is None or not gm.is_file():
                raise RuntimeError("Global Mapper not detected; ECW unavailable")
            rgb_dir = qa / "gm_rgb_input"
            pairs = ortho.prepare_gm_inputs([tif], rgb_dir)
            out_pairs = ortho.run_global_mapper(
                gm, pairs, base / product_folder("ECW", converter=True), qa, compression=10,
                workers=1, overwrite=overwrite)
            ecw = out_pairs[0][1]
            if not ecw.is_file() or ecw.stat().st_size == 0:
                raise RuntimeError(f"ECW export failed for {entity}")
            results["products"]["ECW"] = {"file": str(ecw), "status": "created"}

    if make_laz:
        lidar.validate_pdal_available()
        files = lidar.source_clouds(Path(cloud_source))
        inventory, point_crs, crs_meta = lidar.cloud_inventory(files, crs, qa)
        chosen = lidar.intersecting_sources(geom, inventory)
        if not chosen:
            raise ValueError(f"No point-cloud coverage for AOI {entity}")
        laz = base / product_folder("LAZ", converter=True) / f"{stem}.laz"
        if overwrite or not laz.is_file() or laz.stat().st_size == 0:
            lidar.run_pdal_segment(SimpleNamespace(geometry=geom), chosen, laz, None, point_crs)
            laz_status = "created"
        else:
            laz_status = "exists"
        if not laz.is_file() or laz.stat().st_size == 0:
            raise RuntimeError(f"LAZ export failed for {entity}")
        results["products"]["LAZ"] = {"file": str(laz), "status": laz_status,
                                       "vertical_crs": crs_meta.get("vertical_crs")}
        if make_zlas:
            ezlas = Path(ezlas_exe) if ezlas_exe else lidar.find_ezlas()
            if not ezlas or not ezlas.is_file():
                raise FileNotFoundError("EzLAS.exe is required for zLAS conversion")
            zlas = base / product_folder("zLAS", converter=True) / f"{stem}.zlas"
            if overwrite or not zlas.is_file() or zlas.stat().st_size == 0:
                temp_dir = qa / "temp_las_for_zlas"
                temp_dir.mkdir(parents=True, exist_ok=True)
                tmp_las = temp_dir / f"{stem}.las"
                lidar.temp_las_from_existing_laz(laz, tmp_las, point_crs)
                created = lidar.convert_zlas_batch(
                    temp_dir, base / product_folder("zLAS", converter=True), ezlas, [stem],
                    lidar.horizontal_crs(point_crs), qa_dir=qa)
                tmp_las.unlink(missing_ok=True)
                if zlas not in created:
                    raise RuntimeError(f"zLAS conversion or verification failed for {entity}")
                zlas_status = "created"
            else:
                zlas_status = "exists"
            results["products"]["zLAS"] = {"file": str(zlas), "status": zlas_status}

    (qa / "execution.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"DONE: AOI Converter {entity} -> {base}", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser(description="ATCO standalone AOI converter")
    ap.add_argument("--aoi", type=Path, required=True)
    ap.add_argument("--entity", type=str)
    ap.add_argument("--revision", type=str)
    ap.add_argument("--aoi-layer", type=str)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--ortho-source", type=Path)
    ap.add_argument("--cloud-source", type=Path)
    for p in ("tiff", "ecw", "laz", "zlas"):
        ap.add_argument(f"--{p}", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--global-mapper-exe", type=Path)
    ap.add_argument("--ezlas-exe", type=Path)
    args = ap.parse_args()
    process(args.aoi, args.output_root,
            ortho_source=args.ortho_source, cloud_source=args.cloud_source,
            make_tiff=args.tiff, make_ecw=args.ecw,
            make_laz=args.laz, make_zlas=args.zlas,
            overwrite=args.overwrite, global_mapper=args.global_mapper_exe,
            ezlas_exe=args.ezlas_exe, entity_name=args.entity,
            revision=args.revision, aoi_layer=args.aoi_layer)


if __name__ == "__main__":
    main()
