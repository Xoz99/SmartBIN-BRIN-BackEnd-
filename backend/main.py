import io
import os
import asyncio
import json
import ssl
import queue
import threading
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import numpy as np
from PIL import Image
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
import paho.mqtt.client as mqtt_client
import serial
import serial.tools.list_ports

# Baca file .env (di folder yang sama) kalau ada → config per-node tanpa ubah kode
# maupun ketik env panjang tiap run. Cukup `python3 main.py`. (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ========================================================
# SETUP TFLITE RUNTIME
# ========================================================
try:
    import ai_edge_litert.interpreter as tflite
    _HAS_TF = False
except ImportError:
    try:
        import tflite_runtime.interpreter as tflite
        _HAS_TF = False
    except ImportError:
        import tensorflow.lite as tflite
        _HAS_TF = True

if _HAS_TF:
    from tensorflow.keras.applications.efficientnet import preprocess_input as eff_preprocess
else:
    eff_preprocess = None

# ========================================================
# CONFIG
# ========================================================
MODEL_PATH   = "model_advanced.tflite"
FRONTEND_DIR = "../frontend"
CLASS_NAMES  = ["Anorganik", "B3", "Organik"]

# --- SERIAL PORTS ---
# AUTO-DETEKSI STM32 vs LoRa dari deskripsi/VID USB (biar tidak ketuker saat nomor
# ttyACM* geser tiap reboot — penyebab "perintah aktuator nyasar ke LoRa").
#   STM32   = STMicroelectronics / Virtual COM / VID 0483
#   LoRa32  = CH340 / QinHeng / VID 1a86 (atau CP210x / Silicon Labs)
# Override paksa lewat env STM32_PORT / LORA_PORT kalau deteksi meleset.
STM32_PORT = os.environ.get("STM32_PORT")   # None → auto-detect
LORA_PORT  = os.environ.get("LORA_PORT")    # None → auto-detect
BAUD_RATE  = int(os.environ.get("BAUD_RATE", "115200"))

# --- Backend SmartBIN (MQTT HiveMQ Cloud) — set per-node lewat env var. ---
# NODE_ID WAJIB unik tiap Raspberry Pi/bin (mis. bin-001, bin-002, ...).
# Jalankan misalnya: NODE_ID=bin-005 python3 main.py
MQTT_HOST    = os.environ.get("MQTT_HOST", "4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud")
MQTT_PORT    = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER    = os.environ.get("MQTT_USER", "bintrash")
MQTT_PASS    = os.environ.get("MQTT_PASS", "Smartbinbrin1")
NODE_ID      = os.environ.get("NODE_ID", "bin-003")

# --- Jalur forward yang aktif (bisa dimatiin per-jalur lewat env) ---
# Default: MQTT + LoRa nyala (perilaku lama). Matikan salah satu saat eksperimen
# perbandingan biar tidak dobel-tulis ke backend (mis. FORWARD_MQTT=0).
FORWARD_MQTT = os.environ.get("FORWARD_MQTT", "1") == "1"
FORWARD_LORA = os.environ.get("FORWARD_LORA", "1") == "1"

# Log sensor ringkas: cetak tiap N bacaan (1=tiap bacaan, 5=lebih sepi). Kurangi spam.
LOG_SENSOR_EVERY = int(os.environ.get("LOG_SENSOR_EVERY", "1"))


def _sensor_summary(d: dict) -> str:
    """Ringkasan 1-baris bacaan STM32 (bukan dump JSON kepotong yang bikin rancu).
    'organik/anorganik/b3' di sini = KOMPARTEMEN sensor, BUKAN hasil deteksi kamera."""
    try:
        vols = "/".join(str((d.get(c) or {}).get("volume", "-")) for c in ("organik", "anorganik", "b3"))
        berat = d.get("berat_g", "-")
        batt = (d.get("battery") or {}).get("percent", "-")
        gps = "fix" if (d.get("sat") or 0) > 0 else "-"
        return f"berat={berat}g laci(O/A/B3)={vols}% batt={batt}% gps={gps}"
    except Exception:
        return ""

# --- Jalur perbandingan HTTP (penelitian LoRa vs HTTP) ---
# COMPARE_HTTP=1 → tiap bacaan STM32 JUGA di-POST langsung ke server (transport=http),
# selain lewat LoRa (transport=lora, ditandai gateway RX). seq+sentAt disuntik ke tiap
# bacaan supaya backend bisa hitung packet loss (dari seq) & latency (createdAt−sentAt).
COMPARE_HTTP      = os.environ.get("COMPARE_HTTP", "0") == "1"
BACKEND_HTTP_URL  = os.environ.get("BACKEND_HTTP_URL", "http://192.168.1.23:3000").rstrip("/")
DEVICE_INGEST_KEY = os.environ.get("DEVICE_INGEST_KEY", "")
INGEST_URL        = f"{BACKEND_HTTP_URL}/ingest/sensor"

# Topik harus SAMA PERSIS dengan yang di-subscribe backend (src/mqtt/topics.js):
#   smartbin/+/sensor, smartbin/+/status, smartbin/+/classification
TOPIC_SENSOR         = f"smartbin/{NODE_ID}/sensor"
TOPIC_STATUS         = f"smartbin/{NODE_ID}/status"
TOPIC_CMD            = f"smartbin/{NODE_ID}/cmd"
TOPIC_CLASSIFICATION = f"smartbin/{NODE_ID}/classification"  # <- lapor hasil pemilahan ke backend

