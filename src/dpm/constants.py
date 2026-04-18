"""Shared constants for the DPM agent, client, CLI, and GUI."""

# Process state codes — single source of truth for the lifecycle.
#
# State machine transitions:
#   READY   →  RUNNING  (start_process succeeds)
#   READY   →  FAILED   (start_process raises)
#   RUNNING →  READY    (clean exit: code 0, or graceful stop via SIGTERM)
#   RUNNING →  FAILED   (non-zero exit detected by monitor_process)
#   RUNNING →  KILLED   (stop_process escalated to SIGKILL)
#   FAILED  →  RUNNING  (manual restart or auto_restart)
#   KILLED  →  RUNNING  (manual restart)
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
