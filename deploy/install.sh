#!/usr/bin/env bash
# Instalacja całego systemu jedną komendą, po pierwszym zalogowaniu na RPi przez SSH:
#
#   curl -fsSL https://raw.githubusercontent.com/LOGIN/iot-system/main/deploy/install.sh \
#     | bash -s -- https://github.com/LOGIN/iot-system.git
#
# Co robi:
#   1. instaluje git, pobiera repozytorium do ~/iot-system (lub aktualizuje istniejące),
#   2. jeśli nie ma .env – tworzy go z .env.example (bez haseł, środowisko laboratoryjne),
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
    echo "Utworzono .env z .env.example."
fi

echo "=== 3/3 Instalacja systemu ==="
./deploy/bootstrap.sh

echo
echo "Logowanie do aplikacji (konta testowe z backendu): admin / admin123"
