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


def list_changes(repo_root):
    """[(name, change_dir)] for every non-archive change dir containing tasks.md."""
    changes_dir = os.path.join(repo_root, "openspec", "changes")
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
