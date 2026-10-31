#!/usr/bin/env python3
"""transcript_ledger.py -- collector of the prompt ledger (opsx slad-promptow, design D6-D12, D16).

Reads the Claude Code transcripts (~/.claude/projects/**/*.jsonl) incrementally and appends two kinds
of records to the day files of monitor/prompt_log.py (~/.claude/prompts/YYYY-MM-DD.jsonl):
  prompt -- what was typed / injected (text redacted and clipped),
  call   -- one record per model API call with the token counts (the contract with usage_report).

    python monitor/transcript_ledger.py --once               one pass
    python monitor/transcript_ledger.py --loop 120           service loop (monitor/services.py `ledger`)
    python monitor/transcript_ledger.py --once --backfill-days 30   first start only: read N days back

Parsing rules (all measured, see design D6): tool_result lines are not prompts; prompts typed during a
turn exist only as attachment/queued_command; one answer is several `assistant` lines with the same
message.id, so ONE call per api-request id (or per message.id for CLI < 2.1.302, which has no
api-request records). api-request-blob lines (megabytes) are skipped without parsing.

Standard library only. ASCII only.
"""
import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prompt_log as pl  # noqa: E402

PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")
LOCK_PATH = os.path.join(tempfile.gettempdir(), "monitor_ledger.lock")
MAX_READ = 64 * 1024 * 1024      # bytes per file per pass (D8)
IDLE_S = 15 * 60                 # a file quiet this long: pending prompts/calls are written as they are
RETENTION_DAYS = 90
LOG_MAX = 1024 * 1024
HEAD_BYTES = 4096
MATCH_BEFORE_S, MATCH_AFTER_S = 120, 600   # D7: prompt time minus direct entry time in [-120, +600]
MAIN_QUERY = ("repl_main_thread", "sdk")
SKIP_PREFIXES = ("<local-command-", "[Request interrupted")

_PARSE_RE = re.compile(rb'"type"\s*:\s*"(?:user|assistant|api-request)"|queued_command')


# -- pure parsing -----------------------------------------------------------------------------

def classify_line(raw):
    """True when a transcript line must be parsed (D6 byte filter). api-request-blob lines carry
    megabytes and are never parsed."""
    if raw.startswith(b'{"type":"api-request-blob"') or raw.startswith(b'{"type":"file-history'):
        return False
    return _PARSE_RE.search(raw) is not None


def parse_ts(s):
    """aware datetime from a transcript timestamp ('...Z' = UTC); None when unreadable."""
    try:
        s = str(s)
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def agent_type_of(query_source, attribution=None):
    """'agent:builtin:Plan' -> 'Plan'; plain 'agent' -> the attributionAgent name or '?'."""
    qs = str(query_source or "")
    if qs.startswith("agent:"):
        return qs.rsplit(":", 1)[-1] or "?"
    return attribution or "?"


def kind_proc(entrypoint, query_source, in_subagent_file, project, cli_source=None, attribution=None):
    """(kind, source, proc, agent_type) by the D6 table. The four kinds are the contract with usage_report:
    Claude Desktop is `okno` with source `desktop`."""
    qs = str(query_source or "")
    atype = None
    if entrypoint == "sdk-cli" or qs == "sdk":
        kind, source = "cli-p", cli_source
    elif entrypoint == "claude-desktop":
        kind, source = "okno", "desktop"
    elif in_subagent_file or qs.startswith("agent"):
        kind, source = "subagent", "okno"
        atype = agent_type_of(qs, attribution)
    else:
        kind, source = "okno", "okno"
    return kind, source, pl.proc_name(kind, project, source, atype), atype


def message_text(content):
    """(text, has_tool_result) of a transcript message.content (str or list of parts)."""
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    texts, tool = [], False
    for part in content:
        if not isinstance(part, dict):
            continue
        t = part.get("type")
        if t == "tool_result":
            tool = True
        elif t == "text":
            texts.append(str(part.get("text") or ""))
        elif t == "image":
            texts.append("[obraz]")
    return "\n".join(texts), tool


def command_text(text):
    """'<command-name>/model</command-name>...<command-args>x</command-args>' -> '/model x'."""
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    out = (name.group(1).strip() if name else text.strip())
    if args and args.group(1).strip():
        out += " " + args.group(1).strip()
    return out


