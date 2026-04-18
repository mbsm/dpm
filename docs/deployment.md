# Deployment & Packaging

## Install extras (from source)

```bash
pip install -e ".[gui]"      # GUI + client
pip install -e ".[dev]"      # Development (pytest)
pip install -e ".[gui,dev]"  # Everything
```

## Debian packages

Build `.deb` packages for production deployment:

```bash
sudo apt install debhelper dh-python python3-all python3-setuptools
dpkg-buildpackage -us -uc -b
```

This produces three packages:

| Package | Purpose |
|---------|---------|
| `python3-dpm` | Python library and all binaries (`dpmd`, `dpm`, `dpm-gui`) |
| `dpmd` | Systemd service, config, RT limits (depends on `python3-dpm`) |
| `dpm-tools` | Desktop entry and icon for GUI (depends on `python3-dpm`) |

## Installing on a cluster

```bash
# On each cluster host
sudo apt install ./dpmd_0.1.0_all.deb

# On the operator workstation
sudo apt install ./dpm-tools_0.1.0_all.deb
```

The `dpmd` package installs the systemd service, creates a `dpm` system user with appropriate group memberships (video, render, plugdev), sets up log and data directories, and enables the service automatically.

```bash
systemctl status dpmd           # check service status
journalctl -u dpmd -f           # follow logs
```

Removal: `apt remove dpmd` stops and disables the service. `apt purge dpmd` also removes config and log files.

## Message protocol

| Direction | Message | Channel | Content |
|-----------|---------|---------|---------|
| Client → Daemon | `command_t` | `command_channel` | Process commands with seq-based UDP dedup |
| Daemon → Client | `host_info_t` | `host_info_channel` | Host telemetry (CPU, memory, network) |
| Daemon → Client | `host_procs_t` | `host_procs_channel` | Process table snapshot |
| Daemon → Client | `proc_output_t` | `proc_outputs_channel` | stdout/stderr chunks |
