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
    MONITOR_AUTOSYNC_MIN_S  task length in seconds after which `stop` hands a gated commit + push
                         to monitor/auto_sync.py (rule 2.9); default 600, 0 disables
Environment variables of the same names override the file.
MONITOR_ENV_FILE overrides the location of the env file (tests).

The task clock and rule 2.9 work without MONITOR_URL: they are about git, not about the dashboard.

Contract: this script must never slow down or break a Claude Code session.
Network timeout 2 s, every error swallowed and logged to
<tempdir>/monitor_heartbeat.log, exit code always 0, nothing on stdout.

Standard library only. ASCII only.
"""

import argparse
import datetime
import json
import os
import re
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
CONTINUE_WINDOW_S = 120.0  # same window the server uses to treat a prompt as a continuation
AUTOSYNC_MIN_S = 600.0     # rule 2.9: a task this long ends with a gated commit + push
AGENT_LABEL_LEN = 60       # Claude Code already cuts a description to 3-5 words; this is a guard
AGENTS_MAX = 10            # how many background agents one event carries


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
    for k in ("MONITOR_URL", "MONITOR_TOKEN", "MONITOR_MACHINE", "MONITOR_SEND_PROMPT",
              "MONITOR_SEND_TOKENS", "MONITOR_AUTOSYNC_MIN_S"):
        if os.environ.get(k) is not None:
            cfg[k] = os.environ[k]
    return cfg


def autosync_min_s(cfg):
    """Seconds after which a finished task triggers the gated commit + push; 0 disables it."""
    try:
        return float(cfg.get("MONITOR_AUTOSYNC_MIN_S", AUTOSYNC_MIN_S))
    except (TypeError, ValueError):
        return AUTOSYNC_MIN_S


def read_stdin_json():
    """Claude Code always writes the hook payload as UTF-8. Read bytes and decode UTF-8
    explicitly: sys.stdin uses the console code page (cp1250 on a Polish Windows), which turned
    every 'a with ogonek' into two mojibake characters on the way to the phone (2026-09-15)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        stream = getattr(sys.stdin, "buffer", None)
        raw = stream.read().decode("utf-8", "replace") if stream is not None else sys.stdin.read()
        raw = raw.lstrip("\ufeff")  # some shells prepend a BOM
        return json.loads(raw) if raw.strip() else {}
    except Exception as e:
        log("stdin parse failed: %r" % (e,))
        return {}


def git(args, cwd):
    try:
        out = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3
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


# -- task clock: how long the current task has been running (rule 2.9) ---------------------------
# The server measures this too, but the hook must decide on its own machine and without a network
# round trip. The rules are the server's (tasks.py): a prompt arriving less than CONTINUE_WINDOW_S
# after the last activity was typed mid-turn and continues the task instead of starting a new one.

def next_stamp(stamp, event, now, continue_window_s=CONTINUE_WINDOW_S):
    """Pure decision: (new stamp or None, finished task duration in seconds or None).

    stamp is {"start": epoch, "last": epoch} or None. A returned stamp is to be stored, None means
    'forget the task'. The duration is returned only for the event that ends the task."""
    if event == "prompt":
        if stamp and now - stamp.get("last", 0) < continue_window_s:
            return {"start": stamp["start"], "last": now}, None  # mid-turn prompt: same task
        return {"start": now, "last": now}, None
    if stamp is None:
        return None, None
    if event in ("tool", "waiting", "label"):
        return {"start": stamp["start"], "last": now}, None
    if event in ("stop", "stop_failure", "session_end"):
        return None, max(0.0, now - stamp["start"])
    return stamp, None


def should_autosync(duration_s, min_s):
    """True when a task ran long enough that the user is probably not at the keyboard (rule 2.9)."""
    return bool(min_s) and min_s > 0 and duration_s is not None and duration_s >= min_s


def task_stamp_path(session_id):
    return os.path.join(tempfile.gettempdir(), "monitor_task_%s.json" % (session_id or "nosession"))


def track_task(event, session_id, now=None):
    """Read the stamp, apply next_stamp, write it back. Returns the finished duration or None.
    Every failure is swallowed: the task clock must never break a session."""
    path = task_stamp_path(session_id)
    now = time.time() if now is None else now
    stamp = None
    try:
        with open(path, encoding="utf-8") as f:
            stamp = json.load(f)
        if not isinstance(stamp, dict) or "start" not in stamp:
            stamp = None
    except (OSError, ValueError):
        pass
    try:
        new, duration = next_stamp(stamp, event, now)
        if new is None:
            if os.path.exists(path):
                os.remove(path)
        elif new != stamp:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(new, f)
        return duration
    except Exception as e:
        log("task clock failed: %r" % (e,))
        return None


