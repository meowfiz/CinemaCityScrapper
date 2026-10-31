#!/usr/bin/env python3
"""usage_report.py -- the machine side of the "Zuzycie" app (opsx aplikacja-zuzycie, design sections 0-1).

Reads today's day file of the prompt ledger (~/.claude/prompts/YYYY-MM-DD.jsonl, written by
monitor/transcript_ledger.py), sums the `call` records into 10-minute buckets x process x model and POSTs the
WHOLE day to the monitor server (POST /api/usage/report). A second report of the same day replaces the first,
so a restart never doubles numbers. Prompt texts never leave the machine: the report has no field for them.

Besides tokens the report carries the machine's own load (user decision 2026-10-31: tokens + machine + CPU +
GPU): `sys` rows with the mean and the peak of CPU and GPU utilisation per 10-minute bucket, sampled by a small
daemon thread (every SAMPLE_S) from GetSystemTimes / /proc/stat and from ~/.claude/gpu_state.json (gpu_watch).

Called from the `ledger` service loop: usage_report.tick(changed_days) after every collector pass.

Standard library only, ASCII only (ZASADY 4.1). Decisions are pure functions (ZASADY 4.5).
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prompt_log as pl  # noqa: E402

SCHEMA = 1
BUCKET_MIN = 10
MIN_GAP_S = 120          # one day is sent at most this often
KEEPALIVE_S = 300        # today is sent at least this often (the server calls a machine offline after 15 min)
BACKOFF_S = 60           # silence after a failed send
CATCHUP_DAYS = 7         # after a start: today and this many days back, when the file is newer than the sent state
SAMPLE_S = 15.0
SYS_KEEP_DAYS = 9
GPU_FRESH_S = 30.0
MAX_STR = 200
TOKEN_FIELDS = ("out", "in", "cc", "cr")

_STATE = {"started": False, "sent": None, "last_try": {}, "last_ok": {}, "backoff_until": 0.0,
          "sampler": None, "sys": None}
_LOCK = threading.Lock()


# -- pure ---------------------------------------------------------------------------------------

def process_of(rec):
    """'okno:<project>', 'subagent:<project>', 'desktop:<project>', 'cli-p:<source>', 'api:<source>'. Pure."""
    kind = str(rec.get("kind") or "?")
    project = str(rec.get("project") or "?")
    source = str(rec.get("source") or "?")
    if kind == "okno" and source == "desktop":
        return "desktop:" + project
    if kind in ("okno", "subagent"):
        return "%s:%s" % (kind, project)
    return "%s:%s" % (kind, source)


def parse_iso(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def bucket_iso(dt):
    """Start of the 10-minute bucket of an aware datetime, as '2026-10-31T14:20+01:00'. Pure."""
    minute = (dt.minute // BUCKET_MIN) * BUCKET_MIN
    return dt.replace(minute=minute, second=0, microsecond=0).isoformat(timespec="minutes")


def _count(rec, key):
    v = rec.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None
    return v


def summarize(records, day, stats=None):
    """Rows (bucket x process x model) of one day from ledger records. Pure.

    Only `type == "call"`; one record per `call_id` (a second one with the same id is a duplicate of the
    same answer, design 0.3); a record with a negative or non-integer count is skipped and counted in
    stats["skipped"]; a record without known tokens (tokens_known false) counts as a call with zeros and
    in `unk`. stats["dups"] counts dropped duplicates."""
    stats = stats if stats is not None else {}
    stats.setdefault("skipped", 0)
    stats.setdefault("dups", 0)
    seen, acc = set(), {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "call":
            continue
        ts = str(rec.get("ts") or "")
        dt = parse_iso(ts)
        if dt is None or dt.tzinfo is None or ts[:10] != day:
            stats["skipped"] += 1
            continue
        cid = rec.get("call_id") or rec.get("id")
        if cid is not None:
            if cid in seen:
                stats["dups"] += 1
                continue
            seen.add(cid)
        nums = [_count(rec, k) for k in TOKEN_FIELDS]
        if any(n is None for n in nums):
            stats["skipped"] += 1
            continue
        proc = process_of(rec)
        model = str(rec.get("model") or "?")[:MAX_STR]
        key = (bucket_iso(dt), proc, model)
        row = acc.get(key)
        if row is None:
            row = acc[key] = {"t": key[0], "process": proc[:MAX_STR], "kind": str(rec.get("kind") or "?")[:MAX_STR],
                              "project": (str(rec["project"])[:MAX_STR] if rec.get("project") else None),
                              "source": str(rec.get("source") or "?")[:MAX_STR], "model": model,
                              "calls": 0, "unk": 0, "out": 0, "in": 0, "cc": 0, "cr": 0, "last_ts": ts}
        row["calls"] += 1
        if rec.get("tokens_known") is False:
            row["unk"] += 1
        for k, n in zip(TOKEN_FIELDS, nums):
            row[k] += n
        if ts > row["last_ts"]:
            row["last_ts"] = ts
    return sorted(acc.values(), key=lambda r: (r["t"], r["process"], r["model"]))


def cpu_pct(prev, cur):
    """Utilisation in % from two (idle, total) counters; None when the counters did not advance. Pure."""
    if not prev or not cur:
        return None
    d_total, d_idle = cur[1] - prev[1], cur[0] - prev[0]
    if d_total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))


def gpu_from_state(state, now_s):
    """Mean utilisation of the samples in gpu_state.json, or None when the file is stale (older than
    GPU_FRESH_S), absent or has no samples. Pure."""
    if not isinstance(state, dict):
        return None
    at = state.get("at")
    if not isinstance(at, (int, float)) or now_s - at > GPU_FRESH_S:
        return None
    vals = []
    for s in state.get("samples") or []:
        try:
            vals.append(float(s[1]))
        except (TypeError, ValueError, IndexError):
            pass
    if not vals and isinstance(state.get("util"), (int, float)):
        vals = [float(state["util"])]
    return sum(vals) / len(vals) if vals else None


def add_sample(buckets, bucket, cpu, gpu):
    """Fold one sample into {bucket: [cpu_sum, cpu_n, cpu_max, gpu_sum, gpu_n, gpu_max]}. Pure (mutates `buckets`)."""
    b = buckets.setdefault(bucket, [0.0, 0, 0.0, 0.0, 0, 0.0])
    if cpu is not None:
        b[0] += cpu
        b[1] += 1
        b[2] = max(b[2], cpu)
    if gpu is not None:
        b[3] += gpu
        b[4] += 1
        b[5] = max(b[5], gpu)
    return buckets


def sys_rows(buckets):
    """[{t, cpu, cpu_max, gpu, gpu_max, n}] sorted by bucket; cpu/gpu null where nothing was sampled. Pure."""
    out = []
    for t in sorted(buckets):
        b = buckets[t]
        out.append({"t": t, "cpu": round(b[0] / b[1], 1) if b[1] else None, "cpu_max": round(b[2], 1) if b[1] else None,
                    "gpu": round(b[3] / b[4], 1) if b[4] else None, "gpu_max": round(b[5], 1) if b[4] else None,
                    "n": max(b[1], b[4])})
    return out


def due_days(now, changed, stat_now, sent, last_try, first):
    """Which days to send now. `stat_now` {day: [mtime, size]} of existing files, `sent` the sent state,
    `last_try` {day: epoch}. A day is due when its file differs from the sent state (and MIN_GAP_S passed since
    the last try), or - for today - KEEPALIVE_S passed. On the first call yesterday and CATCHUP_DAYS back
    are included. Pure; `now` is an aware datetime."""
    today = now.strftime("%Y-%m-%d")
    cands = {today, (now - timedelta(days=1)).strftime("%Y-%m-%d")} | set(changed or [])
    if first:
        cands |= {(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(CATCHUP_DAYS + 1)}
    t = now.timestamp()
    out = []
    for day in sorted(cands):
        gap = t - last_try.get(day, 0.0)
        changed_file = day in stat_now and list(stat_now[day]) != list(sent.get(day) or [])
        if day == today:
            if gap >= KEEPALIVE_S or (changed_file and gap >= MIN_GAP_S):
                out.append(day)
        elif changed_file and gap >= MIN_GAP_S:
            out.append(day)
    return out


# -- machine ------------------------------------------------------------------------------------

def _read_day(day, directory=None):
    path = pl.day_path(day, directory)
    recs = []
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"call"' not in raw:
                    continue                      # prompt records are never parsed (design 0.1)
                try:
                    recs.append(json.loads(raw.decode("utf-8", "replace")))
                except ValueError:
                    continue
    except OSError:
        pass
    return recs


def repo_head(root=None):
    """Short commit of the clone this module runs from, read from .git (no subprocess); '' when unknown."""
    root = root or os.path.dirname(HERE)
    try:
        git = os.path.join(root, ".git")
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            ref = head.split(None, 1)[1]
            p = os.path.join(git, *ref.split("/"))
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    return fh.read().strip()[:7]
            with open(os.path.join(git, "packed-refs"), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().endswith(ref):
                        return line.split()[0][:7]
            return ""
        return head[:7]
    except OSError:
        return ""


def build_report(day, machine, now, directory=None, sys_buckets=None, ledger_state=None):
    """The whole POST body of one day (design 0.2)."""
    stats = {}
    rows = summarize(_read_day(day, directory), day, stats)
    rep = {"schema": SCHEMA, "machine": machine, "day": day, "tz": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:],
           "generated_at": now.isoformat(timespec="seconds"), "code": repo_head(),
           "ledger": dict({"last_pass": None, "lag_s": None}, **(ledger_state or {}), calls_skipped=stats["skipped"]),
           "rows": rows, "sys": sys_rows((sys_buckets or {}).get(day, {}))}
    return rep


def _ledger_state(now, directory=None):
    """lag_s from the ledger lock file (touched by the collector's loop); {} when absent."""
    try:
        p = os.path.join(tempfile.gettempdir(), "monitor_ledger.lock")
        mt = os.path.getmtime(p)
        return {"last_pass": datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds"),
                "lag_s": int(max(0, now.timestamp() - mt))}
    except OSError:
        return {}


def _monitor_cfg():
    cfg = pl.read_env_file(os.path.join(os.path.expanduser("~"), ".claude", "monitor.env"))
    return cfg.get("MONITOR_URL"), cfg.get("MONITOR_TOKEN")


def post(report, timeout=10):
    url, token = _monitor_cfg()
    if not url or not token:
        return False
    req = urllib.request.Request(url.rstrip("/") + "/api/usage/report", data=json.dumps(report).encode("utf-8"),
                                 method="POST", headers={"Authorization": "Bearer " + token,
                                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return 200 <= resp.status < 300


# -- CPU / GPU sampling ---------------------------------------------------------------------------

def cpu_counters():
    """(idle, total) cumulative counters of this machine, or None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            f = lambda x: (x.dwHighDateTime << 32) | x.dwLowDateTime  # noqa: E731
            return (f(idle), f(kern) + f(user))      # kernel time includes idle
        with open("/proc/stat", encoding="utf-8") as fh:
            parts = [int(x) for x in fh.readline().split()[1:]]
        return (parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts[:8]))
    except Exception:  # noqa: BLE001
        return None


def gpu_state_path():
    return os.environ.get("USAGE_GPU_STATE") or os.path.join(os.path.expanduser("~"), ".claude", "gpu_state.json")


def read_gpu(now_s=None):
    try:
        with open(gpu_state_path(), "rb") as fh:
            return gpu_from_state(json.loads(fh.read().decode("utf-8", "replace")), now_s or time.time())
    except (OSError, ValueError):
        return None


def sys_path(directory=None):
    return os.path.join(directory or pl.log_dir(), ".usage_sys.json")


def load_sys(directory=None):
    try:
        with open(sys_path(directory), "rb") as fh:
            d = json.loads(fh.read().decode("utf-8", "replace"))
        return {day: {t: list(v) for t, v in bs.items()} for day, bs in (d.get("days") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_sys(days, directory=None):
    keep = sorted(days)[-SYS_KEEP_DAYS:]
    try:
        os.makedirs(directory or pl.log_dir(), exist_ok=True)
        tmp = sys_path(directory) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"days": {d: days[d] for d in keep}}, fh)
        os.replace(tmp, sys_path(directory))
    except OSError:
        pass


class Sampler(threading.Thread):
    """Daemon thread: every SAMPLE_S one CPU and one GPU sample into the day's buckets."""

    def __init__(self, days, directory=None):
        super().__init__(name="usage-sampler", daemon=True)
        self.days, self.directory, self.prev = days, directory, None

    def sample(self, now):
        cur = cpu_counters()
        cpu = cpu_pct(self.prev, cur)
        self.prev = cur
        gpu = read_gpu(now.timestamp())
        with _LOCK:
            add_sample(self.days.setdefault(now.strftime("%Y-%m-%d"), {}), bucket_iso(now), cpu, gpu)

    def run(self):
        while True:
            try:
                self.sample(datetime.now().astimezone())
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SAMPLE_S)


# -- the entry point of the ledger loop ---------------------------------------------------------

def _stat_days(days, directory=None):
    out = {}
    for d in days:
        try:
            st = os.stat(pl.day_path(d, directory))
            out[d] = [int(st.st_mtime), st.st_size]
        except OSError:
            pass
    return out


def _load_sent(directory=None):
    try:
        with open(os.path.join(directory or pl.log_dir(), ".usage_sent.json"), "rb") as fh:
            d = json.loads(fh.read().decode("utf-8", "replace"))
        return {k: v for k, v in d.items() if isinstance(v, list)}
    except (OSError, ValueError, AttributeError):
        return {}


def _save_sent(sent, directory=None):
    try:
        with open(os.path.join(directory or pl.log_dir(), ".usage_sent.json"), "w", encoding="utf-8") as fh:
            json.dump(sent, fh)
    except OSError:
        pass


def tick(changed_days, now=None, directory=None, sender=None):
    """Send the days that are due (see due_days). Never raises into the collector's loop. Returns the list of
    days sent. USAGE_NO_REPORT=1 (conftest) turns it into a no-op: a test must not post to the live server."""
    if os.environ.get("USAGE_NO_REPORT") == "1":
        return []
    sent_days = []
    try:
        now = now or datetime.now().astimezone()
        if not _STATE["started"]:
            _STATE["started"] = True
            _STATE["sent"] = _load_sent(directory)
            _STATE["sys"] = load_sys(directory)
            if _STATE["sampler"] is None and sender is None:
                _STATE["sampler"] = Sampler(_STATE["sys"], directory)
                _STATE["sampler"].start()
            first = True
        else:
            first = False
        if now.timestamp() < _STATE["backoff_until"]:
            return []
        today = now.strftime("%Y-%m-%d")
        cands = {(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(CATCHUP_DAYS + 2)} | set(changed_days or [])
        stat_now = _stat_days(cands, directory)
        send = sender or post
        machine = pl.machine_name()
        for day in due_days(now, changed_days, stat_now, _STATE["sent"], _STATE["last_try"], first):
            _STATE["last_try"][day] = now.timestamp()
            with _LOCK:
                snap = {d: {t: list(v) for t, v in b.items()} for d, b in (_STATE["sys"] or {}).items()}
            rep = build_report(day, machine, now, directory, snap, _ledger_state(now, directory))
            try:
                ok = send(rep)
            except Exception:  # noqa: BLE001 -- a network error silences us for BACKOFF_S, never the collector
                _STATE["backoff_until"] = now.timestamp() + BACKOFF_S
                break
            if ok:
                _STATE["sent"][day] = stat_now.get(day, [])
                sent_days.append(day)
        if sent_days:
            _save_sent(_STATE["sent"], directory)
            if _STATE["sampler"] is not None or sender is not None:
                with _LOCK:
                    save_sys(_STATE["sys"] or {}, directory)
    except Exception:  # noqa: BLE001
        pass
    return sent_days


def main(argv=None):
    """python monitor/usage_report.py --day 2026-10-30   prints the report of one day (no POST)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    rep = build_report(args.day, pl.machine_name(), datetime.now().astimezone(), sys_buckets=load_sys())
    dt = time.perf_counter() - t0
    sys.stdout.write(json.dumps(rep, indent=1, ensure_ascii=True)[:4000] + "\n")
    sys.stderr.write("rows %d, build %.3f s\n" % (len(rep["rows"]), dt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
