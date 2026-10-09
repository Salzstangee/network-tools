#!/usr/bin/env python3
"""pingmon - interactive console ping monitor.

Commands (type at the > prompt):
  add <ip|host|cidr> [...]      add targets; a CIDR (10.0.0.0/24) is scanned
                                and only the reachable addresses are added;
                                host:port (10.0.0.1:443) is checked by TCP connect
  del <ip|host|#> [...]         remove targets (by name or list number)
  clear                         remove every target except the first one
  clear all                     remove every target
  list                          show status table once
  watch                         live table, refreshes until you press Enter
  interval <sec>                change ping interval (default 1s)
  help                          show this
  quit                          exit
"""

import ipaddress
import os
import platform
import re
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

INTERVAL = 1.0          # seconds between ping rounds
TIMEOUT = 1             # seconds to wait for a reply
SCAN_WORKERS = 64       # parallel pings while sweeping a network
MAX_SCAN_HOSTS = 1024   # refuse to sweep anything bigger (/22)
HISTORY = 30            # ping results kept per host for the loss graph
STATE_FILE = os.path.expanduser("~/.pingmon_targets")

IS_WINDOWS = platform.system().lower() == "windows"
IS_MAC = platform.system().lower() == "darwin"
PAYLOAD = b"abcdefghijklmnopqrstuvwabcdefghi"     # the 32 bytes Windows ping sends

targets = {}            # host -> dict(up, rtt, ms, since, checked, hist, sent, lost)
lock = threading.Lock()
stop = threading.Event()


# Windows: ICMP straight through iphlpapi instead of one ping.exe process per probe
_icmp = None
if IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes

        class _IpOptions(ctypes.Structure):
            _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte),
                        ("Flags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte),
                        ("OptionsData", ctypes.c_void_p)]

        class _EchoReply(ctypes.Structure):
            _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong),
                        ("RoundTripTime", ctypes.c_ulong), ("DataSize", ctypes.c_ushort),
                        ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                        ("Options", _IpOptions)]

        _icmp = ctypes.WinDLL("iphlpapi.dll")
        _icmp.IcmpCreateFile.restype = wintypes.HANDLE
        _icmp.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p,
                                       wintypes.WORD, ctypes.POINTER(_IpOptions),
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        _icmp.IcmpSendEcho.restype = wintypes.DWORD
        _icmp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    except (ImportError, OSError, AttributeError):
        _icmp = None

_dns = {}               # host -> (expires, [(family, address), ...])
_dns_lock = threading.Lock()


def lookup(host):
    """Addresses of a host as [(family, address)], cached so a dead DNS can't stall every round."""
    now = time.monotonic()
    with _dns_lock:
        hit = _dns.get(host)
    if hit and hit[0] > now:
        return hit[1]
    try:
        infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
        addrs = [(fam, sa[0]) for fam, _, _, _, sa in infos if fam in (socket.AF_INET, socket.AF_INET6)]
    except (OSError, UnicodeError):
        addrs = []
    with _dns_lock:
        _dns[host] = (now + (30 if addrs else 10), addrs)
    return addrs


def split_port(target):
    """'host:443' or '[v6]:443' -> (host, 443); anything else -> (target, None)."""
    if target.startswith("["):
        host, sep, port = target[1:].partition("]:")
    else:
        host, sep, port = target.rpartition(":")
        if ":" in host:         # a bare IPv6 address, not host:port
            return target, None
    if sep and host and port.isdigit() and 0 < int(port) < 65536:
        return host, int(port)
    return target, None


