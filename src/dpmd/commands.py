"""LCM command dispatch for the DPM daemon."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from dpm_msgs import command_t
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Failed to import 'dpm_msgs'. Install the project via 'pip install -e .'."
    ) from e

from dpm.constants import DPM_PROTOCOL_VERSION
from dpmd.processes import (
    create_process,
    delete_process,
    start_group,
    start_process,
    stop_group,
    stop_process,
)

if TYPE_CHECKING:
    from dpmd.daemon import Daemon


# Gap in command seq (microseconds) that's large enough to be interpreted
# as a client restart rather than a late duplicate. The client seeds its
# seq from wall-clock microseconds, so a jump backwards by more than this
# threshold cannot happen within a single live client session and is
# therefore safe to treat as a restart (or a clock correction).
_SEQ_RESTART_THRESHOLD_USEC = 60_000_000  # 60 s


# Dispatch: action -> (target, msg_attribute).
# `target` is either:
#   - a function-name string (resolved in this module via globals()) for
#     free functions in dpmd.processes re-exported here — late-binding lets
#     tests patch("dpmd.commands.start_process") intercept the call, and
#   - a method-name string prefixed with "d." for Daemon methods.
_CMD_DISPATCH = {
    "start_process": ("start_process", "name"),
    "stop_process": ("stop_process", "name"),
    "delete_process": ("delete_process", "name"),
    "start_group": ("start_group", "group"),
    "stop_group": ("stop_group", "group"),
    "set_interval": ("d.set_interval", "exec_command"),
    "set_persistence": ("d.set_persistence", "exec_command"),
}


_logged_version_mismatch: dict = {}


def command_handler(d: "Daemon", channel, data) -> None:
    """Handle incoming command messages."""
    msg = command_t.decode(data)

    # Drop messages from peers on a different wire-protocol version.
    # Logged once per (sender, version) pair to avoid flooding the journal.
    if msg.protocol_version != DPM_PROTOCOL_VERSION:
        key = (msg.hostname or "?", msg.protocol_version)
        if key not in _logged_version_mismatch:
            _logged_version_mismatch[key] = True
            logging.warning(
                "Dropping command with protocol_version=%d from %s "
                "(expected %d).",
                msg.protocol_version, key[0], DPM_PROTOCOL_VERSION,
            )
        return

    # Ignore commands not addressed to this host. An empty hostname is
    # treated as a broadcast (applies to all nodes).
    if msg.hostname and msg.hostname != d.hostname:
        return

    # Drop duplicate or reordered UDP commands via monotonic seq.
    # If seq jumps backwards by more than _SEQ_RESTART_THRESHOLD_USEC, treat
    # it as a client restart or clock correction and accept. Keeps the daemon
    # responsive if a client was started with a skewed clock and later fixed.
    dedup_key = (msg.hostname, msg.action, msg.name)
    with d._last_seq_lock:
        last = d._last_seq.get(dedup_key, -1)
        if msg.seq <= last and (last - msg.seq) < _SEQ_RESTART_THRESHOLD_USEC:
            logging.debug("Dropping duplicate command seq=%d key=%s", msg.seq, dedup_key)
            return
        if last >= 0 and msg.seq < last:
            logging.info(
                "Accepting seq rollback (client restart?) key=%s last=%d new=%d",
                dedup_key, last, msg.seq,
            )
        # Evict oldest entry (FIFO) if cap reached
        if dedup_key not in d._last_seq and len(d._last_seq) >= d._LAST_SEQ_MAX_KEYS:
            d._last_seq.popitem(last=False)
        d._last_seq[dedup_key] = msg.seq

    action = msg.action

    if action == "create_process":
        # Look up via globals() so tests can patch("dpmd.commands.create_process").
        globals()["create_process"](
            d,
            msg.name, msg.exec_command, msg.auto_restart, msg.realtime, msg.group,
            work_dir=msg.work_dir, cpuset=msg.cpuset,
            cpu_limit=msg.cpu_limit, mem_limit=msg.mem_limit,
            isolated=msg.isolated, rt_priority=msg.rt_priority,
        )
    elif action in _CMD_DISPATCH:
        target, attr = _CMD_DISPATCH[action]
        value = getattr(msg, attr)
        if target.startswith("d."):
            # Daemon method (e.g. set_interval, set_persistence)
            getattr(d, target[2:])(value)
        else:
            # Free function in this module — look up via globals() for late
            # binding so tests can patch the name at module scope.
            globals()[target](d, value)
    else:
        logging.warning("Unknown action: %s", action)
