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

# --- SERIAL PORTS — WAJIB beda satu sama lain! ---
# Cek dengan `ls /dev/ttyACM* /dev/ttyUSB*` dan `dmesg | grep tty`
# Bisa di-override per-node lewat env (STM32_PORT / LORA_PORT) tanpa ubah kode.
STM32_PORT = os.environ.get("STM32_PORT", "/dev/ttyACM1")   # STM32 (STMicroelectronics Virtual COM Port)
LORA_PORT  = os.environ.get("LORA_PORT", "/dev/ttyACM0")    # LoRa32 gateway/transmitter (QinHeng/CH340)
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
def init_all_serial():
    """
    Buka koneksi serial STM32 dan LoRa satu kali di awal startup.
    Kalau dua port ternyata sama, salah satu SENGAJA tidak dibuka
    biar gak rebutan port yang sama.
    """
    global arduino, lora_tx

    if STM32_PORT == LORA_PORT:
        print(f"[!] FATAL: STM32_PORT dan LORA_PORT sama-sama '{STM32_PORT}'! "
              f"Cek `ls /dev/ttyACM*` dan pisahkan portnya di config.")
        return

    try:
        arduino = serial.Serial(STM32_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        print(f"[+] STM32 terhubung di {STM32_PORT} @ {BAUD_RATE}")
    except Exception as e:
        arduino = None
        print(f"[!] Gagal buka STM32 di {STM32_PORT}: {e}")

    try:
        lora_tx = serial.Serial(LORA_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        print(f"[+] LoRa terhubung di {LORA_PORT} @ {BAUD_RATE}")
    except Exception as e:
        lora_tx = None
        print(f"[!] Gagal buka LoRa di {LORA_PORT}: {e}")


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

                # Suntik metadata penelitian: seq (nomor urut → packet loss) + sentAt
                # (jam device → latency). Dipakai jalur LoRa & HTTP. Nilai sama untuk
                # satu bacaan → perbandingan per-paket adil.
                _seq_counter += 1
                data["seq"] = _seq_counter
                data["sentAt"] = datetime.now(timezone.utc).isoformat()
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

                print(f"[Dispatcher] seq={_seq_counter} {fwd[:72]}")
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
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
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
CONF_THRESHOLD   = float(os.environ.get("CONF_THRESHOLD", "0.75"))    # confidence minimum utk aktuasi + lapor
COOLDOWN_SEC     = float(os.environ.get("COOLDOWN_SEC", "3.0"))       # jeda antar deteksi (anti jepret beruntun)
AUTO_START_CAM   = os.environ.get("AUTO_START_CAM", "1") == "1"       # default ON di Raspi (set 0 utk matikan)


class CameraWorker:
    """Loop kamera di thread terpisah supaya endpoint HTTP tetap responsif."""

    def __init__(self):
        self.thread  = None
        self.running = False
        self.cap     = None
        self.last    = {"kategori": None, "confidence": None, "ts": None}
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

        print(f"[CAM] Kamera realtime AKTIF (index {CAMERA_INDEX}) — mode motion-gate, tanpa web.")
        prev = None
        cooldown_until = 0.0

        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
            now = time.time()

            if prev is not None and now >= cooldown_until:
                delta  = cv2.absdiff(prev, gray)
                thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
                motion = int(thresh.sum())

                if motion > MOTION_THRESHOLD:
                    # Ada objek masuk → tunggu diam → jepret frame final
                    time.sleep(SETTLE_DELAY)
                    ok2, shot = self.cap.read()
                    if ok2:
                        img = Image.fromarray(cv2.cvtColor(shot, cv2.COLOR_BGR2RGB))
                        try:
                            kategori, conf = _predict(img)
                        except Exception as e:
                            print(f"[CAM] gagal klasifikasi: {e}")
                            kategori, conf = None, 0.0

                        if kategori and conf >= CONF_THRESHOLD:
                            print(f"[CAM] ✓ {kategori} ({conf:.0%}) → aktuasi STM32 + lapor backend")
                            kirim_ke_stm32(kategori)
                            report_classification(kategori, conf)
                            self.last = {"kategori": kategori, "confidence": conf, "ts": now}
                        else:
                            print(f"[CAM] objek terdeteksi tapi confidence rendah ({conf:.0%}) — dilewati")

                    cooldown_until = time.time() + COOLDOWN_SEC
                    prev = None  # reset baseline setelah aksi
                    continue

            prev = gray
            time.sleep(0.03)  # cap ~30 fps, hemat CPU

        if self.cap:
            self.cap.release()
            self.cap = None
        print("[CAM] Kamera realtime berhenti.")


camera_worker = CameraWorker()

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
        "cooldown_sec": COOLDOWN_SEC,
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
