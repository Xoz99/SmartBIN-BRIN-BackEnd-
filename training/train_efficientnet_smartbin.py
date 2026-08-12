"""
Training ulang model utama SmartBIN (organik / anorganik / B3) — EfficientNetB0.
Dijalankan di Google Colab. Output: model_fp16.tflite yang KOMPATIBEL dengan
backend/main.py tanpa perlu ubah kode di Raspi.

===================================================================
ATURAN WAJIB biar model nyambung ke main.py (JANGAN dilanggar):
  1. Arsitektur = EfficientNetB0, input 224x224x3.
  2. PIKSEL MENTAH 0-255 masuk ke model. JANGAN Rescaling(1./255),
     JANGAN preprocess_input manual. EfficientNet punya normalisasi
     di DALAM modelnya. main.py di Raspi kasih piksel mentah (pass-through),
     jadi normalisasi HARUS ada di dalam model (otomatis dari EfficientNetB0).
  3. Urutan kelas HARUS ["Anorganik", "B3", "Organik"] (index 0/1/2).
     image_dataset_from_directory nyortir folder alfabetis -> cocok.
     Tapi WAJIB di-print & dicek (lihat ASSERT di bawah).
  4. Export TFLite fp16 (bobot fp16, input/output tetap float32).
===================================================================

Cara pakai (Colab):
  - Export dataset dari Roboflow: format "Folder Structure" (classification),
    bukan COCO/YOLO. Nanti dapet folder train/ valid/ test/ berisi
    subfolder Anorganik/ B3/ Organik/.
  - Set DATA_DIR ke lokasi hasil unzip.
  - Jalankan sel per sel.
"""

import tensorflow as tf
from tensorflow.keras import layers, models
import numpy as np

# ---- Konfigurasi ----
DATA_DIR   = "/content/smartbin-dataset"   # ganti: hasil export Roboflow (ada train/ valid/)
IMG_SIZE   = (224, 224)
BATCH      = 32
EPOCHS_HEAD = 12       # tahap 1: latih kepala (base beku)
EPOCHS_FT   = 8        # tahap 2: fine-tune sebagian base
CLASS_ORDER = ["Anorganik", "B3", "Organik"]  # HARUS urutan ini

# ---- 1. Load dataset (piksel MENTAH 0-255, jangan dinormalisasi) ----
train_ds = tf.keras.utils.image_dataset_from_directory(
    f"{DATA_DIR}/train",
    image_size=IMG_SIZE, batch_size=BATCH, label_mode="categorical",
    shuffle=True, seed=42,
)
val_ds = tf.keras.utils.image_dataset_from_directory(
    f"{DATA_DIR}/valid",
    image_size=IMG_SIZE, batch_size=BATCH, label_mode="categorical",
    shuffle=False,
)

# ---- CEK URUTAN KELAS — kalau gagal, STOP, jangan lanjut ----
print("Urutan kelas terdeteksi:", train_ds.class_names)
assert train_ds.class_names == CLASS_ORDER, (
    f"URUTAN KELAS SALAH: {train_ds.class_names} != {CLASS_ORDER}. "
    "Rename folder atau sesuaikan, kalau tidak label di main.py bakal ketuker!"
)

AUTOTUNE = tf.data.AUTOTUNE
train_ds = train_ds.prefetch(AUTOTUNE)
val_ds   = val_ds.prefetch(AUTOTUNE)

# ---- 2. Augmentasi (bantu daun kering & variasi cahaya) ----
# Augmentasi jalan di 0-255, aman karena belum ada normalisasi.
data_aug = models.Sequential([
    layers.RandomFlip("horizontal"),
    layers.RandomRotation(0.15),
    layers.RandomZoom(0.15),
    layers.RandomBrightness(0.15, value_range=(0, 255)),
    layers.RandomContrast(0.15),
], name="augment")

# ---- 3. Bangun model: input MENTAH -> EfficientNetB0 (normalisasi internal) ----
base = tf.keras.applications.EfficientNetB0(
    include_top=False, weights="imagenet",
    input_shape=(224, 224, 3),
)
base.trainable = False   # tahap 1: beku

inputs = layers.Input(shape=(224, 224, 3))   # piksel MENTAH 0-255
x = data_aug(inputs)
x = base(x, training=False)                  # EfficientNet normalisasi di dalam sini
x = layers.GlobalAveragePooling2D()(x)
x = layers.Dropout(0.3)(x)
outputs = layers.Dense(len(CLASS_ORDER), activation="softmax")(x)
model = models.Model(inputs, outputs)

