import os
import time
import yaml
import lcm
import threading
import logging
from typing import Dict, Optional, Tuple


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
        "Install the project (editable is OK):\n"
        "  pip install -e .\n"
        "Or run from repo without install:\n"
        "  PYTHONPATH=src python -m dpm.gui.main\n"
    ) from e


class Controller:
    """
    Thread model:
      - One background thread owns lc_sub and calls handle_timeout().
      - Publishing uses lc_pub (separate LCM instance) from the GUI thread.

    Data model:
      - Handlers update internal dicts under locks.
      - Properties return snapshot copies to avoid "dict changed size" races in GUI.
    """

    def __init__(self, config_path: str):
        self.config = self.load_config(config_path)

        self.command_channel = self.config["command_channel"]
        self.host_info_channel = self.config["host_info_channel"]
        self.proc_outputs_channel = self.config["proc_outputs_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]

        self.lc_url = self.config["lcm_url"]

        # locks
        self._hosts_lock = threading.Lock()
        self._procs_lock = threading.Lock()
        self._outputs_lock = threading.Lock()

        # data (hostname -> host_info_t)
        self._hosts: Dict[str, host_info_t] = {}

        # data (proc_name -> proc_info_t)
        self._procs: Dict[str, proc_info_t] = {}

        # last message + rolling text buffers (proc_name -> ...)
        self._proc_outputs: Dict[str, proc_output_t] = {}
        self._proc_output_buffers: Dict[str, str] = {}

        # Incremented when a buffer is trimmed/reset so GUI can resync safely
        self._proc_output_buffer_gen: Dict[str, int] = {}

        # LCM instances (separate for thread-safety)
        self.lc_sub: Optional[lcm.LCM] = None
        self.lc_pub: Optional[lcm.LCM] = None

        # thread control
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lcm_backoff_s = 0.25

        self._init_lcm()

    def load_config(self, config_path: str) -> dict:
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Configuration file {config_path} not found.")
        if not os.access(config_path, os.R_OK):
            raise PermissionError(f"Configuration file {config_path} is not readable.")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration file {config_path}: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error loading configuration file {config_path}: {e}")

        required = [
            "command_channel",
            "host_info_channel",
            "proc_outputs_channel",
            "host_procs_channel",
            "lcm_url",
        ]
        for k in required:
            if k not in config:
                raise KeyError(f"Missing required configuration field: {k}")
        return config

    def _init_lcm(self) -> None:
        """(Re)initialize LCM instances and subscriptions."""
        self.lc_sub = lcm.LCM(self.lc_url)
        self.lc_pub = lcm.LCM(self.lc_url)

        # subscribe (owned by background thread)
        self.lc_sub.subscribe(self.host_info_channel, self.host_info_handler)
        self.lc_sub.subscribe(self.host_procs_channel, self.host_procs_handler)
        self.lc_sub.subscribe(self.proc_outputs_channel, self.proc_outputs_handler)

        logging.info(
            "Controller LCM initialized url=%s channels: cmd=%s host_info=%s host_procs=%s outputs=%s",
            self.lc_url,
            self.command_channel,
            self.host_info_channel,
            self.host_procs_channel,
            self.proc_outputs_channel,
        )

    def _reconnect_lcm(self, err: Exception) -> None:
        logging.exception("Controller LCM error: %s", err)
        delay = self._lcm_backoff_s
        logging.warning("Reinitializing Controller LCM in %.2fs...", delay)
        time.sleep(delay)
        self._lcm_backoff_s = min(self._lcm_backoff_s * 2.0, 5.0)

        try:
            self._init_lcm()
            self._lcm_backoff_s = 0.25
            logging.info("Controller LCM reinitialized successfully.")
        except Exception as e2:
            logging.exception("Controller LCM reinit failed: %s", e2)

    # -----------------
    # LCM handlers (background thread)
    # -----------------
    def host_info_handler(self, channel, data) -> None:
        msg = host_info_t.decode(data)
        with self._hosts_lock:
            self._hosts[msg.hostname] = msg

    def host_procs_handler(self, channel, data) -> None:
        msg = host_procs_t.decode(data)
        hostname = msg.hostname

        # Update atomically under lock (avoid GUI races)
        with self._procs_lock:
            # remove old procs for this host that are not in the new set
            existing_names = [n for (n, p) in self._procs.items() if getattr(p, "hostname", "") == hostname]
            new_names = {p.name for p in msg.procs}
            for n in existing_names:
                if n not in new_names:
                    del self._procs[n]

            # upsert
            for p in msg.procs:
                self._procs[p.name] = p

    def proc_outputs_handler(self, channel, data) -> None:
        msg = proc_output_t.decode(data)
        name = msg.name

        out = getattr(msg, "stdout", "") or ""
        err = getattr(msg, "stderr", "") or ""

        if not out and not err:
            return

        chunk_parts = []
        if out:
            chunk_parts.append(out)
        if err:
            chunk_parts.append("[stderr]\n" + err)
        chunk = "\n".join(chunk_parts)

        with self._outputs_lock:
            self._proc_outputs[name] = msg

            buf = self._proc_output_buffers.get(name, "")
            if buf and not buf.endswith("\n") and not chunk.startswith("\n"):
                buf += "\n"
            buf += chunk

            # Cap memory per process (protect GUI)
            MAX_BYTES = 2 * 1024 * 1024  # 2MB per proc
            if len(buf) > MAX_BYTES:
                buf = buf[-MAX_BYTES:]
                self._proc_output_buffer_gen[name] = self._proc_output_buffer_gen.get(name, 0) + 1

            self._proc_output_buffers[name] = buf
            self._proc_output_buffer_gen.setdefault(name, 0)

    def get_proc_output_delta(self, proc_name: str, last_gen: int, last_len: int) -> Tuple[int, str, bool, int]:
        """
        Thread-safe per-process accessor to avoid copying the full outputs dict.

        Returns: (cur_gen, delta_text, reset, cur_len)
          - reset=True means caller should replace its view with full buffer (delta_text is full buffer).
          - reset=False means caller can append delta_text.
        """
        with self._outputs_lock:
            buf = self._proc_output_buffers.get(proc_name, "")
            cur_gen = self._proc_output_buffer_gen.get(proc_name, 0)

        cur_len = len(buf)

        # Buffer was trimmed/reset since caller last saw it -> full redraw
        if cur_gen != last_gen:
            return cur_gen, buf, True, cur_len

        # Caller is out of range (e.g., cleared) -> full redraw
        if last_len > cur_len:
            return cur_gen, buf, True, cur_len

        # Normal case: append only
        return cur_gen, buf[last_len:], False, cur_len

    def get_proc_output_last(self, proc_name: str) -> Optional[proc_output_t]:
        """
        Thread-safe: return the last proc_output_t for a single process (no dict copy).
        """
        with self._outputs_lock:
            return self._proc_outputs.get(proc_name)

    # Keep snapshot property if other GUI parts use it, but prefer get_proc_output_delta()
    @property
    def proc_output_buffers(self) -> Dict[str, str]:
        with self._outputs_lock:
            return dict(self._proc_output_buffers)

    # -----------------
    # Publishing (GUI thread)
    # -----------------
    def _publish(self, msg: command_t) -> None:
        if self.lc_pub is None:
            raise RuntimeError("LCM publisher not initialized.")
        self.lc_pub.publish(self.command_channel, msg.encode())

    def create_proc(self, cmd_name, proc_cmd, group, host, auto_restart=False, realtime=False) -> None:
        msg = command_t()
        msg.name = cmd_name
        msg.group = group
        msg.hostname = host
        msg.action = "create_process"
        msg.exec_command = proc_cmd  
        msg.auto_restart = bool(auto_restart)
        msg.realtime = bool(realtime)
        self._publish(msg)

    def start_proc(self, cmd_name, host) -> None:
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.action = "start_process"
        self._publish(msg)

    def stop_proc(self, cmd_name, host) -> None:
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.action = "stop_process"
        self._publish(msg)

    def del_proc(self, cmd_name, host) -> None:
        msg = command_t()
        msg.name = cmd_name
        msg.hostname = host
        msg.action = "delete_process"
        self._publish(msg)

    def start_group(self, group, host) -> None:
        msg = command_t()
        msg.group = group
        msg.hostname = host
        msg.action = "start_group"
        self._publish(msg)

    def stop_group(self, group, host) -> None:
        msg = command_t()
        msg.group = group
        msg.hostname = host
        msg.action = "stop_group"
        self._publish(msg)

    # -----------------
    # Thread-safe snapshots for GUI
    # -----------------
    @property
    def hosts(self) -> Dict[str, host_info_t]:
        with self._hosts_lock:
            return dict(self._hosts)

    @property
    def procs(self) -> Dict[str, proc_info_t]:
        with self._procs_lock:
            return dict(self._procs)

    @property
    def proc_outputs(self) -> Dict[str, proc_output_t]:
        with self._outputs_lock:
            return dict(self._proc_outputs)

    # -----------------
    # Thread management
    # -----------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_func, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _thread_func(self) -> None:
        while self._running:
            if self.lc_sub is None:
                time.sleep(0.2)
                continue
            try:
                self.lc_sub.handle_timeout(100)
            except OSError as e:
                # Same failure class as the node: lcm_handle_timeout() returned -1
                self._reconnect_lcm(e)
            except Exception as e:
                logging.exception("Controller LCM handler error: %s", e)
                time.sleep(0.2)
