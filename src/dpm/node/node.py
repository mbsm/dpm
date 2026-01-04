#!/usr/bin/env python3
import os
import time
import psutil
from subprocess import PIPE
import logging
import logging.handlers
import socket
import yaml
import fcntl
import sys
import threading
import signal
import shlex
import lcm

# Define process state constants
STATE_READY = "T"
STATE_RUNNING = "R"
STATE_FAILED = "F"
STATE_KILLED = "K"

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
        "  dpm-node\n"
        "Or for repo runs without install:\n"
        "  PYTHONPATH=src python -m dpm.node.node\n"
    ) from e


def get_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class Timer:
    def __init__(self, timeout):
        now = time.time()
        self.t0 = now
        self.period = timeout
        self.next = now + timeout

    def timeout(self):
        now = time.time()
        if now > self.next:
            self.next += self.period
            return True
        return False


def set_nonblocking(fd):
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)


def is_running(proc):
    if proc is None:
        return False
    return proc.poll() is None


def stream_reader(stream, output_list):
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
                output_list.append(line + "\n")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug(f"Stream Reader: Captured line: {repr(line)}")
    except Exception as e:
        logging.error(f"Stream Reader: Error reading stream: {e}")


class NodeAgent:
    def __init__(self, config_file='/etc/dpm/dpm.yaml'):
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
            or "/var/log/dpm/dpm-node.log"
        )

        self.hostname = socket.gethostname()
        self.last_publish_time = 0
        self.last_net_tx = 0
        self.last_net_rx = 0
        self.processes = {}

        self._stop_event = threading.Event()

        # Graceful shutdown (systemd sends SIGTERM)
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self._lcm_backoff_s = 0.25
        self._init_lcm()

        self.init_logging()
        logging.info(f"Host initialized with channels: command={self.command_channel}, info={self.host_info_channel}")

    def _init_lcm(self):
        """(Re)initialize LCM and subscriptions."""
        self.lc = lcm.LCM(self.lc_url)

        # IMPORTANT: re-subscribe after recreating LCM
        self.lc.subscribe(self.command_channel, self.command_handler)

        logging.info(f"LCM initialized url={self.lc_url} command_channel={self.command_channel}")

    def _handle_lcm_error(self, e: Exception):
        logging.error(f"LCM error: {e}. Reinitializing LCM in {self._lcm_backoff_s:.2f}s...")
        time.sleep(self._lcm_backoff_s)
        self._lcm_backoff_s = min(self._lcm_backoff_s * 2.0, 5.0)  # cap backoff
        try:
            self._init_lcm()
            self._lcm_backoff_s = 0.25  # reset on success
            logging.info("LCM reinitialized successfully.")
        except Exception as e2:
            logging.error(f"LCM reinit failed: {e2}")

    def load_config(self, config_path):
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Configuration file {config_path} not found.")
        if not os.access(config_path, os.R_OK):
            raise PermissionError(f"Configuration file {config_path} is not readable.")
        try:
            with open(config_path, "r") as file:
                config = yaml.safe_load(file)
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration file {config_path}: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error loading configuration file {config_path}: {e}")

        required_fields = [
            "command_channel", "host_info_channel", "proc_outputs_channel",
            "host_procs_channel", "stop_timeout", "monitor_interval", "output_interval",
            "host_status_interval", "procs_status_interval", "lcm_url"
        ]
        for field in required_fields:
            if field not in config:
                raise KeyError(f"Missing required configuration field: {field}")
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

        is_systemd = bool(os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"))

        # Always attach a stream handler (journald or console)
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(fmt)
        stream_handler.setLevel(level)
        logger.addHandler(stream_handler)

        if is_systemd:
            logger.info(f"Logging initialized for journald (level={cfg_level}); file logging disabled.")
            return

        # Non-systemd: optional file logging
        log_path = self.log_file_path
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10 * 1024 * 1024, backupCount=5
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            logger.addHandler(fh)
            logger.info(f"Logging initialized (level={cfg_level}) path={log_path}")
        except Exception as e:
            logger.warning(f"File logging disabled ({e}); stdout only.")

    def command_handler(self, channel, data):
        msg = command_t.decode(data)

        # ...existing code (host filter etc)...

        action = msg.action

        if action == "create_process":
            # Call positionally to match existing NodeAgent.create_process signature
            # Expected order (based on current codebase usage): (name, exec_command, auto_restart, realtime, group)
            self.create_process(msg.name, msg.exec_command, msg.auto_restart, msg.realtime, msg.group)

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

        else:
            logging.warning("Unknown action: %s", action)

    def create_process(self, process_name, exec_command, auto_restart, realtime, group):
        """
        Canonical internal schema (dict):
          proc, exec_command, auto_restart, realtime, group, state, status, errors, exit_code, stdout, stderr
        """
        self.processes[process_name] = {
            "proc": None,
            "ps_proc": None,                 # <-- keep a persistent psutil.Process for cpu sampling
            "exec_command": exec_command,
            "auto_restart": bool(auto_restart),
            "realtime": bool(realtime),
            "group": group,
            "state": STATE_READY,
            "status": "stopped",
            "errors": "",
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "stdout_lines": [],
            "stderr_lines": [],
        }
        logging.info(
            "Create Process: Created process: %s with command: %s auto_restart: %s and realtime: %s",
            process_name, exec_command, auto_restart, realtime
        )

    def delete_process(self, process_name):
        if process_name in self.processes:
            if self.processes[process_name]["proc"] is not None:
                self.stop_process(process_name)
            # ensure no stale psutil handle
            self.processes[process_name]["ps_proc"] = None
            del self.processes[process_name]
            logging.info(f"Delete Process: Deleted process: {process_name}")
        else:
            logging.warning(f"Delete Process: Process {process_name} not found, ignoring command.")

    def start_process(self, process_name):
        if process_name not in self.processes:
            logging.warning(
                "Start Process: Process %s not found in the process table. Ignoring command.",
                process_name
            )
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]
        exec_command = proc_info["exec_command"]
        realtime = proc_info["realtime"]

        if is_running(proc):
            logging.info(
                "Start Process: Process %s is already running with PID %s. Skipping start.",
                process_name, proc.pid
            )
            return

        logging.info("Start Process: Starting process: %s with command: %s", process_name, exec_command)
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
            self.processes[process_name]["proc"] = proc
            self.processes[process_name]["state"] = STATE_RUNNING
            self.processes[process_name]["status"] = "running"
            self.processes[process_name]["errors"] = ""

            # Start threads to read stdout and stderr
            stdout_lines = []
            stderr_lines = []
            stdout_thread = threading.Thread(target=stream_reader, args=(proc.stdout, stdout_lines), daemon=True)
            stderr_thread = threading.Thread(target=stream_reader, args=(proc.stderr, stderr_lines), daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            self.processes[process_name]["stdout_lines"] = stdout_lines
            self.processes[process_name]["stderr_lines"] = stderr_lines

            logging.info("Start Process: Started process: %s with PID %s", process_name, proc.pid)

            # Prime CPU sampling via the persistent psutil.Process used in publish_host_procs()
            try:
                proc_info["ps_proc"] = psutil.Process(proc.pid)
                proc_info["ps_proc"].cpu_percent(interval=None)
            except Exception:
                proc_info["ps_proc"] = None

            if realtime:
                try:
                    os.sched_setscheduler(proc.pid, os.SCHED_FIFO, os.sched_param(40))
                    logging.info(
                        "Start Process: Set real-time priority for process: %s with PID %s",
                        process_name, proc.pid
                    )
                except PermissionError:
                    logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: Permission denied.")
                    self.processes[process_name]["errors"] = "Permission denied setting real-time priority."
                except Exception as e:
                    logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: {e}")
                    self.processes[process_name]["errors"] = str(e)

        except Exception as e:
            # Mark process as failed and store error
            error_msg = f"Failed to start process {process_name}: {e}"
            logging.error(f"Start Process: {error_msg}")
            proc_info["state"] = STATE_FAILED
            proc_info["status"] = "failed"
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
                logging.debug(f"Start Process: Published startup error output for {process_name}")
            except Exception as pub_e:
                logging.error(f"Start Process: Failed to publish startup error for {process_name}: {pub_e}")

    def stop_process(self, process_name):
        if process_name not in self.processes:
            logging.warning(f"Stop Process: Process {process_name} not found, ignoring command.")
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]

        if proc is None:
            logging.info(f"Stop Process: Process {process_name} not running, ignoring command.")
            return

        if proc_info["state"] == STATE_READY:
            logging.info(f"Stop Process: Process {process_name} is already stopped.")
            return

        try:
            # Prefer process-group termination (handles spawned children)
            sent = self._kill_process_group(proc.pid, signal.SIGTERM)
            if not sent:
                proc.terminate()

            proc.wait(timeout=self.stop_timeout)
            logging.info(f"Stop Process: Gracefully stopped process: {process_name} with PID {proc.pid}")
            proc_info["exit_code"] = proc.returncode
            proc_info["state"] = STATE_READY
            proc_info["status"] = "stopped"
            proc_info["proc"] = None
            proc_info["ps_proc"] = None  # CLEAR
            return

        except psutil.TimeoutExpired:
            # Escalate to SIGKILL for the group
            self._kill_process_group(proc.pid, signal.SIGKILL)
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

            logging.warning(f"Stop Process: Forcefully killed process: {process_name} with PID {proc.pid}")
            proc_info["exit_code"] = proc.returncode
            proc_info["state"] = STATE_KILLED
            proc_info["status"] = "killed"
            proc_info["proc"] = None
            proc_info["ps_proc"] = None  # CLEAR

    def _handle_signal(self, signum, frame):
        logging.info(f"Received signal {signum}; shutting down.")
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
        except Exception as e:
            logging.warning(f"Failed to resolve pgid for pid={pid}: {e}")
            return False

        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return False
        except Exception as e:
            logging.warning(f"Failed to signal process group pgid={pgid} sig={sig}: {e}")
            return False

    def monitor_process(self, process_name):
        if process_name not in self.processes:
            logging.warning(f"Monitor Process: Called with process {process_name} not in process table.")
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]

        # Nothing to monitor if not running
        if proc is None or proc_info["state"] != STATE_RUNNING:
            return

        if not is_running(proc):
            exit_code = proc.poll()
            logging.warning(f"Monitor Process: Process {process_name} stopped with exit code: {exit_code}")

            proc_info["state"] = STATE_FAILED
            proc_info["status"] = "failed"
            proc_info["exit_code"] = exit_code if exit_code is not None else -1
            proc_info["proc"] = None

            # Capture any remaining output
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
                except Exception as pub_e:
                    logging.error(f"Monitor Process: Failed to publish output for {process_name}: {pub_e}")
            else:
                proc_info["errors"] = "Process stopped unexpectedly."

            if proc_info["auto_restart"]:
                logging.info(f"Monitor Process: Restarting process {process_name}.")
                self.start_process(process_name)
            return

        # Still running: pull any accumulated stream output into stdout/stderr buffers
        stdout_content = "".join(proc_info["stdout_lines"])
        stderr_content = "".join(proc_info["stderr_lines"])
        if stdout_content:
            proc_info["stdout"] += stdout_content
        if stderr_content:
            proc_info["stderr"] += stderr_content
        proc_info["stdout_lines"].clear()
        proc_info["stderr_lines"].clear()

    def publish_host_info(self):
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

        self.lc.publish(self.host_info_channel, msg.encode())

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

    def publish_host_procs(self):
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
            msg_proc.status = proc_info["status"]
            msg_proc.errors = proc_info["errors"]
            msg_proc.exec_command = proc_info["exec_command"]
            msg_proc.auto_restart = proc_info["auto_restart"]
            msg_proc.realtime = proc_info["realtime"]
            msg_proc.exit_code = int(proc_info["exit_code"])

            proc = proc_info["proc"]
            if proc is not None and is_running(proc):
                pid = int(proc.pid)
                msg_proc.pid = pid

                p = proc_info.get("ps_proc")
                if p is None:
                    try:
                        p = psutil.Process(pid)
                        p.cpu_percent(interval=None)  # prime if we missed it
                        proc_info["ps_proc"] = p
                    except Exception:
                        p = None

                if p is None:
                    msg_proc.cpu = 0.0
                    msg_proc.mem_rss = 0
                    msg_proc.mem_vms = 0
                    msg_proc.priority = 0
                    msg_proc.ppid = -1
                    msg_proc.runtime = 0
                else:
                    # CPU: fraction (0..N), GUI will display cpu*100
                    try:
                        msg_proc.cpu = float(p.cpu_percent(interval=None)) / 100.0
                    except Exception:
                        msg_proc.cpu = 0.0

                    # MEM: publish kB to fit int32; GUI _mem_mb() divides by 1024 => MB
                    try:
                        mi = p.memory_info()
                        msg_proc.mem_rss = int(mi.rss // 1024)
                        msg_proc.mem_vms = int(mi.vms // 1024)
                    except Exception:
                        msg_proc.mem_rss = 0
                        msg_proc.mem_vms = 0

                    try:
                        msg_proc.priority = int(self._htop_priority(pid))
                    except Exception:
                        msg_proc.priority = 0

                    try:
                        msg_proc.ppid = int(p.ppid())
                    except Exception:
                        msg_proc.ppid = -1

                    try:
                        msg_proc.runtime = int(time.time() - p.create_time())
                    except Exception:
                        msg_proc.runtime = 0
            else:
                msg_proc.cpu = 0.0
                msg_proc.mem_rss = 0
                msg_proc.mem_vms = 0
                msg_proc.priority = -1
                msg_proc.pid = -1
                msg_proc.ppid = -1
                msg_proc.runtime = 0

            msg.procs.append(msg_proc)
            msg.num_procs += 1

        self.lc.publish(self.host_procs_channel, msg.encode())

    def publish_procs_outputs(self):
        for process_name, proc_info in self.processes.items():
            stdout = proc_info["stdout"]
            stderr = proc_info["stderr"]
            if not stdout and not stderr:
                continue

            msg = proc_output_t()
            msg.timestamp = int(time.time() * 1e6)
            msg.name = process_name
            msg.hostname = self.hostname
            msg.group = proc_info["group"]
            msg.stdout = stdout
            msg.stderr = stderr
            self.lc.publish(self.proc_outputs_channel, msg.encode())

            proc_info["stdout"] = ""
            proc_info["stderr"] = ""

    def run(self):
        logging.info("Host running.")
        while not self._stop_event.is_set():
            try:
                self.lc.handle_timeout(50)
            except OSError as e:
                self._handle_lcm_error(e)
                continue

            if self.monitor_timer.timeout():
                for process_name in list(self.processes.keys()):
                    self.monitor_process(process_name)
                    
            if self.output_timer.timeout():
                self.publish_procs_outputs()
                
            if self.host_status_timer.timeout():
                self.publish_host_info()
                
            if self.procs_status_timer.timeout():
                self.publish_host_procs()

        # Optional: stop everything on shutdown (recommended for systemd)
        logging.info("Stopping managed processes...")
        for name in list(self.processes.keys()):
            try:
                self.stop_process(name)
            except Exception as e:
                logging.warning(f"Shutdown: failed stopping {name}: {e}")

def main():
    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")
    agent = NodeAgent(config_file=config_path)
    agent.run()

if __name__ == "__main__":
    main()