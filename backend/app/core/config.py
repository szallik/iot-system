from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Dict


class Settings(BaseSettings):
    APP_NAME: str = "IoT Room Monitoring & Access Control API"
    DEBUG: bool = False

    DATABASE_URL: str = "sqlite:///./data/iot_system.db"

    MQTT_BROKER_HOST: str = "192.168.0.107"
    MQTT_BROKER_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    MQTT_BASE_TOPIC: str = "zigbee2mqtt"

    JWT_SECRET_KEY: str = "tajny-klucz-laboratoryjny"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    FRONTEND_DIST_PATH: str = "../frontend/dist"

    DEVICE_ALIASES: Dict[str, str] = {
        "Temp x humidity": "czujnik_temperatury",
        "env_sensor_1": "czujnik_temperatury",
        "0xa4c1380cbaa9eaca": "czujnik_temperatury",
        "wibracje": "czujnik_wibracji",
        "0xa4c138d0573d40da": "czujnik_wibracji",
        "0xf044d3fffe2a5a50": "przycisk",
        "occupancy_sensor": "czujnik_obecnosci",
        "czujnik_obecności": "czujnik_obecnosci",
        "0xf044d3fffe014c86": "czujnik_obecnosci",
        "0xa4c13879d97f71a0": "listwa",
        "0xd878f0fffe0fa836": "pilot",
        "0xa4c138acc1df59e7": "zarowka_rgb",
        "cieplomierz": "heat_meter_1",
        "kontrola_dostepu": "access_1",
    }

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()