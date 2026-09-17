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

# The worker processes that call into this module (ask_worker.py, execute_worker.py) run
# DETACHED -- no console of their own. Every child that IS a console app (git, claude.CMD) then
# gets a brand new, visible console window, because Windows has nothing to inherit into. Found
# live (2026-09-17): a user watched "a bunch of black windows" flash on screen for one question.
# CREATE_NO_WINDOW is the standard fix; a no-op dict everywhere but Windows.
QUIET_SUBPROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

ALLOW_TOOLS = ["Read", "Glob", "Grep", "Bash(git log:*)", "Bash(git status:*)",
               "Bash(git show:*)", "Bash(git diff:*)"]
DENY_TOOLS = ["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
              "Bash(git push:*)", "Bash(git commit:*)", "Bash(git add:*)", "Task"]
ANSWER_MAX = 4000
SPEECH_MAX = 1100
DEFAULT_TIMEOUT_S = 180.0
PULL_TIMEOUT_S = 30.0

DEFAULT_SYSTEM = (
    "Jestes madrym, oczytanym rozmowca, ktory zna te projekty od srodka. Odpowiadasz po polsku, "
    "a Twoja odpowiedz jest CZYTANA NA GLOS -- piszesz wiec pelnymi, plynnymi zdaniami, tak jak "
    "mowi czlowiek, ktory rozumie temat i umie go wytlumaczyc. "
    "Masz opowiedziec tak, zeby zrozumial i programista, i ktos zupelnie spoza branzy: najpierw "
    "sens, potem szczegol. Termin fachowy mozesz uzyc, ale wtedy wyjasnij go w tym samym zdaniu. "
    "SENS PRZED STANEM: gdy pytanie brzmi czym jest projekt, co robi, do czego sluzy albo jakie ma "
    "glowne zadanie -- nie raportuj postepu prac. Przeczytaj README, dokumentacje i kod, zrozum, "
    "JAKI PROBLEM ten projekt rozwiazuje, DLA KOGO i JAK z grubsza dziala, i to opowiedz wlasnymi "
    "slowami. Opis ostatniego commita nie jest odpowiedzia na pytanie, czym jest projekt. "
    "POMIJAJ SZUM TECHNICZNY: zadnych skrotow commitow, nazw galezi, sciezek do plikow, nazw "
    "funkcji, numerow zadan ani procentow ukonczenia -- chyba ze pytanie dotyczy wprost wlasnie "
    "tego. Na glos takie rzeczy sa nie do sluchania. "
    "Odpowiadaj NA ZADANE PYTANIE. Jesli masz dostep do kilku repozytoriow (--add-dir), ustal, "
    "ktore sa istotne, i nazwij je po ludzku. "
    "DLUGOSC: jesli pytanie okresla dlugosc (np. 'w 2 zdaniach', 'krotko'), trzymaj sie jej scisle "
    "-- to ma pierwszenstwo przed kompletnoscia; inaczej od trzech do szesciu zdan, spojnym akapitem. "
    "Bez naglowkow, bez numerowanych punktow, bez list ani markdownu -- to ma byc mowa, nie notatka. "
    "Masz dostep TYLKO do odczytu: nie zmieniaj plikow, nie commituj, nie uruchamiaj testow. "
    "Jesli czegos nie da sie ustalic, powiedz to wprost jednym zdaniem zamiast zgadywac. "
    "Gdzie szukac: czym projekt jest -- README, dokumentacja, kod, openspec/changes/*/proposal.md; "
    "co sie w nim teraz dzieje -- notes/start.md, najnowsza notatka w notes/sesje/, "
    "openspec/STATUS.md, notes/HANDOFF_*.md."
)


# -- freshness (from Car) ----------------------------------------------------------------------

def _git(args, cwd, timeout):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt, nobody is watching
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env,
                          **QUIET_SUBPROCESS)


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


