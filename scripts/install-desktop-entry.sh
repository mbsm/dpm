#!/usr/bin/env bash
set -euo pipefail

# System-wide installer for a desktop entry to launch dpm-gui
# Usage: sudo ./scripts/install-desktop-entry.sh [install|uninstall]
# This script only supports system-wide installs and must be run as root.

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo $0 [install|uninstall]"
  exit 1
fi

ACTION="install"
if [[ "${1:-}" == "install" || "${1:-}" == "uninstall" ]]; then
  ACTION="$1"
else
  echo "Usage: sudo $0 [install|uninstall]"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="dpm-gui.desktop"
APP_NAME="DPM GUI"
ICON_SRC="$REPO_ROOT/assets/icons/dpm-gui.png"
LAUNCHER_SYS="/usr/local/bin/dpm-gui"

DEST_DIR="/usr/local/share/applications"
ICON_DEST_DIR="/usr/local/share/icons/hicolor/256x256/apps"

mkdir -p "$DEST_DIR"
mkdir -p "$ICON_DEST_DIR"

if [[ "$ACTION" == "install" ]]; then
  # Create a stable wrapper in /usr/local/bin that runs the packaged GUI from /opt/dpm
  TMP_WRAPPER="$(mktemp)"
  cat >"$TMP_WRAPPER" <<'WRP'
#!/usr/bin/env bash
# Wrapper to run the DPM GUI from a system install under /opt/dpm
export PYTHONPATH=/opt/dpm
exec /usr/bin/env python3 -m dpm.gui.app "$@"
WRP
  install -m 0755 "$TMP_WRAPPER" "$LAUNCHER_SYS"
  rm -f "$TMP_WRAPPER"
  echo "Installed system wrapper to $LAUNCHER_SYS"

  # Install runtime files into /opt/dpm so the system wrapper can run without the repo
  INSTALL_PREFIX="/opt/dpm"
  SRC_DPM_DIR="$(realpath "$REPO_ROOT/src/dpm" 2>/dev/null || true)"
  SRC_DPM_MSGS_DIR="$(realpath "$REPO_ROOT/src/dpm_msgs" 2>/dev/null || true)"
  SRC_NODE_DIR="$(realpath "$REPO_ROOT/src/dpm/node" 2>/dev/null || true)"
  SRC_CONFIG="$REPO_ROOT/dpm.yaml"
  SRC_ASSETS_DIR="$REPO_ROOT/assets"

  mkdir -p "$INSTALL_PREFIX"
  if [[ -n "$SRC_DPM_DIR" && -d "$SRC_DPM_DIR" ]]; then
    echo "Copying dpm package to $INSTALL_PREFIX/dpm"
    cp -a "$SRC_DPM_DIR" "$INSTALL_PREFIX/dpm"
  else
    echo "Warning: $SRC_DPM_DIR not found; package dpm will not be installed to $INSTALL_PREFIX" >&2
  fi

  if [[ -n "$SRC_DPM_MSGS_DIR" && -d "$SRC_DPM_MSGS_DIR" ]]; then
    echo "Copying dpm_msgs to $INSTALL_PREFIX/dpm_msgs"
    cp -a "$SRC_DPM_MSGS_DIR" "$INSTALL_PREFIX/dpm_msgs"
  else
    echo "Warning: $SRC_DPM_MSGS_DIR not found; dpm_msgs will not be installed to $INSTALL_PREFIX" >&2
  fi

  # Also copy the node agent directory so the GUI can spawn a local node when needed
  if [[ -n "$SRC_NODE_DIR" && -d "$SRC_NODE_DIR" ]]; then
    echo "Copying node agent to $INSTALL_PREFIX/node"
    cp -a "$SRC_NODE_DIR" "$INSTALL_PREFIX/node"
  else
    echo "Warning: $SRC_NODE_DIR not found; node agent will not be installed to $INSTALL_PREFIX" >&2
  fi

  if [[ -f "$SRC_CONFIG" ]]; then
    echo "Copying config $SRC_CONFIG -> $INSTALL_PREFIX/dpm.yaml"
    cp "$SRC_CONFIG" "$INSTALL_PREFIX/dpm.yaml"
    chmod 644 "$INSTALL_PREFIX/dpm.yaml" || true
    # Also install a top-level /opt/dpm.yaml for services/GUI that expect /opt/dpm.yaml
    echo "Also installing top-level config -> /opt/dpm.yaml"
    cp "$SRC_CONFIG" "/opt/dpm.yaml"
    chmod 644 "/opt/dpm.yaml" || true
  else
    echo "Warning: $SRC_CONFIG not found; no config installed." >&2
  fi

  if [[ -d "$SRC_ASSETS_DIR" ]]; then
    echo "Copying assets to $INSTALL_PREFIX/assets"
    cp -a "$SRC_ASSETS_DIR" "$INSTALL_PREFIX/assets"
  fi

  # Ensure correct permissions
  chown -R root:root "$INSTALL_PREFIX"
  # Ensure top-level config ownership
  if [ -f "/opt/dpm.yaml" ]; then
    chown root:root "/opt/dpm.yaml"
  fi
  chmod -R u=rwX,go=rX "$INSTALL_PREFIX" || true

  # Copy icon if present and set Icon field accordingly
  ICON_FIELD="utilities-terminal"
  if [[ -f "$ICON_SRC" ]]; then
    install -m 0644 "$ICON_SRC" "$ICON_DEST_DIR/dpm-gui.png"
    ICON_FIELD="dpm-gui"
  else
    echo "Warning: Icon not found at $ICON_SRC. Using generic icon name: $ICON_FIELD" >&2
  fi

  # Write desktop entry
  TMP_DESKTOP="$(mktemp)"
  cat >"$TMP_DESKTOP" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
