#!/usr/bin/env python3
"""DPM agent — runs on each host to manage and monitor local processes."""

import fcntl
import logging
import logging.handlers
import os
import shlex
import signal
import socket
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from subprocess import PIPE

import psutil
import yaml

import lcm

# Process state constants — single source of truth for the lifecycle.
#
# State machine transitions:
#   READY   →  RUNNING  (start_process succeeds)
#   READY   →  FAILED   (start_process raises)
#   RUNNING →  READY    (clean exit: code 0, or graceful stop via SIGTERM)
#   RUNNING →  FAILED   (non-zero exit detected by monitor_process)
#   RUNNING →  KILLED   (stop_process escalated to SIGKILL)
#   FAILED  →  RUNNING  (manual restart or auto_restart)
#   KILLED  →  RUNNING  (manual restart)
#
STATE_READY = "T"
STATE_RUNNING = "R"
STATE_FAILED = "F"
STATE_KILLED = "K"

# Human-readable labels derived from state codes (single source of truth).
STATE_DISPLAY = {
    STATE_READY: "Ready",
    STATE_RUNNING: "Running",
    STATE_FAILED: "Failed",
    STATE_KILLED: "Killed",
}

# Maximum bytes sent per process per publish cycle. Prevents a chatty process
# from producing LCM messages too large to fragment reliably over UDP.
MAX_OUTPUT_CHUNK = 64 * 1024  # 64 KB

from dpm.agent.cgroups import cgroups_available, cleanup_cgroup, setup_cgroup

try:
    from dpm_msgs import (
        command_t,
        host_info_t,
        host_procs_t,
        proc_info_t,
        proc_output_t,
    )
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Failed to import 'dpm_msgs'.\n"
        "Install the project and run via the installed entry point:\n"
        "  pip install -e .\n"
        "  dpm-agent\n"
        "Or for repo runs without install:\n"
        "  PYTHONPATH=src python -m dpm.agent.agent\n"
    ) from e


def get_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@dataclass
class Timer:
    """Simple periodic timer helper. Uses monotonic clock to be NTP-safe."""
    period: float
    next: float = field(init=False)

    def __post_init__(self):
        self.next = time.monotonic() + self.period

    def timeout(self) -> bool:
        now = time.monotonic()
        if now > self.next:
            # Skip past any missed intervals to avoid burst catch-up
            missed = int((now - self.next) / self.period)
            self.next += (missed + 1) * self.period
            return True
        return False


def set_nonblocking(fd: int) -> None:
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)


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


