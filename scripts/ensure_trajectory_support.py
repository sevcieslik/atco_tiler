"""Optional one-time PyArrow install for TX Tile Index trajectory attribution.

Runs in a child process before any selected products are processed. The base
ATCO runtime stays lean and no existing LiDAR/imagery products are rebuilt.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from runtime_package_manager import ENV
from runtime_package_manager import install_optional_package

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
        print("TX trajectory index is absent: skipping optional PyArrow installation. "
              "Flight fields will remain blank until the index is published.", flush=True)
        return 0
    if pyarrow_ready():
        print("TX trajectory support (PyArrow) already available.", flush=True)
        return 0
    print(f"TX Tile Index needs PyArrow: installing into existing {ENV} via ATCO Micromamba", flush=True)
    install_optional_package("pyarrow")
    # The new package may not be visible in the parent import cache.
    import importlib
    importlib.invalidate_caches()
    if not pyarrow_ready():
        raise RuntimeError("Micromamba finished, but PyArrow cannot be imported.")
    print("PyArrow installed and validated. Continuing TX Tile Index.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)
