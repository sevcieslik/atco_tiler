from __future__ import annotations
import importlib, json, subprocess, sys, shutil
from pathlib import Path
TOOL_ROOT=Path(__file__).resolve().parent; CONFIG_DIR=TOOL_ROOT/'config'; CONFIG_DIR.mkdir(parents=True,exist_ok=True)
CLIENT_GDB=Path(r'W:\NMCP26054\products\project\project\132_project_information_pack\v1\Source Data\RS_Aquis_data2026_Revision1_July2027.gdb')
CENTRAL_ROOT=Path(r'W:\APPS\PIT\ATCO'); EZLAS=TOOL_ROOT/'tools'/'EzLAS'/'EzLAS.exe'
REQUIRED=['geopandas','shapely','pyproj','rasterio','numpy','pandas']
def missing():
    out=[]
    for n in REQUIRED:
        try: importlib.import_module(n)
        except Exception: out.append(n)
    return out
def main():
    print('\nATCO Retiler first-run / runtime check'); print(f'Python : {sys.executable}'); print(f'Version: {sys.version.split()[0]}')
    m=missing()
    if m:
        print('ERROR: GIS runtime is missing Python packages:',', '.join(m)); print('Run INSTALL_RUNTIME.bat to create/repair the conda environment.'); return 2
    p=subprocess.run(['pdal','--version'],capture_output=True,text=True) if shutil.which('pdal') else subprocess.CompletedProcess(['pdal','--version'], 127, '', 'PDAL not installed')
    if p.returncode: print('NOTE: PDAL CLI not installed. TIFF/ECW remain available; run INSTALL_LIDAR_SUPPORT.bat for LAZ/zLAS.')
    print(f"PDAL   : {(p.stdout or p.stderr).strip()}")
    print(f"Central: {'OK' if (CENTRAL_ROOT/'database'/'atco_aoi_master.sqlite').is_file() else 'MISSING'}")
    print(f"GDB    : {'OK' if CLIENT_GDB.is_dir() else 'MISSING'}")
    ezlas_status = 'OK' if EZLAS.is_file() else r'MISSING - put EzLAS.exe in scripts\tools\EzLAS'
    print(f'EzLAS  : {ezlas_status}')
    if not CLIENT_GDB.is_dir(): print('\nNOTE: Client source GDB not found; some ATCO validation tasks may be unavailable.')
    if not (CENTRAL_ROOT/'database'/'atco_aoi_master.sqlite').is_file(): print('\nNOTE: Central AOI database unavailable; Standard Retiler requires it, AOI Converter does not.')
    (CONFIG_DIR/'runtime_ok.json').write_text(json.dumps({'python':sys.executable,'pdal':(p.stdout or p.stderr).strip(),'client_gdb':str(CLIENT_GDB),'central_root':str(CENTRAL_ROOT),'ezlas_present':EZLAS.is_file()},indent=2),encoding='utf-8')
    return 0
if __name__=='__main__': raise SystemExit(main())
