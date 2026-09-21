#!/usr/bin/env bash
#
# vh-server-install.sh — install the VirtualHere USB server as a boot service on a
# PAT station node (CM5/Pi), so its VEGAMET can be configured remotely over NetBird.
#
# Run ON THE NODE (over NetBird SSH), as a sudo-capable user:
#     curl/scp this file over, then:  bash vh-server-install.sh
#
# What it does (idempotent):
#   - downloads the correct VirtualHere server binary for this CPU
#   - names the server after the hostname (so it's identifiable in the client)
#   - installs a systemd service (auto-start on boot, auto-restart)
#   - verifies it's listening on :7575 and that a VEGAMET is on USB
#
# See vegamet-remote-config-guide.md for the full picture.
set -euo pipefail

DIR=/opt/virtualhere
BIN="$DIR/vhusbd"
CFG="$DIR/config.ini"
UNIT=/etc/systemd/system/virtualhere.service
PORT=7575

# --- pick the binary for this architecture -------------------------------------
case "$(uname -m)" in
  aarch64|arm64) VH_FILE=vhusbdarm64 ;;   # CM5, Pi4/Pi5 64-bit  (verified)
  armv7l|armhf)  VH_FILE=vhusbdarm   ;;   # 32-bit Pi
  x86_64|amd64)  VH_FILE=vhusbdx86_64 ;;  # x86 node
  *) echo "Unsupported CPU $(uname -m) — pick a binary manually from virtualhere.com"; exit 1 ;;
esac
VH_URL="https://www.virtualhere.com/sites/default/files/usbserver/$VH_FILE"

echo "[1/6] download $VH_FILE"
sudo mkdir -p "$DIR"
sudo curl -fsSL -o "$BIN" "$VH_URL"
sudo chmod +x "$BIN"

echo "[2/6] name the server after the hostname (identifies this station in the client)"
if ! sudo grep -q '^ServerName=' "$CFG" 2>/dev/null; then
  printf 'ServerName=%s\n' "$(hostname)" | sudo tee "$CFG" >/dev/null
fi
echo "     $(sudo grep ServerName= "$CFG")"

echo "[3/6] install systemd service"
sudo tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=VirtualHere USB Server (VEGAMET remote config over NetBird)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$BIN
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

echo "[4/6] enable + start"
sudo systemctl daemon-reload
sudo systemctl enable --now virtualhere.service

echo "[5/6] verify listener"
sleep 2
if (ss -tln 2>/dev/null || sudo ss -tln) | grep -q ":$PORT"; then
  echo "     listening on :$PORT  (OK)"
else
  echo "     NOT listening on :$PORT — check: journalctl -u virtualhere -n30"
fi

echo "[6/6] verify the VEGAMET is on USB"
if lsusb | grep -qi '1ada:0002\|VEGAMET'; then
  echo "     VEGAMET present on USB  (OK)"
else
  echo "     WARNING: no VEGAMET (1ada:0002) on USB — check the USB cable to the controller"
fi

echo
echo "Done. From a config laptop's VirtualHere client, add hub:  $(hostname).pty-smart.local:$PORT"
echo "(or this node's NetBird IP:$PORT), then right-click the VEGAMET -> Use."
