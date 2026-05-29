# SmartBIN Backend — API Documentation (untuk Tim Frontend)

Dokumen ini berisi seluruh REST endpoint + WebSocket real-time event yang tersedia di backend SmartBIN.

---

## 1. Informasi Umum

| Item | Nilai |
|---|---|
| Base URL (dev) | `http://localhost:3000` |
| Base URL (prod) | _menyusul saat sudah deploy_ |
| Format | JSON |
| Auth | `Authorization: Bearer <token>` (kecuali login & health) |
| Max body size | 10 MB |

> **Catatan:** tidak ada prefix `/api`. Endpoint langsung di root, mis. `/auth/login`, `/bins`.

### Health check (tanpa auth)
```
GET /health
→ 200 { "success": true, "message": "SmartBin Backend is running" }
```

### Format Response Standar

**Sukses:**
```json
{ "success": true, "message": "Success", "data": { ... } }
```

**Error:**
```json
{ "success": false, "message": "Pesan error", "data": null }
```

**Paginated (untuk list yang dibatasi halaman):**
```json
{
  "success": true,
  "message": "Success",
  "data": [ ... ],
  "pagination": { "total": 120, "page": 1, "limit": 50, "totalPages": 3 }
}
```

### Kode Status yang Umum
| Code | Arti |
|---|---|
| 200 | OK |
| 201 | Resource dibuat |
| 400 | Request tidak valid |
| 401 | Token tidak ada / kadaluarsa / kredensial salah |
| 403 | Role tidak punya akses (atau bukan area-nya) |
| 404 | Resource tidak ditemukan |
| 409 | Konflik (mis. email/nodeId/nama sudah ada) |
| 422 | Validasi body gagal (`message: "Validation failed"`) |

### Role & Akses
- **ADMIN** — akses penuh.
- **PETUGAS** — otomatis dibatasi hanya ke `area` miliknya (bins/alerts di luar areanya → 403).

---

## 2. Auth — `/auth`

### POST `/auth/login` — _publik, rate-limit 10x / 15 menit per IP_
**Body:**
```json
{ "email": "admin@smartbin.id", "password": "secret123" }
```
**Response 200:**
```json
{
  "success": true,
  "message": "Login successful",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6...",
    "user": { "id": "ckxxx", "name": "Admin", "email": "admin@smartbin.id", "role": "ADMIN", "areaId": null }
  }
}
```
> Simpan `token`, kirim di setiap request berikutnya via header `Authorization: Bearer <token>`. Token berlaku 7 hari.

### GET `/auth/me` — _auth_
Mengembalikan profil user yang sedang login.
```json
{ "success": true, "message": "Success",
  "data": { "id": "ckxxx", "name": "Admin", "email": "...", "role": "ADMIN",
            "areaId": "ck...", "area": { "id": "ck...", "name": "Area A" }, "createdAt": "2026-05-01T..." } }
```

### PUT `/auth/password` — _auth_
**Body:** `{ "oldPassword": "lama123", "newPassword": "baru456" }` (min 6 char, harus beda)
**Response 200:** `{ "success": true, "message": "Password updated", "data": null }`

---

## 3. Bins — `/bins`

Objek **Bin** (field dari DB):
```json
{
  "id": "ck...", "nodeId": "bin-001", "location": "Lobby Gedung A",
  "lat": -6.2, "lng": 106.8, "createdAt": "2026-05-01T...",
  "weightThreshold": null, "volumeThreshold": null, "gasThreshold": null, "batteryThreshold": null,
  "areaId": "ck..."
}
```

### GET `/bins` — _auth_
List semua bin (PETUGAS hanya area-nya), **diperkaya status live dari Redis**:
```json
{
  "success": true, "message": "Bins retrieved",
  "data": [
    {
      "id": "ck...", "nodeId": "bin-001", "location": "Lobby A", "lat": -6.2, "lng": 106.8,
      "areaId": "ck...", "createdAt": "...",
      "status": "online",                // "online" | "offline"
      "lastSeen": "2026-05-29T07:00:00Z", // null jika belum pernah konek
      "latest": {                          // null jika belum ada data sensor
        "weight": 12.5, "volume": 60, "battery": 88, "gas": 120, "rssi": -67,
        "timestamp": "2026-05-29T07:00:00Z", "logId": "ck..."
      }
    }
  ]
}
```

