#!/bin/bash
# filepath: /home/mbustos/dpm/agent/install_dpm_agent.sh

# Script to install/uninstall and enable/disable the dpm-agent systemd service

# --- Configuration ---
SERVICE_FILE_NAME="dpm-agent.service"
SOURCE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )" # Get script's directory
SOURCE_PATH="${SOURCE_DIR}/${SERVICE_FILE_NAME}"
DEST_PATH="/etc/systemd/system/${SERVICE_FILE_NAME}"
# --- End Configuration ---

# --- Functions ---

install_service() {
    echo "Installing ${SERVICE_FILE_NAME}..."

    # --- Install System Dependencies ---
    echo "Checking/Installing system dependencies (python3-psutil)..."
    # Check if apt-get is available
    if ! command -v apt-get &> /dev/null; then
        echo "ERROR: apt-get command not found. This script requires a Debian/Ubuntu based system to install dependencies."
        echo "Please install 'python3-psutil' manually."
        # Decide if you want to exit or continue without dependency check
        # exit 1 # Option: Exit if apt-get is not found
    else
        # Update package list
        echo "Updating package list (apt-get update)..."
        apt-get update
        if [ $? -ne 0 ]; then
            echo "WARNING: Failed to update package lists. Proceeding with install attempt anyway."
        fi

        # Install python3-psutil
        echo "Installing python3-psutil..."
        apt-get install -y python3-psutil
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to install python3-psutil using apt-get."
            echo "Please install it manually (e.g., 'sudo apt-get install python3-psutil') and retry."
            exit 1
        fi
        echo "System dependencies checked/installed."
    fi
    # --- End Dependency Install ---

    # Check if source service file exists
    if [ ! -f "${SOURCE_PATH}" ]; then
        echo "ERROR: Source service file not found at ${SOURCE_PATH}"
        exit 1
    fi

    # --- Ask user for agent path and build ExecStart ---
    DEFAULT_AGENT_DIR="${SOURCE_DIR}"
    echo ""
    echo "Detected agent directory: ${DEFAULT_AGENT_DIR}"
    read -r -p "Use this directory for WorkingDirectory and ExecStart? [Y/n]: " yn
    yn=${yn:-Y}
    case "$yn" in
        [Nn]*)
            read -r -p "Enter full path to the agent directory (must contain agent.py): " AGENT_DIR
            ;;
        *)
            AGENT_DIR="${DEFAULT_AGENT_DIR}"
            ;;
    esac

    # Validate agent directory
    if [ ! -d "${AGENT_DIR}" ] || [ ! -f "${AGENT_DIR}/agent.py" ]; then
        echo "ERROR: '${AGENT_DIR}' is not valid or agent.py not found."
        exit 1
    fi

    EXEC_START="/usr/bin/env python3 ${AGENT_DIR}/agent.py"
    echo "Using:"
    echo "  WorkingDirectory=${AGENT_DIR}"
    echo "  ExecStart=${EXEC_START}"
    echo ""

    # Create a temp copy of the unit and modify it
    TMP_UNIT="$(mktemp)"
    cp "${SOURCE_PATH}" "${TMP_UNIT}"

    # Replace or insert WorkingDirectory and ExecStart inside [Service] section
    # 1) Try to replace existing lines
    sed -i "s|^WorkingDirectory=.*$|WorkingDirectory=${AGENT_DIR}|g" "${TMP_UNIT}"
    sed -i "s|^ExecStart=.*$|ExecStart=${EXEC_START}|g" "${TMP_UNIT}"

    # 2) If not present, insert after [Service]
    if ! grep -q '^WorkingDirectory=' "${TMP_UNIT}"; then
        sed -i "/^\[Service\]/a WorkingDirectory=${AGENT_DIR}" "${TMP_UNIT}"
    fi
    if ! grep -q '^ExecStart=' "${TMP_UNIT}"; then
        sed -i "/^\[Service\]/a ExecStart=${EXEC_START}" "${TMP_UNIT}"
    fi

    echo "Preview of modified unit (first 30 lines):"
    head -n 30 "${TMP_UNIT}" | sed 's/^/  /'
    echo ""
    read -r -p "Proceed with installation? [Y/n]: " proceed
    proceed=${proceed:-Y}
    case "$proceed" in
        [Nn]*)
            echo "Aborted by user."
            rm -f "${TMP_UNIT}"
            exit 1
            ;;
    esac

    # Copy the modified unit into place
    echo "Copying modified unit to ${DEST_PATH}..."
    cp "${TMP_UNIT}" "${DEST_PATH}"
    rm -f "${TMP_UNIT}"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to copy service file."
        exit 1
    fi

    # Set correct permissions
    chmod 644 "${DEST_PATH}"

    # Reload systemd manager configuration
    echo "Reloading systemd daemon..."
    systemctl daemon-reload
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to reload systemd daemon."
        # Attempt to clean up
        rm -f "${DEST_PATH}"
        exit 1
    fi

    # Enable the service to start on boot
    echo "Enabling ${SERVICE_FILE_NAME} to start on boot..."
    systemctl enable "${SERVICE_FILE_NAME}"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to enable service."
        # Attempt to clean up
        rm -f "${DEST_PATH}"
        systemctl daemon-reload # Reload again after removing
        exit 1
    fi

    echo ""
    echo "${SERVICE_FILE_NAME} installed and enabled successfully."
    echo "You can start it now using: sudo systemctl start ${SERVICE_FILE_NAME}"
    echo "You can check its status using: sudo systemctl status ${SERVICE_FILE_NAME}"
}

