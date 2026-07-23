# Raspi TX — Node / Seeder (bin-003)

Raspi ini **pengirim** (device sensor `bin-003`). Untuk penelitian, `lora_tx_seed.py`
memancing data dummy dan bisa kirim lewat **3 mode**:

| Mode | Perintah | Jalur |
|------|----------|-------|
| **LoRa** | `python3 lora_tx_seed.py` | tulis ke modul LoRa → RF → board RX → gateway RX → server |
| **HTTP** | `python3 lora_tx_seed.py --http` | POST langsung ke server via internet |
| **HYBRID** | `python3 lora_tx_seed.py --hybrid` | **dua-duanya sekaligus**, `seq` & `sentAt` identik → perbandingan adil |

Tiap paket bawa `seq` (deteksi packet loss) + `sentAt` (hitung latency). Karena kedua
jalur pakai device & jam yang sama, offset jam saling meniadakan → perbandingan sah.

```
                        ┌─ LoRa (serial→RF) ──▶ board RX ──▶ gateway RX ──▶ SERVER  (transport=lora)
Raspi TX (1 bacaan) ────┤   seq=7, sentAt=X
                        └─ HTTP (POST) ─────────internet──────────────────▶ SERVER  (transport=http)
```

File yang jalan di sini:
- **`main.py`** — IMPLEMENTASI ASLI (baca STM32, kamera+TFLite, forward LoRa/HTTP/MQTT)
- **`lora_tx_seed.py`** — seeder data dummy (buat tes tanpa STM32; TIDAK dipakai saat implementasi)

---

## 0. Implementasi asli: `main.py` (dengan perbandingan LoRa vs HTTP)

`main.py` baca sensor dari **STM32** (serial) lalu forward ke beberapa jalur. Tiap
bacaan otomatis disuntik `seq` + `sentAt` (buat packet loss & latency).

Jalur bisa diatur lewat env:
| Env | Default | Arti |
|-----|---------|------|
| `STM32_PORT` | `/dev/ttyACM1` | serial STM32 |
| `LORA_PORT` | `/dev/ttyACM0` | modul LoRa TX (WAJIB beda dari STM32_PORT) |
| `FORWARD_LORA` | `1` | forward ke LoRa (→ RX → gateway, transport=lora) |
| `FORWARD_MQTT` | `1` | forward ke MQTT HiveMQ (transport=null) |
| `COMPARE_HTTP` | `0` | **`1` = POST langsung ke server (transport=http)** |
| `BACKEND_HTTP_URL` | `http://192.168.1.23:3000` | server tujuan jalur HTTP (LAN/VPS) |
| `DEVICE_INGEST_KEY` | — | wajib kalau server set key |

**Jalankan eksperimen perbandingan (LoRa vs HTTP, tanpa MQTT dobel):**
```bash
cd ~/backend
COMPARE_HTTP=1 FORWARD_MQTT=0 \
BACKEND_HTTP_URL=http://192.168.1.23:3000 \
DEVICE_INGEST_KEY=4cc3a6eb99c5bb859b5006b0d23d8f0bd41294953371d543 \
python3 main.py
```
main.py = FastAPI di `:8000`. Cek jalur aktif: `curl http://localhost:8000/status`.

> ⚠️ **Dobel-tulis:** tiap bacaan dikirim per jalur aktif → tersimpan 1×/jalur di
> backend. LoRa+HTTP = 2 baris SensorLog per bacaan (nilai berat sama). Ini yang bikin
> perbandingan bisa jalan, TAPI metrik akumulasi berat bisa dobel. Buat perbandingan,
> jalankan sebagai eksperimen terkontrol; buat produksi akurat, pakai satu jalur saja.

---

## 1. Yang harus ada di Raspi TX (untuk seeder)

- Folder `~/backend/` berisi `lora_tx_seed.py`
- **Modul LoRa TX** kecolok via USB (buat mode LoRa/hybrid)
- Internet (buat mode HTTP/hybrid)
- Dependency: `requests` (HTTP), `pyserial` (LoRa)

```bash
pip install requests pyserial
```

---

## 2. Cari port modul LoRa TX

> Ini port modul LoRa **di Raspi TX**, beda dari board RX di Raspi RX.

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

---

## 3. Konfigurasi (lewat env var, bukan .env)

| Env | Arti | Contoh |
|-----|------|--------|
| `LORA_PORT` | port modul LoRa TX | `/dev/ttyACM0` |
| `BAUD_RATE` | baud modul LoRa | `115200` |
| `NODE_ID` | id node | `bin-003` |
| `BACKEND_URL` | **server tujuan leg HTTP** (LAN/VPS) | `https://vps` |
| `DEVICE_INGEST_KEY` | wajib kalau POST ke server langsung | `4cc3a6...` |

---

## 4. Jalankan

### Mode HYBRID (disarankan buat perbandingan)
```bash
cd ~/backend
LORA_PORT=/dev/ttyACM0 \
BACKEND_URL=https://your-vps-url \
DEVICE_INGEST_KEY=4cc3a6eb99c5bb859b5006b0d23d8f0bd41294953371d543 \
python3 lora_tx_seed.py --hybrid --count 30 --interval 2
```
Tiap bacaan keluar 2 baris:
```
[LoRa seq=7] tx → serial
[HTTP seq=7] ✓ HTTP 202 (48ms RTT)
```

### Mode HTTP saja (ke server/VPS)
```bash
BACKEND_URL=https://your-vps-url \
DEVICE_INGEST_KEY=4cc3a6eb99c5bb859b5006b0d23d8f0bd41294953371d543 \
python3 lora_tx_seed.py --http --count 30 --interval 2
```
> Ke gateway RX (satu jaringan) juga bisa: `BACKEND_URL=http://<ip-rx>:8080` (tanpa key,
> pintu gateway terbuka).

### Mode LoRa saja
```bash
LORA_PORT=/dev/ttyACM0 python3 lora_tx_seed.py --count 30 --interval 2
```

### Opsi
- `--count N` jumlah paket (0 = terus-menerus) · `--interval S` detik antar kirim
- `--start-seq N` nomor urut awal · `--dry-run` cetak JSON saja (tanpa kirim)

---

## 5. Troubleshooting

| Gejala | Penyebab | Solusi |
|--------|----------|--------|
| `error: unrecognized arguments: --http/--hybrid` | file versi lama | SCP `lora_tx_seed.py` terbaru dari laptop |
| `Gagal buka /dev/ttyACMx` | port LoRa salah | cek langkah 2, set `LORA_PORT` |
| HTTP `Connection refused` | tujuan mati / salah host | pastikan server/gateway jalan & `BACKEND_URL` benar. `localhost` cuma valid kalau server di Raspi yang sama |
| HTTP `401` | `DEVICE_INGEST_KEY` kosong/salah | isi env key yang sama dgn server |
| HTTP `404 Unknown nodeId` | `bin-003` belum terdaftar di server | daftarkan di backend |
| di dashboard leg LoRa `seq=-` | firmware board RX motong field | perbaiki firmware RX (nerusin JSON utuh) |

> **Beda jaringan / VPS:** leg HTTP arahkan `BACKEND_URL` ke VPS (pakai key). Leg LoRa
> tetap via RF ke board RX — nggak butuh internet, jadi beda jaringan tidak masalah.
