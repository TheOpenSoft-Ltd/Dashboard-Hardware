import datetime
import json
import os
import random
import threading
import time
from hashlib import sha1
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import redis
from dotenv import load_dotenv
from pymodbus.client import ModbusTcpClient

load_dotenv()

UTC_TZ = ZoneInfo("UTC")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_CHANNEL = os.getenv("REDIS_RADAR_CHANNEL", "radar-data")

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
redis_client.ping()
print(f"[Redis] Connected to {REDIS_HOST}:{REDIS_PORT}", flush=True)


def generate_random_sha():
    random_uuid = uuid4()
    return sha1(str(random_uuid).encode()).hexdigest()


STATION_NAME = os.getenv("STATION_NAME", "")
STATION_ID = os.getenv("STATION_ID", "")
DEVICE_ID = os.getenv("DEVICE_ID", "")
MODE = os.getenv("MODE", "RADAR")
CLIENT_ID = f"{DEVICE_ID}-{generate_random_sha()}"
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = "sensor/{}/radar".format(DEVICE_ID)
STATUS_TOPIC = "sensor/{}/status".format(DEVICE_ID)
HEARTBEAT_TOPIC = "sensor/{}/heartbeat".format(DEVICE_ID)

MODBUS_HOST = os.getenv("HOST", "192.168.1.106")
MODBUS_SLAVEID = 1

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE_PREFIX = "sensor"

# --- self-healing tunables (env-overridable) ------------------------------
TIME = int(os.getenv("TIME") or "2")                       # loop cadence (s)
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL") or "10")
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT") or "3")     # per modbus read (s)
N_FAULT = int(os.getenv("N_FAULT") or "5")                 # consec fails -> FAULT
T_FAULT = float(os.getenv("T_FAULT") or "60")             # max s degraded -> FAULT
N_OPEN = int(os.getenv("N_OPEN") or "5")                  # consec fails -> circuit open
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE") or "1")
BACKOFF_CAP = float(os.getenv("BACKOFF_CAP") or "30")
LOG_EVERY = int(os.getenv("LOG_EVERY") or "30")          # rate-limit repeated error logs

# --- optional systemd watchdog (graceful no-op if lib/WatchdogSec absent) --
try:
    import systemd.daemon as _sd

    def wd_ready():
        try:
            _sd.notify("READY=1")
        except Exception:
            pass

    def wd_ping():
        try:
            _sd.notify("WATCHDOG=1")
        except Exception:
            pass
except Exception:
    def wd_ready():
        pass

    def wd_ping():
        pass


class FileService:
    def __init__(self, log_dir: str, filename_prefix: str):
        self.log_dir = log_dir
        self.filename_prefix = filename_prefix
        self._lock = threading.Lock()

    def _get_log_path(self) -> Path:
        now = datetime.datetime.now(tz=UTC_TZ)
        filename = f"{self.filename_prefix}_{now.strftime('%Y%m%d')}.log"
        return Path(self.log_dir) / filename

    def _ensure_log_dir(self) -> None:
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)

    def _write(self, message: str) -> None:
        self._ensure_log_dir()
        log_path = self._get_log_path()
        timestamp = datetime.datetime.now(tz=UTC_TZ).isoformat()
        with self._lock:
            with open(log_path, "a") as f:
                f.write(f"{timestamp} {message}\n")

    def save_log(self, data: dict, topic: str = "") -> None:
        payload_json = json.dumps(data)
        formatted = f"[cyan]TOPIC[/cyan] | [yellow]{topic}[/yellow] | [magenta]PAYLOAD[/magenta] | [green]{payload_json}[/green]"
        self._write(formatted)


file_service = FileService(LOG_DIR, LOG_FILE_PREFIX)

# ==========================================================================
# Self-healing state machine
#   STARTING -> ONLINE <-> DEGRADED -> FAULT  (any -> OFFLINE via LWT)
#   status published to STATUS_TOPIC ONLY on transition (edge-triggered),
#   so recovery self-clears a stuck "error" and there is no log/MQTT spam.
# ==========================================================================
STATE_STATUS = {"ONLINE": "online", "FAULT": "error", "OFFLINE": "offline"}
state = "STARTING"
last_status = None          # last status actually published to STATUS_TOPIC
last_heartbeat = 0.0
errorcounter = 0            # lifetime (kept for backward compat)
reconnect_count = 0


def _status_payload(status):
    return {
        "id": STATION_ID,
        "device_id": DEVICE_ID,
        "station_name": STATION_NAME,
        "mode": MODE,
        "status": status,
        "lastseen": str(datetime.datetime.now(datetime.timezone.utc)),
    }


def publish_status(status):
    """Publish status to STATUS_TOPIC (retained). Safe to call from any thread."""
    global last_status
    last_status = status
    mqtt_client.publish(STATUS_TOPIC, json.dumps(_status_payload(status)), qos=1, retain=True)
    file_service.save_log({"event": "status", "status": status}, STATUS_TOPIC)


def enter(new_state):
    """Transition the health state; publish status only when it changes."""
    global state
    if new_state != state:
        print(f"[radar] state {state} -> {new_state}", flush=True)
        state = new_state
    status = STATE_STATUS.get(new_state)
    if status and status != last_status:
        publish_status(status)


# ==========================================================================
# MQTT (LWT = offline retained; on_connect re-asserts the true current status)
# ==========================================================================
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("MQTT Client Connected", flush=True)
        # re-assert the real current status after a (re)connect, do NOT force online
        if last_status:
            client.publish(STATUS_TOPIC, json.dumps(_status_payload(last_status)), qos=1, retain=True)
    else:
        print(f"MQTT Connection failed with code {reason_code}", flush=True)


