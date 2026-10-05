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

# The dashboard watcher lives in the HA repository (it is the only one with dashboards) and is
# started from here, because this file is the one place that answers "what is running on this
# machine". Absent HA clone = the service simply does not apply, exactly like the gateway in a
# repository that has none.
DASH_REL = os.path.join("project_files", "python", "deploy_dashboard.py")
DASH_LOCK = os.path.join(tempfile.gettempdir(), "monitor_dashboards.lock")
DASH_INTERVAL_S = int(os.environ.get("MONITOR_DASH_INTERVAL_S", "300"))
DASH_TTL_S = DASH_INTERVAL_S * 3   # two missed rounds is noise; three is a dead process

STATE_UP, STATE_DOWN, STATE_OFF = "dziala", "nie dziala", "wylaczone"


def dashboard_script(repo_map=None):
    """Path to the HA repo's deploy script, or None when this machine has no HA clone."""
    mapping = repo_map if repo_map is not None else ask_worker.load_repo_map()
    ha = mapping.get("HA")
    if not ha:
        return None
    path = os.path.join(ha, DASH_REL)
    return path if os.path.exists(path) else None


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


def pid_on_port(port, runner=None):
    """The PID listening on `port`, or None. Injected runner so this is testable.

    Needed because the gateway is the one service that can be running without this module's
    pid file: started by hand from an open terminal, it holds the port and nothing here knows
    its PID. Measured 2026-09-20: a gateway from three days earlier survived `--restart`, which
    reported success -- the port answered, so the state table said "dziala", and the freshly
    deployed code simply never ran.
    """
    run = runner or (lambda args: subprocess.run(args, capture_output=True, timeout=10))
    try:
        if os.name == "nt":
            out = run(["netstat", "-ano", "-p", "TCP"])
        else:
            out = run(["lsof", "-t", "-i", ":%d" % port, "-sTCP:LISTEN"])
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.decode("utf-8", "replace") if isinstance(out.stdout, bytes) else (out.stdout or "")
    return parse_listener_pid(text, port)


def parse_listener_pid(text, port):
    """Pure: the listening PID for `port` in netstat/lsof output, or None.

    Only LISTENING rows count -- an outbound connection to the same port number is not the
    server, and killing it would be killing a client.
    """
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 1 and parts[0].isdigit():      # lsof -t
            return int(parts[0])
        if len(parts) >= 5 and parts[0].upper() == "TCP" and "LISTEN" in parts[3].upper():
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port) and parts[-1].isdigit():
                return int(parts[-1])
    return None


def stop_gateway(cfg, kill=os.kill):
    """Stop whatever is serving, not only what we started.

    The pid file is tried first because it is exact; the port owner is the fallback, and it is
    the case that actually happens after somebody ran the gateway by hand once.
    """
    stopped = kill_pid(read_pid(GATEWAY_LOCK), kill)
    if not stopped:
        stopped = kill_pid(pid_on_port(GATEWAY_PORT), kill)
    try:
        os.remove(GATEWAY_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


def dashboards_state(cfg, now=None, probe=None):
    """Is the dashboard watcher alive? Lock freshness, like the workers -- a loop that sleeps
    five minutes cannot be probed by a port."""
    script = dashboard_script()
    if not script:
        return STATE_OFF, "ta maszyna nie ma klonu HA (nie dotyczy)"
    live = ask_worker.lock_is_live(DASH_LOCK, time.time() if now is None else now, DASH_TTL_S)
    return (STATE_UP if live else STATE_DOWN), \
        "dashboardy HA na zywo po scaleniu (co %d s)" % DASH_INTERVAL_S


def dashboard_python(repo, exists=os.path.exists):
    """The HA venv's pythonw.exe (no console at all), else its python.exe, else ours.

    With python.exe the watcher showed up as a Windows Terminal window titled
    '...\\HA\\.venv\\Scripts\\python.exe' -- and closing that stray-looking window killed the
    service (found dead 2026-10-05). pythonw has no console to show or to close."""
    scripts = os.path.join(repo, ".venv", "Scripts")
    for exe in ("pythonw.exe", "python.exe"):
        cand = os.path.join(scripts, exe)
        if exists(cand):
            return cand
    return quiet_interpreter()


def start_dashboards(cfg):
    script = dashboard_script()
    if not script:
        return "skipped"
    if dashboards_state(cfg)[0] == STATE_UP:
        return "running"
    # The HA repo's own venv: deploy_dashboard.py needs websockets and pyyaml, which the base
    # interpreter here does not have. Same rule as execute_worker._python_for().
    repo = os.path.dirname(os.path.dirname(os.path.dirname(script)))
    python = dashboard_python(repo)
    proc = spawn_detached([python, script, "--watch", str(DASH_INTERVAL_S), "--lock", DASH_LOCK], cwd=repo)
    write_pid(DASH_LOCK, proc.pid)
    return "spawned"


def stop_dashboards(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(DASH_LOCK), kill)
    try:
        os.remove(DASH_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


SERVICES = [
    ("ask", "worker pytan", ask_state, start_ask, stop_ask),
    ("execute", "worker zlecen", execute_state, start_execute, stop_execute),
    ("voice", "bramka glosowa", gateway_state, start_gateway, stop_gateway),
    ("dashboards", "dashboardy HA", dashboards_state, start_dashboards, stop_dashboards),
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
