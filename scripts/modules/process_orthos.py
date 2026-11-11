#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import ColorInterp
from rasterio.features import geometry_mask
from rasterio.merge import merge
from shapely import wkb
from shapely.geometry import box
from shapely.ops import unary_union

from atco_delivery import enrich_segments, output_base

CENTRAL_ROOT = Path(r"W:\APPS\PIT\ATCO")
DB_PATH = CENTRAL_ROOT / "database" / "atco_aoi_master.sqlite"
SEGMENTS_ROOT = CENTRAL_ROOT / "segments" / "circuit"
DEFAULT_OUTPUT_ROOT = Path(r"D:\ATCO\output")
CONFIG_FILE = Path(__file__).resolve().with_name("ortho_processor_config.json")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def progress_line(label, done, total, *, workers=None, extra=None, width=24):
    total = max(int(total), 1)
    done = max(0, min(int(done), total))
    filled = int(round(width * done / total))
    bar = "#" * filled + "-" * (width - filled)
    suffix = f" | workers={workers}" if workers else ""
    if extra:
        suffix += f" | {extra}"
    print(f"\r{label:<8} [{bar}] {done:>3}/{total:<3}{suffix}", end="", flush=True)
    if done >= total:
        print("", flush=True)


def norm_path_text(p: Path) -> str:
    return str(p.resolve()) if p.exists() and not str(p).startswith("W:") else str(p)


def find_polygon_shp(path: Path):
    path = Path(path)
    if path.is_file():
        if path.suffix.lower() != '.shp' or not path.stem.lower().endswith('_polygon'):
            raise ValueError(f'Expected *_Polygon.shp, received: {path}')
        return path
    if not path.is_dir():
        raise FileNotFoundError(f'AOI path not found: {path}')
    found = sorted(p for p in path.rglob('*.shp') if p.stem.lower().endswith('_polygon'))
    if not found:
        raise FileNotFoundError(f'No *_Polygon.shp below {path}')
    if len(found) > 1:
        names = '\n'.join(f'  - {p}' for p in found[:20])
        raise ValueError(f'Multiple Polygon SHPs found. Point to one entity/version folder or the exact SHP:\n{names}')
    return found[0]


def circuit_from_polygon(path: Path):
    suffix = '_polygon'
    stem = path.stem
    if not stem.lower().endswith(suffix):
        raise ValueError(path)
    return stem[:-len(suffix)]


def db_connect_readonly(path: Path):
    # Do not use SQLite URI mode on W:; Windows can resolve the mapped drive to a UNC
    # authority that SQLite rejects. PRAGMA query_only protects the central registry.
    con = sqlite3.connect(str(path))
    con.execute('PRAGMA query_only=ON')
    return con


def load_aoi_revision(circuit: str, selected_polygon: Path):
    if not DB_PATH.is_file():
        raise FileNotFoundError(f'Central AOI database missing: {DB_PATH}')
    selected = gpd.read_file(selected_polygon)
    if selected.empty or not selected.crs:
        raise ValueError(f'AOI polygon is empty or has no CRS: {selected_polygon}')
    selected_geom = unary_union([g for g in selected.geometry if g is not None and not g.is_empty])
    if selected_geom.is_empty:
        raise ValueError(f'No valid AOI polygon geometry: {selected_polygon}')

    with db_connect_readonly(DB_PATH) as con:
        entity = con.execute(
            "SELECT current_revision FROM entities WHERE entity_kind='circuit' AND entity_id=?",
            (circuit,),
        ).fetchone()
        if not entity:
            raise ValueError(f'{circuit} is not registered in the central AOI database')
        rows = con.execute(
            """SELECT id, revision_num, revision_code, source_version, crs_wkt, polygon_wkb, source_folder
               FROM revisions WHERE entity_kind='circuit' AND entity_id=? ORDER BY revision_num""",
            (circuit,),
        ).fetchall()
    if not rows:
        raise ValueError(f'No registered AOI revisions for {circuit}')

    matches = []
    selected_crs = CRS.from_user_input(selected.crs)
    for row in rows:
        rid, revision_num, revision_code, source_version, crs_wkt, polygon_blob, source_folder = row
        target_crs = CRS.from_wkt(crs_wkt)
        test_geom = selected_geom
        if not selected_crs.equals(target_crs):
            test_geom = gpd.GeoSeries([selected_geom], crs=selected_crs).to_crs(target_crs).iloc[0]
        central_geom = wkb.loads(polygon_blob)
        diff = test_geom.symmetric_difference(central_geom).area
        # 0.05 m2 protects against harmless serialization precision while still detecting edits.
        if diff <= 0.05:
            matches.append((row, diff, target_crs, central_geom))

    current_revision_id = int(entity[0])
    if not matches:
        current = next((r for r in rows if int(r[0]) == current_revision_id), rows[-1])
        raise RuntimeError(
            f'AOI VERSION MISMATCH: selected AOI does not match central {current[2]} for {circuit}.\n'
            'If this is a production AOI update, publish it through ATCO Central AOI Registry first; '
            'then regenerate/publish the segment revision before processing imagery.'
        )
    row, diff, target_crs, central_geom = matches[-1]
    if int(row[0]) != current_revision_id:
        current = next((r for r in rows if int(r[0]) == current_revision_id), rows[-1])
        raise RuntimeError(
            f'OUTDATED AOI: selected AOI matches {row[2]}, but current central revision is {current[2]}. '
            'Use the current production AOI.'
        )
    return {
        'revision_id': row[0],
        'revision_num': int(row[1]),
        'revision_code': row[2],
        'source_version': row[3],
        'crs': target_crs,
        'geometry': central_geom,
        'source_folder': row[6],
        'difference_m2': diff,
    }


