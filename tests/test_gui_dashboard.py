"""Headless UI smoke tests. Run under xvfb-run on Linux or on Windows desktop."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atco_retiler_gui import RetilerApp, APP_VERSION


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Needs graphical display")
class DashboardSmokeTests(unittest.TestCase):
    def setUp(self):
        self.app = RetilerApp()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_build_dashboard_starts_with_selectable_products(self):
        self.assertEqual(APP_VERSION, "1.07")
        self.assertEqual(set(self.app.product_checks), {"TIFF", "ECW", "LAZ", "zLAS", "Tile Index"})
        self.assertEqual(len(self.app.exec_rows), 5)
        self.assertEqual(len(self.app.selected_products()), 5)
        self.assertIn("Build", self.app.pages)
        self.assertIn("Data Sources", self.app.pages)
        self.assertIn("QA & Reports", self.app.pages)

    def test_select_all_and_dependencies(self):
        self.app.set_all_products(False)
        self.assertEqual(self.app.selected_products(), [])
        self.assertEqual(str(self.app.run_btn["state"]), "disabled")
        self.app.product_vars["ECW"].set(True)
        self.app._enforce_product_dependencies("ECW")
        self.assertTrue(self.app.product_vars["TIFF"].get())
        self.assertEqual(str(self.app.run_btn["state"]), "normal")
        self.app.product_vars["TIFF"].set(False)
        self.app._enforce_product_dependencies("TIFF")
        self.assertFalse(self.app.product_vars["ECW"].get())
        self.app.set_all_products(True)
        self.assertEqual(len(self.app.selected_products()), 5)
        self.app.product_vars["LAZ"].set(False)
        self.app._enforce_product_dependencies("LAZ")
        self.assertFalse(self.app.product_vars["zLAS"].get())

    def test_log_can_expand_without_losing_messages(self):
        self.app.log("UI smoke test", "INFO")
        self.assertEqual(int(self.app.log_text["height"]), 4)
        self.app.toggle_log_details()
        self.assertEqual(int(self.app.log_text["height"]), 12)
        self.assertIn("UI smoke test", self.app.log_text.get("1.0", "end"))
        self.app.toggle_log_details()
        self.assertEqual(int(self.app.log_text["height"]), 4)


if __name__ == "__main__":
    unittest.main()
