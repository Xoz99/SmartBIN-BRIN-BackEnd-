"""
SmartBin RPi Gateway — UNIFIED (LoRa + HTTP jadi satu)
======================================================
Satu proses di Raspi RX dengan DUA pintu masuk, keduanya diukur di titik yang
SAMA (Raspi) lalu diteruskan ke backend pusat lewat HTTP POST /ingest/sensor.

  Jalur LoRa : Node TX --RF 923MHz--> board RX --serial--> gateway ini
               → metrik RF diukur di sini: rssi, snr, packetLen, throughput (airtime)
               → transport = "lora"

  Jalur HTTP : Node --WiFi POST /ingest/sensor--> gateway ini (HTTP server di Raspi)
               → metrik diukur di sini: ukuran paket, throughput
               → transport = "http"

Keduanya masuk satu antrean → satu worker POST → backend pusat. Backend menghitung
latencyMs (createdAt − sentAt) dan mendeteksi packet loss dari `seq`. Jadi jalur
LoRa vs HTTP bisa dibandingkan adil karena diukur dari titik yang sama.

Jalankan : python gateway_unified.py
Env      : lihat .env.example — plus tambahan HTTP server & LoRa RF di bawah.
"""

import os
import re
import json
import math
import time
import queue
import signal
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Config: backend tujuan (teruskan ke sini) ───────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://192.168.1.44:3000").rstrip("/")
DEVICE_KEY = os.getenv("DEVICE_INGEST_KEY", "")
GATEWAY_ID = os.getenv("GATEWAY_ID", "rpi-gateway-01")
POST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "10"))
POST_RETRIES = int(os.getenv("HTTP_RETRIES", "2"))
INGEST_URL = f"{BACKEND_URL}/ingest/sensor"

