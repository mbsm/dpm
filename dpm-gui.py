#!/usr/bin/env python3
"""
Convenient launcher to run the PyQt5 GUI from the repo root.

Usage:
  python3 dpm-gui.py
"""
import os
import sys

# ensure the GUI src directory is on sys.path
GUI_SRC = os.path.join(os.path.dirname(__file__), "dpm-pyqt5-gui", "src")
if GUI_SRC not in sys.path:
    sys.path.insert(0, GUI_SRC)

# ensure controller package is importable when running from repo root
CONTROLLER_DIR = os.path.join(os.path.dirname(__file__), "controller")
if CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, CONTROLLER_DIR)

from main import main  # imports dpm-pyqt5-gui/src/main.py
if __name__ == "__main__":
    main()