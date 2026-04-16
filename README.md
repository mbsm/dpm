# DPM — Distributed Process Manager

A lightweight distributed process manager for trusted Linux clusters.
DPM uses [LCM](https://lcm-proj.github.io/) multicast for real-time control and telemetry, enabling operators to manage, monitor, and orchestrate processes across multiple hosts from a single interface.

## Features

- **Multi-host process management** — create, start, stop, restart, move, and delete processes on any host in the cluster
- **Live telemetry** — CPU, memory, and network metrics streamed over LCM multicast
- **Dependency-aware launch files** — declarative YAML-based startup and shutdown with parallel wave execution
- **Resource isolation** — per-process cgroups v2 with cpuset pinning, CPU bandwidth limits, and memory caps
- **Auto-restart with backoff** — exponential backoff with configurable circuit breaker (max restart limit)
- **Process persistence** — optionally save and restore process definitions across agent restarts
- **Group operations** — batch start/stop by named process groups
- **Multiple interfaces** — PyQt5 GUI, full-featured CLI, or build your own client on top of the Supervisor library

## Architecture

```
 ┌─────────────────────────────────────────────────────────┐
 │                      LCM Multicast                      │
 └────────┬──────────────────────────────────┬─────────────┘
          │                                  │
   ┌──────▼──────┐                    ┌──────▼──────┐
   │    Agent     │  (one per host)   │  Supervisor │  (one instance)
   │              │                   │             │
   │ - manages    │   host_info_t ──► │ - aggregates│
   │   local      │   host_procs_t ─► │   telemetry │
   │   processes  │   proc_output_t ► │ - sends     │
   │ - publishes  │                   │   commands  │
   │   telemetry  │ ◄── command_t ─── │             │
   └──────────────┘                   └──────┬──────┘
                                             │
                                      ┌──────▼──────┐
                                      │     UI      │
                                      │  (PyQt GUI) │
                                      │             │
                                      │ - displays  │
                                      │   hosts &   │
                                      │   processes │
                                      │ - operator  │
                                      │   controls  │
                                      └─────────────┘
```

### Agent (`dpm-agent`)

Runs on each host in the cluster, typically as a systemd service:

- Manages local processes (create, start, stop, delete)
- Monitors process liveness and exit status
- Publishes host and process telemetry over LCM
- Streams stdout/stderr output to the supervisor
- Auto-restarts failed processes with exponential backoff
- Supports per-process working directory, cpuset isolation, and CPU/memory cgroup limits

### Supervisor

A UI-agnostic library that any client can use to interact with the cluster:

- Subscribes to agent telemetry over LCM (host info, process snapshots, output)
- Sends commands to agents (create, start, stop, delete — individual or batch)
- Maintains thread-safe state snapshots for the UI layer
- Handles LCM reconnection with backoff

### GUI (`dpm-gui`)

A PyQt5 desktop application built on top of the Supervisor:

- Host cards with online/offline status, CPU/memory usage, process counts, persistence mode, and telemetry interval
- Process tree grouped by process group with status, CPU, memory, auto-restart, CPU isolation, and priority columns
- Context menus for start/stop/edit/move/delete operations
- Live process output in modeless windows
- Save/load process specs as YAML
- Declarative launch/shutdown with a live progress dialog

### CLI (`dpm`)

A command-line interface for scripting, headless servers, and automation. No PyQt5 dependency — works on any system with Python.

```bash
# Status & monitoring
dpm status                              # all hosts and processes
dpm status @jet1                        # filter to one host
dpm hosts                               # hosts only
dpm logs camera@jet1                    # stream output (Ctrl+C to stop)

# Process control
dpm create camera@jet1 --cmd "cam-node" -g perception --auto-restart
dpm start camera@jet1
dpm stop camera@jet1
dpm restart camera@jet1
dpm delete camera@jet1                  # stop and remove
dpm move camera@jet1 @jet2              # migrate to another host

# Group operations
dpm start-group perception@jet1
dpm stop-group perception@jet1
dpm start-all
dpm stop-all

# Spec files
dpm save snapshot.yaml                  # export current state
dpm load system.yaml                    # import process definitions

# Launch files
dpm launch startup.yaml                 # dependency-based startup
dpm shutdown startup.yaml               # reverse-order shutdown

# Agent configuration
dpm set-interval @jet1 2                # telemetry interval (seconds)
dpm set-persistence @jet1 on            # enable process persistence
```

## Quick Start

### Prerequisites

- Python 3.10+
- LCM library installed ([lcm-proj.github.io](https://lcm-proj.github.io/))

### Install from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui,dev]"
```

### Run

```bash
# Start agent
DPM_CONFIG=./dpm.yaml dpm-agent

# Start GUI (in another terminal)
DPM_CONFIG=./dpm.yaml dpm-gui
```

To run directly from the repo without installing:

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.agent.agent
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main
```

## Configuration

Default config path: `/etc/dpm/dpm.yaml`. Override with the `DPM_CONFIG` environment variable.
A local example is provided in the repository root (`dpm.yaml`).

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

Both the agent and supervisor read the same config file. The agent uses all fields; the supervisor only needs the LCM-related fields (URL and channel names).

## Process Lifecycle

Each managed process follows this state machine:

```
                    ┌──────────────────────────────────┐
                    │                                  │
  create ──► READY ──► start ──► RUNNING               │
               ▲                   │                   │
               │         ┌─────────┼──────────┐        │
               │         ▼         ▼          ▼        │
               │     exit(0)   exit(!=0)   SIGKILL     │
               │         │         │          │        │
               │         ▼         ▼          ▼        │
               └──── READY      FAILED     KILLED      │
                                  │                    │
                                  ├── auto_restart ────┘
                                  │   (with backoff)
                                  │
                                  └── max_restarts exceeded
                                          │
                                          ▼
                                      SUSPENDED
                                          │
                                          └── manual start ──► RUNNING
```

| State | Code | Description |
|-------|------|-------------|
| **READY** | `T` | Created or cleanly stopped (exit code 0 or graceful stop) |
| **RUNNING** | `R` | Process is alive |
| **FAILED** | `F` | Exited with non-zero code or failed to start |
| **KILLED** | `K` | Forcefully killed (SIGKILL after stop timeout) |
| **SUSPENDED** | `S` | Auto-restart attempts exhausted (circuit breaker tripped) |

Auto-restart triggers only on FAILED with exponential backoff (1 s → 2 s → 4 s → … capped at 60 s). The backoff counter resets on clean exit (code 0). When `max_restarts` is configured and exceeded, the process enters SUSPENDED. A manual `dpm start` clears the counter and resumes normal operation.

## Resource Isolation

Processes can be assigned dedicated CPU cores and resource limits using cgroups v2:

```bash
dpm create slam@jet1 --cmd "slam-node" \
    --cpuset 2,3 \
    --isolated \
    --cpu-limit 2.0 \
    --mem-limit 4294967296
```

| Flag | cgroup control | Example | Description |
|------|----------------|---------|-------------|
| `--cpuset` | `cpuset.cpus` | `0,1` | Pin to specific cores |
| `--isolated` | `cpuset.cpus.partition` | — | Reserve cores exclusively (no sharing) |
| `--cpu-limit` | `cpu.max` | `1.5` | CPU bandwidth limit in cores |
| `--mem-limit` | `memory.max` | `4294967296` | Memory limit in bytes (4 GB) |

### CPU isolation

The `--isolated` flag reserves cores exclusively for a process:

- **CPU affinity** — the process is pinned to the specified cores via `cpuset.cpus`
- **Exclusive reservation** — no other isolated process can claim the same cores (validated at start time)
- **Realtime scheduling** — combine with `--realtime` for `SCHED_FIFO` priority on the pinned cores

This provides low-preemption execution suitable for latency-sensitive workloads such as motion controllers or localization nodes.

For full kernel-level scheduler isolation, add `isolcpus=<cores>` to the kernel command line (e.g., `isolcpus=20-23`). DPM's cpuset affinity then ensures only your processes run on those reserved cores.

The agent creates cgroup directories under `/sys/fs/cgroup/dpm/<process>/` and cleans them up on stop/delete.

**Requirements:** cgroups v2 unified hierarchy and `Delegate=yes` in the systemd service unit (included in the `dpm-agent` package). On development machines without cgroup access, processes run without limits (graceful degradation).

## Launch Files

Launch files define a declarative dependency graph for multi-host startup and shutdown. Groups start in parallel waves resolved from their dependencies.

```yaml
name: "AGV1 Full System"
timeout: 30                  # default wait timeout per group (seconds)

# Optional: create processes before launching (skipped on shutdown)
processes:
  - name: VLP 16 Node
    host: sensor-host
    cmd: /opt/agv/bin/vlp16_node
    group: Sensors
  - name: SLAM Node
    host: perception-host
    cmd: /opt/agv/bin/slam_node
    group: Perception

# Dependency graph
groups:
  Sensors: {}                          # no deps → wave 1
  Input: {}                            # no deps → wave 1
  Simulation: {}                       # no deps → wave 1
  Perception:
    requires: [Sensors]                # hard: fails if Sensors fails
  Planning:
    requires: [Perception]             # hard chain: Sensors → Perception → Planning
    after: [Input]                     # soft: waits for Input, continues on failure
```

### Dependency types

| Directive | Behavior |
|-----------|----------|
| `requires: [A, B]` | Hard dependency + ordering. If A or B fail, this group and all dependents fail. |
| `after: [A, B]` | Soft ordering. Wait for A and B, but continue even if they fail. |

Both accept a list of group names. Groups with neither directive start immediately (wave 1). `requires` implies `after`.

### Execution model

**Launch** resolves the dependency graph via topological sort into parallel waves:

```
Wave 1: Sensors, Input, Simulation    (no dependencies — parallel)
Wave 2: Perception                    (requires Sensors — now running)
Wave 3: Planning                      (requires Perception, after Input — both ready)
```

Each wave starts all groups in parallel, then waits for every process to reach RUNNING before proceeding.

**Shutdown** reverses wave order: Planning stops first, then Perception, then Sensors/Input/Simulation in parallel.

### Failure behavior

- `requires` chain failure → all downstream groups aborted
- `after` chain failure → dependent groups continue with a warning
- Shutdown timeouts produce warnings but do not block the sequence

## Message Protocol

| Direction | Message | Channel | Content |
|-----------|---------|---------|---------|
| Supervisor → Agent | `command_t` | `command_channel` | Process commands with seq-based UDP dedup |
| Agent → Supervisor | `host_info_t` | `host_info_channel` | Host telemetry (CPU, memory, network) |
| Agent → Supervisor | `host_procs_t` | `host_procs_channel` | Process table snapshot |
| Agent → Supervisor | `proc_output_t` | `proc_outputs_channel` | stdout/stderr chunks |

## Packaging

### Install extras

```bash
pip install -e ".[gui]"      # GUI + supervisor
pip install -e ".[dev]"      # Development (pytest)
pip install -e ".[gui,dev]"  # Everything
```

### Debian packages

Build `.deb` packages for production deployment:

```bash
sudo apt install debhelper dh-python python3-all python3-setuptools
dpkg-buildpackage -us -uc -b
```

This produces three packages:

| Package | Purpose |
|---------|---------|
| `python3-dpm` | Python library and all binaries (`dpm-agent`, `dpm`, `dpm-gui`) |
| `dpm-agent` | Systemd service, config, RT limits (depends on `python3-dpm`) |
| `dpm-tools` | Desktop entry and icon for GUI (depends on `python3-dpm`) |

### Deployment

```bash
# On each cluster host
sudo apt install ./dpm-agent_0.1.0_all.deb

# On the operator workstation
sudo apt install ./dpm-tools_0.1.0_all.deb
```

The `dpm-agent` package installs the systemd service, creates a `dpm` system user with appropriate group memberships (video, render, plugdev), sets up log and data directories, and enables the service automatically.

```bash
systemctl status dpm-agent           # check service status
journalctl -u dpm-agent -f           # follow logs
```

Removal: `apt remove dpm-agent` stops and disables the service. `apt purge dpm-agent` also removes config and log files.

## Logging

| Context | Destination | Notes |
|---------|-------------|-------|
| Agent under systemd | `journalctl -u dpm-agent` | stdout/stderr captured by journald |
| Agent standalone | `/var/log/dpm/dpm-agent.log` | Rotating file (10 MB × 5 backups) |
| Agent in development | stdout | Console output |
| GUI | stderr | Standard Python logging |

The agent auto-detects systemd (via `INVOCATION_ID` / `JOURNAL_STREAM` env vars) and disables file logging when running under journald. Log level is controlled by the `DPM_LOG_LEVEL` environment variable (default: `INFO`).

## Testing

```bash
pytest                    # unit tests (no network required)
pytest -m integration     # integration tests (requires live LCM multicast)
```

## Project Structure

```
src/
  dpm/
    agent/            # Agent daemon — one per host
    supervisor/       # Supervisor library — telemetry aggregation, command dispatch
    cli/              # CLI tool — scriptable interface
    gui/              # PyQt5 GUI — desktop application
    utils/            # Shared utilities (config loading, local agent management)
    constants.py      # Shared state constants and thresholds
    spec_io.py        # YAML process spec save/load
  dpm_msgs/           # Generated LCM message bindings
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Matias Bustos SM
