# DPM Node

The Node runs on each host. It receives commands over LCM, manages local processes, and publishes host/process status and outputs.

Core files
- Runtime: `node/node.py` (class `NodeAgent`)
- Systemd unit (template): `node/dpm-node.service`
- Installer: `agent/install_dpm_node.sh`
- Config: `dpm.yaml` at repository root

Requirements
- Linux (Debian/Ubuntu tested)
- Python 3.8+
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
- The Node reads `dpm.yaml` from the repository root by default.
- Required fields in `dpm.yaml`:
  - `command_channel`, `host_info_channel`, `proc_outputs_channel`, `host_procs_channel`
  - `stop_timeout`, `monitor_interval`, `output_interval`, `host_status_interval`, `procs_status_interval`
  - `lcm_url`

Running
- Foreground (debug):
```bash
cd /home/mbustos/dpm/node
python3 node.py
```
- As a systemd service (recommended):
```bash
cd /home/mbustos/dpm
sudo ./agent/install_dpm_node.sh install
sudo systemctl start dpm-node.service
sudo journalctl -u dpm-node.service -f
```
- Uninstall:
```bash
sudo ./agent/install_dpm_node.sh uninstall
```

LCM / Networking
- Ensure `lcm_url` is identical across Controller and Node(s).
- For multicast, verify network routing and firewall rules.

Troubleshooting
- Config errors: verify `dpm.yaml` exists and is readable.
- LCM connectivity: verify `lcm_url` and network reachability.
- Realtime scheduling: requires CAP_SYS_NICE or root.

Logs
- When run as a service, Node logs go to journald:
```bash
sudo journalctl -u dpm-node.service -f
```


