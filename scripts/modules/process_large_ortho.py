"""Memory-bounded standalone RGBI export for large non-segmented AOIs.

Writes one tiled BigTIFF using 1024x1024 windows. No full-AOI raster array
is allocated. Keeps v1.07 NoData=255 rules and background rejection.
"""
from __future__ import annotations

import shutil
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.features import geometry_mask
from rasterio.merge import merge
from rasterio.transform import Affine
from rasterio.windows import Window, bounds as window_bounds
from shapely.geometry import box


def preflight_storage(folder: Path, total_pixels: int, *, ecw: bool):
    folder.mkdir(parents=True, exist_ok=True)
    # Upper-bound estimate for uncompressed uint8 RGBI and optional temporary RGB.
    # LZW often reduces actual size, but we avoid relying on that.
    need = int(total_pixels * (7 if ecw else 4) * 1.15)
    free = shutil.disk_usage(folder).free
    if free < need:
        raise RuntimeError(
            f"Not enough free space for large standalone mosaic: "
            f"{free / (1024**3):.1f} GiB free; allow at least "
            f"{need / (1024**3):.1f} GiB for the TIFF"
            + (" and ECW RGB intermediate" if ecw else "")
            + ". Use a larger output volume or a smaller AOI."
        )


def export_streamed_rgb_i(name, bounds, geom, files, out_tif: Path, crs, res,
                          *, overwrite=False, window_size=1024):
    if out_tif.is_file() and out_tif.stat().st_size > 0 and not overwrite:
        return "exists", 0, 0.0
    rx, ry = map(float, res)
    left, bottom, right, top = map(float, bounds)
    width = int(round((right - left) / rx))
    height = int(round((top - bottom) / ry))
    if width < 1 or height < 1:
        raise ValueError(f"Empty raster bounds for {name}: {bounds}")
    transform = Affine(rx, 0, left, 0, -ry, top)
    tmp = out_tif.with_suffix(".stream.tmp.tif")
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()

    written = 0
    pixel_count = width * height
    completed = False
    try:
        with ExitStack() as stack:
            datasets = [stack.enter_context(rasterio.open(p)) for p in files]
            if any(ds.count < 4 or ds.dtypes[0] != "uint8" for ds in datasets):
                raise ValueError("Standalone TIFF export requires four uint8 RGBI bands")
            profile = datasets[0].profile.copy()
            for key in ("blockxsize", "blockysize", "photometric", "interleave", "nodata"):
                profile.pop(key, None)
            profile.update(
                driver="GTiff", width=width, height=height, count=4,
                dtype="uint8", transform=transform, crs=crs,
                nodata=255, tiled=True, blockxsize=512, blockysize=512,
                compress="lzw", photometric="RGB", BIGTIFF="YES",
            )

            def copy_nonwhite(merged_data, new_data, merged_mask, new_mask, **kwargs):
                mask = np.asarray(new_mask, dtype=bool)
                if mask.ndim == 0:
                    valid_source = np.full(new_data.shape[1:], not bool(mask), dtype=bool)
                elif mask.ndim == 2:
                    valid_source = ~mask
                else:
                    valid_source = ~np.any(mask[:4], axis=0)
                fill = (np.all(new_data[:4] == 254, axis=0)
                        | np.all(new_data[:4] == 255, axis=0))
                copy = (merged_data[4] == 0) & valid_source & ~fill
                np.copyto(merged_data[:4], new_data[:4], where=copy[None, :, :])
                merged_data[4][copy] = 1

            with rasterio.open(tmp, "w", **profile) as dst:
                dst.colorinterp = (ColorInterp.red, ColorInterp.green,
                                   ColorInterp.blue, ColorInterp.undefined)
                dst.update_tags(
                    ATCO_BLOCKNUM=name,
                    ATCO_BACKGROUND_RULE="RGBI background 255,255,255,255; valid 255 adjusted to 254",
                    ATCO_BAND4="NIR",
                    ATCO_EXPORT_MODE="Standalone AOI streamed windows",
                )
                nrows = (height + window_size - 1) // window_size
                ncols = (width + window_size - 1) // window_size
                all_chunks = nrows * ncols
                progress_every = max(1, all_chunks // 100)
                completed_chunks = 0

                for row in range(0, height, window_size):
                    for col in range(0, width, window_size):
                        window = Window(col, row, min(window_size, width-col),
                                        min(window_size, height-row))
                        chunk_bounds = window_bounds(window, transform)
                        chunk_footprint = box(*chunk_bounds)
                        completed_chunks += 1
                        if not geom.intersects(chunk_footprint):
                            continue
                        matching = [ds for ds in datasets
                                    if box(ds.bounds.left, ds.bounds.bottom,
                                           ds.bounds.right, ds.bounds.top).intersects(chunk_footprint)]
                        if not matching:
                            continue
                        array, affine = merge(
                            matching, bounds=chunk_bounds, res=(rx, ry),
                            indexes=[1, 2, 3, 4], output_count=5,
                            masked=False, nodata=0, method=copy_nonwhite,
                            target_aligned_pixels=False,
                        )
                        # Pixel lattice must match the output block exactly.
                        if array.shape[1:] != (int(window.height), int(window.width)):
                            raise RuntimeError(
                                f"Block grid mismatch {array.shape[1:]} vs "
                                f"{(int(window.height), int(window.width))}: {chunk_bounds}")
                        inside = geometry_mask([geom], out_shape=array.shape[1:],
                                               transform=affine, invert=True)
                        valid = inside & (array[4] == 1)
                        if not valid.any():
                            continue
                        pixels = array[:4].copy()
                        pixels[(pixels == 255) & valid[None, :, :]] = 254
                        pixels[:, ~valid] = 255
                        dst.write(pixels, window=window)
                        written += int(valid.sum())
                        if completed_chunks % progress_every == 0:
                            print(f"STREAM TIFF {completed_chunks}/{all_chunks} blocks", flush=True)
                print(f"STREAM TIFF {all_chunks}/{all_chunks} blocks complete", flush=True)

        if written == 0:
            raise ValueError(f"No valid source imagery inside standalone AOI: {name}")
        with rasterio.open(tmp) as check:
            if (check.nodata != 255 or check.count != 4 or check.width != width
                    or check.height != height):
                raise RuntimeError(f"Standalone TIFF metadata validation failed: {tmp}")
        tmp.replace(out_tif)
        completed = True
        out_tif.with_suffix(".prj").write_text(
            rasterio.crs.CRS.from_user_input(crs).to_wkt("WKT1_ESRI"),
            encoding="utf-8",
        )
        return "created (streamed)", written, (100 * written / pixel_count)
    finally:
        if not completed:
            tmp.unlink(missing_ok=True)
