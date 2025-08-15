#!/usr/bin/python3
import curses
import time
import os
import sys
import subprocess

# New config path at repo root
CONFIG_PATH = "dpm.yaml"
REPO_ROOT = os.path.abspath(os.path.dirname(__file__))

# Track locally spawned node processes for testing
_spawned_nodes = []  # list of tuples: (Popen, logfile_path)

# Ensure controller package is importable when running from repo root
from controller import Controller

REPORT_THRESHOLD = 5  # number of seconds after which host is considered offline

# Import TUI modules (split for readability)
from tui.helpers import _prompt_input, _prompt_yesno, _show_message, select_from_list, get_hosts_list, get_procs_for_host, get_proc_by_selected_index
from tui.forms import show_create_process_form, show_process_dialog
from tui.panels import draw_hosts_panel, draw_process_table
from tui.io import save_all_process_specs, load_and_create

def main(stdscr):
    # Initialize Controller
    try:
        controller = Controller(CONFIG_PATH)
        controller.start()
    except Exception as e:
        return f"Error initializing Controller: {e}"

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)

    selected_host_idx = 0
    selected_proc_idx = 0
    focus = "procs"  # could be "hosts" or "procs"

    try:
        while True:
            stdscr.erase()
            maxy, maxx = stdscr.getmaxyx()

            # Host row height (title + 3 rows for each host column)
            host_h = max(4, maxy // 6)
            # Reserve final line for footer/help
            proc_h = maxy - host_h - 1

            host_win = stdscr.derwin(host_h, maxx, 0, 0)
            proc_win = stdscr.derwin(proc_h, maxx, host_h, 0)

            # Refresh data
            hosts = get_hosts_list(controller)
            if not hosts:
                hosts = ["(no hosts)"]
            # clamp selected_host_idx
            if selected_host_idx >= len(hosts):
                selected_host_idx = max(0, len(hosts) - 1)

            selected_host = hosts[selected_host_idx]

            # get processes for selected host
            procs = get_procs_for_host(controller, selected_host)
            if not procs:
                procs = []

            # clamp selected_proc_idx
            if selected_proc_idx >= len(procs):
                selected_proc_idx = max(0, len(procs) - 1)

            # Determine host staleness threshold from controller config (fallback to REPORT_THRESHOLD)
            host_threshold = REPORT_THRESHOLD
            try:
                host_threshold = float(controller.config.get("report_threshold", REPORT_THRESHOLD))
            except Exception:
                host_threshold = REPORT_THRESHOLD

            # Draw hosts as a horizontal row
            draw_hosts_panel(host_win, controller.hosts, threshold=host_threshold)

            # Draw processes table on bottom area
            draw_process_table(proc_win, procs, selected_proc_idx)

            # Footer / help line
            help_line = "Left/Right: switch host  Up/Down: select proc  Enter: dialog  n:new  P:spawn node  O:stop node  A:save all  L:load  q:quit"
            try:
                stdscr.addstr(maxy - 1, 0, help_line[:maxx - 1], curses.A_DIM)
            except Exception:
                pass

            stdscr.refresh()
            host_win.refresh()
            proc_win.refresh()

            # handle input
            try:
                ch = stdscr.get_wch()
            except Exception:
                ch = None

            if ch is None:
                continue

            # navigation keys
            if ch == curses.KEY_LEFT:
                # move host left (previous)
                selected_host_idx = max(0, selected_host_idx - 1)
                selected_proc_idx = 0
            elif ch == curses.KEY_RIGHT:
                selected_host_idx = min(len(hosts) - 1, selected_host_idx + 1)
                selected_proc_idx = 0
            elif ch == curses.KEY_UP:
                selected_proc_idx = max(0, selected_proc_idx - 1)
            elif ch == curses.KEY_DOWN:
                selected_proc_idx = min(len(procs) - 1 if procs else 0, selected_proc_idx + 1)
            elif ch in ("\t",):  # Tab toggles focus
                focus = "hosts" if focus == "procs" else "procs"
            elif ch in ("\n", "\r"):
                # Show process dialog for the UI-selected process (use same mapping as panel)
                proc = get_proc_by_selected_index(controller, selected_proc_idx)
                if proc is None:
                    _show_message(stdscr, "No process selected", duration=1.0)
                else:
                    show_process_dialog(stdscr, controller, proc)
            elif ch in ("n", "N"):
                show_create_process_form(stdscr, controller, default_host=selected_host)
            # start/stop via shortcuts removed — use Enter -> dialog menu to Start/Stop
            elif ch in ("P",):
                spawn_local_node(stdscr)
            elif ch in ("O",):
                stop_last_spawned_node(stdscr)
            # Accept printable characters returned by get_wch() (strings)
            elif isinstance(ch, str) and ch.upper() == "A":
                save_all_command(stdscr, controller)
            elif isinstance(ch, str) and ch.upper() == "L":
                load_specs_command(stdscr, controller)
            elif ch in ("q", "Q"):
                break

            # small sleep to avoid busy loop (handled by curses timeout as well)
            time.sleep(0.01)

    finally:
        # stop any remaining spawned nodes
        while _spawned_nodes:
            try:
                proc, logfile, logf = _spawned_nodes.pop()
                proc.terminate()
                time.sleep(0.2)
                if proc.poll() is None:
                    proc.kill()
                try:
                    logf.close()
                except Exception:
                    pass
            except Exception:
                pass
        controller.stop()

def spawn_local_node(stdscr):
    """
    Spawn a local Node (node/node.py) as a background process for testing.
    Logs are written to ./logs/node-<timestamp>.log. Shows a transient status message.
    """
    logs_dir = os.path.join(REPO_ROOT, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        pass

    ts = int(time.time())
    logfile = os.path.join(logs_dir, f"node-{ts}.log")
    try:
        logf = open(logfile, "a")
        proc = subprocess.Popen(
            ["/usr/bin/env", "python3", "node/node.py"],
            stdout=logf,
            stderr=logf,
            cwd=REPO_ROOT,
            close_fds=True,
        )
        _spawned_nodes.append((proc, logfile, logf))
        try:
            _show_message(stdscr, f"Spawned node PID {proc.pid} -> {os.path.basename(logfile)}", duration=2.0)
        except Exception:
            pass
    except Exception as e:
        try:
            _show_message(stdscr, f"Failed to spawn node: {e}", duration=2.5)
        except Exception:
            pass

def stop_last_spawned_node(stdscr):
    """
    Stop the most recently spawned local Node (if any). Cleans up logfile handle.
    """
    if not _spawned_nodes:
        try:
            _show_message(stdscr, "No spawned nodes to stop", duration=1.5)
        except Exception:
            pass
        return

    proc, logfile, logf = _spawned_nodes.pop()
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
            try:
                _show_message(stdscr, f"Terminated node PID {proc.pid}", duration=1.5)
            except Exception:
                pass
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
            try:
                _show_message(stdscr, f"Killed node PID {proc.pid}", duration=1.5)
            except Exception:
                pass
    except Exception as e:
        try:
            _show_message(stdscr, f"Error stopping node: {e}", duration=2.5)
        except Exception:
            pass
    finally:
        try:
            logf.close()
        except Exception:
            pass

def draw_footer(stdscr):
    """Draw the bottom help/footer bar."""
    maxy, maxx = stdscr.getmaxyx()
    try:
        stdscr.attron(curses.A_REVERSE)
        stdscr.addstr(maxy - 1, 0, " " * (maxx - 1))
        stdscr.addstr(maxy - 1, 1, FOOTER_HELP[: maxx - 3])
        stdscr.attroff(curses.A_REVERSE)
    except Exception:
        pass

def save_all_command(stdscr, controller):
    """
    Prompt for a filename and save all current processes via save_all_process_specs().
    """
    default_path = os.path.join("saved", "processes.yml")
    fname = _prompt_input(stdscr, "Save all specs to", default_path)
    if not fname:
        _show_message(stdscr, "Save canceled")
        return
    try:
        written, skipped = save_all_process_specs(fname, controller, append=False)
        _show_message(stdscr, f"Saved {written} specs, skipped {skipped}", duration=2.5)
    except Exception as e:
        _show_message(stdscr, f"Save failed: {e}", duration=2.5)

def load_specs_command(stdscr, controller):
    """
    Prompt for YAML filename and call load_and_create(..., controller).
    Shows a short summary message of created items / errors.
    """
    default_path = os.path.join("saved", "processes.yml")
    fname = _prompt_input(stdscr, "Load specs from", default_path)
    if not fname:
        _show_message(stdscr, "Load canceled")
        return
    try:
        created, errors = load_and_create(fname, controller)
        msg = f"Created: {len(created)}"
        if errors:
            msg += f", Errors: {len(errors)}"
        _show_message(stdscr, msg, duration=2.5)
    except Exception as e:
        _show_message(stdscr, f"Load failed: {e}", duration=2.5)

# Help/footer text (start/stop/delete removed — use Enter -> menu)
FOOTER_HELP = (
    "Left/Right: switch host  Up/Down: select proc  Enter: dialog  "
    "n:new  P:spawn node  O:stop node  "
    "A:save all  L:load  q:quit"
)

if __name__ == "__main__":
    result = curses.wrapper(main)
    if result:
        print(result)