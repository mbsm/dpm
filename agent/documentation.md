### Documentation for procman3.py

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

#### **4.1. Running the Script**
To run the script:
```bash
python3 procman3.py
```

#### **4.2. Running as a Daemon**
Uncomment the `daemonize()` call in the `__main__` block to run the script as a background daemon:
```python
if __name__ == "__main__":
    daemonize()
    procman = Procman3()
    procman.run()
```

#### **4.3. Sending Commands**
Commands can be sent to the process manager via the LCM `command_channel`. The supported commands are:
- `create_process`
- `start_process`
- `stop_process`
- `delete_process`

Each command must include the process name, command, and optional parameters (e.g., auto-restart, real-time priority).

---

### **5. Error Handling**

#### **5.1. Configuration Errors**
- Missing or unreadable configuration files raise `FileNotFoundError` or `PermissionError`.
- Invalid YAML syntax raises `ValueError`.

#### **5.2. Process Management Errors**
- Starting a process with an invalid command raises `FileNotFoundError`.
- Setting real-time priority without sufficient permissions logs a `PermissionError`.

#### **5.3. LCM Errors**
- Errors during LCM initialization or message handling raise `RuntimeError`.

#### **5.4. Logging**
All errors and warnings are logged to `./log/procman3.log`.

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
   Check `./log/procman3.log` for process activity and errors.

---