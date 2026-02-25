from pat_smart.modules.sandbox.worker import SandboxWorker
from pat_smart.services.mqtt.client import MQTTClient


class SandboxRunner:

    def __init__(
        self,
        host: str,
        client_id: str,
        deviceId: str,
        station_name: str,
        station_id: str,
    ):
        self.mqtt = MQTTClient(host, client_id)
        self.deviceId = deviceId
        self.station_name = station_name
        self.station_id = station_id
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
