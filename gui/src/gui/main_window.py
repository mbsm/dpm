from PyQt5.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QListWidget, QPushButton, QHBoxLayout, QLabel, QMessageBox
from PyQt5.QtCore import Qt
from .process_dialog import ProcessDialog
from .process_output import ProcessOutput

class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("DPM - Process Manager")
        self.setGeometry(100, 100, 900, 600)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.layout = QVBoxLayout()
        self.central_widget.setLayout(self.layout)

        self.hosts_label = QLabel("Hosts")
        self.layout.addWidget(self.hosts_label)

        self.hosts_list = QListWidget()
        self.layout.addWidget(self.hosts_list)

        self.processes_label = QLabel("Processes")
        self.layout.addWidget(self.processes_label)

        self.processes_list = QListWidget()
        self.layout.addWidget(self.processes_list)

        self.button_layout = QHBoxLayout()
        self.layout.addLayout(self.button_layout)

        self.start_button = QPushButton("Start Process")
        self.start_button.clicked.connect(self.start_process)
        self.button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Process")
        self.stop_button.clicked.connect(self.stop_process)
        self.button_layout.addWidget(self.stop_button)

        self.edit_button = QPushButton("Edit Process")
        self.edit_button.clicked.connect(self.edit_process)
        self.button_layout.addWidget(self.edit_button)

        self.view_output_button = QPushButton("View Output")
        self.view_output_button.clicked.connect(self.view_output)
        self.button_layout.addWidget(self.view_output_button)

        # initial population
        self.load_hosts()
        self.load_processes()

    def load_hosts(self):
        self.hosts_list.clear()
        # controller.hosts is a dict hostname -> host_info_t
        for host in self.controller.hosts.keys():
            self.hosts_list.addItem(host)

    def load_processes(self):
        self.processes_list.clear()
        # controller.procs is a dict proc_name -> proc_info object
        for proc in self.controller.procs.values():
            name = getattr(proc, "name", str(proc))
            self.processes_list.addItem(name)

    def _selected_host(self):
        item = self.hosts_list.currentItem()
        if item:
            return item.text()
        return None

    def _selected_proc(self):
        item = self.processes_list.currentItem()
        if item:
            return item.text()
        return None

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
        outputs = self.controller.proc_outputs
        msg = outputs.get(proc_name)
        output_text = ""
        if msg is not None:
            output_text = getattr(msg, "stdout", "") or getattr(msg, "output", "") or ""
        else:
            output_text = "(no output available)"
        dlg = ProcessOutput(proc_name, output_text, parent=self)
        dlg.exec_()