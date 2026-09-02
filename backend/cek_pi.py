"""Bedah satu jepretan Pi: probabilitas PENUH model utama & penjaga, plus
keputusan tiap kombinasi ambang. Jalanin DI PI, di ~/backend.
    python3 cek_pi.py                 # pakai last_shot.jpg
    python3 cek_pi.py foto_lain.jpg
Preprocessing sengaja disamain persis dgn _preprocess() main.py (piksel 0-255).
"""
import sys
import numpy as np
from PIL import Image

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from ai_edge_litert.interpreter import Interpreter

CLASS   = ["Anorganik", "B3", "Organik"]
PRIMARY = "model_ai_baru/an_or32.tflite"   # MODEL_PATH_NEW di main.py
GUARD   = "model_combo.tflite"             # MODEL_PATH di main.py
B3      = CLASS.index("B3")
img_path = sys.argv[1] if len(sys.argv) > 1 else "last_shot.jpg"


def jalankan(path, img):
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    ins, outs = it.get_input_details(), it.get_output_details()
    h, w = int(ins[0]["shape"][1]), int(ins[0]["shape"][2])
    arr = np.array(img.convert("RGB").resize((w, h)), dtype=np.float32)   # 0-255, pass-through
    it.set_tensor(ins[0]["index"], np.expand_dims(arr, 0).astype(ins[0]["dtype"]))
    it.invoke()
    p = it.get_tensor(outs[0]["index"])[0].astype(np.float32)
    if outs[0]["dtype"] != np.float32:
        scale, zero = outs[0]["quantization"]
        if scale:
            p = (p.astype(np.float32) - zero) * scale
    return p


img = Image.open(img_path)
print(f"gambar: {img_path}  {img.size}")
p_p = jalankan(PRIMARY, img)
g_p = jalankan(GUARD, img)
for nama, p in (("UTAMA (an_or32)", p_p), ("PENJAGA (model_combo)", g_p)):
    print(f"  {nama:22s} " + "  ".join(f"{c}={v*100:5.1f}%" for c, v in zip(CLASS, p))
          + f"   → {CLASS[int(np.argmax(p))]}")

g_b3, p_b3 = float(g_p[B3]), float(p_p[B3])
kedua = max(float(v) for i, v in enumerate(g_p) if i != B3)
utama = CLASS[int(np.argmax(p_p))]
print(f"\n  guard B3={g_b3*100:.1f}%  (kelas kedua guard={kedua*100:.1f}%, "
      f"margin={(g_b3-kedua)*100:.1f}%)   utama B3={p_b3*100:.1f}%")

print("\nkeputusan per setelan:")
for nama, gate, margin, floor in (
        ("Pi SEKARANG  (gate .55, tanpa margin/veto)", 0.55, 0.0,  0.0),
        ("patch baru   (gate .55, margin .15, veto .15)", 0.55, 0.15, 0.15),
        ("samain sim   (gate .70, margin .15, veto .15)", 0.70, 0.15, 0.15)):
    gagal = []
    if g_b3 < gate:
        gagal.append(f"gate ({g_b3*100:.0f}%<{gate*100:.0f}%)")
    if margin > 0 and g_b3 - kedua < margin:
        gagal.append(f"margin ({(g_b3-kedua)*100:.0f}%<{margin*100:.0f}%)")
    if floor > 0 and p_b3 < floor:
        gagal.append(f"veto ({p_b3*100:.0f}%<{floor*100:.0f}%)")
    print(f"  {nama:46s} → " + ("B3" if not gagal else f"{utama}   [ditolak: {', '.join(gagal)}]"))
