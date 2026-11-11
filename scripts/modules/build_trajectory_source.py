from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception as exc:
    print("ERROR: pyarrow is required.")
    print("Install with: python -m pip install pyarrow")
    raise

DEFAULT_FLIGHT_ROOT = Path(r"W:\NMCP26054\products\flight")
DEFAULT_DEST_ROOT = Path(r"W:\APPS\PIT\ATCO\source\trajectories")

SBET_DTYPE = np.dtype([
    ("gps_sow", "<f8"),
    ("lat_rad", "<f8"),
    ("lon_rad", "<f8"),
    ("alt_m", "<f8"),
    ("vel_n", "<f8"),
    ("vel_e", "<f8"),
    ("vel_down", "<f8"),
    ("roll_rad", "<f8"),
    ("pitch_rad", "<f8"),
    ("heading_rad", "<f8"),
    ("wander_rad", "<f8"),
    ("acc_x", "<f8"),
    ("acc_y", "<f8"),
    ("acc_z", "<f8"),
    ("ang_rate_x", "<f8"),
    ("ang_rate_y", "<f8"),
    ("ang_rate_z", "<f8"),
])
SBET_RECORD_BYTES = SBET_DTYPE.itemsize
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
GPS_UTC_OFFSET_2026 = 18  # seconds; project year is 2026

FLIGHT_RE = re.compile(
    r"^(?P<prefix>[A-Za-z]+)(?P<doy>\d{3})(?P<yy>\d{2})(?:_(?P<suffix>.+))?$"
)
VER_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)


@dataclass
class FlightSource:
    trajectory_id: str
    flight_dir: Path
    version_dir: Path
    version_num: int
    sbet_path: Path


def parse_args():
    p = argparse.ArgumentParser(
        description="Build ATCO trajectory/SBET Parquet source from processed trajectory products."
    )
    p.add_argument("--flight-root", default=str(DEFAULT_FLIGHT_ROOT))
    p.add_argument("--dest-root", default=str(DEFAULT_DEST_ROOT))
    p.add_argument("--all-versions", action="store_true",
                   help="Build every version found, not only latest v# for each flight.")
    p.add_argument("--force", action="store_true",
                   help="Rebuild parquet even if source fingerprint is unchanged.")
    p.add_argument("--chunk-records", type=int, default=250_000)
    return p.parse_args()


def version_number(path: Path) -> int | None:
    m = VER_RE.match(path.name)
    return int(m.group(1)) if m else None


def pick_sbet(version_dir: Path, flight_name: str) -> Path | None:
    candidates = sorted(version_dir.glob("sbet*.out"))
    if not candidates:
        candidates = sorted(version_dir.glob("SBET*.out"))
    if not candidates:
        return None
    exact = [p for p in candidates if flight_name.lower() in p.stem.lower()]
    if exact:
        return exact[0]
    return candidates[0]


def discover_sources(flight_root: Path, all_versions: bool) -> list[FlightSource]:
    out: list[FlightSource] = []
    if not flight_root.exists():
        raise FileNotFoundError(f"Flight products root not found: {flight_root}")

    for flight_dir in sorted(p for p in flight_root.iterdir() if p.is_dir()):
        traj_root = flight_dir / "19_processed_trajectory"
        if not traj_root.exists():
            continue
        versions = []
        for vdir in traj_root.iterdir():
            if not vdir.is_dir():
                continue
            vn = version_number(vdir)
            if vn is None:
                continue
            sbet = pick_sbet(vdir, flight_dir.name)
            if sbet:
                versions.append((vn, vdir, sbet))
        if not versions:
            continue
        versions.sort(key=lambda x: x[0])
        selected = versions if all_versions else [versions[-1]]
        for vn, vdir, sbet in selected:
            out.append(FlightSource(
                trajectory_id=flight_dir.name,
                flight_dir=flight_dir,
                version_dir=vdir,
                version_num=vn,
                sbet_path=sbet,
            ))
    return out


