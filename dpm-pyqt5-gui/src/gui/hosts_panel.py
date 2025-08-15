from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem

class HostsPanel(QWidget):
    def __init__(self, master):
        super().__init__()
        self.master = master
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        self.title_label = QLabel("Hosts Status")
        layout.addWidget(self.title_label)

        self.hosts_list = QListWidget()
        layout.addWidget(self.hosts_list)

        self.setLayout(layout)
        self.update_hosts()

    def update_hosts(self):
        self.hosts_list.clear()
        for host, status in self.master.hosts.items():
            item = QListWidgetItem(f"{host}: {'Online' if status['online'] else 'Offline'}")
            self.hosts_list.addItem(item)