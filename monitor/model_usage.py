#!/usr/bin/env python3
"""model_usage.py -- one JSON line per paid model call, so usage can be audited.

Log: ~/.claude/model_usage.jsonl (override: MODEL_USAGE_LOG), per machine, never in a repo.
Each line: ts (UTC ISO), machine, source, model, ok, error, dur_s, tokens_in, tokens_out,
cost_usd, prompt, answer (both cut to MAX_TEXT chars).

record() never raises: a logging failure must not cost a voice answer.
Under pytest (PYTEST_CURRENT_TEST) record() writes nothing unless `path=` is given, so tests
cannot pollute the real log.

Viewer: `python monitor/model_usage.py [YYYY-MM-DD]` prints a table; the voice gateway serves
the same rows at /modele (HTML) and /modele.json.
"""
import html
import json
import os
import platform
import sys
import threading
import urllib.parse
from datetime import datetime, timezone

MAX_TEXT = 4000
_LOCK = threading.Lock()


def log_path():
    return os.environ.get("MODEL_USAGE_LOG") or os.path.join(
        os.path.expanduser("~"), ".claude", "model_usage.jsonl")


def _cut(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


def make_entry(source, model, prompt, answer, dur_s=None, ok=True, error="",
               tokens_in=None, tokens_out=None, cost_usd=None, now=None):
    """The dict that gets written. Pure."""
    now = now or datetime.now(timezone.utc)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "machine": platform.node(),
        "source": str(source or ""),
        "model": str(model or "domyslny model CLI"),
        "ok": bool(ok),
        "error": str(error or "")[:300],
        "dur_s": None if dur_s is None else round(float(dur_s), 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "prompt": _cut(prompt),
        "answer": _cut(answer),
    }


def record(source, model, prompt, answer, dur_s=None, ok=True, error="",
           tokens_in=None, tokens_out=None, cost_usd=None, path=None):
    try:
        if path is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return
        entry = make_entry(source, model, prompt, answer, dur_s, ok, error,
                           tokens_in, tokens_out, cost_usd)
        target = path or log_path()
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        # One binary write of the whole line: text mode adds "\r\n" in a second step and two
        # processes appending at once (parallel batch runs) tore a line in half on 2026-10-16.
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with _LOCK, open(target, "ab") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def _local_day(ts):
    """'YYYY-MM-DD' in the machine's local time for an ISO UTC stamp, or ''."""
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def read_rows(day=None, path=None, limit=1000):
    """Rows newest first. day='YYYY-MM-DD' (local) filters; bad lines are skipped."""
    rows = []
    try:
        with open(path or log_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (not day or _local_day(row.get("ts")) == day):
                    rows.append(row)
    except OSError:
        return []
    rows.reverse()
    return rows[:limit]


def summarize(rows):
    """{model: calls} plus total cost where known. Pure."""
    by_model = {}
    cost = 0.0
    for r in rows:
        by_model[r.get("model", "?")] = by_model.get(r.get("model", "?"), 0) + 1
        if isinstance(r.get("cost_usd"), (int, float)):
            cost += r["cost_usd"]
    return by_model, round(cost, 4)


def _local_time(ts):
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "?"


_CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#667;--line:#dde;--bad:#b00020}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e6e6;--mut:#99a;--line:#2c3036;--bad:#ff6b6b}}
body{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;margin:0;padding:12px 16px}
h1{font-size:18px;margin:0 0 6px}.mut{color:var(--mut)}
table{border-collapse:collapse;width:100%;margin-top:10px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
td.t{white-space:nowrap}.bad{color:var(--bad)}
details summary{cursor:pointer}pre{white-space:pre-wrap;margin:4px 0 0;font:inherit}
@media (max-width:640px){th.h,td.h{display:none}}
"""


BATCH_MIN = 20  # a source with more calls than this in the shown rows is a batch run, not a conversation


def source_groups(rows, source=""):
    """({source: calls} for sources with > BATCH_MIN rows, rows to list one by one). Pure.
    With `source` set, that source is listed in full and nothing else is collapsed."""
    if source:
        return {}, [r for r in rows if r.get("source") == source]
    counts = {}
    for r in rows:
        counts[r.get("source", "")] = counts.get(r.get("source", ""), 0) + 1
    big = {s: n for s, n in counts.items() if n > BATCH_MIN}
    return big, [r for r in rows if r.get("source", "") not in big]


def render_html(rows, day="", days=(), source=""):
    """Self-contained HTML page for the rows. Pure; every field is escaped."""
    e = html.escape
    all_rows = rows
    big, rows = source_groups(all_rows, source)
    by_model, cost = summarize(all_rows)
    parts = ["<!doctype html><html lang=pl><head><meta charset=utf-8>",
             "<meta name=viewport content='width=device-width,initial-scale=1'>",
             "<title>Modele - log uzycia</title><style>", _CSS, "</style></head><body>",
             "<h1>Uzycie modeli%s</h1>" % (" - " + e(day) if day else ""),
             "<div class=mut>%d wywolan: %s%s</div>" % (
                 len(all_rows),
                 ", ".join("%s &times; %d" % (e(m), n) for m, n in sorted(by_model.items())) or "brak",
                 (" &middot; koszt znany: $%s" % cost) if cost else "")]
    if days:
        parts.append("<div class=mut>dni: " + " ".join(
            "<a href='?day=%s'>%s</a>" % (e(d), e(d)) for d in days) + "</div>")
    for s, n in sorted(big.items(), key=lambda kv: -kv[1]):
        grp = [r for r in all_rows if r.get("source", "") == s]
        gcost = sum(r["cost_usd"] for r in grp if isinstance(r.get("cost_usd"), (int, float)))
        parts.append("<div class=mut>seria: <b>%s</b> &times; %d%s &middot; <a href='?day=%s&amp;source=%s'>pokaz</a>"
                     "</div>" % (e(s or "?"), n, (" &middot; $%.2f" % gcost) if gcost else "",
                                 e(day), e(urllib.parse.quote(s, safe=""))))
    if source:
        parts.append("<div class=mut>tylko zrodlo <b>%s</b> &middot; <a href='?day=%s'>wszystkie</a></div>"
                     % (e(source), e(day)))
    parts.append("<table><tr><th>godz.</th><th>model</th><th class=h>zrodlo</th>"
                 "<th class=h>czas</th><th>zapytanie / odpowiedz</th></tr>")
    for r in rows:
        ok = r.get("ok", True)
        tok = ""
        if r.get("tokens_in") is not None or r.get("tokens_out") is not None:
            tok = " &middot; tok %s/%s" % (e(str(r.get("tokens_in"))), e(str(r.get("tokens_out"))))
        if isinstance(r.get("cost_usd"), (int, float)):
            tok += " &middot; $%.4f" % r["cost_usd"]
        ans = r.get("answer") or ""
        err = "" if ok else " <span class=bad>BLAD: %s</span>" % e(r.get("error") or "")
        parts.append(
            "<tr><td class=t>%s</td><td>%s</td><td class=h>%s</td><td class='h t'>%s%s</td>"
            "<td><details><summary>%s</summary><pre>%s</pre><div class=mut>maszyna: %s%s</div>"
            "</details>%s</td></tr>" % (
                e(_local_time(r.get("ts"))), e(r.get("model", "?")), e(r.get("source", "")),
                e(str(r.get("dur_s"))) + " s" if r.get("dur_s") is not None else "", tok,
                e((r.get("prompt") or "")[:140]), e(ans), e(r.get("machine", "")), "", err))
    parts.append("</table></body></html>")
    return "".join(parts)


def days_available(path=None, last=14):
    seen = []
    try:
        with open(path or log_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = _local_day(json.loads(line).get("ts"))
                except ValueError:
                    continue
                if d and d not in seen:
                    seen.append(d)
    except OSError:
        return []
    return sorted(seen, reverse=True)[:last]


def main(argv):
    day = argv[1] if len(argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    rows = read_rows(day=day)
    by_model, cost = summarize(rows)
    print("%s: %d wywolan %s koszt znany $%s" % (day, len(rows), by_model, cost))
    for r in reversed(rows):
        print("%s  %-22s %-16s %5ss  %s -> %s" % (
            _local_time(r.get("ts")), r.get("model"), r.get("source"), r.get("dur_s"),
            (r.get("prompt") or "")[:60], (r.get("answer") or "")[:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
