from pat_smart.config import Settings
from pat_smart.modules.sandbox.worker import SandboxWorker
from pat_smart.services.mqtt.client import MQTTClient

settings = Settings()  # type: ignore


class SandboxRunner:

    def __init__(
        self,
    ):
        self.mqtt = MQTTClient()
        self.deviceId = settings.DEVICE_ID
        self.station_name = settings.STATION_NAME
        self.station_id = settings.STATION_ID
        self.worker: SandboxWorker | None = None

    def start(self):
        connected = self.mqtt.connect()
        if not connected:
            raise RuntimeError("MQTT connection failed")

        self.worker = SandboxWorker(
            mqtt_client=self.mqtt,
            deviceId=self.deviceId,
            station_name=self.station_name,
            station_id=self.station_id,
        )
        self.worker.start()
        self.worker._loop()

    def stop(self):
        if self.worker:
            self.worker.stop()

        self.mqtt.disconnect()
