# SmartBin Backend

> Node.js + Express + Prisma + MQTT + WebSocket backend for a Smart Bin IoT system.

## Architecture

```
ESP32 (sensor) → LoRa 923MHz → Raspberry Pi 4 → MQTT → Node.js Backend
                                                            ├── PostgreSQL (Prisma)
                                                            ├── Redis (cache/thresholds)
                                                            ├── WebSocket (real-time)
                                                            └── Python FastAPI (YOLOv5)
```

---

## Quick Start (Local)

### 1. Prerequisites

| Tool | Version |
|------|---------|
| Node.js | ≥ 18 |
| PostgreSQL | 15+ |
| Redis | 7+ |
| Mosquitto | 2+ |
| Python | 3.11+ (for classify service) |

### 2. Install dependencies

```bash
npm install
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your local credentials
```

### 4. Run database migrations

```bash
npx prisma migrate dev --name init
```

### 5. Seed sample data

```bash
npm run seed
```

Output:
```
Admin login:   admin@smartbin.local / admin123
Petugas login: petugas@smartbin.local / petugas123
```

### 6. Start Mosquitto (locally)

```bash
mosquitto -c mosquitto/mosquitto.conf
```

### 7. Run development server

```bash
npm run dev
```

Server starts on `http://localhost:3000`

---

## Simulate IoT Data (MQTT)

In a separate terminal, run the simulator to publish fake sensor data every 5 seconds:

```bash
npm run simulate
```

This will publish to `smartbin/bin-001/sensor`, `smartbin/bin-002/sensor`, `smartbin/bin-003/sensor` with gradually increasing weight/volume until they exceed thresholds (triggering alerts).

---

## Python Classify Service

```bash
cd classify
pip install -r requirements.txt

# Place your YOLOv5 model file at:
# classify/model/best.pt

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `GET http://localhost:8000/health`

---

## API Endpoints

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/auth/login` | ❌ | Get JWT token |
| GET | `/bins` | ✅ | List all bins with live status |
| GET | `/bins/:id` | ✅ | Single bin details |
| GET | `/bins/:id/history` | ✅ | Paginated sensor logs |
| PUT | `/bins/:id/threshold` | ✅ ADMIN | Set weight/volume thresholds |
| GET | `/alerts` | ✅ | List alerts (filter: `?resolved=false`) |
| PUT | `/alerts/:id/resolve` | ✅ | Mark alert resolved |
| POST | `/classify` | ✅ | Classify waste image |
| GET | `/health` | ❌ | Health check |

### Example cURL

```bash
# Login
curl -X POST http://localhost:3000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@smartbin.local","password":"admin123"}'

# Get bins (use token from login)
curl http://localhost:3000/bins \
  -H "Authorization: Bearer <token>"

# Classify image
curl -X POST http://localhost:3000/classify \
  -H "Authorization: Bearer <token>" \
  -F "image=@/path/to/waste.jpg"

# Get unresolved alerts
curl "http://localhost:3000/alerts?resolved=false" \
  -H "Authorization: Bearer <token>"

# Resolve alert
curl -X PUT http://localhost:3000/alerts/<alertId>/resolve \
  -H "Authorization: Bearer <token>"

# Set threshold (admin only)
curl -X PUT http://localhost:3000/bins/<binId>/threshold \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"weightThreshold":40,"volumeThreshold":80}'
```

---

## WebSocket Events

Connect to `ws://localhost:3000` to receive real-time events.

| Event | Payload |
|-------|---------|
| `BIN_UPDATE` | `{ nodeId, binId, weight, volume, battery, rssi, timestamp }` |
| `ALERT_NEW` | `{ alertId, nodeId, binId, type, message, createdAt }` |
| `BIN_STATUS` | `{ nodeId, status: 'online'\|'offline', lastSeen }` |

---

## Docker (Production)

### Setup

```bash
cp .env.example .env
# Place YOLOv5 model at: classify/model/best.pt
# Place Firebase credentials at: firebase-credentials.json (optional)
```

### Start all services

```bash
docker compose up -d
```

### Run migrations inside container

```bash
docker compose exec backend npx prisma migrate deploy
docker compose exec backend node scripts/seed.js
```

### Services

| Service | Port |
|---------|------|
| Backend (Node.js) | 3000 |
| Classify (Python) | 8000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Mosquitto MQTT | 1883 |

---

## MQTT Topics

| Topic | Direction | Payload |
|-------|-----------|---------|
| `smartbin/{nodeId}/sensor` | ESP32 → Backend | `{ weight, volume, battery, rssi }` |
| `smartbin/{nodeId}/status` | ESP32 → Backend | `{ status: "online"\|"offline" }` |
| `smartbin/{nodeId}/image` | ESP32 → Backend | base64 image string |

---

## Project Structure

```
smartbin-backend/
├── src/
│   ├── config/          # env, db, redis, mqtt
│   ├── mqtt/            # subscriber, handlers
│   ├── api/             # routes, controllers, middlewares
│   ├── services/        # business logic
│   ├── models/          # Prisma query layer
│   ├── websocket/       # ws.js
│   └── utils/           # logger, response
├── classify/            # Python FastAPI YOLOv5 microservice
├── scripts/             # seed.js, simulate-mqtt.js
├── prisma/              # schema.prisma, migrations
├── mosquitto/           # mosquitto.conf
├── Dockerfile
├── docker-compose.yml
└── server.js
```
