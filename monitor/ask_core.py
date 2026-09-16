#!/usr/bin/env python3
"""
ask_core.py -- the shared "ask the repositories" core (change ask-core).

Two projects had built this twice: Car_chatGPT_integration (voice in the car: Whisper -> claude -p
-> speech) and project_integration (phone / Home Assistant: inbox -> claude -p -> notification).
Each had a better half, and this file is the union:

  from Car     refresh_repo / refresh_all  -- pull --ff-only only on a clean tree, age spoken out
               loud, so an answer is never silently based on a week-old checkout;
               DENY_TOOLS next to ALLOW_TOOLS; for_speech
  from monitor question on STDIN, instructions as ONE line in --append-system-prompt
               (on Windows `claude` is claude.CMD and cmd.exe truncates an argument at the first
               newline -- on 2026-09-16 that silently dropped every question for half a day, and
               the model answered by summarising the repo, which looked plausible)

Callers keep what makes them different: the voice bridge passes its driver system prompt and speaks
the result, the worker passes the queue's question and posts the answer back.

    from ask_core import ask, refresh_all, for_speech
    fresh = refresh_all(paths)                       # optional, costs one parallel pull
    answer, error = ask(paths, "co zostalo w JK?", system=MY_PROMPT, context=fresh)

Standard library only. ASCII only. No function here raises for an expected failure: a failed answer
is (None, "reason"), because both callers must keep running.
"""

import os
import re
import shutil
import subprocess
import threading
from datetime import datetime

ALLOW_TOOLS = ["Read", "Glob", "Grep", "Bash(git log:*)", "Bash(git status:*)",
               "Bash(git show:*)", "Bash(git diff:*)"]
DENY_TOOLS = ["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
              "Bash(git push:*)", "Bash(git commit:*)", "Bash(git add:*)", "Task"]
ANSWER_MAX = 4000
SPEECH_MAX = 700
DEFAULT_TIMEOUT_S = 180.0
PULL_TIMEOUT_S = 30.0

DEFAULT_SYSTEM = (
    "Jestes asystentem stanu repozytoriow; uzytkownik pyta z telefonu albo z auta, odpowiadaj po polsku. "
    "Odpowiadaj NA ZADANE PYTANIE, nie streszczaj repozytorium, chyba ze o to prosi. "
    "Jesli masz dostep do kilku repozytoriow (--add-dir), pytanie moze dotyczyc dowolnego z nich albo "
    "wszystkich naraz -- ustal, ktore sa istotne, i nazwij je w odpowiedzi. "
    "DLUGOSC: jesli pytanie okresla dlugosc (np. 'w 2 zdaniach', 'jednym zdaniem', 'krotko'), trzymaj "
    "sie jej scisle -- to ma pierwszenstwo przed kompletnoscia; inaczej maks. 6 zdan. "
    "Bez naglowka, bez listy zrodel, bez numerowanych punktow, chyba ze pytanie o nie prosi. "
    "Masz dostep TYLKO do odczytu: nie zmieniaj plikow, nie commituj, nie uruchamiaj testow. "
    "Jesli czegos nie da sie ustalic z plikow, napisz to wprost zamiast zgadywac. "
    "Zrodla w kolejnosci: notes/start.md, najnowsza notatka w notes/sesje/, openspec/STATUS.md, "
    "openspec/changes/*/tasks.md, notes/HANDOFF_*.md."
)


# -- freshness (from Car) ----------------------------------------------------------------------

