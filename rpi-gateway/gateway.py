"""
SmartBin RPi Gateway
====================
Bridges LoRa packets from ESP32 sensor nodes to the SmartBin MQTT broker.

Flow:
    ESP32 --LoRa 923MHz--> RPi (this script) --MQTT--> Backend

Tasks performed:
    1. Receive LoRa packets, parse, publish to smartbin/{nodeId}/sensor
    2. Heartbeat: publish smartbin/{nodeId}/status = "online" every HEARTBEAT_INTERVAL
    3. (Optional) Camera capture for one bin → smartbin/{CAMERA_NODE_ID}/image

Run with: python gateway.py
Env config: see .env.example
"""

import os
import io
import json
import time
import signal
import base64
import logging
import threading
from typing import Iterator, Optional, Tuple

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "192.168.1.10")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USERNAME", "")
MQTT_PASS = os.getenv("MQTT_PASSWORD", "")
GATEWAY_ID = os.getenv("GATEWAY_ID", "rpi-gateway-01")

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "60"))
LORA_DRIVER = os.getenv("LORA_DRIVER", "rfm9x").lower()  # rfm9x | mock
LORA_FREQ = float(os.getenv("LORA_FREQ_MHZ", "923.0"))

CAMERA_NODE_ID = os.getenv("CAMERA_NODE_ID", "").strip()
CAMERA_INTERVAL = int(os.getenv("CAMERA_INTERVAL_SEC", "300"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gateway")

# ─── Shared state ────────────────────────────────────────────────────────────
running = True
known_nodes: set[str] = set()
known_nodes_lock = threading.Lock()


# ─── LoRa receiver ───────────────────────────────────────────────────────────
def lora_receiver() -> Iterator[str]:
    """Yield raw packet strings from LoRa (or mock)."""
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

    if LORA_DRIVER == "rfm9x":
        # Adafruit RFM9x driver — wiring expected:
        #   CS    → CE1   (GPIO 7,  pin 26)
        #   RST   → D25   (GPIO 25, pin 22)
        #   SCK   → SCLK  (GPIO 11, pin 23)
        #   MOSI  → MOSI  (GPIO 10, pin 19)
        #   MISO  → MISO  (GPIO 9,  pin 21)
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
    Try JSON first, then pipe-separated.

    Accepted formats:
        JSON: {"node":"bin-001","weight":45.2,"volume":87,"battery":78,"gas":150,"rssi":-65}
        JSON compact: {"n":"bin-001","w":45.2,"v":87,"b":78,"g":150,"r":-65}
        Pipe: bin-001|45.2|87|78|150|-65

    Returns: (nodeId, payload) or (None, None) if unparseable.
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


# ─── MQTT ────────────────────────────────────────────────────────────────────
def make_mqtt_client() -> mqtt.Client:
    # Use API v2 if available (paho-mqtt 2.x), fall back to default for 1.x
    try:
        cli = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=GATEWAY_ID,
            clean_session=True,
        )
        cli.on_connect = lambda c, u, f, rc, props=None: log.info(
            f"MQTT connected (rc={rc}) → {BROKER_HOST}:{BROKER_PORT}"
        )
        cli.on_disconnect = lambda c, u, f, rc, props=None: log.warning(
            f"MQTT disconnected (rc={rc})"
        )
    except (AttributeError, TypeError):
        cli = mqtt.Client(client_id=GATEWAY_ID, clean_session=True)
        cli.on_connect = lambda c, u, f, rc: log.info(
            f"MQTT connected (rc={rc}) → {BROKER_HOST}:{BROKER_PORT}"
        )
        cli.on_disconnect = lambda c, u, rc: log.warning(f"MQTT disconnected (rc={rc})")

    if MQTT_USER:
        cli.username_pw_set(MQTT_USER, MQTT_PASS)

    # LWT — if RPi crashes, broker auto-publishes offline for the gateway
    cli.will_set(
        f"smartbin/{GATEWAY_ID}/status",
        json.dumps({"status": "offline"}),
        qos=1,
        retain=True,
    )

    return cli


def publish_status(cli: mqtt.Client, node: str, status: str) -> None:
    cli.publish(
        f"smartbin/{node}/status",
        json.dumps({"status": status}),
        qos=1,
        retain=True,
    )


# ─── Heartbeat thread ────────────────────────────────────────────────────────
def heartbeat_loop(cli: mqtt.Client) -> None:
    while running:
        with known_nodes_lock:
            nodes = list(known_nodes)
        for node in nodes:
            publish_status(cli, node, "online")
        publish_status(cli, GATEWAY_ID, "online")
        if nodes:
            log.debug(f"♥ heartbeat sent for {len(nodes)} bin(s)")
        # sleep in small steps so SIGTERM is responsive
        for _ in range(HEARTBEAT_INTERVAL):
            if not running:
                return
            time.sleep(1)


# ─── Camera thread (optional) ────────────────────────────────────────────────
def camera_loop(cli: mqtt.Client) -> None:
    if not CAMERA_NODE_ID:
        return

    try:
        from picamera2 import Picamera2
    except ImportError:
        log.error("picamera2 not installed — `pip install picamera2` or unset CAMERA_NODE_ID")
        return

    cam = Picamera2()
    cam.configure(cam.create_still_configuration(main={"size": (640, 480)}))
    cam.start()
    log.info(f"📷 camera ready, capturing every {CAMERA_INTERVAL}s → {CAMERA_NODE_ID}")

    while running:
        for _ in range(CAMERA_INTERVAL):
            if not running:
                cam.stop()
                return
            time.sleep(1)

        try:
            buf = io.BytesIO()
            cam.capture_file(buf, format="jpeg")
            b64 = base64.b64encode(buf.getvalue()).decode()
            cli.publish(
                f"smartbin/{CAMERA_NODE_ID}/image",
                json.dumps({"image": b64}),
                qos=1,
            )
            log.info(f"📷 image published ({len(b64) // 1024} KB b64)")
        except Exception as e:
            log.error(f"camera capture failed: {e}")


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    global running

    cli = make_mqtt_client()
    cli.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    cli.loop_start()

    threading.Thread(target=heartbeat_loop, args=(cli,), daemon=True).start()
    if CAMERA_NODE_ID:
        threading.Thread(target=camera_loop, args=(cli,), daemon=True).start()

    def stop(signum, _frame):
        global running
        log.info(f"signal {signum} received — shutting down")
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info(f"gateway {GATEWAY_ID} online → {BROKER_HOST}:{BROKER_PORT}")
    publish_status(cli, GATEWAY_ID, "online")

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
            publish_status(cli, node, "online")

        cli.publish(f"smartbin/{node}/sensor", json.dumps(payload), qos=1)
        log.info(
            f"→ {node} | w={payload.get('weight')}kg "
            f"v={payload.get('volume')}% b={payload.get('battery')}% "
            f"g={payload.get('gas', '-')}ppm rssi={payload.get('rssi', '-')}"
        )

    # Graceful shutdown
    publish_status(cli, GATEWAY_ID, "offline")
    time.sleep(0.5)  # allow LWT/status to flush
    cli.loop_stop()
    cli.disconnect()
    log.info("gateway stopped cleanly")


if __name__ == "__main__":
    main()
