import io
import os
import asyncio
import numpy as np
from PIL import Image
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from google import genai

try:
    # LiteRT (penerus tflite-runtime) — dipakai di Mac/desktop & RPi
    import ai_edge_litert.interpreter as tflite
    _HAS_TF = False
except ImportError:
    try:
        # fallback ke tflite-runtime kalau ada
        import tflite_runtime.interpreter as tflite
        _HAS_TF = False
    except ImportError:
        # fallback terakhir: TensorFlow penuh (desktop)
        import tensorflow.lite as tflite
        _HAS_TF = True

# eff_preprocess hanya tersedia jika TensorFlow penuh terpasang (desktop)
if _HAS_TF:
    from tensorflow.keras.applications.efficientnet import preprocess_input as eff_preprocess
else:
    eff_preprocess = None

import serial
import serial.tools.list_ports

app = FastAPI(title="EcoSort AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== SESUAIKAN 3 BARIS INI DI RASPBERRY PI =====
MODEL_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_advanced.tflite")
FRONTEND_DIR = "/home/ecosort/frontend"
SERIAL_PORT  = "/dev/ttyUSB0"      # cek dgn: ls /dev/ttyUSB* /dev/ttyACM*
# =================================================

SERIAL_BAUD = 115200

CLASS_NAMES = ["Anorganik", "B3", "Organik"]

ARDUINO_CMD = {
    "Organik":   "organik",
    "Anorganik": "anorganik",
    "B3":        "B3",
}

STATIC_TIPS = {
    "Anorganik": (
        "• Pisahkan dari sampah organik dan B3 sebelum dibuang.\n"
        "• Jangan dibakar — asap plastik/kaca mengandung zat beracun.\n"
        "• Bawa ke bank sampah: botol plastik, kaleng, dan kardus punya nilai jual.\n"
        "• Cuci bersih kemasan sebelum disetor agar mudah didaur ulang."
    ),
    "B3": (
        "• JANGAN buang ke tempat sampah biasa — B3 mencemari tanah & air tanah.\n"
        "• Kumpulkan di drop-box B3 (apotek, minimarket, atau dinas lingkungan hidup).\n"
        "• Baterai, lampu, elektronik rusak, dan cat termasuk kategori B3.\n"
        "• Penanganan salah dapat menyebabkan kebakaran atau keracunan logam berat."
    ),
    "Organik": (
        "• Bisa dijadikan kompos dalam 4–8 minggu dengan metode sederhana di rumah.\n"
        "• Jangan campur dengan plastik — mempersulit pengomposan.\n"
        "• Sisa sayur, buah, dan makanan basi sangat cocok untuk eco-enzyme.\n"
        "• Kompos yang dihasilkan bisa dipakai langsung untuk pupuk tanaman."
    ),
}

# ================= LOAD MODEL TFLITE =================
interpreter   = None
input_details = None
output_details = None
IN_H = IN_W   = 224
INPUT_DTYPE   = np.float32

print(f"[+] Loading model TFLite dari: {MODEL_PATH}")
try:
    interpreter = tflite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
    input_details  = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    shape       = input_details[0]['shape']
    IN_H, IN_W  = int(shape[1]), int(shape[2])
    INPUT_DTYPE = input_details[0]['dtype']

    print(f"[+] Model TFLite loaded! Input: {IN_W}x{IN_H}, dtype={INPUT_DTYPE.__name__}")
except Exception as e:
    interpreter = None
    print(f"[!] Error loading model TFLite: {e}")

# Lock supaya inferensi dari HTTP /predict/ dan loop kamera realtime tidak tabrakan
# (tflite Interpreter tidak aman dipanggil paralel).
import threading
_infer_lock = threading.Lock()

def _preprocess(image: Image.Image) -> np.ndarray:
    """Preprocess gambar — DISAMAKAN dengan training EfficientNet (Keras).

    PENTING: EfficientNet Keras 'preprocess_input' itu PASS-THROUGH.
    Model EfficientNet sudah punya layer Normalization/Rescaling di dalamnya,
    jadi input yang diharapkan adalah piksel MENTAH 0-255 (float32).
    Normalisasi ImageNet manual (mean/std) -> SALAH, bikin prediksi ngaco.
    """
    image = image.convert("RGB").resize((IN_W, IN_H))
    arr   = np.array(image, dtype=np.float32)

    if INPUT_DTYPE == np.float32:
        if eff_preprocess is not None:
            # di desktop: pakai fungsi resmi (tetap pass-through utk EfficientNet)
            arr = eff_preprocess(arr)
        else:
            # di Pi (tflite-runtime tanpa TF): EfficientNet = pass-through, 0-255 mentah.
            # JANGAN normalisasi manual.
            pass
        arr = np.expand_dims(arr, axis=0).astype(np.float32)
    else:
        # model terkuantisasi (uint8/int8): kirim piksel mentah sesuai dtype input
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
    conf  = float(preds[idx])
    return CLASS_NAMES[idx], conf

arduino = None

def _auto_detect_port():
    keywords = ("CP210", "CH340", "USB Serial", "Silicon Labs", "wch", "UART", "ttyUSB", "ttyACM")
    for p in serial.tools.list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''} {p.device}"
        if any(k.lower() in desc.lower() for k in keywords):
            return p.device
    return None