class FileTracker:
    """State machine for ONE transcript file: feed() takes parsed records in file order and returns the
    records that are ready for the journal; the rest waits (pending) for a model name or a final usage."""

    RECENT = 500

    def __init__(self, rel, machine, tz=None, secrets=None, matcher=None):
        self.rel = rel.replace("\\", "/")
        self.machine, self.tz, self.secrets, self.matcher = machine, tz, secrets, matcher
        self.is_sub = "/subagents/" in self.rel
        m = re.search(r"agent-([^/]+)\.jsonl$", self.rel) if self.is_sub else None
        self.file_agent = m.group(1) if m else None
        self.project = None
        self.entrypoint = None
        self.session = None
        self.attribution = None
        self.last_prompt_id = None
        self.cli_source = None
        self.match_tries = 0
        self.has_api = False
        self.pprompts = []
        self.pcalls = {}
        self.recent = []

    # -- state ----------------------------------------------------------------------------------
    KEYS = ("project", "entrypoint", "session", "attribution", "last_prompt_id", "cli_source",
            "match_tries", "has_api", "pprompts", "pcalls", "recent")

    def dump(self):
        d = {k: getattr(self, k) for k in self.KEYS}
        d["recent"] = self.recent[-20:]
        return d

    def load(self, d):
        for k in self.KEYS:
            if k in d:
                setattr(self, k, d[k])
        return self

    # -- helpers --------------------------------------------------------------------------------
    def _iso(self, raw):
        dt = parse_ts(raw)
        return pl.local_iso(dt, self.tz) if dt else None

    def _learn(self, rec):
        cwd = rec.get("cwd")
        if cwd:
            self.project = pl.project_of(cwd)
        if rec.get("entrypoint"):
            self.entrypoint = rec["entrypoint"]
        if rec.get("sessionId"):
            self.session = rec["sessionId"]
        if rec.get("attributionAgent"):
            self.attribution = rec["attributionAgent"]

    def _agent_id(self, rec):
        return self.file_agent or rec.get("agentId")

    def _remember(self, cid):
        self.recent.append(cid)
        if len(self.recent) > self.RECENT:
            del self.recent[:len(self.recent) - self.RECENT]

    # -- prompts --------------------------------------------------------------------------------
    def _draft(self, sub, text, rid, ts_raw, rec):
        text, extra = pl.strip_reminders(text)
        ts = self._iso(ts_raw)
        if not text or not ts:
            return None
        qs = rec.get("querySource")
        kind, source, proc, atype = kind_proc(self.entrypoint, qs, self.is_sub, self.project,
                                              self.cli_source, self.attribution)
        pr = pl.make_prompt(sub, text, ts, self.machine, self.project, kind, source, "transcript",
                            session=self.session, agent_id=self._agent_id(rec), prompt_id=rec.get("promptId"),
                            rec_id=rid, secrets=self.secrets, proc=proc, extra_chars=extra)
        if kind == "cli-p" and self.cli_source is None and self.matcher and self.match_tries < 3 \
                and sub in ("user", "queued"):
            self.match_tries += 1
            dt = parse_ts(ts_raw)
            src = self.matcher(pr["sha"], dt.timestamp()) if dt else None
            if src:
                self.cli_source = src
                pr["source"] = src
                pr["proc"] = pl.proc_name("cli-p", self.project, src)
        return pr

    def prompts_from(self, rec):
        """Prompt records for one transcript record (D6 table); model is filled in later."""
        t = rec.get("type")
        if t == "attachment":
            att = rec.get("attachment")
            if not isinstance(att, dict) or att.get("type") != "queued_command" \
                    or att.get("commandMode") != "prompt" or self.is_sub:
                return []
            text, _ = message_text(att.get("prompt"))
            pr = self._draft("queued", text, "q:%s" % rec.get("uuid"), rec.get("timestamp") or att.get("timestamp"), rec)
            return [pr] if pr else []
        if t != "user" or "toolUseResult" in rec:
            return []
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return []
        text, has_tool = message_text(msg.get("content"))
        if has_tool or not text.strip() or text.startswith(SKIP_PREFIXES):
            return []
        sidechain = bool(rec.get("isSidechain"))
        if rec.get("isCompactSummary"):
            sub = "compact"
        elif rec.get("isMeta"):
            sub = "meta"
        elif self.is_sub or sidechain:
            if rec.get("parentUuid") is None:
                sub = "agent"
            elif self.is_sub:
                sub = "continue"
            else:
                return []
        elif text.lstrip().startswith("<command-name>"):
            sub, text = "command", command_text(text)
        else:
            sub = "user"
        pr = self._draft(sub, text, "p:%s" % rec.get("uuid"), rec.get("timestamp"), rec)
        return [pr] if pr else []

    def _resolve_prompts(self, model, query_source=None):
        ready = []
        for pr in self.pprompts:
            pr["model"] = model
            if pr["kind"] == "subagent" and pr["proc"].endswith(":?") and str(query_source).startswith("agent:"):
                pr["proc"] = pr["proc"][:-1] + agent_type_of(query_source)   # the type is known only from the request
            ready.append(pr)
        self.pprompts = []
        return ready

    # -- calls ----------------------------------------------------------------------------------
    def _finish(self, c, status=None):
        usage = c.get("usage")
        if status is None:
            status = "ok" if (usage and c.get("stop")) else ("partial" if usage else "no_response")
        qs = c.get("qs")
        kind, source, proc, atype = kind_proc(c.get("entrypoint"), qs, self.is_sub, c.get("project"),
                                              self.cli_source, self.attribution)
        rec = pl.make_call(c["id"], c["ts"], self.machine, c.get("project"), kind, source, "transcript",
                           c.get("model"), usage=usage, session=self.session, agent_id=c.get("agent_id"),
                           query_source=qs, prompt_id=c.get("prompt_id"), status=status, proc=proc,
                           tokens_known=usage is not None)
        self._remember(c["id"])
        return rec

    def _close_same_key(self, key, keep_id):
        out = []
        # Only a call that already has a usage is closed here. One whose answer has not been read yet stays:
        # Claude Code writes the next api-request line BEFORE the late assistant lines of the previous call
        # (measured 30.10: 5 of 85 calls of one project written as no_response with zero tokens, 1.1 percent of
        # its weight). A call that never gets an answer is written as no_response by flush_idle.
        for cid in [k for k, c in self.pcalls.items() if c.get("key") == key and k != keep_id and c.get("usage")]:
            out.append(self._finish(self.pcalls.pop(cid)))
        return out

    def _on_api_request(self, rec):
        rid = rec.get("id")
        if not rid:
            return []
        cid = "c:%s" % rid
        self.has_api = True
        if cid in self.pcalls or cid in self.recent:
            return []
        params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
        qs = rec.get("querySource")
        agent_id = self._agent_id(rec)
        key = [qs, agent_id]
        out = self._close_same_key(key, cid)
        if self.pprompts and (qs in MAIN_QUERY or str(qs).startswith("agent:")):
            out += self._resolve_prompts(params.get("model"), qs)
        self.pcalls[cid] = {"id": cid, "ts": self._iso(rec.get("timestamp")), "model": params.get("model"),
                            "qs": qs, "agent_id": agent_id, "key": key, "entrypoint": self.entrypoint,
                            "project": self.project, "prompt_id": self.last_prompt_id,
                            "usage": None, "stop": False}
        return out

    def _on_assistant(self, rec):
        msg = rec.get("message")
        if not isinstance(msg, dict) or msg.get("model") == "<synthetic>" or rec.get("isApiErrorMessage"):
            return []
        out = []
        if self.pprompts and not self.has_api:
            out += self._resolve_prompts(msg.get("model"))
        usage = msg.get("usage")
        if not isinstance(usage, dict) or not (msg.get("id") or rec.get("requestRef")):
            return out
        ref = rec.get("requestRef")
        cid = ("c:%s" % ref) if ref else ("m:%s" % msg["id"])
        if cid in self.recent:
            return out
        c = self.pcalls.get(cid)
        if c is None:
            sidechain = bool(rec.get("isSidechain")) or self.is_sub
            qs = "agent" if sidechain else "repl_main_thread"
            agent_id = self._agent_id(rec)
            c = {"id": cid, "ts": self._iso(rec.get("timestamp")), "model": msg.get("model"), "qs": qs,
                 "agent_id": agent_id, "key": [qs, agent_id], "entrypoint": self.entrypoint,
                 "project": self.project, "prompt_id": self.last_prompt_id, "usage": None, "stop": False}
            self.pcalls[cid] = c
        stop = msg.get("stop_reason") is not None
        if stop or not c["stop"]:      # a record with stop_reason is the final one; before it, the latest wins
            c["usage"] = usage
            c["stop"] = c["stop"] or stop
        if msg.get("model"):
            c["model"] = msg["model"]
        if c["stop"]:
            out.append(self._finish(self.pcalls.pop(cid)))
        return out

    # -- feed -----------------------------------------------------------------------------------
    def feed(self, rec):
        t = rec.get("type")
        if t in ("user", "assistant", "attachment"):
            self._learn(rec)
        if t in ("user", "attachment"):
            if t == "user" and rec.get("promptId") and not self.is_sub:
                self.last_prompt_id = rec["promptId"]
            drafts = self.prompts_from(rec)
            self.pprompts.extend(drafts)
            return []
        if t == "api-request":
            return self._on_api_request(rec)
        if t == "assistant":
            return self._on_assistant(rec)
        return []

    def flush_idle(self):
        """The file has been quiet for IDLE_S: write what waits (prompt without model, calls without an
        answer as no_response, calls without a final record as partial)."""
        out = self._resolve_prompts(None)
        for cid in list(self.pcalls):
            out.append(self._finish(self.pcalls.pop(cid)))
        return out


