"""What this machine can actually do -- detected, never declared (design D7).

A hand-written capability list goes stale in a week and the router starts sending work into the
void. Everything here answers by looking: is the binary on PATH, does the directory exist.

Only stdlib, ASCII, no network: this runs inside the worker loop on every start.
"""

import os
import shutil
import subprocess

# name -> how to prove it. A probe returns True/False and never raises.
PROBE_TIMEOUT_S = 4.0
# Detection runs inside a DETACHED worker process (no console of its own), so a console-app
# probe (docker, nvidia-smi, pytest) would otherwise flash a brand new visible window each time.
QUIET_SUBPROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _which(name):
    return shutil.which(name) is not None


def _run_ok(args):
    try:
        proc = subprocess.run(args, capture_output=True, timeout=PROBE_TIMEOUT_S, **QUIET_SUBPROCESS)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def has_python():
    return _which("python") or _which("python3")


def has_git():
    return _which("git")


def has_docker():
    # On PATH is not enough: Docker Desktop is often installed and not running, and a worker that
    # claims docker while the daemon is down fails every routed task.
    return _which("docker") and _run_ok(["docker", "info"])


def has_node():
    return _which("node")


def has_gpu():
    return _which("nvidia-smi") and _run_ok(["nvidia-smi", "-L"])


def has_cuda():
    return _which("nvcc") or bool(os.environ.get("CUDA_PATH"))


def has_pytest():
    return _which("pytest") or _run_ok(["python", "-m", "pytest", "--version"])


def has_venv(paths):
    """A repository with its own virtualenv -- the difference between 'tests run' and 'ImportError'."""
    for path in paths or ():
        for name in (".venv", "venv"):
            if os.path.isdir(os.path.join(path, name)):
                return True
    return False


PROBES = [
    ("python", has_python),
    ("git", has_git),
    ("docker", has_docker),
    ("node", has_node),
    ("gpu", has_gpu),
    ("cuda", has_cuda),
    ("pytest", has_pytest),
]


def detect(paths=(), probes=None):
    """The list a worker reports. Sorted, so an unchanged machine produces an unchanged string
    and the audit log stays quiet (design D5)."""
    found = []
    for name, probe in (probes if probes is not None else PROBES):
        try:
            if probe():
                found.append(name)
        except Exception:  # noqa: BLE001 - a broken probe must not stop the worker
            pass
    try:
        if has_venv(paths):
            found.append("venv")
    except OSError:
        pass
    return sorted(found)
