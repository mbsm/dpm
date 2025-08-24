import os
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

# Import the application bits
from dpm.controller.controller import Controller  # type: ignore
from dpm.gui.main_window import MainWindow  # type: ignore


def main() -> None:
    # Allow running even if called outside the repo root
    app = QApplication(sys.argv)

    config_path = '/etc/dpm/dpm.yaml'
    controller = Controller(config_path)
    controller.start()

    window = MainWindow(controller)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