def load_segments(circuit: str, revision_code: str):
    folder = SEGMENTS_ROOT / circuit / revision_code
    shp = folder / f'{circuit}_AOI_Segments.shp'
    if not shp.is_file():
        raise FileNotFoundError(
            f'Published segment set missing for {circuit} {revision_code}: {shp}\n'
            'Publish AOI Segments before processing imagery.'
        )
    seg = gpd.read_file(shp)
    if seg.empty or 'BlockNum' not in seg.columns or not seg.crs:
        raise ValueError(f'Invalid central segment index: {shp}')
    if seg['BlockNum'].isna().any() or seg['BlockNum'].duplicated().any():
        raise ValueError(f'Missing/duplicate BlockNum in {shp}')
    if not seg.geometry.is_valid.all():
        raise ValueError(f'Invalid geometry in {shp}')
    seg = enrich_segments(seg, circuit)
    return shp, seg


def source_tiffs(folder: Path):
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f'Ortho source folder not found: {folder}')
    files = sorted(p for p in folder.rglob('*') if p.suffix.lower() in ('.tif', '.tiff'))
    if not files:
        raise FileNotFoundError(f'No TIF/TIFF files below {folder}')
    return files


def raster_inventory(files, expected_crs: CRS):
    inv = []
    first_transform = None
    first_res = None
    max_data_bands = 3
    for i, p in enumerate(files, 1):
        with rasterio.open(p) as ds:
            if not ds.crs:
                raise ValueError(f'Missing CRS in source TIFF: {p}')
            src_crs = CRS.from_user_input(ds.crs)
            if not src_crs.equals(expected_crs):
                raise ValueError(
                    f'Source TIFF CRS mismatch: {p.name} = {src_crs.to_string()}, '
                    f'circuit = {expected_crs.to_string()}. v1 requires source imagery in circuit CRS.'
                )
            rx, ry = abs(ds.transform.a), abs(ds.transform.e)
            if rx <= 0 or ry <= 0:
                raise ValueError(f'Invalid pixel size: {p}')
            if first_res is None:
                first_res = (rx, ry)
                first_transform = ds.transform
            else:
                if abs(rx-first_res[0]) > 1e-9 or abs(ry-first_res[1]) > 1e-9:
                    raise ValueError(
                        f'Mixed TIFF resolutions are not accepted in v1: {p.name} has {rx},{ry}; '
                        f'expected {first_res[0]},{first_res[1]}'
                    )
                # Require common pixel lattice. Tiny floating error is okay.
                dx = (ds.transform.c - first_transform.c) / first_res[0]
                dy = (first_transform.f - ds.transform.f) / first_res[1]
                if abs(dx-round(dx)) > 1e-5 or abs(dy-round(dy)) > 1e-5:
                    raise ValueError(f'Source TIFF is not aligned to the common pixel grid: {p.name}')
            data_bands = ds.count
            if ds.count >= 4 and len(ds.colorinterp) >= 4 and ds.colorinterp[3] == ColorInterp.alpha:
                data_bands = 3
            max_data_bands = max(max_data_bands, min(data_bands, 4))
            inv.append({
                'path': str(p),
                'name': p.name,
                'bands': ds.count,
                'data_bands': min(data_bands, 4),
                'width': ds.width,
                'height': ds.height,
                'res_x': rx,
                'res_y': ry,
                'left': ds.bounds.left,
                'bottom': ds.bounds.bottom,
                'right': ds.bounds.right,
                'top': ds.bounds.top,
            })
        if i % 100 == 0:
            print(f'  Indexed TIFFs: {i}', flush=True)
    # Preserve NIR when present. If some files are RGB and others RGBI, fail instead of silently inventing band 4.
    counts = {r['data_bands'] for r in inv}
    if len(counts) != 1:
        raise ValueError(f'Mixed data-band counts in source imagery: {sorted(counts)}. Normalize imagery before processing.')
    band_count = counts.pop()
    if band_count not in (3, 4):
        raise ValueError(f'Expected RGB or RGBI source imagery; detected {band_count} data bands')
    return inv, first_transform, first_res, band_count


def align_bounds(bounds, transform, res):
    minx, miny, maxx, maxy = bounds
    rx, ry = res
    ox, oy = transform.c, transform.f
    left = ox + math.floor((minx - ox) / rx + 1e-10) * rx
    right = ox + math.ceil((maxx - ox) / rx - 1e-10) * rx
    top = oy - math.floor((oy - maxy) / ry + 1e-10) * ry
    bottom = oy - math.ceil((oy - miny) / ry - 1e-10) * ry
    # Stabilize floating output for shapefile/raster bounds.
    return tuple(round(v, 8) for v in (left, bottom, right, top))


def remove_shapefile_family(shp_path: Path):
    stem = shp_path.with_suffix('')
    for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.shp.xml'):
        p = Path(str(stem) + ext)
        if p.exists():
            p.unlink()



