"""
Tes model_advanced.tflite di 1 gambar (buat cek akurasi di laptop tanpa Pi).
    python test_tflite.py foto.jpg [model_advanced.tflite]
Pakai preprocessing SAMA dgn main.py (resize ke input model, pass-through EfficientNet).
"""
import sys
import numpy as np

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter  # fallback: TF penuh (laptop)
from PIL import Image

CLASS = ["Anorganik", "B3", "Organik"]
img_path = sys.argv[1]
model = sys.argv[2] if len(sys.argv) > 2 else "model_advanced.tflite"

it = Interpreter(model_path=model)
it.allocate_tensors()
inp = it.get_input_details()[0]
out = it.get_output_details()[0]
h, w = int(inp["shape"][1]), int(inp["shape"][2])

img = Image.open(img_path).convert("RGB").resize((w, h))
x = np.expand_dims(np.array(img, dtype=np.float32), 0)  # pass-through (EfficientNet)
it.set_tensor(inp["index"], x)
it.invoke()
p = it.get_tensor(out["index"])[0].astype(float)

order = sorted(range(len(CLASS)), key=lambda i: -p[i])
print(f"\n{img_path}")
for i in order:
    bar = "█" * int(p[i] * 20)
    print(f"  {CLASS[i]:10s} {p[i]*100:5.1f}%  {bar}")
print(f"  → {CLASS[order[0]]} ({p[order[0]]*100:.1f}%)\n")