class Agent:
    """LCM-based agent managing local processes and telemetry on a single host."""

    def __init__(self, config_file: str = "/etc/dpm/dpm.yaml"):
        self.config = self.load_config(config_file)
        self.host_info_channel = self.config["host_info_channel"]
        self.proc_outputs_channel = self.config["proc_outputs_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]
        self.stop_timeout = self.config["stop_timeout"]

        self.monitor_timer = Timer(self.config["monitor_interval"])
        self.output_timer = Timer(self.config["output_interval"])
        self.host_status_timer = Timer(self.config["host_status_interval"])
        self.procs_status_timer = Timer(self.config["procs_status_interval"])
        self.lc_url = self.config["lcm_url"]
        self.command_channel = self.config["command_channel"]

        # Accept both keys; file logging is only used when NOT under systemd
        self.log_file_path = (
            self.config.get("log_file_path")
            or self.config.get("log_file")
            or "/var/log/dpm/dpm-agent.log"
        )

        # Process persistence: when enabled, the agent saves its process
        # registry to disk on every create/delete and reloads on startup.
        # Processes with auto_restart=True are started automatically on reload.
        self._persist = bool(self.config.get("persist_processes", False))
        self._persist_path = (
            self.config.get("persist_path")
            or "/var/lib/dpm/processes.yaml"
        )

        self.hostname = socket.gethostname()
        _net = psutil.net_io_counters()
        self.last_publish_time = time.time()
        self.last_net_tx = _net.bytes_sent
        self.last_net_rx = _net.bytes_recv
        self.processes = {}

        self._stop_event = threading.Event()

        # Track last seen seq per sender to drop UDP duplicates.
        # Key: (hostname, action, name) — value: last accepted seq.
        # Capped at _LAST_SEQ_MAX_KEYS entries; FIFO eviction via OrderedDict.
        self._last_seq: OrderedDict = OrderedDict()
        self._last_seq_lock = threading.Lock()
        self._LAST_SEQ_MAX_KEYS = 1000

        # Graceful shutdown (systemd sends SIGTERM)
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self._lcm_backoff_s = 0.25
        self._init_lcm()

        self.init_logging()
        logging.info(
            "Host initialized with channels: command=%s info=%s persist=%s",
            self.command_channel,
            self.host_info_channel,
            self._persist,
        )

        if self._persist:
            self._load_settings()
            self._load_registry()

    def _init_lcm(self):
        """(Re)initialize LCM and subscriptions."""
        old = getattr(self, "lc", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        self.lc = lcm.LCM(self.lc_url)

        # IMPORTANT: re-subscribe after recreating LCM
        self.lc.subscribe(self.command_channel, self.command_handler)

        logging.info(
            "LCM initialized url=%s command_channel=%s",
            self.lc_url,
            self.command_channel,
        )

    def _handle_lcm_error(self, e: Exception) -> None:
        logging.error(
            "LCM error: %s. Reinitializing LCM in %.2fs...",
            e,
            self._lcm_backoff_s,
        )
        time.sleep(self._lcm_backoff_s)
        self._lcm_backoff_s = min(self._lcm_backoff_s * 2.0, 5.0)  # cap backoff
        try:
            self._init_lcm()
            self._lcm_backoff_s = 0.25  # reset on success
            logging.info("LCM reinitialized successfully.")
        except (OSError, RuntimeError) as e2:
            logging.error("LCM reinit failed: %s", e2)

    def load_config(self, config_path: str) -> dict:
        from dpm.utils.config import load_dpm_config

        config = load_dpm_config(config_path, [
            "command_channel",
            "host_info_channel",
            "proc_outputs_channel",
            "host_procs_channel",
            "stop_timeout",
            "monitor_interval",
            "output_interval",
            "host_status_interval",
            "procs_status_interval",
            "lcm_url",
        ])

        # Validate numeric ranges to prevent tight loops or indefinite hangs
        for interval_key in ("monitor_interval", "output_interval",
                             "host_status_interval", "procs_status_interval"):
            val = config[interval_key]
            if not isinstance(val, (int, float)) or val < 0.05:
                raise ValueError(f"{interval_key} must be a number >= 0.05, got {val!r}")

        stop_timeout = config["stop_timeout"]
        if not isinstance(stop_timeout, (int, float)) or stop_timeout <= 0 or stop_timeout > 300:
            raise ValueError(f"stop_timeout must be a number in (0, 300], got {stop_timeout!r}")

        return config

    def init_logging(self):
        """
        Under systemd/journald: log to stdout/stderr only (journald captures it).
        Otherwise: log to stdout + optional rotating file.
        """
        logger = logging.getLogger()

        cfg_level = os.environ.get("DPM_LOG_LEVEL", "INFO").upper()
        if cfg_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            cfg_level = "INFO"
        level = getattr(logging, cfg_level, logging.INFO)
        logger.setLevel(level)

        # Remove any pre-existing handlers to avoid duplicates
        for h in list(logger.handlers):
            logger.removeHandler(h)

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        is_systemd = bool(
            os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM")
        )

        # Always attach a stream handler (journald or console)
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(fmt)
        stream_handler.setLevel(level)
        logger.addHandler(stream_handler)

        if is_systemd:
            logger.info(
                "Logging initialized for journald (level=%s); file logging disabled.",
                cfg_level,
            )
            return

        # Non-systemd: optional file logging
        log_path = self.log_file_path
        try:
            log_dir = os.path.dirname(log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10 * 1024 * 1024, backupCount=5
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            logger.addHandler(fh)
            logger.info("Logging initialized (level=%s) path=%s", cfg_level, log_path)
        except OSError as e:
            logger.warning("File logging disabled (%s); stdout only.", e)

    # -----------------
    # Persistence (atomic YAML writes)
    # -----------------
    @staticmethod
    def _atomic_yaml_write(path: str, data) -> None:
        """Write data to a YAML file atomically (temp + rename)."""
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)

    def _save_registry(self) -> None:
        """Save process definitions to disk (called on create/delete)."""
        if not self._persist:
            return
        specs = []
        for name, info in self.processes.items():
            specs.append({
                "name": name,
                "exec_command": info["exec_command"],
                "group": info.get("group", ""),
                "auto_restart": info["auto_restart"],
                "realtime": info["realtime"],
            })
        try:
            self._atomic_yaml_write(self._persist_path, specs)
            logging.debug("Persisted %d process specs to %s", len(specs), self._persist_path)
        except OSError as e:
            logging.error("Failed to persist process registry: %s", e)

    def _save_settings(self) -> None:
        """Save runtime settings overrides to disk."""
        if not self._persist:
            return
        settings = {
            "monitor_interval": self.monitor_timer.period,
            "output_interval": self.output_timer.period,
            "host_status_interval": self.host_status_timer.period,
            "procs_status_interval": self.procs_status_timer.period,
        }
        settings_path = os.path.join(os.path.dirname(self._persist_path), "settings.yaml")
        try:
            self._atomic_yaml_write(settings_path, settings)
            logging.debug("Persisted settings to %s", settings_path)
        except OSError as e:
            logging.error("Failed to persist settings: %s", e)

    def _load_registry(self) -> None:
        """Load process definitions from disk on startup."""
        if not os.path.exists(self._persist_path):
            logging.info("No persisted registry at %s", self._persist_path)
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                specs = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logging.error("Failed to load persisted registry: %s", e)
            return
        if not isinstance(specs, list):
            logging.warning("Persisted registry is not a list, ignoring.")
            return

        loaded = 0
        auto_started = 0
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            name = spec.get("name", "")
            exec_command = spec.get("exec_command", "")
            if not name or not exec_command:
                continue
            self.create_process(
                name,
                exec_command,
                spec.get("auto_restart", False),
                spec.get("realtime", False),
                spec.get("group", ""),
            )
            loaded += 1
            if spec.get("auto_restart", False):
                self.start_process(name)
                auto_started += 1

        logging.info(
            "Loaded %d processes from %s (%d auto-started)",
            loaded, self._persist_path, auto_started,
        )

    def _load_settings(self) -> None:
        """Load runtime settings overrides from disk on startup."""
        settings_path = os.path.join(os.path.dirname(self._persist_path), "settings.yaml")
        if not os.path.exists(settings_path):
            return
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logging.error("Failed to load persisted settings: %s", e)
            return
        if not isinstance(settings, dict):
            return

        changed = []
        for key, timer in [
            ("monitor_interval", self.monitor_timer),
            ("output_interval", self.output_timer),
            ("host_status_interval", self.host_status_timer),
            ("procs_status_interval", self.procs_status_timer),
        ]:
            val = settings.get(key)
            if isinstance(val, (int, float)) and val >= 0.05:
                timer.period = float(val)
                changed.append(f"{key}={val}")

        if changed:
            logging.info("Loaded persisted settings: %s", ", ".join(changed))

    def command_handler(self, channel, data) -> None:
        """Handle incoming command messages."""
        msg = command_t.decode(data)

        # Ignore commands not addressed to this host. An empty hostname is
        # treated as a broadcast (applies to all nodes).
        if msg.hostname and msg.hostname != self.hostname:
            return

        # Drop duplicate or reordered UDP commands via monotonic seq.
        # Accept seq==0 when last>0 as a supervisor-restart signal.
        dedup_key = (msg.hostname, msg.action, msg.name)
        with self._last_seq_lock:
            last = self._last_seq.get(dedup_key, -1)
            if msg.seq <= last and not (msg.seq == 0 and last > 0):
                logging.debug("Dropping duplicate command seq=%d key=%s", msg.seq, dedup_key)
                return
            # Evict oldest entry (FIFO) if cap reached
            if dedup_key not in self._last_seq and len(self._last_seq) >= self._LAST_SEQ_MAX_KEYS:
                self._last_seq.popitem(last=False)
            self._last_seq[dedup_key] = msg.seq

        action = msg.action

        if action == "create_process":
            # Call positionally to match Agent.create_process signature
            # Expected order (based on current codebase usage): (name, exec_command, auto_restart, realtime, group)
            self.create_process(
                msg.name, msg.exec_command, msg.auto_restart, msg.realtime, msg.group
            )

        elif action == "start_process":
            self.start_process(msg.name)

        elif action == "stop_process":
            self.stop_process(msg.name)

        elif action == "delete_process":
            self.delete_process(msg.name)

        elif action == "start_group":
            self.start_group(msg.group)

        elif action == "stop_group":
            self.stop_group(msg.group)

        elif action == "set_interval":
            self.set_interval(msg.exec_command)

        elif action == "set_persistence":
            self.set_persistence(msg.exec_command)

        else:
            logging.warning("Unknown action: %s", action)

    def create_process(
        self, process_name, exec_command, auto_restart, realtime, group,
        *, cpuset: str = "", cpu_limit: float = 0.0, mem_limit: int = 0
    ) -> None:
        """Register a process definition without starting it."""
        existing = self.processes.get(process_name)
        if existing is not None and is_running(existing.get("proc")):
            logging.warning(
                "Create Process: Process %s is running (PID %s); stopping before re-create.",
                process_name, existing["proc"].pid,
            )
            self.stop_process(process_name)

        self.processes[process_name] = {
            "proc": None,
            "ps_proc": None,
            "exec_command": exec_command,
            "auto_restart": bool(auto_restart),
            "realtime": bool(realtime),
            "group": group,
            "state": STATE_READY,
            "errors": "",
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "restart_count": 0,
            "last_restart_time": 0.0,
            "cpuset": cpuset,
            "cpu_limit": float(cpu_limit),
            "mem_limit": int(mem_limit),
        }
        logging.info(
            "Create Process: Created process: %s with command: %s auto_restart: %s and realtime: %s",
            process_name,
            exec_command,
            auto_restart,
            realtime,
        )
        self._save_registry()

    def delete_process(self, process_name) -> None:
        """Delete a process definition, stopping it first if needed."""
        if process_name in self.processes:
            if self.processes[process_name]["proc"] is not None:
                self.stop_process(process_name)
            # ensure no stale psutil handle
            self.processes[process_name]["ps_proc"] = None
            cleanup_cgroup(process_name)
            del self.processes[process_name]
            logging.info("Delete Process: Deleted process: %s", process_name)
            self._save_registry()
        else:
            logging.warning(
                "Delete Process: Process %s not found, ignoring command.", process_name
            )

    def start_process(self, process_name) -> None:
        """Start a configured process if it is not already running."""
        if process_name not in self.processes:
            logging.warning(
                "Start Process: Process %s not found in the process table. Ignoring command.",
                process_name,
            )
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]
        exec_command = proc_info["exec_command"]
        realtime = proc_info["realtime"]

        if is_running(proc):
            logging.info(
                "Start Process: Process %s is already running with PID %s. Skipping start.",
                process_name,
                proc.pid,
            )
            return

        # Clear any stale buffered output from a previous run so it isn't
        # re-published after restart and mixed with the new process's output.
        proc_info["stdout"] = ""
        proc_info["stderr"] = ""

        logging.info(
            "Start Process: Starting process: %s with command: %s",
            process_name,
            exec_command,
        )

        # Set output scaffolding before Popen so monitor_process always finds them
        output_lock = threading.Lock()
        stdout_lines = []
        stderr_lines = []
        proc_info["output_lock"] = output_lock
        proc_info["stdout_lines"] = stdout_lines
        proc_info["stderr_lines"] = stderr_lines

        try:
            argv = shlex.split(exec_command)
            proc = psutil.Popen(
                argv,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            proc_info["proc"] = proc
            proc_info["state"] = STATE_RUNNING
            proc_info["errors"] = ""
            proc_info["restart_count"] = 0

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

            logging.info(
                "Start Process: Started process: %s with PID %s", process_name, proc.pid
            )

            # Prime CPU sampling via the persistent psutil.Process used in publish_host_procs()
            try:
                proc_info["ps_proc"] = psutil.Process(proc.pid)
                proc_info["ps_proc"].cpu_percent(interval=None)
            except (psutil.Error, OSError, ValueError):
                proc_info["ps_proc"] = None

            if realtime:
                try:
                    rt_prio = self.config.get("rt_priority", 40)
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
                    self.processes[process_name][
                        "errors"
                    ] = "Permission denied setting real-time priority."
                except (OSError, ValueError) as e:
                    logging.error(
                        "Start Process: Failed to set real-time priority for process %s: %s",
                        process_name,
                        e,
                    )
                    self.processes[process_name]["errors"] = str(e)

            # Apply cgroup resource limits (cpuset, CPU, memory)
            _cpuset = proc_info.get("cpuset", "")
            _cpu_limit = proc_info.get("cpu_limit", 0.0)
            _mem_limit = proc_info.get("mem_limit", 0)
            if (_cpuset or _cpu_limit > 0 or _mem_limit > 0) and cgroups_available():
                try:
                    setup_cgroup(process_name, proc.pid,
                                 cpuset=_cpuset, cpu_limit=_cpu_limit, mem_limit=_mem_limit)
                except OSError as e:
                    logging.warning(
                        "Start Process: cgroup setup failed for %s: %s (continuing without limits)",
                        process_name, e,
                    )

        except (OSError, ValueError, psutil.Error) as e:
            # Mark process as failed and store error
            error_msg = f"Failed to start process {process_name}: {e}"
            logging.error("Start Process: %s", error_msg)
            proc_info["state"] = STATE_FAILED
            proc_info["errors"] = str(e)
            proc_info["proc"] = None

            # Publish startup error to proc_outputs so GUI can show it
            try:
                msg = proc_output_t()
                msg.timestamp = int(time.time() * 1e6)
                msg.name = process_name
                msg.hostname = self.hostname
                msg.group = proc_info.get("group", "")
                msg.stdout = ""
                msg.stderr = error_msg
                self.lc.publish(self.proc_outputs_channel, msg.encode())
                logging.debug(
                    "Start Process: Published startup error output for %s", process_name
                )
            except OSError as pub_e:
                logging.error(
                    "Start Process: Failed to publish startup error for %s: %s",
                    process_name,
                    pub_e,
                )

    def stop_process(self, process_name) -> None:
        """Stop a running process and update its state."""
        if process_name not in self.processes:
            logging.warning(
                "Stop Process: Process %s not found, ignoring command.", process_name
            )
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]

        if proc is None:
            logging.info(
                "Stop Process: Process %s not running, ignoring command.", process_name
            )
            return

        if proc_info["state"] == STATE_READY:
            logging.info("Stop Process: Process %s is already stopped.", process_name)
            return

        try:
            # Prefer process-group termination (handles spawned children)
            sent = self._kill_process_group(proc.pid, signal.SIGTERM)
            if not sent:
                proc.terminate()

            proc.wait(timeout=self.stop_timeout)
            logging.info(
                "Stop Process: Gracefully stopped process: %s with PID %s",
                process_name,
                proc.pid,
            )
            proc_info["exit_code"] = proc.returncode if proc.returncode is not None else 0
            proc_info["state"] = STATE_READY

        except psutil.TimeoutExpired:
            # Escalate to SIGKILL for the group
            self._kill_process_group(proc.pid, signal.SIGKILL)
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
            proc_info["exit_code"] = proc.returncode if proc.returncode is not None else -9
            proc_info["state"] = STATE_KILLED

        finally:
            proc_info["proc"] = None
            proc_info["ps_proc"] = None
            cleanup_cgroup(process_name)

    def _handle_signal(self, signum, frame) -> None:
        logging.info("Received signal %s; shutting down.", signum)
        self._stop_event.set()

    def _kill_process_group(self, pid: int, sig: int) -> bool:
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

    def monitor_process(self, process_name) -> None:
        """Monitor a running process and publish any buffered output."""
        if process_name not in self.processes:
            logging.warning(
                "Monitor Process: Called with process %s not in process table.",
                process_name,
            )
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]

        # Nothing to monitor if not running
        if proc is None or proc_info["state"] != STATE_RUNNING:
            return

        if not is_running(proc):
            exit_code = proc.poll()
            exit_code = exit_code if exit_code is not None else -1
            proc_info["exit_code"] = exit_code
            proc_info["proc"] = None

            if exit_code == 0:
                logging.info(
                    "Monitor Process: Process %s exited cleanly (code 0).",
                    process_name,
                )
                proc_info["state"] = STATE_READY
            else:
                logging.warning(
                    "Monitor Process: Process %s failed with exit code: %s",
                    process_name,
                    exit_code,
                )
                proc_info["state"] = STATE_FAILED

            # Capture any remaining output
            output_lock = proc_info["output_lock"]
            with output_lock:
                stdout_content = "".join(proc_info["stdout_lines"])
                stderr_content = "".join(proc_info["stderr_lines"])
                proc_info["stdout_lines"].clear()
                proc_info["stderr_lines"].clear()

            if stdout_content or stderr_content:
                proc_info["errors"] = stdout_content + stderr_content

                # Publish the captured output to LCM immediately
                try:
                    msg = proc_output_t()
                    msg.timestamp = int(time.time() * 1e6)
                    msg.name = process_name
                    msg.hostname = self.hostname
                    msg.group = proc_info["group"]
                    msg.stdout = stdout_content
                    msg.stderr = stderr_content
                    self.lc.publish(self.proc_outputs_channel, msg.encode())
                except OSError as pub_e:
                    logging.error(
                        "Monitor Process: Failed to publish output for %s: %s",
                        process_name,
                        pub_e,
                    )
            elif exit_code != 0:
                proc_info["errors"] = f"Process exited with code {exit_code}."

            # Auto-restart only on failure (non-zero exit), with exponential backoff
            if proc_info["auto_restart"] and exit_code != 0:
                restart_count = proc_info.get("restart_count", 0)
                elapsed = time.monotonic() - proc_info.get("last_restart_time", 0.0)
                backoff = min(2 ** restart_count, 60)
                if elapsed < backoff:
                    return  # wait for backoff period
                proc_info["restart_count"] = restart_count + 1
                proc_info["last_restart_time"] = time.monotonic()
                logging.info(
                    "Monitor Process: Restarting process %s (attempt %d, backoff %.0fs).",
                    process_name, restart_count + 1, backoff,
                )
                self.start_process(process_name)
            return

        # Still running: pull any accumulated stream output into stdout/stderr buffers
        output_lock = proc_info["output_lock"]
        with output_lock:
            stdout_content = "".join(proc_info["stdout_lines"])
            stderr_content = "".join(proc_info["stderr_lines"])
            if stdout_content:
                proc_info["stdout"] += stdout_content
            if stderr_content:
                proc_info["stderr"] += stderr_content
            proc_info["stdout_lines"].clear()
            proc_info["stderr_lines"].clear()

    def publish_host_info(self) -> None:
        """Publish host-wide telemetry (CPU, memory, network)."""
        current_time = time.time()
        time_diff = current_time - self.last_publish_time
        self.last_publish_time = current_time

        net_io = psutil.net_io_counters()
        net_tx = net_io.bytes_sent
        net_tx_diff = net_tx - self.last_net_tx
        self.last_net_tx = net_tx

        net_rx = net_io.bytes_recv
        net_rx_diff = net_rx - self.last_net_rx
        self.last_net_rx = net_rx

        sent_kbps = net_tx_diff / time_diff if time_diff > 0 else 0
        recv_kbps = net_rx_diff / time_diff if time_diff > 0 else 0

        cpu_usage = psutil.cpu_percent(interval=None) / 100.0
        uptime = int(time.time() - psutil.boot_time())
        mem = psutil.virtual_memory()

        msg = host_info_t()
        msg.timestamp = int(time.time() * 1e6)
        msg.hostname = self.hostname
        msg.ip = get_ip()
        msg.cpus = psutil.cpu_count()
        msg.cpu_usage = cpu_usage
        msg.mem_total = mem.total
        msg.mem_free = mem.free
        msg.mem_used = mem.used
        msg.mem_usage = mem.percent / 100.0
        msg.network_sent = sent_kbps / 1024
        msg.network_recv = recv_kbps / 1024
        msg.uptime = uptime
        msg.report_interval = self.host_status_timer.period
        msg.persist = self._persist

        try:
            self.lc.publish(self.host_info_channel, msg.encode())
        except OSError as e:
            logging.error("Failed to publish host info: %s", e)
            self._handle_lcm_error(e)

    def _htop_priority(self, pid: int) -> int:
        """
        Match htop/top PRI column:
          - RT tasks (SCHED_FIFO/RR): negative rtprio (e.g., -40)
          - Normal tasks: 20 + nice (nice=0 -> 20)
        """
        policy = os.sched_getscheduler(pid)
        if policy in (os.SCHED_FIFO, os.SCHED_RR):
            rtprio = int(os.sched_getparam(pid).sched_priority)
            return -rtprio

        nice = int(psutil.Process(pid).nice())
        return 20 + nice

    def _ensure_psutil_proc(self, proc_info: dict, pid: int) -> psutil.Process | None:
        p = proc_info.get("ps_proc")
        if p is not None:
            return p

        try:
            p = psutil.Process(pid)
            p.cpu_percent(interval=None)
            proc_info["ps_proc"] = p
            return p
        except (psutil.Error, OSError, ValueError):
            return None

    @staticmethod
    def _zero_proc_metrics(msg_proc: proc_info_t) -> None:
        msg_proc.cpu = 0.0
        msg_proc.mem_rss = 0
        msg_proc.mem_vms = 0
        msg_proc.priority = -1
        msg_proc.pid = -1
        msg_proc.ppid = -1
        msg_proc.runtime = 0

    def _fill_proc_metrics(
        self, msg_proc: proc_info_t, proc_info: dict, pid: int
    ) -> None:
        p = self._ensure_psutil_proc(proc_info, pid)
        if p is None:
            self._zero_proc_metrics(msg_proc)
            return

        try:
            msg_proc.cpu = float(p.cpu_percent(interval=None)) / 100.0
        except (psutil.Error, OSError, ValueError):
            msg_proc.cpu = 0.0

        try:
            mi = p.memory_info()
            msg_proc.mem_rss = int(mi.rss // 1024)
            msg_proc.mem_vms = int(mi.vms // 1024)
        except (psutil.Error, OSError, ValueError):
            msg_proc.mem_rss = 0
            msg_proc.mem_vms = 0

        try:
            msg_proc.priority = int(self._htop_priority(pid))
        except (psutil.Error, OSError, ValueError):
            msg_proc.priority = 0

        try:
            msg_proc.ppid = int(p.ppid())
        except (psutil.Error, OSError, ValueError):
            msg_proc.ppid = -1

        try:
            msg_proc.runtime = int(time.time() - p.create_time())
        except (psutil.Error, OSError, ValueError):
            msg_proc.runtime = 0

    def publish_host_procs(self) -> None:
        """Publish process-level telemetry for all managed processes."""
        msg = host_procs_t()
        msg.timestamp = int(time.time() * 1e6)
        msg.hostname = self.hostname
        msg.procs = []
        msg.num_procs = 0

        for process_name, proc_info in self.processes.items():
            msg_proc = proc_info_t()

            msg_proc.name = process_name
            msg_proc.group = proc_info["group"]
            msg_proc.hostname = self.hostname
            msg_proc.state = proc_info["state"]
            msg_proc.status = STATE_DISPLAY.get(proc_info["state"], "Ready").lower()
            msg_proc.errors = proc_info["errors"]
            msg_proc.exec_command = proc_info["exec_command"]
            msg_proc.auto_restart = proc_info["auto_restart"]
            msg_proc.realtime = proc_info["realtime"]
            msg_proc.exit_code = int(proc_info["exit_code"])

            proc = proc_info["proc"]
            if proc is not None and is_running(proc):
                pid = int(proc.pid)
                msg_proc.pid = pid
                self._fill_proc_metrics(msg_proc, proc_info, pid)
            else:
                self._zero_proc_metrics(msg_proc)

            msg.procs.append(msg_proc)
            msg.num_procs += 1

        try:
            self.lc.publish(self.host_procs_channel, msg.encode())
        except OSError as e:
            logging.error("Failed to publish host procs: %s", e)
            self._handle_lcm_error(e)

    def publish_procs_outputs(self) -> None:
        """Publish buffered stdout/stderr chunks for all processes.

        At most MAX_OUTPUT_CHUNK bytes are sent per stream per call; any
        remaining bytes stay in the buffer and are sent on the next cycle.
        """
        for process_name, proc_info in self.processes.items():
            stdout = proc_info["stdout"]
            stderr = proc_info["stderr"]
            if not stdout and not stderr:
                continue

            stdout_chunk = stdout[:MAX_OUTPUT_CHUNK]
            stderr_chunk = stderr[:MAX_OUTPUT_CHUNK]

            msg = proc_output_t()
            msg.timestamp = int(time.time() * 1e6)
            msg.name = process_name
            msg.hostname = self.hostname
            msg.group = proc_info["group"]
            msg.stdout = stdout_chunk
            msg.stderr = stderr_chunk
            try:
                self.lc.publish(self.proc_outputs_channel, msg.encode())
            except OSError as e:
                logging.error("Failed to publish proc output for %s: %s", process_name, e)
                self._handle_lcm_error(e)
                return  # stop publishing this cycle; LCM will be reinitialized

            proc_info["stdout"] = stdout[len(stdout_chunk):]
            proc_info["stderr"] = stderr[len(stderr_chunk):]

    def _group_matches(self, process_group: str, target_group: str | None) -> bool:
        pg = (process_group or "").strip()
        tg = (target_group or "").strip()
        if not tg or tg.lower() == "(ungrouped)":
            return pg == "" or pg.lower() == "(ungrouped)"
        return pg == tg

    def start_group(self, group: str | None) -> None:
        """Start all processes that belong to a named group."""
        for name, info in self.processes.items():
            if self._group_matches(info.get("group", ""), group):
                self.start_process(name)

    def stop_group(self, group: str | None) -> None:
        """Stop all processes that belong to a named group."""
        for name, info in self.processes.items():
            if self._group_matches(info.get("group", ""), group):
                self.stop_process(name)

    def set_persistence(self, value_str: str) -> None:
        """Enable or disable process registry persistence at runtime.

        The value is 'on' or 'off' (passed via exec_command field).
        """
        val = (value_str or "").strip().lower()
        if val in ("on", "true", "1"):
            self._persist = True
            self._save_registry()
            self._save_settings()
            logging.info("Persistence enabled (path: %s)", self._persist_path)
        elif val in ("off", "false", "0"):
            self._persist = False
            logging.info("Persistence disabled")
        else:
            logging.warning("set_persistence: invalid value %r (expected on/off)", value_str)

    def set_interval(self, value_str: str) -> None:
        """Set all telemetry/monitor intervals to a new value (seconds).

        The value is passed as a string (via exec_command field of command_t).
        """
        try:
            seconds = float(value_str)
        except (TypeError, ValueError):
            logging.warning("set_interval: invalid value %r", value_str)
            return
        if seconds < 0.05:
            logging.warning("set_interval: value %.2f too small (min 0.05)", seconds)
            return

        for timer in (self.monitor_timer, self.output_timer,
                      self.host_status_timer, self.procs_status_timer):
            timer.period = seconds

        logging.info("Telemetry interval set to %.1fs", seconds)
        self._save_settings()

    def run(self) -> None:
        """Main event loop for monitoring and publishing."""
        logging.info("Host running.")
        while not self._stop_event.is_set():
            try:
                self.lc.handle_timeout(50)
            except OSError as e:
                self._handle_lcm_error(e)
                continue

            if self.monitor_timer.timeout():
                for process_name in list(self.processes.keys()):
                    try:
                        self.monitor_process(process_name)
                    except Exception as e:
                        logging.error("monitor_process %s raised: %s", process_name, e, exc_info=True)

            if self.output_timer.timeout():
                try:
                    self.publish_procs_outputs()
                except Exception as e:
                    logging.error("publish_procs_outputs raised: %s", e, exc_info=True)

            if self.host_status_timer.timeout():
                try:
                    self.publish_host_info()
                except Exception as e:
                    logging.error("publish_host_info raised: %s", e, exc_info=True)

            if self.procs_status_timer.timeout():
                try:
                    self.publish_host_procs()
                except Exception as e:
                    logging.error("publish_host_procs raised: %s", e, exc_info=True)

        # Optional: stop everything on shutdown (recommended for systemd)
        logging.info("Stopping managed processes...")
        for name in list(self.processes.keys()):
            try:
                self.stop_process(name)
            except (OSError, RuntimeError, ValueError) as e:
                logging.warning("Shutdown: failed stopping %s: %s", name, e)


def main() -> None:
    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")
    agent = Agent(config_file=config_path)
    agent.run()


if __name__ == "__main__":
    main()