# ========================================================
# GLOBAL STATE
# ========================================================
arduino        = None   # koneksi serial ke STM32
lora_tx        = None   # koneksi serial ke LoRa
latest_sensor  = {}
sensor_lock    = threading.Lock()
mqtt_connected = False
_mqtt_client   = None
_is_running    = False
_dispatcher_thread = None
_seq_counter   = 0      # nomor urut paket (buat deteksi packet loss di backend)
_http_q        = None   # antrean POST jalur HTTP (queue.Queue) — non-blocking
_http_session  = None   # requests.Session buat jalur HTTP

# ========================================================
# INIT SERIAL — SEKALI SAJA, DI SATU TEMPAT
# ========================================================
def _detect_ports():
    """Deteksi port STM32 & LoRa dari deskripsi/VID USB. Balikin (stm32, lora).
    STM32 = STMicroelectronics/Virtual COM/VID 0483; LoRa = CH340/QinHeng/VID 1a86."""
    stm = lora = None
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer or ''} {getattr(p, 'product', '') or ''} {p.hwid or ''}".lower()
        if any(k in blob for k in ("stmicro", "stm32", "virtual com", "0483")):
            stm = stm or p.device
        elif any(k in blob for k in ("ch340", "ch910", "qinheng", "1a86", "wch", "cp210", "silicon labs")):
            lora = lora or p.device
    return stm, lora


def init_all_serial():
    """
    Buka koneksi serial STM32 dan LoRa satu kali di awal startup.
    Port ditentukan berjenjang: env (STM32_PORT/LORA_PORT) > auto-deteksi USB >
    default ttyACM. Auto-deteksi mencegah port ketuker saat ttyACM* geser.
    """
    global arduino, lora_tx

    auto_stm, auto_lora = _detect_ports()
    stm_port  = STM32_PORT or auto_stm or "/dev/ttyACM1"
    lora_port = LORA_PORT  or auto_lora or "/dev/ttyACM0"
    print(f"[Serial] Deteksi USB → STM32={auto_stm or '-'} LoRa={auto_lora or '-'} | "
          f"dipakai: STM32={stm_port}, LoRa={lora_port}")

    if stm_port == lora_port:
        print(f"[!] PERINGATAN: STM32 & LoRa sama-sama '{stm_port}'. Set env "
              f"STM32_PORT/LORA_PORT manual. LoRa dilewati, STM32 tetap dicoba.")

    try:
        arduino = serial.Serial(stm_port, BAUD_RATE, timeout=1)
        time.sleep(2)
        print(f"[+] STM32 terhubung di {stm_port} @ {BAUD_RATE}")
    except Exception as e:
        arduino = None
        print(f"[!] Gagal buka STM32 di {stm_port}: {e}")

    if lora_port != stm_port:
        try:
            lora_tx = serial.Serial(lora_port, BAUD_RATE, timeout=1)
            time.sleep(2)
            print(f"[+] LoRa terhubung di {lora_port} @ {BAUD_RATE}")
        except Exception as e:
            lora_tx = None
            print(f"[!] Gagal buka LoRa di {lora_port}: {e}")


def close_all_serial():
    global arduino, lora_tx
    if arduino is not None and arduino.is_open:
        arduino.close()
        print("[+] Serial STM32 ditutup.")
    if lora_tx is not None and lora_tx.is_open:
        lora_tx.close()
        print("[+] Serial LoRa ditutup.")

# ========================================================
# SATU DISPATCHER THREAD — baca STM32, forward ke MQTT + LoRa
# ========================================================
def _serial_dispatcher_loop():
    global latest_sensor, _seq_counter
    print("[Dispatcher] Thread dimulai.")
    while _is_running:
        if arduino is None or not arduino.is_open:
            time.sleep(1)
            continue
        try:
            if arduino.in_waiting > 0:
                raw_line = arduino.readline().decode("utf-8", errors="ignore").strip()
                if not raw_line:
                    continue

                # STM32 kadang kirim JSON dengan prefix teks (mis. "[->Raspi] {...}")
                # dan/atau ada garbage bytes nyempil. Ambil substring dari '{' pertama
                # sampai '}' terakhir, baru diparse — bukan cek startswith/endswith ketat.
                start_idx = raw_line.find("{")
                end_idx   = raw_line.rfind("}")
                if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
                    continue  # baris log biasa (mis. "[Jarak] ...", "[GPS] ..."), skip

                line = raw_line[start_idx:end_idx + 1]
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[Dispatcher] JSON invalid: {e} | raw: {raw_line[:100]}")
                    continue

                # Metadata penelitian: seq (nomor urut → packet loss) + sentAt (jam
                # device → latency). STM32 EcoSort SUDAH sertakan seq/sentAt sendiri →
                # HORMATI punya device (jangan ditimpa). Hanya suntik kalau device
                # belum kirim (mis. firmware lama). Nilai sama untuk satu bacaan dipakai
                # jalur LoRa & HTTP → perbandingan per-paket adil.
                if "seq" not in data:
                    _seq_counter += 1
                    data["seq"] = _seq_counter
                data.setdefault("sentAt", datetime.now(timezone.utc).isoformat())
                data.setdefault("nodeId", NODE_ID)
                # JSON yang diteruskan (tanpa field internal _*).
                fwd = json.dumps({k: v for k, v in data.items() if not str(k).startswith("_")})

                # 1. Update state in-memory (buat /sensor/latest)
                with sensor_lock:
                    latest_sensor = {**data, "_topic": TOPIC_SENSOR, "_timestamp": time.time()}

                # 2. Forward ke MQTT
                if FORWARD_MQTT and _mqtt_client is not None and mqtt_connected:
                    _mqtt_client.publish(TOPIC_SENSOR, fwd)

                # 3. Forward ke LoRa (→ board RX → gateway tandai transport=lora)
                if FORWARD_LORA and lora_tx is not None and lora_tx.is_open:
                    try:
                        lora_tx.write((fwd + "\n").encode("utf-8"))
                    except Exception as e:
                        print(f"[Dispatcher] Gagal kirim ke LoRa: {e}")

                # 4. Forward ke HTTP langsung ke server (transport=http) — non-blocking.
                if COMPARE_HTTP and _http_q is not None:
                    body = {k: v for k, v in data.items() if not str(k).startswith("_")}
                    body["transport"] = "http"
                    try:
                        _http_q.put_nowait(body)
                    except queue.Full:
                        pass  # backend lama down → buang, jangan sumbat baca STM32

                if LOG_SENSOR_EVERY > 0 and _seq_counter % LOG_SENSOR_EVERY == 0:
                    print(f"[STM32] seq={_seq_counter} | {_sensor_summary(data)}")
        except Exception as e:
            print(f"[Dispatcher] Error baca STM32: {e}")
            time.sleep(1)
        time.sleep(0.01)
    print("[Dispatcher] Thread berhenti.")


