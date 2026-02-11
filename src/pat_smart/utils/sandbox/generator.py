import random
import time


def generate_payload(topic: str) -> dict:
    if "temp" in topic:
        return {
            "value": round(random.uniform(20, 40), 2),
            "unit": "°C",
            "ts": time.time(),
        }

    if "humidity" in topic:
        return {
            "value": round(random.uniform(40, 80), 2),
            "unit": "%",
            "ts": time.time(),
        }

    return {
        "value": random.random(),
        "ts": time.time(),
    }
