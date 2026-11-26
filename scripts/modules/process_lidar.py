#!/usr/bin/env python3
from __future__ import annotations

from circuit_identity import resolve_circuit_id

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely import wkb
from shapely.geometry import box
from shapely.ops import unary_union

from cloud_source_selection import select_entity_clouds

from atco_delivery import (
    YEAR as DELIVERY_YEAR, client_entity_name,
    enrich_segments, entity_type, is_tx, output_base, product_folder, tile_index_shp_path, utm_zone,
)

CENTRAL_ROOT = Path(r"W:\APPS\PIT\ATCO")
DB_PATH = CENTRAL_ROOT / "database" / "atco_aoi_master.sqlite"
SEGMENTS_ROOT = CENTRAL_ROOT / "segments" / "circuit"
DEFAULT_OUTPUT_ROOT = Path(r"D:\ATCO\output")
YEAR = 2026
TRAJECTORY_ROOT = CENTRAL_ROOT / "source" / "trajectories"


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

def _startupinfo_hidden():
    if os.name != 'nt':
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def _windows_descendant_pids(root_pid: int):
    if os.name != 'nt':
        return {int(root_pid)}
    import ctypes
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', ctypes.c_ulong), ('cntUsage', ctypes.c_ulong),
            ('th32ProcessID', ctypes.c_ulong), ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
            ('th32ModuleID', ctypes.c_ulong), ('cntThreads', ctypes.c_ulong),
            ('th32ParentProcessID', ctypes.c_ulong), ('pcPriClassBase', ctypes.c_long),
            ('dwFlags', ctypes.c_ulong), ('szExeFile', ctypes.c_wchar * MAX_PATH),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return {int(root_pid)}
    parent_map = {}
    try:
        entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parent_map.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    found = {int(root_pid)}
    frontier = [int(root_pid)]
    while frontier:
        parent = frontier.pop()
        for child in parent_map.get(parent, []):
            if child not in found:
                found.add(child); frontier.append(child)
    return found


def _hide_windows_for_pids(pids):
    if os.name != 'nt':
        return
    import ctypes
    user32 = ctypes.windll.user32
    pids = {int(x) for x in pids}
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
        return True
    user32.EnumWindows(callback, 0)


def run(cmd, *, check=True, hide_windows=False):
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    if hide_windows and os.name == 'nt':
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=creationflags, startupinfo=_startupinfo_hidden()
        )
        # EzLAS is a GUI-ish executable on some installations and can create
        # visible top-level windows despite CREATE_NO_WINDOW. Hide its own and
        # any child windows for the lifetime of the command.
        while proc.poll() is None:
            try:
                _hide_windows_for_pids(_windows_descendant_pids(proc.pid))
            except Exception:
                pass
            import time
            time.sleep(0.05)
        stdout, stderr = proc.communicate()
        p = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    else:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=creationflags, startupinfo=_startupinfo_hidden() if os.name == 'nt' else None
        )
    if check and p.returncode:
        tail = (p.stderr or p.stdout or '')[-3000:]
        raise RuntimeError(f'{Path(str(cmd[0])).name} failed ({p.returncode}):\n{tail}')
    return p


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
        raise ValueError(f'Multiple Polygon SHPs found. Point to one entity/version folder or exact SHP:\n{names}')
    return found[0]


def circuit_from_polygon(path: Path):
    stem = path.stem
    if not stem.lower().endswith('_polygon'):
        raise ValueError(path)
    return stem[:-len('_polygon')]


def db_connect_readonly(path: Path):
    # Do not use URI mode for mapped W: drives; SQLite can reject resolved UNC authority.
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
        canonical_circuit, current_revision_id = resolve_circuit_id(con, circuit)
        circuit = canonical_circuit
        rows = con.execute(
            """SELECT id, revision_num, revision_code, source_version, crs_wkt, polygon_wkb, source_folder
               FROM revisions WHERE entity_kind='circuit' AND entity_id=? ORDER BY revision_num""",
            (circuit,),
        ).fetchall()
    if not rows:
        raise ValueError(f'No registered AOI revisions for {circuit}')

    selected_crs = CRS.from_user_input(selected.crs)
    matches = []
    for row in rows:
        target_crs = CRS.from_wkt(row[4])
        test_geom = selected_geom
        if not selected_crs.equals(target_crs):
            test_geom = gpd.GeoSeries([selected_geom], crs=selected_crs).to_crs(target_crs).iloc[0]
        central_geom = wkb.loads(row[5])
        diff = test_geom.symmetric_difference(central_geom).area
        if diff <= 0.05:
            matches.append((row, diff, target_crs, central_geom))

    if not matches:
        current = next((r for r in rows if int(r[0]) == current_revision_id), rows[-1])
        raise RuntimeError(
            f'AOI VERSION MISMATCH: selected AOI does not match central {current[2]} for {circuit}.\n'
            'Publish the production AOI update in the central registry, then regenerate/publish segments first.'
        )
    row, diff, target_crs, central_geom = matches[-1]
    if int(row[0]) != current_revision_id:
        current = next((r for r in rows if int(r[0]) == current_revision_id), rows[-1])
        raise RuntimeError(
            f'OUTDATED AOI: selected AOI matches {row[2]}, but central current revision is {current[2]}. '
            'Use the current production AOI.'
        )
    return {
        'entity_id': circuit,
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
            'Publish AOI Segments before processing LiDAR.'
        )
    seg = gpd.read_file(shp)
    if seg.empty or 'BlockNum' not in seg.columns or not seg.crs:
        raise ValueError(f'Invalid central segment set: {shp}')
    if seg['BlockNum'].isna().any() or seg['BlockNum'].duplicated().any():
        raise ValueError(f'Missing/duplicate BlockNum in {shp}')
    if not seg.geometry.is_valid.all():
        raise ValueError(f'Invalid segment geometry in {shp}')
    seg = enrich_segments(seg, circuit)
    return shp, seg


def source_clouds(folder: Path):
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f'Point-cloud source folder not found: {folder}')
    files = sorted(p for p in folder.rglob('*') if p.suffix.lower() in ('.las', '.laz'))
    if not files:
        raise FileNotFoundError(f'No LAS/LAZ files below {folder}')
    return files


def crs_from_json(obj):
    """Read explicit PDAL CRS fields, including string and PROJJSON forms."""
    if isinstance(obj, str) and obj.strip().casefold() not in ('', 'unknown', 'undefined', 'none', 'null'):
        try:
            return CRS.from_user_input(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        if obj.get('type') in ('ProjectedCRS', 'CompoundCRS', 'GeographicCRS',
                               'GeodeticCRS', 'BoundCRS'):
            try:
                return CRS.from_json_dict(obj)
            except Exception:
                pass
        for key in ('compoundwkt', 'wkt', 'spatialreference', 'comp_spatialreference',
                    'srs', 'horizontal', 'prettywkt', 'proj4', 'projjson', 'coordinate_system'):
            if key in obj:
                got = crs_from_json(obj[key])
                if got is not None:
                    return got
        # Recurse into containers only; filenames and unrelated numeric
        # metadata must not accidentally be interpreted as CRS definitions.
        for key, value in obj.items():
            if key == 'units':
                continue
            if isinstance(value, (dict, list)):
                got = crs_from_json(value)
                if got is not None:
                    return got
    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (dict, list)):
                got = crs_from_json(value)
                if got is not None:
                    return got
    return None


def has_explicit_crs_metadata(obj):
    """Distinguish blank projection metadata from a nonempty unparseable CRS."""
    keys = ('compoundwkt', 'wkt', 'spatialreference', 'comp_spatialreference',
            'srs', 'horizontal', 'prettywkt', 'prettycompoundwkt', 'proj4', 'projjson', 'coordinate_system', 'vertical')
    if isinstance(obj, dict):
        if str(obj.get('user_id', '')).strip().casefold() == 'lasf_projection':
            return True
        for key, value in obj.items():
            if key == 'units':
                continue
            if key in keys:
                if isinstance(value, str) and value.strip().casefold() not in ('', 'unknown', 'undefined', 'none', 'null'):
                    return True
                if isinstance(value, dict) and value.get('type') in (
                    'ProjectedCRS', 'CompoundCRS', 'GeographicCRS', 'GeodeticCRS', 'BoundCRS', 'VerticalCRS'
                ):
                    return True
            if isinstance(value, (dict, list)) and has_explicit_crs_metadata(value):
                return True
    elif isinstance(obj, list):
        return any(has_explicit_crs_metadata(v) for v in obj if isinstance(v, (dict, list)))
    return False


