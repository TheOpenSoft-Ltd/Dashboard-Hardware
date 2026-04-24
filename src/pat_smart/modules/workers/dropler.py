import datetime
import json
import logging
import os
import ssl
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from pymodbus.client import ModbusSerialClient
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

load_dotenv()

CLIENT_ID = os.getenv("CLIENTID", "")
STATION_NAME = os.getenv("STATION_NAME", "")
STATION_ID = os.getenv("STATION_ID", "")
MQTT_HOST = os.getenv("MQTT_HOST", "")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CERT = os.getenv("MQTT_CERT", "")
MQTT_PRIVATE_KEY = os.getenv("MQTT_PRIVATE_KEY", "")
MQTT_CA = os.getenv("MQTT_CA", "")
TOPIC = f"{CLIENT_ID}/value"

HOST = os.getenv("HOST", "")
SLAVE_ID = 1
PORT = os.getenv("USBPORT", "")
TIME = int(os.getenv("TIME", "10"))

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "radar.log"

custom_theme = Theme(
    {"cyan": "cyan", "yellow": "yellow", "magenta": "magenta", "green": "green"}
)
console = Console(theme=custom_theme, file=sys.__stdout__, force_terminal=True)


class RichFileHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            plain_msg = (
                msg.replace("[cyan]", "")
                .replace("[/cyan]", "")
                .replace("[yellow]", "")
                .replace("[/yellow]", "")
                .replace("[magenta]", "")
                .replace("[/magenta]", "")
                .replace("[green]", "")
                .replace("[/green]", "")
            )
            with open(LOG_FILE, "a") as f:
                f.write(plain_msg + "\n")
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        RichFileHandler(),
        RichHandler(
            console=console,
            show_time=False,
            show_level=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        ),
    ],
)
logger = logging.getLogger(__name__)

errorcounter = 0

mqtt_client = mqtt.Client(client_id=f"{CLIENT_ID}-radar")
mqtt_client.tls_set(
    MQTT_CA, MQTT_CERT, MQTT_PRIVATE_KEY, tls_version=ssl.PROTOCOL_TLS_CLIENT
)
mqtt_client.tls_insecure_set(True)

logger.info("MQTT Client Created")
mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
mqtt_client.loop_start()

client = ModbusSerialClient(
    port=PORT,
    baudrate=9600,
    stopbits=1,
    bytesize=8,
    parity="N",
    timeout=1,
)

connection = client.connect()
if connection:
    logger.info("Connected.")
else:
    logger.error("Connection failed.")
    exit()

logger.info("Modbus Client Ready")


def read_float(address):
    resp = client.read_holding_registers(address, count=2, device_id=SLAVE_ID)
    return ModbusSerialClient.convert_from_registers(
        registers=resp.registers,
        data_type=ModbusSerialClient.DATATYPE.FLOAT32,
        word_order="big",
    )


while True:
    try:
        velocity = read_float(2)
        flowrate = read_float(3)
        temp = read_float(4)
        cumulative_flow = read_float(8)

        dateTime = str(datetime.datetime.now(datetime.timezone.utc))

        data = {
            "device_id": CLIENT_ID,
            "station_name": STATION_NAME,
            "station_id": STATION_ID,
            "date_time": dateTime,
            "velocity": float(f"{velocity:.6f}"),
            "flowrate": float(f"{flowrate:.6f}"),
            "cumulative_flow": float(f"{cumulative_flow:.2f}"),
            "temperature": float(f"{temp:.3f}"),
        }

        mqtt_client.publish(TOPIC, json.dumps(data), qos=1)

        payload_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        logger.info(
            f"[cyan]TOPIC[/cyan] | "
            f"[yellow]{TOPIC}[/yellow] | "
            f"[magenta]PAYLOAD[/magenta] | "
            f"[green]{payload_json}[/green]"
        )

        time.sleep(TIME)

    except Exception as error:
        logger.error(f"Exception occurred: {error}")
        errorcounter += 1
        time.sleep(TIME)
