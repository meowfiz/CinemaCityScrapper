"""repo_touch.py -- which repository is this session actually CHANGING (change touched-repos).

Measured 2026-09-19: a session opened in project_integration spent half a day editing files in the
HA repository (a kettle automation, a config dump from the Pi, commits, a PR). The monitor showed
HA as idle the whole time, because every heartbeat carried the SESSION's repo -- the directory the
window was opened in -- and nothing ever carried the repo the work landed in.

The source of truth here is a file WRITE, not a declaration and not a mode somebody has to switch
on: PostToolUse already hands the hook the tool name and its arguments, so the path a Write/Edit
just saved is free information. A read is not work on a repository and deliberately does not count.

Privacy: this module turns a path into a repo NAME. The path itself never leaves the machine --
the caller sends the name, the machine and the source repo, nothing else.

Standard library only. ASCII only. Every function here is pure apart from load_repo_map, which
reads one small file; nothing raises for the caller (the hook must never break a session).
"""

import os

# ~/.claude/monitor_repos.env -- the map of this machine's clones, "name=path" per line. Written by
# the SessionStart hook (ask_worker.register_repo) and never committed: paths differ per machine.
REPOS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "monitor_repos.env")

# tool name -> the argument naming the file it wrote. Read/Grep/Glob are absent on purpose: reading
# a repository is not working on it, and counting it would mark every repo a search touched.
WRITE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

# How long one repo stays quiet after a report. Far below the server's 90 s worker ttl, so the
# badge never expires between two writes, and far above one tool call, so a loop of edits in one
# file costs one request rather than a hundred.
TOUCH_THROTTLE_S = 20.0


def load_repo_map(path=None):
    """{repo name: absolute path} for the repos this machine knows about.

    Lives here rather than in ask_worker.py so the PostToolUse hook can read the map without
    importing the worker (which pulls in the whole ask stack); ask_worker re-exports it, so there
    is still exactly one parser of this file.
    """
    out = {}
    try:
        with open(path or os.environ.get("MONITOR_REPOS_FILE") or REPOS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, target = line.split("=", 1)
                name, target = name.strip(), target.strip().strip('"').strip("'")
                if name and target:
                    out[name] = target
    except OSError:
        pass
    return out


def written_path(tool_name, tool_input):
    """The file this tool call SAVED, or None. Anything that only read is None by design."""
    key = WRITE_TOOLS.get(tool_name)
    if key is None or not isinstance(tool_input, dict):
        return None
    value = tool_input.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _key(path):
    """A path in the one form two paths can be compared in on this machine."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def repo_for_path(path, repo_map):
    """The repo in the map that CONTAINS this path, longest root first, else None.

    Longest first because a repo checked out inside another one (a worktree, a vendored clone)
    must win over its parent -- the other order would report every edit as the parent's.
    """
    if not path or not repo_map:
        return None
    try:
        target = _key(path)
    except (OSError, ValueError, TypeError):
        return None
    best = None
    for name, root in repo_map.items():
        if not root:
            continue
        try:
            rkey = _key(root)
        except (OSError, ValueError, TypeError):
            continue
        if target == rkey or target.startswith(rkey.rstrip(os.sep) + os.sep):
            if best is None or len(rkey) > best[1]:
                best = (name, len(rkey))
    return best[0] if best else None


def touched_repo(tool_name, tool_input, repo_map, session_repo):
    """The OTHER repo this tool call wrote into, or None.

    None when the tool did not write, when the path is outside every known repo, and -- the case
    that matters -- when it wrote inside the session's own repo: that is already on the dashboard
    as the session's repo, and reporting it again would say nothing new.
    """
    name = repo_for_path(written_path(tool_name, tool_input), repo_map)
    if name is None or (session_repo and name == session_repo):
        return None
    return name


def next_touch(state, repo, now, throttle_s=TOUCH_THROTTLE_S):
    """Pure: (new state, True if this touch should be reported now).

    state is {repo: epoch of the last report} or None. Returning the state rather than writing it
    keeps the throttle testable without a clock and without a temp directory.
    """
    out = dict(state or {})
    try:
        last = float(out.get(repo, 0.0))
    except (TypeError, ValueError):
        last = 0.0
    if now - last < throttle_s:
        return out, False
    out[repo] = now
    return out, True
