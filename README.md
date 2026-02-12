# DPM (Distributed Process Manager)

DPM is a lightweight distributed process manager for trusted Linux clusters.
It uses LCM multicast for control and telemetry.

## What it does

- Runs an agent per host to manage local processes.
- Provides a controller + GUI for operators.
- Streams host telemetry, process snapshots, and process output.

## Architecture

- Node/Agent: `src/dpm/node/node.py`
- Controller: `src/dpm/controller/controller.py`
- GUI entrypoint: `src/dpm/gui/main.py`
- LCM schemas: `lcm/*.lcm`
- Generated message bindings: `src/dpm_msgs/*.py`

Detailed architecture notes: `docs/architecture.md`

## Quick Start (development)

### 1) Create environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2) Start node

```bash
DPM_CONFIG=./dpm.yaml dpm-node
```

### 3) Start GUI

```bash
DPM_CONFIG=./dpm.yaml dpm-gui
```

## Running without install (repo mode)

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.node.node
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main
```

## Configuration

Default config path: `/etc/dpm/dpm.yaml`.
Override with `DPM_CONFIG`.

Example local config is provided in `dpm.yaml`.

## Process actions

Supported command actions:

- `create_process`
- `start_process`
- `stop_process`
- `delete_process`
- `start_group`
- `stop_group`

## Message flow

- GUI -> Node: `command_t` on `command_channel`
- Node -> GUI:
  - `host_info_t`
  - `host_procs_t`
  - `proc_output_t`

## Install options

`install.sh` now supports component-based installation.

Install both service and GUI (default):

```bash
sudo ./install.sh install
# or
sudo ./install.sh install both
```

Install only node service:

```bash
sudo ./install.sh install service
```

Service target installs only service dependencies (`psutil`, `PyYAML`, `lcm`) and skips GUI packages.

Install only GUI desktop integration:

```bash
sudo ./install.sh install gui
```

GUI target installs only GUI dependencies (`PyQt5` + controller deps).

Uninstall both (default):

```bash
sudo ./install.sh uninstall
# or
sudo ./install.sh uninstall both
```

Uninstall only service or only GUI:

```bash
sudo ./install.sh uninstall service
sudo ./install.sh uninstall gui
```

## Code inspection workflow

Static checks (recommended):

```bash
source .venv/bin/activate
PYTHONPATH=src flake8 src/dpm --max-line-length=120
PYTHONPATH=src pylint src/dpm --disable=import-error
PYTHONPATH=src bandit -r src/dpm -f txt
```

Review playbook and quality gates: `docs/code-inspection.md`

## License

This project is licensed under the MIT License. See `LICENSE`.

## Author

Matias Bustos SM