def start_dispatcher():
    global _dispatcher_thread
    _dispatcher_thread = threading.Thread(target=_serial_dispatcher_loop, daemon=True)
    _dispatcher_thread.start()

# ========================================================
# JALUR HTTP PERBANDINGAN (opsional — penelitian LoRa vs HTTP)
# ========================================================
def _http_compare_worker():
    """POST tiap bacaan ke server (transport=http) di thread sendiri, biar loop
    baca STM32 tidak pernah berhenti nunggu jaringan."""
    while _is_running:
        try:
            body = _http_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            r = _http_session.post(INGEST_URL, json=body, timeout=10)
            if r.status_code not in (200, 201, 202):
                print(f"[CompareHTTP] seq={body.get('seq')} → HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"[CompareHTTP] seq={body.get('seq')} gagal: {e}")
        finally:
            _http_q.task_done()


def init_http_compare():
    """Siapkan session + antrean + worker untuk jalur HTTP. Hanya kalau COMPARE_HTTP=1."""
    global _http_q, _http_session
    if not COMPARE_HTTP:
        return
    try:
        import requests
    except ImportError:
        print("[CompareHTTP] modul 'requests' belum ada — jalur HTTP dimatikan. (pip install requests)")
        return
    _http_session = requests.Session()
    _http_session.headers.update({"Content-Type": "application/json"})
    if DEVICE_INGEST_KEY:
        _http_session.headers.update({"X-Device-Key": DEVICE_INGEST_KEY})
    else:
        print("[CompareHTTP] DEVICE_INGEST_KEY kosong — POST tanpa auth (hanya jalan kalau server juga tak set key).")
    _http_q = queue.Queue(maxsize=2000)
    threading.Thread(target=_http_compare_worker, daemon=True).start()
    print(f"[CompareHTTP] aktif → {INGEST_URL}")

# ========================================================
# MQTT
# ========================================================
def _on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        client.subscribe(TOPIC_STATUS)  # sensor gak perlu di-subscribe balik, kita yg publish
        print(f"[MQTT] Connected!")
        client.publish(TOPIC_STATUS, json.dumps({"status": "online", "via": "fastapi"}), retain=True)
    else:
        mqtt_connected = False
        print(f"[MQTT] Gagal connect rc={rc}")

def _on_disconnect(client, userdata, rc, properties=None, *args):
    global mqtt_connected
    mqtt_connected = False
    print(f"[MQTT] Disconnect rc={rc}, reconnect otomatis...")

def init_mqtt():
    global _mqtt_client
    try:
        client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=f"fastapi-ecosort-{NODE_ID}")
    except AttributeError:
        client = mqtt_client.Client(client_id=f"fastapi-ecosort-{NODE_ID}")

    client.username_pw_set(MQTT_USER, MQTT_PASS)
    # Verifikasi TLS STANDAR OS (perbaikan tim IoT): HiveMQ Cloud kadang nolak
    # koneksi insecure (cert_reqs=CERT_NONE) → MQTT putus-nyambung / "sebagian jalan".
    # Butuh CA certs OS (Pi: `sudo apt install ca-certificates`).
    client.tls_set()
    client.on_connect    = _on_connect
    client.on_disconnect = _on_disconnect

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        _mqtt_client = client
        print(f"[MQTT] Connecting ke {MQTT_HOST}:{MQTT_PORT}...")
    except Exception as e:
        print(f"[MQTT] Gagal init: {e}")

def publish_cmd(cmd: str) -> bool:
    if _mqtt_client is None or not mqtt_connected:
        print("[MQTT] Tidak terkoneksi, cmd dilewati.")
        return False
    try:
        _mqtt_client.publish(TOPIC_CMD, cmd)
        print(f"[MQTT] Publish cmd → {TOPIC_CMD}: {cmd}")
        return True
    except Exception as e:
        print(f"[MQTT] Gagal publish: {e}")
        return False

# ========================================================
# ARDUINO CMD
# ========================================================
ARDUINO_CMD = {"Organik": "organik", "Anorganik": "anorganik", "B3": "B3"}