def horizontal_crs(crs):
    """Return the 2D/horizontal component of projected or compound CRS."""
    c = CRS.from_user_input(crs)
    try:
        return c.to_2d()
    except Exception:
        if getattr(c, 'is_compound', False):
            for sub in c.sub_crs_list:
                if sub.is_projected or sub.is_geographic:
                    return sub
        return c


def vertical_crs(crs):
    """Return vertical CRS component if present, otherwise None."""
    c = CRS.from_user_input(crs)
    if getattr(c, 'is_vertical', False):
        return c
    if getattr(c, 'is_compound', False):
        for sub in c.sub_crs_list:
            if getattr(sub, 'is_vertical', False):
                return sub
    return None


def crs_short_label(crs):
    c = CRS.from_user_input(crs)
    auth = c.to_authority()
    return f'{auth[0]}:{auth[1]}' if auth else c.name

def bbox_from_obj(obj):
    if isinstance(obj, dict):
        if all(k in obj for k in ('minx', 'miny', 'maxx', 'maxy')):
            return tuple(float(obj[k]) for k in ('minx', 'miny', 'maxx', 'maxy'))
        for value in obj.values():
            got = bbox_from_obj(value)
            if got:
                return got
    return None


def fast_terrascan_header(path: Path):
    """Read only a narrowly recognised TerraScan LAS 1.4 header.

    Accept no projection VLRs, no EVLRs, and only the standard LASzip VLR.
    All other layouts, including files with CRS, use PDAL. Never read points.
    """
    import math
    import struct
    path = Path(path)
    try:
        with path.open('rb') as f:
            stat = os.fstat(f.fileno())
            h = f.read(375)
            if len(h) != 375 or h[:4] != b'LASF' or h[24:26] != bytes((1, 4)):
                return None
            if h[58:90].split(b'\x00', 1)[0].strip().lower() != b'terrascan':
                return None
            header_size, data_offset, vlr_count = struct.unpack_from('<HII', h, 94)
            point_format = h[104] & 0x3f
            point_count = struct.unpack_from('<Q', h, 247)[0]
            evlr_offset, evlr_count = struct.unpack_from('<QI', h, 235)
            if (header_size != 375 or not header_size <= data_offset < stat.st_size
                    or point_format not in (6, 7, 8, 9, 10) or not point_count
                    or evlr_offset or evlr_count or vlr_count > 1):
                return None
            if struct.unpack_from('<H', h, 6)[0] & 16:
                return None
            f.seek(header_size)
            for _ in range(vlr_count):
                v = f.read(54)
                if len(v) != 54:
                    return None
                uid = v[2:18].split(b'\x00', 1)[0].strip().lower()
                record_id, length = struct.unpack_from('<HH', v, 18)
                if uid != b'laszip encoded' or record_id != 22204:
                    return None
                if f.tell() + length > data_offset:
                    return None
                f.seek(length, 1)
            if f.tell() != data_offset:
                return None
            if path.suffix.lower() == '.laz' and vlr_count != 1:
                return None
            maxx, minx, maxy, miny, maxz, minz = struct.unpack_from('<6d', h, 179)
            scales = struct.unpack_from('<3d', h, 131)
            if not all(math.isfinite(v) for v in (minx, miny, minz, maxx, maxy, maxz, *scales)):
                return None
            if minx > maxx or miny > maxy or minz > maxz or any(v <= 0 for v in scales):
                return None
            return {'path': str(path), 'name': path.name,
                    'bounds': [minx, miny, maxx, maxy], 'crs_wkt': None,
                    'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                    'header_reader': 'TerraScan LAS 1.4 binary (no CRS)'}
    except (OSError, ValueError, struct.error):
        return None


def pdal_summary(path: Path, *, pdal_exe=None, log=None, fast_missing=False):
    """Shared header inspection for preflight and processing; no CRS guessing."""
    import time
    path = Path(path)
    emit = log or (lambda line: print(line, flush=True))
    if fast_missing:
        fast = fast_terrascan_header(path)
        if fast is not None:
            return fast
    exe = str(pdal_exe or shutil.which('pdal') or 'pdal')
    docs = []
    def inspect(option):
        cmd = [exe, 'info', option, str(path)]
        started = time.monotonic()
        emit(f'LiDAR header: {option} {path}')
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    errors='replace', timeout=120,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            emit(f'ERROR: PDAL header command failed: {cmd!r}: {exc}')
            raise RuntimeError(f'PDAL {option} failed for {path}: {exc}') from exc
        emit(f'PDAL {option}: exit={result.returncode}, elapsed={time.monotonic()-started:.1f}s')
        if result.stderr.strip():
            emit(f'PDAL stderr ({path.name}, {option}): {result.stderr.strip()}')
        if result.returncode:
            emit(f'PDAL stdout ({path.name}, {option}): {result.stdout}')
            raise RuntimeError(f'PDAL {option} failed for {path}: exit {result.returncode}')
        try:
            doc = json.loads(result.stdout)
        except ValueError as exc:
            emit(f'Invalid PDAL JSON ({path.name}, {option}): {result.stdout}')
            raise RuntimeError(f'Invalid PDAL JSON for {path}: {exc}') from exc
        docs.append((option, doc))
        return doc

    data = inspect('--summary')
    summary = data.get('summary', data)
    bounds = bbox_from_obj(summary.get('bounds', {})) or bbox_from_obj(data.get('bounds', {}))
    source_crs = crs_from_json(summary) or crs_from_json(data)
    header_meta = summary.get('metadata', {})
    complete_blank = (isinstance(header_meta, dict) and isinstance(header_meta.get('srs'), dict)
                      and 'spatialreference' in header_meta and 'comp_spatialreference' in header_meta
                      and not has_explicit_crs_metadata(data))
    if not bounds or (source_crs is None and not complete_blank):
        emit(f'LiDAR header fallback: {path.name}; bounds={bounds}; CRS={source_crs is not None}')
        meta = inspect('--metadata')
        bounds = bounds or bbox_from_obj(meta)
        source_crs = source_crs or crs_from_json(meta)
    explicit_crs = any(has_explicit_crs_metadata(doc) for _, doc in docs) if source_crs is None else False
    if not bounds or (source_crs is None and explicit_crs):
        for option, doc in docs:
            emit(f'PDAL diagnostic JSON ({path.name}, {option}): {json.dumps(doc, ensure_ascii=False)}')
    if not bounds:
        raise ValueError(f'LiDAR extent unavailable: {path}')
    if source_crs is None:
        if explicit_crs:
            raise ValueError(f'LiDAR CRS metadata exists but cannot be interpreted: {path}. Do not overwrite an unknown CRS.')
        emit(f'WARNING: LiDAR CRS unavailable after summary and metadata inspection: {path}')
    else:
        emit(f'LiDAR header OK: {path.name}; CRS={crs_short_label(source_crs)}; bounds={bounds}')
    return {
        'path': str(path),
        'name': path.name,
        'bounds': list(bounds),
        'crs_wkt': source_crs.to_wkt() if source_crs is not None else None,
        'size': path.stat().st_size,
        'mtime_ns': path.stat().st_mtime_ns,
        'header_reader': 'PDAL',
    }


def missing_source_crs(expected_crs):
    """ATCO TerraScan header repair: assign AOI horizontal CRS + CGVD2013."""
    from pyproj.crs import CompoundCRS
    horizontal = horizontal_crs(expected_crs)
    if not any(horizontal.equals(CRS.from_epsg(code)) for code in (2955, 2956)):
        raise ValueError(
            f'Missing LiDAR CRS: automatic assignment is limited to ATCO NAD83(CSRS) '
            f'UTM zones 11/12; AOI CRS is {crs_short_label(horizontal)}.'
        )
    return CRS.from_user_input(CompoundCRS(
        name=f'{horizontal.name} + CGVD2013 height',
        components=[horizontal, CRS.from_epsg(6647)],
    ).to_wkt())


def effective_source_crs(row, expected_crs, log=None):
    """Only an absent CRS may be assigned; never replace an existing one."""
    if row.get('crs_wkt'):
        return CRS.from_wkt(row['crs_wkt'])
    assigned = missing_source_crs(expected_crs)
    row['assigned_crs_wkt'] = assigned.to_wkt()
    emit = log or (lambda line: print(line, flush=True))
    emit(f'WARNING: {row["name"]}: missing source CRS; assuming AOI '
         f'{crs_short_label(horizontal_crs(assigned))} + CGVD2013 for LiDAR export only; '
         'no coordinate transformation; source file unchanged.')
    return assigned


def cache_fingerprint(files):
    return [{'path': str(p), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in files]


def cloud_inventory(files, expected_crs: CRS, qa_dir: Path | None, *, pdal_exe=None, log=None, workers=None):
    import time
    emit = log or (lambda line: print(line, flush=True))
    started = time.monotonic()
    files = sorted((Path(p) for p in files), key=lambda p: str(p).casefold())
    if not files:
        raise ValueError('No source LAS/LAZ files')
    cache = qa_dir / 'source_inventory.json' if qa_dir is not None else None
    fp = cache_fingerprint(files)
    if cache is not None and cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if (saved.get('fingerprint') == fp and saved.get('crs', {}).get('expected_horizontal_wkt') == horizontal_crs(expected_crs).to_wkt()
                    and saved.get('crs', {}).get('header_policy') == 'assign_missing_only_v2'):
                rows = saved['files']
                meta = saved.get('crs') or {}
                if meta.get('output_crs_wkt'):
                    output_crs = CRS.from_wkt(meta['output_crs_wkt'])
                    emit(f'Using cached point-cloud inventory: {len(rows)} files | elapsed={time.monotonic()-started:.1f}s')
                    if meta.get('horizontal_crs'):
                        emit(f'  Horizontal CRS: {meta["horizontal_crs"]}')
                    if meta.get('vertical_crs'):
                        emit(f'  Vertical CRS  : {meta["vertical_crs"]} (preserved in output LAZ/LAS)')
                    return rows, output_crs, meta
        except Exception:
            pass

    workers = min(8, resolve_parallel_workers(configured_parallel_workers() if workers is None else workers, len(files)))
    rows, failures = [], []
    emit(f'Indexing {len(files)} LAS/LAZ source file(s) with {workers} worker(s)...')
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(pdal_summary, p, pdal_exe=pdal_exe, log=emit, fast_missing=True): p for p in files}
        for i, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            try:
                rows.append(fut.result())
            except Exception as exc:
                failures.append(f'{p}: {type(exc).__name__}: {exc}')
                emit(f'ERROR: {failures[-1]}')
            if i % 25 == 0 or i == len(files):
                emit(f'  Indexed {i}/{len(files)} | elapsed={time.monotonic()-started:.1f}s')
    if failures:
        raise ValueError(f'{len(failures)}/{len(files)} LiDAR header checks failed. First: {failures[0]}. See Activity Log.')
    rows.sort(key=lambda r: r['path'].casefold())
    fast_count = sum(r.get('header_reader', '').startswith('TerraScan') for r in rows)
    emit(f'Header scan: {fast_count} binary TerraScan; {len(rows)-fast_count} PDAL; elapsed={time.monotonic()-started:.1f}s')

    expected_horizontal = horizontal_crs(expected_crs)
    mismatched = []
    source_crs_objects = []
    parsed = {}
    assigned = None
    assigned_wkt = None
    components = {}
    for r in rows:
        wkt = r.get('crs_wkt')
        if wkt is None:
            if assigned is None:
                assigned = missing_source_crs(expected_crs)
                assigned_wkt = assigned.to_wkt()
            r['assigned_crs_wkt'] = assigned_wkt
            src = assigned
        else:
            if wkt not in parsed:
                parsed[wkt] = CRS.from_wkt(wkt)
            src = parsed[wkt]
        source_crs_objects.append(src)
        if id(src) not in components:
            hcrs = horizontal_crs(src)
            components[id(src)] = (hcrs, vertical_crs(src), hcrs.equals(expected_horizontal))
        hcrs, _, matches = components[id(src)]
        if not matches:
            mismatched.append((r['name'], crs_short_label(hcrs), src.to_string()))
    if mismatched:
        sample = ', '.join(f'{n}={h}' for n, h, _ in mismatched[:5])
        raise ValueError(
            f'{len(mismatched)} source file(s) do not match circuit horizontal CRS '
            f'{crs_short_label(expected_horizontal)}: {sample}'
        )

    # Preserve the source vertical datum in output LAS/LAZ. A compound CRS such as
    # EPSG:2956 + CGVD2013 is valid when its horizontal component matches the AOI.
    verticals = [components[id(c)][1] for c in source_crs_objects]
    has_vertical = [v is not None for v in verticals]
    if any(has_vertical) and not all(has_vertical):
        raise ValueError(
            'Source point clouds mix files with and without a vertical CRS. '
            'Standardise the LAS/LAZ headers before processing so Z values are not ambiguously labelled.'
        )
    if all(has_vertical):
        first_vertical = verticals[0]
        inconsistent_vertical = []
        for r, v in zip(rows, verticals):
            if v is not first_vertical and not v.equals(first_vertical):
                inconsistent_vertical.append((r['name'], crs_short_label(v)))
        if inconsistent_vertical:
            sample = ', '.join(f'{n}={v}' for n, v in inconsistent_vertical[:5])
            raise ValueError(
                f'Source point clouds use inconsistent vertical CRS values: {sample}'
            )
        output_crs = source_crs_objects[0]
    else:
        output_crs = expected_horizontal

    rows_meta = {
        'expected_horizontal_wkt': expected_horizontal.to_wkt(),
        'header_policy': 'assign_missing_only_v2',
        'assigned_missing_crs_files': [r['name'] for r in rows if r.get('assigned_crs_wkt')],
        'horizontal_crs': crs_short_label(expected_horizontal),
        'vertical_crs': crs_short_label(verticals[0]) if verticals and verticals[0] is not None else None,
        'output_crs_wkt': output_crs.to_wkt(),
    }
    emit(f'  Horizontal CRS: {rows_meta["horizontal_crs"]}')
    if rows_meta['vertical_crs']:
        emit(f'  Vertical CRS  : {rows_meta["vertical_crs"]} (preserved in output LAZ/LAS)')

    emit(f'Missing source CRS: {len(rows_meta["assigned_missing_crs_files"])}/{len(rows)}; assign AOI + CGVD2013 on LiDAR export only; source files unchanged')
    if cache_fingerprint(files) != fp:
        raise ValueError('Source LAS/LAZ changed during header inspection; validate again.')
    if cache is not None:
        try:
            qa_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=qa_dir, delete=False) as f:
                tmp = Path(f.name)
                json.dump({'fingerprint': fp, 'files': rows, 'crs': rows_meta}, f)
            try:
                os.replace(tmp, cache)
            finally:
                tmp.unlink(missing_ok=True)
            emit(f'Inventory cache saved: {cache}')
        except OSError as exc:
            emit(f'WARNING: could not save inventory cache: {exc}')
    emit(f'LiDAR inventory complete: {len(rows)} files; elapsed={time.monotonic()-started:.1f}s')
    return rows, output_crs, rows_meta


