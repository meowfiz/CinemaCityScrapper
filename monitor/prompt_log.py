#!/usr/bin/env python3
"""prompt_log.py -- the prompt ledger: one JSON line per prompt and per model call (opsx slad-promptow).

Day file: ~/.claude/prompts/YYYY-MM-DD.jsonl (override: PROMPT_LOG_DIR), per machine, never in a repo.
Two writers share it: monitor/transcript_ledger.py (everything Claude Code writes to ~/.claude/projects)
and direct entries from the places that leave no transcript (log_prompt / log_call below).

Record schema (v 1) is in openspec/changes/slad-promptow/design.md D3; the `call` fields are the
contract with monitor/usage_report.py (aplikacja-zuzycie design 0.1).

log_prompt() / log_call() never raise (a logging failure must not cost a voice answer). Under pytest
(PYTEST_CURRENT_TEST) they write nothing unless `path=` is given.

Viewer: python monitor/prompt_log.py --day YYYY-MM-DD [--proc X] [--grep Y]
"""
import argparse
import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

SCHEMA = 1
KINDS = ("okno", "subagent", "cli-p", "api")
SOURCES = ("okno", "desktop", "ask_worker", "execute_worker", "context_refresh", "voice_brain", "glos",
           "diacritics", "command_decide", "ear_summary", "humanize", "phonetize", "style_flags",
           "vcc_assistant", "ksiazka", "router", "codex")
KIND_ALIASES = {"desktop": "okno", "brain": "cli-p", "codex": "api", "cli_p": "cli-p"}

MAX_PROMPT = 20000          # chars kept of a prompt (D5)
CLIP_HEAD = 16000
CLIP_TAIL = 2000
MAX_META = 2000             # chars kept of CLI-injected text (skills, background notifications)
MAX_LINE = 64 * 1024        # bytes of one journal line (D5, D9)
DAY_BUDGET = 200 * 1024 * 1024   # bytes of one day file after which prompts are written without text (D10)
SECRET = "[SEKRET]"

_SECRET_RES = [
    (re.compile(r"(?i)Authorization:[ \t]*(?!\[SEKRET\])[^\r\n]*"), "Authorization: " + SECRET),
    (re.compile(r"Bearer\s+(?!\[SEKRET\])[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + SECRET),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"), SECRET),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), SECRET),
    (re.compile(r"(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}"), SECRET),
    (re.compile(r"xox[abp]-[A-Za-z0-9-]{10,}"), SECRET),
    (re.compile(r"AKIA[0-9A-Z]{16}"), SECRET),
]
_ASSIGN_RE = re.compile(r"(?i)(password|passwd|haslo|has\u0142o|token|secret|api[_-]?key|MONITOR_TOKEN)"
                        r"([\"']?[ \t]*[=:][ \t]*[\"']?)(?!\[SEKRET\])([^\s\"',;]+)")
_APPPASS_RE = re.compile(r"\b[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}\b")
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

_MACHINE_SECRETS = None


# -- pure helpers -----------------------------------------------------------------------------

def read_env_file(path):
    """{KEY: value} from a KEY=VALUE file; missing or unreadable file -> {}."""
    out = {}
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("\"'")
    return out


def secret_values(claude_dir=None):
    """Exact secret values of this machine: keys with TOKEN|SECRET|PASS|KEY in their name in
    ~/.claude/*.env, value length >= 8 (D4 rule 5). Read from disk, never stored."""
    base = claude_dir or os.path.join(os.path.expanduser("~"), ".claude")
    vals = set()
    try:
        names = [n for n in os.listdir(base) if n.endswith(".env")]
    except OSError:
        return ()
    for name in names:
        for key, val in read_env_file(os.path.join(base, name)).items():
            if re.search(r"TOKEN|SECRET|PASS|KEY", key.upper()) and len(val) >= 8:
                vals.add(val)
    return tuple(sorted(vals, key=len, reverse=True))


def machine_secrets():
    global _MACHINE_SECRETS
    if _MACHINE_SECRETS is None:
        try:
            _MACHINE_SECRETS = secret_values()
        except Exception:  # noqa: BLE001
            _MACHINE_SECRETS = ()
    return _MACHINE_SECRETS


