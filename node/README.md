# DPM Node

The Node runs on each host. It receives commands over LCM, manages local processes, and publishes host/process status and outputs for the Controller.

Core files
- Implementation: `node/node.py` (class `NodeAgent`)
- Systemd unit (repo): `node/dpm-node.service`
- Installer: `agent/install_dpm_node.sh`
- Config: `dpm.yaml` at repository root (`/home/mbustos/dpm/dpm.yaml`)

Requirements
- OS: Linux (Debian/Ubuntu tested)
- Python: 3.8+
- System packages:
```bash
sudo apt-get update
sudo apt-get install -y lcm liblcm-dev python3-lcm python3-psutil python3-yaml
```
- Recommended virtualenv:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install psutil pyyaml
```

Configuration
- The Node reads `dpm.yaml` (default runtime path when started from the repo root is `./dpm.yaml`).
- Required fields:
  - `command_channel`, `host_info_channel`, `proc_outputs_channel`, `host_procs_channel`
  - `stop_timeout`, `monitor_interval`, `output_interval`, `host_status_interval`, `procs_status_interval`
  - `lcm_url`
- Example: see `dpm/dpm.yaml.example` or the repo root README.

Running

Development / foreground:
```bash
cd /home/mbustos/dpm/node
python3 node.py
```

As a systemd service (recommended):
1. From the repo root run the interactive installer:
   ```bash
   cd /home/mbustos/dpm
   sudo ./agent/install_dpm_node.sh install
   ```
   The installer updates WorkingDirectory / ExecStart in the installed unit.
2. Start and view logs:
   ```bash
   sudo systemctl start dpm-node.service
   sudo systemctl status dpm-node.service
   sudo journalctl -u dpm-node.service -f
   ```
3. Uninstall:
   ```bash
   sudo ./agent/install_dpm_node.sh uninstall
   ```

LCM / Networking notes
- Ensure `lcm_url` is reachable by Controller and Node(s).
- For multicast, verify network routing and firewall rules.
- Use `lcm-spy` if available to inspect traffic.

Commands the Node handles (sent on `command_channel` by the Controller)
- `create_process`, `start_process`, `stop_process`, `delete_process`, `start_group`, `stop_group`

Troubleshooting
- Config file error: ensure `/home/mbustos/dpm/dpm.yaml` exists and is readable.
- LCM issues: verify `lcm_url` and network reachability.
- Realtime scheduling: setting realtime requires CAP_SYS_NICE or running as root.
- Logs: `sudo journalctl -u dpm-node.service -f` (service) or stderr (foreground).

Development notes
- Main class: `NodeAgent` in `node/node.py`.
- If you move/rename files, update the installer and `node/dpm-node.service` accordingly.


