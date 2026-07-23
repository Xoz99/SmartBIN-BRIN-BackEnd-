"""
Sensor Seeder — 2 jalur: LoRa (serial) & HTTP/internet (POST langsung).
=======================================================================
Ditaruh di folder backend Raspi TX (bin-003). Memancing data untuk MEMBANDINGKAN
dua jalur komunikasi ke backend:

  LoRa : tulis JSON ke modul LoRa (serial) → RF → board RX → gateway → backend
  HTTP : POST JSON langsung ke backend /ingest/sensor lewat internet (WiFi)

Tiap paket bawa `seq` (nomor urut) + `sentAt` (jam device) supaya backend bisa
hitung PACKET LOSS (dari lompatan seq) dan LATENCY (createdAt − sentAt). Karena
kedua jalur pakai device & jam yang sama, offset jam saling meniadakan saat
dibandingkan → perbandingan latency tetap sahih.

Contoh:
    python3 lora_tx_seed.py                        # jalur LoRa (serial), default port
    python3 lora_tx_seed.py --http                 # jalur HTTP/internet langsung
    python3 lora_tx_seed.py --hybrid               # LoRa + HTTP SEKALIGUS (perbandingan adil)
    python3 lora_tx_seed.py --http --count 20 --interval 1
    python3 lora_tx_seed.py --dry-run              # cetak JSON saja
    LORA_PORT=/dev/ttyUSB0 python3 lora_tx_seed.py

Mode HYBRID: tiap bacaan dikirim via LoRa (serial→RF→RX→gateway) DAN HTTP (POST
langsung ke server) dengan seq & sentAt IDENTIK. Butuh modul LoRa (serial) +
internet. BACKEND_URL = server tujuan HTTP (mis. VPS). Contoh:
    BACKEND_URL=https://vps DEVICE_INGEST_KEY=xxx python3 lora_tx_seed.py --hybrid

Env:
    LORA_PORT   default /dev/ttyACM0   (mode LoRa)
    BAUD_RATE   default 115200
    NODE_ID     default bin-003
    BACKEND_URL default http://192.168.1.44:3000   (mode --http)
    DEVICE_INGEST_KEY  (mode --http; harus sama dgn .env backend)
"""

import os
import json
import time
import random
import argparse
from datetime import datetime, timezone

DEFAULT_PORT = os.environ.get("LORA_PORT", "/dev/ttyACM0")
BAUD_RATE = int(os.environ.get("BAUD_RATE", "115200"))
NODE_ID = os.environ.get("NODE_ID", "bin-003")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://192.168.1.44:3000").rstrip("/")
DEVICE_KEY = os.environ.get("DEVICE_INGEST_KEY", "")


def make_reading(seq: int) -> dict:
    """Satu bacaan dummy + metadata penelitian (seq, sentAt).
    Key sensor SAMA dgn yang dibaca gateway_http.py / backend."""
    return {
        "nodeId": NODE_ID,
        "weight": round(random.uniform(5, 60), 1),   # kg
        "volume": random.randint(10, 95),            # % penuh
        "battery": random.randint(40, 100),          # %
        "gas": random.randint(50, 400),              # ppm
        "rssi": random.randint(-90, -40),            # dBm (LoRa asli diisi board RX)
        "seq": seq,                                  # nomor urut → deteksi packet loss
        "sentAt": datetime.now(timezone.utc).isoformat(),  # jam device → latency
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed data sensor: jalur LoRa (serial), HTTP/internet, atau HYBRID (dua-duanya sekaligus).")
    ap.add_argument("--http", action="store_true", help="kirim via HTTP POST /ingest/sensor (bukan LoRa serial)")
    ap.add_argument("--hybrid", action="store_true", help="kirim TIAP bacaan lewat LoRa DAN HTTP sekaligus (seq & sentAt sama → perbandingan adil)")
    ap.add_argument("--port", default=DEFAULT_PORT, help=f"port serial LoRa (default {DEFAULT_PORT})")
    ap.add_argument("--baud", type=int, default=BAUD_RATE, help=f"baud rate (default {BAUD_RATE})")
    ap.add_argument("--backend", default=BACKEND_URL, help=f"URL backend utk --http (default {BACKEND_URL})")
    ap.add_argument("--interval", type=float, default=5.0, help="detik antar kirim (default 5)")
    ap.add_argument("--count", type=int, default=0, help="jumlah paket, 0 = terus-menerus (default 0)")
    ap.add_argument("--start-seq", type=int, default=1, help="nomor urut awal (default 1)")
    ap.add_argument("--dry-run", action="store_true", help="cetak JSON saja, tidak kirim")
    args = ap.parse_args()

    mode = "HYBRID" if args.hybrid else ("HTTP" if args.http else "LoRa")
    use_serial = mode in ("LoRa", "HYBRID")   # tulis ke modul LoRa (→ RF → RX → gateway)
    use_http = mode in ("HTTP", "HYBRID")     # POST langsung ke server via internet
    ser = None
    session = None
    ingest_url = f"{args.backend}/ingest/sensor"

    if args.dry_run:
        print(f"[dry-run] mode {mode} — JSON hanya dicetak.")
    else:
        if use_http:
            import requests
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            if DEVICE_KEY:
                session.headers.update({"X-Device-Key": DEVICE_KEY})
            else:
                print("[!] DEVICE_INGEST_KEY kosong — POST tanpa auth (hanya jalan kalau backend juga tak set key).")
            print(f"[+] HTTP → {ingest_url}")
        if use_serial:
            import serial  # pyserial
            try:
                ser = serial.Serial(args.port, args.baud, timeout=1)
                time.sleep(2)  # kasih modul LoRa waktu init
                print(f"[+] LoRa → serial {args.port} @ {args.baud}")
            except Exception as e:
                print(f"[!] Gagal buka {args.port}: {e}")
                print("    Cek `ls /dev/ttyACM* /dev/ttyUSB*` atau pakai --dry-run / --http.")
                return
        print(f"[+] Mode {mode} dimulai.")

    seq = args.start_seq
    sent = 0
    try:
        while args.count == 0 or sent < args.count:
            data = make_reading(seq)
            line = json.dumps(data)

            if args.dry_run:
                print(f"[{mode} seq={seq}] {line}")
            else:
                # LoRa: tulis JSON MENTAH (tanpa transport) ke modul → RX gateway yang
                # menandai transport=lora. Ini didahulukan agar RF & POST ~bersamaan.
                if use_serial:
                    ser.write((line + "\n").encode("utf-8"))
                    ser.flush()
                    print(f"[LoRa seq={seq}] tx → serial")
                # HTTP: kirim SALINAN dgn transport=http langsung ke server. seq & sentAt
                # sama persis dgn kembaran LoRa-nya → perbandingan per-paket adil.
                if use_http:
                    http_body = dict(data)
                    http_body["transport"] = "http"
                    t0 = time.time()
                    try:
                        r = session.post(ingest_url, json=http_body, timeout=10)
                        dt = (time.time() - t0) * 1000
                        ok = r.status_code in (200, 201, 202)
                        print(f"[HTTP seq={seq}] {'✓' if ok else '✗'} HTTP {r.status_code} ({dt:.0f}ms RTT)")
                    except Exception as e:
                        print(f"[HTTP seq={seq}] ✗ gagal: {e}")

            seq += 1
            sent += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[+] Dihentikan.")
    finally:
        if ser is not None and ser.is_open:
            ser.close()
            print("[+] Serial ditutup.")
    print(f"[+] Selesai. Mode {mode}, total terkirim: {sent}")


if __name__ == "__main__":
    main()
