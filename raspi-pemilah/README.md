# SmartBin — Pemilah Sampah (Raspberry Pi)

Folder mandiri untuk dijalankan di **Raspberry Pi**. Tugasnya:
kamera HP → klasifikasi jenis sampah (TFLite) → kirim perintah ke ESP32 (servo) lewat serial USB.

> Tidak butuh backend Node / Postgres / Redis. Cukup folder ini.

## Isi folder
| File | Fungsi |
|------|--------|
| `main.py` | Server (FastAPI): klasifikasi + halaman scan HP + kirim serial |
| `model_advanced.tflite` | Model klasifikasi sampah (Anorganik / B3 / Organik) |
| `requirements.txt` | Dependency Python |
| `gen-cert.sh` | Buat sertifikat HTTPS (wajib utk kamera HP) |
| `run.sh` | Jalankan server (auto-buat cert) |

## Cara pakai di Raspberry Pi

```bash
# 1. Masuk folder
cd raspi-pemilah

# 2. Buat virtualenv + install dependency
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Jalankan (script ini otomatis buat sertifikat HTTPS sekali)
bash run.sh
```

Saat jalan akan tampil URL, mis: `https://192.168.1.50:8000/scan`

## Pakai dari HP
1. HP & Raspi harus di **WiFi yang sama**.
2. Buka URL `/scan` di browser HP.
3. Muncul peringatan sertifikat → **Lanjutkan / Proceed** (wajar, self-signed).
4. Izinkan **kamera** → arahkan ke sampah → jenis sampah tampil realtime (ambang 75%).

## Sambungan ke ESP32 (servo)
- ESP32 dicolok ke Raspi via USB → muncul sbg `/dev/ttyUSB0` atau `/dev/ttyACM0`.
- Cek: `ls /dev/ttyUSB* /dev/ttyACM*`
- Kalau bukan `/dev/ttyUSB0`, ubah `SERIAL_PORT` di `main.py` (baris ~49).
- `main.py` kirim teks `organik` / `anorganik` / `B3` (baud **115200**) — firmware ESP32 (tim IoT) yang menggerakkan servo.

## Setelan (opsional, lewat env var — tanpa ubah kode)
```bash
CONF_THRESHOLD=0.8 python3 main.py   # ubah ambang webcam lokal
AUTO_START_CAM=1   python3 main.py   # nyalakan webcam yang dicolok ke Raspi
```
Ambang kamera HP (75%) ada di `main.py` (cari `confidence>=0.75`).

## Catatan
- Sertifikat `cert.pem`/`key.pem` dibuat ulang per-perangkat (sudah ditangani `run.sh`).
- Endpoint prediksi volume (`/lstm`) tidak disertakan di sini — itu bukan bagian pemilahan.
