### Documentation for DPM agent

---

#### **Overview**
procman3.py is a process management system designed to manage and monitor processes on a host system. It uses the Lightweight Communications and Marshalling (LCM) library for inter-process communication and YAML for configuration. The script supports creating, starting, stopping, deleting, and monitoring processes, as well as publishing system and process metrics.

---

### **Table of Contents**
1. Dependencies
2. Configuration
3. Classes and Functions
4. Usage
5. Error Handling

---

### **1. Dependencies**
The script requires the following Python libraries:
- `os`: For file and directory operations.
- `time`: For time-based operations.
- `lcm`: For inter-process communication.
- `psutil`: For process and system monitoring.
- `subprocess.PIPE`: For handling process I/O.
- `logging`: For logging system events.
- `socket`: For network operations.
- `yaml`: For reading YAML configuration files.
- `fcntl`: For setting non-blocking I/O.
- `sys`: For modifying the Python path.

Install missing dependencies using:
```bash
pip install psutil pyyaml lcm
```

---

### **2. Configuration**
The script uses a YAML configuration file (`procman3.yaml`) to define its settings. Below is an example configuration:

```yaml
command_channel: "COMMAND_CHANNEL"
deputy_info_channel: "DEPUTY_INFO_CHANNEL"
proc_outputs_channel: "PROC_OUTPUTS_CHANNEL"
deputy_procs_channel: "DEPUTY_PROCS_CHANNEL"
stop_timeout: 5
monitor_interval: 1
output_interval: 2
deputy_status_interval: 5
procs_status_interval: 5
lcm_url: "udpm://239.255.76.67:7667?ttl=1"
```

**Fields:**
- `command_channel`: LCM channel for receiving commands.
- `deputy_info_channel`: LCM channel for publishing system metrics.
- `proc_outputs_channel`: LCM channel for publishing process outputs.
- `deputy_procs_channel`: LCM channel for publishing process statuses.
- `stop_timeout`: Timeout (in seconds) for stopping processes gracefully.
- `monitor_interval`: Interval (in seconds) for monitoring processes.
- `output_interval`: Interval (in seconds) for publishing process outputs.
- `deputy_status_interval`: Interval (in seconds) for publishing system metrics.
- `procs_status_interval`: Interval (in seconds) for publishing process statuses.
- `lcm_url`: URL for LCM communication.

---

### **3. Classes and Functions**

#### **3.1. `Timer` Class**
A utility class for managing periodic tasks.

- **Methods**:
  - `__init__(timeout)`: Initializes the timer with a timeout period.
  - `timeout()`: Returns `True` if the timer has expired, otherwise `False`.

---

#### **3.2. `Procman3` Class**
The main class for managing processes and publishing system metrics.

- **Methods**:
  - `__init__(config_file="procman3.yaml")`: Initializes the process manager and loads the configuration.
  - `command_handler(channel, data)`: Handles incoming commands via LCM.
  - `create_process(process_name, proc_command, restart_on_failure, realtime, group)`: Creates a new process.
  - `start_process(process_name)`: Starts a process.
  - `stop_process(process_name)`: Stops a process.
  - `delete_process(process_name)`: Deletes a process from the process table.
  - `monitor_process(process_name)`: Monitors a process and restarts it if necessary.
  - `publish_host_info()`: Publishes system metrics (CPU, memory, network, uptime).
  - `publish_host_procs()`: Publishes the status of all managed processes.
  - `publish_procs_outputs()`: Publishes the stdout and stderr of processes.
  - `run()`: Main loop for handling LCM messages and periodic tasks.

---

#### **3.3. Utility Functions**
- `get_ip()`: Retrieves the IP address of the host.
- `set_nonblocking(fd)`: Sets a file descriptor to non-blocking mode.
- `is_running(proc)`: Checks if a process is running.

---

### **4. Usage**

#### **4.1. Running the Script Manually**
To run the agent directly from the command line (useful for testing):
```bash
# Navigate to the agent directory
cd /path/to/dpm/agent
# Run the agent script
python3 agent.py
```
*Note: When run manually, logs will typically go to the console/stderr unless file logging is explicitly re-enabled in `agent.py`.*

#### **4.2. Running as a Systemd Service (Recommended)**

For running the agent automatically on boot and managing it as a system service on Ubuntu 18.04 or similar systems, use the provided `systemd` unit file and installation script.

**Prerequisites:**
*   Ensure `dpm-agent.service` and `install_dpm_agent.sh` are present in the agent directory (`/home/mbustos/dpm/agent/`).
*   Make sure the paths (`WorkingDirectory`, `ExecStart`) inside `dpm-agent.service` are correct for your system.
*   Ensure `install_dpm_agent.sh` has execute permissions (`chmod +x install_dpm_agent.sh`).

