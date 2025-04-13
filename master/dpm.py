import curses
import time
import os
import sys

sys.path.append(os.path.dirname(__file__))
from master import DPM_Master

REPORT_THRESHOLD = 5  # number of seconds after which host is considered offline
CONFIG_PATH = "../dpm.yaml"  # update this to your config path

def draw_hosts_panel(win, hosts):
    """
    Draw all hosts in a single panel with CPU/Memory graphs
    """
    win.erase()
    win.box()
    
    # Draw title
    win.attron(curses.A_BOLD)
    win.addstr(1, 2, "HOSTS")
    win.attroff(curses.A_BOLD)
    
    # Draw horizontal line
    win.hline(2, 1, curses.ACS_HLINE, win.getmaxyx()[1]-2)
    
    current_time = time.time()
    y_pos = 3
    
    # Header for hosts list
    win.addstr(y_pos, 2, "Hostname")
    win.addstr(y_pos, 20+5, "CPU")
    win.addstr(y_pos, 34+5, "MEM")
    y_pos += 1
    
    # Horizontal line below header
    win.hline(y_pos, 1, curses.ACS_HLINE, win.getmaxyx()[1]-2)
    y_pos += 1
    
    for hostname, host_info in hosts.items():
        # Check if host is offline
        offline = (current_time - host_info.timestamp*1e-6) > REPORT_THRESHOLD
        
        # Reset values to 0 when offline
        cpu = 0 if offline else host_info.cpu_usage
        mem = 0 if offline else host_info.mem_used / host_info.mem_total
        
        # Display hostname (red if offline)
        if offline:
            win.attron(curses.color_pair(2))  # Red color
            win.addstr(y_pos, 2, f"{hostname[:16]}")
            win.attroff(curses.color_pair(2))
        else:
            win.addstr(y_pos, 2, f"{hostname[:16]}")
        
        # Draw CPU usage graph - 10 chars width
        cpu_bar_width = 10
        cpu_filled = int(cpu * cpu_bar_width)
        
        # Ensure cpu_filled is within bounds
        cpu_filled = max(0, min(cpu_filled, cpu_bar_width))
        
        # Start of CPU bar
        win.addstr(y_pos, 20, "[")
        
        # Calculate percentage for display
        cpu_percent = f"{cpu*100:3.1f}%"
        
        # Position percentage at right side (before closing bracket)
        percent_pos = 21 + cpu_bar_width - len(cpu_percent)
        
        # Draw the filled bar up to the percentage position
        if offline:
            win.attron(curses.color_pair(2))  # Red for offline
        else:
            win.attron(curses.color_pair(4))  # Green for online
            
        for i in range(min(percent_pos - 21, cpu_filled)):
            win.addch(y_pos, 21 + i, curses.ACS_BLOCK)
            
        if offline:
            win.attroff(curses.color_pair(2))
        else:
            win.attroff(curses.color_pair(4))
        
        # Draw percentage in white, aligned to the right
        win.attron(curses.color_pair(1))  # White color
        win.addstr(y_pos, percent_pos, cpu_percent)
        win.attroff(curses.color_pair(1))
        
        # End of CPU bar
        win.addstr(y_pos, 21 + cpu_bar_width, "]")
        
        # Draw Memory usage graph - 10 chars width
        mem_bar_width = 10
        mem_filled = int(mem * mem_bar_width)
        
        # Ensure mem_filled is within bounds
        mem_filled = max(0, min(mem_filled, mem_bar_width))
        
        # Start of MEM bar
        win.addstr(y_pos, 34, "[")
        
        # Calculate percentage for display
        mem_percent = f"{mem*100:3.1f}%"
        
        # Position percentage at right side (before closing bracket)
        percent_pos = 35 + mem_bar_width - len(mem_percent)
        
        # Draw the filled bar up to the percentage position
        if offline:
            win.attron(curses.color_pair(2))  # Red for offline
        else:
            win.attron(curses.color_pair(4))  # Green for online
            
        for i in range(min(percent_pos - 35, mem_filled)):
            win.addch(y_pos, 35 + i, curses.ACS_BLOCK)
            
        if offline:
            win.attroff(curses.color_pair(2))
        else:
            win.attroff(curses.color_pair(4))
        
        # Draw percentage in white, aligned to the right
        win.attron(curses.color_pair(1))  # White color
        win.addstr(y_pos, percent_pos, mem_percent)
        win.attroff(curses.color_pair(1))
        
        # End of MEM bar
        win.addstr(y_pos, 35 + mem_bar_width, "]")
        
        y_pos += 1
    
    win.refresh()