def find_ezlas():
    candidates = []
    env = os.environ.get('ATCO_EZLAS_EXE')
    if env:
        candidates.append(Path(env))
    script_dir = Path(__file__).resolve().parent
    candidates += [
        script_dir.parent / 'tools' / 'EzLAS' / 'EzLAS.exe',
        script_dir / 'EzLAS.exe',
        Path(r'C:\EzLAS\EzLAS.exe'),
        Path(r'C:\Tools\EzLAS\EzLAS.exe'),
        Path(r'C:\Program Files\Esri\LAS Optimizer\EzLAS.exe'),
        Path(r'C:\Program Files (x86)\Esri\LAS Optimizer\EzLAS.exe'),
    ]
    which = shutil.which('EzLAS.exe') or shutil.which('EzLAS')
    if which:
        candidates.insert(0, Path(which))
    for p in candidates:
        if p.is_file():
            return p
    return None


def write_prj(path: Path, crs):
    path.with_suffix('.prj').write_text(CRS.from_user_input(crs).to_wkt(version='WKT1_ESRI'), encoding='utf-8')


def remove_shapefile_family(shp_path: Path):
    stem = shp_path.with_suffix('')
    for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.shp.xml'):
        p = Path(str(stem) + ext)
        if p.exists():
            p.unlink()



