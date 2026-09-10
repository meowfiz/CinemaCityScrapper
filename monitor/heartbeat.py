"""
heartbeat.py -- Claude Code hook that reports session state to the monitor server.

Called from .claude/settings.json hooks:
    python monitor/heartbeat.py --event session_start|prompt|tool|stop|session_end
Claude Code passes hook context as JSON on stdin (session_id, cwd, prompt, ...).

Configuration (never in the repo): ~/.claude/monitor.env with KEY=VALUE lines
    MONITOR_URL          server base URL; required, otherwise exit silently
    MONITOR_TOKEN        bearer token; required
    MONITOR_MACHINE      machine label; default: hostname
    MONITOR_SEND_PROMPT  1 (default) or 0 to omit prompt_excerpt
Environment variables of the same names override the file.
MONITOR_ENV_FILE overrides the location of the env file (tests).

Contract: this script must never slow down or break a Claude Code session.
Network timeout 2 s, every error swallowed and logged to
<tempdir>/monitor_heartbeat.log, exit code always 0, nothing on stdout.

Standard library only. ASCII only.
"""

import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taskparse  # noqa: E402

EVENTS = ("session_start", "prompt", "tool", "stop", "session_end")
NET_TIMEOUT_S = 2.0
TOOL_THROTTLE_S = 30.0
PROMPT_EXCERPT_LEN = 120
LOG_NAME = "monitor_heartbeat.log"


def log(msg):
    try:
        path = os.path.join(tempfile.gettempdir(), LOG_NAME)
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


def load_config():
    cfg = {}
    env_file = os.environ.get("MONITOR_ENV_FILE") or os.path.join(
        os.path.expanduser("~"), ".claude", "monitor.env"
    )
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ("MONITOR_URL", "MONITOR_TOKEN", "MONITOR_MACHINE", "MONITOR_SEND_PROMPT"):
        if os.environ.get(k) is not None:
            cfg[k] = os.environ[k]
    return cfg


def read_stdin_json():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception as e:
        log("stdin parse failed: %r" % (e,))
        return {}


def git(args, cwd):
    try:
        out = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=3
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def repo_name(cwd):
    url = git(["config", "--get", "remote.origin.url"], cwd)
    if url:
        name = url.replace("\\", "/").rstrip("/").split("/")[-1].split(":")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    top = git(["rev-parse", "--show-toplevel"], cwd)
    return os.path.basename(top or cwd)


def repo_root(cwd):
    return git(["rev-parse", "--show-toplevel"], cwd) or cwd


def commits_ahead(cwd):
    out = git(["rev-list", "--count", "@{u}..HEAD"], cwd)
    try:
        return int(out) if out is not None else None
    except ValueError:
        return None


def throttled(event, session_id):
    """True if a 'tool' event for this session was sent less than TOOL_THROTTLE_S ago."""
    if event != "tool":
        return False
    stamp = os.path.join(tempfile.gettempdir(), "monitor_hb_%s.stamp" % (session_id or "nosession"))
    now = time.time()
    try:
        if now - os.path.getmtime(stamp) < TOOL_THROTTLE_S:
            return True
    except OSError:
        pass
    try:
        with open(stamp, "w") as f:
            f.write(str(now))
    except OSError:
        pass
    return False


def build_event(event, hook, cfg):
    cwd = hook.get("cwd") or os.getcwd()
    root = repo_root(cwd)
    ev = {
        "repo": repo_name(cwd),
        "machine": cfg.get("MONITOR_MACHINE") or platform.node(),
        "session_id": hook.get("session_id") or "unknown",
        "event": event,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "ahead": commits_ahead(cwd),
    }
    ev.update(taskparse.describe_repo(root))
    if event == "prompt" and cfg.get("MONITOR_SEND_PROMPT", "1") != "0":
        prompt = hook.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            ev["prompt_excerpt"] = " ".join(prompt.split())[:PROMPT_EXCERPT_LEN]
    if event == "tool" and hook.get("tool_name"):
        ev["tool_name"] = str(hook["tool_name"])[:60]
    return ev


def post(url, token, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/events",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "User-Agent": "monitor-heartbeat/1",
        },
    )
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT_S) as resp:
        return resp.status


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--event", required=True, choices=EVENTS)
    args, _ = parser.parse_known_args(argv)

    cfg = load_config()
    url = cfg.get("MONITOR_URL")
    token = cfg.get("MONITOR_TOKEN")
    if not url or not token:
        return 0

    hook = read_stdin_json()
    if throttled(args.event, hook.get("session_id")):
        return 0

    payload = build_event(args.event, hook, cfg)
    try:
        status = post(url, token, payload)
        if status >= 300:
            log("server returned %s for %s" % (status, args.event))
    except Exception as e:
        log("post failed (%s): %r" % (args.event, e))
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the session
        log("unexpected: %r" % (e,))
    sys.exit(0)
