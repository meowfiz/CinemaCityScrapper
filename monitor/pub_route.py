#!/usr/bin/env python3
"""
pub_route.py -- publication files written in a CODE repo travel to their publication repo by
themselves (ZASADY 6.4; decision of the user 2026-10-22: "results of publication work must land in
the right directories and repos, even when I start something in RibnXtr by accident").

    python monitor/pub_route.py --plan            what would move where (changes nothing)
    python monitor/pub_route.py --apply           move: copy, verify SHA-256, commit in target, push
                                                  target through its own auto_sync gates, then remove
                                                  in the source and commit the removal + pointers
    python monitor/pub_route.py --apply --no-push same, commits stay local
    python monitor/pub_route.py --hook            PostToolUse hook: says when a written file belongs
                                                  to a publication repo (never blocks, never fails)

Routes are DATA: <repo>/.claude/pub_routes.json (versioned in git). Without that file this script
does nothing, so the pack can carry it to every repo. Clones of target repos come from
~/.claude/monitor_repos.env (same map as transfer.py / ready.py).

Per file the decision is a pure function of three SHA-256 sums (source, target now, target as last
logged in the target's notes/MIGRACJA_log.md):
    target absent                    -> copy
    target == source                 -> same      (only the source copy is removed)
    target == last logged != source  -> update    (target untouched since our move: fast-forward)
    otherwise                        -> conflict  (reported, file stays in the source, nothing written)
Never overwrites a file that differs from what we put there. Never touches code (`nigdy` patterns)
or anything outside the routes. Removal in the source happens only after the copy was read back with
the same SHA-256 and committed in the target; auto_sync's gate G3b accepts exactly those removals.

Standard library only. ASCII only. Text read and written as UTF-8 explicitly (ZASADY 4.10).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
CFG_REL = ".claude/pub_routes.json"
LOG_REL = "notes/MIGRACJA_log.md"
DATA_POINTER_REL = "notes/DANE_w_zrodle.md"
SRC_POINTER_REL = "notes/publikacja/PRZENIESIONE.md"
PROJECTS_REL = "notes/PROJEKTY_PUBLIKACYJNE.md"
REPO_MAP = os.environ.get("PUB_ROUTE_REPO_MAP") or os.path.join(
    os.path.expanduser("~"), ".claude", "monitor_repos.env")
IDENT = ["-c", "user.email=meow@mat.umk.pl", "-c", "user.name=meowfiz"]
LOG_HEAD = ("# Log migracji z repo zrodlowego\n\n"
            "Dopisywany przez `monitor/pub_route.py` (ZASADY 6.4). Jeden wiersz = jeden plik przeniesiony "
            "albo zaktualizowany; SHA-256 sprawdzone po kopii. Nie edytowac recznie.\n\n"
            "| data | zrodlo | plik w zrodle | sha256 | plik tutaj | HEAD zrodla |\n"
            "|---|---|---|---|---|---|\n")
LOG_ROW_RE = re.compile(r"^\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*`([^`]+)`\s*\|\s*([0-9a-f]{64})\s*\|\s*`([^`]+)`\s*\|")


# --- pure part (tested in tests/test_pub_route.py, ZASADY 4.5) -------------------------------

def glob_regex(pattern):
    """`**` crosses directories, `*` and `?` do not. Same semantics as tools/transfer.py."""
    out, i = [], 0
    p = pattern.replace("\\", "/")
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _match(pattern, path):
    return bool(glob_regex(pattern).match(path))


def route_for(path, cfg):
    """(route, rule, mode) for a repo-relative path, or (None, None, None).
    `nigdy` wins over everything (code, shared files, our own pointers); then the first move rule,
    then the first pointer rule."""
    path = path.replace("\\", "/")
    if any(_match(p, path) for p in cfg.get("nigdy", [])) or path in (CFG_REL, SRC_POINTER_REL, PROJECTS_REL):
        return None, None, None
    for route in cfg.get("trasy", []):
        for rule in route.get("move", []):
            if _match(rule["glob"], path):
                return route, rule, "move"
    for route in cfg.get("trasy", []):
        for rule in route.get("pointer", []):
            if _match(rule["glob"], path):
                return route, rule, "pointer"
    return None, None, None


def target_path(path, rule):
    """Path in the target repo: prefix `z` replaced by `do` (both default to keeping the path)."""
    z, do = rule.get("z", ""), rule.get("do", rule.get("z", ""))
    if z and not path.startswith(z):
        raise ValueError("%s nie zaczyna sie od %s" % (path, z))
    return do + path[len(z):]


def decide(src_sha, dst_sha, logged_sha):
    """'copy' | 'same' | 'update' | 'conflict' -- see module docstring."""
    if dst_sha is None:
        return "copy"
    if dst_sha == src_sha:
        return "same"
    if logged_sha is not None and dst_sha == logged_sha:
        return "update"
    return "conflict"


def parse_log(text):
    """{'by_target': {target: last sha}, 'by_source': {source path: last sha}} from MIGRACJA_log.md."""
    by_t, by_s = {}, {}
    for ln in (text or "").splitlines():
        m = LOG_ROW_RE.match(ln)
        if m:
            by_s[m.group(3)] = m.group(4)
            by_t[m.group(5)] = m.group(4)
    return {"by_target": by_t, "by_source": by_s}


def log_rows(moves, source_name, head, today):
    return ["| %s | %s | `%s` | %s | `%s` | %s |" % (today, source_name, m["path"], m["sha"], m["target"], head[:12])
            for m in moves]


def make_plan(files, cfg, src_sha, target_state):
    """[{path, projekt, repo, target, mode, sha, action}] for every file on a route.
    src_sha(path) -> hex; target_state(repo, target) -> (dst_sha or None, logged_sha or None, clone or None).
    Pointer-mode files get action 'pointer' (they never move: run_files are not in git, ZASADY 2.5)."""
    out = []
    for p in sorted(files):
        route, rule, mode = route_for(p, cfg)
        if route is None:
            continue
        item = {"path": p, "projekt": route.get("projekt"), "repo": route["repo"], "mode": mode}
        if mode == "pointer":
            item.update(target=None, sha=None, action="pointer")
            out.append(item)
            continue
        t = target_path(p, rule)
        sha = src_sha(p)
        dst, logged, clone = target_state(route["repo"], t)
        action = "no-clone" if clone is None else decide(sha, dst, logged)
        item.update(target=t, sha=sha, action=action)
        out.append(item)
    return out


def g3b(deleted, cfg, log_of, target_sha):
    """(allowed, refused) among source paths deleted vs upstream.
    Allowed only when the path is on a move route, the target repo's log has it with SHA S, and the
    target file exists now with SHA S. log_of(repo) -> parse_log dict; target_sha(repo, t) -> hex|None."""
    allowed, refused = [], []
    for p in deleted:
        route, rule, mode = route_for(p, cfg)
        if mode != "move":
            refused.append(p)
            continue
        log = log_of(route["repo"])
        s = log["by_source"].get(p)
        t = target_path(p, rule)
        if s and log["by_target"].get(t) == s and target_sha(route["repo"], t) == s:
            allowed.append(p)
        else:
            refused.append(p)
    return allowed, refused


def hook_message(rel, cfg):
    route, _rule, mode = route_for(rel, cfg)
    if route is None:
        return None
    if mode == "pointer":
        return ("pub_route: %s to dane publikacji %s (repo %s) -- zostaja tutaj (run_files poza gitem), "
                "repo publikacji dostanie wskaznik na koncu zadania." % (rel, route.get("projekt"), route["repo"]))
    return ("pub_route: ten plik nalezy do repo %s (publikacja %s) -- zostanie przeniesiony tam na koncu "
            "zadania (kopia + SHA-256, w tym repo zostanie wskaznik). Pisz dalej w tej samej sciezce."
            % (route["repo"], route.get("projekt")))


def render_src_pointer(rows):
    """notes/publikacja/PRZENIESIONE.md in the source: every moved file and where it lives now."""
    out = ["# Przeniesione do repo publikacyjnych", "",
           "Generowany przez `monitor/pub_route.py` (trasy: `.claude/pub_routes.json`). Pliki z tej listy",
           "zyja w repo publikacji; tu ich nie ma. Nowy plik w tej samej sciezce zostanie przeniesiony",
           "na koncu zadania. Nie edytowac recznie.", "",
           "| plik (tu) | repo | plik tam | sha256 (12) | data |", "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["path"]):
        out.append("| `%s` | %s | `%s` | %s | %s |" % (r["path"], r["repo"], r["target"], r["sha"][:12], r["date"]))
    return "\n".join(out) + "\n"


def parse_src_pointer(text):
    rows = []
    for ln in (text or "").splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*(\S+)\s*\|\s*`([^`]+)`\s*\|\s*([0-9a-f]+)\s*\|\s*(\S+)\s*\|", ln)
        if m:
            rows.append({"path": m.group(1), "repo": m.group(2), "target": m.group(3), "sha": m.group(4),
                         "date": m.group(5)})
    return rows


def render_projects(cfg, repo_map, source_name):
    out = ["# Projekty publikacyjne", "",
           "Generowany przez `monitor/pub_route.py` z `.claude/pub_routes.json`. Zasada: **kod zostaje w %s,"
           % source_name,
           "praca nad publikacja (teksty, openspec publikacji, figury, literatura) jest w repo publikacji.**",
           "Pliki pisane tu na trasie sa przenoszone automatycznie na koncu zadania (auto_sync).",
           "Klon repo publikacji na danej maszynie: `~/.claude/monitor_repos.env` (sciezki sa per maszyna,",
           "wiec nie ma ich w tym pliku); log przeniesien z SHA: `notes/MIGRACJA_log.md` w repo publikacji.", "",
           "| projekt | repo | kod w %s | trasy (move) |" % source_name, "|---|---|---|---|"]
    for r in cfg.get("trasy", []):
        out.append("| %s | %s | %s | %s |" % (
            r.get("projekt"), r["repo"], r.get("kod", "-"),
            ", ".join("`%s`" % m["glob"] for m in r.get("move", []))))
    return "\n".join(out) + "\n"


def render_data_pointer(source_name, rows):
    out = ["# Dane tej publikacji w repo zrodlowym", "",
           "Generowany przez `monitor/pub_route.py`. Pliki z `project_files/run_files/` repo %s (poza gitem," % source_name,
           "ZASADY 2.5) -- zostaja tam i jada transferem tamtego repo (2.13). Stan na maszynie, ktora ostatnio liczyla.", "",
           "| plik w %s | bajty | sha256 (12) |" % source_name, "|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["path"]):
        out.append("| `%s` | %d | %s |" % (r["path"], r["bytes"], r["sha"][:12]))
    return "\n".join(out) + "\n"


# --- machine part ----------------------------------------------------------------------------

def load_repo_map(path=None):
    out = {}
    try:
        with io.open(path or REPO_MAP, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def load_cfg(root):
    try:
        with io.open(os.path.join(root, *CFG_REL.split("/")), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None


def git(repo, *args, check=False):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, stdin=subprocess.DEVNULL, **QUIET)
    out = r.stdout.decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError("git %s rc=%d: %s" % (" ".join(args), r.returncode,
                                                 r.stderr.decode("utf-8", "replace").strip()[:300]))
    return r.returncode, out


def repo_files(root):
    """Tracked + untracked-not-ignored files, repo-relative with '/'."""
    _, a = git(root, "ls-files", "-z")
    _, b = git(root, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted(set(p for p in (a + b).split("\0") if p))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _abs(root, rel):
    return os.path.join(root, *rel.split("/"))


def read_text(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build_plan(root, cfg, repo_map, files=None):
    files = repo_files(root) if files is None else files
    logs = {}

    def target_state(repo, t):
        clone = repo_map.get(repo)
        if not clone or not os.path.isdir(clone):
            return None, None, None
        if repo not in logs:
            logs[repo] = parse_log(read_text(_abs(clone, LOG_REL)))
        p = _abs(clone, t)
        return (sha256_file(p) if os.path.isfile(p) else None), logs[repo]["by_target"].get(t), clone

    return make_plan(files, cfg, lambda p: sha256_file(_abs(root, p)), target_state)


def g3b_for(root, deleted, cfg=None, repo_map=None):
    cfg = cfg or load_cfg(root) or {}
    repo_map = repo_map if repo_map is not None else load_repo_map()

    def log_of(repo):
        clone = repo_map.get(repo)
        return parse_log(read_text(_abs(clone, LOG_REL)) if clone else None)

    def target_sha(repo, t):
        clone = repo_map.get(repo)
        p = _abs(clone, t) if clone else None
        return sha256_file(p) if p and os.path.isfile(p) else None

    return g3b(deleted, cfg, log_of, target_sha)


def _today():
    return _dt.date.today().isoformat()


def _append_note(clone, lines):
    p = _abs(clone, "notes/sesje/%s-pub_route.md" % _today())
    t = read_text(p) or ("# Sesja %s (automat pub_route)\n\n## Co zrobiono\n\n" % _today())
    write_text(p, t.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
    return "notes/sesje/%s-pub_route.md" % _today()


def _target_ready(clone):
    """(ok, why). Target must be a clean clone, fast-forwarded to origin when behind."""
    rc, st = git(clone, "status", "--porcelain")
    if rc != 0:
        return False, "git status nie dziala"
    if st.strip():
        return False, "repo docelowe ma niezacommitowane zmiany (inna sesja?) -- nie ruszam"
    git(clone, "fetch", "-q", "origin")
    rc, _ = git(clone, "merge-base", "--is-ancestor", "HEAD", "@{u}")
    if rc == 0:
        git(clone, "merge", "-q", "--ff-only", "@{u}")
    return True, ""


def _push_target(clone, report):
    """Push through the target's own auto_sync gates (2.8); fallback: nothing, commit stays local."""
    a = os.path.join(clone, "monitor", "auto_sync.py")
    if not os.path.exists(a):
        report.append("  %s: brak monitor/auto_sync.py -- commit lokalny" % os.path.basename(clone))
        return "local"
    r = subprocess.run([sys.executable, a, "--root", clone], cwd=clone, capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=1200, **QUIET)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    outcome = out[0] if out else "?"
    report.append("  %s: auto_sync %s (%s)" % (os.path.basename(clone), outcome, (out[-1] if out else "")[:90]))
    return outcome