def _find_stats_dimension(obj, dimension='GpsTime'):
    if isinstance(obj, dict):
        name = obj.get('name') or obj.get('dimension')
        if str(name).casefold() == str(dimension).casefold() and 'minimum' in obj and 'maximum' in obj:
            return obj
        for value in obj.values():
            found = _find_stats_dimension(value, dimension)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_stats_dimension(value, dimension)
            if found is not None:
                return found
    return None


def gps_time_range(laz_path: Path):
    """Return min/max adjusted GPS time from a final tile, or None if unavailable."""
    if not laz_path.is_file() or laz_path.stat().st_size == 0:
        return None
    proc = run(['pdal', 'info', '--stats', '--dimensions', 'GpsTime', str(laz_path)], check=False)
    if proc.returncode:
        return None
    try:
        data = json.loads(proc.stdout)
        stat = _find_stats_dimension(data, 'GpsTime')
        if stat is None:
            return None
        return float(stat['minimum']), float(stat['maximum'])
    except Exception:
        return None


def load_trajectory_index():
    path = TRAJECTORY_ROOT / 'trajectory_index.parquet'
    if not path.is_file():
        print('NOTE: Trajectory index missing; Tile Index flight attributes will be blank.', flush=True)
        return None
    try:
        import pyarrow  # noqa: F401 - optional, not part of the base GIS runtime
    except ImportError as exc:
        raise RuntimeError(
            'Tile Index trajectory attribution needs optional PyArrow. Run '
            'INSTALL_TRAJECTORY_SUPPORT.bat and retry. '
            'Without it the Tile Index would have missing flight metadata.'
        ) from exc
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        raise RuntimeError(f'Cannot read TX trajectory index: {path}: {exc}') from exc
    required = {'date_from_folder', 'flightline_code',
                'las_adjusted_gps_start', 'las_adjusted_gps_end'}
    if not required.issubset(df.columns):
        raise ValueError(f'Trajectory index missing columns {required - set(df.columns)}: {path}')
    return df


def trajectory_attributes(laz_path: Path, trajectory_index):
    """Best-effort attribution shared by TX and DX using final LAZ GPS range.

    Return paired date/flightline lists and the earliest acquisition DDDYY.
    Temporal overlap alone does not prove the aircraft supplied points.
    """
    if trajectory_index is None:
        return '', '', None
    rng = gps_time_range(laz_path)
    if not rng:
        return '', '', None
    lo, hi = rng
    if not (float('-inf') < lo <= hi < float('inf')):
        return '', '', None
    df = trajectory_index
    hit = df[(df['las_adjusted_gps_end'] >= lo) & (df['las_adjusted_gps_start'] <= hi)]
    pairs = set()
    for day, line in zip(hit['date_from_folder'], hit['flightline_code']):
        if pd.isna(day) or pd.isna(line):
            continue
        day = pd.to_datetime(day, errors='coerce')
        line = str(line).strip()
        if pd.isna(day) or not line:
            continue
        pairs.add((day.date(), line))
    ordered = sorted(pairs)
    if not ordered:
        return '', '', None
    dates = ', '.join(day.isoformat() for day, _ in ordered)
    lines = ', '.join(line for _, line in ordered)
    if any(len(value.encode('utf-8')) > 254 for value in (dates, lines)):
        raise ValueError(f'Tile Index flight attributes exceed SHP text limit: {laz_path}')
    julian = int(ordered[0][0].strftime('%j%y'))
    return dates, lines, julian


def build_tile_index_shp(circuit: str, seg: gpd.GeoDataFrame, base: Path, *, laz_dir: Path | None = None):
    """Create the intermediate client Tile Index as a shapefile.

    Retiler deliberately stops at SHP.  The final project/entity GDB is assembled
    later with the other client products.  GDB-managed fields such as OBJECTID,
    Shape, Shape_Length and Shape_Area are therefore not written here.
    """
    shp = tile_index_shp_path(base, circuit)
    shp.parent.mkdir(parents=True, exist_ok=True)
    zone = utm_zone(seg.crs)
    client_name = client_entity_name(circuit)

    # Do not publish an apparently complete client index without its LAZ inputs.
    # This also protects CLI callers and combined LAZ + Tile Index runs.
    laz_dir = Path(laz_dir) if laz_dir is not None else base / product_folder('LAZ')
    missing_laz = []
    for name in seg['PointCloudName']:
        path = laz_dir / f'{name}.laz'
        if not path.is_file() or path.stat().st_size == 0:
            missing_laz.append(path.name)
    if missing_laz:
        examples = ', '.join(missing_laz[:5])
        raise RuntimeError(
            f'Tile Index blocked for {circuit}: {len(missing_laz)}/{len(seg)} '
            f'expected LAZ tiles are missing or empty in {laz_dir}. '
            f'Examples: {examples}. Generate the missing LAZ tiles first, '
            'then regenerate Tile Index, or select LAZ + Tile Index together.'
        )
    trajectory_index = load_trajectory_index()
    if trajectory_index is None or trajectory_index.empty:
        raise RuntimeError(
            f'Tile Index blocked for {circuit}: the central trajectory index is missing or empty. '
            'Publish trajectory_index.parquet before generating the client Tile Index.'
        )
    tx = is_tx(circuit)
    records = []
    for _, row in seg.iterrows():
        fldate, flightline, julian = '', '', None
        if laz_dir is not None and trajectory_index is not None:
            laz_path = laz_dir / f'{row.PointCloudName}.laz'
            fldate, flightline, julian = trajectory_attributes(laz_path, trajectory_index)
        if tx:
            rec = {
                'BlockNum': str(row.TileSeq),
                'ZONE': str(zone),
                'YEAR': str(YEAR),
                'LINE': str(circuit),
                'NAME': str(row.PointCloudName),
                'fldate': fldate,
                'Flightline': flightline,
            }
        else:
            rec = {
                'BlockNum': str(row.TileSeq),
                'PRJ_NAME': client_name,
                'YEAR': str(YEAR),
                'ZONE': str(zone),
                'NAME': str(row.PointCloudName),
                'flline_id': julian,
                'flightid': flightline,
                'WONum': '',
                'fldate': fldate,
                'Flightline': flightline,
            }
        rec['geometry'] = row.geometry
        records.append(rec)
    frame = gpd.GeoDataFrame(records, geometry='geometry', crs=seg.crs)
    if not tx:
        # Nullable 32-bit integer, including tiles without flight metadata.
        frame['flline_id'] = pd.array(frame['flline_id'], dtype='Int32')
    missing = int((frame['Flightline'] == '').sum())
    if missing == len(frame):
        raise RuntimeError(
            f'Tile Index blocked for {circuit}: no flight metadata matched any of the '
            f'{len(frame)} LAZ tiles. Check PDAL, LAZ GPS Time and the trajectory index.'
        )
    if missing:
        print(f'WARNING: Tile Index {circuit}: {missing}/{len(frame)} tiles have no matched flight metadata.', flush=True)
    print('Tile Index attribution uses GPS range candidates; overlapping flights may require review.', flush=True)

    if frame.geom_type.isin(['MultiPolygon']).any():
        raise ValueError('Client Tile Index requires one singlepart polygon per tile; multipart segment detected')

    remove_shapefile_family(shp)
    # Shapefile is intentional here: it is an intermediate product that will be
    # imported as TILES during final GDB assembly.  All business field names fit
    # within the DBF 10-character limit.
    frame.to_file(shp, driver='ESRI Shapefile', index=False)

    qa = base / 'QA' / 'TILES'
    qa.mkdir(parents=True, exist_ok=True)
    frame.drop(columns='geometry').to_csv(qa / 'TILES_attributes.csv', index=False, encoding='utf-8-sig')
    (qa / 'TILES_schema.json').write_text(json.dumps({
        'entity': circuit,
        'client_entity_name': client_name,
        'entity_type': entity_type(circuit),
        'intermediate_shapefile': str(shp),
        'final_gdb_layer': 'TILES',
        'feature_count': len(frame),
        'fields': [c for c in frame.columns if c != 'geometry'],
        'zone': str(zone),
        'year': str(YEAR),
        'tx_trajectory_attribution': bool(tx),
        'trajectory_attribution': 'GPS_RANGE_CANDIDATES',
        'trajectory_index': str(TRAJECTORY_ROOT / 'trajectory_index.parquet'),
        'tiles_without_flight_metadata': missing,
        'dx_flline_id_rule': 'earliest matched acquisition date, DDDYY integer' if not tx else None,
        'dx_flightid_rule': 'same as Flightline' if not tx else None,
        'note': 'OBJECTID/Shape and GDB geometry fields are created later when this SHP is imported into the final delivery GDB.',
    }, indent=2), encoding='utf-8')
    return shp, frame


