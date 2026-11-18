"""Optional PyArrow dependency is installed only for TX trajectory attribution."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ensure_trajectory_support as setup


class TrajectorySupportTests(unittest.TestCase):
    def test_existing_pyarrow_skips_conda(self):
        with (patch.object(setup.sys, "executable", str(setup.ENV / "python.exe")),
              patch.object(setup.INDEX.__class__, "is_file", return_value=True),
              patch.object(setup, "pyarrow_ready", return_value=True),
              patch.object(setup, "find_conda") as conda):
            self.assertEqual(setup.main(), 0)
            conda.assert_not_called()

    def test_missing_index_skips_install(self):
        with (patch.object(setup.sys, "executable", str(setup.ENV / "python.exe")),
              patch.object(setup.INDEX.__class__, "is_file", return_value=False),
              patch.object(setup, "find_conda") as conda):
            self.assertEqual(setup.main(), 0)
            conda.assert_not_called()

    def test_missing_pyarrow_with_index_requires_conda(self):
        with (patch.object(setup.sys, "executable", str(setup.ENV / "python.exe")),
              patch.object(setup.INDEX.__class__, "is_file", return_value=True),
              patch.object(setup, "pyarrow_ready", return_value=False),
              patch.object(setup, "find_conda", side_effect=FileNotFoundError("No Conda"))):
            with self.assertRaisesRegex(FileNotFoundError, "No Conda"):
                setup.main()


if __name__ == "__main__":
    unittest.main()