def spawn_auto_sync(root):
    """Hand the gated commit + push to monitor/auto_sync.py (ZASADY 2.8 gates G1-G5) and return
    at once -- the Stop hook has a budget of a few seconds."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_sync.py")
    if not os.path.exists(script):
        return False
    try:
        subprocess.run([sys.executable, script, "--root", root, "--spawn"],
                       cwd=root, capture_output=True, timeout=5)
        return True
    except Exception as e:
        log("auto-sync spawn failed: %r" % (e,))
        return False


# -- background agents: what is running when the shell shows nothing (change background-agents) ---
# Measured on this Claude Code build (2026-09-15): PostToolUse carries tool_name, tool_input and
# tool_use_id, and the matching task-notification carries the same tool-use-id -- so a background
# agent can be tracked by identity, start to finish, without parsing the transcript.

def agent_from_tool(tool_name, tool_input, tool_use_id, now):
    """A registry entry for a tool call that starts background work, else None."""
    if not tool_use_id:
        return None
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Agent":
        kind, fallback = "agent", "agent w tle"
    elif tool_name in ("Bash", "PowerShell") and ti.get("run_in_background"):
        kind, fallback = "bash", "komenda w tle"
    else:
        return None
    label = " ".join(str(ti.get("description") or "").split())[:AGENT_LABEL_LEN]
    return {"id": str(tool_use_id)[:100], "kind": kind, "label": label or fallback, "started": now}


TOOL_USE_ID_RE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")


def finished_ids(text):
    """Every tool-use-id named by a task-notification (one can close several background tasks)."""
    return TOOL_USE_ID_RE.findall(text) if isinstance(text, str) else []


def next_agents(state, event, hook, now):
    """Pure: the registry after this event. Keyed by tool_use_id, so a lost start means one
    invisible agent and a lost finish means one entry that hangs -- which is exactly what the
    silence threshold on the server is there to report."""
    out = dict(state or {})
    if event == "session_end":
        return {}
    if event == "tool":
        entry = agent_from_tool(hook.get("tool_name"), hook.get("tool_input"), hook.get("tool_use_id"), now)
        if entry is not None:
            out[entry["id"]] = entry
    elif event == "prompt":
        text = hook.get("user_input") or hook.get("prompt")
        if is_synthetic_prompt(text):  # a task-notification reaches UserPromptSubmit
            for tid in finished_ids(text):
                out.pop(tid, None)
    return out


def agents_payload(state):
    """The wire form: oldest first, capped, timestamps as ISO like every other field."""
    out = []
    for entry in sorted(state.values(), key=lambda e: e.get("started") or 0)[:AGENTS_MAX]:
        started = entry.get("started")
        out.append({
            "id": entry.get("id"),
            "label": entry.get("label") or "",
            "kind": entry.get("kind") or "agent",
            "started_ts": datetime.datetime.fromtimestamp(
                started or 0, datetime.timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def agents_path(session_id):
    return os.path.join(tempfile.gettempdir(), "monitor_agents_%s.json" % (session_id or "nosession"))


def read_agents(session_id):
    try:
        with open(agents_path(session_id), encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def track_agents(event, hook, session_id, now=None):
    """Apply one event to the registry on disk. Returns (wire list, changed, was_empty).
    Every failure is swallowed: the registry must never break a session."""
    now = time.time() if now is None else now
    state = read_agents(session_id)
    try:
        new = next_agents(state, event, hook, now)
    except Exception as e:
        log("agent registry failed: %r" % (e,))
        return [], False, True
    if new != state:
        path = agents_path(session_id)
        try:
            if new:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(new, f)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as e:
            log("agent registry write failed: %r" % (e,))
    return agents_payload(new), new != state, not state


def spawn_agent_tick(session_id, cwd):
    """Start the detached liveness ticker. Starting it twice is harmless: the child takes a lock
    file and exits at once when another ticker already holds it."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_tick.py")
    if not os.path.exists(script):
        return False
    try:
        subprocess.run([sys.executable, script, "--session", str(session_id or "nosession"),
                        "--cwd", cwd, "--spawn"], cwd=cwd, capture_output=True, timeout=5)
        return True
    except Exception as e:
        log("agent tick spawn failed: %r" % (e,))
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
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
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


def build_event(event, hook, cfg, label=None, agents=None):
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
    if agents:  # what is running while the shell shows nothing (change background-agents)
        ev["agents"] = agents
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
    if args.event == "label" and not (args.label or "").strip():
        return 0

    hook = {} if args.event == "label" else read_stdin_json()

    # Rule 2.9: a task longer than MONITOR_AUTOSYNC_MIN_S ends with a gated commit + push. Done
    # before the monitor check on purpose -- this is about git, not about the dashboard.
    duration = track_task(args.event, hook.get("session_id"))
    if args.event in ("stop", "stop_failure") and should_autosync(duration, autosync_min_s(cfg)):
        if spawn_auto_sync(repo_root(hook.get("cwd") or os.getcwd())):
            log("auto-sync spawned after a %.0f s task" % duration)

    # Background agents: track them here too, so the registry survives a machine with no monitor.
    agents, agents_changed, was_empty = track_agents(args.event, hook, hook.get("session_id"))
    if agents and (was_empty or agents_changed):
        spawn_agent_tick(hook.get("session_id"), hook.get("cwd") or os.getcwd())

    if not url or not token:
        return 0
    # a start or a finish of a background agent is the only moment its name is known: never throttle it
    if agents_changed is False and throttled(args.event, hook.get("session_id")):
        return 0

    payload = build_event(args.event, hook, cfg, label=args.label, agents=agents)
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
