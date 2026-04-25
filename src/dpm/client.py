"""Client — publishes commands to daemons and aggregates telemetry for the UI."""

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
        log_chunk_t,
        proc_info_t,
    )
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Failed to import 'dpm_msgs'.\n"
        "Install the project (editable is OK):\n"
        "  pip install -e .\n"
        "Or run from repo without install:\n"
        "  PYTHONPATH=src python -m dpm.gui.main\n"
    ) from e

from dpm.constants import DPM_PROTOCOL_VERSION


@dataclass
class _ProcOutputState:
    """Per-process output state: rolling text buffer + generation counter."""
    buf: str = ""
    gen: int = 0
    last_seen_us: int = 0  # daemon timestamp of the most recent chunk
    last_host: str = ""    # which host published it


def _version_ok(msg, _logged: dict = {}) -> bool:
    """Return True iff *msg* carries the expected protocol version.

    Logs a single warning per (remote, version) pair to avoid log spam
    from a mismatched fleet member that publishes at 1 Hz.
    """
    v = getattr(msg, "protocol_version", 0)
    if v == DPM_PROTOCOL_VERSION:
        return True
    key = (getattr(msg, "hostname", "") or "?", v)
    if key not in _logged:
        _logged[key] = True
        logging.warning(
            "Dropping message with protocol_version=%d from %s (expected %d). "
            "Upgrade/downgrade so all peers share a version.",
            v, key[0], DPM_PROTOCOL_VERSION,
        )
    return False


