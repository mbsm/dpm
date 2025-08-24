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

uninstall() {
    echo "Starting DPM uninstallation..."

    # 1. Stop and disable systemd service
    echo "Stopping and disabling dpm-node service..."
    systemctl stop dpm-node.service >/dev/null 2>&1 || echo "Service not running."
    systemctl disable dpm-node.service >/dev/null 2>&1 || echo "Service not enabled."
    rm -f /etc/systemd/system/dpm-node.service
    systemctl daemon-reload

    # 2. Remove desktop entry and icon
    echo "Removing desktop entry and icon..."
    rm -f "$DESKTOP_ENTRY_INSTALL_PATH/$DESKTOP_ENTRY_NAME"
    if command -v xdg-icon-resource &> /dev/null; then
        echo "Uninstalling icon using xdg-icon-resource..."
        xdg-icon-resource uninstall --size 256 "$DPM_ICON_NAME"
    else
        echo "xdg-icon-resource not found, removing icon manually."
        rm -f "$DPM_ICON_INSTALL_PATH/$DPM_ICON_NAME.png"
    fi

    if [ -x "$(command -v update-desktop-database)" ]; then
        update-desktop-database "$DESKTOP_ENTRY_INSTALL_PATH"
    fi

    # 3. Remove installed files and directories
    echo "Removing installed files..."
    rm -rf "$DPM_INSTALL_DIR"
    rm -rf "$DPM_CONFIG_DIR"
    rm -rf "$DPM_LOG_DIR"
    SUDO_USER_HOME=$(get_sudo_user_home)
    if [ -d "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME" ]; then
        echo "Removing save directory: $SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
        rm -rf "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    fi

    # 4. Remove user permissions file
    echo "Removing real-time permissions configuration..."
    rm -f /etc/security/limits.d/99-dpm-realtime.conf

    echo "DPM uninstallation completed successfully!"
    echo "Note: The user '$DPM_SERVICE_USER' was not removed."
    exit 0
}

# Function to get the home directory of the user who invoked sudo
get_sudo_user_home() {
    if [ -n "$SUDO_USER" ]; then
        getent passwd "$SUDO_USER" | cut -d: -f6
    else
        echo "$HOME"
    fi
}

# --- Installation Steps ---

install() {
    echo "Starting DPM installation..."

    # 1. Clean up previous installations
    echo "Cleaning up previous installations..."
    rm -rf "$DPM_INSTALL_DIR"
    rm -rf "$DPM_CONFIG_DIR"
    SUDO_USER_HOME=$(get_sudo_user_home)
    if [ -d "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME" ]; then
        rm -rf "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    fi

    # 2. Create directories
    echo "Creating necessary directories..."
    mkdir -p "$DPM_INSTALL_DIR"
    mkdir -p "$DPM_CONFIG_DIR"
    mkdir -p "$DPM_LOG_DIR"
    chown "$DPM_SERVICE_USER:$DPM_SERVICE_USER" "$DPM_LOG_DIR"
    mkdir -p "$SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"
    chown -R "$SUDO_USER:$SUDO_USER" "$SUDO_USER_HOME/.dpm"

    # 3. Copy project files and setup.py
    echo "Copying source files to $DPM_INSTALL_DIR..."
    cp -r "$DPM_SRC_DIR/src" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/setup.py" "$DPM_INSTALL_DIR/"
    cp "$DPM_SRC_DIR/dpm.yaml" "$DPM_CONFIG_DIR/dpm.yaml"
    # Copy other necessary files if any
    cp "$DPM_SRC_DIR/requirements.txt" "$DPM_INSTALL_DIR/"

    # 4. User and permissions setup
    echo "Setting up user '$DPM_SERVICE_USER'..."
    if ! id -u "$DPM_SERVICE_USER" >/dev/null 2>&1; then
        echo "Creating user '$DPM_SERVICE_USER'..."
        useradd -r -s /bin/false "$DPM_SERVICE_USER"
    fi

    # Grant real-time permissions to the user
    echo "Granting real-time permissions..."
    cat > /etc/security/limits.d/99-dpm-realtime.conf <<EOF
# Permissions for dpm-node service
$DPM_SERVICE_USER   soft    rtprio  99
$DPM_SERVICE_USER   hard    rtprio  99
EOF

    # 5. Systemd service setup
    echo "Setting up systemd service for dpm-node..."
    # Create a Python virtual environment
    python3 -m venv "$DPM_INSTALL_DIR/venv"
    "$DPM_INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$DPM_INSTALL_DIR/venv/bin/pip" install -r "$DPM_INSTALL_DIR/requirements.txt"
    
    # Install the DPM package using setup.py
    echo "Installing DPM package into virtual environment..."
    cd "$DPM_INSTALL_DIR"
    "$DPM_INSTALL_DIR/venv/bin/pip" install -e .

    # Create and install the service file
    cat > /etc/systemd/system/dpm-node.service <<EOF
[Unit]
Description=DPM Node Service
After=network-online.target
Wants=network-online.target

[Service]
User=$DPM_SERVICE_USER
Group=$DPM_SERVICE_USER
ExecStart=$DPM_INSTALL_DIR/venv/bin/python -m dpm.node.node
WorkingDirectory=$DPM_INSTALL_DIR
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    echo "Reloading systemd daemon and starting dpm-node service..."
    systemctl daemon-reload
    systemctl enable dpm-node.service
    systemctl start dpm-node.service

    echo "Checking dpm-node service status:"
    systemctl status dpm-node.service --no-pager

    # 6. Desktop entry for dpm-gui
    echo "Creating desktop entry for dpm-gui..."
    # Install icon
    if command -v xdg-icon-resource &> /dev/null; then
        echo "Installing icon using xdg-icon-resource..."
        xdg-icon-resource install --size 256 "$DPM_SRC_DIR/assets/icons/dpm-gui.png" "$DPM_ICON_NAME"
    else
        echo "xdg-icon-resource not found, installing icon manually."
        mkdir -p "$DPM_ICON_INSTALL_PATH"
        cp "$DPM_SRC_DIR/assets/icons/dpm-gui.png" "$DPM_ICON_INSTALL_PATH/$DPM_ICON_NAME.png"
    fi

    # Create desktop file
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

    # Update desktop database
    echo "Updating desktop database..."
    update-desktop-database "$DESKTOP_ENTRY_INSTALL_PATH"

    echo "DPM installation completed successfully!"
    echo "You can find the application in your system's menu."
    echo "Saved files will be stored in $SUDO_USER_HOME/$DPM_SAVE_DIR_NAME"

    exit 0
}

# --- Main script ---

case "$1" in
    install)
        install
        ;;
    uninstall)
        uninstall
        ;;
    *)
        echo "Usage: $0 {install|uninstall}"
        exit 1
        ;;
esac