def build_ortho_index(seg: gpd.GeoDataFrame, transform, res, out_shp: Path, overwrite=False):
    records = []
    for _, row in seg.iterrows():
        b = align_bounds(row.geometry.bounds, transform, res)
        records.append({
            'BlockNum': str(row.DeliveryName),
            'CENTRAL_ID': str(row.CentralBlock),
            'CIRCUIT': str(row.CIRCUIT),
            'AOI_REV': str(row.AOI_REV),
            'SEG_REV': str(row.AOI_REV),
            'geometry': box(*b),
        })
    out = gpd.GeoDataFrame(records, geometry='geometry', crs=seg.crs)
    if out['BlockNum'].duplicated().any():
        raise ValueError('Duplicate BlockNum generated in ortho index')
    out_shp.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and out_shp.exists():
        remove_shapefile_family(out_shp)
    out.to_file(out_shp)
    return out


def overlap_report(index: gpd.GeoDataFrame, aoi_geom, qa_dir: Path):
    union = unary_union(index.geometry.tolist())
    gap = aoi_geom.difference(union).area
    rows = []
    sindex = index.sindex
    seen = set()
    total_overlap = 0.0
    for i, row in index.iterrows():
        for j in sindex.intersection(row.geometry.bounds):
            if i == j:
                continue
            key = tuple(sorted((int(i), int(j))))
            if key in seen:
                continue
            seen.add(key)
            other = index.loc[j]
            inter = row.geometry.intersection(other.geometry)
            if inter.area > 0.0001:
                area = inter.area
                total_overlap += area
                rows.append({'BlockNum_A': row.BlockNum, 'BlockNum_B': other.BlockNum, 'OVERLAP_M2': round(area, 3)})
    qa_dir.mkdir(parents=True, exist_ok=True)
    with (qa_dir/'overlap_pairs.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['BlockNum_A','BlockNum_B','OVERLAP_M2'])
        w.writeheader(); w.writerows(rows)
    return gap, total_overlap, len(rows)


def choose_sources_for_bounds(files, bounds):
    b = box(*bounds)
    chosen = []
    for p in files:
        with rasterio.open(p) as ds:
            if box(*ds.bounds).intersects(b):
                chosen.append(p)
    return chosen


def write_prj(path: Path, crs):
    path.with_suffix('.prj').write_text(CRS.from_user_input(crs).to_wkt(version='WKT1_ESRI'), encoding='utf-8')


def export_tile(blocknum: str, bounds, circuit_aoi, files, out_tif: Path, band_count: int, crs, res, overwrite=False):
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    if out_tif.exists() and not overwrite:
        return 'exists', 0, 0.0
    if band_count != 4:
        raise ValueError(
            f'ATCO final TIFF NoData rule requires 4-band RGBI imagery; detected {band_count} band(s).'
        )
    chosen = choose_sources_for_bounds(files, bounds)
    if not chosen:
        return 'no source coverage', 0, 0.0
    datasets = []
    try:
        for p in chosen:
            datasets.append(rasterio.open(p))
        # ATCO source ortho backgrounds sometimes use RGBI=(254,254,254,254)
        # as an unmasked fill value. Reject precisely this four-band tuple
        # *before* mosaicking: otherwise method='first' may select the white
        # fringe and obscure real imagery from an overlapping source.
        #
        # The fifth merge band is an in-memory validity marker. It is never
        # written to the client TIFF (which retains exactly four RGBI bands).
        def merge_first_nonwhite(merged_data, new_data, merged_mask, new_mask, **kwargs):
            # Rasterio can pass a scalar False mask for a completely
            # unmasked source, rather than a 4 x height x width array.
            # Support scalar, 2-D dataset and per-band 3-D masks.
            mask = np.asarray(new_mask, dtype=bool)
            if mask.ndim == 0:
                candidate = np.full(new_data.shape[1:], not bool(mask), dtype=bool)
            elif mask.ndim == 2:
                candidate = ~mask
            else:
                candidate = ~np.any(mask[:4], axis=0)
            background = np.all(new_data[:4] == 254, axis=0)
            copy_pixel = (merged_data[4] == 0) & candidate & ~background
            np.copyto(merged_data[:4], new_data[:4], where=copy_pixel[None, :, :])
            merged_data[4][copy_pixel] = 1

        arr, affine = merge(
            datasets,
            bounds=bounds,
            res=res,
            indexes=[1, 2, 3, 4],
            output_count=5,
            masked=False,
            nodata=0,
            method=merge_first_nonwhite,
            target_aligned_pixels=False,
        )
        source_valid = arr[4] == 1
        data = arr[:4].copy()
        aoi_valid = geometry_mask([circuit_aoi], out_shape=data.shape[1:], transform=affine, invert=True)
        valid = source_valid & aoi_valid
        valid_pixels = int(valid.sum())
        total_pixels = int(valid.size)
        coverage_pct = (100.0 * valid_pixels / total_pixels) if total_pixels else 0.0

        # Do not use a per-band NoData=255 marker for RGBI imagery: an
        # otherwise valid pixel may legitimately contain 255 in any band.
        # Carry one explicit dataset validity mask instead and preserve every
        # valid source sample byte-for-byte (including 255).
        data[:, ~valid] = 0

        profile = datasets[0].profile.copy()
        for key in ('blockxsize','blockysize','photometric','interleave','nodata'):
            profile.pop(key, None)
        profile.update(
            driver='GTiff', height=data.shape[1], width=data.shape[2], count=4,
            dtype=data.dtype, transform=affine, crs=crs, nodata=None,
            tiled=True, blockxsize=512, blockysize=512, compress='lzw',
            photometric='RGB', BIGTIFF='IF_SAFER'
        )
        final_tmp = out_tif.with_suffix('.nodata.tmp.tif')
        if final_tmp.exists():
            final_tmp.unlink()
        # GDAL_TIFF_INTERNAL_MASK keeps the 1-bit mask inside the deliverable,
        # not in an easily forgotten .msk sidecar.
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
            with rasterio.open(final_tmp, 'w', **profile) as dst:
                dst.write(data)
                dst.colorinterp = (
                    ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.undefined
                )
                dst.write_mask((valid.astype(np.uint8) * 255))

        # Read back the delivered pixels/mask before replacing a previous TIFF.
        with rasterio.open(final_tmp) as check:
            if check.nodata is not None:
                raise RuntimeError(f'Unexpected NoData value in {final_tmp}')
            if not np.array_equal(check.dataset_mask() > 0, valid):
                raise RuntimeError(f'Internal TIFF mask mismatch for {blocknum}')
            if not np.array_equal(check.read()[:, valid], data[:, valid]):
                raise RuntimeError(f'Valid TIFF sample values changed for {blocknum}')
        if final_tmp.with_suffix(final_tmp.suffix + '.msk').exists():
            raise RuntimeError(f'Internal TIFF mask was written as a sidecar: {final_tmp}')

        if out_tif.exists():
            out_tif.unlink()
        final_tmp.replace(out_tif)
        write_prj(out_tif, crs)
        return f'created from {len(chosen)} source(s)', valid_pixels, coverage_pct
    finally:
        for ds in datasets:
            ds.close()


def load_config():
    if not CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


def find_global_mapper():
    cfg = load_config()
    candidates = []
    env = os.environ.get('GLOBAL_MAPPER_EXE')
    if env: candidates.append(Path(env))
    if cfg.get('global_mapper_exe'): candidates.append(Path(cfg['global_mapper_exe']))
    for base in (Path(os.environ.get('ProgramFiles', r'C:\Program Files')), Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))):
        if base.exists():
            for pat in ('GlobalMapper*','globalmapper*'):
                for d in sorted(base.glob(pat), reverse=True):
                    for name in ('global_mapper.exe','GlobalMapper.exe','global_mapper64.exe'):
                        candidates.append(d/name)
    seen = set()
    for p in candidates:
        s = str(p).casefold()
        if s in seen: continue
        seen.add(s)
        if p.is_file():
            return p
    return None