def kirim_ke_stm32(kategori: str) -> dict:
    cmd = ARDUINO_CMD.get(kategori)
    if not cmd:
        return {"ok": False, "channel": None, "reason": f"Kategori '{kategori}' tidak dikenal"}

    if arduino is not None and arduino.is_open:
        try:
            arduino.write((cmd + "\n").encode("utf-8"))
            arduino.flush()
            print(f"[Serial] Kirim ke STM32: {cmd}")
            return {"ok": True, "channel": "serial", "cmd": cmd}
        except Exception as e:
            print(f"[Serial] Gagal: {e}")

    if publish_cmd(cmd):
        return {"ok": True, "channel": "mqtt", "cmd": cmd}

    return {"ok": False, "channel": None, "reason": "Serial & MQTT keduanya tidak aktif"}

# Label model (Kapital) → enum backend SmartBIN (lowercase; "B3" → "b3").
BACKEND_LABEL_MAP = {"Organik": "organik", "Anorganik": "anorganik", "B3": "b3"}

def report_classification(kategori: str, confidence: float) -> bool:
    """Lapor hasil pemilahan ke backend SmartBIN lewat MQTT.

    Topik : smartbin/{NODE_ID}/classification
    Payload: {"label": <organik|anorganik|b3>, "confidence": <0..1>}
    Backend (src/mqtt/handlers/classificationData.js) menyimpan ke DB,
    memicu analitik + pairing berat/deposit, lalu broadcast ke frontend.
    """
    label = BACKEND_LABEL_MAP.get(kategori, "unknown")
    if _mqtt_client is None or not mqtt_connected:
        print("[MQTT] Tidak terkoneksi — hasil klasifikasi TIDAK dilaporkan ke backend.")
        return False
    payload = json.dumps({"label": label, "confidence": float(confidence)})
    try:
        _mqtt_client.publish(TOPIC_CLASSIFICATION, payload, qos=1)
        print(f"[MQTT] Lapor klasifikasi → {TOPIC_CLASSIFICATION}: {payload}")
        return True
    except Exception as e:
        print(f"[MQTT] Gagal lapor klasifikasi: {e}")
        return False

# ========================================================
# LOAD MODEL TFLITE
# ========================================================
interpreter    = None
input_details  = None
output_details = None
IN_H = IN_W   = 224
INPUT_DTYPE   = np.float32
_infer_lock    = threading.Lock()  # tflite tidak aman dipanggil paralel (kamera vs /predict/)

print(f"[+] Loading model TFLite dari: {MODEL_PATH}")
try:
    interpreter = tflite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
    input_details  = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    shape       = input_details[0]['shape']
    IN_H, IN_W  = int(shape[1]), int(shape[2])
    INPUT_DTYPE = input_details[0]['dtype']
    print(f"[+] Model loaded! Input: {IN_W}x{IN_H}, dtype={INPUT_DTYPE.__name__}")
except Exception as e:
    interpreter = None
    print(f"[!] Error loading model: {e}")

def _preprocess(image: Image.Image) -> np.ndarray:
    image = image.convert("RGB").resize((IN_W, IN_H))
    arr   = np.array(image, dtype=np.float32)
    if INPUT_DTYPE == np.float32:
        if eff_preprocess is not None:
            # Desktop (TensorFlow penuh): fungsi resmi — untuk EfficientNet ini
            # pass-through (tidak mengubah nilai piksel).
            arr = eff_preprocess(arr)
        else:
            # Raspi (tflite-runtime tanpa TF): EfficientNet = pass-through, piksel
            # mentah 0-255. JANGAN normalisasi mean/std ImageNet (bikin prediksi ngaco).
            pass
        arr = np.expand_dims(arr, axis=0).astype(np.float32)
    else:
        arr = np.expand_dims(arr, axis=0).astype(INPUT_DTYPE)
    return arr

def _predict(image: Image.Image):
    inp = _preprocess(image)
    with _infer_lock:
        interpreter.set_tensor(input_details[0]['index'], inp)
        interpreter.invoke()
        preds = interpreter.get_tensor(output_details[0]['index'])[0].copy()
    if output_details[0]['dtype'] != np.float32:
        scale, zero = output_details[0]['quantization']
        if scale:
            preds = (preds.astype(np.float32) - zero) * scale
    preds = preds.astype(np.float32)
    idx   = int(np.argmax(preds))
    return CLASS_NAMES[idx], float(preds[idx])

# ========================================================
# KAMERA REALTIME DI RASPI (motion-gate + auto klasifikasi)
# ========================================================
# Saat main.py jalan, kamera Raspi langsung dibuka (AUTO_START_CAM=1 default).
# TANPA buka web: begitu ada objek masuk (gerakan terdeteksi) → tunggu diam →
# jepret → klasifikasi → aktuasi STM32 → lapor ke backend SmartBIN.
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    _HAS_CV2 = False
    print("[!] OpenCV (cv2) belum terpasang. Kamera realtime nonaktif. -> pip install opencv-python")

# Semua bisa di-override lewat env var (tanpa ubah kode)
CAMERA_INDEX     = int(os.environ.get("CAMERA_INDEX", "0"))           # 0 = kamera default Raspi
MOTION_THRESHOLD = int(os.environ.get("MOTION_THRESHOLD", "1500000")) # total piksel berubah utk dianggap "ada objek"
SETTLE_DELAY     = float(os.environ.get("SETTLE_DELAY", "0.6"))       # detik tunggu objek diam sebelum jepret
CONF_THRESHOLD   = float(os.environ.get("CONF_THRESHOLD", "0"))       # 0 = TANPA batas: tiap objek langsung diproses apapun confidence-nya
COOLDOWN_SEC     = float(os.environ.get("COOLDOWN_SEC", "3.0"))       # jeda saat TAK ada aktuasi (conf rendah / jepret gagal)
# Debug: simpan frame yang PERSIS diklasifikasi ke file, biar keliatan "AI liat apa"
# (bandingin blur/pencahayaan/background vs shot bersih di browser). SAVE_SHOT=0 matiin.
SAVE_SHOT = os.environ.get("SAVE_SHOT", "1") == "1"
SHOT_PATH = os.environ.get("SHOT_PATH", "last_shot.jpg")

