#!/usr/bin/env python3
import os
import time
import lcm
import psutil
from subprocess import PIPE
import logging
import logging.handlers
import socket
import yaml
import fcntl
import sys
import threading
import errno

# Define process state constants
STATE_READY = "T"
STATE_RUNNING = "R"
STATE_FAILED = "F"
STATE_KILLED = "K"

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from dpm_msgs import (
    command_t,
    host_info_t,
    host_procs_t,
    proc_info_t,
    proc_output_t,
)


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
        self.command_channel = self.config["command_channel"]
        self.host_info_channel = self.config["host_info_channel"]
        self.proc_outputs_channel = self.config["proc_outputs_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]
        self.stop_timeout = self.config["stop_timeout"]

        self.monitor_timer = Timer(self.config["monitor_interval"])
        self.output_timer = Timer(self.config["output_interval"])
        self.host_status_timer = Timer(self.config["host_status_interval"])
        self.procs_status_timer = Timer(self.config["procs_status_interval"])
        self.lc_url = self.config["lcm_url"]
        self.log_file_path = self.config.get("log_file_path", "/var/log/dpm/node.log")

        self.hostname = socket.gethostname()
        self.last_publish_time = 0
        self.last_net_tx = 0
        self.last_net_rx = 0
        self.processes = {}

        try:
            self.lc = lcm.LCM(self.lc_url)
            self.subscription = self.lc.subscribe(self.command_channel, self.command_handler)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize LCM with URL {self.lc_url}: {e}")

        self.init_logging()
        logging.info(f"Host initialized with channels: command={self.command_channel}, info={self.host_info_channel}")

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
        log_path = "/var/log/dpm/node.log"
        logger = logging.getLogger()

        # Resolve desired level
        cfg_level = os.environ.get("DPM_LOG_LEVEL", "INFO").upper()
        if cfg_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            cfg_level = "INFO"
        level = getattr(logging, cfg_level, logging.INFO)
        logger.setLevel(level)

        # Remove any pre-existing handlers to avoid duplicating
        for h in list(logger.handlers):
            logger.removeHandler(h)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(level)
        logger.addHandler(stderr_handler)

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10*1024*1024, backupCount=5
            )
            fh.setFormatter(formatter)
            fh.setLevel(level)
            logger.addHandler(fh)
            logger.info(f"Logging initialized (level={cfg_level}) path={log_path}")
        except Exception as e:
            logger.warning(f"File logging disabled ({e}); stderr only.")

    def command_handler(self, channel, data):
        msg = command_t.decode(data)
        if msg.hostname != self.hostname:
            return

        logging.info(f"Command handler: Received command: {msg.command} for process: {msg.proc_command}")
        group = msg.group

        if msg.command == "create_process":
            self.create_process(msg.name, msg.proc_command, msg.auto_restart, msg.realtime, group)
        elif msg.command == "start_process":
            self.start_process(msg.name)
        elif msg.command == "stop_process":
            self.stop_process(msg.name)
        elif msg.command == "delete_process":
            self.delete_process(msg.name)
        elif msg.command == "start_group":
            self.start_group(msg.group)
        elif msg.command == "stop_group":
            self.stop_group(msg.group)
        else:
            logging.warning(f"Command handler: Unknown command: {msg.command} for process: {msg.proc_command}")

    def create_process(self, process_name, proc_command, restart_on_failure, realtime, group):
        if process_name in self.processes:
            proc_info = self.processes[process_name]
            proc = proc_info["proc"]
            if is_running(proc):
                self.stop_process(process_name)
        self.processes[process_name] = {
            "proc": None,
            "cmd": proc_command,
            "restart": restart_on_failure,
            "realtime": realtime,
            "exit_code": -1,
            "group": group,
            "errors": "",
            "state": STATE_READY,
            "status": "stopped",
            "runtime": 0,
            "stdout": "",
            "stderr": "",
        }
        logging.info(f"Create Process: Created process: {process_name} with command: {proc_command} auto_restart: {restart_on_failure} and realtime: {realtime}")

    def delete_process(self, process_name):
        if process_name in self.processes:
            if self.processes[process_name]["proc"] is not None:
                self.stop_process(msg.name)
            del self.processes[process_name]
            logging.info(f"Delete Process: Deleted process: {process_name}")
        else:
            logging.warning(f"Delete Process: Process {process_name} not found, ignoring command.")

    def start_process(self, process_name):
        if process_name not in self.processes:
            logging.warning(f"Start Process: Process {process_name} not found in the process table. Ignoring command.")
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]
        proc_command = proc_info["cmd"]
        realtime = proc_info["realtime"]

        if is_running(proc):
            logging.info(f"Start Process: Process {process_name} is already running with PID {proc.pid}. Skipping start.")
        else:
            logging.info(f"Start Process: Starting process: {process_name} with command: {proc_command}")
            try:
                proc = psutil.Popen(
                    proc_command.split(),
                    stdout=PIPE,
                    stderr=PIPE,
                    text=True,              # enable text mode (no decode warnings)
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1               # line-buffered (valid in text mode)
                )
                self.processes[process_name]["proc"] = proc
                self.processes[process_name]["state"] = STATE_RUNNING
                self.processes[process_name]["status"] = "running"

                # Start threads to read stdout and stderr
                stdout_lines = []
                stderr_lines = []
                stdout_thread = threading.Thread(target=stream_reader, args=(proc.stdout, stdout_lines), daemon=True)
                stderr_thread = threading.Thread(target=stream_reader, args=(proc.stderr, stderr_lines), daemon=True)
                stdout_thread.start()
                stderr_thread.start()

                self.processes[process_name]["stdout_lines"] = stdout_lines
                self.processes[process_name]["stderr_lines"] = stderr_lines

                logging.info(f"Start Process: Started process: {process_name} with PID {proc.pid}")

                if realtime:
                    try:
                        os.sched_setscheduler(proc.pid, os.SCHED_FIFO, os.sched_param(40))
                        logging.info(f"Start Process: Set real-time priority for process: {process_name} with PID {proc.pid}")
                    except PermissionError:
                        logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: Permission denied.")
                        self.processes[process_name]["errors"] = "Permission denied setting real-time priority."
                    except Exception as e:
                        logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: {e}")
                        self.processes[process_name]["errors"] = str(e)

            except Exception as e:
                error_msg = f"Failed to start process {process_name}: {e}"
                logging.error(f"Start Process: {error_msg}")
                self.processes[process_name]["state"] = STATE_FAILED
                self.processes[process_name]["proc"] = None
                self.processes[process_name]["errors"] = str(e)
                self.processes[process_name]["status"] = "failed"
                
                # Publish the error to proc_output channel immediately
                msg = proc_output_t()
                msg.timestamp = int(time.time() * 1e6)
                msg.name = process_name
                msg.hostname = self.hostname
                msg.group = self.processes[process_name]["group"]
                msg.stdout = ""
                msg.stderr = error_msg
                self.lc.publish(self.proc_outputs_channel, msg.encode())

    def stop_process(self, process_name):
        if process_name in self.processes:
            proc_info = self.processes[process_name]
            proc = proc_info["proc"]

            if proc and proc_info["state"] == STATE_READY:
                logging.info(f"Stop Process: Process {process_name} is already stopped.")
                return
            if proc is None:
                logging.info(f"Stop Process: Process {process_name} not running, ignoring command.")
                return
            try:
                proc.terminate()
                proc.wait(timeout=self.stop_timeout)
                logging.info(f"Stop Process: Gracefully stopped process: {process_name} with PID {proc.pid}")
                self.processes[process_name]["exit_code"] = proc.returncode
                self.processes[process_name]["state"] = STATE_READY
                self.processes[process_name]["status"] = "stopped"
            except psutil.TimeoutExpired:
                proc.kill()
                logging.warning(f"Stop Process: Forcefully killed process: {process_name} with PID {proc.pid}")
                self.processes[process_name]["exit_code"] = proc.returncode
                self.processes[process_name]["state"] = STATE_KILLED
                self.processes[process_name]["status"] = "killed"
        else:
            logging.warning(f"Stop Process: Process {process_name} not found, ignoring command.")

    def start_group(self, group):
        for name, proc in self.processes.items():
            if proc.get("group") == group:
                self.start_process(name)

    def stop_group(self, group):
        for name, proc in self.processes.items():
            if proc.get("group") == group:
                self.stop_process(name)

    def monitor_process(self, process_name):
        if process_name not in self.processes:
            logging.warning(f"Monitor Process: Called with process {process_name} not in process table.")
            return

        proc_info = self.processes[process_name]
        proc = proc_info["proc"]

        if proc and proc_info["state"] == STATE_READY:
            proc_info["exit_code"] = proc.poll()
            return

        if proc_info["state"] == STATE_RUNNING:
            if not is_running(proc):
                logging.warning(f"Monitor Process: Process {process_name} found stopped with exit code: {proc.poll()}")
                proc_info["state"] = STATE_FAILED
                proc_info["exit_code"] = proc.poll()
                proc_info["proc"] = None
                proc_info["status"] = "failed"
                
                # Capture any remaining output from the streams
                if proc_info.get("stdout_lines") is not None and proc_info.get("stderr_lines") is not None:
                    # Give streams a moment to finish reading
                    import time
                    time.sleep(0.1)
                    
                    stdout_content = "".join(proc_info["stdout_lines"])
                    stderr_content = "".join(proc_info["stderr_lines"])
                    
                    logging.info(f"Monitor Process: Captured stdout for {process_name}: {repr(stdout_content)}")
                    logging.info(f"Monitor Process: Captured stderr for {process_name}: {repr(stderr_content)}")
                    
                    proc_info["errors"] = stdout_content + stderr_content
                    proc_info["stdout_lines"].clear()
                    proc_info["stderr_lines"].clear()
                    
                    # Publish the captured output to LCM immediately
                    if stdout_content or stderr_content:
                        msg = proc_output_t()
                        msg.timestamp = int(time.time() * 1e6)
                        msg.name = process_name
                        msg.hostname = self.hostname
                        msg.group = proc_info["group"]
                        msg.stdout = stdout_content
                        msg.stderr = stderr_content
                        self.lc.publish(self.proc_outputs_channel, msg.encode())
                        logging.debug(f"Monitor Process: Published output for failed process {process_name}")
                else:
                    proc_info["errors"] = "Process stopped unexpectedly."

                if proc_info["restart"]:
                    logging.info(f"Monitor Process: Restarting process {process_name}.")
                    self.start_process(process_name)
            else:
                if "stdout_lines" in proc_info and "stderr_lines" in proc_info:
                    stdout_content = "".join(proc_info["stdout_lines"])
                    stderr_content = "".join(proc_info["stderr_lines"])
                    
                    proc_info["stdout"] = stdout_content
                    proc_info["stderr"] = stderr_content
                    proc_info["stdout_lines"].clear()
                    proc_info["stderr_lines"].clear()
                    
                    # Log any new output for debugging
                    if stdout_content:
                        if logging.getLogger().isEnabledFor(logging.DEBUG):
                            logging.debug(f"Monitor Process: New stdout from {process_name}: {repr(stdout_content)}")
                    if stderr_content:
                        if logging.getLogger().isEnabledFor(logging.DEBUG):
                            logging.debug(f"Monitor Process: New stderr from {process_name}: {repr(stderr_content)}")
                else:
                    logging.warning(f"Monitor Process: Process {process_name} has no stdout or stderr streams.")

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

    def publish_host_procs(self):
        msg = host_procs_t()
        msg.timestamp = int(time.time() * 1e6)
        msg.hostname = self.hostname
        msg.procs = []
        msg.num_procs = 0

        for process_name, proc_info in self.processes.items():
            msg_proc = proc_info_t()
            msg_proc.name = process_name
            msg_proc.hostname = self.hostname
            proc = proc_info["proc"]
            if proc and is_running(proc):
                try:
                    msg_proc.cpu = proc.cpu_percent(interval=None) / 100.0
                except Exception:
                    msg_proc.cpu = 0.0
                try:
                    mem_info = proc.memory_info()
                    msg_proc.mem_rss = mem_info.rss // 1024
                    msg_proc.mem_vms = mem_info.vms // 1024
                except Exception:
                    msg_proc.mem_rss = 0
                    msg_proc.mem_vms = 0
                msg_proc.priority = proc.nice()
                msg_proc.pid = proc.pid
                msg_proc.ppid = proc.ppid()
                msg_proc.exit_code = -1
                msg_proc.errors = proc_info["errors"]
                msg_proc.status = proc_info["status"]
                msg_proc.state = proc_info["state"]
                msg_proc.group = proc_info["group"]
                msg_proc.cmd = proc_info["cmd"]
                msg_proc.realtime = proc_info["realtime"]
                msg_proc.auto_restart = proc_info["restart"]
                try:
                    msg_proc.runtime = int(time.time() - proc.create_time())
                except Exception:
                    msg_proc.runtime = 0
            else:
                msg_proc.pid = -1
                msg_proc.exit_code = proc_info.get("exit_code", -1)
                msg_proc.errors = proc_info["errors"]
                msg_proc.status = proc_info["status"]
                msg_proc.state = proc_info["state"]
                msg_proc.group = proc_info["group"]
                msg_proc.cmd = proc_info["cmd"]

            msg.procs.append(msg_proc)
            msg.num_procs += 1
        self.lc.publish(self.host_procs_channel, msg.encode())

    def publish_procs_outputs(self):
        for process_name, proc_info in self.processes.items():
            msg = proc_output_t()
            msg.timestamp = int(time.time() * 1e6)
            msg.name = process_name
            msg.hostname = self.hostname
            msg.group = proc_info["group"]
            msg.stdout = proc_info["stdout"]
            msg.stderr = proc_info["stderr"]
            self.lc.publish(self.proc_outputs_channel, msg.encode())
            proc_info["stdout"] = ""
            proc_info["stderr"] = ""

    def run(self):
        logging.info("Host running.")
        while True:
            self.lc.handle_timeout(50)
            if self.monitor_timer.timeout():
                for process_name in list(self.processes.keys()):
                    self.monitor_process(process_name)
            if self.output_timer.timeout():
                self.publish_procs_outputs()
            if self.host_status_timer.timeout():
                self.publish_host_info()
            if self.procs_status_timer.timeout():
                self.publish_host_procs()


if __name__ == "__main__":
    agent = NodeAgent()
    agent.run()