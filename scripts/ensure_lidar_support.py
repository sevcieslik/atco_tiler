"""Install optional PDAL into ATCO's existing project-local Conda environment.

Called automatically by the GUI when LAZ/zLAS is requested. The base runtime
remains lightweight. Run as a child process so the UI does not block.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from runtime_package_manager import package_command

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv"


def pdal_executable():
    for candidate in (
        ENV / "Library" / "bin" / "pdal.exe",
        ENV / "Scripts" / "pdal.exe",
        ENV / "bin" / "pdal",
    ):
        if candidate.is_file():
            return candidate
    executable = shutil.which("pdal")
    return Path(executable) if executable else None


def pdal_ready():
    binary = pdal_executable()
    if not binary:
        return False
    try:
        return subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True,
            timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False



def main():
    if Path(sys.executable).resolve() != (ENV / "python.exe").resolve():
        raise RuntimeError("Optional PDAL installer must run using scripts\\.venv\\python.exe")
    if pdal_ready():
        print("PDAL already available: no download required.", flush=True)
        return 0

    print(f"PDAL missing: adding it to existing runtime {ENV} via ATCO Micromamba", flush=True)
    install = package_command("pdal")
    proc = subprocess.Popen(install, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            bufsize=1, errors="replace")
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    result = proc.wait()
    if result != 0:
        raise RuntimeError(
            f"ATCO Micromamba PDAL installation failed (exit {result}); "
            "check the Activity Log. No full environment rebuild was requested."
        )
    if not pdal_ready():
        raise RuntimeError("Micromamba completed, but PDAL cannot be started.")
    print("PDAL installed and validated. Continuing selected build.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)