# Mode KUMPULIN DATASET (buat retrain model): tiap objek yang di-capture disimpan ke
# dataset/<DATASET_LABEL>/<timestamp>.jpg. Set DATASET_LABEL = jenis yang lagi ditaruh
# (organik/anorganik/b3) biar langsung ter-label benar. Ganti label → restart.
#   DATASET_CAPTURE=1 DATASET_LABEL=anorganik python3 main.py
DATASET_CAPTURE = os.environ.get("DATASET_CAPTURE", "0") == "1"
DATASET_DIR     = os.environ.get("DATASET_DIR", "dataset")
DATASET_LABEL   = os.environ.get("DATASET_LABEL", "unsorted")

# Deteksi objek DI ATAS platform (buletan merah): fokus ROI tengah + bandingkan dgn
# kondisi KOSONG (baseline). Cuma analisis kalau ada objek nutupin platform & sudah
# diam, SEKALI per objek. Nilai = rata-rata beda piksel (0-255). Sesuaikan via env.
CAMERA_WARMUP_SEC = float(os.environ.get("CAMERA_WARMUP_SEC", "2.0"))  # tunggu kamera settle (auto-exposure) sebelum ambil baseline → cegah false-trigger di awal
IDLE_LOG_SEC = float(os.environ.get("IDLE_LOG_SEC", "15"))  # interval log "menunggu objek" saat platform kosong (biar keliatan hidup, tak spam)
ROI_FRAC    = float(os.environ.get("ROI_FRAC", "0.6"))    # fraksi tengah frame (area buletan) yg dipantau+diklasifikasi
OBJECT_DIFF = float(os.environ.get("OBJECT_DIFF", "18"))  # beda dari kosong utk dianggap ADA objek (naikin kalau sering false)
CLEAR_DIFF  = float(os.environ.get("CLEAR_DIFF", "9"))    # beda di bawah ini = platform kosong lagi → re-arm
STILL_MOVE  = float(os.environ.get("STILL_MOVE", "4"))    # gerak antar-frame di bawah ini = objek sudah diam
STILL_NEED  = int(os.environ.get("STILL_NEED", "3"))      # butuh N frame diam berturut sebelum jepret
REARM_BUFFER = float(os.environ.get("REARM_BUFFER", "1.5"))  # jeda ekstra setelah aktuator selesai sebelum siap objek baru


