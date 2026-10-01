"""
taskparse.py -- shared parser for OpenSpec tasks.md and change context.

This file is copied verbatim into two places:
  pack/monitor/taskparse.py   (shipped to every repo, used by heartbeat.py
                               and notes/gen_openspec_status.py)
  server/taskparse.py         (used by the GitHub poller)
A test compares the SHA-256 of both copies, so edit one and copy.

Parsing rules (identical to the original notes/gen_openspec_status.py from
RibnXtr2026, kept 1:1 so STATUS.md in a repo and the number shown on the
phone never disagree):
  - "## N. Group name" -> a group heading
  - "- [ ] N.N description" / "- [x] N.N description" -> a task line
  - Multi-line continuations (extra detail under a task, not starting with
    "- [") are folded into the preceding task's description.

Standard library only. ASCII only.
"""

import glob
import os
import re

TASK_RE = re.compile(r"^- \[([ xX])\]\s*(.*)$")
GROUP_RE = re.compile(r"^##\s+(.*)$")
WHY_RE = re.compile(r"^##\s+Why\s*$")
HEADING_RE = re.compile(r"^#{1,6}\s")

SUMMARY_MAX = 90


def parse_tasks_lines(lines):
    """Returns list of (group_name, [[done, text], ...]) from an iterable of lines."""
    groups = []
    current_group = None
    current_tasks = None
    current_task = None

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        m = GROUP_RE.match(line)
        if m:
            if current_group is not None:
                groups.append((current_group, current_tasks))
            current_group = m.group(1).strip()
            current_tasks = []
            current_task = None
            continue

        m = TASK_RE.match(line)
        if m:
            done = m.group(1).lower() == "x"
            text = m.group(2).strip()
            current_task = [done, text]
            if current_tasks is not None:
                current_tasks.append(current_task)
            continue

        stripped = line.strip()
        if current_task is not None and stripped and current_tasks is not None:
            current_task[1] = (current_task[1] + " " + stripped).strip()

    if current_group is not None:
        groups.append((current_group, current_tasks))
    return groups


def parse_tasks_text(text):
    return parse_tasks_lines(text.splitlines(True))


def parse_tasks_md(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_tasks_lines(f)


def progress(groups):
    """Returns (done, total)."""
    total = sum(len(tasks) for _, tasks in groups)
    done = sum(1 for _, tasks in groups for d, _ in tasks if d)
    return done, total


def first_open_task(groups):
    """Text of the first unchecked task, or None."""
    for _, tasks in groups:
        for done, text in tasks:
            if not done:
                return text
    return None


def summary_from_text(text, max_len=SUMMARY_MAX):
    """First sentence of the '## Why' section, cut to max_len. None if absent."""
    in_why = False
    para = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if WHY_RE.match(line):
            in_why = True
            continue
        if not in_why:
            continue
        if HEADING_RE.match(line):
            break
        stripped = line.strip()
        if stripped.startswith("<!--"):
            continue
        if not stripped:
            if para:
                break
            continue
        para.append(stripped)
    if not para:
        return None
    joined = " ".join(para)
    m = re.search(r"[.!?](\s|$)", joined)
    sentence = joined[: m.end()].strip() if m else joined
    if len(sentence) > max_len:
        sentence = sentence[: max_len - 3].rstrip() + "..."
    return sentence


def summary_from_proposal(path, max_len=SUMMARY_MAX):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return summary_from_text(f.read(), max_len)
    except OSError:
        return None


OPENSPEC_MAX_DEPTH = 3  # how deep below the repo root a nested openspec/ may sit
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "archive"}


def _has_changes(openspec_dir):
    return os.path.isdir(os.path.join(openspec_dir, "changes"))


def openspec_dir(repo_root):
    """<root>/openspec, or else the shallowest nested .../openspec that has changes/.

    Some projects keep their spec next to the material, not at the root (Python_I_2.0:
    nowe/New/openspec). Hard-coding <root>/openspec showed such a repo as "no OpenSpec" on
    the phone and in HA. The root still wins whenever it exists, so no other repo changes.
    Ties at one depth break by sorted path, so the answer does not depend on listdir order.
    None when there is no OpenSpec at all."""
    top = os.path.join(repo_root, "openspec")
    if _has_changes(top):
        return top
    level = [repo_root]
    for _depth in range(OPENSPEC_MAX_DEPTH):
        nxt = []
        for base in level:
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for n in names:
                p = os.path.join(base, n)
                if n in _SKIP_DIRS or not os.path.isdir(p):
                    continue
                nxt.append(p)
        for base in nxt:
            cand = os.path.join(base, "openspec")
            if _has_changes(cand):
                return cand
        level = nxt
    return None


def openspec_prefix(paths):
    """Same rule as openspec_dir, on a flat list of repo paths (a GitHub tree has no dirs to
    walk): "" for <root>/openspec, "nowe/New/" for a nested one, None when no change has a
    tasks.md. Only active (non-archive) changes count."""
    best = None
    for p in paths:
        m = CHANGE_TASKS_RE.match(p)
        if not m:
            continue
        pre = m.group("pre")
        if pre.count("/") > OPENSPEC_MAX_DEPTH:
            continue
        key = (pre.count("/"), pre)
        if best is None or key < best:
            best = key
    return None if best is None else best[1]


CHANGE_TASKS_RE = re.compile(r"^(?P<pre>(?:[^/]+/)*?)openspec/changes/(?!archive/)(?P<name>[^/]+)/tasks\.md$")


def list_changes(repo_root):
    """[(name, change_dir)] for every non-archive change dir containing tasks.md."""
    spec = openspec_dir(repo_root)
    if spec is None:
        return []
    changes_dir = os.path.join(spec, "changes")
    out = []
    for d in sorted(glob.glob(os.path.join(changes_dir, "*"))):
        if not os.path.isdir(d) or os.path.basename(d) == "archive":
            continue
        if os.path.exists(os.path.join(d, "tasks.md")):
            out.append((os.path.basename(d), d))
    return out


def active_change(repo_root):
    """(name, change_dir) of the change whose tasks.md has the newest mtime, or None."""
    best = None
    best_mtime = -1.0
    for name, d in list_changes(repo_root):
        mtime = os.path.getmtime(os.path.join(d, "tasks.md"))
        if mtime > best_mtime:
            best = (name, d)
            best_mtime = mtime
    return best


def describe_repo(repo_root):
    """Task context for a heartbeat: change, task, done, total, summary (None when no OpenSpec)."""
    ctx = {"change": None, "task": None, "done": None, "total": None, "summary": None}
    ac = active_change(repo_root)
    if ac is None:
        return ctx
    name, d = ac
    groups = parse_tasks_md(os.path.join(d, "tasks.md"))
    done, total = progress(groups)
    ctx["change"] = name
    ctx["task"] = first_open_task(groups)
    ctx["done"] = done
    ctx["total"] = total
    ctx["summary"] = summary_from_proposal(os.path.join(d, "proposal.md")) or name
    return ctx
