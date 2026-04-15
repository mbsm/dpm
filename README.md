# DPM (Distributed Process Manager)

DPM is a lightweight distributed process manager for trusted Linux clusters.
It uses LCM multicast for control and telemetry.

> Inspired by `libbot procman`, built primarily as a learning project.

## Architecture

DPM has two core components connected over LCM multicast:

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

Runs on each host in the cluster, typically as a systemd service. Responsibilities:

- Manage local processes: create, start, stop, delete
- Monitor process liveness and exit status
- Publish host telemetry (CPU, memory, network) and process state over LCM
- Stream process stdout/stderr output to the supervisor
- Auto-restart failed processes with exponential backoff (with configurable max restart limit)
- Per-process working directory, cpuset isolation, CPU/memory cgroup limits

Source: `src/dpm/agent/agent.py`

### Supervisor

The supervisor is a library that any user interface can use to interact with the system. It:

- Subscribes to agent telemetry over LCM (host info, process snapshots, output)
- Sends commands to agents (create/start/stop/delete processes and groups)
- Maintains thread-safe state snapshots for the UI layer
- Handles LCM reconnection with backoff

Source: `src/dpm/supervisor/supervisor.py`

### CLI (`dpm`)

A command-line interface for scripting, headless servers, and automation. Uses the `@host` syntax:

```bash
dpm status                              # all hosts + processes
dpm status @jet1                        # filter to one host
dpm hosts                               # hosts only
dpm start camera@jet1                   # start a process
dpm stop camera@jet1                    # stop a process
dpm restart camera@jet1                 # stop + start
dpm create camera@jet1 --cmd "cam-node" -g perception --auto-restart
dpm create slam@jet2 --cmd "slam-node" --work-dir /opt/robot --cpuset 0,1 --isolated --cpu-limit 2.0 --mem-limit 4294967296
dpm delete camera@jet1                  # stop + remove
dpm move camera@jet1 @jet2              # move process to another host
dpm start-group perception@jet1         # batch start
dpm stop-group perception@jet1          # batch stop
dpm load system.yaml                    # create processes from spec
dpm save snapshot.yaml                  # save current state
dpm start-all                           # start every process
dpm stop-all                            # stop every process
dpm logs camera@jet1                    # stream output (Ctrl+C)
dpm set-interval @jet1 2               # set telemetry interval (seconds)
dpm set-persistence @jet1 on           # enable process persistence
dpm launch startup.yaml                 # declarative dependency-based startup
dpm shutdown startup.yaml               # reverse wave order shutdown
```

No PyQt5 dependency — works on headless systems.

Source: `src/dpm/cli/`

### GUI (`dpm-gui`)

A PyQt5 desktop application. It uses the Supervisor to:

- Display host cards with online/offline status, CPU/memory usage, process counts, persistence mode, and telemetry interval
- Right-click host cards to toggle persistence or change telemetry interval
- Show a process tree grouped by process group with columns for status, CPU, memory, auto-restart, CPU isolation, and priority
- Start/stop/edit/move/delete processes via context menus
- Stream live process output in modeless windows with a working clear button
- Save/load process specs as YAML files
- Launch/shutdown via declarative launch files with a live progress dialog

The Supervisor is designed to be UI-agnostic — a CLI, web frontend, or any custom client could use the same Supervisor class.

Source: `src/dpm/gui/`

### Shared utilities

- `src/dpm/spec_io.py` — YAML-based process spec save/load
- `src/dpm/utils/config.py` — shared config loading for agent and supervisor
- `src/dpm/utils/local_agent.py` — spawn/stop a local agent from the GUI

## Process State Machine

Each managed process follows this lifecycle:

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

States:
- **READY** (`T`): created or cleanly stopped (exit code 0 or graceful stop)
- **RUNNING** (`R`): process is alive
- **FAILED** (`F`): process exited with non-zero code, or failed to start
- **KILLED** (`K`): process was forcefully killed (SIGKILL after stop timeout)
- **SUSPENDED** (`S`): auto-restart attempts exhausted (circuit breaker tripped)

