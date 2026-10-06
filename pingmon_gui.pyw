#!/usr/bin/env python3
"""PingMon for Windows - a Win9x-style GUI on top of pingmon.py.

Same engine, same target file (~/.pingmon_targets) as the console version;
only the front end differs. Standard library only (tkinter).
"""

import ipaddress
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from tkinter import filedialog, messagebox

import pingmon as core

APP = "PingMon"
VERSION = "1.0"
REFRESH_MS = 250        # UI poll rate
GRAPH_POINTS = 120      # RTT samples kept per host for the history graph
LOG_LINES = 500         # event log length

# Win9x system palette
FACE = "#c0c0c0"
SHADOW = "#808080"
HILITE = "#ffffff"
DARK = "#000000"
NAVY = "#000080"
# "phosphor" display palette
SCREEN = "#000000"
GREEN = "#00ff00"
DIMGREEN = "#008000"
GRID = "#004000"
RED = "#ff2020"
DIMRED = "#800000"
AMBER = "#ffb000"
GREY = "#808080"

# 16x16 pixel art, one char per pixel: . = transparent
PALETTE = {"k": "#000000", "w": "#ffffff", "g": "#00c000", "G": "#00ff00",
           "d": "#008000", "r": "#e00000", "R": "#ff6060", "s": "#808080",
           "l": "#c0c0c0", "n": "#000080", "b": "#0000ff", "y": "#ffff00"}
ICONS = {
    "app": [
        "................",
        ".kkkkkkkkkkkkkk.",
        ".kssssssssssssk.",
        ".kskkkkkkkkkksk.",
        ".kskddddddddksk.",
        ".kskdddddGddksk.",
        ".kskddddGdGdksk.",
        ".kskGGdGddGGksk.",
        ".kskddGddddddsk.",
        ".kskddddddddksk.",
        ".kskkkkkkkkkksk.",
        ".kssssssssssssk.",
        ".kkkkkkkkkkkkkk.",
        ".....kssssk.....",
        "...kkkkkkkkkk...",
        "................"],
    "add": [
        "................",
        "................",
        "......kkkk......",
        "......kGgk......",
        "......kGgk......",
        "......kGgk......",
        "..kkkkkGgkkkkk..",
        "..kGGGGGgGGGGk..",
        "..kgggggggggdk..",
        "..kkkkkGgkkkkk..",
        "......kGgk......",
        "......kGgk......",
        "......kgdk......",
        "......kkkk......",
        "................",
        "................"],
    "sweep": [
        "................",
        ".....kkkkkk.....",
        "...kkddddddkk...",
        "..kddddGddddk...",
        "..kdddGdddddkk..",
        ".kddddGddddddk..",
        ".kdddddGddddddk.",
        ".kddddddGGGGGGk.",
        ".kddddddddddddk.",
        ".kddyddddddddk..",
        "..kddddddddGdk..",
        "..kdddddddddk...",
        "...kkddddddkk...",
        ".....kkkkkk.....",
        "................",
        "................"],
    "remove": [
        "................",
        "................",
        "..kk........kk..",
        ".kRrk......kRrk.",
        "..kRrk....kRrk..",
        "...kRrk..kRrk...",
        "....kRrkkRrk....",
        ".....kRrRrk.....",
        ".....kRrRrk.....",
        "....kRrkkRrk....",
        "...kRrk..kRrk...",
        "..kRrk....kRrk..",
        ".kRrk......kRrk.",
        "..kk........kk..",
        "................",
        "................"],
    "clear": [
        "................",
        "......kkkk......",
        "..kkkkkllkkkkk..",
        "..kwwwwwwwwwsk..",
        "..kkkkkkkkkkkk..",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kwlslslslk...",
        "...kkkkkkkkkk...",
        "................",
        "................"],
    "top": [
        "................",
        ".......kk.......",
        "......kbbk......",
        ".....kbbbbk.....",
        "....kbbbbbbk....",
        "...kkkkbbkkkk...",
        "......kbbk......",
        "......kbbk......",
        "......kbbk......",
        "......kkkk......",
        "................",
        "..kkkkkkkkkkkk..",
        "..knnnnnnnnnnk..",
        "..kkkkkkkkkkkk..",
        "................",
        "................"],
    "info": [
        "................",
        ".....kkkkkk.....",
        "...kkwwwwwwkk...",
        "..kwwwwbbwwwwk..",
        "..kwwwwbbwwwwk..",
        ".kwwwwwwwwwwwwk.",
        ".kwwwwbbbwwwwwk.",
        ".kwwwwwbbwwwwwk.",
        ".kwwwwwbbwwwwwk.",
        ".kwwwwwbbwwwwwk.",
        "..kwwwwbbwwwwk..",
        "..kwwwbbbbwwwk..",
        "...kkwwwwwwkk...",
        ".....kkkkkk.....",
        "................",
        "................"],
}