def _center_roi(frame, frac):
    """Crop kotak tengah frame (area platform/buletan). frac=0.6 → 60% tengah."""
    h, w = frame.shape[:2]
    s = int(min(h, w) * max(0.1, min(1.0, frac)))
    cy, cx = h // 2, w // 2
    y0 = max(0, cy - s // 2)
    x0 = max(0, cx - s // 2)
    return frame[y0:y0 + s, x0:x0 + s]

# Mode OBJEK (motion-gate 1x): pas objek masuk → jepret+analisis SEKALI, lalu tunggu
# objek diangkat (scene sepi >= REARM_CLEAR_SEC) baru siap objek berikutnya. Cegah
# analisis berulang objek yang sama & spam confidence-rendah.
REARM_CLEAR_SEC  = float(os.environ.get("REARM_CLEAR_SEC", "2.0"))
AUTO_START_CAM   = os.environ.get("AUTO_START_CAM", "1") == "1"       # default ON di Raspi (set 0 utk matikan)

# Monitor kamera di dashboard: push frame TERAKHIR ke backend tiap CAMERA_PUSH_SEC
# (Pi konek KELUAR ke server publik — tak perlu buka port Pi). Backend simpan frame
# terakhir per bin; FE ambil via <img> /camera/{nodeId}/latest.jpg.
CAMERA_PUSH     = os.environ.get("CAMERA_PUSH", "1") == "1"
CAMERA_PUSH_SEC = float(os.environ.get("CAMERA_PUSH_SEC", "1.5"))
CAMERA_JPEG_Q   = int(os.environ.get("CAMERA_JPEG_Q", "70"))

# Durasi aktuator per kategori (detik). Setelah aktuasi, kamera DIKUNCI selama ini
# supaya tidak jepret ulang saat mekanik (stepper+tilt+servo+auto-reset) MASIH GERAK
# — mencegah deteksi/aktuasi dobel & frame ngaco. STM32 TIDAK diubah; timing di sisi
# Raspi. Ukur ulang sekali dgn stopwatch di hardware asli, tambah ~1s buffer, lalu
# override per-kategori lewat env: ACTUATOR_SEC_ORGANIK / _ANORGANIK / _B3.
ACTUATOR_TIMES = {
    "organik":   float(os.environ.get("ACTUATOR_SEC_ORGANIK",   "7.0")),
    "anorganik": float(os.environ.get("ACTUATOR_SEC_ANORGANIK", "8.0")),
    "b3":        float(os.environ.get("ACTUATOR_SEC_B3",         "9.5")),
}
ACTUATOR_DEFAULT_SEC = float(os.environ.get("ACTUATOR_SEC_DEFAULT", "10.0"))

def _actuator_lock_sec(kategori: str) -> float:
    """Estimasi durasi aktuator utk kategori (case-insensitive: 'Organik'→'organik')."""
    return ACTUATOR_TIMES.get((kategori or "").lower().strip(), ACTUATOR_DEFAULT_SEC)


class CameraWorker:
    """Loop kamera di thread terpisah supaya endpoint HTTP tetap responsif."""

    def __init__(self):
        self.thread  = None
        self.running = False
        self.cap     = None
        self.last    = {"kategori": None, "confidence": None, "ts": None}
        self.last_raw = None   # frame BGR terakhir (buat push monitor ke dashboard)
        self.error   = None

    def start(self):
        if not _HAS_CV2:
            self.error = "OpenCV belum terpasang (pip install opencv-python)"
            print(f"[CAM] {self.error}")
            return False
        if interpreter is None:
            self.error = "Model klasifikasi tidak ter-load"
            print(f"[CAM] {self.error}")
            return False
        if self.running:
            return True
        self.error   = None
        self.running = True
        self.thread  = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        if self.cap:
            self.cap.release()
            self.cap = None

    def _loop(self):
        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        if not self.cap or not self.cap.isOpened():
            self.error = f"Kamera index {CAMERA_INDEX} gagal dibuka"
            print(f"[CAM] {self.error}")
            self.running = False
            return

        print(f"[CAM] Kamera AKTIF (index {CAMERA_INDEX}) — mode PLATFORM: analisis objek di ROI tengah (buletan), 1x per objek.")
        print("[CAM] (pastikan platform/buletan KOSONG saat start — dipakai sbg baseline)")
        baseline = None       # ROI abu-abu saat platform KOSONG
        prev_roi = None       # ROI frame sebelumnya (deteksi gerak)
        armed = True
        still = 0
        rearm_at = 0.0        # waktu boleh re-arm (setelah aktuator selesai)
        warmup_until = time.time() + CAMERA_WARMUP_SEC  # settle auto-exposure dulu
        n_obj = 0             # nomor urut objek yang sudah diproses
        detecting = False     # objek lagi diamati (buat log "objek masuk" sekali)
        idle_log_at = 0.0     # waktu berikutnya log "menunggu objek"
        warmed = False        # sudah lewat warmup (buat log sekali)

        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            self.last_raw = frame  # frame penuh buat feed monitor

            # Warmup: kamera settle dulu sebelum ambil baseline & mulai deteksi,
            # supaya frame gelap/nyetel di awal tak dikira "objek" → gerak sendiri.
            if time.time() < warmup_until:
                if not warmed:
                    print(f"[CAM] ⏳ Warmup {CAMERA_WARMUP_SEC:.0f}s — kamera menyala & settle (pastikan buletan KOSONG)...")
                    warmed = True
                time.sleep(0.03)
                continue

            roi = _center_roi(frame, ROI_FRAC)
            gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (21, 21), 0)

            if baseline is None:
                baseline = gray       # anggap platform kosong saat start
                prev_roi = gray
                idle_log_at = time.time() + IDLE_LOG_SEC
                print("[CAM] ✅ Baseline platform KOSONG diambil — SIAP, menunggu objek di buletan...")
                time.sleep(0.03)
                continue

            obj_diff = float(cv2.absdiff(gray, baseline).mean())   # beda dari kondisi kosong
            move     = float(cv2.absdiff(gray, prev_roi).mean())    # gerak antar-frame
            prev_roi = gray

            if armed:
                # Objek nutupin platform (beda dari kosong) DAN sudah diam beberapa frame.
                if obj_diff > OBJECT_DIFF and move < STILL_MOVE:
                    if not detecting:
                        detecting = True
                        print(f"[CAM] 👀 Objek MASUK buletan (obj_diff={obj_diff:.0f} > {OBJECT_DIFF:.0f}) — tunggu diam...")
                    still += 1
                    if still >= STILL_NEED:
                        detecting = False
                        shot = frame
                        for _ in range(2):
                            ok2, f2 = self.cap.read()   # ambil frame segar
                            if ok2:
                                shot = f2
                        self.last_raw = shot
                        if SAVE_SHOT:
                            try:
                                cv2.imwrite(SHOT_PATH, shot)  # frame penuh yang diklasifikasi
                            except Exception:
                                pass
                        if DATASET_CAPTURE:
                            try:
                                ddir = os.path.join(DATASET_DIR, DATASET_LABEL)
                                os.makedirs(ddir, exist_ok=True)
                                fn = os.path.join(ddir, f"{int(time.time() * 1000)}.jpg")
                                cv2.imwrite(fn, shot)
                                print(f"[Dataset] +1 ({DATASET_LABEL}) → {fn}")
                            except Exception as e:
                                print(f"[Dataset] gagal simpan: {e}")
                        # Klasifikasi FRAME PENUH (bukan crop ROI) — model lebih akurat
                        # dgn framing penuh, sama seperti sim browser. ROI cuma dipakai
                        # untuk DETEKSI kapan ada objek di platform, bukan input model.
                        img = Image.fromarray(cv2.cvtColor(shot, cv2.COLOR_BGR2RGB))
                        try:
                            kategori, conf = _predict(img)
                        except Exception as e:
                            print(f"[CAM] gagal klasifikasi: {e}")
                            kategori, conf = None, 0.0

                        n_obj += 1
                        if kategori and conf >= CONF_THRESHOLD:
                            print(f"[CAM] #{n_obj} ✓ {kategori} ({conf:.0%}) → aktuasi STM32 + lapor backend")
                            kirim_ke_stm32(kategori)
                            report_classification(kategori, conf)
                            self.last = {"kategori": kategori, "confidence": conf, "ts": time.time()}
                        else:
                            print(f"[CAM] #{n_obj} objek di platform, confidence rendah ({conf:.0%}) — dilewati")

                        armed = False    # tunggu aktuator selesai jatuhin objek
                        still = 0
                        # Re-arm setelah aktuator kelar (objek jatuh & platform reset).
                        wait_s = _actuator_lock_sec(kategori) + REARM_BUFFER
                        rearm_at = time.time() + wait_s
                        print(f"[CAM] ⏳ tunggu aktuator ~{wait_s:.1f}s (objek jatuh + mekanik reset)...")
                else:
                    if detecting:
                        # objek keburu pindah/goyang sebelum diam → batal, tunggu lagi
                        detecting = False
                        print(f"[CAM] objek belum diam / pindah (obj_diff={obj_diff:.0f}) — tunggu lagi...")
                    still = 0
                    # Heartbeat: platform kosong & siap. Log tiap IDLE_LOG_SEC biar
                    # keliatan sistem hidup tanpa spam tiap frame.
                    if time.time() >= idle_log_at:
                        print(f"[CAM] … menunggu objek (platform kosong, obj_diff={obj_diff:.0f})")
                        idle_log_at = time.time() + IDLE_LOG_SEC
            else:
                # Re-arm berbasis WAKTU: setelah aktuator selesai, anggap objek sudah
                # jatuh & platform reset → siap objek baru + perbarui baseline ke kondisi
                # platform SEKARANG (kosong, walau posisi sedikit bergeser). Lebih robust
                # daripada nunggu tampilan balik PERSIS ke baseline lama.
                if time.time() >= rearm_at:
                    armed = True
                    baseline = gray
                    idle_log_at = time.time() + IDLE_LOG_SEC
                    print(f"[CAM] ✅ Platform kosong lagi — SIAP objek berikutnya (#{n_obj + 1}).")

            time.sleep(0.03)  # ~30fps buat feed, hemat CPU

        if self.cap:
            self.cap.release()
            self.cap = None
        print("[CAM] Kamera realtime berhenti.")


camera_worker = CameraWorker()


def _camera_push_loop():
    """Push frame kamera TERAKHIR ke backend tiap CAMERA_PUSH_SEC untuk MONITOR
    di dashboard. Encode JPEG di sini (bukan tiap frame) supaya hemat CPU. Pi
    konek KELUAR ke server (BACKEND_HTTP_URL) — tak perlu buka port di Pi."""
    try:
        import requests
    except ImportError:
        print("[CamPush] modul 'requests' belum ada — monitor kamera nonaktif.")
        return
    url = f"{BACKEND_HTTP_URL}/camera/frame"
    while _is_running:
        time.sleep(CAMERA_PUSH_SEC)
        if not (CAMERA_PUSH and camera_worker.running):
            continue
        frame = camera_worker.last_raw
        if frame is None or not _HAS_CV2:
            continue
        try:
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_Q])
            if not ok:
                continue
            requests.post(url, data=jpg.tobytes(), timeout=5, headers={
                "Content-Type": "image/jpeg",
                "X-Node-Id": NODE_ID,
                "X-Device-Key": DEVICE_INGEST_KEY,
            })
        except Exception as e:
            print(f"[CamPush] gagal kirim frame: {e}")

