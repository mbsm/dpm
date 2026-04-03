# Copilot instructions for DPM (Distributed Process Manager)

## Big picture
- DPM uses **LCM multicast** to coordinate processes in a trusted cluster.
- **Node/Agent** (per host): [src/dpm/node/node.py](../src/dpm/node/node.py)
- **Controller + GUI** (operator): [src/dpm/controller/controller.py](../src/dpm/controller/controller.py) and [src/dpm/gui/main.py](../src/dpm/gui/main.py)
- Message flow:
  - GUI → Node: `command_t` on `command_channel` (actions are strings)
  - Node → GUI: `host_info_t` (telemetry), `host_procs_t` (process snapshot), `proc_output_t` (stdout/stderr chunks)

## Developer workflows
- Dev run (recommended):
  - `python3 -m venv .venv && . .venv/bin/activate && pip install -e .`
  - `DPM_CONFIG=./dpm.yaml dpm-node` and `DPM_CONFIG=./dpm.yaml dpm-gui`
- Repo run (no install):
  - `PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.node.node`
  - `PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main`

## Config, messages, regen
- Config default is `/etc/dpm/dpm.yaml` (override via `DPM_CONFIG`); channels + `lcm_url` are in [dpm.yaml](../dpm.yaml).
- LCM schemas are in `lcm/*.lcm`; generated bindings are in `src/dpm_msgs/*.py` (do not hand-edit).
- After editing `lcm/*.lcm`, run `./gen-types.sh` (needs `lcm-gen` on PATH).

## Process model (node-side)
- `command_t.action`: `create_process`, `start_process`, `stop_process`, `delete_process`, `start_group`, `stop_group`.
- `exec_command` is parsed with `shlex.split()`; write commands that survive splitting/quoting.
- Processes start with `start_new_session=True` and are stopped by signaling the **process group** (see `_kill_process_group()` in [src/dpm/node/node.py](../src/dpm/node/node.py)).
- State codes in `proc_info_t.state` are single letters (node defines `T/R/F/K`).

## Threading/UI rules
- Controller uses two LCM instances: `lc_sub` in a background thread, `lc_pub` for GUI-thread publishing.
- GUI should read `controller.hosts`/`controller.procs` (snapshot copies) and use `get_proc_output_delta()` for output streaming.

## Packaging notes / gotchas
- `./install.sh install` is the most complete installer (sets up `/opt/dpm` + venv + systemd unit).
- [packaging/systemd/dpm-node.service](../packaging/systemd/dpm-node.service) looks older/different; keep units in sync if changing service behavior.
- GUI menu “Spawn Local Node” references `dpm.utils.local_node`, but `src/dpm/utils/` is not present here; run `dpm-node` manually.
