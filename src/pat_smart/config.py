from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    MQTT_HOST: str
    MQTT_PORT: int = 1883
    MQTT_CERT: str
    MQTT_PRIVATE_KEY: str
    MQTT_CA: str
    DEVICE_ID: str
    STATION_ID: str
    STATION_NAME: str
