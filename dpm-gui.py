#!/usr/bin/env python3
"""
Launcher: run the PyQt5 GUI from the repository root.

Usage:
  python3 dpm-gui.py
"""
import os
import sys

# Ensure repo root is on sys.path (useful if executed from other CWDs)
REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from PyQt5.QtWidgets import QApplication

# Import controller (package at repo_root/controller) and GUI modules (package gui)
from controller import Controller
from gui.main_window import MainWindow

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