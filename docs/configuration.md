# Configuration

Default config path: `/etc/dpm/dpm.yaml`. Override with the `DPM_CONFIG` environment variable. A local example is provided in the repository root (`dpm.yaml`).

Both the agent and supervisor read the same file. The agent uses all fields; the supervisor only needs the LCM-related fields.

```yaml
# LCM transport
lcm_url: "udpm://239.255.76.67:7667?ttl=1"

# LCM channel names
command_channel: "DPM/commands"
host_info_channel: "DPM/host_info"
proc_outputs_channel: "DPM/proc_outputs"
host_procs_channel: "DPM/host_procs"

# Timer intervals (seconds) — how often the agent publishes telemetry
monitor_interval: 1
output_interval: 1
host_status_interval: 1
procs_status_interval: 1

# Timeout for graceful stop before SIGKILL (seconds)
stop_timeout: 2

# Signal sent for graceful stop (SIGKILL escalation unchanged)
stop_signal: "SIGINT"

# Maximum auto-restart attempts before suspending (-1 = unlimited)
max_restarts: -1

# Realtime scheduling priority for processes with realtime=true (1–99)
# rt_priority: 40

# Process registry persistence (agent only)
# When true, process definitions are saved to disk and reloaded on agent restart.
# Processes with auto_restart=true are started automatically on reload.
# persist_processes: false
# persist_path: /var/lib/dpm/processes.yaml
```

## Logging

| Context | Destination | Notes |
|---------|-------------|-------|
| Agent under systemd | `journalctl -u dpm-agent` | stdout/stderr captured by journald |
| Agent standalone | `/var/log/dpm/dpm-agent.log` | Rotating file (10 MB × 5 backups) |
| Agent in development | stdout | Console output |
| GUI | stderr | Standard Python logging |

The agent auto-detects systemd (via `INVOCATION_ID` / `JOURNAL_STREAM` env vars) and disables file logging when running under journald. Log level is controlled by the `DPM_LOG_LEVEL` environment variable (default: `INFO`).
