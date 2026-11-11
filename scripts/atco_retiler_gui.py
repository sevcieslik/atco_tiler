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

APP_NAME = "ATCO Retiler"
APP_VERSION = "1.07"

# Palette
NAV = "#0e1a26"
NAV_2 = "#0b1620"
BG = "#102132"
CARD = "#1b3042"
BORDER = "#3c5769"
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
ROW_BG = "#22384c"


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
            ("Data Sources", "▧"),
            ("Settings", "⚙"),
            ("QA & Reports", "▥"),
            ("About", "ⓘ"),
        ]
        for label, icon in items:
            btn = tk.Button(
                self.sidebar, text=f"  {icon}   {label}", anchor="w",
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
        self.pages["Data Sources"] = self._build_mapping_page()
        self.pages["Settings"] = self._build_settings_page()
        self.pages["QA & Reports"] = self._build_qa_page()
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
        return tk.Button(
            parent, text=text, command=command,
            bg=GREEN if primary else ROW_BG,
            fg="#ffffff" if primary else TEXT,
            activebackground=GREEN_DARK if primary else BLUE_SOFT,
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            bd=0, relief="flat", padx=12, pady=10, cursor="hand2",
        )

    def _build_build_page(self):
        page = tk.Frame(self.content_wrap, bg=BG)
        page.grid_columnconfigure(0, weight=7)
        page.grid_columnconfigure(1, weight=4)
        page.grid_rowconfigure(2, weight=1)
        page.grid_rowconfigure(3, weight=0)

        # Live context tiles.
        metrics = tk.Frame(page, bg=BG)
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(14, 6))
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1, uniform="metric")
        self.summary_values = {}
        summaries = (
            ("project", "ACTIVE PROJECT", "Awaiting input", "Circuit / AOI revision"),
            ("sources", "INPUT DATA", "—", "Ortho TIFFs + LiDAR files"),
            ("products", "SELECTED PRODUCTS", "5", "TIFF · ECW · LAZ · zLAS · Index"),
            ("segments", "SEGMENTS", "—", "Tiles to process"),
        )
        for i, (key, heading, value, hint) in enumerate(summaries):
            outer, inner = self.card(metrics)
            outer.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0 if i == 3 else 5))
            self._dashboard_label(inner, heading, size=9, color=MUTED).pack(
                anchor="w", padx=16, pady=(11, 2))
            label = self._dashboard_label(inner, value, size=18, bold=True)
            label.pack(anchor="w", padx=16)
            self.summary_values[key] = label
            self._dashboard_label(inner, hint, size=9, color=MUTED).pack(
                anchor="w", padx=16, pady=(1, 11))

        # Compact input/output setup shared with the Data Sources page.
        setup_outer, setup = self.card(page, "Project Setup")
        setup_outer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        setup.grid_columnconfigure(1, weight=1)
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
        self._dashboard_button(setup, "Open Output", self.open_output).grid(
            row=1, column=3, sticky="ew", padx=(0, 14), pady=5)
        self.context_label = self._dashboard_label(setup, "Not detected", size=9, color=MUTED)
        self.context_label.grid(row=0, column=3, sticky="e", padx=(6, 14))
        self.mapping_chip_frame = tk.Frame(setup, bg=CARD)
        self.mapping_chip_frame.grid(row=2, column=0, columnspan=4,
                                     sticky="w", padx=16, pady=(4, 10))

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
            cb.pack(side="left", padx=7, pady=10)
            self.product_checks[product] = cb
            name_box = tk.Frame(table, bg=row_bg)
            name_box.grid(row=i, column=1, sticky="nsew", padx=1, pady=1)
            self._dashboard_label(name_box, product, size=10, bold=True,
                                  bg=row_bg).pack(anchor="w", padx=8, pady=(5, 0))
            self._dashboard_label(name_box, descriptions[product], size=9,
                                  color=MUTED, bg=row_bg).pack(anchor="w", padx=8, pady=(0, 5))
            prog_box = tk.Frame(table, bg=row_bg)
            prog_box.grid(row=i, column=2, sticky="nsew", padx=1, pady=1)
            pb = ttk.Progressbar(prog_box, maximum=100, value=0, length=110)
            pb.pack(side="left", fill="x", expand=True, padx=(6, 8), pady=13)
            count = self._dashboard_label(prog_box, "0 / 0", size=9, bg=row_bg,
                                          width=8, anchor="e")
            count.pack(side="right", padx=(0, 5))
            status_box = tk.Frame(table, bg=row_bg)
            status_box.grid(row=i, column=3, sticky="nsew", padx=1, pady=1)
            status = self.badge(status_box, "Not started", "muted")
            status.pack(side="left", padx=7, pady=12)
            self.exec_rows[product] = (pb, count, status)

        # Keep the established build callbacks and selected-product dependencies.
        actions_outer, actions = self.card(page, "Build Controls")
        actions_outer.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=6)
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        self.run_btn = self._dashboard_button(
            actions, "▶  Run Build", lambda: self.start_build(False), primary=True)
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(14, 5), pady=(4, 7))
        self.rerun_btn = self._dashboard_button(
            actions, "↻  Rerun Missing", lambda: self.start_build(True))
        self.rerun_btn.grid(row=0, column=1, sticky="ew", padx=(5, 14), pady=(4, 7))
        self.pause_btn = self._dashboard_button(actions, "⏸  Pause", self.toggle_pause)
        self.pause_btn.configure(state="disabled")
        self.pause_btn.grid(row=1, column=0, sticky="ew", padx=(14, 5), pady=(0, 7))
        self.stop_btn = self._dashboard_button(actions, "■  Stop", self.stop_build)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.grid(row=1, column=1, sticky="ew", padx=(5, 14), pady=(0, 7))
        self.validate_btn = self._dashboard_button(actions, "✓  Validate Setup", self.validate_setup)
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
            ("Segments", "segments"),
            ("Source TIFFs", "source_tiffs"),
            ("Source LiDAR files", "source_lidar"),
            ("Parallel workers", "workers"),
            ("Current stage", "stage"),
            ("Elapsed", "elapsed"),
        )
        for i, (label, key) in enumerate(detail_rows):
            self._dashboard_label(details, label, size=9, color=MUTED).grid(
                row=i, column=0, sticky="w", pady=5)
            value = self._dashboard_label(details, "—", size=9, bold=key in ("mode", "stage"),
                                          anchor="w", justify="left", wraplength=250)
            value.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=5)
            self.detail_values[key] = value

        # Compact by default; details remain available on demand.
        log_outer, log_panel = self.card(page)
        log_outer.grid(row=3, column=0, columnspan=2, sticky="nsew",
                       padx=12, pady=(6, 12))
        log_head = tk.Frame(log_panel, bg=CARD)
        log_head.pack(fill="x", padx=13, pady=(10, 7))
        self._dashboard_label(log_head, "Activity Log", size=11, bold=True).pack(side="left")
        self._dashboard_button(log_head, "Show Details ▼", self.toggle_log_details).pack(side="right")
        self._dashboard_button(log_head, "Open QA Folder", self.open_qa_folder).pack(side="right", padx=(0, 7))
        self._dashboard_button(log_head, "Clear", self.clear_log).pack(side="right", padx=(0, 7))
        self.log_text = tk.Text(log_panel, height=4, bg=LOG_BG, fg=LOG_TEXT,
                                insertbackground=TEXT, relief="flat",
                                font=("Consolas", 9), padx=10, pady=7, wrap="none")
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text.tag_configure("INFO", foreground="#82d7a0")
        self.log_text.tag_configure("WARN", foreground="#ffe29c")
        self.log_text.tag_configure("ERROR", foreground="#ff9292")
        self.log_text.configure(state="disabled")
        self._log_expanded = False
        self._sync_product_selection()
        return page

    def toggle_log_details(self):
        self._log_expanded = not self._log_expanded
        self.log_text.configure(height=12 if self._log_expanded else 4)

    def set_all_products(self, selected):
        if self.running:
            return
        for value in self.product_vars.values():
            value.set(bool(selected))
        self._sync_product_selection()

    def _sync_product_selection(self):
        selected = len(self.selected_products())
        if hasattr(self, "selected_count_label"):
            self.selected_count_label.configure(
                text=f"{selected} of {len(self.product_vars)} products selected")
        if hasattr(self, "summary_values"):
            self.summary_values["products"].configure(text=str(selected))
        if hasattr(self, "run_btn") and not self.running:
            state = "normal" if selected else "disabled"
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

    def refresh_mapping(self):
        self.input_entry.delete(0,"end")
        self.input_entry.insert(0, human_path(self.input_root))
        self.output_entry.delete(0,"end")
        self.output_entry.insert(0, human_path(self.output_root))

        if self.input_root and self.input_root.is_dir():
            self.mapped = core.map_input(self.input_root)
        else:
            self.mapped = {"aoi":None,"orthos":None,"point_cloud":None}

        for w in self.mapping_chip_frame.winfo_children():
            w.destroy()
        for key,label in (("aoi","AOI"),("orthos","Orthos"),("point_cloud","Point Cloud (LiDAR)")):
            ok = self.mapped.get(key) is not None
            self.badge(self.mapping_chip_frame, f"{'✓' if ok else '✕'}  {label}", "ok" if ok else "bad").pack(side="left", padx=(0,8))

        for w in self.mapping_detail.winfo_children():
            w.destroy()
        for key,label in (("aoi","AOI"),("orthos","Orthos"),("point_cloud","Point Cloud")):
            p = self.mapped.get(key)
            row = tk.Frame(self.mapping_detail,bg=CARD); row.pack(fill="x",pady=5)
            self.badge(row, "OK" if p else "MISSING", "ok" if p else "bad").pack(side="left")
            tk.Label(row,text=label,width=16,anchor="w",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold")).pack(side="left",padx=10)
            tk.Label(row,text=str(p or "Not mapped"),anchor="w",bg=CARD,fg=MUTED,font=("Segoe UI",9)).pack(side="left",fill="x",expand=True)

        self.context = None
        self.blocks = []
        try:
            if self.mapped.get("aoi"):
                sys.path.insert(0,str(MODULES))
                import process_orthos as po
                shp = po.find_polygon_shp(self.mapped["aoi"])
                circuit = po.circuit_from_polygon(shp)
                rec = po.load_aoi_revision(circuit, shp)
                _, seg = po.load_segments(circuit, rec["revision_code"])
                self.blocks = [str(v) for v in seg["DeliveryName"].tolist()]
                self.context = (circuit, rec["revision_code"])
                self.context_label.configure(text=f"{circuit} / {rec['revision_code']}")
            else:
                self.context_label.configure(text="Not detected")
        except Exception as exc:
            self.context_label.configure(text="Mapping error")
            self.log(f"Context detection: {exc}", "WARN")

        self._count_source_files()
        if hasattr(self, "summary_values"):
            self.summary_values["project"].configure(
                text=f"{self.context[0]} / {self.context[1]}" if self.context else "Awaiting input")
            self.summary_values["sources"].configure(
                text=f"{self.source_tiff_count} + {self.source_lidar_count}")
            self.summary_values["segments"].configure(
                text=str(len(self.blocks)) if self.blocks else "—")
        self.refresh_product_status()

    def selected_products(self):
        return [k for k,v in self.product_vars.items() if v.get()]

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
        if not self.context or not self.blocks:
            return {}
        circuit, rev = self.context
        base = core.output_base(self.output_root, circuit)
        data = {}
        for prod,folder,ext in (
            ("TIFF","TIFF",".tif"),
            ("ECW","ECW",".ecw"),
            ("LAZ","LAZ",".laz"),
            ("zLAS","zLAS",".zlas"),
        ):
            present = sum(1 for b in self.blocks if self._is_valid_file(base/folder/f"{b}{ext}"))
            data[prod] = (len(self.blocks), present)
        tile_shp = core.tile_index_shp_path(base, circuit)
        shp_ok = self._is_valid_file(tile_shp) and tile_shp.with_suffix('.dbf').is_file() and tile_shp.with_suffix('.shx').is_file()
        data["Tile Index"] = (1, 1 if shp_ok else 0)
        return data

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
        self.refresh_mapping()
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
        for check in self.product_checks.values():
            check.configure(state="disabled")
        self.paused = False
        self.stop_requested = False
        self.suspended_pids = []
        self.build_mode = "Rerun Missing" if rerun else "Full Build"
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
        self.log(f"{'RERUN MISSING' if rerun else 'FULL BUILD'} - {self.context[0]} / {self.context[1]}", "INFO")

        th = threading.Thread(target=self._build_worker, args=(rerun,selected,gm), daemon=True)
        th.start()

    def _build_worker(self, rerun, selected, gm):
        try:
            env = os.environ.copy()
            env["ATCO_PARALLEL_WORKERS"] = str(int(self.cfg.get("parallel_workers", self.cfg.get("ecw_workers", 2))))
            overwrite = [] if rerun else ["--overwrite"]

            # Imagery
            if any(x in selected for x in ("TIFF","ECW")):
                args = [
                    sys.executable, str(MODULES/"process_orthos.py"),
                    "--aoi", str(self.mapped["aoi"]),
                    "--source", str(self.mapped["orthos"]),
                    "--output-root", str(self.output_root),
                    *overwrite,
                ]
                if "ECW" in selected:
                    args += ["--global-mapper-exe", str(gm)]
                else:
                    args += ["--tiff-only"]
                rc = self._run_stream(args, env=env)
                if rc:
                    raise RuntimeError(f"Imagery processor failed with exit code {rc}")

            # LiDAR / Tile Index SHP
            if any(x in selected for x in ("LAZ","zLAS","Tile Index")):
                args = [
                    sys.executable, str(MODULES/"process_lidar.py"),
                    "--aoi", str(self.mapped["aoi"]),
                    "--output-root", str(self.output_root),
                    *overwrite,
                ]
                if any(x in selected for x in ("LAZ","zLAS")):
                    args += ["--source", str(self.mapped["point_cloud"])]
                if "zLAS" in selected:
                    args += ["--zlas","--ezlas-exe",str(EZLAS)]
                if "Tile Index" in selected:
                    args += ["--tile-index"]
                rc = self._run_stream(args, env=env)
                if rc:
                    raise RuntimeError(f"LiDAR processor failed with exit code {rc}")

            if self.stop_requested:
                self.log_queue.put(("STOP", "Build stopped by operator"))
            else:
                self.log_queue.put(("DONE", "Build complete"))
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
        base = self.current_base()
        if not base or not base.exists():
            return
        for p in list(base.rglob("*.tmp")) + list(base.rglob("*.tmp.*")):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
        for folder in (base/"TIFF", base/"ECW", base/"LAZ", base/"zLAS"):
            if folder.is_dir():
                for p in folder.iterdir():
                    try:
                        if p.is_file() and p.stat().st_size == 0:
                            p.unlink()
                    except OSError:
                        pass
        shutil.rmtree(base/"QA"/"LiDAR"/"temp_las_for_zlas", ignore_errors=True)

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
            "muted": ("#eef1f3", MUTED)
        }
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

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0","end")
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self):
        try:
            while True:
                kind,payload = self.log_queue.get_nowait()
                if kind == "LINE":
                    self.log(payload,"INFO")
                    self._parse_progress(payload)
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
        for check in self.product_checks.values():
            check.configure(state="normal")
        self._sync_product_selection()
        self.pause_btn.configure(state="disabled", text="⏸  Pause")
        self.stop_btn.configure(state="disabled")
        self.status_text.configure(text="Stopped" if stopped else ("Ready" if ok else "Build failed"))
        self.status_dot.configure(fg=AMBER if stopped else ("#48bd54" if ok else RED))
        self.refresh_product_status()
        if stopped:
            messagebox.showinfo(APP_NAME, "Build stopped. Completed products were kept; temporary/zero-byte files were cleaned.")
        elif ok:
            messagebox.showinfo(APP_NAME, "Build finished.")
        else:
            messagebox.showerror(APP_NAME, "Build failed. Check the log and QA diagnostics.")

    # ---------- validation/settings/QA ----------
    def validate_setup(self):
        self.log("Validating runtime and sources...", "INFO")
        checks = []
        checks.append(("Central AOI DB",(core.CENTRAL_ROOT/"database"/"atco_aoi_master.sqlite").is_file()))
        checks.append(("Central Segments",(core.CENTRAL_ROOT/"segments"/"circuit").is_dir()))
        checks.append(("Client source GDB",core.CLIENT_GDB.is_dir()))
        checks.append(("Trajectory index",(core.TRAJECTORY_ROOT/"trajectory_index.parquet").is_file()))
        checks.append(("EzLAS.exe",EZLAS.is_file()))
        checks.append(("Global Mapper",core.detect_global_mapper(self.cfg) is not None))
        try:
            import pyogrio
            gdb_write = pyogrio.list_drivers().get("OpenFileGDB") in ("rw", "raw")
        except Exception:
            try:
                import fiona
                gdb_write = fiona.supported_drivers.get("OpenFileGDB") in ("rw", "raw")
            except Exception:
                gdb_write = False
        checks.append(("OpenFileGDB write", gdb_write))
        for key,label in (("aoi","Input/AOI"),("orthos","Input/Orthos"),("point_cloud","Input/Point Cloud")):
            checks.append((label,self.mapped.get(key) is not None))
        bad = []
        for name,ok in checks:
            self.log(f"{'OK' if ok else 'MISSING'}  {name}", "INFO" if ok else "WARN")
            if not ok:
                bad.append(name)
        if bad:
            messagebox.showwarning(APP_NAME, "Validation completed with missing items:\n\n" + "\n".join(bad))
        else:
            messagebox.showinfo(APP_NAME, "Validation passed.")

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
