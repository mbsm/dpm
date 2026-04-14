# DPM Production Hardening -- Design Spec

**Date:** 2026-04-13
**Scope:** 6 features to make DPM production-ready for distributed robotics clusters.

---

## Overview

This spec adds six capabilities to DPM:

1. **Circuit breaker** -- Global max restart limit with a SUSPENDED state
2. **Per-process working directory** -- `work_dir` field on the process definition
3. **Cpusets (core isolation)** -- cgroups v2 cpuset controller for RT process isolation
4. **Configurable stop signal** -- Global config constant replacing hardcoded SIGTERM
5. **CPU/memory limits** -- cgroups v2 resource controllers per process
6. **Launch scripts** -- YAML-based orchestration for ordered multi-host startup/shutdown

Features 3 and 5 share cgroup infrastructure. Feature 6 is a separate CLI-level module that does not touch the agent or command_t.

---

## 1. Circuit Breaker (Max Restart Limit)

### Config

New key in `dpm.yaml`:

```yaml
max_restarts: 10    # -1 = unlimited (default, backward-compatible)
```

### New State: SUSPENDED

Add `STATE_SUSPENDED = "S"` to `dpm/constants.py` and `STATE_DISPLAY`:

```python
STATE_SUSPENDED = "S"
STATE_DISPLAY[STATE_SUSPENDED] = "Suspended"
```

### State Machine Changes

```
FAILED -> SUSPENDED    (auto_restart enabled AND restart_count >= max_restarts)
SUSPENDED -> RUNNING   (manual start_process command)
SUSPENDED -> READY     (manual start_process fails)
```

### Agent Behavior

In `monitor_process`, when auto-restart would trigger:

1. Read `max_restarts` from `self.config`.
2. If `max_restarts >= 0` and `restart_count >= max_restarts`:
   - Set state to `STATE_SUSPENDED`.
   - Log a warning: "Process X suspended after N restart attempts."
   - Do NOT restart.
3. Otherwise: existing exponential backoff restart logic.

In `start_process`, when called on a SUSPENDED process:

1. Reset `restart_count` to 0.
2. Clear `last_restart_time`.
3. Proceed with normal start logic (state -> RUNNING or FAILED).

### CLI/GUI Changes

- `format_state("S")` returns "Suspended" (already handled by `STATE_DISPLAY`).
- No new CLI command needed -- `dpm start name@host` clears the suspended state.

### Persistence

No change -- `max_restarts` is a config constant, not per-process. The `restart_count` is runtime-only (not persisted), which is correct: after an agent restart, a fresh counter is appropriate.

---

## 2. Per-Process Working Directory

### LCM Schema

Add to `command_t.lcm`:

```
string work_dir;
```

### Agent Behavior

In `create_process`:

- Store `work_dir` in the process dict.

In `start_process`:

- If `work_dir` is non-empty:
  - Validate the directory exists (`os.path.isdir(work_dir)`).
  - If invalid: set state to FAILED, store error message, return.
  - Pass `cwd=work_dir` to `psutil.Popen()`.
