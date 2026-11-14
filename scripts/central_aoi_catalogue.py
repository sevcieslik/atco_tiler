"""Read the existing ATCO operational AOI registry (no schema migrations).

Uses the original entities/revisions schema from tools/atco_aoi_db.py.
Only current revisions are offered for delivery. Circuits continue to use
approved published segment indexes. Areas are single whole-polygon outputs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely import wkb
from shapely.ops import unary_union

DEFAULT_DB = Path(r"W:\APPS\PIT\ATCO\database\atco_aoi_master.sqlite")


def get_standalone_records(db: Path = DEFAULT_DB):
    """List current entity_kind='area' entries from existing entities/revisions."""
    db = Path(db)
    if not db.is_file():
        return []
    with sqlite3.connect(str(db)) as con:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            """SELECT e.entity_id, r.revision_code, r.polygon_source,
                      r.crs_wkt, r.polygon_wkb, r.id, r.source_version
               FROM entities e
               JOIN revisions r ON r.id = e.current_revision
               WHERE e.entity_kind = 'area' AND r.entity_kind = 'area'
                 AND r.entity_id = e.entity_id
               ORDER BY e.entity_id"""
        ).fetchall()
    results = []
    for entity, revision, source, crs_wkt, polygon, rid, version in rows:
        # Original publisher stores GPKG/FileGDB layer as "path::layer".
        filepath, separator, layer = source.partition("::")
        results.append(dict(
            kind="area", entity=entity, revision=revision,
            path=Path(filepath), layer=layer if separator else None,
            source_ref=source, crs_wkt=crs_wkt, polygon_wkb=polygon,
            revision_id=rid, source_version=version, has_segments=False))
    return results


def validate_standalone(record):
    """Require exact registered geometry and current revision before processing.

    The original publishing tool owns edits, versioning, and source snapshots.
    This function is read-only; never publish revised geometry here.
    """
    path = Path(record["path"])
    if not path.is_file():
        raise FileNotFoundError(f"Registered AOI source unavailable: {path}")
    frame = gpd.read_file(path, **({"layer": record["layer"]} if record.get("layer") else {}))
    if frame.empty or not frame.crs or not frame.geometry.is_valid.all():
        raise ValueError(f"Invalid registered AOI shapefile: {path}")
    kinds = frame.geometry.geom_type.dropna()
    if kinds.empty or not kinds.isin(("Polygon", "MultiPolygon")).all():
        raise ValueError(f"Registered AOI must contain polygons: {path}")
    selected_geom = unary_union(
        [g for g in frame.geometry if g is not None and not g.is_empty]
    )
    if selected_geom.is_empty:
        raise ValueError(f"Empty registered AOI: {path}")
    selected_crs = CRS.from_user_input(frame.crs)
    reference_crs = CRS.from_wkt(record["crs_wkt"])
    if not selected_crs.equals(reference_crs):
        selected_geom = gpd.GeoSeries(
            [selected_geom], crs=selected_crs
        ).to_crs(reference_crs).iloc[0]
    registered_geom = wkb.loads(record["polygon_wkb"])
    difference = registered_geom.symmetric_difference(selected_geom).area
    if difference > 0.05:
        raise RuntimeError(
            f"AOI VERSION MISMATCH: {record['entity']} {record['revision']} "
            f"differs from central polygon by {difference:.3f} square map units. "
            "Publish a new revision using the existing ATCO AOI Registry."
        )
    return selected_geom, reference_crs