uninstall_service() {
    echo "Uninstalling ${SERVICE_FILE_NAME}..."

    # Check if service file exists in systemd directory
    if [ ! -f "${DEST_PATH}" ]; then
        echo "INFO: Service file ${DEST_PATH} not found. Assuming already uninstalled."
        # Optionally check if service is active/enabled anyway, though unlikely without the file
        systemctl list-units --full -all | grep -q "${SERVICE_FILE_NAME}" && echo "WARNING: Service unit found active/loaded but file is missing. Attempting disable/stop."
    fi

    # Stop the service if it's running
    echo "Stopping service (if running)..."
    systemctl stop "${SERVICE_FILE_NAME}" # Ignore errors if not running

    # Disable the service
    echo "Disabling service..."
    systemctl disable "${SERVICE_FILE_NAME}"
    if [ $? -ne 0 ]; then
        # Don't exit on error here, maybe it was never enabled, still try to remove file
        echo "WARNING: Failed to disable service (maybe it was not enabled?)."
    fi

    # Remove the service file
    if [ -f "${DEST_PATH}" ]; then
        echo "Removing service file ${DEST_PATH}..."
        rm -f "${DEST_PATH}"
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to remove service file ${DEST_PATH}."
            # Even if removal fails, try reloading daemon
        fi
    fi

    # Reload systemd manager configuration
    echo "Reloading systemd daemon..."
    systemctl daemon-reload
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to reload systemd daemon."
        exit 1 # Exit here as system state might be inconsistent
    fi

    # Note: This script does NOT uninstall system packages (like python3-psutil) during uninstall.
    echo ""
    echo "${SERVICE_FILE_NAME} uninstalled successfully."
}

# --- Main Script ---

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root or using sudo."
  exit 1
fi

# Check for argument
if [ "$#" -ne 1 ]; then
    echo "Usage: sudo $0 [install|uninstall]"
    exit 1
fi

ACTION="$1"

case "$ACTION" in
    install)
        install_service
        ;;
    uninstall)
        uninstall_service
        ;;
    *)
        echo "Invalid action: ${ACTION}"
        echo "Usage: sudo $0 [install|uninstall]"
        exit 1
        ;;
esac

exit 0