"""ATCO-owned standalone Micromamba package manager for optional GIS dependencies.

No host Conda invocation, no global solver or base-environment changes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv"
MAMBA = ROOT / ".installer" / "micromamba.exe"


def micromamba_executable():
    if os.name != "nt":
        raise RuntimeError("ATCO automatic Micromamba bootstrap is Windows-only.")
    if not MAMBA.is_file():
        bootstrap = ROOT / "bootstrap_micromamba.ps1"
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(bootstrap)]
        result = subprocess.run(cmd, check=False)
        if result.returncode or not MAMBA.is_file():
            raise RuntimeError(
                "Cannot bootstrap standalone ATCO Micromamba. "
                "See installer log and check access to official GitHub releases."
            )
    probe = subprocess.run([str(MAMBA), "--version"], capture_output=True, text=True)
    if probe.returncode:
        raise RuntimeError(f"ATCO Micromamba is invalid: {probe.stderr}")
    return MAMBA


def package_command(package: str):
    mamba = micromamba_executable()
    root_prefix = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ATCO" / "micromamba"
    return [str(mamba), "install", "--yes", "--root-prefix", str(root_prefix),
            "--prefix", str(ENV), "--override-channels",
            "--strict-channel-priority", "-c", "conda-forge", package]
