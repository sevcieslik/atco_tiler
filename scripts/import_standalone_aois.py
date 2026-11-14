"""Central registry extension for unsegmented Telecoms and Substations AOIs.

Stored in the SAME ATCO SQLite database as circuit revisions, but a separate
table avoids changing existing circuit revision schemas without inspecting
their constraints. Import is dry-run by default, transaction-bound on --apply.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely.ops import unary_union

DEFAULT_ROOT = Path(r"W:\NMCP26054\products\area")
DEFAULT_DB = Path(r"W:\APPS\PIT\ATCO\database\atco_aoi_master.sqlite")
TABLE = "standalone_aoi_registry"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS standalone_aoi_registry (
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('substation','telecom')),
    entity_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    source_shp TEXT NOT NULL,
    source_folder TEXT NOT NULL,
    crs_wkt TEXT NOT NULL,
    polygon_wkb BLOB NOT NULL,
    area_native REAL NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(entity_kind, entity_id, revision)
)
"""


def name_and_kind(path: Path):
    names = [x.name for x in path.parents]
    folder = next((x for x in names
                   if x.lower().endswith("_substation") or x.lower().endswith("_telecom")
                   or x.lower().endswith("_telecoms")), None)
    if not folder:
        raise ValueError(f"Cannot derive Substation/Telecoms entity folder from {path}")
    kind = "substation" if folder.lower().endswith("_substation") else "telecom"
    entity = re.sub(r"_(Substation|Telecoms?)$", "", folder, flags=re.I)
    # Preserve client prefixes and original case to avoid collision.
    if not entity:
        raise ValueError(f"Invalid entity name: {folder}")
    return kind, entity


def source_version(shp: Path):
    for ancestor in shp.parents:
        if re.fullmatch(r"v\d+", ancestor.name, flags=re.I):
            return ancestor.name.lower()
    raise ValueError(f"Expected a version folder (v1, v2, ...): {shp}")


def detect_polygon_candidates(root: Path):
    """Only explicitly named minimum-coverage folders, no line/point files."""
    if not root.is_dir():
        raise FileNotFoundError(f"Missing source root: {root}")
    candidates = []
    for directory in root.rglob("*"):
        if not directory.is_dir() or "minimum_coverage_polygon" not in directory.name.lower():
            continue
        for shp in directory.rglob("*"):
            if shp.is_file() and shp.suffix.lower() == ".shp":
                candidates.append(shp)
    return sorted(set(candidates), key=lambda x: str(x).casefold())


def prepare(path: Path):
    kind, entity = name_and_kind(path)
    revision = source_version(path)
    frame = gpd.read_file(path)
    if frame.empty or not frame.crs:
        raise ValueError(f"AOI empty or missing CRS: {path}")
    kinds = frame.geometry.geom_type.dropna()
    if kinds.empty or not kinds.isin(("Polygon", "MultiPolygon")).all():
        return None  # Not a polygon source (e.g., line auxiliaries).
    if not frame.geometry.is_valid.all():
        raise ValueError(f"Invalid polygon geometry: {path}")
    geometry = unary_union([g for g in frame.geometry if g is not None and not g.is_empty])
    if geometry.is_empty:
        raise ValueError(f"Empty geometry: {path}")
    return dict(entity_kind=kind, entity_id=entity, revision=revision,
                source_shp=str(path), source_folder=str(path.parent),
                crs_wkt=CRS.from_user_input(frame.crs).to_wkt(),
                polygon_wkb=geometry.wkb, area_native=float(geometry.area),
                imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def records(db: Path):
    if not db.is_file():
        return []
    with sqlite3.connect(str(db)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone():
            return []
        rows = con.execute(
            f"SELECT entity_kind,entity_id,revision,source_shp,area_native "
            f"FROM {TABLE} ORDER BY entity_kind, entity_id, revision"
        ).fetchall()
    return [dict(zip(("entity_kind","entity_id","revision","source_shp","area_native"), r))
            for r in rows]


def import_aois(root: Path = DEFAULT_ROOT, db: Path = DEFAULT_DB, *,
                apply=False, report: Path | None = None):
    if not db.is_file():
        raise FileNotFoundError(f"Central registry does not exist: {db}")
    grouped = {}
    errors = []
    for shp in detect_polygon_candidates(root):
        try:
            item = prepare(shp)
            if item is None:
                continue
            key = (item["entity_kind"], item["entity_id"].casefold(), item["revision"])
            # Never silently merge or choose one of competing source shapefiles.
            if key in grouped:
                raise ValueError(f"Duplicate entity/version polygon: {grouped[key]['source_shp']} and {shp}")
            grouped[key] = item
        except Exception as exc:
            errors.append(f"{shp}: {exc}")
    summary = dict(source_root=str(root), database=str(db), dry_run=not apply,
                   discovered=len(grouped), rejected=errors,
                   entities=[{k: v for k, v in row.items() if k not in ("polygon_wkb","crs_wkt")}
                             for row in grouped.values()])
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if errors:
        raise ValueError(f"Import validation failed for {len(errors)} items; no database changes. "
                         f"See report: {report}")
    if not apply:
        return summary

    # Backup before mutation, then one write transaction. Do not touch circuit rows.
    backup = db.with_name(db.name + ".before_standalone_import.bak")
    with sqlite3.connect(str(db)) as source, sqlite3.connect(str(backup)) as target:
        source.backup(target)
    cols = ("entity_kind","entity_id","revision","source_shp","source_folder",
            "crs_wkt","polygon_wkb","area_native","imported_at")
    with sqlite3.connect(str(db), timeout=60) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(CREATE_TABLE)
        for item in grouped.values():
            existing = con.execute(
                f"SELECT source_shp,crs_wkt,polygon_wkb FROM {TABLE} "
                "WHERE entity_kind=? AND entity_id=? AND revision=?",
                (item["entity_kind"],item["entity_id"],item["revision"])).fetchone()
            if existing:
                if (existing[0] != item["source_shp"] or
                        existing[1] != item["crs_wkt"] or existing[2] != item["polygon_wkb"]):
                    raise RuntimeError(
                        f"Registered entity changed: {item['entity_id']} {item['revision']}. "
                        "Publish a new version instead of overwriting an existing registration.")
                continue
            con.execute(f"INSERT INTO {TABLE} ({','.join(cols)}) "
                        f"VALUES ({','.join('?' for _ in cols)})",
                        tuple(item[c] for c in cols))
    summary["dry_run"] = False
    summary["backup"] = str(backup)
    return summary


def main():
    p = argparse.ArgumentParser(description="Register Telecoms/Substations polygons in ATCO central SQLite")
    p.add_argument("--source", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--apply", action="store_true", help="Write only after validated dry-run")
    p.add_argument("--report", type=Path, default=Path("standalone_aoi_import_report.json"))
    args = p.parse_args()
    result = import_aois(args.source, args.db, apply=args.apply, report=args.report)
    print(f"Validated {result['discovered']} standalone AOI records; "
          f"{'IMPORTED' if args.apply else 'DRY RUN ONLY'}.")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
