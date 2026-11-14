from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

TOOL_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = TOOL_ROOT.parent
MODULES = TOOL_ROOT / "modules"
CONFIG_DIR = TOOL_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "atco_processor_config.json"
ASSETS = TOOL_ROOT / "assets" / "branding"
EZLAS = TOOL_ROOT / "tools" / "EzLAS" / "EzLAS.exe"
DOCS = TOOL_ROOT / "docs"

sys.path.insert(0, str(TOOL_ROOT))
import atco_processor as core
from atco_delivery import product_folder

APP_NAME = "ATCO Retiler"
APP_VERSION = "1.08-dev"


def current_commit():
    """Read the checked-out Git commit without needing git.exe on PATH.

    The GIS launcher intentionally isolates PATH, so calling git directly is
    unreliable. Read .git/HEAD and its reference, including packed refs and
    Git worktree .git pointer files.
    """
    try:
        dotgit = PACKAGE_ROOT / ".git"
        if dotgit.is_file():
            pointer = dotgit.read_text(encoding="utf-8").strip()
            if not pointer.lower().startswith("gitdir:"):
                return "unknown"
            gitdir = Path(pointer.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = PACKAGE_ROOT / gitdir
        elif dotgit.is_dir():
            gitdir = dotgit
        else:
            return "unknown"
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            loose = gitdir / ref
            if loose.is_file():
                head = loose.read_text(encoding="utf-8").strip()
            else:
                packed = gitdir / "packed-refs"
                head = ""
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line and not line.startswith(("#", "^")):
                            sha, _, name = line.partition(" ")
                            if name.strip() == ref:
                                head = sha
                                break
                # Linked worktrees may store shared refs in the common git dir.
                if not head and (gitdir / "commondir").is_file():
                    common = Path((gitdir / "commondir").read_text(encoding="utf-8").strip())
                    common = common if common.is_absolute() else gitdir / common
                    shared_ref = common / ref
                    if shared_ref.is_file():
                        head = shared_ref.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
            return head[:7].lower()
    except (OSError, UnicodeError, ValueError):
        pass
    return "unknown"


# Palette
NAV = "#0e1a26"
NAV_2 = "#0b1620"
BG = "#102132"
CARD = "#1c3144"
BORDER = "#486578"
TEXT = "#edf5fa"
MUTED = "#a8bdcb"
GREEN = "#27a43e"
GREEN_DARK = "#208b35"
GREEN_SOFT = "#183e31"
BLUE = "#4f9ed8"
BLUE_SOFT = "#203d56"
RED = "#f07373"
RED_SOFT = "#49313c"
AMBER = "#f0ba54"
AMBER_SOFT = "#4a4031"
LOG_BG = "#0c1b29"
LOG_TEXT = "#dce9f3"
ROW_BG = "#263f54"


def human_path(p):
    return str(p) if p else ""


def win_open(path: Path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_local_file(path: Path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def memory_gb():
    if os.name != "nt":
        return None
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(stat)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        used = stat.ullTotalPhys - stat.ullAvailPhys
        return used / (1024 ** 3)
    return None


class RoundedActionButton(tk.Canvas):
    """Canvas-backed button: rounded geometry, hover and disabled states.

    Implements the small subset of Tk Button's configure interface used by
    build/pause/resume callbacks, so the processing code is unchanged.
    """
    def __init__(self, parent, text, command, *, primary=False):
        self._text = text
        self._command = command
        self._state = "normal"
        self._primary = primary
        self._hover = False
        width = max(116, 18 + len(text) * 8)
        super().__init__(parent, width=width, height=40, bg=CARD,
                         highlightthickness=0, bd=0, relief="flat",
                         cursor="hand2")
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self._redraw()

    def _enter(self, _event):
        self._hover = True
        self._redraw()

    def _leave(self, _event):
        self._hover = False
        self._redraw()

    def _click(self, _event):
        if self._state == "normal" and self._command:
            self._command()

    def _redraw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 24), max(self.winfo_height(), 24)
        disabled = self._state != "normal"
        fill = ("#305344" if disabled and self._primary else "#304355" if disabled
                else GREEN_DARK if self._primary and self._hover
                else GREEN if self._primary else "#36576d" if self._hover
                else "#2a465d")
        foreground = "#8ea99a" if disabled and self._primary else "#8198aa" if disabled else "#ffffff"
        r = 9
        points = [r, 1, w-r, 1, w-1, 1, w-1, r,
                  w-1, h-r, w-1, h-1, w-r, h-1,
                  r, h-1, 1, h-1, 1, h-r, 1, r, 1, 1]
        self.create_polygon(points, fill=fill, outline="", smooth=True,
                            splinesteps=12)
        self.create_text(w // 2, h // 2, text=self._text, fill=foreground,
                         font=("Segoe UI", 10, "bold" if self._primary else "normal"))

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        if "state" in kwargs:
            self._state = str(kwargs.pop("state"))
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
        if kwargs:
            super().configure(**kwargs)
        self._redraw()

    config = configure

    def __getitem__(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().__getitem__(key)


class RetilerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1560x940")
        self.minsize(1180, 760)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cfg = core.load_config()
        self.input_root = self._initial_input()
        self.output_root = self._initial_output()
        self.mapped = {"aoi": None, "orthos": None, "point_cloud": None}
        self.context = None
        self.blocks = []
        self.cloud_blocks = []
        self.entity_records = []
        self.aoi_options = []
        self._aoi_selection_initialized = False
        self.input_validated = False
        self.validating = False
        self.running = False
        self.paused = False
        self.process = None
        self.stop_requested = False
        self.suspended_pids = []
        self.log_queue = queue.Queue()
        self.session_created = {k: 0 for k in ("TIFF","ECW","LAZ","zLAS","Tile Index")}
        self.build_mode = "Idle"
        self.current_stage = "Idle"
        self.build_start_time = None
        self.build_elapsed_final = 0.0
        self.source_tiff_count = 0
        self.source_lidar_count = 0

        self.product_vars = {
            "TIFF": tk.BooleanVar(value=True),
            "ECW": tk.BooleanVar(value=True),
            "LAZ": tk.BooleanVar(value=True),
            "zLAS": tk.BooleanVar(value=True),
            "Tile Index": tk.BooleanVar(value=True),
        }

        self._configure_styles()
        self._build_shell()
        self._show_page("Build")
        self.after(150, self._drain_log_queue)
        self.after(400, self.refresh_mapping)
        self.after(1500, self._status_tick)

    # ---------- config ----------
    def _initial_input(self):
        p = self.cfg.get("last_input_root")
        if p and Path(p).is_dir():
            return Path(p)
        default = PACKAGE_ROOT / "input"
        return default if default.is_dir() else None

    def _initial_output(self):
        custom = self.cfg.get("gui_output_root")
        if custom:
            return Path(custom)
        if self.input_root:
            return core.default_output_root(self.input_root)
        return PACKAGE_ROOT / "output"

    def save_cfg(self):
        if self.input_root:
            self.cfg["last_input_root"] = str(self.input_root)
        self.cfg["gui_output_root"] = str(self.output_root)
        core.save_config(self.cfg)

    # ---------- styling ----------
    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TProgressbar",
                        troughcolor="#405a70",
                        background=GREEN,
                        bordercolor="#405a70",
                        lightcolor=GREEN,
                        darkcolor=GREEN,
                        thickness=10)
        style.configure("Blue.Horizontal.TProgressbar",
                        troughcolor="#405a70",
                        background=BLUE,
                        bordercolor="#405a70",
                        lightcolor=BLUE,
                        darkcolor=BLUE,
                        thickness=10)
        style.configure("Treeview",
                        background=CARD,
                        fieldbackground=CARD,
                        foreground=TEXT,
                        rowheight=27,
                        borderwidth=0)
        style.configure("Treeview.Heading",
                        background=ROW_BG,
                        foreground=TEXT,
                        relief="flat",
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", BLUE_SOFT)], foreground=[("selected", TEXT)])

    # ---------- shell ----------
    def _build_shell(self):
        self.header = tk.Frame(self, bg=NAV_2, height=86)
        self.header.pack(side="top", fill="x")
        self.header.pack_propagate(False)

        self._load_logo_header()
        self.commit_badge = tk.Label(
            self.header, text=f"Commit  {current_commit()}", bg="#1d3242",
            fg="#d4e4ee", font=("Consolas", 10, "bold"), padx=14, pady=8,
        )
        self.commit_badge.pack(side="right", padx=(8, 22), pady=24)

        self.sidebar = tk.Frame(self, bg=NAV, width=222)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_wrap = tk.Frame(self, bg=BG)
        self.content_wrap.pack(side="right", fill="both", expand=True)

        self.statusbar = tk.Frame(self, bg=NAV_2, height=30)
        self.statusbar.pack(side="bottom", fill="x")
        self.statusbar.pack_propagate(False)

        self.status_dot = tk.Label(self.statusbar, text="●", fg="#48bd54", bg=NAV_2, font=("Segoe UI", 10))
        self.status_dot.pack(side="left", padx=(14,5))
        self.status_text = tk.Label(self.statusbar, text="Ready", fg="#e6ecf0", bg=NAV_2, font=("Segoe UI", 9))
        self.status_text.pack(side="left")

        self.resource_label = tk.Label(self.statusbar, text="", fg="#d5dde3", bg=NAV_2, font=("Segoe UI", 9))
        self.resource_label.pack(side="right", padx=16)

        self.nav_buttons = {}
        items = [
            ("Build", "▶"),
            ("Activity Log", "▤"),
            ("Data Sources", "▧"),
            ("Settings", "⚙"),
            ("QA & Reports", "▥"),
            ("How to Start", "ⓘ"),
            ("About", "ⓘ"),
        ]
        for label, icon in items:
            btn = tk.Button(
                self.sidebar, text=f"  {icon}   {'Process' if label == 'Build' else label}", anchor="w",
                relief="flat", bd=0, bg=NAV, fg="#eef3f6",
                activebackground="#263642", activeforeground="white",
                font=("Segoe UI", 11), padx=12, pady=13,
                command=lambda n=label: self._show_page(n)
            )
            btn.pack(fill="x", padx=10, pady=(12 if label=="Build" else 2, 0))
            self.nav_buttons[label] = btn

        tk.Frame(self.sidebar, bg="#31404c", height=1).pack(fill="x", padx=14, pady=(16,8))
        self.tech_notes_btn = tk.Button(
            self.sidebar, text="  ▤   Technical Notes", anchor="w",
            relief="flat", bd=0, bg=NAV, fg="#eef3f6",
            activebackground="#263642", activeforeground="white",
            font=("Segoe UI", 10), padx=12, pady=10,
            command=lambda: self._open_doc("technical_notes.html")
        )
        self.tech_notes_btn.pack(fill="x", padx=10, pady=(0,2))
        self.faq_btn = tk.Button(
            self.sidebar, text="  ?   FAQ / Troubleshooting", anchor="w",
            relief="flat", bd=0, bg=NAV, fg="#eef3f6",
            activebackground="#263642", activeforeground="white",
            font=("Segoe UI", 10), padx=12, pady=10,
            command=lambda: self._open_doc("faq_troubleshooting_qa.html")
        )
        self.faq_btn.pack(fill="x", padx=10, pady=(0,2))

        self.pages = {}
        self.pages["Build"] = self._build_build_page()
        self.pages["Activity Log"] = self._build_log_page()
        self.pages["Data Sources"] = self._build_mapping_page()
        self.pages["Settings"] = self._build_settings_page()
        self.pages["QA & Reports"] = self._build_qa_page()
        self.pages["How to Start"] = self._build_how_to_start_page()
        self.pages["About"] = self._build_about_page()

    def _load_logo_header(self):
        left = tk.Frame(self.header, bg=NAV_2)
        left.pack(side="left", fill="y", padx=(28,0))

        self.logo_images = []
        x = 0
        for name in ("ATCO_header.png", "NM_Group_header.png"):
            p = ASSETS / name
            try:
                img = tk.PhotoImage(file=str(p))
                self.logo_images.append(img)
                lab = tk.Label(left, image=img, bg=NAV_2)
                lab.pack(side="left", padx=(0,24))
            except Exception:
                fallback = "ATCO" if "ATCO" in name else "NM Group"
                tk.Label(left, text=fallback, bg=NAV_2, fg="white",
                         font=("Segoe UI", 22, "bold")).pack(side="left", padx=(0,24))

        tk.Frame(left, width=1, bg="#6d7881").pack(side="left", fill="y", pady=17, padx=(0,25))
        tk.Label(left, text=f"{APP_NAME} v{APP_VERSION}",
                 bg=NAV_2, fg="white", font=("Segoe UI", 17, "bold")).pack(side="left", pady=25)

    def _show_page(self, name):
        for p in self.pages.values():
            p.pack_forget()
        page = self.pages[name]
        page.pack(fill="both", expand=True)
        for n,b in self.nav_buttons.items():
            b.configure(bg=GREEN if n == name else NAV,
                        activebackground=GREEN_DARK if n == name else "#263642")
        if name == "QA & Reports":
            self.refresh_qa_tree()
        elif name == "Data Sources":
            self.refresh_mapping()

    def _open_doc(self, filename):
        try:
            open_local_file(DOCS / filename)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open documentation:\n{exc}")

    # ---------- widgets ----------
    def card(self, parent, title=None):
        outer = tk.Frame(parent, bg=BORDER, bd=0)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            tk.Label(inner, text=title, bg=CARD, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12,6))
        # A dedicated content frame lets each card use either grid or pack
        # without mixing geometry managers with the packed heading above.
        body = tk.Frame(inner, bg=CARD)
        body.pack(fill="both", expand=True)
        return outer, body

    def badge(self, parent, text, kind="ok"):
        colors = {
            "ok": (GREEN_SOFT, "#8fe4a2"),
            "run": (BLUE_SOFT, "#a2d5f7"),
            "bad": (RED_SOFT, "#ffc5c5"),
            "warn": (AMBER_SOFT, "#ffe0a1"),
            "muted": (ROW_BG, MUTED)
        }
        bg, fg = colors.get(kind, colors["muted"])
        return tk.Label(parent, text=text, bg=bg, fg=fg, padx=9, pady=3,
                        font=("Segoe UI", 9, "bold"))

    # ---------- Build dashboard ----------
    def _dashboard_label(self, parent, text, *, size=10, bold=False,
                         color=TEXT, bg=CARD, **options):
        return tk.Label(parent, text=text, bg=bg, fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"),
                        **options)

    def _dashboard_button(self, parent, text, command, *, primary=False):
        return RoundedActionButton(parent, text, command, primary=primary)

    def _build_build_page(self):
        # Canvas viewport prevents lower products from being clipped on
        # Windows with display scaling or shorter screen heights.
        viewport = tk.Frame(self.content_wrap, bg=BG)
        canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        page = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>", lambda _event: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(
            window_id, width=event.width))
        canvas.bind("<Enter>", lambda _event: canvas.bind_all(
            "<MouseWheel>", lambda wheel: canvas.yview_scroll(
                int(-wheel.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))
        page.grid_columnconfigure(0, weight=7)
        page.grid_columnconfigure(1, weight=4)
        page.grid_rowconfigure(2, weight=0)
        page.grid_rowconfigure(3, weight=0)

        # Live context tiles.
        metrics = tk.Frame(page, bg=BG)
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(14, 6))
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1, uniform="metric")
        self.summary_values = {}
        summaries = (
            ("project", "▤   ACTIVE PROJECT", "Awaiting input", "Circuit / AOI revision"),
            ("sources", "▥   INPUT DATA", "—", "Ortho TIFFs + LiDAR files"),
            ("products", "◈   SELECTED PRODUCTS", "5", "TIFF · ECW · LAZ · zLAS · Index"),
            ("segments", "▦   PROCESSING UNITS", "—", "Eligible tiles / areas"),
        )
        for i, (key, heading, value, hint) in enumerate(summaries):
            outer, inner = self.card(metrics)
            outer.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0 if i == 3 else 5))
            self._dashboard_label(inner, heading, size=9, color="#a9c9df").pack(
                anchor="w", padx=16, pady=(8, 1))
            label = self._dashboard_label(inner, value, size=20, bold=True)
            label.pack(anchor="w", padx=16)
            self.summary_values[key] = label
            self._dashboard_label(inner, hint, size=9, color=MUTED).pack(
                anchor="w", padx=16, pady=(1, 8))

        # Compact input/output setup shared with the Data Sources page.
        setup_outer, setup = self.card(page, "Project Setup")
        setup_outer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        setup.grid_columnconfigure(1, weight=1)
        setup.grid_columnconfigure(3, minsize=180)
        for idx, (caption, entry_attr, command) in enumerate((
            ("Input (Project Data)", "input_entry", self.choose_input),
            ("Output (Deliverables)", "output_entry", self.choose_output),
        )):
            self._dashboard_label(setup, caption, size=9).grid(
                row=idx, column=0, sticky="w", padx=(16, 12), pady=5)
            entry = tk.Entry(setup, bg=NAV_2, fg=TEXT, insertbackground=TEXT,
                             relief="flat", bd=0, font=("Consolas", 10))
            entry.grid(row=idx, column=1, sticky="ew", ipady=8, pady=5)
            setattr(self, entry_attr, entry)
            self._dashboard_button(setup, "Browse", command).grid(
                row=idx, column=2, sticky="ew", padx=(8, 6), pady=5)
        # Compact readiness lights on the right, as in the approved mockup.
        self.mapping_chip_frame = tk.Frame(setup, bg=CARD)
        self.mapping_chip_frame.grid(row=0, column=3, rowspan=2,
                                     sticky="nsw", padx=(18, 14), pady=(0, 4))
        self.context_label = self._dashboard_label(setup, "Not detected", size=9, color=MUTED)
        self.context_label.grid(row=2, column=0, columnspan=2, sticky="w",
                                padx=16, pady=(0, 7))
        self._dashboard_button(setup, "Open Output", self.open_output).grid(
            row=2, column=2, sticky="e", padx=(8, 6), pady=(0, 7))

        # Entity selection belongs to Project Setup, not a second mode section.
        entity_area = tk.Frame(setup, bg=CARD)
        entity_area.grid(row=3, column=0, columnspan=4, sticky="ew",
                         padx=16, pady=(3, 12))
        entity_header = tk.Frame(entity_area, bg=CARD)
        entity_header.pack(fill="x")
        self._dashboard_label(
            entity_header, "Eligible entities after Validate (Ctrl/Shift for multiple)",
            size=9, color=MUTED).pack(side="left")
        tk.Button(entity_header, text="Select All", command=self._select_all_aois,
                  bg=ROW_BG, fg=TEXT, relief="flat", padx=9).pack(side="right")
        self.aoi_list = tk.Listbox(
            entity_area, selectmode=tk.EXTENDED, exportselection=False, height=5,
            bg=NAV_2, fg=TEXT, selectbackground=GREEN_DARK,
            selectforeground="#ffffff", font=("Consolas", 10), relief="flat")
        self.aoi_list.pack(fill="x", expand=True, pady=(5, 0))
        self.aoi_list.bind("<<ListboxSelect>>",
                           lambda _event: self._update_aoi_context())

        # Progress table: checkboxes are the authoritative product selectors.
        progress_outer, progress = self.card(page, "Products & Progress")
        progress_outer.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=6)
        progress.grid_columnconfigure(0, weight=1)
        toolbar = tk.Frame(progress, bg=CARD)
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(0, 7))
        self.selected_count_label = self._dashboard_label(
            toolbar, "5 products selected", size=9, color=MUTED)
        self.selected_count_label.pack(side="left")
        self._dashboard_button(toolbar, "Select All", lambda: self.set_all_products(True)).pack(
            side="right", padx=(6, 0))
        self._dashboard_button(toolbar, "Deselect All", lambda: self.set_all_products(False)).pack(
            side="right", padx=(6, 0))

        table = tk.Frame(progress, bg=CARD)
        table.grid(row=1, column=0, sticky="nsew", padx=13, pady=(0, 12))
        table.grid_columnconfigure(1, weight=2)
        table.grid_columnconfigure(2, weight=3)
        for col, label in enumerate(("PROCESS", "PRODUCT / DESCRIPTION", "PROGRESS", "STATUS")):
            self._dashboard_label(table, label, size=9, bold=True,
                                  color=MUTED, bg=ROW_BG, anchor="w").grid(
                row=0, column=col, sticky="ew", ipady=10, padx=1)
        self.exec_rows = {}
        self.product_checks = {}
        descriptions = {
            "TIFF": "GeoTIFF · RGBI masked",
            "ECW": "Compressed imagery",
            "LAZ": "Point cloud",
            "zLAS": "Compressed point cloud",
            "Tile Index": "Shapefile index",
        }
        for i, product in enumerate(("TIFF", "ECW", "LAZ", "zLAS", "Tile Index"), start=1):
            row_bg = CARD if i % 2 else ROW_BG
            select = tk.Frame(table, bg=row_bg)
            select.grid(row=i, column=0, sticky="nsew", padx=1, pady=1)
            cb = tk.Checkbutton(
                select, variable=self.product_vars[product],
                command=lambda n=product: self._enforce_product_dependencies(n),
                bg=row_bg, fg=TEXT, activebackground=row_bg,
                activeforeground=TEXT, selectcolor=NAV_2, bd=0,
                highlightthickness=0, cursor="hand2",
            )
            cb.pack(side="left", padx=7, pady=7)
            self.product_checks[product] = cb
            name_box = tk.Frame(table, bg=row_bg)
            name_box.grid(row=i, column=1, sticky="nsew", padx=1, pady=1)
            self._dashboard_label(name_box, product, size=10, bold=True,
                                  bg=row_bg).pack(anchor="w", padx=8, pady=(3, 0))
            self._dashboard_label(name_box, descriptions[product], size=9,
                                  color=MUTED, bg=row_bg).pack(anchor="w", padx=8, pady=(0, 3))
            prog_box = tk.Frame(table, bg=row_bg)
            prog_box.grid(row=i, column=2, sticky="nsew", padx=1, pady=1)
            pb = ttk.Progressbar(prog_box, maximum=100, value=0, length=110)
            pb.pack(side="left", fill="x", expand=True, padx=(6, 8), pady=10)
            count = self._dashboard_label(prog_box, "0 / 0", size=9, bg=row_bg,
                                          width=8, anchor="e")
            count.pack(side="right", padx=(0, 5))
            status_box = tk.Frame(table, bg=row_bg)
            status_box.grid(row=i, column=3, sticky="nsew", padx=1, pady=1)
            status = self.badge(status_box, "Not started", "muted")
            status.pack(side="left", padx=7, pady=9)
            self.exec_rows[product] = (pb, count, status)

        # Keep the established build callbacks and selected-product dependencies.
        actions_outer, actions = self.card(page, "Processing Controls")
        actions_outer.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=6)
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        self.run_btn = self._dashboard_button(
            actions, "▶  Process", lambda: self.start_build(False), primary=True)
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(14, 5), pady=(4, 7))
        self.rerun_btn = self._dashboard_button(
            actions, "↻  Process Missing", lambda: self.start_build(True))
        self.rerun_btn.grid(row=0, column=1, sticky="ew", padx=(5, 14), pady=(4, 7))
        self.pause_btn = self._dashboard_button(actions, "⏸  Pause", self.toggle_pause)
        self.pause_btn.configure(state="disabled")
        self.pause_btn.grid(row=1, column=0, sticky="ew", padx=(14, 5), pady=(0, 7))
        self.stop_btn = self._dashboard_button(actions, "■  Stop", self.stop_build)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.grid(row=1, column=1, sticky="ew", padx=(5, 14), pady=(0, 7))
        self.validate_btn = self._dashboard_button(actions, "✓  Validate Input", self.validate_setup)
        self.validate_btn.grid(row=2, column=0, columnspan=2, sticky="ew",
                               padx=14, pady=(0, 12))
        tk.Frame(actions, bg=BORDER, height=1).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=14)
        details = tk.Frame(actions, bg=CARD)
        details.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=14, pady=(8, 6))
        details.grid_columnconfigure(1, weight=1)
        self.detail_values = {}
        detail_rows = (
            ("Mode", "mode"),
            ("Selected products", "selected"),
            ("Processing units", "segments"),
            ("Source TIFFs", "source_tiffs"),
            ("Source LiDAR files", "source_lidar"),
            ("Parallel workers", "workers"),
            ("Current stage", "stage"),
            ("Elapsed", "elapsed"),
        )
        for i, (label, key) in enumerate(detail_rows):
            self._dashboard_label(details, label, size=9, color=MUTED).grid(
                row=i, column=0, sticky="w", pady=3)
            value = self._dashboard_label(details, "—", size=9, bold=key in ("mode", "stage"),
                                          anchor="w", justify="left", wraplength=250)
            value.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=3)
            self.detail_values[key] = value

        # Compact by default; details remain available on demand.
        log_outer, log_panel = self.card(page)
        log_outer.grid(row=3, column=0, columnspan=2, sticky="nsew",
                       padx=12, pady=(6, 12))
        log_head = tk.Frame(log_panel, bg=CARD)
        log_head.pack(fill="x", padx=13, pady=(10, 7))
        self._dashboard_label(log_head, "▸  Activity Log", size=11, bold=True).pack(side="left")
        self._dashboard_label(log_head, "Full-screen log available on the left",
                              size=9, color=MUTED).pack(side="left", padx=14)
        self.log_toggle_btn = self._dashboard_button(
            log_head, "Show Details ▼", self.toggle_log_details)
        self.log_toggle_btn.pack(side="right")
        self._dashboard_button(log_head, "Open QA Folder", self.open_qa_folder).pack(side="right", padx=(0, 7))
        self._dashboard_button(log_head, "Clear", self.clear_log).pack(side="right", padx=(0, 7))
        log_view = tk.Frame(log_panel, bg=LOG_BG)
        log_view.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_scroll = ttk.Scrollbar(log_view, orient="vertical")
        self.log_scroll.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_view, height=12, bg=LOG_BG, fg=LOG_TEXT,
            insertbackground=TEXT, relief="flat", font=("Consolas", 10),
            padx=10, pady=8, wrap="word",
            yscrollcommand=self.log_scroll.set)
        self.log_scroll.configure(command=self.log_text.yview)
        # Log remains hidden until the operator explicitly expands it.
        self.log_text.pack_forget()
        self.log_text.tag_configure("INFO", foreground="#82d7a0")
        self.log_text.tag_configure("WARN", foreground="#ffe29c")
        self.log_text.tag_configure("ERROR", foreground="#ff9292")
        self.log_text.configure(state="disabled")
        self._log_expanded = False
        self._sync_product_selection()
        return viewport

    def _build_log_page(self):
        """Full-width searchable/scrollable diagnostics without dashboard clipping."""
        page = tk.Frame(self.content_wrap, bg=BG)
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 10))
        tk.Label(head, text="Activity Log", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Button(head, text="Clear", command=self.clear_log,
                  bg=ROW_BG, fg=TEXT, relief="flat", padx=14, pady=8).pack(side="right")
        box = tk.Frame(page, bg=LOG_BG, highlightthickness=1,
                       highlightbackground=BORDER)
        box.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        vertical = ttk.Scrollbar(box, orient="vertical")
        vertical.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(box, orient="horizontal")
        horizontal.pack(side="bottom", fill="x")
        self.log_full_text = tk.Text(
            box, bg=LOG_BG, fg=LOG_TEXT, insertbackground=TEXT,
            font=("Consolas", 10), padx=12, pady=12, wrap="none",
            xscrollcommand=horizontal.set, yscrollcommand=vertical.set,
            relief="flat", bd=0, state="disabled")
        self.log_full_text.pack(fill="both", expand=True)
        vertical.configure(command=self.log_full_text.yview)
        horizontal.configure(command=self.log_full_text.xview)
        for key, color in (("INFO", "#82d7a0"), ("WARN", "#ffe29c"),
                           ("ERROR", "#ff9292")):
            self.log_full_text.tag_configure(key, foreground=color)
        return page

    def toggle_log_details(self):
        self._log_expanded = not self._log_expanded
        if self._log_expanded:
            self.log_text.pack(fill="both", expand=True)
            self.log_toggle_btn.configure(text="Hide Details ▲")
        else:
            self.log_text.pack_forget()
            self.log_toggle_btn.configure(text="Show Details ▼")

    def set_all_products(self, selected):
        if self.running or self.validating:
            return
        before = tuple(self.selected_products())
        for name, value in self.product_vars.items():
            value.set(bool(selected) and (name != "Tile Index" or self.has_segmented_entities()))
        if self.input_validated and tuple(self.selected_products()) != before:
            # Eligibility was checked for a different set of required sources.
            self._invalidate_input_validation()
        else:
            self._sync_product_selection()

    def _sync_product_selection(self):
        selected = len(self.selected_products())
        if hasattr(self, "selected_count_label"):
            self.selected_count_label.configure(
                text=f"{selected} of {len(self.product_vars)} products selected")
        if hasattr(self, "summary_values"):
            self.summary_values["products"].configure(text=str(selected))
        if hasattr(self, "run_btn") and not self.running:
            state = "normal" if selected and self.input_validated and self.context else "disabled"
            self.run_btn.configure(state=state)
            self.rerun_btn.configure(state=state)
        self._refresh_build_details()

    def _enforce_product_dependencies(self, changed):
        if changed == "ECW" and self.product_vars["ECW"].get():
            self.product_vars["TIFF"].set(True)
        if changed == "TIFF" and not self.product_vars["TIFF"].get():
            self.product_vars["ECW"].set(False)
        if changed == "zLAS" and self.product_vars["zLAS"].get():
            self.product_vars["LAZ"].set(True)
        if changed == "LAZ" and not self.product_vars["LAZ"].get():
            self.product_vars["zLAS"].set(False)
        if hasattr(self, "input_validated") and self.input_validated:
            self._invalidate_input_validation()
        else:
            self._sync_product_selection()

    # ---------- Mapping page ----------
    def _build_mapping_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        title = tk.Label(page, text="Data Sources", bg=BG, fg=TEXT, font=("Segoe UI",18,"bold"))
        title.pack(anchor="w", padx=18, pady=(18,10))
        outer, inner = self.card(page, "Current source mapping")
        outer.pack(fill="x", padx=18, pady=8)
        self.mapping_detail = tk.Frame(inner, bg=CARD)
        self.mapping_detail.pack(fill="x", padx=16, pady=(0,14))
        tk.Button(page, text="Refresh Mapping", command=self.refresh_mapping,
                  bg=GREEN, fg="white", relief="flat", padx=16, pady=9).pack(anchor="w", padx=18, pady=8)
        return page

    # ---------- Settings page ----------
    def _build_settings_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        tk.Label(page, text="Settings", bg=BG, fg=TEXT, font=("Segoe UI",18,"bold")).pack(anchor="w", padx=18, pady=(18,10))
        outer, inner = self.card(page, "Processing")
        outer.pack(fill="x", padx=18, pady=8)
        grid = tk.Frame(inner, bg=CARD); grid.pack(fill="x", padx=16, pady=(2,16))

        tk.Label(grid, text="Parallel workers", bg=CARD, fg=TEXT, font=("Segoe UI",10)).grid(row=0,column=0,sticky="w",pady=7)
        self.workers_var = tk.StringVar(value=str(self.cfg.get("parallel_workers", self.cfg.get("ecw_workers", 2))))
        tk.Entry(grid, textvariable=self.workers_var, width=14, relief="solid", bd=1).grid(row=0,column=1,sticky="w",padx=18)
        tk.Label(grid, text="-1 = Auto / all available CPU. Applied to TIFF, ECW, LAZ and zLAS.", bg=CARD, fg=MUTED, font=("Segoe UI",9)).grid(row=0,column=2,sticky="w")

        tk.Label(grid, text="Global Mapper", bg=CARD, fg=TEXT, font=("Segoe UI",10)).grid(row=1,column=0,sticky="w",pady=7)
        self.gm_var = tk.StringVar(value=str(self.cfg.get("global_mapper_exe") or core.detect_global_mapper(self.cfg) or ""))
        tk.Entry(grid, textvariable=self.gm_var, width=70, relief="solid", bd=1).grid(row=1,column=1,columnspan=2,sticky="ew",padx=18)
        tk.Button(grid, text="Browse", command=self.choose_gm, relief="solid", bd=1, bg="#fff").grid(row=1,column=3,padx=6)

        tk.Label(grid, text="EzLAS", bg=CARD, fg=TEXT, font=("Segoe UI",10)).grid(row=2,column=0,sticky="w",pady=7)
        tk.Label(grid, text=str(EZLAS), bg=CARD, fg=MUTED, font=("Segoe UI",9)).grid(row=2,column=1,columnspan=3,sticky="w",padx=18)

        grid.grid_columnconfigure(2, weight=1)
        tk.Button(page, text="Save Settings", command=self.save_settings,
                  bg=GREEN, fg="white", activebackground=GREEN_DARK,
                  relief="flat", font=("Segoe UI",10,"bold"), padx=18, pady=9).pack(anchor="w", padx=18, pady=8)
        return page

    # ---------- QA page ----------
    def _build_qa_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        top = tk.Frame(page, bg=BG); top.pack(fill="x", padx=18, pady=(18,10))
        tk.Label(top, text="QA & Reports", bg=BG, fg=TEXT, font=("Segoe UI",18,"bold")).pack(side="left")
        tk.Button(top, text="Open QA Folder", command=self.open_qa_folder, bg="#fff", relief="solid", bd=1).pack(side="right", padx=(6,0))
        tk.Button(top, text="Refresh", command=self.refresh_qa_tree, bg="#fff", relief="solid", bd=1).pack(side="right")
        outer, inner = self.card(page, "QA files for current circuit / revision")
        outer.pack(fill="both", expand=True, padx=18, pady=(0,18))
        self.qa_tree = ttk.Treeview(inner, columns=("path","size"), show="headings")
        self.qa_tree.heading("path", text="File")
        self.qa_tree.heading("size", text="Size")
        self.qa_tree.column("path", width=850, anchor="w")
        self.qa_tree.column("size", width=100, anchor="e")
        self.qa_tree.pack(fill="both", expand=True, padx=12, pady=(0,12))
        self.qa_tree.bind("<Double-1>", self._open_selected_qa)
        return page

    # ---------- Quick start ----------
    def _build_how_to_start_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        tk.Label(page, text="How to Start", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=18, pady=(18, 8))
        self._dashboard_label(
            page, "ATCO Retiler production workflow", bg=BG, color=MUTED
        ).pack(anchor="w", padx=18, pady=(0, 10))
        steps = (
            ("1. Select source data",
             "Use Data Sources or Browse on Build. Input must contain AOI, orthos and point_cloud as required by your selected products."),
            ("2. Check project and revision",
             "Confirm the detected circuit/revision, the approved central segment count and the AOI / Orthos / LiDAR readiness indicators."),
            ("3. Select deliverables",
             "Tick TIFF, ECW, LAZ, zLAS and/or Tile Index in Products & Progress. ECW selects TIFF; zLAS selects LAZ."),
            ("4. Validate setup",
             "Click Validate Setup and resolve missing tools or central data before processing. Check Global Mapper for ECW and EzLAS for zLAS."),
            ("5. Run or recover",
             "Process overwrites existing selected outputs after confirmation. Process Missing only fills missing products. Pause and Stop apply to active processing."),
            ("6. Review QA and deliver",
             "Open QA & Reports and verify imagery extent/masks, ECW transparency, LAZ classifications, zLAS equivalence and Tile Index attributes."),
        )
        for heading, description in steps:
            outer, inner = self.card(page)
            outer.pack(fill="x", padx=18, pady=5)
            self._dashboard_label(inner, heading, bold=True).pack(
                anchor="w", padx=16, pady=(10, 4))
            tk.Label(inner, text=description, bg=CARD, fg=MUTED,
                     font=("Segoe UI", 10), justify="left", wraplength=920
                     ).pack(anchor="w", padx=16, pady=(0, 12))
        row = tk.Frame(page, bg=BG)
        row.pack(fill="x", padx=18, pady=(8, 15))
        self._dashboard_button(
            row, "Technical Notes", lambda: self._open_doc("technical_notes.html")
        ).pack(side="left", padx=(0, 8))
        self._dashboard_button(
            row, "FAQ / Troubleshooting", lambda: self._open_doc("faq_troubleshooting_qa.html")
        ).pack(side="left")
        return page

    # ---------- About ----------
    def _build_about_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        tk.Label(page, text=f"{APP_NAME} v{APP_VERSION}", bg=BG, fg=TEXT,
                 font=("Segoe UI",22,"bold")).pack(anchor="w", padx=20, pady=(24,6))
        tk.Label(page, text="ATCO 2026 production retiling workflow",
                 bg=BG, fg=MUTED, font=("Segoe UI",11)).pack(anchor="w", padx=20)
        outer, inner = self.card(page, "Architecture")
        outer.pack(fill="x", padx=20, pady=18)
        info = (
            "GUI wrapper over the existing production processors.\n\n"
            "Imagery: processing footprints → TIFF → ECW\n"
            "LiDAR: segment crop → LAZ → zLAS\n"
            "Management: Tile Index SHP → later import as TILES into the final delivery GDB\n\n"
            "Central AOI and approved segments remain authoritative on W:\\APPS\\PIT\\ATCO.\n"
            "Technical logs and diagnostics are kept under QA; delivery folders contain client products.\n\n"
            "Bugs, improvements and issues: pit@nmgroup.com"
        )
        tk.Label(inner, text=info, justify="left", bg=CARD, fg=TEXT,
                 font=("Segoe UI",10), wraplength=900).pack(anchor="w", padx=16, pady=(0,18))
        return page

    # ---------- mapping/context ----------
    def choose_input(self):
        initial = str(self.input_root or PACKAGE_ROOT)
        p = filedialog.askdirectory(initialdir=initial, title="Select ATCO Retiler input folder")
        if not p:
            return
        self.input_root = Path(p)
        self._aoi_selection_initialized = False
        self.output_root = core.default_output_root(self.input_root)
        self.save_cfg()
        self.refresh_mapping()

    def choose_output(self):
        p = filedialog.askdirectory(initialdir=str(self.output_root or PACKAGE_ROOT), title="Select output root")
        if not p:
            return
        self.output_root = Path(p)
        self.save_cfg()
        self.refresh_mapping()

    def choose_gm(self):
        p = filedialog.askopenfilename(
            title="Select Global Mapper executable",
            filetypes=[("Executable","*.exe"),("All files","*.*")]
        )
        if p:
            self.gm_var.set(p)

    def _select_all_aois(self):
        self.aoi_list.selection_set(0, "end")
        self._update_aoi_context()

    def selected_aois(self):
        return [self.aoi_options[i] for i in self.aoi_list.curselection()
                if i < len(self.aoi_options)]

    def _update_aoi_context(self):
        self.context = None
        self.blocks = []
        self.cloud_blocks = []
        self.entity_records = []
        selected = self.selected_aois()
        if not selected:
            self.context_label.configure(text="Select one or more eligible entities")
        else:
            try:
                sys.path.insert(0, str(MODULES))
                import process_orthos as po
                from central_aoi_catalogue import validate_standalone
                from atco_delivery import YEAR, TILE_VERSION
                for item in selected:
                    entity = item["entity"]
                    shp = item["path"]
                    if item["has_segments"]:
                        rec = po.load_aoi_revision(entity, shp)
                        _, seg = po.load_segments(entity, rec["revision_code"])
                        imagery = [str(v) for v in seg["ImageryName"]]
                        cloud = [str(v) for v in seg["PointCloudName"]]
                        revision = rec["revision_code"]
                    else:
                        validate_standalone(item)
                        imagery = [f"{entity}_{YEAR}_{TILE_VERSION}"]
                        cloud = imagery[:]
                        revision = item["revision"]
                    record = {
                        "path": shp, "entity": entity, "revision": revision,
                        "imagery": imagery, "cloud": cloud,
                        "has_segments": item["has_segments"], "kind": item["kind"],
                        "layer": item.get("layer"), "source_ref": item.get("source_ref"),
                    }
                    self.entity_records.append(record)
                    self.blocks.extend(imagery)
                    self.cloud_blocks.extend(cloud)
                first = self.entity_records[0]
                self.context = (first["entity"], first["revision"])
                self.context_label.configure(
                    text=f"{len(self.entity_records)} eligible entity(s) | "
                         + ", ".join(f"{r['entity']} ({r['kind']})"
                                     for r in self.entity_records[:5]))
            except Exception as exc:
                self.context_label.configure(text="AOI validation error")
                self.log(f"AOI preflight: {exc}", "WARN")
                self.context = None
                self.blocks = []
                self.cloud_blocks = []
                self.entity_records = []
        if hasattr(self, "product_checks"):
            allow_index = bool(self.has_segmented_entities())
            self.product_checks["Tile Index"].configure(
                state="normal" if allow_index else "disabled")
            if not allow_index:
                self.product_vars["Tile Index"].set(False)
            self._sync_product_selection()
        if hasattr(self, "summary_values"):
            self.summary_values["project"].configure(
                text=f"{len(self.entity_records)} entities" if self.entity_records else "Awaiting input")
            self.summary_values["segments"].configure(
                text=str(sum(len(x["imagery"]) for x in self.entity_records
                             if x["has_segments"])) if self.entity_records else "—")
        self.refresh_product_status()

    def refresh_mapping(self):
        """Map local inputs only. Never enumerate remote registry entries here."""
        self.input_entry.delete(0, "end")
        self.input_entry.insert(0, human_path(self.input_root))
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, human_path(self.output_root))
        self.mapped = (core.map_input(self.input_root)
                       if self.input_root and self.input_root.is_dir()
                       else {"aoi": None, "orthos": None, "point_cloud": None})
        for w in self.mapping_chip_frame.winfo_children():
            w.destroy()
        for key, label in (("aoi", "AOI"), ("orthos", "Orthos"),
                           ("point_cloud", "Point Cloud (LiDAR)")):
            ok = self.mapped.get(key) is not None
            self.badge(self.mapping_chip_frame,
                       f"{'●' if ok else '○'}  {label}",
                       "ok" if ok else "bad").pack(side="top", anchor="w", pady=3)
        for w in self.mapping_detail.winfo_children():
            w.destroy()
        for key, label in (("aoi", "AOI"), ("orthos", "Orthos"),
                           ("point_cloud", "Point Cloud")):
            p = self.mapped.get(key)
            row = tk.Frame(self.mapping_detail, bg=CARD)
            row.pack(fill="x", pady=5)
            self.badge(row, "OK" if p else "MISSING", "ok" if p else "bad").pack(side="left")
            tk.Label(row, text=label, width=16, anchor="w", bg=CARD, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).pack(side="left", padx=10)
            tk.Label(row, text=str(p or "Not mapped"), anchor="w", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
        self._invalidate_input_validation()
        self._count_source_files()
        if hasattr(self, "summary_values"):
            self.summary_values["sources"].configure(
                text=f"{self.source_tiff_count} + {self.source_lidar_count}")
        self.refresh_product_status()

    def _invalidate_input_validation(self):
        self.input_validated = False
        self.context = None
        self.blocks = []
        self.cloud_blocks = []
        self.entity_records = []
        self.aoi_options = []
        self.aoi_list.configure(state="normal")
        self.aoi_list.delete(0, "end")
        self.context_label.configure(text="Select local input, then Validate Input to find eligible entities")
        self.product_vars["Tile Index"].set(False)
        self.product_checks["Tile Index"].configure(state="disabled")
        self._sync_product_selection()
        if hasattr(self, "summary_values"):
            self.summary_values["project"].configure(text="Awaiting validation")
            self.summary_values["segments"].configure(text="—")

    def _apply_eligible_entities(self, eligible, excluded):
        """Called on the UI thread after spatial checks complete."""
        self.validating = False
        self.validate_btn.configure(state="normal", text="✓  Validate Input")
        self.aoi_options = list(eligible)
        self.aoi_list.delete(0, "end")
        for item in eligible:
            self.aoi_list.insert(
                "end", f"{item['entity']}  [{item['kind']}]  {item['revision']}")
        if eligible:
            self.input_validated = True
            self.aoi_list.selection_set(0)
            self._update_aoi_context()
            self.log(f"VALIDATION: {len(eligible)} eligible entities; "
                     f"{len(excluded)} excluded. Footprint coverage is not pixel QA.", "INFO")
            for name, reason in excluded:
                self.log(f"Excluded {name}: {reason}", "WARN")
            messagebox.showinfo(APP_NAME,
                f"Input validated. {len(eligible)} eligible entities found.\n"
                "Choose the entities to process.\n\n"
                "Coverage uses spatial footprints; output QA still applies.")
        else:
            self._invalidate_input_validation()
            self.log(f"VALIDATION: 0 eligible entities; {len(excluded)} exclusions.", "WARN")
            for name, reason in excluded:
                self.log(f"Excluded {name}: {reason}", "WARN")
            messagebox.showwarning(APP_NAME,
                "No circuit or area has sufficient matching local AOI and "
                "source coverage. Check Activity Log.")
        self.refresh_product_status()

    def has_segmented_entities(self):
        return any(r.get("has_segments") for r in self.entity_records)

    def selected_products(self):
        return [k for k, v in self.product_vars.items() if v.get() and
                (k != "Tile Index" or self.has_segmented_entities()
                 or not self.entity_records)]

    def product_paths(self):
        if not self.context:
            return None
        circuit, rev = self.context
        return core.output_base(self.output_root, circuit)

    def _is_valid_file(self,p):
        try:
            return p.is_file() and p.stat().st_size > 0
        except OSError:
            return False

    def status_snapshot(self):
        if not self.entity_records:
            return {}
        counts = {key: [0, 0] for key in ("TIFF", "ECW", "LAZ", "zLAS", "Tile Index")}
        for record in self.entity_records:
            entity = record["entity"]
            base = core.output_base(self.output_root, entity)
            for product, folder, ext in (
                ("TIFF", "TIFF", ".tif"),
                ("ECW", "ECW", ".ecw"),
                ("LAZ", "LAZ", ".laz"),
                ("zLAS", "zLAS", ".zlas"),
            ):
                names = record["cloud"] if product in ("LAZ", "zLAS") else record["imagery"]
                counts[product][0] += len(names)
                target_folder = product_folder(product, converter=not record["has_segments"])
                counts[product][1] += sum(
                    1 for name in names if self._is_valid_file(base / target_folder / f"{name}{ext}"))
            if record["has_segments"]:
                shp = core.tile_index_shp_path(base, entity)
                counts["Tile Index"][0] += 1
                if (self._is_valid_file(shp) and shp.with_suffix(".dbf").is_file()
                        and shp.with_suffix(".shx").is_file()):
                    counts["Tile Index"][1] += 1
        return {key: tuple(value) for key, value in counts.items()}

    def refresh_product_status(self):
        snap = self.status_snapshot()
        for prod in ("TIFF","ECW","LAZ","zLAS","Tile Index"):
            total,present = snap.get(prod,(0,0))
            missing = max(total-present,0)
            pb,count,st = self.exec_rows[prod]
            pct = (present/total*100) if total else 0
            pb.configure(value=pct, style="TProgressbar")
            count.configure(text=f"{present} / {total}")
            if total and missing == 0:
                self._set_badge(st,"✓ Complete","ok")
            elif total and present > 0:
                self._set_badge(st,f"{missing} missing","warn")
            elif total:
                self._set_badge(st,"Not started","muted")
            else:
                self._set_badge(st,"Not checked","muted")
        self._refresh_build_details()

    def _count_source_files(self):
        self.source_tiff_count = 0
        self.source_lidar_count = 0

        orthos = self.mapped.get("orthos")
        if orthos and Path(orthos).is_dir():
            try:
                self.source_tiff_count = sum(
                    1 for p in Path(orthos).rglob("*")
                    if p.is_file() and p.suffix.casefold() in (".tif", ".tiff")
                )
            except OSError:
                self.source_tiff_count = 0

        point_cloud = self.mapped.get("point_cloud")
        if point_cloud and Path(point_cloud).is_dir():
            try:
                self.source_lidar_count = sum(
                    1 for p in Path(point_cloud).rglob("*")
                    if p.is_file() and p.suffix.casefold() in (".las", ".laz")
                )
            except OSError:
                self.source_lidar_count = 0

    def _elapsed_seconds(self):
        if self.build_start_time is not None and self.running:
            return max(0.0, time.time() - self.build_start_time)
        return max(0.0, self.build_elapsed_final)

    def _format_elapsed(self, seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _refresh_build_details(self):
        if not hasattr(self, "detail_values"):
            return
        workers = self.cfg.get("parallel_workers", self.cfg.get("ecw_workers", 2))
        cpu = os.cpu_count() or 1
        workers_text = f"Auto / all available ({cpu})" if workers == -1 else str(workers)
        selected = ", ".join(self.selected_products()) or "None"
        values = {
            "mode": self.build_mode,
            "selected": selected,
            "segments": str(len(self.blocks)) if self.blocks else "-",
            "source_tiffs": str(self.source_tiff_count) if self.mapped.get("orthos") else "-",
            "source_lidar": str(self.source_lidar_count) if self.mapped.get("point_cloud") else "-",
            "workers": workers_text,
            "stage": self.current_stage,
            "elapsed": self._format_elapsed(self._elapsed_seconds()),
        }
        for key, value in values.items():
            widget = self.detail_values.get(key)
            if widget:
                widget.configure(text=value)

    # ---------- build ----------
    def start_build(self, rerun):
        if self.running:
            return
        if not self.input_validated:
            messagebox.showwarning(APP_NAME, "Run Validate Input before processing.")
            return
        selected = self.selected_products()
        if not selected:
            messagebox.showwarning(APP_NAME, "Select at least one product to build.")
            return
        if not self.context:
            messagebox.showerror(APP_NAME, "Circuit/revision could not be detected from the AOI.")
            return

        needs_ortho = any(x in selected for x in ("TIFF","ECW"))
        needs_pc = any(x in selected for x in ("LAZ","zLAS"))
        if needs_ortho and not self.mapped.get("orthos"):
            messagebox.showerror(APP_NAME, "Orthos input is missing.")
            return
        if needs_pc and not self.mapped.get("point_cloud"):
            messagebox.showerror(APP_NAME, "Point Cloud input is missing.")
            return
        # Missing PDAL is installed noninteractively in the worker before any
        # TIFF/ECW work begins. Do not block the GUI or pre-download for imagery.
        if "zLAS" in selected and not EZLAS.is_file():
            messagebox.showerror(APP_NAME, f"EzLAS.exe is missing:\n{EZLAS}")
            return

        gm = None
        if "ECW" in selected:
            gm = core.detect_global_mapper(self.cfg)
            if not gm:
                messagebox.showerror(APP_NAME, "Global Mapper was not detected. ECW cannot be built.")
                return

        if not rerun:
            snap = self.status_snapshot()
            found = [(p, snap.get(p,(0,0))[1]) for p in selected if snap.get(p,(0,0))[1] > 0]
            if found:
                circuit,rev = self.context
                lines = "\n".join(f"{p}: {n} existing" for p,n in found)
                if not messagebox.askyesno(
                    "Overwrite existing output",
                    f"Existing output found for {circuit} / {rev}:\n\n{lines}\n\n"
                    "Full Build will overwrite existing selected products.\n"
                    "Use Rerun Missing if you only want to fill gaps.\n\nContinue?"
                ):
                    return

        self.running = True
        self.aoi_list.configure(state="disabled")
        for check in self.product_checks.values():
            check.configure(state="disabled")
        self.paused = False
        self.stop_requested = False
        self.suspended_pids = []
        self.build_mode = "Process Missing" if rerun else "Process All"
        self.current_stage = "Starting"
        self.build_start_time = time.time()
        self.build_elapsed_final = 0.0
        self.run_btn.configure(state="disabled")
        self.rerun_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="⏸  Pause")
        self.stop_btn.configure(state="normal")
        self.status_text.configure(text="Running")
        self.status_dot.configure(fg=BLUE)
        self.session_created = {k: 0 for k in self.session_created}
        self.log(f"{'PROCESS MISSING' if rerun else 'PROCESS ALL'} - {self.context[0]} / {self.context[1]}", "INFO")

        records = list(self.entity_records)
        input_sources = dict(self.mapped)
        output_root = Path(self.output_root)
        workers = int(self.cfg.get("parallel_workers", self.cfg.get("ecw_workers", 2)))
        th = threading.Thread(
            target=self._build_worker,
            args=(rerun, selected, gm, records, input_sources, output_root, workers),
            daemon=True)
        th.start()

    def _build_worker(self, rerun, selected, gm, records, input_sources, output_root, workers):
        try:
            env = os.environ.copy()
            env["ATCO_PARALLEL_WORKERS"] = str(workers)
            overwrite = [] if rerun else ["--overwrite"]
            if any(product in selected for product in ("LAZ", "zLAS")):
                self.log_queue.put(("LINE",
                    "Checking optional PDAL support for LAZ/zLAS..."))
                rc = self._run_stream(
                    [sys.executable, str(TOOL_ROOT / "ensure_lidar_support.py")],
                    env=env)
                if rc:
                    raise RuntimeError(
                        "Automatic PDAL setup failed. See Activity Log for Conda errors.")
                if self.stop_requested:
                    self.log_queue.put(("STOP", "Build stopped during PDAL setup"))
                    return
            for idx, record in enumerate(records, 1):
                if self.stop_requested:
                    break
                aoi = str(record["path"])
                self.log_queue.put(("LINE",
                    f"ENTITY {idx}/{len(records)}: {record['entity']} ({record['kind']})"))
                if not record["has_segments"]:
                    products = [p for p in selected if p != "Tile Index"]
                    if not products:
                        self.log_queue.put(("LINE",
                            "Skipping area entity: Tile Index only applies to circuits"))
                        continue
                    args = [
                        sys.executable, str(MODULES / "process_aoi_converter.py"),
                        "--aoi", aoi, "--entity", record["entity"],
                        "--revision", record["revision"],
                        * (["--aoi-layer", record["layer"]] if record.get("layer") else []),
                        "--output-root", str(output_root), *overwrite,
                    ]
                    if any(p in products for p in ("TIFF", "ECW")):
                        args += ["--ortho-source", str(input_sources["orthos"])]
                    if any(p in products for p in ("LAZ", "zLAS")):
                        args += ["--cloud-source", str(input_sources["point_cloud"])]
                    for product in products:
                        args.append("--" + product.lower())
                    if "ECW" in products:
                        args += ["--global-mapper-exe", str(gm)]
                    if "zLAS" in products:
                        args += ["--ezlas-exe", str(EZLAS)]
                    rc = self._run_stream(args, env=env)
                    if rc:
                        raise RuntimeError(f"Converter failed for {record['entity']} (exit {rc})")
                    self.log_queue.put(("ENTITY_DONE", record["entity"]))
                    continue
                if any(p in selected for p in ("TIFF", "ECW")):
                    args = [
                        sys.executable, str(MODULES / "process_orthos.py"),
                        "--aoi", aoi, "--source", str(input_sources["orthos"]),
                        "--output-root", str(output_root), *overwrite,
                    ]
                    if "ECW" in selected:
                        args += ["--global-mapper-exe", str(gm)]
                    else:
                        args += ["--tiff-only"]
                    rc = self._run_stream(args, env=env)
                    if rc:
                        raise RuntimeError(f"Imagery failed for {record['entity']} (exit {rc})")
                if self.stop_requested:
                    break
                if any(p in selected for p in ("LAZ", "zLAS", "Tile Index")):
                    args = [
                        sys.executable, str(MODULES / "process_lidar.py"),
                        "--aoi", aoi, "--output-root", str(output_root), *overwrite,
                    ]
                    if any(p in selected for p in ("LAZ", "zLAS")):
                        args += ["--source", str(input_sources["point_cloud"])]
                    if "zLAS" in selected:
                        args += ["--zlas", "--ezlas-exe", str(EZLAS)]
                    if "Tile Index" in selected:
                        args += ["--tile-index"]
                    rc = self._run_stream(args, env=env)
                    if rc:
                        raise RuntimeError(f"LiDAR failed for {record['entity']} (exit {rc})")
                self.log_queue.put(("ENTITY_DONE", record["entity"]))
            if self.stop_requested:
                self.log_queue.put(("STOP", "Build stopped by operator"))
            else:
                self.log_queue.put(("DONE", f"Build complete: {len(records)} entities"))
        except Exception as exc:
            if self.stop_requested:
                self.log_queue.put(("STOP", "Build stopped by operator"))
            else:
                self.log_queue.put(("FAIL", str(exc)))

    def _run_stream(self, args, env=None):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=0, env=env, creationflags=creationflags
        )
        buf = ""
        while True:
            ch = self.process.stdout.read(1)
            if ch == "" and self.process.poll() is not None:
                if buf.strip():
                    self.log_queue.put(("LINE",buf.strip()))
                break
            if ch in ("\n","\r"):
                if buf.strip():
                    self.log_queue.put(("LINE",buf.strip()))
                buf = ""
            else:
                buf += ch
        return self.process.wait()

    def _windows_process_tree(self, root_pid):
        """Return root PID plus all descendants using Toolhelp32Snapshot."""
        if os.name != "nt":
            return [root_pid]

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        MAX_PATH = 260

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * MAX_PATH),
            ]

        k32 = ctypes.windll.kernel32
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return [root_pid]

        parent_map = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                parent_map.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
                ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snapshot)

        result = []
        queue_pids = [int(root_pid)]
        seen = set()
        while queue_pids:
            pid = queue_pids.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            result.append(pid)
            queue_pids.extend(parent_map.get(pid, []))
        return result

    def _nt_process_action(self, pid, suspend=True):
        if os.name != "nt":
            return False
        PROCESS_SUSPEND_RESUME = 0x0800
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        handle = k32.OpenProcess(PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            if suspend:
                status = ntdll.NtSuspendProcess(handle)
            else:
                status = ntdll.NtResumeProcess(handle)
            return status == 0
        finally:
            k32.CloseHandle(handle)

    def _terminate_process_tree(self):
        if not self.process or self.process.poll() is not None:
            return
        try:
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    capture_output=True, text=True, creationflags=creationflags
                )
            else:
                self.process.kill()
        except Exception as exc:
            self.log(f"Stop: could not terminate full process tree: {exc}", "WARN")

    def _cleanup_partial_outputs(self):
        # A stopped multi-entity run may leave temporary files beyond the first entity.
        bases = ([core.output_base(self.output_root, r["entity"]) for r in self.entity_records]
                 if self.entity_records else [self.current_base()])
        for base in bases:
            if not base or not base.exists():
                continue
            for p in list(base.rglob("*.tmp")) + list(base.rglob("*.tmp.*")):
                try:
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            for folder in (base / name for product in ("TIFF", "ECW", "LAZ", "zLAS")
                           for name in (product_folder(product), product_folder(product, converter=True))):
                if folder.is_dir():
                    for p in folder.iterdir():
                        try:
                            if p.is_file() and p.stat().st_size == 0:
                                p.unlink()
                        except OSError:
                            pass
            shutil.rmtree(base / "QA" / "LiDAR" / "temp_las_for_zlas",
                          ignore_errors=True)
            shutil.rmtree(base / "QA" / "Converter" / "temp_las_for_zlas",
                          ignore_errors=True)

    def stop_build(self, confirm=True):
        if not self.running:
            return
        if confirm and not messagebox.askyesno(APP_NAME, "Stop the current build? Completed products will be kept."):
            return
        self.stop_requested = True
        self.status_text.configure(text="Stopping...")
        self.current_stage = "Stopping"
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled")
        self.log("Stopping process tree...", "WARN")
        self._terminate_process_tree()
        self._cleanup_partial_outputs()

    def on_close(self):
        if self.running:
            if not messagebox.askyesno(APP_NAME, "A build is running. Stop it and exit?"):
                return
            self.stop_requested = True
            self._terminate_process_tree()
            self._cleanup_partial_outputs()
        self.destroy()

    def toggle_pause(self):
        if not self.running or not self.process or self.process.poll() is not None:
            return

        if os.name != "nt":
            messagebox.showwarning(APP_NAME, "Pause/Resume is currently implemented for Windows.")
            return

        if not self.paused:
            pids = self._windows_process_tree(self.process.pid)
            # Suspend parent first so it cannot create more child jobs while the
            # existing descendants are being suspended.
            suspended = []
            for pid in pids:
                if self._nt_process_action(pid, suspend=True):
                    suspended.append(pid)
            self.suspended_pids = suspended
            self.paused = True
            self.pause_btn.configure(text="▶  Resume")
            self.status_text.configure(text="Paused")
            self.current_stage = "Paused"
            self._refresh_build_details()
            self.status_dot.configure(fg=AMBER)
            self.log(f"Paused process tree ({len(suspended)} process(es)).", "WARN")
        else:
            # Resume children first, then the root process last.
            pids = list(reversed(self.suspended_pids or self._windows_process_tree(self.process.pid)))
            resumed = 0
            for pid in pids:
                if self._nt_process_action(pid, suspend=False):
                    resumed += 1
            self.suspended_pids = []
            self.paused = False
            self.pause_btn.configure(text="⏸  Pause")
            self.status_text.configure(text="Running")
            self.current_stage = "Resuming"
            self._refresh_build_details()
            self.status_dot.configure(fg=BLUE)
            self.log(f"Resumed process tree ({resumed} process(es)).", "INFO")

    def _parse_progress(self, line):
        # e.g. ECW [######---] 6/11 | workers=11
        m = re.search(r"\b(TIFF|ECW|LAZ|zLAS)\s+\[[^\]]+\]\s+(\d+)\s*/\s*(\d+)", line)
        if m:
            p,done,total = m.group(1),int(m.group(2)),int(m.group(3))
            self.current_stage = p
            self._set_product_progress(p,done,total,running=done < total)
            self._refresh_build_details()
            return
        m = re.search(r"\b(TIFF|ECW|LAZ|zLAS)\s*:\s*(\d+)\s*/\s*(\d+)", line)
        if m:
            p,done,total = m.group(1),int(m.group(2)),int(m.group(3))
            self.current_stage = p
            self._set_product_progress(p,done,total,running=done < total)
            self._refresh_build_details()
            return
        if "Tile Index SHP:" in line:
            self.current_stage = "Tile Index"
            self._set_product_progress("Tile Index",1,1,False)
            self._refresh_build_details()

    def _set_product_progress(self, product, done, total, running=False):
        if product not in self.exec_rows:
            return
        pb,count,st = self.exec_rows[product]
        pb.configure(value=(done/total*100 if total else 0),
                     style="Blue.Horizontal.TProgressbar" if running else "TProgressbar")
        count.configure(text=f"{done} / {total}")
        self._set_badge(st, "Processing..." if running else "✓ OK", "run" if running else "ok")

    def _set_badge(self, widget, text, kind):
        colors = {
            "ok": (GREEN_SOFT, GREEN_DARK),
            "run": (BLUE_SOFT, BLUE),
            "bad": (RED_SOFT, RED),
            "warn": (AMBER_SOFT, "#a76c00"),
            "muted": (ROW_BG, MUTED)
        }
        colors["ok"] = (GREEN_SOFT, "#8fe4a2")
        colors["run"] = (BLUE_SOFT, "#a2d5f7")
        colors["bad"] = (RED_SOFT, "#ffc5c5")
        colors["warn"] = (AMBER_SOFT, "#ffe0a1")
        bg,fg = colors[kind]
        widget.configure(text=text,bg=bg,fg=fg)

    # ---------- log ----------
    def log(self, text, level="INFO"):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] ")
        self.log_text.insert("end", f"{level:<5} ", level if level in ("INFO","WARN","ERROR") else None)
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if hasattr(self, "log_full_text"):
            self.log_full_text.configure(state="normal")
            self.log_full_text.insert("end", f"[{stamp}] ")
            self.log_full_text.insert("end", f"{level:<5} ",
                                      level if level in ("INFO", "WARN", "ERROR") else None)
            self.log_full_text.insert("end", text + "\n")
            self.log_full_text.see("end")
            self.log_full_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0","end")
        self.log_text.configure(state="disabled")
        if hasattr(self, "log_full_text"):
            self.log_full_text.configure(state="normal")
            self.log_full_text.delete("1.0", "end")
            self.log_full_text.configure(state="disabled")

    def _drain_log_queue(self):
        try:
            while True:
                kind,payload = self.log_queue.get_nowait()
                if kind == "LINE":
                    self.log(payload,"INFO")
                    self._parse_progress(payload)
                elif kind == "ELIGIBLE":
                    self._apply_eligible_entities(*payload)
                elif kind == "VALIDATE_FAIL":
                    self.validating = False
                    self.validate_btn.configure(state="normal", text="✓  Validate Input")
                    self.log(f"Validation failed: {payload}", "ERROR")
                    messagebox.showerror(APP_NAME, f"Input validation failed:\\n\\n{payload}")
                elif kind == "ENTITY_DONE":
                    self.log(f"Completed entity: {payload}", "INFO")
                    self.refresh_product_status()
                elif kind == "DONE":
                    self.log(payload,"INFO")
                    self._finish_run(True)
                elif kind == "FAIL":
                    self.log(payload,"ERROR")
                    self._finish_run(False)
                elif kind == "STOP":
                    self.log(payload,"WARN")
                    self._finish_run(False, stopped=True)
        except queue.Empty:
            pass
        self.after(120,self._drain_log_queue)

    def _finish_run(self, ok, stopped=False):
        if self.build_start_time is not None:
            self.build_elapsed_final = max(0.0, time.time() - self.build_start_time)
        self.build_start_time = None
        self.current_stage = "Stopped" if stopped else ("Complete" if ok else "Failed")
        self.running = False
        self.paused = False
        self.process = None
        self.suspended_pids = []
        self.stop_requested = False
        self.run_btn.configure(state="normal")
        self.rerun_btn.configure(state="normal")
        self.aoi_list.configure(state="normal")
        for name, check in self.product_checks.items():
            check.configure(state="disabled" if name == "Tile Index" and not self.has_segmented_entities()
                            else "normal")
        self._sync_product_selection()
        self.pause_btn.configure(state="disabled", text="⏸  Pause")
        self.stop_btn.configure(state="disabled")
        self.status_text.configure(text="Stopped" if stopped else ("Ready" if ok else "Processing failed"))
        self.status_dot.configure(fg=AMBER if stopped else ("#48bd54" if ok else RED))
        self.refresh_product_status()
        if stopped:
            messagebox.showinfo(APP_NAME, "Build stopped. Completed products were kept; temporary/zero-byte files were cleaned.")
        elif ok:
            messagebox.showinfo(APP_NAME, "Processing finished.")
        else:
            messagebox.showerror(APP_NAME, "Processing failed. Check the log and QA diagnostics.")

    # ---------- validation/settings/QA ----------
    def validate_setup(self):
        """Discover processing eligibility exclusively from local input coverage."""
        if self.running or self.validating:
            return
        self._invalidate_input_validation()
        selected = self.selected_products()
        if not selected:
            messagebox.showwarning(APP_NAME, "Select at least one product before validation.")
            return
        if not self.mapped.get("aoi"):
            messagebox.showwarning(APP_NAME, "Input/aoi with polygon geometries is required.")
            return
        if any(p in selected for p in ("TIFF", "ECW")) and not self.mapped.get("orthos"):
            messagebox.showwarning(APP_NAME, "Input/orthos is missing.")
            return
        if any(p in selected for p in ("LAZ", "zLAS")) and not self.mapped.get("point_cloud"):
            messagebox.showwarning(APP_NAME, "Input/point_cloud is missing.")
            return
        self.validating = True
        self.validate_btn.configure(state="disabled", text="Checking coverage...")
        self.log("VALIDATE: indexing local AOI, image footprints, and LiDAR footprints "
                 "against central AOI revisions...", "INFO")
        mapped = dict(self.mapped)
        threading.Thread(
            target=self._eligibility_worker,
            args=(mapped, list(selected)), daemon=True).start()

    def _eligibility_worker(self, mapped, selected):
        try:
            from input_eligibility import discover_eligible
            pdal_exe = None
            if any(p in selected for p in ("LAZ", "zLAS")):
                from ensure_lidar_support import pdal_executable, pdal_ready
                if not pdal_ready():
                    self.log_queue.put(("LINE",
                        "PDAL not installed: adding optional LiDAR support before coverage scan..."))
                    rc = self._run_stream(
                        [sys.executable, str(TOOL_ROOT / "ensure_lidar_support.py")])
                    if rc:
                        raise RuntimeError("Automatic PDAL installation failed. "
                                           "See Activity Log for details.")
                    if not pdal_ready():
                        raise RuntimeError("PDAL installation completed but PDAL is unavailable.")
                pdal_exe = pdal_executable()
            eligible, excluded = discover_eligible(
                mapped, selected, pdal_exe=pdal_exe)
            self.log_queue.put(("ELIGIBLE", (eligible, excluded)))
        except Exception as exc:
            self.log_queue.put(("VALIDATE_FAIL", str(exc)))

    def save_settings(self):
        try:
            n = int(self.workers_var.get().strip())
            if n == 0 or n < -1:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Parallel workers must be -1 or a positive integer.")
            return
        self.cfg["parallel_workers"] = n
        self.cfg.pop("ecw_workers", None)
        gm = self.gm_var.get().strip()
        if gm:
            if not Path(gm).is_file():
                messagebox.showerror(APP_NAME, "Global Mapper executable does not exist.")
                return
            self.cfg["global_mapper_exe"] = gm
        else:
            self.cfg.pop("global_mapper_exe",None)
        self.save_cfg()
        messagebox.showinfo(APP_NAME, "Settings saved.")

    def current_base(self):
        if not self.context:
            return None
        return core.output_base(self.output_root, self.context[0])

    def open_output(self):
        win_open(self.output_root)

    def open_qa_folder(self):
        base = self.current_base()
        if not base:
            messagebox.showwarning(APP_NAME, "Circuit/revision is not detected yet.")
            return
        win_open(base/"QA")

    def refresh_qa_tree(self):
        if not hasattr(self,"qa_tree"):
            return
        for item in self.qa_tree.get_children():
            self.qa_tree.delete(item)
        base = self.current_base()
        if not base:
            return
        qa = base/"QA"
        if not qa.exists():
            return
        for p in sorted(x for x in qa.rglob("*") if x.is_file()):
            try:
                rel = p.relative_to(qa)
                size = p.stat().st_size
                size_txt = f"{size/1024:.1f} KB" if size < 1024**2 else f"{size/1024**2:.1f} MB"
                self.qa_tree.insert("", "end", values=(str(rel),size_txt), tags=(str(p),))
            except OSError:
                pass

    def _open_selected_qa(self,event=None):
        sel = self.qa_tree.selection()
        if not sel:
            return
        item = sel[0]
        vals = self.qa_tree.item(item,"values")
        base = self.current_base()
        if base and vals:
            p = base/"QA"/vals[0]
            if p.exists():
                if os.name == "nt":
                    os.startfile(str(p))

    def _status_tick(self):
        cpu = os.cpu_count() or 1
        workers = self.cfg.get("parallel_workers", self.cfg.get("ecw_workers", 2))
        worker_txt = f"all ({cpu})" if workers == -1 else str(workers)
        mem = memory_gb()
        extra = f" | RAM used: {mem:.1f} GB" if mem is not None else ""
        self.resource_label.configure(text=f"CPU: {cpu} | Parallel workers: {worker_txt}{extra} | v{APP_VERSION}")
        self._refresh_build_details()
        self.after(1000,self._status_tick)


if __name__ == "__main__":
    app = RetilerApp()
    app.mainloop()
