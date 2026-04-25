"""Process lifecycle for the DPM daemon.

All functions take the Daemon instance as first argument `d`. No methods.
"""
from __future__ import annotations

import logging
import os
import shlex
import signal
import threading
import time
from dataclasses import dataclass, field
from subprocess import PIPE
from typing import Any, TYPE_CHECKING

import psutil

from dpm.constants import (
    STATE_FAILED,
    STATE_KILLED,
    STATE_READY,
    STATE_RUNNING,
    STATE_SUSPENDED,
)

from dpmd.cgroups import cgroups_available, cleanup_cgroup, setup_cgroup
from dpmd.proc_logs import open_process_log

if TYPE_CHECKING:
    from dpmd.daemon import Daemon


def is_running(proc: psutil.Popen | None) -> bool:
    if proc is None:
        return False
    return proc.poll() is None


def stream_reader(stream, log_file) -> None:
    """Read lines from *stream* and write them to *log_file* (if not None).

    Disk is the single source of truth for process output. The publisher
    tails the same file to push live chunks to subscribers, and read_log
    serves history from it. There is no in-memory buffer.
    """
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.rstrip("\r\n")
            if line and log_file is not None:
                log_file.write(line + "\n")
    except (OSError, ValueError) as e:
        logging.error("Stream Reader: Error reading stream: %s", e)


@dataclass
class Proc:
    """All state for a single managed process.

    Output is *not* held in memory. Both stdout and stderr land directly
    in ``log_file`` (a single chronological stream); the publisher tails
    that file for live subscribers, and ``read_log`` serves history from
    the same file.

    ``errors`` is a short human-readable status string (≤ a couple of
    hundred chars) — never log content. Examples: a pre-launch reason
    like ``"Working directory does not exist: /foo"`` or a post-exit
    summary like ``"exit 134 (SIGABRT)"``.
    """
    exec_command: str = ""
    auto_restart: bool = False
    realtime: bool = False
    rt_priority: int = 0
    isolated: bool = False
    group: str = ""
    work_dir: str = ""
    cpuset: str = ""
    cpu_limit: float = 0.0
    mem_limit: int = 0
    state: str = STATE_READY
    errors: str = ""
    exit_code: int = -1
    restart_count: int = 0
    last_restart_time: float = 0.0
    proc: Any = None       # psutil.Popen | None
    ps_proc: Any = None    # psutil.Process | None
    stdout_thread: Any = None  # threading.Thread | None
    stderr_thread: Any = None  # threading.Thread | None
    log_file: Any = None       # proc_logs.ProcessLogFile | None


def create_process(
    d: "Daemon", process_name, exec_command, auto_restart, realtime, group,
    work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0, isolated=False,
    rt_priority=0,
) -> None:
    """Register a process definition without starting it."""
    existing = d.processes.get(process_name)
    if existing is not None and is_running(existing.proc):
        logging.warning(
            "Create Process: Process %s is running (PID %s); stopping before re-create.",
            process_name, existing.proc.pid,
        )
        stop_process(d, process_name)

    rt_prio = int(rt_priority)
    if rt_prio and not (1 <= rt_prio <= 99):
        # SCHED_FIFO range is 1..99 on Linux; 0 means "use daemon default".
        logging.warning(
            "Create Process: rt_priority %d for %s out of range [1,99]; clamping to default (0).",
            rt_prio, process_name,
        )
        rt_prio = 0

    d.processes[process_name] = Proc(
        exec_command=exec_command,
        auto_restart=bool(auto_restart),
        realtime=bool(realtime),
        rt_priority=rt_prio,
        isolated=bool(isolated),
        group=group,
        work_dir=work_dir,
        cpuset=cpuset,
        cpu_limit=float(cpu_limit),
        mem_limit=int(mem_limit),
    )
    logging.info(
        "Create Process: Created process: %s with command: %s auto_restart: %s realtime: %s rt_priority: %s",
        process_name,
        exec_command,
        auto_restart,
        realtime,
        rt_prio if rt_prio else "default",
    )
    d._save_registry()


def delete_process(d: "Daemon", process_name) -> None:
    """Delete a process definition, stopping it first if needed."""
    if process_name in d.processes:
        if d.processes[process_name].proc is not None:
            stop_process(d, process_name)
        # ensure no stale psutil handle
        d.processes[process_name].ps_proc = None
        # Close the on-disk log handle. The file itself is left in place
        # for post-mortem; rotation will eventually age it out.
        log_file = d.processes[process_name].log_file
        if log_file is not None:
            log_file.write_marker("deleted")
            log_file.close()
        cleanup_cgroup(process_name)
        del d.processes[process_name]
        logging.info("Delete Process: Deleted process: %s", process_name)
        d._save_registry()
    else:
        logging.warning(
            "Delete Process: Process %s not found, ignoring command.", process_name
        )


