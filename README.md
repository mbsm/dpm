# DPM — Distributed Process Manager

DPM is a lightweight distributed process manager that communicates over LCM (Lightweight Communications and Marshalling). It provides a Controller (central coordinator), Node (per-host runtime), and user interfaces (TUI and PyQt5 GUI).

Repository layout
```
/home/mbustos/dpm
├── controller/      # Controller backend (package `controller`)
├── node/            # Node runtime and systemd service template
├── gui/             # PyQt5 GUI package (modules live here)
├── dpm.py           # Terminal UI (TUI) entrypoint (repo root)
├── dpm-gui.py       # Launcher to start the PyQt5 GUI from repo root
├── dpm.yaml         # Global configuration (repo root)
└── README.md        # This file
```

Quick summary
- Controller: orchestrates processes and publishes/consumes LCM channels (package: `controller`, class: `Controller`).
- Node: runs on each host, manages local processes and publishes status (package: `node`, class: `NodeAgent`).
- TUI: `dpm.py` (curses-based terminal UI).
- GUI: `gui/` package; start via `python3 dpm-gui.py` from repo root.

Requirements
- OS: Linux (Debian/Ubuntu tested)
- Python: 3.8+
- LCM runtime + Python bindings:
  - sudo apt-get install -y lcm liblcm-dev python3-lcm
- Python packages:
  - pip install psutil pyyaml
  - PyQt5 only required for the GUI: pip install PyQt5
- Optional (GUI only): sudo apt-get install -y libcanberra-gtk-module libcanberra-gtk3-module

Configuration — dpm.yaml
Place `dpm.yaml` at the repository root. Example values (see `node/dpm.yaml.example` if present):
```yaml
command_channel: "DPM_COMMAND"
host_info_channel: "DPM_HOST_INFO"
proc_outputs_channel: "DPM_PROC_OUTPUTS"
host_procs_channel: "DPM_HOST_PROCS"

stop_timeout: 5
monitor_interval: 1
output_interval: 2
host_status_interval: 2
procs_status_interval: 2

lcm_url: "udpm://239.255.76.67:7667?ttl=1"
```
Notes:
- Channel names must match between Controller and all Nodes.
- Ensure `lcm_url` is reachable by all participants (multicast routing / firewall rules).

Running

Controller (TUI)
- Start from repo root:
```bash
python3 dpm.py
```
TUI features / keybindings:
- Left / Right: switch selected host
- Up / Down: select process within host
- Enter: open process dialog (Start / Stop / Edit / View Output / Delete)
- n: create new process (prompts; host prefilled to selected host)
- s: start selected process
- t: stop selected process
- d: delete selected process
- P: spawn a local Node process for testing (logs written to ./logs/)
- O: stop the last spawned local Node
- q: quit

Node (per-host)
- Development / foreground:
```bash
cd node
python3 node.py
```
- Recommended: install as a systemd service
```bash
# from repo root
sudo ./agent/install_dpm_node.sh install
sudo systemctl start dpm-node.service
sudo journalctl -u dpm-node.service -f
```
- Uninstall:
```bash
sudo ./agent/install_dpm_node.sh uninstall
```

GUI (PyQt5)
- From repo root (recommended):
```bash
python3 dpm-gui.py
```
- Or run GUI entry directly (if you prefer):
```bash
python3 gui/main.py
```
The GUI reads `dpm.yaml` from the repository root by default.

Developer notes (runtime)
- Controller exposes thread-safe properties used by UIs:
  - controller.hosts, controller.procs, controller.proc_outputs
- Controller command helpers used by UIs:
  - create_proc(name, cmd, group, host, auto_restart, realtime)
  - start_proc(name, host)
  - stop_proc(name, host)
  - del_proc(name, host)

Troubleshooting
- Config not found: ensure `dpm.yaml` exists at repo root and is readable.
- LCM connectivity issues: verify `lcm_url`, multicast routing, and firewall rules.
- Realtime scheduling: requesting realtime priority requires CAP_SYS_NICE or root.
- GUI GTK warnings: install libcanberra-gtk-module/libcanberra-gtk3-module (GUI only).

Support
- Open an issue in the repository with details and logs.