def draw_process_table(win, procs, selected_idx=0):
    """
    Draw a table of processes from DPM_Master
    """
    win.erase()
    win.box()
    
    # Table header
    win.attron(curses.A_BOLD)
    win.addstr(1, 2, "PROCESSES")
    win.attroff(curses.A_BOLD)
    
    # Draw horizontal line
    win.hline(2, 1, curses.ACS_HLINE, win.getmaxyx()[1]-2)
    
    # Column headers
    win.attron(curses.A_BOLD)
    win.addstr(3, 2, "Group")
    win.addstr(3, 18, "Name")
    win.addstr(3, 38, "Status")
    win.addstr(3, 48, "CPU")
    win.addstr(3, 58, "MEM")
    win.addstr(3, 68, "Auto")  # Added Auto column for auto_restart
    win.attroff(curses.A_BOLD)
    
    # Draw horizontal line
    win.hline(4, 1, curses.ACS_HLINE, win.getmaxyx()[1]-2)
    
    # Process list
    proc_list = list(procs.values())
    max_rows = win.getmaxyx()[0] - 6  # Leave room for header and borders
    
    start_idx = max(0, min(selected_idx - max_rows + 1, len(proc_list) - max_rows)) if len(proc_list) > max_rows else 0
    end_idx = min(start_idx + max_rows, len(proc_list))
    
    for i, proc in enumerate(proc_list[start_idx:end_idx]):
        row = i + 5  # Start after header
            
        # STATE_READY = "T" STATE_RUNNING = "R" STATE_FAILED = "F" STATE_KILLED = "K"
        state_names = {
            "T": "Ready",
            "R": "Running",
            "F": "Failed",
            "K": "Killed"
        }
        status = state_names.get(proc.state, "Unknown")
        
        # Highlight selected process with gray (dim) background
        if i + start_idx == selected_idx:
            win.attron(curses.A_DIM)  # Use dim attribute for gray effect
            
        # Draw group and name for all processes
        win.addstr(row, 2, f"{proc.group[:12]}")
        win.addstr(row, 18, f"{proc.name[:18]}")
        
        # Draw status with appropriate color
        if proc.state == "R":  # Running
            win.attron(curses.color_pair(4))  # Green
            win.addstr(row, 38, status)
            win.attroff(curses.color_pair(4))
        elif proc.state == "F" or proc.state == "K":  # Failed or Killed
            win.attron(curses.color_pair(2))  # Red
            win.addstr(row, 38, status)
            win.attroff(curses.color_pair(2))
        else:  # Ready or any other state
            win.addstr(row, 38, status)  # Default white text
            
        # Draw CPU and Memory columns
        if proc.state == "R":  # Running
            win.attron(curses.color_pair(4))  # Green
            win.addstr(row, 48, f"{proc.cpu*100:.1f}%")
            win.addstr(row, 58, f"{proc.mem_rss:.1f}MB")
            win.attroff(curses.color_pair(4))
        else:
            win.addstr(row, 48, f"{proc.cpu*100:.1f}%")
            win.addstr(row, 58, f"{proc.mem_rss:.1f}MB")
            
        # Draw auto-restart flag
        win.addstr(row, 68, "Yes" if proc.auto_restart else "No")
        
        # Turn off gray highlight for selected row
        if i + start_idx == selected_idx:
            win.attroff(curses.A_DIM)
    
    win.refresh()

