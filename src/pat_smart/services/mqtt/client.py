import json
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import paho.mqtt.client as mqtt

from pat_smart.common.enum import SensorStatusType
from pat_smart.config import Settings
from pat_smart.utils.generator import generate_random_sha

settings = Settings()  # type: ignore


class MQTTClient:

    STATUS_INTERVAL = 10  # seconds

    def __init__(self):
        self.host = settings.MQTT_HOST
        self.port = settings.MQTT_PORT
        self.deviceId = settings.DEVICE_ID
        self.status_topic = f"sensor/{settings.DEVICE_ID}/status"
        self.heartbeat_topic = f"sensor/{settings.DEVICE_ID}/heartbeat"

        self.client = mqtt.Client(
            client_id=f"${settings.DEVICE_ID}-${generate_random_sha()}",
            clean_session=True,
        )

        self._on_message_cb: Callable | None = None

        self.client.tls_set(
            ca_certs=settings.MQTT_CA,
            certfile=settings.MQTT_CERT,
            keyfile=settings.MQTT_PRIVATE_KEY,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        # LWT
        self.client.will_set(
            topic=self.status_topic,
            payload=json.dumps(
                {
                    "id": settings.STATION_ID,
                    "device_id": settings.DEVICE_ID,
                    "mode": settings.MODE,
                    "status": SensorStatusType.OFFLINE,
                },
            ),
            qos=1,
            retain=True,
        )

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._connected = threading.Event()
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    # --------- Lifecycle--------------
    def connect(self, timeout: int = 5) -> bool:
        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()

            connected = self._connected.wait(timeout=timeout)

            if connected:
                self._start_heartbeat()

            return connected

        except ConnectionRefusedError:
            print(f"[MQTT] connection refused ({self.host}:{self.port})")
        except socket.timeout:
            print("[MQTT] connection timeout")
        except socket.gaierror:
            print("[MQTT] DNS resolution failed")
        except Exception as e:
            print(f"[MQTT] unexpected error: {e}")

        return False

    def disconnect(self):
        self._stop_heartbeat.set()

        self.publish(
            self.status_topic,
            {
                "id": settings.STATION_ID,
                "device_id": settings.DEVICE_ID,
                "mode": settings.MODE,
                "status": SensorStatusType.OFFLINE,
                "lastseen": str(datetime.now(tz=timezone.utc)),
            },
            qos=1,
            retain=True,
        )

        self.client.loop_stop()
        self.client.disconnect()

    # ---------- heartbeat ----------
    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._stop_heartbeat.clear()

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while not self._stop_heartbeat.is_set():
            if self._connected.is_set():
                self.publish(
                    self.heartbeat_topic,
                    {
                        "id": settings.STATION_ID,
                        "device_id": settings.DEVICE_ID,
                        "mode": settings.MODE,
                        "status": SensorStatusType.ONLINE,
                        "lastseen": str(datetime.now(tz=timezone.utc)),
                    },
                    qos=1,
                    retain=False,
                )

            time.sleep(self.STATUS_INTERVAL)

    # ---------- subscribe / publish ----------
    def subscribe(self, topic: str, qos: int = 0):
        self.client.subscribe(topic, qos)

    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False):
        self.client.publish(topic, json.dumps(payload), qos=qos, retain=retain)

    def set_message_handler(self, cb: Callable):
        self._on_message_cb = cb

    # ---------- callbacks ----------
    def _on_connect(self, _client, _userdata, _flags, rc):
        if rc == 0:
            self._connected.set()
            print("[MQTT] connected successfully")

            self.publish(
                self.status_topic,
                {
                    "id": settings.STATION_ID,
                    "device_id": settings.DEVICE_ID,
                    "mode": settings.MODE,
                    "status": SensorStatusType.ONLINE,
                    "lastseen": str(datetime.now(tz=timezone.utc)),
                },
                qos=1,
                retain=True,
            )
        else:
            print(f"[MQTT] connect failed: {rc}")

    def _on_disconnect(self, _client, _userdata, rc):
        self._connected.clear()
        print("[MQTT] disconnected")

    def _on_message(self, _client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            payload = msg.payload.decode()

        if self._on_message_cb:
            self._on_message_cb(msg.topic, payload)