- If empty: omit `cwd` (inherits agent's working directory).

### Supervisor

`create_proc()` accepts `work_dir: str = ""` and forwards it via `_send_command()`.

### CLI

New optional flag on `dpm create`:

```
dpm create name@host --cmd "..." [--work-dir /path/to/dir]
```

### GUI

New `QLineEdit` field "Working Directory" in `ProcessDialog`.

### Spec I/O

New optional field `work_dir` in YAML specs. Default: `""`. Included in `_validate_spec` as an optional string field.

### Persistence

Included in the agent's `_save_registry()` / `_load_registry()` cycle.

---

## 3 & 5. Cgroups v2: Cpusets + CPU/Memory Limits

These share the same cgroup infrastructure and are designed together.

### LCM Schema

Add to `command_t.lcm`:

```
string  cpuset;      // comma-separated core IDs, e.g. "0,1"
double  cpu_limit;   // CPU bandwidth in cores, 0.0 = unlimited
int64_t mem_limit;   // memory limit in bytes, 0 = unlimited
```

### Cgroup Infrastructure (Agent)

New module: `src/dpm/agent/cgroups.py`

Responsible for:

- **Creating** a cgroup directory: `/sys/fs/cgroup/dpm/<process_name>/`
- **Writing** controller files:
  - `cpuset.cpus`: e.g. `"0,1"` (only if `cpuset` is non-empty)
  - `cpu.max`: e.g. `"100000 100000"` for 1.0 cores, `"200000 100000"` for 2.0 cores (only if `cpu_limit > 0`)
  - `memory.max`: e.g. `"1073741824"` for 1GB (only if `mem_limit > 0`)
- **Placing** a PID: write PID to `cgroup.procs`
- **Cleaning up**: remove the cgroup directory on process stop/delete

Public interface:

```python
def setup_cgroup(name: str, pid: int, cpuset: str, cpu_limit: float, mem_limit: int) -> None:
    """Create cgroup, write limits, place PID. Raises on failure."""

def cleanup_cgroup(name: str) -> None:
    """Remove the cgroup directory. Best-effort, logs errors."""

def cgroups_available() -> bool:
    """Return True if cgroups v2 is available and writable."""
```

### Agent Behavior

In `start_process`, after `Popen()` succeeds:

1. If any of `cpuset`, `cpu_limit`, or `mem_limit` are set:
   - Call `setup_cgroup(process_name, pid, cpuset, cpu_limit, mem_limit)`.
   - On failure: log warning, continue without limits (non-fatal, same pattern as RT priority).

In `stop_process` and `delete_process`:

1. Call `cleanup_cgroup(process_name)` in the `finally` block.

### Validation

- `cpuset`: Agent validates core IDs against `os.cpu_count()` at start time. Invalid IDs logged as warning; valid subset applied.
- `cpu_limit`: Must be > 0 if set. Converted to `cpu.max` format: `int(cpu_limit * 100000) 100000`.
- `mem_limit`: Must be > 0 if set. Written directly to `memory.max`.

### Systemd Integration

Add `Delegate=yes` to `debian/dpm-agent.service`:

```ini
[Service]
Delegate=yes
```

This gives the `dpm` user a delegated cgroup subtree where it can create child cgroups.

### Graceful Degradation

If cgroups v2 is not available (dev machines, containers without cgroup access):

- `cgroups_available()` returns False.
- `setup_cgroup()` logs a warning and returns without error.
- All processes run without resource isolation.

### Supervisor

`create_proc()` accepts `cpuset: str = ""`, `cpu_limit: float = 0.0`, `mem_limit: int = 0` and forwards them.

### CLI

New optional flags on `dpm create`:

```
dpm create name@host --cmd "..." [--cpuset 0,1] [--cpu-limit 1.5] [--mem-limit 1073741824]
```

### GUI

New fields in `ProcessDialog`:
- "CPU Set" (`QLineEdit`, placeholder "e.g. 0,1,2")
- "CPU Limit (cores)" (`QLineEdit`, placeholder "e.g. 1.5")
- "Memory Limit (bytes)" (`QLineEdit`, placeholder "e.g. 1073741824")

### Spec I/O

New optional fields: `cpuset`, `cpu_limit`, `mem_limit`. Defaults: `""`, `0.0`, `0`.

### Persistence

All three fields included in `_save_registry()` / `_load_registry()`.

---

## 4. Configurable Stop Signal

### Config

New key in `dpm.yaml`:

```yaml
stop_signal: "SIGINT"    # default; any valid signal name
```

### Agent Behavior

In `__init__`:

- Parse `stop_signal` from config: `getattr(signal, self.config.get("stop_signal", "SIGINT"))`.
- Store as `self.stop_signal` (int).
- Validate it's a real signal; fall back to `signal.SIGINT` if invalid.

In `stop_process`:

- Replace `signal.SIGTERM` with `self.stop_signal` in the first attempt.
- SIGKILL escalation after `stop_timeout` is unchanged.

In `_kill_process_group`:

- No change -- it already takes `sig` as a parameter.

### Config Validation

In `load_config`, validate `stop_signal` if present:

- Must be a string matching a `signal.SIG*` attribute.
- Reject SIGKILL and SIGSTOP (can't be caught, so using them as the "graceful" signal is meaningless).

### No LCM/CLI/GUI Changes

This is agent-side only. No command_t field, no CLI flag, no GUI widget.

---

## 6. Launch Scripts (Orchestration Layer)

### New Module

`src/dpm/cli/launch.py` -- lives entirely at the CLI/supervisor level.

### YAML Format

```yaml
name: "AGV1 Full System"
timeout: 30            # default per-step timeout in seconds (optional)

steps:
  - start: lidar_driver@sensor-host
  - start: camera_driver@sensor-host
  - wait_running:
      targets: [lidar_driver@sensor-host, camera_driver@sensor-host]
      timeout: 15

  - start: slam@perception-host
  - start: detector@perception-host
  - wait_running:
      targets: [slam@perception-host]

  - start: planner@planning-host
  - wait_running:
      targets: [planner@planning-host]
      timeout: 20

  - start: controller@planning-host
```

### Step Types

| Step | Format | Behavior |
|------|--------|----------|
| `start` | `name@host` | Send start command |
| `stop` | `name@host` | Send stop command |
| `create` | `{name, host, cmd, ...}` | Create a process definition |
| `wait_running` | `{targets: [...], timeout: N}` | Block until all targets reach state "R" |
| `wait_stopped` | `{targets: [...], timeout: N}` | Block until all targets leave state "R" |
| `sleep` | `<seconds>` (float) | Fixed delay (hardware settling) |

### Step Execution

Each step:

1. Print the step being executed (e.g., `[3/8] start lidar_driver@sensor-host`).
2. Execute the action using existing supervisor methods.
3. For `wait_*` steps: poll using `wait_for_state` with the step's timeout (or the script-level default).
4. On timeout or error: print which step failed, stop executing, exit with non-zero code.

No automatic rollback -- the operator decides what to do.

### CLI Commands

```
dpm launch <script.yaml>       # execute steps top-to-bottom
dpm shutdown <script.yaml>     # execute in reverse: start->stop, wait_running->wait_stopped
```

`dpm shutdown` transforms each step:
- `start: X` becomes `stop: X`
- `wait_running: [...]` becomes `wait_stopped: [...]`
- `stop: X` becomes `start: X` (inverse)
- `create: {...}` is skipped (don't delete definitions on shutdown)
- `sleep: N` is preserved
- Steps are executed in reverse order.

### Integration with Existing Code

- Reuses `Supervisor.start_proc()`, `stop_proc()`, `create_proc()`.
- Reuses `wait_for_state()`, `wait_for_telemetry()` from `dpm/cli/wait.py`.
- No agent changes. No supervisor changes. No command_t changes.

### CLI Parser

Add `launch` and `shutdown` subcommands to `build_parser()` in `cli.py`:

```
dpm launch <path>
dpm shutdown <path>
```

Add `cmd_launch` and `cmd_shutdown` to `DISPATCH`.

---

## LCM Schema Change Summary

Updated `command_t.lcm`:

```
package dpm_msgs;

struct command_t
{
    int64_t seq;
    string  name;
    string  group;
    string  hostname;
    string  action;
    string  exec_command;
    boolean auto_restart;
    boolean realtime;

    // -- new fields --
    string  work_dir;       // working directory (empty = inherit)
    string  cpuset;         // cgroup cpuset cores, e.g. "0,1"
    double  cpu_limit;      // cgroup CPU bandwidth in cores (0.0 = unlimited)
    int64_t mem_limit;      // cgroup memory limit in bytes (0 = unlimited)
}
```

After editing, regenerate bindings: `./gen-types.sh`

---

## Config Change Summary

New keys in `dpm.yaml` (all optional, backward-compatible defaults):

```yaml
# Maximum auto-restart attempts before suspending (-1 = unlimited)
max_restarts: -1

# Signal sent for graceful stop (SIGKILL escalation unchanged)
stop_signal: "SIGINT"
```

---

## Backward Compatibility

- All new command_t fields have zero-value defaults. Old supervisors sending to new agents: new fields are zero/empty, which means "don't use this feature." No breakage.
- New supervisors sending to old agents: old agents ignore unknown fields in LCM decode (LCM uses fingerprint-based decoding, so the message fingerprint changes -- **old agents will reject messages from new supervisors**). This is a breaking change that requires upgrading agents and supervisors together. This is acceptable for a pre-1.0 project.
- New config keys are optional with sensible defaults. Existing dpm.yaml files work unchanged.

---

## Testing Strategy

### Unit Tests

- **Circuit breaker**: Test that `monitor_process` transitions to SUSPENDED after N restarts. Test that `start_process` clears the counter on a SUSPENDED process.
- **Working directory**: Test that `start_process` passes `cwd=` to Popen. Test validation of non-existent directory.
- **Stop signal**: Test that `stop_process` sends the configured signal instead of SIGTERM.
- **Cgroups**: Test `setup_cgroup` / `cleanup_cgroup` with mocked filesystem writes. Test graceful degradation when cgroups unavailable.
- **Launch scripts**: Test YAML parsing, step execution order, shutdown reversal, timeout handling.

### Integration Tests

- **Cgroups**: Verify cgroup files are created and PID is placed correctly (requires cgroups v2 environment).
- **Launch scripts**: End-to-end test with a multi-step script against live agents.

---

## Implementation Order

1. **Constants + state machine** -- Add SUSPENDED state to constants.py, update STATE_DISPLAY, update formatting tests.
2. **LCM schema** -- Add 4 fields to command_t.lcm, regenerate bindings.
3. **Circuit breaker** -- Config parsing, agent monitor_process logic, start_process reset.
4. **Stop signal** -- Config parsing, agent stop_process change.
5. **Working directory** -- Agent, supervisor, CLI, GUI, spec_io, persistence.
6. **Cgroups module** -- New `cgroups.py` with setup/cleanup/available.
7. **Cpusets + resource limits** -- Agent integration, supervisor, CLI, GUI, spec_io, persistence.
8. **Launch scripts** -- New `launch.py` module, CLI commands, YAML parser.
9. **Tests** -- Unit tests for each feature, update existing tests for new fields.
