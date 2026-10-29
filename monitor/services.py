#!/usr/bin/env python3
"""
services.py -- one command that brings this machine's background services up, and one table that
says whether they are up.

    python monitor/services.py            # table: what runs, what does not, why
    python monitor/services.py --start    # start whatever is missing (idempotent), then the table
    python monitor/services.py --restart  # replace running ones (after a pack update)
    python monitor/services.py --stop     # stop them
    python monitor/services.py --json     # the same state, machine readable

Why this file exists. Three processes have to run for the phone, the car and the Home Assistant
panel to reach this machine, and each one was started a different way: the ask worker from a
logon .cmd, the execute worker by hand, the voice gateway from an open terminal. Sitting down at
the computer meant remembering all three, and forgetting one meant silence from exactly the place
the system exists to be reached from -- away from the desk. There is now one answer to "is it
up?" and one answer to "bring it up".

What it does NOT do: it never starts anything the machine has not been configured for. No
MONITOR_URL/MONITOR_TOKEN in ~/.claude/monitor.env means the workers stay down and the table says
so, rather than spawning loops that cannot reach anything.

Truth comes from a probe, not from a note somewhere: a worker is up when its lock has been
touched recently, the gateway is up when its port accepts a connection. A lock file left behind
by a process that died is not evidence of anything, and is reported as "nie dziala".

Standard library only. ASCII only.
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
# Uslugi tla chodza na pythonw (bez konsoli): kazdy podproces bez tej flagi dostaje WLASNA konsole,
# a z Windows Terminal jako terminalem domyslnym -- okno, ktore zostaje (2026-10-05: 432 okna).
QUIET = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import heartbeat  # noqa: E402
import ask_worker  # noqa: E402
import execute_worker  # noqa: E402

GATEWAY = os.path.join(ROOT, "tools", "voice_gateway.py")
GATEWAY_LOCK = os.path.join(tempfile.gettempdir(), "monitor_voice_gateway.lock")
GATEWAY_PORT = int(os.environ.get("ASK_VOICE_PORT", "8788"))
PROBE_TIMEOUT_S = 1.0

# The dashboard watcher lives in the HA repository (it is the only one with dashboards) and is
# started from here, because this file is the one place that answers "what is running on this
# machine". Absent HA clone = the service simply does not apply, exactly like the gateway in a
# repository that has none.
DASH_REL = os.path.join("project_files", "python", "deploy_dashboard.py")
DASH_LOCK = os.path.join(tempfile.gettempdir(), "monitor_dashboards.lock")
DASH_INTERVAL_S = int(os.environ.get("MONITOR_DASH_INTERVAL_S", "300"))
DASH_TTL_S = DASH_INTERVAL_S * 3   # two missed rounds is noise; three is a dead process

STATE_UP, STATE_DOWN, STATE_OFF = "dziala", "nie dziala", "wylaczone"


def dashboard_script(repo_map=None):
    """Path to the HA repo's deploy script, or None when this machine has no HA clone."""
    mapping = repo_map if repo_map is not None else ask_worker.load_repo_map()
    ha = mapping.get("HA")
    if not ha:
        return None
    path = os.path.join(ha, DASH_REL)
    return path if os.path.exists(path) else None


# -- the voice gateway -----------------------------------------------------------------------
# It is an HTTP(S) server, not a poll loop, so its lock says nothing about whether it works --
# the port does. A gateway bound to the tailnet address answers on the loopback too only if it
# binds 0.0.0.0, which it deliberately does not; so probe the address it actually binds.

def gateway_bind():
    """The address the gateway binds, asked of the same source the gateway asks."""
    if os.environ.get("ASK_VOICE_BIND"):
        return os.environ["ASK_VOICE_BIND"]
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, timeout=5, **QUIET)
        ip = out.stdout.decode("utf-8", "replace").strip().splitlines()
        if out.returncode == 0 and ip:
            return ip[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


def port_open(host, port, timeout=PROBE_TIMEOUT_S, connect=None):
    """Pure apart from the injected connector: does something accept connections there?"""
    if connect is not None:
        return connect(host, port)
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def spawn_detached(args, cwd=None, env=None, log=None):
    """Start a process that outlives this one and shows no console window. `env`: extra variables.
    `log`: path of a file the output is appended to (default: discarded)."""
    out = subprocess.DEVNULL
    if log:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        out = open(log, "ab")
    kw = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": subprocess.STDOUT if log else subprocess.DEVNULL,
          "close_fds": True, "cwd": cwd or os.path.expanduser("~")}
    if env:
        kw["env"] = dict(os.environ, **env)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(args, **kw)


def quiet_interpreter():
    """pythonw.exe where it exists: a logon start must not flash a console window."""
    exe = sys.executable or "python"
    quiet = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return quiet if os.name == "nt" and os.path.exists(quiet) else exe


