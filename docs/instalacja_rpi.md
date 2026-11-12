# Instalacja Raspberry Pi od zera

Procedura odtworzenia serwera po awarii karty SD. Wszystkie nazwy (host, użytkownik, sieć) są takie same jak przed awarią, więc notatki i komendy z wcześniejszych testów pozostają aktualne.

**Stan procedury:** przygotowana, **nieprzetestowana** na sprzęcie. Wyniki pierwszego uruchomienia należy dopisać w rozdz. 7.

## 1. Ustalone nazwy

| Parametr | Wartość |
|---|---|
| System | Raspberry Pi OS Lite (64-bit), Debian 13 „trixie” |
| Nazwa hosta | `IOTlabGR2` |
| Użytkownik | `iot_lab` |
| Sieć Wi-Fi | `IOT_wifi` |
| Kraj Wi-Fi | `PL` |
| Strefa czasowa / klawiatura | `Europe/Warsaw` / `pl` |
| Katalog repozytorium na RPi | `/home/iot_lab/iot-system` |
| Katalog Zigbee2MQTT | `/opt/zigbee2mqtt` |
| Porty | MQTT `1883`, aplikacja `8000`, interfejs Z2M `8080` |

## 2. Nagranie karty (Raspberry Pi Imager, Windows)

1. **Urządzenie:** Raspberry Pi 4.
2. **System:** *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**.
3. **Nośnik:** nowa karta SD.
4. **Ustawienia (customisation), przed zapisem:**
   - hostname: `IOTlabGR2`,
   - użytkownik i hasło: `iot_lab` + hasło ustalone w zespole,
   - Wi-Fi: SSID `IOT_wifi`, hasło sieci, kraj `PL`,
   - lokalizacja: `Europe/Warsaw`, klawiatura `pl`,
   - **SSH: włączone**, uwierzytelnianie hasłem. Opcjonalnie wklej klucz publiczny z Maca (`cat ~/.ssh/id_ed25519.pub`), wtedy logowanie odbywa się bez hasła.
5. Zapis i weryfikacja (kilka minut). Kartę włożyć do RPi, podłączyć koordynator NOUS E16 do USB i dopiero wtedy zasilanie.

Pierwszy rozruch trwa 1–2 minuty (RPi rozszerza system plików i restartuje się).

## 3. Połączenie z Maca

```bash
# Usunięcie starego klucza hosta – nowy system ma nowy klucz, bez tego ssh odmówi połączenia
ssh-keygen -R IOTlabGR2.local
ssh-keygen -R iotlabgr2.local

ping -c 3 IOTlabGR2.local          # czy RPi jest widoczne w sieci (mDNS)
ssh iot_lab@IOTlabGR2.local
```

Jeśli `.local` nie działa, adres IP można odczytać na RPi podłączonym do monitora poleceniem `hostname -I`. Przed awarią RPi miało adres `192.168.0.107`, ale jest on przydzielany przez DHCP i może się zmienić.

## 4. Repozytorium i plik `.env`

Wariant A – repozytorium publiczne:
```bash
git clone https://github.com/<konto>/iot-system.git ~/iot-system
```

Wariant B – repozytorium prywatne lub brak dostępu z RPi: kopia z Maca.
```bash
# na Macu, w katalogu nadrzędnym repozytorium
scp -r iot-system iot_lab@IOTlabGR2.local:~/
```

Plik z hasłami (tylko na RPi, nie w gicie):
```bash
cd ~/iot-system
cp .env.example .env
nano .env        # ustawić hasła MQTT i JWT_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"   # wartość dla JWT_SECRET_KEY
```

Skrócenie czasu na zajęciach: frontend można zbudować wcześniej na Macu i skopiować, bo to statyczne pliki niezależne od architektury. `bootstrap.sh` pominie wtedy budowanie.
```bash
# na Macu
cd iot-system/frontend && npm ci && npm run build
scp -r dist iot_lab@IOTlabGR2.local:~/iot-system/frontend/
```

## 5. Instalacja

```bash
cd ~/iot-system
./deploy/bootstrap.sh
```

Skrypt wykonuje kolejno:

1. instalację pakietów (`mosquitto`, `nodejs`, `npm`, `python3-venv`, `git` i narzędzia) oraz `pnpm`;
2. konfigurację Mosquitto: konta z `.env`, wyłączenie dostępu anonimowego, plik `~/.config/mosquitto_sub` i `~/.config/mosquitto_pub` z danymi konta testowego;
3. pobranie Zigbee2MQTT w wersji z `.env` (2.14.2) do `/opt/zigbee2mqtt` i instalację zależności;
4. utworzenie `data/configuration.yaml` z szablonu i skopiowanie konwertera modułu PIN do `data/external_converters/`;
5. środowisko Python backendu (`backend/.venv`);
6. budowę aplikacji webowej (jeśli brak `frontend/dist`);
7. usługi `systemd` `zigbee2mqtt` i `iot-backend` z autostartem.

Najdłużej trwają kroki 1 i 3. Skrypt można uruchomić ponownie po błędzie – wykonane kroki są pomijane.

## 6. Weryfikacja i parowanie

```bash
systemctl is-active mosquitto zigbee2mqtt iot-backend      # trzy razy "active"
journalctl -u zigbee2mqtt -n 50 --no-pager                  # szukać "Zigbee2MQTT started!"
mosquitto_sub -t 'zigbee2mqtt/bridge/state' -C 1 -v         # {"state":"online"}
curl -s localhost:8000/api/health                           # "mqtt_connected": true
```

Klucz sieci ZigBee był zapisany tylko w utraconym `configuration.yaml`, dlatego **należy się spodziewać ponownego parowania wszystkich urządzeń**.

Parowanie:
```bash
# zezwolenie na dołączanie przez 254 s (alternatywnie przycisk "Permit join" w http://<IP>:8080)
mosquitto_pub -t 'zigbee2mqtt/bridge/request/permit_join' -m '{"time":254}'
journalctl -u zigbee2mqtt -f          # obserwacja dołączających urządzeń
```

Urządzenia wprowadza się w tryb parowania zgodnie z ich instrukcjami (zwykle przytrzymanie przycisku do migania diody). Sekcja `devices` w konfiguracji przypisuje nazwy po adresach IEEE, więc urządzenia powinny od razu pojawić się pod nazwami z tabeli w [urzadzenia.md](urzadzenia.md). Sprawdzenie:

```bash
mosquitto_sub -t 'zigbee2mqtt/bridge/devices' -C 1 \
  | jq -r '.[] | "\(.friendly_name) | \(.model_id) | \(.ieee_address)"'
```

Po sparowaniu wszystkiego – kopia zapasowa (rozdz. 8).

## 7. Wyniki pierwszej instalacji

Do uzupełnienia po zajęciach: data, czas trwania kroków, napotkane błędy, wersje (`node -v`, `python3 -V`, wersja Z2M z logu).

## 8. Kopia zapasowa

```bash
~/iot-system/deploy/backup.sh                 # na RPi -> ~/backups/iot-<data>.tgz
scp iot_lab@IOTlabGR2.local:~/backups/iot-*.tgz .   # na Macu
```

Archiwum zawiera `configuration.yaml` (z kluczem sieci), `coordinator_backup.json`, bazę urządzeń Z2M i bazę backendu. **Nie umieszczać go w repozytorium.** Kopię robimy po każdym parowaniu nowego urządzenia.

## 9. Typowe problemy

| Objaw | Sprawdzenie / rozwiązanie |
|---|---|
| `ssh: REMOTE HOST IDENTIFICATION HAS CHANGED` | `ssh-keygen -R IOTlabGR2.local` |
| Z2M nie startuje, błąd portu szeregowego | `ls -l /dev/serial/by-id/`; poprawić `ZIGBEE_SERIAL_PORT` w `.env` i `serial.port` w `/opt/zigbee2mqtt/data/configuration.yaml`, potem `sudo systemctl restart zigbee2mqtt` |
| Z2M zgłasza błąd sekcji `devices` | usunąć sekcję `devices` z `configuration.yaml`, nazwy nadać po sparowaniu w interfejsie Z2M |
| `Connection Refused: not authorised` w `mosquitto_sub` | podać konto jawnie: `mosquitto_sub -h localhost -u tester -P <hasło> -t '#' -v` |
| `mqtt_connected: false` w `/api/health` | `journalctl -u iot-backend -n 50`; zgodność `MQTT_USERNAME`/`MQTT_PASSWORD` z kontem w Mosquitto |
| Konwerter modułu PIN nie jest ładowany | w logu Z2M przy starcie; sprawdzić `advanced.enable_external_js: true` i plik w `data/external_converters/` |