def iter_records(data):
    """Parsed records of a block of complete transcript lines; (records, bad_lines)."""
    recs, bad = [], 0
    for raw in data.split(b"\n"):
        raw = raw.strip()
        if not raw or not classify_line(raw):
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    return recs, bad


def read_block(path, off, limit=MAX_READ):
    """(complete_lines_bytes, consumed): only whole lines, so a half-written last line waits for the
    next pass (D8). A single line longer than `limit` is skipped instead of blocking the file forever."""
    with open(path, "rb") as fh:
        fh.seek(off)
        data = fh.read(limit)
    cut = data.rfind(b"\n")
    if cut < 0:
        return (b"", len(data)) if len(data) >= limit else (b"", 0)
    return data[:cut + 1], cut + 1


# -- direct-entry index (D7) ------------------------------------------------------------------

class DirectIndex:
    """sha -> [(epoch, source)] of persisted direct prompt entries, to attribute `claude -p` sessions."""

    def __init__(self, directory):
        self.dir = directory
        self.pos = {}
        self.by_sha = {}

    def _refresh(self, days):
        for day in days:
            path = pl.day_path(day, self.dir)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = self.pos.get(path, 0)
            if size <= off:
                continue
            with open(path, "rb") as fh:
                fh.seek(off)
                block = fh.read(size - off)
            cut = block.rfind(b"\n") + 1
            self.pos[path] = off + cut
            for raw in block[:cut].split(b"\n"):
                if b'"direct"' not in raw:
                    continue
                try:
                    r = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if r.get("origin") == "direct" and r.get("type") == "prompt" and r.get("persisted"):
                    dt = parse_ts(r.get("ts"))
                    if dt:
                        self.by_sha.setdefault(r.get("sha"), []).append((dt.timestamp(), r.get("source")))

    def lookup(self, sha, epoch):
        """Source of the nearest direct entry with this sha such that
        -120 s <= prompt time - entry time <= 600 s, else None."""
        days = {datetime.fromtimestamp(epoch + d).astimezone().strftime("%Y-%m-%d") for d in (-86400, 0, 86400)}
        self._refresh(sorted(days))
        best = None
        for t, src in self.by_sha.get(sha, ()):
            delta = epoch - t
            if -MATCH_BEFORE_S <= delta <= MATCH_AFTER_S and (best is None or abs(delta) < best[0]):
                best = (abs(delta), src)
        return best[1] if best else None


