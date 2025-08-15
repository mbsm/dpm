# DPM Master Component

This directory contains the master node implementation for the Distributed Process Manager (DPM) system. The master is responsible for central monitoring and control of processes running on multiple DPM agents across different hosts.

---

## **Table of Contents**
1.  Overview
2.  Components
    *   `controller/controller.py`: Core Controller Logic
    *   `dpm.py`: Terminal User Interface (TUI) — now instantiates `controller.Controller`
3.  Dependencies
4.  Configuration
5.  Usage
6.  TUI Keybindings

---

## **1. Overview**

The DPM Master acts as the central hub in the DPM system. It:
*   Listens for status updates (host info, process info, process output) from DPM agents via LCM.
*   Maintains an internal state representing the status of all known hosts and processes.
*   Provides an interface (`dpm.py`) for users to view the system status and issue commands.
*   Sends commands (create, start, stop, edit processes) to specific agents via LCM.

---

## **2. Components**

### **`controller/controller.py`**: Core Controller Logic

This script defines the `Controller` class, which encapsulates the core functionality of the master node.

*   **Initialization:**
    *   Loads configuration from `dpm.yaml` (specified by `CONFIG_PATH` in `dpm.py`).
    *   Initializes LCM communication using the URL and channels defined in the config.
    *   Subscribes to LCM channels (`host_info_channel`, `host_procs_channel`, `proc_outputs_channel`) to receive messages from nodes.
*   **LCM Handling:**
    *   Runs a background thread (`_thread_func`) to continuously handle incoming LCM messages using `lc.handle_timeout()`.
    *   Message handlers (`host_info_handler`, `host_procs_handler`, `proc_outputs_handler`) decode incoming messages and update the master's internal state (`_hosts`, `_procs`, `_proc_outputs`).
    *   Uses `threading.Lock` to ensure thread-safe access to the shared state dictionaries.
*   **State Management:**
    *   Stores information about hosts, processes, and process outputs in dictionaries.
    *   Provides thread-safe properties (`hosts`, `procs`, `proc_outputs`) for accessing this state (e.g., by the TUI).
*   **Command Publishing:**
    *   Provides methods (`create_proc`, `start_proc`, `stop_proc`, `start_group`, `stop_group`) to create and publish `command_t` messages on the `command_channel` to instruct agents.
*   **Thread Management:**
    *   `start()` and `stop()` methods control the background LCM handling thread.

### **`dpm.py`**: Terminal User Interface (TUI)

This script provides a terminal-based user interface for interacting with the DPM Master. It uses the `curses` library.

*   **Initialization:**
    *   Initializes `curses` for screen drawing.
    *   Creates an instance of `DPM_Master` from `master.py`.
    *   Starts the master's LCM handling thread.
    *   Defines color pairs for UI elements.
*   **UI Layout:**
    *   Divides the screen into two main panels:
        *   **Hosts Panel (Left):** Displays a list of connected hosts, their status (online/offline based on `REPORT_THRESHOLD`), and basic resource usage (CPU, Memory) using simple bar graphs. Drawn by `draw_hosts_panel`.
        *   **Processes Panel (Right):** Displays a table of all known processes across all hosts, including group, name, status (Ready, Running, Failed, Killed), CPU/Memory usage, and auto-restart flag. Drawn by `draw_process_table`. Allows selection using arrow keys.
*   **Interaction:**
    *   Runs a main loop that periodically refreshes the UI panels with data fetched from the `DPM_Master` instance.
    *   Handles keyboard input (`getkey`) for navigation and actions (see TUI Keybindings).
    *   Uses helper functions (`show_create_process_form`, `show_process_dialog`, `show_edit_process_form`) to display interactive forms/dialogs for creating, viewing/controlling, and editing processes. These forms handle user input for process details and call the appropriate `DPM_Master` methods to send commands.
*   **Cleanup:**
    *   Stops the `DPM_Master` thread and restores the terminal state upon exit (`curses.wrapper`).

---

## **3. Dependencies**

*   **Python 3**
*   **`lcm`**: Python bindings for LCM.
*   **`PyYAML`**: For parsing the `dpm.yaml` configuration file.
*   **`curses`**: Standard Python library for TUI (usually included, but ensure development headers like `libncursesw5-dev` are installed on Debian/Ubuntu if building Python or modules).

Install Python dependencies using pip:
```bash
pip install pyyaml lcm
```

---

## **4. Configuration**

The master relies on the main `dpm.yaml` file located one level above the `master` directory (path defined by `CONFIG_PATH` in `dpm.py`). Ensure this file exists and contains the correct LCM channel names and URL.

```python
# master/dpm.py
CONFIG_PATH = "../dpm.yaml"
```

---

## **5. Usage**

To run the DPM Master TUI:

1.  Ensure DPM agents are running on the target hosts.
2.  Ensure the `dpm.yaml` file is correctly configured and accessible at `../dpm.yaml` relative to `dpm.py`.
3.  Navigate to the `master` directory in your terminal:
    ```bash
    cd /path/to/dpm/master
    ```
4.  Run the `dpm.py` script:
    ```bash
    python3 dpm.py
    ```

The terminal will clear and display the DPM interface.

---

## **6. TUI Keybindings**

*   **`q`**: Quit the application.
*   **`n`**: Open the "Create New Process" form.
*   **`Up Arrow`**: Move selection up in the process list.
*   **`Down Arrow`**: Move selection down in the process list.
*   **`Enter`**: Open the dialog for the selected process (allows Start/Stop/Edit).

**In Forms/Dialogs:**

*   **`Tab` / `Shift+Tab`**: Navigate between fields/buttons.
*   **`Enter`**:
    *   Toggle boolean fields (checkboxes).
    *   Activate selected button (OK/Cancel/Update/Start/Stop).
*   **`Esc`**: Cancel the current form/dialog.
*   **`Arrow Keys` (in Dialog)**: Navigate options.
*   **`1`/`2`/`3` (in Dialog)**: Select corresponding option.
*   **Printable Characters (in Text Fields)**: Enter text.
*   **`Backspace`**: Delete character in text fields.