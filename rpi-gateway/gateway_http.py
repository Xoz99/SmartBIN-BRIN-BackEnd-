"""
SmartBin RPi Gateway — HTTP variant
===================================
Sama seperti gateway.py, TAPI mengirim data ke server pusat lewat HTTP POST
(bukan MQTT). Cocok kalau Raspi LoRa-RX hanya punya jalur internet biasa dan
tidak terhubung ke broker MQTT.

Flow:
    Node --LoRa 923MHz--> RPi (script ini) --HTTP POST--> Backend /ingest/sensor

Endpoint  : POST {BACKEND_URL}/ingest/sensor
Auth      : header  X-Device-Key: {DEVICE_INGEST_KEY}   (harus sama dgn .env backend)
Body      : {"nodeId": "...", "weight":.., "volume":.., "battery":.., "gas":.., "rssi":..}

Jalankan  : python gateway_http.py
Env config: lihat .env.example (BACKEND_URL, DEVICE_INGEST_KEY, LORA_*).
"""

import os
import re
import json
import time
import queue
import signal
import logging
import threading
from typing import Iterator, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://192.168.1.44:3000").rstrip("/")
DEVICE_KEY = os.getenv("DEVICE_INGEST_KEY", "")
GATEWAY_ID = os.getenv("GATEWAY_ID", "rpi-gateway-01")
POST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "10"))
POST_RETRIES = int(os.getenv("HTTP_RETRIES", "2"))

LORA_DRIVER = os.getenv("LORA_DRIVER", "rfm9x").lower()  # serial | rfm9x | mock
LORA_FREQ = float(os.getenv("LORA_FREQ_MHZ", "923.0"))

# Dipakai HANYA saat LORA_DRIVER=serial (board LoRa32/TTGO sbg RX, nyolok USB ke Raspi).
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

INGEST_URL = f"{BACKEND_URL}/ingest/sensor"

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gateway-http")

# ─── Shared state ────────────────────────────────────────────────────────────
running = True
known_nodes: set[str] = set()
known_nodes_lock = threading.Lock()


# ─── LoRa receiver ───────────────────────────────────────────────────────────
def lora_receiver() -> Iterator[str]:
    """Yield raw packet strings from LoRa (or mock). Identik dengan gateway.py."""
    if LORA_DRIVER == "mock":
        log.warning("LORA_DRIVER=mock — generating fake packets every 5s")
        import random
        nodes = ["bin-001", "bin-002", "bin-003"]
        while running:
            time.sleep(5)
            n = random.choice(nodes)
            yield json.dumps({
                "node": n,
                "weight": round(random.uniform(10, 60), 1),
                "volume": random.randint(20, 95),
                "battery": random.randint(40, 100),
                "gas": random.randint(50, 400),
                "rssi": random.randint(-90, -40),
            })
        return

    if LORA_DRIVER == "serial":
        # Board LoRa32 (LilyGO TTGO) jadi RX terpisah, nyolok ke Raspi via USB.
        # Firmware-nya (sketch .ino "LoRa Core") nge-print tiap paket LENGKAP sbg:
        #     SENSOR:<nodeId>:<json>
        # Baris lain ("[RF IN]...", "[OK]...", "[UNKNOWN RF]...") = log → diabaikan.
        # nodeId dari header board dianggap otoritatif dan disuntikkan ke JSON,
        # karena payload dari STM32 belum tentu punya field node sendiri.
        import serial  # pyserial

        # Board ngeprint metrik RF SEBELUM baris SENSOR:, contoh:
        #   [RF IN] Len: 42 | RSSI: -60 | SNR: 9.5
        #   SENSOR:bin-003:{...}
        # Kita tangkap RSSI/SNR/Len ASLI dari [RF IN], lalu tempelkan ke payload
        # SENSOR berikutnya (override rssi dummy dari node) — ini yang dipakai
        # buat penelitian kualitas link LoRa.
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

            # Baris metrik RF dari board → simpan buat ditempel ke SENSOR berikutnya
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
                continue  # log board lain, skip
            # Split hanya 2x → bagian ke-3 tetap JSON utuh walau isinya ada ':'
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
            d["nodeId"] = node  # header board menang atas field di JSON
            # Tempel metrik RF ASLI (override rssi dummy dari node)
            if pending_rf is not None:
                d["rssi"] = pending_rf["rssi"]
                d["snr"] = pending_rf["snr"]
                d["packetLen"] = pending_rf["packetLen"]
                pending_rf = None
            yield json.dumps(d)
        ser.close()
        return

    if LORA_DRIVER == "rfm9x":
        # Adafruit RFM9x — wiring sama seperti gateway.py:
        #   CS→CE1(pin26)  RST→D25(pin22)  SCK→SCLK(23)  MOSI→(19)  MISO→(21)
        # PENTING: freq & modem config HARUS sama dgn firmware TX (lihat catatan
        # kompatibilitas RF). Default RFM9x = SF7 / BW125k / CR4-5 / sync 0x12.
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
            try:
                yield pkt.decode("utf-8")
            except UnicodeDecodeError:
                log.warning("non-UTF8 packet dropped")
        return

    raise RuntimeError(f"Unknown LORA_DRIVER: {LORA_DRIVER}")