def write_pid(path, pid):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "at": time.time(), "machine": platform.node()}, f)
    except OSError:
        pass


def read_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f).get("pid") or 0) or None
    except (OSError, ValueError, TypeError):
        return None


def kill_pid(pid, kill=os.kill):
    if not pid or pid == os.getpid():
        return False
    try:
        kill(pid, 15)  # SIGTERM; TerminateProcess on Windows
        return True
    except OSError:
        return False  # already gone


# -- the three services ----------------------------------------------------------------------

def _cfg_missing(cfg):
    return not cfg.get("MONITOR_URL") or not cfg.get("MONITOR_TOKEN")


def ask_state(cfg, now=None, probe=None):
    if _cfg_missing(cfg):
        return STATE_OFF, "brak MONITOR_URL/MONITOR_TOKEN w ~/.claude/monitor.env"
    poll = ask_worker._float(cfg, "MONITOR_ASK_POLL_S", ask_worker.POLL_S)
    live = ask_worker.lock_is_live(ask_worker.lock_path(),
                                   time.time() if now is None else now, poll)
    return (STATE_UP if live else STATE_DOWN), "pytania z telefonu, auta i panelu (read-only)"


def execute_state(cfg, now=None, probe=None):
    if _cfg_missing(cfg):
        return STATE_OFF, "brak MONITOR_URL/MONITOR_TOKEN w ~/.claude/monitor.env"
    live = execute_worker.another_is_running(time.time() if now is None else now)
    repos = execute_worker._enabled_repos(ask_worker.load_repo_map())
    note = ("zlecenia zmieniajace kod: %s" % ", ".join(repos)) if repos \
        else "zlecenia zmieniajace kod (zadne repo nie ma wlaczonego execute)"
    return (STATE_UP if live else STATE_DOWN), note


def _voice_env_value(key):
    """Read a KEY=VALUE from ~/.claude/voice.env without importing voice_gateway (avoids
    pulling in faster_whisper at status time)."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "voice.env")
    if not os.path.exists(path):
        return os.environ.get(key, "")
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    if k.strip() == key:
                        return v.strip()
    except OSError:
        pass
    return os.environ.get(key, "")


def gateway_state(cfg, now=None, probe=None, version_probe=None):
    if not os.path.exists(GATEWAY):
        return STATE_OFF, "tego repozytorium nie dotyczy (brak tools/voice_gateway.py)"
    host = gateway_bind()
    up = port_open(host, GATEWAY_PORT, connect=probe)
    stt_hosts = _voice_env_value("ASK_STT_HOSTS") or _voice_env_value("ASK_STT_REMOTE") or "local"
    whisper = _voice_env_value("ASK_WHISPER") or "small"
    device = _voice_env_value("ASK_WHISPER_DEVICE") or "cpu"
    tts_engine = _voice_env_value("ASK_VOICE_TTS_ENGINE") or "edge"
    tts_hosts = (_voice_env_value("ASK_VOICE_TTS_HOSTS")
                 or _voice_env_value("ASK_VOICE_TTS_REMOTE")
                 or ("local" if tts_engine != "chatterbox" else "none"))
    note = ("bramka glosowa na %s:%d; stt=%s whisper=%s/%s; tts=%s hosts=%s"
            % (host, GATEWAY_PORT, stt_hosts, whisper, device, tts_engine, tts_hosts))
    # 2026-10-22: after `--restart --only voice` the old process kept the port, /health still
    # answered with the old fingerprint and this table said "dziala". Same check as relay_stale.
    if up and (probe is None or version_probe) and gateway_stale(host, version_probe=version_probe):
        note = ("STARY KOD bramki (plik zmieniony po starcie): python monitor/services.py --restart "
                "--only voice; " + note)
    return (STATE_UP if up else STATE_DOWN), note


def _file_version(path):
    """Same fingerprint as voice_gateway.file_version: sha256[:12], line endings normalised."""
    import hashlib
    try:
        with open(path, "rb") as fh:
            raw = fh.read().replace(b"\r\n", b"\n")
    except OSError:
        return None
    return hashlib.sha256(raw).hexdigest()[:12]


def _running_version(host):
    """X-Gateway-Version of whatever answers /health, or None (self-signed cert accepted)."""
    import ssl
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for scheme in ("https", "http"):
        try:
            url = "%s://%s:%d/health" % (scheme, host, GATEWAY_PORT)
            with urllib.request.urlopen(url, timeout=3, context=ctx) as resp:
                return resp.headers.get("X-Gateway-Version")
        except Exception:  # noqa: BLE001 - failure = not this scheme
            continue
    return None


def gateway_stale(host, version_probe=None):
    """True only when the running gateway reports a fingerprint different from the file on disk.
    Unknown on either side is not 'stale' (an old gateway without the header is ready.py's case)."""
    running = (version_probe or _running_version)(host)
    on_disk = _file_version(GATEWAY)
    return bool(running and on_disk and running != "unknown" and running != on_disk)


def start_ask(cfg):
    return ask_worker.ensure_running(cfg)


def start_execute(cfg):
    return execute_worker.ensure_running(cfg)


def start_gateway(cfg, probe=None):
    state, _note = gateway_state(cfg, probe=probe)
    if state == STATE_OFF:
        return "skipped"
    if state == STATE_UP:
        return "running"
    proc = spawn_detached([quiet_interpreter(), GATEWAY], cwd=ROOT)
    write_pid(GATEWAY_LOCK, proc.pid)
    return "spawned"


def stop_ask(cfg, kill=os.kill):
    stopped = kill_pid(ask_worker.lock_pid(), kill)
    ask_worker.drop_lock()
    return "stopped" if stopped else "not-running"


def stop_execute(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(execute_worker.lock_path()), kill)
    try:
        os.remove(execute_worker.lock_path())
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


def pid_on_port(port, runner=None):
    """The PID listening on `port`, or None. Injected runner so this is testable.

    Needed because the gateway is the one service that can be running without this module's
    pid file: started by hand from an open terminal, it holds the port and nothing here knows
    its PID. Measured 2026-09-20: a gateway from three days earlier survived `--restart`, which
    reported success -- the port answered, so the state table said "dziala", and the freshly
    deployed code simply never ran.
    """
    run = runner or (lambda args: subprocess.run(args, capture_output=True, timeout=10, **QUIET))
    try:
        if os.name == "nt":
            out = run(["netstat", "-ano", "-p", "TCP"])
        else:
            out = run(["lsof", "-t", "-i", ":%d" % port, "-sTCP:LISTEN"])
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.decode("utf-8", "replace") if isinstance(out.stdout, bytes) else (out.stdout or "")
    return parse_listener_pid(text, port)


def parse_listener_pid(text, port):
    """Pure: the listening PID for `port` in netstat/lsof output, or None.

    Only LISTENING rows count -- an outbound connection to the same port number is not the
    server, and killing it would be killing a client.
    """
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 1 and parts[0].isdigit():      # lsof -t
            return int(parts[0])
        if len(parts) >= 5 and parts[0].upper() == "TCP" and "LISTEN" in parts[3].upper():
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port) and parts[-1].isdigit():
                return int(parts[-1])
    return None


