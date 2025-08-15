# DPM Agent

The DPM Agent runs on each host, receives commands over LCM, manages local processes, and publishes host/process status and outputs.

Core files:
- Agent: [`agent.NodeAgent`](agent/agent.py)
- Systemd unit: [agent/dpm-agent.service](agent/dpm-agent.service)
- Installer: [agent/install_dpm_agent.sh](agent/install_dpm_agent.sh)

## Dependencies
- System: lcm, python3-lcm
- Python: psutil, PyYAML
- Install (Ubuntu):
```bash
sudo apt-get install -y lcm liblcm-dev python3-lcm python3-psutil python3-yaml
```

## Configuration
The agent reads dpm.yaml (default relative path: ../dpm.yaml). Ensure it exists at repo root (/home/mbustos/dpm/dpm.yaml).

Required fields:
- command_channel, host_info_channel, proc_outputs_channel, host_procs_channel
- stop_timeout, monitor_interval, output_interval, host_status_interval, procs_status_interval
- lcm_url

See the example in the root [README](../README.md).

## Running the Agent
- Foreground:
```bash
cd /home/mbustos/dpm/agent
python3 agent.py
```

- As a systemd service:
  - Edit [agent/dpm-agent.service](agent/dpm-agent.service) WorkingDirectory and ExecStart to point to your actual paths (replace /home/agv1 with /home/mbustos).
  - Install and enable:
    ```bash
    sudo ./install_dpm_agent.sh install
    sudo systemctl start dpm-agent.service
    sudo systemctl status dpm-agent.service
    ```
  - Uninstall:
    ```bash
    sudo ./install_dpm_agent.sh uninstall
    ```

Logs (when run as a service) go to journald:
```bash
sudo journalctl -u dpm-agent.service -f
```

## Commands handled
Sent on command_channel by the Master:
- create_process
- start_process
- stop_process
- delete_process
- start_group
- stop_group

## Notes
- Realtime priority requires elevated privileges (CAP_SYS_NICE) or running as root.
- The agent publishes:
  - Host metrics on host_info_channel
  - Process table on host_procs_channel
  - Process outputs on proc_outputs_channel