def show_create_process_form(stdscr, master):
    """
    Display a form for creating a new process
    """
    # Save current cursor visibility and hide cursor during form setup
    old_cursor = curses.curs_set(0)
    
    # Get screen dimensions
    screen_height, screen_width = stdscr.getmaxyx()
    
    # Create form window (centered) - increased width for longer commands
    form_height, form_width = 16, 80
    form_y = (screen_height - form_height) // 2
    form_x = (screen_width - form_width) // 2
    
    form_win = curses.newwin(form_height, form_width, form_y, form_x)
    form_win.keypad(True)  # Enable keypad for input
    
    # Form fields - increased width for command
    fields = [
        {"name": "Process Name", "value": "", "type": "text", "y": 2, "x": 3, "width": 40},
        {"name": "Process Command", "value": "", "type": "text", "y": 3, "x": 3, "width": 70}, # Much wider command field
        {"name": "Group", "value": "", "type": "text", "y": 4, "x": 3, "width": 30},
        {"name": "Host", "value": "", "type": "text", "y": 5, "x": 3, "width": 30},
        {"name": "Auto Restart", "value": False, "type": "bool", "y": 6, "x": 3},
        {"name": "Realtime", "value": False, "type": "bool", "y": 7, "x": 3},
        {"name": "OK", "value": "", "type": "button", "y": 10, "x": 30},
        {"name": "Cancel", "value": "", "type": "button", "y": 10, "x": 45}
    ]
    
    current_field = 0
    result = None
    
    # Get available hosts for dropdown
    hosts = list(master.hosts.keys())
    
    # Form input loop
    while True:
        form_win.clear()
        form_win.box()
        
        # Draw form title
        form_win.attron(curses.A_BOLD)
        form_win.addstr(0, 2, "Create New Process")
        form_win.attroff(curses.A_BOLD)
        
        # Draw fields
        for i, field in enumerate(fields):
            # Draw field label
            if field["type"] != "button":
                form_win.addstr(field["y"], field["x"], f"{field['name']}: ")
                
                # Draw field value
                if field["type"] == "text":
                    # If this is the current field, show cursor
                    if i == current_field:
                        curses.curs_set(1)
                        form_win.attron(curses.A_UNDERLINE)
                        form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, 
                                       field["value"] + " " * (field["width"] - len(field["value"])))
                        form_win.attroff(curses.A_UNDERLINE)
                    else:
                        form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, field["value"])
                
                elif field["type"] == "bool":
                    # Draw checkbox
                    checkbox = "[X]" if field["value"] else "[ ]"
                    if i == current_field:
                        form_win.attron(curses.A_REVERSE)
                    form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, checkbox)
                    if i == current_field:
                        form_win.attroff(curses.A_REVERSE)
            
            # Draw buttons
            else:
                if i == current_field:
                    form_win.attron(curses.A_REVERSE)
                form_win.addstr(field["y"], field["x"], f"[ {field['name']} ]")
                if i == current_field:
                    form_win.attroff(curses.A_REVERSE)
        
        # Additional help text
        form_win.addstr(12, 3, "Tab/Shift+Tab: Navigate | Enter: Select | Esc: Cancel")
        
        # Draw host suggestions if Host field is active
        if current_field == 3 and hosts:
            host_input = fields[3]["value"].lower()
            matching_hosts = [h for h in hosts if host_input in h.lower()]
            if matching_hosts:
                form_win.addstr(5, 28, "Suggestions: " + ", ".join(matching_hosts[:3]))
                if len(matching_hosts) > 3:
                    form_win.addstr(5, 28 + 12 + len(", ".join(matching_hosts[:3])), "...")
        
        form_win.refresh()
        
        # Handle key input
        ch = form_win.getch()
        
        # Tab navigation
        if ch == 9:  # Tab key
            current_field = (current_field + 1) % len(fields)
        elif ch == curses.KEY_BTAB or ch == 353:  # Shift+Tab (might vary by system)
            current_field = (current_field - 1) % len(fields)
        
        # Enter key - toggles boolean fields and activates buttons
        elif ch == 10:  # Enter key
            if fields[current_field]["type"] == "bool":
                fields[current_field]["value"] = not fields[current_field]["value"]
            elif fields[current_field]["name"] == "OK":
                # Validate and submit form
                if not fields[0]["value"]:
                    # Flash an error message if process name is empty
                    form_win.addstr(8, 3, "Error: Process name is required!", curses.A_BOLD | curses.A_REVERSE)
                    form_win.refresh()
                    time.sleep(1)
                    continue
                
                if not fields[3]["value"]:
                    # Flash an error message if host is empty
                    form_win.addstr(8, 3, "Error: Host is required!", curses.A_BOLD | curses.A_REVERSE)
                    form_win.refresh()
                    time.sleep(1)
                    continue
                
                # Form is valid, prepare result
                result = {
                    "name": fields[0]["value"],
                    "command": fields[1]["value"],
                    "group": fields[2]["value"],
                    "host": fields[3]["value"],
                    "auto_restart": fields[4]["value"],
                    "realtime": fields[5]["value"]
                }
                break
            elif fields[current_field]["name"] == "Cancel":
                break
        
        # Escape key - cancel form
        elif ch == 27:  # Escape key
            break
        
        # Field editing for text fields
        elif current_field < 4:  # Text fields only
            if ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:  # Backspace
                fields[current_field]["value"] = fields[current_field]["value"][:-1]
            elif ch >= 32 and ch <= 126:  # Printable characters
                if len(fields[current_field]["value"]) < fields[current_field]["width"]:
                    fields[current_field]["value"] += chr(ch)
    
    # Reset cursor visibility
    curses.curs_set(old_cursor)
    
    # If we have a result, create the process
    if result:
        master.create_proc(
            result["name"],
            result["command"],
            result["group"],
            result["host"],
            result["auto_restart"],
            result["realtime"]
        )
    
    # Clear key buffer
    curses.flushinp()
    return result

