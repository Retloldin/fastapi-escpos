#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="fastapi-escpos"
INSTALL_DIR="/opt/${SERVICE_NAME}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SERVICE_NAME}"
SERVICE_GROUP="${SERVICE_NAME}"
HOST="0.0.0.0"
PORT="8000"
UPDATE_MODE=0
SKIP_APT=0
NO_ENABLE=0
NO_START=0

usage() {
  cat <<'EOF'
Usage:
  sudo ./install_debian_service.sh [options]

Options:
  --update                 Update code, dependencies, and restart the service.
  --install-dir DIR        Installation directory. Default: /opt/fastapi-escpos
  --service-name NAME      systemd service name. Default: fastapi-escpos
  --user USER              Service user. Default: fastapi-escpos
  --group GROUP            Primary group. Default: fastapi-escpos
  --host HOST              Host for uvicorn. Default: 0.0.0.0
  --port PORT              Port for uvicorn. Default: 8000
  --skip-apt               Skip apt-get update/install.
  --no-enable              Do not enable the service at boot.
  --no-start               Do not start or restart the service at the end.
  -h, --help               Show this help message.

Examples:
  sudo ./install_debian_service.sh
  sudo ./install_debian_service.sh --update
  sudo ./install_debian_service.sh --install-dir /srv/fastapi-escpos --port 8080
EOF
}

log() {
  printf '[install] %s\n' "$*"
}

fail() {
  printf '[install][error] %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "This script must be run as root (use sudo)."
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --update)
        UPDATE_MODE=1
        shift
        ;;
      --install-dir)
        INSTALL_DIR="$2"
        shift 2
        ;;
      --service-name)
        SERVICE_NAME="$2"
        shift 2
        ;;
      --user)
        SERVICE_USER="$2"
        shift 2
        ;;
      --group)
        SERVICE_GROUP="$2"
        shift 2
        ;;
      --host)
        HOST="$2"
        shift 2
        ;;
      --port)
        PORT="$2"
        shift 2
        ;;
      --skip-apt)
        SKIP_APT=1
        shift
        ;;
      --no-enable)
        NO_ENABLE=1
        shift
        ;;
      --no-start)
        NO_START=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1"
        ;;
    esac
  done
}

check_platform() {
  [[ -r /etc/os-release ]] || fail "/etc/os-release was not found"
  # shellcheck disable=SC1091
  source /etc/os-release

  [[ "${ID}" == "debian" ]] || fail "This script is intended for Debian. Detected system: ${ID:-unknown}"

  if [[ "${VERSION_CODENAME:-}" != "trixie" ]]; then
    log "Warning: Debian 13 (trixie) was expected but '${VERSION_CODENAME:-unknown}' was detected. Continuing."
  fi
}

install_system_packages() {
  local packages=(
    ca-certificates
    python3
    python3-venv
    python3-dev
    build-essential
    libusb-1.0-0
    udev
    rsync
    libjpeg-dev
  )

  log "Updating APT package indexes"
  apt-get update
  log "Installing system packages: ${packages[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
}

ensure_group() {
  if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    log "Creating group ${SERVICE_GROUP}"
    groupadd --system "${SERVICE_GROUP}"
  fi
}

ensure_user() {
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    log "Creating user ${SERVICE_USER}"
    useradd \
      --system \
      --gid "${SERVICE_GROUP}" \
      --home-dir "${INSTALL_DIR}" \
      --create-home \
      --shell /usr/sbin/nologin \
      "${SERVICE_USER}"
  fi

  for extra_group in lp plugdev; do
    if getent group "${extra_group}" >/dev/null 2>&1; then
      usermod -a -G "${extra_group}" "${SERVICE_USER}"
    fi
  done
}

sync_project() {
  log "Syncing project into ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"

  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.mypy_cache/' \
    --exclude '.env' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"

  if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    if [[ -f "${PROJECT_DIR}/.env" ]]; then
      cp "${PROJECT_DIR}/.env" "${INSTALL_DIR}/.env"
    elif [[ -f "${PROJECT_DIR}/.env.example" ]]; then
      cp "${PROJECT_DIR}/.env.example" "${INSTALL_DIR}/.env"
    else
      fail "Neither .env nor .env.example was found to copy the configuration."
    fi
  fi

  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"
}

setup_venv() {
  log "Preparing virtual environment in ${INSTALL_DIR}/.venv"
  if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
  fi

  "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip setuptools wheel
  "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
}

install_systemd_unit() {
  local unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
  log "Generating systemd unit ${unit_path}"

  cat > "${unit_path}" <<EOF
[Unit]
Description=ESC/POS FastAPI Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
SupplementaryGroups=lp plugdev
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn app.main:app --host ${HOST} --port ${PORT}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  if [[ "${NO_ENABLE}" -eq 0 ]]; then
    systemctl enable "${SERVICE_NAME}"
  fi
}

restart_service() {
  if [[ "${NO_START}" -eq 1 ]]; then
    log "Skipping service start/restart because --no-start was used"
    return
  fi

  if [[ "${UPDATE_MODE}" -eq 1 ]]; then
    log "Restarting service ${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"
  else
    log "Starting service ${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}" 2>/dev/null || systemctl start "${SERVICE_NAME}"
  fi
}

show_summary() {
  cat <<EOF

Installation complete.

Service:       ${SERVICE_NAME}
Directory:     ${INSTALL_DIR}
User:          ${SERVICE_USER}
Host/Port:     ${HOST}:${PORT}

Useful commands:
  systemctl status ${SERVICE_NAME}
  journalctl -u ${SERVICE_NAME} -f
  systemctl restart ${SERVICE_NAME}

Notes:
  - Review ${INSTALL_DIR}/.env to confirm USB settings, bearer token, and CORS.
  - If you change .env, restart the service so the configuration is applied.
  - Use --update to resync code, dependencies, and service files.
EOF
}

main() {
  parse_args "$@"
  require_root
  check_platform

  if [[ "${SKIP_APT}" -eq 0 ]]; then
    install_system_packages
  fi

  ensure_group
  ensure_user
  sync_project
  setup_venv
  install_systemd_unit
  restart_service
  show_summary
}

main "$@"
