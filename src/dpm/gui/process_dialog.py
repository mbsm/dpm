"""Dialog for creating or editing a process."""

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class ProcessDialog(QDialog):
    def __init__(self, client, proc=None):
        super().__init__()
        self.client = client
        self.proc = proc
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Process Management")
        self.setModal(True)

        layout = QVBoxLayout()

        self.form_layout = QFormLayout()
        self.name_input = QLineEdit()
        self.command_input = QLineEdit()
        self.group_input = QLineEdit()
        self.host_input = QLineEdit()
        self.auto_restart_checkbox = QCheckBox()
        self.realtime_checkbox = QCheckBox()
        self.isolated_checkbox = QCheckBox()
        self.work_dir_input = QLineEdit()
        self.work_dir_input.setPlaceholderText("/path/to/working/dir")
        self.cpuset_input = QLineEdit()
        self.cpuset_input.setPlaceholderText("e.g. 0,1,2")
        self.cpu_limit_input = QLineEdit()
        self.cpu_limit_input.setPlaceholderText("e.g. 1.5 (cores)")
        self.mem_limit_input = QLineEdit()
        self.mem_limit_input.setPlaceholderText("e.g. 1073741824 (bytes)")

        if self.proc:
            self.load_process_data()

        self.form_layout.addRow("Process Name:", self.name_input)
        self.form_layout.addRow("Proc Command:", self.command_input)
        self.form_layout.addRow("Group:", self.group_input)
        self.form_layout.addRow("Host:", self.host_input)
        self.form_layout.addRow("Auto Restart:", self.auto_restart_checkbox)
        self.form_layout.addRow("Realtime:", self.realtime_checkbox)
        self.form_layout.addRow("Isolated:", self.isolated_checkbox)
        self.form_layout.addRow("Working Dir:", self.work_dir_input)
        self.form_layout.addRow("CPU Set:", self.cpuset_input)
        self.form_layout.addRow("CPU Limit:", self.cpu_limit_input)
        self.form_layout.addRow("Mem Limit:", self.mem_limit_input)

        layout.addLayout(self.form_layout)

        self.button_box = QVBoxLayout()
        self.save_button = QPushButton("Save")
        self.cancel_button = QPushButton("Cancel")

        self.save_button.clicked.connect(self.save_process)
        self.cancel_button.clicked.connect(self.reject)

        self.button_box.addWidget(self.save_button)
        self.button_box.addWidget(self.cancel_button)

        layout.addLayout(self.button_box)

        self.setLayout(layout)

    def load_process_data(self):
        # use self.proc (not self.process)
        self.name_input.setText(getattr(self.proc, "name", "") or "")
        self.command_input.setText(getattr(self.proc, "exec_command", "") or "")
        self.group_input.setText(getattr(self.proc, "group", "") or "")
        self.host_input.setText(getattr(self.proc, "hostname", "") or "")
        self.auto_restart_checkbox.setChecked(
            bool(getattr(self.proc, "auto_restart", False))
        )
        self.realtime_checkbox.setChecked(bool(getattr(self.proc, "realtime", False)))
        self.isolated_checkbox.setChecked(bool(getattr(self.proc, "isolated", False)))
        self.work_dir_input.setText(getattr(self.proc, "work_dir", "") or "")
        self.cpuset_input.setText(getattr(self.proc, "cpuset", "") or "")
        cpu_limit = getattr(self.proc, "cpu_limit", 0.0) or 0.0
        self.cpu_limit_input.setText(str(cpu_limit) if cpu_limit > 0 else "")
        mem_limit = getattr(self.proc, "mem_limit", 0) or 0
        self.mem_limit_input.setText(str(mem_limit) if mem_limit > 0 else "")

    def save_process(self):
        name = self.name_input.text().strip()
        proc_command = self.command_input.text().strip()
        group = self.group_input.text().strip()
        host = self.host_input.text().strip()
        auto_restart = self.auto_restart_checkbox.isChecked()
        realtime = self.realtime_checkbox.isChecked()
        isolated = self.isolated_checkbox.isChecked()
        work_dir = self.work_dir_input.text().strip()
        cpuset = self.cpuset_input.text().strip()
        try:
            cpu_limit = float(self.cpu_limit_input.text().strip() or "0")
        except ValueError:
            cpu_limit = 0.0
        try:
            mem_limit = int(self.mem_limit_input.text().strip() or "0")
        except ValueError:
            mem_limit = 0

        if not name or not proc_command or not host:
            QMessageBox.warning(
                self,
                "Input Error",
                "Process name, proc command, and host are required.",
            )
            return

        try:
            if self.proc:
                old_host = getattr(self.proc, "hostname", host)
                old_name = getattr(self.proc, "name", None)
                if old_name:
                    self.client.stop_proc(old_name, old_host)
                    self.client.del_proc(old_name, old_host)
            self.client.create_proc(
                name, proc_command, group, host, auto_restart, realtime,
                work_dir=work_dir, cpuset=cpuset,
                cpu_limit=cpu_limit, mem_limit=mem_limit,
                isolated=isolated,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save process: {e}")
            return

        self.accept()