# ========================================================
# GEMINI
# ========================================================
STATIC_TIPS = {
    "Anorganik": "• Pisahkan dari organik.\n• Jangan dibakar.\n• Bawa ke bank sampah.\n• Cuci bersih kemasan.",
    "B3": "• JANGAN buang ke tempat biasa.\n• Kumpulkan di drop-box B3.\n• Baterai dan lampu termasuk B3.",
    "Organik": "• Bisa dijadikan kompos.\n• Jangan campur plastik.\n• Cocok untuk eco-enzyme.",
}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
gemini_client  = None
tips_cache: dict = {}

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("[+] Gemini siap!")
else:
    print("[!] GEMINI_API_KEY tidak ditemukan. (Tips akan menggunakan mode statis)")

# ========================================================
# FASTAPI LIFESPAN
# ========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_running
    _is_running = True

    init_all_serial()   # <-- SATU-SATUNYA tempat buka serial STM32 & LoRa
    init_mqtt()
    init_http_compare() # <-- jalur HTTP perbandingan (kalau COMPARE_HTTP=1)
    start_dispatcher()  # <-- SATU-SATUNYA thread pembaca serial

    if AUTO_START_CAM:
        if camera_worker.start():
            print("[CAM] Auto-start kamera Raspi aktif (AUTO_START_CAM=1) — milah otomatis tanpa web.")
        else:
            print(f"[CAM] Auto-start kamera gagal: {camera_worker.error}")

    if CAMERA_PUSH:
        threading.Thread(target=_camera_push_loop, daemon=True).start()
        print(f"[CAM] Push frame monitor aktif → {BACKEND_HTTP_URL}/camera/frame tiap {CAMERA_PUSH_SEC}s")

    print("[+] Startup selesai: STM32, LoRa, MQTT, Dispatcher semua aktif.")
    yield

    _is_running = False
    camera_worker.stop()
    if _dispatcher_thread is not None:
        _dispatcher_thread.join(timeout=2)
    close_all_serial()
    if _mqtt_client is not None:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()
        print("[+] MQTT ditutup.")

