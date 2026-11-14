"""Fast discovery tests: no central registry or GIS data required."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from aoi_batch import discover_aois


class AOIBatchTests(unittest.TestCase):
    def test_discover_multiple_standard_entities_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for filename in ("9L155_Polygon.shp", "9L137_Polygon.shp"):
                (root / filename).touch()
            self.assertEqual([x.stem for x in discover_aois(root)],
                             ["9L137_Polygon", "9L155_Polygon"])

    def test_converter_allows_standalone_polygon_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Substation_01.shp").touch()
            self.assertEqual(len(discover_aois(root, converter=True)), 1)
            with self.assertRaises(FileNotFoundError):
                discover_aois(root, converter=False)

    def test_reject_duplicate_entities_across_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a").mkdir()
            (root / "b").mkdir()
            (root / "a" / "9L137_Polygon.shp").touch()
            (root / "b" / "9L137_Polygon.shp").touch()
            with self.assertRaisesRegex(ValueError, "Ambiguous AOI entity"):
                discover_aois(root)

    def test_explicit_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "9L137_Polygon.shp"
            path.touch()
            self.assertEqual(discover_aois(path), [path])


if __name__ == "__main__":
    unittest.main()
