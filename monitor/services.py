#!/usr/bin/env python3
"""
services.py -- one command that brings this machine's background services up, and one table that
says whether they are up.

    python monitor/services.py            # table: what runs, what does not, why
    python monitor/services.py --start    # start whatever is missing (idempotent), then the table
    python monitor/services.py --restart  # replace running ones (after a pack update)
    python monitor/services.py --stop     # stop them
    python monitor/services.py --json     # the same state, machine readable

Why this file exists. Three processes have to run for the phone, the car and the Home Assistant
panel to reach this machine, and each one was started a different way: the ask worker from a
logon .cmd, the execute worker by hand, the voice gateway from an open terminal. Sitting down at
the computer meant remembering all three, and forgetting one meant silence from exactly the place
the system exists to be reached from -- away from the desk. There is now one answer to "is it
up?" and one answer to "bring it up".

What it does NOT do: it never starts anything the machine has not been configured for. No
MONITOR_URL/MONITOR_TOKEN in ~/.claude/monitor.env means the workers stay down and the table says
so, rather than spawning loops that cannot reach anything.

Truth comes from a probe, not from a note somewhere: a worker is up when its lock has been
touched recently, the gateway is up when its port accepts a connection. A lock file left behind
by a process that died is not evidence of anything, and is reported as "nie dziala".

Standard library only. ASCII only.
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import heartbeat  # noqa: E402
import ask_worker  # noqa: E402
import execute_worker  # noqa: E402

GATEWAY = os.path.join(ROOT, "tools", "voice_gateway.py")
GATEWAY_LOCK = os.path.join(tempfile.gettempdir(), "monitor_voice_gateway.lock")
GATEWAY_PORT = int(os.environ.get("ASK_VOICE_PORT", "8788"))
PROBE_TIMEOUT_S = 1.0

STATE_UP, STATE_DOWN, STATE_OFF = "dziala", "nie dziala", "wylaczone"


# -- the voice gateway -----------------------------------------------------------------------
# It is an HTTP(S) server, not a poll loop, so its lock says nothing about whether it works --
# the port does. A gateway bound to the tailnet address answers on the loopback too only if it
# binds 0.0.0.0, which it deliberately does not; so probe the address it actually binds.

def gateway_bind():
    """The address the gateway binds, asked of the same source the gateway asks."""
    if os.environ.get("ASK_VOICE_BIND"):
        return os.environ["ASK_VOICE_BIND"]
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, timeout=5)
        ip = out.stdout.decode("utf-8", "replace").strip().splitlines()
        if out.returncode == 0 and ip:
            return ip[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


def port_open(host, port, timeout=PROBE_TIMEOUT_S, connect=None):
    """Pure apart from the injected connector: does something accept connections there?"""
    if connect is not None:
        return connect(host, port)
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def spawn_detached(args, cwd=None):
    """Start a process that outlives this one and shows no console window."""
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": cwd or os.path.expanduser("~")}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(args, **kw)


def quiet_interpreter():
    """pythonw.exe where it exists: a logon start must not flash a console window."""
    exe = sys.executable or "python"
    quiet = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return quiet if os.name == "nt" and os.path.exists(quiet) else exe


def write_pid(path, pid):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "at": time.time(), "machine": platform.node()}, f)
    except OSError:
        pass


def read_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f).get("pid") or 0) or None
    except (OSError, ValueError, TypeError):
        return None


def kill_pid(pid, kill=os.kill):
    if not pid or pid == os.getpid():
        return False
    try:
        kill(pid, 15)  # SIGTERM; TerminateProcess on Windows
        return True
    except OSError:
        return False  # already gone


# -- the three services ----------------------------------------------------------------------

def _cfg_missing(cfg):
    return not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN")


def ask_state(cfg, now=None, probe=None):
    if _cfg_missing(cfg):
        return STATE_OFF, "brak MONITOR_URL/MONITOR_TOKEN w ~/.claude/monitor.env"
    poll = ask_worker._float(cfg, "MONITOR_ASK_POLL_S", ask_worker.POLL_S)
    live = ask_worker.lock_is_live(ask_worker.lock_path(),
                                   time.time() if now is None else now, poll)
    return (STATE_UP if live else STATE_DOWN), "pytania z telefonu, auta i panelu (read-only)"


def execute_state(cfg, now=None, probe=None):
    if _cfg_missing(cfg):
        return STATE_OFF, "brak MONITOR_URL/MONITOR_TOKEN w ~/.claude/monitor.env"
    live = execute_worker.another_is_running(time.time() if now is None else now)
    repos = execute_worker._enabled_repos(ask_worker.load_repo_map())
    note = ("zlecenia zmieniajace kod: %s" % ", ".join(repos)) if repos \
        else "zlecenia zmieniajace kod (zadne repo nie ma wlaczonego execute)"
    return (STATE_UP if live else STATE_DOWN), note


def gateway_state(cfg, now=None, probe=None):
    if not os.path.exists(GATEWAY):
        return STATE_OFF, "tego repozytorium nie dotyczy (brak tools/voice_gateway.py)"
    host = gateway_bind()
    up = port_open(host, GATEWAY_PORT, connect=probe)
    return (STATE_UP if up else STATE_DOWN), "bramka glosowa na %s:%d" % (host, GATEWAY_PORT)


def start_ask(cfg):
    return ask_worker.ensure_running(cfg)


def start_execute(cfg):
    return execute_worker.ensure_running(cfg)


def start_gateway(cfg, probe=None):
    state, _note = gateway_state(cfg, probe=probe)
    if state == STATE_OFF:
        return "skipped"
    if state == STATE_UP:
        return "running"
    proc = spawn_detached([quiet_interpreter(), GATEWAY], cwd=ROOT)
    write_pid(GATEWAY_LOCK, proc.pid)
    return "spawned"


def stop_ask(cfg, kill=os.kill):
    stopped = kill_pid(ask_worker.lock_pid(), kill)
    ask_worker.drop_lock()
    return "stopped" if stopped else "not-running"


def stop_execute(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(execute_worker.lock_path()), kill)
    try:
        os.remove(execute_worker.lock_path())
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


def stop_gateway(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(GATEWAY_LOCK), kill)
    try:
        os.remove(GATEWAY_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


SERVICES = [
    ("ask", "worker pytan", ask_state, start_ask, stop_ask),
    ("execute", "worker zlecen", execute_state, start_execute, stop_execute),
    ("voice", "bramka glosowa", gateway_state, start_gateway, stop_gateway),
]


def snapshot(cfg, now=None, probe=None):
    """[{name, title, state, note}] -- what is up on this machine right now."""
    rows = []
    for name, title, state_fn, _start, _stop in SERVICES:
        state, note = state_fn(cfg, now=now, probe=probe)
        rows.append({"name": name, "title": title, "state": state, "note": note})
    return rows


def table(rows, header=None):
    out = [header] if header else []
    for r in rows:
        mark = {STATE_UP: "[+]", STATE_DOWN: "[ ]", STATE_OFF: "[-]"}[r["state"]]
        out.append("  %s %-8s %-15s %-10s %s"
                   % (mark, r["name"], r["title"], r["state"], r["note"]))
    return "\n".join(out)


def start_all(cfg):
    """Start what is missing. Returns {name: outcome}. Idempotent: running services are left alone."""
    return {name: start(cfg) for name, _t, _s, start, _stop in SERVICES}


def stop_all(cfg, kill=os.kill):
    return {name: stop(cfg, kill) for name, _t, _s, _start, stop in SERVICES}


def wait_until_up(cfg, names, deadline_s=6.0, sleep=time.sleep, probe=None):
    """A spawned process needs a moment before its lock or its port exists. Without this the
    table printed right after --start says 'nie dziala' about something that is starting fine."""
    end = time.time() + deadline_s
    while time.time() < end:
        rows = {r["name"]: r["state"] for r in snapshot(cfg, probe=probe)}
        if all(rows.get(n) != STATE_DOWN for n in names):
            return True
        sleep(0.4)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", action="store_true", help="start whatever is missing")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--restart", action="store_true", help="stop, then start (after a pack update)")
    ap.add_argument("--json", action="store_true")
    # A hook runs on every session start and must neither print nor block: it spawns what is
    # missing and returns. Waiting for a lock to appear would put seconds on top of opening a
    # window, for an answer nobody reads.
    ap.add_argument("--quiet", action="store_true", help="start, print nothing, do not wait (hooks)")
    args = ap.parse_args(argv)

    cfg = heartbeat.load_config()

    if args.stop or args.restart:
        stop_all(cfg)
    if args.start or args.restart:
        outcomes = start_all(cfg)
        spawned = [n for n, o in outcomes.items() if o == "spawned"]
        if spawned and not args.quiet:
            wait_until_up(cfg, spawned)

    if args.quiet:
        return 0

    rows = snapshot(cfg)
    if args.json:
        print(json.dumps({"machine": platform.node(), "services": rows}, ensure_ascii=True))
        return 0

    down = [r for r in rows if r["state"] == STATE_DOWN]
    print(table(rows, "uslugi na %s:" % platform.node()))
    if down and not (args.start or args.restart):
        print("\n  nie dziala: %s -- podnies je: python monitor/services.py --start"
              % ", ".join(r["name"] for r in down))
    return 0


if __name__ == "__main__":
    sys.exit(main())
