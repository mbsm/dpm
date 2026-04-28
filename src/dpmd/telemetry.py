"""Telemetry publishers and helpers for the DPM daemon."""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import TYPE_CHECKING

import psutil

from dpm.constants import DPM_PROTOCOL_VERSION, STATE_DISPLAY

try:
    from dpm_msgs import (
        host_info_t,
        host_procs_t,
        log_chunk_t,
        proc_info_t,
    )
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Failed to import 'dpm_msgs'. Install the project via 'pip install -e .'."
    ) from e

from dpmd.limits import MAX_OUTPUT_CHUNK
from dpmd.processes import Proc, is_running

if TYPE_CHECKING:
    from dpmd.daemon import Daemon


def get_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def publish_host_info(d: "Daemon") -> None:
    """Publish host-wide telemetry (CPU, memory, network)."""
    current_time = time.time()
    time_diff = current_time - d.last_publish_time
    d.last_publish_time = current_time

    net_io = psutil.net_io_counters()
    net_tx = net_io.bytes_sent
    net_tx_diff = net_tx - d.last_net_tx
    d.last_net_tx = net_tx

    net_rx = net_io.bytes_recv
    net_rx_diff = net_rx - d.last_net_rx
    d.last_net_rx = net_rx

    sent_kbps = net_tx_diff / time_diff if time_diff > 0 else 0
    recv_kbps = net_rx_diff / time_diff if time_diff > 0 else 0

    cpu_usage = psutil.cpu_percent(interval=None) / 100.0
    uptime = int(time.time() - psutil.boot_time())
    mem = psutil.virtual_memory()

    msg = host_info_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.timestamp = int(time.time() * 1e6)
    # Refresh cached IP every 60 seconds
    now_mono = time.monotonic()
    if now_mono - d._ip_refresh_time > 60:
        d._cached_ip = get_ip()
        d._ip_refresh_time = now_mono

    msg.hostname = d.hostname
    msg.ip = d._cached_ip
    msg.cpus = d._cpu_count
    msg.cpu_usage = cpu_usage
    msg.mem_total = mem.total
    msg.mem_free = mem.free
    msg.mem_used = mem.used
    msg.mem_usage = mem.percent / 100.0
    msg.network_sent = sent_kbps / 1024
    msg.network_recv = recv_kbps / 1024
    msg.uptime = uptime
    msg.report_interval = d.host_status_timer.period
    msg.persist = d._persist

    try:
        d.lc.publish(d.host_info_channel, msg.encode())
    except OSError as e:
        logging.error("Failed to publish host info: %s", e)


def _htop_priority(pid: int, p: psutil.Process) -> int:
    """
    Match htop/top PRI column:
      - RT tasks (SCHED_FIFO/RR): negative rtprio (e.g., -40)
      - Normal tasks: 20 + nice (nice=0 -> 20)

    Takes the already-cached psutil.Process to avoid a per-tick constructor
    hit on low-end hardware with many managed processes.
    """
    policy = os.sched_getscheduler(pid)
    if policy in (os.SCHED_FIFO, os.SCHED_RR):
        rtprio = int(os.sched_getparam(pid).sched_priority)
        return -rtprio

    nice = int(p.nice())
    return 20 + nice


def _ensure_psutil_proc(d: "Daemon", proc_info: "Proc", pid: int) -> psutil.Process | None:
    p = proc_info.ps_proc
    if p is not None:
        return p

    try:
        p = psutil.Process(pid)
        p.cpu_percent(interval=None)
        proc_info.ps_proc = p
        return p
    except (psutil.Error, OSError, ValueError):
        return None


def _zero_proc_metrics(d: "Daemon", msg_proc: proc_info_t) -> None:
    msg_proc.cpu = 0.0
    msg_proc.mem_rss = 0
    msg_proc.mem_vms = 0
    msg_proc.priority = -1
    msg_proc.pid = -1
    msg_proc.ppid = -1
    msg_proc.runtime = 0


