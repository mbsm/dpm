import lcm
import sys
import os
import time
import yaml
import threading

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from dpm_msg import (
    command_t,
    host_info_t,
    host_procs_t,
    proc_info_t,
    proc_output_t,
)


class DPM_Master:
    def __init__(self, config_path):
        # load configuration
        self.config = self.load_config(config_path)
        # initialize channels
        self.command_channel = self.config["command_channel"]
        self.host_info_channel = self.config["host_info_channel"]
        self.proc_outputs_channel = self.config["proc_outputs_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]
        
        # initialize LCM
        lc_url = self.config["lcm_url"]
        self.lc = lcm.LCM(lc_url)
        
        #subscribe to the host_info_channel, proc_outputs_channel and host_procs_channel
        self.s1 = self.lc.subscribe(self.host_info_channel, self.host_info_handler)
        self.s2 = self.lc.subscribe(self.host_procs_channel, self.host_procs_handler)
        self.s3 = self.lc.subscribe(self.proc_outputs_channel, self.proc_outputs_handler)
        
        # Thread synchronization
        self._hosts_lock = threading.Lock()
        self._procs_lock = threading.Lock()
        self._outputs_lock = threading.Lock()
        
        # Data structures
        self._hosts = {}
        self._proc_outputs = {}
        self._procs = {}
        
        # Thread control
        self._running = False
        self._thread = None
    
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
    
    def host_info_handler(self, channel, data):
        # decode the message
        msg = host_info_t.decode(data)

        with self._hosts_lock:
            self._hosts[msg.hostname] = msg

    
    def host_procs_handler(self, channel, data):
        msg = host_procs_t.decode(data)
        
        with self._procs_lock:
            # First, identify and remove processes from this host that aren't in the new message
            hostname = msg.hostname
            # Get all process names from this host
            host_procs = [name for name, proc in self._procs.items() if proc.hostname == hostname]
            # Get process names from the new message
            new_proc_names = [proc.name for proc in msg.procs]
            # Remove processes that no longer exist
            for proc_name in host_procs:
                if proc_name not in new_proc_names:
                    del self._procs[proc_name]
        
        # Now update with the current processes
        for proc in msg.procs:
            self._procs[proc.name] = proc
    
    def proc_outputs_handler(self, channel, data):
        msg = proc_output_t.decode(data)
        with self._outputs_lock:
            self._proc_outputs[msg.name] = msg
    
    def create_proc(self, cmd_name, proc_cmd, group, host, auto_restart=False, realtime=False):
        msg = command_t()
        msg.name = cmd_name
        msg.group = group
        msg.hostname = host
        msg.command = "create_process"
        msg.proc_command = proc_cmd
        msg.auto_restart = auto_restart
        msg.realtime = realtime
        self.lc.publish(self.command_channel, msg.encode())
    
    def start_proc(self, cmd_name, host):
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.command = "start_process"
        self.lc.publish(self.command_channel, msg.encode())
    
    def stop_proc(self, cmd_name, host):
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.command = "stop_process"
        self.lc.publish(self.command_channel, msg.encode())
        
    def del_proc(self, cmd_name, host):
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.command = "delete_process"
        self.lc.publish(self.command_channel, msg.encode())
    
    def start_group(self, group, host):
        msg = command_t()
        msg.group = group
        msg.hostname = host
        msg.command = "start_group"
        self.lc.publish(self.command_channel, msg.encode())
    
    def stop_group(self, group, host):
        msg = command_t()
        msg.group = group
        msg.hostname = host
        msg.command = "stop_group"
        self.lc.publish(self.command_channel, msg.encode())
    
    # Thread-safe accessors for data
    @property
    def hosts(self):
        with self._hosts_lock:
            return self._hosts.copy()
    
    @property
    def procs(self):
        with self._procs_lock:
            return self._procs.copy()
    
    @property
    def proc_outputs(self):
        with self._outputs_lock:
            return self._proc_outputs.copy()
    
    # Thread management methods
    def start(self):
        """Start the LCM handling thread"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._thread_func, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the LCM handling thread"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)  # Give it 2 seconds to terminate
            self._thread = None
    
    def _thread_func(self):
        """Thread function that handles LCM messages"""
        while self._running:
            try:
                # Instead of just a timeout, we'll handle all queued messages
                # This ensures we don't miss any messages
                self.lc.handle_timeout(100)
            except Exception as e:
                # Log any errors but keep the thread running
                print(f"LCM handler error: {e}", file=sys.stderr)
                time.sleep(0.1)  # Small delay to avoid tight loop on persistent errors
    
    def update(self):
        """Legacy method kept for compatibility; does nothing now that the thread handles updates"""
        pass  # The thread is now handling updates





