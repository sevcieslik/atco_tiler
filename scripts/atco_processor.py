from __future__ import annotations
import json, os, subprocess, sys, shutil
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = TOOL_ROOT.parent
MODULES = TOOL_ROOT / 'modules'
sys.path.insert(0, str(MODULES))
from atco_delivery import enrich_segments, output_base, product_folder, tile_index_shp_path
CONFIG_DIR = TOOL_ROOT / 'config'
CONFIG_PATH = CONFIG_DIR / 'atco_processor_config.json'
CENTRAL_ROOT = Path(r'W:\APPS\PIT\ATCO')
CLIENT_GDB = Path(r'W:\NMCP26054\products\project\project\132_project_information_pack\v1\Source Data\RS_Aquis_data2026_Revision1_July2027.gdb')
TRAJECTORY_ROOT = CENTRAL_ROOT / 'source' / 'trajectories'
FLIGHT_PRODUCTS = Path(r'W:\NMCP26054\products\flight')
EZLAS = TOOL_ROOT / 'tools' / 'EzLAS' / 'EzLAS.exe'
FOLDER_ALIASES = {
    'aoi': ['aoi'],
    'orthos': ['orthos', 'ortho', 'imagery'],
    'point_cloud': ['point_cloud', 'point cloud', 'pointcloud', 'lidar'],
}

def _enable_ansi():
    # Modern Windows Terminal/cmd supports ANSI; os.system("") enables VT handling
    # on common Windows Python builds. If unsupported, escape codes are stripped below.
    try:
        os.system("")
        return True
    except Exception:
        return False


def print_brand_header():
    ansi = _enable_ansi()
    reset = '\033[0m' if ansi else ''
    white = '\033[97m' if ansi else ''
    gold = '\033[38;5;214m' if ansi else ''
    green = '\033[38;5;113m' if ansi else ''
    grey = '\033[90m' if ansi else ''

    atco = [
        r'    _  _____ ____ ___  ',
        r'   / \|_   _/ ___/ _ \ ',
        r'  / _ \ | || |  | | | |',
        r' / ___ \| || |__| |_| |',
        r'/_/   \_\_| \____\___/ ',
    ]

    nm_group = [
        r' _   _ __  __      ____ ____   ___  _   _ ____  ',
        r'| \ | |  \/  |    / ___|  _ \ / _ \| | | |  _ \ ',
        r'|  \| | |\/| |   | |  _| |_) | | | | | | | |_) |',
        r'| |\  | |  | |   | |_| |  _ <| |_| | |_| |  __/ ',
        r'|_| \_|_|  |_|    \____|_| \_\\___/ \___/|_|    ',
    ]

    print()
    for i in range(max(len(atco), len(nm_group))):
        left = atco[i] if i < len(atco) else ''
        right = nm_group[i] if i < len(nm_group) else ''
        print(f'{white}{left:<31}{green}{right}{reset}')

    print(
        f'{gold}  ---------------------{reset}'
        f'          {green}Innovation In Infrastructure{reset}'
    )
    print(f'{grey}  ATCO 2026 Production Processor  |  NM Group{reset}')
    print()

def load_config():
    cfg = {}
    if CONFIG_PATH.is_file():
        try: cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception: cfg = {}
    default_input = PACKAGE_ROOT / 'input'
    if default_input.is_dir() and not cfg.get('last_input_root'):
        cfg['last_input_root'] = str(default_input)
    return cfg

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')

def browse_folder(initial=None):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
        p = filedialog.askdirectory(initialdir=str(initial) if initial else None, title='Select ATCO input folder')
        root.destroy()
        return Path(p) if p else None
    except Exception:
        ps = "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Select ATCO input folder'; $d.ShowNewFolderButton=$false; if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){Write-Output $d.SelectedPath}"
        r = subprocess.run(['powershell.exe','-NoProfile','-Command',ps], capture_output=True, text=True)
        value = (r.stdout or '').strip()
        return Path(value) if value else None