Comment=Distributed Process Manager GUI
Exec=$LAUNCHER_SYS
TryExec=$LAUNCHER_SYS
Icon=$ICON_FIELD
Terminal=false
Categories=Utility;Development;
StartupNotify=true
Keywords=process;manager;dpm;gui;
Path=$REPO_ROOT
DESKTOP

  install -m 0644 "$TMP_DESKTOP" "$DEST_DIR/$APP_ID"
  rm -f "$TMP_DESKTOP"

  # Update desktop database and icon cache (best effort)
  update-desktop-database >/dev/null 2>&1 || true
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache /usr/local/share/icons/hicolor >/dev/null 2>&1 || true
  fi

  echo "Installed $APP_NAME desktop entry to $DEST_DIR/$APP_ID"
  if [[ "$ICON_FIELD" == "dpm-gui" ]]; then
    echo "Icon installed to $ICON_DEST_DIR/dpm-gui.png"
  fi
  exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
  echo "Removing $APP_NAME desktop entry (system-wide)..."
  if test -f "$DEST_DIR/$APP_ID"; then
    rm -f "$DEST_DIR/$APP_ID"
    echo "Removed $DEST_DIR/$APP_ID"
  else
    echo "No desktop entry found at $DEST_DIR/$APP_ID"
  fi

  if test -f "$ICON_DEST_DIR/dpm-gui.png"; then
    rm -f "$ICON_DEST_DIR/dpm-gui.png"
    echo "Removed icon $ICON_DEST_DIR/dpm-gui.png"
  else
    echo "No icon found at $ICON_DEST_DIR/dpm-gui.png"
  fi

  if test -f "$LAUNCHER_SYS"; then
    rm -f "$LAUNCHER_SYS"
    echo "Removed system wrapper $LAUNCHER_SYS"
  else
    echo "No system wrapper found at $LAUNCHER_SYS"
  fi

  # Remove installed runtime and top-level config
  if [ -d "/opt/dpm" ]; then
    echo "Removing installed runtime at /opt/dpm"
    rm -rf "/opt/dpm"
    echo "Removed /opt/dpm"
  else
    echo "No installed runtime found at /opt/dpm"
  fi

  if [ -f "/opt/dpm.yaml" ]; then
    rm -f "/opt/dpm.yaml"
    echo "Removed /opt/dpm.yaml"
  else
    echo "No top-level config found at /opt/dpm.yaml"
  fi

  update-desktop-database >/dev/null 2>&1 || true
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache /usr/local/share/icons/hicolor >/dev/null 2>&1 || true
  fi

  echo "Uninstall completed."
  exit 0
fi

# Should not reach here
exit 1
