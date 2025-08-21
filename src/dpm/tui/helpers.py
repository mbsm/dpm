import curses
import time

def _prompt_input(stdscr, prompt, initial=""):
    """Single-line text input popup; returns the final string (or None on cancel)."""
    curses.echo()
    maxy, maxx = stdscr.getmaxyx()
    w = min(maxx - 4, max(40, len(prompt) + len(initial) + 10))
    win = stdscr.subwin(3, w, maxy // 2 - 1, (maxx - w) // 2)
    win.erase()
    win.box()
    try:
        win.addstr(1, 2, f"{prompt}: {initial}")
    except Exception:
        pass
    stdscr.refresh()
    win.refresh()
    # move cursor after prompt+initial
    try:
        stdscr.move(maxy // 2, (maxx - w) // 2 + 2 + len(prompt) + 2 + len(initial))
    except Exception:
        pass
    try:
        inp = win.getstr(1, 2 + len(prompt) + 2 + len(initial), 4096)
        if isinstance(inp, bytes):
            inp = inp.decode("utf-8")
    except Exception:
        inp = ""
    curses.noecho()
    if inp is None:
        return None
    return inp if inp != "" else initial

def _prompt_yesno(stdscr, prompt, default=False):
    """Yes/no popup; returns True/False."""
    maxy, maxx = stdscr.getmaxyx()
    w = len(prompt) + 12
    win = stdscr.subwin(3, w, maxy // 2 - 1, (maxx - w) // 2)
    win.erase()
    win.box()
    choice = "Y/n" if default else "y/N"
    try:
        win.addstr(1, 2, f"{prompt} [{choice}]: ")
    except Exception:
        pass
    stdscr.refresh()
    win.refresh()
    ch = win.getch()
    if ch in (ord("y"), ord("Y")):
        return True
    if ch in (ord("n"), ord("N")):
        return False
    return default

def _show_message(stdscr, text, duration=1.5):
    """Transient message on the bottom line."""
    maxy, maxx = stdscr.getmaxyx()
    try:
        stdscr.attron(curses.A_REVERSE)
        stdscr.addstr(maxy - 1, 0, " " * (maxx - 1))
        stdscr.addstr(maxy - 1, 1, text[: (maxx - 3)])
        stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()
    except Exception:
        pass
    time.sleep(duration)

def select_from_list(stdscr, title, items, selected_idx=0):
    """Popup list selection; returns selected item (or None)."""
    if not items:
        return None
    maxy, maxx = stdscr.getmaxyx()
    h = min(len(items) + 4, maxy - 4)
    w = min(max(len(title) + 4, max((len(str(i)) for i in items)) + 6, 40), maxx - 4)
    win = stdscr.subwin(h, w, (maxy - h) // 2, (maxx - w) // 2)
    win.keypad(True)
    sel = selected_idx
    while True:
        win.erase()
        win.box()
        try:
            win.addstr(0, 2, f" {title} ", curses.A_BOLD)
        except Exception:
            pass
        start = 0
        if sel >= h - 4:
            start = sel - (h - 5)
        for i in range(start, min(len(items), start + h - 4)):
            txt = str(items[i])
            try:
                if i == sel:
                    win.addstr(2 + i - start, 2, txt[: w - 4], curses.A_REVERSE)
                else:
                    win.addstr(2 + i - start, 2, txt[: w - 4])
            except Exception:
                pass
        stdscr.refresh()
        win.refresh()
        ch = win.getch()
        if ch in (curses.KEY_UP, ord('k')):
            sel = (sel - 1) % len(items)
        elif ch in (curses.KEY_DOWN, ord('j')):
            sel = (sel + 1) % len(items)
        elif ch in (10, 13, ord("\n"), ord("\r")):
            return items[sel]
        elif ch in (27, ord('q')):  # ESC/q
            return None

# New helpers for dpm.py
def get_hosts_list(controller):
    """
    Return a sorted list of hostnames from Controller (safe copy).
    Usage: from tui.helpers import get_hosts_list
    """
    try:
        hosts = controller.hosts  # thread-safe property
        return sorted(list(hosts.keys()))
    except Exception:
        return []

def get_procs_for_host(controller, host):
    """
    Return a list of proc objects for `host`.
    Usage: from tui.helpers import get_procs_for_host
    """
    try:
        procs = controller.procs  # thread-safe dict procname->proc
        return [p for p in procs.values() if getattr(p, "hostname", None) == host]
    except Exception:
        return []

def get_proc_by_selected_index(controller, selected_idx):
    """
    Return the proc object corresponding to the UI's selected_idx.
    Recreates the grouping/sorting order used by draw_process_table:
      - groups by proc.group (empty -> "(ungrouped)")
      - groups sorted by group name
      - procs within group sorted by proc.name
    selected_idx is the index among processes (not counting group headers).
    """
    try:
        procs = list(controller.procs.values())
    except Exception:
        return None

    groups = {}
    for p in procs:
        g = getattr(p, "group", "") or "(ungrouped)"
        groups.setdefault(g, []).append(p)

    sorted_group_names = sorted(groups.keys())
    ordered = []
    for g in sorted_group_names:
        groups[g].sort(key=lambda x: getattr(x, "name", ""))
        ordered.extend(groups[g])

    if 0 <= selected_idx < len(ordered):
        return ordered[selected_idx]
    return None