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
from pymodbus.client import ModbusSerialClient

load_dotenv()

UTC_TZ = ZoneInfo("UTC")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_CHANNEL = os.getenv("REDIS_DROPLER_CHANNEL", "dropler-data")

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
redis_client.ping()
print(f"[Redis] Connected to {REDIS_HOST}:{REDIS_PORT}", flush=True)


def generate_random_sha():
    random_uuid = uuid4()
    return sha1(str(random_uuid).encode()).hexdigest()


STATION_NAME = os.getenv("STATION_NAME", "")
STATION_ID = os.getenv("STATION_ID", "")
DEVICE_ID = os.getenv("DEVICE_ID", "")
MODE = os.getenv("MODE", "DROPLER")
CLIENT_ID = f"{DEVICE_ID}-{generate_random_sha()}"
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = "sensor/{}/dropler".format(DEVICE_ID)
STATUS_TOPIC = "sensor/{}/status".format(DEVICE_ID)
HEARTBEAT_TOPIC = "sensor/{}/heartbeat".format(DEVICE_ID)

MODBUS_PORT = os.getenv("MODBUS_USBPORT", "/dev/ttyUSB0")
MODBUS_SLAVEID = 1
MODBUS_BAUDRATE = int(os.getenv("MODBUS_BAUDRATE") or "9600")

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE_PREFIX = "sensor"

# --- self-healing tunables (env-overridable) ------------------------------
TIME = int(os.getenv("TIME") or "2")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL") or "10")
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT") or "1")
N_FAULT = int(os.getenv("N_FAULT") or "5")
T_FAULT = float(os.getenv("T_FAULT") or "60")
N_OPEN = int(os.getenv("N_OPEN") or "5")
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE") or "1")
BACKOFF_CAP = float(os.getenv("BACKOFF_CAP") or "30")
LOG_EVERY = int(os.getenv("LOG_EVERY") or "30")

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
# Self-healing health state machine (edge-triggered, self-clearing status)
# ==========================================================================
STATE_STATUS = {"ONLINE": "online", "FAULT": "error", "OFFLINE": "offline"}
state = "STARTING"
last_status = None
last_heartbeat = 0.0
errorcounter = 0
reconnect_count = 0

# --- one station status for a dual-sensor station (workers v4, 2026-09-25) -------------------------
# On a FULL station the LEVEL worker (radar.py) speaks for the station - Carey 2026-09-25: "use water
# level as main, if this is down, it can show as offline, if only flow rate offline, it can show as
# error". This worker leaves its own status in a file in RAM for it, publishes that own status and its
# last will on its OWN topic (a dead flow meter must never announce the whole station offline), and
# its heartbeat carries the same station status as the level worker's, so the two stop contradicting
# each other. MODE=DROPLER (flow meter only) keeps the old behaviour: this worker is the station.
SENSOR = "flow"
OTHER_SENSOR = "level" if MODE == "FULL" else None
OWN_STATUS_TOPIC = "sensor/{}/status/flow".format(DEVICE_ID) if OTHER_SENSOR else STATUS_TOPIC
SENSOR_STATE_DIR = os.getenv("SENSOR_STATE_DIR") or ("/dev/shm" if os.path.isdir("/dev/shm") else "/tmp")
SENSOR_STALE_S = float(os.getenv("SENSOR_STALE_S") or "30")
SENSOR_GRACE_S = float(os.getenv("SENSOR_GRACE_S") or "60")
_started = time.monotonic()
_last_own = None


def station_status(level, flow):
    """The station's one status from its two sensors (None = nothing known yet). Same rule as radar.py."""
    if level == "offline":
        return "offline"
    if level == "error":
        return "error"
    if level == "online":
        return "error" if flow in ("error", "offline") else "online"
    return None


def _sensor_path(name):
    return os.path.join(SENSOR_STATE_DIR, "pat-smart-%s-%s.json" % (DEVICE_ID or "station", name))


