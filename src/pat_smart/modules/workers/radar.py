import datetime
import json
import os
import ssl
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
print(f"[Redis] Connected to {REDIS_HOST}:{REDIS_PORT}")


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
MQTT_CA = os.getenv("MQTT_CA", "")
MQTT_CERT = os.getenv("MQTT_CERT", "")
MQTT_PRIVATE_KEY = os.getenv("MQTT_PRIVATE_KEY", "")
# Use TLS automatically on the secure MQTT port (8883); plain TCP on 1883.
MQTT_USE_TLS = MQTT_PORT == 8883
TOPIC = "sensor/{}/radar".format(DEVICE_ID)
STATUS_TOPIC = "sensor/{}/status".format(DEVICE_ID)
HEARTBEAT_TOPIC = "sensor/{}/heartbeat".format(DEVICE_ID)

MODBUS_HOST = os.getenv("HOST", "192.168.1.106")
MODBUS_SLAVEID = 1

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE_PREFIX = "sensor"


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
        log_path = Path(self.log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

    def _write(self, message: str) -> None:
        self._ensure_log_dir()
        log_path = self._get_log_path()
        timestamp = datetime.datetime.now(tz=UTC_TZ).isoformat()
        log_line = f"{timestamp} {message}\n"
        with self._lock:
            with open(log_path, "a") as f:
                f.write(log_line)

    def save_log(self, data: dict, topic: str = "") -> None:
        payload_json = json.dumps(data)
        formatted = f"[cyan]TOPIC[/cyan] | [yellow]{topic}[/yellow] | [magenta]PAYLOAD[/magenta] | [green]{payload_json}[/green]"
        self._write(formatted)


TIME = int(os.getenv("TIME") or "2")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL") or "10")
errorcounter = 0

mqtt_connected = False
last_heartbeat = 0

file_service = FileService(LOG_DIR, LOG_FILE_PREFIX)


def on_connect(client, userdata, flags, reason_code, properties=None):
    global mqtt_connected
    if reason_code == 0:
        mqtt_connected = True
        print(f"MQTT Client Connected")
        print(f"Publishing status to {STATUS_TOPIC}")
        status_payload = {
            "id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "mode": MODE,
            "status": "online",
        }
        result = client.publish(
            STATUS_TOPIC, json.dumps(status_payload), qos=1, retain=True
        )
        print(f"Status published: {result.rc}")
        file_service.save_log(
            {"event": "connected", "status": "online", "rc": result.rc}, STATUS_TOPIC
        )
    else:
        print(f"MQTT Connection failed with code {reason_code}")
        error_data = {
            "event": "connect_failed",
            "error": "MQTT connection failed",
            "return_code": reason_code,
        }
        file_service.save_log(error_data, TOPIC)
        raise ConnectionError(f"MQTT connection failed with code {reason_code}")


def on_disconnect(client, userdata, reason_code, properties=None):
    global mqtt_connected
    mqtt_connected = False
    print(f"MQTT disconnected with code {reason_code}")
    status_payload = {
        "id": STATION_ID,
        "device_id": DEVICE_ID,
        "station_name": STATION_NAME,
        "mode": MODE,
        "status": "offline",
    }
    result = client.publish(
        STATUS_TOPIC, json.dumps(status_payload), qos=1, retain=True
    )
    print(f"Offline status published: {result.rc}")
    file_service.save_log(
        {"event": "disconnected", "status": "offline", "rc": result.rc}, STATUS_TOPIC
    )
    print("Auto-reconnect enabled, waiting for reconnection...")


mqtt_client = mqtt.Client(
    client_id=CLIENT_ID,
    clean_session=True,
)

mqtt_client.will_set(
    topic=STATUS_TOPIC,
    payload=json.dumps(
        {
            "id": STATION_ID,
            "device_id": DEVICE_ID,
            "mode": MODE,
            "status": "offline",
        },
    ),
    qos=1,
    retain=True,
)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

if MQTT_USE_TLS:
    print(f"[MQTT] TLS enabled (port {MQTT_PORT})")
    mqtt_client.tls_set(
        ca_certs=MQTT_CA or None,
        certfile=MQTT_CERT or None,
        keyfile=MQTT_PRIVATE_KEY or None,
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
else:
    print(f"[MQTT] TLS disabled (port {MQTT_PORT})")

mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
mqtt_client.loop_start()


client = ModbusTcpClient(MODBUS_HOST, port=502)

while True:
    try:
        response_level = client.read_holding_registers(
            31005 - 1, count=2, device_id=MODBUS_SLAVEID
        )

        level = ModbusTcpClient.convert_from_registers(
            registers=response_level.registers,
            data_type=ModbusTcpClient.DATATYPE.FLOAT32,
            word_order="little",
        )
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

        current_time = time.time()

        if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
            heartbeat_payload = {
                "id": STATION_ID,
                "device_id": DEVICE_ID,
                "station_name": STATION_NAME,
                "mode": MODE,
                "status": "online",
                "lastseen": dateTime,
            }
            mqtt_client.publish(
                HEARTBEAT_TOPIC, json.dumps(heartbeat_payload), qos=1, retain=False
            )
            last_heartbeat = current_time

        time.sleep(TIME)
    except Exception as error:
        print("[!] Exception occured: ", error)
        errorcounter = errorcounter + 1

        status_payload = {
            "id": STATION_ID,
            "device_id": DEVICE_ID,
            "station_name": STATION_NAME,
            "mode": MODE,
            "status": "error",
            "lastseen": str(datetime.datetime.now(datetime.timezone.utc)),
        }
        mqtt_client.publish(
            STATUS_TOPIC, json.dumps(status_payload), qos=1, retain=True
        )

        time.sleep(TIME)
