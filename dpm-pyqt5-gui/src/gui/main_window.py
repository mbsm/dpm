from PyQt5.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QListWidget, QPushButton, QHBoxLayout, QLabel, QMessageBox
from PyQt5.QtCore import Qt

class MainWindow(QMainWindow):
    def __init__(self, dpm_master):
        super().__init__()
        self.dpm_master = dpm_master
        self.setWindowTitle("DPM - Process Manager")
        self.setGeometry(100, 100, 800, 600)

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

        self.load_hosts()
        self.load_processes()

    def load_hosts(self):
        self.hosts_list.clear()
        for host in self.dpm_master.hosts:
            self.hosts_list.addItem(host)

    def load_processes(self):
        self.processes_list.clear()
        for proc in self.dpm_master.procs:
            self.processes_list.addItem(proc.name)

    def start_process(self):
        selected_process = self.processes_list.currentItem()
        if selected_process:
            self.dpm_master.start_proc(selected_process.text())
            self.load_processes()
        else:
            QMessageBox.warning(self, "Warning", "No process selected.")

    def stop_process(self):
        selected_process = self.processes_list.currentItem()
        if selected_process:
            self.dpm_master.stop_proc(selected_process.text())
            self.load_processes()
        else:
            QMessageBox.warning(self, "Warning", "No process selected.")

    def edit_process(self):
        selected_process = self.processes_list.currentItem()
        if selected_process:
            # Logic to open the process edit dialog
            pass
        else:
            QMessageBox.warning(self, "Warning", "No process selected.")

    def view_output(self):
        selected_process = self.processes_list.currentItem()
        if selected_process:
            # Logic to open the process output display
            pass
        else:
            QMessageBox.warning(self, "Warning", "No process selected.")