# -- the collector ----------------------------------------------------------------------------

class Ledger:
    def __init__(self, projects_dir=None, out_dir=None, machine=None, tz=None, now=None, secrets=None,
                 backfill_days=7, max_read=MAX_READ, idle_s=IDLE_S, retention_days=RETENTION_DAYS):
        self.projects_dir = projects_dir or PROJECTS_DIR
        self.out_dir = out_dir or pl.log_dir()
        self.state_path = os.path.join(self.out_dir, ".state.json")
        self.log_path = os.path.join(self.out_dir, "ledger.log")
        self.machine = machine or pl.machine_name()
        self.tz, self.now, self.secrets = tz, now or time.time, secrets
        self.backfill_days, self.max_read, self.idle_s = backfill_days, max_read, idle_s
        self.retention_days = retention_days
        self.index = DirectIndex(self.out_dir)
        self.seen = {}
        self.state = self._load_state()
        self.first_run = not self.state.get("files") and not os.path.exists(self.state_path)
        self.bad_lines = 0

    # -- state / log ----------------------------------------------------------------------------
    def _load_state(self):
        try:
            with open(self.state_path, "rb") as fh:
                st = json.loads(fh.read().decode("utf-8"))
            if isinstance(st, dict) and st.get("v") == 1:
                return st
        except (OSError, ValueError):
            pass
        return {"v": 1, "files": {}, "pending": {}}

    def save_state(self):
        os.makedirs(self.out_dir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(self.state, ensure_ascii=True).encode("ascii"))
        os.replace(tmp, self.state_path)

    def log(self, msg):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > LOG_MAX:
                os.replace(self.log_path, self.log_path + ".1")
            line = "%s %s\n" % (datetime.now().astimezone().isoformat(timespec="seconds"), msg)
            with open(self.log_path, "ab") as fh:
                fh.write(line.encode("utf-8", "replace"))
        except OSError:
            pass

    # -- journal --------------------------------------------------------------------------------
    def _seen_ids(self, day):
        if day not in self.seen:
            ids = set()
            rows, _bad = pl.read_day(day, self.out_dir)
            for r in rows:
                if r.get("id"):
                    ids.add(r["id"])
            self.seen[day] = ids
        return self.seen[day]

    def preload_recent_days(self, days=3):
        for i in range(days):
            self._seen_ids(datetime.fromtimestamp(self.now() - i * 86400).astimezone(self.tz).strftime("%Y-%m-%d"))

    def write_records(self, recs, changed):
        """Append records to the day file of their own `ts`; an id already in the journal is skipped."""
        written = 0
        for rec in recs:
            day = pl.day_of(rec["ts"])
            ids = self._seen_ids(day)
            if rec["id"] in ids:
                continue
            pl.append_record(pl.day_path(day, self.out_dir), rec)
            ids.add(rec["id"])
            changed.add(day)
            written += 1
        return written

    # -- scanning -------------------------------------------------------------------------------
    def scan(self):
        found = []
        stack = [self.projects_dir]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.name.endswith(".jsonl"):
                            try:
                                stt = e.stat()
                            except OSError:
                                continue
                            found.append((os.path.relpath(e.path, self.projects_dir).replace("\\", "/"),
                                          e.path, stt.st_size, stt.st_mtime))
            except OSError:
                continue
        return found

    def _head(self, path, n):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read(n)).hexdigest()

    def _tracker(self, rel):
        tr = FileTracker(rel, self.machine, self.tz, self.secrets, self.index.lookup)
        pend = self.state["pending"].get(rel)
        if pend:
            tr.load(pend)
        return tr

    def _process_file(self, rel, path, size, mtime, recs_out):
        st = self.state["files"].get(rel)
        now = self.now()
        if st is None:
            if self.first_run and mtime < now - self.backfill_days * 86400:
                self.state["files"][rel] = {"off": size, "size": size, "mtime": mtime,
                                            "hl": min(size, HEAD_BYTES), "head": self._head(path, min(size, HEAD_BYTES))}
                return True
            st = {"off": 0, "size": 0, "mtime": 0, "hl": 0, "head": ""}
        changed = st.get("size") != size or st.get("mtime") != mtime or st.get("off", 0) < size
        tr = None
        if changed:
            rewritten = size < st.get("off", 0) or bool(st.get("hl") and self._head(path, st["hl"]) != st.get("head"))
            if rewritten:
                st["off"] = 0
                self.state["pending"].pop(rel, None)
            tr = self._tracker(rel)
            data, used = read_block(path, st["off"], self.max_read)
            recs, bad = iter_records(data)
            self.bad_lines += bad
            for rec in recs:
                recs_out.extend(tr.feed(rec))
            st["off"] += used
            st["hl"] = min(size, HEAD_BYTES)
            st["head"] = self._head(path, st["hl"])
        st["size"], st["mtime"] = size, mtime
        waiting = tr is not None and (tr.pprompts or tr.pcalls)
        if tr is None:
            pend = self.state["pending"].get(rel) or {}
            waiting = bool(pend.get("pprompts") or pend.get("pcalls"))
        if waiting and now - mtime >= self.idle_s:
            tr = tr or self._tracker(rel)
            recs_out.extend(tr.flush_idle())
        if tr is not None:
            d = tr.dump()
            if not (tr.pprompts or tr.pcalls):
                d["recent"] = []
            self.state["pending"][rel] = d
        self.state["files"][rel] = st
        return changed

    def retention(self, today):
        """Delete day files older than RETENTION_DAYS, once a day."""
        if self.state.get("retention_day") == today:
            return 0
        self.state["retention_day"] = today
        removed = 0
        limit = self.now() - self.retention_days * 86400
        try:
            for name in os.listdir(self.out_dir):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", name):
                    p = os.path.join(self.out_dir, name)
                    if os.path.getmtime(p) < limit:
                        os.remove(p)
                        removed += 1
        except OSError:
            pass
        if removed:
            self.log("retention: removed %d day files older than %d days" % (removed, self.retention_days))
        return removed

    def run_once(self, tick=True):
        """One pass over all transcripts. Returns {'files', 'written', 'days', 'bad'}."""
        t0 = time.process_time()
        if not self.seen:
            self.preload_recent_days()
        recs, changed_files = [], 0
        self.bad_lines = 0
        for rel, path, size, mtime in self.scan():
            try:
                if self._process_file(rel, path, size, mtime, recs):
                    changed_files += 1
            except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the pass
                self.log("ERROR %s: %r" % (rel, exc))
        changed = set()
        written = self.write_records(recs, changed)
        today = datetime.fromtimestamp(self.now()).astimezone(self.tz).strftime("%Y-%m-%d")
        self.retention(today)
        self.save_state()
        self.first_run = False
        stats = {"files": changed_files, "written": written, "days": sorted(changed), "bad": self.bad_lines,
                 "cpu_s": round(time.process_time() - t0, 3)}
        if written or self.bad_lines:
            self.log("pass: %d files changed, %d records written, days %s, bad lines %d, cpu %.2f s"
                     % (changed_files, written, ",".join(stats["days"]), self.bad_lines, stats["cpu_s"]))
        if tick:
            self.tick_report(stats["days"])
        return stats

    def tick_report(self, days):
        """usage_report.tick(changed_days) -- the other half of the contract (aplikacja-zuzycie). An error there
        never stops the collector; before that module exists the ImportError is ignored."""
        try:
            usage_report = importlib.import_module("usage_report")   # a sibling of task B; absent until it lands
        except ImportError:
            return
        except Exception as exc:  # noqa: BLE001
            self.log("usage_report import failed: %r" % (exc,))
            return
        try:
            usage_report.tick(days)
        except Exception as exc:  # noqa: BLE001
            self.log("usage_report.tick failed: %r" % (exc,))