def apply(root, push=True, repo_map=None, log=print):
    """Run the routes once. Returns (summary dict, report lines). Never raises for one bad target."""
    cfg = load_cfg(root)
    report = []
    summary = {"moved": 0, "same": 0, "updated": 0, "conflict": 0, "no_clone": 0, "skipped_repo": 0,
               "pointer": 0, "removed_in_source": 0}
    if not cfg:
        return summary, ["pub_route: brak %s -- nic do zrobienia" % CFG_REL]
    repo_map = load_repo_map() if repo_map is None else repo_map
    source_name = cfg.get("zrodlo") or os.path.basename(root)
    _, head = git(root, "rev-parse", "HEAD")
    head = head.strip()
    plan = build_plan(root, cfg, repo_map)
    by_repo = {}
    for it in plan:
        by_repo.setdefault(it["repo"], []).append(it)
    done_moves = []
    for repo, items in sorted(by_repo.items()):
        moves = [i for i in items if i["action"] in ("copy", "update", "same")]
        for i in items:
            if i["action"] == "conflict":
                summary["conflict"] += 1
                report.append("KONFLIKT %s -> %s:%s (cel zmieniony od ostatniego przeniesienia) -- zostaje tutaj"
                              % (i["path"], repo, i["target"]))
            elif i["action"] == "no-clone":
                summary["no_clone"] += 1
        pointers = [i for i in items if i["action"] == "pointer"]
        if not moves and not pointers:
            continue
        clone = repo_map.get(repo)
        if not clone or not os.path.isdir(clone):
            report.append("%s: brak klonu na tej maszynie -- %d plikow czeka" % (repo, len(moves)))
            continue
        ok, why = _target_ready(clone)
        if not ok:
            summary["skipped_repo"] += 1
            report.append("%s: %s (%d plikow czeka)" % (repo, why, len(moves)))
            continue
        # re-plan against the fast-forwarded target: someone may have pushed there meanwhile
        fresh = {i["path"]: i for i in build_plan(root, cfg, repo_map, files=[m["path"] for m in moves])}
        moves = [fresh[m["path"]] for m in moves if fresh[m["path"]]["action"] in ("copy", "update", "same")]
        written = []
        for m in moves:
            dst = _abs(clone, m["target"])
            if m["action"] in ("copy", "update"):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(_abs(root, m["path"]), dst)
            if sha256_file(dst) != m["sha"]:
                raise RuntimeError("SHA-256 po kopii rozne: %s" % dst)
            written.append(m)
        changed = [m for m in written if m["action"] in ("copy", "update")]
        log_text = read_text(_abs(clone, LOG_REL)) or LOG_HEAD
        logged = parse_log(log_text)
        new_rows = [m for m in written if logged["by_source"].get(m["path"]) != m["sha"]
                    or logged["by_target"].get(m["target"]) != m["sha"]]
        paths = [m["target"] for m in changed]
        if new_rows:
            write_text(_abs(clone, LOG_REL), log_text.rstrip("\n") + "\n" +
                       "\n".join(log_rows(new_rows, source_name, head, _today())) + "\n")
            paths.append(LOG_REL)
        if pointers:
            rows = []
            for p in pointers:
                fp = _abs(root, p["path"])
                rows.append({"path": p["path"], "bytes": os.path.getsize(fp), "sha": sha256_file(fp)})
            txt = render_data_pointer(source_name, rows)
            if read_text(_abs(clone, DATA_POINTER_REL)) != txt:
                write_text(_abs(clone, DATA_POINTER_REL), txt)
                paths.append(DATA_POINTER_REL)
            summary["pointer"] += len(pointers)
        if paths:
            paths.append(_append_note(clone, [
                "- pub_route: z %s @ %s: %d nowych/zaktualizowanych, %d bez zmian, %d wskaznikow danych."
                % (source_name, head[:8], len(changed), len(written) - len(changed), len(pointers))]))
            git(clone, "add", "--", *paths, check=True)
            git(clone, *IDENT, "commit", "-q", "-m",
                "pub_route: %d plikow z %s @ %s (ZASADY 6.4)" % (len(changed), source_name, head[:8]), "--",
                *paths, check=True)
            _, ns = git(clone, "show", "--name-status", "--format=", "HEAD")
            if any(ln[:1] == "D" for ln in ns.splitlines()):
                raise RuntimeError("commit w %s zawiera usuniecia -- przerwane" % repo)
            report.append("%s: commit %d plikow + log" % (repo, len(changed)))
            if push:
                _push_target(clone, report)
        summary["moved"] += sum(1 for m in written if m["action"] == "copy")
        summary["updated"] += sum(1 for m in written if m["action"] == "update")
        summary["same"] += sum(1 for m in written if m["action"] == "same")
        done_moves.extend(written)
    if done_moves:
        _remove_in_source(root, cfg, repo_map, source_name, done_moves, summary, report)
    report.insert(0, "pub_route: przeniesione %(moved)d, zaktualizowane %(updated)d, juz w celu %(same)d, "
                     "konflikty %(conflict)d, bez klonu %(no_clone)d, repo pominiete %(skipped_repo)d, "
                     "usuniete w zrodle %(removed_in_source)d" % summary)
    return summary, report