### GET `/bins/:id` — _auth_
Detail satu bin + threshold aktif.
```json
{
  "success": true, "message": "Success",
  "data": {
    "id": "ck...", "nodeId": "bin-001", "location": "Lobby A", "lat": -6.2, "lng": 106.8, "areaId": "ck...",
    "status": "online",
    "latest": { "weight": 12.5, "volume": 60, "battery": 88, "gas": 120, "rssi": -67, "timestamp": "...", "logId": "..." },
    "threshold": { "weight": 45, "volume": 85, "gas": 300, "battery": 20 }
  }
}
```

### GET `/bins/:id/history?limit=50&page=1` — _auth_
Riwayat data sensor (paginated, urut terbaru dulu).
```json
{
  "success": true, "message": "History retrieved",
  "data": [
    { "id": "ck...", "binId": "ck...", "weight": 12.5, "volume": 60, "battery": 88, "gas": 120, "rssi": -67, "createdAt": "..." }
  ],
  "pagination": { "total": 240, "page": 1, "limit": 50, "totalPages": 5 }
}
```

### GET `/bins/route/optimal?lat=<lat>&lng=<lng>` — _auth_
Hitung rute optimal (nearest-neighbor) menuju bin yang **penuh** (alert FULL_WEIGHT / FULL_VOLUME belum resolved), dari titik koordinat awal.
**Query wajib:** `lat`, `lng` (koordinat posisi petugas/awal).
```json
{
  "success": true, "message": "Rute optimal berhasil di-generate",
  "data": {
    "route": [
      { "id": "ck...", "nodeId": "bin-003", "location": "...", "lat": -6.21, "lng": 106.81,
        "alerts": [ ... ], "distanceFromPreviousKm": 0.8 }
    ],
    "googleMapsUrl": "https://www.google.com/maps/dir/?api=1&origin=...&destination=...&waypoints=...",
    "qrCodeBase64": "data:image/png;base64,iVBOR..."  // QR berisi link Google Maps
  }
}
```
> Jika tidak ada bin penuh: `{ "route": [], "googleMapsUrl": null, "message": "Bagus! Tidak ada tempat sampah yang penuh." }`

### POST `/bins` — **ADMIN**
**Body:**
```json
{ "nodeId": "bin-001", "location": "Lobby A", "lat": -6.2, "lng": 106.8, "areaId": "ck..." }
```
- `nodeId` (string, wajib, unik) · `location` (min 3) · `lat`/`lng` (number) · `areaId` (opsional/nullable)
- 409 jika `nodeId` sudah ada. Response 201 mengembalikan objek bin.

### PUT `/bins/:id` — **ADMIN**
Body sama seperti POST tapi **semua field opsional** (partial update).

### PUT `/bins/:id/threshold` — **ADMIN**
Set ambang batas alert per-bin. **Minimal 1 field** harus diisi.
```json
{ "weightThreshold": 50, "volumeThreshold": 90, "gasThreshold": 350, "batteryThreshold": 15 }
```
- `volumeThreshold` & `batteryThreshold`: 1–100 · `weightThreshold` & `gasThreshold`: angka positif
**Response:** `{ "weight": 50, "volume": 90, "gas": 350, "battery": 15 }`

### DELETE `/bins/:id` — **ADMIN**
`{ "success": true, "message": "Bin deleted", "data": null }`

---

## 4. Alerts — `/alerts`

Tipe alert (`type`): `FULL_WEIGHT` · `FULL_VOLUME` · `BATTERY_LOW` · `GAS_HIGH`

