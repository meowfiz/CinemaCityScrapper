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
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heartbeat  # noqa: E402

POLL_S = 30.0
ANSWER_TIMEOUT_S = 180.0
READ_ONLY_TOOLS = "Read,Grep,Glob"
REPOS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "monitor_repos.env")
ANSWER_MAX = 4000


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


def answer_prompt(question):
    """The instruction the headless agent gets. Read-only is enforced by --allowed-tools as well;
    saying it here too keeps the answer honest instead of promising an edit it cannot make."""
    return (
        "Odpowiedz po polsku, zwiezle (maks. 6 zdan), na pytanie o stan tego repozytorium. "
        "Masz dostep TYLKO do odczytu: nie zmieniaj plikow, nie commituj, nie uruchamiaj testow. "
        "Jesli czegos nie da sie ustalic z plikow, napisz to wprost zamiast zgadywac. "
        "Zrodla w kolejnosci: notes/start.md, notes/sesje/, openspec/STATUS.md, "
        "openspec/changes/*/tasks.md.\n\nPytanie: " + question
    )


def run_ask(repo_path, question, timeout_s=ANSWER_TIMEOUT_S, runner=None):
    """(answer, error). Never raises: a failed answer is a reported error, not a dead worker."""
    exe = shutil.which("claude")
    if not exe:
        return None, "claude CLI not found on PATH"
    if not os.path.isdir(repo_path):
        return None, "repo path does not exist: %s" % repo_path
    cmd = [exe, "-p", answer_prompt(question), "--allowed-tools", READ_ONLY_TOOLS]
    # the headless run fires the repo's hooks too; this tells heartbeat.py not to count it as a window
    env = dict(os.environ, MONITOR_HEADLESS="1")
    try:
        run = runner or subprocess.run
        out = run(cmd, cwd=repo_path, capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return None, "timeout after %.0f s" % timeout_s
    except Exception as e:
        return None, "runner failed: %r" % (e,)
    if out.returncode != 0:
        return None, ("claude exited %d: %s" % (out.returncode, (out.stderr or "").strip()))[:1000]
    text = (out.stdout or "").strip()
    return (text[:ANSWER_MAX] if text else None), (None if text else "empty answer")


def handle(cfg, item, repo_map, runner=None):
    """Answer one claimed item and report the result. Returns the outcome string."""
    repo = item.get("repo")
    path = repo_map.get(repo)
    if item.get("kind") != "ask":
        api(cfg, "POST", "/api/inbox/%d/answer" % item["id"], {"error": "unsupported kind"})
        return "unsupported"
    if not path:
        api(cfg, "POST", "/api/inbox/%d/answer" % item["id"],
            {"error": "repo %s is not on this machine (see ~/.claude/monitor_repos.env)" % repo})
        return "unknown-repo"
    answer, error = run_ask(path, item.get("text") or "", _float(cfg, "MONITOR_ASK_TIMEOUT_S",
                                                                 ANSWER_TIMEOUT_S), runner)
    api(cfg, "POST", "/api/inbox/%d/answer" % item["id"],
        {"answer": answer} if answer else {"error": error or "no answer"})
    return "answered" if answer else "failed"


def serve(cfg, once=False, sleep=time.sleep, runner=None):
    poll_s = _float(cfg, "MONITOR_ASK_POLL_S", POLL_S)
    handled = 0
    while True:
        repo_map = load_repo_map()  # re-read each pass: a repo can be added without a restart
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
    args, _ = ap.parse_known_args(argv)

    cfg = heartbeat.load_config()
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
    n = serve(cfg, once=args.once or not args.serve)
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
