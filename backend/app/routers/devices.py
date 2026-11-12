import json
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import utc_now_iso
from app.models.entities import Device, Event
from app.schemas.api_schemas import DeviceResponse, DeviceCommandRequest
from app.services.mqtt_service import mqtt_manager

router = APIRouter(prefix="/api/devices", tags=["Urządzenia"])


def _serialize_device(dev: Device) -> DeviceResponse:
    try:
        state_dict = json.loads(dev.last_state or "{}")
    except Exception:
        state_dict = {}
    return DeviceResponse(
        id=dev.id,
        nazwa=dev.nazwa,
        typ=dev.typ,
        online=dev.online,
        mqtt_topic=dev.mqtt_topic,
        ieee_address=dev.ieee_address,
        last_state=state_dict,
        updated_at=dev.updated_at,
    )


@router.get("", response_model=List[DeviceResponse])
def list_devices(db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.id).all()
    return [_serialize_device(d) for d in devices]


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: str, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise AppError("DEVICE_NOT_FOUND", f"Urządzenie '{device_id}' nie istnieje.", 404)
    return _serialize_device(device)


@router.post("/{device_id}/commands")
def send_device_command(
    device_id: str, cmd: DeviceCommandRequest, db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise AppError("DEVICE_NOT_FOUND", f"Urządzenie '{device_id}' nie istnieje.", 404)

    mqtt_payload = cmd.to_mqtt_payload()
    if not mqtt_payload:
        raise AppError("EMPTY_COMMAND", "Nie przekazano żadnych parametrów sterujących.", 400)

    ok = mqtt_manager.publish_device_command(device_id, mqtt_payload)
    if not ok:
        raise AppError("MQTT_PUBLISH_ERROR", "Błąd publikacji rozkazu MQTT.", 503)

    db.add(
        Event(
            device_id=device.id,
            typ="command_sent",
            payload=json.dumps(mqtt_payload),
            ts=utc_now_iso(),
        )
    )
    db.commit()

    return {
        "status": "sent",
        "device_id": device.id,
        "mqtt_topic": f"zigbee2mqtt/{device.mqtt_topic or device.id}/set",
        "command": mqtt_payload,
    }