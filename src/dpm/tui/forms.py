import curses
import curses.ascii
import time
from .helpers import _prompt_input, _prompt_yesno, _show_message, select_from_list
from .io import save_process_spec

def show_create_process_form(stdscr, controller, default_host=None):
    """
    Modal inline-edit form for creating a process.

    Fields (focusable, Tab/Shift-Tab to navigate):
      0 Process Name     (text)
      1 Host             (select list via Enter)
      2 Command          (text)
      3 Group            (text)
      4 Auto Restart     (toggle)
      5 Realtime         (toggle)
      6 [Apply]          (button)
      7 [Cancel]         (button)

    Enter on Apply validates and calls controller.create_proc(...)
    Esc or Cancel aborts.
    """
    curses.curs_set(1)
    hosts = sorted(list(controller.hosts.keys()))
    host_prefill = default_host or (hosts[0] if hosts else "")

    # form state
    name = ""
    host = host_prefill
    command = ""
    group = ""
    auto_restart = False
    realtime = False

    focus = 0
    field_buffers = {
        0: name,
        2: command,
        3: group,
    }

    def draw(win):
        win.erase()
        win.box()
        h, w = win.getmaxyx()
        title = " Create Process "
        try:
            win.attron(curses.A_BOLD)
            win.addstr(0, 2, title)
            win.attroff(curses.A_BOLD)
        except Exception:
            pass

        # layout
        labels = [
            ("Process Name:", field_buffers[0]),
            ("Host:", host or "(none)"),
            ("Command:", field_buffers[2]),
            ("Group:", field_buffers[3]),
            ("Auto Restart:", "On" if auto_restart else "Off"),
            ("Realtime:", "On" if realtime else "Off"),
        ]

        for idx, (lab, val) in enumerate(labels):
            y = 2 + idx
            x = 2
            try:
                if focus == idx:
                    # highlight the whole line for focused field
                    win.attron(curses.A_REVERSE)
                    win.addstr(y, x, f"{lab:<15} ")
                    win.addstr(y, x + 16, f"{str(val)[: w - 20]}")
                    win.attroff(curses.A_REVERSE)
                else:
                    win.addstr(y, x, f"{lab:<15} ")
                    win.addstr(y, x + 16, f"{str(val)[: w - 20]}")
            except Exception:
                pass

        # buttons (Apply, Cancel)
        btn_y = 2 + len(labels) + 1
        try:
            if focus == 6:
                win.attron(curses.A_REVERSE)
            win.addstr(btn_y, 4, "[ Apply ]")
            if focus == 6:
                win.attroff(curses.A_REVERSE)

            if focus == 7:
                win.attron(curses.A_REVERSE)
            win.addstr(btn_y, 16, "[ Cancel ]")
            if focus == 7:
                win.attroff(curses.A_REVERSE)
        except Exception:
            pass

        # help
        try:
            win.attron(curses.A_DIM)
            win.addstr(h - 2, 2, "Tab/Shift-Tab: navigate  Enter: edit/select/apply  Esc: cancel")
            win.attroff(curses.A_DIM)
        except Exception:
            pass

        win.refresh()

    maxy, maxx = stdscr.getmaxyx()
    win_w = min(96, maxx - 4)
    win_h = 12
    win = stdscr.subwin(win_h, win_w, (maxy - win_h) // 2, (maxx - win_w) // 2)
    win.keypad(True)

    while True:
        # sync buffers into locals
        name = field_buffers.get(0, "")
        command = field_buffers.get(2, "")
        group = field_buffers.get(3, "")

        draw(win)
        ch = win.getch()

        # Navigation
        if ch in (9,):  # TAB
            focus = (focus + 1) % 8
            curses.curs_set(0 if focus in (1,4,5,6,7) else 1)
            continue
        if ch in (curses.KEY_BTAB, 353):  # Shift-Tab
            focus = (focus - 1) % 8
            curses.curs_set(0 if focus in (1,4,5,6,7) else 1)
            continue

        # Cancel
        if ch in (27, ord('q')):  # ESC or q
            _show_message(stdscr, "Create canceled")
            curses.curs_set(0)
            return

        # If focus is Host (1): open selection on Enter or Space
        if focus == 1 and ch in (10, 13, ord(' ')):
            sel = select_from_list(stdscr, "Select host", hosts, hosts.index(host) if host in hosts else 0)
            if sel:
                host = sel
            continue

        # Toggles
        if focus in (4, 5) and ch in (10, 13, ord(' ')):
            if focus == 4:
                auto_restart = not auto_restart
            else:
                realtime = not realtime
            continue

        # Buttons
        if focus == 6 and ch in (10, 13):  # Apply
            # read latest values
            name = field_buffers.get(0, "").strip()
            command = field_buffers.get(2, "").strip()
            group = field_buffers.get(3, "").strip()
            if not name:
                _show_message(stdscr, "Name required")
                continue
            if not command:
                _show_message(stdscr, "Command required")
                continue
            if not host:
                _show_message(stdscr, "Host required")
                continue
            try:
                controller.create_proc(name, command, group, host, auto_restart, realtime)
                _show_message(stdscr, f"Created {name}@{host}")
            except Exception as e:
                _show_message(stdscr, f"Create failed: {e}", duration=2.5)
            curses.curs_set(0)
            return

        if focus == 7 and ch in (10, 13):  # Cancel
            _show_message(stdscr, "Create canceled")
            curses.curs_set(0)
            return

        # Text editing for fields 0 (name), 2 (command), 3 (group)
        if focus in (0, 2, 3):
            buf = field_buffers.get(focus, "")
            # start editing if Enter pressed or printable char
            if ch in (10, 13):
                # Enter moves to next field
                focus = (focus + 1) % 8
                continue
            if ch in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_UP, curses.KEY_DOWN):
                # ignore cursor movement in inline editor
                continue
            if ch in (curses.KEY_BACKSPACE, 127, curses.ascii.BS):
                buf = buf[:-1]
                field_buffers[focus] = buf
                continue
            if ch == curses.KEY_DC:
                # delete full buffer
                field_buffers[focus] = ""
                continue
            # printable characters
            if 32 <= ch <= 126:
                buf += chr(ch)
                field_buffers[focus] = buf
                continue
            # other keys ignored

    # end while

