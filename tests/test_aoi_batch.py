"""AOI discovery tests with real SHP geometry, including mixed line/polygon folders."""
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from aoi_batch import discover_aois


def polygon(path):
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 20, 20)],
                     crs="EPSG:2956").to_file(path)


def line(path):
    gpd.GeoDataFrame({"id": [1]},
                     geometry=[LineString([(0, 0), (1, 1)])],
                     crs="EPSG:2956").to_file(path)


class AOIBatchTests(unittest.TestCase):
    def test_discover_multiple_standard_entities(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            polygon(root / "9L155_Polygon.shp")
            polygon(root / "9L137_Polygon.shp")
            self.assertEqual([x.stem for x in discover_aois(root)],
                             ["9L137_Polygon", "9L155_Polygon"])

    def test_converter_filters_line_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            polygon(root / "9L137_Polygon.shp")
            line(root / "9L137_Lines.shp")
            self.assertEqual([x.stem for x in discover_aois(root, converter=True)],
                             ["9L137_Polygon"])

    def test_converter_allows_unsegmented_substation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            polygon(root / "Substation_01.shp")
            self.assertEqual(len(discover_aois(root, converter=True)), 1)
            with self.assertRaises(FileNotFoundError):
                discover_aois(root, converter=False)

    def test_duplicate_entity_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a").mkdir()
            (root / "b").mkdir()
            polygon(root / "a" / "9L137_Polygon.shp")
            polygon(root / "b" / "9L137_Polygon.shp")
            with self.assertRaisesRegex(ValueError, "Ambiguous AOI entity"):
                discover_aois(root)

    def test_explicit_polygon_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "9L137_Polygon.shp"
            polygon(path)
            self.assertEqual(discover_aois(path), [path])

    def test_explicit_line_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "9L137_Lines.shp"
            line(path)
            with self.assertRaisesRegex(ValueError, "Only polygon AOIs"):
                discover_aois(path, converter=True)


if __name__ == "__main__":
    unittest.main()
