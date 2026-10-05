# DOKUMEN PANDUAN DEPLOYMENT SISTEM SMARTBIN DARI NOL (FROM SCRATCH)
**Untuk Keperluan Pendaftaran Hak Paten / HKI & Panduan Pengembang (Developer Technical Manual)**

---

## 1. Gambaran Arsitektur Sistem (System Architecture Overview)

Sistem SmartBIN terdiri dari 4 lapisan utama yang terintegrasi secara *real-time*:

```mermaid
graph TD
    subgraph Edge Layer [1. Edge & Node Layer]
        ESP32[Node ESP32 Telemetri / GPS]
        Raspi[Raspberry Pi Pemilah + Kamera + STM32]
    end

    subgraph IoT Layer [2. IoT Broker Layer]
        HiveMQ[HiveMQ Cloud MQTT Broker - Port 8883 TLS]
    end

    subgraph Server Layer [3. Server / VPS Cloud Layer]
        Caddy[Caddy Reverse Proxy - HTTPS / SSL]
        Backend[Node.js Express + Prisma ORM]
        Predict[Python TFLite Microservice]
        DB[(PostgreSQL Database)]
        Redis[(Redis Cache & Session)]
    end

    subgraph Client Layer [4. Presentation Layer]
        Vercel[Vercel Dashboard Admin & Web App]
    end

    ESP32 -->|Publish Telemetry MQTT| HiveMQ
    Raspi -->|Publish Classifier & Sensor MQTT| HiveMQ
    Raspi -->|HTTP Ingest REST API| Caddy
    HiveMQ -->|Subscribe & Sync Data| Backend
    Caddy -->|Reverse Proxy /predict| Predict
    Caddy -->|Reverse Proxy API & WS| Backend
    Backend --> DB
    Backend --> Redis
    Vercel -->|REST API & WebSocket wss://| Caddy
```

