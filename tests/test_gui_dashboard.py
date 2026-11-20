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
        self.assertFalse(hasattr(self.app, "process_mode"))
        self.assertEqual(len(self.app.selected_products()), 5)
        self.assertIn("Build", self.app.pages)
        self.assertIn("Data Sources", self.app.pages)
        self.assertIn("QA & Reports", self.app.pages)
        self.assertIn("How to Start", self.app.pages)
        self.assertTrue(self.app.commit_badge["text"].startswith("Commit"))
        self.assertEqual(str(self.app.run_btn["state"]), "disabled")

    def test_processing_requires_local_validation(self):
        self.assertFalse(self.app.input_validated)
        self.assertEqual(self.app.aoi_list.size(), 0)
        self.assertEqual(self.app.run_btn["text"], "▶  Process")
        self.assertEqual(self.app.validate_btn["text"], "✓  Validate Input")
        self.assertEqual(self.app.run_btn["state"], "disabled")

    def test_empty_cloud_with_laz_selected_fails_fast_and_recovers(self):
        """No PDAL install or background coverage worker for empty LiDAR."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "point_cloud").mkdir()
            (root / "aoi").mkdir()
            self.app.mapped = {
                "aoi": root / "aoi", "orthos": root,
                "point_cloud": root / "point_cloud",
            }
            self.app.product_vars["LAZ"].set(True)
            self.app.product_vars["zLAS"].set(True)
            with (patch.object(gui.threading, "Thread") as worker,
                  patch.object(gui.messagebox, "showwarning") as warning):
                self.app.validate_setup()
                self.assertTrue(warning.called)
                worker.assert_not_called()
            self.assertFalse(self.app.validating)
            self.assertFalse(self.app.input_validated)
            self.assertEqual(self.app.validate_btn["state"], "normal")
            self.assertEqual(self.app.input_badge_states["point_cloud"], "error")
            # Correct selection is immediately possible without restarting.
            self.app.product_vars["LAZ"].set(False)
            self.app._enforce_product_dependencies("LAZ")
            self.app.validate_setup()
            self.assertTrue(self.app.validating)
            self.assertEqual(self.app.validate_btn["state"], "disabled")

    def test_excluded_area_does_not_block_circuit_or_tile_index(self):
        """UAT: 9L137 eligible, Battle River excluded on imagery."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
        import process_orthos as ortho
        eligible = [{
            "kind": "circuit", "entity": "9L137", "revision": "R01",
            "has_segments": True, "path": Path("input/aoi/9L137_Polygon.shp"),
        }]
        excluded = [("BATTLE_RIVER_Substation", "insufficient imagery footprint coverage")]
        self.app._validation_products = ("TIFF", "ECW", "LAZ", "zLAS")
        with (patch.object(ortho, "load_aoi_revision", return_value={"revision_code": "R01"}),
              patch.object(ortho, "load_segments", return_value=(Path("segments.shp"), {
                  "ImageryName": ["9L137_0001_2026_5cm_RGBN_v1"],
                  "PointCloudName": ["9L137_0001_v1"],
              })),
              patch.object(gui.messagebox, "showinfo")):
            self.app._apply_eligible_entities(eligible, excluded)
        self.assertTrue(self.app.input_validated)
        self.assertEqual(self.app.aoi_list.size(), 1)
        self.assertEqual(len(self.app.entity_records), 1)
        self.assertEqual(self.app.entity_records[0]["entity"], "9L137")
        self.assertEqual(self.app.run_btn["state"], "normal")
        self.assertEqual(self.app.product_checks["Tile Index"]["state"], "normal")
        for checked in (False, True):
            self.app.product_vars["Tile Index"].set(checked)
            self.app._enforce_product_dependencies("Tile Index")
            self.assertTrue(self.app.input_validated)
            self.assertEqual(self.app.aoi_list.size(), 1)
            self.assertEqual(self.app.context, ("9L137", "R01"))
            self.assertEqual(self.app.run_btn["state"], "normal")
        self.assertIn("Tile Index", self.app.selected_products())

    def test_select_all_and_dependencies(self):
        self.app.set_all_products(False)
        self.assertEqual(self.app.selected_products(), [])
        self.assertEqual(str(self.app.run_btn["state"]), "disabled")
        self.app.product_vars["ECW"].set(True)
        self.app._enforce_product_dependencies("ECW")
        self.assertTrue(self.app.product_vars["TIFF"].get())
        self.assertEqual(str(self.app.run_btn["state"]), "disabled")
        self.app.product_vars["TIFF"].set(False)
        self.app._enforce_product_dependencies("TIFF")
        self.assertFalse(self.app.product_vars["ECW"].get())
        self.app.set_all_products(True)
        # Index is only available when at least one selected entity has segments.
        self.assertEqual(len(self.app.selected_products()), 4)
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

    def test_validate_control_is_beside_sources_and_badges_are_gated(self):
        self.app.refresh_mapping()
        self.assertEqual(self.app.validate_btn.winfo_manager(), "grid")
        self.assertEqual(int(self.app.validate_btn.grid_info()["row"]), 2)
        self.assertEqual(int(self.app.validate_btn.grid_info()["column"]), 3)
        self.assertEqual(
            self.app.input_badge_states,
            {"aoi": "pending", "orthos": "pending", "point_cloud": "pending"})
        self.assertTrue(all(
            child["text"].startswith("○")
            for child in self.app.mapping_chip_frame.winfo_children()))
        self.app._set_input_badges({
            "aoi": "validated", "orthos": "validated",
            "point_cloud": "validated"})
        self.assertTrue(all(
            child["text"].startswith("●")
            for child in self.app.mapping_chip_frame.winfo_children()))
        self.app._invalidate_input_validation()
        self.assertTrue(all(
            child["text"].startswith("○")
            for child in self.app.mapping_chip_frame.winfo_children()))

    def test_readiness_lights_are_vertical(self):
        self.app.refresh_mapping()
        chips = self.app.mapping_chip_frame.winfo_children()
        self.assertEqual(len(chips), 3)
        self.assertTrue(all(c.pack_info()["side"] == "top" for c in chips))


if __name__ == "__main__":
    unittest.main()
