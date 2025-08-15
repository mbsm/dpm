#!/bin/bash
# Installer for dpm-node.service
SERVICE_FILE_NAME="dpm-node.service"
# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# Default service source in the repo (../node relative to this script)
DEFAULT_SERVICE_SRC="$(realpath "${SCRIPT_DIR}/../node/${SERVICE_FILE_NAME}")"
DEST_PATH="/etc/systemd/system/${SERVICE_FILE_NAME}"

prompt_yesno() {
  local prompt="$1"
  if [ "${ASSUME_YES:-false}" = true ]; then
    return 0
  fi
  read -r -p "${prompt} [Y/n]: " ans
  ans=${ans:-Y}
  case "$ans" in
    [Nn]*) return 1 ;;
    *) return 0 ;;
  esac
}

# New flag handling: support per-user install and assume-yes
USER_MODE=false
ASSUME_YES=false

# Allow flexible args: first positional is action, remaining optional flags
if [ "$#" -lt 1 ]; then
  echo "Usage: $0 [install|uninstall] [--user] [-y|--yes]"
  exit 1
fi

action="$1"
shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user) USER_MODE=true ;;
    -y|--yes) ASSUME_YES=true ;;
    *) echo "Unknown option: $1" ; exit 1 ;;
  esac
  shift
done

# If not user mode, require root
if [ "$USER_MODE" = false ] && [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo $0 ${action} [--user] [-y]"
  exit 1
fi

# Adjust systemctl command and destination path for user mode
if [ "$USER_MODE" = true ]; then
  SYSTEMCTL_CMD="systemctl --user"
  DEST_PATH="${HOME}/.config/systemd/user/${SERVICE_FILE_NAME}"
else
  SYSTEMCTL_CMD="systemctl"
  DEST_PATH="/etc/systemd/system/${SERVICE_FILE_NAME}"
fi

install_service() {
  echo "Installing ${SERVICE_FILE_NAME}..."

  if [ ! -f "${DEFAULT_SERVICE_SRC}" ]; then
    echo "Service file not found at ${DEFAULT_SERVICE_SRC}"
    read -r -p "Enter full path to the service file to use: " svc_src
    DEFAULT_SERVICE_SRC="${svc_src}"
  fi

  if [ ! -f "${DEFAULT_SERVICE_SRC}" ]; then
    echo "ERROR: Service file still not found. Aborting."
    exit 1
  fi

  # Ask for WorkingDirectory / ExecStart base path
  DEFAULT_AGENT_DIR="$(realpath "${SCRIPT_DIR}/..")/node"
  echo "Detected repo node directory: ${DEFAULT_AGENT_DIR}"
  if prompt_yesno "Use this directory for WorkingDirectory and ExecStart?"; then
    AGENT_DIR="${DEFAULT_AGENT_DIR}"
  else
    read -r -p "Enter full path to the node directory (must contain node.py): " AGENT_DIR
  fi

  if [ ! -d "${AGENT_DIR}" ] || [ ! -f "${AGENT_DIR}/node.py" ]; then
    echo "ERROR: '${AGENT_DIR}' not valid or node.py not found."
    exit 1
  fi

  EXEC_START="/usr/bin/env python3 ${AGENT_DIR}/node.py"

  TMP_UNIT="$(mktemp)"
  cp "${DEFAULT_SERVICE_SRC}" "${TMP_UNIT}"

  sed -i "s|^WorkingDirectory=.*$|WorkingDirectory=${AGENT_DIR}|g" "${TMP_UNIT}"
  sed -i "s|^ExecStart=.*$|ExecStart=${EXEC_START}|g" "${TMP_UNIT}"

  if ! grep -q '^WorkingDirectory=' "${TMP_UNIT}"; then
    sed -i "/^\[Service\]/a WorkingDirectory=${AGENT_DIR}" "${TMP_UNIT}"
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

  # Ensure destination directory exists for user mode
  mkdir -p "$(dirname "${DEST_PATH}")"
  cp "${TMP_UNIT}" "${DEST_PATH}"
  rm -f "${TMP_UNIT}"
  chmod 644 "${DEST_PATH}"

  ${SYSTEMCTL_CMD} daemon-reload
  ${SYSTEMCTL_CMD} enable "${SERVICE_FILE_NAME}"

  echo "Starting ${SERVICE_FILE_NAME}..."
  if ! ${SYSTEMCTL_CMD} start "${SERVICE_FILE_NAME}"; then
    echo "Failed to start ${SERVICE_FILE_NAME}. Check logs with:'"
    if [ "$USER_MODE" = true ]; then
      echo "  journalctl --user -u ${SERVICE_FILE_NAME} -n 200 --no-pager"
    else
      echo "  sudo journalctl -u ${SERVICE_FILE_NAME} -n 200 --no-pager"
    fi
    return 1
  fi

  # Wait briefly for service to become active
  wait_secs=10
  while [ $wait_secs -gt 0 ]; do
    if ${SYSTEMCTL_CMD} is-active --quiet "${SERVICE_FILE_NAME}"; then
      echo "${SERVICE_FILE_NAME} is running. Recent logs:"
      if [ "$USER_MODE" = true ]; then
        journalctl --user -u "${SERVICE_FILE_NAME}" -n 20 --no-pager | sed 's/^/  /'
      else
        journalctl -u "${SERVICE_FILE_NAME}" -n 20 --no-pager | sed 's/^/  /'
      fi
      return 0
    fi
    sleep 1
    wait_secs=$((wait_secs - 1))
  done

  echo "Service did not reach 'active' state within timeout. Check logs for details:"
  if [ "$USER_MODE" = true ]; then
    echo "  journalctl --user -u ${SERVICE_FILE_NAME} -f"
  else
    echo "  sudo journalctl -u ${SERVICE_FILE_NAME} -f"
  fi
  return 1
}

uninstall_service() {
  echo "Uninstalling ${SERVICE_FILE_NAME}..."
  ${SYSTEMCTL_CMD} stop "${SERVICE_FILE_NAME}" 2>/dev/null || true
  ${SYSTEMCTL_CMD} disable "${SERVICE_FILE_NAME}" 2>/dev/null || true
  if [ -f "${DEST_PATH}" ]; then
    rm -f "${DEST_PATH}"
  fi
  ${SYSTEMCTL_CMD} daemon-reload
  echo "${SERVICE_FILE_NAME} uninstalled."
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
  *) echo "Usage: $0 [install|uninstall] [--user] [-y|--yes]"; exit 1 ;;
esac

# fallback
exit 0