model.compile(
    optimizer=tf.keras.optimizers.Adam(1e-3),
    loss="categorical_crossentropy", metrics=["accuracy"],
)

# ---- Tangani kelas tak seimbang (B3 biasanya lebih sedikit) ----
# Hitung class_weight dari jumlah file per kelas.
import pathlib, collections
counts = collections.Counter()
for cls in CLASS_ORDER:
    counts[cls] = len(list(pathlib.Path(f"{DATA_DIR}/train/{cls}").glob("*")))
total = sum(counts.values())
class_weight = {i: total / (len(CLASS_ORDER) * counts[c]) for i, c in enumerate(CLASS_ORDER)}
print("Jumlah per kelas:", dict(counts), "| class_weight:", class_weight)

# ---- 4a. Tahap 1: latih kepala ----
model.fit(train_ds, validation_data=val_ds, epochs=EPOCHS_HEAD,
          class_weight=class_weight)

# ---- 4b. Tahap 2: fine-tune ~30% layer atas base ----
base.trainable = True
n = len(base.layers)
for layer in base.layers[: int(n * 0.7)]:
    layer.trainable = False
# BatchNorm tetap beku biar stabil
for layer in base.layers:
    if isinstance(layer, layers.BatchNormalization):
        layer.trainable = False

model.compile(
    optimizer=tf.keras.optimizers.Adam(1e-5),   # LR kecil buat fine-tune
    loss="categorical_crossentropy", metrics=["accuracy"],
)
model.fit(train_ds, validation_data=val_ds, epochs=EPOCHS_FT,
          class_weight=class_weight)

# ---- 5. Evaluasi + confusion matrix (cek daun kebaca apa) ----
print("\n=== Evaluasi val ===")
y_true, y_pred = [], []
for imgs, labels in val_ds:
    p = model.predict(imgs, verbose=0)
    y_pred.extend(np.argmax(p, axis=1))
    y_true.extend(np.argmax(labels.numpy(), axis=1))
try:
    from sklearn.metrics import classification_report, confusion_matrix
    print(classification_report(y_true, y_pred, target_names=CLASS_ORDER))
    print("Confusion matrix (baris=asli, kolom=prediksi):")
    print(confusion_matrix(y_true, y_pred))
except ImportError:
    print("sklearn gak ada, skip report")

# ---- 6. Export TFLite fp16 (timpa model_ai_baru/model_fp16.tflite di Raspi) ----
converter = tf.lite.TFLiteConverter.from_keras_model(model)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
converter.target_spec.supported_types = [tf.float16]   # bobot fp16
tflite_model = converter.convert()

out_path = "/content/model_fp16.tflite"
with open(out_path, "wb") as f:
    f.write(tflite_model)
print(f"\nTersimpan: {out_path} ({len(tflite_model)/1e6:.1f} MB)")

# ---- 7. Verifikasi model TFLite: input/output dtype & bentuk ----
interp = tf.lite.Interpreter(model_path=out_path)
interp.allocate_tensors()
inp = interp.get_input_details()[0]
outp = interp.get_output_details()[0]
print("Input :", inp["shape"], inp["dtype"], "  (harus [1 224 224 3] float32)")
print("Output:", outp["shape"], outp["dtype"], "  (harus [1 3] float32)")

# ---- 8. Uji cepat 1 gambar MENTAH (tanpa normalisasi apa pun) ----
# Pastikan hasilnya masuk akal sebelum dikirim ke Raspi.
import os
sample = None
for cls in CLASS_ORDER:
    fs = list(pathlib.Path(f"{DATA_DIR}/valid/{cls}").glob("*"))
    if fs:
        sample = str(fs[0]); expect = cls; break
if sample:
    img = tf.keras.utils.load_img(sample, target_size=IMG_SIZE)
    arr = tf.keras.utils.img_to_array(img)[None].astype("float32")  # MENTAH 0-255
    interp.set_tensor(inp["index"], arr)
    interp.invoke()
    prob = interp.get_tensor(outp["index"])[0]
    print(f"Uji {sample} (asli={expect}) -> "
          f"{dict(zip(CLASS_ORDER, [round(float(x), 3) for x in prob]))}")

# Download model_fp16.tflite dari Colab:
#   from google.colab import files; files.download("/content/model_fp16.tflite")
# Lalu scp ke Raspi:
#   scp model_fp16.tflite brin@100.99.74.71:~/backend/model_ai_baru/model_fp16.tflite
