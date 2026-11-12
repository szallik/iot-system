#!/usr/bin/env bash
# Instalacja całego systemu na czystym Raspberry Pi OS Lite (64-bit).
# Uruchomienie (jako zwykły użytkownik, np. iot_lab; skrypt sam używa sudo):
#   cd ~/iot-system && cp .env.example .env && ./deploy/bootstrap.sh
# Skrypt można uruchamiać wielokrotnie – kroki już wykonane są pomijane.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
Z2M_DIR=/opt/zigbee2mqtt
USER_NAME="$(id -un)"

log()  { printf '\n\033[1;34m=== %s ===\033[0m\n' "$*"; }
warn() { printf '\033[1;33mUWAGA: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mBŁĄD: %s\033[0m\n' "$*"; exit 1; }

[[ $EUID -ne 0 ]] || die "Uruchom jako zwykły użytkownik (nie przez sudo)."
[[ -f "$REPO_DIR/.env" ]] || die "Brak pliku .env. Wykonaj: cp .env.example .env"

set -a; source "$REPO_DIR/.env"; set +a
: "${ZIGBEE_SERIAL_PORT:?}"
Z2M_VERSION="${Z2M_VERSION:-2.14.2}"

# ---------------------------------------------------------------------------
log "1/7 Pakiety systemowe"
sudo apt-get update
sudo apt-get install -y git curl jq gettext-base build-essential \
    mosquitto mosquitto-clients nodejs npm python3-venv python3-pip

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 20 ]] || die "Node.js $NODE_MAJOR jest za stary (wymagany >= 20)."
command -v pnpm >/dev/null || sudo npm install -g pnpm
sudo usermod -aG dialout "$USER_NAME"

# ---------------------------------------------------------------------------
log "2/7 Mosquitto (port 1883, bez haseł)"
sudo install -m 644 "$REPO_DIR/config/mosquitto/iot.conf" /etc/mosquitto/conf.d/iot.conf
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

# ---------------------------------------------------------------------------
log "3/7 Zigbee2MQTT $Z2M_VERSION"
if [[ ! -f "$Z2M_DIR/package.json" ]]; then
    sudo mkdir -p "$Z2M_DIR"
    sudo chown "$USER_NAME:$USER_NAME" "$Z2M_DIR"
    if ! git clone --depth 1 --branch "$Z2M_VERSION" https://github.com/Koenkk/zigbee2mqtt.git "$Z2M_DIR"; then
        warn "Brak wersji $Z2M_VERSION – pobieram najnowszą."
        rm -rf "$Z2M_DIR"/* "$Z2M_DIR"/.[!.]* 2>/dev/null || true
        git clone --depth 1 https://github.com/Koenkk/zigbee2mqtt.git "$Z2M_DIR"
    fi
fi
if [[ ! -d "$Z2M_DIR/node_modules" ]]; then
    (cd "$Z2M_DIR" && pnpm install --frozen-lockfile)
fi

# ---------------------------------------------------------------------------
log "4/7 Konfiguracja Zigbee2MQTT"
if [[ ! -e "$ZIGBEE_SERIAL_PORT" ]]; then
    warn "Nie znaleziono $ZIGBEE_SERIAL_PORT. Dostępne porty:"
    ls -l /dev/serial/by-id/ 2>/dev/null || echo "(brak /dev/serial/by-id – czy koordynator jest podłączony?)"
    if [[ -e /dev/ttyUSB0 ]]; then
        warn "Używam /dev/ttyUSB0."
        ZIGBEE_SERIAL_PORT=/dev/ttyUSB0
    fi
fi
mkdir -p "$Z2M_DIR/data/external_converters"
cp "$REPO_DIR"/config/zigbee2mqtt/external_converters/*.js "$Z2M_DIR/data/external_converters/"
if [[ -f "$Z2M_DIR/data/configuration.yaml" ]]; then
    echo "configuration.yaml już istnieje – pozostawiam bez zmian."
else
    export ZIGBEE_SERIAL_PORT
    envsubst '${ZIGBEE_SERIAL_PORT}' \
        < "$REPO_DIR/config/zigbee2mqtt/configuration.yaml.template" \
        > "$Z2M_DIR/data/configuration.yaml"
    chmod 600 "$Z2M_DIR/data/configuration.yaml"
    echo "Utworzono $Z2M_DIR/data/configuration.yaml"
fi

# ---------------------------------------------------------------------------
log "5/7 Backend (Python venv)"
cd "$REPO_DIR/backend"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p data

# ---------------------------------------------------------------------------
log "6/7 Aplikacja webowa"
if [[ -f "$REPO_DIR/frontend/dist/index.html" ]]; then
    echo "frontend/dist już istnieje – pomijam budowanie."
else
    cd "$REPO_DIR/frontend"
    npm ci
    npm run build
fi

# ---------------------------------------------------------------------------
log "7/7 Usługi systemd"
NODE_BIN="$(command -v node)"
for unit in zigbee2mqtt iot-backend; do
    sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@NODE@|$NODE_BIN|g" \
        "$REPO_DIR/deploy/systemd/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable zigbee2mqtt iot-backend
sudo systemctl restart zigbee2mqtt iot-backend

sleep 5
log "Stan usług"
for s in mosquitto zigbee2mqtt iot-backend; do
    printf '%-14s %s\n' "$s" "$(systemctl is-active "$s" || true)"
done
IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

Aplikacja:         http://$IP:8000   (API: http://$IP:8000/docs)
Zigbee2MQTT (UI):  http://$IP:8080
Logi:              journalctl -u zigbee2mqtt -f    |    journalctl -u iot-backend -f
Test MQTT:         mosquitto_sub -t 'zigbee2mqtt/#' -v

Uwaga: grupa 'dialout' działa w nowej sesji SSH (dla usług systemd od razu).
EOF
