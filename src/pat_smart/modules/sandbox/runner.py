from pat_smart.modules.sandbox.worker import SandboxWorker
from pat_smart.services.mqtt.client import MQTTClient


class SandboxRunner:

    def __init__(self, host: str, client_id: str, serial: str, station_name: str):
        self.mqtt = MQTTClient(host, client_id)
        self.serial = serial
        self.station_name = station_name
        self.worker: SandboxWorker | None = None

    def start(self):
        connected = self.mqtt.connect()
        if not connected:
            raise RuntimeError("MQTT connection failed")

        self.worker = SandboxWorker(
            mqtt_client=self.mqtt, serial=self.serial, station_name=self.station_name
        )
        self.worker.start()
        self.worker._loop()

    def stop(self):
        if self.worker:
            self.worker.stop()

        self.mqtt.disconnect()