def make_icon(name, zoom=1):
    art = ICONS[name]
    img = tk.PhotoImage(width=16, height=16)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            if ch != ".":
                img.put(PALETTE[ch], (x, y))
    return img.zoom(zoom) if zoom > 1 else img


def pick_font(root, candidates, size, weight="normal"):
    families = {f.lower() for f in tkfont.families(root)}
    for fam in candidates:
        if fam.lower() in families:
            return tkfont.Font(root=root, family=fam, size=size, weight=weight)
    return tkfont.Font(root=root, family=candidates[-1], size=size, weight=weight)


def rtt_ms(rtt):
    try:
        return float(rtt.rstrip("ms"))
    except (AttributeError, ValueError):
        return 0.0


def bevel(canvas, x0, y0, x1, y1, sunken=False):
    """Classic two-tone 3D border, the way Win9x draws a button face."""
    tl, br = (SHADOW, HILITE) if sunken else (HILITE, DARK)
    canvas.create_rectangle(x0, y0, x1, y1, fill=FACE, outline="")
    canvas.create_line(x0, y1 - 1, x0, y0, x1 - 1, y0, fill=tl)
    canvas.create_line(x0, y1 - 1, x1 - 1, y1 - 1, x1 - 1, y0, fill=br)
    if not sunken:
        canvas.create_line(x0 + 1, y1 - 2, x1 - 2, y1 - 2, x1 - 2, y0 + 1, fill=SHADOW)


class ToolButton(tk.Label):
    """Flat toolbar button that pops up on hover and sinks on click (IE4 style)."""

    def __init__(self, parent, image, text, command):
        super().__init__(parent, image=image, text=text, compound="top",
                         bg=FACE, relief="flat", bd=1, padx=6, pady=1)
        self.command = command
        self.bind("<Enter>", lambda e: self.config(relief="raised"))
        self.bind("<Leave>", lambda e: self.config(relief="flat"))
        self.bind("<ButtonPress-1>", lambda e: self.config(relief="sunken"))
        self.bind("<ButtonRelease-1>", self._release)

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.config(relief="raised" if inside else "flat")
        if inside:
            self.command()


