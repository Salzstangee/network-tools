# pingmon

An interactive console ping monitor. Single file, Python 3 standard library only,
no dependencies to install.

You add hosts at a prompt, a background thread pings them continuously, and a
table shows who is up, who is down, the round-trip time, and — the useful bit —
*since when* each host has been in its current state. Targets are remembered
between runs.

```
  #  HOST            STATUS         RTT  SINCE
------------------------------------------------
  1  10.20.30.1      ● UP         0.4ms  09:12:03
  2  10.20.30.14     ● UP         1.1ms  09:12:03
  3  SWVIE0001       ● DOWN           -  09:41:57
  4  8.8.8.8         ● UP        21.0ms  09:12:03

4 targets — 3 up, 1 down, 0 unknown   (interval 1s, 09:44:12)
```

## Requirements

- Python 3.6+ (no third-party packages)
- A working `ping` binary in `PATH` — the system one is called as a subprocess

Runs on Linux, macOS, WSL, and Windows; the ping flags and output parsing are
switched per platform (including German-locale `Zeit=` output).

## Usage

```bash
python3 pingmon.py                          # start with saved targets
python3 pingmon.py 10.20.30.1 8.8.8.8       # start and add these
python3 pingmon.py 10.20.30.0/24            # start and sweep this network
```

Or make it executable and run it directly:

```bash
chmod +x pingmon.py
./pingmon.py
```

## Commands

Typed at the `>` prompt:

| Command | Aliases | What it does |
|---|---|---|
| `add <ip\|host\|cidr> [...]` | `a` | Add targets. CIDRs are swept — see below |
| `del <ip\|host\|#> [...]` | `d`, `rm`, `remove` | Remove targets by name or by table number |
| `list` | `l`, `ls` | Print the status table once |
| `watch` | `w` | Live table, refreshing every second |
| `interval <sec>` | | Change the ping interval (minimum 1s) |
| `help` | `h`, `?` | Show the command list |
| `quit` | `q`, `exit` | Save and exit |

Pressing Enter on an empty prompt prints the table — same as `list`.

In `watch` mode the screen refreshes every second until you press Enter, which
drops you back to the prompt. `Ctrl-C` or `Ctrl-D` at the prompt exits cleanly
and saves.

### Adding hosts

Arguments are space-separated, and you can mix forms freely:

```
> add 10.20.30.1 SWVIE0001 8.8.8.8
  added 10.20.30.1
  added SWVIE0001
  added 8.8.8.8
```

Hostnames work anywhere an IP does. Commas are **not** separators — `add a,b`
is treated as one host named `a,b`. Adding a host that is already listed leaves
the existing entry (and its state history) untouched.

### Sweeping a network

Any argument containing `/` is parsed as a network, pinged in parallel, and
**only the addresses that answer are added**:

```
> add 10.20.30.0/24
  sweeping 10.20.30.0/24 (254 addresses)
  scanning 254/254 — 7 up
  7 reachable, 7 added
```

Details worth knowing:

- The network address and broadcast address are skipped; a `/32` yields its
  single address.
- You do not need to give the network address — `10.20.30.7/24` is accepted and
  normalised to the containing `/24`.
- Discovered hosts are added in numeric IP order, not in the order they replied.
- Anything larger than **1024 addresses** (`/22`) is refused, so a mistyped
  `/16` cannot start a 65k-address sweep:
  `10.0.0.0/16 covers 65534 addresses — limit is 1024`
- The sweep blocks the prompt while it runs. A full `/22` takes roughly 15–20
  seconds; a `/24` is a few seconds.
- **A host that is down during the sweep is never added.** Once a host *is* on
  the list, later outages show up as DOWN as normal — the sweep is discovery,
  not monitoring.
- Discovery is **ICMP-only**, so hosts that filter ping (Windows clients with
  the firewall enabled, for example) will not be found. Network gear is
  generally fine.

### Removing hosts

By name, or by the number in the table:

```
> del 3 SWVIE0001
```

Numbers refer to the table as printed at that moment. Deleting several by
number in one command shifts the remaining numbers as it goes, so for multiple
deletions either delete highest-number-first or just use the names.

## Persistence

Targets are stored one per line in `~/.pingmon_targets`, written on every add,
delete, and exit, and read back at startup. Lines starting with `#` are ignored
on read, which makes bulk-loading easy:

```bash
printf '10.20.30.1\n10.20.30.2\nSWVIE0001\n' >> ~/.pingmon_targets
```

Note that the file is **rewritten** whenever the target list changes, so any
comments or blank lines you put there are dropped the first time you add or
remove a host from inside the app.

Only the host list is persisted — up/down state and the SINCE timestamps start
fresh each run.

## Configuration

Constants at the top of `pingmon.py`:

| Constant | Default | Meaning |
|---|---|---|
| `INTERVAL` | `1.0` | Seconds between ping rounds (also settable at runtime) |
| `TIMEOUT` | `1` | Seconds to wait for a single reply |
| `SCAN_WORKERS` | `64` | Parallel pings during a network sweep |
| `MAX_SCAN_HOSTS` | `1024` | Largest network the sweep will accept |
| `STATE_FILE` | `~/.pingmon_targets` | Where the target list is saved |

If you want to sweep bigger ranges, raising `SCAN_WORKERS` is the better knob —
sweep time scales with `hosts / workers`.

## How it works

- One background worker thread wakes every `INTERVAL` seconds and pings all
  current targets **in parallel** (one thread per target), so the round takes
  about as long as the slowest single ping rather than the sum of them.
- Each result updates a shared dict under a lock. The `SINCE` timestamp is only
  touched when the up/down state actually *changes*, which is what makes it a
  flap/outage marker rather than a "last checked" clock.
- Network sweeps use a bounded `ThreadPoolExecutor` instead of one thread per
  address, so a `/22` does not spawn a thousand threads.
- Status is colour-coded with ANSI escapes: green ● UP, red ● DOWN, grey ● …
  for a target that has not been checked yet.

## Limitations

- ICMP only — no TCP-port or ARP-based discovery, so firewalled hosts look down.
- IPv4-oriented in practice; IPv6 addresses work as plain targets, but sweeping
  an IPv6 prefix is not useful and will hit the size limit immediately.
- No logging or alerting — state lives in memory and is lost on exit.
- On most systems the unprivileged `ping` binary is used, so no raw-socket
  permissions are needed, but very locked-down images may not ship one.