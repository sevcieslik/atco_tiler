"""AOI discovery and batch selection for ATCO Retiler v1.08.

Never merge geometries from different entities. Batch ordering is deterministic.
"""
from pathlib import Path


def _has_polygon_geometry(shp: Path) -> bool:
    """Do not treat *_Lines.shp or other non-polygon layers as entities."""
    import geopandas as gpd

    frame = gpd.read_file(shp)
    if frame.empty:
        return False
    kinds = frame.geometry.geom_type.dropna()
    return not kinds.empty and kinds.isin(("Polygon", "MultiPolygon")).all()



def discover_aois(path: Path, *, converter: bool = False):
    root = Path(path)
    if root.is_file():
        if root.suffix.lower() != ".shp":
            raise ValueError(f"AOI must be a shapefile: {root}")
        if not converter and not root.stem.lower().endswith("_polygon"):
            raise ValueError(f"Standard Retiler requires *_Polygon.shp: {root}")
        if not _has_polygon_geometry(root):
            raise ValueError(f"Only polygon AOIs are supported: {root}")
        return [root]
    if not root.is_dir():
        raise FileNotFoundError(f"AOI path not found: {root}")
    files = sorted(
        (p for p in root.rglob("*")
         if p.is_file() and p.suffix.lower() == ".shp"
         and (converter or p.stem.lower().endswith("_polygon"))),
        key=lambda p: str(p).casefold(),
    )
    files = [p for p in files if _has_polygon_geometry(p)]
    if not files:
        raise FileNotFoundError(f"No polygon shapefiles found in {root}")
    unique = {}
    for path in files:
        name = path.stem[:-8] if path.stem.lower().endswith("_polygon") else path.stem
        key = name.casefold()
        if key in unique:
            raise ValueError(f"Ambiguous AOI entity {name}: {unique[key]} and {path}")
        unique[key] = path
    return files