def ask_input_root(cfg):
    last = cfg.get('last_input_root')
    prompt = 'Input folder path or [B] Browse' + (f' [{last}]' if last else '') + ': '
    value = input(prompt).strip().strip('"')
    if not value and last: root = Path(last)
    elif value.lower() == 'b':
        root = browse_folder(last)
        if root is None: return None
    else: root = Path(value)
    if not root.is_dir(): raise FileNotFoundError(f'Input folder not found: {root}')
    cfg['last_input_root'] = str(root); save_config(cfg)
    return root

def find_child(root, aliases):
    by_name = {p.name.casefold(): p for p in root.iterdir() if p.is_dir()}
    for a in aliases:
        if a.casefold() in by_name: return by_name[a.casefold()]
    return None

def map_input(root): return {k: find_child(root, v) for k,v in FOLDER_ALIASES.items()}
def default_output_root(root):
    if root.name.casefold() == 'input':
        return root.parent / 'output'
    return root / 'output'

def detect_global_mapper(cfg):
    saved = cfg.get('global_mapper_exe')
    if saved and Path(saved).is_file(): return Path(saved)
    for env_name in ('ProgramFiles','ProgramFiles(x86)'):
        base = Path(os.environ.get(env_name,''))
        if not base.is_dir(): continue
        for d in sorted(base.glob('GlobalMapper*'), reverse=True):
            for name in ('global_mapper.exe','GlobalMapper.exe','global_mapper64.exe'):
                p=d/name
                if p.is_file():
                    cfg['global_mapper_exe']=str(p); save_config(cfg); return p
    return None

def validate_environment(cfg, input_root=None, verbose=True):
    items=[]
    def add(name, ok, detail): items.append((name,bool(ok),str(detail)))
    add('Central AOI DB',(CENTRAL_ROOT/'database'/'atco_aoi_master.sqlite').is_file(),CENTRAL_ROOT/'database'/'atco_aoi_master.sqlite')
    add('Central Segments',(CENTRAL_ROOT/'segments'/'circuit').is_dir(),CENTRAL_ROOT/'segments'/'circuit')
    add('Client source GDB',CLIENT_GDB.is_dir(),CLIENT_GDB)
    add('Trajectory index',(TRAJECTORY_ROOT/'trajectory_index.parquet').is_file(),TRAJECTORY_ROOT/'trajectory_index.parquet')
    add('EzLAS.exe',EZLAS.is_file(),EZLAS)
    gm=detect_global_mapper(cfg); add('Global Mapper',gm is not None,gm or 'not detected')
    p=subprocess.run(['pdal','--version'],capture_output=True,text=True); add('PDAL CLI',p.returncode==0,(p.stdout or p.stderr).strip())
    if input_root:
        m=map_input(input_root)
        for k in ('aoi','orthos','point_cloud'): add(f'Input/{k}',m[k] is not None,m[k] or 'missing')
    if verbose:
        print('\nATCO setup / source validation'); print('-'*78)
        for n,ok,d in items: print(f"{'OK' if ok else 'MISSING':7} {n:22} {d}")
        print('-'*78)
        print('Missing Global Mapper blocks ECW only.')
        print('Missing EzLAS.exe blocks zLAS only.')
        print('Missing trajectory index does not block TIFF/LAZ builds; refresh it from the menu.')
    return items

def run_module(name,args,env=None,check=True):
    p=subprocess.run([sys.executable,str(MODULES/name),*[str(a) for a in args]],env=env)
    if check and p.returncode:
        raise RuntimeError(f'{name} failed with exit code {p.returncode}')
    return p.returncode

def require_folder(mapped,key):
    p=mapped.get(key)
    if not p: raise FileNotFoundError(f'Input folder is missing required subfolder: {key}')
    return p

