#!/usr/bin/env python3
"""
execute_worker.py -- runs work items that CHANGE code (change project-orchestrator, phase D).

    python monitor/execute_worker.py --once     # take at most one item, run it, report
    python monitor/execute_worker.py --serve    # keep taking items until stopped
    python monitor/execute_worker.py --status   # is this machine allowed to execute, and why not

This is the first mechanism in the system that writes code with nobody at the keyboard, so it is
built around refusals, not around features:

  * OFF unless the repository says otherwise. `.claude/orchestrator.json` with {"execute": true}
    is the only thing that turns it on, and it lives in the repository, not in the pack.
  * Never the default branch. Work happens on `orch/<item id>` and the branch is checked again
    right before the commit -- a name computed once is not proof of where HEAD ended up.
  * Never on top of somebody's work. A dirty tree is a refusal: the human at that machine may be
    mid-change, and committing their files under a task they did not write is unforgivable.
  * Never a red tree. `pytest` must pass, and its exit code is read directly -- no pipe that can
    swallow it (rule 2.7).
  * Never a deletion. `git diff --name-status` with a single D line stops the commit.
  * Always back to where we started. The original branch is restored in a `finally`, including
    after a crash, so the next human to sit down finds their checkout as they left it.

The agent itself gets Write/Edit but NOT git: committing and pushing are done here, after the
gates, so "the model decided to commit" cannot happen.

Standard library only. ASCII only. Never raises out of main().
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_core  # noqa: E402
import capabilities  # noqa: E402
import heartbeat  # noqa: E402
import sessions  # noqa: E402

try:
    import ask_worker  # noqa: E402  - shares the config loader, the api() helper and the repo map
except ImportError:  # pragma: no cover - the pack always ships both
    ask_worker = None

# One source of truth for "this never goes into a commit" (rule 2.5), shared with the automatic
# end-of-session sync. Restating the lists here would let them drift apart silently.
try:
    from auto_sync import BLOCKED_PREFIXES, BLOCKED_SUFFIXES  # noqa: E402
except ImportError:  # pragma: no cover - the pack always ships auto_sync.py
    BLOCKED_PREFIXES = ("project_files/run_files/", "notes/user_tasks/")
    BLOCKED_SUFFIXES = (".log", ".tmp", ".pyc", ".pyo")

WORKER_KIND = "execute"
WORKER_VERSION = "1.0"
POLL_S = 30.0
AGENT_TIMEOUT_S = 900.0        # 15 minutes of model work, then the item fails with a reason
TESTS_TIMEOUT_S = 900.0
FLAGS_FILE = os.path.join(".claude", "orchestrator.json")
BRANCH_PREFIX = "orch/"
PROTECTED_BRANCHES = ("main", "master")

AGENT_ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "Bash(git status:*)", "Bash(git diff:*)",
               "Bash(git log:*)", "Bash(python -m pytest:*)", "Bash(pytest:*)"]
AGENT_DENY = ["Bash(git push:*)", "Bash(git commit:*)", "Bash(git add:*)", "Bash(git checkout:*)",
              "Bash(git reset:*)", "Bash(git rebase:*)", "Bash(gh:*)", "WebFetch", "WebSearch", "Task"]

AGENT_SYSTEM = (
    "Wykonujesz jedno zadanie w repozytorium. Zmieniaj pliki narzedziami Write i Edit. "
    "Nie commituj, nie pushuj, nie zmieniaj galezi - zrobi to system po sprawdzeniu bramek. "
    "Uruchom testy, jesli repozytorium je ma. Jesli zadanie jest niejasne albo wymaga decyzji "
    "projektowej, nie zgaduj: napisz, czego brakuje, i nie zmieniaj plikow."
)


# -- flags ----------------------------------------------------------------------------------

def load_flags(repo_path, read=None):
    """Read .claude/orchestrator.json. Missing file, bad JSON and a bad type all mean OFF:
    an unattended writer must fail closed."""
    path = os.path.join(repo_path, FLAGS_FILE)
    try:
        raw = read(path) if read else open(path, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def execute_enabled(flags):
    return flags.get("execute") is True


def push_enabled(flags):
    """Pushing the branch is separately switchable: a machine may be allowed to work and not
    allowed to publish."""
    return flags.get("push", True) is True


def pr_enabled(flags):
    return flags.get("pr", False) is True


# -- pure gates -----------------------------------------------------------------------------

def branch_name(item_id):
    return "%s%s" % (BRANCH_PREFIX, item_id)


def is_protected(branch):
    return (branch or "").strip().lower() in PROTECTED_BRANCHES


def dirty_lines(status_porcelain):
    """Porcelain lines that count as somebody's work in progress.

    The flags file is excluded on purpose: it is this worker's own switch, it is often left
    untracked, and refusing to work because of the file that enables working is a trap -- the
    first run would always fail with a message about a clean tree.
    """
    out = []
    for line in (status_porcelain or "").splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"').replace("\\", "/")
        if path.endswith(FLAGS_FILE.replace("\\", "/")):
            continue
        out.append(line)
    return out


def refuse_reason(flags, status_porcelain, item):
    """Everything that can be decided before touching git. Returns None when the item may run.

    Order matters only for the message; each condition is independent.
    """
    if not execute_enabled(flags):
        return "execute is off on this machine (.claude/orchestrator.json)"
    if dirty_lines(status_porcelain):
        return "working tree is not clean; refusing to commit somebody else's changes"
    if not (item.get("text") or "").strip():
        return "empty task text"
    return None


def artifacts(name_status):
    """Staged paths that rule 2.5 says never belong in a commit: logs and run artefacts.

    Found on the very first real execute run (2026-09-18, item 27): the agent's work left
    `project_files/run_files/auto_sync.log` in the tree and `git add -A` swept it into the
    commit. auto_sync.py has refused these paths since it was written; this worker commits too,
    so it needs the same rule -- and it imports the lists rather than restating them, because two
    copies of an allowlist drift and the drift is invisible until something wrong is published.
    """
    out = []
    for line in (name_status or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = parts[-1].strip().strip('"').replace("\\", "/")
        if path.startswith(BLOCKED_PREFIXES) or path.endswith(BLOCKED_SUFFIXES):
            out.append(path)
    return out


def allowed_paths(flags):
    """Path prefixes this repository allows an unattended agent to change.

    Empty list (the default) means "no restriction beyond the other gates" -- adding a scope is a
    decision the repository makes, not one forced on every repository the day this shipped.
    """
    raw = flags.get("paths")
    if not isinstance(raw, list):
        return []
    return [str(p).strip().replace("\\", "/").lstrip("./") for p in raw if str(p).strip()]


def outside_scope(name_status, allowed):
    """Staged paths that fall outside what the repository allows (change agent-experience D.1).

    Until now `execute: true` was a single boolean: the agent could touch anything in the
    repository. That is fine for a sandbox and too much for a project with real work in it -- the
    switch people hesitate over is not "may it write" but "may it write THERE".
    """
    if not allowed:
        return []
    out = []
    for line in (name_status or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = parts[-1].strip().strip('"').replace("\\", "/")
        if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in allowed):
            out.append(path)
    return out


def too_big(name_status, flags):
    """A change larger than the repository is willing to accept unattended, or None.

    A limit on FILES, not on lines: it is the number a person can hold in their head when
    deciding whether to read a diff, and it does not punish a legitimately long generated file.
    """
    limit = flags.get("max_files")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    changed = [ln for ln in (name_status or "").splitlines() if ln.strip()]
    if len(changed) <= limit:
        return None
    return ("zmiana obejmuje %d plikow, a to repozytorium pozwala bez nadzoru na %d"
            % (len(changed), limit))


def deletions(name_status):
    """Lines of `git diff --name-status` that delete a file. Rule 2.7: a commit made by a machine
    never removes anything."""
    out = []
    for line in (name_status or "").splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip().upper().startswith("D"):
            out.append(parts[-1].strip())
    return out


def commit_message(item_id, text):
    """One subject line plus the task, so `git log` answers 'why does this branch exist'."""
    first = " ".join((text or "").split())
    subject = first[:60] if first else "orchestrator task"
    return "orch #%s: %s\n\nZadanie zlecone przez Project Orchestrator (work item %s).\n" % (
        item_id, subject, item_id)


def outcome_error(reason):
    return {"error": reason[:2000]}


# -- git ------------------------------------------------------------------------------------

def git(args, cwd, timeout=120.0, runner=None):
    run = runner or subprocess.run
    return run(["git"] + list(args), cwd=cwd, capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=timeout, **ask_core.QUIET_SUBPROCESS)


def git_out(args, cwd, runner=None):
    proc = git(args, cwd, runner=runner)
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def current_branch(cwd, runner=None):
    return git_out(["rev-parse", "--abbrev-ref", "HEAD"], cwd, runner)


# -- the run ---------------------------------------------------------------------------------

def say(on_progress, pct, note):
    """Report a stage if somebody is listening. The stages are coarse on purpose: 15/65/85 are
    real boundaries in this function (branch made, tests starting, committing), and a made-up
    smooth percentage would be a worse lie than three honest ones."""
    if on_progress:
        try:
            on_progress(pct, note)
        except Exception:  # noqa: BLE001 - progress must never fail the work
            pass


def run_item(repo_path, item, agent=None, git_runner=None, flags=None, timeout=AGENT_TIMEOUT_S,
             on_progress=None):
    """Do one work item. Returns the body to POST to /api/tasks/{id}/result.

    Never raises: a failure is a reported failure, because an unreported one leaves the item
    RUNNING until the requeue sweep, and the person who asked hears nothing.
    """
    item_id = item.get("id")
    flags = load_flags(repo_path) if flags is None else flags
    status = git_out(["status", "--porcelain", "--untracked-files=all"], repo_path, git_runner)
    reason = refuse_reason(flags, status, item)
    if reason:
        return outcome_error(reason)

    start_branch = current_branch(repo_path, git_runner)
    branch = branch_name(item_id)
    if is_protected(branch):
        return outcome_error("refusing to work on a protected branch: %s" % branch)

    created = False
    try:
        made = git(["checkout", "-b", branch], repo_path, runner=git_runner)
        if made.returncode != 0:
            return outcome_error("cannot create branch %s: %s" % (branch, (made.stderr or "")[:300]))
        created = True

        say(on_progress, 15, "galaz utworzona, pracuje nad zmianami")
        answer, error = _run_agent(repo_path, item, agent, timeout)
        if error:
            return outcome_error(error)

        # The branch is checked AGAIN here: the name was computed before the agent ran, and only
        # this answers where HEAD actually is now.
        here = current_branch(repo_path, git_runner)
        if here != branch or is_protected(here):
            return outcome_error("HEAD moved to %s; refusing to commit" % (here or "?"))

        # dirty_lines, not the raw status: the flags file is this worker's own switch and is
        # usually untracked. Counting it as a change made "the agent changed nothing" commit the
        # switch itself -- found by the test, not by reading.
        changed = dirty_lines(git_out(["status", "--porcelain", "--untracked-files=all"],
                                      repo_path, git_runner))
        if not changed:
            return {"result": (answer or "Brak zmian w plikach.")[:20000], "branch": branch}

        say(on_progress, 65, "zmiany gotowe, uruchamiam testy")
        tests = _run_tests(repo_path, flags, git_runner)
        if tests is not None:
            return outcome_error(tests)

        git(["add", "-A"], repo_path, runner=git_runner)
        git(["reset", "-q", "--", FLAGS_FILE], repo_path, runner=git_runner)  # never commit the switch
        staged = git_out(["diff", "--cached", "--name-status"], repo_path, git_runner)
        junk = artifacts(staged)
        if junk:
            # Unstaged, not refused: a log the agent happened to touch is not a reason to throw
            # away good work -- it is a reason not to publish the log (rule 2.5).
            git(["reset", "-q", "--"] + junk, repo_path, runner=git_runner)
            staged = git_out(["diff", "--cached", "--name-status"], repo_path, git_runner)
            if not staged.strip():
                return {"result": (answer or "Tylko artefakty, nic do zacommitowania.")[:20000],
                        "branch": branch}

        # change agent-experience D.1: WHERE an unattended agent may write, and how much of it.
        # Checked after the artefact unstaging, so a log outside the scope is dropped rather than
        # counted as a violation.
        stray = outside_scope(staged, allowed_paths(flags))
        if stray:
            git(["reset"], repo_path, runner=git_runner)
            return outcome_error(
                "zmiany poza dozwolonym zakresem (%s): %s"
                % (", ".join(allowed_paths(flags)), ", ".join(stray[:5])))
        oversized = too_big(staged, flags)
        if oversized:
            git(["reset"], repo_path, runner=git_runner)
            return outcome_error(oversized)
        gone = deletions(staged)
        if gone:
            git(["reset"], repo_path, runner=git_runner)
            return outcome_error("refusing a commit that deletes %d file(s): %s"
                                 % (len(gone), ", ".join(gone[:5])))

        say(on_progress, 85, "testy przeszly, commituje")
        done = git(["-c", "user.email=orchestrator@local", "-c", "user.name=orchestrator",
                    "commit", "-q", "-m", commit_message(item_id, item.get("text"))],
                   repo_path, runner=git_runner)
        if done.returncode != 0:
            return outcome_error("commit failed: %s" % (done.stderr or "")[:300])
        commit_hash = git_out(["rev-parse", "HEAD"], repo_path, git_runner)

        pr_url = None
        if push_enabled(flags):
            pushed = git(["push", "-q", "--set-upstream", "origin", branch], repo_path,
                         timeout=300.0, runner=git_runner)
            if pushed.returncode != 0:
                return {"result": (answer or "")[:20000], "commit_hash": commit_hash,
                        "branch": branch,
                        "error": "committed locally, push failed: %s" % (pushed.stderr or "")[:200]}
            if pr_enabled(flags):
                pr_url = _open_pr(repo_path, branch, item_id, item.get("text"))

        return {"result": (answer or "")[:20000], "commit_hash": commit_hash,
                "branch": branch, "pr_url": pr_url}
    except Exception as exc:  # noqa: BLE001 - a crash must still report
        return outcome_error("execute worker crashed: %r" % (exc,))
    finally:
        # Back to where the human left their checkout, always. The branch itself stays, so a
        # failed run can be inspected.
        if created and start_branch:
            git(["checkout", "-q", start_branch], repo_path, runner=git_runner)


def _run_agent(repo_path, item, agent, timeout):
    """The model gets Write/Edit but no git: commits are the system's decision, not the model's."""
    if agent is not None:
        return agent(repo_path, item)
    return ask_core.ask([repo_path], item.get("text") or "", system=AGENT_SYSTEM,
                        timeout=timeout, allow=AGENT_ALLOW, deny=AGENT_DENY, use_digest=True)


def _run_tests(repo_path, flags, git_runner=None):
    """None means the gate holds. The exit code is read directly -- a pipe that swallows it is
    exactly how a red tree got pushed once already (rule 2.7)."""
    if flags.get("tests") is False:
        return None
    python = _python_for(repo_path)
    try:
        proc = subprocess.run([python, "-m", "pytest", "-q"], cwd=repo_path, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=TESTS_TIMEOUT_S,
                              **ask_core.QUIET_SUBPROCESS)
    except (OSError, subprocess.SubprocessError) as exc:
        return "tests could not run: %r" % (exc,)
    if proc.returncode == 5:  # pytest: no tests collected
        return None
    if proc.returncode != 0:
        tail = " ".join((proc.stdout or "").split())[-400:]
        return "tests failed (exit %d): %s" % (proc.returncode, tail)
    return None


def _python_for(repo_path):
    """A repository with its own environment is tested with it -- the base interpreter often
    lacks the dependencies and would report a green run of nothing."""
    for rel in (os.path.join(".venv", "Scripts", "python.exe"), os.path.join(".venv", "bin", "python")):
        candidate = os.path.join(repo_path, rel)
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _open_pr(repo_path, branch, item_id, text):
    try:
        proc = subprocess.run(["gh", "pr", "create", "--fill", "--head", branch],
                              cwd=repo_path, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120.0,
                              **ask_core.QUIET_SUBPROCESS)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("http"):
            return line.strip()
    return None


# -- loop ------------------------------------------------------------------------------------

def worker_id(cfg):
    return "%s/%s" % (ask_worker.machine_name(cfg), WORKER_KIND)


def send_beat(cfg, repo_map, caps=None, running=0):
    body = {
        "machine": ask_worker.machine_name(cfg), "kind": WORKER_KIND,
        "hostname": platform.node(), "os": platform.system(), "version": WORKER_VERSION,
        "project_ids": sorted(_enabled_repos(repo_map)),
        "capabilities": list(caps) if caps is not None else capabilities.detect(list(repo_map.values())),
        "capacity": 1, "running": int(running),
    }
    try:
        ask_worker.api(cfg, "POST", "/api/workers/heartbeat", body, timeout=10.0)
        return True
    except Exception as e:  # noqa: BLE001
        heartbeat.log("execute worker heartbeat failed: %r" % (e,))
        return False


def _enabled_repos(repo_map):
    """Only repositories that switched execution on are advertised. A repo that never opted in
    must not even appear as routable, so the server queues instead of assigning."""
    return [name for name, path in (repo_map or {}).items()
            if path and execute_enabled(load_flags(path))]


def claim(cfg, limit=1):
    path = "/api/tasks/claim?worker_id=%s&kind=execute&kind=session&kind=session_stop&limit=%d" % (
        _quote(worker_id(cfg)), limit)
    return ask_worker.api(cfg, "POST", path).get("items", [])


def _quote(text):
    import urllib.parse
    return urllib.parse.quote(text, safe="")


def report(cfg, item_id, body):
    return ask_worker.api(cfg, "POST", "/api/tasks/%s/result" % item_id, body)


def progress(cfg, item_id, pct, note):
    """Tell the server how far this item is (change remote-tasks 7.4b). Best effort: a worker
    that cannot report progress must still finish the job, so this never raises and never
    retries -- the next stage will say where we got to anyway."""
    try:
        ask_worker.api(cfg, "POST", "/api/tasks/%s/progress" % item_id,
                       {"pct": pct, "note": note}, timeout=BEAT_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        pass


def run_session(repo_path, item, flags=None, starter=None):
    """Start a project working on its own. Returns the body to report.

    The same switch as every other write: a background session has Write and Edit, so a repository
    that has not opted into execution does not get one either.
    """
    flags = load_flags(repo_path) if flags is None else flags
    if not execute_enabled(flags):
        return outcome_error("execute is off in this repository (.claude/orchestrator.json)")
    short, error = (starter or sessions.start)(repo_path, item.get("text") or "")
    if error:
        return outcome_error(error)
    return {"result": "Projekt pracuje, numer sesji %s." % short, "session_bg_id": short}


def run_session_stop(item, stopper=None):
    """Stop one. The id travels in the item text, because that is what the caller heard."""
    ok, message = (stopper or sessions.stop)((item.get("text") or "").strip())
    if not ok:
        return outcome_error(message)
    return {"result": message}


def handle(cfg, item, repo_map, agent=None):
    repo = item.get("repo")
    kind = item.get("kind")
    if kind == "session_stop":
        # No repository needed: a session id is enough, and the session may already be gone.
        report(cfg, item.get("id"), run_session_stop(item))
        return "done"
    path = (repo_map or {}).get(repo)
    if not path or not os.path.isdir(path):
        report(cfg, item.get("id"), outcome_error("repo %s is not on this machine" % repo))
        return "unknown-repo"
    if kind == "session":
        body = run_session(path, item)
        report(cfg, item.get("id"), body)
        return "failed" if body.get("error") else "done"
    body = run_item(path, item, agent=agent,
                    on_progress=lambda pct, note: progress(cfg, item.get("id"), pct, note))
    report(cfg, item.get("id"), body)
    return "failed" if body.get("error") else "done"


def lock_path():
    """Its own lock, next to the ask worker's -- the two are different jobs and must not exclude
    each other, but two of THESE on one machine is an accident waiting to happen."""
    return os.path.join(tempfile.gettempdir(), "monitor_execute_worker.lock")


def another_is_running(now=None):
    """Is a second execute worker already serving on this machine?

    The ask worker has had this since it was written; this one was started by hand and never got
    it. It matters more here, not less: two ask workers would at worst answer two questions, but
    two execute workers check out branches in the SAME working tree, and the second one's
    `git checkout` lands in the middle of the first one's edits. That is precisely the accident
    rule 2.7 exists for, and no gate downstream can see it coming.
    """
    return ask_worker.lock_is_live(lock_path(), time.time() if now is None else now, POLL_S)


def touch_lock(now):
    try:
        with open(lock_path(), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": now, "machine": platform.node()}, f)
    except OSError:
        pass


def spawn_detached():
    """Start this worker in the background, no console window (same shape as the ask worker's)."""
    args = [sys.executable, os.path.abspath(__file__), "--serve"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": os.path.expanduser("~")}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def ensure_running(cfg, now=None):
    """Start the worker unless one is alive. Returns 'running' | 'spawned' | 'no-config'.

    The ask worker has had this since it was written and this one did not, which is why it was
    started by hand and was therefore usually not running at all: a work item sent from the phone
    sat in the queue until someone noticed. Same contract, same idempotence -- a second call while
    one serves is a no-op.
    """
    if not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN"):
        return "no-config"
    if another_is_running(now):
        return "running"
    spawn_detached()
    return "spawned"


def serve(cfg, once=False, sleep=time.sleep, agent=None):
    caps = None
    if not once and another_is_running():
        heartbeat.log("execute worker: another one is already serving here, exiting")
        return 0
    heartbeat.log("execute worker started pid=%d" % os.getpid())
    handled = 0
    while True:
        if not once:
            touch_lock(time.time())  # a stale lock is how the next start knows we died
        repo_map = ask_worker.load_repo_map()
        if caps is None:
            caps = capabilities.detect([p for p in repo_map.values() if p])
        send_beat(cfg, repo_map, caps)
        items = []
        if _enabled_repos(repo_map):        # nothing opted in: beat, but never ask for work
            try:
                items = claim(cfg)
            except Exception as e:  # noqa: BLE001
                heartbeat.log("execute worker claim failed: %r" % (e,))
        for item in items:
            try:
                send_beat(cfg, repo_map, caps, running=1)
                heartbeat.log("execute worker: %s #%s" % (item.get("repo"), item.get("id")))
                handle(cfg, item, repo_map, agent)
                handled += 1
            except Exception as e:  # noqa: BLE001 - one bad item must not stop the worker
                heartbeat.log("execute worker item failed: %r" % (e,))
        if once:
            return handled
        sleep(POLL_S if not items else 1.0)


def status_text(cfg, repo_map=None):
    repo_map = ask_worker.load_repo_map() if repo_map is None else repo_map
    on = _enabled_repos(repo_map)
    lines = ["execute worker %s" % worker_id(cfg),
             "repozytoria z wlaczonym wykonywaniem: %s" % (", ".join(on) if on else "brak")]
    for name, path in sorted((repo_map or {}).items()):
        flags = load_flags(path)
        lines.append("  %-28s execute=%s push=%s pr=%s" % (
            name, execute_enabled(flags), push_enabled(flags), pr_enabled(flags)))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--spawn", action="store_true", help="start detached unless one is running")
    args, _rest = ap.parse_known_args(argv)
    try:
        cfg = heartbeat.load_config()
    except Exception:  # noqa: BLE001
        cfg = {}
    if args.spawn:
        print(ensure_running(cfg))
        return 0
    if args.status or not (args.serve or args.once):
        print(status_text(cfg))
        return 0
    if not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN"):
        print("brak konfiguracji monitora (~/.claude/monitor.env)")
        return 0
    try:
        serve(cfg, once=args.once)
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        heartbeat.log("execute worker died: %r" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
