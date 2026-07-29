"""
SIMULASI SISTEM di laptop — logika mode PLATFORM yang SAMA dgn main.py Pi,
pakai webcam laptop + model_advanced.tflite. Lihat alur sistem tanpa hardware:

  Platform KOSONG (baseline) → objek masuk kotak → auto jepret + klasifikasi
  → "AKTUASI" (simulasi jatuhin) → tunggu aktuator → siap objek berikutnya

Kontrol:  B = ambil ulang baseline (pas kosong)  |  Q = keluar
Jalankan:  python sim_platform.py
Preprocessing & urutan kelas SAMA dgn main.py (EfficientNet pass-through).
"""
import time
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

# ── Config (sama dgn main.py; ubah di sini buat coba-coba) ──
ROI_FRAC     = 0.6
OBJECT_DIFF  = 18.0   # beda dari kosong utk dianggap ada objek
CLEAR_DIFF   = 9.0
STILL_MOVE   = 4.0    # gerak antar-frame di bawah ini = objek diam
STILL_NEED   = 3
REARM_BUFFER = 1.5
CONF_THRESHOLD = 0.0  # 0 = tanpa batas (sama default main.py)
CAMERA_WARMUP_SEC = 2.0  # kamera settle dulu sebelum ambil baseline (cegah gerak sendiri di awal)
ACT_TIME = {"organik": 7.0, "anorganik": 8.0, "b3": 9.5}

CLASS = ["Anorganik", "B3", "Organik"]
COLOR = {"Anorganik": (153, 124, 91), "B3": (94, 90, 209), "Organik": (108, 132, 72)}  # BGR


def center_roi(frame, frac):
    h, w = frame.shape[:2]
    s = int(min(h, w) * frac)
    cy, cx = h // 2, w // 2
    y0, x0 = max(0, cy - s // 2), max(0, cx - s // 2)
    return frame[y0:y0 + s, x0:x0 + s], (x0, y0, s)


def classify(interp, inp, out, frame_bgr):
    h, w = int(inp["shape"][1]), int(inp["shape"][2])
    img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)).resize((w, h))
    x = np.expand_dims(np.array(img, dtype=np.float32), 0)
    interp.set_tensor(inp["index"], x)
    interp.invoke()
    p = interp.get_tensor(out["index"])[0].astype(float)
    i = int(np.argmax(p))
    return CLASS[i], float(p[i]), p


def main():
    it = Interpreter(model_path="model_advanced.tflite")
    it.allocate_tensors()
    inp, out = it.get_input_details()[0], it.get_output_details()[0]

    cap = cv2.VideoCapture(0)
    baseline = None
    prev = None
    armed = True
    still = 0
    rearm_at = 0.0
    last = None  # (label, conf)
    warmup_until = time.time() + CAMERA_WARMUP_SEC

    print("SIMULASI mode PLATFORM. Pastikan KOTAK kosong dulu (baseline). B=re-baseline, Q=keluar.")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        roi, (x0, y0, s) = center_roi(frame, ROI_FRAC)
        gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (21, 21), 0)
        now = time.time()

        # ── WARMUP: kamera settle dulu, belum deteksi (baseline diambil setelah ini) ──
        if now < warmup_until:
            cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (200, 200, 200), 2)
            cv2.putText(frame, f"MENYALA... warmup {warmup_until - now:.0f}s (kotak kosong)",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            cv2.imshow("SIMULASI EcoSort — B=baseline, Q=keluar", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
            baseline = None  # baseline diambil dari frame pertama SETELAH warmup
            continue

        obj_diff = move = 0.0
        if baseline is None:
            baseline = gray
            prev = gray
        else:
            obj_diff = float(cv2.absdiff(gray, baseline).mean())
            move = float(cv2.absdiff(gray, prev).mean())
            prev = gray

            if armed:
                if obj_diff > OBJECT_DIFF and move < STILL_MOVE:
                    still += 1
                    if still >= STILL_NEED:
                        label, conf, _ = classify(it, inp, out, frame)
                        last = (label, conf)
                        if conf >= CONF_THRESHOLD:
                            print(f"[CAM] ✓ {label} ({conf*100:.0f}%) → AKTUASI (simulasi jatuhin)")
                        armed = False
                        still = 0
                        rearm_at = now + ACT_TIME.get(label.lower(), 8.0) + REARM_BUFFER
                else:
                    still = 0
            else:
                if now >= rearm_at:
                    armed = True
                    baseline = gray
                    print("[CAM] siap objek berikutnya.")

        # ── Gambar ──
        if armed:
            box_col = (108, 132, 72)   # hijau: siap
            status = "SIAP - taruh objek di kotak"
        else:
            wait = max(0, rearm_at - now)
            box_col = (94, 90, 209)    # merah: aktuator jalan
            status = f"AKTUASI... siap dalam {wait:.0f}s (angkat objek)"
        cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), box_col, 2)
        cv2.putText(frame, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, box_col, 2)
        cv2.putText(frame, f"obj_diff={obj_diff:5.1f} (butuh>{OBJECT_DIFF:.0f})  move={move:4.1f}",
                    (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        if last:
            lbl, cf = last
            c = COLOR.get(lbl, (200, 200, 200))
            cv2.putText(frame, f"Terakhir: {lbl} {cf*100:.0f}%", (10, 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)

        cv2.imshow("SIMULASI EcoSort — B=baseline, Q=keluar", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("b"):
            baseline = gray
            armed = True
            still = 0
            print("[CAM] baseline di-reset (kotak dianggap kosong).")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