def show_output_panel(stdscr, controller, proc):
    """
    Open a transient panel on top that continuously displays the output for `proc`.
    Close with ESC. Use Up/Down to scroll history.
    """
    curses.curs_set(0)
    maxy, maxx = stdscr.getmaxyx()
    h = max(8, maxy - 6)
    w = max(40, maxx - 10)
    y = (maxy - h) // 2
    x = (maxx - w) // 2
    win = stdscr.subwin(h, w, y, x)
    win.keypad(True)
    win.timeout(250)  # refresh interval

    scroll = 0
    history_lines = []
    last_lines = None

    def _lookup_output():
        outputs = getattr(controller, "proc_outputs", None) or getattr(controller, "_proc_outputs", None) or {}
        candidates = [
            getattr(proc, "name", None),
            f"{getattr(proc, 'name', '')}@{getattr(proc, 'hostname', '')}",
            f"{getattr(proc, 'hostname', '')}:{getattr(proc, 'name', '')}",
        ]
        msg = None
        if isinstance(outputs, dict):
            for k in candidates:
                if not k:
                    continue
                if k in outputs:
                    msg = outputs.get(k)
                    break
        if msg is None and isinstance(outputs, dict) and getattr(proc, "name", None) in outputs:
            msg = outputs.get(getattr(proc, "name"))

        # extract text from common shapes
        text = ""
        try:
            if msg is None:
                text = ""
            elif isinstance(msg, str):
                text = msg
            elif isinstance(msg, bytes):
                text = msg.decode("utf-8", "replace")
            elif hasattr(msg, "stdout"):
                text = getattr(msg, "stdout") or ""
            elif isinstance(msg, dict):
                text = msg.get("stdout") or msg.get("output") or ""
            else:
                text = str(msg)
        except Exception:
            text = "(error reading output)"
        return text

    while True:
        win.erase()
        win.box()
        title = f" Output: {getattr(proc, 'name', '')} "
        try:
            win.attron(curses.A_BOLD)
            win.addstr(0, 2, title[: (w - 4)])
            win.attroff(curses.A_BOLD)
        except Exception:
            pass

        # fetch current output text (may be empty intermittently)
        text = _lookup_output()

        # Convert to lines and manage history:
        if text:
            new_lines = text.splitlines()
            if last_lines is None:
                # first time we see data -> replace history
                history_lines = new_lines[:]
            else:
                # if the output is a continued stream (new_lines starts with last_lines),
                # append only the tail; otherwise replace (log rotation or different source)
                if len(new_lines) >= len(last_lines) and new_lines[: len(last_lines)] == last_lines:
                    # append only new lines
                    if len(new_lines) > len(last_lines):
                        history_lines.extend(new_lines[len(last_lines) :])
                else:
                    # replace entire history (e.g. rotated or full dump)
                    history_lines = new_lines[:]
            last_lines = new_lines
        else:
            # no new text right now -> keep existing history_lines unchanged
            pass

        # ensure there's at least an empty line for display
        if not history_lines:
            history_lines = ["(no output)"]

        # display window content (leave 3 rows for borders/title/footer)
        content_h = h - 4
        # clamp scroll
        max_scroll = max(0, len(history_lines) - content_h)
        scroll = max(0, min(scroll, max_scroll))
        start = max(0, len(history_lines) - content_h - scroll)
        visible = history_lines[start : start + content_h]

        for i, ln in enumerate(visible):
            try:
                win.addstr(2 + i, 2, ln[: (w - 4)])
            except Exception:
                pass

        # footer / help
        try:
            win.attron(curses.A_DIM)
            help_txt = "Esc: close  Up/Down: scroll"
            win.addstr(h - 2, 2, help_txt[: (w - 4)])
            win.attroff(curses.A_DIM)
        except Exception:
            pass

        win.refresh()

        ch = win.getch()
        if ch in (27,):  # ESC
            break
        if ch == curses.KEY_UP:
            scroll = min(scroll + 1, max_scroll)
        elif ch == curses.KEY_DOWN:
            scroll = max(0, scroll - 1)
        # loop and refresh output periodically

    # restore
    try:
        curses.curs_set(1)
    except Exception:
        pass
    stdscr.touchwin()
    stdscr.refresh()