**Installation:**
1.  Navigate to the agent directory in your terminal.
2.  Run the installation script with `sudo`:
    ```bash
    sudo ./install_dpm_agent.sh install
    ```
    This will:
    *   Copy `dpm-agent.service` to `/etc/systemd/system/`.
    *   Reload the `systemd` daemon.
    *   Enable the `dpm-agent` service to start automatically on boot.

**Starting/Stopping/Status:**
*   **Start:** `sudo systemctl start dpm-agent.service`
*   **Stop:** `sudo systemctl stop dpm-agent.service`
*   **Restart:** `sudo systemctl restart dpm-agent.service`
*   **Check Status:** `sudo systemctl status dpm-agent.service`

**Uninstallation:**
1.  Navigate to the agent directory in your terminal.
2.  Run the installation script with the `uninstall` argument using `sudo`:
    ```bash
    sudo ./install_dpm_agent.sh uninstall
    ```
    This will:
    *   Stop the service (if running).
    *   Disable the service from starting on boot.
    *   Remove the `dpm-agent.service` file from `/etc/systemd/system/`.
    *   Reload the `systemd` daemon.

#### **4.3. Sending Commands**
Commands are sent to the agent via the LCM `command_channel` defined in `dpm.yaml`. The DPM Master typically handles sending these commands based on user actions in the TUI or predefined configurations. Supported commands include:
- `create_process`
- `start_process`
- `stop_process`
- `delete_process`
- `start_group`
- `stop_group`

---

### **5. Error Handling and Logging**

#### **5.1. Configuration Errors**
- Missing or unreadable configuration files (`dpm.yaml`) will likely cause errors during initialization.
- Invalid YAML syntax will raise errors during parsing.

#### **5.2. Process Management Errors**
- Attempting to manage non-existent processes will log warnings.
- Errors during process start/stop (e.g., permission issues, invalid commands) will be logged.

#### **5.3. LCM Errors**
- Issues with LCM initialization (e.g., network configuration, invalid URL) can prevent the agent from starting or communicating.

#### **5.4. Viewing Logs (Systemd/Journald)**
When running as a `systemd` service using the provided `dpm-agent.service` file, logs are directed to the `systemd` journal. Use `journalctl` to view them:

1.  **View all logs for the service:**
    ```bash
    sudo journalctl -u dpm-agent.service
    ```
    This shows all stored logs for the unit since the journal began or was last rotated.

2.  **Follow logs in real-time (like `tail -f`):**
    ```bash
    sudo journalctl -f -u dpm-agent.service
    ```
    This is useful for watching logs as the agent runs. Press `Ctrl+C` to stop following.

3.  **View logs from the current boot:**
    ```bash
    sudo journalctl -b -u dpm-agent.service
    ```

4.  **View the last N lines:**
    ```bash
    sudo journalctl -n 50 -u dpm-agent.service # Shows the last 50 lines
    ```

5.  **Filter by time:**
    ```bash
    sudo journalctl -u dpm-agent.service --since "yesterday"
    sudo journalctl -u dpm-agent.service --since "2025-04-13 10:00:00" --until "2025-04-13 11:00:00"
    ```

Remember to use `sudo` as accessing the system journal typically requires root privileges.

---

### **6. Example Workflow**

1. **Start the Process Manager**:
   ```bash
   python3 procman3.py
   ```

2. **Send a Command to Create a Process**:
   Publish a message to the `command_channel` with the following fields:
   - `command`: `"create_process"`
   - `name`: `"example_process"`
   - `proc_command`: `"sleep 10"`
   - `auto_restart`: `True`
   - `realtime`: `False`

3. **Monitor Logs**:
   Since you configured the `dpm-agent.service` to use `StandardOutput=journal` and `StandardError=journal`, all the output from your agent's `logging` calls (which now go to stderr) will be captured by the `systemd` journal.

You can view these logs using the `journalctl` command:

1.  **View all logs for the service:**
    ```bash
    sudo journalctl -u dpm-agent.service
    ```
    This shows all stored logs for the unit since the journal began or was last rotated.

2.  **Follow logs in real-time (like `tail -f`):**
    ```bash
    sudo journalctl -f -u dpm-agent.service
    ```
    This is useful for watching logs as the agent runs. Press `Ctrl+C` to stop following.

3.  **View logs from the current boot:**
    ```bash
    sudo journalctl -b -u dpm-agent.service
    ```

4.  **View the last N lines:**
    ```bash
    sudo journalctl -n 50 -u dpm-agent.service # Shows the last 50 lines
    ```

5.  **Filter by time:**
    ```bash
    sudo journalctl -u dpm-agent.service --since "yesterday"
    sudo journalctl -u dpm-agent.service --since "2025-04-13 10:00:00" --until "2025-04-13 11:00:00"
    ```

Remember to use `sudo` as accessing the system journal typically requires root privileges.

---