def gms_quote(path: Path):
    return str(path).replace('"', "'")


def build_gm_rgba_input(src_tif: Path, out_tif: Path):
    """Create a Global Mapper-only RGBA GeoTIFF.

    The client TIFF remains RGBI and carries an internal dataset mask.
    ECW must not see NIR as its 4th band because Global Mapper can interpret
    band 4 as alpha. This intermediate copies RGB byte-for-byte and converts
    the TIFF's explicit validity mask into a real 0/255 alpha band.
    """
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_tif.with_suffix('.tmp.tif')
    if tmp.exists():
        tmp.unlink()
    with rasterio.open(src_tif) as src:
        if src.count < 3:
            raise ValueError(f'Expected RGB TIFF for ECW conversion: {src_tif}')
        if any(dt != 'uint8' for dt in src.dtypes[:3]):
            raise ValueError(f'ECW RGB intermediate expects uint8 RGB bands: {src_tif} has {src.dtypes[:3]}')
        profile = src.profile.copy()
        for key in ('blockxsize','blockysize','photometric','interleave','nodata'):
            profile.pop(key, None)
        profile.update(
            driver='GTiff', count=4, dtype='uint8', nodata=None,
            tiled=True, blockxsize=256, blockysize=256, compress='lzw',
            photometric='RGB', interleave='pixel', BIGTIFF='IF_SAFER'
        )
        with rasterio.open(tmp, 'w', **profile) as dst:
            dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
            for _, window in src.block_windows(1):
                rgb = src.read([1, 2, 3], window=window)
                alpha = src.dataset_mask(window=window)
                dst.write(rgb, indexes=[1, 2, 3], window=window)
                dst.write(alpha.astype(np.uint8), indexes=4, window=window)
            # This is a temporary QA-only file; ensure alpha was actually
            # produced before Global Mapper receives the raster.
        with rasterio.open(tmp) as check:
            if check.colorinterp[3] != ColorInterp.alpha:
                raise RuntimeError(f'Global Mapper TIFF has no alpha band: {tmp}')
            with rasterio.open(src_tif) as src:
                for _, window in src.block_windows(1):
                    if not np.array_equal(check.read(4, window=window), src.dataset_mask(window=window)):
                        raise RuntimeError(f'Global Mapper alpha differs from source mask: {tmp}')
    if out_tif.exists():
        out_tif.unlink()
    tmp.replace(out_tif)
    return out_tif


