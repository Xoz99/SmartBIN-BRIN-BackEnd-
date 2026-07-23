import io
import os
import asyncio
import numpy as np
from PIL import Image
import tensorflow as tf
from tensorflow.keras import layers
from tensorflow.keras.applications.efficientnet import preprocess_input as eff_preprocess
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai

# ====== TAMBAHAN: serial ke Arduino/ESP32 ======
import serial
import serial.tools.list_ports

app = FastAPI(title="EcoSort AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= CONFIG =================
MODEL_PATH = r"D:\TUBES FINAL BANGET\Keras + Tensor\CHAPSS 2\AI Path\Deteksi Objek\model_sampah_Advanced.keras"
IMG_SIZE   = (224, 224)

CLASS_NAMES = ["Anorganik", "B3", "Organik"]

SERIAL_PORT = "COM7"
SERIAL_BAUD = 115200

FRONTEND_DIR = r"D:\TUBES FINAL BANGET\Test Flask Object Detection EcosortAI\frontend"

# Mapping kategori model -> perintah yang dimengerti Arduino (huruf kecil sesuai sketch)
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

# ================= CUSTOM LAYER =================
class ChannelAttentionLayer(tf.keras.layers.Layer):
    def __init__(self, reduction_ratio=16, **kwargs):
        super().__init__(**kwargs)
        self.reduction_ratio = reduction_ratio

    def build(self, input_shape):
        ch = input_shape[-1]
        self.gap = layers.GlobalAveragePooling2D()
        self.gmp = layers.GlobalMaxPooling2D()
        self.fc1 = layers.Dense(max(1, ch // self.reduction_ratio), activation="relu", use_bias=False)
        self.fc2 = layers.Dense(ch, activation="sigmoid", use_bias=False)
        super().build(input_shape)

    def call(self, x):
        avg_w = self.fc2(self.fc1(self.gap(x)))
        max_w = self.fc2(self.fc1(self.gmp(x)))
        ch    = tf.shape(x)[-1]
        scale = tf.reshape(avg_w + max_w, [-1, 1, 1, ch])
        return x * scale

    def get_config(self):
        cfg = super().get_config()
        cfg.update({"reduction_ratio": self.reduction_ratio})
        return cfg

# ================= LOAD MODEL =================
print(f"[+] Loading model dari: {MODEL_PATH}")
try:
    model = tf.keras.models.load_model(
        MODEL_PATH,
        compile=False,
        custom_objects={"ChannelAttentionLayer": ChannelAttentionLayer},
    )
    print("[+] Model loaded!")
except Exception as e:
    model = None
    print(f"[!] Error loading model: {e}")

# ================= SERIAL / ARDUINO SETUP =================
arduino = None

def _auto_detect_port():
    """Cari port yang kemungkinan ESP32/Arduino (CP210x, CH340, USB Serial)."""
    keywords = ("CP210", "CH340", "USB Serial", "Silicon Labs", "wch", "UART")
    for p in serial.tools.list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''}"
        if any(k.lower() in desc.lower() for k in keywords):
            return p.device
    return None

def init_serial():
    global arduino
    port = SERIAL_PORT or _auto_detect_port()
    if not port:
        print("[!] Port serial tidak ditemukan. Set env var SERIAL_PORT (mis. COM5) jika perlu.")
        return
    try:
        arduino = serial.Serial(port, SERIAL_BAUD, timeout=1)
        # ESP32 biasanya reset saat port dibuka — beri waktu boot
        import time
        time.sleep(2)
        print(f"[+] Serial terhubung ke {port} @ {SERIAL_BAUD}")
    except Exception as e:
        arduino = None
        print(f"[!] Gagal buka serial {port}: {e}")

def kirim_ke_arduino(kategori: str) -> bool:
    """Kirim perintah ke Arduino sesuai kategori sampah."""
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

@app.on_event("startup")
def _startup():
    init_serial()

@app.on_event("shutdown")
def _shutdown():
    if arduino is not None and arduino.is_open:
        arduino.close()
        print("[+] Serial ditutup.")

# ================= GEMINI SETUP =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAMwyORIARVCBAboc6GvCI1uI0W5XD7Kxw")
gemini_client  = None
tips_cache: dict[str, str] = {}

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("[+] Gemini siap!")
else:
    print("[!] GEMINI_API_KEY tidak ditemukan. Endpoint /genai/* tidak aktif.")

class TipsReq(BaseModel):
    kategori: str

class AskReq(BaseModel):
    pertanyaan: str
    kategori: str = ""

class ArduinoReq(BaseModel):
    perintah: str   # "organik" | "anorganik" | "B3" | "reset"

# ================= ROUTES =================
@app.get("/status")
def home():
    return {
        "status": "online",
        "model": "model_sampah_Advanced.keras (EfficientNetB3)",
        "classes": CLASS_NAMES,
        "gemini": "ready" if gemini_client else "unavailable (set GEMINI_API_KEY)",
        "arduino": "connected" if (arduino and arduino.is_open) else "disconnected",
    }

@app.get("/")
def home():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

@app.post("/predict/")
async def predict(file: UploadFile = File(...)):
    if model is None:
        return {"error": "Model tidak ter-load di server"}

    try:
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        image     = image.resize(IMG_SIZE)
        img_array = np.array(image, dtype=np.float32)
        img_array = eff_preprocess(img_array)
        img_tensor = tf.expand_dims(tf.convert_to_tensor(img_array), axis=0)

        cls_preds = model.predict(img_tensor, verbose=0)[0]
        class_idx  = int(np.argmax(cls_preds))
        confidence = float(cls_preds[class_idx])
        kategori   = CLASS_NAMES[class_idx]

        print(f"[+] Deteksi: {kategori} ({confidence:.1%})")

        # ====== TAMBAHAN: kirim hasil ke Arduino ======
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
    """Kirim perintah manual ke Arduino: organik / anorganik / B3 / reset."""
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

app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)