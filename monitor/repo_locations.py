#!/usr/bin/env python3
"""
repo_locations.py -- which repositories this machine actually has, and where (change
machine-repo-registry).

The monitor has always routed work by PROJECT NAME while knowing nothing about whether the
repository exists anywhere. The knowledge lived in ~/.claude/monitor_repos.env and never left the
machine, so "where is this project checked out, and does anyone even have it" could not be
answered from the phone or from the other computer.

Two things this module deliberately does NOT do:

  * it never clones. A worker that clones on demand is a worker that creates directories nobody
    asked for -- which is exactly the problem this change was raised to prevent (user, 2026-09-18:
    "zeby nie tworzyc lokalnie jakichs bzdurnych katalogow"). A missing clone stays a refusal with
    a readable reason.
  * it never writes the result into a repository. Paths differ per machine, so a file in git would
    conflict from both sides on every commit (rule 2.4), and a public repo would publish the
    directory layout of a private machine. The server is the registry; git is not.

What it reports is an OBSERVATION, not a truth about disks: a machine that is switched off says
nothing, and silence means "unknown", never "the clone is gone". No destructive decision may rest
on it.

Standard library only. ASCII only.
"""

import os
import subprocess

# The worker calling this runs DETACHED, so every console child would otherwise flash its own
# window on Windows (found live 2026-09-17).
try:
    from ask_core import QUIET_SUBPROCESS
except ImportError:  # pragma: no cover - the pack always ships ask_core
    QUIET_SUBPROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

GIT_TIMEOUT_S = 10.0


def _git(args, cwd, timeout=GIT_TIMEOUT_S):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt, nobody is watching
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env,
                          **QUIET_SUBPROCESS)


def describe(project_id, path, runner=None):
    """One clone as the registry stores it. Never raises -- a broken checkout is still a fact
    worth reporting, and a report that throws takes the heartbeat down with it.

    `present` is the only field that is certain. Everything else is best effort: a directory that
    exists but is not a git repository, or a git that times out, still yields a usable row saying
    the clone is there.
    """
    row = {"project_id": project_id, "path": path, "present": False,
           "remote": None, "branch": None, "clean": None}
    if not path or not os.path.isdir(path):
        return row
    row["present"] = True
    run = runner or _git
    try:
        remote = run(["remote", "get-url", "origin"], path)
        if remote.returncode == 0:
            row["remote"] = (remote.stdout or "").strip() or None
        branch = run(["rev-parse", "--abbrev-ref", "HEAD"], path)
        if branch.returncode == 0:
            row["branch"] = (branch.stdout or "").strip() or None
        status = run(["status", "--porcelain"], path)
        if status.returncode == 0:
            row["clean"] = not (status.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        pass  # the directory is there; that alone answers the question this registry exists for
    return row


def collect(repo_map, runner=None):
    """Every repository this machine knows about, sorted by project id for a stable report."""
    return [describe(name, path, runner) for name, path in sorted((repo_map or {}).items())]


def present_only(rows):
    """The rows a router may act on: a mapped path whose directory is actually there."""
    return [r for r in rows or [] if r.get("present")]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ask_worker
    for r in collect(ask_worker.load_repo_map()):
        print("%-26s %-8s %-28s %s" % (r["project_id"], "jest" if r["present"] else "BRAK",
                                       r["branch"] or "-", r["path"]))
