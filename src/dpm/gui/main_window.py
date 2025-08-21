from PyQt5.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QListWidget, QListWidgetItem, QPushButton, QHBoxLayout, QLabel, QMessageBox, QAction, QFileDialog, QTreeWidget, QTreeWidgetItem, QMenu, QFrame, QApplication
from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtGui import QColor, QBrush, QFontMetrics, QPalette
from .process_dialog import ProcessDialog
from .process_output import ProcessOutput
from dpm.tui.io import save_all_process_specs, load_and_create
import os
import time

# TUI-style status mapping from single-letter state codes
STATE_NAME_MAP = {
    "T": "Ready",
    "R": "Running",
    "S": "Stopped",
    "F": "Failed",
    "K": "Killed",
    "E": "Exited",  # optional if your backend uses it
}

HOST_OFFLINE_THRESHOLD_SEC = 5

# Color palette (TUI-like)
COLOR_GREEN = QColor(46, 204, 113)   # Running / Yes / Low usage
COLOR_RED = QColor(231, 76, 60)      # Stopped / Failed / Killed / Offline / High usage
COLOR_YELLOW = QColor(241, 196, 15)  # Mixed / Medium usage
COLOR_GRAY = QColor(127, 140, 141)   # Ready / Exited / No

# Simple “card” widget for host stats
class HostCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HostCard")
        self.setFrameShape(QFrame.StyledPanel)
        # Theme applied dynamically
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(10, 8, 10, 8)
        self.v.setSpacing(4)
        self.title = QLabel("", self)
        self.title.setObjectName("HostTitle")
        self.status = QLabel("", self)
        self.status.setObjectName("StatLabel")
        self.mem = QLabel("", self)
        self.mem.setObjectName("StatLabel")
        self.mem.setTextFormat(Qt.RichText)   # color only the number
        self.cpu = QLabel("", self)
        self.cpu.setObjectName("StatLabel")
        self.cpu.setTextFormat(Qt.RichText)   # color only the number
        self.v.addWidget(self.title)
        self.v.addWidget(self.status)
        self.v.addWidget(self.mem)
        self.v.addWidget(self.cpu)
        self.set_theme(dark=False)  # default light theme

    def set_theme(self, dark: bool):
        if dark:
            self.setStyleSheet("""
                #HostCard {
                    border: 1px solid #444;
                    border-radius: 4px;
                    padding: 8px;
                    background: #2b2b2b;
                }
                #HostTitle { font-weight: 600; color: #ffffff; font-size: 12px; }
                #StatLabel { color: #dddddd; font-size: 10px; }
            """)
        else:
            self.setStyleSheet("""
                #HostCard {
                    border: 1px solid #000;       /* black border */
                    border-radius: 4px;
                    padding: 8px;
                    background: #ffffff;          /* white card */
                }
                #HostTitle { font-weight: 600; color: #000; font-size: 12px; }
                #StatLabel { color: #000; font-size: 10px; }  /* smaller stats text */
            """)

    def set_data(self, host: str, online: bool, cpu_pct: int, mem_pct: int, usage_color_fn):
        self.title.setText(host)
        self.status.setText("Online" if online else "Offline")
        self.status.setStyleSheet(f"color: {(COLOR_GREEN if online else COLOR_RED).name()}")

        mem_color = usage_color_fn(mem_pct).name()
        cpu_color = usage_color_fn(cpu_pct).name()
        # Color only the numbers using rich text
        self.mem.setText(f"Mem usage: <span style='color:{mem_color}'>{mem_pct}%</span>")
        self.cpu.setText(f"Cpu usage: <span style='color:{cpu_color}'>{cpu_pct}%</span>")