def intersecting_sources(geom, inventory):
    chosen = []
    for r in inventory:
        if box(*r['bounds']).intersects(geom):
            chosen.append(r)
    return chosen


def pipeline_for_segment(seg_row, chosen, laz_out: Path, temp_las: Path | None, crs):
    stages = []
    endpoints = []
    for idx, r in enumerate(chosen):
        tag = f'reader_{idx}'
        reader = {'type': 'readers.las', 'filename': r['path'], 'tag': tag}
        if r.get('assigned_crs_wkt'):
            reader['default_srs'] = r['assigned_crs_wkt']
        stages.append(reader)
        endpoints.append(tag)
    if len(endpoints) > 1:
        stages.append({'type': 'filters.merge', 'inputs': endpoints, 'tag': 'merged'})
        endpoint = 'merged'
    else:
        endpoint = endpoints[0]

    stages.append({'type': 'filters.crop', 'inputs': [endpoint], 'polygon': seg_row.geometry.wkt, 'tag': 'cropped'})
    stages.append({'type': 'filters.expression', 'inputs': ['cropped'], 'expression': 'Classification != 7', 'tag': 'clean'})

    # Do not alter Synthetic flags during retiling. ReturnNumber=0/Synthetic
    # consistency is reported in QA only; class 93 can legitimately require review.
    common_writer = {'type': 'writers.las', 'inputs': ['clean'], 'forward': 'all', 'a_srs': CRS.from_user_input(crs).to_wkt()}
    laz_writer = dict(common_writer)
    laz_writer.update({'filename': str(laz_out), 'compression': 'laszip'})
    stages.append(laz_writer)
    if temp_las is not None:
        las_writer = dict(common_writer)
        las_writer.update({'filename': str(temp_las), 'compression': 'none'})
        stages.append(las_writer)
    return {'pipeline': stages}