Auto-restart triggers only on FAILED state with exponential backoff (1s, 2s, 4s, ... capped at 60s).
When `max_restarts` is configured and exceeded, the process enters SUSPENDED instead of restarting.
A manual `dpm start` clears the counter and resumes normal operation.

## Command Actions

- `create_process` — register a process definition (stops existing if running); supports `isolated` flag for CPU partition isolation
- `start_process` — launch the process (any non-RUNNING state; clears SUSPENDED counter)
- `stop_process` — configurable signal (default SIGINT) → wait → SIGKILL if needed
- `delete_process` — stop + remove from registry + cleanup cgroup
- `start_group` / `stop_group` — batch operations by group name
- `set_interval` / `set_persistence` — runtime agent configuration (persisted across restarts when persistence is enabled)

## Message Flow

- Supervisor → Agent: `command_t` on `command_channel` (with seq-based UDP dedup)
- Agent → Supervisor:
  - `host_info_t` — host telemetry (CPU, memory, network)
  - `host_procs_t` — process table snapshot
  - `proc_output_t` — stdout/stderr chunks

## Quick Start (development)

### 1) Create environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui,dev]"
```

### 2) Start agent

```bash
DPM_CONFIG=./dpm.yaml dpm-agent
```

### 3) Start GUI

```bash
DPM_CONFIG=./dpm.yaml dpm-gui
```

### Running without install (repo mode)

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.agent.agent
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main
```

## Configuration

Default config path: `/etc/dpm/dpm.yaml`. Override with `DPM_CONFIG` environment variable.
A local example is provided in `dpm.yaml`.

```yaml
# LCM transport
lcm_url: "udpm://239.255.76.67:7667?ttl=1"

# LCM channel names
command_channel: "DPM/commands"
host_info_channel: "DPM/host_info"
proc_outputs_channel: "DPM/proc_outputs"
host_procs_channel: "DPM/host_procs"

# Timer intervals (seconds) — how often the agent publishes telemetry
monitor_interval: 1        # check process liveness
output_interval: 1         # publish stdout/stderr chunks
host_status_interval: 1    # publish host CPU/memory/network
procs_status_interval: 1   # publish process table snapshot

# Timeout for graceful stop before SIGKILL (seconds)
stop_timeout: 2

# Signal sent for graceful stop (SIGKILL escalation unchanged)
stop_signal: "SIGINT"

# Maximum auto-restart attempts before suspending (-1 = unlimited)
max_restarts: -1

# Realtime scheduling priority for processes with realtime=true (1-99)
# rt_priority: 40

# Process registry persistence (agent only).
# When true, process definitions are saved to disk and reloaded on agent restart.
# Processes with auto_restart=true are started automatically on reload.
# persist_processes: false
# persist_path: /var/lib/dpm/processes.yaml
```

Both the agent and supervisor read the same config file. The agent uses all fields;
the supervisor only needs the LCM-related fields (URL and channel names).

## Resource Isolation (cgroups v2)

Processes can be assigned dedicated CPU cores and resource limits using cgroups v2:

```bash
dpm create slam@jet1 --cmd "slam-node" \
    --cpuset 2,3 \
    --isolated \
    --cpu-limit 2.0 \
    --mem-limit 4294967296
```

| Flag | cgroup file | Example | Description |
|------|-------------|---------|-------------|
| `--cpuset` | `cpuset.cpus` | `0,1` | Pin to specific cores (CPU affinity) |
| `--isolated` | `cpuset.cpus.partition` | — | Remove cores from general scheduler (true isolation) |
| `--cpu-limit` | `cpu.max` | `1.5` | CPU bandwidth limit in cores |
| `--mem-limit` | `memory.max` | `4294967296` | Memory limit in bytes (4 GB) |

### CPU isolation

The `--isolated` flag reserves cores exclusively for a process:

