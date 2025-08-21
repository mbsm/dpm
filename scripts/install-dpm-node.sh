#!/bin/bash
# Installer for dpm-node.service (system-only)
# Usage: sudo ./scripts/install-dpm-node.sh [install|uninstall]
set -euo pipefail

SERVICE_FILE_NAME="dpm-node.service"
# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# Default service source in the repo (packaging/systemd)
DEFAULT_SERVICE_SRC="$(realpath "${SCRIPT_DIR}/../packaging/systemd/${SERVICE_FILE_NAME}")"
# Default source agent directory in the repo (fixed migrated layout)
REPO_AGENT_DIR="$(realpath "${SCRIPT_DIR}/../src/dpm/node")"
if [ ! -d "${REPO_AGENT_DIR}" ] || [ ! -f "${REPO_AGENT_DIR}/node.py" ]; then
  echo "ERROR: Repository node directory not found at ${REPO_AGENT_DIR} or missing node.py"
  echo "Expected location: ${SCRIPT_DIR}/../src/dpm/node"
  exit 1
fi

# Target install directory for the agent
INSTALL_PREFIX="/opt/dpm"
INSTALL_AGENT_DIR="${INSTALL_PREFIX}/node"
DEST_PATH="/etc/systemd/system/${SERVICE_FILE_NAME}"