def on_disconnect(client, userdata, reason_code, properties=None):
    print(f"MQTT disconnected with code {reason_code}; auto-reconnect", flush=True)


mqtt_client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
mqtt_client.will_set(
    topic=STATUS_TOPIC,
    payload=json.dumps({"id": STATION_ID, "device_id": DEVICE_ID, "mode": MODE, "status": "offline"}),
    qos=1,
    retain=True,
)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

# Conditional mutual-TLS: enable TLS only when all three cert files are present,
# otherwise connect plaintext. A board without certs degrades to plain instead of
# crash-looping — safe during the TLS migration (cert files arrive per-station later).
_mqtt_certs = (os.getenv("MQTT_CERT", ""), os.getenv("MQTT_PRIVATE_KEY", ""), os.getenv("MQTT_CA", ""))
if all(_mqtt_certs) and all(os.path.exists(p) for p in _mqtt_certs):
    import ssl
    mqtt_client.tls_set(
        ca_certs=_mqtt_certs[2], certfile=_mqtt_certs[0], keyfile=_mqtt_certs[1],
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    print("[MQTT] TLS enabled (client certs present)", flush=True)
else:
    print("[MQTT] plaintext (no client certs present)", flush=True)
mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
mqtt_client.loop_start()


# ==========================================================================
# Modbus connection manager: reconnecting client + circuit breaker + backoff
# ==========================================================================
client = ModbusTcpClient(MODBUS_HOST, port=502, timeout=READ_TIMEOUT)
_failures = 0
_circuit_open = False
_next_probe = 0.0


def _backoff(f):
    return min(BACKOFF_BASE * (2 ** min(f, 6)), BACKOFF_CAP) + random.uniform(0, BACKOFF_BASE)


def ensure_connected():
    global client
    try:
        if getattr(client, "connected", False):
            return
        client.connect()
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        client = ModbusTcpClient(MODBUS_HOST, port=502, timeout=READ_TIMEOUT)
        client.connect()


def read_level():
    """Return (level, data_source). Raises on failure. Honors circuit breaker."""
    global _failures, _circuit_open, _next_probe, reconnect_count
    now = time.monotonic()
    if _circuit_open and now < _next_probe:
        raise TimeoutError("circuit-open: skipping probe")
    ensure_connected()
    r = client.read_holding_registers(31005 - 1, count=2, device_id=MODBUS_SLAVEID)
    if r.isError():
        raise IOError(f"modbus error: {r}")
    level = ModbusTcpClient.convert_from_registers(
        registers=r.registers, data_type=ModbusTcpClient.DATATYPE.FLOAT32, word_order="little"
    )
    # success -> reset breaker
    _failures = 0
    _circuit_open = False
    data_source = "modbus_pv2"
    if level == 0:
        # distinguish dry pipe vs not-configured (does NOT change online/fault)
        try:
            b = client.read_holding_registers(1002, count=2, device_id=MODBUS_SLAVEID)
            if not b.isError():
                base = ModbusTcpClient.convert_from_registers(
                    registers=b.registers, data_type=ModbusTcpClient.DATATYPE.FLOAT32, word_order="little"
                )
                data_source = "dry_pipe" if base and base > 0 else "unconfigured"
        except Exception:
            pass
    return level, data_source


def on_read_failure(err):
    global _failures, _circuit_open, _next_probe, reconnect_count
    if isinstance(err, TimeoutError) and "circuit-open" in str(err):
        return  # P0 2026-06-16: circuit-open skip != real read failure;
                # counting it re-armed _next_probe forever -> half-open never reached (stuck-open)
    _failures += 1
    if not isinstance(err, TimeoutError):  # don't count breaker-skips as new socket errors
        reconnect_count += 1
        try:
            client.close()
        except Exception:
            pass
    _circuit_open = _failures >= N_OPEN
    _next_probe = time.monotonic() + _backoff(_failures)
    if _failures == 1 or _failures % LOG_EVERY == 0:
        print(f"[radar] read error #{_failures} (circuit={'open' if _circuit_open else 'closed'}): {err!r}", flush=True)


# ==========================================================================
# Main loop
# ==========================================================================
wd_ready()
last_success = time.monotonic()

while True:
    try:
        level, data_source = read_level()
        dateTime = str(datetime.datetime.now(datetime.timezone.utc))
        data = {
            "station_id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "date_time": dateTime,
            "level": float("{0:.2f}".format(level)),
        }
        mqtt_client.publish(TOPIC, json.dumps(data), qos=1)
        redis_client.publish(REDIS_CHANNEL, json.dumps(data))
        file_service.save_log(data, TOPIC)
        last_success = time.monotonic()
        enter("ONLINE")
    except Exception as error:
        errorcounter += 1
        on_read_failure(error)
        degraded_for = time.monotonic() - last_success
        if _failures >= N_FAULT or degraded_for >= T_FAULT:
            enter("FAULT")
        else:
            enter("DEGRADED")

    # heartbeat (liveness + self-healing metrics)
    if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
        hb = {
            "id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "mode": MODE,
            "status": "online" if state == "ONLINE" else ("error" if state == "FAULT" else "degraded"),
            "lastseen": str(datetime.datetime.now(datetime.timezone.utc)),
            "health_state": state,
            "consecutive_errors": _failures,
            "reconnect_count": reconnect_count,
            "last_success_age_s": round(time.monotonic() - last_success, 1),
        }
        mqtt_client.publish(HEARTBEAT_TOPIC, json.dumps(hb), qos=1, retain=False)
        last_heartbeat = time.time()

    wd_ping()  # pet systemd watchdog every iteration (no-op unless WatchdogSec set)
    time.sleep(TIME)