def _icmp_echo(addr, size, df, timeout):
    """One echo through IcmpSendEcho: no process to start, no console text to decode."""
    data = (PAYLOAD * (size // len(PAYLOAD) + 1))[:size]
    opts = _IpOptions(Ttl=128, Flags=2) if df else None       # 2 = IP_FLAG_DF
    buf = ctypes.create_string_buffer(ctypes.sizeof(_EchoReply) + size + 64)
    handle = _icmp.IcmpCreateFile()
    if handle in (None, ctypes.c_void_p(-1).value):
        return False, None, None
    try:
        n = _icmp.IcmpSendEcho(handle, struct.unpack("<L", socket.inet_aton(addr))[0],
                               data, len(data), ctypes.byref(opts) if opts else None,
                               buf, len(buf), int(timeout * 1000))
    finally:
        _icmp.IcmpCloseHandle(handle)
    reply = _EchoReply.from_buffer_copy(buf)
    if not n or reply.Status != 0:      # 0 = IP_SUCCESS; else unreachable, TTL expired, too big
        return False, None, None
    return True, float(reply.RoundTripTime), reply.Options.Ttl


def _ping_exe(host, size, df, timeout):
    """One echo via the system ping binary: Linux/macOS, and IPv6 on Windows."""
    extra = {}
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), "-l", str(size)]
        cmd += ["-f"] if df else []
        extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW: no console flash in the GUI
        extra["encoding"] = "oem"             # ping writes the console codepage (cp850: "ausgeführt")
    elif IS_MAC:                              # macOS takes -W in milliseconds
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), "-s", str(size)]
        cmd += ["-D"] if df else []
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), "-s", str(size)]
        cmd += ["-M", "do"] if df else []
    try:
        # stdin=DEVNULL: a windowed exe has no valid stdin handle to inherit
        res = subprocess.run(cmd + [host], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", timeout=timeout + 3, **extra)
    except (subprocess.TimeoutExpired, OSError):
        return False, None, None
    out = res.stdout
    # parse "time=12.3 ms" / "time<1ms" / German "Zeit=12ms"; Windows exits 0 even on
    # "Destination host unreachable", so only a line with a time or a TTL counts as a reply
    m = re.search(r"(?:time|zeit)\s*([=<])\s*([\d.]+)", out, re.IGNORECASE)
    ttl = re.search(r"ttl[=:]\s*(\d+)", out, re.IGNORECASE)
    if res.returncode != 0 or not (m or ttl):
        return False, None, None
    rtt = (0.0 if m.group(1) == "<" else float(m.group(2))) if m else None
    return True, rtt, ttl and int(ttl.group(1))


def echo(host, size=32, df=False, timeout=TIMEOUT):
    """One ICMP echo: (ok, rtt_ms, reply_ttl). rtt 0.0 means under 1 ms (Windows resolution)."""
    addrs = lookup(host)
    if not addrs:               # unresolvable: don't make ping wait for the same DNS failure
        return False, None, None
    v4 = [a for fam, a in addrs if fam == socket.AF_INET]
    if _icmp and v4:
        try:
            return _icmp_echo(v4[0], size, df, timeout)
        except (OSError, ValueError, ctypes.ArgumentError):
            pass
    return _ping_exe(host, size, df, timeout)


def tcp_ping(host, port, timeout=TIMEOUT):
    """TCP connect time to host:port, for hosts that drop ICMP: (ok, rtt_ms)."""
    for fam, addr in lookup(host)[:2]:
        start = time.perf_counter()
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((addr, port))
        except OSError:
            continue
        return True, (time.perf_counter() - start) * 1000
    return False, None


def probe(target):
    """(ok, rtt_ms) for one target: ICMP echo, or a TCP connect for host:port."""
    host, port = split_port(target)
    if port:
        return tcp_ping(host, port)
    return echo(host)[:2]


def fmt_rtt(ms):
    if ms is None:
        return "-"
    return "<1ms" if ms == 0 else f"{ms:.1f}ms"


def ping(host):
    """Return (ok, rtt_ms_string)."""
    ok, ms = probe(host)
    return ok, fmt_rtt(ms) if ok else "-"


def check(host):
    ok, ms = probe(host)
    now = datetime.now()
    with lock:
        t = targets.get(host)
        if t is None:
            return
        if t["up"] is not ok:
            t["since"] = now
        t["up"] = ok
        t["rtt"] = fmt_rtt(ms) if ok else "-"
        t["ms"] = ms if ok else None
        t["checked"] = now
        t["hist"].append(ok)
        t["sent"] += 1
        if not ok:
            t["lost"] += 1


def worker():
    while not stop.is_set():
        with lock:
            hosts = list(targets)
        if hosts:
            threads = [threading.Thread(target=check, args=(h,), daemon=True)
                       for h in hosts]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        stop.wait(INTERVAL)


def add(host):
    with lock:
        if host in targets:
            return False
        targets[host] = {"up": None, "rtt": "-", "ms": None, "since": None, "checked": None,
                         "hist": deque(maxlen=HISTORY), "sent": 0, "lost": 0}
    threading.Thread(target=check, args=(host,), daemon=True).start()
    return True


def expand(token):
    """Return the addresses of a CIDR token, or None if it isn't one."""
    if "/" not in token:
        return None
    try:
        net = ipaddress.ip_network(token, strict=False)
    except ValueError:
        return None
    # .hosts() drops network/broadcast; it is empty for a single-address /32
    return [str(h) for h in net.hosts()] or [str(net.network_address)]


def scan(hosts):
    """Ping every host in parallel, return the reachable ones, lowest IP first."""
    alive, done, total = [], 0, len(hosts)
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {pool.submit(ping, h): h for h in hosts}
        for fut in as_completed(futures):
            done += 1
            if fut.result()[0]:
                alive.append(futures[fut])
            print(f"\r  scanning {done}/{total} — {len(alive)} up",
                  end="", flush=True)
    print()
    return sorted(alive, key=ipaddress.ip_address)


def add_targets(tokens):
    """Add plain hosts directly; sweep CIDR tokens and add what answers."""
    for token in tokens:
        hosts = expand(token)
        if hosts is None:
            print(f"  added {token}" if add(token) else f"  {token} already listed")
            continue
        if len(hosts) > MAX_SCAN_HOSTS:
            print(f"  {token} covers {len(hosts)} addresses — "
                  f"limit is {MAX_SCAN_HOSTS}")
            continue
        print(f"  sweeping {token} ({len(hosts)} addresses)")
        alive = scan(hosts)
        added = [h for h in alive if add(h)]
        known = len(alive) - len(added)
        print(f"  {len(alive)} reachable, {len(added)} added"
              + (f", {known} already listed" if known else ""))
    save()


def remove(key):
    with lock:
        if key in targets:
            del targets[key]
            return key
        if key.isdigit():
            hosts = list(targets)
            i = int(key) - 1
            if 0 <= i < len(hosts):
                host = hosts[i]
                del targets[host]
                return host
    return None


def clear(keep_first=True):
    """Drop every target, optionally keeping the first one. Returns how many went."""
    with lock:
        hosts = list(targets)
        doomed = hosts[1:] if keep_first else hosts
        for host in doomed:
            del targets[host]
    return len(doomed)


def pct(lost, sent):
    """Loss as a short string; never rounds a real loss down to 0%."""
    if not sent:
        return "-"
    share = 100.0 * lost / sent
    if lost and share < 1:
        return "<1%"
    return f"{share:.0f}%"


def loss_pct(t):
    return pct(t["lost"], t["sent"])


def graph(hist):
    """Right-aligned strip of the last HISTORY pings: green = reply, red = lost."""
    bar = "".join("\033[32m█\033[0m" if ok else "\033[31m█\033[0m" for ok in hist)
    return " " * (HISTORY - len(hist)) + bar


def table():
    with lock:
        items = list(targets.items())
    if not items:
        return "no targets yet — use:  add <ip>\n"

    width = max(len(h) for h, _ in items)
    width = max(width, 6)
    up = sum(1 for _, t in items if t["up"] is True)
    down = sum(1 for _, t in items if t["up"] is False)
    sent = sum(t["sent"] for _, t in items)
    lost = sum(t["lost"] for _, t in items)

    header = (f"{'#':>3}  {'HOST':<{width}}  {'STATUS':<9} {'RTT':>8}  "
              f"{'LOSS':>5}  {'SINCE':<8}  LAST {HISTORY} PINGS")
    lines = [header, "-" * len(header)]
    for i, (host, t) in enumerate(items, 1):
        if t["up"] is True:
            status = "\033[32m● UP\033[0m     "
        elif t["up"] is False:
            status = "\033[31m● DOWN\033[0m   "
        else:
            status = "\033[90m● ...\033[0m    "
        since = t["since"].strftime("%H:%M:%S") if t["since"] else "-"
        lines.append(f"{i:>3}  {host:<{width}}  {status} {t['rtt']:>8}  "
                     f"{loss_pct(t):>5}  {since:<8}  {graph(t['hist'])}")
    lines.append("")
    lines.append(f"{len(items)} targets — {up} up, {down} down, "
                 f"{len(items) - up - down} unknown   "
                 f"{lost}/{sent} pings lost ({pct(lost, sent)})   "
                 f"(interval {INTERVAL:g}s, {datetime.now():%H:%M:%S})")
    return "\n".join(lines) + "\n"


def watch():
    print("live view — press Enter to return to the prompt")
    done = threading.Event()

    def wait_key():
        try:
            sys.stdin.readline()
        except Exception:
            pass
        done.set()

    threading.Thread(target=wait_key, daemon=True).start()
    while not done.is_set():
        print("\033[H\033[J" + table() + "\n(Enter = back)", flush=True)
        done.wait(1.0)


def load():
    try:
        with open(STATE_FILE) as fh:
            for line in fh:
                host = line.strip()
                if host and not host.startswith("#"):
                    add(host)
    except FileNotFoundError:
        pass


def save():
    with lock:
        hosts = list(targets)
    try:
        with open(STATE_FILE, "w") as fh:
            fh.write("\n".join(hosts) + "\n")
    except OSError:
        pass


def main():
    global INTERVAL

    load()
    if sys.argv[1:]:
        add_targets(sys.argv[1:])

    threading.Thread(target=worker, daemon=True).start()

    print(__doc__.split("\n", 2)[2].strip())
    print()

    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            print(table())
            continue

        parts = shlex.split(raw)
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd in ("add", "a"):
            add_targets(args)
        elif cmd in ("del", "rm", "remove", "d"):
            for key in args:
                gone = remove(key)
                print(f"  removed {gone}" if gone else f"  no such target: {key}")
            save()
        elif cmd in ("clear", "c"):
            keep_first = not (args and args[0].lower() == "all")
            gone = clear(keep_first)
            with lock:
                left = list(targets)
            print(f"  removed {gone} target{'s' if gone != 1 else ''}"
                  + (f", kept {left[0]}" if keep_first and left else ""))
            save()
        elif cmd in ("list", "ls", "l"):
            print(table())
        elif cmd in ("watch", "w"):
            watch()
        elif cmd == "interval":
            try:
                INTERVAL = max(1.0, float(args[0]))
                print(f"  interval = {INTERVAL:g}s")
            except (IndexError, ValueError):
                print("  usage: interval <seconds>")
        elif cmd in ("help", "h", "?"):
            print(__doc__.split("\n", 2)[2].strip())
        else:
            print(f"  unknown command: {cmd}  (try 'help')")

    stop.set()
    save()
    print("bye")


if __name__ == "__main__":
    main()