app = FastAPI(title="EcoSort AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================================================
# PYDANTIC MODELS
# ========================================================
class TipsReq(BaseModel):
    kategori: str

class AskReq(BaseModel):
    pertanyaan: str
    kategori: str = ""

class ArduinoReq(BaseModel):
    perintah: str

class MqttCmdReq(BaseModel):
    cmd: str

# ========================================================
# ROUTES
# ========================================================
@app.get("/status")
def status():
    with sensor_lock:
        sensor_ok = bool(latest_sensor)
    return {
        "status": "online", "model": os.path.basename(MODEL_PATH),
        "input_size": f"{IN_W}x{IN_H}", "classes": CLASS_NAMES,
        "gemini": "ready" if gemini_client else "unavailable",
        "serial_stm32": "connected" if (arduino and arduino.is_open) else "disconnected",
        "serial_lora": "connected" if (lora_tx and lora_tx.is_open) else "disconnected",
        "mqtt": "connected" if mqtt_connected else "disconnected",
        "sensor_data": "ada" if sensor_ok else "belum ada",
        "camera": "running" if camera_worker.running else "stopped",
        "forward": {
            "mqtt": FORWARD_MQTT,
            "lora": FORWARD_LORA,
            "http_compare": COMPARE_HTTP and (_http_q is not None),
        },
        "last_seq": _seq_counter,
    }

# ================= KONTROL KAMERA REALTIME =================
@app.post("/camera/start")
def camera_start():
    ok = camera_worker.start()
    return {"status": "success" if ok else "error", "running": camera_worker.running, "error": camera_worker.error}

@app.post("/camera/stop")
def camera_stop():
    camera_worker.stop()
    return {"status": "success", "running": camera_worker.running}

@app.get("/camera/status")
def camera_status():
    return {
        "running": camera_worker.running,
        "has_opencv": _HAS_CV2,
        "camera_index": CAMERA_INDEX,
        "conf_threshold": CONF_THRESHOLD,
        "motion_threshold": MOTION_THRESHOLD,
        "cooldown_sec": COOLDOWN_SEC,           # jeda saat tak ada aktuasi
        "actuator_lock_sec": ACTUATOR_TIMES,    # lock kamera per-kategori saat aktuasi
        "last_detection": camera_worker.last,
        "error": camera_worker.error,
    }

@app.get("/")
def home():
    if os.path.exists(os.path.join(FRONTEND_DIR, "index.html")):
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
    return {"message": "Frontend index.html tidak ditemukan!"}

@app.post("/predict/")
async def predict(file: UploadFile = File(...)):
    if interpreter is None:
        return {"error": "Model tidak ter-load"}
    try:
        image_bytes = await file.read()
        image       = Image.open(io.BytesIO(image_bytes))
        kategori, confidence = await asyncio.to_thread(_predict, image)
        print(f"[+] Deteksi: {kategori} ({confidence:.1%})")
        aktuator = kirim_ke_stm32(kategori)
        report_classification(kategori, confidence)   # <- lapor ke backend SmartBIN (analitik/deposit)
        return {"status": "success", "hasil": [{"kategori": kategori, "confidence": confidence}], "aktuator": aktuator}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/arduino/")
async def arduino_manual(req: ArduinoReq):
    cmd = req.perintah.strip()
    if cmd not in {"organik", "anorganik", "B3", "reset"}:
        return {"status": "error", "message": "Perintah tidak valid"}
    hasil = kirim_ke_stm32(cmd) if cmd in ARDUINO_CMD.values() else {"ok": publish_cmd(cmd), "channel": "mqtt", "cmd": cmd}
    return {"status": "success" if hasil["ok"] else "error", **hasil}

@app.get("/sensor/latest")
def sensor_latest():
    with sensor_lock:
        if not latest_sensor:
            return {"status": "kosong", "message": "Belum ada data dari STM32"}
        return {"status": "ok", "data": latest_sensor}

@app.get("/sensor/battery")
def sensor_battery():
    with sensor_lock:
        batt = latest_sensor.get("battery")
    if not batt:
        return {"status": "kosong", "message": "Belum ada data baterai"}
    return {"status": "ok", "battery": batt}

@app.post("/mqtt/cmd/")
async def mqtt_cmd(req: MqttCmdReq):
    cmd = req.cmd.strip()
    if cmd not in {"organik", "anorganik", "B3", "reset"}:
        return {"status": "error", "message": f"Cmd tidak valid: {cmd}"}
    ok = publish_cmd(cmd)
    return {"status": "success" if ok else "error", "cmd": cmd, "mqtt_connected": mqtt_connected}

@app.get("/mqtt/status")
def mqtt_status():
    return {"connected": mqtt_connected, "broker": MQTT_HOST, "topic_sub": TOPIC_SENSOR, "topic_cmd": TOPIC_CMD}

@app.post("/genai/tips/")
async def genai_tips(req: TipsReq):
    if gemini_client is None:
        return {"status": "success", "tips": STATIC_TIPS.get(req.kategori, ""), "source": "static"}
    if req.kategori in tips_cache:
        return {"status": "success", "tips": tips_cache[req.kategori], "cached": True}
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Tips singkat sampah {req.kategori} dalam 3 bullet poin.",
        )
        tips_cache[req.kategori] = resp.text
        return {"status": "success", "tips": resp.text, "cached": False, "source": "gemini"}
    except Exception:
        return {"status": "success", "tips": STATIC_TIPS.get(req.kategori, ""), "source": "static"}

@app.post("/genai/ask/")
async def genai_ask(req: AskReq):
    if gemini_client is None:
        return {"status": "success", "jawaban": "Maaf, AI sedang tidak tersedia."}
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Jawab singkat maksimal 3 kalimat: {req.pertanyaan}",
        )
        return {"status": "success", "jawaban": resp.text}
    except Exception:
        return {"status": "success", "jawaban": "Gagal menghubungi AI."}

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
