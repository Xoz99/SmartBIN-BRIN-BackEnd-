# Raspi RX — Gateway (LoRa + HTTP → Server)

Raspi ini **penerima**. Tugasnya nerima data sensor lewat **dua pintu**, ukur metrik,
lalu teruskan ke **server/backend** (laptop LAN atau VPS).

```
Node TX ──RF 923MHz──▶ board LoRa RX ──USB/serial──┐
                                                    ├─▶ gateway_unified.py ──HTTP──▶ SERVER
Node HTTP ──WiFi POST :8080────────────────────────┘        (tag: lora/http)     /ingest/sensor
```

File yang jalan di sini: **`gateway_unified.py`**.

---

## 1. Yang harus ada di Raspi RX

- Folder `~/rpi-gateway/` berisi: `gateway_unified.py`, `.env`, `requirements.txt`, `find-lora-port.sh`
- **Board LoRa RX** kecolok via USB (LoRa32/TTGO firmware "LoRa Core", ngeprint `SENSOR:<node>:<json>`)
- Python 3.9+ dengan dependency: `requests`, `python-dotenv`, `pyserial`

Install dependency (sekali aja):
```bash
cd ~/rpi-gateway
pip install -r requirements.txt      # atau: pip install requests python-dotenv pyserial
```

---

## 2. Cari port board LoRa RX

Nomor `ttyACM*` bisa **berubah** tiap cabut-colok/reboot. Cek:
```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```
Atau otomatis (cabut board → jalanin → colok board):
```bash
./find-lora-port.sh
```

Kalau "permission denied" saat buka port:
```bash
sudo usermod -a -G dialout $USER   # lalu logout-login / reboot
```

---

## 3. Konfigurasi `.env`

```ini
# Tujuan akhir data (server backend). LAN: http://<ip-laptop>:3000 | VPS: https://vps
BACKEND_URL=http://192.168.1.23:3000
DEVICE_INGEST_KEY=4cc3a6eb99c5bb859b5006b0d23d8f0bd41294953371d543   # SAMA dgn .env backend

# Pintu LoRa (board RX via serial)
LORA_DRIVER=serial
SERIAL_PORT=/dev/ttyACM1        # sesuaikan hasil langkah 2
SERIAL_BAUD=115200
LORA_SF=7                       # samain dgn firmware TX (buat hitung throughput)
LORA_BW_HZ=125000
LORA_CR_DENOM=5

# Pintu HTTP (server di Raspi ini)
GATEWAY_HTTP_ENABLE=1
GATEWAY_HTTP_HOST=0.0.0.0
GATEWAY_HTTP_PORT=8080
GATEWAY_INGEST_KEY=             # kosong = terbuka (LAN). Isi kalau mau wajib key.

LOG_LEVEL=INFO
```

> `LORA_DRIVER=none` → matikan pintu LoRa (gateway jadi HTTP-only).

---

## 4. Jalankan

```bash
cd ~/rpi-gateway
python3 gateway_unified.py
```

Log sehat:
```
gateway-unified rpi-gateway-01 → http://192.168.1.23:3000/ingest/sensor (lora=serial, http=on)
HTTP ingest ready @ http://0.0.0.0:8080/ingest/sensor  (terbuka)
LoRa serial ready @ /dev/ttyACM1 115200bd
✓ [lora] bin-003 | w=42.9kg v=17.0% rssi=-22dBm snr=9.5dB len=115B tp=4722bps seq=7
✓ [http] bin-003 | ...
```

Tanda `[lora]` / `[http]` = jalur asal paket. `✓` = kesimpan di server, `✗` = gagal.

Cek pintu HTTP hidup:
```bash
curl http://localhost:8080/health   # → {"success": true, ...}
```

Cari IP Raspi ini (buat dikasih ke node HTTP di jaringan sama):
```bash
hostname -I
```

Jalankan sebagai service (opsional, biar auto-start): edit `smartbin-gateway.service`
→ `ExecStart=... gateway_unified.py`, lalu `sudo systemctl enable --now smartbin-gateway`.

---

## 5. Troubleshooting

| Gejala | Penyebab | Solusi |
|--------|----------|--------|
| `could not open port /dev/ttyACMx` | port salah/berubah | cek langkah 2, update `SERIAL_PORT` |
| `LoRa serial ready` tapi sepi (nggak ada `[lora]`) | board nggak ngeprint `SENSOR:` | cek `cat /dev/ttyACM1` — board ngirim apa |
| `POST ... 401` | `DEVICE_INGEST_KEY` beda dgn server | samakan key gateway & server |
| `POST ... 404 Unknown nodeId` | node belum terdaftar di server | daftarkan bin di backend |
| `POST ... 500 Invalid prisma...` | kolom DB baru belum diterapkan di server | di server: `npx prisma migrate deploy && npx prisma generate` + restart |
| `[lora]` masuk tapi `seq=-` | firmware board RX motong field JSON | perbaiki firmware biar nerusin JSON utuh |

> **Beda jaringan / pakai VPS:** LoRa (TX→RX) itu radio, nggak butuh internet.
> Yang diarahkan ke VPS cuma `BACKEND_URL` (leg RX→server). Node HTTP yang beda
> jaringan sebaiknya POST langsung ke VPS, bukan ke `:8080` Raspi ini.
