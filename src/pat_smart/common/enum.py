from enum import Enum


class SensorType(str, Enum):
    DROPLER = "dropler"
    RADAR = "radar"


class SensorStatusType(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
