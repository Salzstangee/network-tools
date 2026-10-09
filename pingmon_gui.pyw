#!/usr/bin/env python3
"""PingMon for Windows - a Win9x-style GUI on top of pingmon.py.

Same engine, same target file (~/.pingmon_targets) as the console version;
only the front end differs. tkinter only; the tray icon additionally needs
pystray + pillow (bundled into PingMon.exe) and is simply left out without them.
"""

import ipaddress
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from tkinter import filedialog, messagebox

import pingmon as core

try:
    import pystray
    from PIL import Image
except Exception:       # not installed, or no usable tray backend (pystray raises ValueError)
    pystray = None

APP = "PingMon"
VERSION = "1.3"
IS_WINDOWS = sys.platform == "win32"
REPO = "https://github.com/Salzstangee/network-tools"
SELF_UPDATE = IS_WINDOWS and getattr(sys, "frozen", False)    # only the exe can swap itself
SETTINGS_FILE = os.path.expanduser("~/.pingmon_settings.json")
INSTANCE_PORT = 47231   # loopback port that keeps PingMon single-instance
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULTS = {"autostart": False, "start_minimized": True, "close_to_tray": False,
            "notify": True, "interval": 1.0, "beep": False, "on_top": False,
            "check_updates": True, "slow_ms": 100}
