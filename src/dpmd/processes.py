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

try:
    from dpm_msgs import proc_output_t
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Failed to import 'dpm_msgs'. Install the project via 'pip install -e .'."
    ) from e

if TYPE_CHECKING:
    from dpmd.daemon import Daemon


def is_running(proc: psutil.Popen | None) -> bool:
    if proc is None:
        return False
    return proc.poll() is None


def stream_reader(stream, output_list, lock: threading.Lock) -> None:
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            # line is str when Popen(text=True); keep compatibility if bytes
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.rstrip("\r\n")
            if line:
                with lock:
                    output_list.append(line + "\n")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug("Stream Reader: Captured line: %r", line)
    except (OSError, ValueError) as e:
        logging.error("Stream Reader: Error reading stream: %s", e)


@dataclass
class Proc:
    """All state for a single managed process."""
    exec_command: str = ""
    auto_restart: bool = False
    realtime: bool = False
    isolated: bool = False
    group: str = ""
    work_dir: str = ""
    cpuset: str = ""
    cpu_limit: float = 0.0
    mem_limit: int = 0
    state: str = STATE_READY
    errors: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    restart_count: int = 0
    last_restart_time: float = 0.0
    proc: Any = None       # psutil.Popen | None
    ps_proc: Any = None    # psutil.Process | None
    output_lock: Any = None  # threading.Lock | None
    stdout_lines: list = field(default_factory=list)
    stderr_lines: list = field(default_factory=list)
    stdout_thread: Any = None  # threading.Thread | None
    stderr_thread: Any = None  # threading.Thread | None


def create_process(
    d: "Daemon", process_name, exec_command, auto_restart, realtime, group,
    work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0, isolated=False,
) -> None:
    """Register a process definition without starting it."""
    existing = d.processes.get(process_name)
    if existing is not None and is_running(existing.proc):
        logging.warning(
            "Create Process: Process %s is running (PID %s); stopping before re-create.",
            process_name, existing.proc.pid,
        )
        stop_process(d, process_name)

    d.processes[process_name] = Proc(
        exec_command=exec_command,
        auto_restart=bool(auto_restart),
        realtime=bool(realtime),
        isolated=bool(isolated),
        group=group,
        work_dir=work_dir,
        cpuset=cpuset,
        cpu_limit=float(cpu_limit),
        mem_limit=int(mem_limit),
    )
    logging.info(
        "Create Process: Created process: %s with command: %s auto_restart: %s and realtime: %s",
        process_name,
        exec_command,
        auto_restart,
        realtime,
    )
    d._save_registry()


