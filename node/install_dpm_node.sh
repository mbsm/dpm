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
  read -r -p "${prompt} [Y/n]: " ans
  ans=${ans:-Y}
  case "$ans" in
    [Nn]*) return 1 ;;
    *) return 0 ;;
  esac
}

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo $0 [install|uninstall]"
  exit 1
fi

if [ "$#" -ne 1 ]; then
  echo "Usage: sudo $0 [install|uninstall]"
  exit 1
fi

action="$1"

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

  cp "${TMP_UNIT}" "${DEST_PATH}"
  rm -f "${TMP_UNIT}"
  chmod 644 "${DEST_PATH}"

  systemctl daemon-reload
  systemctl enable "${SERVICE_FILE_NAME}"

  echo "${SERVICE_FILE_NAME} installed. Start with: sudo systemctl start ${SERVICE_FILE_NAME}"
}

uninstall_service() {
  echo "Uninstalling ${SERVICE_FILE_NAME}..."
  systemctl stop "${SERVICE_FILE_NAME}" 2>/dev/null || true
  systemctl disable "${SERVICE_FILE_NAME}" 2>/dev/null || true
  if [ -f "${DEST_PATH}" ]; then
    rm -f "${DEST_PATH}"
  fi
  systemctl daemon-reload
  echo "${SERVICE_FILE_NAME} uninstalled."
}

case "${action}" in
  install) install_service ;;
  uninstall) uninstall_service ;;
  *) echo "Usage: $0 [install|uninstall]"; exit 1 ;;
esac

exit 0