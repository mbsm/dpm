import curses
import time

def draw_hosts_panel(win, hosts, threshold=5):
    """
    One-row-per-host with htop-style CPU and MEM bars on the same row.

    Title is drawn over the top border (like a framed title in Qt).
    Layout per row:
      <hostname(40)>  cpu [||||||||||||||||||||    51%]  mem [||||||||||||||||||||    12%]
    """
    # init colors
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_WHITE, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_GREEN, -1)
    except Exception:
        pass

    win.erase()
    win.box()
    height, width = win.getmaxyx()

    # Title drawn on the box border (over the top) so it looks like a frame title
    try:
        win.attron(curses.A_BOLD)
        win.addstr(0, 2, " HOSTS ")
        win.attroff(curses.A_BOLD)
    except Exception:
        pass

    current_time = time.time()
    # Start drawing host rows from row 1 (immediately below the top border)
    y_pos = 1

    hostname_w = 40
    cpu_label_w = 4   # "cpu "
    cpu_bar_w = 20
    cpu_pct_w = 8
    mem_label_w = 4   # "mem "
    mem_bar_w = 20
    mem_pct_w = 8

    for hostname, host_info in hosts.items():
        if y_pos >= height - 1:
            break

        ts = getattr(host_info, "timestamp", 0)
        try:
            age = current_time - (ts * 1e-6)
        except Exception:
            age = float("inf")
        offline = age > threshold

        cpu_frac = 0.0 if offline else float(getattr(host_info, "cpu_usage", 0.0) or 0.0)
        cpu_frac = max(0.0, min(1.0, cpu_frac))
        mem_frac = 0.0
        try:
            mem_total = getattr(host_info, "mem_total", 0) or 0
            mem_used = getattr(host_info, "mem_used", 0) or 0
            if mem_total:
                mem_frac = max(0.0, min(1.0, float(mem_used) / float(mem_total)))
        except Exception:
            mem_frac = 0.0

        x_pos = 1
        try:
            name_display = f"{hostname[:hostname_w-1]:<{hostname_w}}"
            if offline:
                win.addstr(y_pos, x_pos, name_display, curses.color_pair(2))
            else:
                win.addstr(y_pos, x_pos, name_display)
        except Exception:
            pass
        x_pos += hostname_w + 1

        # CPU label + bracket + bars
        try:
            win.addstr(y_pos, x_pos, "cpu ")
        except Exception:
            pass
        x_pos += cpu_label_w
        try:
            win.addstr(y_pos, x_pos, "[")
        except Exception:
            pass
        x_pos += 1

        cpu_filled = int(round(cpu_frac * cpu_bar_w))
        for i in range(cpu_bar_w):
            try:
                if i < cpu_filled:
                    rel = (i + 1) * 100.0 / max(1, cpu_bar_w)
                    if rel <= 50:
                        color = curses.color_pair(4)
                    elif rel <= 80:
                        color = curses.color_pair(3)
                    else:
                        color = curses.color_pair(2)
                    if offline:
                        color = curses.color_pair(2)
                    win.addstr(y_pos, x_pos + i, "|", color)
                else:
                    win.addstr(y_pos, x_pos + i, " ")
            except Exception:
                pass
        x_pos += cpu_bar_w

        try:
            pct_str = f"{int(cpu_frac*100):3d}%"
            pct_field = f"{pct_str:>{cpu_pct_w-1}}"
            win.addstr(y_pos, x_pos, f"{pct_field}]",
                       curses.color_pair(2) if offline else curses.A_NORMAL)
        except Exception:
            pass
        x_pos += cpu_pct_w + 2

        # MEM
        if x_pos + mem_label_w + mem_bar_w + mem_pct_w + 2 < width - 1:
            try:
                win.addstr(y_pos, x_pos, "mem ")
            except Exception:
                pass
            x_pos += mem_label_w
            try:
                win.addstr(y_pos, x_pos, "[")
            except Exception:
                pass
            x_pos += 1
            mem_filled = int(round(mem_frac * mem_bar_w))
            for i in range(mem_bar_w):
                try:
                    if i < mem_filled:
                        win.addstr(y_pos, x_pos + i, "|", curses.color_pair(4))
                    else:
                        win.addstr(y_pos, x_pos + i, " ")
                except Exception:
                    pass
            x_pos += mem_bar_w
            try:
                mem_pct = f"{int(mem_frac*100):3d}%"
                mem_field = f"{mem_pct:>{mem_pct_w-1}}"
                win.addstr(y_pos, x_pos, f"{mem_field}]")
            except Exception:
                pass

        y_pos += 1

    win.refresh()