def imagery_menu(input_root,mapped,cfg):
    aoi=require_folder(mapped,'aoi'); orthos=require_folder(mapped,'orthos'); out=default_output_root(input_root); gm=detect_global_mapper(cfg)
    print('\nImagery\n1. TIFF + ECW\n2. TIFF only\n3. Ortho Processing Footprints only\n4. Back')
    c=input('Choose [1-4]: ').strip()
    if c=='4': return
    if c not in ('1','2','3'): raise ValueError('Invalid imagery option')
    env=os.environ.copy(); env['ATCO_PARALLEL_WORKERS']=str(int(cfg.get('parallel_workers', cfg.get('ecw_workers',2))))
    if c=='3':
        code = "from pathlib import Path; import sys; sys.path.insert(0,r'"+str(MODULES)+"'); import process_orthos as m; p=m.find_polygon_shp(Path(r'"+str(aoi)+"')); c=m.circuit_from_polygon(p); a=m.load_aoi_revision(c,p); _,s=m.load_segments(c,a['revision_code']); fs=m.source_tiffs(Path(r'"+str(orthos)+"')); _,t,r,_=m.raster_inventory(fs,a['crs']); b=m.output_base(Path(r'"+str(out)+"'),c); oi=m.build_ortho_index(s,t,r,b/'QA'/'Ortho_Processing_Footprints'/(c+'_Ortho_Processing_Footprints.shp')); g,o,n=m.overlap_report(oi,a['geometry'],b/'QA'/'Imagery'); print(f'Created {len(oi)} footprints; gap={g:.3f} m2; overlap={o:.1f} m2 ({n} pairs)')"
        p=subprocess.run([sys.executable,'-c',code],env=env)
        if p.returncode: raise RuntimeError('Ortho Processing Footprints build failed')
        return
    args=['--aoi',aoi,'--source',orthos,'--output-root',out]
    if c=='2': args.append('--tiff-only')
    else:
        if not gm: raise FileNotFoundError('Global Mapper was not detected. ECW build cannot run.')
        args += ['--global-mapper-exe',gm]
    run_module('process_orthos.py',args,env=env)

def lidar_menu(input_root,mapped):
    aoi=require_folder(mapped,'aoi'); point_cloud=mapped.get('point_cloud'); out=default_output_root(input_root)
    print('\nLiDAR\n1. LAZ only\n2. LAZ + zLAS\n3. Tile Index SHP only\n4. LAZ + zLAS + Tile Index SHP\n5. Back')
    c=input('Choose [1-5]: ').strip()
    if c=='5': return
    if c not in ('1','2','3','4'): raise ValueError('Invalid LiDAR option')
    args=['--aoi',aoi,'--output-root',out]
    if c!='3':
        if not point_cloud: raise FileNotFoundError('Input folder is missing point_cloud')
        args += ['--source',point_cloud]
    if c in ('2','4'):
        if not EZLAS.is_file(): raise FileNotFoundError(f'EzLAS.exe is missing. Put it here and rerun:\n{EZLAS}')
        args += ['--zlas','--ezlas-exe',EZLAS]
    if c in ('3','4'): args.append('--tile-index')
    run_module('process_lidar.py',args)

def _merge_move(src: Path, dst: Path):
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        target = dst / src.name
        if not target.exists():
            shutil.move(str(src), str(target))
        return
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists() and item.is_dir() and target.is_dir():
            _merge_move(item, target)
            try:
                item.rmdir()
            except OSError:
                pass
        elif not target.exists():
            shutil.move(str(item), str(target))
    try:
        src.rmdir()
    except OSError:
        pass


