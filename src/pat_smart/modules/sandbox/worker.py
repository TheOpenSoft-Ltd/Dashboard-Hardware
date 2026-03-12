import threading

from pat_smart.common.enum import SensorType
from pat_smart.utils.generator import generate_payload_mockup
from pat_smart.utils.logger import log_topic, setup_logger


class SandboxWorker:

    def __init__(
        self,
        mqtt_client,
        deviceId: str,
        station_name: str,
        station_id: str,
        interval: int = 2,
    ):
        self.mqtt = mqtt_client
        self.deviceId = deviceId
        self.interval = interval
        self.station_name = station_name
        self.station_id = station_id

        self._stop_event = threading.Event()
        self._thread = None

        self.logger = setup_logger("SANDBOX")

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def _loop(self):
        while not self._stop_event.is_set():

            topic_radar = f"sensor/{self.deviceId}/{SensorType.RADAR.value}"
            topic_dropler = f"sensor/{self.deviceId}/{SensorType.DROPLER.value}"

            # publish same payload or different if needed
            payload_vega = generate_payload_mockup(
                topic_radar, self.deviceId, self.station_name, self.station_id
            )
            payload_dropler = generate_payload_mockup(
                topic_dropler, self.deviceId, self.station_name, self.station_id
            )

            self.mqtt.publish(topic_radar, payload_vega)
            self.mqtt.publish(topic_dropler, payload_dropler)

            log_topic(self.logger, topic_radar, payload_vega)
            log_topic(self.logger, topic_dropler, payload_dropler)

            self._stop_event.wait(self.interval)
