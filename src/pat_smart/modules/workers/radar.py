import datetime
import json
import os
import ssl
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from pymodbus.client import ModbusTcpClient

load_dotenv()

CLIENT_ID = os.getenv("CLIENTID", "")
STATION_NAME = os.getenv("STATION_NAME", "")
STATION_ID = os.getenv("STATION_ID", "")
ENDPOINT = os.getenv("ENDPOINT", "")
TOPIC = "{}/value".format(CLIENT_ID)
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
# CERTIFICATE = "../cert/certificate.pem.crt"
# PRIVATE_KEY = "../cert/private.pem.key"
# AMAZON_ROOT_CA_1 = "../cert/RootCA1.pem"

HOST = os.getenv("HOST", "127.0.0.1")
SLAVEID = 1

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE_PREFIX = "dropler"


class FileService:
    def __init__(self, log_dir: str, filename_prefix: str):
        self.log_dir = log_dir
        self.filename_prefix = filename_prefix
        self._lock = threading.Lock()

    def _get_log_path(self) -> Path:
        now = datetime.datetime.now()
        filename = f"{self.filename_prefix}_{now.strftime('%Y%m%d')}.log"
        return Path(self.log_dir) / filename

    def _ensure_log_dir(self) -> None:
        log_path = Path(self.log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

    def _write(self, message: str) -> None:
        self._ensure_log_dir()
        log_path = self._get_log_path()
        timestamp = datetime.datetime.now().isoformat()
        log_line = f"{timestamp} {message}\n"
        with self._lock:
            with open(log_path, "a") as f:
                f.write(log_line)

    def save_log(self, data: dict, topic: str = "") -> None:
        payload_json = json.dumps(data)
        formatted = f"[cyan]TOPIC[/cyan] | [yellow]{topic}[/yellow] | [magenta]PAYLOAD[/magenta] | [green]{payload_json}[/green]"
        self._write(formatted)


TIME = int(os.getenv("TIME") or "10")
errorcounter = 0

file_service = FileService(LOG_DIR, LOG_FILE_PREFIX)

mqtt_client = mqtt.Client(
    client_id=CLIENT_ID,
    clean_session=True,
)

# mqtt_client.tls_set(
#     ca_certs=AMAZON_ROOT_CA_1,
#     certfile=CERTIFICATE,
#     keyfile=PRIVATE_KEY,
#     tls_version=ssl.PROTOCOL_TLS_CLIENT,
# )

mqtt_client.connect(ENDPOINT, MQTT_PORT, keepalive=60)
mqtt_client.loop_start()

print("MQTT Client Connected")

client = ModbusTcpClient(HOST, port=502)

while True:
    try:
        response_flowrate = client.read_holding_registers(
            31001, count=2, device_id=SLAVEID
        )
        response_level = client.read_holding_registers(
            31005, count=2, device_id=SLAVEID
        )
        response_totalize = client.read_holding_registers(
            31009, count=2, device_id=SLAVEID
        )
        level = ModbusTcpClient.convert_from_registers(
            registers=response_level.registers,
            data_type=ModbusTcpClient.DATATYPE.FLOAT32,
            word_order="big",
        )
        dateTime = str(datetime.datetime.now(datetime.timezone.utc))
        data = {
            "device_id": CLIENT_ID,
            "station_name": STATION_NAME,
            "station_id": STATION_ID,
            "date_time": dateTime,
            "level": float("{0:.3f}".format(level)),
        }
        mqtt_client.publish(TOPIC, json.dumps(data), qos=1)
        file_service.save_log(data, TOPIC)
        time.sleep(TIME)
    except Exception as error:
        print("[!] Exception occured: ", error)
        errorcounter = errorcounter + 1
    time.sleep(TIME)
