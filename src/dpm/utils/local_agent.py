"""Spawn and stop a local dpm-agent subprocess from the GUI."""

import logging
import os
import signal
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_last_spawned_proc = None


def spawn_local_agent(config_path: str = "/etc/dpm/dpm.yaml"):
    """Start a dpm-agent process in the background.

    Returns (pid, logfile_path).
    """
    global _last_spawned_proc

    logfile = os.path.join(tempfile.gettempdir(), "dpm-agent-local.log")
    env = dict(os.environ)
    env["DPM_CONFIG"] = config_path

    with open(logfile, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            ["dpm-agent"],
            stdout=lf,
            stderr=lf,
            env=env,
            start_new_session=True,
        )

    _last_spawned_proc = proc
    logger.info("Spawned local dpm-agent PID %d, log -> %s", proc.pid, logfile)
    return proc.pid, logfile


def stop_last_spawned_agent(timeout: float = 5.0) -> bool:
    """Stop the last spawned local agent.

    Returns True if terminated gracefully, False if killed.
    Raises RuntimeError if no agent was spawned.
    """
    global _last_spawned_proc

    if _last_spawned_proc is None:
        raise RuntimeError("No local agent has been spawned.")

    proc = _last_spawned_proc
    _last_spawned_proc = None

    if proc.poll() is not None:
        logger.info("Local agent PID %d already exited.", proc.pid)
        return True

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        proc.terminate()

    try:
        proc.wait(timeout=timeout)
        logger.info("Local agent PID %d terminated gracefully.", proc.pid)
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        proc.wait(timeout=2)
        logger.warning("Local agent PID %d killed.", proc.pid)
        return False
