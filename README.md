# ATCO Retiler

ATCO Retiler v1.06, Python-based LiDAR and orthophoto processing workflow.

## Project layout

- `ATCO_RETILER.bat` launches the application from the project root.
- `scripts/` contains the GUI, processing modules, install scripts, documentation and branding.
- `input/` and `output/` are local working directories and are not tracked.
- `scripts/.venv/` is a local Python/Conda environment and is not tracked.

## Setup

1. Place the current application files in `scripts/`.
2. Place the root launcher `ATCO_RETILER.bat` beside the `scripts` folder.
3. Run `scripts/INSTALL_RUNTIME.bat` if necessary.
4. Install the separately licensed EzLAS executable in `scripts/tools/EzLAS/` if required.
5. Run `ATCO_RETILER.bat`.

Configure machine-specific paths locally. Avoid committing credentials, source imagery, LiDAR, outputs, machine-specific runtime state or proprietary executables.

## Documentation

See `scripts/README.txt`, `scripts/docs/technical_notes.html` and `scripts/docs/faq_troubleshooting_qa.html`.

Project contents are uploaded separately from this initial repository setup.