def _find_expressionstats_statistics(obj):
    """Find the first filters.expressionstats statistic list in PDAL metadata."""
    if isinstance(obj, dict):
        stats = obj.get('statistic')
        if isinstance(stats, list) and any(isinstance(x, dict) and 'expression' in x for x in stats):
            return stats
        for value in obj.values():
            found = _find_expressionstats_statistics(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_expressionstats_statistics(value)
            if found is not None:
                return found
    return None


def _expressionstat_count(item):
    if not isinstance(item, dict):
        return 0
    bins = item.get('bins')
    if not isinstance(bins, list):
        return 0
    total = 0
    for b in bins:
        try:
            total += int(b.get('count', 0))
        except Exception:
            pass
    return total


def synthetic_flag_qa(laz_path: Path):
    """Validate ReturnNumber=0 / Synthetic consistency in a delivered LAZ.

    Returns final-output counts and classification breakdowns for both
    Synthetic and non-Synthetic ReturnNumber=0 points. This is QA-only.
    """
    if not laz_path.is_file() or laz_path.stat().st_size == 0:
        return {
            'return0_total': 0,
            'return0_synthetic': 0,
            'return0_non_synthetic': 0,
            'synthetic_by_class': {},
            'non_synthetic_by_class': {},
            'status': 'missing',
        }

    with tempfile.TemporaryDirectory(prefix='atco_synthetic_flag_qa_') as tmp:
        tmp = Path(tmp)
        manifest = tmp / 'pipeline.json'
        metadata = tmp / 'metadata.json'
        pipeline = {
            'pipeline': [
                {'type': 'readers.las', 'filename': str(laz_path), 'tag': 'reader'},
                {
                    'type': 'filters.expressionstats',
                    'inputs': ['reader'],
                    'dimension': 'Classification',
                    'expressions': [
                        'ReturnNumber == 0',
                        'ReturnNumber == 0 && Synthetic == 1',
                        'ReturnNumber == 0 && Synthetic == 0',
                    ],
                    'tag': 'synthetic_flag_stats',
                },
                {'type': 'writers.null', 'inputs': ['synthetic_flag_stats']},
            ]
        }
        manifest.write_text(json.dumps(pipeline, indent=2), encoding='utf-8')
        run(['pdal', 'pipeline', str(manifest), '--metadata', str(metadata)])

        data = json.loads(metadata.read_text(encoding='utf-8'))
        stats = _find_expressionstats_statistics(data) or []
        by_position = {}
        for item in stats:
            try:
                by_position[int(item.get('position', len(by_position)))] = item
            except Exception:
                pass

        total_item = by_position.get(0, {})
        synth_item = by_position.get(1, {})
        nonsynth_item = by_position.get(2, {})

        synthetic_by_class = {}
        for b in synth_item.get('bins', []) if isinstance(synth_item, dict) else []:
            try:
                cls = int(float(b.get('value')))
                count = int(b.get('count', 0))
                synthetic_by_class[cls] = synthetic_by_class.get(cls, 0) + count
            except Exception:
                pass

        non_synthetic_by_class = {}
        for b in nonsynth_item.get('bins', []) if isinstance(nonsynth_item, dict) else []:
            try:
                cls = int(float(b.get('value')))
                count = int(b.get('count', 0))
                non_synthetic_by_class[cls] = non_synthetic_by_class.get(cls, 0) + count
            except Exception:
                pass

        return0_total = _expressionstat_count(total_item)
        return0_synthetic = _expressionstat_count(synth_item)
        return0_non_synthetic = _expressionstat_count(nonsynth_item)

        return {
            'return0_total': return0_total,
            'return0_synthetic': return0_synthetic,
            'return0_non_synthetic': return0_non_synthetic,
            'synthetic_by_class': synthetic_by_class,
            'non_synthetic_by_class': non_synthetic_by_class,
            'status': 'OK' if return0_non_synthetic == 0 else 'REVIEW',
        }


def safe_synthetic_flag_qa(laz_path: Path):
    try:
        return synthetic_flag_qa(laz_path)
    except Exception as exc:
        return {
            'return0_total': 0,
            'return0_synthetic': 0,
            'return0_non_synthetic': 0,
            'synthetic_by_class': {},
            'non_synthetic_by_class': {},
            'status': f'QA_ERROR: {type(exc).__name__}: {exc}',
        }


def run_pdal_segment(seg_row, chosen, laz_out: Path, temp_las: Path | None, crs):
    laz_out.parent.mkdir(parents=True, exist_ok=True)
    if temp_las is not None:
        temp_las.parent.mkdir(parents=True, exist_ok=True)
    pipeline = pipeline_for_segment(seg_row, chosen, laz_out, temp_las, crs)
    with tempfile.TemporaryDirectory(prefix='atco_lidar_pdal_') as tmp:
        manifest = Path(tmp) / 'pipeline.json'
        manifest.write_text(json.dumps(pipeline, indent=2), encoding='utf-8')
        try:
            run(['pdal', 'pipeline', str(manifest)])
            if any(r.get('assigned_crs_wkt') for r in chosen):
                for output in (laz_out, temp_las):
                    if output is None:
                        continue
                    header = pdal_summary(output)
                    if not header['crs_wkt'] or not CRS.from_wkt(header['crs_wkt']).equals(CRS.from_user_input(crs)):
                        raise RuntimeError(f'Output CRS verification failed after missing-header assignment: {output}')
                    print(f'Output CRS verification PASS: {output.name}', flush=True)
        except Exception:
            if laz_out.exists():
                laz_out.unlink()
            if temp_las is not None and temp_las.exists():
                temp_las.unlink()
            raise


def temp_las_from_existing_laz(laz_path: Path, temp_las: Path, crs):
    """Create a conservative uncompressed LAS specifically for EzLAS.

    We preserve the LAS version/point format/scale/offset/header from the final LAZ,
    but deliberately do not forward arbitrary VLRs. Projection metadata is rebuilt
    from the known compound CRS. This avoids carrying writer-specific metadata into
    the standalone Esri optimizer.
    """
    temp_las.parent.mkdir(parents=True, exist_ok=True)
    pipeline = {'pipeline': [
        {'type': 'readers.las', 'filename': str(laz_path), 'tag': 'reader'},
        {'type': 'writers.las', 'inputs': ['reader'], 'filename': str(temp_las),
         'compression': 'none',
         'forward': 'header,scale,offset',
         'extra_dims': 'all',
         'a_srs': CRS.from_user_input(crs).to_wkt()},
    ]}
    with tempfile.TemporaryDirectory(prefix='atco_lidar_pdal_') as tmp:
        manifest = Path(tmp) / 'pipeline.json'
        manifest.write_text(json.dumps(pipeline, indent=2), encoding='utf-8')
        run(['pdal', 'pipeline', str(manifest)])


def _ezlas_logs(folder: Path):
    if not folder.exists():
        return []
    files = []
    for pattern in ('EzLasLog*.log', 'EzLasLog*.txt', 'EzLASLog*.log', 'EzLASLog*.txt'):
        files.extend(folder.glob(pattern))
    unique = {str(p).casefold(): p for p in files}
    return sorted(unique.values(), key=lambda p: p.stat().st_mtime_ns)


def _tail_text(path: Path, chars=5000):
    try:
        return path.read_text(encoding='utf-8', errors='replace')[-chars:]
    except Exception:
        return ''


def _collect_zlas(zlas_dir: Path, expected_blocks):
    created = []
    missing = []
    # EzLAS normally writes directly to the requested output folder. Be lenient on
    # extension case and search one level recursively in case a build preserves a subfolder.
    candidates = list(zlas_dir.rglob('*.zlas')) + list(zlas_dir.rglob('*.ZLAS'))
    by_stem = {p.stem.casefold(): p for p in candidates}
    for block in expected_blocks:
        got = by_stem.get(str(block).casefold())
        if got is None:
            missing.append(str(block))
        else:
            created.append(got)
    return created, missing


def _move_ezlas_logs(zlas_dir: Path, qa_ezlas_dir: Path, prefix: str):
    qa_ezlas_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for p in _ezlas_logs(zlas_dir):
        target = qa_ezlas_dir / f'{prefix}_{p.name}'
        n = 1
        while target.exists():
            target = qa_ezlas_dir / f'{prefix}_{n}_{p.name}'
            n += 1
        try:
            shutil.move(str(p), str(target))
            moved.append(target)
        except Exception:
            pass
    return moved


def _clean_zlas_delivery_folder(zlas_dir: Path, qa_ezlas_dir: Path, prefix='cleanup'):
    """Keep the client zLAS delivery folder limited to .zlas files only."""
    if not zlas_dir.exists():
        return []
    qa_ezlas_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for p in list(zlas_dir.iterdir()):
        if p.is_dir() or p.suffix.casefold() == '.zlas':
            continue
        target = qa_ezlas_dir / f'{prefix}_{p.name}'
        n = 1
        while target.exists():
            target = qa_ezlas_dir / f'{prefix}_{n}_{p.name}'
            n += 1
        try:
            shutil.move(str(p), str(target))
            moved.append(target)
        except Exception:
            pass
    return moved


def _ezlas_verify(zlas_file: Path, ezlas: Path, zlas_dir: Path, qa_ezlas_dir: Path):
    """Use EzLAS' own /V mode to verify the generated zLAS."""
    proc = run([str(ezlas), str(zlas_file), str(zlas_dir), '/V', '/ES'], check=False, hide_windows=True)
    logs = _move_ezlas_logs(zlas_dir, qa_ezlas_dir, f'verify_{zlas_file.stem}')
    log_tail = _tail_text(logs[-1]) if logs else ''
    invalid = bool(re.search(r'Invalid files\\s*[:=]?\\s*[1-9]\\d*', log_tail, flags=re.I))
    return proc.returncode == 0 and not invalid, proc, logs


def convert_zlas_batch(temp_dir: Path, zlas_dir: Path, ezlas: Path, expected_blocks, crs, qa_dir: Path | None = None):
    """Convert and verify zLAS in parallel using isolated work folders."""
    zlas_dir.mkdir(parents=True, exist_ok=True)
    qa_ezlas_dir = (qa_dir or temp_dir.parent) / 'EzLAS'
    qa_ezlas_dir.mkdir(parents=True, exist_ok=True)
    work_root = qa_ezlas_dir / 'work'
    work_root.mkdir(parents=True, exist_ok=True)

    help_proc = run([str(ezlas), '/?'], check=False, hide_windows=True)
    (qa_ezlas_dir / 'ezlas_help.txt').write_text(
        (help_proc.stdout or '') + '\n' + (help_proc.stderr or ''),
        encoding='utf-8', errors='replace'
    )

    total = len(expected_blocks)
    workers = resolve_parallel_workers(configured_parallel_workers(), total)
    results_by_block = {}
    created_by_block = {}

    def _one(block):
        src = temp_dir / f'{block}.las'
        final_out = zlas_dir / f'{block}.zlas'
        block_work = work_root / str(block)
        if block_work.exists():
            shutil.rmtree(block_work, ignore_errors=True)
        block_work.mkdir(parents=True, exist_ok=True)

        result = {
            'BlockNum': block,
            'CONVERT_RETURN': None,
            'CREATED': False,
            'VERIFY': False,
            'ERROR': '',
        }

        if not src.is_file():
            result['ERROR'] = 'temporary LAS missing'
            return result, None

        proc = run(
            [str(ezlas), str(src), str(block_work), '/O', '/PS=S', '/C=100'],
            check=False, hide_windows=True
        )
        result['CONVERT_RETURN'] = proc.returncode
        convert_logs = _move_ezlas_logs(block_work, qa_ezlas_dir, f'convert_{block}')

        (qa_ezlas_dir / f'{block}_command.txt').write_text(
            'COMMAND:\n' +
            ' '.join([str(ezlas), str(src), str(block_work), '/O', '/PS=S', '/C=100']) +
            f'\n\nRETURN_CODE={proc.returncode}\n\nSTDOUT\n{proc.stdout or ""}\n\nSTDERR\n{proc.stderr or ""}\n',
            encoding='utf-8', errors='replace'
        )

        made_candidates = sorted(block_work.glob('*.zlas')) + sorted(block_work.glob('*.ZLAS'))
        made = made_candidates[0] if made_candidates else None
        if not made or not made.is_file() or made.stat().st_size == 0:
            tail = _tail_text(convert_logs[-1]) if convert_logs else ''
            result['ERROR'] = tail[-1500:] if tail else f'EzLAS created no output (return {proc.returncode})'
            return result, None

        result['CREATED'] = True
        verified, verify_proc, _verify_logs = _ezlas_verify(
            made, ezlas, block_work, qa_ezlas_dir
        )
        result['VERIFY'] = verified
        if not verified:
            result['ERROR'] = f'zLAS verification failed (return {verify_proc.returncode})'
            return result, None

        if final_out.exists():
            final_out.unlink()
        shutil.move(str(made), str(final_out))
        _clean_zlas_delivery_folder(block_work, qa_ezlas_dir, f'work_extra_{block}')
        return result, final_out

    done = 0
    progress_line('zLAS', done, total, workers=workers)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, block): block for block in expected_blocks}
        for fut in as_completed(futures):
            block = futures[fut]
            result, created = fut.result()
            results_by_block[block] = result
            if created:
                created_by_block[block] = created
            done += 1
            progress_line('zLAS', done, total, workers=workers,
                          extra='verified' if created else 'check QA')

    results = [results_by_block[b] for b in expected_blocks]
    created = [created_by_block[b] for b in expected_blocks if b in created_by_block]

    _move_ezlas_logs(zlas_dir, qa_ezlas_dir, 'final')
    _clean_zlas_delivery_folder(zlas_dir, qa_ezlas_dir, 'delivery_extra')

    with (qa_ezlas_dir / 'zlas_validation.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(
            f, fieldnames=['BlockNum', 'CONVERT_RETURN', 'CREATED', 'VERIFY', 'ERROR']
        )
        w.writeheader()
        w.writerows(results)

    missing = [str(b) for b in expected_blocks if str(b) not in created_by_block]
    if missing:
        raise RuntimeError(
            f'EzLAS did not create and verify {len(missing)}/{len(expected_blocks)} zLAS file(s): '
            + ', '.join(missing[:20]) +
            f'. Diagnostics: {qa_ezlas_dir}'
        )

    for p in temp_dir.glob('*.las'):
        p.unlink(missing_ok=True)
    for p in temp_dir.glob('*.prj'):
        p.unlink(missing_ok=True)
    try:
        temp_dir.rmdir()
    except OSError:
        pass
    shutil.rmtree(work_root, ignore_errors=True)

    return created


