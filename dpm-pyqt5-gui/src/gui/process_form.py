from PyQt5 import QtWidgets, QtCore

class ProcessForm(QtWidgets.QWidget):
    def __init__(self, master, process=None):
        super().__init__()
        self.master = master
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
        self.group_input.setText(self.process.group)
        self.host_input.setText(self.process.hostname)
        self.auto_restart_checkbox.setChecked(self.process.auto_restart)
        self.realtime_checkbox.setChecked(self.process.realtime)

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

        if self.process:
            self.master.update_process(self.process, name, command, group, host, auto_restart, realtime)
        else:
            self.master.create_process(name, command, group, host, auto_restart, realtime)

        self.close()