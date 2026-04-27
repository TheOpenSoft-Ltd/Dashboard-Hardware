from importlib.metadata import requires
from pathlib import Path
from typing import Required

from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_env_file_path():
    user_config = Path.home() / ".config" / "pat-smart" / ".env"
    if user_config.exists():
        return str(user_config)
    local_env = Path(".env")
    if local_env.exists():
        return str(local_env)
    current_dir = Path.cwd() / ".env"
    if current_dir.exists():
        return str(current_dir)
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    ENV: str = "development"
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_CERT: str = "temp"
    MQTT_PRIVATE_KEY: str = "temp"
    MQTT_CA: str = "temp"
    DEVICE_ID: str = "temp"
    STATION_ID: str = "temp"
    STATION_NAME: str = "temp"
    MODE: str = "DROPLER"
    HEARTBEAT_INTERVAL: int = 10
    LOG_DIR: str = str(Path.home() / ".local" / "state" / "pat-smart" / "logs")
    LOG_FILE_PREFIX: str = "sensor"
    MODBUS_HOST: str = "localhost"
    MODBUS_PORT: int = 502
    MODBUS_USBPORT: str = "/dev/ttyUSB0"
    RTMP_URL: str = "rtsp://localhost:1000"
    RTSP_URL: str = "rtmp://localhost:8444"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_RADAR_CHANNEL: str = "radar-data"
    REDIS_DROPLER_CHANNEL: str = "dropler-data"