class Client:
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
        self.log_chunks_channel = self.config["log_chunks_channel"]
        self.host_procs_channel = self.config["host_procs_channel"]

        # Pending read_log requests: seq -> assembly state.
        # Chunks may arrive interleaved with other traffic; we collect by
        # request_seq and signal completion when last=True arrives.
        self._read_log_pending: Dict[int, dict] = {}
        self._read_log_lock = threading.Lock()

        self.lc_url = self.config["lcm_url"]

        # locks
        self._hosts_lock = threading.Lock()
        self._procs_lock = threading.Lock()
        self._outputs_lock = threading.Lock()
        self._lcm_lock = threading.Lock()

        # data (hostname -> host_info_t)
        self._hosts: Dict[str, host_info_t] = {}
        # last-seen monotonic timestamp per host (for stale eviction)
        self._host_last_seen: Dict[str, float] = {}
        # multiplier of report_interval before a host is considered offline
        self._host_offline_factor = 3.0

        # data ((hostname, proc_name) -> proc_info_t)
        self._procs: Dict[Tuple[str, str], proc_info_t] = {}
        # secondary index: hostname -> set of proc names (avoids linear scan)
        self._procs_by_host: Dict[str, set] = {}

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
        # Deduplicates concurrent reconnect requests (publisher + subscriber
        # may both detect an error on the same broken socket).
        self._reconnecting = False

        self._init_lcm()

    def load_config(self, config_path: str) -> dict:
        from dpm.config import load_dpm_config

        return load_dpm_config(config_path, [
            "command_channel",
            "host_info_channel",
            "log_chunks_channel",
            "host_procs_channel",
            "lcm_url",
        ])

    def _init_lcm(self) -> None:
        """(Re)initialize LCM instances and subscriptions.

        Creates the new instances first, swaps them in atomically, then
        briefly sleeps before closing the old ones. The grace period lets
        any in-flight ``handle_timeout`` (up to 100 ms) finish on the old
        subscriber without risking a use-after-free from the LCM C library.
        Callers should hold ``self._lcm_lock`` (``__init__`` runs before
        any thread starts, so the lock is optional there).
        """
        old_sub = getattr(self, "lc_sub", None)
        old_pub = getattr(self, "lc_pub", None)

        new_sub = lcm.LCM(self.lc_url)
        new_pub = lcm.LCM(self.lc_url)
        new_sub.subscribe(self.host_info_channel, self.host_info_handler)
        new_sub.subscribe(self.host_procs_channel, self.host_procs_handler)
        new_sub.subscribe(self.log_chunks_channel, self.log_chunks_handler)

        self.lc_sub = new_sub
        self.lc_pub = new_pub

        if old_sub is not None or old_pub is not None:
            time.sleep(0.15)  # > max handle_timeout (100 ms)
            for old in (old_sub, old_pub):
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass

        logging.info(
            "Client LCM initialized url=%s channels: cmd=%s host_info=%s host_procs=%s log_chunks=%s",
            self.lc_url,
            self.command_channel,
            self.host_info_channel,
            self.host_procs_channel,
            self.log_chunks_channel,
        )

    def _reconnect_lcm(self, err: Exception) -> None:
        """Reinitialize LCM after an error, deduping concurrent requests."""
        with self._lcm_lock:
            if self._reconnecting:
                logging.debug("Client LCM reconnect already in progress; skipping.")
                return
            self._reconnecting = True
            delay = self._lcm_backoff_s
            self._lcm_backoff_s = min(self._lcm_backoff_s * 2.0, 5.0)

        logging.warning("Client LCM error (%s); reinitializing in %.2fs...", err, delay)
        try:
            time.sleep(delay)
            with self._lcm_lock:
                try:
                    self._init_lcm()
                    self._lcm_backoff_s = 0.25
                    logging.info("Client LCM reinitialized successfully.")
                except (OSError, RuntimeError) as e2:
                    logging.error("Client LCM reinit failed: %s", e2)
        finally:
            with self._lcm_lock:
                self._reconnecting = False

    # -----------------
    # LCM handlers (background thread)
    # -----------------
    def host_info_handler(self, _channel, data) -> None:
        try:
            msg = host_info_t.decode(data)
        except Exception as e:
            logging.error("host_info_handler: decode failed: %s", e)
            return
        if not _version_ok(msg):
            return
        with self._hosts_lock:
            self._hosts[msg.hostname] = msg
            self._host_last_seen[msg.hostname] = time.monotonic()

    def host_procs_handler(self, _channel, data) -> None:
        try:
            msg = host_procs_t.decode(data)
        except Exception as e:
            logging.error("host_procs_handler: decode failed: %s", e)
            return
        if not _version_ok(msg):
            return
        hostname = msg.hostname

        # Update atomically under lock (avoid GUI races)
        removed_names: set = set()
        with self._procs_lock:
            # remove old procs for this host that are not in the new set
            existing_names = self._procs_by_host.get(hostname, set())
            new_names = {p.name for p in msg.procs}
            for name in existing_names - new_names:
                del self._procs[(hostname, name)]
            removed_names = existing_names - new_names

            # upsert
            for p in msg.procs:
                self._procs[(hostname, p.name)] = p
            self._procs_by_host[hostname] = new_names

            # If a removed proc name doesn't exist on ANY other host, its
            # output buffer can be freed. Checked here (under the procs
            # lock) so we see a consistent view of which hosts own which
            # names. Output-state lock is acquired in _maybe_evict.
            evictable = {
                n for n in removed_names
                if not any(n in names for names in self._procs_by_host.values())
            }
        if evictable:
            self._evict_proc_output_states(evictable)

    def _evict_proc_output_states(self, names: set) -> None:
        """Drop output buffers for proc names no longer present on any host."""
        with self._outputs_lock:
            for name in names:
                self._proc_output_states.pop(name, None)

    def log_chunks_handler(self, _channel, data) -> None:
        try:
            msg = log_chunk_t.decode(data)
        except Exception as e:
            logging.error("log_chunks_handler: decode failed: %s", e)
            return
        if not _version_ok(msg):
            return

        # request_seq != 0 means this is a response to a read_log call we
        # made. Route those into the pending-request collector below;
        # they don't belong in the live tail buffer.
        if msg.request_seq != 0:
            self._collect_read_log_chunk(msg)
            return

        # Live publish: a daemon is sending us output for a process we
        # have an active subscription on.
        name = msg.name
        chunk = msg.content or ""
        if not chunk:
            return

        with self._outputs_lock:
            state = self._proc_output_states.setdefault(name, _ProcOutputState())
            state.last_seen_us = int(getattr(msg, "timestamp", 0))
            state.last_host = msg.hostname

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

    def _collect_read_log_chunk(self, msg) -> None:
        """Assemble chunks for an in-flight read_log request keyed by request_seq."""
        with self._read_log_lock:
            slot = self._read_log_pending.get(msg.request_seq)
            if slot is None:
                # Request not from us, or already completed and consumed.
                return
            slot["parts"].append(msg.content or "")
            if msg.last:
                slot["done"] = True
                slot["event"].set()

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

    def get_proc_output_metadata(self, proc_name: str) -> Tuple[int, str]:
        """Thread-safe: return (last_seen_us, last_host) for *proc_name*.

        Returns ``(0, "")`` if no output has been observed.
        """
        with self._outputs_lock:
            state = self._proc_output_states.get(proc_name)
            if state is None:
                return 0, ""
            return state.last_seen_us, state.last_host

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
        msg.protocol_version = DPM_PROTOCOL_VERSION
        msg.seq = self._cmd_seq
        self._cmd_seq += 1
        # Encode once and reuse on retry — the payload is identical and
        # encode() allocates a fresh bytes object each call.
        encoded = msg.encode()
        try:
            self.lc_pub.publish(self.command_channel, encoded)
        except OSError as e:
            logging.error("Publish failed, reconnecting: %s", e)
            self._reconnect_lcm(e)
            # Retry once after reconnect
            try:
                if self.lc_pub is not None:
                    self.lc_pub.publish(self.command_channel, encoded)
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
        rt_priority: int = 0,
        isolated: bool = False,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
        since_us: int = 0,
        tail_lines: int = 0,
        ttl_seconds: int = 0,
    ) -> int:
        """Publish a command_t. Returns the seq stamped on the message."""
        msg = command_t()
        msg.action = action
        msg.name = name
        msg.hostname = hostname
        msg.group = group
        msg.exec_command = exec_command
        msg.auto_restart = bool(auto_restart)
        msg.realtime = bool(realtime)
        msg.rt_priority = int(rt_priority)
        msg.isolated = bool(isolated)
        msg.work_dir = work_dir
        msg.cpuset = cpuset
        msg.cpu_limit = float(cpu_limit)
        msg.mem_limit = int(mem_limit)
        msg.since_us = int(since_us)
        msg.tail_lines = int(tail_lines)
        msg.ttl_seconds = int(ttl_seconds)
        seq = self._cmd_seq
        self._publish(msg)
        return seq

    def create_proc(
        self,
        cmd_name: str,
        proc_cmd: str,
        group: str,
        host: str,
        auto_restart: bool = False,
        realtime: bool = False,
        rt_priority: int = 0,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
        isolated: bool = False,
    ) -> None:
        self._send_command("create_process", name=cmd_name, hostname=host, group=group,
                           exec_command=proc_cmd, auto_restart=auto_restart, realtime=realtime,
                           rt_priority=rt_priority,
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

    def read_log(
        self,
        cmd_name: str,
        host: str,
        *,
        since_us: int = 0,
        tail_lines: int = 0,
        timeout: float = 5.0,
    ) -> str:
        """Synchronously fetch on-disk log content for *cmd_name* on *host*.

        Blocks until the daemon's final chunk arrives or *timeout* elapses.
        Returns the concatenated content (may be empty if no log exists or
        if filters excluded everything).
        """
        slot = {"parts": [], "done": False, "event": threading.Event()}
        seq = self._cmd_seq
        with self._read_log_lock:
            self._read_log_pending[seq] = slot
        try:
            self._send_command(
                "read_log", name=cmd_name, hostname=host,
                since_us=since_us, tail_lines=tail_lines,
            )
            slot["event"].wait(timeout=timeout)
        finally:
            with self._read_log_lock:
                self._read_log_pending.pop(seq, None)
        return "".join(slot["parts"])

    def subscribe_output(
        self, cmd_name: str, host: str, ttl_seconds: int = 5
    ) -> None:
        """Tell the daemon to start (or extend) live output publishing for *cmd_name*.

        Subscriptions auto-expire — call again every ttl_seconds/2 or so
        to keep them alive while the user is watching. No explicit
        unsubscribe needed; just stop renewing.
        """
        self._send_command(
            "subscribe_output", name=cmd_name, hostname=host,
            ttl_seconds=int(ttl_seconds),
        )

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
    def proc_output_buffers_snapshot(self) -> Dict[str, str]:
        """Snapshot of the rolling text buffer per process. Empty if not subscribed."""
        with self._outputs_lock:
            return {k: s.buf for k, s in self._proc_output_states.items()}

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

    def _evict_stale_hosts(self) -> None:
        """Remove hosts that haven't reported within their expected interval."""
        now = time.monotonic()
        evicted_names: set = set()
        # Hold both locks to maintain the invariant that _hosts and _procs
        # are consistent (no orphaned proc entries for evicted hosts).
        with self._hosts_lock, self._procs_lock:
            stale = []
            for hostname, last_seen in self._host_last_seen.items():
                info = self._hosts.get(hostname)
                interval = getattr(info, "report_interval", 2.0) if info else 2.0
                if now - last_seen > interval * self._host_offline_factor:
                    stale.append(hostname)
            for hostname in stale:
                del self._hosts[hostname]
                del self._host_last_seen[hostname]
                for name in self._procs_by_host.pop(hostname, set()):
                    self._procs.pop((hostname, name), None)
                    evicted_names.add(name)
                logging.info("Client: evicted stale host %s", hostname)

            # Only evict output state for names that no surviving host still owns
            still_owned = {
                n for names in self._procs_by_host.values() for n in names
            }
            evictable = evicted_names - still_owned
        if evictable:
            self._evict_proc_output_states(evictable)

    def _thread_func(self) -> None:
        evict_counter = 0
        while self._running:
            # Snapshot the current subscriber so a concurrent reconnect that
            # swaps self.lc_sub cannot yank our reference mid-call. _init_lcm
            # keeps the old instance alive for > 100 ms after swap, which
            # outlasts the handle_timeout below.
            lc = self.lc_sub
            if lc is None:
                time.sleep(0.2)
                continue
            try:
                lc.handle_timeout(100)
            except OSError as e:
                # Same failure class as the node: lcm_handle_timeout() returned -1
                self._reconnect_lcm(e)
            except Exception as e:
                logging.exception("Client LCM handler error: %s", e)
                time.sleep(0.2)

            # Run eviction check every ~5 seconds (50 iterations × 100ms timeout)
            evict_counter += 1
            if evict_counter >= 50:
                evict_counter = 0
                self._evict_stale_hosts()
