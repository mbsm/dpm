#!/usr/bin/env python3
import sys
import os
from PyQt5.QtWidgets import QApplication

# Ensure repo root is importable (should be when launching from repo root)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from controller import Controller
from .main_window import MainWindow

def main():
    config_path = os.path.join(REPO_ROOT, "dpm.yaml")
    app = QApplication(sys.argv)
    controller = Controller(config_path)
    controller.start()
    window = MainWindow(controller)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()