def init_serial():
    global arduino
    port = SERIAL_PORT or _auto_detect_port()
    if not port:
        print("[!] Port serial tidak ditemukan. Cek 'ls /dev/ttyUSB*' atau '/dev/ttyACM*'.")
        return
    try:
        arduino = serial.Serial(port, SERIAL_BAUD, timeout=1)
        import time
        time.sleep(2)
        print(f"[+] Serial terhubung ke {port} @ {SERIAL_BAUD}")
    except Exception as e:
        arduino = None
        print(f"[!] Gagal buka serial {port}: {e}")

def kirim_ke_arduino(kategori: str) -> bool:
    if arduino is None or not arduino.is_open:
        print("[!] Serial tidak aktif, perintah dilewati.")
        return False
    cmd = ARDUINO_CMD.get(kategori)
    if not cmd:
        print(f"[!] Tidak ada mapping perintah untuk kategori: {kategori}")
        return False
    try:
        arduino.write((cmd + "\n").encode("utf-8"))
        arduino.flush()
        print(f"[+] Kirim ke Arduino: {cmd}")
        return True
    except Exception as e:
        print(f"[!] Gagal kirim serial: {e}")
        return False

# ================= KAMERA REALTIME (motion-gate + auto klasifikasi) =================
# Kamera baca frame terus-menerus. Klasifikasi HANYA dijalankan saat ada objek
# masuk (terdeteksi gerakan) — bukan berdasarkan countdown/durasi. Setelah ada
# objek -> tunggu sebentar biar diam -> jepret -> klasifikasi -> kirim ke Arduino.
import time as _time

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    _HAS_CV2 = False
    print("[!] OpenCV (cv2) belum terpasang. Kamera realtime nonaktif. -> pip install opencv-python")

# Semua bisa di-override lewat env var (tidak perlu ubah kode)
CAMERA_INDEX     = int(os.environ.get("CAMERA_INDEX", "0"))          # 0 = webcam default
MOTION_THRESHOLD = int(os.environ.get("MOTION_THRESHOLD", "1500000"))# total piksel berubah utk dianggap "ada objek"
SETTLE_DELAY     = float(os.environ.get("SETTLE_DELAY", "0.6"))      # detik tunggu objek diam sebelum jepret
CONF_THRESHOLD   = float(os.environ.get("CONF_THRESHOLD", "0.75"))   # confidence minimum supaya servo digerakkan
COOLDOWN_SEC     = float(os.environ.get("COOLDOWN_SEC", "3.0"))      # jeda antar deteksi (anti jepret beruntun)
AUTO_START_CAM   = os.environ.get("AUTO_START_CAM", "0") == "1"      # webcam lokal server (default OFF; pakai kamera HP via /scan)


