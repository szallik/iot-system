#!/usr/bin/env bash
# Kopia stanu sieci ZigBee i bazy danych do ~/backups/iot-<data>.tgz
# Archiwum zawiera klucz sieci ZigBee – trzymać poza repozytorium.
# Pobranie na laptopa (z laptopa):
#   scp iot_lab@IOTlabGR2.local:~/backups/iot-*.tgz .
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
Z2M_DATA=/opt/zigbee2mqtt/data
OUT_DIR="$HOME/backups"
STAMP="$(date +%Y%m%d-%H%M)"
mkdir -p "$OUT_DIR"

FILES=()
for f in configuration.yaml coordinator_backup.json database.db state.json; do
    [[ -f "$Z2M_DATA/$f" ]] && FILES+=("$Z2M_DATA/$f")
done
[[ -f "$REPO_DIR/backend/data/iot_system.db" ]] && FILES+=("$REPO_DIR/backend/data/iot_system.db")

[[ ${#FILES[@]} -gt 0 ]] || { echo "Brak plików do skopiowania."; exit 1; }
tar -czf "$OUT_DIR/iot-$STAMP.tgz" "${FILES[@]}" 2>/dev/null
chmod 600 "$OUT_DIR/iot-$STAMP.tgz"
echo "Zapisano $OUT_DIR/iot-$STAMP.tgz:"
tar -tzf "$OUT_DIR/iot-$STAMP.tgz"