### GET `/alerts?resolved=false&page=1&limit=50` — _auth_
List alert (paginated, terbaru dulu). PETUGAS hanya area-nya.
- `resolved` (opsional): `true` / `false`. Kalau tidak diisi → semua.
```json
{
  "success": true, "message": "Alerts retrieved",
  "data": [
    {
      "id": "ck...", "binId": "ck...", "type": "FULL_VOLUME",
      "message": "Bin bin-001: Volume 90% has reached threshold 85%",
      "resolved": false, "createdAt": "...", "resolvedAt": null,
      "bin": { "nodeId": "bin-001", "location": "Lobby A" }
    }
  ],
  "pagination": { "total": 12, "page": 1, "limit": 50, "totalPages": 1 }
}
```

### PUT `/alerts/:id/resolve` — _auth_
Tandai alert selesai (PETUGAS hanya boleh resolve alert di areanya).
**Response:** objek alert dengan `resolved: true`, `resolvedAt` terisi.

---

## 5. Users — `/users` (**ADMIN only**)

Semua endpoint di sini butuh role ADMIN. Field `password` **tidak pernah** dikembalikan.

| Method | Path | Keterangan |
|---|---|---|
| GET | `/users` | List semua user (+ `area: {id, name}`) |
| GET | `/users/:id` | Detail user |
| POST | `/users` | Buat user (201) |
| PUT | `/users/:id` | Update (partial) |
| DELETE | `/users/:id` | Hapus user |

**Body POST `/users`:**
```json
{ "name": "Budi", "email": "budi@smartbin.id", "password": "rahasia6", "role": "PETUGAS", "areaId": "ck..." }
```
- `name` (min 2) · `email` (unik, 409 jika dobel) · `password` (min 6) · `role` (`ADMIN` | `PETUGAS`, default `PETUGAS`) · `areaId` (opsional/nullable)
- **PUT:** semua field opsional.

---

## 6. Areas — `/areas` (**ADMIN only**)

| Method | Path | Keterangan |
|---|---|---|
| GET | `/areas` | List area |
| GET | `/areas/:id` | Detail area |
| POST | `/areas` | Buat area (201) |
| PUT | `/areas/:id` | Update nama |
| DELETE | `/areas/:id` | Hapus (409 jika masih ada bin/user terhubung) |

**Body POST/PUT:** `{ "name": "Area Selatan" }` — `name` 3–100 char, unik (409 jika dobel).

---

## 7. Pickups — `/pickups`

Bukti pengambilan sampah oleh petugas. **Checkpoint hybrid:**
1. Petugas scan QR rute → buka Google Maps → datang ke lokasi.
2. Petugas tekan **"Selesai"** → `POST /pickups/:binId/complete` (catat siapa + kapan + GPS). Status awal `MENUNGGU_SENSOR`.
3. Sensor membaca bin sudah kosong (volume/berat turun) → alert FULL auto-resolve → pickup otomatis jadi `SELESAI` (`sensorConfirmedAt` terisi).

> Jadi admin bisa tahu **siapa** yang ambil, **kapan**, **di mana**, dan apakah sampah **benar-benar** terangkat (terverifikasi sensor). Pickup yang nyangkut di `MENUNGGU_SENSOR` = petugas menekan Selesai tapi sensor belum konfirmasi (perlu dicek).

Status (`status`): `MENUNGGU_SENSOR` · `SELESAI`

Objek **Pickup**:
```json
{
  "id": "ck...", "binId": "ck...", "petugasId": "ck...",
  "areaId": "ck...", "alertId": "ck...",        // alertId null jika tidak ada alert penuh aktif
  "status": "MENUNGGU_SENSOR",
  "completedAt": "2026-05-29T10:25:47Z",
  "completedLat": -6.2088, "completedLng": 106.8456,  // GPS saat tekan Selesai (bisa null)
  "sensorConfirmedAt": null,                    // terisi saat sensor konfirmasi
  "createdAt": "...",
  "bin": { "nodeId": "bin-001", "location": "Gedung A - Lantai 1", "lat": -6.2088, "lng": 106.8456 },
  "petugas": { "id": "ck...", "name": "Petugas Kebersihan", "email": "petugas@smartbin.local" }
}
```

