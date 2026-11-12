# System IoT dla automatyki biurowej

Projekt zespołowy z przedmiotu *Technologie Internetu Rzeczy* (Politechnika Wrocławska). Lokalny system na Raspberry Pi 4: urządzenia ZigBee i dwa autorskie moduły ESP32-C6 (kontrola dostępu PIN, ciepłomierz), Zigbee2MQTT, broker Mosquitto, backend FastAPI z bazą SQLite oraz aplikacja webowa React.

```
Urządzenia ZigBee / ESP32-C6 ──ZigBee──▶ NOUS E16 ──USB──▶ Zigbee2MQTT ──MQTT──▶ Mosquitto ──MQTT──▶ Backend ──HTTP──▶ Aplikacja
                                                                                                      │
                                                                                                    SQLite
```

## Struktura

| Katalog | Zawartość | Pochodzenie |
|---|---|---|
| `backend/` | API, logika, MQTT, baza danych (FastAPI, SQLAlchemy, paho-mqtt) | repozytorium `AKaczucha/iot-room-system` (commit `8aae479`) |
| `frontend/` | aplikacja webowa (React, TypeScript, Vite) | repozytorium `krys2kk/Projekt-IoT` (commit `77d6d29`) |
| `firmware/access_control/` | firmware modułu PIN (`key_pad.ino`) | `krys2kk/Projekt-IoT` |
| `firmware/heat_meter/` | firmware ciepłomierza | do przygotowania |
| `config/mosquitto/` | konfiguracja brokera | nowe |
| `config/zigbee2mqtt/` | szablon `configuration.yaml`, konwerter `access_1.js` | nowe / konwerter ze specyfikacji modułu PIN |
| `deploy/` | `bootstrap.sh`, `backup.sh`, usługi `systemd` | nowe |
| `docs/` | instalacja, urządzenia, logika automatyki, specyfikacja modułu PIN | nowe / `specification_report.md` |

## Szybki start na Raspberry Pi

Pełna procedura: [docs/instalacja_rpi.md](docs/instalacja_rpi.md).

```bash
git clone <adres-repozytorium> ~/iot-system
cd ~/iot-system
cp .env.example .env
./deploy/bootstrap.sh
```

Po instalacji: aplikacja `http://<IP>:8000`, dokumentacja API `http://<IP>:8000/docs`, interfejs Zigbee2MQTT `http://<IP>:8080`.

## Praca lokalna (bez Raspberry Pi)

```bash
# backend + symulator telemetrii (wymaga lokalnego brokera MQTT, np. mosquitto)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
MQTT_BROKER_HOST=localhost .venv/bin/uvicorn app.main:app --reload
.venv/bin/python simulate_mqtt.py

# frontend z przekierowaniem /api na backend
cd frontend && npm ci && npm run dev
```

## Dokumentacja

- [docs/instalacja_rpi.md](docs/instalacja_rpi.md) – odtworzenie serwera od zera, weryfikacja, kopie zapasowe
- [docs/urzadzenia.md](docs/urzadzenia.md) – nazwy, adresy IEEE, zarejestrowane komunikaty MQTT
- [docs/automatyka.md](docs/automatyka.md) – tryby systemu i reguły automatyzacji (wersja 2, MVP)
- [docs/modul_pin_specyfikacja.md](docs/modul_pin_specyfikacja.md) – specyfikacja modułu kontroli dostępu
- [frontend/README.md](frontend/README.md) – szczegóły aplikacji webowej

## Stan

| Obszar | Stan |
|---|---|
| Backend: API, modele, obsługa MQTT, symulator | zaimplementowane |
| Aplikacja webowa | zaimplementowane |
| Firmware i konwerter modułu PIN | zaimplementowane |
| Skrypt instalacji, konfiguracja Mosquitto i Z2M | przygotowane, nieprzetestowane na sprzęcie |
| Tryby systemu i reguły R1–R14 (`docs/automatyka.md`) | planowane |
| Wspólny kontrakt PIN backend ↔ moduł (`credential` / `request_id`) | planowane – backend oczekuje obecnie `presented_value` |
| Ciepłomierz | planowane |
| Testy z rzeczywistymi urządzeniami po odtworzeniu RPi | planowane |

## Zasady pracy

- Gałąź `main` zawiera wersję działającą na RPi. Zmiany przez osobne gałęzie (`backend/...`, `frontend/...`, `firmware/...`) i pull requesty.
- Broker MQTT działa bez haseł (środowisko laboratoryjne). Plik `.env` jest lokalny dla RPi i nie trafia do repozytorium.
- Zmiana nazw urządzeń, tematów MQTT lub API wymaga aktualizacji `docs/` w tym samym pull requeście.

## Licencja

Kod w `frontend/` i `firmware/access_control/` jest udostępniony na licencji MIT (pliki `LICENSE` w tych katalogach). Licencję całego repozytorium ustala zespół.