class CameraWorker:
    """Loop kamera di thread terpisah supaya endpoint HTTP tetap responsif."""

    def __init__(self):
        self.thread = None
        self.running = False
        self.cap = None
        self.last = {"kategori": None, "confidence": None, "ts": None}
        self.error = None

    def start(self):
        if not _HAS_CV2:
            self.error = "OpenCV belum terpasang (pip install opencv-python)"
            return False
        if interpreter is None:
            self.error = "Model klasifikasi tidak ter-load"
            return False
        if self.running:
            return True
        self.error = None
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
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

        print(f"[CAM] Kamera realtime AKTIF (index {CAMERA_INDEX}) — mode motion-gate, tanpa countdown.")
        prev = None
        cooldown_until = 0.0

        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                _time.sleep(0.05)
                continue

            gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
            now = _time.time()

            if prev is not None and now >= cooldown_until:
                delta  = cv2.absdiff(prev, gray)
                thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
                motion = int(thresh.sum())

                if motion > MOTION_THRESHOLD:
                    # Ada objek masuk → tunggu sebentar biar diam, lalu jepret frame final
                    _time.sleep(SETTLE_DELAY)
                    ok2, shot = self.cap.read()
                    if ok2:
                        img = Image.fromarray(cv2.cvtColor(shot, cv2.COLOR_BGR2RGB))
                        try:
                            kategori, conf = _predict(img)
                        except Exception as e:
                            print(f"[CAM] gagal klasifikasi: {e}")
                            kategori, conf = None, 0.0

                        if kategori and conf >= CONF_THRESHOLD:
                            print(f"[CAM] ✓ {kategori} ({conf:.0%}) → kirim ke Arduino")
                            kirim_ke_arduino(kategori)
                            self.last = {"kategori": kategori, "confidence": conf, "ts": now}
                        else:
                            print(f"[CAM] objek terdeteksi tapi confidence rendah ({conf:.0%}) — dilewati")

                    cooldown_until = _time.time() + COOLDOWN_SEC
                    prev = None  # reset baseline setelah aksi
                    continue

            prev = gray
            _time.sleep(0.03)  # cap ~30 fps, hemat CPU

        if self.cap:
            self.cap.release()
            self.cap = None
        print("[CAM] Kamera realtime berhenti.")


camera_worker = CameraWorker()


@app.on_event("startup")
def _startup():
    init_serial()
    if AUTO_START_CAM:
        if camera_worker.start():
            print("[CAM] Auto-start kamera realtime diaktifkan (AUTO_START_CAM=1).")
        else:
            print(f"[CAM] Auto-start gagal: {camera_worker.error}")

@app.on_event("shutdown")
def _shutdown():
    camera_worker.stop()
    if arduino is not None and arduino.is_open:
        arduino.close()
        print("[+] Serial ditutup.")

# ================= PREDIKSI VOLUME (LSTM TFLite) =================
try:
    import prediksi_lstm
    _PREDIKSI_OK = prediksi_lstm.init()
except Exception as e:
    print(f"[LSTM-TFLite] init gagal: {e}")
    prediksi_lstm = None
    _PREDIKSI_OK = False

@app.get("/lstm/{kecamatan}")
def prediksi_volume(kecamatan: str):
    if not prediksi_lstm or not prediksi_lstm.is_ready():
        return {"error": "Model prediksi belum siap"}
    data = prediksi_lstm.predict_kecamatan(kecamatan)
    if data is None:
        return {"error": "Kecamatan tidak ditemukan"}
    return data

# ================= GEMINI SETUP =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
gemini_client  = None
tips_cache: dict[str, str] = {}

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("[+] Gemini siap!")
else:
    print("[!] GEMINI_API_KEY tidak ditemukan. Set: export GEMINI_API_KEY=...  Endpoint /genai/* nonaktif.")

class TipsReq(BaseModel):
    kategori: str

class AskReq(BaseModel):
    pertanyaan: str
    kategori: str = ""

class ArduinoReq(BaseModel):
    perintah: str

# ================= ROUTES =================
@app.get("/status")
def status():
    return {
        "status": "online",
        "model": os.path.basename(MODEL_PATH),
        "input_size": f"{IN_W}x{IN_H}",
        "classes": CLASS_NAMES,
        "gemini": "ready" if gemini_client else "unavailable (set GEMINI_API_KEY)",
        "arduino": "connected" if (arduino and arduino.is_open) else "disconnected",
        "camera": "running" if camera_worker.running else "stopped",
    }

@app.get("/")
def home():
    return {"status": "ok"}

