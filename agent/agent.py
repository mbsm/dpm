#!/usr/bin/env python3
import os
import time
import lcm
import psutil
from subprocess import PIPE
import logging
import socket
import yaml
import fcntl
import sys

# Define process state constants
STATE_READY = "T"
STATE_RUNNING = "R"
STATE_FAILED = "F"
STATE_KILLED = "K"

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from dpm_msg import (
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


class DPMAgent:
    def __init__(self, config_file="../dpm.yaml"):
        current_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(current_dir, config_file)
        self.config = self.load_config(config_path)

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

        self.init_logging(current_dir)
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

    def init_logging(self, current_dir):
        #logs will go to the stderr, systemd will pick them up and write them to the journal
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

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
            "status": "S",
            "runtime": 0,
            "stdout": "",
            "stderr": "",
        }
        logging.info(f"Create Process: Created process: {process_name} with command: {proc_command} auto_restart: {restart_on_failure} and realtime: {realtime}")
        
    def delete_process(self, process_name):
        if process_name in self.processes:
            if self.processes[process_name]["proc"] is not None:
                self.stop_process(process_name)
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
                proc = psutil.Popen([proc_command], stdout=PIPE, stderr=PIPE)
                set_nonblocking(proc.stdout)
                set_nonblocking(proc.stderr)
                self.processes[process_name]["proc"] = proc
                self.processes[process_name]["state"] = STATE_RUNNING
                logging.info(f"Start Process: Started process: {process_name} with PID {proc.pid}")

                if realtime:
                    try:
                        os.sched_setscheduler(proc.pid, os.SCHED_FIFO, os.sched_param(40))
                        logging.info(f"Start Process: Set real-time priority for process: {process_name} with PID {proc.pid}")
                    except PermissionError:
                        logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: Permission denied.")
                        self.processes[process_name]["errors"] = f"Permission denied setting real-time priority."
                    except Exception as e:
                        logging.error(f"Start Process: Failed to set real-time priority for process {process_name}: {e}")
                        self.processes[process_name]["errors"] = str(e)

            except Exception as e:
                logging.error(f"Start Process: Failed to start process {process_name}: {e}")
                self.processes[process_name]["state"] = STATE_FAILED
                self.processes[process_name]["proc"] = None
                self.processes[process_name]["errors"] = str(e)

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
            except psutil.TimeoutExpired:
                proc.kill()
                logging.warning(f"Stop Process: Forcefully killed process: {process_name} with PID {proc.pid}")
                self.processes[process_name]["exit_code"] = proc.returncode
                self.processes[process_name]["state"] = STATE_KILLED
        else:
            logging.warning(f"Stop Process: Process {process_name} not found, ignoring command.")
            
    def start_group(self, group):
        for name, proc in self.processes:
            if(proc.group==group):
                self.start_process(name)
                
    def stop_group(self, group):
        for name, proc in self.processes:
            if(proc.group==group):
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
                logging.warning(f"Monitor Process: Process {process_name} found stopped.")
                proc_info["state"] = STATE_FAILED
                proc_info["exit_code"] = proc.poll()
                proc_info["proc"] = None

                if proc_info["restart"]:
                    logging.info(f"Monitor Process: Restarting process {process_name}.")
                    self.start_process(process_name)
            else:
                proc_info["stdout"] = proc.stdout.read1().decode("utf-8")
                proc_info["stderr"] = proc.stderr.read1().decode("utf-8")

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

        sent_kbps = net_tx_diff / time_diff
        recv_kbps = net_rx_diff / time_diff

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
                msg_proc.cpu = proc.cpu_percent(interval=None) / 100.0
                mem_info = proc.memory_info()
                msg_proc.mem_rss = mem_info.rss // 1024
                msg_proc.mem_vms = mem_info.vms // 1024
                msg_proc.priority = proc.nice()
                msg_proc.pid = proc.pid
                msg_proc.ppid = proc.ppid()
                msg_proc.exit_code = -1
                msg_proc.errors = proc_info["errors"]
                msg_proc.status = proc.status()
                msg_proc.state = proc_info["state"]
                msg_proc.group = proc_info["group"]
                msg_proc.cmd = proc_info["cmd"]
                msg_proc.realtime = proc_info["realtime"]
                msg_proc.auto_restart = proc_info["restart"]
                msg_proc.runtime = int(time.time() - proc.create_time())
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
            proc_info["Errors"] = ""
        self.lc.publish(self.host_procs_channel, msg.encode())

    def publish_procs_outputs(self):
        for process_name, proc_info in self.processes.items():
            msg = proc_output_t()
            msg.timestamp = int(time.time() * 1e6)
            msg.name = process_name
            msg.hostname = self.hostname
            msg.stdout = proc_info["stdout"] + proc_info["stderr"]
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
    agent = DPMAgent()
    agent.run()