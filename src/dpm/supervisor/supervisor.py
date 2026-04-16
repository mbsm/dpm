"""Supervisor — publishes commands to agents and aggregates telemetry for the UI."""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import yaml

import lcm

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


@dataclass
class _ProcOutputState:
    """Per-process output state: last LCM message, rolling text buffer, generation counter."""
    last_msg: Optional[proc_output_t] = None
    buf: str = ""
    gen: int = 0


class Supervisor:
    """
    Thread model:
      - One background thread owns lc_sub and calls handle_timeout().
      - Publishing uses lc_pub (separate LCM instance) from the GUI thread.

    Data model:
      - Handlers update internal dicts under locks.
      - Properties return snapshot copies to avoid "dict changed size" races in GUI.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
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
        self._lcm_lock = threading.Lock()

        # data (hostname -> host_info_t)
        self._hosts: Dict[str, host_info_t] = {}

        # data ((hostname, proc_name) -> proc_info_t)
        self._procs: Dict[Tuple[str, str], proc_info_t] = {}

        # Per-process output state (proc_name -> _ProcOutputState)
        self._proc_output_states: Dict[str, _ProcOutputState] = {}

        # Monotonic command sequence number (GUI thread only — no lock needed).
        # Start from current microsecond timestamp so independent CLI sessions
        # don't collide on seq=0 and trigger the agent's dedup filter.
        self._cmd_seq: int = int(time.time() * 1e6)

        # LCM instances (separate for thread-safety)
        self.lc_sub: Optional[lcm.LCM] = None
        self.lc_pub: Optional[lcm.LCM] = None

        # thread control
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lcm_backoff_s = 0.25

        self._init_lcm()

    def load_config(self, config_path: str) -> dict:
        from dpm.utils.config import load_dpm_config

        return load_dpm_config(config_path, [
            "command_channel",
            "host_info_channel",
            "proc_outputs_channel",
            "host_procs_channel",
            "lcm_url",
        ])

    def _init_lcm(self) -> None:
        """(Re)initialize LCM instances and subscriptions."""
        for attr in ("lc_sub", "lc_pub"):
            old = getattr(self, attr, None)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
        self.lc_sub = lcm.LCM(self.lc_url)
        self.lc_pub = lcm.LCM(self.lc_url)

        # subscribe (owned by background thread)
        self.lc_sub.subscribe(self.host_info_channel, self.host_info_handler)
        self.lc_sub.subscribe(self.host_procs_channel, self.host_procs_handler)
        self.lc_sub.subscribe(self.proc_outputs_channel, self.proc_outputs_handler)

        logging.info(
            "Supervisor LCM initialized url=%s channels: cmd=%s host_info=%s host_procs=%s outputs=%s",
            self.lc_url,
            self.command_channel,
            self.host_info_channel,
            self.host_procs_channel,
            self.proc_outputs_channel,
        )

    def _reconnect_lcm(self, err: Exception) -> None:
        logging.exception("Supervisor LCM error: %s", err)
        delay = self._lcm_backoff_s
        logging.warning("Reinitializing Supervisor LCM in %.2fs...", delay)
        time.sleep(delay)
        self._lcm_backoff_s = min(self._lcm_backoff_s * 2.0, 5.0)

        with self._lcm_lock:
            try:
                self._init_lcm()
                self._lcm_backoff_s = 0.25
                logging.info("Supervisor LCM reinitialized successfully.")
            except (OSError, RuntimeError) as e2:
                logging.exception("Supervisor LCM reinit failed: %s", e2)

    # -----------------
    # LCM handlers (background thread)
    # -----------------
    def host_info_handler(self, _channel, data) -> None:
        try:
            msg = host_info_t.decode(data)
        except Exception as e:
            logging.error("host_info_handler: decode failed: %s", e)
            return
        with self._hosts_lock:
            self._hosts[msg.hostname] = msg

    def host_procs_handler(self, _channel, data) -> None:
        try:
            msg = host_procs_t.decode(data)
        except Exception as e:
            logging.error("host_procs_handler: decode failed: %s", e)
            return
        hostname = msg.hostname

        # Update atomically under lock (avoid GUI races)
        with self._procs_lock:
            # remove old procs for this host that are not in the new set
            existing_keys = [
                k
                for k in self._procs
                if k[0] == hostname
            ]
            new_names = {p.name for p in msg.procs}
            for k in existing_keys:
                if k[1] not in new_names:
                    del self._procs[k]

            # upsert
            for p in msg.procs:
                self._procs[(hostname, p.name)] = p

    def proc_outputs_handler(self, _channel, data) -> None:
        try:
            msg = proc_output_t.decode(data)
        except Exception as e:
            logging.error("proc_outputs_handler: decode failed: %s", e)
            return
        name = msg.name

        out = getattr(msg, "stdout", "") or ""
        err = getattr(msg, "stderr", "") or ""

        if not out and not err:
            return

        parts = [out] if out else []
        if err:
            parts.append("[stderr]\n" + err)
        chunk = "\n".join(parts)

        with self._outputs_lock:
            state = self._proc_output_states.setdefault(name, _ProcOutputState())
            state.last_msg = msg

            buf = state.buf
            if buf and not buf.endswith("\n") and not chunk.startswith("\n"):
                buf += "\n"
            buf += chunk

            # Cap memory per process (protect GUI)
            MAX_BYTES = 2 * 1024 * 1024  # 2MB per proc
            if len(buf) > MAX_BYTES:
                buf = buf[-MAX_BYTES:]
                state.gen += 1

            state.buf = buf

    def get_proc_output_delta(
        self, proc_name: str, last_gen: int, last_len: int
    ) -> Tuple[int, str, bool, int]:
        """
        Thread-safe per-process accessor to avoid copying the full outputs dict.

        Returns: (cur_gen, delta_text, reset, cur_len)
          - reset=True means caller should replace its view with full buffer (delta_text is full buffer).
          - reset=False means caller can append delta_text.
        """
        with self._outputs_lock:
            state = self._proc_output_states.get(proc_name)
            if state is None:
                return 0, "", False, 0
            buf = state.buf
            cur_gen = state.gen
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
            state = self._proc_output_states.get(proc_name)
            return state.last_msg if state else None

    def clear_proc_output(self, proc_name: str) -> None:
        """Thread-safe: flush the output buffer for *proc_name*."""
        with self._outputs_lock:
            state = self._proc_output_states.get(proc_name)
            if state is not None:
                state.buf = ""
                state.gen += 1

    # Keep snapshot property if other GUI parts use it, but prefer get_proc_output_delta()
    @property
    def proc_output_buffers(self) -> Dict[str, str]:
        with self._outputs_lock:
            return {k: s.buf for k, s in self._proc_output_states.items()}

    # -----------------
    # Publishing (GUI thread)
    # -----------------
    def _publish(self, msg: command_t) -> None:
        if self.lc_pub is None:
            raise RuntimeError("LCM publisher not initialized.")
        msg.seq = self._cmd_seq
        self._cmd_seq += 1
        try:
            self.lc_pub.publish(self.command_channel, msg.encode())
        except OSError as e:
            logging.error("Publish failed, reconnecting: %s", e)
            self._reconnect_lcm(e)
            # Retry once after reconnect
            try:
                if self.lc_pub is not None:
                    self.lc_pub.publish(self.command_channel, msg.encode())
            except (OSError, AttributeError) as e2:
                logging.error("Publish retry also failed: %s", e2)

    def _send_command(
        self,
        action: str,
        name: str = "",
        hostname: str = "",
        group: str = "",
        exec_command: str = "",
        auto_restart: bool = False,
        realtime: bool = False,
        isolated: bool = False,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
    ) -> None:
        msg = command_t()
        msg.action = action
        msg.name = name
        msg.hostname = hostname
        msg.group = group
        msg.exec_command = exec_command
        msg.auto_restart = bool(auto_restart)
        msg.realtime = bool(realtime)
        msg.isolated = bool(isolated)
        msg.work_dir = work_dir
        msg.cpuset = cpuset
        msg.cpu_limit = float(cpu_limit)
        msg.mem_limit = int(mem_limit)
        self._publish(msg)

    def create_proc(
        self,
        cmd_name: str,
        proc_cmd: str,
        group: str,
        host: str,
        auto_restart: bool = False,
        realtime: bool = False,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
        isolated: bool = False,
    ) -> None:
        self._send_command("create_process", name=cmd_name, hostname=host, group=group,
                           exec_command=proc_cmd, auto_restart=auto_restart, realtime=realtime,
                           isolated=isolated, work_dir=work_dir, cpuset=cpuset,
                           cpu_limit=cpu_limit, mem_limit=mem_limit)

    def start_proc(self, cmd_name: str, host: str) -> None:
        self._send_command("start_process", name=cmd_name, hostname=host)

    def stop_proc(self, cmd_name: str, host: str) -> None:
        self._send_command("stop_process", name=cmd_name, hostname=host)

    def del_proc(self, cmd_name: str, host: str) -> None:
        self._send_command("delete_process", name=cmd_name, hostname=host)

    def start_group(self, group: str, host: str) -> None:
        self._send_command("start_group", hostname=host, group=group)

    def stop_group(self, group: str, host: str) -> None:
        self._send_command("stop_group", hostname=host, group=group)

    def set_interval(self, host: str, seconds: float) -> None:
        self._send_command("set_interval", hostname=host, exec_command=str(seconds))

    def set_persistence(self, host: str, enabled: bool) -> None:
        self._send_command("set_persistence", hostname=host, exec_command="on" if enabled else "off")

    # -----------------
    # Thread-safe snapshots for GUI
    # -----------------
    @property
    def hosts(self) -> Dict[str, host_info_t]:
        with self._hosts_lock:
            return dict(self._hosts)

    @property
    def procs(self) -> Dict[Tuple[str, str], proc_info_t]:
        with self._procs_lock:
            return dict(self._procs)

    @property
    def proc_outputs(self) -> Dict[str, proc_output_t]:
        with self._outputs_lock:
            return {k: s.last_msg for k, s in self._proc_output_states.items()
                    if s.last_msg is not None}

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

    def reconnect_lcm(self, new_url: str) -> None:
        """Change the LCM URL at runtime, persist to config file, and reconnect."""
        was_running = self._running
        self.stop()

        self.lc_url = new_url
        self.config["lcm_url"] = new_url
        self._init_lcm()

        # Persist the new URL back to dpm.yaml
        logging.warning("Persisting new lcm_url %r to %s", new_url, self.config_path)
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            raw["lcm_url"] = new_url
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
        except (OSError, yaml.YAMLError):
            logging.exception("Failed to persist lcm_url to %s", self.config_path)

        if was_running:
            self.start()

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
                logging.exception("Supervisor LCM handler error: %s", e)
                time.sleep(0.2)
