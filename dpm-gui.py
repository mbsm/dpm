#!/usr/bin/env python3
"""
Launcher: run the GUI from the repository root.

Usage:
  python3 dpm-gui.py
"""
import os
import sys

# Ensure repo root is on sys.path so 'gui' and 'controller' packages are importable
ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui.main import main

if __name__ == "__main__":
    main()