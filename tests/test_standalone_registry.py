"""Central standalone AOI import contract against a temporary SQLite database."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_standalone_aois import import_aois, records
from central_aoi_catalogue import get_standalone_records, validate_standalone


class RegistryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "atco_aoi_master.sqlite"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE entities (entity_kind TEXT, entity_id TEXT, current_revision INTEGER)")
            con.execute("INSERT INTO entities VALUES ('circuit','9L137', 1)")
        self.folder = (self.root / "products" / "area" / "BATTLE_RIVER_Substation"
                       / "05_minimum_coverage_polygon" / "v1")
        self.folder.mkdir(parents=True)
        self.shp = self.folder / "coverage.shp"
        gpd.GeoDataFrame({"id":[1]}, geometry=[box(0,0,20,20)],
                         crs="EPSG:2956").to_file(self.shp)

    def test_dry_run_does_not_modify_database(self):
        report = self.root / "report.json"
        result = import_aois(self.root/"products"/"area",self.db, report=report)
        self.assertEqual(result["discovered"],1)
        self.assertTrue(result["dry_run"])
        self.assertTrue(report.is_file())
        self.assertEqual(records(self.db),[])

    def test_apply_keeps_circuit_rows_and_registers_standalone(self):
        result = import_aois(self.root/"products"/"area", self.db, apply=True)
        self.assertFalse(result["dry_run"])
        self.assertTrue(Path(result["backup"]).is_file())
        found = get_standalone_records(self.db)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["kind"],"substation")
        self.assertEqual(found[0]["entity"],"BATTLE_RIVER")
        self.assertEqual(found[0]["revision"],"v1")
        validate_standalone(found[0])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT entity_id FROM entities").fetchone()[0],"9L137")

    def test_changed_registered_geometry_requires_new_version(self):
        import_aois(self.root/"products"/"area", self.db, apply=True)
        gpd.GeoDataFrame({"id":[1]},geometry=[box(0,0,30,30)],
                         crs="EPSG:2956").to_file(self.shp)
        with self.assertRaises(RuntimeError):
            import_aois(self.root/"products"/"area", self.db, apply=True)


if __name__ == "__main__":
    unittest.main()
