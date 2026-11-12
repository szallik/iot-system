#!/usr/bin/env bash
# Instalacja całego systemu jedną komendą, po pierwszym zalogowaniu na RPi przez SSH:
#
#   curl -fsSL https://raw.githubusercontent.com/LOGIN/iot-system/main/deploy/install.sh \
#     | bash -s -- https://github.com/LOGIN/iot-system.git
#
# Co robi:
#   1. instaluje git, pobiera repozytorium do ~/iot-system (lub aktualizuje istniejące),
#   2. jeśli nie ma .env – tworzy go z .env.example i losuje wszystkie hasła,
#   3. uruchamia deploy/bootstrap.sh (pakiety, Mosquitto, Zigbee2MQTT, backend, frontend, usługi).
set -euo pipefail

REPO_URL="${1:-}"
TARGET="$HOME/iot-system"

[[ $EUID -ne 0 ]] || { echo "Uruchom jako zwykły użytkownik (np. iot_lab), nie jako root."; exit 1; }

echo "=== 1/3 Repozytorium ==="
command -v git >/dev/null || { sudo apt-get update && sudo apt-get install -y git; }
if [[ -d "$TARGET/.git" ]]; then
    git -C "$TARGET" pull --ff-only
else
    [[ -n "$REPO_URL" ]] || { echo "Podaj adres repozytorium: ... | bash -s -- https://github.com/LOGIN/iot-system.git"; exit 1; }
    git clone "$REPO_URL" "$TARGET"
fi
cd "$TARGET"

echo "=== 2/3 Plik .env ==="
if [[ -f .env ]]; then
    echo ".env już istnieje – pozostawiam bez zmian."
else
    cp .env.example .env
    rnd() { python3 -c "import secrets; print(secrets.token_hex($1))"; }
    sed -i \
        -e "s|^MQTT_PASSWORD=.*|MQTT_PASSWORD=$(rnd 12)|" \
        -e "s|^Z2M_MQTT_PASSWORD=.*|Z2M_MQTT_PASSWORD=$(rnd 12)|" \
        -e "s|^MQTT_TEST_PASSWORD=.*|MQTT_TEST_PASSWORD=$(rnd 12)|" \
        -e "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(rnd 32)|" \
        .env
    chmod 600 .env
    echo "Utworzono .env z losowymi hasłami."
fi

echo "=== 3/3 Instalacja systemu ==="
./deploy/bootstrap.sh

echo
echo "Hasła MQTT (zapisz je w bezpiecznym miejscu, NIE w repozytorium):"
grep -E '^(MQTT_USERNAME|MQTT_PASSWORD|Z2M_MQTT_USER|Z2M_MQTT_PASSWORD|MQTT_TEST_USER|MQTT_TEST_PASSWORD)=' .env
