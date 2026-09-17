#!/usr/bin/env python3
"""
ask_worker.py -- answers questions sent from the phone (change remote-tasks).

    python monitor/ask_worker.py --serve       # poll the monitor inbox until stopped
    python monitor/ask_worker.py --once        # take at most one item and answer it (tests, cron)
    python monitor/ask_worker.py --list        # show the repo map this machine can answer for

The loop: claim an item from the monitor's inbox -> run a READ-ONLY headless agent in that repo
(`claude -p` with Read/Grep/Glob only) -> post the answer back. The server then notifies the phone.

Why a pull loop and not SSH from Home Assistant: this machine already talks OUTBOUND to the monitor
(the same token as heartbeat.py), so nothing new has to listen, no key has to be installed, and it
works on Windows, where the inotify/sshd design does not exist. It also survives sleep: on waking
the worker simply polls again.

Only 'ask' is implemented. Starting work that WRITES is refused by the server on purpose, until the
commit/push gates of rules 2.7-2.9 cover something typed on a phone.

Repo map (never in the repo -- paths differ per machine): ~/.claude/monitor_repos.env
    RibnXtr2026=D:\\programming\\RibnXtr2026
    project_integration=D:\\claude_projects\\project_integration

Configuration is shared with heartbeat.py (~/.claude/monitor.env):
    MONITOR_URL, MONITOR_TOKEN, MONITOR_MACHINE
    MONITOR_ASK_POLL_S    seconds between polls (default 30)
    MONITOR_ASK_TIMEOUT_S how long one answer may take (default 180)

Standard library only. ASCII only. Never raises out of main().
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_core  # noqa: E402
import capabilities  # noqa: E402
import heartbeat  # noqa: E402
import live_sessions  # noqa: E402

POLL_S = 30.0
ANSWER_TIMEOUT_S = 180.0
# change project-orchestrator, phase A: the server counts three missed beats as death,
# so the beat must not be able to hold the loop for longer than one poll.
BEAT_TIMEOUT_S = 10.0
WORKER_VERSION = "1.1"
READ_ONLY_TOOLS = "Read,Grep,Glob"
REPOS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "monitor_repos.env")
ANSWER_MAX = 4000


# -- one worker per machine, started by whichever Claude Code window comes first -------------------
# The SessionStart hook calls `ask_worker.py --spawn`. The first call on a machine starts the loop
# detached; every later call finds a live lock and returns at once. A worker that dies stops
# touching the lock, so after LOCK_STALE_POLLS polls the next window starts a fresh one.

LOCK_STALE_POLLS = 3


def lock_path():
    return os.path.join(tempfile.gettempdir(), "monitor_ask_worker.lock")


def lock_is_live(path, now, poll_s, read=None):
    """Pure apart from the injected reader: is another worker alive on this machine?"""
    try:
        raw = read(path) if read else open(path, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(data, dict) or data.get("pid") == os.getpid():
        return False
    return (now - float(data.get("at") or 0)) < poll_s * LOCK_STALE_POLLS


def touch_lock(now):
    try:
        with open(lock_path(), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": now, "machine": platform.node()}, f)
    except OSError:
        pass


def drop_lock():
    try:
        os.remove(lock_path())
    except OSError:
        pass


def spawn_detached():
    args = [sys.executable, os.path.abspath(__file__), "--serve"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": os.path.expanduser("~")}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def lock_pid(path=None):
    try:
        data = json.loads(open(path or lock_path(), encoding="utf-8").read())
        return int(data.get("pid") or 0) or None
    except (OSError, ValueError, TypeError):
        return None


def restart(cfg, kill=os.kill):
    """A running worker keeps the code it started with; after a pack update it must be replaced.
    Terminate the pid from the lock, drop the lock, start a fresh one. Returns the ensure_running outcome."""
    pid = lock_pid()
    if pid and pid != os.getpid():
        try:
            kill(pid, 15)  # SIGTERM; TerminateProcess on Windows
        except OSError:
            pass  # already gone
    drop_lock()
    return ensure_running(cfg)


def ensure_running(cfg, now=None):
    """Start the worker unless one is alive. Returns 'running' | 'spawned' | 'no-config'."""
    if not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN"):
        return "no-config"
    if lock_is_live(lock_path(), time.time() if now is None else now, _float(cfg, "MONITOR_ASK_POLL_S", POLL_S)):
        return "running"
    spawn_detached()
    return "spawned"


def register_repo(name, path, map_path=None):
    """Add repo=path to the machine's map unless present. Called by the SessionStart hook, so a repo
    becomes answerable on a machine the first time a window is opened in it -- no manual list."""
    target = map_path or os.environ.get("MONITOR_REPOS_FILE") or REPOS_FILE
    if not name or not path or not os.path.isdir(path):
        return False
    current = load_repo_map(target)
    if current.get(name) == path:
        return False
    lines = []
    try:
        with open(target, encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError:
        pass
    lines = [ln for ln in lines if not ln.strip().startswith(name + "=")]
    lines.append("%s=%s" % (name, path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).strip("\n") + "\n")
    return True


def load_repo_map(path=None):
    """{repo name: absolute path} for the repos this machine can answer about."""
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


def machine_name(cfg):
    return cfg.get("MONITOR_MACHINE") or platform.node()


def _float(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def api(cfg, method, path, body=None, timeout=20.0):
    url = cfg["MONITOR_URL"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["MONITOR_TOKEN"],
        "User-Agent": "monitor-ask-worker/1",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def claim(cfg, limit=1):
    return api(cfg, "POST", "/api/inbox/claim?machine=%s&limit=%d" % (
        urllib.parse.quote(machine_name(cfg)), limit)).get("items", [])


def beat_payload(cfg, repo_map, caps=None, running=0):
    """What this worker tells the server about itself (change project-orchestrator, phase A).

    Until now the server learned this worker exists only when an answer arrived, so a machine
    with no open Claude Code window was invisible even while the worker ran on it.
    """
    paths = [p for p in (repo_map or {}).values() if p]
    return {
        "machine": machine_name(cfg),
        "kind": "ask",
        "hostname": platform.node(),
        "os": platform.system(),
        "version": WORKER_VERSION,
        "project_ids": sorted((repo_map or {}).keys()),
        "capabilities": list(caps) if caps is not None else capabilities.detect(paths),
        "capacity": 1,          # one headless agent at a time; questions are answered in turn
        "running": int(running),
    }


def send_beat(cfg, repo_map, caps=None, running=0):
    """Never raises: a server that is down must not stop a worker from answering questions."""
    try:
        api(cfg, "POST", "/api/workers/heartbeat", beat_payload(cfg, repo_map, caps, running),
            timeout=BEAT_TIMEOUT_S)
        return True
    except Exception as e:  # noqa: BLE001
        heartbeat.log("ask worker heartbeat failed: %r" % (e,))
        return False


def send_windows(cfg, lister=None):
    """Report which Claude Code windows are open here (stream C), alongside the heartbeat.

    Deliberately a separate call from the beat: asking the CLI costs a subprocess and can be
    slow, and a worker that cannot enumerate windows must still be registered as alive. Never
    raises, for the same reason send_beat does not.
    """
    try:
        windows = (lister or live_sessions.list_windows)()
        api(cfg, "POST", "/api/windows",
            {"machine": machine_name(cfg), "windows": windows}, timeout=BEAT_TIMEOUT_S)
        return len(windows)
    except Exception as e:  # noqa: BLE001
        heartbeat.log("ask worker windows report failed: %r" % (e,))
        return 0


def system_prompt():
    """Instructions for the headless agent. The phone wording is the core's default; the car bridge
    passes its own (three sentences, no markdown) -- that is a product difference, not a technical one."""
    return ask_core.DEFAULT_SYSTEM


def answer_prompt(question):
    """Kept for callers and tests: the full instruction + question as one text."""
    return system_prompt() + "\n\nPytanie: " + question


# re-exported so the worker's own tests and callers keep one import (the logic lives in ask_core)
sentence_limit = ask_core.sentence_limit
cut_sentences = ask_core.cut_sentences


_SENTENCE_LIMIT_RE = re.compile(
    r"\b(?:w|do|max\.?|maks\.?|maksymalnie)\s+(\d{1,2})\s+zdani(?:a|ach|u)\b|\bjednym zdaniem\b|\bw jednym zdaniu\b",
    re.IGNORECASE)
# Polish capitals as escapes: the pack is ASCII-only (rule 4.1)
_SENTENCE_END_RE = re.compile(
    "(?<=[.!?])\\s+(?=[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b0-9`*_\"'(\\[])")


def sentence_limit(question):
    """The number of sentences the question asks for, or None. Deterministic, so the promise made
    in the panel ('w 2 zdaniach') is kept even when the model does not keep it (panel #5, 2026-09-16)."""
    m = _SENTENCE_LIMIT_RE.search(question or "")
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else 1


def cut_sentences(text, n):
    """First n sentences of the answer; a split never happens inside a code span or a number."""
    if not text or not n or n <= 0:
        return text
    parts = _SENTENCE_END_RE.split(text.strip())
    return " ".join(parts[:n]).strip() if len(parts) > n else text.strip()


ALL_REPOS = "*"  # the phone asks across every repo this machine has, not one named repo


def run_ask(repo_path, question, timeout_s=ANSWER_TIMEOUT_S, runner=None, extra_paths=(),
            refresh=True, use_digest=True):
    """(answer, error) for one queued question. A thin client of ask_core (change ask-core): the
    call itself, the freshness pull and the sentence limit live there, shared with the car bridge.

    refresh=True is the Car contribution the monitor lacked: without it an answer can be based on a
    checkout from days ago and nothing says so.

    use_digest=True by measurement, not by hope: the pre-registered run of 2026-09-16 (10 questions,
    interleaved) moved the median from 18,4 s to 12,3 s (-33%), 9 of 10 pairs faster, concrete facts
    kept in 10/10 answers. It missed the declared 40%, so it is a partial result, and the tail barely
    moved (p90 26,1 -> 22,9 s) -- the queue path takes it because six seconds of median is worth
    having; the car path decides on its own numbers."""
    paths = [repo_path] + [p for p in extra_paths if p and p != repo_path]
    context = ask_core.refresh_all(paths) if refresh else None
    return ask_core.ask(paths, question, system=system_prompt(), timeout=timeout_s,
                        runner=runner, context=context, use_digest=use_digest)


def resolve_paths(repo, repo_map):
    """(cwd, extra_paths) for one item. ALL_REPOS spans every repo this machine has; the newest
    path first, so the agent's working directory is the repo most likely to be asked about."""
    if repo == ALL_REPOS:
        paths = [p for p in repo_map.values() if os.path.isdir(p)]
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return (paths[0], paths[1:]) if paths else (None, [])
    return repo_map.get(repo), []


def handle(cfg, item, repo_map, runner=None):
    """Answer one claimed item and report the result. Returns the outcome string."""
    repo = item.get("repo")
    if item.get("kind") != "ask":
        api(cfg, "POST", "/api/inbox/%d/answer" % item["id"], {"error": "unsupported kind"})
        return "unsupported"
    path, extra = resolve_paths(repo, repo_map)
    if not path:
        api(cfg, "POST", "/api/inbox/%d/answer" % item["id"],
            {"error": ("brak repozytoriow na tej maszynie (~/.claude/monitor_repos.env)" if repo == ALL_REPOS
                       else "repo %s is not on this machine (see ~/.claude/monitor_repos.env)" % repo)})
        return "unknown-repo"
    answer, error = run_ask(path, item.get("text") or "", _float(cfg, "MONITOR_ASK_TIMEOUT_S",
                                                                ANSWER_TIMEOUT_S), runner, extra)
    api(cfg, "POST", "/api/inbox/%d/answer" % item["id"],
        {"answer": answer} if answer else {"error": error or "no answer"})
    return "answered" if answer else "failed"


def serve(cfg, once=False, sleep=time.sleep, runner=None):
    poll_s = _float(cfg, "MONITOR_ASK_POLL_S", POLL_S)
    handled = 0
    if not once and lock_is_live(lock_path(), time.time(), poll_s):
        return 0  # another worker owns this machine
    heartbeat.log("ask worker started pid=%d" % os.getpid())
    # Capabilities are probed once per process, not per poll: 'docker info' costs seconds and the
    # answer does not change while the worker runs (design D7).
    caps = None
    while True:
        if not once:
            touch_lock(time.time())  # a stale lock is how the next window knows to restart us
        repo_map = load_repo_map()  # re-read each pass: a repo can be added without a restart
        if caps is None:
            caps = capabilities.detect([p for p in repo_map.values() if p])
        send_beat(cfg, repo_map, caps)  # same loop as the claim: no second thread, no second timer
        send_windows(cfg)               # which Claude Code windows are open here (stream C)
        try:
            items = claim(cfg)
        except Exception as e:
            heartbeat.log("ask worker claim failed: %r" % (e,))
            items = []
        for item in items:
            try:
                heartbeat.log("ask worker: %s #%s" % (item.get("repo"), item.get("id")))
                handle(cfg, item, repo_map, runner)
                handled += 1
            except Exception as e:  # one bad item must not stop the worker
                heartbeat.log("ask worker item failed: %r" % (e,))
        if once:
            return handled
        sleep(poll_s if not items else 1.0)  # drain a burst quickly, then go back to sleep


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--spawn", action="store_true", help="start detached unless one is running")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--restart", action="store_true", help="replace the running worker (after a pack update)")
    args, _ = ap.parse_known_args(argv)

    cfg = heartbeat.load_config()
    if args.restart:
        print(restart(cfg))
        return 0
    if args.status:
        live = lock_is_live(lock_path(), time.time(), _float(cfg, "MONITOR_ASK_POLL_S", POLL_S))
        print("worker: %s (%s)" % ("dziala" if live else "nie dziala", lock_path()))
        return 0
    if args.spawn:
        print(ensure_running(cfg))
        return 0
    if args.list:
        repo_map = load_repo_map()
        print("machine: %s" % machine_name(cfg))
        print("repos (%s):" % (REPOS_FILE if os.path.exists(REPOS_FILE) else "BRAK PLIKU"))
        for name, path in sorted(repo_map.items()):
            print("  %-28s %s %s" % (name, path, "" if os.path.isdir(path) else "(brak katalogu)"))
        return 0
    if not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN"):
        print("MONITOR_URL / MONITOR_TOKEN missing in ~/.claude/monitor.env")
        return 0
    try:
        n = serve(cfg, once=args.once or not args.serve)
    finally:
        if args.serve:
            drop_lock()
    print("obsluzone: %d" % n)
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        heartbeat.log("ask worker unexpected: %r" % (e,))
    sys.exit(0)