# -- state digest: hand the model what it would otherwise hunt for ------------------------------
# Both projects reached the same idea independently (ask-core 3.x here, poc-carplay-command 2.7 in
# the car bridge), so it lives here once. Built IN MEMORY on every call: reading four small files
# costs milliseconds, while a file written into the repo would need an allowlist entry, would get
# committed, and would be stale exactly when it matters.

DIGEST_MAX_BYTES = 4000
_SECTION_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def _read(path, limit=8000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _first_section(text):
    """The first '## ...' section of a document -- in notes/start.md that is 'Ostatnia sesja'."""
    if not text:
        return ""
    marks = [m.start() for m in _SECTION_RE.finditer(text)]
    if not marks:
        return text.strip()
    # skip a leading document title ('# START') and take the first real section, up to the next one
    start = marks[1] if (marks[0] == 0 and len(marks) > 1) else marks[0]
    nxt = [m for m in marks if m > start]
    return (text[start:nxt[0]] if nxt else text[start:]).strip()


def digest(path, max_bytes=DIGEST_MAX_BYTES, git_runner=None):
    """A compact state block for one repository, or '' when there is nothing to say.

    Sources are the ones a person would open first, in that order: the newest session summary,
    the generated OpenSpec status, the current change and its first open task, the last commits."""
    name = os.path.basename(str(path).rstrip("\\/"))
    parts = []
    head = _first_section(_read(os.path.join(path, "notes", "start.md")))
    if head:
        parts.append("Z notes/start.md:\n" + head)
    status = _read(os.path.join(path, "openspec", "STATUS.md"), 2000)
    line = next((ln for ln in status.splitlines() if "tasks complete" in ln or "Overall" in ln), "")
    if line:
        parts.append("Z openspec/STATUS.md: " + line.strip().lstrip("*# "))
    try:
        import taskparse  # the same parser the monitor uses -- never a second list
        ctx = taskparse.describe_repo(path)
        if ctx.get("change"):
            parts.append("Biezaca zmiana: %s (%s/%s zadan). Pierwsze otwarte: %s" % (
                ctx["change"], ctx.get("done"), ctx.get("total"), (ctx.get("task") or "-")[:200]))
    except Exception:
        pass
    g = git_runner or _git
    try:
        log = g(["log", "-3", "--format=%h %ad %s", "--date=short"], path, 10).stdout.strip()
        if log:
            parts.append("Ostatnie commity:\n" + log)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if not parts:
        return ""
    body = ("STAN REPOZYTORIUM %s (przygotowany przez most, nie szukaj tego w plikach):\n\n" % name
            + "\n\n".join(parts))
    return body[:max_bytes]


def digest_block(paths, max_bytes=DIGEST_MAX_BYTES, git_runner=None):
    """Digests of every repository, sharing the byte budget so one repo cannot eat the prompt."""
    paths = [p for p in (paths or []) if os.path.isdir(p)]
    if not paths:
        return ""
    each = max(500, max_bytes // len(paths))
    blocks = [d for d in (digest(p, each, git_runner) for p in paths) if d]
    return ("\n\n".join(blocks) + "\n\n") if blocks else ""


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
    """Strip what a loudspeaker cannot say: code blocks, markdown marks, then squeeze whitespace.

    Truncation cuts on a sentence boundary, never mid-word: a voice that stops in the middle of
    a word sounds broken, while a slightly shorter answer that ends on a period sounds finished.
    """
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"[*_`#>|\[\]]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= limit // 2:
        return head[:cut + 1]
    head = head[:limit - 3]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip(",;: ") + "..."


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
        context=None, allow=None, deny=None, env=None, use_digest=False):
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

    stdin_text = ((digest_block(paths) if use_digest else "")
                  + (context_block(context) if context else "")
                  + (question or ""))
    cmd = build_command(exe, paths, system, model, allow, deny)
    run = runner or subprocess.run
    # the headless run fires the repo's hooks too; heartbeat.py must not count it as an open window
    run_env = dict(env or os.environ, MONITOR_HEADLESS="1")
    try:
        out = run(cmd, input=stdin_text, cwd=paths[0], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=timeout, env=run_env,
                  **QUIET_SUBPROCESS)
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