def validate_pdal_available():
    pdal = shutil.which('pdal')
    if not pdal:
        raise RuntimeError('PDAL CLI not found in PATH. Run through ATCO_LIDAR_PROCESSOR.bat using the existing GIS environment.')
    p = run(['pdal', '--version'], check=False)
    if p.returncode:
        raise RuntimeError('PDAL executable was found but failed to run.')
    return (p.stdout or p.stderr).strip()


def process(aoi_input: Path, source_folder: Path | None, output_root: Path, *, make_zlas=False, make_index=False, overwrite=False, ezlas_exe=None):
    pdal_version = validate_pdal_available() if source_folder is not None else None

    polygon = find_polygon_shp(aoi_input)
    circuit = circuit_from_polygon(polygon)
    aoi = load_aoi_revision(circuit, polygon)
    circuit = aoi['entity_id']
    segment_shp, seg = load_segments(circuit, aoi['revision_code'])
    if not CRS.from_user_input(seg.crs).equals(aoi['crs']):
        raise ValueError(f'Segment CRS does not match central AOI CRS for {circuit}')

    base = output_base(Path(output_root), circuit)
    laz_dir = base / product_folder('LAZ')
    zlas_dir = base / product_folder('zLAS')
    qa_dir = base / 'QA' / 'LiDAR'
    temp_dir = qa_dir / 'temp_las_for_zlas'
    qa_dir.mkdir(parents=True, exist_ok=True)

    tile_index_path = None
    if make_index and source_folder is None:
        tile_index_path, _ = build_tile_index_shp(circuit, seg, base, laz_dir=laz_dir if laz_dir.is_dir() else None)
        print(f'Tile Index SHP: {tile_index_path}', flush=True)

    if source_folder is None:
        summary = {
            'created_at': now_iso(), 'circuit': circuit, 'aoi_revision': aoi['revision_code'],
            'segments_source': str(segment_shp), 'tile_index_shp': str(tile_index_path) if tile_index_path else None,
            'mode': 'tile_index_only', 'output_root': str(base),
        }
        (qa_dir / 'execution.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        return summary

    with sqlite3.connect(str(DB_PATH)) as con:
        known_entities = [row[0] for row in con.execute("SELECT entity_id FROM entities")]
    files, selection_mode = select_entity_clouds(source_folder, circuit, known_entities)
    print(f"LiDAR source selection for {circuit}: {selection_mode}, {len(files)} file(s)", flush=True)
    inventory, point_cloud_crs, cloud_crs_meta = cloud_inventory(files, aoi['crs'], qa_dir)

    ezlas = None
    if make_zlas:
        ezlas = Path(ezlas_exe) if ezlas_exe else find_ezlas()
        if not ezlas or not ezlas.is_file():
            raise FileNotFoundError(
                'EzLAS.exe was not found. Place it in tools\\EzLAS\\EzLAS.exe, set ATCO_EZLAS_EXE, '
                'or provide --ezlas-exe. LAZ-only mode does not require it.'
            )
        print(f'EzLAS: {ezlas}', flush=True)

    rows = []
    zlas_pending = []
    if make_zlas:
        temp_dir.mkdir(parents=True, exist_ok=True)
        for stale in list(temp_dir.glob('*.las')) + list(temp_dir.glob('*.prj')):
            stale.unlink(missing_ok=True)

    segment_jobs = []
    for _, row in seg.iterrows():
        segment_jobs.append((str(row.PointCloudName), row))

    laz_workers = resolve_parallel_workers(configured_parallel_workers(), len(segment_jobs))

    def _segment_job(job):
        block, row = job
        laz_out = laz_dir / f'{block}.laz'
        zlas_out = zlas_dir / f'{block}.zlas'
        chosen = intersecting_sources(row.geometry, inventory)
        rec = {
            'BlockNum': block,
            'SOURCE_FILES': len(chosen),
            'LAZ': '',
            'zLAS': 'not requested' if not make_zlas else '',
            'NOISE_FILTER': 'Classification != 7',
            'SYNTHETIC_FLAG_RULE': 'QA only - no flag edits',
            'RETURN0_TOTAL': '',
            'RETURN0_SYNTHETIC': '',
            'RETURN0_NON_SYNTHETIC': '',
            'SYNTHETIC_FLAG_QA': '',
            '_SYNTHETIC_BY_CLASS': {},
            '_NON_SYNTHETIC_BY_CLASS': {},
        }

        if not chosen:
            rec['LAZ'] = 'no input coverage'
            if make_zlas:
                rec['zLAS'] = 'no input coverage'
            return block, rec, False

        need_laz = overwrite or not laz_out.exists()
        existing_flag_qa = None

        # Cached LAZ is not rebuilt because of Synthetic flags. Flag consistency
        # is a QA observation only and must never change source semantics automatically.
        if not need_laz:
            existing_flag_qa = safe_synthetic_flag_qa(laz_out)

        existing_zlas_valid = False
        if make_zlas and not overwrite and zlas_out.is_file() and zlas_out.stat().st_size > 0:
            try:
                existing_zlas_valid, _verify_proc, _verify_logs = _ezlas_verify(
                    zlas_out, ezlas, zlas_dir, qa_dir / 'EzLAS'
                )
            except Exception:
                existing_zlas_valid = False
            if not existing_zlas_valid:
                zlas_out.unlink(missing_ok=True)

        # If LAZ is rebuilt, its zLAS equivalent must also be rebuilt.
        need_zlas = make_zlas and (overwrite or need_laz or not existing_zlas_valid)
        temp_las = temp_dir / f'{block}.las' if need_zlas else None

        if need_laz:
            run_pdal_segment(row, chosen, laz_out, None, point_cloud_crs)
            rec['LAZ'] = 'created'
            flag_qa = safe_synthetic_flag_qa(laz_out)
        else:
            rec['LAZ'] = 'exists'
            flag_qa = existing_flag_qa or safe_synthetic_flag_qa(laz_out)

        rec['SYNTHETIC_FLAG_RULE'] = 'QA only - no flag edits'
        rec['RETURN0_TOTAL'] = int(flag_qa.get('return0_total', 0))
        rec['RETURN0_SYNTHETIC'] = int(flag_qa.get('return0_synthetic', 0))
        rec['RETURN0_NON_SYNTHETIC'] = int(flag_qa.get('return0_non_synthetic', 0))
        rec['SYNTHETIC_FLAG_QA'] = flag_qa.get('status', 'UNKNOWN')
        rec['_SYNTHETIC_BY_CLASS'] = flag_qa.get('synthetic_by_class', {})
        rec['_NON_SYNTHETIC_BY_CLASS'] = flag_qa.get('non_synthetic_by_class', {})

        if need_zlas:
            temp_las_from_existing_laz(laz_out, temp_las, point_cloud_crs)

        if make_zlas:
            rec['zLAS'] = 'pending conversion' if need_zlas else 'exists + verified'

        return block, rec, need_zlas

    rows_by_block = {}
    pending_blocks = []
    done = 0
    progress_line('LAZ', done, len(segment_jobs), workers=laz_workers)
    with ThreadPoolExecutor(max_workers=laz_workers) as ex:
        futures = {ex.submit(_segment_job, job): job[0] for job in segment_jobs}
        for fut in as_completed(futures):
            block, rec, need_zlas = fut.result()
            rows_by_block[block] = rec
            if need_zlas:
                pending_blocks.append(block)
            done += 1
            progress_line('LAZ', done, len(segment_jobs), workers=laz_workers)

    rows = [rows_by_block[block] for block, _row in segment_jobs]
    zlas_pending = [(block, rows_by_block[block]) for block, _row in segment_jobs if block in set(pending_blocks)]

    if make_zlas and zlas_pending:
        blocks = [b for b, _ in zlas_pending]
        print(f'zLAS: converting/verifying {len(blocks)} tile(s) in parallel...', flush=True)
        created = convert_zlas_batch(temp_dir, zlas_dir, ezlas, blocks, horizontal_crs(point_cloud_crs), qa_dir=qa_dir)
        made = {p.stem for p in created}
        for block, rec in zlas_pending:
            rec['zLAS'] = 'created' if block in made else 'failed'
        progress_line('zLAS', len(created), len(blocks), extra='batch complete')
    if make_zlas and temp_dir.exists():
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    if make_index:
        tile_index_path, _ = build_tile_index_shp(circuit, seg, base, laz_dir=laz_dir)
        print(f'Tile Index SHP: {tile_index_path}', flush=True)

    report_fields = [
        'BlockNum', 'SOURCE_FILES', 'LAZ', 'zLAS', 'NOISE_FILTER',
        'SYNTHETIC_FLAG_RULE', 'RETURN0_TOTAL', 'RETURN0_SYNTHETIC',
        'RETURN0_NON_SYNTHETIC', 'SYNTHETIC_FLAG_QA',
    ]
    with (qa_dir / 'processing_report.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=report_fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)

    with (qa_dir / 'return0_flag_by_class.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['BlockNum', 'Classification', 'Synthetic', 'Points'])
        w.writeheader()
        for rec in rows:
            for cls, count in sorted((rec.get('_SYNTHETIC_BY_CLASS') or {}).items()):
                w.writerow({'BlockNum': rec.get('BlockNum'), 'Classification': cls, 'Synthetic': 1, 'Points': count})
            for cls, count in sorted((rec.get('_NON_SYNTHETIC_BY_CLASS') or {}).items()):
                w.writerow({'BlockNum': rec.get('BlockNum'), 'Classification': cls, 'Synthetic': 0, 'Points': count})

    summary = {
        'created_at': now_iso(),
        'circuit': circuit,
        'aoi_revision': aoi['revision_code'],
        'aoi_source_selected': str(polygon),
        'central_aoi_source': aoi['source_folder'],
        'segments_source': str(segment_shp),
        'source_point_cloud_folder': str(source_folder),
        'source_file_count': len(files),
        'segment_count': len(seg),
        'point_cloud_horizontal_crs': cloud_crs_meta.get('horizontal_crs'),
        'point_cloud_vertical_crs': cloud_crs_meta.get('vertical_crs'),
        'point_cloud_output_crs_wkt': point_cloud_crs.to_wkt(),
        'noise_removed': [7],
        'synthetic_flag_qa': {
            'edits_enabled': False,
            'rule': 'Report ReturnNumber == 0 by Synthetic flag and Classification; do not alter points',
            'review_condition': 'RETURN0_NON_SYNTHETIC > 0',
            'qa_report': str(qa_dir / 'return0_flag_by_class.csv'),
        },
        'pdal_version': pdal_version,
        'zlas_requested': bool(make_zlas),
        'ezlas_exe': str(ezlas) if ezlas else None,
        'tile_index_shp': str(tile_index_path) if tile_index_path else None,
        'parallel_workers_config': configured_parallel_workers(),
        'output_root': str(base),
    }
    (qa_dir / 'execution.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'\nDONE: {base}', flush=True)
    return summary


def interactive():
    print('ATCO - LiDAR Processor v1.2')
    print(f'Central: {CENTRAL_ROOT}')
    print(f'Output : {DEFAULT_OUTPUT_ROOT}')
    print('')
    print('1. LAZ only')
    print('2. LAZ + zLAS')
    print('3. Tile Index SHP only')
    print('4. LAZ + zLAS + Tile Index SHP')
    print('5. Exit')
    choice = input('\nChoose [1-5]: ').strip()
    if choice == '5':
        return 0
    if choice not in ('1', '2', '3', '4'):
        raise SystemExit('Invalid choice')

    aoi_input = input('AOI version folder or *_Polygon.shp: ').strip().strip('"')
    source = None
    if choice in ('1', '2', '4'):
        source = input('Source LAS/LAZ folder: ').strip().strip('"')
    out_default = str(DEFAULT_OUTPUT_ROOT)
    out = input(f'Output root [{out_default}]: ').strip().strip('"') or out_default

    make_zlas = choice in ('2', '4')
    make_index = choice in ('3', '4')
    ezlas = None
    if make_zlas:
        found = find_ezlas()
        if found:
            print(f'EzLAS detected: {found}')
            ezlas = str(found)
        else:
            ezlas = input('EzLAS.exe path: ').strip().strip('"')
            if not Path(ezlas).is_file():
                raise FileNotFoundError(f'EzLAS.exe not found: {ezlas}')

    process(
        Path(aoi_input), Path(source) if source else None, Path(out),
        make_zlas=make_zlas, make_index=make_index, overwrite=False, ezlas_exe=ezlas,
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description='ATCO LiDAR segment processor')
    ap.add_argument('--aoi', help='AOI version folder or exact *_Polygon.shp')
    ap.add_argument('--source', help='Folder containing source LAS/LAZ')
    ap.add_argument('--output-root', default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument('--zlas', action='store_true')
    ap.add_argument('--tile-index', '--tiles-gdb', dest='tile_index', action='store_true', help='Create intermediate Tile Index shapefile for later import into the final delivery GDB')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--ezlas-exe')
    a = ap.parse_args()
    try:
        if not a.aoi:
            return interactive()
        if not a.source and not a.tile_index:
            raise ValueError('--source is required unless --tile-index is the only requested product')
        process(
            Path(a.aoi), Path(a.source) if a.source else None, Path(a.output_root),
            make_zlas=a.zlas, make_index=a.tile_index, overwrite=a.overwrite, ezlas_exe=a.ezlas_exe,
        )
        return 0
    except Exception as exc:
        print(f'\nERROR: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