prompt_yesno() {
  local prompt="$1"
  # Always prompt interactively
  read -r -p "${prompt} [Y/n]: " ans
  ans=${ans:-Y}
  case "$ans" in
    [Nn]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Only system installs supported

# Arg parsing: action only (no -y/--yes)
if [ "$#" -lt 1 ]; then
  echo "Usage: $0 [install|uninstall]"
  exit 1
fi

action="$1"
shift || true
if [ "$#" -gt 0 ]; then
  echo "Warning: extra arguments ignored" >&2
fi

# Require root for system install/uninstall
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo $0 ${action}"
  exit 1
fi

SYSTEMCTL_CMD="systemctl"

install_service() {
  echo "Installing ${SERVICE_FILE_NAME} system-wide..."

  if [ ! -f "${DEFAULT_SERVICE_SRC}" ]; then
    echo "Service file not found at ${DEFAULT_SERVICE_SRC}"
    read -r -p "Enter full path to the service file to use: " svc_src
    DEFAULT_SERVICE_SRC="${svc_src}"
  fi

  if [ ! -f "${DEFAULT_SERVICE_SRC}" ]; then
    echo "ERROR: Service file still not found. Aborting."
    exit 1
  fi

  # Ensure agent source exists in repo
  if [ ! -d "${REPO_AGENT_DIR}" ] || [ ! -f "${REPO_AGENT_DIR}/node.py" ]; then
    echo "ERROR: Repository node directory not found or missing node.py: ${REPO_AGENT_DIR}"
    exit 1
  fi

  echo "Will install agent from: ${REPO_AGENT_DIR} -> ${INSTALL_AGENT_DIR}"
  if [ -d "${INSTALL_PREFIX}" ]; then
    if ! prompt_yesno "${INSTALL_PREFIX} already exists. Overwrite with repo contents?"; then
      echo "Aborted by user."; exit 1
    fi
    rm -rf "${INSTALL_PREFIX}"
  fi

  mkdir -p "${INSTALL_PREFIX}"
  # Copy the node directory contents into the installation prefix
  cp -a "${REPO_AGENT_DIR}" "${INSTALL_PREFIX}/node"
  # Also copy top-level packages so Python imports work (dpm and dpm_msgs)
  SRC_DPM_DIR="$(realpath "${SCRIPT_DIR}/../src/dpm")"
  SRC_DPM_MSGS_DIR="$(realpath "${SCRIPT_DIR}/../src/dpm_msgs")"

  if [ -d "${SRC_DPM_DIR}" ]; then
    echo "Copying package ${SRC_DPM_DIR} -> ${INSTALL_PREFIX}/dpm"
    cp -a "${SRC_DPM_DIR}" "${INSTALL_PREFIX}/dpm"
  else
    echo "Warning: ${SRC_DPM_DIR} not found; dpm package will not be installed to ${INSTALL_PREFIX}" >&2
  fi

  if [ -d "${SRC_DPM_MSGS_DIR}" ]; then
    echo "Copying package ${SRC_DPM_MSGS_DIR} -> ${INSTALL_PREFIX}/dpm_msgs"
    cp -a "${SRC_DPM_MSGS_DIR}" "${INSTALL_PREFIX}/dpm_msgs"
  else
    echo "Warning: ${SRC_DPM_MSGS_DIR} not found; dpm_msgs will not be installed to ${INSTALL_PREFIX}" >&2
  fi

  # Copy configuration file dpm.yaml into the install prefix if present
  REPO_CONFIG="$(realpath "${SCRIPT_DIR}/../dpm.yaml" 2>/dev/null || true)"
  if [ -f "${REPO_CONFIG}" ]; then
    echo "Installing config ${REPO_CONFIG} -> ${INSTALL_PREFIX}/dpm.yaml"
    cp "${REPO_CONFIG}" "${INSTALL_PREFIX}/dpm.yaml"
    chmod 640 "${INSTALL_PREFIX}/dpm.yaml" || true
  else
    echo "Warning: dpm.yaml not found at repo root (${SCRIPT_DIR}/../dpm.yaml). Skipping config install." >&2
  fi

  # Ensure correct permissions and create runtime user
  if ! id -u dpmuser >/dev/null 2>&1; then
    echo "Creating system user 'dpmuser'..."
    # create system user with no login and its own group
    useradd -r -U -s /usr/sbin/nologin -M -d /nonexistent dpmuser
  fi
  # Make dpmuser the owner of the installed files so the service can run as that user
  chown -R dpmuser:dpmuser "${INSTALL_PREFIX}"

  EXEC_START="/usr/bin/env python3 ${INSTALL_AGENT_DIR}/node.py"

  TMP_UNIT="$(mktemp)"
  cp "${DEFAULT_SERVICE_SRC}" "${TMP_UNIT}"

  # Ensure PYTHONPATH is set so the service finds packages under ${INSTALL_PREFIX}
  if ! grep -q '^Environment=PYTHONPATH=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a Environment=PYTHONPATH=${INSTALL_PREFIX}" "${TMP_UNIT}"
  else
    sed -i "s|^Environment=PYTHONPATH=.*$|Environment=PYTHONPATH=${INSTALL_PREFIX}|g" "${TMP_UNIT}"
  fi

  sed -i "s|^WorkingDirectory=.*$|WorkingDirectory=${INSTALL_AGENT_DIR}|g" "${TMP_UNIT}" || true
  sed -i "s|^ExecStart=.*$|ExecStart=${EXEC_START}|g" "${TMP_UNIT}" || true
  # Ensure the service runs as dpmuser
  sed -i "s|^User=.*$|User=dpmuser|g" "${TMP_UNIT}" || true
  if ! grep -q '^User=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a User=dpmuser" "${TMP_UNIT}"
  fi

  # Add capability settings to allow nice/priority adjustments
  if ! grep -q '^AmbientCapabilities=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a AmbientCapabilities=CAP_SYS_NICE" "${TMP_UNIT}"
  fi
  if ! grep -q '^CapabilityBoundingSet=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a CapabilityBoundingSet=CAP_SYS_NICE" "${TMP_UNIT}"
  fi

  if ! grep -q '^WorkingDirectory=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a WorkingDirectory=${INSTALL_AGENT_DIR}" "${TMP_UNIT}"
  fi
  if ! grep -q '^ExecStart=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a ExecStart=${EXEC_START}" "${TMP_UNIT}"
  fi

  echo "Preview (first 40 lines):"
  head -n 40 "${TMP_UNIT}" | sed 's/^/  /'
  if ! prompt_yesno "Proceed with installation?"; then
    rm -f "${TMP_UNIT}"
    echo "Aborted."
    exit 1
  fi

  # Backup existing unit file (timestamped) before replacing
  if [ -f "${DEST_PATH}" ]; then
    TS="$(date +%Y%m%d-%H%M%S)"
    BACKUP_PATH="${DEST_PATH}.bak.${TS}"
    echo "Existing unit found at ${DEST_PATH}; creating backup ${BACKUP_PATH}"
    cp "${DEST_PATH}" "${BACKUP_PATH}"
    echo "Backup created."
  fi

  cp "${TMP_UNIT}" "${DEST_PATH}"
  rm -f "${TMP_UNIT}"
  chmod 644 "${DEST_PATH}"

  # Ask whether to enable/start the service permanently (enable makes it persistent)
  if prompt_yesno "Enable the service to start on boot and start it now?"; then
    ${SYSTEMCTL_CMD} daemon-reload
    ${SYSTEMCTL_CMD} enable "${SERVICE_FILE_NAME}"
    echo "Attempting to start ${SERVICE_FILE_NAME}..."
    if ${SYSTEMCTL_CMD} start "${SERVICE_FILE_NAME}"; then
      # Wait briefly for service to become active
      wait_secs=10
      while [ $wait_secs -gt 0 ]; do
        if ${SYSTEMCTL_CMD} is-active --quiet "${SERVICE_FILE_NAME}"; then
          echo "${SERVICE_FILE_NAME} is running. Recent logs:"
          journalctl -u "${SERVICE_FILE_NAME}" -n 20 --no-pager | sed 's/^/  /'
          return 0
        fi
        sleep 1
        wait_secs=$((wait_secs - 1))
      done

      echo "Service did not reach 'active' state within timeout. Check logs for details:"
      echo "  journalctl -u ${SERVICE_FILE_NAME} -f"
      return 1
    else
      echo "Failed to start ${SERVICE_FILE_NAME}. Check logs with:" 
      echo "  journalctl -u ${SERVICE_FILE_NAME} -n 200 --no-pager"
      return 1
    fi
  else
    ${SYSTEMCTL_CMD} daemon-reload
    echo "Service installed at ${DEST_PATH} but not enabled or started."
    echo "To enable and start later: sudo systemctl enable --now ${SERVICE_FILE_NAME}"
    return 0
  fi
}

uninstall_service() {
  echo "Uninstalling ${SERVICE_FILE_NAME}..."
  ${SYSTEMCTL_CMD} stop "${SERVICE_FILE_NAME}" 2>/dev/null || true
  ${SYSTEMCTL_CMD} disable "${SERVICE_FILE_NAME}" 2>/dev/null || true
  if [ -f "${DEST_PATH}" ]; then
    rm -f "${DEST_PATH}"
    echo "Removed unit file ${DEST_PATH}"
  else
    echo "Unit file not present at ${DEST_PATH}"
  fi
  ${SYSTEMCTL_CMD} daemon-reload

  if [ -d "${INSTALL_PREFIX}" ]; then
    if prompt_yesno "Remove installed files at ${INSTALL_PREFIX}?"; then
      rm -rf "${INSTALL_PREFIX}"
      echo "Removed ${INSTALL_PREFIX}"
    else
      echo "Left ${INSTALL_PREFIX} in place."
    fi
  else
    echo "No installed files at ${INSTALL_PREFIX}"
  fi

  echo "Uninstallation complete."
}

case "${action}" in
  install)
    install_service
    rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "Installation failed (exit code $rc)."
      exit $rc
    fi
    echo "Installation completed successfully."
    exit 0
    ;;
  uninstall)
    uninstall_service
    rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "Uninstallation failed (exit code $rc)."
      exit $rc
    fi
    echo "Uninstallation completed successfully."
    exit 0
    ;;
  *) echo "Usage: $0 [install|uninstall]"; exit 1 ;;
esac

# fallback
exit 0
