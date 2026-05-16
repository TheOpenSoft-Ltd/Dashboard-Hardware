#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="TheOpenSoft-Ltd"
REPO_NAME="Dashboard-Hardware"

INSTALL_VERSION="${VERSION:-${INSTALL_VERSION:-latest}}"
AUTO_INSTALL="${AUTO_INSTALL:-true}"

OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"

usage() {
  cat <<USAGE
Usage: install.sh [OPTIONS]

Install PAT Smart from GitHub releases.

Options:
  --version VER          Version to install
  --no-auto-install      Skip dependency installation
  -h, --help             Show help

Examples:
  curl -fsSL https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/main/scripts/install.sh | bash

  curl -fsSL https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/main/scripts/install.sh | bash -s -- --version v1.0.0

USAGE
}

log() {
  printf '[pat-smart] %s\n' "$*" >&2
}

error() {
  printf '[pat-smart][error] %s\n' "$*" >&2
  exit 1
}

detect_linux_distro() {

  [[ -f "$OS_RELEASE_FILE" ]] || error "Cannot detect Linux distribution"

  . "$OS_RELEASE_FILE"

  DISTRO_ID="$ID"
  DISTRO_VERSION="${VERSION_ID:-}"
}

install_dependencies_linux() {
  detect_linux_distro
  log "Detected Linux ($DISTRO_ID)"
  case "$DISTRO_ID" in
    ubuntu|debian|pop|linuxmint|elementary)
      UPDATE_CMD="sudo apt update"
      INSTALL_CMD="sudo apt install -y"
      PACKAGES=(
        python3
        python3-pip
        python3-venv
        git
        pipx
        redis-server
        ffmpeg
        curl
        tar
      )
      ;;
    fedora|rhel|centos|rocky|almalinux)
      UPDATE_CMD="sudo dnf check-update || true"
      INSTALL_CMD="sudo dnf install -y"
      PACKAGES=(
        python3
        python3-pip
        git
        pipx
        redis
        ffmpeg
        curl
        tar
      )
      ;;  
    arch|archarm|manjaro|endeavouros)
      UPDATE_CMD="sudo pacman -Sy"
      INSTALL_CMD="sudo pacman -S --noconfirm"
      PACKAGES=(
        python
        python-pip
        git
        python-pipx
        redis
        ffmpeg
        curl
        tar
      )
      ;;
    alpine)
      UPDATE_CMD="sudo apk update"
      INSTALL_CMD="sudo apk add"
      PACKAGES=(
        python3
        py3-pip
        git
        pipx
        redis
        ffmpeg
        curl
        tar
      )
      ;;
    *)
      error "Unsupported Linux distribution: $DISTRO_ID"
      ;;
  esac
  log "Installing dependencies..."
  eval "$UPDATE_CMD"
  $INSTALL_CMD "${PACKAGES[@]}"

  if command -v pipx >/dev/null 2>&1; then
    pipx ensurepath >/dev/null 2>&1 || true
  fi

  if command -v systemctl >/dev/null 2>&1; then

    sudo systemctl enable redis-server >/dev/null 2>&1 || true
    sudo systemctl start redis-server >/dev/null 2>&1 || true

  fi
  log "Dependencies installed successfully"
}

check_dependencies() {

  local missing=false

  command -v python3 >/dev/null 2>&1 || missing=true
  command -v git >/dev/null 2>&1 || missing=true
  command -v ffmpeg >/dev/null 2>&1 || missing=true
  command -v tar >/dev/null 2>&1 || missing=true

  if ! command -v pipx >/dev/null 2>&1; then
    missing=true
  fi

  if ! command -v redis-server >/dev/null 2>&1; then
    missing=true
  fi

  if [[ "$missing" == "true" ]]; then
    if [[ "$AUTO_INSTALL" == "true" ]]; then
      install_dependencies_linux
    else
      error "Missing required dependencies"
    fi
  fi  
  log "All dependencies available"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version)
        [[ $# -lt 2 ]] && error "--version requires argument"
        INSTALL_VERSION="$2"
        shift 2
        ;;
      --no-auto-install)
        AUTO_INSTALL="false"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        error "Unknown option: $1"
        ;;
    esac
  done
}

get_latest_release() {
  local api_url="https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/releases/latest"
  curl -fsSL "$api_url" \
    | sed -En 's/.*"tag_name": "([^"]+)".*/\1/p'
}

download_and_install() {

  local version="$1"
  if [[ "$version" == "latest" ]]; then
    log "Fetching latest release..."
    version=$(get_latest_release)
    log "Latest version: $version"
  fi

  local version_tag="$version"
  [[ "$version_tag" =~ ^v ]] || version_tag="v$version_tag"
  local version_number="${version_tag#v}"
  local url="https://github.com/$REPO_OWNER/$REPO_NAME/releases/download/$version_tag/pat_smart-${version_number}.tar.gz"
  TEMP_DIR=$(mktemp -d)

  local archive="$TEMP_DIR/pat_smart.tar.gz"

  log "Downloading release..."

  curl -fsSL \
    -o "$archive" \
    "$url" || error "Download failed"

  # remove old version
  if pipx list | grep -q "pat-smart"; then
    log "Removing old PAT Smart version..."
    pipx uninstall pat-smart >/dev/null 2>&1 || true
  fi

  log "Installing PAT Smart..."
  pipx install "$archive" >&2 || error "pipx install failed"
  echo "$archive"
}

copy_workers() {
  local archive="$1"
  log "Extracting workers..."
  local extract_dir
  extract_dir=$(mktemp -d)
  tar -xzf "$archive" -C "$extract_dir"
  
  local source_dir
  source_dir=$(find "$extract_dir" \
    -maxdepth 1 \
    -type d \
    -name "pat_smart-*")
  
  local worker_src="$source_dir/src/pat_smart/modules/workers"
  local worker_dest="$HOME/.config/pat-smart/workers"
  
  mkdir -p "$worker_dest"
  cp -r "$worker_src"/* "$worker_dest/"
  log "Workers copied to:"
  log "  $worker_dest"
  rm -rf "$extract_dir"
}

main() {
  parse_args "$@"
  trap '[[ -d "${TEMP_DIR:-}" ]] && rm -rf "$TEMP_DIR"' EXIT
  log "Checking dependencies..."
  check_dependencies

  
  # cleanup local conflicting installs
  rm -f "$HOME/.local/bin/pat-smart" 2>/dev/null || true

  local archive
  archive=$(download_and_install "$INSTALL_VERSION")
  copy_workers "$archive"
  cat <<EOF

✅ PAT Smart installation complete

Next steps:
  pat-smart init
  pat-smart install
  pat-smart doctor

Workers:
  ~/.config/pat-smart/workers/

Logs:
  ~/.local/state/pat-smart/logs/

Update:
  curl -fsSL https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/main/scripts/install.sh | bash

Uninstall:
  pipx uninstall pat-smart
  rm -rf ~/.config/pat-smart

EOF
}
if [[ "${BASH_SOURCE[0]-$0}" == "$0" ]]; then
  main "$@"
fi