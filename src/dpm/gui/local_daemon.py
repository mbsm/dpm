"""Spawn and stop a local dpmd subprocess from the GUI."""

import logging
import os
import signal
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_last_spawned_proc = None


def spawn_local_daemon(config_path: str = "/etc/dpm/dpm.yaml"):
    """Start a dpmd process in the background.

    Raises RuntimeError if a prior spawn is still alive — the GUI only
    tracks a single handle, so a second spawn without stopping the first
    would leak it (unkillable from the UI).

    Returns (pid, logfile_path).
    """
    global _last_spawned_proc

    if _last_spawned_proc is not None and _last_spawned_proc.poll() is None:
        raise RuntimeError(
            f"Local daemon already running (PID {_last_spawned_proc.pid}); "
            "stop it before spawning another."
        )

    logfile = os.path.join(tempfile.gettempdir(), "dpmd-local.log")
    env = dict(os.environ)
    env["DPM_CONFIG"] = config_path

    with open(logfile, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            ["dpmd"],
            stdout=lf,
            stderr=lf,
            env=env,
            start_new_session=True,
        )

    _last_spawned_proc = proc
    logger.info("Spawned local dpmd PID %d, log -> %s", proc.pid, logfile)
    return proc.pid, logfile


def stop_last_spawned_daemon(timeout: float = 5.0) -> bool:
    """Stop the last spawned local daemon.

    Returns True if terminated gracefully, False if killed.
    Raises RuntimeError if no daemon was spawned.
    """
    global _last_spawned_proc

    if _last_spawned_proc is None:
        raise RuntimeError("No local daemon has been spawned.")

    proc = _last_spawned_proc
    _last_spawned_proc = None

    if proc.poll() is not None:
        logger.info("Local daemon PID %d already exited.", proc.pid)
        return True

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        proc.terminate()

    try:
        proc.wait(timeout=timeout)
        logger.info("Local daemon PID %d terminated gracefully.", proc.pid)
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        proc.wait(timeout=2)
        logger.warning("Local daemon PID %d killed.", proc.pid)
        return False
