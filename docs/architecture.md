# DPM Architecture (Distributed Process Manager)

## 1. Overview

DPM is a distributed process manager intended for **trusted environments** (e.g., a robot’s onboard cluster). It uses **LCM** for message transport.

This project is inspired by `libbot procman` and is developed primarily for learning.

DPM has two primary roles:

- **Daemon**: runs on each Linux host (typically as a `systemd` service via `dpmd.service`). It starts/stops/monitors local processes and reports state/output back to the client.
- **Client + GUI**: runs on an operator machine (or one host in the cluster). It sends commands to daemons and displays host/process state and output.

## 2. Components

### 2.1 Daemon (dpmd)
Responsibilities:
- Maintain a local registry of managed processes (specs + runtime state).
- Execute actions: create, start, stop, delete.
- Monitor process liveness and exit status.
- Periodically publish:
  - host telemetry (cpu/mem)
  - process state
  - process output

Runtime environment:
- Runs as a low-privilege service user.
- Managed by systemd (`dpmd.service`).

### 2.2 Client + GUI
Responsibilities:
- Discover and list active hosts (daemons publishing).
- Show processes per host and their states.
- Send commands to daemons (create/start/stop/delete).
- Show streamed output per process (stdout/stderr).
- Persist/load process specs via YAML.

### 2.3 Shared utilities
- YAML spec IO module used by both GUI and TUI/CLI (if present).

## 3. LCM message model (current)

Current messages (LCM types):
- `command_t`: GUI → Daemon (requests an action)
- `host_info_t`: Daemon → GUI (host telemetry)
- `host_procs_t`: Daemon → GUI (process table snapshot)
- `proc_output_t`: Daemon → GUI (stdout/stderr chunks)
- `proc_info_t`: embedded inside `host_procs_t`

Current behavior:
- GUI infers command success by observing subsequent `host_procs_t` updates and/or output.

## 4. Process State Machine

The `state` field is the single source of truth for process lifecycle. The redundant `status` string field has been removed; display labels are derived from state codes via `STATE_DISPLAY`.

### 4.1 States

| Code | Name      | Meaning |
|------|-----------|---------|
| `T`  | Ready     | Created but not started, or cleanly stopped (exit 0 / graceful SIGTERM) |
| `R`  | Running   | Process is alive |
| `F`  | Failed    | Non-zero exit, or failed to start |
| `K`  | Killed    | Force-killed (SIGKILL after stop timeout) |
| `S`  | Suspended | Auto-restart attempts exhausted (circuit breaker tripped) |

### 4.2 Transitions

```
create_process  → READY
start_process   → RUNNING (success) or FAILED (Popen/exec error)
stop_process    → READY (graceful) or KILLED (SIGKILL escalation)
monitor: exit 0 → READY
monitor: exit≠0 → FAILED → auto_restart (with backoff) → RUNNING
```

### 4.3 Auto-restart with backoff

Processes with `auto_restart=True` are restarted only on FAILED state (non-zero exit). Exponential backoff prevents tight restart loops: 1s, 2s, 4s, 8s, ... capped at 60s. The counter resets on clean exit (code 0). When `max_restarts` is configured and exceeded, the process enters SUSPENDED. A manual `dpm start` clears the counter and resumes normal operation.

### 4.4 Realtime priority

Per-process `realtime` flag uses `SCHED_FIFO` with configurable priority via `rt_priority` in `dpm.yaml` (default: 40). Requires `CAP_SYS_NICE`.

## 5. Resource Isolation and Process Controls

### 5.1 Cgroups v2

Processes can be assigned CPU affinity, CPU bandwidth limits, memory limits, and exclusive core reservation via cgroups v2. The daemon creates per-process cgroup directories under `/sys/fs/cgroup/<daemon-cgroup>/<process_name>/` and cleans them up on stop/delete.

Per-process fields: `cpuset`, `cpu_limit`, `mem_limit`, `isolated`.

Requires: cgroups v2 unified hierarchy and `Delegate=yes` in the systemd service unit. Graceful degradation when cgroups are unavailable.

### 5.2 Realtime priority

Per-process `realtime` flag uses `SCHED_FIFO` with configurable priority via `rt_priority` in `dpm.yaml`. Combine with `--cpuset --isolated` for low-preemption execution.

### 5.3 Declarative launch system

YAML-based dependency graph for multi-host startup/shutdown. Groups start in parallel waves resolved by topological sort. Supports hard dependencies (`requires`) and soft ordering (`after`).

### 5.4 Stale host eviction

The client automatically removes hosts that stop reporting telemetry (3x their `report_interval`), keeping the UI and CLI clean.

### 5.5 Future enhancements
- Health checks (readiness probes, port checks)
- Per-process `rt_priority` override (currently global config)
- File descriptor limits, environment variable allowlist

## 6. Packaging

The project uses `pyproject.toml` (PEP 621) for metadata, dependencies, and entry points. `setup.py` is kept as a thin shim for backward compatibility.

One source package (`dpm`) produces three binary `.deb` packages:

| Package | Purpose | Key files installed |
|---------|---------|---------------------|
| **`python3-dpm`** | Python library + all entry points | `/usr/bin/dpmd`, `/usr/bin/dpm`, `/usr/bin/dpm-gui`, `/usr/lib/python3/dist-packages/dpm/`, `/usr/lib/python3/dist-packages/dpm_msgs/` |
| **`dpmd`** | Daemon systemd service + config | `/lib/systemd/system/dpmd.service`, `/etc/dpm/dpm.yaml`, `/etc/security/limits.d/99-dpm-realtime.conf` |
| **`dpm-tools`** | GUI desktop integration | `/usr/share/applications/dpm-gui.desktop`, `/usr/share/icons/hicolor/256x256/apps/dpm-gui.png` |

Both `dpmd` and `dpm-tools` depend on `python3-dpm`, which is auto-installed by `apt`.

### What happens on install

**`apt install ./dpmd_*.deb`** triggers `dpmd.postinst` which:

1. Creates system user `dpm` (no home, no shell) via `adduser --system`
2. Adds `dpm` to `video`, `render`, `plugdev` groups (GPU and USB camera access)
3. Creates `/var/log/dpm/` owned by `dpm:dpm`
4. Runs `systemctl daemon-reload`
5. Runs `systemctl enable dpmd.service`
6. Runs `systemctl start dpmd.service`

**`apt remove dpmd`** triggers `dpmd.prerm` which stops and disables the service.

**`apt purge dpmd`** triggers `dpmd.postrm` which also removes `/etc/dpm/` and `/var/log/dpm/`.

### Build tooling

- `debian/control` with three binary stanzas (`python3-dpm`, `dpmd`, `dpm-tools`)
- `debian/rules` using `dh` + `pybuild` (dh-python)
- `debian/*.install` manifests for file placement
- `debian/dpmd.postinst` / `.prerm` / `.postrm` — maintainer scripts for service lifecycle
- LCM Python bindings ship pre-generated in `src/dpm_msgs/` (no build-time `lcm-gen` dependency)

### Acceptance criteria

- `apt install dpmd` results in a running service (`systemctl status dpmd`)
- `apt install dpm-tools` provides `dpm` CLI; GUI works if `python3-pyqt5` is installed
- `apt upgrade` preserves `/etc/dpm/dpm.yaml` edits (conffile behavior)
- `apt remove` stops services; `apt purge` removes config and logs

## 7. Security (future enhancement)

DPM’s current threat model is a trusted cluster, but a future enhancement can add message confidentiality/integrity while staying on LCM:

**Secure envelope concept**:
- Publish a single LCM type on one channel (e.g., `DPM/secure`).
- Envelope has an **unencrypted header** (timestamp, sender, nonce, type).
- Envelope payload contains **encrypted inner LCM bytes** (AEAD).

Receiver:
1) checks replay window
2) decrypts with a pre-shared key (or per-node keys in a stricter model)
3) dispatches the inner message to existing handlers

This can be introduced without changing the internal high-level architecture.

## 8. Protocol improvement plan (vNext): “ACK + minimal info in one message”

### 8.1 Goals
- Provide immediate feedback to the GUI after commands.
- Reduce GUI-side inference and “did it happen yet?” ambiguity.
- Keep the payload **minimal** while still allowing correct UI updates.
- Preserve periodic snapshots for eventual consistency.

### 8.2 Proposed additions/changes

#### A) Add command identity to requests
Extend (or replace) command messages to include:
- `command_id` (int64 or UUID string)
- `action` (string/enum: CREATE/START/STOP/DELETE)
- minimal target fields: `hostname`, `proc_name`, `group` (as needed)

This allows:
- idempotency (dedupe by `command_id`)
- clear mapping from user action → node response

#### B) Add a single “proc event” response that includes ACK + minimal state
Introduce a new LCM message (example name: `proc_event_t`) published by daemons, containing:

- `timestamp`
- `hostname`
- `command_id` (echo back; 0 if unsolicited)
- `action` (echo back)
- `ok` (bool)
- `message` (short error or status summary; keep small)
- `proc` (a `proc_info_t` snapshot for the affected process)
- optional: `pid` (int32) and `exit_code` (int32) if relevant

This message acts as:
- an ACK/result for commands, **and**
- the minimal info the GUI needs to update the UI for that specific process immediately.

#### C) Keep periodic snapshots (recommended)
Continue publishing periodic snapshots (e.g., `host_procs_t`, `host_info_t`) at a low rate for:
- recovery after GUI restart
- healing missed messages (LCM/UDP can drop packets)
- baseline synchronization

GUI logic becomes:
- Update UI instantly on `proc_event_t`.
- Periodically reconcile against `host_procs_t` snapshots.

### 8.3 Bandwidth minimization guidelines
- Limit `proc_event_t.message` length (e.g., <= 256 bytes).
- Do **not** embed full stdout/stderr in ACK messages.
- Keep `proc_output_t` separate and rate-limited (or provide on-demand output later).