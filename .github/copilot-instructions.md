# Copilot instructions for DPM (Distributed Process Manager)

## Big picture
- DPM uses **LCM multicast** to coordinate processes in a trusted cluster.
- **Agent** (per host): [src/dpm/agent/agent.py](../src/dpm/agent/agent.py)
- **Supervisor** (library): [src/dpm/supervisor/supervisor.py](../src/dpm/supervisor/supervisor.py)
- **GUI** (operator UI): [src/dpm/gui/main.py](../src/dpm/gui/main.py)
- Message flow:
  - Supervisor → Agent: `command_t` on `command_channel` (actions are strings)
  - Agent → Supervisor: `host_info_t` (telemetry), `host_procs_t` (process snapshot), `proc_output_t` (stdout/stderr chunks)

## Developer workflows
- Dev run (recommended):
  - `python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[gui,dev]"`
  - `DPM_CONFIG=./dpm.yaml dpm-agent` and `DPM_CONFIG=./dpm.yaml dpm-gui`
- Repo run (no install):
  - `PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.agent.agent`
  - `PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main`

## Config, messages, regen
- Config default is `/etc/dpm/dpm.yaml` (override via `DPM_CONFIG`); channels + `lcm_url` are in [dpm.yaml](../dpm.yaml).
- LCM schemas are in `lcm/*.lcm`; generated bindings are in `src/dpm_msgs/*.py` (do not hand-edit).
- After editing `lcm/*.lcm`, run `./gen-types.sh` (needs `lcm-gen` on PATH).

## Process state machine (agent-side)
- States: READY (`T`), RUNNING (`R`), FAILED (`F`), KILLED (`K`).
- `state` is the single source of truth; display labels derived from `STATE_DISPLAY` dict.
- `command_t.action`: `create_process`, `start_process`, `stop_process`, `delete_process`, `start_group`, `stop_group`.
- `exec_command` is parsed with `shlex.split()`; write commands that survive splitting/quoting.
- Processes start with `start_new_session=True` and are stopped by signaling the **process group**.
- Auto-restart triggers only on FAILED state with exponential backoff.

## Threading/UI rules
- Supervisor uses two LCM instances: `lc_sub` in a background thread, `lc_pub` for GUI-thread publishing.
- GUI should read `supervisor.hosts`/`supervisor.procs` (snapshot copies) and use `get_proc_output_delta()` for output streaming.
- The Supervisor is UI-agnostic; a TUI, CLI, or web frontend could use it directly.

## Packaging
- `pyproject.toml` is the source of truth for metadata, dependencies, and entry points.
- `debian/` directory provides `.deb` packaging (two binary packages: `dpm-agent`, `dpm-supervisor`).
- `debian/` directory provides `.deb` packaging for production deployment.