# -- lock and loop ----------------------------------------------------------------------------

def touch_lock(now, path=LOCK_PATH):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "at": now, "machine": pl.machine_name()}, fh)
    except OSError:
        pass


def other_instance_live(now, poll_s, path=LOCK_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("pid") != os.getpid() and now - float(data.get("at") or 0) < poll_s * 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", type=float, metavar="SECONDS")
    ap.add_argument("--backfill-days", type=int, default=7, help="first start only (default 7)")
    args = ap.parse_args(argv)
    if args.loop and other_instance_live(time.time(), max(args.loop, 30) + 30):
        return 0
    led = Ledger(backfill_days=args.backfill_days)
    if args.once:
        stats = led.run_once()
        sys.stdout.write(json.dumps(stats) + "\n")
        return 0
    led.log("start: loop %.0f s, projects %s, out %s" % (args.loop, led.projects_dir, led.out_dir))
    cpu = []
    try:
        while True:
            touch_lock(time.time())
            stats = led.run_once()
            touch_lock(time.time())
            cpu.append(stats["cpu_s"])
            if len(cpu) >= 30:
                cpu.sort()
                led.log("30 passes: median cpu %.3f s, max %.3f s" % (cpu[len(cpu) // 2], cpu[-1]))
                cpu = []
            time.sleep(args.loop)
    finally:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