def show_process_dialog(stdscr, master, proc):
    """
    Display a dialog with options to start, stop, or edit a process
    """
    # Save current cursor visibility
    old_cursor = curses.curs_set(0)
    
    # Get screen dimensions
    screen_height, screen_width = stdscr.getmaxyx()
    
    # Create dialog window (centered) - increased width and height for more details
    dialog_height, dialog_width = 24, 80
    dialog_y = (screen_height - dialog_height) // 2
    dialog_x = (screen_width - dialog_width) // 2
    
    dialog_win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
    dialog_win.keypad(True)  # Enable keypad for input
    
    # Define actions based on current process state
    is_running = proc.state == "R"
    
    # Dialog options
    options = []
    if not is_running:
        options.append("Start Process")
    else:
        options.append("Stop Process")
    options.append("Edit Process")
    options.append("Cancel")
    
    selected_option = 0
    
    # State mapping
    state_names = {
        "T": "Ready", 
        "R": "Running",
        "F": "Failed",
        "K": "Killed"
    }
    
    while True:
        dialog_win.clear()
        dialog_win.box()
        
        # Draw dialog title
        dialog_win.attron(curses.A_BOLD)
        dialog_win.addstr(0, 2, f"Process: {proc.name}")
        dialog_win.attroff(curses.A_BOLD)
        
        # Draw process info - all available attributes
        y_pos = 2
        
        # Basic identification information
        dialog_win.addstr(y_pos, 2, f"Process ID: {proc.pid if proc.pid != -1 else 'N/A'}")
        dialog_win.addstr(y_pos, 40, f"Parent PID: {proc.ppid if proc.ppid != -1 else 'N/A'}")
        y_pos += 1
        
        dialog_win.addstr(y_pos, 2, f"Group: {proc.group}")
        dialog_win.addstr(y_pos, 40, f"Host: {proc.hostname}")
        y_pos += 1
        
        # Status information with colors
        dialog_win.addstr(y_pos, 2, "State: ")
        if proc.state == "R":  # Running
            dialog_win.attron(curses.color_pair(4))  # Green
            dialog_win.addstr(y_pos, 9, state_names.get(proc.state, proc.state))
            dialog_win.attroff(curses.color_pair(4))
        elif proc.state == "F" or proc.state == "K":  # Failed or Killed
            dialog_win.attron(curses.color_pair(2))  # Red
            dialog_win.addstr(y_pos, 9, state_names.get(proc.state, proc.state))
            dialog_win.attroff(curses.color_pair(2))
        else:  # Ready or any other state
            dialog_win.addstr(y_pos, 9, state_names.get(proc.state, proc.state))
        
        dialog_win.addstr(y_pos, 40, f"Status: {proc.status}")
        y_pos += 1
        
        # Configuration flags
        dialog_win.addstr(y_pos, 2, f"Auto Restart: {'Yes' if proc.auto_restart else 'No'}")
        dialog_win.addstr(y_pos, 40, f"Realtime: {'Yes' if proc.realtime else 'No'}")
        y_pos += 1
        
        # Priority and runtime
        dialog_win.addstr(y_pos, 2, f"Priority: {proc.priority if proc.priority != -1 else 'N/A'}")
        dialog_win.addstr(y_pos, 40, f"Runtime: {proc.runtime} seconds")
        y_pos += 1
        
        # Exit code if applicable
        if proc.state in ["F", "K"]:
            dialog_win.addstr(y_pos, 2, f"Exit Code: {proc.exit_code}")
            y_pos += 1
        
        # Resource usage
        dialog_win.addstr(y_pos, 2, f"CPU Usage: {proc.cpu*100:.1f}%")
        y_pos += 1
        
        dialog_win.addstr(y_pos, 2, f"Memory RSS: {proc.mem_rss:.1f} KB ({proc.mem_rss/1024:.1f} MB)")
        y_pos += 1
        
        dialog_win.addstr(y_pos, 2, f"Memory VMS: {proc.mem_vms:.1f} KB ({proc.mem_vms/1024:.1f} MB)")
        y_pos += 1
        
        # Command (might be long)
        y_pos += 1
        dialog_win.addstr(y_pos, 2, "Command:")
        y_pos += 1
        
        # Display command over multiple lines if needed
        max_cmd_width = dialog_width - 4
        if len(proc.cmd) <= max_cmd_width:
            dialog_win.addstr(y_pos, 2, proc.cmd)
            y_pos += 1
        else:
            # Split command into multiple lines
            for i in range(0, len(proc.cmd), max_cmd_width):
                dialog_win.addstr(y_pos, 2, proc.cmd[i:i+max_cmd_width])
                y_pos += 1
                # Limit to 3 lines max
                if i >= 2 * max_cmd_width:
                    dialog_win.addstr(y_pos, 2, "...")
                    y_pos += 1
                    break
        
        # Show errors if any
        if hasattr(proc, 'errors') and proc.errors:
            y_pos += 1
            dialog_win.attron(curses.color_pair(2))  # Red for errors
            dialog_win.addstr(y_pos, 2, "Errors:")
            y_pos += 1
            
            # Split error messages into multiple lines if needed
            for i in range(0, len(proc.errors), max_cmd_width):
                dialog_win.addstr(y_pos, 2, proc.errors[i:i+max_cmd_width])
                y_pos += 1
                # Limit to 3 lines max
                if i >= 2 * max_cmd_width:
                    dialog_win.addstr(y_pos, 2, "...")
                    y_pos += 1
                    break
                    
            dialog_win.attroff(curses.color_pair(2))
        
        # Draw horizontal line
        dialog_win.hline(dialog_height - 5, 1, curses.ACS_HLINE, dialog_width - 2)
        
        # Draw options
        for i, option in enumerate(options):
            if i == selected_option:
                dialog_win.attron(curses.A_REVERSE)
            dialog_win.addstr(dialog_height - 4 + i, 2, f"[{i+1}] {option}")
            if i == selected_option:
                dialog_win.attroff(curses.A_REVERSE)
        
        dialog_win.refresh()
        
        # Handle key input
        ch = dialog_win.getch()
        
        if ch == curses.KEY_UP and selected_option > 0:
            selected_option -= 1
        elif ch == curses.KEY_DOWN and selected_option < len(options) - 1:
            selected_option += 1
        elif ch in [10, 13]:  # Enter key
            # Handle the selected option
            if options[selected_option] == "Start Process":
                master.start_proc(proc.name, proc.hostname)
                break
            elif options[selected_option] == "Stop Process":
                master.stop_proc(proc.name, proc.hostname)
                break
            elif options[selected_option] == "Edit Process":
                show_edit_process_form(stdscr, master, proc)
                break
            else:  # Cancel
                break
        elif ch == 27:  # Escape key
            break
        elif ch in [49, 50, 51]:  # Number keys 1-3
            option_idx = ch - 49  # Convert to 0-based index
            if option_idx < len(options):
                selected_option = option_idx
                # Simulate Enter key press
                continue
    
    # Reset cursor visibility
    curses.curs_set(old_cursor)
    
    # Clear key buffer
    curses.flushinp()