def _rgba_is_current(src_tif: Path, rgba_tif: Path):
    if not rgba_tif.is_file():
        return False
    try:
        return rgba_tif.stat().st_mtime_ns >= src_tif.stat().st_mtime_ns
    except OSError:
        return False


def configured_parallel_workers():
    raw = os.environ.get('ATCO_PARALLEL_WORKERS',
                         os.environ.get('ATCO_ECW_WORKERS', '2'))
    try:
        return int(raw)
    except Exception:
        return 2


def resolve_parallel_workers(value, pending_count):
    try:
        value = int(value)
    except Exception:
        value = 2
    if pending_count <= 0:
        return 1
    if value == -1:
        return max(1, min(os.cpu_count() or 1, pending_count))
    if value < 1:
        return 1
    return max(1, min(value, pending_count))


def prepare_gm_inputs(tiffs, gm_input_dir: Path, workers=None):
    """Build the Global Mapper RGBA intermediates in parallel and reuse current ones."""
    gm_input_dir.mkdir(parents=True, exist_ok=True)
    workers = resolve_parallel_workers(configured_parallel_workers() if workers is None else workers, len(tiffs))
    outputs = [(tif, gm_input_dir / (tif.stem + '_GM_RGBA.tif')) for tif in tiffs]
    pending = [(tif, rgba) for tif, rgba in outputs if not _rgba_is_current(tif, rgba)]
    reused = len(outputs) - len(pending)

    if not pending:
        print(f'RGBA    [########################] {len(outputs):>3}/{len(outputs):<3} | cached', flush=True)
        return outputs

    done = reused
    progress_line('RGBA', done, len(outputs), workers=workers,
                  extra=f'{reused} cached' if reused else None)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(build_gm_rgba_input, tif, rgba): (tif, rgba) for tif, rgba in pending}
        for fut in as_completed(futs):
            fut.result()
            done += 1
            progress_line('RGBA', done, len(outputs), workers=workers)
    return outputs

def make_gm_script(gm_inputs, ecw_dir: Path, gms_path: Path, compression=10):
    gm_log = gms_path.with_suffix('.gm.log')
    lines = [
        'GLOBAL_MAPPER_SCRIPT VERSION="1.00" ENABLE_PROGRESS=NO LOG_TO_COMMAND_PROMPT=NO SHOW_WARNINGS=NO',
        f'SET_LOG_FILE FILENAME="{gms_quote(gm_log)}" APPEND_TO_FILE=NO',
        'UNLOAD_ALL',
    ]
    results = []
    for final_tif, rgba_tif in gm_inputs:
        ecw = ecw_dir / (final_tif.stem + '.ecw')
        # Import all RGBA bands. BAND_SETUP="0,1,2" selected only RGB and
        # risked dropping the explicit alpha channel before ECW export.
        # The intermediate already has RGB + alpha; NIR is not present.
        lines.append(
            f'IMPORT FILENAME="{gms_quote(rgba_tif)}" TYPE=AUTO '
            f'CONTRAST_MODE=NONE COLOR_SCALE="0,0,0"'
        )
        lines.append(
            f'EXPORT_RASTER FILENAME="{gms_quote(ecw)}" TYPE=ECW '
            f'TARGET_COMPRESSION={int(compression)} BG_TRANSPARENT=YES '
            f'ECW_FORMAT_VERSION=3 GEN_PRJ_FILE=YES OVERWRITE_EXISTING=YES '
            f'LAYER_BOUNDS="{gms_quote(rgba_tif)}" USE_EXACT_BOUNDS=YES'
        )
        lines.append('UNLOAD_ALL')
        results.append((final_tif, ecw))
    gms_path.parent.mkdir(parents=True, exist_ok=True)
    gms_path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return results


def _windows_descendant_pids(root_pid: int):
    """Return root PID and all descendants using Toolhelp32Snapshot."""
    if os.name != 'nt':
        return {int(root_pid)}

    import ctypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', ctypes.c_ulong),
            ('cntUsage', ctypes.c_ulong),
            ('th32ProcessID', ctypes.c_ulong),
            ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
            ('th32ModuleID', ctypes.c_ulong),
            ('cntThreads', ctypes.c_ulong),
            ('th32ParentProcessID', ctypes.c_ulong),
            ('pcPriClassBase', ctypes.c_long),
            ('dwFlags', ctypes.c_ulong),
            ('szExeFile', ctypes.c_wchar * MAX_PATH),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return {int(root_pid)}

    parent_map = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parent_map.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)

    result = set()
    queue = [int(root_pid)]
    while queue:
        pid = queue.pop(0)
        if pid in result:
            continue
        result.add(pid)
        queue.extend(parent_map.get(pid, []))
    return result


