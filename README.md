# DPM — Distributed Process Manager

DPM is a lightweight distributed process manager that communicates over LCM (Lightweight Communications and Marshalling). It provides a Controller (central coordinator), Node (per-host runtime), and user interfaces (TUI and PyQt5 GUI).

Quick summary
- Controller: orchestrates processes and publishes/consumes LCM channels (package: `controller`, class: `Controller`).
- Node: runs on each host, manages local processes and publishes status (package: `node`, class: `NodeAgent`).
- UI:
  - TUI: `dpm.py` (terminal interface at repo root)
  - GUI: PyQt5 GUI under `gui/` with launcher `dpm-gui.py` at repo root

Repository layout
```
dpm/
├── controller/             # Controller backend
├── node/                   # Node runtime and service
├── gui/          # PyQt5 GUI
├── dpm.py                  # Terminal UI (TUI) entrypoint (repo root)
├── dpm-gui.py              # Launcher to start the PyQt5 GUI from repo root
├── dpm.yaml                # Global configuration (repo root)
└── README.md               # This file
```

Requirements
- OS: Linux (Debian/Ubuntu tested)
- Python: 3.8+
- LCM runtime and Python bindings:
  - sudo apt-get install -y lcm liblcm-dev python3-lcm
- Python packages:
  - pip install psutil pyyaml
  - PyQt5 only required for the GUI: pip install PyQt5
- Optional (silence GTK warnings for GUI):
  - sudo apt-get install -y libcanberra-gtk-module libcanberra-gtk3-module

Configuration — dpm.yaml
Place `dpm.yaml` at the repository root. Example (`dpm/dpm.yaml.example`):

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

Notes
- Channel names must match between Controller and all Nodes.
- Ensure `lcm_url` is reachable by all participants (multicast routing and firewall rules may apply).

Running

Controller (TUI)
- From repo root:
```bash
python3 dpm.py
```
The TUI reads `dpm.yaml` from the repository root by default.

Node (per-host)
- Run in foreground (development/debug):
```bash
cd node
python3 node.py
```
- Install as a systemd service (recommended for production):
```bash
# from repo root
sudo ./agent/install_dpm_node.sh install
sudo systemctl start dpm-node.service
sudo journalctl -u dpm-node.service -f
```
- To uninstall:
```bash
sudo ./agent/install_dpm_node.sh uninstall
```

GUI (PyQt5)
- Start GUI from repo root:
```bash
python3 dpm-gui.py
```
- Or run directly:
```bash
python3 gui/src/main.py
```
PyQt5 is required only for the GUI. The GUI reads `dpm.yaml` from the repo root by default.

LCM / Networking
- Verify `lcm_url` and network reachability for Controller and Nodes.
- For multicast, ensure network and firewall allow the multicast group and port.
- Use `lcm-spy` (if available) to inspect LCM traffic for debugging.

Commands handled (sent on the configured command channel)
- create_process
- start_process
- stop_process
- delete_process
- start_group
- stop_group

Troubleshooting
- Configuration file not found: ensure `dpm.yaml` exists at the repository root and is readable.
- LCM connectivity issues: verify `lcm_url`, network routing, and firewall rules.
- Realtime scheduling: requesting realtime priority requires CAP_SYS_NICE or root.
- GTK warning on GUI start: install `libcanberra-gtk-module` and `libcanberra-gtk3-module` to silence messages.

Support
- For issues or questions, open an issue