def stop_gateway(cfg, kill=os.kill):
    """Stop whatever is serving, not only what we started.

    The pid file is tried first because it is exact; the port owner is the fallback, and it is
    the case that actually happens after somebody ran the gateway by hand once.
    """
    stopped = kill_pid(read_pid(GATEWAY_LOCK), kill)
    if not stopped:
        stopped = kill_pid(pid_on_port(GATEWAY_PORT), kill)
    try:
        os.remove(GATEWAY_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


def dashboards_state(cfg, now=None, probe=None):
    """Is the dashboard watcher alive? Lock freshness, like the workers -- a loop that sleeps
    five minutes cannot be probed by a port."""
    script = dashboard_script()
    if not script:
        return STATE_OFF, "ta maszyna nie ma klonu HA (nie dotyczy)"
    live = ask_worker.lock_is_live(DASH_LOCK, time.time() if now is None else now, DASH_TTL_S)
    return (STATE_UP if live else STATE_DOWN), \
        "dashboardy HA na zywo po scaleniu (co %d s)" % DASH_INTERVAL_S


def dashboard_python(repo, exists=os.path.exists):
    """The HA venv's pythonw.exe (no console at all), else its python.exe, else ours.

    With python.exe the watcher showed up as a Windows Terminal window titled
    '...\\HA\\.venv\\Scripts\\python.exe' -- and closing that stray-looking window killed the
    service (found dead 2026-10-05). pythonw has no console to show or to close."""
    scripts = os.path.join(repo, ".venv", "Scripts")
    for exe in ("pythonw.exe", "python.exe"):
        cand = os.path.join(scripts, exe)
        if exists(cand):
            return cand
    return quiet_interpreter()


def start_dashboards(cfg):
    script = dashboard_script()
    if not script:
        return "skipped"
    if dashboards_state(cfg)[0] == STATE_UP:
        return "running"
    # The HA repo's own venv: deploy_dashboard.py needs websockets and pyyaml, which the base
    # interpreter here does not have. Same rule as execute_worker._python_for().
    repo = os.path.dirname(os.path.dirname(os.path.dirname(script)))
    python = dashboard_python(repo)
    proc = spawn_detached([python, script, "--watch", str(DASH_INTERVAL_S), "--lock", DASH_LOCK], cwd=repo)
    write_pid(DASH_LOCK, proc.pid)
    return "spawned"


def stop_dashboards(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(DASH_LOCK), kill)
    try:
        os.remove(DASH_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


# -- cli-session-bridge: windows in tmux, ssh/phone access, "do okna" dispatcher ---------------
# Like the gateway, it lives in project_integration's tools/ and does not apply anywhere else.
# --restart replaces the dispatcher and ttyd but never the WSL keepalive: stopping that would shut
# WSL down and kill every detached window -- the running Claudes this exists to keep.

BRIDGE = os.path.join(ROOT, "tools", "cli_bridge.py")


def _bridge():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli_bridge", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bridge_state(cfg, now=None, probe=None):
    if not os.path.exists(BRIDGE):
        return STATE_OFF, "tego repozytorium nie dotyczy (brak tools/cli_bridge.py)"
    cb = _bridge()
    if not cb.host_enabled():
        return STATE_OFF, "maszyna nie jest hostem (python tools/cli_bridge.py --apply)"
    up = cb.runtime_up()
    note = "okna w tmux: ssh, telefon :%d, 'do okna'" % cb.TTYD_PORT
    # 2026-10-22: the work PC's relay served day-old page code after a pull and the table said
    # "dziala" -- the voice fixes never reached that window. Running != running the code on disk.
    if up and getattr(cb, "relay_stale", None) and cb.relay_stale():
        note = ("STARY KOD relaya (plik zmieniony po starcie): python monitor/services.py --restart "
                "--only bridge, potem przeladuj karty okien i wlacz glosnik")
    return (STATE_UP if up else STATE_DOWN), note


def start_bridge(cfg):
    state, _note = bridge_state(cfg)
    if state == STATE_OFF:
        return "skipped"
    if state == STATE_UP:
        return "running"
    _bridge().ensure()
    return "spawned"


def stop_bridge(cfg, kill=os.kill):
    if not os.path.exists(BRIDGE):
        return "not-running"
    _bridge().stop(keepalive=False)
    return "stopped"


# -- VCC (serwer glosow prywatnych) ---------------------------------------------
# VideoContentCreator serwuje prywatne glosy (edyta_gorniak itd.) na 127.0.0.1:8790.
# Bramka glosowa (voice_gateway.py) i wyoming_tts.py (addon na Pi) wolaja go dla kazdego
# Glosowo w wybranym glosie, z fallbackiem do Piper gdy nie odpowiada. Padnie VCC -> wszystko
# fallbackuje do Piper, user slyszy Piper zamiast swojego glosu, "nabu casa milczy" gdy VCC
# cold start przekroczy BACKOFF_S addon'u.
# Komenda: `python <VCC_REPO>/project_files/python/vcc.py serve --no-browser`
VCC_PORT = int(os.environ.get("ASK_VCC_PORT", "8790"))
VCC_LOCK = os.path.join(tempfile.gettempdir(), "monitor_vcc.lock")


def vcc_repo(repo_map=None):
    """Path to VideoContentCreator clone, or None when this machine does not have it."""
    mapping = repo_map if repo_map is not None else ask_worker.load_repo_map()
    repo = mapping.get("VideoContentCreator")
    return repo if repo and os.path.isdir(repo) else None


def vcc_script(repo_map=None):
    repo = vcc_repo(repo_map)
    if not repo:
        return None
    path = os.path.join(repo, "project_files", "python", "vcc.py")
    return path if os.path.exists(path) else None


def _vcc_disabled():
    """True when VOICE_VCC_ENABLED=0 in ~/.claude/voice.env (per-machine opt-out, e.g. laptop)."""
    return _voice_env_value("VOICE_VCC_ENABLED") == "0"


def vcc_state(cfg, now=None, probe=None):
    """Serwer glosow prywatnych na 127.0.0.1:VCC_PORT. Probe tak samo jak bramka -- port otwarty
    = usluga zyje; stary pid file bez portu = DEAD."""
    if not vcc_script():
        return STATE_OFF, "ta maszyna nie ma klonu VideoContentCreator (nie dotyczy)"
    if _vcc_disabled():
        return STATE_OFF, "wylaczone per voice.env (VOICE_VCC_ENABLED=0); glosy z serwera lub edge-tts"
    up = port_open("127.0.0.1", VCC_PORT, connect=probe)
    return (STATE_UP if up else STATE_DOWN), "glosy prywatne na 127.0.0.1:%d (edyta_gorniak itd.); fallback: Piper" % VCC_PORT


# Safe margin for the voice gateway (openspec/changes/voice-safe-margin, 2026-10-26): ONE voice process
# (two held 3.6 + 3.0 GB and left 858 MiB of the 11 GB card free) whose model stays loaded (0 = never
# idle out), so the gateway needs no keep-alive renders. The editor's own defaults stay in VCC.
VCC_ENV = {"VCC_MAX_WORKERS": "1", "VCC_TTS_IDLE_S": "0"}


def start_vcc(cfg, probe=None):
    script = vcc_script()
    if not script:
        return "skipped"
    if _vcc_disabled():
        return "disabled"
    if port_open("127.0.0.1", VCC_PORT, connect=probe):
        return "running"
    repo = vcc_repo()
    proc = spawn_detached([quiet_interpreter(), script, "serve", "--no-browser"], cwd=repo, env=VCC_ENV)
    write_pid(VCC_LOCK, proc.pid)
    return "spawned"


def stop_vcc(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(VCC_LOCK), kill)
    if not stopped:
        stopped = kill_pid(pid_on_port(VCC_PORT), kill)
    try:
        os.remove(VCC_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


# -- gpuwatch (pomiar bierny karty i pamieci) -------------------------------------
# gpu_watch.py co 2 s czyta nvidia-smi (VRAM, obciazenie) i wolna pamiec zatwierdzona hosta i pisze
# ~/.claude/gpu_state.json. Niczego nie renderuje i nie restartuje VCC (od 2026-10-26: bezpieczny
# margines -- sonda byla praca GPU poza kolejka bramki). Bramka decyduje sama (clone_admit).
# Lock-based liveness jak dashboards: petla dotyka locka co probke.
GPUWATCH = os.path.join(HERE, "gpu_watch.py")
GPUWATCH_LOCK = os.path.join(tempfile.gettempdir(), "monitor_gpu_watch.lock")
GPUWATCH_EVERY_S = float(os.environ.get("GPU_WATCH_SAMPLE_S", "2"))
GPUWATCH_LIVE_S = max(GPUWATCH_EVERY_S, 10.0)   # lock poll for liveness: nvidia-smi may take up to 10 s


def gpuwatch_state(cfg, now=None, probe=None):
    """Czujnik GPU zyje? Swiezosc locka, jak workery -- petla spiaca ~90 s nie da sie sprawdzic
    portem. lock_is_live sam mnozy przez LOCK_STALE_POLLS (3), wiec martwy po ~3 cyklach. Nie
    dotyczy maszyny bez VCC albo z VCC wylaczonym (nie ma czego pilnowac)."""
    if not vcc_script():
        return STATE_OFF, "ta maszyna nie ma VCC (nie dotyczy)"
    if _vcc_disabled():
        return STATE_OFF, "VCC wylaczone (VOICE_VCC_ENABLED=0)"
    live = ask_worker.lock_is_live(GPUWATCH_LOCK, time.time() if now is None else now, GPUWATCH_LIVE_S)
    return (STATE_UP if live else STATE_DOWN), \
        "pomiar bierny VRAM/obciazenia/pamieci co %ds (bez renderu, bez restartu VCC)" % int(GPUWATCH_EVERY_S)


def start_gpuwatch(cfg):
    if not vcc_script():
        return "skipped"
    if _vcc_disabled():
        return "disabled"
    if gpuwatch_state(cfg)[0] == STATE_UP:
        return "running"
    proc = spawn_detached([quiet_interpreter(), GPUWATCH], cwd=ROOT)
    write_pid(GPUWATCH_LOCK, proc.pid)
    return "spawned"


def stop_gpuwatch(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(GPUWATCH_LOCK), kill)
    try:
        os.remove(GPUWATCH_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


# -- twins (automat lekkich glosow) -----------------------------------------------
# tools/light_voices.py auto --every 600: nowe glosy z VCC -> nagrania -> douczanie -> test ->
# publikacja na Pi -> mail. Petla nie dotyka locka (etap uczenia trwa godzinami), wiec zywotnosc to
# plik pid z start_twins + sprawdzenie, czy proces o tym pid istnieje (brak locka swiezosci).
TWINS = os.path.join(ROOT, "tools", "light_voices.py")
TWINS_LOCK = os.path.join(tempfile.gettempdir(), "monitor_twins.lock")
TWINS_EVERY_S = 600
TWINS_LOG = os.path.join(ROOT, "project_files", "run_files", "light_voices", "auto.log")


def pid_alive(pid):
    """Does a process with this pid exist? (os.kill(pid, 0) on Windows would send CTRL_C, so ask tasklist.)"""
    if not pid:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True,
                                 timeout=10, **QUIET)
            return (" %d " % pid) in out.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _twins_disabled():
    """True when VOICE_TWINS_ENABLED=0 in ~/.claude/voice.env (per-machine opt-out)."""
    return _voice_env_value("VOICE_TWINS_ENABLED") == "0"


def twins_state(cfg, now=None, probe=None):
    """Automat lekkich glosow. Zywotnosc: pid_alive(pid z TWINS_LOCK) (testy podmieniaja pid_alive)."""
    if not os.path.exists(TWINS):
        return STATE_OFF, "brak tools/light_voices.py (nie dotyczy)"
    if not vcc_repo():
        return STATE_OFF, "ta maszyna nie ma klonu VideoContentCreator (nie dotyczy)"
    if _twins_disabled():
        return STATE_OFF, "wylaczone per voice.env (VOICE_TWINS_ENABLED=0)"
    up = bool(pid_alive(read_pid(TWINS_LOCK)))   # `probe` is the port connector: not for pids
    return (STATE_UP if up else STATE_DOWN), \
        "automat lekkich glosow: nowe glosy z VCC -> nagrania, douczanie, test, publikacja na Pi, mail"


def start_twins(cfg):
    state = twins_state(cfg)[0]
    if state == STATE_OFF:
        return "disabled" if (os.path.exists(TWINS) and vcc_repo() and _twins_disabled()) else "skipped"
    if state == STATE_UP:
        return "running"
    proc = spawn_detached([quiet_interpreter(), TWINS, "auto", "--every", str(TWINS_EVERY_S)],
                          cwd=ROOT, log=TWINS_LOG)
    write_pid(TWINS_LOCK, proc.pid)
    return "spawned"


def stop_twins(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(TWINS_LOCK), kill)
    try:
        os.remove(TWINS_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


# -- mail (zlecenia mailem) -------------------------------------------------------
# tools/mail_inbox.py --loop: co 60 s IMAP skrzynki na zlecenia -> decyzja -> /api/tasks -> odpowiedz
# w watku (openspec/changes/mail-zlecenia). Tylko w repo zbiorczym (tools/) i tylko na maszynie, ktora
# ma liste upowaznionych z polem "inbox" ORAZ haslo tej skrzynki w Menedzerze poswiadczen: bez hasla
# petla nie ma czym sie zalogowac, wiec usluga "nie dotyczy" zamiast padac w kolko.
MAIL_INBOX = os.path.join(ROOT, "tools", "mail_inbox.py")
MAIL_INBOX_LOCK = os.path.join(tempfile.gettempdir(), "monitor_mail_inbox.lock")
MAIL_INBOX_POLL_S = 60
MAIL_AUTH = os.path.join(os.path.expanduser("~"), ".claude", "mail_zlecenia.json")
MAIL_INBOX_LOG = os.path.join(ROOT, "project_files", "run_files", "mail_zlecenia", "service.log")


def _mail_cred_present(user):
    """True when Credential Manager holds a password for `user` (via tools/send_mail.py)."""
    import importlib.util
    path = os.path.join(ROOT, "tools", "send_mail.py")
    try:
        spec = importlib.util.spec_from_file_location("send_mail", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return bool(mod.cred_read(user))
    except Exception:  # noqa: BLE001
        return False


def mail_inbox_applies(auth_path=None, cred=None, exists=os.path.exists):
    """(True, inbox) or (False, why not). Pure given `cred` and `exists`."""
    if not exists(MAIL_INBOX):
        return False, "brak tools/mail_inbox.py (nie dotyczy)"
    try:
        with open(auth_path or MAIL_AUTH, encoding="utf-8") as f:
            inbox = str((json.load(f) or {}).get("inbox") or "").strip()
    except (OSError, ValueError, AttributeError):
        inbox = ""
    if not inbox:
        return False, "brak ~/.claude/mail_zlecenia.json z polem inbox (nie dotyczy)"
    if not (cred or _mail_cred_present)(inbox):
        return False, "brak hasla skrzynki zlecen w Menedzerze poswiadczen (nie dotyczy)"
    return True, inbox


def mail_inbox_state(cfg, now=None, probe=None, applies=None):
    ok, why = (applies or mail_inbox_applies)()
    if not ok:
        return STATE_OFF, why
    if _cfg_missing(cfg):
        return STATE_OFF, "brak MONITOR_URL/MONITOR_TOKEN w ~/.claude/monitor.env"
    live = ask_worker.lock_is_live(MAIL_INBOX_LOCK, time.time() if now is None else now, MAIL_INBOX_POLL_S)
    return (STATE_UP if live else STATE_DOWN), "zlecenia mailem: skrzynka co %d s -> kolejka zadan" % MAIL_INBOX_POLL_S


def start_mail_inbox(cfg):
    state = mail_inbox_state(cfg)[0]
    if state == STATE_OFF:
        return "skipped"
    if state == STATE_UP:
        return "running"
    proc = spawn_detached([quiet_interpreter(), MAIL_INBOX, "--loop"], cwd=ROOT, log=MAIL_INBOX_LOG)
    write_pid(MAIL_INBOX_LOCK, proc.pid)
    return "spawned"


def stop_mail_inbox(cfg, kill=os.kill):
    stopped = kill_pid(read_pid(MAIL_INBOX_LOCK), kill)
    try:
        os.remove(MAIL_INBOX_LOCK)
    except OSError:
        pass
    return "stopped" if stopped else "not-running"


SERVICES = [
    ("ask", "worker pytan", ask_state, start_ask, stop_ask),
    ("execute", "worker zlecen", execute_state, start_execute, stop_execute),
    ("vcc", "serwer glosow", vcc_state, start_vcc, stop_vcc),
    ("gpuwatch", "czujnik GPU", gpuwatch_state, start_gpuwatch, stop_gpuwatch),
    ("twins", "lekkie glosy", twins_state, start_twins, stop_twins),
    ("voice", "bramka glosowa", gateway_state, start_gateway, stop_gateway),
    ("dashboards", "dashboardy HA", dashboards_state, start_dashboards, stop_dashboards),
    ("bridge", "okna zdalnie", bridge_state, start_bridge, stop_bridge),
    ("mail", "zlecenia mailem", mail_inbox_state, start_mail_inbox, stop_mail_inbox),
]


def snapshot(cfg, now=None, probe=None):
    """[{name, title, state, note}] -- what is up on this machine right now."""
    rows = []
    for name, title, state_fn, _start, _stop in SERVICES:
        state, note = state_fn(cfg, now=now, probe=probe)
        rows.append({"name": name, "title": title, "state": state, "note": note})
    return rows


def table(rows, header=None):
    out = [header] if header else []
    for r in rows:
        mark = {STATE_UP: "[+]", STATE_DOWN: "[ ]", STATE_OFF: "[-]"}[r["state"]]
        out.append("  %s %-8s %-15s %-10s %s"
                   % (mark, r["name"], r["title"], r["state"], r["note"]))
    return "\n".join(out)


def start_all(cfg, only=None):
    """Start what is missing. Returns {name: outcome}. Idempotent: running services are left
    alone. `only` filters to a single service by name (--restart --only voice to not disturb the
    bridge, user 2026-10-13: 8 restarts of the voice gateway in one day killed every browser's
    ttyd WebSocket and the user had to reload each window from HA)."""
    paused = tokens_paused()
    return {name: ("paused" if paused and name in TOKEN_SERVICES else start(cfg))
            for name, _t, _s, start, _stop in SERVICES if only is None or name == only}


# 2026-10-29 (user: "jesli jest jakikolwiek proces zuzywajacy tokeny, spauzuj wszystkie"): services that can START
# model work on their own (a task from the queue, a mail order). With the pause file they stay down, also after the
# session-start hook runs `--start`. Idle windows and the voice gateway (user-initiated questions) are not stopped.
TOKEN_SERVICES = ("ask", "execute", "mail")
PAUSE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "pause_tokens")


def tokens_paused(path=None):
    return os.path.exists(path or PAUSE_FILE)


def set_pause(on, path=None):
    path = path or PAUSE_FILE
    if on:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    elif os.path.exists(path):
        os.remove(path)


def stop_all(cfg, kill=os.kill, only=None):
    return {name: stop(cfg, kill) for name, _t, _s, _start, stop in SERVICES
            if only is None or name == only}


def wait_until_up(cfg, names, deadline_s=6.0, sleep=time.sleep, probe=None):
    """A spawned process needs a moment before its lock or its port exists. Without this the
    table printed right after --start says 'nie dziala' about something that is starting fine."""
    end = time.time() + deadline_s
    while time.time() < end:
        rows = {r["name"]: r["state"] for r in snapshot(cfg, probe=probe)}
        if all(rows.get(n) != STATE_DOWN for n in names):
            return True
        sleep(0.4)
    return False


def wait_until_down(names, deadline_s=3.0, sleep=time.sleep, probe=None):
    """After a kill signal the port is held briefly. Wait before starting a replacement.
    Only checks the gateway port (the one service that uses a port, not a lock). Pure logic,
    tested via probe injection. Called in the restart path to fix a race: stop_gateway sends
    TerminateProcess, start_gateway checks the port before the old process releases it, sees
    STATE_UP and returns 'running' -- so no new process is spawned and the table still shows
    STARY KOD after restart (found 2026-10-24)."""
    host = gateway_bind()
    end = time.time() + deadline_s
    while time.time() < end:
        connect = probe or (lambda h, p: port_open(h, p))
        if not connect(host, GATEWAY_PORT):
            return True
        sleep(0.3)
    return False


DELEGATE_TASK = "ClaudeServicesRemoteStart"


def must_delegate(env, os_name):
    """True when services would be started from an ssh shell on Windows. Pure.

    Found 2026-10-11 (GEIH38C, reported by the VCC session): `ready.py --apply` over ssh
    restarted every service as a child of the ssh session; OpenSSH killed them all on
    disconnect and the gateway was down for minutes. DETACHED_PROCESS does not leave the
    session's job object."""
    return os_name == "nt" and bool(env.get("SSH_CONNECTION")) and not env.get("SERVICES_NO_DELEGATE")


def delegate_to_desktop(argv):
    """Run the same command as a one-shot task in the logged-on user's session (/it): there it
    survives the ssh disconnect, sees the user's credentials, and starts exactly as at logon."""
    py = sys.executable.replace("python.exe", "pythonw.exe")
    cmd = '"%s" "%s" %s' % (py if os.path.exists(py) else sys.executable, os.path.abspath(__file__),
                            " ".join(a for a in argv if a != "--json"))
    run = lambda a: subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace", **QUIET)  # noqa: E731
    created = run(["schtasks", "/Create", "/F", "/TN", DELEGATE_TASK, "/SC", "ONCE", "/ST", "23:59",
                   "/IT", "/TR", cmd])
    started = created.returncode == 0 and run(["schtasks", "/Run", "/TN", DELEGATE_TASK]).returncode == 0
    if created.returncode == 0:
        time.sleep(2)  # the running instance survives the delete; a kept "once" task would fire again at 23:59
        run(["schtasks", "/Delete", "/F", "/TN", DELEGATE_TASK])
    if started:
        print("zdalna powloka (ssh): uslugi startuja w sesji uzytkownika przez zadanie %s -- "
              "sprawdz za chwile: python monitor/services.py" % DELEGATE_TASK)
        return 0
    print("zdalna powloka (ssh) i nie udalo sie przekazac startu do sesji uzytkownika: %s -- "
          "uslugi uruchomione stad zginelyby po rozlaczeniu, wiec ich nie startuje"
          % (created.stderr or created.stdout).strip()[:200])
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", action="store_true", help="start whatever is missing")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--restart", action="store_true", help="stop, then start (after a pack update)")
    ap.add_argument("--only", metavar="SERVICE", default=None,
                    help="limit --start/--stop/--restart to one service (voice, bridge, ask, ...)")
    ap.add_argument("--pause", action="store_true",
                    help="stop ask/execute/mail and keep them down (also after --start) until --resume")
    ap.add_argument("--resume", action="store_true", help="lift --pause and start them again")
    ap.add_argument("--json", action="store_true")
    # A hook runs on every session start and must neither print nor block: it spawns what is
    # missing and returns. Waiting for a lock to appear would put seconds on top of opening a
    # window, for an answer nobody reads.
    ap.add_argument("--quiet", action="store_true", help="start, print nothing, do not wait (hooks)")
    args = ap.parse_args(argv)

    if (args.start or args.restart) and must_delegate(os.environ, os.name):
        return delegate_to_desktop(argv if argv is not None else sys.argv[1:])

    cfg = heartbeat.load_config()

    if args.pause:
        set_pause(True)
        for name in TOKEN_SERVICES:
            stop_all(cfg, only=name)
    if args.resume:
        set_pause(False)
        args.start = True

    if args.stop or args.restart:
        stop_all(cfg, only=args.only)
        if args.restart and (args.only is None or args.only == "voice"):
            wait_until_down(["voice"])  # port release after TerminateProcess is not instant
    if args.start or args.restart:
        outcomes = start_all(cfg, only=args.only)
        spawned = [n for n, o in outcomes.items() if o == "spawned"]
        if spawned and not args.quiet:
            wait_until_up(cfg, spawned)

    if args.quiet:
        return 0

    rows = snapshot(cfg)
    if args.json:
        print(json.dumps({"machine": platform.node(), "services": rows}, ensure_ascii=True))
        return 0

    down = [r for r in rows if r["state"] == STATE_DOWN and not (tokens_paused() and r["name"] in TOKEN_SERVICES)]
    print(table(rows, "uslugi na %s:" % platform.node()))
    if tokens_paused():
        print("\n  WSTRZYMANE (zuzycie tokenow): %s -- wznow: python monitor/services.py --resume" % ", ".join(TOKEN_SERVICES))
    if down and not (args.start or args.restart):
        print("\n  nie dziala: %s -- podnies je: python monitor/services.py --start"
              % ", ".join(r["name"] for r in down))
    return 0


if __name__ == "__main__":
    sys.exit(main())