def show_edit_process_form(stdscr, master, proc):
    """
    Display a form for editing an existing process
    """
    # Save current cursor visibility
    old_cursor = curses.curs_set(0)
    
    # Get screen dimensions
    screen_height, screen_width = stdscr.getmaxyx()
    
    # Create form window (centered) - increased width for longer commands
    form_height, form_width = 16, 80
    form_y = (screen_height - form_height) // 2
    form_x = (screen_width - form_width) // 2
    
    form_win = curses.newwin(form_height, form_width, form_y, form_x)
    form_win.keypad(True)  # Enable keypad for input
    
    # Form fields pre-populated with process data - increased width for command
    fields = [
        {"name": "Process Name", "value": proc.name, "type": "text", "y": 2, "x": 3, "width": 40},
        {"name": "Process Command", "value": proc.cmd, "type": "text", "y": 3, "x": 3, "width": 70}, # Much wider command field
        {"name": "Group", "value": proc.group, "type": "text", "y": 4, "x": 3, "width": 30},
        {"name": "Host", "value": proc.hostname, "type": "text", "y": 5, "x": 3, "width": 30},
        {"name": "Auto Restart", "value": proc.auto_restart, "type": "bool", "y": 6, "x": 3},
        {"name": "Realtime", "value": proc.realtime, "type": "bool", "y": 7, "x": 3},
        {"name": "Update", "value": "", "type": "button", "y": 10, "x": 30},
        {"name": "Cancel", "value": "", "type": "button", "y": 10, "x": 45}
    ]
    
    current_field = 0
    result = None
    
    # Get available hosts for dropdown
    hosts = list(master.hosts.keys())
    
    # Form input loop (similar to create process form)
    while True:
        form_win.clear()
        form_win.box()
        
        # Draw form title
        form_win.attron(curses.A_BOLD)
        form_win.addstr(0, 2, "Edit Process")
        form_win.attroff(curses.A_BOLD)
        
        # Draw fields (similar to create process form)
        for i, field in enumerate(fields):
            # Draw field label
            if field["type"] != "button":
                form_win.addstr(field["y"], field["x"], f"{field['name']}: ")
                
                # Draw field value
                if field["type"] == "text":
                    # If this is the current field, show cursor
                    if i == current_field:
                        curses.curs_set(1)
                        form_win.attron(curses.A_UNDERLINE)
                        form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, 
                                       field["value"] + " " * (field["width"] - len(field["value"])))
                        form_win.attroff(curses.A_UNDERLINE)
                    else:
                        form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, field["value"])
                
                elif field["type"] == "bool":
                    # Draw checkbox
                    checkbox = "[X]" if field["value"] else "[ ]"
                    if i == current_field:
                        form_win.attron(curses.A_REVERSE)
                    form_win.addstr(field["y"], field["x"] + len(field["name"]) + 2, checkbox)
                    if i == current_field:
                        form_win.attroff(curses.A_REVERSE)
            
            # Draw buttons
            else:
                if i == current_field:
                    form_win.attron(curses.A_REVERSE)
                form_win.addstr(field["y"], field["x"], f"[ {field['name']} ]")
                if i == current_field:
                    form_win.attroff(curses.A_REVERSE)
        
        # Additional help text
        form_win.addstr(12, 3, "Tab/Shift+Tab: Navigate | Enter: Select | Esc: Cancel")
        
        # Draw host suggestions if Host field is active
        if current_field == 3 and hosts:
            host_input = fields[3]["value"].lower()
            matching_hosts = [h for h in hosts if host_input in h.lower()]
            if matching_hosts:
                form_win.addstr(5, 28, "Suggestions: " + ", ".join(matching_hosts[:3]))
                if len(matching_hosts) > 3:
                    form_win.addstr(5, 28 + 12 + len(", ".join(matching_hosts[:3])), "...")
        
        form_win.refresh()
        
        # Handle input (similar to create process form)
        ch = form_win.getch()
        
        # Tab navigation
        if ch == 9:  # Tab key
            current_field = (current_field + 1) % len(fields)
        elif ch == curses.KEY_BTAB or ch == 353:  # Shift+Tab (might vary by system)
            current_field = (current_field - 1) % len(fields)
        
        # Enter key - toggles boolean fields and activates buttons
        elif ch == 10:  # Enter key
            if fields[current_field]["type"] == "bool":
                fields[current_field]["value"] = not fields[current_field]["value"]
            elif fields[current_field]["name"] == "Update":
                # Validate and submit form
                if not fields[0]["value"]:
                    form_win.addstr(8, 3, "Error: Process name is required!", curses.A_BOLD | curses.A_REVERSE)
                    form_win.refresh()
                    time.sleep(1)
                    continue
                
                if not fields[3]["value"]:
                    form_win.addstr(8, 3, "Error: Host is required!", curses.A_BOLD | curses.A_REVERSE)
                    form_win.refresh()
                    time.sleep(1)
                    continue
                
                # Form is valid, prepare result
                result = {
                    "name": fields[0]["value"],
                    "command": fields[1]["value"],
                    "group": fields[2]["value"],
                    "host": fields[3]["value"],
                    "auto_restart": fields[4]["value"],
                    "realtime": fields[5]["value"]
                }
                break
            elif fields[current_field]["name"] == "Cancel":
                break
        
        # Escape key - cancel form
        elif ch == 27:  # Escape key
            break
        
        # Field editing for text fields
        elif current_field < 4:  # Text fields only
            if ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:  # Backspace
                fields[current_field]["value"] = fields[current_field]["value"][:-1]
            elif ch >= 32 and ch <= 126:  # Printable characters
                if len(fields[current_field]["value"]) < fields[current_field]["width"]:
                    fields[current_field]["value"] += chr(ch)
    
    # Reset cursor visibility
    curses.curs_set(old_cursor)
    
    # If we have a result, update the process
    if result:
        # First stop the current process if it's running
        if proc.state == "R":
            master.stop_proc(proc.name, proc.hostname)
            # Small delay to ensure the stop command is processed
            time.sleep(0.5)
        
        # Then create a new process with the updated settings
        master.create_proc(
            result["name"],
            result["command"],
            result["group"],
            result["host"],
            result["auto_restart"],
            result["realtime"]
        )
        
        # If the original process was running, start the new one
        if proc.state == "R":
            # Small delay to ensure the create command is processed
            time.sleep(0.5)
            master.start_proc(result["name"], result["host"])
    
    # Clear key buffer
    curses.flushinp()

