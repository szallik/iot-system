import json
import logging
import time
import paho.mqtt.client as mqtt
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import utc_now_iso
from app.models.entities import Device, Measurement, Event
from app.services.access_service import verify_pin_and_log, DECISION_GRANTED
from app.services.automation_service import process_automations

logger = logging.getLogger("iot.mqtt")

METRIC_UNITS: Dict[str, str] = {
    "temperature": "°C",
    "temperature_1": "°C",
    "temperature_2": "°C",
    "humidity": "%",
    "flow": "l/min",
    "power": "W",
    "energy": "kWh",
    "current": "A",
    "battery": "%",
}


class MQTTManager:
    def __init__(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="iot-fastapi-backend"
        )
        if settings.MQTT_USERNAME:
            self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.connected = False

    def start(self):
        try:
            self.client.connect_async(
                settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, keepalive=60
            )
            self.client.loop_start()
            logger.info(
                f"Uruchomiono klienta MQTT -> {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}"
            )
        except Exception as e:
            logger.error(f"Błąd połączenia MQTT: {e}")

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            logger.error(f"Błąd rozłączania MQTT: {e}")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self.connected = True
            base = settings.MQTT_BASE_TOPIC
            client.subscribe(f"{base}/+")
            client.subscribe(f"{base}/bridge/devices")
            client.subscribe(f"{base}/bridge/state")
            logger.info(f"Połączono z MQTT. Subskrypcja: {base}/+")
        else:
            logger.error(f"Błąd połączenia z brokerem MQTT, kod: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        logger.warning(f"Rozłączono z MQTT (kod={reason_code}). Ponawianie...")

    def resolve_device_id(self, raw_topic_name: str) -> str:
        return settings.DEVICE_ALIASES.get(raw_topic_name, raw_topic_name)

    def publish_device_command(self, device_id: str, payload: Dict[str, Any]) -> bool:
        db = SessionLocal()
        try:
            device = db.query(Device).filter(Device.id == device_id).first()
            target_topic_name = device.mqtt_topic if (device and device.mqtt_topic) else device_id
        finally:
            db.close()

        topic = f"{settings.MQTT_BASE_TOPIC}/{target_topic_name}/set"
        try:
            msg_info = self.client.publish(topic, json.dumps(payload), qos=1)
            logger.info(f"MQTT PUBLISH -> {topic}: {payload}")
            return msg_info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Błąd publikacji na {topic}: {e}")
            return False

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode("utf-8", errors="ignore").strip()
        if not raw_payload or topic.endswith("/set") or topic.endswith("/get"):
            return

        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            return

        base_prefix = f"{settings.MQTT_BASE_TOPIC}/"
        if not topic.startswith(base_prefix):
            return

        subtopic = topic[len(base_prefix):]

        db = SessionLocal()
        try:
            if subtopic == "bridge/devices" and isinstance(data, list):
                self._sync_bridge_devices(db, data)
                return
            if subtopic.startswith("bridge/"):
                return

            self._handle_device_telemetry(db, subtopic, data)
        except Exception as e:
            logger.exception(f"Błąd przetwarzania wiadomości z {topic}: {e}")
            db.rollback()
        finally:
            db.close()

    def _sync_bridge_devices(self, db: Session, devices_list: list):
        ts = utc_now_iso()
        for item in devices_list:
            if item.get("type") == "Coordinator":
                continue
            friendly_name = item.get("friendly_name")
            ieee = item.get("ieee_address")
            canonical_id = self.resolve_device_id(friendly_name)

            device = db.query(Device).filter(Device.id == canonical_id).first()
            if not device and ieee:
                alias_by_ieee = settings.DEVICE_ALIASES.get(ieee)
                if alias_by_ieee:
                    canonical_id = alias_by_ieee
                    device = db.query(Device).filter(Device.id == canonical_id).first()

            if device:
                device.mqtt_topic = friendly_name
                device.ieee_address = ieee
                device.online = not item.get("disabled", False)
                device.updated_at = ts
            else:
                db.add(
                    Device(
                        id=canonical_id,
                        nazwa=friendly_name,
                        typ="zigbee_device",
                        online=True,
                        mqtt_topic=friendly_name,
                        ieee_address=ieee,
                        last_state="{}",
                        updated_at=ts,
                    )
                )
        db.commit()

    def _handle_device_telemetry(
        self, db: Session, raw_device_topic: str, payload: Dict[str, Any]
    ):
        ts = utc_now_iso()
        device_id = self.resolve_device_id(raw_device_topic)

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            device = Device(
                id=device_id,
                nazwa=raw_device_topic,
                typ="zigbee_device",
                online=True,
                mqtt_topic=raw_device_topic,
                last_state="{}",
                updated_at=ts,
            )
            db.add(device)
            db.flush()

        device.online = True
        device.mqtt_topic = raw_device_topic
        device.updated_at = ts

        try:
            current_state = json.loads(device.last_state or "{}")
        except Exception:
            current_state = {}
        previous_state = dict(current_state)
        # PIN nie trafia do last_state (ani do bazy)
        current_state.update(
            {k: v for k, v in payload.items() if k not in ("credential", "presented_value")}
        )
        device.last_state = json.dumps(current_state)

        # 1a. Kontrola PIN – kontrakt modułu access_1 (docs/modul_pin_specyfikacja.md):
        #     żądanie {"request_id", "credential"} -> odpowiedź {"request_id", "granted"}
        if payload.get("credential") is not None and payload.get("request_id") is not None:
            request_id = payload["request_id"]
            now_ts = time.time()
            # Z2M (cache_state) może powtórzyć ostatnie żądanie w kolejnym komunikacie
            # urządzenia – to samo request_id w ciągu 30 s traktujemy jako duplikat.
            if (
                previous_state.get("last_request_id") == request_id
                and now_ts - float(previous_state.get("last_request_ts", 0)) < 30
            ):
                db.commit()
                return
            current_state["last_request_id"] = request_id
            current_state["last_request_ts"] = now_ts
            device.last_state = json.dumps(current_state)
            decision_code, _, _ = verify_pin_and_log(
                db=db,
                device_id=device_id,
                raw_presented_value=str(payload["credential"]),
            )
            reply_topic = f"{settings.MQTT_BASE_TOPIC}/{raw_device_topic}/set"
            self.client.publish(
                reply_topic,
                json.dumps({"request_id": request_id, "granted": decision_code == DECISION_GRANTED}),
                qos=1,
            )
            return

        # 1b. Kontrola PIN – dawny format (presented_value -> multistate_output)
        if "presented_value" in payload and payload["presented_value"] is not None:
            decision_code, _, _ = verify_pin_and_log(
                db=db,
                device_id=device_id,
                raw_presented_value=payload["presented_value"],
            )
            reply_topic = f"{settings.MQTT_BASE_TOPIC}/{raw_device_topic}/set"
            self.client.publish(
                reply_topic,
                json.dumps({"multistate_output": decision_code}),
                qos=1,
            )
            return

        # 2. Pomiary liczbowe
        for metric_key, unit in METRIC_UNITS.items():
            if metric_key in payload and isinstance(payload[metric_key], (int, float)):
                db.add(
                    Measurement(
                        device_id=device_id,
                        metric=metric_key,
                        value=float(payload[metric_key]),
                        unit=unit,
                        ts=ts,
                    )
                )

        if "voltage" in payload and isinstance(payload["voltage"], (int, float)):
            unit_v = "V" if device.typ == "power_strip" or device_id == "listwa" else "mV"
            db.add(
                Measurement(
                    device_id=device_id,
                    metric="voltage",
                    value=float(payload["voltage"]),
                    unit=unit_v,
                    ts=ts,
                )
            )

        # 3. Obecność
        if "occupancy" in payload and isinstance(payload["occupancy"], bool):
            db.add(
                Measurement(
                    device_id=device_id,
                    metric="occupancy",
                    value=1.0 if payload["occupancy"] else 0.0,
                    unit="bool",
                    ts=ts,
                )
            )
            db.add(
                Event(
                    device_id=device_id,
                    typ="occupancy",
                    payload=json.dumps(
                        {"occupancy": payload["occupancy"], "illumination": payload.get("illumination")}
                    ),
                    ts=ts,
                )
            )

        # 4. Wibracje
        if "alarm_status" in payload:
            is_alarm = payload["alarm_status"] == "alarm"
            db.add(
                Measurement(
                    device_id=device_id,
                    metric="vibration",
                    value=1.0 if is_alarm else 0.0,
                    unit="bool",
                    ts=ts,
                )
            )
            if is_alarm:
                db.add(
                    Event(
                        device_id=device_id,
                        typ="vibration_alarm",
                        payload=json.dumps(payload),
                        ts=ts,
                    )
                )

        # 5. Przycisk / pilot
        if "action" in payload and payload["action"]:
            db.add(
                Event(
                    device_id=device_id,
                    typ="action",
                    payload=json.dumps({"action": payload["action"]}),
                    ts=ts,
                )
            )

        db.commit()

        # 6. Ewaluacja automatyzacji
        process_automations(
            db=db,
            source_device_id=device_id,
            incoming_payload=payload,
            publish_command_cb=self.publish_device_command,
        )


mqtt_manager = MQTTManager()