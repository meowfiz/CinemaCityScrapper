#!/usr/bin/env python3
"""
agent_tick.py -- liveness ticker for background agents (change background-agents, design D4).

    python monitor/agent_tick.py --session <id> --spawn   # return at once, run detached
    python monitor/agent_tick.py --session <id>           # run the loop here (also what --spawn starts)
    python monitor/agent_tick.py --session <id> --once    # one tick, for tests

Started by monitor/heartbeat.py when the background-agent registry stops being empty. The loop is:
sleep, read the registry, stop when it is empty or gone, otherwise send one `tool` event with
`tool_name = "agent-tick"` and the agents still running.

Why a positive signal instead of the server inferring from silence: silence cannot tell "the agent
is working and simply not logging" from "the session died". A tick can -- it comes from the machine
the agent was supposed to run on.

Two tickers for one session are harmless: the second takes the lock file, finds it alive and exits.

Configuration comes from the same ~/.claude/monitor.env as heartbeat.py:
    MONITOR_AGENT_TICK_S      seconds between ticks (default 600)
    MONITOR_AGENT_TICK_MAX_S  hard lifetime, so a forgotten registry cannot leave a process for days
                              (default 21600 = 6 h)

Standard library only. ASCII only. Never raises out of main(): exit code is always 0.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heartbeat  # noqa: E402

TICK_S = 600.0
TICK_MAX_S = 6 * 60 * 60.0
LOCK_STALE_TICKS = 2  # a lock older than this many ticks belonged to a process that died


def lock_path(session_id):
    return os.path.join(tempfile.gettempdir(), "monitor_agents_%s.tick" % (session_id or "nosession"))


def lock_is_live(path, now, tick_s, read=None):
    """True when another ticker holds the lock and has touched it recently enough.
    Pure apart from the injected reader, so the staleness rule is testable."""
    try:
        raw = read(path) if read else open(path, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("pid") == os.getpid():
        return False  # our own lock from a previous run in this process (tests)
    age = now - float(data.get("at") or 0)
    return age < tick_s * LOCK_STALE_TICKS


def take_lock(session_id, now):
    path = lock_path(session_id)
    if lock_is_live(path, now, tick_seconds()):
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": now}, f)
        return True
    except OSError:
        return False


def touch_lock(session_id, now):
    try:
        with open(lock_path(session_id), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": now}, f)
    except OSError:
        pass


def drop_lock(session_id):
    try:
        os.remove(lock_path(session_id))
    except OSError:
        pass


def _float_env(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def tick_seconds(cfg=None):
    return _float_env(cfg if cfg is not None else heartbeat.load_config(), "MONITOR_AGENT_TICK_S", TICK_S)


def should_continue(agents, started, now, max_s):
    """Keep ticking only while something is running and the hard lifetime is not spent."""
    return bool(agents) and (now - started) < max_s


def send_tick(cfg, session_id, agents, cwd):
    url, token = cfg.get("MONITOR_URL"), cfg.get("MONITOR_TOKEN")
    if not url or not token:
        return False
    hook = {"session_id": session_id, "cwd": cwd, "tool_name": "agent-tick"}
    payload = heartbeat.build_event("tool", hook, cfg, agents=agents)
    try:
        heartbeat.post(url, token, payload)
        return True
    except Exception as e:
        heartbeat.log("agent tick post failed: %r" % (e,))
        return False


def run(session_id, cwd, once=False, sleep=time.sleep, now_fn=time.time):
    cfg = heartbeat.load_config()
    tick_s = tick_seconds(cfg)
    max_s = _float_env(cfg, "MONITOR_AGENT_TICK_MAX_S", TICK_MAX_S)
    started = now_fn()
    if not take_lock(session_id, started):
        return 0
    ticks = 0
    try:
        while True:
            if not once:
                sleep(tick_s)
            agents = heartbeat.agents_payload(heartbeat.read_agents(session_id))
            now = now_fn()
            if not should_continue(agents, started, now, max_s):
                return ticks
            touch_lock(session_id, now)
            if send_tick(cfg, session_id, agents, cwd):
                ticks += 1
            if once:
                return ticks
    finally:
        drop_lock(session_id)


def spawn_detached(session_id, cwd):
    args = [sys.executable, os.path.abspath(__file__), "--session", str(session_id), "--cwd", cwd]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": cwd}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--session", required=True)
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--spawn", action="store_true")
    ap.add_argument("--once", action="store_true")
    args, _ = ap.parse_known_args(argv)
    if args.spawn:
        if lock_is_live(lock_path(args.session), time.time(), tick_seconds()):
            return 0  # another ticker already watches this session
        spawn_detached(args.session, args.cwd)
        return 0
    run(args.session, args.cwd, once=args.once)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break a session
        heartbeat.log("agent tick unexpected: %r" % (e,))
    sys.exit(0)