REFRESH_MS = 250        # UI poll rate
GRAPH_POINTS = 600      # samples kept per host (feeds the graph and the wide loss strip)
GRAPH_WINDOW = 300      # samples the RTT graph spreads across its full width
LOG_LINES = 500         # event log length
DOWN_AFTER = 2          # lost pings in a row before a host is DOWN (beep, toast, red tray icon)

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
YELLOW = "#ffff00"
DIMYELLOW = "#808000"
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
    "lookup": [
        "................",
        "...kkkkk........",
        "..kwwwwwk.......",
        ".kwwbbwwwk......",
        ".kwbwwwwwk......",
        ".kwbwwwwwk......",
        ".kwwwwwwwk......",
        ".kwwwwwwwk......",
        "..kwwwwwk.......",
        "...kkkkkk.......",
        "........kkk.....",
        ".........kkk....",
        "..........kkk...",
        "...........kkk..",
        "............kk..",
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


def fmt_dur(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m"


def dns_name(target):
    """What the DNS column shows: the reverse name of an IP, or the address a hostname has."""
    host, _ = core.split_port(target)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        addrs = sorted(core.lookup(host), key=lambda a: a[0] != socket.AF_INET)
        return addrs[0][1] if addrs else "(no DNS)"
    try:
        return socket.gethostbyaddr(host)[0]
    except (OSError, UnicodeError):
        return ""


def bevel(canvas, x0, y0, x1, y1, sunken=False):
    """Classic two-tone 3D border, the way Win9x draws a button face."""
    tl, br = (SHADOW, HILITE) if sunken else (HILITE, DARK)
    canvas.create_rectangle(x0, y0, x1, y1, fill=FACE, outline="")
    canvas.create_line(x0, y1 - 1, x0, y0, x1 - 1, y0, fill=tl)
    canvas.create_line(x0, y1 - 1, x1 - 1, y1 - 1, x1 - 1, y0, fill=br)
    if not sunken:
        canvas.create_line(x0 + 1, y1 - 2, x1 - 2, y1 - 2, x1 - 2, y0 + 1, fill=SHADOW)


def load_settings():
    try:
        with open(SETTINGS_FILE) as fh:
            return {**DEFAULTS, **json.load(fh)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w") as fh:
            json.dump(settings, fh, indent=2)
    except OSError:
        pass


def launch_command():
    """What the Run key starts: the exe itself, or pythonw + this script from source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}" --autostart'


def autostart_get():
    """Current HKCU Run entry for PingMon, or None (also None off Windows)."""
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP)[0]
    except OSError:
        return None


def autostart_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass


def claim_instance():
    """Listen on a loopback port; if another PingMon holds it, ask that one to show itself.

    Returns the listening socket, or None when an existing PingMon answered.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", INSTANCE_PORT))
        srv.listen(2)
        return srv
    except OSError:
        srv.close()
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
            c.sendall(b"pingmon-show\n")
            if c.recv(16).startswith(b"ok"):
                return None
    except OSError:
        pass
    return False    # port taken by something else: run without the single-instance guard


def version_tuple(tag):
    """'v1.10' -> (1, 10), so 1.10 sorts after 1.9; junk sorts lowest."""
    try:
        return tuple(int(p) for p in tag.strip().lstrip("vV").split("."))
    except ValueError:
        return (0,)


def fetch(url, method="GET", timeout=15):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": f"{APP}/{VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release():
    """Tag of the newest release, read from where /releases/latest redirects (no API rate limit)."""
    with fetch(f"{REPO}/releases/latest", "HEAD") as resp:
        url = resp.geturl()
    if "/releases/tag/" not in url:
        raise OSError("no release published yet")
    return url.rstrip("/").rsplit("/", 1)[1]


def update_script(new_name, name):
    """cmd script that swaps the downloaded exe in once PingMon has exited, then starts it.

    A running exe can't be overwritten, so the move retries until the old process is gone.
    """
    q = lambda s: s.replace("%", "%%")
    lines = ["@echo off", "set n=0", ":retry",
             f'move /y "{q(new_name)}" "{q(name)}" >nul 2>&1 && goto ok',
             "set /a n+=1", "if %n% geq 30 goto fail",
             "ping -n 2 127.0.0.1 >nul",       # 1s sleep; timeout.exe refuses to run without a console
             "goto retry",
             ":ok", f'start "" "{q(name)}" --updated', "goto end",
             ":fail", f'start "" "{q(name)}"',
             ":end", '(goto) 2>nul & del "%~f0"']
    return "\r\n".join(lines) + "\r\n"


def stage_update(new_file):
    folder, name = os.path.split(sys.executable)
    script = os.path.join(tempfile.gettempdir(), "pingmon_update.cmd")
    with open(script, "w", encoding="oem", newline="") as fh:   # cmd reads the OEM codepage
        fh.write(update_script(os.path.basename(new_file), name))
    subprocess.Popen(["cmd", "/c", script], cwd=folder, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x08000000)    # CREATE_NO_WINDOW


def tray_image(alert):
    """The app icon for the tray; the screen turns red while any host is down."""
    pal = dict(PALETTE)
    if alert:
        pal.update(d="#800000", G="#ff4040")
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(ICONS["app"]):
        for x, ch in enumerate(row):
            if ch != ".":
                h = pal[ch]
                img.putpixel((x, y), (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), 255))
    return img.resize((64, 64), Image.NEAREST)


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

    SEG_W, SEG_GAP, BAR_W = 8, 2, 300

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

    def make_bar(self, parent):
        self.bar = tk.Canvas(parent, width=self.BAR_W, height=18, bg=FACE,
                             highlightthickness=0, bd=2, relief="sunken")
        return self.bar

    def draw_bar(self, frac):
        """Segmented navy progress bar, the Win9x file-copy kind."""
        self.bar.delete("all")
        step = self.SEG_W + self.SEG_GAP
        segs = int((self.BAR_W - 2) * frac) // step
        for i in range(segs):
            x = 3 + i * step
            self.bar.create_rectangle(x, 4, x + self.SEG_W - 1, 17, fill=NAVY, outline="")


class SweepDialog(Dialog):
    """Sweeps one or more networks with a segmented Win9x progress bar."""

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
        self.make_bar(body).grid(row=2, column=0, columnspan=2)
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


class UpdateDialog(Dialog):
    """Help > Check for Updates: compare with the latest release, download it, swap the exe."""

    def __init__(self, app):
        super().__init__(app.root, "Check for Updates")
        self.app = app
        self.tag = None
        self.cancel = threading.Event()
        self.results = queue.Queue()     # from the worker threads, handled in poll()

        body = tk.Frame(self, bg=FACE, padx=12, pady=10)
        body.pack()
        tk.Label(body, image=app.big_icon, bg=FACE).grid(row=0, column=0, rowspan=2,
                                                         sticky="n", padx=(0, 12))
        self.title_lbl = tk.Label(body, text="Checking for updates...", bg=FACE,
                                  font=app.ui_bold, anchor="w")
        self.title_lbl.grid(row=0, column=1, sticky="w")
        self.info_lbl = tk.Label(body, text=f"Asking GitHub for the latest release.\n"
                                            f"You are running version {VERSION}.",
                                 bg=FACE, justify="left", anchor="w", wraplength=340)
        self.info_lbl.grid(row=1, column=1, sticky="w", pady=(2, 8))
        body.grid_columnconfigure(1, minsize=340)    # fixed width: the box must not jump per step
        self.make_bar(body).grid(row=2, column=0, columnspan=2)
        self.bar.grid_remove()
        self.btns = tk.Frame(body, bg=FACE)
        self.btns.grid(row=3, column=0, columnspan=2, pady=(12, 0))
        self.buttons(("Cancel", self.close))
        self.bind("<Escape>", lambda e: self.close())
        self.show()
        threading.Thread(target=self.check, daemon=True).start()
        self.poll()

    def buttons(self, *specs):
        for w in self.btns.winfo_children():
            w.destroy()
        for i, (text, cmd) in enumerate(specs):
            tk.Button(self.btns, text=text, width=12, bg=FACE, activebackground=FACE,
                      default="active" if i == 0 else "normal", command=cmd).pack(side="left", padx=4)
        self.bind("<Return>", (lambda e: specs[0][1]()) if specs else "")

    def check(self):
        try:
            self.results.put(("latest", latest_release()))
        except Exception as e:
            self.results.put(("error", "Update check failed", f"GitHub could not be reached:\n{e}"))

    def download(self, dest):
        url = f"{REPO}/releases/download/{self.tag}/PingMon.exe"
        try:
            with fetch(url, timeout=30) as resp, open(dest, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while not self.cancel.is_set():
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    self.results.put(("progress", done, total))
            if self.cancel.is_set():
                raise OSError("cancelled")
            with open(dest, "rb") as fh:
                is_exe = fh.read(2) == b"MZ"
            if (total and done != total) or done < 1_000_000 or not is_exe:
                raise OSError(f"incomplete file ({done} bytes)")
        except Exception as e:
            try:
                os.remove(dest)
            except OSError:
                pass
            self.results.put(("error", "Download failed", str(e)))
            return
        self.results.put(("downloaded", dest))

    def poll(self):
        if not self.winfo_exists():
            return
        while not self.results.empty():
            kind, *args = self.results.get_nowait()
            getattr(self, "on_" + kind)(*args)
        self.after(100, self.poll)

    def on_latest(self, tag):
        self.tag = tag
        if version_tuple(tag) <= version_tuple(VERSION):
            self.title_lbl.config(text=f"{APP} is up to date")
            self.info_lbl.config(text=f"Version {VERSION} is the latest release.")
            self.buttons(("OK", self.close))
            return
        self.title_lbl.config(text=f"Version {tag.lstrip('vV')} is available")
        if SELF_UPDATE:
            self.info_lbl.config(text=f"You are running version {VERSION}.\n\n"
                                      f"Update Now downloads it, replaces\n{sys.executable}\n"
                                      "and restarts PingMon. Targets and settings are kept.")
            self.buttons(("Update Now", self.start_download), ("Release Page", self.open_page),
                         ("Later", self.close))
        else:
            self.info_lbl.config(text=f"You are running version {VERSION} from source.\n"
                                      "Get the new files from the release page or git.")
            self.buttons(("Release Page", self.open_page), ("Close", self.close))

    def start_download(self):
        self.title_lbl.config(text=f"Downloading {APP} {self.tag.lstrip('vV')}")
        self.info_lbl.config(text="Connecting...")
        self.bar.grid()
        self.draw_bar(0)
        self.buttons(("Cancel", self.close))
        threading.Thread(target=self.download, args=(sys.executable + ".new",),
                         daemon=True).start()

    def on_progress(self, done, total):
        mb = 1024 * 1024
        self.info_lbl.config(text=f"{done / mb:.1f} of {total / mb:.1f} MB received"
                             if total else f"{done / mb:.1f} MB received")
        self.draw_bar(done / total if total else 0)

    def on_downloaded(self, path):
        try:
            stage_update(path)
        except (OSError, UnicodeError) as e:
            self.on_error("Update failed", f"Could not install the new version:\n{e}")
            return
        self.title_lbl.config(text=f"Restarting {APP}...")
        self.info_lbl.config(text="The new version starts in a moment.")
        self.buttons()
        self.after(500, self.app.quit)

    def on_error(self, title, text):
        if self.cancel.is_set():
            return
        self.title_lbl.config(text=title)
        self.info_lbl.config(text=text[:300])
        self.bar.grid_remove()
        self.buttons(("Release Page", self.open_page), ("Close", self.close))

    def open_page(self):
        webbrowser.open(f"{REPO}/releases/tag/{self.tag}" if self.tag else f"{REPO}/releases")
        self.close()

    def close(self):
        self.cancel.set()
        super().close()


def describe_ip(addr):
    ip = ipaddress.ip_address(addr)
    for test, what in ((ip.is_loopback, "loopback"), (ip.is_link_local, "link-local"),
                       (ip.is_multicast, "multicast"), (ip.is_private, "private"),
                       (ip.is_global, "public")):
        if test:
            return f"IPv{ip.version}, {what}"
    return f"IPv{ip.version}"


def ttl_guess(ttl):
    """Replies start at TTL 64 (Linux, macOS, most embedded), 128 (Windows) or 255 (routers)."""
    for start, what in ((64, "Linux/Unix/macOS or embedded"), (128, "Windows"),
                        (255, "router or switch")):
        if ttl <= start:
            hops = start - ttl
            return f"probably {what}, {hops} hop{'' if hops == 1 else 's'} away"
    return ""


class HostInfo(tk.Toplevel):
    """Host Info window: DNS both ways, echo reply, what the monitor saw, trace route."""

    def __init__(self, app, target):
        super().__init__(app.root, bg=FACE)
        self.app, self.target = app, target
        self.host, self.port = core.split_port(target)
        self.lines = queue.Queue()      # (segments) from the worker, None when a job is done
        self.cancel = threading.Event()
        self.proc = None
        self.busy = False
        self.sections = 0
        self.title(f"Host Info - {target}")
        self.transient(app.root)    # stays above an always-on-top PingMon, hides with it
        self.geometry("760x500")
        self.minsize(480, 300)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

        head = tk.Frame(self, bg=FACE, padx=10, pady=8)
        head.pack(fill="x")
        tk.Label(head, image=app.icons["lookup"], bg=FACE).pack(side="left", padx=(0, 8))
        tk.Label(head, text=target, bg=FACE, font=app.ui_bold).pack(side="left")

        btns = tk.Frame(self, bg=FACE, padx=10)
        self.status = tk.Label(self, text="", bg=FACE, bd=1, relief="sunken", anchor="w", padx=3)
        self.status.pack(side="bottom", fill="x", padx=2, pady=(0, 2))
        btns.pack(side="bottom", fill="x", pady=8)
        wrap = tk.Frame(self, bd=2, relief="sunken")
        wrap.pack(fill="both", expand=True, padx=10)
        self.text = tk.Text(wrap, bg=SCREEN, fg=GREEN, font=app.mono, bd=0, padx=6, pady=4,
                            highlightthickness=0, wrap="none", cursor="arrow")
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, colour in (("head", AMBER), ("key", DIMGREEN), ("val", GREEN),
                            ("warn", AMBER), ("bad", RED)):
            self.text.tag_configure(tag, foreground=colour)
        self.text.configure(state="disabled")

        def button(text, cmd, side="left"):
            b = tk.Button(btns, text=text, width=12, bg=FACE, activebackground=FACE, command=cmd)
            b.pack(side=side, padx=(0, 6) if side == "left" else (6, 0))
            return b
        self.b_refresh = button("Refresh", self.refresh)
        self.b_trace = button("Trace Route", self.trace)
        self.b_stop = button("Stop", self.stop)
        button("Close", self.close, "right")
        button("Copy All", self.copy, "right")
        self.refresh()
        self.poll()

    # ---- job plumbing ----------------------------------------------------

    def run(self, status, job, *args):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.status.config(text=status)
        for b, on in ((self.b_refresh, False), (self.b_trace, False), (self.b_stop, True)):
            b.config(state="normal" if on else "disabled")
        threading.Thread(target=self.work, args=(job, args), daemon=True).start()

    def work(self, job, args):
        try:
            job(*args)
        except Exception as e:      # a lookup gone wrong must not leave the window stuck
            self.out(f"error: {e}", "bad")
        self.lines.put(None)

    def poll(self):
        if not self.winfo_exists():
            return
        changed = False
        while not self.lines.empty():
            segs = self.lines.get_nowait()
            if segs is None:
                self.busy = False
                self.status.config(text="Ready")
                for b, on in ((self.b_refresh, True), (self.b_trace, True), (self.b_stop, False)):
                    b.config(state="normal" if on else "disabled")
                continue
            if not changed:
                self.text.configure(state="normal")
                changed = True
            for text, tag in segs:
                self.text.insert("end", text, tag)
            self.text.insert("end", "\n")
        if changed:
            self.text.see("end")
            self.text.configure(state="disabled")
        self.after(80, self.poll)

    def out(self, text, tag="val"):
        self.lines.put([(text, tag)])

    def kv(self, key, value, tag="val"):
        self.lines.put([(f"{key} ".ljust(19, "."), "key"), (f" {value}", tag)])

    def section(self, title):
        if self.sections:
            self.lines.put([])      # blank line between sections, not above the first
        self.sections += 1
        self.lines.put([(f"== {title} ".ljust(64, "="), "head")])

    # ---- buttons ---------------------------------------------------------

    def refresh(self):
        self.run("Looking up...", self.general, self.app.host_stats(self.target))

    def trace(self):
        self.run("Tracing the route - Stop cancels", self.trace_route)

    def stop(self):
        self.cancel.set()
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))
        self.status.config(text="Copied to the clipboard.")

    def close(self):
        self.stop()
        self.app.info_windows.pop(self.target, None)
        self.destroy()

    # ---- jobs (worker thread) --------------------------------------------

    def general(self, stats):
        host, port = self.host, self.port
        self.section(f"Host Info  {datetime.now():%Y-%m-%d %H:%M:%S}")
        self.kv("Target", self.target + (f"  (TCP check on port {port})" if port else ""))
        try:
            addrs = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM, 0,
                                           socket.AI_CANONNAME)
            except (OSError, UnicodeError) as e:
                self.kv("Forward lookup", f"failed ({e})", "bad")
                infos = []
            addrs = list(dict.fromkeys(sa[0] for *_, sa in infos))
            if addrs:
                self.kv("Forward lookup", ", ".join(addrs))
            canon = infos[0][3] if infos else ""
            if canon and canon.lower() != host.lower():
                self.kv("Canonical name", canon)
        addrs.sort(key=lambda a: ":" in a)      # IPv4 first
        for a in addrs[:4]:
            self.kv("Address", f"{a}  ({describe_ip(a)})")
        if addrs:
            self.lookup_back(addrs[0])
        ok, ms, ttl = core.echo(host)
        if ok:
            self.kv("Echo reply", core.fmt_rtt(ms) + (f", TTL {ttl}: {ttl_guess(ttl)}" if ttl else ""))
        else:
            self.kv("Echo reply", "no reply", "bad")
        if port:
            ok, ms = core.tcp_ping(host, port)
            self.kv(f"TCP port {port}", f"accepts connections ({core.fmt_rtt(ms)})" if ok
                    else "no connection", "val" if ok else "bad")

        self.section("Monitoring")
        if not stats:
            self.out("not in the target list any more", "warn")
            return
        since = stats["since"]
        state = stats["status"]
        if since:
            state += f" since {since:%H:%M:%S} ({fmt_dur((datetime.now() - since).total_seconds())})"
        self.kv("Status", state, {"DOWN": "bad", "LOST": "warn", "SLOW": "warn"}.get(stats["status"], "val"))
        self.kv("Pings", f"{stats['sent']} sent, {stats['lost']} lost "
                         f"({core.pct(stats['lost'], stats['sent'])})")
        if stats["n"]:
            self.kv("RTT", f"last {core.fmt_rtt(stats['last'])}, min {stats['min']:.1f}, "
                           f"avg {stats['avg']:.1f}, max {stats['max']:.1f} ms")
            if stats["jitter"] is not None:
                self.kv("Jitter", f"{stats['jitter']:.1f} ms (over the last {stats['n']} replies)")
        count, longest, total = stats["outages"]
        self.kv("Outages", f"{count}, longest {fmt_dur(longest)}, total {fmt_dur(total)}"
                if count else "none since PingMon started")

    def lookup_back(self, addr):
        """Reverse DNS, whether that name points back, and the local address used to get there."""
        try:
            name, aliases, _ = socket.gethostbyaddr(addr)
        except (OSError, UnicodeError):
            self.kv("Reverse DNS", "no PTR record", "warn")
        else:
            self.kv("Reverse DNS", name + (f"  (aliases: {', '.join(aliases)})" if aliases else ""))
            back = {a for _, a in core.lookup(name)}
            self.kv("Forward-confirmed", f"yes, {name} points back" if addr in back
                    else "no, that name resolves elsewhere", "val" if addr in back else "warn")
        try:    # connecting a UDP socket sends nothing; it only asks the routing table
            with socket.socket(socket.AF_INET6 if ":" in addr else socket.AF_INET,
                               socket.SOCK_DGRAM) as s:
                s.connect((addr, 9))
                self.kv("Local address", f"{s.getsockname()[0]}  (this PC's side of the route)")
        except OSError:
            self.kv("Local address", "no route to this address", "bad")

    def trace_route(self):
        self.section(f"Trace Route  {datetime.now():%H:%M:%S}")
        extra = {}
        if IS_WINDOWS:
            cmd = ["tracert", "-h", "30", "-w", "1000", self.host]
            extra = {"creationflags": 0x08000000, "encoding": "oem"}
        else:
            tool = shutil.which("traceroute") or shutil.which("tracepath")
            if not tool:
                self.out("neither traceroute nor tracepath is installed", "warn")
                return
            cmd = [tool, self.host]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, errors="replace",
                                         **extra)
        except OSError as e:
            self.out(f"could not start {cmd[0]}: {e}", "bad")
            return
        for line in self.proc.stdout:
            if line.strip():
                self.out(line.rstrip())
        self.proc.wait()
        self.proc = None
        if self.cancel.is_set():
            self.out("stopped", "warn")


