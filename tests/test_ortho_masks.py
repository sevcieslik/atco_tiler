"""Regression tests for ATCO RGBI masks and Global Mapper ECW scripts.

Run from repository root:
    python -m unittest discover -s tests -v

These tests do not run Global Mapper itself. A Windows GIS visual/round-trip
check of the produced ECW is still required before a production rollout.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, MaskFlags
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from process_orthos import export_tile, build_gm_rgba_input, make_gm_script


class OrthoMaskTests(unittest.TestCase):
    def test_rgb_nir_values_and_mask_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.tif"
            # The entire top row is no coverage. The first valid pixel has
            # legitimate 255 values in multiple data bands.
            rgbn = np.full((4, 16, 16), 60, dtype=np.uint8)
            rgbn[:, 1, 0] = [255, 120, 255, 240]
            valid = np.ones((16, 16), dtype=np.uint8) * 255
            valid[0, :] = 0
            profile = dict(driver="GTiff",width=16,height=16,count=4,
                           dtype="uint8",crs="EPSG:3857",
                           transform=from_origin(0,16,1,1),
                           nodata=None)
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                with rasterio.open(source, "w", **profile) as dst:
                    dst.write(rgbn)
                    dst.colorinterp = (ColorInterp.red, ColorInterp.green,
                                       ColorInterp.blue, ColorInterp.undefined)
                    dst.write_mask(valid)
            out = root / "tile.tif"
            status, pixels, _pct = export_tile(
                "TILE_0001", (0,0,16,16), box(0,0,16,16),
                [source], out, 4, "EPSG:3857", (1,1)
            )
            self.assertTrue(status.startswith("created"), status)
            with rasterio.open(out) as ds:
                self.assertIsNone(ds.nodata)
                self.assertEqual(ds.count, 4)
                self.assertEqual(ds.colorinterp[3], ColorInterp.undefined)
                self.assertTrue(ds.dataset_mask()[0,:].max() == 0)
                np.testing.assert_array_equal(ds.read()[:, 1, 0], rgbn[:,1,0])
                self.assertEqual(pixels, 16*15)
                self.assertIn(MaskFlags.per_dataset, ds.mask_flag_enums[0])
            self.assertFalse(Path(str(out)+".msk").exists())
            rgba = root / "rgba.tif"
            build_gm_rgba_input(out, rgba)
            with rasterio.open(rgba) as ds:
                self.assertEqual(ds.colorinterp[3], ColorInterp.alpha)
                np.testing.assert_array_equal(ds.read(4)[0,:], np.zeros(16, dtype=np.uint8))
                self.assertEqual(ds.read(4)[1,0], 255)
                np.testing.assert_array_equal(ds.read([1,2,3])[:,1,0], rgbn[:3,1,0])

    def test_gm_script_keeps_alpha_and_uses_ecw_v3_transparency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            script = root / "run.gms"
            make_gm_script([(root / "tile.tif", root / "tile_GM_RGBA.tif")],
                           root, script)
            text = script.read_text(encoding="utf-8")
            self.assertIn("BG_TRANSPARENT=YES", text)
            self.assertIn("ECW_FORMAT_VERSION=3", text)
            self.assertNotIn("BAND_SETUP=", text)
            self.assertNotIn("USE_BAND4_AS_ALPHA=", text)


if __name__ == "__main__":
    unittest.main()
