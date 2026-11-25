"""Optional one-time PyArrow install for TX/DX Tile Index trajectory attribution.

Runs in a child process before any selected products are processed. The base
ATCO runtime stays lean and no existing LiDAR/imagery products are rebuilt.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ensure_lidar_support import ENV, find_conda

INDEX = Path(r"W:\APPS\PIT\ATCO\source\trajectories\trajectory_index.parquet")


def pyarrow_ready():
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def main():
    if Path(sys.executable).resolve() != (ENV / "python.exe").resolve():
        raise RuntimeError("PyArrow installer must run using scripts\\.venv\\python.exe")
    if not INDEX.is_file():
        print("TX/DX trajectory index is absent: skipping optional PyArrow installation. "
              "Flight fields will remain blank until the index is published.", flush=True)
        return 0
    if pyarrow_ready():
        print("TX/DX trajectory support (PyArrow) already available.", flush=True)
        return 0
    conda = find_conda()
    print(f"TX/DX Tile Index needs PyArrow: installing into existing {ENV}", flush=True)
    cmd = [
        str(conda), "install", "--yes", "--prefix", str(ENV),
        "--override-channels", "--strict-channel-priority",
        "-c", "conda-forge", "pyarrow",
    ]
    if conda.suffix.lower() == ".bat":
        if os.name != "nt":
            raise RuntimeError("conda.bat requires Windows")
        cmd = ["cmd.exe", "/d", "/c", *cmd]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               bufsize=1, errors="replace")
    for line in process.stdout:
        print(line.rstrip(), flush=True)
    if process.wait():
        raise RuntimeError("PyArrow Conda installation failed; see Activity Log.")
    # The new package may not be visible in the parent import cache.
    import importlib
    importlib.invalidate_caches()
    if not pyarrow_ready():
        raise RuntimeError("Conda finished, but PyArrow cannot be imported.")
    print("PyArrow installed and validated. Continuing TX/DX Tile Index.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)
