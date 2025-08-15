# DPM GUI

The GUI is a PyQt5-based frontend for DPM. It connects to a local Controller instance (reads configuration from `dpm.yaml` in the repository root).

Quick start
- From repository root:
```bash
python3 dpm-gui.py
```
- Or run the package entrypoint:
```bash
python3 gui/main.py
```

Requirements
- PyQt5: pip install PyQt5
- Other runtime dependencies: psutil, pyyaml (Controller uses these)

Behavior
- The GUI instantiates a `controller.Controller` (config path defaults to repo root `dpm.yaml`) and displays hosts, processes, and process output.
- Actions available: create/edit/delete processes, start/stop processes, view process output.

Notes
- The GUI requires PyQt5 only; Nodes do not require GUI packages.
- If you see GTK warnings on start, install:
  sudo apt-get install -y libcanberra-gtk-module libcanberra-gtk3-module