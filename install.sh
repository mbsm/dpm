#!/bin/bash

# Exit on any error
set -e

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo"
  exit 1
fi

# --- Configuration ---
DPM_SRC_DIR="$(pwd)"
DPM_INSTALL_DIR="/opt/dpm"
DPM_CONFIG_DIR="/etc/dpm"
DPM_LOG_DIR="/var/log/dpm"
DPM_SAVE_DIR_NAME=".dpm/save"
DPM_SERVICE_USER="dms_user"
DPM_ICON_NAME="dpm-gui"
DPM_ICON_INSTALL_PATH="/usr/share/icons/hicolor/256x256/apps"
DESKTOP_ENTRY_NAME="dpm-gui.desktop"
DESKTOP_ENTRY_INSTALL_PATH="/usr/share/applications"

# --- Functions ---

get_sudo_user_home() {
    if [ -n "$SUDO_USER" ]; then
        getent passwd "$SUDO_USER" | cut -d: -f6
    else
        echo "$HOME"
    fi
}

usage() {
    echo "Usage: $0 {install|uninstall} [service|gui|both]"
    echo "  install service   Install/update node service only"
    echo "  install gui       Install/update GUI desktop integration only"
    echo "  install both      Install/update both service and GUI (default)"
    echo "  uninstall service Remove node service only"
    echo "  uninstall gui     Remove GUI desktop integration only"
    echo "  uninstall both    Remove both service and GUI (default)"
}

validate_target() {
    case "$1" in
        service|gui|both)
            ;;
        *)
            echo "Invalid target: $1"
            usage
            exit 1
            ;;
    esac
}

prepare_runtime() {
    target="$1"
    req_file="requirements.txt"

    case "$target" in
        service)
            req_file="requirements-service.txt"
            ;;
        gui)
            req_file="requirements-gui.txt"
            ;;
        both)
            req_file="requirements.txt"
            ;;
    esac

    echo "Preparing DPM runtime in $DPM_INSTALL_DIR..."
    mkdir -p "$DPM_INSTALL_DIR"
    mkdir -p "$DPM_CONFIG_DIR"

    cp -r "$DPM_SRC_DIR/src" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/setup.py" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/requirements.txt" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/requirements-service.txt" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/requirements-gui.txt" "$DPM_INSTALL_DIR/"

    if [ ! -f "$DPM_CONFIG_DIR/dpm.yaml" ]; then
        cp "$DPM_SRC_DIR/dpm.yaml" "$DPM_CONFIG_DIR/dpm.yaml"
    else
        echo "Config exists at $DPM_CONFIG_DIR/dpm.yaml; leaving it unchanged."
    fi

    python3 -m venv "$DPM_INSTALL_DIR/venv"
    "$DPM_INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$DPM_INSTALL_DIR/venv/bin/pip" install -r "$DPM_INSTALL_DIR/$req_file"

    echo "Installing DPM package into virtual environment..."
    cd "$DPM_INSTALL_DIR"
    "$DPM_INSTALL_DIR/venv/bin/pip" install -e .
}

install_service_component() {
    echo "Installing service component..."

    if ! id -u "$DPM_SERVICE_USER" >/dev/null 2>&1; then
        echo "Creating user '$DPM_SERVICE_USER'..."
        useradd -r -s /bin/false "$DPM_SERVICE_USER"
    fi

    # Grant GPU access (required on Jetson/NVIDIA for CUDA child processes)
    for grp in video render; do
        if getent group "$grp" >/dev/null 2>&1; then
            usermod -a -G "$grp" "$DPM_SERVICE_USER"
            echo "Added '$DPM_SERVICE_USER' to group '$grp'."
        fi
    done

    mkdir -p "$DPM_LOG_DIR"
    chown "$DPM_SERVICE_USER:$DPM_SERVICE_USER" "$DPM_LOG_DIR"

    cat > /etc/security/limits.d/99-dpm-realtime.conf <<EOF
# Note: PAM limits often do NOT apply to systemd services.
# Kept for interactive/foreground runs.
$DPM_SERVICE_USER   soft    rtprio  99
$DPM_SERVICE_USER   hard    rtprio  99
EOF

    cat > /etc/systemd/system/dpm-node.service <<EOF
[Unit]
Description=DPM Node Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$DPM_SERVICE_USER
Group=$DPM_SERVICE_USER
WorkingDirectory=$DPM_INSTALL_DIR

# Ensure node reads standard config location
Environment=DPM_CONFIG=/etc/dpm/dpm.yaml

# journald logging
StandardOutput=journal
StandardError=journal

# Run via installed console script (robust against module path changes)
ExecStart=$DPM_INSTALL_DIR/venv/bin/dpm-node

Restart=always
RestartSec=2

# Realtime permissions for systemd service
LimitRTPRIO=99
AmbientCapabilities=CAP_SYS_NICE
CapabilityBoundingSet=CAP_SYS_NICE

# Basic hardening
NoNewPrivileges=true
PrivateTmp=true

# Allow reading/executing binaries under /home/* while keeping it non-writable
ProtectHome=read-only
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable dpm-node.service
    systemctl restart dpm-node.service
    systemctl status dpm-node.service --no-pager
}