def show_process_dialog(stdscr, controller, proc):
    """
    Existing process operations dialog (Start/Stop/Edit/View/Delete).
    Edit uses the create form to re-create the proc with new settings.
    """
    options = ["Start", "Stop", "Edit", "View Output", "Delete", "Cancel"]
    sel = 0
    maxy, maxx = stdscr.getmaxyx()
    w = max(len(o) for o in options) + 10
    h = len(options) + 4
    win = stdscr.subwin(h, w, (maxy - h) // 2, (maxx - w) // 2)
    win.keypad(True)
    curses.curs_set(0)
    while True:
        win.erase()
        win.box()
        try:
            win.addstr(0, 2, f" Process: {getattr(proc, 'name', '')} ", curses.A_BOLD)
            for i, o in enumerate(options):
                if i == sel:
                    win.addstr(2 + i, 2, o, curses.A_REVERSE)
                else:
                    win.addstr(2 + i, 2, o)
        except Exception:
            pass
        stdscr.refresh()
        win.refresh()
        ch = win.getch()
        if ch in (curses.KEY_UP, ord('k')):
            sel = (sel - 1) % len(options)
        elif ch in (curses.KEY_DOWN, ord('j')):
            sel = (sel + 1) % len(options)
        elif ch in (10, 13):
            choice = options[sel]
            try:
                if choice == "Start":
                    controller.start_proc(proc.name, proc.hostname)
                    _show_message(stdscr, f"Start sent for {proc.name}@{proc.hostname}")
                elif choice == "Stop":
                    controller.stop_proc(proc.name, proc.hostname)
                    _show_message(stdscr, f"Stop sent for {proc.name}@{proc.hostname}")
                elif choice == "Edit":
                    # reuse create form (prefill host and name by passing defaults via controller)
                    show_create_process_form(stdscr, controller, default_host=proc.hostname)
                elif choice == "View Output":
                    # Open the live output panel (ESC to close)
                    show_output_panel(stdscr, controller, proc)
                elif choice == "Delete":
                    controller.del_proc(proc.name, proc.hostname)
                    _show_message(stdscr, f"Deleted {proc.name}@{proc.hostname}")
                else:
                    pass
            except Exception as e:
                _show_message(stdscr, f"Operation failed: {e}", duration=2.5)
            break
        elif ch in (ord('q'), 27):
            break
    curses.curs_set(1)