# ─── Config: pintu masuk LoRa (board RX via serial) ──────────────────────────
LORA_DRIVER = os.getenv("LORA_DRIVER", "serial").lower()  # serial | rfm9x | mock | none
LORA_FREQ = float(os.getenv("LORA_FREQ_MHZ", "923.0"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
# Parameter RF LoRa — HARUS sama dengan firmware TX. Dipakai untuk hitung airtime
# → throughput. Default Adafruit/umum: SF7, BW125kHz, CR4/5.
LORA_SF = int(os.getenv("LORA_SF", "7"))
LORA_BW_HZ = int(os.getenv("LORA_BW_HZ", "125000"))
LORA_CR_DENOM = int(os.getenv("LORA_CR_DENOM", "5"))       # 5=4/5 .. 8=4/8
LORA_PREAMBLE = int(os.getenv("LORA_PREAMBLE", "8"))

# ─── Config: pintu masuk HTTP (server di Raspi ini) ──────────────────────────
HTTP_ENABLE = os.getenv("GATEWAY_HTTP_ENABLE", "1") not in ("0", "false", "no")
HTTP_LISTEN_HOST = os.getenv("GATEWAY_HTTP_HOST", "0.0.0.0")
HTTP_LISTEN_PORT = int(os.getenv("GATEWAY_HTTP_PORT", "8080"))
# Kunci opsional untuk endpoint HTTP Raspi ini. Kosong = terbuka (LAN penelitian).
# Kalau diisi, node harus kirim header X-Device-Key yang sama.
GATEWAY_INGEST_KEY = os.getenv("GATEWAY_INGEST_KEY", "")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gateway-unified")

# ─── Shared state ────────────────────────────────────────────────────────────
running = True
known_nodes: set[str] = set()
known_nodes_lock = threading.Lock()


# ─── Hitung airtime LoRa → throughput ────────────────────────────────────────
def lora_airtime_s(payload_len: int) -> float:
    """Waktu-di-udara (detik) satu paket LoRa (rumus Semtech SX127x).
    Dipakai untuk throughput RF = payload_len*8 / airtime."""
    sf, bw, cr = LORA_SF, LORA_BW_HZ, LORA_CR_DENOM - 4
    de = 1 if sf >= 11 else 0                 # low data rate optimize
    t_sym = (2 ** sf) / bw
    t_preamble = (LORA_PREAMBLE + 4.25) * t_sym
    num = 8 * payload_len - 4 * sf + 28 + 16 * 1 - 20 * 0  # CRC on, explicit header
    den = 4 * (sf - 2 * de)
    n_payload = 8 + max(math.ceil(num / den) * (cr + 4), 0)
    return t_preamble + n_payload * t_sym


def throughput_bps(payload_len: Optional[int], airtime_s: Optional[float]) -> Optional[float]:
    if not payload_len or not airtime_s or airtime_s <= 0:
        return None
    return (payload_len * 8) / airtime_s


# ─── LoRa receiver ───────────────────────────────────────────────────────────
def lora_receiver() -> Iterator[str]:
    """Yield raw packet strings dari LoRa (atau mock). Sama seperti gateway_http.py."""
    if LORA_DRIVER == "mock":
        log.warning("LORA_DRIVER=mock — generating fake packets every 5s")
        import random
        nodes = ["bin-001", "bin-002", "bin-003"]
        seq = 0
        while running:
            time.sleep(5)
            seq += 1
            n = random.choice(nodes)
            yield json.dumps({
                "node": n,
                "weight": round(random.uniform(10, 60), 1),
                "volume": random.randint(20, 95),
                "battery": random.randint(40, 100),
                "gas": random.randint(50, 400),
                "rssi": random.randint(-90, -40),
                "snr": round(random.uniform(-5, 10), 1),
                "packetLen": random.randint(30, 60),
                "seq": seq,
            })
        return

    if LORA_DRIVER == "serial":
        # Board LoRa32 (LilyGO TTGO) sbg RX, nyolok ke Raspi via USB. Firmware "LoRa
        # Core" ngeprint tiap paket: SENSOR:<nodeId>:<json>, didahului baris metrik:
        #   [RF IN] Len: 42 | RSSI: -60 | SNR: 9.5
        import serial  # pyserial
        rf_re = re.compile(r"Len:\s*(\d+).*?RSSI:\s*(-?\d+).*?SNR:\s*(-?[\d.]+)")
        pending_rf = None

        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        log.info(f"LoRa serial ready @ {SERIAL_PORT} {SERIAL_BAUD}bd")
        while running:
            try:
                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
            except serial.SerialException as e:
                log.error(f"serial error: {e} — reconnect 2s")
                time.sleep(2)
                continue
            if not raw_line:
                continue

            if raw_line.startswith("[RF IN]"):
                m = rf_re.search(raw_line)
                if m:
                    pending_rf = {
                        "packetLen": int(m.group(1)),
                        "rssi": int(m.group(2)),
                        "snr": float(m.group(3)),
                    }
                continue

            if not raw_line.startswith("SENSOR:"):
                continue
            parts = raw_line.split(":", 2)
            if len(parts) < 3:
                continue
            node = parts[1].strip()
            payload_json = parts[2].strip()
            try:
                d = json.loads(payload_json)
            except json.JSONDecodeError:
                log.warning(f"SENSOR line JSON invalid: {payload_json[:80]!r}")
                continue
            if not isinstance(d, dict):
                continue
            d["nodeId"] = node
            if pending_rf is not None:
                d["rssi"] = pending_rf["rssi"]
                d["snr"] = pending_rf["snr"]
                d["packetLen"] = pending_rf["packetLen"]
                pending_rf = None
            yield json.dumps(d)
        ser.close()
        return

    if LORA_DRIVER == "rfm9x":
        import board, busio, digitalio
        from adafruit_rfm9x import RFM9x
        cs = digitalio.DigitalInOut(board.CE1)
        reset = digitalio.DigitalInOut(board.D25)
        spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        rfm = RFM9x(spi, cs, reset, LORA_FREQ)
        rfm.tx_power = 23
        log.info(f"LoRa RFM9x ready @ {LORA_FREQ}MHz")
        while running:
            pkt = rfm.receive(timeout=2.0)
            if pkt is None:
                continue
            # Metrik RF dari radio langsung — tempel ke payload.
            try:
                d = json.loads(pkt.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                log.warning("paket RFM9x tak bisa diparse — dibuang")
                continue
            if isinstance(d, dict):
                d.setdefault("rssi", rfm.last_rssi)
                d.setdefault("snr", getattr(rfm, "last_snr", None))
                d.setdefault("packetLen", len(pkt))
                yield json.dumps(d)
        return

    if LORA_DRIVER == "none":
        log.info("LORA_DRIVER=none — pintu LoRa dimatikan, hanya HTTP.")
        while running:
            time.sleep(1)
        return

    raise RuntimeError(f"Unknown LORA_DRIVER: {LORA_DRIVER}")


# ─── Packet parser (dari string LoRa) ────────────────────────────────────────
def parse_packet_full(text: str) -> Tuple[Optional[str], Optional[dict]]:
    """Ambil (nodeId, payload PENUH). Untuk payload EcoSort bertingkat (battery &
    kompartemen sebagai object), teruskan JSON APA ADANYA — backend yang flatten
    (normalizeSensorPayload). Ini penting: parser lama coba float(battery) padahal
    battery = object → TypeError → paket ke-DROP. Fallback ke pipe utk format lama."""
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            node = data.get("nodeId") or data.get("node") or data.get("n")
            if not node:
                return None, None
            # nodeId dikirim terpisah → keluarkan dari payload.
            payload = {k: v for k, v in data.items() if k not in ("nodeId", "node", "n")}
            # Kompat format compact lama (w/v/b/g/r) → nama penuh. Tidak menyentuh
            # payload nested (kunci 'battery'/'organik'/dst tetap utuh).
            for short, full in (("w", "weight"), ("v", "volume"), ("b", "battery"),
                                ("g", "gas"), ("r", "rssi")):
                if short in payload and full not in payload:
                    payload[full] = payload.pop(short)
            return node, payload
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Fallback: format pipe lama  node|weight|volume|battery|gas|rssi
    return parse_packet(text)


def parse_packet(text: str) -> Tuple[Optional[str], Optional[dict]]:
    """JSON dulu, lalu pipe-separated. Sama seperti gateway_http.py."""
    text = text.strip()
    try:
        data = json.loads(text)
        node = data.get("node") or data.get("nodeId") or data.get("n")
        if not node:
            return None, None
        payload = {
            "weight": float(data.get("weight", data.get("w", 0))),
            "volume": float(data.get("volume", data.get("v", 0))),
            "battery": float(data.get("battery", data.get("b", 0))),
        }
        for k_src, k_dst, cast in (
            ("gas", "gas", float), ("g", "gas", float),
            ("rssi", "rssi", int), ("r", "rssi", int),
            ("snr", "snr", float),
            ("packetLen", "packetLen", int),
            ("seq", "seq", int),
        ):
            v = data.get(k_src)
            if v is not None and k_dst not in payload:
                payload[k_dst] = cast(v)
        sent_at = data.get("sentAt")
        if sent_at is not None:
            payload["sentAt"] = str(sent_at)
        return node, payload
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    parts = text.split("|")
    if len(parts) >= 4:
        try:
            node = parts[0].strip()
            payload = {
                "weight": float(parts[1]),
                "volume": float(parts[2]),
                "battery": float(parts[3]),
            }
            if len(parts) >= 5 and parts[4]:
                payload["gas"] = float(parts[4])
            if len(parts) >= 6 and parts[5]:
                payload["rssi"] = int(parts[5])
            return node, payload
        except ValueError:
            pass
    return None, None


# ─── Antrean + worker POST ke backend ────────────────────────────────────────
_post_q: "queue.Queue[Tuple[str, dict]]" = queue.Queue(maxsize=2000)

_session = requests.Session()
if DEVICE_KEY:
    _session.headers.update({"X-Device-Key": DEVICE_KEY})
_session.headers.update({"Content-Type": "application/json"})


def enqueue(node: str, payload: dict) -> None:
    """Masukkan bacaan ke antrean. Kalau penuh, buang yang TERLAMA agar data
    terbaru tetap masuk (backend lama down tidak menyumbat serial/HTTP)."""
    with known_nodes_lock:
        is_new = node not in known_nodes
        known_nodes.add(node)
    if is_new:
        log.info(f"+ new bin discovered: {node}")
    try:
        _post_q.put_nowait((node, payload))
    except queue.Full:
        try:
            _post_q.get_nowait()
            _post_q.task_done()
        except queue.Empty:
            pass
        try:
            _post_q.put_nowait((node, payload))
        except queue.Full:
            pass
        log.warning("antrean POST penuh — bacaan terlama dibuang")


def post_sensor(node: str, payload: dict) -> bool:
    """POST satu bacaan ke backend pusat. Retry sederhana. True kalau sukses.
    transport & metrik ikut apa adanya di payload (di-set saat ingest)."""
    body = {"nodeId": node, **payload}
    for attempt in range(POST_RETRIES + 1):
        try:
            r = _session.post(INGEST_URL, json=body, timeout=POST_TIMEOUT)
            if r.status_code in (200, 201, 202):
                return True
            if r.status_code == 404:
                log.error(f"nodeId '{node}' belum terdaftar di backend (404) — skip")
                return False
            if r.status_code == 401:
                log.error("X-Device-Key ditolak (401) — cek DEVICE_INGEST_KEY")
                return False
            log.warning(f"POST {node} → HTTP {r.status_code}: {r.text[:120]}")
        except requests.RequestException as e:
            log.warning(f"POST {node} gagal (attempt {attempt + 1}): {e}")
        if attempt < POST_RETRIES:
            time.sleep(1.5 * (attempt + 1))
    return False


def _poster_worker() -> None:
    while running:
        try:
            node, payload = _post_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            ok = post_sensor(node, payload)
            flag = "✓" if ok else "✗"
            tp = payload.get("throughputBps")
            # Payload nested EcoSort tak punya weight/volume flat → tampilkan berat_g.
            w = payload.get('weight', payload.get('berat_g', '-'))
            log.info(
                f"{flag} [{payload.get('transport', '?'):>4}] {node} | "
                f"w={w} v={payload.get('volume', '-')} "
                f"rssi={payload.get('rssi', '-')}dBm snr={payload.get('snr', '-')}dB "
                f"len={payload.get('packetLen', '-')}B "
                f"tp={f'{tp:.0f}bps' if tp else '-'} "
                f"seq={payload.get('seq', '-')} | antre={_post_q.qsize()}"
            )
        finally:
            _post_q.task_done()


# ─── Pintu masuk HTTP (server di Raspi ini) ──────────────────────────────────
class _IngestHandler(BaseHTTPRequestHandler):
    server_version = "SmartBinGateway/1.0"

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("/health", ""):
            self._reply(200, {"success": True, "message": "gateway-unified up",
                              "gateway": GATEWAY_ID})
        else:
            self._reply(404, {"success": False, "message": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") not in ("/ingest/sensor", "/ingest"):
            self._reply(404, {"success": False, "message": "not found"})
            return
        if GATEWAY_INGEST_KEY and self.headers.get("X-Device-Key") != GATEWAY_INGEST_KEY:
            self._reply(401, {"success": False, "message": "invalid device key"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._reply(400, {"success": False, "message": "invalid json"})
            return
        node = body.get("nodeId") or body.get("node")
        if not node:
            self._reply(400, {"success": False, "message": "nodeId is required"})
            return

        # payload = body.payload kalau ada, kalau tidak seluruh body minus identitas.
        if isinstance(body.get("payload"), dict):
            payload = dict(body["payload"])
        else:
            payload = {k: v for k, v in body.items() if k not in ("nodeId", "node")}

        # Metrik jalur HTTP diukur DI RASPI: ukuran paket + throughput (size/latency).
        payload["transport"] = "http"
        size = len(raw)
        payload.setdefault("packetLen", size)
        tp = None
        sent_at = payload.get("sentAt")
        if sent_at:
            try:
                import datetime as _dt
                t0 = _dt.datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
                lat_s = max(1e-3, _dt.datetime.now(_dt.timezone.utc).timestamp() - t0.timestamp())
                tp = (size * 8) / lat_s
            except Exception:
                pass
        if tp:
            payload["throughputBps"] = round(tp, 1)

        enqueue(node, payload)
        self._reply(202, {"success": True, "message": "accepted (gateway http)",
                          "nodeId": node})

    def log_message(self, *args):  # matikan log bawaan http.server (kita pakai logger sendiri)
        return


def _http_server_thread() -> None:
    httpd = ThreadingHTTPServer((HTTP_LISTEN_HOST, HTTP_LISTEN_PORT), _IngestHandler)
    httpd.daemon_threads = True
    log.info(f"HTTP ingest ready @ http://{HTTP_LISTEN_HOST}:{HTTP_LISTEN_PORT}/ingest/sensor"
             + ("  (butuh X-Device-Key)" if GATEWAY_INGEST_KEY else "  (terbuka)"))
    while running:
        httpd.timeout = 1.0
        httpd.handle_request()
    httpd.server_close()


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    global running

    if not DEVICE_KEY:
        log.warning("DEVICE_INGEST_KEY kosong — POST ke backend tanpa auth "
                    "(hanya jalan kalau backend juga tak set key).")

    def stop(signum, _frame):
        global running
        log.info(f"signal {signum} received — shutting down")
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info(f"gateway-unified {GATEWAY_ID} → {INGEST_URL} "
             f"(lora={LORA_DRIVER}, http={'on' if HTTP_ENABLE else 'off'})")

    threading.Thread(target=_poster_worker, daemon=True).start()
    if HTTP_ENABLE:
        threading.Thread(target=_http_server_thread, daemon=True).start()

    # Pintu LoRa dijalankan di thread utama (kecuali dimatikan).
    for raw in lora_receiver():
        if not running:
            break
        node, payload = parse_packet_full(raw)  # teruskan payload penuh (nested EcoSort OK)
        if not node:
            log.warning(f"unparseable packet: {raw[:80]!r}")
            continue
        payload["transport"] = "lora"
        # Throughput RF dari airtime LoRa (butuh packetLen).
        plen = payload.get("packetLen")
        if plen:
            tp = throughput_bps(plen, lora_airtime_s(plen))
            if tp:
                payload["throughputBps"] = round(tp, 1)
        enqueue(node, payload)

    log.info("gateway-unified stopped cleanly")


if __name__ == "__main__":
    main()