class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        # Keep modeless output windows alive (one per proc)
        self.output_windows = {}
        # Keep and reuse host cards to avoid flicker/resize jumps
        self._host_item_map = {}  # host -> (QListWidgetItem, HostCard)
        self.dark_mode = False
        self.setWindowTitle("DPM - Process Manager")
        self.setGeometry(100, 100, 900, 600)

        # Create a menu bar
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        # Black mode toggle
        self.black_action = QAction("&Black Mode", self, checkable=True)
        self.black_action.toggled.connect(self.toggle_black_mode)
        file_menu.addAction(self.black_action)
        file_menu.addSeparator()
        # Quit
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(lambda: QApplication.instance().quit())
        file_menu.addAction(quit_action)

        save_action = QAction("&Save As...", self)
        save_action.triggered.connect(self.save_all_processes)
        file_menu.addAction(save_action)

        load_action = QAction("&Load...", self)
        load_action.triggered.connect(self.load_processes_from_file)
        file_menu.addAction(load_action)

        node_menu = menu_bar.addMenu("&Node")
        spawn_action = QAction("&Spawn Local Node", self)
        spawn_action.triggered.connect(self.spawn_local_node)
        node_menu.addAction(spawn_action)

        stop_action = QAction("S&top Local Node", self)
        stop_action.triggered.connect(self.stop_local_node)
        node_menu.addAction(stop_action)

        # Process menu (replaces the New button)
        process_menu = menu_bar.addMenu("&Process")
        act_proc_new = QAction("&New...", self)
        act_proc_new.triggered.connect(self.new_process)
        process_menu.addAction(act_proc_new)
        act_proc_delete = QAction("&Delete...", self)
        act_proc_delete.triggered.connect(self.delete_process)
        process_menu.addAction(act_proc_delete)

        # Main layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        self.hosts_label = QLabel("Hosts")
        layout.addWidget(self.hosts_label)

        self.hosts_list = QListWidget()
        # Display hosts as cards in a wrapping grid
        self.hosts_list.setViewMode(self.hosts_list.IconMode)
        self.hosts_list.setResizeMode(self.hosts_list.Adjust)
        self.hosts_list.setMovement(self.hosts_list.Static)
        self.hosts_list.setSpacing(12)
        self.hosts_list.setWordWrap(True)
        # Fix each card’s allocated area; width will be recomputed to fit hostnames
        self._host_card_size = QSize(240, 96)  # initial; will auto-adjust
        self.hosts_list.setGridSize(self._host_card_size)
        layout.addWidget(self.hosts_list)

        self.processes_label = QLabel("Processes")
        layout.addWidget(self.processes_label)

        # Tree table: Group/Proc, Host, Status, CPU, MEM, Auto, Priority
        self.processes_tree = QTreeWidget()
        self.processes_tree.setColumnCount(7)
        self.processes_tree.setHeaderLabels(["Group/Proc", "Host", "Status", "CPU", "MEM (MB)", "Auto", "Priority"])
        self.processes_tree.setRootIsDecorated(True)
        self.processes_tree.setAlternatingRowColors(True)
        self.processes_tree.setColumnWidth(0, 300)
        self.processes_tree.setColumnWidth(1, 160)
        self.processes_tree.setColumnWidth(2, 120)
        self.processes_tree.setColumnWidth(3, 80)
        self.processes_tree.setColumnWidth(4, 90)
        self.processes_tree.setColumnWidth(5, 80)
        self.processes_tree.setColumnWidth(6, 80)
        # Right-click context menu on process tree
        self.processes_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.processes_tree.customContextMenuRequested.connect(self._show_process_context_menu)
        layout.addWidget(self.processes_tree)

        # No bottom buttons; use Process menu and right-click context menu
        #

        # after widgets created
        # initial population
        self.load_hosts()
        self.load_processes()
        # widen window to show all columns
        self._ensure_min_width()
        # ensure theme applied (light by default)
        self.apply_theme()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(1000)

    def _ensure_min_width(self):
        total = 0
        for i in range(self.processes_tree.columnCount()):
            total += self.processes_tree.columnWidth(i)
        # add some padding for tree indent, borders, and potential scrollbar
        desired = total + 120
        if self.minimumWidth() < desired:
            self.setMinimumWidth(desired)
        if self.width() < desired:
            self.resize(desired, self.height())

    def toggle_black_mode(self, enabled: bool):
        self.dark_mode = enabled
        self.apply_theme()

    def apply_theme(self):
        app = QApplication.instance()
        if self.dark_mode:
            # Dark palette
            pal = QPalette()
            pal.setColor(QPalette.Window, QColor("#1e1e1e"))
            pal.setColor(QPalette.WindowText, QColor("#e0e0e0"))
            pal.setColor(QPalette.Base, QColor("#1e1e1e"))
            pal.setColor(QPalette.AlternateBase, QColor("#252525"))
            pal.setColor(QPalette.ToolTipBase, QColor("#1e1e1e"))
            pal.setColor(QPalette.ToolTipText, QColor("#e0e0e0"))
            pal.setColor(QPalette.Text, QColor("#e0e0e0"))
            pal.setColor(QPalette.Button, QColor("#2a2a2a"))
            pal.setColor(QPalette.ButtonText, QColor("#e0e0e0"))
            pal.setColor(QPalette.BrightText, QColor("#ff5555"))
            pal.setColor(QPalette.Link, QColor("#4aa3ff"))
            pal.setColor(QPalette.Highlight, QColor("#264f78"))
            pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
            app.setPalette(pal)
            # Widgets specific
            self.processes_tree.setStyleSheet("QTreeWidget { background:#1e1e1e; color:#e0e0e0; }")
            self.hosts_list.setStyleSheet("QListWidget { background:#1e1e1e; }")
            # Update existing cards
            for _, card in self._host_item_map.values():
                card.set_theme(True)
        else:
            # Reset to default/light palette
            app.setPalette(QPalette())
            self.processes_tree.setStyleSheet("")
            self.hosts_list.setStyleSheet("")
            for _, card in self._host_item_map.values():
                card.set_theme(False)

    def save_all_processes(self):
        default_path = os.path.join("saved", "processes.yml")
        fname, _ = QFileDialog.getSaveFileName(self, "Save All Specs", default_path, "YAML Files (*.yml *.yaml)")
        if not fname:
            return
        try:
            written, skipped = save_all_process_specs(fname, self.controller, append=False)
            QMessageBox.information(self, "Success", f"Saved {written} specs, skipped {skipped}.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")

    def load_processes_from_file(self):
        default_path = os.path.join("saved", "processes.yml")
        fname, _ = QFileDialog.getOpenFileName(self, "Load Specs", default_path, "YAML Files (*.yml *.yaml)")
        if not fname:
            return
        try:
            created, errors = load_and_create(fname, self.controller)
            msg = f"Created: {len(created)}"
            if errors:
                msg += f", Errors: {len(errors)}"
            QMessageBox.information(self, "Load Complete", msg)
            self.load_processes() # Refresh the process list
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Load failed: {e}")

    def load_hosts(self):
        # Update or create cards without clearing to avoid flicker
        now = time.time()
        seen_hosts = set()
        for host, info in self.controller.hosts.items():
            ts_us = getattr(info, "timestamp", 0) or 0
            try:
                age = now - (float(ts_us) * 1e-6)
            except Exception:
                age = float("inf")
            offline = age > HOST_OFFLINE_THRESHOLD_SEC

            cpu_frac = 0.0 if offline else float(getattr(info, "cpu_usage", 0.0) or 0.0)
            try:
                mem_total = float(getattr(info, "mem_total", 0) or 0.0)
                mem_used = float(getattr(info, "mem_used", 0) or 0.0)
                mem_frac = (mem_used / mem_total) if mem_total else float(getattr(info, "mem_usage", 0.0) or 0.0)
            except Exception:
                mem_frac = float(getattr(info, "mem_usage", 0.0) or 0.0)

            cpu_pct = max(0, min(100, int(round(cpu_frac * 100))))
            mem_pct = max(0, min(100, int(round(mem_frac * 100))))

            seen_hosts.add(host)
            tup = self._host_item_map.get(host)
            if tup is None:
                # Create once
                item = QListWidgetItem()
                item.setData(Qt.UserRole, host)
                item.setSizeHint(self._host_card_size)
                card = HostCard()
                # Width will be set after we recompute the grid size
                self.hosts_list.addItem(item)
                self.hosts_list.setItemWidget(item, card)
                self._host_item_map[host] = (item, card)
            else:
                item, card = tup
            # Update content only
            card.set_data(host, online=not offline, cpu_pct=cpu_pct, mem_pct=mem_pct, usage_color_fn=self._usage_color)

        # Remove cards for hosts no longer present
        stale = [h for h in self._host_item_map.keys() if h not in seen_hosts]
        for h in stale:
            item, _card = self._host_item_map.pop(h)
            row = self.hosts_list.row(item)
            if row >= 0:
                self.hosts_list.takeItem(row)

        # Recompute and apply grid width to just fit the (longest) hostname
        self._recompute_host_grid_width(sorted(seen_hosts))

    # --- helpers ---
    def _recompute_host_grid_width(self, host_names):
        if not self._host_item_map:
            return
        # Use the title font from any existing card for accurate metrics
        any_card = next(iter(self._host_item_map.values()))[1]
        fm = QFontMetrics(any_card.title.font())
        longest = max((fm.horizontalAdvance(h) for h in host_names), default=0)

        # Compute padding: layout (10+10) + frame padding (8+8) + borders (1+1) + small fudge
        padding = 10 + 10 + 8 + 8 + 1 + 1 + 8  # = 46 px
        new_w = max(160, longest + padding)    # enforce a sensible minimum

        # Keep the existing height; derive from any card sizeHint if you prefer dynamic
        new_size = QSize(new_w, self._host_card_size.height())

        if new_size != self._host_card_size:
            self._host_card_size = new_size
            self.hosts_list.setGridSize(new_size)
            for item, card in self._host_item_map.values():
                item.setSizeHint(new_size)
                # Slightly narrower than grid cell so the border doesn’t clip
                card.setFixedWidth(new_w - 6)

    def load_processes(self):
        # Save the expansion state of groups before clearing the tree
        expansion_state = {}
        for i in range(self.processes_tree.topLevelItemCount()):
            item = self.processes_tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if data and data.get("type") == "group":
                group_name = data.get("name")
                if group_name:
                    expansion_state[group_name] = item.isExpanded()

        self.processes_tree.clear()
        groups = {}
        for p in self.controller.procs.values():
            groups.setdefault(p.group or "(ungrouped)", []).append(p)

        for group_name, procs in sorted(groups.items(), key=lambda kv: kv[0].lower()):
            count = len(procs)
            g_status, g_cpu_frac, g_mem_mb, g_auto, g_host = self._aggregate_group_stats(procs)
            g_prio = self._group_priority_str(procs)
            group_cpu_str = f"{int(round(g_cpu_frac * 100)):d}%"
            group_mem_str = f"{int(round(g_mem_mb))}"
            # Note: Host column is index 1
            group_item = QTreeWidgetItem([f"[{count}] {group_name}", g_host, g_status, group_cpu_str, group_mem_str, g_auto, g_prio])
            group_item.setFirstColumnSpanned(False)
            group_item.setData(0, Qt.UserRole, {"type": "group", "name": group_name})
            # Colorize group row (Status and Auto only)
            group_item.setForeground(2, QBrush(self._status_color(g_status)))
            group_item.setForeground(5, QBrush(self._auto_color(g_auto)))
            self.processes_tree.addTopLevelItem(group_item)

            # Restore the saved expansion state for this group
            # Default to expanded for new groups
            is_expanded = expansion_state.get(group_name, True)
            group_item.setExpanded(is_expanded)

            for proc in sorted(procs, key=lambda p: p.name.lower()):
                status = self._proc_status(proc)
                cpu = float(getattr(proc, "cpu", 0.0) or 0.0)
                cpu_str = f"{cpu*100:.1f}%"
                mem_mb = self._mem_mb(proc)
                auto = "Yes" if getattr(proc, "auto_restart", False) else "No"
                host_name = getattr(proc, "hostname", "") or ""
                prio_str = self._proc_priority(proc)
                # Order: name, host, status, cpu, mem, auto, priority
                child = QTreeWidgetItem([proc.name, host_name, status, cpu_str, f"{int(round(mem_mb))}", auto, prio_str])
                child.setData(0, Qt.UserRole, {"type": "proc", "name": proc.name, "host": host_name})
                # Colorize process row (Status and Auto only)
                child.setForeground(2, QBrush(self._status_color(status)))
                child.setForeground(5, QBrush(self._auto_color(auto)))
                group_item.addChild(child)

        # self.processes_tree.expandAll() # This is the line causing the issue
        # self._ensure_min_width() # No longer needed here

    def _proc_priority(self, proc) -> str:
        val = getattr(proc, "priority", None)
        if val is None:
            val = getattr(proc, "prio", None)
        if val is None:
            return ""
        try:
            return str(int(val))
        except Exception:
            return str(val)
    
    def _group_priority_str(self, procs) -> str:
        vals = []
        for p in procs:
            v = getattr(p, "priority", None)
            if v is None:
                v = getattr(p, "prio", None)
            if v is not None:
                try:
                    v = int(v)
                except Exception:
                    pass
                vals.append(v)
        if not vals:
            return ""
        unique = set(vals)
        return str(next(iter(unique))) if len(unique) == 1 else "Mixed"

    def _ensure_min_width(self):
        total = 0
        for i in range(self.processes_tree.columnCount()):
            total += self.processes_tree.columnWidth(i)
        # add some padding for tree indent, borders, and potential scrollbar
        desired = total + 120
        if self.minimumWidth() < desired:
            self.setMinimumWidth(desired)
        if self.width() < desired:
            self.resize(desired, self.height())

    def refresh_all(self):
        sel_host = self._selected_host()
        # preserve selected proc name if any
        sel_proc_name = None
        cur_item = self.processes_tree.currentItem()
        if cur_item:
            d = cur_item.data(0, Qt.UserRole)
            if isinstance(d, dict) and d.get("type") == "proc":
                sel_proc_name = d.get("name")
        # reload
        self.load_hosts()
        self.load_processes()
        # restore selections
        if sel_host:
            for i in range(self.hosts_list.count()):
                it = self.hosts_list.item(i)
                if it.data(Qt.UserRole) == sel_host:
                    self.hosts_list.setCurrentItem(it)
                    break
        if sel_proc_name:
            # search tree for a child with matching name
            top_count = self.processes_tree.topLevelItemCount()
            for i in range(top_count):
                top = self.processes_tree.topLevelItem(i)
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child.text(0) == sel_proc_name:
                        self.processes_tree.setCurrentItem(child)
                        break

    def start_process(self):
        proc_name = self._selected_proc()
        host_name = self._selected_host()
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        if not host_name:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        try:
            self.controller.start_proc(proc_name, host_name)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start process: {e}")

    def stop_process(self):
        proc_name = self._selected_proc()
        host_name = self._selected_host()
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        if not host_name:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        try:
            self.controller.stop_proc(proc_name, host_name)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to stop process: {e}")

    def edit_process(self):
        proc_name = self._selected_proc()
        host_name = self._selected_host()
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        proc = self.controller.procs.get(proc_name)
        dlg = ProcessDialog(self.controller, proc)
        if dlg.exec_():
            # refresh lists after an edit/create
            self.load_processes()

    def view_output(self):
        proc_name = self._selected_proc()
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        self._open_output_window(proc_name)

    # Context menu helper
    def _view_output_direct(self, proc_name):
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        self._open_output_window(proc_name)

    def _open_output_window(self, proc_name: str):
        # Reuse existing window if still alive
        w = self.output_windows.get(proc_name)
        if w is not None:
            try:
                if w.isVisible():
                    w.raise_()
                    w.activateWindow()
                    return
            except RuntimeError:
                # Stale reference; remove and recreate
                self.output_windows.pop(proc_name, None)

        # Optional initial text (stdout + stderr)
        init_text = ""
        try:
            msg = getattr(self.controller, "proc_outputs", {}).get(proc_name)
        except Exception:
            msg = None
        if msg is not None:
            out = getattr(msg, "stdout", None) or getattr(msg, "output", None) or getattr(msg, "text", None) or ""
            err = getattr(msg, "stderr", None) or getattr(msg, "err", None) or ""
            init_text = out
            if err:
                if init_text and not init_text.endswith("\n"):
                    init_text += "\n"
                init_text += "[stderr]\n" + err

        # Create modeless window that self-updates from controller
        w = ProcessOutput(proc_name, init_text, self.controller, None)
        w.setWindowModality(Qt.NonModal)
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        self.output_windows[proc_name] = w
        w.destroyed.connect(lambda _: self.output_windows.pop(proc_name, None))
        w.show()

    # --- Context-menu direct helpers ---
    def _start_proc_direct(self, proc_name: str, host_name: str = None):
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        host = host_name or self._selected_host()
        if not host:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        try:
            self.controller.start_proc(proc_name, host)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start {proc_name}@{host}: {e}")

    def _stop_proc_direct(self, proc_name: str, host_name: str = None):
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        host = host_name or self._selected_host()
        if not host:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        try:
            self.controller.stop_proc(proc_name, host)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to stop {proc_name}@{host}: {e}")

    def _edit_proc_direct(self, proc_name: str):
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        proc = self.controller.procs.get(proc_name)
        dlg = ProcessDialog(self.controller, proc)
        if dlg.exec_():
            self.load_processes()

    def _delete_proc_direct(self, proc_name: str, host_name: str = None):
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        host = host_name or self._selected_host()
        if not host:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        if QMessageBox.question(self, "Confirm", f"Delete process '{proc_name}'?") != QMessageBox.Yes:
            return
        try:
            self.controller.del_proc(proc_name, host)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete process: {e}")

    def new_process(self):
        dlg = ProcessDialog(self.controller, None)  # creation mode
        if dlg.exec_():
            self.load_processes()

    def delete_process(self):
        proc_name = self._selected_proc()
        if not proc_name:
            QMessageBox.warning(self, "Warning", "No process selected.")
            return
        host_name = self._selected_host()
        if not host_name:
            QMessageBox.warning(self, "Warning", "No host selected.")
            return
        if QMessageBox.question(self, "Confirm", f"Delete process '{proc_name}'?") != QMessageBox.Yes:
            return
        try:
            self.controller.del_proc(proc_name, host_name)
            self.load_processes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete process: {e}")

    def spawn_local_node(self):
        try:
            # TODO: call shared helper, e.g., utils.local_node.spawn()
            QMessageBox.information(self, "Node", "Spawned local node.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to spawn local node: {e}")

    def stop_local_node(self):
        try:
            # TODO: call shared helper, e.g., utils.local_node.stop_last()
            QMessageBox.information(self, "Node", "Stopped local node.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to stop local node: {e}")

    def _proc_status(self, proc) -> str:
        """Best-effort status string from the process object."""
        try:
            s = getattr(proc, "status", None) or getattr(proc, "state", None) or ""
            if isinstance(s, str):
                s = s.strip()
                if len(s) == 1:
                    mapped = STATE_NAME_MAP.get(s.upper())
                    if mapped:
                        return mapped
                if s:
                    return s.capitalize()
            running = getattr(proc, "running", None)
            if isinstance(running, bool):
                return "Running" if running else "Stopped"
        except Exception:
            pass
        return "Ready"

    def _mem_mb(self, proc) -> float:
        """Return memory usage in MB. Tries various common fields."""
        try:
            v = getattr(proc, "mem_rss", None)  # kB from LCM
            if v is not None:
                return float(v) / 1024.0
            for name in ("mem_mb", "memory_mb", "rss_mb"):
                v = getattr(proc, name, None)
                if v is not None:
                    return float(v)
            for name in ("mem_bytes", "memory_bytes", "rss_bytes", "rss"):
                v = getattr(proc, name, None)
                if v is not None:
                    return float(v) / (1024.0 * 1024.0)
            v = getattr(proc, "mem", None)
            if v is not None:
                v = float(v)
                return v / (1024.0 * 1024.0) if v > 4096.0 else v
        except Exception:
            pass
        return 0.0

    def _status_color(self, status_str: str) -> QColor:
        s = (status_str or "").lower()
        if s == "running":
            return COLOR_GREEN
        if s in ("stopped", "failed", "killed", "error"):
            return COLOR_RED
        if s == "mixed":
            return COLOR_YELLOW
        if s in ("ready",) or s.startswith("exited"):
            return COLOR_GRAY
        return COLOR_GRAY

    def _auto_color(self, auto_str: str) -> QColor:
        s = (auto_str or "").lower()
        if s == "yes":
            return COLOR_GREEN
        if s == "mixed":
            return COLOR_YELLOW
        return COLOR_GRAY

    def _usage_color(self, pct: int) -> QColor:
        """Green < 40%, Yellow 40–70%, Red > 70%."""
        if pct < 40:
            return COLOR_GREEN
        if pct <= 70:
            return COLOR_YELLOW
        return COLOR_RED

    # Context menu on the process tree
    def _show_process_context_menu(self, pos):
        item = self.processes_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, dict):
            return

        menu = QMenu(self)

        if data.get("type") == "proc":
            proc_name = data.get("name")
            host_name = data.get("host") or self._selected_host()

            act_start = QAction("Start", self)
            act_start.triggered.connect(lambda: self._start_proc_direct(proc_name, host_name))
            menu.addAction(act_start)

            act_stop = QAction("Stop", self)
            act_stop.triggered.connect(lambda: self._stop_proc_direct(proc_name, host_name))
            menu.addAction(act_stop)

            act_edit = QAction("Edit", self)
            act_edit.triggered.connect(lambda: self._edit_proc_direct(proc_name))
            menu.addAction(act_edit)

            act_view = QAction("View Output", self)
            act_view.triggered.connect(lambda: self._view_output_direct(proc_name))
            menu.addAction(act_view)

            act_delete = QAction("Delete...", self)
            act_delete.triggered.connect(lambda: self._delete_proc_direct(proc_name, host_name))
            menu.addAction(act_delete)
        else:
            # Group context menu
            group_name = data.get("name", "")
            act_start_all = QAction("Start All", self)
            act_start_all.triggered.connect(lambda: self._start_group(group_name))
            menu.addAction(act_start_all)

            act_stop_all = QAction("Stop All", self)
            act_stop_all.triggered.connect(lambda: self._stop_group(group_name))
            menu.addAction(act_stop_all)

            act_view_all = QAction("View Output (All)", self)
            act_view_all.triggered.connect(lambda: self._view_group_outputs(group_name))
            menu.addAction(act_view_all)

        menu.exec_(self.processes_tree.viewport().mapToGlobal(pos))

    # --- Group helpers ---
    def _procs_in_group(self, group_name: str):
        try:
            return [p for p in self.controller.procs.values() if (getattr(p, "group", "") or "(ungrouped)") == group_name]
        except Exception:
            return []

    def _start_group(self, group_name: str):
        procs = self._procs_in_group(group_name)
        if not procs:
            QMessageBox.information(self, "Start All", f"No processes found in group '{group_name}'.")
            return
        selected_host = self._selected_host()
        failures = []
        missing_host = []
        for p in procs:
            host = getattr(p, "hostname", "") or selected_host
            if not host:
                missing_host.append(p.name)
                continue
            try:
                self.controller.start_proc(p.name, host)
            except Exception as e:
                failures.append(f"{p.name}@{host}: {e}")
        self.load_processes()
        if missing_host:
            QMessageBox.warning(self, "Start All", f"No host for: {', '.join(missing_host)}")
        if failures:
            QMessageBox.critical(self, "Start All", "Some failed:\n" + "\n".join(failures))

    def _stop_group(self, group_name: str):
        procs = self._procs_in_group(group_name)
        if not procs:
            QMessageBox.information(self, "Stop All", f"No processes found in group '{group_name}'.")
            return
        selected_host = self._selected_host()
        failures = []
        missing_host = []
        for p in procs:
            host = getattr(p, "hostname", "") or selected_host
            if not host:
                missing_host.append(p.name)
                continue
            try:
                self.controller.stop_proc(p.name, host)
            except Exception as e:
                failures.append(f"{p.name}@{host}: {e}")
        self.load_processes()
        if missing_host:
            QMessageBox.warning(self, "Stop All", f"No host for: {', '.join(missing_host)}")
        if failures:
            QMessageBox.critical(self, "Stop All", "Some failed:\n" + "\n".join(failures))

    def _view_group_outputs(self, group_name: str):
        procs = self._procs_in_group(group_name)
        if not procs:
            QMessageBox.information(self, "View Output (All)", f"No processes found in group '{group_name}'.")
            return
        for p in procs:
            try:
                self._open_output_window(p.name)
            except Exception:
                pass

    # --- Selection helpers ---
    def _selected_host(self):
        it = self.hosts_list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _selected_proc(self):
        item = self.processes_tree.currentItem()
        if not item:
            return None
        data = item.data(0, Qt.UserRole)
        if isinstance(data, dict) and data.get("type") == "proc":
            return data.get("name")
        return None

    def _aggregate_group_stats(self, procs):
        """Return (status, avg_cpu_frac, total_mem_mb, auto_str, host_str)."""
        if not procs:
            return ("Ready", 0.0, 0.0, "No", "")

        # Group Status with TUI-like rules:
        # - Running if ALL are running
        # - Ready   if ALL are ready
        # - Mixed   otherwise
        def _is_running(p):
            st_char = getattr(p, "state", None)
            if isinstance(st_char, str) and st_char.upper() == "R":
                return True
            return self._proc_status(p).lower() == "running"

        def _is_ready(p):
            st_char = getattr(p, "state", None)
            if isinstance(st_char, str) and st_char.upper() == "T":
                return True
            return self._proc_status(p).lower() == "ready"

        all_running = all(_is_running(p) for p in procs)
        all_ready = all(_is_ready(p) for p in procs)
        g_status = "Running" if all_running else ("Ready" if all_ready else "Mixed")

        # CPU: average of available cpu fractions (0..1)
        cpu_vals = []
        for p in procs:
            try:
                c = getattr(p, "cpu", None)
                if c is None:
                    c = getattr(p, "cpu_usage", None)
                if c is not None:
                    cpu_vals.append(float(c))
            except Exception:
                pass
        g_cpu_frac = (sum(cpu_vals) / len(cpu_vals)) if cpu_vals else 0.0

        # MEM: sum of per-proc MB
        mem_vals = []
        for p in procs:
            try:
                mem_vals.append(float(self._mem_mb(p)))
            except Exception:
                pass
        g_mem_mb = sum(mem_vals) if mem_vals else 0.0

        # Auto: Yes/No/Mixed based on auto_restart
        autos = [bool(getattr(p, "auto_restart", False)) for p in procs]
        if all(autos):
            g_auto = "Yes"
        elif any(autos):
            g_auto = "Mixed"
        else:
            g_auto = "No"

        # Host: single host if uniform, else "(multiple)"
        hosts = {getattr(p, "hostname", "") or "" for p in procs}
        g_host = next(iter(hosts)) if len(hosts) == 1 else "(multiple)"

        return (g_status, g_cpu_frac, g_mem_mb, g_auto, g_host)

    # --- Per-process helpers used by the table ---
    def _proc_status(self, proc) -> str:
        """Best-effort status string from the process object."""
        try:
            s = getattr(proc, "status", None) or getattr(proc, "state", None) or ""
            if isinstance(s, str):
                s = s.strip()
                if len(s) == 1:
                    mapped = STATE_NAME_MAP.get(s.upper())
                    if mapped:
                        return mapped
                if s:
                    return s.capitalize()
            running = getattr(proc, "running", None)
            if isinstance(running, bool):
                return "Running" if running else "Stopped"
        except Exception:
            pass
        return "Ready"

    def _mem_mb(self, proc) -> float:
        """Return memory usage in MB. Tries various common fields."""
        try:
            v = getattr(proc, "mem_rss", None)  # kB from LCM
            if v is not None:
                return float(v) / 1024.0
            for name in ("mem_mb", "memory_mb", "rss_mb"):
                v = getattr(proc, name, None)
                if v is not None:
                    return float(v)
            for name in ("mem_bytes", "memory_bytes", "rss_bytes", "rss"):
                v = getattr(proc, name, None)
                if v is not None:
                    return float(v) / (1024.0 * 1024.0)
            v = getattr(proc, "mem", None)
            if v is not None:
                v = float(v)
                return v / (1024.0 * 1024.0) if v > 4096.0 else v
        except Exception:
            pass
        return 0.0

    def _status_color(self, status_str: str) -> QColor:
        s = (status_str or "").lower()
        if s == "running":
            return COLOR_GREEN
        if s in ("stopped", "failed", "killed", "error"):
            return COLOR_RED
        if s == "mixed":
            return COLOR_YELLOW
        if s in ("ready",) or s.startswith("exited"):
            return COLOR_GRAY
        return COLOR_GRAY

    def _auto_color(self, auto_str: str) -> QColor:
        s = (auto_str or "").lower()
        if s == "yes":
            return COLOR_GREEN
        if s == "mixed":
            return COLOR_YELLOW
        return COLOR_GRAY

    def _usage_color(self, pct: int) -> QColor:
        """Green < 40%, Yellow 40–70%, Red > 70%."""
        if pct < 40:
            return COLOR_GREEN
        if pct <= 70:
            return COLOR_YELLOW
        return COLOR_RED
