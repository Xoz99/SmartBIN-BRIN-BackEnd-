# Panduan Menjalankan SmartBIN bin-003

Panduan langkah demi langkah menyalakan alat: dari remote lewat Tailscale, masuk ke
Raspberry Pi, sampai sistem jalan dan muncul **ceklis hijau** — siap dipakai.

**Alur singkat:**
`Tailscale → SSH ke Raspi → cd backend → python main.py → ceklis hijau → siap`

---

## Yang disiapkan dulu — Akun Tailscale

Dipakai untuk remote (masuk jarak jauh) ke Raspberry Pi bin-003.

| Keterangan | Isi |
|---|---|
| **Email** | `testinglinux807@gmail.com` |
| **Password** | 🔒 Minta ke **pembimbing lapangan / admin** (sengaja tidak ditulis di sini demi keamanan) |

> **⚠️ Syarat penting:** **Raspberry Pi harus dalam keadaan nyala dan terhubung ke
> internet** (WiFi/kabel) supaya bisa di-remote lewat Tailscale. Kalau Raspi mati atau
> internetnya putus, SSH tidak akan bisa masuk.

---

## Langkah Menjalankan

### 1. Sambungkan Tailscale

Buka aplikasi Tailscale di laptop, login pakai akun di atas. Pastikan device kamu sudah
ter-*add* dan aktif di jaringan yang sama dengan Raspi.

> **Kenapa perlu:** Raspi cuma bisa diakses lewat jaringan Tailscale. Kalau belum connect,
> langkah SSH berikutnya pasti gagal.

### 2. Remote (SSH) ke Raspberry Pi

Setelah Tailscale nyala, buka Terminal dan masuk ke Raspi lewat alamat IP-nya:

```bash
ssh brin@100.99.74.71
```

> **Tip:** `100.99.74.71` adalah IP Tailscale Raspi. Kalau diminta password, masukkan
> password Raspi (bukan password Tailscale).

**✅ Kalau BERHASIL**, akan muncul permintaan password lalu prompt Raspi seperti ini:

```text
brin@100.99.74.71's password:        ← ketik password Raspi (tidak kelihatan saat diketik), Enter
Linux raspberrypi 6.1.0-rpi ...
Last login: Tue Aug  5 ...
brin@raspberrypi:~ $                  ← sudah masuk! prompt berubah jadi "brin@raspberrypi"
```

Tanda berhasil: prompt Terminal berubah jadi **`brin@raspberrypi:~ $`**.

> *Kalau ini SSH pertama kali dari laptop ini, sebelum password muncul ada pertanyaan
> `Are you sure you want to continue connecting (yes/no)?` — ketik `yes` lalu Enter.*

**❌ Kalau GAGAL / nge-stuck** (diam >10 detik tanpa muncul apa-apa, lalu error seperti
`Operation timed out` atau `No route to host`):

Artinya Raspi **tidak terjangkau**. Cek berurutan:

1. **Tailscale** di laptop sudah nyala & login? (langkah 1)
2. **Raspi nyala** dan lampunya hidup?
3. **Internet Raspi** hidup? (WiFi/kabel di lokasi Raspi)
4. Kalau semua sudah oke tapi tetap stuck: tekan `Ctrl+C` untuk batal, tunggu ~30 detik,
   lalu coba `ssh brin@100.99.74.71` lagi.

### 3. Masuk ke folder backend

Begitu berhasil masuk ke Raspi, pindah ke folder tempat program utama berada:

```bash
cd backend
```

### 4. Jalankan sistemnya

Nyalakan program utama SmartBIN (kamera, model AI, dan pengiriman data):

```bash
python main.py
```

> **Kalau muncul error / gagal:** port-nya masih kepakai. Lihat bagian
> **Kalau Gagal Jalan** di bawah, lalu ulangi langkah ini.

### 5. Tunggu sampai ceklis hijau muncul

Biarkan program memuat. Jangan ditutup dan jangan diganggu dulu — tunggu sampai indikator
**✅ ceklis hijau** tampil, tanda kamera dan model AI sudah siap.

> **✅ Ceklis hijau = sistem siap.** Setelah tanda ini muncul, SmartBIN sudah aktif dan
> menunggu objek.

> **⚠️ Penting saat run:** jaga SmartBIN dalam **keadaan tenang** dan **jangan banyak orang
> di sekitarnya**. Ini supaya baseline kamera bersih dan deteksi sampah akurat.

