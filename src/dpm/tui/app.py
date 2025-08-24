#!/usr/bin/env python3
"""
Entry point for the DPM TUI when run as a package: python -m dpm.tui.app
This mirrors the original top-level dpm.py logic but uses package imports so it works
when the project is used as a package (src/ layout).
"""
import curses
import time
import os
from pathlib import Path

from dpm.controller.controller import Controller  # type: ignore
from dpm.tui.helpers import (
    _prompt_input,
    _prompt_yesno,
    _show_message,
    select_from_list,
    get_hosts_list,
    get_procs_for_host,
    get_proc_by_selected_index,
)
from dpm.tui.forms import show_create_process_form, show_process_dialog
from dpm.tui.panels import draw_hosts_panel, draw_process_table
from dpm.tui.io import save_all_process_specs, load_and_create


def _resolve_config_path() -> str:
    env_cfg = os.getenv("DPM_CONFIG")
    if env_cfg and os.path.isfile(env_cfg):
        return env_cfg
    etc_cfg = "/etc/dpm/dpm.yaml"
    if os.path.isfile(etc_cfg):
        return etc_cfg
    opt_cfg = "/opt/dpm/dpm.yaml"
    if os.path.isfile(opt_cfg):
        return opt_cfg
    xdg_home = os.getenv("XDG_CONFIG_HOME", os.path.join(Path.home(), ".config"))
    xdg_cfg = os.path.join(xdg_home, "dpm", "dpm.yaml")
    if os.path.isfile(xdg_cfg):
        return xdg_cfg
    # repo fallback
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        candidate = p / "dpm.yaml"
        if candidate.is_file():
            return str(candidate)
    return etc_cfg


REPO_ROOT = str(Path(__file__).resolve().parents[3])

_spawned_nodes = []
REPORT_THRESHOLD = 5


def _spawn_local_node(stdscr):
    logs_dir = os.path.join(REPO_ROOT, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        pass
    ts = int(time.time())
    logfile = os.path.join(logs_dir, f"node-{ts}.log")
    try:
        logf = open(logfile, "a")
        import subprocess

        proc = subprocess.Popen(
            ["/usr/bin/env", "python3", os.path.join(REPO_ROOT, "node", "node.py")],
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


def _stop_last_spawned_node(stdscr):
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
        except Exception:
            proc.kill()
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


def _save_all_command(stdscr, controller):
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


def _load_specs_command(stdscr, controller):
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


FOOTER_HELP = (
    "Left/Right: switch host  Up/Down: select proc  Enter: dialog  "
    "n:new  P:spawn node  O:stop node  "
    "A:save all  L:load  q:quit"
)


def _curses_main(stdscr):
    try:
        controller = Controller(_resolve_config_path())
        controller.start()
    except Exception as e:
        return f"Error initializing Controller: {e}"

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)

    selected_host_idx = 0
    selected_proc_idx = 0
    focus = "procs"

    try:
        while True:
            stdscr.erase()
            maxy, maxx = stdscr.getmaxyx()

            host_h = max(4, maxy // 6)
            proc_h = maxy - host_h - 1

            host_win = stdscr.derwin(host_h, maxx, 0, 0)
            proc_win = stdscr.derwin(proc_h, maxx, host_h, 0)

            hosts = get_hosts_list(controller)
            if not hosts:
                hosts = ["(no hosts)"]
            if selected_host_idx >= len(hosts):
                selected_host_idx = max(0, len(hosts) - 1)
            selected_host = hosts[selected_host_idx]

            procs = get_procs_for_host(controller, selected_host)
            if not procs:
                procs = []
            if selected_proc_idx >= len(procs):
                selected_proc_idx = max(0, len(procs) - 1)

            host_threshold = REPORT_THRESHOLD
            try:
                host_threshold = float(controller.config.get("report_threshold", REPORT_THRESHOLD))
            except Exception:
                host_threshold = REPORT_THRESHOLD

            draw_hosts_panel(host_win, controller.hosts, threshold=host_threshold)
            draw_process_table(proc_win, procs, selected_proc_idx)

            try:
                stdscr.addstr(maxy - 1, 0, FOOTER_HELP[: maxx - 1], curses.A_DIM)
            except Exception:
                pass

            stdscr.refresh()
            host_win.refresh()
            proc_win.refresh()

            try:
                ch = stdscr.get_wch()
            except Exception:
                ch = None

            if ch is None:
                continue

            if ch == curses.KEY_LEFT:
                selected_host_idx = max(0, selected_host_idx - 1)
                selected_proc_idx = 0
            elif ch == curses.KEY_RIGHT:
                selected_host_idx = min(len(hosts) - 1, selected_host_idx + 1)
                selected_proc_idx = 0
            elif ch == curses.KEY_UP:
                selected_proc_idx = max(0, selected_proc_idx - 1)
            elif ch == curses.KEY_DOWN:
                selected_proc_idx = min(len(procs) - 1 if procs else 0, selected_proc_idx + 1)
            elif ch in ("\t",):
                focus = "hosts" if focus == "procs" else "procs"
            elif ch in ("\n", "\r"):
                proc = get_proc_by_selected_index(controller, selected_proc_idx)
                if proc is None:
                    _show_message(stdscr, "No process selected", duration=1.0)
                else:
                    show_process_dialog(stdscr, controller, proc)
            elif ch in ("n", "N"):
                show_create_process_form(stdscr, controller, default_host=selected_host)
            elif ch in ("P",):
                _spawn_local_node(stdscr)
            elif ch in ("O",):
                _stop_last_spawned_node(stdscr)
            elif isinstance(ch, str) and ch.upper() == "A":
                _save_all_command(stdscr, controller)
            elif isinstance(ch, str) and ch.upper() == "L":
                _load_specs_command(stdscr, controller)
            elif ch in ("q", "Q"):
                break

            time.sleep(0.01)

    finally:
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
        try:
            controller.stop()
        except Exception:
            pass


def main():
    return curses.wrapper(_curses_main)


if __name__ == "__main__":
    result = main()
    if result:
        print(result)
