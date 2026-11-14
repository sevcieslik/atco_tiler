"""Memory-bounded whole-area TIFF streaming regression (no large fixture)."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from process_large_ortho import export_streamed_rgb_i


class StreamedTIFFTests(unittest.TestCase):
    def test_streamed_output_keeps_explicit_white_nodata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.tif"
            arr = np.full((4, 8, 8), 100, dtype="uint8")
            arr[0, 2, 2] = 255
            arr[:, 0, :] = 254
            with rasterio.open(source, "w", driver="GTiff", height=8, width=8,
                               count=4, dtype="uint8", crs="EPSG:2956",
                               transform=from_origin(0, 8, 1, 1)) as dst:
                dst.write(arr)
                dst.colorinterp = (ColorInterp.red, ColorInterp.green,
                                   ColorInterp.blue, ColorInterp.undefined)
            output = root / "TIFF_area" / "test.tif"
            status, nvalid, _ = export_streamed_rgb_i(
                "test", (0, 0, 8, 8), box(1, 1, 7, 7),
                [source], output, "EPSG:2956", (1, 1), window_size=3)
            self.assertIn("created", status)
            self.assertGreater(nvalid, 0)
            with rasterio.open(output) as ds:
                self.assertEqual(ds.nodata, 255)
                self.assertEqual(ds.count, 4)
                self.assertEqual(ds.width, 8)
                self.assertEqual(ds.height, 8)
                self.assertEqual(ds.read()[:, 0, 0].tolist(), [255] * 4)
                self.assertEqual(ds.read(1)[2, 2], 254)
                self.assertEqual(ds.read(2)[4, 4], 100)
                self.assertEqual(ds.read()[:, 7, 7].tolist(), [255] * 4)


if __name__ == "__main__":
    unittest.main()