def main(stdscr):
    # Initialize DPM_Master
    try:
        master = DPM_Master(CONFIG_PATH)
        # Start the LCM handling thread
        master.start()
    except Exception as e:
        return f"Error initializing DPM_Master: {e}"
    
    # Clear screen and initialize colors
    curses.curs_set(0)
    stdscr.nodelay(True)  # non-blocking input
    stdscr.clear()

    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Default
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)    # Offline/Stopped
    # curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected - no longer used
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Running
    
    # Get screen dimensions
    screen_height, screen_width = stdscr.getmaxyx()
    
    # Define host panel dimensions (left side)
    host_panel_width = 50  # Increased width to accommodate graphs
    host_panel_height = screen_height
    host_panel_x = 0
    
    # Define process panel dimensions (right side)
    proc_panel_width = screen_width - host_panel_width - 1
    proc_panel_height = screen_height
    proc_panel_x = host_panel_width + 1
    
    # Create panels
    host_win = curses.newwin(host_panel_height, host_panel_width, 0, host_panel_x)
    proc_win = curses.newwin(proc_panel_height, proc_panel_width, 0, proc_panel_x)
    
    # Variables for interaction
    selected_proc = 0
    last_update = 0
    update_interval = 0.5  # seconds
    
    try:
        # Main UI loop
        while True:
            # Update DPM_Master data
            master.update()
            
            current_time = time.time()
            if current_time - last_update >= update_interval:
                # Draw the host panel on the left side
                draw_hosts_panel(host_win, master.hosts)  # Thread-safe access
                
                # Draw the process table on the right side
                draw_process_table(proc_win, master.procs, selected_proc)  # Thread-safe access
                
                last_update = current_time
            
            # Handle user input
            try:
                ch = stdscr.getkey()
                if ch.lower() == 'q':
                    break
                elif ch.lower() == 'n':  # New process
                    # Temporarily switch to blocking input mode
                    stdscr.nodelay(False)
                    show_create_process_form(stdscr, master)
                    # Switch back to non-blocking
                    stdscr.nodelay(True)
                    # Redraw everything
                    stdscr.clear()
                    stdscr.refresh()
                    host_win.clear()
                    proc_win.clear()
                elif ch == 'KEY_UP' and selected_proc > 0:
                    selected_proc -= 1
                elif ch == 'KEY_DOWN' and selected_proc < len(master.procs) - 1:
                    selected_proc += 1
                elif ch in ['\n', '\r', 'KEY_ENTER']:  # Enter key - show process dialog
                    proc_list = list(master.procs.values())
                    if proc_list and 0 <= selected_proc < len(proc_list):
                        # Temporarily switch to blocking input mode
                        stdscr.nodelay(False)
                        show_process_dialog(stdscr, master, proc_list[selected_proc])
                        # Switch back to non-blocking
                        stdscr.nodelay(True)
                        # Redraw everything
                        stdscr.clear()
                        stdscr.refresh()
                        host_win.clear()
                        proc_win.clear()
            except Exception:
                # no key pressed
                pass
                
            stdscr.refresh()
            time.sleep(0.05)  # Reduce CPU usage
    finally:
        # Make sure to stop the thread when exiting
        master.stop()

if __name__ == "__main__":
    result = curses.wrapper(main)
    if result:  # If there was an error
        print(result)