# ================= HALAMAN SCAN (KAMERA HP) =================
SCAN_HTML = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>EcoSort — Scan Sampah</title>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body { margin:0; font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;
         background:#0b1220; color:#fff; overflow:hidden; }
  #wrap { position:fixed; inset:0; }
  video { width:100%; height:100%; object-fit:cover; }
  #overlay { position:fixed; inset:0; display:flex; flex-direction:column;
             justify-content:flex-end; pointer-events:none; }
  #card { margin:16px; padding:18px 20px; border-radius:18px;
          background:rgba(10,16,32,.78); backdrop-filter:blur(8px);
          box-shadow:0 8px 30px rgba(0,0,0,.45); }
  #label { font-size:30px; font-weight:800; letter-spacing:.3px; }
  #conf  { font-size:15px; opacity:.85; margin-top:4px; }
  #hint  { font-size:13px; opacity:.6; margin-top:10px; }
  .dot { display:inline-block; width:12px; height:12px; border-radius:50%;
         margin-right:8px; vertical-align:middle; background:#555; }
  .Organik   { color:#34d399; } .Organik .dot, #card.Organik .dot { background:#34d399; }
  .Anorganik { color:#60a5fa; } #card.Anorganik .dot { background:#60a5fa; }
  .B3        { color:#f87171; } #card.B3 .dot { background:#f87171; }
  #err { position:fixed; top:0; left:0; right:0; padding:14px; background:#7f1d1d;
         font-size:14px; display:none; }
</style>
</head>
<body>
<div id="wrap"><video id="cam" autoplay playsinline muted></video></div>
<div id="overlay">
  <div id="card">
    <div id="label"><span class="dot"></span>Arahkan ke sampah…</div>
    <div id="conf"></div>
    <div id="hint">Kategori diperbarui otomatis (realtime)</div>
  </div>
</div>
<div id="err"></div>
<canvas id="cv" width="224" height="224" style="display:none"></canvas>
<script>
const video=document.getElementById('cam'), canvas=document.getElementById('cv'),
      ctx=canvas.getContext('2d'), card=document.getElementById('card'),
      label=document.getElementById('label'), conf=document.getElementById('conf'),
      errBox=document.getElementById('err');
let busy=false;

function showErr(m){ errBox.style.display='block'; errBox.textContent=m; }

async function startCam(){
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    showErr('Browser memblokir kamera. Pastikan buka lewat HTTPS.'); return;
  }
  try{
    const stream=await navigator.mediaDevices.getUserMedia(
      { video:{ facingMode:{ ideal:'environment' } }, audio:false });
    video.srcObject=stream;
  }catch(e){ showErr('Gagal akses kamera: '+e.message); }
}

function cropDraw(){
  // center-crop kotak lalu resize ke 224x224
  const vw=video.videoWidth, vh=video.videoHeight; if(!vw) return false;
  const s=Math.min(vw,vh), sx=(vw-s)/2, sy=(vh-s)/2;
  ctx.drawImage(video, sx,sy,s,s, 0,0,224,224); return true;
}

async function scan(){
  if(busy || !cropDraw()) return;
  busy=true;
  canvas.toBlob(async (blob)=>{
    try{
      const fd=new FormData(); fd.append('file', blob, 'frame.jpg');
      const r=await fetch('/predict/', { method:'POST', body:fd });
      const j=await r.json();
      if(j.status==='success' && j.hasil && j.hasil[0]){
        const h=j.hasil[0], pct=Math.round(h.confidence*100);
        if(h.confidence>=0.75){
          card.className=h.kategori;
          label.innerHTML='<span class="dot"></span>'+h.kategori;
          conf.textContent='Keyakinan '+pct+'%';
        }else{
          card.className='';
          label.innerHTML='<span class="dot"></span>Arahkan ke sampah…';
          conf.textContent='';
        }
      }
    }catch(e){ /* abaikan 1 frame gagal */ }
    finally{ busy=false; }
  }, 'image/jpeg', 0.8);
}

startCam();
setInterval(scan, 700);
</script>
</body>
</html>"""

@app.get("/scan", response_class=HTMLResponse)
def scan_page():
    return SCAN_HTML

# ================= KONTROL KAMERA REALTIME =================
@app.post("/camera/start")
def camera_start():
    ok = camera_worker.start()
    return {
        "status": "success" if ok else "error",
        "running": camera_worker.running,
        "error": camera_worker.error,
    }

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

@app.post("/predict/")
async def predict(file: UploadFile = File(...)):
    if interpreter is None:
        return {"error": "Model tidak ter-load di server"}

    try:
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes))

        kategori, confidence = await asyncio.to_thread(_predict, image)

        print(f"[+] Deteksi: {kategori} ({confidence:.1%})")
        terkirim = kirim_ke_arduino(kategori)

        return {
            "status": "success",
            "hasil": [{
                "kategori":   kategori,
                "confidence": confidence,
            }],
            "arduino_terkirim": terkirim,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.post("/arduino/")
async def arduino_manual(req: ArduinoReq):
    if arduino is None or not arduino.is_open:
        return {"status": "error", "message": "Serial tidak aktif. Set SERIAL_PORT."}

    cmd = req.perintah.strip()
    valid = {"organik", "anorganik", "B3", "reset"}
    if cmd not in valid:
        return {"status": "error", "message": f"Perintah tidak valid. Pilih: {valid}"}

    try:
        arduino.write((cmd + "\n").encode("utf-8"))
        arduino.flush()
        return {"status": "success", "terkirim": cmd}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/genai/tips/")
async def genai_tips(req: TipsReq):
    if gemini_client is None:
        return {"status": "error", "message": "Gemini tidak aktif. Set env var GEMINI_API_KEY."}

    k = req.kategori
    if k in tips_cache:
        return {"status": "success", "tips": tips_cache[k], "cached": True}

    prompt = (
        f"Kamu adalah asisten edukasi lingkungan. Sampah yang terdeteksi: '{k}'. "
        f"Berikan 3-4 poin singkat dalam Bahasa Indonesia yang informatif: "
        f"(1) cara memilah yang benar, (2) dampak lingkungan jika salah buang, "
        f"(3) nilai daur ulang atau manfaat ekonomi. "
        f"Gunakan format bullet • dan bahasa yang santai tapi edukatif."
    )
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.0-flash-lite",
            contents=prompt,
        )
        tips_cache[k] = resp.text
        return {"status": "success", "tips": resp.text, "cached": False, "source": "gemini"}
    except Exception as e:
        print(f"[!] Gemini tips error (fallback ke static): {e}")
        fallback = STATIC_TIPS.get(k, "Pilah sampah sesuai kategorinya sebelum dibuang.")
        return {"status": "success", "tips": fallback, "cached": False, "source": "static"}


@app.post("/genai/ask/")
async def genai_ask(req: AskReq):
    if gemini_client is None:
        return {"status": "error", "message": "Gemini tidak aktif. Set env var GEMINI_API_KEY."}

    ctx = f"Konteks: sampah terakhir terdeteksi adalah '{req.kategori}'. " if req.kategori else ""
    prompt = (
        f"Kamu adalah asisten edukasi pengelolaan sampah yang ramah dan informatif. "
        f"{ctx}"
        f"Jawab pertanyaan berikut dalam Bahasa Indonesia secara ringkas (maksimal 4 kalimat): "
        f"{req.pertanyaan}"
    )
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.0-flash-lite",
            contents=prompt,
        )
        return {"status": "success", "jawaban": resp.text}
    except Exception as e:
        print(f"[!] Gemini ask error (fallback ke static): {e}")
        tips = STATIC_TIPS.get(req.kategori, "")
        jawaban = (
            f"Maaf, AI sedang tidak tersedia. "
            + (f"Berikut informasi dasar tentang sampah {req.kategori}:\n\n{tips}" if tips
               else "Silakan pilah sampah sesuai kategorinya: Organik, Anorganik, atau B3.")
        )
        return {"status": "success", "jawaban": jawaban}

# app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")

def _get_lan_ip():
    """Cari IP LAN Pi (yang dipakai buat akses dari HP/device lain)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

if __name__ == "__main__":
    import uvicorn

    PORT = 8000
    lan_ip = _get_lan_ip()

    # HTTPS: wajib supaya kamera HP (getUserMedia) bisa diakses lewat jaringan.
    # Pakai cert.pem & key.pem kalau ada (lihat README perintah openssl di bawah).
    BASE = os.path.dirname(os.path.abspath(__file__))
    SSL_CERT = os.environ.get("SSL_CERT", os.path.join(BASE, "cert.pem"))
    SSL_KEY  = os.environ.get("SSL_KEY",  os.path.join(BASE, "key.pem"))
    ssl_args = {}
    scheme = "http"
    if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY):
        ssl_args = {"ssl_certfile": SSL_CERT, "ssl_keyfile": SSL_KEY}
        scheme = "https"

    print("\n" + "=" * 54)
    print("  EcoSort AI Backend siap!")
    print("=" * 54)
    print(f"  Scan dari HP    : {scheme}://{lan_ip}:{PORT}/scan")
    print(f"  Lokal           : {scheme}://localhost:{PORT}")
    print(f"  (pastikan HP & laptop di WiFi yang sama)")
    if scheme == "http":
        print("  ⚠️  Belum HTTPS — kamera HP akan DIBLOKIR browser.")
        print("      Buat sertifikat dulu:")
        print(f"      openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem \\")
        print(f"        -out cert.pem -days 365 -subj \"/CN={lan_ip}\"")
    else:
        print("  🔒 HTTPS aktif. Di HP akan muncul peringatan sertifikat —")
        print("      pilih 'Lanjutkan/Proceed' (wajar untuk self-signed).")
    print("=" * 54 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=PORT, **ssl_args)
