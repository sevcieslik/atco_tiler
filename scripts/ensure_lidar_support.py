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


def find_conda():
    explicit = os.environ.get("ATCO_CONDA_BAT", "").strip().strip('"')
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise FileNotFoundError(f"ATCO_CONDA_BAT does not exist: {p}")

    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    for root in (
        Path(r"C:\Tools\Miniconda3"),
        Path(r"C:\Tools\Miniforge3"),
        home / "miniforge3",
        home / "miniconda3",
        local / "miniforge3",
        local / "miniconda3",
        Path(r"C:\ProgramData\miniforge3"),
        Path(r"C:\ProgramData\miniconda3"),
    ):
        for part in ("condabin/conda.bat", "Scripts/conda.exe"):
            candidate = root / part
            if candidate.is_file():
                return candidate
    for exe in ("conda.bat", "conda.exe"):
        found = shutil.which(exe)
        if found:
            return Path(found)
    raise FileNotFoundError(
        "Conda was not found. Install Miniconda or set ATCO_CONDA_BAT "
        "to its conda.bat. The existing ATCO environment is unchanged."
    )


def main():
    if Path(sys.executable).resolve() != (ENV / "python.exe").resolve():
        raise RuntimeError("Optional PDAL installer must run using scripts\\.venv\\python.exe")
    if pdal_ready():
        print("PDAL already available: no download required.", flush=True)
        return 0

    conda = find_conda()
    print(f"PDAL missing: adding it to existing runtime {ENV}", flush=True)
    print(f"Using Conda: {conda}", flush=True)
    install = [
        str(conda), "install", "--yes", "--prefix", str(ENV),
        "--override-channels", "--strict-channel-priority",
        "-c", "conda-forge", "pdal",
    ]
    if conda.suffix.lower() == ".bat":
        if os.name != "nt":
            raise RuntimeError("conda.bat requires Windows")
        install = ["cmd.exe", "/d", "/c", *install]
    proc = subprocess.Popen(install, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            bufsize=1, errors="replace")
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    result = proc.wait()
    if result != 0:
        raise RuntimeError(
            f"Conda PDAL installation failed (exit {result}); "
            "check the Activity Log. No full environment rebuild was requested."
        )
    if not pdal_ready():
        raise RuntimeError("Conda completed, but PDAL cannot be started.")
    print("PDAL installed and validated. Continuing selected build.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)