# ─── Packet parser ───────────────────────────────────────────────────────────
def parse_packet(text: str) -> Tuple[Optional[str], Optional[dict]]:
    """
    Try JSON first, then pipe-separated. Identik dengan gateway.py.

    Format diterima:
        JSON        : {"node":"bin-001","weight":45.2,"volume":87,"battery":78,"gas":150,"rssi":-65}
        JSON compact: {"n":"bin-001","w":45.2,"v":87,"b":78,"g":150,"r":-65}
        Pipe        : bin-001|45.2|87|78|150|-65

    Returns: (nodeId, payload) atau (None, None) kalau tak bisa diparse.
    """
    text = text.strip()

    # Try JSON
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
        gas = data.get("gas", data.get("g"))
        if gas is not None:
            payload["gas"] = float(gas)
        rssi = data.get("rssi", data.get("r"))
        if rssi is not None:
            payload["rssi"] = int(rssi)
        # Metrik link LoRa (dari baris [RF IN] board) — buat penelitian
        snr = data.get("snr")
        if snr is not None:
            payload["snr"] = float(snr)
        plen = data.get("packetLen")
        if plen is not None:
            payload["packetLen"] = int(plen)
        # Metadata perbandingan transport (dari device, lewat LoRa)
        seq = data.get("seq")
        if seq is not None:
            payload["seq"] = int(seq)
        sent_at = data.get("sentAt")
        if sent_at is not None:
            payload["sentAt"] = str(sent_at)
        return node, payload
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try pipe-separated
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


# ─── HTTP poster ─────────────────────────────────────────────────────────────
_session = requests.Session()
if DEVICE_KEY:
    _session.headers.update({"X-Device-Key": DEVICE_KEY})
_session.headers.update({"Content-Type": "application/json"})


def post_sensor(node: str, payload: dict) -> bool:
    """POST satu bacaan ke backend. Retry sederhana dgn backoff. True kalau sukses."""
    # Tandai jalur = LoRa (dibandingkan dgn node yang POST HTTP langsung).
    body = {"nodeId": node, "transport": "lora", **payload}
    for attempt in range(POST_RETRIES + 1):
        try:
            r = _session.post(INGEST_URL, json=body, timeout=POST_TIMEOUT)
            if r.status_code in (200, 201, 202):
                return True
            # 404 = nodeId belum terdaftar di backend, retry tak akan menolong.
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


# ─── Antrean + worker POST ───────────────────────────────────────────────────
# Baca serial dan POST HTTP dipisah thread. Kalau digabung, POST yang lambat/gagal
# (timeout + retry sleep) bikin serial berhenti dibaca → paket numpuk di buffer →
# update datang beruntun (ngestack) + sebagian kebuang. Antrean menjaga serial
# selalu terkuras cepat; worker yang menanggung latensi/retry POST.
_post_q: "queue.Queue[Tuple[str, dict]]" = queue.Queue(maxsize=2000)


def _poster_worker() -> None:
    """Ambil bacaan dari antrean lalu POST ke backend. Jalan di thread sendiri."""
    while running:
        try:
            node, payload = _post_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            ok = post_sensor(node, payload)
            flag = "✓" if ok else "✗"
            log.info(
                f"{flag} {node} | w={payload.get('weight')}kg "
                f"v={payload.get('volume')}% b={payload.get('battery')}% "
                f"g={payload.get('gas', '-')}ppm "
                f"rssi={payload.get('rssi', '-')}dBm snr={payload.get('snr', '-')}dB "
                f"len={payload.get('packetLen', '-')}B | antre={_post_q.qsize()}"
            )
        finally:
            _post_q.task_done()


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    global running

    if not DEVICE_KEY:
        log.warning("DEVICE_INGEST_KEY kosong — request dikirim tanpa auth "
                    "(hanya jalan kalau backend juga tak set key).")

    def stop(signum, _frame):
        global running
        log.info(f"signal {signum} received — shutting down")
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info(f"gateway-http {GATEWAY_ID} → {INGEST_URL} (driver={LORA_DRIVER})")

    # Worker POST di thread terpisah supaya loop baca serial tak pernah berhenti.
    poster = threading.Thread(target=_poster_worker, daemon=True)
    poster.start()

    for raw in lora_receiver():
        if not running:
            break
        node, payload = parse_packet(raw)
        if not node:
            log.warning(f"unparseable packet: {raw[:80]!r}")
            continue

        with known_nodes_lock:
            is_new = node not in known_nodes
            known_nodes.add(node)
        if is_new:
            log.info(f"+ new bin discovered: {node}")

        # Masukkan ke antrean (non-blocking). Kalau penuh (backend lama down),
        # buang bacaan TERLAMA supaya data terbaru tetap masuk.
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

    log.info("gateway-http stopped cleanly")


if __name__ == "__main__":
    main()
