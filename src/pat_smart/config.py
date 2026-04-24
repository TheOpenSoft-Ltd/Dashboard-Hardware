from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    ENV: str = "development"
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_CERT: str = "s"
    MQTT_PRIVATE_KEY: str = "s"
    MQTT_CA: str = "s"
    DEVICE_ID: str = "s"
    STATION_ID: str = "s"
    STATION_NAME: str = "s"
    MODE: str = "DROPLER" or "LADAR" or "FULL"
    LOG_DIR: str = "logs"
    LOG_FILE_PREFIX: str = "sensor_log"
    MODBUS_HOST: str = "localhost"
    MODBUS_PORT: int = 502
