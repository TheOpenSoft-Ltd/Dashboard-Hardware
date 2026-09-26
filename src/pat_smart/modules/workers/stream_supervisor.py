#!/usr/bin/env python3
"""
Self-healing CCTV stream supervisor (reference) — wraps ffmpeg RTSP->RTMP with:
  - health state machine (edge-triggered, self-clearing status)
  - camera + AMS dependency pre-check + circuit breaker + backoff(+jitter)
  - progress-watchdog: kills a frozen-but-alive ffmpeg (out_time not advancing)
  - systemd watchdog (sd_notify, opt-in no-op) + MQTT status/heartbeat (opt-in)
No secrets in code: RTSP_URL / RTMP_URL come from the environment (.env via systemd).
Drop-in replacement for stream.sh:  ExecStart=/usr/bin/python3 stream_supervisor.py
"""
import json
import os
import random
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse

RTSP_URL = os.environ["RTSP_URL"]
RTMP_URL = os.environ["RTMP_URL"]
STREAM_ID = os.getenv("STREAM_ID") or RTMP_URL.rstrip("/").split("/")[-1]
DEVICE_ID = os.getenv("DEVICE_ID", "")

# --- tunables (env-overridable) ---
RW_TIMEOUT_US = int(os.getenv("RW_TIMEOUT_US") or "5000000")   # RTSP read timeout
STALL_TTL = float(os.getenv("STALL_TTL") or "15")             # no-progress -> kill ffmpeg
N_OPEN = int(os.getenv("N_OPEN") or "5")                      # consecutive fails -> circuit open
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE") or "2")
BACKOFF_CAP = float(os.getenv("BACKOFF_CAP") or "60")
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL") or "15")
ENC = os.getenv("ENC", "libx264")                            # copy | h264_v4l2m2m | libx264
BITRATE = os.getenv("BITRATE", "2000k")
GOP = os.getenv("GOP_SIZE", "60")
AUDIO = os.getenv("AUDIO", "aac")                            # aac | none (camera with no usable audio track)
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "128k")

# --- optional systemd watchdog ---
try:
    import systemd.daemon as _sd
    def wd_ready():
        try: _sd.notify("READY=1")
        except Exception: pass
    def wd_ping():
        try: _sd.notify("WATCHDOG=1")
        except Exception: pass
except Exception:
    def wd_ready(): pass
    def wd_ping(): pass

# --- optional MQTT status (edge-triggered, retained, self-clearing) ---
STATUS_TOPIC = f"cctv/{STREAM_ID}/status"
HEARTBEAT_TOPIC = f"cctv/{STREAM_ID}/heartbeat"
STATE_STATUS = {"STREAMING": "online", "FAULT": "error", "OFFLINE": "offline"}
state = "STARTING"
last_status = None
last_reason = None


