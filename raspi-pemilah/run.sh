#!/usr/bin/env bash
# Jalankan server pemilah di Raspberry Pi.
set -e
cd "$(dirname "$0")"

# aktifkan venv kalau ada
[ -d venv ] && source venv/bin/activate

# buat cert kalau belum ada (sekali)
if [ ! -f cert.pem ] || [ ! -f key.pem ]; then
  echo "[*] Sertifikat belum ada, membuat dulu..."
  bash gen-cert.sh
fi

# Port serial ESP32 di Raspi biasanya /dev/ttyUSB0 atau /dev/ttyACM0.
# Sesuaikan SERIAL_PORT di main.py bila perlu (cek: ls /dev/ttyUSB* /dev/ttyACM*)
python3 main.py
