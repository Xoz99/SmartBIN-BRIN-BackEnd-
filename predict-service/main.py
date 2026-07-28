"""
SmartBin — Predict Service (EfficientNet TFLite)
================================================
Microservice inference PUBLIK untuk kamera device admin di dashboard.
Pakai model_advanced.tflite yang SAMA dengan yang jalan di Pi (backend/main.py),
supaya hasil klasifikasi konsisten. Di-expose lewat Caddy di smartbin.sbs/predict.

Endpoint:
  GET  /health   → {"ok": true} kalau model ter-load
  POST /predict  → multipart 'file' (gambar) → {"kategori", "confidence"}
"""

import io
import os
import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

# LiteRT / tflite runtime (robust: ai-edge-litert > tflite_runtime > tensorflow)
try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        from tensorflow.lite import Interpreter  # type: ignore

MODEL_PATH  = os.environ.get("MODEL_PATH", "/app/model/model_advanced.tflite")
CLASS_NAMES = ["Anorganik", "B3", "Organik"]  # HARUS sama urutan dgn training

app = FastAPI(title="SmartBin Predict (EfficientNet TFLite)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

interpreter = None
input_details = None
output_details = None
IN_H = IN_W = 224
INPUT_DTYPE = np.float32


@app.on_event("startup")
def _load():
    global interpreter, input_details, output_details, IN_H, IN_W, INPUT_DTYPE
    if not os.path.exists(MODEL_PATH):
        print(f"[WARN] Model tidak ada di {MODEL_PATH} — /predict akan balas error.")
        return
    try:
        interpreter = Interpreter(model_path=MODEL_PATH)
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
        shape = input_details[0]["shape"]
        IN_H, IN_W = int(shape[1]), int(shape[2])
        INPUT_DTYPE = input_details[0]["dtype"]
        print(f"[+] Model loaded: {IN_W}x{IN_H} dtype={INPUT_DTYPE.__name__}")
    except Exception as e:
        interpreter = None
        print(f"[!] Gagal load model: {e}")


def _preprocess(image: Image.Image) -> np.ndarray:
    image = image.convert("RGB").resize((IN_W, IN_H))
    arr = np.array(image, dtype=np.float32)
    if INPUT_DTYPE == np.float32:
        # EfficientNet = pass-through (piksel mentah 0-255). Sama dgn Pi.
        arr = np.expand_dims(arr, axis=0).astype(np.float32)
    else:
        arr = np.expand_dims(arr, axis=0).astype(INPUT_DTYPE)
    return arr


def _predict(image: Image.Image):
    inp = _preprocess(image)
    interpreter.set_tensor(input_details[0]["index"], inp)
    interpreter.invoke()
    preds = interpreter.get_tensor(output_details[0]["index"])[0].copy()
    if output_details[0]["dtype"] != np.float32:
        scale, zero = output_details[0]["quantization"]
        if scale:
            preds = (preds.astype(np.float32) - zero) * scale
    preds = preds.astype(np.float32)
    idx = int(np.argmax(preds))
    return CLASS_NAMES[idx], float(preds[idx])


@app.get("/health")
def health():
    return {"ok": interpreter is not None}


@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    if interpreter is None:
        return {"status": "error", "message": "model belum ter-load"}
    try:
        image = Image.open(io.BytesIO(await file.read()))
        kategori, confidence = _predict(image)
        return {"status": "success", "kategori": kategori, "confidence": confidence}
    except Exception as e:
        return {"status": "error", "message": str(e)}