def draw_process_table(win, procs, selected_idx=0):
    """
    Draw processes grouped by 'group' (htop-like tree view).
    Restored simpler selection styling (reverse) to avoid hiding rows.
    """
    win.erase()
    win.box()

    # Normalize to list/dict
    if isinstance(procs, dict):
        proc_list = list(procs.values())
    else:
        try:
            proc_list = list(procs)
        except Exception:
            proc_list = []

    height, width = win.getmaxyx()

    try:
        win.attron(curses.A_BOLD)
        win.addstr(0, 2, " PROCESSES ")
        win.attroff(curses.A_BOLD)
    except Exception:
        pass

    header_row = 1
    sep_row = 2
    try:
        win.attron(curses.A_BOLD)
        win.addstr(header_row, 2, "Group/Proc")
        win.addstr(header_row, 38, "Status")
        win.addstr(header_row, 48, "CPU")
        win.addstr(header_row, 58, "MEM")
        win.addstr(header_row, 68, "Auto")
        win.attroff(curses.A_BOLD)
    except Exception:
        pass

    try:
        win.hline(sep_row, 1, curses.ACS_HLINE, width - 2)
    except Exception:
        pass

    # Build groups
    groups = {}
    for p in proc_list:
        g = getattr(p, "group", "") or "(ungrouped)"
        groups.setdefault(g, []).append(p)

    sorted_group_names = sorted(groups.keys())
    for g in sorted_group_names:
        groups[g].sort(key=lambda x: getattr(x, "name", ""))

    # Build display rows
    display_rows = []
    proc_pos = 0
    selected_display_idx = 0
    for g in sorted_group_names:
        procs_in_g = groups[g]
        agg_cpu = 0.0
        agg_mem = 0.0
        count = 0
        states = []
        autos = []
        for p in procs_in_g:
            try:
                agg_cpu += float(getattr(p, "cpu", 0.0) or 0.0)
            except Exception:
                pass
            try:
                agg_mem += float(getattr(p, "mem_rss", 0) or 0.0)
            except Exception:
                pass
            states.append(getattr(p, "state", ""))
            autos.append(bool(getattr(p, "auto_restart", getattr(p, "restart", False))))
            count += 1

        if count == 0:
            group_status = "None"
        else:
            all_running = all(s == "R" for s in states)
            none_running = all(s != "R" for s in states)
            if all_running:
                group_status = "Running"
            elif none_running:
                group_status = "Stopped"
            else:
                group_status = "Mixed"

        if all(autos):
            group_auto = "Yes"
        elif any(autos):
            group_auto = "Mixed"
        else:
            group_auto = "No"

        meta = {"cpu": agg_cpu, "mem": agg_mem, "count": count, "status": group_status, "auto": group_auto}
        display_rows.append(("group", g, meta))

        for p in procs_in_g:
            display_rows.append(("proc", p))
            if proc_pos == selected_idx:
                selected_display_idx = len(display_rows) - 1
            proc_pos += 1

    total_display = len(display_rows)
    max_rows = height - (sep_row + 2)
    if max_rows < 1:
        win.refresh()
        return

    start_idx = 0
    if isinstance(selected_display_idx, int) and total_display > max_rows:
        start_idx = max(0, min(selected_display_idx - max_rows + 1, total_display - max_rows))

    row_no = 0
    state_names = {"T": "Ready", "R": "Running", "F": "Failed", "K": "Killed"}
    for disp_idx in range(start_idx, min(total_display, start_idx + max_rows)):
        row = sep_row + 1 + row_no
        try:
            entry = display_rows[disp_idx]
            if entry[0] == "group":
                _, gname, meta = entry
                label = f"[{meta['count']}] {gname}"
                try:
                    win.attron(curses.A_BOLD)
                    win.addstr(row, 2, f"{label[:36]:36}")
                    # Status column aligns with header at col 38
                    win.addstr(row, 38, f"{meta['status'][:7]:7}")
                    # CPU column at 48 (percentage)
                    cpu_pct = int(round(meta["cpu"] * 100))
                    win.addstr(row, 48, f"{cpu_pct:5d}%")
                    # MEM column at 58 (integer)
                    mem_val = int(round(meta["mem"]))
                    win.addstr(row, 58, f"{mem_val:6d}")
                    # Auto column at 68
                    win.addstr(row, 68, f"{meta['auto'][:5]:5}")
                    win.attroff(curses.A_BOLD)
                except Exception:
                    pass
            else:
                _, proc = entry
                is_selected = (disp_idx == selected_display_idx)
                if is_selected:
                    sel_attr = curses.A_REVERSE
                else:
                    sel_attr = curses.A_NORMAL

                pname = getattr(proc, "name", "") or ""
                try:
                    win.addstr(row, 4, f"{pname[:32]:32}", sel_attr)
                except Exception:
                    pass

                status = state_names.get(getattr(proc, "state", ""), "Unknown")
                try:
                    if getattr(proc, "state", "") == "R":
                        attr = curses.color_pair(4)
                        if is_selected:
                            attr |= sel_attr
                        win.addstr(row, 38, f"{status:7}", attr)
                    elif getattr(proc, "state", "") in ("F", "K"):
                        attr = curses.color_pair(2)
                        if is_selected:
                            attr |= sel_attr
                        win.addstr(row, 38, f"{status:7}", attr)
                    else:
                        win.addstr(row, 38, f"{status:7}", sel_attr)
                except Exception:
                    pass

                try:
                    cpu = float(getattr(proc, "cpu", 0.0) or 0.0)
                    win.addstr(row, 48, f"{cpu*100:5.1f}%", sel_attr)
                except Exception:
                    try:
                        win.addstr(row, 48, "  -  ", sel_attr)
                    except Exception:
                        pass

                try:
                    mem = getattr(proc, "mem_rss", 0)
                    win.addstr(row, 58, f"{mem:6}", sel_attr)
                except Exception:
                    try:
                        win.addstr(row, 58, "   - ", sel_attr)
                    except Exception:
                        pass

                try:
                    auto = getattr(proc, "auto_restart", getattr(proc, "restart", False))
                    win.addstr(row, 68, "Yes" if auto else "No ", sel_attr)
                except Exception:
                    pass
        except Exception:
            pass

        row_no += 1

    win.refresh()
