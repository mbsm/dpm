"""Shared constants for the DPM daemon, client, CLI, and GUI."""

# DPM wire-protocol version. Bump whenever an LCM schema changes shape
# (field added/removed/reordered). Peers compare this number on every
# message and drop mismatches.
#
# Pre-1.0: versions are numbered from 1. There is no compatibility
# window — all daemons + clients must be upgraded in lockstep.
DPM_PROTOCOL_VERSION = 1

# Process state codes — single source of truth for the lifecycle.
#
# State machine transitions:
#   READY     →  RUNNING    (start_process succeeds)
#   READY     →  FAILED     (start_process raises)
#   RUNNING   →  READY      (clean exit: code 0, or graceful stop via stop_signal)
#   RUNNING   →  FAILED     (non-zero exit detected by monitor_process)
#   RUNNING   →  KILLED     (stop_process escalated to SIGKILL)
#   FAILED    →  RUNNING    (manual restart or auto_restart with backoff)
#   FAILED    →  SUSPENDED  (auto_restart hit max_restarts — circuit breaker)
#   KILLED    →  RUNNING    (manual restart)
#   SUSPENDED →  RUNNING    (manual start — clears restart_count)
#
STATE_READY = "T"
STATE_RUNNING = "R"
STATE_FAILED = "F"
STATE_KILLED = "K"
STATE_SUSPENDED = "S"

# Human-readable labels derived from state codes.
STATE_DISPLAY = {
    STATE_READY: "Ready",
    STATE_RUNNING: "Running",
    STATE_FAILED: "Failed",
    STATE_KILLED: "Killed",
    STATE_SUSPENDED: "Suspended",
}

# Seconds without a telemetry update before a host is considered offline.
HOST_OFFLINE_THRESHOLD_SEC = 5.0