def migrate_legacy_qa(base: Path):
    """Collapse old QA folders and remove technical artifacts from delivery folders."""
    qa = base / 'QA'
    imagery = qa / 'Imagery'
    lidar = qa / 'LiDAR'
    footprints = qa / 'Ortho_Processing_Footprints'
    ezlas_qa = lidar / 'EzLAS'

    # Move old standalone folders.
    _merge_move(base / 'QA_LiDAR', lidar)
    _merge_move(base / 'Ortho_Processing_Footprints', footprints)

    # EzLAS writes EzLasLog (n).txt beside zLAS files on some builds.
    # The client delivery folder must contain only .zlas products.
    zlas_dir = base / product_folder('zLAS')
    if zlas_dir.exists():
        for item in list(zlas_dir.iterdir()):
            if item.is_file() and item.suffix.casefold() != '.zlas':
                ezlas_qa.mkdir(parents=True, exist_ok=True)
                target = ezlas_qa / f'legacy_{item.name}'
                n = 1
                while target.exists():
                    target = ezlas_qa / f'legacy_{n}_{item.name}'
                    n += 1
                try:
                    shutil.move(str(item), str(target))
                except Exception:
                    pass

    # Older imagery QA files lived directly in QA. Move everything except the
    # new named QA subfolders into QA/Imagery.
    if qa.exists():
        keep = {'Imagery', 'LiDAR', 'Ortho_Processing_Footprints'}
        legacy = [p for p in list(qa.iterdir()) if p.name not in keep]
        if legacy:
            imagery.mkdir(parents=True, exist_ok=True)
            for item in legacy:
                target = imagery / item.name
                if target.exists() and item.is_dir() and target.is_dir():
                    _merge_move(item, target)
                elif not target.exists():
                    shutil.move(str(item), str(target))



def product_status(input_root,mapped):
    aoi = require_folder(mapped,'aoi')
    sys.path.insert(0, str(MODULES))
    import process_orthos as ortho
    polygon = ortho.find_polygon_shp(aoi)
    circuit = ortho.circuit_from_polygon(polygon)
    aoi_rec = ortho.load_aoi_revision(circuit, polygon)
    _, seg = ortho.load_segments(circuit, aoi_rec['revision_code'])
    seg = enrich_segments(seg, circuit) if 'DeliveryName' not in seg.columns else seg
    blocks = [str(v) for v in seg['ImageryName'].tolist()]
    cloud_blocks = [str(v) for v in seg['PointCloudName'].tolist()]
    base = output_base(default_output_root(input_root), circuit)
    migrate_legacy_qa(base)

    def ok(p):
        try:
            return p.is_file() and p.stat().st_size > 0
        except OSError:
            return False

    missing = {
        'TIFF': [b for b in blocks if not ok(base/product_folder('TIFF')/f'{b}.tif')],
        'ECW': [b for b in blocks if not ok(base/product_folder('ECW')/f'{b}.ecw')],
        'LAZ': [b for b in cloud_blocks if not ok(base/product_folder('LAZ')/f'{b}.laz')],
        'zLAS': [b for b in cloud_blocks if not ok(base/product_folder('zLAS')/f'{b}.zlas')],
    }
    tile_shp = tile_index_shp_path(base, circuit)
    shp_ok = ok(tile_shp) and tile_shp.with_suffix('.dbf').is_file() and tile_shp.with_suffix('.shx').is_file()
    missing['Tile Index'] = [] if shp_ok else ['missing']
    return circuit, aoi_rec['revision_code'], blocks, base, missing


def print_product_status(input_root,mapped,title='PRODUCT STATUS'):
    circuit, rev, blocks, base, missing = product_status(input_root,mapped)
    print(f'\n{title} - {circuit} {rev}')
    print('-'*58)
    for key in ('TIFF','ECW','LAZ','zLAS','Tile Index'):
        miss = missing[key]
        total = 1 if key == 'Tile Index' else len(blocks)
        have = total - len(miss)
        print(f'{key:<12} {have:>3}/{total:<3}  {"OK" if not miss else f"{len(miss)} missing"}')
    print('-'*58)
    return missing


def existing_product_summary(input_root, mapped):
    circuit, rev, blocks, base, missing = product_status(input_root, mapped)
    totals = {
        'TIFF': len(blocks),
        'ECW': len(blocks),
        'LAZ': len(blocks),
        'zLAS': len(blocks),
        'Tile Index': 1,
    }
    existing = {k: totals[k] - len(missing[k]) for k in totals}
    return circuit, rev, totals, existing


