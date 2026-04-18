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


def command_handler(d: "Daemon", channel, data) -> None:
    """Handle incoming command messages."""
    msg = command_t.decode(data)

    # Ignore commands not addressed to this host. An empty hostname is
    # treated as a broadcast (applies to all nodes).
    if msg.hostname and msg.hostname != d.hostname:
        return

    # Drop duplicate or reordered UDP commands via monotonic seq.
    # Accept seq==0 when last>0 as a client-restart signal.
    dedup_key = (msg.hostname, msg.action, msg.name)
    with d._last_seq_lock:
        last = d._last_seq.get(dedup_key, -1)
        if msg.seq <= last and not (msg.seq == 0 and last > 0):
            logging.debug("Dropping duplicate command seq=%d key=%s", msg.seq, dedup_key)
            return
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
            isolated=msg.isolated,
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
