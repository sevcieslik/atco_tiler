"""Standalone converter AOI checks (no registry, PDAL or Global Mapper needed)."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box, LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from process_aoi_converter import read_aoi


class ConverterAOITests(unittest.TestCase):
    def test_polygon_valid_without_central_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            shp = Path(folder) / "Substation_01.shp"
            gpd.GeoDataFrame({"code": ["S1"]}, geometry=[box(0, 0, 20, 30)],
                             crs="EPSG:2956").to_file(shp)
            geom, crs = read_aoi(shp)
            self.assertAlmostEqual(geom.area, 600)
            self.assertEqual(crs.to_epsg(), 2956)

    def test_non_polygon_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            shp = Path(folder) / "not_a_polygon.shp"
            gpd.GeoDataFrame({"code": ["L1"]},
                             geometry=[LineString([(0, 0), (1, 1)])],
                             crs="EPSG:2956").to_file(shp)
            with self.assertRaisesRegex(ValueError, "Only polygon AOIs"):
                read_aoi(shp)


if __name__ == "__main__":
    unittest.main()
