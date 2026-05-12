# 🚀 PAT Smart CLI Installation Guide

### 📖 Overview

PAT Smart is a CLI-based monitoring and worker management tool for:

* MQTT
* Modbus TCP/RS485
* Redis
* RTSP/RTMP streaming
* Systemd service management
* IoT monitoring

The CLI provides:

* Installation
* Configuration
* Service management
* Diagnostics
* Log viewing
* Worker reloads

---

# ⚙️ Installation

### ⚡ Quick Install

Run:

```bash
curl -fsSL https://raw.githubusercontent.com/TheOpenSoft-Ltd/Dashboard-Hardware/main/scripts/install.sh | bash
```

This installer automatically installs:

* Python 3
* pipx
* Redis
* FFmpeg
* Git
* PAT Smart

### 📦 Install Specific Version

```bash
curl -fsSL https://raw.githubusercontent.com/TheOpenSoft-Ltd/Dashboard-Hardware/main/scripts/install.sh | bash -s -- --version v0.1.0
```

# Manual Installation

### Install from Release Package

```bash
pipx install pat-smart-0.1.0.tar.gz
```

---

# Update PAT Smart

Re-run installer:

```bash
curl -fsSL https://raw.githubusercontent.com/TheOpenSoft-Ltd/Dashboard-Hardware/main/scripts/install.sh | bash
```

---

# Uninstall

## 🧹 Remove PAT Smart Package

```bash
pipx uninstall pat-smart
```

## 🗂️ Remove Configuration

```bash
rm -rf ~/.config/pat-smart
```

## 📜 Remove Logs

```bash
rm -rf ~/.local/state/pat-smart
```

---

# 🛠️ Initial Setup

After installation:

```bash
pat-smart init
```

This command creates:

```text
~/.config/pat-smart/.env
```

The CLI will ask for:

* MQTT settings
* Device information
* Redis configuration
* Modbus configuration
* Certificates
* Operating mode

---

# Install Services

After initialization:

```bash
pat-smart install
```

This command:

* Creates systemd services
* Enables auto start
* Starts PAT Smart workers

---

# 🔄 Operating Modes

PAT Smart supports 3 modes.

## DROPLER

Runs:

* Dropler worker
* Stream worker

```bash
pat-smart mode DROPLER
```

---

## RADAR

Runs:

* Radar worker
* Stream worker

```bash
pat-smart mode RADAR
```

---

## FULL

Runs:

* Dropler worker
* Radar worker
* Stream worker

```bash
pat-smart mode FULL
```

---

# Apply Mode Changes

After changing mode:

```bash
pat-smart reload
```

The reload command:

* Stops unused services
* Restarts required services
* Reloads systemd

---

# 📊 Service Status

Check service status:

```bash
pat-smart status
```

Example:

```text
📊 PAT SMART STATUS

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Service                    ┃ Active     ┃ Enabled    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ pat-smart-dropler.service  │ ✅ active  │ ✅ enabled │
│ pat-smart-stream.service   │ ✅ active  │ ✅ enabled │
└────────────────────────────┴────────────┴────────────┘
```

---

# 🩺 Diagnostics

Run full diagnostics:

```bash
pat-smart doctor
```

Checks:

* MQTT connectivity
* Redis connectivity
* Modbus permissions
* Certificates
* Service status
* Filesystem access
* Configuration validation

Example:

```text
❌ Doctor found 2 error(s)
```

---

# 📜 Logs

## Show Logs

```bash
pat-smart logs
```

---

## Follow Logs

```bash
pat-smart logs -f
```

---

## Show Specific Service Logs

Dropler:

```bash
pat-smart logs dropler
```

Radar:

```bash
pat-smart logs radar
```

Stream:

```bash
pat-smart logs stream
```

---

# 🌍 Show Environment Configuration

```bash
pat-smart env
```

Displays current configuration values.

Sensitive values are masked automatically.

---

# 🔁 Reload Services

```bash
pat-smart reload
```

This command:

* Reloads systemd
* Stops unrelated services
* Restarts required workers

---

# 🗑️ Remove Services

```bash
pat-smart uninstall
```

---

# Remove Services and Data

```bash
pat-smart uninstall --purge
```

This removes:

* systemd services
* configuration
* logs

---

# 📁 Service Files

Systemd services are installed to:

```text
/etc/systemd/system/
```

Workers are stored at:

```text
~/.config/pat-smart/workers/
```

Logs are stored at:

```text
~/.local/state/pat-smart/logs/
```

---

# 🐧 Useful Linux Commands

## Journalctl

```bash
journalctl -u pat-smart-dropler.service
```

Follow logs:

```bash
journalctl -fu pat-smart-dropler.service
```

---

## Restart Service

```bash
sudo systemctl restart pat-smart-dropler.service
```

---

## Stop Service

```bash
sudo systemctl stop pat-smart-dropler.service
```

---

## Start Service

```bash
sudo systemctl start pat-smart-dropler.service
```

---

# 🚨 Troubleshooting

## pipx Not Found

Install:

```bash
sudo apt install -y pipx
```

Then:

```bash
pipx ensurepath
```

Reconnect SSH.

---

## Redis Connection Failed

Start Redis:

```bash
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

---

## FFmpeg Missing

Install:

```bash
sudo apt install -y ffmpeg
```

---

## MQTT Connection Failed

Check:

```bash
pat-smart doctor
```

Verify:

* MQTT host
* MQTT port
* certificates
* firewall

---

# ✅ Recommended Deployment Flow

## 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/TheOpenSoft-Ltd/Dashboard-Hardware/main/scripts/install.sh | bash
```

---

## 2. Initialize Configuration

```bash
pat-smart init
```

---

## 3. Install Services

```bash
pat-smart install
```

---

## 4. Verify

```bash
pat-smart doctor
```

---

## 5. Check Status

```bash
pat-smart status
```

---

## 6. Follow Logs

```bash
pat-smart logs -f
```
