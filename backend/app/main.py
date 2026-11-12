import os
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import engine, Base, SessionLocal
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
    unhandled_exception_handler,
)
from app.core.security import hash_secret_with_salt, utc_now_iso
from app.models.entities import User, Device, Automation
from app.services.mqtt_service import mqtt_manager
from app.routers import auth, devices, telemetry, users, access_reports, automations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("iot.main")


def seed_initial_data():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add_all(
                [
                    User(
                        login="admin",
                        hash_pin=hash_secret_with_salt("1234"),
                        password_hash=hash_secret_with_salt("admin123"),
                        rola="admin",
                        aktywny=True,
                    ),
                    User(
                        login="jan_kowalski",
                        hash_pin=hash_secret_with_salt("4821"),
                        password_hash=hash_secret_with_salt("user123"),
                        rola="user",
                        aktywny=True,
                    ),
                    User(
                        login="zablokowany_pracownik",
                        hash_pin=hash_secret_with_salt("9999"),
                        password_hash=hash_secret_with_salt("guest123"),
                        rola="guest_no_access",
                        aktywny=False,
                    ),
                ]
            )
            logger.info("Zainicjalizowano użytkowników testowych (PIN: 1234, 4821, 9999).")

        if db.query(Device).count() == 0:
            ts = utc_now_iso()
            initial_devices = [
                Device(id="czujnik_temperatury", nazwa="Czujnik temperatury i wilgotności (NOUS E5)", typ="env_sensor", online=True, mqtt_topic="czujnik_temperatury", ieee_address="0xa4c1380cbaa9eaca", updated_at=ts),
                Device(id="czujnik_wibracji", nazwa="Czujnik wibracji (NOUS E14)", typ="vibration_sensor", online=True, mqtt_topic="czujnik_wibracji", ieee_address="0xa4c138d0573d40da", updated_at=ts),
                Device(id="przycisk", nazwa="Przycisk bezprzewodowy (SONOFF SNZB-01P)", typ="button", online=True, mqtt_topic="przycisk", ieee_address="0xf044d3fffe2a5a50", updated_at=ts),
                Device(id="listwa", nazwa="Listwa zasilająca 3-gniazdowa (NOUS A11Z)", typ="power_strip", online=True, mqtt_topic="listwa", ieee_address="0xa4c13879d97f71a0", updated_at=ts),
                Device(id="pilot", nazwa="Pilot 4-przyciskowy (Moes TS0044)", typ="remote", online=True, mqtt_topic="pilot", ieee_address="0xd878f0fffe0fa836", updated_at=ts),
                Device(id="czujnik_obecnosci", nazwa="Czujnik obecności (SONOFF SNZB-06P)", typ="occupancy_sensor", online=True, mqtt_topic="czujnik_obecności", ieee_address="0xf044d3fffe014c86", updated_at=ts),
                Device(id="zarowka_rgb", nazwa="Żarówka RGB (NOUS P3Z)", typ="rgb_bulb", online=True, mqtt_topic="zarowka_rgb", ieee_address="0xa4c138acc1df59e7", updated_at=ts),
                Device(id="heat_meter_1", nazwa="Ciepłomierz (ESP32-C6)", typ="heat_meter", online=False, mqtt_topic="heat_meter_1", updated_at=ts),
                Device(id="access_1", nazwa="Kontrola dostępu (ESP32-C6)", typ="access_keypad", online=False, mqtt_topic="access_1", updated_at=ts),
            ]
            db.add_all(initial_devices)
            logger.info("Zainicjalizowano urządzenia ZigBee.")

        if db.query(Automation).count() == 0:
            rules = [
                Automation(
                    nazwa="Pojedyncze kliknięcie przycisku przełącza gniazdo L1 listwy",
                    trigger=json.dumps({"device_id": "przycisk", "field": "action", "op": "==", "value": "single"}),
                    warunek=None,
                    akcja=json.dumps({"device_id": "listwa", "command": {"state_l1": "TOGGLE"}}),
                    aktywna=True,
                ),
                Automation(
                    nazwa="Wykrycie obecności włącza żarówkę",
                    trigger=json.dumps({"device_id": "czujnik_obecnosci", "field": "occupancy", "op": "==", "value": True}),
                    warunek=None,
                    akcja=json.dumps({"device_id": "zarowka_rgb", "command": {"state": "ON", "brightness": 254}}),
                    aktywna=True,
                ),
                Automation(
                    nazwa="Brak obecności wyłącza żarówkę",
                    trigger=json.dumps({"device_id": "czujnik_obecnosci", "field": "occupancy", "op": "==", "value": False}),
                    warunek=None,
                    akcja=json.dumps({"device_id": "zarowka_rgb", "command": {"state": "OFF"}}),
                    aktywna=True,
                ),
                Automation(
                    nazwa="Alarm wibracji zapala czerwone światło",
                    trigger=json.dumps({"device_id": "czujnik_wibracji", "field": "alarm_status", "op": "==", "value": "alarm"}),
                    warunek=None,
                    akcja=json.dumps({"device_id": "zarowka_rgb", "command": {"state": "ON", "color": {"r": 255, "g": 0, "b": 0}}}),
                    aktywna=True,
                ),
            ]
            db.add_all(rules)
            logger.info("Zainicjalizowano reguły automatyzacji.")

        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_initial_data()
    mqtt_manager.start()
    yield
    mqtt_manager.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Lokalny serwer aplikacyjny IoT (FastAPI + SQLite + Mosquitto)",
    lifespan=lifespan,
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(telemetry.router)
app.include_router(users.router)
app.include_router(access_reports.router)
app.include_router(automations.router)


@app.get("/api/health", tags=["System"])
def health_check():
    return {
        "status": "ok",
        "mqtt_connected": mqtt_manager.connected,
        "ts": utc_now_iso(),
    }


if os.path.isdir(settings.FRONTEND_DIST_PATH):
    app.mount("/", StaticFiles(directory=settings.FRONTEND_DIST_PATH, html=True), name="frontend")