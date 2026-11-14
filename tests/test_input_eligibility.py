"""Input-first eligibility: never expose remote entities without local AOI and imagery."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import input_eligibility as engine


class InputEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local_aoi = self.root / "input" / "aoi"
        self.ortho = self.root / "input" / "orthos"
        self.cloud = self.root / "input" / "point_cloud"
        self.local_aoi.mkdir(parents=True)
        self.ortho.mkdir(parents=True)
        self.cloud.mkdir(parents=True)
        self.db = self.root / "db.sqlite"
        gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 20, 20)],
                         crs="EPSG:2956").to_file(self.local_aoi / "9L137_Polygon.shp")
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE entities (
                    entity_kind TEXT, entity_id TEXT, current_revision INTEGER);
                CREATE TABLE revisions (
                    id INTEGER PRIMARY KEY, entity_kind TEXT, entity_id TEXT,
                    revision_code TEXT, polygon_source TEXT,
                    crs_wkt TEXT, polygon_wkb BLOB);
            """)
            crs = "EPSG:2956"
            from pyproj import CRS
            for idx, kind, name, geom in (
                (1, "circuit", "9L137", box(0, 0, 20, 20)),
                (2, "area", "INSIDE_Substation", box(2, 2, 6, 6)),
                (3, "area", "OUTSIDE_Telecom", box(100, 100, 110, 110))):
                con.execute("INSERT INTO entities VALUES (?,?,?)", (kind, name, idx))
                con.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                            (idx, kind, name, "R01", str(self.local_aoi/"9L137_Polygon.shp"),
                             CRS.from_user_input(crs).to_wkt(), geom.wkb))
        self.segments_root = self.root / "segments" / "circuit"
        published = self.segments_root / "9L137" / "R01"
        published.mkdir(parents=True)
        (published / "9L137_AOI_Segments.shp").touch()
        self.mapped = {"aoi":self.local_aoi, "orthos":self.ortho, "point_cloud":self.cloud}

    def _image(self, filename, x, y, width=20, height=20):
        path = self.ortho / filename
        with rasterio.open(path, "w", driver="GTiff", width=width, height=height,
                           count=4, dtype="uint8", crs="EPSG:2956",
                           transform=from_origin(x, y, 1, 1)) as dst:
            dst.write(np.full((4,height,width),50,dtype="uint8"))
        return path

    def test_excludes_remote_area_without_local_overlap(self):
        self._image("in.tif",0,20)
        eligible, excluded = engine.discover_eligible(self.mapped, ["TIFF"], db=self.db, segments_root=self.segments_root)
        self.assertEqual({x["entity"] for x in eligible}, {"9L137","INSIDE_Substation"})
        self.assertNotIn("OUTSIDE_Telecom", [x["entity"] for x in eligible])
        self.assertTrue(all("W:" not in x["entity"] for x in eligible))

    def test_rejects_local_aoi_with_no_image_coverage(self):
        self._image("far.tif",1000,1020)
        eligible, excluded = engine.discover_eligible(self.mapped, ["TIFF"], db=self.db)
        self.assertEqual(eligible, [])
        self.assertTrue(any("insufficient imagery" in reason for _, reason in excluded))

    def test_requires_lidar_coverage_for_laz_selection(self):
        self._image("in.tif",0,20)
        with patch.object(engine, "_cloud_footprints", return_value=(box(100,100,110,110),1)):
            eligible, excluded = engine.discover_eligible(
                self.mapped, ["TIFF","LAZ"], db=self.db, pdal_exe="pdal", segments_root=self.segments_root)
        self.assertEqual(eligible, [])
        self.assertTrue(any("insufficient LiDAR" in reason for _, reason in excluded))

    def test_source_footprint_threshold_does_not_approve_partial_aoi(self):
        self._image("partial.tif",0,20,width=10,height=20)
        eligible, _ = engine.discover_eligible(self.mapped, ["TIFF"], db=self.db)
        self.assertEqual([x["entity"] for x in eligible], ["INSIDE_Substation"])


if __name__ == "__main__":
    unittest.main()