def confirm_full_overwrite(input_root, mapped):
    circuit, rev, totals, existing = existing_product_summary(input_root, mapped)
    if not any(existing.values()):
        return True

    print(f'\nExisting output found for {circuit} {rev}:')
    print('-' * 42)
    for key in ('TIFF', 'ECW', 'LAZ', 'zLAS', 'Tile Index'):
        print(f'{key:<12} {existing[key]:>3}/{totals[key]:<3}')
    print('-' * 42)
    print('Full Build will overwrite ALL existing products for this circuit/revision.')
    print('Use "Rerun missing products" instead if you only want to fill gaps.')
    answer = input('Continue and overwrite? [Y/N]: ').strip().lower()
    return answer in ('y', 'yes')



def full_build(input_root,mapped,cfg,rerun=False):
    aoi=require_folder(mapped,'aoi')
    orthos=require_folder(mapped,'orthos')
    pc=require_folder(mapped,'point_cloud')
    out=default_output_root(input_root)
    gm=detect_global_mapper(cfg)

    if rerun:
        missing = print_product_status(input_root,mapped,'BEFORE RERUN')
        if all(not v for v in missing.values()):
            print('\nNothing is missing. No processing required.')
            return
        print('\nRERUN MISSING PRODUCTS')
        overwrite_existing = False
    else:
        if not confirm_full_overwrite(input_root, mapped):
            print('\nFull Build cancelled. Existing output was not changed.')
            return
        print('\nFULL BUILD - OVERWRITE ENABLED')
        overwrite_existing = True

    print('  Imagery : TIFF + ECW')
    print('  LiDAR   : LAZ + zLAS + TILES in delivery GDB')
    print(f'  Output  : {out}\n')

    failures=[]
    env=os.environ.copy()
    env['ATCO_PARALLEL_WORKERS']=str(int(cfg.get('parallel_workers', cfg.get('ecw_workers',2))))

    # Imagery is allowed to fail at ECW stage. TIFFs already created remain valid,
    # and LiDAR continues.
    if gm:
        imagery_args=['--aoi',aoi,'--source',orthos,'--output-root',out,'--global-mapper-exe',gm]
        if overwrite_existing:
            imagery_args.append('--overwrite')
        rc=run_module(
            'process_orthos.py',
            imagery_args,
            env=env, check=False
        )
        if rc:
            failures.append('ECW/imagery stage incomplete - see QA/Imagery and GM logs')
    else:
        print('Global Mapper unavailable: building TIFF only; ECW left pending.')
        imagery_args=['--aoi',aoi,'--source',orthos,'--output-root',out,'--tiff-only']
        if overwrite_existing:
            imagery_args.append('--overwrite')
        rc=run_module(
            'process_orthos.py',
            imagery_args,
            env=env, check=False
        )
        if rc:
            failures.append('TIFF stage failed')
        failures.append('ECW pending - Global Mapper unavailable')

    # If EzLAS is unavailable we still build LAZ + Tile Index SHP.
    lidar_args=['--aoi',aoi,'--source',pc,'--output-root',out,'--tile-index']
    if overwrite_existing:
        lidar_args.append('--overwrite')
    if EZLAS.is_file():
        lidar_args += ['--zlas','--ezlas-exe',EZLAS]
    else:
        print('EzLAS.exe unavailable: building LAZ + Tile Index SHP; zLAS left pending.')
        failures.append('zLAS pending - EzLAS.exe unavailable')

    rc=run_module('process_lidar.py',lidar_args,check=False)
    if rc:
        failures.append('LiDAR/zLAS stage incomplete')

    missing=print_product_status(input_root,mapped,'BUILD RESULT')
    total_missing=sum(len(v) for v in missing.values())
    if total_missing:
        print(f'\nPARTIAL BUILD: {total_missing} product item(s) still missing.')
        print('Use "Rerun missing products" after the dependency/problem is resolved.')
    else:
        print('\nBUILD COMPLETE: all expected products are present.')

    if failures:
        print('\nNotes:')
        for x in failures:
            print(f'  - {x}')

