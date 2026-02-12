# DPM Architecture (Distributed Process Manager)

## 1. Overview

DPM is a distributed process manager intended for **trusted environments** (e.g., a robot’s onboard cluster). It uses **LCM** for message transport.

This project is inspired by `libbot procman` and is developed primarily for learning.

DPM has two primary roles:

- **Agent (Node)**: runs on each Linux host (typically as a `systemd` service). It starts/stops/monitors local processes and reports state/output back to the controller.
- **Controller (GUI)**: runs on an operator machine (or one host in the cluster). It sends commands to agents and displays host/process state and output.

## 2. Components

### 2.1 Agent (dpm node)
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
- Managed by systemd (`dpm-node.service`).

### 2.2 Controller (GUI)
Responsibilities:
- Discover and list active hosts (agents publishing).
- Show processes per host and their states.
- Send commands to agents (create/start/stop/delete).
- Show streamed output per process (stdout/stderr).
- Persist/load process specs via YAML.

### 2.3 Shared utilities
- YAML spec IO module used by both GUI and TUI/CLI (if present).

## 3. LCM message model (current)

Current messages (LCM types):
- `command_t`: GUI → Agent (requests an action)
- `host_info_t`: Agent → GUI (host telemetry)
- `host_procs_t`: Agent → GUI (process table snapshot)
- `proc_output_t`: Agent → GUI (stdout/stderr chunks)
- `proc_info_t`: embedded inside `host_procs_t`

Current behavior:
- GUI infers command success by observing subsequent `host_procs_t` updates and/or output.

## 4. Node feature roadmap (non-protocol)

### 4.1 Realtime / performance controls (optional)
Potential per-process spec fields:
- `cpu_affinity`: list of cores (pin process)
- `rt_policy`: FIFO/RR
- `rt_priority`: 1..99
- `nice`: -20..19

Implementation options:
- `os.sched_setaffinity(pid, ...)`
- `os.sched_setscheduler(pid, policy, priority)` (requires `CAP_SYS_NICE`)
- cgroups (`cpuset`, `cpu`) for stronger containment (recommended if multiple child processes/forks)

### 4.2 Resource limits
- memory limit, cpu quota (cgroups)
- file descriptor limits
- environment variable allowlist

### 4.3 Health checks and restart policy
- readiness checks (port open / command exit / custom probe)
- restart policy: backoff, max retries, cooldown timers
- dependency ordering by group

## 5. Packaging plan: move from install.sh to .deb packages

### 5.1 Target packages
Produce Debian packages managed by `apt`:

- **`dpm-agent`** (or `dpm-node`):
  - installs the agent runtime
  - installs default config to `/etc/dpm/dpm.yaml` (conffile)
  - installs systemd unit: `dpm-node.service`
  - creates service user (if needed)
  - enables + starts the service (postinst)

- **`dpm-controller`** (or renamed GUI package, e.g. `dpm-gui`):
  - installs GUI application + desktop integration 
  - depends on PyQt5 and runtime deps
  

Recommended approach: **one source package** (`dpm`) producing **multiple binary packages** (`dpm-agent`, `dpm-controller`).

### 5.2 Debian packaging approach (recommended)
Use standard Debian tooling:
- `debian/control` with two binary stanzas
- `debian/rules` using `dh` + `pybuild` (dh-python)
- `debian/*.install` to place files into:
  - python package: `/usr/lib/python3/dist-packages/`
  - scripts: `/usr/bin/`
  - config: `/etc/dpm/dpm.yaml`
  - systemd: `/lib/systemd/system/dpm-node.service`

Service management:
- `postinst`: create user, `systemctl daemon-reload`, enable/start
- `prerm/postrm`: stop/disable on removal (policy-dependent)

Generated LCM Python bindings:
- Option A (recommended): ship generated `src/dpm_msgs/*.py` as part of the package (no build-time LCM dependency).
- Option B: generate at build time (requires `lcm-gen` in Build-Depends).

### 5.3 Migration from install.sh
- Keep `install.sh` only for dev/testing or remove it once `.deb` packaging is stable.
- Encode existing install behavior into:
  - Debian conffiles
  - `postinst` scripts
  - systemd unit

### 5.4 Acceptance criteria
- `apt install dpm-agent` results in a running service (`systemctl status dpm-node`).
- `apt install dpm-controller` provides a runnable GUI entry point.
- `apt upgrade` preserves `/etc/dpm/dpm.yaml` changes (conffile behavior).
- `apt remove` stops services; `apt purge` removes config (if desired).

## 6. Security (future enhancement)

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

## 7. Protocol improvement plan (vNext): “ACK + minimal info in one message”

### 7.1 Goals
- Provide immediate feedback to the GUI after commands.
- Reduce GUI-side inference and “did it happen yet?” ambiguity.
- Keep the payload **minimal** while still allowing correct UI updates.
- Preserve periodic snapshots for eventual consistency.

### 7.2 Proposed additions/changes

#### A) Add command identity to requests
Extend (or replace) command messages to include:
- `command_id` (int64 or UUID string)
- `action` (string/enum: CREATE/START/STOP/DELETE)
- minimal target fields: `hostname`, `proc_name`, `group` (as needed)

This allows:
- idempotency (dedupe by `command_id`)
- clear mapping from user action → node response

#### B) Add a single “proc event” response that includes ACK + minimal state
Introduce a new LCM message (example name: `proc_event_t`) published by agents, containing:

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

### 7.3 Bandwidth minimization guidelines
- Limit `proc_event_t.message` length (e.g., <= 256 bytes).
- Do **not** embed full stdout/stderr in ACK messages.
- Keep `proc_output_t` separate and rate-limited (or provide on-demand output later).