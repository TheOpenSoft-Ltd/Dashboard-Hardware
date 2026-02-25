import random
from datetime import datetime, timezone
from hashlib import sha1
from uuid import uuid4

from pat_smart.common.enum import SensorType


def generate_random_sha():
    random_uuid = uuid4()
    return sha1(str(random_uuid).encode()).hexdigest()


def generate_payload_mockup(
    topic: str, deviceId: str, station_name: str, station_id: str
) -> dict:
    payload = {
        "deviceId": deviceId,
        "station_name": station_name,
        "station_id": station_id,
        "date_time": str(datetime.now(tz=timezone.utc)),
    }

    if SensorType.VEGA in topic:
        payload = payload | {
            "level": round(random.uniform(1.0, 4.0), 2),
        }
    elif SensorType.DROPLER in topic:
        payload = payload | {
            "velocity": round(random.uniform(5.0, 10.0), 4),
            "flowrate": round(random.uniform(10.0, 40.0), 4),
            "cumulative_flow": round(random.uniform(10, 1000), 2),
            "temperature": round(random.uniform(5.0, 30.0), 2),
        }
    return payload