- **CPU affinity** — the process is pinned to the specified cores via `cpuset.cpus`
- **Exclusive reservation** — no other isolated process can claim the same cores (validated at start time)
- **Realtime scheduling** — combine with `--realtime` for `SCHED_FIFO` priority on the pinned cores

This gives low-preemption execution suitable for latency-sensitive processes like motion controllers or localization nodes.

For full kernel-level scheduler isolation (removing cores from the general scheduler entirely), add `isolcpus=<cores>` to the kernel command line. For example, `isolcpus=20-23` reserves cores 20-23 at boot. DPM's cpuset affinity then ensures only your processes use those cores.

The agent creates cgroup directories under `/sys/fs/cgroup/dpm/<process_name>/` and cleans them up on stop/delete.

**Requirements:** cgroups v2 unified hierarchy and `Delegate=yes` in the systemd service unit (included in the dpm-agent package). On dev machines without cgroup access, processes run without limits (graceful degradation).

## Launch Files

Launch files use a declarative dependency graph to orchestrate multi-host startup and
shutdown. Groups start in parallel waves resolved from their dependencies.

### Syntax

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
  # ...

# Dependency graph — groups start in parallel where dependencies allow
groups:
  Sensors: {}                          # no deps → starts first
  Input: {}                            # no deps → starts first
  Simulation: {}                       # no deps → starts first
  Perception:
    requires: [Sensors]                # hard dep: won't start if Sensors fails
  Planning:
    requires: [Perception]             # hard dep chain: Sensors → Perception → Planning
    after: [Input]                     # soft dep: waits for Input, but starts anyway if it fails
```

### Dependency types

| Directive | Meaning |
|-----------|---------|
| `requires: [A, B]` | Hard dependency + ordering. If A or B fail to start, this group and all its dependents fail. |
| `after: [A, B]` | Pure ordering. Wait for A and B before starting, but continue even if they fail. |

Both accept a list of group names. A group with neither starts immediately (wave 1).
`requires` implies `after` — no need to specify both.

### Execution

```bash
dpm launch system.yaml       # resolve graph → start in parallel waves
dpm shutdown system.yaml     # reverse wave order → stop in parallel
```

**Launch** resolves the dependency graph into waves via topological sort:

```
Wave 1: Sensors, Input, Simulation    (no dependencies — parallel)
Wave 2: Perception                    (requires Sensors — now running)
Wave 3: Planning                      (requires Perception, after Input — both ready)
```

Each wave starts all its groups in parallel, then waits for every process in those
groups to reach Running before proceeding to the next wave.

**Shutdown** reverses the wave order: Planning stops first, then Perception, then
Sensors/Input/Simulation in parallel.

### Failure behavior

- If a group in a `requires` chain fails, all downstream groups are aborted.
- If a group in an `after` chain fails, dependent groups continue with a warning.
- On shutdown, timeouts produce warnings but don't stop the shutdown sequence.

## Log Paths

| Component | Destination | Notes |
|-----------|-------------|-------|
| **Agent (systemd)** | `journalctl -u dpm-agent` | stdout/stderr captured by journald |
| **Agent (standalone)** | `/var/log/dpm/dpm-agent.log` | Rotating file (10 MB x 5 backups), only when not under systemd |
| **Agent (dev)** | stdout | Always prints to console |
| **GUI** | stderr | Standard Python logging to console |

The agent auto-detects systemd (via `INVOCATION_ID` / `JOURNAL_STREAM` env vars) and
disables file logging when running under journald. Log level is controlled by the
`DPM_LOG_LEVEL` environment variable (default: `INFO`).

## Testing

```bash
# Unit tests (no network required)
pytest

