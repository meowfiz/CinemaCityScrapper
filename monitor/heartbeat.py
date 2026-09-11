"""
heartbeat.py -- Claude Code hook that reports session state to the monitor server.

Called from .claude/settings.json hooks:
    python monitor/heartbeat.py --event session_start|prompt|tool|stop|stop_failure|waiting|session_end
Claude Code passes hook context as JSON on stdin (session_id, cwd, user_input/prompt, prompt_id,
last_assistant_message, notification_type, error_type, ...).

Called by Claude itself (no stdin) to name the running task for the phone (change task-timer-panel):
    python monitor/heartbeat.py --event label --label "Parser tasks.md: kontynuacje"

Configuration (never in the repo): ~/.claude/monitor.env with KEY=VALUE lines
    MONITOR_URL          server base URL; required, otherwise exit silently
    MONITOR_TOKEN        bearer token; required
    MONITOR_MACHINE      machine label; default: hostname
    MONITOR_SEND_PROMPT  1 (default) or 0 to omit prompt_excerpt
    MONITOR_SEND_TOKENS  1 (default) or 0 to skip `rtk gain` counters on stop/session_end
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
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taskparse  # noqa: E402

EVENTS = ("session_start", "prompt", "tool", "stop", "stop_failure", "waiting", "session_end", "label")
NET_TIMEOUT_S = 2.0
TOOL_THROTTLE_S = 30.0
PROMPT_EXCERPT_LEN = 120
RESULT_EXCERPT_LEN = 120
LABEL_LEN = 80
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
    for k in ("MONITOR_URL", "MONITOR_TOKEN", "MONITOR_MACHINE", "MONITOR_SEND_PROMPT", "MONITOR_SEND_TOKENS"):
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


def rtk_tokens(cwd):
    """Cumulative RTK token counters for this project (rtk gain --project --format json).
    Returns dict(commands, input, output, saved) or None when rtk is missing or slow.
    These are tokens of tool output filtered by RTK, a proxy for tool-output volume,
    not the Claude API bill."""
    exe = shutil.which("rtk")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "gain", "--project", "--format", "json"],
            cwd=cwd, capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        summ = json.loads(out.stdout).get("summary") or {}
        return {
            "commands": int(summ.get("total_commands", 0)),
            "input": int(summ.get("total_input", 0)),
            "output": int(summ.get("total_output", 0)),
            "saved": int(summ.get("total_saved", 0)),
        }
    except Exception:
        return None


def first_sentence(text, limit):
    """First sentence of Claude's answer, whitespace collapsed, markdown bullets stripped, cut to limit."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = " ".join(text.split()).lstrip("-*# ")
    for i, ch in enumerate(s):
        if ch in ".!?" and (i + 1 == len(s) or s[i + 1] == " ") and i >= 8:
            s = s[:i + 1]
            break
    return s[:limit] if s else None


SYNTHETIC_PROMPT_MARKS = ("<task-notification>", "<system-reminder>", "[SYSTEM NOTIFICATION", "<<autonomous-loop")


def is_synthetic_prompt(text):
    """Background-task and loop wake-ups reach UserPromptSubmit too; they are activity, not a new task."""
    return isinstance(text, str) and text.lstrip().startswith(SYNTHETIC_PROMPT_MARKS)


def build_event(event, hook, cfg, label=None):
    cwd = hook.get("cwd") or os.getcwd()
    root = repo_root(cwd)
    wire_event = "stop" if event == "stop_failure" else event
    if event == "prompt" and is_synthetic_prompt(hook.get("user_input") or hook.get("prompt")):
        event = wire_event = "tool"
        hook = dict(hook, tool_name="task-notification")
    ev = {
        "repo": repo_name(cwd),
        "machine": cfg.get("MONITOR_MACHINE") or platform.node(),
        "session_id": hook.get("session_id") or ("label" if event == "label" else "unknown"),
        "event": wire_event,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "ahead": commits_ahead(cwd),
    }
    ev.update(taskparse.describe_repo(root))
    if hook.get("prompt_id"):
        ev["prompt_id"] = str(hook["prompt_id"])[:100]
    if event == "prompt" and cfg.get("MONITOR_SEND_PROMPT", "1") != "0":
        # Claude Code >= 2.1 sends the text as user_input; older builds as prompt
        prompt = hook.get("user_input") or hook.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            ev["prompt_excerpt"] = " ".join(prompt.split())[:PROMPT_EXCERPT_LEN]
    if event == "stop":
        if cfg.get("MONITOR_SEND_TOKENS", "1") != "0":  # not on session_end: 1.5 s hook budget there
            tokens = rtk_tokens(cwd)
            if tokens is not None:
                ev["tokens"] = tokens
        if cfg.get("MONITOR_SEND_PROMPT", "1") != "0":
            excerpt = first_sentence(hook.get("last_assistant_message"), RESULT_EXCERPT_LEN)
            if excerpt:
                ev["result_excerpt"] = excerpt
    if event == "stop_failure":
        ev["error_type"] = str(hook.get("error_type") or "unknown")[:60]
    if event == "waiting":
        ev["notification_type"] = str(hook.get("notification_type") or "permission_prompt")[:60]
    if event == "tool" and hook.get("tool_name"):
        ev["tool_name"] = str(hook["tool_name"])[:60]
    if event == "label":
        ev["label"] = " ".join(str(label or "").split())[:LABEL_LEN]
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
    parser.add_argument("--label", default=None)
    args, _ = parser.parse_known_args(argv)

    cfg = load_config()
    url = cfg.get("MONITOR_URL")
    token = cfg.get("MONITOR_TOKEN")
    if not url or not token:
        return 0
    if args.event == "label" and not (args.label or "").strip():
        return 0

    hook = {} if args.event == "label" else read_stdin_json()
    if throttled(args.event, hook.get("session_id")):
        return 0

    payload = build_event(args.event, hook, cfg, label=args.label)
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