def parse_flight_name(name: str) -> dict:
    m = FLIGHT_RE.match(name)
    if not m:
        return {
            "date_code_ok": False,
            "flight_prefix": None,
            "flight_date": None,
            "flight_code": name.split("_")[0] if "_" in name else name,
            "flightline_code": name.split("_", 1)[1] if "_" in name else None,
        }
    yy = int(m.group("yy"))
    year = 2000 + yy
    doy = int(m.group("doy"))
    try:
        dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
        if dt.year != year:
            raise ValueError
    except Exception:
        dt = None
    return {
        "date_code_ok": dt is not None,
        "flight_prefix": m.group("prefix"),
        "flight_date": dt,
        "flight_code": f'{m.group("prefix")}{m.group("doy")}{m.group("yy")}',
        "flightline_code": m.group("suffix"),
    }


def gps_week_for_date(dt: datetime) -> int:
    return int((dt.date() - GPS_EPOCH.date()).days // 7)


def sbet_validate(path: Path) -> tuple[int, np.memmap]:
    size = path.stat().st_size
    if size <= 0 or size % SBET_RECORD_BYTES:
        raise ValueError(
            f"{path.name}: invalid SBET size {size:,}; expected multiple of "
            f"{SBET_RECORD_BYTES} bytes."
        )
    arr = np.memmap(path, dtype=SBET_DTYPE, mode="r")
    if len(arr) == 0:
        raise ValueError(f"{path.name}: no SBET records.")
    if not np.isfinite(arr["gps_sow"][0]) or not np.isfinite(arr["gps_sow"][-1]):
        raise ValueError(f"{path.name}: invalid GPS time values.")
    return len(arr), arr


def fingerprint(src: FlightSource) -> dict:
    st = src.sbet_path.stat()
    return {
        "source_path": str(src.sbet_path),
        "source_size": st.st_size,
        "source_mtime_ns": st.st_mtime_ns,
        "trajectory_id": src.trajectory_id,
        "source_version": src.version_num,
    }


def already_current(meta_path: Path, fp: dict) -> bool:
    try:
        old = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    keys = ("source_path", "source_size", "source_mtime_ns",
            "trajectory_id", "source_version")
    return all(old.get(k) == fp.get(k) for k in keys)


def build_parquet(src: FlightSource, dest_root: Path, chunk_records: int, force: bool) -> dict:
    records, arr = sbet_validate(src.sbet_path)
    parsed = parse_flight_name(src.trajectory_id)

    out_dir = dest_root / src.trajectory_id / f"v{src.version_num}"
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "sbet.parquet"
    meta_path = out_dir / "source.json"
    fp = fingerprint(src)

    if parquet_path.exists() and not force and already_current(meta_path, fp):
        print(f"SKIP {src.trajectory_id} v{src.version_num}: current")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    if not parsed["date_code_ok"]:
        raise ValueError(
            f"{src.trajectory_id}: cannot derive date from flight folder name. "
            "Expected pattern similar to AB23926_G521_1A."
        )

    flight_date = parsed["flight_date"]
    gps_week = gps_week_for_date(flight_date)
    week_start = GPS_EPOCH + timedelta(weeks=gps_week)

    first_sow = float(arr["gps_sow"][0])
    last_sow = float(arr["gps_sow"][-1])

    # Validate date encoded in folder against GPS seconds-of-week weekday.
    gps_day_from_sow = int(first_sow // 86400) % 7  # Sunday=0
    gps_day_from_name = (flight_date.weekday() + 1) % 7  # Python Mon=0 -> Sunday=0
    weekday_match = gps_day_from_sow == gps_day_from_name

    # Estimate native frequency from a small prefix.
    sample_n = min(records, 50_000)
    dt = np.diff(np.asarray(arr["gps_sow"][:sample_n], dtype=np.float64))
    dt = dt[(dt > 0) & np.isfinite(dt)]
    median_dt = float(np.median(dt)) if len(dt) else math.nan
    sample_hz = (1.0 / median_dt) if median_dt > 0 else math.nan

    schema = pa.schema([
        ("trajectory_id", pa.string()),
        ("flight_code", pa.string()),
        ("flightline_code", pa.string()),
        ("source_version", pa.int16()),
        ("gps_week", pa.int32()),
        ("gps_sow", pa.float64()),
        ("gps_seconds", pa.float64()),
        ("las_adjusted_gps_time", pa.float64()),
        ("gps_datetime", pa.timestamp("us", tz="UTC")),
        ("utc_datetime", pa.timestamp("us", tz="UTC")),
        ("latitude_deg", pa.float64()),
        ("longitude_deg", pa.float64()),
        ("sbet_altitude_m", pa.float64()),
        ("velocity_north_mps", pa.float64()),
        ("velocity_east_mps", pa.float64()),
        ("velocity_down_mps", pa.float64()),
        ("roll_deg", pa.float64()),
        ("pitch_deg", pa.float64()),
        ("heading_deg", pa.float64()),
        ("wander_deg", pa.float64()),
        ("accel_x_mps2", pa.float64()),
        ("accel_y_mps2", pa.float64()),
        ("accel_z_mps2", pa.float64()),
        ("angular_rate_x_deg_s", pa.float64()),
        ("angular_rate_y_deg_s", pa.float64()),
        ("angular_rate_z_deg_s", pa.float64()),
    ])

    tmp = parquet_path.with_suffix(".parquet.tmp")
    if tmp.exists():
        tmp.unlink()

    writer = pq.ParquetWriter(
        tmp,
        schema=schema,
        compression="zstd",
        use_dictionary=["trajectory_id", "flight_code", "flightline_code"],
        write_statistics=True,
    )

    try:
        for start in range(0, records, chunk_records):
            stop = min(start + chunk_records, records)
            a = arr[start:stop]
            sow = np.asarray(a["gps_sow"], dtype=np.float64)
            gps_seconds = gps_week * 604800.0 + sow
            adjusted = gps_seconds - 1_000_000_000.0

            # Store timestamps as integer microseconds to avoid Python object arrays.
            gps_us = np.rint(gps_seconds * 1_000_000.0).astype(np.int64)
            epoch_us = int(GPS_EPOCH.timestamp() * 1_000_000)
            unix_gps_us = epoch_us + gps_us
            unix_utc_us = unix_gps_us - GPS_UTC_OFFSET_2026 * 1_000_000

            n = stop - start
            cols = [
                pa.array([src.trajectory_id] * n, type=pa.string()),
                pa.array([parsed["flight_code"]] * n, type=pa.string()),
                pa.array([parsed["flightline_code"]] * n, type=pa.string()),
                pa.array(np.full(n, src.version_num, dtype=np.int16)),
                pa.array(np.full(n, gps_week, dtype=np.int32)),
                pa.array(sow),
                pa.array(gps_seconds),
                pa.array(adjusted),
                pa.array(unix_gps_us, type=pa.timestamp("us", tz="UTC")),
                pa.array(unix_utc_us, type=pa.timestamp("us", tz="UTC")),
                pa.array(np.degrees(np.asarray(a["lat_rad"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["lon_rad"], dtype=np.float64))),
                pa.array(np.asarray(a["alt_m"], dtype=np.float64)),
                pa.array(np.asarray(a["vel_n"], dtype=np.float64)),
                pa.array(np.asarray(a["vel_e"], dtype=np.float64)),
                pa.array(np.asarray(a["vel_down"], dtype=np.float64)),
                pa.array(np.degrees(np.asarray(a["roll_rad"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["pitch_rad"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["heading_rad"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["wander_rad"], dtype=np.float64))),
                pa.array(np.asarray(a["acc_x"], dtype=np.float64)),
                pa.array(np.asarray(a["acc_y"], dtype=np.float64)),
                pa.array(np.asarray(a["acc_z"], dtype=np.float64)),
                pa.array(np.degrees(np.asarray(a["ang_rate_x"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["ang_rate_y"], dtype=np.float64))),
                pa.array(np.degrees(np.asarray(a["ang_rate_z"], dtype=np.float64))),
            ]
            table = pa.Table.from_arrays(cols, schema=schema)
            writer.write_table(table)
            print(f"  {src.trajectory_id} v{src.version_num}: {stop:,}/{records:,}")
    finally:
        writer.close()

    tmp.replace(parquet_path)

    def utc_from_sow(sow: float) -> datetime:
        gps_dt = week_start + timedelta(seconds=sow)
        return gps_dt - timedelta(seconds=GPS_UTC_OFFSET_2026)

    meta = {
        **fp,
        "parquet_path": str(parquet_path),
        "records": records,
        "record_bytes": SBET_RECORD_BYTES,
        "sample_hz": sample_hz,
        "gps_week": gps_week,
        "gps_sow_start": first_sow,
        "gps_sow_end": last_sow,
        "gps_seconds_start": gps_week * 604800.0 + first_sow,
        "gps_seconds_end": gps_week * 604800.0 + last_sow,
        "las_adjusted_gps_start": gps_week * 604800.0 + first_sow - 1_000_000_000.0,
        "las_adjusted_gps_end": gps_week * 604800.0 + last_sow - 1_000_000_000.0,
        "utc_start": utc_from_sow(first_sow).isoformat(),
        "utc_end": utc_from_sow(last_sow).isoformat(),
        "date_from_folder": flight_date.date().isoformat(),
        "weekday_validation": "PASS" if weekday_match else "FAIL",
        "flight_prefix": parsed["flight_prefix"],
        "flight_code": parsed["flight_code"],
        "flightline_code": parsed["flightline_code"],
        "lat_min": float(np.degrees(np.min(arr["lat_rad"]))),
        "lat_max": float(np.degrees(np.max(arr["lat_rad"]))),
        "lon_min": float(np.degrees(np.min(arr["lon_rad"]))),
        "lon_max": float(np.degrees(np.max(arr["lon_rad"]))),
        "alt_min_m": float(np.min(arr["alt_m"])),
        "alt_max_m": float(np.max(arr["alt_m"])),
        "built_utc": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def write_index(rows: list[dict], dest_root: Path):
    if not rows:
        return
    # Keep only current/latest row per trajectory in trajectory_index.
    latest = {}
    for r in rows:
        key = r["trajectory_id"]
        if key not in latest or int(r["source_version"]) > int(latest[key]["source_version"]):
            latest[key] = r
    current_rows = [latest[k] for k in sorted(latest)]

    index_cols = [
        "trajectory_id", "source_version", "flight_code", "flightline_code",
        "date_from_folder", "weekday_validation", "gps_week",
        "gps_sow_start", "gps_sow_end",
        "gps_seconds_start", "gps_seconds_end",
        "las_adjusted_gps_start", "las_adjusted_gps_end",
        "utc_start", "utc_end",
        "records", "sample_hz",
        "lat_min", "lat_max", "lon_min", "lon_max",
        "alt_min_m", "alt_max_m",
        "source_path", "parquet_path",
    ]

    # Build with pyarrow directly.
    dict_rows = [{c: r.get(c) for c in index_cols} for r in current_rows]
    table = pa.Table.from_pylist(dict_rows)
    pq.write_table(table, dest_root / "trajectory_index.parquet", compression="zstd")

    import csv
    with (dest_root / "trajectory_index.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=index_cols)
        w.writeheader()
        w.writerows(dict_rows)

    all_rows = [{c: r.get(c) for c in index_cols} for r in rows]
    pq.write_table(pa.Table.from_pylist(all_rows),
                   dest_root / "trajectory_versions.parquet", compression="zstd")


def main():
    args = parse_args()
    flight_root = Path(args.flight_root)
    dest_root = Path(args.dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    print("ATCO Trajectory Source Builder v1")
    print(f"Flight products: {flight_root}")
    print(f"Destination    : {dest_root}")
    print()

    sources = discover_sources(flight_root, args.all_versions)
    if not sources:
        raise RuntimeError(
            f"No SBET files found under */19_processed_trajectory/v*/ in {flight_root}"
        )

    print(f"Found {len(sources)} trajectory source(s).")
    rows = []
    failures = []

    for i, src in enumerate(sources, 1):
        print(f"\n[{i}/{len(sources)}] {src.trajectory_id} v{src.version_num}")
        print(f"  {src.sbet_path}")
        try:
            rows.append(build_parquet(
                src, dest_root, args.chunk_records, args.force
            ))
        except Exception as exc:
            failures.append((src.trajectory_id, src.version_num, str(exc)))
            print(f"  ERROR: {exc}")

    write_index(rows, dest_root)

    report = {
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "flight_root": str(flight_root),
        "dest_root": str(dest_root),
        "successful": len(rows),
        "failed": len(failures),
        "failures": [
            {"trajectory_id": a, "version": b, "error": c}
            for a, b, c in failures
        ],
    }
    (dest_root / "build_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print("\nDONE")
    print(f"Successful: {len(rows)}")
    print(f"Failed    : {len(failures)}")
    print(f"Index     : {dest_root / 'trajectory_index.parquet'}")
    if failures:
        sys.exit(2)


if __name__ == "__main__":
    main()
