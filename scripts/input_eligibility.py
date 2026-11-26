"""Input-first spatial preflight for ATCO circuits and standalone area AOIs.

The central registry is a read-only reference, never a pick-list. Candidates
must overlap Input/AOI and have full source footprint coverage for the
selected product types. Footprints are bounding envelopes, not proof of valid
pixels/points inside every footprint; the processing pipeline remains final QA.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import geopandas as gpd
import rasterio
from pyproj import CRS
from shapely import wkb
from shapely.geometry import box
from shapely.ops import unary_union, transform as shape_transform
from pyproj import Transformer

from central_aoi_catalogue import DEFAULT_DB
from modules.aoi_batch import discover_aois


MIN_AREA_COVERAGE = 0.99


def _to_crs(geometry, from_crs, to_crs):
    source = CRS.from_user_input(from_crs).to_2d()
    target = CRS.from_user_input(to_crs).to_2d()
    if source.equals(target):
        return geometry
    transformer = Transformer.from_crs(source, target, always_xy=True)
    return shape_transform(transformer.transform, geometry)


def _read_input_polygons(folder: Path):
    sources = discover_aois(folder, converter=True)
    polygons = []
    for path in sources:
        frame = gpd.read_file(path)
        if frame.empty or not frame.crs:
            raise ValueError(f"Input AOI has no CRS or polygons: {path}")
        geoms = [g for g in frame.geometry if g is not None and not g.is_empty]
        if not geoms or not all(g.geom_type in ("Polygon", "MultiPolygon") and g.is_valid for g in geoms):
            raise ValueError(f"Invalid Input AOI geometry: {path}")
        polygons.append((path, unary_union(geoms), CRS.from_user_input(frame.crs)))
    return polygons


def _registered(db: Path):
    if not Path(db).is_file():
        raise FileNotFoundError(f"Central AOI registry unavailable: {db}")
    with sqlite3.connect(str(db)) as con:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            """SELECT e.entity_kind, e.entity_id, r.revision_code, r.polygon_source,
                      r.crs_wkt, r.polygon_wkb
               FROM entities e JOIN revisions r ON e.current_revision = r.id
               WHERE e.entity_kind=r.entity_kind AND e.entity_id=r.entity_id
                 AND e.entity_kind IN ('circuit','area')
               ORDER BY e.entity_kind, e.entity_id"""
        ).fetchall()
    return [dict(kind=kind, entity=entity, revision=rev, source_ref=source,
                 path=Path(source.split("::", 1)[0]),
                 layer=source.split("::", 1)[1] if "::" in source else None,
                 crs=CRS.from_wkt(crs), geom=wkb.loads(geom))
            for kind, entity, rev, source, crs, geom in rows]


def _image_footprints(folder, crs):
    if not folder or not Path(folder).is_dir():
        raise FileNotFoundError("Input/orthos folder is missing")
    rasters = [p for p in Path(folder).rglob("*") if p.suffix.lower() in (".tif", ".tiff")]
    if not rasters:
        raise ValueError("No TIFF imagery under Input/orthos")
    geoms = []
    for path in rasters:
        with rasterio.open(path) as ds:
            if ds.crs is None:
                raise ValueError(f"Imagery has no CRS: {path}")
            if ds.count < 4:
                raise ValueError(f"Four-band RGBI imagery required: {path}")
            geoms.append(_to_crs(box(*ds.bounds), ds.crs, crs))
    return unary_union(geoms), len(rasters)


def _cloud_footprints(folder, crs, pdal_exe, entity=None, registered_entities=(), log=None, qa_dir=None, workers=None):
    from modules.cloud_source_selection import select_entity_clouds
    emit = log or (lambda line: None)
    selection_mode = 'all'
    if not folder or not Path(folder).is_dir():
        raise FileNotFoundError("Input/point_cloud folder is missing")
    if entity:
        files, selection_mode = select_entity_clouds(
            Path(folder), entity, registered_entities)
    else:
        files = sorted(p for p in Path(folder).rglob("*")
                       if p.is_file() and p.suffix.lower() in (".las", ".laz"))
    if not files:
        raise ValueError("No LAS/LAZ files under Input/point_cloud")
    if not pdal_exe:
        raise RuntimeError("PDAL required to inspect LiDAR footprints")
    from modules.process_lidar import cloud_inventory, horizontal_crs, crs_short_label
    expected = horizontal_crs(crs)
    emit(f'LiDAR coverage: entity={entity}; files={len(files)}; selection={selection_mode}; expected CRS={crs_short_label(expected)}; PDAL={pdal_exe}')
    # The same inventory is used by the export worker. It validates all
    # source horizontal/vertical CRS before any bounds can count as coverage.
    rows, _, _ = cloud_inventory(files, crs, qa_dir, pdal_exe=pdal_exe,
                                 log=emit, workers=workers)
    emit(f'LiDAR coverage header results: {len(rows)}/{len(files)} passed')
    return unary_union([box(*r['bounds']) for r in rows]), len(files)


def _coverage(subject, footprint):
    if subject.is_empty or subject.area <= 0:
        return 0.0
    return float(subject.intersection(footprint).area / subject.area)


def discover_eligible(mapped, products, *, db=DEFAULT_DB, pdal_exe=None, log=None, output_root=None, workers=None,
                      segments_root=Path(r"W:\\APPS\\PIT\\ATCO\\segments\\circuit")):
    """Return verified candidates and spatial-coverage notes.

    Validation requires the Input/AOI polygons to be an actual spatial subset
    of a registered area, or a full circuit matching the central polygon.
    Nothing is selected automatically if validation finds no candidates.
    """
    emit = log or (lambda line: None)
    emit(f"Coverage scan: registry={db}; products={products}; mapped={mapped}")
    inputs = _read_input_polygons(Path(mapped["aoi"]))
    registered = _registered(db)
    emit(f"Coverage scan: {len(inputs)} local AOI files; {len(registered)} registered entities")
    needs_image = any(p in products for p in ("TIFF", "ECW"))
    needs_cloud = any(p in products for p in ("LAZ", "zLAS"))
    needs_index = "Tile Index" in products
    cache = {}
    qualified = []
    excluded = []
    for row in registered:
        geom = row["geom"]
        if geom.is_empty or not geom.is_valid or geom.area <= 0:
            excluded.append((row["entity"], "invalid registered polygon"))
            continue
        footprint = unary_union([_to_crs(poly, crs, row["crs"])
                                 for _, poly, crs in inputs])
        input_cov = _coverage(geom, footprint)
        if input_cov < MIN_AREA_COVERAGE:
            continue
        emit(f"Coverage candidate: {row['entity']} {row['revision']}; AOI coverage={input_cov:.3%}; CRS={row['crs'].to_string()}")
        if needs_index and not any(p != "Tile Index" for p in products) and row["kind"] != "circuit":
            excluded.append((row["entity"], "Tile Index requires approved segments"))
            continue
        local_source = None
        if row["kind"] == "circuit":
            # Existing circuit pipeline validates source against the exact
            # central polygon and requires a published segment index.
            for path, poly, crs in inputs:
                difference = _to_crs(poly, crs, row["crs"]).symmetric_difference(geom).area
                if difference <= 0.05:
                    local_source = path
                    break
            if local_source is None:
                excluded.append((row["entity"], "local circuit AOI differs from registered revision"))
                continue
            approved_segments = (Path(segments_root)
                                 / row["entity"] / row["revision"]
                                 / f"{row['entity']}_AOI_Segments.shp")
            if not approved_segments.is_file():
                excluded.append((row["entity"], "approved central segment index unavailable"))
                continue
        if needs_image:
            key = ("image", row["crs"].to_wkt())
            if key not in cache:
                cache[key] = _image_footprints(mapped.get("orthos"), row["crs"])
            image_cov = _coverage(geom, cache[key][0])
            emit(f"Imagery coverage {row['entity']}: {image_cov:.3%} ({cache[key][1]} files)")
            if image_cov < MIN_AREA_COVERAGE:
                excluded.append((row["entity"], f"insufficient imagery footprint coverage ({image_cov:.3%}; requires 99%)"))
                continue
        if needs_cloud or (needs_index and row["kind"] == "circuit"):
            key = ("cloud", row["crs"].to_wkt(), row["entity"])
            if key not in cache:
                try:
                    qa_dir = (Path(output_root) / row["entity"] / "QA" /
                              ("LiDAR" if row["kind"] == "circuit" else "Converter")) if output_root is not None else None
                    cache[key] = _cloud_footprints(
                        mapped.get("point_cloud"), row["crs"], pdal_exe,
                        entity=row["entity"],
                        registered_entities=[r["entity"] for r in registered], log=emit,
                        qa_dir=qa_dir, workers=workers)
                except ValueError as exc:
                    excluded.append((row["entity"], str(exc)))
                    continue
            cloud_cov = _coverage(geom, cache[key][0])
            emit(f"LiDAR footprint coverage {row['entity']}: {cloud_cov:.3%} ({cache[key][1]} files)")
            if cloud_cov < MIN_AREA_COVERAGE:
                excluded.append((row["entity"], f"insufficient LiDAR footprint coverage ({cloud_cov:.3%}; requires 99%)"))
                continue
        emit(f"Coverage PASS: {row['entity']}")
        qualified.append(dict(
            kind=row["kind"], entity=row["entity"], revision=row["revision"],
            has_segments=row["kind"] == "circuit",
            path=local_source if local_source is not None else row["path"],
            layer=row["layer"], source_ref=row["source_ref"],
            crs_wkt=row["crs"].to_wkt(), polygon_wkb=geom.wkb,
            coverage_note="Spatial footprint coverage >=99% (not per-pixel/point QA)",
        ))
    return qualified, excluded