class Dialog(tk.Toplevel):
    """Modal grey box with the parent's icon."""

    def __init__(self, parent, title):
        super().__init__(parent, bg=FACE)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def show(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_reqwidth()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def close(self):
        self.grab_release()
        self.destroy()


class SweepDialog(Dialog):
    """Sweeps one or more networks with a segmented Win9x progress bar."""

    SEG_W, SEG_GAP, BAR_W = 8, 2, 300

    def __init__(self, app, networks):
        super().__init__(app.root, "Network Sweep")
        self.app = app
        self.queue = list(networks)
        self.cancel = threading.Event()
        self.state = {"done": 0, "total": 1, "alive": [], "finished": False}
        self.found = []

        body = tk.Frame(self, bg=FACE, padx=12, pady=10)
        body.pack()
        tk.Label(body, image=app.icons["sweep"], bg=FACE).grid(row=0, column=0, rowspan=2,
                                                               sticky="n", padx=(0, 10))
        self.title_lbl = tk.Label(body, text="", bg=FACE, font=app.ui_bold, anchor="w")
        self.title_lbl.grid(row=0, column=1, sticky="w")
        self.info_lbl = tk.Label(body, text="", bg=FACE, font=app.ui_font, anchor="w")
        self.info_lbl.grid(row=1, column=1, sticky="w", pady=(2, 8))
        self.bar = tk.Canvas(body, width=self.BAR_W, height=18, bg=FACE,
                             highlightthickness=0, bd=2, relief="sunken")
        self.bar.grid(row=2, column=0, columnspan=2)
        self.btn = tk.Button(body, text="Cancel", width=10, font=app.ui_font,
                             bg=FACE, activebackground=FACE, command=self.on_button)
        self.btn.grid(row=3, column=0, columnspan=2, pady=(12, 0))
        self.bind("<Escape>", lambda e: self.on_button())
        self.show()
        self.next_network()

    def next_network(self):
        if not self.queue or self.cancel.is_set():
            self.finish()
            return
        net = self.queue.pop(0)
        hosts = core.expand(net)
        self.title_lbl.config(text=f"Sweeping {net}")
        self.state = {"done": 0, "total": len(hosts), "alive": [], "finished": False}
        threading.Thread(target=self.run, args=(hosts, self.state), daemon=True).start()
        self.poll()

    def run(self, hosts, st):
        # own loop instead of core.scan(): that one prints to stdout and can't be cancelled
        def probe(h):
            if self.cancel.is_set():
                return h, False
            return h, core.ping(h)[0]

        with ThreadPoolExecutor(max_workers=core.SCAN_WORKERS) as pool:
            for host, ok in pool.map(probe, hosts):
                st["done"] += 1
                if ok:
                    st["alive"].append(host)
        st["finished"] = True

    def poll(self):
        if not self.winfo_exists():
            return
        st = self.state
        self.info_lbl.config(text=f"{st['done']} of {st['total']} addresses probed, "
                                  f"{len(st['alive'])} answering")
        self.draw_bar(st["done"] / max(st["total"], 1))
        if st["finished"]:
            alive = sorted(st["alive"], key=ipaddress.ip_address)
            self.found += [h for h in alive if core.add(h)]
            self.next_network()
        else:
            self.after(100, self.poll)

    def draw_bar(self, frac):
        self.bar.delete("all")
        step = self.SEG_W + self.SEG_GAP
        segs = int((self.BAR_W - 2) * frac) // step
        for i in range(segs):
            x = 3 + i * step
            self.bar.create_rectangle(x, 4, x + self.SEG_W - 1, 17, fill=NAVY, outline="")

    def finish(self):
        core.save()
        for h in self.found:
            self.app.log(f"{h:<16} added by sweep", DIMGREEN)
        verb = "Cancelled" if self.cancel.is_set() else "Sweep complete"
        self.title_lbl.config(text=verb)
        self.info_lbl.config(text=f"{len(self.found)} new target(s) added.")
        self.btn.config(text="OK", command=self.close)
        self.bind("<Return>", lambda e: self.close())
        self.bind("<Escape>", lambda e: self.close())
        self.btn.focus_set()

    def on_button(self):
        self.cancel.set()

    def close(self):
        self.cancel.set()
        super().close()


class AboutDialog(Dialog):
    def __init__(self, app):
        super().__init__(app.root, f"About {APP}")
        body = tk.Frame(self, bg=FACE, padx=14, pady=12)
        body.pack()
        tk.Label(body, image=app.big_icon, bg=FACE).grid(row=0, column=0, rowspan=4,
                                                         sticky="n", padx=(0, 14))
        rows = [(f"{APP} for Windows", app.ui_bold),
                (f"Version {VERSION}", app.ui_font),
                ("Interactive ICMP network monitor", app.ui_font),
                (f"Copyright (C) {datetime.now().year}", app.ui_font)]
        for i, (text, font) in enumerate(rows):
            tk.Label(body, text=text, bg=FACE, font=font, anchor="w").grid(
                row=i, column=1, sticky="w")
        tk.Frame(body, height=2, bd=1, relief="sunken").grid(
            row=4, column=0, columnspan=2, sticky="we", pady=10)
        tk.Label(body, text=f"Target file:  {core.STATE_FILE}", bg=FACE,
                 font=app.ui_font, anchor="w").grid(row=5, column=0, columnspan=2, sticky="w")
        ok = tk.Button(body, text="OK", width=10, font=app.ui_font, bg=FACE,
                       activebackground=FACE, default="active", command=self.close)
        ok.grid(row=6, column=0, columnspan=2, pady=(12, 0))
        self.bind("<Return>", lambda e: self.close())
        self.bind("<Escape>", lambda e: self.close())
        self.show()


class App:
    def __init__(self, root):
        self.root = root
        self.selected = None
        self.samples = {}           # host -> deque of (up, rtt_ms)
        self.last_checked = {}      # host -> datetime of the last sample taken
        self.last_state = {}        # host -> last up/down seen, for the event log
        self.counts = {}            # host -> samples taken in total, scrolls the graph grid
        self.rows = []              # host order as drawn

        root.title(f"{APP} - Network Monitor")
        root.configure(bg=FACE)
        root.geometry("900x620")
        root.minsize(640, 420)
        root.option_add("*Menu.tearOff", 0)

        self.ui_font = pick_font(root, ["MS Sans Serif", "Microsoft Sans Serif",
                                        "Tahoma", "DejaVu Sans"], 8)
        self.ui_bold = pick_font(root, ["MS Sans Serif", "Microsoft Sans Serif",
                                        "Tahoma", "DejaVu Sans"], 8, "bold")
        self.mono = pick_font(root, ["Fixedsys", "Terminal", "Lucida Console",
                                     "Courier New", "DejaVu Sans Mono"], 10)
        self.small_mono = pick_font(root, ["Terminal", "Lucida Console",
                                           "Courier New", "DejaVu Sans Mono"], 8)
        root.option_add("*Font", self.ui_font)

        self.icons = {n: make_icon(n) for n in ICONS}
        self.big_icon = make_icon("app", 2)
        root.iconphoto(True, self.big_icon, self.icons["app"])

        self.interval = tk.DoubleVar(value=core.INTERVAL)
        self.on_top = tk.BooleanVar(value=False)
        self.beep = tk.BooleanVar(value=False)

        self.build_menu()
        self.build_toolbar()
        self.build_statusbar()
        self.build_body()
        self.bind_keys()

        core.load()
        threading.Thread(target=core.worker, daemon=True).start()
        self.log(f"{APP} {VERSION} started, {len(core.targets)} target(s) loaded", AMBER)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()

    # ---- layout --------------------------------------------------------

    def build_menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m)
        f.add_command(label="Import Targets...", underline=0, command=self.import_targets)
        f.add_command(label="Export Targets...", underline=0, command=self.export_targets)
        f.add_separator()
        f.add_command(label="Exit", underline=1, accelerator="Alt+F4", command=self.quit)
        m.add_cascade(label="File", underline=0, menu=f)

        t = tk.Menu(m)
        t.add_command(label="Add Target", underline=0, accelerator="Ctrl+N",
                      command=self.focus_entry)
        t.add_command(label="Sweep Network...", underline=0, accelerator="Ctrl+S",
                      command=self.ask_sweep)
        t.add_separator()
        t.add_command(label="Remove Selected", underline=0, accelerator="Del",
                      command=self.remove_selected)
        t.add_command(label="Copy Address", underline=0, accelerator="Ctrl+C",
                      command=self.copy_selected)
        t.add_separator()
        t.add_command(label="Clear (keep #1)", underline=1, command=lambda: self.clear(True))
        t.add_command(label="Clear All", underline=6, command=lambda: self.clear(False))
        m.add_cascade(label="Targets", underline=0, menu=t)

        v = tk.Menu(m)
        iv = tk.Menu(v)
        for sec in (1, 2, 5, 10, 30):
            iv.add_radiobutton(label=f"{sec} second{'s' if sec > 1 else ''}",
                               variable=self.interval, value=float(sec),
                               command=self.set_interval)
        v.add_cascade(label="Ping Interval", underline=0, menu=iv)
        v.add_separator()
        v.add_checkbutton(label="Always on Top", underline=0, variable=self.on_top,
                          command=lambda: self.root.attributes("-topmost", self.on_top.get()))
        v.add_checkbutton(label="Beep on Host Down", underline=0, variable=self.beep)
        v.add_separator()
        v.add_command(label="Clear Event Log", underline=6, command=self.clear_log)
        m.add_cascade(label="View", underline=0, menu=v)

        h = tk.Menu(m)
        h.add_command(label=f"About {APP}...", underline=0, command=lambda: AboutDialog(self))
        m.add_cascade(label="Help", underline=0, menu=h)
        self.root.config(menu=m)

        self.ctx = tk.Menu(self.root)
        self.ctx.add_command(label="Copy Address", command=self.copy_selected)
        self.ctx.add_command(label="Remove", command=self.remove_selected)

    def build_toolbar(self):
        bar = tk.Frame(self.root, bg=FACE, bd=1, relief="raised")
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=FACE)
        inner.pack(fill="x", padx=2, pady=2)

        tk.Frame(inner, width=3, bd=1, relief="raised", bg=FACE).pack(
            side="left", fill="y", padx=(0, 4))   # Win9x toolbar grip
        for icon, text, cmd in (("add", "Add", self.add_from_entry),
                                ("sweep", "Sweep", self.ask_sweep),
                                ("remove", "Remove", self.remove_selected),
                                ("clear", "Clear", lambda: self.clear(True))):
            ToolButton(inner, self.icons[icon], text, cmd).pack(side="left")
        tk.Frame(inner, width=2, bd=1, relief="sunken").pack(side="left", fill="y", padx=6)
        ToolButton(inner, self.icons["top"], "On Top", self.toggle_top).pack(side="left")
        ToolButton(inner, self.icons["info"], "About",
                   lambda: AboutDialog(self)).pack(side="left")
        tk.Frame(inner, width=2, bd=1, relief="sunken").pack(side="left", fill="y", padx=6)

        tk.Label(inner, text="Target:", bg=FACE, underline=0).pack(side="left")
        self.entry = tk.Entry(inner, width=34, relief="sunken", bd=2, bg="white",
                              highlightthickness=0, font=self.mono)
        self.entry.pack(side="left", padx=(4, 4), ipady=1)
        self.entry.bind("<Return>", lambda e: self.add_from_entry())
        tk.Button(inner, text="Add", width=7, bg=FACE, activebackground=FACE,
                  command=self.add_from_entry).pack(side="left")

    def build_body(self):
        pane = tk.PanedWindow(self.root, orient="vertical", bg=FACE, bd=0,
                              sashwidth=6, sashrelief="raised")
        pane.pack(fill="both", expand=True, padx=4, pady=(4, 2))

        # target list
        top = tk.Frame(pane, bg=FACE, bd=2, relief="sunken")
        self.list = tk.Canvas(top, bg=SCREEN, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(top, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<Button-1>", self.on_click)
        self.list.bind("<Button-3>", self.on_context)
        self.list.bind("<MouseWheel>",
                       lambda e: self.list.yview_scroll(-e.delta // 120, "units"))
        self.list.bind("<Button-4>", lambda e: self.list.yview_scroll(-1, "units"))
        self.list.bind("<Button-5>", lambda e: self.list.yview_scroll(1, "units"))
        pane.add(top, minsize=120, stretch="always")

        # graph + event log side by side, each in a classic group box
        bottom = tk.Frame(pane, bg=FACE)
        gbox = tk.LabelFrame(bottom, text=" RTT History ", bg=FACE, bd=2, relief="groove")
        gbox.pack(side="left", fill="both", expand=True, padx=(0, 3))
        gwrap = tk.Frame(gbox, bd=2, relief="sunken")
        gwrap.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.graph = tk.Canvas(gwrap, bg=SCREEN, highlightthickness=0, height=150)
        self.graph.pack(fill="both", expand=True)
        self.graph_box = gbox

        lbox = tk.LabelFrame(bottom, text=" Event Log ", bg=FACE, bd=2, relief="groove")
        lbox.pack(side="left", fill="both", expand=True, padx=(3, 0))
        lwrap = tk.Frame(lbox, bd=2, relief="sunken")
        lwrap.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.logbox = tk.Text(lwrap, bg=SCREEN, fg=GREEN, font=self.small_mono, bd=0,
                              highlightthickness=0, height=8, width=40, wrap="none",
                              cursor="arrow", state="disabled")
        lsb = tk.Scrollbar(lwrap, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.logbox.pack(side="left", fill="both", expand=True)
        pane.add(bottom, minsize=110, height=200)

    def build_statusbar(self):
        bar = tk.Frame(self.root, bg=FACE)
        bar.pack(side="bottom", fill="x", padx=2, pady=(0, 2))
        self.status = {}
        for key, width in (("msg", 0), ("targets", 11), ("up", 7), ("down", 8),
                           ("loss", 22), ("interval", 12), ("clock", 9)):
            lbl = tk.Label(bar, text="", bg=FACE, bd=1, relief="sunken", anchor="w",
                           padx=3, width=width or None)
            if key == "msg":
                lbl.pack(side="left", fill="x", expand=True, padx=(0, 2))
            else:
                lbl.pack(side="left", padx=(0, 2))
            self.status[key] = lbl
        tk.Label(bar, text="", bg=FACE, width=2).pack(side="left")   # size-grip spot

    def bind_keys(self):
        r = self.root
        r.bind("<Delete>", lambda e: None if e.widget is self.entry else self.remove_selected())
        r.bind("<Control-n>", lambda e: self.focus_entry())
        r.bind("<Control-s>", lambda e: self.ask_sweep())
        r.bind("<Control-c>", lambda e: None if e.widget is self.entry else self.copy_selected())
        r.bind("<Up>", lambda e: self.move_sel(-1))
        r.bind("<Down>", lambda e: self.move_sel(1))

    # ---- actions --------------------------------------------------------

    def focus_entry(self):
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def add_from_entry(self):
        tokens = self.entry.get().split()
        if not tokens:
            self.focus_entry()
            self.flash("Type an address into the Target box first.")
            return
        self.entry.delete(0, "end")
        nets = []
        for tok in tokens:
            hosts = core.expand(tok)
            if hosts is None:
                if core.add(tok):
                    self.log(f"{tok:<16} added", DIMGREEN)
                else:
                    self.flash(f"{tok} is already listed.")
            elif len(hosts) > core.MAX_SCAN_HOSTS:
                messagebox.showerror(APP, f"{tok} covers {len(hosts)} addresses.\n\n"
                                          f"The limit is {core.MAX_SCAN_HOSTS}.", parent=self.root)
            else:
                nets.append(tok)
        core.save()
        if nets:
            SweepDialog(self, nets)

    def ask_sweep(self):
        d = Dialog(self.root, "Sweep Network")
        body = tk.Frame(d, bg=FACE, padx=12, pady=10)
        body.pack()
        tk.Label(body, image=self.icons["sweep"], bg=FACE).grid(row=0, column=0, rowspan=2,
                                                                sticky="n", padx=(0, 10))
        tk.Label(body, text="Network to sweep (CIDR, max /22):", bg=FACE).grid(
            row=0, column=1, sticky="w")
        e = tk.Entry(body, width=26, bd=2, relief="sunken", font=self.mono,
                     highlightthickness=0)
        e.grid(row=1, column=1, sticky="w", pady=(4, 2))
        tk.Label(body, text="Only addresses that answer are added.", bg=FACE,
                 fg=SHADOW).grid(row=2, column=1, sticky="w")
        btns = tk.Frame(body, bg=FACE)
        btns.grid(row=3, column=0, columnspan=2, pady=(12, 0))

        def go(_=None):
            net = e.get().strip()
            hosts = core.expand(net)
            if hosts is None:
                messagebox.showerror(APP, f"'{net}' is not a network.\n\n"
                                          "Example: 10.20.30.0/24", parent=d)
                return
            if len(hosts) > core.MAX_SCAN_HOSTS:
                messagebox.showerror(APP, f"{net} covers {len(hosts)} addresses.\n\n"
                                          f"The limit is {core.MAX_SCAN_HOSTS}.", parent=d)
                return
            d.close()
            SweepDialog(self, [net])

        tk.Button(btns, text="OK", width=10, bg=FACE, activebackground=FACE,
                  default="active", command=go).pack(side="left", padx=4)
        tk.Button(btns, text="Cancel", width=10, bg=FACE, activebackground=FACE,
                  command=d.close).pack(side="left", padx=4)
        d.bind("<Return>", go)
        d.bind("<Escape>", lambda _: d.close())
        d.show()
        e.focus_set()

    def remove_selected(self):
        host = self.selected
        if host and core.remove(host):
            core.save()
            self.log(f"{host:<16} removed", GREY)
            idx = self.rows.index(host) if host in self.rows else 0
            with core.lock:
                left = list(core.targets)
            self.selected = left[min(idx, len(left) - 1)] if left else None

    def copy_selected(self):
        if self.selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.selected)
            self.flash(f"Copied {self.selected} to the clipboard.")

    def clear(self, keep_first):
        with core.lock:
            n = len(core.targets)
        if not n or (keep_first and n == 1):
            return
        what = "every target except #1" if keep_first else "ALL targets"
        if not messagebox.askyesno(APP, f"Remove {what}?", parent=self.root):
            return
        gone = core.clear(keep_first)
        core.save()
        self.log(f"cleared {gone} target(s)", GREY)

    def set_interval(self):
        core.INTERVAL = self.interval.get()
        self.log(f"ping interval set to {core.INTERVAL:g}s", AMBER)

    def toggle_top(self):
        self.on_top.set(not self.on_top.get())
        self.root.attributes("-topmost", self.on_top.get())
        self.flash("Always on top " + ("ON." if self.on_top.get() else "OFF."))

    def import_targets(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import Targets",
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        n = 0
        with open(path) as fh:
            for line in fh:
                host = line.strip()
                if host and not host.startswith("#") and core.add(host):
                    n += 1
        core.save()
        self.log(f"imported {n} target(s) from {os.path.basename(path)}", AMBER)

    def export_targets(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Export Targets",
                                            defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt")])
        if not path:
            return
        with core.lock:
            hosts = list(core.targets)
        with open(path, "w") as fh:
            fh.write("\n".join(hosts) + "\n")
        self.flash(f"Exported {len(hosts)} target(s).")

    def quit(self):
        core.stop.set()
        core.save()
        self.root.destroy()

    # ---- list interaction ----------------------------------------------

    def row_at(self, y):
        i = int((self.list.canvasy(y) - self.header_h) // self.row_h)
        return self.rows[i] if 0 <= i < len(self.rows) and y > self.header_h else None

    def on_click(self, event):
        self.list.focus_set()
        host = self.row_at(event.y)
        if host:
            self.selected = host
            self.draw_list()

    def on_context(self, event):
        host = self.row_at(event.y)
        if host:
            self.selected = host
            self.draw_list()
            self.ctx.tk_popup(event.x_root, event.y_root)

    def move_sel(self, delta):
        if not self.rows or self.root.focus_get() is self.entry:
            return
        i = self.rows.index(self.selected) if self.selected in self.rows else -1
        self.selected = self.rows[max(0, min(len(self.rows) - 1, i + delta))]
        self.draw_list()

    # ---- log / status ---------------------------------------------------

    def log(self, text, colour=GREEN):
        tag = colour
        self.logbox.configure(state="normal")
        self.logbox.tag_configure(tag, foreground=colour)
        self.logbox.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n", tag)
        lines = int(self.logbox.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.logbox.delete("1.0", f"{lines - LOG_LINES}.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def flash(self, msg):
        self.status["msg"].config(text=msg)
        self.root.after(4000, lambda: self.status["msg"].config(text="")
                        if self.status["msg"].cget("text") == msg else None)

    # ---- periodic refresh ----------------------------------------------

    def tick(self):
        with core.lock:
            items = [(h, dict(t, hist=list(t["hist"]))) for h, t in core.targets.items()]
        live = {h for h, _ in items}
        for d in (self.samples, self.last_checked, self.last_state, self.counts):
            for gone in set(d) - live:
                del d[gone]

        for host, t in items:
            if t["checked"] and t["checked"] != self.last_checked.get(host):
                self.last_checked[host] = t["checked"]
                self.counts[host] = self.counts.get(host, 0) + 1
                self.samples.setdefault(host, deque(maxlen=GRAPH_POINTS)).append(
                    (t["up"], rtt_ms(t["rtt"]) if t["up"] else None))
            prev = self.last_state.get(host)
            if t["up"] is not None and t["up"] is not prev:
                if prev is not None:
                    if t["up"]:
                        self.log(f"{host:<16} UP    (rtt {t['rtt']})", GREEN)
                    else:
                        self.log(f"{host:<16} DOWN  ** no reply **", RED)
                        if self.beep.get():
                            self.root.bell()
                elif not t["up"]:
                    self.log(f"{host:<16} DOWN  at first check", RED)
                self.last_state[host] = t["up"]

        if self.selected not in live:
            self.selected = items[0][0] if items else None
        self.items = items
        self.draw_list()
        self.draw_graph()
        self.update_status()
        self.root.after(REFRESH_MS, self.tick)

    def update_status(self):
        items = self.items
        up = sum(1 for _, t in items if t["up"] is True)
        down = sum(1 for _, t in items if t["up"] is False)
        sent = sum(t["sent"] for _, t in items)
        lost = sum(t["lost"] for _, t in items)
        s = self.status
        if not s["msg"].cget("text"):
            s["msg"].config(text="Ready - Target box takes IPs, hostnames or CIDRs")
        s["targets"].config(text=f"{len(items)} targets")
        s["up"].config(text=f"{up} up")
        s["down"].config(text=f"{down} down", fg="#a00000" if down else "black")
        s["loss"].config(text=f"Lost {lost}/{sent} ({core.pct(lost, sent)})")
        s["interval"].config(text=f"Interval {core.INTERVAL:g}s")
        s["clock"].config(text=datetime.now().strftime("%H:%M:%S"))
        title = f"{APP} - {up}/{len(items)} up"
        if down:
            title += f" - {down} DOWN"
        self.root.title(title)

    def draw_list(self):
        c = self.list
        c.delete("all")
        f = self.mono
        cw = f.measure("0")
        lh = f.metrics("linespace")
        self.row_h = lh + 4
        self.header_h = self.ui_font.metrics("linespace") + 6
        items = getattr(self, "items", [])
        self.rows = [h for h, _ in items]

        hostw = max([len(h) for h in self.rows] + [16])
        cols = [("#", 4 * cw, "e"), ("Host", (hostw + 2) * cw, "w"),
                ("Status", 9 * cw, "w"), ("RTT", 9 * cw, "e"), ("Loss", 7 * cw, "e"),
                ("Since", 11 * cw, "w"), (f"Last {core.HISTORY} pings",
                                          core.HISTORY * (cw - 1) + 2 * cw, "w")]
        width = max(c.winfo_width(), sum(w for _, w, _ in cols) + 8)

        y = self.header_h
        for i, (host, t) in enumerate(items):
            top, bot = y + i * self.row_h, y + (i + 1) * self.row_h
            mid = (top + bot) // 2
            sel = host == self.selected
            if t["up"] is True:
                fg, dot, status = GREEN, GREEN, "UP"
            elif t["up"] is False:
                fg, dot, status = RED, RED, "DOWN"
            else:
                fg, dot, status = GREY, GREY, "..."
            if sel:
                c.create_rectangle(0, top, width, bot - 1, fill=NAVY, outline="")
                c.create_rectangle(1, top, width - 1, bot - 2, outline=AMBER, dash=(1, 1))
            since = t["since"].strftime("%H:%M:%S") if t["since"] else "-"
            values = [str(i + 1), host, status, t["rtt"], core.pct(t["lost"], t["sent"]), since]
            x = 4
            for (name, w, anchor), val in zip(cols, values):
                tx = x + w - cw if anchor == "e" else x
                if name == "Status":
                    c.create_oval(tx, mid - 4, tx + 8, mid + 4, fill=dot, outline=DARK)
                    if t["up"] is True:
                        c.create_oval(tx + 2, mid - 2, tx + 4, mid, fill="#c0ffc0", outline="")
                    tx += 12
                c.create_text(tx, mid, text=val, anchor=anchor, font=f, fill=fg)
                x += w
            # loss strip: one cell per ping, newest on the right
            hist = t["hist"]
            x += (core.HISTORY - len(hist)) * (cw - 1)
            for ok in hist:
                c.create_rectangle(x, top + 3, x + cw - 3, bot - 4,
                                   fill=DIMGREEN if ok else RED, outline="")
                x += cw - 1

        if not items:
            c.create_text(20, self.header_h + 20, anchor="w", font=f, fill=DIMGREEN,
                          text="No targets.  Type an address into the Target box and press Enter.")

        # header drawn last so it stays on top; pinned to the visible top edge
        oy = c.canvasy(0)
        x = 0
        for i, (name, w, anchor) in enumerate(cols):
            ww = w + 4 if i == 0 else w
            if i == len(cols) - 1:
                ww = width - x
            bevel(c, x, oy, x + ww, oy + self.header_h)
            tx = x + ww - 6 if anchor == "e" else x + 6
            c.create_text(tx, oy + self.header_h // 2, text=name, anchor=anchor,
                          font=self.ui_font, fill=DARK)
            x += ww
        c.configure(scrollregion=(0, 0, width, y + len(items) * self.row_h + 4))

    def draw_graph(self):
        g = self.graph
        g.delete("all")
        w, h = g.winfo_width(), g.winfo_height()
        if w < 20 or h < 20:
            return
        host = self.selected
        self.graph_box.config(text=f" RTT History - {host} " if host else " RTT History ")
        step = 12
        # Task-Manager style: the grid scrolls left with every sample
        shift = (self.counts.get(host, 0) * 4) % step
        for gx in range(w - shift, -1, -step):
            g.create_line(gx, 0, gx, h, fill=GRID)
        for gy in range(h, -1, -step):
            g.create_line(0, gy, w, gy, fill=GRID)
        if not host:
            return
        pts = list(self.samples.get(host, ()))
        values = [v for up, v in pts if up and v is not None]
        top = max(values + [10.0]) * 1.25
        dx = 4
        poly = []
        for i, (up, v) in enumerate(reversed(pts)):
            x = w - 2 - i * dx
            if x < 0:
                break
            if up:
                poly.append((x, h - 2 - (v / top) * (h - 14)))
            else:
                g.create_rectangle(x - 1, 0, x + 1, h, fill=DIMRED, outline="")
                if len(poly) > 1:
                    g.create_line(*[c for p in poly for c in p], fill=GREEN)
                poly = []
        if len(poly) > 1:
            g.create_line(*[c for p in poly for c in p], fill=GREEN)
        elif poly:
            g.create_rectangle(poly[0][0] - 1, poly[0][1] - 1, poly[0][0] + 1,
                               poly[0][1] + 1, fill=GREEN, outline="")
        g.create_text(4, 3, anchor="nw", fill=GREEN, font=self.small_mono,
                      text=f"{top:.0f} ms")
        g.create_text(4, h - 3, anchor="sw", fill=GREEN, font=self.small_mono, text="0")
        if values:
            g.create_text(w - 4, 3, anchor="ne", fill=GREEN, font=self.small_mono,
                          text=f"min {min(values):.1f}  avg {sum(values) / len(values):.1f}"
                               f"  max {max(values):.1f} ms")


def main():
    if sys.platform == "win32":
        try:   # crisp fonts on high-DPI screens instead of blurry bitmap scaling
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
