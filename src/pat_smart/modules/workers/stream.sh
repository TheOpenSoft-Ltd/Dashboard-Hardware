#!/bin/bash
# self-healing launcher: source .env then exec the python supervisor (pat-smart venv)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && { set -a; source "$ENV_FILE"; set +a; }
exec /home/admin/.local/share/pipx/venvs/pat-smart/bin/python3 "$SCRIPT_DIR/stream_supervisor.py"
