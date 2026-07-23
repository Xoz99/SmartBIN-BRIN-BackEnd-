#!/usr/bin/env bash
#
# find-lora-port.sh — deteksi otomatis port serial board LoRa.
#
# Cara pakai:
#   1. CABUT dulu board LoRa dari USB.
#   2. Jalankan:  ./find-lora-port.sh
#   3. Saat diminta, COLOK board LoRa-nya.
#
# Script akan membandingkan daftar port sebelum & sesudah dicolok,
# lalu menampilkan port baru yang muncul (itu board LoRa-mu).
# Kalau ketemu, ditawarkan untuk langsung update SERIAL_PORT di .env.
#
# Jalan di macOS (cu.*) maupun Linux/Raspi (ttyUSB*/ttyACM*).

set -euo pipefail

# ─── Lokasi .env (folder yang sama dengan script ini) ────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# ─── Tentukan pola port sesuai OS ────────────────────────────────────────────
case "$(uname -s)" in
  Darwin)  PATTERN=(/dev/cu.*) ; OS="macOS" ;;
  Linux)   PATTERN=(/dev/ttyUSB* /dev/ttyACM*) ; OS="Linux" ;;
  *)       echo "OS tidak dikenali: $(uname -s)"; exit 1 ;;
esac

# Ambil snapshot daftar port (baris kosong kalau tidak ada).
list_ports() {
  ls -1 "${PATTERN[@]}" 2>/dev/null || true
}

echo "OS terdeteksi : $OS"
echo "Pola port     : ${PATTERN[*]}"
echo

# ─── Snapshot SEBELUM colok ──────────────────────────────────────────────────
before="$(list_ports)"
echo "Port terpasang sekarang:"
if [ -z "$before" ]; then echo "  (tidak ada)"; else echo "$before" | sed 's/^/  /'; fi
echo
echo ">>> COLOK board LoRa sekarang, lalu tekan ENTER..."
read -r _

# Beri waktu driver USB mendaftarkan device.
sleep 2

# ─── Snapshot SESUDAH colok ──────────────────────────────────────────────────
after="$(list_ports)"

# Cari port yang ADA di 'after' tapi TIDAK ada di 'before'.
new_ports="$(comm -13 <(echo "$before" | sort) <(echo "$after" | sort) | sed '/^$/d')"

echo
if [ -z "$new_ports" ]; then
  echo "❌ Tidak ada port baru yang terdeteksi."
  echo "   Cek hal berikut:"
  echo "     - Kabel USB (harus kabel data, bukan cuma charge)"
  echo "     - Driver USB-serial (CH340/CP2102) terpasang"
  echo "     - macOS: pastikan pola cu.* — Linux: cek 'dmesg -w' saat colok"
  exit 1
fi

count="$(echo "$new_ports" | wc -l | tr -d ' ')"
if [ "$count" -gt 1 ]; then
  echo "⚠️  Terdeteksi lebih dari satu port baru:"
  echo "$new_ports" | sed 's/^/  /'
  echo "   Colok HANYA board LoRa (cabut USB-serial lain) lalu ulangi."
  exit 1
fi

PORT="$new_ports"
echo "✅ Port board LoRa: $PORT"
echo

# ─── Tawarkan update .env ────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo "Catatan: $ENV_FILE belum ada. Set manual:  SERIAL_PORT=$PORT"
  exit 0
fi

read -r -p "Update SERIAL_PORT di .env jadi '$PORT'? [y/N] " ans
case "$ans" in
  y|Y)
    if grep -q '^SERIAL_PORT=' "$ENV_FILE"; then
      # BSD sed (macOS) & GNU sed (Linux) sama-sama dukung -i dengan backup ext.
      sed -i.bak "s#^SERIAL_PORT=.*#SERIAL_PORT=$PORT#" "$ENV_FILE"
    else
      printf '\nSERIAL_PORT=%s\n' "$PORT" >> "$ENV_FILE"
    fi
    echo "✅ .env diperbarui (backup: .env.bak). SERIAL_PORT=$PORT"
    ;;
  *)
    echo "Dilewati. Set manual di .env:  SERIAL_PORT=$PORT"
    ;;
esac