install_gui_component() {
    echo "Installing GUI component..."
    SUDO_USER_HOME=$(get_sudo_user_home)
    mkdir -p "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    if [ -n "$SUDO_USER" ]; then
        chown -R "$SUDO_USER:$SUDO_USER" "$SUDO_USER_HOME/.dpm" || true
    fi

    if command -v xdg-icon-resource >/dev/null 2>&1; then
        xdg-icon-resource install --size 256 "$DPM_SRC_DIR/assets/icons/dpm-gui.png" "$DPM_ICON_NAME"
    else
        mkdir -p "$DPM_ICON_INSTALL_PATH"
        cp "$DPM_SRC_DIR/assets/icons/dpm-gui.png" "$DPM_ICON_INSTALL_PATH/$DPM_ICON_NAME.png"
    fi

    cat > "$DESKTOP_ENTRY_INSTALL_PATH/$DESKTOP_ENTRY_NAME" <<EOF
[Desktop Entry]
Name=DPM GUI
Comment=Distributed Process Manager
Exec=$DPM_INSTALL_DIR/venv/bin/python -m dpm.gui.main
Icon=$DPM_ICON_NAME
Path=$DPM_INSTALL_DIR
Type=Application
Categories=System;
EOF

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_ENTRY_INSTALL_PATH"
    fi
}

uninstall_service_component() {
    echo "Removing service component..."
    systemctl stop dpm-node.service >/dev/null 2>&1 || echo "Service not running."
    systemctl disable dpm-node.service >/dev/null 2>&1 || echo "Service not enabled."
    rm -f /etc/systemd/system/dpm-node.service
    systemctl daemon-reload
    rm -f /etc/security/limits.d/99-dpm-realtime.conf
    rm -rf "$DPM_LOG_DIR"
}

uninstall_gui_component() {
    echo "Removing GUI component..."
    rm -f "$DESKTOP_ENTRY_INSTALL_PATH/$DESKTOP_ENTRY_NAME"
    if command -v xdg-icon-resource >/dev/null 2>&1; then
        xdg-icon-resource uninstall --size 256 "$DPM_ICON_NAME" || true
    else
        rm -f "$DPM_ICON_INSTALL_PATH/$DPM_ICON_NAME.png" || true
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_ENTRY_INSTALL_PATH" || true
    fi

    SUDO_USER_HOME=$(get_sudo_user_home)
    if [ -d "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME" ]; then
        rm -rf "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    fi
}

uninstall() {
    target="$1"
    echo "Starting DPM uninstallation target=$target..."

    if [ "$target" = "service" ] || [ "$target" = "both" ]; then
        uninstall_service_component
    fi

    if [ "$target" = "gui" ] || [ "$target" = "both" ]; then
        uninstall_gui_component
    fi

    if [ "$target" = "both" ]; then
        echo "Removing shared runtime in $DPM_INSTALL_DIR..."
        rm -rf "$DPM_INSTALL_DIR"
    fi

    echo "DPM uninstallation completed successfully."
    echo "Note: The user '$DPM_SERVICE_USER' was not removed."
    exit 0
}

install() {
    target="$1"
    echo "Starting DPM installation target=$target..."

    prepare_runtime "$target"

    if [ "$target" = "service" ] || [ "$target" = "both" ]; then
        install_service_component
    fi

    if [ "$target" = "gui" ] || [ "$target" = "both" ]; then
        install_gui_component
        SUDO_USER_HOME=$(get_sudo_user_home)
        echo "You can find the application in your system's menu."
        echo "Saved files will be stored in $SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    fi

    echo "DPM installation completed successfully."
    exit 0
}

# --- Main script ---
ACTION="$1"
TARGET="${2:-both}"

validate_target "$TARGET"

case "$ACTION" in
    install)
        install "$TARGET"
        ;;
    uninstall)
        uninstall "$TARGET"
        ;;
    *)
        usage
        exit 1
        ;;
esac