class App:
    def __init__(self, root, server=None):
        self.root = root
        self.settings = load_settings()
        self.events = queue.Queue()     # tray and second-instance requests, handled in tick()
        self.tray = None
        self.tray_state = None
        self.told_tray = False
        self.selected = None
        self.samples = {}           # host -> deque of (up, rtt_ms)
        self.last_checked = {}      # host -> datetime of the last sample taken
        self.alarm = {}             # host -> True while DOWN, False once seen up
        self.since = {}             # host -> start of the current UP/DOWN period
        self.outages = {}           # host -> [count, longest_s, total_s]
        self.counts = {}            # host -> samples taken in total, scrolls the graph grid
        self.names = {}             # host -> DNS column text
        self.resolving = set()
        self.resolver = ThreadPoolExecutor(max_workers=8)
        self.info_windows = {}      # host -> open HostInfo window
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

        st = self.settings
        core.INTERVAL = float(st["interval"])
        if IS_WINDOWS:   # the registry is the truth: Task Manager can remove the entry too
            st["autostart"] = autostart_get() is not None
        self.interval = tk.DoubleVar(value=core.INTERVAL)
        self.on_top = tk.BooleanVar(value=st["on_top"])
        self.beep = tk.BooleanVar(value=st["beep"])
        self.v_autostart = tk.BooleanVar(value=st["autostart"])
        self.v_minimized = tk.BooleanVar(value=st["start_minimized"])
        self.v_close_tray = tk.BooleanVar(value=st["close_to_tray"])
        self.v_notify = tk.BooleanVar(value=st["notify"])
        self.v_updates = tk.BooleanVar(value=st["check_updates"])
        self.slow_ms = tk.IntVar(value=st["slow_ms"])
        root.attributes("-topmost", self.on_top.get())

        self.build_menu()
        self.build_toolbar()
        self.build_statusbar()
        self.build_body()
        self.bind_keys()

        core.load()
        threading.Thread(target=core.worker, daemon=True).start()
        self.log(f"{APP} {VERSION} started, {len(core.targets)} target(s) loaded", AMBER)
        self.server = server
        if server:
            threading.Thread(target=self.listen, daemon=True).start()
        self.start_tray()
        if st["autostart"] and autostart_get() != launch_command():
            autostart_set(True)     # exe was moved: point the Run entry at the new path
            self.log("autostart entry updated to this program's location", AMBER)
        if "--autostart" in sys.argv and st["start_minimized"] and self.tray:
            root.withdraw()
        if "--updated" in sys.argv:
            self.log(f"updated to version {VERSION}", AMBER)
        if st["check_updates"]:
            threading.Thread(target=self.quiet_update_check, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
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
        t.add_command(label="Host Info...", underline=0, accelerator="Alt+Enter",
                      command=self.host_info)
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
        sl = tk.Menu(v)
        for ms in (0, 20, 50, 100, 200, 500):
            sl.add_radiobutton(label=f"{ms} ms" if ms else "Off", variable=self.slow_ms,
                               value=ms, command=self.set_slow)
        v.add_cascade(label="Slow Threshold (yellow)", underline=0, menu=sl)
        v.add_separator()
        v.add_checkbutton(label="Always on Top", underline=0, variable=self.on_top,
                          command=self.apply_top)
        v.add_checkbutton(label="Beep on Host Down", underline=0, variable=self.beep,
                          command=self.persist)
        v.add_separator()
        v.add_command(label="Clear Event Log", underline=6, command=self.clear_log)
        m.add_cascade(label="View", underline=0, menu=v)

        st = tk.Menu(m)
        tray = "normal" if pystray else "disabled"
        st.add_checkbutton(label="Start with Windows", underline=0, variable=self.v_autostart,
                           command=self.toggle_autostart,
                           state="normal" if IS_WINDOWS else "disabled")
        st.add_checkbutton(label="Start Minimized to Tray", underline=6,
                           variable=self.v_minimized, command=self.persist, state=tray)
        st.add_separator()
        st.add_checkbutton(label="Close to Tray (keep running)", underline=0,
                           variable=self.v_close_tray, command=self.persist, state=tray)
        st.add_checkbutton(label="Tray Notification on Host Down", underline=0,
                           variable=self.v_notify, command=self.persist, state=tray)
        st.add_separator()
        st.add_checkbutton(label="Check for Updates on Startup", underline=10,
                           variable=self.v_updates, command=self.persist)
        if not pystray:
            st.add_separator()
            st.add_command(label="(tray needs: pip install pystray pillow)", state="disabled")
        m.add_cascade(label="Settings", underline=0, menu=st)

        h = tk.Menu(m)
        h.add_command(label="Check for Updates...", underline=0, command=lambda: UpdateDialog(self))
        h.add_separator()
        h.add_command(label=f"About {APP}...", underline=0, command=lambda: AboutDialog(self))
        m.add_cascade(label="Help", underline=0, menu=h)
        self.root.config(menu=m)

        self.ctx = tk.Menu(self.root)
        self.ctx.add_command(label="Host Info...", command=self.host_info)
        self.ctx.add_separator()
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
        ToolButton(inner, self.icons["lookup"], "Info", self.host_info).pack(side="left")
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
        self.list.bind("<Double-Button-1>", lambda e: self.row_at(e.y) and self.host_info())
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
        pane.add(bottom, minsize=110, height=200, stretch="always")

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
        r.bind("<Alt-Return>", lambda e: self.host_info())

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
        self.persist()

    def set_slow(self):
        ms = self.slow_ms.get()
        self.log(f"slow threshold {'set to %d ms' % ms if ms else 'off'}", AMBER)
        self.persist()

    def host_info(self):
        host = self.selected
        if not host:
            self.flash("Select a target first.")
            return
        win = self.info_windows.get(host)
        if win and win.winfo_exists():
            win.deiconify()
            win.lift()
            return
        self.info_windows[host] = HostInfo(self, host)

    def host_stats(self, host):
        """Snapshot of what the monitor knows about a host, for the Host Info window."""
        with core.lock:
            t = core.targets.get(host)
            t = dict(t) if t else None
        if not t:
            return None
        vals = [v for up, v in self.samples.get(host, ()) if up and v is not None]
        steps = [abs(b - a) for a, b in zip(vals, vals[1:])]
        return {"status": self.status_of(host, t)[0], "since": self.since.get(host),
                "sent": t["sent"], "lost": t["lost"], "last": t["ms"], "n": len(vals),
                "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "avg": sum(vals) / len(vals) if vals else None,
                "jitter": sum(steps) / len(steps) if steps else None,
                "outages": list(self.outages.get(host, [0, 0, 0]))}

    def apply_top(self):
        self.root.attributes("-topmost", self.on_top.get())
        self.persist()

    def toggle_top(self):
        self.on_top.set(not self.on_top.get())
        self.apply_top()
        self.flash("Always on top " + ("ON." if self.on_top.get() else "OFF."))

    def persist(self):
        self.settings.update(interval=core.INTERVAL, on_top=self.on_top.get(),
                             beep=self.beep.get(), autostart=self.v_autostart.get(),
                             start_minimized=self.v_minimized.get(),
                             close_to_tray=self.v_close_tray.get(), notify=self.v_notify.get(),
                             check_updates=self.v_updates.get(), slow_ms=self.slow_ms.get())
        save_settings(self.settings)

    def toggle_autostart(self):
        on = self.v_autostart.get()
        where = sys.executable.lower()
        if on and getattr(sys, "frozen", False) and ("\\downloads\\" in where or "\\temp\\" in where):
            if not messagebox.askyesno(
                    APP, f"PingMon is running from\n{sys.executable}\n\n"
                         "Autostart will point to exactly this file. If you move or delete it "
                         "later, autostart stops working.\n\n"
                         "Better: move PingMon.exe to a fixed folder first "
                         "(e.g. %LOCALAPPDATA%\\PingMon).\n\nEnable autostart anyway?",
                    parent=self.root):
                self.v_autostart.set(False)
                return
        try:
            autostart_set(on)
        except OSError as e:
            messagebox.showerror(APP, f"Could not change autostart:\n{e}", parent=self.root)
            self.v_autostart.set(autostart_get() is not None)
            return
        self.log("start with Windows " + ("enabled" if on else "disabled"), AMBER)
        self.persist()

    def quiet_update_check(self):
        time.sleep(10)      # after a Windows logon the network may not be up yet
        try:
            tag = latest_release()
        except Exception:
            return
        if version_tuple(tag) > version_tuple(VERSION):
            self.events.put(("update", tag))

    # ---- tray / window ---------------------------------------------------

    def start_tray(self):
        if not pystray:
            return
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP}", lambda: self.events.put("show"), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda: self.events.put("quit")))
        try:
            self.tray = pystray.Icon(APP, tray_image(False), APP, menu)
            self.tray.run_detached()
        except Exception as e:      # no tray on this desktop: the window just behaves normally
            self.tray = None
            self.log(f"no system tray available ({e.__class__.__name__})", GREY)

    def notify(self, msg):
        if self.tray and self.v_notify.get() and getattr(self.tray, "HAS_NOTIFICATION", False):
            try:
                self.tray.notify(msg, APP)
            except Exception:
                pass

    def listen(self):
        """Second-instance requests: answer and bring this window up."""
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    if conn.recv(32).startswith(b"pingmon-show"):
                        conn.sendall(b"ok")
                        self.events.put("show")
                except OSError:
                    pass

    def on_close(self):
        if self.tray and self.v_close_tray.get():
            self.root.withdraw()
            if not self.told_tray:
                self.told_tray = True
                if getattr(self.tray, "HAS_NOTIFICATION", False):
                    self.tray.notify(f"{APP} keeps monitoring in the background. "
                                     "Right-click the tray icon to exit.", APP)
        else:
            self.quit()

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)      # Windows won't raise a window otherwise
        self.root.after(200, lambda: self.root.attributes("-topmost", self.on_top.get()))
        self.root.focus_force()

    def import_targets(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import Targets",
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:    # utf-8-sig: Notepad likes to start files with a BOM
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                hosts = [tok for line in fh for tok in line.split("#", 1)[0].split()]
        except OSError as e:
            messagebox.showerror(APP, f"Could not read the file:\n{e}", parent=self.root)
            return
        n = sum(1 for h in hosts if core.add(h))
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
        try:
            with open(path, "w") as fh:
                fh.write("\n".join(hosts) + "\n")
        except OSError as e:
            messagebox.showerror(APP, f"Could not write the file:\n{e}", parent=self.root)
            return
        self.flash(f"Exported {len(hosts)} target(s).")

    def quit(self):
        core.stop.set()
        core.save()
        self.persist()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.server:
            self.server.close()
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
        while not self.events.empty():
            ev = self.events.get_nowait()
            if ev == "quit":
                self.quit()
                return
            if ev == "show":
                self.show_window()
            elif ev[0] == "update":
                self.log(f"version {ev[1].lstrip('vV')} is available - Help > Check for Updates",
                         AMBER)
        with core.lock:
            items = [(h, dict(t, hist=list(t["hist"]))) for h, t in core.targets.items()]
        live = {h for h, _ in items}
        for d in (self.samples, self.last_checked, self.alarm, self.since, self.outages,
                  self.counts, self.names):
            for gone in set(d) - live:
                del d[gone]

        for host, t in items:
            if host not in self.names and host not in self.resolving:
                self.resolving.add(host)
                self.resolver.submit(self.resolve, host)
            if t["checked"] and t["checked"] != self.last_checked.get(host):
                self.last_checked[host] = t["checked"]
                self.counts[host] = self.counts.get(host, 0) + 1
                self.samples.setdefault(host, deque(maxlen=GRAPH_POINTS)).append(
                    (t["up"], t["ms"] if t["up"] else None))
                self.transition(host, t)

        if self.selected not in live:
            self.selected = items[0][0] if items else None
        self.items = items
        if self.root.state() != "withdrawn":    # hidden in the tray: only the status matters
            self.draw_list()
            self.draw_graph()
        self.update_status()
        self.root.after(REFRESH_MS, self.tick)

    def resolve(self, host):
        self.names[host] = dns_name(host)
        self.resolving.discard(host)

    def transition(self, host, t):
        """Event log, toast and beep for a fresh sample. One lost ping is only noted;
        DOWN_AFTER in a row make the host DOWN."""
        streak = 0
        for ok in reversed(t["hist"]):
            if ok:
                break
            streak += 1
        prev = self.alarm.get(host)     # None = not judged yet, True = DOWN, False = up
        if t["up"]:
            if prev:
                secs = (t["since"] - self.since[host]).total_seconds()
                o = self.outages.setdefault(host, [0, 0.0, 0.0])
                o[0] += 1
                o[1] = max(o[1], secs)
                o[2] += secs
                self.log(f"{host:<16} UP    after {fmt_dur(secs)} down (rtt {t['rtt']})", GREEN)
                self.notify(f"{host} is back UP after {fmt_dur(secs)} ({t['rtt']})")
            if prev is not False:
                self.since[host] = t["since"]
            self.alarm[host] = False
        elif streak >= DOWN_AFTER:
            if not prev:
                self.since[host] = t["since"]       # the first ping of this run that got lost
                self.alarm[host] = True
                if prev is None:
                    self.log(f"{host:<16} DOWN  no reply since start", RED)
                else:
                    self.log(f"{host:<16} DOWN  ** {streak} pings lost **", RED)
                    self.notify(f"{host} is DOWN - no reply")
                    if self.beep.get():
                        self.root.bell()
        elif prev is False:
            self.log(f"{host:<16} LOST  {streak} ping{'s' if streak > 1 else ''}, no reply", AMBER)

    def status_of(self, host, t):
        """(label, colour) of a target as the list shows it."""
        if t["up"] is None:
            return "...", GREY
        if self.alarm.get(host):
            return "DOWN", RED
        if not t["up"]:
            return "LOST", AMBER
        slow = self.slow_ms.get()
        if slow and t["ms"] is not None and t["ms"] > slow:
            return "SLOW", YELLOW
        return "UP", GREEN

    def update_status(self):
        items = self.items
        down = sum(1 for h, _ in items if self.alarm.get(h))
        up = sum(1 for h, t in items if t["up"] is not None and not self.alarm.get(h))
        sent = sum(t["sent"] for _, t in items)
        lost = sum(t["lost"] for _, t in items)
        s = self.status
        if not s["msg"].cget("text"):
            s["msg"].config(text="Ready - Target box takes IPs, hostnames, host:port or CIDRs")
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
        if self.tray and (bool(down), title) != self.tray_state:
            self.tray_state = (bool(down), title)
            self.tray.icon = tray_image(bool(down))
            self.tray.title = title

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
        cell = cw - 1
        avail = c.winfo_width()

        hostw = max([len(h) for h in self.rows] + [16])
        cols = [("#", 4 * cw, "e"), ("Host", (hostw + 2) * cw, "w"), ("DNS", 0, "w"),
                ("Status", 9 * cw, "w"), ("RTT", 9 * cw, "e"), ("Loss", 7 * cw, "e"),
                ("Since", 11 * cw, "w")]
        # DNS column: as wide as the longest name, but never squeezing the loss strip below 20
        others = sum(w for _, w, _ in cols) + 4 + 20 * cell + 2 * cw
        names = [self.names.get(h, "") for h in self.rows]
        dnsw = max(8, min(max([len(n) for n in names] + [8]), (avail - others) // cw - 2, 40))
        cols[2] = ("DNS", (dnsw + 2) * cw, "w")
        # the loss strip takes whatever width is left, so a bigger window shows more pings
        fixed = sum(w for _, w, _ in cols) + 4
        slots = max(20, min(GRAPH_POINTS, (avail - fixed - 2 * cw) // cell))
        cols.append((f"Last {slots} pings", slots * cell + 2 * cw, "w"))
        width = max(avail, sum(w for _, w, _ in cols) + 8)

        y = self.header_h
        view_top = c.canvasy(0)
        view_bot = view_top + c.winfo_height()
        for i, (host, t) in enumerate(items):
            top, bot = y + i * self.row_h, y + (i + 1) * self.row_h
            if bot < view_top or top > view_bot:    # only draw what is on screen
                continue
            mid = (top + bot) // 2
            status, fg = self.status_of(host, t)
            if host == self.selected:
                c.create_rectangle(0, top, width, bot - 1, fill=NAVY, outline="")
                c.create_rectangle(1, top, width - 1, bot - 2, outline=AMBER, dash=(1, 1))
            since = self.since.get(host) or t["since"]
            name = names[i] if len(names[i]) <= dnsw else names[i][:dnsw - 2] + ".."
            values = [str(i + 1), host, name, status, t["rtt"], core.pct(t["lost"], t["sent"]),
                      since.strftime("%H:%M:%S") if since else "-"]
            x = 4
            for (col, w, anchor), val in zip(cols, values):
                tx = x + w - cw if anchor == "e" else x
                colour = fg
                if col == "Status":
                    c.create_oval(tx, mid - 4, tx + 8, mid + 4, fill=fg, outline=DARK)
                    if status in ("UP", "SLOW"):
                        c.create_oval(tx + 2, mid - 2, tx + 4, mid, fill="#ffffff", outline="")
                    tx += 12
                elif col == "DNS" and host != self.selected:
                    colour = DIMGREEN if fg == GREEN else fg
                c.create_text(tx, mid, text=val, anchor=anchor, font=f, fill=colour)
                x += w
            # loss strip: one cell per ping, newest on the right
            seen = self.samples.get(host)
            hist = [up for up, _ in seen][-slots:] if seen else t["hist"]
            x += (slots - len(hist)) * cell
            for ok in hist:
                c.create_rectangle(x, top + 3, x + cw - 3, bot - 4,
                                   fill=DIMGREEN if ok else RED, outline="")
                x += cell

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
        # the last GRAPH_WINDOW samples always span the full width, whatever the window size
        dx = (w - 4) / (GRAPH_WINDOW - 1)
        # Task-Manager style: the grid scrolls left with every sample
        shift = int(self.counts.get(host, 0) * dx) % step
        for gx in range(w - shift, -1, -step):
            g.create_line(gx, 0, gx, h, fill=GRID)
        for gy in range(h, -1, -step):
            g.create_line(0, gy, w, gy, fill=GRID)
        if not host:
            return
        pts = list(self.samples.get(host, ()))[-GRAPH_WINDOW:]
        values = [v for up, v in pts if up and v is not None]
        top = max(values + [10.0]) * 1.25
        poly = []
        for i, (up, v) in enumerate(reversed(pts)):
            x = w - 2 - i * dx
            if x < 0:
                break
            if up:
                poly.append((x, h - 2 - (v / top) * (h - 14)))
            else:
                g.create_rectangle(x - max(1, dx / 2), 0, x + max(1, dx / 2), h,
                                   fill=DIMRED, outline="")
                if len(poly) > 1:
                    g.create_line(*[c for p in poly for c in p], fill=GREEN)
                poly = []
        if len(poly) > 1:
            g.create_line(*[c for p in poly for c in p], fill=GREEN)
        elif poly:
            g.create_rectangle(poly[0][0] - 1, poly[0][1] - 1, poly[0][0] + 1,
                               poly[0][1] + 1, fill=GREEN, outline="")
        slow = self.slow_ms.get()
        if slow and slow < top:
            sy = h - 2 - (slow / top) * (h - 14)
            g.create_line(0, sy, w, sy, fill=DIMYELLOW, dash=(4, 3))
            g.create_text(w - 4, sy - 2, anchor="se", fill=DIMYELLOW, font=self.small_mono,
                          text=f"slow {slow} ms")
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
    server = claim_instance()
    if server is None:      # an existing PingMon was asked to show itself
        return
    root = tk.Tk()
    App(root, server or None)
    root.mainloop()
    os._exit(0)     # never linger as an invisible process if the tray thread hangs


if __name__ == "__main__":
    main()