def _hide_windows_for_pids(pids):
    """Hide any visible top-level Windows belonging to the supplied PIDs."""
    if os.name != 'nt':
        return

    import ctypes

    user32 = ctypes.windll.user32
    SW_HIDE = 0

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @WNDENUMPROC
    def enum_proc(hwnd, lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
        return True

    user32.EnumWindows(enum_proc, 0)


def _run_gm_script(gm_exe: Path, gms_path: Path):
    """Run a Global Mapper .gms batch with every GM window kept hidden."""
    creationflags = 0
    startupinfo = None

    if os.name == 'nt':
        creationflags |= getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    proc = subprocess.Popen(
        [str(gm_exe), str(gms_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )

    # Global Mapper is a GUI application. Some versions create their own
    # top-level window even when launched in script/batch mode. Keep hiding
    # the GM process and any child windows until the script completes.
    if os.name == 'nt':
        import time
        while proc.poll() is None:
            try:
                pids = _windows_descendant_pids(proc.pid)
                _hide_windows_for_pids(pids)
            except Exception:
                pass
            time.sleep(0.025)

    stdout, stderr = proc.communicate()

    class _Result:
        pass

    result = _Result()
    result.returncode = proc.returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _write_gm_log(qa_dir: Path, stem: str, proc):
    log_dir = qa_dir / 'gm_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f'{stem}.log'
    p.write_text(
        f'RETURN_CODE={proc.returncode}\n\nSTDOUT\n{proc.stdout or ""}\n\nSTDERR\n{proc.stderr or ""}\n',
        encoding='utf-8', errors='replace'
    )
    return p


def resolve_ecw_workers(value, pending_count):
    return resolve_parallel_workers(value, pending_count)

def run_global_mapper(gm_exe: Path, gm_inputs, ecw_dir: Path, qa_dir: Path,
                      compression=10, workers=2, overwrite=False):
    """Export ECW in parallel GM batches with quiet progress and sequential retry."""
    ecw_dir.mkdir(parents=True, exist_ok=True)
    all_pairs = [(t, ecw_dir/(t.stem+'.ecw')) for t, _ in gm_inputs]
    pending = []
    for final_tif, rgba_tif in gm_inputs:
        ecw = ecw_dir / (final_tif.stem + '.ecw')
        if ecw.is_file() and ecw.stat().st_size == 0:
            ecw.unlink(missing_ok=True)
        current = ecw.is_file() and ecw.stat().st_size > 0 and ecw.stat().st_mtime_ns >= final_tif.stat().st_mtime_ns
        if overwrite or not current:
            pending.append((final_tif, rgba_tif))

    cached = len(gm_inputs) - len(pending)
    if not pending:
        print(f'ECW     [########################] {len(gm_inputs):>3}/{len(gm_inputs):<3} | cached', flush=True)
        return all_pairs

    workers = resolve_ecw_workers(workers, len(pending))
    chunks = [[] for _ in range(workers)]
    for i, pair in enumerate(pending):
        chunks[i % workers].append(pair)

    scripts = []
    for i, chunk in enumerate(chunks, 1):
        if not chunk:
            continue
        gms = qa_dir / f'export_ecw_batch_{i:02d}.gms'
        pairs = make_gm_script(chunk, ecw_dir, gms, compression=compression)
        scripts.append((gms, pairs))

    progress_line('ECW', cached, len(gm_inputs), workers=workers,
                  extra=f'{cached} cached' if cached else None)

    failures = []
    with ThreadPoolExecutor(max_workers=len(scripts)) as ex:
        futs = {ex.submit(_run_gm_script, gm_exe, gms): (gms, pairs) for gms, pairs in scripts}
        last_count = -1
        while True:
            finished = sum(1 for f in futs if f.done())
            created = sum(
                1 for tif, _rgba in gm_inputs
                if (ecw_dir/(tif.stem+'.ecw')).is_file() and (ecw_dir/(tif.stem+'.ecw')).stat().st_size > 0
            )
            if created != last_count:
                progress_line('ECW', created, len(gm_inputs), workers=workers)
                last_count = created
            if finished == len(futs):
                break
            time.sleep(0.5)

        for fut, (gms, pairs) in futs.items():
            proc = fut.result()
            _write_gm_log(qa_dir, gms.stem, proc)
            missing = [ecw for _, ecw in pairs if not ecw.is_file() or ecw.stat().st_size == 0]
            if proc.returncode != 0 or missing:
                failures.append((gms, proc, missing))

    missing_inputs = [
        (final_tif, rgba_tif)
        for final_tif, rgba_tif in pending
        if not (ecw_dir/(final_tif.stem+'.ecw')).is_file()
        or (ecw_dir/(final_tif.stem+'.ecw')).stat().st_size == 0
    ]

    if failures or missing_inputs:
        print(f'ECW retry: {len(missing_inputs)} missing tile(s), sequential fallback', flush=True)
        if missing_inputs:
            for final_tif, _rgba_tif in missing_inputs:
                bad = ecw_dir/(final_tif.stem+'.ecw')
                if bad.is_file() and bad.stat().st_size == 0:
                    bad.unlink(missing_ok=True)
            retry_gms = qa_dir/'export_ecw_retry_sequential.gms'
            retry_pairs = make_gm_script(missing_inputs, ecw_dir, retry_gms, compression=compression)
            proc = _run_gm_script(gm_exe, retry_gms)
            log_path = _write_gm_log(qa_dir, retry_gms.stem, proc)
            missing = [str(ecw) for _, ecw in retry_pairs if not ecw.is_file() or ecw.stat().st_size == 0]
            created = sum(
                1 for tif, _rgba in gm_inputs
                if (ecw_dir/(tif.stem+'.ecw')).is_file() and (ecw_dir/(tif.stem+'.ecw')).stat().st_size > 0
            )
            progress_line('ECW', created, len(gm_inputs), workers=1)
            if proc.returncode != 0 or missing:
                raise RuntimeError(
                    f'Global Mapper ECW conversion incomplete: {len(missing)} tile(s) missing. '
                    f'GM log: {log_path}'
                )

    created = sum(
        1 for tif, _rgba in gm_inputs
        if (ecw_dir/(tif.stem+'.ecw')).is_file() and (ecw_dir/(tif.stem+'.ecw')).stat().st_size > 0
    )
    progress_line('ECW', created, len(gm_inputs), workers=workers)
    return all_pairs

def process(aoi_path: Path, source_folder: Path, output_root: Path, make_ecw=True, overwrite=False, gm_exe=None):
    polygon = find_polygon_shp(aoi_path)
    circuit = circuit_from_polygon(polygon)
    print(f'Circuit: {circuit}', flush=True)
    aoi = load_aoi_revision(circuit, polygon)
    print(f'AOI: {aoi["revision_code"]} confirmed against central registry', flush=True)
    segment_shp, seg = load_segments(circuit, aoi['revision_code'])
    if not CRS.from_user_input(seg.crs).equals(aoi['crs']):
        raise ValueError(f'Segment CRS mismatch for {circuit}: {seg.crs} vs {aoi["crs"]}')
    files = source_tiffs(source_folder)
    print(f'Source TIFFs: {len(files)}', flush=True)
    inv, transform, res, band_count = raster_inventory(files, aoi['crs'])
    print(f'Raster grid: {res[0]:g} x {res[1]:g} m; data bands: {band_count}', flush=True)

    base = output_base(Path(output_root), circuit)
    tif_dir = base/'TIFF'
    ecw_dir = base/'ECW'
    qa_root = base/'QA'
    qa_dir = qa_root/'Imagery'
    index_dir = qa_root/'Ortho_Processing_Footprints'
    index_shp = index_dir/f'{circuit}_Ortho_Processing_Footprints.shp'
    ortho_index = build_ortho_index(seg, transform, res, index_shp, overwrite=overwrite)
    gap, overlap_m2, overlap_pairs = overlap_report(ortho_index, aoi['geometry'], qa_dir)
    if gap > 0.05:
        raise RuntimeError(f'Ortho footprint index does not cover AOI: gap={gap:.3f} m2')
    print(f'Ortho processing footprints: {len(ortho_index)} tiles; overlap pairs={overlap_pairs}; overlap area={overlap_m2:.1f} m2', flush=True)

    qa_dir.mkdir(parents=True, exist_ok=True)
    with (qa_dir/'source_inventory.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(inv[0].keys())); w.writeheader(); w.writerows(inv)

    rows = []
    outputs = []
    tiff_counts = {'created': 0, 'exists': 0, 'missing': 0}
    tiff_jobs = []
    for _, row in ortho_index.iterrows():
        blocknum = str(row.BlockNum)
        tiff_jobs.append((blocknum, row.geometry.bounds, tif_dir/f'{blocknum}.tif'))

    tiff_workers = resolve_parallel_workers(configured_parallel_workers(), len(tiff_jobs))

    def _tiff_job(job):
        blocknum, bounds, out_tif = job
        status, valid_pixels, coverage_pct = export_tile(
            blocknum, bounds, aoi['geometry'], files, out_tif,
            band_count, aoi['crs'], res, overwrite=overwrite
        )
        return blocknum, out_tif, status, valid_pixels, coverage_pct

    results_by_block = {}
    done = 0
    progress_line('TIFF', done, len(tiff_jobs), workers=tiff_workers)
    with ThreadPoolExecutor(max_workers=tiff_workers) as ex:
        futures = {ex.submit(_tiff_job, job): job[0] for job in tiff_jobs}
        for fut in as_completed(futures):
            blocknum, out_tif, status, valid_pixels, coverage_pct = fut.result()
            results_by_block[blocknum] = (out_tif, status, valid_pixels, coverage_pct)
            done += 1
            progress_line('TIFF', done, len(tiff_jobs), workers=tiff_workers)

    for blocknum, _bounds, _out in tiff_jobs:
        out_tif, status, valid_pixels, coverage_pct = results_by_block[blocknum]
        if status.startswith('created'):
            tiff_counts['created'] += 1
        elif status == 'exists':
            tiff_counts['exists'] += 1
        else:
            tiff_counts['missing'] += 1
        rows.append({
            'BlockNum': blocknum,
            'TIFF': status,
            'VALID_PIXELS': valid_pixels,
            'TILE_COVERAGE_PCT': round(coverage_pct, 4),
            'ECW': 'pending' if make_ecw else 'not requested',
        })
        if out_tif.is_file():
            outputs.append(out_tif)

    print(f"TIFF summary: {tiff_counts['created']} created, {tiff_counts['exists']} cached, {tiff_counts['missing']} missing", flush=True)

    if make_ecw:
        gm = Path(gm_exe) if gm_exe else find_global_mapper()
        if gm is None or not gm.is_file():
            raise RuntimeError(
                'Global Mapper executable not found. Set GLOBAL_MAPPER_EXE or run interactive mode and provide the EXE path.'
            )
        cfg = load_config(); cfg['global_mapper_exe'] = str(gm); save_config(cfg)
        ecw_dir.mkdir(parents=True, exist_ok=True)
        gm_input_dir = qa_dir/'gm_rgb_input'
        parallel_workers = configured_parallel_workers()
        gm_inputs = prepare_gm_inputs(outputs, gm_input_dir, workers=parallel_workers)
        ecw_workers = parallel_workers
        pairs = run_global_mapper(gm, gm_inputs, ecw_dir, qa_dir,
                                  compression=10, workers=ecw_workers, overwrite=overwrite)
        ecw_exists = {p.stem: p.is_file() for _, p in pairs}
        for r in rows:
            r['ECW'] = 'created' if ecw_exists.get(r['BlockNum'], False) else ('no TIFF' if not (tif_dir/f"{r['BlockNum']}.tif").exists() else 'failed')

    with (qa_dir/'processing_report.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['BlockNum','TIFF','VALID_PIXELS','TILE_COVERAGE_PCT','ECW'])
        w.writeheader(); w.writerows(rows)
    summary = {
        'created_at': now_iso(), 'circuit': circuit, 'aoi_revision': aoi['revision_code'],
        'aoi_source_selected': str(polygon), 'central_aoi_source': aoi['source_folder'],
        'segments_source': str(segment_shp), 'source_imagery_folder': str(source_folder),
        'source_tiff_count': len(files), 'data_band_count_tiff': band_count,
        'ecw_bands': 3 if make_ecw else None, 'pixel_size': list(res),
        'ortho_processing_footprint_count': len(ortho_index), 'footprint_gap_m2': gap,
        'footprint_overlap_m2': overlap_m2, 'footprint_overlap_pairs': overlap_pairs,
        'tiff_background': '4-band RGBI with an internal TIFF validity mask; valid 255 preserved; nodata unset',
        'ecw_background': 'RGBA intermediate alpha -> Global Mapper BG_TRANSPARENT=YES, ECW v3' if make_ecw else None,
        'ecw_rgb_source': 'dedicated 8-bit RGB copy of TIFF bands 1/2/3; NIR excluded from GM input' if make_ecw else None,
        'parallel_workers_config': configured_parallel_workers(), 'output_root': str(base),
    }
    (qa_dir/'execution.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'\nDONE: {base}', flush=True)
    return summary


def interactive():
    print('ATCO - Ortho Processor v1.1')
    print(f'Central: {CENTRAL_ROOT}')
    print('')
    print('1. Process TIFF + ECW')
    print('2. Process TIFF only')
    print('3. Build/test Ortho Processing Footprints only')
    print('4. Exit')
    choice = input('\nChoose [1-4]: ').strip()
    if choice == '4': return 0
    if choice not in ('1','2','3'):
        raise SystemExit('Invalid choice')
    aoi_input = input('AOI version folder or *_Polygon.shp: ').strip().strip('"')
    source = input('Source orthophoto TIFF folder: ').strip().strip('"') if choice in ('1','2') else None
    out_default = str(DEFAULT_OUTPUT_ROOT)
    out = input(f'Output root [{out_default}]: ').strip().strip('"') or out_default

    if choice == '3':
        polygon = find_polygon_shp(Path(aoi_input))
        circuit = circuit_from_polygon(polygon)
        aoi = load_aoi_revision(circuit, polygon)
        shp, seg = load_segments(circuit, aoi['revision_code'])
        print('Index-only mode needs source raster grid to align footprint edges exactly.')
        source = input('Source orthophoto TIFF folder: ').strip().strip('"')
        files = source_tiffs(Path(source))
        _, transform, res, _ = raster_inventory(files, aoi['crs'])
        base = output_base(Path(out), circuit)
        oi = build_ortho_index(seg, transform, res, base/'QA'/'Ortho_Processing_Footprints'/f'{circuit}_Ortho_Processing_Footprints.shp')
        gap, ov, pairs = overlap_report(oi, aoi['geometry'], base/'QA')
        print(f'Created {len(oi)} ortho processing footprints; AOI gap={gap:.3f} m2; overlap={ov:.1f} m2 ({pairs} pairs)')
        return 0

    gm = None
    if choice == '1':
        found = find_global_mapper()
        if found:
            print(f'Global Mapper detected: {found}')
            gm = str(found)
        else:
            gm = input('Global Mapper EXE path: ').strip().strip('"')
            if not Path(gm).is_file():
                raise FileNotFoundError(f'Global Mapper EXE not found: {gm}')
    process(Path(aoi_input), Path(source), Path(out), make_ecw=(choice=='1'), overwrite=False, gm_exe=gm)
    return 0


def main():
    ap = argparse.ArgumentParser(description='ATCO orthophoto segment processor')
    ap.add_argument('--aoi', help='AOI version folder or exact *_Polygon.shp')
    ap.add_argument('--source', help='Folder containing source TIF/TIFF imagery')
    ap.add_argument('--output-root', default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument('--tiff-only', action='store_true')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--global-mapper-exe')
    a = ap.parse_args()
    try:
        if not a.aoi:
            return interactive()
        if not a.source:
            raise ValueError('--source is required in non-interactive mode')
        process(Path(a.aoi), Path(a.source), Path(a.output_root), make_ecw=not a.tiff_only,
                overwrite=a.overwrite, gm_exe=a.global_mapper_exe)
        return 0
    except Exception as exc:
        print(f'\nERROR: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
