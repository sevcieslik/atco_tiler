# ATCO Retiler v1.06

Python application to retile ATCO LiDAR and four-band RGB+NIR orthophoto products.

Run `ATCO_RETILER.bat` from the root directory. It invokes `scripts/INSTALL_RUNTIME.bat` if necessary, validates the runtime and launches the GUI.

The processing code, documentation and install scripts are in `scripts/`. See `scripts/README.txt` and `scripts/docs/`.

## Local setup

1. Create `input/aoi/`, `input/orthos/`, `input/point_cloud/` as required.
2. Set site-specific paths in `scripts/config/defaults.json` (`client_gdb`, `central_root`, `trajectory_root`). The repository includes blank path values so client/network paths are not published.
3. Configure Global Mapper and other local dependencies using the application or local config files.
4. If licensed, supply `scripts/tools/EzLAS/EzLAS.exe` separately.
5. Launch `ATCO_RETILER.bat`.

**Note:** Source data, outputs, Python/Conda environments, local runtime metadata, and separately licensed executables must not be committed.
