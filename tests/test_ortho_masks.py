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
from process_orthos import export_tile, build_gm_rgb_input, make_gm_script


class OrthoMaskTests(unittest.TestCase):
    def test_rgb_nir_and_explicit_nodata_255_for_arcgis(self):
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
                self.assertEqual(ds.nodata, 255)
                self.assertEqual(ds.count, 4)
                self.assertEqual(ds.colorinterp[3], ColorInterp.undefined)
                self.assertTrue(ds.dataset_mask()[0,:].max() == 0)
                np.testing.assert_array_equal(ds.read()[:, 1, 0], np.array([254,120,254,240], dtype=np.uint8))
                self.assertEqual(pixels, 16*15)
                self.assertIn(MaskFlags.nodata, ds.mask_flag_enums[0])
            self.assertFalse(Path(str(out)+".msk").exists())
            rgb = root / "gm_rgb3_white.tif"
            build_gm_rgb_input(out, rgb)
            with rasterio.open(rgb) as ds:
                self.assertEqual(ds.count, 3)
                self.assertIsNone(ds.nodata)
                self.assertEqual(ds.colorinterp, (ColorInterp.red, ColorInterp.green, ColorInterp.blue))
                np.testing.assert_array_equal(ds.read()[:,0,:], np.full((3,16),255,dtype=np.uint8))
                np.testing.assert_array_equal(ds.read()[:,1,0], np.array([254,120,254], dtype=np.uint8))

    def test_tiff_explicit_nodata_and_white_fill(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            source = path / "source.tif"
            pixels = np.full((4,16,16), 90, dtype=np.uint8)
            pixels[:,0,:] = 254
            pixels[:,1,2] = [255,155,100,255]
            with rasterio.open(source, "w", driver="GTiff", width=16,
                               height=16, count=4, dtype="uint8",
                               crs="EPSG:3857",transform=from_origin(0,16,1,1)) as ds:
                ds.write(pixels)
            output = path / "output.tif"
            export_tile("TEST",(0,0,16,16),box(0,0,16,16),
                        [source],output,4,"EPSG:3857",(1,1))
            with rasterio.open(output) as ds:
                self.assertEqual(ds.nodata,255)
                self.assertTrue(np.all(ds.read()[:,0,:] == 255))
                self.assertTrue(np.all(ds.dataset_mask()[0,:] == 0))
                np.testing.assert_array_equal(ds.read()[:,1,2],
                                              [254,155,100,254])
                self.assertNotIn(MaskFlags.per_dataset, ds.mask_flag_enums[0])

    def test_254_background_is_ignored_before_overlapping_mosaic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile = dict(driver="GTiff", width=16, height=16, count=4,
                           dtype="uint8", crs="EPSG:3857",
                           transform=from_origin(0, 16, 1, 1), nodata=None)
            first = np.full((4, 16, 16), 70, dtype=np.uint8)
            first[:, 0, :] = 254  # visually white unmasked edge fringe
            first[:, 1, 1] = [255, 200, 255, 150]  # real bright pixel
            second = np.full((4, 16, 16), 90, dtype=np.uint8)
            for name, data in (("first.tif", first), ("second.tif", second)):
                with rasterio.open(root / name, "w", **profile) as dst:
                    dst.write(data)
            out = root / "tile.tif"
            export_tile("TEST", (0, 0, 16, 16), box(0,0,16,16),
                        [root / "first.tif", root / "second.tif"],
                        out, 4, "EPSG:3857", (1,1))
            with rasterio.open(out) as ds:
                np.testing.assert_array_equal(ds.read()[:,0,0], second[:,0,0])
                np.testing.assert_array_equal(ds.read()[:,1,1], np.array([254,200,254,150],dtype=np.uint8))
                self.assertEqual(ds.dataset_mask()[0,0], 255)

    def test_254_background_without_fallback_becomes_transparent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = np.full((4,16,16), 80, dtype=np.uint8)
            data[:,0,:] = 254
            source = root / "source.tif"
            with rasterio.open(source, "w", driver="GTiff", width=16, height=16,
                               count=4, dtype="uint8", crs="EPSG:3857",
                               transform=from_origin(0,16,1,1)) as ds:
                ds.write(data)
            out = root / "tile.tif"
            export_tile("TEST", (0,0,16,16), box(0,0,16,16),
                        [source], out, 4, "EPSG:3857", (1,1))
            with rasterio.open(out) as ds:
                self.assertTrue(np.all(ds.dataset_mask()[0,:] == 0))
                self.assertTrue(np.all(ds.dataset_mask()[1,:] == 255))

    def test_scalar_rasterio_mask_on_unmasked_source(self):
        # Reproduce the failure observed on 9L137:
        # IndexError: invalid index to scalar variable.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "source.tif"
            data = np.full((4, 16, 16), 100, dtype=np.uint8)
            data[:, 0, 0] = 254
            with rasterio.open(src, "w", driver="GTiff", width=16, height=16,
                               count=4, dtype="uint8", crs="EPSG:3857",
                               transform=from_origin(0, 16, 1, 1)) as dst:
                dst.write(data)
                # Do not let GDAL infer band 4 as an alpha channel.
                dst.colorinterp = (ColorInterp.red, ColorInterp.green,
                                   ColorInterp.blue, ColorInterp.undefined)
            out = root / "out.tif"
            export_tile("TEST", (0,0,16,16), box(0,0,16,16), [src],
                        out, 4, "EPSG:3857", (1,1))
            with rasterio.open(out) as ds:
                self.assertEqual(ds.dataset_mask()[0,0], 0)
                self.assertEqual(ds.dataset_mask()[0,1], 255)

    def test_255_white_fill_is_excluded_or_replaced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile = dict(driver="GTiff", width=16, height=16, count=4,
                           dtype="uint8", crs="EPSG:3857",
                           transform=from_origin(0,16,1,1))
            a = np.full((4,16,16), 80, dtype=np.uint8)
            b = np.full((4,16,16), 50, dtype=np.uint8)
            a[:,0,:] = 255
            # Legitimate bright sample with only SOME bands set to 255.
            a[:,1,0] = [255, 120, 255, 120]
            for name, data in (("a.tif", a), ("b.tif", b)):
                with rasterio.open(root / name, "w", **profile) as dst:
                    dst.write(data)
                    dst.colorinterp = (ColorInterp.red, ColorInterp.green,
                                       ColorInterp.blue, ColorInterp.undefined)
            out = root / "merged.tif"
            export_tile("TEST",(0,0,16,16),box(0,0,16,16),
                        [root/"a.tif",root/"b.tif"],out,4,"EPSG:3857",(1,1))
            with rasterio.open(out) as ds:
                np.testing.assert_array_equal(ds.read()[:,0,0], b[:,0,0])
                np.testing.assert_array_equal(ds.read()[:,1,0], np.array([254,120,254,120],dtype=np.uint8))
                self.assertEqual(ds.dataset_mask()[0,0], 255)
            alone = root / "alone.tif"
            export_tile("ALONE",(0,0,16,16),box(0,0,16,16),
                        [root/"a.tif"],alone,4,"EPSG:3857",(1,1))
            with rasterio.open(alone) as ds:
                self.assertEqual(ds.dataset_mask()[0,0], 0)

    def test_rgb_intermediate_preserves_valid_near_white_and_no_alpha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "rgbn.tif"
            pixels = np.full((4,16,16), 110, dtype=np.uint8)
            pixels[:,0,0] = [254,255,254,255]
            pixels[:,1,1] = [255,254,255,17]
            mask = np.full((16,16),255,dtype=np.uint8)
            mask[0,0] = 0
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                with rasterio.open(src,"w",driver="GTiff",width=16,height=16,
                                   count=4,dtype="uint8",crs="EPSG:3857",
                                   transform=from_origin(0,16,1,1),nodata=None) as ds:
                    ds.write(pixels)
                    ds.write_mask(mask)
            dest = root / "intermediate.tif"
            build_gm_rgb_input(src,dest)
            with rasterio.open(dest) as ds:
                self.assertEqual(ds.count,3)
                self.assertTrue(np.all(ds.read()[:,0,0]==255))
                np.testing.assert_array_equal(ds.read()[:,1,1],pixels[:3,1,1])
                self.assertNotIn(ColorInterp.alpha,ds.colorinterp)

    def test_gm_script_uses_three_band_rgb_white_background(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            script = root / "run.gms"
            make_gm_script([(root / "tile.tif", root / "tile_GM_RGB3_WHITE.tif")],
                           root, script)
            text = script.read_text(encoding="utf-8")
            self.assertIn("BG_TRANSPARENT=NO", text)
            self.assertIn("ECW_FORMAT_VERSION=3", text)
            self.assertNotIn("BAND_SETUP=", text)
            self.assertNotIn("USE_BAND4_AS_ALPHA=", text)


if __name__ == "__main__":
    unittest.main()