# Integration tests (requires live LCM multicast)
pytest -m integration
```

## Packaging

The project uses `pyproject.toml` (PEP 621) for metadata and packaging.
Install with extras:

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
| **`python3-dpm`** | Python library + all binaries (`dpm-agent`, `dpm`, `dpm-gui`) |
| **`dpm-agent`** | Systemd service, config, RT limits (depends on `python3-dpm`) |
| **`dpm-tools`** | Desktop entry + icon for GUI (depends on `python3-dpm`) |

Install on each host in the cluster:

```bash
# Install agent (auto-installs python3-dpm as dependency)
sudo apt install ./dpm-agent_0.1.0_all.deb

# Install CLI + GUI on the operator workstation
sudo apt install ./dpm-tools_0.1.0_all.deb
```

### What `apt install dpm-agent` does

1. Installs `python3-dpm` (Python code + `/usr/bin/dpm-agent`)
2. Creates system user `dpm` (no home directory, no login shell)
3. Adds `dpm` user to `video`, `render`, `plugdev` groups (GPU + USB camera access)
4. Installs config to `/etc/dpm/dpm.yaml` (preserved on upgrade)
5. Installs systemd unit `dpm-agent.service`
6. Creates `/var/log/dpm/` and `/var/lib/dpm/` owned by `dpm:dpm`
7. Runs `systemctl daemon-reload && systemctl enable --now dpm-agent`

After install, the agent is running:

```bash
systemctl status dpm-agent           # check service status
journalctl -u dpm-agent -f           # follow logs
```

### What `apt remove` / `apt purge` does

- `apt remove dpm-agent` — stops and disables the service, removes the unit file
- `apt purge dpm-agent` — also removes `/etc/dpm/` and `/var/log/dpm/`
- The `dpm` system user is **not** removed (standard Debian policy)

### File locations after install

| Path | Package | Description |
|------|---------|-------------|
| `/usr/bin/dpm-agent` | `python3-dpm` | Agent entry point |
| `/usr/bin/dpm` | `python3-dpm` | CLI entry point |
| `/usr/bin/dpm-gui` | `python3-dpm` | GUI entry point |
| `/usr/lib/python3/dist-packages/dpm/` | `python3-dpm` | Python library |
| `/usr/lib/python3/dist-packages/dpm_msgs/` | `python3-dpm` | LCM message bindings |
| `/etc/dpm/dpm.yaml` | `dpm-agent` | Config (conffile — preserved on upgrade) |
| `/lib/systemd/system/dpm-agent.service` | `dpm-agent` | Systemd unit |
| `/etc/security/limits.d/99-dpm-realtime.conf` | `dpm-agent` | RT scheduling limits |
| `/var/log/dpm/` | created by postinst | Log directory (when not using journald) |
| `/var/lib/dpm/processes.yaml` | created by agent | Persisted process registry (when `persist_processes: true`) |
| `/usr/share/applications/dpm-gui.desktop` | `dpm-tools` | Desktop menu entry |
| `/usr/share/icons/hicolor/256x256/apps/dpm-gui.png` | `dpm-tools` | Application icon |

## Project Structure

```
src/
  dpm/
    agent/          # Agent — runs on each host (dpm-agent)
      agent.py      # Agent class, process state machine, LCM handlers
      cgroups.py    # cgroups v2 management (cpusets, CPU/memory limits)
    supervisor/     # Supervisor — aggregates telemetry, sends commands
      supervisor.py # Supervisor class, thread-safe state, LCM pub/sub
    cli/            # CLI tool (dpm) — scriptable interface
      cli.py        # Entry point, argparse, dispatch
      commands.py   # Command handlers
      formatting.py # Table rendering
      wait.py       # Polling helpers
      launch.py     # Declarative launch system with dependency graph
    gui/            # PyQt5 GUI (dpm-gui)
      main.py       # Application entry point
      main_window.py
      process_dialog.py
      process_output.py
    utils/          # Shared utilities
      config.py     # Config loading
      local_agent.py
    constants.py    # Shared state constants and thresholds
    spec_io.py      # YAML process spec save/load
  dpm_msgs/         # Generated LCM message bindings
```

## License

This project is licensed under the MIT License. See `LICENSE`.

## Author

Matias Bustos SM