### POST `/pickups/:binId/complete` — _auth (PETUGAS area-nya / ADMIN)_
Petugas menekan tombol **"Selesai"** setelah mengambil sampah.
**Body (semua opsional):**
```json
{ "lat": -6.2088, "lng": 106.8456 }
```
- `lat`/`lng` (number, opsional) — GPS petugas, **dicatat apa adanya** (tidak ada validasi jarak ke bin).
- PETUGAS hanya boleh complete bin di areanya (di luar area → 403).
- Otomatis mengaitkan alert `FULL_VOLUME`/`FULL_WEIGHT` aktif (kalau ada) ke `alertId`.
- **Response 201:** objek Pickup dengan `status: "MENUNGGU_SENSOR"`.

### GET `/pickups?status=&binId=&page=1&limit=50` — _auth_
Riwayat pickup (paginated, terbaru dulu). PETUGAS hanya area-nya.
- `status` (opsional): `MENUNGGU_SENSOR` / `SELESAI`
- `binId` (opsional): filter per bin
```json
{
  "success": true, "message": "Pickups retrieved",
  "data": [ { ...objek Pickup... } ],
  "pagination": { "total": 1, "page": 1, "limit": 50, "totalPages": 1 }
}
```

### GET `/pickups/:id` — _auth_
Detail satu pickup (PETUGAS hanya boleh akses pickup di areanya).

---

## 8. WebSocket — Real-time Updates

Backend mengirim update real-time (data sensor, status bin, alert, klasifikasi) lewat WebSocket.

**Koneksi:**
```
ws://localhost:3000?token=<JWT>
```
> Token JWT (dari login) **wajib** dikirim sebagai query param `token`. Tanpa token / token invalid → koneksi ditolak (401).

**Format setiap pesan:**
```json
{ "event": "NAMA_EVENT", "payload": { ... } }
```

**Saat pertama konek**, server kirim:
```json
{ "event": "CONNECTED", "payload": { "message": "SmartBin WebSocket ready" } }
```

### Daftar Event

| Event | Kapan | Payload |
|---|---|---|
| `BIN_UPDATE` | Data sensor baru masuk | `{ nodeId, binId, weight, volume, battery, gas, rssi, timestamp }` |
| `BIN_STATUS` | Bin online/offline berubah | `{ nodeId, status, lastSeen }` |
| `ALERT_NEW` | Alert baru terpicu | `{ alertId, nodeId, binId, type, message, createdAt, areaId }` |
| `ALERT_RESOLVED` | Alert auto-resolved (bin dikosongkan) | `{ alertId, nodeId, binId, type }` |
| `CLASSIFICATION_NEW` | Hasil klasifikasi sampah baru | `{ id, nodeId, binId, label, confidence, createdAt }` |
| `PICKUP_COMPLETED` | Petugas tekan "Selesai" (menunggu sensor) | `{ pickupId, binId, nodeId, petugasId, status, completedAt, areaId }` |
| `PICKUP_CONFIRMED` | Sensor konfirmasi pickup → `SELESAI` | `{ pickupId, binId, nodeId, petugasId, status, sensorConfirmedAt, areaId }` |

> `areaId` di `ALERT_NEW` berguna untuk filter di sisi FE (mis. hanya tampilkan alert untuk area petugas yang login).

> **Heartbeat:** server ping tiap 30 detik. Library WS biasanya auto-handle pong.

---

## 9. Catatan untuk Frontend

- **Klasifikasi sampah** belum punya endpoint REST untuk list/history — datanya saat ini hanya dikirim live via WebSocket (`CLASSIFICATION_NEW`) dan tersimpan di DB. Label yang mungkin: `organik` · `anorganik` · `b3` · `unknown`. (Beri tahu backend kalau FE butuh endpoint history klasifikasi.)
- Field `status` & `latest` pada bin berasal dari Redis (live). `status: "offline"` artinya bin tidak mengirim heartbeat dalam 3 menit terakhir.
- CORS di mode development mengizinkan semua origin (`*`); di production dibatasi sesuai `CORS_ORIGIN`.
- Semua timestamp dalam format ISO 8601 (UTC).
