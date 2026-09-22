#!/usr/bin/env bash
#
# vh-server-install-oldfleet.sh — install the VirtualHere USB server on one of the
# 58 ALREADY-DEPLOYED PAT nodes (PIR001-015 / PIT001-043).
#
# WHY A SEPARATE SCRIPT FROM vh-server-install.sh:
#   The new CM5 boards give `admin` blanket NOPASSWD sudo. The OLD deployed nodes do NOT —
#   they allow NOPASSWD only for:  apt-get, apt, dpkg, systemctl, mkdir, tee
#   (verified on PIT004 2026-09-11). So `sudo curl` and `sudo chmod` are DENIED and the
#   stock installer fails partway. This version uses only permitted operations:
#     - download the binary as `admin` into /tmp   (no sudo needed)
#     - place it with `sudo tee`                   (permitted)
#     - make it executable via ExecStartPre in the unit, which already runs as root
#     - write unit/config with `sudo tee`, manage with `sudo systemctl`
#   It does NOT modify sudoers and does NOT touch pat-smart, redis or netbird.
#
# SAFETY on a live node: additive only — a new /opt/virtualhere dir, a new
# virtualhere.service, and an otherwise-unused port 7575. Sensor data is unaffected:
# the VEGAMET's *ethernet* carries Modbus; its USB is config-only.
#   ⚠️ On MODE=FULL / DROPLER nodes the dropler worker may hold /dev/ttyACM*. Installing is
#      still safe — VirtualHere only claims a device when a client clicks "Use". Do not
#      claim the serial device on such a node while dropler is running.
#
# Run ON THE NODE as admin:   bash vh-server-install-oldfleet.sh
set -euo pipefail

DIR=/opt/virtualhere
BIN="$DIR/vhusbd"
CFG="$DIR/config.ini"
UNIT=/etc/systemd/system/virtualhere.service
PORT=7575
TMP=/tmp/vhusbd.$$

case "$(uname -m)" in
  aarch64|arm64) VH_FILE=vhusbdarm64 ;;
  armv7l|armhf)  VH_FILE=vhusbdarm   ;;
  x86_64|amd64)  VH_FILE=vhusbdx86_64 ;;
  *) echo "FAIL unsupported CPU $(uname -m)"; exit 1 ;;
esac
CHMOD=$(command -v chmod)

echo "[1/6] download $VH_FILE as admin (no sudo)"
curl -fsSL -o "$TMP" "https://www.virtualhere.com/sites/default/files/usbserver/$VH_FILE"
test -s "$TMP" || { echo "FAIL empty download"; exit 1; }
echo "      $(wc -c < "$TMP") bytes"

echo "[2/6] place binary with sudo tee (sudo cp/mv/install are denied here)"
sudo -n /usr/bin/mkdir -p "$DIR"
sudo -n /usr/bin/tee "$BIN" < "$TMP" >/dev/null
rm -f "$TMP"

echo "[3/6] config: name the server after the hostname"
printf 'ServerName=%s\n' "$(hostname)" | sudo -n /usr/bin/tee "$CFG" >/dev/null

echo "[4/6] systemd unit (ExecStartPre does the chmod as root — sudo chmod is denied)"
sudo -n /usr/bin/tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=VirtualHere USB Server (VEGAMET remote config over NetBird)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStartPre=$CHMOD 0755 $BIN
ExecStart=$BIN
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

echo "[5/6] enable + start"
sudo -n /usr/bin/systemctl daemon-reload
sudo -n /usr/bin/systemctl enable --now virtualhere.service >/dev/null 2>&1
sleep 3

echo "[6/6] verify"
ACT=$(systemctl is-active virtualhere.service 2>/dev/null)
LSN=$(ss -tln 2>/dev/null | grep -c ":$PORT" || true)
VEG=$(lsusb 2>/dev/null | grep -ci '1ada:0002\|VEGAMET' || true)
PAT=$(for u in pat-smart-radar pat-smart-dropler pat-smart-stream; do printf '%s=%s ' "$u" "$(systemctl is-active $u)"; done)
echo "      virtualhere=$ACT  listening7575=$LSN  vegamet_on_usb=$VEG"
echo "      pat-smart UNCHANGED? $PAT"
[ "$ACT" = active ] && [ "$LSN" -ge 1 ] && echo "RESULT OK $(hostname)" || echo "RESULT FAIL $(hostname)"
