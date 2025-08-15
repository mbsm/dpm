from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem

class HostsPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
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
        # controller.hosts is dict hostname -> host_info_t
        for host, info in self.controller.hosts.items():
            # show simple online indicator and ip/cpu if available
            ip = getattr(info, "ip", None)
            cpu = getattr(info, "cpu_usage", None)
            cpu_str = f" CPU:{int(cpu*100)}%" if cpu is not None else ""
            ip_str = f" {ip}" if ip else ""
            item = QListWidgetItem(f"{host}:{ip_str}{cpu_str} - Online")
            self.hosts_list.addItem(item)