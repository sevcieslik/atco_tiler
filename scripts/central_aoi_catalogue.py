"""Read-only unified AOI catalogue for GUI and processing dispatch.

Circuits stay authoritative in existing entities/revisions tables.
Telecoms and substations live in standalone_aoi_registry in the same DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely import wkb
from shapely.ops import unary_union

from import_standalone_aois import DEFAULT_DB, TABLE


def get_standalone_records(db: Path = DEFAULT_DB):
    if not Path(db).is_file():
        return []
    with sqlite3.connect(str(db)) as con:
        con.execute("PRAGMA query_only=ON")
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone():
            return []
        rows = con.execute(
            f"SELECT entity_kind,entity_id,revision,source_shp,crs_wkt,polygon_wkb "
            f"FROM {TABLE} ORDER BY entity_kind,entity_id,revision"
        ).fetchall()
    items = []
    for kind, entity, revision, source, crs_wkt, polygon in rows:
        items.append(dict(kind=kind, entity=entity, revision=revision,
                          path=Path(source), crs_wkt=crs_wkt, polygon_wkb=polygon,
                          has_segments=False))
    # Retain the newest registered version per entity.
    newest = {}
    for item in items:
        key = item["kind"], item["entity"].casefold()
        if key not in newest or int(item["revision"].lstrip("v")) > int(newest[key]["revision"].lstrip("v")):
            newest[key] = item
    return sorted(newest.values(), key=lambda x: (x["kind"], x["entity"].casefold()))


def validate_standalone(record):
    path = Path(record["path"])
    if not path.is_file():
        raise FileNotFoundError(f"Registered AOI source file unavailable: {path}")
    frame = gpd.read_file(path)
    if frame.empty or not frame.crs or not frame.geometry.is_valid.all():
        raise ValueError(f"Invalid registered AOI shapefile: {path}")
    kinds = frame.geometry.geom_type.dropna()
    if kinds.empty or not kinds.isin(("Polygon","MultiPolygon")).all():
        raise ValueError(f"Registered AOI is not polygonal: {path}")
    selected_geom = unary_union([g for g in frame.geometry if g is not None and not g.is_empty])
    local_crs = CRS.from_user_input(frame.crs)
    reference_crs = CRS.from_wkt(record["crs_wkt"])
    if not local_crs.equals(reference_crs):
        selected_geom = gpd.GeoSeries([selected_geom], crs=local_crs).to_crs(reference_crs).iloc[0]
    registered_geom = wkb.loads(record["polygon_wkb"])
    difference = registered_geom.symmetric_difference(selected_geom).area
    if difference > 0.05:
        raise RuntimeError(
            f"AOI VERSION MISMATCH: {record['entity']} {record['revision']} changed "
            f"since import ({difference:.3f} coordinate units squared). "
            "Publish a new registry version before delivery.")
    return selected_geom, reference_crs
