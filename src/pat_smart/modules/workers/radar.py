import datetime
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
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

# --- 4-20 mA loop current (Track A, 2026-09-23) ----------------------------
# The VEGAMET does not expose the loop current on Modbus (register sweep 0..40000
# found only the PV); it is on the controller's web status page, no PIN. The
# worker publishes the raw number only (decision D2); classification lives
# downstream in the statistics service.
CURRENT_HOST = os.getenv("VEGAMET_HTTP_HOST") or MODBUS_HOST
CURRENT_POLL_S = float(os.getenv("CURRENT_POLL_S") or "10")      # one HTTP read every N s, off the level path
CURRENT_TIMEOUT_S = float(os.getenv("CURRENT_TIMEOUT_S") or "2")  # a page read takes ~0.5 s on the LAN
CURRENT_STALE_S = float(os.getenv("CURRENT_STALE_S") or "60")     # older than this -> current_ma is null
LOOP_MIN_MA, LOOP_MAX_MA = 3.8, 20.5                              # outside = loop fault (logged here, classified downstream)

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
# Loop current (mA): read from the controller's web page in its OWN thread, so a
# slow or absent page can never delay or fail the level loop. The level payload
# carries the last value read (or null when never read / older than
# CURRENT_STALE_S) plus data_source, which read_level() already computes.
# ==========================================================================
_current = {"ma": None, "ts": 0.0, "prefix": None, "ok": None}
_current_lock = threading.Lock()
_MA_RE = re.compile(r"([0-9]+[.,][0-9]+)\s*mA")


def _http_get(url, timeout=CURRENT_TIMEOUT_S):
    """(final url after redirects, body as text). The page is latin-1 (German UI)."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.geturl(), r.read().decode("latin-1", "replace")


def discover_current_prefix():
    """Page path prefix (e.g. '/049/') taken from the controller's root redirect; never hard-coded."""
    final, _ = _http_get("http://%s/" % CURRENT_HOST)
    path = urllib.parse.urlsplit(final).path
    if "/" not in path.strip("/"):
        return "/"
    return path.rsplit("/", 1)[0] + "/"


def parse_current_ma(html):
    """First 'n,nnn mA' / 'n.nnn mA' on the page with tags stripped, as a float; None if absent."""
    text = re.sub(r"<[^>]+>", " ", html)
    m = _MA_RE.search(text)
    return float(m.group(1).replace(",", ".")) if m else None


def read_current_once(prefix):
    _, html = _http_get("http://%s%sinput.htm" % (CURRENT_HOST, prefix))
    return parse_current_ma(html)


def current_snapshot():
    """The loop current for the payload: None when never read or stale."""
    with _current_lock:
        if _current["ma"] is None or time.monotonic() - _current["ts"] > CURRENT_STALE_S:
            return None
        return _current["ma"]


def _current_loop():
    time.sleep(2)  # let the level loop come up first; the first payloads carry null
    fails = 0
    while True:
        try:
            if _current["prefix"] is None:
                _current["prefix"] = discover_current_prefix()
                print(f"[radar] loop current page: http://{CURRENT_HOST}{_current['prefix']}input.htm", flush=True)
            ma = read_current_once(_current["prefix"])
            with _current_lock:
                _current["ma"], _current["ts"] = ma, time.monotonic()
            ok = ma is not None and LOOP_MIN_MA <= ma <= LOOP_MAX_MA
            if ok != _current["ok"]:
                print(f"[radar] loop {'ok' if ok else 'FAULT'}: {ma} mA", flush=True)
                _current["ok"] = ok
            fails = 0
            delay = CURRENT_POLL_S
        except Exception as e:
            fails += 1
            if fails == 1 or fails % LOG_EVERY == 0:
                print(f"[radar] loop current read error #{fails}: {e!r}", flush=True)
            if fails >= 3:
                _current["prefix"] = None  # re-discover: the controller may have rebooted or renumbered its pages
            delay = min(CURRENT_POLL_S * (2 ** min(fails, 4)), 300)
        time.sleep(delay)


def build_payload(level, data_source):
    """The radar payload: level as before, plus the raw loop current and where the level came from."""
    ma = current_snapshot()
    return {
        "station_id": STATION_ID,
        "device_id": DEVICE_ID,
        "station_name": STATION_NAME,
        "date_time": str(datetime.datetime.now(datetime.timezone.utc)),
        "level": float("{0:.2f}".format(level)),
        "current_ma": None if ma is None else round(ma, 3),
        "data_source": data_source,
    }


threading.Thread(target=_current_loop, name="loop-current", daemon=True).start()


# ==========================================================================
# Main loop
# ==========================================================================
wd_ready()
last_success = time.monotonic()

while True:
    try:
        level, data_source = read_level()
        data = build_payload(level, data_source)
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
            "current_ma": current_snapshot(),
        }
        mqtt_client.publish(HEARTBEAT_TOPIC, json.dumps(hb), qos=1, retain=False)
        last_heartbeat = time.time()

    wd_ping()  # pet systemd watchdog every iteration (no-op unless WatchdogSec set)
    time.sleep(TIME)
