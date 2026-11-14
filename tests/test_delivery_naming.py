"""ATCO v1.08 client delivery naming contract (per product)."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from atco_delivery import (client_entity_name, delivery_imagery_name,
                           delivery_point_cloud_name, product_folder, tile_index_shp_path,
                           tile_sequence)

class DeliveryNamingTests(unittest.TestCase):
    def test_tx_circuit_and_sequence(self):
        self.assertEqual(tile_sequence("9L137_MU_0008"), "0008")
        self.assertEqual(delivery_point_cloud_name("9L137", "9L137_MU_0008"), "9L137_0008_v1")
        self.assertEqual(delivery_imagery_name("9L137", "9L137_MU_0008"),
                         "9L137_0008_2026_5cm_RGBN_v1")

    def test_dx_client_name_is_preserved(self):
        self.assertEqual(client_entity_name("UTIKUMA_OILFIELD"), "UTIKUMA OILFIELD")
        self.assertEqual(delivery_point_cloud_name("UTIKUMA_OILFIELD", "UTIKUMA_OILFIELD_MU_0001"),
                         "UTIKUMA OILFIELD_0001_v1")

    def test_product_stems_are_separate(self):
        self.assertNotEqual(delivery_imagery_name("9L137", "9L137_MU_0008"),
                            delivery_point_cloud_name("9L137", "9L137_MU_0008"))

    def test_product_folders_are_mode_isolated(self):
        for product in ("TIFF", "ECW", "LAZ", "zLAS"):
            self.assertEqual(product_folder(product), f"{product}_segments")
            self.assertEqual(product_folder(product, converter=True), f"{product}_area")
            self.assertNotEqual(product_folder(product),
                                product_folder(product, converter=True))

    def test_index_filename(self):
        self.assertEqual(tile_index_shp_path(Path("output/9L137"), "9L137").name,
                         "9L137_2026_Tiles_v1.shp")
