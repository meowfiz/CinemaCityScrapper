#!/usr/bin/env python3
"""
live_sessions.py -- which Claude Code windows are open on THIS machine, right now.

Answers the half of "wydawanie zlecen do aktywnych okien" that the platform actually supports.
The spike (2026-09-17) settled what is and is not possible:

  DISCOVERY      yes. `claude agents --json` prints every active session, interactive ones
                 included, as JSON, and explicitly does not need a TTY -- so a detached worker
                 can read it. Each entry carries pid, cwd, kind, status (busy/idle), sessionId
                 and a human name.
  SEND INTO A    no. Nothing in the CLI injects a prompt into a running interactive window.
  LIVE WINDOW    `claude attach` opens a BACKGROUND session in a terminal; `--resume <id>` on a
                 session that is already running "starts a copy and says so". There is no inbox
                 for a live TTY, so a dispatcher cannot make a window that a human is using do
                 something -- and it should not pretend otherwise.
  DISPATCH INTO  yes, with a condition: `claude -p --resume <sessionId>` continues THAT
  ITS CONTEXT    conversation, but only sensibly while the window is idle; against a busy one it
                 forks a copy, which is two sessions editing one repo -- exactly what rule 2.7
                 exists to prevent.

So this module reports; it never dispatches. What it enables is the honest question from the
phone: "what is open where, and is it busy" -- and, later, a dispatcher that refuses to touch a
busy window instead of racing a human.

Standard library only. ASCII only.
"""

import json
import os
import shutil
import subprocess

# Same reason as ask_core: the worker calling this runs DETACHED, so every console child would
# otherwise flash its own window (found live 2026-09-17).
QUIET_SUBPROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

TIMEOUT_S = 25.0
BUSY = "busy"
IDLE = "idle"


def repo_of(cwd):
    """The repository name a window is working in -- the last path segment, however it is spelled.

    Windows paths arrive with backslashes and any case ("D:\\programming\\ribnxtr2026"), so this
    is deliberately not os.path.basename alone: the monitor's project ids are the repo directory
    names, and a trailing separator would otherwise yield an empty name.
    """
    text = str(cwd or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


def parse_windows(stdout):
    """`claude agents --json` output -> the rows the monitor stores. Never raises: a CLI that
    changed its output, or printed a warning first, must not take the heartbeat down with it."""
    text = (stdout or "").strip()
    if not text:
        return []
    start = text.find("[")
    if start < 0:
        return []
    try:
        parsed = json.loads(text[start:])
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        cwd = entry.get("cwd") or ""
        out.append({
            "pid": entry.get("pid"),
            "cwd": cwd,
            "repo": repo_of(cwd),
            "kind": entry.get("kind") or "",
            "status": entry.get("status") or "",
            "session_id": entry.get("sessionId") or "",
            "name": entry.get("name") or "",
        })
    return out


def list_windows(runner=None, timeout=TIMEOUT_S):
    """Every live Claude Code window on this machine, or [] if the CLI cannot be asked.

    An empty list means "nothing to report", never an error: this is decoration on a heartbeat,
    and a machine whose `claude` is missing or slow must keep beating.
    """
    run = runner or _run
    try:
        result = run(["claude", "agents", "--json"], timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    return parse_windows(getattr(result, "stdout", ""))


def _run(args, timeout):
    """Resolve `claude` once and run it WITHOUT a shell.

    On Windows `claude` is claude.CMD, which is why this used shell=True. But that puts cmd.exe
    between us and the program, and cmd.exe re-quotes the path -- on 2026-09-18 one call came
    back with "'\"C:\...\claude.exe\"' is not recognized", and because a non-zero exit means
    "cannot ask", the window list read as EMPTY: not "nothing is running", but "I could not
    look", reported as the former. shutil.which resolves the .CMD itself, the same pattern
    ask_core has used since it was written.
    """
    exe = shutil.which(args[0]) or args[0]
    return subprocess.run([exe] + list(args[1:]), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          **QUIET_SUBPROCESS)


def dispatchable(window):
    """Could a dispatcher safely continue this window's conversation?

    Only an idle interactive window: resuming a busy one forks a copy, and two sessions editing
    one working tree is the accident rule 2.7 was written for.
    """
    return bool(window.get("session_id")) and window.get("kind") == "interactive" \
        and window.get("status") == IDLE


if __name__ == "__main__":
    for w in list_windows():
        print("%-28s %-12s %-8s %s" % (w["repo"], w["kind"], w["status"], w["session_id"]))
