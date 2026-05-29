#!/usr/bin/env bash
# Generate mosquitto passwd file for SmartBin MQTT broker.
# Usage: ./scripts/setup-mqtt-auth.sh <username> <password>
set -euo pipefail

USER="${1:?usage: setup-mqtt-auth.sh <username> <password>}"
PASS="${2:?usage: setup-mqtt-auth.sh <username> <password>}"
PASSWD_FILE="$(dirname "$0")/../mosquitto/passwd"

# Use the official mosquitto image to run mosquitto_passwd (so we don't need it installed locally)
touch "$PASSWD_FILE"
docker run --rm -v "$(realpath "$PASSWD_FILE"):/mosquitto/passwd" eclipse-mosquitto:2 \
    mosquitto_passwd -b /mosquitto/passwd "$USER" "$PASS"

echo "✓ Wrote $PASSWD_FILE for user '$USER'"
echo "  Update .env with:"
echo "    MQTT_USERNAME=$USER"
echo "    MQTT_PASSWORD=$PASS"