def write_sensor_state(status, now=None):
    """Leave this sensor's own status for the level worker on the same station (atomic replace)."""
    path = _sensor_path(SENSOR)
    try:
        with open(path + ".tmp", "w") as f:
            json.dump({"status": status, "state": state, "t": time.time() if now is None else now}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def read_sensor_state(name, now=None):
    """The other sensor's status from its file if fresh; "offline" when missing or older than
    SENSOR_STALE_S; None while this worker is still in its start-up grace."""
    now = time.time() if now is None else now
    try:
        with open(_sensor_path(name)) as f:
            d = json.load(f)
        if now - float(d.get("t", 0)) <= SENSOR_STALE_S and d.get("status") in ("online", "error", "offline"):
            return d["status"]
    except (OSError, ValueError, TypeError):
        pass
    return None if time.monotonic() - _started < SENSOR_GRACE_S else "offline"


def own_status():
    """This sensor's status: online / error; a DEGRADED spell keeps the last one; None before the first."""
    global _last_own
    s = STATE_STATUS.get(state)
    if s is not None:
        _last_own = s
    return _last_own


def station_view(now=None):
    """(station status, per-sensor detail or None). MODE=DROPLER: this sensor's status alone."""
    own = own_status()
    if not OTHER_SENSOR:
        return own, None
    level = read_sensor_state(OTHER_SENSOR, now)
    return station_status(level, own), {"level": level, "flow": own}


def _status_payload(status):
    return {
        "id": STATION_ID,
        "device_id": DEVICE_ID,
        "station_name": STATION_NAME,
        "mode": MODE,
        "status": status,
        "lastseen": str(datetime.datetime.now(datetime.timezone.utc)),
        "sensor": SENSOR,
    }


def publish_status(status):
    """This sensor's own status: the station topic when it is the only sensor, else its own topic."""
    global last_status
    last_status = status
    mqtt_client.publish(OWN_STATUS_TOPIC, json.dumps(_status_payload(status)), qos=1, retain=True)
    file_service.save_log({"event": "status", "status": status}, OWN_STATUS_TOPIC)


def enter(new_state):
    global state
    if new_state != state:
        print(f"[dropler] state {state} -> {new_state}", flush=True)
        state = new_state
    if OTHER_SENSOR:
        write_sensor_state(own_status())
    status = STATE_STATUS.get(new_state)
    if status and status != last_status:
        publish_status(status)


# ==========================================================================
# MQTT (LWT offline; on_connect re-asserts the true current status)
# ==========================================================================
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("MQTT Client Connected", flush=True)
        if last_status:
            client.publish(OWN_STATUS_TOPIC, json.dumps(_status_payload(last_status)), qos=1, retain=True)
    else:
        print(f"MQTT Connection failed with code {reason_code}", flush=True)


def on_disconnect(client, userdata, reason_code, properties=None):
    print(f"MQTT disconnected with code {reason_code}; auto-reconnect", flush=True)


mqtt_client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
mqtt_client.will_set(
    topic=OWN_STATUS_TOPIC,  # FULL: the flow meter's own topic - its death is an error for the station, not offline
    payload=json.dumps({"id": STATION_ID, "device_id": DEVICE_ID, "mode": MODE, "status": "offline", "sensor": SENSOR}),
    qos=1,
    retain=True,
)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.reconnect_delay_set(min_delay=5, max_delay=5)

# Conditional username/password auth: set credentials only when MQTT_USERNAME is
# present. Inert while the broker still has allow_anonymous=on, and required the
# moment it is turned off — so credentials can be rolled out ahead of the flip
# rather than during it. Must be called BEFORE connect(). Independent of TLS: the
# healer cannot speak TLS, so user/pass is the auth path for the plain listener.
_mqtt_user = os.getenv("MQTT_USERNAME", "")
if _mqtt_user:
    mqtt_client.username_pw_set(_mqtt_user, os.getenv("MQTT_PASSWORD") or None)
    print("[MQTT] credentials set (user=%s)" % _mqtt_user, flush=True)

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
# Modbus-RTU (serial) connection manager: reconnect + circuit breaker
# ==========================================================================
def _new_serial():
    return ModbusSerialClient(
        port=MODBUS_PORT, baudrate=MODBUS_BAUDRATE, stopbits=1, bytesize=8,
        parity="N", timeout=READ_TIMEOUT,
    )


client = _new_serial()
try:
    client.connect()  # best-effort; loop self-heals if USB not ready yet (no hard exit)
except Exception as e:
    print(f"[dropler] initial serial connect failed (will retry): {e!r}", flush=True)

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
        client = _new_serial()
        client.connect()


def read_float(address):
    resp = client.read_holding_registers(address, count=2, device_id=MODBUS_SLAVEID)
    if resp.isError():
        raise IOError(f"modbus error reg{address}: {resp}")
    return ModbusSerialClient.convert_from_registers(
        registers=resp.registers, data_type=ModbusSerialClient.DATATYPE.FLOAT32, word_order="big"
    )


def read_doppler():
    """Return (liquid_level, velocity, flowrate, temp, cumulative). Raises on failure. Honors breaker.
    NOTE: 0 values are VALID (no-flow / pipe-size not configured) -> still ONLINE, not a fault.
    v2 register fix 2026-06-17: flowrate now 0x0006 (was read_float(3) = corrupt reg[3,4] mix of
    velocity_low+temp_high); +liquid_level 0x0000 (v1 missing). Map per playbook-jgdp-f22d150
    (PIT020 field reverse-engineering 2026-05-27). velocity 0x0002 was already correct."""
    global _failures, _circuit_open, _next_probe
    now = time.monotonic()
    if _circuit_open and now < _next_probe:
        raise TimeoutError("circuit-open: skipping probe")
    ensure_connected()
    liquid_level = read_float(0)   # 0x0000  m
    velocity = read_float(2)       # 0x0002  m/s
    temp = read_float(4)           # 0x0004  degC
    flowrate = read_float(6)       # 0x0006  m3/s  (v2 fix: was read_float(3))
    cumulative = read_float(8)     # 0x0008  m3
    _failures = 0
    _circuit_open = False
    return liquid_level, velocity, flowrate, temp, cumulative


def on_read_failure(err):
    global _failures, _circuit_open, _next_probe, reconnect_count
    if isinstance(err, TimeoutError) and "circuit-open" in str(err):
        return  # P0 2026-06-17 stuck-open fix (same as radar)
    _failures += 1
    if not isinstance(err, TimeoutError):
        reconnect_count += 1
        try:
            client.close()
        except Exception:
            pass
    _circuit_open = _failures >= N_OPEN
    _next_probe = time.monotonic() + _backoff(_failures)
    if _failures == 1 or _failures % LOG_EVERY == 0:
        print(f"[dropler] read error #{_failures} (circuit={'open' if _circuit_open else 'closed'}): {err!r}", flush=True)


# ==========================================================================
# Main loop
# ==========================================================================
wd_ready()
last_success = time.monotonic()

while True:
    try:
        liquid_level, velocity, flowrate, temp, cumulative = read_doppler()
        dateTime = str(datetime.datetime.now(tz=UTC_TZ))
        data = {
            "station_id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "date_time": dateTime,
            "liquid_level": float(f"{liquid_level:.4f}"),
            "velocity": float(f"{velocity:.6f}"),
            "flowrate": float(f"{flowrate:.6f}"),
            "cumulative_flow": float(f"{cumulative:.2f}"),
            "temperature": float(f"{temp:.3f}"),
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

    if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
        hb = {
            "id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "mode": MODE,
            # FULL: the STATION status (the same one the level worker reports), not this sensor's
            "status": (station_view()[0] or "degraded") if OTHER_SENSOR
                      else ("online" if state == "ONLINE" else ("error" if state == "FAULT" else "degraded")),
            "lastseen": str(datetime.datetime.now(datetime.timezone.utc)),
            "health_state": state,
            "consecutive_errors": _failures,
            "reconnect_count": reconnect_count,
            "last_success_age_s": round(time.monotonic() - last_success, 1),
            "sensor": SENSOR,
        }
        if OTHER_SENSOR:
            hb["sensors"] = station_view()[1]
        mqtt_client.publish(HEARTBEAT_TOPIC, json.dumps(hb), qos=1, retain=False)
        last_heartbeat = time.time()

    wd_ping()
    time.sleep(TIME)
