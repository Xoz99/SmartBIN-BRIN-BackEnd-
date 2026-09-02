#!/usr/bin/env bash
# Deploy update SmartBIN ke Raspberry Pi (ensemble + voting).
# Jalanin dari root repo di Mac:  bash deploy_to_pi.sh
set -euo pipefail

PI="brin@100.99.74.71"          # ganti kalau IP Tailscale beda
DEST="~/backend"                # folder backend di Pi
REPO="$(cd "$(dirname "$0")" && pwd)"

echo "==> Repo lokal : $REPO"
echo "==> Target Pi  : $PI:$DEST"
echo

# --- Cek file lokal ada semua sebelum kirim ---
need=(
  "backend/main.py"
  "backend/remote_control.py"
  "backend/model_combo.tflite"
  "model_ai_baru/an_or32.tflite"
)
for f in "${need[@]}"; do
  [ -f "$REPO/$f" ] || { echo "!! File hilang: $f — batal."; exit 1; }
done
echo "[ok] Semua file lokal ada."
echo

# --- 1. Backup main.py & remote_control.py di Pi (timestamp) ---
TS=$(date +%s)
echo "==> Backup main.py & remote_control.py di Pi (.bak.$TS)"
ssh "$PI" "cd $DEST && cp -f main.py main.py.bak.$TS 2>/dev/null || true; \
           cp -f remote_control.py remote_control.py.bak.$TS 2>/dev/null || true; \
           mkdir -p model_ai_baru"

# --- 2. Kirim file inti backend ---
echo "==> Kirim main.py, remote_control.py, model_combo.tflite"
scp "$REPO/backend/main.py" \
    "$REPO/backend/remote_control.py" \
    "$REPO/backend/model_combo.tflite" \
    "$PI:$DEST/"

# --- 3. Kirim AI utama ke model_ai_baru/ ---
echo "==> Kirim model_ai_baru/an_or32.tflite"
scp "$REPO/model_ai_baru/an_or32.tflite" "$PI:$DEST/model_ai_baru/"

# --- 4. Verifikasi di Pi ---
echo
echo "==> Verifikasi hasil di Pi:"
ssh "$PI" "cd $DEST && \
  echo '--- config main.py Pi ---' && \
  grep -nE 'MODEL_PATH |MODEL_PATH_NEW|ENSEMBLE |VOTE_FRAMES' main.py | head && \
  echo '--- file model ---' && \
  ls -la model_combo.tflite model_ai_baru/an_or32.tflite"

echo
echo "=== SELESAI KIRIM ==="
echo "Restart service di Pi, lalu cek log harus muncul DUA baris:"
echo "  [+] Model utama loaded! Input: 224x224, dtype=float32"
echo "  [+] Penjaga B3 (model lama) loaded: model_combo.tflite | override B3 ..."
echo
echo "Restart (pilih sesuai setup lu):"
echo "  ssh $PI 'sudo systemctl restart smartbin'   # kalau pakai systemd"
echo "  # atau jalanin manual:  cd ~/backend && python3 main.py"
