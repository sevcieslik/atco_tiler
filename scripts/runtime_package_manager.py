"""ATCO-owned standalone Micromamba package manager for optional GIS dependencies.

No host Conda invocation, no global solver or base-environment changes.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
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
    """Install an optional dependency into the existing local ATCO environment."""
    if package not in ("pdal", "pyarrow"):
        raise ValueError(f"Unsupported optional ATCO package: {package}")
    mamba = micromamba_executable()
    root_prefix = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ATCO" / "micromamba"
    return [str(mamba), "install", "--yes", "--root-prefix", str(root_prefix),
            "--prefix", str(ENV), "--override-channels",
            "--strict-channel-priority", "-c", "conda-forge", package]


def install_optional_package(package: str) -> Path:
    """Stream Micromamba output to console and independent log; never modify source files."""
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"install_{package}_{datetime.now():%Y%m%d_%H%M%S}.log"

    def emit(message: str, log):
        print(message, flush=True)
        log.write(message + "\n")
        log.flush()

    with log_file.open("w", encoding="utf-8", errors="replace") as log:
        emit(f"ATCO optional {package} installation started: {datetime.now().isoformat(timespec='seconds')}", log)
        emit(f"Environment: {ENV}", log)
        emit(f"Log: {log_file}", log)
        if not (ENV / "python.exe").is_file():
            raise RuntimeError("ATCO runtime missing. Run INSTALL_RUNTIME.bat first.")
        command = package_command(package)
        emit(f"Package manager: {command[0]}", log)
        env = os.environ.copy()
        if os.name == "nt":
            system = os.environ.get("SystemRoot", r"C:\Windows")
            env["PATH"] = os.pathsep.join((
                str(ENV), str(ENV / "Library" / "bin"), str(ENV / "Scripts"),
                str(Path(system) / "System32"), system,
                str(Path(system) / "System32" / "WindowsPowerShell" / "v1.0")))
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1, env=env)
        if process.stdout is None:
            raise RuntimeError("Cannot read Micromamba installer output")
        for line in process.stdout:
            emit(line.rstrip("\r\n"), log)
        rc = process.wait()
        emit(f"ATCO optional {package} installation finished | exit_code={rc}", log)
        if rc:
            raise RuntimeError(f"Micromamba {package} installation failed (exit {rc}). Log: {log_file}")
    return log_file
