from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFormLayout, QLineEdit, QCheckBox, QMessageBox
from PyQt5.QtCore import Qt

class ProcessDialog(QDialog):
    def __init__(self, controller, proc=None):
        super().__init__()
        self.controller = controller
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

        if self.proc:
            self.name_input.setText(self.proc.name)
            self.command_input.setText(self.proc.cmd)
            self.group_input.setText(getattr(self.proc, "group", ""))
            self.host_input.setText(getattr(self.proc, "hostname", ""))
            self.auto_restart_checkbox.setChecked(getattr(self.proc, "auto_restart", False))
            self.realtime_checkbox.setChecked(getattr(self.proc, "realtime", False))

        self.form_layout.addRow("Process Name:", self.name_input)
        self.form_layout.addRow("Command:", self.command_input)
        self.form_layout.addRow("Group:", self.group_input)
        self.form_layout.addRow("Host:", self.host_input)
        self.form_layout.addRow("Auto Restart:", self.auto_restart_checkbox)
        self.form_layout.addRow("Realtime:", self.realtime_checkbox)

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

    def save_process(self):
        name = self.name_input.text().strip()
        command = self.command_input.text().strip()
        group = self.group_input.text().strip()
        host = self.host_input.text().strip()
        auto_restart = self.auto_restart_checkbox.isChecked()
        realtime = self.realtime_checkbox.isChecked()

        if not name or not command or not host:
            QMessageBox.warning(self, "Input Error", "Process name, command, and host are required.")
            return

        try:
            if self.proc:
                # if editing, remove old proc and create updated one
                old_host = getattr(self.proc, "hostname", host)
                old_name = getattr(self.proc, "name", None)
                if old_name:
                    self.controller.del_proc(old_name, old_host)
                self.controller.create_proc(name, command, group, host, auto_restart, realtime)
            else:
                self.controller.create_proc(name, command, group, host, auto_restart, realtime)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save process: {e}")
            return

        self.accept()