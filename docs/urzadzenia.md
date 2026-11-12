# Urządzenia ZigBee – nazwy i komunikaty

Nazwy w Zigbee2MQTT (`friendly_name`) są równe identyfikatorom urządzeń w backendzie, więc mapowanie aliasów (`DEVICE_ALIASES`) jest potrzebne tylko dla starych nazw.

Komunikaty w rozdz. 2 pochodzą z notatek z testów sprzed awarii karty SD (`notatki/komunikaty_iot_4.txt`, `komunikaty_v2_iot_5.txt`, `system_info_iot_notatka_2.txt`). Po odtworzeniu systemu trzeba je ponownie potwierdzić.

## 1. Lista urządzeń

| Nazwa (Z2M = backend) | Urządzenie | `model_id` w Z2M | Adres IEEE | Typ ZigBee |
|---|---|---|---|---|
| `czujnik_temperatury` | NOUS E5 | TS0201 | `0xa4c1380cbaa9eaca` | EndDevice |
| `czujnik_wibracji` | NOUS E14 | TS0601 | `0xa4c138d0573d40da` | EndDevice |
| `przycisk` | Sonoff SNZB-01P | SNZB-01P | `0xf044d3fffe2a5a50` | EndDevice |
| `listwa` | NOUS A11Z | TS011F | `0xa4c13879d97f71a0` | Router |
| `pilot` | Tuya TS0044 (Z2M: Moes XH-SY-04Z) | TS0044 | `0xd878f0fffe0fa836` | EndDevice |
| `czujnik_obecnosci` | Sonoff SNZB-06P | SNZB-06P | `0xf044d3fffe014c86` | Router |
| `zarowka_rgb` | NOUS P3Z | TS0505B | `0xa4c138acc1df59e7` | brak danych |
| `access_1` | moduł PIN (ESP32-C6) | `ESP32C6_PIN_PAD_V1` | po sparowaniu | EndDevice |
| `heat_meter_1` | ciepłomierz (ESP32-C6) | do ustalenia | po sparowaniu | do ustalenia |
| (koordynator) | NOUS E16, Z-Stack 3.x, firmware 20240710 | – | `0x00124b00388a1026` | Coordinator |

Uwaga: przed awarią Z2M stosował nazwę `czujnik_obecności` (z polskim znakiem). Obecnie obowiązuje `czujnik_obecnosci`.

## 2. Komunikaty zarejestrowane przed awarią

Temat: `zigbee2mqtt/<nazwa>`.

**czujnik_temperatury**
```json
{"battery":100,"humidity":68.01,"linkquality":193,"temperature":27.58}
```
Raport tylko przy zmianie wartości (w jednej z prób brak komunikatu przez 60 s).

**czujnik_wibracji** – `alarm_status`: `normal` / `alarm`
```json
{"alarm_status":"alarm","battery_state":"high","linkquality":193,"sensitivity":"middle","silent_mode":"ON"}
```

**przycisk** – `action`: `single`, `double`, `long`
```json
{"action":"single","battery":100,"linkquality":196,"update":{"installed_version":8704,"latest_release_notes":null,"latest_source":null,"latest_version":8704,"state":"idle"},"voltage":3100}
```
Komunikaty okresowe (bateria) nie zawierają pola `action`.

**pilot** – zarejestrowane `action`: `1_single`, `1_double`, `1_hold`, `2_single`, `3_single`, `4_single`, `4_double`
```json
{"action":"1_single","linkquality":182}
```

**listwa** – zmiany `state_l1`…`state_l3` zarejestrowane (polecenia wysyłano wtedy z interfejsu Z2M, nie z backendu)
```json
{"child_lock":"UNLOCK","countdown_l1":0,"countdown_l2":0,"countdown_l3":0,"current":0.13,"energy":0,"indicator_mode":"off/on","linkquality":196,"power":0,"power_on_behavior_l1":"previous","power_on_behavior_l2":"previous","power_on_behavior_l3":"previous","state_l1":"ON","state_l2":"OFF","state_l3":"OFF","switch_type_button":"release","voltage":238}
```

**czujnik_obecnosci** – zarejestrowano tylko konfigurację, **bez zdarzeń obecności**
```json
{"occupancy_sensitivity":"high","occupancy_timeout":60,"update":{"installed_version":4102,"latest_version":4101,"state":"idle"}}
```

**zarowka_rgb** – po `mosquitto_pub -t 'zigbee2mqtt/zarowka_rgb/set' -m '{"state":"OFF"}'`
```json
{"brightness":254,"linkquality":196,"state":"OFF"}
```

## 3. Polecenia do sprawdzenia po odtworzeniu

```bash
mosquitto_pub -t 'zigbee2mqtt/zarowka_rgb/set' -m '{"state":"ON","color":{"r":255,"g":0,"b":0}}'
mosquitto_pub -t 'zigbee2mqtt/zarowka_rgb/set' -m '{"state":"TOGGLE"}'
mosquitto_pub -t 'zigbee2mqtt/listwa/set'      -m '{"state_l2":"ON"}'
mosquitto_sub -t 'zigbee2mqtt/czujnik_obecnosci' -v     # wejście / wyjście z zasięgu
```

Pełna lista testów: [automatyka.md](automatyka.md), rozdz. 9.