def _remove_in_source(root, cfg, repo_map, source_name, moves, summary, report):
    _, tracked = git(root, "ls-files", "-z")
    tracked = set(p for p in tracked.split("\0") if p)
    removed_tracked = []
    for m in moves:
        if m["path"] in tracked:
            # -f: the working-tree content IS what was copied and verified (sha of the disk file),
            # so a local modification is not lost -- it lives in the target now
            git(root, "rm", "-q", "-f", "--", m["path"], check=True)
            removed_tracked.append(m["path"])
        else:
            try:
                os.remove(_abs(root, m["path"]))
            except OSError:
                pass
        summary["removed_in_source"] += 1
    old = parse_src_pointer(read_text(_abs(root, SRC_POINTER_REL)))
    keep = {r["path"]: r for r in old}
    for m in moves:
        keep[m["path"]] = {"path": m["path"], "repo": m["repo"], "target": m["target"], "sha": m["sha"],
                           "date": _today()}
    write_text(_abs(root, SRC_POINTER_REL), render_src_pointer(list(keep.values())))
    write_text(_abs(root, PROJECTS_REL), render_projects(cfg, repo_map, source_name))
    paths = removed_tracked + [SRC_POINTER_REL, PROJECTS_REL]
    git(root, "add", "--", SRC_POINTER_REL, PROJECTS_REL, check=True)
    allowed, refused = g3b_for(root, removed_tracked, cfg, repo_map)
    if refused:
        raise RuntimeError("G3b nie przechodzi dla %s -- przerwane przed commitem w zrodle" % refused[:5])
    rc, _ = git(root, "diff", "--cached", "--quiet", "--", *paths)
    if rc == 0:
        report.append("%s: %d plikow spoza gita usunietych, wskazniki bez zmian -- brak commitu"
                      % (source_name, len(moves)))
        return
    git(root, *IDENT, "commit", "-q", "-m",
        "pub_route: %d plikow przeniesionych do repo publikacji (ZASADY 6.4; log w celu)" % len(moves),
        "--", *paths, check=True)
    report.append("%s: commit usuniecia %d plikow (G3b: %d z logiem) + wskazniki" % (
        source_name, len(removed_tracked), len(allowed)))


