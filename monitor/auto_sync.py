#!/usr/bin/env python3
"""
auto_sync.py -- end-of-session commit + push with guards (ZASADY_PRACY 2.8, session 65).

Two ways to run:
    python monitor/auto_sync.py --spawn        # from the SessionEnd hook: returns at once, the real
                                               # run continues detached (hook budget is a few seconds)
    python monitor/auto_sync.py                # run now, in the foreground (also what --spawn starts)
    python monitor/auto_sync.py --dry-run      # decide and report, change nothing

Guards -- ALL must hold, the first failure stops the run and is written to the report:
  G1  a session note for today (notes/sesje/<YYYY-MM-DD>-*.md) is either changed in the working
      tree or touched by the last commit -- no note, no sync (rule 1.1);
  G2  path policy (rule 2.5): TRACKED changes under a blocked prefix (project_files/run_files/,
      notes/user_tasks/, *.log, *.tmp) stop the commit; UNTRACKED files are committed only when they
      lie under an allowlisted prefix (notes/, openspec/, project_files/python/, monitor/, .claude/,
      .cursor/, CLAUDE.md, ZASADY_PRACY.md, .gitignore) -- other untracked files are left alone and
      listed in the report, never added;
  G3  after the commit, `git diff --name-status <upstream>...HEAD` has zero 'D' rows (rule 2.7);
  G4  `python -m pytest -q project_files/python/tests` is green (rule 4.4);
  G5  after `git fetch`, the upstream is an ancestor of HEAD (nobody pushed from the other machine;
      otherwise commit stays local and the report says so -- never pull/rebase/force automatically).

Every run appends one block to project_files/run_files/auto_sync.log (not in the repo) and posts a
`label` heartbeat "auto-sync: pushed|local|skipped (<reason>)" so the phone shows the outcome.
Standard library only, ASCII only. Never raises out of main(): exit code 0 unless --strict.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

ALLOWED_PREFIXES = ("notes/", "openspec/", "project_files/python/", "monitor/", ".cursor/", ".claude/")
ALLOWED_FILES = ("CLAUDE.md", "ZASADY_PRACY.md", ".gitignore")
BLOCKED_PREFIXES = ("project_files/run_files/", "notes/user_tasks/")
BLOCKED_SUFFIXES = (".log", ".tmp", ".pyc", ".pyo")
NOTE_DIR = "notes/sesje/"
# --untracked-files=all: plain `git status --porcelain` collapses a NEW directory to one row
# ("?? notes/sesje/"), so the first session note of a repo never matched NOTE_DIR + today and gate
# G1 refused to sync exactly the repo that had just started keeping notes (HA repo, 2026-09-16).
STATUS_ARGS = ["status", "--porcelain", "--untracked-files=all"]
TESTS = ["python", "-m", "pytest", "-q", "project_files/python/tests"]
LOG_REL = os.path.join("project_files", "run_files", "auto_sync.log")


# ---------------------------------------------------------------------------------------------
# pure decisions (tested in project_files/python/tests/test_auto_sync.py)
# ---------------------------------------------------------------------------------------------

def resolve_python(root, tests, exists=os.path.exists):
    """Same command, but run by the interpreter that can actually import the project.
    A repo-local virtualenv wins over the interpreter running this hook (which on Windows is
    whatever python Claude Code found -- here anaconda, without the server dependencies)."""
    if not tests or os.path.basename(tests[0]).lower() not in ("python", "python3", "python.exe", "python3.exe"):
        return list(tests)
    for rel in (os.path.join(".venv", "Scripts", "python.exe"), os.path.join(".venv", "bin", "python"),
                os.path.join("venv", "Scripts", "python.exe"), os.path.join("venv", "bin", "python")):
        if exists(os.path.join(root, rel)):
            return [os.path.join(root, rel)] + list(tests[1:])
    return [sys.executable or tests[0]] + list(tests[1:])


def test_paths(tests: list) -> list:
    """The path-looking arguments of a pytest command, i.e. everything after the last flag
    that is not itself a flag or a flag value."""
    skip = {"-m", "-p", "-k", "-o", "--rootdir"}
    out, prev = [], ""
    for arg in tests[1:]:
        if not arg.startswith("-") and prev not in skip and arg != "pytest":
            out.append(_norm(arg))
        prev = arg
    return out


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip().strip('"')


def parse_status(porcelain: str) -> list:
    """`git status --porcelain` -> list of (status, path). status '??' = untracked; renames give
    the new name. The caller must NOT strip the output: the first column may be a space."""
    out = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append((status, _norm(path)))
    return out


CONFIG_REL = os.path.join(".claude", "auto_sync.json")


def load_config(root):
    """Optional per-repo overrides: {"allow": ["server/", ...], "tests": ["python", "-m", ...]}.
    Absent or unreadable file = the defaults above, so no repo changes behaviour by accident."""
    cfg = {"allow": list(ALLOWED_PREFIXES), "files": list(ALLOWED_FILES), "tests": list(TESTS),
           "rebase": True}  # an unpushed commit is invisible from the other machine (2.8, 2026-09-16)
    try:
        with open(os.path.join(root, CONFIG_REL), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(data.get("allow"), list):
        cfg["allow"] = sorted(set(cfg["allow"]) | {_norm(p) for p in data["allow"] if p})
    if isinstance(data.get("files"), list):
        cfg["files"] = sorted(set(cfg["files"]) | {_norm(p) for p in data["files"] if p})
    if isinstance(data.get("tests"), list) and data["tests"]:
        cfg["tests"] = [str(x) for x in data["tests"]]
    if isinstance(data.get("rebase"), bool):
        cfg["rebase"] = data["rebase"]
    return cfg


def is_allowed(p: str, allow=None, files=None) -> bool:
    p = _norm(p)
    return p in (files or ALLOWED_FILES) or any(p.startswith(pre) for pre in (allow or ALLOWED_PREFIXES))


def is_blocked(p: str) -> bool:
    p = _norm(p)
    # __pycache__ anywhere: with --untracked-files=all a .pyc under an allowlisted monitor/ would
    # otherwise ride along (it did, HA repo 2026-09-16, commit 7a8b601 -- reverted by hand)
    if "__pycache__/" in p or p.startswith("__pycache__"):
        return True
    return any(p.startswith(pre) for pre in BLOCKED_PREFIXES) or p.endswith(BLOCKED_SUFFIXES)


def is_new(status: str) -> bool:
    """True when `git status --porcelain` says the path is not in HEAD yet: untracked ("??")
    or added to the index ("A "). Both must pass the allowlist; a modification of an already
    tracked file is a past human decision and travels with the session."""
    return status == "??" or status[:1] == "A"


def commit_command(paths: list, message: str) -> list:
    """`git commit -- <paths>` commits exactly those paths and leaves the rest of the index
    alone. Without the `--`, git commits the WHOLE index, so anything staged before the run
    (by the user or by a `stash pop`) rides along and silently bypasses G2."""
    return ["commit", "-q", "-m", message, "--"] + list(paths)


def classify(entries: list, allow=None, files=None) -> dict:
    """Split status entries into what to commit, what to leave, what blocks the run (G2)."""
    commit, leave, blocked = [], [], []
    for status, p in entries:
        if is_new(status):
            # not in HEAD yet: never ADD anything under run_files or outside the allowlist.
            # 'A ' counts as new even though git calls it staged -- `git stash pop` and a plain
            # `git add` both stage files the automat was never meant to decide about (2026-09-15).
            (commit if is_allowed(p, allow, files) and not is_blocked(p) else leave).append(p)
        elif _norm(p).endswith(BLOCKED_SUFFIXES):
            blocked.append(p)
        else:
            # already tracked = a past human decision (e.g. the small model JSONs under run_files
            # that the literature tables cite by SHA); a modification travels with the session
            commit.append(p)
    return {"commit": commit, "leave": leave, "blocked": blocked}


# A commit can pass every gate and still push a tree that does not run. Found by the Car session,
# 2026-09-16: `poc/ask_server.py` was tracked, so its edit was committed, while the brand-new
# `poc/intent.py` and `poc/aliases.json` it imports were left alone (poc/ is outside the allowlist).
# The work PC pulls hourly, would have taken an ask_server.py importing a missing module, and the
# watchdog would have restarted it silently. Half a change is worse than none: stop instead.
RISKY_SUFFIXES = (".py", ".json", ".yaml", ".yml", ".ps1", ".sh", ".js", ".ts", ".css", ".html", ".sql")


def _dirname(p: str) -> str:
    n = _norm(p)
    return n.rsplit("/", 1)[0] if "/" in n else ""


def risky_leftovers(commit: list, leave: list) -> list:
    """New source files sitting in a directory we are committing source into, left outside the
    allowlist. Deliberately narrow: only source-like next to source-like, same directory, so a
    stray backup next to a committed .gitignore does not stop the sync."""
    dirs = {_dirname(p) for p in commit if _norm(p).endswith(RISKY_SUFFIXES)}
    out = []
    for p in leave:
        n = _norm(p)
        if not is_blocked(n) and n.endswith(RISKY_SUFFIXES) and _dirname(n) in dirs:
            out.append(n)
    return sorted(out)


def has_today_note(paths: list, today: str) -> bool:
    """G1 on a list of paths (changed in tree or in the last commit)."""
    prefix = NOTE_DIR + today
    return any(_norm(p).startswith(prefix) and p.endswith(".md") for p in paths)


def deleted_rows(name_status: str) -> list:
    """Rows of `git diff --name-status` whose status starts with D. Empty == G3 holds."""
    return [ln for ln in name_status.splitlines() if ln[:1] == "D"]


def decide(entries: list, last_commit_paths: list, today: str, allow=None, files=None) -> tuple:
    """(action, reason, classification). action: 'commit' | 'push-only' | 'skip'."""
    c = classify(entries, allow, files)
    if c["blocked"]:
        return "skip", "G2 tracked artefacts changed: %s" % ", ".join(c["blocked"][:5]), c
    if c["commit"]:
        if not has_today_note(c["commit"], today) and not has_today_note(last_commit_paths, today):
            return "skip", "G1 no session note for %s" % today, c
        risky = risky_leftovers(c["commit"], c["leave"])
        if risky:
            return "skip", ("G2 new source next to committed source would be left behind: %s "
                            "-- add its prefix to .claude/auto_sync.json or move the files"
                            % ", ".join(risky[:5])), c
        return "commit", "%d paths to commit, %d untracked left alone" % (len(c["commit"]), len(c["leave"])), c
    if has_today_note(last_commit_paths, today):
        return "push-only", "tree clean, last commit carries the session note", c
    return "skip", "G1 nothing to commit and last commit has no session note for %s" % today, c


# ---------------------------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------------------------

def git(args, cwd, check=False, raw=False):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args), r.returncode, r.stderr.strip()))
    return r.stdout if raw else r.stdout.strip()


def repo_root(start):
    return git(["rev-parse", "--show-toplevel"], start) or start


def log_block(root, lines):
    try:
        path = os.path.join(root, LOG_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n=== %s\n" % _dt.datetime.now().isoformat(timespec="seconds"))
            for ln in lines:
                f.write(ln + "\n")
    except OSError:
        pass


def heartbeat_label(root, text):
    hb = os.path.join(root, "monitor", "heartbeat.py")
    if os.path.exists(hb):
        try:
            subprocess.run([sys.executable, hb, "--event", "label", "--label", text[:80]], cwd=root,
                           timeout=10, capture_output=True)
        except Exception:
            pass


def run(root, dry_run=False):
    today = _dt.date.today().isoformat()
    cfg = load_config(root)
    report = ["root=%s dry_run=%s" % (root, dry_run)]
    outcome = "skipped"
    try:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=True)
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
        if not upstream:
            report.append("skip: no upstream for %s" % branch)
            return outcome, report
        entries = parse_status(git(STATUS_ARGS, root, check=True, raw=True))
        last_paths = git(["show", "--pretty=format:", "--name-only", "HEAD"], root).splitlines()
        action, reason, c = decide(entries, last_paths, today, cfg["allow"], cfg["files"])
        report.append("decision=%s (%s)" % (action, reason))
        if c["leave"]:
            report.append("untracked left alone: %d (e.g. %s)" % (len(c["leave"]), ", ".join(c["leave"][:3])))
        if action == "skip":
            return outcome, report

        if action == "commit":
            report.append("to commit: %s" % ", ".join(c["commit"][:12]) + (" ..." if len(c["commit"]) > 12 else ""))
            if not dry_run:
                git(["add", "-A", "--"] + c["commit"], root, check=True)
                msg = ("auto-sync %s: session notes and tracked work (ZASADY 2.8)\n\n"
                       "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" % today)
                git(commit_command(c["commit"], msg), root, check=True)
                report.append("committed %s" % git(["rev-parse", "--short", "HEAD"], root))

        # push guards
        git(["fetch", "-q", "origin"], root)
        ahead = git(["rev-list", "--count", "%s..HEAD" % upstream], root) or "0"
        behind = git(["rev-list", "--count", "HEAD..%s" % upstream], root) or "0"
        report.append("ahead=%s behind=%s" % (ahead, behind))
        if ahead == "0" and not (action == "commit" and dry_run):
            report.append("nothing to push")
            outcome = "local"
            return outcome, report
        if behind != "0":
            # An unpushed commit is invisible from the other machine, so leaving it local is not a
            # safe default -- it is a lost commit waiting to happen (user decision 2026-09-16).
            # Rule 2.3 already prescribes the safe move: fetch, look, then pull --rebase (autostash),
            # never --force. The automat can do the "look" mechanically: the rebase must apply
            # cleanly, and afterwards G3 and G4 are re-checked against the NEW tree, because a push
            # of a rebase nobody tested is exactly what G4 exists to prevent.
            if cfg["rebase"] and not dry_run:
                ok, note = rebase_onto_upstream(root)
                report.append("G5 upstream moved (behind %s): %s" % (behind, note))
                if not ok:
                    outcome = "local"
                    return outcome, report
                behind = git(["rev-list", "--count", "HEAD..%s" % upstream], root) or "0"
                ahead = git(["rev-list", "--count", "%s..HEAD" % upstream], root) or "0"
                report.append("po rebase: ahead=%s behind=%s" % (ahead, behind))
            else:
                report.append("G5 upstream moved (behind %s): commit stays local (rebase off%s)"
                              % (behind, ", dry-run" if dry_run else ""))
                outcome = "local"
                return outcome, report
        dels = deleted_rows(git(["diff", "--name-status", "%s...HEAD" % upstream], root))
        if dels:
            report.append("G3 deletions vs upstream (%d): %s" % (len(dels), "; ".join(dels[:5])))
            outcome = "local"
            return outcome, report
        missing = [p for p in test_paths(cfg["tests"]) if not os.path.exists(os.path.join(root, p))]
        if missing and len(missing) == len(test_paths(cfg["tests"])):
            # a repo with no tests at all: say so, do not fake a green run and do not block
            # forever on pytest exit code 4 ("no tests ran") -- that killed rule 2.9 here
            report.append("G4 brak testow (%s) -- pominieta" % ", ".join(missing))
        else:
            cmd = resolve_python(root, cfg["tests"])
            report.append("G4 interpreter: %s" % os.path.basename(cmd[0]))
            t = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900)
            tail = (t.stdout.strip().splitlines() or [""])[-1]
            report.append("G4 pytest rc=%d: %s" % (t.returncode, tail))
            if t.returncode != 0:
                outcome = "local"
                return outcome, report
        if dry_run:
            report.append("dry-run: would push %s -> %s" % (branch, upstream))
            outcome = "local"
            return outcome, report
        git(["push", "-q", "origin", "HEAD"], root, check=True)
        remote = git(["ls-remote", "origin", branch], root).split()[0:1]
        head = git(["rev-parse", "HEAD"], root)
        ok = bool(remote) and remote[0] == head
        report.append("pushed; ls-remote %s HEAD" % ("==" if ok else "!="))
        outcome = "pushed" if ok else "local"
        return outcome, report
    except Exception as e:  # never break a session end
        report.append("error: %r" % (e,))
        return outcome, report


def rebase_outcome(rc, conflicts):
    """Pure: (ok, note) for a `git pull --rebase --autostash` result. A conflict is not a failure to
    hide -- it is the one case a human has to look at, so the commit stays local and says why."""
    if rc == 0 and not conflicts:
        return True, "pull --rebase czysty, probuje push"
    if conflicts:
        return False, "KONFLIKT przy rebase (%s) -- commit zostaje lokalny, rozwiaz recznie" % (
            ", ".join(conflicts[:3]))
    return False, "pull --rebase nieudany (rc=%s) -- commit zostaje lokalny" % rc


def git_rc(args, cwd):
    """(returncode, stdout) -- git() swallows the code, and here the code is the decision."""
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def rebase_onto_upstream(root):
    """pull --rebase --autostash, aborting cleanly on conflict so the tree is never left mid-rebase."""
    rc, _ = git_rc(["pull", "--rebase", "--autostash"], root)
    conflicts = [ln for ln in git(["diff", "--name-only", "--diff-filter=U"], root).splitlines()
                 if ln.strip()]
    ok, note = rebase_outcome(rc, conflicts)
    if not ok:
        git(["rebase", "--abort"], root)  # harmless when no rebase is in progress
    return ok, note


def spawn_detached(root):
    args = [sys.executable, os.path.abspath(__file__), "--root", root]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": root}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--spawn", action="store_true", help="return immediately, run detached")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 when not pushed")
    a = ap.parse_args(argv)
    root = repo_root(a.root)
    if a.spawn:
        spawn_detached(root)
        return 0
    outcome, report = run(root, dry_run=a.dry_run)
    log_block(root, [outcome] + report)
    reason = report[-1] if report else ""
    if not a.dry_run:
        heartbeat_label(root, "auto-sync: %s (%s)" % (outcome, reason[:50]))
    for ln in [outcome] + report:
        print(ln)
    return 0 if (outcome == "pushed" or not a.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