def _status_json(status, reason):
    return json.dumps({
        "stream_id": STREAM_ID, "device_id": DEVICE_ID, "status": status, "fault_reason": reason,
        "lastseen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def _on_connect(client, userdata, flags, reason_code, properties=None):
    # After any (re)connect the broker has already published the last will ("offline", retained).
    # Status is edge-triggered, so without this a stream that kept running stays "offline" until its
    # state next changes (41 cameras on 2026-09-26). Re-assert the real current status, as radar and
    # dropler do - never force online: before the first status there is nothing to say.
    if reason_code == 0 and last_status:
        client.publish(STATUS_TOPIC, _status_json(last_status, last_reason), qos=1, retain=True)


_mqtt = None
try:
    import paho.mqtt.client as mqtt
    _mqtt = mqtt.Client(client_id=f"stream-{STREAM_ID}-{os.getpid()}", clean_session=True)
    _mqtt.will_set(STATUS_TOPIC, json.dumps({
        "stream_id": STREAM_ID, "device_id": DEVICE_ID, "status": "offline", "fault_reason": "mqtt_disconnected",
    }), qos=1, retain=True)
    _mqtt.on_connect = _on_connect
    # Conditional username/password auth: inert until the broker drops allow_anonymous.
    # Must precede connect(). Independent of TLS — the healer cannot speak TLS, so
    # user/pass is the auth path for the plain listener.
    _mu = os.getenv("MQTT_USERNAME", "")
    if _mu:
        _mqtt.username_pw_set(_mu, os.getenv("MQTT_PASSWORD") or None)
    # Conditional mutual-TLS: certs when all three files exist, else plaintext (already in try/except).
    _sc = (os.getenv("MQTT_CERT", ""), os.getenv("MQTT_PRIVATE_KEY", ""), os.getenv("MQTT_CA", ""))
    if all(_sc) and all(os.path.exists(p) for p in _sc):
        import ssl
        _mqtt.tls_set(ca_certs=_sc[2], certfile=_sc[0], keyfile=_sc[1], tls_version=ssl.PROTOCOL_TLS_CLIENT)
    _mqtt.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")), keepalive=60)
    _mqtt.reconnect_delay_set(min_delay=5, max_delay=5)
    _mqtt.loop_start()
except Exception as e:
    print(f"[stream] MQTT disabled: {e!r}", flush=True)

def publish_status(status, reason=None):
    global last_status, last_reason
    last_status, last_reason = status, reason
    if _mqtt:
        _mqtt.publish(STATUS_TOPIC, _status_json(status, reason), qos=1, retain=True)


def enter(new_state, reason=None):
    global state
    if new_state != state:
        print(f"[stream] state {state} -> {new_state}" + (f" ({reason})" if reason else ""), flush=True)
        state = new_state
    status = STATE_STATUS.get(new_state)
    if status and status != last_status:
        publish_status(status, reason)


def reachable(url, default_port):
    p = urlparse(url)
    host, port = p.hostname, (p.port or default_port)
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


def build_ffmpeg():
    venc = ["-c:v", "copy"] if ENC == "copy" else ["-c:v", ENC, "-preset", "ultrafast",
                                                   "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", "4000k"]
    # NOTE: do NOT hard-code an input-timeout flag — its name differs across ffmpeg builds
    # (rtsp uses -timeout, not -rw_timeout, on ffmpeg 7.x). Stall detection is the progress-
    # watchdog (§3.3) which is build-independent. Set FFMPEG_INPUT_OPTS in .env only if your
    # build supports it, e.g. "-timeout 5000000".
    extra_in = os.getenv("FFMPEG_INPUT_OPTS", "").split()
    # AUDIO=none -> -an. Some cameras expose an audio track ffmpeg cannot encode and the
    # whole stream dies on it (PIT036, hand-fixed on site 2026-07-06). Default keeps aac.
    aenc = ["-an"] if AUDIO.strip().lower() in ("none", "off", "0", "") else ["-c:a", AUDIO, "-b:a", AUDIO_BITRATE]
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-rtsp_transport", "tcp", *extra_in, "-i", RTSP_URL,
        *venc, "-g", GOP, *aenc,
        "-f", "flv", "-progress", "pipe:1", "-stats_period", "2", RTMP_URL,
    ]


def backoff(n):
    return min(BACKOFF_BASE * (2 ** min(n, 6)), BACKOFF_CAP) + random.uniform(0, BACKOFF_BASE)


def run_ffmpeg_once():
    """Spawn ffmpeg; return (exit_reason, frames). Kills it if progress stalls."""
    proc = subprocess.Popen(build_ffmpeg(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    last_out_us = -1
    last_advance = time.monotonic()
    frames = 0
    streaming_announced = False

    def reader():
        nonlocal last_out_us, last_advance, frames
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    v = int(line.split("=", 1)[1])
                    if v > last_out_us:
                        last_out_us = v
                        last_advance = time.monotonic()
                except Exception:
                    pass
            elif line.startswith("frame="):
                try: frames = int(line.split("=", 1)[1])
                except Exception: pass
            elif " " in line:        # ffmpeg log message (not progress key=value) -> surface it
                print(f"[stream][ffmpeg] {line}", flush=True)
    t = threading.Thread(target=reader, daemon=True)
    t.start()

    while True:
        if proc.poll() is not None:
            return ("exit", frames)
        stalled = time.monotonic() - last_advance
        if last_out_us >= 0 and not streaming_announced:
            streaming_announced = True
            enter("STREAMING")
        if streaming_announced:
            wd_ping()  # only pet the watchdog while frames are actually advancing
        if stalled > STALL_TTL:
            print(f"[stream] frozen ({stalled:.0f}s no progress) -> killing ffmpeg", flush=True)
            proc.kill()
            try: proc.wait(timeout=5)
            except Exception: pass
            return ("frozen", frames)
        time.sleep(1)


# --- supervisor main loop ---
wd_ready()
failures = 0
last_heartbeat = 0.0
while True:
    # dependency pre-check (don't spin ffmpeg against a dead camera/AMS)
    if not reachable(RTSP_URL, 554):
        enter("FAULT", "camera_unreachable"); failures += 1
        time.sleep(backoff(failures)); continue
    if not reachable(RTMP_URL, 1935):
        enter("FAULT", "ams_unreachable"); failures += 1
        time.sleep(backoff(failures)); continue

    reason, frames = run_ffmpeg_once()

    if frames > 0:               # we did stream -> a later failure is transient
        failures = 0
    else:
        failures += 1            # never produced a frame -> encoder/handshake problem

    if failures == 0:
        enter("DEGRADED", reason)       # brief gap before restart, status unchanged
        delay = backoff(1)
    elif failures >= N_OPEN:
        enter("FAULT", reason)          # circuit open
        delay = backoff(failures)
    else:
        enter("DEGRADED", reason)
        delay = backoff(failures)

    if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL and _mqtt:
        _mqtt.publish(HEARTBEAT_TOPIC, json.dumps({
            "stream_id": STREAM_ID, "device_id": DEVICE_ID,
            "stream_state": state, "restart_count": failures, "encoder": ENC,
            "lastseen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }), qos=1, retain=False)
        last_heartbeat = time.time()

    print(f"[stream] ffmpeg {reason} (frames={frames}); retry in {delay:.0f}s", flush=True)
    time.sleep(delay)
