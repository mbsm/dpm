DPM GUI Desktop Entry
======================

This directory contains a helper script to install a desktop entry so "DPM GUI" shows up in your Linux application launcher (GNOME/KDE/etc.).

Files:
- install-desktop-entry.sh: Installer script. It creates a .desktop entry and copies the icon.

Expected icon:
- Place your PNG icon at icons/dpm-gui.png (256x256 recommended). The script will copy it to the proper location.

Usage:
- User install (recommended):
  ./scripts/install-desktop-entry.sh

- System-wide install (requires sudo):
  ./scripts/install-desktop-entry.sh --system

What it does:
- Creates ~/.local/share/applications/dpm-gui.desktop (or /usr/local/share/applications for system mode)
- Copies icons/dpm-gui.png to the user icon theme (~/.local/share/icons/hicolor/256x256/apps)
- Updates the desktop database

Exec command:
- The desktop entry will execute: python3 /full/path/to/dpm-gui.py

Troubleshooting:
- If the icon does not appear immediately, try logging out/in or run:
  gtk-update-icon-cache ~/.local/share/icons/hicolor 2>/dev/null || true
- If the app does not launch, verify Python and PyQt5 are installed in your environment.
