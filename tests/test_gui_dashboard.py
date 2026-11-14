"""Headless UI smoke tests. Run under xvfb-run on Linux or on Windows desktop."""
import os
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import atco_retiler_gui as gui
from atco_retiler_gui import RetilerApp, APP_VERSION


class GitBadgeTests(unittest.TestCase):
    def test_git_head_without_executable_on_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref_path = root / ".git" / "refs" / "heads" / "feature"
            ref_path.parent.mkdir(parents=True)
            ref_path.write_text("123456789abcdef0123456789abcdef012345678\n")
            (root / ".git" / "HEAD").write_text("ref: refs/heads/feature\n")
            with patch.object(gui, "PACKAGE_ROOT", root):
                with patch.object(gui.subprocess, "run", side_effect=FileNotFoundError):
                    self.assertEqual(gui.current_commit(), "1234567")

    def test_missing_git_metadata_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gui, "PACKAGE_ROOT", Path(directory)):
                self.assertEqual(gui.current_commit(), "unknown")


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Needs graphical display")
class DashboardSmokeTests(unittest.TestCase):
    def setUp(self):
        self.app = RetilerApp()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_build_dashboard_starts_with_selectable_products(self):
        self.assertEqual(APP_VERSION, "1.08-dev")
        self.assertEqual(set(self.app.product_checks), {"TIFF", "ECW", "LAZ", "zLAS", "Tile Index"})
        self.assertEqual(len(self.app.exec_rows), 5)
        self.assertTrue(hasattr(self.app, "aoi_list"))
        self.assertEqual(self.app.process_mode.get(), "standard")
        self.assertEqual(len(self.app.selected_products()), 5)
        self.assertIn("Build", self.app.pages)
        self.assertIn("Data Sources", self.app.pages)
        self.assertIn("QA & Reports", self.app.pages)
        self.assertIn("How to Start", self.app.pages)
        self.assertTrue(self.app.commit_badge["text"].startswith("Commit"))
        self.assertEqual(str(self.app.run_btn["state"]), "normal")

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
        self.assertFalse(self.app.log_text.winfo_manager())
        self.app.toggle_log_details()
        self.assertEqual(self.app.log_text.winfo_manager(), "pack")
        self.assertIn("UI smoke test", self.app.log_text.get("1.0", "end"))
        self.assertEqual(self.app.log_toggle_btn["text"], "Hide Details ▲")
        self.app.toggle_log_details()
        self.assertFalse(self.app.log_text.winfo_manager())
        self.assertEqual(self.app.log_toggle_btn["text"], "Show Details ▼")

    def test_round_buttons_keep_processing_interface(self):
        self.app.pause_btn.configure(state="normal", text="▶  Resume")
        self.assertEqual(self.app.pause_btn["state"], "normal")
        self.assertEqual(self.app.pause_btn["text"], "▶  Resume")
        self.app.pause_btn.configure(state="disabled")
        self.assertEqual(self.app.pause_btn["state"], "disabled")

    def test_readiness_lights_are_vertical(self):
        self.app.refresh_mapping()
        chips = self.app.mapping_chip_frame.winfo_children()
        self.assertEqual(len(chips), 3)
        self.assertTrue(all(c.pack_info()["side"] == "top" for c in chips))


if __name__ == "__main__":
    unittest.main()
