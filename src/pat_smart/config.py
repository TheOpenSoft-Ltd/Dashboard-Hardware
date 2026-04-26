from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
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
    MODE: str = "DROPLER" or "LADAR" or "FULL"
    HEARTBEAT_INTERVAL: int = 10
    LOG_DIR: str = "logs"
    LOG_FILE_PREFIX: str = "sensor"
    MODBUS_HOST: str = "localhost"
    MODBUS_PORT: int = 502
