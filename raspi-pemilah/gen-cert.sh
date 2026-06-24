#!/usr/bin/env bash
# Buat sertifikat HTTPS self-signed (WAJIB supaya kamera HP bisa diakses).
# Jalankan SEKALI di Raspberry Pi: bash gen-cert.sh
set -e
cd "$(dirname "$0")"

# Deteksi IP LAN Raspi otomatis
LAN_IP=$(python3 -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0]);s.close()" 2>/dev/null || hostname -I | awk '{print $1}')
echo "[*] IP LAN Raspi terdeteksi: $LAN_IP"

openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 \
  -subj "/CN=$LAN_IP" \
  -addext "subjectAltName=IP:$LAN_IP,IP:127.0.0.1,DNS:localhost" 2>/dev/null \
  || openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=$LAN_IP"

echo "[+] cert.pem & key.pem dibuat."
echo "[+] Nanti buka dari HP: https://$LAN_IP:8000/scan"
