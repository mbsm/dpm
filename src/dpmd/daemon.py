#!/usr/bin/env python3
"""DPM daemon — runs on each host to manage and monitor local processes."""

import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import psutil
import yaml

import lcm

from dpmd.cgroups import _resolve_cgroup_base
# Re-export for backwards compatibility with callers that used
# ``from dpmd.daemon import MAX_OUTPUT_CHUNK``.
from dpmd.limits import MAX_OUTPUT_BUFFER, MAX_OUTPUT_CHUNK  # noqa: F401
from dpmd.commands import command_handler
from dpmd.processes import (
    _handle_signal,
    create_process,
    monitor_process,
    start_process,
    stop_process,
)
from dpmd.telemetry import (
    get_ip,
    publish_host_info,
    publish_host_procs,
    publish_procs_outputs,
)


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


class Daemon:
    """LCM-based daemon managing local processes and telemetry on a single host."""

    def __init__(self, config_file: str = "/etc/dpm/dpm.yaml"):
        self.config = self.load_config(config_file)
        self.host_info_channel = self.config["host_info_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]
        self.log_chunks_channel = self.config["log_chunks_channel"]

        # Active live-output subscriptions: {process_name: monotonic_expiry}.
        # Output is published only for processes with a non-expired entry.
        self.output_subscriptions: dict = {}
        self._subscriptions_lock = threading.Lock()
        # Per-process chunk index for unsolicited live publishes.
        self._live_chunk_index: dict = {}
        self.stop_timeout = self.config["stop_timeout"]
        self.max_restarts = int(self.config.get("max_restarts", -1))

        # Configurable stop signal (default SIGINT; SIGKILL escalation unchanged)
        sig_name = self.config.get("stop_signal", "SIGINT")
        self.stop_signal = getattr(signal, sig_name, None)
        if self.stop_signal is None or self.stop_signal in (signal.SIGKILL, signal.SIGSTOP):
            logging.warning("Invalid stop_signal %r, falling back to SIGINT.", sig_name)
            self.stop_signal = signal.SIGINT

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
            or "/var/log/dpm/dpmd.log"
        )

        # Per-process on-disk logs — separate from this daemon's own log.
        # Set process_log_dir to "" (empty) in dpm.yaml to disable disk logging.
        from dpmd.proc_logs import (
            DEFAULT_BACKUPS,
            DEFAULT_DIR,
            DEFAULT_MAX_BYTES,
        )
        cfg_dir = self.config.get("process_log_dir", DEFAULT_DIR)
        self.process_log_dir = cfg_dir if cfg_dir else None
        self.process_log_max_bytes = int(
            self.config.get("process_log_max_bytes", DEFAULT_MAX_BYTES)
        )
        self.process_log_backups = int(
            self.config.get("process_log_backups", DEFAULT_BACKUPS)
        )

        # Process persistence: when enabled, the daemon saves its process
        # registry to disk on every create/delete and reloads on startup.
        # Processes with auto_restart=True are started automatically on reload.
        self._persist = bool(self.config.get("persist_processes", False))
        self._persist_path = (
            self.config.get("persist_path")
            or "/var/lib/dpm/processes.yaml"
        )

        self.hostname = socket.gethostname()
        self._cached_ip = get_ip()
        self._ip_refresh_time = time.monotonic()
        self._cpu_count = psutil.cpu_count()
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
        signal.signal(signal.SIGTERM, lambda signum, frame: _handle_signal(self, signum, frame))
        signal.signal(signal.SIGINT, lambda signum, frame: _handle_signal(self, signum, frame))

        self._lcm_backoff_s = 0.25
        self._init_lcm()

        self.init_logging()
        logging.info(
            "Host initialized with channels: command=%s info=%s persist=%s",
            self.command_channel,
            self.host_info_channel,
            self._persist,
        )

        # Initialize cgroups early — moves daemon PID to a leaf cgroup and
        # enables controllers before any child processes are spawned.
        _resolve_cgroup_base()

        # Check persisted settings first — persistence may have been enabled
        # at runtime in a previous session even if the config file says otherwise.
        self._load_settings()
        if self._persist:
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
        self.lc.subscribe(
            self.command_channel,
            lambda channel, data: command_handler(self, channel, data),
        )

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
        from dpm.config import load_dpm_config

        config = load_dpm_config(config_path, [
            "command_channel",
            "host_info_channel",
            "log_chunks_channel",
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
        """Write data to a YAML file atomically (temp + rename).

        Fsyncs both the file and the containing directory so the rename is
        durable across a crash — without the dir fsync the new name can be
        lost even though the file data is on disk.
        """
        dir_path = os.path.dirname(path) or "."
        os.makedirs(dir_path, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
        dir_fd = os.open(dir_path, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _save_registry(self) -> None:
        """Save process definitions to disk (called on create/delete)."""
        if not self._persist:
            return
        specs = []
        for name, info in self.processes.items():
            specs.append({
                "name": name,
                "exec_command": info.exec_command,
                "group": info.group,
                "auto_restart": info.auto_restart,
                "realtime": info.realtime,
                "isolated": info.isolated,
                "work_dir": info.work_dir,
                "cpuset": info.cpuset,
                "cpu_limit": info.cpu_limit,
                "mem_limit": info.mem_limit,
            })
        try:
            self._atomic_yaml_write(self._persist_path, specs)
            logging.debug("Persisted %d process specs to %s", len(specs), self._persist_path)
        except OSError as e:
            logging.error("Failed to persist process registry: %s", e)

    def _save_settings_with_persist(self, persist_value: bool) -> None:
        """Write settings with an explicit persist flag value."""
        settings = {
            "persist": persist_value,
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

    def _save_settings(self) -> None:
        """Save runtime settings overrides to disk."""
        if not self._persist:
            return
        self._save_settings_with_persist(True)

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
            create_process(
                self,
                name,
                exec_command,
                spec.get("auto_restart", False),
                spec.get("realtime", False),
                spec.get("group", ""),
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
                isolated=bool(spec.get("isolated", False)),
            )
            loaded += 1
            if spec.get("auto_restart", False):
                start_process(self, name)
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

        # Restore the persist flag before anything else — it may have been
        # enabled at runtime in a previous session.
        if settings.get("persist", False):
            self._persist = True

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
            # Temporarily keep _persist on to write the updated settings file,
            # then disable it.
            self._save_settings_with_persist(False)
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
                        monitor_process(self, process_name)
                    except Exception as e:
                        logging.error("monitor_process %s raised: %s", process_name, e, exc_info=True)

            if self.output_timer.timeout():
                try:
                    publish_procs_outputs(self)
                except Exception as e:
                    logging.error("publish_procs_outputs raised: %s", e, exc_info=True)

            if self.host_status_timer.timeout():
                try:
                    publish_host_info(self)
                except Exception as e:
                    logging.error("publish_host_info raised: %s", e, exc_info=True)

            if self.procs_status_timer.timeout():
                try:
                    publish_host_procs(self)
                except Exception as e:
                    logging.error("publish_host_procs raised: %s", e, exc_info=True)

        # Optional: stop everything on shutdown (recommended for systemd)
        logging.info("Stopping managed processes...")
        for name in list(self.processes.keys()):
            try:
                stop_process(self, name)
            except (OSError, RuntimeError, ValueError) as e:
                logging.warning("Shutdown: failed stopping %s: %s", name, e)
