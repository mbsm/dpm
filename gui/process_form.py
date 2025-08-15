from PyQt5 import QtWidgets, QtCore

class ProcessForm(QtWidgets.QWidget):
    def __init__(self, controller, process=None):
        super().__init__()
        self.controller = controller
        self.process = process
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Process Form")
        self.setGeometry(100, 100, 400, 300)

        layout = QtWidgets.QVBoxLayout()

        self.name_input = QtWidgets.QLineEdit(self)
        self.name_input.setPlaceholderText("Process Name")
        layout.addWidget(self.name_input)

        self.command_input = QtWidgets.QLineEdit(self)
        self.command_input.setPlaceholderText("Process Command")
        layout.addWidget(self.command_input)

        self.group_input = QtWidgets.QLineEdit(self)
        self.group_input.setPlaceholderText("Group")
        layout.addWidget(self.group_input)

        self.host_input = QtWidgets.QLineEdit(self)
        self.host_input.setPlaceholderText("Host")
        layout.addWidget(self.host_input)

        self.auto_restart_checkbox = QtWidgets.QCheckBox("Auto Restart", self)
        layout.addWidget(self.auto_restart_checkbox)

        self.realtime_checkbox = QtWidgets.QCheckBox("Realtime", self)
        layout.addWidget(self.realtime_checkbox)

        self.submit_button = QtWidgets.QPushButton("Submit", self)
        self.submit_button.clicked.connect(self.submit)
        layout.addWidget(self.submit_button)

        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.close)
        layout.addWidget(self.cancel_button)

        self.setLayout(layout)

        if self.process:
            self.load_process_data()

    def load_process_data(self):
        self.name_input.setText(self.process.name)
        self.command_input.setText(self.process.cmd)
        self.group_input.setText(getattr(self.process, "group", ""))
        self.host_input.setText(getattr(self.process, "hostname", ""))
        self.auto_restart_checkbox.setChecked(getattr(self.process, "auto_restart", False))
        self.realtime_checkbox.setChecked(getattr(self.process, "realtime", False))

    def submit(self):
        name = self.name_input.text().strip()
        command = self.command_input.text().strip()
        group = self.group_input.text().strip()
        host = self.host_input.text().strip()
        auto_restart = self.auto_restart_checkbox.isChecked()
        realtime = self.realtime_checkbox.isChecked()

        if not name or not command or not host:
            QtWidgets.QMessageBox.warning(self, "Input Error", "Please fill in all required fields.")
            return

        try:
            if self.process:
                old_name = getattr(self.process, "name", None)
                old_host = getattr(self.process, "hostname", host)
                if old_name:
                    self.controller.del_proc(old_name, old_host)
                self.controller.create_proc(name, command, group, host, auto_restart, realtime)
            else:
                self.controller.create_proc(name, command, group, host, auto_restart, realtime)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to submit process: {e}")
            return

        self.close()