def start_process(d: "Daemon", process_name) -> None:
    """Start a configured process if it is not already running."""
    if process_name not in d.processes:
        logging.warning(
            "Start Process: Process %s not found in the process table. Ignoring command.",
            process_name,
        )
        return

    proc_info = d.processes[process_name]
    proc = proc_info.proc
    exec_command = proc_info.exec_command
    realtime = proc_info.realtime

    if is_running(proc):
        logging.info(
            "Start Process: Process %s is already running with PID %s. Skipping start.",
            process_name,
            proc.pid,
        )
        return

    # Clear suspended state on manual start
    if proc_info.state == STATE_SUSPENDED:
        proc_info.restart_count = 0
        proc_info.last_restart_time = 0.0
        logging.info(
            "Start Process: Clearing SUSPENDED state for %s.", process_name
        )

    # Join any still-alive reader threads from a previous run so we don't
    # leak threads writing to the file handle on a manual restart.
    for tattr in ("stdout_thread", "stderr_thread"):
        t = getattr(proc_info, tattr, None)
        if t is not None:
            t.join(timeout=2.0)
            setattr(proc_info, tattr, None)

    logging.info(
        "Start Process: Starting process: %s with command: %s",
        process_name,
        exec_command,
    )

    # Open (or reuse) the on-disk log file. One file per process; both
    # stdout and stderr are appended in chronological order. The daemon
    # treats this file as the single source of truth for process output:
    # the publisher tails it for live subscribers, and read_log serves
    # history from it. Disabled if process_log_dir is falsy.
    log_dir = getattr(d, "process_log_dir", None)
    if log_dir and proc_info.log_file is None:
        proc_info.log_file = open_process_log(
            process_name,
            log_dir=log_dir,
            max_bytes=getattr(d, "process_log_max_bytes", 50 * 1024 * 1024),
            backups=getattr(d, "process_log_backups", 3),
        )
    log_file = proc_info.log_file
    if log_file is not None:
        log_file.write_marker(f"start cmd={exec_command!r}")

    work_dir = proc_info.work_dir
    if work_dir and not os.path.isdir(work_dir):
        error_msg = f"Working directory does not exist: {work_dir}"
        logging.error("Start Process: %s", error_msg)
        if log_file is not None:
            log_file.write_marker(f"start failed: {error_msg}")
        proc_info.state = STATE_FAILED
        proc_info.errors = error_msg
        return

    try:
        argv = shlex.split(exec_command)
        popen_kwargs = dict(
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
        if work_dir:
            popen_kwargs["cwd"] = work_dir

        proc = psutil.Popen(argv, **popen_kwargs)
        proc_info.proc = proc
        proc_info.state = STATE_RUNNING
        proc_info.errors = ""

        # Reader threads write child output straight to the log file.
        # ProcessLogFile serializes writes with its own lock, so the two
        # readers can share the handle without interleaving mid-line.
        stdout_thread = threading.Thread(
            target=stream_reader,
            args=(proc.stdout, log_file),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stream_reader,
            args=(proc.stderr, log_file),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        proc_info.stdout_thread = stdout_thread
        proc_info.stderr_thread = stderr_thread

        logging.info(
            "Start Process: Started process: %s with PID %s", process_name, proc.pid
        )

        # Prime CPU sampling via the persistent psutil.Process used in publish_host_procs()
        try:
            proc_info.ps_proc = psutil.Process(proc.pid)
            proc_info.ps_proc.cpu_percent(interval=None)
        except (psutil.Error, OSError, ValueError):
            proc_info.ps_proc = None

        if realtime:
            try:
                rt_prio = proc_info.rt_priority or d.config.get("rt_priority", 40)
                os.sched_setscheduler(proc.pid, os.SCHED_FIFO, os.sched_param(rt_prio))
                logging.info(
                    "Start Process: Set real-time priority for process: %s with PID %s",
                    process_name,
                    proc.pid,
                )
            except PermissionError:
                msg = "Permission denied setting real-time priority."
                logging.error(
                    "Start Process: Failed to set real-time priority for process %s: Permission denied.",
                    process_name,
                )
                if log_file is not None:
                    log_file.write_marker(f"warning: {msg}")
                proc_info.errors = msg
            except (OSError, ValueError) as e:
                logging.error(
                    "Start Process: Failed to set real-time priority for process %s: %s",
                    process_name,
                    e,
                )
                if log_file is not None:
                    log_file.write_marker(f"warning: rt priority: {e}")
                proc_info.errors = str(e)

        # Apply cgroup resource limits (cpuset, CPU, memory, isolation)
        _cpuset = proc_info.cpuset
        _cpu_limit = proc_info.cpu_limit
        _mem_limit = proc_info.mem_limit
        _isolated = proc_info.isolated
        if (_cpuset or _cpu_limit > 0 or _mem_limit > 0) and cgroups_available():
            try:
                setup_cgroup(process_name, proc.pid,
                             cpuset=_cpuset, cpu_limit=_cpu_limit,
                             mem_limit=_mem_limit, isolated=_isolated)
            except (OSError, ValueError) as e:
                err_msg = f"cgroup setup failed: {e}"
                logging.warning(
                    "Start Process: %s for %s (continuing without limits)",
                    err_msg, process_name,
                )
                if log_file is not None:
                    log_file.write_marker(f"warning: {err_msg}")
                proc_info.errors = err_msg

    except (OSError, ValueError, psutil.Error) as e:
        # Mark process as failed and store error
        error_msg = f"Failed to start process {process_name}: {e}"
        logging.error("Start Process: %s", error_msg)
        proc_info.state = STATE_FAILED
        proc_info.errors = str(e)
        proc_info.proc = None
        if log_file is not None:
            log_file.write_marker(f"start failed: {error_msg}")


def stop_process(d: "Daemon", process_name) -> None:
    """Stop a running process and update its state."""
    if process_name not in d.processes:
        logging.warning(
            "Stop Process: Process %s not found, ignoring command.", process_name
        )
        return

    proc_info = d.processes[process_name]
    proc = proc_info.proc

    if proc is None:
        logging.info(
            "Stop Process: Process %s not running, ignoring command.", process_name
        )
        return

    if proc_info.state == STATE_READY:
        logging.info("Stop Process: Process %s is already stopped.", process_name)
        return

    if not is_running(proc):
        logging.info(
            "Stop Process: Process %s (PID %s) already exited, updating state.",
            process_name, proc.pid,
        )
        proc_info.proc = None
        proc_info.ps_proc = None
        proc_info.state = STATE_READY
        proc_info.exit_code = proc.returncode if proc.returncode is not None else -1
        cleanup_cgroup(process_name)
        return

    try:
        # Prefer process-group termination (handles spawned children)
        sent = _kill_process_group(d, proc.pid, d.stop_signal)
        if not sent:
            os.kill(proc.pid, d.stop_signal)

        proc.wait(timeout=d.stop_timeout)
        logging.info(
            "Stop Process: Gracefully stopped process: %s with PID %s",
            process_name,
            proc.pid,
        )
        # Normalize the "killed by the signal we sent" case to exit code 0:
        # a process that dies of SIGTERM/SIGINT returns -15/-2 from wait(),
        # which reads like a crash to UI consumers. The stop was deliberate,
        # so report success. Only spontaneous (!= our signal) exits keep
        # their raw code.
        rc = proc.returncode
        if rc is None:
            proc_info.exit_code = 0
        elif rc < 0 and -rc == int(d.stop_signal):
            proc_info.exit_code = 0
        else:
            proc_info.exit_code = rc
        proc_info.state = STATE_READY

    except psutil.TimeoutExpired:
        # Escalate to SIGKILL for the group
        _kill_process_group(d, proc.pid, signal.SIGKILL)
        try:
            proc.kill()
        except (psutil.Error, OSError, ValueError) as e:
            logging.debug("Stop Process: kill failed for %s: %s", process_name, e)
        try:
            proc.wait(timeout=2)
        except (psutil.Error, OSError, ValueError) as e:
            logging.debug("Stop Process: wait failed for %s: %s", process_name, e)

        logging.warning(
            "Stop Process: Forcefully killed process: %s with PID %s",
            process_name,
            proc.pid,
        )
        proc_info.exit_code = proc.returncode if proc.returncode is not None else -9
        proc_info.state = STATE_KILLED

    finally:
        proc_info.proc = None
        proc_info.ps_proc = None
        cleanup_cgroup(process_name)


def _handle_signal(d: "Daemon", signum, frame) -> None:
    logging.info("Received signal %s; shutting down.", signum)
    d._stop_event.set()


def _kill_process_group(d: "Daemon", pid: int, sig: int) -> bool:
    """
    Kill the whole process group for pid (requires the process was started with start_new_session=True).
    Returns True if a signal was sent, False otherwise.
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return False
    except OSError as e:
        logging.warning("Failed to resolve pgid for pid=%s: %s", pid, e)
        return False

    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except OSError as e:
        logging.warning(
            "Failed to signal process group pgid=%s sig=%s: %s", pgid, sig, e
        )
        return False


def _exit_summary(exit_code: int) -> str:
    """Short human-readable description of an exit, for proc_info.errors."""
    if exit_code == 0:
        return ""
    if exit_code < 0:
        try:
            sig_name = signal.Signals(-exit_code).name
        except ValueError:
            sig_name = f"signal {-exit_code}"
        return f"exit {exit_code} ({sig_name})"
    return f"exit {exit_code}"


def _read_log_tail(log_file, n_bytes: int = 4096) -> str:
    """Read up to *n_bytes* from the end of *log_file*'s on-disk path.

    Used to fill the crash sidecar with recent stderr after a process
    exits. Best-effort: returns "" on any I/O error.
    """
    if log_file is None:
        return ""
    try:
        path = log_file.path
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _check_auto_restart(d: "Daemon", process_name: str, proc_info: "Proc") -> None:
    """Check backoff timer and restart a failed process if ready.

    Called both when a process first fails and on subsequent monitor
    cycles while waiting for the backoff period to elapse.
    """
    restart_count = proc_info.restart_count

    # Circuit breaker: suspend if max restarts exceeded
    if d.max_restarts >= 0 and restart_count >= d.max_restarts:
        proc_info.state = STATE_SUSPENDED
        logging.warning(
            "Monitor Process: Process %s suspended after %d restart attempts.",
            process_name, restart_count,
        )
        return

    elapsed = time.monotonic() - proc_info.last_restart_time
    backoff = min(2 ** restart_count, 60)
    if elapsed < backoff:
        return  # wait for backoff period — will re-enter on next monitor cycle

    proc_info.restart_count = restart_count + 1
    proc_info.last_restart_time = time.monotonic()
    logging.info(
        "Monitor Process: Restarting process %s (attempt %d, backoff %.0fs).",
        process_name, restart_count + 1, backoff,
    )
    start_process(d, process_name)


def monitor_process(d: "Daemon", process_name) -> None:
    """Monitor a running process; reap on exit and trigger auto-restart."""
    if process_name not in d.processes:
        logging.warning(
            "Monitor Process: Called with process %s not in process table.",
            process_name,
        )
        return

    proc_info = d.processes[process_name]
    proc = proc_info.proc

    # Re-entry for backoff: process already failed, waiting to restart
    if proc is None and proc_info.state == STATE_FAILED and proc_info.auto_restart:
        _check_auto_restart(d, process_name, proc_info)
        return

    # Nothing to monitor if not running
    if proc is None or proc_info.state != STATE_RUNNING:
        return

    if is_running(proc):
        return

    # Process has exited — reap, write markers, decide on auto-restart.
    exit_code = proc.poll()
    exit_code = exit_code if exit_code is not None else -1
    proc_info.exit_code = exit_code
    proc_info.proc = None

    log_file = proc_info.log_file
    if exit_code == 0:
        logging.info(
            "Monitor Process: Process %s exited cleanly (code 0).",
            process_name,
        )
        proc_info.state = STATE_READY
        proc_info.restart_count = 0
        proc_info.errors = ""
        if log_file is not None:
            log_file.write_marker("exit code=0")
    else:
        logging.warning(
            "Monitor Process: Process %s failed with exit code: %s",
            process_name,
            exit_code,
        )
        proc_info.state = STATE_FAILED
        proc_info.errors = _exit_summary(exit_code)
        if log_file is not None:
            log_file.write_marker(f"exit code={exit_code}")

    # Wait for reader threads to drain the closed pipes; their writes
    # land in log_file before we move on.
    for tattr in ("stdout_thread", "stderr_thread"):
        t = getattr(proc_info, tattr, None)
        if t is not None:
            t.join(timeout=2.0)
            setattr(proc_info, tattr, None)

    # Forensic breadcrumb: a separate file with exit context plus the
    # last few KB of the on-disk log. Survives log rotation.
    if exit_code != 0 and log_file is not None:
        log_file.write_crash_sidecar(
            exit_code, proc_info.restart_count, _read_log_tail(log_file)
        )

    if proc_info.auto_restart and exit_code != 0:
        _check_auto_restart(d, process_name, proc_info)


def _group_matches(d: "Daemon", process_group: str, target_group: str | None) -> bool:
    pg = (process_group or "").strip()
    tg = (target_group or "").strip()
    if not tg or tg.lower() == "(ungrouped)":
        return pg == "" or pg.lower() == "(ungrouped)"
    return pg == tg


def start_group(d: "Daemon", group: str | None) -> None:
    """Start all processes that belong to a named group."""
    for name, info in d.processes.items():
        if _group_matches(d, info.group, group):
            start_process(d, name)


def stop_group(d: "Daemon", group: str | None) -> None:
    """Stop all processes that belong to a named group."""
    for name, info in d.processes.items():
        if _group_matches(d, info.group, group):
            stop_process(d, name)
