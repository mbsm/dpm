#!/usr/bin/env bash
# Build dpm .debs from the current tree and install them on local + remote hosts.
#
# Usage:
#   ./deploy.sh                  # build, then deploy to default HOSTS
#   ./deploy.sh --build-only     # just rebuild .debs into deb/
#   ./deploy.sh host1 host2 ...  # build, then deploy to the given hosts
#
# Hosts must have passwordless sudo. Remote hosts must also have passwordless ssh.
# Use the literal "localhost" to target the current machine.

set -euo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEB_DIR="$REPO_DIR/deb"
DEFAULT_HOSTS=(localhost kepler)

PKGS=(python3-dpm dpmd dpm-tools)
VERSION=0.1.0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

build_debs() {
    log "Building .debs from $REPO_DIR"
    cd "$REPO_DIR"

    rm -rf debian/dpm-tools debian/python3-dpm debian/dpmd debian/dpm-agent \
           debian/tmp debian/files debian/*.substvars debian/*.debhelper \
           debian/debhelper-build-stamp .pybuild build src/*.egg-info

    dpkg-buildpackage -b -uc -us

    mkdir -p "$DEB_DIR"
    rm -f "$DEB_DIR"/*.deb
    for pkg in "${PKGS[@]}"; do
        mv "$REPO_DIR/../${pkg}_${VERSION}_all.deb" "$DEB_DIR/"
    done
    rm -f "$REPO_DIR"/../dpm_"${VERSION}"_*.{buildinfo,changes}

    log "Built:"
    ls -la "$DEB_DIR"
}

# Print a shell snippet that performs the install. Reused for local + remote.
install_snippet() {
    local deb_path="$1"
    cat <<EOF
set -e
# Legacy: pre-rename dpm-agent package. Stop and purge if still present.
if dpkg -l dpm-agent 2>/dev/null | grep -q '^ii'; then
    sudo systemctl stop dpm-agent || true
    sudo systemctl disable dpm-agent || true
    sudo dpkg --purge dpm-agent
fi
sudo dpkg -i \\
    "$deb_path/python3-dpm_${VERSION}_all.deb" \\
    "$deb_path/dpmd_${VERSION}_all.deb" \\
    "$deb_path/dpm-tools_${VERSION}_all.deb"
sudo systemctl enable --now dpmd
echo "--- post-install state ---"
systemctl is-active dpmd
systemctl is-enabled dpmd
dpkg -l | grep -E '^ii\s+(dpm|python3-dpm)' || true
EOF
}

deploy_local() {
    log "Deploying to localhost"
    bash -c "$(install_snippet "$DEB_DIR")"
}

deploy_remote() {
    local host="$1"
    log "Deploying to $host"

    local remote_dir="/tmp/dpm-deploy-$$"
    ssh "$host" "mkdir -p $remote_dir"
    # shellcheck disable=SC2086
    scp -q "$DEB_DIR"/python3-dpm_${VERSION}_all.deb \
           "$DEB_DIR"/dpmd_${VERSION}_all.deb \
           "$DEB_DIR"/dpm-tools_${VERSION}_all.deb \
           "$host:$remote_dir/"
    ssh "$host" "$(install_snippet "$remote_dir"); rm -rf $remote_dir"
}

main() {
    local build_only=false
    local hosts=()

    for arg in "$@"; do
        case "$arg" in
            --build-only) build_only=true ;;
            -h|--help)    sed -n '2,12p' "$0"; exit 0 ;;
            -*)           die "unknown flag: $arg" ;;
            *)            hosts+=("$arg") ;;
        esac
    done

    [[ ${#hosts[@]} -eq 0 ]] && hosts=("${DEFAULT_HOSTS[@]}")

    build_debs

    $build_only && { log "Build only — skipping deploy"; exit 0; }

    for host in "${hosts[@]}"; do
        if [[ "$host" == "localhost" ]]; then
            deploy_local
        else
            deploy_remote "$host"
        fi
    done

    log "Done."
}

main "$@"