def redact(text, secrets=None):
    """(text, n): secrets replaced by [SEKRET], n = number of replacements (D4). Pure.
    `secrets`: exact values to remove; default: the ones of this machine."""
    text = str(text or "")
    n = 0
    for val in (machine_secrets() if secrets is None else secrets):
        if val and val in text:
            n += text.count(val)
            text = text.replace(val, SECRET)
    for rx, repl in _SECRET_RES:
        text, k = rx.subn(repl, text)
        n += k
    text, k = _ASSIGN_RE.subn(lambda m: m.group(1) + m.group(2) + SECRET, text)
    n += k
    text, k = _APPPASS_RE.subn(SECRET, text)
    n += k
    return text, n


def strip_reminders(text):
    """(text without <system-reminder> blocks, number of chars removed). Pure."""
    text = str(text or "")
    out = _REMINDER_RE.sub("", text)
    return out.strip(), len(text) - len(out)


def sha_of(text):
    return hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest()


def clip(text, limit=MAX_PROMPT):
    """(kept, cut, chars, sha): `sha` and `chars` describe the full text; `cut` is False or "limit" (D5)."""
    text = str(text or "")
    chars, sha = len(text), sha_of(text)
    if chars <= limit:
        return text, False, chars, sha
    if limit >= MAX_PROMPT:
        head, tail = CLIP_HEAD, CLIP_TAIL
    else:
        tail = max(0, limit // 10)
        head = max(0, limit - tail - 60)       # 60: room for the marker, so the kept text stays within `limit`
    marker = "\n[... uciete %d znakow, sha256 w polu sha ...]\n" % (chars - head - tail)
    return text[:head] + marker + (text[chars - tail:] if tail else ""), "limit", chars, sha


def local_iso(dt, tz=None):
    """ISO time with offset, in `tz` (default: this machine's zone). Naive input is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).isoformat(timespec="seconds")


def day_of(ts):
    """'YYYY-MM-DD' of an ISO stamp as written (its own local date, not the collection day)."""
    return str(ts)[:10]


def machine_name(env_path=None):
    cfg = read_env_file(env_path or os.path.join(os.path.expanduser("~"), ".claude", "monitor.env"))
    return cfg.get("MONITOR_MACHINE") or platform.node()


_PROJECT_CACHE = {}


def project_of(cwd):
    """Repo name for a working directory: git root (a worktree .claude/worktrees/<x> maps to its parent
    repo); outside a repo the directory name; no cwd -> None."""
    if not cwd:
        return None
    key = str(cwd)
    if key in _PROJECT_CACHE:
        return _PROJECT_CACHE[key]
    norm = key.replace("\\", "/").rstrip("/")
    name = None
    m = re.search(r"^(.*?)/\.claude/worktrees/[^/]+", norm)
    if m:
        norm = m.group(1)
        name = os.path.basename(norm) or None
    if name is None:
        cur = norm
        for _ in range(40):
            if os.path.exists(cur + "/.git"):
                name = os.path.basename(cur)
                break
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
    if not name:
        name = os.path.basename(norm) or norm or None
    if len(_PROJECT_CACHE) > 2000:
        _PROJECT_CACHE.clear()
    _PROJECT_CACHE[key] = name
    return name


def normalize_kind(kind):
    kind = KIND_ALIASES.get(kind, kind)
    return kind if kind in KINDS else "cli-p"


SOURCE_ALIASES = {"phonetize-teacher": "phonetize", "voice_fallback": "glos", "voice_fallback_web": "glos"}


def normalize_source(source):
    source = SOURCE_ALIASES.get(source, source)
    return source if source in SOURCES else None


def needs_call_record(argv):
    """D1: a direct entry writes a `call` record only when the call leaves no transcript: argv has
    --no-session-persistence, or it is not the Claude CLI at all (API, warm brain)."""
    args = [str(a) for a in (argv or [])]
    if "--no-session-persistence" in args:
        return True
    return not any(os.path.basename(a).lower().replace(".exe", "").replace(".cmd", "") == "claude"
                   for a in args[:1])


def direct_id(prefix, ts, source, sha, pid=None):
    raw = "%s|%s|%s|%s|%s" % (prefix, ts, source, sha, os.getpid() if pid is None else pid)
    return "d:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def proc_name(kind, project, source, agent_type=None):
    """'okno:<project>', 'subagent:<project>:<type>', 'cli-p:<project>:<source>', 'api:<source>' (D3)."""
    if kind == "okno":
        return ("desktop:" if source == "desktop" else "okno:") + str(project)
    if kind == "subagent":
        return "subagent:%s:%s" % (project, agent_type or "?")
    if kind == "cli-p":
        return "cli-p:%s:%s" % (project, source or "?")
    return "api:%s" % (source or "?")


def make_prompt(sub, text, ts, machine, project, kind, source, origin, session=None, agent_id=None,
                model=None, persisted=None, prompt_id=None, proc=None, rec_id=None, secrets=None,
                agent_type=None, extra_chars=0):
    """The `prompt` record (D3). Pure: `ts` is passed in; redaction runs before the sha is taken."""
    kind = normalize_kind(kind)
    source = normalize_source(source)
    body, red = redact(str(text or "").strip(), secrets)
    body = body.strip()
    kept, cut, chars, sha = clip(body, MAX_META if sub == "meta" else MAX_PROMPT)
    rec = {
        "v": SCHEMA, "type": "prompt", "id": rec_id or direct_id("p", ts, source, sha),
        "ts": ts, "machine": machine, "project": project, "kind": kind,
        "proc": proc or proc_name(kind, project, source, agent_type), "source": source,
        "origin": origin, "session": session, "agent_id": agent_id, "model": model,
        "sub": sub, "prompt": kept, "chars": chars, "sha": sha, "cut": cut, "redacted": red,
        "extra_chars": int(extra_chars or 0), "prompt_id": prompt_id,
    }
    if persisted is not None:
        rec["persisted"] = bool(persisted)
    return rec


def _int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def usage_fields(usage):
    """(out, in, cc, cr, cc_1h, known) from an Anthropic- or OpenAI-style usage dict; unknown -> zeros."""
    if not isinstance(usage, dict) or not usage:
        return 0, 0, 0, 0, 0, False
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    cc = usage.get("cache_creation_input_tokens")
    cr = usage.get("cache_read_input_tokens")
    cc1 = (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") \
        if isinstance(usage.get("cache_creation"), dict) else 0
    known = any(isinstance(v, (int, float)) for v in (out, inp, cc, cr))
    return _int(out), _int(inp), _int(cc), _int(cr), _int(cc1), known


def make_call(call_id, ts, machine, project, kind, source, origin, model, usage=None, session=None,
              agent_id=None, query_source=None, prompt_id=None, status="ok", dur_ms=None, proc=None,
              agent_type=None, tokens_known=None):
    """The `call` record (D3, contract with usage_report). One record per API call. Pure."""
    kind = normalize_kind(kind)
    source = normalize_source(source)
    out, inp, cc, cr, cc1, known = usage_fields(usage)
    if tokens_known is not None:
        known = bool(tokens_known) and known
    rec = {
        "v": SCHEMA, "type": "call", "id": call_id, "call_id": call_id, "ts": ts, "machine": machine,
        "project": project, "kind": kind, "proc": proc or proc_name(kind, project, source, agent_type),
        "source": source, "origin": origin, "session": session, "agent_id": agent_id, "model": model,
        "query_source": query_source, "prompt_id": prompt_id, "out": out, "in": inp, "cc": cc, "cr": cr,
        "cc_1h": cc1, "tokens_known": known, "status": status,
    }
    if dur_ms is not None:
        rec["dur_ms"] = _int(dur_ms)
    return rec


# -- writing ----------------------------------------------------------------------------------

def log_dir():
    return os.environ.get("PROMPT_LOG_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "prompts")


def day_path(day, directory=None):
    return os.path.join(directory or log_dir(), day + ".jsonl")


def encode_line(rec):
    """One journal line as bytes, at most MAX_LINE: an oversized prompt is cut further (never dropped)."""
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    while len(line) > MAX_LINE and rec.get("prompt"):
        keep = max(0, len(rec["prompt"]) // 2 - 100) if len(rec["prompt"]) > 200 else 0
        rec = dict(rec, prompt=rec["prompt"][:keep], cut="limit")
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > MAX_LINE:
        rec = {k: v for k, v in rec.items() if k != "prompt"}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    return line


def append_record(path, rec):
    """Append one record: ONE unbuffered binary write of the whole line, so two processes appending
    at once cannot tear it (model_usage tore a line with a text-mode write, 2026-10-16)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > DAY_BUDGET and rec.get("type") == "prompt" and rec.get("prompt"):
        rec = dict(rec, prompt="", cut="budget")
    line = encode_line(rec)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.name == "nt" and _append_nt(path, line):
        return
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _append_nt(path, line):
    """Windows: the C runtime emulates O_APPEND with seek-then-write, which two processes race on (measured:
    596 of 600 lines survived). A handle opened with FILE_APPEND_DATA ONLY makes NTFS append every write at
    the end of the file atomically. Returns False when that is not possible (caller falls back)."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        # FILE_APPEND_DATA, share read|write|delete, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL
        handle = k32.CreateFileW(os.path.abspath(path), 0x0004, 0x7, None, 4, 0x80, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return False
        try:
            written = wintypes.DWORD(0)
            ok = k32.WriteFile(handle, line, len(line), ctypes.byref(written), None)
            return bool(ok) and written.value == len(line)
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def _target(rec, path):
    return path or day_path(day_of(rec["ts"]))


def log_prompt(model, prompt, source, project=None, kind="cli-p", session=None, persisted=False,
               path=None, proc=None, cwd=None, now=None, machine=None):
    """Direct `prompt` entry for a call the transcript collector cannot attribute (or cannot see).
    Returns the record id, or None when nothing was written. Never raises."""
    try:
        if path is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        now = now or datetime.now(timezone.utc)
        if project is None:
            project = project_of(cwd or os.getcwd())
        rec = make_prompt("direct", prompt, local_iso(now), machine or machine_name(), project, kind, source, "direct",
                          session=session, model=model, persisted=persisted, proc=proc)
        append_record(_target(rec, path), rec)
        return rec["id"]
    except Exception:  # noqa: BLE001
        return None


def log_call(model, source, project=None, kind="cli-p", session=None, usage=None, status="ok",
             dur_ms=None, persisted=False, path=None, proc=None, cwd=None, now=None, tokens_known=None,
             machine=None):
    """Direct `call` entry (tokens from `usage` when the caller has them). A call whose transcript the
    collector reads (`persisted=True`) writes nothing, so it is never counted twice (D1). Never raises."""
    try:
        if persisted or (path is None and os.environ.get("PYTEST_CURRENT_TEST")):
            return None
        now = now or datetime.now(timezone.utc)
        if project is None:
            project = project_of(cwd or os.getcwd())
        ts = local_iso(now)
        rec = make_call(direct_id("c", ts, source, "", None), ts, machine or machine_name(), project, kind, source,
                        "direct", model, usage=usage, session=session, status=status, dur_ms=dur_ms,
                        proc=proc, tokens_known=tokens_known)
        append_record(_target(rec, path), rec)
        return rec["id"]
    except Exception:  # noqa: BLE001
        return None


# -- viewer -----------------------------------------------------------------------------------

def read_day(day, directory=None):
    """(records, bad_lines) of a day file; unparsable lines are skipped and counted."""
    rows, bad = [], 0
    try:
        with open(day_path(day, directory), "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    bad += 1
                    continue
                if isinstance(rec, dict):
                    rows.append(rec)
    except OSError:
        pass
    return rows, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--proc", help="substring of the process name")
    ap.add_argument("--grep", help="substring of the prompt text")
    ap.add_argument("--calls", action="store_true", help="show call records instead of prompts")
    args = ap.parse_args(argv)
    rows, bad = read_day(args.day)
    want = "call" if args.calls else "prompt"
    shown = 0
    for r in rows:
        if r.get("type") != want:
            continue
        if args.proc and args.proc not in str(r.get("proc")):
            continue
        if args.grep and args.grep.lower() not in str(r.get("prompt", "")).lower():
            continue
        shown += 1
        if want == "prompt":
            text = " ".join(str(r.get("prompt", "")).split())[:160]
            line = "%s %-28s %-8s %-18s %s" % (str(r.get("ts"))[11:19], str(r.get("proc"))[:28], r.get("sub"),
                                               str(r.get("model"))[:18], text)
        else:
            line = "%s %-28s %-18s out=%s in=%s cc=%s cr=%s %s" % (
                str(r.get("ts"))[11:19], str(r.get("proc"))[:28], str(r.get("model"))[:18],
                r.get("out"), r.get("in"), r.get("cc"), r.get("cr"), r.get("status"))
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.write(("%d %s, %d bad lines\n" % (shown, want, bad)).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
