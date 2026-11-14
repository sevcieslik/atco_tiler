"""Compatibility tests for the original ATCO entities/revisions SQLite model.

No standalone_aoi_registry table exists or is created.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from central_aoi_catalogue import get_standalone_records, validate_standalone


class OriginalRegistryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.db = self.root / "atco_aoi_master.sqlite"
        self.area = self.root / "BATTLE_RIVER_Substation_Polygon.shp"
        self.circuit = self.root / "9L137_Polygon.shp"
        gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 10, 10)],
                         crs="EPSG:2956").to_file(self.area)
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE entities (
                    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('circuit','area')),
                    entity_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    current_revision INTEGER,
                    PRIMARY KEY (entity_kind, entity_id));
                CREATE TABLE revisions (
                    id INTEGER PRIMARY KEY, entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL, revision_num INTEGER NOT NULL,
                    revision_code TEXT NOT NULL, source_version TEXT NOT NULL,
                    polygon_source TEXT NOT NULL, crs_wkt TEXT NOT NULL,
                    polygon_wkb BLOB NOT NULL);
            """)
            geom = gpd.read_file(self.area).geometry.iloc[0]
            crs = gpd.read_file(self.area).crs.to_wkt()
            con.execute("INSERT INTO entities VALUES ('circuit','9L137','2026',2)")
            con.execute("INSERT INTO entities VALUES ('area','BATTLE_RIVER_Substation','2026',1)")
            con.execute("INSERT INTO revisions VALUES (1,'area',?,1,'R01','v1',?,?,?)",
                        ("BATTLE_RIVER_Substation", str(self.area), crs, geom.wkb))
            con.execute("INSERT INTO revisions VALUES (2,'circuit',?,1,'R01','v1',?,?,?)",
                        ("9L137", str(self.circuit), crs, geom.wkb))

    def test_area_is_loaded_from_original_tables(self):
        items = get_standalone_records(self.db)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entity"], "BATTLE_RIVER_Substation")
        self.assertEqual(items[0]["revision"], "R01")
        self.assertEqual(items[0]["source_version"], "v1")
        self.assertEqual(items[0]["kind"], "area")
        self.assertFalse(items[0]["has_segments"])
        self.assertEqual(items[0]["path"], self.area)
        validate_standalone(items[0])
        with sqlite3.connect(self.db) as con:
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master WHERE name='standalone_aoi_registry'"
            ).fetchone())

    def test_registered_polygon_change_fails_validation(self):
        item = get_standalone_records(self.db)[0]
        gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 12, 10)],
                         crs="EPSG:2956").to_file(self.area)
        with self.assertRaisesRegex(RuntimeError, "AOI VERSION MISMATCH"):
            validate_standalone(item)

    def test_layer_qualified_sources_are_parsed(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE revisions SET polygon_source = ? WHERE id=1",
                        (str(self.root / "coverage.gpkg") + "::minimum_coverage",))
        item = get_standalone_records(self.db)[0]
        self.assertEqual(item["path"], self.root / "coverage.gpkg")
        self.assertEqual(item["layer"], "minimum_coverage")


if __name__ == "__main__":
    unittest.main()
