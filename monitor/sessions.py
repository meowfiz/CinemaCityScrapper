#!/usr/bin/env python3
"""
sessions.py -- start and stop a project working on its own (change agent-experience, phase E).

The user's own words: "chce rozmawiac z projektem i startowac, wylaczac". Everything else in this
system is a question or a one-shot task; this is the missing verb -- a project that WORKS, for as
long as it takes, without a window open and without anybody watching.

What "a running project" IS, decided by measurement rather than by taste (2026-09-18):

    claude --bg "<zadanie>"     starts a background session and prints  backgrounded * <short id>
    claude agents --json        lists it as kind=background, with the FULL session id
    claude logs <short id>      shows what it has been doing
    claude stop <short id>      stops it

Three facts that cost an experiment each, and that the code must respect:

  * `--bg` and `-p` CONFLICT. The prompt is positional. (`--print never starts the interactive
    session that agents attaches to, so the job would be unattachable.`)
  * `stop` wants the SHORT id, the first 8 characters. Handing it the full id from `agents --json`
    answers "No job matching ...", which reads exactly like "already stopped" and is not.
  * the task travels as an ARGUMENT, so it must be a single line -- on Windows `claude` is
    claude.CMD and cmd.exe truncates an argument at the first newline. That defect cost half a day
    on 2026-09-16 and is the reason ask_core puts questions on stdin instead.

This module never decides WHETHER a project may run: that gate lives with the execute worker,
which is where every other "may it write" decision already lives.

Standard library only. ASCII only.
"""

import os
import re
import shutil
import subprocess

try:
    from ask_core import QUIET_SUBPROCESS
except ImportError:  # pragma: no cover - the pack always ships ask_core
    QUIET_SUBPROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

START_TIMEOUT_S = 120.0
STOP_TIMEOUT_S = 60.0
TASK_MAX = 900

# "backgrounded * bcc5fb0c" -- the id is hex and short; the separator is a bullet the terminal
# may or may not render, so it is not part of the pattern.
_STARTED = re.compile(r"backgrounded\s*\W*\s*([0-9a-f]{6,12})\b", re.I)


def one_line(task):
    """The task as a single line, because it travels as a command-line argument.

    Newlines are not escaped or rejected -- they are folded to spaces. A refusal here would turn
    a perfectly good multi-line instruction into an error for a reason the user cannot see.
    """
    return " ".join(str(task or "").split())[:TASK_MAX]


def short_id(session_id):
    """What `claude stop` accepts: the first 8 characters of the id `agents --json` reports."""
    return str(session_id or "").strip()[:8]


def parse_started(stdout):
    """The id of a session just started, or None if the output does not name one."""
    match = _STARTED.search(stdout or "")
    return match.group(1) if match else None


def _run(args, cwd, timeout):
    """No shell: on Windows cmd.exe re-quotes the resolved path and has already turned one call
    into "is not recognized as an internal or external command" (2026-09-18)."""
    exe = shutil.which(args[0]) or args[0]
    return subprocess.run([exe] + list(args[1:]), cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          **QUIET_SUBPROCESS)


def start(repo_path, task, allow=None, runner=None, timeout=START_TIMEOUT_S):
    """(short_id, error). Starts a background session working in `repo_path`.

    Never raises: a project that will not start is a reported reason, because the caller is a
    worker loop that must keep going.
    """
    text = one_line(task)
    if not text:
        return None, "puste zadanie"
    if not repo_path or not os.path.isdir(repo_path):
        return None, "nie ma takiego katalogu: %s" % repo_path
    args = ["claude", "--bg"]
    if allow:
        args += ["--allowedTools"] + list(allow)
    args.append(text)
    try:
        out = (runner or _run)(args, repo_path, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "nie udalo sie uruchomic: %r" % (exc,)
    if getattr(out, "returncode", 1) != 0:
        return None, ("claude --bg zwrocil %s: %s"
                      % (out.returncode, (out.stderr or "").strip()[:300]))
    found = parse_started(out.stdout)
    if not found:
        return None, "sesja wystartowala, ale nie podala swojego numeru"
    return found, None


def stop(session_id, runner=None, timeout=STOP_TIMEOUT_S):
    """(stopped, message). A session that is already gone counts as stopped: the caller asked for
    a state, not for an event, and reporting a failure would send someone looking for nothing."""
    ident = short_id(session_id)
    if not ident:
        return False, "nie podano numeru sesji"
    try:
        out = (runner or _run)(["claude", "stop", ident], None, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "nie udalo sie zatrzymac: %r" % (exc,)
    text = ((out.stdout or "") + " " + (out.stderr or "")).strip()
    if getattr(out, "returncode", 1) == 0:
        return True, text[:200] or ("zatrzymana %s" % ident)
    if "no job matching" in text.lower():
        return True, "sesja %s juz nie dziala" % ident
    return False, text[:200] or "nieznany blad"
