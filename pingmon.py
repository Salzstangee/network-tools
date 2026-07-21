#!/usr/bin/env python3
"""pingmon - interactive console ping monitor.

Commands (type at the > prompt):
  add <ip|host|cidr> [...]      add targets; a CIDR (10.0.0.0/24) is scanned
                                and only the reachable addresses are added
  del <ip|host|#> [...]         remove targets (by name or list number)
  list                          show status table once
  watch                         live table, refreshes until you press Enter
  interval <sec>                change ping interval (default 5s)
  help                          show this
  quit                          exit
"""

import ipaddress
import os
import platform
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

INTERVAL = 1.0          # seconds between ping rounds
TIMEOUT = 1             # seconds to wait for a reply
SCAN_WORKERS = 64       # parallel pings while sweeping a network
MAX_SCAN_HOSTS = 1024   # refuse to sweep anything bigger (/22)
STATE_FILE = os.path.expanduser("~/.pingmon_targets")

IS_WINDOWS = platform.system().lower() == "windows"

targets = {}            # host -> dict(up=None/True/False, rtt=str, since=datetime, checked=datetime)
lock = threading.Lock()
stop = threading.Event()


def ping(host):
    """Return (ok, rtt_ms_string)."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(TIMEOUT * 1000), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(TIMEOUT), host]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=TIMEOUT + 3)
    except (subprocess.TimeoutExpired, OSError):
        return False, "-"
    if res.returncode != 0:
        return False, "-"
    # parse "time=12.3 ms" / "time<1ms" / German "Zeit=12ms"
    rtt = "-"
    out = res.stdout
    for key in ("time=", "time<", "Zeit=", "Zeit<"):
        if key in out:
            tail = out.split(key, 1)[1]
            num = ""
            for ch in tail:
                if ch.isdigit() or ch == ".":
                    num += ch
                else:
                    break
            if num:
                rtt = f"{float(num):.1f}ms"
            break
    return True, rtt


def check(host):
    ok, rtt = ping(host)
    now = datetime.now()
    with lock:
        t = targets.get(host)
        if t is None:
            return
        if t["up"] is not ok:
            t["since"] = now
        t["up"] = ok
        t["rtt"] = rtt
        t["checked"] = now


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
        targets[host] = {"up": None, "rtt": "-", "since": None, "checked": None}
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


def table():
    with lock:
        items = list(targets.items())
    if not items:
        return "no targets yet — use:  add <ip>\n"

    width = max(len(h) for h, _ in items)
    width = max(width, 6)
    up = sum(1 for _, t in items if t["up"] is True)
    down = sum(1 for _, t in items if t["up"] is False)

    lines = [f"{'#':>3}  {'HOST':<{width}}  {'STATUS':<9} {'RTT':>8}  SINCE",
             "-" * (3 + 2 + width + 2 + 9 + 1 + 8 + 2 + 8)]
    for i, (host, t) in enumerate(items, 1):
        if t["up"] is True:
            status = "\033[32m● UP\033[0m     "
        elif t["up"] is False:
            status = "\033[31m● DOWN\033[0m   "
        else:
            status = "\033[90m● ...\033[0m    "
        since = t["since"].strftime("%H:%M:%S") if t["since"] else "-"
        lines.append(f"{i:>3}  {host:<{width}}  {status} {t['rtt']:>8}  {since}")
    lines.append("")
    lines.append(f"{len(items)} targets — {up} up, {down} down, "
                 f"{len(items) - up - down} unknown   "
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