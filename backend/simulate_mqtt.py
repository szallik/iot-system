import os
import time
import json
import random
import paho.mqtt.client as mqtt

# Adres i konto brokera ze zmiennych środowiskowych (np. po `set -a; source ../.env; set +a`)
BROKER = os.getenv("MQTT_BROKER_HOST", "localhost")
PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
USER = os.getenv("MQTT_TEST_USER", "")
PASSWORD = os.getenv("MQTT_TEST_PASSWORD", "")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test_simulator")
if USER:
    client.username_pw_set(USER, PASSWORD)
client.connect(BROKER, PORT, 60)
client.loop_start()

print("Uruchomiono symulator telemetrii IoT. Wciśnij Ctrl+C aby zatrzymać.")
accumulated_heat_energy = 0.1250

try:
    while True:
        temp = round(21.0 + random.uniform(-1.5, 2.5), 2)
        hum = round(45.0 + random.uniform(-5.0, 8.0), 2)
        client.publish(
            "zigbee2mqtt/czujnik_temperatury",
            json.dumps({"temperature": temp, "humidity": hum, "battery": 95}),
        )

        t_in = round(48.0 + random.uniform(-1.0, 2.0), 2)
        t_out = round(41.0 + random.uniform(-1.0, 1.5), 2)
        flow = round(2.0 + random.uniform(-0.3, 0.4), 2)
        power = int((t_in - t_out) * flow * 69.7)
        accumulated_heat_energy += round(power * (5 / 3600) / 1000, 5)

        heat_payload = {
            "temperature_1": t_in,
            "temperature_2": t_out,
            "flow": flow,
            "power": power,
            "energy": round(accumulated_heat_energy, 4),
            "battery": 88,
            "voltage": 3620,
        }
        client.publish("zigbee2mqtt/heat_meter_1", json.dumps(heat_payload))

        client.publish(
            "zigbee2mqtt/listwa",
            json.dumps({"power": random.choice([0, 45, 120]), "voltage": 238, "current": 0.5}),
        )

        time.sleep(5)
except KeyboardInterrupt:
    client.loop_stop()
    client.disconnect()
    print("Zatrzymano symulator.")