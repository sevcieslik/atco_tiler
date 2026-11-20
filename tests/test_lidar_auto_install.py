"""Test that automatic PDAL setup is safe, optional, and idempotent."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ensure_lidar_support as lidar_setup


class PDALSetupTests(unittest.TestCase):
    @patch.object(lidar_setup, "pdal_ready", return_value=True)
    @patch.object(lidar_setup, "find_conda")
    def test_installed_pdal_requires_no_conda_or_download(self, conda, ready):
        with patch.object(lidar_setup.sys, "executable", str(lidar_setup.ENV / "python.exe")):
            self.assertEqual(lidar_setup.main(), 0)
        conda.assert_not_called()

    @patch.object(lidar_setup, "pdal_ready", return_value=False)
    @patch.object(lidar_setup, "find_conda", side_effect=FileNotFoundError("No Conda"))
    def test_missing_conda_gives_explicit_error(self, conda, ready):
        with patch.object(lidar_setup.sys, "executable", str(lidar_setup.ENV / "python.exe")):
            with self.assertRaisesRegex(FileNotFoundError, "No Conda"):
                lidar_setup.main()


if __name__ == "__main__":
    unittest.main()
