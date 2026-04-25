# Configuration

Default config path: `/etc/dpm/dpm.yaml`. Override with the `DPM_CONFIG` environment variable. A local example is provided in the repository root (`dpm.yaml`).

Both the daemon and client read the same file. The daemon uses all fields; the client only needs the LCM-related fields.

```yaml
# LCM transport
lcm_url: "udpm://239.255.76.67:7667?ttl=1"

# LCM channel names
command_channel: "DPM/commands"
host_info_channel: "DPM/host_info"
log_chunks_channel: "DPM/log_chunks"
host_procs_channel: "DPM/host_procs"

# Timer intervals (seconds) — how often the daemon publishes telemetry
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

# Process registry persistence (daemon only)
# When true, process definitions are saved to disk and reloaded on daemon restart.
# Processes with auto_restart=true are started automatically on reload.
# persist_processes: false
# persist_path: /var/lib/dpm/processes.yaml

# Per-process on-disk log files (daemon only).
# The single source of truth for process output: reader threads write
# merged stdout+stderr here, `dpm logs` reads from it, and the live
# publisher tails it for subscribers. Set process_log_dir to "" to
# disable process output entirely (no in-memory fallback).
process_log_dir: /var/log/dpm/processes
process_log_max_bytes: 52428800     # 50 MB; rotated to <name>.log.1, .2, ...
process_log_backups: 3
```

## Logging

DPM writes two distinct kinds of logs.

**Daemon's own log** — events from the dpmd process itself (commands received, processes spawned, errors):

| Context | Destination | Notes |
|---------|-------------|-------|
| Daemon under systemd | `journalctl -u dpmd` | stdout/stderr captured by journald |
| Daemon standalone | `/var/log/dpm/dpmd.log` | Rotating file (10 MB × 5 backups) |
| Daemon in development | stdout | Console output |
| GUI | stderr | Standard Python logging |

The daemon auto-detects systemd (via `INVOCATION_ID` / `JOURNAL_STREAM` env vars) and disables file logging when running under journald. Log level is controlled by the `DPM_LOG_LEVEL` environment variable (default: `INFO`).

**Per-process logs** — merged stdout+stderr of every managed process, in chronological order, plus daemon-emitted markers for lifecycle events (`start cmd=…`, `exit code=N`, `warning: …`, `start failed: …`):

| Path | Notes |
|------|-------|
| `<process_log_dir>/<name>.log` | Active log; line-buffered append. Default `/var/log/dpm/processes/<name>.log` |
| `<process_log_dir>/<name>.log.1` … `.N` | Size-rotated backups; `process_log_backups` generations kept |
| `<process_log_dir>/<name>.log.crash` | Forensic breadcrumb appended on every non-zero exit (exit code, restart count, last 4 KB of output). Survives rotation. |

These files are the canonical output store. `dpm logs <name>` reads from here. The GUI's "view output" panel reads history from here (via `read_log` RPC) and follows new bytes via the live `log_chunks_channel`.