def refresh_trajectories(): run_module('build_trajectory_source.py',['--flight-root',FLIGHT_PRODUCTS,'--dest-root',TRAJECTORY_ROOT])

def settings_menu(cfg):
    while True:
        print(f"\nSettings\n1. Parallel workers [{cfg.get('ecw_workers',2)}]\n2. Global Mapper [{cfg.get('global_mapper_exe') or 'auto-detect'}]\n3. Back")
        c=input('Choose [1-3]: ').strip()
        if c=='3': return
        if c=='1':
            raw=input('ECW workers [-1 = all available, or 1..N]: ').strip()
            n=int(raw)
            if n == 0 or n < -1:
                raise ValueError('Use -1 for all available CPU, or a positive worker count')
            cfg['ecw_workers']=n
            save_config(cfg)
            if n == -1:
                print(f'Parallel workers set to AUTO/ALL ({os.cpu_count() or 1} logical CPU available).')
            else:
                print(f'Parallel workers set to {n}.')
        elif c=='2':
            value=input('Global Mapper EXE path or [A] auto-detect: ').strip().strip('"')
            if value.lower()=='a':
                cfg.pop('global_mapper_exe',None); gm=detect_global_mapper(cfg); print(f'Detected: {gm}' if gm else 'Not detected')
            else:
                p=Path(value)
                if not p.is_file(): raise FileNotFoundError(p)
                cfg['global_mapper_exe']=str(p); save_config(cfg)
        else: raise ValueError('Invalid option')

def main():
    cfg=load_config(); input_root=Path(cfg['last_input_root']) if cfg.get('last_input_root') and Path(cfg['last_input_root']).is_dir() else None
    print_brand_header()
    print('ATCO Retiler CLI v1.06')
    print('='*72)
    print(f'Client source GDB : {CLIENT_GDB}\nCentral AOI       : {CENTRAL_ROOT}\nEzLAS expected    : {EZLAS}')
    while True:
        mapped=map_input(input_root) if input_root else {'aoi':None,'orthos':None,'point_cloud':None}
        if input_root:
            print(f'\nInput : {input_root}\nOutput: {default_output_root(input_root)}')
            print('Mapped:',f"AOI={'OK' if mapped['aoi'] else 'MISSING'},",f"Orthos={'OK' if mapped['orthos'] else 'MISSING'},",f"PointCloud={'OK' if mapped['point_cloud'] else 'MISSING'}")
        else: print('\nInput : not selected')
        print('\n1. Select / change input folder\n2. Imagery\n3. LiDAR\n4. Full build (TIFF + ECW + LAZ + zLAS + Tile Index SHP)\n5. Rerun missing products\n6. Validate setup / source data\n7. Refresh Trajectory/SBET source\n8. Settings\n9. Exit')
        choice=input('\nChoose [1-9]: ').strip()
        try:
            if choice=='1':
                s=ask_input_root(cfg); input_root=s or input_root
            elif choice in ('2','3','4','5'):
                if input_root is None:
                    selected=ask_input_root(cfg)
                    if selected is None: continue
                    input_root=selected; mapped=map_input(input_root)
                if choice=='2': imagery_menu(input_root,mapped,cfg)
                elif choice=='3': lidar_menu(input_root,mapped)
                elif choice=='4': full_build(input_root,mapped,cfg,rerun=False)
                else: full_build(input_root,mapped,cfg,rerun=True)
            elif choice=='6': validate_environment(cfg,input_root)
            elif choice=='7': refresh_trajectories()
            elif choice=='8': settings_menu(cfg)
            elif choice=='9': return 0
            else: print('Invalid option.')
        except Exception as exc:
            print(f'\nERROR: {type(exc).__name__}: {exc}')
            input('\nPress Enter to return to the main menu...')

if __name__=='__main__': raise SystemExit(main())
