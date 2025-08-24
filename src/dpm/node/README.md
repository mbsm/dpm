# DPM Node — Host Agent Quick Reference

This document is for node operators. For full project details, see the [project root README](../../README.md).

## What is the Node?
The Node runs on each host. It receives commands over LCM, manages local processes, and publishes host/process status and outputs.

## Core files
- Runtime: `node.py` (class `NodeAgent`)
- Systemd unit (template): `dpm-node.service`
- Installer: `../../scripts/install-dpm-node.sh`
- Config: `dpm.yaml` (copied to `/opt/dpm.yaml` for system installs)

## Requirements
- Linux (Debian/Ubuntu tested)
- Python 3.8+
- System packages: lcm, python3-lcm, python3-psutil, python3-yaml

## Running the Node
- Foreground (debug):
  ```bash
  cd /opt/dpm/node  # or your repo/node path
  python3 node.py
  ```
- As a systemd service (recommended):
  ```bash
  sudo ./scripts/install-dpm-node.sh install
  sudo systemctl start dpm-node.service
  sudo journalctl -u dpm-node.service -f
  ```
- Uninstall:
  ```bash
  sudo ./scripts/install-dpm-node.sh uninstall
  ```

## Configuration
- The Node reads `dpm.yaml` from `/opt/dpm.yaml` (system install) or repo root (dev).
- Required fields: see root README.

## LCM / Networking
- Ensure `lcm_url` is identical across Controller and Node(s).
- For multicast, verify network routing and firewall rules.

## Troubleshooting
- Config errors: verify `/opt/dpm.yaml` exists and is readable.
- LCM connectivity: verify `lcm_url` and network reachability.
- Realtime scheduling: requires CAP_SYS_NICE or root (systemd service sets this up).

## Logs
- When run as a service, Node logs go to journald:
  ```bash
  sudo journalctl -u dpm-node.service -f
  ```
- Foreground: logs written to stdout/stderr.

For more, see the main [README](../../README.md).