1. **Presentation Layer (Frontend)**: Web App Admin & Dashboard Pemantauan ter-host di **Vercel**.
2. **Server / Cloud Layer (Backend VPS)**:
   - **Caddy**: Reverse proxy dengan SSL/HTTPS otomatis (Cloudflare Origin Certificate / Let's Encrypt).
   - **Backend Engine**: Node.js (Express.js + Prisma ORM + WebSocket server + MQTT Client).
   - **Database**: PostgreSQL 16 (Penyimpanan data relasional: Bins, Users, Ingest Logs, Pickups, Alerts).
   - **Cache**: Redis 7 (Penyimpanan sesi, rate limiting, pub/sub internal).
   - **Predict Microservice**: Python FastAPI/Flask (Inference AI EfficientNet/MobileNet TFLite untuk verifikasi foto web).
3. **IoT Broker Layer**: **HiveMQ Cloud MQTT** (Transport data IoT aman via TLS Port 8883).
4. **Edge Device Layer**:
   - **Raspberry Pi Pemilah**: AI Computer Vision (Kamera), kontrol actuator STM32 via USB Serial, dan gateway HTTP/MQTT.
   - **ESP32 Telemetry Nodes**: Node sensor tingkat kepenuhan, gas, GPS, dan status baterai.

---

## 2. Prasyarat System (Prerequisites)

Sebelum memulai deployment, pastikan resource berikut telah disiapkan:

- **Virtual Private Server (VPS)**: Linux Ubuntu 22.04 / 24.04 LTS (Minimal 2 vCPU, RAM 2GB-4GB).
- **Domain Name**: Domain aktif (misal `smartbin.sbs` disetting ke IP VPS via Cloudflare DNS).
- **Akun HiveMQ Cloud**: Akun gratis/berbayar di [HiveMQ Cloud](https://www.hivemq.com/cloud/).
- **Akun Vercel**: Akun Vercel terhubung ke GitHub repository frontend.
- **Hardware Edge**: Raspberry Pi 4 (RAM 4GB+), Kamera USB/CSI, STM32 Microcontroller, ESP32 DevKit, Sensor Ultrasonik/Gas/GPS.

---

## 3. Tahap 1: Setup IoT MQTT Broker (HiveMQ Cloud)

1. Login ke **HiveMQ Cloud Dashboard**.
2. Buat Cluster baru (pilih opsi *Free Tier*).
3. Catat **Cluster URI / Broker URL**, contoh: `4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud`.
4. Buka tab **Access Management** (Credentials):
   - Tambahkan User Baru (misal: Username `bintrash`, Password `Smartbinbrin1`).
   - Berikan akses Read/Write (*Publish/Subscribe*) ke semua topik.
5. Port komunikasi yang digunakan adalah **`8883`** (MQTT Secure TLS/SSL).

---

## 4. Tahap 2: Deployment Backend Core di Cloud VPS (Dari Nol)

### Step 2.1 — SSH & Install Engine Dependencies di VPS

Masuk ke VPS via SSH dari Terminal laptop:
```bash
ssh root@<IP_VPS_ANDA>
```

Install Docker & Git di VPS:
```bash
# Update sistem
apt update && apt upgrade -y

# Install git, curl, openssl
apt install -y git curl openssl

# Install Docker Engine & Docker Compose Plugin
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Verifikasi docker
docker --version
docker compose version
```

### Step 2.2 — Clone Repository & Setup Directory

```bash
cd /opt
git clone https://github.com/USER_ANDA/SmartBIN-BRIN-BackEnd-.git smartbin-backend
cd smartbin-backend
```

### Step 2.3 — Konfigurasi Environment File (`.env`)

Salin file contoh `.env.example` menjadi `.env`:
```bash
cp .env.example .env
nano .env
```

Isi dan amankan variabel berikut pada file `.env`:

```env
# ================================
# App & Network
# ================================
PORT=3000
NODE_ENV=production
CORS_ORIGIN=https://frontend-smartbin-brin.vercel.app
BACKEND_TS_BIND=0.0.0.0

# ================================
# Database (PostgreSQL)
# ================================
# Ganti CHANGE_ME_DB_PASSWORD dengan password kuat yang aman!
POSTGRES_PASSWORD=SmartBinPasswordStrong123!
DATABASE_URL="postgresql://smartbin:SmartBinPasswordStrong123!@postgres:5432/smartbin_db?schema=public"

# ================================
# Redis Cache
# ================================
REDIS_URL="redis://redis:6379"

# ================================
# MQTT (HiveMQ Cloud)
# ================================
MQTT_BROKER_URL=mqtts://4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud:8883
MQTT_USERNAME=bintrash
MQTT_PASSWORD=Smartbinbrin1
MQTT_CLIENT_ID=

# ================================
# Security Secrets (Generate via OpenSSL)
# ================================
# Jalankan `openssl rand -hex 64` di terminal untuk hasilkan secret ini:
JWT_SECRET=e7d6928e3b1284fa9a0293d84b2c1a890123456789abcdef0123456789abcdef
JWT_EXPIRES_IN="7d"

# Jalankan `openssl rand -hex 24` untuk Key HTTP Ingest Perangkat Edge:
DEVICE_INGEST_KEY=c391ab89201f928e19024a87239102938471209384719284

# ================================
# Microservices & Config
# ================================
CLASSIFY_SERVICE_URL="http://predict:9000"
WEIGHT_MODE=accumulate
WEIGHT_ZERO_THRESHOLD=0.03
AUTH_RATE_LIMIT_MAX=0
```

### Step 2.4 — Setup Sertifikat SSL Caddy (Cloudflare Origin / SSL)

Buat folder `certs` dan letakkan sertifikat SSL domain VPS di sana:
```bash
mkdir -p certs
```

*Jika menggunakan Cloudflare SSL Mode Full (Strict):*
- Simpan Origin Certificate ke `./certs/origin.pem`.
- Simpan Private Key ke `./certs/origin-key.pem`.

Verifikasi file `Caddyfile`:
```caddy
smartbin.sbs {
	tls /etc/caddy/certs/origin.pem /etc/caddy/certs/origin-key.pem
	handle /predict* {
		reverse_proxy predict:9000
	}
	handle {
		reverse_proxy backend:3000
	}
}
```

### Step 2.5 — Build & Run Containers via Docker Compose

Jalankan perintah berikut di folder `/opt/smartbin-backend`:

```bash
# Build dan jalankan service di background
docker compose -f docker-compose.prod.yml up -d --build

# Cek status container
docker compose -f docker-compose.prod.yml ps
```

### Step 2.6 — Database Migration & Initial Seeding

Setelah container PostgreSQL dan Backend aktif:
```bash
# Jalankan Prisma DB Migration
docker compose -f docker-compose.prod.yml exec backend npx prisma migrate deploy

# Jalankan Data Seed Awal (Admin User, Default Bins, Areas)
docker compose -f docker-compose.prod.yml exec backend npm run seed
```

### Step 2.7 — Verifikasi Kesehatan Server VPS

Jalankan test HTTP health check:
```bash
curl -I https://smartbin.sbs/health
```
*Hasil harus mengembalikan HTTP status `200 OK`.*

---

## 5. Tahap 3: Deployment Frontend Dashboard di Vercel

1. **Push Frontend Codebase** ke GitHub repository.
2. Login ke **[Vercel Dashboard](https://vercel.com/)** -> **Add New Project** -> **Import Git Repository**.
3. Pada halaman **Configure Project**:
   - Framework Preset: **Next.js** / **Vite** / **React**.
   - Build Command: `npm run build`
   - Output Directory: `.next` atau `dist`
4. **Environment Variables** (Tambahkan variabel berikut):
   - `NEXT_PUBLIC_API_BASE_URL` = `https://smartbin.sbs`
   - `NEXT_PUBLIC_MQTT_BROKER_URL` = `wss://4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud:8884/mqtt` *(jika frontend membaca MQTT langsung)*
5. Klik **Deploy**.
6. Setelah selesai, atur **Domains** di Setting Vercel ke domain pilihan (misal: `frontend-smartbin-brin.vercel.app` atau `app.smartbin.sbs`).

---

## 6. Tahap 4: Setup & Configuration Edge Device (Raspberry Pi Pemilah)

### Step 4.1 — Flash & Initial Setup OS

1. Flash **Raspberry Pi OS 64-bit** (Debian Bookworm/Bullseye) ke MicroSD menggunakan Raspberry Pi Imager.
2. Aktifkan SSH, Wi-Fi, dan ganti password default user `brin`.
3. Pasang MicroSD ke Raspberry Pi, nyalakan, dan SSH dari laptop:
   ```bash
   ssh brin@<IP_RASPI_LOKAL_ATAU_TAILSCALE>
   ```

### Step 4.2 — Install System Dependencies & Hardware Drivers

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git libatlas-base-dev libopencv-dev v4l-utils
```

### Step 4.3 — Konfigurasi Hak Akses Serial & Kamera

Tambahkan user `brin` ke group `dialout` dan `video`:
```bash
sudo usermod -a -G dialout,video $USER
```

### Step 4.4 — Clone Repository Edge & Virtual Environment

```bash
cd ~
git clone https://github.com/USER_ANDA/SmartBIN-BRIN-BackEnd-.git smartbin-edge
cd smartbin-edge/raspi-pemilah

# Buat virtual environment python
python3 -m venv venv
source venv/bin/activate

# Install dependency python
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4.5 — Konfigurasi File `.env` Edge Raspberry Pi

Buat file `.env` di folder `raspi-pemilah`:
```bash
nano .env
```

Isi `.env` Edge:
```env
# Server Backend HTTP Ingest URL
BACKEND_HTTP_URL=https://smartbin.sbs/ingest/sensor
CLASSIFICATION_INGEST_URL=https://smartbin.sbs/ingest/classifications
DEVICE_INGEST_KEY=c391ab89201f928e19024a87239102938471209384719284

# HiveMQ Cloud Credentials
MQTT_BROKER_URL=mqtts://4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud:8883
MQTT_USERNAME=bintrash
MQTT_PASSWORD=Smartbinbrin1
MQTT_CLIENT_ID=smartbin-pemilah-003

# Hardware Serial Ports
STM32_PORT=/dev/ttyUSB0
LORA_PORT=/dev/ttyUSB1
BAUDRATE=115200

# Camera & Auto Start
CAMERA_INDEX=0
AUTO_START_CAM=1
```

### Step 4.6 — Konfigurasi Systemd Auto-Start Service

Agar program pemilah otomatis menyala saat Raspberry Pi dinyalakan:
```bash
sudo nano /etc/systemd/system/smartbin-pemilah.service
```

Masukkan konfigurasi berikut:
```ini
[Unit]
Description=SmartBIN Edge AI Sorter Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=brin
WorkingDirectory=/home/brin/smartbin-edge/raspi-pemilah
ExecStart=/home/brin/smartbin-edge/raspi-pemilah/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Aktifkan service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable smartbin-pemilah.service
sudo systemctl start smartbin-pemilah.service
```

Cek status jalannya service:
```bash
sudo systemctl status smartbin-pemilah.service
```

---

## 7. Tahap 5: Flashing Firmware Node Telemetri (ESP32)

### Step 5.1 — Setup Arduino IDE & Library Requirements

1. Install **Arduino IDE 2.x**.
2. Install ESP32 Board Manager:
   - Tambahkan URL di Preference: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Install board **esp32 by Espressif Systems**.
3. Install Library via Library Manager:
   - `PubSubClient` (oleh Nick O'Leary)
   - `ArduinoJson` (oleh Benoit Blanchon)
   - `TinyGPS++` (oleh Mikal Hart)
   - `WiFiClientSecure` (bawaan ESP32)

### Step 5.2 — Konfigurasi Firmware Kode ESP32 (`bin_gps_location.ino`)

Buka file `esp32-node/bin_gps_location.ino` pada Arduino IDE.

Sesuaikan konstanta WiFi & HiveMQ:
```cpp
// Wi-Fi Credentials
const char* ssid = "NAMA_WIFI_LOKASI";
const char* password = "PASSWORD_WIFI_LOKASI";

// HiveMQ Cloud Credentials
const char* mqtt_server = "4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud";
const int mqtt_port = 8883;
const char* mqtt_user = "bintrash";
const char* mqtt_pass = "Smartbinbrin1";
const char* client_id = "ESP32-BIN-NODE-003";

// Topic Publish Telemetri
const char* topic_telemetry = "smartbin/bins/BIN-003/telemetry";
```

### Step 5.3 — Flashing Kode ke ESP32

1. Hubungkan ESP32 ke Laptop via kabel USB Data.
2. Pilih Board: **ESP32 Dev Module**.
3. Pilih Port Serial yang sesuai (misal `/dev/ttyUSB0` atau `COM3`).
4. Klik tombol **Upload** (Panah Kanan).
5. Buka Serial Monitor (Baudrate `115200`) untuk memverifikasi ESP32 berhasil terhubung ke Wi-Fi dan HiveMQ MQTT Broker.

---

## 8. Tahap 6: Pengujian & Verifikasi Integrasi End-to-End (E2E Test)

### 1. Uji Konektivitas Database & Backend API
Buka Terminal VPS atau lokal, jalankan script simulasi ingest:
```bash
node scripts/trigger-sensor-data.js
```
*Pastikan data sensor tersimpan di tabel `SensorIngestLog` di PostgreSQL.*

### 2. Uji Publikasi & Langganan MQTT (HiveMQ)
Jalankan simulator MQTT backend:
```bash
npm run simulate
```
*Pastikan pesan `smartbin/bins/BIN-003/telemetry` terkirim dan terbaca di Backend.*

### 3. Uji Pemilahan Otomatis Edge (Raspberry Pi)
Lihat log real-time Raspberry Pi:
```bash
journalctl -u smartbin-pemilah.service -f
```
- Tempatkan sampah di atas platform.
- Kamera mengambil gambar -> Model TFLite mengklasifikasi -> Sinyal dikirim ke STM32 -> Aktuator memilah -> Telemetri dikirim ke Backend.

---

## 9. Ringkasan Matriks Variabel Environment Globals

| Nama Variabel | Dipakai Di | Fungsi | Contoh Nilai |
|---|---|---|---|
| `DATABASE_URL` | VPS Backend | Connection string database PostgreSQL | `postgresql://user:pass@postgres:5432/smartbin_db` |
| `REDIS_URL` | VPS Backend | Connection string Redis Caching | `redis://redis:6379` |
| `MQTT_BROKER_URL` | VPS Backend, Raspi, ESP32 | URL Broker HiveMQ Cloud TLS Port 8883 | `mqtts://<cluster>.s1.eu.hivemq.cloud:8883` |
| `MQTT_USERNAME` | VPS Backend, Raspi, ESP32 | User autentikasi HiveMQ | `bintrash` |
| `MQTT_PASSWORD` | VPS Backend, Raspi, ESP32 | Password autentikasi HiveMQ | `Smartbinbrin1` |
| `JWT_SECRET` | VPS Backend | Secret key penandatanganan token JWT | `openssl rand -hex 64` |
| `DEVICE_INGEST_KEY` | VPS Backend & Raspi Edge | Key autentikasi HTTP Push dari perangkat Edge | `openssl rand -hex 24` |
| `CORS_ORIGIN` | VPS Backend | Origin domain Frontend yang diizinkan | `https://frontend-smartbin-brin.vercel.app` |
| `NEXT_PUBLIC_API_BASE_URL` | Vercel Frontend | Endpoint utama Backend API | `https://smartbin.sbs` |

---
*Dokumen Spesifikasi Teknikal & Panduan Deployment Sistem SmartBIN · Terintegrasi BRIN (Badan Riset dan Inovasi Nasional)*
