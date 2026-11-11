"""Naming conventions for the published ATCO delivery artifacts."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from atco_delivery import client_entity_name, delivery_tile_name, tile_index_shp_path

class DeliveryNamingTests(unittest.TestCase):
    def test_tx_circuit_and_central_sequence(self):
        self.assertEqual(delivery_tile_name("9L137", "9L137_MU_0008"), "9L137_2026_Tiles_v1_0008")
    def test_dx_original_client_naming_is_unchanged(self):
        self.assertEqual(delivery_tile_name("UTIKUMA_OILFIELD", "UTIKUMA_OILFIELD_MU_0001"), "UTIKUMA OILFIELD_2026_Tiles_v1_0001")
    def test_all_output_extensions_share_the_same_stem(self):
        stem = delivery_tile_name("9L137", "9L137_MU_0008")
        self.assertEqual([stem + ext for ext in (".tif", ".ecw", ".laz", ".zlas")],
                         ["9L137_2026_Tiles_v1_0008" + ext for ext in (".tif", ".ecw", ".laz", ".zlas")])

    def test_index_filename(self):
        self.assertEqual(tile_index_shp_path(Path("output/9L137"), "9L137").name, "9L137_2026_Tiles_v1.shp")
