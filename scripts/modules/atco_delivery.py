from __future__ import annotations

import re
from pathlib import Path
from pyproj import CRS

YEAR = 2026
TILE_VERSION = "v1"
TX_ENTITIES = {"7L23", "7L56", "7L65", "9L137", "9L155", "9L32", "9L66"}


def is_tx(entity: str) -> bool:
    return str(entity).upper() in TX_ENTITIES


def entity_type(entity: str) -> str:
    return "TX" if is_tx(entity) else "DX"


def client_entity_name(entity: str) -> str:
    """Client-facing entity name.

    TX already uses the client circuit code.  The central DX IDs use underscores
    only because they are filesystem/database-safe; the SoW SQLite uses spaces.
    """
    entity = str(entity).strip()
    return entity if is_tx(entity) else entity.replace("_", " ")


def tile_sequence(blocknum: str) -> str:
    """Stable sequential component from approved central segment BlockNum.

    Existing published segment sets are intentionally not rewritten.  Examples:
      9L137_MU_0001 -> 0001
      UTIKUMA_OILFIELD_MU_0269 -> 0269
    """
    text = str(blocknum).strip()
    m = re.search(r"(?:_MU_)?(\d+)$", text, flags=re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot derive tile sequence from central BlockNum: {blocknum}")
    raw = m.group(1)
    return raw.zfill(max(4, len(raw)))


def delivery_tile_prefix(entity: str) -> str:
    """Common client naming stem for every tiled product."""
    return f"{client_entity_name(entity)}_{YEAR}_Tiles_{TILE_VERSION}"


def delivery_imagery_name(entity: str, blocknum: str) -> str:
    """Client imagery schema: <Circuit>_<TileID>_<Year>_<5cm_RGBN>_<version>."""
    return f"{client_entity_name(entity)}_{tile_sequence(blocknum)}_{YEAR}_5cm_RGBN_{TILE_VERSION}"


def delivery_point_cloud_name(entity: str, blocknum: str) -> str:
    """Client classified cloud schema: <Circuit>_<TileID>_<version>."""
    return f"{client_entity_name(entity)}_{tile_sequence(blocknum)}_{TILE_VERSION}"


def delivery_tile_name(entity: str, blocknum: str) -> str:
    """Compatibility alias for the imagery name; use product-specific names elsewhere."""
    return delivery_imagery_name(entity, blocknum)


def enrich_segments(seg, entity: str):
    out = seg.copy()
    out["CentralBlock"] = out["BlockNum"].astype(str)
    out["TileSeq"] = out["CentralBlock"].map(tile_sequence)
    out["ImageryName"] = out["CentralBlock"].map(lambda b: delivery_imagery_name(entity, b))
    out["PointCloudName"] = out["CentralBlock"].map(lambda b: delivery_point_cloud_name(entity, b))
    # Retain the imagery compatibility field for existing ortho processing.
    out["DeliveryName"] = out["ImageryName"]
    for col in ("ImageryName", "PointCloudName"):
        if out[col].duplicated().any():
            dupes = out.loc[out[col].duplicated(False), col].tolist()
            raise ValueError(f"Duplicate {col} derived from central segments: {dupes[:10]}")
    return out


def output_base(output_root: Path, entity: str) -> Path:
    # AOI revision is metadata, not an extra delivery folder.
    return Path(output_root) / str(entity)


def delivery_gdb_path(base: Path, entity: str) -> Path:
    """Reserved for the later final-delivery GDB assembly stage."""
    return Path(base) / f"{client_entity_name(entity)}_{YEAR}.gdb"


def tile_index_shp_path(base: Path, entity: str) -> Path:
    """Intermediate Tile Index written by Retiler before final GDB assembly."""
    return Path(base) / "Tile_Index" / f"{delivery_tile_prefix(entity)}.shp"


def utm_zone(crs) -> str:
    c = CRS.from_user_input(crs)
    auth = c.to_authority()
    if auth:
        # Project uses EPSG:2955 (zone 11N) and EPSG:2956 (zone 12N).
        if str(auth[1]) == "2955":
            return "11"
        if str(auth[1]) == "2956":
            return "12"
    name = c.name or ""
    m = re.search(r"UTM\s+zone\s+(\d+)", name, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot derive UTM zone from CRS: {c.to_string()}")
