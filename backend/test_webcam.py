"""
Tes model_advanced.tflite pakai WEBCAM laptop (mirip alur Pi).
    python test_webcam.py
Tekan SPASI = jepret + analisis, Q = keluar.
Preprocessing sama dgn main.py (resize input model, pass-through EfficientNet).
"""
import cv2
import numpy as np

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter
from PIL import Image

CLASS = ["Anorganik", "B3", "Organik"]

it = Interpreter(model_path="model_advanced.tflite")
it.allocate_tensors()
inp = it.get_input_details()[0]
out = it.get_output_details()[0]
h, w = int(inp["shape"][1]), int(inp["shape"][2])

cap = cv2.VideoCapture(0)
print("SPASI = jepret + analisis  |  Q = keluar")
label = "arahkan objek, tekan SPASI"

while True:
    ok, frame = cap.read()
    if not ok:
        break
    cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow("Tes Model — SPASI=analisis, Q=keluar", frame)
    k = cv2.waitKey(1) & 0xFF
    if k == ord("q"):
        break
    if k == ord(" "):
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((w, h))
        x = np.expand_dims(np.array(img, dtype=np.float32), 0)
        it.set_tensor(inp["index"], x)
        it.invoke()
        p = it.get_tensor(out["index"])[0].astype(float)
        i = int(np.argmax(p))
        label = f"{CLASS[i]} {p[i]*100:.0f}%"
        print(label, "|", {CLASS[j]: round(float(p[j]) * 100) for j in range(3)})

cap.release()
cv2.destroyAllWindows()
