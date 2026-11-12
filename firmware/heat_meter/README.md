# Ciepłomierz (ESP32-C6)

Firmware do przygotowania (osoba 6). Stan: **planowane**.

Wymagania uzgodnione z backendem (`backend/app/services/mqtt_service.py`, urządzenie `heat_meter_1`):

| Pole | Jednostka | Opis |
|---|---|---|
| `temperature_1` | °C | temperatura zasilania (DS18B20) |
| `temperature_2` | °C | temperatura powrotu (DS18B20) |
| `flow` | l/min | przepływ z czujnika turbinowego |
| `power` | W | moc cieplna |
| `energy` | kWh | energia skumulowana |

Komunikację ZigBee należy oprzeć na rozwiązaniu sprawdzonym w module PIN (`firmware/access_control/`, konwerter `config/zigbee2mqtt/external_converters/access_1.js`). Do backendu trafia temat `zigbee2mqtt/heat_meter_1`.
