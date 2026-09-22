#!/bin/bash
# self-healing launcher: source .env then exec the python stream supervisor (pat-smart venv).
# Activated 2026-07-06: previously invoked ffmpeg directly, which crash-looped (Restart=always)
# whenever the RTSP camera was unreachable. stream_supervisor.py adds camera/AMS pre-check,
# backoff+jitter, circuit breaker, and a frozen-ffmpeg progress-watchdog, so a down camera
# now parks the service in a quiet FAULT/backoff state and auto-resumes when it returns.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"
VENV_PY=/home/admin/.local/share/pipx/venvs/pat-smart/bin/python3

if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

[ -x "$VENV_PY" ] || VENV_PY=/usr/bin/python3
exec "$VENV_PY" "$SCRIPT_DIR/stream_supervisor.py"