def cmd_plan(root):
    cfg = load_cfg(root)
    if not cfg:
        print("brak %s" % CFG_REL)
        return 0
    plan = build_plan(root, cfg, load_repo_map())
    agg = {}
    for it in plan:
        agg.setdefault((it["repo"], it["action"]), []).append(it)
    for (repo, act), items in sorted(agg.items()):
        print("%-32s %-9s %4d" % (repo, act, len(items)))
        for i in items[:50]:
            print("    %s -> %s" % (i["path"], i["target"] or "(wskaznik)"))
    if not plan:
        print("nic na trasach")
    return 0


def cmd_hook(root):
    """PostToolUse: tool_input.file_path on a route -> additionalContext for Claude. Never fails."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw or "{}")
        fp = (data.get("tool_input") or {}).get("file_path") or (data.get("tool_input") or {}).get("notebook_path")
        cfg = load_cfg(root)
        if not fp or not cfg:
            return 0
        rel = os.path.relpath(os.path.abspath(fp), os.path.abspath(root)).replace(os.sep, "/")
        if rel.startswith("../"):
            return 0
        msg = hook_message(rel, cfg)
        if msg:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                                "additionalContext": msg}}))
            sys.stderr.write(msg + "\n")
    except Exception:  # noqa: BLE001 - a hook must never break a tool call
        pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="trasy publikacji (ZASADY 6.4)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--hook", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    root = os.path.abspath(a.root)
    if a.hook:
        return cmd_hook(root)
    if a.plan:
        return cmd_plan(root)
    summary, report = apply(root, push=not a.no_push)
    for ln in report:
        print(ln)
    return 1 if summary["conflict"] else 0


if __name__ == "__main__":
    sys.exit(main())