### 6. SmartBIN siap dipakai

Selesai. Alat sudah bisa digunakan — masukkan sampah, sistem akan memilah dan mencatat
datanya otomatis. **Biarkan Terminal tetap terbuka** selama alat dipakai.

> **⚠️ Cara pakai yang benar — jangan buru-buru naro barang berikutnya:**
> Setelah sampah dipilah dan **jatuh**, mekanik akan **muter balik ke posisi awal** dulu
> (butuh beberapa detik). **Tunggu sampai platform benar-benar diam & kembali ke posisi
> semula** baru masukkan barang berikutnya. Kalau kecepetan naro barang saat mekanik masih
> bergerak, deteksinya bisa kacau / dobel.
>
> Patokannya: tunggu sampai di Terminal muncul baris
> `[CAM] ✅ Platform kosong lagi — SIAP objek berikutnya` — **itu tanda boleh naro barang lagi.**

---

## Contoh Tampilan Terminal (Kondisi Normal)

Setelah menjalankan `python main.py`, output di Terminal kira-kira seperti ini. Baris
yang **ditandai** di bawah adalah **ceklis hijau** — tanda alat sudah siap:

```text
[+] Loading model TFLite utama dari: model_ai_baru/model_fp16.tflite
[+] Model utama loaded! Input: 224x224, dtype=float32
[+] Penjaga B3 (model lama) loaded: model_advanced.tflite | override B3 kalau prob B3 >= 55%

INFO:     Started server process [742]
INFO:     Waiting for application startup.
[Serial] Deteksi USB → STM32=/dev/ttyUSB0 LoRa=/dev/ttyUSB1
[+] STM32 terhubung di /dev/ttyUSB0 @ 115200
[+] LoRa terhubung di /dev/ttyUSB1 @ 115200
[MQTT] Connecting ke 4b1ed76f...hivemq.cloud:8883...
[MQTT] Connected!
[CompareHTTP] aktif → http://100.82.171.105:3000/ingest/sensor
[CAM] Kamera AKTIF (index 0) — mode PLATFORM: analisis objek di ROI tengah (buletan), 1x per objek.
[CAM] (pastikan platform/buletan KOSONG saat start — dipakai sbg baseline)
[CAM] ⏳ Warmup 2s — kamera menyala & settle (pastikan buletan KOSONG)...
[CAM] ✅ Baseline platform KOSONG diambil — SIAP, menunggu objek di buletan...   ◀── CEKLIS HIJAU
[CAM] Auto-start kamera Raspi aktif (AUTO_START_CAM=1) — milah otomatis tanpa web.
[+] Startup selesai: STM32, LoRa, MQTT, Dispatcher semua aktif.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
[CAM] … menunggu objek (platform kosong, obj_diff=1)
```

> **Yang paling penting:** baris `[CAM] ✅ ... SIAP, menunggu objek ...` — **itu ceklis
> hijaunya.** Begitu muncul, alat sudah aktif dan siap dipakai.

**Contoh saat sampah masuk & terpilah** (muncul otomatis waktu ada objek di platform):

```text
[STM32] seq=128 | berat=0.14kg volume=12% ...
[CAM] #1 ✓ Organik (91%) → aktuasi STM32 + lapor backend
[CAM] ⏳ tunggu aktuator ~6.6s (objek jatuh + mekanik reset)...
[CAM] ✅ Platform kosong lagi — SIAP objek berikutnya (#2).
```

> *Catatan: nama port (`/dev/ttyUSB0`), angka `seq`, dan persentase bisa beda tiap
> device/jalannya — yang penting pola & baris ceklis hijaunya sama.*

---

## Kalau Gagal Jalan

**Masalah:** `python main.py` error / gak mau jalan.

**Penyebab umum:** port `8000` masih dipakai proses lama yang belum mati.

**Cara benerin:**

1. Matikan proses yang nyangkut di port 8000:

   ```bash
   sudo fuser -k 8000/tcp
   ```

2. Jalankan lagi program utamanya:

   ```bash
   python main.py
   ```

3. Tunggu lagi sampai **ceklis hijau** muncul (langkah 5).

---

*SmartBIN bin-003 · Panduan operasional · Remote: Tailscale → `ssh brin@100.99.74.71`*