def _git(args, cwd, timeout):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt, nobody is watching
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def age_phrase(iso, now=None):
    """'2026-09-15 11:02:03 +0200' -> 'X min/h/dni temu'. Spoken, so never an exact stamp."""
    try:
        when = datetime.strptime(str(iso).strip()[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError, TypeError):
        return "nieznany czas"
    minutes = max(0, int(((now or datetime.now()) - when).total_seconds() // 60))
    if minutes < 60:
        return "%d min temu" % minutes
    if minutes < 60 * 48:
        return "%d h temu" % (minutes // 60)
    return "%d dni temu" % (minutes // 1440)


def refresh_repo(path, timeout=PULL_TIMEOUT_S, runner=None):
    """Fast-forward one repository if that is safe, then report what we have.

    Never rebases, never pushes, never touches a dirty tree (rules 2.3 / 2.7): the answering
    machine is a reader. A repo it cannot reach still answers -- from the local state, with the age
    said out loud, which is the honest version of 'nie wiem, czy to aktualne'."""
    g = runner or _git
    name = os.path.basename(str(path).rstrip("\\/"))
    try:
        before = g(["rev-parse", "--short", "HEAD"], path, 10).stdout.strip()
        if g(["status", "--porcelain"], path, 15).stdout.strip():
            state = "lokalne zmiany, nie odswiezam"
        else:
            pull = g(["pull", "--ff-only"], path, timeout)
            after = g(["rev-parse", "--short", "HEAD"], path, 10).stdout.strip()
            if pull.returncode != 0:
                state = "NIE odswiezone (brak polaczenia albo rozjazd z remote)"
            elif before == after:
                state = "bez zmian na remote"
            else:
                state = "podciagniete z remote"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "%s: NIE odswiezone (%s)" % (name, type(exc).__name__)
    try:
        head = g(["log", "-1", "--format=%h|%ci|%s"], path, 10).stdout.strip()
        sha, when, subject = head.split("|", 2)
        return "%s: %s, ostatni commit %s %s (%s)" % (name, state, sha, age_phrase(when), subject[:60])
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return "%s: %s" % (name, state)


def refresh_all(paths, timeout=PULL_TIMEOUT_S, runner=None):
    """Every repository in parallel -- the wall clock is one pull, not their sum (Car: 1,95 s / 4)."""
    paths = [p for p in (paths or []) if os.path.isdir(p)]
    if not paths:
        return []
    results = [None] * len(paths)

    def one(i, p):
        results[i] = refresh_repo(p, timeout, runner)

    threads = [threading.Thread(target=one, args=(i, p), daemon=True) for i, p in enumerate(paths)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 5)
    return [r for r in results if r]


def context_block(lines):
    """Freshness report prepended to the question, so the model can say 'odpowiadam ze stanu sprzed...'."""
    if not lines:
        return ""
    return ("STAN DANYCH sprawdzony przed ta odpowiedzia (kazde repozytorium osobno):\n"
            + "\n".join("- " + ln for ln in lines)
            + "\nJesli repozytorium jest oznaczone jako NIE odswiezone, a pytanie go dotyczy, "
              "powiedz w jednym zdaniu, ze odpowiadasz ze stanu sprzed podanego czasu.\n\n"
              "PYTANIE: ")


# -- length asked for in the question (from monitor) -------------------------------------------

_SENTENCE_LIMIT_RE = re.compile(
    r"\b(?:w|do|max\.?|maks\.?|maksymalnie)\s+(\d{1,2})\s+zdani(?:a|ach|u)\b|\bjednym zdaniem\b|\bw jednym zdaniu\b",
    re.IGNORECASE)
_SENTENCE_END_RE = re.compile(
    "(?<=[.!?])\\s+(?=[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b0-9`*_\"'(\\[])")


def sentence_limit(question):
    """How many sentences the question asks for, or None. Deterministic, because a promise made in
    the interface ('w 2 zdaniach') has to hold even when the model does not keep it."""
    m = _SENTENCE_LIMIT_RE.search(question or "")
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else 1


def cut_sentences(text, n):
    if not text or not n or n <= 0:
        return text
    parts = _SENTENCE_END_RE.split(text.strip())
    return " ".join(parts[:n]).strip() if len(parts) > n else text.strip()


def for_speech(text, limit=SPEECH_MAX):
    """Strip what a loudspeaker cannot say: code blocks, markdown marks, then squeeze whitespace."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"[*_`#>|\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


# -- the call ------------------------------------------------------------------------------------

def build_command(exe, paths, system, model=None, allow=None, deny=None):
    """argv for `claude -p`. NO element may contain a newline: on Windows `claude` is claude.CMD and
    cmd.exe truncates an argument at the first one. The question never travels here -- it goes on
    stdin -- which is exactly the defect of 2026-09-16 that this function exists to make impossible."""
    cmd = [exe, "-p", "--output-format", "text",
           "--append-system-prompt", " ".join((system or DEFAULT_SYSTEM).split())]
    if model:
        cmd += ["--model", model]
    cmd += ["--allowedTools"] + list(allow or ALLOW_TOOLS)
    cmd += ["--disallowedTools"] + list(deny or DENY_TOOLS)
    for path in list(paths or [])[1:]:
        if os.path.isdir(path):
            cmd += ["--add-dir", path]
    return cmd


def ask(paths, question, system=None, model=None, timeout=DEFAULT_TIMEOUT_S, runner=None,
        context=None, allow=None, deny=None, env=None):
    """(answer, error). Never raises: a failed answer is a reported error, not a dead caller.

    paths[0] is the working directory, the rest are handed over with --add-dir, so one question can
    span every repository the machine has. `context` is the freshness report from refresh_all()."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return None, "brak repozytoriow do przeszukania"
    if not os.path.isdir(paths[0]):
        return None, "repo path does not exist: %s" % paths[0]
    exe = shutil.which("claude")
    if not exe:
        return None, "claude CLI not found on PATH"

    stdin_text = (context_block(context) if context else "") + (question or "")
    cmd = build_command(exe, paths, system, model, allow, deny)
    run = runner or subprocess.run
    # the headless run fires the repo's hooks too; heartbeat.py must not count it as an open window
    run_env = dict(env or os.environ, MONITOR_HEADLESS="1")
    try:
        out = run(cmd, input=stdin_text, cwd=paths[0], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=timeout, env=run_env)
    except subprocess.TimeoutExpired:
        return None, "timeout after %.0f s" % timeout
    except Exception as exc:
        return None, "runner failed: %r" % (exc,)
    if out.returncode != 0:
        return None, ("claude exited %d: %s" % (out.returncode, (out.stderr or "").strip()))[:1000]
    text = (out.stdout or "").strip()
    if not text:
        return None, "empty answer"
    limit = sentence_limit(question)
    if limit:
        text = cut_sentences(text, limit)
    return text[:ANSWER_MAX], None
