import json
import threading
import paho.mqtt.client as mqtt
from typing import Callable
import socket

class MQTTClient:

    def __init__(self, host:str, client_id: str, port:int = 1883):
        self.host = host
        self.port = port

        self.client = mqtt.Client(client_id=client_id)

        self._on_message_cb: Callable | None = None

        # bind callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._connected = threading.Event()
    
    # --------- Lifecycle--------------
    def connect(self, timeout: int = 5) -> bool:
        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()
            connected = self._connected.wait(timeout=timeout)
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
        self.client.loop_stop()
        self.client.disconnect()
    
    # ---------- subscribe / publish ----------
    def subscribe(self, topic: str, qos: int = 0):
        self.client.subscribe(topic, qos)

    def publish(self, topic: str, payload: dict, qos: int = 1):
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def set_message_handler(self, cb: Callable):
        """
        cb(topic: str, payload: dict)
        """
        self._on_message_cb = cb

     # ---------- callbacks ----------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected.set()
            print('[MQTT] connected successfully')
        else:
            print(f"[MQTT] connect failed: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected.clear()
        print("[MQTT] disconnected")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            payload = msg.payload.decode()

        if self._on_message_cb:
            self._on_message_cb(msg.topic, payload)
        