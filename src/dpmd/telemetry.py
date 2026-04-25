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
        d._handle_lcm_error(e)


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
        d._handle_lcm_error(e)


def publish_procs_outputs(d: "Daemon") -> None:
    """Publish live output chunks only for processes a client has subscribed to.

    Subscriptions are short-TTL (~5 s) entries refreshed by clients while
    they're actively watching with ``dpm logs --follow``. Without an
    active subscription, the daemon still reads pipes (so the child
    doesn't block) and writes to the on-disk log — it just doesn't
    publish on the wire. That's the whole point: silent-by-default.
    """
    now_mono = time.monotonic()
    with d._subscriptions_lock:
        # Drop expired entries up front so a chatty proc doesn't keep
        # re-checking a stale dict on every cycle.
        for name in [n for n, exp in d.output_subscriptions.items() if exp <= now_mono]:
            d.output_subscriptions.pop(name, None)
        active = set(d.output_subscriptions.keys())

    if not active:
        # Drain ring buffers anyway so they don't grow unbounded while
        # nobody's listening. Without this, `_OutBuf.append` would still
        # cap memory at MAX_OUTPUT_BUFFER, but we'd carry stale content
        # forward indefinitely.
        for proc_info in d.processes.values():
            proc_info.stdout.take(MAX_OUTPUT_CHUNK)
            proc_info.stderr.take(MAX_OUTPUT_CHUNK)
        return

    now_us = int(time.time() * 1_000_000)
    for process_name in active:
        proc_info = d.processes.get(process_name)
        if proc_info is None:
            continue

        # Peek (don't drain) so a publish failure leaves the bytes in the
        # ring buffer for the next cycle instead of silently losing them.
        stdout_chunk = proc_info.stdout.peek(MAX_OUTPUT_CHUNK)
        stderr_chunk = proc_info.stderr.peek(MAX_OUTPUT_CHUNK)
        if not stdout_chunk and not stderr_chunk:
            continue

        # Merge stderr after stdout for the live-publish path. The on-disk
        # log already has them interleaved; here we don't have line-level
        # ordering so a deterministic stdout-then-stderr is the best we
        # can do without a more invasive plumbing change.
        content = stdout_chunk + stderr_chunk
        idx = d._live_chunk_index.get(process_name, 0)

        msg = log_chunk_t()
        msg.protocol_version = DPM_PROTOCOL_VERSION
        msg.request_seq = 0  # unsolicited live publish
        msg.timestamp = now_us
        msg.hostname = d.hostname
        msg.name = process_name
        msg.chunk_index = idx
        msg.last = False
        msg.content = content
        try:
            d.lc.publish(d.log_chunks_channel, msg.encode())
        except OSError as e:
            logging.error("Failed to publish log chunk for %s: %s", process_name, e)
            d._handle_lcm_error(e)
            return  # stop publishing this cycle; LCM will be reinitialized

        # Commit only on successful publish: drain the bytes we just sent
        # and bump chunk_index so a follower sees a contiguous sequence.
        proc_info.stdout.take(len(stdout_chunk))
        proc_info.stderr.take(len(stderr_chunk))
        d._live_chunk_index[process_name] = idx + 1
