"""
Konversi model Keras (.keras) → TFLite (model_advanced.tflite) untuk main.py & predict-service.

WAJIB dijalankan di mesin yang punya TensorFlow penuh (laptop / Google Colab) —
BUKAN di Raspi (Pi cuma punya tflite-runtime, nggak bisa load .keras/convert).

    pip install tensorflow
    python convert_to_tflite.py model_sampah_advanced.keras

Output: model_advanced.tflite + cetak input shape & jumlah kelas buat verifikasi.
Lalu ganti backend/model_advanced.tflite dgn hasil ini (di Pi & di VPS/predict-service).
"""
import sys
import tensorflow as tf

src = sys.argv[1] if len(sys.argv) > 1 else "model_sampah_advanced.keras"
out = sys.argv[2] if len(sys.argv) > 2 else "model_advanced.tflite"

print(f"[+] Load model: {src}")
model = tf.keras.models.load_model(src)

# Info penting buat dicocokin dgn main.py (_preprocess & CLASS_NAMES).
print("──────────────────────────────────────────────")
print(f"[i] Input shape : {model.input_shape}")   # harusnya (None, H, W, 3), mis. (None,224,224,3)
print(f"[i] Output shape: {model.output_shape}")  # (None, jumlah_kelas) — harus 3
try:
    print("[i] Layer pertama (cek ada Rescaling/preprocessing bawaan?):")
    for lyr in model.layers[:4]:
        print(f"      - {lyr.__class__.__name__}: {lyr.name}")
except Exception:
    pass
print("──────────────────────────────────────────────")

conv = tf.lite.TFLiteConverter.from_keras_model(model)
# Float32 (sama dgn model lama). Jangan aktifkan optimizations/quant dulu — bisa turunin akurasi.
tflite = conv.convert()
with open(out, "wb") as f:
    f.write(tflite)
print(f"[+] Tersimpan: {out} ({len(tflite)//1024} KB)")

print("""
[!] CEK 3 hal ini biar hasilnya bener di Pi:
  1. Input HxW  → main.py auto-baca dari model, jadi aman berapapun.
  2. Preprocessing → _preprocess main.py = PASS-THROUGH (piksel 0-255, konvensi EfficientNet).
     - Kalau model punya layer Rescaling/preprocess BAWAAN → pass-through BENAR (biarin).
     - Kalau model MINTA input ter-normalisasi (0-1 / ImageNet) tanpa layer bawaan →
       harus sesuaikan _preprocess. Tanya tim AI: "preprocessing-nya apa?"
  3. Urutan kelas output (index 0,1,2) HARUS = CLASS_NAMES ['Anorganik','B3','Organik'].
     Tanya tim AI urutan training-nya. Kalau beda → update CLASS_NAMES di main.py.
""")