def _fill_proc_metrics(
    d: "Daemon", msg_proc: proc_info_t, proc_info: "Proc", pid: int
) -> None:
    p = _ensure_psutil_proc(d, proc_info, pid)
    if p is None:
        _zero_proc_metrics(d, msg_proc)
        return

    # Each metric can fail independently if the process exits mid-collection.
    # Granular try/except preserves already-collected values.
    _exc = (psutil.Error, OSError, ValueError)

    try:
        msg_proc.cpu = float(p.cpu_percent(interval=None)) / 100.0
    except _exc:
        msg_proc.cpu = 0.0

    try:
        mi = p.memory_info()
        msg_proc.mem_rss = int(mi.rss // 1024)
        msg_proc.mem_vms = int(mi.vms // 1024)
    except _exc:
        msg_proc.mem_rss = 0
        msg_proc.mem_vms = 0

    try:
        msg_proc.priority = int(_htop_priority(pid, p))
    except _exc:
        msg_proc.priority = 0

    try:
        msg_proc.ppid = int(p.ppid())
    except _exc:
        msg_proc.ppid = -1

    try:
        msg_proc.runtime = int(time.time() - p.create_time())
    except _exc:
        msg_proc.runtime = 0


def publish_host_procs(d: "Daemon") -> None:
    """Publish process-level telemetry for all managed processes."""
    msg = host_procs_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.timestamp = int(time.time() * 1e6)
    msg.hostname = d.hostname
    msg.procs = []
    msg.num_procs = 0

    for process_name, proc_info in d.processes.items():
        msg_proc = proc_info_t()

        msg_proc.name = process_name
        msg_proc.group = proc_info.group
        msg_proc.hostname = d.hostname
        msg_proc.state = proc_info.state
        msg_proc.status = STATE_DISPLAY.get(proc_info.state, "Ready").lower()
        msg_proc.errors = proc_info.errors
        msg_proc.exec_command = proc_info.exec_command
        msg_proc.auto_restart = proc_info.auto_restart
        msg_proc.realtime = proc_info.realtime
        msg_proc.isolated = proc_info.isolated
        msg_proc.exit_code = int(proc_info.exit_code)

        proc = proc_info.proc
        if proc is not None and is_running(proc):
            pid = int(proc.pid)
            msg_proc.pid = pid
            _fill_proc_metrics(d, msg_proc, proc_info, pid)
        else:
            _zero_proc_metrics(d, msg_proc)

        msg.procs.append(msg_proc)
        msg.num_procs += 1

    try:
        d.lc.publish(d.host_procs_channel, msg.encode())
    except OSError as e:
        logging.error("Failed to publish host procs: %s", e)


def publish_procs_outputs(d: "Daemon") -> None:
    """Tail each subscribed process's on-disk log and publish new bytes.

    Output is *not* held in memory by the daemon. The on-disk log file
    (``proc_info.log_file``) is the single source of truth: reader
    threads append to it line-by-line, ``read_log`` serves history from
    it, and this function tails it for live subscribers.

    State per subscription is just ``(byte_offset, inode)``. On the
    first cycle after subscribe we anchor at current EOF — new
    subscribers see only what arrives from this point forward. If a
    rotation is detected (inode changed, or file shrank), we reset to
    offset 0 of the new file. Subscriptions are short-TTL (~5 s) and
    refreshed by clients while they're actively following.
    """
    now_mono = time.monotonic()
    with d._subscriptions_lock:
        for name in [n for n, exp in d.output_subscriptions.items() if exp <= now_mono]:
            d.output_subscriptions.pop(name, None)
            d._log_offsets.pop(name, None)
            d._live_chunk_index.pop(name, None)
        active = list(d.output_subscriptions.keys())

    if not active:
        return

    now_us = int(time.time() * 1_000_000)
    for process_name in active:
        proc_info = d.processes.get(process_name)
        if proc_info is None or proc_info.log_file is None:
            continue
        path = proc_info.log_file.path

        try:
            st = os.stat(path)
        except OSError:
            continue

        prev = d._log_offsets.get(process_name)
        if prev is None:
            # First cycle since subscribe: anchor at current EOF; the
            # next cycle will ship anything written after this moment.
            d._log_offsets[process_name] = (st.st_size, st.st_ino)
            continue

        offset, inode = prev
        if inode != st.st_ino or st.st_size < offset:
            offset, inode = 0, st.st_ino  # rotated or truncated; reread

        if st.st_size <= offset:
            continue

        try:
            with open(path, "rb") as f:
                f.seek(offset)
                raw = f.read(MAX_OUTPUT_CHUNK)
        except OSError as e:
            logging.warning("publish_procs_outputs: read failed for %s: %s", path, e)
            continue

        # Trim to the last newline so we never ship a partial line. If
        # there is no newline at all, only ship when the chunk is full
        # (degenerate case: a single line longer than MAX_OUTPUT_CHUNK).
        last_nl = raw.rfind(b"\n")
        if last_nl == -1:
            if len(raw) < MAX_OUTPUT_CHUNK:
                continue  # wait for the rest of the line
            shipped_bytes = raw
        else:
            shipped_bytes = raw[: last_nl + 1]

        if not shipped_bytes:
            continue

        idx = d._live_chunk_index.get(process_name, 0)
        msg = log_chunk_t()
        msg.protocol_version = DPM_PROTOCOL_VERSION
        msg.request_seq = 0  # unsolicited live publish
        msg.timestamp = now_us
        msg.hostname = d.hostname
        msg.name = process_name
        msg.chunk_index = idx
        msg.last = False
        msg.content = shipped_bytes.decode("utf-8", errors="replace")
        try:
            d.lc.publish(d.log_chunks_channel, msg.encode())
        except OSError as e:
            logging.error("Failed to publish log chunk for %s: %s", process_name, e)
            return

        d._log_offsets[process_name] = (offset + len(shipped_bytes), inode)
        d._live_chunk_index[process_name] = idx + 1
