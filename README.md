# DPM — Distributed Process Manager

DPM is a lightweight distributed process manager that communicates over LCM (Lightweight Communications and Marshalling). It consists of:
- Master: Orchestrates and monitors processes across hosts via LCM.
- Agent: Runs on each host, spawns/monitors processes, and publishes host/process metrics.
- UI: A curses TUI and a PyQt5 GUI to view state and control processes.

Core classes/files:
- [`master.DPM_Master`](master/master.py)
- [`agent.NodeAgent`](agent/agent.py)
- TUI: [dpm.py](dpm.py)
- PyQt5 GUI entrypoint: [dpm-pyqt5-gui/src/main.py](dpm-pyqt5-gui/src/main.py)

## Project layout
```
dpm/
├── agent/                  # Host-side agent and systemd integration
├── master/                 # Master (backend, TUI)
├── dpm-pyqt5-gui/          # PyQt5 GUI
├── dpm.yaml                # Global configuration (see below)
└── README.md               # This file
```

## Requirements
- Linux (tested on Ubuntu)
- Python 3.8+
- LCM runtime and Python bindings
  - apt: sudo apt-get install lcm liblcm-dev python3-lcm
- Python packages
  - psutil, PyYAML (yaml), PyQt5 (for GUI)
  - pip: pip install psutil pyyaml PyQt5
- Optional (GUI warning fix):
  - sudo apt-get install libcanberra-gtk-module libcanberra-gtk3-module

## Configuration: dpm.yaml
Both Master and Agent read the same YAML. Place at repo root: /home/mbustos/dpm/dpm.yaml

Example:
```yaml
# dpm.yaml
command_channel: "DPM_COMMAND"
host_info_channel: "DPM_HOST_INFO"
proc_outputs_channel: "DPM_PROC_OUTPUTS"
host_procs_channel: "DPM_HOST_PROCS"

stop_timeout: 5
monitor_interval: 1
output_interval: 2
host_status_interval: 2
procs_status_interval: 2

# LCM multicast example URL (adjust for your network)
lcm_url: "udpm://239.255.76.67:7667?ttl=1"
```

Notes:
- Channels must match across Master and Agents.
- lcm_url should be reachable by all participants (consider multicast routing/firewall).

## Components

### Master
- Class: [`master.DPM_Master`](master/master.py)
- Responsibilities:
  - Subscribes to host/process/output channels.
  - Publishes commands to control processes and groups.
- Public API (LCM commands):
  - create_proc(name, cmd, group, host, auto_restart=False, realtime=False)
  - start_proc(name, host)
  - stop_proc(name, host)
  - del_proc(name, host)
  - start_group(group, host)
  - stop_group(group, host)
- Thread-safe views:
  - .hosts, .procs, .proc_outputs

The curses TUI lives in [dpm.py](dpm.py). Some UI functions (e.g., output viewer) are placeholders.

### Agent
- Class: [`agent.NodeAgent`](agent/agent.py)
- Responsibilities:
  - Receives commands (create/start/stop/delete/start_group/stop_group).
  - Spawns and monitors processes.
  - Publishes host and process metrics and process outputs.
- Service integration:
  - Unit file: [agent/dpm-agent.service](agent/dpm-agent.service)
  - Installer: [agent/install_dpm_agent.sh](agent/install_dpm_agent.sh)

### PyQt5 GUI
- Entry: [dpm-pyqt5-gui/src/main.py](dpm-pyqt5-gui/src/main.py)
- Starts [`master.DPM_Master`](master/master.py) and opens the GUI MainWindow (see gui/main_window.py).
- Ensure the config path in main.py points to your dpm.yaml.

## Setup

1) Install system deps
```bash
sudo apt-get update
sudo apt-get install -y lcm liblcm-dev python3-lcm python3-psutil python3-yaml
# Optional (silence GTK warnings for PyQt5 apps)
sudo apt-get install -y libcanberra-gtk-module libcanberra-gtk3-module
```

2) (Optional) Use a virtualenv
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install psutil pyyaml PyQt5
```

3) Create/verify dpm.yaml at /home/mbustos/dpm/dpm.yaml (see example above).

## Running

- Agent (foreground):
```bash
cd agent
python3 agent.py
```

- Agent as a service:
  - Edit WorkingDirectory and ExecStart in [agent/dpm-agent.service](agent/dpm-agent.service) to match your path.
  - Install:
    ```bash
    cd agent
    sudo chmod +x install_dpm_agent.sh
    sudo ./install_dpm_agent.sh install
    sudo systemctl start dpm-agent.service
    sudo systemctl status dpm-agent.service
    ```

- PyQt5 GUI:
```bash
python3 dpm-pyqt5-gui/src/main.py
```
Make sure [dpm-pyqt5-gui/src/main.py](dpm-pyqt5-gui/src/main.py) uses the correct config path (e.g., /home/mbustos/dpm/dpm.yaml).

- Curses TUI:
  - The TUI exists in [dpm.py](dpm.py); some UI functions are placeholders and may need completion before use.

## Troubleshooting

- GTK module warning on GUI start:
  - Gtk-Message: Failed to load module "canberra-gtk-module"
  - Fix:
    ```bash
    sudo apt-get install libcanberra-gtk-module libcanberra-gtk3-module
    ```

- FileNotFoundError: Configuration file not found:
  - Ensure the path you pass to [`master.DPM_Master`](master/master.py) points to an existing, readable dpm.yaml.
  - In GUI, update main.py to: DPM_Master("/home/mbustos/dpm/dpm.yaml")

- No LCM traffic:
  - Verify multicast route/firewall and that lcm_url is the same for Master and Agent.
  - Test with lcm-spy if available.

## Development
- Code style: Python 3, standard library + psutil, PyYAML, PyQt5 where applicable.
- UI code under dpm-pyqt5-gui/gui (MainWindow, dialogs) should consume [`master.DPM_Master`](master/master.py) via its properties and methods.