def delete_process(d: "Daemon", process_name) -> None:
    """Delete a process definition, stopping it first if needed."""
    if process_name in d.processes:
        if d.processes[process_name].proc is not None:
            stop_process(d, process_name)
        # ensure no stale psutil handle
        d.processes[process_name].ps_proc = None
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

    # Clear any stale buffered output from a previous run so it isn't
    # re-published after restart and mixed with the new process's output.
    proc_info.stdout = ""
    proc_info.stderr = ""

    logging.info(
        "Start Process: Starting process: %s with command: %s",
        process_name,
        exec_command,
    )

    # Set output scaffolding before Popen so monitor_process always finds them
    output_lock = threading.Lock()
    stdout_lines = []
    stderr_lines = []
    proc_info.output_lock = output_lock
    proc_info.stdout_lines = stdout_lines
    proc_info.stderr_lines = stderr_lines

    work_dir = proc_info.work_dir
    if work_dir and not os.path.isdir(work_dir):
        error_msg = f"Working directory does not exist: {work_dir}"
        logging.error("Start Process: %s", error_msg)
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

        # Start threads to read stdout and stderr.
        # A single lock guards both lists so the drain in monitor_process
        # is always consistent with concurrent appends from the reader threads.
        stdout_thread = threading.Thread(
            target=stream_reader, args=(proc.stdout, stdout_lines, output_lock), daemon=True
        )
        stderr_thread = threading.Thread(
            target=stream_reader, args=(proc.stderr, stderr_lines, output_lock), daemon=True
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
                rt_prio = d.config.get("rt_priority", 40)
                os.sched_setscheduler(proc.pid, os.SCHED_FIFO, os.sched_param(rt_prio))
                logging.info(
                    "Start Process: Set real-time priority for process: %s with PID %s",
                    process_name,
                    proc.pid,
                )
            except PermissionError:
                logging.error(
                    "Start Process: Failed to set real-time priority for process %s: Permission denied.",
                    process_name,
                )
                d.processes[process_name].errors = "Permission denied setting real-time priority."
            except (OSError, ValueError) as e:
                logging.error(
                    "Start Process: Failed to set real-time priority for process %s: %s",
                    process_name,
                    e,
                )
                d.processes[process_name].errors = str(e)

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
                logging.warning(
                    "Start Process: cgroup setup failed for %s: %s (continuing without limits)",
                    process_name, e,
                )

    except (OSError, ValueError, psutil.Error) as e:
        # Mark process as failed and store error
        error_msg = f"Failed to start process {process_name}: {e}"
        logging.error("Start Process: %s", error_msg)
        proc_info.state = STATE_FAILED
        proc_info.errors = str(e)
        proc_info.proc = None

        # Publish startup error to proc_outputs so GUI can show it
        try:
            msg = proc_output_t()
            msg.timestamp = int(time.time() * 1e6)
            msg.name = process_name
            msg.hostname = d.hostname
            msg.group = proc_info.group
            msg.stdout = ""
            msg.stderr = error_msg
            d.lc.publish(d.proc_outputs_channel, msg.encode())
            logging.debug(
                "Start Process: Published startup error output for %s", process_name
            )
        except OSError as pub_e:
            logging.error(
                "Start Process: Failed to publish startup error for %s: %s",
                process_name,
                pub_e,
            )


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
        proc_info.exit_code = proc.returncode if proc.returncode is not None else 0
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


def _drain_output(d: "Daemon", proc_info: "Proc") -> tuple[str, str]:
    """Drain buffered stdout/stderr lines under the output lock.

    Returns (stdout_content, stderr_content) and clears the line buffers.
    """
    output_lock = proc_info.output_lock
    with output_lock:
        stdout_content = "".join(proc_info.stdout_lines)
        stderr_content = "".join(proc_info.stderr_lines)
        proc_info.stdout_lines.clear()
        proc_info.stderr_lines.clear()
    return stdout_content, stderr_content


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
    """Monitor a running process and publish any buffered output."""
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

    if not is_running(proc):
        exit_code = proc.poll()
        exit_code = exit_code if exit_code is not None else -1
        proc_info.exit_code = exit_code
        proc_info.proc = None

        if exit_code == 0:
            logging.info(
                "Monitor Process: Process %s exited cleanly (code 0).",
                process_name,
            )
            proc_info.state = STATE_READY
            proc_info.restart_count = 0
        else:
            logging.warning(
                "Monitor Process: Process %s failed with exit code: %s",
                process_name,
                exit_code,
            )
            proc_info.state = STATE_FAILED

        # Wait for reader threads to finish so all pipe data is captured
        for tattr in ("stdout_thread", "stderr_thread"):
            t = getattr(proc_info, tattr, None)
            if t is not None:
                t.join(timeout=2.0)
                setattr(proc_info, tattr, None)

        # Capture any remaining output
        stdout_content, stderr_content = _drain_output(d, proc_info)

        if stdout_content or stderr_content:
            proc_info.errors = stdout_content + stderr_content

            # Publish the captured output to LCM immediately
            try:
                msg = proc_output_t()
                msg.timestamp = int(time.time() * 1e6)
                msg.name = process_name
                msg.hostname = d.hostname
                msg.group = proc_info.group
                msg.stdout = stdout_content
                msg.stderr = stderr_content
                d.lc.publish(d.proc_outputs_channel, msg.encode())
            except OSError as pub_e:
                logging.error(
                    "Monitor Process: Failed to publish output for %s: %s",
                    process_name,
                    pub_e,
                )
        elif exit_code != 0:
            proc_info.errors = f"Process exited with code {exit_code}."

        # Auto-restart only on failure (non-zero exit), with exponential backoff
        if proc_info.auto_restart and exit_code != 0:
            _check_auto_restart(d, process_name, proc_info)
        return

    # Still running: pull any accumulated stream output into stdout/stderr buffers
    stdout_content, stderr_content = _drain_output(d, proc_info)
    if stdout_content:
        proc_info.stdout += stdout_content
    if stderr_content:
        proc_info.stderr += stderr_content

    # Cap buffers to prevent unbounded memory growth
    from dpmd.daemon import MAX_OUTPUT_BUFFER
    if len(proc_info.stdout) > MAX_OUTPUT_BUFFER:
        proc_info.stdout = proc_info.stdout[-MAX_OUTPUT_BUFFER:]
    if len(proc_info.stderr) > MAX_OUTPUT_BUFFER:
        proc_info.stderr = proc_info.stderr[-MAX_OUTPUT_BUFFER:]


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
